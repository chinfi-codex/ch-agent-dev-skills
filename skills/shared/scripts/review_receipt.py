#!/usr/bin/env python3
"""Create and validate independent, fresh v2 review receipts.

Lazy shared API contract:
  ticket_context.parse_issue/create_context/load_context and select_conformance
  agents_config.load(config_path) -> validated v2 mapping
  dispatch_config.resolve(config["dispatch"], host, role, tier="default")

``create`` takes a candidate JSON containing review findings, coverage,
identities and trust material.  Cryptographic content hashes and Git identities
are computed here, never accepted from candidate override fields.  ``validate``
recomputes all of them and fails closed on stale/tampered artifacts.  Host trust
also requires a live host_adapters native-session verification, supplied by the
dispatcher through impl/<issue>/host-probes.json (or the explicit API/CLI input).
The native session must exist, be independent, and bind this exact review action.

Feature-level receipts (``create-feature`` / ``validate-feature``) bind the
whole ``integration/candidate.json`` candidate: base=main_head,
head=candidate, tree=candidate_tree, the full ``git diff --binary`` digest,
acceptance-plan hash, every diff file's review coverage, and every ticket
implementer's native session/agent.  Review rounds chain through
``round`` + ``previous_receipt_sha256``; a changed candidate invalidates old
receipts.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "agent-dev.review-receipt/v2"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "review-receipt.schema.json"
FEATURE_SCHEMA = "agent-dev.feature-review-receipt/v1"
FEATURE_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "feature-review-receipt.schema.json"
HOST_PROBES_NAME = "host-probes.json"
HOST_PROOF_SCHEMA = "agent-dev.host-capability-receipt/v1"
FEATURE_PROBES_DIR = "integration"
FEATURE_RECEIPT_NAME = "review-receipt.json"
FEATURE_PROOF_NAME = "feature-review-host-proof.json"
FEATURE_REVIEW_SIBLINGS = (
    "integration/feature-review.json",
    "integration/review-candidate.json",
    "integration/feature-review-candidate.json",
    "integration/review-input.json",
    "reviews/feature-review.json",
    "reviews/review-candidate.json",
    "reviews/feature-review-candidate.json",
    "reviews/review-input.json",
)
HEX64 = re.compile(r"[0-9a-f]{64}")


def _sibling(name: str):
    directory = str(Path(__file__).resolve().parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module(name)


class ReceiptError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _git(repo: Path, *args: str, binary=False):
    result = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(repo), *args],
                            capture_output=True, text=not binary)
    if result.returncode:
        raise ReceiptError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout


def _root_for_issue(issue: Path) -> Path:
    for parent in issue.resolve().parents:
        if (parent / "dev" / "agents-config.md").is_file():
            return parent
    raise ReceiptError("cannot find dev/agents-config.md above issue")


def _load_inputs(issue: Path, repo: Path):
    try:
        tc = _sibling("ticket_context")
        ac = _sibling("agents_config")
    except ImportError as exc:
        raise ReceiptError("ticket_context/agents_config are required: %s" % exc)
    parsed = tc.parse_issue(issue)
    issue_id = parsed.get("frontmatter", {}).get("issue-id")
    if not issue_id:
        raise ReceiptError("ticket has no issue-id")
    impl_dir = issue.parent.parent / "impl" / issue_id
    frozen = impl_dir / "ticket-context.json"
    if frozen.is_file():
        context = tc.load_context(frozen)
    else:
        context = tc.create_context(issue, repo)
    root = _root_for_issue(issue)
    config_value = context.get("config_path", "dev/agents-config.md")
    config_path = Path(config_value)
    if not config_path.is_absolute():
        candidate = repo / config_path
        config_path = candidate if candidate.is_file() else root / config_path
    config = ac.load(config_path)
    return parsed, context, config, impl_dir.resolve()


def _artifact(path_value, base: Path):
    path = Path(path_value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _artifact_record(path: Path):
    if not path.is_file():
        raise ReceiptError("artifact is not a file: %s" % path)
    return {"path": str(path), "sha256": _sha(path)}


def _preview_paths(value):
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict):
        entries = value.get("files", value.get("preview"))
        if entries is None and isinstance(value.get("paths"), list):
            entries = value["paths"]
    else:
        entries = None
    if not isinstance(entries, list):
        raise ReceiptError("coverage preview must be a list or an object with files[]")
    paths = []
    for entry in entries:
        path = entry if isinstance(entry, str) else entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path:
            raise ReceiptError("coverage preview contains an entry without path")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise ReceiptError("coverage preview contains duplicate paths")
    return sorted(paths)


def _changed_paths(repo: Path, base: str, head: str):
    output = _git(repo, "diff", "--name-only", "-M", base, head, "--")
    return sorted(path for path in output.splitlines() if path)


def _diff_sha(repo: Path, base: str, head: str):
    return hashlib.sha256(_git(repo, "diff", "--binary", "--full-index", base, head, "--", binary=True)).hexdigest()


def _schema_errors(receipt):
    try:
        js = _sibling("json_schema")
        return js.load_validate(SCHEMA_PATH, receipt)
    except (ImportError, ValueError, OSError) as exc:
        return ["receipt schema validation unavailable: %s" % exc]


def _validate_identity(identity, expected_role, dispatch, label):
    errors = []
    try:
        dc = _sibling("dispatch_config")
        tier = identity.get("tier", "default")
        resolved = dc.resolve(dispatch, identity["host"], expected_role, tier)
    except (ImportError, ValueError, KeyError, TypeError) as exc:
        return ["%s dispatch resolve failed: %s" % (label, exc)]
    for key in ("host", "model", "effort"):
        if identity.get(key) != resolved.get(key):
            errors.append("%s %s does not match dispatch_config.resolve" % (label, key))
    if identity.get("role") != expected_role:
        errors.append("%s role must be %s" % (label, expected_role))
    native = resolved.get("native", {})
    expected_agent = native.get("subagent_type", native.get("agent_type"))
    if expected_agent and identity.get("agent") != expected_agent:
        errors.append("%s agent must be dispatched as %s" % (label, expected_agent))
    return errors


def _load_host_probes(value, impl_dir: Path):
    if isinstance(value, dict):
        return value
    path = Path(value) if value else impl_dir / HOST_PROBES_NAME
    if not path.is_file():
        return {}
    try:
        probes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReceiptError("host probes unavailable/invalid: %s" % exc)
    if not isinstance(probes, dict):
        raise ReceiptError("host probes must be a native-proof object/mapping")
    return probes


def _review_action(receipt):
    return {
        "review_id": "%s-r%d-%s" % (receipt["issue"], receipt["round"], receipt["report_sha256"][:16]),
        "round": receipt["round"], "issue": receipt["issue"],
        "head": receipt["head"], "tree": receipt["tree"],
        "report_sha256": receipt["report_sha256"],
    }


def _native_proof_input(receipt, probes):
    proof = copy.deepcopy(probes.get("native_proof", probes))
    reviewer = receipt["reviewer"]
    proof["host"] = reviewer["host"]
    proof["session"] = reviewer["session"]
    # The probe supplies native evidence/hash, while the receipt supplies the
    # immutable action it must prove.  A different review action cannot be
    # authorized by replaying a generic independent-session capability.
    proof["review"] = _review_action(receipt)
    if receipt.get("attestation"):
        proof["operator_attestation"] = copy.deepcopy(receipt["attestation"])
    return proof


def _capability_receipt(receipt, config, probes):
    reviewer = receipt["reviewer"]
    native_proof = _native_proof_input(receipt, probes)
    adapters = _sibling("host_adapters")
    try:
        result = adapters.verify_review_session(
            reviewer["host"], reviewer, receipt["implementer"], native_proof, config)
    except (adapters.HostCapabilityError, ValueError, OSError) as exc:
        raise ReceiptError("independent_session capability is not available/proven: %s" % exc)
    return {
        "schema": HOST_PROOF_SCHEMA, "verified": True, "attested_by": "host",
        "identity": copy.deepcopy(reviewer), "native_proof": native_proof,
        "verification": result,
    }


def _produce_native_proof(receipt, config, probes):
    """Attach this exact review through the real host API, never locally forge it."""
    adapters = _sibling("host_adapters")
    if not hasattr(adapters, "write_review_binding"):
        raise ReceiptError("host_adapters.write_review_binding is required for receipt finalization")
    try:
        produced = adapters.write_review_binding(
            receipt["reviewer"]["host"], receipt["reviewer"], receipt["implementer"],
            _review_action(receipt), config)
    except (adapters.HostCapabilityError, ValueError, OSError) as exc:
        raise ReceiptError("cannot attach review through native host: %s" % exc)
    if not isinstance(produced, dict):
        raise ReceiptError("write_review_binding returned a non-object proof")
    if produced.get("written") is not True or produced.get("requires_native_apply") is not False:
        raise ReceiptError("native review binding is not persisted/readable yet")
    record = produced.get("native_session")
    review = produced.get("review")
    if not isinstance(record, dict) or not isinstance(review, dict):
        raise ReceiptError("write_review_binding lacks native read-back session/review")
    merged = copy.deepcopy(probes)
    merged.update(produced)
    merged["native_hash"] = adapters._native_digest(record, review)
    return merged


def _validate_trust(receipt, config, receipt_path: Path, probes):
    errors = []
    enforcement = config.get("enforcement") or {}
    required = enforcement.get("review-trust")
    if required and receipt["trust"] != required:
        errors.append("trust must be %s by enforcement.review-trust" % required)
    needs_probe = receipt["trust"] == "host-verified" or enforcement.get("require-host-capabilities") is not False
    expected = None
    if needs_probe:
        try:
            expected = _capability_receipt(receipt, config, probes)
        except ReceiptError as exc:
            errors.append(str(exc))
    if receipt["trust"] == "host-verified":
        proof = receipt.get("host_verification")
        if not proof:
            return errors + ["host-verified receipt lacks host proof; operator attestation cannot substitute"]
        reviewer = receipt["reviewer"]
        if proof["host"] != reviewer["host"] or proof["session"] != reviewer["session"]:
            errors.append("host proof identity does not match reviewer")
        path = _artifact(proof["proof"], receipt_path.parent)
        try:
            if _sha(path) != proof["proof_sha256"]:
                errors.append("host proof hash mismatch")
            value = json.loads(path.read_text(encoding="utf-8"))
            if expected is not None and value != expected:
                errors.append("host proof does not match the live host_adapters probe receipt")
        except (OSError, ValueError) as exc:
            errors.append("host proof unavailable/invalid: %s" % exc)
        if receipt.get("attestation") is not None:
            errors.append("host-verified receipt must not substitute operator attestation")
    else:
        attestation = receipt.get("attestation")
        if not attestation:
            errors.append("operator-attested receipt lacks operator attestation")
        elif "independent review" not in attestation["statement"].lower() and "独立评审" not in attestation["statement"]:
            errors.append("operator attestation must explicitly attest independent review")
        if receipt.get("host_verification") is not None:
            errors.append("operator-attested receipt must not claim host verification")
    return errors


def _validate_coverage(receipt, repo: Path, receipt_path: Path):
    errors = []
    files = receipt["coverage"]["files"]
    paths = [entry["path"] for entry in files]
    if len(paths) != len(set(paths)):
        errors.append("receipt coverage contains duplicate paths")
    reviewed = sum(entry["status"] == "reviewed" for entry in files)
    skipped = sum(entry["status"] == "skipped" for entry in files)
    if (receipt["total_files"], receipt["reviewed_files"], receipt["skipped_files"]) != (len(files), reviewed, skipped):
        errors.append("total/reviewed/skipped counts do not match coverage")
    for entry in files:
        if entry["status"] == "skipped" and not entry["reason"].strip():
            errors.append("skipped coverage lacks reason: %s" % entry["path"])
    try:
        preview_path = _artifact(receipt["artifacts"]["coverage_preview"]["path"], receipt_path.parent)
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        expected = _preview_paths(preview)
        if sorted(paths) != expected:
            errors.append("coverage does not include the exact preview full set")
        actual_diff = _changed_paths(repo, receipt["base"], receipt["head"])
        if expected != actual_diff:
            errors.append("coverage preview does not equal the ticket diff full set")
    except (OSError, ValueError, ReceiptError, KeyError) as exc:
        errors.append("coverage preview invalid: %s" % exc)
    return errors


def _validate_artifacts(receipt, receipt_path: Path):
    errors = []
    expected_hashes = {
        "report": receipt["report_sha256"],
        "process_check_review": receipt["process_check_review_sha256"],
        "coverage_preview": receipt["coverage"]["preview_sha256"],
    }
    for key, digest in expected_hashes.items():
        artifact = receipt["artifacts"][key]
        if artifact["sha256"] != digest:
            errors.append("%s hash fields disagree" % key)
        try:
            path = _artifact(artifact["path"], receipt_path.parent)
            if not path.is_file() or _sha(path) != digest:
                errors.append("%s is missing or tampered" % key)
        except OSError as exc:
            errors.append("%s unreadable: %s" % (key, exc))
    try:
        process_path = _artifact(receipt["artifacts"]["process_check_review"]["path"], receipt_path.parent)
        process = json.loads(process_path.read_text(encoding="utf-8"))
        if process.get("issue") != receipt["issue"] or process.get("phase") != "review":
            errors.append("process-check-review issue/phase mismatch")
        if process.get("head") != receipt["head"] or process.get("tree") != receipt["tree"]:
            errors.append("process-check-review head/tree stale")
        if process.get("verdict") != "PASS":
            errors.append("process-check-review is not PASS")
    except (OSError, ValueError) as exc:
        errors.append("process-check-review invalid JSON: %s" % exc)
    return errors


def _validate_previous(receipt, receipt_path: Path):
    previous = receipt["previous_receipt"]
    if receipt["round"] == 1:
        return [] if previous is None else ["round 1 must not reference previous receipt"]
    if previous is None:
        return ["round >1 requires previous receipt"]
    try:
        path = _artifact(previous["path"], receipt_path.parent)
        if _sha(path) != previous["sha256"]:
            return ["previous receipt hash mismatch"]
        value = json.loads(path.read_text(encoding="utf-8"))
        errors = _schema_errors(value)
        if value.get("round") != receipt["round"] - 1 or value.get("issue") != receipt["issue"]:
            errors.append("previous receipt round/issue chain mismatch")
        return errors
    except (OSError, ValueError) as exc:
        return ["previous receipt invalid: %s" % exc]


def validate_receipt(receipt, issue_path, repo, receipt_path=None, host_probes=None):
    """Return all semantic/freshness errors for an already loaded receipt."""
    issue_path = Path(issue_path).resolve()
    repo = Path(repo).resolve()
    receipt_path = Path(receipt_path).resolve() if receipt_path else issue_path.parent.parent / "impl" / str(receipt.get("issue", "")) / "review-receipt.json"
    errors = _schema_errors(receipt)
    if errors:
        return errors
    try:
        parsed, context, config, impl_dir = _load_inputs(issue_path, repo)
        probes = _load_host_probes(host_probes, impl_dir)
        issue_id = parsed["frontmatter"]["issue-id"]
        if receipt["issue"] != issue_id:
            errors.append("receipt issue mismatch")
        if receipt["base"] != context["base"]:
            errors.append("receipt base differs from frozen ticket context")
        target = context.get("target_branch")
        if target:
            _git(repo, "rev-parse", "--verify", target + "^{commit}")
        actual_head = _git(repo, "rev-parse", "HEAD").strip()
        actual_tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
        if receipt["head"] != actual_head or receipt["tree"] != actual_tree:
            errors.append("receipt head/tree is stale")
        if receipt["diff_sha256"] != _diff_sha(repo, receipt["base"], receipt["head"]):
            errors.append("receipt diff hash mismatch")
        dispatch = config["dispatch"]
        errors.extend(_validate_identity(receipt["implementer"], "development", dispatch, "implementer"))
        errors.extend(_validate_identity(receipt["reviewer"], "review", dispatch, "reviewer"))
        impl = receipt["implementer"]
        reviewer = receipt["reviewer"]
        if impl["host"] == reviewer["host"] and impl["session"] == reviewer["session"]:
            errors.append("reviewer and implementer must have different sessions")
        if impl["host"] == reviewer["host"] and impl["agent"] == reviewer["agent"]:
            errors.append("reviewer and implementer must be different agents")
        errors.extend(_validate_trust(receipt, config, receipt_path, probes))
        errors.extend(_validate_artifacts(receipt, receipt_path))
        errors.extend(_validate_coverage(receipt, repo, receipt_path))
        errors.extend(_validate_previous(receipt, receipt_path))
        verification_path = impl_dir / "verification-implementer.json"
        if verification_path.is_file():
            verification = json.loads(verification_path.read_text(encoding="utf-8"))
            verifier = _sibling("verify_issue")
            errors.extend(verifier.validate_verification(
                verification, verification_path, repo, expected_runner="implementer",
                expected_issue=receipt["issue"], require_fast=True))
            if verification.get("base") != context["base"]:
                errors.append("implementer verification base mismatch")
            identity = verification.get("identity")
            if identity and identity != impl:
                errors.append("implementer identity differs from verification runner")
        else:
            errors.append("implementer verification JSON is required")
        for finding in receipt["findings"]:
            if finding["severity"] in {"critical", "high"}:
                if finding["status"] != "closed" or not finding["closure"].strip():
                    errors.append("critical/high finding is not closed with evidence: %s" % finding["id"])
    except (ReceiptError, ImportError, KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        errors.append("receipt validation failed closed: %s" % exc)
    return errors


def create_receipt(candidate, issue_path, repo, *, report, process_check_review,
                   coverage_preview, output=None, host_probes=None):
    """Populate dynamic hashes/Git fields, validate, and return (receipt, errors)."""
    issue_path = Path(issue_path).resolve()
    repo = Path(repo).resolve()
    receipt = copy.deepcopy(candidate)
    try:
        parsed, context, config, impl_dir = _load_inputs(issue_path, repo)
        probes = _load_host_probes(host_probes, impl_dir)
        head = _git(repo, "rev-parse", "HEAD").strip()
        tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
        output = Path(output).resolve() if output else impl_dir / "review-receipt.json"
        artifacts = receipt.setdefault("artifacts", {})
        artifacts["report"] = _artifact_record(_artifact(report, issue_path.parent))
        artifacts["process_check_review"] = _artifact_record(_artifact(process_check_review, issue_path.parent))
        artifacts["coverage_preview"] = _artifact_record(_artifact(coverage_preview, issue_path.parent))
        preview_value = json.loads(_artifact(coverage_preview, issue_path.parent).read_text(encoding="utf-8"))
        _preview_paths(preview_value)
        previous = receipt.get("previous_receipt")
        if isinstance(previous, str):
            receipt["previous_receipt"] = _artifact_record(_artifact(previous, output.parent))
        elif isinstance(previous, dict) and previous.get("path"):
            receipt["previous_receipt"] = _artifact_record(_artifact(previous["path"], output.parent))
        receipt.update({
            "schema": SCHEMA, "issue": parsed["frontmatter"]["issue-id"],
            "base": context["base"], "head": head, "tree": tree,
            "diff_sha256": _diff_sha(repo, context["base"], head),
            "process_check_review_sha256": artifacts["process_check_review"]["sha256"],
            "report_sha256": artifacts["report"]["sha256"],
            "created_at": _now(),
        })
        receipt.setdefault("coverage", {})["preview_sha256"] = artifacts["coverage_preview"]["sha256"]
        receipt.setdefault("previous_receipt", None)
        receipt.setdefault("semantic_closures", [])
        receipt.setdefault("attestation", None)
        receipt.setdefault("host_verification", None)
        files = receipt.get("coverage", {}).get("files", [])
        receipt["total_files"] = len(files)
        receipt["reviewed_files"] = sum(f.get("status") == "reviewed" for f in files)
        receipt["skipped_files"] = sum(f.get("status") == "skipped" for f in files)
        enforcement = config.get("enforcement") or {}
        required_trust = enforcement.get("review-trust")
        if required_trust and receipt["trust"] != required_trust:
            raise ReceiptError("trust must be %s by enforcement.review-trust" % required_trust)
        needs_native = receipt["trust"] == "host-verified" or enforcement.get("require-host-capabilities") is not False
        if needs_native:
            if receipt["trust"] == "host-verified" and not (receipt.get("host_verification") or {}).get("proof"):
                raise ReceiptError("host-verified create requires a host proof destination")
            probes = _produce_native_proof(receipt, config, probes)
            if not isinstance(host_probes, dict):
                probe_path = Path(host_probes) if host_probes else impl_dir / HOST_PROBES_NAME
                probe_path.parent.mkdir(parents=True, exist_ok=True)
                probe_path.write_text(json.dumps(probes, ensure_ascii=False, indent=2,
                                                   sort_keys=True) + "\n", encoding="utf-8")
        if receipt.get("host_verification"):
            proof = receipt["host_verification"]
            proof_path = _artifact(proof["proof"], output.parent)
            expected_proof = _capability_receipt(receipt, config, probes)
            proof_path.parent.mkdir(parents=True, exist_ok=True)
            proof_path.write_text(json.dumps(expected_proof, ensure_ascii=False, indent=2,
                                               sort_keys=True) + "\n", encoding="utf-8")
            proof["proof_sha256"] = _sha(proof_path)
    except (ReceiptError, OSError, ValueError, KeyError, TypeError) as exc:
        return receipt, ["cannot create receipt: %s" % exc]
    errors = validate_receipt(receipt, issue_path, repo, output, host_probes=probes)
    if not errors:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt, errors


# ---------- Feature-level independent review receipts ----------

def _feature_schema_errors(receipt):
    try:
        js = _sibling("json_schema")
        return js.load_validate(FEATURE_SCHEMA_PATH, receipt)
    except (ImportError, ValueError, OSError) as exc:
        return ["feature receipt schema validation unavailable: %s" % exc]


def _feature_oid(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ReceiptError("%s must be a full lowercase Git OID" % label)
    return value


def _feature_diff_sha(repo: Path, base: str, head: str):
    """Bind the whole-feature diff exactly as ``git diff --binary BASE..HEAD``."""
    return hashlib.sha256(_git(repo, "diff", "--binary", "%s..%s" % (base, head), "--",
                               binary=True)).hexdigest()


def _feature_name(feature_dir: Path, candidate):
    value = candidate.get("feature") if isinstance(candidate, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _sibling("artifact_scope").scope_key(feature_dir)


def _load_feature_config(repo: Path, feature_dir: Path):
    try:
        ac = _sibling("agents_config")
    except ImportError as exc:
        raise ReceiptError("agents_config is required: %s" % exc)
    features = _sibling("artifact_scope").feature_root(feature_dir.resolve()).parent.parent
    if features.name != "features" or features.parent.name != "dev":
        raise ReceiptError("feature receipts require a dev/features/<module>/<slug> directory")
    workspace = features.parent.parent
    top_level = Path(_git(workspace, "rev-parse", "--show-toplevel").strip()).resolve()
    if top_level != workspace:
        raise ReceiptError("dev/features must be in its Git workspace root")

    common_arg = ("rev-parse", "--path-format=absolute", "--git-common-dir")
    source_git = Path(_git(workspace, *common_arg).strip()).resolve()
    candidate_git = Path(_git(repo, *common_arg).strip()).resolve()
    if source_git != candidate_git:
        raise ReceiptError("feature directory and candidate must belong to the same Git repository")

    # Use the feature workspace policy, which also governs freeze and promote.
    # The candidate checkout can carry a historical version of that policy.
    path = workspace / "dev" / "agents-config.md"
    if not path.is_file() or path.is_symlink():
        raise ReceiptError("feature workspace dev/agents-config.md must be a regular file")
    return ac.load(path)


def _feature_review_metadata(raw, feature_dir: Path):
    if not isinstance(raw, dict):
        raise ReceiptError("feature input must be an object")
    if isinstance(raw.get("review"), dict):
        meta = copy.deepcopy(raw["review"])
        for key in ("reviewer", "findings", "verdict", "trust", "attestation", "host_verification",
                    "review_round", "previous_receipt", "previous_receipt_sha256",
                    "coverage", "coverage_preview", "preview", "process_check",
                    "process_check_review", "artifacts"):
            if key in raw and key not in meta:
                meta[key] = copy.deepcopy(raw[key])
        return meta
    if any(key in raw for key in ("reviewer", "findings", "trust",
                                  "host_verification", "review_round")):
        return raw
    for relative in FEATURE_REVIEW_SIBLINGS:
        path = feature_dir / relative
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReceiptError("feature review metadata is invalid: %s" % exc)
        if isinstance(value, dict):
            if isinstance(value.get("review"), dict):
                return value["review"]
            return value
    return raw


def _feature_round(meta):
    value = meta.get("review_round")
    if value is None and isinstance(meta.get("review"), dict):
        value = meta["review"].get("round")
    if value is None:
        value = meta.get("round", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReceiptError("review round must be a positive integer")
    return value


def _feature_path(value, feature_dir: Path, candidate_path=None, label="path"):
    if isinstance(value, dict):
        value = value.get("path")
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ReceiptError("%s must be a non-empty path" % label)
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    bases = [feature_dir]
    if candidate_path is not None:
        bases.append(Path(candidate_path).parent)
    bases.append(Path.cwd())
    for base in bases:
        resolved = (base / path).resolve()
        if resolved.is_file():
            return resolved
    return (feature_dir / path).resolve()


def _feature_preview_entries(value):
    if isinstance(value, list):
        entries = value
    elif isinstance(value, dict):
        entries = None
        for key in ("files", "preview", "entries", "paths"):
            if key in value:
                entries = value[key]
                break
        if entries is None:
            raise ReceiptError("coverage preview must contain files[]")
    else:
        raise ReceiptError("coverage preview must be a list or an object with files[]")
    result = []
    if isinstance(entries, dict):
        pairs = list(entries.items())
    elif isinstance(entries, list):
        pairs = []
        for entry in entries:
            if isinstance(entry, str):
                pairs.append((entry, "reviewed", ""))
            elif isinstance(entry, dict):
                pairs.append((entry.get("path"), entry.get("status", "reviewed"),
                              entry.get("reason", "")))
            else:
                raise ReceiptError("coverage preview contains an entry without path")
    else:
        raise ReceiptError("coverage preview files must be a list or mapping")
    for pair in pairs:
        if len(pair) == 2:
            path, status = pair
            reason = ""
        else:
            path, status, reason = pair
        if isinstance(status, dict):
            reason = status.get("reason", "")
            status = status.get("status", "reviewed")
        if not isinstance(path, str) or not path.strip():
            raise ReceiptError("coverage preview contains an entry without path")
        if path.startswith("/") or ".." in Path(path).parts:
            raise ReceiptError("coverage preview path must be repository-relative: %s" % path)
        if status not in ("reviewed", "skipped"):
            raise ReceiptError("coverage preview status must be reviewed/skipped: %s" % path)
        if reason is None:
            reason = ""
        if not isinstance(reason, str):
            raise ReceiptError("coverage preview reason must be a string: %s" % path)
        result.append({"path": path, "status": status, "reason": reason.strip()})
    if len({entry["path"] for entry in result}) != len(result):
        raise ReceiptError("coverage preview contains duplicate paths")
    return sorted(result, key=lambda entry: entry["path"])


def _feature_coverage_entries(value):
    return _feature_preview_entries(value)


def _feature_coverage_key(entries):
    """Compare the full path/status set; skipped reasons are validated separately."""
    return sorted((entry["path"], entry["status"]) for entry in entries)


def _feature_coverage_value_errors(entries, changed):
    errors = []
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)):
        errors.append("receipt coverage contains duplicate paths")
    for entry in entries:
        if entry["status"] == "skipped" and not entry["reason"].strip():
            errors.append("skipped coverage lacks reason: %s" % entry["path"])
    missing = sorted(set(changed) - set(paths))
    extra = sorted(set(paths) - set(changed))
    if missing or extra:
        errors.append("coverage does not cover the exact feature diff file set "
                      "(missing: %s; extra: %s)" % (", ".join(missing) or "-", ", ".join(extra) or "-"))
    return errors


def _issue_frontmatter_records(feature_dir: Path):
    try:
        ac = _sibling("agents_config")
    except ImportError as exc:
        raise ReceiptError("agents_config is required: %s" % exc)
    records = {}
    for path in sorted((feature_dir / "issues").glob("issue-*.md")):
        try:
            frontmatter = ac.read_frontmatter(path)
        except (ValueError, OSError) as exc:
            raise ReceiptError("cannot read ticket %s: %s" % (path, exc))
        issue_id = str(frontmatter.get("issue-id") or "").strip()
        if not issue_id:
            match = re.match(r"issue-[0-9]+[a-z]?", path.stem, re.I)
            issue_id = match.group(0) if match else ""
        if not issue_id:
            raise ReceiptError("ticket %s has no issue-id" % path)
        if issue_id in records:
            raise ReceiptError("duplicate issue-id in feature: %s" % issue_id)
        records[issue_id] = (path, frontmatter)
    return records


def _implementers_from_candidate(feature_dir: Path, candidate):
    issues = candidate.get("issues") if isinstance(candidate, dict) else None
    if not isinstance(issues, list) or not issues:
        raise ReceiptError("candidate.issues must be a non-empty array")
    records = _issue_frontmatter_records(feature_dir)
    seen, result = set(), []
    for entry in issues:
        issue_id = ((entry.get("id") or entry.get("issue_id"))
                    if isinstance(entry, dict) else entry)
        if not isinstance(issue_id, str) or not issue_id.strip():
            raise ReceiptError("candidate.issues entry lacks id")
        issue_id = issue_id.strip()
        if issue_id in seen:
            raise ReceiptError("candidate.issues contains duplicate id: %s" % issue_id)
        seen.add(issue_id)
        record = records.get(issue_id)
        if record is None:
            folded = [value for key, value in records.items() if key.lower() == issue_id.lower()]
            record = folded[0] if len(folded) == 1 else None
        if record is None:
            raise ReceiptError("candidate issue %s has no main-workspace ticket" % issue_id)
        path, frontmatter = record
        dispatch = frontmatter.get("dispatch")
        if not isinstance(dispatch, dict):
            raise ReceiptError("ticket %s has no dispatch block" % path)
        session = (dispatch.get("session_id") or dispatch.get("session-id")
                   or dispatch.get("sessionId"))
        agent = (dispatch.get("agent") or dispatch.get("agent_type")
                 or dispatch.get("role") or "development")
        host = dispatch.get("host")
        if not isinstance(session, str) or not session.strip():
            raise ReceiptError("ticket %s has no dispatched session_id" % path)
        if not isinstance(agent, str) or not agent.strip():
            raise ReceiptError("ticket %s has no dispatched agent" % path)
        item = {"issue": issue_id, "agent": agent.strip(), "session": session.strip()}
        if isinstance(host, str) and host.strip():
            item["host"] = host.strip()
        result.append(item)
    return result


def _feature_independence_errors(reviewer, implementers):
    errors = []
    for implementer in implementers:
        if reviewer.get("session") == implementer.get("session"):
            errors.append("reviewer session equals implementer session for %s" % implementer["issue"])
        if reviewer.get("agent") == implementer.get("agent"):
            errors.append("reviewer agent equals implementer agent for %s" % implementer["issue"])
    return errors


def _feature_plan_sha(candidate, feature_dir: Path):
    plan = candidate.get("acceptance_plan") if isinstance(candidate, dict) else None
    if not isinstance(plan, dict):
        raise ReceiptError("candidate.acceptance_plan must be an object")
    digest = plan.get("sha256")
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise ReceiptError("candidate.acceptance_plan.sha256 must be a SHA256")
    path_value = plan.get("path")
    if isinstance(path_value, str) and path_value.strip():
        path = Path(path_value)
        if not path.is_absolute():
            path = feature_dir / path
        path = path.resolve()
        if path.is_file() and _sha(path) != digest:
            raise ReceiptError("acceptance plan hash does not match candidate.acceptance_plan.sha256")
    return digest


def _feature_process_path(meta, candidate, feature_dir: Path, candidate_path=None):
    value = meta.get("process_check") or meta.get("process_check_review")
    artifacts = meta.get("artifacts")
    if value is None and isinstance(artifacts, dict):
        value = artifacts.get("process_check") or artifacts.get("process_check_review")
    if value is None and isinstance(candidate.get("process_check"), (str, dict)):
        value = candidate["process_check"]
    if value is not None:
        return _feature_path(value, feature_dir, candidate_path, "process-check")
    for name in ("process-check-review.json", "process-check.json", "process-check-feature.json"):
        path = feature_dir / FEATURE_PROBES_DIR / name
        if path.is_file():
            return path.resolve()
    return None


def _feature_process_errors(path: Path, receipt):
    errors = []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return errors
    if not isinstance(value, dict):
        return errors
    for key in ("candidate", "head"):
        if key in value and value[key] != receipt["head"]:
            errors.append("process-check %s does not match receipt head" % key)
    for key in ("candidate_tree", "tree"):
        if key in value and value[key] != receipt["tree"]:
            errors.append("process-check %s does not match receipt tree" % key)
    for key in ("main_head", "base"):
        if key in value and value[key] != receipt["base"]:
            errors.append("process-check %s does not match receipt base" % key)
    if value.get("feature") is not None and value["feature"] != receipt["feature"]:
        errors.append("process-check feature does not match receipt")
    verdict = value.get("verdict")
    if isinstance(verdict, str) and verdict.upper() != "PASS":
        errors.append("process-check is not PASS")
    return errors


def _feature_review_action(receipt):
    context = {
        "feature": receipt["feature"],
        "candidate": receipt["head"],
        "tree": receipt["tree"],
        "main_head": receipt["base"],
        "round": receipt["round"],
        "report_sha256": receipt["report_sha256"],
    }
    return {
        "review_id": "%s-r%s-%s" % (receipt["feature"].replace("/", "-"),
                                    receipt["round"], receipt["report_sha256"][:16]),
        "round": receipt["round"],
        "feature": receipt["feature"],
        "candidate": receipt["head"],
        "head": receipt["head"],
        "tree": receipt["tree"],
        "main_head": receipt["base"],
        "issue": receipt["feature"],
        "report_sha256": receipt["report_sha256"],
        "context_digest": _canonical_hash(context),
    }


def _feature_native_implementer(receipt):
    implementer = receipt["implementers"][0]
    return {"host": implementer.get("host") or receipt["reviewer"]["host"],
            "agent": implementer["agent"], "session": implementer["session"],
            "role": "development"}


def _feature_native_proof_input(receipt, probes):
    proof = copy.deepcopy(probes.get("native_proof", probes))
    reviewer = receipt["reviewer"]
    proof["host"] = reviewer["host"]
    proof["session"] = reviewer["session"]
    # The receipt supplies the immutable action the native session must prove.
    proof["review"] = _feature_review_action(receipt)
    if receipt.get("attestation"):
        proof["operator_attestation"] = copy.deepcopy(receipt["attestation"])
    return proof


def _feature_capability_receipt(receipt, config, probes):
    adapters = _sibling("host_adapters")
    reviewer = receipt["reviewer"]
    native_proof = _feature_native_proof_input(receipt, probes)
    try:
        result = adapters.verify_review_session(
            reviewer["host"], reviewer, _feature_native_implementer(receipt),
            native_proof, config)
    except (adapters.HostCapabilityError, ValueError, OSError) as exc:
        raise ReceiptError("feature independent_session capability is not available/proven: %s" % exc)
    return {
        "schema": HOST_PROOF_SCHEMA, "verified": True, "attested_by": "host",
        "identity": copy.deepcopy(reviewer), "native_proof": native_proof,
        "verification": result,
    }


def _produce_feature_native_proof(receipt, config, probes):
    """Attach this exact feature review through the real host API."""
    adapters = _sibling("host_adapters")
    if not hasattr(adapters, "write_review_binding"):
        raise ReceiptError("host_adapters.write_review_binding is required for feature receipt finalization")
    reviewer = receipt["reviewer"]
    try:
        produced = adapters.write_review_binding(
            reviewer["host"], reviewer, _feature_native_implementer(receipt),
            _feature_review_action(receipt), config)
    except (adapters.HostCapabilityError, ValueError, OSError) as exc:
        raise ReceiptError("cannot attach feature review through native host: %s" % exc)
    if not isinstance(produced, dict):
        raise ReceiptError("write_review_binding returned a non-object proof")
    if produced.get("written") is not True or produced.get("requires_native_apply") is not False:
        raise ReceiptError("native feature review binding is not persisted/readable yet")
    record = produced.get("native_session")
    review = produced.get("review")
    if not isinstance(record, dict) or not isinstance(review, dict):
        raise ReceiptError("write_review_binding lacks native read-back session/review")
    merged = copy.deepcopy(probes)
    merged.update(produced)
    merged["native_hash"] = adapters._native_digest(record, review)
    return merged


def _feature_finding_errors(receipt):
    errors = []
    seen = set()
    for finding in receipt["findings"]:
        if finding["id"] in seen:
            errors.append("duplicate finding id: %s" % finding["id"])
        seen.add(finding["id"])
        if (receipt["verdict"] in {"pass", "pass-with-notes"}
                and finding["severity"] in {"critical", "high"}
                and (finding["status"] != "closed" or not finding["closure"].strip())):
            errors.append("critical/high finding is not closed with evidence: %s" % finding["id"])
    return errors


def _feature_binding_errors(receipt, candidate, repo: Path, feature_dir: Path):
    errors = []
    if candidate is not None:
        if (_sibling("artifact_scope").campaign_id(feature_dir)
                and candidate.get("feature") != _sibling("artifact_scope").scope_key(feature_dir)):
            errors.append("candidate belongs to another campaign directory")
        feature = candidate.get("feature")
        if isinstance(feature, str) and feature and receipt["feature"] != feature:
            errors.append("receipt feature differs from candidate.feature")
        stacked = candidate.get("stack_base")
        if stacked:
            binding = (("base", "review_base"), ("head", "candidate"),
                       ("tree", "candidate_tree"), ("merge_base", "review_merge_base"))
        else:
            binding = (("base", "main_head"), ("head", "candidate"),
                       ("tree", "candidate_tree"), ("merge_base", "merge_base"))
        for field, candidate_field in binding:
            if candidate.get(candidate_field) != receipt[field]:
                errors.append("receipt %s differs from candidate.json %s" % (field, candidate_field))
        if stacked:
            recorded = receipt.get("stack_base")
            if (not isinstance(recorded, dict)
                    or recorded.get("commit") != stacked.get("commit")
                    or recorded.get("declaration_sha256") != stacked.get("declaration_sha256")):
                errors.append("receipt stack_base differs from the candidate's pinned predecessor")
        plan = candidate.get("acceptance_plan") or {}
        if isinstance(plan, dict) and plan.get("sha256") != receipt["plan_sha256"]:
            errors.append("receipt plan_sha256 differs from candidate.acceptance_plan.sha256")
        if isinstance(plan, dict) and isinstance(plan.get("path"), str) and plan["path"].strip():
            plan_path = Path(plan["path"])
            if not plan_path.is_absolute():
                plan_path = feature_dir / plan_path
            plan_path = plan_path.resolve()
            if plan_path.is_file():
                try:
                    if _sha(plan_path) != receipt["plan_sha256"]:
                        errors.append("acceptance plan changed since the receipt was created")
                except OSError as exc:
                    errors.append("acceptance plan unreadable: %s" % exc)
        branch = candidate.get("integration_branch")
        if isinstance(branch, str) and branch and branch != receipt["integration_branch"]:
            errors.append("receipt integration_branch differs from candidate")
    try:
        actual_tree = _git(repo, "rev-parse", "--verify", receipt["head"] + "^{tree}").strip()
        if actual_tree != receipt["tree"]:
            errors.append("receipt tree does not match the frozen candidate commit")
        if receipt["diff_sha256"] != _feature_diff_sha(repo, receipt["base"], receipt["head"]):
            errors.append("receipt diff hash mismatch")
        actual_merge_base = _git(repo, "merge-base", receipt["base"], receipt["head"]).strip()
        if actual_merge_base != receipt["merge_base"]:
            errors.append("receipt merge_base does not match git merge-base main_head candidate")
    except ReceiptError as exc:
        errors.append("feature binding cannot be recomputed: %s" % exc)
    try:
        branch_ref = _git(repo, "rev-parse", "--verify",
                          "refs/heads/" + receipt["integration_branch"] + "^{commit}").strip()
        if branch_ref != receipt["head"]:
            errors.append("integration branch advanced past the frozen candidate")
    except ReceiptError:
        # A fetched/detached candidate need not have a local branch ref.
        pass
    return errors


def _feature_artifact_errors(receipt, receipt_path: Path):
    errors = []
    for key, digest_key in (("report", "report_sha256"), ("process_check", "process_check_sha256")):
        artifact = receipt["artifacts"].get(key)
        digest = receipt.get(digest_key)
        if digest is None:
            if artifact is not None:
                errors.append("%s artifact/hash fields disagree" % key)
            continue
        if not isinstance(artifact, dict):
            errors.append("%s artifact is missing" % key)
            continue
        if artifact.get("sha256") != digest:
            errors.append("%s hash fields disagree" % key)
        try:
            path = _artifact(artifact["path"], receipt_path.parent)
            if not path.is_file() or _sha(path) != digest:
                errors.append("%s is missing or tampered" % key)
            elif key == "process_check":
                errors.extend(_feature_process_errors(path, receipt))
        except (OSError, ValueError, KeyError) as exc:
            errors.append("%s unreadable: %s" % (key, exc))
    preview = receipt["coverage"].get("preview")
    if preview is not None:
        try:
            path = _artifact(preview["path"], receipt_path.parent)
            if not path.is_file() or _sha(path) != preview["sha256"]:
                errors.append("coverage preview is missing or tampered")
        except (OSError, ValueError, KeyError) as exc:
            errors.append("coverage preview unreadable: %s" % exc)
    return errors


def _feature_coverage_errors(receipt, repo: Path, receipt_path: Path):
    errors = []
    entries = receipt["coverage"]["files"]
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)):
        errors.append("receipt coverage contains duplicate paths")
    reviewed = sum(entry["status"] == "reviewed" for entry in entries)
    skipped = sum(entry["status"] == "skipped" for entry in entries)
    if (receipt["total_files"], receipt["reviewed_files"], receipt["skipped_files"]) != (
            len(entries), reviewed, skipped):
        errors.append("total/reviewed/skipped counts do not match coverage")
    for entry in entries:
        if entry["status"] == "skipped" and not entry["reason"].strip():
            errors.append("skipped coverage lacks reason: %s" % entry["path"])
    try:
        actual_diff = _changed_paths(repo, receipt["base"], receipt["head"])
    except ReceiptError as exc:
        return errors + ["feature diff cannot be recomputed: %s" % exc]
    missing = sorted(set(actual_diff) - set(paths))
    extra = sorted(set(paths) - set(actual_diff))
    if missing or extra:
        errors.append("coverage does not cover the exact feature diff file set "
                      "(missing: %s; extra: %s)" % (", ".join(missing) or "-", ", ".join(extra) or "-"))
    preview = receipt["coverage"].get("preview")
    if preview is not None:
        try:
            path = _artifact(preview["path"], receipt_path.parent)
            value = json.loads(path.read_text(encoding="utf-8"))
            preview_entries = _feature_preview_entries(value)
            if _feature_coverage_key(preview_entries) != _feature_coverage_key(entries):
                errors.append("coverage preview and coverage files disagree")
            if sorted(entry["path"] for entry in preview_entries) != actual_diff:
                errors.append("coverage preview does not equal the feature diff full set")
        except (OSError, ValueError, ReceiptError, KeyError) as exc:
            errors.append("coverage preview invalid: %s" % exc)
    return errors


def _feature_previous_errors(receipt, receipt_path: Path):
    errors = []
    round_value = receipt["round"]
    previous_hash = receipt["previous_receipt_sha256"]
    previous = receipt.get("previous_receipt")
    if round_value == 1:
        if previous_hash is not None:
            errors.append("round 1 must not reference previous receipt")
        if previous is not None:
            errors.append("round 1 must not embed a previous receipt")
        return errors
    if not isinstance(previous_hash, str) or not HEX64.fullmatch(previous_hash):
        errors.append("round >1 requires previous_receipt_sha256")
        return errors
    if previous is None:
        return errors
    try:
        path = _artifact(previous["path"], receipt_path.parent)
        if path.resolve() == receipt_path.resolve():
            errors.append("previous receipt must not be the receipt itself")
            return errors
        if not path.is_file() or _sha(path) != previous["sha256"]:
            errors.append("previous receipt hash mismatch")
            return errors
        if previous["sha256"] != previous_hash:
            errors.append("previous receipt hash fields disagree")
            return errors
        value = json.loads(path.read_text(encoding="utf-8"))
        errors.extend(_feature_schema_errors(value))
        if value.get("round") != round_value - 1:
            errors.append("previous receipt round chain mismatch")
        if value.get("feature") != receipt["feature"]:
            errors.append("previous receipt feature mismatch")
    except (OSError, ValueError, KeyError) as exc:
        errors.append("previous receipt invalid: %s" % exc)
    return errors


def _feature_trust_errors(receipt, config, receipt_path: Path, probes):
    errors = []
    enforcement = config.get("enforcement") or {}
    required = enforcement.get("review-trust")
    if required and receipt["trust"] != required:
        errors.append("trust must be %s by enforcement.review-trust" % required)
    needs_probe = (receipt["trust"] == "host-verified"
                   or enforcement.get("require-host-capabilities") is not False)
    expected = None
    if needs_probe:
        try:
            expected = _feature_capability_receipt(receipt, config, probes)
        except ReceiptError as exc:
            errors.append(str(exc))
    if receipt["trust"] == "host-verified":
        proof = receipt.get("host_verification")
        if not proof:
            return errors + ["host-verified receipt lacks host proof; operator attestation cannot substitute"]
        reviewer = receipt["reviewer"]
        if proof["host"] != reviewer["host"] or proof["session"] != reviewer["session"]:
            errors.append("host proof identity does not match reviewer")
        if proof.get("review_action") is not None and proof["review_action"] != _feature_review_action(receipt):
            errors.append("host proof review action does not match the feature binding")
        try:
            path = _artifact(proof["proof"], receipt_path.parent)
            if _sha(path) != proof["proof_sha256"]:
                errors.append("host proof hash mismatch")
            value = json.loads(path.read_text(encoding="utf-8"))
            if expected is not None and value != expected:
                errors.append("host proof does not match the live host_adapters probe receipt")
        except (OSError, ValueError, KeyError) as exc:
            errors.append("host proof unavailable/invalid: %s" % exc)
        if receipt.get("attestation") is not None:
            errors.append("host-verified receipt must not substitute operator attestation")
    else:
        attestation = receipt.get("attestation")
        if not attestation:
            errors.append("operator-attested receipt lacks operator attestation")
        elif ("independent review" not in attestation["statement"].lower()
              and "独立评审" not in attestation["statement"]):
            errors.append("operator attestation must explicitly attest independent review")
        if receipt.get("host_verification") is not None:
            errors.append("operator-attested receipt must not claim host verification")
    return errors


def _load_candidate_json(feature_dir: Path):
    path = feature_dir / FEATURE_PROBES_DIR / "candidate.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReceiptError("feature candidate.json is unreadable/invalid: %s" % exc)
    if not isinstance(value, dict):
        raise ReceiptError("feature candidate.json must be an object")
    return value


def _validate_feature_receipt_value(receipt, feature_dir, repo, receipt_path, candidate=None):
    feature_dir = Path(feature_dir).resolve()
    repo = Path(repo).resolve()
    receipt_path = Path(receipt_path).resolve()
    errors = _feature_schema_errors(receipt)
    if errors:
        return errors
    try:
        config = _load_feature_config(repo, feature_dir)
        probes = _load_host_probes(None, Path(receipt_path).parent)
        if candidate is None:
            candidate = _load_candidate_json(feature_dir)
        errors.extend(_feature_binding_errors(receipt, candidate, repo, feature_dir))
        errors.extend(_validate_identity(receipt["reviewer"], "review", config["dispatch"], "reviewer"))
        implementers = []
        if candidate is not None:
            try:
                current = _implementers_from_candidate(feature_dir, candidate)
                if current != receipt["implementers"]:
                    errors.append("implementer identities differ from main-workspace tickets")
                implementers = current
            except (ReceiptError, ImportError, ValueError, OSError) as exc:
                errors.append("cannot re-read implementer identities: %s" % exc)
        if not implementers:
            implementers = receipt["implementers"]
        errors.extend(_feature_independence_errors(receipt["reviewer"], implementers))
        errors.extend(_feature_finding_errors(receipt))
        errors.extend(_feature_artifact_errors(receipt, receipt_path))
        errors.extend(_feature_coverage_errors(receipt, repo, receipt_path))
        errors.extend(_feature_previous_errors(receipt, receipt_path))
        errors.extend(_feature_trust_errors(receipt, config, receipt_path, probes))
    except (ReceiptError, ImportError, KeyError, TypeError, ValueError, OSError,
            subprocess.SubprocessError) as exc:
        errors.append("feature receipt validation failed closed: %s" % exc)
    return errors


def validate_feature_receipt(feature_dir, repo, receipt_path, candidate=None):
    """Return all semantic/freshness errors for a feature-level review receipt.

    ``candidate`` is for round-scoped closeout, where the frozen candidate lives
    under ``integration/<round>/candidate.json`` instead of ``integration/``.
    """
    path = Path(receipt_path).resolve()
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ["feature receipt invalid JSON: %s" % exc]
    if not isinstance(receipt, dict):
        return ["feature receipt must be a JSON object"]
    return _validate_feature_receipt_value(receipt, feature_dir, repo, path, candidate=candidate)


def _find_feature_receipt(feature_dir: Path, digest, exclude=None, extra_dirs=()):
    exclude = Path(exclude).resolve() if exclude is not None else None
    roots, seen = [], set()
    for root in (feature_dir, *extra_dirs):
        if root is None:
            continue
        root = Path(root).resolve()
        if root in seen:
            continue
        seen.add(root)
        roots.append(root)
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            try:
                resolved = path.resolve()
                if exclude is not None and resolved == exclude:
                    continue
                if not path.is_file() or _sha(path) != digest:
                    continue
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("schema") == FEATURE_SCHEMA:
                    return path
            except (OSError, ValueError):
                continue
    return None


def create_feature_receipt(feature_dir, repo, input_path, report, coverage_preview=None, output=None):
    """Populate a whole-feature review receipt from integration/candidate.json.

    Review metadata (reviewer/findings/round/trust/...) may live beside the
    candidate fields, under ``review``, or in a sibling feature-review JSON.
    Git identities and content hashes are always recomputed here.
    """
    feature_dir = Path(feature_dir).resolve()
    repo = Path(repo).resolve()
    input_path = Path(input_path).resolve()
    output_path = (Path(output).resolve() if output
                   else feature_dir / FEATURE_PROBES_DIR / FEATURE_RECEIPT_NAME)
    receipt = {}
    errors = []
    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ReceiptError("feature input must be an object")
        candidate = raw
        for key in ("integration_candidate", "candidate_path", "candidate_json"):
            value = raw.get(key)
            if isinstance(value, str):
                path = Path(value)
                if not path.is_absolute():
                    path = input_path.parent / path
                candidate = json.loads(path.read_text(encoding="utf-8"))
                break
        if isinstance(raw.get("candidate"), dict):
            candidate = raw["candidate"]
        if not isinstance(candidate, dict):
            raise ReceiptError("candidate input must be an object")
        meta = _feature_review_metadata(raw, feature_dir)
        config = _load_feature_config(repo, feature_dir)

        feature = _feature_name(feature_dir, candidate)
        stacked = candidate.get("stack_base")
        if stacked:
            base = _feature_oid(candidate.get("review_base"), "candidate.review_base")
            merge_base = _feature_oid(candidate.get("review_merge_base"), "candidate.review_merge_base")
            if not isinstance(stacked, dict) or stacked.get("commit") != base:
                raise ReceiptError("stacked candidate review base must equal its pinned predecessor")
        else:
            base = _feature_oid(candidate.get("main_head"), "candidate.main_head")
            merge_base = _feature_oid(candidate.get("merge_base"), "candidate.merge_base")
        head = _feature_oid(candidate.get("candidate"), "candidate.candidate")
        tree = _feature_oid(candidate.get("candidate_tree"), "candidate.candidate_tree")
        branch = candidate.get("integration_branch")
        if not isinstance(branch, str) or not branch.strip():
            raise ReceiptError("candidate.integration_branch must be a non-empty string")
        actual_tree = _git(repo, "rev-parse", "--verify", head + "^{tree}").strip()
        if actual_tree != tree:
            raise ReceiptError("candidate_tree does not match the candidate commit tree")
        actual_merge_base = _git(repo, "merge-base", base, head).strip()
        if actual_merge_base != merge_base:
            raise ReceiptError("candidate merge base does not match git merge-base base candidate")
        diff_sha = _feature_diff_sha(repo, base, head)
        changed = _changed_paths(repo, base, head)
        if not changed:
            raise ReceiptError("feature diff is empty; there is nothing to review")
        plan_sha = _feature_plan_sha(candidate, feature_dir)

        reviewer = copy.deepcopy(meta.get("reviewer"))
        if not isinstance(reviewer, dict):
            raise ReceiptError("reviewer identity is required in the review metadata")
        implementers = _implementers_from_candidate(feature_dir, candidate)
        findings = copy.deepcopy(meta.get("findings") or [])
        if not isinstance(findings, list):
            raise ReceiptError("findings must be an array")
        verdict = meta.get("verdict") or "pass"
        if verdict not in ("pass", "pass-with-notes", "blocked"):
            errors.append("verdict must be pass/pass-with-notes/blocked")

        declared_coverage = meta.get("coverage")
        preview_value = coverage_preview if coverage_preview is not None else (
            meta.get("coverage_preview", meta.get("preview")))
        if preview_value is None and isinstance(declared_coverage, dict):
            preview_value = declared_coverage.get("preview")
        preview_artifact = None
        preview_entries = None
        if preview_value is not None:
            path_like = (isinstance(preview_value, (str, Path))
                         or (isinstance(preview_value, dict) and "path" in preview_value))
            if path_like:
                preview_path = _feature_path(preview_value, feature_dir, input_path,
                                             "coverage preview")
                try:
                    loaded_preview = json.loads(preview_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ReceiptError("coverage preview is unreadable/invalid: %s" % exc)
                preview_artifact = _artifact_record(preview_path)
                preview_entries = _feature_preview_entries(loaded_preview)
            else:
                preview_entries = _feature_preview_entries(preview_value)
        if isinstance(declared_coverage, dict) and "files" not in declared_coverage:
            declared_coverage = None
        if declared_coverage is not None:
            coverage_entries = _feature_coverage_entries(declared_coverage)
            if (preview_entries is not None
                    and _feature_coverage_key(coverage_entries) != _feature_coverage_key(preview_entries)):
                errors.append("coverage files do not match the coverage preview")
        elif preview_entries is not None:
            coverage_entries = preview_entries
        else:
            coverage_entries = [{"path": path, "status": "reviewed", "reason": ""}
                                for path in changed]
        coverage_entries = sorted(coverage_entries, key=lambda entry: entry["path"])
        errors.extend(_feature_coverage_value_errors(coverage_entries, changed))

        round_value = _feature_round(meta)
        previous_hash = meta.get("previous_receipt_sha256")
        previous_value = meta.get("previous_receipt")
        if isinstance(previous_value, str) and HEX64.fullmatch(previous_value):
            previous_hash = previous_hash or previous_value
            previous_value = None
        previous_artifact = None
        if round_value == 1:
            if previous_hash is not None or previous_value is not None:
                errors.append("round 1 must not reference previous receipt")
            previous_hash = None
        elif not isinstance(previous_hash, str) or not HEX64.fullmatch(previous_hash):
            errors.append("round >1 requires previous_receipt_sha256")
            previous_hash = None
        else:
            if previous_value is not None:
                previous_path = _feature_path(previous_value, feature_dir, input_path,
                                              "previous receipt")
                if _sha(previous_path) != previous_hash:
                    errors.append("previous receipt hash mismatch")
                else:
                    previous_artifact = _artifact_record(previous_path)
            elif output_path.is_file() and _sha(output_path) == previous_hash:
                previous_artifact = _artifact_record(output_path)
            else:
                found = _find_feature_receipt(
                    feature_dir, previous_hash, exclude=output_path,
                    extra_dirs=(output_path.parent, input_path.parent))
                if found is not None:
                    previous_artifact = _artifact_record(found)
            if previous_artifact is not None and Path(previous_artifact["path"]).resolve() == output_path:
                archive = output_path.parent / ("%s-r%d-%s.json" % (
                    output_path.stem, round_value - 1, previous_hash[:8]))
                archive.write_bytes(output_path.read_bytes())
                previous_artifact = _artifact_record(archive)

        report_artifact = _artifact_record(_feature_path(report, feature_dir, input_path, "report"))
        process_path = _feature_process_path(meta, candidate, feature_dir, input_path)
        process_artifact = _artifact_record(process_path) if process_path is not None else None

        enforcement = config.get("enforcement") or {}
        required_trust = enforcement.get("review-trust")
        trust = meta.get("trust") or required_trust or "host-verified"
        if trust not in ("host-verified", "operator-attested"):
            errors.append("trust must be host-verified/operator-attested")
        if required_trust and trust != required_trust:
            errors.append("trust must be %s by enforcement.review-trust" % required_trust)
        attestation = copy.deepcopy(meta.get("attestation"))
        host_verification = copy.deepcopy(meta.get("host_verification"))
        if trust == "host-verified":
            if attestation is not None:
                errors.append("host-verified receipt must not substitute operator attestation")
            if host_verification is None:
                host_verification = {
                    "verified": True, "host": reviewer.get("host"),
                    "session": reviewer.get("session"),
                    "proof": str(feature_dir / FEATURE_PROBES_DIR / FEATURE_PROOF_NAME),
                    "proof_sha256": "0" * 64,
                }
            elif not isinstance(host_verification, dict):
                errors.append("host_verification must be an object")
                host_verification = None
            else:
                host_verification["verified"] = True
                host_verification["host"] = reviewer.get("host")
                host_verification["session"] = reviewer.get("session")
                host_verification["proof"] = str(_feature_path(
                    host_verification.get("proof")
                    or feature_dir / FEATURE_PROBES_DIR / FEATURE_PROOF_NAME,
                    feature_dir, input_path, "host proof"))
                host_verification["proof_sha256"] = "0" * 64
                host_verification.pop("review_action", None)
        else:
            if host_verification is not None:
                errors.append("operator-attested receipt must not claim host verification")
                host_verification = None

        errors.extend(_validate_identity(reviewer, "review", config["dispatch"], "reviewer"))
        errors.extend(_feature_independence_errors(reviewer, implementers))
        errors.extend(_feature_finding_errors({"findings": findings, "verdict": verdict}))

        receipt = {
            "schema": FEATURE_SCHEMA,
            "feature": feature,
            "integration_branch": branch.strip(),
            "base": base,
            "head": head,
            "tree": tree,
            "merge_base": merge_base,
            "diff_sha256": diff_sha,
            "plan_sha256": plan_sha,
            "report_sha256": report_artifact["sha256"],
            "process_check_sha256": (process_artifact["sha256"] if process_artifact is not None else None),
            "verdict": verdict,
            "findings": findings,
            "total_files": len(coverage_entries),
            "reviewed_files": sum(entry["status"] == "reviewed" for entry in coverage_entries),
            "skipped_files": sum(entry["status"] == "skipped" for entry in coverage_entries),
            "coverage": {"preview": preview_artifact, "files": coverage_entries},
            "round": round_value,
            "previous_receipt_sha256": previous_hash,
            "previous_receipt": previous_artifact,
            "implementers": implementers,
            "reviewer": reviewer,
            "trust": trust,
            "attestation": attestation,
            "host_verification": host_verification,
            "artifacts": {"report": report_artifact, "process_check": process_artifact},
            "created_at": _now(),
        }
        if stacked:
            receipt["stack_base"] = {key: stacked.get(key) for key in (
                "predecessor_scope", "predecessor_ref", "commit", "tree",
                "source_sha256", "declaration_sha256")}
        if host_verification is not None:
            host_verification["review_action"] = _feature_review_action(receipt)
    except (ReceiptError, OSError, ValueError, KeyError, TypeError) as exc:
        return receipt, ["cannot create feature receipt: %s" % exc]

    if errors:
        return receipt, errors

    try:
        probes = _load_host_probes(None, output_path.parent)
        needs_native = (trust == "host-verified"
                        or enforcement.get("require-host-capabilities") is not False)
        if needs_native:
            probes = _produce_feature_native_proof(receipt, config, probes)
            probe_path = output_path.parent / HOST_PROBES_NAME
            probe_path.parent.mkdir(parents=True, exist_ok=True)
            probe_path.write_text(json.dumps(probes, ensure_ascii=False, indent=2,
                                             sort_keys=True) + "\n", encoding="utf-8")
        if receipt.get("host_verification") is not None and needs_native:
            proof = receipt["host_verification"]
            proof_path = _artifact(proof["proof"], output_path.parent)
            expected_proof = _feature_capability_receipt(receipt, config, probes)
            proof_path.parent.mkdir(parents=True, exist_ok=True)
            proof_path.write_text(json.dumps(expected_proof, ensure_ascii=False, indent=2,
                                             sort_keys=True) + "\n", encoding="utf-8")
            proof["proof_sha256"] = _sha(proof_path)
    except (ReceiptError, OSError, ValueError, KeyError, TypeError) as exc:
        return receipt, ["cannot create feature receipt: %s" % exc]

    errors = _validate_feature_receipt_value(receipt, feature_dir, repo, output_path,
                                             candidate=candidate)
    if not errors:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2,
                                          sort_keys=True) + "\n", encoding="utf-8")
    return receipt, errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--issue", required=True)
    create.add_argument("--repo", required=True)
    create.add_argument("--input", "--candidate", dest="candidate", required=True)
    create.add_argument("--report", required=True)
    create.add_argument("--process-check", dest="process_check", required=True)
    create.add_argument("--coverage-preview", required=True)
    create.add_argument("--host-probes")
    create.add_argument("--output")
    validate = sub.add_parser("validate")
    validate.add_argument("--issue", required=True)
    validate.add_argument("--repo", required=True)
    validate.add_argument("--receipt", required=True)
    validate.add_argument("--host-probes")
    create_feature = sub.add_parser("create-feature")
    create_feature.add_argument("--feature-dir", required=True)
    create_feature.add_argument("--repo", required=True)
    create_feature.add_argument("--input", "--candidate", dest="candidate", required=True)
    create_feature.add_argument("--report", required=True)
    create_feature.add_argument("--coverage-preview")
    create_feature.add_argument("--output")
    validate_feature = sub.add_parser("validate-feature")
    validate_feature.add_argument("--feature-dir", required=True)
    validate_feature.add_argument("--repo", required=True)
    validate_feature.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
            receipt, errors = create_receipt(
                candidate, args.issue, args.repo, report=args.report,
                process_check_review=args.process_check,
                coverage_preview=args.coverage_preview, output=args.output,
                host_probes=args.host_probes)
        elif args.command == "validate":
            path = Path(args.receipt).resolve()
            receipt = json.loads(path.read_text(encoding="utf-8"))
            errors = validate_receipt(receipt, args.issue, args.repo, path,
                                      host_probes=args.host_probes)
        elif args.command == "create-feature":
            receipt, errors = create_feature_receipt(
                args.feature_dir, args.repo, args.candidate, args.report,
                coverage_preview=args.coverage_preview, output=args.output)
        else:
            path = Path(args.receipt).resolve()
            receipt = json.loads(path.read_text(encoding="utf-8"))
            errors = validate_feature_receipt(args.feature_dir, args.repo, path)
    except (OSError, ValueError) as exc:
        print("review-receipt: input error: %s" % exc, file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print("[FAIL] %s" % error)
        return 1
    if args.command in ("create-feature", "validate-feature"):
        print("feature-review-receipt %s round=%s verdict=%s PASS" % (
            receipt.get("feature"), receipt.get("round"), receipt.get("verdict")))
    else:
        print("review-receipt %s round=%s verdict=%s PASS" % (
            receipt.get("issue"), receipt.get("round"), receipt.get("verdict")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
