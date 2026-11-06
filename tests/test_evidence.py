from database import IsolatedDatabase, verify_users
import json
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
from types import SimpleNamespace
import httpx
from starlette.exceptions import HTTPException
from backend import db
from backend.evidence import EvidenceTools, EvidenceContext, DEFINITIONS, INPUT_BYTES, TOOL_BYTES, encoded, http_exchanges, instance_relationships
from backend.investigator import validate_report
from test_provider_contract import REPORT, Clock, success

class RelationshipTests(unittest.TestCase):
    def test_legacy_generated_response_is_distinct_from_received_peer_status(self):
        rows = [
            {"id": "received", "kind": "log", "service": "order", "trace_id": "trace", "span_id": "client", "downstream": "payment", "http_status": 503},
            {"id": "returned", "kind": "span", "service": "order", "trace_id": "trace", "span_id": "server", "name": "order.checkout", "attributes": {"http_status": 502}},
            {"id": "other", "kind": "log", "service": "order", "trace_id": "other-trace", "message": "Checkout request completed", "http_status": 200},
        ]
        exchanges = http_exchanges(rows)
        self.assertEqual([(row["event"], row["response_status"], row["trace_id"]) for row in exchanges], [
            ("received_downstream_error_response", 503, "trace"), ("generated_server_response", 502, "trace"),
            ("generated_server_response", 200, "other-trace")])

    def test_instance_links_require_an_explicit_matching_identifier(self):
        rows = [{"id": "request", "target_instance": "worker-a"}, {"id": "exit", "instance": "worker-a"},
                {"id": "healthy", "instance": "worker-b"}, {"id": "startup-error", "exit_code": 1}]
        links = instance_relationships(rows)
        self.assertEqual(links[0]["same_instance_sources"], ["exit"])
        self.assertNotIn("startup-error", json.dumps(links))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patch = IsolatedDatabase(Path(self.directory.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        db.init()
        for owner in ("alice", "bob"):
            db.execute("INSERT INTO users VALUES(%s,%s,%s,%s,%s)", (owner, owner, owner, "hash", db.now()))
            db.execute("INSERT INTO runs(id,user_id,scenario,state,created_at) VALUES(%s,%s,%s,%s,%s)", (owner, owner, "healthy", "completed", db.now()))
        self.trace = "a" * 32
        with db.connection() as conn, conn.pipeline():
            for i in range(260):
                trace = self.trace if i >= 250 else "b" * 32
                self.insert(conn, str(i), "alice", "log", "payment", f"2026-11-02T00:{i // 60:02d}:{i % 60:02d}+00:00",
                            {"message": "Log " + str(i), "trace_id": trace, "level": "info"})
            self.insert(conn, "bob-source", "bob", "log", "payment", "2026-11-02T00:04:10+00:00", {"trace_id": self.trace})

    def insert(self, conn, id, owner, kind, service, timestamp, payload):
        conn.execute("INSERT INTO evidence(id,run_id,user_id,kind,service,timestamp,payload) VALUES(%s,%s,%s,%s,%s,%s,%s)", (id, owner, owner, kind, service, timestamp, json.dumps(payload)))

    def test_trace_time_and_account_filters_before_limits(self):
        tools = EvidenceTools("alice", "alice", groq=True)
        rows = tools.call("search_logs", {"trace_id": self.trace, "service": "payment", "limit": 2,
            "start_time": "2026-11-02T05:34:13+05:30", "end_time": "2026-11-02T00:04:17Z"})["records"]
        self.assertEqual([row["id"] for row in rows], ["253", "254"])
        self.assertTrue(all(row["trace_id"] == self.trace for row in rows))
        self.assertEqual(EvidenceTools("alice", "bob", groq=True).call("search_logs", {})["records"], [])
        self.assertTrue(all(row["service"] == "payment" for row in rows))

    def test_trace_span_filter_is_before_limit(self):
        with db.connection() as conn:
            for i in range(230):
                self.insert(conn, "span-" + str(i), "alice", "span", "order", f"2026-11-02T01:{i // 60:02d}:{i % 60:02d}Z",
                            {"trace_id": self.trace if i >= 225 else "c" * 32, "name": "http.payment", "duration_ms": i})
        result = EvidenceTools("alice", "alice").call("get_trace", {"trace_id": self.trace, "limit": 3})
        self.assertEqual([r["id"] for r in result], ["span-225", "span-226", "span-227"])

    def test_schema_validation(self):
        tools = EvidenceTools("alice", "alice", groq=True)
        cases = [("search_logs", {"trace_id": "g" * 32}), ("search_logs", {"trace_id": "0" * 32}),
                 ("get_trace", {}), ("get_metrics", {"trace_id": self.trace}), ("get_topology", {"limit": 2}),
                 ("find_traces", {"limit": 51}), ("search_logs", {"limit": True}),
                 ("search_logs", {"start_time": "2026-11-02"}), ("search_logs", {"end_time": None}),
                 ("search_logs", {"start_time": "2026-11-02T01:00:00Z", "end_time": "2026-11-02T00:00:00Z"})]
        for name, args in cases:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                tools.call(name, args)
        self.assertEqual(len(EvidenceTools("alice", "alice").call("search_logs", {})), 100)
        tools.call("search_logs", {})
        self.assertEqual(len(tools.seen), 20)

    def test_deduplication_payload_bounds_and_original_storage(self):
        with db.connection() as conn:
            self.insert(conn, "huge", "alice", "log", "gateway", "2026-11-02T00:00:00Z", {"message": "\u754c" * 20000})
        tools = EvidenceTools("alice", "alice", groq=True)
        context = EvidenceContext(tools)
        result = tools.call("search_logs", {"limit": 50})
        self.assertLessEqual(len(encoded(result)), TOOL_BYTES)
        context.add("search_logs", {"limit": 50}, result)
        context.add("search_logs", {"limit": 50}, result)
        self.assertEqual(len(context.records), len(result["records"]))
        payload, supplied = context.payload({"tools": DEFINITIONS}, "system", "investigate")
        self.assertLessEqual(len(encoded(payload)), INPUT_BYTES)
        self.assertNotIn("huge", supplied)
        self.assertGreater(result["omitted_groups"], 0)
        self.assertTrue(result["query_limit_reached"])
        self.assertEqual(len(json.loads(db.one("SELECT payload FROM evidence WHERE id='huge'")["payload"])["message"]), 20000)
        tools.supplied = supplied
        validate_report({**REPORT, "evidence_ids": [next(iter(supplied))]}, tools)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            validate_report({**REPORT, "evidence_ids": ["huge"]}, tools)
        for _ in range(24):
            context.add("search_logs", {"service": "payment", "trace_id": self.trace, "limit": 50,
                "start_time": "2026-11-02T00:00:00+00:00", "end_time": "2026-11-02T00:01:00+00:00"}, result)
        payload, supplied = context.payload({"tools": DEFINITIONS}, "system", "investigate")
        self.assertLessEqual(len(encoded(payload)), INPUT_BYTES)
        self.assertGreater(len(supplied), 0)
        from backend.investigator import REPORT_SCHEMA
        payload, supplied = context.payload({"response_format": {"type": "json_schema", "json_schema": {"schema": REPORT_SCHEMA}}}, "system", "report")
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(set(schema["properties"]["evidence_ids"]["items"]["enum"]), supplied)
        self.assertEqual(set(schema["properties"]["hypotheses"]["items"]["properties"]["evidence_ids"]["items"]["enum"]), supplied)
        self.assertLessEqual(len(encoded(payload)), INPUT_BYTES)
        self.assertNotIn("enum", REPORT_SCHEMA["properties"]["evidence_ids"]["items"])

    def test_grouped_repeats_retain_conflicting_observations_and_timings(self):
        from backend.evidence import compact
        rows = [{"id": str(i), "kind": "span", "service": "payment", "name": "payment.process",
                 "timestamp": f"2026-11-02T00:00:0{i}Z", "started_at": "2026-11-02T00:00:00Z",
                 "trace_id": self.trace, "span_id": "1" * 16, "parent_id": "2" * 16,
                 "status": "error" if i < 3 else "ok", "duration_ms": 4000 + i} for i in range(4)]
        result = compact(rows, TOOL_BYTES)
        self.assertEqual(len(result["records"]), 2)
        first = result["records"][0]
        self.assertEqual(first["repetitions"], 3)
        self.assertEqual(first["duration_ms_range"], [4000, 4002])
        self.assertEqual(first["parent_id"], "2" * 16)
        self.assertEqual({r["status"] for r in result["records"]}, {"ok", "error"})

    def test_timing_comparisons_require_supplied_trace_relationships(self):
        from backend.evidence import timing_comparisons, compact, http_exchanges, evidence_tables
        log = {"id": "timeout", "kind": "log", "service": "order", "trace_id": self.trace,
               "span_id": "caller", "timestamp": db.now(), "downstream": "payment", "timeout_ms": 2000}
        parent = {"id": "child", "kind": "span", "service": "payment", "trace_id": self.trace,
                  "span_id": "child-span", "parent_id": "caller", "timestamp": db.now(), "name": "payment.checkout", "duration_ms": 4020}
        operation = {**parent, "id": "operation", "span_id": "work", "parent_id": "child-span",
                     "name": "payment.process", "duration_ms": 4000, "status": "ok"}
        rows = [log, parent, operation]
        result = timing_comparisons(rows)[0]
        measured = next(o for o in result["operations"] if o["source"] == "operation")
        self.assertTrue(measured["exceeds_caller_timeout"])
        self.assertEqual(measured["relationship_sources"], ["operation", "child"])
        self.assertEqual(timing_comparisons([log, operation]), [])
        self.assertEqual(timing_comparisons([log, {**parent, "trace_id": "c" * 32}, operation]), [])
        # Priority trace spans survive a competing flood of unrelated observations.
        noise = [{**log, "id": f"noise-{i}", "message": "noise" * 100} for i in range(30)]
        compacted = compact(noise + rows, 2000, {"child", "operation"})
        self.assertTrue({"child", "operation"} <= {r["id"] for r in compacted["records"]})
        caller = {**parent, "id": "caller-source", "span_id": "caller", "service": "order"}
        self.assertEqual(http_exchanges([log, caller])[0]["event"], "caller_timeout")
        response_log = {**log, "http_status": 503}
        response_log.pop("timeout_ms")
        exchange = http_exchanges([response_log, caller])[0]
        self.assertEqual(exchange["event"], "received_downstream_error_response")
        self.assertNotIn("explicit_deadline_ms", exchange)
        caller_only = http_exchanges([response_log])[0]
        self.assertEqual(caller_only["event"], "received_downstream_error_response")
        self.assertEqual(caller_only["sources"], ["timeout"])
        self.assertNotIn("measured_elapsed_ms_not_deadline", caller_only)
        self.assertEqual(http_exchanges([{**response_log, "http_status": 200}])[0]["event"], "received_downstream_response")
        connection_log = {k: v for k, v in log.items() if k not in {"downstream", "timeout_ms"}}
        connection_log["error_type"] = "ConnectError"
        connection_span = {**caller, "name": "http.payment"}
        self.assertEqual(http_exchanges([connection_log, connection_span])[0]["event"], "connection_failed_without_http_response")
        table = evidence_tables(rows)["span"]
        restored = [dict(zip(table["columns"], r)) for r in table["rows"]]
        self.assertEqual(restored[1]["duration_ms"], 4000)
        self.assertEqual(restored[1]["parent_id"], "child-span")

    def test_long_collection_baseline_retains_later_metric_change_and_recovery(self):
        from backend.investigator import collect_baseline, REPORT_SCHEMA
        from backend.lab import store_rows, summary
        rows = [{"id": f"metric-{i}", "kind": "metric", "service": "jobs",
                 "timestamp": f"2026-11-02T00:{i // 60:02d}:{i % 60:02d}Z",
                 "requests": i, "queue_depth": 80 if 40 <= i < 80 else 0,
                 "message": "Queue sample"} for i in range(120)]
        db.execute("INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                   ("long", "alice", "Long", "", "paste", db.now(), "long", json.dumps(summary(rows))))
        store_rows("long", "alice", rows)
        tools = EvidenceTools(None, "alice", groq=True, collection_id="long")
        reads = []
        context, _ = collect_baseline(tools, lambda name, args, result: reads.append(result))
        self.assertTrue(all(len(encoded(result)) <= TOOL_BYTES for result in reads))
        payload, supplied = context.payload({"response_format": {"type": "json_schema", "json_schema": {"schema": REPORT_SCHEMA}}},
                                            "Assess the operational evidence.", "Report")
        selected = [row for row in rows if row["id"] in supplied]
        self.assertEqual({row["queue_depth"] for row in selected}, {0, 80})
        self.assertTrue(any(int(row["id"].split("-")[1]) >= 100 for row in context.records.values()))
        self.assertLessEqual(len(encoded(payload)), INPUT_BYTES)
        self.assertEqual(len(db.evidence(None, "alice", collection_id="long", limit=200)), 120)

    def test_status_only_callers_cannot_establish_an_internal_cause(self):
        from backend.evidence import caller_only_responses
        received = {"kind": "log", "service": "frontend", "level": "error", "downstream": "catalog", "http_status": 503}
        completed = {"kind": "log", "service": "frontend", "level": "error", "message": "Request completed", "http_status": 502}
        self.assertTrue(caller_only_responses([received, completed]))
        self.assertFalse(caller_only_responses([received, {**completed, "stack_trace": "TypeError: invalid value"}]))
        self.assertFalse(caller_only_responses([received, {"kind": "span", "service": "catalog", "name": "db.acquire", "status": "error"}]))
        self.assertFalse(caller_only_responses([received, {**completed, "message": "Database query failed"}]))

    def test_replayed_queries_use_fresh_report_and_enforce_uncertainty(self):
        from backend.investigator import Investigator
        from backend.lab import store_rows, summary
        rows = [{"id": "caller", "kind": "log", "service": "gateway", "timestamp": db.now(),
                 "level": "error", "message": "Dependency failed", "downstream": "catalog", "http_status": 503}]
        db.execute("INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                   ("callers", "alice", "Caller observations", "", "paste", db.now(), "callers", json.dumps(summary(rows))))
        store_rows("callers", "alice", rows)
        payloads = []
        invalid = False
        def respond(request):
            payload = json.loads(request.content)
            payloads.append(payload)
            self.assertNotIn("tools", payload)
            schema = payload["response_format"]["json_schema"]["schema"]
            self.assertEqual(schema["properties"]["assessment"]["enum"], ["inconclusive"])
            self.assertEqual(schema["properties"]["origin"]["enum"], ["unknown"])
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                **REPORT, "assessment": "supported" if invalid else "inconclusive", "origin": "gateway" if invalid else "unknown",
                "affected_services": ["gateway"], "evidence_ids": ["caller"], "hypotheses": []})}}]})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                return await Investigator(client).groq(EvidenceTools(None, "alice", groq=True, collection_id="callers"),
                    lambda *args: None, replay_queries=[{"tool": "search_logs", "arguments": {"service": "gateway"}}])
        with patch("backend.investigator.GROQ_KEY", "fixture-key"):
            report, usage = asyncio.run(run())
            self.assertEqual(report["assessment"], "inconclusive")
            self.assertEqual(usage["requests"], 1)
            self.assertIn("replayed", usage["query_mode"])
            invalid = True
            with self.assertRaisesRegex(ValueError, "invalid report"):
                asyncio.run(run())
        self.assertEqual(len(payloads), 2)

    def test_restart_marks_waiting_interrupted_and_keeps_activity(self):
        db.execute("INSERT INTO incidents VALUES(%s,%s,%s,%s,%s,%s,%s)", ("incident", "alice", "alice", "title", "resolved", db.now(), db.now()))
        db.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at,activity,usage) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            ("waiting", "incident", "alice", "groq", "quota_waiting", db.now(), '[{"tool":"search_logs","count":2}]', '{"requests":1}'))
        db.init()
        row = db.one("SELECT * FROM investigations WHERE id='waiting'")
        self.assertEqual(row["state"], "interrupted")
        self.assertEqual(json.loads(row["activity"])[0]["count"], 2)
        self.assertEqual(json.loads(row["usage"])["requests"], 1)

    def test_waiting_counts_as_active_and_retry_preserves_attempt(self):
        from backend.api import start_investigation
        db.execute("INSERT INTO incidents VALUES(%s,%s,%s,%s,%s,%s,%s)", ("incident", "alice", "alice", "title", "resolved", db.now(), db.now()))
        incident = db.one("SELECT * FROM incidents WHERE id='incident'")
        db.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                   ("waiting", "incident", "alice", "groq", "quota_waiting", db.now()))
        enqueued = []
        request = SimpleNamespace(path_params={"id": "incident"}, app=SimpleNamespace(state=SimpleNamespace(
            investigator=SimpleNamespace(enqueue=enqueued.append))))
        with patch("backend.api.owned", return_value=incident), patch("backend.api.auth.csrf"), patch("backend.api.auth.body", new=AsyncMock(return_value={"engine": "rules"})):
            with self.assertRaises(HTTPException) as duplicate:
                asyncio.run(start_investigation(request))
            self.assertEqual(duplicate.exception.status_code, 409)
            db.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                       ("queued", "incident", "alice", "rules", "queued", db.now()))
            with self.assertRaises(HTTPException) as limit:
                asyncio.run(start_investigation(request))
            self.assertEqual(limit.exception.status_code, 429)
            db.execute("UPDATE investigations SET state='failed'")
            response = asyncio.run(start_investigation(request))
            self.assertEqual(response.status_code, 202)
            self.assertEqual(len(enqueued), 1)
            self.assertNotEqual(enqueued[0], "waiting")
            self.assertEqual(db.one("SELECT state FROM investigations WHERE id='waiting'")["state"], "failed")

    def test_persisted_wait_keeps_evidence_and_resumes_without_repeating_tools(self):
        from backend.investigator import Investigator
        from backend.quota import GroqQuota
        db.execute("INSERT INTO incidents VALUES(%s,%s,%s,%s,%s,%s,%s)", ("incident", "alice", "alice", "title", "resolved", db.now(), db.now()))
        db.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                   ("live", "incident", "alice", "groq", "queued", db.now()))
        clock = Clock()
        observed = []
        async def sleep(seconds):
            row = db.one("SELECT * FROM investigations WHERE id='live'")
            self.assertEqual(row["state"], "quota_waiting")
            self.assertGreater(len(json.loads(row["activity"])[0]["evidence_ids"]), 0)
            self.assertIn("retry_at", json.loads(row["usage"]))
            observed.append(row["activity"])
            await clock.sleep(seconds)
        attempts = []
        def respond(request):
            attempts.append(json.loads(request.content))
            return httpx.Response(429, headers={"Retry-After": "5"}) if len(attempts) == 2 else success(attempts[-1])
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await Investigator(client, GroqQuota(clock=clock, sleep=sleep)).investigate("live")
        with patch("backend.investigator.GROQ_KEY", "fixture-key"):
            asyncio.run(run())
        row = db.one("SELECT * FROM investigations WHERE id='live'")
        self.assertEqual(row["state"], "completed", row["error"])
        self.assertEqual(len(json.loads(row["activity"])), 4)
        self.assertEqual(attempts[1], attempts[2])
        self.assertEqual(row["activity"], observed[0])
