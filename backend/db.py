import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DATA
from .migrations import migrate_collections

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

@contextmanager
def connection():
    conn = sqlite3.connect(DATA / "platform.sqlite3", timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init():
    DATA.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES(1);
        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
            digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), scenario TEXT NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL, ended_at TEXT,
            requests INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, error TEXT);
        CREATE TABLE IF NOT EXISTS incidents(
            id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL REFERENCES runs(id),
            user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT);
        CREATE TABLE IF NOT EXISTS evidence(
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
            user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
            service TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS evidence_run ON evidence(run_id, timestamp);
        CREATE INDEX IF NOT EXISTS incidents_owner ON incidents(user_id, created_at);
        CREATE TABLE IF NOT EXISTS investigations(
            id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id),
            user_id TEXT NOT NULL REFERENCES users(id), engine TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, completed_at TEXT, report TEXT, activity TEXT NOT NULL DEFAULT '[]',
            error TEXT, usage TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS supplements(
            id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL REFERENCES investigations(id),
            user_id TEXT NOT NULL REFERENCES users(id), state TEXT NOT NULL,
            created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
            result TEXT, source_ids TEXT NOT NULL DEFAULT '[]',
            usage TEXT NOT NULL DEFAULT '{}', error TEXT);
        CREATE INDEX IF NOT EXISTS supplements_parent ON supplements(investigation_id, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS supplements_active ON supplements(investigation_id)
            WHERE state IN ('queued','running','quota_waiting');
        INSERT OR IGNORE INTO schema_version VALUES(2);
        """)
        migrate_collections(conn)
        for row in conn.execute("SELECT id, usage FROM investigations WHERE state IN ('queued','running','quota_waiting')").fetchall():
            usage = json.loads(row["usage"])
            triage = usage.get("triage")
            if triage and triage.get("state") in {"pending", "running"}:
                triage.update(state="interrupted", completed_at=now(), reason="Server restarted before triage finished.")
                conn.execute("UPDATE investigations SET usage=? WHERE id=?", (json.dumps(usage), row["id"]))
        conn.execute("UPDATE supplements SET state='interrupted', completed_at=?, error='Server restarted. Retry further analysis to create a new retained attempt.' WHERE state IN ('queued','running','quota_waiting')", (now(),))
        conn.execute("UPDATE investigations SET state='interrupted', error='Server restarted. Start a new investigation.' WHERE state IN ('queued','running','quota_waiting')")
        conn.execute("UPDATE runs SET state='interrupted', ended_at=? WHERE state IN ('running','starting','resetting')", (now(),))
        conn.execute("UPDATE incidents SET state='interrupted' WHERE state='open'")

def one(sql, args=()):
    with connection() as conn:
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row else None

def all_rows(sql, args=()):
    with connection() as conn:
        return [dict(row) for row in conn.execute(sql, args)]

def execute(sql, args=()):
    with connection() as conn:
        conn.execute(sql, args)

def investigation_scope(investigation):
    collection_id = investigation.get("collection_id")
    if collection_id:
        return None, collection_id
    incident = one("SELECT run_id FROM incidents WHERE id=? AND user_id=?",
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
    sql = f"SELECT * FROM evidence WHERE {field}=? AND user_id=?"
    args = [scope, owner]
    for key, value in (("kind", kind), ("service", service)):
        if value:
            sql += f" AND {key}=?"
            args.append(value)
    if trace_id:
        sql += " AND json_extract(payload, '$.trace_id')=?"
        args.append(trace_id)
    if level:
        sql += " AND json_extract(payload, '$.level')=?"
        args.append(level)
    if query:
        sql += " AND instr(lower(COALESCE(json_extract(payload, '$.message'),'')), lower(?))>0"
        args.append(query)
    for value, operator in ((start_time, ">="), (end_time, "<=")):
        if value:
            sql += f" AND julianday(timestamp){operator}julianday(?)"
            args.append(value)
    rows = all_rows(sql + " ORDER BY timestamp IS NULL, julianday(timestamp), COALESCE(json_extract(payload, '$.source_line'),0), id LIMIT ? OFFSET ?",
                    (*args, min(limit, 201), offset))
    return [public_evidence(row) for row in rows]

