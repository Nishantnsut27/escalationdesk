from database import IsolatedDatabase, verify_users
"""Offline authorization, bundle, migration and live-arrival regression tests."""
import json
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from backend import auth, db, demo
from backend.api import create_app
from backend.catalog import CATALOG
from backend.lab import summary, store_rows
from scripts.prepare_demo import export_bundle


def fixture_bundle():
    cases = []
    for meta in CATALOG:
        slug = meta["id"]
        rows = [{"id": f"{slug}-{i}", "kind": "log", "service": "fixture", "timestamp": "2026-01-01T00:00:00+00:00", "message": f"Synthetic test observation {i}", "level": "error"} for i in range(500)]
        investigation = {"id": slug + "-report", "engine": "groq", "state": "completed", "created_at": rows[0]["timestamp"], "completed_at": rows[0]["timestamp"],
            "report": {"assessment": "inconclusive", "origin": "unknown", "summary": "Synthetic fixture; never a published recording.", "affected_services": ["fixture"], "evidence_ids": [rows[0]["id"]],
                       "hypotheses": [], "recommendations": ["Inspect evidence"], "limitations": ["Test fixture"]},
            "activity": [], "usage": {"model": "mock-provider-fixture", "requests": 0, "supplied_evidence_ids": [rows[0]["id"]]}}
        cases.append({"slug": slug, **{k: meta[k] for k in ("title", "description", "category")}, "evidence": rows, "evidence_digest": demo.digest(rows), "investigation": investigation,
            "collection": {"id": slug + "-collection", "created_at": rows[0]["timestamp"], "symptom": "Fixture", "summary": summary(rows)},
            "review": {"accepted": True, "reviewed_at": rows[0]["timestamp"], "notes": "Synthetic fixtures exercise installation validation only.", "evidence_digest": demo.digest(rows), "report_digest": demo.digest(investigation)}})
    return {"version": "v1", "cases": cases}


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.object(auth, "PUBLIC_URL", ""))
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / ".cache")
        self.addCleanup(self.directory.cleanup)
        self.storage = IsolatedDatabase(Path(self.directory.name))
        self.storage.start()
        self.addCleanup(self.storage.stop)
        db.init()
        self.bundle = fixture_bundle()
        self.path = Path(self.directory.name) / "bundle.json"
        self.path.write_text(json.dumps(self.bundle), encoding="utf-8")
        demo.install(self.path)
        db.execute("INSERT INTO users VALUES(%s,%s,%s,%s,%s)", ("regular", "Regular", "regular@example.test", "unused", db.now()))
        verify_users()
        self.app = create_app()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        auth.attempts.clear()

    async def login(self):
        result = await self.client.post("/api/auth/demo-login", json={"password": "Demo@login"})
        self.assertEqual(result.status_code, 200, result.text)
        session = result.json()
        self.client.headers["x-csrf-token"] = session["csrf"]
        self.assertEqual(session["user"]["role"], "demo")
        self.assertTrue(session["user"]["read_only"])
        return session

    async def test_read_only_session_browsing_and_all_writes(self):
        # No controller/investigator is installed in app.state. Any attempted scheduling fails this test.
        await self.login()
        for endpoint in ("/api/auth/me", "/api/demo/cases", "/api/collections/pool-collection", "/api/collections/pool-collection/evidence?offset=400", "/api/investigations/pool-report", "/api/investigations/pool-report/evidence", "/api/evidence/pool-499"):
            self.assertEqual((await self.client.get(endpoint)).status_code, 200, endpoint)
        for endpoint in ("/api/imports", "/api/collections/pool-collection/investigations", "/api/incidents/anything/investigations", "/api/investigations/pool-report/supplements", "/api/scenarios/rollout/start", "/api/scenarios/reset", "/api/auth/signup", "/api/future-connections"):
            for method in ("POST", "PUT", "DELETE"):
                self.assertEqual((await self.client.request(method, endpoint, json={})).status_code, 403, endpoint)
        self.assertEqual((await self.client.get("/api/runs")).status_code, 403)
        self.assertEqual((await self.client.post("/api/auth/logout", json={})).status_code, 200)
        self.assertEqual((await self.client.get("/api/demo/cases")).status_code, 401)

    async def test_concurrent_sessions_expiry_and_account_isolation(self):
        await self.login()
        other = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(other.aclose)
        self.assertEqual((await other.post("/api/auth/demo-login", json={"password": "wrong"})).status_code, 401)
        self.assertEqual((await other.post("/api/auth/demo-login", json={"password": "Demo@login"})).status_code, 200)
        await self.client.post("/api/auth/logout", json={})
        self.assertEqual((await other.get("/api/auth/me")).status_code, 200)
        db.execute("INSERT INTO sessions VALUES(%s,%s,%s)", (auth.digest("regular-token"), "regular", time.time() + 60))
        self.client.cookies.set(auth.COOKIE, "regular-token")
        self.assertEqual((await self.client.get("/api/evidence/pool-0")).status_code, 404)
        self.assertEqual((await self.client.get("/api/demo/cases")).status_code, 403)
        db.execute("UPDATE sessions SET expires=0")
        self.assertEqual((await other.get("/api/evidence/pool-0")).status_code, 401)

    async def test_unpublished_demo_owned_sources_are_not_visible(self):
        await self.login()
        db.execute("INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            ("unpublished", demo.DEMO_ID, "Hidden", "", "paste", db.now(), "hidden", json.dumps(summary([]))))
        store_rows("unpublished", demo.DEMO_ID, [{"id": "hidden-source", "service": "fixture", "kind": "log", "timestamp": db.now(), "message": "Hidden"}])
        self.assertEqual((await self.client.get("/api/collections/unpublished")).status_code, 404)
        self.assertEqual((await self.client.get("/api/evidence/hidden-source")).status_code, 404)

    def test_installation_is_idempotent_and_invalid_versions_preserve_existing_data(self):
        tables = ("users", "account_roles", "collections", "evidence", "investigations", "demo_cases", "demo_datasets")
        before = {table: db.all_rows(f"SELECT * FROM {table}") for table in tables}
        demo.install(self.path)
        db.init()
        for table in tables:
            self.assertEqual(before[table], db.all_rows(f"SELECT * FROM {table}"))
        broken = deepcopy(self.bundle)
        broken["cases"][0]["evidence"].pop()
        self.path.write_text(json.dumps(broken), encoding="utf-8")
        with self.assertRaises(ValueError):
            demo.install(self.path)
        self.assertTrue(demo.ready())
        self.assertEqual(before["investigations"], db.all_rows("SELECT * FROM investigations"))

    async def test_arrival_cursor_retains_late_timestamps_and_live_analysis_is_denied(self):
        db.execute("INSERT INTO sessions VALUES(%s,%s,%s)", (auth.digest("regular-token"), "regular", time.time() + 60))
        self.client.cookies.set(auth.COOKIE, "regular-token")
        self.client.headers["x-csrf-token"] = auth.digest("regular-token:csrf")
        db.execute("INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            ("live", "regular", "Live", "", "live_scenario", db.now(), "live", json.dumps(summary([]))))
        db.execute("INSERT INTO runs(id,user_id,scenario,state,created_at,collection_id) VALUES(%s,%s,%s,%s,%s,%s)", ("run", "regular", "rollout", "running", db.now(), "live"))
        for index in range(3):
            store_rows("live", "regular", [{"id": f"event-{index}", "kind": "log", "service": "fixture", "timestamp": f"2026-01-01T00:00:0{3-index}+00:00", "message": str(index)}])
        first = (await self.client.get("/api/runs/run/evidence?limit=2")).json()
        self.assertTrue(first["has_more"])
        second = (await self.client.get(f"/api/runs/run/evidence?after={first['next_cursor']}")).json()
        self.assertEqual(second["evidence"][0]["id"], "event-2")
        self.assertFalse(second["has_more"])
        self.assertEqual((await self.client.post("/api/collections/live/investigations", json={"engine": "rules"})).status_code, 409)
        self.assertEqual((await self.client.get("/api/runs/run/evidence?after=-1")).status_code, 400)

    def test_bundle_rejects_changed_reports_missing_sources_and_false_certainty(self):
        for mutate in (lambda b: b["cases"][0]["investigation"]["report"].update(summary="Changed"),
                       lambda b: b["cases"][0]["investigation"]["usage"].update(supplied_evidence_ids=["missing"]),
                       lambda b: b["cases"][6]["investigation"]["report"].update(assessment="supported")):
            broken = deepcopy(self.bundle)
            mutate(broken)
            with self.assertRaises(ValueError):
                demo.validate(broken)

    def test_export_adds_selected_cases_and_keeps_published_reports(self):
        subset = {"version": "v1", "cases": self.bundle["cases"][:1]}
        self.path.write_text(json.dumps(subset), encoding="utf-8")
        # Synthetic observations test publication mechanics, not real captures.
        with patch("scripts.verify_demo_captures.verify"):
            result = export_bundle(self.bundle["cases"][1:2], self.path)
            self.assertEqual(result["cases"], 2)
            published = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(published["cases"], self.bundle["cases"][:2])
            before = self.path.read_bytes()
            export_bundle(self.bundle["cases"][1:2], self.path)
            self.assertEqual(before, self.path.read_bytes())

    def test_export_rejects_replacement_and_invalid_capture_without_changing_file(self):
        before = self.path.read_bytes()
        changed = deepcopy(self.bundle["cases"][:1])
        changed[0]["investigation"]["report"]["summary"] = "A different reviewed response"
        changed[0]["review"]["report_digest"] = demo.digest(changed[0]["investigation"])
        with patch("scripts.verify_demo_captures.verify"):
            with self.assertRaisesRegex(ValueError, "cannot be rewritten"):
                export_bundle(changed, self.path)
        self.assertEqual(before, self.path.read_bytes())
        with patch("scripts.verify_demo_captures.verify", side_effect=ValueError("Invalid capture")):
            with self.assertRaisesRegex(ValueError, "Invalid capture"):
                export_bundle(self.bundle["cases"][:1], self.path)
        self.assertEqual(before, self.path.read_bytes())

    def test_export_interruption_preserves_published_file(self):
        before = self.path.read_bytes()
        with patch("scripts.verify_demo_captures.verify"), patch.object(Path, "replace", side_effect=OSError("Interrupted")):
            with self.assertRaisesRegex(OSError, "Interrupted"):
                export_bundle(self.bundle["cases"][:1], self.path)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.path.parent.glob(".bundle-*.tmp")), [])

    async def test_reviewed_subset_unlocks_login_and_expands_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as directory, IsolatedDatabase(Path(directory)):
            db.init()
            self.assertFalse(demo.ready())
            path = Path(directory) / "bundle.json"
            subset = {"version": "v1", "cases": self.bundle["cases"][:1]}
            path.write_text(json.dumps(subset), encoding="utf-8")
            demo.install(path)
            self.assertTrue(demo.ready())
            await self.login()
            self.assertEqual((await self.client.get("/api/demo/cases")).json()[0]["slug"], "pool")
            before = db.all_rows("SELECT * FROM investigations")
            account = db.one("SELECT * FROM users WHERE id=%s", (demo.DEMO_ID,))
            subset["cases"] = self.bundle["cases"][:2]
            path.write_text(json.dumps(subset), encoding="utf-8")
            demo.install(path)
            self.assertEqual(demo.case_count(), 2)
            self.assertEqual(before[0], db.one("SELECT * FROM investigations WHERE id=%s", (before[0]["id"],)))
            self.assertEqual(account, db.one("SELECT * FROM users WHERE id=%s", (demo.DEMO_ID,)))
            self.assertEqual(len((await self.client.get("/api/demo/cases")).json()), 2)
            subset["cases"] = self.bundle["cases"][:1]
            path.write_text(json.dumps(subset), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "removed or rewritten"):
                demo.install(path)
            self.assertEqual(demo.case_count(), 2)

    async def test_normal_workspace_denies_live_controls_and_keeps_saved_evidence(self):
        from backend import config
        db.execute("INSERT INTO sessions VALUES(%s,%s,%s)", (auth.digest("regular-token"), "regular", time.time() + 60))
        self.client.cookies.set(auth.COOKIE, "regular-token")
        self.client.headers["x-csrf-token"] = auth.digest("regular-token:csrf")
        with patch.object(config, "ENABLE_LAB", False):
            for endpoint in ("/api/scenarios/pool/start", "/api/scenarios/reset"):
                self.assertEqual((await self.client.post(endpoint, json={})).status_code, 404)
            self.assertEqual((await self.client.get("/api/scenarios")).status_code, 404)
            self.assertEqual((await self.client.get("/api/runs")).status_code, 200)
