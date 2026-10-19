#!/usr/bin/env python3
"""把 session_title.py 以非破坏、幂等方式安装到 CC/Codex/ZCode hooks。"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HOSTS = ("claude", "codex", "zcode")
MARKER = "session_title.py"
EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")
HOST_EVENTS = {
    "claude": ("SessionStart", "UserPromptSubmit"),
    "codex": EVENTS,
    "zcode": EVENTS,
}


def atomic_json_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def python_prefix():
    override = os.environ.get("SESSION_TITLE_PYTHON")
    if override:
        return shlex.split(override)
    return [sys.executable]


def hook_item(host, script):
    command = " ".join(shlex.quote(x) for x in python_prefix() + [str(script), "hook", "--host", host])
    return {"type": "command", "command": command, "timeout": 60}


def is_managed_item(item):
    if not isinstance(item, dict):
        return False
    hooks = item.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(isinstance(h, dict) and MARKER in str(h.get("command", "")) for h in hooks)


def merge_events(existing, host, script, zcode_nested=False):
    result = remove_events(existing)
    for event in HOST_EVENTS[host]:
        groups = list(result.get(event) or [])
        matcher = "startup|resume|fork" if event == "SessionStart" else ("Write|Edit" if event == "PostToolUse" else None)
        group = {"hooks": [hook_item(host, script)]}
        if matcher:
            group["matcher"] = matcher
        groups.append(group)
        result[event] = groups
    return result


def remove_events(existing):
    result = dict(existing) if isinstance(existing, dict) else {}
    for event in EVENTS:
        old = result.get(event)
        if not isinstance(old, list):
            continue
        groups = [g for g in old if not is_managed_item(g)]
        if groups:
            result[event] = groups
        else:
            result.pop(event, None)
    return result


def load_config(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("宿主配置必须是 JSON 对象")
    return data


def install(host, script, config_path):
    config = load_config(config_path)
    if host == "zcode":
        hooks = dict(config.get("hooks") or {})
        hooks["enabled"] = True
        hooks["events"] = merge_events(hooks.get("events"), host, script, True)
        config["hooks"] = hooks
    else:
        config["hooks"] = merge_events(config.get("hooks"), host, script)
    atomic_json_write(config_path, config)
    return config_path


def default_config(host, home):
    if host == "claude":
        return home / ".claude" / "settings.json"
    if host == "codex":
        return home / ".codex" / "hooks.json"
    return home / ".zcode" / "cli" / "config.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=HOSTS, action="append", required=True)
    parser.add_argument("--script", type=Path, help="已分发的 session_title.py；默认取本脚本同目录")
    parser.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, help="单宿主时指定配置文件（主要用于测试）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove", action="store_true", help="只移除本功能管理的 hooks")
    args = parser.parse_args(argv)
    if args.config and len(args.host) != 1:
        parser.error("--config 只能与一个 --host 一起使用")
    script = (args.script or Path(__file__).with_name("session_title.py")).resolve()
    if not script.is_file():
        parser.error("找不到 " + str(script))
    home = (args.home or Path.home()).expanduser().resolve()
    try:
        for host in dict.fromkeys(args.host):
            path = (args.config or default_config(host, home)).expanduser().resolve()
            config = load_config(path) if path.exists() else {}
            if args.remove:
                if host == "zcode":
                    hooks = dict(config.get("hooks") or {})
                    hooks["events"] = remove_events(hooks.get("events"))
                    config["hooks"] = hooks
                else:
                    config["hooks"] = remove_events(config.get("hooks"))
                if not args.dry_run and path.exists():
                    atomic_json_write(path, config)
            elif args.dry_run:
                if host == "zcode":
                    hooks = dict(config.get("hooks") or {})
                    hooks["enabled"] = True
                    hooks["events"] = merge_events(hooks.get("events"), host, script)
                    config["hooks"] = hooks
                else:
                    config["hooks"] = merge_events(config.get("hooks"), host, script)
            else:
                install(host, script, path)
                config = load_config(path)
            if args.remove:
                result = {"host": host, "path": str(path), "removed": True}
            elif args.dry_run:
                result = {"host": host, "path": str(path), "config": config}
            else:
                result = {"host": host, "path": str(path)}
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("install-host-hooks: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
