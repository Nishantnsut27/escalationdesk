-- Original platform schema v5. Schema only; all test records are artificial.
CREATE TABLE schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE users(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE sessions(
            digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
CREATE TABLE runs(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), scenario TEXT NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL, ended_at TEXT,
            requests INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, error TEXT, collection_id TEXT REFERENCES collections(id), stage TEXT);
CREATE TABLE incidents(
            id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL REFERENCES runs(id),
            user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE supplements(
            id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL REFERENCES investigations(id),
            user_id TEXT NOT NULL REFERENCES users(id), state TEXT NOT NULL,
            created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
            result TEXT, source_ids TEXT NOT NULL DEFAULT '[]',
            usage TEXT NOT NULL DEFAULT '{}', error TEXT);
CREATE TABLE collections(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            title TEXT NOT NULL, symptom TEXT NOT NULL, filename TEXT,
            source_type TEXT NOT NULL, created_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL, summary TEXT NOT NULL,
            UNIQUE(user_id, fingerprint));
CREATE TABLE "evidence"(
            id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id),
            user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
            service TEXT NOT NULL, timestamp TEXT, payload TEXT NOT NULL,
            collection_id TEXT REFERENCES collections(id),
            CHECK ((run_id IS NULL) != (collection_id IS NULL)));
CREATE TABLE "investigations"(
            id TEXT PRIMARY KEY, incident_id TEXT REFERENCES incidents(id),
            user_id TEXT NOT NULL REFERENCES users(id), engine TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, completed_at TEXT, report TEXT,
            activity TEXT NOT NULL DEFAULT '[]', error TEXT, usage TEXT NOT NULL DEFAULT '{}',
            collection_id TEXT REFERENCES collections(id),
            CHECK ((incident_id IS NULL) != (collection_id IS NULL)));
CREATE TABLE account_roles(user_id TEXT PRIMARY KEY REFERENCES users(id), role TEXT NOT NULL CHECK(role='demo'));
CREATE TABLE evidence_arrivals(sequence INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT UNIQUE NOT NULL REFERENCES evidence(id));
CREATE TABLE demo_datasets(version TEXT PRIMARY KEY, digest TEXT NOT NULL, installed_at TEXT NOT NULL);
CREATE TABLE demo_cases(slug TEXT PRIMARY KEY, version TEXT NOT NULL REFERENCES demo_datasets(version),
            position INTEGER NOT NULL, metadata TEXT NOT NULL, collection_id TEXT UNIQUE NOT NULL REFERENCES collections(id),
            investigation_id TEXT UNIQUE NOT NULL REFERENCES investigations(id));
CREATE TABLE sentry_connections(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            installation_id TEXT UNIQUE NOT NULL, organization TEXT NOT NULL, region TEXT NOT NULL,
            credentials TEXT, expires_at REAL NOT NULL DEFAULT 0, state TEXT NOT NULL,
            projects TEXT NOT NULL DEFAULT '[]', capabilities TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, checked_at TEXT, error TEXT, generation INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sentry_oauth_states(
            digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            session_digest TEXT NOT NULL, region TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE sentry_deliveries(
            digest TEXT PRIMARY KEY, installation_id TEXT NOT NULL, action TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE sentry_imports(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            connection_id TEXT NOT NULL REFERENCES sentry_connections(id),
            collection_id TEXT UNIQUE NOT NULL REFERENCES collections(id),
            fingerprint TEXT NOT NULL, parameters TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, completed_at TEXT, progress TEXT NOT NULL DEFAULT '{}',
            error TEXT, retry_of TEXT REFERENCES sentry_imports(id));
CREATE TABLE sentry_source_keys(
            collection_id TEXT NOT NULL REFERENCES collections(id), source_key TEXT NOT NULL,
            content_digest TEXT NOT NULL, evidence_id TEXT NOT NULL REFERENCES evidence(id),
            PRIMARY KEY(collection_id, source_key, content_digest));
CREATE INDEX incidents_owner ON incidents(user_id, created_at);
CREATE INDEX supplements_parent ON supplements(investigation_id, created_at);
CREATE UNIQUE INDEX supplements_active ON supplements(investigation_id)
            WHERE state IN ('queued','running','quota_waiting');
CREATE INDEX evidence_run ON evidence(run_id, timestamp);
CREATE INDEX evidence_collection ON evidence(collection_id, timestamp);
CREATE INDEX collections_owner ON collections(user_id, created_at);
CREATE UNIQUE INDEX collection_investigation_active
            ON investigations(collection_id) WHERE state IN ('queued','running','quota_waiting');
CREATE UNIQUE INDEX sentry_import_active ON sentry_imports(user_id) WHERE state IN ('queued','running','rate_limited');
CREATE INDEX sentry_import_owner ON sentry_imports(user_id,created_at);
CREATE TRIGGER evidence_arrival AFTER INSERT ON evidence BEGIN
            INSERT INTO evidence_arrivals(evidence_id) VALUES(NEW.id); END;
