#!/usr/bin/env python3
"""架构契约巡检：模块四方一致 / supersedes 链 / 契约义务派发 / 登记↔代码，一处计算、只报告不改文件。

原则（对齐 spec-kit analyze）：--check 只做可机器判定的一致性检查并报告，不回写任何文件；
唯一写入是 --init 初始化盘点生成 docs/ARCHITECTURE.md 基线草稿（已存在则拒绝覆盖）。
规则与写权限矩阵详见 shared/references/contract-check.md。

用法：
  python3 contract_check.py --check --repo <项目根> [--json OUT]
  python3 contract_check.py --init  --repo <项目根>

退出码：0 = 通过（或初始化成功）；1 = 有 FAIL 发现；2 = 输入 / 配置错误。
只依赖 Python 3.8+ 标准库。

多分支口径：接口表可选第 6 列「分支」。该列为空的条目在当前检出（工作树）核实；
填写分支名的条目用 `git show <分支>:<文件>` 在该分支上核实——多现行分支项目
（如 A 线 / B 线长期并行）的架构差异逐条标注分支即可机器核对，无需切换检出。
非 git 仓库中的分支条目只 INFO 提示，不 FAIL。
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

ARCH_REL = Path("docs") / "ARCHITECTURE.md"
GLOSSARY_REL = Path("docs") / "GLOSSARY.md"

INIT_SKELETON = """<!-- baseline: {today}（建档日。SPEC-2 只强制此日期及之后的 tech-spec；更早的历史 spec 只提示，补录或豁免由人工在复盘中处理） -->
# ARCHITECTURE

## 模块现行态

> 与 GLOSSARY「feature-module 归档」节保持一致：GLOSSARY 是模块归属的登记单源，本表是其代码视角镜像，两边对不上即漂移（`contract_check.py --check` 会报）。

| 模块（feature-module） | 职责一句话 | 代码位置 | 成员 feature-slug |
| --- | --- | --- | --- |
{module_rows}

## 接口现行态

> 现行 = 已合入主干的代码事实，不是某份 tech-spec 的意图。意图落现行态时签名以代码为准；「来源」列填促成最近一次变更的 tech-spec 文件名（供追溯意图与决策理由）。

| 接口（名 + 形态：函数 / HTTP / 事件 / 协议） | 现行签名 / 契约要点 | 代码位置（文件:符号） | 来源（tech-spec 文件名） | 生效日期 |
| --- | --- | --- | --- | --- |
| … | … | … | … | YYYY-MM-DD |

## 跨 feature 架构约束

| 约束 | 依据（PRD / tech-spec / 仓库等级） | 生效日期 |
| --- | --- | --- |
| … | … | YYYY-MM-DD |
"""

PLACEHOLDERS = {"…", "", "（待核对）", "待核对", "YYYY-MM-DD"}


def fail(results, check_id, message):
    results.append({"id": check_id, "level": "FAIL", "message": message})


def info(results, check_id, message):
    results.append({"id": check_id, "level": "INFO", "message": message})


def split_row(line):
    """按未转义的 `|` 切单元格（`\\|` 是单元格内的字面竖线，markdown 惯例）。"""
    parts = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [p.replace("\\|", "|").strip() for p in parts]


def is_retired(cells):
    """删除线包裹的单元格 = 已退役条目（如 ~~旧模块~~），不参与对账。"""
    return any(re.fullmatch(r"~~.*~~", c) for c in cells)


def parse_table_section(text, section_prefix):
    """取 `## <section_prefix>` 节内第一张表的数据行（去表头、去分隔行、去占位行、去退役行）。

    只取第一张表：一节内后续的说明性附表（如 GLOSSARY 归档节的 slug 迁移记录表）不混入口径。
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and section_prefix in line:
            start = i + 1
            break
    if start is None:
        return []
    raw = []
    in_table = False
    table_done = False
    for line in lines[start:]:
        if line.startswith("## ") or table_done:
            break
        if line.strip().startswith("|"):
            in_table = True
            cells = split_row(line)
            if all(set(c) <= set("-: ") for c in cells):
                continue
            raw.append(cells)
        elif in_table:
            table_done = True  # 第一张表结束，后续内容（含第二张表）不再收集
    if not raw:
        return []
    return [r for r in raw[1:]
            if not all(c in PLACEHOLDERS for c in r) and not is_retired(r)]


def parse_frontmatter(text):
    """极简 frontmatter 解析：顶层 `key: value`，值剥离行内注释。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    data = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if m:
            data[m.group(1)] = m.group(2).split("#", 1)[0].strip()
    return data


def section_body(text, heading_prefix):
    """取 `### <heading_prefix>` 节正文（剥 HTML 注释），到下一个同级或上级标题为止。"""
    grab, buf = False, []
    for line in text.splitlines():
        if line.startswith("### ") and heading_prefix in line:
            grab = True
            continue
        if grab and (line.startswith("### ") or line.startswith("## ")):
            break
        if grab:
            buf.append(line)
    body = re.sub(r"<!--.*?-->", "", "\n".join(buf), flags=re.S)
    return body.strip()


def module_set_from_glossary(repo):
    path = repo / GLOSSARY_REL
    if not path.exists():
        return None
    rows = parse_table_section(path.read_text(encoding="utf-8"), "feature-module 归档")
    return {r[0] for r in rows if r and r[0] not in PLACEHOLDERS}


def level1_dirs(path):
    if not path.exists():
        return None
    return {p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")}


def parse_arch_modules(arch_text):
    return parse_table_section(arch_text, "模块现行态")


def parse_arch_interfaces(arch_text):
    return parse_table_section(arch_text, "接口现行态")


def find_tech_specs(repo):
    root = repo / "dev" / "features"
    if not root.exists():
        return []
    return sorted(root.glob("*/*/*-tech-spec-*.md"))


def find_issues(repo, module, slug):
    d = repo / "dev" / "features" / module / slug / "issues"
    if not d.exists():
        return []
    return sorted(d.glob("*.md"))


def check_mod1(repo, results, glossary_modules, arch_rows):
    docs_dirs = level1_dirs(repo / "docs" / "features")
    dev_dirs = level1_dirs(repo / "dev" / "features")
    if arch_rows is not None:
        arch_modules = {r[0] for r in arch_rows if r and r[0] not in PLACEHOLDERS}
        if glossary_modules is not None and arch_modules != glossary_modules:
            only_g = glossary_modules - arch_modules
            only_a = arch_modules - glossary_modules
            fail(results, "MOD-1",
                 f"ARCHITECTURE 模块表与 GLOSSARY 归档不一致：仅 GLOSSARY 有 {sorted(only_g)}，仅 ARCHITECTURE 有 {sorted(only_a)}")
        elif glossary_modules is None and arch_modules:
            info(results, "MOD-1", "GLOSSARY 不存在或无归档节，模块表跳过与 GLOSSARY 的对账")
    for label, dirs in (("docs/features/", docs_dirs), ("dev/features/", dev_dirs)):
        if dirs is None or glossary_modules is None:
            continue
        if dirs != glossary_modules:
            info(results, "MOD-1",
                 f"{label} 一层目录与 GLOSSARY 归档存在瞬时差：目录侧 {sorted(dirs - glossary_modules)}，登记侧 {sorted(glossary_modules - dirs)}（在途需求允许，不 FAIL）")


def check_spec1(repo, results, specs):
    by_name = {}
    for spec in specs:
        by_name.setdefault(spec.name, spec)
    for spec in specs:
        fm = parse_frontmatter(spec.read_text(encoding="utf-8"))
        target = fm.get("supersedes", "")
        if not target:
            continue
        hit = by_name.get(target)
        if hit is None:
            hit = spec.parent / target
        if not hit.exists():
            fail(results, "SPEC-1", f"{spec.name} 的 supersedes 指向不存在的文件：{target}")
    # 环检测（沿 supersedes 链走，每链步数不超过 specs 总数）
    sup_map = {}
    for spec in specs:
        t = parse_frontmatter(spec.read_text(encoding="utf-8")).get("supersedes", "")
        if t:
            sup_map[spec.name] = t
    for name in sup_map:
        seen, cur = set(), name
        while cur in sup_map:
            if cur in seen:
                fail(results, "SPEC-1", f"supersedes 链存在环：{name} 所在链回到 {cur}")
                break
            seen.add(cur)
            cur = sup_map[cur]


def arch_baseline(arch_text):
    """从基线注释取建档日（`<!-- baseline: YYYY-MM-DD ... -->`）；无则 None（全量严格）。"""
    m = re.search(r"<!--\s*baseline:\s*(\d{4}-\d{2}-\d{2})", arch_text)
    return m.group(1) if m else None


def spec_date(spec):
    """从文件名取 spec 日期（`*-tech-spec-YYYY-MM-DD[-vX.Y].md`）；取不到返回 None。"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", spec.name)
    return m.group(1) if m else None


def check_spec2(repo, results, specs, arch_text, head="HEAD"):
    baseline = arch_baseline(arch_text) if arch_text is not None else None
    import architecture_graph
    for spec in specs:
        fm = parse_frontmatter(spec.read_text(encoding="utf-8"))
        if fm.get("status") != "已确认（待拆解）":
            continue
        body = section_body(spec.read_text(encoding="utf-8"), "3.2")
        if not body:
            continue
        rel = spec.relative_to(repo)
        date = spec_date(spec)
        if baseline and date and date < baseline:
            info(results, "SPEC-2",
                 f"{rel} 早于建档日（{date} < {baseline}），3.2 接口变更待补录或显式豁免")
            continue
        # v2 never treats an arbitrary ARCHITECTURE word or a source citation as
        # dispatch evidence.  Every structured stable ID must reconcile through
        # ticket frontmatter, its shard, action/supersedes and bound evidence.
        errors = architecture_graph.check_flow_forward(repo, spec, head=head)
        for error in errors:
            fail(results, "SPEC-2", f"{rel}: {error}")


def iter_locations(cell):
    for part in re.split(r"[、;,；]|<br\s*/?>", cell):
        part = part.strip().strip("`")
        if part and part not in PLACEHOLDERS:
            yield part


def git_show(repo, branch, path):
    """读 <branch>:<path> 内容。返回 (content, None)；不存在/失败返回 (None, 原因)。"""
    try:
        r = subprocess.run(["git", "show", f"{branch}:{path}"],
                           cwd=repo, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git 不可用（{exc}）"
    if r.returncode != 0:
        return None, "分支上不存在"
    return r.stdout, None


def check_code(repo, results, arch_rows, arch_interfaces):
    if arch_rows is not None:
        for row in arch_rows:
            if len(row) < 3:
                continue
            for loc in iter_locations(row[2]):
                target = repo / loc.split(":", 1)[0]
                if not target.exists():
                    fail(results, "CODE-2", f"模块「{row[0]}」登记的代码位置不存在：{loc}")
    git_ok = None
    for row in arch_interfaces:
        if len(row) < 3:
            continue
        branch = row[5].strip(" `") if len(row) > 5 and row[5] not in PLACEHOLDERS else ""
        for loc in iter_locations(row[2]):
            path_part, _, symbol = loc.rpartition(":")
            if branch:
                if git_ok is None:
                    git_ok = (repo / ".git").exists()
                    if not git_ok:
                        info(results, "CODE-1", "非 git 仓库，分支条目的跨分支核实跳过（不 FAIL）")
                if not git_ok:
                    break
                content, err = git_show(repo, branch, path_part)
                if err is not None:
                    fail(results, "CODE-1",
                         f"接口「{row[0]}」登记的文件在分支 {branch} 上不存在：{path_part}")
                    continue
                if symbol and symbol.strip() not in content:
                    fail(results, "CODE-1",
                         f"接口「{row[0]}」登记的符号在 {branch}:{path_part} 中检索不到：{symbol.strip()}（该分支漂移或已改名）")
                continue
            target = repo / path_part
            if not target.is_file():
                fail(results, "CODE-1", f"接口「{row[0]}」登记的文件不存在：{path_part}")
                continue
            if symbol:
                try:
                    content = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                if symbol.strip() not in content:
                    fail(results, "CODE-1",
                         f"接口「{row[0]}」登记的符号在 {path_part} 中检索不到：{symbol.strip()}（漂移或已改名）")


def check_architecture_graph(repo, results, head="HEAD"):
    """Validate v2 shards, module parity and the generated homepage."""
    graph_dir = repo / "docs" / "architecture"
    if not graph_dir.exists():
        return {"records": [], "files": []}
    import architecture_graph
    details = architecture_graph.load_graph_details(repo)
    graph_records = details["records"]
    graph_files = details["files"]
    _, errors = architecture_graph.check(repo, head=head)
    for error in errors:
        fail(results, "GRAPH-1", error)
    arch_path = repo / ARCH_REL
    arch_text = arch_path.read_text(encoding="utf-8") if arch_path.is_file() else ""
    old_modules = parse_arch_modules(arch_text) if arch_text else []
    old_interfaces = parse_arch_interfaces(arch_text) if arch_text else []
    legacy_facts = bool(old_modules or old_interfaces)
    if graph_files and not graph_records and legacy_facts:
        fail(results, "GRAPH-2",
             "空图谱或空 records 不能掩盖 ARCHITECTURE.md 旧事实；先一次性迁移，再生成首页")
    elif not graph_files and legacy_facts and graph_dir.is_dir():
        fail(results, "GRAPH-2",
             "空 docs/architecture 目录不能掩盖旧表；请运行 migrate-legacy 或移除空目录")
    module_records = [r for r in graph_records if r.get("kind") == "module"]
    glossary_modules = module_set_from_glossary(repo)
    # With legacy facts and no valid records, the legacy MOD-1/CODE checks below
    # remain active instead of an empty graph silently replacing them.
    if (graph_records or not legacy_facts) and graph_files and glossary_modules is not None:
        graph_modules = {r.get("module") for r in module_records if r.get("module")}
        if graph_modules != glossary_modules:
            fail(results, "MOD-1", "图谱模块分片与 GLOSSARY 归档不一致："
                 f"仅 GLOSSARY 有 {sorted(glossary_modules - graph_modules)}，"
                 f"仅图谱有 {sorted(graph_modules - glossary_modules)}")
    if graph_files and arch_text:
        headings = ("模块现行态", "接口现行态", "跨 feature 架构约束")
        present = [heading for heading in headings
                   if re.search(r"^##\s+" + re.escape(heading) + r"\s*$", arch_text, re.M)]
        if present:
            fail(results, "GRAPH-2",
                 "图谱分片已存在，ARCHITECTURE.md 仍保留旧架构表标题："
                 + "、".join(present) + "；首页只能生成，架构修改必须写入 docs/architecture/*.json")
    if graph_files:
        _, homepage_errors = architecture_graph.render_homepage(repo, write=False, head=head)
        for error in homepage_errors:
            fail(results, "GRAPH-3", error)
    return details


def run_check(repo, issue_path=None, base=None, head="HEAD"):
    results = []
    arch_path = repo / ARCH_REL
    arch_text = arch_path.read_text(encoding="utf-8") if arch_path.exists() else None
    if arch_text is None:
        info(results, "INIT", "docs/ARCHITECTURE.md 未创建；无契约需求的项目可跳过，有接口变更的需求应先 --init 建档")
    glossary_modules = module_set_from_glossary(repo)
    parsed_modules = parse_arch_modules(arch_text) if arch_text is not None else None
    parsed_interfaces = parse_arch_interfaces(arch_text) if arch_text is not None else []
    specs = find_tech_specs(repo)

    graph_details = check_architecture_graph(repo, results, head=head)
    graph_files = bool(graph_details["files"])
    legacy_facts = bool(parsed_modules or parsed_interfaces)
    legacy_fallback = legacy_facts and not graph_details["records"]
    legacy_checks = not graph_files or legacy_fallback
    arch_rows = parsed_modules if legacy_checks else None
    arch_interfaces = parsed_interfaces if legacy_checks else []

    check_mod1(repo, results, glossary_modules, arch_rows)
    check_spec1(repo, results, specs)
    check_spec2(repo, results, specs, arch_text, head=head)
    if arch_text is not None and legacy_checks:
        check_code(repo, results, arch_rows, arch_interfaces)
    if issue_path is not None and base is not None:
        import architecture_graph
        for error in architecture_graph.check_flow_back(repo, issue_path, base, head):
            fail(results, "FLOW-1", f"{issue_path}: {error}")
    return results


def run_init(repo):
    arch_path = repo / ARCH_REL
    if arch_path.exists():
        print(f"错误：{arch_path} 已存在，--init 拒绝覆盖（living 文件原地更新，不重建）", file=sys.stderr)
        return 2
    modules = module_set_from_glossary(repo)
    if modules is None:
        print("提示：docs/GLOSSARY.md 不存在或无「feature-module 归档」节，模块表留空待补", file=sys.stderr)
        rows = ["| … | … | … | … |"]
    else:
        glossary_text = (repo / GLOSSARY_REL).read_text(encoding="utf-8")
        reg_rows = parse_table_section(glossary_text, "feature-module 归档")
        rows = []
        for r in reg_rows:
            if not r or r[0] in PLACEHOLDERS:
                continue
            desc = r[1] if len(r) > 1 else ""
            members = r[2] if len(r) > 2 else ""
            rows.append(f"| {r[0]} | {desc} | （待核对） | {members} |")
        if not rows:
            rows = ["| … | … | … | … |"]
    arch_path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    arch_path.write_text(
        INIT_SKELETON.format(module_rows="\n".join(rows), today=today),
        encoding="utf-8")
    print(f"已生成基线草稿：{arch_path}")
    print("下一步：逐行核对「代码位置」、按代码现状补全接口表与约束表（逐条标注 文件:符号 证据），人工确认后生效")
    return 0


def main():
    parser = argparse.ArgumentParser(description="架构契约巡检（只报告不改文件；--init 生成基线草稿）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="运行一致性检查")
    mode.add_argument("--init", action="store_true", help="生成 docs/ARCHITECTURE.md 基线草稿（已存在则拒绝）")
    parser.add_argument("--repo", default=".", help="项目根目录（默认当前目录）")
    parser.add_argument("--issue", help="flow-back：票文件（须同时给 --base）")
    parser.add_argument("--base", help="flow-back：冻结基准 commit（须同时给 --issue）")
    parser.add_argument("--head", default="HEAD", help="候选 HEAD（默认 HEAD）")
    parser.add_argument("--json", dest="json_out", help="检查结果写 JSON 到指定文件")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"错误：--repo 不是目录：{repo}", file=sys.stderr)
        return 2

    if args.init:
        return run_init(repo)
    if bool(args.issue) != bool(args.base):
        print("错误：flow-back 必须同时指定 --issue 与 --base", file=sys.stderr)
        return 2
    issue_path = (Path(args.issue) if Path(args.issue).is_absolute() else repo / args.issue).resolve() if args.issue else None
    results = run_check(repo, issue_path=issue_path, base=args.base, head=args.head)
    fails = [r for r in results if r["level"] == "FAIL"]
    for r in results:
        print(f"[{r['level']}] {r['id']} {r['message']}")
    print(f"汇总：FAIL {len(fails)} 项，INFO {len(results) - len(fails)} 项")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"results": results, "fail_count": len(fails)},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
