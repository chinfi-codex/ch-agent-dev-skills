import copy
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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs/architecture").mkdir(parents=True)
        (self.root / "evidence.txt").write_text("pending evidence\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def init_repo(self):
        if (self.root / ".git").exists():
            return self.git("rev-parse", "HEAD")
        self.git("init", "-q")
        self.git("config", "user.name", "T")
        self.git("config", "user.email", "t@e.invalid")
        (self.root / "src.py").write_text("def run(): pass\n", encoding="utf-8")
        (self.root / "aux.py").write_text("def assist(): pass\n", encoding="utf-8")
        (self.root / "test.txt").write_text("test passed\n", encoding="utf-8")
        (self.root / "spec.md").write_text("spec evidence\n", encoding="utf-8")
        self.git("add", "src.py", "aux.py", "test.txt", "spec.md", "evidence.txt")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def put(self, records, name="a.json", **document):
        value = {"schema": "pandu.architecture-graph/v2", "records": records, **document}
        (self.root / "docs/architecture" / name).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def row(self, **changes):
        row = {
            "id": "a1.entry",
            "module": "A1",
            "kind": "interface",
            "name": "entry",
            "responsibility": "test responsibility",
            "status": "needs_verification",
            "version": "v1",
            "applies_to": ["v1"],
            "source": {"path": "src.py", "symbol": "run"},
            "verification": "pending verification",
            "evidence": [{
                "type": "other", "path": "evidence.txt",
                "sha256": digest(self.root / "evidence.txt"),
            }],
            "last_verified": None,
            "last_verified_version": "unverified",
            "verified_commit": None,
            "relations": [],
            "supersedes": [],
        }
        row.update(changes)
        return row

    def current_row(self, **changes):
        commit = self.init_repo()
        tree = self.git("rev-parse", "HEAD^{tree}")
        source = {
            "path": "src.py", "symbol": "run",
            "sha256": digest(self.root / "src.py"),
        }
        evidence = []
        for kind, path in (("code", "src.py"), ("test", "test.txt"), ("spec", "spec.md")):
            evidence.append({
                "type": kind, "path": path, "sha256": digest(self.root / path),
                "commit": commit, "tree": tree,
            })
        row = self.row(
            status="current", source=source, verification="verified fixture",
            evidence=evidence, last_verified="2026-11-05",
            last_verified_version="v1", verified_commit=commit,
        )
        row.update(changes)
        return row

    def auxiliary_source(self, **changes):
        self.init_repo()
        source = {
            "path": "aux.py", "symbol": "assist",
            "sha256": digest(self.root / "aux.py"),
        }
        source.update(changes)
        return source

    def write_legacy(self):
        (self.root / "docs/ARCHITECTURE.md").write_text(
            "<!-- baseline: 2026-10-22 -->\n"
            "## 接口现行态\n"
            "| Interface | Contract | Source | Origin | Date |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| run | sig | src.py:run | spec.md | 2026-01-01 |\n",
            encoding="utf-8")

    def test_valid_current_and_queryable(self):
        row = self.current_row()
        self.put([row])
        records, errors = graph.check(self.root)
        self.assertEqual([], errors)
        self.assertEqual(1, len(graph.select(records, module="A1", interface="entry")))

    def test_valid_multi_source_current_and_release_only(self):
        for status in ("current", "release_only"):
            with self.subTest(status=status):
                row = self.current_row(status=status, sources=[self.auxiliary_source()])
                self.put([row])
                records, errors = graph.check(self.root)
                self.assertEqual([], errors)
                self.assertEqual("src.py", records[0]["source"]["path"])
                self.assertEqual(["aux.py"], [source["path"] for source in records[0]["sources"]])

    def test_pending_multi_source_is_not_automatically_promoted(self):
        row = self.row(sources=[self.auxiliary_source()])
        self.put([row])
        records, errors = graph.check(self.root)
        self.assertEqual([], errors)
        self.assertEqual("needs_verification", records[0]["status"])
        self.assertIsNone(records[0]["verified_commit"])
        self.assertEqual([], graph.select(records))
        self.assertEqual(["a1.entry"], [
            record["id"] for record in graph.select(records, status="needs_verification")
        ])

    def test_auxiliary_source_hash_symbol_and_missing_file_are_rejected(self):
        cases = {
            "hash": (self.auxiliary_source(sha256="0" * 64),
                     "source hash does not match verified_commit: aux.py"),
            "symbol": (self.auxiliary_source(symbol="missing"),
                       "source symbol missing at verified_commit: missing"),
            "missing": (self.auxiliary_source(path="missing.py", sha256="0" * 64),
                        "cannot read source at verified_commit: missing.py"),
        }
        for status in ("current", "release_only"):
            for label, (auxiliary, expected) in cases.items():
                with self.subTest(status=status, case=label):
                    self.put([self.current_row(status=status, sources=[auxiliary])])
                    _, errors = graph.check(self.root)
                    self.assertTrue(any(expected in error for error in errors), errors)
                    self.assertTrue(any("HEAD" in error and auxiliary["path"] in error
                                        for error in errors), errors)

    def test_auxiliary_source_head_drift_is_rejected(self):
        for status in ("current", "release_only"):
            with self.subTest(status=status):
                row = self.current_row(status=status, sources=[self.auxiliary_source()])
                (self.root / "aux.py").write_text(
                    "def assist(): return %r\n" % status, encoding="utf-8")
                self.git("add", "aux.py")
                self.git("commit", "-qm", "auxiliary source moved after verification")
                self.put([row])
                _, errors = graph.check(self.root)
                self.assertTrue(any("stale source hash at HEAD: aux.py" in error
                                    for error in errors), errors)
                self.assertTrue(any("source changed since verified_commit: aux.py" in error
                                    for error in errors), errors)

    def test_duplicate_source_bindings_are_rejected_but_multiple_symbols_share_a_file(self):
        cases = {
            "primary": [self.auxiliary_source(path="src.py", symbol="run")],
            "auxiliary": [self.auxiliary_source(), self.auxiliary_source()],
            "same-symbol": [self.auxiliary_source(), self.auxiliary_source(sha256="0" * 64)],
        }
        for label, sources in cases.items():
            with self.subTest(case=label):
                self.put([self.row(sources=sources)])
                _, errors = graph.check(self.root)
                self.assertTrue(any("duplicate" in error or "unique" in error for error in errors), errors)

        self.init_repo()
        another_symbol = self.auxiliary_source(path="src.py", symbol="other",
                                                sha256=digest(self.root / "src.py"))
        self.put([self.row(sources=[another_symbol])])
        _, errors = graph.check(self.root)
        self.assertEqual(errors, [], errors)

    def test_release_only_auxiliary_source_requires_head_ancestry(self):
        row = self.current_row(status="release_only", sources=[self.auxiliary_source()])
        self.git("checkout", "--orphan", "other")
        self.git("rm", "-rf", ".")
        (self.root / "src.py").write_text("def run(): pass\n", encoding="utf-8")
        (self.root / "aux.py").write_text("def assist(): pass\n", encoding="utf-8")
        self.git("add", "src.py", "aux.py")
        self.git("commit", "-qm", "independent root")
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("release_only verified_commit is not an ancestor" in error
                            for error in errors), errors)

    def test_unknown_fields_and_wrong_types_are_rejected(self):
        cases = [
            ("document", lambda doc: doc.update(extra=True)),
            ("record", lambda doc: doc["records"][0].update(extra=True)),
            ("source", lambda doc: doc["records"][0]["source"].update(extra=True)),
            ("sources-type", lambda doc: doc["records"][0].update(sources={})),
            ("sources-field", lambda doc: doc["records"][0].update(
                sources=[{"path": "aux.py", "extra": True}])),
            ("sources-sha256", lambda doc: doc["records"][0].update(
                sources=[{"path": "aux.py", "sha256": "0" * 63}])),
            ("sources-symbol", lambda doc: doc["records"][0].update(
                sources=[{"path": "aux.py", "symbol": ""}])),
            ("relation", lambda doc: doc["records"][0]["relations"].append(
                {"type": "calls", "target": "a1.other", "extra": True})),
            ("evidence", lambda doc: doc["records"][0]["evidence"][0].update(extra=True)),
            ("record-type", lambda doc: doc["records"][0]["source"].__setitem__("path", [])),
            ("relation-type", lambda doc: doc["records"][0]["relations"].append(
                {"type": "unknown", "target": "a1.other"})),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                row = self.row()
                if label == "relation":
                    row["relations"] = []
                doc = {"schema": "pandu.architecture-graph/v2", "records": [row]}
                mutate(doc)
                (self.root / "docs/architecture/a.json").write_text(json.dumps(doc), encoding="utf-8")
                _, errors = graph.check(self.root)
                self.assertTrue(errors, label)
                self.assertTrue(any("$." in error or "$:" in error for error in errors), errors)

    def test_full_sha_date_and_id_patterns_are_enforced(self):
        row = self.row(id="BAD ID", last_verified="2026-02-30", verified_commit="abc123")
        self.put([row])
        _, errors = graph.check(self.root)
        text = "\n".join(errors)
        self.assertIn("pattern", text)
        self.assertIn("date", text)
        self.assertIn("git-sha", text)

    def test_current_requires_full_resolvable_verified_commit(self):
        row = self.row(status="current", verified_commit="abc123")
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("full 40-character verified_commit" in error for error in errors))

    def test_non_ancestor_current_is_rejected(self):
        row = self.current_row()
        self.git("checkout", "--orphan", "other")
        self.git("rm", "-rf", ".")
        (self.root / "src.py").write_text("def run(): pass\n", encoding="utf-8")
        self.git("add", "src.py")
        self.git("commit", "-qm", "independent root")
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("not an ancestor" in error for error in errors), errors)

    def test_missing_and_tampered_evidence_are_rejected(self):
        row = self.current_row()
        row["evidence"][1]["sha256"] = "0" * 64
        row["evidence"][2]["path"] = "missing.txt"
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("evidence hash does not match" in error for error in errors), errors)
        self.assertTrue(any("missing at" in error for error in errors), errors)

    def test_evidence_outside_repo_and_tree_tampering_are_rejected(self):
        row = self.current_row()
        row["evidence"][0]["tree"] = "0" * 40
        row["evidence"].append({
            "type": "other", "path": "../outside", "sha256": "0" * 64,
        })
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("tree" in error and "resolvable" in error for error in errors), errors)
        self.assertTrue(any("escapes repository" in error for error in errors), errors)

    def test_stale_source_hash_is_rejected_for_current(self):
        row = self.current_row()
        row["source"]["sha256"] = "0" * 64
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("stale source hash" in error for error in errors))

    def test_source_change_after_verified_commit_is_rejected(self):
        row = self.current_row()
        (self.root / "src.py").write_text("def run(): return 'changed'\n", encoding="utf-8")
        self.git("add", "src.py")
        self.git("commit", "-qm", "source moved after verification")
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("source changed since verified_commit" in error for error in errors), errors)

    def test_release_only_uses_historical_commit_and_not_default_query(self):
        row = self.current_row(status="release_only")
        (self.root / "src.py").unlink()
        self.put([row])
        records, errors = graph.check(self.root)
        self.assertEqual([], errors)
        self.assertEqual([], graph.select(records))
        self.assertEqual(["a1.entry"], [r["id"] for r in graph.select(records, version="v1")])

    def test_dangling_relation_bad_type_and_supersedes_cycle(self):
        first = self.row(supersedes=["a1.second"], relations=[{"type": "calls", "target": "missing.id"}])
        second = self.row(id="a1.second", supersedes=["a1.entry"], relations=[{"type": "bad", "target": "a1.entry"}])
        self.put([first, second])
        _, errors = graph.check(self.root)
        self.assertTrue(any("dangling relation" in error for error in errors))
        self.assertTrue(any("invalid relation type" in error for error in errors))
        self.assertTrue(any("supersedes cycle" in error for error in errors))

    def test_duplicate_id_across_shards_and_current_interface_conflict(self):
        first = self.current_row()
        second = self.current_row(id="a1.other")
        self.put([first, second])
        self.put([first], name="b.json")
        _, errors = graph.check(self.root)
        self.assertTrue(any("duplicate id across shards" in error for error in errors))
        self.assertTrue(any("conflicting current interface" in error for error in errors))

    def test_base_head_detects_disappearance_and_illegal_id_reuse(self):
        self.init_repo()
        old = self.row(id="a1.old")
        stable = self.row(id="a1.stable")
        self.put([old, stable])
        self.git("add", "docs/architecture/a.json")
        self.git("commit", "-qm", "base graph")
        base = self.git("rev-parse", "HEAD")
        changed = copy.deepcopy(stable)
        changed["name"] = "different-interface"
        self.put([changed])
        self.git("add", "docs/architecture/a.json")
        self.git("commit", "-qm", "illegal graph edit")
        head = self.git("rev-parse", "HEAD")
        _, errors = graph.check(self.root, base=base, head=head)
        self.assertTrue(any("stable id disappeared: a1.old" in error for error in errors), errors)
        self.assertTrue(any("stable id reused" in error for error in errors), errors)

    def test_current_query_excludes_retired_pending_and_superseded(self):
        pending = self.row()
        retired = self.row(id="a1.old", status="retired")
        superseded = self.row(id="a1.previous", status="superseded")
        current = self.current_row(id="a1.v1")
        rows = [current, pending, retired, superseded]
        self.assertEqual(["a1.v1"], [r["id"] for r in graph.select(rows)])
        self.assertEqual(["a1.entry"], [r["id"] for r in graph.select(rows, status="needs_verification")])

    def test_versioned_query_requires_matching_applicability(self):
        rows = [
            self.row(id="a1.v1", version="v1", applies_to=["v1"]),
            self.row(id="a1.v2", version="v2", applies_to=["v2"]),
        ]
        self.assertEqual(["a1.v1"], [r["id"] for r in graph.select(
            rows, version="v1", status="needs_verification")])

    def test_homepage_is_deterministic_and_byte_checked_by_cli(self):
        self.put([self.row()])
        first = graph.homepage([self.row()], "2026-10-22")
        self.assertEqual(first, graph.homepage([self.row()], "2026-10-22"))
        self.assertLess(len(first.splitlines()), 21)
        command = [sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "render"]
        written = subprocess.run(command + ["write", "--repo", str(self.root)], capture_output=True, text=True)
        self.assertEqual(0, written.returncode, written.stdout + written.stderr)
        checked = subprocess.run(command + ["check", "--repo", str(self.root)], capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stdout)
        with (self.root / "docs/ARCHITECTURE.md").open("a", encoding="utf-8") as handle:
            handle.write("hand edit\n")
        drift = subprocess.run(command + ["check", "--repo", str(self.root)], capture_output=True, text=True)
        self.assertEqual(1, drift.returncode)
        self.assertIn("byte comparison failed", drift.stdout)

    def test_homepage_stale_when_shard_changes(self):
        self.put([self.row()])
        graph.render_homepage(self.root, write=True)
        self.put([self.row(), self.row(id="a1.other")])
        _, errors = graph.render_homepage(self.root, write=False)
        self.assertTrue(any("stale" in error for error in errors))

    def test_legacy_requires_one_time_migration_and_never_auto_current(self):
        self.write_legacy()
        records, errors = graph.read_records(self.root)
        self.assertEqual([], records)
        self.assertTrue(any("migration required" in error for error in errors))
        migrated_cli = subprocess.run([
            sys.executable, "-I", str(SCRIPT / "architecture_graph.py"),
            "migrate-legacy", "--repo", str(self.root),
        ], capture_output=True, text=True)
        self.assertEqual(0, migrated_cli.returncode, migrated_cli.stdout + migrated_cli.stderr)
        migrated, errors = graph.read_records(self.root)
        self.assertEqual([], errors)
        self.assertEqual(1, len(migrated))
        self.assertEqual("needs_verification", migrated[0]["status"])
        self.assertEqual([], graph.select(migrated))
        self.assertIsNone(migrated[0]["verified_commit"])
        self.assertIsNone(migrated[0]["last_verified"])
        self.assertIsInstance(migrated[0]["evidence"][0], dict)
        audit = json.loads((self.root / "docs/architecture/evidence/cutover-audit.json").read_text(encoding="utf-8"))
        self.assertFalse(audit["automatically_current"])
        self.assertEqual("2026-10-22", audit["spec_baseline_date"])
        self.assertNotIn("接口现行态", (self.root / "docs/ARCHITECTURE.md").read_text(encoding="utf-8"))
        self.assertTrue(graph.migrate_legacy(self.root))

    def test_runtime_dual_source_is_rejected_not_merged(self):
        self.write_legacy()
        self.put([self.row(id="a1.graph")])
        records, errors = graph.read_records(self.root)
        self.assertEqual(["a1.graph"], [record["id"] for record in records])
        self.assertTrue(any("dual source" in error for error in errors))
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT / "architecture_graph.py"), "--repo", str(self.root)],
            capture_output=True, text=True,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("dual source", result.stdout)

    def test_empty_graph_does_not_mask_legacy_rows(self):
        self.write_legacy()
        self.put([])
        records, errors = graph.read_records(self.root)
        self.assertEqual([], records)
        self.assertTrue(any("migration required" in error for error in errors))

    def test_malformed_records_return_schema_and_semantic_errors_not_exceptions(self):
        bad = self.row(id=[], status=[], supersedes=None, relations=None, evidence=None)
        good = self.row(id="a1.good", supersedes=[{}])
        self.put([bad, good])
        _, errors = graph.check(self.root)
        self.assertTrue(any("empty id" in error for error in errors))
        self.assertTrue(any("invalid status" in error for error in errors))
        self.assertTrue(any("supersedes must be a list" in error for error in errors))
        self.assertTrue(any("relations must be a list" in error for error in errors))
        self.assertTrue(any("invalid supersedes target" in error for error in errors))

    def test_malformed_evidence_provenance_does_not_reach_git(self):
        row = self.row(relations=[{"type": [], "target": "a1.entry"}])
        row["evidence"][0]["type"] = []
        row["evidence"][0]["commit"] = []
        self.put([row])
        _, errors = graph.check(self.root)
        self.assertTrue(any("invalid evidence type" in error for error in errors), errors)
        self.assertTrue(any("invalid relation type" in error for error in errors), errors)
        self.assertTrue(any("evidence commit is not resolvable" in error for error in errors), errors)

    def test_contract_check_reports_graph_errors(self):
        (self.root / "docs/architecture/bad.json").write_text(
            json.dumps({"schema": "pandu.architecture-graph/v2", "records": [{"id": "broken"}]}),
            encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT / "contract_check.py"), "--check", "--repo", str(self.root)],
            capture_output=True, text=True,
        )
        self.assertIn("GRAPH-1", result.stdout)


if __name__ == "__main__":
    unittest.main()
