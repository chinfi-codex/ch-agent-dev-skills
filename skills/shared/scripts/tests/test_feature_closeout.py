#!/usr/bin/env python3
"""v2 feature candidate, business acceptance, authorization and merge tests."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import delivery
import feature_closeout as fc
import human_decision as hd
import state_store

SCRIPT = SCRIPTS / "feature_closeout.py"
SLUG = "demo"

CONFIG = """```yaml
schema-version: 2
tracker: local
main-branch: main
repo-level: C
quality-gate: {fast: 'python3 -I t.py'}
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
    zcode:
      supervisor-review: {model: provider/supervisor, effort: high}
      development:
        default: {model: provider/dev, effort: medium}
        escalated: {model: provider/dev, effort: high}
enforcement: {review-trust: operator-attested}
```
"""


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def command_case():
    return {
        "title": "命令业务验收", "source": "PRD#AC-01", "required": True,
        "executor": "command", "oracle": "auto", "steps": ["执行验收命令"],
        "expected": "退出码为 0", "command": "python3 -I t.py",
        "assertions": {"exit": 0}, "evidence": ["log"], "human_reason": None,
    }


def browser_case():
    return {
        "title": "浏览器业务验收", "source": "PRD#AC-01", "required": True,
        "executor": "browser-agent", "oracle": "auto", "steps": ["打开页面"],
        "expected": "只显示匹配结果", "assertions": {"dom": "只有匹配项"},
        "evidence": ["screenshot"], "human_reason": None,
    }


def human_case():
    return {
        "title": "看业务结论", "source": "PRD#AC-02", "required": True,
        "executor": "human", "oracle": "human", "steps": ["打开报告"],
        "expected": "领域结论合理", "human_reason": "domain-judgment",
    }


def plan_text(cases):
    lines = ["schema: agent-dev.feature-acceptance/v1", "prd: prd.md", "tech_spec: demo-tech-spec-2026-11-14.md", "cases:"]
    for aid, case in cases.items():
        lines += [f"  {aid}:", f"    title: {json.dumps(case['title'], ensure_ascii=False)}",
                  f"    source: {case['source']}", f"    required: {'true' if case['required'] else 'false'}",
                  f"    executor: {case['executor']}", f"    oracle: {case['oracle']}",
                  f"    steps: {json.dumps(case['steps'], ensure_ascii=False)}",
                  f"    expected: {json.dumps(case['expected'], ensure_ascii=False)}"]
        if case.get("command"):
            lines.append(f"    command: {json.dumps(case['command'])}")
        if "assertions" in case:
            lines.append("    assertions:")
            for key, value in case["assertions"].items():
                lines.append(f"      {key}: {json.dumps(value, ensure_ascii=False)}")
        if case.get("evidence"):
            lines.append(f"    evidence: {json.dumps(case['evidence'], ensure_ascii=False)}")
        reason = case.get("human_reason")
        lines.append("    human_reason:" if reason is None else f"    human_reason: {reason}")
    return "\n".join(lines) + "\n"


class FeatureCloseoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        sh(self.root, "init", "-q", "-b", "main")
        sh(self.root, "config", "user.email", "t@t")
        sh(self.root, "config", "user.name", "t")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x = 1\n")
        (self.root / ".gitignore").write_text("dev/\n.worktrees/\n")
        sh(self.root, "add", ".")
        sh(self.root, "commit", "-qm", "init")
        self.wt = self.root / ".worktrees" / "integration-demo"
        sh(self.root, "worktree", "add", "-q", str(self.wt), "-b", "feat/demo", "main")
        (self.wt / "src" / "a.py").write_text("x = 2\n")
        (self.wt / "t.py").write_text("from pathlib import Path\nassert 'x = 2' in Path('src/a.py').read_text()\n")
        sh(self.wt, "add", ".")
        sh(self.wt, "commit", "-qm", "issue-001")
        self.merged_commit = sh(self.wt, "rev-parse", "HEAD")
        (self.root / "dev").mkdir()
        (self.root / "dev" / "agents-config.md").write_text(CONFIG)
        self.fdir = self.root / "dev" / "features" / "mod" / SLUG
        (self.fdir / "issues").mkdir(parents=True)
        (self.fdir / "impl" / "issue-001").mkdir(parents=True)
        (self.fdir / "demo-tech-spec-2026-11-14.md").write_text("---\nstatus: 已确认（待拆解）\n---\n# Spec\n")
        self.write_issue("done")
        self.write_conformance("conform")
        self.write_merged(self.merged_commit)
        self.cases = {"AC-1": command_case(), "AC-2": human_case()}
        self.write_plan()

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, status):
        (self.fdir / "issues" / "issue-001-x.md").write_text(
            f"---\nissue-id: issue-001\nfeature-slug: {SLUG}\nstatus: {status}\ntech-spec: demo-tech-spec-2026-11-14.md\n---\n# x\n")

    def write_conformance(self, verdict):
        tree = sh(self.wt, "rev-parse", "HEAD^{tree}")
        (self.fdir / "impl" / "issue-001" / "conformance-issue-001.md").write_text(
            "```yaml\nissue: issue-001\ncontext_digest: %s\ncommit: %s\ntree: %s\nverdict: %s\n"
            "checker: main-session\nchecker_session: main-1\n"
            "selection: {algorithm: sha256-v1, rate: 1, selected: true}\ndate: 2026-11-15\n```\n" %
            ("a" * 64, self.merged_commit, tree, verdict))

    def generation_artifact(self, kind="raw"):
        generation = "manual-" + kind
        directory = self.fdir / "integration" / "raw" / generation
        directory.mkdir(parents=True, exist_ok=True)
        name = "screen.png" if kind == "screenshot" else "raw.log"
        content = b"png-evidence" if kind == "screenshot" else b"command-raw\n"
        path = directory / name
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        manifest = {
            "schema": "agent-dev.artifact-generation/v2", "subject": "mod/demo:AC-1:" + kind,
            "generation": generation, "candidate_tree": self.tree, "status": "current",
            "created_at": "2026-11-15T00:00:00Z",
            "artifacts": [{"path": name, "kind": kind, "sha256": digest, "size": len(content)}],
        }
        (directory / "artifact-generation-manifest.json").write_text(json.dumps(manifest))
        return {"type": kind, "path": path.relative_to(self.fdir).as_posix(), "sha256": digest,
                "candidate": self.head, "candidate_tree": self.tree, "generation": generation}

    def write_merged(self, commit):
        event_id = "merge-" + hashlib.sha256(("issue-001:" + commit).encode()).hexdigest()[:24]
        (self.fdir / "progress.jsonl").write_text(json.dumps({
            "event_id": event_id, "operation_id": event_id,
            "event": "merged", "issue": "issue-001", "commit": commit}) + "\n")

    def write_plan(self):
        (self.fdir / "acceptance-plan.yaml").write_text(plan_text(self.cases))

    def run_cli(self, *args, script=SCRIPT):
        return subprocess.run([sys.executable, "-I", str(script), *args], capture_output=True, text=True)

    def freeze(self):
        return self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt))

    def report(self):
        return self.run_cli("report", "--feature-dir", str(self.fdir), "--repo", str(self.wt))

    def check(self, phase, *extra, generate_report=True):
        if generate_report:
            generated = self.report()
            if generated.returncode:
                return generated
        return self.run_cli("check", "--phase", phase, "--feature-dir", str(self.fdir),
                            "--repo", str(self.wt), *extra)

    @property
    def candidate(self):
        return json.loads((self.fdir / "integration" / "candidate.json").read_text())

    @property
    def tree(self):
        return self.candidate["candidate_tree"]

    @property
    def head(self):
        return self.candidate["candidate"]

    def auto_result(self, aid="AC-1"):
        return {
            "id": aid, "oracle": "auto", "executor": "command", "status": "pass",
            "execution": {"executor": "command", "command": "python3 -I t.py", "exit": 0,
                          "started": True, "generator": "verify_issue",
                          "candidate": self.head, "candidate_tree": self.tree},
            "assertions": [{"type": "exit", "expected": 0, "actual": 0, "status": "pass"}],
            "artifacts": [self.generation_artifact("raw")],
        }

    def browser_result(self):
        artifact = self.generation_artifact("screenshot")
        return {
            "id": "AC-1", "oracle": "auto", "executor": "browser-agent", "status": "pass",
            "execution": {"executor": "browser-agent", "url": "http://127.0.0.1:8000/reports",
                          "actions": ["打开页面"], "console_errors": [], "page_errors": [],
                          "candidate": self.head, "candidate_tree": self.tree},
            "assertions": [{"type": "dom", "expected": "只有匹配项", "actual": "只有匹配项", "status": "pass"}],
            "artifacts": [artifact],
        }

    def human_result(self, confirmed=False):
        result = {"id": "AC-2", "oracle": "human", "executor": "human"}
        if confirmed:
            result["human"] = {"status": "confirmed", "by": "user"}
        return result

    def write_evidence(self, items, all_passed=True):
        integ = self.fdir / "integration"
        (integ / "evidence.md").write_text(
            f"```yaml\ncandidate: {self.head}\ncandidate_tree: {self.tree}\nall-passed: {'true' if all_passed else 'false'}\n"
            "ran:\n  direct: 3\n  indirect: 5\n  deferred: 0\n  quarantined: 0\n```\n")
        (integ / "conformance-feature.md").write_text(
            f"```yaml\nverdict: conform\ncandidate: {self.head}\ncandidate_tree: {self.tree}\n"
            "checker: main-session\nchecker_session: main-feature\ndate: 2026-11-15\n```\n")
        (integ / "business-results.json").write_text(json.dumps({
            "schema": "agent-dev.feature-business-results/v2", "candidate": self.head,
            "candidate_tree": self.tree, "plan_sha256": self.candidate["acceptance_plan"]["sha256"],
            "environment": {"name": "integration-test"}, "items": items}))

    def confirm(self, **overrides):
        self.report()
        integ = self.fdir / "integration"
        values = {
            "scope": "merge-main", "conditions": [], "candidate": self.head,
            "candidate_tree": self.tree, "main_head": self.candidate["main_head"],
            "confirmed-by": "user", "confirmed-at": "2026-11-12 10:00", "quote": "确认合入",
            "report_sha256": hashlib.sha256((integ / "acceptance-report.md").read_bytes()).hexdigest(),
            "business_results_sha256": hashlib.sha256((integ / "business-results.json").read_bytes()).hexdigest(),
        }
        values.update(overrides)
        lines = []
        for key, value in values.items():
            if isinstance(value, list):
                encoded = json.dumps(value, ensure_ascii=False)
            else:
                encoded = json.dumps(value, ensure_ascii=False)
            lines.append(f"{key}: {encoded}")
        (integ / "confirmation.md").write_text("```yaml\n" + "\n".join(lines) + "\n```\n")

    def ready_local(self):
        self.assertEqual(self.freeze().returncode, 0)
        self.write_evidence([self.auto_result(), self.human_result(True)])
        self.confirm()

    def promote(self, strategy="no-ff"):
        return delivery.promote(str(self.fdir / "integration" / "candidate.json"), self.wt,
                                self.root, strategy=strategy)

    def create_active_decision(self, ident="active-decision"):
        # Active decisions originate before the ticket reaches done.  Create in
        # that valid lifecycle state, then restore the freeze precondition.
        self.write_issue("in-progress")
        issue = self.fdir / "issues" / "issue-001-x.md"
        plan = self.fdir / "acceptance-plan.yaml"
        hd.request(self.root, {
            "schema": hd.SCHEMA, "id": ident,
            "scope": {"repo": ".", "feature": "mod/demo", "issue": "issue-001"},
            "source": {"host": "zcode", "skill": "implement", "role": "development",
                       "dispatch_id": "dispatch-1", "session_id": "session-1"},
            "basis": {"issue_sha256": hashlib.sha256(issue.read_bytes()).hexdigest(),
                      "head_commit": self.merged_commit,
                      "report_sha256": hashlib.sha256(plan.read_bytes()).hexdigest()},
            "question": {
                "context": "候选仍有需要人裁决的范围问题。", "decision": "继续还是暂缓？",
                "why": "两条路径的验收范围不同。", "recommendation": "hold",
                "options": [
                    {"id": "hold", "label": "暂缓", "description": "保留候选并等待裁决。",
                     "route": {"action": "defer", "strategy": "none", "role": None, "tier": None,
                               "next_status": "deferred", "prompt": None, "operation": None}},
                    {"id": "continue", "label": "继续", "description": "按原候选继续核对。",
                     "route": {"action": "continue", "strategy": "resume", "role": "development",
                               "tier": "default", "next_status": "in-progress", "prompt": "继续核对。",
                               "operation": None}},
                ],
            },
            "evidence": [{"path": "dev/features/mod/demo/acceptance-plan.yaml",
                          "sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
                          "summary": "验收计划与候选范围。"}],
            "presentation": {"header": "候选裁决"}, "expires_at": None,
        }, main_session=True, now="2026-11-15T00:00:00Z")
        self.write_issue("done")

    # ---- common candidate freeze ----
    def test_freeze_green_binds_full_merged_ancestor_and_plan(self):
        result = self.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.candidate["mode"], "local-human")
        self.assertEqual(self.candidate["issues"][0]["merged_commit"], self.merged_commit)
        self.assertEqual(self.candidate["candidate"], self.merged_commit)
        self.assertEqual(self.candidate["acceptance_plan"]["cases"], ["AC-1", "AC-2"])

    def test_freeze_requires_feature_wide_architecture_completion(self):
        spec = self.fdir / "demo-tech-spec-2026-11-14.md"
        spec.write_text("""---
status: 已确认（待拆解）
---
# Spec

### 3.2 接口变更

| 稳定 ID | 动作 | 名称 | 模块 | 源代码位置 | supersedes | 验证证据 |
| --- | --- | --- | --- | --- | --- | --- |
| demo.entry | new | entry | mod | src/a.py:run | — | t.py |
""", encoding="utf-8")
        issue = self.fdir / "issues" / "issue-001-x.md"
        issue.write_text(issue.read_text().replace("tech-spec:", "architecture-updates: [demo.entry]\ntech-spec:"), encoding="utf-8")
        result = self.freeze()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("architecture-feature", result.stdout)
        self.assertIn("demo.entry", result.stdout)

    def test_freeze_requires_done_full_ancestor_and_conform(self):
        self.write_issue("in-review")
        self.assertEqual(self.freeze().returncode, 1)
        self.write_issue("done")
        self.write_merged("abc")
        result = self.freeze()
        self.assertEqual(result.returncode, 1)
        self.assertIn("完整 40 位", result.stdout)
        self.write_merged(self.merged_commit)
        self.write_conformance("skipped")
        result = self.freeze()
        self.assertEqual(result.returncode, 1)
        self.assertIn("每票均须 conform", result.stdout)

    def test_freeze_rejects_non_ancestor_and_active_decision(self):
        sh(self.root, "checkout", "-qb", "other")
        (self.root / "other.py").write_text("x\n")
        sh(self.root, "add", "other.py")
        sh(self.root, "commit", "-qm", "other")
        other = sh(self.root, "rev-parse", "HEAD")
        sh(self.root, "checkout", "-q", "main")
        self.write_merged(other)
        self.assertEqual(self.freeze().returncode, 1)
        self.write_merged(self.merged_commit)
        self.create_active_decision("pending-decision")
        result = self.freeze()
        self.assertEqual(result.returncode, 1)
        self.assertIn("活动人工决策", result.stdout)

    def test_issues_scope_requires_nonempty_exact_set(self):
        for value in ("", "issue-999", "issue-001,issue-001"):
            with self.subTest(value=value):
                result = self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                                      "--issues", value)
                self.assertEqual(result.returncode, 1)
        (self.fdir / "issues" / "issue-002-y.md").write_text(
            "---\nissue-id: issue-002\nstatus: in-progress\n---\n")
        result = self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                              "--issues", "issue-001")
        self.assertEqual(result.returncode, 1)
        self.assertIn("遗漏", result.stdout)
        self.write_issue("deferred")
        (self.fdir / "issues" / "issue-002-y.md").unlink()
        result = self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                              "--issues", "issue-001")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any(text in result.stdout for text in ("全过滤", "未知", "deferred")), result.stdout)

    def test_checker_projection_contains_only_required_facts(self):
        self.assertEqual(self.freeze().returncode, 0)
        projection = self.candidate["issues"][0]["conformance_context"]
        self.assertEqual(set(projection), set(fc.CHECKER_FIELDS))
        self.assertEqual(projection["checker"], "main-session")
        self.assertEqual(set(projection["selection"]), {"algorithm", "rate", "selected"})

    def test_invalid_legacy_dates_are_not_compared_as_strings(self):
        config = self.root / "dev" / "agents-config.md"
        config.write_text(CONFIG.replace("feature-closeout: {mode: local-human}",
                                         "feature-closeout: {mode: local-human, since: '2026-11-09'}"))
        issue = self.fdir / "issues" / "issue-001-x.md"
        for created in ("2026-11-31", "", "11/01/2026"):
            with self.subTest(created=created):
                issue.write_text("---\nissue-id: issue-001\nfeature-slug: demo\ncreated: '%s'\nstatus: done\n---\n" % created)
                result = self.run_cli("target", "--issue", str(issue))
                self.assertEqual(result.returncode, 2)

    def test_freeze_requires_main_synced_and_rejects_drift_ignore_config(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "n.md").write_text("n\n")
        sh(self.root, "add", "docs")
        sh(self.root, "commit", "-qm", "main moves")
        result = self.freeze()
        self.assertEqual(result.returncode, 1)
        self.assertIn("尚未同步", result.stdout)
        config = self.root / "dev" / "agents-config.md"
        config.write_text(CONFIG.replace("feature-closeout: {mode: local-human}",
                                         "feature-closeout: {mode: local-human, main-drift-ignore: [docs/**]}"))
        result = self.run_cli("validate-plan", "--feature-dir", str(self.fdir))
        # validate-plan itself is config-independent; freeze/target still fail strict config.
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.freeze().returncode, 2)

    def test_cooperative_ci_uses_same_candidate(self):
        config = self.root / "dev" / "agents-config.md"
        config.write_text(CONFIG.replace(
            "feature-closeout: {mode: local-human}",
            "feature-closeout:\n  mode: cooperative-ci\n  ci: {provider: gitea, base-url: 'https://gitea.example', repository: o/r, workflow: full, artifact: verdict.json, token-env: GITEA_TOKEN}"))
        result = self.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.candidate["mode"], "cooperative-ci")
        self.write_evidence([self.auto_result(), self.human_result(True)])
        result = self.check("merge")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ---- evidence and report binding ----
    def test_verify_green_and_stale_commit_or_tree_fail(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result()])
        self.assertEqual(self.check("verify", "--record").returncode, 0)
        (self.wt / "src" / "a.py").write_text("x = 3\n")
        sh(self.wt, "commit", "-qam", "late")
        self.assertEqual(self.check("verify", generate_report=False).returncode, 1)

    def test_command_requires_verify_issue_and_candidate_bound_artifacts(self):
        self.freeze()
        auto = self.auto_result()
        auto["execution"]["generator"] = "handwritten"
        self.write_evidence([auto, self.human_result()])
        result = self.check("verify")
        self.assertEqual(result.returncode, 1)
        self.assertIn("verify_issue", result.stdout)
        self.cases = {"AC-1": browser_case()}
        self.write_plan()
        self.freeze()
        browser = self.browser_result()
        browser["artifacts"][0]["candidate"] = "0" * 40
        self.write_evidence([browser])
        result = self.check("verify")
        self.assertEqual(result.returncode, 1)
        self.assertIn("artifact generation candidate", result.stdout)

    def test_run_ac_executes_frozen_command_and_generates_hash_bound_raw(self):
        self.freeze()
        result = self.run_cli("run-ac", "--feature-dir", str(self.fdir), "--repo", str(self.wt), "--ac", "AC-1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        results = json.loads((self.fdir / "integration" / "business-results.json").read_text())
        item = results["items"][0]
        self.assertEqual(item["status"], "pass")
        self.assertEqual(item["execution"]["generator"], "verify_issue")
        artifact = item["artifacts"][0]
        self.assertEqual(artifact["candidate"], self.head)
        self.assertTrue((self.fdir / artifact["path"]).is_file())

    def test_run_ac_supersedes_previous_generation_with_self_contained_manifest(self):
        self.freeze()
        self.assertEqual(self.run_cli("run-ac", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                                      "--ac", "AC-1").returncode, 0)
        first = json.loads((self.fdir / "integration" / "business-results.json").read_text())["items"][0]
        first_generation = first["execution"]["generation"]
        first_path = self.fdir / "integration" / "raw" / first_generation / "artifact-generation-manifest.json"
        self.assertEqual(self.run_cli("run-ac", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                                      "--ac", "AC-1").returncode, 0)
        second = json.loads((self.fdir / "integration" / "business-results.json").read_text())["items"][0]
        second_generation = second["execution"]["generation"]
        second_path = self.fdir / "integration" / "raw" / second_generation / "artifact-generation-manifest.json"
        old = json.loads(first_path.read_text())
        new = json.loads(second_path.read_text())
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(new["status"], "current")
        self.assertEqual(new["supersedes"], first_generation)
        self.assertEqual({a["path"] for a in new["artifacts"]}, {"result.json", second["artifacts"][0]["path"].split("/", 3)[-1]})
        facts = fc.manifest_facts(self.fdir / "integration")
        self.assertEqual(len([fact for fact in facts if fact["status"] == "current"]), 1)

    def test_report_is_hash_stable_when_confirmation_is_added(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result(True)])
        self.report()
        before = (self.fdir / "integration" / "acceptance-report.md").read_bytes()
        self.confirm()
        self.report()
        after = (self.fdir / "integration" / "acceptance-report.md").read_bytes()
        self.assertEqual(before, after)

    # ---- local-human merge authorization ----
    def test_merge_requires_scope_empty_conditions_and_matching_hashes(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result(True)])
        self.confirm(scope="deploy-prod", conditions=["先观察"])
        result = self.check("merge")
        self.assertEqual(result.returncode, 1)
        self.assertIn("scope", result.stdout)
        self.assertIn("conditions", result.stdout)
        self.confirm(report_sha256="0" * 64)
        result = self.check("merge")
        self.assertEqual(result.returncode, 1)
        self.assertIn("report_sha256", result.stdout)

    def test_any_main_movement_invalidates_even_docs_only(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result(True)])
        self.confirm()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "n.md").write_text("n\n")
        sh(self.root, "add", "docs")
        sh(self.root, "commit", "-qm", "docs drift")
        result = self.check("merge", generate_report=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("任何路径变化", result.stdout)

    def test_local_human_explicit_main_target_is_rejected(self):
        issue = self.fdir / "issues" / "issue-001-x.md"
        issue.write_text("---\nissue-id: issue-001\nfeature-slug: demo\ntarget-branch: main\nstatus: done\n---\n")
        result = self.run_cli("target", "--issue", str(issue))
        self.assertEqual(result.returncode, 2)
        self.assertIn("显式", result.stderr)

    def test_merge_rejects_active_decision(self):
        self.ready_local()
        self.create_active_decision("routing-decision")
        result = self.check("merge")
        self.assertEqual(result.returncode, 1)
        self.assertIn("活动人工决策", result.stdout)

    # ---- in-process record_merge through delivery.promote ----
    def test_no_ff_promote_and_same_operation_record_replay(self):
        self.ready_local()
        captured = {}
        real_authorize = fc.authorize_merge
        real_record = fc.record_merge
        def authorize(*args, **kwargs):
            captured["auth"] = real_authorize(*args, **kwargs)
            return captured["auth"]
        def record(auth, result):
            captured["result"] = dict(result)
            return real_record(auth, result)
        with mock.patch.object(delivery.fc if hasattr(delivery, "fc") else fc, "authorize_merge", authorize):
            # delivery lazy-imports the same module object.
            with mock.patch.object(fc, "record_merge", record):
                with mock.patch.object(fc, "authorize_merge", authorize):
                    result = self.promote()
        commit = result["merge_commit"]
        parents = sh(self.root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        self.assertEqual(parents, [self.candidate["main_head"], self.candidate["candidate"]])
        self.assertEqual(sh(self.root, "rev-parse", "HEAD^{tree}"), self.candidate["candidate_tree"])
        replay = real_record(captured["auth"], captured["result"])
        self.assertFalse(replay.failed, [i["msg"] for i in replay.items])
        events = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines() if '"feature-merged"' in line]
        self.assertEqual(len(events), 1)
        event = json.loads(events[0])
        self.assertEqual(event["host"], "zcode")
        self.assertEqual(event["branch"], "feat/demo")
        self.assertEqual(event["target"], "main")
        self.assertRegex(event["ts"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_squash_promote_and_extra_change_rejected(self):
        self.ready_local()
        result = self.promote("squash")
        commit = result["merge_commit"]
        parents = sh(self.root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        self.assertEqual(parents, [self.candidate["main_head"]])
        self.assertEqual(sh(self.root, "rev-parse", "HEAD^{tree}"), self.candidate["candidate_tree"])

        # Fresh fixture state is required after a successful squash.
        self.tearDown()
        self.setUp()
        self.ready_local()
        original = delivery._execute_feature_merge
        def extra_edit(main_repo, candidate, strategy):
            sh(main_repo, "merge", "--squash", candidate["candidate"])
            (main_repo / "unauthorized.py").write_text("BAD = True\n")
            sh(main_repo, "add", "unauthorized.py")
            sh(main_repo, "commit", "-qm", "squash plus unauthorized edit")
            return {"repo": str(main_repo), "commit": sh(main_repo, "rev-parse", "HEAD"), "strategy": "squash"}
        with mock.patch.object(delivery, "_execute_feature_merge", extra_edit):
            with self.assertRaisesRegex(delivery.DeliveryError, "tree|pending"):
                self.promote("squash")
        self.assertNotIn("merged_commit", self.candidate)

    def test_feature_target_mutation_before_update_ref_cannot_move_main(self):
        self.ready_local()
        original = delivery._cas_update_ref
        before = self.candidate["main_head"]
        def competing_update(repo, branch, new, frozen_old):
            tree = sh(repo, "rev-parse", frozen_old + "^{tree}")
            competing = delivery._commit_tree(repo, tree, [frozen_old], "feature competing mutation\n")
            original(repo, branch, competing, frozen_old)
            return original(repo, branch, new, frozen_old)
        with mock.patch.object(delivery, "_cas_update_ref", side_effect=competing_update):
            with self.assertRaisesRegex(delivery.DeliveryError, "target CAS failed"):
                self.promote()
        self.assertNotEqual(sh(self.root, "rev-parse", "HEAD"), before)
        self.assertNotIn("merged_commit", self.candidate)
        self.assertNotIn("feature-merged", (self.fdir / "progress.jsonl").read_text())

    def test_feature_reconcile_after_merge_before_record(self):
        self.ready_local()
        with mock.patch.object(fc, "record_merge", side_effect=delivery.DeliveryError("before record crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "before record crash"):
                self.promote()
        merge_commit = sh(self.root, "rev-parse", "HEAD")
        self.assertNotIn("merged_commit", self.candidate)
        result = delivery.reconcile(candidate_path=self.fdir / "integration" / "candidate.json",
                                    candidate_repo=self.wt, repo=self.root)
        self.assertEqual(result["merge_commit"], merge_commit)
        self.assertEqual(self.candidate["merged_commit"], merge_commit)
        events = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines() if '"feature-merged"' in line]
        self.assertEqual(len(events), 1)

    def test_feature_reconcile_after_candidate_write_before_event(self):
        self.ready_local()
        with mock.patch.object(fc, "_idempotent_merged_event", side_effect=fc.InputError("event write crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "candidate remains pending"):
                self.promote()
        merge_commit = sh(self.root, "rev-parse", "HEAD")
        self.assertEqual(self.candidate["merged_commit"], merge_commit)
        self.assertNotIn("feature-merged", (self.fdir / "progress.jsonl").read_text())
        result = delivery.reconcile(candidate_path=self.fdir / "integration" / "candidate.json",
                                    candidate_repo=self.wt, repo=self.root)
        self.assertEqual(result["merge_commit"], merge_commit)
        aggregate = delivery.reconcile_operations(
            self.root, apply=True, candidate_path=self.fdir / "integration" / "candidate.json",
            candidate_repo=self.wt, main_repo=self.root)
        again = aggregate["operations"][0]
        self.assertEqual(again["operation_id"], result["operation_id"])
        events = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines() if '"feature-merged"' in line]
        self.assertEqual(len(events), 1)
        self.assertEqual(sh(self.root, "rev-list", "--count", "main"), "3")

    def test_closeout_template_matches_checker_contract(self):
        text = (SCRIPTS.parent / "templates" / "feature-closeout.md").read_text(encoding="utf-8")
        for required in (
            "candidate: <candidate.json 的 candidate 完整 commit>",
            "main_head: <candidate.json 冻结时的 main 完整 commit>",
            "scope: merge-main", "report_sha256:", "business_results_sha256:",
            '"started": true', '"generator": "verify_issue"',
            '"candidate": "<candidate 完整 commit>"',
            '"candidate_tree": "<tree>"', '"generation": "<当前代际>"',
            "integration/raw/<generation>/",
        ):
            self.assertIn(required, text)
        self.assertNotIn("scope: [merge-main]", text)
        self.assertNotIn("integration/artifacts/<generation>/", text)

    def test_resume_rejects_refrozen_candidate_and_strategy_crossing(self):
        self.ready_local()
        result = self.promote("squash")
        with self.assertRaisesRegex(delivery.DeliveryError, "cross no-ff/squash"):
            delivery.reconcile(candidate_path=self.fdir / "integration" / "candidate.json",
                               candidate_repo=self.wt, repo=self.root, strategy="no-ff")
        replay = delivery.reconcile(candidate_path=self.fdir / "integration" / "candidate.json",
                                    candidate_repo=self.wt, repo=self.root)
        self.assertEqual(replay["strategy"], "squash")
        sh(self.wt, "merge", "--no-ff", "--no-edit", "main")
        (self.wt / "src" / "b.py").write_text("y = 2\n")
        sh(self.wt, "add", "src/b.py")
        sh(self.wt, "commit", "-qm", "refreeze B")
        self.assertEqual(self.freeze().returncode, 0)
        with self.assertRaisesRegex(delivery.DeliveryError, "full hash"):
            delivery.reconcile(candidate_path=self.fdir / "integration" / "candidate.json",
                               candidate_repo=self.wt, repo=self.root)
        self.assertEqual(self.candidate["candidate"], sh(self.wt, "rev-parse", "HEAD"))
        self.assertNotEqual(self.candidate.get("merged_commit"), result["merge_commit"])

    def test_authorize_observed_crash_revalidates_and_merges_once(self):
        self.ready_local()
        with mock.patch.object(delivery, "_authorization_payload", side_effect=delivery.DeliveryError("authorize observed crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "authorize observed crash"):
                self.promote()
        journal = state_store.OperationJournal.load(self.fdir / "integration" / "delivery-promote.json")
        self.assertEqual(journal.current_step, "authorize")
        original = delivery._execute_feature_merge
        calls = []
        def counted(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)
        with mock.patch.object(delivery, "_execute_feature_merge", side_effect=counted):
            result = self.promote()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.candidate["merged_commit"], result["merge_commit"])
        events = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines() if '"feature-merged"' in line]
        self.assertEqual(len(events), 1)

    def _crash_before_feature_merge(self):
        self.ready_local()
        with mock.patch.object(delivery, "_execute_feature_merge", side_effect=delivery.DeliveryError("before merge crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "before merge crash"):
                self.promote()
        journal = state_store.OperationJournal.load(self.fdir / "integration" / "delivery-promote.json")
        self.assertEqual(journal.steps[0]["status"], "done")
        self.assertEqual(journal.current_step, "merge")
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), self.candidate["main_head"])

    def test_withdrawn_confirmation_requires_reauthorization_before_merge(self):
        self._crash_before_feature_merge()
        (self.fdir / "integration" / "confirmation.md").unlink()
        with mock.patch.object(fc, "authorize_merge", wraps=fc.authorize_merge) as authorize:
            with self.assertRaisesRegex(delivery.DeliveryError, "authorization failed"):
                self.promote()
            authorize.assert_called_once()
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), self.candidate["main_head"])
        self.assertNotIn("merged_commit", self.candidate)

    def test_new_active_decision_blocks_premerge_recovery(self):
        self._crash_before_feature_merge()
        self.create_active_decision("new-premerge-decision")
        with mock.patch.object(fc, "authorize_merge", wraps=fc.authorize_merge) as authorize:
            with self.assertRaisesRegex(delivery.DeliveryError, "authorization failed"):
                self.promote()
            authorize.assert_called_once()
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), self.candidate["main_head"])
        self.assertNotIn("merged_commit", self.candidate)

    def test_completed_merge_recovery_does_not_reauthorize(self):
        self.ready_local()
        with mock.patch.object(fc, "record_merge", side_effect=delivery.DeliveryError("after merge crash")):
            with self.assertRaisesRegex(delivery.DeliveryError, "after merge crash"):
                self.promote()
        merge_commit = sh(self.root, "rev-parse", "HEAD")
        journal = state_store.OperationJournal.load(self.fdir / "integration" / "delivery-promote.json")
        self.assertEqual(journal.steps[1]["status"], "done")
        with mock.patch.object(fc, "authorize_merge", side_effect=AssertionError("must not reauthorize")):
            result = delivery.reconcile(candidate_path=self.fdir / "integration" / "candidate.json",
                                        candidate_repo=self.wt, repo=self.root)
        self.assertEqual(result["merge_commit"], merge_commit)
        events = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines() if '"feature-merged"' in line]
        self.assertEqual(len(events), 1)

    def test_public_record_merge_cli_is_gone(self):
        result = self.run_cli("record-merge", "--feature-dir", str(self.fdir), "--repo", str(self.root), "--commit", "HEAD")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
