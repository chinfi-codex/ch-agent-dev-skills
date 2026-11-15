#!/usr/bin/env python3
"""Read-only v2 ticket policy aggregation for review, merge and close.

The frozen ``ticket_context`` is the only source of base/target/scope/DoD/spec
identity.  Evidence is accepted only as verification JSON plus hashed raw logs;
Markdown evidence is a deterministic rendering checked against that JSON.

CLI: process_check.py --issue ISSUE --repo WORKTREE --phase review|merge|close
                     [--json OUTPUT]

The ticket worktree is never written.  Generated-refresh commands run only in a
fresh disposable detached worktree and must reproduce the exact HEAD tree.
Other scripts historically import the small parsing/git helpers at the bottom
of this file; those compatibility helpers are not a second v2 policy engine.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

DEFAULT_NON_PRODUCTION = [
    "tests/**", "**/tests/**", "test/**", "**/test/**", "**/test_*", "**/*_test.*",
    "**/*.test.*", "**/*.spec.*", "docs/**", "dev/**", "**/*.md",
]
TEST_PATTERNS = [
    "tests/**", "**/tests/**", "test/**", "**/test/**", "**/test_*", "**/*_test.*",
    "**/*.test.*", "**/*.spec.*",
]
ARCHITECTURE_PATTERNS = ["docs/ARCHITECTURE.md", "docs/architecture/**"]
SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".m", ".mm", ".php", ".py", ".rb", ".rs", ".scala",
    ".sh", ".sql", ".swift", ".ts", ".tsx", ".vue",
}
VERIFICATION_SCHEMA = "agent-dev.verification/v2"
RECEIPT_SCHEMA = "agent-dev.review-receipt/v2"
MANIFEST_SCHEMA = "agent-dev.artifact-generation/v2"
MANIFEST_NAME = "artifact-generation-manifest.json"


def _sibling(name: str):
    directory = str(Path(__file__).resolve().parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module(name)


class InputError(Exception):
    pass


# Compatibility parsing API for dispatch_config/feature_closeout/session_title.
# v2 policy itself calls agents_config/ticket_context directly and never parses
# a second, permissive copy of ticket or config YAML.
def parse_yaml_subset(text: str) -> dict:
    try:
        return _sibling("agents_config").parse_yaml_subset(text)
    except (ImportError, ValueError) as exc:
        raise InputError(str(exc))


def read_frontmatter(path: Path) -> dict:
    try:
        return _sibling("agents_config").read_frontmatter(path)
    except (ImportError, ValueError) as exc:
        raise InputError(str(exc))


def read_first_yaml_block(path: Path) -> dict:
    try:
        return _sibling("agents_config").read_first_yaml_block(path)
    except (ImportError, ValueError) as exc:
        raise InputError(str(exc))


def md_section(text: str, title: str) -> str:
    match = re.search(r"^##\s+[^\n]*%s[^\n]*\n(.*?)(?=^#{1,2}\s|\Z)" % re.escape(title), text, re.S | re.M)
    return match.group(1) if match else ""


# ---------- glob and Git ----------

def glob_to_regex(pattern: str) -> re.Pattern:
    index, out = 0, []
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return re.compile("".join(out) + r"\Z")


def match_any(path: str, patterns):
    for pattern in patterns or []:
        pattern = str(pattern).strip()
        if not pattern:
            continue
        if glob_to_regex(pattern).match(path) or (pattern.endswith("/") and path.startswith(pattern)) or fnmatch.fnmatch(path, pattern):
            return pattern
    return None


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode:
        raise InputError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout


def _repo_root(repo: Path) -> Path:
    return Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()


def _head_tree(repo: Path):
    head = git(repo, "rev-parse", "HEAD").strip()
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise InputError("HEAD/tree must be complete 40-character Git OIDs")
    return head, tree


def changed_files(repo: Path, base: str, head: str):
    """Return status/old_path/path/added/deleted, retaining both rename sides."""
    status_tokens = git(repo, "diff", "--name-status", "-z", "-M", base, head, "--").split("\0")
    statuses = {}
    index = 0
    while index < len(status_tokens):
        status = status_tokens[index]
        if not status:
            break
        if status.startswith("R"):
            old, new = status_tokens[index + 1], status_tokens[index + 2]
            statuses[(old, new)] = {"status": status, "old_path": old, "path": new}
            index += 3
        else:
            path = status_tokens[index + 1]
            statuses[(None, path)] = {"status": status, "old_path": None, "path": path}
            index += 2
    tokens = git(repo, "diff", "--numstat", "-z", "-M", base, head, "--").split("\0")
    files = []
    index = 0
    while index < len(tokens):
        if not tokens[index]:
            break
        added, deleted, path = tokens[index].split("\t", 2)
        if path == "":
            old, new = tokens[index + 1], tokens[index + 2]
            key = (old, new)
            index += 3
        else:
            key = (None, path)
            index += 1
        entry = statuses.get(key)
        if entry is None:
            # Renames with no numstat still appear in name-status; ordinary entries
            # must always join exactly. Never recover by basename or path prefix.
            matches = [v for (old, new), v in statuses.items() if new == path and old is None]
            if len(matches) != 1:
                raise InputError("cannot join exact name-status/numstat entry for %r" % path)
            entry = matches[0]
        files.append({
            "status": entry["status"], "old_path": entry["old_path"], "path": entry["path"],
            "added": 0 if added == "-" else int(added),
            "deleted": 0 if deleted == "-" else int(deleted),
        })
    for entry in statuses.values():
        if not any(f["path"] == entry["path"] and f["old_path"] == entry["old_path"] for f in files):
            files.append(dict(entry, added=0, deleted=0))
    return sorted(files, key=lambda item: (item["path"], item["old_path"] or ""))


def _find_root(issue: Path) -> Path:
    for parent in issue.resolve().parents:
        if (parent / "dev" / "agents-config.md").is_file():
            return parent
    raise InputError("cannot find dev/agents-config.md above issue")


def _integration_ticket(issue: Path, issue_id: str, parsed_issue) -> bool:
    """Use ticket_context.parse_issue for successor semantics, never raw YAML."""
    for candidate in sorted(issue.parent.glob("issue-*.md")):
        if candidate.resolve() == issue.resolve():
            continue
        try:
            other = parsed_issue(candidate)
            deps = other.get("frontmatter", {}).get("blocked-by") or []
            if isinstance(deps, str):
                deps = [deps]
            if issue_id in [str(dep) for dep in deps]:
                return False
        except (ValueError, OSError):
            continue
    return True


def _integration_leaf_count(issue: Path, parsed_issue) -> int:
    count = 0
    for candidate in sorted(issue.parent.glob("issue-*.md")):
        ident = str(parsed_issue(candidate).get("frontmatter", {}).get("issue-id") or "")
        if _integration_ticket(candidate, ident, parsed_issue):
            count += 1
    return count


def target_branch(config: dict, fm: dict, feature_dir=None) -> str:
    """Compatibility delegate; ticket_context remains the only v2 rule."""
    try:
        return _sibling("ticket_context").target_branch(config, fm, feature_dir)
    except (ImportError, ValueError) as exc:
        raise InputError(str(exc))


class Report:
    def __init__(self):
        self.items = []

    def add(self, check, level, msg):
        self.items.append({"check": check, "level": level, "msg": msg})

    def extend(self, check, messages):
        for message in messages:
            self.add(check, "FAIL", message)

    @property
    def failed(self):
        return any(item["level"] == "FAIL" for item in self.items)


def _load_context(issue: Path, repo: Path):
    try:
        tc = _sibling("ticket_context")
        ac = _sibling("agents_config")
    except ImportError as exc:
        raise InputError("shared ticket_context/agents_config are required: %s" % exc)
    parsed = tc.parse_issue(issue)
    issue_id = parsed.get("frontmatter", {}).get("issue-id")
    if not issue_id:
        raise InputError("ticket has no issue-id")
    impl_dir = (issue.parent.parent / "impl" / issue_id).resolve()
    frozen = impl_dir / "ticket-context.json"
    context = tc.load_context(frozen) if frozen.is_file() else tc.create_context(issue, repo)
    root = _find_root(issue)
    config_value = context.get("config_path", "dev/agents-config.md")
    config_path = Path(config_value)
    if not config_path.is_absolute():
        candidate = repo / config_path
        config_path = candidate if candidate.is_file() else root / config_path
    config = ac.load(config_path)
    return tc, parsed, context, config, root, impl_dir


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _load_json(path: Path):
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise InputError("duplicate JSON key %s in %s" % (key, path))
            value[key] = item
        return value
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except (OSError, UnicodeError, ValueError) as exc:
        raise InputError("cannot read JSON %s: %s" % (path, exc))


def _check_context_freshness(rep, issue, root, context):
    hashes = context.get("hashes") or {}
    paths = {"issue": issue}
    for key, field in (("spec", "spec_path"), ("config", "config_path")):
        value = context.get(field)
        if value:
            paths[key] = Path(value) if Path(value).is_absolute() else root / value
    for key, path in paths.items():
        try:
            actual = _sibling("ticket_context").issue_content_hash(path) if key == "issue" else _sha(path)
            if hashes.get(key) != actual:
                rep.add("context", "FAIL", "%s hash differs from frozen ticket context" % key)
        except OSError as exc:
            rep.add("context", "FAIL", "cannot hash frozen %s input: %s" % (key, exc))


def _all_paths(file):
    return [p for p in (file.get("old_path"), file["path"]) if p]


def _production_files(files, policy):
    excluded = policy.get("production-exclude") or DEFAULT_NON_PRODUCTION
    result = []
    for file in files:
        path = file["old_path"] if file["status"] == "D" and file.get("old_path") else file["path"]
        if not match_any(path, excluded):
            result.append(file)
    return result


def _check_scope_redlines(rep, files, context, policy):
    declared = context.get("declared_scope") or []
    if not declared:
        rep.add("scope", "FAIL", "frozen context declared_scope is empty")
    drift = []
    deny = []
    for file in files:
        for path in _all_paths(file):
            if not match_any(path, declared):
                drift.append(path)
                rep.add("scope", "FAIL", "%s is outside declared-scope; drift registration cannot exempt it" % path)
            hit = match_any(path, policy.get("deny") or [])
            if hit:
                deny.append(path)
                rep.add("redline-deny", "FAIL", "%s (%s) hits deny glob %s; add/modify/delete/rename and registration are never exempt" % (path, file["status"], hit))
    return sorted(set(drift)), sorted(set(deny))


def _safe_artifact(root: Path, relative: str):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise InputError("artifact path must remain relative to impl dir: %s" % relative)
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise InputError("artifact escapes impl dir: %s" % relative)
    return resolved


def _check_aux_raw(rep, document, path, label):
    records = list(document.get("red_records", []))
    bug = document.get("bug_regression") or {}
    records += list(bug.get("revert", [])) + list(bug.get("restore", []))
    records += [bug[key] for key in ("red", "green") if bug.get(key)]
    for record in records:
        raw = record.get("raw_log") or {}
        try:
            raw_path = _safe_artifact(path.parent, raw.get("path", ""))
            if raw_path.is_symlink() or not raw_path.is_file() or _sha(raw_path) != raw.get("sha256"):
                rep.add("evidence-raw", "FAIL", "%s %s raw log missing/symlink/hash mismatch" % (label, record.get("id")))
            else:
                verifier = _sibling("verify_issue")
                rep.extend("evidence-raw", verifier.record_semantic_errors(record, raw_path.read_bytes()))
        except (InputError, OSError) as exc:
            rep.add("evidence-raw", "FAIL", "%s %s raw log invalid: %s" % (label, record.get("id"), exc))


def _red_semantics(records):
    value = json.loads(json.dumps(records or [], ensure_ascii=False, sort_keys=True))
    for record in value:
        if isinstance(record.get("raw_log"), dict):
            record["raw_log"].pop("path", None)
    return value


def _bug_semantics(bug):
    """Compare immutable bug operations while allowing per-generation raw copies."""
    value = json.loads(json.dumps(bug or {}, ensure_ascii=False, sort_keys=True))
    records = list(value.get("revert", [])) + list(value.get("restore", []))
    records += [value[key] for key in ("red", "green") if value.get(key)]
    for record in records:
        if isinstance(record.get("raw_log"), dict):
            record["raw_log"].pop("path", None)
    return value


def _verification_generation(document):
    parts = Path(document.get("manifest", "")).parts
    return parts[1] if len(parts) == 3 and parts[0] == "raw" and parts[2] == MANIFEST_NAME else None


def _validate_manifest(rep, impl_dir: Path, issue_id: str, budget, tree, primary=None):
    paths = sorted((impl_dir / "raw").glob("*/" + MANIFEST_NAME)) if (impl_dir / "raw").is_dir() else []
    if not paths:
        rep.add("artifact-manifest", "FAIL", "no generation artifact-generation-manifest.json found")
        return
    schema = _sibling("json_schema")
    schema_path = Path(__file__).resolve().parent.parent / "references" / "artifact-generation.schema.json"
    indexed = {}
    manifests = {}
    currents = []
    max_mb = int(budget.get("artifact-manifest-max-mb", 5))
    for path in paths:
        if path.is_symlink() or path.stat().st_size > max_mb * 1024 * 1024:
            rep.add("artifact-manifest", "FAIL", "manifest symlink/budget violation: %s" % path)
            continue
        try:
            manifest = _load_json(path)
            rep.extend("artifact-manifest", schema.load_validate(schema_path, manifest))
        except (InputError, ValueError, OSError) as exc:
            rep.add("artifact-manifest", "FAIL", "manifest invalid: %s" % exc)
            continue
        generation = manifest.get("generation")
        if generation in manifests:
            rep.add("artifact-manifest", "FAIL", "duplicate generation: %s" % generation)
        manifests[generation] = (path, manifest)
        if manifest.get("subject") != issue_id or manifest.get("candidate_tree") is None:
            rep.add("artifact-manifest", "FAIL", "manifest subject/tree mismatch: %s" % generation)
        if manifest.get("status") == "current":
            currents.append((path, manifest))
        seen = set()
        entries = manifest.get("artifacts")
        if not isinstance(entries, list):
            rep.add("artifact-manifest", "FAIL", "manifest artifacts must be a list: %s" % generation)
            continue
        for entry in entries:
            relative = entry.get("path")
            if relative in seen:
                rep.add("artifact-manifest", "FAIL", "duplicate manifest path: %s" % relative)
                continue
            seen.add(relative)
            try:
                artifact = _safe_artifact(path.parent, relative)
                if artifact.is_symlink() or not artifact.is_file():
                    rep.add("artifact-manifest", "FAIL", "manifest artifact missing/symlink: %s" % relative)
                    continue
                if _sha(artifact) != entry.get("sha256") or artifact.stat().st_size != entry.get("size"):
                    rep.add("artifact-manifest", "FAIL", "manifest artifact hash/size mismatch: %s" % relative)
                absolute = artifact.resolve()
                if absolute in indexed and indexed[absolute] != entry:
                    rep.add("artifact-manifest", "FAIL", "conflicting generation entries for %s" % artifact)
                indexed[absolute] = entry
            except (InputError, OSError) as exc:
                rep.add("artifact-manifest", "FAIL", str(exc))
    if len(currents) != 1:
        rep.add("artifact-manifest", "FAIL", "exactly one current generation is required")
    elif primary is not None:
        path, current = currents[0]
        if current.get("generation") != _verification_generation(primary):
            for runner in ("implementer", "supervisor"):
                candidate_path = impl_dir / ("verification-%s.json" % runner)
                if candidate_path.is_file():
                    candidate = _load_json(candidate_path)
                    if _verification_generation(candidate) == current.get("generation") and candidate.get("tree") == tree:
                        primary = candidate
                        break
        expected_relative = "raw/%s/%s" % (_verification_generation(primary), MANIFEST_NAME)
        if (primary.get("manifest") != expected_relative
                or current.get("generation") != _verification_generation(primary)
                or current.get("candidate_tree") != tree):
            rep.add("artifact-manifest", "FAIL", "primary manifest generation/candidate_tree is stale")
        archived_name = "verification-%s.json" % primary.get("runner")
        archived = path.parent / archived_name
        try:
            if _load_json(archived) != primary:
                rep.add("artifact-manifest", "FAIL", "archived primary verification differs from current root copy")
        except (InputError, OSError) as exc:
            rep.add("artifact-manifest", "FAIL", "archived primary verification missing: %s" % exc)
        evidence_name = "evidence-%s.md" % issue_id
        try:
            if (path.parent / evidence_name).read_bytes() != (impl_dir / evidence_name).read_bytes():
                rep.add("artifact-manifest", "FAIL", "archived evidence rendering differs from current root copy")
            for name in ("verification-red.json", "bug-regression.json"):
                root_copy = impl_dir / name
                if root_copy.is_file() and _load_json(path.parent / name) != _load_json(root_copy):
                    rep.add("artifact-manifest", "FAIL", "archived auxiliary evidence differs: %s" % name)
        except (InputError, OSError) as exc:
            rep.add("artifact-manifest", "FAIL", "archived evidence missing/unreadable: %s" % exc)
        supersedes = current.get("supersedes")
        if supersedes:
            previous = manifests.get(supersedes)
            if previous is None or previous[1].get("status") != "superseded":
                rep.add("artifact-manifest", "FAIL", "current supersedes chain is broken")
    raw_root = impl_dir / "raw"
    if raw_root.is_symlink():
        rep.add("artifact-raw", "FAIL", "raw root must not be a symlink")
    elif raw_root.is_dir():
        for directory, dirs, names in os.walk(str(raw_root), followlinks=False):
            linked = [name for name in dirs if (Path(directory) / name).is_symlink()]
            dirs[:] = [name for name in dirs if name not in linked]
            for name in linked:
                rep.add("artifact-raw", "FAIL", "raw directory symlink is forbidden: %s" % (Path(directory) / name))
            for name in names:
                artifact = Path(directory) / name
                if name == MANIFEST_NAME:
                    continue
                if artifact.is_symlink():
                    rep.add("artifact-raw", "FAIL", "raw file symlink is forbidden: %s" % artifact)
                elif artifact.resolve() not in indexed:
                    rep.add("artifact-raw", "FAIL", "raw file is not manifest-indexed by any generation: %s" % artifact)


def _validate_verification(rep, impl_dir, repo, context, issue_id, runner, require_fast=True):
    path = impl_dir / ("verification-%s.json" % runner)
    if not path.is_file():
        rep.add("evidence", "FAIL", "verification JSON missing: %s" % path.name)
        return None
    try:
        document = _load_json(path)
        verifier = _sibling("verify_issue")
        errors = verifier.validate_verification(document, path, repo, expected_runner=runner,
                                                expected_issue=issue_id, require_fast=require_fast)
        rep.extend("evidence-%s" % runner, errors)
        if document.get("base") != context["base"]:
            rep.add("evidence-%s" % runner, "FAIL", "verification base differs from frozen context")
        _check_aux_raw(rep, document, path, runner)
        return document
    except (InputError, ImportError, ValueError, OSError) as exc:
        rep.add("evidence-%s" % runner, "FAIL", "verification validation failed: %s" % exc)
        return None


def _validate_red(rep, impl_dir, added_tests, repo, base):
    if not added_tests:
        return
    path = impl_dir / "verification-red.json"
    if not path.is_file():
        rep.add("test-change", "FAIL", "added tests lack verify_issue red structured record: %s" % ", ".join(added_tests))
        return
    try:
        document = _load_json(path)
        schema = _sibling("json_schema")
        schema_path = Path(__file__).resolve().parent.parent / "references" / "verification.schema.json"
        rep.extend("test-change", schema.load_validate(schema_path, document))
        records = document.get("red_records", [])
        _check_aux_raw(rep, document, path, "red")
        for test in added_tests:
            matches = [r for r in records if test in r.get("test_paths", [])]
            if not matches:
                rep.add("test-change", "FAIL", "no red record maps added test %s" % test)
            for record in matches:
                if not (record.get("kind") == "red" and record.get("pass") is True
                        and record.get("started") is True and not record.get("timed_out")
                        and record.get("actual_exit") not in (None, 0)
                        and record.get("output_matched") is True):
                    rep.add("test-change", "FAIL", "red record is not a completed assertion failure: %s" % test)
                try:
                    added_at_red = git(repo, "diff", "--name-status", base, record["head"], "--", test).strip()
                    if not added_at_red.startswith("A"):
                        rep.add("test-change", "FAIL", "red record head does not contain the test as newly added: %s" % test)
                except (InputError, KeyError) as exc:
                    rep.add("test-change", "FAIL", "red record Git ancestry invalid for %s: %s" % (test, exc))
    except (InputError, ImportError, ValueError, OSError) as exc:
        rep.add("test-change", "FAIL", "invalid red verification: %s" % exc)


def _test_mappings(log_text):
    mappings = []
    for match in re.finditer(r"```json\s*\n(.*?)\n```", log_text, re.S):
        try:
            value = json.loads(match.group(1))
        except ValueError as exc:
            raise InputError("invalid JSON test-change mapping: %s" % exc)
        entries = value.get("test-changes", value.get("test_changes")) if isinstance(value, dict) else None
        if entries is not None:
            if not isinstance(entries, list):
                raise InputError("test-changes mapping must be a list")
            mappings.extend(entries)
    return mappings


def _slug(text):
    value = re.sub(r"[^\w\- \u4e00-\u9fff]", "", text.strip().lower(), flags=re.UNICODE)
    return re.sub(r"\s", "-", value)


def _valid_spec_anchor(anchor, context, root):
    if not isinstance(anchor, str) or "#" not in anchor:
        return False
    path, fragment = anchor.split("#", 1)
    if path != context.get("spec_path") or not fragment:
        return False
    try:
        text = (root / path).read_text(encoding="utf-8")
    except OSError:
        return False
    headings = [re.sub(r"^#+\s*", "", line).strip() for line in text.splitlines() if line.startswith("#")]
    return any(fragment == heading or fragment == _slug(heading) for heading in headings)


def _check_test_changes(rep, files, policy, context, repo, impl_dir, root, log_text, receipt, phase):
    changed = [f for f in files if match_any(f["path"], policy.get("test-change") or TEST_PATTERNS)
               or (f.get("old_path") and match_any(f["old_path"], policy.get("test-change") or TEST_PATTERNS))]
    added = sorted(f["path"] for f in changed if f["status"] == "A")
    _validate_red(rep, impl_dir, added, repo, context["base"])
    existing = [f for f in changed if f["status"] != "A"]
    if not existing:
        return []
    try:
        mappings = _test_mappings(log_text)
    except InputError as exc:
        rep.add("test-change", "FAIL", str(exc))
        return []
    real_dod = {item["id"] for item in context["dod"]}
    pending = []
    for file in existing:
        path = file["path"]
        if file["status"] == "D":
            rep.add("test-change", "FAIL", "existing test deletion is denied by default: %s" % path)
            continue
        matches = [m for m in mappings if isinstance(m, dict) and m.get("path") == path]
        if len(matches) != 1:
            rep.add("test-change", "FAIL", "existing test needs exactly one structured mapping: %s" % path)
            continue
        mapping = matches[0]
        dod_id = mapping.get("dod_id", mapping.get("dod-id"))
        anchor = mapping.get("spec_anchor", mapping.get("spec-anchor"))
        conflict = mapping.get("conflict", mapping.get("conflict_explanation"))
        scope = mapping.get("scope")
        if dod_id not in real_dod:
            rep.add("test-change", "FAIL", "%s mapping does not reference a real DoD ID" % path)
        if not _valid_spec_anchor(anchor, context, root):
            rep.add("test-change", "FAIL", "%s mapping lacks a real frozen spec anchor" % path)
        if not isinstance(conflict, str) or not conflict.strip():
            rep.add("test-change", "FAIL", "%s mapping lacks conflict explanation" % path)
        scope_valid = ((isinstance(scope, str) and bool(scope.strip()))
                       or (isinstance(scope, list) and bool(scope)
                           and all(isinstance(item, str) and item.strip() for item in scope)))
        if not scope_valid:
            rep.add("test-change", "FAIL", "%s mapping lacks behavioral scope" % path)
        digest = _canonical_hash(mapping)
        closures = [] if receipt is None else [
            c for c in receipt.get("semantic_closures", [])
            if c.get("path") == path and c.get("dod_id") == dod_id
            and c.get("spec_anchor") == anchor and c.get("mapping_sha256") == digest
            and c.get("status") == "closed" and c.get("note", "").strip()
        ]
        if phase != "review" and not closures:
            rep.add("test-change", "FAIL", "%s mapping lacks matching review-receipt semantic closure" % path)
        elif phase == "review":
            pending.append(path)
    if pending and phase == "review":
        rep.add("test-change", "REVIEW", "existing test mappings require receipt semantic closure: %s" % ", ".join(pending))
    return changed


def _run_generator(rep, repo, head, tree, entry, changed_paths):
    ident = entry.get("id")
    pattern = entry.get("glob")
    command = entry.get("generator")
    if not all(isinstance(v, str) and v.strip() for v in (ident, pattern, command)):
        rep.add("generated-refresh", "FAIL", "each entry requires non-empty id/glob/generator")
        return
    before_status = git(repo, "status", "--porcelain=v1", "--untracked-files=no")
    with tempfile.TemporaryDirectory(prefix="process-generated-") as temp:
        worktree = Path(temp) / "worktree"
        try:
            git(repo, "worktree", "add", "--detach", str(worktree), head)
            result = subprocess.run(command, cwd=str(worktree), shell=True, capture_output=True,
                                    text=True, timeout=120)
            output = (result.stdout + result.stderr).strip()
            if result.returncode:
                rep.add("generated-refresh", "FAIL", "%s generator exit=%s: %s" % (ident, result.returncode, output[-500:]))
                return
            touched = [p for p in git(worktree, "diff", "--name-only", "HEAD", "--").splitlines() if p]
            touched += [p for p in git(worktree, "ls-files", "--others", "--exclude-standard").splitlines() if p]
            outside = sorted(set(p for p in touched if not match_any(p, [pattern])))
            if outside:
                rep.add("generated-refresh", "FAIL", "%s generator wrote outside output glob: %s" % (ident, ", ".join(outside)))
            git(worktree, "add", "-A")
            actual_tree = git(worktree, "write-tree").strip()
            if actual_tree != tree:
                rep.add("generated-refresh", "FAIL", "%s generator does not reproduce exact HEAD tree" % ident)
        except subprocess.TimeoutExpired:
            rep.add("generated-refresh", "FAIL", "%s generator timed out" % ident)
        except (InputError, OSError) as exc:
            rep.add("generated-refresh", "FAIL", "%s generator failed to start/worktree: %s" % (ident, exc))
        finally:
            if worktree.exists():
                git(repo, "worktree", "remove", "--force", str(worktree))
    try:
        after_head, after_tree = _head_tree(repo)
        after_status = git(repo, "status", "--porcelain=v1", "--untracked-files=no")
        if (after_head, after_tree, after_status) != (head, tree, before_status):
            rep.add("generated-refresh", "FAIL", "%s changed the ticket worktree/HEAD/tree" % ident)
    except InputError as exc:
        rep.add("generated-refresh", "FAIL", str(exc))


def _check_generated(rep, repo, files, policy, head, tree):
    entries = policy.get("generated-refresh") or []
    ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    commands = [entry.get("generator") for entry in entries if isinstance(entry, dict)]
    if (len(ids) != len(entries) or len(set(ids)) != len(ids)
            or any(not isinstance(value, str) or not value.strip() for value in ids)):
        rep.add("generated-refresh", "FAIL", "generated-refresh IDs must be present and unique")
    if (len(commands) != len(entries) or len(set(commands)) != len(commands)
            or any(not isinstance(value, str) or not value.strip() for value in commands)):
        rep.add("generated-refresh", "FAIL", "each generated-refresh entry must have a unique generator command")
    paths = sorted({p for file in files for p in _all_paths(file)})
    for entry in entries:
        if isinstance(entry, dict) and any(match_any(path, [entry.get("glob", "")]) for path in paths):
            _run_generator(rep, repo, head, tree, entry, paths)


def _check_architecture(rep, repo, issue, context, files, base, head):
    paths = sorted({p for file in files for p in _all_paths(file)})
    architecture_changed = [p for p in paths if match_any(p, ARCHITECTURE_PATTERNS)]
    declared = context.get("architecture_updates") or []
    if declared and not architecture_changed:
        rep.add("architecture", "FAIL", "ticket declares architecture-updates but no architecture artifact changed")
    try:
        graph = _sibling("architecture_graph")
        _, errors = graph.check(repo, head=head, base=base)
        rep.extend("architecture-graph", errors)
        root = _find_root(issue)
        spec_path = root / context["spec_path"]
        issue_paths = sorted((spec_path.parent / "issues").glob("*.md"))
        errors = graph.check_flow_forward(
            repo, spec_path, issue_paths, head=head, implementation_ids=set(declared),
            allow_flow_back=True)
        rep.extend("architecture-forward", [str(error) for error in errors])
        if (repo / "docs" / "architecture").is_dir() and list((repo / "docs" / "architecture").glob("*.json")):
            _, errors = graph.render_homepage(repo, write=False, head=head)
            rep.extend("architecture-homepage", [str(error) for error in errors])
        if hasattr(graph, "check_flow_back"):
            errors = graph.check_flow_back(repo, issue, base, head, spec_path=spec_path)
            rep.extend("architecture-flow", [str(error) for error in errors])
        elif declared or architecture_changed:
            rep.add("architecture-flow", "FAIL", "architecture_graph.check_flow_back is required for architecture changes")
    except (ImportError, ValueError, OSError, TypeError) as exc:
        rep.add("architecture-graph", "FAIL", "architecture validation failed: %s" % exc)


def _check_worktree_dev(rep, repo, root):
    if not (repo / ".git").is_file():
        return
    main_dev = root / "dev"
    entry = repo / "dev"
    if not entry.is_symlink():
        rep.add("worktree-dev", "FAIL", "ticket worktree dev must be a symlink to main workspace dev")
        return
    try:
        if not entry.resolve(strict=True).is_dir() or entry.resolve(strict=True) != main_dev.resolve(strict=True):
            rep.add("worktree-dev", "FAIL", "worktree dev symlink resolves to wrong target")
    except (OSError, RuntimeError) as exc:
        rep.add("worktree-dev", "FAIL", "worktree dev symlink is broken: %s" % exc)


def _check_budget(rep, impl_dir, issue_id, budget):
    paths = {
        "impl-log": (impl_dir / ("impl-log-%s.md" % issue_id), int(budget.get("impl-log-max-lines", 120))),
        "evidence": (impl_dir / ("evidence-%s.md" % issue_id), int(budget.get("evidence-max-lines", budget.get("impl-log-max-lines", 120)))),
    }
    for label, (path, maximum) in paths.items():
        if not path.is_file():
            rep.add("artifact-budget", "FAIL", "%s missing: %s" % (label, path.name))
            continue
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > maximum:
                rep.add("artifact-budget", "FAIL", "%s lines %s > %s" % (label, lines, maximum))
        except OSError as exc:
            rep.add("artifact-budget", "FAIL", "cannot read %s: %s" % (label, exc))
    total = 0
    for directory, dirs, names in os.walk(str(impl_dir), followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in names:
            path = Path(directory) / name
            try:
                if path.is_symlink():
                    try:
                        path.resolve(strict=True).relative_to(impl_dir.resolve())
                    except (ValueError, OSError):
                        rep.add("artifact-budget", "FAIL", "impl artifact symlink escapes impl dir: %s" % path)
                        continue
                total += path.stat().st_size
            except OSError as exc:
                rep.add("artifact-budget", "FAIL", "cannot stat artifact %s: %s" % (path, exc))
    maximum = int(budget.get("impl-dir-max-mb", 20)) * 1024 * 1024
    if total > maximum:
        rep.add("artifact-budget", "FAIL", "impl directory bytes %s > %s" % (total, maximum))
    return total


def _table_rows(section):
    rows = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if cells and all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows[1:] if rows else []


def _placeholder(value):
    return not value or value.strip() in {"无", "…", "-", "N/A"} or "<" in value or "待核" in value


def _evidence_pointer_problem(text, repo, impl_dir):
    code = re.search(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9]+:[A-Za-z_][\w.<>-]*", text)
    test = re.search(r"(?:^|[\s`（(])((?:tests?/|[^\s`]+/tests?/|[^\s`]*test_[^\s`]+)[^\s`|,，;；]*)", text)
    evidence = re.search(r"(?:verification-(?:implementer|supervisor)\.json|evidence-[\w-]+\.md)(?:#[\w:.-]+)?", text)
    if not (code and test and evidence):
        return "missing code + test + verification evidence pointers"
    code_path = code.group(0).split(":", 1)[0]
    test_path = test.group(1).strip("`.),，；;")
    for path in (code_path, test_path):
        if Path(path).is_absolute() or ".." in Path(path).parts:
            return "evidence source path escapes repository: %s" % path
        try:
            git(repo, "cat-file", "-e", "HEAD:%s" % path)
        except InputError:
            return "evidence source path is absent at HEAD: %s" % path
    reference = evidence.group(0)
    name, _, fragment = reference.partition("#")
    path = impl_dir / name
    if not path.is_file() or path.is_symlink():
        return "evidence artifact is missing: %s" % name
    if fragment and name.startswith("verification-"):
        try:
            document = _load_json(path)
        except InputError as exc:
            return str(exc)
        ids = {record.get("id") for record in document.get("records", [])}
        ids.update(record.get("id") for record in document.get("red_records", []))
        if fragment not in ids:
            return "verification evidence fragment is absent: %s" % reference
    return None


def _check_conformance(rep, tc, context, config, impl_dir, issue_id, integration, log_text, head, tree, repo,
                       feature_coverage_required):
    path = impl_dir / ("conformance-%s.md" % issue_id)
    if not path.is_file():
        rep.add("conformance", "FAIL", "conformance record missing: %s" % path.name)
        return
    try:
        text = path.read_text(encoding="utf-8")
        header = read_first_yaml_block(path)
    except (OSError, InputError) as exc:
        rep.add("conformance", "FAIL", "conformance header invalid: %s" % exc)
        return
    required = ("issue", "context_digest", "commit", "tree", "verdict", "checker",
                "checker_session", "selection", "date")
    for key in required:
        if _placeholder(str(header.get(key) or "")):
            rep.add("conformance", "FAIL", "conformance header lacks %s" % key)
    if (header.get("issue") != issue_id or header.get("commit") != head
            or header.get("tree") != tree
            or header.get("context_digest") != context.get("gate2_context_digest")):
        rep.add("conformance", "FAIL", "conformance issue/context_digest/commit/tree is stale or mismatched")
    if "head" in header and header.get("head") != head:
        rep.add("conformance", "FAIL", "optional conformance head differs from commit/HEAD")
    expected_checker = (config.get("conformance") or {}).get("checker", "main-session")
    if header.get("checker") != expected_checker:
        rep.add("conformance", "FAIL", "conformance checker must be %s" % expected_checker)
    try:
        datetime.date.fromisoformat(str(header.get("date") or ""))
    except ValueError:
        rep.add("conformance", "FAIL", "conformance date must be YYYY-MM-DD")
    verdict = header.get("verdict")
    mode = (config.get("conformance") or {}).get("mode", "always")
    mandatory = integration or mode == "always"
    selected = True
    rate = (config.get("conformance") or {}).get("sample-rate", "1/3")
    if mode == "sampled" and not integration:
        selected = tc.select_conformance(context, rate)
    selection = header.get("selection")
    if not isinstance(selection, dict) or any(key not in selection for key in ("algorithm", "rate", "selected")):
        rep.add("conformance", "FAIL", "conformance selection requires algorithm/rate/selected")
    else:
        if selection.get("algorithm") != "sha256-v1":
            rep.add("conformance", "FAIL", "conformance selection.algorithm must be sha256-v1")
        if not isinstance(selection.get("selected"), bool) or selection["selected"] != selected:
            rep.add("conformance", "FAIL", "conformance selection.selected does not match ticket_context selection")
        expected_rate = Fraction(1) if mandatory else Fraction(str(rate))
        try:
            actual_rate = Fraction(str(selection["rate"]))
            if actual_rate != expected_rate:
                rep.add("conformance", "FAIL", "conformance selection.rate differs from effective policy")
        except (ValueError, ZeroDivisionError):
            rep.add("conformance", "FAIL", "conformance selection.rate is invalid")
    if verdict == "skipped":
        if mandatory or selected:
            rep.add("conformance", "FAIL", "skipped is not selected by ticket_context.select_conformance or ticket is integration")
        return
    if verdict != "conform":
        rep.add("conformance", "FAIL", "conformance verdict must be conform, got %s" % verdict)
        return
    if mandatory or selected:
        rows = _table_rows(md_section(text, "需求点清单"))
        if not rows:
            rep.add("conformance", "FAIL", "conform record has an empty requirement-point table")
        numbers = []
        for row in rows:
            if len(row) < 5:
                rep.add("conformance", "FAIL", "requirement row lacks number/source/verdict/evidence")
                continue
            number, requirement, source, decision, evidence = row[:5]
            if not re.fullmatch(r"[0-9]+", number):
                rep.add("conformance", "FAIL", "requirement point number is invalid: %s" % number)
            else:
                numbers.append(int(number))
            if _placeholder(requirement) or _placeholder(source):
                rep.add("conformance", "FAIL", "requirement %s lacks text/source" % number)
            if decision != "已实现" or decision in {"未实现", "语义偏差"}:
                rep.add("conformance", "FAIL", "conform record contains non-implemented/semantic deviation at %s" % number)
            pointer_problem = _evidence_pointer_problem(evidence, repo, impl_dir)
            if _placeholder(evidence) or pointer_problem:
                rep.add("conformance", "FAIL", "requirement %s evidence invalid: %s" % (number, pointer_problem))
        if numbers and numbers != list(range(1, len(rows) + 1)):
            rep.add("conformance", "FAIL", "requirement point numbers must be unique and sequential")
        matrix_rows = _table_rows(md_section(log_text, "需求覆盖矩阵"))
        sample = md_section(text, "抽查记录")
        for row in matrix_rows:
            if any("★" in cell for cell in row):
                ident = row[0].strip(" `")
                lines = [line for line in sample.splitlines() if ident and ident in line]
                if not lines or not any(_evidence_pointer_problem(line, repo, impl_dir) is None for line in lines):
                    rep.add("conformance", "FAIL", "critical matrix row %s is absent from sampled code/test/evidence record" % ident)
        if feature_coverage_required:
            feature = md_section(text, "Feature 级覆盖核对")
            feature_rows = _table_rows(feature)
            if not feature_rows or any(any(cell in {"未实现", "语义偏差"} for cell in row) for row in feature_rows):
                rep.add("conformance", "FAIL", "integration ticket lacks complete feature-level coverage table")
            for row in feature_rows:
                if not row or _evidence_pointer_problem(row[-1], repo, impl_dir):
                    rep.add("conformance", "FAIL", "integration feature coverage row lacks valid evidence pointers")


def _load_receipt(rep, impl_dir, issue, repo):
    path = impl_dir / "review-receipt.json"
    if not path.is_file():
        rep.add("review-receipt", "FAIL", "review receipt missing")
        return None
    try:
        receipt = _load_json(path)
        validator = _sibling("review_receipt")
        errors = validator.validate_receipt(receipt, issue, repo, path)
        rep.extend("review-receipt", errors)
        if receipt.get("verdict") not in {"pass", "pass-with-notes"}:
            rep.add("review-receipt", "FAIL", "receipt verdict must be pass/pass-with-notes")
        return receipt
    except (InputError, ImportError, ValueError, OSError) as exc:
        rep.add("review-receipt", "FAIL", "receipt validation failed: %s" % exc)
        return None


def _check_human_decisions(rep, root, context, issue_id):
    try:
        module = _sibling("human_decision")
        feature = "/".join(Path(context["feature_dir"]).parts[-2:])
        active = module.active_for_issue(root, feature, issue_id)
        if active:
            rep.add("human-decision", "FAIL", "active human decision package blocks close")
    except Exception as exc:
        # Decision storage is external to this aggregator; any read/conflict/schema
        # error means close cannot prove that no active package exists.
        rep.add("human-decision", "FAIL", "cannot prove absence of active human decisions: %s" % exc)


def run(args):
    issue = Path(args.issue).resolve()
    repo = _repo_root(Path(args.repo).resolve())
    if not issue.is_file():
        raise InputError("ticket file does not exist: %s" % issue)
    tc, parsed, context, config, root, impl_dir = _load_context(issue, repo)
    issue_id = parsed["frontmatter"]["issue-id"]
    if context.get("issue_id") != issue_id:
        raise InputError("frozen context issue_id mismatch")
    head, tree = _head_tree(repo)
    target = context.get("target_branch")
    if not target:
        raise InputError("frozen context lacks target_branch")
    target_commit = git(repo, "rev-parse", "--verify", target + "^{commit}").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", target_commit):
        raise InputError("target branch did not resolve to exact commit")
    base = context.get("base")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise InputError("frozen context base must be a full commit")
    git(repo, "cat-file", "-e", base + "^{commit}")
    rep = Report()
    _check_context_freshness(rep, issue, root, context)
    files = changed_files(repo, base, head)
    if not files:
        rep.add("diff", "FAIL", "frozen base..HEAD diff is empty")
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=no")
    if status:
        rep.add("worktree-clean", "FAIL", "tracked worktree changes are forbidden during process check")
    untracked = [p for p in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
    source = [p for p in untracked if Path(p).suffix.lower() in SOURCE_SUFFIXES]
    if source:
        rep.add("worktree-clean", "FAIL", "unignored untracked source: %s" % ", ".join(source))
    policy = config["verify-policy"]
    drift, deny = _check_scope_redlines(rep, files, context, policy)
    production = _production_files(files, policy)
    production_files = len(production)
    production_loc = sum(file["added"] + file["deleted"] for file in production)
    max_files = int(policy.get("ticket-max-files", 10))
    max_loc = int(policy.get("ticket-max-loc", 400))
    if production_files > max_files:
        rep.add("ticket-size", "FAIL", "production files %s > %s; re-split required" % (production_files, max_files))
    if production_loc > max_loc:
        rep.add("ticket-size", "FAIL", "production LOC %s > %s; re-split required" % (production_loc, max_loc))

    log_path = impl_dir / ("impl-log-%s.md" % issue_id)
    try:
        log_text = log_path.read_text(encoding="utf-8")
    except OSError:
        log_text = ""
        rep.add("impl-log", "FAIL", "impl-log missing: %s" % log_path.name)
    if not _table_rows(md_section(log_text, "需求覆盖矩阵")):
        rep.add("impl-log", "FAIL", "requirement coverage matrix is empty/missing")

    integration = _integration_ticket(issue, issue_id, tc.parse_issue)

    implementer = _validate_verification(rep, impl_dir, repo, context, issue_id, "implementer")
    supervisor = None
    receipt = None
    if args.phase in {"merge", "close"}:
        supervisor = _validate_verification(rep, impl_dir, repo, context, issue_id, "supervisor")
        receipt = _load_receipt(rep, impl_dir, issue, repo)
    primary = supervisor if args.phase in {"merge", "close"} else implementer
    if primary is not None:
        try:
            verifier = _sibling("verify_issue")
            evidence_path = impl_dir / ("evidence-%s.md" % issue_id)
            candidates = [primary]
            if args.phase == "review":
                supervisor_path = impl_dir / "verification-supervisor.json"
                if supervisor_path.is_file():
                    try:
                        other = _load_json(supervisor_path)
                        if other.get("head") == head and other.get("tree") == tree:
                            candidates.append(other)
                    except InputError:
                        pass
            expected = [verifier.render_evidence(item, issue_id) for item in candidates]
            if not evidence_path.is_file() or evidence_path.read_text(encoding="utf-8") not in expected:
                rep.add("evidence-rendering", "FAIL", "evidence Markdown is not deterministic rendering of current verification JSON")
        except (ImportError, OSError) as exc:
            rep.add("evidence-rendering", "FAIL", "cannot validate evidence rendering: %s" % exc)

    _check_test_changes(rep, files, policy, context, repo, impl_dir, root, log_text, receipt, args.phase)
    red_path = impl_dir / "verification-red.json"
    if red_path.is_file():
        try:
            red_document = _load_json(red_path)
            for label, document in (("implementer", implementer), ("supervisor", supervisor)):
                if document is not None and _red_semantics(document.get("red_records", [])) != _red_semantics(red_document.get("red_records", [])):
                    rep.add("test-change", "FAIL", "%s verification does not bind current red records" % label)
        except InputError as exc:
            rep.add("test-change", "FAIL", str(exc))
    _check_generated(rep, repo, files, policy, head, tree)
    _check_architecture(rep, repo, issue, context, files, base, head)
    _check_worktree_dev(rep, repo, root)
    budget = config.get("artifact-budget") or {}
    _check_budget(rep, impl_dir, issue_id, budget)
    _validate_manifest(rep, impl_dir, issue_id, budget, tree, primary)

    if parsed.get("frontmatter", {}).get("type") == "bug":
        bug_path = impl_dir / "bug-regression.json"
        if not bug_path.is_file():
            rep.add("bug-regression", "FAIL", "bug ticket lacks structured bug-regression.json")
        else:
            try:
                bug_doc = _load_json(bug_path)
                verifier = _sibling("verify_issue")
                rep.extend("bug-regression", verifier.validate_verification(
                    bug_doc, bug_path, repo, expected_issue=issue_id, require_fast=False))
                bug = bug_doc.get("bug_regression") or {}
                if not bug.get("pass") or not bug.get("revert") or not bug.get("restore"):
                    rep.add("bug-regression", "FAIL", "structured revert/red/restore/green proof is incomplete")
                if not (implementer or {}).get("bug_regression"):
                    rep.add("bug-regression", "FAIL", "implementer verification does not bind bug regression proof")
                elif _bug_semantics(implementer["bug_regression"]) != _bug_semantics(bug_doc.get("bug_regression")):
                    rep.add("bug-regression", "FAIL", "implementer bug regression proof differs from bug-regression.json")
                if args.phase in {"merge", "close"} and not (supervisor or {}).get("bug_regression"):
                    rep.add("bug-regression", "FAIL", "supervisor verification does not bind bug regression proof")
                elif args.phase in {"merge", "close"} and _bug_semantics(supervisor["bug_regression"]) != _bug_semantics(bug_doc.get("bug_regression")):
                    rep.add("bug-regression", "FAIL", "supervisor bug regression proof differs from bug-regression.json")
            except (InputError, ImportError, ValueError, OSError) as exc:
                rep.add("bug-regression", "FAIL", "bug regression proof invalid: %s" % exc)

    if args.phase == "close":
        _check_conformance(rep, tc, context, config, impl_dir, issue_id, integration, log_text, head, tree, repo,
                           integration and _integration_leaf_count(issue, tc.parse_issue) == 1)
        _check_human_decisions(rep, root, context, issue_id)

    meta = {
        "issue": issue_id, "phase": args.phase, "target-branch": target,
        "target-commit": target_commit, "base": base, "head": head, "tree": tree,
        "production-files": production_files, "production-loc": production_loc,
        "ticket-max-files": max_files, "ticket-max-loc": max_loc,
        "integration-ticket": integration, "out-of-scope": drift, "deny-hits": deny,
        "changed-files": files,
        "context-digest": context.get("gate2_context_digest"),
    }
    return rep, meta


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="read-only v2 ticket process policy aggregation")
    parser.add_argument("--issue", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--phase", choices=("review", "merge", "close"), required=True)
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)
    try:
        rep, meta = run(args)
    except (InputError, ImportError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print("process-check: input error: %s" % exc, file=sys.stderr)
        return 2
    verdict = "FAIL" if rep.failed else "PASS"
    print("process-check %s phase=%s %s tree=%s -> %s" % (
        meta["issue"], meta["phase"], meta["head"], meta["tree"], verdict))
    print("  production files %s/%s; LOC %s/%s; integration=%s" % (
        meta["production-files"], meta["ticket-max-files"], meta["production-loc"],
        meta["ticket-max-loc"], meta["integration-ticket"]))
    for item in rep.items:
        print("  [%s] %s: %s" % (item["level"], item["check"], item["msg"]))
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"verdict": verdict, **meta, "items": rep.items},
                                     ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
