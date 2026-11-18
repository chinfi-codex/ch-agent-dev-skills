#!/usr/bin/env python3
"""Architecture contract sweep for the ARCH-CHECK model.

Checks are read-only.  The architecture contract itself lives in
``docs/ARCH-CHECK.json``; this script covers the surrounding documentation
facts that the checker does not:

- MOD-1: GLOSSARY "feature-module 归档" rows are well formed and unique.
- SPEC-1: tech-spec ``supersedes`` targets exist and the chain has no cycle.
- GUIDE-1: ``docs/ARCHITECT.md`` is the deterministic rendering of the rules.

Usage:
    python3 contract_check.py --check --repo <项目根> [--json OUT]
    python3 contract_check.py --init  --repo <项目根>

Exit codes: 0 pass; 1 findings; 2 input/config error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

GLOSSARY_REL = Path("docs") / "GLOSSARY.md"
RULES_REL = Path("docs") / "ARCH-CHECK.json"
GUIDE_REL = Path("docs") / "ARCHITECT.md"
PLACEHOLDERS = {"…", "", "（待核对）", "待核对", "YYYY-MM-DD"}


def fail(results: List[Dict[str, str]], check_id: str, message: str) -> None:
    results.append({"id": check_id, "level": "FAIL", "message": message})


def info(results: List[Dict[str, str]], check_id: str, message: str) -> None:
    results.append({"id": check_id, "level": "INFO", "message": message})


def split_row(line: str) -> List[str]:
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [part.replace("\\|", "|").strip() for part in parts]


def is_retired(cells: List[str]) -> bool:
    return any(re.fullmatch(r"~~.*~~", cell) for cell in cells)


def parse_table_section(text: str, section_prefix: str) -> List[List[str]]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("## ") and section_prefix in line:
            start = index + 1
            break
    if start is None:
        return []
    raw: List[List[str]] = []
    in_table = False
    table_done = False
    for line in lines[start:]:
        if line.startswith("## ") or table_done:
            break
        if line.strip().startswith("|"):
            in_table = True
            cells = split_row(line)
            if all(set(cell) <= set("-: ") for cell in cells):
                continue
            raw.append(cells)
        elif in_table:
            table_done = True
    if not raw:
        return []
    return [row for row in raw[1:]
            if not all(cell in PLACEHOLDERS for cell in row) and not is_retired(row)]


def parse_frontmatter(text: str) -> Dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    data: Dict[str, Any] = {}
    current: Optional[str] = None
    for raw in text[3:end].splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if match:
            key, value = match.group(1), match.group(2)
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                data[key] = [item.strip() for item in inner.split(",") if item.strip()]
            elif value:
                data[key] = value
            else:
                current = key
                data[key] = []
            continue
        item = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if item and current:
            data[current].append(item.group(1))
    return data


def module_rows(repo: Path) -> List[List[str]]:
    path = repo / GLOSSARY_REL
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    return parse_table_section(text, "feature-module 归档")


def check_mod1(repo: Path, results: List[Dict[str, str]]) -> None:
    rows = module_rows(repo)
    if not rows:
        info(results, "MOD-1", "GLOSSARY 无 feature-module 归档表；模块登记可跳过")
        return
    seen = set()
    for row in rows:
        if len(row) < 2:
            fail(results, "MOD-1", "GLOSSARY module row has fewer than 2 columns: %r" % row)
            continue
        name = row[0]
        if not name or name in PLACEHOLDERS:
            fail(results, "MOD-1", "GLOSSARY module row has an empty module name")
            continue
        if name in seen:
            fail(results, "MOD-1", "duplicate GLOSSARY module: %s" % name)
        seen.add(name)
    info(results, "MOD-1", "GLOSSARY modules: %d" % len(seen))


def find_tech_specs(repo: Path) -> List[Path]:
    return sorted((repo / "dev" / "features").glob("*/*/*-tech-spec-*.md"))


def _supersedes(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def check_spec1(repo: Path, results: List[Dict[str, str]]) -> None:
    specs = find_tech_specs(repo)
    if not specs:
        info(results, "SPEC-1", "无 tech-spec；supersedes 链可跳过")
        return
    names = {spec.name for spec in specs}
    graph: Dict[str, List[str]] = {}
    for spec in specs:
        try:
            frontmatter = parse_frontmatter(spec.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            fail(results, "SPEC-1", "%s: cannot read frontmatter: %s" % (spec.name, exc))
            continue
        targets = _supersedes(frontmatter.get("supersedes"))
        graph[spec.name] = targets
        for target in targets:
            if target == spec.name:
                fail(results, "SPEC-1", "%s supersedes itself" % spec.name)
            elif target not in names:
                fail(results, "SPEC-1", "%s supersedes missing tech-spec: %s" % (spec.name, target))
    visiting, visited = set(), set()

    def visit(name: str) -> None:
        if name in visiting:
            fail(results, "SPEC-1", "supersedes cycle at %s" % name)
            return
        if name in visited:
            return
        visiting.add(name)
        for target in graph.get(name, []):
            if target in graph:
                visit(target)
        visiting.discard(name)
        visited.add(name)

    for name in sorted(graph):
        visit(name)
    info(results, "SPEC-1", "tech-spec supersedes chain: %d files" % len(graph))


def check_guide(repo: Path, results: List[Dict[str, str]]) -> None:
    rules_path = repo / RULES_REL
    if not rules_path.is_file():
        info(results, "GUIDE-1", "%s 不存在；ARCH-CHECK 未启用" % RULES_REL.as_posix())
        return
    try:
        import arch_check
    except ImportError as exc:
        fail(results, "GUIDE-1", "arch-check unavailable: %s" % exc)
        return
    try:
        rules = arch_check.load_rules_file(rules_path)
        errors = arch_check.validate_rules(rules)
        if errors:
            for error in errors:
                fail(results, "GUIDE-1", error)
            return
        for error in arch_check.render_guide_check(repo, "HEAD", rules, worktree=True):
            fail(results, "GUIDE-1", error)
    except (arch_check.InputError, OSError, ValueError, TypeError) as exc:
        fail(results, "GUIDE-1", "ARCH-CHECK validation failed: %s" % exc)


def run_check(repo: Path) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    if (not (repo / GLOSSARY_REL).is_file() and not (repo / RULES_REL).is_file()
            and not find_tech_specs(repo)):
        info(results, "INIT", "未发现 GLOSSARY、ARCH-CHECK 或 tech-spec；无架构契约的项目可跳过")
        return results
    check_mod1(repo, results)
    check_spec1(repo, results)
    check_guide(repo, results)
    return results


def run_init(repo: Path) -> int:
    try:
        import arch_check
    except ImportError as exc:
        print("ERROR: arch-check unavailable: %s" % exc, file=sys.stderr)
        return 2
    try:
        path = arch_check.init_repo(repo)
    except (arch_check.InputError, OSError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    print("created %s and %s; edit groups/rules, then run arch_check.py render write" % (
        path.relative_to(repo).as_posix(), GUIDE_REL.as_posix()))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ARCH-CHECK contract sweep")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--init", action="store_true")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print("ERROR: repository does not exist: %s" % repo, file=sys.stderr)
        return 2
    if args.init:
        return run_init(repo)
    results = run_check(repo)
    for item in results:
        print("[%s] %s %s" % (item["level"], item["id"], item["message"]))
    failed = sum(1 for item in results if item["level"] == "FAIL")
    informed = sum(1 for item in results if item["level"] == "INFO")
    print("汇总：FAIL %d 项，INFO %d 项" % (failed, informed))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"results": results, "failed": failed, "informed": informed},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
