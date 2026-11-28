#!/usr/bin/env python3
"""Strict v2 agents-config parsing and validation, with no YAML dependency.

Public API:
    read_frontmatter(path) -> dict
    read_first_yaml_block(path) -> dict
        Read Markdown without rewriting it; only the frontmatter/first fenced
        YAML block is parsed.
    parse_yaml_subset(text) -> dict
        Parse the deliberately small YAML subset used by this repository:
        indentation mappings/lists, list mappings, flow lists/mappings, quoted
        and plain scalars.  Duplicate keys, malformed indentation, aliases,
        tags, anchors and multiline scalars raise ConfigError.
    validate_config(config, schema_path=None) -> dict
        Validate a schema-version 2 mapping, apply documented defaults to
        optional sections, enforce cross-field invariants, and return a deep
        copy.  Unknown/legacy keys and unsafe mode combinations fail closed.
    load(path) -> dict
        Equivalent to ``validate_config(read_first_yaml_block(path))``.

The canonical schema is ``../references/agents-config.schema.json``.  This is
not a general YAML parser and must not be replaced by a permissive parser that
silently ignores unsupported syntax.
"""
from __future__ import annotations

import copy
import argparse
import json
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Direct ``python -I path/to/agents_config.py`` does not reliably include the
# script directory on sys.path; sibling standard-library modules live here.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import dispatch_config as _dispatch
import json_schema


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "agents-config.schema.json"
_DEFAULTS = {
    "conformance": {"mode": "always", "sample-rate": "1/3", "checker": "main-session"},
    "max-fix-rounds": 99,
    "review-round-alert": 3,
    "ocr": {"mode": "delegate", "provider": None, "model": None,
            "rule-file": ".opencodereview/rule.json"},
    "gitlab-ci": "manual",
    "artifact-budget": {
        "impl-log-max-lines": 120,
        "evidence-max-lines": 120,
        "impl-dir-max-mb": 20,
        "integration-dir-max-mb": 20,
        "artifact-manifest-max-mb": 5,
    },
    "integration-runtime": {
        "ready-timeout-seconds": 60,
        "stop-timeout-seconds": 30,
        "lease-timeout-seconds": 3600,
        "runtime-dir": ".agent-dev/runtime",
        "dev-service": None,
    },
    "retention": {"keep-generations": 2, "salvage-days": 30,
                  "journal-days": 30, "integration-days": 30},
    "enforcement": {"require-host-capabilities": True, "require-clean-tree": True},
}
_FEATURE_DEFAULTS = {
    "integration-branch": "feat/<feature-slug>",
    "since": None,
    "verify-budget-seconds": 1200,
    "selection-map": None,
    "ci": None,
    "verify-instance": None,
    "prod": None,
    "pr": None,
}
_VERIFY_DEFAULTS = {
    "production-exclude": ["tests/**", "**/tests/**", "test/**", "**/test/**",
                            "**/test_*", "**/*_test.*", "**/*.test.*", "**/*.spec.*",
                            "docs/**", "dev/**", "**/*.md"],
    "scope": "strict",
    "quarantine-max-age-days": 14,
    "daily-regression": None,
}


class ConfigError(ValueError):
    """Configuration or supported-YAML syntax is invalid."""


def _strip_comment(line: str) -> str:
    out: List[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if quote == '"':
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            out.append(char)
            if char == quote:
                # Consume both halves of YAML's doubled single-quote escape.
                if index + 1 < len(line) and line[index + 1] == "'":
                    index += 1
                    out.append(line[index])
                else:
                    quote = None
        elif char in ('"', "'"):
            quote = char
            out.append(char)
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            break
        else:
            out.append(char)
        index += 1
    if quote:
        raise ConfigError("unterminated quoted scalar: %s" % line)
    return "".join(out).rstrip()


def _split_flow(text: str, delimiter: str = ",") -> List[str]:
    parts: List[str] = []
    start = 0
    stack: List[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 1
                else:
                    quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in "[{":
            stack.append("]" if char == "[" else "}")
        elif char in "]}":
            if not stack or stack.pop() != char:
                raise ConfigError("unbalanced flow collection: %s" % text)
        elif char == delimiter and not stack:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    if quote or stack:
        raise ConfigError("unterminated flow collection: %s" % text)
    parts.append(text[start:].strip())
    if any(part == "" for part in parts):
        raise ConfigError("empty item in flow collection: %s" % text)
    return parts


def _mapping_colon(text: str, flow: bool = False) -> int:
    stack: List[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 1
                else:
                    quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack:
                raise ConfigError("unbalanced mapping: %s" % text)
            stack.pop()
        elif char == ":" and not stack:
            if flow or index + 1 == len(text) or text[index + 1].isspace():
                return index
        index += 1
    return -1


def _parse_key(text: str) -> str:
    text = text.strip()
    if not text:
        raise ConfigError("empty mapping key")
    if text[0] in ('"', "'"):
        value = _scalar(text)
        if not isinstance(value, str) or not value:
            raise ConfigError("mapping key must be a non-empty string")
        return value
    if any(char in text for char in "[]{}") or text.startswith(("&", "*", "!")):
        raise ConfigError("unsupported mapping key: %s" % text)
    return text


def _scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    if text[0] == '"':
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise ConfigError("invalid double-quoted scalar %s: %s" % (text, exc))
        if not isinstance(value, str):
            raise ConfigError("quoted scalar must be a string")
        return value
    if text[0] == "'":
        if len(text) < 2 or text[-1] != "'":
            raise ConfigError("invalid single-quoted scalar: %s" % text)
        return text[1:-1].replace("''", "'")
    if text[0] == "[":
        if not text.endswith("]"):
            raise ConfigError("unterminated flow list: %s" % text)
        inner = text[1:-1].strip()
        return [] if not inner else [_scalar(item) for item in _split_flow(inner)]
    if text[0] == "{":
        if not text.endswith("}"):
            raise ConfigError("unterminated flow mapping: %s" % text)
        inner = text[1:-1].strip()
        result: Dict[str, Any] = {}
        if not inner:
            return result
        for item in _split_flow(inner):
            colon = _mapping_colon(item, flow=True)
            if colon < 0:
                raise ConfigError("flow mapping item lacks colon: %s" % item)
            key = _parse_key(item[:colon])
            if key in result:
                raise ConfigError("duplicate mapping key: %s" % key)
            result[key] = _scalar(item[colon + 1:])
        return result
    if text.startswith(("|", ">", "&", "*", "!")):
        raise ConfigError("unsupported YAML scalar/anchor/tag: %s" % text)
    if text in ("null", "Null", "NULL", "~"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if re.fullmatch(r"[-+]?(?:0|[1-9][0-9]*)", text):
        return int(text)
    if re.fullmatch(r"[-+]?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[eE][-+]?[0-9]+)?", text):
        return float(text)
    if text[0] in "]}" or text[-1:] in "[{":
        raise ConfigError("unbalanced scalar: %s" % text)
    return text


class _YamlParser:
    def __init__(self, text: str):
        self.lines: List[Tuple[int, str, int]] = []
        for number, raw in enumerate(text.splitlines(), 1):
            prefix = raw[:len(raw) - len(raw.lstrip(" \t"))]
            if "\t" in prefix:
                raise ConfigError("line %d: tabs are not allowed for indentation" % number)
            line = _strip_comment(raw)
            if not line.strip():
                continue
            if line.strip() in ("---", "..."):
                raise ConfigError("line %d: YAML document markers are not supported" % number)
            indent = len(line) - len(line.lstrip(" "))
            self.lines.append((indent, line.strip(), number))

    @staticmethod
    def _is_item(content: str) -> bool:
        return content == "-" or content.startswith("- ")

    def parse(self) -> Dict[str, Any]:
        if not self.lines:
            return {}
        if self.lines[0][0] != 0:
            raise ConfigError("line %d: top-level content must not be indented" % self.lines[0][2])
        value, index = self._block(0, 0)
        if index != len(self.lines):
            indent, content, number = self.lines[index]
            raise ConfigError("line %d: unexpected indentation/content: %s" % (number, content))
        if not isinstance(value, dict):
            raise ConfigError("top-level YAML value must be a mapping")
        return value

    def _block(self, index: int, indent: int) -> Tuple[Any, int]:
        if index >= len(self.lines) or self.lines[index][0] != indent:
            raise ConfigError("internal YAML indentation error")
        if self._is_item(self.lines[index][1]):
            return self._list(index, indent)
        return self._map(index, indent)

    def _pair(self, content: str, number: int, next_index: int, indent: int) -> Tuple[str, Any, int]:
        colon = _mapping_colon(content)
        if colon < 0:
            raise ConfigError("line %d: expected key: value mapping" % number)
        key = _parse_key(content[:colon])
        raw_value = content[colon + 1:].strip()
        if raw_value:
            return key, _scalar(raw_value), next_index
        if next_index < len(self.lines) and self.lines[next_index][0] > indent:
            child_indent = self.lines[next_index][0]
            value, next_index = self._block(next_index, child_indent)
            return key, value, next_index
        return key, None, next_index

    def _map(self, index: int, indent: int,
             first: Optional[Tuple[str, int]] = None) -> Tuple[Dict[str, Any], int]:
        result: Dict[str, Any] = {}
        if first is not None:
            key, value, index = self._pair(first[0], first[1], index, indent)
            result[key] = value
        while index < len(self.lines):
            current_indent, content, number = self.lines[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ConfigError("line %d: unexpected indentation" % number)
            if self._is_item(content):
                break
            key, value, next_index = self._pair(content, number, index + 1, indent)
            if key in result:
                raise ConfigError("line %d: duplicate mapping key %s" % (number, key))
            result[key] = value
            index = next_index
        return result, index

    def _list(self, index: int, indent: int) -> Tuple[List[Any], int]:
        result: List[Any] = []
        while index < len(self.lines):
            current_indent, content, number = self.lines[index]
            if current_indent != indent or not self._is_item(content):
                break
            raw = content[1:].strip()
            if not raw:
                index += 1
                if index < len(self.lines) and self.lines[index][0] > indent:
                    child_indent = self.lines[index][0]
                    value, index = self._block(index, child_indent)
                else:
                    value = None
                result.append(value)
                continue
            if _mapping_colon(raw) >= 0:
                value, index = self._map(index + 1, indent + 2, first=(raw, number))
            else:
                value = _scalar(raw)
                index += 1
                if index < len(self.lines) and self.lines[index][0] > indent:
                    raise ConfigError("line %d: scalar list item cannot have nested content"
                                      % self.lines[index][2])
            result.append(value)
        return result, index


def parse_yaml_subset(text: str) -> Dict[str, Any]:
    """Parse the repository's strict YAML subset into ordinary Python values."""
    if not isinstance(text, str):
        raise ConfigError("YAML input must be text")
    try:
        return _YamlParser(text).parse()
    except ConfigError:
        raise
    except (ValueError, IndexError) as exc:
        raise ConfigError("invalid YAML subset: %s" % exc)


def _read(path: Any) -> Tuple[Path, str]:
    source = Path(path)
    try:
        return source, source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError("cannot read %s: %s" % (source, exc))


def read_frontmatter(path: Any) -> Dict[str, Any]:
    source, text = _read(path)
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not match:
        raise ConfigError("%s lacks valid frontmatter" % source)
    return parse_yaml_subset(match.group(1))


def read_first_yaml_block(path: Any) -> Dict[str, Any]:
    source, text = _read(path)
    match = re.search(r"```ya?ml[ \t]*\r?\n(.*?)\r?\n```[ \t]*(?:\r?\n|$)", text, re.S)
    if not match:
        raise ConfigError("%s lacks a fenced yaml block" % source)
    return parse_yaml_subset(match.group(1))


def _fill_defaults(result: Dict[str, Any], defaults: Dict[str, Any]) -> None:
    for key, value in defaults.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            _fill_defaults(result[key], value)


def _valid_repo_path(value: str) -> bool:
    return (bool(value) and value == value.strip() and "\x00" not in value
            and not value.startswith(("/", "\\")) and "\\" not in value
            and not re.match(r"^[A-Za-z]:", value)
            and ".." not in Path(value).parts and not any(not part for part in value.split("/")))


def _valid_glob(value: str) -> bool:
    if not _valid_repo_path(value) or any(char in value for char in "{}"):
        return False
    # Character classes must be closed; no malformed pattern is silently literal.
    return value.count("[") == value.count("]")


def _valid_branch(value: str, allow_placeholder: bool = False) -> bool:
    branch = value.replace("<feature-slug>", "feature") if allow_placeholder else value
    if "<" in branch or ">" in branch:
        return False
    if (not branch or branch != branch.strip() or branch.startswith("-")
            or branch.startswith("/") or branch.endswith(("/", "."))
            or ".." in branch or "//" in branch or "@{" in branch
            or any(char in branch for char in " ~^:?*[\\\x00")):
        return False
    return all(component and component != "@" and not component.endswith(".lock")
               and not component.startswith(".") for component in branch.split("/"))


def _check_paths(config: Dict[str, Any]) -> None:
    policy = config["verify-policy"]
    for key in ("deny", "test-change", "production-exclude"):
        for pattern in policy.get(key, []):
            if not _valid_glob(pattern):
                raise ConfigError("verify-policy.%s contains invalid repo glob: %r" % (key, pattern))
    for item in policy["generated-refresh"]:
        if not _valid_glob(item["glob"]):
            raise ConfigError("generated-refresh glob is invalid: %r" % item["glob"])
    scope = policy.get("scope")
    if isinstance(scope, dict) and "production-exclude" in scope:
        if "production-exclude" in policy and policy["production-exclude"] != _VERIFY_DEFAULTS["production-exclude"]:
            raise ConfigError("scope.production-exclude and verify-policy.production-exclude cannot both be set")
        for pattern in scope["production-exclude"]:
            if not _valid_glob(pattern):
                raise ConfigError("scope.production-exclude contains invalid glob: %r" % pattern)
    closeout = config["feature-closeout"]
    for section, key in ((policy["gate-scope"], "categories-file"), (closeout, "selection-map"),
                         (config["ocr"], "rule-file"),
                         (config["integration-runtime"], "runtime-dir")):
        value = section.get(key)
        if value is not None and not _valid_repo_path(value):
            raise ConfigError("%s must be a repository-relative path without '..': %r" % (key, value))
    daily = policy.get("daily-regression")
    if daily and not _valid_repo_path(daily["record-dir"]):
        raise ConfigError("daily-regression.record-dir must be repository-relative")
    if closeout["verify-instance"] and not _valid_repo_path(closeout["verify-instance"]["lease"]):
        raise ConfigError("verify-instance.lease must be repository-relative")


def _check_rate(value: Any) -> None:
    if isinstance(value, bool):
        raise ConfigError("conformance.sample-rate must be a rational number, not boolean")
    try:
        rate = value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ConfigError("conformance.sample-rate is invalid: %r (%s)" % (value, exc))
    if not (Fraction(0) < rate <= Fraction(1)):
        raise ConfigError("conformance.sample-rate must be > 0 and <= 1")


def _check_modes(config: Dict[str, Any]) -> None:
    if config["host-budget"]["max-runs"] > config["host-budget"]["total"]:
        raise ConfigError("host-budget.max-runs cannot exceed total workers")
    refresh = config["verify-policy"]["generated-refresh"]
    refresh_globs = [item["glob"] for item in refresh]
    refresh_ids = [item["id"] for item in refresh]
    refresh_generators = [item["generator"] for item in refresh]
    if len(set(refresh_globs)) != len(refresh_globs):
        raise ConfigError("each generated-refresh glob must have exactly one generator")
    if len(set(refresh_ids)) != len(refresh_ids):
        raise ConfigError("generated-refresh IDs must be unique")
    if len(set(refresh_generators)) != len(refresh_generators):
        raise ConfigError("each generated-refresh entry must have a unique generator command")
    if "functional" in config["verify-policy"]["gate-scope"]["non-gating"]:
        raise ConfigError("functional tests cannot be declared non-gating")
    daily = config["verify-policy"].get("daily-regression")
    if daily and daily["enabled"] and not daily["workflow"]:
        raise ConfigError("enabled daily-regression requires a CI workflow")

    closeout = config["feature-closeout"]
    mode = closeout["mode"]
    if not _valid_branch(config["main-branch"]):
        raise ConfigError("main-branch is not a valid Git branch name")
    if not _valid_branch(closeout["integration-branch"], allow_placeholder=True):
        raise ConfigError("feature-closeout.integration-branch is not a valid branch pattern")
    if mode != "local-human" and (closeout["verify-instance"] is not None or closeout["prod"] is not None):
        raise ConfigError("verify-instance/prod are only valid with feature-closeout.mode=local-human")
    ci = closeout["ci"]
    if mode in ("local-human", "pr") and ci is not None:
        raise ConfigError("feature-closeout.ci is only valid with server-enforced/cooperative-ci")
    if mode not in ("local-human", "pr") and config["integration-runtime"]["dev-service"] is not None:
        raise ConfigError("integration-runtime.dev-service is only valid with local-human")
    if mode not in ("local-human", "pr") and ci is None:
        raise ConfigError("server-enforced/cooperative-ci requires feature-closeout.ci")
    pr = closeout.get("pr")
    if mode == "pr":
        if pr is None:
            raise ConfigError("feature-closeout.mode=pr requires feature-closeout.pr")
        if not re.fullmatch(r"https://[^\s/]+(?:/[^\s]*)?", pr["base-url"]):
            raise ConfigError("feature-closeout.pr.base-url must be an HTTPS origin")
        if any(char.isspace() for char in pr["repository"]) or pr["repository"].startswith(("/", ".")):
            raise ConfigError("feature-closeout.pr.repository must be an owner/repository path")
    elif pr is not None:
        raise ConfigError("feature-closeout.pr is only valid with mode=pr")
    if ci is not None:
        if not re.fullmatch(r"https://[^\s/]+(?:/[^\s]*)?", ci["base-url"]):
            raise ConfigError("feature-closeout.ci.base-url must be an HTTPS origin")
        if any(char.isspace() for char in ci["repository"]) or ci["repository"].startswith(("/", ".")):
            raise ConfigError("feature-closeout.ci.repository must be an owner/repository path")
        if not _valid_repo_path(ci["artifact"]):
            raise ConfigError("feature-closeout.ci.artifact must be a repository-relative filename")

    ocr = config["ocr"]
    if ocr["mode"] in ("local", "ci"):
        if not ocr["provider"] or not ocr["model"]:
            raise ConfigError("ocr local/ci modes require provider and model")
        if any(char in ocr["model"] for char in "<>") or ocr["model"] != ocr["model"].strip():
            raise ConfigError("ocr.model must be a concrete model ID")
    if (ocr["mode"] == "ci") != (config["gitlab-ci"] == "enabled"):
        raise ConfigError("ocr.mode=ci and gitlab-ci=enabled must be configured together")

    enforcement = config["enforcement"]
    protected = enforcement.get("require-protected-branch", False)
    expected_sha = enforcement.get("require-expected-sha", False)
    if mode == "server-enforced" and not (protected and expected_sha):
        raise ConfigError("server-enforced requires protected-branch and expected-SHA enforcement")
    if mode != "server-enforced" and (protected or expected_sha):
        raise ConfigError("non-server modes must not claim server-side branch/SHA enforcement")
    _check_rate(config["conformance"]["sample-rate"])


def validate_config(config: Dict[str, Any], schema_path: Any = None) -> Dict[str, Any]:
    """Return a validated/defaulted deep copy of a v2 configuration."""
    if not isinstance(config, dict):
        raise ConfigError("agents config must be a mapping")
    result = copy.deepcopy(config)
    _fill_defaults(result, _DEFAULTS)
    if isinstance(result.get("feature-closeout"), dict):
        _fill_defaults(result["feature-closeout"], _FEATURE_DEFAULTS)
    if isinstance(result.get("verify-policy"), dict):
        _fill_defaults(result["verify-policy"], _VERIFY_DEFAULTS)
    errors = json_schema.load_validate(schema_path or SCHEMA_PATH, result)
    if errors:
        raise ConfigError("agents config validation failed:\n  " + "\n  ".join(errors))
    try:
        _dispatch.validate({"dispatch": result["dispatch"]})
    except _dispatch.ConfigError as exc:
        raise ConfigError(str(exc))
    _check_paths(result)
    _check_modes(result)
    return result


def load(path: Any) -> Dict[str, Any]:
    """Read the first Markdown YAML block and return a strict v2 config."""
    return validate_config(read_first_yaml_block(path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "show"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
        if args.command == "show":
            print(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(json.dumps({"ok": True, "schema-version": config["schema-version"],
                              "mode": config["feature-closeout"]["mode"]}, ensure_ascii=False))
        return 0
    except (ConfigError, json_schema.SchemaError, OSError, ValueError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
