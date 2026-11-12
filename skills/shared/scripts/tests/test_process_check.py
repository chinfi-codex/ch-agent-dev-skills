#!/usr/bin/env python3
"""process_check.py 的红线 / 漂移 / 票大小 / DoD+fast 证据 / 覆盖矩阵 / 符合性 / worktree / CI 契约测试。

运行：python3 -I skills/shared/scripts/tests/test_process_check.py -v
每个用例在独立临时目录建合成 git 仓（main + feat 分支 + dev/ 产物；dev/ 按约定不入库）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "process_check.py"

CONFIG = """```yaml
main-branch: main
verify-policy:
  ticket-max-files: 10
  ticket-max-loc: 400
redlines:
  - "tests/**"
```
"""

LOG_OK = """## 做了什么
x

## 需求覆盖矩阵

| 需求点 | 实现位置 | 覆盖测试 | evidence 行 | 关键 |
|---|---|---|---|---|
| DoD-1 | src/a.py:f | tests/test_a.py | L3 | ★ |

## 红线接触记录
tests/test_a.py 需求驱动更新 -> DoD-1

## 漂移记录

| 检查时点 | 漂移文件清单 | 处理 |
|---|---|---|
| r1 | src/b.py | 登记 |
"""


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ProcessCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = Path(self.tmp.name)
        sh(r, "git", "init", "-q", "-b", "main")
        sh(r, "git", "config", "user.email", "t@t")
        sh(r, "git", "config", "user.name", "t")
        self.feat = r / "dev/features/m/f"
        (self.feat / "issues").mkdir(parents=True)
        (self.feat / "impl/issue-001").mkdir(parents=True)
        (r / "src").mkdir()
        (r / "tests").mkdir()
        (r / "dev/agents-config.md").write_text(CONFIG, encoding="utf-8")
        (r / "src/a.py").write_text("a\n")
        (r / "tests/test_a.py").write_text("t\n")
        sh(r, "git", "add", "src", "tests")
        sh(r, "git", "commit", "-qm", "base")
        sh(r, "git", "checkout", "-qb", "feat")
        (r / "src/a.py").write_text("a\nb\n")
        (r / "src/b.py").write_text("c\n")
        (r / "tests/test_a.py").write_text("t\nt2\n")
        sh(r, "git", "add", "src", "tests")
        sh(r, "git", "commit", "-qm", "work")
        self.head = sh(r, "git", "rev-parse", "HEAD")
        self.issue = self.feat / "issues/issue-001-x.md"
        self.write_issue()
        (self.feat / "issues/issue-002-y.md").write_text(
            "---\nissue-id: issue-002\nblocked-by: [issue-001]\n---\n", encoding="utf-8")
        self.log = self.feat / "impl/issue-001/impl-log-issue-001.md"
        self.log.write_text(LOG_OK, encoding="utf-8")
        self.write_evidence()

    def tearDown(self):
        self.tmp.cleanup()

    @property
    def evidence_path(self):
        return self.feat / "impl/issue-001/evidence-issue-001.md"

    def write_issue(self, issue_type=None):
        type_line = f"type: {issue_type}\n" if issue_type else ""
        self.issue.write_text(
            f"---\nissue-id: issue-001\n{type_line}blocked-by: []\n"
            'declared-scope: ["src/a.py"]\n---\n', encoding="utf-8")

    def write_evidence(self, all_passed=True, commit=None):
        passed = "true" if all_passed else "false"
        self.evidence_path.write_text(
            f"```yaml\ncommit: {commit or self.head}\nall-passed: {passed}\n```\n",
            encoding="utf-8")

    def write_config(self, extra=""):
        (self.root / "dev/agents-config.md").write_text(
            CONFIG.replace("redlines:\n", extra + "redlines:\n"), encoding="utf-8")

    def make_integration(self):
        (self.feat / "issues/issue-002-y.md").unlink()

    def check(self, phase, repo=None, extra=()):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--issue", str(self.issue),
                            "--repo", str(repo or self.root), "--phase", phase, *extra],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def write_conformance(self, verdict="conform", commit=None):
        (self.feat / "impl/issue-001/conformance-issue-001.md").write_text(
            f"```yaml\nissue: issue-001\ncommit: {commit or self.head}\nverdict: {verdict}\n```\n",
            encoding="utf-8")

    # ---- 红线、漂移与票大小 ----

    def test_unregistered_redline_and_drift_fail(self):
        self.log.write_text(LOG_OK.replace("tests/test_a.py 需求驱动更新 -> DoD-1", "无")
                            .replace("src/b.py", "无漂移"), encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("tests/test_a.py", out)
        self.assertIn("src/b.py", out)

    def test_registered_passes_with_review_items(self):
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.assertIn("[REVIEW] redline", out)
        self.assertIn("生产文件 2 / 上限 10", out)
        self.assertIn("集成票 否", out)

    def test_drift_record_section_is_required(self):
        self.log.write_text(LOG_OK.replace("## 漂移记录", "## 其他记录"), encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("impl-log「漂移记录」为空", out)

    def test_too_many_production_files_require_resplit(self):
        self.write_config()
        path = self.root / "dev/agents-config.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "ticket-max-files: 10", "ticket-max-files: 1"), encoding="utf-8")
        for phase in ("review", "merge", "close"):
            with self.subTest(phase=phase):
                code, out = self.check(phase)
                self.assertEqual(code, 1, out)
                self.assertIn("ticket-max-files 1", out)
                self.assertIn("须再拆票", out)

    def test_excess_production_loc_requires_resplit(self):
        path = self.root / "dev/agents-config.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "ticket-max-loc: 400", "ticket-max-loc: 1"), encoding="utf-8")
        code, out = self.check("merge")
        self.assertEqual(code, 1, out)
        self.assertIn("生产 LOC 2 > ticket-max-loc 1", out)
        self.assertIn("须再拆票", out)

    def test_ticket_size_limits_have_defaults(self):
        path = self.root / "dev/agents-config.md"
        path.write_text("""```yaml
main-branch: main
redlines:
  - "tests/**"
```
""", encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.assertIn("生产文件 2 / 上限 10", out)
        self.assertIn("LOC 2 / 上限 400", out)

    # ---- 所有票只核 DoD + fast 证据 ----

    def test_ordinary_merge_and_close_need_only_dod_fast_evidence(self):
        self.assertEqual(self.check("review")[0], 0)
        self.assertEqual(self.check("merge")[0], 0)
        self.write_conformance()
        code, out = self.check("close")
        self.assertEqual(code, 0, out)

    def test_integration_merge_and_close_need_only_dod_fast_evidence(self):
        self.make_integration()
        code, out = self.check("merge")
        self.assertEqual(code, 0, out)
        self.assertIn("集成票 是", out)
        self.write_conformance()
        self.assertEqual(self.check("close")[0], 0)

    def test_evidence_requires_all_passed_and_fresh_commit(self):
        self.write_evidence(all_passed=False)
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("DoD + fast", out)
        self.assertIn("all-passed", out)

        self.write_evidence()
        (self.root / "src/a.py").write_text("a\nb\nlate\n")
        sh(self.root, "git", "commit", "-qam", "late")
        code, out = self.check("merge")
        self.assertEqual(code, 1, out)
        self.assertIn("证据过期", out)

    def test_json_metadata_reports_limits_and_integration_status(self):
        output = self.root / "dev/process-check.json"
        code, out = self.check("review", extra=("--json", str(output)))
        self.assertEqual(code, 0, out)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["ticket-max-files"], 10)
        self.assertEqual(result["ticket-max-loc"], 400)
        self.assertFalse(result["integration-ticket"])
        self.assertNotIn("successors", result)
        self.assertFalse(any(key.startswith("tier") or key == "ticket-tier" for key in result))

        self.make_integration()
        code, out = self.check("merge", extra=("--json", str(output)))
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["integration-ticket"])

    # ---- CI 分支 / main 放行 ----

    def write_ci_verdict(self, **updates):
        value = {
            "schema": "pandu.test-verdict/v1", "status": "completed", "pass": True,
            "new_failures": [], "unattributed_failures": [], "skipped_env_n": 0,
            "attribution_status": "not-needed", "quarantined_n": 0,
            "commit": self.head, "tree": sh(self.root, "git", "rev-parse", "HEAD^{tree}"),
            "ref": "refs/heads/feat", "run_id": "12345", "ran_n": 1,
        }
        value.update(updates)
        path = self.root / "dev/ci-verdict.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def promote_args(self, verdict_path, **proof_updates):
        proof = {
            "schema": "pandu.ci-run-proof/v1", "run_id": "12345", "event": "push",
            "status": "completed", "conclusion": "success", "commit": self.head,
            "branch": "feat", "verdict_pass": True,
            "verdict_sha256": hashlib.sha256(Path(verdict_path).read_bytes()).hexdigest(),
        }
        proof.update(proof_updates)
        path = self.root / "dev/ci-run-proof.json"
        path.write_text(json.dumps(proof), encoding="utf-8")
        return ["--ci-verdict", verdict_path, "--ci-run-proof", str(path)]

    def test_promote_requires_matching_green_ci_verdict_and_run_proof(self):
        verdict = self.write_ci_verdict()
        code, out = self.check("promote", extra=["--ci-verdict", verdict])
        self.assertEqual(code, 1, out)
        self.assertIn("ci-run-proof", out)

        code, out = self.check("promote", extra=self.promote_args(verdict))
        self.assertEqual(code, 0, out)
        self.assertIn("CI 判决与 HEAD commit / tree 一致", out)

        stale = self.write_ci_verdict(tree="0" * 40)
        code, out = self.check("promote", extra=self.promote_args(stale))
        self.assertEqual(code, 1, out)
        self.assertIn("tree", out)

        incomplete = self.write_ci_verdict(status="incomplete", **{"pass": False})
        code, out = self.check("promote", extra=self.promote_args(incomplete))
        self.assertEqual(code, 1, out)
        self.assertIn("pass", out)

        no_tests = self.write_ci_verdict(ran_n=0)
        code, out = self.check("promote", extra=self.promote_args(no_tests))
        self.assertEqual(code, 1, out)
        self.assertIn("没有实际执行测试", out)

        overdue = self.write_ci_verdict(quarantine_overdue_n=1)
        code, out = self.check("promote", extra=self.promote_args(overdue))
        self.assertEqual(code, 1, out)
        self.assertIn("超期隔离", out)

        missing_hash = self.write_ci_verdict(quarantined_n=1)
        code, out = self.check("promote", extra=self.promote_args(missing_hash))
        self.assertEqual(code, 1, out)
        self.assertIn("配置哈希", out)

    def test_promote_rejects_missing_verdict_and_mismatched_run_proof(self):
        code, out = self.check("promote")
        self.assertEqual(code, 1, out)
        self.assertIn("ci-verdict", out)

        verdict = self.write_ci_verdict()
        code, out = self.check("promote", extra=self.promote_args(verdict, branch="other"))
        self.assertEqual(code, 1, out)
        self.assertIn("真实 run 分支", out)

        code, out = self.check("promote", extra=self.promote_args(verdict, verdict_sha256="0" * 64))
        self.assertEqual(code, 1, out)
        self.assertIn("哈希不一致", out)

    # ---- 需求覆盖矩阵、TDD 与 bug 回归有效性 ----

    def test_matrix_section_missing_fails(self):
        self.log.write_text(LOG_OK.replace("## 需求覆盖矩阵", "## 覆盖矩阵（改名）"), encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("需求覆盖矩阵", out)

    def test_added_test_without_red_evidence_warns(self):
        (self.root / "tests/test_new.py").write_text("new\n")
        sh(self.root, "git", "add", "tests/test_new.py")
        sh(self.root, "git", "commit", "-qm", "add test")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.write_evidence()
        self.log.write_text(LOG_OK.replace("## 做了什么\nx", "## 做了什么\nx；tests/test_new.py 新增登记"),
                            encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.assertIn("tdd-red", out)

        self.log.write_text(LOG_OK.replace(
            "## 做了什么\nx", "## 做了什么\nx；tests/test_new.py 见红：断言失败，预期 1 实得 0"),
            encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.assertNotIn("tdd-red", out)

    def test_bug_ticket_requires_revert_section(self):
        self.write_issue(issue_type="bug")
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("回归有效性", out)

        self.evidence_path.write_text(
            self.evidence_path.read_text(encoding="utf-8")
            + "\n## 回归有效性\n\n- revert 后重跑：exit 1（断言失败）\n- 恢复后重跑：exit 0\n",
            encoding="utf-8")
        self.assertEqual(self.check("review")[0], 0)

        self.write_issue()
        self.write_evidence()
        self.assertEqual(self.check("review")[0], 0)

    # ---- worktree dev/ 卫生（软链约定）----

    def make_worktree(self, name="issue-001"):
        wt = self.root / ".worktrees" / name
        sh(self.root, "git", "worktree", "add", "--detach", str(wt), "HEAD")
        return wt

    def test_worktree_dev_absent_or_symlink_passes(self):
        wt = self.make_worktree()
        code, out = self.check("review", repo=wt)
        self.assertEqual(code, 0, out)
        self.assertNotIn("worktree-dev", out)

        os.symlink("../../dev", wt / "dev")
        code, out = self.check("review", repo=wt)
        self.assertEqual(code, 0, out)
        self.assertNotIn("worktree-dev", out)

    def test_worktree_dev_real_copy_fails(self):
        wt = self.make_worktree()
        (wt / "dev").mkdir()
        (wt / "dev/capture.png").write_bytes(b"x")
        code, out = self.check("review", repo=wt)
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL] worktree-dev", out)
        self.assertIn("实体副本", out)

    def test_worktree_dev_tracked_only_warns(self):
        sh(self.root, "git", "add", "dev")
        sh(self.root, "git", "commit", "-qm", "track dev")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.write_evidence()
        wt = self.make_worktree()
        code, out = self.check("review", repo=wt)
        self.assertEqual(code, 0, out)
        self.assertIn("[REVIEW] worktree-dev", out)

    # ---- 需求符合性核对（close 相）----

    def test_close_requires_conformance_record(self):
        code, out = self.check("close")
        self.assertEqual(code, 1, out)
        self.assertIn("符合性核对记录不存在", out)

    def test_close_conform_passes(self):
        self.write_conformance()
        self.assertEqual(self.check("close")[0], 0)

    def test_close_deviate_or_missing_fails(self):
        self.write_conformance(verdict="deviate")
        code, out = self.check("close")
        self.assertEqual(code, 1, out)
        self.assertIn("deviate", out)

    def test_close_stale_conformance_fails(self):
        self.write_conformance()
        (self.root / "src/a.py").write_text("a\nb\nlate\n")
        sh(self.root, "git", "commit", "-qam", "late")
        code, out = self.check("close")
        self.assertEqual(code, 1, out)
        self.assertIn("核对过期", out)

    def test_sampled_skip_allowed_for_non_integration(self):
        self.write_config("conformance:\n  mode: sampled\n  sample-rate: 0\n")
        self.write_conformance(verdict="skipped")
        code, out = self.check("close")
        self.assertEqual(code, 0, out)

    def test_sampled_skip_rejected_for_integration(self):
        self.write_config("conformance:\n  mode: sampled\n  sample-rate: 100\n")
        self.make_integration()
        self.write_conformance(verdict="skipped")
        code, out = self.check("close")
        self.assertEqual(code, 1, out)
        self.assertIn("非集成票", out)
        self.write_conformance()
        self.assertEqual(self.check("close")[0], 0)

    def test_always_mode_rejects_skipped_for_non_integration(self):
        self.write_conformance(verdict="skipped")
        code, out = self.check("close")
        self.assertEqual(code, 1, out)
        self.assertIn("skipped", out)


if __name__ == "__main__":
    unittest.main()
