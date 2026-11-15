#!/usr/bin/env python3
"""Execute ticket DoD + fast commands and emit hash-backed v2 evidence.

Expected lazy-loaded shared APIs (kept small so this module has no v1 fallback):
  ticket_context.parse_issue(issue_path) -> mapping/object with issue id and dod
  ticket_context.context(issue_path, repo) -> mapping/object with base/head/tree
  agents_config.load(root_or_issue) -> configuration mapping
  agents_config.fast(config) -> non-empty command (quality-gate.fast is accepted)

Only ``run`` executes the parsed DoD and the configured fast command.  ``red``
records a real first assertion failure for newly added tests. ``bug-regression``
uses a disposable detached worktree for caller-supplied revert/restore commands;
it never resets, restores, or stashes the ticket worktree.  Each run writes a
self-contained generation under raw/<generation>/ with the shared
artifact-generation-manifest.json; only a passing full DoD+fast run advances the
current/supersedes chain.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SCHEMA = "agent-dev.verification/v2"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "verification.schema.json"
MANIFEST_NAME = "artifact-generation-manifest.json"
SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".kt", ".m", ".mm", ".php", ".py", ".rb", ".rs", ".scala",
    ".sh", ".sql", ".swift", ".ts", ".tsx", ".vue",
}
ASSERTION_RE = re.compile(
    r"AssertionError|AssertionError:|FAILED|FAIL:|assertion failed|expected.*actual|"
    r"assert\s|Expected| expectation |expect\(", re.I)


def _sibling(name: str):
    """Import only a known sibling contract module under ``python3 -I``."""
    directory = str(Path(__file__).resolve().parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module(name)


class VerificationError(ValueError):
    pass


def _get(value, *names, default=None):
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _git(repo: Path, *args: str, check=True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if check and result.returncode:
        raise VerificationError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result


def _head_tree(repo: Path):
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    tree = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    return head, tree


def _snapshot(repo: Path):
    head, tree = _head_tree(repo)
    tracked = _git(repo, "status", "--porcelain=v1", "--untracked-files=no").stdout
    untracked = [p for p in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0") if p]
    source = sorted(p for p in untracked if Path(p).suffix.lower() in SOURCE_SUFFIXES)
    return {"head": head, "tree": tree, "tracked_clean": not bool(tracked),
            "untracked_source": source}


def _load_shared(issue: Path, repo: Path):
    try:
        tc = _sibling("ticket_context")
        ac = _sibling("agents_config")
    except ImportError as exc:
        raise VerificationError("shared ticket_context/agents_config are required: %s" % exc)
    if not hasattr(tc, "parse_issue"):
        raise VerificationError("ticket_context.parse_issue(issue_path) is required")
    parsed = tc.parse_issue(issue)
    issue_id = _get(_get(parsed, "frontmatter", default={}), "issue-id")
    frozen = issue.parent.parent / "impl" / str(issue_id) / "ticket-context.json"
    if frozen.is_file() and hasattr(tc, "load_context"):
        context = tc.load_context(frozen)
    elif hasattr(tc, "context"):
        context = tc.context(issue, repo)
    elif hasattr(tc, "create_context"):
        context = tc.create_context(issue, repo)
    else:
        raise VerificationError("ticket_context.load_context/create_context is required")
    if context.get("dod") is not None and parsed.get("dod") != context.get("dod"):
        raise VerificationError("current ticket DoD differs from frozen ticket-context")
    root = _find_root(issue)
    config_path = _get(context, "config_path")
    if config_path:
        candidate = Path(config_path)
        config_path = candidate if candidate.is_absolute() else repo / candidate
        if not config_path.is_file():
            config_path = root / candidate
    else:
        config_path = root / "dev" / "agents-config.md"
    if hasattr(ac, "load_for_issue"):
        config = ac.load_for_issue(issue)
    elif hasattr(ac, "load"):
        config = ac.load(config_path)
    else:
        raise VerificationError("agents_config.load(config_path) is required")
    return parsed, context, config, root


def _find_root(issue: Path) -> Path:
    for parent in issue.resolve().parents:
        if (parent / "dev" / "agents-config.md").is_file():
            return parent
    raise VerificationError("cannot find dev/agents-config.md above %s" % issue)


def _issue_id(parsed, context, issue: Path) -> str:
    value = _get(context, "issue_id", "issue-id")
    if isinstance(value, dict):
        value = None
    value = value or _get(parsed, "issue_id", "issue-id")
    if not value:
        fm = _get(parsed, "frontmatter", "metadata", default={})
        value = _get(fm, "issue_id", "issue-id")
    if not value:
        raise VerificationError("ticket_context did not provide issue-id")
    return str(value)


def _base(context, repo: Path) -> str:
    diff = _get(context, "diff", "diff_context", default={})
    value = _get(context, "base", "base_commit") or _get(diff, "base", "base_commit")
    if not value or not re.fullmatch(r"[0-9a-f]{40}", str(value)):
        raise VerificationError("ticket_context.context must provide the exact 40-character base")
    return str(value)


def _impl_dir(context, issue: Path, issue_id: str) -> Path:
    value = _get(context, "impl_dir", "impl-dir")
    return Path(value).resolve() if value else (issue.parent.parent / "impl" / issue_id).resolve()


def _fast_command(config, ac=None) -> str:
    if ac is None:
        ac = _sibling("agents_config")
    value = None
    if hasattr(ac, "fast"):
        value = ac.fast(config)
    elif hasattr(ac, "quality_gate_fast"):
        value = ac.quality_gate_fast(config)
    else:
        value = _get(_get(config, "quality-gate", "quality_gate", default={}), "fast")
    if not isinstance(value, str) or not value.strip():
        raise VerificationError("quality-gate.fast is missing; verification is failed closed")
    if re.search(r"<[^>]+>", value):
        raise VerificationError("quality-gate.fast still contains a placeholder")
    return value.strip()


def _dod_items(parsed, context):
    value = _get(parsed, "dod", "dods", "definition_of_done")
    if value is None:
        value = _get(context, "dod", "dods", "definition_of_done")
    if not isinstance(value, (list, tuple)) or not value:
        raise VerificationError("ticket_context.parse_issue/context returned no structured DoD")
    tasks = []
    seen = set()
    for index, item in enumerate(value, 1):
        if isinstance(item, str):
            raise VerificationError("DoD %d is not structured by ticket_context (id/command/expected)" % index)
        ident = str(_get(item, "id", "dod_id", "dod-id", default="") or "").strip()
        command = _get(item, "command")
        if not ident or not isinstance(command, str) or not command.strip():
            raise VerificationError("DoD %d lacks a real id or command" % index)
        if ident in seen:
            raise VerificationError("duplicate DoD id: %s" % ident)
        seen.add(ident)
        expected = _get(item, "expected", default={})
        expected_exit = _get(item, "expected_exit", "expected-exit", "exit")
        if expected_exit is None:
            expected_exit = _get(expected, "exit", "exit_code", "exit-code", default=0)
        expected_output = _get(item, "expected_output", "expected-output", "output")
        if expected_output is None and isinstance(expected, str):
            expected_output = expected
        if expected_output is None:
            expected_output = _get(expected, "output", "contains", default="")
        if not isinstance(expected_exit, int) or isinstance(expected_exit, bool):
            raise VerificationError("%s expected exit must be an integer" % ident)
        categories = _get(item, "categories", "category", default=[])
        if isinstance(categories, str):
            categories = [categories]
        environment = _get(item, "requires_env", "required_env", "env", default=[])
        if isinstance(environment, dict):
            environment = list(environment)
        if isinstance(environment, str):
            environment = [environment]
        quarantine = _get(item, "quarantine", "quarantined", default=[])
        if isinstance(quarantine, str):
            quarantine = [quarantine]
        tasks.append({
            "id": ident, "kind": "dod", "command": command.strip(),
            "cwd": str(_get(item, "cwd", default=".")),
            "expected_exit": expected_exit, "expected_output": str(expected_output or ""),
            "categories": list(categories or []), "environment": list(environment or []),
            "quarantine": list(quarantine or []),
            "test_paths": list(_get(item, "test_paths", "test-paths", default=[]) or []),
        })
    return tasks


def _apply_categories(tasks, config, root: Path) -> None:
    """Classify parsed DoD/fast entries through the shared strict YAML parser."""
    policy = _get(config, "verify-policy", "verify_policy", default={}) or {}
    gate = _get(policy, "gate-scope", "gate_scope", default={}) or {}
    relative = _get(gate, "categories-file", "categories_file")
    if not relative:
        return
    path = root / str(relative)
    if not path.is_file():
        raise VerificationError("gate-scope.categories-file does not exist: %s" % relative)
    ac = _sibling("agents_config")
    try:
        data = ac.parse_yaml_subset(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise VerificationError("cannot parse categories-file: %s" % exc)
    entries = _get(data, "categories", "tests", default=data)
    if isinstance(entries, list):
        entries = {str(_get(item, "id", "name", "command", default="")): item for item in entries}
    if not isinstance(entries, dict):
        raise VerificationError("categories-file must contain a mapping or list of test entries")
    for task in tasks:
        entry = entries.get(task["id"]) or entries.get(task["command"])
        if entry is None:
            continue
        if not isinstance(entry, dict):
            entry = {"categories": [entry]}
        categories = _get(entry, "categories", "category", default=[])
        if isinstance(categories, str):
            categories = [categories]
        environment = _get(entry, "requires-env", "requires_env", "env", default=[])
        if isinstance(environment, str):
            environment = [environment]
        quarantine = _get(entry, "quarantine", "quarantined", default=[])
        if isinstance(quarantine, str):
            quarantine = [quarantine]
        task["categories"] = list(categories or [])
        task["environment"] = list(environment or [])
        task["quarantine"] = list(quarantine or [])


def verification_tasks(parsed, context, config, root: Path):
    """Return the frozen ordered DoD+fast contract used by runner and validators."""
    tasks = _dod_items(parsed, context)
    tasks.append({
        "id": "fast", "kind": "fast", "command": _fast_command(config),
        "cwd": ".", "expected_exit": 0, "expected_output": "",
        "categories": [], "environment": [], "quarantine": [], "test_paths": [],
    })
    _apply_categories(tasks, config, root)
    policy = _get(config, "verify-policy", "verify_policy", default={}) or {}
    gate = _get(policy, "gate-scope", "gate_scope", default={}) or {}
    non_gating = set(_get(gate, "non-gating", "non_gating", default=[]) or [])
    for task in tasks:
        task["non_gating"] = bool(non_gating.intersection(task.get("categories", [])))
        _reject_out_of_ticket_scope(task)
    return tasks


def _reject_out_of_ticket_scope(task) -> None:
    tier = str(_get(task, "tier", "scope", default="") or "").lower()
    command = task["command"]
    if tier in {"scoped", "full"} or re.search(r"(?:^|\s)--(?:tier|test-scope|scope)(?:=|\s+)(?:scoped|full)(?:\s|$)", command):
        raise VerificationError("%s attempts forbidden ticket-level scoped/full execution" % task["id"])
    if re.search(r"(?:^|\s)(?:--(?:scoped|full)|scoped|full)(?:\s|$)", command):
        raise VerificationError("%s must not select scoped/full suites" % task["id"])


def _environment(task):
    declared = task.get("environment", [])
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "variables": {name: os.environ.get(name, "") for name in declared},
    }


def _safe_cwd(repo: Path, relative: str) -> Path:
    cwd = (repo / relative).resolve() if not Path(relative).is_absolute() else Path(relative).resolve()
    try:
        cwd.relative_to(repo.resolve())
    except ValueError:
        raise VerificationError("command cwd escapes ticket worktree: %s" % relative)
    if not cwd.is_dir():
        raise VerificationError("command cwd does not exist: %s" % cwd)
    return cwd


def _artifact_path(impl_dir: Path, relative: str) -> Path:
    path = (impl_dir / relative).resolve()
    try:
        path.relative_to(impl_dir.resolve())
    except ValueError:
        raise VerificationError("raw artifact escapes impl directory: %s" % relative)
    return path


def _write_raw(impl_dir: Path, runner: str, ident: str, attempt: int, data: bytes, generation: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", ident).strip("-") or "command"
    relative = "raw/%s/%s/%s-r%d.log" % (generation, runner, safe, attempt)
    path = _artifact_path(impl_dir, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}


def _base_record(task, runner, head, tree, repo, attempt=1, generation=None):
    return {
        "id": task["id"], "kind": task["kind"], "command": task["command"],
        "cwd": str(_safe_cwd(repo, task.get("cwd", "."))),
        "expected_exit": task.get("expected_exit", 0),
        "expected_output": task.get("expected_output", ""), "actual_exit": None,
        "duration_seconds": 0.0, "started_at": _utc_now(), "ended_at": _utc_now(),
        "raw_log": {"path": "raw/%s/pending" % (generation or "legacy"), "sha256": ""}, "runner": runner,
        "head": head, "tree": tree, "environment": _environment(task),
        "started": False, "timed_out": False, "output_matched": False,
        "gating": True, "status": "fail", "pass": False, "attempt": attempt,
        "test_paths": list(task.get("test_paths", [])),
        "skip_proof": {"kind": "none", "items": []},
    }


def _finish_raw(record, impl_dir, data):
    record["raw_log"] = _write_raw(impl_dir, record["runner"], record["id"], record["attempt"], data,
                                   _record_generation(record))


def _output_matches(expected: str, output: str) -> bool:
    return not expected or expected in output


def _quarantine_names(config, root: Path):
    ac = _sibling("agents_config")
    if hasattr(ac, "quarantine"):
        entries = ac.quarantine(config, root)
    elif hasattr(ac, "quarantine_entries"):
        entries = ac.quarantine_entries(config, root)
    elif hasattr(ac, "parse_yaml_subset") and (root / "dev" / "known-failures.yaml").is_file():
        try:
            data = ac.parse_yaml_subset((root / "dev" / "known-failures.yaml").read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise VerificationError("cannot parse quarantine list: %s" % exc)
        entries = _get(data, "failures", default=[])
    else:
        raise VerificationError("quarantined DoD requires dev/known-failures.yaml or agents_config.quarantine")
    result = {}
    for entry in entries or []:
        name = _get(entry, "name")
        if (not name or not _get(entry, "signature") or not _get(entry, "owner")
                or not _get(entry, "destination") or not _get(entry, "first-seen", "first_seen")):
            raise VerificationError("quarantine entry lacks name/signature/owner/destination/first-seen")
        result[str(name)] = entry
    return result


def _execute(task, *, runner, repo, impl_dir, head, tree, timeout, config, root,
             attempt=1, red=False, generation=None):
    record = _base_record(task, runner, head, tree, repo, attempt, generation)
    policy = _get(config, "verify-policy", "verify_policy", default={}) or {}
    gate = _get(policy, "gate-scope", "gate_scope", default={}) or {}
    non_gating = set(_get(gate, "non-gating", "non_gating", default=[]) or [])
    if non_gating.intersection(task.get("categories", [])):
        record["gating"] = False
    missing = [name for name in task.get("environment", []) if not os.environ.get(name)]
    if missing and _get(gate, "env-skip", "env_skip", default=False):
        record["status"] = "env-skip"
        record["gating"] = False
        record["skip_proof"] = {"kind": "env-skip", "items": sorted(missing)}
        _finish_raw(record, impl_dir, ("ENV_SKIP missing=" + ",".join(sorted(missing)) + "\n").encode())
        return record
    quarantined = list(task.get("quarantine", []))
    if quarantined:
        known = _quarantine_names(config, root)
        unknown = sorted(set(quarantined) - set(known))
        if unknown:
            _finish_raw(record, impl_dir, ("quarantine entries not configured: " + ",".join(unknown)).encode())
            return record
        record["skip_proof"] = {"kind": "quarantine", "items": sorted(quarantined)}
    started = time.monotonic()
    record["started_at"] = _utc_now()
    output = b""
    try:
        process = subprocess.Popen(
            record["command"], cwd=record["cwd"], shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )
        record["started"] = True
        try:
            output, _ = process.communicate(timeout=timeout)
            record["actual_exit"] = process.returncode
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
            record["actual_exit"] = 124
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            output, _ = process.communicate()
            output += b"\nVERIFY_TIMEOUT\n"
    except OSError as exc:
        output = ("VERIFY_NOT_STARTED: %s\n" % exc).encode()
    record["ended_at"] = _utc_now()
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    text = output.decode("utf-8", errors="replace")
    record["output_matched"] = _output_matches(record["expected_output"], text)
    _finish_raw(record, impl_dir, output)
    if red or record["expected_exit"] == "nonzero":
        assertion = bool(ASSERTION_RE.search(text))
        record["pass"] = (record["started"] and not record["timed_out"]
                          and record["actual_exit"] not in (None, 0)
                          and record["output_matched"] and assertion)
    else:
        record["pass"] = (record["started"] and not record["timed_out"]
                          and record["actual_exit"] == record["expected_exit"]
                          and record["output_matched"])
    if quarantined:
        proof = all(any(name in line and re.search(r"(?i)(?:skip|quarantin)", line)
                        for line in text.splitlines()) for name in quarantined)
        record["pass"] = bool(record["pass"] and proof)
        record["status"] = "quarantined" if record["pass"] else "fail"
    elif not record["gating"] and record["status"] != "env-skip":
        record["status"] = "non-gating"
    else:
        record["status"] = "pass" if record["pass"] else "fail"
    return record


def _invariants_hold(before, after) -> bool:
    return (before["tracked_clean"] and after["tracked_clean"]
            and not before["untracked_source"] and not after["untracked_source"]
            and before["head"] == after["head"] and before["tree"] == after["tree"])


def _compute_pass(document) -> bool:
    if not _invariants_hold(document["before"], document["after"]):
        return False
    records = document.get("records", [])
    if not records:
        return False
    for record in records:
        if record.get("status") in {"env-skip", "non-gating", "load-flake"}:
            continue
        if not record.get("gating", True):
            continue
        if (record.get("status") == "quarantined" or record.get("gating", True)
                or record.get("skip_proof", {}).get("kind") == "quarantine"):
            if not record.get("pass") is True:
                return False
    for record in document.get("red_records", []):
        if not record.get("pass") is True:
            return False
    bug = document.get("bug_regression")
    if bug is not None and bug.get("pass") is not True:
        return False
    return True


def _counts(document):
    records = document.get("records", [])
    return {
        "total": len(records),
        "passed": sum(r.get("pass") is True for r in records),
        "failed": sum((not r.get("pass") and r.get("status") not in {"env-skip", "non-gating", "load-flake"}) for r in records),
        "non_gating": sum(r.get("status") == "non-gating" for r in records),
        "env_skip": sum(r.get("status") == "env-skip" for r in records),
        "quarantined": sum(r.get("status") == "quarantined" for r in records),
        "load_flake": sum(r.get("status") == "load-flake" for r in records),
    }


def _identity(args, runner):
    source = {}
    if getattr(args, "identity_json", None):
        source = json.loads(Path(args.identity_json).read_text(encoding="utf-8"))
    role = "development" if runner == "implementer" else "supervisor"
    result = {
        "host": getattr(args, "host", None) or source.get("host") or os.environ.get("AGENT_HOST") or "unspecified",
        "agent": getattr(args, "agent", None) or source.get("agent") or os.environ.get("AGENT_NAME") or runner,
        "session": getattr(args, "session", None) or source.get("session") or os.environ.get("AGENT_SESSION") or "unspecified",
        "model": getattr(args, "model", None) or source.get("model") or os.environ.get("AGENT_MODEL") or "unspecified",
        "effort": getattr(args, "effort", None) or source.get("effort") or os.environ.get("AGENT_EFFORT") or "unspecified",
        "role": role,
    }
    tier = getattr(args, "tier", None) or source.get("tier")
    if runner == "implementer":
        result["tier"] = tier or "default"
    return result


def generation_of(document) -> str:
    """Read generation from the schema-stable manifest pointer."""
    parts = Path(document.get("manifest", "")).parts
    if len(parts) != 3 or parts[0] != "raw" or parts[2] != MANIFEST_NAME or not parts[1]:
        raise VerificationError("invalid generation manifest path: %s" % document.get("manifest"))
    return parts[1]


def _record_generation(record) -> str:
    parts = Path((record.get("raw_log") or {}).get("path", "")).parts
    if len(parts) >= 2 and parts[0] == "raw" and parts[1]:
        return parts[1]
    return "legacy"


def _new_document(issue, runner, base, before, identity):
    generation = "%s-%s-%s" % (before["head"][:12], runner, uuid.uuid4().hex[:16])
    return {
        "schema": SCHEMA, "issue": issue, "runner": runner, "base": base,
        "head": before["head"], "tree": before["tree"], "before": before,
        "after": dict(before), "records": [], "red_records": [],
        "bug_regression": None, "pass": False,
        "counts": {"total": 0, "passed": 0, "failed": 0, "non_gating": 0,
                   "env_skip": 0, "quarantined": 0, "load_flake": 0},
        "identity": identity, "generated_at": _utc_now(),
        "manifest": "raw/%s/%s" % (generation, MANIFEST_NAME),
    }


def _load_red_records(path: Path):
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return value.get("red_records") or value.get("records") or []


def _load_bug(path: Path):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value.get("bug_regression")
    except (OSError, ValueError):
        return None


def run_verification(args) -> dict:
    issue = Path(args.issue).resolve()
    repo = Path(args.repo).resolve()
    parsed, context, config, root = _load_shared(issue, repo)
    issue_id = _issue_id(parsed, context, issue)
    impl_dir = Path(args.output_dir).resolve() if args.output_dir else _impl_dir(context, issue, issue_id)
    tasks = verification_tasks(parsed, context, config, root)
    before = _snapshot(repo)
    document = _new_document(issue_id, args.runner, _base(context, repo), before, _identity(args, args.runner))
    document["red_records"] = []
    auxiliary_documents = {}
    red_path = impl_dir / "verification-red.json"
    if red_path.is_file():
        red_document = _rehome_document(json.loads(red_path.read_text(encoding="utf-8")),
                                        impl_dir, generation_of(document))
        _write_json(red_path, red_document)
        document["red_records"] = red_document.get("red_records", [])
        auxiliary_documents["verification-red.json"] = red_document
    bug_path = impl_dir / "bug-regression.json"
    document["bug_regression"] = None
    if bug_path.is_file():
        bug_document = _rehome_document(json.loads(bug_path.read_text(encoding="utf-8")),
                                        impl_dir, generation_of(document))
        _write_json(bug_path, bug_document)
        document["bug_regression"] = bug_document.get("bug_regression")
        auxiliary_documents["bug-regression.json"] = bug_document
    if before["tracked_clean"] and not before["untracked_source"]:
        for task in tasks:
            record = _execute(task, runner=args.runner, repo=repo, impl_dir=impl_dir,
                              head=before["head"], tree=before["tree"], timeout=args.timeout,
                              config=config, root=root, generation=generation_of(document))
            document["records"].append(record)
            if record["timed_out"] and record["kind"] in {"dod", "fast"}:
                try:
                    loaded = os.getloadavg()[0] > (os.cpu_count() or 1)
                except (AttributeError, OSError):
                    loaded = False
                if loaded:
                    retry_task = dict(task, id=task["id"] + ":load-flake-retry")
                    retry = _execute(retry_task, runner=args.runner, repo=repo, impl_dir=impl_dir,
                                     head=before["head"], tree=before["tree"], timeout=args.timeout,
                                     config=config, root=root, attempt=2,
                                     generation=generation_of(document))
                    retry["retry_of"] = task["id"]
                    document["records"].append(retry)
                    if retry["pass"]:
                        record["status"] = "load-flake"
                        record["gating"] = False
    else:
        for task in tasks:
            record = _base_record(task, args.runner, before["head"], before["tree"], repo,
                                  generation=generation_of(document))
            _finish_raw(record, impl_dir, b"VERIFY_NOT_STARTED: dirty worktree or untracked source\n")
            document["records"].append(record)
    document["after"] = _snapshot(repo)
    document["head"] = before["head"]
    document["tree"] = before["tree"]
    document["counts"] = _counts(document)
    document["pass"] = _compute_pass(document)
    document["generated_at"] = _utc_now()
    output = impl_dir / ("verification-%s.json" % args.runner)
    _write_json(output, document)
    write_evidence_and_manifest(impl_dir, issue_id, primary=document,
                                auxiliary_documents=auxiliary_documents)
    return document


def red_verification(args) -> dict:
    issue = Path(args.issue).resolve()
    repo = Path(args.repo).resolve()
    parsed, context, config, root = _load_shared(issue, repo)
    issue_id = _issue_id(parsed, context, issue)
    impl_dir = Path(args.output_dir).resolve() if args.output_dir else _impl_dir(context, issue, issue_id)
    before = _snapshot(repo)
    document = _new_document(issue_id, "implementer", _base(context, repo), before,
                             _identity(args, "implementer"))
    task = {"id": args.id, "kind": "red", "command": args.command, "cwd": args.cwd,
            "expected_exit": "nonzero", "expected_output": args.expected_output or "",
            "categories": [], "environment": [], "quarantine": [],
            "test_paths": list(args.test_path)}
    _reject_out_of_ticket_scope(task)
    if before["tracked_clean"] and not before["untracked_source"]:
        record = _execute(task, runner="implementer", repo=repo, impl_dir=impl_dir,
                          head=before["head"], tree=before["tree"], timeout=args.timeout,
                          config=config, root=root, red=True, generation=generation_of(document))
    else:
        record = _base_record(task, "implementer", before["head"], before["tree"], repo,
                              generation=generation_of(document))
        _finish_raw(record, impl_dir, b"VERIFY_NOT_STARTED: dirty worktree or untracked source\n")
    document["records"] = [record]
    red_path = impl_dir / "verification-red.json"
    previous_red = []
    if red_path.is_file():
        previous_document = _rehome_document(json.loads(red_path.read_text(encoding="utf-8")),
                                             impl_dir, generation_of(document))
        previous_red = previous_document.get("red_records", [])
    document["red_records"] = [r for r in previous_red if r.get("id") != record["id"]] + [record]
    document["after"] = _snapshot(repo)
    document["counts"] = _counts(document)
    document["pass"] = _compute_pass(document)
    document["generated_at"] = _utc_now()
    _write_json(red_path, document)
    write_evidence_and_manifest(impl_dir, issue_id, primary=document,
                                advance=False, document_name="verification-red.json")
    return document


def bug_regression(args) -> dict:
    issue = Path(args.issue).resolve()
    repo = Path(args.repo).resolve()
    parsed, context, config, root = _load_shared(issue, repo)
    issue_id = _issue_id(parsed, context, issue)
    if _get(parsed, "type") != "bug":
        fm = _get(parsed, "frontmatter", "metadata", default={})
        if _get(fm, "type") != "bug":
            raise VerificationError("bug-regression is only valid for type: bug")
    impl_dir = Path(args.output_dir).resolve() if args.output_dir else _impl_dir(context, issue, issue_id)
    before = _snapshot(repo)
    if not before["tracked_clean"] or before["untracked_source"]:
        raise VerificationError("bug-regression requires a clean ticket worktree")
    document = _new_document(issue_id, args.runner, _base(context, repo), before,
                             _identity(args, args.runner))
    worktrees = tempfile.TemporaryDirectory(prefix="verify-bug-")
    temporary = Path(worktrees.name) / "worktree"
    try:
        _git(repo, "worktree", "add", "--detach", str(temporary), before["head"])
        revert_records = []
        for index, command in enumerate(args.revert_command, 1):
            task = {"id": "bug-revert-%d" % index, "kind": "bug-revert", "command": command,
                    "cwd": ".", "expected_exit": 0, "expected_output": "", "environment": [],
                    "categories": [], "quarantine": [], "test_paths": []}
            revert_records.append(_execute(task, runner=args.runner, repo=temporary, impl_dir=impl_dir,
                                           head=before["head"], tree=before["tree"], timeout=args.timeout,
                                           config=config, root=root, generation=generation_of(document)))
        changed = [p for p in _git(temporary, "diff", "--name-only", "-z").stdout.split("\0") if p]
        allowed = set(args.fix_path)
        scope_ok = bool(changed) and set(changed) <= allowed
        if not scope_ok and revert_records:
            revert_records[-1]["pass"] = False
            revert_records[-1]["status"] = "fail"
            extra = ("REVERT_SCOPE_FAIL changed=%s allowed=%s\n" % (changed, sorted(allowed))).encode()
            _finish_raw(revert_records[-1], impl_dir, extra)
        test_task = {"id": "bug-red", "kind": "bug-red", "command": args.command, "cwd": args.cwd,
                     "expected_exit": "nonzero", "expected_output": args.expected_output or "",
                     "environment": [], "categories": [], "quarantine": [], "test_paths": list(args.test_path or [])}
        red = _execute(test_task, runner=args.runner, repo=temporary, impl_dir=impl_dir,
                       head=before["head"], tree=before["tree"], timeout=args.timeout,
                       config=config, root=root, red=True, generation=generation_of(document))
        red["pass"] = bool(red["pass"] and scope_ok and all(r["pass"] for r in revert_records))
        if not red["pass"]:
            red["status"] = "fail"
        restore_records = []
        for index, command in enumerate(args.restore_command, 1):
            task = {"id": "bug-restore-%d" % index, "kind": "bug-restore", "command": command,
                    "cwd": ".", "expected_exit": 0, "expected_output": "", "environment": [],
                    "categories": [], "quarantine": [], "test_paths": []}
            restore_records.append(_execute(task, runner=args.runner, repo=temporary, impl_dir=impl_dir,
                                            head=before["head"], tree=before["tree"], timeout=args.timeout,
                                            config=config, root=root, generation=generation_of(document)))
        restored = _snapshot(temporary)
        restore_ok = (restored["tracked_clean"] and not restored["untracked_source"]
                      and restored["head"] == before["head"] and restored["tree"] == before["tree"])
        if not restore_ok and restore_records:
            restore_records[-1]["pass"] = False
            restore_records[-1]["status"] = "fail"
        green_task = dict(test_task, id="bug-green", kind="bug-green", expected_exit=0,
                          expected_output="")
        green = _execute(green_task, runner=args.runner, repo=temporary, impl_dir=impl_dir,
                         head=before["head"], tree=before["tree"], timeout=args.timeout,
                         config=config, root=root, generation=generation_of(document))
        green["pass"] = bool(green["pass"] and restore_ok and all(r["pass"] for r in restore_records))
        if not green["pass"]:
            green["status"] = "fail"
        bug_pass = bool(red["pass"] and green["pass"] and scope_ok and restore_ok
                        and all(r["pass"] for r in revert_records + restore_records))
        document["bug_regression"] = {"pass": bug_pass, "revert": revert_records,
                                      "red": red, "restore": restore_records, "green": green}
        document["records"] = revert_records + [red] + restore_records + [green]
    finally:
        if temporary.exists():
            _git(repo, "worktree", "remove", "--force", str(temporary), check=False)
        worktrees.cleanup()
    document["after"] = _snapshot(repo)
    document["counts"] = _counts(document)
    document["pass"] = _compute_pass(document)
    document["generated_at"] = _utc_now()
    _write_json(impl_dir / "bug-regression.json", document)
    write_evidence_and_manifest(impl_dir, issue_id, primary=document,
                                advance=False, document_name="bug-regression.json")
    return document


def _md(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_evidence(document, issue_id=None) -> str:
    issue_id = issue_id or document["issue"]
    lines = [
        "# Evidence：%s" % issue_id, "",
        "> Generated deterministically by verify_issue.py; verification JSON and hashed raw logs are authoritative.",
        "", "- issue: `%s`" % issue_id,
        "- runner: `%s`" % document["runner"],
        "- head: `%s`" % document["head"], "- tree: `%s`" % document["tree"],
        "- pass: `%s`" % str(document["pass"]).lower(),
        "- verification: `verification-%s.json`" % document["runner"],
        "- artifact manifest: `%s`" % MANIFEST_NAME, "",
        "## 验收命令执行记录", "",
        "| ID | 类型 | 命令 | 预期退出 | 实际退出 | 输出匹配 | 状态 | raw log sha256 |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for record in document.get("records", []):
        lines.append("| %s | %s | `%s` | %s | %s | %s | %s | `%s` |" % (
            _md(record["id"]), record["kind"], _md(record["command"]), record["expected_exit"],
            record["actual_exit"], str(record["output_matched"]).lower(), record["status"],
            record["raw_log"]["sha256"]))
    lines.extend(["", "## fast 质量门记录", ""])
    fast = [r for r in document.get("records", []) if r["kind"] == "fast"]
    lines.extend(("fast: `%s`" % fast[0]["status"],) if fast else ("fast: missing",))
    lines.extend(["", "## TDD 见红记录", ""])
    if document.get("red_records"):
        for record in document["red_records"]:
            lines.append("- `%s` tests=%s exit=%s raw=`%s` sha256=`%s`" % (
                record["id"], ",".join(record.get("test_paths", [])), record["actual_exit"],
                record["raw_log"]["path"], record["raw_log"]["sha256"]))
    else:
        lines.append("无")
    lines.extend(["", "## 回归有效性（结构化 bug 验证）", ""])
    bug = document.get("bug_regression")
    if bug:
        lines.append("- pass: `%s`；revert/red/restore/green 记录见 `bug-regression.json`" % str(bug["pass"]).lower())
    else:
        lines.append("无")
    lines.extend(["", "## 人工确认项（non-gating / env-skip）", ""])
    special = [r for r in document.get("records", []) if r["status"] in {"non-gating", "env-skip", "quarantined", "load-flake"}]
    if special:
        for record in special:
            lines.append("- `%s`: %s；proof=%s" % (record["id"], record["status"], json.dumps(record["skip_proof"], ensure_ascii=False, sort_keys=True)))
    else:
        lines.append("无")
    return "\n".join(lines) + "\n"


def _record_slots(document):
    for key in ("records", "red_records"):
        for record in document.get(key, []):
            yield record
    bug = document.get("bug_regression") or {}
    for key in ("revert", "restore"):
        for record in bug.get(key, []):
            yield record
    for key in ("red", "green"):
        if bug.get(key):
            yield bug[key]


def _rehome_document(document, impl_dir: Path, generation: str):
    """Copy historical evidence into a new self-contained generation."""
    document = copy.deepcopy(document)
    for record in _record_slots(document):
        raw = record.get("raw_log") or {}
        relative = raw.get("path")
        if not relative or relative.startswith("raw/%s/" % generation):
            continue
        source = _artifact_path(impl_dir, relative)
        if not source.is_file() or source.is_symlink():
            raise VerificationError("cannot rehome missing/symlink raw evidence: %s" % relative)
        old_generation = _record_generation(record)
        name = "%s-%s-%s" % (old_generation, record.get("runner", "runner"), source.name)
        destination_relative = "raw/%s/history/%s" % (generation, name)
        destination = _artifact_path(impl_dir, destination_relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        destination.write_bytes(data)
        raw["path"] = destination_relative
        raw["sha256"] = hashlib.sha256(data).hexdigest()
    document["manifest"] = "raw/%s/%s" % (generation, MANIFEST_NAME)
    return document


def _current_generation(impl_dir: Path, issue_id: str):
    matches = []
    raw = impl_dir / "raw"
    if not raw.is_dir():
        return None
    for path in sorted(raw.glob("*/" + MANIFEST_NAME)):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("subject") == issue_id and value.get("status") == "current":
            matches.append((path, value))
    if len(matches) > 1:
        raise VerificationError("multiple current artifact generations for %s" % issue_id)
    return matches[0] if matches else None


def _write_generation_manifest(impl_dir: Path, issue_id: str, document, documents,
                               status: str, supersedes=None, evidence=False):
    generation_dir = _artifact_path(impl_dir, "raw/" + generation_of(document))
    generation_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, value in documents.items():
        archived = generation_dir / name
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(_json_bytes(value))
        files[name] = "verification"
    if evidence:
        name = "evidence-%s.md" % issue_id
        source = impl_dir / name
        archived = generation_dir / name
        archived.write_bytes(source.read_bytes())
        files[name] = "evidence"
    for value in documents.values():
        for record in _record_slots(value):
            raw = record.get("raw_log") or {}
            relative = raw.get("path")
            if not relative:
                continue
            source = _artifact_path(impl_dir, relative)
            try:
                archived_relative = source.relative_to(generation_dir).as_posix()
            except ValueError:
                raise VerificationError("raw evidence is not self-contained in generation: %s" % relative)
            if not source.is_file() or source.is_symlink():
                raise VerificationError("raw evidence missing/symlink: %s" % relative)
            files[archived_relative] = "raw"
    artifacts = []
    for relative in sorted(files):
        path = generation_dir / relative
        artifacts.append({"path": relative, "kind": files[relative],
                          "sha256": _sha(path), "size": path.stat().st_size})
    manifest = {
        "schema": "agent-dev.artifact-generation/v2", "subject": issue_id,
        "generation": generation_of(document), "candidate_tree": document["tree"],
        "status": status, "created_at": document["generated_at"], "artifacts": artifacts,
    }
    if supersedes:
        manifest["supersedes"] = supersedes
    _write_json(generation_dir / MANIFEST_NAME, manifest)
    return generation_dir / MANIFEST_NAME


def write_evidence_and_manifest(impl_dir: Path, issue_id: str, primary=None,
                                auxiliary_documents=None, advance=None, document_name=None):
    auxiliary_documents = dict(auxiliary_documents or {})
    view = primary
    if view is None:
        for name in ("verification-supervisor.json", "verification-implementer.json"):
            path = impl_dir / name
            if path.is_file():
                view = json.loads(path.read_text(encoding="utf-8"))
                break
    is_full_run = bool(primary and any(record.get("kind") == "fast" for record in primary.get("records", [])))
    if advance is None:
        advance = is_full_run and primary.get("pass") is True
    if is_full_run:
        (impl_dir / ("evidence-%s.md" % issue_id)).write_text(
            render_evidence(primary, issue_id), encoding="utf-8")
    if primary is None:
        # Auxiliary red/bug generation: archive only its own evidence bundle.
        return impl_dir / "raw"
    previous = _current_generation(impl_dir, issue_id) if advance else None
    documents = {document_name or ("verification-%s.json" % primary["runner"]): primary}
    documents.update(auxiliary_documents)
    status = "current" if advance else "superseded"
    supersedes = previous[1]["generation"] if previous else None
    manifest = _write_generation_manifest(
        impl_dir, issue_id, primary, documents, status, supersedes, evidence=is_full_run)
    if advance and previous:
        previous_path, previous_manifest = previous
        previous_manifest["status"] = "superseded"
        _write_json(previous_path, previous_manifest)
    return manifest


def _resolve_raw(document_path: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise VerificationError("raw log path must be relative and non-empty")
    path = (document_path.parent / relative).resolve()
    try:
        path.relative_to(document_path.parent.resolve())
    except ValueError:
        raise VerificationError("raw log path escapes impl directory")
    return path


def record_semantic_errors(record, raw_bytes: bytes):
    """Recompute one record from its actual exit and immutable raw output."""
    errors = []
    text = raw_bytes.decode("utf-8", errors="replace")
    if record["status"] == "env-skip":
        if record["started"] or record["pass"] or record["skip_proof"]["kind"] != "env-skip":
            errors.append("%s invalid env-skip semantics" % record["id"])
        for name in record["skip_proof"]["items"]:
            if name not in text:
                errors.append("%s env-skip proof lacks %s" % (record["id"], name))
        return errors
    observed_match = bool(record["started"] and _output_matches(record["expected_output"], text))
    if record["output_matched"] != observed_match:
        errors.append("%s output_matched differs from raw log" % record["id"])
    if record["expected_exit"] == "nonzero":
        expected_pass = (record["started"] and not record["timed_out"]
                         and record["actual_exit"] not in (None, 0) and observed_match)
        if record["kind"] in {"red", "bug-red"}:
            expected_pass = bool(expected_pass and ASSERTION_RE.search(text))
    else:
        expected_pass = (record["started"] and not record["timed_out"]
                         and record["actual_exit"] == record["expected_exit"] and observed_match)
    if record["skip_proof"]["kind"] == "quarantine":
        proof = all(any(name in line and re.search(r"(?i)(?:skip|quarantin)", line)
                        for line in text.splitlines())
                    for name in record["skip_proof"]["items"])
        expected_pass = bool(expected_pass and proof)
    if record["pass"] != expected_pass:
        errors.append("%s pass bit differs from actual exit/output/log" % record["id"])
    return errors


def _expected_tasks_for_document(document_path: Path):
    frozen = document_path.parent / "ticket-context.json"
    tc = _sibling("ticket_context")
    ac = _sibling("agents_config")
    context = tc.load_context(frozen)
    root = _find_root(frozen)
    issue = (root / context["issue_path"]).resolve()
    parsed = tc.parse_issue(issue)
    config = ac.load(root / context["config_path"])
    return verification_tasks(parsed, context, config, root)


def _task_shape_errors(record, task, repo: Path, expected_id=None):
    errors = []
    expected_id = expected_id or task["id"]
    expected = {
        "id": expected_id, "kind": task["kind"], "command": task["command"],
        "expected_exit": task["expected_exit"], "expected_output": task["expected_output"],
    }
    for key, value in expected.items():
        if record.get(key) != value:
            errors.append("%s %s differs from frozen DoD/fast contract" % (task["id"], key))
    try:
        expected_cwd = _safe_cwd(repo, task.get("cwd", "."))
        if Path(record.get("cwd", "")).resolve() != expected_cwd:
            errors.append("%s cwd differs from frozen DoD/fast contract" % task["id"])
    except VerificationError as exc:
        errors.append(str(exc))
    if record.get("status") in {"non-gating", "env-skip"} and record.get("gating"):
        errors.append("%s non-gating/env-skip record cannot remain gating" % task["id"])
    if (record.get("status") not in {"non-gating", "env-skip", "load-flake"}
            and not task.get("non_gating") and not record.get("gating")):
        errors.append("%s was incorrectly removed from gating" % task["id"])
    if record.get("status") == "non-gating" and not task.get("non_gating"):
        errors.append("%s was incorrectly marked non-gating" % task["id"])
    if task.get("non_gating") and record.get("status") not in {"non-gating", "env-skip", "load-flake"}:
        errors.append("%s lost its configured non-gating classification" % task["id"])
    if record.get("status") == "env-skip":
        if not task.get("environment") or record.get("skip_proof", {}).get("kind") != "env-skip":
            errors.append("%s has no configured env-skip basis" % task["id"])
    if record.get("status") == "quarantined":
        configured = sorted(task.get("quarantine", []))
        if not configured or record.get("skip_proof", {}).get("items") != configured:
            errors.append("%s quarantine differs from configured entries" % task["id"])
    return errors


def _task_sequence_errors(records, tasks, repo: Path):
    errors = []
    index = 0
    for task in tasks:
        if index >= len(records):
            errors.append("missing execution for %s" % task["id"])
            break
        record = records[index]
        index += 1
        if record.get("retry_of"):
            errors.append("%s is an unexpected replacement retry" % task["id"])
        if record.get("attempt") != 1:
            errors.append("%s first attempt must be attempt 1" % task["id"])
        errors.extend(_task_shape_errors(record, task, repo))
        if index < len(records) and records[index].get("retry_of") == task["id"]:
            retry = records[index]
            index += 1
            if not record.get("timed_out") or record.get("status") != "load-flake" or record.get("gating"):
                errors.append("%s retry lacks an original timed-out load-flake record" % task["id"])
            if retry.get("attempt") != 2:
                errors.append("%s load-flake retry must be attempt 2" % task["id"])
            errors.extend(_task_shape_errors(retry, task, repo, task["id"] + ":load-flake-retry"))
        elif record.get("status") == "load-flake":
            errors.append("%s load-flake has no bound retry" % task["id"])
    if index != len(records):
        errors.append("extra gating/unbound execution records are forbidden: %s" %
                      ", ".join(str(record.get("id")) for record in records[index:]))
    return errors


def validate_verification(document, document_path: Path, repo: Path,
                          expected_runner=None, expected_issue=None, require_fast=True,
                          expected_tasks=None):
    """Return validation errors; never trust the supplied top-level pass bit."""
    repo = Path(repo).resolve()
    document_path = Path(document_path).resolve()
    errors = []
    try:
        js = _sibling("json_schema")
        errors.extend(js.load_validate(SCHEMA_PATH, document))
    except (ImportError, ValueError, OSError) as exc:
        return ["verification schema validation unavailable: %s" % exc]
    if errors:
        return errors
    if expected_runner and document["runner"] != expected_runner:
        errors.append("runner must be %s" % expected_runner)
    if expected_issue and document["issue"] != expected_issue:
        errors.append("issue mismatch")
    try:
        head, tree = _head_tree(repo)
    except (VerificationError, OSError) as exc:
        return errors + [str(exc)]
    if document["head"] != head or document["tree"] != tree:
        errors.append("verification head/tree is stale")
    for side in ("before", "after"):
        snap = document[side]
        if snap["head"] != document["head"] or snap["tree"] != document["tree"]:
            errors.append("%s snapshot head/tree mismatch" % side)
        if not snap["tracked_clean"] or snap["untracked_source"]:
            errors.append("%s snapshot is not clean" % side)
    if require_fast:
        try:
            tasks = expected_tasks or _expected_tasks_for_document(document_path)
            errors.extend(_task_sequence_errors(document["records"], tasks, repo))
        except (VerificationError, ImportError, ValueError, OSError) as exc:
            errors.append("cannot reconcile frozen DoD/fast commands: %s" % exc)
    if require_fast and not any(r["kind"] == "fast" for r in document["records"]):
        errors.append("fast execution record is missing")
    ids = set()
    document_generation = None
    if require_fast:
        try:
            document_generation = generation_of(document)
        except VerificationError as exc:
            errors.append(str(exc))
    for record in document["records"]:
        if record["id"] in ids:
            errors.append("duplicate record id: %s" % record["id"])
        ids.add(record["id"])
        if record["head"] != document["head"] or record["tree"] != document["tree"]:
            errors.append("%s record head/tree mismatch" % record["id"])
        if record["runner"] != document["runner"]:
            errors.append("%s runner mismatch" % record["id"])
        if require_fast and document_generation and _record_generation(record) != document_generation:
            errors.append("%s generation differs from current verification generation" % record["id"])
        try:
            raw = _resolve_raw(document_path, record["raw_log"]["path"])
            if raw.is_symlink() or not raw.is_file() or _sha(raw) != record["raw_log"]["sha256"]:
                errors.append("%s raw log missing/symlink/hash mismatch" % record["id"])
            else:
                errors.extend(record_semantic_errors(record, raw.read_bytes()))
        except (VerificationError, OSError) as exc:
            errors.append("%s raw log invalid: %s" % (record["id"], exc))
        if record["started"] and record["actual_exit"] is None:
            errors.append("%s missing actual exit" % record["id"])
        if (record["expected_output"] and not record["output_matched"]
                and record["status"] not in {"non-gating", "load-flake"}):
            errors.append("%s expected output mismatch" % record["id"])
        if record["timed_out"] and record["status"] not in {"non-gating", "load-flake"}:
            errors.append("%s timed out" % record["id"])
        if not record["started"] and record["status"] not in {"env-skip"}:
            errors.append("%s command did not start" % record["id"])
    computed = _compute_pass(document)
    if document["pass"] != computed:
        errors.append("pass bit does not match runner records")
    if not computed:
        errors.append("verification did not pass")
    return errors


def _add_identity_arguments(parser):
    parser.add_argument("--identity-json")
    parser.add_argument("--host")
    parser.add_argument("--agent")
    parser.add_argument("--session")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--tier", choices=("default", "escalated"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command_name", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--issue", required=True)
    common.add_argument("--repo", required=True)
    common.add_argument("--output-dir")
    common.add_argument("--timeout", type=float, default=120.0)
    _add_identity_arguments(common)

    run_p = sub.add_parser("run", parents=[common])
    run_p.add_argument("runner", choices=("implementer", "supervisor"))

    red_p = sub.add_parser("red", parents=[common])
    red_p.add_argument("--id", required=True)
    red_p.add_argument("--command", required=True)
    red_p.add_argument("--cwd", default=".")
    red_p.add_argument("--expected-output")
    red_p.add_argument("--test-path", action="append", required=True)

    bug_p = sub.add_parser("bug-regression", parents=[common])
    bug_p.add_argument("runner", choices=("implementer", "supervisor"))
    bug_p.add_argument("--command", required=True)
    bug_p.add_argument("--cwd", default=".")
    bug_p.add_argument("--expected-output")
    bug_p.add_argument("--test-path", action="append")
    bug_p.add_argument("--fix-path", action="append", required=True)
    bug_p.add_argument("--revert-command", action="append", required=True)
    bug_p.add_argument("--restore-command", action="append", required=True)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        if args.command_name == "run":
            document = run_verification(args)
        elif args.command_name == "red":
            document = red_verification(args)
        else:
            document = bug_regression(args)
    except (VerificationError, ImportError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("verify-issue: %s" % exc, file=sys.stderr)
        return 2
    print("verify-issue %s %s head=%s pass=%s" % (
        args.command_name, document["issue"], document["head"], str(document["pass"]).lower()))
    for record in document.get("records", []):
        print("  [%s] %s exit=%s raw=%s" % (
            record["status"].upper(), record["id"], record["actual_exit"], record["raw_log"]["path"]))
    return 0 if document["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
