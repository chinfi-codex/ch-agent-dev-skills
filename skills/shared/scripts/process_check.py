#!/usr/bin/env python3
"""确定性流程核对：红线 / 漂移 / 票大小 / DoD+fast 证据 / 需求符合性。

dev 阶段的流程核对只在这里做一次：/implement 在发起评审前（--phase review）与
转 ready 前（--phase merge）运行，主会话收口时运行 --phase close（= merge 全部项 +
需求覆盖矩阵 + 需求符合性核对记录）；/ai-review 只读本脚本
的输出，不再自行复核 glob 与 evidence。本脚本只做可机器判定的事——语义判断
（需求驱动测试更新的映射是否成立、清理复核、需求点与实现是否语义相符）
留给评审与主会话的符合性核对。

用法：
  python3 process_check.py --issue <票文件> --repo <票 worktree 或仓库> \
      --phase review|merge|close|promote [--base REV] [--head REV] [--json OUT]
  CI 分支 / main 放行另加：--phase promote --ci-verdict <branch-ci-verdict.json> \
      --ci-run-proof <trusted-gitea-run-proof.json>

退出码：0 = 全部通过；1 = 有 FAIL 项；2 = 输入 / 配置错误。
只依赖 Python 3.8+ 标准库；只读仓库与 dev/ 产物，唯一写入是 --json 指定的文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

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


def target_branch(config: dict, fm: dict) -> str:
    """票的目标分支（worktree 基准 / rebase 目标 / 合并目标 / diff base）：
    票 frontmatter target-branch 显式指定 > feature-closeout.mode=local-human 时代入 integration-branch > main-branch。
    feature-closeout.since（YYYY-MM-DD）早于该日 created 的存量票不套用集成分支默认，保持在途票原行为。"""
    explicit = str(fm.get("target-branch") or "").strip()
    if explicit:
        return explicit
    fc = config.get("feature-closeout") or {}
    since = str(fc.get("since") or "").strip()
    created = str(fm.get("created") or "").strip()
    legacy = bool(since and re.match(r"\d{4}-\d{2}-\d{2}", created) and created < since)
    if str(fc.get("mode") or "") == "local-human" and not legacy:
        pattern = str(fc.get("integration-branch") or "feat/<feature-slug>")
        slug = str(fm.get("feature-slug") or "").strip()
        if "<feature-slug>" in pattern and not slug:
            raise InputError("local-human 模式下票 frontmatter 缺 feature-slug，无法代入集成分支；或显式写 target-branch")
        return pattern.replace("<feature-slug>", slug)
    return str(config.get("main-branch") or "main")


def find_root(issue_path: Path) -> Path:
    for p in issue_path.resolve().parents:
        if (p / "dev" / "agents-config.md").is_file():
            return p
    raise InputError("向上找不到 dev/agents-config.md（先运行 /setup-dev）")


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
    target_ref = target_branch(config, fm)
    if not args.base:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", target_ref], capture_output=True)
        if r.returncode != 0:
            raise InputError(f"目标分支 {target_ref} 不存在：local-human 须先建集成分支（监督循环 · 需求收口第 1 步），"
                             f"或在票 frontmatter 写 target-branch")
    head = git(repo, "rev-parse", args.head).strip()
    base = args.base or git(repo, "merge-base", target_ref, head).strip()

    rep = Report()
    files = changed_files(repo, base, head)
    if not files:
        rep.add("diff", "FAIL", f"{base[:8]}..{head[:8]} diff 为空")

    log_text = impl_log.read_text(encoding="utf-8") if impl_log.is_file() else ""
    if not log_text:
        rep.add("impl-log", "FAIL", f"impl-log 不存在：{impl_log}")
    redline_sec = md_section(log_text, "红线接触")
    drift_sec = md_section(log_text, "漂移记录")
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

    # 2. 漂移：declared-scope 外的文件须在「漂移记录」节登记
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
        rep.add("drift", "FAIL", "impl-log「漂移记录」为空（无漂移也须留一行）")

    # 3. 票大小：按实际生产 diff 判定，超限必须再拆票。
    n_prod = len(prod)
    loc = sum(a + d for _, _, a, d in prod)
    ticket_max_files = int(policy.get("ticket-max-files") or 10)
    ticket_max_loc = int(policy.get("ticket-max-loc") or 400)
    is_integration = not successors(issue_path, issue_id)
    if n_prod > ticket_max_files:
        rep.add("ticket-size", "FAIL",
                f"生产文件 {n_prod} > ticket-max-files {ticket_max_files}；票过大，须再拆票")
    if loc > ticket_max_loc:
        rep.add("ticket-size", "FAIL",
                f"生产 LOC {loc} > ticket-max-loc {ticket_max_loc}；票过大，须再拆票")

    # 4. 票级证据只核 DoD + fast：全部通过，且本票文件在证据 commit 与 HEAD 间未再变化。
    ev = read_first_yaml_block(evidence) if evidence.is_file() else {}
    if not ev:
        rep.add("evidence", "FAIL", f"evidence 不存在或缺 yaml 头：{evidence}")
    ev_text = evidence.read_text(encoding="utf-8") if evidence.is_file() else ""
    if str(fm.get("type") or "").strip() == "bug" and ev_text and not md_section(ev_text, "回归有效性").strip():
        rep.add("evidence", "FAIL", "type: bug 票缺 evidence「回归有效性」节（revert 修复 → 见红 → 恢复 → 绿）")
    if ev:
        ev_commit = str(ev.get("commit") or "")
        if ev.get("all-passed") is not True:
            rep.add("evidence", "FAIL", "DoD + fast 证据的 all-passed 不是 true")
        if not _same_ticket_tree(repo, ev_commit, head, [p for _, p, _, _ in files]):
            rep.add("evidence", "FAIL", f"evidence commit {ev_commit[:8] or '<空>'} 与 HEAD {head[:8]} 的本票文件不一致（证据过期）")

    if args.phase == "promote":
        if not args.ci_verdict:
            rep.add("ci-verdict", "FAIL", "main 放行缺 --ci-verdict；不得把票级 close 当作 main 放行")
        else:
            verdict_path = Path(args.ci_verdict)
            try:
                verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                verdict = {}
                rep.add("ci-verdict", "FAIL", f"CI 判决不可读：{exc}")
            expected_tree = git(repo, "rev-parse", f"{head}^{{tree}}").strip()
            checks = (
                (verdict.get("schema") == "pandu.test-verdict/v1", "schema 非法"),
                (verdict.get("status") == "completed", "status 不是 completed"),
                (verdict.get("pass") is True, "pass 不是 true"),
                (verdict.get("commit") == head, "commit 与 HEAD 不一致"),
                (verdict.get("tree") == expected_tree, "tree 与 HEAD 不一致"),
                (verdict.get("ref") == git(repo, "symbolic-ref", "HEAD").strip(), "ref 与当前分支不一致"),
                (bool(verdict.get("run_id")), "缺 run_id"),
                (verdict.get("ran_n", 0) > 0, "没有实际执行测试"),
                (verdict.get("skipped_env_n") == 0, "仍有 skipped-env 未决项"),
                (not verdict.get("new_failures"), "仍有新增失败"),
                (not verdict.get("unattributed_failures"), "仍有未归因失败"),
                (verdict.get("attribution_status") in ("not-needed", "completed"), "失败归因尚未完成"),
                (verdict.get("quarantine_overdue_n", 0) == 0, "仍有超期隔离条目"),
                (not verdict.get("quarantined_n") or bool(verdict.get("quarantine_config_sha256")),
                 "隔离项缺配置哈希"),
            )
            for ok, message in checks:
                if not ok:
                    rep.add("ci-verdict", "FAIL", f"main 放行判决无效：{message}")
            proof = {}
            if not args.ci_run_proof:
                rep.add("ci-run-proof", "FAIL", "main 放行缺受控监督生成的 --ci-run-proof")
            else:
                try:
                    proof = json.loads(Path(args.ci_run_proof).read_text(encoding="utf-8"))
                    verdict_digest = hashlib.sha256(verdict_path.read_bytes()).hexdigest()
                except (OSError, ValueError) as exc:
                    verdict_digest = ""
                    rep.add("ci-run-proof", "FAIL", f"真实 run 证明不可读：{exc}")
                current_ref = git(repo, "symbolic-ref", "HEAD").strip()
                proof_checks = (
                    (proof.get("schema") == "pandu.ci-run-proof/v1", "run 证明 schema 非法"),
                    (proof.get("run_id") == str(verdict.get("run_id")), "run_id 与真实 run 证明不一致"),
                    (proof.get("commit") == head, "真实 run commit 与 HEAD 不一致"),
                    (proof.get("branch") in (current_ref, current_ref.removeprefix("refs/heads/")),
                     "真实 run 分支与当前分支不一致"),
                    (proof.get("status") == "completed", "真实 run 尚未完成"),
                    (proof.get("conclusion") == "success", "真实 run 不是 success"),
                    (proof.get("verdict_pass") is True, "真实 run 的 verdict 未通过"),
                    (proof.get("verdict_sha256") == verdict_digest, "verdict 文件与真实 run 证据哈希不一致"),
                )
                for ok, message in proof_checks:
                    if not ok:
                        rep.add("ci-run-proof", "FAIL", f"main 放行真实 run 证明无效：{message}")
            if not rep.failed:
                rep.add("ci-verdict", "PASS", "CI 判决与 HEAD commit / tree 一致且全部必跑项通过")

    # 5. worktree dev/ 卫生：约定是「无 dev/」或「指向主工作区 dev/ 的软链」；实体副本 = 磁盘随票线性膨胀、清场丢真源
    # .git 为文件说明 --repo 是 worktree 而非主工作区（主工作区的 dev/ 本来就是真源，不查）
    if (repo / ".git").is_file():
        dev_entry = repo / "dev"
        if dev_entry.is_symlink():
            pass
        elif dev_entry.is_dir():
            if git(repo, "ls-files", "--", "dev").strip():
                rep.add("worktree-dev", "REVIEW",
                        "worktree 内 dev/ 是 git 跟踪的实体目录——dev/ 入库使每个 worktree 自带完整副本，"
                        "建议 dev/ 出库（git rm -r --cached dev）后改软链约定")
            else:
                rep.add("worktree-dev", "FAIL",
                        "worktree 内 dev/ 是未跟踪实体副本——约定为指向主工作区 dev/ 的软链；"
                        "先把未收录内容按 salvage 规则收回主工作区，再删副本建软链（ln -s ../../dev <worktree>/dev）")
        elif dev_entry.exists():
            rep.add("worktree-dev", "FAIL", "worktree 内 dev/ 是非常规文件类型，应为指向主工作区 dev/ 的软链")

    # 6. 需求符合性核对（仅 close 相）：主会话合并前核对结论落盘，此处只把机器可判的关
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
                if not (mode == "sampled" and not is_integration):
                    rep.add("conformance", "FAIL",
                            f"verdict: skipped 仅允许 conformance.mode=sampled 的非集成票"
                            f"（当前 mode={mode}，集成票={'是' if is_integration else '否'}）")
            elif verdict != "conform":
                rep.add("conformance", "FAIL", f"需求符合性核对结论为 {verdict or '<空>'}，须为 conform（deviate / missing 先回修）")
            elif not _same_ticket_tree(repo, str(cv.get("commit") or ""), head, [p for _, p, _, _ in files]):
                rep.add("conformance", "FAIL", "conformance commit 与 HEAD 的本票文件不一致（核对过期，回修后须重核）")

    meta = {
        "issue": issue_id, "phase": args.phase, "target-branch": target_ref, "base": base, "head": head,
        "production-files": n_prod, "production-loc": loc,
        "ticket-max-files": ticket_max_files, "ticket-max-loc": ticket_max_loc,
        "integration-ticket": is_integration,
        "drift": drift, "redline-hits": redline_hits,
    }
    return rep, meta


def _same_ticket_tree(repo: Path, a: str, b: str, paths) -> bool:
    if not a:
        return False
    if a == b or b.startswith(a) or a.startswith(b):
        return True
    r = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", a, b, "--", *paths], capture_output=True)
    return r.returncode == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dev 流程确定性核对（红线 / 漂移 / 票大小 / DoD+fast 证据 / 需求符合性）")
    ap.add_argument("--issue", required=True, help="主工作区内的票文件路径")
    ap.add_argument("--repo", required=True, help="票 worktree（或含该分支的仓库）路径")
    ap.add_argument("--phase", choices=["review", "merge", "close", "promote"], required=True,
                    help="review=评审前置门；merge=转 ready 前；close=需求分支收口；promote=main 放行（需 CI 判决）")
    ap.add_argument("--ci-verdict", help="promote 所需的 pandu.test-verdict/v1 JSON")
    ap.add_argument("--ci-run-proof", help="受控监督通过真实 Gitea API 生成的 pandu.ci-run-proof/v1 JSON")
    ap.add_argument("--base", help="固定点；默认 merge-base(目标分支, head)，目标分支见 target_branch()")
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
    print(f"  生产文件 {meta['production-files']} / 上限 {meta['ticket-max-files']}；"
          f"LOC {meta['production-loc']} / 上限 {meta['ticket-max-loc']}；"
          f"集成票 {'是' if meta['integration-ticket'] else '否'}")
    for i in rep.items:
        print(f"  [{i['level']}] {i['check']}: {i['msg']}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"verdict": verdict, **meta, "items": rep.items},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
