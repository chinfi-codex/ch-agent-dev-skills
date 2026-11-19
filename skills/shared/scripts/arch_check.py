#!/usr/bin/env python3
"""Deterministic Python ARCH-CHECK.

The single machine-readable architecture contract is ``docs/ARCH-CHECK.json``;
``docs/ARCHITECTURE.md`` is its deterministic rendering: an eleven-section
architecture overview plus the layered contract and rule appendices.  Checks
are Python-AST based and run at the feature candidate gate or from the local
pre-push hook.  There is deliberately no ticket-level architecture gate.

CLI:
    arch_check.py check   --repo REPO [--head REV|--worktree] [--rules PATH]
    arch_check.py validate --repo REPO [--rules PATH]
    arch_check.py render write|check --repo REPO [--head REV|--worktree]
    arch_check.py init    --repo REPO
    arch_check.py install-hook --repo REPO

Exit codes: 0 pass, 1 violations/mismatch, 2 input or rules error.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import json_schema as js  # noqa: E402

SCHEMA_NAME = "agent-dev.arch-check/v1"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "arch-check.schema.json"
RULES_REL = "docs/ARCH-CHECK.json"
GUIDE_REL = "docs/ARCHITECTURE.md"
LEGACY_GRAPH_REL = "docs/architecture"
SEVERITIES = ("error", "warn")
COMMENT_KINDS = ("commented_code", "placeholder", "restating", "long_line", "long_docstring")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
STEP_RE = re.compile(r"Step\s*1", re.I)
STEP2_RE = re.compile(r"Step\s*2", re.I)
PYTHON_SUFFIX = ".py"
SKIP_DIRS = {".git", ".hg", ".svn", ".worktrees", "__pycache__", ".venv", "venv", "node_modules", "dev", "tests", "test", "build", "dist"}


class InputError(Exception):
    pass


# ---------- deterministic helpers ----------


def _dedup(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _git(repo: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
        capture_output=True,
        text=text,
        check=False,
    )


def _resolve_repo(repo: Path) -> Path:
    path = Path(repo).expanduser().resolve()
    if not path.is_dir():
        raise InputError("repository directory does not exist: %s" % repo)
    return path


def _safe_relative_path(value: str, label: str) -> Optional[PurePosixPath]:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value or "\\" in value:
        raise InputError("%s must be a non-empty POSIX relative path: %r" % (label, value))
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InputError("%s must be a canonical repository-relative path: %s" % (label, value))
    return path


def _safe_glob(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value or "\\" in value:
        raise InputError("%s must be a non-empty POSIX glob: %r" % (label, value))
    path = PurePosixPath(value.replace("**/", ""))
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise InputError("%s must stay inside the repository: %s" % (label, value))
    return value


def _glob_to_regex(pattern: str) -> "re.Pattern[str]":
    index, out = 0, []
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$")


def _match_path(path: str, pattern: str) -> bool:
    if _glob_to_regex(pattern).match(path):
        return True
    return PurePosixPath(path).match(pattern)


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(_match_path(path, pattern) for pattern in patterns)


def _read_worktree(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


def _revision_python_files(repo: Path, revision: str) -> List[str]:
    result = _git(repo, "ls-tree", "-r", "--name-only", "-z", revision)
    if result.returncode != 0:
        raise InputError("cannot list revision %s: %s" % (revision, result.stderr.decode("utf-8", "replace").strip()))
    files = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", "replace")
        if not path.endswith(PYTHON_SUFFIX):
            continue
        if any(_skip_dir(part) for part in PurePosixPath(path).parts[:-1]):
            continue
        files.append(path)
    return sorted(files)


def _worktree_python_files(repo: Path) -> List[str]:
    files = []
    for directory, dirs, names in os.walk(str(repo), followlinks=False):
        dirs[:] = [name for name in dirs if not _skip_dir(name)]
        for name in names:
            if not name.endswith(PYTHON_SUFFIX):
                continue
            path = Path(directory) / name
            if path.is_file():
                files.append(path.relative_to(repo).as_posix())
    return sorted(files)


def _read_blobs(repo: Path, revision: str, paths: Sequence[str]) -> Dict[str, bytes]:
    if not paths:
        return {}
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None
    out: Dict[str, bytes] = {}
    try:
        for path in paths:
            proc.stdin.write(("%s:%s\n" % (revision, path)).encode("utf-8"))
            proc.stdin.flush()
            header = proc.stdout.readline()
            if not header:
                break
            parts = header.split()
            if len(parts) == 3 and parts[1] == b"blob":
                size = int(parts[2])
                data = proc.stdout.read(size)
                proc.stdout.read(1)
                out[path] = data
            elif len(parts) == 2 and parts[1] == b"missing":
                continue
            else:
                break
    finally:
        proc.stdin.close()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
    return out


def read_blob(repo: Path, revision: str, path: str) -> Optional[bytes]:
    """Read one repository-relative file at a revision (None when missing)."""
    _safe_relative_path(path, "path")
    result = _git(repo, "show", "%s:%s" % (revision, path))
    if result.returncode != 0:
        return None
    return result.stdout


# ---------- rules ----------


def _load_rules_bytes(raw: bytes, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("%s: invalid JSON (%s)" % (label, type(exc).__name__))
    if not isinstance(value, dict):
        raise InputError("%s: rules must be a JSON object" % label)
    return value


def load_rules_file(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError("cannot read rules %s: %s" % (path, exc))
    return _load_rules_bytes(raw, path.name)


def _validate_paths(value: str, label: str) -> None:
    _safe_relative_path(value, label)


def _validate_group_selector(selector: Any, groups: Set[str], label: str, allow_empty: bool = False) -> List[str]:
    errors = []
    if not isinstance(selector, list):
        return ["%s must be a list" % label]
    if not selector and not allow_empty:
        errors.append("%s must not be empty" % label)
    for item in selector:
        if not isinstance(item, str) or item not in groups:
            errors.append("%s references unknown group: %s" % (label, item))
    return errors


def _validate_overview(overview: Dict[str, Any]) -> List[str]:
    errors = []
    for field in ("structure", "diagram"):
        if "```" in str(overview.get(field) or ""):
            errors.append("architecture.overview.%s must not contain a triple-backtick fence" % field)
    notes = overview.get("notes") or {}
    if isinstance(notes, dict):
        for key, value in notes.items():
            if "```" in str(value or ""):
                errors.append("architecture.overview.notes.%s must not contain a triple-backtick fence" % key)
    return errors


def validate_rules(rules: Dict[str, Any]) -> List[str]:
    try:
        errors = list(js.load_validate(SCHEMA_PATH, rules))
    except js.SchemaError as exc:
        return ["rules schema is invalid: %s" % exc]
    if errors:
        return errors
    errors.extend(_validate_overview(rules["architecture"].get("overview") or {}))
    groups = set()
    layers = {layer.get("name") for layer in rules["architecture"]["layers"] if isinstance(layer, dict)}
    for group in rules["groups"]:
        ident = group["id"]
        if ident in groups:
            errors.append("duplicate group id: %s" % ident)
        groups.add(ident)
        if group["layer"] not in layers:
            errors.append("group %s references unknown layer: %s" % (ident, group["layer"]))
        for pattern in group["paths"]:
            try:
                _safe_glob(pattern, "group %s path" % ident)
            except InputError as exc:
                errors.append(str(exc))
    for root in rules["source_roots"]:
        if root == ".":
            continue
        try:
            _validate_paths(root, "source_roots entry")
        except InputError as exc:
            errors.append(str(exc))
    seen_rules = set()
    for category in ("forbidden", "cycles", "banned_calls", "methods", "types", "comments", "duplication"):
        for rule in rules["rules"][category]:
            ident = rule["id"]
            if not RULE_ID_RE.fullmatch(ident):
                errors.append("invalid rule id: %s" % ident)
            if ident in seen_rules:
                errors.append("duplicate rule id: %s" % ident)
            seen_rules.add(ident)
            if not STEP_RE.search(rule["comment"]) or not STEP2_RE.search(rule["comment"]):
                errors.append("rule %s comment must contain Step 1 and Step 2" % ident)
            if category == "forbidden":
                errors.extend(_validate_group_selector(rule["from"], groups, "rule %s from" % ident))
                errors.extend(_validate_group_selector(rule["to"], groups, "rule %s to" % ident))
            elif category == "cycles":
                errors.extend(_validate_group_selector(rule["groups"], groups, "rule %s groups" % ident, allow_empty=True))
            elif category == "banned_calls":
                errors.extend(_validate_group_selector(rule["from"], groups, "rule %s from" % ident, allow_empty=True))
                for pattern in rule["calls"]:
                    if not isinstance(pattern, str) or not pattern:
                        errors.append("rule %s has an empty call pattern" % ident)
            elif category in {"methods", "types", "comments", "duplication"}:
                for pattern in rule.get("exclude", []):
                    try:
                        _safe_glob(pattern, "rule %s exclude" % ident)
                    except InputError as exc:
                        errors.append(str(exc))
                for pattern in rule.get("allow", []):
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append("rule %s allow pattern is invalid: %s" % (ident, exc))
    return _dedup(errors)


# ---------- Python analysis ----------


def _module_name_for_root(path: str, root: str) -> Optional[str]:
    pure = PurePosixPath(path)
    if pure.suffix != PYTHON_SUFFIX:
        return None
    parts = list(pure.parts)
    root_parts = [] if root == "." else list(PurePosixPath(root).parts)
    if root_parts and parts[: len(root_parts)] != root_parts:
        return None
    rest = parts[len(root_parts):]
    if not rest:
        return None
    if rest[-1] == "__init__.py":
        rest = rest[:-1]
    elif rest[-1].endswith(PYTHON_SUFFIX):
        rest[-1] = rest[-1][: -len(PYTHON_SUFFIX)]
    else:
        return None
    return ".".join(rest) if rest else None


def _module_name(path: str, source_roots: Sequence[str]) -> Optional[str]:
    for root in sorted(source_roots, key=lambda value: (-len(value), value)):
        name = _module_name_for_root(path, root)
        if name:
            return name
    return None


def _module_index(files: Sequence[str], source_roots: Sequence[str]) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for path in sorted(files):
        for root in source_roots:
            name = _module_name_for_root(path, root)
            if name:
                index.setdefault(name, path)
    return index


def _current_module(path: str, source_roots: Sequence[str]) -> Optional[str]:
    return _module_name(path, source_roots)


def _package_parts(path: str, source_roots: Sequence[str]) -> List[str]:
    module = _current_module(path, source_roots)
    if module is None:
        return []
    parts = module.split(".")
    if PurePosixPath(path).name != "__init__.py":
        parts = parts[:-1]
    return parts


def _longest_prefix(index: Dict[str, str], name: str) -> Optional[str]:
    parts = name.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in index:
            return index[candidate]
        parts.pop()
    return None


def _resolve_import(
    node: ast.AST,
    path: str,
    source_roots: Sequence[str],
    index: Dict[str, str],
) -> List[Tuple[str, int, str]]:
    """Return (target_path, line, imported_module_name) for internal imports."""
    targets: List[Tuple[str, int, str]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name
            target = _longest_prefix(index, name)
            if target:
                targets.append((target, getattr(node, "lineno", 1), name))
    elif isinstance(node, ast.ImportFrom):
        base: List[str] = []
        if node.level:
            package = _package_parts(path, source_roots)
            if node.level > len(package) + 1:
                return targets
            base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
        if node.module:
            base.extend(node.module.split("."))
        for alias in node.names:
            if alias.name == "*":
                candidate_parts = list(base)
            else:
                candidate_parts = list(base) + [alias.name]
            target = _longest_prefix(index, ".".join(candidate_parts))
            if target is None and base:
                target = _longest_prefix(index, ".".join(base))
            if target:
                targets.append((target, getattr(node, "lineno", 1), ".".join(candidate_parts)))
    return targets


def _alias_map(tree: ast.AST, path: str, source_roots: Sequence[str]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    package = ".".join(_package_parts(path, source_roots))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    aliases.setdefault(head, head)
        elif isinstance(node, ast.ImportFrom):
            base_parts: List[str] = []
            if node.level:
                parts = _package_parts(path, source_roots)
                if node.level > len(parts) + 1:
                    continue
                base_parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
            if node.module:
                base_parts.extend(node.module.split("."))
            base = ".".join(base_parts)
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                aliases[bound] = (base + "." + alias.name) if base else alias.name
    if package:
        aliases.setdefault("__package__", package)
    return aliases


def _call_name(node: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value, aliases)
        if base is None:
            return None
        return base + "." + node.attr
    return None


def _function_params(node: ast.AST, count_varargs: bool) -> int:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return 0
    args = node.args
    names = list(args.posonlyargs) + list(args.args)
    if names and names[0].arg in {"self", "cls"}:
        names = names[1:]
    count = len(names) + len(args.kwonlyargs)
    if count_varargs:
        count += 1 if args.vararg is not None else 0
        count += 1 if args.kwarg is not None else 0
    return count


def _docstring_nodes(tree: ast.AST) -> Iterable[Tuple[ast.AST, ast.Constant]]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            yield first.value, first.value


def _looks_like_code(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith(("todo", "fixme", "note:", "step ", "see ", "http://", "https://", "type:", "noqa", "pylint:", "fmt:", "pragma:", "-*-", "!")):
        return False
    if re.match(r"^[\w.]+\s*=\s*.+$", stripped):
        return True
    if re.match(r"^(?:if|elif|else|for|while|def|class|return|import|from|try|except|finally|with|assert|raise|yield|await|async|print|del|global|nonlocal|pass|break|continue)\b", stripped):
        return True
    if re.search(r"(?:==|!=|<=|>=|->)", stripped) and not stripped.endswith("."):
        return True
    if re.match(r"^[\w.]+\s*\(.*\)\s*(?:#.*)?$", stripped) and not stripped.endswith("."):
        return True
    return False


def _comment_text(token: str) -> str:
    return token.lstrip("#").strip()


def _comment_findings(
    path: str,
    source: str,
    tree: ast.AST,
    rule: Dict[str, Any],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    ident = rule["id"]
    severity = rule["severity"]
    kinds = set(rule["kinds"])
    allow = [re.compile(pattern) for pattern in rule["allow"]]
    max_lines = rule["max_lines"]
    max_doc = rule["max_docstring_lines"]
    comments: List[Tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                comments.append((token.start[0], _comment_text(token.string)))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return findings
    for line, text in comments:
        if not text:
            if "placeholder" in kinds:
                findings.append(_finding(ident, severity, path, line, "empty comment", rule["comment"]))
            continue
        if any(pattern.search(text) for pattern in allow):
            continue
        if "commented_code" in kinds and _looks_like_code(text):
            findings.append(_finding(ident, severity, path, line, "commented-out code", rule["comment"]))
        if "placeholder" in kinds and re.match(r"^(?:TODO|FIXME|XXX|TBD)\b", text, re.I):
            findings.append(_finding(ident, severity, path, line, "placeholder comment", rule["comment"]))
        if "restating" in kinds and _restates_code(text, line, source):
            findings.append(_finding(ident, severity, path, line, "comment restates the next code line", rule["comment"]))
    if "long_line" in kinds:
        run_start, run_last = None, None
        for line, _text in comments:
            if run_start is None:
                run_start, run_last = line, line
            elif line == run_last + 1:
                run_last = line
            else:
                if run_last - run_start + 1 > max_lines:
                    findings.append(_finding(ident, severity, path, run_start,
                                             "comment block is %d lines (limit %d)" % (run_last - run_start + 1, max_lines),
                                             rule["comment"]))
                run_start, run_last = line, line
        if run_start is not None and run_last is not None and run_last - run_start + 1 > max_lines:
            findings.append(_finding(ident, severity, path, run_start,
                                     "comment block is %d lines (limit %d)" % (run_last - run_start + 1, max_lines),
                                     rule["comment"]))
    if "long_docstring" in kinds:
        for node, constant in _docstring_nodes(tree):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start) or start
            if end - start + 1 > max_doc:
                findings.append(_finding(ident, severity, path, start,
                                         "docstring is %d lines (limit %d)" % (end - start + 1, max_doc),
                                         rule["comment"]))
    return findings


_RESTATING_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
    "this", "that", "it", "as", "by", "from", "be", "if", "then", "else", "return",
}


def _restates_code(text: str, line: int, source: str) -> bool:
    words = {word.lower() for word in re.findall(r"[A-Za-z_]\w*", text)} - _RESTATING_STOP
    if not words or len(words) > 8:
        return False
    lines = source.splitlines()
    for offset in range(1, 4):
        index = line - 1 + offset
        if index >= len(lines):
            break
        candidate = lines[index].strip()
        if not candidate or candidate.startswith("#"):
            continue
        code_words = {word.lower() for word in re.findall(r"[A-Za-z_]\w*", candidate)} - _RESTATING_STOP
        if code_words and len(words & code_words) / len(words) >= 0.8:
            return True
        break
    return False


def _finding(rule: str, severity: str, path: str, line: int, message: str, remediation: str) -> Dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "path": path,
        "line": int(line) if line else 1,
        "message": message,
        "remediation": remediation,
    }


def _normalized_lines(source: str) -> List[str]:
    lines = []
    for raw in source.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(re.sub(r"\s+", " ", stripped))
    return lines


def _duplication_findings(files: Dict[str, str], rules: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rule in rules:
        excluded = rule["exclude"]
        windows: Dict[Tuple[str, ...], Tuple[str, int]] = {}
        duplicates: Dict[Tuple[str, ...], List[Tuple[str, int]]] = {}
        total = 0
        for path in sorted(files):
            if _matches_any(path, excluded):
                continue
            lines = _normalized_lines(files[path])
            if len(lines) < rule["min_lines"]:
                continue
            for start in range(0, len(lines) - rule["min_lines"] + 1):
                window = tuple(lines[start:start + rule["min_lines"]])
                total += 1
                first = windows.get(window)
                if first is None:
                    windows[window] = (path, start + 1)
                    continue
                duplicates.setdefault(window, [first]).append((path, start + 1))
        if not total or not duplicates:
            continue
        duplicated = sum(len(entries) - 1 for entries in duplicates.values())
        rate = int(round(duplicated * 100 / total))
        if rate <= rule["max_rate"]:
            continue
        pairs = []
        for entries in list(duplicates.values())[:20]:
            for path, line in entries[:4]:
                pairs.append("%s:%d" % (path, line))
        findings.append(_finding(
            rule["id"], rule["severity"], pairs[0].split(":", 1)[0], int(pairs[0].split(":", 1)[1]),
            "duplication rate %d%% (limit %d%%); examples: %s" % (rate, rule["max_rate"], ", ".join(pairs)),
            rule["comment"],
        ))
    return findings


def _edges_for_file(
    path: str,
    tree: ast.AST,
    source_roots: Sequence[str],
    index: Dict[str, str],
) -> List[Tuple[str, str, int, str]]:
    edges: List[Tuple[str, str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for target, line, name in _resolve_import(node, path, source_roots, index):
                edges.append((path, target, line, name))
    return edges


def _group_for_path(path: str, groups: Sequence[Dict[str, Any]]) -> Optional[str]:
    for group in sorted(groups, key=lambda item: item["id"]):
        if _matches_any(path, group["paths"]):
            return group["id"]
    return None


def _cycle_findings(
    edges: Sequence[Tuple[str, str]],
    edge_example: Dict[Tuple[str, str], Tuple[str, int]],
    rules: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rule in rules:
        allowed = set(rule["groups"]) if rule["groups"] else None
        adjacency: Dict[str, Set[str]] = {}
        for source, target in edges:
            if allowed is not None and (source not in allowed or target not in allowed):
                continue
            adjacency.setdefault(source, set()).add(target)
        cycles: Set[Tuple[str, ...]] = set()
        for start in sorted(adjacency):
            stack: List[str] = []
            visiting: Set[str] = set()

            def visit(node: str) -> None:
                if node in visiting:
                    index = stack.index(node)
                    cycle = tuple(stack[index:])
                    if cycle:
                        rotations = [cycle[i:] + cycle[:i] for i in range(len(cycle))]
                        cycles.add(min(rotations))
                    return
                if node in stack:
                    return
                stack.append(node)
                visiting.add(node)
                for target in sorted(adjacency.get(node, ())):
                    visit(target)
                visiting.discard(node)
                stack.pop()

            visit(start)
        for cycle in sorted(cycles)[:50]:
            path = list(cycle) + [cycle[0]]
            examples = []
            first = None
            for left, right in zip(cycle, list(cycle[1:]) + [cycle[0]]):
                example = edge_example.get((left, right))
                if example:
                    first = first or example
                    examples.append("%s -> %s (%s:%d)" % (left, right, example[0], example[1]))
            findings.append(_finding(
                rule["id"], rule["severity"], first[0] if first else "", first[1] if first else 1,
                "dependency cycle: %s%s" % (" -> ".join(path),
                                            ("; " + "; ".join(examples)) if examples else ""),
                rule["comment"],
            ))
    return findings


def _call_findings(
    path: str,
    tree: ast.AST,
    group: Optional[str],
    source_roots: Sequence[str],
    rules: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    aliases = _alias_map(tree, path, source_roots)
    for rule in rules:
        if rule["from"] and (group is None or group not in set(rule["from"])):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func, aliases)
            if name is None:
                continue
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in rule["calls"]):
                findings.append(_finding(rule["id"], rule["severity"], path, getattr(node, "lineno", 1),
                                         "banned call: %s" % name, rule["comment"]))
    return findings


def _method_findings(path: str, tree: ast.AST, rules: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rule in rules:
        if _matches_any(path, rule["exclude"]):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                count = _function_params(node, rule["count_varargs"])
                if count > rule["max_params"]:
                    findings.append(_finding(rule["id"], rule["severity"], path, node.lineno,
                                             "%s has %d parameters (limit %d)" % (node.name, count, rule["max_params"]),
                                             rule["comment"]))
    return findings


def _type_findings(path: str, tree: ast.AST, rules: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for rule in rules:
        if _matches_any(path, rule["exclude"]):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                lines = end - node.lineno + 1
                if lines > rule["max_lines"]:
                    findings.append(_finding(rule["id"], rule["severity"], path, node.lineno,
                                             "class %s is %d lines (limit %d)" % (node.name, lines, rule["max_lines"]),
                                             rule["comment"]))
    return findings


def _analyze(
    repo: Path,
    files: Sequence[str],
    blobs: Dict[str, bytes],
    rules: Dict[str, Any],
    revision_label: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    findings: List[Dict[str, Any]] = []
    infos: List[str] = []
    parsed: Dict[str, ast.AST] = {}
    sources: Dict[str, str] = {}
    for path in files:
        raw = blobs.get(path)
        if raw is None:
            infos.append("%s: file disappeared at %s" % (path, revision_label))
            continue
        try:
            source = raw.decode("utf-8")
        except UnicodeError:
            infos.append("%s: not UTF-8; skipped" % path)
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            infos.append("%s:%s: syntax error; skipped (%s)" % (path, exc.lineno or 1, exc.msg))
            continue
        sources[path] = source
        parsed[path] = tree
    source_roots = rules["source_roots"]
    index = _module_index(list(parsed), source_roots)
    groups = rules["groups"]
    group_by_path: Dict[str, Optional[str]] = {path: _group_for_path(path, groups) for path in parsed}
    edges: List[Tuple[str, str]] = []
    edge_example: Dict[Tuple[str, str], Tuple[str, int]] = {}
    forwarded: List[Tuple[str, str, int, str]] = []
    for path, tree in parsed.items():
        for source, target, line, name in _edges_for_file(path, tree, source_roots, index):
            from_group = group_by_path.get(source)
            to_group = group_by_path.get(target)
            if from_group and to_group and from_group != to_group:
                edges.append((from_group, to_group))
                edge_example.setdefault((from_group, to_group), (source, line))
            forwarded.append((source, target, line, name))
    forbidden = rules["rules"]["forbidden"]
    for rule in forbidden:
        from_groups = set(rule["from"])
        to_groups = set(rule["to"])
        for source, target, line, name in forwarded:
            from_group = group_by_path.get(source)
            to_group = group_by_path.get(target)
            if from_group in from_groups and to_group in to_groups:
                findings.append(_finding(
                    rule["id"], rule["severity"], source, line,
                    "%s imports %s (%s -> %s)" % (from_group, to_group, name, to_group),
                    rule["comment"],
                ))
    findings.extend(_cycle_findings(edges, edge_example, rules["rules"]["cycles"]))
    for path, tree in parsed.items():
        group = group_by_path.get(path)
        findings.extend(_call_findings(path, tree, group, source_roots, rules["rules"]["banned_calls"]))
        findings.extend(_method_findings(path, tree, rules["rules"]["methods"]))
        findings.extend(_type_findings(path, tree, rules["rules"]["types"]))
        for rule in rules["rules"]["comments"]:
            if _matches_any(path, rule["exclude"]):
                continue
            findings.extend(_comment_findings(path, sources[path], tree, rule))
    findings.extend(_duplication_findings(sources, rules["rules"]["duplication"]))
    findings = _sort_findings(findings)
    return findings, infos


def _sort_findings(findings: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for item in findings:
        key = (item["severity"], item["rule"], item["path"], item["line"], item["message"])
        unique.setdefault(key, item)
    return [unique[key] for key in sorted(unique)]


# ---------- checking ----------


def _revision_blobs(repo: Path, head: str, files: Sequence[str], worktree: bool) -> Dict[str, bytes]:
    if worktree:
        blobs = {}
        for path in files:
            data = _read_worktree(repo / path)
            if data is not None:
                blobs[path] = data
        return blobs
    return _read_blobs(repo, head, files)


def check_repo(
    repo: Path,
    head: str = "HEAD",
    rules: Optional[Dict[str, Any]] = None,
    worktree: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    repo = _resolve_repo(repo)
    if rules is None:
        raw = _read_worktree(repo / RULES_REL) if worktree else read_blob(repo, head, RULES_REL)
        if raw is None:
            return [], ["%s: rules not found; arch-check skipped" % RULES_REL], {}
        rules = _load_rules_bytes(raw, RULES_REL)
    errors = validate_rules(rules)
    if errors:
        raise InputError("; ".join(errors))
    files = _worktree_python_files(repo) if worktree else _revision_python_files(repo, head)
    if not files:
        return [], ["no Python files found; arch-check skipped"], rules
    blobs = _revision_blobs(repo, head, files, worktree)
    findings, infos = _analyze(repo, files, blobs, rules, "working tree" if worktree else head)
    if not worktree:
        if _git(repo, "cat-file", "-e", "%s:%s" % (head, LEGACY_GRAPH_REL)).returncode == 0:
            infos.append("%s exists but is no longer read; delete it after creating %s" % (LEGACY_GRAPH_REL, RULES_REL))
    return findings, _dedup(infos), rules


# ---------- guide rendering ----------


def _md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


_EMPTY_SECTION = "> 未登记；首次架构变更时由 `/tech-spec` 3.2 按本节要求补全。"


def _md_value(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return _md_escape(text) if text else "—"


def _md_list(values: Any) -> str:
    if not isinstance(values, list):
        return "—"
    items = [str(item).strip() for item in values if str(item).strip()]
    return _md_escape(", ".join(items)) if items else "—"


def _has_content(value: Any) -> bool:
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return bool("" if value is None else str(value).strip())


def _note(overview: Dict[str, Any], key: str) -> str:
    notes = overview.get("notes") or {}
    return str(notes.get(key) or "").strip() if isinstance(notes, dict) else ""


def _empty_section(lines: List[str], overview: Dict[str, Any], key: str) -> None:
    lines.extend([_note(overview, key) or _EMPTY_SECTION, ""])


def _fenced(lines: List[str], body: Any, language: str, note: str = "") -> None:
    text = "" if body is None else str(body).strip("\n")
    if text.strip():
        lines.extend(["```%s" % language, text, "```", ""])
    else:
        lines.extend([note or _EMPTY_SECTION, ""])


def _table(lines: List[str], headers: List[str], rows: List[List[str]]) -> None:
    lines.append("| %s |" % " | ".join(headers))
    lines.append("| %s |" % " | ".join("---" for _ in headers))
    lines.extend("| %s |" % " | ".join(row) for row in rows)
    lines.append("")


def _bullets(lines: List[str], entries: List[Tuple[str, str]]) -> None:
    for label, value in entries:
        lines.append("- %s：%s" % (label, value))
    lines.append("")


def render_guide(rules: Dict[str, Any]) -> bytes:
    architecture = rules["architecture"]
    overview = architecture.get("overview") or {}
    project = overview.get("project") or {}
    deployment = overview.get("deployment") or {}
    security = overview.get("security") or {}
    development = overview.get("development") or {}
    lines = [
        "# %s" % architecture["title"],
        "",
        "> 本页由 `docs/ARCH-CHECK.json` 确定性生成；只编辑该契约文件，不要手改本页。",
        "> 编码前先读本页；首次架构变更时由 `/tech-spec` 3.2 补全 1–10 节。票级不设架构门，feature candidate 的 `freeze / check` 强制校验。",
        "",
        architecture["summary"],
        "",
        "## 1. 项目结构",
        "",
    ]
    _fenced(lines, overview.get("structure"), "text", _note(overview, "structure"))
    lines.extend(["## 2. 高层系统图", ""])
    _fenced(lines, overview.get("diagram"), "mermaid", _note(overview, "diagram"))

    lines.extend(["## 3. 核心组件", ""])
    components = overview.get("components") or []
    if components:
        _table(
            lines,
            ["组件", "职责", "技术", "部署"],
            [[
                _md_value(item.get("name")),
                _md_value(item.get("description")),
                _md_list(item.get("technologies")),
                _md_value(item.get("deployment")),
            ] for item in components],
        )
    else:
        _empty_section(lines, overview, "components")

    lines.extend(["## 4. 数据存储", ""])
    data_stores = overview.get("data_stores") or []
    if data_stores:
        _table(
            lines,
            ["存储", "类型", "用途", "关键数据"],
            [[
                _md_value(item.get("name")),
                _md_value(item.get("type")),
                _md_value(item.get("purpose")),
                _md_list(item.get("keys")),
            ] for item in data_stores],
        )
    else:
        _empty_section(lines, overview, "data_stores")

    lines.extend(["## 5. 外部集成", ""])
    integrations = overview.get("integrations") or []
    if integrations:
        _table(
            lines,
            ["服务", "用途", "集成方式"],
            [[
                _md_value(item.get("name")),
                _md_value(item.get("purpose")),
                _md_value(item.get("method")),
            ] for item in integrations],
        )
    else:
        _empty_section(lines, overview, "integrations")

    lines.extend(["## 6. 部署与基础设施", ""])
    deployment_rows = [
        ("云平台", deployment.get("cloud")),
        ("关键服务", deployment.get("services")),
        ("CI/CD", deployment.get("ci_cd")),
        ("监控与日志", deployment.get("monitoring")),
    ]
    if any(_has_content(value) for _, value in deployment_rows):
        _bullets(lines, [(label, _md_list(value) if isinstance(value, list) else _md_value(value))
                         for label, value in deployment_rows])
    else:
        _empty_section(lines, overview, "deployment")

    lines.extend(["## 7. 安全", ""])
    security_rows = [
        ("认证", security.get("authentication")),
        ("授权", security.get("authorization")),
        ("加密", security.get("encryption")),
        ("安全实践", security.get("practices")),
    ]
    if any(_has_content(value) for _, value in security_rows):
        _bullets(lines, [(label, _md_list(value) if isinstance(value, list) else _md_value(value))
                         for label, value in security_rows])
    else:
        _empty_section(lines, overview, "security")

    lines.extend(["## 8. 开发与测试环境", ""])
    development_rows = [
        ("本地启动", development.get("setup")),
        ("测试框架", development.get("test_frameworks")),
        ("质量工具", development.get("quality_tools")),
    ]
    if any(_has_content(value) for _, value in development_rows):
        _bullets(lines, [(label, _md_list(value) if isinstance(value, list) else _md_value(value))
                         for label, value in development_rows])
    else:
        _empty_section(lines, overview, "development")

    lines.extend(["## 9. 演进与技术债", ""])
    roadmap = overview.get("roadmap") or []
    if roadmap:
        lines.extend("- %s" % _md_escape(str(item)) for item in roadmap)
        lines.append("")
    else:
        _empty_section(lines, overview, "roadmap")

    lines.extend(["## 10. 项目标识", ""])
    identity = [
        ("项目名称", project.get("name")),
        ("仓库", project.get("repository")),
        ("负责团队", project.get("team")),
        ("最近更新", project.get("updated")),
    ]
    if any(_has_content(value) for _, value in identity):
        _table(lines, ["项", "值"], [[label, _md_value(value)] for label, value in identity])
    else:
        _empty_section(lines, overview, "project")

    lines.extend([
        "## 11. 术语与缩写",
        "",
        "项目术语、缩写与 feature-module 归档见 [`docs/GLOSSARY.md`](GLOSSARY.md)；本页不复制术语表，避免双真源。",
        "",
        "## 附录 A：分层与组",
        "",
        "### 分层",
        "",
    ])
    if architecture["layers"]:
        _table(
            lines,
            ["层", "职责", "允许依赖"],
            [[
                _md_escape(layer["name"]),
                _md_escape(layer["description"]),
                _md_escape(", ".join(layer["may_depend_on"]) or "—"),
            ] for layer in architecture["layers"]],
        )
    else:
        lines.extend(["（未配置分层。）", ""])
    lines.extend(["### 组与路径", ""])
    if rules["groups"]:
        _table(
            lines,
            ["组", "层", "路径", "职责"],
            [[
                _md_escape(group["id"]),
                _md_escape(group["layer"]),
                _md_escape(", ".join(group["paths"])),
                _md_escape(group["description"]),
            ] for group in sorted(rules["groups"], key=lambda item: item["id"])],
        )
    else:
        lines.extend(["（未配置组。）", ""])

    lines.extend(["## 附录 B：依赖规则", ""])
    any_rule = False
    for rule in rules["rules"]["forbidden"]:
        any_rule = True
        lines.extend([
            "### %s" % rule["id"],
            "",
            "- 级别：`%s`" % rule["severity"],
            "- 禁止：%s → %s" % (", ".join(rule["from"]), ", ".join(rule["to"])),
            "- 修复：%s" % rule["comment"],
            "",
        ])
    for rule in rules["rules"]["cycles"]:
        any_rule = True
        lines.extend([
            "### %s" % rule["id"],
            "",
            "- 级别：`%s`" % rule["severity"],
            "- 禁止：%s 组内循环依赖" % (", ".join(rule["groups"]) or "全部组"),
            "- 修复：%s" % rule["comment"],
            "",
        ])
    if not any_rule:
        lines.extend(["（未配置依赖规则。）", ""])

    lines.extend(["## 附录 C：代码规则", ""])
    any_rule = False
    for rule in rules["rules"]["banned_calls"]:
        any_rule = True
        lines.extend([
            "### %s" % rule["id"],
            "",
            "- 级别：`%s`" % rule["severity"],
            "- 禁止调用：%s" % ", ".join(rule["calls"]),
            "- 修复：%s" % rule["comment"],
            "",
        ])
    for rule in rules["rules"]["methods"]:
        any_rule = True
        lines.extend([
            "### %s" % rule["id"],
            "",
            "- 级别：`%s`" % rule["severity"],
            "- 参数上限：%d（%s）" % (rule["max_params"], "计入 *args/**kwargs" if rule["count_varargs"] else "不计 *args/**kwargs"),
            "- 修复：%s" % rule["comment"],
            "",
        ])
    for rule in rules["rules"]["types"]:
        any_rule = True
        lines.extend([
            "### %s" % rule["id"],
            "",
            "- 级别：`%s`" % rule["severity"],
            "- 类行数上限：%d" % rule["max_lines"],
            "- 修复：%s" % rule["comment"],
            "",
        ])
    for rule in rules["rules"]["comments"]:
        any_rule = True
        lines.extend([
            "### %s" % rule["id"],
            "",
            "- 级别：`%s`" % rule["severity"],
            "- 检查：%s" % ", ".join(rule["kinds"]),
            "- 修复：%s" % rule["comment"],
            "",
        ])
    for rule in rules["rules"]["duplication"]:
        any_rule = True
        lines.extend([
            "### %s" % rule["id"],
            "",
            "- 级别：`%s`" % rule["severity"],
            "- 重复率上限：%d%%（窗口 %d 行）" % (rule["max_rate"], rule["min_lines"]),
            "- 修复：%s" % rule["comment"],
            "",
        ])
    if not any_rule:
        lines.extend(["（未配置代码规则。）", ""])

    lines.extend(["## 附录 D：放置规则", ""])
    if architecture["placement"]:
        lines.extend("- %s" % item for item in architecture["placement"])
    else:
        lines.append("（未配置放置规则。）")
    lines.extend([
        "",
        "## 附录 E：运行检查",
        "",
        "```bash",
        "python3 ../shared/scripts/arch_check.py check --repo <项目根> --head HEAD --rules docs/ARCH-CHECK.json",
        "```",
        "",
        "票级不设架构门；feature candidate 的 `freeze/check` 会强制运行；本地 pre-push 会提前拦截。",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def render_guide_check(repo: Path, head: str, rules: Dict[str, Any], worktree: bool = False) -> List[str]:
    expected = render_guide(rules)
    actual = _read_worktree(repo / GUIDE_REL) if worktree else read_blob(repo, head, GUIDE_REL)
    if actual is None:
        return ["%s is missing; run arch_check.py render write" % GUIDE_REL]
    if actual != expected:
        return ["%s is stale or hand-edited; run arch_check.py render write" % GUIDE_REL]
    return []


# ---------- init / hook ----------


def _template_rules() -> Dict[str, Any]:
    return {
        "schema": SCHEMA_NAME,
        "version": 1,
        "language": "python",
        "source_roots": ["."],
        "architecture": {
            "title": "项目架构",
            "summary": "按层分包：上层可以调用下层，下层不得反向依赖上层；跨层共享能力下沉到更低的层。",
            "layers": [],
            "placement": [],
            "overview": {
                "project": {"name": "", "repository": "", "team": "", "updated": ""},
                "structure": "",
                "diagram": "",
                "components": [],
                "data_stores": [],
                "integrations": [],
                "deployment": {"cloud": "", "services": [], "ci_cd": "", "monitoring": ""},
                "security": {"authentication": "", "authorization": "", "encryption": "", "practices": []},
                "development": {"setup": "", "test_frameworks": [], "quality_tools": []},
                "roadmap": [],
                "notes": {},
            },
        },
        "groups": [],
        "rules": {
            "forbidden": [],
            "cycles": [],
            "banned_calls": [],
            "methods": [],
            "types": [],
            "comments": [],
            "duplication": [],
        },
    }


def init_repo(repo: Path) -> Path:
    repo = _resolve_repo(repo)
    rules_path = repo / RULES_REL
    if rules_path.exists():
        raise InputError("%s already exists; refusing to overwrite" % RULES_REL)
    rules = _template_rules()
    guide_path = repo / GUIDE_REL
    if guide_path.exists() and guide_path.read_bytes() != render_guide(rules):
        raise InputError(
            "%s already exists and is not a managed render; move it before init" % GUIDE_REL)
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_bytes(_pretty_json(rules))
    guide_path.write_bytes(render_guide(rules))
    return rules_path


MARK_BEGIN = "# >>> arch-check (managed) >>>"
MARK_END = "# <<< arch-check (managed) <<<"


def _hook_block() -> str:
    python = shlex.quote(sys.executable)
    script = shlex.quote(str(Path(__file__).resolve()))
    return "\n".join([
        MARK_BEGIN,
        'if git cat-file -e "HEAD:docs/ARCH-CHECK.json" 2>/dev/null; then',
        '  %s %s check --repo "$(git rev-parse --show-toplevel)" --head HEAD --rules docs/ARCH-CHECK.json' % (python, script),
        '  status=$?',
        '  if [ "$status" -ne 0 ]; then',
        '    echo "arch-check blocked this push." >&2',
        '    echo "Step 1: fix the violations above; if a rule is wrong, update docs/ARCH-CHECK.json and run arch_check.py render write." >&2',
        '    echo "Step 2: commit the fix and push again. The feature candidate gate enforces the same rules." >&2',
        '    exit "$status"',
        '  fi',
        'elif [ -f "docs/ARCH-CHECK.json" ]; then',
        '  echo "arch-check: docs/ARCH-CHECK.json is present but not committed; commit it with docs/ARCHITECTURE.md to enable the pre-push check." >&2',
        'fi',
        MARK_END,
    ])


def install_hook(repo: Path) -> Path:
    repo = _resolve_repo(repo)
    result = _git(repo, "rev-parse", "--git-path", "hooks")
    if result.returncode != 0:
        raise InputError("not a git repository: %s" % repo)
    hooks_dir = Path(result.stdout.decode("utf-8", "replace").strip())
    if not hooks_dir.is_absolute():
        hooks_dir = (repo / hooks_dir).resolve()
    hook = hooks_dir / "pre-push"
    existing = ""
    if hook.exists():
        try:
            existing = hook.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise InputError("cannot read existing pre-push hook: %s" % exc)
        first = existing.splitlines()[0] if existing.splitlines() else ""
        if first.startswith("#!") and "sh" not in first:
            raise InputError("existing pre-push hook is not a POSIX shell script; refusing to modify it")
    block = _hook_block()
    if MARK_BEGIN in existing and MARK_END in existing:
        before, rest = existing.split(MARK_BEGIN, 1)
        _old, after = rest.split(MARK_END, 1)
        content = before.rstrip("\n") + "\n" + block + after
    else:
        content = (existing.rstrip("\n") + "\n\n" if existing.strip() else "#!/bin/sh\n\n") + block + "\n"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook.write_text(content, encoding="utf-8")
    hook.chmod(0o755)
    return hook


# ---------- reporting ----------


def _print_findings(findings: Sequence[Dict[str, Any]], infos: Sequence[str]) -> None:
    for item in findings:
        label = {"error": "FAIL", "warn": "WARN", "info": "INFO"}.get(item["severity"], "INFO")
        print("%s [%s] %s:%d — %s" % (label, item["rule"], item["path"], item["line"], item["message"]))
        for line in str(item["remediation"]).splitlines():
            print("  " + line.strip())
    for info in infos:
        print("INFO " + info)


def _summary(findings: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "errors": sum(1 for item in findings if item["severity"] == "error"),
        "warnings": sum(1 for item in findings if item["severity"] == "warn"),
        "infos": sum(1 for item in findings if item["severity"] == "info"),
    }


def _rules_digest(raw: bytes) -> str:
    return _sha256(raw)


def _load_rules_for_cli(repo: Path, head: str, rules_path: str, worktree: bool) -> Tuple[Dict[str, Any], bytes]:
    relative = PurePosixPath(rules_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise InputError("rules path must stay inside the repository: %s" % rules_path)
    raw = _read_worktree(repo / relative) if worktree else read_blob(repo, head, relative.as_posix())
    if raw is None:
        raise InputError("rules not found at %s: %s" % ("working tree" if worktree else head, rules_path))
    return _load_rules_bytes(raw, rules_path), raw


def _run_check(args) -> int:
    repo = _resolve_repo(Path(args.repo))
    try:
        rules, raw = _load_rules_for_cli(repo, args.head, args.rules, args.worktree)
        errors = validate_rules(rules)
        if errors:
            print("ERROR: " + "; ".join(errors), file=sys.stderr)
            return 2
        findings, infos, _ = check_repo(repo, args.head, rules=rules, worktree=args.worktree)
        guide_errors = render_guide_check(repo, args.head, rules, worktree=args.worktree)
        for error in guide_errors:
            findings.append(_finding(
                "architect-guide", "error", GUIDE_REL, 1, error,
                "Step 1: run `arch_check.py render write` after editing docs/ARCH-CHECK.json.\n"
                "Step 2: commit docs/ARCHITECTURE.md; never hand-edit it.",
            ))
        findings = _sort_findings(findings)
        _print_findings(findings, infos)
        summary = _summary(findings)
        summary["infos"] += len(infos)
        print("arch-check: %d error(s), %d warning(s); rules %s" % (
            summary["errors"], summary["warnings"], args.rules))
        if args.json:
            report = {
                "schema": "agent-dev.arch-check-report/v1",
                "rules_sha256": _rules_digest(raw),
                "findings": findings,
                "infos": list(infos),
                "summary": summary,
            }
            Path(args.json).write_bytes(_pretty_json(report))
        if summary["errors"]:
            return 1
        if args.strict and summary["warnings"]:
            return 1
        return 0
    except InputError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


def _run_validate(args) -> int:
    repo = _resolve_repo(Path(args.repo))
    try:
        rules, _raw = _load_rules_for_cli(repo, args.head, args.rules, args.worktree)
        errors = validate_rules(rules)
    except InputError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print("FAIL " + error)
        print("rules errors=%d" % len(errors))
        return 2
    print("rules OK: %s" % args.rules)
    return 0


def _run_render(args) -> int:
    repo = _resolve_repo(Path(args.repo))
    try:
        worktree = True if args.action == "write" else args.worktree
        rules, _raw = _load_rules_for_cli(repo, args.head, args.rules, worktree)
        errors = validate_rules(rules)
        if errors:
            print("ERROR: " + "; ".join(errors), file=sys.stderr)
            return 2
        expected = render_guide(rules)
        path = repo / GUIDE_REL
        if args.action == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
            print("guide written: %s" % GUIDE_REL)
            return 0
        actual = _read_worktree(path) if args.worktree else read_blob(repo, args.head, GUIDE_REL)
        if actual is None:
            print("FAIL %s is missing; run render write" % GUIDE_REL)
            return 1
        if actual != expected:
            print("FAIL %s is stale or hand-edited; run render write" % GUIDE_REL)
            return 1
        print("guide current: %s" % GUIDE_REL)
        return 0
    except InputError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


def _run_init(args) -> int:
    try:
        path = init_repo(Path(args.repo))
    except InputError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    print("created %s and %s; edit overview/groups/rules, then run render write" % (path.name, GUIDE_REL))
    return 0


def _run_install_hook(args) -> int:
    try:
        path = install_hook(Path(args.repo))
    except InputError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    print("installed arch-check pre-push hook: %s" % path)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="deterministic Python ARCH-CHECK")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(target, *, head=True, rules=True):
        target.add_argument("--repo", required=True)
        if head:
            target.add_argument("--head", default="HEAD")
            target.add_argument("--worktree", action="store_true")
        if rules:
            target.add_argument("--rules", default=RULES_REL)

    check_p = sub.add_parser("check")
    common(check_p)
    check_p.add_argument("--json")
    check_p.add_argument("--strict", action="store_true")
    check_p.set_defaults(func=_run_check)

    validate_p = sub.add_parser("validate")
    common(validate_p)
    validate_p.set_defaults(func=_run_validate)

    render_p = sub.add_parser("render")
    render_p.add_argument("action", choices=("write", "check"))
    common(render_p)
    render_p.set_defaults(func=_run_render)

    init_p = sub.add_parser("init")
    init_p.add_argument("--repo", required=True)
    init_p.set_defaults(func=_run_init)

    hook_p = sub.add_parser("install-hook")
    hook_p.add_argument("--repo", required=True)
    hook_p.set_defaults(func=_run_install_hook)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
