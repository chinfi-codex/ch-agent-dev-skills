#!/usr/bin/env python3
"""Create and consume immutable v4 ticket contexts for delivery and gates.

Public API:
    target_branch(config, frontmatter, feature_dir=None) -> str
        Explicit ticket target > local-human integration branch > main branch.
    parse_issue(path) -> {"frontmatter": dict, "dod": list}
        Parse frontmatter and the DoD Markdown section.  Checkbox commands must
        be backtick-delimited; they become DoD-1, DoD-2, ... .  Empty sections,
        duplicate commands and malformed checkboxes raise ContextError.
    create_context(issue_path, repo, output_path=None, *, primary_repo=None) -> dict
        Freeze complete target/base/source OIDs, source tree, repo-relative
        paths, scope, issue/DoD/spec/config hashes and the
        gate2 digest.  All input/output paths must stay inside the Git worktree.
        With no output_path, no file is written.
    load_context(path) -> dict
        Strict JSON + schema + sequential DoD + digest validation.  Corrupt or
        tampered contexts are rejected, never repaired by guessing.
    issue_content_hash(path) -> str
        Hash canonical immutable requirement content.  status/dispatch/verify-fails
        and tracker publication fields are excluded; DoD remains separately bound.
    find_spec(issue_path, repo) -> Path
        Resolve only the current dated spec named by the issue.
    context_digest(context) -> str
        SHA256 of canonical JSON with ``gate2_context_digest`` excluded.
    select_conformance(context, rate) -> bool
        Deterministically compare SHA256(gate2 digest + issue ID) / 2**256 with
        a fractions.Fraction rate in (0, 1].
    declared_scope(frontmatter) -> list[str]
        Validated declared-scope list, shared with spec_tree.slice_issue.
    check_plan(issue_path, repo) -> {"ok", "errors", "warnings", "issues", ...}
        Read-only gate-2 plan validation: DoD shape, DAG, scope overlap,
        acceptance-plan binding, plus spec §3.1 tree ↔ declared-scope closure
        and per-ticket 方案切片 projection equality.  Strict closure applies
        only to specs declaring ``spec-tree-version: 1``; legacy specs get
        diagnostics in ``warnings`` and never block gate 2.

CLI (for delivery):
    python3 ticket_context.py create --issue ISSUE --repo REPO [--output OUT]
    python3 ticket_context.py show CONTEXT
    python3 ticket_context.py validate CONTEXT

Only Python's standard library and the sibling contract modules are used.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _datetime
import hashlib
import json
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Isolated CLI mode (-I) does not guarantee sibling imports on all supported
# Python versions; scripts in this directory intentionally form one package-less
# standard-library contract layer.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_scope

import agents_config
from globmatch import is_concrete_path, match_any
import json_schema
from state_store import atomic_json_write
from process_check import InputError as PlanInputError


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "ticket-context.schema.json"
CONTEXT_SCHEMA = "agent-dev.ticket-context/v4"
_OID = re.compile(r"^[0-9a-f]{40}$")


class ContextError(ValueError):
    """A ticket, Git identity, path, or frozen context is invalid."""


def _validate_scope(directory, frontmatter):
    try:
        artifact_scope.validate_ticket(directory, frontmatter)
    except ValueError as exc:
        raise ContextError(str(exc)) from exc


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(repo)] + list(args),
                                capture_output=True, text=True)
    except OSError as exc:
        raise ContextError("cannot execute git: %s" % exc)
    if result.returncode != 0:
        raise ContextError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout.strip()


def _repo_root(repo: Any) -> Path:
    path = Path(repo).resolve()
    if not path.is_dir():
        raise ContextError("repo is not a directory: %s" % path)
    root = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ContextError("repo path escapes its Git worktree: %s" % path)
    return root


def _inside(root: Path, value: Any, label: str, must_exist: bool = True) -> Tuple[Path, str]:
    candidate = Path(value)
    path = (root / candidate) if not candidate.is_absolute() else candidate
    try:
        resolved = path.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise ContextError("%s cannot be resolved: %s (%s)" % (label, path, exc))
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise ContextError("%s must stay inside repo %s: %s" % (label, root, resolved))
    if must_exist and not resolved.is_file():
        raise ContextError("%s is not a file: %s" % (label, resolved))
    return resolved, relative.as_posix()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContextError("context is not canonical JSON: %s" % exc)
    return text.encode("utf-8")


_MUTABLE_ISSUE_FIELDS = {
    "status", "dispatch", "verify-fails", "tracker-iid", "tracker-url",
}


def immutable_issue_material(issue_path: Any) -> Dict[str, Any]:
    """Return requirement semantics, excluding only delivery/publication state.

    Scope, dependencies, type, target and every Markdown requirement section stay
    immutable.  Frontmatter is canonicalized as data so formatting/comments in
    mutable delivery fields cannot invalidate an otherwise unchanged ticket.
    """
    source = Path(issue_path)
    try:
        text = source.read_text(encoding="utf-8")
        frontmatter = agents_config.read_frontmatter(source)
    except (OSError, UnicodeError, agents_config.ConfigError) as exc:
        raise ContextError("cannot read immutable issue content %s: %s" % (source, exc))
    match = re.match(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not match:
        raise ContextError("%s lacks valid frontmatter" % source)
    immutable = {key: value for key, value in frontmatter.items() if key not in _MUTABLE_ISSUE_FIELDS}
    return {"schema": "agent-dev.ticket-requirement/v1",
            "frontmatter": immutable, "body": text[match.end():]}


def issue_content_hash(issue_path: Any) -> str:
    """Hash immutable requirement content, never status/dispatch/verify-fails."""
    return _sha256_bytes(_canonical(immutable_issue_material(issue_path)))


def context_digest(context: Dict[str, Any]) -> str:
    """Hash all frozen fields except the self-referential digest field."""
    if not isinstance(context, dict):
        raise ContextError("context must be an object")
    material = {key: value for key, value in context.items() if key != "gate2_context_digest"}
    return _sha256_bytes(_canonical(material))


def _valid_branch_name(value: str) -> bool:
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and not value.startswith(("-", "/")) and not value.endswith(("/", "."))
            and ".." not in value and "//" not in value and "@{" not in value
            and not any(char in value for char in " ~^:?*[\\\x00")
            and all(part and part != "@" and not part.startswith(".")
                    and not part.endswith(".lock") for part in value.split("/")))


def target_branch(config: Dict[str, Any], frontmatter: Dict[str, Any],
                  feature_dir: Any = None) -> str:
    """Resolve the only target-branch rule used by start/rebase/merge/cleanup."""
    if not isinstance(config, dict) or not isinstance(frontmatter, dict):
        raise ContextError("config and frontmatter must be mappings")
    explicit = frontmatter.get("target-branch")
    if isinstance(explicit, dict) and not explicit:
        # 空块（`target-branch:` 被某些解析器映射成 {}）不是分支名，按未写处理
        explicit = None
    if explicit is not None and str(explicit).strip():
        branch = str(explicit).strip()
    else:
        closeout = config.get("feature-closeout") or {}
        if not isinstance(closeout, dict):
            raise ContextError("feature-closeout must be a mapping")
        mode = closeout.get("mode")
        if mode not in ("server-enforced", "cooperative-ci", "local-human"):
            raise ContextError("unsupported feature-closeout.mode: %r" % mode)
        use_integration = mode == "local-human"
        since = closeout.get("since")
        if use_integration and since:
            created = frontmatter.get("created")
            try:
                since_date = _datetime.date.fromisoformat(str(since))
                created_date = _datetime.date.fromisoformat(str(created))
            except ValueError:
                raise ContextError("feature-closeout.since and ticket created must be YYYY-MM-DD")
            if created_date < since_date:
                use_integration = False
        if use_integration:
            pattern = str(closeout.get("integration-branch") or "feat/<feature-slug>")
            if (feature_dir is not None and artifact_scope.campaign_id(feature_dir)
                    and "<feature-slug>" not in pattern):
                raise ContextError("campaign integration-branch must contain <feature-slug> for isolation")
            slug = str(frontmatter.get("feature-slug") or "").strip()
            if not slug and feature_dir is not None:
                slug = Path(feature_dir).resolve().name
            if "<feature-slug>" in pattern and not slug:
                raise ContextError("local-human target requires feature-slug or feature_dir")
            if feature_dir is not None:
                _validate_scope(feature_dir, frontmatter)
                slug = artifact_scope.branch_key(feature_dir, slug)
            branch = pattern.replace("<feature-slug>", slug)
        else:
            branch = str(config.get("main-branch") or "").strip()
    if not _valid_branch_name(branch):
        raise ContextError("invalid target Git branch: %r" % branch)
    return branch


def _dod_section(text: str) -> str:
    match = re.search(r"^##[ \t]+[^\n]*(?:DoD|验收标准)[^\n]*\n(.*?)(?=^#{1,2}[ \t]+|\Z)",
                      text, re.S | re.M)
    if not match:
        raise ContextError("ticket lacks a level-2 DoD/验收标准 section")
    return match.group(1)


def parse_dod(text: str) -> List[Dict[str, Any]]:
    """Parse commands with independent expected exit and optional output assertion."""
    section = _dod_section(text)
    checkbox_lines = [line for line in section.splitlines()
                      if re.match(r"^\s*[-*]\s+\[[ xX]\]", line)]
    if not checkbox_lines:
        raise ContextError("DoD section has no checkbox items")
    result: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for line in checkbox_lines:
        remainder = re.match(r"^\s*[-*]\s+\[[ xX]\]\s*(.*)$", line).group(1)
        match = re.fullmatch(r"`([^`\n]+)`\s*(.*)", remainder)
        if not match or not match.group(1).strip():
            raise ContextError("DoD checkbox lacks a non-empty backtick command: %s" % line.strip())
        command, suffix = match.group(1).strip(), match.group(2).strip()
        expected_exit, expected_output = 0, ""
        if suffix:
            oracle = re.fullmatch(r"→\s*退出码\s*`(-?[0-9]+)`\s*(?:；\s*期望输出\s*`([^`\n]*)`)?", suffix)
            if not oracle:
                raise ContextError("DoD oracle must be → 退出码 `<integer>` with optional ；期望输出 `<text>`: %s" % line.strip())
            expected_exit = int(oracle.group(1))
            if oracle.group(2) is not None:
                expected_output = oracle.group(2).strip()
                if not expected_output:
                    raise ContextError("DoD expected-output field is empty: %s" % line.strip())
        if command in seen:
            raise ContextError("duplicate DoD command at DoD-%d and DoD-%d" %
                               (seen[command], len(result) + 1))
        seen[command] = len(result) + 1
        result.append({"id": "DoD-%d" % (len(result) + 1), "command": command,
                       "expected_exit": expected_exit, "expected_output": expected_output})
    for line in section.splitlines():
        if re.match(r"^\s*[-*]\s+\[", line) and line not in checkbox_lines:
            raise ContextError("malformed DoD checkbox: %s" % line.strip())
    return result


def parse_issue(path: Any) -> Dict[str, Any]:
    """Return the parsed ticket frontmatter and non-empty, unique DoD list."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContextError("cannot read ticket %s: %s" % (source, exc))
    try:
        frontmatter = agents_config.read_frontmatter(source)
    except agents_config.ConfigError as exc:
        raise ContextError(str(exc))
    return {"frontmatter": frontmatter, "dod": parse_dod(text)}


def _find_spec(frontmatter: Dict[str, Any], text: str, root: Path,
               feature_dir: Path) -> Path:
    declared = frontmatter.get("tech-spec") or frontmatter.get("spec")
    references: List[str] = []
    if declared:
        references.append(str(declared))
    references.extend(re.findall(r"(?:tech-spec|技术方案)\s*(?:文件|路径)?\s*[：:]?\s*`([^`\n]+)`", text))
    if not references:
        references.extend(re.findall(r"(?:tech-spec|技术方案)\s*[：:]\s*([^\s，,；;]+\.md)", text))
    unique = list(dict.fromkeys(item.strip() for item in references if item.strip()))
    if not unique:
        raise ContextError("ticket must name its tech-spec in frontmatter or 备注")
    if len(unique) != 1:
        raise ContextError("ticket names multiple tech-spec files: %s" % ", ".join(unique))
    value = unique[0]
    relative = Path(value)
    if relative.is_absolute():
        raise ContextError("tech-spec path must be repository-relative")
    if value.startswith(("dev/", "docs/")):
        candidate = root / relative
    else:
        candidate = feature_dir / relative
    resolved, _ = _inside(root, candidate, "tech-spec")
    if artifact_scope.campaign_id(feature_dir) and resolved.parent != feature_dir.resolve():
        raise ContextError("campaign ticket must reference its own campaign tech-spec")
    return resolved


def find_spec(issue_path: Any, repo: Any) -> Path:
    """Resolve the single current tech-spec named by an issue, never all dated specs."""
    root = _repo_root(repo)
    issue, _ = _inside(root, issue_path, "issue")
    try:
        text = issue.read_text(encoding="utf-8")
        frontmatter = agents_config.read_frontmatter(issue)
    except (OSError, UnicodeError, agents_config.ConfigError) as exc:
        raise ContextError("cannot resolve issue tech-spec: %s" % exc)
    return _find_spec(frontmatter, text, root, issue.parent.parent)


def _oid(repo: Path, revision: str, tree: bool = False) -> str:
    suffix = "^{tree}" if tree else "^{commit}"
    value = _git(repo, "rev-parse", "--verify", revision + suffix)
    if not _OID.fullmatch(value):
        raise ContextError("git returned a non-full/unsupported OID for %s: %r" % (revision, value))
    return value


def _scope(frontmatter: Dict[str, Any], key: str) -> List[str]:
    value = frontmatter.get(key)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ContextError("ticket %s must be a list of non-empty strings" % key)
    result = [item.strip() for item in value]
    if len(set(result)) != len(result):
        raise ContextError("ticket %s contains duplicates" % key)
    return result


def declared_scope(frontmatter: Dict[str, Any]) -> List[str]:
    """Validated declared-scope; public because spec_tree slices from it."""
    return _scope(frontmatter, "declared-scope")


def _validate_context(context: Dict[str, Any], schema_path: Path = SCHEMA_PATH) -> None:
    errors = json_schema.load_validate(schema_path, context)
    if errors:
        raise ContextError("ticket context validation failed:\n  " + "\n  ".join(errors))
    for key in ("issue_path", "feature_dir", "spec_path", "config_path"):
        value = context[key]
        if (Path(value).is_absolute() or ".." in Path(value).parts or "\\" in value
                or value.startswith("/") or not value or any(not part for part in value.split("/"))):
            raise ContextError("%s must be a normalized repository-relative path" % key)
    for pattern in context["declared_scope"]:
        if (Path(pattern).is_absolute() or ".." in Path(pattern).parts or "\\" in pattern
                or pattern.startswith("/") or pattern.count("[") != pattern.count("]")):
            raise ContextError("declared_scope contains an invalid repository glob: %r" % pattern)
    if not _valid_branch_name(context["target_branch"]):
        raise ContextError("context target_branch is not a valid Git branch")
    commands = [item["command"] for item in context["dod"]]
    if len(set(commands)) != len(commands):
        raise ContextError("context contains duplicate DoD commands")
    expected_ids = ["DoD-%d" % (index + 1) for index in range(len(commands))]
    if [item["id"] for item in context["dod"]] != expected_ids:
        raise ContextError("context DoD IDs must be sequential DoD-1...DoD-N")
    if context["hashes"]["dod"] != _sha256_bytes(_canonical(context["dod"])):
        raise ContextError("context DoD hash does not match parsed DoD list")
    if context["gate2_context_digest"] != context_digest(context):
        raise ContextError("gate2 context digest mismatch; frozen context was modified")


def _verify_issue_slice(issue_text: str, spec: Path, spec_relative: str, scope: List[str]) -> None:
    """Freeze-time re-check of the 方案切片 projection for v1 spec trees.

    check-plan validates the whole group read-only; this closes the gap between
    that pass and freezing (validate, then edit, then freeze).  Legacy specs
    (no ``spec-tree-version``) never fail here.
    """
    import spec_tree
    try:
        version = spec_tree.spec_tree_version(spec)
    except spec_tree.SpecTreeError as exc:
        raise ContextError(str(exc)) from exc
    if version is None:
        return
    try:
        spec_bytes = spec.read_bytes()
        entries = spec_tree.parse_tree(spec_bytes.decode("utf-8"))
        if entries is None:
            raise ContextError("spec %s declares spec-tree-version but §3.1 has no ```text tree block" % spec_relative)
        expected = spec_tree.projection(entries, scope)
        spec_tree.verify_slice(issue_text, spec_relative, _sha256_bytes(spec_bytes), expected)
    except spec_tree.SpecTreeError as exc:
        raise ContextError(str(exc)) from exc
    except (OSError, UnicodeError) as exc:
        raise ContextError("cannot read spec for slice verification: %s" % exc)


def create_context(issue_path: Any, repo: Any, output_path: Any = None, *, primary_repo: Any = None) -> Dict[str, Any]:
    """Freeze a ticket from a worktree while keeping all dev artifacts in the primary checkout.

    ``repo`` supplies source/target Git OIDs. ``primary_repo`` supplies the issue,
    spec, config and output boundary; it defaults to ``repo`` for ordinary callers.
    This distinction is required when <worktree>/dev is a symlink to <primary>/dev.
    """
    root = _repo_root(repo)
    primary = _repo_root(primary_repo) if primary_repo is not None else root
    issue, issue_relative = _inside(primary, issue_path, "issue")
    try:
        issue_text = issue.read_text(encoding="utf-8")
        issue_hash = issue_content_hash(issue)
    except (OSError, UnicodeError) as exc:
        raise ContextError("cannot read issue: %s" % exc)
    parsed = parse_issue(issue)
    frontmatter = parsed["frontmatter"]
    issue_id = frontmatter.get("issue-id")
    if not isinstance(issue_id, str) or not re.fullmatch(r"issue-[0-9]+[a-z]?", issue_id):
        raise ContextError("ticket frontmatter has invalid/missing issue-id")
    config, config_relative = _inside(primary, "dev/agents-config.md", "agents-config")
    try:
        config_data = agents_config.load(config)
        config_bytes = config.read_bytes()
    except (agents_config.ConfigError, OSError) as exc:
        raise ContextError("invalid agents config: %s" % exc)
    feature_dir = issue.parent.parent
    _validate_scope(feature_dir, frontmatter)
    feature_relative = feature_dir.resolve().relative_to(primary).as_posix()
    spec = _find_spec(frontmatter, issue_text, primary, feature_dir)
    spec_relative = spec.relative_to(primary).as_posix()
    target = target_branch(config_data, frontmatter, feature_dir)
    source_head = _oid(root, "HEAD")
    target_commit = _oid(root, "refs/heads/" + target)
    base = _git(root, "merge-base", target_commit, source_head)
    if not _OID.fullmatch(base):
        raise ContextError("merge-base did not return a full Git OID")
    source_tree = _oid(root, source_head, tree=True)
    scope = _scope(frontmatter, "declared-scope")
    if not scope:
        raise ContextError("declared-scope must be non-empty when freezing a ticket")
    _verify_issue_slice(issue_text, spec, spec_relative, scope)
    dod = parsed["dod"]
    context: Dict[str, Any] = {
        "schema": CONTEXT_SCHEMA,
        "issue_id": issue_id,
        "issue_path": issue_relative,
        "feature_dir": feature_relative,
        "spec_path": spec_relative,
        "config_path": config_relative,
        "target_branch": target,
        "target_commit": target_commit,
        "base": base,
        "source_head": source_head,
        "source_tree": source_tree,
        "declared_scope": scope,
        "hashes": {
            "issue": issue_hash,
            "dod": _sha256_bytes(_canonical(dod)),
            "spec": _sha256_bytes(spec.read_bytes()),
            "config": _sha256_bytes(config_bytes),
        },
        "dod": dod,
    }
    stack_base, _ = artifact_scope.checked_stack_base(
        primary, feature_dir, target=target, main_branch=config_data["main-branch"],
    )
    if stack_base is not None:
        context["stack_base"] = stack_base
    context["gate2_context_digest"] = context_digest(context)
    _validate_context(context)
    if output_path is not None:
        output, _ = _inside(primary, output_path, "context output", must_exist=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(output, context)
    return copy.deepcopy(context)


def _reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContextError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _load_context(path: Any, candidate: bool = False) -> Dict[str, Any]:
    """Load a context without trusting handwritten or partially valid JSON."""
    source = Path(path)
    try:
        context = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except ContextError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContextError("cannot load context %s: %s" % (source, exc))
    if not isinstance(context, dict):
        raise ContextError("ticket context root must be an object")
    schema_path = SCHEMA_PATH
    if candidate and context.get("schema") == "agent-dev.ticket-context/v3":
        schema_path = SCHEMA_PATH.with_name("ticket-context-v3.schema.json")
    _validate_context(context, schema_path)
    return context


def load_context(path: Any) -> Dict[str, Any]:
    """Load only the current v4 contract; delivery never accepts historical v3."""
    return _load_context(path)


def load_candidate_context(path: Any) -> Dict[str, Any]:
    """Read original v3/v4 contexts without migration or rewriting their digest."""
    return _load_context(path, candidate=True)


def _snapshot_paths(feature_dir: Any, sha: str) -> Tuple[Path, Path]:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ContextError("expected-sha must be a lowercase SHA256")
    feature = Path(feature_dir).resolve(strict=True)
    directory = feature / "config-snapshots"
    paths = (directory / (sha + ".md"), directory / (sha + ".receipt.json"))
    if directory.is_symlink() or any(path.is_symlink() for path in paths):
        raise ContextError("config snapshot paths must not be symlinks")
    if directory.resolve().parent != feature:
        raise ContextError("config snapshot path escapes feature")
    return paths


def load_archived_config(feature_dir: Any, sha: str) -> Tuple[Path, Dict[str, Any]]:
    """Authenticate a registered, content-addressed configuration snapshot."""
    snapshot, receipt_path = _snapshot_paths(feature_dir, sha)
    data = snapshot.read_bytes()
    if _sha256_bytes(data) != sha:
        raise ContextError("config snapshot digest mismatch")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, UnicodeError) as exc:
        raise ContextError("invalid config snapshot receipt: %s" % exc)
    if (not isinstance(receipt, dict) or receipt.get("sha256") != sha
            or receipt.get("size") != len(data) or not isinstance(receipt.get("source_path"), str)):
        raise ContextError("invalid config snapshot receipt")
    config = agents_config.load(snapshot)
    if snapshot.read_bytes() != data:
        raise ContextError("config snapshot changed during validation")
    return snapshot, config


def archive_config(feature_dir: Any, source: Any, expected_sha: str) -> Dict[str, Any]:
    """Register exact original bytes; never overwrite conflicting archive files."""
    snapshot, receipt_path = _snapshot_paths(feature_dir, expected_sha)
    source = Path(source).resolve(strict=True)
    data = source.read_bytes()
    if _sha256_bytes(data) != expected_sha:
        raise ContextError("source config digest mismatch")
    agents_config.load(source)
    if source.read_bytes() != data:
        raise ContextError("source config changed during validation")
    if snapshot.exists() and snapshot.read_bytes() != data:
        raise ContextError("config snapshot conflicts with existing bytes")
    receipt = {"source_path": str(source), "sha256": expected_sha, "size": len(data)}
    if receipt_path.exists():
        load_archived_config(feature_dir, expected_sha)
        return json.loads(receipt_path.read_text(encoding="utf-8"))
    snapshot.parent.mkdir(exist_ok=True)
    if not snapshot.exists():
        with snapshot.open("xb") as stream:
            stream.write(data)
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    load_archived_config(feature_dir, expected_sha)
    return receipt


def _fraction(rate: Any) -> Fraction:
    if isinstance(rate, bool):
        raise ContextError("conformance rate must not be boolean")
    try:
        value = rate if isinstance(rate, Fraction) else Fraction(str(rate).strip())
    except (ValueError, ZeroDivisionError, AttributeError) as exc:
        raise ContextError("invalid conformance rate %r: %s" % (rate, exc))
    if not Fraction(0) < value <= Fraction(1):
        raise ContextError("conformance rate must be > 0 and <= 1")
    return value


def select_conformance(context: Dict[str, Any], rate: Any) -> bool:
    """Return a stable sampling decision independent of hash randomization/time."""
    if not isinstance(context, dict) or not isinstance(context.get("issue_id"), str):
        raise ContextError("sampling requires a context with issue_id")
    fraction = _fraction(rate)
    digest = context.get("gate2_context_digest") or context_digest(context)
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ContextError("sampling requires a valid gate2 context digest")
    if "gate2_context_digest" in context and digest != context_digest(context):
        raise ContextError("cannot sample from a tampered context digest")
    material = "%s:%s" % (digest, context["issue_id"])
    position = Fraction(int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest(), "big"),
                        1 << 256)
    return position < fraction


def _scope_root(pattern: str) -> str:
    match = re.search(r"[*?\[]", pattern)
    if not match:
        return str(Path(pattern).parent).replace(".", "", 1).strip("/")
    return pattern[:match.start()].rstrip("/")


def _scopes_overlap(left: str, right: str) -> bool:
    import fnmatch
    if left == right:
        return True
    left_glob, right_glob = bool(re.search(r"[*?\[]", left)), bool(re.search(r"[*?\[]", right))
    if not left_glob and not right_glob:
        return False
    if not left_glob:
        return fnmatch.fnmatchcase(left, right)
    if not right_glob:
        return fnmatch.fnmatchcase(right, left)
    first, second = _scope_root(left), _scope_root(right)
    if not first or not second:
        return True
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")


def check_plan(issue_path: Any, repo: Any, *, primary_repo: Any = None) -> Dict[str, Any]:
    """Read-only validation of the whole ticket group before any delivery mutation."""
    root = _repo_root(primary_repo if primary_repo is not None else repo)
    issue, _ = _inside(root, issue_path, "issue")
    errors: List[str] = []
    warnings: List[str] = []
    try:
        spec = find_spec(issue, root)
    except ContextError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": warnings, "issues": [], "all_statuses": {}}
    feature = issue.parent.parent
    records: Dict[str, Dict[str, Any]] = {}
    all_statuses: Dict[str, str] = {}
    for candidate in sorted((feature / "issues").glob("issue-*.md")):
        try:
            parsed = parse_issue(candidate)
            fm = parsed["frontmatter"]
            _validate_scope(feature, fm)
            ident = str(fm.get("issue-id") or "")
            if not re.fullmatch(r"issue-[0-9]+[a-z]?", ident):
                raise ContextError("%s has invalid/missing issue-id" % candidate.name)
            if ident in records:
                raise ContextError("duplicate issue-id in feature: %s" % ident)
            status = str(fm.get("status") or "")
            all_statuses[ident] = status
            candidate_spec = find_spec(candidate, root)
            records[ident] = {"path": candidate, "frontmatter": fm, "dod": parsed["dod"],
                              "spec": candidate_spec}
        except (ContextError, agents_config.ConfigError, OSError, ValueError) as exc:
            errors.append("%s: %s" % (candidate.name, exc))
    group = {ident: item for ident, item in records.items() if item["spec"] == spec}
    current_id = next((ident for ident, item in records.items() if item["path"] == issue), "")
    if current_id not in group:
        errors.append("%s: current ticket does not belong to its resolved tech-spec group" % issue.name)
    dependencies: Dict[str, List[str]] = {}
    scopes: Dict[str, List[str]] = {}
    for ident, item in group.items():
        fm = item["frontmatter"]
        if not 1 <= len(item["dod"]) <= 3:
            errors.append("%s: DoD must contain 1-3 commands" % ident)
        text = item["path"].read_text(encoding="utf-8")
        what = re.search(r"(?ms)^##\s+要做什么[^\n]*\n(.*?)(?=^##\s+|\Z)", text)
        if not what or not what.group(1).strip():
            errors.append("%s: 要做什么 must describe a demonstrable result" % ident)
        try:
            declared = _scope(fm, "declared-scope")
            if not declared:
                raise ContextError("declared-scope must not be empty")
            for pattern in declared:
                if pattern in {".", "./", "*", "**", "**/*", "./**"} or pattern.endswith("/**"):
                    errors.append("%s: unbounded directory glob is not an enumerable scope: %s" % (ident, pattern))
            scopes[ident] = declared
        except ContextError as exc:
            errors.append("%s: %s" % (ident, exc))
        blocked = fm.get("blocked-by")
        if blocked is None:
            blocked = []
        if not isinstance(blocked, list) or any(not isinstance(dep, str) or not dep.strip() for dep in blocked):
            errors.append("%s: blocked-by must be a list of issue IDs" % ident)
            blocked = []
        blocked = [dep.strip() for dep in blocked]
        if len(set(blocked)) != len(blocked):
            errors.append("%s: blocked-by contains duplicates" % ident)
        if ident in blocked:
            errors.append("%s: cannot depend on itself" % ident)
        for dep in blocked:
            if dep not in all_statuses:
                errors.append("%s: unknown blocked-by dependency %s" % (ident, dep))
        dependencies[ident] = blocked
    visiting, visited = set(), set()
    def visit(ident):
        if ident in visiting:
            errors.append("blocked-by cycle includes %s" % ident)
            return
        if ident in visited:
            return
        visiting.add(ident)
        for dep in dependencies.get(ident, []):
            if dep in group:
                visit(dep)
        visiting.remove(ident)
        visited.add(ident)
    for ident in sorted(group):
        visit(ident)
    def reaches(start, target, seen=None):
        if start == target:
            return True
        seen = set() if seen is None else seen
        if start in seen or start not in group:
            return False
        seen.add(start)
        return any(reaches(dep, target, seen) for dep in dependencies.get(start, []))
    identifiers = sorted(group)
    for index, left in enumerate(identifiers):
        for right in identifiers[index + 1:]:
            overlap = any(_scopes_overlap(a, b) for a in scopes.get(left, []) for b in scopes.get(right, []))
            if overlap and not reaches(left, right) and not reaches(right, left):
                errors.append("%s and %s have overlapping scope but no serial blocked-by path" % (left, right))
    try:
        import feature_closeout
        plan = feature_closeout.read_plan(feature)
        errors.extend(feature_closeout.validate_plan_data(plan))
        errors.extend(feature_closeout.validate_plan_sources(feature, plan))
        plan_spec_name = str(plan.get("tech_spec") or "")
        if plan_spec_name != spec.name:
            errors.append("acceptance plan tech_spec must equal %s" % spec.name)
    except (ImportError, ValueError, OSError, TypeError, PlanInputError) as exc:
        errors.append("acceptance plan validation failed: %s" % exc)
    if group:
        _check_spec_tree_closure(root, spec, group, scopes, all_statuses, errors, warnings)
    summary = [{"issue_id": ident, "status": all_statuses.get(ident),
                "blocked_by": dependencies.get(ident, []), "declared_scope": scopes.get(ident, [])}
               for ident in identifiers]
    return {"ok": not errors, "errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings)),
            "spec_path": spec.relative_to(root).as_posix(), "issues": summary,
            "all_statuses": all_statuses}


def _check_spec_tree_closure(root: Path, spec: Path, group: Dict[str, Dict[str, Any]],
                             scopes: Dict[str, List[str]], all_statuses: Dict[str, str],
                             errors: List[str], warnings: List[str]) -> None:
    """Close the spec §3.1 tree against every ticket's declared-scope and slices.

    Strict (errors) only for specs declaring ``spec-tree-version: 1``.  Legacy
    specs run the same checks diagnostically into ``warnings`` so in-flight
    features are informed without blocking gate 2.  Coverage skips tickets in
    ``cancelled`` / ``deferred`` states; merged/done tickets keep covering.
    """
    import spec_tree
    try:
        version = spec_tree.spec_tree_version(spec)
        spec_text = spec.read_text(encoding="utf-8")
        if version is not None:
            entries = spec_tree.parse_tree(spec_text)
        else:
            try:
                entries = spec_tree.parse_tree(spec_text)
            except spec_tree.SpecTreeError as exc:
                warnings.append("legacy spec 3.1 tree diagnostic failed: %s" % exc)
                return
    except spec_tree.SpecTreeError as exc:
        errors.append("spec 3.1 tree invalid: %s" % exc)
        return
    except (OSError, UnicodeError) as exc:
        errors.append("cannot read spec 3.1 tree: %s" % exc)
        return
    spec_relative = spec.relative_to(root).as_posix()
    sha = _sha256_bytes(spec.read_bytes())
    findings = errors if version is not None else warnings
    active = {ident: patterns for ident, patterns in scopes.items()
              if all_statuses.get(ident) not in {"cancelled", "deferred"}}
    if not active:
        return
    if entries is None:
        findings.append("spec %s has no §3.1 ```text tree block; tree/scope closure check skipped%s"
                        % (spec_relative, "" if version is None else " although spec-tree-version is declared"))
        if version is not None:
            errors.append("spec %s declares spec-tree-version but has no §3.1 tree; add the tree to the spec" % spec_relative)
        return
    tree_paths = {entry["path"] for entry in entries}
    for entry in sorted(entries, key=lambda item: item["path"]):
        if not any(match_any(entry["path"], patterns) for patterns in active.values()):
            findings.append("%s is in the spec 3.1 tree but no active ticket declared-scope covers it; re-split required"
                            % entry["path"])
    for ident in sorted(active):
        for pattern in active[ident]:
            if is_concrete_path(pattern):
                if pattern not in tree_paths:
                    findings.append("%s declared-scope path %s is absent from the spec 3.1 tree; "
                                    "scope must not exceed the plan" % (ident, pattern))
            elif not any(match_any(entry["path"], [pattern]) for entry in entries):
                findings.append("%s declared-scope glob %s matches no tree file; fix the pattern or list the files in the tree"
                                % (ident, pattern))
        text = group[ident]["path"].read_text(encoding="utf-8")
        expected = spec_tree.projection(entries, active[ident])
        try:
            if version is None and not spec_tree.has_slice_section(text):
                continue
            spec_tree.verify_slice(text, spec_relative, sha, expected)
        except spec_tree.SpecTreeError as exc:
            findings.append("%s: %s" % (ident, exc))


def _print(context: Dict[str, Any]) -> None:
    print(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--issue", required=True)
    create.add_argument("--repo", required=True)
    create.add_argument("--output")
    create.add_argument("--primary-repo", help="主工作区；票 worktree 的 dev/ 软链指向此处时使用")
    for name in ("show", "validate"):
        sub = subparsers.add_parser(name)
        sub.add_argument("context", nargs="?")
        sub.add_argument("--context", dest="context_option")
    plan = subparsers.add_parser("check-plan")
    plan.add_argument("--issue", required=True)
    plan.add_argument("--repo", required=True)
    plan.add_argument("--primary-repo")
    archive = subparsers.add_parser("archive-config")
    archive.add_argument("--feature-dir", required=True)
    archive.add_argument("--source", required=True)
    archive.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            context = create_context(args.issue, args.repo, args.output, primary_repo=args.primary_repo)
            _print(context)
        elif args.command == "archive-config":
            _print(archive_config(args.feature_dir, args.source, args.expected_sha))
        elif args.command == "check-plan":
            result = check_plan(args.issue, args.repo, primary_repo=args.primary_repo)
            _print(result)
            return 0 if result["ok"] else 1
        else:
            path = args.context_option or args.context
            if not path:
                raise ContextError("%s requires a context path" % args.command)
            context = load_context(path)
            if args.command == "show":
                _print(context)
            else:
                print(json.dumps({"ok": True, "issue_id": context["issue_id"],
                                  "gate2_context_digest": context["gate2_context_digest"]},
                                 ensure_ascii=False, sort_keys=True))
        return 0
    except (ContextError, agents_config.ConfigError, json_schema.SchemaError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
