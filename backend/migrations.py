"""Version 3 adds collections while preserving legacy report and source identities."""
def migrate_collections(conn):
    if conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 3:
        return
    # SQLite cannot relax NOT NULL with ALTER COLUMN. Rebuild only the two
    # affected tables in one transaction, retaining every legacy field and ID.
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE collections(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            title TEXT NOT NULL, symptom TEXT NOT NULL, filename TEXT,
            source_type TEXT NOT NULL, created_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL, summary TEXT NOT NULL,
            UNIQUE(user_id, fingerprint))""")
        conn.execute("""CREATE TABLE evidence_v3(
            id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id),
            user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
            service TEXT NOT NULL, timestamp TEXT, payload TEXT NOT NULL,
            collection_id TEXT REFERENCES collections(id),
            CHECK ((run_id IS NULL) != (collection_id IS NULL)))""")
        conn.execute("""INSERT INTO evidence_v3(id,run_id,user_id,kind,service,timestamp,payload)
            SELECT id,run_id,user_id,kind,service,timestamp,payload FROM evidence""")
        conn.execute("""CREATE TABLE investigations_v3(
            id TEXT PRIMARY KEY, incident_id TEXT REFERENCES incidents(id),
            user_id TEXT NOT NULL REFERENCES users(id), engine TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, completed_at TEXT, report TEXT,
            activity TEXT NOT NULL DEFAULT '[]', error TEXT, usage TEXT NOT NULL DEFAULT '{}',
            collection_id TEXT REFERENCES collections(id),
            CHECK ((incident_id IS NULL) != (collection_id IS NULL)))""")
        conn.execute("""INSERT INTO investigations_v3(
            id,incident_id,user_id,engine,state,created_at,completed_at,report,activity,error,usage)
            SELECT id,incident_id,user_id,engine,state,created_at,completed_at,report,activity,error,usage
            FROM investigations""")
        conn.execute("DROP TABLE evidence")
        conn.execute("ALTER TABLE evidence_v3 RENAME TO evidence")
        conn.execute("DROP TABLE investigations")
        conn.execute("ALTER TABLE investigations_v3 RENAME TO investigations")
        conn.execute("CREATE INDEX evidence_run ON evidence(run_id, timestamp)")
        conn.execute("CREATE INDEX evidence_collection ON evidence(collection_id, timestamp)")
        conn.execute("CREATE INDEX collections_owner ON collections(user_id, created_at)")
        conn.execute("""CREATE UNIQUE INDEX collection_investigation_active
            ON investigations(collection_id) WHERE state IN ('queued','running','quota_waiting')""")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Collection migration failed its relationship integrity check.")
        conn.execute("INSERT INTO schema_version VALUES(3)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def migrate_demo(conn):
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    if version != 3:
        return
    conn.commit()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Separate role storage preserves the existing users record contract.
        conn.execute("CREATE TABLE account_roles(user_id TEXT PRIMARY KEY REFERENCES users(id), role TEXT NOT NULL CHECK(role='demo'))")
        conn.execute("ALTER TABLE runs ADD COLUMN collection_id TEXT REFERENCES collections(id)")
        conn.execute("ALTER TABLE runs ADD COLUMN stage TEXT")
        conn.execute("CREATE TABLE evidence_arrivals(sequence INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT UNIQUE NOT NULL REFERENCES evidence(id))")
        conn.execute("INSERT INTO evidence_arrivals(evidence_id) SELECT id FROM evidence ORDER BY rowid")
        conn.execute("""CREATE TRIGGER evidence_arrival AFTER INSERT ON evidence BEGIN
            INSERT INTO evidence_arrivals(evidence_id) VALUES(NEW.id); END""")
        conn.execute("CREATE TABLE demo_datasets(version TEXT PRIMARY KEY, digest TEXT NOT NULL, installed_at TEXT NOT NULL)")
        conn.execute("""CREATE TABLE demo_cases(slug TEXT PRIMARY KEY, version TEXT NOT NULL REFERENCES demo_datasets(version),
            position INTEGER NOT NULL, metadata TEXT NOT NULL, collection_id TEXT UNIQUE NOT NULL REFERENCES collections(id),
            investigation_id TEXT UNIQUE NOT NULL REFERENCES investigations(id))""")
        conn.execute("INSERT INTO schema_version VALUES(4)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate_sentry(conn):
    if conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] != 4:
        return
    conn.commit()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE sentry_connections(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            installation_id TEXT UNIQUE NOT NULL, organization TEXT NOT NULL, region TEXT NOT NULL,
            credentials TEXT, expires_at REAL NOT NULL DEFAULT 0, state TEXT NOT NULL,
            projects TEXT NOT NULL DEFAULT '[]', capabilities TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, checked_at TEXT, error TEXT, generation INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE sentry_oauth_states(
            digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            session_digest TEXT NOT NULL, region TEXT NOT NULL, expires REAL NOT NULL)""")
        conn.execute("""CREATE TABLE sentry_deliveries(
            digest TEXT PRIMARY KEY, installation_id TEXT NOT NULL, action TEXT NOT NULL, received_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE sentry_imports(
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            connection_id TEXT NOT NULL REFERENCES sentry_connections(id),
            collection_id TEXT UNIQUE NOT NULL REFERENCES collections(id),
            fingerprint TEXT NOT NULL, parameters TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, completed_at TEXT, progress TEXT NOT NULL DEFAULT '{}',
            error TEXT, retry_of TEXT REFERENCES sentry_imports(id))""")
        conn.execute("CREATE UNIQUE INDEX sentry_import_active ON sentry_imports(user_id) WHERE state IN ('queued','running','rate_limited')")
        conn.execute("CREATE INDEX sentry_import_owner ON sentry_imports(user_id,created_at)")
        conn.execute("""CREATE TABLE sentry_source_keys(
            collection_id TEXT NOT NULL REFERENCES collections(id), source_key TEXT NOT NULL,
            content_digest TEXT NOT NULL, evidence_id TEXT NOT NULL REFERENCES evidence(id),
            PRIMARY KEY(collection_id, source_key, content_digest))""")
        conn.execute("INSERT INTO schema_version VALUES(5)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
