#!/usr/bin/env python3
"""Ticket delivery integration tests on real temporary Git worktrees."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import delivery
import state_store
import verify_issue

SCRIPT = SCRIPTS / "delivery.py"

CONFIG = """```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'python3 -I t.py'}
host-budget: {total: 2, max-runs: 1, queue-timeout: 30}
verify-policy:
  ticket-max-files: 10
  ticket-max-loc: 400
  deny: []
  test-change: [tests/**]
  generated-refresh: []
  base-freeze: true
  attribution: two-sided
  gate-scope: {categories-file: null, non-gating: [], env-skip: true}
feature-closeout: {mode: local-human}
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


def sh(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True).stdout.strip()


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        sh(self.root, "init", "-q", "-b", "main")
        sh(self.root, "config", "user.email", "t@t")
        sh(self.root, "config", "user.name", "t")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n")
        (self.root / "t.py").write_text("from pathlib import Path\nassert int(Path('src/a.py').read_text().split('=')[1]) in (1, 2, 3)\n")
        (self.root / ".gitignore").write_text("dev/\n.worktrees/\n.logs/\ndocs/\n")
        sh(self.root, "add", ".")
        sh(self.root, "commit", "-qm", "init")
        sh(self.root, "branch", "feat/demo", "main")
        (self.root / "dev").mkdir()
        (self.root / "dev" / "agents-config.md").write_text(CONFIG)
        self.fdir = self.root / "dev" / "features" / "mod" / "demo"
        (self.fdir / "issues").mkdir(parents=True)
        self.spec = self.fdir / "demo-tech-spec-2026-11-14.md"
        self.spec.write_text("---\nstatus: 已确认（待拆解）\n---\n# Spec\n\nTicket scope: src/a.py.\n")
        prd = self.root / "docs" / "features" / "mod" / "demo" / "demo-prd-2026-11-14.md"
        prd.parent.mkdir(parents=True)
        prd.write_text("# PRD\n\n### 端到端验收场景\n- AC-1：Given 测试输入，When 执行命令，Then 退出码为 0\n")
        digest = __import__("hashlib").sha256(prd.read_bytes()).hexdigest()
        (self.fdir / "acceptance-plan.yaml").write_text(f"""schema: agent-dev.feature-acceptance/v2
tech_spec: demo-tech-spec-2026-11-14.md
documents:
  prd:
    path: docs/features/mod/demo/demo-prd-2026-11-14.md
    sha256: {digest}
cases:
  AC-1:
    title: 命令验收
    source: {{document: prd, anchor: AC-1}}
    required: true
    executor: command
    oracle: auto
    preconditions: [环境：隔离测试环境]
    steps: [执行命令]
    expected: 退出码为 0
    command: python3 -I t.py
    assertions: {{exit: 0}}
    evidence: [log]
""")
        self.issue = self.fdir / "issues" / "issue-001-x.md"
        self.write_issue()
        self.integration = self.root / ".worktrees" / "integration-demo"
        sh(self.root, "worktree", "add", "-q", str(self.integration), "feat/demo")

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, target=None):
        explicit = f"target-branch: {target}\n" if target else ""
        self.issue.write_text(
            "---\nissue-id: issue-001\nfeature-slug: demo\ntitle: X\nstatus: confirmed\nblocked-by: []\n"
            f"declared-scope: [src/a.py]\ntech-spec: {self.spec.name}\n{explicit}---\n"
            "# X\n\n## 要做什么\n\n演示命令行为。\n\n## DoD\n\n- [ ] `python3 -I t.py`\n")

    @property
    def context_path(self):
        return self.fdir / "impl" / "issue-001" / "ticket-context.json"

    @property
    def context(self):
        return json.loads(self.context_path.read_text())

    @property
    def source(self):
        return self.root / ".worktrees" / "issue-001"

    def start(self):
        return delivery.start(self.issue, self.root)

    def implement(self):
        (self.source / "src" / "a.py").write_text("x = 2\n")
        sh(self.source, "add", "src/a.py")
        sh(self.source, "commit", "-qm", "implement issue")

    def final_rebase(self):
        return delivery.rebase(self.context_path)

    def verify(self, runner):
        args = SimpleNamespace(
            issue=str(self.issue), repo=str(self.source), output_dir=None, timeout=30.0,
            runner=runner, identity_json=None, host="zcode", agent=runner,
            session="session-" + runner,
            model="provider/dev" if runner == "implementer" else "provider/supervisor",
            effort="medium" if runner == "implementer" else "high", tier=None,
        )
        document = verify_issue.run_verification(args)
        self.assertTrue(document["pass"], document)

    def prepare(self):
        self.start()
        self.implement()
        self.final_rebase()
        self.verify("implementer")
        self.verify("supervisor")
        receipt = self.fdir / "impl" / "issue-001" / "review-receipt.json"
        receipt.write_text(json.dumps({
            "verdict": "pass", "base": self.context["base"], "head": self.context["source_head"],
            "tree": self.context["source_tree"]}))
        return receipt

    def merge(self):
        with mock.patch.object(delivery, "_run_ticket_gate",
                               return_value={"head": self.context["source_head"]}):
            return delivery.merge(self.context_path)

    def reconcile(self):
        with mock.patch.object(delivery, "_run_ticket_gate",
                               return_value={"head": self.context["source_head"]}):
            return delivery.reconcile(self.context_path)

    def test_start_cli_isolated_freezes_context_and_main_dev_symlink(self):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "start",
                                 "--issue", str(self.issue), "--repo", str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue((self.source / "dev").is_symlink())
        self.assertEqual((self.source / "dev").resolve(), (self.root / "dev").resolve())
        context = self.context
        self.assertEqual(context["source_head"], sh(self.source, "rev-parse", "HEAD"))
        self.assertEqual(context["target_branch"], "feat/demo")
        self.assertEqual(context["target_commit"], sh(self.integration, "rev-parse", "HEAD"))
        self.assertEqual(context["declared_scope"], ["src/a.py"])
        self.assertEqual(context["hashes"]["spec"], __import__("hashlib").sha256(self.spec.read_bytes()).hexdigest())
        self.assertEqual(payload["branch"], "feat/demo-issue-001")

    def test_start_resumes_interrupted_context_creation(self):
        first = self.start()
        self.context_path.unlink()
        second = self.start()
        self.assertEqual(first["branch"], second["branch"])
        self.assertEqual(first["source_head"], second["source_head"])
        self.assertTrue(self.context_path.is_file())

    def test_start_rejects_proposed_active_without_state_and_foreign_target_without_side_effects(self):
        self.issue.write_text(self.issue.read_text().replace("status: confirmed", "status: proposed"))
        with self.assertRaisesRegex(delivery.DeliveryError, "fresh start requires confirmed"):
            self.start()
        self.issue.write_text(self.issue.read_text().replace("status: proposed", "status: in-review"))
        with self.assertRaisesRegex(delivery.DeliveryError, "existing delivery state"):
            self.start()
        self.write_issue(target="release/ticket")
        with self.assertRaisesRegex(delivery.DeliveryError, "feature integration branch"):
            self.start()
        self.assertFalse(self.source.exists())

    def test_start_rejects_unfinished_dependency_before_git_mutation(self):
        second = self.fdir / "issues" / "issue-002-y.md"
        second.write_text(self.issue.read_text().replace("issue-001", "issue-002")
                          .replace("declared-scope: [src/a.py]", "declared-scope: [src/b.py]"), encoding="utf-8")
        self.issue.write_text(self.issue.read_text().replace("blocked-by: []", "blocked-by: [issue-002]"), encoding="utf-8")
        with self.assertRaisesRegex(delivery.DeliveryError, "neither merged nor done"):
            self.start()
        self.assertFalse(self.source.exists())

    def test_start_accepts_merged_or_done_dependency(self):
        for status in ("merged", "done"):
            with self.subTest(status=status):
                # Independent fixture per accepted dependency status.
                self.tearDown()
                self.setUp()
                second = self.fdir / "issues" / "issue-002-y.md"
                second.write_text(self.issue.read_text().replace("issue-001", "issue-002")
                                  .replace("declared-scope: [src/a.py]", "declared-scope: [src/b.py]")
                                  .replace("status: confirmed", "status: " + status), encoding="utf-8")
                self.issue.write_text(self.issue.read_text().replace(
                    "blocked-by: []", "blocked-by: [issue-002]"), encoding="utf-8")
                result = self.start()
                self.assertEqual(result["target"], "feat/demo")

    def test_confirmed_redesign_refreezes_context_but_active_resume_cannot(self):
        first = self.start()
        old = self.context
        self.spec.write_text(self.spec.read_text() + "\nDEC-01 replaced by new evidence.\n", encoding="utf-8")
        self.issue.write_text(self.issue.read_text().replace("status: confirmed", "status: in-progress"), encoding="utf-8")
        with self.assertRaisesRegex(delivery.DeliveryError, "active resume cannot silently refreeze"):
            self.start()
        self.assertEqual(self.context["gate2_context_digest"], old["gate2_context_digest"])
        self.issue.write_text(self.issue.read_text().replace("status: in-progress", "status: confirmed"), encoding="utf-8")
        second = self.start()
        self.assertEqual(first["branch"], second["branch"])
        self.assertNotEqual(self.context["gate2_context_digest"], old["gate2_context_digest"])
        self.assertNotEqual(self.context["hashes"]["spec"], old["hashes"]["spec"])

    def test_invalid_dod_start_has_no_git_side_effects(self):
        self.issue.write_text(self.issue.read_text().replace("`python3 -I t.py`", "python3 -I t.py"))
        with self.assertRaisesRegex(delivery.DeliveryError, "plan check failed"):
            self.start()
        self.assertFalse(self.source.exists())
        missing = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--verify", "-q", "feat/demo-issue-001"],
                                 capture_output=True)
        self.assertNotEqual(missing.returncode, 0)

    def test_rebase_success_before_context_replace_recovers_without_second_rebase(self):
        self.start()
        self.implement()
        (self.integration / "src" / "b.py").write_text("y = 1\n")
        sh(self.integration, "add", "src/b.py")
        sh(self.integration, "commit", "-qm", "target moves")
        old_context = self.context
        original_write = state_store.atomic_json_write
        def fail_context_replace(path, value, *args, **kwargs):
            if Path(path).resolve() == self.context_path.resolve():
                raise delivery.DeliveryError("simulated context replace crash")
            return original_write(path, value, *args, **kwargs)
        with mock.patch.object(state_store, "atomic_json_write", side_effect=fail_context_replace):
            with self.assertRaisesRegex(delivery.DeliveryError, "context replace crash"):
                self.final_rebase()
        self.assertEqual(self.context, old_context)
        self.assertNotEqual(sh(self.source, "rev-parse", "HEAD"), old_context["source_head"])
        original_git = delivery._git
        rebase_calls = []
        def observe_git(repo, *args, **kwargs):
            if args and args[0] == "rebase":
                rebase_calls.append(args)
            return original_git(repo, *args, **kwargs)
        with mock.patch.object(delivery, "_git", side_effect=observe_git):
            result = self.final_rebase()
        self.assertFalse(rebase_calls)
        self.assertEqual(result["context"]["source_head"], sh(self.source, "rev-parse", "HEAD"))
        invalidations = (self.fdir / "impl" / "issue-001" / "delivery-invalidations.jsonl").read_text().splitlines()
        self.assertEqual(len(invalidations), 1)

    def test_local_human_explicit_main_target_rejected_before_worktree(self):
        self.write_issue("main")
        with self.assertRaisesRegex(delivery.DeliveryError, "explicit target-branch"):
            self.start()
        self.assertFalse(self.source.exists())

    def test_green_merge_marks_merged_once_and_retains_worktree(self):
        self.prepare()
        result = self.merge()
        self.assertEqual(result["status"], "merged")
        commit = result["merge_commit"]
        self.assertEqual(sh(self.integration, "rev-list", "--parents", "-n", "1", commit).split()[1:],
                         [self.context["target_commit"], self.context["source_head"]])
        self.assertEqual(sh(self.integration, "rev-parse", "HEAD^{tree}"), self.context["source_tree"])
        self.assertIn("status: merged", self.issue.read_text())
        self.assertNotIn("status: done", self.issue.read_text())
        events = [json.loads(line) for line in (self.fdir / "progress.jsonl").read_text().splitlines()]
        self.assertEqual(len([e for e in events if e.get("event") == "merged"]), 1)
        self.assertFalse([e for e in events if e.get("event") == "accepted"])
        merged = [e for e in events if e.get("event") == "merged"][0]
        self.assertEqual(merged["host"], "zcode")
        self.assertEqual(merged["branch"], "feat/demo-issue-001")
        self.assertEqual(merged["target"], "feat/demo")
        self.assertRegex(merged["ts"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        # Worktree/branch are retained until feature finalize accepts the candidate.
        self.assertTrue(self.source.exists())
        self.assertEqual(sh(self.source, "rev-parse", "--abbrev-ref", "HEAD"), "feat/demo-issue-001")
        journal = state_store.OperationJournal.load(self.context_path.parent / "delivery-operation.json")
        self.assertEqual(journal.state, "completed")
        # Repeated merge is idempotent: same commit, no second event, no cleanup.
        replay = self.merge()
        self.assertTrue(replay.get("replayed"))
        self.assertEqual(replay["merge_commit"], commit)
        self.assertEqual(replay["status"], "merged")
        events = [json.loads(line) for line in (self.fdir / "progress.jsonl").read_text().splitlines()]
        self.assertEqual(len([e for e in events if e.get("event") == "merged"]), 1)
        self.assertTrue(self.source.exists())
        self.assertTrue(delivery.source_branch_exists(self.root, "feat/demo-issue-001"))

    def test_merge_replay_after_accepted_finalize_is_idempotent(self):
        self.prepare()
        result = self.merge()
        self.issue.write_text(self.issue.read_text().replace("status: merged", "status: done"),
                              encoding="utf-8")
        accepted = {"event_id": "accepted-issue-001", "event": "accepted",
                    "issue": "issue-001", "target": "feat/demo",
                    "commit": result["merge_commit"]}
        state_store.append_event_once(self.fdir / "progress.jsonl", accepted, accepted["event_id"])
        replay = self.merge()
        self.assertTrue(replay.get("replayed"))
        self.assertEqual(replay["status"], "done")
        self.assertEqual(replay["merge_commit"], result["merge_commit"])
        events = [json.loads(line) for line in (self.fdir / "progress.jsonl").read_text().splitlines()]
        self.assertEqual(sum(e.get("event") == "merged" for e in events), 1)
        self.assertEqual(sum(e.get("event") == "accepted" for e in events), 1)
        self.assertTrue(self.source.exists())

    def test_merge_preflight_calls_process_check_ticket_phase(self):
        self.prepare()
        import process_check
        with mock.patch.object(process_check, "run",
                               return_value=(process_check.Report(), {
                                   "head": self.context["source_head"],
                                   "base": self.context["base"]})) as run:
            result = delivery.merge(self.context_path)
        self.assertEqual(result["status"], "merged")
        called = run.call_args.args[0]
        self.assertEqual(called.phase, "ticket")
        self.assertEqual(called.base, self.context["base"])
        self.assertEqual(called.head, self.context["source_head"])

    def test_dirty_source_or_target_blocks_before_merge(self):
        self.prepare()
        (self.source / "dirty.py").write_text("dirty\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "both be clean"):
            self.merge()
        (self.source / "dirty.py").unlink()
        (self.integration / "src" / "target.py").write_text("target\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "both be clean"):
            self.merge()

    def test_target_advances_requires_rebase_and_old_evidence_is_invalid(self):
        receipt = self.prepare()
        verification = (self.fdir / "impl" / "issue-001" / "verification-supervisor.json").read_bytes()
        (self.integration / "src" / "b.py").write_text("y = 1\n")
        sh(self.integration, "add", "src/b.py")
        sh(self.integration, "commit", "-qm", "target moves")
        with self.assertRaisesRegex(delivery.DeliveryError, "rebase is required"):
            self.merge()
        result = self.final_rebase()
        self.assertTrue(result["changed"])
        self.assertTrue(receipt.is_file(), "rebase must retain old review history")
        self.assertEqual((self.fdir / "impl" / "issue-001" / "verification-supervisor.json").read_bytes(), verification)
        invalidations = (self.fdir / "impl" / "issue-001" / "delivery-invalidations.jsonl").read_text()
        self.assertIn("rebase-invalidation", invalidations)
        with self.assertRaisesRegex(delivery.DeliveryError, "verification.*(?:HEAD|head|tree|base)"):
            delivery.merge(self.context_path)

    def test_rebase_conflict_preserves_live_conflict_and_invalidation(self):
        self.start()
        self.implement()
        (self.integration / "src" / "a.py").write_text("x = 3\n")
        sh(self.integration, "commit", "-qam", "target conflict")
        old = self.context
        with self.assertRaisesRegex(delivery.DeliveryError, "conflict state preserved"):
            self.final_rebase()
        self.assertEqual(self.context, old)
        self.assertTrue(delivery._rebase_in_progress(self.source))
        self.assertIn("UU src/a.py", sh(self.source, "status", "--porcelain"))
        marker = json.loads((self.context_path.parent / "latest-invalidation.json").read_text())
        self.assertEqual(marker["status"], "conflict")

    def test_ticket_merge_does_not_require_supervisor_receipt_or_conformance(self):
        self.prepare()
        impl = self.fdir / "impl" / "issue-001"
        (impl / "verification-supervisor.json").unlink()
        (impl / "review-receipt.json").unlink()
        (impl / "conformance-issue-001.md").unlink(missing_ok=True)
        result = self.merge()
        self.assertEqual(result["status"], "merged")
        self.assertIn("status: merged", self.issue.read_text())
        self.assertTrue(self.source.exists())

    def test_implementer_verification_is_still_required_and_decision_blocks(self):
        self.prepare()
        impl = self.fdir / "impl" / "issue-001"
        (impl / "verification-implementer.json").unlink()
        with self.assertRaisesRegex(delivery.DeliveryError, "verification JSON missing: verification-implementer"):
            delivery.merge(self.context_path)
        self.verify("implementer")
        (self.fdir / "decisions").mkdir()
        # Bypass packet creation only to simulate an unreadable/foreign active
        # record; the production reader still fails closed on it.
        (self.fdir / "decisions" / "broken.json").write_text("not-json")
        with self.assertRaisesRegex(delivery.DeliveryError, "decision|human"):
            self.merge()

    def test_crash_after_merge_before_record_recovers_exact_transaction(self):
        self.prepare()
        with mock.patch.object(delivery, "_record_ticket", side_effect=delivery.DeliveryError("simulated record crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "simulated record crash"):
                self.merge()
        merge_commit = sh(self.integration, "rev-parse", "HEAD")
        self.assertNotIn("status: merged", self.issue.read_text())
        journal = state_store.OperationJournal.load(self.context_path.parent / "delivery-operation.json")
        self.assertEqual(journal.current_step, "record")
        result = self.reconcile()
        self.assertEqual(result["merge_commit"], merge_commit)
        self.assertIn("status: merged", self.issue.read_text())
        self.assertTrue(self.source.exists())
        # Repeated reconcile after completion stays an idempotent replay.
        replay = self.reconcile()
        self.assertTrue(replay.get("replayed"))
        self.assertEqual(replay["merge_commit"], merge_commit)

    def test_corrupt_or_legacy_progress_fails_before_merge(self):
        self.prepare()
        target_before = sh(self.integration, "rev-parse", "HEAD")
        cases = (
            '{"event":"merged"}\n',
            '{"event_id":"x","event_id":"y"}\n',
            '{"event_id":"x"}',
            'not-json\n',
        )
        for content in cases:
            with self.subTest(content=content):
                (self.fdir / "progress.jsonl").write_text(content)
                with self.assertRaises(Exception):
                    self.merge()
                self.assertEqual(sh(self.integration, "rev-parse", "HEAD"), target_before)
                self.assertNotIn("status: merged", self.issue.read_text())

    def test_local_target_mutation_before_update_ref_is_a_real_cas_failure(self):
        self.prepare()
        original = delivery._cas_update_ref
        def competing_update(repo, branch, new, frozen_old):
            tree = sh(repo, "rev-parse", frozen_old + "^{tree}")
            competing = delivery._commit_tree(repo, tree, [frozen_old], "competing target mutation\n")
            original(repo, branch, competing, frozen_old)
            return original(repo, branch, new, frozen_old)
        with mock.patch.object(delivery, "_cas_update_ref", side_effect=competing_update):
            with self.assertRaisesRegex(delivery.DeliveryError, "target CAS failed"):
                self.merge()
        self.assertNotEqual(sh(self.integration, "rev-parse", "HEAD"), self.context["source_head"])
        self.assertNotIn("status: merged", self.issue.read_text())
        progress = self.fdir / "progress.jsonl"
        self.assertFalse(progress.exists() and '"event": "merged"' in progress.read_text())

    def configure_remote(self):
        config = self.root / "dev" / "agents-config.md"
        config.write_text(CONFIG.replace("tracker: local", "tracker: gitlab").replace(
            "feature-closeout: {mode: local-human}",
            "feature-closeout:\n  mode: cooperative-ci\n  ci: {provider: gitea, base-url: 'https://gitea.example', repository: o/r, workflow: full, artifact: verdict.json, token-env: GITEA_TOKEN}"))

    def test_remote_merge_target_advanced_is_needs_human_without_retry(self):
        self.configure_remote()
        self.prepare()
        merge_commit = delivery._commit_tree(
            self.root, self.context["source_tree"],
            [self.context["target_commit"], self.context["source_head"]], "remote original merge\n")
        remote = Path(self.tmp.name) / "remote.git"
        sh(self.root, "clone", "--bare", "-q", ".", str(remote))
        sh(self.root, "remote", "add", "origin", str(remote))
        sh(self.root, "push", "-q", "origin", merge_commit + ":refs/heads/main")
        later = delivery._commit_tree(self.root, self.context["source_tree"], [merge_commit],
                                      "target advances after merge\n")
        sh(self.root, "push", "-q", "origin", later + ":refs/heads/main")
        api = StubAPI("gitea", self.context["source_head"], "main", merge_commit)
        api.merged = True
        api.target_tip = later
        adapter = delivery.GiteaMRAdapter(api)
        capability = {"mode": "cooperative-ci", "ok": True, "checks": {}}
        with mock.patch("ci_proof.capabilities", return_value=capability), \
             mock.patch("ci_proof.require_capabilities", return_value=capability), \
             mock.patch.object(delivery, "mr_adapter", return_value=adapter):
            with self.assertRaisesRegex(delivery.DeliveryError, "needs-human"):
                self.merge()
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), self.context["target_commit"])
        self.assertFalse(any(call[0] == "PUT" for call in api.calls))
        self.assertNotIn("status: merged", self.issue.read_text())
        journal = state_store.OperationJournal.load(self.context_path.parent / "delivery-operation.json")
        observed = journal.steps[0]["observed"]
        self.assertEqual(observed["status"], "needs-human")
        self.assertEqual(observed["merge_commit"], merge_commit)
        self.assertEqual(observed["remote_target"], later)
        self.assertTrue(observed["original_merge_in_target"])
        calls_before = list(api.calls)
        with self.assertRaisesRegex(delivery.DeliveryError, "needs-human"):
            self.reconcile()
        self.assertEqual(api.calls, calls_before)

    def test_operation_discovery_includes_ticket_rebase_and_feature(self):
        self.prepare()
        self.merge()
        feature_journal = self.fdir / "integration" / "delivery-promote.json"
        journal = state_store.OperationJournal.create(feature_journal, "feature-" + "a" * 64,
                                                       ["authorize", "merge", "verify", "record"])
        for step in ("authorize", "merge", "verify", "record"):
            journal.advance(step, expected={}, observed={})
        journal.complete()
        (self.fdir / "integration" / "candidate.json").write_text(json.dumps({"integration_branch": "feat/demo"}))
        result = delivery.discover_operations(self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual({item["kind"] for item in result["operations"]}, {"ticket", "rebase", "feature"})
        feature = next(item for item in result["operations"] if item["kind"] == "feature")
        self.assertEqual(feature["candidate_path"], str((self.fdir / "integration" / "candidate.json").resolve()))
        self.assertEqual(feature["candidate_repo"], str(self.integration.resolve()))
        with mock.patch("human_decision.list", return_value=[{"id": "d", "status": "routing", "route": {"route_id": "r"}}]):
            routed = delivery.discover_operations(self.root)
        self.assertFalse(routed["ok"])
        self.assertEqual(routed["state"], "needs-attention")
        self.assertEqual(routed["unknown_routes"][0]["route_id"], "r")

    def test_completed_merge_retains_resources_and_rejects_merged_start(self):
        self.prepare()
        self.merge()
        self.assertTrue(self.source.exists())
        self.assertTrue(delivery.source_branch_exists(self.root, "feat/demo-issue-001"))
        residual = self.fdir / "impl" / "issue-001" / "old.orig"
        residual.write_text("kept until feature finalize\n")
        replay = delivery.reconcile(self.context_path)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "merged")
        self.assertTrue(residual.is_file())
        # A merged ticket cannot be re-opened by delivery.start; feature finalize
        # owns merged -> accepted -> done.
        with self.assertRaisesRegex(delivery.DeliveryError, "already merged|feature finalize"):
            self.start()
        self.assertIn("status: merged", self.issue.read_text())
        self.assertTrue(self.source.exists())

    def test_crash_after_cas_before_materialize_recovers_without_new_merge(self):
        self.prepare()
        with mock.patch.object(delivery, "_materialize_cas_result", side_effect=delivery.DeliveryError("after CAS crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "after CAS crash"):
                self.merge()
        merge_commit = sh(self.integration, "rev-parse", "HEAD")
        self.assertNotEqual(sh(self.integration, "rev-parse", "HEAD^{tree}"), self.context["target_commit"])
        self.assertFalse(delivery._clean(self.integration))
        original_commit_tree = delivery._commit_tree
        calls = []
        def observe(*args, **kwargs):
            calls.append(args)
            return original_commit_tree(*args, **kwargs)
        with mock.patch.object(delivery, "_commit_tree", side_effect=observe):
            result = self.reconcile()
        self.assertFalse(calls)
        self.assertEqual(result["merge_commit"], merge_commit)
        self.assertTrue(delivery._clean(self.integration) if self.integration.exists() else True)

    def test_crash_after_git_merge_before_observed_is_recovered(self):
        self.prepare()
        journal, expected = delivery._journal(self.context, self.context_path)
        journal.advance("merge", expected=expected)
        sh(self.integration, "merge", "--no-ff", "--no-edit", self.context["source_head"])
        merge_commit = sh(self.integration, "rev-parse", "HEAD")
        result = self.reconcile()
        self.assertEqual(result["merge_commit"], merge_commit)
        self.assertEqual(sh(self.integration, "rev-list", "--parents", "-n", "1", merge_commit).split()[1:],
                         [self.context["target_commit"], self.context["source_head"]])

    def test_reconcile_rejects_parent_or_tree_forgery(self):
        self.prepare()
        journal, expected = delivery._journal(self.context, self.context_path)
        journal.advance("merge", expected=expected)
        sh(self.integration, "merge", "--no-ff", "--no-edit", self.context["source_head"])
        (self.integration / "unauthorized.py").write_text("BAD = True\n")
        sh(self.integration, "add", "unauthorized.py")
        sh(self.integration, "commit", "--amend", "-qm", "tree forgery")
        with self.assertRaisesRegex(delivery.DeliveryError, "tree"):
            self.reconcile()
        self.assertNotIn("status: merged", self.issue.read_text())

    def test_reconcile_without_journal_or_arbitrary_commit_is_not_an_api(self):
        self.prepare()
        with self.assertRaisesRegex(delivery.DeliveryError, "no exact delivery operation"):
            delivery.reconcile(self.context_path)
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "reconcile",
                                 "--context", str(self.context_path), "--commit", "HEAD"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


class StubAPI:
    def __init__(self, provider, source, target, merge_commit):
        self.provider = provider
        self.source = source
        self.target = target
        self.merge_commit = merge_commit
        self.repo_path = "/repo"
        self.calls = []
        self.merged = False
        self.target_tip = None

    def get_json(self, path, query=None):
        self.calls.append(("GET", path))
        if self.provider == "gitea" and path == "/repo/pulls":
            return [{"number": 7, "head": {"ref": "feat/demo-issue-001", "sha": self.source},
                     "base": {"ref": self.target}}]
        if self.provider == "gitlab" and path == "/repo/merge_requests":
            return [{"iid": 7, "source_branch": "feat/demo-issue-001", "target_branch": self.target,
                     "sha": self.source}]
        if self.provider == "gitea" and path == "/repo/pulls/7":
            if not self.merged:
                return {"state": "open", "mergeable": True, "head": {"sha": self.source}, "base": {"ref": self.target}}
            return {"merged": True, "merge_commit_sha": self.merge_commit,
                    "head": {"sha": self.source}, "base": {"ref": self.target}}
        if self.provider == "gitea" and path == "/repo/branches/" + delivery.urllib.parse.quote(self.target, safe=""):
            return {"commit": {"id": self.target_tip or self.merge_commit}}
        if self.provider == "gitlab" and path == "/repo/merge_requests/7":
            if not self.merged:
                return {"state": "opened", "merge_status": "can_be_merged", "sha": self.source, "target_branch": self.target}
            return {"state": "merged", "sha": self.source, "target_branch": self.target,
                    "merge_commit_sha": self.merge_commit}
        if self.provider == "gitlab" and path == "/repo/repository/branches/" + delivery.urllib.parse.quote(self.target, safe=""):
            return {"commit": {"id": self.target_tip or self.merge_commit}}
        raise AssertionError(path)

    def request_json(self, path, *, method, data):
        self.calls.append((method, path, data))
        self.merged = True
        return {}


class MRAdapterTest(unittest.TestCase):
    def test_expected_source_sha_and_post_merge_reread(self):
        source, target, commit = "a" * 40, "feat/demo", "b" * 40
        for provider, cls, expected_key in (("gitea", delivery.GiteaMRAdapter, "expected_head_sha"),
                                            ("gitlab", delivery.GitLabMRAdapter, "sha")):
            with self.subTest(provider=provider):
                api = StubAPI(provider, source, target, commit)
                adapter = cls(api)
                self.assertEqual(adapter.merge("7", source_sha=source, target_branch=target), commit)
                mutation = [c for c in api.calls if c[0] == "PUT"][0]
                self.assertEqual(mutation[2][expected_key], source)
                self.assertGreaterEqual(sum(c[0] == "GET" for c in api.calls), 3)

    def test_stale_source_is_rejected_before_mutation(self):
        api = StubAPI("gitea", "a" * 40, "feat/demo", "b" * 40)
        with self.assertRaises(delivery.DeliveryError):
            delivery.GiteaMRAdapter(api).merge("7", source_sha="c" * 40, target_branch="feat/demo")
        self.assertFalse(any(c[0] == "PUT" for c in api.calls))

    def test_unique_discovery_target_cas_and_merged_recovery(self):
        source, target, commit = "a" * 40, "feat/demo", "b" * 40
        for provider, cls in (("gitea", delivery.GiteaMRAdapter), ("gitlab", delivery.GitLabMRAdapter)):
            with self.subTest(provider=provider):
                self.assertFalse(cls.TARGET_CAS_SUPPORTED)
                api = StubAPI(provider, source, target, commit)
                adapter = cls(api)
                self.assertEqual(adapter.find("feat/demo-issue-001", target), "7")
                with self.assertRaisesRegex(delivery.DeliveryError, "target moved"):
                    adapter.merge("7", source_sha=source, target_branch=target, target_before="c" * 40)
                self.assertFalse(any(c[0] == "PUT" for c in api.calls))
                api.merged = True
                self.assertEqual(adapter.merge("7", source_sha=source, target_branch=target), commit)
                self.assertFalse(any(c[0] == "PUT" for c in api.calls))
    def test_server_enforced_remote_mutation_is_rejected_before_any_api_write(self):
        for provider, cls in (("gitea", delivery.GiteaMRAdapter), ("gitlab", delivery.GitLabMRAdapter)):
            with self.subTest(provider=provider):
                api = StubAPI(provider, "a" * 40, "feat/demo", "b" * 40)
                with self.assertRaisesRegex(delivery.DeliveryError, "target-old-OID CAS"):
                    cls(api).merge("7", source_sha="a" * 40, target_branch="feat/demo",
                                   target_before="c" * 40, mode="server-enforced")
                self.assertFalse(any(call[0] == "PUT" for call in api.calls))


if __name__ == "__main__":
    unittest.main()
