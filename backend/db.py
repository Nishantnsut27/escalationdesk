"""Platform PostgreSQL storage. Reference workloads keep their own SQLite stores."""
import json
import os
import uuid
from datetime import datetime, timezone
from .config import DATA
from . import postgres
from .postgres_migrations import migrate

Error = postgres.psycopg.Error
IntegrityError = postgres.psycopg.IntegrityError
_target = None
_schema = None

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

def target():
    if _target:
        return _target
    if os.environ.get("DESK_TEST_SCHEMA"):
        return postgres.test_target()
    return os.environ.get("DESK_DATABASE_URL", "")

def schema():
    return _schema or os.environ.get("DESK_TEST_SCHEMA", "public")

def connection():
    return postgres.connection(target(),schema())

lock = postgres.lock
close = postgres.close

def init():
    DATA.mkdir(parents=True,exist_ok=True)
    with connection() as conn:
        if schema()=="public" and (DATA/"platform.sqlite3").exists():
            transferred=conn.execute("SELECT to_regclass('storage_transfers') AS name").fetchone()["name"]
            if not transferred or not conn.execute("SELECT 1 FROM storage_transfers LIMIT 1").fetchone():
                raise ValueError("Migrate the retained SQLite backup before starting PostgreSQL. Automatic fallback is disabled.")
        migrate(conn)
        for row in conn.execute("SELECT c.id,c.summary FROM collections c JOIN sentry_imports s ON s.collection_id=c.id WHERE s.state IN ('queued','running','rate_limited')").fetchall():
            summary=json.loads(row["summary"])
            summary["coverage"]="Server restarted during Sentry import. Retained evidence is partial; retry creates a new snapshot."
            conn.execute("UPDATE collections SET summary=%s WHERE id=%s",(json.dumps(summary),row["id"]))
        conn.execute("UPDATE sentry_imports SET state='interrupted',completed_at=%s,error='Server restarted. Retry to create a new snapshot.' WHERE state IN ('queued','running','rate_limited')",(now(),))
        for row in conn.execute("SELECT id,usage FROM investigations WHERE state IN ('queued','running','quota_waiting')").fetchall():
            usage=json.loads(row["usage"])
            triage=usage.get("triage")
            if triage and triage.get("state") in {"pending","running"}:
                triage.update(state="interrupted",completed_at=now(),reason="Server restarted before triage finished.")
                conn.execute("UPDATE investigations SET usage=%s WHERE id=%s",(json.dumps(usage),row["id"]))
        conn.execute("UPDATE supplements SET state='interrupted',completed_at=%s,error='Server restarted. Retry further analysis to create a new retained attempt.' WHERE state IN ('queued','running','quota_waiting')",(now(),))
        conn.execute("UPDATE investigations SET state='interrupted',error='Server restarted. Start a new investigation.' WHERE state IN ('queued','running','quota_waiting')")
        conn.execute("UPDATE runs SET state='interrupted',ended_at=%s WHERE state IN ('running','starting','resetting')",(now(),))
        conn.execute("UPDATE incidents SET state='interrupted' WHERE state='open'")

def one(statement,args=()):
    with connection() as conn:
        row=conn.execute(statement,args).fetchone()
        return dict(row) if row else None

def all_rows(statement,args=()):
    with connection() as conn:
        return [dict(row) for row in conn.execute(statement,args)]

def execute(statement,args=()):
    with connection() as conn:
        conn.execute(statement,args)

def investigation_scope(investigation):
    collection_id = investigation.get("collection_id")
    if collection_id:
        return None, collection_id
    incident = one("SELECT run_id FROM incidents WHERE id=%s AND user_id=%s",
                   (investigation["incident_id"], investigation["user_id"]))
    if not incident:
        raise ValueError("Investigation evidence scope is unavailable.")
    return incident["run_id"], None


def public_evidence(row):
    return {**json.loads(row["payload"]),
            **{key: row[key] for key in ("id", "kind", "service", "timestamp") }}


def evidence(run_id, owner, kind=None, service=None, limit=100, trace_id=None,
             start_time=None, end_time=None, *, collection_id=None, offset=0, query=None, level=None):
    field, scope = ("collection_id", collection_id) if collection_id else ("run_id", run_id)
    statement = f"SELECT e.* FROM evidence e JOIN evidence_index i ON i.evidence_id=e.id WHERE e.{field}=%s AND e.user_id=%s"
    args = [scope, owner]
    for key,value in (("kind",kind),("service",service)):
        if value:
            statement += f" AND e.{key}=%s"
            args.append(value)
    for key,value in (("trace_id",trace_id),("level",level)):
        if value:
            statement += f" AND i.{key}=%s"
            args.append(value)
    if query:
        statement += " AND strpos(lower(COALESCE(i.message,'')),lower(%s))>0"
        args.append(query)
    for value,operator in ((start_time,">="),(end_time,"<=")):
        if value:
            statement += f" AND i.observed_at{operator}%s::timestamptz"
            args.append(value)
    rows=all_rows(statement+" ORDER BY i.observed_at NULLS LAST,i.source_line,e.id LIMIT %s OFFSET %s",
                  (*args,min(limit,201),offset))
    return [public_evidence(row) for row in rows]
