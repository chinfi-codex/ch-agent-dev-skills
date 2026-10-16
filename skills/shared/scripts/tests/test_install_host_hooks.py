#!/usr/bin/env python3
"""install_host_hooks.py 的非破坏合并、幂等与跨宿主 schema 测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "install_host_hooks.py"


class InstallHostHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="host hooks 安装 ")
        self.root = Path(self.tmp.name)
        self.script = self.root / "scripts with space" / "session_title.py"
        self.script.parent.mkdir()
        self.script.write_text("# test\n", encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"SESSION_TITLE_PYTHON": sys.executable})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_install(self, host, config):
        return subprocess.run([sys.executable, str(SCRIPT), "--host", host, "--script", str(self.script),
                               "--config", str(config)], capture_output=True, text=True)

    def test_claude_preserves_existing_settings_and_is_idempotent(self):
        config = self.root / "settings.json"
        original = {"env": {"A": "B"}, "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other hook"}]}]}}
        config.write_text(json.dumps(original), encoding="utf-8")
        for _ in range(2):
            result = self.run_install("claude", config)
            self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["env"], original["env"])
        self.assertEqual(len(data["hooks"]["UserPromptSubmit"]), 2)
        managed = [g for g in data["hooks"]["UserPromptSubmit"] if "session_title.py" in str(g)]
        self.assertEqual(len(managed), 1)
        self.assertIn("PostToolUse", data["hooks"])
        self.assertEqual(data["hooks"]["PostToolUse"][0]["matcher"], "Write|Edit")

    def test_codex_uses_flat_hooks_and_zcode_uses_events(self):
        codex = self.root / "codex.json"
        zcode = self.root / "zcode.json"
        zcode.write_text('{"mcp":{"servers":{"keep":{}}},"hooks":{"events":{}}}', encoding="utf-8")
        self.assertEqual(self.run_install("codex", codex).returncode, 0)
        self.assertEqual(self.run_install("zcode", zcode).returncode, 0)
        codex_data = json.loads(codex.read_text(encoding="utf-8"))
        zcode_data = json.loads(zcode.read_text(encoding="utf-8"))
        self.assertIn("SessionStart", codex_data["hooks"])
        self.assertTrue(zcode_data["hooks"]["enabled"])
        self.assertIn("Stop", zcode_data["hooks"]["events"])
        self.assertEqual(zcode_data["mcp"], {"servers": {"keep": {}}})

    def test_invalid_json_is_not_overwritten(self):
        config = self.root / "broken.json"
        config.write_text("{broken", encoding="utf-8")
        result = self.run_install("claude", config)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(config.read_text(encoding="utf-8"), "{broken")

    def test_only_owned_hook_is_replaced(self):
        config = self.root / "settings.json"
        config.write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "keep", "hooks": [{"type": "command", "command": "other"}]},
            {"hooks": [{"type": "command", "command": "python old/session_title.py hook --host claude"}]}
        ]}}), encoding="utf-8")
        self.assertEqual(self.run_install("claude", config).returncode, 0)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        self.assertEqual(data["hooks"]["Stop"][0]["matcher"], "keep")

    def test_remove_only_owned_hooks(self):
        config = self.root / "settings.json"
        config.write_text(json.dumps({"env": {"KEEP": "1"}, "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "other"}]},
            {"hooks": [{"type": "command", "command": "python old/session_title.py hook --host codex"}]}
        ]}}), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT), "--host", "codex", "--script", str(self.script),
                                 "--config", str(config), "--remove"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["env"], {"KEEP": "1"})
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertNotIn("session_title.py", str(data))


if __name__ == "__main__":
    unittest.main()
