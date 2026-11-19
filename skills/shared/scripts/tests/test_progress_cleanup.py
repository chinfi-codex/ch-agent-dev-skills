#!/usr/bin/env python3
"""v2 authorized cleanup: no merge, full snapshots, Saga crash recovery and refs."""
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
from unittest import mock

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
        (self.repo / "dev" / "agents-config.md").write_text(
            """```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'true'}
host-budget: {total: 2, max-runs: 1, queue-timeout: 30}
verify-policy:
  ticket-max-files: 10
  ticket-max-loc: 400
  deny: []
  test-change: []
  generated-refresh: []
  base-freeze: true
  attribution: two-sided
  gate-scope: {categories-file: null, non-gating: [], env-skip: true}
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
""", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "init")
        self.feature_dir = self.repo / "dev" / "features" / "mod" / "slug"
        self.impl_dir = self.feature_dir / "impl" / "issue-001"
        self.impl_dir.mkdir(parents=True)
        (self.impl_dir / "impl-log-issue-001.md").write_text("# impl-log\n保留 keep.orig\n", encoding="utf-8")
        (self.impl_dir / "keep.orig").write_text("referenced\n", encoding="utf-8")
        (self.impl_dir / "old.orig").write_text("delete me\n", encoding="utf-8")
        self.write_issue("issue-001", "done")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              check=check, capture_output=True, text=True)

    def write_issue(self, issue_id, status, extra=""):
        issues = self.feature_dir / "issues"
        issues.mkdir(parents=True, exist_ok=True)
        (issues / f"{issue_id}-sample.md").write_text(
            f"---\nfeature-slug: slug\nissue-id: {issue_id}\ntitle: 测试票\nstatus: {status}\n{extra}---\n",
            encoding="utf-8")

    def merged_worktree(self, branch="feat/slug-issue-001"):
        self.git("checkout", "-q", "-b", branch)
        (self.repo / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "feature")
        source = self.git("rev-parse", branch).stdout.strip()
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", branch, "-m", "merge")
        merge = self.git("rev-parse", "HEAD").stdout.strip()
        wt = self.repo / ".worktrees" / "issue-001"
        self.git("worktree", "add", "-q", str(wt), branch)
        return branch, wt, source, merge

    def plan(self, status="done", intent="close", issue="issue-001-sample"):
        self.write_issue("issue-001", status)
        return progress.cleanup_plan(self.repo, issue=issue, intent=intent)

    def apply(self, plan):
        return pc.apply_operation(self.repo, plan["operation_id"], plan["confirm"])

    def add_unmerged_commit(self, branch, wt):
        self.git("checkout", "-q", branch, cwd=wt)
        (wt / "later.py").write_text("LATER = True\n")
        self.git("add", ".", cwd=wt)
        self.git("commit", "-q", "-m", "paused work", cwd=wt)

    def test_legacy_merge_execute_and_routes_are_removed(self):
        self.assertFalse(hasattr(pc, "_merge_preserve"))
        self.assertFalse(hasattr(pc, "execute_plan"))
        self.assertFalse(hasattr(progress, "record_merged_event"))
        for command in ("cleanup", "close"):
            result = subprocess.run([sys.executable, "-I", str(SCRIPT_DIR / "progress.py"), command,
                                     "--repo", str(self.repo)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, "-I", str(SCRIPT_DIR / "progress.py"), "close-and-clean",
                                 "--repo", str(self.repo), "--issue", "issue-001", "--intent", "merge"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_salvage_logs_dedup_and_dev_retention(self):
        branch, wt, _, _ = self.merged_worktree()
        (wt / "code.py").write_text("VALUE = 99\n")
        raw = wt / "dev" / "features" / "mod" / "slug" / "impl" / "issue-001"
        raw.mkdir(parents=True)
        (raw / "unique.txt").write_text("unique evidence\n")
        (raw / "dup-a.txt").write_text("same bytes\n")
        (raw / "dup-b.txt").write_text("same bytes\n")
        (raw / "same-as-main.txt").write_text("already collected\n")
        (self.impl_dir / "same-as-main.txt").write_text("already collected\n")
        (wt / ".logs").mkdir()
        (wt / ".logs" / "active.log").write_text("must salvage\n")
        (wt / "node_modules" / "pkg").mkdir(parents=True)
        (wt / "node_modules" / "pkg" / "index.js").write_text("cache\n")
        plan = self.plan()
        self.assertTrue(plan["eligible"], plan.get("reason"))
        import json_schema
        errors = json_schema.load_validate(SCRIPT_DIR.parent / "references/cleanup-operation.schema.json", plan["_auth"])
        self.assertEqual(errors, [])
        self.assertEqual(plan["salvage"]["files"], 5)
        self.assertEqual(plan["salvage"]["logs"], 1)
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "completed")
        self.assertFalse(wt.exists())
        salvage = Path(result["salvage_dir"])
        self.assertEqual(salvage.name, "issue-001-" + plan["operation_id"])
        self.assertTrue((salvage / "files/.logs/active.log").is_file())
        self.assertTrue((salvage / "uncommitted.patch").is_file())
        objects = list((salvage / "objects").iterdir())
        self.assertEqual(len(objects), 4)
        a = salvage / "files/dev/features/mod/slug/impl/issue-001/dup-a.txt"
        b = salvage / "files/dev/features/mod/slug/impl/issue-001/dup-b.txt"
        self.assertEqual(os.stat(a).st_ino, os.stat(b).st_ino)
        manifest = json.loads((salvage / "_manifest.json").read_text())
        self.assertNotIn("same-as-main.txt", json.dumps(manifest["files"]))
        self.assertNotIn("node_modules", json.dumps(manifest["files"]))
        self.assertFalse((self.impl_dir / "old.orig").exists())
        self.assertTrue((self.impl_dir / "keep.orig").is_file())
        events = [json.loads(line) for line in (self.feature_dir / "progress.jsonl").read_text().splitlines()]
        self.assertEqual(sum(event["event"] == "close-clean" for event in events), 1)

    def test_relative_and_absolute_dev_symlink_are_valid(self):
        for absolute in (False, True):
            with self.subTest(absolute=absolute):
                self.tearDown(); self.setUp()
                _, wt, _, _ = self.merged_worktree()
                os.symlink(str(self.repo / "dev") if absolute else "../../dev", wt / "dev")
                plan = self.plan()
                self.assertTrue(plan["eligible"], plan.get("reason"))
                self.assertTrue(plan["salvage"]["dev_symlink"]["valid"])
                result = self.apply(plan)
                self.assertTrue(result["ok"], result)
                self.assertIsNone(result["salvage_dir"])

    def test_wrong_or_broken_dev_symlink_fails_before_side_effects(self):
        for target in (str(self.repo), str(self.repo / "missing-dev")):
            with self.subTest(target=target):
                self.tearDown(); self.setUp()
                _, wt, _, _ = self.merged_worktree()
                os.symlink(target, wt / "dev")
                plan = self.plan()
                self.assertFalse(plan["eligible"])
                self.assertTrue(plan["protected"])
                self.assertTrue(wt.exists())

    def test_close_requires_done_and_real_merge(self):
        branch, wt, _, _ = self.merged_worktree()
        self.add_unmerged_commit(branch, wt)
        plan = self.plan()
        self.assertFalse(plan["eligible"])
        self.assertIn("done 不等于已合并", plan["reason"])
        plan = self.plan(status="in-progress")
        self.assertFalse(plan["eligible"])

    def test_merged_ticket_is_not_cleanup_done(self):
        _, wt, _, _ = self.merged_worktree()
        plan = self.plan(status="merged")
        self.assertFalse(plan["eligible"])
        self.assertIn("真实 done", plan["reason"])
        self.assertTrue(wt.exists())

    def test_discard_saves_patch_then_defers_without_unlocking(self):
        branch, wt, _, _ = self.merged_worktree()
        self.add_unmerged_commit(branch, wt)
        (wt / "code.py").write_text("VALUE = 99\n")
        self.write_issue("issue-002", "confirmed", extra="blocked-by: [issue-001]\n")
        plan = self.plan(status="in-progress", intent="discard")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "deferred")
        salvage = Path(result["salvage_dir"])
        self.assertIn("LATER = True", (salvage / "branch.patch").read_text())
        self.assertIn("VALUE = 99", (salvage / "uncommitted.patch").read_text())
        issues = progress.load_issues(self.repo, self.feature_dir, [], {})
        child = next(item for item in issues if item["id"] == "issue-002")
        self.assertFalse(child["ready"])

    def test_dev_only_is_internal_and_requires_no_code_runtime_or_sessions(self):
        self.write_issue("issue-002", "needs-human")
        impl = self.feature_dir / "impl" / "issue-002"
        impl.mkdir()
        (impl / "old.orig").write_text("delete\n")
        plan = progress.cleanup_plan(self.repo, issue="issue-002", intent="discard")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertNotIn("mode", plan)
        self.assertIsNone(plan["resources"]["local_ref"])
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "deferred")
        self.assertFalse((impl / "old.orig").exists())

    def test_missing_branch_is_not_merge_proof_but_exact_record_is(self):
        branch, wt, source, merge = self.merged_worktree("feat/custom-issue-001")
        self.git("worktree", "remove", "--force", str(wt))
        self.git("branch", "-D", branch)
        (self.impl_dir / "old2.orig").write_text("delete\n")
        plan = self.plan()
        self.assertFalse(plan["eligible"])
        self.assertNotEqual(plan.get("reason", "").find("merged"), -1)
        event = {"event": "merged", "issue": "issue-001", "branch": branch,
                 "target": "main", "host": "zcode", "source_head": source, "commit": merge}
        (self.feature_dir / "progress.jsonl").write_text(json.dumps(event) + "\n")
        plan = self.plan()
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertEqual(plan["merge_proof"]["kind"], "record")
        self.assertEqual(plan["merge_proof"]["host"], "zcode")
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)

    def test_target_branch_never_falls_back_to_main(self):
        branch, wt, _, _ = self.merged_worktree()
        # Integration forked before the ticket merge: main ancestry must not pass.
        self.git("branch", "integration", "main^1")
        self.write_issue("issue-001", "done", extra="target-branch: integration\n")
        plan = progress.cleanup_plan(self.repo, issue="issue-001", intent="close")
        self.assertFalse(plan["eligible"])
        self.assertIn("done 不等于已合并", plan["reason"])
        self.git("checkout", "-q", "integration")
        self.git("merge", "-q", "--no-ff", branch, "-m", "merge target")
        plan = progress.cleanup_plan(self.repo, issue="issue-001", intent="close")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertEqual(plan["target_branch"], "integration")

    def test_remote_only_and_detached_never_become_dev_only(self):
        self.write_issue("issue-002", "in-progress")
        impl = self.feature_dir / "impl" / "issue-002"; impl.mkdir()
        (impl / "old.orig").write_text("delete\n")
        wt = self.repo / ".worktrees" / "issue-002"
        self.git("worktree", "add", "-q", "--detach", str(wt), "main")
        plan = progress.cleanup_plan(self.repo, issue="issue-002", intent="discard")
        self.assertFalse(plan["eligible"])
        self.assertIn("detached", plan["reason"])

    def test_active_descendant_and_protected_branches_are_retained(self):
        parent, wt, _, _ = self.merged_worktree()
        self.write_issue("issue-002", "in-progress")
        child = "feat/slug-issue-002"
        self.git("checkout", "-q", "-b", child, parent)
        (self.repo / "child.py").write_text("CHILD = True\n")
        self.git("add", "."); self.git("commit", "-q", "-m", "child")
        self.git("checkout", "-q", "main")
        self.git("worktree", "add", "-q", str(self.repo / ".worktrees/issue-002"), child)
        plan = self.plan()
        self.assertFalse(plan["eligible"])
        self.assertIn("issue-002", plan["reason"])
        self.assertTrue(wt.exists())
        resources = pc.discover_resources(self.repo)
        self.assertTrue(resources["worktrees"][0]["main"])

    def test_proposed_and_backup_never_get_execution(self):
        self.write_issue("issue-002", "proposed")
        impl = self.feature_dir / "impl" / "issue-002"; impl.mkdir()
        (impl / "old.orig").write_text("delete\n")
        plan = progress.cleanup_plan(self.repo, issue="issue-002", intent="discard")
        self.assertFalse(plan["eligible"])
        self.assertIsNone(plan["operation_id"])
        oid = self.git("rev-parse", "main").stdout.strip()
        self.git("update-ref", "refs/heads/backup/slug-issue-002", oid)
        self.write_issue("issue-002", "in-progress")
        plan = progress.cleanup_plan(self.repo, issue="issue-002", intent="discard")
        self.assertFalse(plan["eligible"])

    def test_remote_only_branch_blocks_dev_only(self):
        self.write_issue("issue-002", "in-progress")
        impl = self.feature_dir / "impl" / "issue-002"; impl.mkdir()
        (impl / "old.orig").write_text("delete\n")
        oid = self.git("rev-parse", "main").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/feat/slug-issue-002", oid)
        plan = progress.cleanup_plan(self.repo, issue="issue-002", intent="discard")
        self.assertFalse(plan["eligible"])
        self.assertIn("remote-only", plan["reason"])
        self.assertTrue((impl / "old.orig").exists())

    def test_detached_recovers_only_from_exact_authorized_journal(self):
        _, wt, _, _ = self.merged_worktree()
        plan = self.plan()
        self.git("checkout", "-q", "--detach", cwd=wt)
        result = pc.reconcile_operations(self.repo, plan["operation_id"], apply=True)["operations"][0]
        self.assertTrue(result["ok"], result)
        self.assertFalse(wt.exists())

    def test_confirm_change_and_lock_contention_are_pre_side_effect(self):
        _, wt, _, _ = self.merged_worktree()
        plan = self.plan()
        (wt / "node_modules").mkdir()
        (wt / "node_modules" / "new.txt").write_text("changed ignored\n")
        with self.assertRaises(pc.StaleOperation):
            self.apply(plan)
        self.assertTrue(wt.exists())
        self.tearDown(); self.setUp()
        _, wt, _, _ = self.merged_worktree()
        plan = self.plan()
        with pc._operation_lock(self.repo):
            with self.assertRaises(pc.CleanupError) as caught:
                self.apply(plan)
        self.assertEqual(caught.exception.code, "conflict")
        self.assertTrue(wt.exists())

    def test_crash_after_each_side_effect_converges_without_duplicate_event(self):
        for step, function in (("salvaged", "_salvage"), ("worktree-removed", "_remove_worktrees"),
                               ("refs-updated", "_update_refs"), ("dev-compacted", "_compact_dev_and_account")):
            with self.subTest(step=step):
                self.tearDown(); self.setUp()
                _, wt, _, _ = self.merged_worktree()
                (wt / ".logs").mkdir(); (wt / ".logs" / "x.log").write_text("x\n")
                plan = self.plan()
                original = getattr(pc, function)
                def crash(*args, __original=original, **kwargs):
                    __original(*args, **kwargs)
                    raise pc.CleanupError("injected post-side-effect crash")
                with mock.patch.object(pc, function, side_effect=crash):
                    result = self.apply(plan)
                self.assertEqual(result["state"], "needs-attention")
                journal = pc.OperationJournal.load(pc.operation_path(self.repo, plan["operation_id"]))
                self.assertEqual(journal.current_step, step)
                reconciled = pc.reconcile_operations(self.repo, plan["operation_id"], apply=True)["operations"][0]
                self.assertTrue(reconciled["ok"], reconciled)
                events = [json.loads(line) for line in (self.feature_dir / "progress.jsonl").read_text().splitlines()]
                self.assertEqual(sum(event.get("event") == "close-clean" for event in events), 1)

    def test_late_warning_never_completes(self):
        _, _, _, _ = self.merged_worktree()
        plan = self.plan()
        original = pc._compact_dev_and_account
        def warn(*args, **kwargs):
            observed = original(*args, **kwargs)
            observed["warnings"].append("late warning")
            return observed
        with mock.patch.object(pc, "_compact_dev_and_account", side_effect=warn):
            result = self.apply(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "needs-attention")
        journal = pc.OperationJournal.load(pc.operation_path(self.repo, plan["operation_id"]))
        self.assertNotEqual(journal.state, "completed")

    def test_log_growth_and_unstopped_service_block_deletion(self):
        _, wt, _, _ = self.merged_worktree()
        (wt / ".logs").mkdir(); log = wt / ".logs/growing.log"; log.write_text("a\n")
        plan = self.plan()
        log.write_text("a\ngrew\n")
        with self.assertRaises(pc.StaleOperation):
            self.apply(plan)
        self.assertTrue(wt.exists())
        self.tearDown(); self.setUp()
        _, wt, source, _ = self.merged_worktree()
        import integration_runtime
        service = {"schema": "agent-dev.dev-service/v2", "service_id": "service-live",
                   "repo": str(self.repo.resolve()), "feature_dir": str(self.feature_dir.resolve()),
                   "round": None, "candidate": source, "worktree": str(wt.resolve()),
                   "log_generation": "g1", "machine_id": integration_runtime._machine(),
                   "state": "ready", "pid": None, "started_at": "2026-01-01T00:00:00Z",
                   "ready_at": "2026-01-01T00:00:00Z", "stopped_at": None,
                   "log_path": str(wt / ".logs/service.log"), "revision": 1}
        path = integration_runtime._service_path(self.repo, "service-live")
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(service))
        plan = self.plan()
        self.assertFalse(plan["eligible"])
        self.assertIn("未停止", plan["reason"])
        self.assertTrue(wt.exists())

    def test_exact_upstream_unchanged_is_deleted_with_lease(self):
        branch, wt, _, _ = self.merged_worktree()
        remote = Path(self.tmp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "-u", "origin", branch)
        plan = self.plan()
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/" + branch).stdout, "")

    def test_remote_oid_change_is_needs_attention(self):
        branch, wt, _, _ = self.merged_worktree()
        remote = Path(self.tmp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "-u", "origin", branch)
        plan = self.plan()
        with mock.patch.object(pc, "_remote_oid", return_value="0" * 40):
            result = self.apply(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "needs-attention")
        self.assertIn("远端 OID 已变化", "；".join(result["warnings"]))
        remote_oid = self.git("ls-remote", "origin", "refs/heads/" + branch).stdout
        self.assertIn("refs/heads/", remote_oid)

    def test_feature_merged_runtime_salvage_and_integration_ref_cleanup(self):
        import integration_runtime
        (self.repo / "dev/agents-config.md").write_text("""```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'true'}
host-budget: {total: 2, max-runs: 1, queue-timeout: 30}
verify-policy:
  ticket-max-files: 10
  ticket-max-loc: 400
  deny: []
  test-change: []
  generated-refresh: []
  base-freeze: true
  attribution: two-sided
  gate-scope: {categories-file: null, non-gating: [], env-skip: true}
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
feature-closeout:
  mode: local-human
  verify-instance:
    name: test
    deploy: "true"
    lease: .agent-dev/lease.json
integration-runtime:
  runtime-dir: .agent-dev/runtime
  ready-timeout-seconds: 2
  stop-timeout-seconds: 2
  dev-service:
    start: "exec python3 -c 'import time; time.sleep(30)'"
    ready: "true"
    stop: "kill \\"$AGENT_DEV_SERVICE_PID\\""
    status: "kill -0 \\"$AGENT_DEV_SERVICE_PID\\""
```
""", encoding="utf-8")
        branch = "feat/slug"
        self.git("checkout", "-q", "-b", branch)
        (self.repo / "feature.py").write_text("FEATURE = True\n")
        self.git("add", "."); self.git("commit", "-q", "-m", "feature")
        candidate = self.git("rev-parse", "HEAD").stdout.strip()
        tree = self.git("rev-parse", "HEAD^{tree}").stdout.strip()
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", branch, "-m", "feature merge")
        merged = self.git("rev-parse", "HEAD").stdout.strip()
        worktree = self.repo / ".worktrees/integration-slug"
        self.git("worktree", "add", "-q", str(worktree), branch)
        integration = self.feature_dir / "integration"; integration.mkdir()
        (integration / "candidate.json").write_text(json.dumps({
            "schema": "agent-dev.feature-candidate/v2", "candidate": candidate,
            "candidate_tree": tree, "integration_branch": branch,
            "main_branch": "main", "merged_commit": merged}))
        (self.feature_dir / "progress.jsonl").write_text(json.dumps({
            "event": "feature-merged", "issue": "feature", "commit": merged}) + "\n")
        binding = dict(repo=self.repo, feature_dir=self.feature_dir, round=None,
                       candidate=candidate, worktree=worktree, log_generation="gen-1")
        service = integration_runtime.start_service(**binding)
        lease = integration_runtime.acquire_lease(**binding)
        plan = progress.cleanup_plan(self.repo, feature_dir=str(self.feature_dir), intent="close")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)
        self.assertFalse(worktree.exists())
        self.assertNotEqual(self.git("rev-parse", "--verify", branch, check=False).returncode, 0)
        self.assertEqual(json.loads((self.repo / ".agent-dev/lease.json").read_text())["lease_id"], lease["lease_id"])
        self.assertIsNotNone(json.loads((self.repo / ".agent-dev/lease.json").read_text())["released_at"])
        service_record = json.loads(integration_runtime._service_path(self.repo, service["service_id"]).read_text())
        self.assertEqual(service_record["state"], "stopped")

    def test_real_squash_promote_then_close_and_clean(self):
        sys.path.insert(0, str(SCRIPT_DIR / "tests"))
        from test_feature_closeout import FeatureCloseoutTest
        case = FeatureCloseoutTest("test_squash_promote_and_extra_change_rejected")
        case.setUp()
        try:
            case.ready_local()
            promoted = case.promote("squash")
            candidate = json.loads((case.fdir / "integration/candidate.json").read_text())
            self.assertEqual(candidate["merge_strategy"], "squash")
            parents = self.git("rev-list", "--parents", "-n", "1", promoted["merge_commit"], cwd=case.root).stdout.split()[1:]
            self.assertNotIn(candidate["candidate"], parents)
            plan = progress.cleanup_plan(case.root, feature_dir=str(case.fdir), intent="close")
            self.assertTrue(plan["eligible"], plan.get("reason"))
            result = pc.apply_operation(case.root, plan["operation_id"], plan["confirm"])
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["state"], "completed")
            self.assertTrue(result["worktree_removed"])
            self.assertTrue(result["branch_deleted"])
        finally:
            case.tearDown()

    def _promoted_round(self, round_name, branch):
        """Create one promoted (feature-merged) round candidate for scoping tests."""
        self.git("checkout", "-q", "-b", branch)
        (self.repo / ("round-%s.py" % round_name)).write_text("VALUE = '%s'\n" % round_name, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "round %s" % round_name)
        candidate = self.git("rev-parse", "HEAD").stdout.strip()
        tree = self.git("rev-parse", "HEAD^{tree}").stdout.strip()
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", branch, "-m", "merge round %s" % round_name)
        merged = self.git("rev-parse", "HEAD").stdout.strip()
        integration = self.feature_dir / "integration" / round_name
        integration.mkdir(parents=True)
        (integration / "candidate.json").write_text(json.dumps({
            "schema": "agent-dev.feature-candidate/v2", "candidate": candidate,
            "candidate_tree": tree, "integration_branch": branch,
            "main_branch": "main", "merged_commit": merged}), encoding="utf-8")
        with (self.feature_dir / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "feature-merged", "issue": "feature",
                                     "commit": merged}) + "\n")

    def test_feature_cleanup_scopes_candidates_by_round(self):
        # 多轮复用同一 feature 目录：不带 round 拒绝猜测；带 round 只清本轮分支
        self._promoted_round("2026-11-01", "feat/slug-2026-11-01")
        self._promoted_round("2026-11-18", "feat/slug-2026-11-18")
        with self.assertRaisesRegex(pc.CleanupError, "找到 2 个"):
            pc._feature_candidate(self.repo, self.feature_dir)
        blocked = pc.make_plan(self.repo, feature_dir=self.feature_dir, intent="close")
        self.assertFalse(blocked.get("eligible"))
        self.assertIn("找到 2 个", str(blocked.get("error") or blocked.get("reason")))
        plan = pc.make_plan(self.repo, feature_dir=self.feature_dir, intent="close", round="2026-11-18")
        self.assertTrue(plan["eligible"], plan.get("reason"))
        self.assertEqual(plan["branch"], "feat/slug-2026-11-18")
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["branch_deleted"])
        self.assertEqual(self.git("rev-parse", "--verify", "feat/slug-2026-11-01", check=False).returncode, 0)

    def test_completed_journal_reobserves_remaining_paths(self):
        branch, wt, _, _ = self.merged_worktree()
        plan = self.plan()
        result = self.apply(plan)
        self.assertTrue(result["ok"], result)
        wt.mkdir(parents=True)
        self.git("branch", branch, "main")
        observed = pc.inspect_operation(self.repo, plan["operation_id"])
        self.assertFalse(observed["ok"])
        self.assertEqual(observed["state"], "needs-attention")
        self.assertFalse(observed["worktree_removed"])
        self.assertFalse(observed["branch_deleted"])
        self.assertIn(str(wt.resolve()), observed["remaining_paths"])
        self.assertIn("ref:refs/heads/" + branch, observed["remaining_paths"])

    def test_aggregate_reconcile_is_readonly_and_classifies_provider_failure(self):
        _, _, _, _ = self.merged_worktree()
        plan = self.plan()
        import delivery
        with mock.patch("delivery.reconcile_operations") as delivery_reconcile, \
                mock.patch("delivery.discover_operations", wraps=delivery.discover_operations) as delivery_discover, \
                mock.patch("human_decision.reconcile") as human_reconcile:
            result = progress.aggregate_reconcile(self.repo)
        self.assertEqual(set(result["categories"]), {"cleanup", "delivery", "human_decision", "runtime"})
        self.assertFalse(result["applied"])
        delivery_reconcile.assert_not_called()
        delivery_discover.assert_called_once_with(self.repo, None)
        human_reconcile.assert_not_called()
        self.assertEqual(result["categories"]["cleanup"]["operations"][0]["operation_id"], plan["operation_id"])
        corrupt = self.impl_dir / "delivery-operation.json"
        corrupt.write_text("{broken")
        result = progress.aggregate_reconcile(self.repo)
        self.assertFalse(result["categories"]["delivery"]["ok"])
        self.assertIn("cannot load journal", result["categories"]["delivery"]["operations"][0]["error"])
        args = SimpleNamespace(kind="delivery", context="missing-context", operation_id=None,
                               candidate=None, candidate_path=None, candidate_repo=None, feature=None)
        with mock.patch("delivery.reconcile_operations", side_effect=RuntimeError("provider down")):
            failure = progress.apply_reconcile(self.repo, args)
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["kind"], "delivery")
        self.assertIn("provider down", failure["error"])

    def test_rebase_feature_promote_and_unknown_route_reconcile(self):
        from state_store import OperationJournal
        def completed(path, operation_id, steps):
            journal = OperationJournal.create(path, operation_id, steps)
            for step in steps:
                journal.advance(step, expected={"operation_id": operation_id}, observed={"ok": True})
            journal.complete()
        rebase = self.impl_dir / "rebase-operation-rebase-test.json"
        completed(rebase, "rebase-test", ["rebase", "record"])
        integration = self.feature_dir / "integration"; integration.mkdir()
        promote = integration / "delivery-promote.json"
        completed(promote, "feature-test", ["authorize", "merge", "verify", "record"])
        result = progress.aggregate_reconcile(self.repo)
        operations = result["categories"]["delivery"]["operations"]
        self.assertEqual({item["kind"] for item in operations}, {"rebase", "feature"})
        with mock.patch("human_decision.list", return_value=[{
                "id": "decision-1", "status": "routing", "route": {"route_id": "route-1"}}]):
            routed = progress.aggregate_reconcile(self.repo)
        self.assertTrue(routed["categories"]["human_decision"]["unknown_routes"])
        self.assertEqual(routed["state"], "needs-attention")
        args = SimpleNamespace(kind="delivery", context=None, operation_id=None,
                               candidate=None, candidate_path="/tmp/candidate.json", candidate_repo="/tmp/integration",
                               feature=None, strategy="squash")
        with mock.patch("delivery.reconcile_operations", return_value={"state": "completed"}) as reconcile:
            recovered = progress.apply_reconcile(self.repo, args)
        self.assertTrue(recovered["ok"])
        reconcile.assert_called_once_with(self.repo, None, apply=True, context=None,
                                          candidate_path="/tmp/candidate.json",
                                          candidate_repo="/tmp/integration", main_repo=self.repo,
                                          strategy="squash")

    def test_isolated_cli_preview_apply_and_readonly_reconcile(self):
        _, wt, _, _ = self.merged_worktree()
        preview = subprocess.run([sys.executable, "-I", str(SCRIPT_DIR / "progress.py"), "close-and-clean",
                                  "--repo", str(self.repo), "--issue", "issue-001"], capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr + preview.stdout)
        plan = json.loads(preview.stdout)
        before = pc.reconcile_operations(self.repo, plan["operation_id"], apply=False)
        self.assertTrue(before["operations"][0]["worktree_removed"] is False)
        self.assertTrue(wt.exists())
        applied = subprocess.run([sys.executable, "-I", str(SCRIPT_DIR / "progress.py"), "close-and-clean",
                                  "--repo", str(self.repo), "--issue", "issue-001", "--apply",
                                  "--operation-id", plan["operation_id"], "--confirm", plan["confirm"]],
                                 capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        self.assertEqual(json.loads(applied.stdout)["state"], "completed")

class DSHProgressHTTPTest(unittest.TestCase):
    def test_v2_routes_stale_protected_and_legacy_404(self):
        script = SCRIPT_DIR.parent / "dsh-progress-panel" / "test_progress_panel_http.mjs"
        result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
