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
import review_receipt as rr
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
enforcement: {review-trust: operator-attested, require-host-capabilities: false}
```
"""


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def command_case():
    return {
        "title": "命令业务验收", "source": {"document": "prd", "anchor": "AC-1"}, "required": True,
        "executor": "command", "oracle": "auto", "steps": ["执行验收命令"],
        "preconditions": ["环境：隔离测试环境", "数据：输入记录 1 条"],
        "expected": "退出码为 0", "command": "python3 -I t.py",
        "assertions": {"exit": 0}, "evidence": ["log"], "human_reason": None,
    }


def browser_case():
    return {
        "title": "浏览器业务验收", "source": {"document": "prd", "anchor": "AC-1"}, "required": True,
        "executor": "browser-agent", "oracle": "auto", "steps": ["打开页面"],
        "preconditions": ["环境：浏览器测试实例", "数据：匹配记录 1 条"],
        "expected": "只显示匹配结果", "assertions": {"dom": "只有匹配项"},
        "evidence": ["screenshot"], "human_reason": None,
    }


def human_case():
    return {
        "title": "看业务结论", "source": {"document": "prd", "anchor": "AC-2"}, "required": True,
        "executor": "human", "oracle": "human", "steps": ["打开报告"],
        "preconditions": ["环境：候选报告只读访问", "数据：报告 1 份及已确认基准 1 份"],
        "expected": "领域结论合理", "human_reason": "domain-judgment",
        "evidence": ["human-review-record"], "human_expect": "关键事实与已确认基准一致，错误数 == 0 条",
    }


def plan_text(cases, documents):
    lines = ["schema: agent-dev.feature-acceptance/v2", "tech_spec: demo-tech-spec-2026-11-14.md", "documents:"]
    for alias, document in documents.items():
        lines += [f"  {alias}:", f"    path: {document['path']}", f"    sha256: {document['sha256']}"]
    lines.append("cases:")
    for aid, case in cases.items():
        lines += [f"  {aid}:", f"    title: {json.dumps(case['title'], ensure_ascii=False)}",
                  f"    source: {{document: {case['source']['document']}, anchor: {case['source']['anchor']}}}", f"    required: {'true' if case['required'] else 'false'}",
                  f"    executor: {case['executor']}", f"    oracle: {case['oracle']}",
                  f"    preconditions: {json.dumps(case.get('preconditions', []), ensure_ascii=False)}",
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
        if case.get("human_expect"):
            lines.append(f"    human_expect: {json.dumps(case['human_expect'], ensure_ascii=False)}")
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
        (self.root / ".gitignore").write_text("dev/\n.worktrees/\ndocs/\n")
        sh(self.root, "add", ".")
        sh(self.root, "commit", "-qm", "init")
        self.wt = self.root / ".worktrees" / "integration-demo"
        sh(self.root, "worktree", "add", "-q", str(self.wt), "-b", "feat/demo", "main")
        (self.wt / "src" / "a.py").write_text("x = 2\n")
        (self.wt / "t.py").write_text("from pathlib import Path\nassert 'x = 2' in Path('src/a.py').read_text()\n")
        (self.wt / "src" / "test_a.py").write_text("assert True\n")
        sh(self.wt, "add", ".")
        sh(self.wt, "commit", "-qm", "issue-001")
        self.merged_commit = sh(self.wt, "rev-parse", "HEAD")
        (self.root / "dev").mkdir()
        (self.root / "dev" / "agents-config.md").write_text(CONFIG)
        self.fdir = self.root / "dev" / "features" / "mod" / SLUG
        (self.fdir / "issues").mkdir(parents=True)
        (self.fdir / "impl" / "issue-001").mkdir(parents=True)
        (self.fdir / "impl" / "issue-001" / "verification-supervisor.json").write_text(
            json.dumps({"records": [{"id": "DoD-1"}]}), encoding="utf-8")
        (self.fdir / "demo-tech-spec-2026-11-14.md").write_text("---\nstatus: 已确认（待拆解）\n---\n# Spec\n")
        self.write_issue("done")
        self.write_conformance("conform")
        self.write_merged(self.merged_commit)
        self.cases = {"AC-1": command_case(), "AC-2": human_case()}
        self.docs = self.root / "docs" / "features" / "mod" / SLUG
        self.docs.mkdir(parents=True)
        self.prd = self.docs / "demo-prd-2026-11-14.md"
        self.prd.write_text("""# PRD

### 端到端验收场景
- AC-1：Given 测试数据，When 执行命令，Then 退出码为 0
- AC-2：Given 候选报告，When 人工核对，Then 关键事实错误数为 0
""", encoding="utf-8")
        self.write_plan()

    def tearDown(self):
        self.tmp.cleanup()

    def write_issue(self, status):
        (self.fdir / "issues" / "issue-001-x.md").write_text(
            f"---\nissue-id: issue-001\nfeature-slug: {SLUG}\nstatus: {status}\n"
            "dispatch:\n  host: zcode\n  model: provider/dev\n  session_id: impl-1\n  agent: development\n"
            "tech-spec: demo-tech-spec-2026-11-14.md\n---\n# x\n")

    def write_conformance(self, verdict, issue_id="issue-001"):
        tree = sh(self.wt, "rev-parse", "HEAD^{tree}")
        (self.fdir / "impl" / issue_id).mkdir(parents=True, exist_ok=True)
        (self.fdir / "impl" / issue_id / f"conformance-{issue_id}.md").write_text(
            f"```yaml\nissue: {issue_id}\ncontext_digest: %s\ncommit: %s\ntree: %s\nverdict: %s\n"
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

    def write_merged(self, commit, issue_id="issue-001", append=False):
        event_id = "merge-" + hashlib.sha256((issue_id + ":" + commit).encode()).hexdigest()[:24]
        mode = "a" if append else "w"
        with (self.fdir / "progress.jsonl").open(mode, encoding="utf-8") as stream:
            stream.write(json.dumps({
                "event_id": event_id, "operation_id": event_id,
                "event": "merged", "issue": issue_id, "commit": commit}) + "\n")

    def write_plan(self):
        documents = {"prd": {"path": self.prd.relative_to(self.root).as_posix(),
                              "sha256": hashlib.sha256(self.prd.read_bytes()).hexdigest()}}
        if getattr(self, "change_request", None):
            documents["change_request"] = {
                "path": self.change_request.relative_to(self.root).as_posix(),
                "sha256": hashlib.sha256(self.change_request.read_bytes()).hexdigest()}
        (self.fdir / "acceptance-plan.yaml").write_text(plan_text(self.cases, documents))

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
    def handoff(self):
        return json.loads((self.fdir / "integration" / "review-handoff.json").read_text())

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
        (integ / "conformance-feature.md").write_text(self.feature_conformance_text())
        (integ / "business-results.json").write_text(json.dumps({
            "schema": "agent-dev.feature-business-results/v2", "candidate": self.head,
            "candidate_tree": self.tree, "plan_sha256": self.candidate["acceptance_plan"]["sha256"],
            "environment": {"name": "integration-test"}, "items": items}))
        self.write_feature_gate()

    def write_feature_gate(self, findings=None, review_round=1, previous_receipt_sha256=None):
        integ = self.fdir / "integration"
        report = integ / "feature-review.md"
        report.write_text("# Feature review\n\nverdict: pass\n", encoding="utf-8")
        payload = dict(self.candidate)
        payload.update({
            "review_round": review_round,
            "previous_receipt_sha256": previous_receipt_sha256,
            "reviewer": {
                "host": "zcode", "agent": "supervisor-review", "session": "review-1",
                "model": "provider/supervisor", "effort": "high", "role": "review",
            },
            "findings": findings or [], "verdict": "pass", "trust": "operator-attested",
            "attestation": {
                "operator": "user", "statement": "独立评审已完成，未发现阻断问题。",
                "confirmed_at": "2026-11-16T00:00:00Z",
            },
        })
        input_path = integ / "feature-review-input.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        receipt, errors = rr.create_feature_receipt(
            self.fdir, self.wt, input_path, report=report,
            output=integ / "feature-review-receipt.json")
        if errors:
            raise AssertionError(errors)
        self.write_candidate_verification()

    def write_candidate_verification(self):
        integ = self.fdir / "integration"
        generation = "candidate-fixture"
        entries = []
        for item in self.candidate["issues"]:
            issue_id = item["id"]
            directory = integ / "raw" / generation / issue_id
            directory.mkdir(parents=True, exist_ok=True)
            evidence = directory / "verification-supervisor.json"
            evidence.write_text(json.dumps({
                "issue": issue_id, "head": self.head, "tree": self.tree, "pass": True,
                "records": [{"id": "DoD-1", "status": "pass"}],
            }), encoding="utf-8")
            entries.append({
                "issue": issue_id, "pass": True,
                "verification_path": evidence.relative_to(integ).as_posix(),
                "verification_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "head": self.head, "tree": self.tree,
            })
        (integ / "candidate-verification.json").write_text(json.dumps({
            "schema": "agent-dev.candidate-verification/v1",
            "candidate": self.head, "candidate_tree": self.tree,
            "main_head": self.candidate["main_head"],
            "plan_sha256": self.candidate["acceptance_plan"]["sha256"],
            "issues": entries, "all-passed": True, "generated": generation,
        }), encoding="utf-8")

    def add_second_leaf(self):
        (self.fdir / "issues" / "issue-002-y.md").write_text(
            f"---\nissue-id: issue-002\nfeature-slug: {SLUG}\nstatus: done\n"
            "dispatch:\n  host: zcode\n  model: provider/dev\n  session_id: impl-2\n  agent: development\n"
            "tech-spec: demo-tech-spec-2026-11-14.md\n---\n# y\n")
        (self.fdir / "impl" / "issue-002").mkdir(parents=True, exist_ok=True)
        (self.fdir / "impl" / "issue-002" / "verification-supervisor.json").write_text(
            json.dumps({"records": [{"id": "DoD-1"}]}), encoding="utf-8")
        self.write_conformance("conform", "issue-002")
        self.write_merged(self.merged_commit, "issue-002", append=True)

    def add_repair_issue(self, issue_id, findings, merged_commit, evidence=True):
        path = self.fdir / "issues" / (issue_id + "-repair.md")
        path.write_text(
            f"---\nissue-id: {issue_id}\nfeature-slug: {SLUG}\nstatus: done\n"
            f"review-findings: {json.dumps(findings)}\n"
            f"dispatch:\n  host: zcode\n  model: provider/dev\n  session_id: impl-{issue_id}\n"
            "  agent: development\ntech-spec: demo-tech-spec-2026-11-14.md\n---\n# repair\n",
            encoding="utf-8")
        impl = self.fdir / "impl" / issue_id
        impl.mkdir(parents=True, exist_ok=True)
        if evidence:
            for name in ("ticket-context.json", "process-check-ticket.json", "verification-implementer.json"):
                (impl / name).write_text(json.dumps({"issue": issue_id, "source": name}), encoding="utf-8")
            generation = impl / "raw" / ("repair-" + issue_id)
            generation.mkdir(parents=True)
            (generation / "artifact-generation-manifest.json").write_text(json.dumps({
                "schema": "agent-dev.artifact-generation/v2", "subject": issue_id,
                "generation": "repair-" + issue_id, "candidate_tree": self.tree,
                "status": "current", "created_at": "2026-11-16T00:00:00Z", "artifacts": [],
            }), encoding="utf-8")
        self.write_merged(merged_commit, issue_id=issue_id, append=True)
        return path

    def feature_conformance_text(self):
        spec = self.fdir / "demo-tech-spec-2026-11-14.md"
        spec_rel = spec.relative_to(self.root).as_posix()
        spec_sha = hashlib.sha256(spec.read_bytes()).hexdigest()
        issue_ids = sorted(item["id"] for item in self.candidate["issues"])
        rows = "\n".join(
            f"| {index} | 命令行为 | demo-tech-spec-2026-11-14.md#3 | {issue_id} | 已实现 | src/a.py:run + src/test_a.py + verification-supervisor.json#DoD-1 |"
            for index, issue_id in enumerate(issue_ids, 1))
        return f"""```yaml
issue: feature
verdict: conform
candidate: {self.head}
candidate_tree: {self.tree}
checker: main-session
checker_session: main-feature
date: 2026-11-15
coverage:
  issues: [{', '.join(issue_ids)}]
  specs:
    - {{path: {spec_rel}, sha256: {spec_sha}}}
```

## Feature 级覆盖核对

| 编号 | 需求点 | 来源 | 票 | 判定 | 证据 |
| --- | --- | --- | --- | --- | --- |
{rows}
"""

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

    def ready_local(self, status="done"):
        self.write_issue(status)
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
    def write_change_request(self, retain="[]", replace="{}", retire="[]"):
        self.change_request = self.docs / "demo-change-request-2026-11-15.md"
        self.change_request.write_text("""---
doc_type: change-request
upstream:
  prd: demo-prd-2026-11-14.md
baseline_ac:
  retain: %s
  replace: %s
  retire: %s
---
# Change Request

### Delta 验收场景
- AC-D1：Given 旧行为，When 执行变更，Then 新行为退出码为 0
""" % (retain, replace, retire), encoding="utf-8")

    def validate_plan(self):
        return self.run_cli("validate-plan", "--feature-dir", str(self.fdir))

    def test_standard_source_requires_full_prd_coverage(self):
        self.cases = {"AC-1": command_case()}
        self.write_plan()
        result = self.validate_plan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("AC 覆盖不完整", result.stdout)

    def test_delta_and_retained_baseline_reconcile(self):
        self.write_change_request(retain="[AC-2]")
        delta = command_case()
        delta["source"] = {"document": "change_request", "anchor": "AC-D1"}
        self.cases = {"AC-D1": delta, "AC-2": human_case()}
        self.write_plan()
        result = self.validate_plan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_replace_retire_and_document_drift_fail(self):
        self.write_change_request(replace="{AC-1: AC-D1}", retire="[AC-2]")
        delta = command_case()
        delta["source"] = {"document": "change_request", "anchor": "AC-D1"}
        self.cases = {"AC-D1": delta, "AC-2": human_case()}
        self.write_plan()
        result = self.validate_plan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("AC-2", result.stdout)
        self.write_change_request(retain="[AC-2]")
        self.cases = {"AC-D1": delta, "AC-2": human_case()}
        self.write_plan()
        self.prd.write_text(self.prd.read_text() + "\n需求文档已漂移\n", encoding="utf-8")
        result = self.validate_plan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("sha256", result.stdout)

    def test_baseline_ac_dispositions_must_be_explicit(self):
        self.write_change_request(retain="[AC-2]")
        self.change_request.write_text(self.change_request.read_text().replace("baseline_ac:", "baseline_ac_missing:"), encoding="utf-8")
        delta = command_case()
        delta["source"] = {"document": "change_request", "anchor": "AC-D1"}
        self.cases = {"AC-D1": delta, "AC-2": human_case()}
        self.write_plan()
        result = self.validate_plan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("全部显式声明", result.stdout)

    def test_required_case_structure_fields_fail(self):
        case = command_case()
        del case["preconditions"]
        self.cases = {"AC-1": case}
        self.prd.write_text("# PRD\n\n### 端到端验收场景\n- AC-1：Given 输入，When 执行，Then 退出码为 0\n", encoding="utf-8")
        self.write_plan()
        result = self.validate_plan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("preconditions", result.stdout)
        case = human_case()
        del case["human_expect"]
        del case["evidence"]
        self.cases = {"AC-2": case}
        self.prd.write_text("# PRD\n\n### 端到端验收场景\n- AC-2：Given 报告，When 人核，Then 无错误\n", encoding="utf-8")
        self.write_plan()
        result = self.validate_plan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("human_expect", result.stdout)
        self.assertIn("evidence", result.stdout)

    def test_first_freeze_handoff_binds_full_diff_and_inputs(self):
        result = self.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        handoff = self.handoff
        self.assertEqual(handoff["schema"], "agent-dev.review-handoff/v1")
        self.assertEqual(handoff["purpose"], "review-location-only")
        self.assertIsNone(handoff["previous"])
        self.assertEqual(handoff["candidate"], self.head)
        self.assertEqual(handoff["candidate_tree"], self.tree)
        self.assertEqual(handoff["review_base"], self.candidate["main_head"])
        self.assertEqual(handoff["diff"], {
            "base": self.candidate["main_head"], "candidate": self.head,
            "sha256": rr._feature_diff_sha(self.wt, self.candidate["main_head"], self.head),
            "files": rr._changed_paths(self.wt, self.candidate["main_head"], self.head),
        })
        self.assertEqual(handoff["diff"]["files"], ["src/a.py", "src/test_a.py", "t.py"])
        self.assertEqual(handoff["closure_rows"], [])
        self.assertEqual(handoff["repairs"], [])
        self.assertEqual(handoff["input_changes"], {"spec": "unknown", "config": "unknown", "plan": "unknown"})
        self.assertEqual(len(handoff["inputs"]["spec"]), 1)
        for artifact in handoff["inputs"]["spec"] + [handoff["inputs"]["config"], handoff["inputs"]["plan"]]:
            self.assertFalse(Path(artifact["path"]).is_absolute())
            self.assertNotIn("..", Path(artifact["path"]).parts)
            self.assertEqual(artifact["sha256"], hashlib.sha256((self.root / artifact["path"]).read_bytes()).hexdigest())

    def test_same_candidate_refreeze_is_byte_idempotent_and_not_self_previous(self):
        self.assertEqual(self.freeze().returncode, 0)
        candidate_path = self.fdir / "integration" / "candidate.json"
        handoff_path = self.fdir / "integration" / "review-handoff.json"
        first_candidate = json.loads(candidate_path.read_text())
        first = handoff_path.read_bytes()
        self.assertEqual(self.freeze().returncode, 0)
        self.assertEqual(handoff_path.read_bytes(), first)
        self.assertIsNone(self.handoff["previous"])
        self.assertNotEqual(json.loads(candidate_path.read_text())["frozen_at"], first_candidate["frozen_at"])
        before_failed_freeze = candidate_path.read_bytes()
        self.write_issue("in-progress")
        self.assertEqual(self.freeze().returncode, 1)
        self.assertEqual(handoff_path.read_bytes(), first)
        self.assertEqual(candidate_path.read_bytes(), before_failed_freeze)

    def test_same_candidate_refreeze_compares_immediate_input_hashes(self):
        self.assertEqual(self.freeze().returncode, 0)
        spec = self.fdir / "demo-tech-spec-2026-11-14.md"
        spec.write_text(spec.read_text() + "\n补充同候选的验收边界\n", encoding="utf-8")
        result = self.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.handoff["input_changes"]["spec"], "changed")
        self.assertEqual(self.handoff["input_changes"]["config"], "unchanged")
        self.assertEqual(self.handoff["input_changes"]["plan"], "unchanged")

    def test_refreeze_rejects_tampered_previous_report_bytes(self):
        self.assertEqual(self.freeze().returncode, 0)
        self.write_feature_gate(findings=[])
        integ = self.fdir / "integration"
        receipt = json.loads((integ / "feature-review-receipt.json").read_text())
        report = receipt["artifacts"]["report"]["path"]
        report_path = Path(report) if Path(report).is_absolute() else integ / report
        report_path.write_text(report_path.read_text() + "\n未授权改动\n", encoding="utf-8")
        before = (integ / "candidate.json").read_bytes()
        sh(self.wt, "commit", "-q", "--allow-empty", "-m", "new review candidate")
        result = self.freeze()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("实际 SHA256", result.stderr)
        self.assertEqual((integ / "candidate.json").read_bytes(), before)

    def test_refreeze_handoff_maps_findings_to_multiple_repair_tickets(self):
        self.assertEqual(self.freeze().returncode, 0)
        findings = [
            {"id": "F-1", "severity": "medium", "status": "open",
             "location": "src/a.py:1", "message": "边界行为需要修复", "closure": ""},
            {"id": "F-2", "severity": "low", "status": "closed",
             "location": "t.py:1", "message": "补充说明", "closure": "原评审已核对"},
        ]
        self.write_feature_gate(findings=findings)
        first_handoff = self.handoff
        old_candidate = self.candidate
        old_receipt_path = self.fdir / "integration" / "feature-review-receipt.json"
        old_receipt_sha = hashlib.sha256(old_receipt_path.read_bytes()).hexdigest()
        old_report = json.loads(old_receipt_path.read_text())["artifacts"]["report"]
        (self.wt / "src" / "a.py").write_text("x = 3\n")
        (self.wt / "src" / "repair.py").write_text("repair = True\n")
        sh(self.wt, "add", "src/a.py", "src/repair.py")
        sh(self.wt, "commit", "-qm", "review repairs")
        repair_commit = sh(self.wt, "rev-parse", "HEAD")
        self.add_repair_issue("issue-002", ["F-1"], repair_commit, evidence=True)
        self.add_repair_issue("issue-003", ["F-1"], repair_commit, evidence=False)
        self.add_repair_issue("issue-004", [], repair_commit, evidence=True)
        result = self.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("新候选全部重跑", result.stdout)
        handoff = self.handoff
        previous = handoff["previous"]
        self.assertEqual(previous["candidate"], old_candidate["candidate"])
        self.assertEqual(previous["candidate_tree"], old_candidate["candidate_tree"])
        self.assertEqual(previous["main_head"], old_candidate["main_head"])
        self.assertEqual(previous["plan_sha256"], old_candidate["acceptance_plan"]["sha256"])
        self.assertEqual(previous["inputs"], first_handoff["inputs"])
        self.assertEqual(previous["receipt"]["sha256"], old_receipt_sha)
        self.assertEqual(previous["receipt"]["round"], 1)
        self.assertIsNone(previous["receipt"]["previous_receipt_sha256"])
        self.assertEqual(previous["receipt"]["findings"], findings)
        self.assertEqual(previous["receipt"]["report"]["sha256"], old_report["sha256"])
        self.assertFalse(Path(previous["receipt"]["report"]["path"]).is_absolute())
        self.assertEqual(previous["incremental_diff"], {
            "base": old_candidate["candidate"], "candidate": self.head,
            "sha256": rr._feature_diff_sha(self.wt, old_candidate["candidate"], self.head),
            "files": ["src/a.py", "src/repair.py"],
        })
        self.assertEqual(previous["ticket_changes"]["added"], ["issue-002", "issue-003", "issue-004"])
        self.assertEqual(previous["ticket_changes"]["removed"], [])
        self.assertEqual([repair["issue"] for repair in handoff["repairs"]],
                         ["issue-002", "issue-003", "issue-004"])
        self.assertEqual([row["finding"]["id"] for row in handoff["closure_rows"]], ["F-1", "F-2"])
        first_row, second_row = handoff["closure_rows"]
        self.assertEqual(first_row["finding"], findings[0])
        self.assertEqual([repair["issue"] for repair in first_row["repairs"]], ["issue-002", "issue-003"])
        self.assertEqual(second_row["repairs"], [])
        self.assertNotIn("closed", first_row)
        full = first_row["repairs"][0]
        missing = first_row["repairs"][1]
        self.assertEqual(full["merged_commit"], repair_commit)
        for key in ("ticket_context", "process_check_ticket", "verification_implementer", "artifact_manifest"):
            self.assertEqual(full[key]["status"], "present")
            self.assertEqual(full[key]["sha256"], hashlib.sha256(
                (self.fdir / full[key]["path"]).read_bytes()).hexdigest())
            self.assertEqual(missing[key]["status"], "missing")
            self.assertIsNone(missing[key]["sha256"])
        unassigned = handoff["repairs"][2]
        self.assertEqual(unassigned["review_findings"], [])
        self.assertNotIn("issue-004", [repair["issue"] for repair in first_row["repairs"]])

        # Repeating the same new candidate preserves the earlier transition,
        # rather than replacing it with a candidate -> itself transition.
        before = (self.fdir / "integration" / "review-handoff.json").read_bytes()
        self.assertEqual(self.freeze().returncode, 0)
        self.assertEqual((self.fdir / "integration" / "review-handoff.json").read_bytes(), before)
        self.assertEqual(self.handoff["previous"]["candidate"], old_candidate["candidate"])

    def test_freeze_green_binds_full_merged_ancestor_and_plan(self):
        result = self.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.candidate["mode"], "local-human")
        self.assertEqual(self.candidate["issues"][0]["merged_commit"], self.merged_commit)
        self.assertEqual(self.candidate["candidate"], self.merged_commit)
        self.assertEqual(self.candidate["acceptance_plan"]["cases"], ["AC-1", "AC-2"])

    def test_freeze_rejects_ticket_slug_round_mismatch(self):
        # 复用旧 feature 目录的轮次：票 feature-slug 与 --round 不一致即 FAIL
        issue = self.fdir / "issues" / "issue-001-x.md"
        issue.write_text(issue.read_text(encoding="utf-8")
                         .replace("feature-slug: %s" % SLUG, "feature-slug: demo-2026-11-01"), encoding="utf-8")
        result = self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                              "--round", "demo-2026-11-18", "--issues", "issue-001")
        self.assertEqual(result.returncode, 1)
        self.assertIn("feature-slug=demo-2026-11-01", result.stdout)
        self.assertNotIn("已冻结", result.stdout)

    def test_freeze_round_accepts_matching_slug_and_exempt_promoted_old_tickets(self):
        # 已 promote 进 main 的旧轮票（旧 slug）不阻塞新 round；本轮票 slug 必须与 round 一致
        sh(self.root, "merge", "--no-ff", "-q", "-m", "promote round 1", "feat/demo")
        main_head = sh(self.root, "rev-parse", "main")
        wt2 = self.root / ".worktrees" / "integration-round2"
        sh(self.root, "worktree", "add", "-q", str(wt2), "-b", "feat/demo-2026-11-18", "main")
        issue = self.fdir / "issues" / "issue-001-x.md"
        self.write_issue("done")
        second = self.fdir / "issues" / "issue-002-y.md"
        second.write_text(
            "---\nissue-id: issue-002\nfeature-slug: demo-2026-11-18\nstatus: merged\n"
            "dispatch:\n  host: zcode\n  model: provider/dev\n  session_id: impl-2\n  agent: development\n"
            "tech-spec: demo-tech-spec-2026-11-14.md\n---\n# y\n", encoding="utf-8")
        self.write_merged(main_head, issue_id="issue-002", append=True)
        result = self.run_cli("freeze", "--feature-dir", str(self.fdir), "--repo", str(wt2),
                              "--round", "demo-2026-11-18", "--issues", "issue-001,issue-002")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        candidate = json.loads((self.fdir / "integration" / "demo-2026-11-18" / "candidate.json").read_text())
        self.assertEqual(candidate["integration_branch"], "feat/demo-2026-11-18")
        self.assertEqual(candidate["round"], "demo-2026-11-18")
        self.assertEqual([item["id"] for item in candidate["issues"]], ["issue-001", "issue-002"])

    def _write_arch_rules(self, rules):
        import arch_check as ac

        (self.wt / "docs").mkdir(parents=True, exist_ok=True)
        (self.wt / "docs/ARCH-CHECK.json").write_text(json.dumps(rules), encoding="utf-8")
        (self.wt / "docs/ARCHITECTURE.md").write_bytes(ac.render_guide(rules))
        sh(self.wt, "add", "-f", "docs/ARCH-CHECK.json", "docs/ARCHITECTURE.md")
        sh(self.wt, "commit", "-qm", "arch rules")
        return sh(self.wt, "rev-parse", "HEAD")

    def _base_arch_rules(self):
        import arch_check as ac

        return {
            "schema": ac.SCHEMA_NAME,
            "version": 1,
            "language": "python",
            "source_roots": ["."],
            "architecture": {
                "title": "Demo",
                "summary": "Layered.",
                "layers": [
                    {"name": "data", "description": "data", "may_depend_on": []},
                    {"name": "web", "description": "web", "may_depend_on": ["data"]},
                ],
                "placement": [],
            },
            "groups": [
                {"id": "model", "paths": ["src/model/**"], "layer": "data", "description": "models"},
                {"id": "controller", "paths": ["src/controller/**"], "layer": "web", "description": "handlers"},
            ],
            "rules": {
                "forbidden": [], "cycles": [], "banned_calls": [], "methods": [],
                "types": [], "comments": [], "duplication": [],
            },
        }

    def test_arch_check_gate_binds_rules_and_guide(self):
        head = self._write_arch_rules(self._base_arch_rules())
        report = fc.Report()
        meta = fc._check_arch_rules(report, self.wt, head)
        self.assertFalse(report.failed, report.items)
        self.assertEqual(meta["rules"], "docs/ARCH-CHECK.json")
        self.assertEqual(len(meta["rules_sha256"]), 64)
        self.assertEqual(meta["guide"], "docs/ARCHITECTURE.md")
        self.assertEqual(len(meta["guide_sha256"]), 64)

    def test_arch_check_gate_reports_forbidden_dependency(self):
        rules = self._base_arch_rules()
        rules["rules"]["forbidden"] = [{
            "id": "model-no-upward",
            "severity": "error",
            "from": ["model"],
            "to": ["controller"],
            "comment": "Step 1: find the import.\nStep 2: move the shared type down.",
        }]
        (self.wt / "src/model").mkdir(parents=True, exist_ok=True)
        (self.wt / "src/controller").mkdir(parents=True, exist_ok=True)
        (self.wt / "src/controller/y.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
        (self.wt / "src/model/x.py").write_text(
            "from src.controller.y import handler\n\n\ndef f():\n    return handler()\n", encoding="utf-8")
        head = self._write_arch_rules(rules)
        sh(self.wt, "add", "src/controller/y.py", "src/model/x.py")
        sh(self.wt, "commit", "-qm", "violation")
        head = sh(self.wt, "rev-parse", "HEAD")
        report = fc.Report()
        fc._check_arch_rules(report, self.wt, head)
        self.assertTrue(report.failed)
        self.assertTrue(any(item["check"] == "arch-check" and "model-no-upward" in item["msg"]
                            for item in report.items))

    def test_freeze_embeds_arch_check_metadata(self):
        self._write_arch_rules(self._base_arch_rules())
        result = self.freeze()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.candidate["arch_check"]["rules"], "docs/ARCH-CHECK.json")
        self.assertEqual(len(self.candidate["arch_check"]["rules_sha256"]), 64)
        self.assertEqual(self.candidate["arch_check"]["guide"], "docs/ARCHITECTURE.md")
        self.assertEqual(len(self.candidate["arch_check"]["guide_sha256"]), 64)

    def test_arch_check_gate_skips_without_rules(self):
        report = fc.Report()
        meta = fc._check_arch_rules(report, self.wt, self.merged_commit)
        self.assertFalse(report.failed, report.items)
        self.assertIsNone(meta)
        self.assertTrue(any(item["check"] == "arch-check" and item["level"] == "INFO"
                            for item in report.items))

    def test_freeze_accepts_merged_or_done_and_requires_full_ancestor(self):
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
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.candidate["issues"][0]["status"], "done")
        self.assertNotIn("conformance", self.candidate["issues"][0])

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

    def test_candidate_freezes_only_ticket_identity_and_merged_commit(self):
        self.assertEqual(self.freeze().returncode, 0)
        issue = self.candidate["issues"][0]
        self.assertEqual(set(issue), {"id", "status", "merged_commit"})
        self.assertEqual(issue["status"], "done")
        self.assertEqual(issue["merged_commit"], self.merged_commit)

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
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs" / "n.md").write_text("n\n")
        sh(self.root, "add", "-f", "docs")
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
        self.prd.write_text("# PRD\n\n### 端到端验收场景\n- AC-1：Given 测试页面，When 打开页面，Then 只显示匹配项\n", encoding="utf-8")
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

    def test_phase_timing_start_finish_replay_cli_and_drift_rejection(self):
        self.assertEqual(self.freeze().returncode, 0)
        with self.assertRaisesRegex(fc.InputError, "须已有"):
            fc.record_phase(self.fdir, self.wt, "review", "finish")
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:00Z"):
            start = fc.record_phase(self.fdir, self.wt, "review", "start")
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:30Z"):
            replay = fc.record_phase(self.fdir, self.wt, "review", "start")
        self.assertEqual(replay, start)
        self.assertEqual(start["event"], "digest")
        self.assertEqual(start["issue"], "feature")
        self.assertEqual(start["kind"], "feature-phase-timing")
        self.assertEqual(start["candidate"], self.head)
        self.assertEqual(start["candidate_tree"], self.tree)
        self.assertEqual(start["state"], "running")
        self.assertEqual(start["duration_seconds"], 0)
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:05.250000Z"):
            finish = fc.record_phase(self.fdir, self.wt, "review", "finish")
        self.assertEqual(finish["started_at"], start["started_at"])
        self.assertEqual(finish["ended_at"], "2026-11-16T10:00:05.250000Z")
        self.assertEqual(finish["duration_seconds"], 5.25)
        self.assertEqual(finish["state"], "finished")
        self.assertNotEqual(finish["event_id"], start["event_id"])
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:01:00Z"):
            self.assertEqual(fc.record_phase(self.fdir, self.wt, "review", "finish"), finish)
        events = [json.loads(line) for line in (self.fdir / "progress.jsonl").read_text().splitlines()]
        self.assertEqual(len([event for event in events if event.get("kind") == "feature-phase-timing"]), 2)
        self.assertEqual(len([event for event in events if event.get("event") == "merged"]), 1)

        output = self.fdir / "integration" / "phase-event.json"
        cli = self.run_cli("phase", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                           "--phase", "conformance", "--event", "start", "--json", str(output))
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["event_id"], json.loads(output.read_text())["event_id"])
        cli_replay = self.run_cli("phase", "--feature-dir", str(self.fdir), "--repo", str(self.wt),
                                  "--phase", "conformance", "--event", "start")
        self.assertEqual(cli_replay.returncode, 0, cli_replay.stdout + cli_replay.stderr)
        self.assertEqual(json.loads(cli_replay.stdout)["started_at"], json.loads(cli.stdout)["started_at"])

        (self.wt / "src" / "drift.py").write_text("drift = True\n")
        sh(self.wt, "add", "src/drift.py")
        sh(self.wt, "commit", "-qm", "candidate drift")
        with self.assertRaisesRegex(fc.InputError, "漂移"):
            fc.record_phase(self.fdir, self.wt, "review", "start")
        with self.assertRaisesRegex(fc.InputError, "漂移"):
            fc.record_phase(self.fdir, self.wt, "acceptance", "start")
        self.assertEqual(len([line for line in (self.fdir / "progress.jsonl").read_text().splitlines()
                              if '"feature-phase-timing"' in line]), 3)

    def test_phase_timing_restarts_when_candidate_plan_bytes_change(self):
        self.assertEqual(self.freeze().returncode, 0)
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:00Z"):
            old_start = fc.record_phase(self.fdir, self.wt, "review", "start")
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:05Z"):
            fc.record_phase(self.fdir, self.wt, "review", "finish")
        third = command_case()
        third["source"]["anchor"] = "AC-3"
        self.cases["AC-3"] = third
        self.prd.write_text(self.prd.read_text() +
                            "- AC-3：Given 新计划，When 重冻结，Then 阶段重新计时\n",
                            encoding="utf-8")
        self.write_plan()
        self.assertEqual(self.freeze().returncode, 0)
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:01:00Z"):
            new_start = fc.record_phase(self.fdir, self.wt, "review", "start")
        self.assertNotEqual(new_start["event_id"], old_start["event_id"])
        self.assertNotEqual(new_start["candidate_sha256"], old_start["candidate_sha256"])
        rows = fc.phase_timing_summary(self.fdir, self.candidate)
        self.assertEqual([(row["phase"], row["state"], row["started_at"]) for row in rows],
                         [("review", "running", "2026-11-16T10:01:00Z")])

    def test_report_summarizes_only_real_current_candidate_phase_times(self):
        self.assertEqual(self.freeze().returncode, 0)
        self.write_evidence([self.auto_result(), self.human_result(True)])
        self.assertEqual(self.report().returncode, 0)
        report_path = self.fdir / "integration" / "acceptance-report.md"
        self.assertNotIn("## 阶段耗时", report_path.read_text())
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:00Z"):
            fc.record_phase(self.fdir, self.wt, "review", "start")
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:05Z"):
            fc.record_phase(self.fdir, self.wt, "review", "finish")
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:06Z"):
            fc.record_phase(self.fdir, self.wt, "acceptance", "start")
        rows = fc.phase_timing_summary(self.fdir, self.candidate)
        self.assertEqual([(row["phase"], row["state"], row["duration_seconds"]) for row in rows],
                         [("review", "finished", 5.0), ("acceptance", "running", 0.0)])
        self.assertEqual(self.report().returncode, 0)
        first = report_path.read_bytes()
        text = first.decode()
        self.assertIn("## 阶段耗时", text)
        self.assertIn("| review | 已完成 | 2026-11-16T10:00:00Z | 2026-11-16T10:00:05Z | 5 |", text)
        self.assertIn("| acceptance | 进行中（截至开始） | 2026-11-16T10:00:06Z | — | 0 |", text)
        self.assertNotIn("| candidate-verification |", text)
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(report_path.read_bytes(), first)

    def test_report_is_hash_stable_when_confirmation_is_added(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result(True)])
        self.report()
        before = (self.fdir / "integration" / "acceptance-report.md").read_bytes()
        self.confirm()
        self.report()
        after = (self.fdir / "integration" / "acceptance-report.md").read_bytes()
        self.assertEqual(before, after)

    def test_final_timed_report_is_confirmed_after_timing_and_merge_stable(self):
        self.write_issue("done")
        self.assertEqual(self.freeze().returncode, 0)
        self.write_evidence([self.auto_result(), self.human_result(True)])
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:00Z"):
            fc.record_phase(self.fdir, self.wt, "acceptance", "start")
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:01Z"):
            fc.record_phase(self.fdir, self.wt, "report", "start")
        self.assertEqual(self.report().returncode, 0)
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:02Z"):
            fc.record_phase(self.fdir, self.wt, "report", "finish")
        with mock.patch.object(fc, "_phase_now", return_value="2026-11-16T10:00:03Z"):
            fc.record_phase(self.fdir, self.wt, "acceptance", "finish")
        self.assertEqual(self.report().returncode, 0)
        report_path = self.fdir / "integration" / "acceptance-report.md"
        final = report_path.read_bytes()
        self.assertIn("| report | 已完成 |", final.decode())
        self.assertIn("| acceptance | 已完成 |", final.decode())
        self.confirm()
        self.assertEqual(report_path.read_bytes(), final)
        self.assertEqual(self.report().returncode, 0)
        self.assertEqual(report_path.read_bytes(), final)
        result = self.check("merge")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report_path.read_bytes(), final)

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
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs" / "n.md").write_text("n\n")
        sh(self.root, "add", "-f", "docs")
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
    def test_multi_leaf_candidate_uses_one_scoped_coverage_record(self):
        self.add_second_leaf()
        self.ready_local()
        self.assertEqual(["issue-001", "issue-002"], sorted(item["id"] for item in self.candidate["issues"]))
        text = (self.fdir / "integration" / "conformance-feature.md").read_text()
        self.assertEqual(2, text.count("verification-supervisor.json#DoD-1"))
        self.assertEqual(self.check("verify", generate_report=False).returncode, 0)

    def test_candidate_empty_coverage_and_missing_leaf_fail(self):
        self.ready_local()
        path = self.fdir / "integration" / "conformance-feature.md"
        path.write_text(f"```yaml\nissue: feature\nverdict: conform\ncandidate: {self.head}\ncandidate_tree: {self.tree}\n"
                        "checker: main-session\nchecker_session: main-feature\ndate: 2026-11-15\n```\n")
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("coverage", result.stdout)

    def test_candidate_missing_leaf_evidence_fails(self):
        self.add_second_leaf()
        self.ready_local()
        path = self.fdir / "integration" / "conformance-feature.md"
        path.write_text("\n".join(line for line in path.read_text().splitlines()
                                  if not line.startswith("| 2 |")), encoding="utf-8")
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("未覆盖全部", result.stdout)

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

    def test_merged_ticket_promotes_to_done_with_accepted_event(self):
        self.ready_local("merged")
        self.assertEqual(self.candidate["issues"][0]["status"], "merged")
        result = self.promote()
        self.assertTrue(result["finalized"])
        self.assertTrue(result["cleanup_pending"])
        issue = self.fdir / "issues" / "issue-001-x.md"
        self.assertEqual(fc.read_frontmatter(issue)["status"], "done")
        accepted = [json.loads(line) for line in (self.fdir / "progress.jsonl").read_text().splitlines()
                    if json.loads(line).get("event") == "accepted"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["issue"], "issue-001")
        replay = self.run_cli("finalize", "--feature-dir", str(self.fdir), "--repo", str(self.root),
                              "--no-cleanup")
        self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
        accepted = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines()
                    if json.loads(line).get("event") == "accepted"]
        self.assertEqual(len(accepted), 1)

    def test_check_requires_feature_review_and_candidate_verification(self):
        self.freeze()
        self.write_evidence([self.auto_result(), self.human_result(True)])
        self.report()
        receipt = self.fdir / "integration" / "feature-review-receipt.json"
        backup = receipt.read_bytes()
        receipt.unlink()
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("feature-review", result.stdout)
        receipt.write_bytes(backup)
        verification = self.fdir / "integration" / "candidate-verification.json"
        backup = verification.read_bytes()
        verification.unlink()
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("candidate-verification", result.stdout)

    def test_check_rejects_ticket_status_tamper_after_freeze(self):
        self.ready_local()
        self.write_issue("merged")
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("status 在 freeze 后", result.stdout)

    def test_candidate_verification_failure_and_evidence_tamper_block(self):
        self.ready_local()
        path = self.fdir / "integration" / "candidate-verification.json"
        original = json.loads(path.read_text())
        failed = dict(original, **{"all-passed": False})
        failed["issues"] = [dict(item, **{"pass": False}) for item in original["issues"]]
        path.write_text(json.dumps(failed), encoding="utf-8")
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("并非全部通过", result.stdout)
        path.write_text(json.dumps(original), encoding="utf-8")
        evidence = self.fdir / "integration" / original["issues"][0]["verification_path"]
        evidence.write_text("{}", encoding="utf-8")
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("哈希不匹配", result.stdout)

    def test_feature_review_blocked_verdict_blocks(self):
        self.ready_local()
        path = self.fdir / "integration" / "feature-review-receipt.json"
        receipt = json.loads(path.read_text())
        receipt["verdict"] = "blocked"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        result = self.check("verify", generate_report=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("verdict=blocked", result.stdout)

    def test_finalize_cleanup_removes_ticket_and_feature_resources(self):
        self.ready_local("merged")
        ticket_wt = self.root / ".worktrees" / "issue-001"
        sh(self.root, "branch", "feat/demo-issue-001", self.merged_commit)
        sh(self.root, "worktree", "add", "-q", str(ticket_wt), "feat/demo-issue-001")
        self.promote()
        result = self.run_cli("finalize", "--feature-dir", str(self.fdir), "--repo", str(self.root))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(ticket_wt.exists())
        branches = sh(self.root, "branch", "--format=%(refname:short)")
        self.assertNotIn("feat/demo-issue-001", branches.splitlines())
        self.assertNotIn("feat/demo", branches.splitlines())
        self.assertFalse(self.wt.exists())

    def test_candidate_verification_tamper_blocks_reauthorization(self):
        self._crash_before_feature_merge()
        path = self.fdir / "integration" / "candidate-verification.json"
        value = json.loads(path.read_text())
        value["all-passed"] = False
        value["issues"] = [dict(item, **{"pass": False}) for item in value["issues"]]
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(delivery.DeliveryError, "并非全部通过"):
            self.promote()
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), self.candidate["main_head"])

    def test_feature_operation_id_binds_review_and_candidate_evidence(self):
        self.ready_local()
        candidate = self.candidate
        evidence = fc.feature_gate_evidence(self.fdir / "integration")
        first = delivery._feature_operation_id(candidate, None, evidence)
        changed = dict(evidence, confirmation_sha256="0" * 64)
        self.assertNotEqual(first, delivery._feature_operation_id(candidate, None, changed))

    def test_changed_confirmation_supersedes_pending_authorization(self):
        self._crash_before_feature_merge()
        path = self.fdir / "integration" / "confirmation.md"
        path.write_text(path.read_text() + "\n", encoding="utf-8")
        result = self.promote()
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), result["merge_commit"])
        events = [line for line in (self.fdir / "progress.jsonl").read_text().splitlines()
                  if json.loads(line).get("event") == "feature-merged"]
        self.assertEqual(len(events), 1)
        self.assertTrue(list((self.fdir / "integration" / "superseded").glob("*.superseded.json")))

    def test_public_record_merge_cli_is_gone(self):
        result = self.run_cli("record-merge", "--feature-dir", str(self.fdir), "--repo", str(self.root), "--commit", "HEAD")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
