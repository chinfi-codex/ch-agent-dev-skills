#!/usr/bin/env python3
"""Contract tests for POSIX locks, atomic writes, event IDs and journals."""
from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import state_store as ss


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomic_text_and_json_write_replace_without_temp_residue(self):
        target = self.root / "state" / "value.json"
        ss.atomic_json_write(target, {"b": 2, "a": [1, True]})
        first = target.read_bytes()
        self.assertEqual(first, b'{\n  "a": [\n    1,\n    true\n  ],\n  "b": 2\n}\n')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        ss.atomic_write(target, "replacement\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement\n")
        self.assertEqual(list(target.parent.glob("*.tmp")), [])
        with self.assertRaises(ss.StateError):
            ss.atomic_json_write(target, {"bad": float("nan")})
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement\n")
        with self.assertRaises(TypeError):
            ss.atomic_write(target, {"not": "bytes"})

    def test_append_event_once_is_idempotent_and_rejects_conflict_or_corruption(self):
        path = self.root / "events.jsonl"
        lock = self.root / "locks/events.lock"
        event = {"event": "merged", "operation_id": "op-1", "commit": "a" * 40}
        self.assertTrue(ss.append_event_once(path, event, lock_path=lock))
        self.assertFalse(ss.append_event_once(path, dict(event), lock_path=lock))
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
        # Alternate positional API: (path, event_id, event).
        self.assertTrue(ss.append_event_once(path, "op-2", {"event": "salvaged"}, lock_path=lock))
        conflict = dict(event, commit="b" * 40)
        with self.assertRaisesRegex(ss.StateError, "reused"):
            ss.append_event_once(path, conflict, lock_path=lock)
        partial = self.root / "partial.jsonl"
        partial.write_text(json.dumps({"id": "partial"}), encoding="utf-8")
        with self.assertRaisesRegex(ss.StateError, "unterminated final event"):
            ss.append_event_once(partial, {"id": "next"}, lock_path=lock)
        with path.open("a", encoding="utf-8") as stream:
            stream.write("{broken\n")
        with self.assertRaisesRegex(ss.StateError, "corrupt"):
            ss.append_event_once(path, {"id": "op-3"}, lock_path=lock)

    def test_flock_mutual_exclusion_release_and_unsupported_hard_fail(self):
        path = self.root / "locks/mutex.lock"
        first = ss.FileLock(path)
        second = ss.FileLock(path)
        with first:
            self.assertTrue(first.held)
            self.assertFalse(second.acquire(blocking=False))
            self.assertFalse(second.held)
        self.assertTrue(second.acquire(blocking=False))
        second.release()
        second.release()
        with mock.patch.object(ss.fcntl, "flock", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            with self.assertRaisesRegex(ss.LockError, "refusing unlocked"):
                ss.FileLock(self.root / "unsupported.lock").acquire(blocking=False)

    def test_kernel_releases_flock_when_owner_process_dies(self):
        path = self.root / "locks/death.lock"
        script = self.root.parent / ("lock_owner_%d.py" % os.getpid())
        # The script is outside the fixture data directory and only receives paths.
        script.write_text("""import os, sys
sys.path.insert(0, sys.argv[1])
from state_store import FileLock
lock = FileLock(sys.argv[2])
assert lock.acquire(blocking=False)
os._exit(0)
""", encoding="utf-8")
        try:
            subprocess.run([sys.executable, "-I", str(script), str(Path(ss.__file__).parent), str(path)],
                           check=True, capture_output=True, text=True)
        finally:
            script.unlink()
        lock = ss.FileLock(path)
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_journal_concurrent_create_reuses_same_operation(self):
        path = self.root / "operations/concurrent.json"
        lock = self.root / "locks/concurrent.lock"
        script = self.root.parent / ("journal_create_%d.py" % os.getpid())
        script.write_text("""import sys
sys.path.insert(0, sys.argv[1])
from state_store import OperationJournal
journal = OperationJournal.create(sys.argv[2], 'shared-op', ['step'], lock_path=sys.argv[3])
assert journal.operation_id == 'shared-op'
""", encoding="utf-8")
        command = [sys.executable, "-I", str(script), str(Path(ss.__file__).parent), str(path), str(lock)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(2)]
        try:
            results = [process.communicate(timeout=10) for process in processes]
        except subprocess.TimeoutExpired:
            for process in processes:
                process.kill()
            self.fail("concurrent journal creation deadlocked")
        finally:
            script.unlink()
        for process, (stdout, stderr) in zip(processes, results):
            self.assertEqual(process.returncode, 0, stdout + stderr)
        loaded = ss.OperationJournal.load(path, lock_path=lock)
        self.assertEqual(loaded.operation_id, "shared-op")
        self.assertEqual(loaded.revision, 0)

    def test_journal_crash_resume_order_digest_and_idempotent_create(self):
        path = self.root / "operations/cleanup.json"
        lock = self.root / "locks/cleanup.lock"
        journal = ss.OperationJournal.create(path, "op-1", [
            {"id": "salvage", "expected": {"path": "dev/a"}},
            "remove",
        ], lock_path=lock)
        self.assertEqual(journal.current_step, "salvage")
        self.assertEqual(ss.OperationJournal.create(path, "op-1", ["salvage", "remove"], lock_path=lock).state, "running")
        with self.assertRaisesRegex(ss.StateError, "another operation"):
            ss.OperationJournal.create(path, "op-other", ["salvage", "remove"], lock_path=lock)

        self.assertTrue(journal.advance("salvage", expected={"path": "dev/a"}))
        # Simulated crash: intent is durable, observed is absent.
        recovered = ss.OperationJournal.load(path, lock_path=lock)
        self.assertEqual(recovered.steps[0]["status"], "running")
        self.assertNotIn("observed", recovered.steps[0])
        with self.assertRaisesRegex(ss.StateError, "unfinished"):
            recovered.complete()
        with self.assertRaisesRegex(ss.StateError, "order"):
            recovered.advance("remove", expected="gone", observed="gone")
        self.assertTrue(recovered.advance("salvage", expected={"path": "dev/a"}, observed={"path": "dev/a"}))
        self.assertFalse(recovered.advance("salvage", expected={"path": "dev/a"}, observed={"path": "dev/a"}))
        self.assertTrue(recovered.advance("remove", expected="gone", observed="gone"))
        self.assertTrue(recovered.complete())
        self.assertFalse(recovered.complete())
        loaded = ss.OperationJournal.load(path, lock_path=lock)
        self.assertEqual(loaded.state, "completed")
        self.assertEqual(loaded.revision, 4)
        self.assertEqual(loaded.data["steps"][1]["observed"], "gone")

    def test_journal_corruption_unknown_fields_and_stale_writer_fail(self):
        path = self.root / "journal.json"
        journal = ss.OperationJournal.create(path, "op", ["one"])
        stale = ss.OperationJournal.load(path)
        journal.advance("one", expected=1)
        with self.assertRaisesRegex(ss.StateError, "stale"):
            stale.advance("one", expected=1, observed=1)

        path.write_text('{"schema": "x", "schema": "y"}', encoding="utf-8")
        with self.assertRaisesRegex(ss.StateError, "duplicate JSON key"):
            ss.OperationJournal.load(path)
        path.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(ss.StateError, "cannot load"):
            ss.OperationJournal.load(path)
        data = journal.data
        data["steps"][0]["status"] = "done"  # observed intentionally missing
        ss.atomic_json_write(path, data)
        with self.assertRaisesRegex(ss.StateError, "lacks observed"):
            ss.OperationJournal.load(path)
        data = journal.data
        data["unknown"] = True
        ss.atomic_json_write(path, data)
        with self.assertRaisesRegex(ss.StateError, "unknown"):
            ss.OperationJournal.load(path)

    def test_journal_failure_is_terminal_and_idempotent(self):
        path = self.root / "failed.json"
        journal = ss.OperationJournal.create(path, "op-fail", ["one"])
        journal.advance("one", expected={"old": "a"})
        self.assertTrue(journal.fail("side effect failed", observed={"old": "unknown"}))
        self.assertFalse(journal.fail("side effect failed"))
        loaded = ss.OperationJournal.load(path)
        self.assertEqual(loaded.state, "failed")
        self.assertEqual(loaded.steps[0]["status"], "failed")
        with self.assertRaisesRegex(ss.StateError, "cannot be overwritten"):
            loaded.fail("different")
        with self.assertRaisesRegex(ss.StateError, "only a running"):
            loaded.advance("one", expected={"old": "a"}, observed={"old": "a"})
        with self.assertRaisesRegex(ss.StateError, "cannot complete"):
            loaded.complete()


if __name__ == "__main__":
    unittest.main()
