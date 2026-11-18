#!/usr/bin/env python3
"""Identity, dispatch, coverage and freshness tests for review_receipt.py."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import review_receipt as rr
import ticket_context as tc
import verify_issue as vi
from test_verify_issue import CONFIG, ISSUE, sh


SCRIPT = SCRIPTS / "review_receipt.py"

class ReviewReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "test@example.com")
        sh(self.root, "git", "config", "user.name", "Test")
        self.feature = self.root / "dev/features/m/demo"
        self.impl = self.feature / "impl/issue-001"
        self.impl.mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src/a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "tests/test_dod.py").write_text('print("dod-ok")\n', encoding="utf-8")
        (self.root / "tests/test_fast.py").write_text('print("fast-ok")\n', encoding="utf-8")
        self.config = self.root / "dev/agents-config.md"
        # The default fixture exercises mandatory external host proof.
        self.config.write_text(CONFIG.replace("review-trust: operator-attested", "review-trust: host-verified"),
                               encoding="utf-8")
        self.spec = self.feature / "demo-tech-spec-2026-11-14.md"
        self.spec.write_text("# Spec\n\n## 3. Behavior\n", encoding="utf-8")
        self.issue = self.feature / "issues/issue-001-demo.md"
        self.issue.parent.mkdir()
        self.issue.write_text(ISSUE, encoding="utf-8")
        sh(self.root, "git", "add", "src", "tests")
        sh(self.root, "git", "commit", "-qm", "base")
        sh(self.root, "git", "checkout", "-qb", "feat/demo-issue-001")
        (self.root / "src/a.py").write_text("a = 2\n", encoding="utf-8")
        sh(self.root, "git", "commit", "-qam", "implement")
        self.context = tc.create_context(self.issue, self.root, self.impl / "ticket-context.json")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = vi.main([
                "run", "implementer", "--issue", str(self.issue), "--repo", str(self.root),
                "--host", "zcode", "--agent", "development", "--session", "impl-session",
                "--model", "provider/dev", "--effort", "medium",
            ])
        self.assertEqual(code, 0)
        self.implementer = json.loads((self.impl / "verification-implementer.json").read_text())["identity"]
        self.reviewer = {
            "host": "zcode", "agent": "supervisor-review", "session": "review-session",
            "model": "provider/supervisor", "effort": "high", "role": "review",
        }
        self.process = self.impl / "process-check-review.json"
        self.process.write_text(json.dumps({
            "issue": "issue-001", "phase": "review", "head": self.head, "tree": self.tree,
            "base": self.context["base"], "verdict": "PASS", "items": [],
        }), encoding="utf-8")
        self.report = self.impl / "review-report.md"
        self.report.write_text("# Review\n\npass\n", encoding="utf-8")
        self.preview = self.impl / "coverage-preview.json"
        self.preview.write_text(json.dumps({"files": ["src/a.py"]}), encoding="utf-8")
        self.proof = self.impl / "host-proof.json"
        self.proof.write_text(json.dumps({
            "verified": True, "attested_by": "host", "identity": self.reviewer,
        }), encoding="utf-8")
        self.host_probes = self.impl / rr.HOST_PROBES_NAME
        report_sha = hashlib.sha256(self.report.read_bytes()).hexdigest()
        self.review_action = {
            "review_id": "issue-001-r1-%s" % report_sha[:16], "round": 1, "issue": "issue-001",
            "head": self.head, "tree": self.tree, "report_sha256": report_sha,
        }
        command = [sys.executable, str(SCRIPTS / "tests" / "fake_host_server.py")]
        self.native_env = mock.patch.dict(os.environ, {
            "FAKE_HOST": "zcode", "FAKE_HOST_LOG": str(self.impl / "native-host.jsonl"),
            "FAKE_HOST_STATE": str(self.impl / "native-state.json"),
            "FAKE_ROLE": "supervisor-review",
            "SESSION_TITLE_ZCODE_COMMAND": shlex.join(command),
        })
        self.native_env.start()
        self.assertNotIn("FAKE_REVIEW", os.environ)
        self.receipt_path = self.impl / "review-receipt.json"

    def tearDown(self):
        self.native_env.stop()
        self.tmp.cleanup()

    def candidate(self):
        return {
            "verdict": "pass", "findings": [], "semantic_closures": [], "round": 1,
            "previous_receipt": None, "implementer": copy.deepcopy(self.implementer),
            "reviewer": copy.deepcopy(self.reviewer), "trust": "host-verified",
            "attestation": None,
            "host_verification": {
                "verified": True, "host": "zcode", "session": "review-session",
                "proof": str(self.proof), "proof_sha256": "0" * 64,
            },
            "coverage": {"preview_sha256": "0" * 64,
                         "files": [{"path": "src/a.py", "status": "reviewed", "reason": ""}]},
        }

    def create(self, candidate=None):
        return rr.create_receipt(
            candidate or self.candidate(), self.issue, self.root, report=self.report,
            process_check_review=self.process, coverage_preview=self.preview,
            output=self.receipt_path)

    def validate(self):
        value = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        return rr.validate_receipt(value, self.issue, self.root, self.receipt_path)

    def test_create_and_validate_cli_with_host_proof(self):
        receipt, errors = self.create()
        self.assertEqual(errors, [])
        self.assertTrue(self.receipt_path.is_file())
        self.assertEqual(receipt["total_files"], 1)
        self.assertEqual(receipt["reviewed_files"], 1)
        self.assertEqual(receipt["skipped_files"], 0)
        self.assertEqual(receipt["diff_sha256"], rr._diff_sha(self.root, self.context["base"], self.head))
        produced = json.loads(self.host_probes.read_text())
        self.assertTrue(produced["written"])
        self.assertEqual(produced["review"], self.review_action)
        self.assertIn("native_hash", produced)
        methods = [json.loads(line).get("method") for line in
                   (self.impl / "native-host.jsonl").read_text().splitlines()]
        self.assertIn("v4/command", methods)
        self.assertIn("session/list", methods)
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT), "validate", "--issue", str(self.issue),
            "--repo", str(self.root), "--receipt", str(self.receipt_path),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validate_rereads_native_proof_without_another_write(self):
        _, errors = self.create()
        self.assertEqual(errors, [])
        log = self.impl / "native-host.jsonl"
        log.unlink()
        self.assertEqual(self.validate(), [])
        methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
        self.assertIn("session/list", methods)
        self.assertNotIn("v4/command", methods)

    def test_direct_isolated_cli_create(self):
        candidate_path = self.impl / "candidate.json"
        candidate_path.write_text(json.dumps(self.candidate()), encoding="utf-8")
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT), "create", "--issue", str(self.issue),
            "--repo", str(self.root), "--input", str(candidate_path),
            "--report", str(self.report), "--process-check", str(self.process),
            "--coverage-preview", str(self.preview), "--output", str(self.receipt_path),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.validate(), [])

    def test_operator_attestation_is_valid_when_explicitly_configured(self):
        self.config.write_text(CONFIG, encoding="utf-8")
        self.context = tc.create_context(self.issue, self.root, self.impl / "ticket-context.json")
        candidate = self.candidate()
        candidate["trust"] = "operator-attested"
        candidate["host_verification"] = None
        candidate["attestation"] = {
            "operator": "operator-1", "statement": "independent review confirmed",
            "confirmed_at": "2026-11-15T00:00:00Z",
        }
        _, errors = self.create(candidate)
        self.assertEqual(errors, [])

    def test_same_session_or_agent_is_not_independent_review(self):
        candidate = self.candidate()
        candidate["reviewer"]["session"] = self.implementer["session"]
        _, errors = self.create(candidate)
        self.assertTrue(any("different sessions" in error for error in errors), errors)
        candidate = self.candidate()
        candidate["reviewer"]["agent"] = self.implementer["agent"]
        _, errors = self.create(candidate)
        self.assertTrue(any("different agents" in error for error in errors), errors)

    def test_wrong_reviewer_model_or_agent_fails_dispatch_resolve(self):
        candidate = self.candidate()
        candidate["reviewer"]["model"] = "provider/dev"
        _, errors = self.create(candidate)
        self.assertTrue(any("model" in error and "dispatch_config.resolve" in error for error in errors), errors)
        candidate = self.candidate()
        candidate["reviewer"]["agent"] = "development"
        _, errors = self.create(candidate)
        self.assertTrue(any("dispatched as supervisor-review" in error for error in errors), errors)

    def test_report_process_and_head_tampering_fail(self):
        _, errors = self.create()
        self.assertEqual(errors, [])
        self.report.write_text("tampered report", encoding="utf-8")
        self.assertTrue(any("report" in error and "tampered" in error for error in self.validate()))
        self.report.write_text("# Review\n\npass\n", encoding="utf-8")
        value = json.loads(self.process.read_text())
        value["verdict"] = "FAIL"
        self.process.write_text(json.dumps(value), encoding="utf-8")
        self.assertTrue(any("process_check_review" in error for error in self.validate()))

    def test_coverage_must_equal_preview_and_actual_diff(self):
        _, errors = self.create()
        self.assertEqual(errors, [])
        receipt = json.loads(self.receipt_path.read_text())
        receipt["coverage"]["files"] = []
        receipt["total_files"] = receipt["reviewed_files"] = receipt["skipped_files"] = 0
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("exact preview full set" in error for error in errors), errors)
        candidate = self.candidate()
        self.preview.write_text(json.dumps({"files": []}), encoding="utf-8")
        _, errors = self.create(candidate)
        self.assertTrue(any("preview" in error for error in errors), errors)

    def test_open_critical_high_cannot_get_pass_receipt(self):
        for severity in ("critical", "high"):
            with self.subTest(severity=severity):
                candidate = self.candidate()
                candidate["findings"] = [{
                    "id": "F-1", "severity": severity, "status": "open",
                    "location": "src/a.py:1", "message": "bad", "closure": "",
                }]
                _, errors = self.create(candidate)
                self.assertTrue(any("not closed" in error for error in errors), errors)

    def test_operator_attestation_cannot_replace_required_host_proof(self):
        candidate = self.candidate()
        candidate["trust"] = "operator-attested"
        candidate["host_verification"] = None
        candidate["attestation"] = {
            "operator": "operator-1", "statement": "independent review confirmed",
            "confirmed_at": "2026-11-14T00:00:00Z",
        }
        _, errors = self.create(candidate)
        self.assertTrue(any("enforcement.review-trust" in error for error in errors), errors)
        os.environ["FAKE_MISSING_SESSION"] = "1"
        _, errors = self.create(self.candidate())
        self.assertRegex("\n".join(errors), "independent_session capability|cannot attach review through native host")

    def test_full_proof_and_hash_replacement_fails_live_probe_receipt(self):
        _, errors = self.create()
        self.assertEqual(errors, [])
        self.proof.write_text(json.dumps({
            "schema": rr.HOST_PROOF_SCHEMA, "verified": True, "attested_by": "host",
            "identity": self.reviewer, "probe": {"host": "zcode", "capabilities": {
                "independent_session": "available",
            }},
        }), encoding="utf-8")
        receipt = json.loads(self.receipt_path.read_text())
        receipt["host_verification"]["proof_sha256"] = rr._sha(self.proof)
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("live host_adapters probe receipt" in error for error in errors), errors)

    def test_simultaneous_probes_proof_hash_replacement_fails(self):
        _, errors = self.create()
        self.assertEqual(errors, [])
        self.host_probes.write_text(json.dumps({"native_hash": "0" * 64}), encoding="utf-8")
        self.proof.write_text(json.dumps({"verified": True, "attested_by": "host"}), encoding="utf-8")
        receipt = json.loads(self.receipt_path.read_text())
        receipt["host_verification"]["proof_sha256"] = rr._sha(self.proof)
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("independent_session capability" in error for error in errors), errors)

    def test_fake_reviewer_session_or_implementer_native_role_fails(self):
        os.environ["FAKE_MISSING_SESSION"] = "1"
        candidate = self.candidate()
        candidate["reviewer"]["session"] = "missing-native-session"
        _, errors = self.create(candidate)
        self.assertRegex("\n".join(errors), "independent_session capability|cannot attach review through native host")
        os.environ.pop("FAKE_MISSING_SESSION", None)
        _, errors = self.create()
        self.assertEqual(errors, [])
        os.environ["FAKE_ROLE"] = "development"
        errors = self.validate()
        self.assertTrue(any("独立评审角色" in error or "independent_session capability" in error
                            for error in errors), errors)

    def test_unknown_capability_blocks_operator_mode_when_required(self):
        self.config.write_text(CONFIG, encoding="utf-8")
        self.context = tc.create_context(self.issue, self.root, self.impl / "ticket-context.json")
        os.environ["FAKE_MISSING_SESSION"] = "1"
        candidate = self.candidate()
        candidate["trust"] = "operator-attested"
        candidate["host_verification"] = None
        candidate["attestation"] = {
            "operator": "operator-1", "statement": "independent review confirmed",
            "confirmed_at": "2026-11-15T00:00:00Z",
        }
        _, errors = self.create(candidate)
        self.assertRegex("\n".join(errors), "independent_session capability|cannot attach review through native host")
        self.assertFalse(self.receipt_path.exists())

    def test_any_head_change_invalidates_receipt(self):
        _, errors = self.create()
        self.assertEqual(errors, [])
        (self.root / "src/a.py").write_text("a = 3\n", encoding="utf-8")
        sh(self.root, "git", "commit", "-qam", "late")
        errors = self.validate()
        self.assertTrue(any("head/tree is stale" in error for error in errors), errors)


class FeatureReviewReceiptTest(unittest.TestCase):
    """Whole-feature review receipts bound to integration/candidate.json."""

    def linked_candidate(self):
        candidate = self.root / ".linked-candidate"
        sh(self.root, "git", "worktree", "add", "-q", "--detach", str(candidate), self.candidate)
        config = candidate / "dev" / "agents-config.md"
        config.parent.mkdir()
        return candidate, config

    def test_feature_config_uses_authoritative_feature_workspace(self):
        candidate, candidate_config = self.linked_candidate()
        candidate_config.write_text("obsolete candidate checkout config\n", encoding="utf-8")
        self.assertEqual(rr._load_feature_config(candidate, self.feature)["enforcement"]["review-trust"],
                         "host-verified")

    def test_feature_config_rejects_invalid_authoritative_config(self):
        candidate, candidate_config = self.linked_candidate()
        candidate_config.write_text(CONFIG, encoding="utf-8")
        self.config.write_text(CONFIG.replace("verify-policy:\n", "verify-policy:\n  known-failures: []\n"),
                               encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown property 'known-failures'"):
            rr._load_feature_config(candidate, self.feature)

    def test_feature_config_does_not_fall_back_when_authoritative_config_missing(self):
        candidate, candidate_config = self.linked_candidate()
        candidate_config.write_text(CONFIG, encoding="utf-8")
        self.config.unlink()
        with self.assertRaises(ValueError):
            rr._load_feature_config(candidate, self.feature)

    def test_feature_config_rejects_unrelated_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            other_repo = Path(directory)
            sh(other_repo, "git", "init", "-q", "-b", "main")
            with self.assertRaisesRegex(rr.ReceiptError, "same Git repository"):
                rr._load_feature_config(other_repo, self.feature)

    def test_feature_config_rejects_noncanonical_feature_path(self):
        outside = self.root / "outside" / "m" / "demo"
        outside.mkdir(parents=True)
        with self.assertRaisesRegex(rr.ReceiptError, "dev/features"):
            rr._load_feature_config(self.root, outside)

    def test_feature_config_rejects_symlinked_policy(self):
        candidate, candidate_config = self.linked_candidate()
        candidate_config.write_text(CONFIG, encoding="utf-8")
        self.config.unlink()
        self.config.symlink_to(candidate_config)
        with self.assertRaisesRegex(rr.ReceiptError, "regular file"):
            rr._load_feature_config(candidate, self.feature)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "test@example.com")
        sh(self.root, "git", "config", "user.name", "Test")
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src/a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "tests/test_fast.py").write_text('print("fast-ok")\n', encoding="utf-8")
        self.config = self.root / "dev/agents-config.md"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(CONFIG.replace("review-trust: operator-attested",
                                              "review-trust: host-verified"), encoding="utf-8")
        sh(self.root, "git", "add", "src", "tests")
        sh(self.root, "git", "commit", "-qm", "base")
        self.main_head = sh(self.root, "git", "rev-parse", "HEAD")
        self.main_tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")

        sh(self.root, "git", "checkout", "-qb", "feat/demo")
        (self.root / "src/a.py").write_text("a = 2\n", encoding="utf-8")
        (self.root / "src/b.py").write_text("b = 1\n", encoding="utf-8")
        sh(self.root, "git", "add", "src")
        sh(self.root, "git", "commit", "-qm", "implement feature")
        self.candidate = sh(self.root, "git", "rev-parse", "HEAD")
        self.tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")

        self.feature = self.root / "dev/features/m/demo"
        (self.feature / "issues").mkdir(parents=True)
        (self.feature / "integration").mkdir()
        (self.feature / "issues/issue-001-demo.md").write_text(
            "---\nfeature-slug: demo\nissue-id: issue-001\nstatus: done\n"
            "dispatch:\n  host: zcode\n  model: provider/dev\n"
            "  session_id: impl-session-1\n  agent: development\n---\n# One\n",
            encoding="utf-8")
        (self.feature / "issues/issue-002-demo.md").write_text(
            "---\nfeature-slug: demo\nissue-id: issue-002\nstatus: done\n"
            "dispatch:\n  host: zcode\n  model: provider/dev\n"
            "  session_id: impl-session-2\n  agent: development\n---\n# Two\n",
            encoding="utf-8")
        self.plan = self.feature / "acceptance-plan.yaml"
        self.plan.write_text("schema: agent-dev.feature-acceptance/v2\ncases: {}\n", encoding="utf-8")
        self.plan_sha = hashlib.sha256(self.plan.read_bytes()).hexdigest()

        self.reviewer = {
            "host": "zcode", "agent": "supervisor-review", "session": "review-session",
            "model": "provider/supervisor", "effort": "high", "role": "review",
        }
        self.report = self.feature / "integration/review-report.md"
        self.report.write_text("# Feature review\n\npass\n", encoding="utf-8")
        self.preview = self.feature / "integration/coverage-preview.json"
        self.write_preview()
        self.process = self.feature / "integration/process-check-review.json"
        self.write_process()
        self.proof = self.feature / "integration/feature-review-host-proof.json"
        self.receipt_path = self.feature / "integration/review-receipt.json"
        self.candidate_json = self.feature / "integration/candidate.json"
        self.write_candidate()

        command = [sys.executable, str(TESTS / "fake_host_server.py")]
        self.native_env = mock.patch.dict(os.environ, {
            "FAKE_HOST": "zcode",
            "FAKE_HOST_LOG": str(self.feature / "integration/native-host.jsonl"),
            "FAKE_HOST_STATE": str(self.feature / "integration/native-state.json"),
            "FAKE_ROLE": "supervisor-review",
            "SESSION_TITLE_ZCODE_COMMAND": shlex.join(command),
        })
        self.native_env.start()
        self.assertNotIn("FAKE_REVIEW", os.environ)

    def tearDown(self):
        self.native_env.stop()
        self.tmp.cleanup()

    def write_preview(self, files=None):
        self.preview.write_text(json.dumps({"files": files if files is not None else [
            {"path": "src/a.py", "status": "reviewed", "reason": ""},
            {"path": "src/b.py", "status": "reviewed", "reason": ""},
        ]}), encoding="utf-8")

    def write_process(self):
        self.process.write_text(json.dumps({
            "feature": "m/demo", "candidate": self.candidate, "candidate_tree": self.tree,
            "main_head": self.main_head, "verdict": "PASS",
        }), encoding="utf-8")

    def payload(self, **overrides):
        value = {
            "schema": "agent-dev.feature-candidate/v2",
            "feature": "m/demo",
            "round": 1,
            "integration_branch": "feat/demo",
            "main_head": self.main_head,
            "merge_base": self.main_head,
            "candidate": self.candidate,
            "candidate_tree": self.tree,
            "frozen_at": "2026-11-16T00:00:00Z",
            "issues": [{"id": "issue-001"}, {"id": "issue-002"}],
            "acceptance_plan": {"path": "acceptance-plan.yaml", "sha256": self.plan_sha,
                                "cases": ["AC-1"]},
            "reviewer": copy.deepcopy(self.reviewer),
            "findings": [],
            "previous_receipt_sha256": None,
            "trust": "host-verified",
            "attestation": None,
            "host_verification": {
                "verified": True, "host": "zcode", "session": "review-session",
                "proof": str(self.proof), "proof_sha256": "0" * 64,
            },
        }
        value.update(overrides)
        return value

    def write_candidate(self, **overrides):
        value = self.payload(**overrides)
        self.candidate_json.write_text(json.dumps(value, indent=2), encoding="utf-8")
        return value

    def create(self, output=None, preview=None, **overrides):
        if overrides:
            self.write_candidate(**overrides)
        return rr.create_feature_receipt(
            self.feature, self.root, self.candidate_json, report=self.report,
            coverage_preview=preview if preview is not None else self.preview,
            output=output or self.receipt_path)

    def validate(self, path=None):
        return rr.validate_feature_receipt(self.feature, self.root, path or self.receipt_path)

    def advance_feature(self):
        (self.root / "src/b.py").write_text("b = 2\n", encoding="utf-8")
        sh(self.root, "git", "commit", "-qam", "rework")
        self.candidate = sh(self.root, "git", "rev-parse", "HEAD")
        self.tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")

    def test_pass_receipt_binds_whole_diff_and_native_feature_action(self):
        receipt, errors = self.create()
        self.assertEqual(errors, [])
        self.assertTrue(self.receipt_path.is_file())
        self.assertEqual(receipt["base"], self.main_head)
        self.assertEqual(receipt["head"], self.candidate)
        self.assertEqual(receipt["tree"], self.tree)
        self.assertEqual(receipt["plan_sha256"], self.plan_sha)
        self.assertEqual(receipt["diff_sha256"],
                         rr._feature_diff_sha(self.root, self.main_head, self.candidate))
        self.assertEqual((receipt["total_files"], receipt["reviewed_files"],
                          receipt["skipped_files"]), (2, 2, 0))
        self.assertEqual([item["issue"] for item in receipt["implementers"]],
                         ["issue-001", "issue-002"])
        self.assertEqual(receipt["reviewer"]["session"], "review-session")
        probes = json.loads((self.feature / "integration/host-probes.json").read_text())
        self.assertTrue(probes["written"])
        self.assertEqual(probes["review"]["feature"], "m/demo")
        self.assertEqual(probes["review"]["head"], self.candidate)
        self.assertEqual(probes["review"]["tree"], self.tree)
        proof = json.loads(self.proof.read_text())
        action = proof["native_proof"]["review"]
        self.assertEqual(action["feature"], "m/demo")
        self.assertEqual(action["candidate"], self.candidate)
        self.assertEqual(action["tree"], self.tree)
        self.assertEqual(action["main_head"], self.main_head)
        self.assertEqual(self.validate(), [])
        methods = [json.loads(line).get("method") for line in
                   (self.feature / "integration/native-host.jsonl").read_text().splitlines()]
        self.assertIn("v4/command", methods)
        self.assertIn("session/list", methods)

    def test_coverage_must_exactly_match_feature_diff(self):
        self.write_preview([{"path": "src/a.py", "status": "reviewed", "reason": ""}])
        _, errors = self.create()
        self.assertTrue(any("feature diff file set" in error for error in errors), errors)
        self.assertFalse(self.receipt_path.exists())

        self.write_preview([
            {"path": "src/a.py", "status": "reviewed", "reason": ""},
            {"path": "src/b.py", "status": "skipped", "reason": ""},
        ])
        _, errors = self.create()
        self.assertTrue(any("skipped coverage lacks reason" in error for error in errors), errors)

        self.write_preview([
            {"path": "src/a.py", "status": "reviewed", "reason": ""},
            {"path": "src/b.py", "status": "skipped", "reason": "generated fixture"},
        ])
        receipt, errors = self.create()
        self.assertEqual(errors, [])
        self.assertEqual(receipt["skipped_files"], 1)

    def test_open_critical_or_high_cannot_get_pass_receipt(self):
        for severity in ("critical", "high"):
            with self.subTest(severity=severity):
                finding = {
                    "id": "F-1", "severity": severity, "status": "open",
                    "location": "src/a.py:1", "message": "bad", "closure": "",
                }
                _, errors = self.create(findings=[finding])
                self.assertTrue(any("not closed" in error for error in errors), errors)
                self.assertFalse(self.receipt_path.exists())
        _, errors = self.create(findings=[{
            "id": "F-2", "severity": "critical", "status": "closed",
            "location": "src/a.py:1", "message": "bad", "closure": "",
        }])
        self.assertTrue(any("not closed" in error for error in errors), errors)

    def test_reviewer_must_differ_from_every_ticket_implementer(self):
        reviewer = copy.deepcopy(self.reviewer)
        reviewer["session"] = "impl-session-2"
        _, errors = self.create(reviewer=reviewer)
        self.assertTrue(any("reviewer session equals implementer session for issue-002" in error
                            for error in errors), errors)
        reviewer = copy.deepcopy(self.reviewer)
        reviewer["agent"] = "development"
        _, errors = self.create(reviewer=reviewer)
        self.assertTrue(any("reviewer agent equals implementer agent for issue-001" in error
                            for error in errors), errors)

    def test_candidate_tree_or_branch_change_invalidates_receipt(self):
        _, errors = self.create()
        self.assertEqual(errors, [])
        self.advance_feature()
        errors = self.validate()
        self.assertTrue(any("integration branch advanced" in error for error in errors), errors)

        receipt = json.loads(self.receipt_path.read_text())
        receipt["tree"] = self.main_tree
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("tree does not match" in error for error in errors), errors)

        self.write_candidate(candidate=self.main_head, candidate_tree=self.main_tree)
        errors = self.validate()
        self.assertTrue(any("head differs from candidate.json" in error for error in errors), errors)

    def test_report_coverage_and_process_check_tampering_fails(self):
        receipt, errors = self.create()
        self.assertEqual(errors, [])
        original_receipt = self.receipt_path.read_bytes()
        self.report.write_text("tampered report", encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("report is missing or tampered" in error for error in errors), errors)

        self.receipt_path.write_bytes(original_receipt)
        receipt = json.loads(self.receipt_path.read_text())
        receipt["report_sha256"] = "0" * 64
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("report" in error for error in errors), errors)

        self.receipt_path.write_bytes(original_receipt)
        self.preview.write_text(json.dumps({"files": []}), encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("coverage preview is missing or tampered" in error for error in errors), errors)

        self.receipt_path.write_bytes(original_receipt)
        self.process.write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
        errors = self.validate()
        self.assertTrue(any("process_check is missing or tampered" in error for error in errors), errors)

    def test_re_review_chain_and_stale_previous_round(self):
        first = self.feature / "integration/review-receipt-r1.json"
        receipt1, errors = self.create(output=first)
        self.assertEqual(errors, [])
        first_sha = hashlib.sha256(first.read_bytes()).hexdigest()

        self.advance_feature()
        self.write_process()
        self.report.write_text("# Feature review round 2\n\npass\n", encoding="utf-8")
        second = self.feature / "integration/review-receipt-r2.json"
        receipt2, errors = self.create(
            output=second, round=2, previous_receipt_sha256=first_sha)
        self.assertEqual(errors, [])
        self.assertEqual(receipt2["round"], 2)
        self.assertEqual(receipt2["previous_receipt_sha256"], first_sha)
        self.assertEqual(receipt2["previous_receipt"]["sha256"], first_sha)
        self.assertEqual(self.validate(second), [])
        self.assertTrue(self.validate(first))

        receipt = json.loads(second.read_text())
        receipt["previous_receipt_sha256"] = "0" * 64
        second.write_text(json.dumps(receipt), encoding="utf-8")
        errors = self.validate(second)
        self.assertTrue(any("previous receipt" in error for error in errors), errors)

    def test_direct_isolated_cli_create_and_validate_feature(self):
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT), "create-feature",
            "--feature-dir", str(self.feature), "--repo", str(self.root),
            "--input", str(self.candidate_json), "--report", str(self.report),
            "--coverage-preview", str(self.preview), "--output", str(self.receipt_path),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT), "validate-feature",
            "--feature-dir", str(self.feature), "--repo", str(self.root),
            "--receipt", str(self.receipt_path),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.validate(), [])

    def test_sibling_feature_review_metadata_is_accepted(self):
        review = self.candidate_json.with_name("feature-review.json")
        review.write_text(json.dumps({
            "reviewer": copy.deepcopy(self.reviewer), "findings": [], "round": 1,
            "trust": "host-verified",
            "host_verification": {
                "verified": True, "host": "zcode", "session": "review-session",
                "proof": str(self.proof), "proof_sha256": "0" * 64,
            },
        }), encoding="utf-8")
        pure = self.payload()
        for key in ("reviewer", "findings", "round", "trust", "host_verification"):
            pure.pop(key)
        self.candidate_json.write_text(json.dumps(pure), encoding="utf-8")
        receipt, errors = rr.create_feature_receipt(
            self.feature, self.root, self.candidate_json, report=self.report,
            coverage_preview=self.preview, output=self.receipt_path)
        self.assertEqual(errors, [])
        self.assertEqual(receipt["reviewer"]["session"], "review-session")


if __name__ == "__main__":
    unittest.main()
