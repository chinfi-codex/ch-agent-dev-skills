#!/usr/bin/env python3
"""session_title.py 的阶段投影、命名、绑定与 Hook 写回契约测试。"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "session_title.py"
SPEC = importlib.util.spec_from_file_location("session_title", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SessionTitleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="session title 测试 ")
        self.root = Path(self.tmp.name) / "repo"
        self.docs = self.root / "docs/features/交易链路/订单超时"
        self.dev = self.root / "dev/features/交易链路/订单超时"
        (self.dev / "issues").mkdir(parents=True)
        self.docs.mkdir(parents=True)
        self.host_root = self.root / "host"
        self.env = mock.patch.dict(os.environ, {
            "SESSION_TITLE_STATE": str(self.root / "state.json"),
            "SESSION_TITLE_HOST_ROOT": str(self.host_root),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def brief(self, status="待写PRD"):
        return self.write(self.docs / "订单超时-feature-brief-2026-10-16.md",
                          "---\nstatus: {}\n---\n".format(status))

    def prd(self, status="草稿", date="2026-10-16"):
        return self.write(self.docs / "订单超时-prd-{}.md".format(date),
                          "---\nstatus: {}\n---\n".format(status))

    def tech(self, status="草稿"):
        return self.write(self.dev / "订单超时-tech-spec-2026-10-16.md",
                          "---\nstatus: {}\n---\n".format(status))

    def issue(self, number=3, status="confirmed", title="修复支付回调超时"):
        return self.write(self.dev / "issues/issue-{:03d}-callback.md".format(number),
                          "---\nissue-id: issue-{0:03d}\ntitle: {1}\nstatus: {2}\n---\n".format(number, title, status))

    def feature_binding(self, role=None):
        return MODULE.normalize_binding(self.root, feature="docs/features/交易链路/订单超时", role=role)

    def issue_binding(self, role=None):
        return MODULE.normalize_binding(
            self.root, issue="dev/features/交易链路/订单超时/issues/issue-003-callback.md", role=role)

    def test_requirement_stages_and_latest_date(self):
        self.brief("待验证")
        self.assertEqual(MODULE.compute_title(self.feature_binding()), "[需求澄清·待验证] 订单超时")
        self.prd("草稿", "2026-10-15")
        self.prd("定稿", "2026-10-16")
        self.assertEqual(MODULE.compute_title(self.feature_binding()), "[技术方案·待出方案] 订单超时")
        self.write(self.docs / "订单超时-pd-review-report-2026-10-16.md", "---\nverdict: 阻塞\n---\n")
        self.assertEqual(MODULE.compute_title(self.feature_binding()), "[PRD/评审·阻塞] 订单超时")
        self.tech("草稿")
        self.assertEqual(MODULE.compute_title(self.feature_binding()), "[技术方案·待确认] 订单超时")
        self.tech("已确认（待拆解）")
        self.assertEqual(MODULE.compute_title(self.feature_binding()), "[待拆票] 订单超时")

    def test_issue_names_review_and_truncation(self):
        self.issue(status="mr-submitted", title="这是一个特别特别长且不应该完整进入会话名称的票标题")
        title = MODULE.compute_title(self.issue_binding())
        self.assertTrue(title.startswith("[待合并] 订单超时 · issue-003 "))
        self.assertTrue(title.endswith("…"))
        self.assertEqual(
            MODULE.compute_title(self.issue_binding(role="review")),
            MODULE.compute_title(self.issue_binding(role="review")))

    def test_all_issue_status_names(self):
        expected = {
            "proposed": "待确认", "confirmed": "待执行", "in-progress": "实现中",
            "in-review": "评审中", "mr-submitted": "待合并", "needs-human": "需人工",
            "done": "已合并", "unknown": "状态未知",
        }
        for status, label in expected.items():
            with self.subTest(status=status):
                self.issue(status=status)
                self.assertTrue(MODULE.compute_title(self.issue_binding()).startswith("[" + label + "]"))

    def test_supervisor_aggregate(self):
        self.issue(1, "done", "甲")
        self.issue(2, "needs-human", "乙")
        self.issue(3, "in-progress", "丙")
        self.assertEqual(MODULE.compute_title(self.feature_binding(role="supervisor")),
                         "[开发 1/3·1票需处理] 订单超时")
        self.issue(2, "done", "乙")
        self.issue(3, "done", "丙")
        self.assertEqual(MODULE.compute_title(self.feature_binding(role="supervisor")),
                         "[已合并 3/3] 订单超时")

    def test_prompt_inference_is_unique_and_explicit(self):
        issue = self.issue()
        event = {"prompt": "/implement issue-003", "cwd": str(self.root)}
        binding = MODULE.infer_binding(event, self.root)
        self.assertEqual(binding["issue"], issue.relative_to(self.root).as_posix())
        self.assertEqual(MODULE.infer_binding({"prompt": "看看订单超时"}, self.root), None)

    def test_claude_native_title_and_manual_protection(self):
        self.issue(status="confirmed")
        event = {"session_id": "s1", "cwd": str(self.root), "hook_event_name": "UserPromptSubmit",
                 "prompt": "/implement issue-003"}
        result = MODULE.refresh("claude", event)
        self.assertEqual(result["status"], "native")
        output = MODULE.hook_output("claude", "UserPromptSubmit", result)["hookSpecificOutput"]
        self.assertEqual(output["sessionTitle"], "[待执行] 订单超时 · issue-003 修复支付回调超时")
        self.assertIn("mcp__ccd_session_mgmt__set_session_title", output["additionalContext"])
        self.assertIn('"session_id": "self"', output["additionalContext"])
        saved = MODULE.load_state("claude")["sessions"]["claude:s1"]
        self.assertEqual(saved["last_title"], result["title"])
        event["session_title"] = "我的手工标题"
        result = MODULE.refresh("claude", event)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["reason"], "manual-title")
        forced = MODULE.refresh("claude", event, force=True)
        self.assertEqual(forced["status"], "native")

    def test_claude_non_native_event_does_not_use_transcript_sdk(self):
        self.assertEqual(MODULE.claude_rename({"hook_event_name": "PostToolUse"}, "标题", None),
                         ("标题", "skipped"))

    def test_deepseek_pending_ack_and_manual_protection(self):
        self.issue()
        event = {"session_id": "d1", "cwd": str(self.root), "hook_event_name": "UserPromptSubmit",
                 "prompt": "/implement issue-003", "title_source": "fallback", "session_title": "旧标题"}
        result = MODULE.refresh("deepseek", event)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(MODULE.hook_output("deepseek", "UserPromptSubmit", result),
                         {"action": "rename", "title": result["title"]})
        saved = MODULE.load_state("deepseek")["sessions"]["deepseek:d1"]
        self.assertNotIn("last_title", saved)
        accepted = MODULE.acknowledge("deepseek", "d1", result["title"])
        self.assertEqual(accepted["status"], "written")
        event.update(session_title=result["title"], title_source="user")
        self.assertEqual(MODULE.refresh("deepseek", event)["status"], "unchanged")
        event["session_title"] = "手工标题"
        self.assertEqual(MODULE.refresh("deepseek", event)["status"], "paused")
        self.assertEqual(MODULE.refresh("deepseek", dict(event, origin="subagent"))["reason"], "subagent")

    def test_unbound_hook_does_nothing(self):
        result = MODULE.refresh("claude", {"session_id": "s2", "cwd": str(self.root),
                                           "hook_event_name": "UserPromptSubmit", "prompt": "普通问题"})
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "unbound")

    def test_zcode_protocol_rename_and_manual_protection(self):
        self.issue()
        event = {"session_id": "sess_root", "cwd": str(self.root), "hook_event_name": "PostToolUse"}
        MODULE.set_binding("zcode", "sess_root", self.issue_binding())
        with mock.patch.object(MODULE, "zcode_rename") as rename:
            rename.side_effect = lambda e, title, last, unchanged=False: (title, "unchanged" if unchanged else "written")
            result = MODULE.refresh("zcode", event)
        self.assertEqual(result["status"], "written")
        state = MODULE.load_state("zcode")["sessions"]["zcode:sess_root"]
        self.assertEqual(state["last_title"], result["title"])

    def run_fake_host(self, host, current_title="", title_source=""):
        log = self.root / (host + "-requests.jsonl")
        env = {
            "FAKE_HOST": host,
            "FAKE_HOST_LOG": str(log),
            "FAKE_CURRENT_TITLE": current_title,
            "FAKE_TITLE_SOURCE": title_source,
        }
        command = [sys.executable, str(Path(__file__).with_name("fake_host_server.py"))]
        with mock.patch.dict(os.environ, {"SESSION_TITLE_{}_COMMAND".format(host.upper()): shlex.join(command), **env}):
            if host == "codex":
                return MODULE.codex_rename({"session_id": "thread-1", "cwd": str(self.root)}, "新标题", None), log
            return MODULE.zcode_rename({"session_id": "sess_root", "cwd": str(self.root)}, "新标题", None), log

    def test_codex_ndjson_protocol(self):
        (result, outcome), log = self.run_fake_host("codex")
        self.assertEqual((result, outcome), ("新标题", "written"))
        requests = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r.get("method") for r in requests], ["initialize", "initialized", "thread/read", "thread/name/set"])
        self.assertEqual(requests[-1]["params"], {"threadId": "thread-1", "name": "新标题"})

    def test_zcode_ndjson_protocol(self):
        (result, outcome), log = self.run_fake_host("zcode")
        self.assertEqual((result, outcome), ("新标题", "written"))
        requests = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r.get("method") for r in requests], ["session/list", "session/resume", "v4/command"])
        self.assertEqual(requests[-1]["params"]["type"], "renameSession")
        self.assertEqual(requests[-1]["params"]["payload"], {"title": "新标题"})

    def test_existing_custom_title_is_not_overwritten_by_protocol_adapters(self):
        (result, outcome), _ = self.run_fake_host("zcode", "我的标题", "custom")
        self.assertEqual((result, outcome), (None, "paused"))
        (result, outcome), _ = self.run_fake_host("codex", "我的标题")
        self.assertEqual((result, outcome), (None, "paused"))

    def test_codex_read_failure_is_conservative(self):
        command = [sys.executable, str(Path(__file__).with_name("fake_host_server.py"))]
        env = {
            "SESSION_TITLE_CODEX_COMMAND": shlex.join(command),
            "FAKE_HOST": "codex",
            "FAKE_HOST_LOG": str(self.root / "codex-read-fail.jsonl"),
            "FAKE_READ_FAIL": "1",
        }
        with mock.patch.dict(os.environ, env):
            result = MODULE.codex_rename({"session_id": "thread-1", "cwd": str(self.root)}, "新标题", None)
            self.assertEqual(result, ("新标题", "written"))
            result = MODULE.codex_rename({"session_id": "thread-1", "cwd": str(self.root)}, "更新标题", "新标题")
            self.assertEqual(result, (None, "skipped"))

    def test_protocol_adapters_require_available_app_server(self):
        with mock.patch.object(MODULE, "default_command", return_value=None):
            self.assertEqual(MODULE.codex_rename({"session_id": "t", "cwd": str(self.root)}, "新标题", None),
                             (None, "unavailable"))
            self.assertEqual(MODULE.zcode_rename({"session_id": "sess_root", "cwd": str(self.root)}, "新标题", None),
                             (None, "unavailable"))

    def test_adapter_reentry_is_skipped(self):
        with mock.patch.dict(os.environ, {"SESSION_TITLE_ADAPTER_ACTIVE": "1"}):
            result = MODULE.refresh("zcode", {"session_id": "nested", "cwd": str(self.root),
                                              "hook_event_name": "SessionStart"})
        self.assertEqual(result, {"status": "skipped", "reason": "adapter-reentry"})

    def test_invalid_or_missing_artifacts_fail_closed(self):
        binding = self.feature_binding()
        with self.assertRaises(MODULE.SessionTitleError):
            MODULE.compute_title(binding)
        self.issue()
        (self.dev / "issues/issue-003-callback.md").write_text("not frontmatter", encoding="utf-8")
        self.assertTrue(MODULE.compute_title(self.issue_binding()).startswith("[状态未知]"))

    def test_cli_title_is_read_only(self):
        self.brief()
        result = subprocess.run([sys.executable, str(SCRIPT), "title", "--repo", str(self.root),
                                 "--feature", "docs/features/交易链路/订单超时"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[需求澄清] 订单超时")


if __name__ == "__main__":
    unittest.main()
