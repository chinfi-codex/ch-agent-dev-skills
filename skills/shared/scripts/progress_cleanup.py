#!/usr/bin/env python3
"""progress.py 的收尾清场原语：预检、确认指纹、salvage、worktree/dev 清理。

安全边界：
- 只处理 status=done 的票所对应分支；活跃票与集成分支不进入执行路径。
- dev 软链不跟随；实体 dev 与主工作区逐文件比对，未收录内容先复制到 .salvage。
- 可再生目录不抢救；worktree 删除失败则不继续删分支。
- 未并入任何授权合并目标的分支只清 worktree，不删分支。
- 所有执行都要求 make_plan 的 confirm 指纹一致，调用方负责重新预检比对。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

REPRO_DIRS = {
    ".git", ".logs", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "dist", "build",
    ".next", ".turbo", "coverage", "wheelhouse", "keyring", "package-cache",
}
REPRO_FILES = {".DS_Store", ".coverage"}
DEV_CACHE_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "node_modules", ".venv", "venv", "wheelhouse", "keyring", "package-cache",
}
MAX_WALK_FILES = 50000
MAX_SALVAGE_BYTES = 1024 ** 3


class CleanupError(RuntimeError):
    pass


def _git(repo, *args, check=False):
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="surrogateescape", timeout=30,
    )
    if check and r.returncode:
        raise CleanupError((r.stderr or r.stdout or "git 执行失败").strip())
    return r


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_repro(rel):
    parts = Path(rel).parts
    return any(p in REPRO_DIRS for p in parts) or Path(rel).name in REPRO_FILES \
        or Path(rel).suffix in {".pyc", ".pyo"}


def _status_entries(worktree):
    r = _git(worktree, "status", "--porcelain=v1", "-z", "--ignored", check=True)
    items = r.stdout.split("\0")
    out = []
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if len(item) < 4:
            continue
        xy, rel = item[:2], item[3:]
        if "R" in xy or "C" in xy:
            i += 1  # -z 的重命名记录下一项是旧路径；只处理新路径
        out.append((xy, rel))
    return out


def _entry(path, rel, kind, digest=None):
    st = path.stat()
    return {
        "abs": str(path), "rel": Path(rel).as_posix(), "kind": kind,
        "size": st.st_size, "sha256": digest or _sha256(path),
    }


def _analyze_worktree(worktree, repo):
    wt = Path(worktree)
    repo = Path(repo)
    salvage, symlinks = [], []
    skipped_repro = skipped_same = visited = 0
    logical_bytes = unique_bytes = 0
    unique_hashes = set()
    tracked_changes = 0
    dev_symlink = (wt / "dev").is_symlink()
    try:
        same_device = wt.stat().st_dev == (repo / "dev").stat().st_dev
    except OSError:
        same_device = wt.stat().st_dev == repo.stat().st_dev
    seen = set()

    def consider(path, rel, kind):
        nonlocal skipped_repro, skipped_same, visited, logical_bytes, unique_bytes
        rel = Path(rel).as_posix()
        if rel in seen:
            return
        seen.add(rel)
        visited += 1
        if visited > MAX_WALK_FILES:
            raise CleanupError(f"worktree 待检查文件超过 {MAX_WALK_FILES}，为避免漏抢救已停止；请先人工收窄")
        if _is_repro(rel):
            skipped_repro += 1
            return
        if path.is_symlink():
            symlinks.append({"rel": rel, "target": os.readlink(path)})
            return
        if not path.is_file():
            return
        digest = _sha256(path)
        if kind != "modified":
            main_file = repo / rel
            try:
                if (main_file.is_file() and not main_file.is_symlink()
                        and main_file.stat().st_size == path.stat().st_size
                        and _sha256(main_file) == digest):
                    skipped_same += 1
                    return
            except OSError:
                pass
        size = path.stat().st_size
        logical_bytes += size
        if digest not in unique_hashes:
            unique_hashes.add(digest)
            unique_bytes += size
        if unique_bytes > MAX_SALVAGE_BYTES and not same_device:
            raise CleanupError("跨设备待抢救内容超过 1 GiB，为避免大批量复制已停止；同设备可硬链接抢救，或先人工清理可再生缓存")
        salvage.append(_entry(path, rel, kind, digest))

    def walk_dir(root, rel_root, kind):
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in REPRO_DIRS and not (Path(dirpath) / d).is_symlink()
            )
            for name in sorted(filenames):
                p = Path(dirpath) / name
                rel = (Path(rel_root) / p.relative_to(root)).as_posix()
                consider(p, rel, kind)

    status = _status_entries(wt)
    for xy, rel in status:
        p = wt / rel
        if xy not in ("??", "!!"):
            tracked_changes += 1
            if p.exists() or p.is_symlink():
                consider(p, rel, "modified")
            continue
        if rel.rstrip("/") == "dev" and dev_symlink:
            continue  # 主工作区 dev 真源，不跟随、不复制
        if p.is_dir() and not p.is_symlink():
            walk_dir(p, rel.rstrip("/"), "ignored" if xy == "!!" else "untracked")
        elif p.exists() or p.is_symlink():
            consider(p, rel.rstrip("/"), "ignored" if xy == "!!" else "untracked")

    return {
        "entries": salvage, "symlinks": symlinks,
        "tracked_changes": tracked_changes,
        "dirty_entries": len(status),
        "skipped_repro": skipped_repro, "skipped_same": skipped_same,
        "dev_symlink": dev_symlink,
        "logical_bytes": logical_bytes, "unique_bytes": unique_bytes,
        "unique_files": len(unique_hashes), "same_device": same_device,
    }


def _referenced_docs(feature_dir):
    docs = []
    for p in Path(feature_dir).rglob("*.md"):
        try:
            docs.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(docs)


def _is_dev_candidate(impl_dir, path):
    rel = path.relative_to(impl_dir)
    if any(part in DEV_CACHE_DIRS for part in rel.parts):
        return True
    if any(part.startswith("merge-") for part in rel.parts):
        return True
    if path.suffix in {".ours", ".theirs", ".orig"}:
        return True
    return path.name in {".DS_Store", ".coverage"} or path.suffix in {".pyc", ".pyo"}


def _analyze_dev_cleanup(feature_dir, issue_id):
    impl_dir = Path(feature_dir) / "impl" / issue_id
    if not impl_dir.is_dir() or impl_dir.is_symlink():
        return {"impl_dir": str(impl_dir), "entries": [], "skipped_referenced": 0}
    docs = _referenced_docs(feature_dir)
    entries, skipped_referenced = [], 0
    for dirpath, dirnames, filenames in os.walk(impl_dir, followlinks=False):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file() or not _is_dev_candidate(impl_dir, p):
                continue
            rel = p.relative_to(impl_dir).as_posix()
            # 被 impl-log/evidence/评审等文档引用的捕获物一律保留
            if rel in docs or name in docs:
                skipped_referenced += 1
                continue
            entries.append(_entry(p, rel, "dev-intermediate"))
    return {"impl_dir": str(impl_dir), "entries": entries, "skipped_referenced": skipped_referenced}


def make_plan(repo, branch, ticket, worktree, merge_targets, current_branch):
    """ticket: {id,key,title,status,feature_dir} 或 None。返回含内部执行项的预检计划。"""
    repo = Path(repo).resolve()
    plan = {
        "ok": True, "eligible": False, "branch": branch,
        "_repo": str(repo),
        "issue": ticket.get("id") if ticket else None,
        "title": ticket.get("title") if ticket else None,
        "status": ticket.get("status") if ticket else None,
        "worktree": str(worktree) if worktree else None,
        "merged_into": None, "delete_branch": False,
        "warnings": [], "will": [], "confirm": None,
        "_feature_dir": ticket.get("feature_dir") if ticket else None,
    }
    if not ticket:
        plan["reason"] = "未找到对应票或票号无法唯一归属；只允许清理明确归属于已完成票的残留"
        return plan
    if ticket.get("status") != "done":
        plan["reason"] = f"票状态为 {ticket.get('status')}，活跃工作不提供清理"
        return plan
    if current_branch == branch:
        plan["reason"] = "该分支正在主工作区检出，不能清理"
        return plan
    if worktree and Path(worktree).resolve() == repo:
        plan["reason"] = "目标是主工作区，不是可移除的 worktree"
        return plan
    r = _git(repo, "rev-parse", branch)
    if r.returncode:
        plan["reason"] = "本地分支不存在"
        return plan
    plan["head"] = r.stdout.strip()
    for target in merge_targets:
        if target == branch or not _git(repo, "rev-parse", "--verify", target).returncode == 0:
            continue
        if _git(repo, "merge-base", "--is-ancestor", branch, target).returncode == 0:
            plan["merged_into"] = target
            plan["delete_branch"] = True
            break

    wt_analysis = {"entries": [], "symlinks": [], "tracked_changes": 0,
                   "dirty_entries": 0, "skipped_repro": 0, "skipped_same": 0,
                   "dev_symlink": False}
    if worktree:
        if not Path(worktree).is_dir():
            plan["reason"] = "登记的 worktree 路径不存在，请先 git worktree prune 后重试"
            return plan
        wt_analysis = _analyze_worktree(worktree, repo)
    dev_analysis = _analyze_dev_cleanup(ticket["feature_dir"], ticket["id"])
    plan.update({
        "_wt": wt_analysis, "_dev": dev_analysis,
        "salvage": {
            "files": len(wt_analysis["entries"]),
            "unique_files": wt_analysis.get("unique_files", 0),
            "bytes": wt_analysis.get("unique_bytes", 0),
            "logical_bytes": wt_analysis.get("logical_bytes", 0),
            "method": "hardlink" if wt_analysis.get("same_device") else "copy",
            "sample": [e["rel"] for e in wt_analysis["entries"][:8]],
            "skipped_repro": wt_analysis["skipped_repro"],
            "skipped_same": wt_analysis["skipped_same"],
            "dev_symlink": wt_analysis["dev_symlink"],
            "tracked_changes": wt_analysis["tracked_changes"],
            "dirty_entries": wt_analysis["dirty_entries"],
            "symlinks": wt_analysis["symlinks"],
        },
        "dev_cleanup": {
            "files": len(dev_analysis["entries"]),
            "bytes": sum(e["size"] for e in dev_analysis["entries"]),
            "sample": [e["rel"] for e in dev_analysis["entries"][:8]],
            "skipped_referenced": dev_analysis["skipped_referenced"],
        },
    })
    if worktree and not wt_analysis["dev_symlink"] and (Path(worktree) / "dev").is_dir():
        plan["warnings"].append("worktree 内 dev/ 是实体目录：未收录内容会先抢救，绝不直接删除")
    if wt_analysis["tracked_changes"]:
        plan["warnings"].append("含未提交修改：会保留文件副本与 uncommitted.patch")
    if worktree and not plan["merged_into"]:
        plan["warnings"].append("未并入授权合并目标：只清 worktree，保留本地分支")
    if plan["salvage"]["files"] or wt_analysis["tracked_changes"] or wt_analysis["symlinks"]:
        method = "硬链接" if wt_analysis.get("same_device") else "复制"
        plan["will"].append(f"抢救 {plan['salvage']['files']} 个文件到 dev/.salvage（{method}去重，含清单与补丁）")
    if worktree:
        plan["will"].append("移除 worktree（salvage 成功后）")
    if plan["delete_branch"]:
        plan["will"].append(f"删除已并入 {plan['merged_into']} 的本地分支")
    if plan["dev_cleanup"]["files"]:
        plan["will"].append(f"清理 dev 中间过程文件 {plan['dev_cleanup']['files']} 个")
    if not plan["will"]:
        plan["reason"] = "没有可清理的 worktree、已合入分支或 dev 中间过程文件"
        return plan
    plan["eligible"] = True
    fingerprint = {
        "branch": branch, "head": plan["head"], "worktree": plan["worktree"],
        "merged_into": plan["merged_into"], "delete_branch": plan["delete_branch"],
        "salvage": [(e["rel"], e["size"], e["sha256"]) for e in wt_analysis["entries"]],
        "symlinks": wt_analysis["symlinks"], "tracked_changes": wt_analysis["tracked_changes"],
        "dev": [(e["rel"], e["size"], e["sha256"]) for e in dev_analysis["entries"]],
        "ticket": ticket["key"], "status": ticket["status"],
    }
    plan["confirm"] = hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return plan


def public_plan(plan):
    """返回给 UI 的预检视图，不带内部绝对文件清单。"""
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def _salvage(repo, plan):
    wt = plan["_wt"]
    if not (wt["entries"] or wt["tracked_changes"] or wt["symlinks"]):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(repo) / "dev" / ".salvage" / f"{plan['issue']}-{stamp}"
    target = base
    n = 1
    while target.exists():
        n += 1
        target = Path(str(base) + f"-{n}")
    target.mkdir(parents=True)
    files_root = target / "files"
    for e in wt["entries"]:
        src = Path(e["abs"])
        # 执行前再次核对内容与预检一致，防止预检后的静默替换
        if not src.is_file() or src.is_symlink() or _sha256(src) != e["sha256"]:
            raise CleanupError(f"抢救前文件已变化：{e['rel']}；未移除 worktree")
        obj = target / "objects" / e["sha256"]
        if not obj.exists():
            obj.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, obj)  # 同设备先硬链接，删 worktree 后数据块仍由 salvage 持有
            except OSError:
                shutil.copy2(src, obj, follow_symlinks=False)  # 跨设备才实际复制
        dst = files_root / e["rel"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(obj, dst)  # 同内容路径保留各自入口，物理只存一份
        except FileExistsError:
            pass
    if wt["tracked_changes"]:
        patch = _git(plan["worktree"], "diff", "--binary", "HEAD", check=True)
        (target / "uncommitted.patch").write_text(patch.stdout, encoding="utf-8", errors="surrogateescape")
    manifest = {
        "issue": plan["issue"], "branch": plan["branch"], "head": plan["head"],
        "source_worktree": plan["worktree"], "merged_into": plan["merged_into"],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": "worktree 清场前置 salvage：未收录/未提交内容，软链未跟随",
        "files": [{k: e[k] for k in ("rel", "kind", "size", "sha256")} for e in wt["entries"]],
        "symlinks": wt["symlinks"],
        "skipped_reproducible": wt["skipped_repro"],
        "skipped_already_in_main": wt["skipped_same"],
        "uncommitted_patch": bool(wt["tracked_changes"]),
    }
    (target / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target)


def execute_plan(plan):
    if not plan.get("eligible"):
        raise CleanupError(plan.get("reason") or "该节点不可清理")
    result = {
        "ok": True, "branch": plan["branch"], "issue": plan["issue"],
        "salvage_dir": None, "salvaged_files": 0,
        "worktree_removed": False, "branch_deleted": False,
        "dev_files_removed": 0, "bytes_freed": 0, "warnings": [],
    }
    # _repo 是主工作区，绝不从 worktree 路径猜测。
    repo = Path(plan["_repo"])
    salvage_dir = _salvage(repo, plan)
    result["salvage_dir"] = salvage_dir
    result["salvaged_files"] = len(plan["_wt"]["entries"])

    if plan["worktree"]:
        r = _git(repo, "worktree", "remove", "--force", plan["worktree"])
        if r.returncode:
            raise CleanupError(
                "worktree 移除失败，未继续删除分支或 dev 文件："
                + (r.stderr or r.stdout).strip()
                + (f"；抢救包已保留在 {salvage_dir}" if salvage_dir else "")
            )
        result["worktree_removed"] = True
        _git(repo, "worktree", "prune")
    if plan["delete_branch"]:
        r = _git(repo, "branch", "-D", plan["branch"])
        if r.returncode:
            result["warnings"].append("worktree 已清，但本地分支删除失败：" + (r.stderr or r.stdout).strip())
        else:
            result["branch_deleted"] = True

    impl_dir = Path(plan["_dev"]["impl_dir"])
    for e in plan["_dev"]["entries"]:
        p = Path(e["abs"])
        try:
            if p.is_file() and not p.is_symlink() and _sha256(p) == e["sha256"]:
                p.unlink()
                result["dev_files_removed"] += 1
                result["bytes_freed"] += e["size"]
            else:
                result["warnings"].append(f"dev 文件已变化，保留：{e['rel']}")
        except OSError as exc:
            result["warnings"].append(f"dev 文件删除失败，保留：{e['rel']}（{exc}）")
    if impl_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(impl_dir, topdown=False):
            p = Path(dirpath)
            if p != impl_dir:
                try:
                    p.rmdir()
                except OSError:
                    pass
    result["ok"] = not result["warnings"]
    return result
