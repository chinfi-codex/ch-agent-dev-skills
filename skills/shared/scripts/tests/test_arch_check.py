#!/usr/bin/env python3
"""Unit and CLI tests for the Python ARCH-CHECK."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
SCRIPT = SCRIPTS / "arch_check.py"
sys.path.insert(0, str(SCRIPTS))

import arch_check as ac


def rules(**overrides):
    value = {
        "schema": ac.SCHEMA_NAME,
        "version": 1,
        "language": "python",
        "source_roots": ["."],
        "architecture": {
            "title": "Demo",
            "summary": "controller -> service -> model; no reverse dependencies.",
            "layers": [
                {"name": "presentation", "description": "HTTP/UI", "may_depend_on": ["domain"]},
                {"name": "domain", "description": "business", "may_depend_on": ["data"]},
                {"name": "data", "description": "storage", "may_depend_on": []},
            ],
            "placement": ["Controllers live in app/controllers."],
        },
        "groups": [
            {"id": "controller", "paths": ["app/controllers/**"], "layer": "presentation", "description": "handlers"},
            {"id": "service", "paths": ["app/services/**"], "layer": "domain", "description": "business"},
            {"id": "model", "paths": ["app/model/**"], "layer": "data", "description": "models"},
        ],
        "rules": {
            "forbidden": [], "cycles": [], "banned_calls": [], "methods": [],
            "types": [], "comments": [], "duplication": [],
        },
    }
    value.update(overrides)
    return value


def rule(ident, severity="error", **extra):
    value = {"id": ident, "severity": severity,
             "comment": "Step 1: find the violation.\nStep 2: fix it or update the rule."}
    value.update(extra)
    return value


class ArchCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.write("app/controllers/x.py", "def handler():\n    return 1\n")
        self.write("app/services/y.py", "def run():\n    return 1\n")
        self.write("app/model/z.py", "VALUE = 1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def check(self, value, worktree=True):
        return ac.check_repo(self.root, rules=value, worktree=worktree)

    def test_forbidden_dependency_and_cycle(self):
        self.write("app/controllers/x.py", "from app.services.y import run\n")
        self.write("app/services/y.py", "from app.model.z import VALUE\n")
        self.write("app/model/z.py", "from app.controllers.x import handler\n")
        value = rules()
        value["rules"]["forbidden"] = [rule("model-no-upward", **{"from": ["model"], "to": ["controller"]})]
        value["rules"]["cycles"] = [rule("no-cycles", groups=["controller", "service", "model"])]
        findings, _infos, _ = self.check(value)
        names = {item["rule"] for item in findings}
        self.assertIn("model-no-upward", names)
        self.assertIn("no-cycles", names)
        self.assertTrue(all(item["severity"] == "error" for item in findings if item["rule"] in names))

    def test_relative_imports_resolve_groups(self):
        self.write("src/app/controllers/x.py", "def handler():\n    return 1\n")
        self.write("src/app/model/z.py", "from ..controllers.x import handler\n")
        value = rules(source_roots=["src"])
        value["groups"] = [
            {"id": "controller", "paths": ["src/app/controllers/**"], "layer": "presentation", "description": "handlers"},
            {"id": "model", "paths": ["src/app/model/**"], "layer": "data", "description": "models"},
        ]
        value["rules"]["forbidden"] = [rule("model-no-upward", **{"from": ["model"], "to": ["controller"]})]
        findings, _infos, _ = self.check(value)
        self.assertTrue(any(item["rule"] == "model-no-upward" for item in findings))

    def test_banned_call_alias_and_source_root(self):
        self.write("src/app/services/a.py", "import subprocess as sp\nfrom subprocess import run as r\n\ndef f():\n    sp.run(['ls'])\n    r(['pwd'])\n")
        value = rules(source_roots=["src"])
        value["groups"] = [
            {"id": "service", "paths": ["src/app/services/**"], "layer": "domain", "description": "business"},
        ]
        value["rules"]["banned_calls"] = [rule("no-shell", severity="warn", **{"from": ["service"]},
                                               calls=["subprocess.*"])]
        findings, _infos, _ = self.check(value)
        self.assertEqual(len([item for item in findings if item["rule"] == "no-shell"]), 2)

    def test_multiple_source_roots_resolve_aliases(self):
        self.write("tools/pandu_contracts/a.py", "from capabilities.knowledge.x import helper\n")
        self.write("capabilities/knowledge/x.py", "def helper():\n    return 1\n")
        value = rules(source_roots=["tools", "capabilities", "."])
        value["groups"] = [
            {"id": "contracts", "paths": ["tools/pandu_contracts/**"], "layer": "data", "description": "contracts"},
            {"id": "capabilities", "paths": ["capabilities/**"], "layer": "domain", "description": "capabilities"},
        ]
        value["rules"]["forbidden"] = [rule("contracts-no-capability", **{"from": ["contracts"], "to": ["capabilities"]})]
        findings, _infos, _ = self.check(value)
        self.assertTrue(any(item["rule"] == "contracts-no-capability" for item in findings))

    def test_dev_tests_and_hidden_dirs_are_skipped(self):
        (self.root / "dev").mkdir()
        (self.root / ".local").mkdir()
        (self.root / "dev" / "bad.py").write_text("from app.controllers.x import handler\n", encoding="utf-8")
        (self.root / ".local" / "bad.py").write_text("from app.controllers.x import handler\n", encoding="utf-8")
        value = rules()
        value["rules"]["forbidden"] = [rule("model-no-upward", **{"from": ["model"], "to": ["controller"]})]
        findings, _infos, _ = self.check(value)
        self.assertFalse(any(item["path"].startswith("dev/") or item["path"].startswith(".local/") for item in findings))

    def test_max_params_and_class_lines(self):
        self.write("app/services/y.py", "def run(a, b, c):\n    return a\n\n\nclass Big:\n    x = 1\n    y = 2\n    z = 3\n")
        value = rules()
        value["rules"]["methods"] = [rule("max-params", max_params=2, exclude=[], count_varargs=False)]
        value["rules"]["types"] = [rule("max-class", severity="warn", max_lines=3, exclude=[])]
        findings, _infos, _ = self.check(value)
        self.assertTrue(any(item["rule"] == "max-params" and item["line"] == 1 for item in findings))
        self.assertTrue(any(item["rule"] == "max-class" for item in findings))

    def test_comment_rules(self):
        self.write("app/services/y.py", "def f():\n    # return 1\n    # TODO: remove\n    \"\"\"long\n    doc\n    \"\"\"\n    return 1\n")
        value = rules()
        value["rules"]["comments"] = [rule(
            "comments", severity="warn",
            kinds=["commented_code", "placeholder", "long_docstring"],
            max_lines=2, max_docstring_lines=1, exclude=[], allow=[])]
        findings, _infos, _ = self.check(value)
        messages = [item["message"] for item in findings if item["rule"] == "comments"]
        self.assertTrue(any("commented-out" in message for message in messages))
        self.assertTrue(any("placeholder" in message for message in messages))
        self.assertTrue(any("docstring" in message for message in messages))

    def test_duplication(self):
        self.write("app/services/y.py", "def one():\n    a = 1\n    a = a + 1\n    a = a * 2\n    return a\n\n\ndef two():\n    a = 1\n    a = a + 1\n    a = a * 2\n    return a\n")
        value = rules()
        value["rules"]["duplication"] = [rule("dup", severity="warn", max_rate=10, min_lines=4, exclude=[])]
        findings, _infos, _ = self.check(value)
        self.assertTrue(any(item["rule"] == "dup" for item in findings))

    def test_render_write_and_check(self):
        value = rules()
        (self.root / "docs").mkdir()
        (self.root / "docs/ARCH-CHECK.json").write_text(json.dumps(value), encoding="utf-8")
        written = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "render", "write", "--repo", str(self.root)],
            capture_output=True, text=True)
        self.assertEqual(written.returncode, 0, written.stdout + written.stderr)
        checked = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "render", "check", "--repo", str(self.root), "--worktree"],
            capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        (self.root / "docs/ARCHITECTURE.md").write_text("hand edit\n", encoding="utf-8")
        stale = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "render", "check", "--repo", str(self.root), "--worktree"],
            capture_output=True, text=True)
        self.assertEqual(stale.returncode, 1)

    def test_render_includes_eleven_section_overview(self):
        value = rules()
        value["architecture"]["overview"] = {
            "project": {"name": "Demo", "repository": "git@example.com:demo.git",
                        "team": "core", "updated": "2026-11-19"},
            "structure": "demo/\n  app/",
            "diagram": "flowchart LR\n  A --> B",
            "components": [{"name": "API", "description": "entry", "technologies": ["Flask"], "deployment": "container"}],
            "data_stores": [{"name": "main", "type": "PostgreSQL", "purpose": "business", "keys": ["orders"]}],
            "integrations": [{"name": "pay", "purpose": "collect", "method": "REST"}],
            "deployment": {"cloud": "aliyun", "services": ["ACK"], "ci_cd": "GitLab CI", "monitoring": "Prometheus"},
            "security": {"authentication": "OIDC", "authorization": "RBAC", "encryption": "TLS", "practices": ["no secrets"]},
            "development": {"setup": "make dev", "test_frameworks": ["pytest"], "quality_tools": ["ruff"]},
            "roadmap": ["split reports"],
        }
        text = ac.render_guide(value).decode("utf-8")
        for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.",
                        "## 7.", "## 8.", "## 9.", "## 10.", "## 11.",
                        "## 附录 A", "## 附录 E"):
            self.assertIn(heading, text)
        self.assertIn("flowchart LR", text)
        self.assertIn("GitLab CI", text)
        self.assertIn("docs/GLOSSARY.md", text)
        self.assertNotIn("未登记", text)
        self.assertEqual(ac.validate_rules(value), [])

    def test_overview_notes_mark_sections_not_applicable(self):
        value = rules()
        value["architecture"]["overview"] = {
            "notes": {
                "data_stores": "不适用：纯静态工具仓，无持久化存储。",
                "integrations": "不适用：不调用外部服务。",
            },
        }
        text = ac.render_guide(value).decode("utf-8")
        self.assertIn("不适用：纯静态工具仓，无持久化存储。", text)
        self.assertIn("不适用：不调用外部服务。", text)
        self.assertNotIn("## 4. 数据存储\n\n> 未登记", text)
        self.assertEqual(ac.validate_rules(value), [])

    def test_overview_rejects_fence_injection(self):
        value = rules()
        value["architecture"]["overview"] = {"diagram": "flowchart LR\n```\nA --> B"}
        errors = ac.validate_rules(value)
        self.assertTrue(any("triple-backtick" in error for error in errors))

    def test_init_renders_skeleton_and_refuses_unmanaged_guide(self):
        (self.root / "docs").mkdir()
        ac.init_repo(self.root)
        text = (self.root / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("## 11. 术语与缩写", text)
        self.assertIn("未登记", text)

    def test_init_refuses_unmanaged_architecture_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs/ARCHITECTURE.md").write_text("# legacy\n", encoding="utf-8")
            with self.assertRaises(ac.InputError):
                ac.init_repo(root)

    def test_validate_rejects_unknown_group_and_missing_steps(self):
        value = rules()
        bogus = rule("bogus", **{"from": ["missing"], "to": ["missing"]})
        bogus["comment"] = "no steps"
        value["rules"]["forbidden"] = [bogus]
        errors = ac.validate_rules(value)
        self.assertTrue(any("unknown group" in error for error in errors))
        self.assertTrue(any("Step 1" in error and "Step 2" in error for error in errors))

    def test_schema_rejects_unknown_field(self):
        value = rules()
        value["unknown"] = True
        errors = ac.validate_rules(value)
        self.assertTrue(errors)

    def test_path_safety_rejects_escape(self):
        value = rules()
        value["groups"][0]["paths"] = ["../outside/**"]
        errors = ac.validate_rules(value)
        self.assertTrue(any("inside the repository" in error or "relative" in error for error in errors))

    def test_init_refuses_overwrite(self):
        (self.root / "docs").mkdir()
        first = ac.init_repo(self.root)
        self.assertTrue(first.is_file())
        self.assertTrue((self.root / "docs/ARCHITECTURE.md").is_file())
        with self.assertRaises(ac.InputError):
            ac.init_repo(self.root)

    def test_hook_install_preserves_existing_and_is_idempotent(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        hook = self.root / ".git/hooks/pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")
        path = ac.install_hook(self.root)
        text = path.read_text(encoding="utf-8")
        self.assertIn("echo existing", text)
        self.assertEqual(text.count(ac.MARK_BEGIN), 1)
        ac.install_hook(self.root)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(ac.MARK_BEGIN), 1)
        self.assertEqual(text.count(ac.MARK_END), 1)

    def test_cli_exit_codes(self):
        (self.root / "docs").mkdir()
        (self.root / "docs/ARCH-CHECK.json").write_text(json.dumps(rules()), encoding="utf-8")
        (self.root / "docs/ARCHITECTURE.md").write_bytes(ac.render_guide(rules()))
        clean = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "check", "--repo", str(self.root), "--worktree"],
            capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        broken = rules()
        broken["rules"]["forbidden"] = [rule("model-no-upward", **{"from": ["model"], "to": ["controller"]})]
        self.write("app/model/z.py", "from app.controllers.x import handler\n")
        (self.root / "docs/ARCH-CHECK.json").write_text(json.dumps(broken), encoding="utf-8")
        (self.root / "docs/ARCHITECTURE.md").write_bytes(ac.render_guide(broken))
        failed = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "check", "--repo", str(self.root), "--worktree"],
            capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertIn("Step 1", failed.stdout)

    def test_revision_check_uses_committed_blob(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "a@b"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "a"], cwd=self.root, check=True)
        value = rules()
        value["rules"]["forbidden"] = [rule("model-no-upward", **{"from": ["model"], "to": ["controller"]})]
        (self.root / "docs").mkdir()
        (self.root / "docs/ARCH-CHECK.json").write_text(json.dumps(value), encoding="utf-8")
        (self.root / "docs/ARCHITECTURE.md").write_bytes(ac.render_guide(value))
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        (self.root / "app/model/z.py").write_text("from app.controllers.x import handler\n", encoding="utf-8")
        findings, _infos, _ = ac.check_repo(self.root, "HEAD", rules=value)
        self.assertFalse(any(item["rule"] == "model-no-upward" for item in findings))
        worktree_findings, _infos, _ = ac.check_repo(self.root, rules=value, worktree=True)
        self.assertTrue(any(item["rule"] == "model-no-upward" for item in worktree_findings))


if __name__ == "__main__":
    unittest.main()
