#!/usr/bin/env python3
"""需求收口（feature-closeout: local-human）的确定性门禁。

  validate-plan  校验功能验收计划：执行方式与最终判定分离，人工原因可解释
  freeze         全票 done + conformance + main 已同步 + 验收计划有效 → 冻结代码与计划
  check verify   候选与计划未变 + 自动证据 + AC 完整对账 + 当前验收报告
  check merge    verify 全部项 + 人工结果 / 豁免 + 合并确认绑定同一候选
  report         从冻结计划与实际结果生成 acceptance-report.md，不接受手写摘要代替
  record-merge   核对主工作区合并提交与候选一致并登记
  target         打印票的目标分支

产物一律在主工作区 <feature-dir>/integration/，不写集成 worktree、不提交到集成分支。
退出码：0 = 通过；1 = 有 FAIL；2 = 输入 / 配置错误。只依赖 Python 3.8+ 标准库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_check import (  # noqa: E402  共享解析与 git 辅助，不另写第二份
    InputError, Report, git, match_any, parse_yaml_subset, read_first_yaml_block, read_frontmatter,
    target_branch,
)

CANDIDATE_SCHEMA = "agent-dev.feature-candidate/v2"
PLAN_SCHEMA = "agent-dev.feature-acceptance/v1"
RESULTS_SCHEMA = "agent-dev.feature-business-results/v2"
EXECUTORS = ("command", "browser-agent", "human")
ORACLES = ("auto", "auto+human-review", "human")
ORACLE_RANK = {"auto": 0, "auto+human-review": 1, "human": 2}
HUMAN_REASONS = (
    "intent", "visual-ux", "domain-judgment", "high-risk", "no-reliable-oracle",
    "environment-unavailable", "evidence-insufficient",
)
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan_source(feature_dir: Path) -> Path:
    return feature_dir / "acceptance-plan.yaml"


def read_plan(feature_dir: Path) -> dict:
    path = plan_source(feature_dir)
    if not path.is_file():
        raise InputError(f"{path} 不存在：技术方案阶段必须产出功能验收计划")
    return parse_yaml_subset(path.read_text(encoding="utf-8"))


def validate_plan_data(plan: dict) -> list:
    errors = []
    if plan.get("schema") != PLAN_SCHEMA:
        errors.append(f"schema 应为 {PLAN_SCHEMA}")
    cases = plan.get("cases")
    if not isinstance(cases, dict) or not cases:
        return errors + ["cases 必须是以 AC 编号为键的非空映射"]
    for aid, case in cases.items():
        prefix = f"{aid}："
        if not re.fullmatch(r"AC-[0-9]+", str(aid)):
            errors.append(prefix + "编号须为 AC-<数字>")
        if not isinstance(case, dict):
            errors.append(prefix + "内容必须是映射")
            continue
        for key in ("title", "source", "expected"):
            if not case.get(key):
                errors.append(prefix + f"缺 {key}")
        if not isinstance(case.get("required"), bool):
            errors.append(prefix + "required 必须显式为 true / false")
        executor = case.get("executor")
        oracle = case.get("oracle")
        if executor not in EXECUTORS:
            errors.append(prefix + f"executor={executor!r} 非法（{' / '.join(EXECUTORS)}）")
        if oracle not in ORACLES:
            errors.append(prefix + f"oracle={oracle!r} 非法（{' / '.join(ORACLES)}）")
        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(prefix + "steps 必须是非空列表")
        for list_key in ("preconditions", "human_steps", "evidence"):
            if list_key in case and case.get(list_key) is not None and not isinstance(case.get(list_key), list):
                errors.append(prefix + f"{list_key} 必须是列表")
        if executor == "command" and not case.get("command"):
            errors.append(prefix + "command 执行方式缺 command")
        if executor == "human" and oracle != "human":
            errors.append(prefix + "人工执行时 oracle 必须为 human")
        if oracle in ("auto", "auto+human-review"):
            assertions = case.get("assertions")
            if not isinstance(assertions, dict) or not assertions:
                errors.append(prefix + "自动判定必须写 assertions（断言类型 → 预期）")
        if oracle in ("auto+human-review", "human"):
            if case.get("human_reason") not in HUMAN_REASONS:
                errors.append(prefix + f"人工原因须为 {' / '.join(HUMAN_REASONS)}")
        elif case.get("human_reason"):
            errors.append(prefix + "auto 不需要 human_reason")
        if executor == "browser-agent":
            evidence = case.get("evidence")
            if not isinstance(evidence, list) or "screenshot" not in evidence:
                errors.append(prefix + "浏览器验收的 evidence 必须包含 screenshot")
    return errors


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


# ---------- 验收计划 ----------

def validate_plan(args) -> Report:
    rep = Report()
    try:
        plan = read_plan(Path(args.feature_dir))
    except InputError as exc:
        rep.add("plan", "FAIL", str(exc))
        return rep
    errors = validate_plan_data(plan)
    for error in errors:
        rep.add("plan", "FAIL", error)
    if not errors:
        rep.add("plan", "PASS", f"功能验收计划有效：{len(plan['cases'])} 条 AC，执行方式与最终判定已分离")
    return rep


def frozen_plan(cand: dict, integ: Path) -> dict:
    plan = load_json(integ / "acceptance-plan.json")
    if not plan:
        return {}
    meta = cand.get("acceptance_plan") or {}
    if plan.get("schema") != PLAN_SCHEMA:
        return {}
    if set((plan.get("cases") or {}).keys()) != set(meta.get("cases") or []):
        return {}
    return plan


def check_plan_unchanged(rep: Report, feature_dir: Path, cand: dict):
    meta = cand.get("acceptance_plan") or {}
    try:
        digest = sha256_file(plan_source(feature_dir))
    except FileNotFoundError:
        rep.add("plan", "FAIL", "acceptance-plan.yaml 已丢失；恢复计划后重新 freeze")
        return
    if digest != meta.get("sha256"):
        rep.add("plan", "FAIL", "验收计划在冻结后发生变化；旧结果与报告不得套用，重新 freeze")


# ---------- freeze ----------

def freeze(args) -> Report:
    rep = Report()
    fdir = Path(args.feature_dir)
    repo = Path(args.repo)
    cfg = load_config(fdir)
    integ = integ_dir(args)
    if cfg["mode"] != "local-human":
        rep.add("config", "FAIL", f"feature-closeout.mode={cfg['mode']}；freeze 只用于 local-human 收口")

    try:
        plan = read_plan(fdir)
        plan_errors = validate_plan_data(plan)
    except InputError as exc:
        plan, plan_errors = {}, [str(exc)]
    for error in plan_errors:
        rep.add("plan", "FAIL", error)

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
    digest = sha256_file(plan_source(fdir))
    cand = {
        "schema": CANDIDATE_SCHEMA, "feature": f"{fdir.parent.name}/{fdir.name}", "round": args.round,
        "integration_branch": branch, "main_branch": main, "main_head": main_head, "merge_base": merge_base,
        "candidate": head, "candidate_tree": tree, "frozen_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "issues": issues,
        "acceptance_plan": {
            "path": "acceptance-plan.yaml", "sha256": digest, "cases": sorted(plan["cases"].keys()),
        },
    }
    if old.get("candidate_tree") and old["candidate_tree"] != tree:
        cand["supersedes"] = old["candidate_tree"]
        rep.add("candidate", "PASS", f"新候选取代旧 tree {old['candidate_tree'][:8]}：旧证据与人工确认全部失效，按增量重跑")
    old_plan = (old.get("acceptance_plan") or {}).get("sha256")
    if old_plan and old_plan != digest:
        rep.add("plan", "PASS", "验收计划已换版：旧业务结果、人工确认与报告全部失效")
    integ.mkdir(parents=True, exist_ok=True)
    (integ / "acceptance-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (integ / "candidate.json").write_text(json.dumps(cand, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rep.add("candidate", "PASS", f"已冻结 {branch}@{head[:8]} tree {tree[:8]}（{len(issues)} 票，{len(plan['cases'])} 条 AC）")
    return rep


# ---------- 业务结果 ----------

def _check_artifacts(rep: Report, fdir: Path, aid: str, artifacts) -> bool:
    screenshot = False
    if artifacts is None:
        return False
    if not isinstance(artifacts, list):
        rep.add("evidence", "FAIL", f"{aid} artifacts 必须是列表")
        return False
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not artifact.get("path") or not artifact.get("sha256"):
            rep.add("evidence", "FAIL", f"{aid} 每个产物须带 path 与 sha256")
            continue
        rel = Path(str(artifact["path"]))
        if rel.is_absolute() or ".." in rel.parts:
            rep.add("evidence", "FAIL", f"{aid} 产物路径必须相对需求目录且不得含 ..：{rel}")
            continue
        path = fdir / rel
        if not path.is_file():
            rep.add("evidence", "FAIL", f"{aid} 产物不存在：{rel}")
            continue
        if sha256_file(path) != str(artifact["sha256"]).lower():
            rep.add("evidence", "FAIL", f"{aid} 产物哈希不匹配：{rel}")
            continue
        if artifact.get("type") == "screenshot":
            screenshot = True
    return screenshot


def _check_auto_pass(rep: Report, fdir: Path, item: dict):
    aid = item.get("id") or "<无 id>"
    executor = item.get("executor")
    execution = item.get("execution")
    if not isinstance(execution, dict):
        rep.add("evidence", "FAIL", f"{aid} 自动通过但缺 execution 实际执行记录")
    elif execution.get("executor") != executor:
        rep.add("evidence", "FAIL", f"{aid} execution.executor 与有效执行方式不一致")
    else:
        if executor == "command":
            if not execution.get("command"):
                rep.add("evidence", "FAIL", f"{aid} 命令验收缺实际执行命令")
            if execution.get("exit") != 0 or isinstance(execution.get("exit"), bool):
                rep.add("evidence", "FAIL", f"{aid} 命令验收通过须记录 exit: 0")
        elif executor == "browser-agent":
            if not execution.get("url"):
                rep.add("evidence", "FAIL", f"{aid} 浏览器验收缺实际测试 URL")
            if not isinstance(execution.get("actions"), list) or not execution.get("actions"):
                rep.add("evidence", "FAIL", f"{aid} 浏览器验收缺实际 actions")
            for key in ("console_errors", "page_errors"):
                if not isinstance(execution.get(key), list):
                    rep.add("evidence", "FAIL", f"{aid} 浏览器验收须记录 {key} 列表")
                elif execution.get(key):
                    rep.add("evidence", "FAIL", f"{aid} 浏览器通过但 {key} 非空")
    assertions = item.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        rep.add("evidence", "FAIL", f"{aid} 自动通过必须有实际断言，截图或自述不能代替")
    else:
        for assertion in assertions:
            if not isinstance(assertion, dict) or not assertion.get("type"):
                rep.add("evidence", "FAIL", f"{aid} 断言缺 type")
                continue
            if "expected" not in assertion or "actual" not in assertion or assertion.get("status") != "pass":
                rep.add("evidence", "FAIL", f"{aid} 断言须带 expected / actual 且 status=pass")
    screenshot = _check_artifacts(rep, fdir, aid, item.get("artifacts"))
    if executor == "browser-agent" and not screenshot:
        rep.add("evidence", "FAIL", f"{aid} 浏览器通过至少须有一个 type=screenshot 的有效产物")


def _check_reclassification(rep: Report, spec: dict, item: dict) -> bool:
    aid = item.get("id") or "<无 id>"
    rec = item.get("reclassification")
    if not rec:
        if item.get("oracle") != spec.get("oracle") or item.get("executor") != spec.get("executor"):
            rep.add("classification", "FAIL", f"{aid} 执行方式或判定归属与计划不同，但未登记 reclassification")
            return False
        return True
    if not isinstance(rec, dict):
        rep.add("classification", "FAIL", f"{aid} reclassification 必须是映射")
        return False
    ok = True
    for key, spec_key in (("from_oracle", "oracle"), ("from_executor", "executor")):
        if rec.get(key) != spec.get(spec_key):
            rep.add("classification", "FAIL", f"{aid} reclassification.{key} 与冻结计划不一致")
            ok = False
    if rec.get("to_oracle") != item.get("oracle") or rec.get("to_executor") != item.get("executor"):
        rep.add("classification", "FAIL", f"{aid} reclassification 的目标与条目有效归属不一致")
        ok = False
    from_oracle = spec.get("oracle")
    to_oracle = item.get("oracle")
    if from_oracle not in ORACLE_RANK or to_oracle not in ORACLE_RANK or ORACLE_RANK[to_oracle] <= ORACLE_RANK[from_oracle]:
        rep.add("classification", "FAIL", f"{aid} 收口时只允许向更严格的人工判定调整，不得自动降低人工要求")
        ok = False
    if item.get("executor") != spec.get("executor") and item.get("executor") != "human":
        rep.add("classification", "FAIL", f"{aid} 执行方式只能调整为 human，不得静默换另一条自动通道")
        ok = False
    if item.get("executor") == "human" and item.get("oracle") != "human":
        rep.add("classification", "FAIL", f"{aid} 调整为人工执行时判定归属必须为 human")
        ok = False
    if rec.get("reason") not in HUMAN_REASONS or not rec.get("note"):
        rep.add("classification", "FAIL", f"{aid} 分类调整须带合法 reason 与具体 note")
        ok = False
    return ok


def _check_results(rep: Report, fdir: Path, results: dict, plan: dict, cand: dict, phase: str):
    if not results:
        rep.add("business", "FAIL", "integration/business-results.json 不存在")
        return
    if results.get("schema") != RESULTS_SCHEMA:
        rep.add("business", "FAIL", f"business-results schema 应为 {RESULTS_SCHEMA}")
    if results.get("candidate_tree") != cand.get("candidate_tree"):
        rep.add("business", "FAIL", "business-results 绑定的 candidate_tree 与候选不一致（证据过期）")
    if results.get("plan_sha256") != (cand.get("acceptance_plan") or {}).get("sha256"):
        rep.add("business", "FAIL", "business-results 绑定的验收计划哈希与冻结计划不一致")
    environment = results.get("environment")
    if not isinstance(environment, dict) or not environment.get("name"):
        rep.add("business", "FAIL", "business-results 缺 environment.name（实际测试环境）")
    cases = plan.get("cases") or {}
    if not cases:
        rep.add("plan", "FAIL", "integration/acceptance-plan.json 不存在或与候选索引不一致")
        return
    items = results.get("items")
    if not isinstance(items, list) or not items:
        rep.add("business", "FAIL", "business-results 没有任何 AC 条目")
        return
    seen = set()
    counts = {"auto-pass": 0, "unverified": 0, "human-open": 0}
    for it in items:
        if not isinstance(it, dict):
            rep.add("business", "FAIL", "AC 结果条目必须是映射")
            continue
        aid = it.get("id") or "<无 id>"
        if aid in seen:
            rep.add("coverage", "FAIL", f"{aid} 结果重复")
            continue
        seen.add(aid)
        spec = cases.get(aid)
        if spec is None:
            rep.add("coverage", "FAIL", f"{aid} 不在冻结验收计划中，不得额外混入结果")
            continue
        required = spec.get("required") is True
        oracle = it.get("oracle")
        executor = it.get("executor")
        if oracle not in ORACLES:
            rep.add("business", "FAIL", f"{aid} oracle={oracle!r} 非法（{' / '.join(ORACLES)}）")
            continue
        if executor not in EXECUTORS:
            rep.add("business", "FAIL", f"{aid} executor={executor!r} 非法（{' / '.join(EXECUTORS)}）")
            continue
        _check_reclassification(rep, spec, it)
        status = it.get("status")
        human = it.get("human") or {}
        hstat = human.get("status") or "pending"
        if hstat not in ("pending", "confirmed", "waived", "fail"):
            rep.add("human", "FAIL", f"{aid} human.status={hstat!r} 非法")
            continue
        if oracle == "human":
            if status not in (None, "unverified"):
                rep.add("business", "FAIL", f"{aid} 最终判定为 human，不得填写自动 pass / fail；另记人工结果")
        elif status == "fail":
            rep.add("business", "FAIL" if required else "INFO", f"{aid} 自动执行失败")
        elif status == "unverified":
            counts["unverified"] += 1
            if not it.get("reason"):
                rep.add("business", "FAIL", f"{aid} 标 unverified 但缺 reason")
        elif status == "pass":
            counts["auto-pass"] += 1
            _check_auto_pass(rep, fdir, it)
        else:
            rep.add("business", "FAIL", f"{aid} 自动部分 status={status!r}，须 pass / fail / unverified")
        if status != "pass" and it.get("artifacts"):
            _check_artifacts(rep, fdir, aid, it.get("artifacts"))
        if hstat == "fail":
            rep.add("human", "FAIL" if required else "INFO", f"{aid} 人工测试未通过：{human.get('note') or '见清单'}")
        if phase != "merge" or not required:
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
            rep.add("human", "FAIL", f"{aid} 自动未验证项只能由人显式 waived；若人已实际测试，须登记向 human 的分类调整")
    missing = sorted(set(cases) - seen)
    for aid in missing:
        rep.add("coverage", "FAIL", f"{aid} 在冻结验收计划中但没有结果，不得漏验")
    rep.add("business", "INFO", f"计划 {len(cases)} 条，结果 {len(seen & set(cases))} 条；自动通过 {counts['auto-pass']}，未验证 {counts['unverified']}，人工待定 {counts['human-open']}")


# ---------- 报告 ----------

def _md(value) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _human_reason(spec: dict, item: dict) -> str:
    rec = item.get("reclassification") or {}
    return rec.get("reason") or spec.get("human_reason") or "—"


def _result_map(results: dict):
    out = {}
    for item in results.get("items") or []:
        if isinstance(item, dict) and item.get("id") and item["id"] not in out:
            out[item["id"]] = item
    return out


def _report_decision(plan: dict, results: dict, evidence: dict, conformance: dict, cand: dict, confirmation: dict, material_invalid: bool) -> str:
    items = _result_map(results)
    blocking = material_invalid or evidence.get("all-passed") is not True or conformance.get("verdict") != "conform"
    waiting_auto = waiting_human = waived = 0
    seen = set(items)
    if seen - set(plan.get("cases") or {}):
        blocking = True
    raw_items = results.get("items") or []
    if len(raw_items) != len(items):
        blocking = True
    for aid, spec in (plan.get("cases") or {}).items():
        if spec.get("required") is not True:
            continue
        item = items.get(aid)
        if not item:
            if spec.get("oracle") == "human":
                waiting_human += 1
            else:
                waiting_auto += 1
            continue
        oracle = item.get("oracle")
        status = item.get("status")
        human = item.get("human") or {}
        hstat = human.get("status") or "pending"
        if status == "fail" or hstat == "fail":
            blocking = True
            continue
        if hstat == "waived":
            waived += 1
        if oracle == "human":
            if hstat not in ("confirmed", "waived"):
                waiting_human += 1
        elif status == "pass":
            if oracle == "auto+human-review" and hstat not in ("confirmed", "waived"):
                waiting_human += 1
        elif status == "unverified":
            if hstat != "waived":
                waiting_human += 1
        else:
            waiting_auto += 1
    if blocking:
        return "不可合并：存在失败、覆盖缺口或无效验收材料"
    if waiting_auto:
        return f"等待自动验收：仍有 {waiting_auto} 条必需 AC 没有有效自动结果"
    if waiting_human:
        return f"等待人工处理：仍有 {waiting_human} 条必需 AC 待确认、实测或豁免"
    confirmed = (
        confirmation.get("candidate_tree") == cand.get("candidate_tree")
        and all(confirmation.get(k) for k in ("confirmed-by", "confirmed-at", "quote"))
    )
    if waived:
        suffix = f"，但有 {waived} 项人工豁免"
    else:
        suffix = ""
    return "验收条件已满足" + suffix + ("，合并确认已记录" if confirmed else "，等待人的合并授权")


def render_report(fdir: Path, integ: Path, cand: dict, plan: dict) -> str:
    results = load_json(integ / "business-results.json")
    validation = Report()
    _check_results(validation, fdir, results, plan, cand, "verify")
    material_invalid = any(i["level"] == "FAIL" and i["check"] != "coverage" for i in validation.items)
    evidence = yaml_head(integ / "evidence.md")
    conformance = yaml_head(integ / "conformance-feature.md")
    confirmation = yaml_head(integ / "confirmation.md")
    items = _result_map(results)
    cases = plan.get("cases") or {}
    environment = results.get("environment") or {}
    ran = evidence.get("ran") or {}
    effective = []
    for aid, spec in cases.items():
        item = items.get(aid, {})
        effective.append((aid, spec, item, item.get("oracle") or spec.get("oracle")))
    full = [x for x in effective if x[3] == "auto"]
    assisted = [x for x in effective if x[3] == "auto+human-review"]
    manual = [x for x in effective if x[3] == "human"]
    passed = [x for x in full if x[2].get("status") == "pass"]
    failed = [x for x in full if x[2].get("status") == "fail"]
    unverified = [x for x in effective if x[3] != "human" and x[2].get("status") == "unverified"]
    assisted_done = [x for x in assisted if (x[2].get("human") or {}).get("status") in ("confirmed", "waived")]
    manual_done = [x for x in manual if (x[2].get("human") or {}).get("status") in ("confirmed", "waived")]
    lines = [
        f"# 功能验收报告：{cand.get('round') or fdir.name}", "",
        f"- 候选：{cand.get('integration_branch')} @ {str(cand.get('candidate'))[:12]}（tree {str(cand.get('candidate_tree'))[:12]}）",
        f"- 冻结时间：{cand.get('frozen_at', '—')}",
        f"- 测试环境：{_md(environment.get('name'))}{' / ' + _md(environment.get('url')) if environment.get('url') else ''}",
        f"- 验收计划：{_md((cand.get('acceptance_plan') or {}).get('path'))}（sha256 {str((cand.get('acceptance_plan') or {}).get('sha256'))[:12]}）；{len(items.keys() & cases.keys())}/{len(cases)} 条已有结果，缺失 {len(set(cases) - set(items))} 条",
        f"- 关联自动检查：{'通过' if evidence.get('all-passed') is True else '未通过或未完成'}"
        f"（direct {ran.get('direct', '?')}、indirect {ran.get('indirect', '?')}、deferred {ran.get('deferred', '?')}、quarantined {ran.get('quarantined', '?')}）",
        f"- AI 完整验收：{len(passed)}/{len(full)} 通过，{len(failed)} 失败，{len([x for x in full if x[2].get('status') == 'unverified'])} 未验证",
        f"- AI 执行、人工复核：{len(assisted_done)}/{len(assisted)} 已处理",
        f"- 人工验收：{len(manual_done)}/{len(manual)} 已处理",
        "", "## 逐条验收结果", "",
        "| AC | 需求来源 | 执行方式 | 最终判定 | AI 结果 | 人工原因 | 人工结果 | 证据 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    status_names = {"pass": "通过", "fail": "失败", "unverified": "未验证"}
    human_names = {"pending": "待确认", "confirmed": "已确认", "waived": "已豁免", "fail": "未通过"}
    for aid, spec, item, oracle in effective:
        status = status_names.get(item.get("status"), "未登记") if oracle != "human" else "不适用"
        need_human = oracle in ("human", "auto+human-review") or item.get("status") == "unverified"
        hstat = (item.get("human") or {}).get("status") or "pending"
        human_result = human_names.get(hstat, hstat) if need_human else "不需要"
        artifacts = ", ".join(str(a.get("path")) for a in item.get("artifacts") or [] if isinstance(a, dict) and a.get("path"))
        lines.append(
            f"| {aid} | {_md(spec.get('source'))} | {_md(item.get('executor') or spec.get('executor'))} | {oracle} | {status} | "
            f"{_md(_human_reason(spec, item)) if need_human else '—'} | {human_result} | {_md(artifacts)} |"
        )
    lines += ["", "## 人工验收清单", ""]
    human_rows = [x for x in effective if x[3] in ("human", "auto+human-review") or x[2].get("status") == "unverified"]
    if not human_rows:
        lines.append("无。")
    for aid, spec, item, oracle in human_rows:
        steps = spec.get("human_steps") or spec.get("steps") or []
        expect = spec.get("human_expect") or spec.get("expected")
        hstat = (item.get("human") or {}).get("status") or "pending"
        lines += [
            f"### {aid} {spec.get('title')}",
            f"- 为什么需要人工：{_md(_human_reason(spec, item))}",
            f"- 操作：{'；'.join(str(s) for s in steps)}",
            f"- 期望：{_md(expect)}",
            f"- 当前结果：{human_names.get(hstat, hstat)}",
            "",
        ]
    lines += ["## AI 分类调整", ""]
    adjustments = [(aid, item.get("reclassification")) for aid, _, item, _ in effective if item.get("reclassification")]
    if not adjustments:
        lines.append("无。")
    for aid, rec in adjustments:
        lines.append(f"- {aid}：{rec.get('from_executor')} / {rec.get('from_oracle')} → {rec.get('to_executor')} / {rec.get('to_oracle')}；原因：{rec.get('reason')}；说明：{rec.get('note')}")
    lines += ["", "## 未验证、延期与跳过", ""]
    if not unverified and not evidence.get("deferred"):
        lines.append("无。")
    for aid, spec, item, _ in unverified:
        lines.append(f"- {aid} {spec.get('title')}：{item.get('reason', '未说明原因')}")
    for deferred in evidence.get("deferred") or []:
        lines.append(f"- 延期兜底：{deferred}")
    lines += [
        "", "## 合并建议", "",
        _report_decision(plan, results, evidence, conformance, cand, confirmation, material_invalid), "",
        "本报告只说明验收事实与建议，不代替人的合并、发布或 push 授权。", "",
        "想细看：integration/evidence.md、business-results.json、acceptance-plan.json、artifacts/。", "",
    ]
    return "\n".join(lines)


def report(args) -> int:
    fdir = Path(args.feature_dir)
    integ = integ_dir(args)
    cand = load_json(integ / "candidate.json")
    if not cand:
        raise InputError("integration/candidate.json 不存在：先 freeze")
    _, tree = head_info(Path(args.repo))
    if tree != cand.get("candidate_tree"):
        raise InputError("集成分支 tree 已变化：先重新 freeze，不能给新候选套用旧报告")
    meta = cand.get("acceptance_plan") or {}
    if sha256_file(plan_source(fdir)) != meta.get("sha256"):
        raise InputError("验收计划已变化：先重新 freeze，不能生成跨计划版本的报告")
    plan = frozen_plan(cand, integ)
    if not plan:
        raise InputError("integration/acceptance-plan.json 不存在或与候选索引不一致：重新 freeze")
    text = render_report(fdir, integ, cand, plan)
    (integ / "acceptance-report.md").write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


# ---------- check ----------

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
    check_plan_unchanged(rep, fdir, cand)
    plan = frozen_plan(cand, integ)

    ev = yaml_head(integ / "evidence.md")
    if not ev:
        rep.add("evidence", "FAIL", "integration/evidence.md 不存在或缺 yaml 头")
    else:
        if ev.get("candidate_tree") != tree:
            rep.add("evidence", "FAIL", "evidence 头 candidate_tree 与候选不一致（证据过期）")
        if ev.get("all-passed") is not True:
            rep.add("evidence", "FAIL", "evidence 头 all-passed 不是 true")

    conf = yaml_head(integ / "conformance-feature.md")
    if str(conf.get("verdict") or "") != "conform":
        rep.add("conformance", "FAIL", f"feature 级覆盖核对 verdict={conf.get('verdict') or '<缺>'}，须 conform")

    results = load_json(integ / "business-results.json")
    _check_results(rep, fdir, results, plan, cand, args.phase)

    if plan:
        expected_report = render_report(fdir, integ, cand, plan)
        report_path = integ / "acceptance-report.md"
        if not report_path.is_file():
            rep.add("report", "FAIL", "acceptance-report.md 不存在：自动阶段后先生成报告，再进入人工或合并")
        elif report_path.read_text(encoding="utf-8") != expected_report:
            rep.add("report", "FAIL", "acceptance-report.md 与当前计划、结果或人工确认不一致：重新运行 report，不得手改")

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
        note = f"{cand.get('integration_branch')}@{head[:8]}；验收报告已生成"
        append_event(fdir, event, tree=tree[:12], note=note)
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
        rep.add("candidate", "FAIL", "integration/candidate.json 不存在：先 freeze")
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
    append_event(fdir, "feature-merged", commit=commit[:12], note=f"{cand['integration_branch']} → {cfg['main-branch']}")
    rep.add("merge", "PASS", f"已登记合并 {commit[:8]}")
    return rep


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
    pv = sub.add_parser("validate-plan", help="校验功能验收计划")
    pv.add_argument("--feature-dir", required=True, help="主工作区 dev/features/<feature-module>/<feature-slug>")
    pv.add_argument("--json", help="结果 JSON 写入路径")
    for name in ("freeze", "check", "record-merge", "report"):
        p = sub.add_parser(name)
        p.add_argument("--feature-dir", required=True, help="主工作区 dev/features/<feature-module>/<feature-slug>")
        p.add_argument("--round", help="同一 feature 目录下的一轮需求名：产物写 integration/<round>/，集成分支按 <round> 代入")
        p.add_argument("--repo", required=True, help="freeze / check / report：集成 worktree；record-merge：主工作区")
        if name == "freeze":
            p.add_argument("--issues", help="--round 时本轮票清单，逗号分隔（如 issue-031,issue-039）")
        if name != "report":
            p.add_argument("--json", help="结果 JSON 写入路径")
        if name == "check":
            p.add_argument("--phase", choices=["verify", "merge"], required=True)
            p.add_argument("--record", action="store_true", help="通过时追加 feature-verified / feature-confirmed 事件")
            p.add_argument("--main-repo", help="merge 相：主工作区路径，核对其当前在 main-branch")
        if name == "record-merge":
            p.add_argument("--commit", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "target":
            return target(args)
        if args.cmd == "validate-plan":
            rep = validate_plan(args)
        elif args.cmd == "report":
            return report(args)
        else:
            fn = {"freeze": freeze, "check": check, "record-merge": record_merge}[args.cmd]
            rep = fn(args)
    except InputError as e:
        print(f"feature-closeout: 输入错误：{e}", file=sys.stderr)
        return 2
    title = args.cmd + (f" --phase {args.phase}" if args.cmd == "check" else "")
    return _emit(rep, title, getattr(args, "json", None))


if __name__ == "__main__":
    sys.exit(main())
