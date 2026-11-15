#!/usr/bin/env python3
"""Manifest-linked generation GC, budgets, and protected salvage collection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import artifact_gc as gc


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ArtifactGCTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True, capture_output=True)
        (self.root / "dev").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, generation, status, supersedes=None, artifact=True, subject="mod/slug#issue-001"):
        directory = self.root / "dev" / "features" / "mod" / "slug" / "impl" / "issue-001" / "raw" / generation
        directory.mkdir(parents=True, exist_ok=True)
        artifacts = []
        files = {
            "capture.txt": ("screenshot", generation + "\n"),
            "verification-implementer.json": ("verification", '{"generation": "%s"}\n' % generation),
            "evidence-issue-001.md": ("evidence", "evidence %s\n" % generation),
            "raw/run.log": ("raw", "log %s\n" % generation),
        }
        if artifact:
            for name, (kind, content) in files.items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                artifacts.append({"path": name, "kind": kind,
                                  "sha256": sha(path), "size": path.stat().st_size})
        document = {"schema": gc.SCHEMA, "subject": subject, "generation": generation,
                    "candidate_tree": hashlib.sha1(("tree-" + generation).encode()).hexdigest(),
                    "status": status, "artifacts": artifacts}
        if supersedes:
            document["supersedes"] = supersedes
        path = directory / gc.MANIFEST_NAME
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_only_older_than_current_and_last_success_is_collected(self):
        self.manifest("g1", "superseded")
        self.manifest("g2", "superseded", "g1")
        self.manifest("g3", "current", "g2")
        preview = gc.authorize_gc(gc.preview(self.root))
        import json_schema
        for record in gc.discover_generations(self.root):
            self.assertEqual(json_schema.load_validate(SCRIPT_DIR.parent / "references/artifact-generation.schema.json", record["manifest"]), [])
        self.assertEqual({item["generation"] for item in preview["candidates"]}, {"g1"})
        result = gc.apply_gc(preview)
        self.assertTrue(result["ok"], result)
        self.assertFalse((self.root / "dev/features/mod/slug/impl/issue-001/raw/g1/capture.txt").exists())
        self.assertTrue((self.root / "dev/features/mod/slug/impl/issue-001/raw/g2/capture.txt").is_file())
        self.assertTrue((self.root / "dev/features/mod/slug/impl/issue-001/raw/g3/capture.txt").is_file())
        tombstone = gc.load_manifest(self.root / "dev/features/mod/slug/impl/issue-001/raw/g1" / gc.MANIFEST_NAME)
        self.assertEqual(tombstone["artifacts"], [])

    def test_continuous_full_run_generations_gc_without_impl_root_manifest(self):
        impl = self.root / "dev/features/mod/slug/impl/issue-001"
        self.manifest("g1", "superseded")
        self.manifest("g2", "superseded", "g1")
        self.manifest("g3", "current", "g2")
        for retired, previous, current in (("g1", "g3", "g4"), ("g2", "g4", "g5")):
            plan = gc.authorize_gc(gc.preview(impl))
            self.assertTrue(all(item["generation"] == retired for item in plan["candidates"]), plan)
            result = gc.apply_gc(plan)
            self.assertTrue(result["ok"], result)
            for name in ("capture.txt", "verification-implementer.json", "evidence-issue-001.md", "raw/run.log"):
                self.assertFalse((impl / "raw" / retired / name).exists())
            previous_manifest = impl / "raw" / previous / gc.MANIFEST_NAME
            document = json.loads(previous_manifest.read_text())
            document["status"] = "superseded"
            previous_manifest.write_text(json.dumps(document))
            self.manifest(current, "current", previous)
            records = gc.discover_generations(impl)
            self.assertEqual([r["manifest"]["generation"] for r in records
                              if r["manifest"]["status"] == "current"], [current])
            self.assertFalse((impl / gc.MANIFEST_NAME).exists())
        # Every retired manifest remains as an empty tombstone, so later chains
        # remain provable without keeping self-contained evidence/log payloads.
        for generation in ("g1", "g2"):
            self.assertEqual(gc.load_manifest(impl / "raw" / generation / gc.MANIFEST_NAME)["artifacts"], [])

    def test_no_manifest_or_broken_chain_never_uses_mtime(self):
        old = self.root / "old.log"
        old.write_text("old")
        os.utime(old, (1, 1))
        self.assertEqual(gc.preview(self.root)["candidates"], [])
        self.manifest("g1", "superseded")
        self.manifest("g2", "current", "missing-generation")
        plan = gc.preview(self.root)
        self.assertEqual(plan["candidates"], [])
        self.assertTrue(plan["warnings"])

    def test_reference_switch_and_running_active_artifacts_are_protected(self):
        self.manifest("g1", "superseded")
        self.manifest("g2", "superseded", "g1")
        current = self.manifest("g3", "current", "g2")
        plan = gc.preview(self.root, references={"mod/slug#issue-001": "g2"})
        self.assertEqual(plan["candidates"], [])
        running = self.manifest("g4", "running", None)
        # A running generation has no collectible status even if it is old.
        self.assertIn(str(running.resolve()), gc.preview(self.root)["protected"])

    def test_apply_requires_confirmation_and_rehashes(self):
        self.manifest("g1", "superseded")
        self.manifest("g2", "superseded", "g1")
        self.manifest("g3", "current", "g2")
        raw = gc.preview(self.root)
        with self.assertRaises(gc.ArtifactGCError):
            gc.apply_gc(raw)
        authorized = gc.authorize_gc(raw)
        target = Path(authorized["candidates"][0]["abs"])
        target.write_text("changed")
        with self.assertRaises(gc.ArtifactGCError):
            gc.apply_gc(authorized)
        self.assertTrue(target.is_file())

    def test_salvage_staging_active_and_unique_recovery_are_protected(self):
        salvage = self.root / "dev" / ".salvage"
        staging = salvage / ".staging" / "op"
        staging.mkdir(parents=True)
        unique = salvage / "issue-001-op"
        unique.mkdir()
        (unique / "_manifest.json").write_text(json.dumps({
            "operation_id": "op", "created_at": "2020-01-01T00:00:00Z",
            "branch": "feat/deleted", "branch_patch": {"file": "branch.patch"}}))
        expired = salvage / "issue-002-old"
        expired.mkdir()
        (expired / "x.txt").write_text("x")
        (expired / "_manifest.json").write_text(json.dumps({
            "operation_id": "old", "created_at": "2020-01-01T00:00:00Z"}))
        preview = gc.authorize_gc(gc.preview_salvage(self.root, retention_days=30))
        self.assertEqual([Path(item["path"]).name for item in preview["candidates"]], ["issue-002-old"])
        result = gc.apply_gc(preview)
        self.assertTrue(result["ok"], result)
        self.assertFalse(expired.exists())
        self.assertTrue(staging.exists())
        self.assertTrue(unique.exists())

    def authorized_generation(self):
        self.manifest("g1", "superseded")
        self.manifest("g2", "superseded", "g1")
        self.manifest("g3", "current", "g2")
        return gc.authorize_gc(gc.preview(self.root))

    def test_running_gc_recovers_after_unlink_without_new_preview(self):
        plan = self.authorized_generation()
        target = Path(plan["candidates"][0]["abs"])
        original = Path.unlink
        def crash(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if Path(path) == target:
                raise OSError("crash after unlink")
        with mock.patch.object(Path, "unlink", autospec=True, side_effect=crash):
            with self.assertRaises(OSError):
                gc.apply_gc(plan)
        with mock.patch.object(gc, "preview_generations", side_effect=AssertionError("must not preview")):
            result = gc.apply_gc(plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["operation_id"], plan["operation_id"])
        self.assertFalse(target.exists())

    def test_running_gc_recovers_after_manifest_update(self):
        plan = self.authorized_generation()
        manifest_path = Path(plan["candidates"][0]["manifest_path"])
        original = gc.atomic_json_write
        def crash(path, value, *args, **kwargs):
            original(path, value, *args, **kwargs)
            if Path(path) == manifest_path:
                raise OSError("crash after manifest write")
        with mock.patch.object(gc, "atomic_json_write", side_effect=crash):
            with self.assertRaises(OSError):
                gc.apply_gc(plan)
        with mock.patch.object(gc, "preview_generations", side_effect=AssertionError("must not preview")):
            result = gc.apply_gc(plan)
        self.assertTrue(result["ok"], result)
        self.assertEqual(gc.load_manifest(manifest_path)["artifacts"], [])

    def test_running_salvage_gc_recovers_after_directory_delete(self):
        salvage = self.root / "dev" / ".salvage" / "old"
        salvage.mkdir(parents=True)
        (salvage / "x").write_text("x")
        (salvage / "_manifest.json").write_text(json.dumps({
            "operation_id": "old", "created_at": "2020-01-01T00:00:00Z"}))
        plan = gc.authorize_gc(gc.preview_salvage(self.root, retention_days=30))
        original = gc.shutil.rmtree
        def crash(path, *args, **kwargs):
            original(path, *args, **kwargs)
            raise OSError("crash after rmtree")
        with mock.patch.object(gc.shutil, "rmtree", side_effect=crash):
            with self.assertRaises(OSError):
                gc.apply_gc(plan)
        with mock.patch.object(gc, "preview_salvage", side_effect=AssertionError("must not preview")):
            result = gc.apply_gc(plan)
        self.assertTrue(result["ok"], result)
        self.assertFalse(salvage.exists())

    def test_budget_stats_are_reusable(self):
        impl = self.root / "impl"; impl.mkdir()
        (impl / "a.txt").write_bytes(b"1234")
        result = gc.measure_budget([impl], {"impl-dir-max-mb": 1})
        self.assertEqual(result["bytes"], 4)
        self.assertTrue(result["ok"])
        result = gc.measure_budget([impl], {"impl-dir-max-mb": 0})
        self.assertFalse(result["ok"])

    def test_generation_schema_is_valid_json(self):
        for name in ("artifact-generation.schema.json", "cleanup-operation.schema.json", "integration-runtime.schema.json"):
            value = json.loads((SCRIPT_DIR.parent / "references" / name).read_text())
            self.assertEqual(value["type"], "object")


if __name__ == "__main__":
    unittest.main()
