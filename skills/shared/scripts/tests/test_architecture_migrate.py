import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
import architecture_graph as graph
import architecture_migrate as migration
import state_store

DATE = "2026-11-15"
BACKUP = Path("docs/architecture/evidence/v1-v2-migration-20261115")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs/architecture").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "src.py").write_text("def run():\n    return 'ok'\n\ndef other():\n    return True\n", encoding="utf-8")
        (self.root / "helper.py").write_text("def assist():\n    return True\n", encoding="utf-8")
        (self.root / "tests/test_src.py").write_text("def test_run():\n    assert True\n", encoding="utf-8")
        (self.root / "docs/spec.md").write_text("# run specification\n", encoding="utf-8")
        (self.root / "data.bin").write_bytes(b"evidence\x00bytes\n")
        (self.root / "docs/ARCHITECTURE.md").write_bytes(b"original homepage\x00bytes\n")
        self.git("init", "-q")
        self.git("config", "user.name", "Migration Test")
        self.git("config", "user.email", "migration@example.invalid")
        self.git("add", "src.py", "helper.py", "tests/test_src.py", "docs/spec.md", "data.bin")
        self.git("commit", "-qm", "source fixture")
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        self.original_homepage = (self.root / "docs/ARCHITECTURE.md").read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def row(self, ident="a1.entry", **changes):
        row = {
            "id": ident,
            "module": "A1",
            "responsibility": "migration fixture responsibility",
            "kind": "interface",
            "name": ident,
            "status": "needs_verification",
            "version": "v1",
            "applies_to": ["v1"],
            "source": {"path": "src.py", "symbol": "run"},
            "verification": "legacy verification annotation",
            "evidence": ["tests/test_src.py"],
            "last_verified": "unknown",
            "last_verified_version": "",
            "verified_commit": None,
            "relations": [],
            "supersedes": None,
        }
        row.update(changes)
        return row

    def current_row(self, ident="a1.entry", **changes):
        row = self.row(
            ident,
            status="current",
            source={"path": "src.py", "symbol": "run", "sha256": digest(self.root / "src.py")},
            evidence=["src.py", "tests/test_src.py", "docs/spec.md"],
            last_verified="2026-11-01",
            last_verified_version="v1",
            verified_commit=self.commit,
        )
        row.update(changes)
        return row

    def put(self, records, name="a.json", **document):
        value = {"schema": migration.V1_SCHEMA, "module": "A1",
                 "purpose": "v1 migration fixture", "records": records, **document}
        path = self.root / "docs/architecture" / name
        path.write_bytes(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        return path

    def apply(self):
        return migration.migrate(self.root, apply=True, date=DATE)

    def output(self, name="a.json"):
        return json.loads((self.root / "docs/architecture" / name).read_text(encoding="utf-8"))

    def record(self, ident="a1.entry", name="a.json"):
        return next(item for item in self.output(name)["records"] if item["id"] == ident)

    def audit(self):
        return json.loads((self.root / BACKUP / "audit.json").read_text(encoding="utf-8"))

    def test_dry_run_is_deterministic_and_valid_current_is_preserved(self):
        original = self.current_row(
            kind="legacy_query_interface", supersedes=None,
            last_verified_version="", legacy={"row": 3},
            change_notes="legacy note", row_ordinal=3,
        )
        shard = self.put([original])
        original_shard = shard.read_bytes()
        before_homepage = (self.root / "docs/ARCHITECTURE.md").read_bytes()

        first = migration.migrate(self.root, date=DATE)
        second = migration.migrate(self.root, date=DATE)
        self.assertEqual(first, second)
        self.assertFalse(first["applied"])
        self.assertEqual(1, first["record_count"])
        self.assertEqual(0, first["automatic_promotions"])
        self.assertEqual(original_shard, shard.read_bytes())
        self.assertEqual(before_homepage, (self.root / "docs/ARCHITECTURE.md").read_bytes())
        self.assertFalse((self.root / BACKUP).exists())

        result = self.apply()
        self.assertTrue(result["applied"])
        document = self.output()
        self.assertEqual(migration.V2_SCHEMA, document["schema"])
        self.assertEqual(1, len(document["records"]))
        row = document["records"][0]
        self.assertEqual(original["id"], row["id"])
        self.assertEqual(original["module"], row["module"])
        self.assertEqual(original["responsibility"], row["responsibility"])
        self.assertEqual(original["name"], row["name"])
        self.assertEqual(original["source"], row["source"])
        self.assertEqual("interface", row["kind"])
        self.assertEqual("current", row["status"])
        self.assertEqual([], row["supersedes"])
        self.assertEqual("unverified", row["last_verified_version"])
        self.assertNotIn("legacy", row)
        self.assertNotIn("change_notes", row)
        self.assertNotIn("row_ordinal", row)
        self.assertTrue(any(item["type"] == "legacy" for item in row["evidence"]))
        self.assertTrue(any(
            item["type"] == "code" and item["path"] == "src.py"
            and item["sha256"] == row["source"]["sha256"]
            and item["commit"] == self.commit and item["tree"] == self.tree
            for item in row["evidence"]
        ))

        backup_shard = self.root / BACKUP / "shards/a.json"
        self.assertEqual(original_shard, backup_shard.read_bytes())
        self.assertEqual(self.original_homepage, (self.root / BACKUP / "ARCHITECTURE.md").read_bytes())
        audit = self.audit()
        self.assertEqual(0, audit["automatic_promotions"])
        self.assertEqual("current", audit["records"][0]["after_status"])
        self.assertEqual("preserved", audit["records"][0]["status_change"])
        archived = {item["field"]: item for item in audit["records"][0]["fields"]
                    if item["action"] == "archive"}
        self.assertEqual({"legacy", "change_notes", "row_ordinal"}, set(archived))
        self.assertEqual("legacy note", archived["change_notes"]["original"])
        self.assertEqual(digest(backup_shard), audit["inputs"][0]["sha256"])
        self.assertNotEqual(original_shard, shard.read_bytes())
        _, errors = graph.check(self.root)
        self.assertEqual([], errors)
        _, errors = graph.render_homepage(self.root, write=False)
        self.assertEqual([], errors)

    def test_source_drift_downgrades_and_never_rewrites_verified_commit(self):
        pending = self.row("a1.pending")
        self.put([self.current_row(), pending])
        original_commit = self.commit
        (self.root / "src.py").write_text("def run():\n    return 'drift'\n", encoding="utf-8")
        self.git("add", "src.py")
        self.git("commit", "-qm", "source drift")
        new_head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(original_commit, new_head)

        self.apply()
        current = self.record("a1.entry")
        self.assertEqual("needs_verification", current["status"])
        self.assertEqual(original_commit, current["verified_commit"])
        self.assertEqual("needs_verification", self.record("a1.pending")["status"])
        audit = self.audit()
        self.assertEqual(0, audit["automatic_promotions"])
        entry = next(item for item in audit["records"] if item["id"] == "a1.entry")
        self.assertEqual("downgraded", entry["status_change"])
        self.assertTrue(any("source" in reason for reason in entry["verification_reasons"]))
        self.assertEqual({"a1.entry", "a1.pending"},
                         {item["id"] for item in audit["pending_verification"]})
        self.assertEqual(2, len(self.output()["records"]))

    def test_multiple_sources_are_preserved_when_all_are_unmodified(self):
        auxiliary = {"path": "helper.py", "symbol": "assist",
                     "sha256": digest(self.root / "helper.py"), "line": 1}
        original = self.current_row(sources=[auxiliary])
        self.put([original])
        self.apply()
        row = self.record()
        self.assertEqual("current", row["status"])
        self.assertEqual([auxiliary], row["sources"])
        self.assertEqual(original["source"], row["source"])
        self.assertEqual([], graph.check(self.root)[1])

    def test_only_exact_source_duplicates_are_removed_not_distinct_symbols(self):
        primary = self.current_row()["source"]
        another_symbol = {"path": primary["path"], "symbol": "other",
                          "sha256": primary["sha256"]}
        auxiliary = {"path": "helper.py", "symbol": "assist",
                     "sha256": digest(self.root / "helper.py")}
        original = self.current_row(sources=[primary, another_symbol, auxiliary, auxiliary])
        self.put([original])
        self.apply()
        self.assertEqual([another_symbol, auxiliary], self.record()["sources"])
        self.assertEqual("current", self.record()["status"])
        self.assertEqual([], graph.check(self.root)[1])

    def test_auxiliary_source_drift_forces_downgrade(self):
        auxiliary = {"path": "helper.py", "symbol": "assist",
                     "sha256": digest(self.root / "helper.py")}
        self.put([self.current_row(sources=[auxiliary])])
        (self.root / "helper.py").write_text("def assist():\n    return False\n", encoding="utf-8")
        self.git("add", "helper.py")
        self.git("commit", "-qm", "auxiliary source drift")
        self.apply()
        self.assertEqual("needs_verification", self.record()["status"])
        self.assertEqual([auxiliary], self.record()["sources"])
        entry = self.audit()["records"][0]
        self.assertTrue(any("auxiliary source" in reason
                            for reason in entry["verification_reasons"]))

    def test_command_arguments_keep_typed_historical_test_evidence(self):
        row = self.current_row()
        row["evidence"] = [row["evidence"][0], row["evidence"][2],
                           "python3 tests/test_src.py::test_run --host browser"]
        self.put([row])
        self.apply()
        evidence = [item for item in self.record()["evidence"]
                    if item.get("path") == "tests/test_src.py" and item.get("type") == "test"]
        self.assertEqual(1, len(evidence))
        self.assertEqual(self.commit, evidence[0]["commit"])
        self.assertEqual(self.tree, evidence[0]["tree"])
        self.assertEqual(digest(self.root / "tests/test_src.py"), evidence[0]["sha256"])
        self.assertEqual("current", self.record()["status"])

    def test_legacy_record_links_existing_cutover_archive(self):
        archive = self.root / "docs/architecture/evidence/legacy-cutover-source.json"
        archive.parent.mkdir(parents=True)
        archive.write_text('{"legacy": true}\n', encoding="utf-8")
        self.put([self.row("legacy.one")])
        self.apply()
        evidence = [item for item in self.record("legacy.one")["evidence"]
                    if item.get("path") == "docs/architecture/evidence/legacy-cutover-source.json"]
        self.assertEqual(1, len(evidence))
        self.assertEqual(digest(archive), evidence[0]["sha256"])
        self.assertEqual("legacy", evidence[0]["type"])

    def test_homepage_evidence_uses_original_backup_not_stale_generated_hash(self):
        original = self.current_row()
        original["evidence"].append("docs/ARCHITECTURE.md")
        original["evidence"].append({"type": "other", "path": "docs/ARCHITECTURE.md",
                                     "sha256": hashlib.sha256(self.original_homepage).hexdigest()})
        self.put([original])
        self.apply()
        row = self.record()
        self.assertTrue(all(item.get("path") != "docs/ARCHITECTURE.md" for item in row["evidence"]))
        self.assertEqual([], graph.check(self.root)[1])
        reasons = [item.get("reason", "") for item in self.audit()["records"][0]["evidence"]]
        self.assertTrue(any("homepage" in reason for reason in reasons))

    def test_string_scalar_missing_and_compound_evidence_are_traceable(self):
        rich = self.row("a1.evidence", evidence=[
            "src.py", "tests/test_src.py", "docs/spec.md", "data.bin",
            "src.py:run", "pytest -q", "docs/spec.md#run", 7,
        ])
        missing = self.row("a1.missing", status="legacy")
        missing.pop("evidence")
        missing.pop("supersedes")
        self.put([rich, missing])
        self.apply()

        row = self.record("a1.evidence")
        self.assertEqual(["code", "test", "spec", "other"],
                         [item["type"] for item in row["evidence"][:4]])
        for item, path in zip(row["evidence"][:4],
                              ["src.py", "tests/test_src.py", "docs/spec.md", "data.bin"]):
            self.assertEqual(path, item["path"])
            self.assertEqual(digest(self.root / path), item["sha256"])
        for item in row["evidence"][4:8]:
            self.assertNotIn("path", item)
            self.assertTrue(item["uri"].startswith("urn:pandu:legacy-evidence:"))
        self.assertEqual("needs_verification", self.record("a1.missing")["status"])
        self.assertIsNone(self.record("a1.missing")["last_verified"])
        self.assertEqual([], self.record("a1.missing")["supersedes"])
        self.assertEqual(1, len(self.record("a1.missing")["evidence"]))
        self.assertEqual("legacy", self.record("a1.missing")["evidence"][0]["type"])

        entry = next(item for item in self.audit()["records"] if item["id"] == "a1.evidence")
        originals = [item["original"] for item in entry["evidence"]]
        for raw in ("src.py:run", "pytest -q", "docs/spec.md#run", 7):
            self.assertIn(raw, originals)
        self.assertTrue(all(item["action"] in {"preserve", "map", "archive"}
                            for item in entry["evidence"]))

    def test_unverifiable_added_evidence_downgrades_current(self):
        self.put([self.current_row(evidence=["src.py", "src.py:run", "pytest -q"])])
        self.apply()
        self.assertEqual("needs_verification", self.record()["status"])
        reasons = self.audit()["records"][0]["verification_reasons"]
        self.assertTrue(any("evidence" in reason for reason in reasons))

    def test_relations_are_preserved_mapped_reversed_deduplicated_and_archived(self):
        target_ids = [
            migration.FIXED_TARGET, "a1.target", "a1.uses", "a1.candidate",
            "a1.observed", "a1.input",
        ]
        records = [
            self.row("module.deployment", kind="module", name="deployment"),
            self.row("a1.child", relations=[
                {"type": "calls", "target": migration.TYPO_TARGET},
                {"kind": "extends", "target": migration.TYPO_TARGET},
                {"kind": "consumes", "target": "a1.target"},
                {"type": "part_of", "target": "module.deployment"},
                {"type": "belongs_to", "target": "module.deployment"},
                {"type": "indexes_legacy_candidate", "target": "a1.target"},
                {"type": "supports", "target": "a1.uses"},
                {"type": "uses", "target": "a1.uses"},
                {"type": "candidate_extension", "target": "a1.candidate"},
                {"type": "observes", "target": "a1.observed"},
                {"type": "provides_input_to", "target": "a1.input"},
            ]),
        ] + [self.row(ident) for ident in target_ids]
        original_ids = [row["id"] for row in records]
        self.put(records)
        self.apply()

        document = self.output()
        self.assertEqual(len(records), len(document["records"]))
        self.assertEqual(set(original_ids), {row["id"] for row in document["records"]})
        child = self.record("a1.child")
        self.assertIn({"type": "calls", "target": migration.FIXED_TARGET}, child["relations"])
        self.assertIn({"type": "extends", "target": migration.FIXED_TARGET}, child["relations"])
        self.assertIn({"type": "consumes", "target": "a1.target"}, child["relations"])
        self.assertEqual(1, child["relations"].count({"type": "uses", "target": "a1.uses"}))
        self.assertFalse(any(item["type"] in migration.ARCHIVE_RELATIONS | migration.REVERSE_RELATIONS
                             for item in child["relations"]))
        parent = self.record("module.deployment")
        self.assertEqual(1, parent["relations"].count({"type": "contains", "target": "a1.child"}))

        entry = next(item for item in self.audit()["records"] if item["id"] == "a1.child")
        dispositions = entry["relations"]
        self.assertEqual(len(records[1]["relations"]), len(dispositions))
        self.assertTrue(all(item["action"] in {"preserve", "map", "archive"}
                            for item in dispositions))
        self.assertEqual(1, sum(item["action"] == "preserve" for item in dispositions))
        self.assertEqual({"archive"}, {
            item["action"] for item in dispositions
            if item["original"].get("type") in migration.ARCHIVE_RELATIONS
        })
        reverse = [item for item in dispositions
                   if item["original"].get("type") in migration.REVERSE_RELATIONS]
        self.assertTrue(all(item["action"] == "map" and item["owner_id"] == "module.deployment"
                            for item in reverse))
        self.assertEqual(1, sum(item.get("deduplicated", False) for item in reverse))

    def test_unknown_or_damaged_relation_shapes_fail_without_writes(self):
        cases = [
            [{"type": "mystery", "target": "a1.entry"}],
            [{"type": "calls", "target": "a1.entry", "extra": True}],
            [{"target": "a1.entry"}],
            None,
        ]
        for relations in cases:
            with self.subTest(relations=relations):
                shard = self.put([self.row(relations=relations)])
                original = shard.read_bytes()
                with self.assertRaises(migration.MigrationError):
                    migration.migrate(self.root, apply=True, date=DATE)
                self.assertEqual(original, shard.read_bytes())
                self.assertFalse((self.root / BACKUP).exists())

    def test_staging_check_failure_does_not_touch_target(self):
        shard = self.put([self.current_row()])
        original = shard.read_bytes()
        with mock.patch.object(graph, "check", return_value=([], ["injected check failure"])):
            with self.assertRaisesRegex(migration.MigrationError, "injected check failure"):
                migration.migrate(self.root, apply=True, date=DATE)
        self.assertEqual(original, shard.read_bytes())
        self.assertEqual(self.original_homepage, (self.root / "docs/ARCHITECTURE.md").read_bytes())
        self.assertFalse((self.root / BACKUP).exists())

    def test_publish_failure_rolls_back_every_attempted_write(self):
        shard = self.put([self.current_row()])
        original_shard = shard.read_bytes()
        real_atomic_write = state_store.atomic_write

        def fail_after_homepage_write(path, data, mode=0o600):
            target = Path(path)
            if target == (self.root / "docs/ARCHITECTURE.md").resolve():
                real_atomic_write(path, data, mode=mode)
                raise OSError("injected publish failure")
            return real_atomic_write(path, data, mode=mode)

        with mock.patch.object(state_store, "atomic_write", side_effect=fail_after_homepage_write):
            with self.assertRaisesRegex(migration.MigrationError, "rolled back"):
                migration.migrate(self.root, apply=True, date=DATE)
        self.assertEqual(original_shard, shard.read_bytes())
        self.assertEqual(self.original_homepage, (self.root / "docs/ARCHITECTURE.md").read_bytes())
        self.assertFalse((self.root / BACKUP).exists())

    def test_repeated_apply_is_zero_diff(self):
        self.put([self.current_row()])
        self.apply()
        paths = sorted(path for path in (self.root / "docs").rglob("*") if path.is_file())
        before = {path.relative_to(self.root).as_posix(): digest(path) for path in paths}
        result = migration.migrate(self.root, apply=True, date=DATE)
        after_paths = sorted(path for path in (self.root / "docs").rglob("*") if path.is_file())
        after = {path.relative_to(self.root).as_posix(): digest(path) for path in after_paths}
        self.assertTrue(result["already_migrated"])
        self.assertFalse(result["changed"])
        self.assertEqual(before, after)

    def test_isolated_cli_defaults_to_dry_run_and_requires_repo(self):
        shard = self.put([self.current_row()])
        original = shard.read_bytes()
        command = [sys.executable, "-I", str(SCRIPT / "architecture_migrate.py"),
                   "--repo", str(self.root), "--date", DATE]
        first = subprocess.run(command, capture_output=True, text=True)
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual("dry-run", payload["mode"])
        self.assertFalse(payload["applied"])
        self.assertEqual(original, shard.read_bytes())
        self.assertFalse((self.root / BACKUP).exists())
        missing = subprocess.run(
            [sys.executable, "-I", str(SCRIPT / "architecture_migrate.py")],
            capture_output=True, text=True,
        )
        self.assertEqual(2, missing.returncode)
        self.assertIn("--repo", missing.stderr)


if __name__ == "__main__":
    unittest.main()
