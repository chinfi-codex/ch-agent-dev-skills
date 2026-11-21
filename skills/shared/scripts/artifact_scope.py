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
