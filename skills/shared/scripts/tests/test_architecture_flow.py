import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
import architecture_graph as graph

SPEC_HEADER = """---
doc_type: tech-spec
status: 已确认（待拆解）
---
# Tech Spec

### 3.2 接口变更（新增 / 修改 / 废弃）

| 稳定 ID | 动作 | 名称 | 模块 | 源代码位置 | supersedes | 验证证据 |
| --- | --- | --- | --- | --- | --- | --- |
"""


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.feature = self.root / "dev/features/A1/demo"
        self.issues = self.feature / "issues"
        self.issues.mkdir(parents=True)
        self.impl = self.feature / "impl/issue-001"
        self.impl.mkdir(parents=True)
        (self.root / "dev/agents-config.md").write_text("# test root marker\n", encoding="utf-8")
        (self.root / "docs/architecture").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "src.py").write_text("def run():\n    return 'v1'\n", encoding="utf-8")
        (self.root / "aux.py").write_text("def assist():\n    return 'v1'\n", encoding="utf-8")
        (self.root / "tests/test_entry.txt").write_text("entry passed\n", encoding="utf-8")
        self.spec = self.feature / "demo-tech-spec-2026-11-14.md"
        self.issue = self.issues / "issue-001-entry.md"
        self.log = self.impl / "impl-log-issue-001.md"
        self.write_spec([self.spec_row()])
        self.write_issue(["a1.entry"])
        self.write_log(True)
        self.git("init", "-q")
        self.git("config", "user.name", "T")
        self.git("config", "user.email", "t@e.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "flow inputs")
        self.put([self.row_at("HEAD")])
        self.base = self.commit("base graph")
        self.head = self.base

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    @staticmethod
    def spec_row(ident="a1.entry", action="modify", name="entry", module="A1",
                 source="src.py:run", supersedes="—", evidence="tests/test_entry.txt"):
        return "| %s | %s | %s | %s | %s | %s | %s |" % (
            ident, action, name, module, source, supersedes, evidence)

    def write_spec(self, rows):
        body = rows or ["| … | … | … | … | … | … | … |"]
        self.spec.write_text(SPEC_HEADER + "\n".join(body) + "\n\n### 3.3 数据与存储\n", encoding="utf-8")

    def write_issue(self, identifiers, extra=""):
        value = "[" + ", ".join(identifiers) + "]"
        self.issue.write_text(
            "---\nissue-id: issue-001\nstatus: in-progress\narchitecture-updates: %s\n---\n"
            "# entry\n\n## 验收标准\n%s\n" % (value, extra), encoding="utf-8")

    def write_log(self, mapping):
        row = "| a1.entry | src.py:run | bound source changed |" if mapping else "| 无 | 无 | 无 |"
        self.log.write_text(
            "# impl log\n\n## 红线接触记录\n\n| 稳定 ID | 代码位置 | 原因 |\n| --- | --- | --- |\n"
            + row + "\n\n## 漂移记录\n无\n", encoding="utf-8")

    def git_bytes(self, ref, path):
        return subprocess.check_output(["git", "-C", str(self.root), "show", "%s:%s" % (ref, path)])

    def row_at(self, ref, ident="a1.entry", name="entry", include_aux=False):
        commit = self.git("rev-parse", ref + "^{commit}")
        tree = self.git("rev-parse", ref + "^{tree}")
        evidence = []
        for kind, path in (
            ("code", "src.py"), ("test", "tests/test_entry.txt"),
            ("spec", self.spec.relative_to(self.root).as_posix()),
        ):
            evidence.append({
                "type": kind, "path": path,
                "sha256": hashlib.sha256(self.git_bytes(commit, path)).hexdigest(),
                "commit": commit, "tree": tree,
            })
        row = {
            "id": ident, "module": "A1", "kind": "interface", "name": name,
            "responsibility": "entry contract", "status": "current", "version": "v1",
            "applies_to": ["v1"],
            "source": {
                "path": "src.py", "symbol": "run",
                "sha256": hashlib.sha256(self.git_bytes(commit, "src.py")).hexdigest(),
            },
            "verification": "tests/test_entry.txt at " + commit,
            "evidence": evidence,
            "last_verified": "2026-11-14", "last_verified_version": "v1",
            "verified_commit": commit, "relations": [], "supersedes": [],
        }
        if include_aux:
            row["sources"] = [{
                "path": "aux.py", "symbol": "assist",
                "sha256": hashlib.sha256(self.git_bytes(commit, "aux.py")).hexdigest(),
            }]
        return row

    def put(self, records):
        (self.root / "docs/architecture/a.json").write_text(json.dumps({
            "schema": "pandu.architecture-graph/v2", "records": records,
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "docs/ARCHITECTURE.md").write_text(graph.homepage(records), encoding="utf-8")

    def test_flow_forward_valid_and_check_flow_cli(self):
        self.issue.write_text(self.issue.read_text(encoding="utf-8").replace(
            "architecture-updates:", "tech-spec: demo-tech-spec-2026-11-14.md\narchitecture-updates:"),
            encoding="utf-8")
        self.assertEqual([], graph.check_flow_forward(self.root, self.spec))
        self.assertEqual([], graph.check_flow_back(self.root, self.issue, self.base, self.base))
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "check-flow",
            "--repo", str(self.root), "--issue", str(self.issue),
            "--base", self.base, "--head", self.base,
        ], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_check_flow_cli_keeps_baseline_drift_out_of_ticket(self):
        self.issue.write_text(self.issue.read_text().replace(
            "architecture-updates:", "tech-spec: " + self.spec.name + "\narchitecture-updates:"))
        row = self.row_at(self.base)
        row["relations"] = [{"type": "uses", "target": "missing.baseline"}]
        self.put([row])
        base = self.commit("existing unrelated dangling relation")
        cmd = [sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "check-flow",
               "--repo", str(self.root), "--issue", str(self.issue), "--base", base, "--head", "HEAD"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        row["relations"].append({"type": "uses", "target": "missing.candidate"})
        self.put([row])
        self.commit("new dangling relation")
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("missing.candidate", result.stdout)

    def test_check_flow_cli_skips_unrelated_ticket(self):
        self.issue.write_text(self.issue.read_text().replace(
            "architecture-updates: [a1.entry]", "tech-spec: " + self.spec.name + "\narchitecture-updates: []"))
        (self.root / "unbound.py").write_text("value = 4\n")
        head = self.commit("unrelated source")
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "check-flow",
            "--repo", str(self.root), "--issue", str(self.issue), "--base", self.base, "--head", head,
        ], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("ticket-level architecture gate skipped", result.stdout)

    def test_rendered_homepage_is_not_implementation_for_legacy_rows(self):
        row = self.row_at(self.base)
        row.update(id="legacy.example", module="legacy", status="needs_verification")
        row["source"] = {"path": "docs/ARCHITECTURE.md", "symbol": "archived reference"}
        self.put([self.row_at(self.base), row])
        base = self.commit("include legacy index reference")
        index = self.root / "docs/ARCHITECTURE.md"
        index.write_text(index.read_text() + "\n")
        head = self.commit("change generated homepage only")
        errors = graph.check_flow_back(self.root, self.issue, base, head, spec_path=self.spec,
                                       validated={"records": [row], "errors": []})
        self.assertFalse(any("legacy.example: bound source changed" in error for error in errors), errors)
        _, errors = graph.render_homepage(self.root, write=False, head=head,
                                          validated={"records": [row], "errors": []})
        self.assertTrue(errors, "homepage content must still be validated")

    def test_linked_worktree_uses_only_registered_primary_dev_for_ticket(self):
        self.issue.write_text(self.issue.read_text().replace(
            "architecture-updates:", "tech-spec: " + self.spec.name + "\narchitecture-updates:"))
        checkout = self.root / ".worktrees" / "issue-001"
        self.git("worktree", "add", "--detach", str(checkout), self.base)
        (checkout / "dev").rename(checkout / "retired-dev")
        shared = checkout / "dev"
        shared.symlink_to(self.root / "dev")
        issue = shared / self.issue.relative_to(self.root / "dev")
        cmd = [sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "check-flow",
               "--repo", str(checkout), "--issue", str(issue), "--base", self.base, "--head", self.base]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        shared.unlink()
        shared.symlink_to(self.root / "wrong-dev")
        (self.root / "wrong-dev").mkdir()
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("primary worktree dev", result.stdout + result.stderr)

    def test_only_current_dated_spec_authorizes_or_blocks_graph_changes(self):
        old_spec = self.feature / "demo-tech-spec-2020-01-01.md"
        old_spec.write_text(SPEC_HEADER + self.spec_row(
            ident="a1.legacy", name="legacy", evidence="tests/old-missing.txt") + "\n", encoding="utf-8")
        self.issue.write_text(self.issue.read_text(encoding="utf-8").replace(
            "architecture-updates:", "tech-spec: demo-tech-spec-2026-11-14.md\narchitecture-updates:"),
            encoding="utf-8")
        # The old spec's missing evidence/record is irrelevant to the current spec.
        self.assertEqual([], graph.check_flow_forward(self.root, self.spec, [self.issue]))
        self.assertEqual([], graph.check_flow_back(
            self.root, self.issue, self.base, self.base, spec_path=self.spec))
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "check-flow",
            "--repo", str(self.root), "--issue", str(self.issue),
            "--base", self.base, "--head", self.base,
        ], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        legacy = self.row_at("HEAD", ident="a1.legacy", name="legacy")
        self.put([self.row_at("HEAD"), legacy])
        self.head = self.commit("graph-only old-spec ID")
        errors = graph.check_flow_back(self.root, self.issue, self.base, self.head, spec_path=self.spec)
        self.assertTrue(any("a1.legacy" in error and "graph-only" in error for error in errors), errors)
        _, homepage_errors = graph.render_homepage(self.root, write=False, head=self.head)
        self.assertEqual([], homepage_errors)
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "check-flow",
            "--repo", str(self.root), "--issue", str(self.issue),
            "--base", self.base, "--head", self.head,
        ], capture_output=True, text=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("a1.legacy", result.stdout)




    def test_validated_graph_result_is_reused_without_recheck(self):
        records, errors = graph.check(self.root, head=self.head)
        self.assertEqual([], errors)
        validated = {"records": records, "errors": []}
        with mock.patch.object(graph, "check", side_effect=AssertionError("must not recheck")):
            self.assertEqual([], graph.check_flow_forward(self.root, self.spec, validated=validated))
            _, homepage_errors = graph.render_homepage(
                self.root, write=False, head=self.head, validated=validated)
            self.assertEqual([], homepage_errors)
            self.assertEqual([], graph.check_flow_back(
                self.root, self.issue, self.base, self.head, validated=validated))


    def test_candidate_flow_attributes_each_merge_and_validates_final_graph(self):
        import ticket_context
        import verify_issue
        from test_verify_issue import CONFIG

        self.git("branch", "-M", "main")
        (self.root / "dev/agents-config.md").write_text(CONFIG)
        (self.root / ".git/info/exclude").write_text(
            "/dev/features/A1/demo/impl/issue-001/ticket-context.json\n"
            "/dev/features/A1/demo/integration/\n/dev/features/A1/demo/progress.jsonl\n")
        self.write_spec([self.spec_row(), self.spec_row(
            ident="a1.later", name="later", source="aux.py:assist")])
        self.write_issue(["a1.entry"], extra="- [ ] `true` → 退出码 `0`")
        self.issue.write_text(self.issue.read_text().replace(
            "architecture-updates:", "target-branch: main\ndeclared-scope: [src.py]\n"
            "tech-spec: " + self.spec.name + "\narchitecture-updates:").replace(
            "## 验收标准", "## 验收标准（DoD，可自动判定）"))
        (self.issues / "issue-002-later.md").write_text(
            "---\nissue-id: issue-002\narchitecture-updates: [a1.later]\n---\n")
        original = self.commit("ticket documents")
        self.git("switch", "-c", "entry")
        self.git("commit", "--allow-empty", "-qm", "first issue source")
        ticket = ticket_context.create_context(
            self.issue, self.root, self.impl / "ticket-context.json")
        self.git("switch", "main")
        self.git("merge", "--no-ff", "entry", "-m", "merge first issue")
        merged = self.git("rev-parse", "HEAD")
        (self.feature / "progress.jsonl").write_text(json.dumps(
            dict(event="merged", issue="issue-001", commit=merged, event_id="merge-001")) + "\n")

        (self.root / "aux.py").write_text("def assist():\n    return 'later'\n")
        source = self.commit("later issue implementation")
        later = self.row_at(source, ident="a1.later", name="later")
        digest = hashlib.sha256(self.git_bytes(source, "aux.py")).hexdigest()
        later["source"] = dict(path="aux.py", symbol="assist", sha256=digest)
        later["evidence"][0].update(path="aux.py", sha256=digest)
        self.put([self.row_at(original), later])
        final = self.commit("later issue architecture")
        integration = self.feature / "integration"
        integration.mkdir()
        candidate_path = integration / "candidate.json"
        candidate = dict(candidate=final, candidate_tree=self.git("rev-parse", "HEAD^{tree}"),
                         main_head=original, acceptance_plan={"sha256": "a" * 64},
                         issues=[dict(id="issue-001", status="merged", merged_commit=merged),
                                 dict(id="issue-002", status="merged", merged_commit=final)])
        candidate_path.write_text(json.dumps(candidate))
        binding = dict(candidate_path=str(candidate_path), issue_id="issue-001",
                       candidate_sha256=hashlib.sha256(candidate_path.read_bytes()).hexdigest())

        def run(*, bound=None):
            env = dict(os.environ)
            env.pop(verify_issue.CANDIDATE_FLOW_ENV, None)
            if bound is not None:
                env[verify_issue.CANDIDATE_FLOW_ENV] = json.dumps(bound)
            return subprocess.run([sys.executable, "-I", str(SCRIPT / "architecture_graph.py"),
                                   "check-flow", "--repo", str(self.root), "--issue", str(self.issue),
                                   "--base", ticket["base"], "--head", "HEAD"],
                                  env=env, capture_output=True, text=True)

        def assert_refused(result, diagnostic):
            self.assertNotEqual(0, result.returncode)
            self.assertIn(diagnostic, result.stdout + result.stderr)

        assert_refused(run(), "a1.later: impl-log")
        accepted = run(bound=binding)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        assert_refused(run(bound=dict(binding, issue_id="issue-999")), "not the requested frozen")
        candidate["issues"][0]["merged_commit"] = final
        candidate_path.write_text(json.dumps(candidate))
        assert_refused(run(bound=binding), "digest mismatch")
        binding["candidate_sha256"] = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        assert_refused(run(bound=binding), "differs from the recorded merge event")

        (self.root / "aux.py").write_text("def assist():\n    return 'untracked identity'\n")
        candidate["issues"][0]["merged_commit"] = merged
        candidate["candidate"] = self.commit("later interface validation fails")
        candidate["candidate_tree"] = self.git("rev-parse", "HEAD^{tree}")
        candidate_path.write_text(json.dumps(candidate))
        binding["candidate_sha256"] = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        assert_refused(run(bound=binding), "a1.later")

    def test_describe_ticket_preserves_unverified_record(self):
        row = self.row_at(self.base)
        row["status"] = "needs_verification"
        self.write_spec([self.spec_row(action="describe", evidence="—")])
        self.put([row])
        base = self.commit("preserve historical unverified graph")
        row["responsibility"] = "Correct the description"
        self.put([row])
        head = self.commit("correct only current responsibility wording")
        _, errors = graph.parse_spec_updates(self.spec)
        self.assertEqual([], errors)
        self.assertEqual([], graph.check_flow_forward(
            self.root, self.spec, head=head, implementation_ids={"a1.entry"}))
        self.assertEqual([], graph.check_flow_back(
            self.root, self.issue, base, head, spec_path=self.spec))

    def test_describe_rejects_changes_beyond_declared_text(self):
        row = self.row_at(self.base)
        row["status"] = "needs_verification"
        extra = self.row_at(self.base, ident="a1.unassigned")
        self.put([row, extra])
        base = self.commit("stable records to describe")
        refreshed = dict(row, responsibility="Clear text")
        mutations = {
            "status": [dict(refreshed, status="current"), extra],
            "source": [dict(refreshed, source=dict(row["source"], symbol="changed")), extra],
            "sources": [dict(refreshed, sources=[row["source"]]), extra],
            "verification": [dict(refreshed, verification="claimed"), extra],
            "evidence": [dict(refreshed, evidence=[]), extra],
            "verified_commit": [dict(refreshed, verified_commit=None), extra],
            "relationships": [dict(refreshed, relations=[{"type":"relates", "target":"a1.unassigned"}]), extra],
            "rename": [dict(refreshed, name="different name"), extra],
            "identity": [dict(refreshed, id="a1.identity"), extra],
            "blank": [dict(refreshed, responsibility=" "), extra],
            "add": [refreshed, extra, dict(extra, id="a1.added")],
            "delete": [refreshed],
            "reorder": [extra, refreshed],
            "unassigned": [refreshed, dict(extra, responsibility="Changed indirectly")],
            "no-op": [row, extra],
        }
        for case, records in mutations.items():
            with self.subTest(case=case):
                self.git("reset", "--hard", base)
                self.put(records)
                self.git("add", "docs")
                self.git("commit", "--allow-empty", "-qm", case)
                head = self.git("rev-parse", "HEAD")
                self.assertTrue(graph._describe_delta_errors(self.root, base, head, {"a1.entry"}))
        for relative in ("src.py", "tests/test_entry.txt", "runtime.yaml"):
            with self.subTest(additional_path=relative):
                self.git("reset", "--hard", base)
                self.put([refreshed, extra])
                (self.root / relative).write_text("impersonated file change")
                head = self.commit("smuggled file change")
                errors = graph._describe_delta_errors(self.root, base, head, {"a1.entry"})
                self.assertTrue(any(relative in error for error in errors), errors)
        self.git("reset", "--hard", base)
        self.put([refreshed, extra])
        shard = self.root / "docs/architecture/a.json"
        shard.write_text(shard.read_text().replace('"schema":', '"schema":"ignored","schema":', 1))
        head = self.commit("duplicate graph field")
        errors = graph._describe_delta_errors(self.root, base, head, {"a1.entry"})
        self.assertTrue(any("duplicate JSON member" in error for error in errors), errors)

    def test_describe_design_requires_exact_record_and_empty_evidence(self):
        self.write_spec([self.spec_row(action="describe")])
        _, errors = graph.parse_spec_updates(self.spec)
        self.assertTrue(any("leave verification evidence empty" in error for error in errors))
        self.write_spec([self.spec_row(action="describe", name="renamed", source="aux.py:assist",
                                       evidence="—")])
        errors = graph.check_design(self.root, self.spec)
        self.assertTrue(any("describe must preserve name" in error for error in errors))
        self.assertTrue(any("describe must preserve source.path" in error for error in errors))
        self.write_spec([self.spec_row(ident="a1.unknown", action="describe", evidence="—")])
        errors = graph.check_design(self.root, self.spec)
        self.assertTrue(any("requires an existing graph record" in error for error in errors))

    def test_feature_describe_requires_the_actual_merge_and_unchanged_final_record(self):
        import ticket_context
        from test_verify_issue import CONFIG

        self.git("branch", "-M", "main")
        (self.root / "dev/agents-config.md").write_text(CONFIG)
        (self.root / ".git/info/exclude").write_text(
            "/dev/features/A1/demo/impl/issue-001/ticket-context.json\n"
            "/dev/features/A1/demo/progress.jsonl\n")
        self.write_spec([self.spec_row(action="describe", evidence="—")])
        self.write_issue(["a1.entry"], extra="- [ ] `true` → 退出码 `0`")
        self.issue.write_text(self.issue.read_text().replace(
            "architecture-updates:", "target-branch: main\ndeclared-scope: [docs/architecture/a.json]\n"
            "tech-spec: " + self.spec.name + "\narchitecture-updates:").replace(
            "## 验收标准", "## 验收标准（DoD，可自动判定）"))
        row = self.row_at(self.base)
        row["status"] = "needs_verification"
        self.put([row])
        base = self.commit("describe baseline")
        self.git("switch", "-c", "wording")
        row["responsibility"] = "Verified wording only"
        self.put([row])
        head = self.commit("describe only")
        ticket_context.create_context(
            self.issue, self.root, self.impl / "ticket-context.json")
        self.assertEqual([], graph.check_flow_back(self.root, self.issue, base, head, self.spec))
        self.git("switch", "main")
        self.git("merge", "--no-ff", "wording", "-m", "merge describe ticket")
        merge = self.git("rev-parse", "HEAD")
        event = self.feature / "progress.jsonl"
        event.write_text(json.dumps(
            dict(event="merged", issue="issue-001", commit=merge, event_id="merge-001")) + "\n")
        self.assertEqual([], graph.check_flow_forward(self.root, self.spec, head=merge))
        command = [sys.executable, "-I", str(SCRIPT / "architecture_graph.py"),
                   "check-feature", "--repo", str(self.root), "--spec", str(self.spec),
                   "--feature-dir", str(self.feature)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        first_event = event.read_text()
        self.git("switch", "-c", "wording-follow-up")
        follow_spec = self.feature / "wording-follow-up-tech-spec-2026-11-15.md"
        follow_spec.write_text(self.spec.read_text())
        follow_issue = self.issues / "issue-002-wording.md"
        follow_issue.write_text(self.issue.read_text().replace("issue-001", "issue-002")
                                .replace(self.spec.name, follow_spec.name))
        with (self.root / ".git/info/exclude").open("a") as excludes:
            excludes.write("/dev/features/A1/demo/impl/issue-002/ticket-context.json\n"
                           "/dev/features/A1/demo/issues/issue-002-wording.md\n"
                           "/dev/features/A1/demo/wording-follow-up-tech-spec-2026-11-15.md\n")
        row["responsibility"] = "Follow-up restores wording"
        self.put([row])
        follow_head = self.commit("second describe only")
        follow_context = self.feature / "impl/issue-002/ticket-context.json"
        ticket_context.create_context(follow_issue, self.root, follow_context)
        self.git("switch", "main")
        self.git("merge", "--no-ff", "wording-follow-up", "-m", "merge second describe")
        follow_merge = self.git("rev-parse", "HEAD")
        second_event = json.dumps(dict(event="merged", issue="issue-002", commit=follow_merge,
                                       event_id="merge-002")) + "\n"
        event.write_text(first_event + second_event)
        self.assertEqual([], graph.check_flow_forward(self.root, self.spec, head=follow_merge))
        self.assertEqual([], graph.check_flow_forward(self.root, follow_spec, head=follow_merge))
        event.write_text(first_event)
        errors = graph.check_flow_forward(self.root, self.spec, head=follow_merge)
        self.assertTrue(any("outside a verified describe merge" in err for err in errors), errors)
        event.write_text(first_event + second_event)
        forged = follow_context.read_bytes()
        context = json.loads(forged)
        context["target_commit"] = base
        context["gate2_context_digest"] = ticket_context.context_digest(context)
        follow_context.write_text(json.dumps(context))
        errors = graph.check_flow_forward(self.root, self.spec, head=follow_merge)
        self.assertTrue(any("does not match the frozen ticket" in err for err in errors), errors)
        follow_context.write_bytes(forged)
        errors = graph.check_flow_forward(
            self.root, self.spec, head=follow_merge, feature_issue_ids={"issue-001"})
        self.assertTrue(any("outside a verified describe merge" in err for err in errors), errors)

        row["responsibility"] = "Intermediate unauthorized wording"
        self.put([row])
        self.commit("unowned intermediate graph change")
        row["responsibility"] = "Follow-up restores wording"
        self.put([row])
        back = self.commit("restore after unowned graph drift")
        errors = graph.check_flow_forward(self.root, self.spec, head=back)
        self.assertTrue(any("outside a verified describe merge" in err for err in errors), errors)

        self.git("reset", "--hard", follow_merge)
        trade_spec = follow_spec.read_text()
        for action in ("new", "modify", "retire"):
            with self.subTest(follow_up_action=action):
                follow_spec.write_text(trade_spec.replace("describe", action)
                                      .replace("| — | — |", "| — | tests/test_entry.txt |"))
                errors = graph.check_flow_forward(self.root, self.spec, head=follow_merge)
                self.assertTrue(any("outside a verified describe merge" in err for err in errors), errors)
        follow_spec.write_text(trade_spec)
        row["responsibility"] = "Dirty terminal presentation"
        self.put([row])
        errors = graph.check_flow_forward(self.root, self.spec, head=follow_merge)
        self.assertTrue(any("differs from its verified wording merge" in err for err in errors), errors)
        self.git("reset", "--hard", follow_merge)
        row["responsibility"] = "Undeclared subsequent edit"
        self.put([row])
        head = self.commit("change old describe record again")
        errors = graph.check_flow_forward(self.root, self.spec, head=head)
        self.assertTrue(any("differs from its verified wording merge" in error for error in errors))
        event.write_text(json.dumps(dict(event="merged", issue="issue-001", commit=head,
                                        event_id="merge-001")) + "\n")
        errors = graph.check_flow_forward(self.root, self.spec, head=head)
        self.assertTrue(any("does not match the frozen ticket" in error for error in errors))

        self.git("reset", "--hard", merge)
        self.git("switch", "-c", "wording-with-extra-code")
        row["responsibility"] = "Follow-up that smuggles an executable source"
        self.put([row])
        (self.root / "src.py").write_text("def run():\n    return 'unapproved'\n")
        self.commit("mix wording and code")
        ticket_context.create_context(follow_issue, self.root, follow_context)
        self.git("switch", "main")
        self.git("merge", "--no-ff", "wording-with-extra-code", "-m", "merge mixed follow-up")
        unsafe = self.git("rev-parse", "HEAD")
        event.write_text(first_event + json.dumps(dict(event="merged", issue="issue-002", commit=unsafe,
                                                       event_id="merge-002")) + "\n")
        errors = graph.check_flow_forward(self.root, self.spec, head=unsafe)
        self.assertTrue(any("describe may only change graph shards" in err and "src.py" in err
                            for err in errors), errors)

    def test_flow_forward_empty_and_placeholder_tables_are_ignored(self):
        self.write_spec([])
        self.assertEqual([], graph.check_flow_forward(self.root, self.spec))
        self.spec.write_text("---\nstatus: 已确认（待拆解）\n---\n### 3.2 接口变更\n\n<!-- empty -->\n### 3.3 x\n", encoding="utf-8")
        self.assertEqual([], graph.check_flow_forward(self.root, self.spec))

    def test_flow_forward_multiple_tickets_cover_every_id(self):
        second = self.row_at("HEAD", ident="a1.other", name="other")
        self.put([self.row_at("HEAD"), second])
        self.write_spec([self.spec_row(), self.spec_row(ident="a1.other", name="other", action="new")])
        second_issue = self.issues / "issue-002-other.md"
        second_issue.write_text("---\nissue-id: issue-002\narchitecture-updates: [a1.other]\n---\n", encoding="utf-8")
        self.assertEqual([], graph.check_flow_forward(self.root, self.spec))
        second_issue.write_text("---\nissue-id: issue-002\narchitecture-updates: []\n---\n", encoding="utf-8")
        errors = graph.check_flow_forward(self.root, self.spec)
        self.assertTrue(any("a1.other has no ticket" in error for error in errors), errors)

    def test_flow_forward_wrong_id_and_architecture_prose_do_not_pass(self):
        self.write_issue(["a1.wrong"], extra="ARCHITECTURE must be updated")
        errors = graph.check_flow_forward(self.root, self.spec)
        self.assertTrue(any("wrong or out-of-spec" in error for error in errors), errors)
        self.assertTrue(any("a1.entry has no ticket" in error for error in errors), errors)

    def test_flow_forward_missing_spec_id_fails(self):
        self.write_spec([self.spec_row(ident="")])
        entries, errors = graph.parse_spec_updates(self.spec)
        self.assertEqual([], entries)
        self.assertTrue(any("missing stable ID" in error for error in errors), errors)

    def test_flow_forward_checks_action_supersedes_and_declared_evidence(self):
        self.write_spec([self.spec_row(action="new", supersedes="a1.old", evidence="tests/missing.txt")])
        errors = graph.check_flow_forward(self.root, self.spec)
        self.assertTrue(any("supersedes does not match" in error for error in errors), errors)
        self.assertTrue(any("supersedes target is absent" in error for error in errors), errors)
        self.assertTrue(any("lacks declared verification evidence" in error for error in errors), errors)

    def modify_code_and_graph(self, declared=True):
        (self.root / "src.py").write_text("def run():\n    return 'v2'\n", encoding="utf-8")
        code_commit = self.commit("change bound source")
        self.put([self.row_at(code_commit)])
        self.write_issue(["a1.entry"] if declared else [])
        return self.commit("refresh graph evidence")

    def test_flow_back_declared_source_change_refreshes_shard_and_evidence(self):
        self.head = self.modify_code_and_graph(declared=True)
        self.assertEqual([], graph.check_flow_back(self.root, self.issue, self.base, self.head))

    def test_flow_back_undeclared_change_allows_complete_supplement(self):
        self.head = self.modify_code_and_graph(declared=False)
        self.assertEqual([], graph.check_flow_back(self.root, self.issue, self.base, self.head))

    def test_flow_back_undeclared_change_requires_impl_log_mapping(self):
        self.head = self.modify_code_and_graph(declared=False)
        self.write_log(False)
        errors = graph.check_flow_back(self.root, self.issue, self.base, self.head)
        self.assertTrue(any("impl-log 红线接触记录" in error for error in errors), errors)

    def test_flow_back_missing_shard_and_verification_refresh_fail(self):
        (self.root / "src.py").write_text("def run():\n    return 'v2'\n", encoding="utf-8")
        self.head = self.commit("source without graph")
        errors = graph.check_flow_back(self.root, self.issue, self.base, self.head)
        self.assertTrue(any("without updating its graph shard" in error for error in errors), errors)
        self.assertTrue(any("verification information was not refreshed" in error for error in errors), errors)

    def test_flow_back_auxiliary_source_hit_requires_and_allows_refresh(self):
        self.put([self.row_at("HEAD", include_aux=True)])
        self.base = self.commit("bind auxiliary source")
        (self.root / "aux.py").write_text("def assist():\n    return 'v2'\n", encoding="utf-8")
        self.head = self.commit("auxiliary source without graph")
        errors = graph.check_flow_back(self.root, self.issue, self.base, self.head)
        self.assertTrue(any("a1.entry" in error and "without updating its graph shard" in error
                            for error in errors), errors)
        self.assertTrue(any("a1.entry" in error and "verification information was not refreshed" in error
                            for error in errors), errors)

        self.put([self.row_at(self.head, include_aux=True)])
        self.head = self.commit("refresh auxiliary source verification")
        self.assertEqual([], graph.check_flow_back(self.root, self.issue, self.base, self.head))

    def test_flow_back_deleted_source_requires_retired_or_superseded(self):
        (self.root / "src.py").unlink()
        deleted = self.commit("delete bound source")
        row = self.row_at(self.base)
        row["verification"] = "retired after source deletion"
        self.put([row])
        self.head = self.commit("graph without retirement")
        errors = graph.check_flow_back(self.root, self.issue, self.base, self.head)
        self.assertTrue(any("requires retired or superseded" in error for error in errors), errors)

    def test_flow_back_renamed_source_requires_retired_or_superseded(self):
        self.git("mv", "src.py", "renamed.py")
        self.commit("rename bound source")
        row = self.row_at(self.base)
        row["verification"] = "rename not reconciled"
        self.put([row])
        self.head = self.commit("graph without rename retirement")
        errors = graph.check_flow_back(self.root, self.issue, self.base, self.head)
        self.assertTrue(any("requires retired or superseded" in error for error in errors), errors)

    def test_flow_back_retired_deleted_source_passes(self):
        row = self.row_at(self.base)
        (self.root / "src.py").unlink()
        self.commit("delete bound source")
        row.update({
            "status": "retired", "verification": "retired with deletion evidence",
            "verified_commit": None, "last_verified": None,
            "last_verified_version": "unverified",
        })
        self.put([row])
        self.head = self.commit("retire graph record")
        self.assertEqual([], graph.check_flow_back(self.root, self.issue, self.base, self.head))

    def test_graph_only_change_without_ticket_or_spec_obligation_fails(self):
        row = self.row_at("HEAD")
        row["responsibility"] = "unilateral graph edit"
        self.put([row])
        self.write_spec([])
        self.write_issue([])
        self.head = self.commit("graph only without obligation")
        errors = graph.check_flow_back(self.root, self.issue, self.base, self.head)
        self.assertTrue(any("graph-only change" in error for error in errors), errors)

    def test_ticket_scope_does_not_require_other_tickets_current_but_feature_does(self):
        self.write_spec([self.spec_row(), self.spec_row(ident="a1.other", name="other", action="new")])
        second_issue = self.issues / "issue-002-other.md"
        second_issue.write_text("---\nissue-id: issue-002\narchitecture-updates: [a1.other]\n---\n", encoding="utf-8")
        self.head = self.commit("two tickets, second not implemented")
        ticket_errors = graph.check_flow_forward(
            self.root, self.spec, [self.issue, second_issue], head=self.head,
            implementation_ids={"a1.entry"})
        self.assertEqual([], ticket_errors)
        feature_errors = graph.check_flow_forward(
            self.root, self.spec, [self.issue, second_issue], head=self.head)
        self.assertTrue(any("a1.other" in error and "missing" in error for error in feature_errors), feature_errors)

    def test_design_gate_and_active_leaf(self):
        self.write_spec([self.spec_row(ident="a1.other", name="other", action="new")])
        text = self.spec.read_text(encoding="utf-8")
        text = text.replace("# Tech Spec\n", "# Tech Spec\n\n### 2.1 How / Why\n\nresearch/demo-how.md\n\n### 3.6 承重决策\n\nDEC-01 base+graft\n\n### 3.7 Usage\n\ncaller migration\n")
        self.spec.write_text(text, encoding="utf-8")
        self.assertEqual([], graph.check_design(self.root, self.spec))
        successor = self.feature / "demo-tech-spec-2026-11-15.md"
        successor.write_text(text.replace("---\ndoc_type: tech-spec", "---\ndoc_type: tech-spec\nsupersedes: " + self.spec.name), encoding="utf-8")
        self.assertEqual([], graph.check_design(self.root, successor))
        errors = graph.check_design(self.root, self.spec)
        self.assertTrue(any("superseded" in error for error in errors), errors)

    def test_plan_scope_still_requires_static_assignment_for_existing_ids(self):
        self.write_issue([])
        errors = graph.check_flow_forward(self.root, self.spec, [self.issue], implementation_ids=set())
        self.assertTrue(any("a1.entry has no ticket" in error for error in errors), errors)



if __name__ == "__main__":
    unittest.main()
