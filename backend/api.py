import json
import asyncio
import sqlite3
from contextlib import asynccontextmanager
import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from . import auth, db
from .config import ROOT, GROQ_KEY, JEV_KEY, ORIGINS, SERVICES, service_url
from .investigator import Investigator
from .scenarios import ScenarioController
from .supplements import saved_evidence
from .provider import groq_http_client, jev_http_client
from .triage import JevTriage
from .imports import read_import, create_collection, public_collection
from .catalog import CATALOG, CASE_IDS, ACTIVE
from . import demo
from . import sentry_api
from .sentry_client import SentryClient
from .sentry_imports import ImportWorker

def owned(request, table, record_id):
    user = auth.current(request)
    row = db.one(f"SELECT * FROM {table} WHERE id=? AND user_id=?", (record_id, user["id"]))
    if not row:
        raise HTTPException(404, "Record not found.")
    if auth.is_demo(user):
        field = "investigation_id" if table == "investigations" else "collection_id"
        scope = row["id"] if table in {"collections", "investigations"} else row.get("collection_id")
        if not scope or not db.one(f"SELECT slug FROM demo_cases WHERE {field}=?", (scope,)):
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


def investigation_options(data):
    engine = data.get("engine", "rules")
    if set(data) - {"engine", "triage"} or engine not in {"rules", "groq"}:
        raise HTTPException(400, "Choose evidence rules or Groq.")
    if "triage" in data and (type(data["triage"]) is not bool or engine != "groq"):
        raise HTTPException(400, "Triage must be a boolean and requires Groq.")
    usage = {"triage": {"requested": True, "state": "pending"}} if data.get("triage") else {}
    return engine, json.dumps(usage)


async def import_logs(request):
    user = auth.current(request)
    auth.csrf(request)
    data = await read_import(request)
    collection_id, created = await asyncio.to_thread(create_collection, user["id"], data)
    return JSONResponse(public_collection(owned(request, "collections", collection_id)), status_code=201 if created else 200)


async def collections(request):
    user = auth.current(request)
    return JSONResponse([public_collection(row) for row in db.all_rows(
        "SELECT * FROM collections WHERE user_id=? ORDER BY created_at DESC, id DESC", (user["id"],))])


async def collection_detail(request):
    row = owned(request, "collections", request.path_params["id"])
    sentry_job = db.one("SELECT state FROM sentry_imports WHERE collection_id=? AND user_id=?", (row["id"], row["user_id"]))
    return JSONResponse({"collection": public_collection(row), "import_state": sentry_job["state"] if sentry_job else None,
        "investigations": [serialize_investigation(item) for item in db.all_rows(
            "SELECT * FROM investigations WHERE collection_id=? AND user_id=? ORDER BY created_at DESC, id DESC",
            (row["id"], row["user_id"]))]})


async def collection_evidence(request):
    row = owned(request, "collections", request.path_params["id"])
    params = dict(request.query_params)
    if set(params) - {"kind", "service", "query", "level", "offset", "limit"}:
        raise HTTPException(400, "Unsupported evidence filter.")
    try:
        limit, offset = int(params.pop("limit", "100")), int(params.pop("offset", "0"))
        if not 1 <= limit <= 200 or not 0 <= offset <= 5000:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Use a limit of 1-200 and offset of 0-5000.") from None
    kind = params.pop("kind", None)
    if kind not in {None, "log", "span", "metric"}:
        raise HTTPException(400, "Unknown evidence kind.")
    if "service" in params and params["service"] not in json.loads(row["summary"])["services"]:
        raise HTTPException(400, "Unknown service in this collection.")
    if "query" in params and not 1 <= len(params["query"]) <= 200:
        raise HTTPException(400, "Use a search query of 1-200 characters.")
    if "level" in params and params["level"] not in {"trace", "debug", "info", "warning", "error", "unknown"}:
        raise HTTPException(400, "Unknown log level.")
    rows = db.evidence(None, row["user_id"], kind, collection_id=row["id"], limit=limit + 1, offset=offset, **params)
    return JSONResponse({"collection_id": row["id"], "evidence": rows[:limit],
                         "next_offset": offset + limit if len(rows) > limit else None})


async def start_collection_investigation(request):
    collection = owned(request, "collections", request.path_params["id"])
    auth.csrf(request)
    if db.one("SELECT id FROM sentry_imports WHERE collection_id=? AND state IN ('queued','running','rate_limited')", (collection["id"],)):
        raise HTTPException(409, "Wait for the Sentry import to finish before investigating.")
    if collection["source_type"] == "sentry" and not json.loads(collection["summary"])["observations"]:
        raise HTTPException(409, "This Sentry snapshot contains no observations to investigate.")
    if db.one("SELECT id FROM runs WHERE collection_id=? AND state IN ('starting','running','resetting')", (collection["id"],)):
        raise HTTPException(409, "Wait for the scenario to finish collecting evidence.")
    data = await auth.body(request)
    engine, usage = investigation_options(data)
    if engine == "groq" and not GROQ_KEY:
        raise HTTPException(409, "Groq is not configured. Use evidence rules or configure the server key.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute("SELECT id FROM investigations WHERE collection_id=? AND state IN ('queued','running','quota_waiting')",
                                 (collection["id"],)).fetchone()
        if duplicate:
            return JSONResponse({"id": duplicate["id"]}, status_code=202)
        if active_jobs(conn, collection["user_id"]) >= 2:
            raise HTTPException(429, "You already have two active jobs, including investigations and further analysis.")
        investigation_id = db.uid()
        conn.execute("INSERT INTO investigations(id,collection_id,user_id,engine,state,created_at,usage) VALUES(?,?,?,?,?,?,?)",
                     (investigation_id, collection["id"], collection["user_id"], engine, "queued", db.now(), usage))
    request.app.state.investigator.enqueue(investigation_id)
    return JSONResponse({"id": investigation_id}, status_code=202)


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
    return JSONResponse({"status": "ok", "application": "Escalation Desk", "demo_available": demo.ready(),
                         "demo_case_count": demo.case_count()})

async def overview(request):
    user = auth.current(request)
    services = []
    from .config import ENABLE_LAB
    for name in SERVICES if ENABLE_LAB else ():
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
                         "providers": {"groq": bool(GROQ_KEY), "jev": bool(JEV_KEY)},
                         "runtime_busy": request.app.state.controller.lock.locked()})

async def runs(request):
    user = auth.current(request)
    rows = db.all_rows("SELECT id,scenario,state,created_at,ended_at,requests,failures,error,collection_id,stage FROM runs WHERE user_id=? ORDER BY created_at DESC LIMIT 30", (user["id"],))
    return JSONResponse(rows)

async def run_detail(request):
    row = owned(request, "runs", request.path_params["id"])
    return JSONResponse({"run": {k: v for k, v in row.items() if k != "user_id"},
                         "evidence": db.evidence(row["id"], row["user_id"], limit=200, collection_id=row.get("collection_id"))})

async def scenario_catalog(request):
    auth.current(request)
    from .config import ENABLE_LAB
    if not ENABLE_LAB:
        raise HTTPException(404, "Live scenarios are not enabled in this workspace.")
    return JSONResponse(CATALOG)

async def demo_cases(request):
    if not auth.is_demo(auth.current(request)):
        raise HTTPException(403, "Sign in to the Demo Account to browse recorded cases.")
    return JSONResponse([json.loads(row["metadata"]) for row in db.all_rows("SELECT metadata FROM demo_cases ORDER BY position")])

async def run_evidence(request):
    row = owned(request, "runs", request.path_params["id"])
    try:
        params = dict(request.query_params)
        if set(params) - {"after", "limit"}:
            raise ValueError()
        after, limit = int(params.get("after", 0)), int(params.get("limit", 100))
        if not 0 <= after <= 9223372036854775807 or not 1 <= limit <= 200:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Use a nonnegative cursor and a limit of 1-200.")
    field, scope = ("collection_id", row["collection_id"]) if row.get("collection_id") else ("run_id", row["id"])
    rows = db.all_rows(f"SELECT e.*, a.sequence FROM evidence e JOIN evidence_arrivals a ON a.evidence_id=e.id WHERE e.{field}=? AND e.user_id=? AND a.sequence>? ORDER BY a.sequence LIMIT ?", (scope, row["user_id"], after, limit + 1))
    page = rows[:limit]
    return JSONResponse({"evidence": [db.public_evidence(item) for item in page], "next_cursor": page[-1]["sequence"] if page else after, "has_more": len(rows) > limit})

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
    return JSONResponse({**db.public_evidence(row), "run_id": row["run_id"], "collection_id": row["collection_id"]})

async def start_scenario(request):
    user = auth.current(request)
    auth.csrf(request)
    from .config import ENABLE_LAB
    if not ENABLE_LAB:
        raise HTTPException(404, "Live scenarios are not enabled in this workspace.")
    await auth.body(request)
    scenario = request.path_params["scenario"]
    if scenario not in CASE_IDS | {"healthy"}:
        raise HTTPException(400, "Choose a supported scenario.")
    run_id = await request.app.state.controller.start(user["id"], scenario)
    if run_id is None:
        raise HTTPException(409, "The service runtime is busy. Try again after the active run finishes.")
    return JSONResponse({"id": run_id}, status_code=202)

async def reset_scenario(request):
    user = auth.current(request)
    auth.csrf(request)
    from .config import ENABLE_LAB
    if not ENABLE_LAB:
        raise HTTPException(404, "Live scenarios are not enabled in this workspace.")
    await auth.body(request)
    if not await request.app.state.controller.stop(user["id"]):
        raise HTTPException(404, "You have no active scenario to reset.")
    return JSONResponse({"ok": True})

async def start_investigation(request):
    incident = owned(request, "incidents", request.path_params["id"])
    auth.csrf(request)
    data = await auth.body(request)
    engine, usage = investigation_options(data)
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
        conn.execute("INSERT INTO investigations(id,incident_id,user_id,engine,state,created_at,usage) VALUES(?,?,?,?,?,?,?)",
                     (investigation_id, incident["id"], incident["user_id"], engine, "queued", db.now(), usage))
    request.app.state.investigator.enqueue(investigation_id)
    return JSONResponse({"id": investigation_id}, status_code=202)

async def investigation_detail(request):
    return JSONResponse(serialize_investigation(owned(request, "investigations", request.path_params["id"])))

async def error_response(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

@asynccontextmanager
async def lifespan(app):
    db.init()
    try:
        demo.install()
        app.state.demo_error = None
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        app.state.demo_error = "The recorded demo could not be installed. Existing accounts remain available."
    async with httpx.AsyncClient(trust_env=False) as local, groq_http_client() as provider, jev_http_client() as jev, httpx.AsyncClient(trust_env=False) as sentry_http:
        app.state.local = local
        app.state.controller = ScenarioController(local)
        app.state.investigator = Investigator(provider, triage=JevTriage(jev))
        app.state.investigator.start()
        app.state.sentry = SentryClient(sentry_http)
        app.state.sentry_importer = ImportWorker(app.state.sentry)
        app.state.sentry_importer.start()
        yield
        await app.state.controller.close()
        await app.state.investigator.close()
        await app.state.sentry_importer.close()

def create_app():
    routes = [
        Route("/api/health", health),
        Route("/api/auth/signup", auth.signup, methods=["POST"]),
        Route("/api/auth/login", auth.login, methods=["POST"]),
        Route("/api/auth/demo-login", auth.demo_login, methods=["POST"]),
        Route("/api/demo/cases", demo_cases),
        Route("/api/scenarios", scenario_catalog),
        Route("/api/auth/logout", auth.logout, methods=["POST"]),
        Route("/api/auth/me", auth.me),
        Route("/api/overview", overview),
        Route("/api/imports", import_logs, methods=["POST"]),
        Route("/api/collections", collections),
        Route("/api/collections/{id}", collection_detail),
        Route("/api/collections/{id}/evidence", collection_evidence),
        Route("/api/collections/{id}/investigations", start_collection_investigation, methods=["POST"]),
        Route("/api/runs", runs),
        Route("/api/runs/{id}", run_detail),
        Route("/api/runs/{id}/evidence", run_evidence),
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
    routes.extend(sentry_api.routes)
    if (ROOT / "dist").exists():
        routes.append(Mount("/", StaticFiles(directory=ROOT / "dist", html=True)))
    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={HTTPException: error_response})

    async def security(request, call_next):
        if request.url.path.startswith("/api/") and request.cookies.get(auth.COOKIE):
            try:
                user = auth.current(request)
            except HTTPException:
                user = None
            if user and auth.is_demo(user):
                path = request.url.path
                session_path = path in {"/api/auth/login", "/api/auth/demo-login", "/api/auth/logout", "/api/auth/me"}
                read_path = path == "/api/demo/cases" or path.startswith(("/api/collections/", "/api/investigations/", "/api/evidence/"))
                if not session_path and (request.method not in {"GET", "HEAD"} or not read_path):
                    return JSONResponse({"error": "Demo Account is read-only. Sign in to a regular account to use this action."}, status_code=403, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
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

