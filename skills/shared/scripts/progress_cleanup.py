#!/usr/bin/env python3
"""progress.py 的收尾清场原语：预检、确认指纹、salvage、worktree/dev 清理。

安全边界：
- done 走普通收尾；非 done（除 proposed）按中途搁置处理，由用户选择丢弃或合并保留。
- 中途搁置未合入分支必须先保存完整 branch.patch 和未提交改动，才能删除。
- 无本地分支时走 issue-only：仅清理 dev 候选文件，不动代码、分支或票状态；无候选则拒绝空清理。
- 合并保留只合入已提交内容；目标脏或合并冲突则中止，原 worktree 和分支保留。
- 活跃后代分支仍禁止清理；done 未合入分支只清 worktree，不删分支。
- dev 软链不跟随；实体 dev 与主工作区逐文件比对，未收录内容先复制到 .salvage。
- 可再生目录不抢救；worktree 删除失败则不继续删分支。
- 所有执行都要求 make_plan 的 confirm 指纹一致，调用方负责重新预检比对。
- close_issue 是独立生命周期操作：仅把 status 改为 deferred 并写 digest，不删除分支/worktree/dev；退役状态不作为依赖完成依据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
    status = ticket.get("status")
    if status == "proposed":
        plan["reason"] = "需求仍在 proposed，未开工，不提供清理"
        return plan
    plan["mode"] = "close" if status == "done" else "abandon"
    plan["merge_target"] = next((t for t in merge_targets if t != branch and _git(repo, "rev-parse", "--verify", t).returncode == 0), None)
    wt_analysis = {"entries": [], "symlinks": [], "tracked_changes": 0,
                   "dirty_entries": 0, "skipped_repro": 0, "skipped_same": 0,
                   "dev_symlink": False}
    if not branch:
        # issue-only：无本地分支，仅清 dev 过程文件，不动代码/分支/票状态
        dev_analysis = _analyze_dev_cleanup(ticket["feature_dir"], ticket["id"])
        plan.update({
            "mode": "dev-only", "merge_target": None,
            "_wt": wt_analysis, "_dev": dev_analysis,
            "salvage": {"files": 0, "unique_files": 0, "bytes": 0, "sample": []},
            "dev_cleanup": {
                "files": len(dev_analysis["entries"]),
                "bytes": sum(e["size"] for e in dev_analysis["entries"]),
                "sample": [e["rel"] for e in dev_analysis["entries"][:8]],
                "skipped_referenced": dev_analysis["skipped_referenced"],
            },
        })
        if not dev_analysis["entries"]:
            plan["reason"] = "无本地分支，且 dev 中没有可安全判定的过程文件可清理"
            return plan
        plan["will"].append(f"仅清理 dev 中间过程文件 {len(dev_analysis['entries'])} 个（无本地分支，不动代码与票状态）")
        plan["eligible"] = True
        fingerprint = {
            "branch": None, "head": None, "worktree": None,
            "dev": [(e["rel"], e["size"], e["sha256"]) for e in dev_analysis["entries"]],
            "ticket": ticket["key"], "status": ticket["status"],
        }
        plan["confirm"] = hashlib.sha256(
            json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return plan
    if branch and current_branch == branch:
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

    if plan["mode"] == "abandon" and not plan["merged_into"]:
        plan["delete_branch"] = True
        base = _git(repo, "merge-base", branch, plan["merge_target"]) if plan["merge_target"] else None
        if base is None or base.returncode != 0:
            plan["reason"] = "无法确定完整分支补丁基线，未允许丢弃未合入分支"
            return plan
        plan["abandon_unmerged"] = True
        plan["_patch_base"] = base.stdout.strip()

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
    if plan["mode"] == "abandon" and plan.get("abandon_unmerged"):
        plan["warnings"].append("中途搁置：丢弃或合并前都会保存完整分支补丁；未提交改动只保存到 salvage，不参与合并")
    elif worktree and not plan["merged_into"]:
        plan["warnings"].append("未并入授权合并目标：只清 worktree，保留本地分支")
    if plan["salvage"]["files"] or wt_analysis["tracked_changes"] or wt_analysis["symlinks"] or plan.get("abandon_unmerged"):
        method = "硬链接" if wt_analysis.get("same_device") else "复制"
        plan["will"].append(f"抢救 {plan['salvage']['files']} 个文件到 dev/.salvage（{method}去重，含清单与补丁）")
    if worktree:
        plan["will"].append("移除 worktree（salvage 成功后）")
    if plan["delete_branch"]:
        if plan.get("abandon_unmerged"):
            plan["will"].append("保存完整分支补丁后删除本地分支（丢弃或合并成功后执行）")
        else:
            plan["will"].append(f"删除已并入 {plan['merged_into']} 的本地分支")
    if plan["mode"] == "abandon":
        plan["options"] = {
            "discard": "保存补丁后丢弃 worktree 和本地分支",
            "merge": f"先尝试合并已提交内容到 {plan['merge_target']}，成功后再清理" if plan["merge_target"] else "无可用合并目标",
        }
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


def _salvage(repo, plan, action):
    wt = plan["_wt"]
    if not (wt["entries"] or wt["tracked_changes"] or wt["symlinks"] or plan.get("abandon_unmerged")):
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
    if plan.get("abandon_unmerged"):
        # 未合入的已提交工作完整保存；丢弃与合并保留都先有可恢复补丁
        patch = _git(repo, "diff", "--binary", plan["_patch_base"], plan["branch"], check=True)
        (target / "branch.patch").write_text(patch.stdout, encoding="utf-8", errors="surrogateescape")
    manifest = {
        "issue": plan["issue"], "branch": plan["branch"], "head": plan["head"],
        "source_worktree": plan["worktree"], "merged_into": plan["merged_into"],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": "清场前置 salvage：未收录/未提交内容与完整分支补丁，软链未跟随",
        "mode": plan.get("mode"), "cleanup_action": action,
        "branch_patch": ({"base": plan["_patch_base"], "file": "branch.patch"}
                         if plan.get("abandon_unmerged") else None),
        "files": [{k: e[k] for k in ("rel", "kind", "size", "sha256")} for e in wt["entries"]],
        "symlinks": wt["symlinks"],
        "skipped_reproducible": wt["skipped_repro"],
        "skipped_already_in_main": wt["skipped_same"],
        "uncommitted_patch": bool(wt["tracked_changes"]),
    }
    (target / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target)


def _merge_preserve(repo, plan):
    """先合并已提交分支到授权目标；失败时不移除原 worktree/分支。"""
    if plan["merged_into"]:
        return {"target": plan["merged_into"], "commit": None, "warning": None}
    target = plan.get("merge_target")
    if not target:
        raise CleanupError("没有可用的授权合并目标")
    repo = Path(repo)
    listed = _git(repo, "worktree", "list", "--porcelain", check=True).stdout.splitlines()
    target_wt = None
    current = None
    for line in listed:
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):])
        elif line.startswith("branch ") and current is not None:
            if line[len("branch "):] == f"refs/heads/{target}":
                target_wt = current
                break
    temporary = False
    if target_wt is None:
        target_wt = repo / ".worktrees" / f"progress-merge-{plan['issue']}-{os.getpid()}"
        if target_wt.exists():
            raise CleanupError(f"临时合并目录已存在：{target_wt}")
        r = _git(repo, "worktree", "add", str(target_wt), target)
        if r.returncode:
            raise CleanupError("无法创建目标合并 worktree：" + (r.stderr or r.stdout).strip())
        temporary = True
    try:
        dirty = _git(target_wt, "status", "--porcelain", check=True).stdout.strip()
        if dirty:
            raise CleanupError(f"目标 {target} 的 worktree 有未提交改动，未执行合并；原 worktree 与分支保留")
        r = _git(target_wt, "merge", "--no-ff", "--no-edit", plan["branch"])
        if r.returncode:
            _git(target_wt, "merge", "--abort")
            raise CleanupError(
                f"合并到 {target} 失败，已中止；原 worktree 与分支保留："
                + (r.stderr or r.stdout).strip())
        commit = _git(target_wt, "rev-parse", "HEAD", check=True).stdout.strip()
        warning = None
        if temporary:
            rr = _git(repo, "worktree", "remove", str(target_wt))
            if rr.returncode:
                warning = "合并成功，但临时 worktree 清理失败：" + (rr.stderr or rr.stdout).strip()
            _git(repo, "worktree", "prune")
        return {"target": target, "commit": commit, "warning": warning}
    except CleanupError:
        if temporary and target_wt.exists():
            _git(repo, "worktree", "remove", "--force", str(target_wt))
            _git(repo, "worktree", "prune")
        raise


def execute_plan(plan, action=None):
    if not plan.get("eligible"):
        raise CleanupError(plan.get("reason") or "该节点不可清理")
    action = action or ("dev-only" if plan.get("mode") == "dev-only" else "close" if plan.get("mode") == "close" else "discard")
    if plan.get("mode") == "dev-only":
        if action not in {"close", "dev-only"}:
            raise CleanupError("无本地分支的票仅支持 dev 过程文件清理")
        action = "dev-only"
    if action not in {"close", "discard", "merge", "dev-only"}:
        raise CleanupError(f"未知清理动作：{action}")
    if plan.get("mode") == "close" and action != "close":
        raise CleanupError("已完成票只支持普通收尾清理")
    if plan.get("mode") == "abandon" and action == "close":
        raise CleanupError("中途搁置票请选择丢弃开发或合并保留")
    result = {
        "ok": True, "branch": plan["branch"], "issue": plan["issue"], "action": action,
        "salvage_dir": None, "salvaged_files": 0,
        "branch_patch_saved": bool(plan.get("abandon_unmerged")),
        "worktree_removed": False, "branch_deleted": False,
        "merged_into": plan["merged_into"], "merge_commit": None,
        "dev_files_removed": 0, "bytes_freed": 0, "warnings": [],
    }
    # _repo 是主工作区，绝不从 worktree 路径猜测。
    repo = Path(plan["_repo"])
    salvage_dir = _salvage(repo, plan, action)
    result["salvage_dir"] = salvage_dir
    result["salvaged_files"] = len(plan["_wt"]["entries"])
    if action == "merge":
        try:
            merged = _merge_preserve(repo, plan)
        except CleanupError as exc:
            result["ok"] = False
            result["error"] = str(exc)
            return result
        result["merged_into"] = merged["target"]
        result["merge_commit"] = merged["commit"]
        if merged["warning"]:
            result["warnings"].append(merged["warning"])

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


def _replace_issue_status(text):
    """只替换 frontmatter 或正文首个 yaml 围栏里的 status，保留其余内容。"""
    if text.startswith("---"):
        if not re.search(r"^status:\s*.*$", text, re.M):
            raise CleanupError("票 frontmatter 缺少 status 字段，未修改")
        return re.sub(r"^status:\s*.*$", "status: deferred", text, count=1, flags=re.M)
    match = re.search(r"```yaml\s*\n(.*?)```", text, re.S)
    if not match:
        raise CleanupError("票文件缺少可写的 status 字段，未修改")
    block = match.group(1)
    if not re.search(r"^status:\s*.*$", block, re.M):
        raise CleanupError("票 yaml 块缺少 status 字段，未修改")
    updated = re.sub(r"^status:\s*.*$", "status: deferred", block, count=1, flags=re.M)
    return text[:match.start(1)] + updated + text[match.end(1):]


def close_issue(ticket, reason=""):
    """关闭需求：仅状态改为 deferred + digest，不触碰分支/worktree/dev。"""
    if ticket.get("status") == "done":
        raise CleanupError("该需求已关闭或已完成，无需重复关闭")
    path = Path(ticket["issue_path"])
    text = path.read_text(encoding="utf-8")
    updated = _replace_issue_status(text)
    if updated == text:
        raise CleanupError("票状态未发生变化")
    tmp = path.with_name(path.name + ".close-tmp")
    try:
        tmp.write_text(updated, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    reason = " ".join(str(reason or "中途搁置，用户确认关闭").split())[:200]
    note = f"关闭需求：{ticket['status']} → deferred；原因：{reason}"
    event = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "event": "digest", "issue": ticket["id"], "host": "dsh", "note": note,
    }
    with open(Path(ticket["feature_dir"]) / "progress.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return {"ok": True, "issue": ticket["id"], "previous_status": ticket["status"],
            "status": "deferred", "event": event}
