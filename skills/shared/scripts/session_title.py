#!/usr/bin/env python3
"""按 docs/dev 产物为 Claude Code、Codex、ZCode 会话生成并更新标题。

业务状态只读现有产物；宿主本地状态只保存 session 到 feature/issue 的路由和
上次自动标题。Hook 失败不影响主流程。仅依赖 Python 3.8+ 标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from process_check import parse_yaml_subset
except ImportError:  # 允许作为包外的独立脚本由测试加载
    parse_yaml_subset = None

HOSTS = ("claude", "codex", "zcode", "deepseek")
STATE_VERSION = 1
ISSUE_TITLES = {
    "proposed": "待确认",
    "confirmed": "待执行",
    "in-progress": "实现中",
    "in-review": "评审中",
    "mr-submitted": "待合并",
    "needs-human": "需人工",
    "done": "已合并",
}
ISSUE_LEGACY_STAGES = {
    "implementing": "实现中",
    "review-pending": "评审中",
    "analysis": "待执行",
    "待技术共识确认": "需人工",
}
CLOSED_STATUSES = {"superseded", "cancelled", "deferred"}
NATIVE_TITLE_EVENTS = {"SessionStart", "UserPromptSubmit"}


class SessionTitleError(Exception):
    pass


@contextmanager
def adapter_reentry_guard():
    key = "SESSION_TITLE_ADAPTER_ACTIVE"
    previous = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def field(event, snake, camel=None):
    return event.get(snake, event.get(camel or snake))


def atomic_json_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def host_root(host):
    override = os.environ.get("SESSION_TITLE_HOST_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    names = {"claude": ".claude", "codex": ".codex", "zcode": ".zcode", "deepseek": ".dsh"}
    return Path.home() / names[host]


def state_path(host):
    override = os.environ.get("SESSION_TITLE_STATE")
    if override:
        return Path(override).expanduser().resolve()
    if host == "zcode":
        return host_root(host) / "cli" / "session-title.json"
    return host_root(host) / "session-title.json"


def load_state(host):
    data = read_json(state_path(host), {})
    if not isinstance(data, dict):
        raise SessionTitleError("会话命名状态文件不是 JSON 对象")
    sessions = data.setdefault("sessions", {})
    if not isinstance(sessions, dict):
        raise SessionTitleError("会话命名状态文件的 sessions 不是对象")
    data["version"] = STATE_VERSION
    return data


def save_state(host, state):
    atomic_json_write(state_path(host), state)


def clean_relative(path):
    return Path(path).as_posix().lstrip("./")


def binding_key(host, session_id):
    return host + ":" + session_id


def normalize_binding(repo, target=None, feature=None, issue=None, role=None):
    repo = Path(repo).expanduser().resolve()
    if target:
        target_path = Path(target).expanduser()
        if not target_path.is_absolute():
            target_path = repo / target_path
        target_path = target_path.resolve()
        target_path.relative_to(repo)
        parts = target_path.relative_to(repo).parts
        if len(parts) >= 4 and parts[:2] == ("docs", "features"):
            feature = clean_relative(target_path.relative_to(repo))
        elif len(parts) >= 5 and parts[:2] == ("dev", "features"):
            if "issues" in parts:
                issue = clean_relative(target_path.relative_to(repo))
            else:
                feature = clean_relative(Path("docs/features") / Path(*parts[2:4]))
        else:
            raise SessionTitleError("绑定目标必须位于 docs/features 或 dev/features 下")
    if issue:
        issue_path = (repo / issue).resolve()
        issue_path.relative_to(repo)
        parts = issue_path.relative_to(repo).parts
        if len(parts) < 6 or parts[:2] != ("dev", "features") or parts[-2] != "issues":
            raise SessionTitleError("issue 绑定必须是 dev/features/<module>/<slug>/issues/<票>.md")
        feature = clean_relative(Path("docs/features") / Path(*parts[2:4]))
        issue = clean_relative(issue_path.relative_to(repo))
    if not feature:
        raise SessionTitleError("缺少 feature 或 issue 绑定")
    feature_path = (repo / feature).resolve()
    feature_path.relative_to(repo)
    parts = feature_path.relative_to(repo).parts
    if len(parts) != 4 or parts[:2] != ("docs", "features"):
        raise SessionTitleError("feature 绑定必须是 docs/features/<module>/<slug>")
    result = {
        "repo": str(repo),
        "feature": clean_relative(feature_path.relative_to(repo)),
    }
    if issue:
        result["issue"] = issue
    if role:
        if role not in ("supervisor", "review", "implementation", "requirement"):
            raise SessionTitleError("未知会话角色")
        result["role"] = role
    return result


def set_binding(host, session_id, binding):
    state = load_state(host)
    key = binding_key(host, session_id)
    old = state["sessions"].get(key, {})
    preserved = {k: old[k] for k in ("last_title", "paused", "title_source") if k in old}
    preserved.update(binding)
    state["sessions"][key] = preserved
    save_state(host, state)
    return preserved


def parse_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\s*---\s*\n(.*?)\n---(?:\s*\n|$)", text, re.S)
    if not match:
        return {}
    return parse_yaml_subset(match.group(1))


def dated_files(directory, pattern):
    if not directory.is_dir():
        return []
    files = [p for p in directory.glob(pattern) if p.is_file()]
    def key(path):
        match = re.search(r"(\d{4}-\d{2}-\d{2})(?=\.md$)", path.name)
        return (match.group(1) if match else "", path.name)
    return sorted(files, key=key)


def latest(directory, pattern):
    files = dated_files(directory, pattern)
    return files[-1] if files else None


def safe_frontmatter(path):
    if not path:
        return {}
    try:
        data = parse_frontmatter(path)
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def feature_paths(binding):
    repo = Path(binding["repo"])
    docs = repo / binding["feature"]
    relative = Path(binding["feature"]).relative_to(Path("docs/features"))
    dev = repo / "dev" / "features" / relative
    return repo, docs, dev


def issue_records(dev):
    directory = dev / "issues"
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("issue-*.md")):
        data = safe_frontmatter(path)
        issue_id = str(data.get("issue-id") or data.get("id") or path.name.split("-")[0] + "-" + path.name.split("-")[1])
        records.append({"path": path, "data": data, "id": issue_id})
    return records


def issue_label(record):
    status = str(record["data"].get("status") or "")
    if status in ISSUE_TITLES:
        return ISSUE_TITLES[status]
    if status in ISSUE_LEGACY_STAGES:
        return ISSUE_LEGACY_STAGES[status]
    if status in CLOSED_STATUSES:
        return "已合并"
    return "状态未知"


def truncate_title(value, limit=20):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def fallback_issue_title(path):
    name = re.sub(r"\.md$", "", path.name)
    name = re.sub(r"^issue-\d+-?", "", name)
    return name.replace("-", " ") or path.stem


def render_issue_title(binding, record):
    feature_slug = Path(binding["feature"]).name
    issue_id = str(record["id"])
    title = truncate_title(record["data"].get("title") or fallback_issue_title(record["path"]))
    label = issue_label(record)
    suffix = " · 评审" if binding.get("role") == "review" else ""
    return "[{}] {} · {} {}{}".format(label, feature_slug, issue_id, title, suffix).rstrip()


def render_supervisor_title(binding, records):
    slug = Path(binding["feature"]).name
    total = len(records)
    done = sum(1 for r in records if str(r["data"].get("status") or "") in ({"done"} | CLOSED_STATUSES))
    blocked = sum(1 for r in records if str(r["data"].get("status") or "") == "needs-human")
    if total and done == total and not blocked:
        label = "已合并 {}/{}".format(done, total)
    else:
        label = "开发 {}/{}".format(done, total)
        if blocked:
            label += "·{}票需处理".format(blocked)
    return "[{}] {}".format(label, slug)


def render_requirement_title(binding):
    _, docs, dev = feature_paths(binding)
    slug = docs.name
    records = issue_records(dev)
    if records:
        return render_supervisor_title(binding, records)
    tech = latest(dev, "*-tech-spec-*.md")
    if tech:
        status = safe_frontmatter(tech).get("status")
        label = "待拆票" if status == "已确认（待拆解）" else "技术方案"
        if status == "草稿":
            label += "·待确认"
        return "[{}] {}".format(label, slug)
    prd = latest(docs, "*-prd-*.md")
    change = latest(docs, "*-change-request-*.md")
    if prd or change:
        if prd:
            data = safe_frontmatter(prd)
            review = latest(docs, "*-pd-review-report-*.md")
            verdict = safe_frontmatter(review).get("verdict") if review else None
            if data.get("status") == "定稿" and verdict != "阻塞":
                return "[技术方案·待出方案] {}".format(slug)
            label = "PRD/评审"
            if verdict == "阻塞":
                label += "·阻塞"
            return "[{}] {}".format(label, slug)
        return "[技术方案·待出方案] {}".format(slug)
    brief = latest(docs, "*-feature-brief-*.md")
    if brief:
        status = safe_frontmatter(brief).get("status")
        label = "需求澄清"
        if status == "待验证":
            label += "·待验证"
        elif status == "不进入":
            label += "·不进入"
        return "[{}] {}".format(label, slug)
    raise SessionTitleError("绑定需求尚无可识别产物")


def compute_title(binding):
    if binding.get("issue"):
        path = Path(binding["repo"]) / binding["issue"]
        if not path.is_file():
            raise SessionTitleError("绑定票文件不存在")
        data = safe_frontmatter(path)
        issue_id = str(data.get("issue-id") or data.get("id") or path.name.split("-")[0] + "-" + path.name.split("-")[1])
        return render_issue_title(binding, {"path": path, "data": data, "id": issue_id})
    _, _, dev = feature_paths(binding)
    if binding.get("role") == "supervisor":
        return render_supervisor_title(binding, issue_records(dev))
    return render_requirement_title(binding)


def resolve_path_text(repo, value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip().strip("\"'`")
    value = value.replace("\\", "/")
    value = re.sub(r"^(?:\./)+", "", value)
    absolute = Path(value).expanduser()
    if not absolute.is_absolute():
        absolute = repo / absolute
    try:
        return absolute.resolve().relative_to(repo.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def binding_from_relative(repo, relative, role=None):
    if not relative:
        return None
    match = re.search(r"(?:^|/)dev/features/([^/]+)/([^/]+)/issues/(issue-\d+[^/]*\.md)$", relative)
    if match:
        return normalize_binding(repo, issue="dev/features/{}/{}/issues/{}".format(*match.groups()), role=role or "implementation")
    match = re.search(r"(?:^|/)docs/features/([^/]+)/([^/]+)/[^/]+\.md$", relative)
    if match:
        return normalize_binding(repo, feature="docs/features/{}/{}".format(*match.groups()), role=role)
    match = re.search(r"(?:^|/)dev/features/([^/]+)/([^/]+)/[^/]+\.md$", relative)
    if match:
        return normalize_binding(repo, feature="docs/features/{}/{}".format(*match.groups()), role=role)
    return None


def candidate_paths_from_event(event, repo):
    values = []
    tool_input = field(event, "tool_input", "toolInput") or {}
    if isinstance(tool_input, dict):
        values.extend(tool_input.get(k) for k in ("file_path", "filePath", "path"))
    values.append(event.get("prompt") or "")
    results = []
    for value in values:
        if not isinstance(value, str):
            continue
        tokens = re.findall(r"(?:[A-Za-z]:)?[^\s\"'`<>|]*?(?:docs|dev)/features/[^\s\"'`<>|]+?\.md", value.replace("\\", "/"))
        if not tokens and value.strip().endswith(".md"):
            tokens = [value.strip()]
        for token in tokens:
            relative = resolve_path_text(repo, token)
            if relative:
                results.append(relative)
    return results


def infer_binding(event, repo):
    prompt = str(event.get("prompt") or "")
    role = "review" if re.search(r"(?:^|\s)/ai-review(?:\s|$)", prompt) else None
    supervisor = bool(re.search(r"监督|supervisor", prompt, re.I))
    candidates = []
    for relative in candidate_paths_from_event(event, repo):
        binding = binding_from_relative(repo, relative, "supervisor" if supervisor else role)
        if binding:
            candidates.append(binding)
    issue_id = None
    match = re.search(r"(?:^|\s)/(?:implement|ai-review)\s+(issue-\d+)(?:\s|$)", prompt)
    if match:
        issue_id = match.group(1)
    if issue_id:
        for path in (repo / "dev" / "features").glob("*/*/issues/{}-*.md".format(issue_id)) if (repo / "dev" / "features").is_dir() else []:
            try:
                candidates.append(normalize_binding(repo, target=str(path), role=role or "implementation"))
            except SessionTitleError:
                pass
    unique = {}
    for candidate in candidates:
        key = (candidate["feature"], candidate.get("issue"), candidate.get("role"))
        unique[key] = candidate
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def command_from_env(name):
    value = os.environ.get(name)
    if not value:
        return None
    try:
        import shlex
        return shlex.split(value)
    except ValueError:
        return None


def default_command(host):
    override = command_from_env("SESSION_TITLE_{}_COMMAND".format(host.upper()))
    if override:
        return override
    if host == "claude":
        return None
    if host == "codex":
        import shutil
        executable = shutil.which("codex")
        return [executable, "app-server", "--listen", "stdio://"] if executable else None
    if host == "zcode":
        candidates = []
        import shutil
        executable = shutil.which("zcode")
        if executable:
            candidates.append([executable, "app-server"])
        if sys.platform == "darwin":
            candidates.append(["node", "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs", "app-server"])
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append([str(Path(local) / "Programs" / "ZCode" / "resources" / "glm" / "zcode.exe"), "app-server"])
        return next((c for c in candidates if Path(c[0]).exists() or shutil.which(c[0])), None)
    return None


def write_child_response(process, message):
    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.flush()


class NDJSONClient:
    def __init__(self, command, cwd, timeout=8):
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
        self.process = None
        self.next_id = 1
        self.initialized = False

    def __enter__(self):
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.process:
            return
        try:
            if self.process.stdin:
                self.process.stdin.close()
            if self.process.stdout:
                self.process.stdout.close()
        except OSError:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()

    def call(self, method, params):
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise SessionTitleError("宿主协议进程未启动")
        request_id = "title-{}".format(self.next_id)
        self.next_id += 1
        self.process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                raise SessionTitleError("宿主协议进程提前退出")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                if message.get("error"):
                    raise SessionTitleError("宿主协议错误: {}".format(message["error"].get("message", "unknown")))
                if method == "initialize":
                    write_child_response(self.process, {"method": "initialized", "params": {}})
                    self.initialized = True
                return message.get("result") or {}
            if message.get("method") and message.get("id") is not None:
                write_child_response(self.process, {"id": message["id"], "error": {"code": -32601, "message": "Unsupported by session-title helper"}})
        raise SessionTitleError("宿主协议调用超时")


def codex_rename(event, title, last_title, unchanged=False):
    command = default_command("codex")
    if not command:
        return None, "unavailable"
    with adapter_reentry_guard(), NDJSONClient(command, field(event, "cwd") or os.getcwd()) as client:
        client.call("initialize", {"clientInfo": {"name": "session-title", "title": "Session Title", "version": "1.0.0"}})
        session_id = field(event, "session_id", "sessionId")
        try:
            current = client.call("thread/read", {"threadId": session_id, "includeTurns": False})
        except SessionTitleError:
            # 某些活跃 TUI 线程不能被另一个 app-server 直接 read；官方允许对
            # 已加载或持久化线程 set name。首轮保守尝试，后续无法核对标题则跳过。
            if last_title:
                return None, "skipped"
            current = {}
        thread = current.get("thread") or current
        current_title = thread.get("name")
        if last_title:
            if current_title != last_title:
                return None, "paused"
        elif current_title:
            return None, "paused"
        if unchanged:
            return title, "unchanged"
        client.call("thread/name/set", {"threadId": session_id, "name": title})
        return title, "written"


def zcode_rename(event, title, last_title, unchanged=False):
    command = default_command("zcode")
    if not command:
        return None, "unavailable"
    command = list(command)
    if "--cwd" not in command:
        command += ["--cwd", field(event, "cwd") or os.getcwd()]
    session_id = field(event, "session_id", "sessionId")
    if str(session_id).startswith("sess_subagent_"):
        return None, "skipped"
    with adapter_reentry_guard(), NDJSONClient(command, field(event, "cwd") or os.getcwd()) as client:
        current = client.call("session/list", {"sessionIds": [session_id]})
        sessions = current.get("sessions") or []
        if len(sessions) != 1:
            return None, "skipped"
        session = sessions[0]
        if session.get("sessionKind") not in (None, "interactive", "fork", "workflow_parent"):
            return None, "skipped"
        current_title = session.get("title") or ""
        source = session.get("titleSource")
        if last_title:
            if current_title != last_title:
                return None, "paused"
        elif source == "custom" and current_title:
            return None, "paused"
        if unchanged:
            return title, "unchanged"
        client.call("session/resume", {"sessionId": session_id})
        result = client.call("v4/command", {
            "commandId": str(uuid.uuid4()),
            "clientId": "session-title",
            "sessionId": session_id,
            "type": "renameSession",
            "payload": {"title": title},
            "issuedAt": int(time.time() * 1000),
        })
        status = result.get("status") or (result.get("ack") or {}).get("status")
        if status and status not in ("accepted", "duplicate", "noop"):
            raise SessionTitleError("ZCode 拒绝改名: {}".format(status))
        return title, "written"


def deepseek_rename(event, title, last_title, unchanged=False, override_manual=False):
    """只决定待写标题；原生 Harness 插件 rename 后用 ack 确认。"""
    current_title = event.get("session_title")
    source = event.get("title_source")
    if not override_manual:
        if last_title and current_title is not None and current_title != last_title:
            return None, "paused"
        if not last_title and source == "user" and current_title:
            return None, "paused"
    if unchanged:
        return title, "unchanged"
    return title, "pending"


def claude_rename(event, title, last_title, unchanged=False, override_manual=False):
    current_title = event.get("session_title")
    if not override_manual:
        if current_title is not None and last_title:
            if current_title != last_title:
                return None, "paused"
        elif current_title:
            return None, "paused"
    if unchanged:
        return title, "unchanged"
    event_name = field(event, "hook_event_name", "hookEventName")
    if event_name in NATIVE_TITLE_EVENTS:
        if event_name == "SessionStart" and event.get("source") not in ("startup", "resume", "fork"):
            return None, "skipped"
        return title, "native"
    return title, "skipped"


def rename(host, event, title, last_title, unchanged=False, override_manual=False):
    if host == "claude":
        return claude_rename(event, title, last_title, unchanged, override_manual)
    if host == "codex":
        return codex_rename(event, title, last_title, unchanged)
    if host == "zcode":
        return zcode_rename(event, title, last_title, unchanged)
    if host == "deepseek":
        return deepseek_rename(event, title, last_title, unchanged, override_manual)
    raise SessionTitleError("不支持的宿主")


def refresh(host, event, force=False):
    if os.environ.get("SESSION_TITLE_ADAPTER_ACTIVE") == "1":
        return {"status": "skipped", "reason": "adapter-reentry"}
    if host not in HOSTS:
        raise SessionTitleError("host 必须是 claude、codex 或 zcode")
    session_id = field(event, "session_id", "sessionId")
    cwd = field(event, "cwd") or os.getcwd()
    if not session_id:
        return {"status": "skipped", "reason": "missing-session-id"}
    if field(event, "agent_id", "agentId") or event.get("origin") == "subagent":
        return {"status": "skipped", "reason": "subagent"}
    state = load_state(host)
    key = binding_key(host, str(session_id))
    binding = state["sessions"].get(key)
    if not binding:
        try:
            binding = infer_binding(event, Path(cwd).resolve())
        except (OSError, SessionTitleError):
            binding = None
        if binding:
            binding = set_binding(host, str(session_id), binding)
            state = load_state(host)
            binding = state["sessions"][key]
        else:
            return {"status": "skipped", "reason": "unbound"}
    if binding.get("paused") and not force:
        return {"status": "paused"}
    # worktree 内仍读写主工作区 dev/docs 产物，不能仅凭 cwd 拒绝已绑定会话。
    live_cwd = Path(cwd).resolve()
    bound_repo = Path(binding["repo"]).resolve()
    if live_cwd != bound_repo:
        try:
            live_cwd.relative_to(bound_repo)
        except ValueError:
            inferred = infer_binding(event, live_cwd)
            if not inferred:
                return {"status": "skipped", "reason": "repo-mismatch"}
            binding = set_binding(host, str(session_id), inferred)
            state = load_state(host)
            binding = state["sessions"][key]
    try:
        title = compute_title(binding)
        # 即使业务标题未变化，也必须先检查用户是否已手工改名。
        written_title, outcome = rename(host, event, title, binding.get("last_title"), binding.get("last_title") == title and not force, force)
    except (OSError, ValueError, subprocess.SubprocessError, SessionTitleError) as exc:
        return {"status": "error", "reason": str(exc)}
    if outcome == "paused":
        binding["paused"] = True
        save_state(host, state)
        return {"status": "paused", "reason": "manual-title"}
    if outcome in ("native", "written"):
        binding["last_title"] = written_title
        binding.pop("paused", None)
        save_state(host, state)
    return {"status": outcome, "title": title}


def acknowledge(host, session_id, accepted_title):
    accepted_title = str(accepted_title or "").strip()
    if not accepted_title:
        raise SessionTitleError("accepted title 不能为空")
    state = load_state(host)
    key = binding_key(host, session_id)
    binding = state["sessions"].get(key)
    if not binding:
        raise SessionTitleError("找不到待确认的 session 绑定")
    binding["last_title"] = accepted_title
    binding.pop("paused", None)
    save_state(host, state)
    return {"status": "written", "title": accepted_title}


def desktop_title_context(title):
    params = json.dumps({"session_id": "self", "title": title}, ensure_ascii=False)
    return (
        "<session-title-sync>\n"
        "如果当前宿主提供 mcp__ccd_session_mgmt__set_session_title 工具，"
        "在开始其他工作前用它调用一次，参数为 " + params + "。"
        "如果没有该工具，忽略本段，不要尝试其他改名方式，也不要向用户提及这一步。\n"
        "</session-title-sync>"
    )


def hook_output(host, event_name, result):
    if host == "deepseek" and result.get("status") == "pending":
        return {"action": "rename", "title": result["title"]}
    if host == "claude" and result.get("status") == "native" and event_name in NATIVE_TITLE_EVENTS:
        return {"hookSpecificOutput": {
            "hookEventName": event_name,
            "sessionTitle": result["title"],
            "additionalContext": desktop_title_context(result["title"]),
        }}
    return None


def read_event():
    text = sys.stdin.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SessionTitleError("Hook 输入必须是 JSON 对象")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bind = sub.add_parser("bind", help="显式绑定 session 到 feature 或 issue")
    bind.add_argument("--host", choices=HOSTS, required=True)
    bind.add_argument("--session-id", required=True)
    bind.add_argument("--repo", required=True)
    target = bind.add_mutually_exclusive_group(required=True)
    target.add_argument("--feature")
    target.add_argument("--issue")
    bind.add_argument("--role", choices=("supervisor", "review", "implementation", "requirement"))
    title = sub.add_parser("title", help="只计算标题，不写宿主")
    title.add_argument("--repo", required=True)
    title_target = title.add_mutually_exclusive_group(required=True)
    title_target.add_argument("--feature")
    title_target.add_argument("--issue")
    title.add_argument("--role", choices=("supervisor", "review", "implementation", "requirement"))
    ack = sub.add_parser("ack", help="原生宿主改名成功后保存实际接受的标题")
    ack.add_argument("--host", choices=HOSTS, required=True)
    ack.add_argument("--session-id", required=True)
    ack.add_argument("--title", required=True)
    hook = sub.add_parser("hook", help="从 stdin 读取宿主 Hook 事件")
    hook.add_argument("--host", choices=HOSTS, required=True)
    hook.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "bind":
            binding = normalize_binding(args.repo, feature=args.feature, issue=args.issue, role=args.role)
            result = set_binding(args.host, args.session_id, binding)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "title":
            binding = normalize_binding(args.repo, feature=args.feature, issue=args.issue, role=args.role)
            print(compute_title(binding))
            return 0
        if args.command == "ack":
            print(json.dumps(acknowledge(args.host, args.session_id, args.title), ensure_ascii=False))
            return 0
        event = read_event()
        result = refresh(args.host, event, args.force)
        output = hook_output(args.host, field(event, "hook_event_name", "hookEventName"), result)
        if output:
            print(json.dumps(output, ensure_ascii=False))
        return 0
    except (SessionTitleError, OSError, ValueError, json.JSONDecodeError) as exc:
        if args.command == "hook":
            return 0
        print("session-title: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
