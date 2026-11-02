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
