#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
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
        bad["dispatch"]["hosts"]["claude-code"]["supervisor-review"]["model"] = "deepseek/deepseek-flash"
        with self.assertRaisesRegex(dc.ConfigError, "provider 前缀"):
            dc.validate(bad)
        bad = fixture()
        bad["dispatch"]["hosts"]["codex"]["supervisor-review"]["model"] = "group/never-stuck/extra"
        with self.assertRaisesRegex(dc.ConfigError, "<namespace>/<route>"):
            dc.validate(bad)

    def test_codex_accepts_bare_model_or_namespaced_route(self):
        candidate = fixture()
        candidate["dispatch"]["hosts"]["codex"]["supervisor-review"]["model"] = "group/never-stuck"
        validated = dc.validate(candidate)
        route = validated["hosts"]["codex"]["supervisor-review"]
        self.assertEqual(route["model"], "group/never-stuck")
        self.assertEqual(dc.resolve(validated, "codex", "supervisor")["native"]["model"],
                         "group/never-stuck")
        bare = deepcopy(candidate)
        bare["dispatch"]["hosts"]["codex"]["supervisor-review"]["model"] = "gpt-6.2-sol"
        self.assertEqual(dc.validate(bare)["hosts"]["codex"]["supervisor-review"]["model"], "gpt-6.2-sol")

    def test_zcode_accepts_namespaced_model_route(self):
        candidate = fixture()
        candidate["dispatch"]["hosts"]["zcode"]["supervisor-review"]["model"] = "magpie/group/never-stuck"
        validated = dc.validate(candidate)
        route = validated["hosts"]["zcode"]["supervisor-review"]
        self.assertEqual(route["model"], "magpie/group/never-stuck")
        self.assertEqual(dc.resolve(validated, "zcode", "supervisor")["native"]["model"],
                         "magpie/group/never-stuck")
        dsh_candidate = deepcopy(candidate)
        dsh_candidate["dispatch"]["hosts"]["dsh"]["supervisor-review"]["model"] = "magpie/group/never-stuck"
        with self.assertRaisesRegex(dc.ConfigError, "完整路由"):
            dc.validate(dsh_candidate)

    def test_zcode_still_requires_provider_route(self):
        candidate = fixture()
        candidate["dispatch"]["hosts"]["zcode"]["supervisor-review"]["model"] = "never-stuck"
        with self.assertRaisesRegex(dc.ConfigError, "完整路由"):
            dc.validate(candidate)

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

    def test_execution_checks_only_selected_host(self):
        for host, values in ROUTES.items():
            with self.subTest(host=host):
                candidate = fixture()
                for other in dc.HOSTS:
                    if other != host:
                        candidate["dispatch"]["hosts"][other] = {"invalid": True}
                catalog = {host: {values[0]: [values[1]], values[2]: [values[3]]}}
                result = dc.validate(candidate, catalog, host=host)
                self.assertEqual(list(result["hosts"]), [host])
                with self.assertRaisesRegex(dc.ConfigError, "catalogs"):
                    dc.validate(candidate, {}, host=host)
                catalog[host][values[0]] = []
                with self.assertRaisesRegex(dc.ConfigError, "不支持 effort"):
                    dc.validate(candidate, catalog, host=host)
                del catalog[host][values[0]]
                with self.assertRaisesRegex(dc.ConfigError, "模型目录中不存在"):
                    dc.validate(candidate, catalog, host=host)

    def test_selected_host_must_be_configured(self):
        candidate = fixture()
        del candidate["dispatch"]["hosts"]["codex"]
        with self.assertRaisesRegex(dc.ConfigError, "当前宿主 codex 未配置"):
            dc.validate(candidate, host="codex")
        with self.assertRaisesRegex(dc.ConfigError, "未知宿主"):
            dc.validate(candidate, host="other")

    def test_cli_scopes_validate_resolve_and_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.md"
            def yaml_lines(mapping, indent=0):
                lines = []
                for key, value in mapping.items():
                    prefix = " " * indent + key + ":"
                    if isinstance(value, dict):
                        lines.append(prefix)
                        lines.extend(yaml_lines(value, indent + 2))
                    else:
                        lines.append(prefix + " " + json.dumps(value))
                return lines
            config.write_text("```yaml\n" + "\n".join(yaml_lines(fixture())) + "\n```\n")
            catalog = root / "catalog.json"
            values = ROUTES["codex"]
            catalog.write_text(json.dumps({"codex": {values[0]: [values[1]], values[2]: [values[3]]}}))
            base = [sys.executable, dc.__file__]
            options = ["--config", str(config), "--catalog", str(catalog)]
            for command, extra in [("validate", []), ("resolve", ["--role", "supervisor"]),
                                   ("render", ["--output", str(root / "native")])]:
                with self.subTest(command=command):
                    result = subprocess.run(base + [command] + options + ["--host", "codex"] + extra,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(base + ["validate"] + options, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("必须指定 --host", result.stderr)

    def test_render_native_files(self):
        claude = dc.render(self.dispatch, "claude-code")
        self.assertIn("effort: medium", claude["agents/development.md"])
        self.assertEqual(json.loads(claude["settings.json"])["model"], "claude-opus-5-5")
        codex = dc.render(self.dispatch, "codex")
        self.assertIn("[agents.supervisor-review]", codex["config.toml"])
        self.assertIn('model_reasoning_effort = "medium"', codex["agents/development.toml"])
        zcode = dc.render(self.dispatch, "zcode")
        self.assertIn("thoughtLevel: high", zcode["agents/supervisor-review.md"])
        self.assertIn("receipt", zcode["agents/supervisor-review.md"])
        self.assertIn("human-decision", zcode["agents/development.md"])
        self.assertIn("不直接问人", zcode["agents/development.md"])
        self.assertIn("不合并、不清场", zcode["agents/development-escalated.md"])
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
    def test_dsh_native_session_command_is_canonical_and_validated(self):
        candidate = fixture()
        candidate["dispatch"]["hosts"]["dsh"]["native_session_command"] = "query-native-session --json"
        validated = dc.validate(candidate)
        self.assertEqual(validated["hosts"]["dsh"]["native_session_command"],
                         "query-native-session --json")
        for host in ("zcode", "codex", "claude-code"):
            bad = fixture()
            bad["dispatch"]["hosts"][host]["native_session_command"] = "query"
            with self.subTest(host=host), self.assertRaisesRegex(dc.ConfigError, "仅用于 DSH"):
                dc.validate(bad)
        bad = fixture()
        bad["dispatch"]["hosts"]["dsh"]["native_session_command"] = "query\nrm -rf /"
        with self.assertRaisesRegex(dc.ConfigError, "单行"):
            dc.validate(bad)

    def test_render_check_is_read_only_and_reports_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            files = dc.render(self.dispatch, "zcode")
            self.assertEqual(set(dc.check_rendered(files, output)), set(files))
            dc.write_rendered(files, output)
            before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
            self.assertEqual(dc.check_rendered(files, output), [])
            after = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
            self.assertEqual(before, after)
            target = output / "agents/development.md"
            target.write_text("stale\n", encoding="utf-8")
            self.assertEqual(dc.check_rendered(files, output), ["agents/development.md"])
    def test_direct_isolated_cli_validate_render_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "agents-config.md"
            config.write_text("""```yaml
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
```
""", encoding="utf-8")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"zcode": {
                "provider/supervisor": ["high"],
                "provider/dev": ["medium", "high"],
            }}), encoding="utf-8")
            script = Path(dc.__file__).resolve()

            def cli(*args):
                return subprocess.run([sys.executable, "-I", str(script), *args],
                                      capture_output=True, text=True)

            validated = cli("validate", "--host", "zcode", "--config", str(config), "--catalog", str(catalog))
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
            output = root / "native"
            rendered = cli("render", "--config", str(config), "--host", "zcode", "--output", str(output))
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            checked = cli("render", "--check", "--config", str(config), "--host", "zcode", "--output", str(output))
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            role = output / "agents/development.md"
            role.write_text("stale\n", encoding="utf-8")
            stale = cli("render", "--check", "--config", str(config), "--host", "zcode", "--output", str(output))
            self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
            self.assertIn("agents/development.md", stale.stdout)


if __name__ == "__main__":
    unittest.main()
