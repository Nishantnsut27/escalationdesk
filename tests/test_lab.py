from database import IsolatedDatabase, verify_users
"""Real local TLS, workload lifecycle and account-bound scenario API checks."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from backend import auth, db, config
from backend.api import create_app
from backend.scenarios import ScenarioController


class LiveScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.object(config, "ENABLE_LAB", True))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        storage = IsolatedDatabase(Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        db.init()
        for owner in ("alice", "bob"):
            db.execute("INSERT INTO users VALUES(%s,%s,%s,%s,%s)", (owner, owner, owner, "hash", db.now()))
            db.execute("INSERT INTO sessions VALUES(%s,%s,%s)", (auth.digest(owner), owner, time.time() + 300))
        self.local = httpx.AsyncClient(trust_env=False)
        self.addAsyncCleanup(self.local.aclose)
        verify_users()
        self.app = create_app()
        self.app.state.controller = ScenarioController(self.local)
        self.addAsyncCleanup(self.app.state.controller.close)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.client.cookies.set(auth.COOKIE, "alice")
        self.client.headers["x-csrf-token"] = auth.digest("alice:csrf")

    async def test_real_tls_rejection_and_recovery_with_cursor_and_no_analysis(self):
        response = await self.client.post("/api/scenarios/tls/start", json={})
        self.assertEqual(response.status_code, 202, response.text)
        identifier = response.json()["id"]
        for _ in range(400):
            row = db.one("SELECT * FROM runs WHERE id=%s", (identifier,))
            if row["state"] not in {"starting", "running", "resetting"}:
                break
            await asyncio.sleep(.1)
        self.assertEqual(row["state"], "completed", row)
        self.assertEqual(row["failures"], 10)
        observations = db.evidence(None, "alice", collection_id=row["collection_id"], limit=200)
        self.assertTrue(any("expired" in item.get("error", "").lower() for item in observations))
        self.assertTrue(any(item.get("http_status") == 200 for item in observations))
        self.assertEqual(db.all_rows("SELECT * FROM investigations"), [])
        self.assertFalse(list((db.DATA / "scenarios" / identifier).glob("*key.pem")))
        self.assertFalse(self.app.state.controller.lock.locked())
        page = await self.client.get(f"/api/runs/{identifier}/evidence?limit=200")
        self.assertGreater(len(page.json()["evidence"]), 30)
        self.assertTrue(all(item["timestamp"] for item in page.json()["evidence"]))

    async def test_owner_stop_busy_and_partial_evidence_are_retained(self):
        response = await self.client.post("/api/scenarios/backlog/start", json={})
        identifier = response.json()["id"]
        self.client.cookies.set(auth.COOKIE, "bob")
        self.client.headers["x-csrf-token"] = auth.digest("bob:csrf")
        self.assertEqual((await self.client.post("/api/scenarios/rollout/start", json={})).status_code, 409)
        self.assertEqual((await self.client.post("/api/scenarios/reset", json={})).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/runs/{identifier}/evidence")).status_code, 404)
        self.client.cookies.set(auth.COOKIE, "alice")
        self.client.headers["x-csrf-token"] = auth.digest("alice:csrf")
        await asyncio.sleep(.5)
        self.assertEqual((await self.client.post("/api/scenarios/reset", json={})).status_code, 200)
        row = db.one("SELECT * FROM runs WHERE id=%s", (identifier,))
        self.assertEqual(row["state"], "cancelled", row)
        collection = db.one("SELECT * FROM collections WHERE id=%s", (row["collection_id"],))
        self.assertTrue(json.loads(collection["summary"])["partial"])
        self.assertFalse(self.app.state.controller.lock.locked())
