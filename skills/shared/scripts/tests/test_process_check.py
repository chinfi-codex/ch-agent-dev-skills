#!/usr/bin/env python3
"""process_check.py 的红线 / 漂移 / 档位 / 证据 / full-ref / 覆盖矩阵 / 符合性核对 / worktree dev/ 卫生契约测试。

运行：python3 -m unittest discover -s skills/shared/scripts/tests -v
每个用例在独立临时目录建合成 git 仓（main + feat 分支 + dev/ 产物；dev/ 按约定不入库）。
"""
from __future__ import annotations

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
  light-max-files: 1
  scoped-max-files: 3
  scoped-max-loc: 400
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

## 漂移与升档记录

| 检查时点 | 漂移文件清单 | 是否触发升档 | 处理 |
|---|---|---|---|
| r1 | src/b.py | 否 | 登记 |
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
        self.write_issue(tier="scoped", reason="default")
        (self.feat / "issues/issue-002-y.md").write_text(
            "---\nissue-id: issue-002\nblocked-by: [issue-001]\n---\n", encoding="utf-8")
        self.log = self.feat / "impl/issue-001/impl-log-issue-001.md"
        self.log.write_text(LOG_OK, encoding="utf-8")
        self.write_evidence(target="scoped", executed="scoped")

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, tier, reason):
        self.issue.write_text(
            f"---\nissue-id: issue-001\nblocked-by: []\nverify-tier: {tier}\ntier-reason: {reason}\n"
            f'declared-scope: ["src/a.py"]\n---\n', encoding="utf-8")

    def write_evidence(self, target, executed, extra=""):
        (self.feat / "impl/issue-001/evidence-issue-001.md").write_text(
            f"```yaml\ncommit: {self.head}\nverify-tier: scoped\ntier-target: {target}\n"
            f"tier-executed: {executed}\n{extra}all-passed: true\n```\n", encoding="utf-8")

    def make_integration(self):
        (self.feat / "issues/issue-002-y.md").unlink()
        self.write_evidence(target="full", executed="scoped")
        self.log.write_text(LOG_OK + "| r2 | - | 是 | 集成票升至 full |\n", encoding="utf-8")

    def check(self, phase, repo=None):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--issue", str(self.issue),
                            "--repo", str(repo or self.root), "--phase", phase], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

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
        self.assertIn("原因 default", out)

    def test_integration_ticket_requires_full_target(self):
        (self.feat / "issues/issue-002-y.md").unlink()
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("集成票", out)

    def test_integration_review_phase_defers_full(self):
        self.make_integration()
        self.assertEqual(self.check("review")[0], 0)
        code, out = self.check("merge")
        self.assertEqual(code, 1, out)
        self.assertIn("本阶段要求 full", out)

    def test_full_commit_passes_and_goes_stale(self):
        self.make_integration()
        self.write_evidence(target="full", executed="full", extra=f"full-commit: {self.head}\n")
        self.assertEqual(self.check("merge")[0], 0)
        (self.root / "src/a.py").write_text("a\nb\nlate\n")
        sh(self.root, "git", "commit", "-qam", "late")
        code, out = self.check("merge")
        self.assertEqual(code, 1, out)
        self.assertIn("证据过期", out)

    def _daily_record(self, tree, result="pass"):
        rec = self.root / "dev/full-runs/2026-10-15-x.md"
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(f"# daily full\n\n- tree: `{tree}`\n- result: {result}\n", encoding="utf-8")
        return "dev/full-runs/2026-10-15-x.md"

    def test_full_ref_reuses_matching_daily_record(self):
        self.make_integration()
        tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")
        ref = self._daily_record(tree)
        self.write_evidence(target="full", executed="full", extra=f"full-ref: {ref}\n")
        code, out = self.check("merge")
        self.assertEqual(code, 0, out)

    def test_full_ref_rejects_other_tree_or_failed_run(self):
        self.make_integration()
        ref = self._daily_record("0123456789abcdef0123456789abcdef01234567")
        self.write_evidence(target="full", executed="full", extra=f"full-ref: {ref}\n")
        code, out = self.check("merge")
        self.assertEqual(code, 1, out)
        self.assertIn("不能复用", out)
        tree = sh(self.root, "git", "rev-parse", "HEAD^{tree}")
        self._daily_record(tree, result="fail")
        code, out = self.check("merge")
        self.assertEqual(code, 1, out)
        self.assertIn("不是 pass", out)

    # ---- 需求覆盖矩阵 ----

    def test_matrix_section_missing_fails(self):
        self.log.write_text(LOG_OK.replace("## 需求覆盖矩阵", "## 覆盖矩阵（改名）"), encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("需求覆盖矩阵", out)

    # ---- 见红告警与 bug 票回归有效性 ----

    def test_added_test_without_red_evidence_warns(self):
        # 新增测试命中红线 tests/**，登记但不记见红 → 红线通过但收 REVIEW 告警
        (self.root / "tests/test_new.py").write_text("new\n")
        sh(self.root, "git", "add", "tests/test_new.py")
        sh(self.root, "git", "commit", "-qm", "add test")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.write_evidence(target="scoped", executed="scoped")
        self.log.write_text(LOG_OK.replace("## 做了什么\nx", "## 做了什么\nx；tests/test_new.py 新增登记"),
                            encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.assertIn("tdd-red", out)
        # 补上见红记录 → 告警消失
        self.log.write_text(LOG_OK.replace("## 做了什么\nx", "## 做了什么\nx；tests/test_new.py 见红：断言失败，预期 1 实得 0"),
                            encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 0, out)
        self.assertNotIn("tdd-red", out)

    def test_bug_ticket_requires_revert_section(self):
        self.issue.write_text(
            "---\nissue-id: issue-001\ntype: bug\nblocked-by: []\nverify-tier: scoped\ntier-reason: default\n"
            'declared-scope: ["src/a.py"]\n---\n', encoding="utf-8")
        code, out = self.check("review")
        self.assertEqual(code, 1, out)
        self.assertIn("回归有效性", out)
        # 补上回归有效性节 → 通过
        ev_path = self.feat / "impl/issue-001/evidence-issue-001.md"
        ev_path.write_text(ev_path.read_text(encoding="utf-8")
                           + "\n## 回归有效性（仅 `type: bug` 票必填）\n\n- revert 后重跑：exit 1（断言失败）\n- 恢复后重跑：exit 0\n",
                           encoding="utf-8")
        self.assertEqual(self.check("review")[0], 0)
        # feature 票（缺省类型）不要求该节
        self.write_issue(tier="scoped", reason="default")
        ev_path.write_text(f"```yaml\ncommit: {self.head}\nverify-tier: scoped\ntier-target: scoped\n"
                           "tier-executed: scoped\nall-passed: true\n```\n", encoding="utf-8")
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
        # 软链（约定形态）同样通过
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
        # dev/ 被入库的存量仓：worktree checkout 自带实体 dev/ → REVIEW 告警而非 FAIL
        sh(self.root, "git", "add", "dev")
        sh(self.root, "git", "commit", "-qm", "track dev")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.write_evidence(target="scoped", executed="scoped")
        wt = self.make_worktree()
        code, out = self.check("review", repo=wt)
        self.assertEqual(code, 0, out)
        self.assertIn("[REVIEW] worktree-dev", out)

    # ---- 需求符合性核对（close 相）----

    def write_conformance(self, verdict="conform", commit=None):
        (self.feat / "impl/issue-001/conformance-issue-001.md").write_text(
            f"```yaml\nissue: issue-001\ncommit: {commit or self.head}\nverdict: {verdict}\n```\n",
            encoding="utf-8")

    def write_config(self, extra=""):
        (self.root / "dev/agents-config.md").write_text(CONFIG.replace("```\n", extra + "```\n"),
                                                        encoding="utf-8")

    def make_light_ticket(self):
        sh(self.root, "git", "checkout", "-q", "main")
        sh(self.root, "git", "checkout", "-qb", "feat-light")
        (self.root / "src/a.py").write_text("a\nlight\n")
        sh(self.root, "git", "commit", "-qam", "light work")
        self.head = sh(self.root, "git", "rev-parse", "HEAD")
        self.write_issue(tier="light", reason="default")
        self.write_evidence(target="light", executed="light")

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

    def test_close_skipped_only_for_sampled_light(self):
        # scoped 票即使 sampled 模式也不允许 skipped
        self.write_config("conformance:\n  mode: sampled\n")
        self.write_conformance(verdict="skipped")
        code, out = self.check("close")
        self.assertEqual(code, 1, out)
        self.assertIn("skipped", out)
        # sampled 模式 + 实测 light 档 → skipped 合法
        self.make_light_ticket()
        self.write_conformance(verdict="skipped")
        self.assertEqual(self.check("close")[0], 0)
        # 未配 conformance 节（默认 always）的 light 票不许 skipped
        self.write_config()
        code, out = self.check("close")
        self.assertEqual(code, 1, out)
        self.assertIn("skipped", out)


if __name__ == "__main__":
    unittest.main()
