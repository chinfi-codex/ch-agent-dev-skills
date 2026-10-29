#!/usr/bin/env python3
"""Validate, resolve, and render role-based host dispatch configuration."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from process_check import InputError, read_first_yaml_block

HOSTS = ("codex", "claude-code", "zcode", "dsh")
TIERS = ("default", "escalated")
EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra", "disabled", "enabled", "off"}
ABSTRACT_MODELS = {"default", "escalated", "economy", "main", "inherit"}


class ConfigError(ValueError):
    pass


def _mapping(value, path: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} 必须是映射")
    return value


def _keys(value: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"{path} 含未知字段: {', '.join(unknown)}")


def _model(value, host: str, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConfigError(f"{path} 必须是不带首尾空白的具体模型 ID")
    if value.lower() in ABSTRACT_MODELS or any(c in value for c in "<>[]{}") or any(c.isspace() for c in value):
        raise ConfigError(f"{path} 不是具体模型 ID: {value}")
    if host in {"zcode", "dsh"}:
        parts = value.split("/")
        if len(parts) != 2 or not all(parts):
            raise ConfigError(f"{path} 必须使用 <provider>/<model> 完整路由")
    elif "/" in value:
        raise ConfigError(f"{path} 不得包含宿主 provider 前缀")
    return value


def _route(value: dict, host: str, path: str) -> dict:
    value = _mapping(value, path)
    _keys(value, {"model", "effort"}, path)
    if set(value) != {"model", "effort"}:
        raise ConfigError(f"{path} 必须同时填写 model 与 effort")
    model = _model(value["model"], host, f"{path}.model")
    effort = value["effort"]
    if not isinstance(effort, str) or effort not in EFFORTS:
        raise ConfigError(f"{path}.effort 无效: {effort!r}")
    return {"model": model, "effort": effort}


def validate(config: dict, catalogs: dict | None = None) -> dict:
    dispatch = _mapping(config.get("dispatch"), "dispatch")
    if "model" in dispatch:
        raise ConfigError("dispatch.model 已废止；请迁移为 dispatch.hosts.<宿主>.supervisor-review / development")
    _keys(dispatch, {"schema-version", "executor", "parallel-max", "hosts"}, "dispatch")
    if dispatch.get("schema-version") != 1:
        raise ConfigError("dispatch.schema-version 必须为 1")
    if dispatch.get("executor") != "subagent":
        raise ConfigError("dispatch.executor 必须为 subagent")
    parallel = dispatch.get("parallel-max")
    if not isinstance(parallel, int) or isinstance(parallel, bool) or parallel < 1:
        raise ConfigError("dispatch.parallel-max 必须是正整数")
    hosts = _mapping(dispatch.get("hosts"), "dispatch.hosts")
    if not hosts:
        raise ConfigError("dispatch.hosts 至少配置一个宿主")
    unknown = sorted(set(hosts) - set(HOSTS))
    if unknown:
        raise ConfigError(f"dispatch.hosts 含未知宿主: {', '.join(unknown)}")

    validated = {"schema-version": 1, "executor": "subagent", "parallel-max": parallel, "hosts": {}}
    for host, raw_host in hosts.items():
        path = f"dispatch.hosts.{host}"
        host_config = _mapping(raw_host, path)
        _keys(host_config, {"supervisor-review", "development"}, path)
        if set(host_config) != {"supervisor-review", "development"}:
            raise ConfigError(f"{path} 必须同时配置 supervisor-review 与 development")
        supervisor = _route(host_config["supervisor-review"], host, f"{path}.supervisor-review")
        raw_development = _mapping(host_config["development"], f"{path}.development")
        _keys(raw_development, set(TIERS), f"{path}.development")
        if set(raw_development) != set(TIERS):
            raise ConfigError(f"{path}.development 必须同时配置 default 与 escalated")
        development = {
            tier: _route(raw_development[tier], host, f"{path}.development.{tier}")
            for tier in TIERS
        }
        for role, route in [("supervisor-review", supervisor), *development.items()]:
            if catalogs is None:
                continue
            host_catalog = _mapping(catalogs.get(host), f"catalogs.{host}")
            efforts = host_catalog.get(route["model"])
            if efforts is None:
                raise ConfigError(f"{host} 模型目录中不存在 {route['model']}（{role}）")
            if route["effort"] not in efforts:
                raise ConfigError(f"{host}/{route['model']} 不支持 effort={route['effort']}（{role}）")
        validated["hosts"][host] = {"supervisor-review": supervisor, "development": development}
    return validated


def load(path: Path) -> dict:
    return validate(read_first_yaml_block(path))


def _split_route(route: dict) -> tuple[str, str]:
    return tuple(route["model"].split("/", 1))


def resolve(dispatch: dict, host: str, role: str, tier: str = "default") -> dict:
    if host not in dispatch["hosts"]:
        raise ConfigError(f"当前宿主 {host} 未配置；不得跨宿主套用模型")
    if role not in {"supervisor", "review", "development"}:
        raise ConfigError(f"未知执行角色: {role}")
    if tier not in TIERS:
        raise ConfigError(f"未知开发档位: {tier}")
    host_config = dispatch["hosts"][host]
    config_role = "development" if role == "development" else "supervisor-review"
    route = host_config[config_role][tier] if role == "development" else host_config[config_role]
    context = "current-session" if role == "supervisor" else "independent-subagent"
    result = {
        "host": host,
        "role": role,
        "config-role": config_role,
        "context": context,
        "model": route["model"],
        "effort": route["effort"],
    }
    if role == "development":
        result["tier"] = tier
    native = {}
    if host == "codex":
        native = {"model": route["model"], "model_reasoning_effort": route["effort"]}
        if context != "current-session":
            native["agent_type"] = "development-escalated" if role == "development" and tier == "escalated" else config_role
    elif host == "claude-code":
        native = {"model": route["model"], "effortLevel" if context == "current-session" else "effort": route["effort"]}
        if context != "current-session":
            native["subagent_type"] = "development-escalated" if role == "development" and tier == "escalated" else config_role
    elif host == "zcode":
        native = {"model": route["model"], "thoughtLevel": route["effort"]}
        if context != "current-session":
            native["subagent_type"] = "development-escalated" if role == "development" and tier == "escalated" else config_role
    else:
        provider, model = _split_route(route)
        native = {"provider": provider, "model": model, "reasoningEffort" if context == "current-session" else "reasoning_effort": route["effort"]}
    result["native"] = native
    return result


def _agent_markdown(host: str, name: str, route: dict, description: str, body: str) -> str:
    effort_key = "thoughtLevel" if host == "zcode" else "effort"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        f"model: {route['model']}\n"
        f"{effort_key}: {route['effort']}\n"
        "---\n"
        f"{body.strip()}\n"
    )


def render(dispatch: dict, host: str, dsh_profile: str = "headless") -> dict[str, str]:
    if host not in dispatch["hosts"]:
        raise ConfigError(f"当前宿主 {host} 未配置；不得跨宿主套用模型")
    host_config = dispatch["hosts"][host]
    supervisor = host_config["supervisor-review"]
    routes = {
        "supervisor-review": supervisor,
        "development": host_config["development"]["default"],
        "development-escalated": host_config["development"]["escalated"],
    }
    descriptions = {
        "supervisor-review": "独立 code review / 符合性核对；只评不改，使用监督评审模型。",
        "development": "独立 /implement 开发会话；在指定 worktree 实现、测试并落证据。",
        "development-escalated": "独立 /implement 升档开发会话；在指定 worktree 实现、测试并落证据。",
    }
    bodies = {
        "supervisor-review": "你是独立评审对话。先按派发提示词加载 ai-review skill 并严格执行；只评不改，除指定评审报告外不写文件，不参与实现。",
        "development": "你是被派发的开发实现对话。先按派发提示词加载 implement skill 并严格执行；只在指定 worktree 改动，完成测试与落盘证据后结构化回报。",
        "development-escalated": "你是被派发的升档开发实现对话。先按派发提示词加载 implement skill 并严格执行；只在指定 worktree 改动，完成测试与落盘证据后结构化回报。",
    }
    if host in {"claude-code", "zcode"}:
        files = {
            f"agents/{name}.md": _agent_markdown(host, name, route, descriptions[name], bodies[name])
            for name, route in routes.items()
        }
        if host == "claude-code":
            files["settings.json"] = json.dumps(
                {"model": supervisor["model"], "effortLevel": supervisor["effort"]},
                ensure_ascii=False,
                indent=2,
            ) + "\n"
        return files
    if host == "codex":
        config = [f'model = "{supervisor["model"]}"', f'model_reasoning_effort = "{supervisor["effort"]}"', ""]
        files = {}
        for name, route in routes.items():
            config.extend([
                f"[agents.{name}]",
                f'description = "{descriptions[name]}"',
                f'config_file = "agents/{name}.toml"',
                "",
            ])
            files[f"agents/{name}.toml"] = (
                f'model = "{route["model"]}"\n'
                f'model_reasoning_effort = "{route["effort"]}"\n'
            )
        files["config.toml"] = "\n".join(config)
        return files
    provider, supervisor_model = _split_route(supervisor)
    allowed = []
    for route in (
        supervisor,
        host_config["development"]["default"],
        host_config["development"]["escalated"],
    ):
        route_provider, route_model = _split_route(route)
        entry = {"provider": route_provider, "model": route_model}
        if entry not in allowed:
            allowed.append(entry)
    lines = [
        "- id: agent-default-model",
        "  name: '@deepseek-ai/dsh-agent-default-model'",
        "  config:",
        f"    provider: {provider}",
        f"    model: {supervisor_model}",
        f"    reasoningEffort: {supervisor['effort']}",
        "- id: tool-subagent",
        "  name: '@deepseek-ai/dsh-tool-subagent'",
        "  config:",
        "    provider: spawn",
        "    toolName: subagent",
        "    modelSelectionSettings: true",
    ]
    settings = [
        "- id: subagent-model-selection-settings",
        "  name: '@deepseek-ai/dsh-tool-subagent/model-selection-settings'",
        "  config:",
        "    enabled: true",
        "    allowedModels:",
    ]
    for route in allowed:
        settings.extend([
            f"      - provider: {route['provider']}",
            f"        model: {route['model']}",
        ])
    if dsh_profile == "headless":
        lines.append("- insert:")
        lines.extend(f"    {line}" if line else "" for line in settings)
    elif dsh_profile == "desktop":
        lines.extend(settings)
    else:
        raise ConfigError(f"未知 DSH profile 类型: {dsh_profile}")
    return {"cordis.patch.yml": "\n".join(lines) + "\n"}


def write_rendered(files: dict[str, str], output: Path) -> list[str]:
    written = []
    for relative, content in files.items():
        path = output / relative
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise ConfigError(f"拒绝覆盖已有不同配置: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "resolve", "render"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", choices=HOSTS)
    parser.add_argument("--role", choices=("supervisor", "review", "development"))
    parser.add_argument("--tier", choices=TIERS, default="default")
    parser.add_argument("--catalog", type=Path, help="可选：{host: {model: [effort...]}} 实时模型目录")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dsh-profile", choices=("headless", "desktop"), default="headless")
    args = parser.parse_args(argv)
    try:
        catalogs = json.loads(args.catalog.read_text(encoding="utf-8")) if args.catalog else None
        dispatch = validate(read_first_yaml_block(args.config), catalogs)
        if args.command == "validate":
            result = {"ok": True, "hosts": sorted(dispatch["hosts"])}
        else:
            if not args.host:
                raise ConfigError(f"{args.command} 必须指定 --host")
            if args.command == "resolve":
                if not args.role:
                    raise ConfigError("resolve 必须指定 --role")
                result = resolve(dispatch, args.host, args.role, args.tier)
            else:
                if not args.output:
                    raise ConfigError("render 必须指定 --output")
                result = {"ok": True, "files": write_rendered(render(dispatch, args.host, args.dsh_profile), args.output)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ConfigError, InputError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
