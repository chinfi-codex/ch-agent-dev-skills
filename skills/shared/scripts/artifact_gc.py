#!/usr/bin/env python3
"""Manifest-driven artifact generations, budget accounting and salvage GC.

Nothing is classified from mtime alone. A superseded artifact is eligible only
when a successful current generation explicitly supersedes its chain and the
reference points at that current generation. Current, running, immediately
previous successful, active, staging and unique-recovery content is retained.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from state_store import FileLock, OperationJournal, atomic_json_write

SCHEMA = "agent-dev.artifact-generation/v2"
MANIFEST_NAME = "artifact-generation-manifest.json"
STATUSES = ("running", "current", "superseded")
OID_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class ArtifactGCError(RuntimeError):
    def __init__(self, message, code="gc-failed"):
        super().__init__(message)
        self.code = code


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value):
    path = Path(value)
    return (isinstance(value, str) and bool(value) and not path.is_absolute()
            and ".." not in path.parts and "\\" not in value and "\0" not in value)


def validate_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ArtifactGCError("generation manifest 必须是对象", "invalid-manifest")
    required = {"schema", "subject", "generation", "candidate_tree", "status", "artifacts"}
    if not required <= set(manifest):
        raise ArtifactGCError("generation manifest 缺少字段：" + ", ".join(sorted(required - set(manifest))), "invalid-manifest")
    if manifest["schema"] != SCHEMA:
        raise ArtifactGCError("不支持的 generation schema", "invalid-manifest")
    if not isinstance(manifest["subject"], str) or not manifest["subject"].strip():
        raise ArtifactGCError("subject 必须是非空字符串", "invalid-manifest")
    if not isinstance(manifest["generation"], str) or not manifest["generation"].strip():
        raise ArtifactGCError("generation 必须是非空字符串", "invalid-manifest")
    if not OID_RE.fullmatch(str(manifest["candidate_tree"])):
        raise ArtifactGCError("candidate_tree 必须是完整 Git tree OID", "invalid-manifest")
    if manifest["status"] not in STATUSES:
        raise ArtifactGCError("generation status 非法", "invalid-manifest")
    supersedes = manifest.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        raise ArtifactGCError("supersedes 必须是 generation 或 candidate_tree 字符串", "invalid-manifest")
    if not isinstance(manifest["artifacts"], list):
        raise ArtifactGCError("artifacts 必须是数组", "invalid-manifest")
    seen = set()
    for artifact in manifest["artifacts"]:
        if not isinstance(artifact, dict) or not {"path", "kind", "sha256", "size"} <= set(artifact):
            raise ArtifactGCError("artifact 必须带 path/kind/sha256/size", "invalid-manifest")
        if not _safe_relative(artifact["path"]) or artifact["path"] in seen:
            raise ArtifactGCError("artifact path 不安全或重复", "invalid-manifest")
        seen.add(artifact["path"])
        if not isinstance(artifact["kind"], str) or not artifact["kind"]:
            raise ArtifactGCError("artifact kind 非法", "invalid-manifest")
        if not SHA_RE.fullmatch(str(artifact["sha256"])):
            raise ArtifactGCError("artifact sha256 非法", "invalid-manifest")
        if not isinstance(artifact["size"], int) or isinstance(artifact["size"], bool) or artifact["size"] < 0:
            raise ArtifactGCError("artifact size 非法", "invalid-manifest")
    return manifest


def load_manifest(path, verify=False):
    path = Path(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactGCError("manifest 不可读：%s：%s" % (path, exc), "invalid-manifest")
    validate_manifest(manifest)
    if verify:
        for artifact in manifest["artifacts"]:
            target = path.parent / artifact["path"]
            if not target.is_file() or target.is_symlink() or _sha256(target) != artifact["sha256"] or target.stat().st_size != artifact["size"]:
                raise ArtifactGCError("manifest 产物已变化或丢失：%s" % target, "stale")
    return manifest


def discover_generations(root):
    """Find only explicitly named manifests; never infer generations from times."""
    root = Path(root).resolve()
    records = []
    if not root.is_dir():
        return records
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames
                       if name not in (".git", ".salvage", ".staging")
                       and not (Path(directory) / name).is_symlink()]
        if MANIFEST_NAME not in filenames:
            continue
        path = Path(directory) / MANIFEST_NAME
        if path.is_symlink():
            raise ArtifactGCError("manifest 不得是 symlink：%s" % path, "invalid-manifest")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ArtifactGCError("manifest 不可读：%s：%s" % (path, exc), "invalid-manifest")
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            continue  # legacy artifact inventories are not generation evidence
        manifest = load_manifest(path)
        records.append({"manifest_path": str(path), "root": str(path.parent), "manifest": manifest})
    records.sort(key=lambda item: (item["manifest"]["subject"], item["manifest"]["generation"], item["manifest_path"]))
    return records


def _reference_map(records, references):
    if references is None:
        for subject in {record["manifest"]["subject"] for record in records}:
            currents = [record for record in records if record["manifest"]["subject"] == subject and record["manifest"]["status"] == "current"]
            if len(currents) == 1:
                # The unique current marker is the manifest-backed reference.
                # An explicitly supplied reference map can further restrict it.
                pass
        return {record["manifest"]["subject"]: record["manifest"]["generation"]
                for record in records if record["manifest"]["status"] == "current"}
    if isinstance(references, dict):
        mapping = {}
        for subject, value in references.items():
            if isinstance(value, dict):
                value = value.get("generation") or value.get("candidate_tree")
            mapping[str(subject)] = str(value or "")
        return mapping
    if isinstance(references, (list, tuple)):
        mapping = {}
        for value in references:
            try:
                text = Path(value).read_text(encoding="utf-8")
            except OSError as exc:
                raise ArtifactGCError("活动引用不可读：%s" % exc)
            for record in records:
                manifest = record["manifest"]
                if manifest["generation"] in text or manifest["candidate_tree"] in text:
                    mapping[manifest["subject"]] = manifest["generation"]
        return mapping
    raise ArtifactGCError("references 必须是 mapping 或引用文件列表")


def _matches_identity(record, identity):
    return identity in (record["manifest"]["generation"], record["manifest"]["candidate_tree"])


def preview_generations(root, references=None):
    """Return hash-pinned old artifacts; current + last successful stay retained."""
    records = discover_generations(root)
    refs = _reference_map(records, references)
    by_subject = {}
    for record in records:
        by_subject.setdefault(record["manifest"]["subject"], []).append(record)
    candidates, protected, warnings = [], [], []
    for subject, group in by_subject.items():
        currents = [item for item in group if item["manifest"]["status"] == "current"]
        if len(currents) > 1:
            warnings.append("subject 有多个 current，全部保留：%s" % subject)
            protected += [item["manifest_path"] for item in group]
            continue
        if not currents:
            protected += [item["manifest_path"] for item in group]
            continue
        current = currents[0]
        reference = refs.get(subject)
        if not reference or not _matches_identity(current, reference):
            warnings.append("引用尚未切换到 current，旧代际保留：%s" % subject)
            protected += [item["manifest_path"] for item in group]
            continue
        chain, seen = [], set()
        cursor = current
        while cursor["manifest"].get("supersedes"):
            identity = cursor["manifest"]["supersedes"]
            previous = next((item for item in group if _matches_identity(item, identity)), None)
            if previous is None or previous["manifest_path"] in seen:
                warnings.append("supersedes 链断裂或成环，保留：%s" % subject)
                chain = []
                break
            seen.add(previous["manifest_path"])
            chain.append(previous)
            cursor = previous
        # chain[0] is the immediately previous/last successful generation.
        for index, record in enumerate(chain):
            if index == 0 or record["manifest"]["status"] != "superseded":
                protected.append(record["manifest_path"])
                continue
            for artifact in record["manifest"]["artifacts"]:
                candidates.append(dict(artifact, manifest_path=record["manifest_path"],
                                       generation=record["manifest"]["generation"], subject=subject,
                                       abs=str(Path(record["root"]) / artifact["path"])))
        chain_paths = {item["manifest_path"] for item in chain}
        protected += [item["manifest_path"] for item in group if item["manifest_path"] not in chain_paths]
    retained_paths = set()
    for record in records:
        if record["manifest_path"] in protected or record["manifest"]["status"] in ("current", "running"):
            for artifact in record["manifest"]["artifacts"]:
                retained_paths.add(str(Path(record["root"]) / artifact["path"]))
    filtered = []
    for item in candidates:
        path = Path(item["abs"])
        if item["abs"] in retained_paths:
            warnings.append("产物仍被活动/保留代际引用：%s" % item["abs"])
            continue
        if not path.is_file() or path.is_symlink():
            warnings.append("代际产物不存在或是 symlink，不按 GC 成功处理：%s" % path)
            continue
        if _sha256(path) != item["sha256"] or path.stat().st_size != item["size"]:
            warnings.append("代际产物 hash/size 已变化，保留：%s" % path)
            continue
        filtered.append(item)
    filtered.sort(key=lambda item: item["abs"])
    return {"ok": not warnings, "kind": "artifact-generation", "root": str(Path(root).resolve()),
            "candidates": filtered, "protected": sorted(set(protected)),
            "warnings": list(dict.fromkeys(warnings)),
            "references": (copy.deepcopy(references) if isinstance(references, dict)
                           else [str(item) for item in references] if isinstance(references, (list, tuple)) else None),
            "files": len(filtered), "bytes": sum(item["size"] for item in filtered)}


def measure_budget(paths, limits=None):
    """Shared deterministic budget accounting for process/feature gates."""
    files, total, by_kind = [], 0, {}
    for value in paths:
        path = Path(value)
        if path.is_dir() and not path.is_symlink():
            children = [item for item in path.rglob("*") if item.is_file() and not item.is_symlink()]
        elif path.is_file() and not path.is_symlink():
            children = [path]
        else:
            children = []
        for item in children:
            size = item.stat().st_size
            files.append({"path": str(item), "size": size})
            total += size
            by_kind[item.suffix or "<none>"] = by_kind.get(item.suffix or "<none>", 0) + size
    limits = limits or {}
    over = {}
    for key, value in limits.items():
        if value is None:
            continue
        if key.endswith("-mb"):
            over[key] = total > int(value) * 1024 * 1024
        elif key.endswith("-lines"):
            lines = sum(len(Path(item["path"]).read_text(encoding="utf-8", errors="replace").splitlines()) for item in files)
            over[key] = lines > int(value)
    return {"files": len(files), "bytes": total, "by_kind": by_kind,
            "limits": dict(limits), "over_budget": over, "ok": not any(over.values())}


def budget_stats(root, config=None):
    root = Path(root).resolve()
    budget = {} if config is None else config.get("artifact-budget", config)
    paths = [root / name for name in ("impl", "integration") if (root / name).is_dir()] or [root]
    result = measure_budget(paths, {key: value for key, value in (budget or {}).items()
                                    if key in ("impl-dir-max-mb", "integration-dir-max-mb")})
    generations = discover_generations(root)
    result["generations"] = {status: sum(record["manifest"]["status"] == status for record in generations)
                             for status in STATUSES}
    result["manifests"] = len(generations)
    result["manifest_declared_bytes"] = sum(artifact["size"] for record in generations
                                             for artifact in record["manifest"]["artifacts"])
    return result


def _tree_snapshot(root):
    result = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(dirnames)
        for name in sorted(dirnames):
            path = Path(directory) / name
            if path.is_symlink():
                result.append({"path": str(path.relative_to(root)), "kind": "symlink", "target": os.readlink(path)})
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink():
                result.append({"path": str(path.relative_to(root)), "kind": "symlink", "target": os.readlink(path)})
            elif path.is_file():
                result.append({"path": str(path.relative_to(root)), "kind": "file",
                               "size": path.stat().st_size, "sha256": _sha256(path)})
    return sorted(result, key=lambda item: item["path"])


def _created_timestamp(manifest):
    value = manifest.get("created_at") or manifest.get("created")
    if not value:
        raise ArtifactGCError("salvage manifest 缺 created_at，不按 mtime 猜")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError as exc:
            raise ArtifactGCError("salvage created_at 非法：%r" % value) from exc


def _active_operations(repo):
    active = set()
    try:
        import progress_cleanup
        directory = progress_cleanup.operations_dir(repo)
        for path in directory.glob("*.json"):
            try:
                journal = OperationJournal.load(path)
            except Exception:
                active.add(path.stem)
                continue
            if journal.state != "completed":
                active.add(journal.operation_id)
    except Exception:
        pass
    return active


def preview_salvage(repo, retention_days=30, active_operation_ids=None, now=None, active_references=None):
    repo = Path(repo).resolve()
    root = repo / "dev" / ".salvage"
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    active = _active_operations(repo) | set(active_operation_ids or ())
    reference_text = ""
    for reference in active_references or []:
        try:
            reference_text += Path(reference).read_text(encoding="utf-8") + "\n"
        except OSError as exc:
            raise ArtifactGCError("活动 salvage 引用不可读：%s" % exc)
    candidates, protected, warnings = [], [], []
    if not root.is_dir():
        return {"ok": True, "kind": "salvage", "root": str(root), "candidates": [], "protected": [], "warnings": [], "files": 0, "bytes": 0}
    for path in sorted(root.iterdir()):
        if path.name == ".staging" or path.is_symlink() or not path.is_dir():
            protected.append(str(path))
            continue
        manifest_path = path / "_manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            protected.append(str(path))
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            created = _created_timestamp(manifest)
        except (OSError, ValueError, ArtifactGCError) as exc:
            warnings.append(str(exc))
            protected.append(str(path))
            continue
        operation_id = manifest.get("operation_id")
        unique = bool(manifest.get("unique_recovery"))
        if manifest.get("branch_patch") or manifest.get("uncommitted_patch"):
            branch = manifest.get("branch")
            if branch:
                exists = __import__("subprocess").run(["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/" + branch], capture_output=True).returncode == 0
                unique = unique or not exists
            else:
                unique = True
        referenced = str(path) in reference_text or (operation_id and str(operation_id) in reference_text)
        if operation_id in active or referenced or unique or now - created < int(retention_days) * 86400:
            protected.append(str(path))
            continue
        snapshot = _tree_snapshot(path)
        candidates.append({"path": str(path), "operation_id": operation_id,
                           "created_at": manifest.get("created_at") or manifest.get("created"),
                           "snapshot": snapshot, "bytes": sum(item.get("size", 0) for item in snapshot),
                           "files": sum(item["kind"] == "file" for item in snapshot)})
    return {"ok": not warnings, "kind": "salvage", "root": str(root), "repo": str(repo),
            "retention_days": int(retention_days),
            "active_operation_ids": sorted(set(active_operation_ids or ())),
            "active_references": [str(Path(item)) for item in active_references or []], "candidates": candidates, "protected": protected, "warnings": list(dict.fromkeys(warnings)),
            "files": sum(item["files"] for item in candidates), "bytes": sum(item["bytes"] for item in candidates)}


def preview(root, references=None):
    return preview_generations(root, references)


def _gc_operation_dir(root):
    result = __import__("subprocess").run(["git", "-C", str(root), "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    if result.returncode == 0:
        path = Path(result.stdout.strip())
        if not path.is_absolute():
            path = Path(root) / path
        return path.resolve() / "agent-dev" / "operations" / "artifact-gc"
    return Path(root).resolve() / ".agent-dev" / "operations" / "artifact-gc"


def _plan_material(plan):
    material = {"kind": plan["kind"], "root": plan["root"],
                "candidates": plan["candidates"], "protected": plan["protected"],
                "warnings": plan["warnings"]}
    for key in ("repo", "retention_days", "active_operation_ids", "active_references", "references"):
        if key in plan:
            material[key] = copy.deepcopy(plan[key])
    return material


def authorize_gc(plan, operation_id=None):
    operation_id = operation_id or "artifact-" + uuid.uuid4().hex
    if not OPERATION_RE.fullmatch(operation_id):
        raise ArtifactGCError("operation id 非法")
    material = _plan_material(plan)
    confirm = _digest(material)
    path = _gc_operation_dir(plan["root"]) / (operation_id + ".json")
    journal = OperationJournal.create(path, operation_id, ["authorized", "collected"])
    journal.advance("authorized", expected=material, observed={"confirm": confirm})
    return dict(plan, operation_id=operation_id, confirm=confirm, journal=str(path))


def _current_gc_plan(material, references=None):
    if material["kind"] == "artifact-generation":
        return preview_generations(material["root"],
                                   material.get("references") if references is None else references)
    return preview_salvage(material["repo"], material.get("retention_days", 30),
                           active_operation_ids=material.get("active_operation_ids"),
                           active_references=material.get("active_references"))


def _gc_result(journal, material, observed=None):
    observed = observed or journal.steps[-1].get("observed") or {}
    warnings = list(observed.get("warnings") or [])
    remaining = []
    for item in material.get("candidates") or []:
        path = item.get("abs") if material["kind"] == "artifact-generation" else item.get("path")
        if path and Path(path).exists():
            remaining.append(path)
    if remaining:
        warnings.append("仍有 GC 候选残留：" + "；".join(remaining))
    complete = journal.state == "completed" and not warnings and not remaining
    return {"ok": complete, "state": "completed" if complete else "needs-attention",
            "operation_id": journal.operation_id, "removed": observed.get("removed", 0),
            "bytes_freed": observed.get("bytes_freed", 0),
            "warnings": list(dict.fromkeys(warnings)), "remaining_paths": remaining}


def apply_gc(plan_or_root, operation_id=None, confirm=None, references=None):
    """Apply once from preview; resume a running step only from its old journal."""
    if isinstance(plan_or_root, dict):
        plan = plan_or_root
        operation_id = operation_id or plan.get("operation_id")
        confirm = confirm or plan.get("confirm")
        root = plan["root"]
    else:
        root = str(Path(plan_or_root).resolve())
        plan = None
    if not operation_id or not confirm:
        raise ArtifactGCError("apply 需要 preview 返回的 operation-id 和 confirm")
    path = _gc_operation_dir(root) / (str(operation_id) + ".json")
    with FileLock(str(path) + ".apply.lock", blocking=False):
        # Reload after acquiring the lock: another process may have completed or
        # advanced the operation between the caller's preview and this apply.
        journal = OperationJournal.load(path)
        material = journal.steps[0].get("expected")
        if not material or _digest(material) != confirm:
            raise ArtifactGCError("GC confirm 与授权不符", "stale")
        if journal.state == "completed":
            return _gc_result(journal, material)
        if journal.current_step is None:
            observed = journal.steps[-1].get("observed") or {}
            if not observed.get("warnings"):
                journal.complete()
            return _gc_result(journal, material, observed)
        step = next(item for item in journal.steps if item["id"] == "collected")
        running = step["status"] == "running"
        if not running:
            current = _current_gc_plan(material, references)
            if _digest(_plan_material(current)) != confirm:
                raise ArtifactGCError("GC 目标已变化，请重新 preview", "stale")
        journal.advance("collected", expected={"confirm": confirm})
        removed, bytes_freed = 0, 0
        warnings = list(material.get("warnings") or [])
        if material["kind"] == "artifact-generation":
            for item in material["candidates"]:
                target = Path(item["abs"])
                if not target.exists():
                    # A crash may have happened after unlink but before the
                    # observed write. Missing is accepted only in that running step.
                    if not running:
                        raise ArtifactGCError("删除前产物已消失：%s" % target, "stale")
                else:
                    if not target.is_file() or target.is_symlink() or _sha256(target) != item["sha256"]:
                        raise ArtifactGCError("删除前产物已变化：%s" % target, "stale")
                    target.unlink()
                removed += 1
                bytes_freed += item["size"]
            by_manifest = {}
            for item in material["candidates"]:
                by_manifest.setdefault(item["manifest_path"], set()).add(item["path"])
            for manifest_path, removed_paths in by_manifest.items():
                if not Path(manifest_path).exists():
                    if not running:
                        raise ArtifactGCError("manifest 在更新前消失：%s" % manifest_path, "stale")
                    continue
                manifest = load_manifest(manifest_path)
                manifest["artifacts"] = [item for item in manifest["artifacts"]
                                         if item["path"] not in removed_paths]
                atomic_json_write(manifest_path, manifest)
        else:
            for item in material["candidates"]:
                target = Path(item["path"])
                if not target.exists():
                    if not running:
                        raise ArtifactGCError("删除前 salvage 已消失：%s" % target, "stale")
                else:
                    if _tree_snapshot(target) != item["snapshot"]:
                        raise ArtifactGCError("删除前 salvage 已变化：%s" % target, "stale")
                    shutil.rmtree(target)
                removed += item["files"]
                bytes_freed += item["bytes"]
        observed = {"removed": removed, "bytes_freed": bytes_freed, "warnings": warnings}
        journal.advance("collected", expected={"confirm": confirm}, observed=observed)
        if not warnings:
            journal.complete()
        return _gc_result(journal, material, observed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "budget"):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--root", required=True)
    apply_parser.add_argument("--operation-id", required=True)
    apply_parser.add_argument("--confirm", required=True)
    salvage = sub.add_parser("salvage-preview")
    salvage.add_argument("--repo", required=True)
    salvage.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if args.command == "preview":
            print(json.dumps(authorize_gc(preview(args.root)), ensure_ascii=False))
        elif args.command == "budget":
            print(json.dumps(budget_stats(args.root), ensure_ascii=False))
        elif args.command == "apply":
            print(json.dumps(apply_gc(args.root, args.operation_id, args.confirm), ensure_ascii=False))
        else:
            print(json.dumps(preview_salvage(args.repo, args.retention_days), ensure_ascii=False))
        return 0
    except (ArtifactGCError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": getattr(exc, "code", "gc-failed")}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
