#!/usr/bin/env python3
"""Parse the tech-spec §3.1 module tree and derive per-ticket plan slices.

The §3.1 tree is a machine contract (spec frontmatter ``spec-tree-version: 1``):
each file line is ``<repo-relative path> [新增|修改|删除] <one-line change note>``,
directory lines end with ``/`` and may carry a ``# comment``.  A strong planner
writes the tree once; weak implementers receive their slice verbatim instead of
re-deriving it, and ``ticket_context.check-plan`` closes the tree against every
ticket's ``declared-scope``.

Public API:
    spec_tree_version(spec_path) -> int | None
        Frontmatter ``spec-tree-version``; None means a legacy spec without the
        contract (closure checks run diagnostically, never as gate errors).
    parse_tree(spec_text) -> list[entry] | None
        Entries in source order: {path, action, note, line}.  None when §3.1
        carries no ``text`` tree block.  Malformed trees raise SpecTreeError.
    projection(entries, scope_patterns) -> list[str]
        Tree lines hit by any declared-scope pattern, spec order, deduplicated.
    slice_lines / verify_slice / write_slice
        The ticket 「方案切片」 section: deterministic generation (write_slice)
        and full-projection equality checks (verify_slice).  A slice is stale
        whenever the spec tree or the ticket scope changed without regeneration.

CLI:
    python3 spec_tree.py slice --issue ISSUE --repo REPO [--primary-repo REPO]
        Regenerate the 「方案切片」 section of one ticket from its spec and
        declared-scope.  Run after every scope revision, before check-plan.

Only Python's standard library and sibling contract modules are used.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Isolated CLI mode (-I) drops the script directory from sys.path; sibling
# imports in this package-less standard-library layer use the explicit insert.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents_config
import globmatch

TREE_VERSION = 1
ACTIONS = ("新增", "修改", "删除")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_SECTION_HEAD = re.compile(r"^###\s*3\.1(?![\d.])", re.M)
_NEXT_HEAD = re.compile(r"^#{1,3}\s", re.M)
_ACTION_BRACKET = re.compile(r"\[([^\[\]]+)\]")
_TRAILING_COMMENT = re.compile(r"\s+#.*$", re.S)
_DECOR_TOKEN = re.compile(r"^(?:│\s*|[├└]─*\s*|\s+)")
_SLICE_HEAD = "## 方案切片"
_SOURCE_LINE = re.compile(r"^来源：`([^`]+)`\s*§3\.1\s*$")
_SHA_LINE = re.compile(r"^来源 SHA256：`([0-9a-f]{64})`\s*$")
_FORMAT_LINE = re.compile(r"^切片格式：1\s*$")
_FENCE_OPEN = re.compile(r"^```text\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")


class SpecTreeError(ValueError):
    """The §3.1 tree or a ticket 方案切片 section is malformed or stale."""


def spec_tree_version(spec_path: Any) -> Optional[int]:
    """Read ``spec-tree-version`` from the spec frontmatter; None = legacy spec."""
    try:
        frontmatter = agents_config.read_frontmatter(Path(spec_path))
    except (agents_config.ConfigError, OSError, UnicodeError):
        # Legacy specs may lack frontmatter entirely; they never carry the contract.
        return None
    value = frontmatter.get("spec-tree-version")
    if value is None:
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or value != TREE_VERSION:
        raise SpecTreeError("unsupported spec-tree-version %r; only %d is defined" % (value, TREE_VERSION))
    return value


def _split_indent(line: str, lineno: int) -> Any:
    """Return (indent, rest) treating tree decorations as one indent unit."""
    lead = re.match(r"^[ \t]*", line).group(0)
    if "\t" in lead:
        raise SpecTreeError("line %d: tabs are not allowed in tree indentation" % lineno)
    indent, rest = 0, line
    while True:
        match = _DECOR_TOKEN.match(rest)
        if not match:
            break
        token = match.group(0)
        indent += 4 if token.strip() else len(token)
        rest = rest[match.end():]
    return indent, rest.rstrip()


def _validate_segment(name: str, lineno: int) -> str:
    if not name or name in {".", ".."}:
        raise SpecTreeError("line %d: invalid path segment %r" % (lineno, name))
    if name.startswith("/") or "\\" in name:
        raise SpecTreeError("line %d: path segment %r must be repo-relative without backslashes" % (lineno, name))
    if any(char in name for char in "*?["):
        raise SpecTreeError("line %d: tree file items must be concrete paths, globs are forbidden: %r" % (lineno, name))
    return name


def parse_tree(spec_text: str) -> Optional[List[Dict[str, Any]]]:
    """Parse the single ```text tree block inside §3.1. None when absent."""
    section = _section_text(spec_text)
    if section is None:
        return None
    block = _single_tree_block(section)
    if block is None:
        return None
    entries: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    items: List[Any] = []  # (lineno, indent, kind, payload)
    for offset, raw in enumerate(block.splitlines()):
        lineno = offset + 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent, rest = _split_indent(raw, lineno)
        match = _ACTION_BRACKET.search(rest)
        if match and match.group(1) in ACTIONS:
            items.append((lineno, indent, "file", (rest[:match.start()].strip(), match.group(1), rest[match.end():].strip())))
            continue
        if match:
            raise SpecTreeError("line %d: unknown tree action %r; allowed: %s" % (lineno, match.group(1), "/".join(ACTIONS)))
        candidate = _TRAILING_COMMENT.sub("", rest).strip()
        if candidate.endswith("/"):
            items.append((lineno, indent, "dir", _validate_segment(candidate[:-1].strip(), lineno)))
            continue
        raise SpecTreeError("line %d is neither a file line with [新增/修改/删除] nor a directory ending with '/': %r" % (lineno, stripped))
    if not items:
        raise SpecTreeError("§3.1 tree block has no file lines")
    root_name = _decide_root(items)
    stack: List[Any] = []  # (indent, name, is_root)
    for lineno, indent, kind, payload in items:
        if kind == "dir":
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, payload, root_name == payload and lineno == items[0][0]))
            continue
        entries.append(_file_entry(payload, indent, stack, root_name, lineno, seen))
    if len(entries) == 0:
        raise SpecTreeError("§3.1 tree block declares only directories and no file lines")
    return entries


def _decide_root(items: List[Any]) -> Optional[str]:
    """A display root is the first item when it is <repo>/ or its name prefixes descendants."""
    first = items[0]
    if first[2] != "dir":
        return None
    name = first[3]
    if name == "<repo>":
        return name
    for _, _, kind, payload in items[1:]:
        text = payload[0] if kind == "file" else payload
        if text.startswith(name + "/"):
            return name
    return None
    if root_name is not None and len(entries) == 0:
        raise SpecTreeError("§3.1 tree block declares only a root directory and no file lines")
    return entries


def _section_text(spec_text: str) -> Optional[str]:
    head = _SECTION_HEAD.search(spec_text)
    if not head:
        return None
    following = _NEXT_HEAD.search(spec_text, head.end())
    end = following.start() if following else len(spec_text)
    return spec_text[head.end():end]


def _single_tree_block(section: str) -> Optional[str]:
    lines = section.splitlines()
    blocks: List[List[str]] = []
    inside = False
    buffer: List[str] = []
    for line in lines:
        if inside:
            if _FENCE_CLOSE.match(line):
                blocks.append(buffer)
                buffer, inside = [], False
            else:
                buffer.append(line)
            continue
        if _FENCE_OPEN.match(line):
            inside = True
    if inside:
        raise SpecTreeError("§3.1 contains an unterminated ```text tree block")
    if len(blocks) > 1:
        raise SpecTreeError("§3.1 contains %d ```text tree blocks; exactly one is allowed" % len(blocks))
    return "\n".join(blocks[0]) if blocks else None


def _file_entry(payload: Any, indent: int, stack: List[Any], root_name: Optional[str],
                lineno: int, seen: Dict[str, int]) -> Dict[str, Any]:
    path_part, action, note = payload
    if not path_part:
        raise SpecTreeError("line %d: file line lacks a path before the action bracket" % lineno)
    if not note:
        raise SpecTreeError("line %d: file line lacks a one-line change note after the action bracket" % lineno)
    if path_part.startswith("/") or path_part.endswith("/"):
        raise SpecTreeError("line %d: file path %r must be a repo-relative file path without leading/trailing '/'"
                            % (lineno, path_part))
    while stack and stack[-1][0] >= indent:
        stack.pop()
    segments = [name for _, name, is_root in stack if not is_root]
    segments += [_validate_segment(part, lineno) for part in path_part.split("/")]
    path = "/".join(segments)
    if root_name is not None and path.startswith(root_name + "/"):
        path = path[len(root_name) + 1:]
    if path in seen:
        raise SpecTreeError("line %d: duplicate tree path %s (first at line %d)" % (lineno, path, seen[path]))
    seen[path] = lineno
    return {"path": path, "action": action, "note": note, "line": lineno}


def projection(entries: List[Dict[str, Any]], scope_patterns) -> List[str]:
    """Tree lines covered by the ticket scope, in spec order, deduplicated."""
    lines: List[str] = []
    seen = set()
    for entry in entries:
        if not globmatch.match_any(entry["path"], scope_patterns):
            continue
        line = "%s [%s] %s" % (entry["path"], entry["action"], entry["note"])
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def slice_section(spec_relative: str, spec_sha256: str, lines: List[str]) -> str:
    """Render the ticket 「方案切片」 section; the caller embeds it verbatim."""
    if not SHA256_RE.match(spec_sha256):
        raise SpecTreeError("spec sha256 %r is not a hex digest" % spec_sha256)
    body = "\n".join(["## 方案切片",
                      "",
                      "来源：`%s` §3.1" % spec_relative,
                      "来源 SHA256：`%s`" % spec_sha256,
                      "切片格式：1",
                      "",
                      "本节由 `spec_tree.py slice` 从 tech-spec 3.1 树确定性生成；"
                      "declared-scope 变更后必须重新生成，不得手改。",
                      "",
                      "```text"] + (lines or ["（本票 declared-scope 未命中 3.1 树文件；scope 或树必有错误，先修正再拆）"]) + ["```"])
    return body + "\n"


def _slice_span(issue_text: str) -> Optional[Any]:
    matches = list(re.finditer(r"^%s\s*$" % re.escape(_SLICE_HEAD), issue_text, re.M))
    if len(matches) > 1:
        raise SpecTreeError("ticket carries %d 方案切片 sections; exactly one is allowed" % len(matches))
    return matches[0] if matches else None


def _parse_slice(issue_text: str) -> Optional[Dict[str, Any]]:
    """Return {source, sha256, lines} from the existing section, or None."""
    match = _slice_span(issue_text)
    if match is None:
        return None
    following = re.search(r"^##\s", issue_text[match.end():], re.M)
    end = match.end() + following.start() if following else len(issue_text)
    section = issue_text[match.start():end]
    source = sha = None
    lines: List[str] = []
    inside = False
    for line in section.splitlines()[1:]:
        if inside:
            if _FENCE_CLOSE.match(line):
                inside = False
            else:
                lines.append(line)
            continue
        source_match = _SOURCE_LINE.match(line)
        if source_match:
            source = source_match.group(1)
            continue
        sha_match = _SHA_LINE.match(line)
        if sha_match:
            sha = sha_match.group(1)
            continue
        if _FORMAT_LINE.match(line):
            continue
        if _FENCE_OPEN.match(line):
            inside = True
    if source is None or sha is None:
        raise SpecTreeError("方案切片 section lacks 来源/来源 SHA256 metadata lines; regenerate it")
    return {"source": source, "sha256": sha, "lines": lines}


def has_slice_section(issue_text: str) -> bool:
    """True when a 方案切片 section exists; duplicate sections raise."""
    return _slice_span(issue_text) is not None


def verify_slice(issue_text: str, spec_relative: str, spec_sha256: str, expected_lines: List[str]) -> None:
    """Full-projection equality: the stored slice must equal the recomputed one."""
    stored = _parse_slice(issue_text)
    if stored is None:
        raise SpecTreeError("ticket lacks a 方案切片 section; run: python3 ../shared/scripts/spec_tree.py slice "
                            "--issue <票文件> --repo <项目根>")
    if stored["source"] != spec_relative:
        raise SpecTreeError("方案切片 来源 `%s` does not resolve to this ticket's spec `%s`; regenerate"
                            % (stored["source"], spec_relative))
    if stored["sha256"] != spec_sha256:
        raise SpecTreeError("方案切片 来源 SHA256 does not match the current spec hash; regenerate the slice")
    expected = list(expected_lines)
    if stored["lines"] == expected:
        return
    expected_set, stored_set = set(expected), set(stored["lines"])
    missing = [line for line in expected if line not in stored_set]
    extra = [line for line in stored["lines"] if line not in expected_set]
    if len(stored["lines"]) != len(stored_set):
        extra.append("方案切片 contains duplicate lines")
    detail = []
    if missing:
        detail.append("missing %s" % missing[0])
    if extra:
        detail.append("extra/changed %s" % extra[0])
    message = "方案切片 is stale vs the recomputed projection; run: python3 ../shared/scripts/spec_tree.py slice " \
              "--issue <票文件> --repo <项目根>"
    if detail:
        message = "%s (%s)" % (message, "; ".join(detail))
    raise SpecTreeError(message)


def write_slice(issue_path: Any, spec_relative: str, spec_sha256: str, lines: List[str]) -> None:
    """Replace (or insert before 备注) the ticket's 方案切片 section in place."""
    path = Path(issue_path)
    section = slice_section(spec_relative, spec_sha256, lines)
    text = path.read_text(encoding="utf-8")
    existing = _slice_span(text)
    if existing is not None:
        following = re.search(r"^##\s", text[existing.end():], re.M)
        end = existing.end() + following.start() if following else len(text)
        text = text[:existing.start()] + text[end:]
    head = re.search(r"^##\s+备注\s*$", text, re.M)
    if head:
        text = text[:head.start()] + section + "\n" + text[head.start():]
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + section
    path.write_text(text, encoding="utf-8")


def slice_issue(issue_path: Any, repo: Any, *, primary_repo: Any = None) -> Dict[str, Any]:
    """Resolve the ticket's spec and scope, then rewrite its 方案切片 section."""
    import ticket_context as tc

    root = tc._repo_root(Path(repo).resolve())
    primary = tc._repo_root(Path(primary_repo).resolve()) if primary_repo is not None else root
    issue, _ = tc._inside(primary, issue_path, "issue")
    frontmatter = agents_config.read_frontmatter(issue)
    spec = tc._find_spec(frontmatter, issue.read_text(encoding="utf-8"), primary, issue.parent.parent)
    spec_relative = spec.relative_to(primary).as_posix()
    scope = tc.declared_scope(frontmatter)
    if not scope:
        raise SpecTreeError("ticket declared-scope is empty; derive it from the tech-spec before slicing")
    version = spec_tree_version(spec)
    if version is None:
        raise SpecTreeError("spec %s has no spec-tree-version frontmatter; legacy specs are not sliceable" % spec_relative)
    spec_bytes = spec.read_bytes()
    entries = parse_tree(spec_bytes.decode("utf-8"))
    if entries is None:
        raise SpecTreeError("spec %s has no §3.1 ```text tree block" % spec_relative)
    lines = projection(entries, scope)
    write_slice(issue, spec_relative, _sha256_hex(spec_bytes), lines)
    return {"ok": True, "issue": issue.relative_to(primary).as_posix(), "spec": spec_relative,
            "tree_files": len(entries), "slice_lines": len(lines)}


def _sha256_hex(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    slice_cmd = sub.add_parser("slice")
    slice_cmd.add_argument("--issue", required=True)
    slice_cmd.add_argument("--repo", required=True)
    slice_cmd.add_argument("--primary-repo")
    args = parser.parse_args(argv)
    try:
        if args.command == "slice":
            print(json.dumps(slice_issue(args.issue, args.repo, primary_repo=args.primary_repo),
                             ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (SpecTreeError, agents_config.ConfigError, OSError, UnicodeError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
