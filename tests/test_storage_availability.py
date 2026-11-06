import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from psycopg import OperationalError
from psycopg_pool import TooManyRequests
from backend.api import create_app, overview


class StorageAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_failures_return_safe_json_with_and_without_a_session(self):
        app = create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("backend.api.demo.availability", side_effect=OperationalError("private connection detail")):
                response = await client.get("/api/health")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private", response.text)
            self.assertIn("temporarily unavailable", response.json()["error"])
            with patch("backend.api.auth.current", side_effect=TooManyRequests("private pool detail")):
                response = await client.post("/api/auth/verification-confirmation", json={"token": "unused"},
                                             headers={"Cookie": "desk_session=fixture"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["retry-after"], "5")
            self.assertNotIn("private", response.text)

    async def test_slow_overview_storage_does_not_block_other_coroutines(self):
        release = threading.Event()
        started = threading.Event()
        def slow_query(*_):
            started.set()
            release.wait(1)
            return {"requests": 0, "failures": 0, "total": 0, "incidents": 0, "evidence": 0}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            controller=SimpleNamespace(lock=threading.Lock()))))
        with patch("backend.api.auth.current", return_value={"id": "fixture"}), \
             patch("backend.config.ENABLE_LAB", False), patch("backend.api.db.one", side_effect=slow_query):
            begin = time.monotonic()
            task = asyncio.create_task(overview(request))
            try:
                while not started.is_set():
                    await asyncio.sleep(.01)
                self.assertLess(time.monotonic() - begin, .5)
            finally:
                release.set()
                await task
