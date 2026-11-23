#!/usr/bin/env python3
"""Contract tests for strict v2 agents configuration and YAML subset parsing."""
from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents_config as ac


SCRIPTS = Path(__file__).resolve().parent.parent
TEMPLATE = SCRIPTS.parent / "templates" / "agents-config.md"


def fixture():
    return {
        "schema-version": 2,
        "tracker": "local",
        "main-branch": "main",
        "repo-level": "C",
        "quality-gate": {"fast": "python3 -I tests/test_unit.py"},
        "host-budget": {"total": 4, "max-runs": 2, "queue-timeout": 60},
        "verify-policy": {
            "ticket-max-files": 10,
            "ticket-max-loc": 400,
            "deny": ["protocols/**", "**/*.key"],
            "test-change": ["tests/**"],
            "generated-refresh": [{"id": "architect-guide", "glob": "docs/ARCHITECTURE.md",
                                    "generator": "python3 render.py --write"}],
            "base-freeze": True,
            "attribution": "quarantine",
            "gate-scope": {"categories-file": None, "non-gating": ["perf", "display"],
                            "env-skip": True},
        },
        "feature-closeout": {"mode": "cooperative-ci", "ci": {
            "provider": "gitlab", "base-url": "https://gitlab.example.com",
            "repository": "group/project", "workflow": "full",
            "artifact": "verdict.json", "token-env": "GITLAB_TOKEN",
        }},
        "dispatch": {
            "schema-version": 1,
            "executor": "subagent",
            "parallel-max": 1,
            "hosts": {
                "zcode": {
                    "supervisor-review": {"model": "provider/supervisor", "effort": "high"},
                    "development": {
                        "default": {"model": "provider/dev", "effort": "medium"},
                        "escalated": {"model": "provider/dev", "effort": "high"},
                    },
                }
            },
        },
        "enforcement": {"review-trust": "host-verified", "require-clean-tree": True},
    }


class AgentsConfigTest(unittest.TestCase):
    def test_valid_config_defaults_are_explicit_and_input_not_mutated(self):
        raw = fixture()
        original = copy.deepcopy(raw)
        result = ac.validate_config(raw)
        self.assertEqual(raw, original)
        self.assertEqual(result["conformance"]["mode"], "always")
        self.assertEqual(result["conformance"]["sample-rate"], "1/3")
        self.assertEqual(result["feature-closeout"]["integration-branch"], "feat/<feature-slug>")
        self.assertEqual(result["verify-policy"]["scope"], "strict")
        self.assertEqual(result["artifact-budget"]["integration-dir-max-mb"], 20)
        self.assertTrue(result["integration-runtime"]["ready-timeout-seconds"] > 0)
        self.assertEqual(result["retention"]["keep-generations"], 2)

    def test_current_template_is_valid_v2_without_json_migration(self):
        parsed = ac.read_first_yaml_block(TEMPLATE)
        self.assertEqual(parsed["schema-version"], 2)
        self.assertEqual(parsed["quality-gate"]["fast"], "pytest -q <相关测试文件>")
        self.assertEqual(parsed["conformance"]["sample-rate"], "1/3")
        self.assertEqual(parsed["verify-policy"]["gate-scope"]["non-gating"], ["perf", "display"])
        self.assertNotIn("main-drift-ignore", parsed["feature-closeout"])
        self.assertEqual(len(parsed["dispatch"]["hosts"]), 4)
        validated = ac.validate_config(parsed)
        self.assertEqual(validated["tracker"], "gitlab")
        self.assertEqual(validated["feature-closeout"]["ci"]["provider"], "gitlab")
        self.assertEqual(validated["verify-policy"]["base-freeze"], True)

    def test_pr_closeout_and_remote_execution_plane(self):
        config = fixture()
        config["active-plane"] = "remote"
        config["feature-closeout"] = {
            "mode": "pr", "ci": None,
            "pr": {"provider": "gitea", "base-url": "https://git.example.com",
                   "repository": "AI/repo", "token-env": "PANDU_GITEA_TOKEN"},
        }
        config["execution-planes"] = {
            "local": {"repo": "/Users/dev/repo", "hosts": ["zcode"],
                      "closeout": "local-human", "push": "user-on-closeout"},
            "remote": {"host": "judge", "access": "manual-herdr", "session-manager": "herdr",
                       "repo": "/home/pandu/dev/repo",
                       "worktree-root": "/home/pandu/dev/repo/.worktrees",
                       "interpreter": "/home/pandu/dev/venv/bin/python",
                       "skills-root": "/home/pandu/.dsh/skills",
                       "hosts": ["zcode"], "closeout": "pr", "ci": "disabled",
                       "push": "per-campaign", "unattended": False},
        }
        validated = ac.validate_config(config)
        self.assertEqual(validated["active-plane"], "remote")
        self.assertEqual(validated["feature-closeout"]["mode"], "pr")
        self.assertEqual(validated["execution-planes"]["remote"]["push"], "per-campaign")

        broken = copy.deepcopy(config)
        broken["execution-planes"]["remote"]["ci"] = "enabled"
        with self.assertRaisesRegex(ac.ConfigError, "remote plane must use ci=disabled"):
            ac.validate_config(broken)

        broken = copy.deepcopy(config)
        broken["execution-planes"]["remote"]["repo"] = "/opt/pandu/deploy"
        with self.assertRaisesRegex(ac.ConfigError, "must not live under /opt/pandu"):
            ac.validate_config(broken)

        broken = copy.deepcopy(config)
        broken["feature-closeout"] = {"mode": "pr", "ci": None}
        with self.assertRaisesRegex(ac.ConfigError, "requires feature-closeout.pr"):
            ac.validate_config(broken)

    def test_pr_mode_rejects_ci_object(self):
        config = fixture()
        config["feature-closeout"] = {
            "mode": "pr", "ci": {"provider": "gitlab", "base-url": "https://gitlab.example.com",
                                  "repository": "group/project", "workflow": "full",
                                  "artifact": "verdict.json", "token-env": "GITLAB_TOKEN"},
            "pr": {"provider": "gitea", "base-url": "https://git.example.com",
                   "repository": "AI/repo", "token-env": "PANDU_GITEA_TOKEN"},
        }
        with self.assertRaisesRegex(ac.ConfigError, "ci is only valid"):
            ac.validate_config(config)

    def test_markdown_load_does_not_rewrite_source(self):
        config = fixture()
        yaml_text = """schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'python3 test.py'}
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
    codex:
      supervisor-review: {model: supervisor, effort: high}
      development:
        default: {model: dev, effort: medium}
        escalated: {model: dev, effort: high}
enforcement: {review-trust: operator-attested}
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents-config.md"
            text = "# Config\n\n```yaml\n" + yaml_text + "```\n\nOther markdown stays.\n"
            path.write_text(text, encoding="utf-8")
            loaded = ac.load(path)
            self.assertEqual(loaded["main-branch"], config["main-branch"])
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_unknown_and_legacy_fields_fail(self):
        for mutate in (
                lambda c: c.update({"unknown": True}),
                lambda c: c.update({"redlines": ["tests/**"]}),
                lambda c: c.update({"mechanical-refresh": []}),
                lambda c: c["quality-gate"].update({"scoped": "pytest"}),
                lambda c: c["quality-gate"].update({"full": "pytest"}),
                lambda c: c["verify-policy"].update({"unexpected": 1}),
                lambda c: c["feature-closeout"].update({"main-drift-ignore": ["dev/**"]}),
                lambda c: c["dispatch"].update({"model": "old"}),
        ):
            with self.subTest(mutate=mutate):
                candidate = fixture()
                mutate(candidate)
                with self.assertRaises(ac.ConfigError):
                    ac.validate_config(candidate)

    def test_schema_version_and_bool_integer_confusion_fail(self):
        for version in (1, True, "2", None):
            candidate = fixture()
            candidate["schema-version"] = version
            with self.subTest(version=version), self.assertRaises(ac.ConfigError):
                ac.validate_config(candidate)
        for section, key in (("host-budget", "total"), ("host-budget", "max-runs"),
                             ("host-budget", "queue-timeout")):
            candidate = fixture()
            candidate[section][key] = True
            with self.subTest(section=section, key=key), self.assertRaises(ac.ConfigError):
                ac.validate_config(candidate)
        candidate = fixture()
        candidate["verify-policy"]["ticket-max-loc"] = False
        with self.assertRaises(ac.ConfigError):
            ac.validate_config(candidate)

    def test_invalid_rates_refs_and_globs_fail(self):
        for rate in ("0", "0/3", "2/3/4", "2", "-1/3", "1/0", "abc", True, 0, 1.2):
            candidate = fixture()
            candidate["conformance"] = {"mode": "sampled", "sample-rate": rate,
                                         "checker": "main-session"}
            with self.subTest(rate=rate), self.assertRaises(ac.ConfigError):
                ac.validate_config(candidate)
        for branch in ("../main", "main..x", "/main", "main/", "bad branch", "-bad", "x.lock"):
            candidate = fixture()
            candidate["main-branch"] = branch
            with self.subTest(branch=branch), self.assertRaises(ac.ConfigError):
                ac.validate_config(candidate)
        for pattern in ("", "../secrets/*", "/etc/*", "a//b", "a/{b,c}", "bad[glob"):
            candidate = fixture()
            candidate["verify-policy"]["deny"] = [pattern]
            with self.subTest(pattern=pattern), self.assertRaises(ac.ConfigError):
                ac.validate_config(candidate)

    def test_mode_combinations_fail_closed(self):
        candidate = fixture()
        candidate["feature-closeout"]["mode"] = "server-enforced"
        with self.assertRaisesRegex(ac.ConfigError, "server-enforced requires"):
            ac.validate_config(candidate)
        candidate["enforcement"].update({"require-protected-branch": True,
                                          "require-expected-sha": True})
        self.assertEqual(ac.validate_config(candidate)["feature-closeout"]["mode"], "server-enforced")

        candidate = fixture()
        candidate["enforcement"]["require-protected-branch"] = True
        with self.assertRaisesRegex(ac.ConfigError, "must not claim"):
            ac.validate_config(candidate)

        candidate = fixture()
        candidate["feature-closeout"]["ci"] = None
        with self.assertRaisesRegex(ac.ConfigError, "requires feature-closeout.ci"):
            ac.validate_config(candidate)
        candidate["feature-closeout"]["mode"] = "local-human"
        candidate["feature-closeout"]["ci"] = fixture()["feature-closeout"]["ci"]
        with self.assertRaisesRegex(ac.ConfigError, "only valid"):
            ac.validate_config(candidate)

        candidate = fixture()
        candidate.setdefault("integration-runtime", {})["dev-service"] = {
            "start": "run", "ready": "ready", "stop": "stop", "status": "status"}
        with self.assertRaisesRegex(ac.ConfigError, "dev-service is only valid"):
            ac.validate_config(candidate)
        candidate["feature-closeout"]["mode"] = "local-human"
        candidate["feature-closeout"]["ci"] = None
        self.assertEqual(ac.validate_config(candidate)["integration-runtime"]["dev-service"]["start"], "run")

        candidate = fixture()
        candidate["feature-closeout"] = {"mode": "ci-promote"}
        with self.assertRaises(ac.ConfigError):
            ac.validate_config(candidate)

        candidate = fixture()
        candidate["feature-closeout"] = {"mode": "cooperative-ci",
                                          "verify-instance": {"name": "dev", "deploy": "run", "lease": "lease"}}
        with self.assertRaisesRegex(ac.ConfigError, "only valid"):
            ac.validate_config(candidate)

        candidate = fixture()
        candidate["host-budget"]["max-runs"] = 5
        with self.assertRaisesRegex(ac.ConfigError, "cannot exceed"):
            ac.validate_config(candidate)

    def test_ocr_combinations_and_generator_uniqueness(self):
        candidate = fixture()
        candidate["ocr"] = {"mode": "ci", "provider": "glm", "model": "model",
                             "rule-file": ".opencodereview/rule.json"}
        with self.assertRaisesRegex(ac.ConfigError, "configured together"):
            ac.validate_config(candidate)
        candidate["gitlab-ci"] = "enabled"
        self.assertEqual(ac.validate_config(candidate)["ocr"]["mode"], "ci")

        candidate = fixture()
        candidate["ocr"] = {"mode": "local", "provider": "glm", "model": "<model>",
                             "rule-file": ".opencodereview/rule.json"}
        with self.assertRaisesRegex(ac.ConfigError, "concrete"):
            ac.validate_config(candidate)

        candidate = fixture()
        candidate["verify-policy"]["generated-refresh"] = [
            {"id": "one", "glob": "docs/**", "generator": "one"},
            {"id": "two", "glob": "docs/**", "generator": "two"},
        ]
        with self.assertRaisesRegex(ac.ConfigError, "exactly one generator"):
            ac.validate_config(candidate)
        candidate["verify-policy"]["generated-refresh"] = [
            {"id": "same", "glob": "docs/**", "generator": "one"},
            {"id": "same", "glob": "other/**", "generator": "two"},
        ]
        with self.assertRaisesRegex(ac.ConfigError, "IDs must be unique"):
            ac.validate_config(candidate)
        candidate["verify-policy"]["generated-refresh"] = [
            {"id": "one", "glob": "docs/**", "generator": "same"},
            {"id": "two", "glob": "other/**", "generator": "same"},
        ]
        with self.assertRaisesRegex(ac.ConfigError, "unique generator command"):
            ac.validate_config(candidate)

    def test_dsh_native_session_command_loads_through_strict_schema(self):
        candidate = fixture()
        candidate["dispatch"]["hosts"]["dsh"] = {
            "supervisor-review": {"model": "provider/supervisor", "effort": "high"},
            "development": {
                "default": {"model": "provider/dev", "effort": "medium"},
                "escalated": {"model": "provider/dev", "effort": "high"},
            },
            "native_session_command": "query-native-session --json",
        }
        validated = ac.validate_config(candidate)
        self.assertEqual(validated["dispatch"]["hosts"]["dsh"]["native_session_command"],
                         "query-native-session --json")

    def test_yaml_nested_list_mapping_comments_and_duplicate_rejection(self):
        parsed = ac.parse_yaml_subset("""# comment
items:
  - name: first
    value: 1
    labels: [a, 'b,c', \"d#e\"]
  - name: second
    nested:
      enabled: true
empty: {}
literal: abc#def  # actual comment
quoted: 'it''s # fine'  # comment outside quotes
""")
        self.assertEqual(parsed["items"][0]["labels"], ["a", "b,c", "d#e"])
        self.assertTrue(parsed["items"][1]["nested"]["enabled"])
        self.assertEqual(parsed["literal"], "abc#def")
        self.assertEqual(parsed["quoted"], "it's # fine")
        for bad in ("a: 1\na: 2", "a:\n\tb: 1", "a: [1,,2]", "a: |\n  text",
                    "- not-a-mapping-root", "a:\n  - x: 1\n   y: 2"):
            with self.subTest(bad=bad), self.assertRaises(ac.ConfigError):
                ac.parse_yaml_subset(bad)
    def test_validate_cli(self):
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "agents_config.py"), "validate", "--config", str(TEMPLATE)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"schema-version": 2', result.stdout)


if __name__ == "__main__":
    unittest.main()
