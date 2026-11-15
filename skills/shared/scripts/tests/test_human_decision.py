#!/usr/bin/env python3
"""Focused human-decision v1 contract tests using the real shared modules."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import human_decision as hd
import json_schema
import state_store

FEATURE = "mod/slug"
ISSUE = "issue-001"
T0 = "2026-11-14T10:00:00Z"
T1 = "2026-11-14T10:01:00Z"
T2 = "2026-11-14T10:02:00Z"
T3 = "2026-11-14T10:03:00Z"
T4 = "2026-11-14T10:04:00Z"
T5 = "2026-11-14T10:05:00Z"


class HumanDecisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.feature_dir = self.repo / "dev" / "features" / "mod" / "slug"
        self.issues = self.feature_dir / "issues"
        self.issues.mkdir(parents=True)
        self.issue_path = self.issues / "issue-001-orders.md"
        self.issue_path.write_text("""---
feature-slug: slug
issue-id: issue-001
title: 订单重试
status: in-progress
blocked-by: []
dispatch:
  host: zcode
  model: test-model
  dispatched-at: '2026-11-14 09:00'
  attempts: 3
  dispatch_id: dispatch-old
  session_id: session-old
  parent_session_id: main-old
---
# 订单重试
""", encoding="utf-8")
        report = self.feature_dir / "reviews" / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text("评审摘要：需要选择重试策略。\n", encoding="utf-8")
        self.evidence_rel = report.relative_to(self.repo).as_posix()
        self.evidence_sha = hashlib.sha256(report.read_bytes()).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def envelope(self, decision_id="dec-one"):
        return {
            "schema": hd.SCHEMA,
            "id": decision_id,
            "scope": {"repo": ".", "feature": FEATURE, "issue": ISSUE},
            "source": {
                "host": "zcode", "skill": "implement", "role": "development",
                "dispatch_id": "dispatch-old", "session_id": "session-old",
                "parent_session_id": "main-old",
            },
            "basis": self.basis(),
            "question": {
                "context": "订单回调可能重复到达，现有重试会再次关闭订单。",
                "decision": "本次要增加防重复处理，还是保持现有行为？",
                "why": "两种处理会影响资金核对和后续返工。",
                "recommendation": "retry",
                "options": [
                    {
                        "id": "retry", "label": "增加防重复处理",
                        "description": "在原会话补上防重复处理，再核对订单关闭行为。",
                        "route": {
                            "action": "continue", "strategy": "resume",
                            "role": "development", "tier": "default",
                            "next_status": "in-progress",
                            "prompt": "继续在原会话增加防重复处理并复验。",
                            "operation": None,
                        },
                    },
                    {
                        "id": "fresh", "label": "换独立会话重新修复",
                        "description": "用新的独立会话重新检查实现，代价是多一次完整交接。",
                        "route": {
                            "action": "continue", "strategy": "redispatch",
                            "role": "development", "tier": "escalated",
                            "next_status": "in-progress",
                            "prompt": "根据决策包重新检查并实现防重复处理。",
                            "operation": None,
                        },
                    },
                    {
                        "id": "defer", "label": "暂缓这张票",
                        "description": "保留当前实现和依赖关系，等条件成熟后再继续。",
                        "route": {
                            "action": "defer", "strategy": "none", "role": None, "tier": None,
                            "next_status": "deferred", "prompt": None, "operation": None,
                        },
                    },
                    {
                        "id": "close", "label": "取消这张票",
                        "description": "结束本次开发，但不把相关需求视为已经完成。",
                        "route": {
                            "action": "cancel", "strategy": "none", "role": None, "tier": None,
                            "next_status": "cancelled", "prompt": None, "operation": None,
                        },
                    },
                ],
            },
            "evidence": [{"path": self.evidence_rel, "sha256": self.evidence_sha,
                          "summary": "评审指出重复回调的处理策略需要人选择。"}],
            "presentation": {"header": "订单重试"},
            "expires_at": "2026-11-14T11:00:00Z",
        }

    def basis(self):
        return {"issue_sha256": "a" * 64, "head_commit": "b" * 40,
                "report_sha256": "c" * 64}

    def host_ack(self, packet, receipt="host-receipt"):
        return {"host": packet["source"]["host"],
                "route_id": packet["route"]["route_id"], "receipt": receipt}

    def create(self, decision_id="dec-one", unattended=False):
        return hd.request(self.repo, self.envelope(decision_id), main_session=True,
                          unattended=unattended, now=T0)

    def waiting(self, decision_id="dec-one"):
        self.create(decision_id)
        return hd.present(self.repo, FEATURE, decision_id, main_session=True, now=T1)

    def answered(self, option="retry", decision_id="dec-one"):
        self.waiting(decision_id)
        return hd.answer(self.repo, FEATURE, decision_id, option_id=option,
                         text="选择" + option + "。", current_basis=self.basis(),
                         main_session=True, now=T2)

    def routing(self, option="retry", decision_id="dec-one"):
        self.answered(option, decision_id)
        return hd.route_begin(self.repo, FEATURE, decision_id, current_basis=self.basis(),
                              main_session=True, now=T3)

    def events(self):
        path = self.feature_dir / "progress.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def issue_text(self):
        return self.issue_path.read_text(encoding="utf-8")

    def set_issue_raw(self, status):
        self.issue_path.write_text(re.sub(r"^status:[^\n]*", "status: " + status,
                                          self.issue_text(), count=1, flags=re.M), encoding="utf-8")

    def add_dependent(self):
        path = self.issues / "issue-002-dependent.md"
        path.write_text("""---
feature-slug: slug
issue-id: issue-002
title: 下游票
status: confirmed
blocked-by: [issue-001]
---
""", encoding="utf-8")
        return path

    def resolved_then_cancelled(self, resolved_at=T4, cancelled_at=T5):
        old = self.routing()
        old = hd.route_ack(self.repo, FEATURE, "dec-one", route_id=old["route"]["route_id"],
                           ack=self.host_ack(old), main_session=True, now=resolved_at)
        replacement = self.envelope("dec-cancelled")
        hd.request(self.repo, replacement, main_session=True, now=T5)
        cancelled = hd.cancel(self.repo, FEATURE, "dec-cancelled", reason="人明确取消后续处理",
                              main_session=True, now=cancelled_at)
        self.set_issue_raw("needs-human")
        return old, cancelled

    def test_schema_is_supported_and_validate_request_has_no_side_effects(self):
        packet = hd.validate_request(self.envelope(), repo=self.repo, now=T0)
        self.assertEqual([], json_schema.load_validate(hd.SCHEMA_PATH, packet))
        self.assertEqual("pending", packet["status"])
        self.assertFalse((self.feature_dir / "decisions" / "dec-one.json").exists())
        with self.assertRaises(hd.MainSessionRequired):
            hd.request(self.repo, self.envelope())
        self.assertFalse((self.feature_dir / "decisions" / "dec-one.json").exists())

    def test_path_traversal_absolute_paths_and_symlink_escape_are_rejected(self):
        mutations = []
        bad = self.envelope("dec/../../escape")
        mutations.append(bad)
        bad = self.envelope()
        bad["scope"]["feature"] = "../slug"
        mutations.append(bad)
        bad = self.envelope()
        bad["scope"]["repo"] = "/tmp/other"
        mutations.append(bad)
        bad = self.envelope()
        bad["evidence"][0]["path"] = "../../etc/passwd"
        mutations.append(bad)
        bad = self.envelope()
        bad["evidence"][0]["path"] = "/etc/passwd"
        mutations.append(bad)
        for candidate in mutations:
            with self.subTest(candidate=candidate["id"], scope=candidate["scope"]):
                with self.assertRaises(hd.DecisionError):
                    hd.validate_request(candidate, repo=self.repo, now=T0)
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        linked = self.feature_dir / "reviews" / "linked.md"
        os.symlink(str(outside), str(linked))
        bad = self.envelope()
        bad["evidence"][0] = {"path": linked.relative_to(self.repo).as_posix(),
                              "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                              "summary": "越界证据"}
        with self.assertRaises(hd.ValidationError):
            hd.validate_request(bad, repo=self.repo, now=T0)

    def test_secret_large_log_and_aggregate_limits_fail_closed(self):
        bad = self.envelope()
        bad["question"]["context"] = "很长" * 1001
        with self.assertRaises(hd.ValidationError):
            hd.validate_request(bad, repo=self.repo, now=T0)
        bad = self.envelope()
        bad["question"]["options"][0]["description"] = "api_key=do-not-store-this"
        with self.assertRaisesRegex(hd.ValidationError, "secret"):
            hd.validate_request(bad, repo=self.repo, now=T0)
        bad = self.envelope()
        bad["evidence"][0]["raw_log"] = "x" * 100000
        with self.assertRaises(hd.DecisionError):
            hd.validate_request(bad, repo=self.repo, now=T0)

    def test_request_is_idempotent_and_one_active_packet_per_issue(self):
        first = self.create()
        count = len(self.events())
        second = hd.request(self.repo, self.envelope(), main_session=True, now=T5)
        self.assertEqual(first, second)
        self.assertEqual(count, len(self.events()))
        self.assertIn("status: needs-human", self.issue_text())
        with self.assertRaises(hd.ActiveDecisionExists):
            self.create("dec-other")
        changed = self.envelope()
        changed["basis"]["head_commit"] = "d" * 40
        with self.assertRaises(hd.BasisChanged):
            hd.request(self.repo, changed, main_session=True, now=T5)

    def test_full_state_migration_and_resume_attempt_count(self):
        packet = self.answered()
        self.assertEqual("answered", packet["status"])
        count = len(self.events())
        replay = hd.answer(self.repo, FEATURE, "dec-one", option_id="retry",
                           text="选择retry。", current_basis=self.basis(), main_session=True, now=T5)
        self.assertEqual(packet, replay)
        self.assertEqual(count, len(self.events()))
        routing = hd.route_begin(self.repo, FEATURE, "dec-one", current_basis=self.basis(),
                                 main_session=True, now=T3)
        self.assertEqual("routing", routing["status"])
        plan = routing["route"]["plan"]
        self.assertEqual("resume", plan["kind"])
        self.assertEqual(3, plan["attempts_before"])
        self.assertEqual(3, plan["attempts_after"])
        self.assertEqual("session-old", plan["session_id"])
        route_id = routing["route"]["route_id"]
        again = hd.route_begin(self.repo, FEATURE, "dec-one", current_basis=self.basis(),
                               main_session=True, now=T5)
        self.assertEqual(route_id, again["route"]["route_id"])
        self.assertIn("status: needs-human", self.issue_text())
        resolved = hd.route_ack(self.repo, FEATURE, "dec-one", route_id=route_id,
                                ack=self.host_ack(routing), main_session=True, now=T4)
        self.assertEqual("resolved", resolved["status"])
        self.assertEqual("host-receipt", resolved["route"]["ack"]["receipt"])
        self.assertIn("status: in-progress", self.issue_text())
        count = len(self.events())
        hd.route_ack(self.repo, FEATURE, "dec-one", route_id=route_id,
                     ack=self.host_ack(resolved), main_session=True, now=T5)
        self.assertEqual(count, len(self.events()))
        states = [event["state"] for event in self.events() if event["event"] == "human-decision"]
        self.assertEqual(["pending", "waiting", "answered", "routing", "resolved"], states)
        self.assertTrue(all("decision_id" in event and "route_id" in event
                            for event in self.events() if event["event"] == "human-decision"))

    def test_route_ack_requires_verifiable_matching_host_and_route_identity(self):
        packet = self.routing()
        route_id = packet["route"]["route_id"]
        invalid = [
            None,
            {},
            {"host": "zcode", "route_id": route_id},
            {"host": "other-host", "route_id": route_id, "receipt": "host-receipt"},
            {"host": "zcode", "route_id": "route-" + "0" * 32, "receipt": "host-receipt"},
            {"host": "zcode", "route_id": route_id, "receipt": "host-receipt",
             "dispatch_id": "dispatch-wrong"},
            {"host": "zcode", "route_id": route_id, "receipt": "host-receipt",
             "session_id": "session-wrong"},
            {"host": "zcode", "route_id": route_id, "receipt": "   "},
        ]
        for ack in invalid:
            with self.subTest(ack=ack), self.assertRaises(hd.DecisionError):
                hd.route_ack(self.repo, FEATURE, "dec-one", route_id=route_id,
                             ack=ack, main_session=True, now=T4)
            self.assertEqual("routing", hd.show(self.repo, FEATURE, "dec-one")["status"])
            self.assertIn("status: needs-human", self.issue_text())
        identity = {"host": "zcode", "route_id": route_id, "session_id": "session-old"}
        resolved = hd.route_ack(self.repo, FEATURE, "dec-one", route_id=route_id,
                                ack=identity, main_session=True, now=T4)
        self.assertEqual("resolved", resolved["status"])
        self.assertNotIn("receipt", resolved["route"]["ack"])
        self.assertIn("status: in-progress", self.issue_text())
        with self.assertRaises(hd.ValidationError):
            hd.route_ack(self.repo, FEATURE, "dec-one", route_id=route_id,
                         ack=None, main_session=True, now=T5)
        broken = copy.deepcopy(resolved)
        del broken["route"]["ack"]["host"]
        self.assertTrue(json_schema.load_validate(hd.SCHEMA_PATH, broken))
        broken = copy.deepcopy(resolved)
        del broken["route"]["ack"]["session_id"]
        self.assertTrue(json_schema.load_validate(hd.SCHEMA_PATH, broken),
                        "schema must require receipt or dispatch/session identity")

    def test_redispatch_increments_attempts_once_and_changes_dispatch_identity(self):
        packet = self.routing("fresh")
        plan = packet["route"]["plan"]
        self.assertEqual("redispatch", plan["kind"])
        self.assertEqual(3, plan["attempts_before"])
        self.assertEqual(4, plan["attempts_after"])
        self.assertNotEqual("dispatch-old", plan["dispatch_id"])
        self.assertIsNone(plan["session_id"])
        # The host integration writes dispatch/model/session fields when it
        # actually dispatches; route_begin itself never runs a child.
        self.assertIn("attempts: 3", self.issue_text())
        route_id = packet["route"]["route_id"]
        replay = hd.route_begin(self.repo, FEATURE, "dec-one", current_basis=self.basis(),
                                main_session=True, now=T5)
        self.assertEqual(route_id, replay["route"]["route_id"])
        self.assertEqual(4, replay["route"]["plan"]["attempts_after"])
        resolved = hd.route_ack(self.repo, FEATURE, "dec-one", route_id=route_id,
                                ack={"host": "zcode", "route_id": route_id,
                                     "dispatch_id": plan["dispatch_id"], "session_id": "session-new"},
                                main_session=True, now=T4)
        self.assertEqual("session-new", resolved["route"]["ack"]["session_id"])

    def test_route_failure_requires_known_outcome_and_returns_to_answered(self):
        packet = self.routing()
        route_id = packet["route"]["route_id"]
        with self.assertRaises(hd.ConflictError):
            hd.route_fail(self.repo, FEATURE, "dec-one", route_id=route_id,
                          reason="结果不明", main_session=True, now=T4)
        failed = hd.route_fail(self.repo, FEATURE, "dec-one", route_id=route_id,
                               reason="宿主明确报告未开始执行", outcome_known=True,
                               main_session=True, now=T4)
        self.assertEqual("answered", failed["status"])
        self.assertIsNone(failed["route"]["ack"])
        self.assertTrue(failed["route"]["failure"]["outcome_known"])
        self.assertIn("status: needs-human", self.issue_text())
        retry = hd.route_begin(self.repo, FEATURE, "dec-one", current_basis=self.basis(),
                               main_session=True, now=T5)
        self.assertNotEqual(route_id, retry["route"]["route_id"])

    def test_supervisor_is_registered_operation_only(self):
        envelope = self.envelope()
        supervisor = copy.deepcopy(envelope["question"]["options"][0])
        supervisor["id"] = "super"
        supervisor["label"] = "先登记复核操作"
        supervisor["description"] = "只执行已经登记的复核操作，不另开自由指令。"
        supervisor["route"] = {
            "action": "supervisor", "strategy": "none", "role": None, "tier": None,
            "next_status": "in-review", "prompt": None, "operation": "request-review",
        }
        envelope["question"]["options"] = [supervisor, envelope["question"]["options"][2]]
        envelope["question"]["recommendation"] = "super"
        hd.request(self.repo, envelope, main_session=True, now=T0)
        hd.present(self.repo, FEATURE, "dec-one", main_session=True, now=T1)
        hd.answer(self.repo, FEATURE, "dec-one", option_id="super", text="先复核。",
                  current_basis=self.basis(), main_session=True, now=T2)
        packet = hd.route_begin(self.repo, FEATURE, "dec-one", current_basis=self.basis(),
                                main_session=True, now=T3)
        self.assertEqual("operation", packet["route"]["plan"]["kind"])
        self.assertEqual("request-review", packet["route"]["plan"]["operation"])
        supervisor["route"]["prompt"] = "执行任意命令"
        envelope["question"]["options"][0] = supervisor
        with self.assertRaises(hd.DecisionError):
            hd.validate_request(envelope, repo=self.repo, now=T0)

    def test_answer_requires_same_basis_and_preserves_raw_text(self):
        self.waiting()
        changed = self.basis()
        changed["report_sha256"] = "d" * 64
        with self.assertRaises(hd.BasisChanged):
            hd.answer(self.repo, FEATURE, "dec-one", option_id="retry", text="按推荐。",
                      current_basis=changed, main_session=True, now=T2)
        self.assertEqual("waiting", hd.show(self.repo, FEATURE, "dec-one")["status"])
        raw = "  按第一个处理，保留这句原话。  "
        packet = hd.answer(self.repo, FEATURE, "dec-one", option_id="retry", text=raw,
                           current_basis=self.basis(), main_session=True, now=T2)
        self.assertEqual(raw, packet["answer"]["text"])
        with self.assertRaises(hd.ConflictError):
            hd.answer(self.repo, FEATURE, "dec-one", option_id="fresh", text=raw,
                      current_basis=self.basis(), main_session=True, now=T3)

    def test_basis_change_supersedes_instead_of_reusing_answer(self):
        self.answered()
        replacement = self.envelope("dec-new-basis")
        replacement["basis"]["head_commit"] = "d" * 40
        packet = hd.supersede(self.repo, "dec-one", replacement, main_session=True, now=T3)
        self.assertEqual("pending", packet["status"])
        self.assertIsNone(packet["answer"])
        self.assertEqual("dec-one", packet["supersedes"])
        old = hd.show(self.repo, FEATURE, "dec-one")
        self.assertEqual("superseded", old["status"])
        self.assertEqual("dec-new-basis", old["superseded_by"])
        self.assertEqual(packet, hd.active_for_issue(self.repo, FEATURE, ISSUE))
        with self.assertRaises(hd.StateTransitionError):
            hd.route_begin(self.repo, FEATURE, "dec-one", current_basis=self.basis(),
                           main_session=True, now=T4)

    def test_cancel_timeout_and_explicit_expiry_have_distinct_semantics(self):
        self.waiting()
        packet = hd.cancel_presentation(self.repo, FEATURE, "dec-one", main_session=True, now=T2)
        self.assertEqual("pending", packet["status"])
        self.assertIsNone(packet["answer"])
        self.assertEqual("cancel", packet["presentation"]["interruption"]["kind"])
        hd.present(self.repo, FEATURE, "dec-one", main_session=True, now=T3)
        packet = hd.timeout_presentation(self.repo, FEATURE, "dec-one", main_session=True, now=T4)
        self.assertEqual("pending", packet["status"])
        self.assertIsNone(packet["answer"])
        with self.assertRaises(hd.StateTransitionError):
            hd.expire(self.repo, FEATURE, "dec-one", main_session=True, now=T5)
        packet = hd.expire(self.repo, FEATURE, "dec-one", main_session=True,
                           now="2026-11-14T12:00:00Z")
        self.assertEqual("expired", packet["status"])
        self.assertEqual(packet, hd.active_for_issue(self.repo, FEATURE, ISSUE))
        self.assertIn("status: needs-human", self.issue_text())
        with self.assertRaises(hd.StateTransitionError):
            hd.answer(self.repo, FEATURE, "dec-one", option_id="retry", text="迟到回答",
                      current_basis=self.basis(), main_session=True, now=T5)

    def test_direct_cancel_does_not_unlock_dependents(self):
        dependent = self.add_dependent()
        before = dependent.read_text(encoding="utf-8")
        self.create()
        packet = hd.cancel(self.repo, FEATURE, "dec-one", reason="人明确要求取消本票",
                           main_session=True, now=T1)
        self.assertEqual("cancelled", packet["status"])
        self.assertIn("status: cancelled", self.issue_text())
        self.assertIsNone(hd.active_for_issue(self.repo, FEATURE, ISSUE))
        self.assertEqual(before, dependent.read_text(encoding="utf-8"))
        self.assertNotIn("status: done", before)

    def test_defer_route_does_not_unlock_dependents(self):
        dependent = self.add_dependent()
        before = dependent.read_text(encoding="utf-8")
        packet = self.routing("defer")
        hd.route_ack(self.repo, FEATURE, "dec-one", route_id=packet["route"]["route_id"],
                     ack=self.host_ack(packet), main_session=True, now=T4)
        self.assertIn("status: deferred", self.issue_text())
        self.assertEqual(before, dependent.read_text(encoding="utf-8"))
        self.assertNotIn("status: done", before)

    def test_arbitrary_shell_direct_done_and_unknown_operation_are_rejected(self):
        for field, value in (("shell", "rm -rf /"), ("command", "whoami")):
            envelope = self.envelope()
            envelope["question"]["options"][0]["route"][field] = value
            with self.subTest(field=field), self.assertRaises(hd.DecisionError):
                hd.validate_request(envelope, repo=self.repo, now=T0)
        envelope = self.envelope()
        envelope["question"]["options"][0]["route"]["next_status"] = "done"
        with self.assertRaises(hd.DecisionError):
            hd.validate_request(envelope, repo=self.repo, now=T0)
        envelope = self.envelope()
        envelope["question"]["options"][0]["route"]["operation"] = "arbitrary-operation"
        with self.assertRaises(hd.DecisionError):
            hd.validate_request(envelope, repo=self.repo, now=T0)

    def test_component_projection_is_one_human_question_and_text_fallback_numbers(self):
        packet = hd.validate_request(self.envelope(), repo=self.repo, now=T0)
        # Put the recommended option elsewhere to prove deterministic reordering.
        options = packet["question"]["options"]
        packet["question"]["options"] = [options[1], options[2], options[0], options[3]]
        component = hd.to_question_component(packet)
        self.assertEqual({"questions"}, set(component))
        self.assertEqual(1, len(component["questions"]))
        question = component["questions"][0]
        self.assertEqual({"header", "question", "multiSelect", "options"}, set(question))
        self.assertEqual("订单重试", question["header"])
        self.assertLessEqual(len(question["header"]), 12)
        self.assertFalse(question["multiSelect"])
        self.assertEqual(4, len(question["options"]))
        self.assertTrue(question["options"][0]["label"].endswith("（推荐）"))
        self.assertTrue(all(set(option) == {"label", "description"} for option in question["options"]))
        self.assertGreaterEqual(hd._sentence_count(question["question"]), 2)
        self.assertLessEqual(hd._sentence_count(question["question"]), 4)
        visible = json.dumps(component, ensure_ascii=False)
        for forbidden in ("dec-one", "retry", "needs-human", "route_id", "dispatch-old", "回 A", "方案 A"):
            self.assertNotIn(forbidden, visible)
        fallback = hd.to_text_fallback(packet)
        self.assertIn("1. ", fallback)
        self.assertIn("2. ", fallback)
        self.assertIn("请回复选项编号", fallback)
        self.assertNotIn("route_id", fallback)

    def test_unattended_records_only_and_never_auto_answers(self):
        packet = self.create(unattended=True)
        self.assertEqual("pending", packet["status"])
        packet = hd.present(self.repo, FEATURE, "dec-one", main_session=True,
                            unattended=True, now=T1)
        self.assertEqual("pending", packet["status"])
        self.assertIsNone(packet["answer"])
        self.assertIsNone(packet["route"])
        self.assertIn("status: needs-human", self.issue_text())
        self.assertEqual(["pending"], [event["state"] for event in self.events()
                                      if event["event"] == "human-decision"])

    def test_reconcile_repairs_packet_ticket_and_event_split_writes(self):
        self.create()
        self.set_issue_raw("in-progress")
        (self.feature_dir / "progress.jsonl").unlink()
        report = hd.reconcile(self.repo, FEATURE, main_session=True)
        self.assertTrue(report["ok"], report)
        self.assertIn("status: needs-human", self.issue_text())
        states = [event["state"] for event in self.events() if event["event"] == "human-decision"]
        self.assertEqual(["pending"], states)
        self.assertTrue(any(event["event"] == "needs-human" for event in self.events()))

    def test_reconcile_uses_persisted_ack_but_never_fabricates_one(self):
        packet = self.routing()
        route_id = packet["route"]["route_id"]
        hd.route_ack(self.repo, FEATURE, "dec-one", route_id=route_id,
                     ack=self.host_ack(packet), main_session=True, now=T4)
        self.set_issue_raw("needs-human")
        journal = self.feature_dir / "progress.jsonl"
        kept = []
        for line in journal.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if not (event["event"] == "human-decision" and event.get("state") == "resolved"):
                kept.append(line)
        journal.write_text("\n".join(kept) + "\n", encoding="utf-8")
        report = hd.reconcile(self.repo, FEATURE, main_session=True)
        self.assertTrue(report["ok"], report)
        self.assertIn("status: in-progress", self.issue_text())
        self.assertEqual(1, len([event for event in self.events()
                                 if event["event"] == "human-decision" and event.get("state") == "resolved"]))

    def test_reconcile_latest_terminal_uses_effective_time_not_type_priority(self):
        old, cancelled = self.resolved_then_cancelled()
        self.assertLess(old["timestamps"]["resolved_at"], cancelled["timestamps"]["cancelled_at"])

        def snapshot():
            return {path.relative_to(self.feature_dir).as_posix(): path.read_bytes()
                    for path in self.feature_dir.rglob("*") if path.is_file()}

        before = snapshot()
        classification = hd.classify(self.repo, FEATURE)
        self.assertTrue(classification["ok"], classification)
        entry = next(item for item in classification["issues"] if item["issue"] == ISSUE)
        self.assertEqual("apply-cancelled", entry["category"])
        self.assertEqual("apply-terminal", entry["action"])
        self.assertEqual("dec-cancelled", entry["decision_id"])
        self.assertEqual("cancelled", entry["target_status"])
        self.assertEqual(before, snapshot(), "read-only classification must not create locks or rewrite state")
        report = hd.reconcile(self.repo, FEATURE, main_session=True)
        self.assertTrue(report["ok"], report)
        self.assertIn("status: cancelled", self.issue_text())
        self.assertNotIn("status: in-progress", self.issue_text())

    def test_reconcile_terminal_time_tie_is_a_conflict_without_ticket_change(self):
        self.resolved_then_cancelled(resolved_at=T5, cancelled_at=T5)
        classification = hd.classify(self.repo, FEATURE)
        self.assertFalse(classification["ok"])
        entry = next(item for item in classification["issues"] if item["issue"] == ISSUE)
        self.assertEqual("terminal-conflict", entry["category"])
        report = hd.reconcile(self.repo, FEATURE, main_session=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any("tie" in item["error"] for item in report["conflicts"]))
        self.assertIn("status: needs-human", self.issue_text())

    def test_reconcile_corrupt_terminal_time_does_not_fall_back_to_type_priority(self):
        old, _ = self.resolved_then_cancelled()
        old_path = hd.packet_path(self.repo, FEATURE, old["id"])
        old["timestamps"]["resolved_at"] = "not-a-time"
        state_store.atomic_json_write(old_path, old)
        classification = hd.classify(self.repo, FEATURE)
        self.assertFalse(classification["ok"])
        self.assertTrue(classification["errors"])
        report = hd.reconcile(self.repo, FEATURE, main_session=True)
        self.assertFalse(report["ok"])
        self.assertTrue(report["errors"])
        self.assertIn("status: needs-human", self.issue_text())

    def test_reconcile_unknown_routing_does_not_redispatch(self):
        packet = self.routing()
        route_id = packet["route"]["route_id"]
        report = hd.reconcile(self.repo, FEATURE, main_session=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(route_id, report["unknown_routes"][0]["route_id"])
        self.assertEqual("routing", hd.show(self.repo, FEATURE, "dec-one")["status"])
        self.assertFalse(any(event["event"] == "dispatch" for event in self.events()))
        replay = hd.route_begin(self.repo, FEATURE, "dec-one", current_basis=self.basis(),
                                main_session=True, now=T5)
        self.assertEqual(route_id, replay["route"]["route_id"])

    def test_reconcile_finishes_explicit_supersede_after_crash(self):
        self.create()
        replacement = self.envelope("dec-replacement")
        replacement["supersedes"] = "dec-one"
        packet = hd.validate_request(replacement, repo=self.repo, now=T1)
        state_store.atomic_json_write(hd.packet_path(self.repo, FEATURE, "dec-replacement"), packet)
        report = hd.reconcile(self.repo, FEATURE, main_session=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual("superseded", hd.show(self.repo, FEATURE, "dec-one")["status"])
        self.assertEqual("dec-replacement", hd.active_for_issue(self.repo, FEATURE, ISSUE)["id"])

    def test_reconcile_reports_orphan_needs_human_without_inventing_status(self):
        self.set_issue_raw("needs-human")
        report = hd.reconcile(self.repo, main_session=True)
        self.assertFalse(report["ok"])
        self.assertIn(ISSUE, report["needs_human_without_active"])
        self.assertIn("status: needs-human", self.issue_text())

    def test_direct_cli_isolated_mode_validate_request_and_present(self):
        envelope_path = Path(self.tmp.name) / "envelope.json"
        envelope_path.write_text(json.dumps(self.envelope(), ensure_ascii=False), encoding="utf-8")
        script = SCRIPT_DIR / "human_decision.py"
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

        def run(*arguments):
            completed = subprocess.run(
                [sys.executable, "-I", str(script), *arguments],
                cwd=self.repo, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            return json.loads(completed.stdout)

        packet = run("validate-request", "--repo", str(self.repo),
                     "--request", str(envelope_path), "--now", T0)
        self.assertEqual("pending", packet["status"])
        self.assertFalse(hd.packet_path(self.repo, FEATURE, "dec-one").exists())
        packet = run("request", "--main-session", "--repo", str(self.repo),
                     "--request", str(envelope_path), "--unattended", "--now", T0)
        self.assertEqual("pending", packet["status"])
        self.assertTrue(hd.packet_path(self.repo, FEATURE, "dec-one").is_file())
        component = run("present", "--main-session", "--repo", str(self.repo),
                        "--feature", FEATURE, "--id", "dec-one", "--now", T1)
        self.assertEqual(1, len(component["questions"]))
        self.assertEqual("waiting", hd.show(self.repo, FEATURE, "dec-one")["status"])

    def test_show_list_and_active_queries(self):
        self.assertEqual([], hd.list(self.repo, FEATURE))
        packet = self.create()
        self.assertEqual(packet, hd.show(self.repo, FEATURE, "dec-one"))
        self.assertEqual([packet], hd.list(self.repo, FEATURE, issue=ISSUE, active=True))
        self.assertEqual([packet], hd.active_for_feature(self.repo, FEATURE))
        routed = self.routing()
        hd.route_ack(self.repo, FEATURE, "dec-one", route_id=routed["route"]["route_id"],
                     ack=self.host_ack(routed), main_session=True, now=T4)
        self.assertIsNone(hd.active_for_issue(self.repo, FEATURE, ISSUE))
        self.assertEqual([], hd.list(self.repo, FEATURE, active=True))
        self.assertEqual(1, len(hd.list(self.repo, FEATURE, active=False)))


if __name__ == "__main__":
    unittest.main()
