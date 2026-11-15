#!/usr/bin/env python3
"""测试用 NDJSON 假宿主：记录请求并按 CODEX/ZCODE 模式应答。"""
import json
import os
import sys

mode = os.environ["FAKE_HOST"]
log = os.environ["FAKE_HOST_LOG"]
current = os.environ.get("FAKE_CURRENT_TITLE", "")
source = os.environ.get("FAKE_TITLE_SOURCE", "")
state_path = os.environ.get("FAKE_HOST_STATE")
known_session = None
if state_path and os.path.exists(state_path):
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
        current = state.get("title", current)
        known_session = state.get("session")


def save_state():
    if state_path:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"title": current, "session": known_session}, f)


def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def record(value):
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False) + "\n")


for line in sys.stdin:
    message = json.loads(line)
    record(message)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialized":
        continue
    if mode == "codex":
        if method == "initialize":
            send({"id": request_id, "result": {}})
        elif method == "thread/read":
            if os.environ.get("FAKE_READ_FAIL"):
                send({"id": request_id, "error": {"message": "thread not loaded"}})
            elif os.environ.get("FAKE_MISSING_SESSION") or (known_session and message["params"]["threadId"] != known_session):
                send({"id": request_id, "result": {"thread": None}})
            else:
                thread = {"id": message["params"]["threadId"], "name": current or None,
                          "agent_type": os.environ.get("FAKE_ROLE", "supervisor-review"),
                          "review": json.loads(os.environ.get("FAKE_REVIEW", "{}"))}
                send({"id": request_id, "result": {"thread": thread}})
        elif method == "thread/name/set":
            current = message["params"]["name"]
            known_session = message["params"]["threadId"]
            save_state()
            send({"id": request_id, "result": {}})
        else:
            send({"id": request_id, "error": {"message": "unexpected " + str(method)}})
    else:
        if method == "session/list":
            if os.environ.get("FAKE_MISSING_SESSION"):
                send({"id": request_id, "result": {"sessions": []}})
                continue
            session_id = (message.get("params", {}).get("sessionIds") or ["sess_root"])[0]
            if known_session and session_id != known_session:
                send({"id": request_id, "result": {"sessions": []}})
                continue
            session = {"sessionId": session_id, "sessionKind": "interactive", "title": current,
                       "subagentType": os.environ.get("FAKE_ROLE", "supervisor-review"),
                       "review": json.loads(os.environ.get("FAKE_REVIEW", "{}"))}
            if source:
                session["titleSource"] = source
            send({"id": request_id, "result": {"sessions": [session]}})
        elif method == "session/resume":
            send({"id": request_id, "result": {"sessionId": "sess_root"}})
        elif method == "v4/command":
            current = message["params"]["payload"]["title"]
            known_session = message["params"]["sessionId"]
            save_state()
            send({"id": request_id, "result": {"status": "accepted"}})
        else:
            send({"id": request_id, "error": {"message": "unexpected " + str(method)}})
