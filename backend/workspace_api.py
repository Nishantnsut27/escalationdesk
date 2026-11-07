"""Read-only evidence workspace routes. Every aggregate starts from an owned scope."""
import json

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import auth, db, queries
from .imports import public_collection


def parameters(request):
    if len(request.query_params.multi_items()) != len(set(request.query_params.keys())):
        raise HTTPException(400, "Use each filter only once.")
    return dict(request.query_params)


def evidence_scope(request, params):
    from .api import owned
    table = "runs" if request.url.path.startswith("/api/runs/") else "collections"
    row = owned(request, table, request.path_params["id"])
    scope = {"collection_id": row["id"]} if table == "collections" else (
        {"collection_id": row["collection_id"]} if row.get("collection_id") else {"run_id": row["id"]})
    statement, args = queries.scope_sql(row["user_id"], **scope)
    services = {item["service"] for item in db.all_rows("SELECT DISTINCT e.service" + statement, args)}
    try:
        filters = queries.Filters.parse(params, services=services)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return row, {"owner": row["user_id"], **scope, "filters": filters}


def pagination(params, default=100):
    try:
        limit, offset = int(params.pop("limit", default)), int(params.pop("offset", 0))
        if not 1 <= limit <= 200 or not 0 <= offset <= 2_000_000_000:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, "Use a limit of 1-200 and a nonnegative offset.") from None
    return limit, offset


def rows(request):
    params = parameters(request)
    locate = "offset" not in params
    limit, offset = pagination(params)
    selected = params.pop("selected", None)
    if selected and not queries.ID.fullmatch(selected):
        raise HTTPException(400, "Invalid selected observation.")
    row, scope = evidence_scope(request, params)
    return JSONResponse({"collection_id": scope.get("collection_id"), "run_id": scope.get("run_id"),
                         **queries.page(**scope, limit=limit, offset=offset, selected=selected, locate=locate)})


def histogram(request):
    _, scope = evidence_scope(request, parameters(request))
    return JSONResponse(queries.histogram(**scope))


def timeline(request):
    params = parameters(request)
    limit, offset = pagination(params, default=100)
    _, scope = evidence_scope(request, params)
    result = queries.page(**scope, limit=limit, offset=offset)
    fields = ("id", "kind", "service", "timestamp", "level", "message", "name", "duration_ms", "status", "error_tags", "trace_id", "request_id")
    result["evidence"] = [{key: row[key] for key in fields if key in row} for row in result["evidence"]]
    result["note"] = "Chronological source time; undated observations follow in source order. Order is not causation."
    return JSONResponse(result)


def flow(request):
    _, scope = evidence_scope(request, parameters(request))
    return JSONResponse(queries.request_flow(**scope))


def details(request):
    row, scope = evidence_scope(request, parameters(request))
    statement, args = queries.scope_sql(scope["owner"], **{key: value for key, value in scope.items() if key not in {"owner", "filters"}})
    facets = db.all_rows("SELECT DISTINCT e.service,i.trace_id,i.request_id" + statement, args)
    return JSONResponse({"collection": public_collection(row) if "summary" in row else None,
        "run": {key: row.get(key) for key in ("id", "created_at", "state")} if "summary" not in row else None,
        "services": sorted({item["service"] for item in facets}),
        "traces": sorted({item["trace_id"] for item in facets if item["trace_id"]}),
        "requests": sorted({item["request_id"] for item in facets if item["request_id"]}),
        **queries.summary(**scope)})


def account_scope(request, params=None):
    from .api import owned
    user = auth.current(request)
    params = parameters(request) if params is None else params
    collection = params.pop("collection_id", None)
    if collection:
        owned(request, "collections", collection)
    try:
        filters = queries.Filters.parse(params)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return user, {"owner": user["id"], "collection_id": collection, "demo": auth.is_demo(user), "filters": filters}


def investigations_for(user, collection=None, limit=50, offset=0):
    statement = "SELECT v.id,v.collection_id,v.incident_id,v.engine,v.state,v.stage,v.created_at,v.completed_at,v.report,v.usage,c.title FROM investigations v LEFT JOIN collections c ON c.id=v.collection_id WHERE v.user_id=%s"
    args = [user["id"]]
    if auth.is_demo(user):
        statement += " AND EXISTS (SELECT 1 FROM demo_cases d WHERE d.investigation_id=v.id)"
    if collection:
        statement += " AND v.collection_id=%s"
        args.append(collection)
    values = db.all_rows(statement + " ORDER BY v.created_at DESC,v.id DESC LIMIT %s OFFSET %s", (*args, limit, offset))
    result = []
    for value in values:
        report = json.loads(value.pop("report")) if value["report"] else None
        if "report" in value:
            value.pop("report")
        usage = json.loads(value.pop("usage"))
        value["assessment"] = report.get("assessment") if report else None
        value["summary"] = report.get("summary") if report else None
        value["category"] = usage.get("triage", {}).get("category")
        result.append(value)
    return result


def investigations(request):
    params = parameters(request)
    limit, offset = pagination(params, default=50)
    user, scope = account_scope(request, params)
    if scope["filters"].values():
        raise HTTPException(400, "Investigation history supports collection_id, limit and offset only.")
    return JSONResponse(investigations_for(user, scope["collection_id"], limit=limit, offset=offset))


def errors(request):
    user, scope = account_scope(request)
    return JSONResponse({**queries.summary(**scope), "investigation_categories": classification_counts(user, scope["collection_id"])})


def account_rows(request):
    params = parameters(request)
    limit, offset = pagination(params)
    _, scope = account_scope(request, params)
    return JSONResponse(queries.page(**scope, limit=limit, offset=offset))


def classification_counts(user, collection=None):
    statement = "SELECT COALESCE(v.usage::jsonb->'triage'->>'category','unclassified') category,COUNT(*) investigations FROM investigations v WHERE v.user_id=%s"
    args = [user["id"]]
    if collection:
        statement += " AND v.collection_id=%s"
        args.append(collection)
    if auth.is_demo(user):
        statement += " AND EXISTS (SELECT 1 FROM demo_cases d WHERE d.investigation_id=v.id)"
    return db.all_rows(statement + " GROUP BY 1 ORDER BY investigations DESC,category", args)


def dashboard(request):
    user, scope = account_scope(request)
    condition = "user_id=%s" + (" AND EXISTS (SELECT 1 FROM demo_cases d WHERE d.collection_id=collections.id)" if auth.is_demo(user) else "")
    collections = [public_collection(row) for row in db.all_rows("SELECT * FROM collections WHERE " + condition + " ORDER BY created_at DESC,id DESC", (user["id"],))]
    selected = [row for row in collections if not scope["collection_id"] or row["id"] == scope["collection_id"]]
    overview = queries.summary(**scope)
    overview["counts"]["collections"] = len(selected)
    filters = queries.Filters(**{**scope["filters"].values(), "level": "error"})
    return JSONResponse({**overview, "collections": collections,
        "trend": queries.histogram(**{**scope, "filters": filters}),
        "recent_investigations": investigations_for(user, scope["collection_id"], limit=8),
        "investigation_categories": classification_counts(user, scope["collection_id"]),
        "last_import": selected[0]["created_at"] if selected else None,
        "connections": [] if auth.is_demo(user) else db.all_rows("SELECT id,organization,region,state,checked_at FROM sentry_connections WHERE user_id=%s ORDER BY created_at DESC", (user["id"],)),
        "monitoring_note": "Saved evidence snapshots. Live infrastructure monitoring is not connected."})


routes = [Route("/api/dashboard", dashboard), Route("/api/errors", errors), Route("/api/investigations", investigations), Route("/api/logs", account_rows)]
for prefix in ("collections", "runs"):
    for suffix, endpoint in (("logs", rows), ("histogram", histogram), ("timeline", timeline), ("flow", flow), ("analysis", details)):
        routes.append(Route("/api/" + prefix + "/{id}/" + suffix, endpoint))
