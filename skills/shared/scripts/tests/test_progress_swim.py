#!/usr/bin/env python3
"""swim 数据层测试：开发中判定、依赖显式分类（结构化/正文/里程碑/缺失）与 agent 来源标注。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import progress

ISSUE = "---\nissue-id: {iid}\ntitle: {title}\nstatus: {status}\n{extra}---\n\n# {title}\n{body}\n"


def write_issue(repo, module, slug, iid, title, status, extra="", body=""):
    d = repo / "dev" / "features" / module / slug / "issues"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{iid}-{title}.md").write_text(
        ISSUE.format(iid=iid, title=title, status=status, extra=extra, body=body), encoding="utf-8")


def edge_map(payload):
    return {(e["from"], e["to"], e["kind"]) for e in payload["dependencies"]}


class SwimFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "dev").mkdir(parents=True)
        (self.repo / "dev" / "agents-config.md").write_text(
            "---\nmain-branch: main\n---\n", encoding="utf-8")
        # 需求甲：在制（issue-001 在跑）+ 已完结 issue-002 依赖 issue-001（status 注释带旧日期）
        write_issue(self.repo, "模块A", "需求甲", "issue-001", "基础", "in-progress",
                    extra="dispatch:\n  host: codex\n  model: gpt-x\n  attempts: 1\n")
        write_issue(self.repo, "模块A", "需求甲", "issue-002", "后续", "done           # 2025-01-01 旧标记",
                    extra="blocked-by: [issue-001]\n")
        # 需求乙：仅待派发（confirmed）——不算开发中；跨需求结构化依赖 + 里程碑 + 正文外部前置
        write_issue(self.repo, "模块B", "需求乙", "issue-001", "消费", "confirmed",
                    extra='external-blocked-by: ["模块A/需求甲#issue-002", "M0-merged-baseline"]\n',
                    body="\n外部前置：模块A 需求甲 issue-001 须先合入 main。\n")
        # 需求丙：全部完结，带悬空跨需求引用
        write_issue(self.repo, "模块C", "需求丙", "issue-009", "悬空", "done",
                    extra='external-blocked-by: ["模块A/需求甲#issue-999"]\n')
        # 需求丁：唯一票被关闭（deferred）→ 需求态应为 closed
        write_issue(self.repo, "模块D", "需求丁", "issue-001", "搁置", "deferred")

    def tearDown(self):
        self.tmp.cleanup()

    def swim(self, sessions=None):
        return progress.build_swim_payload(self.repo, sessions=sessions or [])

    def test_developing_only_by_issue_status(self):
        p = self.swim()
        active = [f["id"] for f in p["features"] if f["active"]]
        self.assertEqual(active, ["模块A/需求甲"])
        self.assertEqual(p["stats"]["developing_features"], 1)
        self.assertEqual(p["stats"]["active_issues"], 1)

    def test_dependency_kinds_explicit(self):
        p = self.swim()
        edges = edge_map(p)
        from_a1 = "模块A/需求甲#issue-001"
        from_b1 = "模块B/需求乙#issue-001"
        # 约定与原型一致：from=依赖声明方（后续），to=被依赖的前置；绘制时箭头 前置 → 后续
        self.assertIn(("模块A/需求甲#issue-002", from_a1, "internal"), edges)
        # 结构化跨需求：需求乙#issue-001 声明依赖 需求甲#issue-002
        self.assertIn((from_b1, "模块A/需求甲#issue-002", "external"), edges)
        # 正文外部前置（带证据）指向 issue-001
        body = [e for e in p["dependencies"] if e["kind"] == "body"]
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["from"], from_b1)
        self.assertEqual(body[0]["to"], from_a1)
        self.assertIn("issue-001-消费.md:", body[0]["evidence"])
        self.assertIn("外部前置", body[0]["evidence"])
        # 里程碑单独成类，不伪装成 issue 节点
        ms = [e for e in p["dependencies"] if e["kind"] == "milestone"]
        self.assertEqual(len(ms), 1)
        self.assertEqual(ms[0]["ref"], "M0-merged-baseline")
        self.assertIsNone(ms[0]["to"])
        # 悬空引用保留 resolved=false，目标 issue 号来自原文
        missing = [e for e in p["dependencies"] if e["kind"] == "external" and not e["resolved"]]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["ref"], "模块A/需求甲#issue-999")
        # 可解析跨需求 = 结构化 issue-002 + 正文 issue-001；待核对 = 悬空 issue-999
        self.assertEqual(p["stats"]["resolved_cross"], 2)
        self.assertEqual(p["stats"]["unresolved_cross"], 1)
        self.assertEqual(p["stats"]["milestone_refs"], 1)

    def test_agent_sources_kept_apart(self):
        p = self.swim(sessions=[{"host": "zcode", "paused": False,
                                 "feature": "模块A/需求甲", "issue": "issue-001", "title": "x"}])
        node = next(i for f in p["features"] for i in f["issues"] if i["id"] == "issue-001"
                    and i["feature"] == "模块A/需求甲")
        sources = {(a["host"], a["source"]) for a in node["agents"]}
        self.assertIn(("codex", "dispatch"), sources)
        self.assertIn(("zcode", "session"), sources)
        feature = next(f for f in p["features"] if f["id"] == "模块A/需求甲")
        self.assertIn(("zcode", "session"), {(a["host"], a["source"]) for a in feature["agents"]})

    def test_branch_prefix_is_weak_hint(self):
        import subprocess
        for cmd in (["git", "init", "-q", "-b", "main"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"],
                    ["git", "commit", "--allow-empty", "-m", "init"],
                    ["git", "branch", "codex/需求甲-issue-001"]):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)
        p = self.swim()
        node = next(i for f in p["features"] for i in f["issues"]
                    if i["id"] == "issue-001" and i["feature"] == "模块A/需求甲")
        branch_agents = [a for a in node["agents"] if a["source"] == "branch"]
        self.assertEqual(len(branch_agents), 1)
        self.assertEqual(branch_agents[0]["host"], "codex")
        self.assertEqual(node["branch"], "codex/需求甲-issue-001")

    def test_feature_state_alignment(self):
        """需求态三分类与「开发中」判定同源：active→None，仅可派发→ready，全完结→done，有退役票→closed"""
        p = self.swim()
        states = {f["id"]: f["fstate"] for f in p["features"]}
        self.assertIsNone(states["模块A/需求甲"])
        self.assertEqual(states["模块B/需求乙"], "ready")
        self.assertEqual(states["模块C/需求丙"], "done")
        self.assertEqual(states["模块D/需求丁"], "closed")
        active = {f["id"] for f in p["features"] if f["active"]}
        self.assertEqual(active, {"模块A/需求甲"})

    def test_done_ts_sources(self):
        """完结时间：merged 事件优先于 status 注释日期；只有注释时用注释；两者皆无为 None"""
        from datetime import datetime
        feat = self.repo / "dev" / "features" / "模块A" / "需求甲"
        (feat / "progress.jsonl").write_text(
            '{"ts": "2026-01-02 10:00", "event": "merged", "issue": "issue-002"}\n', encoding="utf-8")
        write_issue(self.repo, "模块E", "需求戊", "issue-001", "注释完结", "done           # 2026-01-01 合并收口")
        p = self.swim()
        by_uid = {i["uid"]: i for f in p["features"] for i in f["issues"]}
        self.assertEqual(by_uid["模块A/需求甲#issue-002"]["done_ts"],
                         datetime(2026, 1, 2, 10, 0).timestamp())
        self.assertEqual(by_uid["模块E/需求戊#issue-001"]["done_ts"],
                         datetime(2026, 1, 1).timestamp())
        self.assertIsNone(by_uid["模块C/需求丙#issue-009"]["done_ts"])

    def test_summary_facts_include_blockers(self):
        """泳道票节点带卡点素材（等人备注/打回次数），事实清单如实收录"""
        feat = self.repo / "dev" / "features" / "模块A" / "需求甲"
        write_issue(self.repo, "模块A", "需求甲", "issue-003", "卡点票", "needs-human",
                    extra="verify-fails: 2\n")
        (feat / "progress.jsonl").write_text(
            '{"ts": "2026-01-02 10:00", "event": "needs-human", "issue": "issue-003", "note": "资损口径需要人拍板"}\n',
            encoding="utf-8")
        p = self.swim()
        node = next(i for f in p["features"] if f["id"] == "模块A/需求甲"
                    for i in f["issues"] if i["id"] == "issue-003")
        self.assertEqual(node["verify_fails"], 2)
        self.assertIn("资损口径", node["human_note"])
        facts = progress._summary_facts(p)
        self.assertIn("卡点票", facts)
        self.assertIn("资损口径需要人拍板", facts)
        self.assertIn("复跑打回 2 次", facts)
        self.assertIn("等人", facts)

    def test_call_llm_against_mock_server(self):
        """_call_llm 走 OpenAI 兼容 /chat/completions 并取回 content"""
        import http.server
        import threading
        seen = {}

        class Mock(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen["auth"] = self.headers.get("authorization")
                seen["path"] = self.path
                body = json.dumps({"choices": [{"message": {"content": "摘要文本"}, "finish_reason": "stop"}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out, finish = progress._call_llm(f"http://127.0.0.1:{srv.server_port}", "k", "m", "p")
            self.assertEqual(out, "摘要文本")
            self.assertEqual(finish, "stop")
            self.assertEqual(seen["path"], "/chat/completions")
            self.assertEqual(seen["auth"], "Bearer k")
        finally:
            srv.shutdown()

    def test_summary_config_precedence(self):
        """配置优先级：参数 > 环境变量 > 宿主 host.json /model（密钥回退同目录 local.env）"""
        home = Path(self.tmp.name) / "home"
        cfgdir = home / ".config" / "pandu"
        cfgdir.mkdir(parents=True)
        (cfgdir / "host.json").write_text(json.dumps(
            {"model": {"base_url": "http://host", "model": "host-model",
                       "api_key_ref_env": "PANDU_DEEPSEEK_API_KEY"}}), encoding="utf-8")
        (cfgdir / "local.env").write_text(
            "PANDU_DEEPSEEK_API_KEY=sk-file\nOTHER=1\n# comment\nBADLINE\n", encoding="utf-8")
        args = SimpleNamespace(base_url=None, model=None, api_key=None)
        self.assertEqual(progress._summary_config(args, home=home),
                         ("http://host", "host-model", "sk-file"))
        # 进程环境已有引用变量时优先于 local.env
        saved = os.environ.get("PANDU_DEEPSEEK_API_KEY")
        os.environ["PANDU_DEEPSEEK_API_KEY"] = "sk-env"
        try:
            self.assertEqual(progress._summary_config(args, home=home)[2], "sk-env")
        finally:
            if saved is None:
                os.environ.pop("PANDU_DEEPSEEK_API_KEY", None)
            else:
                os.environ["PANDU_DEEPSEEK_API_KEY"] = saved
        # 参数最高
        self.assertEqual(progress._summary_config(
            SimpleNamespace(base_url="http://arg", model="m2", api_key="k2"), home=home),
            ("http://arg", "m2", "k2"))

    def test_works_without_git(self):
        with tempfile.TemporaryDirectory() as bare:
            repo = Path(bare) / "r"
            (repo / "dev").mkdir(parents=True)
            write_issue(repo, "模块A", "需求甲", "issue-001", "基础", "in-progress")
            p = progress.build_swim_payload(repo, sessions=[])
            self.assertEqual(p["stats"]["developing_features"], 1)
            self.assertEqual(p["stats"]["issues"], 1)


if __name__ == "__main__":
    unittest.main()
