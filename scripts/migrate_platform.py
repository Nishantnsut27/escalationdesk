"""Back up and migrate platform SQLite to an explicit PostgreSQL target."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import config, postgres
from backend.postgres_migrations import migrate
from psycopg import sql

TABLES=("schema_version","users","collections","runs","incidents","evidence","investigations","supplements",
        "account_roles","sessions","demo_datasets","demo_cases","sentry_connections","sentry_oauth_states",
        "sentry_deliveries","sentry_imports","sentry_source_keys","evidence_arrivals","account_security","auth_tokens","email_jobs","email_deliveries")


def digest_rows(rows):
    parts=sorted(json.dumps(dict(row),sort_keys=True,ensure_ascii=False,separators=(",", ":")) for row in rows)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def backup(source, destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if not source.is_file() or source==destination or destination.exists():
        raise ValueError("Use an existing source and a new, distinct backup path.")
    destination.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer)
        if writer.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or writer.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("The SQLite backup failed integrity checks.")
    return destination


def transfer(source, target, schema="public"):
    source=Path(source).resolve()
    if not source.is_file():
        raise ValueError("The migration source must be an existing SQLite backup.")
    with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)) as reader:
        reader.row_factory=sqlite3.Row
        present={r[0] for r in reader.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not 5 <= reader.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] <= 6:
            raise ValueError("Use a schema version 5 or 6 platform backup.")
        if reader.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Source relationships are invalid.")
        unknown=present-set(TABLES)-{"sqlite_sequence"}
        if unknown:
            raise ValueError("Unrecognized source tables; update the migration before copying.")
        with postgres.connection(target,schema) as conn:
            migrate(conn)
            postgres.lock(conn,"platform-writes")
            for table in TABLES:
                if conn.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))).fetchone():
                    raise ValueError("Migration requires an empty target schema. Existing data will never be overwritten.")
            conn.execute("SET LOCAL statement_timeout='120s'")
            results={}
            for table in TABLES:
                if table not in present:
                    continue
                rows=reader.execute('SELECT * FROM "'+table+'"').fetchall()
                columns=[column[1] for column in reader.execute('PRAGMA table_info("'+table+'")')]
                if table=="evidence_arrivals":
                    # The insertion trigger built query indexes and temporary
                    # arrivals. Replace only those new arrivals with exact IDs.
                    conn.execute("DELETE FROM evidence_arrivals")
                query=sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(sql.Identifier(table),
                    sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
                with conn.cursor() as cursor:
                    cursor.executemany(query,[tuple(row) for row in rows])
                restored=conn.execute(sql.SQL("SELECT {} FROM {}").format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table))).fetchall()
                before,after=digest_rows(rows),digest_rows(restored)
                if len(rows)!=len(restored) or before!=after:
                    raise ValueError("Migration preservation check failed for "+table)
                results[table]={"count":len(rows),"sha256":before,"exact_match":True}
            conn.execute("UPDATE arrival_clock SET value=(SELECT COALESCE(MAX(sequence),0) FROM evidence_arrivals) WHERE id=1")
            conn.execute("CREATE TABLE storage_transfers(source_digest TEXT PRIMARY KEY,verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),manifest TEXT NOT NULL)")
            source_digest=hashlib.sha256(source.read_bytes()).hexdigest()
            conn.execute("INSERT INTO storage_transfers(source_digest,manifest) VALUES(%s,%s)",(source_digest,json.dumps(results,sort_keys=True)))
            # Deferred and immediate PostgreSQL foreign keys must all validate.
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        return {"source_sha256":source_digest,"tables":results,"schema":schema}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("backup","rehearse","cutover"))
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True,help="New backup file, or a JSON verification manifest.")
    parser.add_argument("--writes-paused",action="store_true")
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output path; earlier backups and migration manifests must be retained.")
    if args.action=="backup":
        print(json.dumps({"backup":str(backup(args.source,args.output))})); return
    if args.action=="rehearse":
        target=postgres.test_target()
        schema="operator_rehearsal_"+os.urandom(6).hex()
        with postgres.connection(target) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    else:
        if not args.writes_paused or (config.DATA/"runtime.json").exists():
            raise ValueError("Stop this workspace runtime and use --writes-paused before cutover.")
        target=os.environ.get("DESK_DATABASE_URL","")
        schema="public"
    result=transfer(args.source,target,schema)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({"verified":True,"tables":len(result["tables"]),"manifest":str(args.output),"schema":schema}))


if __name__=="__main__":
    try:
        main()
    except Exception as error:
        # Connection errors can include hosts/users; never echo driver messages.
        raise SystemExit(str(error) if isinstance(error,ValueError) else "Migration failed; transaction rolled back. Inspect target access and rerun with a fresh isolated schema.") from None
