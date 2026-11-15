#!/usr/bin/env python3
"""Safely migrate pandu.architecture-graph/v1 JSON shards to v2.

The default is a deterministic dry-run.  ``--apply`` writes only after a complete
staging repository passes the v2 JSON schema, architecture_graph.check and the
homepage byte renderer.  Original shards and the original homepage are kept in a
nested evidence directory, together with a deterministic audit.json.

Only the Python standard library and the sibling architecture modules are used.
Sibling imports are lazy so ``python3 -I architecture_migrate.py`` works.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

GRAPH_REL = Path("docs") / "architecture"
HOMEPAGE_REL = Path("docs") / "ARCHITECTURE.md"
EVIDENCE_REL = GRAPH_REL / "evidence"
V1_SCHEMA = "pandu.architecture-graph/v1"
V2_SCHEMA = "pandu.architecture-graph/v2"
AUDIT_SCHEMA = "pandu.architecture-v1-v2-migration/v1"
RESULT_SCHEMA = "pandu.architecture-v1-v2-migration-result/v1"
TYPO_TARGET = "a1.window.route.bindings.v1"
FIXED_TARGET = "a1.window-route-bindings.v1"
QUERY_RECONCILIATION_BASELINE_DATE = "2026-10-22"
QUERY_RECONCILIATION_BASELINE_FIELDS = {
    "expected", "observed_definition", "observed_query_rows",
    "separately_tracked_query_pending",
}

ARCHIVE_RECORD_FIELDS = {"legacy", "change_notes", "row_ordinal"}
RECORD_FIELDS = {
    "id", "module", "responsibility", "kind", "name", "status", "version",
    "applies_to", "source", "sources", "verification", "evidence",
    "last_verified", "last_verified_version", "verified_commit", "relations",
    "supersedes",
} | ARCHIVE_RECORD_FIELDS
SOURCE_FIELDS = {"path", "symbol", "sha256", "line"}
EVIDENCE_FIELDS = {"type", "path", "uri", "sha256", "commit", "tree"}
ARCHIVE_RELATIONS = {
    "indexes_legacy_candidate", "supports", "candidate_extension", "observes",
    "provides_input_to",
}
REVERSE_RELATIONS = {"part_of", "belongs_to"}
KIND_RELATIONS = {"extends", "consumes"}
URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CODE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c",
    ".h", ".cc", ".cpp", ".cs", ".rb", ".php", ".swift", ".kt", ".kts",
    ".scala", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql", ".vue",
    ".svelte", ".html", ".css", ".scss",
}


class MigrationError(ValueError):
    """The input cannot be migrated without guessing or losing information."""


def _dependencies():
    # These imports intentionally remain lazy.  Besides making query failures
    # clearer, this mirrors architecture_graph's isolated-script import model.
    import architecture_graph as graph
    import json_schema
    import state_store
    return graph, json_schema, state_store


def _json_bytes(value):
    graph, _, _ = _dependencies()
    return graph._json_bytes(value)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _json_no_duplicates(raw, label):
    def pairs_hook(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise MigrationError("%s: duplicate JSON key: %s" % (label, key))
            value[key] = item
        return value

    def bad_constant(value):
        raise MigrationError("%s: non-finite JSON number: %s" % (label, value))

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook,
                          parse_constant=bad_constant)
    except MigrationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("%s: invalid JSON: %s" % (label, exc)) from exc


def _valid_date(value):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _date_or_today(value):
    value = value or datetime.date.today().isoformat()
    if not _valid_date(value):
        raise MigrationError("--date must be a real YYYY-MM-DD date")
    return value


def _relative_path(repo, path):
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise MigrationError("path is outside repository: %s" % path) from exc


def _backup_path(repo, backup_dir, migration_date):
    graph, _, _ = _dependencies()
    candidate = Path(backup_dir) if backup_dir is not None else (
        EVIDENCE_REL / ("v1-v2-migration-%s" % migration_date.replace("-", ""))
    )
    if not candidate.is_absolute():
        candidate = repo / candidate
    relative = _relative_path(repo, candidate)
    if graph._safe_repo_path(relative) is None:
        raise MigrationError("backup directory must be a canonical repository-relative path")
    path = PurePosixPath(relative)
    try:
        path.relative_to(EVIDENCE_REL.as_posix())
    except ValueError as exc:
        raise MigrationError(
            "backup directory must be below docs/architecture/evidence/") from exc
    if path.as_posix() == EVIDENCE_REL.as_posix():
        raise MigrationError("backup directory must be a dedicated evidence subdirectory")
    return repo / relative, Path(relative)


def _read_inputs(repo):
    folder = repo / GRAPH_REL
    if not folder.is_dir():
        raise MigrationError("architecture shard directory is missing: %s" % GRAPH_REL)
    paths = sorted(folder.glob("*.json"), key=lambda path: path.name)
    if not paths:
        raise MigrationError("no top-level architecture JSON shards found")
    documents = []
    originals = {}
    schemas = set()
    for path in paths:
        if not path.is_file():
            raise MigrationError("shard is not a regular file: %s" % path.name)
        relative = path.relative_to(repo).as_posix()
        raw = path.read_bytes()
        document = _json_no_duplicates(raw, relative)
        if not isinstance(document, dict) or not isinstance(document.get("schema"), str):
            raise MigrationError("%s: shard must be an object with a schema" % relative)
        if not isinstance(document.get("records"), list):
            raise MigrationError("%s: shard records must be an array" % relative)
        schemas.add(document["schema"])
        originals[relative] = raw
        documents.append((relative, document))
    homepage = repo / HOMEPAGE_REL
    if not homepage.is_file():
        raise MigrationError("original homepage is required for byte backup: %s" % HOMEPAGE_REL)
    originals[HOMEPAGE_REL.as_posix()] = homepage.read_bytes()
    return documents, originals, schemas


def _opaque_evidence(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {"type": "other", "uri": "urn:pandu:legacy-evidence:" + _sha256(raw)}


def _classify_path(path):
    pure = PurePosixPath(path)
    lowered_parts = {part.lower() for part in pure.parts}
    name = pure.name.lower()
    suffix = pure.suffix.lower()
    if ({"test", "tests", "testing"} & lowered_parts or name.startswith("test_")
            or name.endswith("_test" + suffix) or ".test." in name):
        return "test"
    if ({"doc", "docs", "spec", "specs", "specification", "specifications"}
            & lowered_parts or suffix in {".md", ".rst", ".adoc"}
            or "spec" in name):
        return "spec"
    if suffix in CODE_SUFFIXES:
        return "code"
    return "other"


def _plain_file_reference(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    # These are commonly fragments, symbols, command lines or compound legacy
    # references.  They remain archived verbatim rather than becoming fake paths.
    if any(char.isspace() for char in value):
        return False
    if any(token in value for token in ("#", ":", "::", ",", ";", "|", "&", "<", ">",
                                        "`", "$", "\"", "'", "*", "?", "[", "]")):
        return False
    return not value.startswith("-")


class _Plan:
    def __init__(self, repo, documents, originals, migration_date, backup_rel, head):
        self.repo = repo
        self.documents = documents
        self.originals = originals
        self.date = migration_date
        self.backup_rel = backup_rel
        self.head = head
        self.entries = []
        self.by_id = {}
        self.evidence_meta = {}
        self.document_dispositions = []
        self.output_documents = {}
        self.output_bytes = {}
        self.audit = {
            "schema": AUDIT_SCHEMA,
            "migration_date": migration_date,
            "backup_dir": backup_rel.as_posix(),
            "head": head,
            "record_count": 0,
            "automatic_promotions": 0,
            "inputs": [],
            "outputs": [],
            "documents": self.document_dispositions,
            "records": [],
            "pending_verification": [],
        }

    def _field(self, audit, field, action, original, output=None, note=None):
        item = {"field": field, "action": action, "original": copy.deepcopy(original)}
        if output is not None:
            item["output"] = copy.deepcopy(output)
        if note:
            item["note"] = note
        audit["fields"].append(item)

    def _normalize_source(self, audit, source, ident, label):
        """Remove only legacy source annotations with explicit audit provenance."""
        if not isinstance(source, dict):
            return copy.deepcopy(source)
        normalized = copy.deepcopy(source)
        if "row_ordinal" in normalized:
            original = normalized.pop("row_ordinal")
            self._field(
                audit, label + ".row_ordinal", "archive", original,
                note="source-local v1 row ordinal is not v2 source provenance; original archived",
            )
        symbol = normalized.get("symbol")
        if isinstance(symbol, str) and not symbol.strip():
            normalized.pop("symbol")
            self._field(
                audit, label + ".symbol", "map", symbol,
                note="empty optional source.symbol removed; no symbol was inferred",
            )
        return normalized

    def _source_array(self, audit, value, ident, primary=None):
        if not isinstance(value, list):
            raise MigrationError("%s: sources must be an array" % ident)
        result = []
        seen = set()
        primary_binding = None
        if isinstance(primary, dict):
            primary_binding = (primary.get("path"), primary.get("symbol"), primary.get("sha256"))
        for index, source in enumerate(value):
            if (not isinstance(source, dict)
                    or set(source) - SOURCE_FIELDS - {"row_ordinal"}):
                raise MigrationError("%s: malformed auxiliary source at index %d" % (ident, index))
            if not isinstance(source.get("path"), str) or not source["path"]:
                raise MigrationError("%s: auxiliary source %d requires path" % (ident, index))
            normalized = self._normalize_source(
                audit, source, ident, "sources[%d]" % index)
            binding = (normalized.get("path"), normalized.get("symbol"), normalized.get("sha256"))
            if primary_binding is not None and binding == primary_binding:
                self._field(audit, "sources[%d]" % index, "map", source,
                            note="auxiliary binding duplicates the primary source; removed without losing a distinct symbol")
                continue
            identity = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if identity in seen:
                self._field(audit, "sources[%d]" % index, "map", source,
                            note="byte-identical auxiliary source binding deduplicated; distinct symbols are preserved")
                continue
            seen.add(identity)
            result.append(normalized)
        return result

    def prepare(self):
        for relative, document in self.documents:
            unknown = set(document) - {"schema", "module", "purpose", "baseline", "records"}
            if unknown:
                # Top-level extensions are retained in the audit instead of being
                # silently discarded by the stricter v2 document shape.
                self.document_dispositions.append({
                    "shard": relative, "action": "archive",
                    "fields": {key: copy.deepcopy(document[key]) for key in sorted(unknown)},
                })
            for index, original in enumerate(document["records"]):
                if not isinstance(original, dict):
                    raise MigrationError("%s: record %d is not an object" % (relative, index))
                unknown_fields = set(original) - RECORD_FIELDS
                if unknown_fields:
                    raise MigrationError("%s: record %d has unknown fields: %s" % (
                        relative, index, ", ".join(sorted(unknown_fields))))
                ident = original.get("id")
                if not isinstance(ident, str) or not ident:
                    raise MigrationError("%s: record %d has no stable id" % (relative, index))
                if ident in self.by_id:
                    raise MigrationError("duplicate stable id across v1 shards: %s" % ident)
                status = original.get("status")
                if not isinstance(status, str) or status not in {
                        "current", "release_only", "superseded", "retired",
                        "needs_verification", "legacy"}:
                    raise MigrationError("%s: unknown v1 status: %r" % (ident, status))
                audit = {
                    "id": ident, "shard": relative, "record_index": index,
                    "before_status": status, "after_status": None,
                    "status_change": None, "fields": [], "relations": [],
                    "evidence": [], "verification_reasons": [],
                }
                entry = {"original": original, "audit": audit, "output": None,
                         "shard": relative, "index": index}
                self.entries.append(entry)
                self.by_id[ident] = entry

        for entry in self.entries:
            self._transform_record(entry)
        for entry in self.entries:
            self._transform_relations(entry)
        for entry in self.entries:
            output = entry["output"]
            output["relations"] = list({
                json.dumps(item, sort_keys=True): item for item in output["relations"]
            }.values())
        for entry in self.entries:
            self._retain_or_downgrade(entry)
        self._build_documents()
        self._build_audit()
        self.audit["record_count"] = len(self.entries)
        if len(self.audit["records"]) != len(self.entries):
            raise AssertionError("record audit count mismatch")
        return self

    def _transform_record(self, entry):
        original, audit = entry["original"], entry["audit"]
        ident = audit["id"]
        output = {}
        entry["output"] = output
        for field in ("id", "module", "responsibility", "name", "version", "applies_to",
                      "verification", "verified_commit"):
            if field in original:
                output[field] = copy.deepcopy(original[field])
                self._field(audit, field, "preserve", original[field], output[field])
        if "source" in original:
            output["source"] = self._normalize_source(
                audit, original["source"], ident, "source")
            self._field(
                audit, "source",
                "map" if output["source"] != original["source"] else "preserve",
                original["source"], output["source"],
                "source-local legacy fields normalized" if output["source"] != original["source"] else None,
            )
        output["verified_commit"] = copy.deepcopy(original.get("verified_commit"))
        if "verified_commit" not in original:
            self._field(audit, "verified_commit", "map", None, None,
                        "missing verified_commit normalized to null")

        kind = original.get("kind")
        if kind == "legacy_query_interface":
            output["kind"] = "interface"
            self._field(audit, "kind", "map", kind, "interface",
                        "legacy query kind normalized without promoting status")
        else:
            output["kind"] = copy.deepcopy(kind)
            self._field(audit, "kind", "preserve", kind, kind)
        output["status"] = "needs_verification" if original["status"] == "legacy" else original["status"]
        self._field(audit, "status", "map" if original["status"] == "legacy" else "preserve",
                    original["status"], output["status"],
                    "legacy status requires fresh verification" if original["status"] == "legacy" else None)

        if "sources" in original:
            output["sources"] = self._source_array(
                audit, original["sources"], ident, primary=output.get("source"))
            self._field(
                audit, "sources",
                "map" if output["sources"] != original["sources"] else "preserve",
                original["sources"], output["sources"],
            )

        supersedes = original.get("supersedes")
        if supersedes is None:
            supersedes = []
        if not isinstance(supersedes, list) or not all(isinstance(item, str) for item in supersedes):
            raise MigrationError("%s: supersedes must be null or an array of ids" % ident)
        normalized = []
        for target in supersedes:
            normalized.append(self._fix_target(target))
        output["supersedes"] = list(dict.fromkeys(normalized))
        self._field(audit, "supersedes", "map" if output["supersedes"] != original.get("supersedes") else "preserve",
                    original.get("supersedes"), output["supersedes"],
                    "null/missing normalized and duplicate references removed")

        last_verified = original.get("last_verified")
        if last_verified is None or (isinstance(last_verified, str)
                                     and last_verified.strip().lower() in {"", "unknown"}):
            output["last_verified"] = None
        else:
            output["last_verified"] = copy.deepcopy(last_verified)
        self._field(audit, "last_verified",
                    "map" if output["last_verified"] != original.get("last_verified") else "preserve",
                    original.get("last_verified"), output["last_verified"],
                    "unknown/missing normalized to null")
        last_version = original.get("last_verified_version")
        if last_version is None or (isinstance(last_version, str) and not last_version.strip()):
            output["last_verified_version"] = "unverified"
        else:
            output["last_verified_version"] = copy.deepcopy(last_version)
        self._field(audit, "last_verified_version",
                    "map" if output["last_verified_version"] != original.get("last_verified_version") else "preserve",
                    original.get("last_verified_version"), output["last_verified_version"],
                    "empty/missing normalized to unverified")

        for field in sorted(ARCHIVE_RECORD_FIELDS & set(original)):
            self._field(audit, field, "archive", original[field], note="v1-only field archived")
        self._transform_evidence(entry)
        # Relations are populated in a second pass so reverse contains edges can
        # target records from any shard.
        output["relations"] = []
        if "relations" not in original or not isinstance(original["relations"], list):
            raise MigrationError("%s: relations must be an array" % ident)
        self._field(audit, "relations", "map", original["relations"], note="see per-relation dispositions")

    def _legacy_evidence(self, entry):
        relative = entry["shard"]
        return {
            "type": "legacy",
            "path": (self.backup_rel / "shards" / PurePosixPath(relative).name).as_posix(),
            "sha256": _sha256(self.originals[relative]),
        }

    def _evidence_disposition(self, entry, index, action, original, output=None, reason=None):
        item = {"index": index, "action": action, "original": copy.deepcopy(original)}
        if output is not None:
            item["output"] = copy.deepcopy(output)
        if reason:
            item["reason"] = reason
        entry["audit"]["evidence"].append(item)

    def _convert_path_evidence(self, item):
        graph, _, _ = _dependencies()
        path = item.get("path")
        if not isinstance(path, str) or graph._safe_repo_path(path) is None or "#" in path:
            return None, "unsafe or fragment-bearing evidence path"
        commit, tree = item.get("commit"), item.get("tree")
        reference = commit or tree
        if commit is not None:
            if not isinstance(commit, str) or graph._resolve_commit(self.repo, commit) != commit:
                return None, "evidence commit is not resolvable"
            if tree is not None and graph._resolve_tree(self.repo, commit) != tree:
                return None, "evidence tree does not match evidence commit"
        elif tree is not None:
            if not isinstance(tree, str) or graph._resolve_tree(self.repo, tree) != tree:
                return None, "evidence tree is not resolvable"
        if reference is not None:
            data = graph._read_git_object(self.repo, reference, path)
        else:
            target = graph._working_file(self.repo, path)
            if target is None:
                return None, "evidence is not a real repository file"
            try:
                data = target.read_bytes()
            except OSError as exc:
                return None, "evidence cannot be read: %s" % exc
        if data is None:
            return None, "evidence path is missing at its Git reference"
        actual = graph._sha256(data)
        expected = item.get("sha256")
        if expected is not None and (not isinstance(expected, str)
                                     or not SHA256_RE.fullmatch(expected) or expected != actual):
            return None, "evidence sha256 does not match the referenced bytes"
        converted = copy.deepcopy(item)
        converted["sha256"] = actual
        converted.setdefault("type", _classify_path(path))
        return converted, None

    def _command_test_evidence(self, entry, raw):
        """Preserve real test-file identities from legacy command strings."""
        if not isinstance(raw, str) or not raw.strip():
            return []
        try:
            tokens = shlex.split(raw)
        except ValueError:
            return []
        graph, _, _ = _dependencies()
        paths = []
        for token in tokens:
            candidate = token.split("::", 1)[0]
            if not candidate or candidate.startswith("-") or graph._safe_repo_path(candidate) is None:
                continue
            if graph._working_file(self.repo, candidate) is None:
                continue
            if _classify_path(candidate) == "test" and candidate not in paths:
                paths.append(candidate)
        if not paths:
            return []
        output = entry["output"]
        commit = output.get("verified_commit")
        tree = graph._resolve_tree(self.repo, commit) if isinstance(commit, str) and commit else None
        converted = []
        for path in paths:
            item = {"type": "test", "path": path}
            if commit and tree:
                item.update(commit=commit, tree=tree)
            evidence, failure = self._convert_path_evidence(item)
            if failure:
                return []
            converted.append(evidence)
        return converted

    def _transform_evidence(self, entry):
        original, output, audit = entry["original"], entry["output"], entry["audit"]
        ident = audit["id"]
        raw_items = original.get("evidence", [])
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raw_items = [raw_items]
        converted_items, metadata = [], []
        for index, raw in enumerate(raw_items):
            if isinstance(raw, dict):
                if set(raw) - EVIDENCE_FIELDS:
                    converted = _opaque_evidence(raw)
                    reason = "unknown evidence fields archived verbatim"
                elif "path" in raw and raw["path"] == HOMEPAGE_REL.as_posix():
                    converted = _opaque_evidence(raw)
                    reason = "generated homepage is not independent evidence; original bytes archived before regeneration"
                elif "path" in raw and "uri" in raw:
                    converted = _opaque_evidence(raw)
                    reason = "ambiguous path/URI evidence archived verbatim"
                elif "path" in raw:
                    evidence_type = raw.get("type")
                    if (evidence_type is not None
                            and (not isinstance(evidence_type, str)
                                 or evidence_type not in _dependencies()[0].EVIDENCE_TYPES)):
                        converted = _opaque_evidence(raw)
                        reason = "unknown evidence type archived verbatim"
                    else:
                        converted, failure = self._convert_path_evidence(raw)
                        reason = failure
                        if converted is None:
                            converted = _opaque_evidence(raw)
                elif isinstance(raw.get("uri"), str) and URI_RE.fullmatch(raw["uri"]):
                    converted = copy.deepcopy(raw)
                    converted.setdefault("type", "other")
                    reason = None
                else:
                    converted = _opaque_evidence(raw)
                    reason = "malformed evidence archived verbatim"
                action = "preserve" if converted == raw else "map"
                if reason:
                    action = "archive"
                self._evidence_disposition(entry, index, action, raw, converted, reason)
                metadata.append({"verifiable": "path" in converted and "uri" not in converted,
                                 "reason": reason or ("URI evidence requires fresh verification"
                                                      if "uri" in converted else None)})
                converted_items.append(converted)
                continue

            command_evidence = self._command_test_evidence(entry, raw)
            if command_evidence:
                for converted in command_evidence:
                    reason = "real test file extracted from command; original command and arguments retained in audit"
                    self._evidence_disposition(entry, index, "map", raw, converted, reason)
                    metadata.append({"verifiable": True, "reason": None})
                    converted_items.append(converted)
                continue
            if isinstance(raw, str) and raw == HOMEPAGE_REL.as_posix():
                converted = _opaque_evidence(raw)
                reason = "generated homepage reference archived against original bytes; regenerated homepage is not reused as historical evidence"
                self._evidence_disposition(entry, index, "archive", raw, converted, reason)
                metadata.append({"verifiable": False, "reason": reason})
                converted_items.append(converted)
                continue
            if isinstance(raw, str) and _plain_file_reference(raw):
                graph, _, _ = _dependencies()
                if graph._safe_repo_path(raw) is not None:
                    target = graph._working_file(self.repo, raw)
                    if target is not None:
                        converted = {"type": _classify_path(raw), "path": raw,
                                     "sha256": graph._sha256(target.read_bytes())}
                        self._evidence_disposition(entry, index, "map", raw, converted,
                                                   "real file classified and hashed")
                        metadata.append({"verifiable": True, "reason": None})
                        converted_items.append(converted)
                        continue
            if (isinstance(raw, str) and URI_RE.fullmatch(raw)
                    and ("://" in raw or raw.lower().startswith(("urn:", "mailto:")))):
                converted = {"type": "other", "uri": raw}
                reason = "URI retained as opaque evidence; original archived"
            else:
                converted = _opaque_evidence(raw)
                reason = "non-file/scalar evidence archived verbatim without path guessing"
            self._evidence_disposition(entry, index, "archive", raw, converted, reason)
            metadata.append({"verifiable": False, "reason": reason})
            converted_items.append(converted)

        if "evidence" not in original or original["evidence"] is None:
            self._evidence_disposition(entry, None, "map", original.get("evidence"), [],
                                       "missing evidence normalized before adding legacy provenance")
        if ident.startswith("legacy."):
            cutover_path = "docs/architecture/evidence/legacy-cutover-source.json"
            cutover, failure = self._convert_path_evidence({"type": "legacy", "path": cutover_path})
            if cutover is not None and all(item.get("path") != cutover_path for item in converted_items):
                converted_items.append(cutover)
                metadata.append({"verifiable": True, "reason": None})
                self._evidence_disposition(
                    entry, "legacy-cutover", "map", cutover_path, cutover,
                    "legacy record linked to the existing cutover archive without inventing provenance")

        legacy = self._legacy_evidence(entry)
        converted_items.append(legacy)
        metadata.append({"verifiable": True, "reason": None})
        self._evidence_disposition(entry, "legacy-backup", "map", None, legacy,
                                   "original shard byte backup provenance")
        output["evidence"] = converted_items
        self.evidence_meta[ident] = metadata
        self._field(audit, "evidence", "map", original.get("evidence"), converted_items,
                    "see per-evidence dispositions")

    def _fix_target(self, target):
        if target != TYPO_TARGET:
            return target
        if FIXED_TARGET not in self.by_id:
            return target
        if TYPO_TARGET in self.by_id:
            raise MigrationError("ambiguous known target correction: both %s and %s exist" % (
                TYPO_TARGET, FIXED_TARGET))
        return FIXED_TARGET

    def _relation_shape(self, relation, ident, index):
        if not isinstance(relation, dict) or not isinstance(relation.get("target"), str):
            raise MigrationError("%s: malformed relation at index %d" % (ident, index))
        if "type" in relation:
            if set(relation) != {"type", "target"} or not isinstance(relation["type"], str):
                raise MigrationError("%s: unknown relation shape at index %d" % (ident, index))
            return "type", relation["type"], relation["target"]
        if "kind" in relation:
            if set(relation) != {"kind", "target"} or not isinstance(relation["kind"], str):
                raise MigrationError("%s: unknown relation kind shape at index %d" % (ident, index))
            return "kind", relation["kind"], relation["target"]
        raise MigrationError("%s: relation %d has neither type nor kind" % (ident, index))

    def _relation_audit(self, entry, index, action, original, output=None, note=None,
                        owner=None, deduplicated=False):
        item = {"index": index, "action": action, "original": copy.deepcopy(original)}
        if output is not None:
            item["output"] = copy.deepcopy(output)
        if note:
            item["note"] = note
        if owner is not None:
            item["owner_id"] = owner
        if deduplicated:
            item["deduplicated"] = True
        entry["audit"]["relations"].append(item)

    def _transform_relations(self, entry):
        graph, _, _ = _dependencies()
        original, output = entry["original"], entry["output"]
        ident = entry["audit"]["id"]
        uses_targets = set()
        for index, relation in enumerate(original["relations"]):
            field, relation_type, target = self._relation_shape(relation, ident, index)
            if field == "type" and relation_type == "uses":
                uses_targets.add(self._fix_target(target))

        for index, relation in enumerate(original["relations"]):
            field, relation_type, target = self._relation_shape(relation, ident, index)
            fixed = self._fix_target(target)
            note = "known misspelled target corrected" if fixed != target else None
            if field == "type" and relation_type in graph.RELATION_TYPES:
                converted = {"type": relation_type, "target": fixed}
                duplicate = converted in output["relations"]
                if not duplicate:
                    output["relations"].append(converted)
                self._relation_audit(entry, index, "map" if fixed != target else "preserve",
                                     relation, converted, note, deduplicated=duplicate)
                continue
            if relation_type not in (REVERSE_RELATIONS | KIND_RELATIONS | ARCHIVE_RELATIONS):
                raise MigrationError("%s: unknown relation type %r at index %d" % (
                    ident, relation_type, index))
            if fixed in uses_targets:
                self._relation_audit(entry, index, "archive", relation,
                                     {"type": "uses", "target": fixed},
                                     "equivalent uses edge retained; legacy edge archived")
                continue
            if relation_type in REVERSE_RELATIONS:
                parent = self.by_id.get(fixed)
                if parent is None:
                    raise MigrationError("%s: reverse contains target does not exist: %s" % (ident, fixed))
                if parent["output"].get("kind") != "module":
                    raise MigrationError("%s: %s target is not a parent module: %s" % (
                        ident, relation_type, fixed))
                converted = {"type": "contains", "target": ident}
                duplicate = converted in parent["output"]["relations"]
                if not duplicate:
                    parent["output"]["relations"].append(converted)
                self._relation_audit(entry, index, "map", relation, converted,
                                     "legacy parent edge reversed to module contains",
                                     owner=fixed, deduplicated=duplicate)
                continue
            if relation_type in KIND_RELATIONS:
                converted = {"type": relation_type, "target": fixed}
                duplicate = converted in output["relations"]
                if not duplicate:
                    output["relations"].append(converted)
                self._relation_audit(entry, index, "map", relation, converted,
                                     "legacy kind renamed to type; original note archived",
                                     deduplicated=duplicate)
                continue
            self._relation_audit(entry, index, "archive", relation,
                                 note="direction/semantics are not safe to infer; original edge archived")

    def _auxiliary_source_errors(self, record):
        graph, _, _ = _dependencies()
        ident = record["id"]
        errors = []
        sources = record.get("sources", [])
        verified = record.get("verified_commit")
        if sources and (not isinstance(verified, str)
                        or graph._resolve_commit(self.repo, verified) != verified):
            return ["auxiliary sources cannot be checked without a resolvable verified_commit"]
        for index, source in enumerate(sources):
            label = "sources[%d]" % index
            path = source.get("path")
            if graph._safe_repo_path(path) is None:
                errors.append("%s: unsafe auxiliary source path" % label)
                continue
            verified_data = graph._read_git_object(self.repo, verified, path)
            errors.extend(graph._source_hash_and_symbol_errors(
                ident, source, verified_data, label + " at verified_commit"))
            if record["status"] == "current" or sources:
                head_data = graph._read_git_object(self.repo, self.head, path)
                errors.extend(graph._source_hash_and_symbol_errors(
                    ident, source, head_data, label + " at HEAD"))
                try:
                    changed = graph._git(self.repo, "diff", "--quiet", verified, self.head, "--", path)
                except (OSError, subprocess.SubprocessError):
                    errors.append("%s: cannot compare auxiliary source drift" % label)
                else:
                    if changed.returncode != 0:
                        errors.append("%s: auxiliary source changed since verified_commit: %s" % (label, path))
        return errors

    def _evidence_retention_errors(self, entry, verified_tree):
        graph, _, _ = _dependencies()
        record = entry["output"]
        errors = []
        for index, (evidence, meta) in enumerate(zip(
                record["evidence"], self.evidence_meta[record["id"]])):
            if not meta["verifiable"]:
                errors.append("evidence[%d]: %s" % (index, meta["reason"] or "not byte-verifiable"))
                continue
            if not isinstance(evidence.get("sha256"), str) or not SHA256_RE.fullmatch(evidence["sha256"]):
                errors.append("evidence[%d]: full path sha256 is required" % index)
            errors.extend(graph._evidence_provenance_errors(
                self.repo, record, evidence, self.head))
            if (evidence.get("commit") is None) != (evidence.get("tree") is None):
                errors.append("evidence[%d]: commit and tree must be bound together" % index)
        return errors

    def _bind_code(self, entry, verified_tree):
        record = entry["output"]
        source = record["source"]
        binding = {
            "type": "code", "path": source["path"], "sha256": source["sha256"],
            "commit": record["verified_commit"], "tree": verified_tree,
        }
        for index, evidence in enumerate(record["evidence"]):
            if (evidence.get("type") == "code" and evidence.get("path") == source["path"]
                    and evidence.get("sha256") == source["sha256"]
                    and evidence.get("commit") in {None, record["verified_commit"]}
                    and evidence.get("tree") in {None, verified_tree}):
                if evidence != binding:
                    evidence.update(binding)
                    entry["audit"]["evidence"].append({
                        "index": index, "action": "map", "original": None,
                        "output": copy.deepcopy(binding),
                        "reason": "code evidence bound to verified source commit and tree",
                    })
                return
        record["evidence"].append(binding)
        self.evidence_meta[record["id"]].append({"verifiable": True, "reason": None})
        entry["audit"]["evidence"].append({
            "index": "code-binding", "action": "map", "original": None,
            "output": copy.deepcopy(binding),
            "reason": "required verified code binding added without changing verified_commit",
        })

    def _retain_or_downgrade(self, entry):
        graph, _, _ = _dependencies()
        record, audit = entry["output"], entry["audit"]
        original_status = audit["before_status"]
        reasons = []
        verified_tree = None
        if original_status in {"current", "release_only"}:
            if not isinstance(record.get("source"), dict):
                reasons.append("primary source is missing or malformed")
            source_errors, verified_tree = graph._check_verified_source(
                self.repo, record, self.head)
            reasons.extend(source_errors)
            if verified_tree is None and not reasons:
                reasons.append("verified source tree could not be resolved")
            reasons.extend(self._auxiliary_source_errors(record))
            reasons.extend(self._evidence_retention_errors(entry, verified_tree))
            if reasons:
                record["status"] = "needs_verification"
            else:
                self._bind_code(entry, verified_tree)
        elif record["status"] == "needs_verification":
            reasons.append("original status requires verification; migration never promotes it")

        audit["after_status"] = record["status"]
        if original_status == record["status"]:
            audit["status_change"] = "preserved"
        elif record["status"] == "needs_verification" and original_status in {"current", "release_only"}:
            audit["status_change"] = "downgraded"
        elif original_status == "legacy" and record["status"] == "needs_verification":
            audit["status_change"] = "legacy_requires_verification"
        else:
            audit["status_change"] = "normalized"
        audit["verification_reasons"] = list(dict.fromkeys(reasons))
        self.audit["records"].append(audit)
        if record["status"] == "needs_verification":
            self.audit["pending_verification"].append({
                "id": record["id"], "shard": entry["shard"],
                "reasons": audit["verification_reasons"] or ["fresh verification required"],
            })

    def _document_baseline(self, relative, document):
        if "baseline" not in document:
            return None
        baseline = document["baseline"]
        if not isinstance(baseline, dict):
            raise MigrationError("%s: baseline must be an object" % relative)
        if set(baseline) <= {"spec_baseline_date"}:
            return copy.deepcopy(baseline)
        is_query_reconciliation = (
            PurePosixPath(relative).stem == "query-reconciliation"
            or document.get("module") == "query-reconciliation"
        )
        if (is_query_reconciliation
                and set(baseline) == QUERY_RECONCILIATION_BASELINE_FIELDS):
            replacement = {
                "spec_baseline_date": QUERY_RECONCILIATION_BASELINE_DATE,
            }
            self.document_dispositions.append({
                "shard": relative,
                "action": "archive",
                "field": "baseline",
                "original": copy.deepcopy(baseline),
                "replacement": copy.deepcopy(replacement),
                "reason": (
                    "legacy query reconciliation observations archived; "
                    "formal baseline uses the historical cutover date 2026-10-22"
                ),
            })
            return replacement
        unknown = sorted(set(baseline) - {"spec_baseline_date"})
        raise MigrationError(
            "%s: unknown baseline fields refused: %s" % (relative, ", ".join(unknown)))

    def _build_documents(self):
        for relative, document in self.documents:
            converted = {key: copy.deepcopy(value) for key, value in document.items()
                         if key in {"module", "purpose"}}
            if "baseline" in document:
                converted["baseline"] = self._document_baseline(relative, document)
            converted["schema"] = V2_SCHEMA
            converted["records"] = [entry["output"] for entry in self.entries
                                    if entry["shard"] == relative]
            self.output_documents[relative] = converted
            self.output_bytes[relative] = _json_bytes(converted)

    def _build_audit(self):
        for relative, _ in self.documents:
            backup = (self.backup_rel / "shards" / PurePosixPath(relative).name).as_posix()
            self.audit["inputs"].append({
                "kind": "shard", "path": relative, "backup_path": backup,
                "sha256": _sha256(self.originals[relative]), "bytes": len(self.originals[relative]),
            })
            self.audit["outputs"].append({
                "kind": "shard", "path": relative,
                "sha256": _sha256(self.output_bytes[relative]), "bytes": len(self.output_bytes[relative]),
            })
        homepage_backup = (self.backup_rel / "ARCHITECTURE.md").as_posix()
        original = self.originals[HOMEPAGE_REL.as_posix()]
        self.audit["inputs"].append({
            "kind": "homepage", "path": HOMEPAGE_REL.as_posix(), "backup_path": homepage_backup,
            "sha256": _sha256(original), "bytes": len(original),
        })


def _attach_git(repo, staging):
    graph, _, _ = _dependencies()
    result = graph._git(repo, "rev-parse", "--absolute-git-dir", text=True)
    if result.returncode != 0:
        return
    git_dir = result.stdout.strip()
    if git_dir:
        os.symlink(git_dir, staging / ".git")


def _copy_local_evidence(plan, staging):
    graph, _, _ = _dependencies()
    for entry in plan.entries:
        for evidence in entry["output"]["evidence"]:
            if "path" not in evidence or evidence.get("commit") or evidence.get("tree"):
                continue
            relative = evidence["path"]
            destination = staging / relative
            if destination.is_file():
                continue
            source = graph._working_file(plan.repo, relative)
            if source is None:
                raise MigrationError("staged local evidence is missing: %s" % relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)


def _stage(plan):
    """Build and validate a complete candidate, returning files to publish."""
    graph, validator, store = _dependencies()
    schema = json.loads(graph.SCHEMA_PATH.read_text(encoding="utf-8"))
    writes = {}
    with tempfile.TemporaryDirectory(prefix="architecture-v1-v2-stage-") as temporary:
        staging = Path(temporary)
        _attach_git(plan.repo, staging)
        for relative, document in plan.documents:
            backup = (plan.backup_rel / "shards" / PurePosixPath(relative).name).as_posix()
            raw = plan.originals[relative]
            store.atomic_write(staging / backup, raw)
            writes[backup] = raw
        homepage_backup = (plan.backup_rel / "ARCHITECTURE.md").as_posix()
        raw_homepage = plan.originals[HOMEPAGE_REL.as_posix()]
        store.atomic_write(staging / homepage_backup, raw_homepage)
        writes[homepage_backup] = raw_homepage

        for relative, raw in plan.output_bytes.items():
            store.atomic_write(staging / relative, raw)
            writes[relative] = raw
        _copy_local_evidence(plan, staging)

        schema_errors = []
        for relative, document in plan.output_documents.items():
            schema_errors.extend("%s: %s" % (relative, error)
                                 for error in validator.validate(schema, document, path="$"))
        if schema_errors:
            raise MigrationError("staged v2 schema validation failed:\n" + "\n".join(schema_errors))
        _, errors = graph.check(staging, head=plan.head)
        if errors:
            raise MigrationError("staged architecture check failed:\n" + "\n".join(errors))
        homepage_bytes, errors = graph.render_homepage(staging, write=True, head=plan.head)
        if errors:
            raise MigrationError("staged homepage rendering failed:\n" + "\n".join(errors))
        _, errors = graph.render_homepage(staging, write=False, head=plan.head)
        if errors:
            raise MigrationError("staged homepage byte check failed:\n" + "\n".join(errors))
        writes[HOMEPAGE_REL.as_posix()] = homepage_bytes
        plan.audit["outputs"].append({
            "kind": "homepage", "path": HOMEPAGE_REL.as_posix(),
            "sha256": _sha256(homepage_bytes), "bytes": len(homepage_bytes),
        })
        audit_bytes = _json_bytes(plan.audit)
        audit_path = (plan.backup_rel / "audit.json").as_posix()
        store.atomic_write(staging / audit_path, audit_bytes)
        writes[audit_path] = audit_bytes
        return writes


def _rollback(repo, attempted, recovery_records, backup_abs):
    errors = []
    for relative in reversed(attempted):
        target = repo / relative
        recovery = recovery_records.get(relative)
        try:
            if recovery is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(recovery, target)
        except OSError as exc:
            errors.append("%s: %s" % (relative, exc))
    if backup_abs.exists():
        try:
            shutil.rmtree(backup_abs)
        except OSError as exc:
            errors.append("backup cleanup: %s" % exc)
    return errors


def _publish(repo, writes, originals, backup_abs):
    _, _, store = _dependencies()
    for relative, raw in originals.items():
        try:
            if (repo / relative).read_bytes() != raw:
                raise MigrationError("input changed between planning and apply: %s" % relative)
        except OSError as exc:
            raise MigrationError("cannot recheck input before apply: %s: %s" % (relative, exc)) from exc
    for relative in writes:
        target = repo / relative
        if target.exists() and not target.is_file():
            raise MigrationError("publish target is not a regular file: %s" % relative)

    recovery_dir = Path(tempfile.mkdtemp(prefix=".architecture-v1-v2-recovery-",
                                        dir=str(repo / "docs")))
    recovery_records = {}
    attempted = []
    try:
        for index, relative in enumerate(writes):
            target = repo / relative
            if target.exists():
                recovery = recovery_dir / str(index)
                shutil.copy2(target, recovery)
                recovery_records[relative] = recovery
        for relative, raw in writes.items():
            attempted.append(relative)
            target = repo / relative
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
            store.atomic_write(target, raw, mode=mode)
    except Exception as exc:
        rollback_errors = _rollback(repo, attempted, recovery_records, backup_abs)
        message = "apply failed; all attempted target writes were rolled back: %s" % exc
        if rollback_errors:
            message = "apply failed and rollback reported errors: %s; %s" % (
                exc, "; ".join(rollback_errors))
        raise MigrationError(message) from exc
    finally:
        shutil.rmtree(recovery_dir, ignore_errors=True)


def migrate(repo, apply=False, date=None, backup_dir=None, head="HEAD"):
    """Return a deterministic migration plan or apply it transactionally.

    Re-running against an all-v2 graph is a zero-diff no-op.  Mixed v1/v2 input
    is rejected because it can indicate an interrupted or manually edited cutover.
    """
    repo = Path(repo).expanduser().resolve()
    if not repo.is_dir():
        raise MigrationError("repository is not a directory: %s" % repo)
    documents, originals, schemas = _read_inputs(repo)
    if schemas == {V2_SCHEMA}:
        return {
            "schema": RESULT_SCHEMA, "mode": "apply" if apply else "dry-run",
            "applied": False, "changed": False, "already_migrated": True,
            "record_count": sum(len(document["records"]) for _, document in documents),
            "automatic_promotions": 0,
        }
    if schemas != {V1_SCHEMA}:
        raise MigrationError("mixed or unsupported shard schemas: %s" % ", ".join(sorted(schemas)))
    migration_date = _date_or_today(date)
    backup_abs, backup_rel = _backup_path(repo, backup_dir, migration_date)
    if backup_abs.exists():
        raise MigrationError("backup directory already exists; refusing overwrite: %s" % backup_rel)
    plan = _Plan(repo, documents, originals, migration_date, backup_rel, head).prepare()
    writes = _stage(plan)
    if apply:
        _publish(repo, writes, originals, backup_abs)
    return {
        "schema": RESULT_SCHEMA,
        "mode": "apply" if apply else "dry-run",
        "applied": bool(apply), "changed": bool(apply), "already_migrated": False,
        "record_count": len(plan.entries),
        "automatic_promotions": 0,
        "audit": plan.audit,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path,
                        help="target repository root (required; no implicit cwd)")
    parser.add_argument("--apply", action="store_true",
                        help="publish the validated candidate; default is dry-run")
    parser.add_argument("--date", dest="migration_date",
                        help="deterministic migration date, YYYY-MM-DD (default: today)")
    parser.add_argument("--backup-dir",
                        help="dedicated directory below docs/architecture/evidence/")
    parser.add_argument("--head", default="HEAD", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        result = migrate(args.repo, apply=args.apply, date=args.migration_date,
                         backup_dir=args.backup_dir, head=args.head)
    except (MigrationError, OSError, subprocess.SubprocessError) as exc:
        print("migration failed: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
