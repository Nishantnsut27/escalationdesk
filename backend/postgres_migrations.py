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

from .signatures_v1 import migration_sql

EVIDENCE_ANALYSIS = """
CREATE TABLE error_signatures(version TEXT NOT NULL,id TEXT NOT NULL,family TEXT NOT NULL,
 label TEXT NOT NULL,pattern TEXT,PRIMARY KEY(version,id));
""" + migration_sql() + """
INSERT INTO error_signatures VALUES('signatures-v1','unknown','unknown','Unclassified',NULL),
 ('signatures-v1','mixed','mixed','Multiple error families',NULL);
ALTER TABLE evidence_index ADD COLUMN request_id TEXT;
ALTER TABLE evidence_index ADD COLUMN span_id TEXT;
ALTER TABLE evidence_index ADD COLUMN parent_id TEXT;
ALTER TABLE evidence_index ADD COLUMN search_text TEXT;
CREATE TABLE evidence_error_tags(evidence_id TEXT NOT NULL REFERENCES evidence(id),version TEXT NOT NULL,
 signature TEXT NOT NULL,family TEXT NOT NULL,PRIMARY KEY(evidence_id,version,signature),
 FOREIGN KEY(version,signature) REFERENCES error_signatures(version,id));
CREATE INDEX evidence_request ON evidence_index(request_id,observed_at);
CREATE INDEX evidence_span_parent ON evidence_index(trace_id,span_id,parent_id);
CREATE INDEX evidence_owner_scope ON evidence(user_id,collection_id,kind,service);
CREATE INDEX evidence_owner_run ON evidence(user_id,run_id,kind,service);
CREATE INDEX evidence_signature ON evidence_error_tags(version,signature,evidence_id);
ALTER TABLE investigations ADD COLUMN stage TEXT;
ALTER TABLE investigations ADD COLUMN supplied_excerpts TEXT;
ALTER TABLE supplements ADD COLUMN stage TEXT;
ALTER TABLE supplements ADD COLUMN supplied_excerpts TEXT;
CREATE FUNCTION source_timestamp(value TEXT) RETURNS TIMESTAMPTZ LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
 IF value IS NULL OR value !~ '[Tt ][0-9]{2}:[0-9]{2}' OR value !~ '([zZ]|[+-][0-9]{2}(:?[0-9]{2})?)$' THEN
  RETURN NULL;
 END IF;
 RETURN value::timestamptz;
 EXCEPTION WHEN others THEN RETURN NULL;
END $$;
CREATE FUNCTION observation_signature_text(document JSONB) RETURNS TEXT LANGUAGE SQL IMMUTABLE AS $$
 SELECT COALESCE(NULLIF(concat_ws(E'\\n',document->>'message',document->>'exception_type',
 document->>'error_type',document->>'error',document->>'stack_trace',document->>'exception'),''),document->>'original','')
$$;
CREATE FUNCTION index_failure_tags(identifier TEXT,document JSONB) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO evidence_error_tags(evidence_id,version,signature,family)
 SELECT identifier,s.version,s.id,s.family FROM error_signatures s WHERE s.version='signatures-v1'
 AND (observation_signature_text(document) ~* s.pattern OR (s.id='unhandled_exception' AND document->>'exception_handled'='false'))
 ON CONFLICT DO NOTHING;
 IF NOT EXISTS (SELECT 1 FROM evidence_error_tags WHERE evidence_id=identifier AND version='signatures-v1') THEN
  INSERT INTO evidence_error_tags VALUES(identifier,'signatures-v1','unknown','unknown');
 ELSIF (SELECT COUNT(DISTINCT family) FROM evidence_error_tags WHERE evidence_id=identifier AND version='signatures-v1')>1 THEN
  INSERT INTO evidence_error_tags VALUES(identifier,'signatures-v1','mixed','mixed') ON CONFLICT DO NOTHING;
 END IF;
END $$;
CREATE OR REPLACE FUNCTION index_observation() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE arrival BIGINT; stamp TIMESTAMPTZ; line BIGINT; document JSONB; severity TEXT;
BEGIN
 document := NEW.payload::jsonb;
 stamp := source_timestamp(NEW.timestamp);
 BEGIN line := COALESCE((document->>'source_line')::bigint,0); EXCEPTION WHEN others THEN line := 0; END;
 severity := lower(COALESCE(document->>'level','unknown'));
 severity := CASE WHEN severity IN ('fatal','critical') THEN 'error' WHEN severity='warn' THEN 'warning'
 WHEN severity IN ('trace','debug','info','warning','error') THEN severity ELSE 'unknown' END;
 INSERT INTO evidence_index(evidence_id,observed_at,trace_id,level,message,source_line,request_id,span_id,parent_id,search_text)
 VALUES(NEW.id,stamp,NULLIF(document->>'trace_id',''),severity,document->>'message',line,
 NULLIF(document->>'request_id',''),NULLIF(document->>'span_id',''),NULLIF(COALESCE(document->>'parent_id',document->>'parent_span_id'),''),
 concat_ws(E'\\n',observation_signature_text(document),document->>'original'));
 PERFORM index_failure_tags(NEW.id,document);
 UPDATE arrival_clock SET value=value+1 WHERE id=1 RETURNING value INTO arrival;
 INSERT INTO evidence_arrivals VALUES(arrival,NEW.id);
 RETURN NEW;
END $$;
UPDATE evidence_index i SET observed_at=source_timestamp(e.timestamp),request_id=NULLIF(e.payload::jsonb->>'request_id',''),span_id=NULLIF(e.payload::jsonb->>'span_id',''),
 parent_id=NULLIF(COALESCE(e.payload::jsonb->>'parent_id',e.payload::jsonb->>'parent_span_id'),''),
 search_text=concat_ws(E'\\n',observation_signature_text(e.payload::jsonb),e.payload::jsonb->>'original'),
 level=CASE WHEN lower(i.level) IN ('fatal','critical') THEN 'error' WHEN lower(i.level)='warn' THEN 'warning'
 WHEN lower(i.level) IN ('trace','debug','info','warning','error') THEN lower(i.level) ELSE 'unknown' END
 FROM evidence e WHERE e.id=i.evidence_id;
SELECT index_failure_tags(id,payload::jsonb) FROM evidence;
"""

from .signatures import migration_sql as migration_sql_v2

# Migration 3 remains byte-for-byte unchanged. Both versions are derived indexes;
# original evidence payloads, reports, source clocks and ownership are untouched.
SIGNATURES_V2 = migration_sql_v2() + """
INSERT INTO error_signatures VALUES('signatures-v2','unknown','unknown','Unclassified',NULL),
 ('signatures-v2','mixed','mixed','Multiple error families',NULL);
ALTER FUNCTION index_failure_tags(TEXT,JSONB) RENAME TO index_failure_tags_v1;
""" + EVIDENCE_ANALYSIS[EVIDENCE_ANALYSIS.index("CREATE FUNCTION index_failure_tags("):EVIDENCE_ANALYSIS.index("CREATE OR REPLACE FUNCTION index_observation()")].replace("index_failure_tags(", "index_failure_tags_v2(").replace("signatures-v1", "signatures-v2") + """
CREATE FUNCTION index_failure_tags(identifier TEXT,document JSONB) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
 PERFORM index_failure_tags_v1(identifier,document);
 PERFORM index_failure_tags_v2(identifier,document);
END $$;
SELECT index_failure_tags_v2(id,payload::jsonb) FROM evidence;
"""

MIGRATIONS = ((1, SCHEMA), (2, ACCOUNT_EMAIL), (3, EVIDENCE_ANALYSIS), (4, SIGNATURES_V2))


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
