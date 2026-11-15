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
        self.assertIn("{{PROGRESS_EVENTS}}", template)
        self.assertIn("{{HUMAN_DECISION}}", template)

    def test_generated_documents_match_templates_and_fragments(self):
        result = subprocess.run(
            ["node", "--experimental-strip-types", str(SKILLS / "scripts" / "gen-skill-docs.ts"), "--check"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
