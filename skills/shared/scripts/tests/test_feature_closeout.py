#!/usr/bin/env python3
"""feature_closeout.py 契约测试：冻结前提、tree 绑定、自动 / 人工分工、main 漂移、合并登记。

运行：python3 -m unittest discover -s skills/shared/scripts/tests -v
每个用例建合成仓：主工作区（main，含 dev/）+ 集成 worktree（feat/<slug>）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "feature_closeout.py"
SLUG = "demo"

CONFIG = """```yaml
main-branch: main
feature-closeout:
  mode: local-human
  integration-branch: "feat/<feature-slug>"
  main-drift-ignore:
    - "dev/**"
    - "docs/**"
```
"""


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FeatureCloseoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = Path(self.tmp.name) / "repo"
        r.mkdir()
        sh(r, "git", "init", "-q", "-b", "main")
        sh(r, "git", "config", "user.email", "t@t")
        sh(r, "git", "config", "user.name", "t")
        (r / "src").mkdir()
        (r / "src" / "a.py").write_text("x = 1\n")
        (r / ".gitignore").write_text("dev/\n.worktrees/\n")
        sh(r, "git", "add", ".")
        sh(r, "git", "commit", "-qm", "init")
        self.wt = r / ".worktrees" / f"integration-{SLUG}"
        sh(r, "git", "worktree", "add", "-q", str(self.wt), "-b", f"feat/{SLUG}", "main")
        (self.wt / "src" / "a.py").write_text("x = 2\n")
        sh(self.wt, "git", "commit", "-qam", "issue-001")
        # 主工作区 dev/
        (r / "dev").mkdir()
        (r / "dev" / "agents-config.md").write_text(CONFIG)
        self.fdir = r / "dev" / "features" / "mod" / SLUG
        (self.fdir / "issues").mkdir(parents=True)
        (self.fdir / "impl" / "issue-001").mkdir(parents=True)
        self.write_issue("done")
        (self.fdir / "impl" / "issue-001" / "conformance-issue-001.md").write_text("```yaml\nverdict: conform\n```\n")
        (self.fdir / "progress.jsonl").write_text(json.dumps({"event": "merged", "issue": "issue-001", "commit": "abc"}) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, status):
        (self.fdir / "issues" / "issue-001-x.md").write_text(f"---\nissue-id: issue-001\nstatus: {status}\n---\n# x\n")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def freeze(self):
        return self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt))

    def check(self, phase, *extra):
        return self.run_cli("check", "--phase", phase, "--feature-dir", str(self.fdir), "--repo", str(self.wt), *extra)

    @property
    def tree(self):
        return json.loads((self.fdir / "integration" / "candidate.json").read_text())["candidate_tree"]

    def write_evidence(self, items, all_passed=True):
        integ = self.fdir / "integration"
        (integ / "evidence.md").write_text(
            f"```yaml\ncandidate_tree: {self.tree}\nall-passed: {'true' if all_passed else 'false'}\n"
            "ran:\n  direct: 3\n  indirect: 5\n  deferred: 1\ndeferred:\n  - tools/tests/x.py：预算截断\n```\n")
        (integ / "conformance-feature.md").write_text("```yaml\nverdict: conform\n```\n")
        (integ / "business-results.json").write_text(json.dumps({
            "schema": "agent-dev.feature-business-results/v1", "candidate_tree": self.tree, "items": items}))

    def confirm(self):
        (self.fdir / "integration" / "confirmation.md").write_text(
            f"```yaml\ncandidate_tree: {self.tree}\nconfirmed-by: user\nconfirmed-at: 2026-11-09 10:00\nquote: 确认合入\n```\n")

    AUTO = {"id": "AC-1", "oracle": "auto", "status": "pass", "command": "python3 t.py"}
    HUMAN = {"id": "AC-2", "oracle": "human"}

    # ---- freeze ----
    def test_freeze_requires_all_done(self):
        self.write_issue("in-review")
        r = self.freeze()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("未全部 done", r.stdout)

    def test_freeze_requires_main_synced(self):
        (self.root / "src" / "b.py").write_text("y = 1\n")
        sh(self.root, "git", "add", "src/b.py")
        sh(self.root, "git", "commit", "-qm", "main moves")
        r = self.freeze()
        self.assertEqual(r.returncode, 1)
        self.assertIn("尚未同步", r.stdout)

    def test_freeze_rejects_wrong_branch(self):
        sh(self.wt, "git", "checkout", "-qb", "codex/other")
        r = self.freeze()
        self.assertEqual(r.returncode, 1)
        self.assertIn("独立集成分支", r.stdout)

    def test_freeze_writes_candidate(self):
        r = self.freeze()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cand = json.loads((self.fdir / "integration" / "candidate.json").read_text())
        self.assertEqual(cand["candidate_tree"], sh(self.wt, "git", "rev-parse", "HEAD^{tree}"))
        self.assertEqual(cand["issues"][0]["merged_commit"], "abc")

    def test_freeze_round_scopes_issues_and_branch(self):
        # 同一 feature 目录另有一张未完成票，但不属本轮
        (self.fdir / "issues" / "issue-002-y.md").write_text("---\nissue-id: issue-002\nstatus: in-progress\n---\n")
        sh(self.wt, "git", "branch", "-m", "feat/round2")
        r = self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt), "--round", "round2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("须配 --issues", r.stdout)
        r = self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                         "--round", "round2", "--issues", "issue-001")
        self.assertEqual(r.returncode, 0, r.stdout)
        cand = json.loads((self.fdir / "integration" / "round2" / "candidate.json").read_text())
        self.assertEqual([i["id"] for i in cand["issues"]], ["issue-001"])

    # ---- 目标分支 ----
    def test_target_branch_resolution(self):
        issue = self.fdir / "issues" / "issue-001-x.md"
        issue.write_text(f"---\nissue-id: issue-001\nfeature-slug: {SLUG}\nstatus: done\n---\n")
        r = self.run_cli("target", "--issue", str(issue))
        self.assertEqual(r.stdout.strip(), f"feat/{SLUG}", r.stderr)
        issue.write_text("---\nissue-id: issue-001\nfeature-slug: x\ntarget-branch: main\nstatus: done\n---\n")
        self.assertEqual(self.run_cli("target", "--issue", str(issue)).stdout.strip(), "main")
        # since 之前创建的存量票保持 main-branch 默认
        cfg = self.root / "dev" / "agents-config.md"
        cfg.write_text(CONFIG.replace("  mode: local-human\n", "  mode: local-human\n  since: 2026-11-09\n"))
        issue.write_text("---\nissue-id: issue-001\nfeature-slug: x\ncreated: 2026-11-01\nstatus: done\n---\n")
        self.assertEqual(self.run_cli("target", "--issue", str(issue)).stdout.strip(), "main")
        issue.write_text("---\nissue-id: issue-001\nfeature-slug: x\ncreated: 2026-11-10\nstatus: done\n---\n")
        self.assertEqual(self.run_cli("target", "--issue", str(issue)).stdout.strip(), "feat/x")

    def test_process_check_diffs_against_integration_branch(self):
        # 票分支自集成分支切出：diff base 必须是集成分支，不得把前序票改动算进本票
        issue = self.fdir / "issues" / "issue-002-y.md"
        issue.write_text(f"---\nissue-id: issue-002\nfeature-slug: {SLUG}\nstatus: in-progress\nverify-tier: light\n"
                         "declared-scope: [src/b.py]\n---\n")
        tw = self.root / ".worktrees" / "issue-002"
        sh(self.root, "git", "worktree", "add", "-q", str(tw), "-b", f"feat/{SLUG}-issue-002", f"feat/{SLUG}")
        (tw / "src" / "b.py").write_text("y = 1\n")
        sh(tw, "git", "add", "src/b.py")
        sh(tw, "git", "commit", "-qm", "issue-002")
        out = Path(self.tmp.name) / "pc.json"
        pc = SCRIPT.parent / "process_check.py"
        subprocess.run([sys.executable, str(pc), "--issue", str(issue), "--repo", str(tw), "--phase", "review",
                        "--json", str(out)], capture_output=True, text=True)
        meta = json.loads(out.read_text())
        self.assertEqual(meta["target-branch"], f"feat/{SLUG}")
        self.assertEqual(meta["base"], sh(self.wt, "git", "rev-parse", "HEAD"))
        self.assertEqual(meta["production-files"], 1)

    def test_process_check_missing_integration_branch(self):
        issue = self.fdir / "issues" / "issue-003-z.md"
        issue.write_text("---\nissue-id: issue-003\nfeature-slug: nobranch\nstatus: in-progress\n---\n")
        pc = SCRIPT.parent / "process_check.py"
        r = subprocess.run([sys.executable, str(pc), "--issue", str(issue), "--repo", str(self.root), "--phase", "review"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("须先建集成分支", r.stderr)

    # ---- verify ----
    def test_verify_passes_and_records(self):
        self.freeze()
        self.write_evidence([self.AUTO, self.HUMAN])
        r = self.check("verify", "--record")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("feature-verified", (self.fdir / "progress.jsonl").read_text())

    def test_verify_fails_on_new_commit_after_freeze(self):
        self.freeze()
        self.write_evidence([self.AUTO])
        (self.wt / "src" / "a.py").write_text("x = 3\n")
        sh(self.wt, "git", "commit", "-qam", "late fix")
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("重新 freeze", r.stdout)

    def test_verify_fails_on_auto_failure_and_missing_command(self):
        self.freeze()
        self.write_evidence([{"id": "AC-1", "oracle": "auto", "status": "fail", "command": "x"},
                             {"id": "AC-3", "oracle": "auto", "status": "pass"}])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自动执行失败", r.stdout)
        self.assertIn("缺执行命令", r.stdout)

    def test_verify_does_not_require_human(self):
        self.freeze()
        self.write_evidence([self.HUMAN])
        self.assertEqual(self.check("verify").returncode, 0)

    # ---- merge ----
    def test_merge_requires_human_and_confirmation(self):
        self.freeze()
        self.write_evidence([self.AUTO, self.HUMAN])
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("人工结果 pending", r.stdout)
        self.assertIn("confirmation.md 不存在", r.stdout)

    def test_unverified_auto_must_be_waived(self):
        self.freeze()
        item = {"id": "AC-4", "oracle": "auto", "status": "unverified", "reason": "真库无授权",
                "human": {"status": "confirmed", "by": "user"}}
        self.write_evidence([item])
        self.confirm()
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("只能由人显式 waived", r.stdout)
        item["human"] = {"status": "waived", "by": "user", "note": "合 main 后另行安排"}
        self.write_evidence([item])
        self.assertEqual(self.check("merge").returncode, 0)

    def test_merge_passes_and_main_drift_rules(self):
        self.freeze()
        human = dict(self.HUMAN, human={"status": "confirmed", "by": "user"})
        self.write_evidence([self.AUTO, human])
        self.confirm()
        self.assertEqual(self.check("merge").returncode, 0)
        # main 只动 docs/ → 仍有效
        (self.root / "docs").mkdir()
        (self.root / "docs" / "n.md").write_text("n\n")
        sh(self.root, "git", "add", "docs")
        sh(self.root, "git", "commit", "-qm", "docs")
        r = self.check("merge", "--main-repo", str(self.root))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("main-drift-ignore 内", r.stdout)
        # main 动代码 → 失效
        (self.root / "src" / "c.py").write_text("z = 1\n")
        sh(self.root, "git", "add", "src/c.py")
        sh(self.root, "git", "commit", "-qm", "code")
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("非忽略路径", r.stdout)

    def test_confirmation_bound_to_tree(self):
        self.freeze()
        self.write_evidence([self.AUTO])
        (self.fdir / "integration" / "confirmation.md").write_text(
            "```yaml\ncandidate_tree: deadbeef\nconfirmed-by: user\nconfirmed-at: x\nquote: ok\n```\n")
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("须重新确认", r.stdout)

    # ---- record-merge / summary ----
    def test_record_merge(self):
        self.freeze()
        cand = json.loads((self.fdir / "integration" / "candidate.json").read_text())["candidate"]
        sh(self.root, "git", "merge", "-q", "--no-ff", f"feat/{SLUG}", "-m", "merge")
        r = self.run_cli("record-merge", "--feature-dir", str(self.fdir), "--repo", str(self.root), "--commit", "HEAD")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("feature-merged", (self.fdir / "progress.jsonl").read_text())
        r = self.run_cli("record-merge", "--feature-dir", str(self.fdir), "--repo", str(self.root), "--commit", "HEAD~1")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(cand)

    def test_summary(self):
        self.freeze()
        self.write_evidence([self.AUTO, dict(self.HUMAN, title="看图例", steps="打开报告", expect="图例齐全")])
        r = self.run_cli("summary", "--feature-dir", str(self.fdir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("需求收口确认：demo", r.stdout)
        self.assertIn("[human] AC-2 看图例", r.stdout)
        self.assertIn("延期兜底", r.stdout)


if __name__ == "__main__":
    unittest.main()
