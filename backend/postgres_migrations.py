"""Transactional platform migrations. Original JSON and timestamps remain text."""
import hashlib
import re
from .postgres import lock

SCHEMA = """
CREATE TABLE schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
 password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE sessions(digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires DOUBLE PRECISION NOT NULL);
CREATE TABLE collections(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
 symptom TEXT NOT NULL, filename TEXT, source_type TEXT NOT NULL, created_at TEXT NOT NULL,
 fingerprint TEXT NOT NULL, summary TEXT NOT NULL, UNIQUE(user_id,fingerprint));
CREATE TABLE runs(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),scenario TEXT NOT NULL,
 state TEXT NOT NULL,created_at TEXT NOT NULL,ended_at TEXT,requests INTEGER DEFAULT 0,failures INTEGER DEFAULT 0,
 error TEXT,collection_id TEXT REFERENCES collections(id),stage TEXT);
CREATE TABLE incidents(id TEXT PRIMARY KEY,run_id TEXT UNIQUE NOT NULL REFERENCES runs(id),
 user_id TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,resolved_at TEXT);
CREATE TABLE evidence(id TEXT PRIMARY KEY,run_id TEXT REFERENCES runs(id),user_id TEXT NOT NULL REFERENCES users(id),
 kind TEXT NOT NULL,service TEXT NOT NULL,timestamp TEXT,payload TEXT NOT NULL,collection_id TEXT REFERENCES collections(id),
 CHECK ((run_id IS NULL) <> (collection_id IS NULL)));
CREATE TABLE investigations(id TEXT PRIMARY KEY,incident_id TEXT REFERENCES incidents(id),user_id TEXT NOT NULL REFERENCES users(id),
 engine TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT,report TEXT,
 activity TEXT NOT NULL DEFAULT '[]',error TEXT,usage TEXT NOT NULL DEFAULT '{}',collection_id TEXT REFERENCES collections(id),
 CHECK ((incident_id IS NULL) <> (collection_id IS NULL)));
CREATE TABLE supplements(id TEXT PRIMARY KEY,investigation_id TEXT NOT NULL REFERENCES investigations(id),
 user_id TEXT NOT NULL REFERENCES users(id),state TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT,
 result TEXT,source_ids TEXT NOT NULL DEFAULT '[]',usage TEXT NOT NULL DEFAULT '{}',error TEXT);
CREATE TABLE account_roles(user_id TEXT PRIMARY KEY REFERENCES users(id),role TEXT NOT NULL CHECK(role='demo'));
CREATE TABLE arrival_clock(id INTEGER PRIMARY KEY CHECK(id=1),value BIGINT NOT NULL);
INSERT INTO arrival_clock VALUES(1,0);
CREATE TABLE evidence_arrivals(sequence BIGINT PRIMARY KEY,evidence_id TEXT UNIQUE NOT NULL REFERENCES evidence(id));
CREATE TABLE evidence_index(evidence_id TEXT PRIMARY KEY REFERENCES evidence(id),observed_at TIMESTAMPTZ,
 trace_id TEXT,level TEXT,message TEXT,source_line BIGINT NOT NULL DEFAULT 0);
CREATE FUNCTION index_observation() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE arrival BIGINT; stamp TIMESTAMPTZ; line BIGINT; document JSONB;
BEGIN
 document := NEW.payload::jsonb;
 BEGIN stamp := NEW.timestamp::timestamptz; EXCEPTION WHEN others THEN stamp := NULL; END;
 BEGIN line := COALESCE((document->>'source_line')::bigint,0); EXCEPTION WHEN others THEN line := 0; END;
 INSERT INTO evidence_index VALUES(NEW.id,stamp,document->>'trace_id',document->>'level',document->>'message',line);
 -- A transactional counter orders committed arrivals. A sequence alone could
 -- let a later transaction commit first and permanently skip an earlier row.
 UPDATE arrival_clock SET value=value+1 WHERE id=1 RETURNING value INTO arrival;
 INSERT INTO evidence_arrivals VALUES(arrival,NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER evidence_arrival AFTER INSERT ON evidence FOR EACH ROW EXECUTE FUNCTION index_observation();
CREATE TABLE demo_datasets(version TEXT PRIMARY KEY,digest TEXT NOT NULL,installed_at TEXT NOT NULL);
CREATE TABLE demo_cases(slug TEXT PRIMARY KEY,version TEXT NOT NULL REFERENCES demo_datasets(version),position INTEGER NOT NULL,
 metadata TEXT NOT NULL,collection_id TEXT UNIQUE NOT NULL REFERENCES collections(id),investigation_id TEXT UNIQUE NOT NULL REFERENCES investigations(id));
CREATE TABLE sentry_connections(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),installation_id TEXT UNIQUE NOT NULL,
 organization TEXT NOT NULL,region TEXT NOT NULL,credentials TEXT,expires_at DOUBLE PRECISION NOT NULL DEFAULT 0,state TEXT NOT NULL,
 projects TEXT NOT NULL DEFAULT '[]',capabilities TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,checked_at TEXT,error TEXT,generation INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sentry_oauth_states(digest TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),session_digest TEXT NOT NULL,
 region TEXT NOT NULL,expires DOUBLE PRECISION NOT NULL);
CREATE TABLE sentry_deliveries(digest TEXT PRIMARY KEY,installation_id TEXT NOT NULL,action TEXT NOT NULL,received_at TEXT NOT NULL);
CREATE TABLE sentry_imports(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),connection_id TEXT NOT NULL REFERENCES sentry_connections(id),
 collection_id TEXT UNIQUE NOT NULL REFERENCES collections(id),fingerprint TEXT NOT NULL,parameters TEXT NOT NULL,state TEXT NOT NULL,
 created_at TEXT NOT NULL,completed_at TEXT,progress TEXT NOT NULL DEFAULT '{}',error TEXT,retry_of TEXT REFERENCES sentry_imports(id));
CREATE TABLE sentry_source_keys(collection_id TEXT NOT NULL REFERENCES collections(id),source_key TEXT NOT NULL,content_digest TEXT NOT NULL,
 evidence_id TEXT NOT NULL REFERENCES evidence(id),PRIMARY KEY(collection_id,source_key,content_digest));
CREATE INDEX evidence_run ON evidence(run_id,timestamp);
CREATE INDEX evidence_collection ON evidence(collection_id,timestamp);
CREATE INDEX evidence_trace ON evidence_index(trace_id,observed_at);
CREATE INDEX collections_owner ON collections(user_id,created_at);
CREATE INDEX incidents_owner ON incidents(user_id,created_at);
CREATE INDEX supplements_parent ON supplements(investigation_id,created_at);
CREATE UNIQUE INDEX supplements_active ON supplements(investigation_id) WHERE state IN ('queued','running','quota_waiting');
CREATE UNIQUE INDEX collection_investigation_active ON investigations(collection_id) WHERE state IN ('queued','running','quota_waiting');
CREATE UNIQUE INDEX sentry_import_active ON sentry_imports(user_id) WHERE state IN ('queued','running','rate_limited');
CREATE INDEX sentry_import_owner ON sentry_imports(user_id,created_at);
"""

ACCOUNT_EMAIL = """
CREATE TABLE account_security(user_id TEXT PRIMARY KEY REFERENCES users(id),verified_at TEXT);
CREATE TABLE auth_tokens(digest TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),created_at DOUBLE PRECISION NOT NULL,
 expires DOUBLE PRECISION NOT NULL,consumed_at DOUBLE PRECISION);
CREATE INDEX auth_tokens_owner ON auth_tokens(user_id,purpose,created_at);
CREATE TABLE email_jobs(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),kind TEXT NOT NULL,
 dedupe_key TEXT UNIQUE NOT NULL,payload TEXT,created_at DOUBLE PRECISION NOT NULL,expires DOUBLE PRECISION NOT NULL,
 state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt DOUBLE PRECISION NOT NULL,
 lease_until DOUBLE PRECISION,provider_id TEXT UNIQUE,last_error TEXT,delivery_status TEXT,delivery_at TEXT);
CREATE INDEX email_jobs_ready ON email_jobs(state,next_attempt);
CREATE TABLE email_deliveries(id TEXT PRIMARY KEY,provider_id TEXT NOT NULL,event_type TEXT NOT NULL,
 occurred_at TEXT NOT NULL,received_at DOUBLE PRECISION NOT NULL);
"""

MIGRATIONS = ((1, SCHEMA), (2, ACCOUNT_EMAIL))


def migrate(conn):
    lock(conn, "platform-migrations")
    conn.execute("CREATE TABLE IF NOT EXISTS platform_migrations(version INTEGER PRIMARY KEY,digest TEXT NOT NULL,applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    for version, statement in MIGRATIONS:
        digest = hashlib.sha256(statement.encode()).hexdigest()
        previous = conn.execute("SELECT digest FROM platform_migrations WHERE version=%s", (version,)).fetchone()
        if previous:
            if previous['digest'] != digest:
                raise ValueError("An applied PostgreSQL migration has changed.")
            continue
        # Pipeline mode uses the extended query protocol: submit each statement
        # separately while keeping the PL/pgSQL dollar-quoted function intact.
        pending = ""
        statements = []
        for part in re.split(r'(\$\$.*?\$\$)',statement,flags=re.S):
            if part.startswith('$$'):
                pending += part
                continue
            fragments = part.split(';')
            for fragment in fragments[:-1]:
                pending += fragment
                if pending.strip():
                    statements.append(pending)
                pending = ""
            pending += fragments[-1]
        if pending.strip():
            statements.append(pending)
        with conn.pipeline():
            for statement in statements:
                conn.execute(statement)
            conn.execute("INSERT INTO platform_migrations(version,digest) VALUES(%s,%s)", (version,digest))
