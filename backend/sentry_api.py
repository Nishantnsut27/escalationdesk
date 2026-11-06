"""Account-owned Sentry connections and explicit import endpoints."""
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route
from . import auth, config, db
from .sentry_client import configured, seal, SentryError, SLUG, INSTALLATION, CONTROL
from .sentry_imports import ACTIVE, DATASETS, parameters, create_import, public_import, query_spec


def user(request, write=False):
    value = auth.current(request)
    if auth.is_demo(value):
        raise HTTPException(403, "Demo Account cannot connect Sentry or import telemetry.")
    if write:
        auth.csrf(request)
    return value


def connection(request, write=False):
    owner = user(request, write)
    row = db.one("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s", (request.path_params["id"], owner["id"]))
    if not row:
        raise HTTPException(404, "Sentry connection not found.")
    return row


def job(request, write=False):
    owner = user(request, write)
    row = db.one("SELECT * FROM sentry_imports WHERE id=%s AND user_id=%s", (request.path_params["id"], owner["id"]))
    if not row:
        raise HTTPException(404, "Sentry import not found.")
    return row


def public_connection(row):
    keys = ("id", "organization", "region", "state", "created_at", "checked_at", "error")
    return {**{k: row[k] for k in keys}, "projects": json.loads(row["projects"]), "capabilities": json.loads(row["capabilities"])}


async def listing(request):
    owner = user(request)
    return JSONResponse({"configured": configured(), "public_url": config.PUBLIC_URL,
        "connections": [public_connection(r) for r in db.all_rows("SELECT * FROM sentry_connections WHERE user_id=%s ORDER BY created_at DESC", (owner["id"],))],
        "imports": [public_import(r) for r in db.all_rows("SELECT * FROM sentry_imports WHERE user_id=%s ORDER BY created_at DESC LIMIT 50", (owner["id"],))]})


async def start(request):
    owner = user(request, True)
    if not configured():
        raise HTTPException(409, "Sentry OAuth is not configured. Follow the operator setup guide.")
    data = await auth.body(request)
    if set(data) - {"region"} or data.get("region") not in {"us", "eu"}:
        raise HTTPException(400, "Choose the Sentry organization's US or EU data region.")
    if str(request.base_url).rstrip("/") != config.PUBLIC_URL:
        raise HTTPException(409, "Open this app at its configured HTTPS address and sign in before connecting Sentry.")
    state = secrets.token_urlsafe(32)
    with db.connection() as conn:
        conn.execute("DELETE FROM sentry_oauth_states WHERE expires<%s OR session_digest=%s", (time.time(), auth.digest(request.cookies[auth.COOKIE])))
        conn.execute("INSERT INTO sentry_oauth_states VALUES(%s,%s,%s,%s,%s)", (auth.digest(state), owner["id"], auth.digest(request.cookies[auth.COOKIE]), data["region"], time.time() + 600))
    return JSONResponse({"url": f"{CONTROL}/sentry-apps/{config.SENTRY_APP_SLUG}/external-install/?" + urlencode({"state": state})})


async def callback(request):
    owner = user(request)
    if not configured():
        raise HTTPException(409, "Sentry OAuth is not configured.")
    params = request.query_params
    with db.connection() as conn:
        db.lock(conn)
        state = conn.execute("SELECT * FROM sentry_oauth_states WHERE digest=%s AND user_id=%s AND session_digest=%s AND expires>%s",
            (auth.digest(params.get("state", "")), owner["id"], auth.digest(request.cookies[auth.COOKIE]), time.time())).fetchone()
        if not state:
            raise HTTPException(400, "Sentry authorization expired or did not match your session. Start Connect Sentry again.")
        conn.execute("DELETE FROM sentry_oauth_states WHERE digest=%s", (state["digest"],))
    if params.get("error"):
        return RedirectResponse(config.PUBLIC_URL + "/#connections?oauth=cancelled", status_code=303)
    installation, organization, code = params.get("installationId", ""), params.get("orgSlug", ""), params.get("code", "")
    if not INSTALLATION.fullmatch(installation) or not SLUG.fullmatch(organization) or not 1 <= len(code) <= 4096:
        raise HTTPException(400, "Sentry returned incomplete authorization information. Reconnect.")
    existing = db.one("SELECT * FROM sentry_connections WHERE installation_id=%s", (installation,))
    previous_generation = existing["generation"] if existing else None
    if existing and existing["user_id"] != owner["id"]:
        raise HTTPException(409, "This Sentry installation is already owned by another workspace.")
    client = request.app.state.sentry
    try:
        tokens, expiry = await client.exchange(installation, code)
        projects = await client.projects(organization, state["region"], tokens["token"])
        # Verify Install must be enabled in Sentry. Token-scoped completion also
        # proves the supplied installation is the one this grant authorized.
        await client.request("PUT", CONTROL, f"/api/0/sentry-app-installations/{installation}/",
                             token=tokens["token"], payload={"status": "installed"})
        encrypted = seal(tokens)
        with db.connection() as conn:
            db.lock(conn)
            if conn.execute("SELECT 1 FROM sentry_deliveries WHERE installation_id=%s AND action='deleted'", (installation,)).fetchone():
                raise HTTPException(409, "This installation was removed during setup. Install Sentry again.")
            existing = conn.execute("SELECT * FROM sentry_connections WHERE installation_id=%s", (installation,)).fetchone()
            if existing:
                if existing["user_id"] != owner["id"]:
                    raise HTTPException(409, "This installation is already owned by another workspace.")
                if previous_generation != existing["generation"]:
                    raise HTTPException(409, "The connection changed during authorization. Start Connect Sentry again.")
                conn.execute("UPDATE sentry_connections SET credentials=%s,expires_at=%s,state='connected',organization=%s,region=%s,projects=%s,capabilities='{}',error=NULL,checked_at=%s,generation=generation+1 WHERE id=%s",
                    (encrypted, expiry, organization, state["region"], json.dumps(projects), db.now(), existing["id"]))
            else:
                conn.execute("INSERT INTO sentry_connections(id,user_id,installation_id,organization,region,credentials,expires_at,state,projects,created_at,checked_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'connected',%s,%s,%s)",
                    (db.uid(), owner["id"], installation, organization, state["region"], encrypted, expiry, json.dumps(projects), db.now(), db.now()))
    except SentryError as exc:
        raise HTTPException(502, str(exc)) from None
    return RedirectResponse(config.PUBLIC_URL + "/#connections?oauth=connected", status_code=303)


async def check(request):
    row = connection(request, True)
    client = request.app.state.sentry
    try:
        token = await client.token(row["id"])
        projects = await client.projects(row["organization"], row["region"], token)
        capabilities = {}
        if projects:
            now = datetime.now(timezone.utc)
            for dataset in DATASETS:
                path, params = query_spec(row, projects[0], dataset, {"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat()})
                if dataset != "errors":
                    params["per_page"] = 1
                try:
                    data, _ = await client.get(row, path, params)
                    records = data if dataset == "errors" else data.get("data") if isinstance(data, dict) else None
                    if not isinstance(records, list):
                        raise SentryError("Sentry returned an invalid dataset response.")
                    capabilities[dataset] = {"state": "available" if records else "empty", "project": projects[0]["slug"]}
                except SentryError as exc:
                    capabilities[dataset] = {"state": "unavailable" if exc.status in {403, 404} else "error", "reason": str(exc)}
                    if exc.status == 401:
                        raise
        db.execute("UPDATE sentry_connections SET projects=%s,capabilities=%s,checked_at=%s,error=NULL WHERE id=%s AND generation=%s AND state='connected'",
            (json.dumps(projects), json.dumps(capabilities), db.now(), row["id"], row["generation"]))
    except SentryError as exc:
        db.execute("UPDATE sentry_connections SET error=%s,checked_at=%s WHERE id=%s AND generation=%s", (str(exc), db.now(), row["id"], row["generation"]))
        raise HTTPException(502, str(exc)) from None
    return JSONResponse(public_connection(db.one("SELECT * FROM sentry_connections WHERE id=%s", (row["id"],))))


def cancel_jobs(conn, connection_id):
    rows = conn.execute("SELECT collection_id FROM sentry_imports WHERE connection_id=%s AND state IN ('queued','running','rate_limited')", (connection_id,)).fetchall()
    conn.execute("UPDATE sentry_imports SET state='cancelled',completed_at=%s,error='Sentry connection was disconnected.' WHERE connection_id=%s AND state IN ('queued','running','rate_limited')", (db.now(), connection_id))
    for item in rows:
        finish_coverage(conn, item["collection_id"], "Import cancelled on disconnect. Retained evidence is partial.")


def finish_coverage(conn, collection_id, message):
    summary = json.loads(conn.execute("SELECT summary FROM collections WHERE id=%s", (collection_id,)).fetchone()[0])
    summary["coverage"] = message
    conn.execute("UPDATE collections SET summary=%s WHERE id=%s", (json.dumps(summary), collection_id))


async def disconnect(request):
    row = connection(request, True)
    with db.connection() as conn:
        conn.execute("DELETE FROM sentry_oauth_states WHERE user_id=%s", (row["user_id"],))
        conn.execute("UPDATE sentry_connections SET state='disconnected',credentials=NULL,expires_at=0,generation=generation+1 WHERE id=%s", (row["id"],))
        cancel_jobs(conn, row["id"])
    return JSONResponse({"ok": True, "message": "Disconnected locally. You can also uninstall Escalation Desk in Sentry's integration settings."})


async def webhook(request):
    if not config.SENTRY_CLIENT_SECRET:
        raise HTTPException(503, "Sentry lifecycle endpoint is not configured.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 65536:
            raise HTTPException(413, "Installation notification is too large.")
        chunks.append(chunk)
    raw = b"".join(chunks)
    expected = hmac.new(config.SENTRY_CLIENT_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("sentry-hook-signature", "")):
        raise HTTPException(401, "Invalid Sentry notification signature.")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        installation = value.get("installation", {}).get("uuid", "")
        if not INSTALLATION.fullmatch(installation):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Invalid installation notification.") from None
    # Only installation events belong to Phase 9; alerts never enqueue analysis.
    if request.headers.get("sentry-hook-resource") != "installation" or value.get("action") not in {"created", "deleted"}:
        return JSONResponse({"ok": True, "ignored": True})
    with db.connection() as conn:
        added = conn.execute("INSERT INTO sentry_deliveries VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING digest", (hashlib.sha256(raw).hexdigest(), installation, value["action"], db.now())).fetchone()
        if added and value["action"] == "deleted":
            row = conn.execute("SELECT id FROM sentry_connections WHERE installation_id=%s", (installation,)).fetchone()
            if row:
                conn.execute("UPDATE sentry_connections SET state='disconnected',credentials=NULL,expires_at=0,generation=generation+1 WHERE id=%s", (row["id"],))
                cancel_jobs(conn, row["id"])
    return JSONResponse({"ok": True})


async def import_start(request):
    row = connection(request, True)
    options = parameters(await auth.body(request), row)
    result, created = create_import(row["user_id"], row, options)
    if created:
        request.app.state.sentry_importer.wake()
    return JSONResponse(public_import(result), status_code=202 if created else 200)


async def import_detail(request):
    return JSONResponse(public_import(job(request)))


async def import_retry(request):
    old = job(request, True)
    if old["state"] not in {"failed", "partial", "interrupted", "cancelled"}:
        raise HTTPException(409, "Retry is available for incomplete imports only.")
    row = db.one("SELECT * FROM sentry_connections WHERE id=%s AND user_id=%s", (old["connection_id"], old["user_id"]))
    result, created = create_import(old["user_id"], row, parameters(json.loads(old["parameters"]), row), old["id"])
    if created:
        request.app.state.sentry_importer.wake()
    return JSONResponse(public_import(result), status_code=202)


async def import_cancel(request):
    row = job(request, True)
    with db.connection() as conn:
        changed = conn.execute("UPDATE sentry_imports SET state='cancelled',completed_at=%s WHERE id=%s AND state IN ('queued','running','rate_limited') RETURNING id", (db.now(), row["id"])).fetchone()
        if changed:
            finish_coverage(conn, row["collection_id"], "Cancelled import. Retained evidence is partial.")
    return JSONResponse(public_import(db.one("SELECT * FROM sentry_imports WHERE id=%s", (row["id"],))))


routes = [
    Route("/api/connections/sentry", listing),
    Route("/api/connections/sentry/start", start, methods=["POST"]),
    Route("/api/connections/sentry/callback", callback),
    Route("/api/connections/sentry/webhook", webhook, methods=["POST"]),
    Route("/api/connections/sentry/{id}/check", check, methods=["POST"]),
    Route("/api/connections/sentry/{id}/disconnect", disconnect, methods=["POST"]),
    Route("/api/connections/sentry/{id}/imports", import_start, methods=["POST"]),
    Route("/api/sentry-imports/{id}", import_detail),
    Route("/api/sentry-imports/{id}/retry", import_retry, methods=["POST"]),
    Route("/api/sentry-imports/{id}/cancel", import_cancel, methods=["POST"]),
]
