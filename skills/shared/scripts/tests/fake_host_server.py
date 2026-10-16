#!/usr/bin/env python3
"""测试用 NDJSON 假宿主：记录请求并按 CODEX/ZCODE 模式应答。"""
import json
import os
import sys

mode = os.environ["FAKE_HOST"]
log = os.environ["FAKE_HOST_LOG"]
current = os.environ.get("FAKE_CURRENT_TITLE", "")
source = os.environ.get("FAKE_TITLE_SOURCE", "")


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
            else:
                send({"id": request_id, "result": {"thread": {"name": current or None}}})
        elif method == "thread/name/set":
            current = message["params"]["name"]
            send({"id": request_id, "result": {}})
        else:
            send({"id": request_id, "error": {"message": "unexpected " + str(method)}})
    else:
        if method == "session/list":
            session = {"sessionId": "sess_root", "sessionKind": "interactive", "title": current}
            if source:
                session["titleSource"] = source
            send({"id": request_id, "result": {"sessions": [session]}})
        elif method == "session/resume":
            send({"id": request_id, "result": {"sessionId": "sess_root"}})
        elif method == "v4/command":
            current = message["params"]["payload"]["title"]
            send({"id": request_id, "result": {"status": "accepted"}})
        else:
            send({"id": request_id, "error": {"message": "unexpected " + str(method)}})
