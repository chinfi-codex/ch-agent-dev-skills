import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_process_forward_uses_all_tickets_for_frozen_spec(self):
        import process_check
        second = self.row_at("HEAD", ident="a1.other", name="other")
        self.put([self.row_at("HEAD"), second])
        self.write_spec([self.spec_row(), self.spec_row(ident="a1.other", name="other", action="new")])
        second_issue = self.issues / "issue-002-other.md"
        second_issue.write_text("---\nissue-id: issue-002\narchitecture-updates: [a1.other]\n---\n", encoding="utf-8")
        self.head = self.commit("two IDs owned by two tickets")
        context = {"spec_path": self.spec.relative_to(self.root).as_posix(),
                   "architecture_updates": []}
        (self.root / "dev/agents-config.md").write_text("# test root marker\n", encoding="utf-8")
        report = process_check.Report()
        process_check._check_architecture(report, self.root, self.issue, context, [], self.base, self.head)
        self.assertFalse(report.failed, report.items)
        second_issue.write_text("---\nissue-id: issue-002\narchitecture-updates: []\n---\n", encoding="utf-8")
        report = process_check.Report()
        process_check._check_architecture(report, self.root, self.issue, context, [], self.base, self.head)
        self.assertTrue(any("a1.other has no ticket" in item["msg"] for item in report.items), report.items)

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

    def test_process_accepts_complete_undeclared_flow_back(self):
        import process_check
        self.head = self.modify_code_and_graph(declared=False)
        (self.root / "dev/agents-config.md").write_text("# test root marker\n", encoding="utf-8")
        context = {"spec_path": self.spec.relative_to(self.root).as_posix(),
                   "architecture_updates": []}
        report = process_check.Report()
        process_check._check_architecture(report, self.root, self.issue, context, [], self.base, self.head)
        self.assertFalse(report.failed, report.items)


if __name__ == "__main__":
    unittest.main()
