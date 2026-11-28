#!/usr/bin/env python3
"""progress — 单人多宿主开发调度台（原型）

形态：一句结论 + 一条事件流 + 一条下钻路。
  status   各项目状态句 + 在制票活性行（终端，0 token）
  replay   事件流时间线回放
  demo     生成演示工作区

数据架构：
  事件流 = dev/features/<module>/<slug>/progress.jsonl（监督循环唯一写者，稀疏持久）
  现状   = 票 frontmatter（status / blocked-by / verify-fails / dispatch 块）
  活性   = 查询时采样，不落盘（worktree git 活动 / 文件 mtime / impl-log 轮次尾部）

零依赖，Python 3.9+ stdlib。
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# Isolated Python (-I) must still resolve the standard-library sibling layer.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_scope
JOURNAL_NAME = "progress.jsonl"
STALL_DEFAULT_MIN = 30
GIT_WORKERS = 8
# 活性探针不扫 dev/（实体副本可达数 GB）；主工作区 impl 目录单独探测。
SKIP_DIRS = {
    ".git", ".logs", ".local", "dev", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "dist", "build", ".next", ".turbo", "coverage",
}

STATUS_ORDER = ["needs-human", "in-progress", "in-review", "mr-submitted", "merged", "confirmed", "proposed"]
STATUS_LABEL = {
    "proposed": "待确认",
    "confirmed": "可派发",
    "in-progress": "在跑",
    "in-review": "评审中",
    "mr-submitted": "待合并",
    "merged": "已合入待验收",
    "needs-human": "等人",
    "done": "已验收",
}
# 真实仓库的状态词变体 → 规范词（pandu 503 票实测）
CLOSED_ALIASES = {"done-review", "done-human", "superseded", "cancelled", "deferred"}
STATUS_ALIAS = {"implementing": "in-progress", "review-pending": "in-review", "analysis": "confirmed"}
ISSUE_ID_RE = re.compile(r"issue-\d+[a-z0-9]*", re.I)
# 开发中泳道：在制口径只看票状态；会话暂停 / 分支存在不参与需求级判定。
# merged 已合入集成分支但 feature candidate 尚未验收，故仍属于开发中。
CANON_STATUSES = ("in-progress", "in-review", "mr-submitted", "merged", "needs-human")
# blocked-by/frontier 的完成口径：merged 表示已进入集成分支，done 表示 feature 已验收。
DEPENDENCY_SATISFIED = ("merged", "done")
BRANCH_HOSTS = {"claude", "codex", "zcode", "deepseek"}
EXTERNAL_BODY_RE = re.compile(
    r"(?:外部前置|外部依赖)\s*(?:[①②③④0-9]+\s*)?[：:]\s*"
    r"([^，。；\n]{1,50}?)\s+(issue-\d+[a-z0-9]*)", re.I)


# ---------- 基础解析 ----------

def coerce(v):
    v = v.strip().strip("'\"")
    if v.startswith("[") and v.endswith("]"):
        return [x.strip() for x in v[1:-1].split(",") if x.strip()]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def parse_frontmatter(path):
    """极简元数据解析：frontmatter 平铺键 + 一层嵌套块（dispatch）。
    非标票（pandu 实测）把元数据写在正文第一个 ```yaml 围栏块，同样解析。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    block = None
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            block = text[3:end]
    else:
        mstart = text.find("```yaml")
        if mstart >= 0:
            mend = text.find("```", mstart + 7)
            if mend > mstart:
                block = text[mstart + 7:mend]
    if block is None:
        return {}
    data, parent = {}, None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#") or ":" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        key, _, val = line.partition(":")
        key, val = key.strip(), re.split(r"\s+#", val.strip())[0].strip()
        if indent == 0:
            if val == "":
                # 空值先记 None（与 ticket_context/agents_config 的 YAML 口径一致）；
                # 只有后续出现缩进子行时才升级为块，避免把空标量误当成空 mapping。
                data[key] = None
                parent = key
            else:
                data[key] = coerce(val)
                parent = None
        elif parent:
            if data.get(parent) is None:
                data[parent] = {}
            if isinstance(data.get(parent), dict):
                data[parent][key] = coerce(val)
    return data


def parse_ts(s):
    if not isinstance(s, str) or not s.strip():
        return None
    text = s.strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        pass
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
        return value.timestamp()
    except ValueError:
        return None


def load_events(feature_dir):
    """事件流：宽容解析，坏行跳过（词汇卫生原则）。"""
    path = feature_dir / JOURNAL_NAME
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("event"):
                    ev["_ts"] = parse_ts(ev.get("ts")) or 0
                    events.append(ev)
            except json.JSONDecodeError:
                pass
    events.sort(key=lambda e: e["_ts"])
    return events


def find_features(repo):
    root = Path(repo) / "dev" / "features"
    if not root.is_dir():
        return []
    return [directory for directory in artifact_scope.scopes(repo)
            if (directory / "issues").is_dir()]


# ---------- 事件流折叠 ----------

def last_event(events, issue, *names):
    for ev in reversed(events):
        if ev.get("issue") == issue and ev.get("event") in names:
            return ev
    return None


def decision_projection(repo, feature_dir, issue_id, status):
    """Project the sole active human-decision packet, never a needs-human note."""
    feature = artifact_scope.scope_key(feature_dir)
    if status != "needs-human" and not (Path(feature_dir) / "decisions").is_dir():
        return {"decision": None, "contract_error": None, "human_note": ""}
    if status == "needs-human" and not (Path(feature_dir) / "decisions").is_dir():
        return {"decision": None,
                "contract_error": "needs-human 缺少活动 human_decision 包；旧事件备注不再充当决定",
                "human_note": ""}
    try:
        import human_decision
        packet = human_decision.active_for_issue(repo, feature, issue_id)
        if packet is not None:
            question = packet.get("question") or {}
            return {"decision": packet, "contract_error": None,
                    "human_note": question.get("decision") or ""}
        if status == "needs-human":
            return {"decision": None,
                    "contract_error": "needs-human 缺少活动 human_decision 包；旧事件备注不再充当决定",
                    "human_note": ""}
    except Exception as exc:
        return {"decision": None, "contract_error": "human_decision 活动包契约错误：" + str(exc),
                "human_note": ""}
    return {"decision": None, "contract_error": None, "human_note": ""}


def dispatch_count(events, issue):
    return sum(1 for e in events if e.get("issue") == issue and e.get("event") == "dispatch")


def dispatch_desc(ev):
    if not ev:
        return None
    parts = [f"{ev.get('host', '?')}·{ev.get('model', '?')}"]
    if ev.get("attempts", 1) > 1:
        parts.append(f"第 {ev['attempts']} 次派发")
    return " ".join(parts)


# ---------- 活性探针（查询时采样，不落盘） ----------

def newest_file(root, skip=SKIP_DIRS, budget=5000):
    newest_ts, newest_path = 0.0, None
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        seen += len(filenames)
        if seen > budget:
            break  # 活性探针只要"最近动静"，超大树抽样即止
        for f in filenames:
            if f in skip:
                continue  # linked worktree 的 .git 是文件，也要跳过
            p = Path(dirpath) / f
            try:
                if p.is_symlink():
                    continue  # dev 软链等不是工作痕迹
                m = p.lstat().st_mtime
            except OSError:
                continue
            if m > newest_ts:
                newest_ts, newest_path = m, p
    return newest_ts, newest_path


def git_last_commit_ts(repo_dir):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(out.stdout.strip())
    except Exception:
        pass
    return 0


def last_round_text(impl_dir):
    """impl-log 轮次尾部：最近一轮在做什么（黑盒期的过程叙事，下钻素材的免费投影）。"""
    newest_ts, newest_path = newest_file(impl_dir)
    if not newest_path:
        return None
    try:
        text = newest_path.read_text(encoding="utf-8")
    except OSError:
        return None
    hits = re.findall(r"^\s*[-*]\s+(.*第\s*\d+\s*轮.*)$", text, re.M)
    if not hits:
        return None
    return hits[-1].strip()[:60]


def worktree_index(repo, branches=None):
    """issue-id → [{dir, hint}]。真实仓库的 worktree 分散：分支名带票号（任意前缀）、
    .worktrees/<id> 约定路径、宿主自管目录（.codex/.claude worktrees）。
    票号跨 feature 撞号（pandu 实测），hint 留作作用域消歧。"""
    if branches is None:
        main = detect_main_branch(repo)
        branches = collect_branches(repo, main) or {}
    idx = {}
    for name, b in branches.items():
        wt = b.get("worktree")
        if not wt:
            continue
        m = ISSUE_ID_RE.search(name)
        if m:
            idx.setdefault(m.group(0).lower(), []).append({"dir": wt, "hint": f"{name} {wt}"})
    conv = Path(repo) / ".worktrees"
    if conv.is_dir():
        for d in conv.iterdir():
            if d.is_dir() and ISSUE_ID_RE.fullmatch(d.name):
                idx.setdefault(d.name.lower(), []).append({"dir": d, "hint": str(d)})
    return idx


def probe_issue(repo, feature_dir, issue_id, wt_entries=()):
    """在制票活性：取各 worktree 与 impl 目录两边最新副作用的较大者。"""
    cands = []  # (ts, kind, detail)
    for e in wt_entries:
        wd = e["dir"] if isinstance(e, dict) else e
        if not wd or not wd.is_dir():
            continue
        gts = git_last_commit_ts(wd)
        mts, mpath = newest_file(wd)
        rel = mpath.relative_to(wd) if mpath else "?"
        ts = max(gts, mts)
        # 文件 mtime 与提交时间常在同秒级，差 2 分钟内认作提交
        kind = "commit" if gts and gts >= mts - 120 else "touch"
        cands.append((ts, kind, str(rel)))
    impl = feature_dir / "impl" / issue_id
    if impl.is_dir():
        mts, mpath = newest_file(impl)
        if mpath:
            cands.append((mts, "impl", mpath.name))
    if not cands:
        return None
    ts, kind, detail = max(cands)
    return {"ts": ts, "kind": kind, "detail": detail, "round": last_round_text(impl)}


def rel_age(minutes):
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes:.0f} 分钟"
    if minutes < 1440:
        h = minutes / 60
        return f"{h:.0f} 小时" if h >= 10 else f"{h:.1f} 小时"
    return f"{minutes / 1440:.0f} 天"


# ---------- 票装配 ----------

def load_issues(repo, feature_dir, events, wt_index=None):
    issues = []
    satisfied_ids = set()
    raw = []
    for f in sorted((feature_dir / "issues").glob("*.md")):
        fm = parse_frontmatter(f)
        m = ISSUE_ID_RE.search(f.stem)
        iid = m.group(0).lower() if m else f.stem
        st = fm.get("status", "未知")
        closed_as = st if st in {"superseded", "cancelled", "deferred"} else None
        st = "done" if st in CLOSED_ALIASES else STATUS_ALIAS.get(st, st)
        title = fm.get("title") or (f.stem[m.end():].lstrip("-_") if m else f.stem)
        it = {
            "id": iid,
            "key": artifact_scope.scope_key(feature_dir) + "/" + f.stem if artifact_scope.campaign_id(feature_dir) else f.stem,
            "issue_path": str(f),
            "title": title,
            "status": st,
            "closed_as": closed_as,
            "feature": artifact_scope.scope_key(feature_dir),
            "blocked_by": fm.get("blocked-by") or [],
            "verify_fails": fm.get("verify-fails", 0) or 0,
            "fm": fm,
        }
        if it["status"] in DEPENDENCY_SATISFIED and not closed_as:
            # merged 已合入集成分支、done 已通过 feature 验收；两者都解锁下游。
            # 退役/取消（映射为 done 但带 closed_as）不等于完成，不解锁依赖。
            satisfied_ids.add(it["id"])
        raw.append(it)
    for it in raw:
        if it["status"] in ("in-progress", "in-review", "mr-submitted", "merged", "needs-human"):
            ev = last_event(events, it["id"], "dispatch")
            it["dispatch"] = dispatch_desc(ev)
            it["dispatch_n"] = dispatch_count(events, it["id"])
        if it["status"] == "in-progress":
            ents = (wt_index or {}).get(it["id"], ())
            # 票号撞号消歧：优先本 feature（模块/slug）名出现在分支名或路径里的 worktree
            pref = [e for e in ents
                    if ((artifact_scope.branch_key(feature_dir) + "-" + it["id"]) in e["hint"]
                        if artifact_scope.campaign_id(feature_dir) else
                        feature_dir.parent.name in e["hint"] or feature_dir.name in e["hint"])]
            it["probe"] = probe_issue(repo, feature_dir, it["id"], pref or ents)
        projection = decision_projection(repo, feature_dir, it["id"], it["status"])
        it.update(projection)
        if it["status"] == "needs-human":
            ev = last_event(events, it["id"], "needs-human")
            if ev:
                it["human_ts"] = ev.get("ts")
        if it["status"] == "mr-submitted":
            ev = last_event(events, it["id"], "mr-submitted")
            it["mr"] = ev.get("note", "") if ev else ""
            it["mr_ts"] = ev.get("ts") if ev else None
        if it["status"] == "merged":
            # 票已合入目标集成分支；worktree/branch 保留到 feature finalize。
            ev = last_event(events, it["id"], "merged")
            it["merged_target"] = (ev or {}).get("target") or ""
            it["merged_commit"] = (ev or {}).get("commit") or ""
            it["merged_ts"] = (ev or {}).get("ts") if ev else None
        if it["status"] == "confirmed":
            it["ready"] = all(b in satisfied_ids for b in it["blocked_by"])
        reviews = feature_dir / "reviews"
        if reviews.is_dir():
            it["review_rounds"] = len(list(reviews.glob(f"{it['id']}*/*.md")))
        issues.append(it)
    return issues


# ---------- 渲染：status ----------

def issue_line(it, now, stall_min):
    st = it["status"]
    mark = {"needs-human": "●等人  ", "in-progress": "●在跑  ", "in-review": "●评审  ",
            "mr-submitted": "●待合并", "merged": "●待验收", "confirmed": "●可派发",
            "proposed": "●待确认"}.get(st, "●?     ")
    head = f"  {mark}  {it['id']}"
    bits = []
    if st == "in-progress":
        p = it.get("probe")
        dispatch = it.get("dispatch") or "未登记派发"
        if p:
            age = (now - p["ts"]) / 60
            if age >= stall_min:
                mark = "●卡？  "
                head = f"  {mark}  {it['id']}"
                bits.append(f"{rel_age(age)}无动静")
            elif p["kind"] == "commit":
                bits.append(f"{rel_age(age)}前有提交（{p['detail']}）")
            else:
                bits.append(f"{rel_age(age)}前有动作（{p['detail']}）")
            if p.get("round"):
                bits.append(f"最后一轮：{p['round']}")
        bits.insert(0, dispatch)
        if it["verify_fails"]:
            bits.append(f"复跑打回 {it['verify_fails']} 次")
    elif st == "mr-submitted":
        if it.get("mr"):
            bits.append(it["mr"])
        if it.get("mr_ts"):
            t = parse_ts(it["mr_ts"])
            if t:
                bits.append(f"{rel_age((now - t) / 60)}前进入合并队列")
        bits.append("等监督收口")
    elif st == "merged":
        target = it.get("merged_target") or "集成分支"
        bits.append(f"已合入 {target}")
        if it.get("merged_ts"):
            t = parse_ts(it["merged_ts"])
            if t:
                bits.append(f"{rel_age((now - t) / 60)}前合入")
        bits.append("等 feature 验收")
    elif st == "needs-human":
        if it.get("review_rounds"):
            bits.append(f"评审第 {it['review_rounds']} 轮")
        if it.get("human_ts"):
            t = parse_ts(it["human_ts"])
            if t:
                bits.append(datetime.fromtimestamp(t).strftime("%H:%M") + " 起")
        if it.get("human_note"):
            bits.append(it["human_note"])
    elif st == "confirmed":
        if it["ready"]:
            bits.append("frontier 就绪，可派发")
        else:
            waiting = [b for b in it["blocked_by"]]
            bits.append(f"blocked-by：{'、'.join(waiting) if waiting else '未知'}")
    elif st == "in-review":
        if it.get("dispatch"):
            bits.append(it["dispatch"])
        if it.get("review_rounds"):
            bits.append(f"评审第 {it['review_rounds']} 轮")
        bits.append("评审对话进行中")
    line = f"{head}   " + " · ".join(bits)
    if it["title"] and it["title"] != it["id"]:
        line += f"\n          {it['title']}"
    return line, mark


def status_command(args):
    projects = resolve_projects(args)
    now = time.time()
    project_chunks, fleet = [], {"total": 0, "done": 0, "merged": 0, "run": 0,
                                 "review": 0, "mr": 0, "human": 0, "ready": 0}
    for name, repo in projects:
        if args.project and name != args.project:
            continue
        chunk, c = render_project(name, repo, args.stall_min, now)
        if chunk is None:
            continue
        project_chunks.append(chunk)
        for k in fleet:
            fleet[k] += c.get(k, 0)
    if len(project_chunks) > 1:
        shown = (fleet["done"] + fleet["merged"] + fleet["run"] + fleet["review"]
                 + fleet["mr"] + fleet["human"] + fleet["ready"])
        other = fleet["total"] - shown
        bits = [f"{fleet['total']} 票"]
        if fleet["done"]:
            bits.append(f"{fleet['done']} 已验收")
        if fleet["merged"]:
            bits.append(f"{fleet['merged']} 待验收")
        if fleet["run"]:
            bits.append(f"{fleet['run']} 在跑")
        if fleet["review"]:
            bits.append(f"{fleet['review']} 评审中")
        if fleet["human"]:
            bits.append(f"{fleet['human']} 等你")
        if fleet["mr"]:
            bits.append(f"{fleet['mr']} 待合并")
        print("舰队 " + " · ".join(bits) + (f" · {other} 其他" if other > 0 else ""))
        print()
    for chunk in project_chunks:
        print(chunk)
        print()


def render_project(name, repo, stall_min, now):
    """返回 (文本块, 仓库级统计)。状态句按 feature 独立计数。"""
    feats = find_features(repo)
    if not feats:
        return None, {}
    out = [f"【{name}】"]
    c = {"total": 0, "done": 0, "merged": 0, "run": 0, "review": 0, "mr": 0,
         "human": 0, "ready": 0, "other": 0}
    wt_index = worktree_index(repo)
    for feat in feats:
        events = load_events(feat)
        issues = load_issues(repo, feat, events, wt_index)
        cf = dict.fromkeys(c, 0)
        for it in issues:
            cf["total"] += 1
            c["total"] += 1
            s = it["status"]
            key = {"done": "done", "merged": "merged", "in-progress": "run", "in-review": "review",
                   "mr-submitted": "mr", "needs-human": "human", "confirmed": "ready"}.get(s, "other")
            cf[key] += 1
            c[key] += 1
        slug = artifact_scope.scope_key(feat)
        lines = []
        for it in sorted(issues, key=lambda x: (STATUS_ORDER.index(x["status"]) if x["status"] in STATUS_ORDER else 99, x["id"])):
            if it["status"] == "done":
                continue
            if it["status"] == "confirmed" and not it["ready"]:
                continue  # 大仓取舍：未就绪的待派发票只进计数，不占行
            lines.append(issue_line(it, now, stall_min)[0])
        if cf["total"] and cf["total"] == cf["done"]:
            out.append(f"{slug} —— {cf['total']} 票全部验收，无遗留。整体正常，无需干预。")
            continue
        parts = []
        if cf["done"]:
            parts.append(f"{cf['done']} 已验收")
        if cf["merged"]:
            parts.append(f"{cf['merged']} 已合入待验收")
        if cf["mr"]:
            parts.append(f"{cf['mr']} 待合并")
        if cf["run"]:
            parts.append(f"{cf['run']} 在跑")
        if cf["review"]:
            parts.append(f"{cf['review']} 评审中")
        if cf["human"]:
            parts.append(f"{cf['human']} 等你")
        if cf["ready"]:
            parts.append(f"{cf['ready']} 可派发")
        if cf["other"]:
            parts.append(f"{cf['other']} 其他")
        head = f"{slug} —— {cf['total']} 票：{'，'.join(parts)}。"
        notes = []
        for it in issues:
            if it["status"] == "in-progress" and it.get("probe"):
                age = (now - it["probe"]["ts"]) / 60
                if age >= stall_min:
                    notes.append(f"要留意 {it['id']} 已 {rel_age(age)}无动静")
        if cf["human"]:
            waiting = [it["id"] for it in issues if it["status"] == "needs-human"]
            notes.append("、".join(waiting) + " 等你裁决")
        head += ("；".join(notes) + "；其余正常。") if notes else "整体正常，无需干预。"
        out.append(head)
        if lines:
            out.append("")
            out.extend(lines)
        out.append("")
    return "\n".join(out).rstrip(), c


# ---------- git 分支层（分支树视图） ----------

def git_run(repo, *cmd_args, timeout=10):
    try:
        return subprocess.run(["git", "-C", str(repo), *cmd_args],
                              capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def pick_repo(args):
    """单仓库定位：--repo 优先，否则从当前目录向上找 dev/features。"""
    if getattr(args, "repo", None):
        return Path(args.repo).resolve()
    p = Path.cwd().resolve()
    for d in [p, *p.parents]:
        if (d / "dev" / "features").is_dir():
            return d
    print("当前目录往上没找到 dev/features——请在项目仓库根目录运行，或用 --repo 指定仓库根。", file=sys.stderr)
    sys.exit(2)


def detect_main_branch(repo):
    cfg = Path(repo) / "dev" / "agents-config.md"
    if cfg.exists():
        m = re.search(r"^main-branch:\s*(\S+)", cfg.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip("'\"")
    for cand in ("main", "master"):
        r = git_run(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{cand}")
        if r and r.returncode == 0:
            return cand
    return None


def inspect_branches(repo, main, records):
    """只对调用方选定的分支计算 ahead/behind/dirty；done 残留按需预检时再查。"""
    tasks = [(b["name"], b) for b in records
             if b["name"] != main and (b.get("worktree") or ISSUE_ID_RE.search(b["name"]))]

    def inspect(item):
        name, b = item
        behind = ahead = dirty = 0
        r = git_run(repo, "rev-list", "--left-right", "--count", f"{main}...{name}")
        if r and r.returncode == 0 and len(r.stdout.split()) == 2:
            behind, ahead = (int(x) for x in r.stdout.split())
        wt = b.get("worktree_path") or b.get("worktree")
        if wt and hasattr(wt, "is_dir") and wt.is_dir():
            r = git_run(wt, "status", "--porcelain")
            if r and r.returncode == 0:
                dirty = sum(1 for l in r.stdout.splitlines() if l.strip())
        return name, behind, ahead, dirty

    with concurrent.futures.ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:
        for name, behind, ahead, dirty in pool.map(inspect, tasks):
            for b in records:
                if b["name"] == name:
                    b["behind"] = behind
                    b["ahead"] = ahead
                    b["dirty"] = dirty
                    break


def collect_branches(repo, main, inspect=True):
    r = git_run(repo, "for-each-ref", "refs/heads",
                "--format=%(refname:short)%09%(objectname:short)%09%(committerdate:unix)")
    if r is None or r.returncode != 0:
        return None
    branches = {}
    for line in r.stdout.splitlines():
        parts = line.split("	")
        if len(parts) < 3:
            continue
        ts = int(parts[2]) if parts[2].isdigit() else 0
        branches[parts[0]] = {"name": parts[0], "sha": parts[1][:7], "ts": ts,
                              "worktree": None, "merged": False, "ahead": 0, "behind": 0, "dirty": 0}
    r = git_run(repo, "worktree", "list", "--porcelain")
    if r and r.returncode == 0:
        cur = None
        for line in r.stdout.splitlines():
            if line.startswith("worktree "):
                cur = Path(line[len("worktree "):])
            elif line.startswith("branch ") and cur is not None:
                br = line[len("branch "):].replace("refs/heads/", "")
                if br in branches:
                    branches[br]["worktree"] = cur
    r = git_run(repo, "branch", "--merged", main, "--format=%(refname:short)")
    if r and r.returncode == 0:
        for line in r.stdout.splitlines():
            n = line.strip()
            if n in branches:
                branches[n]["merged"] = True
    if inspect:
        inspect_branches(repo, main, list(branches.values()))
    return branches


# ---------- 跨 agent 实时绑定（各宿主 session-title 状态） ----------

SESSION_STATE_FILES = {
    "claude": ".claude/session-title.json",
    "codex": ".codex/session-title.json",
    "zcode": ".zcode/cli/session-title.json",
    "deepseek": ".dsh/session-title.json",
}


def load_session_bindings(repo):
    """读取四个宿主的会话命名状态，返回绑定到本仓的会话列表。"""
    repo = str(Path(repo).resolve())
    out = []
    for host, rel in SESSION_STATE_FILES.items():
        try:
            data = json.loads((Path.home() / rel).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for sid, v in (data.get("sessions") or {}).items():
            if v.get("released_at"):
                continue
            r = str(v.get("repo_root") or v.get("repo") or "")
            worktree = str(v.get("worktree") or "")
            if r != repo and not r.startswith(repo + "/") and not worktree.startswith(repo + "/"):
                continue
            issue_path = str(v.get("issue") or "")
            mi = ISSUE_ID_RE.search(Path(issue_path).stem) if issue_path else None
            feat = str(v.get("feature") or "")
            feat_short = feat.split("features/", 1)[-1] if "features/" in feat else feat
            out.append({
                "session_id": sid,
                "host": host,
                "role": v.get("role") or "",
                "paused": bool(v.get("paused")),
                "feature": feat_short,
                "issue": mi.group(0).lower() if mi else None,
                "repo_root": r, "worktree": worktree or None,
                "branch": v.get("branch"), "revision": v.get("revision", 0),
                "bound_at": v.get("bound_at"), "last_seen": v.get("last_seen"),
                "title": v.get("last_title", ""),
            })
    return out


def resolve_ticket(tix_multi, iid, hint):
    """票号跨 feature 撞号（pandu 实测）：先按 feature 名出现在 hint 里匹配，再用在跑优先。"""
    cands = tix_multi.get(iid) or []
    if len(cands) <= 1:
        return cands[0] if cands else None
    if any("/campaigns/" in c["feature"] for c in cands):
        matches = [c for c in cands if (artifact_scope.branch_key(Path("dev/features") / c["feature"]) + "-" + iid) in hint
                   or (c["feature"] + "/issues/" + iid) in hint]
        return matches[0] if len(matches) == 1 else None
    for c in cands:
        if c["feature"].split("/")[0] in hint or c["feature"].split("/")[-1] in hint:
            return c
    for c in cands:
        if c["status"] == "in-progress":
            return c
    for c in cands:
        if c["status"] != "done":
            return c
    return cands[0]


def tree_command(args):
    repo = pick_repo(args)
    now = time.time()
    main = detect_main_branch(repo)
    branches = collect_branches(repo, main) if main else None
    if branches is None:
        print("这里读不到 git 分支（非 git 仓库，或未识别主干分支）。分支树视图需要 git。")
        return
    wt_index = worktree_index(repo, branches)
    tix_multi = {}
    for feat in find_features(repo):
        for it in load_issues(repo, feat, load_events(feat), wt_index):
            tix_multi.setdefault(it["id"], []).append(it)

    main_b = branches.pop(main, None)
    rows, residue = [], {"backup": 0, "done": 0, "other": 0}
    for name, b in branches.items():
        m = ISSUE_ID_RE.search(name)
        cands = tix_multi.get(m.group(0).lower(), []) if m else []
        it = resolve_ticket(tix_multi, m.group(0).lower(), f"{name} {b.get('worktree') or ''}") if m else None
        if name.startswith(("backup/", "codex/preserve")):
            residue["backup" if name.startswith("backup/") else "other"] += 1
        elif b["worktree"]:
            rows.append((name, b, it))
        elif it and it["status"] != "done" and (
                len(cands) == 1
                or any(p in name for c in cands for p in c["feature"].split("/"))):
            rows.append((name, b, it))
        elif it and it["status"] == "done":
            residue["done"] += 1
        else:
            residue["other"] += 1

    def row_order(row):
        it = row[2]
        if it is None:
            return (3, row[0])
        if it["status"] == "done":
            return (2, row[0])
        if it["status"] == "merged":
            return (1, row[0])
        if it["status"] == "mr-submitted":
            return (1, row[0])
        return (0, row[0])

    rows.sort(key=row_order)
    wip = sum(1 for _, _, it in rows if it and it["status"] == "in-progress")
    orphan = sum(1 for _, b, it in rows if it is None and b["worktree"])
    head = f"分支树 —— 主干 {main}"
    if main_b:
        head += f" @{main_b['sha']}"
    head += f" · 活跃分支 {len(rows)}（在制 {wip} · 集成/无票 {orphan}）"
    if any(residue.values()):
        head += f" · 不展示 {sum(residue.values())}（backup {residue['backup']} · 已完成票 {residue['done']} · 其他 {residue['other']}）"
    print(head)
    print()
    lines = []
    if main_b:
        lines.append(f"  {main}  {main_b['sha']}  {rel_age((now - main_b['ts']) / 60)}前推进")
    n = len(rows)
    for i, (name, b, it) in enumerate(rows):
        prefix = "  ├─" if i < n - 1 else "  └─"
        if it is None:
            bits = [name, "集成分支" if b["worktree"] else "? 无对应票（残留？）"]
        else:
            st = it["status"]
            if st == "done":
                bits = [name, "✓ 已完成 · 分支残留可删"]
            else:
                bits = [name, f"{STATUS_LABEL.get(st, st)} · {it.get('dispatch') or '未登记派发'}"]
        if b["ahead"] or b["behind"]:
            bits.append(f"↑{b['ahead']}↓{b['behind']}")
        if b["dirty"]:
            bits.append(f"脏 {b['dirty']} 文件")
        ts = b["ts"]
        if b["worktree"] and b["worktree"].is_dir():
            mts, _ = newest_file(b["worktree"])
            ts = max(ts, mts)
        if ts:
            age = (now - ts) / 60
            warn = "⚠ " if it and it["status"] == "in-progress" and age >= args.stall_min else ""
            bits.append(f"{warn}{rel_age(age)}前")
        lines.append(f"{prefix} " + " · ".join(x for x in bits if x))
    print("\n".join(lines))


# ---------- 跨 agent 聚合 ----------

def hosts_command(args):
    repo = pick_repo(args)
    now = time.time()
    window_start = now - args.hours * 3600
    buckets = {}

    def bucket(host, model):
        key = f"{host}·{model}" if host else "未登记派发"
        return buckets.setdefault(key, {"wip": [], "human": [], "merged": 0, "dispatches": 0, "fails": 0})

    issue_host = {}
    wt_index = worktree_index(repo)
    for feat in find_features(repo):
        events = load_events(feat)
        issues = load_issues(repo, feat, events, wt_index)
        for it in issues:
            host = model = None
            ev = last_event(events, it["id"], "dispatch")
            if ev:
                host, model = ev.get("host"), ev.get("model")
            elif isinstance(it.get("fm", {}).get("dispatch"), dict):
                d = it["fm"]["dispatch"]
                host, model = d.get("host"), d.get("model")
            if host:
                issue_host[it["id"]] = (host, model)
            if it["status"] in ("in-progress", "in-review"):
                bucket(host, model)["wip"].append(it)
            if it["status"] == "needs-human":
                bucket(host, model)["human"].append(it)
            if it["verify_fails"]:
                bucket(host, model)["fails"] += it["verify_fails"]
        for e in events:
            if e["_ts"] < window_start:
                continue
            host, model = e.get("host"), e.get("model")
            ih = issue_host.get(e.get("issue"))
            if ih:
                if not host:
                    host = ih[0]
                if not model:
                    model = ih[1]
            if e["event"] == "dispatch":
                bucket(host, model)["dispatches"] += 1
            elif e["event"] == "merged":
                bucket(host, model)["merged"] += 1
    wip_total = sum(len(b["wip"]) for b in buckets.values())
    human_total = sum(len(b["human"]) for b in buckets.values())
    print(f"跨 agent 聚合 —— 在制 {wip_total} · 近 {args.hours:.0f} 小时：派发 "
          f"{sum(b['dispatches'] for b in buckets.values())} · 合并 "
          f"{sum(b['merged'] for b in buckets.values())} · 等你 {human_total}")
    print()
    order = sorted(buckets,
                   key=lambda k: (-(len(buckets[k]["wip"]) + len(buckets[k]["human"])),
                                  -buckets[k]["merged"], k))
    for key in order:
        b = buckets[key]
        bits = []
        for it in b["wip"]:
            if it["status"] == "in-review":
                bits.append(f"{it['id']}（评审中）")
                continue
            p = it.get("probe")
            note = ""
            if p:
                age = (now - p["ts"]) / 60
                note = f"（⚠ {rel_age(age)}无动静）" if age >= args.stall_min else (
                    f"（{rel_age(age)}前活跃）" if age < 120 else "")
            bits.append(f"{it['id']}{note}")
        for it in b["human"]:
            bits.append(f"{it['id']}（等人）")
        if b["wip"] and b["human"]:
            left = f"在制 {len(b['wip'])} + 等你 {len(b['human'])}"
        elif b["wip"]:
            left = f"在制 {len(b['wip'])}"
        elif b["human"]:
            left = f"等你 {len(b['human'])}"
        else:
            left = "空闲"
        right = []
        if b["merged"]:
            right.append(f"合并 {b['merged']}")
        if b["dispatches"]:
            right.append(f"派发 {b['dispatches']}")
        if b["fails"]:
            right.append(f"打回 {b['fails']}")
        line = f"  ⚙ {key}   {left}"
        if bits:
            line += f"：{'、'.join(bits)}"
        if right:
            line += f"   {' · '.join(right)}"
        print(line)


# ---------- 渲染：replay ----------

EVENT_WIDTH = 13


def fmt_event(ev, now):
    t = ev["_ts"]
    clock = datetime.fromtimestamp(t).strftime("%H:%M") if t else "  ??:??"
    name = ev["event"]
    issue = ev.get("issue", "")
    detail = []
    if name == "dispatch":
        detail.append(f"→ {ev.get('host', '?')}·{ev.get('model', '?')}")
        if ev.get("attempts", 1) > 1:
            detail.append(f"（第 {ev['attempts']} 次）")
    elif name == "verify-fail":
        detail.append(f"第 {ev.get('n', '?')} 次")
    elif name in ("merged", "feature-merged", "accepted"):
        if ev.get("target"):
            detail.append(str(ev["target"]))
        if ev.get("commit"):
            detail.append(ev["commit"])
    elif name in ("feature-verified", "feature-confirmed"):
        if ev.get("tree"):
            detail.append(f"tree {ev['tree'][:8]}")
    elif name in ("close-clean", "reconcile"):
        if ev.get("state"):
            detail.append(str(ev["state"]))
        if ev.get("cleanup_operation_id"):
            detail.append("op " + str(ev["cleanup_operation_id"]))
    if ev.get("note"):
        detail.append(ev["note"])
    age = (now - t) / 60 if t else 0
    age_s = f"（{rel_age(age)}前）" if t and age < 120 else ""
    return f"  {clock}  {name.ljust(EVENT_WIDTH)}{issue.ljust(11)}{' '.join(detail)}{age_s}"


def replay_command(args):
    projects = resolve_projects(args)
    now = time.time()
    cutoff = now - args.hours * 3600
    for name, repo in projects:
        if args.project and name != args.project:
            continue
        for feat in find_features(repo):
            events = load_events(feat)
            shown = []
            for ev in events:
                if ev["_ts"] < cutoff:
                    continue
                if args.host and ev.get("host") != args.host:
                    continue
                if args.issue and ev.get("issue") != args.issue:
                    continue
                shown.append(ev)
            if not shown:
                continue
            slug = artifact_scope.scope_key(feat)
            span = ""
            if shown[0]["_ts"] and shown[-1]["_ts"]:
                a = datetime.fromtimestamp(shown[0]["_ts"]).strftime("%m-%d %H:%M")
                b_fmt = "%H:%M" if datetime.fromtimestamp(shown[0]["_ts"]).date() == datetime.fromtimestamp(shown[-1]["_ts"]).date() else "%m-%d %H:%M"
                b = datetime.fromtimestamp(shown[-1]["_ts"]).strftime(b_fmt)
                span = f"（{a} → {b}，共 {len(shown)} 条）"
            print(f"【{name}】{slug} —— 事件流{span}")
            for ev in shown:
                print(fmt_event(ev, now))
            print()


# ---------- 注册表 ----------

def parse_registry(path):
    projects, cur = [], None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- name:") or line.startswith("- name :"):
            cur = {"name": line.split(":", 1)[1].strip()}
            projects.append(cur)
        elif line.startswith("path:") and cur:
            cur["path"] = line.split(":", 1)[1].strip()
    return [(p["name"], p["path"]) for p in projects if p.get("path")]


def resolve_projects(args):
    if getattr(args, "repo", None):
        r = Path(args.repo).resolve()
        return [(r.name, str(r))]
    for cand in [getattr(args, "registry", None), "./progress.projects.yml",
                 os.path.expanduser("~/.config/ch-progress/projects.yml")]:
        if cand and Path(cand).exists():
            return parse_registry(cand)
    # 单仓库便捷路径：从当前目录向上找 dev/features
    p = Path.cwd().resolve()
    for d in [p, *p.parents]:
        if (d / "dev" / "features").is_dir():
            return [(d.name, str(d))]
    print("定位不到项目：在项目仓库根目录直接运行，或用 --repo 指定仓库根，或用 --registry 指定注册表。\n"
          "想先看效果：progress.py demo DIR 生成演示工作区。", file=sys.stderr)
    sys.exit(2)


# ---------- demo 工作区 ----------

def put(path, content, ago_min=0, now=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if ago_min and now:
        t = now - ago_min * 60
        os.utime(path, (t, t))


def ts_ago(minutes, now):
    return datetime.fromtimestamp(now - minutes * 60).strftime("%Y-%m-%d %H:%M")


def issue_md(id, title, status, extra=""):
    return f"""---
issue-id: {id}
title: {title}
status: {status}
{extra}---
正文略（原型演示数据）。
"""


def demo_command(args):
    import shutil
    root = Path(args.dir).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    now = time.time()

    def sh(cmd, cwd, env=None):
        return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=15)

    def git_env(ago):
        t = datetime.fromtimestamp(now - ago * 60).strftime("%Y-%m-%dT%H:%M:%S")
        return {**os.environ,
                "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@local",
                "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@local",
                "GIT_AUTHOR_DATE": t, "GIT_COMMITTER_DATE": t}

    def put(path, content, ago_min=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if ago_min:
            t = now - ago_min * 60
            os.utime(path, (t, t))

    def write_journal(feat_dir, ev):
        with open(feat_dir / JOURNAL_NAME, "w", encoding="utf-8") as f:
            for ago, e in ev:
                e = {"ts": ts_ago(ago, now), **e}
                if "event_id" not in e:
                    material = json.dumps(e, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    e["event_id"] = "demo-" + hashlib.sha256(material).hexdigest()[:24]
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # 注册表
    (root / "progress.projects.yml").write_text(
        f"projects:\n  - name: pandu\n    path: {root / 'pandu'}\n"
        f"  - name: nova\n    path: {root / 'nova'}\n", encoding="utf-8")

    # ================= pandu（真 git 仓库：分支树 / worktree / ahead-behind 可看） =================
    repo = root / "pandu"
    feat = repo / "dev" / "features" / "auth" / "session-login"
    feat2 = repo / "dev" / "features" / "auth" / "password-reset"

    issues = [
        ("issue-001", "登录表单与校验", "done", ""),
        ("issue-002", "会话签发与刷新", "done", ""),
        ("issue-003", "会话滑动过期与并发登出", "in-progress",
         "verify-fails: 1\ndispatch:\n  host: codex\n  model: gpt-5.2\n"
         "  dispatched-at: '" + ts_ago(95, now) + "'\n  attempts: 2\n"),
        ("issue-004", "登录限流与验证码", "done", ""),
        ("issue-005", "登录失败重试退避", "in-progress",
         "dispatch:\n  host: zcode\n  model: glm-4.7\n"
         "  dispatched-at: '" + ts_ago(125, now) + "'\n  attempts: 1\n"),
        ("issue-006", "登录审计日志", "confirmed", "blocked-by: [issue-003]\n"),
        ("issue-007", "第三方 OAuth 接入", "needs-human", ""),
        ("issue-008", "登录页埋点", "mr-submitted", ""),
    ]
    for iid, title, st, extra in issues:
        put(feat / "issues" / f"{iid}.md", issue_md(iid, title, st, extra))
    for iid, title, st in [("issue-101", "重置链接签发", "done"),
                           ("issue-102", "重置频率限制", "in-review")]:
        put(feat2 / "issues" / f"{iid}.md", issue_md(iid, title, st))

    put(feat / "impl" / "issue-003" / "impl-log-issue-003.md", """# impl-log issue-003（原型演示）

## 做了什么
滑动过期窗口 + 并发登出广播，见红证据已登记。

## 轮次记录
- 第1轮：实现滑动过期；见红 test_session_expiry 首跑失败（断言失败，指向预期行为差异）
- 第2轮：修复复跑失败项 test_checkout——时间源改为注入，退出码全绿
""", 6)
    put(feat / "impl" / "issue-005" / "impl-log-issue-005.md", """# impl-log issue-005（原型演示）

## 轮次记录
- 第1轮：实现基础退避重试
- 第2轮：自修复重试限流（退避 30s 重跑，等待窗口锁）
""", 40)
    put(feat / "reviews" / "issue-007" / "issue-007-review-round-1.md", "# 评审第 1 轮：BLOCKER 2 处\n", 80)
    put(feat / "reviews" / "issue-007" / "issue-007-review-round-2.md", "# 评审第 2 轮：BLOCKER 分歧未决\n", 35)
    put(feat / "reviews" / "issue-003" / "issue-003-review-round-1.md", "# 评审第 1 轮（首次派发后）\n", 100)

    write_journal(feat, [
        (285, {"event": "dispatch", "issue": "issue-001", "host": "codex", "model": "gpt-5.2", "attempts": 1}),
        (270, {"event": "dispatch", "issue": "issue-002", "host": "zcode", "model": "glm-4.7", "attempts": 1}),
        (255, {"event": "dispatch", "issue": "issue-004", "host": "claude", "model": "opus-4.6", "attempts": 1}),
        (240, {"event": "dispatch", "issue": "issue-008", "host": "codex", "model": "gpt-5.2", "attempts": 1}),
        (210, {"event": "merged", "issue": "issue-001", "host": "codex", "model": "gpt-5.2", "commit": "c3d4e5f"}),
        (195, {"event": "merged", "issue": "issue-002", "host": "zcode", "model": "glm-4.7", "commit": "7a8b9c0"}),
        (165, {"event": "merged", "issue": "issue-004", "host": "claude", "model": "opus-4.6", "commit": "1d2e3f4"}),
        (160, {"event": "dispatch", "issue": "issue-003", "host": "codex", "model": "gpt-5.2", "attempts": 1}),
        (145, {"event": "dispatch", "issue": "issue-007", "host": "claude", "model": "opus-4.6", "attempts": 1}),
        (125, {"event": "dispatch", "issue": "issue-005", "host": "zcode", "model": "glm-4.7", "attempts": 1}),
        (100, {"event": "verify-fail", "issue": "issue-003", "host": "codex", "n": 1, "note": "复跑 test_checkout 失败，打回"}),
        (95, {"event": "dispatch", "issue": "issue-003", "host": "codex", "model": "gpt-5.2", "attempts": 2}),
        (45, {"event": "returned", "issue": "issue-008", "note": "评审 pass-with-notes"}),
        (42, {"event": "mr-submitted", "issue": "issue-008", "note": "MR !12"}),
        (35, {"event": "needs-human", "issue": "issue-007", "note": "BLOCKER 分歧：会话过期策略"}),
        (30, {"event": "digest", "note": "无人值守心跳：3/8 合并 · 2 在跑 · 1 等你"}),
    ])
    write_journal(feat2, [
        (180, {"event": "dispatch", "issue": "issue-101", "host": "zcode", "model": "deepseek-v4", "attempts": 1}),
        (120, {"event": "merged", "issue": "issue-101", "host": "zcode", "model": "deepseek-v4", "commit": "9aa001"}),
        (90, {"event": "dispatch", "issue": "issue-102", "host": "kimi", "model": "kimi-v2", "attempts": 1}),
    ])

    put(repo / "dev" / "agents-config.md", "---\nmain-branch: main\nreview-round-alert: 3\n---\n（原型演示配置）\n")
    put(repo / ".gitignore", ".worktrees/\ndev\n")
    put(repo / "README.md", "pandu 原型演示仓库\n")
    if sh(["git", "init", "-q", "-b", "main"], repo).returncode != 0:
        sh(["git", "init", "-q"], repo)
        sh(["git", "symbolic-ref", "HEAD", "refs/heads/main"], repo)
    sh(["git", "add", "-A"], repo, git_env(300))
    sh(["git", "commit", "-q", "-m", "init"], repo, git_env(300))
    # main 前进一次（在制分支按基线冻结不追 main → 分支树显示 ↓1）
    put(repo / "README.md", "pandu 原型演示仓库\n\nchore: main 前进\n")
    sh(["git", "add", "-A"], repo, git_env(20))
    sh(["git", "commit", "-q", "-m", "chore"], repo, git_env(20))

    # needs-human 的演示必须有真实决策包；事件备注只保留历史，不再充当路由真源。
    import human_decision as hd
    decision_issue = feat / "issues" / "issue-007.md"
    decision_report = feat / "reviews" / "issue-007" / "issue-007-review-round-2.md"
    decision_head = sh(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    hd.request(repo, {
        "schema": hd.SCHEMA,
        "id": "dec-demo-oauth-retry",
        "scope": {"repo": ".", "feature": "auth/session-login", "issue": "issue-007"},
        "source": {"host": "claude-code", "skill": "implement", "role": "development",
                   "dispatch_id": "dispatch-demo-007", "session_id": "session-demo-007",
                   "parent_session_id": "session-demo-main"},
        "basis": {
            "issue_sha256": hashlib.sha256(decision_issue.read_bytes()).hexdigest(),
            "head_commit": decision_head,
            "report_sha256": hashlib.sha256(decision_report.read_bytes()).hexdigest(),
        },
        "question": {
            "context": "第三方登录失败后可能重复创建会话，评审对是否保留自动重试存在分歧。",
            "decision": "本次要保留重试并增加防重复创建，还是先关闭自动重试？",
            "why": "这会影响用户登录行为和后续数据核对，不能由实现会话自行决定。",
            "recommendation": "guard",
            "options": [
                {"id": "guard", "label": "增加防重复创建",
                 "description": "保留自动重试，只修改会话创建逻辑并补充重复请求验收。",
                 "route": {"action": "continue", "strategy": "resume", "role": "development",
                           "tier": "default", "next_status": "in-progress",
                           "prompt": "继续实现防重复创建并复验。", "operation": None}},
                {"id": "pause", "label": "先关闭自动重试",
                 "description": "改动较小，但登录失败后需要用户重新操作。",
                 "route": {"action": "continue", "strategy": "redispatch", "role": "development",
                           "tier": "default", "next_status": "in-progress",
                           "prompt": "关闭自动重试并复验登录行为。", "operation": None}},
                {"id": "defer", "label": "暂缓这张票",
                 "description": "保留当前分支和依赖关系，确认方向后再继续。",
                 "route": {"action": "defer", "strategy": "none", "role": None, "tier": None,
                           "next_status": "deferred", "prompt": None, "operation": None}},
            ],
        },
        "evidence": [{"path": decision_report.relative_to(repo).as_posix(),
                      "sha256": hashlib.sha256(decision_report.read_bytes()).hexdigest(),
                      "summary": "第二轮评审对重试策略的分歧。"}],
        "presentation": {"header": "登录重试"},
        "expires_at": None,
    }, main_session=True, unattended=True)

    def make_branch(iid, branch, files, ago, dirty_file=None):
        wt = repo / ".worktrees" / iid
        sh(["git", "worktree", "add", "-q", "-b", branch, str(wt), "main"], repo)
        # checkout 产物的 mtime 是创建时刻，统一回溯到开工前，避免污染活性探针
        t_old = now - (ago + 10) * 60
        for dp, dns, fns in os.walk(wt):
            dns[:] = [d for d in dns if d != ".git"]
            for fn in fns:
                p = Path(dp) / fn
                if not p.is_symlink():
                    os.utime(p, (t_old, t_old))
        for rel, content in files.items():
            put(wt / rel, content, ago)
        sh(["git", "add", "-A"], wt, git_env(ago))
        sh(["git", "commit", "-q", "-m", f"wip {iid}"], wt, git_env(ago))
        if dirty_file:
            put(wt / dirty_file, "（未提交草稿）\n", 2)
        try:
            (wt / "dev").symlink_to("../../dev")
        except OSError:
            pass

    make_branch("issue-003", "feat/session-login-issue-003",
                {"src/auth/session.py": "def sliding_expiry(sess): ...\n",
                 "src/auth/logout.py": "def broadcast_logout(uid): ...\n"}, 4,
                dirty_file="src/auth/NOTES.md")
    make_branch("issue-005", "feat/session-login-issue-005",
                {"src/auth/retry.py": "def backoff_retry(fn, n): ...\n"}, 40)
    make_branch("issue-008", "feat/session-login-issue-008",
                {"src/track/login_page.py": "def track_login(uid): ...\n"}, 42)
    # main 再前进（此后建的分支不落后，之前的在制分支 ↓1）
    put(repo / "README.md", "pandu 原型演示仓库\n\nchore: main 前进\ndocs: 接口现行态表更新\n")
    sh(["git", "add", "-A"], repo, git_env(1))
    sh(["git", "commit", "-q", "-m", "docs"], repo, git_env(1))
    # 已完成票的残留分支（worktree 已清、分支未删）
    sh(["git", "branch", "feat/session-login-issue-004", "main"], repo)
    # 无对应票的孤儿分支
    sh(["git", "branch", "feat/session-login-issue-099", "main"], repo)

    # ================= nova（非 git 仓库：视图优雅降级） =================
    repo2 = root / "nova"
    nfeat = repo2 / "dev" / "features" / "billing" / "export-center"
    for i, (iid, title) in enumerate([
        ("issue-001", "导出任务队列"), ("issue-002", "CSV 分片写出"),
        ("issue-003", "导出进度回执"), ("issue-004", "失败重试与告警"),
        ("issue-005", "导出中心前端页"),
    ]):
        put(nfeat / "issues" / f"{iid}.md", issue_md(iid, title, "done"))
    ev2 = []
    for k, (host, model) in enumerate([("codex", "gpt-5.2"), ("zcode", "glm-4.7"), ("claude", "opus-4.6"),
                                       ("zcode", "deepseek-v4"), ("codex", "gpt-5.2")]):
        ev2.append((1500 - k * 140, {"event": "dispatch", "issue": f"issue-00{k+1}", "host": host, "model": model, "attempts": 1}))
        ev2.append((1430 - k * 140, {"event": "merged", "issue": f"issue-00{k+1}", "host": host, "commit": f"abc00{k+1}"}))
    ev2.append((60, {"event": "digest", "note": "5/5 全部合并，收尾总结已发"}))
    write_journal(nfeat, ev2)

    print(f"演示工作区已生成：{root}\n"
          f"\n单仓库用法（推荐，日常就这一种）：\n"
          f"  cd {root / 'pandu'}\n"
          f"  python3 " + str(Path(__file__).resolve()) + "            # 状态句（需求/票进度）\n"
          f"  python3 " + str(Path(__file__).resolve()) + " tree       # 分支树\n"
          f"  python3 " + str(Path(__file__).resolve()) + " hosts      # 跨 agent 聚合\n"
          f"  python3 " + str(Path(__file__).resolve()) + " replay     # 事件流回放\n"
          f"\n跨项目用法（可选）：\n"
          f"  python3 " + str(Path(__file__).resolve()) + f" status --registry {root / 'progress.projects.yml'}")



# ---------- 树形 payload（DSH 插件 / 富渲染数据源） ----------

def build_tree_payload(repo, stall_min, now=None):
    """按分支嵌套产出面板 payload：main + feature(宿主徽章/进度) + 分支/票树。
    分支父子用 git 祖先链（最深祖先），票吃掉自己的在途分支元数据。"""
    from datetime import datetime
    now = now or time.time()
    KEYS = ["total", "done", "merged", "run", "review", "mr", "human", "ready"]
    CANON = ["in-progress", "in-review", "mr-submitted", "merged", "needs-human"]
    CANON_S = set(CANON)

    main = detect_main_branch(repo)
    if not main:
        return {"repo": Path(repo).resolve().name, "error": "未识别主干分支"}
    branches = collect_branches(repo, main, inspect=False)
    wt_index = worktree_index(repo, branches)
    tix_multi, features = {}, []
    for feat in find_features(repo):
        events = load_events(feat)
        issues = load_issues(repo, feat, events, wt_index)
        for it in issues:
            tix_multi.setdefault(it["id"], []).append(it)
        features.append((feat, events, issues))
    all_slugs = [(artifact_scope.scope_key(feat) if artifact_scope.campaign_id(feat) else feat.name) for feat, _, _ in features]
    bindings = load_session_bindings(repo)

    mb = branches.pop(main, None)
    rows = []
    for name, b in branches.items():
        mt = ISSUE_ID_RE.search(name)
        it = resolve_ticket(tix_multi, mt.group(0).lower(), f"{name} {b.get('worktree') or ''}") if mt else None
        r = {"name": name, "iid": mt.group(0).lower() if mt else None,
             "resolved_key": it["key"] if it else None, "status": it["status"] if it else None,
             "ahead": b["ahead"], "behind": b["behind"], "dirty": b["dirty"], "ts": b["ts"],
             "worktree": bool(b["worktree"]), "worktree_path": b["worktree"],
             "feature": (it["feature"] if it and "/campaigns/" in it["feature"] else it["feature"].split("/")[-1] if it else None) or (next((s for s in all_slugs if (artifact_scope.branch_key(Path("dev/features") / s) if "/campaigns/" in s else s) in name), None))}
        if name.startswith(("backup/", "codex/preserve")):
            r["hint"] = "residue"
            r["protected"] = True
        elif it and it["status"] == "done":
            r["hint"] = "done_residue"
        elif it and it["status"] == "merged":
            r["hint"] = "merged"
        elif not mt and r["worktree"]:
            r["hint"] = "integration"
            r["protected"] = True
        else:
            r["hint"] = "live"
        rows.append(r)

    tk = {}
    for r in rows:
        if r["hint"] != "residue" and r["resolved_key"] and r["status"] in CANON_S:
            tk.setdefault(r["resolved_key"], r)
    consumed = {id(r) for r in tk.values()}
    residue = {"backup": 0, "done": 0, "retired": 0, "other": 0}
    brows = []
    for r in rows:
        if id(r) in consumed:
            continue
        if r["hint"] == "residue":
            residue["backup" if r["name"].startswith("backup/") else "other"] += 1
        elif r["worktree"] or r["hint"] in ("integration", "merged"):
            brows.append(r)
        elif r["hint"] == "done_residue":
            residue["done"] += 1
        else:
            residue["retired"] += 1

    # 在途票、活跃/集成分支才计算完整 git 指标；done 残留只展示，清理时单独预检
    brow_ids = {id(r) for r in brows}
    active_records = [r for r in rows if id(r) in consumed
                      or (id(r) in brow_ids and r["hint"] in ("live", "integration"))]
    inspect_branches(repo, main, active_records)

    def sev_of(s):
        return {"needs-human": 0, "in-progress": 1, "mr-submitted": 2,
                "in-review": 3, "merged": 4}.get(s, 9)

    fnodes = []
    for feat, events, issues in features:
        slug = artifact_scope.scope_key(feat) if artifact_scope.campaign_id(feat) else feat.name
        inflight = [it for it in issues if it["status"] in CANON_S]
        fbrs = [r for r in brows if r["feature"] == slug]
        if not inflight and not fbrs:
            continue
        # 祖先链只在 feature 内的相关行之间算（嵌套不跨 feature），调用数从 O(n²) 全局降到每 feature 数次
        rel = list(dict.fromkeys(
            [r["name"] for r in fbrs]
            + [tk[it["key"]]["name"] for it in inflight if it["key"] in tk and tk[it["key"]]]))
        anc = {n: set() for n in rel}
        # 只需为在途/集成子节点找父；64 个 done 残留之间不做无意义的两两拓扑检查
        active_children = ({r["name"] for r in fbrs if r["hint"] in ("live", "integration", "merged")}
                           | {tk[it["key"]]["name"] for it in inflight
                              if it["key"] in tk and tk[it["key"]]})
        pairs = [(a, b) for b in rel if b in active_children for a in rel if a != b]
        def is_ancestor(pair):
            a, b = pair
            rc = git_run(repo, "merge-base", "--is-ancestor", a, b)
            return b, a, bool(rc and rc.returncode == 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:
            for b, a, yes in pool.map(is_ancestor, pairs):
                if yes:
                    anc[b].add(a)

        def nearest_displayed(n, displayed):
            cands = [c for c in anc.get(n, ()) if c in displayed and c != n]
            if not cands:
                return None
            deep = [c for c in cands if not any(c in anc.get(o, ()) for o in cands if o != c)]
            return sorted(deep, key=len)[0] if deep else sorted(cands, key=len)[0]

        counts = dict.fromkeys(KEYS, 0)
        counts["total"] = len(issues)
        for it in issues:
            k = {"done": "done", "merged": "merged", "in-progress": "run", "in-review": "review",
                 "mr-submitted": "mr", "needs-human": "human", "confirmed": "ready"}.get(it["status"])
            if k:
                counts[k] += 1
        agents = sorted({b["host"] for b in bindings if not b["paused"] and (b["feature"] if artifact_scope.campaign_id(feat) else b["feature"].split("/")[-1]) == slug})
        # 需求态与「开发中」视图同一判定：无在制票即非开发中，呈现时须弱化并标明原因
        retired = sum(1 for it in issues if it.get("closed_as"))
        if inflight:
            fstate = None
        elif retired:
            fstate = "closed"
        elif counts["ready"]:
            fstate = "ready"
        else:
            fstate = "done"

        def ticket_node(it, r):
            p = (it.get("probe") or {})
            return {"kind": "ticket", "id": it["id"], "key": it["key"], "title": it["title"][:44],
                    "feature": it["feature"], "issue_path": it["issue_path"],
                    "status": it["status"], "age_ts": p.get("ts"), "round": (p.get("round") or "")[:56],
                    "verify_fails": it["verify_fails"], "review_rounds": it.get("review_rounds", 0),
                    "human_note": it.get("human_note", ""), "decision": it.get("decision"),
                    "contract_error": it.get("contract_error"),
                    "branch": ({"name": r["name"], "ahead": r["ahead"], "behind": r["behind"],
                                "dirty": r["dirty"], "ts": r["ts"]} if r else None), "children": []}

        nodes_by_name = {}
        displayed = {r["name"] for r in fbrs}
        for r in sorted(fbrs, key=lambda r: -r["ts"]):
            nodes_by_name[r["name"]] = {"kind": "branch", "name": r["name"], "iid": r["iid"],
                                        "resolved_key": r.get("resolved_key"), "protected": bool(r.get("protected")),
                                        "feature": slug, "status": r["status"],
                                        "hint": {"integration": "集成", "merged": "已合入保留"}.get(r["hint"], "残留可删"),
                                        "ahead": r["ahead"], "behind": r["behind"], "dirty": r["dirty"],
                                        "ts": r["ts"], "children": []}
        top = []
        for r in fbrs:
            node = nodes_by_name[r["name"]]
            pn = nearest_displayed(r["name"], displayed)
            if pn and pn != r["name"]:
                nodes_by_name[pn]["children"].append(node)
            else:
                top.append(node)
        for it in sorted(inflight, key=lambda x: (sev_of(x["status"]), x["id"])):
            r = tk.get(it["key"])
            node = ticket_node(it, r)
            pn = nearest_displayed(r["name"], displayed) if r else None
            if pn:
                nodes_by_name[pn]["children"].append(node)
            else:
                top.append(node)
        fnodes.append({"kind": "feature", "slug": slug, "agents": agents, "counts": counts,
                       "closed": not bool(inflight), "fstate": fstate,
                       "sev": min(sev_of(it["status"]) for it in inflight) if inflight else 9,
                       "children": top})

    fnodes.sort(key=lambda f: (f["sev"], f["slug"]))
    for r in sorted(brows, key=lambda r: -r["ts"]):
        if r["feature"] is None:
            fnodes.append({"kind": "branch", "name": r["name"], "iid": r["iid"], "status": r["status"],
                           "resolved_key": r.get("resolved_key"), "protected": bool(r.get("protected")),
                           "hint": {"integration": "集成", "merged": "已合入保留"}.get(r["hint"], ""),
                           "ahead": r["ahead"], "behind": r["behind"], "dirty": r["dirty"], "ts": r["ts"],
                           "children": []})

    def flatten(nodes, last_flags):
        out = []
        for i, n in enumerate(nodes):
            last = i == len(nodes) - 1
            kids = n.get("children") or []
            n2 = {k: v for k, v in n.items() if k != "children"}
            n2["prefix"] = "".join("│  " if not lf else "   " for lf in last_flags) + ("└─ " if last else "├─ ")
            out.append(n2)
            if kids:
                out += flatten(kids, last_flags + (last,))
        return out

    return {"repo": Path(repo).resolve().name, "stall_min": stall_min,
            "sessions": bindings,
            "generated": now, "hygiene": sum(1 for _, _, issues in features
                                             for it in issues if it["status"] not in CANON_S and it["status"] != "done"),
            "main": {"name": main, "sha": mb["sha"], "ts": mb["ts"]} if mb else None,
            "tree": flatten(fnodes, ()), "residue": residue}


def payload_command(args):
    repo = pick_repo(args)
    import json as _json
    print(_json.dumps(build_tree_payload(repo, args.stall_min), ensure_ascii=False))


# ---------- 开发中泳道 payload（横向泳道视图数据源） ----------

def _norm_feature_key(value):
    return re.sub(r"[\s/]+", "", str(value or "")).lower()


def _yaml_values(value):
    if not value:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(v).strip().strip("'\"") for v in value if str(v).strip().strip("'\"")]


def _split_external_ref(ref):
    """external-blocked-by 值形态：`模块/slug#issue-N`、`slug/issue-N`、非 issue 里程碑。
    返回 (feature 标签, 归一化 issue id)；非 issue 引用返回 (原值, None)。"""
    ref = str(ref).strip().strip("'\"")
    if "#" in ref:
        label, _, iid = ref.partition("#")
    elif "/" in ref and ISSUE_ID_RE.fullmatch(ref.rpartition("/")[2] or ""):
        label, _, iid = ref.rpartition("/")
    else:
        return ref, None
    iid = iid.strip()
    if iid and ISSUE_ID_RE.fullmatch(iid):
        return label.strip(), iid.lower()
    return ref, None


def _event_ts(ev):
    if not ev:
        return None
    ts = ev.get("_ts") or parse_ts(ev.get("ts"))
    if not ts and ev.get("ts"):
        try:
            ts = datetime.fromisoformat(str(ev["ts"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            ts = None
    return ts


def _done_ts(events, issue_id, text):
    """完结时间：accepted（feature 验收）优先；历史 done 票回退 merged 事件；
    再回退票 status 行内注释日期（`done  # 2026-10-12 合并…`）。
    三者都没有返回 None——由 UI 按「未知即过期」处理。"""
    ev = last_event(events, issue_id, "accepted")
    if ev:
        ts = _event_ts(ev)
        if ts:
            return ts
    ev = last_event(events, issue_id, "merged")
    if ev:
        ts = _event_ts(ev)
        if ts:
            return ts
    m = re.search(r"^status:\s*done[^\n]*?(\d{4}-\d{2}-\d{2})", text or "", re.M)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").timestamp()
        except ValueError:
            return None
    return None


def build_swim_payload(repo, now=None, sessions=None):
    """开发中泳道 payload：需求×issue 横向依赖图。
    只做 1 次分支名枚举（不做祖先链 / ahead-dirty），依赖全部显式：
    internal=blocked-by（同需求内解析）、external=external-blocked-by、
    body=正文「外部前置/外部依赖」（带 文件:行 证据）、milestone=非 issue 引用。
    目标缺失的引用保留 resolved=false，不把裸票号猜到别的需求上。"""
    now = now or time.time()
    repo = Path(repo)
    if sessions is None:
        sessions = load_session_bindings(repo)
    meta, tix_multi = [], {}
    for feat in find_features(repo):
        feature_id = artifact_scope.scope_key(feat)
        events = load_events(feat)
        rows = load_issues(repo, feat, events, {})  # 空 wt_index：泳道不做 worktree 探针，零额外 git 开销
        meta.append((feature_id, feat, events, rows))
        for it in rows:
            tix_multi.setdefault(it["id"], []).append(it)

    # 分支名 → 票：名称级弱线索（宿主前缀 + 票号作用域消歧），失败退回票文件 branch 字段
    branch_hint = {}
    try:
        main = detect_main_branch(repo)
        if main:
            r = git_run(repo, "for-each-ref", "refs/heads", "--format=%(refname:short)")
            if r is not None and r.returncode == 0:
                for name in (ln.strip() for ln in r.stdout.splitlines()):
                    m = ISSUE_ID_RE.search(name)
                    if not m:
                        continue
                    it = resolve_ticket(tix_multi, m.group(0).lower(), name)
                    if it:
                        branch_hint.setdefault(it["key"], name)
    except Exception:
        branch_hint = {}

    def agent_rows(it, events):
        out = []
        fm_dispatch = it["fm"].get("dispatch")
        if not isinstance(fm_dispatch, dict):
            fm_dispatch = {}
        host = str(fm_dispatch.get("host") or "").strip()
        model = str(fm_dispatch.get("model") or "").strip()
        if not host:
            ev = last_event(events, it["id"], "dispatch")
            if ev:
                host = str(ev.get("host") or "").strip()
                model = str(ev.get("model") or "").strip()
        if host:
            out.append({"host": host, "source": "dispatch", "issue": it["id"], "model": model})
        for b in sessions:
            if not b.get("paused") and b.get("feature") == it["feature"] and b.get("issue") == it["id"]:
                out.append({"host": b["host"], "source": "session", "issue": it["id"], "title": b.get("title") or ""})
        branch = branch_hint.get(it["key"]) or str(it["fm"].get("branch") or "").strip() or None
        if branch:
            prefix = branch.split("/", 1)[0].lower()
            if prefix in BRANCH_HOSTS:
                out.append({"host": prefix, "source": "branch", "issue": it["id"], "branch": branch})
        return out, branch

    features_out, issues_by_uid, it_by_uid = [], {}, {}
    for feature_id, feat, events, rows in meta:
        counts = dict.fromkeys(("total", "done", "merged", "run", "review", "mr",
                                "human", "ready", "other"), 0)
        counts["total"] = len(rows)
        fissues = []
        for it in rows:
            k = {"done": "done", "merged": "merged", "in-progress": "run", "in-review": "review",
                 "mr-submitted": "mr", "needs-human": "human", "confirmed": "ready"}.get(it["status"], "other")
            counts[k] += 1
            agents, branch = agent_rows(it, events)
            try:
                rel_path = str(Path(it["issue_path"]).relative_to(repo))
            except ValueError:
                rel_path = it["issue_path"]
            uid = f"{feature_id}#{it['id']}"
            node = {"uid": uid, "id": it["id"], "key": it["key"], "feature": feature_id,
                    "title": it["title"], "status": it["status"], "issue_path": rel_path,
                    "branch": branch, "agents": agents,
                    "verify_fails": it.get("verify_fails", 0),
                    "human_note": it.get("human_note", ""),
                    "decision": it.get("decision"), "contract_error": it.get("contract_error"),
                    "review_rounds": it.get("review_rounds", 0)}
            if it["status"] == "merged":
                node["merged_ts"] = _event_ts(last_event(events, it["id"], "merged"))
                node["merged_target"] = it.get("merged_target") or ""
                node["merged_commit"] = it.get("merged_commit") or ""
            if it["status"] == "done":
                try:
                    txt = Path(it["issue_path"]).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    txt = ""
                node["done_ts"] = _done_ts(events, it["id"], txt)
            fissues.append(node)
            issues_by_uid[uid] = node
            it_by_uid[uid] = it
        feature_agents, seen = [], set()
        for node in fissues:
            if node["status"] in CANON_STATUSES:
                for a in node["agents"]:
                    kk = (a["host"], a["source"], a.get("issue"))
                    if kk not in seen:
                        seen.add(kk)
                        feature_agents.append(a)
        for b in sessions:
            if not b.get("paused") and b.get("feature") == feature_id:
                kk = (b["host"], "session", b.get("issue"))
                if kk not in seen:
                    seen.add(kk)
                    feature_agents.append({"host": b["host"], "source": "session",
                                           "issue": b.get("issue"), "title": b.get("title") or ""})
        # 需求态三分类（与分支树同一口径）：closed=有已关闭/退役票；ready=仅剩可派发；done=全部完结
        retired = sum(1 for it in rows if it.get("closed_as"))
        active = any(n["status"] in CANON_STATUSES for n in fissues)
        if active:
            fstate = None
        elif retired:
            fstate = "closed"
        elif counts["ready"]:
            fstate = "ready"
        else:
            fstate = "done"
        features_out.append({"id": feature_id, "module": artifact_scope.feature_root(feat).parent.name, "slug": artifact_scope.feature_root(feat).name, "campaign": artifact_scope.campaign_id(feat),
                             "active": active, "fstate": fstate,
                             "counts": counts, "agents": feature_agents, "issues": fissues})

    by_key, by_slug = {}, {}
    for f in features_out:
        by_key.setdefault(_norm_feature_key(f["id"]), []).append(f["id"])
        by_slug.setdefault(_norm_feature_key(f["slug"]), []).append(f["id"])

    def match_feature(label):
        key = _norm_feature_key(label)
        if not key:
            return None
        for table in (by_key, by_slug):
            hits = table.get(key)
            if hits and len(hits) == 1:
                return hits[0]
        return None

    edges = []

    def add_edge(src, target_uid, target_feature, kind, origin, ref, evidence=""):
        edges.append({"from": src["uid"], "from_feature": src["feature"], "to": target_uid,
                      "to_feature": target_feature, "kind": kind, "origin": origin,
                      "ref": str(ref), "resolved": bool(target_uid), "evidence": evidence})

    for uid, it in it_by_uid.items():
        node = issues_by_uid[uid]
        for ref in (it["blocked_by"] or []):
            ref = str(ref).strip().strip("'\"")
            if not ref:
                continue
            target = issues_by_uid.get(f"{node['feature']}#{ref.lower()}")
            add_edge(node, target["uid"] if target else None, node["feature"], "internal", "blocked-by", ref)
        for ref in _yaml_values(it["fm"].get("external-blocked-by")):
            label, iid = _split_external_ref(ref)
            if iid is None:
                add_edge(node, None, None, "milestone", "external-blocked-by", ref)
                continue
            tf = match_feature(label)
            target = issues_by_uid.get(f"{tf}#{iid}") if tf else None
            add_edge(node, target["uid"] if target else None, tf, "external", "external-blocked-by", ref)
        try:
            text = Path(it["issue_path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if len(text) <= 200_000:
            for lineno, line in enumerate(text.splitlines(), 1):
                m = EXTERNAL_BODY_RE.search(line)
                if not m:
                    continue
                tf = match_feature(m.group(1))
                target = issues_by_uid.get(f"{tf}#{m.group(2).lower()}") if tf else None
                add_edge(node, target["uid"] if target else None, tf, "body", "正文外部前置",
                         f"{tf or m.group(1).strip()}#{m.group(2).lower()}",
                         f"{node['issue_path']}:{lineno} {line.strip()[:200]}")

    cross = [e for e in edges if e["kind"] in ("external", "body")]
    stats = {
        "features": len(features_out),
        "issues": len(issues_by_uid),
        "developing_features": sum(1 for f in features_out if f["active"]),
        "active_issues": sum(1 for n in issues_by_uid.values() if n["status"] in CANON_STATUSES),
        "internal_edges": sum(1 for e in edges if e["kind"] == "internal"),
        "missing_internal": sum(1 for e in edges if e["kind"] == "internal" and not e["resolved"]),
        "cross_issue_refs": len(cross),
        "resolved_cross": sum(1 for e in cross if e["resolved"]),
        "unresolved_cross": sum(1 for e in cross if not e["resolved"]),
        "milestone_refs": sum(1 for e in edges if e["kind"] == "milestone"),
    }
    return {"repo": repo.name, "generated": now,
            "generated_label": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S %z"),
            "stats": stats, "features": features_out, "dependencies": edges}


def swim_command(args):
    repo = pick_repo(args)
    import json as _json
    print(_json.dumps(build_swim_payload(repo), ensure_ascii=False))


# ---------- 开发摘要（宿主模型生成项目管理总结） ----------

_SUMMARY_PROMPT = (
    "你是多宿主 AI 开发调度的项目管理助手。下面是「开发中」视图的真实数据事实（可能被截断）。"
    "只基于这些事实，输出一份中文项目管理摘要，包含四段："
    "【整体进展】一两句；【各需求】每个开发中需求一行，写阶段与进展；"
    "【卡点与风险】等人/复跑打回/长期无分支的待合并票/跨需求依赖缺失等，无则写「无」；"
    "【建议关注】最多三条。总计不超过 300 字，直接给结论，不写开场白和免责声明。\n\n=== 事实 ===\n"
)


def _summary_facts(payload):
    """把泳道 payload 压成模型可读的事实清单（截断保护 6000 字）。"""
    st = payload["stats"]
    queued = sum((f["counts"].get("ready") or 0) for f in payload["features"] if f["active"])
    lines = [f"仓库：{payload['repo']}；快照：{payload['generated_label']}；"
             f"开发中需求 {st['developing_features']} 个；在制票 {st['active_issues']} 张；"
             f"待派发 {queued} 张；跨需求依赖可解析 {st['resolved_cross']}、待核对 {st['unresolved_cross']}；"
             f"里程碑引用 {st['milestone_refs']} 条"]
    for f in payload["features"]:
        if not f["active"]:
            continue
        c = f["counts"]
        progress = f"{c.get('done', 0)}/{c.get('total', 0)} 已验收"
        if c.get("merged"):
            progress += f"，{c['merged']} 已合入待验收"
        lines.append(f"需求 {f['slug']}（{f['module']}）：{progress}")
        for i in f["issues"]:
            if i["status"] not in CANON_STATUSES and i["status"] != "confirmed":
                continue
            bits = [STATUS_LABEL.get(i["status"], i["status"])]
            if i.get("branch"):
                bits.append("分支 " + str(i["branch"]))
            hosts = "、".join(sorted({a["host"] + "·" + a["source"] for a in i.get("agents", [])})) or "未绑定"
            bits.append(hosts)
            if i.get("verify_fails"):
                bits.append(f"复跑打回 {i['verify_fails']} 次")
            if i.get("review_rounds"):
                bits.append(f"评审第 {i['review_rounds']} 轮")
            if i.get("contract_error"):
                bits.append("决定包契约错误：" + str(i["contract_error"])[:100])
            elif i.get("human_note"):
                bits.append("等人决定：" + str(i["human_note"])[:80])
            lines.append(f"  - {i['id']} {str(i['title'])[:40]}：{'；'.join(bits)}")
    unresolved = [e["ref"] for e in payload["dependencies"]
                  if e["kind"] in ("external", "body") and not e["resolved"]]
    if unresolved:
        lines.append("待核对跨需求引用：" + "、".join(str(x) for x in unresolved[:8]))
    return "\n".join(lines)[:6000]


def _read_env_file(path):
    """KEY=VALUE 行解析（pandu 本地部署 local.env 形态）；文件缺失或坏行忽略。"""
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def _summary_config(args, home=None):
    """解析摘要模型配置：参数 > CH_PROGRESS_SUMMARY_* 环境变量 > 宿主配置 host.json /model
    （api_key 按 api_key_ref_env 先查进程环境，再回退同目录 local.env）。"""
    home = Path(home) if home else Path.home()
    host = {}
    try:
        host = json.loads((home / ".config" / "pandu" / "host.json").read_text(encoding="utf-8")).get("model") or {}
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    base = (getattr(args, "base_url", None) or os.environ.get("CH_PROGRESS_SUMMARY_BASE_URL")
            or host.get("base_url"))
    model = (getattr(args, "model", None) or os.environ.get("CH_PROGRESS_SUMMARY_MODEL")
             or host.get("model"))
    ref = host.get("api_key_ref_env")
    key = (getattr(args, "api_key", None) or os.environ.get("CH_PROGRESS_SUMMARY_API_KEY")
           or (os.environ.get(ref) if ref else None))
    if not key and ref:
        key = _read_env_file(home / ".config" / "pandu" / "local.env").get(ref)
    return base, model, key


def _call_llm(base_url, api_key, model, prompt, timeout=90):
    # deepseek-flash 等推理模型先产出 reasoning_content 再给正文：预算不足会 length 截断、正文为空。
    # 返回 (正文, finish_reason)，截断状态交给调用方生成可诊断的报错。
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "temperature": 0.3, "max_tokens": 8000,
                         "messages": [{"role": "user", "content": prompt}]}).encode("utf-8"),
        headers={"content-type": "application/json", "authorization": "Bearer " + api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    return (msg.get("content") or "").strip(), choice.get("finish_reason") or ""


def summary_command(args):
    repo = pick_repo(args)
    out = {"repo": repo.name, "generated": time.time()}
    base, model, key = _summary_config(args)
    if not (base and model and key):
        out.update(ok=False, error="未配置总结模型：需要 base_url/model/api_key"
                   "（命令行参数、CH_PROGRESS_SUMMARY_* 环境变量，或宿主配置 host.json /model）")
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)
    try:
        text, finish = _call_llm(base, key, model, _SUMMARY_PROMPT + _summary_facts(build_swim_payload(repo)))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, KeyError, ValueError) as exc:
        out.update(ok=False, error=f"模型调用失败：{exc}")
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)
    if not text:
        reason = "（finish_reason: length，推理未在预算内完成）" if finish == "length" else f"（finish_reason: {finish or '未知'}）"
        out.update(ok=False, error="模型返回正文为空" + reason + "；请重试，或在插件配置把 summary.model 换成非推理模型")
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)
    out.update(ok=True, summary=text, model=model)
    print(json.dumps(out, ensure_ascii=False))


# ---------- 授权清场与崩溃恢复（永不合并代码） ----------

def find_ticket(repo, issue, key=None):
    """Resolve exactly one main-worktree ticket and attach its feature directory."""
    matches = []
    for feat in find_features(repo):
        for item in load_issues(repo, feat, load_events(feat), {}):
            if item["id"] != issue and item["key"] != issue and str(Path(item["issue_path"]).resolve()) != str((Path(repo) / issue).resolve()):
                continue
            if key and item["key"] != key:
                continue
            value = dict(item)
            value["feature_dir"] = str(feat)
            matches.append(value)
    if len(matches) == 1:
        return matches[0]
    return None


def cleanup_plan(repo, branch=None, issue=None, feature_dir=None, intent="close",
                 operation_id=None, authorize=True, round=None):
    """Build/preview a v2 plan. ``branch`` remains only an internal resolver input."""
    import progress_cleanup as pc
    repo = Path(repo).resolve()
    if feature_dir is not None:
        return pc.make_plan(repo, feature_dir=feature_dir, intent=intent,
                            operation_id=operation_id, authorize=authorize, round=round)
    ticket = find_ticket(repo, issue) if issue else None
    if issue and not ticket:
        return {"ok": False, "eligible": False, "error": f"票号不存在或不唯一：{issue}",
                "reason": f"票号不存在或不唯一：{issue}"}
    if not ticket and branch:
        candidates = []
        for feat in find_features(repo):
            for item in load_issues(repo, feat, load_events(feat), {}):
                if pc._branch_matches(dict(item, feature_dir=str(feat)), branch):
                    value = dict(item)
                    value["feature_dir"] = str(feat)
                    candidates.append(value)
        if len(candidates) == 1:
            ticket = candidates[0]
    if not ticket:
        return {"ok": False, "eligible": False, "error": "需要可唯一归属的 issue 或 feature-dir",
                "reason": "需要可唯一归属的 issue 或 feature-dir"}
    plan = pc.make_plan(repo, ticket=ticket, intent=intent,
                        operation_id=operation_id, authorize=False)
    # release 只删 worktree、branch 保留，在途子票不受影响，无需后代延展检查。
    if plan.get("eligible") and plan.get("branch") and intent != "release-worktree":
        main = detect_main_branch(repo)
        branches = collect_branches(repo, main, inspect=False) if main else {}
        tix_multi = {}
        for feat in find_features(repo):
            for item in load_issues(repo, feat, load_events(feat), {}):
                tix_multi.setdefault(item["id"], []).append(item)
        active_children = []
        for other in branches:
            if other == plan["branch"]:
                continue
            match = ISSUE_ID_RE.search(other)
            if not match:
                continue
            child = resolve_ticket(tix_multi, match.group(0).lower(), other)
            if not child or child["status"] not in {"in-progress", "in-review", "mr-submitted",
                                                    "merged", "needs-human", "confirmed"}:
                continue
            result = git_run(repo, "merge-base", "--is-ancestor", plan["branch"], other)
            if result and result.returncode == 0:
                active_children.append(f"{child['feature']}·{child['id']}({child['status']})")
        if active_children:
            plan.update(eligible=False, ok=False, confirm=None, operation_id=None,
                        reason="该分支仍被在途/待派发票延展：" + "、".join(active_children[:4]),
                        active_children=active_children)
    if plan.get("eligible") and authorize:
        pc.authorize_plan(plan, operation_id)
    return plan


def _cleanup_error(exc):
    return {"ok": False, "state": "protected" if getattr(exc, "protected", False) else "needs-attention",
            "error": str(exc), "code": getattr(exc, "code", "cleanup-failed"),
            "stale": getattr(exc, "code", None) == "stale",
            "protected": bool(getattr(exc, "protected", False))}


def close_clean_command(args):
    import progress_cleanup as pc
    try:
        if args.apply and (not args.operation_id or not args.confirm):
            raise pc.CleanupError("--apply 必须同时提供 --operation-id 与 --confirm", code="invalid-confirmation")
        if not args.apply:
            plan = cleanup_plan(pick_repo(args), issue=args.issue, feature_dir=args.feature_dir,
                                intent=args.intent, operation_id=args.operation_id,
                                round=getattr(args, "round", None))
            print(json.dumps(pc.public_plan(plan), ensure_ascii=False))
            if not plan.get("eligible"):
                sys.exit(1)
            return
        result = pc.apply_operation(pick_repo(args), args.operation_id, args.confirm)
        print(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            sys.exit(1)
    except (pc.CleanupError, OSError, ValueError) as exc:
        print(json.dumps(_cleanup_error(exc), ensure_ascii=False))
        sys.exit(1)


def _read_delivery_operations(repo, operation_id=None):
    """Delegate ticket/rebase/feature discovery to delivery's read-only API."""
    import delivery
    return delivery.discover_operations(repo, operation_id)


def _read_human_decisions(repo, operation_id=None):
    import human_decision
    packets = human_decision.list(repo, active=True)
    if operation_id:
        packets = [packet for packet in packets if packet.get("id") == operation_id]
    contract_errors = []
    for feature in find_features(repo):
        for item in load_issues(repo, feature, load_events(feature), {}):
            if item.get("contract_error"):
                contract_errors.append({"issue": item["id"], "feature": item["feature"],
                                        "error": item["contract_error"]})
    unknown_routes = [{"decision_id": packet.get("id"), "route_id": (packet.get("route") or {}).get("route_id")}
                      for packet in packets if packet.get("status") == "routing"]
    return {"kind": "human_decision", "ok": not contract_errors and not unknown_routes,
            "applied": False, "active": packets, "unknown_routes": unknown_routes,
            "state": "needs-attention" if unknown_routes or contract_errors else "observed",
            "contract_errors": contract_errors}


def aggregate_reconcile(repo, operation_id=None):
    """Read-only aggregate; every provider failure is surfaced in its own category."""
    import progress_cleanup as pc
    import integration_runtime
    categories = {}
    readers = {
        "cleanup": lambda: pc.reconcile_operations(repo, operation_id, apply=False),
        "delivery": lambda: _read_delivery_operations(repo, operation_id),
        "human_decision": lambda: _read_human_decisions(repo, operation_id),
        "runtime": lambda: integration_runtime.reconcile(repo, apply=False),
    }
    for kind, reader in readers.items():
        try:
            result = reader()
            if not isinstance(result, dict):
                raise ValueError("provider returned a non-object")
            if kind == "cleanup" and not result.get("operations") and not result.get("error"):
                result["ok"] = True  # no cleanup journals is a valid empty observation
            categories[kind] = result
        except Exception as exc:
            categories[kind] = {"kind": kind, "ok": False, "applied": False,
                                "state": "needs-attention", "error": str(exc)}
    errors = [{"kind": kind, "error": value["error"]}
              for kind, value in categories.items() if value.get("error")]
    for kind, value in categories.items():
        for route in value.get("unknown_routes") or []:
            errors.append({"kind": kind, "error": "宿主路由结果未知，需要原宿主对账", **route})
    needs_attention = bool(errors) or any(value.get("ok") is not True for value in categories.values())
    return {"ok": not needs_attention,
            "applied": False, "categories": categories, "errors": errors,
            "state": "needs-attention" if needs_attention else "observed"}


def apply_reconcile(repo, args):
    """Apply only an explicitly selected provider category."""
    kind = args.kind
    if not kind:
        raise ValueError("reconcile --apply 必须显式指定 --kind cleanup|delivery|human_decision|runtime")
    try:
        if kind == "cleanup":
            if not args.operation_id:
                raise ValueError("cleanup 恢复需要 operation-id")
            import progress_cleanup as pc
            return pc.reconcile_operations(repo, args.operation_id, apply=True)
        if kind == "delivery":
            candidate_path = getattr(args, "candidate_path", None)
            context = getattr(args, "context", None)
            if not candidate_path and not context:
                raise ValueError("delivery 恢复需要 --context ticket-context.json 或 --candidate-path candidate.json --candidate-repo WORKTREE")
            if candidate_path and not getattr(args, "candidate_repo", None):
                raise ValueError("feature delivery 恢复需要 --candidate-repo")
            import delivery
            result = delivery.reconcile_operations(
                repo, getattr(args, "operation_id", None), apply=True,
                context=context, candidate_path=candidate_path,
                candidate_repo=getattr(args, "candidate_repo", None),
                main_repo=repo, strategy=getattr(args, "strategy", None))
        elif kind == "human_decision":
            import human_decision
            result = human_decision.reconcile(repo, args.feature, main_session=True)
        elif kind == "runtime":
            import integration_runtime
            result = integration_runtime.reconcile(repo, args.operation_id, args.candidate, apply=True)
        else:
            raise ValueError("未知 reconcile 类别：" + kind)
        if not isinstance(result, dict):
            raise ValueError("provider returned a non-object")
        result.setdefault("kind", kind)
        result.setdefault("applied", True)
        result.setdefault("ok", result.get("state") == "completed")
        return result
    except Exception as exc:
        return {"kind": kind, "ok": False, "applied": True,
                "state": "needs-attention", "error": str(exc)}


def reconcile_command(args):
    import progress_cleanup as pc
    try:
        if args.apply and not args.kind:
            raise pc.CleanupError("reconcile --apply 必须显式指定 --kind", code="invalid-operation")
        result = (apply_reconcile(pick_repo(args), args) if args.apply
                  else aggregate_reconcile(pick_repo(args), args.operation_id))
        print(json.dumps(result, ensure_ascii=False))
        if args.apply and not result.get("ok"):
            sys.exit(1)
    except (pc.CleanupError, OSError, ValueError) as exc:
        print(json.dumps(_cleanup_error(exc), ensure_ascii=False))
        sys.exit(1)


# ---------- 入口 ----------

def main():
    ap = argparse.ArgumentParser(
        prog="progress",
        description="单仓库开发调度台（原型）：需求进度 + 分支树 + 跨 agent 聚合 + 事件流回放。"
                    "在项目仓库根目录裸跑即出状态句。")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("status", help="状态句 + 在制票活性行（需求/票进度）")
    p1.add_argument("--registry", help="项目注册表 yml（可选，默认单仓库）")
    p1.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p1.add_argument("--project", help="只看某个注册项目")
    p1.add_argument("--stall-min", type=int, default=STALL_DEFAULT_MIN, help="无动静判卡阈值（分钟）")
    p1.set_defaults(func=status_command)

    p2 = sub.add_parser("tree", help="分支树：main + feat 分支 × 票状态 × 宿主 × 活性")
    p2.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p2.add_argument("--stall-min", type=int, default=STALL_DEFAULT_MIN, help="无动静判卡阈值（分钟）")
    p2.set_defaults(func=tree_command)

    p3 = sub.add_parser("hosts", help="跨 agent 聚合：按宿主·模型汇总在制 / 产出 / 异常")
    p3.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p3.add_argument("--hours", type=float, default=24, help="产出统计窗口（小时，默认 24）")
    p3.add_argument("--stall-min", type=int, default=STALL_DEFAULT_MIN, help="无动静判卡阈值（分钟）")
    p3.set_defaults(func=hosts_command)

    p4 = sub.add_parser("replay", help="事件流时间线回放")
    p4.add_argument("--registry", help="项目注册表 yml（可选）")
    p4.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p4.add_argument("--project", help="只看某个注册项目")
    p4.add_argument("--host", help="只看某宿主")
    p4.add_argument("--issue", help="只看某票")
    p4.add_argument("--hours", type=float, default=12, help="回放最近 N 小时（默认 12）")
    p4.set_defaults(func=replay_command)

    p6 = sub.add_parser("payload", help="树形 payload JSON（DSH 插件 / 富渲染数据源）")
    p6.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p6.add_argument("--stall-min", type=int, default=STALL_DEFAULT_MIN, help="无动静判卡阈值（分钟）")
    p6.set_defaults(func=payload_command)

    p9 = sub.add_parser("swim", help="开发中泳道 payload JSON：需求×issue 横向依赖图")
    p9.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p9.set_defaults(func=swim_command)

    p10 = sub.add_parser("summary", help="宿主模型生成开发摘要（项目管理视角）")
    p10.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p10.add_argument("--base-url", help="OpenAI 兼容端点（缺省读环境变量/宿主配置）")
    p10.add_argument("--model", help="模型名（缺省读环境变量/宿主配置）")
    p10.add_argument("--api-key", help="API Key（缺省读环境变量；优先用环境变量传密钥）")
    p10.set_defaults(func=summary_command)

    p7 = sub.add_parser("close-and-clean", help="授权收尾/丢弃清理与 merged 票释放 worktree：默认 preview；apply 需 operation-id + confirm")
    p7.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    target = p7.add_mutually_exclusive_group(required=True)
    target.add_argument("--issue", help="票文件 stem 或票号")
    target.add_argument("--feature-dir", help="已 feature-merged 的主工作区需求目录")
    p7.add_argument("--round", help="--feature-dir 为多轮复用目录时指定本轮 round 名（与 freeze --round 一致）")
    p7.add_argument("--intent", choices=["close", "discard", "release-worktree"], default="close")
    p7.add_argument("--apply", action="store_true", help="执行；缺省只 preview 并授权 journal")
    p7.add_argument("--operation-id", help="preview 返回的授权操作 ID")
    p7.add_argument("--confirm", help="preview 返回的完整状态确认指纹")
    p7.set_defaults(func=close_clean_command)

    p5 = sub.add_parser("demo", help="生成演示工作区")
    p5.add_argument("dir")
    p5.set_defaults(func=demo_command)

    p8 = sub.add_parser("reconcile", help="默认只读聚合 cleanup/delivery/human_decision/runtime；--apply 需显式 --kind")
    p8.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p8.add_argument("--operation-id", help="cleanup operation / runtime service 或 lease ID")
    p8.add_argument("--kind", choices=["cleanup", "delivery", "human_decision", "runtime"],
                    help="恢复类别；只在 --apply 时使用")
    p8.add_argument("--context", help="delivery 恢复所需 ticket-context.json")
    p8.add_argument("--feature", help="human_decision 只恢复指定 module/slug")
    p8.add_argument("--candidate", help="runtime 恢复所需的完整 candidate OID")
    p8.add_argument("--candidate-path", help="delivery feature 的 candidate.json 路径")
    p8.add_argument("--candidate-repo", help="delivery feature 的集成 worktree")
    p8.add_argument("--strategy", choices=["no-ff", "squash"], default="no-ff",
                    help="delivery feature 的原授权合并策略")
    p8.add_argument("--apply", action="store_true", help="恢复已授权 journal，不生成新授权")
    p8.set_defaults(func=reconcile_command)

    args = ap.parse_args()
    if not args.cmd:
        args.cmd = "status"
        args.registry = None
        args.repo = None
        args.project = None
        args.stall_min = STALL_DEFAULT_MIN
        args.func = status_command
    args.func(args)


if __name__ == "__main__":
    main()
