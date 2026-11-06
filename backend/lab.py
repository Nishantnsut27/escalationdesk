"""Bounded, isolated real workload supervision and account-owned capture."""
import asyncio
import ipaddress
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from . import db
from .catalog import CATALOG, ACTIVE
from .config import ROOT
from .imports import redact_object


def summary(rows, partial=False):
    return {**{k: 0 for k in ("input_bytes", "duplicates", "conflicting_ids", "malformed_records", "redactions", "missing_timestamps", "missing_services", "invalid_fields")},
            "observations": len(rows), "input_lines": sum(r["kind"] == "log" for r in rows),
            "services": sorted({r["service"] for r in rows}), "kinds": sorted({r["kind"] for r in rows}),
            "coverage": "Controlled local execution. " + ("Partial recording; some evidence is unavailable." if partial else "Captured observations; unavailable telemetry is not inferred."),
            "redaction_note": "Credentials and local application paths are excluded from the recording.", "partial": partial}


def ports(count=4):
    # Reserve a contiguous free range for the retained three-service reference chain.
    for _ in range(100):
        base = 18000 + secrets.randbelow(35000)
        held = []
        try:
            for port in range(base, base + count):
                sock = socket.socket()
                held.append(sock)
                sock.bind(("127.0.0.1", port))
            return base
        except OSError:
            pass
        finally:
            for sock in held:
                sock.close()
    raise ValueError("No local workload ports are available.")


def certificates(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Escalation Desk local test CA")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=2)).not_valid_after(now + timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "server-key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for label, end in (("valid", now + timedelta(days=1)), ("expired", now - timedelta(days=1))):
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
                .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=2)).not_valid_after(end)
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        (directory / f"{label}.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return now


class LabRuntime:
    def __init__(self, case, run_id, directory, on_rows, on_stage=lambda *args: None, recording=False):
        self.case, self.run_id, self.directory = case, run_id, Path(directory)
        self.on_rows, self.on_stage, self.recording = on_rows, on_stage, recording
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lease = self.directory / "lease"
        self.children, self.logs, self.offsets, self.rows = [], [], {}, []
        self.base, self.key = ports(), secrets.token_urlsafe(32)
        self.requests = self.failures = 0
        self.reference = case in {"pool", "slow", "incomplete"}
        self.edge, self.worker = self.base + 1, self.base + 3
        self.client = httpx.AsyncClient(trust_env=False, timeout=10)
        self.heartbeat = None

    def observation(self, service, message, level="info", kind="log", **fields):
        row = {"id": db.uid(), "service": service, "kind": kind, "timestamp": db.now(), "level": level, "message": message, **fields}
        telemetry = self.directory / "telemetry"
        telemetry.mkdir(exist_ok=True)
        with (telemetry / "operations.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(row) + "\n")

    async def beat(self):
        while True:
            self.lease.touch()
            await asyncio.sleep(1)

    def spawn(self, role, port, **extra):
        env = os.environ.copy()
        env.update(DESK_DATA_DIR=str(self.directory.resolve()), DESK_PORT=str(self.base), DESK_INTERNAL_KEY=self.key,
                   LAB_ROLE=role, LAB_MODE=self.case, LAB_RUN=self.run_id, LAB_PORT=str(port), LAB_TARGET=str(self.worker),
                   LAB_LEASE=str(self.lease.resolve()), PYTHONUNBUFFERED="1")
        env.update(extra)
        self.lease.touch()
        command = [sys.executable, "-m", "backend.launcher", "--service", role] if self.reference else [sys.executable, "-m", "backend.lab_workload"]
        log = (self.directory / f"process-{port}-{len(self.children)}.log").open("w", encoding="utf-8")
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.children.append(child)
        self.logs.append(log)
        return child

    async def stop_child(self, child):
        if child.poll() is None:
            child.terminate()
            try:
                await asyncio.to_thread(child.wait, 5)
            except subprocess.TimeoutExpired:
                child.kill()
                await asyncio.to_thread(child.wait, 5)

    async def ready(self, port, child, tls=False, accept_unready=False):
        last_error = "no response"
        verify = ssl.create_default_context(cafile=str(self.directory / "ca.pem")) if tls else True
        async with httpx.AsyncClient(trust_env=False, verify=verify, timeout=.5) as client:
            for _ in range(80):
                if child.poll() is not None:
                    raise ValueError("Workload process exited before startup completed.")
                try:
                    response = await client.get(f"{'https' if tls else 'http'}://127.0.0.1:{port}/health")
                    if response.status_code == 200 or accept_unready and response.status_code == 503:
                        self.observation("deployer", "Readiness probe completed", instance=str(port), http_status=response.status_code,
                                         version=response.json().get("version", "reference"))
                        return
                except httpx.RequestError as exc:
                    last_error = str(exc)[:200]
                await asyncio.sleep(.1)
        raise ValueError("Workload readiness deadline exceeded: " + last_error)

    async def control(self, port, **data):
        response = await self.client.post(f"http://127.0.0.1:{port}/control", headers={"x-internal-key": self.key}, json=data)
        response.raise_for_status()

    async def expired_ready(self, port, child):
        context = ssl.create_default_context(cafile=str(self.directory / "ca.pem"))
        async with httpx.AsyncClient(verify=context, trust_env=False, timeout=.5) as client:
            for _ in range(80):
                if child.poll() is not None:
                    raise ValueError("HTTPS workload exited before verification.")
                try:
                    await client.get(f"https://127.0.0.1:{port}/health")
                except httpx.RequestError as exc:
                    if "certificate has expired" in str(exc).lower():
                        self.observation("deployer", "TLS verification failed", level="error", error=str(exc)[:300], instance=str(port))
                        return
                await asyncio.sleep(.1)
        raise ValueError("The HTTPS workload did not present its expired certificate.")

    def collect(self):
        additions = []
        for path in (self.directory / "telemetry").glob("*.jsonl"):
            with path.open("rb") as file:
                file.seek(self.offsets.get(path.name, 0))
                while True:
                    position = file.tell()
                    line = file.readline()
                    if not line or not line.endswith(b"\n"):
                        self.offsets[path.name] = position
                        break
                    event = json.loads(line)
                    if self.case == "incomplete" and (event["kind"] != "log" or event["service"] not in {"gateway", "order"}):
                        continue
                    row = {k: v for k, v in event.items() if k not in {"run_id", "user_id", "collection_id"}}
                    row, _ = redact_object(row)
                    additions.append(row)
        if len(self.rows) + len(additions) > 5000:
            raise ValueError("The recording reached its 5,000-observation bound.")
        if additions:
            self.rows.extend(additions)
            self.on_rows(additions)

    async def traffic(self):
        route = "checkout" if self.reference else "request"
        response = await self.client.post(f"http://127.0.0.1:{self.edge}/{route}",
            headers={"x-internal-key": self.key, "x-run-id": self.run_id})
        self.requests += 1
        self.failures += int(response.status_code >= 400)
        for port, service in ((self.edge, "gateway"), (self.worker, "payment" if self.reference else "inventory")):
            if self.case == "tls":
                continue
            try:
                result = await self.client.get(f"http://127.0.0.1:{port}/metrics", headers={"x-internal-key": self.key}, timeout=.15)
                if result.status_code == 200:
                    self.observation(service, "Workload metric sample", kind="metric", **result.json())
            except httpx.RequestError:
                pass
        self.collect()
        await asyncio.sleep(.03)

    async def execute(self):
        self.heartbeat = asyncio.create_task(self.beat())
        tls = self.case == "tls"
        try:
            if tls:
                await asyncio.to_thread(certificates, self.directory)
            if self.reference:
                for role, port in (("payment", self.worker), ("order", self.base + 2), ("gateway", self.edge)):
                    child = self.spawn(role, port)
                    await self.ready(port, child)
            else:
                worker = self.spawn("worker", self.worker)
                await self.ready(self.worker, worker, tls)
                edge = self.spawn("edge", self.edge)
                await self.ready(self.edge, edge)
            self.on_stage("baseline", self.requests, self.failures)
            for _ in range(3):
                await self.traffic()
            self.on_stage("failure", self.requests, self.failures)
            if self.reference:
                await self.control(self.worker, action="pool" if self.case in {"pool", "incomplete"} else "slow")
            elif self.case in {"unavailable", "startup", "tls"}:
                await self.stop_child(worker)
                self.observation("deployer", "Service process exited", instance=str(self.worker), exit_code=worker.returncode)
                if self.case == "startup":
                    for attempt in range(3):
                        failed = self.spawn("worker", self.worker, LAB_REQUIRED_CONFIG="missing")
                        await asyncio.to_thread(failed.wait, 8)
                        self.observation("deployer", "Startup attempt exited", instance=str(self.worker), attempt=attempt + 1, exit_code=failed.returncode)
                elif tls:
                    worker = self.spawn("worker", self.worker, LAB_CERT="expired.pem")
                    await self.expired_ready(self.worker, worker)
                    from cryptography import x509
                    cert = x509.load_pem_x509_certificate((self.directory / "expired.pem").read_bytes())
                    self.observation("inventory", "Presented certificate inspected", not_after=cert.not_valid_after_utc.isoformat(),
                                     not_before=cert.not_valid_before_utc.isoformat(), hostname="127.0.0.1")
            elif self.case == "rollout":
                new = self.spawn("worker", self.base + 2, LAB_VERSION="v2")
                await self.ready(self.base + 2, new, accept_unready=True)
                await self.control(self.edge, fault=True, targets=[self.worker, self.base + 2])
            else:
                await self.control(self.worker, fault=True)
                await self.control(self.edge, fault=True)
            for index in range(400 if self.recording else 10):
                if self.case == "incomplete":
                    await self.control(self.worker, action="pool" if index % 3 else "reset")
                if self.recording and not self.reference:
                    async with asyncio.TaskGroup() as group:
                        for _ in range(4):
                            group.create_task(self.traffic())
                else:
                    await self.traffic()
                self.on_stage("failure", self.requests, self.failures)
                if self.recording and sum(r["kind"] == "log" for r in self.rows) >= 500:
                    break
            self.on_stage("recovery", self.requests, self.failures)
            if self.reference:
                await asyncio.sleep(4.2)
                self.collect()
                await self.control(self.worker, action="reset")
            elif self.case in {"unavailable", "startup", "tls"}:
                if tls:
                    await self.stop_child(worker)
                worker = self.spawn("worker", self.worker)
                await self.ready(self.worker, worker, tls)
            else:
                await self.control(self.worker, fault=False)
                await self.control(self.edge, fault=False, targets=[self.worker])
            if self.case == "backlog":
                await asyncio.sleep(3)
            before = self.failures
            for _ in range(3):
                await self.traffic()
            if self.failures != before:
                raise ValueError("Healthy recovery requests failed.")
            if self.case == "backlog":
                await asyncio.sleep(.1)
                result = await self.client.get(f"http://127.0.0.1:{self.worker}/metrics", headers={"x-internal-key": self.key})
                result.raise_for_status()
                self.observation("inventory", "Final queue metric sample", kind="metric", **result.json())
                if result.json()["queue_depth"]:
                    raise ValueError("Queue did not drain during recovery.")
                self.collect()
        finally:
            await self.close()

    async def close(self):
        for child in reversed(self.children):
            await self.stop_child(child)
        if self.heartbeat:
            self.heartbeat.cancel()
        for file in self.logs:
            file.close()
        await self.client.aclose()
        self.collect()
        self.lease.unlink(missing_ok=True)
        for name in ("server-key.pem",):
            (self.directory / name).unlink(missing_ok=True)


def store_rows(collection, owner, rows):
    with db.connection() as conn:
        for row in rows:
            conn.execute("INSERT INTO evidence(id,collection_id,user_id,kind,service,timestamp,payload) VALUES(?,?,?,?,?,?,?)",
                (row["id"], collection, owner, row["kind"], row["service"], row.get("timestamp"), json.dumps(row)))


async def run_live(run_id, owner, case):
    collection = db.uid()
    meta = next(item for item in CATALOG if item["id"] == case)
    db.execute("INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(?,?,?,?,?,?,?,?)",
               (collection, owner, meta["title"], "Investigate the captured operational observations.", "live_scenario", db.now(), collection, json.dumps(summary([], True))))
    db.execute("UPDATE runs SET collection_id=?, state='running', stage='starting' WHERE id=?", (collection, run_id))
    runtime = LabRuntime(case, run_id, db.DATA / "scenarios" / run_id,
                         lambda rows: store_rows(collection, owner, rows),
                         lambda stage, requests, failures: db.execute("UPDATE runs SET stage=?, requests=?, failures=? WHERE id=?", (stage, requests, failures, run_id)))
    state, error = "completed", None
    try:
        async with asyncio.timeout(120):
            await runtime.execute()
    except asyncio.CancelledError:
        state, error = "cancelled", "Stopped by the owner; retained evidence is partial."
    except Exception as exc:
        state, error = "failed", str(exc)[:250] if isinstance(exc, ValueError) else "Scenario execution failed; retained evidence is partial."
    finally:
        try:
            async with asyncio.timeout(30):
                await runtime.close()
        except Exception:
            state, error = "failed", "Workload cleanup did not finish; lease watchdog will stop remaining processes."
        db.execute("UPDATE collections SET summary=? WHERE id=?", (json.dumps(summary(runtime.rows, state != "completed" or case == "incomplete")), collection))
        db.execute("UPDATE runs SET state=?, stage='finished', error=?, ended_at=?, requests=?, failures=? WHERE id=?",
                   (state, error, db.now(), runtime.requests, runtime.failures, run_id))
