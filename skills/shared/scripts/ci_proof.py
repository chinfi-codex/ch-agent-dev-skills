#!/usr/bin/env python3
"""Online Gitea/GitLab CI capabilities and hash-bound verdict proofs.

There is deliberately no local-verdict or local-run-proof acceptance path.  A
proof is created only after authenticated provider API reads and an artifact
download.  Promotion must call :func:`recheck_proof`, which repeats those reads;
a previously downloaded JSON file is never sufficient by itself.

Public API:
    capabilities(provider, repository, *, base_url, workflow, branch, mode, ...)
    fetch_proof(provider, repository, workflow, run_id, *, base_url, ...)
    recheck_proof(proof, *, base_url=None, repo=None, ...)
    validate_proof(proof, *, repo=None, expected_ref=None, ...)

Only Python's standard library is used.  ``repo`` is used solely to ask Git for
the candidate tree; it is never a substitute for provider evidence.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

# Package-less sibling contract modules must remain available under ``-I``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = "agent-dev.ci-proof/v2"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "ci-proof.schema.json"
MODES = ("server-enforced", "cooperative-ci", "local-human", "pr")
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_OID = re.compile(r"^[0-9a-f]{40}$")


class CIProofError(ValueError):
    """Online evidence is absent, stale, inconsistent, or not authoritative."""


class CapabilityError(CIProofError):
    """A required provider capability is false or cannot be observed."""


def _utc_now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _full_ref(value) -> str:
    ref = str(value or "").strip()
    if not ref:
        raise CIProofError("provider run has no ref/head_branch")
    if ref.startswith("refs/"):
        return ref
    return "refs/heads/" + ref


def _short_ref(value: str) -> str:
    return value[len("refs/heads/"):] if value.startswith("refs/heads/") else value


def _same_ref(left, right) -> bool:
    return _full_ref(left) == _full_ref(right)


def _string_id(value, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CIProofError("%s must be a string or integer ID" % label)
    result = str(value).strip()
    if not result:
        raise CIProofError("%s is empty" % label)
    return result


def _attempt(value) -> int:
    if isinstance(value, bool):
        raise CIProofError("run attempt must be an integer, not boolean")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise CIProofError("provider API does not expose a numeric run attempt")
    if result < 1 or str(value).strip() not in (str(result), str(float(result))):
        raise CIProofError("invalid run attempt: %r" % value)
    return result


def _safe_base_url(value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment:
        raise CIProofError("base URL must be an HTTP(S) origin without query/fragment")
    if parsed.scheme != "https":
        host = (parsed.hostname or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise CIProofError("plain HTTP is allowed only for a loopback test server")
    return base


def _validate_repository(value: str) -> str:
    repository = str(value or "").strip().strip("/")
    if (not repository or repository.startswith("-") or "\\" in repository
            or any(part in ("", ".", "..") for part in repository.split("/"))):
        raise CIProofError("invalid provider repository path")
    return repository


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise CIProofError("refusing cross-origin redirect while provider credentials are attached")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ProviderAdapter:
    """Small authenticated JSON/bytes HTTP adapter shared by CI and MR gates."""

    provider = ""
    api_prefix = ""

    def __init__(self, repository, *, base_url, token=None, timeout=10.0, opener=None):
        self.repository = _validate_repository(repository)
        self.base_url = _safe_base_url(base_url)
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise CIProofError("HTTP timeout must be positive")
        self.token = token if token is not None else os.environ.get(self.provider.upper() + "_TOKEN", "")
        self.opener = opener or urllib.request.build_opener(_SameOriginRedirect())

    @property
    def repo_path(self):
        raise NotImplementedError

    def _headers(self):
        raise NotImplementedError

    def url(self, path, query=None):
        if not str(path).startswith("/"):
            raise CIProofError("provider API path must be absolute")
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def request(self, path, *, method="GET", data=None, query=None, expected=(200,), limit=MAX_JSON_BYTES):
        body = None
        headers = self._headers()
        headers["Accept"] = "application/json"
        if data is not None:
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = self.url(path, query)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
            try:
                status = getattr(response, "status", response.getcode())
                payload = response.read(limit + 1)
                final_url = response.geturl()
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            payload = exc.read(65536)
            raise CIProofError("provider HTTP %s for %s: %s" % (
                exc.code, url, payload.decode("utf-8", errors="replace")[:500])) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CIProofError("provider request failed for %s: %s" % (url, exc)) from exc
        if status not in expected:
            raise CIProofError("provider HTTP %s for %s (expected %s)" % (status, url, expected))
        if len(payload) > limit:
            raise CIProofError("provider response exceeds %d bytes" % limit)
        return payload, final_url

    def get_json(self, path, *, query=None):
        payload, _ = self.request(path, query=query)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise CIProofError("provider returned invalid JSON for %s: %s" % (path, exc)) from exc
        if not isinstance(value, (dict, list)):
            raise CIProofError("provider JSON root must be an object or array")
        return value

    def request_json(self, path, *, method, data):
        payload, _ = self.request(path, method=method, data=data)
        try:
            return json.loads(payload.decode("utf-8")) if payload else {}
        except (UnicodeError, ValueError) as exc:
            raise CIProofError("provider mutation returned invalid JSON: %s" % exc) from exc

    def _probe(self, checks, key, function):
        try:
            value = function()
        except (CIProofError, KeyError, TypeError, ValueError, OSError):
            checks[key] = "unknown"
            return None
        checks[key] = value if value in (True, False, "unknown") else bool(value)
        return value

    def _capability_document(self, mode, checks, details):
        if mode not in MODES:
            raise CIProofError("unsupported closeout mode: %r" % mode)
        core = ("api", "auth", "workflow", "permissions", "post_merge_read")
        required = core + (("protected_branch", "required_checks", "expected_sha_merge")
                           if mode == "server-enforced" else ())
        ok = all(checks.get(key) is True for key in required)
        if mode in ("local-human", "pr"):
            ok = False
            details["mode"] = "%s never consumes CI proof as a merge authorization" % mode
            enforcement = "not-applicable"
        elif mode == "cooperative-ci":
            enforcement = "not-server-enforced"
            details["enforcement"] = "cooperative-ci has no server-side merge enforcement guarantee"
        elif ok:
            enforcement = "verified"
        else:
            enforcement = "unavailable"
        return {
            "schema": "agent-dev.ci-capabilities/v2", "provider": self.provider,
            "repository": self.repository, "mode": mode, "checks": checks,
            "ok": ok, "server_enforcement": enforcement, "details": details,
        }

    def fetch_run(self, run_id):
        raise NotImplementedError

    def download_verdict(self, run_id, artifact_name):
        raise NotImplementedError

    def capabilities(self, *, workflow, branch, mode):
        raise NotImplementedError


class GiteaAdapter(ProviderAdapter):
    provider = "gitea"

    @property
    def repo_path(self):
        return "/api/v1/repos/" + "/".join(urllib.parse.quote(p, safe="") for p in self.repository.split("/"))

    def _headers(self):
        return {"Authorization": "token " + self.token} if self.token else {"User-Agent": "agent-dev-ci-proof"}

    def fetch_run(self, run_id):
        return self.get_json(self.repo_path + "/actions/runs/" + urllib.parse.quote(_string_id(run_id, "run_id"), safe=""))

    def _workflow_visible(self, workflow, run=None):
        values = []
        if isinstance(run, dict):
            values.extend(run.get(k) for k in ("workflow", "workflow_name", "workflow_id", "name", "path"))
        if any(str(v) == str(workflow) for v in values if v is not None):
            return True
        try:
            data = self.get_json(self.repo_path + "/actions/workflows")
        except CIProofError:
            return "unknown"
        entries = data.get("workflows", []) if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return "unknown"
        for item in entries:
            if not isinstance(item, dict):
                continue
            if any(str(item.get(k)) == str(workflow) for k in ("id", "name", "path") if item.get(k) is not None):
                return True
        return False

    def download_verdict(self, run_id, artifact_name):
        listing = self.get_json(self.repo_path + "/actions/runs/" + urllib.parse.quote(str(run_id), safe="") + "/artifacts")
        artifacts = listing.get("artifacts", []) if isinstance(listing, dict) else listing
        if not isinstance(artifacts, list):
            raise CIProofError("Gitea artifact listing is not an array")
        matches = [a for a in artifacts if isinstance(a, dict) and str(a.get("name")) in (artifact_name, artifact_name.removesuffix(".json"))]
        if len(matches) != 1:
            raise CIProofError("expected exactly one Gitea verdict artifact named %s; found %d" % (artifact_name, len(matches)))
        artifact = matches[0]
        ident = _string_id(artifact.get("id"), "artifact id")
        path = self.repo_path + "/actions/artifacts/" + urllib.parse.quote(ident, safe="") + "/zip"
        payload, final_url = self.request(path, limit=MAX_ARTIFACT_BYTES)
        verdict = _verdict_from_artifact(payload, artifact_name)
        return verdict, {
            "id": ident, "name": str(artifact["name"]), "url": final_url,
            "bytes": len(payload), "sha256": _sha(payload), "fetched_at": _utc_now(),
        }

    def capabilities(self, *, workflow, branch, mode):
        checks = {k: "unknown" for k in (
            "api", "auth", "workflow", "protected_branch", "required_checks", "run_attempt",
            "permissions", "expected_sha_merge", "target_cas_merge", "post_merge_read")}
        details = {"target_cas_merge": "not-supported-by-official-Gitea-MR-API"}
        repo = self._probe(checks, "api", lambda: self.get_json(self.repo_path))
        if isinstance(repo, dict):
            permissions = repo.get("permissions")
            if isinstance(permissions, dict):
                checks["permissions"] = bool(permissions.get("admin") or permissions.get("push") or permissions.get("maintain"))
            # Gitea's documented merge body supports expected_head_sha for the
            # source.  It has no target-old-OID CAS parameter.
            checks["expected_sha_merge"] = True
            checks["target_cas_merge"] = False
            checks["run_attempt"] = True
        self._probe(checks, "auth", lambda: isinstance(self.get_json("/api/v1/user"), dict) and bool(self.token))
        self._probe(checks, "workflow", lambda: self._workflow_visible(workflow))
        def pull_read():
            value = self.get_json(self.repo_path + "/pulls", query={"state": "open", "limit": 1})
            if not isinstance(value, list):
                return "unknown"
            return isinstance(value, list)
        self._probe(checks, "post_merge_read", pull_read)
        quoted_branch = urllib.parse.quote(branch, safe="")
        def protection():
            value = self.get_json(self.repo_path + "/branches/" + quoted_branch)
            if not isinstance(value, dict) or "protected" not in value:
                return "unknown"
            return value["protected"] is True
        protected = self._probe(checks, "protected_branch", protection)
        if protected is True:
            def required():
                value = self.get_json(self.repo_path + "/branches/" + quoted_branch + "/protection")
                contexts = value.get("required_status_check_contexts") or value.get("status_check_contexts")
                enabled = value.get("enable_status_check") or value.get("required_status_checks")
                return bool(enabled and isinstance(contexts, list) and contexts)
            self._probe(checks, "required_checks", required)
        return self._capability_document(mode, checks, details)


class GitLabAdapter(ProviderAdapter):
    provider = "gitlab"

    @property
    def repo_path(self):
        return "/api/v4/projects/" + urllib.parse.quote(self.repository, safe="")

    def _headers(self):
        return {"PRIVATE-TOKEN": self.token} if self.token else {"User-Agent": "agent-dev-ci-proof"}

    def fetch_run(self, run_id):
        return self.get_json(self.repo_path + "/pipelines/" + urllib.parse.quote(_string_id(run_id, "run_id"), safe=""))

    def _workflow_visible(self, workflow, run=None):
        # A GitLab pipeline does not expose an Actions-style workflow name.
        # Only the official repository-files API can prove a CI YAML workflow.
        if str(workflow).lower().endswith((".yml", ".yaml")):
            try:
                query = {"ref": _short_ref(_full_ref(run.get("ref")))} if isinstance(run, dict) and run.get("ref") else None
                path = self.repo_path + "/repository/files/" + urllib.parse.quote(str(workflow), safe="")
                value = self.get_json(path, query=query)
                return isinstance(value, dict) and bool(value.get("content"))
            except CIProofError:
                return "unknown"
        return "unknown"

    def download_verdict(self, run_id, artifact_name):
        jobs = self.get_json(self.repo_path + "/pipelines/" + urllib.parse.quote(str(run_id), safe="") + "/jobs", query={"per_page": 100})
        if not isinstance(jobs, list):
            raise CIProofError("GitLab jobs response is not an array")
        candidates = []
        for job in jobs:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            archives = []
            if isinstance(job.get("artifacts_file"), dict):
                archives.append(job["artifacts_file"])
            archives.extend(item for item in (job.get("artifacts") or []) if isinstance(item, dict))
            if any(str(item.get("filename")) == "artifacts.zip" or item.get("file_type") == "archive" for item in archives):
                candidates.append(job)
        matches = []
        for job in candidates:
            path = self.repo_path + "/jobs/" + urllib.parse.quote(str(job["id"]), safe="") + "/artifacts"
            payload, final_url = self.request(path, limit=MAX_ARTIFACT_BYTES)
            try:
                verdict = _verdict_from_artifact(payload, artifact_name, require_zip=True)
            except CIProofError:
                continue
            matches.append((verdict, payload, final_url, job))
        if len(matches) != 1:
            raise CIProofError("expected exactly one GitLab artifacts.zip containing %s; found %d" % (artifact_name, len(matches)))
        verdict, payload, final_url, job = matches[0]
        return verdict, {
            "id": str(job["id"]), "name": artifact_name, "url": final_url,
            "bytes": len(payload), "sha256": _sha(payload), "fetched_at": _utc_now(),
        }

    def capabilities(self, *, workflow, branch, mode):
        checks = {k: "unknown" for k in (
            "api", "auth", "workflow", "protected_branch", "required_checks", "run_attempt",
            "permissions", "expected_sha_merge", "target_cas_merge", "post_merge_read")}
        details = {"target_cas_merge": "not-supported-by-official-GitLab-MR-API",
                   "run_attempt": "not-supported-by-official-pipeline-API"}
        project = self._probe(checks, "api", lambda: self.get_json(self.repo_path))
        if isinstance(project, dict):
            levels = []
            permissions = project.get("permissions")
            if isinstance(permissions, dict):
                for key in ("project_access", "group_access"):
                    entry = permissions.get(key)
                    if isinstance(entry, dict) and entry.get("access_level") is not None:
                        levels.append(entry["access_level"])
                checks["permissions"] = any(isinstance(v, int) and not isinstance(v, bool) and v >= 30 for v in levels) if levels else False
            # GitLab's documented ``sha`` merge parameter guards the MR source
            # SHA.  No target-old-OID CAS exists; custom project.capabilities
            # fields are deliberately ignored as untrusted/non-standard.
            checks["expected_sha_merge"] = True
            checks["target_cas_merge"] = False
            checks["run_attempt"] = False
            details["only_allow_merge_if_pipeline_succeeds"] = project.get("only_allow_merge_if_pipeline_succeeds", "unknown")
        self._probe(checks, "auth", lambda: isinstance(self.get_json("/api/v4/user"), dict) and bool(self.token))
        if isinstance(project, dict) and checks["workflow"] == "unknown":
            if str(workflow).lower().endswith((".yml", ".yaml")):
                self._probe(checks, "workflow", lambda: self._workflow_visible(workflow))
            else:
                # GitLab has no Actions-style workflow enumeration API.  Only an
                # explicit provider capability, CI YAML file, or run metadata can
                # prove the configured workflow.
                details["workflow"] = "workflow must be exposed by project capabilities, CI YAML, or the fetched pipeline"
        def mr_read():
            value = self.get_json(self.repo_path + "/merge_requests", query={"state": "opened", "per_page": 1})
            return isinstance(value, list)
        self._probe(checks, "post_merge_read", mr_read)
        quoted_branch = urllib.parse.quote(branch, safe="")
        def protected():
            value = self.get_json(self.repo_path + "/protected_branches/" + quoted_branch)
            return isinstance(value, dict) and bool(value.get("name"))
        protection = self._probe(checks, "protected_branch", protected)
        if protection is True:
            def required():
                value = self.get_json(self.repo_path + "/protected_branches/" + quoted_branch)
                checks_value = value.get("required_status_checks")
                if isinstance(checks_value, list):
                    return bool(checks_value)
                if isinstance(checks_value, dict):
                    return bool(checks_value.get("checks"))
                return isinstance(project, dict) and project.get("only_allow_merge_if_pipeline_succeeds") is True
            self._probe(checks, "required_checks", required)
        return self._capability_document(mode, checks, details)


def get_adapter(provider, repository, *, base_url, token=None, timeout=10.0, opener=None):
    adapters = {"gitea": GiteaAdapter, "gitlab": GitLabAdapter}
    try:
        cls = adapters[str(provider).lower()]
    except KeyError:
        raise CIProofError("provider must be gitea or gitlab")
    return cls(repository, base_url=base_url, token=token, timeout=timeout, opener=opener)


def capabilities(provider, repository, *, base_url, workflow, branch, mode,
                 token=None, timeout=10.0, opener=None):
    """Return observed capabilities; absence of evidence is always ``unknown``."""
    if mode in ("local-human", "pr"):
        raise CIProofError("%s cannot be upgraded/downgraded into an online CI mode" % mode)
    adapter = get_adapter(provider, repository, base_url=base_url, token=token, timeout=timeout, opener=opener)
    return adapter.capabilities(workflow=workflow, branch=branch, mode=mode)


def require_capabilities(document, mode, *, remote_merge=False):
    if not isinstance(document, dict) or document.get("mode") != mode:
        raise CapabilityError("capability response mode mismatch; refusing automatic downgrade")
    if document.get("ok") is not True:
        unavailable = sorted(k for k, v in (document.get("checks") or {}).items()
                             if v is not True and (mode == "server-enforced" or k in
                                 ("api", "auth", "workflow", "permissions", "post_merge_read")))
        raise CapabilityError("required CI capabilities are false/unknown: " + ", ".join(unavailable))
    if remote_merge and mode == "server-enforced" and (document.get("checks") or {}).get("target_cas_merge") is not True:
        raise CapabilityError("server-enforced remote MR requires target-old-OID CAS; official provider API does not support it")
    if mode == "cooperative-ci" and document.get("server_enforcement") != "not-server-enforced":
        raise CapabilityError("cooperative-ci must explicitly report absence of server enforcement")
    if mode == "server-enforced" and document.get("server_enforcement") != "verified":
        raise CapabilityError("server-enforced mode requires verified server-side capabilities")
    return document


def _verdict_from_artifact(payload, artifact_name, require_zip=False):
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        archive = None
    if require_zip and archive is None:
        raise CIProofError("GitLab artifact is not a valid artifacts.zip")
    if archive is not None:
        candidates = []
        requested = Path(artifact_name)
        if requested.is_absolute() or ".." in requested.parts:
            raise CIProofError("verdict artifact path must be relative and free of '..'")
        names = archive.namelist()
        exact = [name for name in names if name == requested.as_posix()]
        if exact:
            candidates = exact
        else:
            # A bare filename may match a unique basename anywhere in the ZIP;
            # nested requested paths never get relaxed to a different location.
            if len(requested.parts) == 1:
                candidates = [name for name in names if Path(name).name == artifact_name]
            if not candidates and len(requested.parts) == 1 and len([name for name in names if name.lower().endswith(".json")]) == 1:
                candidates = [name for name in names if name.lower().endswith(".json")]
        if len(candidates) != 1:
            raise CIProofError("verdict ZIP must contain exactly one path-matched JSON verdict")
        name = candidates[0]
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise CIProofError("verdict ZIP contains an unsafe path")
        info = archive.getinfo(name)
        if info.file_size > MAX_JSON_BYTES:
            raise CIProofError("verdict JSON is too large")
        data = archive.read(name)
    else:
        data = payload
    if len(data) > MAX_JSON_BYTES:
        raise CIProofError("verdict JSON is too large")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise CIProofError("artifact %s is not valid UTF-8 JSON: %s" % (artifact_name, exc)) from exc
    if not isinstance(value, dict):
        raise CIProofError("verdict artifact root must be an object")
    return value


def _git_tree(repo, commit):
    try:
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", str(commit) + "^{tree}"],
                                capture_output=True, text=True)
    except OSError as exc:
        raise CIProofError("cannot execute git for candidate tree verification: %s" % exc)
    if result.returncode:
        raise CIProofError("candidate commit is unavailable in repo: %s" % result.stderr.strip())
    tree = result.stdout.strip()
    if not _OID.fullmatch(tree):
        raise CIProofError("git returned an invalid tree OID")
    return tree


def _schema_errors(proof):
    try:
        import json_schema
        return json_schema.load_validate(SCHEMA_PATH, proof)
    except (ImportError, ValueError, OSError) as exc:
        return ["CI proof schema validation unavailable: %s" % exc]


def validate_proof(proof, *, repo=None, expected_ref=None, expected_commit=None,
                   expected_tree=None, expected_run_id=None, expected_attempt=None,
                   expected_event=None):
    """Fail closed on run, artifact, verdict, environment, and Git mismatches."""
    errors = _schema_errors(proof)
    if errors:
        return errors
    try:
        verdict = proof["verdict"]
        comparisons = (
            (verdict["commit"], proof["commit"], "verdict/run commit mismatch"),
            (verdict["tree"], proof["tree"], "verdict/run tree mismatch"),
            (str(verdict["run_id"]), proof["run_id"], "verdict/run ID mismatch"),
        )
        for left, right, message in comparisons:
            if left != right:
                errors.append(message)
        if not _same_ref(verdict["ref"], proof["ref"]):
            errors.append("verdict/run ref mismatch")
        if expected_ref is not None and not _same_ref(proof["ref"], expected_ref):
            errors.append("run ref differs from expected candidate ref")
        if expected_commit is not None and proof["commit"] != expected_commit:
            errors.append("run commit differs from expected candidate")
        if expected_tree is not None and proof["tree"] != expected_tree:
            errors.append("run tree differs from expected candidate")
        if expected_run_id is not None and proof["run_id"] != str(expected_run_id):
            errors.append("run ID differs from requested run")
        if expected_attempt is not None and proof["attempt"] != expected_attempt:
            errors.append("run attempt differs from requested attempt")
        if expected_event is not None and proof["event"] != expected_event:
            errors.append("run event differs from expected event")
        if repo is not None and _git_tree(Path(repo), proof["commit"]) != proof["tree"]:
            errors.append("proof tree is not the actual candidate commit tree")
        executed = verdict.get("executed")
        if not isinstance(executed, list) or not executed:
            errors.append("verdict has no executed test inventory")
        elif any(not isinstance(item, dict) or not item.get("id") or item.get("status") not in ("pass", "quarantined") for item in executed):
            errors.append("verdict executed inventory contains a non-pass/non-quarantined item")
        environment = verdict.get("environment")
        if not isinstance(environment, dict) or not environment.get("name"):
            errors.append("verdict lacks executed environment attribution")
        attribution = verdict.get("failure_attribution")
        if verdict["new_failures"] or verdict["unattributed_failures"]:
            errors.append("verdict contains new/unattributed failures")
        if verdict["attribution_status"] == "completed" and not isinstance(attribution, dict):
            errors.append("completed failure attribution lacks structured attribution evidence")
        quarantine = verdict.get("quarantine")
        if verdict["quarantined_n"]:
            if not verdict.get("quarantine_config_sha256"):
                errors.append("quarantined tests lack quarantine config hash")
            if not isinstance(quarantine, dict) or quarantine.get("config_sha256") != verdict.get("quarantine_config_sha256"):
                errors.append("quarantine evidence does not match config hash")
        artifact = proof["artifact"]
        if artifact["bytes"] < 1 or not artifact["url"] or not artifact["fetched_at"]:
            errors.append("artifact download metadata is incomplete")
    except (CIProofError, KeyError, TypeError, ValueError, OSError) as exc:
        errors.append("proof validation failed closed: %s" % exc)
    return errors


def _normalize_run(adapter, run, workflow, run_id, expected_attempt=None):
    if not isinstance(run, dict):
        raise CIProofError("provider run response must be an object")
    actual_id = run.get("id", run.get("run_id"))
    if actual_id is None or str(actual_id) != str(run_id):
        raise CIProofError("provider returned a different run ID")
    if adapter.provider == "gitlab":
        # GitLab pipelines have no native run-attempt identity.  Never invent
        # attempt=1 and never trust a non-standard extension field.
        attempt, attempt_status = None, "not-supported"
        if expected_attempt is not None:
            raise CIProofError("GitLab run attempt is not supported by the official pipeline API")
    else:
        attempt, attempt_status = _attempt(run.get("run_attempt", run.get("attempt"))), "verified"
        if expected_attempt is not None and attempt != expected_attempt:
            raise CIProofError("run attempt differs from expected attempt")
    event = str(run.get("event", run.get("source")) or "").strip()
    if not event:
        raise CIProofError("provider run does not expose event/source")
    ref = _full_ref(run.get("ref", run.get("head_branch")))
    commit = str(run.get("head_sha", run.get("sha")) or "").lower()
    if not _OID.fullmatch(commit):
        raise CIProofError("provider run does not expose a full head commit")
    raw_status = str(run.get("status") or "").lower()
    raw_conclusion = str(run.get("conclusion") or "").lower()
    if adapter.provider == "gitlab":
        conclusion = raw_conclusion or raw_status
        status = "completed" if raw_status in ("success", "failed", "canceled", "cancelled", "skipped", "manual") else raw_status
    else:
        status, conclusion = raw_status, raw_conclusion
    if status != "completed" or conclusion != "success":
        raise CIProofError("run is not a completed success (status=%s conclusion=%s)" % (status or "unknown", conclusion or "unknown"))
    workflow_visible = adapter._workflow_visible(workflow, run)
    if workflow_visible is not True:
        raise CIProofError("workflow is not proven for this run (observed=%s)" % workflow_visible)
    run_url = str(run.get("html_url", run.get("web_url")) or adapter.url(adapter.repo_path + "/actions/runs/" + str(run_id)))
    return {
        "run_id": str(run_id), "attempt": attempt, "attempt_status": attempt_status,
        "event": event, "ref": ref,
        "commit": commit, "status": status, "conclusion": conclusion, "run_url": run_url,
    }


def fetch_proof(provider, repository, workflow, run_id, *, base_url, token=None,
                repo=None, expected_ref=None, expected_commit=None, expected_tree=None,
                expected_attempt=None, expected_event=None, artifact_name="verdict.json",
                output=None, timeout=10.0, opener=None):
    """Fetch a run and its verdict artifact online and return a v2 proof."""
    adapter = get_adapter(provider, repository, base_url=base_url, token=token, timeout=timeout, opener=opener)
    run = adapter.fetch_run(run_id)
    normalized = _normalize_run(adapter, run, workflow, run_id, expected_attempt)
    if expected_attempt is not None and normalized["attempt"] != expected_attempt:
        raise CIProofError("run attempt differs from expected attempt")
    if expected_event is not None and normalized["event"] != expected_event:
        raise CIProofError("run event differs from expected event")
    if expected_ref is not None and not _same_ref(normalized["ref"], expected_ref):
        raise CIProofError("run ref differs from expected candidate ref")
    if expected_commit is not None and normalized["commit"] != expected_commit:
        raise CIProofError("run commit differs from expected candidate")
    tree = _git_tree(Path(repo), normalized["commit"]) if repo is not None else expected_tree
    if not tree or not _OID.fullmatch(str(tree)):
        raise CIProofError("candidate tree must come from the actual local commit or an explicit expected tree")
    if expected_tree is not None and tree != expected_tree:
        raise CIProofError("candidate commit tree differs from expected tree")
    verdict, artifact = adapter.download_verdict(run_id, artifact_name)
    proof = {
        "schema": SCHEMA, "provider": adapter.provider, "repository": adapter.repository,
        "base_url": adapter.base_url,
        "workflow": str(workflow), "tree": tree, "artifact": artifact, "verdict": verdict,
        **normalized,
    }
    errors = validate_proof(proof, repo=repo, expected_ref=expected_ref,
                            expected_commit=expected_commit, expected_tree=tree,
                            expected_run_id=run_id, expected_attempt=expected_attempt,
                            expected_event=expected_event)
    if errors:
        raise CIProofError("CI proof rejected:\n  " + "\n  ".join(errors))
    if output is not None:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return proof


def _immutable_proof(proof):
    value = copy.deepcopy(proof)
    # fetched_at is observation metadata; every security-relevant byte/hash and
    # identity field remains part of the comparison.
    if isinstance(value.get("artifact"), dict):
        value["artifact"].pop("fetched_at", None)
    return value


def recheck_proof(proof, *, base_url=None, token=None, repo=None, expected_ref=None,
                  expected_commit=None, expected_tree=None, expected_event=None,
                  timeout=10.0, opener=None):
    """Repeat provider and artifact reads at promotion; never trust a file replay."""
    if not isinstance(proof, dict):
        raise CIProofError("proof must be an object returned by fetch_proof")
    required = ("provider", "repository", "workflow", "run_id", "attempt", "ref", "commit", "tree", "artifact")
    if any(key not in proof for key in required):
        raise CIProofError("cannot recheck an incomplete proof")
    artifact = proof["artifact"]
    if not isinstance(artifact, dict) or not artifact.get("name"):
        raise CIProofError("cannot recheck proof without artifact name")
    fresh = fetch_proof(
        proof["provider"], proof["repository"], proof["workflow"], proof["run_id"],
        base_url=base_url or proof.get("base_url") or _default_base_url(proof["provider"]),
        token=token, repo=repo,
        expected_ref=expected_ref or proof["ref"], expected_commit=expected_commit or proof["commit"],
        expected_tree=expected_tree or proof["tree"], expected_attempt=proof["attempt"],
        expected_event=expected_event or proof["event"], artifact_name=artifact["name"],
        timeout=timeout, opener=opener,
    )
    if _immutable_proof(fresh) != _immutable_proof(proof):
        raise CIProofError("online proof changed since fetch; ref/run/artifact/verdict must remain identical")
    return fresh


def _default_base_url(provider):
    if provider == "gitea":
        return os.environ.get("GITEA_URL", "https://gitea.com")
    if provider == "gitlab":
        return os.environ.get("GITLAB_URL", "https://gitlab.com")
    raise CIProofError("provider must be gitea or gitlab")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--provider", choices=("gitea", "gitlab"), required=True)
    common.add_argument("--repository", required=True)
    common.add_argument("--base-url", help="Defaults to GITEA_URL/GITLAB_URL or the provider public host")
    common.add_argument("--token-env", help="Read token from this environment variable (never from CLI)")
    common.add_argument("--timeout", type=float, default=10.0)
    cap = sub.add_parser("capabilities", parents=[common])
    cap.add_argument("--workflow", required=True)
    cap.add_argument("--branch", required=True)
    cap.add_argument("--mode", choices=MODES, required=True)
    fetch = sub.add_parser("fetch", parents=[common])
    fetch.add_argument("--workflow", required=True)
    fetch.add_argument("--run-id", required=True)
    fetch.add_argument("--repo", help="Local checkout used to verify the actual candidate tree")
    fetch.add_argument("--ref")
    fetch.add_argument("--commit")
    fetch.add_argument("--tree")
    fetch.add_argument("--attempt", type=int)
    fetch.add_argument("--event")
    fetch.add_argument("--artifact", default="verdict.json")
    fetch.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        base_url = args.base_url or _default_base_url(args.provider)
        token = os.environ.get(args.token_env, "") if args.token_env else None
        if args.command == "capabilities":
            result = capabilities(args.provider, args.repository, base_url=base_url,
                                  workflow=args.workflow, branch=args.branch, mode=args.mode,
                                  token=token, timeout=args.timeout)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return 0 if result["ok"] else 1
        result = fetch_proof(args.provider, args.repository, args.workflow, args.run_id,
                             base_url=base_url, token=token, repo=args.repo,
                             expected_ref=args.ref, expected_commit=args.commit,
                             expected_tree=args.tree, expected_attempt=args.attempt,
                             expected_event=args.event, artifact_name=args.artifact,
                             output=args.output, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (CIProofError, OSError, ValueError) as exc:
        print("ci-proof: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
