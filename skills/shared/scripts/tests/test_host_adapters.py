#!/usr/bin/env python3
"""Four-host capability degradation and destructive session-resource guards."""
import json
import os
import shlex
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
import host_adapters as hosts
import session_title


class HostAdapterTest(unittest.TestCase):
    def test_four_canonical_hosts_and_statuses(self):
        self.assertEqual(hosts.HOSTS, ("claude-code", "codex", "zcode", "dsh"))
        config = {"dispatch": {"hosts": {"codex": {
            "model": "configured-model",
            "capabilities": {"independent_session": True, "browser": False},
        }}}}
        result = hosts.probe_host("codex", config)
        self.assertEqual(result["capabilities"]["independent_session"], "declared")
        self.assertEqual(result["capabilities"]["browser"], "unavailable")
        self.assertEqual(result["capabilities"]["session_activity"], "unknown")
        self.assertTrue(result["model_config_present"])
        self.assertNotEqual(result["capabilities"]["independent_session"], "available")

    def test_real_probe_results_can_degrade(self):
        for value, expected in ((True, "available"), (False, "unavailable"), (None, "unknown")):
            with self.subTest(value=value):
                result = hosts.probe_host("zcode", probes={"browser": value})
                self.assertEqual(result["capabilities"]["browser"], expected)
        result = hosts.probe_host("dsh", probes={"runtime": lambda _ctx: {"status": "available", "proof": "command"}})
        self.assertEqual(result["capabilities"]["runtime"], "available")

    def test_no_independent_session_never_permits_same_context_review(self):
        for host in hosts.HOSTS:
            with self.subTest(host=host):
                with self.assertRaises(hosts.HostCapabilityError):
                    hosts.require_independent_review(host)
        result = hosts.require_independent_review("claude", probes={"independent_session": True})
        self.assertEqual(result["capabilities"]["independent_session"], "available")

    def test_session_activity_unknown_blocks_and_release_unbinds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            state = Path(temporary) / "state.json"
            transcript = Path(temporary) / "transcript.jsonl"
            transcript.write_text("keep\n")
            with mock.patch.dict(os.environ, {"SESSION_TITLE_STATE": str(state)}):
                binding = session_title.normalize_binding(
                    root, feature="docs/features/mod/slug", issue=None, role="supervisor")
                session_title.set_binding("codex", "s1", binding)
                subject = {"kind": "feature", "id": "slug", "feature": "mod/slug",
                           "feature_dir": str(root / "dev/features/mod/slug")}
                result = hosts.session_resource_state(root, subject, [root])
                self.assertTrue(result["blockers"])
                self.assertIn("未知", "；".join(result["blockers"]))
                session_title.release_binding("codex", "s1")
                result = hosts.session_resource_state(root, subject, [root])
                self.assertEqual(result["sessions"], [])
                self.assertFalse(result["blockers"])
            self.assertEqual(transcript.read_text(), "keep\n")

    def test_external_unreachable_codex_dsh_preserve_unique_branch(self):
        for host in ("codex", "dsh"):
            policy = hosts.preservation_requirements(host, {"worktree_reachable": "unknown"}, unique_branch=True)
            self.assertTrue(policy["preserve_branch"])
        policy = hosts.preservation_requirements("zcode", {"worktree_reachable": "unknown"}, unique_branch=True)
        self.assertFalse(policy["preserve_branch"])
        self.assertTrue(policy["block_worktree"])

    def _reviewer(self):
        review = {"review_id": "review-1", "round": 1, "head": "a" * 40}
        return {"host": "zcode", "session": "review-session", "agent": "supervisor-review",
                "role": "review", "review": review}, review

    def _ndjson_proof(self, host, record, review):
        return {"host": host, "session": "review-session", "review": review,
                "native_hash": hosts._native_digest(record, review)}

    def _codex_role_binding(self, declarations, trust="host-verified", reject=False):
        reviewer, review = self._reviewer()
        reviewer["host"] = "codex"
        implementer = {"session": "impl-session", "agent": "development"}
        record = dict(declarations, id="review-session")
        config = {"enforcement": {"review-trust": trust, "require-host-capabilities": False}}
        proof = self._ndjson_proof("codex", record, review)
        proof["operator_attestation"] = {
            "operator": "operator-1", "statement": "independent review confirmed",
            "confirmed_at": "2026-11-20T00:00:00Z"}

        def call(method, params):
            if method == "initialize":
                return {}
            if method == "thread/read":
                return {"thread": dict(record)}
            if method == "thread/name/set":
                record["name"] = params["name"]
                return {}
            self.fail("unexpected native method: " + method)

        with mock.patch("session_title.default_command", return_value=["controlled-native-fixture"]), \
                mock.patch("session_title.NDJSONClient") as client:
            client.return_value.__enter__.return_value.call.side_effect = call
            if reject:
                with self.assertRaises(hosts.HostCapabilityError):
                    hosts.write_review_binding("codex", reviewer, implementer, review, config)
                self.assertNotIn("name", record)
                # Supply a valid binding/hash so rejection can only come from role evidence.
                record["review"] = review
                proof["native_hash"] = hosts._native_digest(record, review)
                with self.assertRaises(hosts.HostCapabilityError):
                    hosts.verify_review_session("codex", reviewer, implementer, proof, config)
            else:
                result = hosts.write_review_binding("codex", reviewer, implementer, review, config)
                self.assertTrue(result["written"])
                proof["native_hash"] = hosts._native_digest(record, review)
                verified = hosts.verify_review_session("codex", reviewer, implementer, proof, config)
                self.assertEqual(verified["role"], "supervisor-review")
                self.assertEqual(verified["verification"], "host-verified")

    def test_codex_native_role_fields_bind_and_verify(self):
        nested = {"source": {"subAgent": {"thread_spawn": {"agent_role": "supervisor_review"}}}}
        cases = [{"agentRole": "SUPERVISOR_REVIEW"}, nested,
                 dict(nested, agentRole="supervisor-review")]
        for field in ("role", "subagent_type", "subagentType", "agent_type", "agentType", "agent"):
            cases.extend([{field: "supervisor_review"},
                          dict(nested, **{field: "supervisor-review", "agentRole": "supervisor-review"})])
        for declarations in cases:
            with self.subTest(declarations=declarations):
                self._codex_role_binding(declarations)

    def test_codex_native_role_conflicts_and_invalid_fields_fail_closed(self):
        nested = {"source": {"subAgent": {"thread_spawn": {"agent_role": "development"}}}}
        cases = [{}, {"agentRole": "development"}, {"agentRole": "unknown-role"},
                 dict(nested, agentRole="supervisor-review"),
                 {"metadata": {"agentRole": "supervisor-review"}},
                 {"title": "supervisor-review"}]
        for field in ("role", "subagent_type", "subagentType", "agent_type", "agentType", "agent"):
            cases.append({"agentRole": "supervisor-review", field: "development"})
            for invalid in (False, 0, [], {}, 1):
                cases.append({"agentRole": "supervisor-review", field: invalid})
        for invalid in (False, 0, [], {}, 1):
            cases.extend([{"role": "supervisor-review", "agentRole": invalid},
                          {"agentRole": "supervisor-review", "source": {
                              "subAgent": {"thread_spawn": {"agent_role": invalid}}}}])
        for malformed in (None, [], "supervisor-review", {"subAgent": []},
                          {"subAgent": {"thread_spawn": "supervisor-review"}}):
            cases.append({"agentRole": "supervisor-review", "source": malformed})
        for trust in ("host-verified", "operator-attested"):
            for declarations in cases:
                with self.subTest(trust=trust, declarations=declarations):
                    self._codex_role_binding(declarations, trust, reject=True)

    def test_zcode_and_codex_require_real_native_review_session(self):
        for host in ("zcode", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                log = root / "native.jsonl"
                reviewer, review = self._reviewer()
                reviewer["host"] = host
                implementer = {"session": "impl-session", "agent": "development"}
                command = [sys.executable, str(SCRIPT_DIR / "tests" / "fake_host_server.py")]
                env = {"FAKE_HOST": host, "FAKE_HOST_LOG": str(log),
                       "FAKE_ROLE": "supervisor-review", "FAKE_REVIEW": json.dumps(review),
                       "SESSION_TITLE_{}_COMMAND".format(host.upper()): shlex.join(command)}
                config = {"enforcement": {"review-trust": "host-verified"}}
                with mock.patch.dict(os.environ, env):
                    record = hosts.read_native_review_session(host, reviewer, config=config)
                    proof = self._ndjson_proof(host, record, review)
                    result = hosts.verify_review_session(host, reviewer, implementer, proof, config)
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["verification"], "host-verified")
                    with self.assertRaises(hosts.HostCapabilityError):
                        hosts.verify_review_session(host, reviewer, implementer,
                                                    dict(proof, native_hash="0" * 64), config)
                    with self.assertRaises(hosts.HostCapabilityError):
                        hosts.verify_review_session(host, reviewer, implementer,
                                                    dict(proof, review=dict(review, review_id="other")), config)
                methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
                self.assertIn("session/list" if host == "zcode" else "thread/read", methods)

    def test_fake_reviewer_or_implementer_masquerading_as_review_fails(self):
        reviewer, review = self._reviewer()
        implementer = {"session": "impl-session", "agent": "development"}
        with self.assertRaises(hosts.HostCapabilityError):
            hosts.verify_review_session("zcode", dict(reviewer, session="impl-session"),
                                        implementer, {"review": review},
                                        {"enforcement": {"review-trust": "host-verified"}})
        with self.assertRaises(hosts.HostCapabilityError):
            hosts.verify_review_session("zcode", dict(reviewer, agent="development"),
                                        implementer, {"review": review},
                                        {"enforcement": {"review-trust": "host-verified"}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [sys.executable, str(SCRIPT_DIR / "tests" / "fake_host_server.py")]
            env = {"FAKE_HOST": "zcode", "FAKE_HOST_LOG": str(root / "log"),
                   "FAKE_REVIEW": json.dumps(review), "FAKE_MISSING_SESSION": "1",
                   "SESSION_TITLE_ZCODE_COMMAND": shlex.join(command)}
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(hosts.HostCapabilityError):
                    hosts.read_native_review_session("zcode", reviewer)

    def test_claude_native_task_record_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewer, review = self._reviewer()
            reviewer["host"] = "claude-code"
            host_root = root / "claude"
            record_path = host_root / "tasks" / "review-session.json"
            record_path.parent.mkdir(parents=True)
            record = {"id": "review-session", "subagent_type": "supervisor-review", "review": review}
            record_path.write_text(json.dumps(record))
            reviewer["native_record"] = str(record_path)
            proof = {"native_record": str(record_path), "review": review,
                     "native_hash": hosts._native_digest(record, review)}
            with mock.patch.dict(os.environ, {"SESSION_TITLE_HOST_ROOT": str(host_root)}):
                result = hosts.verify_review_session(
                    "claude-code", reviewer, {"session": "impl", "agent": "development"}, proof,
                    {"enforcement": {"review-trust": "host-verified"}})
            self.assertTrue(result["ok"])

    def test_native_identity_contradiction_never_uses_operator_fallback(self):
        reviewer, review = self._reviewer()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [sys.executable, str(SCRIPT_DIR / "tests" / "fake_host_server.py")]
            env = {"FAKE_HOST": "zcode", "FAKE_HOST_LOG": str(root / "log"),
                   "FAKE_ROLE": "development", "FAKE_REVIEW": json.dumps(review),
                   "SESSION_TITLE_ZCODE_COMMAND": shlex.join(command)}
            config = {"enforcement": {"review-trust": "operator-attested",
                                      "require-host-capabilities": False}}
            with mock.patch.dict(os.environ, env):
                record = hosts.read_native_review_session("zcode", reviewer, config=config)
                proof = self._ndjson_proof("zcode", record, review)
                proof["operator_attestation"] = {
                    "operator": "operator-1", "statement": "independent review confirmed",
                    "confirmed_at": "2026-11-15T00:00:00Z"}
                with self.assertRaises(hosts.HostCapabilityError):
                    hosts.verify_review_session(
                        "zcode", reviewer, {"session": "impl", "agent": "development"}, proof, config)

    def test_native_write_then_read_binds_zcode_and_codex_without_preseeded_review(self):
        for host in ("zcode", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                reviewer, review = self._reviewer()
                reviewer["host"] = host
                implementer = {"session": "impl-session", "agent": "development"}
                log = root / "native.jsonl"
                command = [sys.executable, str(SCRIPT_DIR / "tests" / "fake_host_server.py")]
                env = {"FAKE_HOST": host, "FAKE_HOST_LOG": str(log),
                       "FAKE_HOST_STATE": str(root / "state.json"),
                       "FAKE_ROLE": "supervisor-review",
                       "SESSION_TITLE_{}_COMMAND".format(host.upper()): shlex.join(command)}
                self.assertNotIn("FAKE_REVIEW", env)
                config = {"enforcement": {"review-trust": "host-verified"}}
                with mock.patch.dict(os.environ, env):
                    written = hosts.write_review_binding(host, reviewer, implementer, review, config)
                    self.assertTrue(written["written"])
                    self.assertFalse(written["requires_native_apply"])
                    record = hosts.read_native_review_session(host, reviewer, config=config)
                    self.assertEqual(hosts._review_binding(record), review)
                    proof = self._ndjson_proof(host, record, review)
                    verified = hosts.verify_review_session(host, reviewer, implementer, proof, config)
                    self.assertTrue(verified["ok"])
                methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
                self.assertIn("v4/command" if host == "zcode" else "thread/name/set", methods)

    def test_dsh_native_command_writes_metadata_then_reads_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewer, review = self._reviewer()
            reviewer["host"] = "dsh"
            state = root / "native-state.json"
            command_script = root / "dsh-native.py"
            command_script.write_text("""import json,os
p=%r
if os.environ.get('AGENT_DEV_SESSION_WRITE') == '1':
 record={'session': {'id': os.environ['AGENT_DEV_SESSION_ID'], 'role': 'supervisor-review',
                    'review': json.loads(os.environ['AGENT_DEV_REVIEW_METADATA'])}}
 with open(p,'w') as f: json.dump(record,f)
else:
 with open(p) as f: print(json.dumps(json.load(f)))
""" % str(state), encoding="utf-8")
            config = {"enforcement": {"review-trust": "host-verified"},
                      "dispatch": {"hosts": {"dsh": {"native_session_command": [sys.executable, str(command_script)]}}}}
            result = hosts.write_review_binding("dsh", reviewer,
                                                {"session": "impl", "agent": "development"}, review, config)
            self.assertTrue(result["written"])
            self.assertEqual(result["channel"], "dsh-native-command")
            self.assertEqual(hosts._review_binding(result["native_session"]), review)

    def test_claude_native_hook_instruction_is_not_fake_persistence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewer, review = self._reviewer()
            reviewer["host"] = "claude-code"
            host_root = root / "claude"
            path = host_root / "tasks" / "review-session.json"
            path.parent.mkdir(parents=True)
            record = {"id": "review-session", "subagent_type": "supervisor-review",
                      "title": "Review", "review": {}}
            path.write_text(json.dumps(record))
            reviewer["native_record"] = str(path)
            with mock.patch.dict(os.environ, {"SESSION_TITLE_HOST_ROOT": str(host_root)}):
                implementer = {"session": "impl", "agent": "development"}
                config = {"enforcement": {"review-trust": "host-verified"}}
                result = hosts.write_review_binding(
                    "claude-code", reviewer, implementer, review)
                self.assertFalse(result["written"])
                self.assertTrue(result["requires_native_apply"])
                self.assertIn("hookSpecificOutput", result["native_instruction"])
                self.assertEqual(json.loads(path.read_text())["title"], "Review")
                # Before the host applies the instruction, verification must fail.
                current = hosts.read_native_review_session("claude-code", reviewer, config=config)
                proof = {"review": review, "native_hash": hosts._native_digest(current, review)}
                with self.assertRaises(hosts.HostCapabilityError):
                    hosts.verify_review_session("claude-code", reviewer, implementer, proof, config)
                # Simulate the host applying its own native sessionTitle instruction.
                record = json.loads(path.read_text())
                record["title"] = result["native_instruction"]["hookSpecificOutput"]["sessionTitle"]
                path.write_text(json.dumps(record))
                with mock.patch.object(hosts, "_write_claude_binding") as write_again:
                    second = hosts.write_review_binding(
                        "claude-code", reviewer, implementer, review, config)
                write_again.assert_not_called()
                self.assertTrue(second["written"])
                self.assertFalse(second["requires_native_apply"])
                self.assertNotIn("native_instruction", second)
                self.assertEqual(second["channel"], "claude-native-readback")
                proof = {"review": review,
                         "native_hash": hosts._native_digest(second["native_session"], review)}
                verified = hosts.verify_review_session(
                    "claude-code", reviewer, implementer, proof, config)
                self.assertTrue(verified["ok"])

    def test_dsh_native_query_and_operator_cooperative_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewer, review = self._reviewer()
            reviewer["host"] = "dsh"
            query = root / "dsh-query.py"
            marker = root / "called"
            query.write_text("import json,os; open(%r,'w').write(os.environ['AGENT_DEV_SESSION_ID']); "
                             "print(json.dumps({'session': {'id': os.environ['AGENT_DEV_SESSION_ID'], "
                             "'role': 'supervisor-review', 'review': %r}}))" %
                             (str(marker), review), encoding="utf-8")
            config = {"enforcement": {"review-trust": "host-verified"},
                      "dispatch": {"hosts": {"dsh": {"native_session_command": [sys.executable, str(query)]}}}}
            record = hosts.read_native_review_session("dsh", reviewer, config=config)
            proof = {"review": review, "native_hash": hosts._native_digest(record, review)}
            result = hosts.verify_review_session("dsh", reviewer,
                                                 {"session": "impl", "agent": "development"}, proof, config)
            self.assertTrue(result["ok"])
            self.assertEqual(marker.read_text(), "review-session")
        reviewer, review = self._reviewer()
        proof = {"review": review, "operator_attestation": {
            "operator": "operator-1", "statement": "independent review confirmed",
            "confirmed_at": "2026-11-15T00:00:00Z"}}
        with mock.patch("session_title.default_command", return_value=None):
            with self.assertRaises(hosts.HostCapabilityError):
                hosts.verify_review_session(
                    "zcode", reviewer, {"session": "impl", "agent": "development"}, proof,
                    {"enforcement": {"review-trust": "operator-attested",
                                     "require-host-capabilities": True}})
            result = hosts.verify_review_session(
                "zcode", reviewer, {"session": "impl", "agent": "development"}, proof,
                {"enforcement": {"review-trust": "operator-attested",
                                 "require-host-capabilities": False}})
        self.assertTrue(result["cooperative"])
        self.assertEqual(result["verification"], "operator-attested")


if __name__ == "__main__":
    unittest.main()
