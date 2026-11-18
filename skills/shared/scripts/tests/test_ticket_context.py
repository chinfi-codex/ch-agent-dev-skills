#!/usr/bin/env python3
"""Temporary-Git contract tests for frozen v4 ticket contexts and DoD parsing."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ticket_context as tc


SCRIPT = Path(__file__).resolve().parent.parent / "ticket_context.py"
CONFIG = """# markdown is retained
```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'python3 -I tests/test_unit.py'}
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
  ci: {provider: gitlab, base-url: 'https://gitlab.example.com', repository: group/project, workflow: full, artifact: verdict.json, token-env: GITLAB_TOKEN}
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
```
"""
ISSUE = """---
feature-slug: demo
issue-id: issue-001
type: feature
status: confirmed
created: 2026-11-14
target-branch:
declared-scope: [src/a.py, tests/**]
---

# Demo

## 要做什么（What to build）

Implement it.

## 验收标准（DoD，可自动判定）

- [ ] `python3 -I tests/test_a.py` → 退出码 `0`；期望输出 `ok`
- [x] `python3 -I tests/test_b.py` → 退出码 `0`

## 备注

上游：tech-spec `demo-tech-spec-2026-11-14.md`，相关节 `#3`。
"""


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class TicketContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "test@example.com")
        sh(self.root, "git", "config", "user.name", "Test")
        self.feature = self.root / "dev/features/m/demo"
        self.feature.mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src/a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "tests/test_a.py").write_text("assert True\n", encoding="utf-8")
        self.spec = self.feature / "demo-tech-spec-2026-11-14.md"
        self.spec.write_text("# Spec\n\n## 3. Interface\n", encoding="utf-8")
        self.config = self.root / "dev/agents-config.md"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.issue = self.feature / "issues/issue-001-demo.md"
        self.issue.parent.mkdir()
        self.issue.write_text(ISSUE, encoding="utf-8")
        sh(self.root, "git", "add", ".")
        sh(self.root, "git", "commit", "-qm", "base")
        self.main = sh(self.root, "git", "rev-parse", "HEAD")
        sh(self.root, "git", "checkout", "-qb", "feat/demo-issue-001")
        (self.root / "src/a.py").write_text("a = 2\n", encoding="utf-8")
        sh(self.root, "git", "commit", "-qam", "implement")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_freezes_full_oids_hashes_scope_dod_and_output(self):
        output = self.feature / "impl/issue-001/ticket-context.json"
        context = tc.create_context(self.issue, self.root, output)
        self.assertEqual(context["schema"], "agent-dev.ticket-context/v4")
        self.assertEqual(context["target_branch"], "main")
        self.assertEqual(context["target_commit"], self.main)
        self.assertEqual(context["base"], self.main)
        self.assertEqual(context["source_head"], self.head)
        self.assertEqual(context["source_tree"], sh(self.root, "git", "rev-parse", "HEAD^{tree}"))
        for key in ("target_commit", "base", "source_head", "source_tree"):
            self.assertRegex(context[key], r"^[0-9a-f]{40}$")
        self.assertEqual(context["declared_scope"], ["src/a.py", "tests/**"])
        self.assertEqual(context["hashes"]["issue"], tc.issue_content_hash(self.issue))
        self.assertEqual(context["hashes"]["spec"], hashlib.sha256(self.spec.read_bytes()).hexdigest())
        self.assertEqual(context["hashes"]["config"], hashlib.sha256(self.config.read_bytes()).hexdigest())
        self.assertEqual([item["id"] for item in context["dod"]], ["DoD-1", "DoD-2"])
        self.assertEqual(context["dod"][0]["expected_exit"], 0)
        self.assertEqual(context["dod"][0]["expected_output"], "ok")
        self.assertEqual(context["dod"][1]["expected_output"], "")
        self.assertEqual(context["gate2_context_digest"], tc.context_digest(context))
        self.assertEqual(tc.load_context(output), context)
        self.assertEqual(tc.create_context(self.issue, self.root), context)

    def test_context_survives_all_real_delivery_phases_without_mocks(self):
        output = self.feature / "impl/issue-001/ticket-context.json"
        frozen = tc.create_context(self.issue, self.root, output)
        phases = ("confirmed", "start", "in-progress", "in-review", "mr-submitted", "merged", "done")
        for index, phase in enumerate(phases):
            with self.subTest(phase=phase):
                text = ISSUE.replace("status: confirmed", "status: " + phase)
                delivery = (
                    "verify-fails: %d\n" % index
                    + "dispatch: {host: codex, model: dev, attempts: %d}\n" % (index + 1)
                    + "tracker-iid: %d\ntracker-url: 'https://tracker.invalid/issues/%d'\n" % (index + 1, index + 1)
                )
                text = text.replace("created: 2026-11-14", delivery + "created: 2026-11-14")
                self.issue.write_text(text, encoding="utf-8")
                current = tc.create_context(self.issue, self.root)
                self.assertEqual(current, frozen)
                self.assertEqual(tc.load_context(output), frozen)
                self.assertEqual(tc.issue_content_hash(self.issue), frozen["hashes"]["issue"])
                self.assertEqual(current["hashes"]["dod"], frozen["hashes"]["dod"])
        changed = self.issue.read_text(encoding="utf-8").replace("python3 -I tests/test_a.py", "python3 -I tests/test_b.py")
        # Duplicate commands are rejected independently of the immutable hash.
        self.issue.write_text(changed, encoding="utf-8")
        with self.assertRaises(tc.ContextError):
            tc.create_context(self.issue, self.root)
        self.issue.write_text(text.replace("期望输出 `ok`", "期望输出 `changed`"), encoding="utf-8")
        changed_context = tc.create_context(self.issue, self.root)
        self.assertNotEqual(changed_context["hashes"]["issue"], frozen["hashes"]["issue"])
        self.assertNotEqual(changed_context["hashes"]["dod"], frozen["hashes"]["dod"])
        self.assertNotEqual(changed_context["gate2_context_digest"], frozen["gate2_context_digest"])
        self.issue.write_text(text.replace("退出码 `0`", "退出码 `1`"), encoding="utf-8")
        changed_exit = tc.create_context(self.issue, self.root)
        self.assertNotEqual(changed_exit["hashes"]["dod"], frozen["hashes"]["dod"])
        self.assertNotEqual(changed_exit["gate2_context_digest"], changed_context["gate2_context_digest"])

    def test_v2_context_is_rejected_without_automatic_migration(self):
        context = tc.create_context(self.issue, self.root)
        context["schema"] = "agent-dev.ticket-context/v2"
        context["dod"] = [{"id": "DoD-1", "command": "python3 -I tests/test_a.py", "expected": "0"}]
        path = self._write_context(context)
        with self.assertRaisesRegex(tc.ContextError, "schema|const|expected"):
            tc.load_context(path)
        self.assertEqual(json.loads(path.read_text())["schema"], "agent-dev.ticket-context/v2")

    def test_target_branch_precedence_local_human_slug_and_legacy_since(self):
        config = {"main-branch": "main", "feature-closeout": {
            "mode": "local-human", "integration-branch": "feat/<feature-slug>", "since": None}}
        fm = {"feature-slug": "demo", "created": "2026-11-14"}
        self.assertEqual(tc.target_branch(config, fm), "feat/demo")
        fm["target-branch"] = "release/ticket"
        self.assertEqual(tc.target_branch(config, fm), "release/ticket")
        del fm["target-branch"]
        del fm["feature-slug"]
        self.assertEqual(tc.target_branch(config, fm, self.feature), "feat/demo")
        config["feature-closeout"]["since"] = "2026-11-01"
        fm["created"] = "2026-10-01"
        self.assertEqual(tc.target_branch(config, fm, self.feature), "main")
        for mode in ("server-enforced", "cooperative-ci"):
            config["feature-closeout"]["mode"] = mode
            self.assertEqual(tc.target_branch(config, fm), "main")
        fm["target-branch"] = "../bad"
        with self.assertRaisesRegex(tc.ContextError, "invalid target"):
            tc.target_branch(config, fm)

    def test_dod_empty_duplicate_and_malformed_fail(self):
        good = tc.parse_issue(self.issue)
        self.assertEqual([item["command"] for item in good["dod"]],
                         ["python3 -I tests/test_a.py", "python3 -I tests/test_b.py"])
        bad_sections = (
            "## 验收标准（DoD）\n\n无\n",
            "## 验收标准（DoD）\n- [ ] plain-command\n",
            "## 验收标准（DoD）\n- [ ] ``\n",
            "## 验收标准（DoD）\n- [] `command`\n",
            "## 验收标准（DoD）\n- [ ] prefix `command`\n",
            "## 验收标准（DoD）\n- [ ] `same`\n- [x] `same`\n",
            "## 验收标准（DoD）\n- [ ] `unterminated\n",
            "## 验收标准（DoD）\n- [ ] `cmd` → 期望输出 `old`\n",
            "## 验收标准（DoD）\n- [ ] `cmd` → 退出码 `zero`\n",
            "## 验收标准（DoD）\n- [ ] `cmd` → 期望输出 `x`；退出码 `0`\n",
            "## 验收标准（DoD）\n- [ ] `cmd` → 退出码 `0`；退出码 `1`\n",
            "## 验收标准（DoD）\n- [ ] `cmd` → 退出码 `0`；期望输出 ``\n",
        )
        for section in bad_sections:
            with self.subTest(section=section):
                path = self.feature / "issues/bad.md"
                path.write_text("---\nissue-id: issue-002\n---\n" + section, encoding="utf-8")
                with self.assertRaises(tc.ContextError):
                    tc.parse_issue(path)

    def test_deterministic_fraction_sampling_and_tamper_detection(self):
        context = tc.create_context(self.issue, self.root)
        material = "%s:%s" % (context["gate2_context_digest"], context["issue_id"])
        position = int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest(), "big")
        expected = Fraction(position, 1 << 256) < Fraction(1, 3)
        self.assertEqual(tc.select_conformance(context, "1/3"), expected)
        self.assertEqual(tc.select_conformance(copy.deepcopy(context), Fraction(1, 3)), expected)
        self.assertTrue(tc.select_conformance(context, 1))
        for rate in (0, -1, "2", "1/0", "bad", True, None):
            with self.subTest(rate=rate), self.assertRaises(tc.ContextError):
                tc.select_conformance(context, rate)
        tampered = copy.deepcopy(context)
        tampered["declared_scope"].append("src/other.py")
        with self.assertRaisesRegex(tc.ContextError, "digest mismatch"):
            tc.load_context(self._write_context(tampered))
        with self.assertRaisesRegex(tc.ContextError, "tampered"):
            tc.select_conformance(tampered, "1/3")

    def test_worktree_git_oids_with_primary_dev_symlink(self):
        worktree = self.root.parent / (self.root.name + "-worktree")
        sh(self.root, "git", "worktree", "add", "-q", str(worktree), "-b", "feat/context-worktree")
        shutil.rmtree(worktree / "dev")
        (worktree / "dev").symlink_to(self.root / "dev", target_is_directory=True)
        output = self.feature / "impl/issue-001/worktree-context.json"
        context = tc.create_context(self.issue, worktree, output, primary_repo=self.root)
        self.assertEqual(context["source_head"], self.head)
        self.assertEqual(context["target_commit"], self.main)
        self.assertEqual(context["config_path"], "dev/agents-config.md")
        self.assertTrue(output.is_file())
        self.assertEqual(tc.load_context(output), context)

    def _write_context(self, context):
        path = self.feature / "impl/issue-001/test-context.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(context), encoding="utf-8")
        return path

    def test_paths_outside_repo_missing_target_and_empty_scope_fail(self):
        outside = Path(self.tmp.name).parent / "outside-ticket-context-test.md"
        outside.write_text(ISSUE, encoding="utf-8")
        try:
            with self.assertRaisesRegex(tc.ContextError, "inside repo"):
                tc.create_context(outside, self.root)
            with self.assertRaisesRegex(tc.ContextError, "inside repo"):
                tc.create_context(self.issue, self.root, outside)
        finally:
            outside.unlink()

        self.issue.write_text(ISSUE.replace("tech-spec `demo-tech-spec-2026-11-14.md`",
                                            "tech-spec `../../../../etc/passwd`"), encoding="utf-8")
        with self.assertRaisesRegex(tc.ContextError, "inside repo|cannot be resolved"):
            tc.create_context(self.issue, self.root)
        self.issue.write_text(ISSUE.replace("declared-scope: [src/a.py, tests/**]",
                                            "declared-scope: []"), encoding="utf-8")
        with self.assertRaisesRegex(tc.ContextError, "non-empty"):
            tc.create_context(self.issue, self.root)
        self.issue.write_text(ISSUE.replace("target-branch:\n", "target-branch: missing\n"), encoding="utf-8")
        with self.assertRaisesRegex(tc.ContextError, "rev-parse"):
            tc.create_context(self.issue, self.root)

    def prepare_plan(self):
        self.spec.write_text("---\nstatus: 已确认（待拆解）\n---\n# Spec\n", encoding="utf-8")
        self.issue.write_text(ISSUE.replace("declared-scope: [src/a.py, tests/**]", "declared-scope: [src/a.py]"), encoding="utf-8")
        docs = self.root / "docs/features/m/demo"
        docs.mkdir(parents=True, exist_ok=True)
        prd = docs / "demo-prd-2026-11-14.md"
        prd.write_text("# PRD\n\n### 端到端验收场景\n- AC-1：Given 测试输入，When 执行测试，Then 退出码为 0\n", encoding="utf-8")
        digest = hashlib.sha256(prd.read_bytes()).hexdigest()
        (self.feature / "acceptance-plan.yaml").write_text(f"""schema: agent-dev.feature-acceptance/v2
tech_spec: demo-tech-spec-2026-11-14.md
documents:
  prd:
    path: docs/features/m/demo/demo-prd-2026-11-14.md
    sha256: {digest}
cases:
  AC-1:
    title: 命令验收
    source: {{document: prd, anchor: AC-1}}
    required: true
    executor: command
    oracle: auto
    preconditions: [环境：隔离测试环境]
    steps: [执行测试]
    expected: 退出码为 0
    command: python3 -I tests/test_a.py
    assertions: {{exit: 0}}
    evidence: [log]
""", encoding="utf-8")

    def write_second_plan_issue(self, blocked="[]", scope="src/b.py"):
        text = self.issue.read_text(encoding="utf-8").replace("issue-001", "issue-002")
        text = text.replace("status: confirmed", "status: proposed\nblocked-by: " + blocked)
        text = text.replace("declared-scope: [src/a.py]", "declared-scope: [" + scope + "]")
        path = self.feature / "issues/issue-002-demo.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_check_plan_validates_group_and_cli(self):
        self.prepare_plan()
        result = tc.check_plan(self.issue, self.root)
        self.assertTrue(result["ok"], result["errors"])
        cli = subprocess.run([sys.executable, "-I", str(SCRIPT), "check-plan", "--issue", str(self.issue),
                              "--repo", str(self.root)], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertTrue(json.loads(cli.stdout)["ok"])

    def test_check_plan_rejects_missing_source_ac(self):
        self.prepare_plan()
        prd = self.root / "docs/features/m/demo/demo-prd-2026-11-14.md"
        prd.write_text(prd.read_text() + "- AC-2：Given 新场景，When 未承接，Then 校验失败\n", encoding="utf-8")
        result = tc.check_plan(self.issue, self.root)
        self.assertFalse(result["ok"])
        self.assertTrue(any("sha256" in error or "覆盖不完整" in error for error in result["errors"]), result["errors"])

    def test_check_plan_rejects_cycles_and_unserialised_overlap(self):
        self.prepare_plan()
        self.issue.write_text(self.issue.read_text(encoding="utf-8").replace(
            "status: confirmed", "status: proposed\nblocked-by: [issue-002]"), encoding="utf-8")
        self.write_second_plan_issue(blocked="[issue-001]")
        result = tc.check_plan(self.issue, self.root)
        self.assertTrue(any("cycle" in error for error in result["errors"]), result["errors"])
        self.issue.write_text(self.issue.read_text(encoding="utf-8").replace("blocked-by: [issue-002]", "blocked-by: []"), encoding="utf-8")
        second = self.feature / "issues/issue-002-demo.md"
        second.write_text(second.read_text(encoding="utf-8").replace("blocked-by: [issue-001]", "blocked-by: []")
                          .replace("declared-scope: [src/b.py]", "declared-scope: [src/a.py]"), encoding="utf-8")
        result = tc.check_plan(self.issue, self.root)
        self.assertTrue(any("overlapping scope" in error for error in result["errors"]), result["errors"])

    def test_cli_create_show_validate(self):
        output = self.feature / "impl/issue-001/cli-context.json"
        create = subprocess.run([sys.executable, "-I", str(SCRIPT), "create", "--issue", str(self.issue),
                                 "--repo", str(self.root), "--output", str(output)],
                                capture_output=True, text=True)
        self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
        created = json.loads(create.stdout)
        self.assertEqual(created["source_head"], self.head)
        show = subprocess.run([sys.executable, "-I", str(SCRIPT), "show", "--context", str(output)],
                              capture_output=True, text=True)
        self.assertEqual(show.returncode, 0, show.stdout + show.stderr)
        self.assertEqual(json.loads(show.stdout), created)
        validate = subprocess.run([sys.executable, "-I", str(SCRIPT), "validate", str(output)],
                                  capture_output=True, text=True)
        self.assertEqual(validate.returncode, 0, validate.stdout + validate.stderr)
        self.assertTrue(json.loads(validate.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
