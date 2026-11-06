"""Private loopback workloads. Only the bounded scenario supervisor launches these."""
import asyncio
import hmac
import os
import sqlite3
import ssl
import time
import traceback
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route
from .config import DATA, INTERNAL_KEY
from .telemetry import Span, emit, parent


def create_workload(role, mode, port, target):
    service = "gateway" if role == "edge" else "inventory"
    version = os.getenv("LAB_VERSION", "v1")
    run_id = os.environ["LAB_RUN"]
    state = {"fault": False, "requests": 0, "failures": 0, "targets": [target], "window": 0, "window_count": 0}
    store = DATA / f"jobs-{port}.sqlite3"

    def connection():
        return sqlite3.connect(store)

    def authorize(request):
        if not INTERNAL_KEY or not hmac.compare_digest(request.headers.get("x-internal-key", ""), INTERNAL_KEY):
            raise HTTPException(403, "Private workload.")

    async def consume():
        while True:
            if not state["fault"]:
                with connection() as conn:
                    job = conn.execute("SELECT id FROM jobs WHERE done=0 ORDER BY id LIMIT 1").fetchone()
                    if job:
                        conn.execute("UPDATE jobs SET done=1 WHERE id=?", (job[0],))
                        emit("worker", run_id, "log", level="info", message="Job acknowledged", job_id=str(job[0]))
            await asyncio.sleep(.02)

    @asynccontextmanager
    async def lifespan(app):
        with connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, amount INTEGER)")
            conn.execute("INSERT INTO products(amount) SELECT 25 WHERE NOT EXISTS(SELECT 1 FROM products)")
            conn.execute("CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, created REAL NOT NULL, done INTEGER DEFAULT 0)")
        task = asyncio.create_task(consume()) if mode == "backlog" and role != "edge" else None
        emit(service, run_id, "log", level="info", message="Service started", version=version, instance=str(port))
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def health(request):
        ready = not (mode == "rollout" and version == "v2")
        return JSONResponse({"ready": ready, "version": version}, status_code=200 if ready else 503)

    async def control(request):
        authorize(request)
        data = await request.json()
        state["fault"] = bool(data.get("fault"))
        if "targets" in data:
            targets = data["targets"]
            if not isinstance(targets, list) or not 1 <= len(targets) <= 2 or any(type(v) is not int or not 1024 <= v <= 65535 for v in targets):
                raise HTTPException(400, "Invalid local targets.")
            state["targets"] = targets
            emit("router", run_id, "log", level="info", message="Traffic routing updated", target_instances=[str(p) for p in targets])
        if mode == "schema" and role != "edge":
            emit(service, run_id, "log", level="info", message="Application release activated", version="v2" if state["fault"] else "v1")
            with connection() as conn:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(products)")]
            emit(service, run_id, "log", level="info", message="Installed database schema inspected", table="products", columns=columns)
        return JSONResponse({"ok": True})

    async def metrics(request):
        authorize(request)
        data = {"requests": state["requests"], "failures": state["failures"]}
        if mode == "backlog" and role != "edge":
            with connection() as conn:
                count, oldest = conn.execute("SELECT COUNT(*), MIN(created) FROM jobs WHERE done=0").fetchone()
            data.update(queue_depth=count, oldest_job_age_ms=round((time.time() - oldest) * 1000) if oldest else 0)
        return JSONResponse(data)

    async def request_work(request):
        authorize(request)
        state["requests"] += 1
        trace, parent_id = parent(request.headers.get("traceparent", ""))
        code = 200
        response_headers = {}
        with Span(service, run_id, f"{service}.request", trace, parent_id) as span:
            span.log("info", "Request received", version=version, instance=str(port))
            try:
                if role == "edge":
                    destination = state["targets"][state["requests"] % len(state["targets"])]
                    tls = mode == "tls"
                    verify = ssl.create_default_context(cafile=str(DATA / "ca.pem")) if tls else True
                    headers = {"x-internal-key": INTERNAL_KEY,
                               "x-workload-credential": "invalid" if mode == "authentication" and state["fault"] else "local-test"}
                    with Span(service, run_id, "http.inventory", span.trace_id, span.span_id) as child:
                        headers["traceparent"] = child.carrier()
                        attempts = 3 if mode == "throttling" else 1
                        for attempt in range(attempts):
                            try:
                                async with httpx.AsyncClient(verify=verify, trust_env=False) as client:
                                    response = await client.post(f"{'https' if tls else 'http'}://127.0.0.1:{destination}/request", headers=headers, timeout=httpx.Timeout(2, connect=.35))
                                child.log("error" if response.status_code >= 400 else "info", "Dependency response received",
                                          http_status=response.status_code, attempt=attempt + 1, target_instance=str(destination),
                                          retry_after=response.headers.get("retry-after"))
                                code = 502 if response.status_code >= 400 else 200
                                if response.status_code != 429 or attempt == attempts - 1:
                                    break
                                child.log("warning", "Retry scheduled", delay_ms=10, retry_after=response.headers.get("retry-after"))
                                await asyncio.sleep(.01)
                            except httpx.RequestError as exc:
                                code = 502
                                detail = str(exc)[:300] or type(exc).__name__
                                child.log("error", "Dependency connection failed: " + detail, error=detail,
                                          error_type=type(exc).__name__, target_instance=str(destination))
                                break
                        child.status = "error" if code >= 400 else "ok"
                elif mode == "exception" and state["fault"]:
                    item = None
                    item["price"]  # An actual request exception, recorded before Starlette returns HTTP 500.
                elif mode == "schema":
                    with connection() as conn:
                        query = "SELECT amount, currency FROM products" if state["fault"] else "SELECT amount FROM products"
                        span.log("info", "Database query started", query=query, version="v2" if state["fault"] else "v1")
                        conn.execute(query).fetchall()
                elif mode == "authentication" and request.headers.get("x-workload-credential") != "local-test":
                    code = 401
                    span.log("error", "Upstream authentication rejected", http_status=401)
                elif mode == "rollout" and version == "v2":
                    code = 503
                    span.log("error", "Request reached instance before readiness", ready=False, version=version, instance=str(port))
                elif mode == "throttling" and state["fault"]:
                    window = int(time.monotonic())
                    if state["window"] != window:
                        state.update(window=window, window_count=0)
                    state["window_count"] += 1
                    if state["window_count"] > 1:
                        code = 429
                        response_headers["Retry-After"] = "1"
                        span.log("warning", "Request rate limit exceeded", http_status=429, retry_after_seconds=1)
                elif mode == "backlog":
                    with connection() as conn:
                        job = conn.execute("INSERT INTO jobs(created) VALUES(?)", (time.time(),)).lastrowid
                    code = 202
                    span.log("info", "Job enqueued", job_id=str(job))
                span.log("error" if code >= 400 else "info", "Request completed", http_status=code, version=version)
                span.attributes["http_status"] = code
                span.status = "error" if code >= 400 else "ok"
            except Exception:
                state["failures"] += 1
                stack = traceback.format_exc().replace(str(Path(__file__).resolve().parent.parent), "<application>")
                span.log("error", "Unhandled request exception", stack_trace=stack, http_status=500)
                raise
        state["failures"] += int(code >= 400)
        return JSONResponse({"ok": code < 400, "version": version}, status_code=code, headers=response_headers)

    return Starlette(lifespan=lifespan, routes=[Route("/health", health), Route("/control", control, methods=["POST"]),
        Route("/metrics", metrics), Route("/request", request_work, methods=["POST"])])


def main():
    def watch_parent():
        lease = Path(os.environ["LAB_LEASE"])
        while True:
            time.sleep(2)
            try:
                alive = time.time() - lease.stat().st_mtime < 15
            except OSError:
                alive = False
            if not alive:
                os._exit(75)
    threading.Thread(target=watch_parent, daemon=True).start()
    role, mode = os.environ["LAB_ROLE"], os.environ["LAB_MODE"]
    port, target = int(os.environ["LAB_PORT"]), int(os.environ.get("LAB_TARGET", "0"))
    if role != "edge" and os.getenv("LAB_REQUIRED_CONFIG") == "missing":
        emit("inventory", os.environ["LAB_RUN"], "log", level="error", message="Startup validation failed: CATALOG_REGION is required", exit_code=1)
        raise SystemExit(1)
    options = {}
    if role != "edge" and mode == "tls":
        options = dict(ssl_certfile=str(DATA / os.getenv("LAB_CERT", "valid.pem")), ssl_keyfile=str(DATA / "server-key.pem"))
    uvicorn.run(create_workload(role, mode, port, target), host="127.0.0.1", port=port, log_level="critical", access_log=False, **options)


if __name__ == "__main__":
    main()
