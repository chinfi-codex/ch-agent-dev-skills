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
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

SCHEMA = "agent-dev.review-receipt/v2"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "review-receipt.schema.json"
HOST_PROBES_NAME = "host-probes.json"
HOST_PROOF_SCHEMA = "agent-dev.host-capability-receipt/v1"


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
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
            receipt, errors = create_receipt(
                candidate, args.issue, args.repo, report=args.report,
                process_check_review=args.process_check,
                coverage_preview=args.coverage_preview, output=args.output,
                host_probes=args.host_probes)
        else:
            path = Path(args.receipt).resolve()
            receipt = json.loads(path.read_text(encoding="utf-8"))
            errors = validate_receipt(receipt, args.issue, args.repo, path,
                                      host_probes=args.host_probes)
    except (OSError, ValueError) as exc:
        print("review-receipt: input error: %s" % exc, file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print("[FAIL] %s" % error)
        return 1
    print("review-receipt %s round=%s verdict=%s PASS" % (
        receipt.get("issue"), receipt.get("round"), receipt.get("verdict")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
