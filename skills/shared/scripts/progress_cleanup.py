#!/usr/bin/env python3
"""Authorized, recoverable close/discard cleanup.  This module never merges code.

A preview freezes every destructive input in an OperationJournal stored below the
Git common directory.  Apply rebuilds the plan under the repository operation
lock and requires the same confirmation digest.  Every side effect has an
expected and an observed value; reconcile inspects reality instead of replaying
commands blindly.  Warnings always leave an operation in needs-attention.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_scope
from state_store import (FileLock, LockError, OperationJournal, StateError,
                         append_event_once, atomic_json_write, atomic_write)

SCHEMA = "agent-dev.cleanup-operation/v2"
JOURNAL_SCHEMA = "agent-dev.operation-journal/v1"
STATES = ("authorized", "quiesced", "salvaged", "accounted",
          "worktree-removed", "refs-updated", "dev-compacted", "completed")
REPRO_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "dist", "build",
    ".next", ".turbo", "coverage", "wheelhouse", "keyring", "package-cache",
}
REPRO_FILES = {".DS_Store", ".coverage"}
DEV_CACHE_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "node_modules", ".venv", "venv", "wheelhouse", "keyring", "package-cache",
}
PROTECTED_PREFIXES = ("backup/", "codex/preserve/")
MAX_WALK_FILES = 50000
MAX_SALVAGE_BYTES = 1024 ** 3
OID_RE = re.compile(r"^[0-9a-f]{40}$")
OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class CleanupError(RuntimeError):
    def __init__(self, message, code="cleanup-failed", protected=False):
        super().__init__(message)
        self.code = code
        self.protected = protected


class StaleOperation(CleanupError):
    def __init__(self, message="预检后状态已变化，请重新预检"):
        super().__init__(message, code="stale")


class ProtectedResource(CleanupError):
    def __init__(self, message):
        super().__init__(message, code="protected", protected=True)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(repo, *args, check=False, timeout=30):
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="surrogateescape",
        timeout=timeout,
    )
    if check and result.returncode:
        raise CleanupError((result.stderr or result.stdout or "git 执行失败").strip())
    return result


def _full_oid(repo, revision):
    result = _git(repo, "rev-parse", "--verify", revision + "^{commit}")
    value = result.stdout.strip()
    return value if result.returncode == 0 and OID_RE.fullmatch(value) else None


def _common_dir(repo):
    result = _git(repo, "rev-parse", "--git-common-dir", check=True)
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(repo) / path
    return path.resolve()


def operations_dir(repo):
    return _common_dir(repo) / "agent-dev" / "operations"


def operation_path(repo, operation_id):
    if not isinstance(operation_id, str) or not OPERATION_RE.fullmatch(operation_id):
        raise CleanupError("operation-id 非法", code="invalid-operation")
    return operations_dir(repo) / (operation_id + ".json")


def _operation_lock(repo):
    # Non-blocking acquisition maps cleanly to HTTP 409 and prevents two applies
    # from validating the same worktree before either performs its side effects.
    return FileLock(operations_dir(repo) / "cleanup.lock", blocking=False)


def _status_entries(worktree):
    result = _git(worktree, "status", "--porcelain=v1", "-z", "--ignored", check=True)
    items = result.stdout.split("\0")
    entries = []
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if len(item) < 4:
            continue
        xy, relative = item[:2], item[3:]
        if "R" in xy or "C" in xy:
            old = items[index] if index < len(items) else ""
            index += 1
            entries.append({"xy": xy, "path": relative, "old_path": old})
        else:
            entries.append({"xy": xy, "path": relative})
    return entries


def _is_repro(relative):
    path = Path(relative)
    return (any(part in REPRO_DIRS for part in path.parts)
            or path.name in REPRO_FILES or path.suffix in {".pyc", ".pyo"})


def _lstat_snapshot(path, relative, xy):
    item = {"path": Path(relative).as_posix().rstrip("/"), "xy": xy}
    try:
        stat = path.lstat()
    except FileNotFoundError:
        item["kind"] = "missing"
        return item
    if path.is_symlink():
        item.update(kind="symlink", target=os.readlink(path))
    elif path.is_file():
        item.update(kind="file", size=stat.st_size, sha256=_sha256(path),
                    mode=stat.st_mode & 0o777)
    elif path.is_dir():
        item["kind"] = "directory"
    else:
        item.update(kind="other", mode=stat.st_mode)
    return item


def _worktree_snapshot(worktree, status=None):
    """Hash all changed/untracked/ignored files, including reproducible trees.

    Git may collapse an ignored directory to one ``!! dir/`` entry.  Walking the
    actual tree is therefore required: a new cache or growing .logs file must
    invalidate confirmation just like a source edit.
    """
    root = Path(worktree)
    status = _status_entries(root) if status is None else status
    snapshot = []
    visited = 0

    def add(path, relative, xy):
        nonlocal visited
        visited += 1
        if visited > MAX_WALK_FILES:
            raise CleanupError("worktree 文件过多，无法形成完整删除授权")
        relative = Path(relative).as_posix().rstrip("/")
        if not relative or relative == ".git":
            return
        if path.is_dir() and not path.is_symlink():
            snapshot.append(_lstat_snapshot(path, relative, xy))
            # Directory entries themselves are represented alongside children.
            for directory, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if d != ".git")
                for name in sorted(dirnames):
                    child = Path(directory) / name
                    child_rel = (Path(relative) / child.relative_to(path)).as_posix()
                    if child.is_symlink():
                        add(child, child_rel, xy)
                    else:
                        snapshot.append(_lstat_snapshot(child, child_rel, xy))
                for name in sorted(filenames):
                    child = Path(directory) / name
                    add(child, (Path(relative) / child.relative_to(path)).as_posix(), xy)
        else:
            snapshot.append(_lstat_snapshot(path, relative, xy))

    for entry in status:
        add(root / entry["path"].rstrip("/"), entry["path"], entry["xy"])
        if entry.get("old_path"):
            snapshot.append({"path": entry["old_path"], "xy": "old", "kind": "missing"})
    snapshot.sort(key=lambda item: (item["path"], item["xy"], item["kind"]))
    return snapshot


def _snapshot_digest(snapshot):
    return _digest(snapshot)


def _entry(path, relative, kind, digest=None):
    stat = path.stat()
    return {"abs": str(path), "path": Path(relative).as_posix(), "kind": kind,
            "size": stat.st_size, "sha256": digest or _sha256(path)}


def _validate_dev_symlink(worktree, repo):
    link = Path(worktree) / "dev"
    if not link.is_symlink():
        return {"present": link.exists(), "symlink": False, "valid": False}
    try:
        target = link.resolve(strict=True)
        expected = (Path(repo) / "dev").resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProtectedResource("worktree 的 dev 软链损坏，拒绝清理：%s" % exc)
    if target != expected:
        raise ProtectedResource("worktree 的 dev 软链未指向主工作区 dev，拒绝清理：%s" % link)
    return {"present": True, "symlink": True, "valid": True,
            "target_text": os.readlink(link), "resolved": str(target)}


def _analyze_worktree(worktree, repo):
    worktree, repo = Path(worktree), Path(repo)
    status = _status_entries(worktree)
    dev_link = _validate_dev_symlink(worktree, repo)
    snapshot = _worktree_snapshot(worktree, status)
    by_path = {(item["path"], item["xy"]): item for item in snapshot}
    salvage, symlinks = [], []
    skipped_repro = skipped_same = 0
    logical_bytes = unique_bytes = 0
    unique_hashes = set()
    tracked_changes = 0
    try:
        same_device = worktree.stat().st_dev == (repo / "dev").stat().st_dev
    except OSError:
        same_device = worktree.stat().st_dev == repo.stat().st_dev

    for entry in status:
        xy, relative = entry["xy"], entry["path"].rstrip("/")
        path = worktree / relative
        if xy not in ("??", "!!"):
            tracked_changes += 1
        candidates = [item for key, item in by_path.items()
                      if key[1] == xy and (key[0] == relative or key[0].startswith(relative + "/"))]
        for item in candidates:
            rel = item["path"]
            if rel == "dev" and dev_link.get("valid"):
                continue  # authorized main-dev link: snapshot it, never package/follow it
            current = worktree / rel
            if item["kind"] == "symlink":
                symlinks.append({"path": rel, "target": item["target"]})
                continue
            if item["kind"] != "file":
                continue
            if _is_repro(rel):
                skipped_repro += 1
                continue
            kind = "modified" if xy not in ("??", "!!") else ("ignored" if xy == "!!" else "untracked")
            digest = item["sha256"]
            if kind != "modified":
                main_file = repo / rel
                try:
                    if (main_file.is_file() and not main_file.is_symlink()
                            and main_file.stat().st_size == item["size"]
                            and _sha256(main_file) == digest):
                        skipped_same += 1
                        continue
                except OSError:
                    pass
            logical_bytes += item["size"]
            if digest not in unique_hashes:
                unique_hashes.add(digest)
                unique_bytes += item["size"]
            if unique_bytes > MAX_SALVAGE_BYTES and not same_device:
                raise CleanupError("跨设备 salvage 超过 1 GiB，拒绝在未控制体积时清理")
            salvage.append(_entry(current, rel, kind, digest))
    salvage.sort(key=lambda item: item["path"])
    symlinks.sort(key=lambda item: item["path"])
    logs = [item for item in snapshot
            if (item["path"] == ".logs" or item["path"].startswith(".logs/"))
            and item["kind"] != "directory"]
    return {
        "entries": salvage, "symlinks": symlinks, "snapshot": snapshot,
        "snapshot_sha256": _snapshot_digest(snapshot),
        "tracked_changes": tracked_changes, "dirty_entries": len(status),
        "skipped_repro": skipped_repro, "skipped_same": skipped_same,
        "dev_symlink": dev_link, "logs": logs,
        "logical_bytes": logical_bytes, "unique_bytes": unique_bytes,
        "unique_files": len(unique_hashes), "same_device": same_device,
    }


def _referenced_docs(feature_dir):
    documents = []
    for path in Path(feature_dir).rglob("*.md"):
        try:
            documents.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(documents)


def _is_dev_candidate(impl_dir, path):
    relative = path.relative_to(impl_dir)
    if any(part in DEV_CACHE_DIRS or part.startswith("merge-") for part in relative.parts):
        return True
    return (path.suffix in {".ours", ".theirs", ".orig", ".pyc", ".pyo"}
            or path.name in {".DS_Store", ".coverage"})


def _analyze_dev_cleanup(feature_dir, issue_id=None):
    roots = ([Path(feature_dir) / "impl" / issue_id] if issue_id
             else sorted(path for path in (Path(feature_dir) / "impl").glob("issue-*") if path.is_dir()))
    documents = _referenced_docs(feature_dir)
    entries, skipped_referenced = [], 0
    for impl_dir in roots:
        if not impl_dir.is_dir() or impl_dir.is_symlink():
            continue
        for directory, dirnames, filenames in os.walk(impl_dir, followlinks=False):
            dirnames[:] = [name for name in dirnames
                           if not (Path(directory) / name).is_symlink()]
            for name in filenames:
                path = Path(directory) / name
                if path.is_symlink() or not path.is_file() or not _is_dev_candidate(impl_dir, path):
                    continue
                relative = path.relative_to(impl_dir).as_posix()
                if relative in documents or name in documents:
                    skipped_referenced += 1
                    continue
                entries.append(_entry(path, str(path), "dev-intermediate"))
    entries.sort(key=lambda item: item["abs"])
    return {"entries": entries, "skipped_referenced": skipped_referenced}


def parse_worktree_porcelain(text):
    records, current = [], None
    for line in text.splitlines():
        if not line.strip():
            if current is not None:
                records.append(current)
                current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current is not None:
                records.append(current)
            current = {"path": value, "detached": False, "locked": False,
                       "prunable": False, "bare": False, "branch": None, "head": None}
        elif current is not None:
            if key == "HEAD":
                current["head"] = value
            elif key == "branch":
                current["branch"] = value
            elif key in ("detached", "bare"):
                current[key] = True
            elif key in ("locked", "prunable"):
                current[key] = True
                current[key + "_reason"] = value or None
    if current is not None:
        records.append(current)
    return records


def discover_resources(repo):
    """Return all registered worktrees, local refs, remote refs and exact upstreams."""
    repo = Path(repo).resolve()
    listed = _git(repo, "worktree", "list", "--porcelain", check=True).stdout
    worktrees = parse_worktree_porcelain(listed)
    main_path = Path(worktrees[0]["path"]).resolve() if worktrees else repo
    for index, item in enumerate(worktrees):
        item["path"] = str(Path(item["path"]).resolve())
        item["branch_name"] = item["branch"][len("refs/heads/"):] if item["branch"] and item["branch"].startswith("refs/heads/") else None
        item["branchless"] = not item["branch"]
        item["main"] = index == 0 or Path(item["path"]) == repo
        protected = item["main"] or item["bare"]
        reason = "主工作区" if item["main"] else ("bare worktree" if item["bare"] else None)
        name = item["branch_name"]
        if name and (name == "main" or name.startswith(PROTECTED_PREFIXES)):
            protected, reason = True, "受保护分支"
        item["protected"], item["protected_reason"] = bool(protected), reason

    local = {}
    result = _git(repo, "for-each-ref", "refs/heads",
                  "--format=%(refname)%00%(refname:short)%00%(objectname)%00%(upstream:short)%00%(upstream)",
                  check=True)
    for line in result.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) != 5:
            continue
        ref, name, oid, upstream, upstream_ref = parts
        item = {"ref": ref, "name": name, "oid": oid,
                "upstream": upstream or None, "upstream_ref": upstream_ref or None,
                "upstream_oid": None, "remote_delete": None,
                "protected": name == "main" or name.startswith(PROTECTED_PREFIXES)}
        if upstream_ref:
            remote_result = _git(repo, "rev-parse", "--verify", upstream_ref)
            if remote_result.returncode == 0 and OID_RE.fullmatch(remote_result.stdout.strip()):
                item["upstream_oid"] = remote_result.stdout.strip()
        if upstream and upstream_ref:
            remote = _git(repo, "config", "--get", "branch.%s.remote" % name)
            merge = _git(repo, "config", "--get", "branch.%s.merge" % name)
            remote_name = remote.stdout.strip() if remote.returncode == 0 else ""
            merge_ref = merge.stdout.strip() if merge.returncode == 0 else ""
            expected_upstream = None
            if remote_name and remote_name != "." and merge_ref.startswith("refs/heads/"):
                short = merge_ref[len("refs/heads/"):]
                expected_upstream = remote_name + "/" + short
                if upstream == expected_upstream and upstream_ref == "refs/remotes/" + expected_upstream:
                    item["remote_delete"] = {"remote": remote_name, "ref": merge_ref,
                                             "tracking_ref": upstream_ref, "oid": item["upstream_oid"]}
        local[name] = item
    remote = {}
    remote_names = _git(repo, "remote").stdout.splitlines()
    result = _git(repo, "for-each-ref", "refs/remotes",
                  "--format=%(refname)%00%(objectname)%00%(symref)", check=True)
    for line in result.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) >= 2 and OID_RE.fullmatch(parts[1]):
            remote[parts[0]] = {"ref": parts[0], "oid": parts[1],
                                "symref": parts[2] if len(parts) > 2 and parts[2] else None}
            for remote_name in sorted(remote_names, key=len, reverse=True):
                prefix = "refs/remotes/" + remote_name + "/"
                if parts[0].startswith(prefix):
                    remote[parts[0]].update(remote=remote_name, name=parts[0][len(prefix):])
            if "name" not in remote[parts[0]] and parts[0].startswith("refs/remotes/"):
                remainder = parts[0][len("refs/remotes/"):]
                if "/" in remainder:
                    remote_name, branch_name = remainder.split("/", 1)
                    remote[parts[0]].update(remote=remote_name, name=branch_name)
    return {"repo": str(repo), "main_worktree": str(main_path),
            "worktrees": worktrees, "local_branches": local, "remote_branches": remote}


def _read_config(repo):
    import agents_config
    path = Path(repo) / "dev" / "agents-config.md"
    try:
        return agents_config.read_first_yaml_block(path), path
    except agents_config.ConfigError:
        try:
            return agents_config.read_frontmatter(path), path
        except agents_config.ConfigError:
            return agents_config.parse_yaml_subset(path.read_text(encoding="utf-8")), path


def _ticket_target(repo, ticket, config):
    import ticket_context
    data = dict(config)
    closeout = dict(data.get("feature-closeout") or {})
    closeout.setdefault("mode", "server-enforced")
    data["feature-closeout"] = closeout
    return ticket_context.target_branch(data, ticket.get("fm") or {}, ticket["feature_dir"])


def _expected_branch(ticket):
    explicit = str((ticket.get("fm") or {}).get("branch") or "").strip()
    if explicit:
        return explicit
    return "feat/%s-%s" % (artifact_scope.branch_key(ticket["feature_dir"], (ticket.get("fm") or {}).get("feature-slug")), ticket["id"])


def _branch_matches(ticket, name):
    if name == _expected_branch(ticket):
        return True
    if ticket["id"] not in name:
        return False
    if artifact_scope.campaign_id(ticket["feature_dir"]):
        return False
    module, slug = ticket["feature"].split("/", 1)
    return slug in name or module in name


def _merged_event_branches(ticket, target):
    """Use delivery's explicit branch/target/host record only when it is unique."""
    path = Path(ticket["feature_dir"]) / "progress.jsonl"
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    branches = []
    for event in events:
        if event.get("event") != "merged" or event.get("issue") != ticket["id"]:
            continue
        if (event.get("target") or event.get("target_branch")) != target:
            continue
        branch = event.get("branch")
        if (isinstance(branch, str) and branch and OID_RE.fullmatch(str(event.get("commit") or ""))
                and OID_RE.fullmatch(str(event.get("source_head") or event.get("head") or ""))):
            branches.append(branch)
    return list(dict.fromkeys(branches))


def _exact_merge_record(repo, ticket, branch, branch_oid, target, target_oid):
    path = Path(ticket["feature_dir"]) / "progress.jsonl"
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    for event in reversed(events):
        if event.get("event") != "merged" or event.get("issue") != ticket["id"]:
            continue
        commit = str(event.get("commit") or "")
        source = str(event.get("source_head") or event.get("head") or "")
        event_target = event.get("target") or event.get("target_branch")
        event_branch = event.get("branch")
        if not (OID_RE.fullmatch(commit) and OID_RE.fullmatch(source)):
            continue
        if event_target != target or event_branch != branch:
            continue
        if branch_oid and source != branch_oid:
            continue
        if _full_oid(repo, commit) != commit or _full_oid(repo, source) != source:
            continue
        if _git(repo, "merge-base", "--is-ancestor", commit, target_oid).returncode != 0:
            continue
        parents = _git(repo, "rev-list", "--parents", "-n", "1", commit, check=True).stdout.split()[1:]
        target_before = str(event.get("target_before") or "")
        if target_before and (not OID_RE.fullmatch(target_before) or target_before not in parents):
            continue
        source_tree = str(event.get("source_tree") or "")
        if source_tree:
            actual_tree = _git(repo, "rev-parse", source + "^{tree}")
            if actual_tree.returncode != 0 or actual_tree.stdout.strip() != source_tree:
                continue
        if commit == source or source in parents:
            return {"kind": "record", "commit": commit, "source_head": source,
                    "branch": event_branch, "target": event_target, "host": event.get("host"),
                    "event": event.get("event_id") or event.get("id")}
    return None


def _merge_proof(repo, ticket, branch, branch_oid, target, target_oid):
    if branch_oid and target_oid and branch != target:
        if _git(repo, "merge-base", "--is-ancestor", branch_oid, target_oid).returncode == 0:
            return {"kind": "ancestor", "source_head": branch_oid, "target_head": target_oid}
    return _exact_merge_record(repo, ticket, branch, branch_oid, target, target_oid)


def _feature_candidate(repo, feature_dir, round_name=None):
    integration = Path(feature_dir) / "integration"
    if round_name:
        # 多轮复用同一 feature 目录时只看本轮候选；旧轮次的结论产物保留在原地，
        # 不得让它们阻塞或顶替本轮清理。
        paths = [integration / round_name / "candidate.json"]
    else:
        direct = integration / "candidate.json"
        paths = [direct] if direct.is_file() else sorted(integration.glob("*/candidate.json"))
    candidates = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CleanupError("候选 JSON 不可读：%s" % exc)
        if value.get("merged_commit"):
            candidates.append((path, value))
    if len(candidates) != 1:
        raise CleanupError("feature-dir 清理要求本轮唯一、已登记的合并候选；找到 %d 个" % len(candidates))
    path, candidate = candidates[0]
    required = ("candidate", "candidate_tree", "integration_branch", "main_branch", "merged_commit")
    if any(not isinstance(candidate.get(key), str) or not candidate[key] for key in required):
        raise CleanupError("候选缺少 integration/candidate/main/merged 身份")
    for key in ("candidate", "candidate_tree", "merged_commit"):
        if not OID_RE.fullmatch(candidate[key]):
            raise CleanupError("候选 %s 必须是完整 Git OID" % key)
    if _full_oid(repo, candidate["candidate"]) != candidate["candidate"]:
        raise CleanupError("候选提交不存在")
    if _full_oid(repo, candidate["merged_commit"]) != candidate["merged_commit"]:
        raise CleanupError("feature-merged 提交不存在")
    if _full_oid(repo, candidate["integration_branch"]) != candidate["candidate"]:
        raise CleanupError("集成分支在候选后有后续提交，拒绝 feature 清理")
    target_oid = _full_oid(repo, candidate["main_branch"])
    if not target_oid or _git(repo, "merge-base", "--is-ancestor", candidate["merged_commit"], target_oid).returncode != 0:
        raise CleanupError("feature-merged 提交不在目标分支中")
    event_path = Path(feature_dir) / "progress.jsonl"
    try:
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise CleanupError("需求事件流不可读：%s" % exc)
    matching = [event for event in events if event.get("event") == "feature-merged"
                and candidate["merged_commit"] == str(event.get("commit") or "")]
    if not matching:
        # Legacy records may have stored a unique prefix; full candidate identity
        # remains authoritative, while v2 events always carry the full commit.
        matching = [event for event in events if event.get("event") == "feature-merged"
                    and str(event.get("commit") or "")
                    and candidate["merged_commit"].startswith(str(event["commit"]))]
    if len(matching) != 1:
        raise CleanupError("缺少唯一且精确对应候选的 feature-merged 事件")
    event = matching[0]
    if candidate.get("merge_operation") and event.get("operation_id") != candidate["merge_operation"]:
        raise CleanupError("feature-merged 事件的 operation_id 与候选不匹配")
    strategy = candidate.get("merge_strategy") or event.get("strategy") or "no-ff"
    if strategy not in ("no-ff", "squash"):
        raise CleanupError("候选 merge_strategy 必须为 no-ff 或 squash")
    if event.get("strategy") and event["strategy"] != strategy:
        raise CleanupError("feature-merged strategy 与候选不一致")
    if strategy == "squash" and (event.get("candidate") != candidate["candidate"]
                                 or event.get("candidate_tree") != candidate["candidate_tree"]):
        raise CleanupError("squash 事件必须精确绑定 candidate 和 candidate_tree")
    parents = _git(repo, "rev-list", "--parents", "-n", "1", candidate["merged_commit"], check=True).stdout.split()[1:]
    main_before = candidate.get("main_head")
    if main_before is not None and not OID_RE.fullmatch(main_before):
        raise CleanupError("候选 main_head 必须是完整 Git OID")
    if strategy == "no-ff":
        # Legacy no-ff candidates predate main_head; their first parent is the
        # frozen main identity. v2 squash never receives this compatibility path.
        main_before = main_before or (parents[0] if len(parents) == 2 else None)
        if not main_before or parents != [main_before, candidate["candidate"]]:
            raise CleanupError("no-ff 合并父提交必须依次为冻结 main HEAD 与 candidate")
    else:
        if not main_before or parents != [main_before]:
            raise CleanupError("squash 提交唯一父提交必须是候选冻结的 main_head")
    tree = _git(repo, "rev-parse", candidate["candidate"] + "^{tree}", check=True).stdout.strip()
    if tree != candidate["candidate_tree"]:
        raise CleanupError("候选 tree 与提交不一致")
    final_tree = _git(repo, "rev-parse", candidate["merged_commit"] + "^{tree}", check=True).stdout.strip()
    if final_tree != candidate["candidate_tree"]:
        raise CleanupError("最终 tree 不等于 candidate tree，拒绝清理")
    proven = _git(repo, "merge-tree", "--write-tree", main_before, candidate["candidate"], check=True).stdout.splitlines()
    if not proven or proven[0].strip() != candidate["candidate_tree"]:
        raise CleanupError("git merge-tree 无法证明最终结果为候选 clean 三方合并 tree")
    candidate["merge_strategy"] = strategy
    candidate["main_head"] = main_before
    # Another feature sharing this integration branch is a shared requirement;
    # candidate validity for one feature never authorizes deleting the other's line.
    for other in artifact_scope.scoped_glob(repo, "integration/**/candidate.json"):
        try:
            if other.resolve() == path.resolve():
                continue
            try:
                other.resolve().relative_to(Path(feature_dir).resolve())
                continue  # earlier rounds of the same feature are not another tenant
            except ValueError:
                pass
            value = json.loads(other.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("integration_branch") == candidate["integration_branch"]:
            raise CleanupError("integration 分支被其他需求/轮次共享，拒绝清理")
    candidate = copy.deepcopy(candidate)
    candidate["path"] = str(path)
    candidate["target_oid"] = target_oid
    return candidate


def _session_runtime_state(repo, subject, worktrees):
    try:
        import host_adapters
        sessions = host_adapters.session_resource_state(
            repo, subject=subject, worktrees=[item["path"] for item in worktrees])
    except ImportError:
        sessions = {"status": "unknown", "sessions": [], "blockers": ["宿主能力模块不可用"]}
    except Exception as exc:
        sessions = {"status": "unknown", "sessions": [], "blockers": [str(exc)]}
    try:
        import integration_runtime
        runtime = integration_runtime.resource_state(
            repo, feature_dir=subject.get("feature_dir"),
            worktrees=[item["path"] for item in worktrees])
    except ImportError:
        runtime = {"status": "unknown", "services": [], "leases": [], "blockers": ["runtime 模块不可用"]}
    except Exception as exc:
        runtime = {"status": "unknown", "services": [], "leases": [], "blockers": [str(exc)]}
    return {"sessions": sessions, "runtime": runtime}


def _new_base(repo, intent, subject):
    return {"ok": True, "eligible": False, "intent": intent, "subject": subject,
            "issue": subject.get("id") if subject.get("kind") == "issue" else None,
            "title": subject.get("title"), "status": subject.get("status"),
            "feature_dir": subject.get("feature_dir"), "branch": None,
            "target_branch": None, "worktree": None, "delete_branch": False,
            "warnings": [], "remaining_paths": [], "will": [],
            "protected": False, "operation_id": None, "confirm": None,
            "state": "preview", "_repo": str(Path(repo).resolve())}


def make_plan(repo, ticket=None, feature_dir=None, intent="close", operation_id=None,
              authorize=True, recovery_authorization=None, authorized_at=None, round=None):
    """Build an immutable close/discard plan; code merge is intentionally absent."""
    repo = Path(repo).resolve()
    if recovery_authorization is not None:
        return _plan_from_authorization(repo, recovery_authorization, operation_id)
    if intent not in ("close", "discard"):
        raise CleanupError("intent 必须是 close 或 discard", code="invalid-intent")
    if (ticket is None) == (feature_dir is None):
        raise CleanupError("issue 与 feature-dir 必须且只能提供一个")
    if ticket is not None:
        ticket = dict(ticket)
        ticket["feature_dir"] = str(Path(ticket["feature_dir"]).resolve())
        subject = {"kind": "issue", "id": ticket["id"], "key": ticket["key"],
                   "title": ticket.get("title"), "status": ticket.get("status"),
                   "feature": ticket["feature"], "feature_dir": ticket["feature_dir"],
                   "issue_path": str(Path(ticket["issue_path"]).resolve())}
    else:
        feature_path = Path(feature_dir).resolve()
        subject = {"kind": "feature", "id": feature_path.name, "key": feature_path.name,
                   "title": feature_path.name, "status": "feature",
                   "feature": artifact_scope.scope_key(feature_path),
                   "feature_dir": str(feature_path), "issue_path": None,
                   "round": str(round).strip() if round else None}
        if intent != "close":
            raise CleanupError("feature-dir 只支持 intent=close", code="invalid-intent")
    plan = _new_base(repo, intent, subject)
    plan["_authorized_at"] = authorized_at or _now()
    try:
        config, config_path = _read_config(repo)
        resources = discover_resources(repo)
        worktrees, refs = [], []
        branch = None
        target_oid = None
        candidate = None
        if ticket is not None:
            branch_names = [name for name in resources["local_branches"] if _branch_matches(ticket, name)]
            if len(branch_names) > 1:
                raise CleanupError("票对应多个本地分支，拒绝猜测唯一资源")
            branch = branch_names[0] if branch_names else _expected_branch(ticket)
            plan["branch"] = branch
            target = _ticket_target(repo, ticket, config)
            plan["target_branch"] = target
            target_oid = _full_oid(repo, target)
            if not target_oid:
                raise CleanupError("票目标分支不存在：%s；不会退回 main/query" % target)
            if branch not in resources["local_branches"]:
                recorded_branches = _merged_event_branches(ticket, target)
                if len(recorded_branches) == 1:
                    branch = recorded_branches[0]
                    plan["branch"] = branch
                elif len(recorded_branches) > 1:
                    raise CleanupError("merged 事件为票记录了多个 source branch，拒绝猜测")
            if branch == target or branch == config.get("main-branch") or branch.startswith(PROTECTED_PREFIXES):
                raise ProtectedResource("main/target/backup/preserve 分支永不作为票资源删除")
            local = resources["local_branches"].get(branch)
            branch_oid = local["oid"] if local else None
            worktrees = [item for item in resources["worktrees"] if item.get("branch_name") == branch]
            if len(worktrees) > 1:
                raise CleanupError("同一票分支登记了多个 worktree，拒绝猜测")
            ambiguous = [item for item in resources["worktrees"]
                         if item["branchless"] and not item["main"]
                         and (ticket["id"] in item["path"] or item["path"] == str(Path(worktrees[0]["path"]) if worktrees else ""))]
            if ambiguous:
                raise CleanupError("detached/branchless worktree 不能按票号猜测归属；仅 reconcile 可按 journal 恢复")
            remote_only = [] if local else [item for item in resources["remote_branches"].values()
                                             if item.get("name") and _branch_matches(ticket, item["name"])]
            if not local and remote_only:
                raise CleanupError("存在 remote-only 分支；没有本地精确 upstream，禁止降级为 dev-only")
            if ticket.get("status") == "proposed":
                raise CleanupError("proposed 尚未开工，不提供清理")
            if intent == "close":
                if ticket.get("status") != "done" or ticket.get("closed_as"):
                    raise CleanupError("普通 close 只接受真实 done 票；中途开发必须显式 intent=discard")
                proof = _merge_proof(repo, ticket, branch, branch_oid, target, target_oid)
                if not proof:
                    raise CleanupError("done 不等于已合并：需要目标分支祖先关系或可验证的精确 merged 记录")
                plan["merge_proof"] = proof
                plan["merged_into"] = target
                plan["delete_branch"] = bool(local)
            else:
                if ticket.get("status") == "done" and not ticket.get("closed_as"):
                    raise CleanupError("done 票不能使用 discard 改写历史")
                plan["delete_branch"] = bool(local)
            if local:
                refs.append(dict(local))
            patch_base = None
            if intent == "discard" and local:
                base = _git(repo, "merge-base", branch_oid, target_oid)
                if base.returncode != 0 or not OID_RE.fullmatch(base.stdout.strip()):
                    raise CleanupError("discard 无法确定完整 branch.patch 基线")
                patch_base = base.stdout.strip()
            plan["_patch_base"] = patch_base
        else:
            candidate = _feature_candidate(repo, subject["feature_dir"], subject.get("round"))
            branch = candidate["integration_branch"]
            plan["branch"] = branch
            plan["target_branch"] = candidate["main_branch"]
            plan["feature_candidate"] = candidate
            plan["merge_proof"] = {"kind": "feature-merged", "commit": candidate["merged_commit"],
                                   "source_head": candidate["candidate"],
                                   "strategy": candidate["merge_strategy"],
                                   "main_head": candidate["main_head"], "tree": candidate["candidate_tree"]}
            plan["merged_into"] = candidate["main_branch"]
            target_oid = candidate["target_oid"]
            local = resources["local_branches"].get(branch)
            if local:
                refs.append(dict(local))
            plan["delete_branch"] = bool(local)
            worktrees = [item for item in resources["worktrees"] if item.get("branch_name") == branch]
            if len(worktrees) > 1:
                raise CleanupError("integration worktree 被多个检出共享，拒绝清理")
            plan["_patch_base"] = None

        analyses = []
        for item in worktrees:
            if item["protected"]:
                raise ProtectedResource("主工作区/bare/受保护 worktree 永不删除：%s" % item["path"])
            if item["locked"]:
                raise CleanupError("locked worktree 需先由持有者解锁：%s" % item["path"])
            if not item["branch"] or item["detached"]:
                if not recovery_authorization:
                    raise CleanupError("detached worktree 不能猜测票归属")
            if Path(item["path"]) == repo:
                raise ProtectedResource("主工作区永不删除")
            if Path(item["path"]).is_dir():
                analysis = _analyze_worktree(item["path"], repo)
            elif item["prunable"]:
                analysis = {"entries": [], "symlinks": [], "snapshot": [],
                            "snapshot_sha256": _digest([]), "tracked_changes": 0,
                            "dirty_entries": 0, "skipped_repro": 0, "skipped_same": 0,
                            "dev_symlink": {"present": False, "symlink": False, "valid": False},
                            "logs": [], "logical_bytes": 0, "unique_bytes": 0,
                            "unique_files": 0, "same_device": True}
            else:
                raise CleanupError("worktree 路径消失但不是 prunable 登记，请先对账")
            analyses.append({"resource": dict(item), "analysis": analysis})
        if analyses:
            plan["worktree"] = analyses[0]["resource"]["path"]
        dev = _analyze_dev_cleanup(subject["feature_dir"], ticket["id"] if ticket else None)
        import artifact_gc
        generations = artifact_gc.preview_generations(subject["feature_dir"])
        known_dev_paths = {entry["abs"] for entry in dev["entries"]}
        for artifact in generations["candidates"]:
            if artifact["abs"] not in known_dev_paths:
                dev["entries"].append({"abs": artifact["abs"], "path": artifact["path"],
                                       "kind": "generation:" + artifact["kind"],
                                       "size": artifact["size"], "sha256": artifact["sha256"]})
                known_dev_paths.add(artifact["abs"])
        dev["entries"].sort(key=lambda item: item["abs"])
        plan["artifact_gc"] = {"files": generations["files"], "bytes": generations["bytes"],
                               "protected": len(generations["protected"]), "warnings": generations["warnings"]}
        session_runtime = _session_runtime_state(repo, subject, worktrees)
        plan["session_runtime"] = session_runtime
        blockers = list(session_runtime["sessions"].get("blockers") or [])
        if ticket is not None:
            blockers += list(session_runtime["runtime"].get("blockers") or [])
        if blockers:
            raise CleanupError("；".join(str(item) for item in blockers))
        plan["runtime"] = session_runtime["runtime"]
        plan["resources"] = {
            "worktrees": [{key: item["resource"].get(key) for key in
                           ("path", "head", "branch", "detached", "locked", "prunable", "bare", "branchless")}
                          for item in analyses],
            "local_ref": refs[0]["ref"] if refs else None,
            "upstream": refs[0].get("upstream") if refs else None,
            "remote_only": False,
        }
        salvage_files = sum(len(item["analysis"]["entries"]) for item in analyses)
        salvage_unique = sum(item["analysis"]["unique_files"] for item in analyses)
        salvage_bytes = sum(item["analysis"]["unique_bytes"] for item in analyses)
        plan["salvage"] = {"files": salvage_files, "unique_files": salvage_unique,
                           "bytes": salvage_bytes, "logical_bytes": sum(item["analysis"]["logical_bytes"] for item in analyses),
                           "method": "hardlink" if all(item["analysis"]["same_device"] for item in analyses) else "copy",
                           "sample": [entry["path"] for item in analyses for entry in item["analysis"]["entries"][:8]],
                           "logs": sum(len(item["analysis"]["logs"]) for item in analyses),
                           "dev_symlink": analyses[0]["analysis"]["dev_symlink"] if analyses else None}
        plan["dev_cleanup"] = {"files": len(dev["entries"]),
                               "bytes": sum(item["size"] for item in dev["entries"]),
                               "sample": [Path(item["abs"]).name for item in dev["entries"][:8]],
                               "skipped_referenced": dev["skipped_referenced"]}
        if refs and refs[0].get("upstream") and not refs[0].get("remote_delete"):
            plan["warnings"].append("上游不是可证明的精确远端 upstream；只处理本地 ref，远端保留并需关注")
        if analyses:
            plan["will"].append("停止并核对活动会话/runtime，随后 salvage（含 .logs）并移除 worktree")
        if refs:
            plan["will"].append("用 expected-old CAS 删除本地 ref；仅对 OID 不变的精确 upstream 删除远端")
        if dev["entries"]:
            plan["will"].append("清理 dev 中间过程文件 %d 个" % len(dev["entries"]))
        if intent == "discard":
            plan["will"].append("salvage 和资源清理完成后才将票改为 deferred；不解锁依赖")
        if not analyses and not refs and not dev["entries"]:
            raise CleanupError("没有代码、会话/runtime 或可清理 dev 资源")
        plan["eligible"] = True
        plan["state"] = "preview"
        plan["remaining_paths"] = [item["resource"]["path"] for item in analyses]
        plan["_resources"] = resources
        plan["_analyses"] = analyses
        plan["_refs"] = refs
        plan["_dev"] = dev
        plan["_config_path"] = str(config_path)
        plan["_config_sha256"] = _sha256(config_path)
        plan["_target_oid"] = target_oid
        return _finalize_plan(repo, plan, operation_id, authorize)
    except ProtectedResource as exc:
        plan.update(ok=False, eligible=False, protected=True, reason=str(exc), error=str(exc), state="protected")
        return plan
    except (CleanupError, OSError, ValueError) as exc:
        plan.update(ok=False, eligible=False, reason=str(exc), error=str(exc), state="blocked")
        return plan


def _authorization(repo, plan):
    ticket_hash = None
    if plan["subject"].get("issue_path"):
        ticket_hash = _sha256(plan["subject"]["issue_path"])
    auth = {
        "schema": SCHEMA, "intent": plan["intent"], "subject": plan["subject"],
        "authorized_at": plan["_authorized_at"],
        "ticket": {"path": plan["subject"].get("issue_path"), "sha256": ticket_hash,
                   "status": plan["subject"].get("status"),
                   "config_path": plan["_config_path"], "config_sha256": plan["_config_sha256"]},
        "target": {"branch": plan["target_branch"], "oid": plan["_target_oid"]},
        "refs": plan["_refs"], "worktrees": plan["_analyses"], "dev": plan["_dev"],
        "session_runtime": plan["session_runtime"],
        "patch_base": plan.get("_patch_base"),
        "feature_candidate": plan.get("feature_candidate"),
        "view": {key: copy.deepcopy(value) for key, value in plan.items()
                 if not key.startswith("_") and key not in ("confirm", "operation_id")},
    }
    return auth


def _finalize_plan(repo, plan, operation_id, authorize):
    auth = _authorization(repo, plan)
    confirm = _digest(auth)
    plan["_auth"] = auth
    plan["confirm"] = confirm
    plan["operation_id"] = operation_id or "cleanup-" + uuid.uuid4().hex
    if authorize:
        authorize_plan(plan, plan["operation_id"])
    plan["state"] = "authorized"
    return plan


def authorize_plan(plan, operation_id=None):
    if not plan.get("eligible") or not plan.get("_auth"):
        raise CleanupError(plan.get("reason") or "计划不可授权")
    repo = plan["_repo"]
    operation_id = operation_id or plan.get("operation_id") or "cleanup-" + uuid.uuid4().hex
    path = operation_path(repo, operation_id)
    journal = OperationJournal.create(path, operation_id, STATES[:-1])
    authorized = journal.steps[0]
    if authorized["status"] == "done":
        journal.advance("authorized", expected=plan["_auth"])
    else:
        journal.advance("authorized", expected=plan["_auth"],
                        observed={"confirm": plan["confirm"]})
    plan["operation_id"] = operation_id
    plan["state"] = "authorized"
    plan["journal"] = str(path)
    return plan


def public_plan(plan):
    if plan.get("_auth") and plan["_auth"].get("view"):
        result = copy.deepcopy(plan["_auth"]["view"])
        result.update(confirm=plan.get("confirm"), operation_id=plan.get("operation_id"),
                      state=plan.get("state"), journal=plan.get("journal"))
        return result
    return {key: copy.deepcopy(value) for key, value in plan.items() if not key.startswith("_")}


def _plan_from_authorization(repo, auth, operation_id=None):
    if not isinstance(auth, dict) or auth.get("schema") != SCHEMA:
        raise CleanupError("journal 缺少受支持的 cleanup 授权")
    plan = copy.deepcopy(auth.get("view") or {})
    plan.update(_repo=str(Path(repo).resolve()), _auth=copy.deepcopy(auth),
                _refs=copy.deepcopy(auth["refs"]), _analyses=copy.deepcopy(auth["worktrees"]),
                _dev=copy.deepcopy(auth["dev"]), _patch_base=auth.get("patch_base"),
                _target_oid=auth["target"]["oid"],
                _config_path=auth["ticket"]["config_path"],
                _config_sha256=auth["ticket"]["config_sha256"],
                confirm=_digest(auth), operation_id=operation_id,
                eligible=True, ok=True, state=operation_state(OperationJournal.load(operation_path(repo, operation_id))) if operation_id else "authorized")
    return plan


def _load_authorized(repo, operation_id):
    journal = OperationJournal.load(operation_path(repo, operation_id))
    step = next((item for item in journal.steps if item["id"] == "authorized"), None)
    if not step or step["status"] != "done" or "expected" not in step:
        raise CleanupError("journal 尚未完成授权，拒绝恢复", code="unauthorized")
    auth = step["expected"]
    if _digest(auth) != step.get("observed", {}).get("confirm"):
        raise CleanupError("journal 授权摘要不一致", code="unauthorized")
    return journal, auth


def operation_state(journal):
    if journal.state == "completed":
        return "completed"
    done = [step["id"] for step in journal.steps if step["status"] == "done"]
    return done[-1] if done else "authorized"


def _worktree_registration(repo, path):
    target = str(Path(path).resolve())
    return any(item["path"] == target for item in discover_resources(repo)["worktrees"])


def _verify_worktree_unchanged(item):
    resource, expected = item["resource"], item["analysis"]
    path = Path(resource["path"])
    if not path.is_dir():
        if resource.get("prunable") and not expected["snapshot"]:
            return
        raise StaleOperation("删除前 worktree 已变化或消失：%s" % path)
    head = _git(path, "rev-parse", "HEAD")
    if resource.get("head") and (head.returncode != 0 or head.stdout.strip() != resource["head"]):
        raise StaleOperation("journal 授权 worktree HEAD 已变化：%s" % path)
    current = None
    # Hashing does not need the main repository except for dev-link validation;
    # validate that link separately against the authorization's resolved target.
    dev = expected.get("dev_symlink") or {}
    if dev.get("symlink"):
        link = path / "dev"
        if not link.is_symlink() or str(link.resolve()) != dev.get("resolved"):
            raise StaleOperation("删除前 dev 软链已变化：%s" % link)
    snapshot = _worktree_snapshot(path)
    if _snapshot_digest(snapshot) != expected["snapshot_sha256"]:
        raise StaleOperation("删除前 worktree/ignored/.logs 状态已变化：%s" % path)


def _validate_salvage_package(target, operation_id):
    manifest_path = Path(target) / "_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if manifest.get("operation_id") != operation_id:
        raise CleanupError("salvage 目录属于另一操作：%s" % target)
    for item in manifest.get("files") or []:
        obj = Path(target) / "objects" / item["sha256"]
        if not obj.is_file() or obj.is_symlink() or _sha256(obj) != item["sha256"]:
            raise CleanupError("salvage 对象缺失或损坏：%s" % item["path"])
    if manifest.get("branch_patch") and not (Path(target) / "branch.patch").is_file():
        raise CleanupError("salvage 缺 branch.patch")
    if manifest.get("uncommitted_patch") and not (Path(target) / "uncommitted.patch").is_file():
        raise CleanupError("salvage 缺 uncommitted.patch")
    return manifest


def _salvage(repo, plan, operation_id):
    analyses = plan["_analyses"]
    needs_patch = bool(plan["intent"] == "discard" and plan["_refs"])
    needed = needs_patch or any(item["analysis"]["entries"] or item["analysis"]["tracked_changes"]
                                or item["analysis"]["symlinks"] for item in analyses)
    if not needed:
        return {"required": False, "packages": []}
    label = plan["issue"] or ("feature-" + plan["subject"]["id"])
    final = Path(repo) / "dev" / ".salvage" / (label + "-" + operation_id)
    staging = Path(repo) / "dev" / ".salvage" / ".staging" / operation_id
    existing = _validate_salvage_package(final, operation_id) if final.exists() else None
    if existing:
        return {"required": True, "packages": [str(final)], "manifest": existing, "resumed": True}
    staging.mkdir(parents=True, exist_ok=True)
    files_root = staging / "files"
    manifest_files = []
    for item in analyses:
        _verify_worktree_unchanged(item)
        analysis = item["analysis"]
        for entry in analysis["entries"]:
            source = Path(entry["abs"])
            if not source.is_file() or source.is_symlink() or _sha256(source) != entry["sha256"]:
                raise StaleOperation("salvage 前文件已变化：%s" % entry["path"])
            obj = staging / "objects" / entry["sha256"]
            if obj.exists() and (obj.is_symlink() or _sha256(obj) != entry["sha256"]):
                raise CleanupError("salvage staging 对象损坏：%s" % obj)
            if not obj.exists():
                obj.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source, obj)
                except OSError:
                    shutil.copy2(source, obj, follow_symlinks=False)
            destination = files_root / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                try:
                    os.link(obj, destination)
                except OSError:
                    shutil.copy2(obj, destination, follow_symlinks=False)
            manifest_files.append({key: entry[key] for key in ("path", "kind", "size", "sha256")})
        if analysis["tracked_changes"]:
            patch = _git(resource_path(item), "diff", "--binary", "HEAD", check=True).stdout
            atomic_write(staging / "uncommitted.patch", patch)
    if needs_patch:
        ref = plan["_refs"][0]
        patch = _git(repo, "diff", "--binary", plan["_patch_base"], ref["oid"], check=True).stdout
        atomic_write(staging / "branch.patch", patch)
    manifest = {
        "schema": "agent-dev.salvage/v2", "operation_id": operation_id,
        "issue": plan["issue"], "subject": plan["subject"], "branch": plan["branch"],
        "target": plan["target_branch"], "created_at": _now(),
        "reason": "授权清场前置 salvage；未跟随任何 symlink；.logs 默认纳入",
        "intent": plan["intent"], "files": manifest_files,
        "worktrees": [{"path": item["resource"]["path"], "head": item["resource"].get("head"),
                       "snapshot_sha256": item["analysis"]["snapshot_sha256"],
                       "symlinks": item["analysis"]["symlinks"],
                       "logs": item["analysis"]["logs"]} for item in analyses],
        "branch_patch": ({"base": plan["_patch_base"], "head": plan["_refs"][0]["oid"],
                          "file": "branch.patch"} if needs_patch else None),
        "uncommitted_patch": any(item["analysis"]["tracked_changes"] for item in analyses),
        "skipped_reproducible": sum(item["analysis"]["skipped_repro"] for item in analyses),
        "skipped_already_in_main": sum(item["analysis"]["skipped_same"] for item in analyses),
    }
    atomic_json_write(staging / "_manifest.json", manifest)
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging, final)
    except FileExistsError:
        existing = _validate_salvage_package(final, operation_id)
        if not existing:
            raise CleanupError("salvage 完成竞争且现有包无效")
    return {"required": True, "packages": [str(final)], "manifest": manifest}


def resource_path(item):
    return item["resource"]["path"]


def _quiesce(repo, plan):
    for item in plan["_analyses"]:
        _verify_worktree_unchanged(item)
    current = _session_runtime_state(repo, plan["subject"],
                                     [item["resource"] for item in plan["_analyses"]])
    blockers = list(current["sessions"].get("blockers") or [])
    if plan["subject"]["kind"] == "feature":
        import integration_runtime
        outcome = integration_runtime.quiesce_feature(
            repo, plan["subject"]["feature_dir"], plan.get("feature_candidate"))
        blockers += list(outcome.get("blockers") or [])
        runtime_observed = outcome
    else:
        blockers += list(current["runtime"].get("blockers") or [])
        runtime_observed = current["runtime"]
    if blockers:
        raise CleanupError("活动会话/日志增长/服务未停止，拒绝删除：" + "；".join(map(str, blockers)),
                           code="active-resource")
    for item in plan["_analyses"]:
        _verify_worktree_unchanged(item)
    return {"sessions": current["sessions"], "runtime": runtime_observed, "blockers": []}


def _observe_worktrees_removed(repo, plan):
    remaining = []
    for item in plan["_analyses"]:
        path = item["resource"]["path"]
        if Path(path).exists() or Path(path).is_symlink() or _worktree_registration(repo, path):
            remaining.append(path)
    return remaining


def _remove_worktrees(repo, plan, running):
    for item in plan["_analyses"]:
        resource = item["resource"]
        path = resource["path"]
        exists = Path(path).exists() or Path(path).is_symlink()
        registered = _worktree_registration(repo, path)
        if not exists and not registered:
            continue
        if not running:
            raise CleanupError("worktree 在授权步骤前消失或登记变化，拒绝猜测结果")
        _verify_worktree_unchanged(item)
        if exists:
            result = _git(repo, "worktree", "remove", "--force", path)
            if result.returncode:
                raise CleanupError("worktree 移除失败：" + (result.stderr or result.stdout).strip())
        prune = _git(repo, "worktree", "prune", "-v")
        if prune.returncode:
            raise CleanupError("git worktree prune 失败：" + (prune.stderr or prune.stdout).strip())
    remaining = _observe_worktrees_removed(repo, plan)
    if remaining:
        raise CleanupError("worktree 仍有残留：" + "、".join(remaining))
    # Only remove empty parents inside the main repository.  Host-managed
    # external sandboxes never cause an arbitrary ancestor to be removed.
    for item in plan["_analyses"]:
        parent = Path(item["resource"]["path"]).parent
        try:
            parent.relative_to(Path(repo).resolve())
            while parent != Path(repo).resolve():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        except ValueError:
            pass
    dry = _git(repo, "worktree", "prune", "--dry-run", "-v", check=True).stdout
    pending = dry.splitlines()
    observed = {"removed": [item["resource"]["path"] for item in plan["_analyses"]],
                "remaining": remaining, "prune_pending": pending}
    if pending:
        observed["warnings"] = ["git worktree prune 后仍有待处理登记：" + "；".join(pending)]
    return observed


def _remote_oid(repo, remote, ref):
    result = _git(repo, "ls-remote", remote, ref, timeout=30)
    if result.returncode:
        raise CleanupError((result.stderr or result.stdout or "远端查询失败").strip())
    for line in result.stdout.splitlines():
        oid, _, name = line.partition("\t")
        if name == ref and OID_RE.fullmatch(oid):
            return oid
    return None


def _update_refs(repo, plan, running):
    observed = {"local_deleted": [], "remote_deleted": [], "warnings": [], "remaining_refs": []}
    for item in plan["_refs"]:
        ref, expected = item["ref"], item["oid"]
        current = _git(repo, "rev-parse", "--verify", ref)
        if current.returncode == 0:
            actual = current.stdout.strip()
            if actual != expected:
                raise StaleOperation("ref 已不是授权 OID，拒绝删除：%s" % ref)
            result = _git(repo, "update-ref", "-d", ref, expected)
            if result.returncode:
                raise CleanupError("expected-old CAS 删除本地 ref 失败：%s：%s" %
                                   (ref, (result.stderr or result.stdout).strip()))
        elif not running:
            raise StaleOperation("ref 在删除步骤前已不存在，不能把它单独视为 merged：%s" % ref)
        if _git(repo, "rev-parse", "--verify", ref).returncode == 0:
            observed["remaining_refs"].append(ref)
        else:
            observed["local_deleted"].append(ref)
        remote = item.get("remote_delete")
        if remote:
            try:
                actual = _remote_oid(repo, remote["remote"], remote["ref"])
                if actual is None:
                    if not running:
                        observed["warnings"].append("远端 ref 在操作前已消失：%s" % remote["ref"])
                elif actual != remote.get("oid"):
                    observed["warnings"].append("远端 OID 已变化，保留：%s" % remote["ref"])
                else:
                    lease = "--force-with-lease=%s:%s" % (remote["ref"], actual)
                    result = _git(repo, "push", lease, remote["remote"], ":" + remote["ref"], timeout=60)
                    if result.returncode:
                        observed["warnings"].append("远端删除失败，保留并需关注：%s：%s" %
                                                   (remote["ref"], (result.stderr or result.stdout).strip()))
                    else:
                        observed["remote_deleted"].append(remote["ref"])
            except CleanupError as exc:
                observed["warnings"].append("远端状态不可证，保留并需关注：%s" % exc)
        elif item.get("upstream"):
            observed["warnings"].append("非精确 upstream，远端未处理：%s" % item["upstream"])
    return observed


def _replace_issue_status(text, status):
    pattern = r"^status:\s*.*$"
    replacement = "status: " + status
    if text.startswith("---"):
        if not re.search(pattern, text, re.M):
            raise CleanupError("票 frontmatter 缺少 status")
        return re.sub(pattern, replacement, text, count=1, flags=re.M)
    match = re.search(r"```yaml\s*\n(.*?)```", text, re.S)
    if not match or not re.search(pattern, match.group(1), re.M):
        raise CleanupError("票缺少可写 status")
    block = re.sub(pattern, replacement, match.group(1), count=1, flags=re.M)
    return text[:match.start(1)] + block + text[match.end(1):]


def _append_progress_event_once(path, event):
    """Adapt legacy id-less JSONL history, then use the shared idempotent writer."""
    path = Path(path)
    lock_path = str(path) + ".lock"
    if path.exists():
        with FileLock(lock_path):
            lines = path.read_text(encoding="utf-8").splitlines()
            migrated = False
            output = []
            for number, line in enumerate(lines, 1):
                if not line.strip():
                    raise CleanupError("progress.jsonl 含空行，拒绝重写")
                value = json.loads(line)
                if not any(key in value for key in ("event_id", "event-id", "operation_id", "operation-id", "id")):
                    value["event_id"] = "legacy:%d:%s" % (number, hashlib.sha256(line.encode("utf-8")).hexdigest())
                    migrated = True
                output.append(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            if migrated:
                atomic_write(path, "\n".join(output) + "\n")
    return append_event_once(path, event, lock_path=lock_path)


def _compact_dev_and_account(repo, plan, operation_id, running, warnings):
    removed, bytes_freed, remaining = 0, 0, []
    for entry in plan["_dev"]["entries"]:
        path = Path(entry["abs"])
        if not path.exists():
            if not running:
                raise StaleOperation("dev 文件在 compact 前发生变化：%s" % path)
            removed += 1
            bytes_freed += entry["size"]
            continue
        if path.is_symlink() or not path.is_file() or _sha256(path) != entry["sha256"]:
            raise StaleOperation("dev 文件内容已变化，保留：%s" % path)
        try:
            path.unlink()
            removed += 1
            bytes_freed += entry["size"]
        except OSError as exc:
            warnings.append("dev 删除失败，保留：%s（%s）" % (path, exc))
            remaining.append(str(path))
    # Empty directories are optional cleanup and never follow symlinks.
    for entry in plan["_dev"]["entries"]:
        parent = Path(entry["abs"]).parent
        feature = Path(plan["subject"]["feature_dir"]).resolve()
        try:
            parent.resolve().relative_to(feature)
        except ValueError:
            continue
        while parent != feature and parent.exists() and not parent.is_symlink():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    previous_status = plan["subject"].get("status")
    status = previous_status
    if plan["intent"] == "discard":
        issue_path = Path(plan["subject"]["issue_path"])
        lock = FileLock(str(issue_path) + ".cleanup.lock")
        with lock:
            text = issue_path.read_text(encoding="utf-8")
            current = re.search(r"^status:\s*([^\s#]+)", text, re.M)
            if current and current.group(1) == "deferred":
                status = "deferred"
            else:
                if _sha256(issue_path) != plan["_auth"]["ticket"]["sha256"]:
                    raise StaleOperation("票文件在 deferred 前发生变化")
                atomic_write(issue_path, _replace_issue_status(text, "deferred"))
                status = "deferred"
    authorized_at = datetime.fromisoformat(plan["_auth"]["authorized_at"].replace("Z", "+00:00"))
    event = {"event_id": operation_id + ":close-clean", "event": "close-clean",
             "ts": authorized_at.astimezone().strftime("%Y-%m-%d %H:%M"), "issue": plan["issue"] or "feature",
             "host": "dsh", "intent": plan["intent"], "cleanup_operation_id": operation_id,
             "state": "needs-attention" if warnings else "completed",
             "note": "清理不合并代码；dev 删除 %d；警告 %d" % (removed, len(warnings))}
    _append_progress_event_once(Path(plan["subject"]["feature_dir"]) / "progress.jsonl", event)
    return {"dev_files_removed": removed, "bytes_freed": bytes_freed,
            "previous_status": previous_status, "status": status,
            "remaining": remaining, "event_id": event["event_id"], "warnings": warnings}


def _step_expected(plan, step):
    auth = plan["_auth"]
    if step == "quiesced":
        return {"session_runtime": auth["session_runtime"]}
    if step == "salvaged":
        return {"worktree_snapshots": [item["analysis"]["snapshot_sha256"] for item in auth["worktrees"]],
                "refs": [{"ref": item["ref"], "oid": item["oid"]} for item in auth["refs"]],
                "patch_base": auth.get("patch_base")}
    if step == "accounted":
        return {"target": auth["target"], "intent": auth["intent"]}
    if step == "worktree-removed":
        return {"paths": [item["resource"]["path"] for item in auth["worktrees"]]}
    if step == "refs-updated":
        return {"refs": auth["refs"]}
    if step == "dev-compacted":
        return {"dev": auth["dev"], "ticket": auth["ticket"]}
    raise CleanupError("未知 Saga 步骤：" + step)


def _result(repo, journal, plan, warnings=None, error=None):
    warnings = list(warnings or [])
    for step in journal.steps:
        observed = step.get("observed")
        if isinstance(observed, dict):
            warnings.extend(str(item) for item in observed.get("warnings") or [])
    warnings = list(dict.fromkeys(warnings))
    state = operation_state(journal)
    if error or warnings:
        state = "needs-attention"
    remaining = []
    remaining_worktrees = []
    try:
        remaining_worktrees = _observe_worktrees_removed(repo, plan)
        remaining.extend(remaining_worktrees)
        for ref in plan["_refs"]:
            if _git(repo, "rev-parse", "--verify", ref["ref"]).returncode == 0:
                remaining.append("ref:" + ref["ref"])
        for entry in plan["_dev"]["entries"]:
            if Path(entry["abs"]).exists():
                remaining.append(entry["abs"])
    except Exception as exc:
        warnings.append("残留盘点失败：" + str(exc))
        state = "needs-attention"
    salvage_dirs = []
    for step in journal.steps:
        if step["id"] == "salvaged" and isinstance(step.get("observed"), dict):
            salvage_dirs += step["observed"].get("packages") or []
    result = {"ok": not error and not warnings and not remaining and journal.state == "completed",
              "state": "needs-attention" if remaining else state,
              "journal_state": journal.state,
              "operation_id": journal.operation_id, "journal": str(journal.path),
              "intent": plan["intent"], "issue": plan["issue"], "branch": plan["branch"],
              "worktree_removed": not bool(remaining_worktrees),
              "branch_deleted": not any(
                  _git(repo, "rev-parse", "--verify", item["ref"]).returncode == 0 for item in plan["_refs"]),
              "salvage_dir": salvage_dirs[0] if salvage_dirs else None,
              "salvaged_files": sum(len(item["analysis"]["entries"]) for item in plan["_analyses"]),
              "branch_patch_saved": bool(plan["intent"] == "discard" and plan["_refs"] and salvage_dirs),
              "dev_files_removed": 0, "bytes_freed": 0,
              "previous_status": plan["subject"].get("status"), "status": plan["subject"].get("status"),
              "warnings": warnings, "remaining_paths": list(dict.fromkeys(remaining))}
    for step in journal.steps:
        if step["id"] == "dev-compacted" and isinstance(step.get("observed"), dict):
            result.update({key: step["observed"][key] for key in
                           ("dev_files_removed", "bytes_freed", "previous_status", "status")
                           if key in step["observed"]})
    if error:
        result["error"] = str(error)
    return result


def _run_operation(repo, operation_id, resume):
    journal, auth = _load_authorized(repo, operation_id)
    plan = _plan_from_authorization(repo, auth, operation_id)
    if journal.state == "completed":
        return _result(repo, journal, plan)
    accumulated_warnings = []
    current = journal.current_step
    if current is None:
        observed = journal.steps[-1].get("observed") or {}
        accumulated_warnings += list(observed.get("warnings") or [])
        if not accumulated_warnings:
            journal.complete()
        return _result(repo, journal, plan, accumulated_warnings)
    if current == "authorized":
        journal.advance("authorized", expected=auth, observed={"confirm": _digest(auth)})
    while journal.current_step and journal.state == "running":
        step_id = journal.current_step
        if step_id == "completed":
            break
        step = next(item for item in journal.steps if item["id"] == step_id)
        running = step["status"] == "running"
        expected = _step_expected(plan, step_id)
        journal.advance(step_id, expected=expected)
        try:
            if step_id == "quiesced":
                observed = _quiesce(repo, plan)
            elif step_id == "salvaged":
                observed = _salvage(repo, plan, operation_id)
            elif step_id == "accounted":
                observed = {"target": plan["target_branch"], "target_oid": plan["_target_oid"],
                            "merge_proof": plan.get("merge_proof"), "remaining_paths": plan.get("remaining_paths") or []}
            elif step_id == "worktree-removed":
                observed = _remove_worktrees(repo, plan, running=True)
            elif step_id == "refs-updated":
                observed = _update_refs(repo, plan, running)
            elif step_id == "dev-compacted":
                prior = []
                for old in journal.steps:
                    if isinstance(old.get("observed"), dict):
                        prior += list(old["observed"].get("warnings") or [])
                observed = _compact_dev_and_account(repo, plan, operation_id, running, prior)
            else:
                raise CleanupError("journal 包含无法识别的步骤：" + step_id)
            journal.advance(step_id, expected=expected, observed=observed)
        except (CleanupError, OSError, StateError) as exc:
            return _result(repo, journal, plan, error=str(exc))
    warnings = []
    for step in journal.steps:
        if isinstance(step.get("observed"), dict):
            warnings += list(step.get("observed").get("warnings") or [])
    if warnings:
        return _result(repo, journal, plan, warnings)
    if all(step["status"] == "done" for step in journal.steps):
        journal.complete()
    return _result(repo, journal, plan, warnings)


def apply_operation(repo, operation_id, confirm):
    """Apply only a fresh, unchanged authorization; started operations use reconcile."""
    repo = Path(repo).resolve()
    try:
        with _operation_lock(repo):
            journal, auth = _load_authorized(repo, operation_id)
            if confirm != _digest(auth):
                raise StaleOperation("确认指纹与授权 journal 不符，请重新预检")
            if operation_state(journal) != "authorized":
                if journal.state == "completed":
                    plan = _plan_from_authorization(repo, auth, operation_id)
                    return _result(repo, journal, plan)
                raise StaleOperation("操作已开始，必须使用 reconcile 按真实状态恢复")
            subject = auth["subject"]
            if subject["kind"] == "issue":
                import progress
                ticket = progress.find_ticket(repo, subject["id"], subject.get("key"))
                if not ticket:
                    raise StaleOperation("授权票已不存在或不唯一")
                rebuilt = make_plan(repo, ticket=ticket, intent=auth["intent"],
                                    operation_id=operation_id, authorize=False,
                                    authorized_at=auth["authorized_at"])
            else:
                rebuilt = make_plan(repo, feature_dir=subject["feature_dir"], intent="close",
                                    operation_id=operation_id, authorize=False,
                                    authorized_at=auth["authorized_at"],
                                    round=subject.get("round"))
            if not rebuilt.get("eligible") or rebuilt["confirm"] != confirm:
                raise StaleOperation("锁内重建计划与确认指纹不同；任何票/config/OID/ignored/dev/runtime 变化都需重新授权")
            return _run_operation(repo, operation_id, resume=False)
    except LockError as exc:
        raise CleanupError("另一清理/恢复操作持有仓库锁：" + str(exc), code="conflict")


def inspect_operation(repo, operation_id):
    journal, auth = _load_authorized(repo, operation_id)
    plan = _plan_from_authorization(repo, auth, operation_id)
    result = _result(repo, journal, plan)
    result["next_step"] = journal.current_step
    result["confirm"] = _digest(auth)
    return result


def _record_reconcile_event(repo, operation_id, result):
    journal, auth = _load_authorized(repo, operation_id)
    event = {"event_id": "%s:reconcile:%d" % (operation_id, journal.revision),
             "event": "reconcile", "ts": datetime.fromisoformat(auth["authorized_at"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M"),
             "issue": auth["subject"].get("id") if auth["subject"]["kind"] == "issue" else "feature",
             "host": "dsh", "cleanup_operation_id": operation_id,
             "revision": journal.revision, "state": result.get("state"),
             "note": "按 journal 与真实资源状态恢复；未机械重放合并或删除命令"}
    _append_progress_event_once(Path(auth["subject"]["feature_dir"]) / "progress.jsonl", event)


def reconcile_operations(repo, operation_id=None, apply=False):
    """Inspect authorized journals; optionally converge one by observed reality."""
    repo = Path(repo).resolve()
    if operation_id:
        paths = [operation_path(repo, operation_id)]
    else:
        directory = operations_dir(repo)
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if apply and len(paths) != 1:
        raise CleanupError("reconcile --apply 必须指定唯一 operation-id", code="invalid-operation")
    results = []
    for path in paths:
        identifier = path.stem
        if not apply:
            try:
                results.append(inspect_operation(repo, identifier))
            except (CleanupError, StateError, OSError) as exc:
                results.append({"ok": False, "state": "needs-attention", "operation_id": identifier,
                                "error": str(exc)})
            continue
        try:
            with _operation_lock(repo):
                # A read-only reconciliation may have changed nothing; reload under
                # lock and let each step observe whether its side effect happened.
                outcome = _run_operation(repo, identifier, resume=True)
                _record_reconcile_event(repo, identifier, outcome)
                results.append(outcome)
        except LockError as exc:
            raise CleanupError("另一清理/恢复操作持有仓库锁：" + str(exc), code="conflict")
        except (CleanupError, StateError, OSError) as exc:
            results.append({"ok": False, "state": "needs-attention", "operation_id": identifier,
                            "error": str(exc)})
    return {"ok": all(item.get("ok") or item.get("state") == "completed" for item in results) and bool(results),
            "applied": apply, "operations": results,
            "state": "completed" if results and all(item.get("state") == "completed" for item in results) else "needs-attention"}


# Deliberately no merge executor, merge event backfill, legacy close command, or
# caller-selectable action/dev-only execution mode exists in this module.
