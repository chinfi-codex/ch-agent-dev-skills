#!/usr/bin/env python3
"""Real-command contract tests for verify_issue.py v2."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json_schema
import ticket_context as tc
import verify_issue as vi


SCRIPT = Path(__file__).resolve().parent.parent / "verify_issue.py"

CONFIG = """```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'python3 -I tests/test_fast.py'}
host-budget: {total: 2, max-runs: 1, queue-timeout: 30}
verify-policy:
  ticket-max-files: 10
  ticket-max-loc: 400
  deny: [protocols/**]
  test-change: [tests/**]
  generated-refresh: []
  base-freeze: true
  attribution: quarantine
  gate-scope: {categories-file: null, non-gating: [perf, display], env-skip: true}
feature-closeout:
  mode: cooperative-ci
  ci: {provider: gitea, base-url: 'https://git.example.test', repository: org/repo, workflow: ci.yml, artifact: verdict.json, token-env: CI_TOKEN}
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
enforcement: {review-trust: operator-attested}
```
"""

ISSUE = """---
feature-slug: demo
issue-id: issue-001
type: feature
status: in-progress
created: 2026-11-14
target-branch:
declared-scope: [src/**, tests/**]
---
# Demo

## 验收标准（DoD，可自动判定）
- [ ] `python3 -I tests/test_dod.py` → 退出码 `0`；期望输出 `dod-ok`

## 备注
上游：tech-spec `demo-tech-spec-2026-11-14.md`，相关节 `#3`。
"""

ISSUE_CANDIDATE = """---
feature-slug: demo
issue-id: {issue_id}
type: feature
status: done
created: 2026-11-14
target-branch:
declared-scope: [src/**, tests/**]
---
# Demo {issue_id}

## 验收标准（DoD，可自动判定）
- [ ] `{command}` → 退出码 `0`；期望输出 `{expected}`

## 备注
上游：tech-spec `demo-tech-spec-2026-11-14.md`，相关节 `#3`。
"""


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ArchiveHistoryTest(unittest.TestCase):
    def test_repeated_rehome_preserves_legacy_bytes_and_bounds_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "raw/legacy/history/" + "x" * 225 + ".log"
            path = root / rel
            path.parent.mkdir(parents=True)
            path.write_bytes(b"legacy evidence")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            doc = {"records": [{"runner": "implementer", "raw_log": {"path": rel, "sha256": sha}}]}
            for i in range(12):
                doc = vi._rehome_document(doc, root, "rerun-%02d" % i)
                raw = doc["records"][0]["raw_log"]
                self.assertEqual(Path(raw["path"]).name, sha + ".log")
                self.assertEqual(raw["sha256"], sha)
                self.assertEqual((root / raw["path"]).read_bytes(), path.read_bytes())
            (root / raw["path"]).write_bytes(b"changed")
            with self.assertRaises(vi.VerificationError):
                vi._rehome_document(doc, root, "next")


class VerifyIssueTest(unittest.TestCase):
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
        self.config.write_text(CONFIG, encoding="utf-8")
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
        self.context_path = self.impl / "ticket-context.json"
        self.context = tc.create_context(self.issue, self.root, self.context_path)
        self.head = sh(self.root, "git", "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = vi.main(list(args))
        return code, output.getvalue()

    def identity(self, runner="implementer"):
        model = "provider/dev" if runner == "implementer" else "provider/supervisor"
        effort = "medium" if runner == "implementer" else "high"
        return ("--host", "zcode", "--agent", "development" if runner == "implementer" else "supervisor",
                "--session", runner + "-session", "--model", model, "--effort", effort)

    def run_ticket(self, runner="implementer", timeout="2"):
        return self.call("run", runner, "--issue", str(self.issue), "--repo", str(self.root),
                         "--timeout", timeout, *self.identity(runner))

    def commit_test_dod(self, text):
        (self.root / "tests/test_dod.py").write_text(text, encoding="utf-8")
        sh(self.root, "git", "add", "tests/test_dod.py")
        sh(self.root, "git", "commit", "-qm", "update dod runner")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")

    def test_real_dod_fast_run_and_deterministic_evidence_manifest(self):
        for runner in ("implementer", "supervisor"):
            with self.subTest(runner=runner):
                code, out = self.run_ticket(runner)
                self.assertEqual(code, 0, out)
                document = json.loads((self.impl / ("verification-%s.json" % runner)).read_text())
                self.assertTrue(document["pass"])
                self.assertEqual([r["id"] for r in document["records"]], ["DoD-1", "fast"])
                self.assertEqual(document["records"][0]["expected_exit"], 0)
                self.assertEqual(document["records"][0]["expected_output"], "dod-ok")
                self.assertTrue(all(r["started"] and r["actual_exit"] == 0 for r in document["records"]))
                self.assertEqual(document["head"], self.head)
                self.assertEqual(document["tree"], sh(self.root, "git", "rev-parse", "HEAD^{tree}"))
                for record in document["records"]:
                    raw = self.impl / record["raw_log"]["path"]
                    self.assertTrue(raw.is_file())
                    self.assertEqual(len(record["raw_log"]["sha256"]), 64)
                    self.assertTrue(raw.stat().st_size > 0)
        evidence = self.impl / "evidence-issue-001.md"
        implementer_doc = json.loads((self.impl / "verification-implementer.json").read_text())
        supervisor_doc = json.loads((self.impl / "verification-supervisor.json").read_text())
        self.assertNotEqual(vi.generation_of(implementer_doc), vi.generation_of(supervisor_doc))
        manifest_path = self.impl / supervisor_doc["manifest"]
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["status"], "current")
        self.assertEqual(manifest["supersedes"], vi.generation_of(implementer_doc))
        indexed = {entry["path"] for entry in manifest["artifacts"]}
        self.assertIn("verification-supervisor.json", indexed)
        self.assertIn("evidence-issue-001.md", indexed)
        self.assertTrue(any(path.startswith("supervisor/") for path in indexed))
        previous = json.loads((self.impl / implementer_doc["manifest"]).read_text())
        self.assertEqual(previous["status"], "superseded")
        self.assertTrue(evidence.is_file())

    def test_direct_isolated_cli_run(self):
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT), "run", "implementer",
            "--issue", str(self.issue), "--repo", str(self.root), *self.identity(),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        self.assertTrue(document["pass"])
        self.assertEqual(len(document["records"]), 2)

    def test_expected_nonzero_exit_does_not_require_output_or_zero_text(self):
        self.commit_test_dod("import sys\nsys.exit(3)\n")
        self.issue.write_text(self.issue.read_text().replace(
            "`python3 -I tests/test_dod.py` → 退出码 `0`；期望输出 `dod-ok`",
            "`python3 -I tests/test_dod.py` → 退出码 `3`"), encoding="utf-8")
        self.context = tc.create_context(self.issue, self.root, self.context_path)
        code, out = self.run_ticket()
        self.assertEqual(code, 0, out)
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        record = document["records"][0]
        self.assertEqual(record["expected_exit"], 3)
        self.assertEqual(record["actual_exit"], 3)
        self.assertEqual(record["expected_output"], "")
        self.assertNotIn("0", (self.impl / record["raw_log"]["path"]).read_text())

    def test_frozen_dod_fast_commands_and_unbound_retries_are_rejected(self):
        self.assertEqual(self.run_ticket()[0], 0)
        path = self.impl / "verification-implementer.json"
        original = json.loads(path.read_text())
        mutations = []
        def mutation(name, apply, expected):
            value = copy.deepcopy(original)
            apply(value)
            mutations.append((name, value, expected))
        mutation("dod-command", lambda d: d["records"][0].update(command="python3 -I -c 'print(1)'"), "command differs")
        mutation("fast-command", lambda d: d["records"][1].update(command="python3 -I -c 'print(1)'"), "command differs")
        mutation("cwd", lambda d: d["records"][0].update(cwd="/"), "cwd differs")
        mutation("expected", lambda d: d["records"][0].update(expected_output="forged"), "expected_output differs")
        mutation("gating", lambda d: d["records"][0].update(gating=False), "removed from gating")
        mutation("extra", lambda d: d["records"].append(copy.deepcopy(d["records"][1])), "extra gating")
        def replacement(value):
            retry = copy.deepcopy(value["records"][0])
            retry["id"] = "DoD-1:load-flake-retry"
            retry["retry_of"] = "DoD-1"
            retry["attempt"] = 2
            value["records"].insert(1, retry)
        mutation("replacement", replacement, "replacement retry|original timed-out")
        for name, document, expected in mutations:
            with self.subTest(name=name):
                errors = vi.validate_verification(document, path, self.root)
                self.assertTrue(errors, name)
                self.assertRegex("\n".join(errors), expected)

    def test_forged_raw_log_and_updated_hash_cannot_rewrite_observed_output(self):
        self.assertEqual(self.run_ticket()[0], 0)
        path = self.impl / "verification-implementer.json"
        document = json.loads(path.read_text())
        record = document["records"][0]
        raw = self.impl / record["raw_log"]["path"]
        raw.write_text("dod-ok\nforged-extra\n", encoding="utf-8")
        errors = vi.validate_verification(document, path, self.root)
        self.assertTrue(any("hash mismatch" in error for error in errors), errors)
        import hashlib
        record["raw_log"]["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
        errors = vi.validate_verification(document, path, self.root)
        # Extra raw text alone is not a failure when required output is present;
        # forging the absence of required output must still fail even with a new hash.
        raw.write_text("forged\n", encoding="utf-8")
        record["raw_log"]["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
        errors = vi.validate_verification(document, path, self.root)
        self.assertTrue(any("raw log" in error or "output" in error for error in errors), errors)

    def test_successive_candidates_form_gc_chain_and_preserve_current_and_last_success(self):
        import artifact_gc as gc
        generations = []
        for candidate in range(3):
            if candidate:
                (self.root / "src/a.py").write_text("a = %d\n" % (candidate + 2), encoding="utf-8")
                sh(self.root, "git", "commit", "-qam", "candidate-%d" % candidate)
            code, out = self.run_ticket()
            self.assertEqual(code, 0, out)
            document = json.loads((self.impl / "verification-implementer.json").read_text())
            generations.append(vi.generation_of(document))
        g1, g2, g3 = generations
        first = json.loads((self.impl / "raw" / g1 / gc.MANIFEST_NAME).read_text())
        second = json.loads((self.impl / "raw" / g2 / gc.MANIFEST_NAME).read_text())
        third = json.loads((self.impl / "raw" / g3 / gc.MANIFEST_NAME).read_text())
        self.assertEqual(first["status"], "superseded")
        self.assertEqual(second["status"], "superseded")
        self.assertEqual(second["supersedes"], g1)
        self.assertEqual(third["status"], "current")
        self.assertEqual(third["supersedes"], g2)
        plan = gc.preview(self.impl)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual({item["generation"] for item in plan["candidates"]}, {g1})
        result = gc.apply_gc(gc.authorize_gc(plan))
        self.assertTrue(result["ok"], result)
        tombstone = gc.load_manifest(self.impl / "raw" / g1 / gc.MANIFEST_NAME)
        self.assertEqual(tombstone["artifacts"], [])
        for generation in (g2, g3):
            manifest_path = self.impl / "raw" / generation / gc.MANIFEST_NAME
            retained = gc.load_manifest(manifest_path, verify=True)
            self.assertTrue(retained["artifacts"])
            self.assertTrue(any(entry["kind"] == "raw" for entry in retained["artifacts"]))
        self.assertEqual(third["generation"], g3)

    def test_output_mismatch_fails_even_with_zero_exit(self):
        self.commit_test_dod('print("not-the-expected-output")\n')
        code, out = self.run_ticket()
        self.assertEqual(code, 1, out)
        record = json.loads((self.impl / "verification-implementer.json").read_text())["records"][0]
        self.assertEqual(record["actual_exit"], 0)
        self.assertFalse(record["output_matched"])
        self.assertFalse(record["pass"])

    def test_dirty_tracked_tree_prevents_command_start(self):
        path = self.root / "src/a.py"
        path.write_text("a = 3\n", encoding="utf-8")
        code, out = self.run_ticket()
        self.assertEqual(code, 1, out)
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        self.assertFalse(document["before"]["tracked_clean"])
        self.assertTrue(all(not record["started"] for record in document["records"]))

    def test_unignored_untracked_source_prevents_start(self):
        (self.root / "src/untracked.py").write_text("x = 1\n", encoding="utf-8")
        code, _ = self.run_ticket()
        self.assertEqual(code, 1)
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        self.assertIn("src/untracked.py", document["before"]["untracked_source"])

    def test_head_change_during_command_fails(self):
        script = (
            'from pathlib import Path; import subprocess; '
            'Path("src/a.py").write_text("a = 9\\n"); '
            'subprocess.run(["git", "add", "src/a.py"], check=True); '
            'subprocess.run(["git", "commit", "-qm", "during-verify"], check=True); '
            'print("dod-ok")\n'
        )
        self.commit_test_dod(script)
        code, out = self.run_ticket()
        self.assertEqual(code, 1, out)
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        self.assertNotEqual(document["before"]["head"], document["after"]["head"])
        self.assertFalse(document["pass"])

    def test_timeout_is_failure_not_pass(self):
        self.commit_test_dod('import time; time.sleep(1); print("dod-ok")\n')
        code, out = self.run_ticket(timeout="0.05")
        self.assertEqual(code, 1, out)
        record = json.loads((self.impl / "verification-implementer.json").read_text())["records"][0]
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["actual_exit"], 124)

    def test_red_requires_real_assertion_failure_and_maps_test(self):
        new_test = self.root / "tests/test_new.py"
        new_test.write_text("assert False\n", encoding="utf-8")
        sh(self.root, "git", "add", "tests/test_new.py")
        sh(self.root, "git", "commit", "-qm", "add new test")
        args = ("red", "--issue", str(self.issue), "--repo", str(self.root), "--id", "red-new",
                "--command", 'python3 -I -c "raise AssertionError(\'first-red\')"',
                "--expected-output", "first-red", "--test-path", "tests/test_new.py",
                *self.identity())
        code, out = self.call(*args)
        self.assertEqual(code, 0, out)
        document = json.loads((self.impl / "verification-red.json").read_text())
        record = document["red_records"][0]
        self.assertTrue(record["pass"])
        self.assertNotEqual(record["actual_exit"], 0)
        self.assertEqual(record["test_paths"], ["tests/test_new.py"])

        bad = ("red", "--issue", str(self.issue), "--repo", str(self.root), "--id", "red-import",
               "--command", 'python3 -I -c "raise ImportError(\'not-an-assertion\')"',
               "--test-path", "tests/test_new.py", *self.identity())
        self.assertEqual(self.call(*bad)[0], 1)

    def test_bug_regression_revert_red_restore_green_in_disposable_worktree(self):
        self.issue.write_text(ISSUE.replace("type: feature", "type: bug"), encoding="utf-8")
        sh(self.root, "git", "add", str(self.issue.relative_to(self.root)))
        sh(self.root, "git", "commit", "-qm", "mark bug ticket")
        self.context = tc.create_context(self.issue, self.root, self.context_path)
        original = (self.root / "src/a.py").read_bytes()
        args = (
            "bug-regression", "implementer", "--issue", str(self.issue), "--repo", str(self.root),
            "--command", 'python3 -I -c "from pathlib import Path; assert Path(\'src/a.py\').read_text() == \'a = 2\\n\'"',
            "--expected-output", "AssertionError", "--test-path", "tests/test_dod.py",
            "--fix-path", "src/a.py",
            "--revert-command", 'python3 -I -c "from pathlib import Path; Path(\'src/a.py\').write_text(\'a = 1\\n\')"',
            "--restore-command", 'python3 -I -c "from pathlib import Path; Path(\'src/a.py\').write_text(\'a = 2\\n\')"',
            *self.identity(),
        )
        code, out = self.call(*args)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.root / "src/a.py").read_bytes(), original)
        document = json.loads((self.impl / "bug-regression.json").read_text())
        self.assertTrue(document["bug_regression"]["pass"])
        self.assertNotEqual(document["bug_regression"]["red"]["actual_exit"], 0)
        self.assertEqual(document["bug_regression"]["green"]["actual_exit"], 0)
        self.assertEqual(sh(self.root, "git", "rev-parse", "HEAD"), document["head"])

    def write_categories(self, text):
        self.config.write_text(CONFIG.replace("categories-file: null", "categories-file: dev/test-categories.yaml"),
                               encoding="utf-8")
        (self.root / "dev/test-categories.yaml").write_text(text, encoding="utf-8")

    def test_non_gating_failure_and_env_skip_have_explicit_records(self):
        self.write_categories(
            "categories:\n  DoD-1: {categories: [perf]}\n  fast: {requires-env: [VERIFY_MISSING_ENV]}\n")
        self.commit_test_dod('raise RuntimeError("perf-noise")\n')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VERIFY_MISSING_ENV", None)
            code, out = self.run_ticket()
        self.assertEqual(code, 0, out)
        path = self.impl / "verification-implementer.json"
        document = json.loads(path.read_text())
        self.assertEqual(document["records"][0]["status"], "non-gating")
        self.assertFalse(document["records"][0]["pass"])
        self.assertEqual(document["records"][1]["status"], "env-skip")
        self.assertFalse(document["records"][1]["started"])
        self.assertEqual(document["records"][1]["skip_proof"]["items"], ["VERIFY_MISSING_ENV"])
        self.assertEqual(vi.validate_verification(document, path, self.root), [])

    def test_quarantine_requires_config_and_raw_skip_proof(self):
        self.write_categories("categories:\n  DoD-1: {quarantine: [known-test]}\n")
        (self.root / "dev/known-failures.yaml").write_text(
            "failures:\n  - name: known-test\n    returncode: 1\n    signature: known failure\n"
            "    owner: test-owner\n    destination: issue-999\n    first-seen: 2026-11-01\n",
            encoding="utf-8")
        self.commit_test_dod('print("dod-ok"); print("QUARANTINED SKIP known-test")\n')
        code, out = self.run_ticket()
        self.assertEqual(code, 0, out)
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        self.assertEqual(document["records"][0]["status"], "quarantined")
        self.commit_test_dod('print("dod-ok")\n')
        code, out = self.run_ticket()
        self.assertEqual(code, 1, out)
        document = json.loads((self.impl / "verification-implementer.json").read_text())
        self.assertFalse(document["records"][0]["pass"])
        self.assertEqual(document["records"][0]["status"], "fail")

    def test_load_flake_timeout_serial_retry_passes_with_two_raw_logs(self):
        fd, marker_name = tempfile.mkstemp(prefix="verify-load-flake-")
        os.close(fd)
        marker = Path(marker_name)
        marker.unlink()
        script = (
            'from pathlib import Path; import os, time; '
            'marker = Path(os.environ["FLAKE_MARKER"]); '
            'first = not marker.exists(); '
            'marker.write_text("ran"); '
            'time.sleep(1) if first else None; '
            'print("dod-ok")\n'
        )
        self.commit_test_dod(script)
        try:
            with mock.patch.dict(os.environ, {"FLAKE_MARKER": str(marker)}), \
                    mock.patch.object(vi.os, "getloadavg", return_value=(999.0, 999.0, 999.0)):
                code, out = self.run_ticket(timeout="0.3")
            self.assertEqual(code, 0, out)
            path = self.impl / "verification-implementer.json"
            document = json.loads(path.read_text())
            self.assertEqual(document["records"][0]["status"], "load-flake")
            self.assertEqual(document["records"][1]["retry_of"], "DoD-1")
            self.assertTrue(document["records"][1]["pass"])
            self.assertEqual(vi.validate_verification(document, path, self.root), [])
            self.assertNotEqual(document["records"][0]["raw_log"]["path"],
                                document["records"][1]["raw_log"]["path"])
        finally:
            marker.unlink(missing_ok=True)

    def test_missing_fast_and_scoped_full_fail_closed(self):
        self.config.write_text(CONFIG.replace("quality-gate: {fast: 'python3 -I tests/test_fast.py'}",
                                               "quality-gate: {fast: ''}"), encoding="utf-8")
        self.assertNotEqual(self.run_ticket()[0], 0)
        self.config.write_text(CONFIG.replace("python3 -I tests/test_fast.py", "python3 -I tests/test_fast.py full"),
                               encoding="utf-8")
        self.assertNotEqual(self.run_ticket()[0], 0)


class CandidateVerificationTest(unittest.TestCase):
    """Candidate-wide re-runs read frozen ticket contexts but execute in integration."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "test@example.com")
        sh(self.root, "git", "config", "user.name", "Test")
        self.feature = self.root / "dev/features/m/demo"
        (self.feature / "issues").mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src/a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "tests/test_dod.py").write_text('print("dod-ok")\n', encoding="utf-8")
        (self.root / "tests/test_dod2.py").write_text('print("dod2-ok")\n', encoding="utf-8")
        (self.root / "tests/test_fast.py").write_text('print("fast-ok")\n', encoding="utf-8")
        (self.root / "dev/agents-config.md").write_text(CONFIG, encoding="utf-8")
        (self.feature / "demo-tech-spec-2026-11-14.md").write_text("# Spec\n\n## 3. Behavior\n",
                                                                  encoding="utf-8")
        self.issue1 = self.write_issue("issue-001", "python3 -I tests/test_dod.py", "dod-ok")
        self.issue2 = self.write_issue("issue-002", "python3 -I tests/test_dod2.py", "dod2-ok")
        sh(self.root, "git", "add", "src", "tests")
        sh(self.root, "git", "commit", "-qm", "base")
        self.ticket_base = sh(self.root, "git", "rev-parse", "HEAD")
        self.contexts = {}
        for issue, issue_id in ((self.issue1, "issue-001"), (self.issue2, "issue-002")):
            self.contexts[issue_id] = tc.create_context(
                issue, self.root, self.feature / "impl" / issue_id / "ticket-context.json")
        (self.root / "docs.txt").write_text("main advance\n", encoding="utf-8")
        sh(self.root, "git", "add", "docs.txt")
        sh(self.root, "git", "commit", "-qm", "advance main")
        self.main_head = sh(self.root, "git", "rev-parse", "HEAD")
        self.wt = self.root / ".worktrees/integration-demo"
        self.wt.parent.mkdir()
        sh(self.root, "git", "worktree", "add", "-q", "-b", "feat/demo", str(self.wt), "main")
        (self.wt / "src/a.py").write_text("a = 2\n", encoding="utf-8")
        sh(self.wt, "git", "add", "src/a.py")
        sh(self.wt, "git", "commit", "-qm", "issue-001")
        self.merge1 = sh(self.wt, "git", "rev-parse", "HEAD")
        (self.wt / "src/b.py").write_text("b = 1\n", encoding="utf-8")
        sh(self.wt, "git", "add", "src/b.py")
        sh(self.wt, "git", "commit", "-qm", "issue-002")
        self.candidate = sh(self.wt, "git", "rev-parse", "HEAD")
        self.candidate_tree = sh(self.wt, "git", "rev-parse", "HEAD^{tree}")
        self.integration = self.feature / "integration"
        self.integration.mkdir()
        self.candidate_path = self.integration / "candidate.json"
        self.write_candidate()
        self.implementer_path = self.feature / "impl/issue-001/verification-implementer.json"
        self.implementer = b'{"ticket":"implementer"}\n'
        self.implementer_path.write_bytes(self.implementer)
        self.implementer_manifest = (self.feature / "impl/issue-001/raw/ticket-generation"
                                     / vi.MANIFEST_NAME)
        self.implementer_manifest.parent.mkdir(parents=True)
        self.implementer_manifest_bytes = b'{"ticket":"manifest"}\n'
        self.implementer_manifest.write_bytes(self.implementer_manifest_bytes)

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, issue_id, command, expected):
        path = self.feature / "issues" / ("%s-demo.md" % issue_id)
        path.write_text(ISSUE_CANDIDATE.format(issue_id=issue_id, command=command, expected=expected),
                        encoding="utf-8")
        return path

    def candidate_document(self, **overrides):
        value = {
            "schema": "agent-dev.feature-candidate/v2",
            "feature": "m/demo", "round": None, "mode": "cooperative-ci",
            "integration_branch": "feat/demo", "candidate_ref": "refs/heads/feat/demo",
            "main_branch": "main", "main_head": self.main_head, "merge_base": self.main_head,
            "candidate": self.candidate, "candidate_tree": self.candidate_tree,
            "frozen_at": "2026-11-15T00:00:00Z",
            "issues": [
                {"id": "issue-001", "status": "done", "merged_commit": self.merge1,
                 "conformance": "conform", "conformance_context": {}},
                {"id": "issue-002", "status": "done", "merged_commit": self.candidate,
                 "conformance": "conform", "conformance_context": {}},
            ],
            "acceptance_plan": {"path": "acceptance-plan.yaml", "sha256": "a" * 64,
                                "cases": ["AC-1"]},
        }
        value.update(overrides)
        return value

    def write_candidate(self, **overrides):
        value = self.candidate_document(**overrides)
        self.candidate_path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = vi.main(list(args))
        return code, output.getvalue()

    def identity(self):
        return ("--host", "zcode", "--agent", "supervisor", "--session", "candidate-session",
                "--model", "provider/supervisor", "--effort", "high")

    def call_candidate(self, *extra, output_dir=None):
        args = ["run-candidate", "--candidate", str(self.candidate_path), "--repo", str(self.wt),
                "--timeout", "5", *self.identity()]
        if output_dir is not None:
            args.extend(("--output-dir", str(output_dir)))
        args.extend(extra)
        return self.call(*args)

    def aggregate(self):
        return json.loads((self.integration / "candidate-verification.json").read_text())

    def test_two_tickets_really_run_at_candidate_head_and_aggregate(self):
        code, out = self.call_candidate()
        self.assertEqual(code, 0, out)
        aggregate = self.aggregate()
        self.assertEqual(aggregate["schema"], "agent-dev.candidate-verification/v1")
        self.assertEqual(aggregate["candidate"], self.candidate)
        self.assertEqual(aggregate["candidate_tree"], self.candidate_tree)
        self.assertEqual(aggregate["main_head"], self.main_head)
        self.assertEqual(aggregate["plan_sha256"], "a" * 64)
        self.assertTrue(aggregate["all-passed"])
        self.assertTrue(aggregate["generated"])
        self.assertEqual([item["issue"] for item in aggregate["issues"]],
                         ["issue-001", "issue-002"])
        self.assertEqual(json_schema.load_validate(vi.CANDIDATE_SCHEMA_PATH, aggregate), [])
        self.assertNotEqual(self.ticket_base, self.main_head)
        for entry in aggregate["issues"]:
            with self.subTest(issue=entry["issue"]):
                self.assertTrue(entry["pass"])
                self.assertRegex(entry["verification_sha256"], r"^[0-9a-f]{64}$")
                path = (self.integration / entry["verification_path"]).resolve()
                self.assertTrue(path.is_file())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 entry["verification_sha256"])
                document = json.loads(path.read_text())
                self.assertEqual(document["issue"], entry["issue"])
                self.assertEqual(document["runner"], "supervisor")
                self.assertTrue(document["pass"])
                self.assertEqual(document["head"], self.candidate)
                self.assertEqual(document["tree"], self.candidate_tree)
                self.assertEqual(document["base"], self.ticket_base)
                self.assertEqual([record["id"] for record in document["records"]],
                                 ["DoD-1", "fast"])
                self.assertTrue(all(record["started"] and record["actual_exit"] == 0
                                    for record in document["records"]))
                for record in document["records"]:
                    raw = path.parent / record["raw_log"]["path"]
                    self.assertTrue(raw.is_file())
                    self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),
                                     record["raw_log"]["sha256"])
        self.assertEqual(self.implementer_path.read_bytes(), self.implementer)
        self.assertEqual(self.implementer_manifest.read_bytes(), self.implementer_manifest_bytes)
        self.assertFalse((self.feature / "impl/issue-001/verification-supervisor.json").exists())

    def test_one_ticket_output_mismatch_fails_aggregate(self):
        (self.wt / "tests/test_dod2.py").write_text('print("broken-output")\n', encoding="utf-8")
        sh(self.wt, "git", "add", "tests/test_dod2.py")
        sh(self.wt, "git", "commit", "-qm", "break issue-002")
        self.candidate = sh(self.wt, "git", "rev-parse", "HEAD")
        self.candidate_tree = sh(self.wt, "git", "rev-parse", "HEAD^{tree}")
        self.write_candidate()
        code, out = self.call_candidate()
        self.assertEqual(code, 1, out)
        aggregate = self.aggregate()
        self.assertFalse(aggregate["all-passed"])
        entries = {entry["issue"]: entry for entry in aggregate["issues"]}
        self.assertTrue(entries["issue-001"]["pass"])
        self.assertFalse(entries["issue-002"]["pass"])
        self.assertIsNotNone(entries["issue-002"]["verification_sha256"])
        document = json.loads(
            (self.integration / entries["issue-002"]["verification_path"]).read_text())
        self.assertFalse(document["pass"])
        self.assertEqual(document["records"][0]["actual_exit"], 0)
        self.assertFalse(document["records"][0]["output_matched"])

    def test_missing_ticket_context_fails_closed_but_other_tickets_still_run(self):
        (self.feature / "impl/issue-002/ticket-context.json").unlink()
        code, out = self.call_candidate()
        self.assertEqual(code, 1, out)
        aggregate = self.aggregate()
        self.assertFalse(aggregate["all-passed"])
        entries = {entry["issue"]: entry for entry in aggregate["issues"]}
        self.assertTrue(entries["issue-001"]["pass"])
        self.assertFalse(entries["issue-002"]["pass"])
        self.assertIsNone(entries["issue-002"]["verification_path"])
        self.assertIsNone(entries["issue-002"]["verification_sha256"])
        self.assertIsNone(entries["issue-002"]["head"])
        self.assertIsNone(entries["issue-002"]["tree"])

    def test_supervisor_passes_frozen_candidate_identity_to_commands(self):
        with mock.patch.object(vi, "_execute", wraps=vi._execute) as execute:
            code, output = self.call_candidate()
        self.assertEqual(code, 0, output)
        bindings = [json.loads(call.kwargs["candidate_flow"]) for call in execute.call_args_list]
        self.assertEqual({item["issue_id"] for item in bindings}, {"issue-001", "issue-002"})
        self.assertEqual({item["candidate_path"] for item in bindings}, {str(self.candidate_path.resolve())})
        self.assertEqual({item["candidate_sha256"] for item in bindings},
                         {hashlib.sha256(self.candidate_path.read_bytes()).hexdigest()})

    def test_candidate_head_or_tree_change_after_freeze_fails_before_execution(self):
        (self.wt / "src/a.py").write_text("a = 99\n", encoding="utf-8")
        sh(self.wt, "git", "add", "src/a.py")
        sh(self.wt, "git", "commit", "-qm", "advance after freeze")
        code, out = self.call_candidate()
        self.assertEqual(code, 1, out)
        aggregate = self.aggregate()
        self.assertFalse(aggregate["all-passed"])
        self.assertEqual(aggregate["candidate"], self.candidate)
        self.assertEqual(aggregate["candidate_tree"], self.candidate_tree)
        for entry in aggregate["issues"]:
            self.assertFalse(entry["pass"])
            self.assertIsNone(entry["verification_path"])
            self.assertIsNone(entry["head"])
            self.assertIsNone(entry["tree"])
        self.assertFalse((self.integration / "raw").exists())

    def test_candidate_tree_mismatch_with_unchanged_head_fails(self):
        main_tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")
        self.write_candidate(candidate_tree=main_tree)
        code, out = self.call_candidate()
        self.assertEqual(code, 1, out)
        aggregate = self.aggregate()
        self.assertFalse(aggregate["all-passed"])
        self.assertEqual(aggregate["candidate"], self.candidate)
        self.assertEqual(aggregate["candidate_tree"], main_tree)
        self.assertTrue(all(entry["pass"] is False for entry in aggregate["issues"]))

    def test_dirty_integration_worktree_fails_before_execution(self):
        (self.wt / "src/a.py").write_text("a = 3\n", encoding="utf-8")
        code, out = self.call_candidate()
        self.assertEqual(code, 1, out)
        aggregate = self.aggregate()
        self.assertFalse(aggregate["all-passed"])
        self.assertTrue(all(entry["pass"] is False and entry["verification_path"] is None
                            for entry in aggregate["issues"]))
        self.assertFalse((self.integration / "raw").exists())

    def test_repeated_candidate_runs_do_not_overwrite_ticket_level_evidence(self):
        first_code, first_out = self.call_candidate()
        self.assertEqual(first_code, 0, first_out)
        first = self.aggregate()
        first_paths = [entry["verification_path"] for entry in first["issues"]]
        self.assertTrue(first["all-passed"])
        second_code, second_out = self.call_candidate()
        self.assertEqual(second_code, 0, second_out)
        second = self.aggregate()
        second_paths = [entry["verification_path"] for entry in second["issues"]]
        self.assertTrue(second["all-passed"])
        self.assertNotEqual(first["generated"], second["generated"])
        self.assertNotEqual(first_paths, second_paths)
        for relative in first_paths + second_paths:
            self.assertTrue((self.integration / relative).is_file(), relative)
        self.assertEqual(self.implementer_path.read_bytes(), self.implementer)
        self.assertEqual(self.implementer_manifest.read_bytes(), self.implementer_manifest_bytes)
        impl_issue = self.feature / "impl/issue-001"
        self.assertEqual(sorted(path.name for path in impl_issue.iterdir()),
                         ["raw", "ticket-context.json", "verification-implementer.json"])
        self.assertFalse((impl_issue / "verification-supervisor.json").exists())
        self.assertFalse((impl_issue / "raw" / "ticket-generation" / "supervisor").exists())

    def test_frozen_context_commands_win_over_current_issue_and_config_drift_fails(self):
        # The ticket file is not an input to candidate verification: relay only
        # the commands frozen in ticket-context.json.
        self.issue1.write_text(
            ISSUE_CANDIDATE.format(issue_id="issue-001",
                                   command="python3 -I -c 'print(\"tampered\")'",
                                   expected="tampered"), encoding="utf-8")
        code, out = self.call_candidate()
        self.assertEqual(code, 0, out)
        aggregate = self.aggregate()
        entry = next(item for item in aggregate["issues"] if item["issue"] == "issue-001")
        document = json.loads((self.integration / entry["verification_path"]).read_text())
        self.assertEqual(document["records"][0]["command"], "python3 -I tests/test_dod.py")
        self.assertEqual(document["records"][0]["expected_output"], "dod-ok")
        # A config whose bytes no longer match the frozen hash must fail closed
        # instead of silently executing a new fast command.
        self.assertEqual(self.call_candidate()[0], 0)
        (self.root / "dev/agents-config.md").write_text(
            CONFIG.replace("python3 -I tests/test_fast.py",
                           "python3 -I -c 'print(\"new-fast\")'"), encoding="utf-8")
        code, out = self.call_candidate()
        self.assertEqual(code, 1, out)
        drift = self.aggregate()
        self.assertFalse(drift["all-passed"])
        self.assertTrue(all(item["pass"] is False for item in drift["issues"]))

    def test_unreadable_ticket_verification_json_fails_aggregate(self):
        output = self.integration / "raw" / "manual-generation"

        def corrupt_evidence(**kwargs):
            issue_dir = kwargs["impl_dir"]
            issue_dir.mkdir(parents=True, exist_ok=True)
            (issue_dir / "verification-supervisor.json").write_text("{not-json",
                                                                    encoding="utf-8")

        with mock.patch.object(vi, "_run_tasks", side_effect=corrupt_evidence):
            document = vi.run_candidate(str(self.candidate_path), str(self.wt),
                                        output_dir=str(output), timeout=5)
        self.assertFalse(document["all-passed"])
        for entry in document["issues"]:
            self.assertFalse(entry["pass"])
            self.assertIsNotNone(entry["verification_path"])
            self.assertIsNotNone(entry["verification_sha256"])
            self.assertIsNone(entry["head"])
            self.assertIsNone(entry["tree"])

    def test_candidate_verification_schema_rejects_malformed_aggregate(self):
        self.assertEqual(self.call_candidate()[0], 0)
        aggregate = self.aggregate()
        self.assertEqual(json_schema.load_validate(vi.CANDIDATE_SCHEMA_PATH, aggregate), [])
        forged = copy.deepcopy(aggregate)
        forged["issues"][0]["verification_sha256"] = "not-a-sha256"
        self.assertTrue(json_schema.load_validate(vi.CANDIDATE_SCHEMA_PATH, forged))
        forged = copy.deepcopy(aggregate)
        forged["all-passed"] = "true"
        self.assertTrue(json_schema.load_validate(vi.CANDIDATE_SCHEMA_PATH, forged))
        forged = copy.deepcopy(aggregate)
        del forged["issues"][0]["head"]
        self.assertTrue(json_schema.load_validate(vi.CANDIDATE_SCHEMA_PATH, forged))

    def test_direct_isolated_cli_run_candidate_supports_identity_json(self):
        identity = self.root / "identity.json"
        identity.write_text(json.dumps({
            "host": "identity-host", "agent": "identity-agent",
            "session": "identity-session", "model": "identity-model",
            "effort": "identity-effort",
        }), encoding="utf-8")
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT), "run-candidate",
            "--candidate", str(self.candidate_path), "--repo", str(self.wt),
            "--timeout", "5", "--identity-json", str(identity),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        aggregate = self.aggregate()
        self.assertTrue(aggregate["all-passed"])
        path = self.integration / aggregate["issues"][0]["verification_path"]
        document = json.loads(path.read_text())
        self.assertEqual(document["identity"]["host"], "identity-host")
        self.assertEqual(document["identity"]["agent"], "identity-agent")
        self.assertEqual(document["identity"]["session"], "identity-session")
        self.assertEqual(document["identity"]["model"], "identity-model")
        self.assertEqual(document["identity"]["effort"], "identity-effort")
        self.assertEqual(document["identity"]["role"], "supervisor")


if __name__ == "__main__":
    unittest.main()
