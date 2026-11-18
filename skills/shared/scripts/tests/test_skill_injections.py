#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


SKILLS = Path(__file__).resolve().parents[3]
REPO = SKILLS.parent


class SkillInjectionTest(unittest.TestCase):
    def test_every_question_component_loads_human_readable_contract(self):
        covered = 0
        for template in SKILLS.glob("*/SKILL.md.tmpl"):
            text = template.read_text(encoding="utf-8")
            if "- AskUserQuestion" not in text:
                continue
            covered += 1
            self.assertIn("{{ASK_FORMAT}}", text, template)
        self.assertGreaterEqual(covered, 5)

    def test_implement_uses_focused_execution_contracts(self):
        template = (SKILLS / "implement" / "SKILL.md.tmpl").read_text(encoding="utf-8")
        self.assertNotIn("{{SUPERVISION_LOOP}}", template)
        for fragment in ("PROGRESS_EVENTS", "HUMAN_DECISION", "DISPATCH_PROTOCOL", "DEV_CONFIG"):
            self.assertNotIn("{{" + fragment + "}}", template)
        self.assertIn("../shared/fragments/human-decision.md", template)

    def test_generated_documents_match_templates_and_fragments(self):
        result = subprocess.run(
            ["node", "--experimental-strip-types", str(SKILLS / "scripts" / "gen-skill-docs.ts"), "--check"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_how_preserves_parallel_exploration_and_independent_synthesis(self):
        template = (SKILLS / "how" / "SKILL.md.tmpl").read_text(encoding="utf-8")
        self.assertIn("2–4", template)
        self.assertIn("references/explorer-prompt.md", template)
        self.assertIn("references/explainer-prompt.md", template)
        self.assertIn("顺序降级", template)
        self.assertIn("不做实质性重写", template)
        self.assertNotIn("grok-", template)
        for reference in ("explorer-prompt.md", "explainer-prompt.md"):
            self.assertTrue((SKILLS / "how" / "references" / reference).is_file())

    def test_why_preserves_seven_sources_confidence_and_planning_constraints(self):
        template = (SKILLS / "why" / "SKILL.md.tmpl").read_text(encoding="utf-8")
        for term in ("Source control", "Issue / ticket", "Long-form documents", "Real-time team chat",
                     "Infrastructure observability", "Error / exception tracking", "Product analytics warehouse",
                     "Direct / Supported / Inferred / Speculative / Unknown",
                     "Preserve / Change / Avoid / Risk", "references/epistemics.md",
                     "references/investigator-prompt.md", "references/synthesizer-prompt.md"):
            self.assertIn(term, template)
        self.assertIn("agent mode", template)
        self.assertIn("本地无 PR", template)
        for source in ("code-archaeology.md", "linear.md", "notion.md", "slack.md",
                       "datadog.md", "sentry.md", "databricks.md", "incident-postmortem.md"):
            self.assertTrue((SKILLS / "why" / "references" / "sources" / source).is_file(), source)

    def test_how_why_are_distributed_and_feed_tech_spec(self):
        sh = (REPO / "sync.sh").read_text(encoding="utf-8")
        ps1 = (REPO / "sync.ps1").read_text(encoding="utf-8")
        self.assertRegex(sh, r"FLAT_ITEMS=\([^\n]*\bhow\b[^\n]*\bwhy\b")
        self.assertIn('"how","why"', ps1)
        tech_spec = (SKILLS / "tech-spec" / "SKILL.md.tmpl").read_text(encoding="utf-8")
        for term in ("/how", "/why", "base + graft", "usage-first", "check-design", "redesign"):
            self.assertIn(term, tech_spec)

    def test_sync_never_rewrites_generated_documents_implicitly(self):
        sh = (REPO / "sync.sh").read_text(encoding="utf-8")
        ps1 = (REPO / "sync.ps1").read_text(encoding="utf-8")
        self.assertIn('gen-skill-docs.ts" --check', sh)
        self.assertIn('gen-skill-docs.ts") --check', ps1)
        self.assertIn("dsh-progress-panel", sh)
        self.assertIn("dsh-progress-panel", ps1)
        self.assertIn("POSIX-only", ps1)
        self.assertIn("__pycache__", sh)
        self.assertIn("__pycache__", ps1)


if __name__ == "__main__":
    unittest.main()
