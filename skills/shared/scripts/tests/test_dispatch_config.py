#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dispatch_config as dc


ROUTES = {
    "codex": ("gpt-6-astra", "low", "gpt-6.1-sol", "medium"),
    "claude-code": ("claude-opus-5-5", "medium", "claude-sonnet-5-5", "medium"),
    "zcode": ("bigmodel-api/GLM-5.3", "high", "deepseek/deepseek-flash", "high"),
    "dsh": ("deepseek-official/deepseek-v4-pro", "high", "deepseek-official/deepseek-flash", "high"),
}


def fixture() -> dict:
    hosts = {}
    for host, (supervisor_model, supervisor_effort, development_model, development_effort) in ROUTES.items():
        hosts[host] = {
            "supervisor-review": {"model": supervisor_model, "effort": supervisor_effort},
            "development": {
                tier: {"model": development_model, "effort": development_effort}
                for tier in ("default", "escalated")
            },
        }
    return {
        "dispatch": {
            "schema-version": 1,
            "executor": "subagent",
            "parallel-max": 2,
            "hosts": hosts,
        }
    }


class DispatchConfigTest(unittest.TestCase):
    def setUp(self):
        self.dispatch = dc.validate(fixture())

    def test_resolve_all_host_development_native_parameters(self):
        expected = {
            "codex": {"agent_type": "development", "model": "gpt-6.1-sol", "model_reasoning_effort": "medium"},
            "claude-code": {"subagent_type": "development", "model": "claude-sonnet-5-5", "effort": "medium"},
            "zcode": {"subagent_type": "development", "model": "deepseek/deepseek-flash", "thoughtLevel": "high"},
            "dsh": {"provider": "deepseek-official", "model": "deepseek-flash", "reasoning_effort": "high"},
        }
        for host, native in expected.items():
            with self.subTest(host=host):
                result = dc.resolve(self.dispatch, host, "development")
                self.assertEqual(result["native"], native)
                self.assertEqual(result["context"], "independent-subagent")

    def test_supervisor_and_review_share_model_but_not_context(self):
        expected_effort_keys = {
            "codex": "model_reasoning_effort",
            "claude-code": "effortLevel",
            "zcode": "thoughtLevel",
            "dsh": "reasoningEffort",
        }
        for host in dc.HOSTS:
            with self.subTest(host=host):
                supervisor = dc.resolve(self.dispatch, host, "supervisor")
                review = dc.resolve(self.dispatch, host, "review")
                self.assertEqual(supervisor["context"], "current-session")
                self.assertEqual(review["context"], "independent-subagent")
                self.assertEqual(supervisor["model"], review["model"])
                self.assertEqual(supervisor["effort"], review["effort"])
                self.assertIn(expected_effort_keys[host], supervisor["native"])
                self.assertNotIn("agent_type", supervisor["native"])
                self.assertNotIn("subagent_type", supervisor["native"])

    def test_development_escalated_uses_distinct_native_role(self):
        for host in ("codex", "claude-code", "zcode"):
            with self.subTest(host=host):
                result = dc.resolve(self.dispatch, host, "development", "escalated")
                key = "agent_type" if host == "codex" else "subagent_type"
                self.assertEqual(result["native"][key], "development-escalated")
                self.assertEqual(result["tier"], "escalated")

    def test_old_format_fails_with_migration_error(self):
        with self.assertRaisesRegex(dc.ConfigError, "dispatch.model 已废止"):
            dc.validate({"dispatch": {"model": "gpt-6.1-sol"}})

    def test_missing_current_host_fails_closed(self):
        dispatch = dc.validate({
            "dispatch": {
                "schema-version": 1,
                "executor": "subagent",
                "parallel-max": 1,
                "hosts": {"codex": fixture()["dispatch"]["hosts"]["codex"]},
            }
        })
        with self.assertRaisesRegex(dc.ConfigError, "不得跨宿主"):
            dc.resolve(dispatch, "zcode", "development")

    def test_placeholder_and_cross_host_model_syntax_fail(self):
        bad = fixture()
        bad["dispatch"]["hosts"]["zcode"]["supervisor-review"]["model"] = "<model>"
        with self.assertRaisesRegex(dc.ConfigError, "具体模型 ID"):
            dc.validate(bad)
        bad = fixture()
        bad["dispatch"]["hosts"]["codex"]["supervisor-review"]["model"] = "deepseek/deepseek-flash"
        with self.assertRaisesRegex(dc.ConfigError, "provider 前缀"):
            dc.validate(bad)

    def test_catalog_checks_model_and_effort(self):
        catalogs = {
            host: {
                values[0]: [values[1]],
                values[2]: [values[3]],
            }
            for host, values in ROUTES.items()
        }
        dc.validate(fixture(), catalogs)
        catalogs["zcode"]["deepseek/deepseek-flash"] = ["low"]
        with self.assertRaisesRegex(dc.ConfigError, "不支持 effort=high"):
            dc.validate(fixture(), catalogs)
        del catalogs["codex"]["gpt-6-astra"]
        with self.assertRaisesRegex(dc.ConfigError, "模型目录中不存在"):
            dc.validate(fixture(), catalogs)

    def test_render_native_files(self):
        claude = dc.render(self.dispatch, "claude-code")
        self.assertIn("effort: medium", claude["agents/development.md"])
        self.assertEqual(json.loads(claude["settings.json"])["model"], "claude-opus-5-5")
        codex = dc.render(self.dispatch, "codex")
        self.assertIn("[agents.supervisor-review]", codex["config.toml"])
        self.assertIn('model_reasoning_effort = "medium"', codex["agents/development.toml"])
        zcode = dc.render(self.dispatch, "zcode")
        self.assertIn("thoughtLevel: high", zcode["agents/supervisor-review.md"])
        dsh = dc.render(self.dispatch, "dsh")
        self.assertIn("provider: spawn", dsh["cordis.patch.yml"])
        self.assertIn("toolName: subagent", dsh["cordis.patch.yml"])
        self.assertIn("modelSelectionSettings: true", dsh["cordis.patch.yml"])
        self.assertIn("- insert:\n    - id: subagent-model-selection-settings", dsh["cordis.patch.yml"])
        self.assertIn("model: deepseek-v4-pro", dsh["cordis.patch.yml"])
        self.assertIn("model: deepseek-flash", dsh["cordis.patch.yml"])
        desktop = dc.render(self.dispatch, "dsh", "desktop")
        self.assertNotIn("- insert:", desktop["cordis.patch.yml"])
        self.assertIn("- id: subagent-model-selection-settings", desktop["cordis.patch.yml"])

    def test_write_rendered_is_idempotent_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            files = dc.render(self.dispatch, "zcode")
            dc.write_rendered(files, output)
            dc.write_rendered(files, output)
            path = output / "agents/development.md"
            path.write_text("user-owned\n", encoding="utf-8")
            with self.assertRaisesRegex(dc.ConfigError, "拒绝覆盖"):
                dc.write_rendered(files, output)
            self.assertEqual(path.read_text(encoding="utf-8"), "user-owned\n")


if __name__ == "__main__":
    unittest.main()
