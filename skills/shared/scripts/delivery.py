#!/usr/bin/env python3
"""Context-only ticket delivery and feature-candidate promotion.

Ticket CLI:
    start --issue ISSUE --repo MAIN_REPO
    rebase --context TICKET_CONTEXT
    merge --context TICKET_CONTEXT
    reconcile --context TICKET_CONTEXT

Feature CLI:
    promote --candidate CANDIDATE_JSON --candidate-repo INTEGRATION_WORKTREE
            --repo MAIN_WORKTREE [--strategy no-ff|squash]
            [--run-id ID --attempt N --event EVENT]  # CI modes only

There are no free base/head/commit arguments.  ``record_merge`` is deliberately
not exposed here or in feature_closeout's CLI.  Only Python's standard library
and lazy sibling contract APIs are used.
"""
from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

# Package-less sibling contract modules must remain available under ``-I``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = "agent-dev.delivery-result/v2"
OPERATION_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "delivery-operation.schema.json"
_ACTIVE_DECISIONS = ("pending", "waiting", "answered", "routing")


class DeliveryError(ValueError):
    """A frozen context, gate, Git transaction, or provider check failed."""


def _module(name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise DeliveryError("required shared module %s is not ready: %s" % (name, exc)) from exc


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _git(repo: Path, *args, check=True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if check and result.returncode:
        raise DeliveryError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result


def _oid(repo: Path, revision, tree=False) -> str:
    suffix = "^{tree}" if tree else "^{commit}"
    value = _git(repo, "rev-parse", "--verify", str(revision) + suffix).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DeliveryError("git did not return a full OID for %s" % revision)
    return value


def _commit_tree(repo: Path, tree: str, parents, message: str) -> str:
    command = ["git", "-C", str(repo), "commit-tree", tree]
    for parent in parents:
        command += ["-p", parent]
    result = subprocess.run(command, input=message, capture_output=True, text=True)
    if result.returncode:
        raise DeliveryError("git commit-tree failed: %s" % result.stderr.strip())
    commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DeliveryError("git commit-tree returned an invalid commit OID")
    return commit


def _cas_update_ref(repo: Path, branch: str, new: str, frozen_old: str) -> str:
    """The only local branch-moving merge primitive; old is a real CAS guard."""
    result = _git(repo, "update-ref", "refs/heads/" + branch, new, frozen_old, check=False)
    if result.returncode:
        raise DeliveryError("target CAS failed; target changed after preflight and was not moved by delivery: %s" %
                            (result.stderr or result.stdout).strip())
    return new


def _materialize_cas_result(target: Path, commit: str):
    """Update index/files without moving a ref or overwriting a later CAS loser."""
    _git(target, "read-tree", "--reset", "-u", commit)
    if _oid(target, "HEAD") != commit:
        raise DeliveryError("target changed after CAS; refusing to move it back")
    if not _clean(target):
        raise DeliveryError("target worktree is not clean after materializing CAS result")


def _restore_unmaterialized_cas(target: Path, commit: str, frozen_old: str):
    if _clean(target):
        return
    if _git(target, "diff", "--quiet", frozen_old, check=False).returncode != 0:
        raise DeliveryError("target worktree changed after CAS; refusing to overwrite unknown edits")
    untracked = [item for item in _git(target, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0") if item]
    dev = target / "dev"
    allowed = {"dev"} if dev.is_symlink() else set()
    if set(untracked) - allowed:
        raise DeliveryError("untracked files appeared after CAS; refusing to overwrite them")
    _materialize_cas_result(target, commit)


def _local_ticket_merge(context, target):
    target_before = context["target_commit"]
    source = context["source_head"]
    merge_tree = _git(target, "merge-tree", "--write-tree", target_before, source).stdout.splitlines()[0].strip()
    if merge_tree != context["source_tree"]:
        raise DeliveryError("clean merge-tree does not equal final rebased source tree")
    commit = _commit_tree(target, merge_tree, [target_before, source],
                          "Merge %s into %s\n" % (source, context["target_branch"]))
    _cas_update_ref(target, context["target_branch"], commit, target_before)
    _materialize_cas_result(target, commit)
    return commit


def _clean(repo: Path) -> bool:
    lines = [line for line in _git(repo, "status", "--porcelain").stdout.splitlines() if line.strip()]
    # Git does not apply a trailing-slash ignore (dev/) to a symlink.  The one
    # mandatory context symlink is not source dirt; every other entry, including
    # untracked files, remains fatal.
    dev = repo / "dev"
    if dev.is_symlink():
        try:
            main_dev = (_root_above(repo) / "dev").resolve()
            if dev.resolve() == main_dev:
                lines = [line for line in lines if line not in ("?? dev", "!! dev")]
        except DeliveryError:
            pass
    return not lines


def _root_above(path: Path) -> Path:
    for parent in path.resolve().parents:
        if (parent / "dev" / "agents-config.md").is_file():
            return parent.resolve()
    raise DeliveryError("cannot find dev/agents-config.md above %s" % path)


def _context_path(root: Path, context: dict) -> Path:
    return root / context["feature_dir"] / "impl" / context["issue_id"] / "ticket-context.json"


def _load_context(path) -> tuple:
    tc = _module("ticket_context")
    source = Path(path).resolve()
    context = tc.load_context(source)
    root = _root_above(source)
    expected = _context_path(root, context)
    if source != expected.resolve():
        raise DeliveryError("context must live at %s; arbitrary context locations are not accepted" % expected)
    issue = root / context["issue_path"]
    if not issue.is_file():
        raise DeliveryError("frozen issue path is missing: %s" % issue)
    return context, root, source, issue


def _config(root: Path, context=None):
    ac = _module("agents_config")
    relative = context.get("config_path") if context else "dev/agents-config.md"
    return ac.load(root / relative)


def _issue_frontmatter(issue: Path) -> dict:
    return _module("agents_config").read_frontmatter(issue)


def _source_branch(context: dict, issue: Path) -> str:
    fm = _issue_frontmatter(issue)
    slug = str(fm.get("feature-slug") or Path(context["feature_dir"]).name).strip()
    if not slug or "/" in slug or slug.startswith("."):
        raise DeliveryError("feature-slug is required to derive the ticket source branch")
    return "feat/%s-%s" % (slug, context["issue_id"])


def _worktrees(root: Path):
    result = []
    current = None
    for line in _git(root, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            current = {"path": Path(line[len("worktree "):]).resolve(), "branch": None}
            result.append(current)
        elif line.startswith("branch ") and current is not None:
            current["branch"] = line[len("branch "):]
    return result


def _worktree_for_branch(root: Path, branch: str) -> Path:
    full = "refs/heads/" + branch
    matches = [entry["path"] for entry in _worktrees(root) if entry["branch"] == full]
    if len(matches) != 1:
        raise DeliveryError("target/source branch %s must have exactly one registered worktree; found %d" % (branch, len(matches)))
    return matches[0]


def _legacy_local_human(fc: dict, fm: dict) -> bool:
    since = fc.get("since")
    if not since:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(since)) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(fm.get("created") or "")):
        raise DeliveryError("feature-closeout.since and ticket created must be YYYY-MM-DD")
    try:
        since_date = _datetime.date.fromisoformat(str(since))
        created_date = _datetime.date.fromisoformat(str(fm.get("created") or ""))
    except ValueError as exc:
        raise DeliveryError("feature-closeout.since and ticket created must be valid YYYY-MM-DD dates: %s" % exc) from exc
    return created_date < since_date


def _assert_target_allowed(context: dict, root: Path, issue: Path):
    config = _config(root, context)
    tc = _module("ticket_context")
    fm = _issue_frontmatter(issue)
    resolved = tc.target_branch(config, fm, root / context["feature_dir"])
    if resolved != context["target_branch"]:
        raise DeliveryError("ticket target changed after context freeze: %s != %s" % (resolved, context["target_branch"]))
    fc = config.get("feature-closeout") or {}
    if fc.get("mode") == "local-human" and resolved == config.get("main-branch"):
        legacy = _legacy_local_human(fc, fm)
        if not legacy:
            raise DeliveryError("local-human tickets may target only the feature integration branch; explicit main is rejected")


def start(issue_path, repo) -> dict:
    """Create the isolated worktree, main-dev symlink, and immutable context."""
    issue = Path(issue_path).resolve()
    root = Path(repo).resolve()
    if not issue.is_file():
        raise DeliveryError("issue does not exist: %s" % issue)
    if _root_above(issue) != root:
        raise DeliveryError("--repo must be the main worktree containing the issue")
    tc = _module("ticket_context")
    ac = _module("agents_config")
    config = ac.load(root / "dev" / "agents-config.md")
    fm = ac.read_frontmatter(issue)
    issue_id = str(fm.get("issue-id") or "")
    if not re.fullmatch(r"issue-[0-9]+[a-z]?", issue_id):
        raise DeliveryError("invalid/missing issue-id")
    feature_dir = issue.parent.parent
    target = tc.target_branch(config, fm, feature_dir)
    fc = config.get("feature-closeout") or {}
    if fc.get("mode") == "local-human" and target == config.get("main-branch"):
        if not _legacy_local_human(fc, fm):
            raise DeliveryError("local-human ticket cannot target main, including explicit target-branch: main")
    if fc.get("mode") == "local-human" and not _legacy_local_human(fc, fm):
        implicit = dict(fm)
        implicit["target-branch"] = None
        expected_target = tc.target_branch(config, implicit, feature_dir)
        if target != expected_target:
            raise DeliveryError("local-human ticket target must be the feature integration branch: %s != %s" % (
                target, expected_target))
    branch = "feat/%s-%s" % (str(fm.get("feature-slug") or feature_dir.name), issue_id)
    worktree = root / ".worktrees" / issue_id
    output = root / context_relative(fm, issue, root) / "impl" / issue_id / "ticket-context.json"
    branch_exists = _git(root, "rev-parse", "--verify", "-q", "refs/heads/" + branch, check=False).returncode == 0
    status = str(fm.get("status") or "")
    resume_state = worktree.exists() or branch_exists or output.is_file()
    if status == "merged":
        # Minimal safe choice: a merged ticket has already landed on the target
        # integration branch.  It must only move forward through feature finalize
        # (merged -> accepted -> done), never by re-opening delivery/start.
        raise DeliveryError("ticket is already merged into the integration branch; "
                            "feature finalize must mark it done and delivery.start cannot re-open it")
    if status != "confirmed":
        if status not in {"in-progress", "in-review", "mr-submitted"} or not resume_state:
            raise DeliveryError("fresh start requires confirmed; active status requires matching existing delivery state")
    plan = tc.check_plan(issue, root, primary_repo=root)
    if not plan.get("ok"):
        raise DeliveryError("start plan check failed; no worktree/branch was created: %s" % "; ".join(plan.get("errors") or []))
    statuses = plan.get("all_statuses") or {}
    blocked = fm.get("blocked-by") or []
    for dependency in blocked:
        # merged is enough to unblock downstream: the dependency code is already
        # on the target integration branch, even if the feature is not accepted yet.
        if statuses.get(dependency) not in {"merged", "done"}:
            raise DeliveryError("blocked-by dependency is neither merged nor done: %s" % dependency)
    _oid(root, target)
    # Validate DoD/spec/config/scope and all primary-repo inputs before any Git
    # mutation.  The provisional main-HEAD context is discarded, never reused.
    try:
        tc.create_context(issue, root, primary_repo=root)
    except (ValueError, OSError) as exc:
        raise DeliveryError("start prevalidation failed; no worktree/branch was created: %s" % exc) from exc
    if worktree.exists() and not (worktree / ".git").exists():
        try:
            worktree.rmdir()  # only an empty directory left by an interrupted add
        except OSError as exc:
            raise DeliveryError("interrupted start left a non-empty unregistered worktree path: %s" % exc) from exc
    registered = [entry for entry in _worktrees(root) if entry["branch"] == "refs/heads/" + branch]
    if registered:
        if len(registered) != 1 or registered[0]["path"] != worktree:
            raise DeliveryError("ticket branch is registered at an unexpected worktree")
        if not branch_exists:
            raise DeliveryError("registered ticket worktree has no matching branch")
    elif worktree.exists():
        raise DeliveryError("ticket worktree path exists but is not registered for the expected branch")
    elif branch_exists:
        _git(root, "worktree", "add", str(worktree), branch)
    else:
        _git(root, "worktree", "add", str(worktree), "-b", branch, target)
    dev = worktree / "dev"
    if (dev.exists() or dev.is_symlink()) and not dev.is_symlink():
        raise DeliveryError("ticket worktree contains a real dev entry; refusing to replace it")
    if not dev.is_symlink():
        try:
            os.symlink("../../dev", dev)
        except OSError as exc:
            raise DeliveryError("cannot create required dev symlink to main dev: %s" % exc) from exc
    if dev.resolve() != (root / "dev").resolve():
        raise DeliveryError("dev symlink does not resolve to the main worktree dev directory")
    if output.is_file():
        context, _, _, _ = _load_context(output)
        try:
            parameters = inspect.signature(tc.create_context).parameters
            kwargs = {"primary_repo": root} if "primary_repo" in parameters else {}
            fresh = tc.create_context(issue, worktree, **kwargs)
        except (ValueError, OSError) as exc:
            raise DeliveryError("ticket_context revalidation failed: %s" % exc) from exc
        if fresh["gate2_context_digest"] != context["gate2_context_digest"]:
            if status != "confirmed":
                raise DeliveryError("existing context is stale; active resume cannot silently refreeze")
            context = fresh
            _module("state_store").atomic_json_write(output, context)
    else:
        try:
            parameters = inspect.signature(tc.create_context).parameters
            kwargs = {"primary_repo": root} if "primary_repo" in parameters else {}
            context = tc.create_context(issue, worktree, **kwargs)
        except (ValueError, OSError) as exc:
            raise DeliveryError(
                "ticket_context.create_context failed; worktree and dev symlink were preserved for idempotent resume: %s" % exc) from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        _module("state_store").atomic_json_write(output, context)
    if context["source_head"] != _oid(worktree, "HEAD") or context["target_commit"] != _oid(root, target):
        raise DeliveryError("ticket_context froze the wrong source/target; refusing to amend it locally")
    return {"schema": SCHEMA, "operation": "start", "context": str(output),
            "worktree": str(worktree), "branch": branch, "target": target,
            "source_head": context["source_head"]}


def context_relative(fm, issue: Path, root: Path) -> str:
    """Return the main-dev feature path without deriving it from a worktree."""
    return issue.parent.parent.resolve().relative_to(root).as_posix()


def _invalidation(context: dict, context_path: Path, *, old_head, new_target,
                  new_head=None, status="completed", error=None):
    impl = context_path.parent
    artifacts = []
    for pattern in ("verification-*.json", "review-receipt.json", "conformance-*.md"):
        for path in sorted(impl.glob(pattern)):
            if path.is_file() and not path.is_symlink():
                artifacts.append({"path": path.name, "sha256": _sha(path)})
    event = {
        "event_id": "rebase-invalidation:" + _digest({
            "old": old_head, "target": new_target, "new": new_head,
            "status": status, "artifacts": artifacts,
        }),
        "schema": "agent-dev.delivery-invalidation/v2", "issue": context["issue_id"],
        "reason": "rebase invalidates all prior verification/review/conformance; history retained",
        "old_source_head": old_head, "new_target": new_target, "new_source_head": new_head,
        "status": status, "artifacts": artifacts,
    }
    if error:
        event["error"] = str(error)
    _module("state_store").append_event_once(impl / "delivery-invalidations.jsonl", event, event["event_id"])
    _module("state_store").atomic_json_write(impl / "latest-invalidation.json", event)
    return event


def _rebase_in_progress(repo: Path) -> bool:
    git_dir = Path(_git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _rebase_recovery(context_path, current_context, target_now):
    for path in sorted(context_path.parent.glob("rebase-operation-*.json")):
        try:
            journal = _module("state_store").OperationJournal.load(path)
            steps = {step["id"]: step for step in journal.steps}
            expected = steps["rebase"].get("expected") or {}
            old = expected.get("context") or {}
            record = steps["record"].get("expected") or {}
            updated = record.get("context") or {}
            identities = {old.get("gate2_context_digest"), updated.get("gate2_context_digest")}
            if (journal.state != "completed" and expected.get("new_target") == target_now
                    and current_context.get("gate2_context_digest") in identities):
                return journal, old, path
        except (ValueError, OSError) as exc:
            raise DeliveryError("corrupt rebase operation journal: %s" % path) from exc
    return None


def _resume_rebase(journal, context, root, source_path, issue, source, operation_path):
    steps = {step["id"]: step for step in journal.steps}
    expected = steps["rebase"]["expected"]
    target_now = expected["new_target"]
    old_head = expected["old_head"]
    conflict_marker = source_path.parent / "rebase-conflict.json"
    started_marker = source_path.parent / "rebase-started.json"
    if steps["rebase"]["status"] != "done":
        if not _clean(source):
            raise DeliveryError("source worktree must be clean before rebase/continue registration")
        current_head = _oid(source, "HEAD")
        target_contained = _git(source, "merge-base", "--is-ancestor", target_now, current_head,
                                check=False).returncode == 0
        started = started_marker.is_file()
        if started and _rebase_in_progress(source):
            raise DeliveryError("rebase conflict is still active; resolve/continue before delivery.rebase registration")
        if not target_contained or not started:
            journal.advance("rebase", expected=expected)
            _module("state_store").atomic_json_write(started_marker, {
                "schema": "agent-dev.rebase-started/v2", "old_head": old_head,
                "old_base": expected["old_base"], "old_target": expected["old_target"],
                "new_target": target_now,
            })
            result = _git(source, "rebase", "--onto", target_now, expected["old_base"], check=False)
            if result.returncode:
                error = (result.stderr or result.stdout).strip()
                event = _invalidation(context, source_path, old_head=old_head, new_target=target_now,
                                      new_head=_oid(source, "HEAD"), status="conflict", error=error)
                _module("state_store").atomic_json_write(conflict_marker, {
                    "schema": "agent-dev.rebase-conflict/v2", "target": target_now,
                    "old_source_head": old_head, "observed_head": _oid(source, "HEAD"),
                    "invalidation_id": event["event_id"],
                })
                raise DeliveryError("rebase conflict; source worktree and conflict state preserved: %s" % error)
        new_head = _oid(source, "HEAD")
        if _git(source, "merge-base", "--is-ancestor", target_now, new_head, check=False).returncode != 0:
            raise DeliveryError("rebase did not produce a HEAD containing the frozen target")
        observed = {"source_head": new_head, "source_tree": _oid(source, new_head, tree=True)}
        journal.advance("rebase", expected=expected, observed=observed)
        steps = {step["id"]: step for step in journal.steps}
    new_head = steps["rebase"]["observed"]["source_head"]
    updated = dict(context)
    updated.update({
        "target_commit": target_now, "base": target_now,
        "source_head": new_head, "source_tree": steps["rebase"]["observed"]["source_tree"],
    })
    tc = _module("ticket_context")
    updated["gate2_context_digest"] = tc.context_digest(updated)
    record_expected = {"context": updated, "old_head": old_head, "new_target": target_now}
    if steps["record"]["status"] != "done":
        journal.advance("record", expected=record_expected)
        event = _invalidation(updated, source_path, old_head=old_head,
                              new_target=target_now, new_head=new_head)
        _module("state_store").atomic_json_write(source_path, updated)
        tc.load_context(source_path)
        journal.advance("record", expected=record_expected,
                        observed={"context_digest": updated["gate2_context_digest"],
                                  "invalidation": event["event_id"]})
    journal.complete()
    final_steps = {step["id"]: step for step in journal.steps}
    for marker in (started_marker, conflict_marker):
        if marker.exists():
            marker.unlink()
    return {"schema": SCHEMA, "operation": "rebase", "changed": True,
            "context": updated, "invalidation": final_steps["record"]["observed"]["invalidation"]}


def rebase(context_path) -> dict:
    """Only entry point that advances final target/base; conflicts stay in place."""
    current, root, source_path, issue = _load_context(context_path)
    _assert_target_allowed(current, root, issue)
    source = _worktree_for_branch(root, _source_branch(current, issue))
    target_now = _oid(root, current["target_branch"])
    recovery = _rebase_recovery(source_path, current, target_now)
    if recovery is not None:
        journal, old, operation_path = recovery
        return _resume_rebase(journal, old, root, source_path, issue, source, operation_path)
    old_head = current["source_head"]
    if target_now == current["target_commit"] and _oid(source, "HEAD") == old_head:
        return {"schema": SCHEMA, "operation": "rebase", "changed": False, "context": current}
    if not _clean(source):
        raise DeliveryError("source worktree must be clean before rebase")
    if target_now != current["target_commit"] and _git(root, "merge-base", "--is-ancestor", current["target_commit"], target_now, check=False).returncode != 0:
        raise DeliveryError("target did not fast-forward from frozen target; refusing non-ff rebase")
    operation_id = "rebase-" + _digest({"context": current["gate2_context_digest"], "new_target": target_now})
    operation_path = source_path.parent / ("rebase-operation-" + operation_id[-16:] + ".json")
    expected = {
        "context": current, "old_head": old_head, "old_base": current["base"],
        "old_target": current["target_commit"], "new_target": target_now,
    }
    journal = _module("state_store").OperationJournal.create(
        operation_path, operation_id, ["rebase", "record"])
    journal.advance("rebase", expected=expected)
    return _resume_rebase(journal, current, root, source_path, issue, source, operation_path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load_json_strict(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeliveryError("cannot load JSON %s: %s" % (path, exc)) from exc
    if not isinstance(value, dict):
        raise DeliveryError("JSON root must be an object: %s" % path)
    return value


def _run_ticket_gate(context, root, issue, source):
    """Ticket-level inner loop only; supervisor/receipt/conformance stay at feature gate."""
    pc = _module("process_check")
    args = SimpleNamespace(
        issue=str(issue), repo=str(source), phase="ticket", base=context["base"],
        head=context["source_head"], ci_verdict=None, ci_run_proof=None,
        impl_log=None, evidence=None, json=None,
    )
    try:
        report, meta = pc.run(args)
    except (ValueError, OSError) as exc:
        raise DeliveryError("process_check ticket failed closed: %s" % exc) from exc
    if report.failed or meta.get("head") != context["source_head"] or meta.get("base") != context["base"]:
        messages = "; ".join(i["msg"] for i in report.items if i["level"] == "FAIL")
        raise DeliveryError("process_check ticket did not pass: " + messages)
    return meta


def _run_decisions(context, root):
    fc = _module("feature_closeout")
    try:
        active = fc._active_decision_records(root / context["feature_dir"], context["issue_id"])
    except Exception as exc:
        raise DeliveryError("human decision state is unreadable; fail closed: %s" % exc) from exc
    if active:
        raise DeliveryError("active human decision blocks merge: " + ", ".join(str(x.get("id")) for x in active))


def _preflight(context, root, issue):
    _assert_target_allowed(context, root, issue)
    source_branch = _source_branch(context, issue)
    source = _worktree_for_branch(root, source_branch)
    target = _worktree_for_branch(root, context["target_branch"])
    if not _clean(source) or not _clean(target):
        raise DeliveryError("source and target worktrees must both be clean (including untracked files)")
    if _oid(source, "HEAD") != context["source_head"] or _oid(source, "HEAD", tree=True) != context["source_tree"]:
        raise DeliveryError("source HEAD/tree differs from frozen context; rebase/verify again")
    target_before = _oid(target, "HEAD")
    if target_before != context["target_commit"]:
        raise DeliveryError("target advanced after context freeze; delivery.rebase is required before merge")
    if _git(source, "merge-base", "--is-ancestor", target_before, context["source_head"], check=False).returncode != 0:
        raise DeliveryError("target is not an ancestor of source; rebase is required")
    # Ticket merge requires only the ticket-level inner loop.  Supervisor
    # verification, review receipts and conformance are deferred to feature finalize.
    _run_ticket_gate(context, root, issue, source)
    _run_decisions(context, root)
    # Fail closed before the merge side effect if the journal is corrupt; the
    # merged event append would otherwise fail only after code landed.
    _module("feature_closeout").load_progress_events(root / context["feature_dir"])
    return {"source": source, "target": target, "source_branch": source_branch, "target_before": target_before}


def _operation_id(context):
    return "ticket-" + _digest({"kind": "ticket-merge", "context": context["gate2_context_digest"]})


def _operation_path(context_path: Path) -> Path:
    return context_path.parent / "delivery-operation.json"


def _journal(context, context_path):
    store = _module("state_store")
    operation_id = _operation_id(context)
    path = _operation_path(context_path)
    expected = {
        "operation_id": operation_id, "context_digest": context["gate2_context_digest"],
        "target_before": context["target_commit"], "source_head": context["source_head"],
        "source_tree": context["source_tree"], "issue_id": context["issue_id"], "strategy": "no-ff",
    }
    # No cleanup step: worktree/branch and the merged ticket are retained until
    # feature finalize accepts the candidate.  Legacy journals may still carry a
    # cleanup step; reuse their exact step IDs and treat that step as retention.
    steps = ["merge", "verify", "record"]
    if path.is_file():
        existing = store.OperationJournal.load(path)
        if existing.operation_id != operation_id:
            raise DeliveryError("delivery journal belongs to another operation")
        steps = [step["id"] for step in existing.steps]
    journal = store.OperationJournal.create(path, operation_id, steps)
    _validate_operation_journal(journal.data)
    return journal, expected


def _validate_operation_journal(data):
    try:
        errors = _module("json_schema").load_validate(OPERATION_SCHEMA_PATH, data)
    except (ValueError, OSError) as exc:
        raise DeliveryError("operation schema validation unavailable: %s" % exc) from exc
    if errors:
        raise DeliveryError("delivery operation journal schema invalid: " + "; ".join(errors))


def _verify_commit_identity(repo, merge_commit, target_before, source_head, source_tree):
    parents = _git(repo, "rev-list", "--parents", "-n", "1", merge_commit).stdout.split()[1:]
    if parents != [target_before, source_head]:
        raise DeliveryError("no-ff merge parents must be exact target_before and source_head")
    tree = _oid(repo, merge_commit, tree=True)
    if tree != source_tree:
        raise DeliveryError("final merge tree differs from final rebased source tree; extra edits are rejected")
    return {"merge_commit": merge_commit, "tree": tree, "parents": parents}


def _verify_ticket_merge(context, target, target_before, merge_commit):
    if _oid(target, "HEAD") != merge_commit:
        raise DeliveryError("target HEAD is not the exact merge transaction")
    return _verify_commit_identity(target, merge_commit, target_before,
                                   context["source_head"], context["source_tree"])



def _record_ticket(context, root, issue, merge_commit):
    _verify_ticket_merge(context, _worktree_for_branch(root, context["target_branch"]),
                         context["target_commit"], merge_commit)
    text = issue.read_text(encoding="utf-8")
    if not re.search(r"^status:\s*.*$", text, re.M):
        raise DeliveryError("issue frontmatter lacks status; cannot mark merged")
    # merged is not acceptance: the worktree/branch stay until feature finalize
    # emits accepted and moves the ticket to done.
    updated = re.sub(r"^status:\s*.*$", "status: merged", text, count=1, flags=re.M)
    _module("state_store").atomic_write(issue, updated, mode=0o644)
    timestamp = _git(root, "show", "-s", "--format=%cd", "--date=format-local:%Y-%m-%d %H:%M", merge_commit).stdout.strip()
    config = _config(root, context)
    hosts = ((config.get("dispatch") or {}).get("hosts") or {})
    host = os.environ.get("AGENT_HOST") or (next(iter(hosts)) if len(hosts) == 1 else "main-session")
    event = {
        "event_id": _operation_id(context),
        "ts": timestamp, "event": "merged", "issue": context["issue_id"],
        "host": host, "branch": _source_branch(context, issue), "target": context["target_branch"],
        "commit": merge_commit, "source_head": context["source_head"],
        "source_tree": context["source_tree"], "target_before": context["target_commit"],
        "context_digest": context["gate2_context_digest"], "operation_id": _operation_id(context),
    }
    _module("state_store").append_event_once(root / context["feature_dir"] / "progress.jsonl",
                                             event, event["event_id"])
    return {"status": "merged", "merged_event": event["event_id"], "commit": merge_commit}


def source_branch_exists(root, branch):
    return _git(root, "rev-parse", "--verify", "-q", "refs/heads/" + branch,
                check=False).returncode == 0


def _remote_merge(context, root, preflight):
    """Merge the unique MR with source-SHA guard and explicit target-CAS boundary."""
    config = _config(root, context)
    fc_config = config.get("feature-closeout") or {}
    mode = fc_config.get("mode")
    if mode not in ("server-enforced", "cooperative-ci"):
        raise DeliveryError("remote MR delivery requires server-enforced/cooperative-ci; local fallback is forbidden")
    settings = _ci_settings(config, {})
    cp = _module("ci_proof")
    cap = cp.capabilities(settings["provider"], settings["repository"],
                          base_url=settings["base_url"], workflow=settings["workflow"],
                          branch=context["target_branch"], mode=mode,
                          token=settings["token"])
    cp.require_capabilities(cap, mode, remote_merge=True)
    adapter = mr_adapter(settings["provider"], settings["repository"],
                         base_url=settings["base_url"], token=settings["token"])
    reference = adapter.find(preflight["source_branch"], context["target_branch"],
                             source_sha=context["source_head"])
    merge_commit = adapter.merge(reference, source_sha=context["source_head"],
                                 target_branch=context["target_branch"],
                                 target_before=context["target_commit"], mode=mode)
    # API success alone is not enough: fetch the target and independently verify
    # the original merge object, even if the target has since advanced.
    _git(root, "fetch", "--no-tags", "origin", context["target_branch"])
    fetched_tip = _oid(root, "FETCH_HEAD")
    if _git(root, "cat-file", "-e", merge_commit + "^{commit}", check=False).returncode != 0:
        _git(root, "fetch", "--no-tags", "origin", merge_commit)
    observed = _verify_commit_identity(root, merge_commit, context["target_commit"],
                                       context["source_head"], context["source_tree"])
    original_in_target = _git(root, "merge-base", "--is-ancestor", merge_commit, fetched_tip,
                              check=False).returncode == 0
    if fetched_tip != merge_commit:
        observed.update({
            "status": "needs-human", "remote_target": fetched_tip,
            "remote_target_advanced": True, "original_merge_in_target": original_in_target,
        })
        return observed
    target = preflight["target"]
    if _oid(target, "HEAD") != context["target_commit"]:
        raise DeliveryError("local target changed during remote merge; refusing to overwrite it")
    _cas_update_ref(target, context["target_branch"], merge_commit, context["target_commit"])
    _materialize_cas_result(target, merge_commit)
    observed["status"] = "merged"
    observed["remote_target"] = fetched_tip
    return observed


def _resume_ticket(context, root, context_path, issue, journal, expected, preflight=None):
    if journal.state == "failed":
        raise DeliveryError("failed journal requires manual inspection; reconcile will not adopt arbitrary Git state")
    if journal.state == "completed":
        # The merge transaction is complete; worktree/branch stay for feature
        # finalize.  Replay must not delete resources or rewrite the ticket.
        status = str(_issue_frontmatter(issue).get("status") or "merged")
        steps = {step["id"]: step for step in journal.steps}
        merge_commit = (steps.get("merge", {}).get("observed") or {}).get("merge_commit")
        result = {"schema": SCHEMA, "operation": "reconcile", "operation_id": journal.operation_id,
                  "state": "completed", "replayed": True, "status": status,
                  "retained": {"worktree": str(root / ".worktrees" / context["issue_id"]),
                               "branch": _source_branch(context, issue)}}
        if merge_commit:
            result["merge_commit"] = merge_commit
        return result
    steps = {step["id"]: step for step in journal.steps}
    if steps["merge"]["status"] != "done":
        target = preflight["target"] if preflight is not None else _worktree_for_branch(root, context["target_branch"])
        target_head = _oid(target, "HEAD")
        if target_head == context["target_commit"]:
            if preflight is None:
                preflight = _preflight(context, root, issue)
                target = preflight["target"]
            journal.advance("merge", expected=expected)
            tracker = _config(root, context).get("tracker")
            if tracker == "local":
                target_head = _local_ticket_merge(context, target)
            elif tracker == "gitlab":
                remote = _remote_merge(context, root, preflight)
                if remote.get("status") == "needs-human":
                    journal.advance("merge", expected=expected, observed=remote)
                    raise DeliveryError("needs-human: remote MR %s succeeded, but target advanced to %s; original merge preserved, no retry/record" %
                                        (remote["merge_commit"], remote["remote_target"]))
                target_head = remote["merge_commit"]
            else:
                raise DeliveryError("unsupported tracker for delivery merge: %r" % tracker)
        else:
            _restore_unmaterialized_cas(target, target_head, context["target_commit"])
        observed = _verify_ticket_merge(context, target, context["target_commit"], target_head)
        journal.advance("merge", expected=expected, observed=observed)
    steps = {step["id"]: step for step in journal.steps}
    merge_commit = steps["merge"]["observed"]["merge_commit"]
    if steps["merge"]["observed"].get("status") == "needs-human":
        raise DeliveryError("needs-human: remote target advanced after the original merge; no duplicate merge or merged/record was performed")
    verify_expected = dict(expected, merge_commit=merge_commit)
    if steps["verify"]["status"] != "done":
        journal.advance("verify", expected=verify_expected)
        observed = _verify_ticket_merge(context, _worktree_for_branch(root, context["target_branch"]),
                                        context["target_commit"], merge_commit)
        journal.advance("verify", expected=verify_expected, observed=observed)
    if steps["record"]["status"] != "done":
        journal.advance("record", expected=verify_expected)
        observed = _record_ticket(context, root, issue, merge_commit)
        journal.advance("record", expected=verify_expected, observed=observed)
    # Legacy journals may still have a cleanup step.  It is now a retention
    # observation, never an authorization to remove the ticket worktree/branch.
    if "cleanup" in steps and steps["cleanup"]["status"] != "done":
        cleanup_expected = dict(verify_expected, issue_id=context["issue_id"])
        journal.advance("cleanup", expected=cleanup_expected)
        retained = {"retained": True, "worktree": str(root / ".worktrees" / context["issue_id"]),
                    "branch": _source_branch(context, issue),
                    "reason": "feature finalize owns acceptance cleanup"}
        journal.advance("cleanup", expected=cleanup_expected, observed=retained)
    journal.complete()
    status = str(_issue_frontmatter(issue).get("status") or "merged")
    if status not in ("merged", "done"):
        raise DeliveryError("ticket status after record is neither merged nor done: %r" % status)
    result = {"schema": SCHEMA, "operation": "merge", "operation_id": journal.operation_id,
              "merge_commit": merge_commit, "status": status, "state": "completed",
              "retained": {"worktree": str(root / ".worktrees" / context["issue_id"]),
                           "branch": _source_branch(context, issue)}}
    if _config(root, context).get("tracker") == "gitlab":
        mode = (_config(root, context).get("feature-closeout") or {}).get("mode")
        result["target_cas_merge"] = "not-supported-by-official-provider-api"
        if mode == "cooperative-ci":
            result["server_enforcement"] = "not-server-enforced"
            result["warning"] = "remote MR used source-SHA guarding, but no atomic target-old-OID CAS or server enforcement guarantee"
    return result


def merge(context_path) -> dict:
    context, root, source_path, issue = _load_context(context_path)
    path = _operation_path(source_path)
    if path.exists():
        return reconcile(source_path)
    preflight = _preflight(context, root, issue)
    journal, expected = _journal(context, source_path)
    return _resume_ticket(context, root, source_path, issue, journal, expected, preflight)


def reconcile(context_path=None, *, candidate_path=None, candidate_repo=None, repo=None,
              strategy=None) -> dict:
    """Resume only an exact ticket or feature journal, never a commit argument."""
    if candidate_path is not None:
        if context_path is not None or candidate_repo is None or repo is None:
            raise DeliveryError("feature reconcile requires --candidate, --candidate-repo and --repo")
        candidate_file = Path(candidate_path).resolve()
        fdir, round_name, integ = _candidate_location(candidate_file)
        candidate = _load_json_strict(candidate_file)
        if not _feature_operation_path(integ).is_file():
            raise DeliveryError("no exact feature promote operation exists for this candidate")
        journal = _module("state_store").OperationJournal.load(_feature_operation_path(integ))
        if not _journalize_existing_feature_merge(candidate, Path(repo).resolve(), journal, strategy):
            raise DeliveryError("feature merge has not executed; use promote to repeat authorization and online gates before any merge")
        return _resume_feature(candidate, fdir, integ, Path(candidate_repo).resolve(),
                               Path(repo).resolve(), strategy, journal=journal,
                               round_name=round_name)
    if context_path is None:
        raise DeliveryError("reconcile requires --context or --candidate")
    context, root, source_path, issue = _load_context(context_path)
    path = _operation_path(source_path)
    if not path.is_file():
        raise DeliveryError("no exact delivery operation exists for this context")
    journal = _module("state_store").OperationJournal.load(path)
    _validate_operation_journal(journal.data)
    if journal.operation_id != _operation_id(context):
        raise DeliveryError("journal operation does not match frozen context")
    _, expected = _journal(context, source_path)
    return _resume_ticket(context, root, source_path, issue, journal, expected)


def _candidate_location(path: Path):
    path = path.resolve()
    if path.name != "candidate.json":
        raise DeliveryError("--candidate must name candidate.json")
    if path.parent.name == "integration":
        return path.parent.parent, None, path.parent
    if path.parent.parent.name == "integration":
        return path.parent.parent.parent, path.parent.name, path.parent
    raise DeliveryError("candidate must be under feature integration/[round]/candidate.json")


def _ci_settings(config, overrides):
    fc = config.get("feature-closeout") or {}
    configured = fc.get("ci") or {}
    if not isinstance(configured, dict):
        raise DeliveryError("feature-closeout.ci must be a mapping")
    result = {}
    for key in ("provider", "repository", "base_url", "workflow", "artifact", "token_env"):
        config_key = key.replace("_", "-")
        result[key] = overrides.get(key) or configured.get(config_key)
    result["artifact"] = result["artifact"] or "verdict.json"
    missing = [key for key in ("provider", "repository", "base_url", "workflow") if not result.get(key)]
    if missing:
        raise DeliveryError("CI settings missing: " + ", ".join(missing))
    token = os.environ.get(result["token_env"], "") if result["token_env"] else None
    result["token"] = token
    return result


def _without_volatile_proof_fields(value):
    if isinstance(value, dict):
        return {key: _without_volatile_proof_fields(item) for key, item in value.items()
                if key != "fetched_at"}
    if isinstance(value, list):
        return [_without_volatile_proof_fields(item) for item in value]
    return value


def _feature_operation_id(candidate, proof, evidence=None):
    return "feature-" + _digest({
        "kind": "feature-promote", "candidate": candidate,
        "proof": _without_volatile_proof_fields(proof),
        "evidence": evidence or {},
    })


def _feature_operation_path(integ: Path) -> Path:
    return integ / "delivery-promote.json"


def _authorization_payload(authorization):
    return json.loads(json.dumps({key: value for key, value in authorization.items() if key != "_capability"},
                                 ensure_ascii=False, allow_nan=False))


def _supersede_pending_feature_journal(integ, existing, new_operation_id):
    """Archive an unmerged pending operation and atomically install the new one."""
    if existing.state not in ("running", "completed"):
        raise DeliveryError("only an unmerged pending feature operation can be superseded")
    steps = {step["id"]: step for step in existing.steps}
    if existing.state == "completed" or steps["merge"]["status"] == "done" or "merge_commit" in (steps["merge"].get("observed") or {}):
        raise DeliveryError("already merged feature operation cannot be superseded")
    if new_operation_id == existing.operation_id:
        raise DeliveryError("identical operation ID does not require supersede")
    history = integ / "superseded"
    old_id = existing.operation_id
    historical_journal = history / ("delivery-promote-%s.json" % old_id)
    marker = history / ("delivery-promote-%s.superseded.json" % old_id)
    store = _module("state_store")
    store.atomic_json_write(historical_journal, existing.data)
    store.atomic_json_write(marker, {
        "schema": "agent-dev.feature-promote-superseded/v2",
        "operation_id": old_id, "state": "superseded", "superseded_by": new_operation_id,
        "reason": "CI run/proof replaced before merge",
        "journal": historical_journal.name,
        "candidate_sha256": steps["authorize"]["expected"]["candidate_sha256"],
    })
    temporary = integ / ("delivery-promote-%s.pending.json" % new_operation_id)
    store.OperationJournal.create(temporary, new_operation_id,
                                  ["authorize", "merge", "verify", "record"])
    # The fixed current path is the sole active authority.  A crash before this
    # replace leaves the old pending current and a resumable named successor.
    os.replace(temporary, _feature_operation_path(integ))
    return store.OperationJournal.load(_feature_operation_path(integ))


def _feature_journal(integ, operation_id, candidate, strategy, evidence):
    expected = {
        "operation_id": operation_id, "candidate_sha256": _digest(candidate),
        "main_before": candidate["main_head"], "main_branch": candidate["main_branch"],
        "integration_branch": candidate["integration_branch"], "mode": candidate["mode"],
        "candidate": candidate["candidate"],
        "candidate_tree": candidate["candidate_tree"], "strategy": strategy,
        "evidence": evidence, "evidence_sha256": _digest(evidence),
    }
    path = _feature_operation_path(integ)
    if path.is_file():
        journal = _module("state_store").OperationJournal.load(path)
        if journal.operation_id != operation_id:
            journal = _supersede_pending_feature_journal(integ, journal, operation_id)
    else:
        journal = _module("state_store").OperationJournal.create(
            path, operation_id, ["authorize", "merge", "verify", "record"])
    journal.advance("authorize", expected=expected)
    _validate_operation_journal(journal.data)
    return journal, expected


def _verify_feature_result(main_repo, candidate, merge_commit, strategy):
    if _oid(main_repo, "HEAD") != merge_commit:
        raise DeliveryError("main HEAD is not the exact feature merge transaction")
    parents = _git(main_repo, "rev-list", "--parents", "-n", "1", merge_commit).stdout.split()[1:]
    expected = [candidate["main_head"], candidate["candidate"]] if strategy == "no-ff" else [candidate["main_head"]]
    if parents != expected:
        raise DeliveryError("feature merge parents do not match frozen main/candidate and strategy")
    tree = _oid(main_repo, merge_commit, tree=True)
    if tree != candidate["candidate_tree"]:
        raise DeliveryError("feature merge tree differs from candidate tree")
    proven = _git(main_repo, "merge-tree", "--write-tree", candidate["main_head"], candidate["candidate"]).stdout.splitlines()[0].strip()
    if proven != tree:
        raise DeliveryError("merge-tree cannot prove the feature merge result")
    return {"merge_commit": merge_commit, "tree": tree, "parents": parents}


def _candidate_without_merge_record(candidate):
    return {key: value for key, value in candidate.items()
            if key not in ("merged_commit", "merged_at", "merge_operation", "merge_strategy", "ci_proof_sha256")}


def _feature_evidence(integ: Path) -> dict:
    return _module("feature_closeout").feature_gate_evidence(integ)


def _validate_feature_resume_identity(candidate, candidate_repo, main_repo, journal, strategy, integ,
                                      allow_evidence_change=False):
    steps = {step["id"]: step for step in journal.steps}
    expected = steps["authorize"].get("expected") or {}
    if not expected:
        raise DeliveryError("feature journal has no frozen candidate identity")
    if _digest(_candidate_without_merge_record(candidate)) != expected.get("candidate_sha256"):
        raise DeliveryError("current candidate full hash differs from journaled candidate; refusing A→B resume")
    evidence = _feature_evidence(integ)
    if evidence != expected.get("evidence") or _digest(evidence) != expected.get("evidence_sha256"):
        if not allow_evidence_change:
            raise DeliveryError("feature review/verification/conformance evidence changed after authorization; "
                                "refreeze and reauthorize before merge")
    if candidate.get("mode") not in ("local-human", "server-enforced", "cooperative-ci"):
        raise DeliveryError("current candidate mode is invalid")
    if candidate.get("main_branch") != expected.get("main_branch") or candidate.get("main_head") != expected.get("main_before"):
        raise DeliveryError("current candidate main identity differs from journal")
    expected_strategy = expected.get("strategy")
    if strategy is not None and strategy != expected_strategy:
        raise DeliveryError("feature reconcile cannot cross no-ff/squash strategy")
    if expected_strategy not in ("no-ff", "squash"):
        raise DeliveryError("feature journal strategy is invalid")
    if _oid(candidate_repo, "HEAD") != candidate.get("candidate") or _oid(candidate_repo, "HEAD", tree=True) != candidate.get("candidate_tree"):
        raise DeliveryError("integration HEAD/tree differs from journaled candidate")
    if _git(main_repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != candidate.get("main_branch"):
        raise DeliveryError("main worktree is not on journaled candidate.main_branch")
    merge_step = steps["merge"]
    merge_commit = (merge_step.get("observed") or {}).get("merge_commit")
    if candidate.get("merged_commit") and candidate["merged_commit"] != merge_commit:
        raise DeliveryError("candidate merged_commit does not belong to this journal")
    if candidate.get("merge_operation") and candidate["merge_operation"] != journal.operation_id:
        raise DeliveryError("candidate merge_operation does not belong to this journal")
    main_head = _oid(main_repo, "HEAD")
    if main_head not in (expected["main_before"], merge_commit):
        raise DeliveryError("main HEAD is neither frozen main_before nor this exact merge; refusing cross-operation resume")
    if steps["authorize"]["status"] == "done":
        payload = steps["authorize"]["observed"]["authorization"]
        recomputed = _feature_operation_id(_candidate_without_merge_record(candidate), payload.get("online_proof"),
                                           expected.get("evidence"))
        if recomputed != journal.operation_id:
            raise DeliveryError("feature journal operation ID does not match candidate/proof identity")
    return expected_strategy


def _journalize_existing_feature_merge(candidate, main_repo, journal, strategy):
    steps = {step["id"]: step for step in journal.steps}
    merge_step = steps["merge"]
    if merge_step["status"] == "done":
        return True
    main_head = _oid(main_repo, "HEAD")
    if main_head == candidate["main_head"]:
        return False
    expected = steps["authorize"]["expected"]
    actual_strategy = expected["strategy"]
    if strategy is not None and strategy != actual_strategy:
        raise DeliveryError("feature reconcile cannot cross no-ff/squash strategy")
    _restore_unmaterialized_cas(main_repo, main_head, candidate["main_head"])
    observed = _verify_feature_result(main_repo, candidate, main_head, actual_strategy)
    journal.advance("merge", expected=expected, observed=observed)
    return True


def _resume_feature(candidate, fdir, integ, candidate_repo, main_repo, strategy,
                    journal=None, expected=None, authorization=None, round_name=None):
    if journal is None:
        journal = _module("state_store").OperationJournal.load(_feature_operation_path(integ))
        _validate_operation_journal(journal.data)
    strategy = _validate_feature_resume_identity(candidate, candidate_repo, main_repo, journal, strategy, integ)
    if expected is None:
        expected = {step["id"]: step for step in journal.steps}["authorize"]["expected"]
    if journal.state == "failed":
        raise DeliveryError("failed feature journal requires manual inspection")
    steps = {step["id"]: step for step in journal.steps}
    if steps["authorize"]["status"] != "done":
        if authorization is None:
            raise DeliveryError("authorization was not persisted; rerun promote to revalidate before any merge")
        payload = _authorization_payload(authorization)
        journal.advance("authorize", expected=expected, observed={"authorization": payload})
        steps = {step["id"]: step for step in journal.steps}
    payload = steps["authorize"]["observed"]["authorization"]
    if authorization is None:
        authorization = _module("feature_closeout").restore_merge_authorization(payload)
    if steps["merge"]["status"] != "done":
        journal.advance("merge", expected=expected)
        if _oid(main_repo, "HEAD") == candidate["main_head"]:
            merge_result = _execute_feature_merge(main_repo, candidate, strategy)
            merge_commit = merge_result["commit"]
        else:
            merge_commit = _oid(main_repo, "HEAD")
            _restore_unmaterialized_cas(main_repo, merge_commit, candidate["main_head"])
        observed = _verify_feature_result(main_repo, candidate, merge_commit, strategy)
        journal.advance("merge", expected=expected, observed=observed)
        steps = {step["id"]: step for step in journal.steps}
    merge_commit = steps["merge"]["observed"]["merge_commit"]
    verify_expected = dict(expected, merge_commit=merge_commit)
    if steps["verify"]["status"] != "done":
        journal.advance("verify", expected=verify_expected)
        observed = _verify_feature_result(main_repo, candidate, merge_commit, strategy)
        journal.advance("verify", expected=verify_expected, observed=observed)
        steps = {step["id"]: step for step in journal.steps}
    if steps["record"]["status"] != "done":
        journal.advance("record", expected=verify_expected)
        merge_result = {"repo": str(main_repo), "commit": merge_commit,
                        "strategy": strategy, "operation_id": journal.operation_id}
        report = _module("feature_closeout").record_merge(authorization, merge_result)
        if report.failed:
            messages = "; ".join(i["msg"] for i in report.items if i["level"] == "FAIL")
            raise DeliveryError("feature merged but recording failed; candidate remains pending: " + messages)
        journal.advance("record", expected=verify_expected,
                        observed={"merge_commit": merge_commit, "recorded": True})
    finalize_report = _module("feature_closeout").finalize_tickets(
        fdir, main_repo, round_name, cleanup=False)
    if finalize_report.failed:
        messages = "; ".join(i["msg"] for i in finalize_report.items if i["level"] == "FAIL")
        raise DeliveryError("feature merged and recorded, but ticket finalize failed; "
                            "run feature-closeout finalize after repair: " + messages)
    journal.complete()
    result = {"schema": SCHEMA, "operation": "promote", "operation_id": journal.operation_id,
              "merge_commit": merge_commit, "strategy": strategy,
              "mode": candidate["mode"], "scope": "merge-main",
              "deploy_prod_authorized": False, "push_authorized": False,
              "finalized": True, "cleanup_pending": True}
    if candidate["mode"] == "cooperative-ci":
        result["server_enforcement"] = "not-server-enforced"
        result["warning"] = "cooperative-ci validates online evidence but provides no server-side merge enforcement guarantee"
    return result


def _execute_feature_merge(main_repo, candidate, strategy):
    if not _clean(main_repo):
        raise DeliveryError("main worktree must be clean before feature promote")
    main_before = candidate["main_head"]
    source = candidate["candidate"]
    if _oid(main_repo, "HEAD") != main_before:
        raise DeliveryError("main advanced after freeze; synchronize, refreeze and revalidate")
    merge_tree = _git(main_repo, "merge-tree", "--write-tree", main_before, source).stdout.splitlines()[0].strip()
    if merge_tree != candidate["candidate_tree"]:
        raise DeliveryError("merge-tree result differs from candidate tree; extra edits are rejected")
    if strategy == "no-ff":
        parents = [main_before, source]
        message = "Merge %s into %s\n" % (candidate["integration_branch"], candidate["main_branch"])
    elif strategy == "squash":
        parents = [main_before]
        message = "Merge %s (squash)\n" % candidate["integration_branch"]
    else:
        raise DeliveryError("strategy must be no-ff or squash")
    commit = _commit_tree(main_repo, merge_tree, parents, message)
    _cas_update_ref(main_repo, candidate["main_branch"], commit, main_before)
    _materialize_cas_result(main_repo, commit)
    return {"repo": str(main_repo), "commit": commit, "strategy": strategy}


def promote(candidate_path, candidate_repo, repo, *, strategy=None, run_id=None,
            attempt=None, event=None, provider=None, repository=None, base_url=None,
            workflow=None, artifact=None, token_env=None, timeout=10.0) -> dict:
    """Promote a feature candidate; a ticket context is not a feature authority."""
    candidate_file = Path(candidate_path).resolve()
    fdir, round_name, integ = _candidate_location(candidate_file)
    candidate = _load_json_strict(candidate_file)
    if candidate.get("schema") != "agent-dev.feature-candidate/v2":
        raise DeliveryError("feature promote requires a v2 feature candidate")
    candidate_repo = Path(candidate_repo).resolve()
    main_repo = Path(repo).resolve()
    if _oid(candidate_repo, "HEAD") != candidate.get("candidate") or _oid(candidate_repo, "HEAD", tree=True) != candidate.get("candidate_tree"):
        raise DeliveryError("integration HEAD/tree differ from feature candidate")
    if _git(main_repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != candidate.get("main_branch"):
        raise DeliveryError("main worktree is not on candidate.main_branch")
    if _feature_operation_path(integ).is_file():
        existing = _module("state_store").OperationJournal.load(_feature_operation_path(integ))
        if _journalize_existing_feature_merge(candidate, main_repo, existing, strategy):
            # Only a journaled/verified merge side effect can bypass reauthorization.
            return _resume_feature(candidate, fdir, integ, candidate_repo, main_repo, strategy,
                                   journal=existing)
        _validate_feature_resume_identity(candidate, candidate_repo, main_repo, existing, strategy, integ,
                                          allow_evidence_change=True)
    strategy = strategy or "no-ff"
    mode = candidate.get("mode")
    if mode not in ("local-human", "server-enforced", "cooperative-ci"):
        raise DeliveryError("candidate mode is missing/invalid")
    fc = _module("feature_closeout")
    config = fc.load_config(fdir)
    if fc.feature_mode(config) != mode:
        raise DeliveryError("configured closeout mode differs from candidate; automatic downgrade is forbidden")
    proof = None
    settings = None
    if mode in ("server-enforced", "cooperative-ci"):
        if not run_id:
            raise DeliveryError("CI feature promote requires --run-id for the online run")
        cp = _module("ci_proof")
        settings = _ci_settings(config, {
            "provider": provider, "repository": repository, "base_url": base_url,
            "workflow": workflow, "artifact": artifact, "token_env": token_env,
        })
        cap = cp.capabilities(settings["provider"], settings["repository"],
                              base_url=settings["base_url"], workflow=settings["workflow"],
                              branch=candidate["main_branch"], mode=mode,
                              token=settings["token"], timeout=timeout)
        cp.require_capabilities(cap, mode)
        proof = cp.fetch_proof(
            settings["provider"], settings["repository"], settings["workflow"], run_id,
            base_url=settings["base_url"], token=settings["token"], repo=candidate_repo,
            expected_ref=candidate.get("candidate_ref") or "refs/heads/" + candidate["integration_branch"],
            expected_commit=candidate["candidate"], expected_tree=candidate["candidate_tree"],
            expected_attempt=attempt, expected_event=event,
            artifact_name=settings["artifact"], timeout=timeout,
        )
    elif any((run_id, provider, repository, workflow)):
        raise DeliveryError("local-human promote does not accept CI run/provider shortcuts")
    args = SimpleNamespace(feature_dir=str(fdir), repo=str(candidate_repo), round=round_name,
                           phase="merge", record=False, main_repo=str(main_repo))
    try:
        authorization = fc.authorize_merge(args, online_proof=proof)
    except Exception as exc:
        raise DeliveryError("feature business/conformance authorization failed: %s" % exc) from exc
    if mode in ("server-enforced", "cooperative-ci"):
        cp = _module("ci_proof")
        if mode == "server-enforced":
            # Re-observe server enforcement as well as the run immediately before
            # the side effect; unknown protection/CAS remains fail-closed.
            cap = cp.capabilities(settings["provider"], settings["repository"],
                                  base_url=settings["base_url"], workflow=settings["workflow"],
                                  branch=candidate["main_branch"], mode=mode,
                                  token=settings["token"], timeout=timeout)
            cp.require_capabilities(cap, mode)
        fresh = cp.recheck_proof(
            proof, base_url=settings["base_url"], token=settings["token"], repo=candidate_repo,
            expected_ref=candidate.get("candidate_ref") or "refs/heads/" + candidate["integration_branch"],
            expected_commit=candidate["candidate"], expected_tree=candidate["candidate_tree"],
            expected_event=event, timeout=timeout,
        )
        authorization["online_proof"] = fresh
        authorization["online_proof_sha256"] = _digest(fresh)
    evidence = _feature_evidence(integ)
    operation_id = _feature_operation_id(candidate, authorization.get("online_proof"), evidence)
    authorization["operation_id"] = operation_id
    journal, expected = _feature_journal(integ, operation_id, candidate, strategy, evidence)
    return _resume_feature(candidate, fdir, integ, candidate_repo, main_repo, strategy,
                           journal=journal, expected=expected, authorization=authorization,
                           round_name=round_name)


class MergeRequestAdapter:
    """Expected-source-SHA MR merge with mandatory post-merge API reread."""

    TARGET_CAS_SUPPORTED = False

    def __init__(self, api):
        self.api = api

    def find(self, source_branch, target_branch, source_sha=None):
        raise NotImplementedError

    def _check_mode(self, mode):
        if mode == "server-enforced" and not self.TARGET_CAS_SUPPORTED:
            raise DeliveryError("server-enforced remote MR requires target-old-OID CAS; official provider API does not support it")

    def merge(self, reference, *, source_sha, target_branch, target_before=None, mode=None):
        self._check_mode(mode)
        raise NotImplementedError

    @staticmethod
    def _branch_commit(value):
        commit = value.get("commit") if isinstance(value, dict) else None
        if isinstance(commit, dict):
            return commit.get("id") or commit.get("sha")
        return None


class GiteaMRAdapter(MergeRequestAdapter):
    def find(self, source_branch, target_branch, source_sha=None):
        values = self.api.get_json(self.api.repo_path + "/pulls", query={"state": "all", "limit": 50})
        if not isinstance(values, list):
            raise DeliveryError("Gitea MR discovery response is not an array")
        matches = [item for item in values if isinstance(item, dict)
                   and (item.get("head") or {}).get("ref") == source_branch
                   and (item.get("base") or {}).get("ref") == target_branch
                   and (source_sha is None or (item.get("head") or {}).get("sha") == source_sha)]
        if len(matches) != 1:
            raise DeliveryError("expected exactly one Gitea MR for frozen source/target/SHA; found %d" % len(matches))
        return str(matches[0].get("number", matches[0].get("id")))

    def _post_merge(self, path, target_branch):
        after = self.api.get_json(path)
        commit = after.get("merge_commit_sha")
        if after.get("merged") is not True or not commit:
            raise DeliveryError("Gitea post-merge API reread did not prove merged state/commit")
        branch_path = self.api.repo_path + "/branches/" + urllib.parse.quote(target_branch, safe="")
        branch = self.api.get_json(branch_path)
        if self._branch_commit(branch) is None:
            raise DeliveryError("Gitea target branch post-merge reread has no commit")
        self.last_result = {"merge_commit": str(commit),
                            "target_head": self._branch_commit(branch),
                            "target_advanced": self._branch_commit(branch) != commit}
        return str(commit)

    def merge(self, reference, *, source_sha, target_branch, target_before=None, mode=None):
        self._check_mode(mode)
        quoted = urllib.parse.quote(str(reference), safe="")
        path = self.api.repo_path + "/pulls/" + quoted
        before = self.api.get_json(path)
        head = ((before.get("head") or {}).get("sha") or before.get("head_sha"))
        base = ((before.get("base") or {}).get("ref") or before.get("base_branch"))
        if head != source_sha or base != target_branch:
            raise DeliveryError("Gitea MR source/target does not match frozen context")
        if before.get("merged") is True:
            return self._post_merge(path, target_branch)
        if before.get("state") != "open" or before.get("mergeable") is not True:
            raise DeliveryError("Gitea MR is not open/mergeable")
        if target_before is not None:
            branch = self.api.get_json(self.api.repo_path + "/branches/" + urllib.parse.quote(target_branch, safe=""))
            if self._branch_commit(branch) != target_before:
                raise DeliveryError("Gitea target moved before MR merge; rebase is required")
        self.api.request_json(path + "/merge", method="PUT", data={
            "Do": "merge", "expected_head_sha": source_sha, "merge_when_checks_succeed": False,
        })
        return self._post_merge(path, target_branch)


class GitLabMRAdapter(MergeRequestAdapter):
    def find(self, source_branch, target_branch, source_sha=None):
        values = self.api.get_json(self.api.repo_path + "/merge_requests", query={
            "state": "all", "source_branch": source_branch, "target_branch": target_branch,
            "per_page": 50,
        })
        if not isinstance(values, list):
            raise DeliveryError("GitLab MR discovery response is not an array")
        matches = [item for item in values if isinstance(item, dict)
                   and item.get("source_branch") == source_branch and item.get("target_branch") == target_branch
                   and (source_sha is None or item.get("sha") == source_sha)]
        if len(matches) != 1:
            raise DeliveryError("expected exactly one GitLab MR for frozen source/target/SHA; found %d" % len(matches))
        return str(matches[0].get("iid"))

    def _post_merge(self, path, target_branch, source_sha):
        after = self.api.get_json(path)
        commit = after.get("merge_commit_sha") or after.get("squash_commit_sha")
        if after.get("state") != "merged" or after.get("sha") != source_sha or not commit:
            raise DeliveryError("GitLab post-merge API reread did not prove source SHA/merged commit")
        branch_path = self.api.repo_path + "/repository/branches/" + urllib.parse.quote(target_branch, safe="")
        branch = self.api.get_json(branch_path)
        if self._branch_commit(branch) is None:
            raise DeliveryError("GitLab target branch post-merge reread has no commit")
        self.last_result = {"merge_commit": str(commit),
                            "target_head": self._branch_commit(branch),
                            "target_advanced": self._branch_commit(branch) != commit}
        return str(commit)

    def merge(self, reference, *, source_sha, target_branch, target_before=None, mode=None):
        self._check_mode(mode)
        path = self.api.repo_path + "/merge_requests/" + urllib.parse.quote(str(reference), safe="")
        before = self.api.get_json(path)
        if before.get("sha") != source_sha or before.get("target_branch") != target_branch:
            raise DeliveryError("GitLab MR source/target does not match frozen context")
        if before.get("state") == "merged":
            return self._post_merge(path, target_branch, source_sha)
        if before.get("state") != "opened" or before.get("merge_status") not in ("can_be_merged", "mergeable"):
            raise DeliveryError("GitLab MR is not open/mergeable")
        if target_before is not None:
            branch = self.api.get_json(self.api.repo_path + "/repository/branches/" + urllib.parse.quote(target_branch, safe=""))
            if self._branch_commit(branch) != target_before:
                raise DeliveryError("GitLab target moved before MR merge; rebase is required")
        self.api.request_json(path + "/merge", method="PUT", data={
            "sha": source_sha, "squash": False, "should_remove_source_branch": False,
        })
        return self._post_merge(path, target_branch, source_sha)


def mr_adapter(provider, repository, *, base_url, token=None, timeout=10.0):
    api = _module("ci_proof").get_adapter(provider, repository, base_url=base_url, token=token, timeout=timeout)
    if provider == "gitea":
        return GiteaMRAdapter(api)
    if provider == "gitlab":
        return GitLabMRAdapter(api)
    raise DeliveryError("MR provider must be gitea or gitlab")


def discover_operations(repo, operation_id=None) -> dict:
    """Read all ticket/rebase/feature delivery journals without side effects."""
    root = Path(repo).resolve()
    feature_root = root / "dev" / "features"
    paths = set(feature_root.glob("*/*/impl/*/delivery-operation.json"))
    paths.update(feature_root.glob("*/*/impl/*/rebase-operation-*.json"))
    paths.update(feature_root.glob("*/*/integration/**/delivery-promote.json"))
    paths.update(feature_root.glob("*/*/integration/**/delivery-promote-*.json"))
    operations = []
    for path in sorted(paths):
        try:
            if ".superseded." in path.name:
                marker = _load_json_strict(path)
                if operation_id and marker["operation_id"] != operation_id:
                    continue
                operations.append({
                    "operation_id": marker["operation_id"], "kind": "feature",
                    "state": "superseded", "current_step": None,
                    "journal": str(path.parent / marker["journal"]),
                    "candidate_path": str(path.parent.parent / "candidate.json"),
                    "superseded_by": marker["superseded_by"], "historical": True,
                    "ok": True,
                })
                continue
            journal = _module("state_store").OperationJournal.load(path)
            if path.parent.name == "superseded" and path.with_name(path.name.removesuffix(".json") + ".superseded.json").is_file():
                continue  # the explicit superseded marker is the historical record
            if operation_id and journal.operation_id != operation_id:
                continue
            if path.name.startswith("delivery-promote"):
                kind = "feature"
                candidate_dir = path.parent.parent if path.parent.name == "superseded" else path.parent
                candidate = candidate_dir / "candidate.json"
                recovery = {"candidate_path": str(candidate), "main_repo": str(root)}
                try:
                    value = _load_json_strict(candidate)
                    matches = [entry["path"] for entry in _worktrees(root)
                               if entry["branch"] == "refs/heads/" + value["integration_branch"]]
                    if len(matches) == 1:
                        recovery["candidate_repo"] = str(matches[0])
                except Exception as exc:
                    recovery["error"] = str(exc)
            else:
                kind = "rebase" if path.name.startswith("rebase-operation-") else "ticket"
                recovery = {"context": str(path.parent / "ticket-context.json")}
            pending_successor = path.name.endswith(".pending.json")
            operations.append({
                "operation_id": journal.operation_id, "kind": kind,
                "state": "needs-attention" if pending_successor else journal.state,
                "current_step": journal.current_step, "journal": str(path),
                "ok": journal.state == "completed" and not pending_successor,
                "historical": path.parent.name == "superseded", **recovery,
            })
        except Exception as exc:
            operations.append({"operation_id": operation_id, "state": "needs-attention",
                               "journal": str(path), "ok": False, "error": str(exc)})
    unknown_routes = []
    try:
        hd = _module("human_decision")
        unknown_routes = [{"decision_id": item.get("id"), "route_id": (item.get("route") or {}).get("route_id")}
                          for item in hd.list(root, active=True) if item.get("status") == "routing"]
    except Exception as exc:
        operations.append({"operation_id": operation_id, "state": "needs-attention",
                           "ok": False, "error": "human decision route discovery: %s" % exc})
    if operation_id and not operations:
        return {"kind": "delivery", "ok": False, "applied": False,
                "state": "needs-attention", "operations": [], "unknown_routes": unknown_routes,
                "error": "delivery operation not found: %s" % operation_id}
    ok = all(item["ok"] for item in operations) and not unknown_routes
    return {"kind": "delivery", "ok": ok, "applied": False, "operations": operations,
            "unknown_routes": unknown_routes, "state": "observed" if ok else "needs-attention"}


def reconcile_operations(repo, operation_id=None, apply=False, *, context=None,
                         candidate_path=None, candidate_repo=None, main_repo=None,
                         strategy=None) -> dict:
    """Aggregate reads or apply one explicitly addressed delivery transaction."""
    if not apply:
        return discover_operations(repo, operation_id)
    result = reconcile(context, candidate_path=candidate_path,
                       candidate_repo=candidate_repo, repo=main_repo, strategy=strategy)
    state = result.get("state", "completed" if result.get("status") == "done" else "needs-attention")
    ok = state == "completed"
    return {"kind": "delivery", "ok": ok, "applied": True,
            "state": state if ok else "needs-attention", "operations": [result],
            "unknown_routes": []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start_p = sub.add_parser("start")
    start_p.add_argument("--issue", required=True)
    start_p.add_argument("--repo", required=True)
    for name in ("rebase", "merge"):
        item = sub.add_parser(name)
        item.add_argument("--context", required=True)
    reconcile_p = sub.add_parser("reconcile")
    reconcile_p.add_argument("--context")
    reconcile_p.add_argument("--candidate")
    reconcile_p.add_argument("--candidate-repo")
    reconcile_p.add_argument("--repo")
    reconcile_p.add_argument("--strategy", choices=("no-ff", "squash"))
    promote_p = sub.add_parser("promote")
    promote_p.add_argument("--candidate", required=True)
    promote_p.add_argument("--candidate-repo", required=True)
    promote_p.add_argument("--repo", required=True)
    promote_p.add_argument("--strategy", choices=("no-ff", "squash"))
    promote_p.add_argument("--run-id")
    promote_p.add_argument("--attempt", type=int)
    promote_p.add_argument("--event")
    promote_p.add_argument("--provider", choices=("gitea", "gitlab"))
    promote_p.add_argument("--repository")
    promote_p.add_argument("--base-url")
    promote_p.add_argument("--workflow")
    promote_p.add_argument("--artifact")
    promote_p.add_argument("--token-env")
    promote_p.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            result = start(args.issue, args.repo)
        elif args.command == "rebase":
            result = rebase(args.context)
        elif args.command == "merge":
            result = merge(args.context)
        elif args.command == "reconcile":
            result = reconcile(args.context, candidate_path=args.candidate,
                               candidate_repo=args.candidate_repo, repo=args.repo,
                               strategy=args.strategy)
        else:
            result = promote(
                args.candidate, args.candidate_repo, args.repo, strategy=args.strategy,
                run_id=args.run_id, attempt=args.attempt, event=args.event,
                provider=args.provider, repository=args.repository, base_url=args.base_url,
                workflow=args.workflow, artifact=args.artifact, token_env=args.token_env,
                timeout=args.timeout,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str))
        return 0
    except (DeliveryError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print("delivery: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
