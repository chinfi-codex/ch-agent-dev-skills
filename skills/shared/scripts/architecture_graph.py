#!/usr/bin/env python3
"""Strict architecture graph v2 reader, verifier, flow checker and renderer.

``docs/architecture/*.json`` is the only architecture fact source.  The legacy
Markdown table can be imported once, but is never merged into runtime reads.
Only Python's standard library is used; Git is required for verified records.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

# Isolated ``python3 -I script.py`` does not guarantee sibling imports.  Keep
# the shared validator lazy, but make its script directory explicitly available.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

SCHEMA_NAME = "pandu.architecture-graph/v2"
GRAPH_REL = Path("docs") / "architecture"
HOMEPAGE_REL = Path("docs") / "ARCHITECTURE.md"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references" / "architecture-graph.schema.json"
STATUSES = {"current", "release_only", "superseded", "retired", "needs_verification"}
KINDS = {"module", "interface", "constraint"}
RELATION_TYPES = {
    "calls", "depends_on", "implements", "extends", "uses", "exposes",
    "contains", "constrains", "emits", "consumes", "reads", "writes",
}
EVIDENCE_TYPES = {
    "code", "test", "spec", "decision", "release", "command", "legacy",
    "cutover", "other",
}
ID_RE = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z0-9_-]+(?:\.[a-z0-9_-]+)*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEHOLDERS = {"", "…", "-", "—", "无", "n/a", "na", "none", "待补", "待核对", "（待核对）"}


def _dedup(items):
    return list(dict.fromkeys(items))


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _git(repo, *args, text=False):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True,
        text=text, timeout=30,
    )


def _ticket_document_root(repo, issue):
    """Read shared ticket documents at the registered primary checkout only."""
    import ticket_context
    repo = Path(repo).resolve()
    shared = repo / "dev"
    if not shared.is_symlink():
        return repo
    listing = _git(repo, "worktree", "list", "--porcelain", "-z", text=True)
    first = listing.stdout.split("\0", 1)[0]
    if listing.returncode or not first.startswith("worktree "):
        raise ticket_context.ContextError("cannot determine primary worktree dev")
    primary = Path(first[len("worktree "):]).resolve()
    expected = primary / "dev"
    if primary == repo or not expected.is_dir() or expected.resolve() != expected or shared.resolve() != expected:
        raise ticket_context.ContextError("shared dev must point to primary worktree dev")
    if not Path(issue).resolve().is_relative_to(expected):
        raise ticket_context.ContextError("shared ticket must stay inside primary worktree dev")
    return primary


def _resolve_commit(repo, revision):
    try:
        result = _git(repo, "rev-parse", "--verify", revision + "^{commit}", text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value if SHA_RE.fullmatch(value) else None


def _resolve_tree(repo, revision):
    try:
        result = _git(repo, "rev-parse", "--verify", revision + "^{tree}", text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value if SHA_RE.fullmatch(value) else None


def _read_git_object(repo, revision, path):
    try:
        result = _git(repo, "show", "%s:%s" % (revision, path))
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _is_ancestor(repo, ancestor, head):
    try:
        result = _git(repo, "merge-base", "--is-ancestor", ancestor, head)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _safe_repo_path(value):
    """Return a canonical relative POSIX path, or None for an unsafe path."""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    canonical = path.as_posix()
    return canonical if canonical == value else None


def _working_file(repo, relative):
    safe = _safe_repo_path(relative)
    if safe is None:
        return None
    try:
        root = repo.resolve()
        target = (repo / safe).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    return target if target.is_file() else None


def split_row(line):
    return [cell.replace("\\|", "|").strip()
            for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _legacy_section_rows(repo, heading):
    path = repo / HOMEPAGE_REL
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    active, rows = False, []
    for line in lines:
        if line.startswith("## "):
            if active:
                break
            active = line.startswith("## " + heading)
            continue
        if active and line.strip().startswith("|"):
            cells = split_row(line)
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            if cells and (cells[0].startswith("接口（") or cells[0].lower() in {"interface", "接口"}):
                continue
            if cells and not all(cell in PLACEHOLDERS for cell in cells):
                rows.append(cells)
    return rows


def legacy_rows(repo):
    rows = _legacy_section_rows(repo, "接口现行态")
    return [row for row in rows if len(row) >= 5 and row[0] != "…"]


def legacy_records(repo):
    """Build unverified v2-shaped records for the one-time importer only."""
    records = []
    for row in legacy_rows(repo):
        branch = row[5] if len(row) > 5 else ""
        code = row[2] if len(row) > 2 else ""
        raw = "\t".join((row[0], code, branch))
        ident = "legacy." + _sha256(raw.encode("utf-8"))[:16]
        path, separator, symbol = code.rpartition(":")
        source = {"path": path if separator else code}
        if separator and symbol:
            source["symbol"] = symbol
        records.append({
            "id": ident,
            "module": "legacy",
            "kind": "interface",
            "name": row[0],
            "responsibility": row[1] or row[0],
            "status": "needs_verification",
            "version": branch or None,
            "applies_to": [branch] if branch else None,
            "source": source,
            "verification": "legacy table import only; code verification required",
            "evidence": [],
            "last_verified": None,
            "last_verified_version": "unverified",
            "verified_commit": None,
            "relations": [],
            "supersedes": [],
        })
    return records


def _schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_document(document):
    # Lazy import keeps query/migration helpers usable while the shared validator
    # is being deployed, and makes the integration point explicit.
    try:
        import json_schema
    except ImportError as exc:
        return ["shared json_schema validator is unavailable: %s" % exc]
    try:
        return json_schema.validate(_schema(), document, path="$")
    except (OSError, UnicodeError, ValueError) as exc:
        return ["cannot validate architecture graph schema: %s" % exc]


def _revision_graph_files(repo, revision):
    try:
        result = _git(repo, "ls-tree", "-r", "--name-only", "-z", revision,
                      "--", GRAPH_REL.as_posix())
    except (OSError, subprocess.SubprocessError) as exc:
        return [], ["cannot list graph at %s: %s" % (revision, exc)]
    if result.returncode != 0:
        return [], ["cannot list graph at %s" % revision]
    files = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="replace")
        path = PurePosixPath(relative)
        if path.parent.as_posix() == GRAPH_REL.as_posix() and path.suffix == ".json":
            files.append(relative)
    return sorted(files), []


def load_graph_details(repo, revision=None):
    repo = Path(repo)
    errors, records, shards, baselines = [], [], {}, []
    if revision is None:
        folder = repo / GRAPH_REL
        files = [] if not folder.is_dir() else [
            path.relative_to(repo).as_posix() for path in sorted(folder.glob("*.json"))
        ]
    else:
        files, list_errors = _revision_graph_files(repo, revision)
        errors.extend(list_errors)
    documents = []
    for relative in files:
        try:
            if revision is None:
                raw = (repo / relative).read_bytes()
            else:
                raw = _read_git_object(repo, revision, relative)
                if raw is None:
                    raise OSError("git object does not exist")
            document = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append("%s: invalid JSON (%s)" % (relative, type(exc).__name__))
            continue
        documents.append((relative, document))
        for error in _validate_document(document):
            errors.append("%s: %s" % (relative, error))
        if not isinstance(document, dict) or not isinstance(document.get("records"), list):
            continue
        baseline = document.get("baseline")
        if isinstance(baseline, dict) and isinstance(baseline.get("spec_baseline_date"), str):
            baselines.append(baseline["spec_baseline_date"])
        for record in document["records"]:
            if not isinstance(record, dict):
                continue
            records.append(record)
            ident = record.get("id")
            if isinstance(ident, str):
                shards.setdefault(ident, []).append(relative)
    if len(set(baselines)) > 1:
        errors.append("conflicting spec_baseline_date values across graph shards")
    return {
        "records": records,
        "errors": errors,
        "shards": shards,
        "baselines": sorted(set(baselines)),
        "files": files,
        "documents": documents,
    }


def load_graph(repo, revision=None):
    details = load_graph_details(repo, revision=revision)
    return details["records"], details["errors"]


def read_records(repo):
    """Read v2 shards only; never merge the legacy table into runtime results."""
    details = load_graph_details(repo)
    records, errors = details["records"], list(details["errors"])
    if legacy_rows(repo):
        if records:
            errors.append("runtime dual source is forbidden: legacy interface rows coexist with v2 shards; run migrate-legacy before cutover or regenerate the homepage")
        else:
            errors.append("migration required: legacy interface rows are not runtime graph records; run migrate-legacy once")
    return records, _dedup(errors)


def _record_identity(record):
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    return (
        record.get("kind"), record.get("module"), record.get("name"),
        source.get("path"), source.get("symbol"),
    )


def _records_by_id(records):
    return {record.get("id"): record for record in records
            if isinstance(record, dict) and isinstance(record.get("id"), str)}


def compare_record_ids(base_records, head_records):
    errors = []
    base = _records_by_id(base_records)
    head = _records_by_id(head_records)
    for ident in sorted(set(base) - set(head)):
        errors.append("stable id disappeared: %s (retain it as retired/superseded)" % ident)
    for ident in sorted(set(base) & set(head)):
        if _record_identity(base[ident]) != _record_identity(head[ident]):
            errors.append("stable id reused for a different identity: %s" % ident)
    return errors


def _record_sources(record):
    """Return the primary binding followed by each well-formed auxiliary binding."""
    bindings = []
    source = record.get("source")
    if isinstance(source, dict):
        bindings.append(("source", source))
    sources = record.get("sources")
    if isinstance(sources, list):
        for index, auxiliary in enumerate(sources):
            if isinstance(auxiliary, dict):
                bindings.append(("sources[%d]" % index, auxiliary))
    return bindings


def _source_path_errors(record):
    """Reject duplicate bindings and inconsistent hashes, not multiple symbols per file."""
    ident = record.get("id")
    errors = []
    seen_bindings = {}
    hashes_by_path = {}
    for label, source in _record_sources(record):
        path = source.get("path")
        if not isinstance(path, str) or not path:
            continue
        binding = (path, source.get("symbol"))
        if binding in seen_bindings:
            errors.append("%s: duplicate source binding across source/sources: %s (%s and %s)" % (
                ident, binding, seen_bindings[binding], label))
        else:
            seen_bindings[binding] = label
        sha256 = source.get("sha256")
        if isinstance(sha256, str):
            known = hashes_by_path.get(path)
            if isinstance(known, str) and known != sha256:
                errors.append("%s: inconsistent source hashes for %s" % (ident, path))
            else:
                hashes_by_path[path] = sha256
    return errors


def _source_hash_and_symbol_errors(ident, source, data, label):
    errors = []
    if data is None:
        return ["%s: cannot read source at %s: %s" % (ident, label, source.get("path"))]
    expected = source.get("sha256")
    if isinstance(expected, str) and _sha256(data) != expected:
        if label == "HEAD":
            errors.append("%s: stale source hash at HEAD: %s" % (ident, source.get("path")))
        else:
            errors.append("%s: source hash does not match %s: %s" % (ident, label, source.get("path")))
    symbol = source.get("symbol")
    if isinstance(symbol, str) and symbol and symbol not in data.decode("utf-8", errors="replace"):
        errors.append("%s: source symbol missing at %s: %s in %s" % (
            ident, label, symbol, source.get("path")))
    return errors


def _check_verified_source(repo, record, head):
    ident = record.get("id")
    status = record.get("status")
    source = record.get("source")
    if not isinstance(source, dict):
        return [], None
    bindings = _record_sources(record)
    errors = []
    verified = record.get("verified_commit")
    if not isinstance(verified, str) or not SHA_RE.fullmatch(verified):
        return ["%s: %s requires a full 40-character verified_commit" % (ident, status)], None
    for label, bound_source in bindings:
        path = bound_source.get("path")
        if not isinstance(path, str) or _safe_repo_path(path) is None:
            errors.append("%s: %s.path must be a safe repository-relative path" % (ident, label))
        expected = bound_source.get("sha256")
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            errors.append("%s: %s requires a full %s.sha256" % (ident, status, label))
    if record.get("last_verified") is None:
        errors.append("%s: %s requires last_verified date" % (ident, status))
    verified_commit = _resolve_commit(repo, verified)
    if verified_commit is None or verified_commit != verified:
        return errors + ["%s: verified_commit is not a resolvable commit: %s" % (ident, verified)], None
    verified_tree = _resolve_tree(repo, verified)
    if verified_tree is None:
        errors.append("%s: cannot resolve tree at verified_commit" % ident)
    for label, bound_source in bindings:
        path = bound_source.get("path")
        if not isinstance(path, str) or _safe_repo_path(path) is None:
            continue
        verified_data = _read_git_object(repo, verified, path)
        errors.extend(_source_hash_and_symbol_errors(
            ident, bound_source, verified_data, "verified_commit"))

    # Historical release_only primary bindings retain their existing semantics.
    # Auxiliary bindings are current delivery inputs for both verified statuses,
    # so they must also remain present and unchanged at the candidate HEAD.
    check_head = status == "current" or any(label != "source" for label, _ in bindings)
    if check_head:
        ancestor = _is_ancestor(repo, verified, head)
        if ancestor is False:
            errors.append("%s: %s verified_commit is not an ancestor of checked HEAD" % (ident, status))
        elif ancestor is None:
            errors.append("%s: cannot verify %s verified_commit ancestry at HEAD" % (ident, status))
    for label, bound_source in bindings:
        if status == "release_only" and label == "source":
            continue
        path = bound_source.get("path")
        if not isinstance(path, str) or _safe_repo_path(path) is None:
            continue
        head_data = _read_git_object(repo, head, path)
        errors.extend(_source_hash_and_symbol_errors(ident, bound_source, head_data, "HEAD"))
        try:
            changed = _git(repo, "diff", "--quiet", verified, head, "--", path)
        except (OSError, subprocess.SubprocessError):
            errors.append("%s: cannot compare verified_commit..HEAD source" % ident)
        else:
            if changed.returncode != 0:
                errors.append("%s: source changed since verified_commit: %s" % (ident, path))
    return errors, verified_tree


def _evidence_provenance_errors(repo, record, evidence, head):
    ident = record.get("id")
    errors = []
    commit = evidence.get("commit")
    tree = evidence.get("tree")
    if commit is not None:
        resolved = _resolve_commit(repo, commit) if isinstance(commit, str) else None
        if resolved is None or resolved != commit:
            errors.append("%s: evidence commit is not resolvable: %s" % (ident, commit))
        elif tree is not None and _resolve_tree(repo, commit) != tree:
            errors.append("%s: evidence tree does not match evidence commit" % ident)
        if record.get("status") == "current" and resolved is not None:
            ancestor = _is_ancestor(repo, commit, head)
            if ancestor is False:
                errors.append("%s: evidence commit is not an ancestor of checked HEAD" % ident)
            elif ancestor is None:
                errors.append("%s: cannot verify evidence commit ancestry" % ident)
    if tree is not None:
        resolved_tree = _resolve_tree(repo, tree) if isinstance(tree, str) else None
        if resolved_tree is None or resolved_tree != tree:
            errors.append("%s: evidence tree is not resolvable: %s" % (ident, tree))
    return errors


def _check_evidence(repo, record, head, verified_tree):
    ident = record.get("id")
    status = record.get("status")
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    evidence_items = record.get("evidence")
    if not isinstance(evidence_items, list):
        return ["%s: evidence must be a list" % ident]
    if not evidence_items:
        return ["%s: evidence must not be empty" % ident]
    errors = []
    for index, evidence in enumerate(evidence_items):
        if not isinstance(evidence, dict):
            errors.append("%s: malformed evidence at index %d" % (ident, index))
            continue
        evidence_type = evidence.get("type")
        if not isinstance(evidence_type, str) or evidence_type not in EVIDENCE_TYPES:
            errors.append("%s: invalid evidence type: %s" % (ident, evidence_type))
        errors.extend(_evidence_provenance_errors(repo, record, evidence, head))
        relative = evidence.get("path")
        if relative is not None:
            if _safe_repo_path(relative) is None:
                errors.append("%s: evidence path escapes repository or is not canonical: %s" % (ident, relative))
                continue
            reference = evidence.get("commit") or evidence.get("tree")
            if reference is not None and not isinstance(reference, str):
                # Schema/provenance errors above are authoritative; do not pass a
                # malformed revision through to subprocess.
                continue
            if reference is not None:
                data = _read_git_object(repo, reference, relative)
                if data is None:
                    errors.append("%s: evidence is missing at %s: %s" % (ident, reference, relative))
                    continue
            else:
                target = _working_file(repo, relative)
                if target is None:
                    errors.append("%s: evidence path is missing or outside repository: %s" % (ident, relative))
                    continue
                try:
                    data = target.read_bytes()
                except OSError:
                    errors.append("%s: cannot read evidence: %s" % (ident, relative))
                    continue
            expected = evidence.get("sha256")
            if not isinstance(expected, str):
                errors.append("%s: local evidence requires sha256: %s" % (ident, relative))
            elif _sha256(data) != expected:
                errors.append("%s: evidence hash does not match: %s" % (ident, relative))
        elif evidence.get("uri") is None:
            errors.append("%s: evidence requires path or uri" % ident)
    if status in {"current", "release_only"}:
        verified = record.get("verified_commit")
        bound = any(
            isinstance(item, dict)
            and item.get("type") == "code"
            and item.get("path") == source.get("path")
            and item.get("sha256") == source.get("sha256")
            and item.get("commit") == verified
            and item.get("tree") == verified_tree
            and verified_tree is not None
            for item in evidence_items
        )
        if not bound:
            errors.append("%s: missing code evidence bound to source.sha256, verified_commit and its tree" % ident)
    return errors


def _validate_graph(repo, details, head):
    """Validate one graph revision's records and evidence against *head*."""
    records, errors = details["records"], list(details["errors"])
    ids, supersedes_by_id = {}, {}
    current_interfaces = {}
    for record in records:
        ident = record.get("id")
        status = record.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            errors.append("%s: invalid status" % ident)
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            errors.append("%s: invalid kind" % ident)
        if not isinstance(ident, str) or not ident:
            errors.append("record has empty id")
            continue
        if ident in ids:
            locations = details["shards"].get(ident, [])
            errors.append("duplicate id across shards: %s (%s)" % (ident, ", ".join(locations)))
        ids[ident] = record
        errors.extend(_source_path_errors(record))
        if status in {"current", "release_only"}:
            source_errors, verified_tree = _check_verified_source(repo, record, head)
            errors.extend(source_errors)
        else:
            verified_tree = None
        errors.extend(_check_evidence(repo, record, head, verified_tree))
        if (status == "current" and record.get("kind") == "interface"
                and isinstance(record.get("module"), str)
                and isinstance(record.get("name"), str)):
            key = (record.get("module"), record.get("name"))
            current_interfaces.setdefault(key, []).append(ident)
    for key, identifiers in sorted(current_interfaces.items(), key=lambda item: repr(item[0])):
        if len(identifiers) > 1:
            errors.append("conflicting current interface records for %s/%s: %s" % (
                key[0], key[1], ", ".join(sorted(identifiers))))
    for record in records:
        ident = record.get("id")
        supersedes = record.get("supersedes", [])
        relations = record.get("relations", [])
        if not isinstance(supersedes, list):
            errors.append("%s: supersedes must be a list" % ident)
            supersedes = []
        if not isinstance(relations, list):
            errors.append("%s: relations must be a list" % ident)
            relations = []
        if not isinstance(ident, str) or ident not in ids:
            continue
        valid_supersedes = []
        for target in supersedes:
            if not isinstance(target, str):
                errors.append("%s: invalid supersedes target %r" % (ident, target))
            elif target not in ids:
                errors.append("%s: supersedes missing id %s" % (ident, target))
            else:
                valid_supersedes.append(target)
        supersedes_by_id[ident] = valid_supersedes
        for relation in relations:
            if not isinstance(relation, dict) or not isinstance(relation.get("target"), str):
                errors.append("%s: malformed relation" % ident)
                continue
            relation_type = relation.get("type")
            if not isinstance(relation_type, str) or relation_type not in RELATION_TYPES:
                errors.append("%s: invalid relation type %s" % (ident, relation_type))
            if relation["target"] not in ids:
                errors.append("%s: dangling relation %s" % (ident, relation["target"]))
    visiting, visited = set(), set()

    def visit(ident):
        if ident in visiting:
            errors.append("supersedes cycle at %s" % ident)
            return
        if ident in visited:
            return
        visiting.add(ident)
        for target in supersedes_by_id.get(ident, []):
            if target in ids:
                visit(target)
        visiting.remove(ident)
        visited.add(ident)

    for ident in ids:
        visit(ident)
    return records, _dedup(errors)


def check(repo, head="HEAD", base=None):
    """Validate v2 shards and verify code/evidence against the candidate HEAD.

    Historical ``release_only`` primary sources are checked only at
    ``verified_commit``; auxiliary sources are also checked at candidate HEAD.
    When *base* is supplied, stable IDs are also compared between *base* and
    *head* graph revisions.
    """
    repo = Path(repo)
    details = load_graph_details(repo)
    records, errors = _validate_graph(repo, details, head)
    if base is not None:
        base_details = load_graph_details(repo, revision=base)
        errors.extend("base: " + error for error in base_details["errors"])
        head_details = load_graph_details(repo, revision=head)
        if head_details["files"] or not details["files"]:
            errors.extend("head: " + error for error in head_details["errors"])
            comparison_head = head_details["records"]
        else:
            comparison_head = records
        errors.extend(compare_record_ids(base_details["records"], comparison_head))
    return records, _dedup(errors)


def check_candidate(repo, head="HEAD", base=None):
    """Return head records plus only the errors introduced relative to *base*.

    ``baseline_errors`` already exist at the frozen base and are reported
    separately; a ticket-level gate must not fail for unrelated repository-wide
    drift.  Ticket/spec mapping errors stay in the flow helpers and are not
    subtracted.
    """
    repo = Path(repo)
    details = load_graph_details(repo)
    records, head_errors = _validate_graph(repo, details, head)
    if base is None:
        return records, head_errors, []
    base_details = load_graph_details(repo, revision=base)
    base_records, base_errors = _validate_graph(repo, base_details, base)
    head_errors = _dedup(list(head_errors) + compare_record_ids(base_records, records))
    baseline = set(base_errors)
    new_errors = [error for error in head_errors if error not in baseline]
    return records, _dedup(new_errors), _dedup(base_errors)


def select(records, *, module=None, interface=None, version=None, status=None):
    out = []
    for record in records:
        if module and record.get("module") != module:
            continue
        if interface and interface not in (record.get("name", "") + " " + record.get("id", "")):
            continue
        if status and record.get("status") != status:
            continue
        if version:
            applies = record.get("applies_to") or []
            if record.get("version") != version and version not in applies and "*" not in applies:
                continue
        if status is None and record.get("status") in {"needs_verification", "superseded", "retired"}:
            continue
        if status is None and record.get("status") == "release_only" and not version:
            continue
        out.append(record)
    return out


def homepage(records, baseline_date=None):
    """Return the deterministic, short generated architecture index."""
    modules = sorted({record.get("module", "") for record in records
                      if record.get("kind") == "module" and record.get("module")})
    current = sum(record.get("status") == "current" for record in records)
    release_only = sum(record.get("status") == "release_only" for record in records)
    pending = sum(record.get("status") == "needs_verification" for record in records)
    lines = ["# 架构图谱", ""]
    if isinstance(baseline_date, str) and DATE_RE.fullmatch(baseline_date):
        lines.extend(["<!-- baseline: %s -->" % baseline_date, ""])
    lines.extend([
        "本页由 `docs/architecture/*.json` 确定性生成；请只编辑架构分片，不要在本页维护架构表。",
        "读取与筛选：`python3 ../shared/scripts/architecture_graph.py --repo <项目根> [--module <模块>|--interface <关键词>|--version <版本>]`。",
        "", "## 覆盖范围", "",
        "- 模块：%d（%s）" % (len(modules), "、".join(modules)),
        "- 架构记录：%d；current %d，release_only %d，needs_verification %d" % (
            len(records), current, release_only, pending),
        "", "## 写入规则", "",
        "模块、接口与约束均以 `docs/architecture/*.json` 分片为唯一写入源；缺少代码、发布或决策证据的记录保持 `needs_verification`。",
        "按票更新对应分片，并在记录中保留来源、验证证据、分支/版本适用范围及替代关系。",
        "", "## 证据与历史", "",
        "迁移审计、旧表逐行归档和未核实项见 `docs/architecture/evidence/`。历史记录不因本页生成而删除。",
    ])
    return "\n".join(lines) + "\n"


def _baseline_date(repo, details):
    if details["baselines"]:
        return details["baselines"][0]
    audit_path = repo / GRAPH_REL / "evidence" / "cutover-audit.json"
    try:
        value = json.loads(audit_path.read_text(encoding="utf-8")).get("spec_baseline_date")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, str) and DATE_RE.fullmatch(value) else None


def render_homepage(repo, write=False, head="HEAD", validated=None):
    """Write the homepage, or compare its exact bytes when *write* is false."""
    repo = Path(repo)
    details = load_graph_details(repo)
    if validated is None:
        records, errors = check(repo, head=head)
    else:
        records = validated["records"]
        errors = list(validated.get("errors", []))
    if not details["files"]:
        errors.append("no v2 graph shards are available for homepage rendering")
    if legacy_rows(repo):
        errors.append("legacy interface table must be migrated once; runtime dual source is forbidden")
    expected = homepage(records, _baseline_date(repo, details)).encode("utf-8")
    path = repo / HOMEPAGE_REL
    if write:
        if not errors:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
        return expected, _dedup(errors)
    try:
        actual = path.read_bytes()
    except OSError:
        errors.append("homepage is missing: docs/ARCHITECTURE.md")
    else:
        if actual != expected:
            errors.append("homepage is hand-edited or stale: docs/ARCHITECTURE.md (byte comparison failed)")
    return expected, _dedup(errors)


def migrate_legacy(repo):
    """Import legacy interface rows once into an unverified v2 migration shard."""
    repo = Path(repo)
    graph_dir = repo / GRAPH_REL
    destination = graph_dir / "legacy-migration.json"
    if destination.exists():
        return ["legacy migration has already been run: %s" % destination.relative_to(repo)]
    if graph_dir.is_dir() and list(graph_dir.glob("*.json")):
        return ["legacy migration is one-time only and refuses to coexist with existing graph shards"]
    records = legacy_records(repo)
    if not records:
        return ["no legacy interface rows found; migration refused"]
    try:
        original = (repo / HOMEPAGE_REL).read_bytes()
    except OSError as exc:
        return ["cannot read legacy homepage: %s" % exc]
    baseline_match = re.search(rb"<!--\s*baseline:\s*(\d{4}-\d{2}-\d{2})", original)
    baseline = baseline_match.group(1).decode("ascii") if baseline_match else datetime.date.today().isoformat()
    evidence_dir = graph_dir / "evidence"
    archive_relative = (GRAPH_REL / "evidence" / "legacy-rows.json").as_posix()
    archive = _json_bytes({
        "schema": "pandu.architecture-legacy-rows/v1",
        "source": HOMEPAGE_REL.as_posix(),
        "rows": [{"id": record["id"], "row": row}
                 for record, row in zip(records, legacy_rows(repo))],
    })
    archive_hash = _sha256(archive)
    for record in records:
        record["evidence"] = [{
            "type": "legacy", "path": archive_relative, "sha256": archive_hash,
        }]
    document = {
        "schema": SCHEMA_NAME,
        "module": "legacy",
        "purpose": "one-time legacy import; every record requires fresh verification",
        "baseline": {"spec_baseline_date": baseline},
        "records": records,
    }
    graph_bytes = _json_bytes(document)
    validation_errors = _validate_document(document)
    if validation_errors:
        return ["migration generated an invalid v2 document: " + error for error in validation_errors]
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "legacy-rows.json").write_bytes(archive)
        destination.write_bytes(graph_bytes)
        audit = {
            "schema": "pandu.architecture-cutover/v1",
            "migrated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "spec_baseline_date": baseline,
            "source": {"path": HOMEPAGE_REL.as_posix(), "sha256": _sha256(original)},
            "archive": {"path": archive_relative, "sha256": archive_hash},
            "graph": {
                "path": destination.relative_to(repo).as_posix(),
                "sha256": _sha256(graph_bytes),
            },
            "record_count": len(records),
            "record_ids": [record["id"] for record in records],
            "status": "needs_verification",
            "automatically_current": False,
        }
        (evidence_dir / "cutover-audit.json").write_bytes(_json_bytes(audit))
    except OSError as exc:
        return ["legacy migration failed while writing cutover files: %s" % exc]
    _, check_errors = check(repo)
    if check_errors:
        return _dedup(check_errors)
    try:
        (repo / HOMEPAGE_REL).write_bytes(homepage(records, baseline).encode("utf-8"))
    except OSError as exc:
        return ["legacy migration could not replace the legacy homepage: %s" % exc]
    return []


def _section_32(text):
    lines = text.splitlines()
    start, level = None, None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,4})\s+3\.2(?:\s|$)", line)
        if match:
            start, level = index + 1, len(match.group(1))
            break
    if start is None:
        return ""
    body = []
    for line in lines[start:]:
        heading = re.match(r"^(#{1,6})\s+", line)
        if heading and len(heading.group(1)) <= level:
            break
        body.append(line)
    return re.sub(r"<!--.*?-->", "", "\n".join(body), flags=re.S).strip()


def _placeholder(cell):
    value = cell.strip().strip("`").strip()
    return value.lower() in PLACEHOLDERS or bool(re.fullmatch(r"<[^>]+>", value))


def _split_references(cell):
    if _placeholder(cell):
        return []
    return [part.strip().strip("`").strip() for part in
            re.split(r"<br\s*/?>|[、；;]", cell) if part.strip().strip("`").strip()]


def _split_ids(cell):
    references = _split_references(cell)
    return [part for reference in references
            for part in re.split(r"[,，\s]+", reference) if part]


def _parse_source_cell(cell):
    path, separator, symbol = cell.strip().strip("`").rpartition(":")
    if not separator:
        return {"path": cell.strip().strip("`")}
    source = {"path": path}
    if symbol:
        source["symbol"] = symbol
    return source


def parse_spec_updates(spec_path):
    """Strictly parse the fixed tech-spec 3.2 Markdown table."""
    spec_path = Path(spec_path)
    try:
        text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [], ["cannot read tech-spec %s: %s" % (spec_path, exc)]
    body = _section_32(text)
    if not body or body.strip().strip("`").lower() in PLACEHOLDERS:
        return [], []
    lines = body.splitlines()
    table_lines = [line for line in lines if line.strip().startswith("|")]
    if not table_lines:
        return [], ["%s: non-empty 3.2 must use the structured architecture table" % spec_path.name]
    extra = [line.strip() for line in lines if line.strip() and not line.strip().startswith("|")]
    errors = ["%s: unstructured content in 3.2: %s" % (spec_path.name, line)
              for line in extra]
    if len(table_lines) < 2:
        return [], errors + ["%s: 3.2 table is missing its separator" % spec_path.name]
    header = [re.sub(r"\s+", "", cell.strip().strip("`")).lower()
              for cell in split_row(table_lines[0])]
    expected = ["稳定id", "动作", "名称", "模块", "源代码位置", "supersedes", "验证证据"]
    if header != expected:
        errors.append("%s: 3.2 table header must be | 稳定 ID | 动作 | 名称 | 模块 | 源代码位置 | supersedes | 验证证据 |" % spec_path.name)
    separator = split_row(table_lines[1])
    if len(separator) != len(header) or not all(set(cell) <= set("-: ") and "-" in cell for cell in separator):
        errors.append("%s: invalid 3.2 table separator" % spec_path.name)
    entries = []
    seen = set()
    for line in table_lines[2:]:
        cells = split_row(line)
        if len(cells) != 7:
            errors.append("%s: 3.2 row must contain exactly 7 cells" % spec_path.name)
            continue
        if all(_placeholder(cell) for cell in cells):
            continue
        if _placeholder(cells[0]):
            errors.append("%s: non-empty 3.2 row is missing stable ID" % spec_path.name)
            continue
        ident, action, name, module, source_cell, supersedes_cell, evidence_cell = cells
        ident = ident.strip().strip("`")
        action = action.strip().strip("`").lower()
        if not ID_RE.fullmatch(ident):
            errors.append("%s: invalid stable ID in 3.2: %s" % (spec_path.name, ident))
        if ident in seen:
            errors.append("%s: duplicate stable ID in 3.2: %s" % (spec_path.name, ident))
        seen.add(ident)
        if action not in {"new", "modify", "retire", "describe"}:
            errors.append("%s: %s has unknown action %s (expected new/modify/retire/describe)" % (
                spec_path.name, ident, action))
        for label, value in (("name", name), ("module", module), ("source", source_cell)):
            if _placeholder(value):
                errors.append("%s: %s is missing %s" % (spec_path.name, ident, label))
        source = _parse_source_cell(source_cell)
        if _safe_repo_path(source.get("path")) is None:
            errors.append("%s: %s has unsafe source path" % (spec_path.name, ident))
        evidence = _split_references(evidence_cell)
        if action == "describe":
            if evidence:
                errors.append("%s: %s describe must leave verification evidence empty" % (spec_path.name, ident))
        elif not evidence:
            errors.append("%s: %s is missing verification evidence" % (spec_path.name, ident))
        supersedes = _split_ids(supersedes_cell)
        for target in supersedes:
            if not ID_RE.fullmatch(target):
                errors.append("%s: %s has invalid supersedes ID %s" % (spec_path.name, ident, target))
        entries.append({
            "id": ident, "action": action, "name": name.strip().strip("`"),
            "module": module.strip().strip("`"), "source": source,
            "supersedes": supersedes, "evidence": evidence,
        })
    return entries, _dedup(errors)


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    return match.group(1) if match else ""


def _yaml_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_architecture_updates(issue_path):
    """Parse ticket frontmatter ``architecture-updates: [stable.id, ...]``."""
    issue_path = Path(issue_path)
    try:
        text = issue_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [], ["cannot read issue %s: %s" % (issue_path, exc)]
    frontmatter = _frontmatter(text)
    lines = frontmatter.splitlines()
    values, found = None, False
    inline = False
    errors = []
    for index, line in enumerate(lines):
        match = re.match(r"^architecture-updates:\s*(.*?)\s*$", line)
        if not match:
            continue
        found = True
        raw = re.split(r"\s+#", match.group(1), maxsplit=1)[0].strip()
        if raw:
            inline = True
            values = [raw]
        else:
            values = []
            for following in lines[index + 1:]:
                if re.match(r"^[A-Za-z][\w-]*:", following):
                    break
                item = re.match(r"^\s*-\s*(.*?)\s*$", following)
                if item:
                    values.append(item.group(1))
                elif following.strip() and not following.lstrip().startswith("#"):
                    errors.append("%s: malformed architecture-updates block" % issue_path.name)
        break
    if not found:
        return [], []
    if values is None:
        values = []
    identifiers = []
    if len(values) == 1 and values[0].startswith("["):
        raw = values[0]
        if not raw.endswith("]"):
            return [], ["%s: architecture-updates must be a closed inline list" % issue_path.name]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            inner = raw[1:-1].strip()
            parsed = [] if not inner else [_yaml_scalar(part) for part in inner.split(",")]
        if not isinstance(parsed, list):
            return [], ["%s: architecture-updates must be a list of stable IDs" % issue_path.name]
        identifiers = parsed
    elif inline:
        return [], ["%s: architecture-updates must be an inline or block list of stable IDs" % issue_path.name]
    else:
        identifiers = [_yaml_scalar(re.sub(r"^\s*-\s*", "", value)) for value in values]
    clean = []
    for ident in identifiers:
        if not isinstance(ident, str):
            errors.append("%s: architecture-updates entries must be strings" % issue_path.name)
            continue
        ident = _yaml_scalar(ident.strip())
        if not ID_RE.fullmatch(ident):
            errors.append("%s: invalid architecture-updates stable ID: %s" % (issue_path.name, ident))
            continue
        clean.append(ident)
    duplicates = sorted({ident for ident in clean if clean.count(ident) > 1})
    for ident in duplicates:
        errors.append("%s: duplicate architecture-updates ID: %s" % (issue_path.name, ident))
    return _dedup(clean), errors


def _evidence_matches(reference, evidence):
    if not isinstance(evidence, dict):
        return False
    for key in ("path", "uri"):
        value = evidence.get(key)
        if isinstance(value, str) and (reference == value or reference.startswith(value + ":")):
            return True
    return False


def _spec_metadata(spec_path):
    try:
        frontmatter = _frontmatter(Path(spec_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return {}
    result = {}
    for key in ("status", "supersedes"):
        match = re.search(r"(?m)^%s:\s*(.*?)\s*$" % re.escape(key), frontmatter)
        result[key] = _yaml_scalar(match.group(1).split("#", 1)[0].strip()) if match else ""
    return result


def active_spec_leaves(spec_paths):
    """Return confirmed leaf specs, without letting drafts retire an active design."""
    paths = [Path(path) for path in spec_paths]
    metadata = {path.name: _spec_metadata(path) for path in paths}
    confirmed = {name for name, item in metadata.items() if item.get("status") == "已确认（待拆解）"}
    superseded = {item.get("supersedes") for name, item in metadata.items()
                  if name in confirmed and item.get("supersedes")}
    leaves = sorted(name for name in confirmed if name not in superseded)
    errors = []
    by_name = {path.name: path for path in paths}
    roots = {}
    for name in leaves:
        current, seen = name, set()
        while metadata.get(current, {}).get("supersedes"):
            if current in seen:
                errors.append("tech-spec supersedes cycle reaches %s" % name)
                break
            seen.add(current)
            current = metadata[current]["supersedes"]
            if current not in by_name:
                break
        roots.setdefault(current, []).append(name)
    for root, names in sorted(roots.items()):
        if len(names) > 1:
            errors.append("ambiguous active tech-spec leaves for %s: %s" % (root, ", ".join(names)))
    return leaves, _dedup(errors)


def _heading_body(text, number):
    match = re.search(r"(?ms)^###\s+%s\s+[^\n]*\n(.*?)(?=^###\s+|^##\s+|\Z)" % re.escape(number), text)
    return match.group(1).strip() if match else ""


def check_design(repo, spec_path):
    """Validate design-time structure without requiring tickets or current records."""
    repo, spec_path = Path(repo), Path(spec_path)
    entries, errors = parse_spec_updates(spec_path)
    try:
        text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return ["cannot read tech-spec %s: %s" % (spec_path, exc)]
    for section in ("2.1", "3.6", "3.7"):
        if not _heading_body(text, section):
            errors.append("%s: required design section %s is missing or empty" % (spec_path.name, section))
    metadata = _spec_metadata(spec_path)
    if metadata.get("status") == "已确认（待拆解）":
        leaves, active_errors = active_spec_leaves(spec_path.parent.glob("*-tech-spec-*.md"))
        errors.extend(active_errors)
        if spec_path.name not in leaves:
            errors.append("%s: superseded tech-spec cannot authorize a new ticket group" % spec_path.name)
    records = load_graph_details(repo)["records"]
    by_id = _records_by_id(records)
    for entry in entries:
        if entry["action"] in {"modify", "retire", "describe"} and entry["id"] not in by_id:
            errors.append("%s: %s action %s requires an existing graph record at design time" % (
                spec_path.name, entry["id"], entry["action"]))
        if entry["action"] == "describe" and entry["id"] in by_id:
            errors.extend(_describe_identity_errors(spec_path, entry, by_id[entry["id"]]))
    return _dedup(errors)


def _describe_identity_errors(spec, entry, record):
    source = record.get("source")
    source = source if isinstance(source, dict) else {}
    mismatched = [field for field in ("name", "module") if entry[field] != record.get(field)]
    mismatched.extend("source." + field for field in ("path", "symbol")
                      if entry["source"].get(field) != source.get(field))
    if set(entry["supersedes"]) != set(record.get("supersedes") or []):
        mismatched.append("supersedes")
    return ["%s: %s describe must preserve %s" % (spec.name, entry["id"], field)
            for field in mismatched]


def _spec_issue_paths(repo, spec):
    """Keep independent confirmed specs in one feature in distinct ticket groups."""
    import ticket_context

    feature = spec.resolve().parent
    issues = sorted((feature / "issues").glob("issue-*.md"))
    leaves, errors = active_spec_leaves(feature.glob("*-tech-spec-*.md"))
    if len(leaves) <= 1:
        return issues, errors
    selected = []
    for issue in issues:
        try:
            if ticket_context.find_spec(issue, _ticket_document_root(repo, issue)) == spec.resolve():
                selected.append(issue)
        except ticket_context.ContextError as exc:
            errors.append("%s: %s" % (issue.name, exc))
    return selected, errors


def check_flow_forward(repo, spec_path, issue_paths=None, head="HEAD", implementation_ids=None,
                       allow_flow_back=False, validated=None, feature_issue_ids=None):
    """Reconcile assignments for every ID and implementation for the selected IDs.

    ``implementation_ids=None`` is the feature-level full check.  A ticket passes
    its own declared IDs so unfinished work owned by later tickets cannot block it.
    """
    repo, spec_path = Path(repo), Path(spec_path)
    entries, errors = parse_spec_updates(spec_path)
    if not entries:
        if issue_paths is not None:
            for issue_path in issue_paths:
                identifiers, issue_errors = parse_architecture_updates(issue_path)
                errors.extend(issue_errors)
                for ident in identifiers:
                    errors.append("%s: ticket declares %s but spec 3.2 is empty" % (
                        spec_path.name, ident))
        return _dedup(errors)
    if issue_paths is None:
        issue_paths, group_errors = _spec_issue_paths(repo, spec_path)
        errors.extend(group_errors)
    declarations = {}
    for issue_path in issue_paths:
        identifiers, issue_errors = parse_architecture_updates(issue_path)
        errors.extend(issue_errors)
        for ident in identifiers:
            declarations.setdefault(ident, []).append(Path(issue_path))
    expected_ids = {entry["id"] for entry in entries}
    entries_by_id = {entry["id"]: entry for entry in entries}
    flow_back_existing = set()
    if allow_flow_back:
        flow_back_existing = {
            ident for ident, record in _records_by_id(load_graph_details(repo)["records"]).items()
            if entries_by_id.get(ident, {}).get("action") in {"modify", "retire"}
        }
    for ident in sorted(expected_ids):
        tickets = declarations.get(ident, [])
        if not tickets and implementation_ids is not None and ident in flow_back_existing:
            continue
        if not tickets:
            errors.append("%s: %s has no ticket architecture-updates declaration" % (spec_path.name, ident))
        elif len(tickets) > 1:
            errors.append("%s: %s is declared by multiple tickets: %s" % (
                spec_path.name, ident, ", ".join(path.name for path in tickets)))
    for ident in sorted(set(declarations) - expected_ids):
        errors.append("%s: ticket declares wrong or out-of-spec architecture ID: %s" % (
            spec_path.name, ident))
    if validated is None:
        records, graph_errors = check(repo, head=head)
    else:
        records = validated["records"]
        graph_errors = list(validated.get("errors", []))
    errors.extend(graph_errors)
    details = load_graph_details(repo)
    by_id = _records_by_id(records)
    selected = None if implementation_ids is None else set(implementation_ids)
    describe_owners = {}
    for entry in entries:
        ident = entry["id"]
        if selected is not None and ident not in selected:
            continue
        record = by_id.get(ident)
        if record is None:
            errors.append("%s: %s is missing from docs/architecture/*.json" % (spec_path.name, ident))
            continue
        if not details["shards"].get(ident):
            errors.append("%s: %s has no owning graph shard" % (spec_path.name, ident))
        if entry["action"] == "describe":
            errors.extend(_describe_identity_errors(spec_path, entry, record))
            owners = declarations.get(ident, [])
            if selected is None and len(owners) == 1:
                describe_owners.setdefault(owners[0], set()).add(ident)
            continue
        for field in ("name", "module"):
            if record.get(field) != entry[field]:
                errors.append("%s: %s %s does not match tech-spec" % (spec_path.name, ident, field))
        source_value = record.get("source")
        record_source = source_value if isinstance(source_value, dict) else {}
        for field in ("path", "symbol"):
            expected = entry["source"].get(field)
            if expected is not None and record_source.get(field) != expected:
                errors.append("%s: %s source.%s does not match tech-spec" % (
                    spec_path.name, ident, field))
        record_supersedes = record.get("supersedes")
        if not isinstance(record_supersedes, list) or any(not isinstance(item, str) for item in record_supersedes):
            record_supersedes = []
        if set(record_supersedes) != set(entry["supersedes"]):
            errors.append("%s: %s supersedes does not match tech-spec" % (spec_path.name, ident))
        if entry["action"] in {"new", "modify"}:
            if record.get("status") != "current":
                errors.append("%s: %s action %s requires current record" % (
                    spec_path.name, ident, entry["action"]))
        else:
            expected_status = "superseded" if entry["supersedes"] else "retired"
            if record.get("status") != expected_status:
                errors.append("%s: %s retire action requires %s record" % (
                    spec_path.name, ident, expected_status))
        for target in entry["supersedes"]:
            target_record = by_id.get(target)
            if target_record is None:
                errors.append("%s: %s supersedes target is absent: %s" % (spec_path.name, ident, target))
            elif target_record.get("status") not in {"superseded", "retired"}:
                errors.append("%s: superseded target remains active: %s" % (spec_path.name, target))
        record_evidence = record.get("evidence")
        if not isinstance(record_evidence, list):
            record_evidence = []
        for reference in entry["evidence"]:
            if not any(_evidence_matches(reference, item) for item in record_evidence):
                errors.append("%s: %s lacks declared verification evidence: %s" % (
                    spec_path.name, ident, reference))
        if entry["action"] in {"new", "modify"}:
            source = record_source
            if not any(
                isinstance(item, dict) and item.get("type") == "code"
                and item.get("path") == source.get("path")
                and item.get("sha256") == source.get("sha256")
                and item.get("commit") == record.get("verified_commit")
                for item in record_evidence
            ):
                errors.append("%s: %s lacks code evidence for the implemented interface" % (
                    spec_path.name, ident))
    for owner, describe_ids in describe_owners.items():
        errors.extend(_describe_feature_errors(repo, spec_path, owner, describe_ids, head, by_id,
                                               details["shards"], feature_issue_ids))
    return _dedup(errors)


def _diff_name_status(repo, base, head):
    try:
        result = _git(repo, "diff", "--name-status", "-z", "-M", base, head, "--")
    except (OSError, subprocess.SubprocessError) as exc:
        return [], ["cannot diff %s..%s: %s" % (base, head, exc)]
    if result.returncode != 0:
        return [], ["cannot diff %s..%s" % (base, head)]
    parts = result.stdout.split(b"\0")
    entries, index = [], 0
    while index < len(parts):
        if not parts[index]:
            index += 1
            continue
        status = parts[index].decode("ascii", errors="replace")
        index += 1
        if index >= len(parts) or not parts[index]:
            break
        old = parts[index].decode("utf-8", errors="replace")
        index += 1
        new = old
        if status.startswith(("R", "C")):
            if index >= len(parts) or not parts[index]:
                break
            new = parts[index].decode("utf-8", errors="replace")
            index += 1
        entries.append((status, old, new))
    return entries, []


def _verification_fingerprint(record):
    if record is None:
        return None
    return (
        record.get("verification"), record.get("evidence"), record.get("last_verified"),
        record.get("last_verified_version"), record.get("verified_commit"), record.get("source"),
        record.get("sources"),
    )


def _impl_log_path(issue_path):
    try:
        text = issue_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    frontmatter = _frontmatter(text)
    match = re.search(r"(?m)^issue-id:\s*([^\s#]+)", frontmatter)
    issue_id = match.group(1) if match else issue_path.stem
    feature = issue_path.parent.parent if issue_path.parent.name == "issues" else issue_path.parent
    candidates = [
        feature / "impl" / issue_id / ("impl-log-%s.md" % issue_id),
        feature / "impl" / ("impl-log-%s.md" % issue_id),
        issue_path.parent / ("impl-log-%s.md" % issue_id),
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def _impl_mapping_errors(issue_path, ident, source):
    path = _impl_log_path(issue_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ["%s: impl-log is missing redline interface-to-code mapping: %s" % (ident, path)]
    match = re.search(r"(?ms)^##\s+红线接触记录\s*$\n(.*?)(?=^##\s+|\Z)", text)
    body = match.group(1) if match else ""
    source_path = source.get("path", "")
    symbol = source.get("symbol", "")
    for line in body.splitlines():
        if ident in line and source_path in line and (not symbol or symbol in line):
            return []
    return ["%s: impl-log 红线接触记录 must map the stable ID to %s%s" % (
        ident, source_path, ":" + symbol if symbol else "")]


def _describe_blob(repo, revision, path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member %s" % key)
            result[key] = value
        return result

    tree = _git(repo, "ls-tree", revision, "--", path, text=True)
    if tree.returncode or not tree.stdout.startswith("100644 blob "):
        raise ValueError("describe requires a regular non-executable graph shard")
    raw = _read_git_object(repo, revision, path)
    if raw is None:
        raise ValueError("missing graph shard")
    return json.loads(raw, object_pairs_hook=unique)


def _describe_delta_errors(repo, base, head, describe_ids):
    """Compare complete committed shard values, masking only declared responsibility strings."""
    entries, errors = _diff_name_status(repo, base, head)
    changed = set()
    for status, old, path in entries:
        if status != "M" or old != path:
            errors.append("describe cannot add, delete, rename or change file type: %s" % path)
            continue
        if path == HOMEPAGE_REL.as_posix():
            for revision in (base, head):
                tree = _git(repo, "ls-tree", revision, "--", path, text=True)
                if tree.returncode or not tree.stdout.startswith("100644 blob "):
                    errors.append("describe requires a regular non-executable generated homepage")
            continue
        graph_path = PurePosixPath(path)
        if graph_path.parent != GRAPH_REL or graph_path.suffix != ".json":
            errors.append("describe may only change graph shards and the generated homepage: %s" % path)
            continue
        try:
            before = _describe_blob(repo, base, path)
            after = _describe_blob(repo, head, path)
            revised = set()
            for left, right in zip(before["records"], after["records"]):
                ident = left["id"]
                if (ident not in describe_ids or right.get("id") != ident
                        or left.get("responsibility") == right.get("responsibility")):
                    continue
                if not isinstance(right["responsibility"], str) or not right["responsibility"].strip():
                    errors.append("describe requires nonempty responsibility text: %s" % ident)
                revised.add(ident)
                right["responsibility"] = left["responsibility"]
            if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
                errors.append("describe changed fields other than declared responsibility: %s" % path)
            if not revised:
                errors.append("describe shard contains no declared responsibility change: %s" % path)
            changed.update(revised)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
            errors.append("describe cannot compare %s: %s" % (path, exc))
    for missing in sorted(describe_ids - changed):
        errors.append("describe did not change declared responsibility: %s" % missing)
    return _dedup(errors)


def _verified_describe_merge(repo, spec, issue, describe_ids, head, merged):
    """Bind a describe ticket to its frozen inputs and exact no-ff merge."""
    import ticket_context

    root = _ticket_document_root(repo, issue)
    issue_id = ticket_context.parse_issue(issue)["frontmatter"]["issue-id"]
    context = ticket_context.load_context(issue.parent.parent / "impl" / issue_id / "ticket-context.json")
    declared, errors = parse_architecture_updates(issue)
    if (errors or set(declared) != describe_ids or set(context["architecture_updates"]) != describe_ids
            or context["issue_path"] != issue.relative_to(root).as_posix()
            or context["spec_path"] != Path(spec).resolve().relative_to(root).as_posix()
            or context["hashes"]["issue"] != ticket_context.issue_content_hash(issue)
            or context["hashes"]["spec"] != _sha256(Path(spec).read_bytes())):
        raise ValueError("describe-only ticket declaration or frozen inputs differ")
    merge = merged.get(issue_id)
    if not isinstance(merge, str) or not SHA_RE.fullmatch(merge):
        raise ValueError("describe ticket has no recorded merge")
    parents = _git(repo, "rev-list", "--parents", "-n", "1", merge, text=True)
    if (parents.returncode
            or parents.stdout.split() != [merge, context["target_commit"], context["source_head"]]
            or _resolve_tree(repo, merge) != context["source_tree"]
            or _is_ancestor(repo, merge, head) is not True):
        raise ValueError("describe merge does not match the frozen ticket and feature head")
    return merge, _describe_delta_errors(repo, context["target_commit"], merge, describe_ids)


def _describe_successors(repo, feature, describe_ids, chain, merged, feature_issue_ids):
    import ticket_context

    successors = {}
    leaves, errors = active_spec_leaves(feature.glob("*-tech-spec-*.md"))
    if errors:
        raise ValueError("; ".join(errors))
    commits = set(chain)
    for issue in sorted((feature / "issues").glob("issue-*.md")):
        declared, declaration_errors = parse_architecture_updates(issue)
        ids = set(declared)
        if declaration_errors or not ids & describe_ids:
            continue
        issue_id = ticket_context.parse_issue(issue)["frontmatter"]["issue-id"]
        claimed = merged.get(issue_id)
        if claimed not in commits or (feature_issue_ids is not None and issue_id not in feature_issue_ids):
            continue
        spec = ticket_context.find_spec(issue, _ticket_document_root(repo, issue))
        if spec.name not in leaves:
            continue
        entries, parse_errors = parse_spec_updates(spec)
        spec_ids = {entry["id"] for entry in entries if entry["action"] == "describe"}
        if parse_errors or not ids <= spec_ids:
            continue
        merge, errors = _verified_describe_merge(repo, spec, issue, ids, chain[-1], merged)
        if errors:
            raise ValueError("%s: %s" % (issue.name, "; ".join(errors)))
        if merge in successors:
            raise ValueError("multiple describe tickets claim the same merge: %s" % merge)
        successors[merge] = ids
    return successors


def _describe_records(repo, commit, describe_ids):
    details = load_graph_details(repo, revision=commit)
    records = _records_by_id(details["records"])
    return {ident: (json.dumps(records.get(ident), sort_keys=True), tuple(details["shards"].get(ident, [])))
            for ident in describe_ids}


def _describe_feature_errors(repo, spec, issue, describe_ids, head, final_records, final_shards,
                             feature_issue_ids):
    """Check every change between the owning merge and the final record."""
    import feature_closeout

    issue = Path(issue).resolve()
    try:
        merged = feature_closeout.last_merged(issue.parent.parent)
        merge, errors = _verified_describe_merge(repo, spec, issue, describe_ids, head, merged)
        if errors:
            return errors
        result = _git(repo, "rev-list", "--first-parent", "--reverse", merge + ".." + head, text=True)
        if result.returncode:
            raise ValueError("cannot read describe first-parent chain")
        chain = result.stdout.split()
        if ((chain and _resolve_commit(repo, chain[0] + "^1") != merge)
                or (not chain and _resolve_commit(repo, head) != merge)):
            raise ValueError("describe merge is not on the feature's first-parent chain")
        successors = _describe_successors(repo, issue.parent.parent, describe_ids, chain, merged,
                                         feature_issue_ids) if chain else {}
        previous = merge
        state = _describe_records(repo, merge, describe_ids)
        verified = state.copy()
        for commit in chain:
            diff, diff_errors = _diff_name_status(repo, previous, commit)
            errors.extend(diff_errors)
            previous = commit
            if not any(PurePosixPath(path).parent == GRAPH_REL for _, old, new in diff for path in (old, new)):
                continue
            actual = _describe_records(repo, commit, describe_ids)
            allowed = successors.get(commit, ())
            for ident in sorted(describe_ids):
                if actual[ident] == state[ident]:
                    continue
                if ident in allowed:
                    verified[ident] = actual[ident]
                else:
                    errors.append("describe record changed outside a verified describe merge: %s at %s" %
                                  (ident, commit))
            state = actual
        for ident in sorted(describe_ids):
            final = (json.dumps(final_records.get(ident), sort_keys=True), tuple(final_shards.get(ident, [])))
            if verified[ident] != final:
                errors.append("describe record differs from its verified wording merge: %s" % ident)
        return errors
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError,
            feature_closeout.InputError) as exc:
        return ["%s: describe feature validation: %s" % (issue.name, exc)]

def check_flow_back(repo, issue_path, base, head, spec_path=None, validated=None):
    """Check implementation diffs back to the issue's current spec and graph."""
    repo, issue_path = Path(repo), Path(issue_path)
    if validated is None:
        records, errors = check(repo, head=head, base=base)
    else:
        records = validated["records"]
        errors = list(validated.get("errors", []))
    base_details = load_graph_details(repo, revision=base)
    head_details = load_graph_details(repo, revision=head)
    if not head_details["files"] and load_graph_details(repo)["files"]:
        head_details = load_graph_details(repo)
    base_by_id = _records_by_id(base_details["records"])
    head_by_id = _records_by_id(head_details["records"])
    diff_entries, diff_errors = _diff_name_status(repo, base, head)
    errors.extend(diff_errors)
    changed_paths = {path for _, old, new in diff_entries for path in (old, new)}
    declared, issue_errors = parse_architecture_updates(issue_path)
    errors.extend(issue_errors)
    declared_set = set(declared)
    if spec_path is None:
        # The issue is the authority for its current dated spec.  Never union
        # every *-tech-spec-*.md in the feature: an old spec can neither
        # authorize nor block the current candidate.
        import ticket_context
        try:
            document_root = _ticket_document_root(repo, issue_path)
        except ticket_context.ContextError as exc:
            return _dedup(errors + [str(exc)])
        try:
            spec_path = ticket_context.find_spec(issue_path, document_root)
        except ticket_context.ContextError as exc:
            if document_root != Path(repo).resolve():
                return _dedup(errors + [str(exc)])
            feature = issue_path.parent.parent if issue_path.parent.name == "issues" else issue_path.parent
            candidates = sorted(feature.glob("*-tech-spec-*.md"))
            if len(candidates) == 1:
                spec_path = candidates[0]
            else:
                errors.append("issue must name exactly one current tech-spec; dated spec inference is forbidden")
                return _dedup(errors)
    spec_path = Path(spec_path)
    entries, spec_errors = parse_spec_updates(spec_path)
    errors.extend(spec_errors)
    describe_ids = {entry["id"] for entry in entries
                    if entry["action"] == "describe" and entry["id"] in declared_set}
    if describe_ids:
        if declared_set != describe_ids:
            errors.append("describe declarations cannot be mixed with other actions in one ticket")
        errors.extend(_describe_delta_errors(repo, base, head, describe_ids))
        return _dedup(errors)
    impacted = {}
    for ident in sorted(set(base_by_id) | set(head_by_id)):
        before, after = base_by_id.get(ident), head_by_id.get(ident)
        candidate = after or before
        if not isinstance(candidate, dict) or candidate.get("kind") != "interface":
            continue
        sources = [source for record in (before, after) if record
                   for _, source in _record_sources(record)]
        paths = {source.get("path") for source in sources if source.get("path")}
        # The generated index is verified byte-for-byte by render_homepage.
        # Its migration-era citations do not bind implementation code.
        paths.discard(HOMEPAGE_REL.as_posix())
        hits = [entry for entry in diff_entries
                if entry[1] in paths or entry[2] in paths]
        if hits:
            impacted[ident] = (before, after, hits)
    for ident, (before, after, hits) in impacted.items():
        shards = set(base_details["shards"].get(ident, [])) | set(head_details["shards"].get(ident, []))
        if not shards & changed_paths:
            errors.append("%s: bound source changed without updating its graph shard in the same diff" % ident)
        if after is None:
            errors.append("%s: graph record disappeared after bound source changed" % ident)
            continue
        source_before = before.get("source", {}) if before and isinstance(before.get("source"), dict) else {}
        deleted_or_renamed = any(
            status.startswith(("D", "R")) and old == source_before.get("path")
            for status, old, _ in hits
        )
        if deleted_or_renamed and after.get("status") not in {"retired", "superseded"}:
            errors.append("%s: deleted/renamed source requires retired or superseded record" % ident)
        if before is not None and _verification_fingerprint(before) == _verification_fingerprint(after):
            errors.append("%s: verification information was not refreshed with the bound source change" % ident)
        if ident not in declared_set:
            selected = before or after
            source = selected.get("source", {}) if isinstance(selected.get("source"), dict) else {}
            errors.extend(_impl_mapping_errors(issue_path, ident, source))
    spec_ids = {entry["id"] for entry in entries}
    all_changed_ids = {
        ident for ident in set(base_by_id) | set(head_by_id)
        if base_by_id.get(ident) != head_by_id.get(ident)
    }
    graph_paths = {path for path in changed_paths
                   if PurePosixPath(path).parent.as_posix() == GRAPH_REL.as_posix()
                   and path.endswith(".json")}
    for ident in sorted(all_changed_ids - set(impacted)):
        if ident not in declared_set and ident not in spec_ids:
            errors.append("%s: graph-only change has neither ticket nor tech-spec architecture obligation" % ident)
    if graph_paths and not all_changed_ids and not declared_set and not spec_ids:
        errors.append("graph-only shard change has no ticket or tech-spec architecture obligation")
    return _dedup(errors)


def _frozen_candidate_flow(repo, issue, base, head, raw):
    """Authenticate the supervisor's final tree and this ticket's bounded merge."""
    import feature_closeout
    import ticket_context
    import verify_issue

    binding = json.loads(raw)
    if not isinstance(binding, dict) or set(binding) != {
            "candidate_path", "candidate_sha256", "issue_id"}:
        raise ValueError("invalid candidate flow binding")
    path = Path(binding["candidate_path"]).resolve()
    primary = _ticket_document_root(repo, issue)
    feature, _ = verify_issue._candidate_location(path)
    if feature != issue.parent.parent or verify_issue._find_root(path) != primary:
        raise ValueError("candidate and ticket are not in the same primary feature")
    candidate_bytes = path.read_bytes()
    if hashlib.sha256(candidate_bytes).hexdigest() != binding["candidate_sha256"]:
        raise ValueError("frozen candidate digest mismatch")
    candidate, _ = verify_issue._candidate_document(path, candidate_bytes)
    requested_head = verify_issue._git(repo, "rev-parse", "--verify", head + "^{commit}").stdout.strip()
    if requested_head != candidate["candidate"]:
        raise ValueError("check-flow HEAD differs from frozen candidate")
    repo_error = verify_issue._candidate_repo_error(repo, candidate)
    if repo_error:
        raise ValueError(repo_error)
    issue_id = ticket_context.parse_issue(issue)["frontmatter"].get("issue-id")
    ticket = next((item for item in candidate["issues"] if item["id"] == issue_id), None)
    if issue_id != binding["issue_id"] or ticket is None:
        raise ValueError("ticket is not the requested frozen candidate member")
    context, _ = verify_issue._candidate_context(feature, primary, issue_id)
    if context["issue_path"] != issue.relative_to(primary).as_posix() or context["base"] != base:
        raise ValueError("check-flow ticket or base differs from frozen ticket context")
    merged = ticket.get("merged_commit")
    try:
        recorded_merge = feature_closeout.last_merged(feature).get(issue_id)
    except feature_closeout.InputError as exc:
        raise ValueError("cannot authenticate ticket merge event: " + str(exc)) from exc
    if not isinstance(merged, str) or not SHA_RE.fullmatch(merged) or recorded_merge != merged:
        raise ValueError("ticket merge differs from the recorded merge event")
    parents = verify_issue._git(repo, "rev-list", "--parents", "-n", "1", merged).stdout.split()
    if parents != [merged, context["target_commit"], context["source_head"]]:
        raise ValueError("ticket merge parents differ from the frozen target and source")
    if (verify_issue._git(repo, "rev-parse", merged + "^{tree}").stdout.strip() != context["source_tree"]
            or verify_issue._git(repo, "merge-base", "--is-ancestor", merged, candidate["candidate"],
                                 check=False).returncode):
        raise ValueError("ticket merge tree is stale or outside the frozen candidate")
    return candidate["candidate"], (parents[1], merged), {item["id"] for item in candidate["issues"]}


def _check_frozen_candidate_flow(repo, issue, spec, base, head, raw):
    try:
        candidate, (ticket_base, ticket_merge), issue_ids = _frozen_candidate_flow(repo, issue, base, head, raw)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return ["candidate flow: " + str(exc)]
    records, errors = check(repo, head=candidate)
    validated = {"records": records, "errors": []}
    errors.extend(check_flow_forward(repo, spec, head=candidate, validated=validated,
                                     feature_issue_ids=issue_ids))
    errors.extend(check_flow_back(repo, issue, ticket_base, ticket_merge, spec_path=spec,
                                  validated=validated))
    _, homepage_errors = render_homepage(repo, write=False, head=candidate, validated=validated)
    return _dedup(errors + homepage_errors)


def _print_errors(errors):
    for error in _dedup(errors):
        print("FAIL " + error)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate, query, migrate and render architecture graph v2")
    parser.add_argument("command", nargs="?", choices=("check", "render", "migrate-legacy", "check-design", "check-flow", "check-feature"))
    parser.add_argument("action", nargs="?", choices=("write", "check"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--base")
    parser.add_argument("--issue", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--module")
    parser.add_argument("--interface")
    parser.add_argument("--version")
    parser.add_argument("--status", choices=sorted(STATUSES))
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if args.command == "render":
        if args.action not in {"write", "check"}:
            parser.error("render requires write or check")
        _, errors = render_homepage(repo, write=args.action == "write", head=args.head)
        _print_errors(errors)
        if not errors:
            print("homepage %s: docs/ARCHITECTURE.md" % ("written" if args.action == "write" else "current"))
        return 1 if errors else 0
    if args.action is not None:
        parser.error("render is the only command taking write/check")
    if args.command == "migrate-legacy":
        errors = migrate_legacy(repo)
        _print_errors(errors)
        if not errors:
            print("legacy migration complete; all records remain needs_verification")
        return 1 if errors else 0
    if args.command == "check-design":
        if args.spec is None:
            parser.error("check-design requires --spec")
        spec = args.spec if args.spec.is_absolute() else repo / args.spec
        errors = check_design(repo, spec)
        _print_errors(errors)
        print("design errors=%d" % len(_dedup(errors)))
        return 1 if errors else 0
    if args.command == "check-feature":
        if args.spec is None or args.feature_dir is None:
            parser.error("check-feature requires --spec and --feature-dir")
        spec = args.spec if args.spec.is_absolute() else repo / args.spec
        feature = args.feature_dir if args.feature_dir.is_absolute() else repo / args.feature_dir
        leaves, errors = active_spec_leaves(feature.glob("*-tech-spec-*.md"))
        if spec.name not in leaves:
            errors.append("%s: not an active confirmed leaf tech-spec" % spec.name)
        if spec.resolve().parent != feature.resolve():
            errors.append("tech-spec must belong to the checked feature directory")
        errors.extend(check_flow_forward(repo, spec, head=args.head))
        _, homepage_errors = render_homepage(repo, write=False, head=args.head)
        errors.extend(homepage_errors)
        _print_errors(errors)
        print("feature flow errors=%d" % len(_dedup(errors)))
        return 1 if errors else 0
    if args.command == "check-flow":
        if args.issue is None or not args.base:
            parser.error("check-flow requires --issue and --base")
        issue = args.issue if args.issue.is_absolute() else repo / args.issue
        import ticket_context
        try:
            spec = ticket_context.find_spec(issue, _ticket_document_root(repo, issue))
        except ticket_context.ContextError as exc:
            _print_errors([str(exc)])
            return 1
        import verify_issue
        candidate_flow = os.environ.get(verify_issue.CANDIDATE_FLOW_ENV)
        if candidate_flow is not None:
            errors = _check_frozen_candidate_flow(repo, issue.resolve(), spec, args.base, args.head,
                                                   candidate_flow)
            _print_errors(errors)
            print("flow errors=%d" % len(errors))
            return 1 if errors else 0
        declared, declaration_errors = parse_architecture_updates(issue)
        if not declared and not declaration_errors:
            entries, diff_errors = _diff_name_status(repo, args.base, args.head)
            if diff_errors:
                _print_errors(diff_errors)
                return 1
            changed = {path for _, old, new in entries for path in (old, new)}
            architecture_changed = any(path == HOMEPAGE_REL.as_posix() or
                                       path.startswith(GRAPH_REL.as_posix() + "/") for path in changed)
            bound_changed = any(source.get("path") in changed
                                for record in load_graph_details(repo)["records"]
                                if isinstance(record, dict) and record.get("kind") == "interface"
                                for _, source in _record_sources(record))
            if not architecture_changed and not bound_changed:
                print("ticket-level architecture gate skipped (full check runs at feature candidate)")
                return 0
        records, candidate_errors, baseline_errors = check_candidate(repo, head=args.head, base=args.base)
        if baseline_errors:
            print("baseline graph errors=%d (reported separately)" % len(baseline_errors))
        validated = {"records": records, "errors": []}
        errors = declaration_errors + candidate_errors
        errors.extend(check_flow_forward(
            repo, spec, head=args.head, implementation_ids=set(declared), allow_flow_back=True,
            validated=validated))
        errors.extend(check_flow_back(repo, issue, args.base, args.head, spec_path=spec,
                                      validated=validated))
        _, homepage_errors = render_homepage(repo, write=False, head=args.head, validated=validated)
        errors.extend(homepage_errors)
        _print_errors(errors)
        print("flow errors=%d" % len(_dedup(errors)))
        return 1 if errors else 0
    if args.command == "check" or args.check:
        records, errors = check(repo, head=args.head, base=args.base)
        _print_errors(errors)
        print("graph records=%d errors=%d" % (len(records), len(_dedup(errors))))
        return 1 if errors else 0
    records, errors = check(repo, head=args.head, base=args.base)
    legacy_errors = []
    if legacy_rows(repo):
        legacy_errors = (["runtime dual source is forbidden: regenerate the homepage after one-time migration"]
                         if records else ["migration required: run migrate-legacy once before querying legacy rows"])
    errors.extend(legacy_errors)
    if errors:
        _print_errors(errors)
        return 1
    selected = select(records, module=args.module, interface=args.interface,
                      version=args.version, status=args.status)
    print(json.dumps(selected, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
