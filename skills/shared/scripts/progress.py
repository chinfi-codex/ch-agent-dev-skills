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
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

JOURNAL_NAME = "progress.jsonl"
STALL_DEFAULT_MIN = 30
GIT_WORKERS = 8
# 活性探针不扫 dev/（实体副本可达数 GB）；主工作区 impl 目录单独探测。
SKIP_DIRS = {
    ".git", ".logs", ".local", "dev", "node_modules", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "dist", "build", ".next", ".turbo", "coverage",
}

STATUS_ORDER = ["needs-human", "in-progress", "in-review", "mr-submitted", "confirmed", "proposed"]
STATUS_LABEL = {
    "proposed": "待确认",
    "confirmed": "可派发",
    "in-progress": "在跑",
    "in-review": "评审中",
    "mr-submitted": "待合并",
    "needs-human": "等人",
    "done": "已合并",
}
# 真实仓库的状态词变体 → 规范词（pandu 503 票实测）
CLOSED_ALIASES = {"done-review", "done-human", "superseded", "cancelled", "deferred"}
STATUS_ALIAS = {"implementing": "in-progress", "review-pending": "in-review", "analysis": "confirmed"}
ISSUE_ID_RE = re.compile(r"issue-\d+[a-z0-9]*", re.I)


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
                data[key] = {}
                parent = key
            else:
                data[key] = coerce(val)
                parent = None
        elif parent and isinstance(data.get(parent), dict):
            data[parent][key] = coerce(val)
    return data


def parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()
    except (ValueError, TypeError):
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
    out = []
    for module_dir in sorted(root.iterdir()):
        if not module_dir.is_dir():
            continue
        for slug_dir in sorted(module_dir.iterdir()):
            if (slug_dir / "issues").is_dir():
                out.append(slug_dir)
    return out


# ---------- 事件流折叠 ----------

def last_event(events, issue, *names):
    for ev in reversed(events):
        if ev.get("issue") == issue and ev.get("event") in names:
            return ev
    return None


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
    done_ids = set()
    raw = []
    for f in sorted((feature_dir / "issues").glob("*.md")):
        fm = parse_frontmatter(f)
        m = ISSUE_ID_RE.search(f.stem)
        iid = m.group(0).lower() if m else f.stem
        st = fm.get("status", "未知")
        st = "done" if st in CLOSED_ALIASES else STATUS_ALIAS.get(st, st)
        title = fm.get("title") or (f.stem[m.end():].lstrip("-_") if m else f.stem)
        it = {
            "id": iid,
            "key": f.stem,
            "title": title,
            "status": st,
            "feature": f"{feature_dir.parent.name}/{feature_dir.name}",
            "blocked_by": fm.get("blocked-by") or [],
            "verify_fails": fm.get("verify-fails", 0) or 0,
            "fm": fm,
        }
        if it["status"] == "done":
            done_ids.add(it["id"])
        raw.append(it)
    for it in raw:
        if it["status"] in ("in-progress", "in-review", "mr-submitted", "needs-human"):
            ev = last_event(events, it["id"], "dispatch")
            it["dispatch"] = dispatch_desc(ev)
            it["dispatch_n"] = dispatch_count(events, it["id"])
        if it["status"] == "in-progress":
            ents = (wt_index or {}).get(it["id"], ())
            # 票号撞号消歧：优先本 feature（模块/slug）名出现在分支名或路径里的 worktree
            pref = [e for e in ents
                    if feature_dir.parent.name in e["hint"] or feature_dir.name in e["hint"]]
            it["probe"] = probe_issue(repo, feature_dir, it["id"], pref or ents)
        if it["status"] == "needs-human":
            ev = last_event(events, it["id"], "needs-human")
            if ev:
                it["human_ts"] = ev.get("ts")
                it["human_note"] = ev.get("note", "")
        if it["status"] == "mr-submitted":
            ev = last_event(events, it["id"], "mr-submitted")
            it["mr"] = ev.get("note", "") if ev else ""
            it["mr_ts"] = ev.get("ts") if ev else None
        if it["status"] == "confirmed":
            it["ready"] = all(b in done_ids for b in it["blocked_by"])
        reviews = feature_dir / "reviews"
        if reviews.is_dir():
            it["review_rounds"] = len(list(reviews.glob(f"{it['id']}*/*.md")))
        issues.append(it)
    return issues


# ---------- 渲染：status ----------

def issue_line(it, now, stall_min):
    st = it["status"]
    mark = {"needs-human": "●等人  ", "in-progress": "●在跑  ", "in-review": "●评审  ",
            "mr-submitted": "●待合并", "confirmed": "●可派发", "proposed": "●待确认"}.get(st, "●?     ")
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
    project_chunks, fleet = [], {"total": 0, "done": 0, "run": 0, "review": 0, "mr": 0, "human": 0, "ready": 0}
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
        merged = fleet["done"] + fleet["run"] + fleet["review"] + fleet["mr"] + fleet["human"] + fleet["ready"]
        other = fleet["total"] - merged
        bits = [f"{fleet['total']} 票"]
        if fleet["done"]:
            bits.append(f"{fleet['done']} 已合并")
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
    c = {"total": 0, "done": 0, "run": 0, "review": 0, "mr": 0, "human": 0, "ready": 0, "other": 0}
    wt_index = worktree_index(repo)
    for feat in feats:
        events = load_events(feat)
        issues = load_issues(repo, feat, events, wt_index)
        cf = dict.fromkeys(c, 0)
        for it in issues:
            cf["total"] += 1
            c["total"] += 1
            s = it["status"]
            key = {"done": "done", "in-progress": "run", "in-review": "review",
                   "mr-submitted": "mr", "needs-human": "human", "confirmed": "ready"}.get(s, "other")
            cf[key] += 1
            c[key] += 1
        slug = f"{feat.parent.name}/{feat.name}"
        lines = []
        for it in sorted(issues, key=lambda x: (STATUS_ORDER.index(x["status"]) if x["status"] in STATUS_ORDER else 99, x["id"])):
            if it["status"] == "done":
                continue
            if it["status"] == "confirmed" and not it["ready"]:
                continue  # 大仓取舍：未就绪的待派发票只进计数，不占行
            lines.append(issue_line(it, now, stall_min)[0])
        if cf["total"] and cf["total"] == cf["done"]:
            out.append(f"{slug} —— {cf['total']} 票全部合并，无遗留。整体正常，无需干预。")
            continue
        parts = []
        if cf["done"]:
            parts.append(f"{cf['done']} 已合并")
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
            r = str(v.get("repo", ""))
            if r != repo and not r.startswith(repo + "/"):
                continue
            issue_path = str(v.get("issue") or "")
            mi = ISSUE_ID_RE.search(Path(issue_path).stem) if issue_path else None
            feat = str(v.get("feature") or "")
            feat_short = feat.split("features/", 1)[-1] if "features/" in feat else feat
            out.append({
                "host": host,
                "role": v.get("role") or "",
                "paused": bool(v.get("paused")),
                "feature": feat_short,
                "issue": mi.group(0).lower() if mi else None,
                "title": v.get("last_title", ""),
            })
    return out


def resolve_ticket(tix_multi, iid, hint):
    """票号跨 feature 撞号（pandu 实测）：先按 feature 名出现在 hint 里匹配，再用在跑优先。"""
    cands = tix_multi.get(iid) or []
    if len(cands) <= 1:
        return cands[0] if cands else None
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
    elif name == "merged":
        if ev.get("commit"):
            detail.append(ev["commit"])
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
            slug = f"{feat.parent.name}/{feat.name}"
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
verify-tier: light
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
    KEYS = ["total", "done", "run", "review", "mr", "human", "ready"]
    CANON = ["in-progress", "in-review", "mr-submitted", "needs-human"]
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
    all_slugs = [feat.name for feat, _, _ in features]
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
             "feature": (it["feature"].split("/")[-1] if it else None) or (next((s for s in all_slugs if s in name), None))}
        if name.startswith(("backup/", "codex/preserve")):
            r["hint"] = "residue"
        elif it and it["status"] == "done":
            r["hint"] = "done_residue"
        elif not mt and r["worktree"]:
            r["hint"] = "integration"
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
        elif r["worktree"] or r["hint"] == "integration":
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
        return {"needs-human": 0, "in-progress": 1, "mr-submitted": 2, "in-review": 3}.get(s, 9)

    fnodes = []
    for feat, events, issues in features:
        slug = feat.name
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
        active_children = ({r["name"] for r in fbrs if r["hint"] in ("live", "integration")}
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
            k = {"done": "done", "in-progress": "run", "in-review": "review",
                 "mr-submitted": "mr", "needs-human": "human", "confirmed": "ready"}.get(it["status"])
            if k:
                counts[k] += 1
        agents = sorted({b["host"] for b in bindings if not b["paused"] and b["feature"].split("/")[-1] == slug})

        def ticket_node(it, r):
            p = (it.get("probe") or {})
            return {"kind": "ticket", "id": it["id"], "key": it["key"], "title": it["title"][:44],
                    "status": it["status"], "age_ts": p.get("ts"), "round": (p.get("round") or "")[:56],
                    "verify_fails": it["verify_fails"], "review_rounds": it.get("review_rounds", 0),
                    "human_note": it.get("human_note", ""),
                    "branch": ({"name": r["name"], "ahead": r["ahead"], "behind": r["behind"],
                                "dirty": r["dirty"], "ts": r["ts"]} if r else None), "children": []}

        nodes_by_name = {}
        displayed = {r["name"] for r in fbrs}
        for r in sorted(fbrs, key=lambda r: -r["ts"]):
            nodes_by_name[r["name"]] = {"kind": "branch", "name": r["name"], "iid": r["iid"],
                                        "status": r["status"],
                                        "hint": "集成" if r["hint"] == "integration" else "残留可删",
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
                       "sev": min(sev_of(it["status"]) for it in inflight) if inflight else 9,
                       "children": top})

    fnodes.sort(key=lambda f: (f["sev"], f["slug"]))
    for r in sorted(brows, key=lambda r: -r["ts"]):
        if r["feature"] is None:
            fnodes.append({"kind": "branch", "name": r["name"], "iid": r["iid"], "status": r["status"],
                           "hint": "集成" if r["hint"] == "integration" else "",
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
            "generated": now, "hygiene": sum(1 for _, _, issues in features
                                             for it in issues if it["status"] not in CANON_S and it["status"] != "done"),
            "main": {"name": main, "sha": mb["sha"], "ts": mb["ts"]} if mb else None,
            "tree": flatten(fnodes, ()), "residue": residue}


def payload_command(args):
    repo = pick_repo(args)
    import json as _json
    print(_json.dumps(build_tree_payload(repo, args.stall_min), ensure_ascii=False))


# ---------- 收尾清场（预检 → 确认 → 重验 → 执行） ----------

def cleanup_merge_targets(repo, ticket, main):
    """授权合并目标：主干 + 特性集成分支 + agents-config 明确定义的 query 工作分支。"""
    targets = [main]
    cfg = Path(repo) / "dev" / "agents-config.md"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8")
        section = text.split("## 特性集成分支", 1)[-1].split("\n## ", 1)[0]
        for line in section.splitlines():
            codes = re.findall(r"`([^`]+)`", line)
            if len(codes) >= 2 and codes[0] == ticket["feature"]:
                targets.append(codes[1])
        if re.search(r"^## query 分支", text, re.M):
            targets.append("query")
    return list(dict.fromkeys(targets))


def cleanup_plan(repo, branch):
    """破坏性操作使用更严格的票归属：feature 名无法消歧时不给执行资格。"""
    import progress_cleanup as pc
    repo = Path(repo).resolve()
    main = detect_main_branch(repo)
    if not main:
        return {"ok": False, "eligible": False, "error": "未识别主干分支"}
    branches = collect_branches(repo, main, inspect=False)
    wt_index = worktree_index(repo, branches)
    tix_multi = {}
    for feat in find_features(repo):
        for it in load_issues(repo, feat, load_events(feat), wt_index):
            it = dict(it)
            it["feature_dir"] = str(feat)
            tix_multi.setdefault(it["id"], []).append(it)
    m = ISSUE_ID_RE.search(branch)
    b = branches.get(branch)
    hint = f"{branch} {(b or {}).get('worktree') or ''}"
    cands = tix_multi.get(m.group(0).lower(), []) if m else []
    matched = [c for c in cands if any(part in hint for part in c["feature"].split("/"))]
    if len(matched) == 1:
        ticket = matched[0]
    elif not matched and len(cands) == 1:
        ticket = cands[0]
    else:
        ticket = None
    current = git_run(repo, "symbolic-ref", "--short", "HEAD")
    current_branch = current.stdout.strip() if current and current.returncode == 0 else None
    plan = pc.make_plan(
        repo, branch, ticket, (b or {}).get("worktree"),
        cleanup_merge_targets(repo, ticket, main) if ticket else [main], current_branch,
    )
    if plan.get("eligible"):
        # done 不等于可删：在途/待派发票可能仍把该分支当作工作基线（基线冻结）
        active_children = []
        for other, ob in branches.items():
            if other == branch:
                continue
            om = ISSUE_ID_RE.search(other)
            if not om:
                continue
            ot = resolve_ticket(tix_multi, om.group(0).lower(), f"{other} {ob.get('worktree') or ''}")
            if not ot or ot["status"] not in {"in-progress", "in-review", "mr-submitted", "needs-human", "confirmed"}:
                continue
            rc = git_run(repo, "merge-base", "--is-ancestor", branch, other)
            if rc and rc.returncode == 0:
                active_children.append(f"{ot['feature']}·{ot['id']}({ot['status']})")
        if active_children:
            active_children = list(dict.fromkeys(active_children))
            plan.update({
                "eligible": False, "confirm": None,
                "reason": "该分支仍被在途/待派发票延展，待这些票收口后再清理：" + "、".join(active_children[:4]),
                "active_children": active_children,
            })
    return plan


def cleanup_command(args):
    import progress_cleanup as pc
    try:
        plan = cleanup_plan(pick_repo(args), args.branch)
        if not args.apply:
            print(json.dumps(pc.public_plan(plan), ensure_ascii=False))
            return
        if not plan.get("eligible"):
            print(json.dumps({"ok": False, "error": plan.get("reason") or "该节点不可清理"}, ensure_ascii=False))
            sys.exit(1)
        if not args.confirm or args.confirm != plan.get("confirm"):
            print(json.dumps({"ok": False, "error": "预检后状态已变化或确认凭据不符，请重新预检"}, ensure_ascii=False))
            sys.exit(1)
        result = pc.execute_plan(plan)
        if result["worktree_removed"] or result["branch_deleted"] or result["dev_files_removed"]:
            feat_dir = Path(plan["_feature_dir"])
            note = (f"插件清场 {args.branch}：worktree 移除={result['worktree_removed']}；"
                    f"本地分支删除={result['branch_deleted']}；dev 过程文件 {result['dev_files_removed']} 个；"
                    f"salvage={result['salvage_dir'] or '无需'}")
            with open(feat_dir / JOURNAL_NAME, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "event": "digest", "issue": plan["issue"], "host": "dsh", "note": note,
                }, ensure_ascii=False) + "\n")
        result["confirm"] = plan["confirm"]
        print(json.dumps(result, ensure_ascii=False))
        if not result["ok"]:
            sys.exit(1)
    except pc.CleanupError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
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

    p7 = sub.add_parser("cleanup", help="已完成票收尾清场：默认预检，--apply 需携带预检 confirm")
    p7.add_argument("--repo", help="仓库根目录（默认从当前目录向上找）")
    p7.add_argument("--branch", required=True, help="要清理的本地分支")
    p7.add_argument("--apply", action="store_true", help="执行清理；缺省只输出预检")
    p7.add_argument("--confirm", help="预检返回的确认指纹；执行前会重新计算并比对")
    p7.set_defaults(func=cleanup_command)

    p5 = sub.add_parser("demo", help="生成演示工作区")
    p5.add_argument("dir")
    p5.set_defaults(func=demo_command)

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
