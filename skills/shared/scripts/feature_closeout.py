#!/usr/bin/env python3
"""需求收口（feature-closeout: local-human）的确定性门禁：冻结候选、核对自动证据与人工确认、登记合并。

全部票合入每需求独立的集成分支后，主会话按「监督循环 · 需求收口」执行：

  freeze       全票 done + 每票 conformance + main 已同步进集成分支 → 写 integration/candidate.json
  check --phase verify   候选 tree 未变 + 自动证据 all-passed + auto AC 无失败 + feature 级 conformance
                         （通过才叫人做人工测试；--record 追加 feature-verified 事件）
  check --phase merge    verify 全部项 + 人工清单全部 confirmed / waived + confirmation.md 绑定同一 tree
                         + main 自冻结起只在 main-drift-ignore 路径前进（--record 追加 feature-confirmed）
  record-merge --commit  核对主工作区合并提交与候选一致 → 回写 candidate.json + feature-merged 事件
  summary      按候选 / 证据 / 业务结果生成人工确认一页纸骨架（stdout）
  target       打印票的目标分支（worktree 基准 / rebase 与合并目标），派发与合并前用它，不手猜

用法：
  python3 feature_closeout.py freeze --feature-dir <主工作区 dev/features/<m>/<slug>> --repo <集成 worktree>
  python3 feature_closeout.py check --phase verify|merge --feature-dir <...> --repo <集成 worktree> [--record] [--json OUT]
  python3 feature_closeout.py record-merge --feature-dir <...> --repo <主工作区> --commit <合并提交>
  python3 feature_closeout.py summary --feature-dir <...>

产物一律在主工作区 <feature-dir>/integration/，不写集成 worktree、不提交到集成分支。
退出码：0 = 通过；1 = 有 FAIL；2 = 输入 / 配置错误。只依赖 Python 3.8+ 标准库。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_check import (  # noqa: E402  共享解析与 git 辅助，不另写第二份
    InputError, Report, git, match_any, read_first_yaml_block, read_frontmatter, target_branch,
)

CANDIDATE_SCHEMA = "agent-dev.feature-candidate/v1"
RESULTS_SCHEMA = "agent-dev.feature-business-results/v1"
ORACLES = ("auto", "auto+human-review", "human")
DEFAULT_DRIFT_IGNORE = ["dev/**", "docs/**"]
JOURNAL = "progress.jsonl"


# ---------- 读取 ----------

def repo_root_of(feature_dir: Path) -> Path:
    for p in feature_dir.resolve().parents:
        if (p / "dev" / "agents-config.md").is_file():
            return p
    raise InputError(f"{feature_dir} 上溯找不到 dev/agents-config.md（--feature-dir 须指向主工作区 dev/features/<m>/<slug>）")


def load_config(feature_dir: Path) -> dict:
    cfg = read_first_yaml_block(repo_root_of(feature_dir) / "dev" / "agents-config.md")
    fc = cfg.get("feature-closeout") or {}
    return {
        "main-branch": str(cfg.get("main-branch") or "main"),
        "mode": str(fc.get("mode") or "ci-promote"),
        "integration-branch": str(fc.get("integration-branch") or "feat/<feature-slug>"),
        "drift-ignore": fc.get("main-drift-ignore") or DEFAULT_DRIFT_IGNORE,
    }


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise InputError(f"{path} 不是合法 JSON：{exc}")


def yaml_head(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return read_first_yaml_block(path)
    except InputError:
        return {}


def last_merged(feature_dir: Path) -> dict:
    out = {}
    path = feature_dir / JOURNAL
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "merged" and ev.get("issue"):
                out[ev["issue"]] = ev.get("commit")
    return out


def append_event(feature_dir: Path, event: str, **fields):
    ev = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "event": event, "issue": "feature"}
    ev.update({k: v for k, v in fields.items() if v not in (None, "")})
    with (feature_dir / JOURNAL).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def integ_dir(args) -> Path:
    base = Path(args.feature_dir) / "integration"
    return base / args.round if getattr(args, "round", None) else base


def head_info(repo: Path):
    head = git(repo, "rev-parse", "HEAD").strip()
    return head, git(repo, "rev-parse", "HEAD^{tree}").strip()


# ---------- freeze ----------

def freeze(args) -> Report:
    rep = Report()
    fdir = Path(args.feature_dir)
    repo = Path(args.repo)
    cfg = load_config(fdir)
    integ = integ_dir(args)
    if cfg["mode"] != "local-human":
        rep.add("config", "FAIL", f"feature-closeout.mode={cfg['mode']}；freeze 只用于 local-human 收口")

    slug = args.round or fdir.name
    if args.round and not args.issues:
        rep.add("round", "FAIL", "--round 须配 --issues 指明本轮票（同一 feature 目录下多轮需求各自收口）")
    scope = {i.strip() for i in (args.issues or "").split(",") if i.strip()}
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    expected = cfg["integration-branch"].replace("<feature-slug>", slug)
    if branch != expected:
        rep.add("branch", "FAIL", f"集成 worktree 当前分支 {branch} ≠ 约定 {expected}（local-human 要求每需求独立集成分支）")

    dirty = [l for l in git(repo, "status", "--porcelain", "--untracked-files=no").splitlines() if l.strip()]
    if dirty:
        rep.add("worktree", "FAIL", f"集成 worktree 有未提交的已跟踪改动 {len(dirty)} 处；冻结前先提交或还原")

    issues = []
    merged = last_merged(fdir)
    files = sorted((fdir / "issues").glob("issue-*.md"))
    if not files:
        rep.add("issues", "FAIL", f"{fdir / 'issues'} 下没有票文件")
    for f in files:
        fm = read_frontmatter(f)
        m = re.match(r"issue-[0-9]+[a-z]?", f.stem)
        iid = str(fm.get("issue-id") or (m.group(0) if m else f.stem))
        status = str(fm.get("status") or "")
        if status == "deferred" or (scope and iid not in scope):
            continue
        conf = yaml_head(fdir / "impl" / iid / f"conformance-{iid}.md")
        verdict = str(conf.get("verdict") or "")
        issues.append({"id": iid, "status": status, "merged_commit": merged.get(iid), "conformance": verdict or None})
        if status != "done":
            rep.add("issues", "FAIL", f"{iid} status={status or '<空>'}，未全部 done 不得冻结")
        elif not merged.get(iid):
            rep.add("issues", "REVIEW", f"{iid} 已 done 但 progress.jsonl 无 merged 事件（先按账实对账补记）")
        if verdict not in ("conform", "skipped"):
            rep.add("conformance", "FAIL", f"{iid} conformance verdict={verdict or '<缺>'}，须 conform")

    main = cfg["main-branch"]
    main_head = git(repo, "rev-parse", main).strip()
    head, tree = head_info(repo)
    merge_base = git(repo, "merge-base", main, "HEAD").strip()
    if merge_base != main_head:
        rep.add("main-sync", "FAIL", f"{main} 尚未同步进集成分支（merge-base {merge_base[:8]} ≠ {main} {main_head[:8]}）；"
                                     f"先在集成 worktree git merge {main}")

    if rep.failed:
        return rep
    old = load_json(integ / "candidate.json")
    cand = {
        "schema": CANDIDATE_SCHEMA, "feature": f"{fdir.parent.name}/{fdir.name}", "round": args.round,
        "integration_branch": branch, "main_branch": main, "main_head": main_head, "merge_base": merge_base,
        "candidate": head, "candidate_tree": tree, "frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "issues": issues,
    }
    if old.get("candidate_tree") and old["candidate_tree"] != tree:
        cand["supersedes"] = old["candidate_tree"]
        rep.add("candidate", "PASS", f"新候选取代旧 tree {old['candidate_tree'][:8]}：旧证据与人工确认全部失效，按增量重跑")
    integ.mkdir(parents=True, exist_ok=True)
    (integ / "candidate.json").write_text(json.dumps(cand, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rep.add("candidate", "PASS", f"已冻结 {branch}@{head[:8]} tree {tree[:8]}（{len(issues)} 票）")
    return rep


# ---------- check ----------

def _check_results(rep: Report, results: dict, tree: str, phase: str):
    if not results:
        rep.add("business", "FAIL", "integration/business-results.json 不存在")
        return
    if results.get("schema") != RESULTS_SCHEMA:
        rep.add("business", "FAIL", f"business-results schema 应为 {RESULTS_SCHEMA}")
    if results.get("candidate_tree") != tree:
        rep.add("business", "FAIL", "business-results 绑定的 candidate_tree 与候选不一致（证据过期）")
    items = results.get("items") or []
    if not items:
        rep.add("business", "FAIL", "business-results 没有任何 AC 条目")
    counts = {"auto-pass": 0, "unverified": 0, "human-open": 0}
    for it in items:
        aid = it.get("id") or "<无 id>"
        oracle = it.get("oracle")
        status = it.get("status")
        human = it.get("human") or {}
        hstat = human.get("status") or "pending"
        if oracle not in ORACLES:
            rep.add("business", "FAIL", f"{aid} oracle={oracle!r} 非法（{' / '.join(ORACLES)}）")
            continue
        if oracle != "human":
            if status == "fail":
                rep.add("business", "FAIL", f"{aid} 自动执行失败")
            elif status == "unverified":
                counts["unverified"] += 1
                if not it.get("reason"):
                    rep.add("business", "FAIL", f"{aid} 标 unverified 但缺 reason")
            elif status == "pass":
                counts["auto-pass"] += 1
                if not it.get("command"):
                    rep.add("business", "FAIL", f"{aid} 自动通过但缺执行命令（不得只留结论）")
            else:
                rep.add("business", "FAIL", f"{aid} 自动部分 status={status!r}，须 pass / fail / unverified")
        if hstat == "fail":
            rep.add("human", "FAIL", f"{aid} 人工测试未通过：{human.get('note') or '见清单'}")
        if phase != "merge":
            continue
        need_human = oracle in ("human", "auto+human-review") or status == "unverified"
        if not need_human:
            continue
        if hstat not in ("confirmed", "waived"):
            counts["human-open"] += 1
            rep.add("human", "FAIL", f"{aid}（{oracle}{'，自动未验证' if status == 'unverified' else ''}）人工结果 {hstat}，须 confirmed / waived")
        elif not human.get("by"):
            rep.add("human", "FAIL", f"{aid} 人工结果缺 by（谁确认）")
        elif hstat == "waived" and not human.get("note"):
            rep.add("human", "FAIL", f"{aid} waived 须写豁免理由 note")
        elif status == "unverified" and oracle != "human" and hstat != "waived":
            rep.add("human", "FAIL", f"{aid} 自动未验证项只能由人显式 waived，不能记 confirmed 冒充通过")
    rep.add("business", "INFO", f"自动通过 {counts['auto-pass']}，未验证 {counts['unverified']}，人工待定 {counts['human-open']}")


def check(args) -> Report:
    rep = Report()
    fdir = Path(args.feature_dir)
    repo = Path(args.repo)
    cfg = load_config(fdir)
    integ = integ_dir(args)
    cand = load_json(integ / "candidate.json")
    if not cand:
        rep.add("candidate", "FAIL", "integration/candidate.json 不存在：先 freeze")
        return rep
    tree = cand.get("candidate_tree")
    head, head_tree = head_info(repo)
    if head_tree != tree:
        rep.add("candidate", "FAIL", f"集成分支 HEAD tree {head_tree[:8]} ≠ 冻结 tree {str(tree)[:8]}：冻结后有新提交，重新 freeze")

    ev = yaml_head(integ / "evidence.md")
    if not ev:
        rep.add("evidence", "FAIL", "integration/evidence.md 不存在或缺 yaml 头")
    else:
        if ev.get("candidate_tree") != tree:
            rep.add("evidence", "FAIL", "evidence 头 candidate_tree 与候选不一致（证据过期）")
        if ev.get("all-passed") is not True:
            rep.add("evidence", "FAIL", "evidence 头 all-passed 不是 true")
        if str(ev.get("tier-executed") or "") == "full":
            rep.add("evidence", "FAIL", "本机关联验证不得记 tier-executed: full")

    conf = yaml_head(integ / "conformance-feature.md")
    if str(conf.get("verdict") or "") != "conform":
        rep.add("conformance", "FAIL", f"feature 级覆盖核对 verdict={conf.get('verdict') or '<缺>'}，须 conform")

    _check_results(rep, load_json(integ / "business-results.json"), tree, args.phase)

    if args.phase == "merge":
        c = yaml_head(integ / "confirmation.md")
        if not c:
            rep.add("confirmation", "FAIL", "integration/confirmation.md 不存在：人未显式确认不得合 main")
        else:
            if c.get("candidate_tree") != tree:
                rep.add("confirmation", "FAIL", "人工确认绑定的 tree 与候选不一致（确认后候选变了，须重新确认）")
            for k in ("confirmed-by", "confirmed-at", "quote"):
                if not c.get(k):
                    rep.add("confirmation", "FAIL", f"confirmation 缺 {k}（须落人的原话与时间）")
        main = cfg["main-branch"]
        main_now = git(repo, "rev-parse", main).strip()
        if main_now != cand.get("main_head"):
            moved = [p for p in git(repo, "diff", "--name-only", f"{cand.get('main_head')}..{main_now}").splitlines() if p]
            bad = [p for p in moved if not match_any(p, cfg["drift-ignore"])]
            if bad:
                rep.add("main-drift", "FAIL", f"{main} 冻结后前进且触及 {len(bad)} 个非忽略路径（如 {bad[0]}）："
                                              f"同步 {main} 后重新 freeze 并跑增量证据")
            else:
                rep.add("main-drift", "PASS", f"{main} 冻结后前进 {len(moved)} 个文件，均在 main-drift-ignore 内")
        if args.main_repo:
            cur = git(Path(args.main_repo), "rev-parse", "--abbrev-ref", "HEAD").strip()
            if cur != main:
                rep.add("main-repo", "FAIL", f"主工作区当前在 {cur}，不在 {main}：停下报告，不在集成 worktree 合 main")

    if not rep.failed and args.record:
        event = "feature-verified" if args.phase == "verify" else "feature-confirmed"
        append_event(fdir, event, tree=tree[:12], note=f"{cand.get('integration_branch')}@{head[:8]}")
    return rep


# ---------- record-merge ----------

def record_merge(args) -> Report:
    rep = Report()
    fdir = Path(args.feature_dir)
    repo = Path(args.repo)
    cfg = load_config(fdir)
    path = integ_dir(args) / "candidate.json"
    cand = load_json(path)
    if not cand:
        rep.add("candidate", "FAIL", "integration/candidate.json 不存在")
        return rep
    commit = git(repo, "rev-parse", args.commit).strip()
    parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
    if cand["candidate"] not in parents:
        rep.add("merge", "FAIL", f"{commit[:8]} 的父提交不含候选 {cand['candidate'][:8]}（不是合并该候选的提交）")
    else:
        diff = [p for p in git(repo, "diff", "--name-only", cand["candidate"], commit).splitlines() if p]
        bad = [p for p in diff if not match_any(p, cfg["drift-ignore"])]
        if bad:
            rep.add("merge", "FAIL", f"合并结果与候选在 {len(bad)} 个非忽略路径上不同（如 {bad[0]}）：落到 main 的不是验证过的代码")
    if rep.failed:
        return rep
    cand["merged_commit"] = commit
    cand["merged_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    path.write_text(json.dumps(cand, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_event(fdir, "feature-merged", commit=commit[:12], note=f"{cand.get('integration_branch')} → {cfg['main-branch']}")
    rep.add("merge", "PASS", f"已登记合并 {commit[:8]}")
    return rep


# ---------- summary ----------

def summary(args) -> int:
    fdir = Path(args.feature_dir)
    integ = integ_dir(args)
    cand = load_json(integ / "candidate.json")
    if not cand:
        raise InputError("integration/candidate.json 不存在：先 freeze")
    ev = yaml_head(integ / "evidence.md")
    res = load_json(integ / "business-results.json")
    items = res.get("items") or []
    ran = ev.get("ran") or {}
    auto = [i for i in items if i.get("oracle") != "human"]
    passed = [i for i in auto if i.get("status") == "pass"]
    unver = [i for i in auto if i.get("status") == "unverified"]
    human = [i for i in items if i.get("oracle") in ("human", "auto+human-review")]
    lines = [
        f"## 需求收口确认：{args.round or fdir.name}", "",
        f"结果先行：{len(cand.get('issues') or [])} 张票已合入 {cand['integration_branch']}；"
        f"自动证据{'全绿' if ev.get('all-passed') is True else '未通过'}"
        f"（direct {ran.get('direct', '?')} 全跑、indirect {ran.get('indirect', '?')} 跑 / {ran.get('deferred', '?')} 延期兜底、"
        f"quarantined {ran.get('quarantined', '?')}）；可机判业务 AC {len(passed)}/{len(auto)} 通过，{len(unver)} 条未验证。",
        "", f"- 候选：{cand['integration_branch']} @ {cand['candidate'][:8]}（tree {cand['candidate_tree'][:8]}）",
        "- 测试环境：<实例地址或启动命令>", "- 人工清单：",
    ]
    for n, it in enumerate(human, 1):
        lines.append(f"  {n}. [{it.get('oracle')}] {it.get('id')} {it.get('title', '')} —— 步骤：{it.get('steps', '<补>')}；期望：{it.get('expect', '<补>')}")
    if unver:
        lines.append("- 需要你判断的风险（未验证 AC，确认即视为豁免）：")
        lines += [f"  - {i.get('id')} {i.get('title', '')}：{i.get('reason')}" for i in unver]
    for d in ev.get("deferred") or []:
        lines.append(f"  - 延期兜底：{d}")
    lines += ["- 想细看：integration/evidence.md、business-results.json、artifacts/", "",
              "确认后我将：① 主工作区 main 执行 git merge --no-ff "
              f"{cand['integration_branch']}；② 发布本机 Prod 并跑冒烟；push 仍等你另行指示。"]
    print("\n".join(lines))
    return 0


# ---------- main ----------

def _emit(rep: Report, title: str, out) -> int:
    verdict = "FAIL" if rep.failed else "PASS"
    print(f"feature-closeout {title} → {verdict}")
    for i in rep.items:
        print(f"  [{i['level']}] {i['check']}: {i['msg']}")
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({"verdict": verdict, "step": title, "items": rep.items},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if rep.failed else 0


def target(args) -> int:
    issue = Path(args.issue)
    root = None
    for p in issue.resolve().parents:
        if (p / "dev" / "agents-config.md").is_file():
            root = p
            break
    if root is None:
        raise InputError(f"{issue} 上溯找不到 dev/agents-config.md")
    print(target_branch(read_first_yaml_block(root / "dev" / "agents-config.md"), read_frontmatter(issue)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="需求收口（local-human）确定性门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pt = sub.add_parser("target", help="打印票的目标分支")
    pt.add_argument("--issue", required=True, help="主工作区内的票文件路径")
    for name in ("freeze", "check", "record-merge", "summary"):
        p = sub.add_parser(name)
        p.add_argument("--feature-dir", required=True, help="主工作区 dev/features/<feature-module>/<feature-slug>")
        p.add_argument("--round", help="同一 feature 目录下的一轮需求名：产物写 integration/<round>/，集成分支按 <round> 代入")
        if name == "freeze":
            p.add_argument("--issues", help="--round 时本轮票清单，逗号分隔（如 issue-031,issue-039）")
        if name != "summary":
            p.add_argument("--repo", required=True, help="freeze / check：集成 worktree；record-merge：主工作区")
            p.add_argument("--json", help="结果 JSON 写入路径")
        if name == "check":
            p.add_argument("--phase", choices=["verify", "merge"], required=True)
            p.add_argument("--record", action="store_true", help="通过时追加 feature-verified / feature-confirmed 事件")
            p.add_argument("--main-repo", help="merge 相：主工作区路径，核对其当前在 main-branch")
        if name == "record-merge":
            p.add_argument("--commit", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "summary":
            return summary(args)
        if args.cmd == "target":
            return target(args)
        fn = {"freeze": freeze, "check": check, "record-merge": record_merge}[args.cmd]
        rep = fn(args)
    except InputError as e:
        print(f"feature-closeout: 输入错误：{e}", file=sys.stderr)
        return 2
    title = args.cmd + (f" --phase {args.phase}" if args.cmd == "check" else "")
    return _emit(rep, title, args.json)


if __name__ == "__main__":
    sys.exit(main())
