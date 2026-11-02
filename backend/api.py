import json
from contextlib import asynccontextmanager
import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from . import auth, db
from .config import ROOT, GROQ_KEY, ORIGINS, SERVICES, service_url
from .investigator import Investigator
from .scenarios import ScenarioController
from .supplements import saved_evidence

def owned(request, table, record_id):
    user = auth.current(request)
    row = db.one(f"SELECT * FROM {table} WHERE id=? AND user_id=?", (record_id, user["id"]))
    if not row:
        raise HTTPException(404, "Record not found.")
    return row

def serialize_investigation(row):
    return {**{key: value for key, value in row.items() if key != "user_id"},
            "report": json.loads(row["report"]) if row["report"] else None,
            "activity": json.loads(row["activity"]), "usage": json.loads(row["usage"]),
            "supplements": [serialize_supplement(item) for item in db.all_rows(
                "SELECT * FROM supplements WHERE investigation_id=? AND user_id=? ORDER BY created_at, id",
                (row["id"], row["user_id"]))]}

def serialize_supplement(row):
    return {**{k: v for k, v in row.items() if k != "user_id"},
            "result": json.loads(row["result"]) if row["result"] else None,
            "source_ids": json.loads(row["source_ids"]), "usage": json.loads(row["usage"])}

def active_jobs(conn, owner):
    return conn.execute("SELECT COUNT(*) FROM (SELECT id FROM investigations WHERE user_id=? AND state IN ('queued','running','quota_waiting') UNION ALL SELECT id FROM supplements WHERE user_id=? AND state IN ('queued','running','quota_waiting'))",
                        (owner, owner)).fetchone()[0]

async def investigation_evidence(request):
    return JSONResponse(saved_evidence(owned(request, "investigations", request.path_params["id"])))

async def start_supplement(request):
    parent = owned(request, "investigations", request.path_params["id"])
    auth.csrf(request)
    data = await auth.body(request)
    if set(data) - {"retry"} or ("retry" in data and not isinstance(data["retry"], bool)):
        raise HTTPException(400, "Only an optional boolean retry is supported.")
    if parent["engine"] != "groq" or parent["state"] != "completed" or not parent["report"]:
        raise HTTPException(409, "Further analysis requires a completed Groq report.")
    with db.connection() as conn:
        # Serialize the check-and-insert across concurrent requests/processes.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM supplements WHERE investigation_id=? AND user_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                                (parent["id"], parent["user_id"])).fetchone()
        if existing and (existing["state"] in {"queued", "running", "quota_waiting", "completed"} or not data.get("retry")):
            return JSONResponse(serialize_supplement(dict(existing)))
        if not GROQ_KEY:
            raise HTTPException(409, "Groq is not configured. Configure the server key to analyze further.")
        if active_jobs(conn, parent["user_id"]) >= 2:
            raise HTTPException(429, "You already have two active jobs, including investigations and further analysis.")
        supplement_id = db.uid()
        conn.execute("INSERT INTO supplements(id,investigation_id,user_id,state,created_at) VALUES(?,?,?,?,?)",
                     (supplement_id, parent["id"], parent["user_id"], "queued", db.now()))
    request.app.state.investigator.enqueue_supplement(supplement_id)
    return JSONResponse(serialize_supplement(db.one("SELECT * FROM supplements WHERE id=?", (supplement_id,))), status_code=202)

async def health(request):
    return JSONResponse({"status": "ok", "application": "Escalation Desk"})

async def overview(request):
    user = auth.current(request)
    services = []
    for name in SERVICES:
        try:
            response = await request.app.state.local.get(service_url(name) + "/health", timeout=1)
            status = "healthy" if response.status_code == 200 else "unavailable"
        except httpx.RequestError:
            status = "unavailable"
        services.append({"name": name, "status": status})
    counts = db.one("SELECT COUNT(*) total, COALESCE(SUM(requests),0) requests, COALESCE(SUM(failures),0) failures FROM runs WHERE user_id=?", (user["id"],))
    counts["incidents"] = db.one("SELECT COUNT(*) count FROM incidents WHERE user_id=?", (user["id"],))["count"]
    counts["evidence"] = db.one("SELECT COUNT(*) count FROM evidence WHERE user_id=?", (user["id"],))["count"]
    return JSONResponse({"services": services, "counts": counts,
                         "providers": {"groq": bool(GROQ_KEY), "jev": False},
                         "runtime_busy": request.app.state.controller.lock.locked()})

async def runs(request):
    user = auth.current(request)
    rows = db.all_rows("SELECT id,scenario,state,created_at,ended_at,requests,failures,error FROM runs WHERE user_id=? ORDER BY created_at DESC LIMIT 30", (user["id"],))
    return JSONResponse(rows)

async def run_detail(request):
    row = owned(request, "runs", request.path_params["id"])
    return JSONResponse({"run": {k: v for k, v in row.items() if k != "user_id"},
                         "evidence": db.evidence(row["id"], row["user_id"], limit=200)})

async def incidents(request):
    user = auth.current(request)
    return JSONResponse(db.all_rows(
        "SELECT id,run_id,title,state,created_at,resolved_at FROM incidents WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (user["id"],)))

async def incident_detail(request):
    row = owned(request, "incidents", request.path_params["id"])
    investigations = db.all_rows("SELECT * FROM investigations WHERE incident_id=? AND user_id=? ORDER BY created_at DESC", (row["id"], row["user_id"]))
    return JSONResponse({"incident": {k: v for k, v in row.items() if k != "user_id"},
                         "evidence": db.evidence(row["run_id"], row["user_id"], limit=200),
                         "investigations": [serialize_investigation(item) for item in investigations]})

async def evidence_detail(request):
    row = owned(request, "evidence", request.path_params["id"])
    return JSONResponse({**{k: v for k, v in row.items() if k not in {"user_id", "payload"}},
                         **json.loads(row["payload"])})

async def start_scenario(request):
    user = auth.current(request)
    auth.csrf(request)
    await auth.body(request)
    scenario = request.path_params["scenario"]
    if scenario not in {"healthy", "pool", "slow"}:
        raise HTTPException(400, "Choose a supported scenario.")
    run_id = await request.app.state.controller.start(user["id"], scenario)
    if run_id is None:
        raise HTTPException(409, "The service runtime is busy. Try again after the active run finishes.")
    return JSONResponse({"id": run_id}, status_code=202)

async def reset_scenario(request):
    user = auth.current(request)
    auth.csrf(request)
    await auth.body(request)
    if not await request.app.state.controller.stop(user["id"]):
        raise HTTPException(404, "You have no active scenario to reset.")
    return JSONResponse({"ok": True})

async def start_investigation(request):
    incident = owned(request, "incidents", request.path_params["id"])
    auth.csrf(request)
    data = await auth.body(request)
    engine = data.get("engine", "rules")
    if engine not in {"rules", "groq"}:
        raise HTTPException(400, "Unknown analysis engine.")
    if engine == "groq" and not GROQ_KEY:
        raise HTTPException(409, "Groq is not configured. Use evidence rules or configure the server key.")
    run = db.one("SELECT state FROM runs WHERE id=?", (incident["run_id"],))
    if run["state"] in {"starting", "running", "resetting"}:
        raise HTTPException(409, "Wait for the scenario to finish collecting downstream evidence.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if active_jobs(conn, incident["user_id"]) >= 2:
            raise HTTPException(429, "You already have two active jobs, including investigations and further analysis.")
        duplicate = conn.execute("SELECT id FROM investigations WHERE incident_id=? AND state IN ('queued','running','quota_waiting')", (incident["id"],)).fetchone()
        if duplicate:
            raise HTTPException(409, "This incident is already being investigated.")
        investigation_id = db.uid()
        conn.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at) VALUES(?,?,?,?,?,?)",
                     (investigation_id, incident["id"], incident["user_id"], engine, "queued", db.now()))
    request.app.state.investigator.enqueue(investigation_id)
    return JSONResponse({"id": investigation_id}, status_code=202)

async def investigation_detail(request):
    return JSONResponse(serialize_investigation(owned(request, "investigations", request.path_params["id"])))

async def error_response(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

@asynccontextmanager
async def lifespan(app):
    db.init()
    async with httpx.AsyncClient(trust_env=False) as local, httpx.AsyncClient() as provider:
        app.state.local = local
        app.state.controller = ScenarioController(local)
        app.state.investigator = Investigator(provider)
        app.state.investigator.start()
        yield
        await app.state.controller.close()
        await app.state.investigator.close()

def create_app():
    routes = [
        Route("/api/health", health),
        Route("/api/auth/signup", auth.signup, methods=["POST"]),
        Route("/api/auth/login", auth.login, methods=["POST"]),
        Route("/api/auth/logout", auth.logout, methods=["POST"]),
        Route("/api/auth/me", auth.me),
        Route("/api/overview", overview),
        Route("/api/runs", runs),
        Route("/api/runs/{id}", run_detail),
        Route("/api/incidents", incidents),
        Route("/api/incidents/{id}", incident_detail),
        Route("/api/incidents/{id}/investigations", start_investigation, methods=["POST"]),
        Route("/api/investigations/{id}", investigation_detail),
        Route("/api/investigations/{id}/evidence", investigation_evidence),
        Route("/api/investigations/{id}/supplements", start_supplement, methods=["POST"]),
        Route("/api/evidence/{id}", evidence_detail),
        Route("/api/scenarios/{scenario}/start", start_scenario, methods=["POST"]),
        Route("/api/scenarios/reset", reset_scenario, methods=["POST"]),
    ]
    if (ROOT / "dist").exists():
        routes.append(Mount("/", StaticFiles(directory=ROOT / "dist", html=True)))
    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={HTTPException: error_response})

    async def security(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in ORIGINS:
                return JSONResponse({"error": "Request origin is not allowed."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": "Cross-site request rejected."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response
    app.add_middleware(BaseHTTPMiddleware, dispatch=security)
    return app

