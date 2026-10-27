#!/usr/bin/env python3
"""progress_cleanup 安全测试：软链不跟随、实体 dev 抢救、去重、合并目标与确认指纹。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import progress
import progress_cleanup as pc


class CleanupFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        (self.repo / ".gitignore").write_text("dev/\n.logs/\nnode_modules/\n.worktrees/\n", encoding="utf-8")
        (self.repo / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / "dev").mkdir()
        (self.repo / "dev" / "agents-config.md").write_text("main-branch: main\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "init")
        self.feature_dir = self.repo / "dev" / "features" / "mod" / "slug"
        self.impl_dir = self.feature_dir / "impl" / "issue-001"
        self.impl_dir.mkdir(parents=True)
        (self.impl_dir / "impl-log-issue-001.md").write_text(
            "# impl-log\n保留证据 keep.orig\n", encoding="utf-8")
        (self.impl_dir / "keep.orig").write_text("referenced\n", encoding="utf-8")
        (self.impl_dir / "old.orig").write_text("delete me\n", encoding="utf-8")
        merge_dir = self.impl_dir / "merge-temp"
        merge_dir.mkdir()
        (merge_dir / "junk.txt").write_text("delete me too\n", encoding="utf-8")
        self.write_issue("issue-001", "done")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              check=True, capture_output=True, text=True)

    def write_issue(self, issue_id, status):
        issues = self.feature_dir / "issues"
        issues.mkdir(parents=True, exist_ok=True)
        (issues / f"{issue_id}-sample.md").write_text(
            f"---\nfeature-slug: slug\nissue-id: {issue_id}\ntitle: 测试票\nstatus: {status}\n---\n",
            encoding="utf-8")

    def ticket(self, status="done"):
        return {"id": "issue-001", "key": "issue-001-sample", "title": "测试票",
                "status": status, "feature": "mod/slug", "feature_dir": str(self.feature_dir)}

    def merged_worktree(self, branch="feat/slug-issue-001"):
        self.git("checkout", "-q", "-b", branch)
        (self.repo / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "feature")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", branch, "-m", "merge")
        wt = self.repo / ".worktrees" / "issue-001"
        self.git("worktree", "add", "-q", str(wt), branch)
        return branch, wt

    def plan(self, branch, wt, status="done"):
        return pc.make_plan(self.repo, branch, self.ticket(status), str(wt), ["main"], "main")

    def test_real_dev_salvage_dedup_and_dev_retention(self):
        branch, wt = self.merged_worktree()
        (wt / "code.py").write_text("VALUE = 99\n", encoding="utf-8")
        raw = wt / "dev" / "features" / "mod" / "slug" / "impl" / "issue-001"
        raw.mkdir(parents=True)
        (raw / "unique.txt").write_text("unique evidence\n", encoding="utf-8")
        (raw / "dup-a.txt").write_text("same bytes\n", encoding="utf-8")
        (raw / "dup-b.txt").write_text("same bytes\n", encoding="utf-8")
        (raw / "same-as-main.txt").write_text("already collected\n", encoding="utf-8")
        (self.impl_dir / "same-as-main.txt").write_text("already collected\n", encoding="utf-8")
        (wt / ".logs").mkdir()
        (wt / ".logs" / "temp.log").write_text("reproducible\n", encoding="utf-8")
        (wt / "node_modules" / "pkg").mkdir(parents=True)
        (wt / "node_modules" / "pkg" / "index.js").write_text("cache\n", encoding="utf-8")

        plan = self.plan(branch, wt)
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertTrue(plan["delete_branch"])
        self.assertEqual(plan["salvage"]["files"], 4)  # unique + 2 dup + modified code
        self.assertEqual(plan["salvage"]["unique_files"], 3)
        self.assertEqual(plan["dev_cleanup"]["files"], 2)  # old.orig + merge-temp/junk
        result = pc.execute_plan(plan)
        self.assertTrue(result["worktree_removed"])
        self.assertTrue(result["branch_deleted"])
        self.assertFalse(wt.exists())
        missing = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--verify", branch],
                                 capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        salvage = Path(result["salvage_dir"])
        self.assertTrue((salvage / "files" / "dev/features/mod/slug/impl/issue-001/unique.txt").is_file())
        self.assertTrue((salvage / "uncommitted.patch").is_file())
        objects = list((salvage / "objects").iterdir())
        self.assertEqual(len(objects), 3)
        a = salvage / "files" / "dev/features/mod/slug/impl/issue-001/dup-a.txt"
        b = salvage / "files" / "dev/features/mod/slug/impl/issue-001/dup-b.txt"
        self.assertEqual(os.stat(a).st_ino, os.stat(b).st_ino)
        manifest = json.loads((salvage / "_manifest.json").read_text())
        self.assertNotIn("same-as-main.txt", json.dumps(manifest))
        self.assertNotIn("temp.log", json.dumps(manifest))
        self.assertFalse((self.impl_dir / "old.orig").exists())
        self.assertFalse((self.impl_dir / "merge-temp").exists())
        self.assertTrue((self.impl_dir / "keep.orig").is_file())
        self.assertTrue((self.impl_dir / "impl-log-issue-001.md").is_file())

    def test_dev_symlink_never_followed(self):
        branch, wt = self.merged_worktree()
        os.symlink("../../dev", wt / "dev")
        plan = self.plan(branch, wt)
        self.assertTrue(plan["salvage"]["dev_symlink"])
        self.assertEqual(plan["salvage"]["files"], 0)
        result = pc.execute_plan(plan)
        self.assertEqual(result["salvaged_files"], 0)
        self.assertTrue((self.impl_dir / "impl-log-issue-001.md").is_file())
        self.assertTrue((self.impl_dir / "keep.orig").is_file())

    def test_unmerged_branch_is_retained(self):
        branch, wt = self.merged_worktree()
        # 再造一个未合并提交，分支保留，worktree 可清
        self.git("checkout", "-q", branch, cwd=wt)
        (wt / "later.py").write_text("LATER = True\n", encoding="utf-8")
        self.git("add", ".", cwd=wt)
        self.git("commit", "-q", "-m", "not merged", cwd=wt)
        plan = self.plan(branch, wt)
        self.assertFalse(plan["delete_branch"])
        self.assertIn("保留本地分支", "；".join(plan["warnings"]))
        result = pc.execute_plan(plan)
        self.assertTrue(result["worktree_removed"])
        self.assertFalse(result["branch_deleted"])
        self.assertEqual(self.git("rev-parse", "--verify", branch).returncode, 0)

    def test_proposed_ticket_not_eligible(self):
        branch, wt = self.merged_worktree()
        plan = self.plan(branch, wt, status="proposed")
        self.assertFalse(plan["eligible"])
        with self.assertRaises(pc.CleanupError):
            pc.execute_plan(plan)
        self.assertTrue(wt.exists())

    def add_unmerged_commit(self, branch, wt, name="later.py", content="LATER = True\n"):
        self.git("checkout", "-q", branch, cwd=wt)
        (wt / name).write_text(content, encoding="utf-8")
        self.git("add", ".", cwd=wt)
        self.git("commit", "-q", "-m", "paused work", cwd=wt)

    def test_abandoned_discard_saves_full_patch(self):
        branch, wt = self.merged_worktree()
        self.add_unmerged_commit(branch, wt)
        (wt / "code.py").write_text("VALUE = 99\n", encoding="utf-8")
        plan = self.plan(branch, wt, status="in-progress")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertEqual(plan["mode"], "abandon")
        result = pc.execute_plan(plan, "discard")
        self.assertTrue(result["worktree_removed"])
        self.assertTrue(result["branch_deleted"])
        self.assertTrue(result["branch_patch_saved"])
        salvage = Path(result["salvage_dir"])
        self.assertIn("LATER = True", (salvage / "branch.patch").read_text())
        self.assertIn("VALUE = 99", (salvage / "uncommitted.patch").read_text())
        self.assertFalse(wt.exists())
        missing = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--verify", branch], capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        subprocess.run(["git", "-C", str(self.repo), "apply", "--check", str(salvage / "branch.patch")],
                       check=True, capture_output=True)

    def test_abandoned_merge_preserve_merges_then_cleans(self):
        branch, wt = self.merged_worktree()
        self.add_unmerged_commit(branch, wt)
        (wt / "code.py").write_text("VALUE = 99\n", encoding="utf-8")
        plan = self.plan(branch, wt, status="in-progress")
        result = pc.execute_plan(plan, "merge")
        self.assertEqual(result["merged_into"], "main")
        self.assertTrue(result["merge_commit"])
        self.assertTrue(result["worktree_removed"])
        self.assertTrue(result["branch_deleted"])
        self.assertTrue((self.repo / "later.py").is_file())
        self.assertFalse(wt.exists())
        self.assertTrue((Path(result["salvage_dir"]) / "branch.patch").is_file())
        self.assertTrue((Path(result["salvage_dir"]) / "uncommitted.patch").is_file())

    def test_merge_conflict_preserves_original_worktree_and_branch(self):
        branch, wt = self.merged_worktree()
        self.add_unmerged_commit(branch, wt, "conflict.py", "BRANCH = True\n")
        (self.repo / "conflict.py").write_text("MAIN = True\n", encoding="utf-8")
        self.git("add", "conflict.py")
        self.git("commit", "-q", "-m", "main conflicts")
        plan = self.plan(branch, wt, status="in-progress")
        result = pc.execute_plan(plan, "merge")
        self.assertFalse(result["ok"])
        self.assertIn("原 worktree 与分支保留", result["error"])
        self.assertTrue(wt.exists())
        self.assertEqual(self.git("rev-parse", "--verify", branch).returncode, 0)
        self.assertEqual((self.repo / "conflict.py").read_text(), "MAIN = True\n")
        self.assertTrue(result["salvage_dir"])
        self.assertTrue((Path(result["salvage_dir"]) / "branch.patch").is_file())

    def test_changed_content_invalidates_confirmation(self):
        branch, wt = self.merged_worktree()
        first = self.plan(branch, wt)
        (wt / "dev" / "new-evidence.txt").parent.mkdir(parents=True, exist_ok=True)
        (wt / "dev" / "new-evidence.txt").write_text("changed\n", encoding="utf-8")
        second = self.plan(branch, wt)
        self.assertNotEqual(first["confirm"], second["confirm"])
        args = SimpleNamespace(repo=str(self.repo), branch=branch, apply=True,
                               confirm=first["confirm"])
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            progress.cleanup_command(args)
        self.assertIn("重新预检", out.getvalue())
        self.assertTrue(wt.exists())
    def test_issue_key_resolves_branch_for_preview(self):
        branch, wt = self.merged_worktree()
        plan = progress.cleanup_plan(self.repo, issue="issue-001-sample")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertEqual(plan["branch"], branch)
        self.assertEqual(plan["issue"], "issue-001")

    def test_issue_only_dev_cleanup_without_branch(self):
        self.write_issue("issue-002", "needs-human")
        impl = self.feature_dir / "impl" / "issue-002"
        impl.mkdir()
        (impl / "impl-log.md").write_text("# impl-log\n保留 keep.orig\n", encoding="utf-8")
        (impl / "keep.orig").write_text("referenced\n", encoding="utf-8")
        (impl / "old.orig").write_text("delete\n", encoding="utf-8")
        plan = progress.cleanup_plan(self.repo, issue="issue-002-sample")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertEqual(plan["mode"], "dev-only")
        self.assertIsNone(plan["branch"])
        self.assertIn("仅清理 dev", "；".join(plan["will"]))
        args = SimpleNamespace(repo=str(self.repo), branch=None, issue="issue-002-sample",
                               apply=True, confirm=plan["confirm"], action="dev-only")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            progress.cleanup_command(args)
        result = json.loads(out.getvalue())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "dev-only")
        self.assertEqual(result["dev_files_removed"], 1)
        self.assertFalse(result["worktree_removed"])
        self.assertFalse(result["branch_deleted"])
        self.assertFalse((impl / "old.orig").exists())
        self.assertTrue((impl / "keep.orig").is_file())
        self.assertTrue((impl / "impl-log.md").is_file())
        event = json.loads((self.feature_dir / "progress.jsonl").read_text().splitlines()[-1])
        self.assertIn("仅 dev", event["note"])
        self.assertNotIn("None", event["note"])

    def test_issue_only_requires_dev_candidates(self):
        self.write_issue("issue-002", "needs-human")
        plan = progress.cleanup_plan(self.repo, issue="issue-002-sample")
        self.assertFalse(plan["eligible"])
        self.assertIn("dev", plan["reason"])

    def test_proposed_issue_only_not_eligible(self):
        self.write_issue("issue-002", "proposed")
        impl = self.feature_dir / "impl" / "issue-002"
        impl.mkdir()
        (impl / "old.orig").write_text("delete\n", encoding="utf-8")
        plan = progress.cleanup_plan(self.repo, issue="issue-002-sample")
        self.assertFalse(plan["eligible"])
        self.assertIn("proposed", plan["reason"])

    def test_close_issue_marks_deferred_without_unblocking_dependents(self):
        self.write_issue("issue-002", "needs-human")
        self.write_issue("issue-003", "confirmed")
        dependent = self.feature_dir / "issues" / "issue-003-sample.md"
        dependent.write_text(dependent.read_text().replace(
            "status: confirmed", "status: confirmed\nblocked-by: [issue-002]"), encoding="utf-8")
        args = SimpleNamespace(repo=str(self.repo), issue="issue-002-sample", reason="中途测试关闭")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            progress.close_command(args)
        result = json.loads(out.getvalue())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["previous_status"], "needs-human")
        self.assertEqual(result["status"], "deferred")
        text = (self.feature_dir / "issues" / "issue-002-sample.md").read_text()
        self.assertIn("status: deferred", text)
        issues = progress.load_issues(self.repo, self.feature_dir, [], {})
        child = next(it for it in issues if it["id"] == "issue-003")
        self.assertFalse(child["ready"])
        event = json.loads((self.feature_dir / "progress.jsonl").read_text().splitlines()[-1])
        self.assertEqual(event["event"], "digest")
        self.assertIn("中途测试关闭", event["note"])

    def test_close_issue_rejects_done(self):
        args = SimpleNamespace(repo=str(self.repo), issue="issue-001-sample", reason="重复关闭")
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            progress.close_command(args)
        self.assertIn("无需重复关闭", out.getvalue())
        self.assertIn("status: done", (self.feature_dir / "issues" / "issue-001-sample.md").read_text())

    def test_close_issue_supports_yaml_fence(self):
        path = self.feature_dir / "issues" / "issue-004-fenced.md"
        path.write_text("# 非标票\n\n```yaml\nissue-id: issue-004\nstatus: in-progress\n```\n", encoding="utf-8")
        args = SimpleNamespace(repo=str(self.repo), issue="issue-004-fenced", reason="关闭非标票")
        with contextlib.redirect_stdout(io.StringIO()):
            progress.close_command(args)
        text = path.read_text()
        self.assertIn("status: deferred", text)
        self.assertNotIn("status: in-progress", text)

    def test_active_descendant_blocks_cleanup(self):
        parent, wt = self.merged_worktree()
        self.write_issue("issue-002", "in-progress")
        child = "feat/slug-issue-002"
        self.git("checkout", "-q", "-b", child, parent)
        (self.repo / "child.py").write_text("CHILD = True\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "active child")
        self.git("checkout", "-q", "main")
        child_wt = self.repo / ".worktrees" / "issue-002"
        self.git("worktree", "add", "-q", str(child_wt), child)
        plan = progress.cleanup_plan(self.repo, parent)
        self.assertFalse(plan["eligible"])
        self.assertIn("issue-002", plan["reason"])
        self.assertTrue(wt.exists())
        self.assertTrue(child_wt.exists())


if __name__ == "__main__":
    unittest.main()
