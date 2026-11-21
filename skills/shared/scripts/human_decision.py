#!/usr/bin/env python3
"""Main-session owned human-decision v1 packets.

Implementation/review sessions call :func:`validate_request` only.  A main
session explicitly passes ``main_session=True`` to every mutating operation.
No function executes a prompt, an operation, a shell command, or a dispatch;
route_begin only persists a controlled plan for the host integration layer.

Public read/projection API is safe to import without sibling modules.  Storage,
frontmatter parsing and JSON Schema validation are imported lazily from
state_store, agents_config and json_schema respectively.
"""
from __future__ import annotations

import argparse
import builtins
import copy
import datetime as _datetime
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

SCHEMA = "ch-agent.human-decision/v1"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "human-decision.schema.json"
ACTIVE_STATUSES = ("pending", "waiting", "answered", "routing", "expired")
TERMINAL_STATUSES = ("resolved", "cancelled", "superseded")
STATUSES = ACTIVE_STATUSES + TERMINAL_STATUSES
REGISTERED_OPERATIONS = ("reconcile-routing", "re-split", "redesign", "request-review", "collect-evidence")
CONTINUE_STATUSES = ("confirmed", "in-progress", "in-review", "mr-submitted")
MAX_REQUEST_BYTES = 128 * 1024

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_OPTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_FEATURE = re.compile(r"^[^/\\\x00-\x1f]{1,64}/[^/\\\x00-\x1f]{1,64}$")
_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ISSUE = re.compile(r"^issue-[0-9]+[a-z0-9]*$")
_SECRET = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|(?:api[_-]?key|access[_-]?token|secret|password|passwd|authorization)"
    r"\s*[:=]\s*[^\s]+|bearer\s+[A-Za-z0-9._~+/-]{12,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}"
    r"|(?:密码|口令|密钥)\s*[:=]\s*[^\s]+)", re.I)
_INTERNAL_VISIBLE = re.compile(
    r"(?:needs-human|in-progress|in-review|mr-submitted|pending|waiting|answered"
    r"|routing|resolved|expired|cancelled|superseded|dispatch_id|session_id"
    r"|parent_session_id|decision_id|route_id|issue_sha|head_commit|report_sha"
    r"|sha256|frontier|worktree|redispatch|resume|supervisor|re-split|redesign|BLOCKER)", re.I)
_FORBIDDEN_KEYS = {
    "shell", "command", "cmd", "exec", "executable", "subprocess", "script",
    "args", "argv", "env", "raw_log", "raw_logs", "log", "logs", "token",
    "secret", "password", "api_key", "authorization",
}


class DecisionError(ValueError):
    """Base class for safe, expected decision failures."""


class ValidationError(DecisionError):
    """The envelope, packet, visible text or path violates the contract."""


class ConflictError(DecisionError):
    """An idempotency key or ticket state was reused with different data."""


class ActiveDecisionExists(ConflictError):
    """A ticket already has an unrelated active decision."""


class BasisChanged(ConflictError):
    """The requested answer belongs to a different immutable basis."""


class StateTransitionError(DecisionError):
    """The requested state transition is not permitted."""


class MainSessionRequired(DecisionError):
    """A sub-session attempted a main-session-only side effect."""


def _sibling(name: str):
    # ``python3 -I script.py`` may omit the script directory from sys.path.
    # Resolve only our known sibling contract modules, lazily and without
    # importing them for pure projection/read help paths.
    directory = str(Path(__file__).resolve().parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module(name)


def _store():
    return _sibling("state_store")


def _schema():
    return _sibling("json_schema")


def _config():
    return _sibling("agents_config")


def _require_main(main_session: bool) -> None:
    if main_session is not True:
        raise MainSessionRequired(
            "implementation/review sessions may only call validate_request; "
            "main_session=True is required for persistence, presentation and routing")


def _now(value: Any = None) -> str:
    if value is None:
        moment = _datetime.datetime.now(_datetime.timezone.utc)
    elif isinstance(value, _datetime.datetime):
        moment = value
    elif isinstance(value, str):
        return _parse_time(value).isoformat(timespec="seconds").replace("+00:00", "Z")
    else:
        raise ValidationError("now must be an RFC3339 string or timezone-aware datetime")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValidationError("now must include a timezone")
    return moment.astimezone(_datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> _datetime.datetime:
    if not isinstance(value, str):
        raise ValidationError("timestamp must be an RFC3339 string")
    try:
        parsed = _datetime.datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("invalid RFC3339 timestamp: %r" % value) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("timestamp must include an explicit timezone")
    return parsed


def _progress_ts(value: str) -> str:
    return _parse_time(value).astimezone().strftime("%Y-%m-%d %H:%M")


def _deepcopy_json(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception as exc:
        raise ValidationError("value must be a JSON object") from exc


def _reject_duplicate_keys(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _scan_security(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise ValidationError("forbidden executable/secret/log field: %s.%s" % (path, key))
            _scan_security(child, "%s.%s" % (path, key))
    elif isinstance(value, builtins.list):
        for index, child in enumerate(value):
            _scan_security(child, "%s[%d]" % (path, index))
    elif isinstance(value, str):
        if "\x00" in value:
            raise ValidationError("NUL is not permitted at %s" % path)
        match = _SECRET.search(value)
        if match:
            raise ValidationError("secret-like material is not permitted at %s" % path)


def _safe_text(value: Any, label: str, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("%s must be non-empty text" % label)
    if len(value) > maximum:
        raise ValidationError("%s exceeds %d characters" % (label, maximum))
    _scan_security(value, label)
    return value


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _sentence_count(text: str) -> int:
    stripped = _one_line(text).strip()
    if not stripped:
        return 0
    marks = re.findall(r"[。！？!?]+|(?<=[A-Za-z0-9\)])\.(?:\s|$)", stripped)
    if marks:
        tail = re.split(r"[。！？!?]+|(?<=[A-Za-z0-9\)])\.(?:\s|$)", stripped)[-1].strip()
        return len(marks) + (1 if tail else 0)
    return 1


def _sentence(value: str) -> str:
    text = _one_line(value).strip()
    return text if re.search(r"[。！？!?]$|\.$", text) else text + "。"


def _without_terminal(value: str) -> str:
    return _one_line(value).strip().rstrip("。！？!?.").strip()


def _validate_visible(value: str, label: str, sentences: Optional[Tuple[int, int]] = None) -> None:
    if not _one_line(value):
        raise ValidationError("%s must contain visible text" % label)
    if _INTERNAL_VISIBLE.search(value):
        raise ValidationError("%s contains an internal machine/status term" % label)
    if re.search(r"(?:^|\s)[A-D](?:\s|$)|方案\s*[A-D]|回\s*[A-D]", value, re.I):
        raise ValidationError("%s uses a letter-only/native-component answer convention" % label)
    if sentences and not sentences[0] <= _sentence_count(value) <= sentences[1]:
        raise ValidationError("%s must contain %d-%d sentences" % (label, sentences[0], sentences[1]))


def _repo_root(repo: Any) -> Path:
    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValidationError("repo is not a directory: %s" % root)
    return root


def _inside(root: Path, candidate: Path, label: str) -> Path:
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValidationError("%s escapes the repository" % label) from exc
    return resolved


def _scope_key(scope):
    try:
        return _sibling("artifact_scope").decision_key(scope)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _feature_dir(repo: Any, feature: str) -> Path:
    root = _repo_root(repo)
    try:
        _sibling("artifact_scope").validate_key(feature)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return _inside(root, root / "dev" / "features" / feature, "feature")


def packet_path(repo: Any, feature: str, decision_id: str) -> Path:
    if not isinstance(decision_id, str) or not _ID.fullmatch(decision_id):
        raise ValidationError("invalid decision id or path traversal")
    feature_dir = _feature_dir(repo, feature)
    return _inside(_repo_root(repo), feature_dir / "decisions" / (decision_id + ".json"), "decision")


def _lock_path(repo: Any, feature: str) -> Path:
    return _inside(_repo_root(repo), _feature_dir(repo, feature) / "decisions" / ".lock", "decision lock")


def _progress_path(repo: Any, feature: str) -> Path:
    return _inside(_repo_root(repo), _feature_dir(repo, feature) / "progress.jsonl", "progress event log")


def _relative_evidence(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValidationError("evidence.path must be a normalized repository-relative POSIX path")
    relative = Path(value)
    if relative.is_absolute() or relative.as_posix() != value or any(part in ("", ".", "..") for part in relative.parts):
        raise ValidationError("evidence.path traversal, absolute or non-normalized path is forbidden")
    if relative.parts[0] in (".git", ".worktrees"):
        raise ValidationError("evidence.path points into a forbidden internal directory")
    return _inside(root, root / relative, "evidence.path")


def _normalize_basis(value: Mapping[str, Any]) -> Dict[str, Any]:
    basis = dict(value)
    if "issue_sha" in basis and "issue_sha256" not in basis:
        basis["issue_sha256"] = basis.pop("issue_sha")
    if "report_sha" in basis and "report_sha256" not in basis:
        basis["report_sha256"] = basis.pop("report_sha")
    return basis


def _basis_from_argument(current_basis: Optional[Mapping[str, Any]], alias: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if current_basis is not None and alias is not None and dict(current_basis) != dict(alias):
        raise ValidationError("current_basis and basis disagree")
    value = current_basis if current_basis is not None else alias
    if not isinstance(value, Mapping):
        raise ValidationError("current_basis is required when answering or starting a route")
    return _normalize_basis(value)


def _assert_basis(packet: Mapping[str, Any], current_basis: Mapping[str, Any]) -> None:
    expected = packet["basis"]
    observed = _normalize_basis(current_basis)
    for key in ("issue_sha256", "head_commit", "report_sha256"):
        if observed.get(key) != expected.get(key):
            raise BasisChanged(
                "decision basis changed at %s; supersede the old packet instead of reusing its answer" % key)


def _normalize_request(envelope: Mapping[str, Any], now: Any = None) -> Dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise ValidationError("request envelope must be an object")
    packet = _deepcopy_json(dict(envelope))
    timestamp = _now(now)
    packet.setdefault("schema", SCHEMA)
    packet.setdefault("status", "pending")
    packet.setdefault("answer", None)
    packet.setdefault("route", None)
    packet.setdefault("expires_at", None)
    packet.setdefault("supersedes", None)
    packet.setdefault("superseded_by", None)
    packet.setdefault("cancellation", None)
    packet.setdefault("evidence", [])
    if isinstance(packet.get("basis"), Mapping):
        packet["basis"] = _normalize_basis(packet["basis"])
    source = packet.setdefault("source", {})
    if isinstance(source, dict):
        source.setdefault("dispatch_id", None)
        source.setdefault("session_id", None)
        source.setdefault("parent_session_id", None)
    presentation = packet.setdefault("presentation", {})
    if isinstance(presentation, dict):
        presentation.setdefault("channel", "none")
        presentation.setdefault("presented_at", None)
        presentation.setdefault("interruption", None)
    question = packet.get("question")
    if isinstance(question, dict) and isinstance(question.get("options"), builtins.list):
        for option in question["options"]:
            if not isinstance(option, dict):
                continue
            route = option.get("route")
            if not isinstance(route, dict):
                continue
            route.setdefault("strategy", "none" if route.get("action") in ("supervisor", "defer", "cancel") else None)
            route.setdefault("role", None)
            route.setdefault("tier", None)
            route.setdefault("prompt", None)
            route.setdefault("operation", None)
    timestamps = packet.setdefault("timestamps", {})
    if isinstance(timestamps, dict):
        timestamps.setdefault("created_at", timestamp)
        timestamps.setdefault("updated_at", timestamps["created_at"])
        for key in ("presented_at", "answered_at", "route_started_at", "resolved_at",
                    "expired_at", "cancelled_at", "superseded_at"):
            timestamps.setdefault(key, None)
    return packet


def _schema_validate(packet: Mapping[str, Any]) -> None:
    errors = _schema().load_validate(SCHEMA_PATH, packet)
    if errors:
        raise ValidationError("human decision schema validation failed:\n  " + "\n  ".join(errors))


def _validate_routes(packet: Mapping[str, Any]) -> None:
    question = packet["question"]
    options = question["options"]
    identifiers = [option["id"] for option in options]
    if len(set(identifiers)) != len(identifiers):
        raise ValidationError("option ids must be unique")
    if question["recommendation"] not in identifiers:
        raise ValidationError("question.recommendation must name an option id")
    for option in options:
        route = option["route"]
        action = route["action"]
        if action == "continue":
            if route["strategy"] not in ("resume", "redispatch"):
                raise ValidationError("continue requires resume or redispatch")
            if route["role"] not in ("development", "review") or route["tier"] not in ("default", "escalated"):
                raise ValidationError("continue requires a controlled development/review role and tier")
            if route["next_status"] not in CONTINUE_STATUSES:
                raise ValidationError("continue cannot close a ticket or set done/needs-human")
            _safe_text(route["prompt"], "continue prompt", 12000)
            if route["operation"] is not None:
                raise ValidationError("continue cannot name a supervisor operation")
            if route["strategy"] == "resume" and not (
                    packet["source"].get("dispatch_id") and packet["source"].get("session_id")):
                raise ValidationError("resume requires source dispatch_id and session_id")
        elif action == "supervisor":
            if route["strategy"] != "none" or route["role"] is not None or route["tier"] is not None:
                raise ValidationError("supervisor uses an operation, never resume/redispatch or model routing")
            if route["operation"] not in REGISTERED_OPERATIONS or route["prompt"] is not None:
                raise ValidationError("supervisor only accepts a registered operation and no free prompt")
            allowed = CONTINUE_STATUSES + (("proposed",) if route["operation"] == "redesign" else ())
            if route["next_status"] not in allowed:
                raise ValidationError("supervisor next_status must be a non-terminal working status")
            if route["operation"] == "redesign" and route["next_status"] != "proposed":
                raise ValidationError("redesign must return the ticket to proposed for a new spec and gate-2")
        else:
            expected = "deferred" if action == "defer" else "cancelled"
            if (route["strategy"] != "none" or route["role"] is not None or route["tier"] is not None
                    or route["prompt"] is not None or route["operation"] is not None
                    or route["next_status"] != expected):
                raise ValidationError("%s only permits the controlled %s close plan" % (action, expected))


def _validate_status(packet: Mapping[str, Any]) -> None:
    status = packet["status"]
    answer, route = packet.get("answer"), packet.get("route")
    timestamps = packet["timestamps"]
    presentation = packet["presentation"]
    if status in ("pending", "waiting") and (answer is not None or route is not None):
        raise ValidationError("pending/waiting cannot contain an answer or route")
    if status == "pending" and presentation.get("presented_at") is not None:
        raise ValidationError("pending cannot retain presented_at")
    if status == "waiting" and presentation.get("presented_at") is None:
        raise ValidationError("waiting requires presented_at")
    if status in ("answered", "routing", "resolved") and answer is None:
        raise ValidationError("%s requires a persisted user answer" % status)
    if status == "routing":
        if route is None or route.get("ack") is not None or route.get("failure") is not None:
            raise ValidationError("routing requires one unacknowledged, unfailed route")
    if status == "resolved":
        if route is None or route.get("ack") is None or route.get("acked_at") is None:
            raise ValidationError("resolved requires an explicit persisted host ack")
        if route["ack"].get("route_id") != route.get("route_id"):
            raise ValidationError("host ack route_id does not match the route")
        normalized_ack = _validated_ack(packet, route, route["ack"], route["acked_at"])
        if normalized_ack != route["ack"]:
            raise ValidationError("persisted host ack is not in normalized verifiable form")
    if status == "answered" and route is not None and route.get("failure") is None:
        raise ValidationError("an answered packet may only retain a known-failed route")
    if status == "expired" and route is not None and route.get("failure") is None:
        raise ValidationError("expired cannot retain an in-flight route")
    if status == "cancelled" and packet.get("cancellation") is None:
        raise ValidationError("cancelled requires explicit cancellation metadata")
    if status != "cancelled" and packet.get("cancellation") is not None:
        raise ValidationError("cancellation metadata is only valid for cancelled")
    if status == "superseded" and not packet.get("superseded_by"):
        raise ValidationError("superseded requires superseded_by")
    for key, expected in (("answered_at", answer is not None),
                          ("route_started_at", route is not None),
                          ("resolved_at", status == "resolved"),
                          ("expired_at", status == "expired"),
                          ("cancelled_at", status == "cancelled"),
                          ("superseded_at", status == "superseded")):
        if (timestamps.get(key) is not None) != expected:
            raise ValidationError("timestamps.%s is inconsistent with packet status" % key)


def _semantic_validate(packet: Mapping[str, Any], repo: Any = None,
                       check_evidence_digest: bool = False) -> None:
    _scan_security(packet)
    if packet.get("schema") != SCHEMA:
        raise ValidationError("unsupported decision schema")
    if not _ID.fullmatch(packet.get("id", "")):
        raise ValidationError("invalid decision id")
    scope = packet.get("scope", {})
    if not _FEATURE.fullmatch(scope.get("feature", "")) or not _ISSUE.fullmatch(scope.get("issue", "")):
        raise ValidationError("invalid feature/issue scope")
    _scope_key(scope)
    repo_name = scope.get("repo")
    if repo_name != "." and (not isinstance(repo_name, str) or not _REPO_NAME.fullmatch(repo_name)
                             or repo_name in (".", "..")):
        raise ValidationError("scope.repo must be '.' or a repository label, never a filesystem path")
    root = _repo_root(repo) if repo is not None else None
    if root is not None:
        packet_path(root, _scope_key(scope), packet["id"])
    for item in packet.get("evidence", []):
        if root is not None:
            path = _relative_evidence(root, item["path"])
        else:
            relative = Path(item["path"])
            if (relative.is_absolute() or "\\" in item["path"] or relative.as_posix() != item["path"]
                    or any(part in ("", ".", "..") for part in relative.parts)):
                raise ValidationError("evidence.path must be repository-relative without traversal")
            path = None
        if check_evidence_digest and path is not None:
            if not path.is_file():
                raise ValidationError("evidence file does not exist: %s" % item["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item["sha256"]:
                raise ValidationError("evidence digest does not match: %s" % item["path"])
    question = packet["question"]
    _validate_visible(question["context"], "question.context", (1, 2))
    _validate_visible(question["decision"], "question.decision", (1, 1))
    _validate_visible(question["why"], "question.why", (1, 1))
    _validate_visible(packet["presentation"]["header"], "presentation.header")
    if len(packet["presentation"]["header"]) > 12:
        raise ValidationError("presentation.header exceeds 12 characters")
    for option in question["options"]:
        _validate_visible(option["label"], "option.label")
        _validate_visible(option["description"], "option.description")
        if re.fullmatch(r"(?:[A-D]|方案\s*[A-D]|continue|defer|cancel)", option["label"].strip(), re.I):
            raise ValidationError("option labels must describe actions, not letters or route names")
    _validate_routes(packet)
    _validate_status(packet)
    if packet.get("expires_at") is not None:
        _parse_time(packet["expires_at"])


def _validate_packet(packet: Mapping[str, Any], repo: Any = None) -> Dict[str, Any]:
    if not isinstance(packet, Mapping):
        raise ValidationError("decision packet must be an object")
    candidate = _deepcopy_json(dict(packet))
    _schema_validate(candidate)
    _semantic_validate(candidate, repo=repo)
    return candidate


def validate_request(envelope: Mapping[str, Any], *, repo: Any = None,
                     now: Any = None) -> Dict[str, Any]:
    """Validate and normalize an *unpersisted* request envelope.

    This is the only write-side function implementation/review sessions should
    call.  It performs no filesystem writes and returns a pending packet that a
    main session may pass to :func:`request`.
    """
    packet = _normalize_request(envelope, now)
    if packet.get("status") != "pending" or packet.get("answer") is not None or packet.get("route") is not None:
        raise ValidationError("a request envelope may only describe a new pending packet")
    try:
        encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError("request envelope must be finite JSON") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValidationError("request envelope exceeds the 128 KiB aggregate limit")
    _schema_validate(packet)
    _semantic_validate(packet, repo=repo, check_evidence_digest=repo is not None)
    return packet


def _load(path: Path, repo: Any = None) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValidationError("non-finite JSON")))
    except DecisionError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValidationError("cannot load decision packet %s: %s" % (path, exc)) from exc
    packet = _validate_packet(data, repo=repo)
    if repo is not None:
        expected = packet_path(repo, _scope_key(packet["scope"]), packet["id"])
        if path.resolve(strict=False) != expected.resolve(strict=False):
            raise ValidationError("decision packet path does not match its scope/id")
    return packet


def _save(path: Path, packet: Mapping[str, Any]) -> None:
    _store().atomic_json_write(path, packet)


def _issue_path(repo: Any, packet: Mapping[str, Any]) -> Path:
    feature_dir = _feature_dir(repo, _scope_key(packet["scope"]))
    issue_id = packet["scope"]["issue"]
    issues_dir = _inside(_repo_root(repo), feature_dir / "issues", "issues")
    candidates = sorted(path for path in issues_dir.glob("*.md") if not path.is_symlink())
    named = [path for path in candidates if re.fullmatch(re.escape(issue_id) + r"(?:-[^/]+)?\.md", path.name)]
    if len(named) == 1:
        return _inside(_repo_root(repo), named[0], "issue")
    matches = []
    for path in candidates:
        try:
            metadata = _config().read_frontmatter(path)
        except Exception:
            continue
        if metadata.get("issue-id") == issue_id:
            matches.append(path)
    if len(matches) != 1:
        raise ValidationError("expected exactly one safe ticket file for %s, found %d" % (issue_id, len(matches)))
    return _inside(_repo_root(repo), matches[0], "issue")


def _issue_metadata(repo: Any, packet: Mapping[str, Any]) -> Tuple[Path, Dict[str, Any]]:
    path = _issue_path(repo, packet)
    try:
        metadata = _config().read_frontmatter(path)
    except Exception as exc:
        raise ValidationError("cannot parse ticket frontmatter: %s" % exc) from exc
    return path, metadata


def _metadata_span(text: str) -> Tuple[int, int]:
    if text.startswith("---"):
        match = re.search(r"\A---[ \t]*\n(.*?)^---[ \t]*(?:\n|$)", text, re.S | re.M)
        if match:
            return match.span(1)
    match = re.search(r"^```yaml[ \t]*\n(.*?)^```[ \t]*(?:\n|$)", text, re.S | re.M)
    if match:
        return match.span(1)
    raise ValidationError("ticket has no writable frontmatter/YAML metadata block")


def _atomic_text_write(path: Path, text: str) -> None:
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
        mode = path.stat().st_mode & 0o777
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory = os.open(str(path.parent), flags)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _set_issue_status(repo: Any, packet: Mapping[str, Any], target: str) -> Tuple[str, bool]:
    path, metadata = _issue_metadata(repo, packet)
    current = metadata.get("status")
    if current == target:
        return str(current), False
    if current == "done" or target == "done":
        raise ConflictError("human decisions never reopen or directly complete a done ticket")
    if current in ("deferred", "cancelled") and target == "needs-human":
        raise ConflictError("an active decision cannot silently reopen a closed ticket")
    if target not in CONTINUE_STATUSES + ("proposed", "needs-human", "deferred", "cancelled"):
        raise ValidationError("unsupported ticket status transition: %s" % target)
    text = path.read_text(encoding="utf-8")
    start, end = _metadata_span(text)
    block = text[start:end]
    replacement = "status: " + target
    if re.search(r"^status\s*:", block, re.M):
        block = re.sub(r"^status\s*:[^\n]*", replacement, block, count=1, flags=re.M)
    else:
        block = replacement + "\n" + block
    _atomic_text_write(path, text[:start] + block + text[end:])
    return str(current), True


def _event_base(packet: Mapping[str, Any], state: str, timestamp: str,
                route_id: Optional[str]) -> Dict[str, Any]:
    return {
        "ts": _progress_ts(timestamp),
        "event": "human-decision",
        "issue": packet["scope"]["issue"],
        "decision_id": packet["id"],
        "state": state,
        "route_id": route_id,
    }


def _append_event(repo: Any, packet: Mapping[str, Any], event: Dict[str, Any], key: str) -> None:
    _store().append_event_once(_progress_path(repo, _scope_key(packet["scope"])), event, key)


def _append_needs_human(repo: Any, packet: Mapping[str, Any]) -> None:
    event = {
        "ts": _progress_ts(packet["timestamps"]["created_at"]),
        "event": "needs-human",
        "issue": packet["scope"]["issue"],
        "decision_id": packet["id"],
        "note": _one_line(packet["question"]["decision"]),
    }
    _append_event(repo, packet, event, "needs-human:" + packet["id"])


def _decision_events(packet: Mapping[str, Any]) -> List[Tuple[Dict[str, Any], str]]:
    status = packet["status"]
    timestamps = packet["timestamps"]
    result = []
    result.append((_event_base(packet, "pending", timestamps["created_at"], None),
                   "human-decision:%s:pending" % packet["id"]))
    presentation = packet["presentation"]
    interruption = presentation.get("interruption")
    if interruption:
        digest = hashlib.sha256((interruption["kind"] + interruption["presented_at"]).encode("utf-8")).hexdigest()[:12]
        event = _event_base(packet, "pending", interruption["at"], None)
        event["note"] = "presentation " + interruption["kind"] + "; no answer inferred"
        result.append((event, "human-decision:%s:pending:%s" % (packet["id"], digest)))
    if presentation.get("presented_at"):
        presentation_digest = hashlib.sha256(presentation["presented_at"].encode("utf-8")).hexdigest()[:12]
        result.append((_event_base(packet, "waiting", presentation["presented_at"], None),
                       "human-decision:%s:waiting:%s" % (packet["id"], presentation_digest)))
    answer = packet.get("answer")
    if answer:
        result.append((_event_base(packet, "answered", answer["answered_at"], None),
                       "human-decision:%s:answered" % packet["id"]))
    route = packet.get("route")
    if route:
        route_id = route["route_id"]
        plan = route["plan"]
        routing = _event_base(packet, "routing", route["started_at"], route_id)
        routing.update({"strategy": plan["strategy"], "attempts": plan["attempts_after"],
                        "next_status": plan["next_status"]})
        result.append((routing, "human-decision:%s:routing:%s" % (packet["id"], route_id)))
        if route.get("failure"):
            failed = _event_base(packet, "answered", route["failed_at"], route_id)
            failed["note"] = route["failure"]["reason"]
            result.append((failed, "human-decision:%s:answered:%s" % (packet["id"], route_id)))
        if route.get("ack"):
            resolved = _event_base(packet, "resolved", route["acked_at"], route_id)
            resolved.update({"strategy": plan["strategy"], "attempts": plan["attempts_after"],
                             "next_status": plan["next_status"]})
            result.append((resolved, "human-decision:%s:resolved:%s" % (packet["id"], route_id)))
    if status == "expired":
        result.append((_event_base(packet, "expired", timestamps["expired_at"], None),
                       "human-decision:%s:expired" % packet["id"]))
    elif status == "cancelled":
        event = _event_base(packet, "cancelled", timestamps["cancelled_at"], None)
        event.update({"next_status": "cancelled", "note": packet["cancellation"]["reason"]})
        result.append((event, "human-decision:%s:cancelled" % packet["id"]))
    elif status == "superseded":
        event = _event_base(packet, "superseded", timestamps["superseded_at"], None)
        event["superseded_by"] = packet["superseded_by"]
        result.append((event, "human-decision:%s:superseded" % packet["id"]))
    return result


def _ensure_events(repo: Any, packet: Mapping[str, Any]) -> None:
    _append_needs_human(repo, packet)
    for event, key in _decision_events(packet):
        _append_event(repo, packet, event, key)


def _ensure_active(repo: Any, packet: Mapping[str, Any]) -> None:
    _set_issue_status(repo, packet, "needs-human")
    _ensure_events(repo, packet)


def _ensure_resolved(repo: Any, packet: Mapping[str, Any]) -> None:
    route = packet.get("route")
    if not route or not route.get("ack") or not route.get("acked_at"):
        raise ConflictError("refusing to resolve/reconcile a ticket without a persisted host ack")
    _set_issue_status(repo, packet, route["plan"]["next_status"])
    _ensure_events(repo, packet)


def _fingerprint(packet: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": packet["schema"],
        "id": packet["id"],
        "scope": packet["scope"],
        "source": packet["source"],
        "basis": packet["basis"],
        "question": packet["question"],
        "evidence": packet["evidence"],
        "header": packet["presentation"]["header"],
        "expires_at": packet["expires_at"],
        "supersedes": packet["supersedes"],
    }


def _feature_packets(repo: Any, feature: str) -> List[Dict[str, Any]]:
    decisions = _feature_dir(repo, feature) / "decisions"
    packets = []
    for path in sorted(decisions.glob("*.json")):
        if path.is_symlink():
            raise ValidationError("symlinked decision packets are forbidden: %s" % path)
        packets.append(_load(path, repo=repo))
    return packets


def request(repo: Any, envelope: Mapping[str, Any], *, main_session: bool = False,
            unattended: bool = False, now: Any = None) -> Dict[str, Any]:
    """Persist one pending packet and put its ticket into needs-human.

    ``unattended`` deliberately changes nothing in the state machine: no host
    question is invoked and the packet remains pending until a person answers.
    """
    _require_main(main_session)
    candidate = validate_request(envelope, repo=repo, now=now)
    feature = _scope_key(candidate["scope"])
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, candidate["id"])
        if path.exists():
            existing = _load(path, repo=repo)
            if _fingerprint(existing) != _fingerprint(candidate):
                if existing["basis"] != candidate["basis"]:
                    raise BasisChanged("the same decision id cannot be reused with a new basis")
                raise ConflictError("the same decision id was reused with different request data")
            if existing["status"] in ACTIVE_STATUSES:
                _ensure_active(repo, existing)
            else:
                _ensure_events(repo, existing)
            return existing
        superseded = None
        if candidate.get("supersedes"):
            old_path = packet_path(repo, feature, candidate["supersedes"])
            if not old_path.exists():
                raise ValidationError("supersedes names a missing decision packet")
            superseded = _load(old_path, repo=repo)
            if superseded["scope"] != candidate["scope"]:
                raise ValidationError("a replacement must keep the same repo/feature/issue scope")
            if superseded["status"] not in ACTIVE_STATUSES:
                raise ConflictError("only an active decision can be superseded")
            if superseded["status"] == "routing":
                raise ConflictError("routing outcome must be reconciled before supersession or redispatch")
        for other in _feature_packets(repo, feature):
            if other["scope"]["issue"] != candidate["scope"]["issue"] or other["status"] not in ACTIVE_STATUSES:
                continue
            if superseded is None or other["id"] != superseded["id"]:
                raise ActiveDecisionExists("ticket already has active decision: " + other["id"])
        # New first: a crash may leave two linked packets, which reconcile can
        # finish without inventing an answer.  The reverse order could leave no
        # active packet for a needs-human ticket and an unrecoverable old basis.
        _save(path, candidate)
        if superseded is not None:
            timestamp = candidate["timestamps"]["created_at"]
            superseded["status"] = "superseded"
            superseded["superseded_by"] = candidate["id"]
            superseded["timestamps"]["superseded_at"] = timestamp
            superseded["timestamps"]["updated_at"] = timestamp
            _validate_packet(superseded, repo=repo)
            _save(old_path, superseded)
            _ensure_events(repo, superseded)
        _ensure_active(repo, candidate)
        return candidate


def supersede(repo: Any, decision_id: str, envelope: Mapping[str, Any], *,
              main_session: bool = False, unattended: bool = False,
              now: Any = None) -> Dict[str, Any]:
    """Replace an active (including expired) packet with a fresh basis."""
    if not isinstance(envelope, Mapping):
        raise ValidationError("replacement envelope must be an object")
    candidate = _deepcopy_json(dict(envelope))
    if candidate.get("supersedes") not in (None, decision_id):
        raise ConflictError("replacement supersedes points at another decision")
    candidate["supersedes"] = decision_id
    return request(repo, candidate, main_session=main_session, unattended=unattended, now=now)


def show(repo: Any, feature: str, decision_id: str) -> Dict[str, Any]:
    return _load(packet_path(repo, feature, decision_id), repo=repo)


def list(repo: Any, feature: Optional[str] = None, issue: Optional[str] = None,
         status: Optional[str] = None, active: Optional[bool] = None) -> List[Dict[str, Any]]:
    root = _repo_root(repo)
    if feature is not None:
        features = [feature]
        _feature_dir(root, feature)
    else:
        features = sorted({path.parent.parent.relative_to(root / "dev" / "features").as_posix()
                           for path in _sibling("artifact_scope").scoped_glob(root, "decisions/*.json")})
    result = []
    for feature_name in features:
        for packet in _feature_packets(root, feature_name):
            if issue is not None and packet["scope"]["issue"] != issue:
                continue
            if status is not None and packet["status"] != status:
                continue
            if active is True and packet["status"] not in ACTIVE_STATUSES:
                continue
            if active is False and packet["status"] in ACTIVE_STATUSES:
                continue
            result.append(packet)
    result.sort(key=lambda packet: (_scope_key(packet["scope"]), packet["scope"]["issue"],
                                    packet["timestamps"]["created_at"], packet["id"]))
    return result


def active_for_issue(repo: Any, feature: str, issue: str) -> Optional[Dict[str, Any]]:
    matches = [packet for packet in _feature_packets(repo, feature)
               if packet["scope"]["issue"] == issue and packet["status"] in ACTIVE_STATUSES]
    if len(matches) > 1:
        raise ConflictError("multiple active decisions for %s: %s" %
                            (issue, ", ".join(packet["id"] for packet in matches)))
    return matches[0] if matches else None


def active_for_feature(repo: Any, feature: str) -> List[Dict[str, Any]]:
    packets = [packet for packet in _feature_packets(repo, feature) if packet["status"] in ACTIVE_STATUSES]
    packets.sort(key=lambda packet: (packet["scope"]["issue"], packet["timestamps"]["created_at"], packet["id"]))
    return packets


def to_question_component(packet: Mapping[str, Any]) -> Dict[str, Any]:
    """Deterministically project exactly one human-only AskUserQuestion."""
    question = packet["question"]
    header = _one_line(packet["presentation"]["header"])
    _validate_visible(header, "presentation.header")
    if len(header) > 12:
        raise ValidationError("question component header exceeds 12 characters")
    recommendation = question["recommendation"]
    if recommendation not in {option["id"] for option in question["options"]}:
        raise ValidationError("question.recommendation must name an option id")
    ordered = sorted(question["options"], key=lambda option: 0 if option["id"] == recommendation else 1)
    if not 2 <= len(ordered) <= 4:
        raise ValidationError("question component requires 2-4 options")
    recommended = next(option for option in ordered if option["id"] == recommendation)
    recommended_label = re.sub(r"[（(]推荐[）)]$", "", _one_line(recommended["label"])).strip()
    text = (_sentence(question["context"])
            + "需要你决定：" + _without_terminal(question["decision"])
            + "；我不能自行拍板，因为" + _without_terminal(question["why"]) + "。"
            + "我建议选择“" + recommended_label + "”，但最终由你决定。")
    if not 2 <= _sentence_count(text) <= 4:
        raise ValidationError("question component must contain 2-4 sentences")
    options = []
    for option in ordered:
        label = _one_line(option["label"])
        if option["id"] == recommendation:
            label = recommended_label + "（推荐）"
        description = _one_line(option["description"])
        _validate_visible(label, "option.label")
        _validate_visible(description, "option.description")
        options.append({"label": label, "description": description})
    component = {"questions": [{
        "header": header,
        "question": text,
        "multiSelect": False,
        "options": options,
    }]}
    # Keep this explicit: adding ids/routes/previews to a host projection is a
    # contract break even if the underlying packet is valid.
    if set(component["questions"][0]) != {"header", "question", "multiSelect", "options"}:
        raise AssertionError("invalid question projection shape")
    return component


def to_text_fallback(packet: Mapping[str, Any]) -> str:
    component = to_question_component(packet)["questions"][0]
    lines = ["【" + component["header"] + "】", component["question"], ""]
    for index, option in enumerate(component["options"], 1):
        lines.append("%d. %s" % (index, option["label"]))
        lines.append("   " + option["description"])
    lines.extend(["", "请回复选项编号，或直接说明你的选择。"])
    return "\n".join(lines)


def present(repo: Any, feature: str, decision_id: str, *, main_session: bool = False,
            channel: str = "component", unattended: bool = False, now: Any = None) -> Dict[str, Any]:
    _require_main(main_session)
    if channel not in ("component", "text"):
        raise ValidationError("presentation channel must be component or text")
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        if unattended:
            return packet
        if packet["status"] == "waiting":
            # Projection is pure; rebuild it to validate the requested channel
            # without writing a second event.
            if channel == "component":
                to_question_component(packet)
            else:
                to_text_fallback(packet)
            _ensure_events(repo, packet)
            return packet
        if packet["status"] != "pending":
            raise StateTransitionError("only pending can become waiting")
        timestamp = _now(now)
        packet["status"] = "waiting"
        packet["presentation"]["channel"] = channel
        packet["presentation"]["presented_at"] = timestamp
        packet["presentation"]["interruption"] = None
        packet["timestamps"]["presented_at"] = timestamp
        packet["timestamps"]["updated_at"] = timestamp
        if channel == "component":
            to_question_component(packet)
        else:
            to_text_fallback(packet)
        _validate_packet(packet, repo=repo)
        _save(path, packet)
        _ensure_events(repo, packet)
        return packet


def _presentation_pending(repo: Any, feature: str, decision_id: str, kind: str,
                          main_session: bool, now: Any) -> Dict[str, Any]:
    _require_main(main_session)
    if kind not in ("cancel", "timeout"):
        raise ValidationError("interruption kind must be cancel or timeout")
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        if packet["status"] == "pending":
            return packet
        if packet["status"] != "waiting":
            raise StateTransitionError("a late cancel/timeout cannot rewrite an answered or routed packet")
        timestamp = _now(now)
        presented_at = packet["presentation"]["presented_at"]
        packet["status"] = "pending"
        packet["presentation"]["interruption"] = {
            "kind": kind, "at": timestamp, "presented_at": presented_at,
        }
        packet["presentation"]["channel"] = "none"
        packet["presentation"]["presented_at"] = None
        packet["timestamps"]["presented_at"] = None
        packet["timestamps"]["updated_at"] = timestamp
        _validate_packet(packet, repo=repo)
        _save(path, packet)
        _ensure_events(repo, packet)
        return packet


def presentation_pending(repo: Any, feature: str, decision_id: str, *, kind: str,
                         main_session: bool = False, now: Any = None) -> Dict[str, Any]:
    return _presentation_pending(repo, feature, decision_id, kind, main_session, now)


def cancel_presentation(repo: Any, feature: str, decision_id: str, *,
                        main_session: bool = False, now: Any = None) -> Dict[str, Any]:
    return _presentation_pending(repo, feature, decision_id, "cancel", main_session, now)


def timeout_presentation(repo: Any, feature: str, decision_id: str, *,
                         main_session: bool = False, now: Any = None) -> Dict[str, Any]:
    return _presentation_pending(repo, feature, decision_id, "timeout", main_session, now)


def answer(repo: Any, feature: str, decision_id: str, *, option_id: str, text: str,
           current_basis: Optional[Mapping[str, Any]] = None,
           basis: Optional[Mapping[str, Any]] = None,
           main_session: bool = False, now: Any = None) -> Dict[str, Any]:
    _require_main(main_session)
    raw = _safe_text(text, "answer.text", 4000)
    observed_basis = _basis_from_argument(current_basis, basis)
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        _assert_basis(packet, observed_basis)
        existing = packet.get("answer")
        if existing is not None:
            if existing["option_id"] != option_id or existing["text"] != raw:
                raise ConflictError("answer idempotency rejects a different option or raw text")
            _ensure_events(repo, packet)
            return packet
        if packet["status"] != "waiting":
            raise StateTransitionError("only a waiting question can accept a person's answer")
        if option_id not in {option["id"] for option in packet["question"]["options"]}:
            raise ValidationError("answer option_id is not one of the declared options")
        timestamp = _now(now)
        packet["answer"] = {"option_id": option_id, "text": raw, "answered_at": timestamp}
        packet["status"] = "answered"
        packet["timestamps"]["answered_at"] = timestamp
        packet["timestamps"]["updated_at"] = timestamp
        _validate_packet(packet, repo=repo)
        _save(path, packet)
        _ensure_events(repo, packet)
        return packet


def _attempts(repo: Any, packet: Mapping[str, Any]) -> int:
    _, metadata = _issue_metadata(repo, packet)
    dispatch = metadata.get("dispatch")
    value = dispatch.get("attempts", 0) if isinstance(dispatch, dict) else 0
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def route_begin(repo: Any, feature: str, decision_id: str, *,
                current_basis: Optional[Mapping[str, Any]] = None,
                basis: Optional[Mapping[str, Any]] = None,
                parent_session_id: Optional[str] = None,
                main_session: bool = False, now: Any = None) -> Dict[str, Any]:
    _require_main(main_session)
    observed_basis = _basis_from_argument(current_basis, basis)
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        _assert_basis(packet, observed_basis)
        if packet["status"] in ("routing", "resolved"):
            _ensure_events(repo, packet)
            return packet
        if packet["status"] != "answered":
            raise StateTransitionError("route_begin requires an unanswered-route answered packet")
        answer_value = packet["answer"]
        option = next(option for option in packet["question"]["options"]
                      if option["id"] == answer_value["option_id"])
        selected = option["route"]
        timestamp = _now(now)
        before = _attempts(repo, packet)
        strategy = selected["strategy"]
        if strategy == "redispatch":
            after = before + 1
            dispatch_id = "dispatch-" + uuid.uuid4().hex
            session_id = None
            kind = "redispatch"
        elif strategy == "resume":
            after = before
            dispatch_id = packet["source"]["dispatch_id"]
            session_id = packet["source"]["session_id"]
            kind = "resume"
        elif selected["action"] == "supervisor":
            after = before
            dispatch_id = packet["source"]["dispatch_id"]
            session_id = packet["source"]["session_id"]
            kind = "operation"
        else:
            after = before
            dispatch_id = packet["source"]["dispatch_id"]
            session_id = packet["source"]["session_id"]
            kind = "close"
        plan = {
            "kind": kind,
            "action": selected["action"],
            "strategy": strategy,
            "host": packet["source"]["host"],
            "role": selected["role"],
            "tier": selected["tier"],
            "next_status": selected["next_status"],
            "prompt": selected["prompt"],
            "operation": selected["operation"],
            "dispatch_id": dispatch_id,
            "session_id": session_id,
            "parent_session_id": parent_session_id or packet["source"].get("parent_session_id"),
            "attempts_before": before,
            "attempts_after": after,
        }
        route_id = "route-" + uuid.uuid4().hex
        packet["route"] = {
            "route_id": route_id,
            "option_id": option["id"],
            "action": selected["action"],
            "plan": plan,
            "started_at": timestamp,
            "ack": None,
            "acked_at": None,
            "failure": None,
            "failed_at": None,
        }
        packet["status"] = "routing"
        packet["timestamps"]["route_started_at"] = timestamp
        packet["timestamps"]["updated_at"] = timestamp
        _validate_packet(packet, repo=repo)
        _save(path, packet)
        _ensure_events(repo, packet)
        return packet


def _validated_ack(packet: Mapping[str, Any], route: Mapping[str, Any],
                   ack: Optional[Mapping[str, Any]], timestamp: str) -> Dict[str, Any]:
    """Return a normalized host receipt only after all available IDs match.

    A host receipt is an alternative to dispatch/session identity, not a way to
    ignore IDs that are supplied.  Thus a wrong host, route, dispatch or known
    session is always rejected, including on an idempotent resolved replay.
    """
    if not isinstance(ack, Mapping) or not ack:
        raise ValidationError("route_ack requires a non-empty verifiable host acknowledgement")
    result = dict(ack)
    allowed = {"route_id", "at", "host", "dispatch_id", "session_id", "receipt", "note"}
    if set(result) - allowed:
        raise ValidationError("host ack contains unknown fields")
    if not isinstance(result.get("host"), str) or not result["host"].strip():
        raise ValidationError("host ack requires host")
    if result["host"] != packet["source"]["host"]:
        raise ConflictError("host ack host does not match decision source identity")
    if not isinstance(result.get("route_id"), str) or not result["route_id"]:
        raise ValidationError("host ack requires route_id")
    if result["route_id"] != route["route_id"]:
        raise ConflictError("host ack route_id disagrees with the persisted route")
    has_receipt = isinstance(result.get("receipt"), str) and bool(result["receipt"].strip())
    if "receipt" in result and not has_receipt:
        raise ValidationError("host receipt must be non-empty text")
    has_identity = result.get("dispatch_id") is not None or result.get("session_id") is not None
    if not has_receipt and not has_identity:
        raise ValidationError("host ack requires a host receipt or a dispatch_id/session_id identity")
    if "dispatch_id" in result:
        if not isinstance(result["dispatch_id"], str) or not result["dispatch_id"]:
            raise ValidationError("host ack dispatch_id must be a non-empty stable ID")
        if result["dispatch_id"] != route["plan"]["dispatch_id"]:
            raise ConflictError("host ack dispatch_id does not match the current route plan")
    if "session_id" in result:
        if not isinstance(result["session_id"], str) or not result["session_id"]:
            raise ValidationError("host ack session_id must be a non-empty stable ID")
        expected_session = route["plan"]["session_id"]
        if expected_session is not None and result["session_id"] != expected_session:
            raise ConflictError("host ack session_id does not match the resumed/source session")
    result["at"] = _now(result.get("at") or timestamp)
    _scan_security(result, "ack")
    return result


def route_ack(repo: Any, feature: str, decision_id: str, *, route_id: str,
              ack: Optional[Mapping[str, Any]] = None, main_session: bool = False,
              now: Any = None) -> Dict[str, Any]:
    """Persist a verified host acknowledgement, resolve, then write next_status."""
    _require_main(main_session)
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        route = packet.get("route")
        if not route or route["route_id"] != route_id:
            raise ConflictError("route_id does not match the persisted route")
        if packet["status"] == "resolved":
            receipt = _validated_ack(packet, route, ack, route["acked_at"])
            if receipt != route["ack"]:
                raise ConflictError("resolved route cannot be acknowledged with different receipt data")
            _ensure_resolved(repo, packet)
            return packet
        if packet["status"] != "routing":
            raise StateTransitionError("only routing can be acknowledged")
        timestamp = _now(now)
        receipt = _validated_ack(packet, route, ack, timestamp)
        route["ack"] = receipt
        route["acked_at"] = timestamp
        route["failure"] = None
        route["failed_at"] = None
        packet["status"] = "resolved"
        packet["timestamps"]["resolved_at"] = timestamp
        packet["timestamps"]["updated_at"] = timestamp
        _validate_packet(packet, repo=repo)
        # Packet/ack first.  Reconcile may finish ticket/event writes, but may
        # never manufacture this ack for a packet still in routing.
        _save(path, packet)
        _ensure_resolved(repo, packet)
        return packet


def route_fail(repo: Any, feature: str, decision_id: str, *, route_id: str,
               reason: str, outcome_known: bool = False,
               main_session: bool = False, now: Any = None) -> Dict[str, Any]:
    _require_main(main_session)
    if outcome_known is not True:
        raise ConflictError("unknown routing outcome cannot fail back or be redispatched; reconcile first")
    detail = _safe_text(reason, "route failure", 1000)
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        route = packet.get("route")
        if not route or route["route_id"] != route_id:
            raise ConflictError("route_id does not match the persisted route")
        if packet["status"] == "answered" and route.get("failure"):
            if route["failure"]["reason"] != detail:
                raise ConflictError("route failure idempotency rejects different failure text")
            _ensure_active(repo, packet)
            return packet
        if packet["status"] != "routing":
            raise StateTransitionError("only routing can fail back to answered")
        timestamp = _now(now)
        route["failure"] = {"reason": detail, "at": timestamp, "outcome_known": True}
        route["failed_at"] = timestamp
        route["ack"] = None
        route["acked_at"] = None
        packet["status"] = "answered"
        packet["timestamps"]["updated_at"] = timestamp
        _validate_packet(packet, repo=repo)
        _save(path, packet)
        _ensure_active(repo, packet)
        return packet


def expire(repo: Any, feature: str, decision_id: str, *, main_session: bool = False,
           now: Any = None) -> Dict[str, Any]:
    _require_main(main_session)
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        if packet["status"] == "expired":
            _ensure_active(repo, packet)
            return packet
        if packet["status"] not in ("pending", "waiting", "answered"):
            raise StateTransitionError("only pending/waiting/answered can explicitly expire")
        if packet["expires_at"] is None:
            raise ValidationError("packet has no expires_at; use supersede to revise an open-ended request")
        timestamp = _now(now)
        if _parse_time(timestamp) < _parse_time(packet["expires_at"]):
            raise StateTransitionError("expires_at has not been reached; cancel/timeout presentation stays pending")
        packet["status"] = "expired"
        packet["timestamps"]["expired_at"] = timestamp
        packet["timestamps"]["updated_at"] = timestamp
        _validate_packet(packet, repo=repo)
        _save(path, packet)
        _ensure_active(repo, packet)
        return packet


def cancel(repo: Any, feature: str, decision_id: str, *, reason: str,
           main_session: bool = False, now: Any = None) -> Dict[str, Any]:
    """Explicitly cancel a ticket request; never used for card dismissal."""
    _require_main(main_session)
    detail = _safe_text(reason, "cancellation reason", 1000)
    with _store().FileLock(_lock_path(repo, feature)):
        path = packet_path(repo, feature, decision_id)
        packet = _load(path, repo=repo)
        if packet["status"] == "cancelled":
            if packet["cancellation"]["reason"] != detail:
                raise ConflictError("cancellation idempotency rejects a different reason")
            _set_issue_status(repo, packet, "cancelled")
            _ensure_events(repo, packet)
            return packet
        if packet["status"] not in ("pending", "waiting", "answered", "expired"):
            raise StateTransitionError("routing must be reconciled first; resolved/superseded cannot cancel")
        timestamp = _now(now)
        packet["status"] = "cancelled"
        packet["cancellation"] = {"reason": detail, "next_status": "cancelled", "cancelled_at": timestamp}
        packet["timestamps"]["cancelled_at"] = timestamp
        packet["timestamps"]["updated_at"] = timestamp
        _validate_packet(packet, repo=repo)
        _save(path, packet)
        _set_issue_status(repo, packet, "cancelled")
        _ensure_events(repo, packet)
        return packet


def _supersede_from_link(repo: Any, old: Dict[str, Any], new: Mapping[str, Any],
                         repairs: List[str]) -> Dict[str, Any]:
    old["status"] = "superseded"
    old["superseded_by"] = new["id"]
    old["timestamps"]["superseded_at"] = new["timestamps"]["created_at"]
    old["timestamps"]["updated_at"] = new["timestamps"]["created_at"]
    _validate_packet(old, repo=repo)
    _save(packet_path(repo, _scope_key(old["scope"]), old["id"]), old)
    repairs.append("superseded %s by linked replacement %s" % (old["id"], new["id"]))
    return old


def _latest_terminal(group: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Select the unique latest resolved/cancelled packet by its own effective time."""
    terminals = []
    for packet in group:
        if packet["status"] == "resolved":
            field = "resolved_at"
        elif packet["status"] == "cancelled":
            field = "cancelled_at"
        else:
            continue
        value = packet["timestamps"].get(field)
        try:
            effective = _parse_time(value)
        except DecisionError as exc:
            raise ConflictError(
                "terminal %s has corrupt %s; no terminal winner selected" % (packet["id"], field)) from exc
        terminals.append((effective, packet))
    if not terminals:
        return None
    newest = max(effective for effective, _ in terminals)
    winners = [packet for effective, packet in terminals if effective == newest]
    if len(winners) != 1:
        raise ConflictError("terminal effective-time tie: " + ", ".join(packet["id"] for packet in winners))
    return winners[0]


def classify(repo: Any, feature: Optional[str] = None) -> Dict[str, Any]:
    """Read-only, explicit reconciliation categories for cleanup aggregators.

    No lock file, packet, ticket or event is created.  Callers can aggregate
    ``issues`` and invoke ``reconcile`` only for the selected repositories.
    """
    root = _repo_root(repo)
    if feature is None:
        feature_root = root / "dev" / "features"
        issue_features = {path.parent.relative_to(feature_root).as_posix()
                          for path in _sibling("artifact_scope").scoped_glob(root, "issues") if path.is_dir()}
        decision_features = {path.parent.parent.relative_to(feature_root).as_posix()
                             for path in _sibling("artifact_scope").scoped_glob(root, "decisions/*.json")}
        features = sorted(issue_features | decision_features)
    else:
        features = [feature]
        _feature_dir(root, feature)
    report: Dict[str, Any] = {"ok": True, "issues": [], "errors": []}
    for feature_name in features:
        try:
            packets = _feature_packets(root, feature_name)
        except DecisionError as exc:
            report["errors"].append({"feature": feature_name, "error": str(exc)})
            continue
        by_issue: Dict[str, List[Dict[str, Any]]] = {}
        for packet in packets:
            by_issue.setdefault(packet["scope"]["issue"], []).append(packet)
        metadata_by_issue = {}
        issues_dir = _feature_dir(root, feature_name) / "issues"
        for issue_path in sorted(issues_dir.glob("*.md")):
            try:
                metadata = _config().read_frontmatter(issue_path)
                issue_id = metadata.get("issue-id")
                if isinstance(issue_id, str):
                    metadata_by_issue[issue_id] = metadata
            except Exception as exc:
                report["errors"].append({"feature": feature_name, "issue": issue_path.name,
                                         "error": str(exc)})
        for issue in sorted(set(by_issue) | set(metadata_by_issue)):
            group = by_issue.get(issue, [])
            metadata = metadata_by_issue.get(issue, {})
            entry: Dict[str, Any] = {"feature": feature_name, "issue": issue,
                                     "ticket_status": metadata.get("status"),
                                     "category": "inactive", "action": "none",
                                     "decision_id": None, "route_id": None,
                                     "target_status": None}
            active = [packet for packet in group if packet["status"] in ACTIVE_STATUSES]
            if len(active) > 1:
                entry.update({"category": "multiple-active", "action": "report"})
                report["ok"] = False
            elif active:
                packet = active[0]
                entry.update({"category": "unknown-routing" if packet["status"] == "routing" else "active",
                              "action": "reconcile-host" if packet["status"] == "routing" else "ensure-active",
                              "decision_id": packet["id"],
                              "route_id": packet["route"]["route_id"] if packet["status"] == "routing" else None,
                              "target_status": "needs-human"})
            elif metadata.get("status") == "needs-human":
                try:
                    latest = _latest_terminal(group)
                except ConflictError as exc:
                    entry.update({"category": "terminal-conflict", "action": "report"})
                    entry["error"] = str(exc)
                    report["ok"] = False
                else:
                    if latest is None:
                        entry.update({"category": "orphan-needs-human", "action": "report"})
                        report["ok"] = False
                    else:
                        target = ("cancelled" if latest["status"] == "cancelled"
                                  else latest["route"]["plan"]["next_status"])
                        entry.update({"category": "apply-" + latest["status"],
                                      "action": "apply-terminal", "decision_id": latest["id"],
                                      "route_id": latest["route"]["route_id"] if latest["status"] == "resolved" else None,
                                      "target_status": target})
            report["issues"].append(entry)
    if report["errors"]:
        report["ok"] = False
    return report


def reconcile(repo: Any, feature: Optional[str] = None, *, main_session: bool = False) -> Dict[str, Any]:
    """Repair packet/ticket/event split writes without inventing person/host data."""
    _require_main(main_session)
    root = _repo_root(repo)
    if feature is None:
        feature_root = root / "dev" / "features"
        issue_features = {path.parent.relative_to(feature_root).as_posix()
                          for path in _sibling("artifact_scope").scoped_glob(root, "issues") if path.is_dir()}
        decision_features = {path.parent.parent.relative_to(feature_root).as_posix()
                             for path in _sibling("artifact_scope").scoped_glob(root, "decisions/*.json")}
        features = sorted(issue_features | decision_features)
    else:
        features = [feature]
        _feature_dir(root, feature)
    report: Dict[str, Any] = {"ok": True, "repairs": [], "conflicts": [],
                              "errors": [], "unknown_routes": [], "needs_human_without_active": []}
    for feature_name in features:
        with _store().FileLock(_lock_path(root, feature_name)):
            try:
                packets = _feature_packets(root, feature_name)
            except DecisionError as exc:
                report["errors"].append({"feature": feature_name, "error": str(exc)})
                continue
            by_id = {packet["id"]: packet for packet in packets}
            # Finish explicit new-before-old supersession links first.
            for packet in sorted(packets, key=lambda item: item["timestamps"]["created_at"]):
                old_id = packet.get("supersedes")
                if packet["status"] not in ACTIVE_STATUSES or not old_id or old_id not in by_id:
                    continue
                old = by_id[old_id]
                if old["status"] == "routing":
                    report["conflicts"].append({
                        "issue": old["scope"]["issue"], "decision_id": old["id"],
                        "error": "linked replacement cannot supersede an unknown in-flight route",
                    })
                    continue
                if old["status"] in ACTIVE_STATUSES and old["scope"] == packet["scope"]:
                    by_id[old_id] = _supersede_from_link(root, old, packet, report["repairs"])
            packets = sorted(by_id.values(), key=lambda item: (item["scope"]["issue"], item["id"]))
            by_issue: Dict[str, List[Dict[str, Any]]] = {}
            for packet in packets:
                by_issue.setdefault(packet["scope"]["issue"], []).append(packet)
            for issue, group in by_issue.items():
                active = [packet for packet in group if packet["status"] in ACTIVE_STATUSES]
                if len(active) > 1:
                    report["conflicts"].append({"issue": issue, "decision_ids": [p["id"] for p in active],
                                                "error": "unrelated active decisions; no winner invented"})
                    continue
                try:
                    if active:
                        packet = active[0]
                        _ensure_active(root, packet)
                        if packet["status"] == "routing":
                            report["unknown_routes"].append({
                                "decision_id": packet["id"], "route_id": packet["route"]["route_id"],
                                "error": "host outcome is not persisted; reconcile with host before any retry",
                            })
                        continue
                    _, metadata = _issue_metadata(root, group[0])
                    if metadata.get("status") != "needs-human":
                        for packet in group:
                            _ensure_events(root, packet)
                        continue
                    try:
                        latest = _latest_terminal(group)
                    except ConflictError as exc:
                        report["conflicts"].append({"issue": issue, "error": str(exc)})
                        continue
                    if latest is None:
                        report["needs_human_without_active"].append(issue)
                        report["conflicts"].append({
                            "issue": issue,
                            "error": "needs-human has no active packet; prior status cannot be invented",
                        })
                    elif latest["status"] == "resolved":
                        _ensure_resolved(root, latest)
                        report["repairs"].append("applied latest acknowledged next_status for " + latest["id"])
                    else:
                        _set_issue_status(root, latest, "cancelled")
                        _ensure_events(root, latest)
                        report["repairs"].append("applied latest explicit cancellation for " + latest["id"])
                except DecisionError as exc:
                    report["errors"].append({"issue": issue, "error": str(exc)})
            issues_dir = _feature_dir(root, feature_name) / "issues"
            for issue_path in sorted(issues_dir.glob("*.md")):
                try:
                    metadata = _config().read_frontmatter(issue_path)
                    issue_id = metadata.get("issue-id")
                    if metadata.get("status") == "needs-human" and issue_id not in by_issue:
                        report["needs_human_without_active"].append(issue_id)
                        report["conflicts"].append({
                            "issue": issue_id,
                            "error": "needs-human has no decision packet; no prior status or answer is invented",
                        })
                except Exception as exc:
                    report["errors"].append({"issue": issue_path.name, "error": str(exc)})
    report["ok"] = not (report["conflicts"] or report["errors"])
    return report


def _read_json_argument(value: str) -> Any:
    try:
        if value == "-":
            return json.load(sys.stdin, object_pairs_hook=_reject_duplicate_keys)
        return json.loads(Path(value).read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValidationError("cannot read JSON argument %s: %s" % (value, exc)) from exc


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _common_mutating(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True)
    parser.add_argument("--main-session", action="store_true", required=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate-request")
    validate_parser.add_argument("--repo")
    validate_parser.add_argument("--request", required=True)
    validate_parser.add_argument("--now")

    request_parser = sub.add_parser("request")
    _common_mutating(request_parser)
    request_parser.add_argument("--request", required=True)
    request_parser.add_argument("--unattended", action="store_true")
    request_parser.add_argument("--now")

    for name in ("show", "present", "answer", "route-begin", "route-ack", "route-fail",
                 "expire", "cancel", "supersede", "cancel-presentation", "timeout-presentation"):
        command = sub.add_parser(name)
        command.add_argument("--repo", required=True)
        command.add_argument("--feature", required=True)
        command.add_argument("--id", required=True)
        if name != "show":
            command.add_argument("--main-session", action="store_true")
        if name in ("present", "answer", "route-begin", "route-ack", "route-fail", "expire",
                    "cancel", "supersede", "cancel-presentation", "timeout-presentation"):
            command.add_argument("--now")
        if name == "present":
            command.add_argument("--channel", choices=("component", "text"), default="component")
            command.add_argument("--unattended", action="store_true")
        elif name == "answer":
            command.add_argument("--option-id", required=True)
            command.add_argument("--text", required=True)
            command.add_argument("--current-basis", required=True)
        elif name == "route-begin":
            command.add_argument("--current-basis", required=True)
            command.add_argument("--parent-session-id")
        elif name == "route-ack":
            command.add_argument("--route-id", required=True)
            command.add_argument("--ack")
        elif name == "route-fail":
            command.add_argument("--route-id", required=True)
            command.add_argument("--reason", required=True)
            command.add_argument("--outcome-known", action="store_true")
        elif name == "cancel":
            command.add_argument("--reason", required=True)
        elif name == "supersede":
            command.add_argument("--request", required=True)
            command.add_argument("--unattended", action="store_true")

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--repo", required=True)
    list_parser.add_argument("--feature")
    list_parser.add_argument("--issue")
    list_parser.add_argument("--status")
    list_parser.add_argument("--active", action="store_true")

    active_parser = sub.add_parser("active")
    active_parser.add_argument("--repo", required=True)
    active_parser.add_argument("--feature", required=True)
    active_parser.add_argument("--issue")

    classify_parser = sub.add_parser("classify")
    classify_parser.add_argument("--repo", required=True)
    classify_parser.add_argument("--feature")

    reconcile_parser = sub.add_parser("reconcile")
    _common_mutating(reconcile_parser)
    reconcile_parser.add_argument("--feature")

    args = parser.parse_args(argv)
    try:
        command = args.command
        if command == "validate-request":
            result = validate_request(_read_json_argument(args.request), repo=args.repo, now=args.now)
        elif command == "request":
            result = request(args.repo, _read_json_argument(args.request),
                             main_session=args.main_session, unattended=args.unattended, now=args.now)
        elif command == "show":
            result = show(args.repo, args.feature, args.id)
        elif command == "present":
            packet = present(args.repo, args.feature, args.id, main_session=args.main_session,
                             channel=args.channel, unattended=args.unattended, now=args.now)
            if args.unattended:
                result = packet
            elif args.channel == "text":
                print(to_text_fallback(packet))
                return 0
            else:
                result = to_question_component(packet)
        elif command == "answer":
            result = answer(args.repo, args.feature, args.id, option_id=args.option_id, text=args.text,
                            current_basis=_read_json_argument(args.current_basis),
                            main_session=args.main_session, now=args.now)
        elif command == "route-begin":
            result = route_begin(args.repo, args.feature, args.id,
                                 current_basis=_read_json_argument(args.current_basis),
                                 parent_session_id=args.parent_session_id,
                                 main_session=args.main_session, now=args.now)
        elif command == "route-ack":
            result = route_ack(args.repo, args.feature, args.id, route_id=args.route_id,
                               ack=_read_json_argument(args.ack) if args.ack else None,
                               main_session=args.main_session, now=args.now)
        elif command == "route-fail":
            result = route_fail(args.repo, args.feature, args.id, route_id=args.route_id,
                                reason=args.reason, outcome_known=args.outcome_known,
                                main_session=args.main_session, now=args.now)
        elif command == "expire":
            result = expire(args.repo, args.feature, args.id,
                            main_session=args.main_session, now=args.now)
        elif command == "cancel":
            result = cancel(args.repo, args.feature, args.id, reason=args.reason,
                            main_session=args.main_session, now=args.now)
        elif command == "supersede":
            result = supersede(args.repo, args.id, _read_json_argument(args.request),
                               main_session=args.main_session, unattended=args.unattended, now=args.now)
        elif command == "cancel-presentation":
            result = cancel_presentation(args.repo, args.feature, args.id,
                                         main_session=args.main_session, now=args.now)
        elif command == "timeout-presentation":
            result = timeout_presentation(args.repo, args.feature, args.id,
                                          main_session=args.main_session, now=args.now)
        elif command == "list":
            result = list(args.repo, feature=args.feature, issue=args.issue,
                          status=args.status, active=True if args.active else None)
        elif command == "active":
            result = (active_for_issue(args.repo, args.feature, args.issue) if args.issue
                      else active_for_feature(args.repo, args.feature))
        elif command == "classify":
            result = classify(args.repo, feature=args.feature)
        elif command == "reconcile":
            result = reconcile(args.repo, feature=args.feature, main_session=args.main_session)
        else:  # pragma: no cover
            parser.error("unknown command")
            return 2
        _print(result)
        return 0 if not isinstance(result, dict) or result.get("ok", True) else 1
    except DecisionError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
