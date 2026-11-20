#!/usr/bin/env python3
"""Canonical capability and session-resource probes for the four supported hosts.

Configuration declares intent; it never proves a capability.  Callers can inject
real probe results (a bool, ``None`` for unknown, a status mapping, or a callable)
through ``probes``.  Destructive consumers must treat every non-``available``
session-activity result as a reason to preserve resources.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HOSTS = ("claude-code", "codex", "zcode", "dsh")
ALIASES = {"claude": "claude-code", "claude-code": "claude-code",
           "deepseek": "dsh", "dsh": "dsh", "codex": "codex", "zcode": "zcode"}
STATE_HOST = {"claude-code": "claude", "codex": "codex", "zcode": "zcode", "dsh": "deepseek"}
CAPABILITIES = ("independent_session", "resume_session", "session_activity",
                "worktree_reachable", "browser", "runtime")
STATUSES = ("declared", "available", "unavailable", "unknown")


class HostCapabilityError(RuntimeError):
    pass


def canonical_host(host):
    try:
        return ALIASES[str(host).strip().lower()]
    except (KeyError, AttributeError):
        raise HostCapabilityError("未知宿主：%r" % (host,))


def _host_config(config, host):
    if not isinstance(config, dict):
        return {}
    if host in config and isinstance(config[host], dict):
        return config[host]
    dispatch = config.get("dispatch") or {}
    hosts = dispatch.get("hosts") or {}
    return hosts.get(host) or {}


def _declaration(config, capability):
    capabilities = config.get("capabilities") or config.get("capability") or {}
    value = capabilities.get(capability, capabilities.get(capability.replace("_", "-")))
    if value is None and capability in config:
        value = config[capability]
    return value


def _probe_value(value, context):
    if callable(value):
        try:
            value = value(context)
        except Exception as exc:
            return "unknown", {"error": str(exc)}
    if isinstance(value, dict):
        status = value.get("status")
        if status not in STATUSES:
            return "unknown", {"error": "invalid probe status", "evidence": copy.deepcopy(value)}
        return status, {"evidence": copy.deepcopy(value)}
    if value is True:
        return "available", {}
    if value is False:
        return "unavailable", {}
    return "unknown", {}


def probe_host(host, config=None, probes=None, context=None):
    """Probe one canonical host without inferring availability from model config."""
    host = canonical_host(host)
    host_cfg = _host_config(config, host)
    probes = probes or {}
    context = dict(context or {}, host=host, config=host_cfg)
    capabilities, evidence = {}, {}
    for capability in CAPABILITIES:
        declared = _declaration(host_cfg, capability)
        if capability in probes:
            status, details = _probe_value(probes[capability], context)
        elif isinstance(declared, dict) and declared.get("probe") is not None:
            status, details = _probe_value(declared.get("probe"), context)
        elif declared is False:
            status, details = "unavailable", {"source": "explicit-config"}
        elif declared is not None:
            # declared=true / a command / a model route is only a declaration.
            status, details = "declared", {"source": "config-not-proof"}
        else:
            status, details = "unknown", {"source": "no-real-probe"}
        capabilities[capability] = status
        evidence[capability] = details
    return {"host": host, "capabilities": capabilities, "evidence": evidence,
            "model_config_present": bool(host_cfg.get("model") or host_cfg.get("development")),
            "ok": True}


def probe_all(config=None, probes=None, context=None):
    return {host: probe_host(host, config, (probes or {}).get(host), context)
            for host in HOSTS}


def independent_review_allowed(host, config=None, probes=None):
    return probe_host(host, config, probes)["capabilities"]["independent_session"] == "available"


def require_independent_review(host, config=None, probes=None):
    result = probe_host(host, config, probes)
    status = result["capabilities"]["independent_session"]
    if status != "available":
        raise HostCapabilityError(
            "宿主 %s 的独立会话能力为 %s；禁止在实现同一上下文自评" % (host, status))
    return result


def _read_state(host):
    import session_title
    path = session_title.state_path(STATE_HOST[host])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise HostCapabilityError("宿主会话状态不可读：%s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("sessions") or {}, dict):
        raise HostCapabilityError("宿主会话状态契约错误：%s" % path)
    return data.get("sessions") or {}


def _same_path(left, right):
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except (OSError, RuntimeError):
        return False


def _binding_issue(binding):
    value = str(binding.get("issue") or "")
    if not value:
        return None
    name = Path(value).stem
    import re
    match = re.search(r"issue-\d+[a-z0-9]*", name, re.I)
    return match.group(0).lower() if match else value.lower()


def _relevant(binding, repo, subject, worktrees):
    if binding.get("released_at"):
        return False
    bound_root = binding.get("repo_root") or binding.get("repo")
    bound_worktree = binding.get("worktree")
    root_match = _same_path(bound_root, repo)
    worktree_match = any(_same_path(bound_worktree, path) for path in worktrees)
    if not root_match and not worktree_match:
        return False
    if subject.get("kind") == "issue":
        if binding.get("issue") and _binding_issue(binding) != subject.get("id"):
            return False
        feature = str(binding.get("feature") or "").split("features/")[-1].strip("/")
        if feature and feature != subject.get("feature"):
            return False
    else:
        feature = str(binding.get("feature") or "").split("features/")[-1].strip("/")
        if feature and feature != subject.get("feature"):
            return False
    return True


def _observed_activity(binding):
    """Only an explicit host observation is evidence; timestamps are not liveness."""
    value = binding.get("session_activity")
    if isinstance(value, dict):
        return value.get("status") if value.get("status") in ("active", "inactive", "unknown") else "unknown"
    if value in ("active", "inactive"):
        return value
    return "unknown"


def session_resource_state(repo, subject, worktrees=(), config=None, probes=None):
    """Project active bindings and fail closed when their activity is unprovable."""
    repo = Path(repo).resolve()
    sessions, blockers = [], []
    for host in HOSTS:
        try:
            bindings = _read_state(host)
        except HostCapabilityError as exc:
            blockers.append(str(exc))
            continue
        for key, binding in bindings.items():
            if not isinstance(binding, dict) or not _relevant(binding, repo, subject, worktrees):
                continue
            activity = _observed_activity(binding)
            capability = "available" if activity in ("active", "inactive") else "unknown"
            worktree = binding.get("worktree")
            reachable = "available" if worktree and Path(worktree).exists() else "unknown"
            record = {"session": key, "host": host, "issue": _binding_issue(binding),
                      "worktree": worktree, "branch": binding.get("branch"),
                      "bound_at": binding.get("bound_at"), "last_seen": binding.get("last_seen"),
                      "revision": binding.get("revision"), "session_activity": activity,
                      "capabilities": {"session_activity": capability,
                                       "worktree_reachable": reachable}}
            sessions.append(record)
            if activity == "active":
                blockers.append("活动会话仍绑定资源：%s" % key)
            else:
                blockers.append("会话活性未知，阻止删除 worktree：%s" % key)
            if host in ("codex", "dsh") and reachable != "available":
                blockers.append("Codex/DSH 外部 worktree 不可达，保留唯一分支：%s" % key)
    return {"status": "blocked" if blockers else "available",
            "sessions": sessions, "blockers": list(dict.fromkeys(blockers))}


def preservation_requirements(host, capabilities, unique_branch=True):
    """Return conservative branch/worktree policy for external host resources."""
    host = canonical_host(host)
    values = capabilities.get("capabilities", capabilities)
    preserve_branch = bool(unique_branch and host in ("codex", "dsh")
                           and values.get("worktree_reachable") != "available")
    block_worktree = values.get("session_activity") != "available"
    return {"preserve_branch": preserve_branch, "block_worktree": block_worktree,
            "reason": "外部能力不可证" if preserve_branch or block_worktree else None}


# ---------- Native review-session verification ----------

def _session_id(value):
    if not isinstance(value, dict):
        return None
    return value.get("session") or value.get("session_id") or value.get("sessionId") or value.get("threadId") or value.get("id")


def _review_marker(review):
    payload = json.dumps(review, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > 4096:
        raise HostCapabilityError("评审绑定标记过大")
    return "AGENT_DEV_REVIEW:" + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _binding_marker(value):
    text = str(value.get("title") or value.get("name") or "") if isinstance(value, dict) else ""
    match = re.search(r"(?:^|\s)AGENT_DEV_REVIEW:([A-Za-z0-9_-]+)(?:\s|$)", text)
    if not match:
        return {}
    try:
        encoded = match.group(1)
        encoded += "=" * (-len(encoded) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise HostCapabilityError("原生评审绑定标记损坏") from exc
    if not isinstance(parsed, dict):
        raise HostCapabilityError("原生评审绑定标记不是对象")
    return parsed


def _review_binding(*values):
    keys = ("review_id", "round", "issue", "feature", "head", "tree",
            "report_sha256", "context_digest", "repo", "worktree")
    result = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        nested = value.get("review") or value.get("review_binding") or {}
        # A native metadata field is preferred over the readable title marker.
        for source in (_binding_marker(value), nested, value.get("metadata") or {}, value):
            for key in keys:
                if key in source and source[key] is not None:
                    result[key] = source[key]
    return result


def _require_review_binding(binding):
    if not binding:
        raise HostCapabilityError("缺少本次评审绑定")
    if not (binding.get("review_id")
            or (binding.get("round") is not None and binding.get("head"))
            or (binding.get("head") and binding.get("report_sha256"))):
        raise HostCapabilityError("本次评审绑定需要 review_id，或 round+head / head+report_sha256")


def _native_digest(record, binding):
    payload = json.dumps({"session": record, "review": binding}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _native_record_from_json(value, session_id):
    if isinstance(value, dict) and isinstance(value.get("session"), dict):
        value = value["session"]
    elif isinstance(value, dict) and isinstance(value.get("thread"), dict):
        value = value["thread"]
    elif isinstance(value, dict) and isinstance(value.get("sessions"), list):
        matches = [item for item in value["sessions"] if _session_id(item) == session_id]
        value = matches[0] if len(matches) == 1 else None
    if not isinstance(value, dict) or _session_id(value) != session_id:
        raise HostCapabilityError("原生宿主中不存在该 reviewer session")
    return value


def _zcode_native_session(reviewer, session_id):
    import session_title
    command = session_title.default_command("zcode")
    if not command:
        raise HostCapabilityError("ZCode app-server 不可用，原生 session/list 无法证明")
    command = list(command)
    cwd = reviewer.get("cwd") or reviewer.get("worktree") or reviewer.get("repo") or os.getcwd()
    if "--cwd" not in command:
        command += ["--cwd", str(cwd)]
    with session_title.adapter_reentry_guard(), session_title.NDJSONClient(command, cwd) as client:
        result = client.call("session/list", {"sessionIds": [session_id]})
    return _native_record_from_json(result, session_id)


def _codex_native_session(reviewer, session_id):
    import session_title
    command = session_title.default_command("codex")
    if not command:
        raise HostCapabilityError("Codex app-server 不可用，原生 thread/read 无法证明")
    cwd = reviewer.get("cwd") or reviewer.get("worktree") or reviewer.get("repo") or os.getcwd()
    with session_title.adapter_reentry_guard(), session_title.NDJSONClient(command, cwd) as client:
        client.call("initialize", {"clientInfo": {"name": "review-session-verify", "version": "1.0.0"}})
        result = client.call("thread/read", {"threadId": session_id, "includeTurns": False})
    return _native_record_from_json(result, session_id)


def _claude_native_session(reviewer, native_proof, session_id):
    import session_title
    root = session_title.host_root("claude")
    configured = reviewer.get("native_record") or native_proof.get("native_record")
    candidates = []
    if configured:
        candidates = [Path(configured).expanduser()]
    else:
        candidates = [root / "tasks" / (session_id + ".json"),
                      root / "sessions" / (session_id + ".json")]
        candidates += sorted((root / "projects").glob("**/" + session_id + ".jsonl"))
    for path in candidates:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root.resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.name == "host-probes.json" or resolved.is_dir():
            continue
        try:
            if resolved.suffix == ".jsonl":
                records = [json.loads(line) for line in resolved.read_text(encoding="utf-8").splitlines() if line.strip()]
                matching = [item for item in records if isinstance(item, dict)
                            and session_id in {item.get("sessionId"), item.get("session_id"), item.get("id")}]
                if matching:
                    return matching[-1]
            else:
                value = json.loads(resolved.read_text(encoding="utf-8"))
                return _native_record_from_json(value, session_id)
        except (OSError, ValueError):
            continue
    raise HostCapabilityError("Claude 原生 Task/session 记录不存在或不可读")


def _dsh_native_session(reviewer, config, session_id):
    host_config = _host_config(config, "dsh")
    command = host_config.get("native_session_command")
    if not command:
        raise HostCapabilityError("DSH 原生 session 查询未配置，无法证明")
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise HostCapabilityError("DSH native_session_command 配置无效")
    command = [item.replace("{session}", session_id) for item in command]
    env = dict(os.environ, AGENT_DEV_SESSION_ID=session_id, AGENT_DEV_SESSION_QUERY="review")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, env=env,
                            cwd=reviewer.get("cwd") or reviewer.get("repo") or os.getcwd())
    if result.returncode:
        raise HostCapabilityError("DSH 原生 session 查询失败：" + (result.stderr or result.stdout).strip())
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise HostCapabilityError("DSH 原生 session 查询未返回 JSON") from exc
    return _native_record_from_json(value, session_id)


def read_native_review_session(host, reviewer, native_proof=None, config=None):
    """Read the real native session; host-probes/probe booleans are never consulted."""
    host = canonical_host(host)
    native_proof = native_proof or {}
    session_id = _session_id(reviewer)
    if not session_id:
        raise HostCapabilityError("reviewer 缺少 session ID")
    if host == "zcode":
        return _zcode_native_session(reviewer, session_id)
    if host == "codex":
        return _codex_native_session(reviewer, session_id)
    if host == "claude-code":
        return _claude_native_session(reviewer, native_proof, session_id)
    return _dsh_native_session(reviewer, config, session_id)


def _title_with_binding(record, review):
    title = str(record.get("title") or record.get("name") or "Review session")
    marker = _review_marker(review)
    pattern = r"(?:^|\s)AGENT_DEV_REVIEW:[A-Za-z0-9_-]+(?=\s|$)"
    if re.search(pattern, title):
        return re.sub(pattern, lambda match: (" " if match.group(0).startswith(" ") else "") + marker,
                      title, count=1)
    return title.rstrip() + " " + marker


def _write_zcode_binding(reviewer, review, session_id):
    import session_title
    command = session_title.default_command("zcode")
    if not command:
        raise HostCapabilityError("ZCode app-server 不可用，无法原生写入评审绑定")
    command = list(command)
    cwd = reviewer.get("cwd") or reviewer.get("worktree") or reviewer.get("repo") or os.getcwd()
    if "--cwd" not in command:
        command += ["--cwd", str(cwd)]
    with session_title.adapter_reentry_guard(), session_title.NDJSONClient(command, cwd) as client:
        current = _native_record_from_json(client.call("session/list", {"sessionIds": [session_id]}), session_id)
        if _native_role(current) not in ("review", "supervisor-review"):
            raise HostCapabilityError("拒绝把评审绑定写到非评审 session")
        client.call("session/resume", {"sessionId": session_id})
        result = client.call("v4/command", {
            "commandId": "review-binding-" + hashlib.sha256(_review_marker(review).encode()).hexdigest()[:16],
            "clientId": "host-adapters", "sessionId": session_id, "type": "renameSession",
            "payload": {"title": _title_with_binding(current, review)},
            "issuedAt": int(__import__("time").time() * 1000),
        })
        status = result.get("status") or (result.get("ack") or {}).get("status")
        if status and status not in ("accepted", "duplicate", "noop"):
            raise HostCapabilityError("ZCode 拒绝原生评审绑定写入：" + str(status))
    return "zcode-native-title-marker"


def _write_codex_binding(reviewer, review, session_id):
    import session_title
    command = session_title.default_command("codex")
    if not command:
        raise HostCapabilityError("Codex app-server 不可用，无法原生写入评审绑定")
    cwd = reviewer.get("cwd") or reviewer.get("worktree") or reviewer.get("repo") or os.getcwd()
    with session_title.adapter_reentry_guard(), session_title.NDJSONClient(command, cwd) as client:
        client.call("initialize", {"clientInfo": {"name": "review-binding", "version": "1.0.0"}})
        current = _native_record_from_json(client.call("thread/read", {"threadId": session_id, "includeTurns": False}), session_id)
        if _native_role(current) not in ("review", "supervisor-review"):
            raise HostCapabilityError("拒绝把评审绑定写到非评审 thread")
        client.call("thread/name/set", {"threadId": session_id,
                                        "name": _title_with_binding(current, review)})
    return "codex-native-title-marker"


def _write_dsh_binding(reviewer, review, config, session_id):
    host_config = _host_config(config, "dsh")
    command = host_config.get("native_session_command")
    if not command:
        raise HostCapabilityError("DSH native_session_command 未配置，无法原生写入评审绑定")
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise HostCapabilityError("DSH native_session_command 配置无效")
    command = [item.replace("{session}", session_id) for item in command]
    env = dict(os.environ, AGENT_DEV_SESSION_ID=session_id, AGENT_DEV_SESSION_QUERY="review",
               AGENT_DEV_SESSION_WRITE="1", AGENT_DEV_REVIEW_METADATA=json.dumps(review, ensure_ascii=False, sort_keys=True),
               AGENT_DEV_REVIEW_MARKER=_review_marker(review))
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, env=env,
                            cwd=reviewer.get("cwd") or reviewer.get("repo") or os.getcwd())
    if result.returncode:
        raise HostCapabilityError("DSH 原生评审绑定写入失败：" + (result.stderr or result.stdout).strip())
    if result.stdout.strip():
        try:
            _native_record_from_json(json.loads(result.stdout), session_id)
        except ValueError as exc:
            raise HostCapabilityError("DSH 原生写入响应不是 JSON") from exc
    return "dsh-native-command"


def _write_claude_binding(reviewer, review, session_id):
    """Return a native hook instruction; never pretend the host already applied it."""
    import session_title
    current = _claude_native_session(reviewer, {}, session_id)
    if _native_role(current) not in ("review", "supervisor-review"):
        raise HostCapabilityError("拒绝把评审绑定写到非评审 Claude session")
    title = _title_with_binding(current, review)
    event = {"session_id": session_id, "cwd": reviewer.get("cwd") or reviewer.get("repo") or os.getcwd(),
             "hook_event_name": "UserPromptSubmit", "session_title": current.get("title") or current.get("name"),
             "title_source": None}
    written, outcome = session_title.claude_rename(event, title, None, override_manual=True)
    if outcome != "native":
        raise HostCapabilityError("Claude 原生 sessionTitle 通道不可用")
    instruction = session_title.hook_output("claude", "UserPromptSubmit", {"status": outcome, "title": written})
    if not instruction:
        raise HostCapabilityError("Claude 原生绑定指令生成失败")
    return "claude-native-hook", instruction


def write_review_binding(host, reviewer, implementer, review, config=None):
    """Attach review identity through a native host channel, then read it back.

    No receipt file or local JSON is accepted as a native write. Claude returns
    an explicit native-hook instruction with ``written=False`` until the host
    applies it; subsequent ``verify_review_session`` still requires real reads.
    """
    host = canonical_host(host)
    if not isinstance(reviewer, dict) or not isinstance(implementer, dict) or not isinstance(review, dict):
        raise HostCapabilityError("reviewer/implementer/review 必须是对象")
    session_id = _session_id(reviewer)
    if not session_id or session_id == _session_id(implementer):
        raise HostCapabilityError("原生绑定要求 reviewer 与 implementer 是不同 session（different sessions）")
    reviewer_agent = reviewer.get("agent") or reviewer.get("role")
    if str(reviewer_agent or "").replace("_", "-") not in ("review", "supervisor-review"):
        raise HostCapabilityError("只能向 review/supervisor-review session 写评审绑定（different agents；must be dispatched as supervisor-review）")
    if reviewer.get("host") and canonical_host(reviewer["host"]) != host:
        raise HostCapabilityError("reviewer host 与写入宿主不一致")
    binding = _review_binding(review)
    _require_review_binding(binding)
    instruction = None
    if host == "zcode":
        channel = _write_zcode_binding(reviewer, binding, session_id)
    elif host == "codex":
        channel = _write_codex_binding(reviewer, binding, session_id)
    elif host == "dsh":
        channel = _write_dsh_binding(reviewer, binding, config, session_id)
    else:
        # Claude hook application is asynchronous. An already applied binding is
        # authoritative native state and must not produce another instruction.
        current = read_native_review_session(host, reviewer, {"review": binding}, config)
        if _native_role(current) not in ("review", "supervisor-review"):
            raise HostCapabilityError("原生回读 session 不是评审角色")
        if _review_binding(current) == binding:
            return {"ok": True, "written": True, "requires_native_apply": False,
                    "host": host, "session": session_id, "review": binding,
                    "channel": "claude-native-readback", "native_session": current}
        channel, instruction = _write_claude_binding(reviewer, binding, session_id)
    if instruction is not None:
        return {"ok": True, "written": False, "requires_native_apply": True,
                "host": host, "session": session_id, "review": binding,
                "channel": channel, "native_instruction": instruction}
    record = read_native_review_session(host, reviewer, {"review": binding}, config)
    if _native_role(record) not in ("review", "supervisor-review"):
        raise HostCapabilityError("原生回读 session 不是评审角色")
    actual = _review_binding(record)
    for key, value in binding.items():
        if actual.get(key) != value:
            raise HostCapabilityError("原生写入后回读绑定不匹配：" + key)
    return {"ok": True, "written": True, "requires_native_apply": False,
            "host": host, "session": session_id, "review": binding,
            "channel": channel, "native_session": record}


def _native_role(record):
    """Resolve only documented role declarations; contradictory evidence is fatal."""
    declarations = [(key, record[key]) for key in (
        "role", "subagent_type", "subagentType", "agent_type", "agentType", "agent", "agentRole")
        if key in record]
    # Do not recursively search metadata or use a title as identity evidence.
    container = record
    path = []
    for key in ("source", "subAgent", "thread_spawn"):
        if key not in container:
            break
        path.append(key)
        container = container[key]
        if not isinstance(container, dict):
            raise HostCapabilityError("原生角色路径必须是对象：" + ".".join(path))
    else:
        if "agent_role" in container:
            declarations.append(("source.subAgent.thread_spawn.agent_role", container["agent_role"]))
    roles = set()
    for key, value in declarations:
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise HostCapabilityError("原生角色声明必须是字符串：" + key)
        roles.add(value.replace("_", "-").lower())
    if len(roles) > 1:
        raise HostCapabilityError("原生角色声明冲突")
    return next(iter(roles), None)


def _operator_attestation(native_proof):
    value = native_proof.get("operator_attestation") or native_proof.get("attestation")
    if not isinstance(value, dict):
        raise HostCapabilityError("协作模式需要显式 operator attestation")
    statement = str(value.get("statement") or "")
    if not value.get("operator") or not value.get("confirmed_at") or not (
            "independent review" in statement.lower() or "独立评审" in statement):
        raise HostCapabilityError("operator attestation 必须明确证明独立评审并带人员/时间")
    return value


def verify_review_session(host, reviewer, implementer, native_proof=None, config=None):
    """Verify a real native reviewer session and its binding to this review.

    Host-verified mode fails closed when native evidence is unavailable. In
    operator-attested mode only *unavailability* may use the cooperative path;
    contradictory native identity/role/binding/hash always fails.
    """
    host = canonical_host(host)
    native_proof = native_proof or {}
    if not isinstance(reviewer, dict) or not isinstance(implementer, dict) or not isinstance(native_proof, dict):
        raise HostCapabilityError("reviewer/implementer/native_proof 必须是对象")
    reviewer_session = _session_id(reviewer)
    implementer_session = _session_id(implementer)
    if not reviewer_session or not implementer_session or reviewer_session == implementer_session:
        raise HostCapabilityError("reviewer 与 implementer 必须是不同的真实 session（different sessions）")
    if reviewer.get("host") and canonical_host(reviewer["host"]) != host:
        raise HostCapabilityError("reviewer host 与核验宿主不一致")
    reviewer_agent = reviewer.get("agent") or reviewer.get("role")
    implementer_agent = implementer.get("agent") or implementer.get("role")
    if reviewer_agent and implementer_agent and reviewer_agent == implementer_agent:
        raise HostCapabilityError("实现角色不能冒充评审角色（different agents；reviewer must be dispatched as supervisor-review）")
    if str(reviewer_agent or "").replace("_", "-") not in ("review", "supervisor-review"):
        raise HostCapabilityError("reviewer 必须是 review/supervisor-review 角色")
    expected_binding = _review_binding(reviewer, native_proof)
    _require_review_binding(expected_binding)
    trust = ((config or {}).get("enforcement") or {}).get("review-trust", "host-verified")
    if trust not in ("host-verified", "operator-attested"):
        raise HostCapabilityError("未知 review-trust 模式：%r" % trust)
    if native_proof.get("host") and canonical_host(native_proof["host"]) != host:
        raise HostCapabilityError("native proof host 不匹配")
    if native_proof.get("session") and native_proof["session"] != reviewer_session:
        raise HostCapabilityError("native proof session 不匹配")
    try:
        record = read_native_review_session(host, reviewer, native_proof, config)
    except (HostCapabilityError, OSError, subprocess.SubprocessError, ValueError) as exc:
        if trust == "host-verified":
            raise HostCapabilityError("host-verified 拒绝：无法原生证明 reviewer session：" + str(exc)) from exc
        if ((config or {}).get("enforcement") or {}).get("require-host-capabilities") is not False:
            raise HostCapabilityError(
                "原生能力不可用且 require-host-capabilities 未显式为 false；拒绝 operator-attested 回退") from exc
        attestation = _operator_attestation(native_proof)
        return {"ok": True, "verified": True, "verification": "operator-attested",
                "cooperative": True, "host": host, "session": reviewer_session,
                "review": expected_binding, "attestation": attestation,
                "native_unavailable": str(exc)}
    role = _native_role(record)
    if role not in ("review", "supervisor-review"):
        raise HostCapabilityError("原生 session 不是独立评审角色")
    if reviewer.get("agent") and role != str(reviewer["agent"]).replace("_", "-"):
        raise HostCapabilityError("原生 session 角色与 reviewer agent 不匹配")
    actual_binding = _review_binding(record)
    _require_review_binding(actual_binding)
    for key, expected in expected_binding.items():
        if key not in actual_binding or actual_binding[key] != expected:
            raise HostCapabilityError("原生 session 未绑定本次评审字段：" + key)
    digest = _native_digest(record, expected_binding)
    supplied = native_proof.get("native_hash") or native_proof.get("proof_sha256")
    if not supplied or supplied != digest:
        raise HostCapabilityError("native proof/hash 与实时原生 session 不匹配")
    return {"ok": True, "verified": True, "verification": "host-verified",
            "cooperative": False, "host": host, "session": reviewer_session,
            "role": role, "review": expected_binding, "native_hash": digest,
            "native_session": record}

