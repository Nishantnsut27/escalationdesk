import asyncio
import hmac
import sqlite3
import time
from contextlib import asynccontextmanager
import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route
from .config import DATA, INTERNAL_KEY, service_url
from .telemetry import Span, emit, parent
from . import sentry_telemetry

class SQLitePool:
    """Two real SQLite connections; acquisition waits are independently bounded."""
    def __init__(self):
        self.available = asyncio.Queue(maxsize=2)
        self.held = []
        self.connections = []

    def open(self):
        DATA.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            connection = sqlite3.connect(DATA / "payment.sqlite3")
            connection.execute("CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, amount INTEGER)")
            connection.commit()
            self.connections.append(connection)
            self.available.put_nowait(connection)

    async def hold(self):
        self.reset()
        for _ in range(2):
            self.held.append(await self.available.get())

    def reset(self):
        for connection in self.held:
            self.available.put_nowait(connection)
        self.held.clear()

    async def acquire(self):
        return await asyncio.wait_for(self.available.get(), timeout=1)

    def release(self, connection):
        self.available.put_nowait(connection)

    def close(self):
        for connection in self.connections:
            connection.close()

def create_service(name):
    sentry_telemetry.initialize(name)
    pool = SQLitePool()
    state = {"delay": 0.0, "requests": 0, "failures": 0, "acquisition_failures": 0, "last_latency_ms": 0.0}

    def authorize(request):
        supplied = request.headers.get("x-internal-key", "")
        if not INTERNAL_KEY or not hmac.compare_digest(supplied, INTERNAL_KEY):
            raise HTTPException(403, "Internal service.")

    @asynccontextmanager
    async def lifespan(app):
        if name == "payment":
            pool.open()
        async with httpx.AsyncClient(trust_env=False) as client:
            app.state.client = client
            yield
        if name == "payment":
            pool.close()
        sentry_telemetry.flush()

    async def health(request):
        return JSONResponse({"service": name, "status": "healthy"})

    async def metrics(request):
        authorize(request)
        metrics = dict(state)
        metrics.pop("delay")
        if name == "payment":
            metrics.update(pool_capacity=2, pool_in_use=2 - pool.available.qsize())
        return JSONResponse(metrics)

    async def control(request):
        authorize(request)
        data = await request.json()
        action = data.get("action")
        if name != "payment" or action not in {"healthy", "pool", "slow", "reset"}:
            raise HTTPException(400, "Unsupported control.")
        pool.reset()
        state["delay"] = 0.0
        if action == "pool":
            await pool.hold()
        if action == "slow":
            state["delay"] = 4.0
        return JSONResponse({"ok": True})

    async def checkout(request):
        authorize(request)
        run_id = request.headers.get("x-run-id", "")
        if not run_id or len(run_id) > 64:
            raise HTTPException(400, "Missing run context.")
        trace_id, parent_id = parent(request.headers.get("traceparent", ""))
        started = time.perf_counter()
        state["requests"] += 1
        code = 200
        with Span(name, run_id, f"{name}.checkout", trace_id, parent_id) as span:
            span.log("info", "Checkout request received")
            if name == "payment":
                try:
                    with Span(name, run_id, "db.acquire", span.trace_id, span.span_id) as acquisition:
                        try:
                            connection = await pool.acquire()
                        except TimeoutError:
                            state["acquisition_failures"] += 1
                            acquisition.log("error", "Connection acquisition timed out", timeout_ms=1000)
                            emit(name, run_id, "metric", pool_capacity=2, pool_in_use=2 - pool.available.qsize(),
                                 acquisition_failures=1)
                            raise
                    try:
                        with Span(name, run_id, "payment.process", span.trace_id, span.span_id) as processing:
                            await asyncio.sleep(state["delay"])
                            connection.execute("INSERT INTO payments(amount) VALUES(2500)")
                            connection.commit()
                            processing.log("info", "Payment processing completed")
                    finally:
                        pool.release(connection)
                except TimeoutError:
                    code = 503
                except sqlite3.Error:
                    span.log("error", "Payment database operation failed")
                    code = 503
            else:
                downstream = "order" if name == "gateway" else "payment"
                timeout = 6.0 if name == "gateway" else 2.0
                with Span(name, run_id, f"http.{downstream}", span.trace_id, span.span_id) as child:
                    try:
                        response = await request.app.state.client.post(
                            service_url(downstream) + "/checkout", timeout=timeout,
                            headers={"x-internal-key": INTERNAL_KEY, "x-run-id": run_id, "traceparent": child.carrier()})
                        if response.status_code >= 400:
                            code = 502
                            child.status = "error"
                            child.log("error", "Downstream request failed", downstream=downstream, http_status=response.status_code)
                    except httpx.TimeoutException:
                        code = 504
                        child.status = "error"
                        child.log("error", "Downstream request timed out", downstream=downstream, timeout_ms=timeout * 1000)
                    except httpx.RequestError:
                        code = 502
                        child.status = "error"
                        child.log("error", "Downstream service unavailable", downstream=downstream)
            if code >= 400:
                span.status = "error"
                state["failures"] += 1
            span.attributes["http_status"] = code
            span.log("error" if code >= 400 else "info", "Checkout request completed", http_status=code)
        state["last_latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return JSONResponse({"ok": code == 200, "service": name, "trace_id": span.trace_id}, status_code=code)

    return Starlette(lifespan=lifespan, routes=[
        Route("/health", health), Route("/metrics", metrics),
        Route("/control", control, methods=["POST"]), Route("/checkout", checkout, methods=["POST"]),
    ])

