#!/usr/bin/env python3
"""Configured verify-instance lease and dev-service lifecycle tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import integration_runtime as runtime


class IntegrationRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.feature = self.repo / "dev/features/mod/slug"
        self.worktree = self.repo / ".worktrees/integration-slug"
        self.worktree.mkdir(parents=True)
        self.feature.mkdir(parents=True)
        (self.repo / "dev/agents-config.md").write_text("""```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'true'}
host-budget: {total: 2, max-runs: 1, queue-timeout: 30}
verify-policy:
  ticket-max-files: 10
  ticket-max-loc: 400
  deny: []
  test-change: []
  generated-refresh: []
  base-freeze: true
  attribution: two-sided
  gate-scope: {categories-file: null, non-gating: [], env-skip: true}
dispatch:
  schema-version: 1
  executor: subagent
  parallel-max: 1
  hosts:
    codex:
      supervisor-review: {model: supervisor, effort: high}
      development:
        default: {model: dev, effort: medium}
        escalated: {model: dev, effort: high}
enforcement: {review-trust: operator-attested}
feature-closeout:
  mode: local-human
  verify-instance:
    name: test
    deploy: "true"
    lease: .agent-dev/verify-lease.json
integration-runtime:
  runtime-dir: .agent-dev/runtime
  ready-timeout-seconds: 2
  stop-timeout-seconds: 2
  lease-timeout-seconds: 3600
  dev-service:
    start: "exec python3 -c 'import time; time.sleep(30)'"
    ready: "true"
    stop: "kill \\"$AGENT_DEV_SERVICE_PID\\""
    status: "kill -0 \\"$AGENT_DEV_SERVICE_PID\\""
```
""", encoding="utf-8")
        self.candidate = "a" * 40

    def tearDown(self):
        try:
            state = runtime.resource_state(self.repo, feature_dir=self.feature)
            for service in state["services"]:
                if service.get("state") != "stopped" and service.get("pid"):
                    try:
                        os.kill(int(service["pid"]), 15)
                    except OSError:
                        pass
        except Exception:
            pass
        self.tmp.cleanup()

    def binding(self, **kwargs):
        return dict(repo=self.repo, feature_dir=self.feature, round=None,
                    candidate=self.candidate, worktree=self.worktree,
                    log_generation="generation-1", **kwargs)

    def test_lease_acquire_heartbeat_release_idempotent_and_bound(self):
        lease = runtime.acquire_lease(**self.binding())
        import json_schema
        self.assertEqual(json_schema.load_validate(SCRIPT_DIR.parent / "references/integration-runtime.schema.json", lease), [])
        self.assertEqual(lease["candidate"], self.candidate)
        self.assertEqual(lease["worktree"], str(self.worktree.resolve()))
        heartbeat = runtime.heartbeat_lease(self.repo, lease["lease_id"], self.candidate)
        self.assertEqual(heartbeat["revision"], 2)
        with self.assertRaises(runtime.RuntimeFailure):
            runtime.heartbeat_lease(self.repo, lease["lease_id"], "b" * 40)
        released = runtime.release_lease(self.repo, lease["lease_id"], self.candidate)
        self.assertFalse(released["idempotent"])
        again = runtime.release_lease(self.repo, lease["lease_id"], self.candidate)
        self.assertTrue(again["idempotent"])
        with self.assertRaises(runtime.RuntimeFailure):
            runtime.release_lease(self.repo, "wrong", self.candidate)

    def test_only_one_live_lease(self):
        lease = runtime.acquire_lease(**self.binding())
        with self.assertRaises(runtime.RuntimeFailure):
            runtime.acquire_lease(**self.binding())
        runtime.release_lease(self.repo, lease["lease_id"], self.candidate)
        replacement = runtime.acquire_lease(**self.binding())
        self.assertNotEqual(lease["lease_id"], replacement["lease_id"])

    def test_no_dev_service_config_fails_unknown_not_npm_guess(self):
        config = {"integration-runtime": {"runtime-dir": ".agent-dev/runtime"}}
        with self.assertRaisesRegex(runtime.RuntimeFailure, "unknown"):
            runtime.start_service(**self.binding(), config=config)

    def test_service_start_ready_status_stop_idempotent(self):
        service = runtime.start_service(**self.binding())
        self.assertEqual(service["state"], "ready")
        status = runtime.status_service(self.repo, service["service_id"], self.candidate)
        self.assertTrue(status["running"])
        with self.assertRaises(runtime.RuntimeFailure):
            runtime.stop_service(self.repo, service["service_id"], "b" * 40)
        stopped = runtime.stop_service(self.repo, service["service_id"], self.candidate)
        self.assertFalse(stopped["running"])
        again = runtime.stop_service(self.repo, service["service_id"], self.candidate)
        self.assertTrue(again["idempotent"])
        self.assertEqual(runtime.status_service(self.repo, service["service_id"], self.candidate)["running"], False)

    def test_quiesce_stops_service_before_releasing_lease(self):
        service = runtime.start_service(**self.binding())
        lease = runtime.acquire_lease(**self.binding())
        result = runtime.quiesce_feature(self.repo, self.feature, self.candidate)
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["stopped"], [service["service_id"]])
        self.assertEqual(result["released"], [lease["lease_id"]])
        state = runtime.resource_state(self.repo, feature_dir=self.feature)
        self.assertEqual(state["blockers"], [])

    def test_cross_machine_cas_or_stop_is_unprovable_and_preserved(self):
        lease = runtime.acquire_lease(**self.binding())
        path = self.repo / ".agent-dev/verify-lease.json"
        value = json.loads(path.read_text())
        value["machine_id"] = "another-machine"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(runtime.RuntimeFailure, "跨机器"):
            runtime.release_lease(self.repo, lease["lease_id"], self.candidate)
        self.assertIsNone(json.loads(path.read_text())["released_at"])

    def test_stop_command_runs_once_across_crash_and_runtime_reconcile(self):
        service = runtime.start_service(**self.binding())
        stop_command = runtime._service_commands(self.repo)["stop"]
        calls = []
        original = runtime._run
        def crash(command, record, *args, **kwargs):
            result = original(command, record, *args, **kwargs)
            if command == stop_command:
                calls.append(1)
                raise runtime.RuntimeFailure("crash after stop command")
            return result
        with mock.patch.object(runtime, "_run", side_effect=crash):
            with self.assertRaises(runtime.RuntimeFailure):
                runtime.stop_service(self.repo, service["service_id"], self.candidate)
        path = runtime._service_path(self.repo, service["service_id"])
        self.assertEqual(json.loads(path.read_text())["state"], "stopping")
        result = runtime.reconcile(self.repo, service["service_id"], self.candidate, apply=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(path.read_text())["state"], "stopped")

    def test_concurrent_stop_is_serialized_and_executes_once(self):
        service = runtime.start_service(**self.binding())
        stop_command = runtime._service_commands(self.repo)["stop"]
        calls = []
        original = runtime._run
        lock = threading.Lock()
        def counted(command, record, *args, **kwargs):
            if command == stop_command:
                with lock:
                    calls.append(1)
            return original(command, record, *args, **kwargs)
        results = []
        with mock.patch.object(runtime, "_run", side_effect=counted):
            workers = [threading.Thread(target=lambda: results.append(
                runtime.stop_service(self.repo, service["service_id"], self.candidate))) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join(10)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(item["state"] == "stopped" for item in results))

    def test_records_bind_candidate_worktree_and_log_generation(self):
        service = runtime.start_service(**self.binding())
        path = runtime._service_path(self.repo, service["service_id"])
        record = json.loads(path.read_text())
        import json_schema
        self.assertEqual(json_schema.load_validate(SCRIPT_DIR.parent / "references/integration-runtime.schema.json", record), [])
        self.assertEqual(record["candidate"], self.candidate)
        self.assertEqual(record["log_generation"], "generation-1")
        self.assertEqual(record["worktree"], str(self.worktree.resolve()))
        runtime.stop_service(self.repo, service["service_id"], self.candidate)


if __name__ == "__main__":
    unittest.main()
