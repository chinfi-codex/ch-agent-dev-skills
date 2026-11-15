#!/usr/bin/env python3
"""POSIX file locking, atomic persistence, idempotent events and journals.

Public API:
    FileLock(path, blocking=True)
        Context manager around ``fcntl.flock``.  ``acquire(blocking=False)``
        returns False on contention.  Unsupported locking raises LockError;
        there is deliberately no unlocked fallback.  Kernel locks are released
        on process death.  The caller chooses the lock-file path.
    atomic_write(path, data, mode=0o600)
    atomic_json_write(path, value, mode=0o600)
        Write/fsync a temporary sibling, atomically replace, then fsync the
        parent directory.  JSON is UTF-8, sorted, deterministic and non-NaN.
    append_event_once(path, event, event_id=None, lock_path=None) -> bool
        Append one JSONL event under a lock.  A repeated ID with identical data
        returns False; conflicting reuse and malformed existing JSONL fail.
        For convenience ``(path, event_id, event, lock_path=...)`` is accepted.
    OperationJournal.create(path, operation_id, steps, lock_path=None)
    OperationJournal.load(path, lock_path=None)
    journal.advance(step_id, expected=..., observed=...)
    journal.complete() / journal.fail(error, step=None, observed=...)
        Journals have an idempotent operation ID, optimistic revisions, and
        ordered steps with expected/observed values.  ``advance(..., observed``
        omitted) records intent before a side effect; loading that running step
        after a crash and supplying observed completes it.  Corruption,
        out-of-order steps, stale writers and conflicting retries raise
        StateError.  Every mutation uses an atomic write while holding the
        caller-selected lock (default: ``<journal>.lock``).

No non-standard modules, background threads, timeouts based on polling, or
implicit worktree state are used.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX hosts
    fcntl = None


class StateError(ValueError):
    """Persistent state is corrupt, conflicting, or cannot be safely changed."""


class LockError(StateError):
    """A requested real file lock could not be acquired."""


_UNSET = object()


def _json_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StateError("value is not finite JSON: %s" % exc)


def _event_line(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StateError("event is not finite JSON: %s" % exc)


def _same(left: Any, right: Any) -> bool:
    return _event_line(left) == _event_line(right)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(str(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Any, data: Any, mode: int = 0o600) -> None:
    """Atomically replace *path* with str/bytes *data* and durable metadata."""
    target = Path(path)
    if isinstance(data, str):
        payload = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray)):
        payload = bytes(data)
    else:
        raise TypeError("atomic_write data must be str or bytes")
    if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode <= 0o7777:
        raise ValueError("mode must be a valid integer permission mask")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor: Optional[int] = None
    temporary: Optional[str] = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix="." + target.name + ".",
                                                  suffix=".tmp", dir=str(target.parent))
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        _fsync_directory(target.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def atomic_json_write(path: Any, value: Any, mode: int = 0o600) -> None:
    """Serialize *value* deterministically and pass it to atomic_write."""
    atomic_write(path, _json_bytes(value), mode=mode)


class FileLock:
    """A real POSIX flock tied to an owned file descriptor."""

    def __init__(self, path: Any, blocking: bool = True):
        self.path = Path(path)
        self.blocking = blocking
        self._descriptor: Optional[int] = None

    @property
    def held(self) -> bool:
        return self._descriptor is not None

    def acquire(self, blocking: Optional[bool] = None) -> bool:
        if self._descriptor is not None:
            return True
        if fcntl is None or not hasattr(fcntl, "flock"):
            raise LockError("POSIX flock is unavailable; refusing unlocked execution")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise LockError("cannot open lock %s: %s" % (self.path, exc))
        should_block = self.blocking if blocking is None else blocking
        operation = fcntl.LOCK_EX | (0 if should_block else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except (BlockingIOError, InterruptedError):
            os.close(descriptor)
            return False
        except OSError as exc:
            os.close(descriptor)
            raise LockError("flock failed for %s: %s; refusing unlocked execution" % (self.path, exc))
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            if fcntl is None:
                raise LockError("POSIX flock is unavailable")
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as exc:
            raise LockError("failed to release lock %s: %s" % (self.path, exc))
        finally:
            os.close(descriptor)

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise LockError("lock is held by another process: %s" % self.path)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


# ``event_id`` and common on-disk spellings are accepted only for identifying
# the event; the stored payload remains otherwise unchanged.
_ID_KEYS = ("event_id", "event-id", "operation_id", "operation-id", "id")


def _event_id(event: Dict[str, Any], explicit: Any) -> str:
    found = [(key, event[key]) for key in _ID_KEYS if key in event]
    if explicit is not None:
        found.append(("event_id", explicit))
    if not found:
        raise StateError("idempotent event requires event_id/operation_id/id")
    if any(not isinstance(value, str) or not value.strip() or value != value.strip()
           for _, value in found):
        raise StateError("event ID must be a non-empty trimmed string")
    values = {value.strip() for _, value in found}
    if len(values) != 1:
        raise StateError("event contains conflicting event/operation IDs")
    return values.pop()


def _load_json_lines(path: Path) -> List[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise StateError("cannot read event log %s: %s" % (path, exc))
    events: List[Dict[str, Any]] = []
    if text and not text.endswith("\n"):
        raise StateError("%s: unterminated final event; refusing to append after a partial write" % path)
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise StateError("%s:%d: blank event lines are corrupt" % (path, number))
        try:
            event = json.loads(line, object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
        except (ValueError, StateError) as exc:
            raise StateError("%s:%d: corrupt JSON event: %s" % (path, number, exc))
        if not isinstance(event, dict):
            raise StateError("%s:%d: event must be an object" % (path, number))
        _event_id(event, None)
        events.append(event)
    return events


def append_event_once(path: Any, event: Any, event_id: Any = None,
                      lock_path: Any = None) -> bool:
    """Atomically append an event once; return False for an identical replay."""
    if isinstance(event, str) and isinstance(event_id, dict):
        event, event_id = event_id, event
    if not isinstance(event, dict):
        raise StateError("event must be a mapping")
    candidate = copy.deepcopy(event)
    identifier = _event_id(candidate, event_id)
    if "event_id" in candidate:
        if candidate["event_id"] != identifier:
            raise StateError("event_id has surrounding whitespace")
    else:
        candidate["event_id"] = identifier
    target = Path(path)
    lock = FileLock(lock_path or str(target) + ".lock")
    with lock:
        existing = _load_json_lines(target)
        for current in existing:
            current_id = _event_id(current, None)
            if current_id == identifier:
                if _same(current, candidate):
                    return False
                raise StateError("event ID %s was reused with different data" % identifier)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = _event_line(candidate)
        try:
            with target.open("ab") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_directory(target.parent)
        except OSError as exc:
            raise StateError("cannot append event log %s: %s" % (target, exc))
        return True


def _unique_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StateError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StateError("non-finite JSON constant: %s" % value)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (OSError, UnicodeError, ValueError, StateError) as exc:
        raise StateError("cannot load journal %s: %s" % (path, exc))
    if not isinstance(value, dict):
        raise StateError("journal root must be an object")
    return value


def _prepare_steps(steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(steps, (list, tuple)) or not steps:
        raise StateError("operation journal requires a non-empty ordered step list")
    result: List[Dict[str, Any]] = []
    identifiers = set()
    for item in steps:
        if isinstance(item, str):
            step = {"id": item}
        elif isinstance(item, dict):
            if set(item) - {"id", "expected"}:
                raise StateError("new journal steps only accept id/expected")
            step = copy.deepcopy(item)
        else:
            raise StateError("journal step must be a string or id/expected mapping")
        identifier = step.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier != identifier.strip():
            raise StateError("journal step ID must be a non-empty trimmed string")
        if identifier in identifiers:
            raise StateError("duplicate journal step ID: %s" % identifier)
        identifiers.add(identifier)
        step["status"] = "pending"
        if "expected" in step:
            _event_line(step["expected"])
        result.append(step)
    return result


def _validate_journal(data: Dict[str, Any]) -> None:
    required = {"schema", "operation_id", "revision", "state", "current_step", "steps"}
    allowed = required | {"error"}
    if set(data) - allowed or not required <= set(data):
        raise StateError("journal has missing or unknown top-level fields")
    if data["schema"] != "agent-dev.operation-journal/v1":
        raise StateError("unsupported operation journal schema")
    if not isinstance(data["operation_id"], str) or not data["operation_id"].strip():
        raise StateError("journal operation_id must be a non-empty string")
    if not isinstance(data["revision"], int) or isinstance(data["revision"], bool) or data["revision"] < 0:
        raise StateError("journal revision must be a non-negative integer")
    if data["state"] not in ("running", "completed", "failed"):
        raise StateError("journal state is invalid")
    steps = data["steps"]
    if not isinstance(steps, list) or not steps:
        raise StateError("journal steps must be a non-empty array")
    identifiers = []
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"id", "status", "expected", "observed"}:
            raise StateError("journal step has unknown fields or wrong type")
        if not {"id", "status"} <= set(step):
            raise StateError("journal step lacks id/status")
        if not isinstance(step["id"], str) or not step["id"]:
            raise StateError("journal step ID is invalid")
        if step["status"] not in ("pending", "running", "done", "failed"):
            raise StateError("journal step status is invalid")
        if step["status"] in ("running", "done") and "expected" not in step:
            raise StateError("started/done journal step lacks expected value")
        if step["status"] == "done" and "observed" not in step:
            raise StateError("done journal step lacks observed value")
        if step["status"] in ("pending", "running") and "observed" in step:
            raise StateError("unfinished journal step cannot have observed value")
        identifiers.append(step["id"])
    if len(set(identifiers)) != len(identifiers):
        raise StateError("journal contains duplicate step IDs")

    first_open = next((index for index, step in enumerate(steps) if step["status"] != "done"), len(steps))
    if any(step["status"] != "done" for step in steps[:first_open]):
        raise StateError("journal has an impossible step ordering")
    if any(step["status"] == "done" for step in steps[first_open:]):
        raise StateError("journal skips an unfinished step")
    if sum(step["status"] in ("running", "failed") for step in steps) > 1:
        raise StateError("journal has multiple active steps")
    current = data["current_step"]
    if current is not None and current not in identifiers:
        raise StateError("journal current_step does not exist")
    all_done = first_open == len(steps)
    if data["state"] == "completed":
        if not all_done or current is not None or "error" in data:
            raise StateError("completed journal has unfinished steps/error")
    elif data["state"] == "running":
        if "error" in data:
            raise StateError("running journal cannot contain an error")
        expected_current = None if all_done else steps[first_open]["id"]
        if current != expected_current:
            raise StateError("running journal current_step is inconsistent")
    else:
        if not isinstance(data.get("error"), str) or not data["error"]:
            raise StateError("failed journal lacks an error")
        expected_current = None if all_done else steps[first_open]["id"]
        if current != expected_current:
            raise StateError("failed journal current_step is inconsistent")


class OperationJournal:
    """A recoverable ordered operation; construct via create() or load()."""

    def __init__(self, path: Path, data: Dict[str, Any], lock_path: Any = None):
        self.path = path
        self.lock_path = Path(lock_path) if lock_path is not None else Path(str(path) + ".lock")
        self._data = copy.deepcopy(data)

    @classmethod
    def create(cls, path: Any, operation_id: str, steps: Any,
               lock_path: Any = None) -> "OperationJournal":
        target = Path(path)
        prepared = _prepare_steps(steps)
        if not isinstance(operation_id, str) or not operation_id.strip() or operation_id != operation_id.strip():
            raise StateError("operation_id must be a non-empty trimmed string")
        def reuse_existing() -> "OperationJournal":
            # This helper performs no locking: callers either observed an atomic
            # completed file, or already hold the create lock.  In particular,
            # never recursively acquire a second flock while holding the first.
            journal = cls.load(target, lock_path=lock_path)
            if journal.operation_id != operation_id:
                raise StateError("journal path already belongs to another operation ID")
            existing = journal.steps
            if [step["id"] for step in existing] != [step["id"] for step in prepared]:
                raise StateError("idempotent create cannot change operation steps")
            for old, requested in zip(existing, prepared):
                if "expected" in requested and ("expected" not in old or not _same(old["expected"], requested["expected"])):
                    raise StateError("idempotent create cannot change expected step data")
            return journal

        if target.exists():
            return reuse_existing()
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(lock_path or str(target) + ".lock")
        with lock:
            if target.exists():
                return reuse_existing()
            data = {
                "schema": "agent-dev.operation-journal/v1",
                "operation_id": operation_id,
                "revision": 0,
                "state": "running",
                "current_step": prepared[0]["id"],
                "steps": prepared,
            }
            atomic_json_write(target, data)
        return cls(target, data, lock_path=lock_path)

    @classmethod
    def load(cls, path: Any, lock_path: Any = None) -> "OperationJournal":
        target = Path(path)
        data = _load_json(target)
        _validate_journal(data)
        return cls(target, data, lock_path=lock_path)

    @property
    def operation_id(self) -> str:
        return self._data["operation_id"]

    @property
    def state(self) -> str:
        return self._data["state"]

    @property
    def revision(self) -> int:
        return self._data["revision"]

    @property
    def current_step(self) -> Optional[str]:
        return self._data["current_step"]

    @property
    def data(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    @property
    def steps(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._data["steps"])

    def _reload_for_write(self) -> None:
        current = _load_json(self.path)
        _validate_journal(current)
        if current["revision"] != self.revision:
            raise StateError("stale journal writer: disk revision %d, writer revision %d" %
                             (current["revision"], self.revision))

    def _save(self, data: Dict[str, Any]) -> None:
        data["revision"] = self.revision + 1
        _validate_journal(data)
        atomic_json_write(self.path, data)
        self._data = copy.deepcopy(data)

    def advance(self, step_id: str, expected: Any = _UNSET,
                observed: Any = _UNSET) -> bool:
        """Record step intent, or its observed result, exactly once and in order."""
        if self.state != "running":
            raise StateError("only a running journal can advance")
        data = self.data
        index = next((i for i, step in enumerate(data["steps"]) if step["id"] == step_id), None)
        if index is None:
            raise StateError("unknown journal step: %s" % step_id)
        step = data["steps"][index]
        if step["status"] == "done":
            if expected is not _UNSET and not _same(step["expected"], expected):
                raise StateError("completed step expected value conflicts with replay")
            if observed is not _UNSET and not _same(step["observed"], observed):
                raise StateError("completed step observed value conflicts with replay")
            return False
        first_open = next(i for i, item in enumerate(data["steps"]) if item["status"] != "done")
        if index != first_open or data["current_step"] != step_id:
            raise StateError("journal steps must advance in declared order")
        if expected is not _UNSET:
            if "expected" in step and not _same(step["expected"], expected):
                raise StateError("step expected value cannot change during retry")
            step["expected"] = copy.deepcopy(expected)
        if "expected" not in step:
            raise StateError("advance requires an expected value")
        changed = step["status"] != "running"
        step["status"] = "running"
        if observed is not _UNSET:
            step["observed"] = copy.deepcopy(observed)
            step["status"] = "done"
            next_step = data["steps"][index + 1]["id"] if index + 1 < len(data["steps"]) else None
            data["current_step"] = next_step
            changed = True
        if not changed:
            return False
        with FileLock(self.lock_path):
            self._reload_for_write()
            self._save(data)
        return True

    def complete(self) -> bool:
        """Mark a fully observed operation complete; replay is a no-op."""
        if self.state == "completed":
            return False
        if self.state != "running":
            raise StateError("failed journal cannot complete")
        if any(step["status"] != "done" for step in self._data["steps"]):
            raise StateError("cannot complete journal with unfinished steps")
        data = self.data
        data["state"] = "completed"
        data["current_step"] = None
        with FileLock(self.lock_path):
            self._reload_for_write()
            self._save(data)
        return True

    def fail(self, error: str, step: Any = None, observed: Any = _UNSET) -> bool:
        """Persist a terminal failure without inventing an observed result."""
        if not isinstance(error, str) or not error.strip():
            raise StateError("journal failure requires a non-empty error")
        if self.state == "completed":
            raise StateError("completed journal cannot fail")
        if self.state == "failed":
            if self._data.get("error") == error and observed is _UNSET:
                return False
            raise StateError("failed journal cannot be overwritten with another failure")
        data = self.data
        step_id = step if step is not None else data["current_step"]
        if step_id is not None:
            index = next((i for i, item in enumerate(data["steps"]) if item["id"] == step_id), None)
            if index is None or data["current_step"] != step_id:
                raise StateError("failure step must be the current journal step")
            current = data["steps"][index]
            if current["status"] == "done":
                raise StateError("completed step cannot be marked failed")
            current["status"] = "failed"
            if observed is not _UNSET:
                current["observed"] = copy.deepcopy(observed)
        elif observed is not _UNSET:
            raise StateError("observed failure data requires a step")
        data["state"] = "failed"
        data["error"] = error
        with FileLock(self.lock_path):
            self._reload_for_write()
            self._save(data)
        return True
