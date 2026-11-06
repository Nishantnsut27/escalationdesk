"""Explicit snapshot imports; provider I/O never occurs from report reads."""
import asyncio
import hashlib
import json
import math
import time
from datetime import datetime, timedelta, timezone
from starlette.exceptions import HTTPException
from . import db
from .imports import parse_logs, redact, redact_object, timestamp
from .sentry_client import SentryError, next_cursor

ACTIVE = ("queued", "running", "rate_limited")
DATASETS = ("errors", "logs", "spans")
MAX_RECORDS = 5000
MAX_BYTES = 10 * 1024 * 1024
IMPORT_SECONDS = 300
NORMALIZATION_VERSION = 2
FIELDS = {
    "logs": ["id", "timestamp", "message", "severity", "trace", "span_id", "service.name", "http.status_code"],
    "spans": ["id", "timestamp", "trace", "span_id", "parent_span", "span.description", "span.op", "span.duration", "span.status", "service.name", "http.status_code"],
}


def query_spec(connection, project, dataset, options, cursor=None):
    params = {"start": options["start"], "end": options["end"]}
    if cursor:
        params["cursor"] = cursor
    org = connection["organization"]
    if dataset == "errors":
        params["full"] = "true"
        return f'/api/0/projects/{org}/{project["slug"]}/events/', params
    params.update(dataset=dataset, project=project["id"], field=FIELDS[dataset], per_page=100, sort="timestamp")
    return f"/api/0/organizations/{org}/events/", params


def source_scrub(value):
    # Provider request headers are often arrays of pairs, not keyed dictionaries.
    # Remove those containers and local variables before the generic redactor.
    if isinstance(value, dict):
        return {k: source_scrub(v) for k, v in value.items()
                if k.lower() not in {"headers", "cookies", "vars", "user", "request", "authorization"}}
    if isinstance(value, list):
        return [source_scrub(v) for v in value]
    return value


def normalize(source, dataset, project, connection):
    if not isinstance(source, dict):
        raise ValueError("Non-object source record")
    scrubbed = source_scrub(source)
    cleaned, changes = redact_object(scrubbed)
    changes += int(scrubbed != source)
    source_id = cleaned.get("eventID") if dataset == "errors" else cleaned.get("id")
    source_id = source_id or cleaned.get("event_id") or cleaned.get("span_id")
    if not isinstance(source_id, (str, int)) or not 1 <= len(str(source_id)) <= 200:
        raise ValueError("Missing source ID")
    # A response scoped to one project must not sneak in evidence from another.
    returned_project = cleaned.get("projectID", cleaned.get("project_id"))
    if returned_project is not None and str(returned_project) != project["id"]:
        raise ValueError("Wrong project in response")
    tags = cleaned.get("tags", [])
    tags = {x.get("key"): x.get("value") for x in tags if isinstance(x, dict)} if isinstance(tags, list) else tags
    tags = tags if isinstance(tags, dict) else {}
    service = cleaned.get("service.name") or tags.get("service.name") or "unknown"
    if not isinstance(service, str):
        service = "unknown"
    trace = cleaned.get("trace") or cleaned.get("trace_id")
    contexts = cleaned.get("contexts", {})
    context = contexts.get("trace", {}) if isinstance(contexts, dict) else {}
    context = context if isinstance(context, dict) else {}
    trace = trace or context.get("trace_id")
    message = cleaned.get("message") or cleaned.get("title") or cleaned.get("span.description") or cleaned.get("span.op") or "Sentry observation"
    exceptions = []
    if dataset == "errors":
        details = []
        for entry in cleaned.get("entries", []):
            if isinstance(entry, dict) and entry.get("type") == "exception":
                for exception in entry.get("data", {}).get("values", []):
                    exceptions.append(exception)
                    details.append(f'{exception.get("type", "Exception")}: {exception.get("value", "")}')
                    for frame in (exception.get("stacktrace") or {}).get("frames", [])[-8:]:
                        details.append(f'  {frame.get("filename", "?")}:{frame.get("lineNo", "?")} in {frame.get("function", "?")}')
        if details:
            message = str(message) + "\n" + "\n".join(details)
    normalized = {"id": str(source_id), "service": service, "kind": "span" if dataset == "spans" else "log",
        "timestamp": cleaned.get("timestamp", cleaned.get("dateCreated")), "message": str(message)[:8000],
        "level": cleaned.get("severity", (tags or {}).get("level", "error" if dataset == "errors" else "unknown"))}
    for key, value in {"trace_id": trace, "span_id": cleaned.get("span_id") or context.get("span_id"),
                       "parent_id": cleaned.get("parent_span") or context.get("parent_span_id"),
                       "span_op": cleaned.get("span.op") or context.get("op")}.items():
        if value:
            normalized[key] = value
    status = cleaned.get("http.status_code")
    if status is None:
        status = tags.get("http.status_code")
    if isinstance(status, str) and status.isascii() and status.isdigit():
        status = int(status)
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        normalized["http_status"] = status
    if exceptions:
        # Sentry orders chained exceptions oldest first; describe the final one.
        exception = exceptions[-1]
        normalized["exception_type"] = exception.get("type")
        handled = (exception.get("mechanism") or {}).get("handled")
        if isinstance(handled, bool):
            normalized["exception_handled"] = handled
    if dataset == "spans":
        normalized.update(name=cleaned.get("span.description") or cleaned.get("span.op") or "Sentry span",
                          status=cleaned.get("span.status", "unknown"))
        duration = cleaned.get("span.duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0:
            normalized["duration_ms"] = duration
    rows, summary = parse_logs(json.dumps(normalized))
    row = rows[0]
    original = json.dumps(cleaned, ensure_ascii=False)
    truncated = len(original.encode()) > 24000
    if truncated:
        original = original.encode()[:24000].decode("utf-8", errors="ignore") + "\n[SOURCE TRUNCATED]"
    row.update(original=original, source_format="sentry", source_dataset=dataset,
        source_id=str(source_id), source_project_id=project["id"], source_project=project["slug"],
        source_organization=connection["organization"], source_connection_id=connection["id"], source_truncated=truncated)
    return row, changes + summary["redactions"], truncated


def parameters(data, connection):
    if not isinstance(data, dict) or set(data) - {"projects", "datasets", "start", "end", "title", "symptom"}:
        raise HTTPException(400, "Choose projects, datasets, start/end, title and an optional symptom.")
    project_ids, datasets = data.get("projects"), data.get("datasets", list(DATASETS))
    allowed = {p["id"] for p in json.loads(connection["projects"])}
    if not isinstance(project_ids, list) or not 1 <= len(project_ids) <= 32 or any(not isinstance(p, str) or p not in allowed for p in project_ids):
        raise HTTPException(400, "Select 1-32 projects from this connection's verified project list.")
    if not isinstance(datasets, list) or not datasets or any(not isinstance(d, str) or d not in DATASETS for d in datasets):
        raise HTTPException(400, "Select errors, logs and/or spans.")
    now = datetime.now(timezone.utc)
    start = timestamp(data.get("start", (now - timedelta(hours=1)).isoformat()))
    end = timestamp(data.get("end", now.isoformat()))
    if not start or not end or not 0 < (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() <= 86400:
        raise HTTPException(400, "Select a valid time window of at most 24 hours, including timezones.")
    if datetime.fromisoformat(end) > now + timedelta(minutes=1):
        raise HTTPException(400, "The import window cannot end in the future.")
    title, symptom = data.get("title", "Sentry evidence"), data.get("symptom", "")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(symptom, str) or len(symptom) > 2000:
        raise HTTPException(400, "Use a 1-120 character title and a symptom of at most 2,000 characters.")
    return dict(projects=sorted(set(project_ids)), datasets=sorted(set(datasets)), start=start, end=end,
                title=redact(title.strip())[0], symptom=redact(symptom)[0])


def public_import(row):
    return {k: json.loads(v) if k in {"parameters", "progress"} else v for k, v in row.items()
            if k not in {"user_id", "fingerprint"}}


def create_import(owner, connection, options, retry_of=None):
    fingerprint = hashlib.sha256(json.dumps([connection["id"], options, NORMALIZATION_VERSION], sort_keys=True).encode()).hexdigest()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not retry_of:
            existing = conn.execute("SELECT * FROM sentry_imports WHERE user_id=? AND fingerprint=? ORDER BY rowid DESC LIMIT 1", (owner, fingerprint)).fetchone()
            if existing:
                return dict(existing), False
        current = conn.execute("SELECT state FROM sentry_connections WHERE id=? AND user_id=?", (connection["id"], owner)).fetchone()
        if not current or current["state"] != "connected":
            raise HTTPException(409, "Reconnect Sentry before importing.")
        if conn.execute("SELECT id FROM sentry_imports WHERE user_id=? AND state IN ('queued','running','rate_limited')", (owner,)).fetchone():
            raise HTTPException(409, "You already have an active Sentry import.")
        if conn.execute("SELECT COUNT(*) FROM sentry_imports WHERE state IN ('queued','running','rate_limited')").fetchone()[0] >= 20:
            raise HTTPException(429, "The import queue is full. Try again shortly.")
        job, collection, stamp = db.uid(), db.uid(), db.now()
        summary = dict(observations=0, input_bytes=0, input_lines=0, duplicates=0, conflicting_ids=0,
            malformed_records=0, redactions=0, missing_timestamps=0, missing_services=0, invalid_fields=0,
            services=[], kinds=[], coverage="Sentry import queued; evidence is not final.",
            redaction_note="Credential patterns, request containers and local variables are removed. Review evidence before analysis.",
            source={"provider": "sentry", "organization": connection["organization"], "connection_id": connection["id"],
                    "region": connection["region"], "projects": options["projects"], "start": options["start"], "end": options["end"]},
            normalization_version=NORMALIZATION_VERSION, datasets={}, truncated_records=0, out_of_window=0)
        conn.execute("INSERT INTO collections VALUES(?,?,?,?,?,?,?,?,?)", (collection, owner, options["title"], options["symptom"],
            None, "sentry", stamp, "sentry:" + job, json.dumps(summary)))
        conn.execute("INSERT INTO sentry_imports(id,user_id,connection_id,collection_id,fingerprint,parameters,state,created_at,retry_of) VALUES(?,?,?,?,?,?,'queued',?,?)",
                     (job, owner, connection["id"], collection, fingerprint, json.dumps(options), stamp, retry_of))
        return dict(conn.execute("SELECT * FROM sentry_imports WHERE id=?", (job,)).fetchone()), True


class ImportWorker:
    def __init__(self, client):
        self.client, self.task = client, None
        self.event = asyncio.Event()

    def start(self):
        self.task = asyncio.create_task(self.loop())

    def wake(self):
        self.event.set()

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def loop(self):
        while True:
            self.event.clear()
            row = db.one("SELECT * FROM sentry_imports WHERE state='queued' ORDER BY created_at,id LIMIT 1")
            if row:
                await self.run(row)
            else:
                await self.event.wait()

    def alive(self, job):
        row = db.one("SELECT state FROM sentry_imports WHERE id=?", (job["id"],))
        connection = db.one("SELECT state FROM sentry_connections WHERE id=?", (job["connection_id"],))
        if not row or row["state"] not in ACTIVE or not connection or connection["state"] != "connected":
            raise SentryError("Import cancelled or connection no longer available.")

    def progress(self, job, summary, state="running", extra=None):
        with db.connection() as conn:
            if conn.execute("UPDATE sentry_imports SET state=?,progress=? WHERE id=? AND state IN ('queued','running','rate_limited')",
                (state, json.dumps({"observations": summary["observations"], "datasets": summary["datasets"], **(extra or {})}), job["id"])).rowcount:
                conn.execute("UPDATE collections SET summary=? WHERE id=?", (json.dumps(summary), job["collection_id"]))

    async def page(self, job, connection, path, params, summary, deadline):
        for attempt in range(3):
            self.alive(job)
            try:
                return await self.client.get(connection, path, params)
            except SentryError as exc:
                if attempt == 2 or (exc.status not in {429, 500, 502, 503, 504} and exc.status is not None):
                    raise
                wait = exc.retry_after if exc.status == 429 else 2 ** attempt
                if not math.isfinite(wait) or wait > 120 or time.monotonic() + wait >= deadline:
                    raise
                self.progress(job, summary, "rate_limited" if exc.status == 429 else "running",
                              {"retry_at": time.time() + wait, "message": str(exc)})
                # Short waits let cancellation/disconnection terminate a queued retry promptly.
                end = time.monotonic() + wait
                while time.monotonic() < end:
                    await asyncio.sleep(min(1, end - time.monotonic()))
                    self.alive(job)
                self.progress(job, summary)

    def save(self, job, connection, project, dataset, records, summary):
        options = json.loads(job["parameters"])
        with db.connection() as conn:
            current = conn.execute("SELECT state FROM sentry_imports WHERE id=?", (job["id"],)).fetchone()
            if current["state"] not in ACTIVE:
                raise SentryError("Import cancelled.")
            for source in records:
                try:
                    row, changes, truncated = normalize(source, dataset, project, connection)
                except (ValueError, TypeError, AttributeError, HTTPException, RecursionError):
                    summary["malformed_records"] += 1
                    continue
                if row["timestamp"] and not datetime.fromisoformat(options["start"]) <= datetime.fromisoformat(row["timestamp"]) <= datetime.fromisoformat(options["end"]):
                    summary["out_of_window"] += 1
                    continue
                payload = {k: v for k, v in row.items() if k not in {"id", "kind", "service", "timestamp"}}
                encoded = json.dumps(payload)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                key = f'{project["id"]}:{dataset}:{row["source_id"]}'
                if conn.execute("SELECT 1 FROM sentry_source_keys WHERE collection_id=? AND source_key=? AND content_digest=?", (job["collection_id"], key, digest)).fetchone():
                    summary["duplicates"] += 1
                    continue
                if summary["observations"] >= MAX_RECORDS or summary["input_bytes"] + len(encoded.encode()) > MAX_BYTES:
                    summary["limit_reached"] = True
                    break
                if row["service"] not in summary["services"] and len(summary["services"]) >= 32:
                    summary["limit_reached"] = True
                    break
                if conn.execute("SELECT 1 FROM sentry_source_keys WHERE collection_id=? AND source_key=?", (job["collection_id"], key)).fetchone():
                    summary["conflicting_ids"] += 1
                conn.execute("INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(?,?,?,?,?,?,?)",
                    (row["id"], job["user_id"], row["kind"], row["service"], row["timestamp"], encoded, job["collection_id"]))
                conn.execute("INSERT INTO sentry_source_keys VALUES(?,?,?,?)", (job["collection_id"], key, digest, row["id"]))
                summary["observations"] += 1
                summary["input_bytes"] += len(encoded.encode())
                summary["redactions"] += changes
                summary["truncated_records"] += int(truncated)
                summary["missing_timestamps"] += int(row["timestamp"] is None)
                summary["missing_services"] += int(row["service"] == "unknown")
                summary["services"] = sorted(set(summary["services"]) | {row["service"]})
                summary["kinds"] = sorted(set(summary["kinds"]) | {row["kind"]})
            conn.execute("UPDATE collections SET summary=? WHERE id=?", (json.dumps(summary), job["collection_id"]))

    async def run(self, job):
        try:
            await asyncio.wait_for(self._run(job), timeout=IMPORT_SECONDS)
        except TimeoutError:
            with db.connection() as conn:
                summary = json.loads(conn.execute("SELECT summary FROM collections WHERE id=?", (job["collection_id"],)).fetchone()[0])
                message = "Import exceeded its five-minute deadline. Retry to create a new snapshot."
                changed = conn.execute("UPDATE sentry_imports SET state=?,error=? WHERE id=? AND state='interrupted'",
                    ("partial" if summary["observations"] else "failed", message, job["id"])).rowcount
                if changed:
                    summary["coverage"] = message
                    conn.execute("UPDATE collections SET summary=? WHERE id=?", (json.dumps(summary), job["collection_id"]))

    async def _run(self, job):
        summary = json.loads(db.one("SELECT summary FROM collections WHERE id=?", (job["collection_id"],))["summary"])
        error, final = None, "completed"
        try:
            self.alive(job)
            self.progress(job, summary)
            connection = db.one("SELECT * FROM sentry_connections WHERE id=?", (job["connection_id"],))
            options = json.loads(job["parameters"])
            token = await self.client.token(connection["id"])
            projects = await self.client.projects(connection["organization"], connection["region"], token)
            projects = {p["id"]: p for p in projects}
            if any(p not in projects for p in options["projects"]):
                raise SentryError("A selected project is no longer authorized. Check the connection and start a new import.", 403)
            streams = [{"project": projects[p], "dataset": d, "cursor": None, "seen": set(), "pages": 0}
                       for p in options["projects"] for d in options["datasets"]]
            for stream in streams:
                summary["datasets"][stream["project"]["slug"] + ":" + stream["dataset"]] = {"state": "pending", "received": 0}
            deadline = time.monotonic() + 300
            requests = 0
            while streams and not summary.get("limit_reached"):
                stream = streams.pop(0)
                key = stream["project"]["slug"] + ":" + stream["dataset"]
                coverage = summary["datasets"][key]
                if requests >= 150:
                    summary["limit_reached"] = True
                    break
                path, params = query_spec(connection, stream["project"], stream["dataset"], options, stream["cursor"])
                try:
                    data, link = await self.page(job, connection, path, params, summary, deadline)
                    requests += 1
                    records = data if stream["dataset"] == "errors" else data.get("data") if isinstance(data, dict) else None
                    if not isinstance(records, list):
                        raise SentryError("Sentry returned an invalid evidence page.")
                    self.save(job, connection, stream["project"], stream["dataset"], records, summary)
                    coverage["received"] += len(records)
                    stream["pages"] += 1
                    coverage["pages"] = stream["pages"]
                    cursor = next_cursor(link, stream["cursor"], stream["seen"])
                    coverage.update(state="partial" if cursor or summary.get("limit_reached") else "available" if coverage["received"] else "empty", cursor=cursor)
                    if cursor:
                        stream["cursor"] = cursor
                        stream["seen"].add(cursor)
                        streams.append(stream)
                except SentryError as exc:
                    coverage.update(state="partial" if coverage["received"] else "unavailable" if exc.status in {403, 404} else "error", reason=str(exc))
                    if exc.status == 401:
                        raise
                self.progress(job, summary)
            if summary.get("limit_reached") or summary["malformed_records"] or summary["truncated_records"] or any(c["state"] not in {"available", "empty"} for c in summary["datasets"].values()):
                final = "partial" if summary["observations"] else "failed"
        except asyncio.CancelledError:
            final, error = "interrupted", "Server stopped. Retry to create a new snapshot."
            raise
        except (SentryError, TimeoutError) as exc:
            final, error = "partial" if summary["observations"] else "failed", str(exc) or "Import exceeded its five-minute deadline."
        except Exception:
            final, error = "partial" if summary["observations"] else "failed", "Import failed unexpectedly. Saved observations remain available."
        finally:
            summary["coverage"] = "Bounded Sentry snapshot; API retention, sampling and dataset access may limit coverage. " + (error or final + ".")
            with db.connection() as conn:
                changed = conn.execute("UPDATE sentry_imports SET state=?,completed_at=?,error=?,progress=? WHERE id=? AND state IN ('queued','running','rate_limited')",
                    (final, db.now(), error, json.dumps({"observations": summary["observations"], "datasets": summary["datasets"]}), job["id"])).rowcount
                if changed:
                    conn.execute("UPDATE collections SET summary=? WHERE id=?", (json.dumps(summary), job["collection_id"]))
