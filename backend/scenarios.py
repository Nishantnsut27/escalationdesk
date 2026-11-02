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
        with db.connection() as conn:
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
                        conn.execute("INSERT OR IGNORE INTO evidence(id,run_id,user_id,kind,service,timestamp,payload) VALUES(?,?,?,?,?,?,?)",
                                     (event["id"], run_id, owner, event["kind"], event["service"],
                                      event["timestamp"], json.dumps(payload)))

    async def collect_metrics(self, run_id, owner, baseline):
        for name in SERVICES:
            response = await self.client.get(service_url(name) + "/metrics",
                                             headers={"x-internal-key": INTERNAL_KEY}, timeout=3)
            response.raise_for_status()
            metric = response.json()
            if baseline is None:
                continue
            for key in ("requests", "failures", "acquisition_failures"):
                metric[key] = metric.get(key, 0) - baseline[name].get(key, 0)
            db.execute("INSERT INTO evidence(id,run_id,user_id,kind,service,timestamp,payload) VALUES(?,?,?,?,?,?,?)",
                       (db.uid(), run_id, owner, "metric", name, db.now(), json.dumps(metric)))

    async def start(self, owner, scenario):
        if self.lock.locked():
            return None
        await self.lock.acquire()
        run_id = db.uid()
        try:
            db.execute("INSERT INTO runs(id,user_id,scenario,state,created_at) VALUES(?,?,?,?,?)",
                       (run_id, owner, scenario, "starting", db.now()))
        except Exception:
            self.lock.release()
            raise
        self.active_id = run_id
        self.task = asyncio.create_task(self.run(run_id, owner, scenario))
        return run_id

    async def run(self, run_id, owner, scenario):
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
            db.execute("UPDATE runs SET state='running' WHERE id=?", (run_id,))
            consecutive = 0
            for _ in range(3):
                result = await self.client.post(service_url("gateway") + "/checkout",
                                               headers={"x-internal-key": INTERNAL_KEY, "x-run-id": run_id}, timeout=10)
                failed = result.status_code >= 400
                consecutive = consecutive + 1 if failed else 0
                db.execute("UPDATE runs SET requests=requests+1, failures=failures+? WHERE id=?", (int(failed), run_id))
                self.ingest(run_id, owner)
                await self.collect_metrics(run_id, owner, baseline)
                if consecutive == 3:
                    incident_id = db.uid()
                    db.execute("INSERT INTO incidents(id,run_id,user_id,title,state,created_at) VALUES(?,?,?,?,?,?)",
                               (incident_id, run_id, owner, "Checkout failures across the service chain", "open", db.now()))
            # Downstream work can finish after Order's timeout; retain those spans before releasing this run.
            await asyncio.sleep(4.2)
            self.ingest(run_id, owner)
        except asyncio.CancelledError:
            final_state = "cancelled"
        except Exception:
            final_state = "failed"
            error = "Service execution failed. Check runtime health and try again."
        finally:
            db.execute("UPDATE runs SET state='resetting' WHERE id=?", (run_id,))
            try:
                await self.control("reset")
                await asyncio.sleep(4.2 if final_state != "completed" else 0)
                self.ingest(run_id, owner)
                recovery = await self.client.post(service_url("gateway") + "/checkout",
                    headers={"x-internal-key": INTERNAL_KEY, "x-run-id": run_id}, timeout=10)
                if recovery.status_code != 200:
                    raise RuntimeError("Recovery failed")
                self.ingest(run_id, owner)
                if incident_id:
                    db.execute("UPDATE incidents SET state='resolved', resolved_at=? WHERE id=?", (db.now(), incident_id))
            except Exception:
                final_state = "failed"
                error = "Automatic recovery could not be verified. Restart the local runtime."
            db.execute("UPDATE runs SET state=?, ended_at=?, error=? WHERE id=?",
                       (final_state, db.now(), error, run_id))
            self.active_id = None
            self.lock.release()

    async def stop(self, owner):
        if not self.active_id:
            return False
        run = db.one("SELECT * FROM runs WHERE id=? AND user_id=?", (self.active_id, owner))
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

