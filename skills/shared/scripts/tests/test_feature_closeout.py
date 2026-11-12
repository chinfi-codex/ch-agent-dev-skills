#!/usr/bin/env python3
"""feature_closeout.py 契约测试：计划冻结、AC 对账、浏览器证据、报告与人工合并门。"""
from __future__ import annotations

import copy
import hashlib
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


def command_case():
    return {
        "title": "命令业务验收", "source": "PRD#AC-01", "required": True,
        "executor": "command", "oracle": "auto", "steps": ["执行验收命令"],
        "expected": "退出码为 0", "command": "python3 t.py",
        "assertions": {"exit": 0}, "evidence": ["log"], "human_reason": None,
    }


def browser_case(oracle="auto"):
    return {
        "title": "浏览器业务验收", "source": "PRD#AC-01", "required": True,
        "executor": "browser-agent", "oracle": oracle, "steps": ["打开页面", "点击筛选"],
        "expected": "只显示匹配结果", "assertions": {"dom": "只有匹配项"},
        "evidence": ["screenshot", "dom", "console"],
        "human_reason": "visual-ux" if oracle != "auto" else None,
    }


def human_case():
    return {
        "title": "看业务结论", "source": "PRD#AC-02", "required": True,
        "executor": "human", "oracle": "human", "steps": ["打开报告"],
        "expected": "领域结论合理", "human_reason": "domain-judgment",
    }


def plan_text(cases):
    lines = ["schema: agent-dev.feature-acceptance/v1", "prd: prd.md", "tech_spec: spec.md", "cases:"]
    for aid, case in cases.items():
        lines.append(f"  {aid}:")
        for key in ("title", "source"):
            lines.append(f"    {key}: {json.dumps(case[key], ensure_ascii=False)}")
        lines.append(f"    required: {'true' if case['required'] else 'false'}")
        lines.append(f"    executor: {case['executor']}")
        lines.append(f"    oracle: {case['oracle']}")
        lines.append(f"    steps: {json.dumps(case['steps'], ensure_ascii=False)}")
        lines.append(f"    expected: {json.dumps(case['expected'], ensure_ascii=False)}")
        if case.get("command"):
            lines.append(f"    command: {json.dumps(case['command'], ensure_ascii=False)}")
        if "assertions" in case:
            lines.append("    assertions:")
            for key, value in case["assertions"].items():
                lines.append(f"      {key}: {json.dumps(value, ensure_ascii=False)}")
        if case.get("evidence"):
            lines.append(f"    evidence: {json.dumps(case['evidence'], ensure_ascii=False)}")
        reason = case.get("human_reason")
        lines.append("    human_reason:" if reason is None else f"    human_reason: {reason}")
    return "\n".join(lines) + "\n"


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
        (r / "dev").mkdir()
        (r / "dev" / "agents-config.md").write_text(CONFIG)
        self.fdir = r / "dev" / "features" / "mod" / SLUG
        (self.fdir / "issues").mkdir(parents=True)
        (self.fdir / "impl" / "issue-001").mkdir(parents=True)
        self.write_issue("done")
        (self.fdir / "impl" / "issue-001" / "conformance-issue-001.md").write_text("```yaml\nverdict: conform\n```\n")
        (self.fdir / "progress.jsonl").write_text(json.dumps({"event": "merged", "issue": "issue-001", "commit": "abc"}) + "\n")
        self.cases = {"AC-1": command_case(), "AC-2": human_case()}
        self.write_plan()

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, status):
        (self.fdir / "issues" / "issue-001-x.md").write_text(f"---\nissue-id: issue-001\nstatus: {status}\n---\n# x\n")

    def write_plan(self):
        (self.fdir / "acceptance-plan.yaml").write_text(plan_text(self.cases))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def freeze(self):
        return self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt))

    def report(self):
        return self.run_cli("report", "--feature-dir", str(self.fdir), "--repo", str(self.wt))

    def check(self, phase, *extra, generate_report=True):
        if generate_report:
            self.report()
        return self.run_cli("check", "--phase", phase, "--feature-dir", str(self.fdir), "--repo", str(self.wt), *extra)

    @property
    def candidate(self):
        return json.loads((self.fdir / "integration" / "candidate.json").read_text())

    @property
    def tree(self):
        return self.candidate["candidate_tree"]

    def auto_result(self, aid="AC-1"):
        return {
            "id": aid, "oracle": "auto", "executor": "command", "status": "pass",
            "execution": {"executor": "command", "command": "python3 t.py", "exit": 0},
            "assertions": [{"type": "exit", "expected": 0, "actual": 0, "status": "pass"}],
        }

    def browser_result(self):
        artifact = self.write_artifact()
        return {
            "id": "AC-1", "oracle": "auto", "executor": "browser-agent", "status": "pass",
            "execution": {
                "executor": "browser-agent", "url": "http://127.0.0.1:8000/reports",
                "actions": ["打开页面", "点击筛选"], "console_errors": [], "page_errors": [],
            },
            "assertions": [{"type": "dom", "expected": "只有匹配项", "actual": "只有匹配项", "status": "pass"}],
            "artifacts": [artifact],
        }

    def human_result(self):
        return {"id": "AC-2", "oracle": "human", "executor": "human"}

    def write_artifact(self):
        path = self.fdir / "integration" / "artifacts" / "screen.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = b"png-evidence"
        path.write_bytes(content)
        return {
            "type": "screenshot", "path": "integration/artifacts/screen.png",
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def write_evidence(self, items, all_passed=True):
        integ = self.fdir / "integration"
        (integ / "evidence.md").write_text(
            f"```yaml\ncandidate_tree: {self.tree}\nall-passed: {'true' if all_passed else 'false'}\n"
            "ran:\n  direct: 3\n  indirect: 5\n  deferred: 1\n  quarantined: 0\n"
            "deferred:\n  - tools/tests/x.py：预算截断\n```\n")
        (integ / "conformance-feature.md").write_text("```yaml\nverdict: conform\n```\n")
        (integ / "business-results.json").write_text(json.dumps({
            "schema": "agent-dev.feature-business-results/v2", "candidate_tree": self.tree,
            "plan_sha256": self.candidate["acceptance_plan"]["sha256"],
            "environment": {"name": "integration-test", "url": "http://127.0.0.1:8000"},
            "items": items}))

    def confirm(self):
        (self.fdir / "integration" / "confirmation.md").write_text(
            f"```yaml\ncandidate_tree: {self.tree}\nconfirmed-by: user\nconfirmed-at: 2026-11-12 10:00\nquote: 确认合入\n```\n")

    # ---- 计划与冻结 ----
    def test_validate_plan(self):
        r = self.run_cli("validate-plan", "--feature-dir", str(self.fdir))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 条 AC", r.stdout)
        self.cases["AC-1"]["assertions"] = {}
        self.write_plan()
        r = self.run_cli("validate-plan", "--feature-dir", str(self.fdir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("自动判定必须写 assertions", r.stdout)

    def test_freeze_requires_plan(self):
        (self.fdir / "acceptance-plan.yaml").unlink()
        r = self.freeze()
        self.assertEqual(r.returncode, 1)
        self.assertIn("功能验收计划", r.stdout)

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

    def test_freeze_writes_candidate_and_plan_snapshot(self):
        r = self.freeze()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cand = self.candidate
        self.assertEqual(cand["candidate_tree"], sh(self.wt, "git", "rev-parse", "HEAD^{tree}"))
        self.assertEqual(cand["issues"][0]["merged_commit"], "abc")
        self.assertEqual(cand["acceptance_plan"]["cases"], ["AC-1", "AC-2"])
        snapshot = json.loads((self.fdir / "integration" / "acceptance-plan.json").read_text())
        self.assertEqual(set(snapshot["cases"]), {"AC-1", "AC-2"})

    def test_freeze_round_scopes_issues_and_branch(self):
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
        cfg = self.root / "dev" / "agents-config.md"
        cfg.write_text(CONFIG.replace("  mode: local-human\n", "  mode: local-human\n  since: 2026-11-09\n"))
        issue.write_text("---\nissue-id: issue-001\nfeature-slug: x\ncreated: 2026-11-01\nstatus: done\n---\n")
        self.assertEqual(self.run_cli("target", "--issue", str(issue)).stdout.strip(), "main")
        issue.write_text("---\nissue-id: issue-001\nfeature-slug: x\ncreated: 2026-11-10\nstatus: done\n---\n")
        self.assertEqual(self.run_cli("target", "--issue", str(issue)).stdout.strip(), "feat/x")

    def test_process_check_diffs_against_integration_branch(self):
        issue = self.fdir / "issues" / "issue-002-y.md"
        issue.write_text(f"---\nissue-id: issue-002\nfeature-slug: {SLUG}\nstatus: in-progress\n"
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

    # ---- 自动结果与覆盖对账 ----
    def test_verify_passes_and_records(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result()])
        r = self.check("verify", "--record")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("feature-verified", (self.fdir / "progress.jsonl").read_text())

    def test_verify_fails_on_new_commit_after_freeze(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result()])
        (self.wt / "src" / "a.py").write_text("x = 3\n")
        sh(self.wt, "git", "commit", "-qam", "late fix")
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("重新 freeze", r.stdout)

    def test_verify_fails_on_auto_failure(self):
        self.freeze()
        auto = self.auto_result()
        auto["status"] = "fail"
        auto.pop("execution")
        auto.pop("assertions")
        self.write_evidence([auto, self.human_result()])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("自动执行失败", r.stdout)

    def test_missing_and_extra_ac_fail(self):
        self.freeze()
        self.write_evidence([self.auto_result()])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("AC-2 在冻结验收计划中但没有结果", r.stdout)
        extra = self.auto_result("AC-99")
        self.write_evidence([self.auto_result(), self.human_result(), extra])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不在冻结验收计划中", r.stdout)

    def test_duplicate_ac_fails(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.auto_result(), self.human_result()])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("结果重复", r.stdout)

    def test_auto_pass_requires_real_assertions_and_command(self):
        self.freeze()
        auto = self.auto_result()
        auto["assertions"] = []
        auto["execution"].pop("command")
        self.write_evidence([auto, self.human_result()])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("缺实际执行命令", r.stdout)
        self.assertIn("截图或自述不能代替", r.stdout)
        report = (self.fdir / "integration" / "acceptance-report.md").read_text()
        self.assertIn("不可合并", report)
        self.assertNotIn("验收条件已满足，等待人的合并授权", report)

    def test_browser_pass_requires_assertions_screenshot_and_matching_hash(self):
        self.cases = {"AC-1": browser_case()}
        self.write_plan()
        self.freeze()
        browser = self.browser_result()
        self.write_evidence([browser])
        self.assertEqual(self.check("verify").returncode, 0)

        browser["assertions"] = []
        self.write_evidence([browser])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("截图或自述不能代替", r.stdout)

        browser = self.browser_result()
        browser["artifacts"][0]["sha256"] = "0" * 64
        self.write_evidence([browser])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("产物哈希不匹配", r.stdout)

        browser = self.browser_result()
        browser["artifacts"] = []
        self.write_evidence([browser])
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("type=screenshot", r.stdout)

    def test_verify_does_not_require_human(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result()])
        self.assertEqual(self.check("verify").returncode, 0)

    def test_verify_requires_current_report(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result()])
        r = self.check("verify", generate_report=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("acceptance-report.md 不存在", r.stdout)

    def test_plan_change_invalidates_results_and_report(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result()])
        self.report()
        with (self.fdir / "acceptance-plan.yaml").open("a") as fh:
            fh.write("# changed after freeze\n")
        r = self.check("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("验收计划在冻结后发生变化", r.stdout)

    # ---- 人工与分类调整 ----
    def test_merge_requires_human_and_confirmation(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result()])
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("人工结果 pending", r.stdout)
        self.assertIn("confirmation.md 不存在", r.stdout)

    def test_unverified_auto_must_be_waived(self):
        self.cases = {"AC-1": command_case()}
        self.write_plan()
        self.freeze()
        item = {"id": "AC-1", "oracle": "auto", "executor": "command", "status": "unverified",
                "reason": "环境不可用", "human": {"status": "confirmed", "by": "user"}}
        self.write_evidence([item])
        self.confirm()
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("只能由人显式 waived", r.stdout)
        item["human"] = {"status": "waived", "by": "user", "note": "合 main 后另行安排"}
        self.write_evidence([item])
        self.assertEqual(self.check("merge").returncode, 0)

    def test_auto_can_escalate_to_actual_human_test(self):
        self.cases = {"AC-1": browser_case()}
        self.write_plan()
        self.freeze()
        item = {
            "id": "AC-1", "oracle": "human", "executor": "human",
            "reclassification": {
                "from_oracle": "auto", "from_executor": "browser-agent",
                "to_oracle": "human", "to_executor": "human",
                "reason": "environment-unavailable", "note": "浏览器工具不可用，改由人按原计划实测",
            },
            "human": {"status": "confirmed", "by": "user"},
        }
        self.write_evidence([item])
        self.confirm()
        r = self.check("merge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        report = (self.fdir / "integration" / "acceptance-report.md").read_text()
        self.assertIn("AI 分类调整", report)
        self.assertIn("浏览器工具不可用", report)

    def test_human_cannot_be_downgraded_during_closeout(self):
        self.freeze()
        downgraded = self.auto_result("AC-2")
        downgraded["reclassification"] = {
            "from_oracle": "human", "from_executor": "human",
            "to_oracle": "auto", "to_executor": "command",
            "reason": "evidence-insufficient", "note": "试图减少人工项",
        }
        self.write_evidence([self.auto_result(), downgraded])
        self.confirm()
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不得自动降低人工要求", r.stdout)

    def test_merge_passes_and_main_drift_rules(self):
        self.freeze()
        human = self.human_result()
        human["human"] = {"status": "confirmed", "by": "user"}
        self.write_evidence([self.auto_result(), human])
        self.confirm()
        self.assertEqual(self.check("merge").returncode, 0)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "n.md").write_text("n\n")
        sh(self.root, "git", "add", "docs")
        sh(self.root, "git", "commit", "-qm", "docs")
        r = self.check("merge", "--main-repo", str(self.root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("main-drift-ignore 内", r.stdout)
        (self.root / "src" / "c.py").write_text("z = 1\n")
        sh(self.root, "git", "add", "src/c.py")
        sh(self.root, "git", "commit", "-qm", "code")
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("非忽略路径", r.stdout)

    def test_confirmation_bound_to_tree(self):
        self.freeze()
        self.write_evidence([self.auto_result(), dict(self.human_result(), human={"status": "confirmed", "by": "user"})])
        (self.fdir / "integration" / "confirmation.md").write_text(
            "```yaml\ncandidate_tree: deadbeef\nconfirmed-by: user\nconfirmed-at: x\nquote: ok\n```\n")
        r = self.check("merge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("须重新确认", r.stdout)

    def test_stale_or_hand_edited_report_fails(self):
        self.freeze()
        human = self.human_result()
        human["human"] = {"status": "confirmed", "by": "user"}
        self.write_evidence([self.auto_result(), human])
        self.confirm()
        self.report()
        report = self.fdir / "integration" / "acceptance-report.md"
        report.write_text(report.read_text().replace("已确认", "手工改写"))
        r = self.check("merge", generate_report=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("不得手改", r.stdout)

    # ---- 合并登记与报告 ----
    def test_record_merge(self):
        self.freeze()
        cand = self.candidate["candidate"]
        sh(self.root, "git", "merge", "-q", "--no-ff", f"feat/{SLUG}", "-m", "merge")
        r = self.run_cli("record-merge", "--feature-dir", str(self.fdir), "--repo", str(self.root), "--commit", "HEAD")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("feature-merged", (self.fdir / "progress.jsonl").read_text())
        r = self.run_cli("record-merge", "--feature-dir", str(self.fdir), "--repo", str(self.root), "--commit", "HEAD~1")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(cand)

    def test_report_has_pending_then_final_human_result(self):
        self.freeze()
        human = self.human_result()
        self.write_evidence([self.auto_result(), human])
        r = self.report()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("功能验收报告：demo", r.stdout)
        self.assertIn("等待人工处理", r.stdout)
        self.assertIn("domain-judgment", r.stdout)
        self.assertIn("延期兜底", r.stdout)
        self.assertNotIn("全绿", r.stdout)
        human["human"] = {"status": "confirmed", "by": "user"}
        self.write_evidence([self.auto_result(), human])
        self.confirm()
        r = self.report()
        self.assertIn("合并确认已记录", r.stdout)
        self.assertIn("已确认", r.stdout)


if __name__ == "__main__":
    unittest.main()
