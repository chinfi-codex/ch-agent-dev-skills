#!/usr/bin/env python3
"""v2 process-check aggregation tests, including v1 weak-semantics negative controls."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import agents_config as ac
import process_check as pc
import review_receipt as rr
import ticket_context as tc
import verify_issue as vi
from test_verify_issue import CONFIG, ISSUE, sh


SCRIPT = SCRIPTS / "process_check.py"

LOG = """# impl-log issue-001

## 需求覆盖矩阵

| 需求点 | 实现位置 | 覆盖测试 | evidence | 关键 |
|---|---|---|---|---|
| DoD-1 | src/a.py:f | tests/test_dod.py | verification-implementer.json#DoD-1 | ★ |

## 红线接触记录
无；登记不能豁免 deny。

## 范围变更记录
无；越界必须重新确认/再拆，登记不能豁免 scope。
"""

SUCCESSOR = """---
feature-slug: demo
issue-id: issue-002
type: feature
blocked-by: [issue-001]
---
# Next
## 验收标准（DoD）
- [ ] `python3 -I tests/test_fast.py`
"""


class ProcessCheckV2Test(unittest.TestCase):
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
        (self.root / "tests/test_existing.py").write_text("assert True\n", encoding="utf-8")
        self.config = self.root / "dev/agents-config.md"
        self.config.write_text(CONFIG.replace("review-trust: operator-attested", "review-trust: host-verified"),
                               encoding="utf-8")
        self.spec = self.feature / "demo-tech-spec-2026-11-14.md"
        self.spec.write_text("# Spec\n\n## 3. Behavior\n", encoding="utf-8")
        self.issue = self.feature / "issues/issue-001-demo.md"
        self.issue.parent.mkdir()
        self.issue.write_text(ISSUE.replace("declared-scope: [src/**, tests/**]",
                                            "declared-scope: [src/**, tests/**, docs/**, generated/**]"), encoding="utf-8")
        sh(self.root, "git", "add", "src", "tests")
        sh(self.root, "git", "commit", "-qm", "base")
        sh(self.root, "git", "checkout", "-qb", "feat/demo-issue-001")
        (self.root / "src/a.py").write_text("a = 1\na = 2\n", encoding="utf-8")
        sh(self.root, "git", "commit", "-qam", "implement")
        # Untracked Markdown is a ticket artifact, not source; successor semantics
        # still come from ticket_context.parse_issue.
        self.successor = self.feature / "issues/issue-002-next.md"
        self.successor.write_text(SUCCESSOR, encoding="utf-8")
        self.context_path = self.impl / "ticket-context.json"
        self.context = tc.create_context(self.issue, self.root, self.context_path)
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")
        self.log = self.impl / "impl-log-issue-001.md"
        self.log.write_text(LOG, encoding="utf-8")
        self.old_human = sys.modules.get("human_decision")
        self.human = types.ModuleType("human_decision")
        self.human.active_for_issue = lambda repo, feature, issue: []
        sys.modules["human_decision"] = self.human
        self.run_verify("implementer")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.run_verify("supervisor")
        self.report = self.impl / "review-report.md"
        self.report.write_text("# Review\n\npass\n", encoding="utf-8")
        self.preview = self.impl / "coverage-preview.json"
        self.reviewer = {
            "host": "zcode", "agent": "supervisor-review", "session": "review-session",
            "model": "provider/supervisor", "effort": "high", "role": "review",
        }
        self.proof = self.impl / "host-proof.json"
        self.proof.write_text(json.dumps({"verified": True, "attested_by": "host",
                                          "identity": self.reviewer}), encoding="utf-8")
        self.host_probes = self.impl / rr.HOST_PROBES_NAME
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
        errors = self.make_receipt()
        self.assertEqual(errors, [])
        self.conformance = self.impl / "conformance-issue-001.md"
        self.write_conformance()

    def tearDown(self):
        self.native_env.stop()
        if self.old_human is None:
            sys.modules.pop("human_decision", None)
        else:
            sys.modules["human_decision"] = self.old_human
        self.tmp.cleanup()

    def run_verify(self, runner, repo=None):
        repo = Path(repo or self.root)
        identity = (["--host", "zcode", "--agent", "development", "--session", "impl-session",
                     "--model", "provider/dev", "--effort", "medium"] if runner == "implementer" else
                    ["--host", "zcode", "--agent", "supervisor", "--session", "supervisor-session",
                     "--model", "provider/supervisor", "--effort", "high"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = vi.main(["run", runner, "--issue", str(self.issue), "--repo", str(repo), *identity])
        self.assertEqual(code, 0, "verification fixture must pass")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")

    def check(self, phase, repo=None, direct=True):
        output = self.impl / ("process-check-%s.json" % phase)
        arguments = ["--issue", str(self.issue), "--repo", str(repo or self.root),
                     "--phase", phase, "--json", str(output)]
        if direct:
            result = subprocess.run([sys.executable, "-I", str(SCRIPT), *arguments],
                                    capture_output=True, text=True)
            return result.returncode, result.stdout + result.stderr
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
            code = pc.main(arguments)
        return code, stdout.getvalue()

    def changed_paths(self):
        return sh(self.root, "git", "diff", "--name-only", self.context["base"], "HEAD").splitlines()

    def make_receipt(self, semantic_closures=None):
        paths = self.changed_paths()
        self.preview.write_text(json.dumps({"files": paths}), encoding="utf-8")
        implementer = json.loads((self.impl / "verification-implementer.json").read_text())["identity"]
        candidate = {
            "verdict": "pass", "findings": [], "semantic_closures": semantic_closures or [],
            "round": 1, "previous_receipt": None, "implementer": implementer,
            "reviewer": self.reviewer, "trust": "host-verified", "attestation": None,
            "host_verification": {"verified": True, "host": "zcode", "session": "review-session",
                                  "proof": str(self.proof), "proof_sha256": "0" * 64},
            "coverage": {"preview_sha256": "0" * 64,
                         "files": [{"path": path, "status": "reviewed", "reason": ""} for path in paths]},
        }
        _, errors = rr.create_receipt(candidate, self.issue, self.root, report=self.report,
                                      process_check_review=self.impl / "process-check-review.json",
                                      coverage_preview=self.preview, output=self.receipt_path)
        return errors

    def write_conformance(self, verdict="conform", feature=True, sample=True, empty=False,
                          selected=True, rate="1", algorithm="sha256-v1"):
        requirement_rows = "" if empty else (
            "| 1 | 行为更新 | DoD-1 / spec#3-behavior | 已实现 | "
            "`src/a.py:f` + `tests/test_dod.py` + `verification-implementer.json#DoD-1` |\n"
        )
        sample_text = ("- DoD-1 `src/a.py:f` `tests/test_dod.py` `verification-implementer.json#DoD-1`\n"
                       if sample else "未打开关键代码\n")
        feature_text = """
## Feature 级覆盖核对（仅集成票）

| # | Feature 需求 | 判定 | 证据 |
|---|---|---|---|
| 1 | 完整行为 | 已实现 | `src/a.py:f` `tests/test_dod.py` `verification-supervisor.json#DoD-1` |
""" if feature else ""
        self.conformance.write_text(
            "```yaml\nissue: issue-001\ncontext_digest: %s\ncommit: %s\ntree: %s\n"
            "checker: main-session\nchecker_session: main-session-1\nverdict: %s\n"
            "selection:\n  algorithm: %s\n  rate: %s\n  selected: %s\ndate: 2026-11-15\n```\n\n"
            "## 需求点清单（反向枚举）\n\n"
            "| # | 需求点 | 来源 | 判定 | 核对依据 |\n|---|---|---|---|---|\n%s\n"
            "## 抽查记录\n\n%s\n%s" % (
                self.context["gate2_context_digest"], self.head, self.tree, verdict,
                algorithm, rate, str(selected).lower(), requirement_rows, sample_text, feature_text),
            encoding="utf-8")

    def config_patch(self, mutate):
        original = ac.load
        def load(path):
            value = original(path)
            mutate(value)
            return value
        return mock.patch.object(ac, "load", new=load)

    def test_review_merge_close_happy_path_and_exact_metadata(self):
        for phase in ("review", "merge", "close"):
            with self.subTest(phase=phase):
                code, out = self.check(phase)
                self.assertEqual(code, 0, out)
        result = json.loads((self.impl / "process-check-close.json").read_text())
        self.assertEqual(result["head"], self.head)
        self.assertEqual(result["tree"], self.tree)
        self.assertEqual(result["production-files"], 1)
        self.assertEqual(result["production-loc"], 1)
        self.assertFalse(result["integration-ticket"])

    def test_status_dispatch_and_verify_fail_phases_keep_context_valid_without_mocks(self):
        original = self.issue.read_text(encoding="utf-8")
        digest = self.context["gate2_context_digest"]
        for index, phase in enumerate(("confirmed", "start", "in-progress", "in-review",
                                       "mr-submitted", "merged", "done")):
            with self.subTest(phase=phase):
                text = original.replace("status: in-progress", "status: " + phase)
                text = text.replace("created: 2026-11-14",
                                    "verify-fails: %d\ndispatch: {host: zcode, model: provider/dev, attempts: %d}\ncreated: 2026-11-14" % (index, index + 1))
                self.issue.write_text(text, encoding="utf-8")
                code, out = self.check("review")
                self.assertEqual(code, 0, out)
                self.assertEqual(tc.load_context(self.context_path)["gate2_context_digest"], digest)
                self.assertEqual(tc.issue_content_hash(self.issue), self.context["hashes"]["issue"])

    def test_removed_v1_cli_overrides_and_promote_are_rejected(self):
        common = [sys.executable, "-I", str(SCRIPT), "--issue", str(self.issue),
                  "--repo", str(self.root)]
        invocations = [
            common + ["--phase", "promote"],
            common + ["--phase", "review", "--base", self.context["base"]],
            common + ["--phase", "review", "--head", "HEAD"],
            common + ["--phase", "review", "--evidence", "evidence.md"],
            common + ["--phase", "review", "--impl-log", "impl.md"],
        ]
        for arguments in invocations:
            with self.subTest(arguments=arguments[-2:]):
                result = subprocess.run(arguments, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertRegex(result.stderr, "unrecognized arguments|invalid choice")

    def test_handwritten_yaml_evidence_is_not_accepted(self):
        (self.impl / "verification-implementer.json").unlink()
        (self.impl / "evidence-issue-001.md").write_text(
            "```yaml\ncommit: %s\nall-passed: true\n```\n" % self.head, encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("verification JSON missing", out)

    def test_short_head_prefix_is_never_accepted_as_fresh(self):
        path = self.impl / "verification-implementer.json"
        document = json.loads(path.read_text())
        document["head"] = self.head[:7]
        path.write_text(json.dumps(document), encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("git-sha", out)

    def test_any_head_change_requires_rerun(self):
        (self.root / "src/a.py").write_text("a = 3\n", encoding="utf-8")
        sh(self.root, "git", "commit", "-qam", "late")
        code, out = self.check("merge")
        self.assertEqual(code, 1)
        self.assertIn("stale", out)

    def test_registered_deny_still_fails_for_modified_path(self):
        self.log.write_text(LOG + "\nsrc/a.py 已登记\n", encoding="utf-8")
        with self.config_patch(lambda c: c["verify-policy"].update({"deny": ["src/a.py"]})):
            code, out = self.check("review", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("redline-deny", out)
        self.assertIn("never exempt", out)

    def test_out_of_scope_registration_does_not_exempt_drift(self):
        other = self.root / "other"
        other.mkdir()
        (other / "b.py").write_text("b = 1\n", encoding="utf-8")
        sh(self.root, "git", "add", "other/b.py")
        sh(self.root, "git", "commit", "-qm", "out of scope")
        self.log.write_text(LOG + "\nother/b.py 漂移已登记\n", encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("outside declared-scope", out)
        self.assertIn("other/b.py", out)

    def test_rename_reconciles_old_and_new_paths(self):
        sh(self.root, "git", "mv", "src/a.py", "src/renamed.py")
        sh(self.root, "git", "commit", "-qm", "rename production")
        files = pc.changed_files(self.root, self.context["base"], "HEAD")
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0]["status"].startswith("R"))
        self.assertEqual(files[0]["old_path"], "src/a.py")
        self.assertEqual(files[0]["path"], "src/renamed.py")
        rep = pc.Report()
        pc._check_scope_redlines(rep, files, {"declared_scope": ["src/renamed.py"]},
                                 {"deny": ["src/a.py", "src/renamed.py"]})
        self.assertEqual(sum(item["check"] == "redline-deny" for item in rep.items), 2)
        self.assertTrue(any(item["check"] == "scope" and "src/a.py" in item["msg"] for item in rep.items))

    def test_ticket_size_uses_actual_production_diff_only(self):
        (self.root / "src/a.py").write_text("a = 1\na = 2\nsecond = 3\n", encoding="utf-8")
        sh(self.root, "git", "commit", "-qam", "two production lines")
        self.run_verify("implementer")
        with self.config_patch(lambda c: c["verify-policy"].update({"ticket-max-loc": 1})):
            code, out = self.check("review", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("production LOC 2 > 1", out)
        result = json.loads((self.impl / "process-check-review.json").read_text())
        self.assertEqual(result["production-files"], 1)
        self.assertEqual(result["production-loc"], 2)

    def test_merge_requires_supervisor_and_review_receipt(self):
        # Legacy diagnostic path retained: delivery no longer calls merge, but
        # the explicit compatibility phase still requires the full evidence set.
        (self.impl / "verification-supervisor.json").unlink()
        code, out = self.check("merge")
        self.assertEqual(code, 1)
        self.assertIn("verification-supervisor.json", out)
        self.run_verify("supervisor")
        self.receipt_path.unlink()
        code, out = self.check("merge")
        self.assertEqual(code, 1)
        self.assertIn("review receipt missing", out)

    def test_ticket_phase_passes_without_supervisor_receipt_or_conformance(self):
        (self.impl / "verification-supervisor.json").unlink()
        self.receipt_path.unlink()
        self.conformance.unlink()
        # Re-run the implementer so its generation is current and the evidence
        # rendering belongs to the only remaining runner.
        self.run_verify("implementer")
        code, out = self.check("ticket")
        self.assertEqual(code, 0, out)
        self.assertNotIn("verification-supervisor", out)
        self.assertNotIn("review receipt", out)
        self.assertNotIn("[FAIL] conformance", out)

    def test_ticket_phase_requires_implementer_and_active_decision_blocks(self):
        (self.impl / "verification-implementer.json").unlink()
        code, out = self.check("ticket")
        self.assertEqual(code, 1)
        self.assertIn("verification JSON missing", out)
        self.run_verify("implementer")
        self.human.active_for_issue = lambda repo, feature, issue: [{"id": "D-1"}]
        code, out = self.check("ticket", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("active human decision", out)

    def test_ticket_phase_runs_inner_checks_but_not_feature_conformance(self):
        with mock.patch.object(pc, "_check_conformance", side_effect=AssertionError("must not run")), \
                mock.patch.object(pc, "_load_receipt", side_effect=AssertionError("must not run")), \
                mock.patch.object(pc, "_validate_verification", wraps=pc._validate_verification) as validate:
            code, out = self.check("ticket", direct=False)
        self.assertEqual(code, 0, out)
        self.assertNotIn("architecture", out)
        runners = [call.args[5] for call in validate.call_args_list]
        self.assertEqual(["implementer"], runners)

    def test_ticket_phase_existing_test_mapping_needs_no_receipt_closure(self):
        self.prepare_existing_test_change()
        mapping = self.existing_test_mapping()
        self.log.write_text(LOG + "\n```json\n" + json.dumps({"test-changes": [mapping]}) + "\n```\n",
                            encoding="utf-8")
        # The receipt from setUp is now stale and must not be consulted.
        code, out = self.check("ticket")
        self.assertEqual(code, 0, out)
        self.assertIn("[REVIEW] test-change", out)
        self.assertNotIn("review-receipt", out)

    def test_added_test_requires_structured_red_record(self):
        (self.root / "tests/test_new.py").write_text("assert True\n", encoding="utf-8")
        sh(self.root, "git", "add", "tests/test_new.py")
        sh(self.root, "git", "commit", "-qm", "add test")
        self.run_verify("implementer")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("added tests lack", out)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            red_code = vi.main([
                "red", "--issue", str(self.issue), "--repo", str(self.root), "--id", "red-new",
                "--command", 'python3 -I -c "raise AssertionError(\'first-red\')"',
                "--expected-output", "first-red", "--test-path", "tests/test_new.py",
                "--host", "zcode", "--agent", "development", "--session", "impl-session",
                "--model", "provider/dev", "--effort", "medium",
            ])
        self.assertEqual(red_code, 0)
        self.run_verify("implementer")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)

    def existing_test_mapping(self):
        return {
            "path": "tests/test_existing.py", "dod_id": "DoD-1",
            "spec_anchor": "dev/features/m/demo/demo-tech-spec-2026-11-14.md#3-behavior",
            "conflict": "DoD-1 behavior changed; old assertion conflicts with the approved spec",
            "scope": "src/a.py behavior and its direct assertion",
        }

    def prepare_existing_test_change(self):
        path = self.root / "tests/test_existing.py"
        path.write_text('# approved behavior update\nprint("dod-ok")\n', encoding="utf-8")
        sh(self.root, "git", "add", "tests/test_existing.py")
        sh(self.root, "git", "commit", "-qm", "update existing test")
        self.run_verify("implementer")

    def test_existing_test_needs_real_mapping_and_receipt_semantic_closure(self):
        self.prepare_existing_test_change()
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("exactly one structured mapping", out)
        mapping = self.existing_test_mapping()
        self.log.write_text(LOG + "\n```json\n" + json.dumps({"test-changes": [mapping]}) + "\n```\n", encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.assertIn("[REVIEW] test-change", out)
        # Refresh supervisor after the same HEAD; merge must reject missing closure.
        self.run_verify("supervisor")
        self.assertEqual(self.make_receipt(), [])
        code, out = self.check("merge")
        self.assertEqual(code, 1)
        self.assertIn("semantic closure", out)
        closure = {"path": mapping["path"], "dod_id": mapping["dod_id"],
                   "spec_anchor": mapping["spec_anchor"], "mapping_sha256": pc._canonical_hash(mapping),
                   "status": "closed", "note": "reviewed DoD/spec/conflict/scope mapping"}
        self.assertEqual(self.make_receipt([closure]), [])
        code, out = self.check("merge")
        self.assertEqual(code, 0, out)

    def test_fake_dod_or_spec_anchor_and_test_deletion_fail(self):
        self.prepare_existing_test_change()
        mapping = self.existing_test_mapping()
        mapping["dod_id"] = "DoD-99"
        self.log.write_text(LOG + "\n```json\n" + json.dumps({"test-changes": [mapping]}) + "\n```\n", encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("real DoD ID", out)
        sh(self.root, "git", "rm", "-q", "tests/test_existing.py")
        sh(self.root, "git", "commit", "-qm", "delete test")
        self.run_verify("implementer")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("deletion is denied", out)

    def test_generator_reproduces_tree_and_rejects_out_of_glob_write(self):
        generated = self.root / "generated"
        generated.mkdir()
        (generated / "out.txt").write_text("ok\n", encoding="utf-8")
        sh(self.root, "git", "add", "generated/out.txt")
        sh(self.root, "git", "commit", "-qm", "generated output")
        head = sh(self.root, "git", "rev-parse", "HEAD")
        tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")
        good = {"id": "gen-1", "glob": "generated/**", "generator":
                'python3 -I -c "from pathlib import Path; Path(\'generated/out.txt\').write_text(\'ok\\n\')"'}
        rep = pc.Report()
        pc._run_generator(rep, self.root, head, tree, good, ["generated/out.txt"])
        self.assertFalse(rep.failed, rep.items)
        bad = dict(good, generator='python3 -I -c "from pathlib import Path; Path(\'src/a.py\').write_text(\'bad\\n\')"')
        rep = pc.Report()
        pc._run_generator(rep, self.root, head, tree, bad, ["generated/out.txt"])
        self.assertTrue(rep.failed)
        self.assertTrue(any("outside output glob" in item["msg"] for item in rep.items))
        stale = dict(good, generator='python3 -I -c "from pathlib import Path; Path(\'generated/out.txt\').write_text(\'stale\\n\')"')
        rep = pc.Report()
        pc._run_generator(rep, self.root, head, tree, stale, ["generated/out.txt"])
        self.assertTrue(any("exact HEAD tree" in item["msg"] for item in rep.items))
        rep = pc.Report()
        pc._check_generated(rep, self.root, [], {"generated-refresh": [{"glob": "generated/**", "generator": "x"}]}, head, tree)
        self.assertTrue(any("IDs" in item["msg"] for item in rep.items))

    def test_artifact_budget_and_raw_manifest_are_hard_gates(self):
        with self.config_patch(lambda c: c["artifact-budget"].update({"impl-log-max-lines": 1})):
            code, out = self.check("review", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("impl-log lines", out)
        with self.config_patch(lambda c: c["artifact-budget"].update({"evidence-max-lines": 1})):
            code, out = self.check("review", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("evidence lines", out)
        raw = self.impl / "raw/orphan.log"
        raw.parent.mkdir(exist_ok=True)
        raw.write_text("unindexed", encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("not manifest-indexed by any generation", out)
        raw.unlink()
        (self.impl / "large.bin").write_bytes(b"x" * (1024 * 1024 + 1))
        with self.config_patch(lambda c: c["artifact-budget"].update({"impl-dir-max-mb": 1})):
            code, out = self.check("review", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("impl directory bytes", out)

    def test_raw_external_symlink_is_rejected(self):
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        raw = self.impl / document["records"][0]["raw_log"]["path"]
        external = self.root / "outside.log"
        external.write_bytes(raw.read_bytes())
        raw.unlink()
        os.symlink(external, raw)
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("raw log", out)

    def test_worktree_dev_broken_wrong_target_and_correct_link(self):
        worktree = self.root / ".worktrees/issue-001"
        sh(self.root, "git", "worktree", "add", "--detach", str(worktree), self.head)
        os.symlink(self.root / "missing-dev", worktree / "dev")
        code, out = self.check("review", repo=worktree)
        self.assertEqual(code, 1)
        self.assertIn("broken", out)
        (worktree / "dev").unlink()
        os.symlink(self.root / "tests", worktree / "dev")
        code, out = self.check("review", repo=worktree)
        self.assertEqual(code, 1)
        self.assertIn("wrong target", out)
        (worktree / "dev").unlink()
        os.symlink(self.root / "dev", worktree / "dev")
        self.run_verify("implementer", repo=worktree)
        code, out = self.check("review", repo=worktree)
        self.assertEqual(code, 0, out)
        sh(self.root, "git", "worktree", "remove", "--force", str(worktree))

    def test_conformance_header_selection_and_real_pointers_fail_closed(self):
        original = self.conformance.read_text(encoding="utf-8")
        cases = [
            (self.context["gate2_context_digest"], "0" * 64, "context_digest"),
            (self.head, self.head[:7], "commit/tree"),
            ("selected: true", "selected: false", "selection.selected"),
            ("src/a.py:f", "src/missing.py:f", "absent at HEAD"),
            ("verification-implementer.json#DoD-1", "verification-implementer.json#DoD-99", "fragment is absent"),
            ("checker_session: main-session-1\n", "", "checker_session"),
        ]
        for old, new, expected in cases:
            with self.subTest(expected=expected):
                self.conformance.write_text(original.replace(old, new), encoding="utf-8")
                code, out = self.check("close")
                self.assertEqual(code, 1)
                self.assertIn(expected, out)
        self.conformance.write_text(original, encoding="utf-8")

    def test_empty_conform_and_missing_critical_sample_fail(self):
        self.write_conformance(empty=True)
        code, out = self.check("close")
        self.assertEqual(code, 1)
        self.assertIn("empty requirement-point table", out)
        self.write_conformance(sample=False)
        code, out = self.check("close")
        self.assertEqual(code, 1)
        self.assertIn("critical matrix row", out)

    def test_sampled_skip_uses_ticket_context_selection(self):
        self.write_conformance(verdict="skipped", selected=False, rate="1/3",
                               algorithm="sha256-v1")
        with self.config_patch(lambda c: c.update({"conformance": {"mode": "sampled", "sample-rate": "1/3", "checker": "main-session"}})), \
                mock.patch.object(tc, "select_conformance", return_value=False) as select:
            code, out = self.check("close", direct=False)
        self.assertEqual(code, 0, out)
        select.assert_called_once()
        self.write_conformance(verdict="skipped", selected=True, rate="1/3",
                               algorithm="sha256-v1")
        with self.config_patch(lambda c: c.update({"conformance": {"mode": "sampled", "sample-rate": "1/3", "checker": "main-session"}})), \
                mock.patch.object(tc, "select_conformance", return_value=True):
            code, out = self.check("close", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("select_conformance", out)

    def test_integration_skip_and_missing_feature_coverage_fail(self):
        self.successor.unlink()
        self.write_conformance(verdict="skipped")
        with self.config_patch(lambda c: c.update({"conformance": {"mode": "sampled", "sample-rate": "1/3", "checker": "main-session"}})), \
                mock.patch.object(tc, "select_conformance", return_value=False):
            code, out = self.check("close", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("integration", out)
        self.write_conformance(feature=False)
        code, out = self.check("close")
        self.assertEqual(code, 1)
        self.assertIn("feature-level coverage", out)

    def test_multi_leaf_skips_duplicate_feature_coverage_but_keeps_ticket_gate(self):
        self.successor.write_text(SUCCESSOR.replace("blocked-by: [issue-001]", "blocked-by: []"), encoding="utf-8")
        self.write_conformance(feature=False)
        code, out = self.check("close")
        self.assertEqual(code, 0, out)
        self.write_conformance(feature=False, empty=True)
        code, out = self.check("close")
        self.assertEqual(code, 1)
        self.assertIn("empty requirement-point table", out)
        self.write_conformance(verdict="skipped", feature=False)
        code, out = self.check("close")
        self.assertEqual(code, 1)
        self.assertIn("integration", out)

    def test_single_leaf_still_requires_ticket_feature_coverage(self):
        self.successor.unlink()
        self.write_conformance(feature=False)
        code, out = self.check("close")
        self.assertEqual(code, 1)
        self.assertIn("feature-level coverage", out)

    def mark_bug_ticket(self):
        self.issue.write_text(ISSUE.replace("type: feature", "type: bug").replace(
            "declared-scope: [src/**, tests/**]", "declared-scope: [src/**, tests/**, docs/**, generated/**]"),
            encoding="utf-8")
        self.context = tc.create_context(self.issue, self.root, self.context_path)

    def test_bug_markdown_section_is_not_structured_revert_proof(self):
        self.mark_bug_ticket()
        self.run_verify("implementer")
        evidence = self.impl / "evidence-issue-001.md"
        evidence.write_text(evidence.read_text() + "\n## 回归有效性\nrevert 后见红，恢复后见绿\n", encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1)
        self.assertIn("structured bug-regression.json", out)

    def test_bug_structured_revert_red_restore_green_binds_both_runners(self):
        self.mark_bug_ticket()
        current = (self.root / "src/a.py").read_text(encoding="utf-8")
        test_command = ('python3 -I -c "from pathlib import Path; '
                        'assert Path(\'src/a.py\').read_text() == %r"' % current)
        revert = ('python3 -I -c "from pathlib import Path; '
                  'Path(\'src/a.py\').write_text(%r)"' % "a = 1\n")
        restore = ('python3 -I -c "from pathlib import Path; '
                   'Path(\'src/a.py\').write_text(%r)"' % current)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = vi.main([
                "bug-regression", "implementer", "--issue", str(self.issue), "--repo", str(self.root),
                "--command", test_command, "--expected-output", "AssertionError",
                "--test-path", "tests/test_dod.py", "--fix-path", "src/a.py",
                "--revert-command", revert, "--restore-command", restore,
                "--host", "zcode", "--agent", "development", "--session", "impl-session",
                "--model", "provider/dev", "--effort", "medium",
            ])
        self.assertEqual(code, 0)
        self.run_verify("implementer")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        # Ticket phase binds bug proof to the implementer only; supervisor
        # binding and receipt closure remain feature-level diagnostics.
        code, out = self.check("ticket")
        self.assertEqual(code, 0, out)
        self.run_verify("supervisor")
        self.assertEqual(self.make_receipt(), [])
        code, out = self.check("merge")
        self.assertEqual(code, 0, out)

    def test_active_human_decision_blocks_close(self):
        self.human.active_for_issue = lambda repo, feature, issue: [{"id": "D-1"}]
        code, out = self.check("close", direct=False)
        self.assertEqual(code, 1)
        self.assertIn("active human decision", out)


if __name__ == "__main__":
    unittest.main()
