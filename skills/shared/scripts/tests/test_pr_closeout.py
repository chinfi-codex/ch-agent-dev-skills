#!/usr/bin/env python3
"""PR closeout evidence must bind the frozen candidate tree to the merged commit."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import feature_closeout as fc


def run(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


class PrCloseoutTest(unittest.TestCase):
    def test_pr_evidence_binds_candidate_and_merge_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            run(repo, "init", "-q", "-b", "main")
            run(repo, "config", "user.email", "test@example.invalid")
            run(repo, "config", "user.name", "test")
            (repo / "f.txt").write_text("base\n", encoding="utf-8")
            run(repo, "add", ".")
            run(repo, "commit", "-q", "-m", "base")
            base = run(repo, "rev-parse", "main")
            run(repo, "checkout", "-q", "-b", "feat")
            (repo / "f.txt").write_text("change\n", encoding="utf-8")
            run(repo, "commit", "-qam", "change")
            candidate = run(repo, "rev-parse", "feat")
            tree = run(repo, "rev-parse", candidate + "^{tree}")
            run(repo, "checkout", "-q", "main")
            run(repo, "merge", "--no-ff", "-q", "feat", "-m", "merge")
            merge_commit = run(repo, "rev-parse", "main")

            integ = Path(temporary) / "integration"
            integ.mkdir()
            evidence = {
                "number": 1, "head_sha": candidate, "head_tree": tree,
                "merge_commit": merge_commit, "strategy": "merge", "merged": True,
                "merged_by": "user",
            }
            (integ / "pr.json").write_text(json.dumps(evidence), encoding="utf-8")
            candidate_record = {"candidate": candidate, "candidate_tree": tree, "main_head": base}

            report = fc.Report()
            fc._check_pr_evidence(report, integ, candidate_record, {"main-branch": "main"}, repo)
            self.assertFalse(report.failed, report.items)

            evidence["head_tree"] = "0" * 40
            (integ / "pr.json").write_text(json.dumps(evidence), encoding="utf-8")
            report = fc.Report()
            fc._check_pr_evidence(report, integ, candidate_record, {"main-branch": "main"}, repo)
            self.assertTrue(report.failed)


if __name__ == "__main__":
    unittest.main()
