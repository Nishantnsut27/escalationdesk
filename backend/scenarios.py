import asyncio
import json
from . import db
from .config import DATA, INTERNAL_KEY, SERVICES, service_url

class ScenarioController:
    def __init__(self, client):
        self.client = client
        self.lock = asyncio.Lock()
        self.task = None
        self.active_id = None

    async def control(self, action):
        result = await self.client.post(service_url("payment") + "/control",
                                       headers={"x-internal-key": INTERNAL_KEY},
                                       json={"action": action}, timeout=5)
        result.raise_for_status()

    def ingest(self, run_id, owner):
        directory = DATA / "telemetry"
        if not directory.exists():
            return
        rows = []
        for path in directory.glob("*.jsonl"):
            with path.open(encoding="utf-8") as file:
                for line in file:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("run_id") != run_id:
                        continue
                    payload = {key: value for key, value in event.items()
                               if key not in {"id", "run_id", "service", "kind", "timestamp"}}
                    rows.append((event["id"], run_id, owner, event["kind"], event["service"],
                                 event["timestamp"], json.dumps(payload)))
        if rows:
            with db.connection() as conn, conn.cursor() as cursor:
                cursor.executemany("INSERT INTO evidence(id,run_id,user_id,kind,service,timestamp,payload) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)

    async def collect_metrics(self, run_id, owner, baseline):
        rows = []
        for name in SERVICES:
            response = await self.client.get(service_url(name) + "/metrics",
                                             headers={"x-internal-key": INTERNAL_KEY}, timeout=3)
            response.raise_for_status()
            metric = response.json()
            if baseline is None:
                continue
            for key in ("requests", "failures", "acquisition_failures"):
                metric[key] = metric.get(key, 0) - baseline[name].get(key, 0)
            rows.append((db.uid(), run_id, owner, "metric", name, db.now(), json.dumps(metric)))
        if rows:
            await asyncio.to_thread(self.store_metrics, rows)

    @staticmethod
    def store_metrics(rows):
        with db.connection() as conn, conn.cursor() as cursor:
            cursor.executemany("INSERT INTO evidence(id,run_id,user_id,kind,service,timestamp,payload) VALUES(%s,%s,%s,%s,%s,%s,%s)", rows)

    async def start(self, owner, scenario):
        if self.lock.locked():
            return None
        await self.lock.acquire()
        run_id = db.uid()
        try:
            await asyncio.to_thread(db.execute,"INSERT INTO runs(id,user_id,scenario,state,created_at) VALUES(%s,%s,%s,%s,%s)",
                       (run_id, owner, scenario, "starting", db.now()))
        except Exception:
            self.lock.release()
            raise
        self.active_id = run_id
        self.task = asyncio.create_task(self.run(run_id, owner, scenario))
        return run_id

    async def run(self, run_id, owner, scenario):
        if scenario not in {"pool", "slow", "healthy"}:
            from .lab import run_live
            try:
                await run_live(run_id, owner, scenario)
            finally:
                self.active_id = None
                self.lock.release()
            return
        final_state = "completed"
        error = None
        incident_id = None
        baseline = {}
        try:
            await self.control("reset")
            for name in SERVICES:
                response = await self.client.get(service_url(name) + "/metrics",
                                                 headers={"x-internal-key": INTERNAL_KEY}, timeout=3)
                response.raise_for_status()
                baseline[name] = response.json()
            await self.control(scenario)
            await asyncio.to_thread(db.execute,"UPDATE runs SET state='running' WHERE id=%s", (run_id,))
            consecutive = 0
            for _ in range(3):
                result = await self.client.post(service_url("gateway") + "/checkout",
                                               headers={"x-internal-key": INTERNAL_KEY, "x-run-id": run_id}, timeout=10)
                failed = result.status_code >= 400
                consecutive = consecutive + 1 if failed else 0
                await asyncio.to_thread(db.execute,"UPDATE runs SET requests=requests+1, failures=failures+%s WHERE id=%s", (int(failed), run_id))
                await asyncio.to_thread(self.ingest,run_id,owner)
                await self.collect_metrics(run_id, owner, baseline)
                if consecutive == 3:
                    incident_id = db.uid()
                    await asyncio.to_thread(db.execute,"INSERT INTO incidents(id,run_id,user_id,title,state,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                               (incident_id, run_id, owner, "Checkout failures across the service chain", "open", db.now()))
            # Downstream work can finish after Order's timeout; retain those spans before releasing this run.
            await asyncio.sleep(4.2)
            await asyncio.to_thread(self.ingest,run_id,owner)
        except asyncio.CancelledError:
            final_state = "cancelled"
        except Exception as exc:
            final_state = "failed"
            error = f"Service execution failed ({type(exc).__name__}). Check runtime health and try again."
        finally:
            await asyncio.to_thread(db.execute,"UPDATE runs SET state='resetting' WHERE id=%s", (run_id,))
            recovery_step = "reset"
            try:
                await self.control("reset")
                await asyncio.sleep(4.2 if final_state != "completed" else 0)
                recovery_step = "evidence collection"
                await asyncio.to_thread(self.ingest,run_id,owner)
                recovery_step = "checkout verification"
                recovery = await self.client.post(service_url("gateway") + "/checkout",
                    headers={"x-internal-key": INTERNAL_KEY, "x-run-id": run_id}, timeout=10)
                if recovery.status_code != 200:
                    raise RuntimeError("Recovery failed")
                recovery_step = "evidence retention"
                await asyncio.to_thread(self.ingest,run_id,owner)
                if incident_id:
                    await asyncio.to_thread(db.execute,"UPDATE incidents SET state='resolved', resolved_at=%s WHERE id=%s", (db.now(), incident_id))
            except Exception as exc:
                final_state = "failed"
                error = f"Automatic recovery could not be verified ({recovery_step}: {type(exc).__name__}). Restart the local runtime."
            await asyncio.to_thread(db.execute,"UPDATE runs SET state=%s, ended_at=%s, error=%s WHERE id=%s",
                       (final_state, db.now(), error, run_id))
            self.active_id = None
            self.lock.release()

    async def stop(self, owner):
        if not self.active_id:
            return False
        run = db.one("SELECT * FROM runs WHERE id=%s AND user_id=%s", (self.active_id, owner))
        if not run:
            return False
        if self.task and not self.task.done():
            self.task.cancel()
            await self.task
        return True

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await self.task

