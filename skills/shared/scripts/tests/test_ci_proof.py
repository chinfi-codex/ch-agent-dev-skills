#!/usr/bin/env python3
"""Online CI proof contract tests using temporary Git and a fake HTTP API."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import ci_proof as cp
import delivery
import feature_closeout as fc
import state_store


def sh(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class FakeProvider:
    def __init__(self, commit, tree):
        self.commit = commit
        self.tree = tree
        self.ref = "refs/heads/feat/demo"
        self.protected = True
        self.capabilities = True
        self.workflow_capabilities = True
        self.verdict_overrides = {}
        self.run_overrides = {}
        self.artifact_payload = None
        self.unavailable_runs = set()
        self.requests = []

    def verdict(self, provider="gitea", run_id="42"):
        value = {
            "schema": "pandu.test-verdict/v1", "status": "completed", "pass": True,
            "commit": self.commit, "tree": self.tree, "ref": self.ref, "run_id": run_id,
            "ran_n": 2, "skipped_env_n": 0, "new_failures": [],
            "unattributed_failures": [], "attribution_status": "not-needed",
            "quarantine_overdue_n": 0, "quarantined_n": 0,
            "executed": [{"id": "unit", "status": "pass"}, {"id": "integration", "status": "pass"}],
            "environment": {"name": "ci-runner", "image": "ci-image-digest"},
            "failure_attribution": None, "quarantine": None,
        }
        value.update(self.verdict_overrides)
        return value

    def run(self, provider="gitea", run_id="42"):
        if provider == "gitea":
            value = {
                "id": int(run_id), "run_attempt": 2, "event": "push",
                "head_branch": "feat/demo", "head_sha": self.commit,
                "status": "completed", "conclusion": "success",
                "workflow_id": "full", "html_url": "https://ci.example/runs/" + run_id,
            }
        else:
            value = {
                "id": int(run_id), "source": "push", "ref": "feat/demo",
                "sha": self.commit, "status": "success",
                "web_url": "https://ci.example/pipelines/" + run_id,
            }
        value.update(self.run_overrides)
        return value

    def gitlab_zip(self):
        payload = self.artifact_payload if self.artifact_payload is not None else json.dumps(self.verdict("gitlab")).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in (("artifacts/test-results/verdict.json", payload),
                                  ("artifacts/test-results/junit.xml", b"<testsuite/>")):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(info, content)
        return buffer.getvalue()

    def response(self, method, path):
        self.requests.append((method, path))
        if path == "/api/v1/repos/o/r":
            return 200, {"permissions": {"push": True}, "capabilities": {
                "expected_sha_merge": self.capabilities, "post_merge_read": True}}
        if path == "/api/v1/user":
            return 200, {"id": 1}
        if path == "/api/v1/repos/o/r/actions/workflows":
            return 200, {"workflows": [{"id": "full", "name": "full"}]}
        if path.startswith("/api/v1/repos/o/r/pulls"):
            return 200, []
        if path == "/api/v1/repos/o/r/branches/main":
            return 200, {"protected": self.protected, "commit": {"id": self.commit}}
        if path == "/api/v1/repos/o/r/branches/main/protection":
            return (200, {"enable_status_check": True, "required_status_check_contexts": ["full"]}) if self.protected else (404, {})
        run_match = re.fullmatch(r"/api/v1/repos/o/r/actions/runs/(\d+)", path)
        if run_match:
            run_id = run_match.group(1)
            return (404, {"message": "run unavailable"}) if run_id in self.unavailable_runs else (200, self.run(run_id=run_id))
        artifacts_match = re.fullmatch(r"/api/v1/repos/o/r/actions/runs/(\d+)/artifacts", path)
        if artifacts_match:
            run_id = artifacts_match.group(1)
            if run_id in self.unavailable_runs:
                return 404, {"message": "artifacts unavailable"}
            return 200, {"artifacts": [{"id": int(run_id), "name": "verdict.json"}]}
        artifact_match = re.fullmatch(r"/api/v1/repos/o/r/actions/artifacts/(\d+)/zip", path)
        if artifact_match:
            run_id = artifact_match.group(1)
            if run_id in self.unavailable_runs:
                return 404, {"message": "artifact unavailable"}
            return 200, self.artifact_payload if self.artifact_payload is not None else json.dumps(self.verdict(run_id=run_id)).encode()
        if path == "/api/v4/projects/g%2Fp":
            return 200, {"id": 7, "path_with_namespace": "g/p",
                         "permissions": {"project_access": {"access_level": 40}, "group_access": None},
                         "only_allow_merge_if_pipeline_succeeds": True}
        if path.startswith("/api/v4/projects/g%2Fp/repository/files/.gitlab-ci.yml"):
            return 200, {"content": "dGVzdA==", "ref": "main"}
        if path == "/api/v4/user":
            return 200, {"id": 1}
        if path.startswith("/api/v4/projects/g%2Fp/merge_requests"):
            return 200, []
        if path == "/api/v4/projects/g%2Fp/protected_branches/main":
            return (200, {"name": "main", "required_status_checks": ["full"]}) if self.protected else (404, {})
        if path == "/api/v4/projects/g%2Fp/pipelines/42":
            return 200, self.run("gitlab")
        if path.startswith("/api/v4/projects/g%2Fp/pipelines/42/jobs"):
            return 200, [{"id": 9, "status": "success", "name": "full",
                          "artifacts_file": {"filename": "artifacts.zip", "size": 1234},
                          "artifacts": [{"file_type": "archive", "filename": "artifacts.zip", "size": 1234}]}]
        if path == "/api/v4/projects/g%2Fp/jobs/9/artifacts":
            return 200, self.gitlab_zip()
        return 404, {"message": "not found", "path": path}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, value = self.server.state.response("GET", self.path)
        payload = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class CIProofFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        sh(self.repo, "init", "-q", "-b", "main")
        sh(self.repo, "config", "user.email", "t@t")
        sh(self.repo, "config", "user.name", "t")
        (self.repo / ".gitignore").write_text("dev/\n.worktrees/\ndocs/\n")
        (self.repo / "a.py").write_text("A = 0\n")
        (self.repo / "test_a.py").write_text("assert True\n")
        sh(self.repo, "add", ".")
        sh(self.repo, "commit", "-qm", "base")
        self.base_commit = sh(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.py").write_text("A = 1\n")
        sh(self.repo, "add", "a.py")
        sh(self.repo, "commit", "-qm", "candidate")
        self.commit = sh(self.repo, "rev-parse", "HEAD")
        self.tree = sh(self.repo, "rev-parse", "HEAD^{tree}")
        self.state = FakeProvider(self.commit, self.tree)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def fetch(self, **kwargs):
        return cp.fetch_proof("gitea", "o/r", "full", "42", base_url=self.base,
                              token="secret", repo=self.repo, expected_ref="refs/heads/feat/demo",
                              expected_commit=self.commit, expected_tree=self.tree,
                              expected_attempt=2, expected_event="push", **kwargs)

    def test_gitea_green_fetch_recheck_and_artifact_hash(self):
        proof = self.fetch()
        self.assertEqual(proof["run_id"], "42")
        self.assertEqual(proof["attempt"], 2)
        self.assertEqual(proof["tree"], self.tree)
        payload = json.dumps(self.state.verdict()).encode()
        self.assertEqual(proof["artifact"]["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(proof["artifact"]["bytes"], len(payload))
        fresh = cp.recheck_proof(proof, token="secret", repo=self.repo)
        self.assertEqual(fresh["artifact"]["sha256"], proof["artifact"]["sha256"])
        self.assertEqual(len(self.state.requests), 6)
        self.assertEqual(sum(path.endswith("/zip") for _, path in self.state.requests), 2)

    def test_feature_operation_id_ignores_artifact_fetch_time(self):
        proof = self.fetch()
        changed = copy.deepcopy(proof)
        changed["artifact"]["fetched_at"] = "2999-01-01T00:00:00Z"
        candidate = {"schema": "agent-dev.feature-candidate/v2", "candidate": self.commit}
        self.assertEqual(delivery._feature_operation_id(candidate, proof),
                         delivery._feature_operation_id(candidate, changed))

    def test_gitlab_online_adapter_green(self):
        artifact = "artifacts/test-results/verdict.json"
        proof = cp.fetch_proof("gitlab", "g/p", ".gitlab-ci.yml", "42", base_url=self.base,
                               token="secret", repo=self.repo, expected_ref="feat/demo",
                               expected_commit=self.commit, expected_tree=self.tree,
                               expected_event="push", artifact_name=artifact)
        self.assertEqual(proof["provider"], "gitlab")
        self.assertIsNone(proof["attempt"])
        self.assertEqual(proof["attempt_status"], "not-supported")
        self.assertEqual(proof["artifact"]["name"], artifact)
        self.assertEqual(proof["artifact"]["sha256"], hashlib.sha256(self.state.gitlab_zip()).hexdigest())
        self.assertEqual(proof["status"], "completed")
        self.assertEqual(proof["conclusion"], "success")
        cap = cp.capabilities("gitlab", "g/p", base_url=self.base, token="secret",
                              workflow=".gitlab-ci.yml", branch="main", mode="server-enforced")
        self.assertTrue(cap["ok"], cap)
        self.assertFalse(cap["checks"]["run_attempt"])
        self.assertFalse(cap["checks"]["target_cas_merge"])

    def test_gitlab_attempt_and_named_workflow_are_explicitly_unsupported(self):
        with self.assertRaisesRegex(cp.CIProofError, "attempt is not supported"):
            cp.fetch_proof("gitlab", "g/p", ".gitlab-ci.yml", "42", base_url=self.base,
                           token="secret", repo=self.repo, expected_attempt=1,
                           expected_tree=self.tree, artifact_name="artifacts/test-results/verdict.json")
        with self.assertRaisesRegex(cp.CIProofError, "workflow is not proven"):
            cp.fetch_proof("gitlab", "g/p", "full", "42", base_url=self.base,
                           token="secret", repo=self.repo, expected_tree=self.tree,
                           artifact_name="artifacts/test-results/verdict.json")

    def test_gitlab_ci_yaml_is_a_real_workflow_capability(self):
        cap = cp.capabilities("gitlab", "g/p", base_url=self.base, token="secret",
                              workflow=".gitlab-ci.yml", branch="main", mode="cooperative-ci")
        self.assertTrue(cap["checks"]["workflow"], cap)
        proof = cp.fetch_proof("gitlab", "g/p", ".gitlab-ci.yml", "42", base_url=self.base,
                               token="secret", repo=self.repo, expected_ref="feat/demo",
                               expected_commit=self.commit, expected_tree=self.tree,
                               expected_event="push", artifact_name="artifacts/test-results/verdict.json")
        self.assertEqual(proof["workflow"], ".gitlab-ci.yml")

    def test_gitlab_custom_capabilities_cannot_fake_target_cas(self):
        cap = cp.capabilities("gitlab", "g/p", base_url=self.base, token="secret",
                              workflow=".gitlab-ci.yml", branch="main", mode="server-enforced")
        self.assertTrue(cap["ok"], cap)
        self.assertFalse(cap["checks"]["target_cas_merge"])
        with self.assertRaisesRegex(cp.CapabilityError, "target-old-OID CAS"):
            cp.require_capabilities(cap, "server-enforced", remote_merge=True)

    def test_fake_run_id_attempt_event_ref_and_commit_fail(self):
        mutations = [
            ({"id": 41}, {}, "different run ID"),
            ({"run_attempt": 1}, {}, "attempt"),
            ({"event": "pull_request"}, {}, "event"),
            ({"head_branch": "main"}, {}, "ref"),
            ({"head_sha": "0" * 40}, {}, "commit"),
        ]
        for run, verdict, text in mutations:
            with self.subTest(text=text):
                self.state.run_overrides = run
                self.state.verdict_overrides = verdict
                with self.assertRaisesRegex(cp.CIProofError, text):
                    self.fetch()
        self.state.run_overrides = {}
        self.state.verdict_overrides = {}

    def test_fake_tree_artifact_and_failed_executed_inventory_fail(self):
        self.state.verdict_overrides = {"tree": "0" * 40}
        with self.assertRaisesRegex(cp.CIProofError, "tree mismatch"):
            self.fetch()
        self.state.verdict_overrides = {"executed": [{"id": "unit", "status": "fail"}]}
        # Schema intentionally catches non-pass executed entries too.
        with self.assertRaisesRegex(cp.CIProofError, "schema|executed"):
            self.fetch()
        self.state.verdict_overrides = {"environment": {}}
        with self.assertRaisesRegex(cp.CIProofError, "environment"):
            self.fetch()
        self.state.verdict_overrides = {"new_failures": [{"id": "x"}]}
        with self.assertRaises(cp.CIProofError):
            self.fetch()
        self.state.verdict_overrides = {}
        self.state.artifact_payload = b"not-json"
        with self.assertRaisesRegex(cp.CIProofError, "valid UTF-8 JSON"):
            self.fetch()

    def test_quarantine_requires_config_and_structured_evidence(self):
        self.state.verdict_overrides = {"quarantined_n": 1, "quarantine_config_sha256": "a" * 64}
        with self.assertRaisesRegex(cp.CIProofError, "quarantine evidence"):
            self.fetch()
        self.state.verdict_overrides["quarantine"] = {"config_sha256": "a" * 64}
        self.assertEqual(self.fetch()["verdict"]["quarantined_n"], 1)

    def test_server_enforced_unknown_protection_or_cas_fails_closed(self):
        self.state.protected = False
        cap = cp.capabilities("gitea", "o/r", base_url=self.base, token="secret",
                              workflow="full", branch="main", mode="server-enforced")
        self.assertFalse(cap["ok"])
        with self.assertRaises(cp.CapabilityError):
            cp.require_capabilities(cap, "server-enforced")
        self.state.protected = True
        self.state.capabilities = False
        cap = cp.capabilities("gitea", "o/r", base_url=self.base, token="secret",
                              workflow="full", branch="main", mode="server-enforced")
        self.assertEqual(cap["checks"]["target_cas_merge"], False)
        with self.assertRaisesRegex(cp.CapabilityError, "target-old-OID CAS"):
            cp.require_capabilities(cap, "server-enforced", remote_merge=True)

    def test_cooperative_is_explicit_not_server_enforced_and_no_downgrade(self):
        self.state.protected = False
        self.state.capabilities = False
        cap = cp.capabilities("gitea", "o/r", base_url=self.base, token="secret",
                              workflow="full", branch="main", mode="cooperative-ci")
        self.assertTrue(cap["ok"], cap)
        self.assertEqual(cap["server_enforcement"], "not-server-enforced")
        with self.assertRaisesRegex(cp.CIProofError, "downgrade|mode"):
            cp.require_capabilities(cap, "server-enforced")
        with self.assertRaises(cp.CIProofError):
            cp.capabilities("gitea", "o/r", base_url=self.base, token="secret",
                            workflow="full", branch="main", mode="local-human")

    def test_offline_is_never_a_local_proof_fallback(self):
        offline = "http://127.0.0.1:1"
        with self.assertRaisesRegex(cp.CIProofError, "request failed"):
            cp.fetch_proof("gitea", "o/r", "full", "42", base_url=offline,
                           token="secret", repo=self.repo, expected_tree=self.tree)

    def test_cli_has_no_local_verdict_or_run_proof_acceptance(self):
        result = subprocess.run([sys.executable, "-I", str(SCRIPTS / "ci_proof.py"), "fetch",
                                 "--provider", "gitea", "--repository", "o/r", "--workflow", "full",
                                 "--run-id", "42", "--ci-verdict", "local.json"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


class CIProofTest(CIProofFixture, unittest.TestCase):
    pass


class CIPromoteTest(unittest.TestCase):
    def setUp(self):
        CIProofFixture.setUp(self)
        sh(self.repo, "branch", "feat/demo")
        self.integration = self.repo / ".worktrees" / "integration"
        sh(self.repo, "worktree", "add", "-q", str(self.integration), "feat/demo")
        sh(self.repo, "reset", "--hard", self.base_commit)
        self.fdir = self.repo / "dev" / "features" / "mod" / "demo"
        self.integ = self.fdir / "integration"
        self.integ.mkdir(parents=True)
        os.environ["CI_TEST_TOKEN"] = "secret"

    def tearDown(self):
        CIProofFixture.tearDown(self)

    def configure(self, mode):
        enforcement = ("{review-trust: operator-attested, require-protected-branch: true, "
                       "require-expected-sha: true}" if mode == "server-enforced"
                       else "{review-trust: operator-attested}")
        config = """```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'python3 -I t.py'}
host-budget: {total: 2, max-runs: 1, queue-timeout: 30}
verify-policy:
  ticket-max-files: 10
  ticket-max-loc: 400
  deny: []
  test-change: [tests/**]
  generated-refresh: []
  base-freeze: true
  attribution: two-sided
  gate-scope: {categories-file: null, non-gating: [], env-skip: true}
feature-closeout:
  mode: %s
  ci: {provider: gitea, base-url: '%s', repository: o/r, workflow: full, artifact: verdict.json, token-env: CI_TEST_TOKEN}
dispatch:
  schema-version: 1
  executor: subagent
  parallel-max: 1
  hosts:
    zcode:
      supervisor-review: {model: provider/supervisor, effort: high}
      development:
        default: {model: provider/dev, effort: medium}
        escalated: {model: provider/dev, effort: high}
enforcement: %s
```
""" % (mode, "https://gitea.example", enforcement)
        (self.repo / "dev").mkdir(exist_ok=True)
        (self.repo / "dev" / "agents-config.md").write_text(config)

    def candidate(self, mode="cooperative-ci"):
        self.configure(mode)
        prd = self.repo / "docs" / "features" / "mod" / "demo" / "demo-prd-2026-11-14.md"
        prd.parent.mkdir(parents=True, exist_ok=True)
        prd.write_text("# PRD\n\n### 端到端验收场景\n- AC-1：Given 测试输入，When 执行命令，Then 退出码为 0\n")
        plan = {
            "schema": "agent-dev.feature-acceptance/v2",
            "documents": {"prd": {"path": "docs/features/mod/demo/demo-prd-2026-11-14.md",
                                    "sha256": hashlib.sha256(prd.read_bytes()).hexdigest()}},
            "cases": {
                "AC-1": {"title": "Command", "source": {"document": "prd", "anchor": "AC-1"}, "required": True,
                          "executor": "command", "oracle": "auto", "steps": ["run"],
                          "preconditions": ["环境：隔离测试环境"],
                          "expected": "exit zero", "command": "python3 -I t.py",
                          "assertions": {"exit": 0}, "evidence": ["log"], "human_reason": None}}}
        plan_bytes = (json.dumps(plan, ensure_ascii=False, sort_keys=True) + "\n").encode()
        (self.fdir / "acceptance-plan.yaml").write_bytes(plan_bytes)
        plan_sha = hashlib.sha256(plan_bytes).hexdigest()
        (self.fdir / "issues").mkdir(exist_ok=True)
        (self.fdir / "impl" / "issue-001").mkdir(parents=True, exist_ok=True)
        spec = self.fdir / "demo-tech-spec-2026-11-14.md"
        spec.write_text("---\nstatus: 已确认（待拆解）\n---\n# Spec\n")
        (self.fdir / "issues" / "issue-001-x.md").write_text(
            "---\nissue-id: issue-001\nfeature-slug: demo\nstatus: done\ntech-spec: demo-tech-spec-2026-11-14.md\n---\n# x\n")
        (self.fdir / "impl" / "issue-001" / "verification-supervisor.json").write_text(
            json.dumps({"records": [{"id": "DoD-1"}]}))
        candidate = {
            "schema": "agent-dev.feature-candidate/v2", "feature": "mod/demo", "round": None,
            "mode": mode, "integration_branch": "feat/demo", "candidate_ref": "refs/heads/feat/demo",
            "main_branch": "main", "main_head": self.base_commit, "merge_base": self.base_commit,
            "candidate": self.commit, "candidate_tree": self.tree, "frozen_at": "2026-11-15T00:00:00Z",
            "issues": [{"id": "issue-001"}], "acceptance_plan": {"path": "acceptance-plan.yaml", "sha256": plan_sha, "cases": ["AC-1"]},
        }
        (self.integ / "candidate.json").write_text(json.dumps(candidate))
        (self.integ / "acceptance-plan.json").write_bytes(plan_bytes)
        (self.integ / "evidence.md").write_text(
            "```yaml\ncandidate: %s\ncandidate_tree: %s\nall-passed: true\nran: {direct: 1, indirect: 0, deferred: 0, quarantined: 0}\n```\n" % (self.commit, self.tree))
        spec_rel = spec.relative_to(self.repo).as_posix()
        spec_sha = hashlib.sha256(spec.read_bytes()).hexdigest()
        (self.integ / "conformance-feature.md").write_text(
            "```yaml\nissue: feature\nverdict: conform\ncandidate: %s\ncandidate_tree: %s\n"
            "checker: main-session\nchecker_session: main-feature\ndate: 2026-11-15\n"
            "coverage:\n  issues: [issue-001]\n  specs:\n    - {path: %s, sha256: %s}\n```\n\n"
            "## Feature 级覆盖核对\n\n"
            "| 编号 | 需求点 | 来源 | 票 | 判定 | 证据 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 命令行为 | demo-tech-spec-2026-11-14.md#3 | issue-001 | 已实现 | a.py:run + test_a.py + verification-supervisor.json#DoD-1 |\n"
            % (self.commit, self.tree, spec_rel, spec_sha))
        generation_dir = self.integ / "raw" / "manual-command"
        generation_dir.mkdir(parents=True)
        raw = b"command-raw\n"
        (generation_dir / "raw.log").write_bytes(raw)
        manifest = {"schema": "agent-dev.artifact-generation/v2", "subject": "mod/demo:AC-1",
                    "generation": "manual-command", "candidate_tree": self.tree, "status": "current",
                    "created_at": "2026-11-15T00:00:00Z",
                    "artifacts": [{"path": "raw.log", "kind": "raw",
                                   "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}]}
        (generation_dir / "artifact-generation-manifest.json").write_text(json.dumps(manifest))
        artifact = {"type": "raw", "path": "integration/raw/manual-command/raw.log",
                    "sha256": hashlib.sha256(raw).hexdigest(), "candidate": self.commit,
                    "candidate_tree": self.tree, "generation": "manual-command"}
        results = {
            "schema": "agent-dev.feature-business-results/v2", "candidate": self.commit,
            "candidate_tree": self.tree, "plan_sha256": plan_sha, "environment": {"name": "integration"},
            "items": [{"id": "AC-1", "oracle": "auto", "executor": "command", "status": "pass",
                        "execution": {"executor": "command", "command": "python3 -I t.py", "exit": 0,
                                      "started": True, "generator": "verify_issue",
                                      "candidate": self.commit, "candidate_tree": self.tree,
                                      "generation": "manual-command"},
                        "assertions": [{"type": "exit", "expected": 0, "actual": 0, "status": "pass"}],
                        "artifacts": [artifact]}]}
        (self.integ / "business-results.json").write_text(json.dumps(results))
        fc.report(SimpleNamespace(feature_dir=str(self.fdir), repo=str(self.integration), round=None))
        return candidate

    def promote(self, mode="cooperative-ci", run_id="42"):
        return delivery.promote(str(self.integ / "candidate.json"), self.integration, self.repo,
                                run_id=run_id, attempt=2, event="push", base_url=self.base, timeout=5.0)

    def test_cooperative_feature_promote_uses_online_proof_and_reports_boundary(self):
        candidate = self.candidate()
        result = self.promote()
        self.assertEqual(result["mode"], "cooperative-ci")
        self.assertEqual(result["server_enforcement"], "not-server-enforced")
        self.assertFalse(result["push_authorized"])
        merge_commit = result["merge_commit"]
        self.assertEqual(sh(self.repo, "rev-list", "--parents", "-n", "1", merge_commit).split()[1:],
                         [self.base_commit, self.commit])
        recorded = json.loads((self.integ / "candidate.json").read_text())
        self.assertEqual(recorded["merged_commit"], merge_commit)
        self.assertEqual(sum(path.endswith("/zip") for _, path in self.state.requests), 2)

    def test_server_enforced_feature_promote_requires_and_rechecks_capabilities(self):
        self.candidate("server-enforced")
        result = self.promote("server-enforced")
        self.assertEqual(result["mode"], "server-enforced")
        self.assertEqual(result["scope"], "merge-main")
        self.assertGreaterEqual(sum(path == "/api/v1/repos/o/r/branches/main/protection" for _, path in self.state.requests), 2)

    def test_green_ci_cannot_replace_business_acceptance(self):
        self.candidate()
        (self.integ / "business-results.json").write_text("{}")
        with self.assertRaisesRegex(delivery.DeliveryError, "business/conformance authorization"):
            self.promote()
        self.assertEqual(sh(self.repo, "rev-parse", "HEAD"), self.base_commit)
        self.assertNotIn("merged_commit", json.loads((self.integ / "candidate.json").read_text()))

    def test_premerge_resume_rechecks_ci_and_failed_recheck_cannot_move_main(self):
        candidate = self.candidate()
        with mock.patch.object(delivery, "_execute_feature_merge", side_effect=delivery.DeliveryError("before CI merge crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "before CI merge crash"):
                self.promote()
        with mock.patch.object(cp, "recheck_proof", side_effect=cp.CIProofError("CI recheck revoked")) as recheck:
            with self.assertRaisesRegex(cp.CIProofError, "CI recheck revoked"):
                self.promote()
            recheck.assert_called_once()
        self.assertEqual(sh(self.repo, "rev-parse", "HEAD"), self.base_commit)
        self.assertNotIn("merged_commit", json.loads((self.integ / "candidate.json").read_text()))

    def test_pending_new_proof_supersedes_old_operation_and_merges_once(self):
        self.candidate()
        with mock.patch.object(delivery, "_execute_feature_merge", side_effect=delivery.DeliveryError("pending crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "pending crash"):
                self.promote(run_id="42")
        old = state_store.OperationJournal.load(self.integ / "delivery-promote.json")
        self.assertEqual(old.current_step, "merge")
        self.state.unavailable_runs.add("42")
        original = delivery._execute_feature_merge
        calls = []
        def counted(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)
        with mock.patch.object(delivery, "_execute_feature_merge", side_effect=counted):
            result = self.promote(run_id="43")
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(result["operation_id"], old.operation_id)
        marker_path = self.integ / "superseded" / ("delivery-promote-%s.superseded.json" % old.operation_id)
        marker = json.loads(marker_path.read_text())
        self.assertEqual(marker["state"], "superseded")
        self.assertEqual(marker["superseded_by"], result["operation_id"])
        self.assertEqual(marker["reason"], "CI run/proof replaced before merge")
        discovered = delivery.discover_operations(self.repo)
        historical = [item for item in discovered["operations"] if item.get("historical")]
        self.assertEqual(len(historical), 1)
        self.assertEqual(historical[0]["state"], "superseded")
        current = [item for item in discovered["operations"] if item["kind"] == "feature" and not item.get("historical")]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["operation_id"], result["operation_id"])
        self.assertEqual(sh(self.repo, "rev-list", "--count", "main"), "3")
        events = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines() if '"feature-merged"' in line]
        self.assertEqual(len(events), 1)

    def test_completed_or_merged_operation_cannot_be_superseded(self):
        self.candidate()
        result = self.promote(run_id="42")
        current = json.loads((self.integ / "candidate.json").read_text())
        with self.assertRaisesRegex(delivery.DeliveryError, "already merged"):
            delivery._feature_journal(self.integ, "feature-" + "b" * 64, current, "no-ff")
        self.assertFalse(list((self.integ / "superseded").glob("*.json")) if (self.integ / "superseded").exists() else [])
        replay = self.promote(run_id="43")
        self.assertEqual(replay["operation_id"], result["operation_id"])

    def test_fake_run_cannot_promote_even_with_valid_business(self):
        self.candidate()
        self.state.run_overrides = {"head_branch": "main"}
        with self.assertRaises(cp.CIProofError):
            self.promote()
        self.assertEqual(sh(self.repo, "rev-parse", "HEAD"), self.base_commit)


if __name__ == "__main__":
    unittest.main()
