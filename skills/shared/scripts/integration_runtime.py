#!/usr/bin/env python3
"""Configuration-driven integration leases and development-service lifecycle.

No npm/dev command is inferred.  Every service and lease is bound to repository,
feature, round, candidate, worktree and log generation.  Release/stop require the
exact resource ID and candidate and are idempotent only after a recorded matching
terminal state.  Unknown capabilities fail closed.
"""
from __future__ import annotations

import copy
import json
import os
import platform
import shlex
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_store import FileLock, atomic_json_write

OID_RE = __import__("re").compile(r"^[0-9a-f]{40}$")
_PROCESSES = {}


class RuntimeFailure(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_config(repo):
    import agents_config
    return agents_config.load(Path(repo) / "dev" / "agents-config.md")


def _runtime_config(repo, config=None):
    config = _load_config(repo) if config is None else config
    value = config.get("integration-runtime") or {}
    if not isinstance(value, dict):
        raise RuntimeFailure("integration-runtime 必须是配置对象")
    return value


def runtime_dir(repo, config=None):
    value = _runtime_config(repo, config).get("runtime-dir") or ".agent-dev/runtime"
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeFailure("integration-runtime.runtime-dir 必须是仓库内相对路径")
    return Path(repo).resolve() / path


def _machine():
    return platform.node() or "unknown-machine"


def _binding(repo, feature_dir, round, candidate, worktree, log_generation):
    values = {"repo": str(Path(repo).resolve()), "feature_dir": str(Path(feature_dir).resolve()),
              "round": round, "candidate": candidate, "worktree": str(Path(worktree).resolve()),
              "log_generation": log_generation, "machine_id": _machine()}
    if not isinstance(candidate, str) or not OID_RE.fullmatch(candidate):
        raise RuntimeFailure("runtime 绑定需要完整 candidate OID")
    if not isinstance(log_generation, str) or not log_generation.strip():
        raise RuntimeFailure("runtime 绑定需要 log_generation")
    if not values["round"] and values["round"] is not None:
        raise RuntimeFailure("round 必须是字符串或 null")
    return values


def _read(path, default=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (OSError, ValueError) as exc:
        raise RuntimeFailure("runtime 状态损坏：%s：%s" % (path, exc))
    if not isinstance(value, dict):
        raise RuntimeFailure("runtime 状态必须是对象：%s" % path)
    return value


def _records(directory):
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.glob("*.json")):
        result.append(_read(path))
    return result


def _lease_path(repo, config=None):
    config = _load_config(repo) if config is None else config
    closeout = config.get("feature-closeout") or {}
    instance = closeout.get("verify-instance")
    if not instance:
        raise RuntimeFailure("未配置 feature-closeout.verify-instance，租约能力 unknown")
    value = instance.get("lease")
    if not isinstance(value, str) or not value:
        raise RuntimeFailure("verify-instance.lease 未配置")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeFailure("verify-instance.lease 必须位于仓库内")
    return Path(repo).resolve() / path


def _service_path(repo, service_id, config=None):
    if not isinstance(service_id, str) or not service_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._" for c in service_id):
        raise RuntimeFailure("service id 非法")
    return runtime_dir(repo, config) / "services" / (service_id + ".json")


def _command(config, name):
    service = config.get("dev-service") or {}
    if not isinstance(service, dict):
        return None
    value = service.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _service_commands(repo, config=None, required=("start", "ready", "stop", "status")):
    config = _runtime_config(repo, config)
    missing = [name for name in required if not _command(config, name)]
    if missing:
        raise RuntimeFailure("dev-service 能力 unknown，缺少配置：" + ", ".join(missing))
    return {name: _command(config, name) for name in required}


def _env(record):
    result = dict(os.environ)
    for key, value in {
        "AGENT_DEV_SERVICE_ID": record.get("service_id") or record.get("lease_id"),
        "AGENT_DEV_REPO": record.get("repo"), "AGENT_DEV_FEATURE_DIR": record.get("feature_dir"),
        "AGENT_DEV_ROUND": record.get("round"), "AGENT_DEV_CANDIDATE": record.get("candidate"),
        "AGENT_DEV_WORKTREE": record.get("worktree"), "AGENT_DEV_LOG_GENERATION": record.get("log_generation"),
        "AGENT_DEV_SERVICE_PID": record.get("pid"),
    }.items():
        result[key] = "" if value is None else str(value)
    return result


def _run(command, record, timeout=30):
    try:
        return subprocess.run(command, shell=True, cwd=record["worktree"], env=_env(record),
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeFailure("配置命令执行失败：%s" % exc)


def _relevant(record, feature_dir=None, worktrees=()):
    if feature_dir is not None and record.get("feature_dir") != str(Path(feature_dir).resolve()):
        return False
    if worktrees and record.get("worktree") not in {str(Path(path).resolve()) for path in worktrees}:
        return False
    return True


def resource_state(repo, feature_dir=None, worktrees=(), config=None):
    """Read runtime records without guessing from process names or package.json."""
    config = _load_config(repo) if config is None else config
    directory = runtime_dir(repo, config)
    services = [item for item in _records(directory / "services")
                if _relevant(item, feature_dir, worktrees)]
    leases = []
    try:
        lease_path = _lease_path(repo, config)
        lease = _read(lease_path)
        if lease and _relevant(lease, feature_dir, worktrees):
            leases.append(lease)
    except RuntimeFailure:
        # No configured verify-instance means no local lease file to inspect.
        pass
    leases += [item for item in _records(directory / "leases")
               if _relevant(item, feature_dir, worktrees)]
    blockers = []
    for service in services:
        if service.get("state") not in ("stopped", "failed"):
            if service.get("machine_id") != _machine():
                blockers.append("跨机器服务能力不可证，保留：%s" % service.get("service_id"))
            else:
                blockers.append("开发服务未停止：%s" % service.get("service_id"))
    for lease in leases:
        if not lease.get("released_at"):
            if lease.get("machine_id") != _machine():
                blockers.append("跨机器租约 CAS 能力不可证，保留：%s" % lease.get("lease_id"))
            else:
                blockers.append("verify-instance 租约未释放：%s" % lease.get("lease_id"))
    return {"status": "blocked" if blockers else "available",
            "services": services, "leases": leases, "blockers": blockers,
            "runtime_dir": str(directory)}


def acquire_lease(repo, feature_dir, round, candidate, worktree, log_generation, config=None):
    config = _load_config(repo) if config is None else config
    path = _lease_path(repo, config)
    binding = _binding(repo, feature_dir, round, candidate, worktree, log_generation)
    timeout = int((_runtime_config(repo, config).get("lease-timeout-seconds") or 3600))
    lock = FileLock(str(path) + ".lock")
    with lock:
        current = _read(path)
        now = time.time()
        if current and not current.get("released_at"):
            if current.get("machine_id") != _machine():
                raise RuntimeFailure("跨机器 lease 接管/CAS 能力 unknown；即使过期也保留")
            try:
                expires = datetime.fromisoformat(str(current.get("expires_at")).replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError) as exc:
                raise RuntimeFailure("活动 lease 的 expires_at 损坏，拒绝接管") from exc
            if expires > now:
                raise RuntimeFailure("verify-instance 租约已由 %s 持有" % current.get("lease_id"))
        lease_id = "lease-" + uuid.uuid4().hex
        record = dict(binding, schema="agent-dev.verify-instance-lease/v2", lease_id=lease_id,
                      acquired_at=_now(), heartbeat_at=_now(), revision=1, released_at=None)
        record["expires_at"] = datetime.fromtimestamp(now + timeout, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        atomic_json_write(path, record)
    return record


def heartbeat_lease(repo, lease_id, candidate, config=None):
    config = _load_config(repo) if config is None else config
    path = _lease_path(repo, config)
    timeout = int((_runtime_config(repo, config).get("lease-timeout-seconds") or 3600))
    with FileLock(str(path) + ".lock"):
        record = _read(path)
        if not record or record.get("lease_id") != lease_id or record.get("candidate") != candidate:
            raise RuntimeFailure("lease id/candidate 不匹配")
        if record.get("released_at"):
            raise RuntimeFailure("已释放 lease 不能 heartbeat")
        if record.get("machine_id") != _machine():
            raise RuntimeFailure("跨机器 lease heartbeat/CAS 能力 unknown")
        record["revision"] = int(record.get("revision") or 0) + 1
        record["heartbeat_at"] = _now()
        record["expires_at"] = datetime.fromtimestamp(time.time() + timeout, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        atomic_json_write(path, record)
    return record


def release_lease(repo, lease_id, candidate, config=None):
    config = _load_config(repo) if config is None else config
    path = _lease_path(repo, config)
    with FileLock(str(path) + ".lock"):
        record = _read(path)
        if not record or record.get("lease_id") != lease_id or record.get("candidate") != candidate:
            raise RuntimeFailure("lease release 需要匹配的 id 和 candidate")
        if record.get("machine_id") != _machine():
            raise RuntimeFailure("跨机器 lease release/CAS 能力 unknown，保留")
        if record.get("released_at"):
            return dict(record, idempotent=True)
        record["released_at"] = _now()
        record["revision"] = int(record.get("revision") or 0) + 1
        atomic_json_write(path, record)
    return dict(record, idempotent=False)


def start_service(repo, feature_dir, round, candidate, worktree, log_generation, config=None):
    config = _load_config(repo) if config is None else config
    commands = _service_commands(repo, config)
    binding = _binding(repo, feature_dir, round, candidate, worktree, log_generation)
    service_id = "service-" + uuid.uuid4().hex
    path = _service_path(repo, service_id, config)
    log_dir = runtime_dir(repo, config) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_generation = "".join(c if c.isalnum() or c in "-._" else "_" for c in log_generation)
    log_path = log_dir / (service_id + "-" + safe_generation + ".log")
    record = dict(binding, schema="agent-dev.dev-service/v2", service_id=service_id,
                  state="starting", pid=None, started_at=_now(), ready_at=None,
                  stopped_at=None, log_path=str(log_path), revision=1)
    _write_service(path, record)
    try:
        with log_path.open("ab") as output:
            process = subprocess.Popen(commands["start"], shell=True, cwd=record["worktree"],
                                       env=_env(record), stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        record["pid"] = process.pid
        _PROCESSES[service_id] = process
        _write_service(path, record)
        timeout = int(_runtime_config(repo, config).get("ready-timeout-seconds") or 60)
        deadline = time.monotonic() + timeout
        while True:
            ready = _run(commands["ready"], record, timeout=min(timeout, 30))
            if ready.returncode == 0:
                break
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeFailure("dev-service ready 失败：%s" % (ready.stderr or ready.stdout).strip())
            time.sleep(0.1)
        record["state"] = "ready"
        record["ready_at"] = _now()
        record["revision"] += 1
        _write_service(path, record)
        return record
    except Exception:
        # Keep the PID/log/binding for diagnosis and for an explicitly configured
        # stop command.  Never claim that a partial start was stopped.
        failed = _read(path, record)
        failed["state"] = "start-failed"
        failed["revision"] = int(failed.get("revision") or 1) + 1
        _write_service(path, failed)
        raise


def _service_lock(path):
    return FileLock(str(path) + ".lock")


def _write_service(path, record):
    with _service_lock(path):
        atomic_json_write(path, record)


def _assert_service(record, service_id, candidate):
    if not record or record.get("service_id") != service_id or record.get("candidate") != candidate:
        raise RuntimeFailure("service 操作需要匹配的 id 和 candidate")


def _status_service_unlocked(path, record, config):
    if record.get("state") == "stopped":
        return dict(record, running=False, capability="available")
    commands = _service_commands(None, config, required=("status",))
    result = _run(commands["status"], record)
    try:
        payload = json.loads(result.stdout.strip())
        running = payload.get("running") if isinstance(payload, dict) else None
    except ValueError:
        running = None
    if not isinstance(running, bool):
        if result.returncode == 0:
            running = True
        elif result.returncode == 1:
            running = False
        else:
            raise RuntimeFailure("dev-service status 能力 unknown：%s" % (result.stderr or result.stdout).strip())
    return dict(record, running=running, capability="available", status_exit=result.returncode)


def status_service(repo, service_id, candidate, config=None):
    config = _load_config(repo) if config is None else config
    path = _service_path(repo, service_id, config)
    with _service_lock(path):
        record = _read(path)
        _assert_service(record, service_id, candidate)
        return _status_service_unlocked(path, record, config)


def _reap_service(service_id, record):
    if record.get("pid"):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                waited, _ = os.waitpid(int(record["pid"]), os.WNOHANG)
                if waited:
                    break
            except (ChildProcessError, OSError):
                break
            time.sleep(0.02)
    process = _PROCESSES.pop(service_id, None)
    if process is not None:
        process.poll()


def stop_service(repo, service_id, candidate, config=None):
    config = _load_config(repo) if config is None else config
    path = _service_path(repo, service_id, config)
    with _service_lock(path):
        record = _read(path)
        _assert_service(record, service_id, candidate)
        if record.get("state") == "stopped":
            return dict(record, running=False, idempotent=True)
        if record.get("machine_id") != _machine():
            raise RuntimeFailure("跨机器 dev-service stop 能力 unknown，保留")
        already_started = record.get("state") == "stopping" or bool(record.get("stop_started_at"))
        if not already_started:
            # Persist the one-way gate before invoking an external stop command.
            # A retry after any crash observes status and never executes it twice.
            record["state"] = "stopping"
            record["stop_started_at"] = _now()
            record["revision"] = int(record.get("revision") or 0) + 1
            atomic_json_write(path, record)
            commands = _service_commands(repo, config, required=("stop", "status"))
            stopped = _run(commands["stop"], record,
                           timeout=int(_runtime_config(repo, config).get("stop-timeout-seconds") or 30))
            if stopped.returncode != 0:
                raise RuntimeFailure("dev-service stop 已执行但返回失败；保持 stopping 并对账：%s" %
                                     (stopped.stderr or stopped.stdout).strip())
        _reap_service(service_id, record)
        status = _status_service_unlocked(path, record, config)
        if status.get("running") is not False:
            raise RuntimeFailure("stop 后 status 未证明服务已停止；保持 stopping 并对账")
        record["state"] = "stopped"
        record["stopped_at"] = _now()
        record["revision"] = int(record.get("revision") or 0) + 1
        atomic_json_write(path, record)
        return dict(record, running=False, idempotent=already_started)


def quiesce_feature(repo, feature_dir, candidate=None, config=None):
    """Stop exactly bound services, then release exactly bound leases."""
    candidate_oid = candidate.get("candidate") if isinstance(candidate, dict) else candidate
    state = resource_state(repo, feature_dir=feature_dir, config=config)
    stopped, released, blockers = [], [], []
    for service in state["services"]:
        if service.get("state") == "stopped":
            stopped.append(service["service_id"])
            continue
        if candidate_oid and service.get("candidate") != candidate_oid:
            blockers.append("服务 candidate 与 feature-merged 候选不匹配：%s" % service.get("service_id"))
            continue
        try:
            stop_service(repo, service["service_id"], service["candidate"], config)
            stopped.append(service["service_id"])
        except RuntimeFailure as exc:
            blockers.append(str(exc))
    if blockers:
        return {"stopped": stopped, "released": released, "blockers": blockers}
    for lease in state["leases"]:
        if lease.get("released_at"):
            released.append(lease["lease_id"])
            continue
        if candidate_oid and lease.get("candidate") != candidate_oid:
            blockers.append("lease candidate 与 feature-merged 候选不匹配：%s" % lease.get("lease_id"))
            continue
        try:
            release_lease(repo, lease["lease_id"], lease["candidate"], config)
            released.append(lease["lease_id"])
        except RuntimeFailure as exc:
            blockers.append(str(exc))
    return {"stopped": stopped, "released": released, "blockers": blockers}


def reconcile(repo, operation_id=None, candidate=None, apply=False):
    """Read runtime resources, or recover one explicitly classified ID/candidate."""
    state = resource_state(repo)
    if not apply:
        return {"kind": "runtime", "ok": not state["blockers"], "applied": False,
                "state": "needs-attention" if state["blockers"] else "observed", **state}
    if not operation_id or not candidate:
        raise RuntimeFailure("runtime 恢复需要 operation-id（service/lease ID）和 candidate")
    services = [item for item in state["services"] if item.get("service_id") == operation_id]
    leases = [item for item in state["leases"] if item.get("lease_id") == operation_id]
    if len(services) + len(leases) != 1:
        raise RuntimeFailure("runtime 恢复对象不存在或不唯一：%s" % operation_id)
    if services:
        result = stop_service(repo, operation_id, candidate)
        return {"kind": "runtime", "ok": True, "applied": True, "state": "stopped",
                "service_id": operation_id, "idempotent": result.get("idempotent", False)}
    result = release_lease(repo, operation_id, candidate)
    return {"kind": "runtime", "ok": True, "applied": True, "state": "released",
            "lease_id": operation_id, "idempotent": result.get("idempotent", False)}
