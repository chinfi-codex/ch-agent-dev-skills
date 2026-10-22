#!/usr/bin/env python3
"""确定性流程核对：红线 / 漂移 / 档位 / 证据完整性 / 需求符合性，一处计算、多方读取。

dev 阶段的流程核对只在这里做一次：/implement 在发起评审前（--phase review）与
转 ready 前（--phase merge）运行，主会话收口时运行 --phase close（= merge 全部项 +
需求覆盖矩阵 + 需求符合性核对记录）；/ai-review 只读本脚本
的输出，不再自行复核 glob、档位与 evidence。本脚本只做可机器判定的事——语义判断
（需求驱动测试更新的映射是否成立、escalate-triggers 的语义命中、清理复核、
需求点与实现是否语义相符）留给评审与主会话的符合性核对。

用法：
  python3 process_check.py --issue <票文件> --repo <票 worktree 或仓库> \
      --phase review|merge|close [--base REV] [--head REV] [--json OUT]

退出码：0 = 全部通过；1 = 有 FAIL 项；2 = 输入 / 配置错误。
只依赖 Python 3.8+ 标准库；只读仓库与 dev/ 产物，唯一写入是 --json 指定的文件。
"""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
import json
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

TIERS = {"light": 0, "scoped": 1, "full": 2}
# 非生产文件（不计入生产文件数 / LOC）；agents-config verify-policy.production-exclude 可覆盖
DEFAULT_NON_PRODUCTION = [
    "tests/**", "**/tests/**", "test/**", "**/test/**", "**/test_*", "**/*_test.*",
    "**/*.test.*", "**/*.spec.*", "docs/**", "dev/**", "**/*.md",
]
# 测试文件（见红告警用；比 DEFAULT_NON_PRODUCTION 窄，只含测试）
TEST_PATTERNS = [
    "tests/**", "**/tests/**", "test/**", "**/test/**", "**/test_*", "**/*_test.*",
    "**/*.test.*", "**/*.spec.*",
]


class InputError(Exception):
    pass


# ---------- 解析 ----------

def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(val: str):
    val = val.strip()
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    if val in ("true", "false"):
        return val == "true"
    if val in ("", "null", "~"):
        return None
    return val


def parse_yaml_subset(text: str) -> dict:
    """足够读 agents-config / frontmatter / evidence 头的缩进 YAML 子集：
    标量、内联列表、块列表（- item）、按缩进嵌套的映射；不支持多行字符串。"""
    root: dict = {}
    stack = [(-1, root)]  # (indent, container)
    pending_key = None  # (indent, parent_dict, key)
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        while len(stack) > 1 and indent <= stack[-1][0] and not (
            body.startswith("- ") and isinstance(stack[-1][1], list) and indent >= stack[-1][0]
        ):
            stack.pop()
        if body.startswith("- ") or body == "-":
            item = _scalar(body[1:])
            if pending_key and indent > pending_key[0]:
                _, parent, key = pending_key
                lst: list = []
                parent[key] = lst
                stack.append((indent, lst))
                pending_key = None
            container = stack[-1][1]
            if isinstance(container, list):
                container.append(item)
            continue
        m = re.match(r"([^:]+):(.*)$", body)
        if not m:
            continue
        key, val = m.group(1).strip().strip("\"'"), m.group(2)
        if pending_key and indent > pending_key[0]:
            _, parent, pkey = pending_key
            child: dict = {}
            parent[pkey] = child
            stack.append((pending_key[0], child))
            pending_key = None
        container = stack[-1][1]
        if not isinstance(container, dict):
            continue
        if val.strip() == "":
            container[key] = None
            pending_key = (indent, container, key)
        else:
            container[key] = _scalar(val)
            pending_key = None
    return root


def read_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    if not m:
        raise InputError(f"{path} 缺 frontmatter")
    return parse_yaml_subset(m.group(1))


def read_first_yaml_block(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"```ya?ml\s*\n(.*?)\n```", text, re.S)
    if not m:
        raise InputError(f"{path} 缺 yaml 代码块")
    return parse_yaml_subset(m.group(1))


def md_section(text: str, title: str) -> str:
    """取 markdown 中标题含 title 的二级节正文（到下一个同级或更高标题为止）。"""
    m = re.search(rf"^##\s+[^\n]*{re.escape(title)}[^\n]*\n(.*?)(?=^#{{1,2}}\s|\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


# ---------- glob ----------

def glob_to_regex(pattern: str) -> re.Pattern:
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def match_any(path: str, patterns) -> str | None:
    for p in patterns or []:
        p = str(p).strip()
        if not p:
            continue
        if glob_to_regex(p).match(path) or (p.endswith("/") and path.startswith(p)) or fnmatch.fnmatch(path, p):
            return p
    return None


# ---------- git ----------

def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise InputError(f"git {' '.join(args)} 失败：{r.stderr.strip()}")
    return r.stdout


def changed_files(repo: Path, base: str, head: str):
    """返回 [(status, path, added, deleted)]；重命名按新路径计。"""
    status = {}
    for line in git(repo, "diff", "--name-status", "-M", f"{base}..{head}").splitlines():
        parts = line.split("\t")
        status[parts[-1]] = parts[0][0]
    out = []
    for line in git(repo, "diff", "--numstat", "-M", f"{base}..{head}").splitlines():
        a, d, path = line.split("\t", 2)
        if " => " in path:  # 重命名的 numstat 形式 a/{x => y}/b
            path = re.sub(r"\{[^{}]*? => ([^{}]*?)\}", r"\1", path).replace("//", "/")
            path = path.split(" => ")[-1]
        out.append((status.get(path, "M"), path, 0 if a == "-" else int(a), 0 if d == "-" else int(d)))
    return out


# ---------- 核对 ----------

class Report:
    def __init__(self):
        self.items = []  # dict(check, level, msg)

    def add(self, check, level, msg):
        self.items.append({"check": check, "level": level, "msg": msg})

    @property
    def failed(self):
        return any(i["level"] == "FAIL" for i in self.items)


def find_root(issue_path: Path) -> Path:
    for p in issue_path.resolve().parents:
        if (p / "dev" / "agents-config.md").is_file():
            return p
    raise InputError("向上找不到 dev/agents-config.md（先运行 /setup-dev）")


def tier_of(v) -> int | None:
    return TIERS.get(str(v).strip()) if v is not None else None


def successors(issue_path: Path, issue_id: str):
    out = []
    for p in sorted(issue_path.parent.glob("issue-*.md")):
        if p.resolve() == issue_path.resolve():
            continue
        try:
            fm = read_frontmatter(p)
        except InputError:
            continue
        deps = fm.get("blocked-by") or []
        if isinstance(deps, str):
            deps = [deps]
        if issue_id in [str(d) for d in deps]:
            out.append(str(fm.get("issue-id") or p.stem))
    return out


def remote_receipt_errors(evidence, ev, head, tree):
    """Validate copied remote evidence without contacting or trusting a live branch tip."""
    errors = []
    paths = ev.get("remote-runs")
    if ev.get("test-location") != "remote" or not isinstance(paths, list) or not paths:
        return ["缺远端测试位置或 remote-runs 回执列表"]
    manifests = ev.get("remote-command-manifests")
    intended = []
    if not isinstance(manifests, list) or not manifests:
        return ["缺提交测试前冻结的 remote-command-manifests 命令清单"]
    try:
        for relative in manifests:
            manifest = (evidence.parent / str(relative)).resolve()
            manifest.relative_to(evidence.parent.resolve())
            commands = json.loads(manifest.read_text())
            if not isinstance(commands, list) or not commands:
                raise ValueError("空命令清单")
            intended.extend((row["name"], tuple(row["argv"])) for row in commands)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"命令清单无效: {exc}"]
    observed = []
    for relative in paths:
        try:
            path = (evidence.parent / str(relative)).resolve()
            path.relative_to(evidence.parent.resolve())
            data = json.loads(path.read_text())
            if data.get("schema") != "agent-dev.remote-test/v1" or data.get("phase") != "completed":
                raise ValueError("远端测试未完成")
            if data.get("commit") != head or data.get("tree") != tree or data.get("final_commit") != head or data.get("dirty") is not False:
                raise ValueError("远端测试提交/tree不一致或输入被修改")
            context = data.get("context") or {}
            if not all(context.get(k) for k in ("instance", "hostname", "fingerprints", "runtime_id", "python")):
                raise ValueError("缺远端实例/环境身份")
            if not data.get("python_version") or not data.get("packages_sha256"):
                raise ValueError("缺运行时指纹")
            rows = data.get("results")
            expected = data.get("commands")
            if not rows or not expected or [r["name"] for r in rows] != [r["name"] for r in expected]:
                raise ValueError("远端命令未完整执行")
            observed.extend((row["name"], tuple(row["argv"])) for row in expected)
            jobs = data.get("jobs")
            if type(jobs) is not int or jobs < 1:
                raise ValueError("缺实际槽位数")
            for row, submitted in zip(rows, expected):
                actual_argv = [arg.replace("{python}", context["python"]).replace("{jobs}", str(jobs)) for arg in submitted["argv"]]
                if row.get("argv") != actual_argv:
                    raise ValueError("实际执行命令与提交清单不一致")
                if type(row.get("returncode")) is not int or not row.get("argv"):
                    raise ValueError("缺实际命令/退出码")
                if row["returncode"] != 0:
                    raise ValueError("远端命令失败；不能以 all-passed 声明覆盖非零退出码")
                log = path.parent / row["log"]
                if Path(row["log"]).name != row["log"]:
                    raise ValueError("非法日志路径")
                if hashlib.sha256(log.read_bytes()).hexdigest() != row.get("log_sha256"):
                    raise ValueError("日志缺失或摘要不符")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{relative}: {exc}")
    if Counter(intended) != Counter(observed):
        errors.append("验收命令清单与远端实际提交集合不一致")
    return errors


def run(args) -> tuple[Report, dict]:
    issue_path = Path(args.issue)
    if not issue_path.is_file():
        raise InputError(f"票文件不存在：{issue_path}")
    root = find_root(issue_path)
    config = read_first_yaml_block(root / "dev" / "agents-config.md")
    fm = read_frontmatter(issue_path)
    issue_id = str(fm.get("issue-id") or "")
    if not issue_id:
        raise InputError("票 frontmatter 缺 issue-id")
    feature_dir = issue_path.parent.parent
    impl_dir = feature_dir / "impl" / issue_id
    impl_log = Path(args.impl_log) if args.impl_log else impl_dir / f"impl-log-{issue_id}.md"
    evidence = Path(args.evidence) if args.evidence else impl_dir / f"evidence-{issue_id}.md"
    repo = Path(args.repo)
    main_branch = str(config.get("main-branch") or "main")
    head = git(repo, "rev-parse", args.head).strip()
    base = args.base or git(repo, "merge-base", main_branch, head).strip()

    rep = Report()
    files = changed_files(repo, base, head)
    if not files:
        rep.add("diff", "FAIL", f"{base[:8]}..{head[:8]} diff 为空")

    log_text = impl_log.read_text(encoding="utf-8") if impl_log.is_file() else ""
    if not log_text:
        rep.add("impl-log", "FAIL", f"impl-log 不存在：{impl_log}")
    redline_sec = md_section(log_text, "红线接触")
    drift_sec = md_section(log_text, "漂移与升档")
    done_sec = md_section(log_text, "做了什么")
    matrix_sec = md_section(log_text, "需求覆盖矩阵")

    # 0. 需求覆盖矩阵：主会话符合性核对的索引，缺了等于堵死合并前最后一道门
    if log_text and not matrix_sec.strip():
        rep.add("matrix", "FAIL", "impl-log「需求覆盖矩阵」为空或缺失（每行为点一行；无对应实现也须如实登记）")

    # 1. 红线：命中即须在 impl-log 登记；新增测试可登记在「做了什么」或「红线接触」
    policy = config.get("verify-policy") or {}
    refresh = [str(x).split("::")[0].split()[0] for x in (config.get("mechanical-refresh") or []) if str(x).strip()]
    redline_hits = []
    for st, path, _, _ in files:
        g = match_any(path, config.get("redlines"))
        if not g:
            continue
        kind = "mechanical-refresh" if match_any(path, refresh) else ("added" if st == "A" else "modified")
        where = redline_sec + (done_sec if kind == "added" else "")
        registered = path in where or Path(path).name in where
        redline_hits.append({"path": path, "glob": g, "status": st, "kind": kind, "registered": registered})
        if not registered:
            rep.add("redline", "FAIL", f"{path}（{st}，命中 {g}）未在 impl-log 登记")
    semantic = [h["path"] for h in redline_hits if h["registered"] and h["kind"] != "added"]
    if semantic:
        rep.add("redline", "REVIEW", "已登记的既有文件改动，需评审复核映射 / 字段范围：" + ", ".join(semantic))

    # 1b. 见红证据：TDD 新增测试须在「做了什么」登记见红记录（先 REVIEW 告警，观察后转 FAIL）
    added_tests = [p for st, p, _, _ in files if st == "A" and match_any(p, TEST_PATTERNS)]
    if added_tests and "见红" not in done_sec:
        rep.add("tdd-red", "REVIEW",
                "新增测试未见红登记（impl-log「做了什么」须记首次失败输出要点）：" + ", ".join(added_tests))

    # 2. 漂移：declared-scope 外的文件须在「漂移与升档」节登记
    non_prod = policy.get("production-exclude") or DEFAULT_NON_PRODUCTION
    prod = [(st, p, a, d) for st, p, a, d in files if not match_any(p, non_prod)]
    declared = fm.get("declared-scope") or []
    if isinstance(declared, str):
        declared = [declared]
    drift = [p for _, p, _, _ in prod if not match_any(p, declared)]
    unregistered_drift = [p for p in drift if p not in drift_sec and Path(p).name not in drift_sec]
    if unregistered_drift:
        rep.add("drift", "FAIL", "declared-scope 外的生产文件未登记漂移：" + ", ".join(unregistered_drift))
    if not drift_sec.strip():
        rep.add("drift", "FAIL", "impl-log「漂移与升档记录」为空（无漂移也须留一行）")

    # 3. 档位：阈值下限 + 只升不降 + 集成票
    ev = read_first_yaml_block(evidence) if evidence.is_file() else {}
    if not ev:
        rep.add("evidence", "FAIL", f"evidence 不存在或缺 yaml 头：{evidence}")
    n_prod = len(prod)
    loc = sum(a + d for _, _, a, d in prod)
    ticket_tier = tier_of(fm.get("verify-tier"))
    target = tier_of(ev.get("tier-target")) if ev else None
    if target is None:
        target = ticket_tier
        if ev:
            rep.add("tier", "FAIL", "evidence 头缺 tier-target（升档后的目标档位，未升档时等于票 verify-tier）")
    required = 0
    reasons = []
    if n_prod > int(policy.get("light-max-files") or 3):
        required, reasons = 1, [f"生产文件 {n_prod} > light-max-files"]
    if n_prod > int(policy.get("scoped-max-files") or 10):
        required = 2
        reasons.append(f"生产文件 {n_prod} > scoped-max-files")
    if loc > int(policy.get("scoped-max-loc") or 400):
        required = 2
        reasons.append(f"生产 LOC {loc} > scoped-max-loc")
    succ = successors(issue_path, issue_id)
    isolation = str(ev.get("isolation") or "").strip() if ev else ""
    if not succ:
        if isolation == "module-scoped":
            rep.add("tier", "REVIEW", "集成票按「模块隔离认定」跑 module-scoped：判定依据见 evidence 档位门记录")
        else:
            required = 2
            reasons.append("集成票（无后继票）")
    if ticket_tier is None:
        rep.add("tier", "FAIL", f"票 verify-tier 非法：{fm.get('verify-tier')!r}")
    elif target is not None and target < ticket_tier:
        rep.add("tier", "FAIL", "tier-target 低于票 verify-tier（档位只升不降）")
    if target is not None and target < required:
        rep.add("tier", "FAIL", "该升没升：" + "；".join(reasons) + f" → 至少 {_name(required)}，当前 {_name(target)}")
    if target is not None and ticket_tier is not None and target > ticket_tier:
        rows = [ln for ln in drift_sec.splitlines()
                if ln.strip().startswith("|") and not re.fullmatch(r"[|\s:-]+", ln.strip()) and "检查时点" not in ln]
        if not any(_name(target) in ln for ln in rows):
            rep.add("tier", "FAIL", f"tier-target 升至 {_name(target)}，但 impl-log「漂移与升档记录」无对应升档行")

    # 4. 证据：按阶段核对执行档位、commit 与 all-passed
    ev_text = evidence.read_text(encoding="utf-8") if evidence.is_file() else ""
    if str(fm.get("type") or "").strip() == "bug" and ev_text and not md_section(ev_text, "回归有效性").strip():
        rep.add("evidence", "FAIL", "type: bug 票缺 evidence「回归有效性」节（revert 修复 → 见红 → 恢复 → 绿）")
    if ev and target is not None:
        executed = tier_of(ev.get("tier-executed"))
        ev_commit = str(ev.get("commit") or "")
        need = target if args.phase in ("merge", "close") else min(target, TIERS["scoped"])
        if executed is None:
            rep.add("evidence", "FAIL", f"tier-executed 非法：{ev.get('tier-executed')!r}")
        elif executed < need:
            rep.add("evidence", "FAIL", f"tier-executed {_name(executed)} < 本阶段要求 {_name(need)}")
        full_ref = str(ev.get("full-ref") or "").strip()
        if executed == TIERS["full"] and not ev.get("full-commit") and not full_ref:
            rep.add("evidence", "FAIL", "tier-executed: full 但缺 full-commit / full-ref（档位虚标）")
        if full_ref:
            ref_path = root / full_ref
            m = re.search(r"^\s*[-*]?\s*tree:\s*`?([0-9a-f]{7,40})", ref_path.read_text(encoding="utf-8"), re.M) \
                if ref_path.is_file() else None
            head_tree = git(repo, "rev-parse", f"{head}^{{tree}}").strip()
            if not m:
                rep.add("evidence", "FAIL", f"full-ref 记录不存在或缺 tree 行：{full_ref}")
            elif not head_tree.startswith(m.group(1)):
                rep.add("evidence", "FAIL", f"full-ref 记录 tree {m.group(1)[:8]} ≠ HEAD tree {head_tree[:8]}，不能复用")
            elif not re.search(r"^\s*[-*]?\s*result:\s*`?pass", ref_path.read_text(encoding="utf-8"), re.M):
                rep.add("evidence", "FAIL", f"full-ref 记录结果不是 pass：{full_ref}")
        if ev.get("all-passed") is not True:
            rep.add("evidence", "FAIL", "all-passed 不是 true")
        if not _same_ticket_tree(repo, ev_commit, head, [p for _, p, _, _ in files]):
            rep.add("evidence", "FAIL", f"evidence commit {ev_commit[:8] or '<空>'} 与 HEAD {head[:8]} 的本票文件不一致（证据过期）")
        if args.phase in ("merge", "close") and target == TIERS["full"] and ev.get("full-commit") and not full_ref:
            if not _same_ticket_tree(repo, str(ev["full-commit"]), head, [p for _, p, _, _ in files]):
                rep.add("evidence", "FAIL", "full-commit 与 HEAD 的本票文件不一致，须在最终 commit 重跑 full")

    # 已有多实例拓扑的判读项目不得以本机测试或口头声明替代远端回执。
    topology_file = repo / "configuration/deploy/topology.json"
    remote_required = False
    if topology_file.is_file():
        try:
            declaration = json.loads(topology_file.read_text())
            remote_required = any(h.get("location") == "server" for h in declaration.get("hosts", {}).values())
        except (OSError, ValueError, TypeError, AttributeError):
            rep.add("remote-evidence", "FAIL", "部署拓扑无法解析，不能静默回退本机测试")
    if remote_required or ev.get("test-location") == "remote":
        tested = str(ev.get("commit") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", tested):
            errors = ["缺有效 evidence.commit"]
        else:
            # Existing _same_ticket_tree gate above proves permitted reuse against HEAD.
            errors = remote_receipt_errors(evidence, ev, tested, git(repo, "rev-parse", f"{tested}^{{tree}}").strip())
        for error in errors:
            rep.add("remote-evidence", "FAIL", error)
        if not errors:
            rep.add("remote-evidence", "PASS", "远端提交/环境/命令与日志完整性一致；验收覆盖与先在失败归因仍须核对")

    # 5. 需求符合性核对（仅 close 相）：主会话合并前核对结论落盘，此处只把机器可判的关
    if args.phase == "close":
        conf = config.get("conformance") or {}
        mode = str(conf.get("mode") or "always")
        cfile = impl_dir / f"conformance-{issue_id}.md"
        cv = read_first_yaml_block(cfile) if cfile.is_file() else {}
        if not cv:
            rep.add("conformance", "FAIL", f"需求符合性核对记录不存在或缺 yaml 头：{cfile}")
        else:
            verdict = str(cv.get("verdict") or "").strip()
            if verdict == "skipped":
                if not (mode == "sampled" and target == TIERS["light"]):
                    rep.add("conformance", "FAIL",
                            f"verdict: skipped 仅在 conformance.mode=sampled 且实测 light 档时允许"
                            f"（当前 mode={mode}，档={_name(target) if target is not None else '?'}）")
            elif verdict != "conform":
                rep.add("conformance", "FAIL", f"需求符合性核对结论为 {verdict or '<空>'}，须为 conform（deviate / missing 先回修）")
            elif not _same_ticket_tree(repo, str(cv.get("commit") or ""), head, [p for _, p, _, _ in files]):
                rep.add("conformance", "FAIL", "conformance commit 与 HEAD 的本票文件不一致（核对过期，回修后须重核）")

    meta = {
        "issue": issue_id, "phase": args.phase, "base": base, "head": head,
        "production-files": n_prod, "production-loc": loc,
        "ticket-tier": fm.get("verify-tier"), "tier-reason": fm.get("tier-reason"), "tier-target": _name(target) if target is not None else None,
        "tier-required-by-rules": _name(required), "successors": succ,
        "drift": drift, "redline-hits": redline_hits,
    }
    return rep, meta


def _name(t):
    return {v: k for k, v in TIERS.items()}.get(t, str(t))


def _same_ticket_tree(repo: Path, a: str, b: str, paths) -> bool:
    if not a:
        return False
    if a == b or b.startswith(a) or a.startswith(b):
        return True
    r = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", a, b, "--", *paths], capture_output=True)
    return r.returncode == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dev 流程确定性核对（红线 / 漂移 / 档位 / 证据 / 需求符合性）")
    ap.add_argument("--issue", required=True, help="主工作区内的票文件路径")
    ap.add_argument("--repo", required=True, help="票 worktree（或含该分支的仓库）路径")
    ap.add_argument("--phase", choices=["review", "merge", "close"], required=True,
                    help="review=评审前置门；merge=转 ready 前；close=主会话收口（含符合性核对）")
    ap.add_argument("--base", help="固定点；默认 merge-base(main-branch, head)")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--impl-log")
    ap.add_argument("--evidence")
    ap.add_argument("--json", help="结果 JSON 写入路径")
    args = ap.parse_args(argv)
    try:
        rep, meta = run(args)
    except InputError as e:
        print(f"process-check: 输入错误：{e}", file=sys.stderr)
        return 2
    verdict = "FAIL" if rep.failed else "PASS"
    print(f"process-check {meta['issue']} phase={meta['phase']} {meta['head'][:8]} → {verdict}")
    print(f"  生产文件 {meta['production-files']} / LOC {meta['production-loc']}；票档 {meta['ticket-tier']} → "
          f"目标 {meta['tier-target']}（原因 {meta['tier-reason'] or '未填'}；规则下限 {meta['tier-required-by-rules']}）；后继票 {meta['successors'] or '无（集成票）'}")
    for i in rep.items:
        print(f"  [{i['level']}] {i['check']}: {i['msg']}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"verdict": verdict, **meta, "items": rep.items},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
