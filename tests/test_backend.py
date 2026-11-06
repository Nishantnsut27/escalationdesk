import asyncio
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import unittest
import uuid
from contextlib import closing

ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = ROOT / ".cache" / ("test-" + uuid.uuid4().hex[:10])
TEST_DATA.mkdir(parents=True)
os.environ["DESK_DATA_DIR"] = str(TEST_DATA)
os.environ["DESK_PORT"] = "8765"
os.environ["GROQ_API_KEY"] = ""
os.environ["DESK_PUBLIC_URL"] = ""  # This isolated runtime uses loopback HTTP, not the operator's HTTPS tunnel.

import httpx
from backend.investigator import EvidenceTools, rule_report, validate_report
from backend import db

class LocalProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log = (TEST_DATA / "launcher.log").open("w")
        cls.server = subprocess.Popen([sys.executable, "-m", "backend.launcher"], cwd=ROOT,
            env={**os.environ, "DESK_ENABLE_LAB": "1"}, stdin=subprocess.PIPE, stdout=cls.log, stderr=subprocess.STDOUT,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        cls.addClassCleanup(cls.cleanup_runtime)
        cls.base = "http://127.0.0.1:8765"
        cls.a = httpx.Client(base_url=cls.base, trust_env=False, timeout=20)
        cls.b = httpx.Client(base_url=cls.base, trust_env=False, timeout=20)
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError((TEST_DATA / "launcher.log").read_text())
            try:
                if cls.a.get("/api/health").status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(.2)
        else:
            raise RuntimeError("Test runtime did not start")
        cls.password = "Local-test-password-782!"
        for client, name in ((cls.a, "alice"), (cls.b, "bob")):
            response = client.post("/api/auth/signup", json={
                "name": name.title(), "email": name + "@example.test", "password": cls.password})
            if response.status_code != 200:
                raise RuntimeError(response.text)
            session = response.json()
            client.headers["X-CSRF-Token"] = session["csrf"]
        cls.owner_a = cls.a.get("/api/auth/me").json()["user"]["id"]
        cls.owner_b = cls.b.get("/api/auth/me").json()["user"]["id"]

    @classmethod
    def cleanup_runtime(cls):
        if hasattr(cls, "a"):
            cls.a.close()
            cls.b.close()
        if cls.server.poll() is None:
            cls.server.stdin.write("stop\n")
            cls.server.stdin.flush()
            cls.server.wait(timeout=20)
        try:
            cls.server.stdin.close()
        except OSError:
            pass
        cls.log.close()
        with httpx.Client(trust_env=False, timeout=.5) as client:
            for port in range(8765, 8769):
                try:
                    client.get(f"http://127.0.0.1:{port}/health")
                except httpx.RequestError:
                    continue
                raise AssertionError(f"Orphan service remains on port {port}")

    def wait_run(self, run_id):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            result = self.a.get("/api/runs/" + run_id).json()
            if result["run"]["state"] not in {"starting", "running", "resetting"}:
                self.assertEqual(result["run"]["state"], "completed", result)
                return result
            time.sleep(.25)
        self.fail("Scenario did not finish")

    def run_scenario(self, scenario):
        response = self.a.post(f"/api/scenarios/{scenario}/start", json={})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["id"]

    def investigate(self, incident_id):
        response = self.a.post(f"/api/incidents/{incident_id}/investigations", json={"engine": "rules"})
        self.assertEqual(response.status_code, 202, response.text)
        investigation_id = response.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = self.a.get("/api/investigations/" + investigation_id).json()
            if row["state"] in {"completed", "failed"}:
                self.assertEqual(row["state"], "completed", row)
                return row
            time.sleep(.1)
        self.fail("Investigation did not complete")

    def test_01_authentication_and_request_protection(self):
        with httpx.Client(base_url=self.base, trust_env=False) as anonymous:
            self.assertEqual(anonymous.get("/api/incidents").status_code, 401)
            self.assertEqual(anonymous.post("/api/auth/login", json={
                "email": "alice@example.test", "password": "incorrect"}).status_code, 401)
        self.assertEqual(self.a.post("/api/scenarios/pool/start", json={}, headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        self.assertEqual(self.a.post("/api/scenarios/pool/start", json={}, headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.a.post("/api/scenarios/unsupported/start", json={}).status_code, 400)
        self.assertEqual(self.a.get("/api/auth/me").headers["Cache-Control"], "no-store")
        with closing(sqlite3.connect(TEST_DATA / "platform.sqlite3")) as conn:
            stored = conn.execute("SELECT password_hash FROM users WHERE email='alice@example.test'").fetchone()[0]
            self.assertTrue(stored.startswith("$argon2id$"))
            self.assertNotIn(self.password, stored)
            digests = {row[0] for row in conn.execute("SELECT digest FROM sessions")}
            self.assertNotIn(self.a.cookies.get("desk_session"), digests)

    def test_02_pool_failure_real_evidence_and_isolation(self):
        run_id = self.run_scenario("pool")
        self.assertEqual(self.b.post("/api/scenarios/slow/start", json={}).status_code, 409)
        self.assertEqual(self.b.post("/api/scenarios/reset", json={}).status_code, 404)
        self.assertEqual(self.b.get("/api/runs/" + run_id).status_code, 404)
        result = self.wait_run(run_id)
        self.assertEqual(result["run"]["requests"], 3)
        self.assertEqual(result["run"]["failures"], 3)
        incidents = self.a.get("/api/incidents").json()
        matched = [item for item in incidents if item["run_id"] == run_id]
        self.assertEqual(len(matched), 1)
        incident = matched[0]
        self.assertEqual(incident["state"], "resolved")
        evidence = result["evidence"]
        services = {row["service"] for row in evidence}
        self.assertEqual(services, {"gateway", "order", "payment"})
        self.assertTrue(any(row.get("pool_in_use") == 2 for row in evidence))
        acquisition = [row for row in evidence if row.get("name") == "db.acquire" and row.get("status") == "error"]
        self.assertEqual(len(acquisition), 3)
        self.assertTrue(all(row["duration_ms"] >= 900 for row in acquisition))
        trace_services = {}
        spans = [row for row in evidence if row["kind"] == "span"]
        for span in spans:
            trace_services.setdefault(span["trace_id"], set()).add(span["service"])
        self.assertTrue(any(services == {"gateway", "order", "payment"} for services in trace_services.values()))
        span_ids = {row["span_id"] for row in spans}
        self.assertTrue(all(row["parent_id"] is None or row["parent_id"] in span_ids for row in spans))
        self.assertEqual(self.b.get("/api/incidents").json(), [])
        self.assertEqual(self.b.get("/api/evidence/" + evidence[0]["id"]).status_code, 404)
        self.assertEqual(self.b.get("/api/incidents/" + incident["id"]).status_code, 404)
        report = self.investigate(incident["id"])
        self.assertEqual(report["report"]["assessment"], "supported")
        self.assertEqual(report["report"]["origin"], "payment")
        self.assertIn("pool", report["report"]["summary"].lower())
        self.assertEqual(report["usage"]["requests"], 0)
        for ref in report["report"]["evidence_ids"]:
            self.assertEqual(self.a.get("/api/evidence/" + ref).status_code, 200)
        self.assertEqual(self.b.get("/api/investigations/" + report["id"]).status_code, 404)
        self.__class__.pool_run = run_id
        self.__class__.pool_incident = incident["id"]

    def test_03_slow_processing_timeout_and_recovery(self):
        result = self.wait_run(self.run_scenario("slow"))
        self.assertEqual(result["run"]["failures"], 3)
        processing = [row for row in result["evidence"] if row.get("name") == "payment.process"]
        self.assertTrue(any(row["duration_ms"] >= 3900 for row in processing))
        timeouts = [row for row in result["evidence"] if row.get("message") == "Downstream request timed out"]
        self.assertEqual(len(timeouts), 3)
        self.assertTrue(all(row["service"] == "order" for row in timeouts))
        incident = next(item for item in self.a.get("/api/incidents").json() if item["run_id"] == result["run"]["id"])
        report = self.investigate(incident["id"])
        self.assertEqual(report["report"]["assessment"], "supported")
        self.assertIn("timeout", report["report"]["summary"])
        self.assertEqual(report["report"]["origin"], "payment")
        self.assertTrue(any(row.get("service") == "gateway" and row.get("http_status") == 200 for row in result["evidence"]))
        with closing(sqlite3.connect(TEST_DATA / "payment.sqlite3")) as conn:
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)
        with closing(sqlite3.connect(TEST_DATA / "platform.sqlite3")) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='payments'").fetchone())

    def test_04_healthy_traffic_no_false_incident(self):
        before = len(self.a.get("/api/incidents").json())
        result = self.wait_run(self.run_scenario("healthy"))
        self.assertEqual(result["run"]["failures"], 0)
        self.assertEqual(result["run"]["requests"], 3)
        self.assertEqual(len(self.a.get("/api/incidents").json()), before)
        tools = EvidenceTools(result["run"]["id"], self.owner_a)
        report = rule_report(tools)
        self.assertEqual(report["assessment"], "inconclusive")
        validate_report(report, tools)

    def test_05_query_bounds_missing_evidence_and_fabricated_references(self):
        tools = EvidenceTools(self.pool_run, self.owner_a)
        with self.assertRaises(ValueError):
            tools.call("execute_sql", {"query": "SELECT * FROM users"})
        with self.assertRaises(ValueError):
            tools.call("search_logs", {"service": "unknown"})
        with self.assertRaises(ValueError):
            tools.call("search_logs", {"limit": 9999})
        other_tools = EvidenceTools(self.pool_run, self.owner_b)
        self.assertEqual(other_tools.call("search_logs", {}), [])
        report = rule_report(tools)
        altered = copy.deepcopy(report)
        altered["evidence_ids"].append("fabricated")
        with self.assertRaises(ValueError):
            validate_report(altered, tools)
        empty_tools = EvidenceTools("missing-run", self.owner_a)
        inconclusive = rule_report(empty_tools)
        self.assertEqual(inconclusive["assessment"], "inconclusive")
        validate_report(inconclusive, empty_tools)

    def test_06_unconfigured_provider_does_not_fake_result(self):
        response = self.a.post(f"/api/incidents/{self.pool_incident}/investigations", json={"engine": "groq"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("not configured", response.json()["error"])
        self.assertFalse(self.a.get("/api/overview").json()["providers"]["groq"])

    def test_07_logout_expiry_and_persisted_login(self):
        previous = self.b.cookies.get("desk_session")
        self.assertEqual(self.b.post("/api/auth/logout", json={}).status_code, 200)
        self.assertEqual(self.b.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.b.get("/api/auth/me", headers={"Cookie": "desk_session=" + previous}).status_code, 401)
        response = self.b.post("/api/auth/login", json={"email": "bob@example.test", "password": self.password})
        self.assertEqual(response.status_code, 200)
        self.b.headers["X-CSRF-Token"] = response.json()["csrf"]
        db.execute("UPDATE sessions SET expires=0 WHERE user_id=?", (self.owner_b,))
        self.assertEqual(self.b.get("/api/auth/me").status_code, 401)

    def test_08_throttled_login_attempts(self):
        with httpx.Client(base_url=self.base, trust_env=False) as anonymous:
            statuses = [anonymous.post("/api/auth/login", json={"email": "nobody@example.test", "password": "wrong"}).status_code for _ in range(13)]
        self.assertIn(429, statuses)

    def test_09_persistence_after_restart(self):
        self.server.stdin.write("stop\n")
        self.server.stdin.flush()
        self.server.wait(timeout=20)
        self.server.stdin.close()
        self.__class__.server = subprocess.Popen([sys.executable, "-m", "backend.launcher"], cwd=ROOT,
            env=os.environ.copy(), stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if self.a.get("/api/health").status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(.2)
        self.assertEqual(self.a.get("/api/auth/me").status_code, 200)
        detail = self.a.get("/api/incidents/" + self.pool_incident)
        self.assertEqual(detail.status_code, 200)
        report = detail.json()["investigations"][0]["report"]
        self.assertEqual(report["assessment"], "supported")
        for ref in report["evidence_ids"]:
            self.assertEqual(self.a.get("/api/evidence/" + ref).status_code, 200)

if __name__ == "__main__":
    unittest.main(verbosity=2)

