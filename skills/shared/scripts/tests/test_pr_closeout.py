#!/usr/bin/env python3
"""PR closeout evidence must bind the frozen candidate tree to the merged commit."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import delivery
import feature_closeout as fc

PR_SETTINGS = {"provider": "gitea", "base-url": "https://git.example.com", "repository": "o/r"}
CFG = {"main-branch": "main", "feature-closeout": {"mode": "pr", "pr": PR_SETTINGS}}
REQUIRED = ("provider", "repository", "number", "base", "head_sha", "head_tree",
            "merge_commit", "strategy", "merged")


def run(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def failures(report: fc.Report) -> str:
    return "\n".join(item["msg"] for item in report.items if item["level"] == "FAIL")


class PrEvidenceTest(unittest.TestCase):
    """Unit checks of _check_pr_evidence against a real merge or squash commit."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        run(self.repo, "init", "-q", "-b", "main")
        run(self.repo, "config", "user.email", "test@example.invalid")
        run(self.repo, "config", "user.name", "test")
        (self.repo / "f.txt").write_text("base\n", encoding="utf-8")
        run(self.repo, "add", ".")
        run(self.repo, "commit", "-q", "-m", "base")
        self.base = run(self.repo, "rev-parse", "main")
        run(self.repo, "checkout", "-q", "-b", "feat")
        (self.repo / "f.txt").write_text("change\n", encoding="utf-8")
        run(self.repo, "commit", "-qam", "change")
        self.candidate = run(self.repo, "rev-parse", "HEAD")
        self.tree = run(self.repo, "rev-parse", self.candidate + "^{tree}")
        run(self.repo, "checkout", "-q", "main")
        self.integ = Path(self.tmp.name) / "integration"
        self.integ.mkdir()
        self.cand = {"candidate": self.candidate, "candidate_tree": self.tree,
                     "main_head": self.base, "mode": "pr"}

    def merge(self, strategy="merge", extra_change=False):
        if extra_change:
            # Another change lands on main before the PR is merged; the PR result
            # is then a new tree instead of the verified candidate tree.
            (self.repo / "other.txt").write_text("other\n", encoding="utf-8")
            run(self.repo, "add", ".")
            run(self.repo, "commit", "-qm", "unrelated change")
        if strategy == "squash":
            run(self.repo, "merge", "-q", "--squash", self.candidate)
            run(self.repo, "commit", "-qm", "squash PR")
        else:
            run(self.repo, "merge", "--no-ff", "-q", self.candidate, "-m", "merge PR")
        return run(self.repo, "rev-parse", "main")

    def evidence(self, **overrides) -> dict:
        value = {"provider": "gitea", "repository": "o/r", "number": 1, "base": "main",
                 "head_sha": self.candidate, "head_tree": self.tree}
        value.update(overrides)
        return value

    def check(self, evidence, cfg=None) -> fc.Report:
        (self.integ / "pr.json").write_text(json.dumps(evidence), encoding="utf-8")
        report = fc.Report()
        fc._check_pr_evidence(report, self.integ, self.cand, cfg or CFG, self.repo)
        return report

    def assertRejected(self, evidence, *fragments, cfg=None):
        report = self.check(evidence, cfg)
        self.assertTrue(report.failed, report.items)
        messages = failures(report)
        for fragment in fragments:
            self.assertIn(fragment, messages)

    def test_merge_evidence_passes(self):
        merge_commit = self.merge()
        report = self.check(self.evidence(merge_commit=merge_commit, strategy="merge", merged=True))
        self.assertFalse(report.failed, report.items)

    def test_squash_evidence_passes(self):
        merge_commit = self.merge("squash")
        report = self.check(self.evidence(merge_commit=merge_commit, strategy="squash", merged=True))
        self.assertFalse(report.failed, report.items)

    def test_missing_file_fails(self):
        report = fc.Report()
        fc._check_pr_evidence(report, self.integ, self.cand, CFG, self.repo)
        self.assertTrue(report.failed)
        self.assertIn("pr.json 不存在", failures(report))

    def test_every_required_field_is_enforced(self):
        merge_commit = self.merge()
        for key in REQUIRED:
            with self.subTest(field=key):
                evidence = self.evidence(merge_commit=merge_commit, strategy="merge", merged=True)
                del evidence[key]
                self.assertRejected(evidence, "缺字段 " + key)

    def test_unmerged_pr_fails(self):
        merge_commit = self.merge()
        self.assertRejected(self.evidence(merge_commit=merge_commit, strategy="merge", merged=False),
                            "尚未合并")

    def test_head_binding_is_enforced(self):
        merge_commit = self.merge()
        self.assertRejected(self.evidence(merge_commit=merge_commit, strategy="merge", merged=True,
                                           head_sha=self.base), "head SHA")
        self.assertRejected(self.evidence(merge_commit=merge_commit, strategy="merge", merged=True,
                                           head_tree="0" * 40), "head tree")

    def test_merge_commit_must_be_the_full_main_head(self):
        merge_commit = self.merge()
        self.assertRejected(self.evidence(merge_commit=merge_commit[:12], strategy="merge", merged=True),
                            "40 位 SHA")
        self.assertRejected(self.evidence(merge_commit=self.base, strategy="merge", merged=True),
                            "未停在 PR merge commit")

    def test_merge_commit_tree_must_equal_candidate_tree(self):
        merge_commit = self.merge(extra_change=True)
        self.assertRejected(self.evidence(merge_commit=merge_commit, strategy="merge", merged=True),
                            "tree 与冻结候选 tree 不一致")

    def test_strategy_must_match_the_real_parents(self):
        merge_commit = self.merge()
        self.assertRejected(self.evidence(merge_commit=merge_commit, strategy="rebase", merged=True),
                            "merge / squash")
        self.assertRejected(self.evidence(merge_commit=merge_commit, strategy="squash", merged=True),
                            "父提交")
        run(self.repo, "reset", "-q", "--hard", self.base)
        squash_commit = self.merge("squash")
        self.assertRejected(self.evidence(merge_commit=squash_commit, strategy="merge", merged=True),
                            "父提交不含冻结候选")

    def test_pr_must_belong_to_the_configured_repository(self):
        merge_commit = self.merge()
        good = self.evidence(merge_commit=merge_commit, strategy="merge", merged=True)
        self.assertRejected(dict(good, provider="gitlab"), "pr.provider")
        self.assertRejected(dict(good, repository="other/repo"), "pr.repository")
        self.assertRejected(dict(good, base="release"), "main-branch")


class PrCloseoutFlowTest(unittest.TestCase):
    """pr-mode closeout on the shared feature fixture: verify gate, PR merge, promote, finalize."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_feature_closeout import CONFIG, FeatureCloseoutTest
        self.case = FeatureCloseoutTest("test_verify_green_and_stale_commit_or_tree_fail")
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        config = (self.case.root / "dev" / "agents-config.md")
        config.write_text(CONFIG.replace(
            "feature-closeout: {mode: local-human}",
            "feature-closeout:\n  mode: pr\n  pr: {provider: gitea, base-url: 'https://gitea.example', repository: o/r}"))

    @property
    def integ(self) -> Path:
        return self.case.fdir / "integration"

    def ready(self):
        """All tickets merged/done, candidate frozen, auto and human evidence recorded."""
        case = self.case
        case.write_issue("done")
        frozen = case.freeze()
        self.assertEqual(frozen.returncode, 0, frozen.stdout + frozen.stderr)
        self.assertEqual(case.candidate["mode"], "pr")
        case.write_evidence([case.auto_result(), case.human_result(True)])

    def merge_pr(self, strategy="merge", extra_change=False):
        """The user merges the PR on Gitea; the main worktree is then fetched to the merge commit."""
        case = self.case
        if extra_change:
            (case.root / "other.txt").write_text("other\n", encoding="utf-8")
            run(case.root, "add", ".")
            run(case.root, "commit", "-qm", "unrelated change")
        if strategy == "squash":
            run(case.root, "merge", "-q", "--squash", case.head)
            run(case.root, "commit", "-qm", "PR #1")
        else:
            run(case.root, "merge", "--no-ff", "-q", case.head, "-m", "PR #1")
        merge_commit = run(case.root, "rev-parse", "main")
        self.write_pr_evidence(merge_commit=merge_commit, strategy=strategy)
        return merge_commit

    def write_pr_evidence(self, **overrides):
        case = self.case
        evidence = {"provider": "gitea", "repository": "o/r", "number": 1, "base": "main",
                    "head_sha": case.head, "head_tree": case.tree, "merged": True,
                    "merged_by": "user", "merged_at": "2026-11-28T10:00:00Z"}
        evidence.update(overrides)
        (self.integ / "pr.json").write_text(json.dumps(evidence), encoding="utf-8")

    def promote(self):
        return delivery.promote(str(self.integ / "candidate.json"), self.case.wt, self.case.root)

    def merged_events(self) -> list:
        path = self.case.fdir / "progress.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "feature-merged"]

    def test_merge_check_requires_main_repo(self):
        self.ready()
        result = self.case.check("merge")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("main-repo", result.stdout)
        self.assertEqual(self.merged_events(), [])

    def test_verify_phase_rejects_main_drift_like_local_human(self):
        self.ready()
        (self.case.root / "docs").mkdir(exist_ok=True)
        (self.case.root / "docs" / "n.md").write_text("n\n", encoding="utf-8")
        run(self.case.root, "add", "-f", "docs")
        run(self.case.root, "commit", "-qm", "docs drift")
        result = self.case.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("任何路径变化", result.stdout)

    def check_merged_pr(self):
        result = self.case.check("merge", "--main-repo", str(self.case.root), generate_report=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_merged_pr_is_recorded_without_moving_main(self):
        self.ready()
        self.assertEqual(self.case.report().returncode, 0)
        merge_commit = self.merge_pr()
        self.check_merged_pr()
        result = self.promote()
        self.assertEqual(result["mode"], "pr")
        self.assertTrue(result["recorded"])
        self.assertEqual(run(self.case.root, "rev-parse", "main"), merge_commit)
        self.assertEqual(self.case.candidate["merged_commit"], merge_commit)
        self.assertEqual(self.case.candidate["merge_strategy"], "merge")
        events = self.merged_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["commit"], merge_commit)
        self.assertIn("PR #1 merged by user", events[0]["note"])

        # Re-running promote is an idempotent replay of the same recorded merge.
        self.assertTrue(self.promote()["recorded"])
        self.assertEqual(len(self.merged_events()), 1)

        # A crash between the candidate record and the event write is recovered
        # by the same replay: the missing feature-merged event is written once.
        journal = self.case.fdir / "progress.jsonl"
        kept = [line for line in journal.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("event") != "feature-merged"]
        journal.write_text("\n".join(kept) + "\n", encoding="utf-8")
        self.assertEqual(self.merged_events(), [])
        self.promote()
        self.assertEqual(len(self.merged_events()), 1)

        finalized = self.case.run_cli("finalize", "--feature-dir", str(self.case.fdir),
                                      "--repo", str(self.case.root), "--no-cleanup")
        self.assertEqual(finalized.returncode, 0, finalized.stdout + finalized.stderr)
        self.assertEqual(fc.read_frontmatter(self.case.fdir / "issues" / "issue-001-x.md")["status"], "done")

    def test_squash_pr_is_recorded_without_moving_main(self):
        self.ready()
        self.assertEqual(self.case.report().returncode, 0)
        merge_commit = self.merge_pr("squash")
        self.check_merged_pr()
        self.promote()
        self.assertEqual(run(self.case.root, "rev-parse", "main"), merge_commit)
        self.assertEqual(self.case.candidate["merge_strategy"], "squash")
        self.assertEqual(len(self.merged_events()), 1)

    def test_pr_merged_onto_a_drifted_main_is_not_recorded(self):
        self.ready()
        self.assertEqual(self.case.report().returncode, 0)
        merge_commit = self.merge_pr(extra_change=True)
        result = self.case.check("merge", "--main-repo", str(self.case.root), generate_report=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("tree 与冻结候选 tree 不一致", result.stdout)
        with self.assertRaisesRegex(delivery.DeliveryError, "tree"):
            self.promote()
        self.assertEqual(run(self.case.root, "rev-parse", "main"), merge_commit)
        self.assertEqual(self.merged_events(), [])
        self.assertNotIn("merged_commit", self.case.candidate)

    def test_promote_without_pr_evidence_fails(self):
        self.ready()
        self.assertEqual(self.case.report().returncode, 0)
        run(self.case.root, "merge", "--no-ff", "-q", self.case.head, "-m", "PR #1")
        with self.assertRaisesRegex(delivery.DeliveryError, "pr.json 不存在"):
            self.promote()
        self.assertEqual(self.merged_events(), [])

    def test_pr_evidence_changed_after_authorization_is_not_recorded(self):
        self.ready()
        self.assertEqual(self.case.report().returncode, 0)
        self.merge_pr()
        real_record = fc.record_merge

        def tamper(authorization, merge_result):
            self.write_pr_evidence(merged_by="someone-else")
            return real_record(authorization, merge_result)

        with mock.patch.object(fc, "record_merge", tamper):
            with self.assertRaisesRegex(delivery.DeliveryError, "在授权后变化"):
                self.promote()
        self.assertEqual(self.merged_events(), [])
        self.assertNotIn("merged_commit", self.case.candidate)


if __name__ == "__main__":
    unittest.main()
