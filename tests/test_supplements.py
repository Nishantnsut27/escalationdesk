import asyncio
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import httpx
from backend import auth, db
from backend.api import create_app
from backend.evidence import encoded, INPUT_BYTES
from backend.investigator import Investigator
from backend.quota import GroqQuota
from backend.supplements import saved_evidence, supplement_payload, validate_supplement
from test_provider_contract import Clock, REPORT


RESULT = {"summary": "The observations are insufficient to assess the original claim.", "relationship": "cannot_assess",
          "explanations": [], "questions": [{"question": "Was processing delayed?", "suggested_check": "Inspect linked processing spans."}],
          "limitations": ["Only saved evidence was reviewed."]}


class SupplementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = patch.object(db, "DATA", Path(directory.name))
        self.storage.start()
        self.addCleanup(self.storage.stop)
        db.init()
        for owner in ("alice", "bob"):
            db.execute("INSERT INTO users VALUES(?,?,?,?,?)", (owner, owner, owner, "hash", db.now()))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (auth.digest(owner), owner, time.time() + 600))
            db.execute("INSERT INTO runs(id,user_id,scenario,state,created_at) VALUES(?,?,?,?,?)", (owner, owner, "healthy", "completed", db.now()))
            db.execute("INSERT INTO incidents VALUES(?,?,?,?,?,?,?)", (owner, owner, owner, "title", "resolved", db.now(), db.now()))
        self.add_source("one")
        self.add_source("two", service="order")
        self.add_source("bob-only", owner="bob")
        self.add_parent("parent")
        self.enqueued = []
        self.app = create_app()
        self.app.state.investigator = SimpleNamespace(enqueue=self.enqueued.append, enqueue_supplement=self.enqueued.append)
        self.configured = patch("backend.api.GROQ_KEY", "test-key")
        self.configured.start()
        self.addCleanup(self.configured.stop)

    def add_source(self, id, owner="alice", kind="log", service="payment", **payload):
        db.execute("INSERT INTO evidence(id,run_id,user_id,kind,service,timestamp,payload) VALUES(?,?,?,?,?,?,?)", (id, owner, owner, kind, service,
                   "2026-11-02T00:00:04+00:00", json.dumps({"message": "Observed", **payload})))

    def add_parent(self, id, engine="groq", ids=None, report=None):
        db.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at,completed_at,report,usage) VALUES(?,?,?,?,?,?,?,?,?)",
                   (id, "alice", "alice", engine, "completed", db.now(), db.now(), json.dumps(report or REPORT),
                    json.dumps({"requests": 2, "supplied_evidence_ids": ids if ids is not None else ["one", "two"]})))

    def parent(self, id="parent"):
        return db.one("SELECT * FROM investigations WHERE id=?", (id,))

    def client(self, owner="alice", csrf=True):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test",
            cookies={auth.COOKIE: owner}, headers={"X-CSRF-Token": auth.digest(owner + ":csrf")} if csrf else {})

    async def submit(self, parent="parent", body=None):
        async with self.client() as client:
            response = await client.post(f"/api/investigations/{parent}/supplements", json=body or {})
            self.assertIn(response.status_code, (200, 202), response.text)
            return response.json()

    async def run_supplement(self, respond):
        row = await self.submit()
        parent_before = self.parent()
        calls, waits = [], []
        clock = Clock()
        async def sleep(seconds):
            current = db.one("SELECT * FROM supplements WHERE id=?", (row["id"],))
            self.assertEqual(current["state"], "quota_waiting")
            waits.append(current)
            await clock.sleep(seconds)
        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertLessEqual(len(request.content), INPUT_BYTES)
            self.assertEqual(body["max_completion_tokens"], 2048)
            self.assertEqual(body["reasoning_effort"], "low")
            self.assertNotIn("tools", body)
            return respond(len(calls), body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("backend.investigator.GROQ_KEY", "secret"), patch("backend.evidence.EvidenceTools.call", side_effect=AssertionError("No fresh queries")):
                await Investigator(client, GroqQuota(clock=clock, sleep=sleep)).supplement(row["id"])
        self.assertEqual(self.parent(), parent_before)
        saved = db.one("SELECT * FROM supplements WHERE id=?", (row["id"],))
        self.assertNotIn("secret", saved["usage"])
        return saved, calls, waits

    @staticmethod
    def response(result=None, finish="stop"):
        return httpx.Response(200, json={"model": "fixture", "usage": {"total_tokens": 19}, "choices": [
            {"finish_reason": finish, "message": {"content": json.dumps(RESULT if result is None else result), "reasoning": "PRIVATE"}}]})

    async def test_scope_over_200_deduplicated_owned_and_missing(self):
        ids = []
        for i in range(215):
            id = f"extra-{i}"
            self.add_source(id)
            ids.append(id)
        ids += ["one", "one", "missing", "bob-only"]
        self.add_parent("wide", ids=ids)
        async with self.client() as client:
            scope = (await client.get("/api/investigations/wide/evidence")).json()
        self.assertEqual(len(scope["evidence"]), 216)
        self.assertEqual(scope["unavailable_source_ids"], ["missing", "bob-only"])
        self.assertEqual(scope["coverage"], "recorded_supplied")
        self.assertNotIn("two", {r["id"] for r in scope["evidence"]})

    async def test_legacy_fallback_and_report_switch(self):
        self.add_parent("legacy", report={**REPORT, "evidence_ids": ["one"], "hypotheses": [{"claim": "Maybe", "evidence_ids": ["two", "one"]}]})
        db.execute("UPDATE investigations SET usage='{}' WHERE id='legacy'")
        scope = saved_evidence(self.parent("legacy"))
        self.assertEqual(scope["coverage"], "legacy_citations_only")
        self.assertEqual(scope["source_ids"], ["one", "two"])
        self.add_parent("other", ids=["two"])
        self.assertEqual(saved_evidence(self.parent("other"))["source_ids"], ["two"])
        self.assertEqual(saved_evidence(self.parent())["source_ids"], ["one", "two"])

    async def test_auth_csrf_rules_and_read_only_polling(self):
        before = self.parent()
        async with self.client("bob") as client:
            for suffix in ("", "/evidence"):
                self.assertEqual((await client.get("/api/investigations/parent" + suffix)).status_code, 404)
            self.assertEqual((await client.post("/api/investigations/parent/supplements", json={})).status_code, 404)
        async with self.client(csrf=False) as client:
            self.assertEqual((await client.post("/api/investigations/parent/supplements", json={})).status_code, 403)
        self.add_parent("rules", engine="rules")
        async with self.client() as client:
            for _ in range(3):
                self.assertEqual((await client.get("/api/investigations/parent")).json()["supplements"], [])
                await client.get("/api/incidents/alice")
                await client.get("/api/investigations/parent/evidence")
            self.assertEqual((await client.post("/api/investigations/rules/supplements", json={})).status_code, 409)
            self.assertEqual((await client.post("/api/investigations/parent/supplements", json={"retry": "yes"})).status_code, 400)
        self.assertEqual(self.enqueued, [])
        self.assertEqual(self.parent(), before)

    async def test_duplicate_clicks_completed_idempotence_and_explicit_retry(self):
        values = await asyncio.gather(*(self.submit() for _ in range(8)))
        ids = {row["id"] for row in values}
        self.assertEqual(len(ids), 1)
        self.assertEqual(len(self.enqueued), 1)
        first = ids.pop()
        for state in ("running", "quota_waiting", "completed"):
            db.execute("UPDATE supplements SET state=? WHERE id=?", (state, first))
            self.assertEqual((await self.submit(body={"retry": True}))["id"], first)
        db.execute("UPDATE supplements SET state='failed' WHERE id=?", (first,))
        self.assertEqual((await self.submit())["id"], first)
        second = await self.submit(body={"retry": True})
        self.assertNotEqual(second["id"], first)
        self.assertEqual(db.one("SELECT state FROM supplements WHERE id=?", (first,))["state"], "failed")
        async with self.client() as client:
            saved = (await client.get("/api/investigations/parent")).json()
            self.assertEqual(len(saved["supplements"]), 2)

    async def test_combined_limits_in_both_directions(self):
        await self.submit()
        self.add_parent("fresh")
        db.execute("UPDATE investigations SET state='quota_waiting' WHERE id='fresh'")
        self.add_parent("another")
        async with self.client() as client:
            self.assertEqual((await client.post("/api/investigations/another/supplements", json={})).status_code, 429)
            self.assertEqual((await client.post("/api/incidents/alice/investigations", json={"engine": "rules"})).status_code, 429)

    async def test_priority_and_running_work_is_not_preempted(self):
        worker = Investigator(None)
        order, entered, release = [], asyncio.Event(), asyncio.Event()
        async def supplement(id):
            order.append(id)
            if id == "running":
                entered.set()
                await release.wait()
        async def investigation(id):
            order.append(id)
        worker.supplement, worker.investigate = supplement, investigation
        worker.enqueue_supplement("running")
        worker.start()
        await entered.wait()
        worker.enqueue_supplement("queued-supplement")
        worker.enqueue("fresh-investigation")
        release.set()
        await asyncio.wait_for(worker.queue.join(), 2)
        await worker.close()
        self.assertEqual(order, ["running", "fresh-investigation", "queued-supplement"])

    async def test_success_and_exact_supplied_ids(self):
        row, calls, waits = await self.run_supplement(lambda *args: self.response())
        self.assertEqual(row["state"], "completed", row["error"])
        self.assertEqual(json.loads(row["source_ids"]), ["one", "two"])
        self.assertEqual(json.loads(row["usage"])["requests"], 1)
        self.assertEqual(json.loads(row["usage"])["total_tokens"], 19)
        self.assertNotIn("PRIVATE", row["result"] + row["usage"])
        self.assertEqual(waits, [])

    async def test_429_wait_and_identical_retry(self):
        row, calls, waits = await self.run_supplement(lambda n, body: httpx.Response(429, headers={"Retry-After": "7"}) if n == 1 else self.response())
        self.assertEqual(row["state"], "completed", row["error"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(json.loads(row["usage"])["quota_wait_seconds"], 7)
        self.assertEqual(json.loads(waits[0]["source_ids"]), ["one", "two"])

    async def test_missing_headers_two_attempt_maximum(self):
        row, calls, _ = await self.run_supplement(lambda *args: httpx.Response(429))
        self.assertEqual(row["state"], "failed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(row["usage"])["quota_wait_seconds"], 60)

    async def test_temporary_server_retry_and_no_saved_evidence(self):
        row, calls, _ = await self.run_supplement(lambda n, body: httpx.Response(503, headers={"Retry-After": "2"}) if n == 1 else self.response())
        self.assertEqual(row["state"], "completed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(row["usage"])["quota_wait_seconds"], 2)
        self.add_parent("empty", ids=["missing"])
        with self.assertRaisesRegex(ValueError, "No saved source"):
            supplement_payload(self.parent("empty"), saved_evidence(self.parent("empty")))

    async def test_waiting_budget_and_execution_deadline_apply_to_supplements(self):
        clock = Clock()
        quota = GroqQuota(clock=clock, sleep=clock.sleep)
        usage = {"quota_wait_seconds": 115}
        with self.assertRaisesRegex(ValueError, "120-second"):
            await quota.wait(6, "Test", usage, lambda state: None, clock() + 300)
        usage = {"quota_wait_seconds": 0}
        with self.assertRaisesRegex(ValueError, "five-minute"):
            await quota.wait(10, "Test", usage, lambda state: None, clock() + 5)
        self.assertEqual(clock.waits, [])

    async def test_daily_exhaustion_and_full_allowance_fail_fast(self):
        row, calls, waits = await self.run_supplement(lambda *args: httpx.Response(429, headers={"Retry-After": "86400"}, json={"error": {"message": "TPD secret"}}))
        self.assertEqual(row["state"], "failed")
        self.assertIn("120-second", row["error"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(waits, [])
        await self.submit(body={"retry": True})
        row, calls, waits = await self.run_supplement(lambda *args: httpx.Response(429, json={"error": {"message": "TPM Limit 100 Requested 5000"}}))
        self.assertEqual(row["state"], "failed")
        self.assertEqual(len(calls), 1)
        self.assertIn("cannot fit", row["error"])

    async def test_invalid_or_truncated_output_is_not_retried(self):
        invalid = deepcopy(RESULT)
        invalid["explanations"] = [{"claim": "Unsupported", "status": "supported", "supporting_citations": ["not-supplied"], "conflicting_citations": []}]
        for value, finish in ((invalid, "stop"), (RESULT, "length")):
            with self.subTest(finish=finish):
                await self.submit(body={"retry": True})
                row, calls, _ = await self.run_supplement(lambda *args: self.response(value, finish))
                self.assertEqual(row["state"], "failed")
                self.assertIsNone(row["result"])
                self.assertEqual(len(calls), 1)

    async def test_schema_integrity_and_optional_alternatives(self):
        self.assertEqual(validate_supplement(RESULT, {"one"}), RESULT)
        for change in ({"confidence": 99}, {"relationship": "confirmed"}, {"questions": RESULT["questions"] * 4}, {"summary": ""}):
            with self.assertRaises(ValueError):
                validate_supplement({**RESULT, **change}, {"one"})
        supported = {**RESULT, "relationship": "supports", "explanations": [{"claim": "Observed", "status": "supported", "supporting_citations": ["one"], "conflicting_citations": []}]}
        validate_supplement(supported, {"one"})
        with self.assertRaises(ValueError):
            validate_supplement(supported, {"two"})

    async def test_compaction_keeps_cited_trace_and_rejects_oversize_minimum(self):
        self.add_source("timeout", service="order", trace_id="trace", span_id="caller", downstream="payment", timeout_ms=2000)
        self.add_source("parent-span", kind="span", trace_id="trace", span_id="child", parent_id="caller", name="payment.checkout", duration_ms=4000)
        self.add_source("late", kind="span", trace_id="trace", span_id="work", parent_id="child", name="payment.process", duration_ms=4000, status="ok", started_at="2026-11-02T00:00:00Z")
        ids = ["timeout", "parent-span", "late"]
        for i in range(50):
            self.add_source(f"noise-{i}", message="noise" * 100)
            ids.append(f"noise-{i}")
        self.add_parent("trace", ids=ids, report={**REPORT, "evidence_ids": ["late", "timeout"]})
        parent = self.parent("trace")
        scope = saved_evidence(parent)
        payload, supplied = supplement_payload(parent, scope)
        self.assertTrue({"timeout", "parent-span", "late"} <= supplied)
        self.assertLess(len(supplied), len(ids))
        data = json.loads(payload["messages"][1]["content"])
        self.assertTrue(data["measured_timing_comparisons"])
        self.assertLessEqual(len(encoded(payload)), INPUT_BYTES)
        self.assertEqual(set(payload["response_format"]["json_schema"]["schema"]["$defs"]["source"]["enum"]), supplied)
        with self.assertRaisesRegex(ValueError, "minimum useful"):
            supplement_payload(parent, scope, budget=100)

    async def test_restart_additive_migration_retains_results_and_interrupts_all_unfinished(self):
        before = self.parent()
        for state in ("queued", "running", "quota_waiting", "completed", "failed"):
            self.add_parent(state)
            db.execute("INSERT INTO supplements(id,investigation_id,user_id,state,created_at,source_ids,usage) VALUES(?,?,?,?,?,?,?)",
                       (state, state, "alice", state, db.now(), '["one"]', '{"requests":1}'))
        db.init()
        db.init()
        self.assertEqual(self.parent(), before)
        self.assertEqual(db.one("SELECT MAX(version) version FROM schema_version")["version"], 5)
        for state in ("queued", "running", "quota_waiting", "completed", "failed"):
            row = db.one("SELECT * FROM supplements WHERE id=?", (state,))
            self.assertEqual(row["state"], "interrupted" if state in ("queued", "running", "quota_waiting") else state)
            self.assertEqual(json.loads(row["source_ids"]), ["one"])
            self.assertEqual(json.loads(row["usage"])["requests"], 1)
