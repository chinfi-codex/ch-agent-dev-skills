#!/usr/bin/env python3
"""Tests for the slim ARCH-CHECK contract sweep (MOD-1 / SPEC-1 / GUIDE-1)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
SCRIPT = SCRIPTS / "contract_check.py"
sys.path.insert(0, str(SCRIPTS))

import arch_check as ac

GLOSSARY = """# GLOSSARY

## feature-module 归档

| feature-module | 说明 | 成员 feature-slug |
| --- | --- | --- |
| 判读配置 | 判读规则与模板配置 | 文档驱动模板配置 |
| 数据接入 | 外部数据源接入 | 实时流接入 |
"""

RULES = {
    "schema": ac.SCHEMA_NAME,
    "version": 1,
    "language": "python",
    "source_roots": ["."],
    "architecture": {"title": "Demo", "summary": "Layered.", "layers": [], "placement": []},
    "groups": [],
    "rules": {
        "forbidden": [], "cycles": [], "banned_calls": [], "methods": [],
        "types": [], "comments": [], "duplication": [],
    },
}

SPEC = """---
doc_type: tech-spec
status: 已确认（待拆解）
supersedes: {target}
---

# Tech Spec

### 3.2 架构影响（层 / 组 / 规则）

| 层 / 组 | 动作 | 路径 / 规则 ID | 说明 | 验证方式 |
| --- | --- | --- | --- | --- |
| 无 | — | — | 无架构影响 | — |
"""


def run(*args):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True)


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write_rules(self):
        (self.root / "docs" / "ARCH-CHECK.json").write_text(json.dumps(RULES), encoding="utf-8")
        (self.root / "docs" / "ARCHITECT.md").write_bytes(ac.render_guide(RULES))

    def check(self):
        return run("--check", "--repo", str(self.root))

    def test_init_creates_rules_and_guide_and_refuses_overwrite(self):
        first = run("--init", "--repo", str(self.root))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue((self.root / "docs" / "ARCH-CHECK.json").is_file())
        self.assertTrue((self.root / "docs" / "ARCHITECT.md").is_file())
        second = run("--init", "--repo", str(self.root))
        self.assertEqual(second.returncode, 2)

    def test_mod1_clean_passes(self):
        (self.root / "docs" / "GLOSSARY.md").write_text(GLOSSARY, encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("MOD-1", result.stdout)

    def test_mod1_duplicate_module_fails(self):
        duplicate = GLOSSARY.replace("| 数据接入 | 外部数据源接入 | 实时流接入 |",
                                     "| 判读配置 | 外部数据源接入 | 实时流接入 |")
        (self.root / "docs" / "GLOSSARY.md").write_text(duplicate, encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate GLOSSARY module", result.stdout)

    def test_spec1_missing_target_fails(self):
        spec = self.root / "dev/features/m/s/demo-tech-spec-2026-01-01.md"
        spec.parent.mkdir(parents=True)
        spec.write_text(SPEC.format(target="missing.md"), encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("supersedes missing tech-spec", result.stdout)

    def test_spec1_cycle_fails(self):
        base = self.root / "dev/features/m/s"
        base.mkdir(parents=True)
        (base / "a-tech-spec-2026-01-01.md").write_text(SPEC.format(target="b-tech-spec-2026-01-01.md"), encoding="utf-8")
        (base / "b-tech-spec-2026-01-01.md").write_text(SPEC.format(target="a-tech-spec-2026-01-01.md"), encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("supersedes cycle", result.stdout)

    def test_guide_without_rules_is_info(self):
        (self.root / "docs" / "GLOSSARY.md").write_text(GLOSSARY, encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("GUIDE-1", result.stdout)

    def test_guide_missing_or_stale_fails(self):
        self.write_rules()
        (self.root / "docs" / "ARCHITECT.md").write_text("hand edited\n", encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("stale or hand-edited", result.stdout)

    def test_guide_valid_passes(self):
        self.write_rules()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_invalid_rules_fail(self):
        bad = dict(RULES)
        bad["rules"] = dict(RULES["rules"])
        bad["rules"]["forbidden"] = [{
            "id": "x", "severity": "error", "from": ["missing"], "to": ["missing"],
            "comment": "no steps",
        }]
        (self.root / "docs" / "ARCH-CHECK.json").write_text(json.dumps(bad), encoding="utf-8")
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown group", result.stdout)


if __name__ == "__main__":
    unittest.main()
