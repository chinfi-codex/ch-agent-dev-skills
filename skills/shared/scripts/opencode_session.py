#!/usr/bin/env python3
"""OpenCode session adapter for agent-dev review binding.

Contract (host_adapters.py):
  argv[1] or AGENT_DEV_SESSION_ID = OpenCode session id (ses_...)
  AGENT_DEV_SESSION_QUERY=review
  AGENT_DEV_SESSION_WRITE=1 for write
  AGENT_DEV_REVIEW_MARKER / AGENT_DEV_REVIEW_METADATA
Prints {"session": {...}} JSON.

OpenCode exposes sessions through `opencode export <id>`; the review binding is
stored as a session-scoped sidecar under ~/.config/opencode/agent-dev-review/.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

BINDING_DIR = os.path.join(os.path.expanduser("~"), ".config", "opencode", "agent-dev-review")
MARKER_RE = re.compile(r"(?:^|\s)AGENT_DEV_REVIEW:[A-Za-z0-9_-]+(?=\s|$)")


def fail(message: str) -> None:
    sys.stderr.write(message + "\n")
    raise SystemExit(2)


def export_session(session_id: str) -> dict:
    commands = [
        ["opencode", "--standalone", "session", "export", session_id],  # v2
        ["opencode", "export", session_id],                              # v1
    ]
    result = None
    for command in commands:
        try:
            candidate = subprocess.run(command, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if candidate.returncode == 0 and candidate.stdout.strip():
            result = candidate
            break
    if result is None:
        fail("opencode session export failed (v1/v2 command not available)")
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        fail("opencode export did not return JSON: %s" % exc)
    if not isinstance(value, dict):
        fail("opencode export JSON must be an object")
    info = value.get("info") if isinstance(value.get("info"), dict) else {}
    if not info.get("id"):
        fail("opencode export lacks session info.id")
    title = info.get("title") or info.get("slug") or str(info["id"])
    location = info.get("location") if isinstance(info.get("location"), dict) else {}
    return {
        "id": str(info["id"]), "name": title, "title": title,
        "role": str(info.get("agent") or info.get("mode") or "build"),
        "cwd": info.get("directory") or location.get("directory") or location.get("cwd"),
    }


def binding_path(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    return os.path.join(BINDING_DIR, safe + ".json")


def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AGENT_DEV_SESSION_ID", "")
    if not session_id:
        fail("missing session id")
    record = export_session(session_id)
    sidecar = binding_path(session_id)
    if os.path.exists(sidecar):
        try:
            with open(sidecar, encoding="utf-8") as handle:
                bound = json.load(handle)
            if isinstance(bound, dict):
                record.update(bound)
        except (OSError, ValueError):
            pass
    record["id"] = str(record.get("id") or session_id)
    if os.environ.get("AGENT_DEV_SESSION_WRITE") == "1":
        marker = os.environ.get("AGENT_DEV_REVIEW_MARKER", "")
        if not marker:
            fail("missing review marker")
        title = str(record.get("title") or record.get("name") or record["id"])
        title = (re.sub(r"AGENT_DEV_REVIEW:[A-Za-z0-9_-]+", marker, title, count=1)
                 if MARKER_RE.search(title) else title.rstrip() + " " + marker)
        record["title"] = title
        record["name"] = title
        record["role"] = "supervisor-review"
        try:
            record["review"] = json.loads(os.environ.get("AGENT_DEV_REVIEW_METADATA", "{}"))
        except ValueError:
            record["review"] = {}
        os.makedirs(BINDING_DIR, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".binding.", dir=BINDING_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, sidecar)
    print(json.dumps({"session": record}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
