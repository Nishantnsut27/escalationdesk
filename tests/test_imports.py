import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from starlette.exceptions import HTTPException
from backend import auth, db
from backend.api import create_app
from backend.evidence import EvidenceTools, EvidenceContext, INPUT_BYTES, encoded
from backend.imports import parse_logs, create_collection
from backend.investigator import Investigator, validate_report
from backend.quota import GroqQuota
from backend.supplements import saved_evidence, supplement_payload
from test_provider_contract import Clock, REPORT


def lines(*records):
    return "\n".join(json.dumps(record) for record in records)


class ParserTests(unittest.TestCase):
    def test_multiline_text_and_json_keep_source_identity_and_real_times(self):
        rows, summary = parse_logs(
            '2026-11-03T10:00:00+05:30 ERROR Request failed\n'
            'Traceback (most recent call last):\n'
            '  File "app.py", line 12\n'
            'TypeError: missing value\n'
            '{"id":"one","service":"catalog","timestamp":"2026-11-03T04:29:00Z","message":"Recovered","level":"info"}\n'
            'Plain untimed message')
        self.assertEqual(len(rows), 3)
        self.assertIn("TypeError", rows[0]["message"])
        self.assertEqual(rows[0]["timestamp"], "2026-11-03T04:30:00+00:00")
        self.assertEqual(rows[1]["source_line"], 5)
        self.assertEqual(rows[1]["service"], "catalog")
        self.assertIsNone(rows[2]["timestamp"])
        self.assertEqual(summary["missing_timestamps"], 1)

    def test_credentials_are_redacted_before_original_and_normalized_storage(self):
        raw = lines({"service": "checkout", "message": "api_key=topsecret", "headers": {"Authorization": "Bearer veryprivate"},
                     "password": "pass-secret", "request": {"cookie": "session=private"}})
        raw += "\nCookie: sid=privatecookie; other=alsoprivate\nhttps://user:privatepass@example.com/path\n"
        raw += "-----BEGIN PRIVATE KEY-----\nprivate-key-bytes\n-----END PRIVATE KEY-----\nERROR after key"
        rows, summary = parse_logs(raw)
        saved = json.dumps(rows)
        for secret in ("topsecret", "veryprivate", "pass-secret", "privatecookie", "alsoprivate", "privatepass", "private-key-bytes"):
            self.assertNotIn(secret, saved)
        self.assertGreater(summary["redactions"], 0)
        self.assertIn("ERROR after key", saved)

    def test_dedup_only_identical_source_ids_and_preserve_conflicts_and_repeated_lines(self):
        first = {"id": "a", "message": "failed", "service": "auth"}
        conflict = {**first, "message": "recovered"}
        rows, summary = parse_logs(lines(first, first, conflict) + "\nhello\nhello")
        self.assertEqual(len(rows), 4)
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(summary["conflicting_ids"], 1)
        self.assertEqual(len({row["id"] for row in rows}), 4)

    def test_malformed_fields_and_nonfinite_input_stay_visible(self):
        rows, summary = parse_logs(lines({"kind": [], "service": [], "duration_ms": "slow", "timestamp": "2026-11-03"})
                                   + '\n{"message":broken}\n{"duration_ms":NaN}')
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary["malformed_records"], 2)
        self.assertGreater(summary["invalid_fields"], 0)
        self.assertTrue(all(row["timestamp"] is None for row in rows))
        self.assertNotIn("duration_ms", rows[0])

    def test_import_bounds_fail_without_truncation(self):
        for content in ("", "x\0y", "x" * (1024 * 1024 + 1), "x" * 32769, "hello\n" * 5001,
                        lines(*[{"service": f"service{i}", "message": "event"} for i in range(33)])):
            with self.subTest(length=len(content)), self.assertRaises(HTTPException):
                parse_logs(content)
        rows, summary = parse_logs(lines({"timestamp": 1793700000000, "message": "epoch"}))
        self.assertIsNotNone(rows[0]["timestamp"])


class ImportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        storage = patch.object(db, "DATA", Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        db.init()
        for owner in ("alice", "bob"):
            db.execute("INSERT INTO users VALUES(?,?,?,?,?)", (owner, owner, owner, "hash", db.now()))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (auth.digest(owner), owner, time.time() + 600))
        self.enqueued = []
        self.app = create_app()
        self.app.state.investigator = SimpleNamespace(enqueue=self.enqueued.append, enqueue_supplement=self.enqueued.append)
        config = patch("backend.api.GROQ_KEY", "fixture-key")
        config.start()
        self.addCleanup(config.stop)

    def client(self, owner="alice", csrf=True):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test",
            cookies={auth.COOKIE: owner}, headers={"X-CSRF-Token": auth.digest(owner + ":csrf")} if csrf else {})

    def collection(self, content=None, owner="alice", **extra):
        id, _ = create_collection(owner, {"content": content or lines(
            {"service": "billing", "timestamp": "2026-11-03T00:00:02Z", "level": "error", "message": "TypeError in handler"},
            {"service": "catalog", "timestamp": "2026-11-03T00:00:01Z", "level": "info", "message": "Request succeeded"}),
            **extra})
        return id

    async def test_import_api_is_idempotent_and_never_creates_incidents_or_analysis(self):
        async with self.client() as client:
            body = {"content": 'ERROR cannot process request', "filename": "site.log", "title": "Site logs"}
            first, second = await asyncio.gather(client.post("/api/imports", json=body), client.post("/api/imports", json=body))
            self.assertEqual({first.status_code, second.status_code}, {200, 201})
            self.assertEqual(first.json()["id"], second.json()["id"])
            listing = (await client.get("/api/collections")).json()
            self.assertEqual(len(listing), 1)
            self.assertNotIn("user_id", listing[0])
            self.assertNotIn("fingerprint", listing[0])
        self.assertEqual(self.enqueued, [])
        for table in ("runs", "incidents", "investigations"):
            self.assertEqual(db.one(f"SELECT count(*) n FROM {table}")["n"], 0)

    async def test_account_csrf_input_and_binary_boundaries(self):
        id = self.collection()
        async with self.client("bob") as other, self.client(csrf=False) as unsafe, self.client("unknown") as anonymous:
            self.assertEqual((await other.get("/api/collections")).json(), [])
            for path in (f"/api/collections/{id}", f"/api/collections/{id}/evidence"):
                self.assertEqual((await other.get(path)).status_code, 404)
                self.assertEqual((await anonymous.get(path)).status_code, 401)
            self.assertEqual((await other.post(f"/api/collections/{id}/investigations", json={})).status_code, 404)
            self.assertEqual((await unsafe.post("/api/imports", json={"content": "log"})).status_code, 403)
            self.assertEqual((await unsafe.post(f"/api/collections/{id}/investigations", json={})).status_code, 403)
        async with self.client() as client:
            for data in ({"content": "log", "owner": "bob"}, {"content": "log", "filename": "../bad.log"},
                         {"content": "log", "filename": "binary.exe"}, {"content": 1}, {"content": "log", "symptom": "x"*2001}):
                self.assertEqual((await client.post("/api/imports", json=data)).status_code, 400)
            self.assertEqual((await client.post("/api/imports", content=b"x", headers={"Content-Type": "text/plain"})).status_code, 415)
            self.assertEqual((await client.post("/api/imports", json={"content": "x"*(1024*1024+1)})).status_code, 413)

    async def test_pagination_filters_timestamp_order_and_protected_sources(self):
        id = self.collection(lines(*[{"service": "auth", "message": f"record-{i}", "id": str(i),
            "timestamp": f"2026-11-03T00:{i//60:02}:{i%60:02}Z", "level": "error" if i == 250 else "info"} for i in reversed(range(260))])
            + "\nuntimed record")
        async with self.client() as client, self.client("bob") as other:
            first = (await client.get(f"/api/collections/{id}/evidence?limit=200")).json()
            self.assertEqual(len(first["evidence"]), 200)
            self.assertEqual(first["evidence"][0]["message"], "record-0")
            self.assertEqual(first["next_offset"], 200)
            second = (await client.get(f"/api/collections/{id}/evidence?offset=200&limit=200")).json()
            self.assertEqual(len(second["evidence"]), 61)
            self.assertIsNone(second["evidence"][-1]["timestamp"])
            filtered = (await client.get(f"/api/collections/{id}/evidence?level=error&query=record-250")).json()["evidence"]
            self.assertEqual(len(filtered), 1)
            source = filtered[0]["id"]
            self.assertEqual((await other.get("/api/evidence/" + source)).status_code, 404)
            self.assertEqual((await client.get(f"/api/collections/{id}/evidence?service=payment")).status_code, 400)
            self.assertEqual((await client.get(f"/api/collections/{id}/evidence?offset=-1")).status_code, 400)
        tools = EvidenceTools(None, "alice", collection_id=id, groq=True)
        rows = tools.call("search_logs", {"service": "auth", "query": "record-250", "limit": 1})["records"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("original", rows[0])
        self.assertEqual(tools.call("get_topology", {})["configured_edges"], [])
        self.assertEqual(tools.call("get_metrics", {})["records"], [])

    async def test_rules_saved_result_and_restart_do_not_assume_demo_mechanism(self):
        id = self.collection()
        async with self.client() as client:
            job = (await client.post(f"/api/collections/{id}/investigations", json={"engine": "rules"})).json()["id"]
            self.assertEqual(len(self.enqueued), 1)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("Unexpected provider request"))) as provider:
                await Investigator(provider).investigate(job)
            result = (await client.get("/api/investigations/" + job)).json()
            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["report"]["origin"], "unknown")
            self.assertEqual(result["report"]["assessment"], "inconclusive")
            self.assertNotIn("payment", json.dumps(result["report"]).lower())
            scope = (await client.get(f"/api/investigations/{job}/evidence")).json()
            self.assertEqual(len(scope["evidence"]), 2)
            before = db.one("SELECT * FROM investigations WHERE id=?", (job,))
            db.init()
            self.assertEqual(before, db.one("SELECT * FROM investigations WHERE id=?", (job,)))
            self.assertEqual(len((await client.get(f"/api/collections/{id}")).json()["investigations"]), 1)

    async def test_combined_job_limit_duplicates_and_retained_retry(self):
        ids = [self.collection(title=f"set {n}") for n in range(3)]
        async with self.client() as client:
            one, duplicate = await asyncio.gather(*[client.post(f"/api/collections/{ids[0]}/investigations", json={}) for _ in range(2)])
            self.assertEqual(one.json()["id"], duplicate.json()["id"])
            await client.post(f"/api/collections/{ids[1]}/investigations", json={})
            self.assertEqual((await client.post(f"/api/collections/{ids[2]}/investigations", json={})).status_code, 429)
            db.execute("UPDATE investigations SET state='failed' WHERE collection_id=?", (ids[1],))
            first_id = one.json()["id"]
            db.execute("UPDATE investigations SET state='completed', engine='groq', report=? WHERE id=?", (json.dumps(REPORT), first_id))
            db.execute("INSERT INTO supplements(id,investigation_id,user_id,state,created_at) VALUES(?,?,?,?,?)", ("supp", first_id, "alice", "queued", db.now()))
            retry = (await client.post(f"/api/collections/{ids[1]}/investigations", json={})).json()["id"]
            self.assertEqual((await client.post(f"/api/collections/{ids[2]}/investigations", json={})).status_code, 429)
            db.init()
            self.assertEqual(db.one("SELECT state FROM investigations WHERE id=?", (retry,))["state"], "interrupted")
            self.assertEqual(db.one("SELECT count(*) n FROM investigations WHERE collection_id=?", (ids[1],))["n"], 2)

    async def test_groq_uses_import_services_bounded_queries_and_only_supplied_citations(self):
        id = self.collection(symptom="Intermittent errors; investigate.")
        calls = []
        def provider_response(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertLessEqual(len(request.content), INPUT_BYTES)
            if "tools" in body:
                props = body["tools"][0]["function"]["parameters"]["properties"]
                self.assertEqual(props["service"]["enum"], ["billing", "catalog"])
                self.assertIn("query", props)
                return httpx.Response(200, json={"choices": [{"message": {"content": "READY"}}], "usage": {"total_tokens": 10}})
            schema = body["response_format"]["json_schema"]["schema"]
            self.assertEqual(schema["properties"]["origin"]["enum"], ["billing", "catalog", "unknown"])
            refs = schema["properties"]["evidence_ids"]["items"]["enum"]
            result = {**deepcopy(REPORT), "origin": "billing", "affected_services": ["billing"], "evidence_ids": [refs[0]]}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}], "usage": {"total_tokens": 10}})
        async with self.client() as client:
            job = (await client.post(f"/api/collections/{id}/investigations", json={"engine": "groq"})).json()["id"]
            clock = Clock()
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider_response)) as provider:
                with patch("backend.investigator.GROQ_KEY", "fixture-key"):
                    await Investigator(provider, GroqQuota(clock=clock, sleep=clock.sleep)).investigate(job)
            result = (await client.get("/api/investigations/" + job)).json()
            self.assertEqual(result["state"], "completed", result["error"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["usage"]["requests"], 2)
            parent = db.one("SELECT * FROM investigations WHERE id=?", (job,))
            payload, supplied = supplement_payload(parent, saved_evidence(parent))
            self.assertLessEqual(len(encoded(payload)), INPUT_BYTES)
            self.assertTrue(supplied <= set(result["usage"]["supplied_evidence_ids"]))
            other_id = self.collection(owner="bob")
            wrong = db.evidence(None, "bob", collection_id=other_id)[0]["id"]
            db.execute("UPDATE investigations SET usage=? WHERE id=?", (json.dumps({"supplied_evidence_ids": [wrong]}), job))
            self.assertEqual(saved_evidence(db.one("SELECT * FROM investigations WHERE id=?", (job,)))["unavailable_source_ids"], [wrong])

    async def test_large_observation_fails_before_provider_and_invalid_calls_cannot_expand_scope(self):
        id = self.collection(lines({"service": "inventory", "message": "x"*12000}))
        tools = EvidenceTools(None, "alice", collection_id=id, groq=True)
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as provider:
            with patch("backend.investigator.GROQ_KEY", "fixture-key"), self.assertRaisesRegex(ValueError, "No useful"):
                await Investigator(provider).groq(tools, lambda *args: None)
        self.assertEqual(calls, [])
        for name, args in (("run_shell", {"cmd": "anything"}), ("search_logs", {"owner": "bob"}),
                           ("search_logs", {"service": "payment"}), ("search_logs", {"offset": True})):
            with self.assertRaises(ValueError):
                tools.call(name, args)
        invalid = deepcopy(REPORT)
        invalid["origin"] = "payment"
        with self.assertRaises(ValueError):
            validate_report(invalid, tools)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        storage = patch.object(db, "DATA", Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        with patch("backend.db.migrate_collections"):
            db.init()
        db.execute("INSERT INTO users VALUES(?,?,?,?,?)", ("u", "User", "user", "hash", db.now()))
        db.execute("INSERT INTO runs(id,user_id,scenario,state,created_at) VALUES(?,?,?,?,?)", ("run", "u", "pool", "completed", db.now()))
        db.execute("INSERT INTO incidents VALUES(?,?,?,?,?,?,?)", ("incident", "run", "u", "Existing", "resolved", db.now(), db.now()))
        db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?)", ("source", "run", "u", "log", "payment", db.now(), '{"message":"Existing"}'))
        db.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at,report,usage) VALUES(?,?,?,?,?,?,?,?)",
                   ("report", "incident", "u", "groq", "completed", db.now(), json.dumps(REPORT), '{"supplied_evidence_ids":["source"]}'))
        db.execute("INSERT INTO supplements(id,investigation_id,user_id,state,created_at,result) VALUES(?,?,?,?,?,?)",
                   ("supplement", "report", "u", "completed", db.now(), '{"summary":"Existing supplement"}'))

    def test_version_two_upgrade_retains_every_field_and_foreign_key(self):
        snapshot = {table: db.all_rows(f"SELECT * FROM {table}") for table in ("users", "runs", "incidents", "evidence", "investigations", "supplements")}
        db.init()
        db.init()
        for table, old in snapshot.items():
            new = db.all_rows(f"SELECT * FROM {table}")
            self.assertEqual([{key: row[key] for key in old[0]} for row in new], old)
        with db.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(saved_evidence(db.one("SELECT * FROM investigations WHERE id='report'"))["evidence"][0]["id"], "source")
        self.assertEqual(db.one("SELECT MAX(version) n FROM schema_version")["n"], 5)

    def test_migration_failure_rolls_back_schema_and_records(self):
        from backend.migrations import migrate_collections
        class FailingConnection:
            def __init__(self, conn): self.conn = conn
            def execute(self, sql, *args):
                if sql.startswith("ALTER TABLE evidence_v3"):
                    raise sqlite3.OperationalError("Fixture interruption")
                return self.conn.execute(sql, *args)
            def commit(self): self.conn.commit()
            def rollback(self): self.conn.rollback()
        with db.connection() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                migrate_collections(FailingConnection(conn))
            self.assertEqual(conn.execute("SELECT id FROM evidence").fetchone()[0], "source")
            self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        db.init()
        self.assertEqual(db.one("SELECT id FROM supplements")["id"], "supplement")
