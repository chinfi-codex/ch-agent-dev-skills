"""Canonical artifact scopes: stable feature roots and optional campaign children.

Legacy feature directories remain readable in place. No migration, latest-file
selection or fallback from a campaign to a parent plan is performed here.
"""
from pathlib import Path
import re


def validate_key(value):
    if not isinstance(value, str):
        raise ValueError("artifact scope must be a relative string")
    parts = value.split("/")
    if (len(parts) not in (2, 4) or (len(parts) == 4 and parts[2] != "campaigns")
            or any(not x or x in (".", "..") or x != x.strip()
                   or re.search(r"[\\\x00-\x1f]", x) for x in parts)):
        raise ValueError("scope must be module/feature[/campaigns/campaign-id]")
    return value


def campaign_id(directory):
    path = Path(directory)
    return (path.name if len(path.parents) >= 5 and path.parent.name == "campaigns"
            and path.parents[3].name == "features"
            and path.parents[4].name in ("dev", "docs") else None)


def feature_root(directory):
    path = Path(directory)
    return path.parent.parent if campaign_id(path) else path


def feature_key(directory):
    root = feature_root(directory)
    return validate_key(root.parent.name + "/" + root.name)


def scope_key(directory):
    key = feature_key(directory)
    campaign = campaign_id(directory)
    return validate_key(key + "/campaigns/" + campaign if campaign else key)


def _branch_segment(value):
    # Preserve readable Chinese names while escaping spaces, dots, percent signs
    # and other Git-ref punctuation injectively (no lossy slug normalization).
    return "".join(char if char.isalnum() or char in "-_" else
                   "".join("%%%02X" % byte for byte in char.encode("utf-8"))
                   for char in value)


def branch_key(directory, legacy_slug=None):
    if campaign_id(directory):
        return "/".join(_branch_segment(part) for part in
                        (*feature_key(directory).split("/"), campaign_id(directory)))
    return legacy_slug or Path(directory).name


def worktree_path(repo, directory, issue_id):
    base = Path(repo) / ".worktrees"
    if campaign_id(directory):
        base /= branch_key(directory)
    return base / issue_id


def scopes(repo):
    """Only authoritative direct roots, never research/transfer/archive copies."""
    root = Path(repo) / "dev" / "features"
    for feature in sorted(root.glob("*/*")):
        if not feature.is_dir() or feature.is_symlink():
            continue
        yield feature
        for campaign in sorted((feature / "campaigns").glob("*")):
            if campaign.is_dir() and not campaign.is_symlink():
                validate_key(scope_key(campaign))
                yield campaign


def scoped_glob(repo, pattern):
    for directory in scopes(repo):
        yield from directory.glob(pattern)


def validate_ticket(directory, frontmatter):
    campaign = campaign_id(directory)
    declared = frontmatter.get("campaign-id")
    if campaign:
        if declared != campaign:
            raise ValueError("ticket campaign-id must match its campaign directory")
        if frontmatter.get("feature-slug") != feature_root(directory).name:
            raise ValueError("feature-slug must remain the stable feature directory name")
    elif declared:
        raise ValueError("campaign ticket must live under feature/campaigns/<campaign-id>/issues")


def decision_key(scope):
    feature = validate_key(scope["feature"])
    campaign = scope.get("campaign")
    if campaign:
        if len(feature.split("/")) != 2:
            raise ValueError("scope.feature must be a stable module/feature")
        return validate_key(feature + "/campaigns/" + campaign)
    return feature


def decision_scope(directory):
    value = {"feature": feature_key(directory)}
    if campaign_id(directory):
        value["campaign"] = campaign_id(directory)
    return value


STACK_BASE = "stack-base.json"
STACK_SOURCE_SCHEMA = "agent-dev.stack-source/v1"
STACK_BASE_SCHEMA = "agent-dev.stack-base/v1"


def _stack_git(repo, *args, check=True):
    import subprocess

    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          text=True, capture_output=True)


def _stack_json(path):
    import json

    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate key")
        return result

    if path.resolve() != path or not path.is_file():
        raise ValueError("stack-base requires a canonical regular artifact: %s" % path)
    data = path.read_bytes()
    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("stack-base artifact must be an object")
    return value, data


def stack_declared(directory):
    """Preserve the acceptance barrier when a declaration is deleted."""
    import json

    directory = Path(directory)
    marker = directory / STACK_BASE
    if marker.exists() or marker.is_symlink():
        return True
    if not campaign_id(directory):
        return False
    for path in (directory / "impl").glob("issue-*/ticket-context.json"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            context = json.loads(path.read_bytes())
        except (ValueError, OSError):
            continue
        if isinstance(context, dict) and "stack_base" in context:
            return True
    return False


def stack_source_path(scope, commit):
    return Path("dev/features") / validate_key(scope) / "integration" / "stack-sources" / (commit + ".json")


def _stack_commit(repo, ref):
    resolved = _stack_git(repo, "rev-parse", "--verify", ref + "^{commit}", check=False)
    if resolved.returncode:
        raise ValueError("stack-base Git reference is missing: %s" % ref)
    return resolved.stdout.strip()


def _stack_ancestor(repo, ancestor, descendant):
    return _stack_git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def stack_source_receipt(repo, directory, ref, commit, main_branch):
    """Derive the development-only receipt from a named local integration ref.

    The predecessor must sit at the exact ref tip, descend from recorded main
    history (its merge base) and not already be contained in main.  The main
    ancestor is a merge base, so a main that advanced after the predecessor
    froze does not invalidate the receipt later.
    """
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("stack-base commit must be a complete Git identity")
    head = _stack_commit(repo, ref)
    main = _stack_commit(repo, "refs/heads/" + main_branch)
    merge_base = _stack_git(repo, "merge-base", main, commit).stdout.strip()
    if (head != commit or not re.fullmatch(r"[a-f0-9]{40}", merge_base)
            or merge_base == commit
            or not _stack_ancestor(repo, merge_base, commit)):
        raise ValueError("stack-base source must be the exact integration tip descended from main")
    return {
        "schema": STACK_SOURCE_SCHEMA, "scope": scope_key(directory), "ref": ref,
        "commit": commit, "tree": _stack_git(repo, "rev-parse", commit + "^{tree}").stdout.strip(),
        "main_ref": "refs/heads/" + main_branch, "main_ancestor": merge_base, "status": "not-accepted",
    }


def checked_stack_base(repo, directory, *, target, main_branch):
    import hashlib

    repo, directory = Path(repo).resolve(), Path(directory).resolve()
    marker = directory / STACK_BASE
    if not marker.exists() and not marker.is_symlink():
        if stack_declared(directory):
            raise ValueError("stack-base declaration was removed from an active campaign")
        return None, None
    if not campaign_id(directory) or directory != repo / "dev/features" / scope_key(directory):
        raise ValueError("stack-base requires its own canonical campaign directory")
    declaration, raw = _stack_json(marker)
    keys = {"schema", "consumer_scope", "predecessor_scope", "predecessor_ref",
            "commit", "tree", "source_path", "source_sha256"}
    if (set(declaration) != keys or declaration["schema"] != STACK_BASE_SCHEMA
            or declaration["consumer_scope"] != scope_key(directory)
            or declaration["predecessor_scope"] == scope_key(directory)):
        raise ValueError("stack-base campaign or schema mismatch")
    commit, tree = declaration["commit"], declaration["tree"]
    ref = declaration["predecessor_ref"]
    if (any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value)
            for value in (commit, tree))
            or not isinstance(ref, str) or not ref.startswith("refs/heads/")
            or ref in ("refs/heads/" + target, "refs/heads/" + main_branch)
            or _stack_git(repo, "check-ref-format", ref, check=False).returncode):
        raise ValueError("stack-base ref, commit or tree is invalid")
    receipt_path = stack_source_path(declaration["predecessor_scope"], commit)
    if declaration["source_path"] != receipt_path.as_posix():
        raise ValueError("stack-base receipt crosses its predecessor scope")
    receipt, receipt_raw = _stack_json(repo / receipt_path)
    if (hashlib.sha256(receipt_raw).hexdigest() != declaration["source_sha256"]
            or receipt != {
                "schema": STACK_SOURCE_SCHEMA, "scope": declaration["predecessor_scope"],
                "ref": ref, "commit": commit, "tree": tree,
                "main_ref": "refs/heads/" + main_branch,
                "main_ancestor": receipt.get("main_ancestor"), "status": "not-accepted",
            }):
        raise ValueError("stack-base source receipt differs from its declaration")
    prior_main = receipt["main_ancestor"]
    if (not isinstance(prior_main, str) or not re.fullmatch(r"[a-f0-9]{40}", prior_main)
            or prior_main == commit or not _stack_ancestor(repo, prior_main, commit)
            or not _stack_ancestor(repo, prior_main, _stack_commit(repo, receipt["main_ref"]))):
        raise ValueError("stack-base source lacks its recorded main ancestry")
    tip = _stack_commit(repo, ref)
    target_tip = _stack_commit(repo, "refs/heads/" + target)
    if (not _stack_ancestor(repo, commit, tip) or not _stack_ancestor(repo, commit, target_tip)
            or _stack_git(repo, "rev-parse", commit + "^{tree}").stdout.strip() != tree):
        raise ValueError("stack-base fixed source is unreachable or its tree differs")
    return {**declaration, "declaration_sha256": hashlib.sha256(raw).hexdigest()}, tip
