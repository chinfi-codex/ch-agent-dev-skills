#!/usr/bin/env python3
"""需求收口（feature-closeout: local-human）的确定性门禁。

  validate-plan  校验功能验收计划：执行方式与最终判定分离，人工原因可解释
  freeze         全票 merged/done + main 已同步 + 验收计划有效 → 冻结代码、票组与计划
  check verify   候选未变 + feature 评审 + 候选复跑 + conformance + 自动证据 + AC 对账 + 当前报告
  check merge    verify 全部项 + 人工结果 / 豁免 + 合并确认绑定同一候选
  report         从冻结计划与实际结果生成 acceptance-report.md，不接受手写摘要代替
  finalize       feature-merged → accepted/done + 逐票/feature close-and-clean
  record_merge  内部 API：仅 delivery.promote 以同进程内存授权调用
  target         打印票的目标分支

产物一律在主工作区 <feature-dir>/integration/，不写集成 worktree、不提交到集成分支。
退出码：0 = 通过；1 = 有 FAIL；2 = 输入 / 配置错误。只依赖 Python 3.8+ 标准库。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_scope
from process_check import (  # noqa: E402  共享解析与 git 辅助，不另写第二份
    InputError, Report, git, parse_yaml_subset, read_first_yaml_block, read_frontmatter,
    md_section, _table_rows, _evidence_pointer_problem, _placeholder,
)

CANDIDATE_SCHEMA = "agent-dev.feature-candidate/v2"
CANDIDATE_VERIFICATION_SCHEMA = "agent-dev.candidate-verification/v1"
CANDIDATE_VERIFICATION_SCHEMA_PATH = (Path(__file__).resolve().parent.parent / "references"
                                      / "candidate-verification.schema.json")
FEATURE_REVIEW_RECEIPT = "feature-review-receipt.json"
REVIEW_HANDOFF = "review-handoff.json"
REVIEW_HANDOFF_SCHEMA = "agent-dev.review-handoff/v1"
PHASE_TIMING_KIND = "feature-phase-timing"
PHASES = ("review", "candidate-verification", "conformance", "acceptance", "report")
CANDIDATE_VERIFICATION = "candidate-verification.json"
FEATURE_CONFORMANCE = "conformance-feature.md"
PLAN_SCHEMA = "agent-dev.feature-acceptance/v2"
RESULTS_SCHEMA = "agent-dev.feature-business-results/v2"
EXECUTORS = ("command", "browser-agent", "human")
ORACLES = ("auto", "auto+human-review", "human")
ORACLE_RANK = {"auto": 0, "auto+human-review": 1, "human": 2}
HUMAN_REASONS = (
    "intent", "visual-ux", "domain-judgment", "high-risk", "no-reliable-oracle",
    "environment-unavailable", "evidence-insufficient",
)
JOURNAL = "progress.jsonl"
MODES = ("server-enforced", "cooperative-ci", "local-human", "pr")
PR_EVIDENCE = "pr.json"
_MERGE_CAPABILITY = object()


# ---------- 读取 ----------

def repo_root_of(feature_dir: Path) -> Path:
    for p in feature_dir.resolve().parents:
        if (p / "dev" / "agents-config.md").is_file():
            return p
    raise InputError(f"{feature_dir} 上溯找不到 dev/agents-config.md（--feature-dir 须指向主工作区 dev/features/<m>/<slug>）")


def load_config(feature_dir: Path) -> dict:
    """Use the strict v2 parser; legacy ci-promote/drift configuration is invalid."""
    try:
        ac = importlib.import_module("agents_config")
        return ac.load(repo_root_of(feature_dir) / "dev" / "agents-config.md")
    except (ImportError, ValueError, OSError) as exc:
        raise InputError(f"agents-config v2 无效：{exc}")


def feature_mode(config: dict) -> str:
    return str((config.get("feature-closeout") or {}).get("mode") or "")


def integration_branch(config: dict, slug: str) -> str:
    fc = config.get("feature-closeout") or {}
    return str(fc.get("integration-branch") or "feat/<feature-slug>").replace("<feature-slug>", slug)


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
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        return json.loads(text, object_pairs_hook=_unique_json_object,
                          parse_constant=_reject_json_constant)
    return parse_yaml_subset(text)


def validate_plan_data(plan: dict) -> list:
    errors = []
    if plan.get("schema") != PLAN_SCHEMA:
        errors.append(f"schema 应为 {PLAN_SCHEMA}")
    documents = plan.get("documents")
    if not isinstance(documents, dict) or not documents:
        errors.append("documents 必须登记需求来源文档")
        documents = {}
    for alias, document in documents.items():
        if alias not in ("prd", "change_request"):
            errors.append(f"documents.{alias} 不是受支持的来源别名")
            continue
        if not isinstance(document, dict) or not document.get("path") or not re.fullmatch(
                r"[0-9a-f]{64}", str(document.get("sha256") or "")):
            errors.append(f"documents.{alias} 必须含 path 与 64 位 sha256")
    cases = plan.get("cases")
    if not isinstance(cases, dict) or not cases:
        return errors + ["cases 必须是以 AC 编号为键的非空映射"]
    for aid, case in cases.items():
        prefix = f"{aid}："
        if not re.fullmatch(r"AC-(?:D)?[0-9]+", str(aid)):
            errors.append(prefix + "编号须为 AC-<数字> 或 AC-D<数字>")
        if not isinstance(case, dict):
            errors.append(prefix + "内容必须是映射")
            continue
        for key in ("title", "source", "expected"):
            if not case.get(key):
                errors.append(prefix + f"缺 {key}")
        source = case.get("source")
        if not isinstance(source, dict) or source.get("document") not in documents:
            errors.append(prefix + "source.document 必须引用 documents 中已登记的来源")
        elif source.get("anchor") != aid:
            errors.append(prefix + "case key 必须等于 source.anchor")
        if not isinstance(case.get("required"), bool):
            errors.append(prefix + "required 必须显式为 true / false")
        executor = case.get("executor")
        oracle = case.get("oracle")
        if executor not in EXECUTORS:
            errors.append(prefix + f"executor={executor!r} 非法（{' / '.join(EXECUTORS)}）")
        if oracle not in ORACLES:
            errors.append(prefix + f"oracle={oracle!r} 非法（{' / '.join(ORACLES)}）")
        steps = case.get("steps")
        if not isinstance(steps, list) or not steps or any(not isinstance(step, str) or not step.strip() for step in steps):
            errors.append(prefix + "steps 必须是非空字符串列表")
        for list_key in ("preconditions", "evidence"):
            values = case.get(list_key)
            if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item.strip() for item in values):
                errors.append(prefix + f"{list_key} 必须是非空字符串列表")
        if "human_steps" in case and case.get("human_steps") is not None and not isinstance(case.get("human_steps"), list):
            errors.append(prefix + "human_steps 必须是列表")
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
            if not isinstance(case.get("human_expect"), str) or not case.get("human_expect").strip():
                errors.append(prefix + "人工 / 混合判定必须写非空 human_expect")
        elif case.get("human_reason") or case.get("human_expect"):
            errors.append(prefix + "auto 不需要 human_reason / human_expect")
        if executor == "browser-agent" and "screenshot" not in (case.get("evidence") or []):
            errors.append(prefix + "浏览器验收的 evidence 必须包含 screenshot")
    return errors


def _ac_ids(document: Path, heading: str) -> set:
    text = document.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^###\s+%s\s*$\n(.*?)(?=^###\s+|^##\s+|\Z)" % re.escape(heading), text)
    if not match:
        raise InputError(f"{document.name} 缺少 ### {heading}")
    identifiers = re.findall(r"(?m)^\s*[-*]\s+(AC-(?:D)?[0-9]+)\s*[：:]", match.group(1))
    if len(set(identifiers)) != len(identifiers):
        raise InputError(f"{document.name} 的 {heading} 存在重复 AC")
    return set(identifiers)


def validate_plan_sources(feature_dir: Path, plan: dict) -> list:
    """Reconcile plan cases with real, hash-bound PRD / change-request AC sets."""
    errors = []
    try:
        repo = repo_root_of(feature_dir)
    except InputError as exc:
        return [str(exc)]
    documents = plan.get("documents") or {}
    cases = plan.get("cases") or {}
    if artifact_scope.campaign_id(feature_dir):
        spec = str(plan.get("tech_spec") or "")
        if not spec or Path(spec).name != spec or not (feature_dir / spec).is_file():
            errors.append("campaign plan must name an existing tech-spec in its own directory")
    docs_root = (repo / "docs" / "features" / artifact_scope.feature_key(feature_dir)).resolve()
    resolved = {}
    for alias, item in documents.items():
        try:
            value = item.get("path")
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                raise InputError(f"documents.{alias}.path 必须是当前 feature 的仓库相对路径")
            path = (repo / relative).resolve()
            path.relative_to(docs_root)
            if not path.is_file():
                raise InputError(f"documents.{alias}.path 不存在：{value}")
            if sha256_file(path) != item.get("sha256"):
                raise InputError(f"documents.{alias}.sha256 与当前文档不一致：{value}")
            resolved[alias] = path
        except (InputError, OSError, ValueError) as exc:
            errors.append(str(exc))
    if errors:
        return errors
    try:
        prd_ids = _ac_ids(resolved["prd"], "端到端验收场景") if "prd" in resolved else set()
        if "change_request" not in resolved:
            expected = prd_ids
            for aid, case in cases.items():
                if (case.get("source") or {}).get("document") != "prd":
                    errors.append(f"{aid}：标准路径只能引用 prd")
        else:
            change_path = resolved["change_request"]
            delta_ids = _ac_ids(change_path, "Delta 验收场景")
            frontmatter = read_frontmatter(change_path)
            upstream_prd = str((frontmatter.get("upstream") or {}).get("prd") or "")
            if upstream_prd and "prd" not in resolved:
                errors.append("change-request 声明 upstream.prd，但 documents.prd 未登记")
            if "prd" in resolved and (not upstream_prd or Path(upstream_prd).name != resolved["prd"].name):
                errors.append("change-request upstream.prd 与 documents.prd.path 不一致")
            if artifact_scope.campaign_id(feature_dir) and "prd" in resolved and upstream_prd:
                declared_prd = (repo / upstream_prd if upstream_prd.startswith("docs/")
                                else change_path.parent / upstream_prd).resolve()
                if declared_prd != resolved["prd"]:
                    errors.append("campaign change-request upstream.prd must bind the exact documents.prd.path")
            baseline = frontmatter.get("baseline_ac") or {}
            if not isinstance(baseline, dict) or any(key not in baseline for key in ("retain", "replace", "retire")):
                errors.append("baseline_ac.retain/replace/retire 必须全部显式声明")
                return errors
            retain = baseline.get("retain") or []
            replace = baseline.get("replace") or {}
            retire = baseline.get("retire") or []
            if not isinstance(retain, list) or not isinstance(replace, dict) or not isinstance(retire, list):
                errors.append("baseline_ac.retain/replace/retire 类型必须为 list/dict/list")
                return errors
            retain = [str(value) for value in retain]
            retire = [str(value) for value in retire]
            replace = {str(key): str(value) for key, value in replace.items()}
            for old in sorted(set(retain) | set(replace) | set(retire)):
                if old not in prd_ids:
                    errors.append(f"baseline_ac 引用了不存在的 PRD AC：{old}")
            groups = [set(retain), set(replace), set(retire)]
            for index, left in enumerate(groups):
                for right in groups[index + 1:]:
                    overlap = left & right
                    if overlap:
                        errors.append("baseline_ac 处置互斥冲突：" + ", ".join(sorted(overlap)))
            for old, target in replace.items():
                if target not in delta_ids:
                    errors.append(f"baseline_ac.replace 目标不是实际 delta AC：{old} -> {target}")
            if "prd" not in resolved and any((retain, replace, retire)):
                errors.append("无 baseline PRD 时 retain/replace/retire 必须全部为空")
            expected = set(delta_ids) | set(retain)
            for aid, case in cases.items():
                source = case.get("source") or {}
                document = source.get("document")
                if document == "change_request" and aid not in delta_ids:
                    errors.append(f"{aid}：change-request 中不存在该 delta AC")
                if document == "prd" and aid not in retain:
                    errors.append(f"{aid}：baseline PRD AC 未声明 retain")
        if set(cases) != set(expected):
            errors.append("AC 覆盖不完整：缺 " + ", ".join(sorted(set(expected) - set(cases)))
                          + "；额外 " + ", ".join(sorted(set(cases) - set(expected))))
    except (InputError, OSError, ValueError) as exc:
        errors.append(str(exc))
    return errors


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_json_constant(value):
    raise InputError("non-finite JSON constant: %s" % value)


def _event_identity(event):
    found = [event[key] for key in ("event_id", "event-id", "operation_id", "operation-id", "id") if key in event]
    if not found or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in found):
        raise InputError("legacy/invalid event without a stable trimmed event_id requires explicit migration")
    if len(set(found)) != 1:
        raise InputError("event contains conflicting event/operation IDs")
    return found[0]


def load_progress_events(feature_dir: Path):
    """Strict pre-merge reader matching state_store.append_event_once semantics."""
    path = feature_dir / JOURNAL
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise InputError("progress.jsonl 不可读：%s" % exc)
    if text and not text.endswith("\n"):
        raise InputError("progress.jsonl 最后一条事件是半行；合并前必须显式修复")
    events, identifiers = [], set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise InputError("progress.jsonl:%d 是空行/坏行" % number)
        try:
            event = json.loads(line, object_pairs_hook=_unique_json_object,
                               parse_constant=_reject_json_constant)
        except (ValueError, InputError) as exc:
            raise InputError("progress.jsonl:%d 损坏：%s" % (number, exc))
        if not isinstance(event, dict):
            raise InputError("progress.jsonl:%d 不是事件对象" % number)
        identifier = _event_identity(event)
        if identifier in identifiers:
            raise InputError("progress.jsonl 含重复 event_id：%s" % identifier)
        identifiers.add(identifier)
        events.append(event)
    return events


def _strict_datetime(value, label="datetime"):
    text = str(value or "").strip()
    if not text:
        raise InputError("%s 缺失" % label)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
            return datetime.strptime(text, "%Y-%m-%d %H:%M")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError("%s 不是合法日期时间：%r" % (label, text))
    return parsed


def _progress_ts(value=None):
    if value is None:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    parsed = _strict_datetime(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


def _event_host(config):
    hosts = ((config.get("dispatch") or {}).get("hosts") or {})
    return os.environ.get("AGENT_HOST") or (next(iter(hosts)) if len(hosts) == 1 else "main-session")


def last_merged(feature_dir: Path) -> dict:
    out = {}
    for ev in load_progress_events(feature_dir):
        if ev.get("event") == "merged" and ev.get("issue"):
            out[ev["issue"]] = ev.get("commit")
    return out


def append_event(feature_dir: Path, event: str, **fields):
    event_id = fields.pop("event_id", None)
    if not event_id:
        raise InputError("新进度事件必须提供稳定 event_id")
    ev = {"ts": _progress_ts(fields.pop("ts", None)), "event": event, "issue": "feature",
          "event_id": event_id}
    ev.update({k: v for k, v in fields.items() if v not in (None, "")})
    try:
        store = importlib.import_module("state_store")
        return store.append_event_once(feature_dir / JOURNAL, ev, event_id)
    except (ImportError, ValueError, OSError) as exc:
        raise InputError("无法写入严格进度事件：%s" % exc)


def _atomic_json(path: Path, value: dict):
    try:
        store = importlib.import_module("state_store")
        store.atomic_json_write(path, value)
    except (ImportError, ValueError, OSError) as exc:
        raise InputError(f"无法原子写入 {path}：{exc}")


def _active_decision_records(feature_dir: Path, issue_id=None):
    """Consume human_decision lazily, with a read-only fail-closed file guard.

    The file guard also covers recovery before the optional decision service has
    loaded.  It never resolves or rewrites a decision.
    """
    found = []
    try:
        hd = importlib.import_module("human_decision")
    except ImportError:
        hd = None
    if hd is not None:
        try:
            feature = artifact_scope.scope_key(feature_dir)
            root = repo_root_of(feature_dir)
            if issue_id is not None and hasattr(hd, "active_for_issue"):
                value = hd.active_for_issue(root, feature, issue_id)
                if value:
                    found.append(value)
            elif hasattr(hd, "active_for_feature"):
                found.extend(hd.active_for_feature(root, feature))
            else:
                for name in ("active_decisions", "list_active"):
                    fn = getattr(hd, name, None)
                    if fn is None:
                        continue
                    try:
                        values = fn(feature_dir, issue_id=issue_id)
                    except TypeError:
                        values = fn(feature_dir)
                    if values:
                        found.extend(values if isinstance(values, list) else [values])
                    break
        except (ValueError, OSError) as exc:
            raise InputError(f"人工决策状态不可可靠读取，fail closed：{exc}")
    for path in sorted((feature_dir / "decisions").glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict) or item.get("schema") != "ch-agent.human-decision/v1":
                continue
            if item.get("status") not in ("pending", "waiting", "answered", "routing", "expired"):
                continue
            scope = item.get("scope") or {}
            feature = artifact_scope.scope_key(feature_dir)
            if scope.get("feature") is not None and artifact_scope.decision_key(scope) != feature:
                continue
            if issue_id is not None and scope.get("issue") not in (None, issue_id):
                continue
            found.append(item)
    unique = {}
    for item in found:
        if isinstance(item, dict):
            unique[str(item.get("id") or id(item))] = item
    return list(unique.values())


def check_no_active_decisions(rep: Report, feature_dir: Path, issue_id=None):
    active = _active_decision_records(feature_dir, issue_id)
    for item in active:
        rep.add("human-decision", "FAIL", f"活动人工决策 {item.get('id') or '<unknown>'} 尚未解决；不得 freeze / merge")


def _idempotent_merged_event(feature_dir: Path, event: dict):
    try:
        store = importlib.import_module("state_store")
        return store.append_event_once(feature_dir / JOURNAL, event, event["event_id"])
    except (ImportError, ValueError, OSError) as exc:
        raise InputError(f"无法登记幂等 merged 事件：{exc}")


def integ_dir(args) -> Path:
    fdir = Path(args.feature_dir)
    base = fdir / "integration"
    round_name = getattr(args, "round", None)
    if round_name:
        relative = Path(str(round_name))
        if relative.is_absolute() or len(relative.parts) != 1 or str(round_name) in (".", ".."):
            raise InputError("--round 必须是单个安全目录名")
        base = base / relative
    try:
        base.resolve().relative_to(fdir.resolve())
    except ValueError:
        raise InputError("integration 路径不得逃逸 feature 目录")
    return base


def head_info(repo: Path):
    head = git(repo, "rev-parse", "HEAD").strip()
    return head, git(repo, "rev-parse", "HEAD^{tree}").strip()


# ---------- candidate phase timing (append-only digest events) ----------


def _phase_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _phase_time(value, label):
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label} 缺 RFC3339 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{label} 不是 RFC3339 时间：{value!r}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InputError(f"{label} 必须含时区")
    return parsed


def _phase_event_id(candidate, round_name, phase, action):
    path = integ_dir(SimpleNamespace(feature_dir=str(candidate["feature_dir"]), round=round_name)) / "candidate.json"
    binding = {
        "candidate": candidate["candidate"], "candidate_tree": candidate["candidate_tree"],
        "candidate_sha256": candidate["candidate_sha256"], "round": round_name,
        "phase": phase, "action": action,
    }
    material = json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    event_id = "feature-phase:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    return event_id, path


def _phase_candidate(feature_dir: Path, repo: Path, round_name):
    integ = integ_dir(SimpleNamespace(feature_dir=str(feature_dir), round=round_name))
    cand = _json_object(integ / "candidate.json", missing=None)
    if not cand:
        raise InputError("integration/candidate.json 不存在：先 freeze")
    candidate = _oid(cand.get("candidate"), "candidate.candidate")
    tree = _oid(cand.get("candidate_tree"), "candidate.candidate_tree")
    if cand.get("round") != round_name:
        raise InputError("candidate.round 与请求 round 不一致")
    head, head_tree = head_info(repo)
    if head != candidate or head_tree != tree:
        raise InputError("当前 HEAD/tree 已漂移，不得记录或重放旧 candidate 阶段计时")
    cand = dict(cand)
    cand["candidate_sha256"] = sha256_file(integ / "candidate.json")
    cand["feature_dir"] = str(feature_dir)
    return cand


def _matching_phase_events(events, cand, round_name, phase):
    matched = []
    for event in events:
        if event.get("kind") != PHASE_TIMING_KIND:
            continue
        if (event.get("candidate") != cand.get("candidate")
                or event.get("candidate_tree") != cand.get("candidate_tree")
                or event.get("candidate_sha256") != cand.get("candidate_sha256")
                or event.get("round") != round_name or event.get("phase") != phase):
            continue
        if event.get("event") != "digest" or event.get("issue") != "feature":
            raise InputError("feature-phase-timing 必须是 issue=feature 的 digest 事件")
        matched.append(event)
    return matched


def _seconds(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise InputError(f"{label} 必须是非负秒数")
    return float(value)


def _finish_duration(start, finish):
    started_at = _phase_time(start.get("started_at"), "phase started_at")
    ended_at = _phase_time(finish.get("ended_at"), "phase ended_at")
    if finish.get("started_at") != start.get("started_at"):
        raise InputError("phase finish.started_at 与 start 不一致")
    elapsed = (ended_at - started_at).total_seconds()
    if elapsed < 0:
        raise InputError("phase ended_at 早于 started_at")
    recorded = _seconds(finish.get("duration_seconds"), "phase duration_seconds")
    if abs(recorded - elapsed) > 0.000001:
        raise InputError("phase duration_seconds 与起止时间不一致")
    return recorded


def _validate_running_phase(start):
    if start.get("state") != "running" or start.get("action") != "start":
        raise InputError("阶段 start 事件 state/action 非法")
    started = _phase_time(start.get("started_at"), "phase started_at")
    if start.get("ended_at") is not None or _seconds(start.get("duration_seconds"), "phase duration_seconds") != 0:
        raise InputError("阶段 running 事件时间字段非法")
    return started


def record_phase(feature_dir: Path, repo: Path, phase: str, action: str, round=None):
    """Record/replay one candidate-bound start or finish without resetting time."""
    feature_dir, repo = Path(feature_dir), Path(repo)
    if phase not in PHASES:
        raise InputError("phase 必须是 " + "/".join(PHASES))
    if action not in ("start", "finish"):
        raise InputError("phase action 必须是 start/finish")
    cand = _phase_candidate(feature_dir, repo, round)
    events = load_progress_events(feature_dir)
    matched = _matching_phase_events(events, cand, round, phase)
    start_id, candidate_path = _phase_event_id(cand, round, phase, "start")
    finish_id, finish_path = _phase_event_id(cand, round, phase, "finish")
    if finish_path != candidate_path:
        raise InputError("阶段计时 candidate 路径不一致")
    by_id = {event.get("event_id"): event for event in matched}
    if len(by_id) != len(matched):
        raise InputError("阶段计时事件缺稳定 event_id")
    allowed = {start_id, finish_id}
    unexpected = sorted(set(by_id) - allowed)
    if unexpected:
        raise InputError("阶段计时 event_id 与 candidate/tree/round/phase 不匹配：" + ", ".join(unexpected))
    start = by_id.get(start_id)
    finish = by_id.get(finish_id)
    if start is not None:
        _validate_running_phase(start)
    if finish is not None and (finish.get("state") != "finished" or finish.get("action") != "finish"):
        raise InputError("阶段 finish 事件 state/action 非法")

    if action == "start" and start is not None:
        return start
    if action == "finish":
        if start is None:
            raise InputError("finish 须已有同 candidate/tree/round/phase 的 start")
        _phase_time(start.get("started_at"), "phase started_at")
        if finish is not None:
            _finish_duration(start, finish)
            return finish

    if sha256_file(candidate_path) != cand["candidate_sha256"]:
        raise InputError("candidate.json 字节在计时期间漂移，拒绝追加旧候选事件")
    timestamp = _phase_now()
    if action == "start":
        event = {
            "event_id": start_id, "ts": timestamp, "event": "digest", "issue": "feature",
            "kind": PHASE_TIMING_KIND, "candidate": cand["candidate"],
            "candidate_tree": cand["candidate_tree"], "candidate_sha256": cand["candidate_sha256"],
            "round": round, "phase": phase,
            "state": "running", "action": "start", "started_at": timestamp,
            "ended_at": None, "duration_seconds": 0,
        }
    else:
        elapsed = (_phase_time(timestamp, "phase ended_at")
                   - _phase_time(start["started_at"], "phase started_at")).total_seconds()
        if elapsed < 0:
            raise InputError("当前时间早于阶段 started_at，拒绝记录负耗时")
        event = {
            "event_id": finish_id, "ts": timestamp, "event": "digest", "issue": "feature",
            "kind": PHASE_TIMING_KIND, "candidate": cand["candidate"],
            "candidate_tree": cand["candidate_tree"], "candidate_sha256": cand["candidate_sha256"],
            "round": round, "phase": phase,
            "state": "finished", "action": "finish", "started_at": start["started_at"],
            "ended_at": timestamp, "duration_seconds": elapsed,
        }
    try:
        store = importlib.import_module("state_store")
        store.append_event_once(feature_dir / JOURNAL, event, event["event_id"])
    except (ImportError, ValueError, OSError) as exc:
        raise InputError(f"无法追加阶段计时事件：{exc}")
    return event


def phase_timing_summary(feature_dir: Path, cand: dict, round=None):
    """Return only real running/finished rows for the current candidate."""
    if round is None:
        round = cand.get("round")
    for key in ("candidate", "candidate_tree"):
        _oid(cand.get(key), "candidate." + key)
    feature_dir = Path(feature_dir)
    candidate_path = integ_dir(SimpleNamespace(
        feature_dir=str(feature_dir), round=round)) / "candidate.json"
    candidate_sha256 = sha256_file(candidate_path)
    events = load_progress_events(feature_dir)
    cand = dict(cand)
    cand["candidate_sha256"] = candidate_sha256
    cand["feature_dir"] = str(feature_dir)
    rows = []
    for phase in PHASES:
        matched = _matching_phase_events(events, cand, round, phase)
        start_id, _ = _phase_event_id(cand, round, phase, "start")
        finish_id, _ = _phase_event_id(cand, round, phase, "finish")
        by_id = {event.get("event_id"): event for event in matched}
        if len(by_id) != len(matched) or set(by_id) - {start_id, finish_id}:
            raise InputError(f"{phase} 阶段计时 event_id 不完整或不匹配")
        start, finish = by_id.get(start_id), by_id.get(finish_id)
        if start is None:
            if finish is not None:
                raise InputError(f"{phase} 有 finish 但缺 start")
            continue
        started_at = _validate_running_phase(start)
        row = {"phase": phase, "state": "running", "started_at": start["started_at"],
               "ended_at": None, "duration_seconds": 0.0}
        if finish is not None:
            if finish.get("state") != "finished" or finish.get("action") != "finish":
                raise InputError(f"{phase} finish state/action 非法")
            row.update({"state": "finished", "ended_at": finish.get("ended_at"),
                        "duration_seconds": _finish_duration(start, finish)})
        # Keep this parse in the summary path too: a no-finish row still has a
        # real timezone-aware start, not an arbitrary display string.
        row["started_at"] = started_at.isoformat().replace("+00:00", "Z")
        rows.append(row)
    return rows


def _phase_seconds_text(value):
    seconds = float(value)
    if seconds.is_integer():
        return str(int(seconds))
    return ("%.6f" % seconds).rstrip("0").rstrip(".")


# ---------- 验收计划 ----------

def validate_plan(args) -> Report:
    rep = Report()
    try:
        plan = read_plan(Path(args.feature_dir))
    except InputError as exc:
        rep.add("plan", "FAIL", str(exc))
        return rep
    errors = validate_plan_data(plan)
    if not errors:
        errors = validate_plan_sources(Path(args.feature_dir), plan)
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
    try:
        plan = read_plan(feature_dir)
        for error in validate_plan_data(plan) + validate_plan_sources(feature_dir, plan):
            rep.add("plan-source", "FAIL", error)
    except InputError as exc:
        rep.add("plan-source", "FAIL", str(exc))




def _json_object(path: Path, *, missing=None):
    """Strict JSON object reader for review materials (duplicate keys are corruption)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return missing
    except (OSError, UnicodeError) as exc:
        raise InputError(f"无法读取 {path}：{exc}")
    try:
        value = json.loads(text, object_pairs_hook=_unique_json_object,
                           parse_constant=_reject_json_constant)
    except (ValueError, InputError) as exc:
        raise InputError(f"{path} 不是合法 JSON：{exc}")
    if not isinstance(value, dict):
        raise InputError(f"{path} 顶层必须是 JSON object")
    return value


def _oid(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise InputError(f"{label} 必须是完整小写 Git OID")
    return value


def _review_base(cand):
    return _oid(cand.get("review_base") or cand.get("main_head"), "candidate review_base")


def _diff_facts(repo: Path, base: str, head: str):
    try:
        rr = importlib.import_module("review_receipt")
        return {"base": base, "candidate": head,
                "sha256": rr._feature_diff_sha(repo, base, head),
                "files": rr._changed_paths(repo, base, head)}
    except (ImportError, ValueError, OSError) as exc:
        raise InputError(f"无法计算评审 diff {base}..{head}：{exc}")


def _repo_artifact(path: Path, root: Path):
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise InputError(f"输入路径不存在或逃逸主仓库：{path}（{exc}）")
    if path.is_symlink() or not resolved.is_file():
        raise InputError(f"输入必须是主工作区普通文件：{path}")
    return {"path": resolved.relative_to(root.resolve()).as_posix(),
            "sha256": sha256_file(resolved)}


def _current_handoff_inputs(fdir: Path, cand: dict):
    """Hash only main-workspace inputs, never a candidate checkout's dev/ copy."""
    root = repo_root_of(fdir)
    try:
        tc = importlib.import_module("ticket_context")
    except ImportError as exc:
        raise InputError(f"ticket_context 不可用：{exc}")
    wanted = {str(item.get("id")) for item in cand.get("issues") or []}
    specs = {}
    seen = set()
    for issue in sorted((fdir / "issues").glob("issue-*.md")):
        fm = read_frontmatter(issue)
        match = re.match(r"issue-[0-9]+[a-z]?", issue.stem)
        iid = str(fm.get("issue-id") or (match.group(0) if match else issue.stem))
        if iid not in wanted:
            continue
        if iid in seen:
            raise InputError(f"重复票号：{iid}")
        seen.add(iid)
        try:
            spec = tc.find_spec(issue, root)
        except (ValueError, OSError) as exc:
            raise InputError(f"无法定位 {iid} 的 tech-spec：{exc}")
        artifact = _repo_artifact(spec, root)
        specs[artifact["path"]] = artifact
    if seen != wanted:
        raise InputError("candidate 票文件不完整：" + ", ".join(sorted(wanted - seen)))
    return {
        "spec": [specs[path] for path in sorted(specs)],
        "config": _repo_artifact(root / "dev" / "agents-config.md", root),
        "plan": _repo_artifact(plan_source(fdir), root),
    }


def _validate_previous_value(previous):
    if previous is None:
        return
    if not isinstance(previous, dict):
        raise InputError("旧 review-handoff previous 必须是 object/null")
    for key in ("candidate", "candidate_tree", "main_head"):
        _oid(previous.get(key), "handoff previous." + key)
    if not isinstance(previous.get("plan_sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", previous["plan_sha256"]):
        raise InputError("handoff previous.plan_sha256 非法")
    issues = previous.get("issues")
    if not isinstance(issues, list) or any(not isinstance(item, dict) or not item.get("id") for item in issues):
        raise InputError("handoff previous.issues 非法")
    if len({item["id"] for item in issues}) != len(issues):
        raise InputError("handoff previous.issues 含重复票")
    if previous.get("inputs") != "unknown" and not isinstance(previous.get("inputs"), dict):
        raise InputError("handoff previous.inputs 必须是 object/unknown")
    receipt = previous.get("receipt")
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise InputError("handoff previous.receipt 必须是 object/null")
        if not isinstance(receipt.get("findings"), list):
            raise InputError("handoff previous.receipt.findings 必须是 list")
        identifiers = []
        for finding in receipt["findings"]:
            if not isinstance(finding, dict) or not isinstance(finding.get("id"), str) or not finding["id"]:
                raise InputError("handoff 旧 finding 缺有效 id")
            identifiers.append(finding["id"])
        if len(set(identifiers)) != len(identifiers):
            raise InputError("handoff 旧 finding id 重复")


def _load_old_handoff(integ: Path, old: dict):
    value = _json_object(integ / REVIEW_HANDOFF, missing=None)
    if value is None:
        return None
    if value.get("schema") != REVIEW_HANDOFF_SCHEMA:
        raise InputError("旧 review-handoff schema 不匹配")
    if not old:
        raise InputError("旧 review-handoff 没有对应 candidate.json")
    if (value.get("candidate") != old.get("candidate")
            or value.get("candidate_tree") != old.get("candidate_tree")
            or value.get("review_base") != _review_base(old)):
        raise InputError("旧 review-handoff 与 candidate.json 绑定不一致")
    _validate_previous_value(value.get("previous"))
    if isinstance(value.get("previous"), dict) and value["previous"].get("candidate") == old.get("candidate"):
        raise InputError("旧 review-handoff 不得把自身 candidate 当作 previous")
    if not isinstance(value.get("inputs"), dict):
        raise InputError("旧 review-handoff.inputs 必须是 object")
    return value


def _stable_receipt_artifact(artifact, integ: Path, root: Path, repo: Path):
    if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
        raise InputError("旧 receipt report 指针非法")
    digest = artifact.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise InputError("旧 receipt report sha256 非法")
    raw = Path(artifact["path"])
    source = raw if raw.is_absolute() else integ / raw
    if source.is_symlink() or not source.is_file():
        raise InputError("旧 receipt report 必须存在且为普通文件")
    resolved = source.resolve()
    if sha256_file(resolved) != digest:
        raise InputError("旧 receipt report 的实际 SHA256 与回执不一致")
    for base in (root.resolve(), repo.resolve()):
        try:
            relative = resolved.relative_to(base)
            return {"path": relative.as_posix(), "sha256": digest}
        except ValueError:
            continue
    raise InputError("旧 receipt report 指针逃逸 feature 主仓库与 candidate repo")


def _capture_previous_receipt(integ: Path, old: dict, root: Path, repo: Path):
    path = integ / FEATURE_REVIEW_RECEIPT
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise InputError("旧 feature-review-receipt.json 必须是普通文件")
    receipt = _json_object(path)
    try:
        rr = importlib.import_module("review_receipt")
        errors = rr._feature_schema_errors(receipt)
    except (ImportError, ValueError, OSError) as exc:
        raise InputError(f"旧 feature receipt schema 不可用：{exc}")
    if errors:
        raise InputError("旧 feature receipt 无效：" + "; ".join(errors))
    expected_base = _review_base(old)
    if (receipt.get("head") != old.get("candidate") or receipt.get("tree") != old.get("candidate_tree")
            or receipt.get("base") != expected_base
            or receipt.get("plan_sha256") != (old.get("acceptance_plan") or {}).get("sha256")):
        raise InputError("旧 feature receipt 与旧 candidate 的 candidate/tree/base/plan 不一致")
    report = receipt["artifacts"]["report"]
    if report.get("sha256") != receipt["report_sha256"]:
        raise InputError("旧 feature receipt report 哈希字段不一致")
    return {
        "path": FEATURE_REVIEW_RECEIPT,
        "sha256": sha256_file(path),
        "round": receipt["round"],
        "previous_receipt_sha256": receipt["previous_receipt_sha256"],
        "reviewer": copy.deepcopy(receipt["reviewer"]),
        "findings": copy.deepcopy(receipt["findings"]),
        "report": _stable_receipt_artifact(report, integ, root, repo),
    }


def _previous_candidate(old: dict):
    for key in ("candidate", "candidate_tree", "main_head"):
        _oid(old.get(key), "old candidate." + key)
    plan_sha = (old.get("acceptance_plan") or {}).get("sha256")
    if not isinstance(plan_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_sha):
        raise InputError("old candidate acceptance_plan.sha256 非法")
    issues = old.get("issues")
    if not isinstance(issues, list) or any(not isinstance(item, dict) or not item.get("id") for item in issues):
        raise InputError("old candidate.issues 非法")
    return {
        "candidate": old["candidate"], "candidate_tree": old["candidate_tree"],
        "main_head": old["main_head"], "plan_sha256": plan_sha,
        "issues": copy.deepcopy(issues),
    }


def _ticket_changes(previous_issues, current_issues):
    before = {str(item.get("id")): item for item in previous_issues}
    after = {str(item.get("id")): item for item in current_issues}
    if len(before) != len(previous_issues) or len(after) != len(current_issues):
        raise InputError("candidate 票组含重复/缺失 issue id")
    changed = [{"id": iid, "previous": copy.deepcopy(before[iid]), "current": copy.deepcopy(after[iid])}
               for iid in sorted(before.keys() & after.keys()) if before[iid] != after[iid]]
    return {"added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()), "changed": changed}


def _repair_artifact(fdir: Path, path: Path):
    try:
        relative = path.relative_to(fdir).as_posix()
    except ValueError:
        raise InputError(f"修复票证据路径逃逸 feature：{path}")
    if path.is_symlink():
        raise InputError(f"修复票证据不得为软链：{relative}")
    if not path.exists():
        return {"path": relative, "sha256": None, "status": "missing"}
    if not path.is_file():
        raise InputError(f"修复票证据不是普通文件：{relative}")
    resolved = path.resolve()
    try:
        resolved.relative_to(fdir.resolve())
    except ValueError:
        raise InputError(f"修复票证据解析后逃逸 feature：{relative}")
    return {"path": relative, "sha256": sha256_file(resolved), "status": "present"}


def _current_repair_manifest(fdir: Path, impl: Path, issue_id: str):
    raw = impl / "raw"
    if raw.is_symlink():
        raise InputError(f"{issue_id} artifact raw 不得为软链")
    currents = []
    if raw.is_dir():
        for path in sorted(raw.glob("*/" + GENERATION_MANIFEST)):
            try:
                path.resolve().relative_to(impl.resolve())
            except ValueError:
                raise InputError(f"{issue_id} artifact manifest 逃逸 impl 目录")
            value = _json_object(path)
            if value.get("subject") == issue_id and value.get("status") == "current":
                currents.append(path)
    if len(currents) > 1:
        raise InputError(f"{issue_id} 有多个 current artifact manifest")
    if not currents:
        return {"path": None, "sha256": None, "status": "missing"}
    return _repair_artifact(fdir, currents[0])


def _declared_review_findings(frontmatter: dict, issue: Path):
    if "review-findings" not in frontmatter:
        return []
    values = frontmatter["review-findings"]
    if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise InputError(f"{issue} review-findings 必须是非空 finding id 字符串列表")
    values = sorted(value.strip() for value in values)
    if len(set(values)) != len(values):
        raise InputError(f"{issue} review-findings 不允许重复 finding id")
    return values


def _repair_records(fdir: Path, cand: dict, added):
    current = {str(item.get("id")): item for item in cand.get("issues") or []}
    records = []
    for issue in sorted((fdir / "issues").glob("issue-*.md")):
        fm = read_frontmatter(issue)
        match = re.match(r"issue-[0-9]+[a-z]?", issue.stem)
        iid = str(fm.get("issue-id") or (match.group(0) if match else issue.stem))
        declared = _declared_review_findings(fm, issue)
        if iid not in added:
            continue
        if iid not in current:
            raise InputError(f"新增修复票 {iid} 不在 candidate 中")
        impl = fdir / "impl" / iid
        try:
            impl.resolve().relative_to(fdir.resolve())
        except ValueError:
            raise InputError(f"{iid} impl 路径逃逸 feature")
        records.append({
            "issue": iid,
            "path": issue.relative_to(fdir).as_posix(),
            "review_findings": declared,
            "merged_commit": current[iid].get("merged_commit"),
            "ticket_context": _repair_artifact(fdir, impl / "ticket-context.json"),
            "process_check_ticket": _repair_artifact(fdir, impl / "process-check-ticket.json"),
            "verification_implementer": _repair_artifact(fdir, impl / "verification-implementer.json"),
            "artifact_manifest": _current_repair_manifest(fdir, impl, iid),
        })
    if {record["issue"] for record in records} != set(added):
        raise InputError("新增修复票文件不完整")
    return records


def _input_changes(current, previous):
    if previous is None or previous.get("inputs") == "unknown":
        return {key: "unknown" for key in current}
    old = previous.get("inputs")
    if not isinstance(old, dict):
        raise InputError("previous.inputs 非法")
    return {key: ("unchanged" if old.get(key) == value else "changed")
            for key, value in current.items()}


def build_review_handoff(fdir: Path, repo: Path, integ: Path, old: dict,
                         old_handoff: dict, cand: dict) -> dict:
    """Build deterministic review-location material; never a review verdict."""
    current_inputs = _current_handoff_inputs(fdir, cand)
    previous = None
    if old:
        if old.get("candidate") == cand.get("candidate"):
            # Re-freezing the same candidate must not create a self-transition.
            previous = copy.deepcopy(old_handoff.get("previous")) if old_handoff else None
        else:
            previous = _previous_candidate(old)
            previous["receipt"] = _capture_previous_receipt(
                integ, old, repo_root_of(fdir), repo)
            previous["inputs"] = (copy.deepcopy(old_handoff["inputs"])
                                  if old_handoff else "unknown")
    _validate_previous_value(previous)
    if previous and previous["candidate"] == cand.get("candidate"):
        raise InputError("review handoff previous 不得等于当前 candidate")

    previous_issues = previous["issues"] if previous else []
    changes = _ticket_changes(previous_issues, cand.get("issues") or []) if previous else None
    if previous:
        previous["incremental_diff"] = _diff_facts(repo, previous["candidate"], cand["candidate"])
        previous["ticket_changes"] = changes
    added = changes["added"] if changes else []
    repairs = _repair_records(fdir, cand, added)
    findings = ((previous.get("receipt") or {}).get("findings") or []) if previous else []
    known_findings = {finding["id"] for finding in findings}
    for repair in repairs:
        unknown = sorted(set(repair["review_findings"]) - known_findings)
        if unknown:
            raise InputError(f"{repair['issue']} review-findings 引用上轮未知 finding：" + ", ".join(unknown))
    closure_rows = []
    for finding in sorted(findings, key=lambda item: item["id"]):
        linked = [copy.deepcopy(repair) for repair in repairs
                  if finding["id"] in repair["review_findings"]]
        closure_rows.append({"finding": copy.deepcopy(finding), "repairs": linked})

    if old and old.get("candidate") == cand.get("candidate") and old_handoff:
        # A same-commit refreeze can still change spec/config/plan. Unchanged
        # inputs retain the prior transition byte-for-byte; real changes compare
        # with the immediately preceding freeze, not an older code candidate.
        if old_handoff["inputs"] == current_inputs and isinstance(old_handoff.get("input_changes"), dict):
            input_changes = copy.deepcopy(old_handoff["input_changes"])
        else:
            input_changes = _input_changes(current_inputs, {"inputs": old_handoff["inputs"]})
    else:
        input_changes = _input_changes(current_inputs, previous)
    return {
        "schema": REVIEW_HANDOFF_SCHEMA,
        "purpose": "review-location-only",
        "feature": cand.get("feature"), "round": cand.get("round"),
        "integration_branch": cand.get("integration_branch"),
        "candidate": _oid(cand.get("candidate"), "candidate"),
        "candidate_tree": _oid(cand.get("candidate_tree"), "candidate_tree"),
        "review_base": _review_base(cand),
        "main_head": _oid(cand.get("main_head"), "candidate.main_head"),
        "plan_sha256": (cand.get("acceptance_plan") or {}).get("sha256"),
        "issues": copy.deepcopy(cand.get("issues") or []),
        "diff": _diff_facts(repo, _review_base(cand), cand["candidate"]),
        "inputs": current_inputs,
        "input_changes": input_changes,
        "previous": previous,
        "repairs": repairs,
        "closure_rows": closure_rows,
    }


def _atomic_freeze_artifacts(integ: Path, plan: dict, cand: dict, handoff: dict):
    """Keep candidate/handoff untouched if any part of a freeze write fails."""
    integ.mkdir(parents=True, exist_ok=True)
    entries = ((integ / "acceptance-plan.json", plan),
               (integ / "candidate.json", cand),
               (integ / REVIEW_HANDOFF, handoff))
    snapshots = {}
    for path, _ in entries:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise InputError(f"freeze 产物路径不是普通文件：{path}")
        snapshots[path] = path.read_bytes() if path.exists() else None
    try:
        for path, value in entries:
            _atomic_json(path, value)
    except Exception as exc:
        rollback_errors = []
        for path, data in snapshots.items():
            try:
                if data is None:
                    if path.exists():
                        path.unlink()
                else:
                    _module_state_store().atomic_write(path, data, mode=0o600)
            except (OSError, ValueError) as rollback:
                rollback_errors.append(f"{path}: {rollback}")
        if rollback_errors:
            raise InputError(f"freeze 写入失败且回滚不完整：{exc}；{'；'.join(rollback_errors)}")
        if isinstance(exc, InputError):
            raise
        raise InputError(f"freeze 产物写入失败，已回滚：{exc}")


# ---------- freeze ----------


def _check_arch_rules(rep: Report, repo: Path, head: str):
    """Run the Python ARCH-CHECK on a frozen candidate and bind rules/guide hashes."""
    try:
        import arch_check
    except ImportError as exc:
        rep.add("arch-check", "FAIL", f"arch-check unavailable: {exc}")
        return None
    rules_rel = arch_check.RULES_REL
    guide_rel = arch_check.GUIDE_REL
    try:
        raw = arch_check.read_blob(repo, head, rules_rel)
        if raw is None:
            rep.add("arch-check", "INFO", f"{rules_rel} 不存在；arch-check 跳过")
            return None
        rules = arch_check._load_rules_bytes(raw, rules_rel)
        errors = arch_check.validate_rules(rules)
        if errors:
            for error in errors:
                rep.add("arch-check", "FAIL", error)
            return None
        findings, infos, _ = arch_check.check_repo(repo, head, rules=rules)
        for finding in findings:
            level = {"error": "FAIL", "warn": "WARN", "info": "INFO"}.get(finding["severity"], "INFO")
            rep.add("arch-check", level, "[%s] %s:%d — %s\n  %s" % (
                finding["rule"], finding["path"], finding["line"], finding["message"],
                finding["remediation"].replace("\n", "\n  ")))
        for info in infos:
            rep.add("arch-check", "INFO", info)
        for error in arch_check.render_guide_check(repo, head, rules):
            rep.add("architect-guide", "FAIL", error)
        guide = arch_check.render_guide(rules)
        return {
            "rules": rules_rel,
            "rules_sha256": arch_check._sha256(raw),
            "guide": guide_rel,
            "guide_sha256": arch_check._sha256(guide),
        }
    except (InputError, ValueError, OSError, TypeError) as exc:
        rep.add("arch-check", "FAIL", f"arch-check failed: {exc}")
        return None


def freeze(args) -> Report:
    rep = Report()
    fdir = Path(args.feature_dir)
    repo = Path(args.repo)
    cfg = load_config(fdir)
    mode = feature_mode(cfg)
    integ = integ_dir(args)
    if mode not in MODES:
        rep.add("config", "FAIL", f"feature-closeout.mode={mode!r} 非法")

    try:
        plan = read_plan(fdir)
        plan_errors = validate_plan_data(plan)
        if not plan_errors:
            plan_errors = validate_plan_sources(fdir, plan)
    except InputError as exc:
        plan, plan_errors = {}, [str(exc)]
    for error in plan_errors:
        rep.add("plan", "FAIL", error)

    if (artifact_scope.campaign_id(fdir)
            and "<feature-slug>" not in str((cfg.get("feature-closeout") or {}).get("integration-branch") or "feat/<feature-slug>")):
        rep.add("config", "FAIL", "campaign integration-branch must contain <feature-slug> for isolation")
    slug = artifact_scope.branch_key(fdir) if artifact_scope.campaign_id(fdir) else (args.round or fdir.name)
    requested = None
    if args.issues is not None:
        tokens = [item.strip() for item in str(args.issues).split(",")]
        if not tokens or any(not item for item in tokens):
            rep.add("issues", "FAIL", "--issues 必须是非空逗号分隔票号集合，不接受空集合或空项")
            tokens = []
        if len(set(tokens)) != len(tokens):
            rep.add("issues", "FAIL", "--issues 不允许重复票号")
        requested = set(tokens)
    if args.round and requested is None:
        rep.add("round", "FAIL", "--round 须配非空 --issues 指明本轮票（同一 feature 目录下多轮需求各自收口）")
    scope = requested
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    expected = integration_branch(cfg, slug)
    if branch != expected:
        rep.add("branch", "FAIL", f"集成 worktree 当前分支 {branch} ≠ 约定 {expected}（每需求使用独立 feature candidate）")

    dirty = [l for l in git(repo, "status", "--porcelain").splitlines() if l.strip()]
    if dirty:
        rep.add("worktree", "FAIL", f"集成 worktree 有未提交或未跟踪改动 {len(dirty)} 处；冻结前先提交、清理或还原")

    issues = []
    merged = last_merged(fdir)
    files = sorted((fdir / "issues").glob("issue-*.md"))
    if not files:
        rep.add("issues", "FAIL", f"{fdir / 'issues'} 下没有票文件")
    available = set()
    for f in files:
        fm = read_frontmatter(f)
        m = re.match(r"issue-[0-9]+[a-z]?", f.stem)
        iid = str(fm.get("issue-id") or (m.group(0) if m else f.stem))
        if str(fm.get("status") or "") != "deferred":
            available.add(iid)
    if not available:
        rep.add("issues", "FAIL", "没有可冻结的非 deferred 票；全过滤/空集合不得形成 candidate")
    if requested is not None and requested != available:
        unknown = sorted(requested - available)
        missing = sorted(available - requested)
        if unknown:
            rep.add("issues", "FAIL", "--issues 含未知或 deferred 票：" + ", ".join(unknown))
        if missing:
            rep.add("issues", "FAIL", "--issues 请求集合不完整，遗漏：" + ", ".join(missing))
    scope = available if requested is None else requested
    for f in files:
        fm = read_frontmatter(f)
        m = re.match(r"issue-[0-9]+[a-z]?", f.stem)
        iid = str(fm.get("issue-id") or (m.group(0) if m else f.stem))
        status = str(fm.get("status") or "")
        if status == "deferred" or (scope and iid not in scope):
            continue
        merged_commit = merged.get(iid)
        ticket_slug = str(fm.get("feature-slug") or "").strip() or fdir.name
        try:
            artifact_scope.validate_ticket(fdir, fm)
        except ValueError as exc:
            rep.add("issues", "FAIL", f"{iid}: {exc}")
        if not artifact_scope.campaign_id(fdir) and ticket_slug != slug:
            in_main = False
            if isinstance(merged_commit, str) and re.fullmatch(r"[0-9a-f]{40}", merged_commit):
                try:
                    in_main = subprocess_run_ancestor(repo, merged_commit, cfg["main-branch"])
                except InputError:
                    in_main = False
            if not in_main:
                rep.add("issues", "FAIL",
                        f"{iid} feature-slug={ticket_slug} ≠ 本轮 round/目录名 {slug}；"
                        "本轮票必须携带与本轮一致的 feature-slug（复用旧 feature 目录时换新 round 名，"
                        "建议 <feature-slug>-<YYYY-MM-DD>），仅已在 main 的旧需求票可保留旧 slug")
        issues.append({"id": iid, "status": status, "merged_commit": merged_commit})
        if status not in ("merged", "done"):
            rep.add("issues", "FAIL", f"{iid} status={status or '<空>'}，须全部 merged（已入集成分支）或 done；"
                                         "评审、复跑与符合性统一在 feature 候选门执行")
        if not isinstance(merged_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", merged_commit):
            rep.add("issues", "FAIL", f"{iid} 缺完整 40 位 merged commit；不得用占位或短哈希冻结")
        else:
            try:
                actual = git(repo, "rev-parse", "--verify", merged_commit + "^{commit}").strip()
                ancestor = subprocess_run_ancestor(repo, merged_commit, "HEAD")
                if actual != merged_commit or not ancestor:
                    rep.add("issues", "FAIL", f"{iid} merged commit {merged_commit[:12]} 不是当前 candidate 的祖先")
            except InputError as exc:
                rep.add("issues", "FAIL", f"{iid} merged commit 不可用：{exc}")
        check_no_active_decisions(rep, fdir, iid)
    check_no_active_decisions(rep, fdir)
    candidate_head, _ = head_info(repo)
    arch_check_meta = _check_arch_rules(rep, repo, candidate_head)

    main = cfg["main-branch"]
    main_head = git(repo, "rev-parse", main).strip()
    head, tree = head_info(repo)
    merge_base = git(repo, "merge-base", main, "HEAD").strip()
    stack_base, predecessor_tip = None, None
    if artifact_scope.stack_declared(fdir):
        if cfg.get("tracker") != "local" or mode != "local-human":
            rep.add("stack-base", "FAIL", "development stacking requires local-human local delivery")
        else:
            try:
                main_root = repo_root_of(fdir)
                stack_base, predecessor_tip = artifact_scope.checked_stack_base(
                    main_root, fdir, target=branch, main_branch=main)
            except (ValueError, OSError) as exc:
                rep.add("stack-base", "FAIL", str(exc))
        if stack_base is not None:
            review_base = stack_base["commit"]
            review_merge_base = git(repo, "merge-base", review_base, "HEAD").strip()
            if (review_merge_base != review_base
                    or not subprocess_run_ancestor(repo, review_base, head)):
                rep.add("stack-base", "FAIL", "stacked candidate must descend exactly from the pinned predecessor")
    elif merge_base != main_head:
        rep.add("main-sync", "FAIL", f"{main} 尚未同步进集成分支（merge-base {merge_base[:8]} ≠ {main} {main_head[:8]}）；"
                                     f"先在集成 worktree git merge {main}")

    if rep.failed:
        return rep
    old = _json_object(integ / "candidate.json", missing={})
    old_handoff = _load_old_handoff(integ, old)
    digest = sha256_file(plan_source(fdir))
    cand = {
        "schema": CANDIDATE_SCHEMA, "feature": artifact_scope.scope_key(fdir), "round": args.round,
        "mode": mode, "integration_branch": branch, "candidate_ref": "refs/heads/" + branch,
        "main_branch": main, "main_head": main_head, "merge_base": merge_base,
        "candidate": head, "candidate_tree": tree, "frozen_at": datetime.now(timezone.utc).isoformat(),
        "issues": issues,
        "arch_check": arch_check_meta,
        "acceptance_plan": {
            "path": "acceptance-plan.yaml", "sha256": digest, "cases": sorted(plan["cases"].keys()),
        },
    }
    if stack_base is not None:
        cand["stack_base"] = {
            "schema": artifact_scope.STACK_BASE_SCHEMA,
            "predecessor_scope": stack_base["predecessor_scope"],
            "predecessor_ref": stack_base["predecessor_ref"],
            "commit": stack_base["commit"],
            "tree": stack_base["tree"],
            "source_path": stack_base["source_path"],
            "source_sha256": stack_base["source_sha256"],
            "declaration_sha256": stack_base["declaration_sha256"],
            "predecessor_tip_at_freeze": predecessor_tip,
        }
        cand["review_base"] = stack_base["commit"]
        cand["review_merge_base"] = git(repo, "merge-base", stack_base["commit"], "HEAD").strip()
    if old.get("candidate_tree") and old["candidate_tree"] != tree:
        cand["supersedes"] = old["candidate_tree"]
        rep.add("candidate", "PASS", f"新候选取代旧 tree {old['candidate_tree'][:8]}：旧证据与人工确认全部失效，新候选全部重跑")
    old_plan = (old.get("acceptance_plan") or {}).get("sha256")
    if old_plan and old_plan != digest:
        rep.add("plan", "PASS", "验收计划已换版：旧业务结果、人工确认与报告全部失效")
    handoff = build_review_handoff(fdir, repo, integ, old, old_handoff, cand)
    _atomic_freeze_artifacts(integ, plan, cand, handoff)
    rep.add("candidate", "PASS", f"已冻结 {branch}@{head[:8]} tree {tree[:8]}（{len(issues)} 票，{len(plan['cases'])} 条 AC，mode={mode}）")
    return rep


def subprocess_run_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    import subprocess
    result = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
                            capture_output=True)
    if result.returncode not in (0, 1):
        raise InputError("git merge-base --is-ancestor 失败：" + result.stderr.decode("utf-8", errors="replace"))
    return result.returncode == 0


GENERATION_MANIFEST = "artifact-generation-manifest.json"
GENERATION_SCHEMA = "agent-dev.artifact-generation/v2"
GENERATION_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "artifact-generation.schema.json"


def load_generation_manifest(path: Path, verify=True):
    value = load_json(path)
    if not value:
        raise InputError("artifact generation manifest 不存在：%s" % path)
    try:
        js = importlib.import_module("json_schema")
        errors = js.load_validate(GENERATION_SCHEMA_PATH, value)
    except (ImportError, ValueError, OSError) as exc:
        raise InputError("artifact generation schema 不可用：%s" % exc)
    if errors:
        raise InputError("artifact generation manifest 无效：" + "; ".join(errors))
    if verify:
        for artifact in value["artifacts"]:
            target = path.parent / artifact["path"]
            if not target.is_file() or target.is_symlink():
                raise InputError("manifest 产物缺失或为软链：%s" % target)
            if sha256_file(target) != artifact["sha256"] or target.stat().st_size != artifact["size"]:
                raise InputError("manifest 产物 hash/size 不匹配：%s" % target)
    return value


def manifest_facts(integ: Path):
    """Return deterministic manifest facts, without inventing verdict semantics."""
    facts = []
    for path in sorted((integ / "raw").glob("*/" + GENERATION_MANIFEST)):
        value = load_generation_manifest(path)
        facts.append({"subject": value["subject"], "generation": value["generation"],
                      "candidate_tree": value["candidate_tree"], "status": value["status"],
                      "supersedes": value.get("supersedes"),
                      "artifacts": [{key: artifact[key] for key in ("path", "kind", "sha256", "size")}
                                    for artifact in value["artifacts"]]})
    currents = {}
    for fact in facts:
        if fact["status"] == "current":
            currents.setdefault(fact["subject"], []).append(fact["generation"])
    ambiguous = {subject: generations for subject, generations in currents.items() if len(generations) > 1}
    if ambiguous:
        raise InputError("同一 subject 有多个 current artifact generations：%s" % ambiguous)
    return facts


def _check_generation_artifact(fdir: Path, aid: str, artifact: dict, path: Path, cand: dict):
    generation = artifact.get("generation")
    if not isinstance(generation, str) or not generation or generation in (".", "..") or "/" in generation or "\\" in generation:
        raise InputError("%s 产物必须绑定安全 generation：%s" % (aid, generation))
    integ = None
    relative_to_feature = path.resolve().relative_to(fdir.resolve())
    parts = relative_to_feature.parts
    if len(parts) < 4 or parts[0] != "integration" or "raw" not in parts:
        raise InputError("%s 产物不在 integration/[round]/raw/<generation> 下" % aid)
    raw_index = parts.index("raw")
    if raw_index + 2 >= len(parts) or parts[raw_index + 1] != generation:
        raise InputError("%s 产物路径与 generation 不一致" % aid)
    integ = fdir.joinpath(*parts[:raw_index])
    manifest_path = integ / "raw" / generation / GENERATION_MANIFEST
    manifest = load_generation_manifest(manifest_path)
    if manifest["generation"] != generation or manifest["candidate_tree"] != cand.get("candidate_tree"):
        raise InputError("%s generation/candidate_tree 与 manifest 不一致" % aid)
    if manifest["status"] != "current":
        raise InputError("%s 使用的 artifact generation 不是 current" % aid)
    try:
        relative = path.resolve().relative_to(manifest_path.parent.resolve()).as_posix()
    except ValueError:
        raise InputError("%s 产物逃逸出其 generation 目录" % aid)
    expected = {"path": relative, "kind": artifact.get("type"),
                "sha256": artifact["sha256"], "size": path.stat().st_size}
    if expected not in manifest["artifacts"]:
        raise InputError("%s 产物未以 path/kind/hash/size 登记进 generation manifest" % aid)


# ---------- 业务结果 ----------

def _check_artifacts(rep: Report, fdir: Path, aid: str, artifacts, cand: dict) -> bool:
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
        if artifact.get("candidate") != cand.get("candidate") or artifact.get("candidate_tree") != cand.get("candidate_tree"):
            rep.add("evidence", "FAIL", f"{aid} 产物未绑定当前 artifact generation candidate / tree：{artifact.get('path')}")
        rel = Path(str(artifact["path"]))
        if rel.is_absolute() or ".." in rel.parts:
            rep.add("evidence", "FAIL", f"{aid} 产物路径必须相对需求目录且不得含 ..：{rel}")
            continue
        path = fdir / rel
        if not path.is_file() or path.is_symlink():
            rep.add("evidence", "FAIL", f"{aid} 产物不存在或为软链：{rel}")
            continue
        if sha256_file(path) != str(artifact["sha256"]).lower():
            rep.add("evidence", "FAIL", f"{aid} 产物哈希不匹配：{rel}")
            continue
        try:
            _check_generation_artifact(fdir, aid, artifact, path, cand)
        except InputError as exc:
            rep.add("evidence", "FAIL", str(exc))
            continue
        if artifact.get("type") == "screenshot":
            screenshot = True
    return screenshot


def _check_auto_pass(rep: Report, fdir: Path, item: dict, cand: dict, spec: dict):
    aid = item.get("id") or "<无 id>"
    executor = item.get("executor")
    execution = item.get("execution")
    if not isinstance(execution, dict):
        rep.add("evidence", "FAIL", f"{aid} 自动通过但缺 execution 实际执行记录")
    elif execution.get("executor") != executor:
        rep.add("evidence", "FAIL", f"{aid} execution.executor 与有效执行方式不一致")
    else:
        if execution.get("candidate") != cand.get("candidate") or execution.get("candidate_tree") != cand.get("candidate_tree"):
            rep.add("evidence", "FAIL", f"{aid} execution 未绑定当前 candidate commit / tree")
        if executor == "command":
            if not execution.get("command") or execution.get("command") != spec.get("command"):
                rep.add("evidence", "FAIL", f"{aid} 命令验收必须执行冻结计划中的原命令")
            if execution.get("generator") != "verify_issue":
                rep.add("evidence", "FAIL", f"{aid} command 业务 AC 必须由 verify_issue 命令执行辅助生成")
            if execution.get("exit") != 0 or isinstance(execution.get("exit"), bool) or execution.get("started") is not True:
                rep.add("evidence", "FAIL", f"{aid} 命令验收通过须真实启动并记录 exit: 0")
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
    screenshot = _check_artifacts(rep, fdir, aid, item.get("artifacts"), cand)
    if executor == "command" and not any(isinstance(a, dict) and a.get("type") == "raw" for a in item.get("artifacts") or []):
        rep.add("evidence", "FAIL", f"{aid} command 通过至少须有一个 candidate-bound raw 产物")
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
    if results.get("candidate") != cand.get("candidate") or results.get("candidate_tree") != cand.get("candidate_tree"):
        rep.add("business", "FAIL", "business-results 绑定的 candidate / candidate_tree 与候选不一致（证据过期）")
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
            _check_auto_pass(rep, fdir, it, cand, spec)
        else:
            rep.add("business", "FAIL", f"{aid} 自动部分 status={status!r}，须 pass / fail / unverified")
        if status != "pass" and it.get("artifacts"):
            _check_artifacts(rep, fdir, aid, it.get("artifacts"), cand)
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


def _report_decision(plan: dict, results: dict, evidence: dict, conformance: dict, cand: dict, confirmation: dict,
                     material_invalid: bool, review: dict = None, candidate_verification: dict = None) -> str:
    items = _result_map(results)
    review = review or {}
    candidate_verification = candidate_verification or {}
    blocking = (material_invalid or evidence.get("all-passed") is not True
                or conformance.get("verdict") != "conform"
                or review.get("verdict") not in ("pass", "pass-with-notes")
                or candidate_verification.get("all-passed") is not True)
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
    # Report bytes must remain stable when confirmation is added: the human
    # confirmation binds this exact report hash.  Confirmation is rendered as a
    # pending authorization checklist, not folded back into a hash cycle.
    if waived:
        suffix = f"，但有 {waived} 项人工豁免"
    else:
        suffix = ""
    waiting = ("，等待人的合并授权" if cand.get("mode") == "local-human"
               else "，等待用户合并 PR" if cand.get("mode") == "pr"
               else "，等待在线 CI 证明与晋级核对")
    return "验收条件已满足" + suffix + waiting


def _source_label(source) -> str:
    if isinstance(source, dict):
        return "%s#%s" % (source.get("document"), source.get("anchor"))
    return str(source or "—")


def render_report(fdir: Path, integ: Path, cand: dict, plan: dict) -> str:
    results = load_json(integ / "business-results.json")
    validation = Report()
    _check_results(validation, fdir, results, plan, cand, "verify")
    material_invalid = any(i["level"] == "FAIL" and i["check"] != "coverage" for i in validation.items)
    evidence = yaml_head(integ / "evidence.md")
    conformance = yaml_head(integ / "conformance-feature.md")
    confirmation = yaml_head(integ / "confirmation.md")
    review = load_json(integ / FEATURE_REVIEW_RECEIPT)
    candidate_verification = load_json(integ / CANDIDATE_VERIFICATION)
    items = _result_map(results)
    cases = plan.get("cases") or {}
    environment = results.get("environment") or {}
    generation_facts = manifest_facts(integ)
    current_generations = sorted((fact["subject"], fact["generation"]) for fact in generation_facts
                                 if fact["status"] == "current")
    generation_text = "、".join("%s@%s" % item for item in current_generations) or "—"
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
        f"- Artifact generations：{_md(generation_text)}",
        f"- 关联自动检查：{'通过' if evidence.get('all-passed') is True else '未通过或未完成'}"
        f"（direct {ran.get('direct', '?')}、indirect {ran.get('indirect', '?')}、deferred {ran.get('deferred', '?')}、quarantined {ran.get('quarantined', '?')}）",
        f"- 整需求独立评审：{_md(review.get('verdict') or '未完成')}"
        f"（reviewer {_md((review.get('reviewer') or {}).get('host'))}/"
        f"{_md((review.get('reviewer') or {}).get('session'))}，findings {len(review.get('findings') or [])}）",
        f"- 候选 supervisor 复跑：{'全部通过' if candidate_verification.get('all-passed') is True else '未完成或有失败'}"
        f"（{sum(1 for item in candidate_verification.get('issues') or [] if item.get('pass') is True)}/"
        f"{len(candidate_verification.get('issues') or [])} 票）",
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
            f"| {aid} | {_md(_source_label(spec.get('source')))} | {_md(item.get('executor') or spec.get('executor'))} | {oracle} | {status} | "
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
        _report_decision(plan, results, evidence, conformance, cand, confirmation, material_invalid,
                         review, candidate_verification), "",
        "本报告只说明验收事实与建议，不代替人的合并、发布或 push 授权。", "",
        "想细看：integration/evidence.md、business-results.json、acceptance-plan.json、artifacts/。", "",
    ]
    timing_rows = phase_timing_summary(fdir, cand, cand.get("round"))
    if timing_rows:
        lines += ["", "## 阶段耗时", "",
                  "| 阶段 | 状态 | 开始（RFC3339） | 结束（RFC3339） | 秒数 |",
                  "|---|---|---|---|---:|"]
        for row in timing_rows:
            state = "已完成" if row["state"] == "finished" else "进行中（截至开始）"
            lines.append("| %s | %s | %s | %s | %s |" % (
                _md(row["phase"]), state, _md(row["started_at"]),
                _md(row["ended_at"]), _phase_seconds_text(row["duration_seconds"])))
        lines.append("")
    return "\n".join(lines)


def report(args) -> int:
    fdir = Path(args.feature_dir)
    integ = integ_dir(args)
    cand = load_json(integ / "candidate.json")
    if not cand:
        raise InputError("integration/candidate.json 不存在：先 freeze")
    head, tree = head_info(Path(args.repo))
    if head != cand.get("candidate") or tree != cand.get("candidate_tree"):
        raise InputError("集成分支 HEAD/tree 已变化：先重新 freeze，不能给新候选套用旧报告")
    cfg = load_config(fdir)
    if (git(Path(args.repo), "rev-parse", cfg["main-branch"]).strip() != cand.get("main_head")
            and not cand.get("stack_base")):
        raise InputError("main 在冻结后已前进：先同步并重新 freeze，不能沿用旧报告")
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

def _check_main_unchanged(rep: Report, repo: Path, cfg: dict, cand: dict, *, phase: str):
    main = cfg["main-branch"]
    try:
        main_now = git(repo, "rev-parse", main).strip()
    except InputError as exc:
        rep.add("main-drift", "FAIL", str(exc))
        return
    if cand.get("mode") == "pr" and phase == "merge":
        # A pr-mode merge phase runs after the user merged the PR, so main sits
        # on the merge commit by design; _check_pr_evidence binds that commit to
        # the candidate tree.  The verify phase keeps the local-human rule: a
        # candidate frozen on an older main must not be pushed for a PR.
        return
    if main_now != cand.get("main_head"):
        if cand.get("stack_base"):
            # Stacked candidates anchor review and safety to the pinned
            # predecessor; an independently advancing main never authorizes
            # merge-main for a stacked campaign.
            rep.add("main-drift", "PASS",
                    f"{main} 冻结后前进（{str(cand.get('main_head'))[:12]} → {main_now[:12]}）；"
                    "叠加候选按前置 pin 锚定，晋级保持拒绝")
            return
        rep.add("main-drift", "FAIL", f"{main} 冻结后前进（{str(cand.get('main_head'))[:12]} → {main_now[:12]}）；"
                                     "任何路径变化都须同步 main、重新 freeze 并重验，main-drift-ignore 不再放行")


def _check_stack_base(rep: Report, fdir: Path, repo: Path, cand: dict):
    """Stacked candidates stay pinned to their declared predecessor source."""
    stacked = cand.get("stack_base")
    declared = artifact_scope.stack_declared(fdir)
    if not stacked:
        if declared:
            rep.add("stack-base", "FAIL", "campaign declares a stack-base but the candidate lacks it；重新 freeze")
        return
    if not declared:
        rep.add("stack-base", "FAIL", "stack-base declaration was removed after freeze；不得绕过叠加验收屏障")
        return
    try:
        current, _tip = artifact_scope.checked_stack_base(
            repo_root_of(fdir), fdir, target=str(cand.get("integration_branch") or ""),
            main_branch=str(cand.get("main_branch") or ""))
    except (ValueError, OSError, InputError) as exc:
        rep.add("stack-base", "FAIL", str(exc))
        return
    if (current["commit"] != stacked.get("commit")
            or current["declaration_sha256"] != stacked.get("declaration_sha256")
            or current["source_sha256"] != stacked.get("source_sha256")):
        rep.add("stack-base", "FAIL", "stack-base 声明在冻结后漂移；重新 freeze 并重跑全部候选证据")
        return
    candidate = str(cand.get("candidate") or "")
    if not re.fullmatch(r"[a-f0-9]{40}", candidate) or not subprocess_run_ancestor(repo, stacked["commit"], candidate):
        rep.add("stack-base", "FAIL", "candidate 不再包含前置 pin，冻结候选已失效")
        return
    if (cand.get("review_base") != stacked["commit"]
            or cand.get("review_merge_base") != stacked["commit"]):
        rep.add("stack-base", "FAIL", "review base 必须恰为前置 pin；独立评审范围不得改锚")


def _check_ticket_states(rep: Report, fdir: Path, repo: Path, cand: dict):
    """Re-read ticket status and merged commits; candidate membership is not a claim."""
    wanted = {str(item.get("id")) for item in cand.get("issues") or []}
    records = {}
    for issue in sorted((fdir / "issues").glob("issue-*.md")):
        fm = read_frontmatter(issue)
        match = re.match(r"issue-[0-9]+[a-z]?", issue.stem)
        iid = str(fm.get("issue-id") or (match.group(0) if match else issue.stem))
        if iid in wanted:
            records[iid] = (issue, fm)
    merged = last_merged(fdir)
    candidate = str(cand.get("candidate") or "")
    for item in cand.get("issues") or []:
        iid = str(item.get("id"))
        if iid not in records:
            rep.add("ticket-state", "FAIL", f"{iid} 票文件已丢失，不得沿用冻结票组")
            continue
        _, fm = records[iid]
        status = str(fm.get("status") or "")
        if status not in ("merged", "done"):
            rep.add("ticket-state", "FAIL", f"{iid} status={status or '<空>'}，须为 merged（待 feature 验收）或 done")
        if item.get("status") is not None and status != item.get("status"):
            rep.add("ticket-state", "FAIL", f"{iid} status 在 freeze 后由 {item.get('status')} 变为 {status}；"
                                                 "候选票组已变化，重新 freeze")
        commit = item.get("merged_commit")
        if merged.get(iid) != commit:
            rep.add("ticket-state", "FAIL", f"{iid} merged 事件与冻结 commit 不一致或已丢失")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            continue
        try:
            if git(repo, "rev-parse", "--verify", commit + "^{commit}").strip() != commit:
                rep.add("ticket-state", "FAIL", f"{iid} merged commit {commit[:12]} 不是完整提交")
            elif not subprocess_run_ancestor(repo, commit, candidate):
                rep.add("ticket-state", "FAIL", f"{iid} merged commit {commit[:12]} 不是 candidate 的祖先")
        except InputError as exc:
            rep.add("ticket-state", "FAIL", f"{iid} merged commit 不可复核：{exc}")


def _check_feature_review(rep: Report, fdir: Path, integ: Path, repo: Path, cand: dict):
    path = integ / FEATURE_REVIEW_RECEIPT
    if not path.is_file():
        rep.add("feature-review", "FAIL", f"{path.name} 不存在：整需求独立评审须在 feature 候选上执行一次")
        return
    try:
        rr = importlib.import_module("review_receipt")
        errors = rr.validate_feature_receipt(fdir, repo, path, candidate=cand)
    except (ImportError, ValueError, OSError, TypeError) as exc:
        rep.add("feature-review", "FAIL", f"整需求评审 receipt 无法校验：{exc}")
        return
    rep.extend("feature-review", errors)
    if errors:
        return
    receipt = load_json(path)
    if receipt.get("verdict") not in ("pass", "pass-with-notes"):
        rep.add("feature-review", "FAIL", f"整需求评审 verdict={receipt.get('verdict') or '<缺>'}，须 pass / pass-with-notes")
    implementers = {str(item.get("session")) for item in receipt.get("implementers") or []}
    reviewer = str((receipt.get("reviewer") or {}).get("session") or "")
    if not reviewer or reviewer in implementers:
        rep.add("feature-review", "FAIL", "reviewer session 必须独立于全部票 implementer")


def _check_candidate_verification(rep: Report, integ: Path, cand: dict):
    path = integ / CANDIDATE_VERIFICATION
    if not path.is_file():
        rep.add("candidate-verification", "FAIL",
                f"{path.name} 不存在：feature 候选须对每票冻结 DoD+fast 复跑一次")
        return
    value = load_json(path)
    if not value:
        rep.add("candidate-verification", "FAIL", f"{path.name} 为空或不可读")
        return
    try:
        js = importlib.import_module("json_schema")
        errors = js.load_validate(CANDIDATE_VERIFICATION_SCHEMA_PATH, value)
    except (ImportError, ValueError, OSError) as exc:
        rep.add("candidate-verification", "FAIL", f"候选复跑 schema 不可用：{exc}")
        return
    for error in errors:
        rep.add("candidate-verification", "FAIL", error)
    if (value.get("candidate") != cand.get("candidate")
            or value.get("candidate_tree") != cand.get("candidate_tree")
            or value.get("main_head") != cand.get("main_head")
            or value.get("plan_sha256") != (cand.get("acceptance_plan") or {}).get("sha256")):
        rep.add("candidate-verification", "FAIL", "候选复跑绑定的 candidate/tree/main_head/plan 与冻结候选不一致")
    stacked = cand.get("stack_base")
    if stacked:
        reported = value.get("stack_base")
        if (not isinstance(reported, dict)
                or reported.get("commit") != stacked.get("commit")
                or reported.get("declaration_sha256") != stacked.get("declaration_sha256")):
            rep.add("candidate-verification", "FAIL", "候选复跑未绑定冻结候选的前置 pin")
    if value.get("all-passed") is not True:
        rep.add("candidate-verification", "FAIL", "候选复跑并非全部通过；任一失败不得进入合并")
    expected = {str(item.get("id")) for item in cand.get("issues") or []}
    entries = value.get("issues") if isinstance(value.get("issues"), list) else []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        iid = str(entry.get("issue") or "")
        if iid in seen:
            rep.add("candidate-verification", "FAIL", f"{iid} 候选复跑结果重复")
        seen.add(iid)
        if iid not in expected:
            rep.add("candidate-verification", "FAIL", f"{iid} 不在冻结票组，不得混入候选复跑")
            continue
        if entry.get("pass") is not True:
            rep.add("candidate-verification", "FAIL", f"{iid} 候选复跑未通过")
        if entry.get("head") != cand.get("candidate") or entry.get("tree") != cand.get("candidate_tree"):
            rep.add("candidate-verification", "FAIL", f"{iid} 复跑记录未绑定当前 candidate/tree")
        relative = entry.get("verification_path")
        digest = entry.get("verification_sha256")
        if not isinstance(relative, str) or not relative or not isinstance(digest, str):
            rep.add("candidate-verification", "FAIL", f"{iid} 缺原始 verification 路径或哈希")
            continue
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            rep.add("candidate-verification", "FAIL", f"{iid} verification 路径必须相对候选目录且不得含 ..")
            continue
        evidence = path.parent / rel
        if not evidence.is_file() or evidence.is_symlink():
            rep.add("candidate-verification", "FAIL", f"{iid} verification 证据不存在或为软链：{relative}")
            continue
        if sha256_file(evidence) != digest:
            rep.add("candidate-verification", "FAIL", f"{iid} verification 证据哈希不匹配：{relative}")
    missing = sorted(expected - seen)
    if missing:
        rep.add("candidate-verification", "FAIL", "候选复跑漏票：" + ", ".join(missing))


def _check_confirmation(rep: Report, integ: Path, cand: dict):
    c = yaml_head(integ / "confirmation.md")
    if not c:
        rep.add("confirmation", "FAIL", "integration/confirmation.md 不存在：人未显式确认不得合 main")
        return
    if c.get("scope") != "merge-main":
        rep.add("confirmation", "FAIL", "confirmation.scope 必须显式为 merge-main；deploy-prod / push 不随此授权")
    if not isinstance(c.get("conditions"), list) or c.get("conditions"):
        rep.add("confirmation", "FAIL", "confirmation.conditions 必须显式为 []；附条件确认不得合并")
    for key, expected, label in (
        ("candidate", cand.get("candidate"), "candidate"),
        ("candidate_tree", cand.get("candidate_tree"), "candidate tree"),
        ("main_head", cand.get("main_head"), "main HEAD"),
    ):
        if c.get(key) != expected:
            rep.add("confirmation", "FAIL", f"confirmation {label} 与候选不一致；候选/main 变化后须重新确认")
    for k in ("confirmed-by", "confirmed-at", "quote"):
        if not c.get(k):
            rep.add("confirmation", "FAIL", f"confirmation 缺 {k}（须落人的原话与时间）")
    if c.get("confirmed-at"):
        try:
            _strict_datetime(c["confirmed-at"], "confirmation.confirmed-at")
        except InputError as exc:
            rep.add("confirmation", "FAIL", str(exc))
    report_path = integ / "acceptance-report.md"
    results_path = integ / "business-results.json"
    if report_path.is_file() and c.get("report_sha256") != sha256_file(report_path):
        rep.add("confirmation", "FAIL", "confirmation.report_sha256 与当前验收报告不一致")
    if results_path.is_file() and c.get("business_results_sha256") != sha256_file(results_path):
        rep.add("confirmation", "FAIL", "confirmation.business_results_sha256 与当前业务结果不一致")
    for forbidden in ("deploy-prod", "deploy_prod", "push", "authorize-push", "authorize-deploy"):
        if c.get(forbidden):
            rep.add("confirmation", "FAIL", f"merge-main 不包含 {forbidden} 授权")


def _check_feature_conformance(rep: Report, fdir: Path, integ: Path, repo: Path, cand: dict):
    path = integ / FEATURE_CONFORMANCE
    conf = yaml_head(path)
    tree = cand.get("candidate_tree")
    if str(conf.get("verdict") or "") != "conform" or conf.get("candidate") != cand.get("candidate") or conf.get("candidate_tree") != tree:
        rep.add("conformance", "FAIL", "feature 级覆盖核对须 verdict=conform 且绑定当前 candidate / tree")
    if conf.get("issue") != "feature":
        rep.add("conformance", "FAIL", "feature conformance 的 issue 必须为 feature")
    for key in ("checker", "checker_session", "date"):
        if not conf.get(key):
            rep.add("conformance", "FAIL", f"feature conformance 缺 {key}")
    cfg = load_config(fdir)
    if conf.get("checker") and conf.get("checker") != (cfg.get("conformance") or {}).get("checker", "main-session"):
        rep.add("conformance", "FAIL", "feature conformance checker 与配置不一致")
    if conf.get("date"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(conf["date"])):
            rep.add("conformance", "FAIL", "feature conformance date 必须是 YYYY-MM-DD")
        try:
            datetime.fromisoformat(str(conf["date"])).date()
        except ValueError:
            rep.add("conformance", "FAIL", "feature conformance date 必须是合法 YYYY-MM-DD")
    wanted = {str(item.get("id")) for item in cand.get("issues") or []}
    coverage = conf.get("coverage") or {}
    if set(coverage.get("issues") or []) != wanted:
        rep.add("conformance-coverage", "FAIL", "coverage.issues 必须精确等于 candidate 冻结票组")
    expected_specs = {}
    try:
        import ticket_context
        primary = repo_root_of(fdir)
        for issue in sorted((fdir / "issues").glob("issue-*.md")):
            fm = read_frontmatter(issue)
            match = re.match(r"issue-[0-9]+[a-z]?", issue.stem)
            ident = str(fm.get("issue-id") or (match.group(0) if match else issue.stem))
            if ident in wanted:
                spec = ticket_context.find_spec(issue, primary)
                expected_specs[spec.relative_to(primary).as_posix()] = sha256_file(spec)
    except (ImportError, ValueError, OSError, TypeError) as exc:
        rep.add("conformance-coverage", "FAIL", f"无法展开 candidate spec 集合：{exc}")
        return
    actual_specs = {}
    for item in coverage.get("specs") or []:
        if isinstance(item, dict) and item.get("path"):
            actual_specs[str(item["path"])] = str(item.get("sha256") or "")
    if actual_specs != expected_specs:
        rep.add("conformance-coverage", "FAIL", "coverage.specs 必须精确覆盖 candidate 全部 tech-spec 及当前 SHA256")
    try:
        text = path.read_text(encoding="utf-8")
        rows = _table_rows(md_section(text, "Feature 级覆盖核对"))
    except (OSError, InputError) as exc:
        rep.add("conformance-coverage", "FAIL", str(exc))
        return
    if not rows:
        rep.add("conformance-coverage", "FAIL", "Feature 级覆盖核对表为空；只有 YAML verdict 不能通过")
    numbers = []
    covered_issues = set()
    for row in rows:
        if len(row) < 6:
            rep.add("conformance-coverage", "FAIL", "feature coverage row 缺编号/需求点/来源/issue/判定/证据")
            continue
        number, requirement, source, issue_id, decision, evidence = row[:6]
        if not re.fullmatch(r"[0-9]+", number):
            rep.add("conformance-coverage", "FAIL", f"feature coverage 编号非法：{number}")
        else:
            numbers.append(int(number))
        if _placeholder(requirement) or _placeholder(source) or "#" not in source:
            rep.add("conformance-coverage", "FAIL", f"feature coverage {number} 缺需求点或 spec 节锚点")
        if issue_id not in wanted:
            rep.add("conformance-coverage", "FAIL", f"feature coverage {number} 引用了 candidate 外票：{issue_id}")
            continue
        covered_issues.add(issue_id)
        if decision != "已实现":
            rep.add("conformance-coverage", "FAIL", f"feature coverage {number} 含未实现或语义偏差")
        problem = _evidence_pointer_problem(evidence, repo, fdir / "impl" / issue_id)
        if _placeholder(evidence) or problem:
            rep.add("conformance-coverage", "FAIL", f"feature coverage {number} 证据无效：{problem}")
    if numbers and numbers != list(range(1, len(rows) + 1)):
        rep.add("conformance-coverage", "FAIL", "feature coverage 编号必须连续且唯一")
    missing_issues = wanted - covered_issues
    if missing_issues:
        rep.add("conformance-coverage", "FAIL", "feature coverage 未覆盖全部 candidate 票：" + ", ".join(sorted(missing_issues)))


def check(args) -> Report:
    rep = Report()
    fdir = Path(args.feature_dir)
    repo = Path(args.repo)
    cfg = load_config(fdir)
    mode = feature_mode(cfg)
    integ = integ_dir(args)
    cand = load_json(integ / "candidate.json")
    if not cand:
        rep.add("candidate", "FAIL", "integration/candidate.json 不存在：先 freeze")
        return rep
    if cand.get("schema") != CANDIDATE_SCHEMA or cand.get("mode") != mode:
        rep.add("candidate", "FAIL", "candidate schema/mode 与当前配置不一致；禁止切换模式或自动降级后复用候选")
    try:
        _strict_datetime(cand.get("frozen_at"), "candidate.frozen_at")
    except InputError as exc:
        rep.add("candidate", "FAIL", str(exc))
    if artifact_scope.campaign_id(fdir):
        if cand.get("feature") != artifact_scope.scope_key(fdir):
            rep.add("candidate", "FAIL", "candidate belongs to another campaign")
        if cand.get("integration_branch") != integration_branch(cfg, artifact_scope.branch_key(fdir)):
            rep.add("candidate", "FAIL", "candidate integration branch differs from campaign")
    tree = cand.get("candidate_tree")
    head, head_tree = head_info(repo)
    if head != cand.get("candidate") or head_tree != tree:
        rep.add("candidate", "FAIL", f"集成分支 HEAD/tree 与冻结候选不一致：冻结后有新提交或 tree 改写，重新 freeze")
    _check_main_unchanged(rep, repo, cfg, cand, phase=args.phase)
    _check_stack_base(rep, fdir, repo, cand)
    check_plan_unchanged(rep, fdir, cand)
    _check_ticket_states(rep, fdir, repo, cand)
    arch_check_meta = _check_arch_rules(rep, repo, str(cand.get("candidate")))
    if arch_check_meta != cand.get("arch_check"):
        rep.add("arch-check", "FAIL", "arch-check rules/guide changed since freeze; re-freeze the candidate")
    plan = frozen_plan(cand, integ)

    ev = yaml_head(integ / "evidence.md")
    if not ev:
        rep.add("evidence", "FAIL", "integration/evidence.md 不存在或缺 yaml 头")
    else:
        if ev.get("candidate") != cand.get("candidate") or ev.get("candidate_tree") != tree:
            rep.add("evidence", "FAIL", "evidence 头 candidate / candidate_tree 与候选不一致（证据过期）")
        if ev.get("all-passed") is not True:
            rep.add("evidence", "FAIL", "evidence 头 all-passed 不是 true")

    _check_feature_conformance(rep, fdir, integ, repo, cand)
    _check_feature_review(rep, fdir, integ, repo, cand)
    _check_candidate_verification(rep, integ, cand)

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
        check_no_active_decisions(rep, fdir)
        if mode == "local-human":
            _check_confirmation(rep, integ, cand)
        elif mode == "pr":
            if not args.main_repo:
                rep.add("main-repo", "FAIL", "feature-closeout.mode=pr 的 merge 检查必须提供 --main-repo")
            else:
                _check_pr_evidence(rep, integ, cand, cfg, Path(args.main_repo))
        elif mode not in ("server-enforced", "cooperative-ci"):
            rep.add("config", "FAIL", f"未知模式 {mode} 不得 merge")
        # CI 模式在这里只形成 candidate/business 授权；delivery.promote 还必须
        # 在同一进程内取得并再次在线核对 ci_proof，confirmation 不可替代。
        if args.main_repo:
            cur = git(Path(args.main_repo), "rev-parse", "--abbrev-ref", "HEAD").strip()
            if cur != cfg["main-branch"]:
                rep.add("main-repo", "FAIL", f"主工作区当前在 {cur}，不在 {cfg['main-branch']}：不在集成 worktree 合 main")

    if not rep.failed and args.record:
        event = "feature-verified" if args.phase == "verify" else "feature-confirmed"
        note = f"{cand.get('integration_branch')}@{head[:8]}；验收报告已生成"
        append_event(fdir, event, event_id=f"{event}:{cand['candidate']}", ts=cand.get("frozen_at"),
                     tree=tree[:12], note=note)
    return rep


def read_pr_evidence(integ: Path) -> dict:
    value = load_json(Path(integ) / PR_EVIDENCE)
    return value if isinstance(value, dict) else {}


def _check_pr_evidence(rep: Report, integ: Path, cand: dict, cfg: dict, main_repo: Path) -> None:
    """Prove a user-merged Gitea PR is exactly this candidate, without CI checks."""
    pr = read_pr_evidence(integ)
    if not pr:
        rep.add("pr", "FAIL", "integration/pr.json 不存在：先由用户合并 PR 并记录 PR 证据")
        return
    for key in ("provider", "repository", "number", "base", "head_sha", "head_tree",
                "merge_commit", "strategy", "merged"):
        if key not in pr:
            rep.add("pr", "FAIL", f"integration/pr.json 缺字段 {key}")
    settings = (cfg.get("feature-closeout") or {}).get("pr") or {}
    if pr.get("provider") != settings.get("provider"):
        rep.add("pr", "FAIL", "PR provider 与 feature-closeout.pr.provider 不一致")
    if pr.get("repository") != settings.get("repository"):
        rep.add("pr", "FAIL", "PR repository 与 feature-closeout.pr.repository 不一致")
    if pr.get("base") != cfg.get("main-branch"):
        rep.add("pr", "FAIL", "PR base 必须是配置的 main-branch")
    if pr.get("merged") is not True:
        rep.add("pr", "FAIL", "PR 尚未合并（merged 必须为 true）")
    if pr.get("head_sha") != cand.get("candidate"):
        rep.add("pr", "FAIL", "PR head SHA 与冻结候选不一致")
    if pr.get("head_tree") != cand.get("candidate_tree"):
        rep.add("pr", "FAIL", "PR head tree 与冻结候选 tree 不一致")
    merge_commit = str(pr.get("merge_commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", merge_commit):
        rep.add("pr", "FAIL", "PR merge commit 必须是完整 40 位 SHA")
        return
    try:
        main_head = git(main_repo, "rev-parse", cfg["main-branch"]).strip()
        if main_head != merge_commit:
            rep.add("pr", "FAIL", "主工作区 main 未停在 PR merge commit；先 fetch/checkout main")
        tree = git(main_repo, "rev-parse", merge_commit + "^{tree}").strip()
        if tree != cand.get("candidate_tree"):
            rep.add("pr", "FAIL", "PR merge commit tree 与冻结候选 tree 不一致")
        strategy = str(pr.get("strategy") or "")
        if strategy not in ("merge", "squash"):
            rep.add("pr", "FAIL", "PR strategy 必须为 merge / squash")
        parents = git(main_repo, "rev-list", "--parents", "-n", "1", merge_commit).split()[1:]
        if strategy == "merge" and cand.get("candidate") not in parents:
            rep.add("pr", "FAIL", "merge commit 的父提交不含冻结候选")
        if strategy == "squash" and len(parents) != 1:
            rep.add("pr", "FAIL", "squash 合并必须只有一个父提交")
    except InputError as exc:
        rep.add("pr", "FAIL", f"PR merge commit 不可证明：{exc}")


def _canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def feature_evidence_hashes(integ: Path) -> dict:
    """Hashes that bind review, candidate re-run and feature conformance to merge."""
    integ = Path(integ)
    return {
        "feature_review_sha256": sha256_file(integ / FEATURE_REVIEW_RECEIPT),
        "candidate_verification_sha256": sha256_file(integ / CANDIDATE_VERIFICATION),
        "feature_conformance_sha256": sha256_file(integ / FEATURE_CONFORMANCE),
    }


def feature_gate_evidence(integ: Path) -> dict:
    """All candidate-gate bytes that must change the promote operation identity."""
    integ = Path(integ)
    evidence = feature_evidence_hashes(integ)
    for key, filename in (
        ("report_sha256", "acceptance-report.md"),
        ("business_results_sha256", "business-results.json"),
        ("confirmation_sha256", "confirmation.md"),
    ):
        path = integ / filename
        evidence[key] = sha256_file(path) if path.is_file() else None
    return evidence


def authorize_merge(args, online_proof=None) -> dict:
    """Return an in-memory, single-scope authorization after all merge gates pass."""
    args.phase = "merge"
    args.record = False
    rep = check(args)
    if rep.failed:
        messages = "; ".join(i["msg"] for i in rep.items if i["level"] == "FAIL")
        raise InputError("merge authorization denied: " + messages)
    fdir = Path(args.feature_dir)
    load_progress_events(fdir)
    integ = integ_dir(args)
    cand = load_json(integ / "candidate.json")
    mode = feature_mode(load_config(fdir))
    if mode in ("local-human", "pr"):
        if online_proof is not None:
            raise InputError(f"{mode} 不接受 CI proof 代替候选/PR 证据")
    elif online_proof is None:
        raise InputError("CI promote 缺同进程在线 proof，不得生成 merge authorization")
    confirmation_path = integ / "confirmation.md"
    authorization = {
        "schema": "agent-dev.feature-merge-authorization/v2",
        "feature_dir": str(fdir.resolve()), "round": getattr(args, "round", None),
        "repo": str(Path(args.repo).resolve()), "mode": mode,
        # pr promote keeps no journal and re-authorizes on replay, when the merge
        # record is already in candidate.json; local/CI modes hash the full record
        # so a recorded candidate can never be re-authorized.
        "candidate_sha256": _canonical_sha(_candidate_record_stripped(cand) if mode == "pr" else cand),
        "candidate": cand["candidate"], "candidate_tree": cand["candidate_tree"],
        "main_head": cand["main_head"], "scope": "merge-main", "conditions": [],
        "report_sha256": sha256_file(integ / "acceptance-report.md"),
        "business_results_sha256": sha256_file(integ / "business-results.json"),
        **feature_evidence_hashes(integ),
        "confirmation_sha256": sha256_file(confirmation_path) if mode == "local-human" else None,
        "pr_sha256": sha256_file(integ / PR_EVIDENCE) if mode == "pr" else None,
        "online_proof_sha256": _canonical_sha(online_proof) if online_proof is not None else None,
        "online_proof": online_proof,
        "_capability": _MERGE_CAPABILITY,
    }
    return authorization


def restore_merge_authorization(payload) -> dict:
    """Restore only delivery's journaled in-process authorization for reconcile."""
    if not isinstance(payload, dict) or payload.get("schema") != "agent-dev.feature-merge-authorization/v2":
        raise InputError("invalid persisted feature merge authorization")
    required = ("feature_dir", "repo", "mode", "candidate_sha256", "candidate", "candidate_tree",
                "main_head", "scope", "conditions", "operation_id", "report_sha256",
                "business_results_sha256", "feature_review_sha256",
                "candidate_verification_sha256", "feature_conformance_sha256")
    if any(key not in payload for key in required) or payload.get("scope") != "merge-main" or payload.get("conditions") != []:
        raise InputError("persisted feature merge authorization is incomplete or out of scope")
    restored = dict(payload)
    restored["_capability"] = _MERGE_CAPABILITY
    return restored


# ---------- internal merge recording (no public CLI) ----------

def _candidate_record_stripped(cand: dict) -> dict:
    return {k: v for k, v in cand.items()
            if k not in ("merged_commit", "merged_at", "merge_operation", "merge_strategy", "ci_proof_sha256")}


def _proven_merge_tree(repo: Path, main_before: str, candidate: str) -> str:
    """Git's own three-way result proves no-ff and squash carry no extra edit."""
    out = git(repo, "merge-tree", "--write-tree", main_before, candidate)
    first = out.splitlines()[0].strip() if out.splitlines() else ""
    if not re.fullmatch(r"[0-9a-f]{40}", first):
        raise InputError("git merge-tree 未给出可证明的 clean tree")
    return first


def record_merge(authorization, merge_result) -> Report:
    """Record only the exact transaction authorized and executed by delivery.promote.

    ``authorization`` is deliberately in-memory and carries a process-local
    capability.  There is no CLI or arbitrary ``--commit`` entry point.
    """
    rep = Report()
    if not isinstance(authorization, dict) or authorization.get("_capability") is not _MERGE_CAPABILITY:
        rep.add("authorization", "FAIL", "record_merge 只接受 delivery.promote 同进程内存 authorization")
        return rep
    if not isinstance(merge_result, dict):
        rep.add("merge", "FAIL", "merge_result 必须是 delivery.promote 的结构化事务结果")
        return rep
    try:
        fdir = Path(authorization["feature_dir"])
        repo = Path(merge_result["repo"])
        integ = fdir / "integration" / authorization["round"] if authorization.get("round") else fdir / "integration"
        path = integ / "candidate.json"
        cand = load_json(path)
        if not cand:
            rep.add("candidate", "FAIL", "candidate.json 不存在")
            return rep
        if _canonical_sha(_candidate_record_stripped(cand)) != authorization["candidate_sha256"]:
            rep.add("candidate", "FAIL", "candidate 在授权后发生变化")
        if authorization.get("scope") != "merge-main" or authorization.get("conditions") != []:
            rep.add("authorization", "FAIL", "授权仅限无附条件 merge-main，不包含 deploy-prod / push")
        if sha256_file(integ / "acceptance-report.md") != authorization["report_sha256"]:
            rep.add("report", "FAIL", "验收报告在授权后变化")
        if sha256_file(integ / "business-results.json") != authorization["business_results_sha256"]:
            rep.add("business", "FAIL", "业务结果在授权后变化")
        for key, filename in (
            ("feature_review_sha256", FEATURE_REVIEW_RECEIPT),
            ("candidate_verification_sha256", CANDIDATE_VERIFICATION),
            ("feature_conformance_sha256", FEATURE_CONFORMANCE),
        ):
            if sha256_file(integ / filename) != authorization.get(key):
                rep.add("evidence", "FAIL", f"{filename} 在授权后变化；评审/复跑/符合性须重新确认")
        if authorization.get("confirmation_sha256"):
            if sha256_file(integ / "confirmation.md") != authorization["confirmation_sha256"]:
                rep.add("confirmation", "FAIL", "人工确认在授权后变化")
        mode = authorization.get("mode")
        if mode == "pr" and sha256_file(integ / PR_EVIDENCE) != authorization.get("pr_sha256"):
            rep.add("pr", "FAIL", "integration/pr.json 在授权后变化")
        commit = git(repo, "rev-parse", "--verify", str(merge_result.get("commit")) + "^{commit}").strip()
        if merge_result.get("operation_id") != authorization.get("operation_id"):
            rep.add("merge", "FAIL", "merge_result operation_id 与精确授权事务不一致")
        if git(repo, "rev-parse", "HEAD").strip() != commit:
            rep.add("merge", "FAIL", "merge_result 不是当前 main HEAD；不接受任意历史 commit")
        tree = git(repo, "rev-parse", commit + "^{tree}").strip()
        if tree != authorization["candidate_tree"]:
            rep.add("tree", "FAIL", "最终 tree 不等于 candidate tree；额外改动不得随合并混入")
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        strategy = merge_result.get("strategy")
        pr = {}
        if mode == "pr":
            pr = read_pr_evidence(integ)
            if not pr or str(pr.get("merge_commit")) != commit:
                rep.add("pr", "FAIL", "record_merge 的 commit 与 integration/pr.json 不一致")
            strategy = str(pr.get("strategy") or "")
            if strategy not in ("merge", "squash"):
                rep.add("merge", "FAIL", "PR strategy 必须为 merge / squash")
            if strategy == "merge" and authorization["candidate"] not in parents:
                rep.add("parents", "FAIL", "PR merge commit 的父提交不含冻结候选")
            if strategy == "squash" and len(parents) != 1:
                rep.add("parents", "FAIL", "PR squash 提交必须只有一个父提交")
        else:
            main_before = authorization["main_head"]
            if strategy == "no-ff":
                if parents != [main_before, authorization["candidate"]]:
                    rep.add("parents", "FAIL", "no-ff 合并父提交必须依次为冻结 main HEAD 与 candidate")
            elif strategy == "squash":
                if parents != [main_before]:
                    rep.add("parents", "FAIL", "squash 提交唯一父提交必须是冻结 main HEAD")
            else:
                rep.add("merge", "FAIL", "strategy 必须为 no-ff / squash")
            proven = _proven_merge_tree(repo, main_before, authorization["candidate"])
            if proven != authorization["candidate_tree"] or proven != tree:
                rep.add("tree", "FAIL", "git merge-tree 无法证明最终结果就是 candidate 的 clean 三方合并 tree")
        existing = cand.get("merged_commit")
        if existing and existing != commit:
            rep.add("replay", "FAIL", f"候选已登记另一合并 {existing[:12]}；不得重放为新 commit")
        if existing and cand.get("merge_operation") != authorization.get("operation_id"):
            rep.add("replay", "FAIL", "已登记候选的 operation_id 不同")
        if rep.failed:
            return rep
        config = load_config(fdir)
        event = {
            "event_id": authorization["operation_id"],
            "ts": cand.get("merged_at") or _progress_ts(), "event": "feature-merged",
            "issue": "feature", "commit": commit, "host": _event_host(config),
            "branch": cand["integration_branch"], "target": cand["main_branch"],
            "candidate": authorization["candidate"], "candidate_tree": authorization["candidate_tree"],
            "operation_id": authorization["operation_id"], "strategy": strategy,
            "note": (f"PR #{pr.get('number')} merged by {pr.get('merged_by') or 'user'}；candidate tree 已核"
                     if mode == "pr" else
                     f"{cand['integration_branch']} → {cand['main_branch']}；仅 merge-main，不含 deploy-prod/push"),
        }
        if not existing:
            recorded = dict(cand)
            recorded.update({
                "merged_commit": commit, "merged_at": event["ts"],
                "merge_operation": authorization["operation_id"], "merge_strategy": strategy,
                "ci_proof_sha256": authorization.get("online_proof_sha256"),
            })
            _atomic_json(path, recorded)
        _idempotent_merged_event(fdir, event)
        rep.add("merge", "PASS", f"已登记合并 {commit[:12]}（{strategy}，幂等操作 {authorization['operation_id']}）")
        return rep
    except (InputError, KeyError, TypeError, ValueError, OSError) as exc:
        rep.add("merge", "FAIL", f"合并登记失败，事务保持 pending：{exc}")
        return rep


def _ticket_identity(fdir: Path, issue: Path, iid: str) -> dict:
    fm = read_frontmatter(issue)
    # key 必须与 progress.load_issues 的构造一致（campaign 目录带 scope 前缀），
    # 否则 apply_operation 用 key 复核 find_ticket 会失配并抛 StaleOperation。
    key = issue.stem
    if artifact_scope.campaign_id(fdir):
        key = artifact_scope.scope_key(fdir) + "/" + key
    return {
        "id": iid, "key": key, "title": str(fm.get("title") or iid), "status": "done",
        "feature": artifact_scope.scope_key(fdir), "feature_dir": str(fdir.resolve()),
        "issue_path": str(issue.resolve()), "fm": fm,
    }


def _cleanup_ticket(rep: Report, repo: Path, fdir: Path, iid: str, issue: Path):
    try:
        pc = importlib.import_module("progress_cleanup")
        progress = importlib.import_module("progress")
        ticket = progress.find_ticket(repo, str(issue.resolve())) or _ticket_identity(fdir, issue, iid)
        resources = pc.discover_resources(repo)
        branch = next((name for name in resources["local_branches"]
                       if pc._branch_matches(ticket, name)), None)
        worktrees = [item for item in resources["worktrees"]
                     if branch and item.get("branch_name") == branch]
        impl_exists = (fdir / "impl" / iid).is_dir()
        if not branch and not worktrees and not impl_exists:
            rep.add("cleanup", "PASS", f"{iid} 已无 worktree/branch/dev 过程物，跳过重复清理")
            return
        plan = pc.make_plan(repo, ticket=ticket, intent="close")
        if not plan.get("ok"):
            rep.add("cleanup", "FAIL", f"{iid} 清理计划不可执行：{plan.get('reason') or plan.get('error')}")
            return
        result = pc.apply_operation(repo, plan["operation_id"], plan["confirm"])
        if result.get("ok"):
            rep.add("cleanup", "PASS", f"{iid} 已清理（journal {result.get('operation_id')}）")
        else:
            rep.add("cleanup", "FAIL", f"{iid} 清理未完成（{result.get('state')}）："
                                         f"{'; '.join(result.get('warnings') or []) or result.get('error')}")
    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        rep.add("cleanup", "FAIL", f"{iid} 清理失败：{exc}")


def _cleanup_feature(rep: Report, repo: Path, fdir: Path, cand: dict, round_name=None):
    try:
        pc = importlib.import_module("progress_cleanup")
        branch_oid = pc._full_oid(repo, cand["integration_branch"])
        worktrees = [item for item in pc.discover_resources(repo)["worktrees"]
                     if item.get("branch_name") == cand["integration_branch"]]
        impl_exists = (fdir / "impl").is_dir() and any((fdir / "impl").iterdir())
        if not branch_oid and not worktrees and not impl_exists:
            rep.add("cleanup", "PASS", "集成分支/worktree/dev 过程物均已清理，跳过重复 feature 清理")
            return
        plan = pc.make_plan(repo, feature_dir=fdir, intent="close", round=round_name)
        if not plan.get("ok"):
            rep.add("cleanup", "FAIL", f"feature 清理计划不可执行：{plan.get('reason') or plan.get('error')}")
            return
        result = pc.apply_operation(repo, plan["operation_id"], plan["confirm"])
        if result.get("ok"):
            rep.add("cleanup", "PASS", f"集成分支与 dev 过程物已清理（journal {result.get('operation_id')}）")
        else:
            rep.add("cleanup", "FAIL", f"feature 清理未完成（{result.get('state')}）："
                                         f"{'; '.join(result.get('warnings') or []) or result.get('error')}")
    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        rep.add("cleanup", "FAIL", f"feature 清理失败：{exc}")


def finalize(args) -> Report:
    return finalize_tickets(Path(args.feature_dir), Path(args.repo), getattr(args, "round", None),
                            cleanup=getattr(args, "cleanup", True))


def finalize_tickets(fdir: Path, repo: Path, round_name=None, cleanup=True) -> Report:
    """feature-merged → accepted/done, then close-and-clean ticket and feature resources."""
    rep = Report()
    fdir = Path(fdir)
    repo = Path(repo)
    integ = fdir / "integration" / round_name if round_name else fdir / "integration"
    cand = load_json(integ / "candidate.json")
    if not cand:
        rep.add("candidate", "FAIL", "integration/candidate.json 不存在：先 freeze 并 promote")
        return rep
    merged_commit = str(cand.get("merged_commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", merged_commit):
        rep.add("merge", "FAIL", "候选尚未登记 feature-merged commit；先完成 promote")
        return rep
    try:
        head = git(repo, "rev-parse", "HEAD").strip()
        branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except InputError as exc:
        rep.add("repo", "FAIL", str(exc))
        return rep
    if head != merged_commit or branch != cand.get("main_branch"):
        rep.add("merge", "FAIL", f"主工作区须停在 {cand.get('main_branch')}@{merged_commit[:12]}；"
                                      f"实际 {branch}@{head[:12]}")
        return rep
    events = load_progress_events(fdir)
    matching = [event for event in events
                if event.get("event") == "feature-merged"
                and event.get("commit") == merged_commit
                and event.get("event_id") == cand.get("merge_operation")]
    if len(matching) != 1:
        rep.add("merge", "FAIL", "缺少唯一且精确绑定候选的 feature-merged 事件")
        return rep
    config = load_config(fdir)
    host = _event_host(config)
    wanted = {str(item.get("id")) for item in cand.get("issues") or []}
    records = {}
    for issue in sorted((fdir / "issues").glob("issue-*.md")):
        fm = read_frontmatter(issue)
        match = re.match(r"issue-[0-9]+[a-z]?", issue.stem)
        iid = str(fm.get("issue-id") or (match.group(0) if match else issue.stem))
        if iid in wanted:
            records[iid] = (issue, fm)
    accepted = {event.get("event_id") for event in events if event.get("event") == "accepted"}
    for item in cand.get("issues") or []:
        iid = str(item.get("id"))
        if iid not in records:
            rep.add("finalize", "FAIL", f"{iid} 票文件丢失，不能写 accepted")
            continue
        issue, fm = records[iid]
        status = str(fm.get("status") or "")
        if status == "done":
            rep.add("finalize", "PASS", f"{iid} 已是 done；历史完成票不重复写 accepted")
            continue
        if status != "merged":
            rep.add("finalize", "FAIL", f"{iid} status={status or '<空>'}，只有 merged 票可 finalize")
            continue
        event_id = f"accepted:{iid}:{cand['candidate']}"
        if event_id not in accepted:
            append_event(fdir, "accepted", event_id=event_id, issue=iid,
                         commit=merged_commit, candidate=cand["candidate"],
                         candidate_tree=cand["candidate_tree"], target=cand.get("main_branch"),
                         branch=cand.get("integration_branch"), host=host,
                         note="feature candidate 通过评审/复跑/符合性并完成 merge-main")
        text = issue.read_text(encoding="utf-8")
        updated = re.sub(r"^status:\s*.*$", "status: done", text, count=1, flags=re.M)
        _module_state_store().atomic_write(issue, updated, mode=0o644)
        rep.add("finalize", "PASS", f"{iid} merged → done（accepted {event_id}）")
    if rep.failed or not cleanup:
        if not rep.failed:
            rep.add("cleanup", "INFO", "已按 --no-cleanup 保留 worktree/branch 与过程物")
        return rep
    for item in cand.get("issues") or []:
        iid = str(item.get("id"))
        if str(item.get("status")) != "merged":
            continue
        if iid in records:
            _cleanup_ticket(rep, repo, fdir, iid, records[iid][0])
    _cleanup_feature(rep, repo, fdir, cand, round_name)
    return rep


def _module_state_store():
    try:
        return importlib.import_module("state_store")
    except ImportError as exc:
        raise InputError(f"state_store 不可用：{exc}")


def run_command_ac(args) -> Report:
    """Execute one frozen command AC through verify_issue's command helper."""
    rep = Report()
    fdir = Path(args.feature_dir)
    repo = Path(args.repo)
    integ = integ_dir(args)
    cand = load_json(integ / "candidate.json")
    plan = frozen_plan(cand, integ) if cand else {}
    spec = (plan.get("cases") or {}).get(args.ac)
    if not cand or not spec:
        rep.add("plan", "FAIL", "candidate / 冻结 AC 不存在，先 freeze")
        return rep
    head, tree = head_info(repo)
    if head != cand.get("candidate") or tree != cand.get("candidate_tree"):
        rep.add("candidate", "FAIL", "当前 HEAD/tree 不是冻结 candidate")
        return rep
    if spec.get("executor") != "command" or spec.get("oracle") not in ("auto", "auto+human-review"):
        rep.add("plan", "FAIL", "run-ac 只接受 command + 自动判定 AC")
        return rep
    subject = "%s:%s" % (cand.get("feature") or fdir.name, args.ac)
    previous = []
    try:
        for manifest_path in sorted((integ / "raw").glob("*/" + GENERATION_MANIFEST)):
            value = load_generation_manifest(manifest_path)
            if value["subject"] == subject and value["status"] == "current":
                previous.append((manifest_path, value))
        if len(previous) > 1:
            raise InputError("同一 AC 有多个 current generation")
    except InputError as exc:
        rep.add("manifest", "FAIL", str(exc))
        return rep
    generation = "%s-%s-%s" % (head, args.ac, uuid.uuid4().hex)
    generation_dir = integ / "raw" / generation
    try:
        vi = importlib.import_module("verify_issue")
        task = {
            "id": args.ac, "kind": "dod", "command": spec["command"], "cwd": ".",
            "expected_exit": 0, "expected_output": str((spec.get("assertions") or {}).get("output", "")),
            "categories": [], "environment": [], "quarantine": [], "test_paths": [],
        }
        record = vi._execute(task, runner="supervisor", repo=repo, impl_dir=integ,
                             head=head, tree=tree, timeout=args.timeout,
                             config=load_config(fdir), root=repo_root_of(fdir), generation=generation)
    except (ImportError, AttributeError, ValueError, OSError) as exc:
        rep.add("execution", "FAIL", f"verify_issue 命令执行辅助不可用：{exc}")
        return rep
    raw = record["raw_log"]
    raw_path = integ / raw["path"]
    artifact = {
        "type": "raw", "path": raw_path.relative_to(fdir).as_posix(), "sha256": raw["sha256"],
        "candidate": head, "candidate_tree": tree, "generation": generation,
    }
    assertions = []
    for kind, expected in (spec.get("assertions") or {}).items():
        if kind == "exit":
            actual = record["actual_exit"]
        elif kind in ("output", "contains"):
            actual = record["output_matched"]
            expected = True
        else:
            actual = None
        assertions.append({"type": kind, "expected": expected, "actual": actual,
                           "status": "pass" if actual == expected else "fail"})
    passed = record["pass"] is True and assertions and all(a["status"] == "pass" for a in assertions)
    item = {
        "id": args.ac, "oracle": spec["oracle"], "executor": "command",
        "status": "pass" if passed else "fail",
        "execution": {
            "executor": "command", "command": spec["command"], "exit": record["actual_exit"],
            "started": record["started"], "generator": "verify_issue",
            "candidate": head, "candidate_tree": tree, "generation": generation,
        },
        "assertions": assertions, "artifacts": [artifact],
    }
    if spec["oracle"] == "auto+human-review":
        item["human"] = {"status": "pending"}
    result_snapshot = {"schema": "agent-dev.feature-ac-result/v2", "candidate": head,
                       "candidate_tree": tree, "generation": generation, "item": item}
    _atomic_json(generation_dir / "result.json", result_snapshot)
    manifest_artifacts = [
        {"path": "result.json", "kind": "business-result",
         "sha256": sha256_file(generation_dir / "result.json"),
         "size": (generation_dir / "result.json").stat().st_size},
        {"path": raw_path.relative_to(generation_dir).as_posix(), "kind": "raw",
         "sha256": raw["sha256"], "size": raw_path.stat().st_size},
    ]
    manifest = {
        "schema": GENERATION_SCHEMA, "subject": subject, "generation": generation,
        "candidate_tree": tree, "status": "current", "created_at": record["ended_at"],
        "artifacts": manifest_artifacts,
    }
    if previous:
        manifest["supersedes"] = previous[0][1]["generation"]
    manifest_path = generation_dir / GENERATION_MANIFEST
    _atomic_json(manifest_path, manifest)
    load_generation_manifest(manifest_path)
    for previous_path, previous_value in previous:
        previous_value["status"] = "superseded"
        _atomic_json(previous_path, previous_value)
    results_path = integ / "business-results.json"
    results = load_json(results_path)
    results.update({
        "schema": RESULTS_SCHEMA, "candidate": head, "candidate_tree": tree,
        "plan_sha256": cand["acceptance_plan"]["sha256"],
        "environment": results.get("environment") or {"name": "integration-worktree"},
    })
    items = [x for x in results.get("items", []) if isinstance(x, dict) and x.get("id") != args.ac]
    items.append(item)
    results["items"] = sorted(items, key=lambda x: x.get("id", ""))
    _atomic_json(results_path, results)
    rep.add("execution", "PASS" if passed else "FAIL",
            f"{args.ac} 实际 exit={record['actual_exit']}，generation={generation}，raw={artifact['path']}")
    return rep

def phase_command(args) -> int:
    event = record_phase(Path(args.feature_dir), Path(args.repo), args.phase, args.event,
                         round=getattr(args, "round", None))
    if args.json:
        _atomic_json(Path(args.json), event)
    print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


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


def _legacy_local_human(closeout: dict, frontmatter: dict) -> bool:
    since = closeout.get("since")
    if not since:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(since)) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(frontmatter.get("created") or "")):
        raise InputError("feature-closeout.since 与票 created 必须是 YYYY-MM-DD")
    try:
        since_date = datetime.fromisoformat(str(since)).date()
        created_date = datetime.fromisoformat(str(frontmatter.get("created") or "")).date()
    except ValueError as exc:
        raise InputError("feature-closeout.since 与票 created 必须是合法 YYYY-MM-DD：%s" % exc)
    return created_date < since_date


def target(args) -> int:
    issue = Path(args.issue)
    root = None
    for p in issue.resolve().parents:
        if (p / "dev" / "agents-config.md").is_file():
            root = p
            break
    if root is None:
        raise InputError(f"{issue} 上溯找不到 dev/agents-config.md")
    try:
        ac = importlib.import_module("agents_config")
        tc = importlib.import_module("ticket_context")
        config = ac.load(root / "dev" / "agents-config.md")
        fm = ac.read_frontmatter(issue)
        target = tc.target_branch(config, fm, issue.parent.parent)
    except (ImportError, ValueError, OSError) as exc:
        raise InputError(str(exc))
    fc = config.get("feature-closeout") or {}
    if fc.get("mode") in ("local-human", "pr") and target == config.get("main-branch"):
        legacy = _legacy_local_human(fc, fm)
        if not legacy:
            raise InputError("local-human/PR 票只能进入本需求集成分支；显式 target-branch=main 同样拒绝")
    print(target)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="需求收口（local-human）确定性门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pt = sub.add_parser("target", help="打印票的目标分支")
    pt.add_argument("--issue", required=True, help="主工作区内的票文件路径")
    pv = sub.add_parser("validate-plan", help="校验功能验收计划")
    pv.add_argument("--feature-dir", required=True, help="主工作区 campaign 目录 dev/features/<module>/<feature>/campaigns/<campaign-id>；兼容历史 feature 根")
    pv.add_argument("--json", help="结果 JSON 写入路径")
    for name in ("freeze", "check", "report", "run-ac"):
        p = sub.add_parser(name)
        p.add_argument("--feature-dir", required=True, help="主工作区 campaign 目录 dev/features/<module>/<feature>/campaigns/<campaign-id>；兼容历史 feature 根")
        p.add_argument("--round", help="campaign 内的执行轮次，写 integration/<round>/，不改变分支；历史 feature 根保留旧 round 分支规则")
        p.add_argument("--repo", required=True, help="集成 worktree")
        if name == "freeze":
            p.add_argument("--issues", help="--round 时本轮票清单，逗号分隔（如 issue-031,issue-039）")
        if name != "report":
            p.add_argument("--json", help="结果 JSON 写入路径")
        if name == "check":
            p.add_argument("--phase", choices=["verify", "merge"], required=True)
            p.add_argument("--record", action="store_true", help="通过时追加 feature-verified / feature-confirmed 事件")
            p.add_argument("--main-repo", help="merge 相：主工作区路径，核对其当前在 main-branch")
        if name == "run-ac":
            p.add_argument("--ac", required=True, help="冻结计划中的 command AC 编号")
            p.add_argument("--timeout", type=float, default=120.0)
    pp = sub.add_parser("phase", help="记录当前 candidate 的阶段 start/finish 计时")
    pp.add_argument("--feature-dir", required=True)
    pp.add_argument("--repo", required=True, help="集成 worktree")
    pp.add_argument("--phase", choices=list(PHASES), required=True)
    pp.add_argument("--event", choices=["start", "finish"], required=True)
    pp.add_argument("--round")
    pp.add_argument("--json", help="事件 JSON 写入路径")
    pf = sub.add_parser("finalize", help="feature-merged 后转 done 并清理票/feature 资源")
    pf.add_argument("--feature-dir", required=True, help="主工作区 campaign 目录 dev/features/<module>/<feature>/campaigns/<campaign-id>；兼容历史 feature 根")
    pf.add_argument("--round", help="同一 feature 目录下的一轮需求名")
    pf.add_argument("--repo", required=True, help="主工作区")
    pf.add_argument("--no-cleanup", action="store_true", dest="no_cleanup",
                    help="只写 accepted/done，保留 worktree/branch 与过程物")
    pf.add_argument("--json", help="结果 JSON 写入路径")
    args = ap.parse_args(argv)
    if args.cmd == "finalize":
        args.cleanup = not args.no_cleanup
    try:
        if args.cmd == "target":
            return target(args)
        if args.cmd == "phase":
            return phase_command(args)
        if args.cmd == "validate-plan":
            rep = validate_plan(args)
        elif args.cmd == "report":
            return report(args)
        else:
            fn = {"freeze": freeze, "check": check, "run-ac": run_command_ac,
                  "finalize": finalize}[args.cmd]
            rep = fn(args)
    except InputError as e:
        print(f"feature-closeout: 输入错误：{e}", file=sys.stderr)
        return 2
    title = args.cmd + (f" --phase {args.phase}" if args.cmd == "check" else "")
    return _emit(rep, title, getattr(args, "json", None))


if __name__ == "__main__":
    sys.exit(main())
