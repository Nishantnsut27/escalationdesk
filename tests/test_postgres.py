import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from psycopg.conninfo import conninfo_to_dict

from database import IsolatedDatabase
from backend import auth, db, postgres
from scripts import migrate_platform


class TargetTests(unittest.TestCase):
    def test_tls_and_separate_project_required(self):
        url='postgresql://fixture:unused@ep-fixture-pooler.neon.tech/neondb?sslmode=require'
        options=conninfo_to_dict(postgres.connection_info(url))
        self.assertEqual(options['sslmode'],'verify-full')
        self.assertTrue(Path(options['sslrootcert']).is_file())
        with patch.dict(os.environ,{'DESK_DATABASE_URL':url,'DESK_TEST_DATABASE_URL':url.replace('-pooler','')}):
            with self.assertRaisesRegex(ValueError,'separate'):
                postgres.test_target()
        for target in ('',url.replace('require','disable'),url+'&hostaddr=127.0.0.1'):
            with self.assertRaises(ValueError):
                postgres.connection_info(target)


class PostgreSQLTests(unittest.TestCase):
    def setUp(self):
        self.directory=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.database=self.enterContext(IsolatedDatabase(self.directory))

    def source(self):
        source=self.directory/'legacy.sqlite3'
        self.password_hash=auth.hasher.hash('migration-test-password')
        self.encryption=Fernet(Fernet.generate_key())
        self.credential=self.encryption.encrypt(b'artificial-sentry-credential').decode()
        self.stamp='2025-03-01T12:34:56.123400+05:30'
        self.payload='{ "id": "source", "trace_id": "trace-one", "source_line": 7, "message": "Observed café", "level": "error" }'
        self.report='{ "summary": "Retained assessment", "evidence_ids": ["source"] }'
        with closing(sqlite3.connect(source)) as conn, conn:
            conn.executescript((Path(__file__).parent/'fixtures/platform_v5.sql').read_text(encoding='utf-8'))
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('INSERT INTO schema_version VALUES(5)')
            conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',('owner','Fixture','fixture@example.invalid',self.password_hash,self.stamp))
            conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',('other','Other','other@example.invalid','disabled',self.stamp))
            conn.execute('INSERT INTO sessions VALUES(?,?,?)',(auth.digest('fixture-session'),'owner',2000000000.0))
            conn.execute('INSERT INTO collections VALUES(?,?,?,?,?,?,?,?,?)',('collection','owner','History','Observed errors','fixture.jsonl','sentry',self.stamp,'fingerprint','{"services":["api"],"observations":1}'))
            conn.execute('INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id) VALUES(?,?,?,?,?,?,?)',('source','owner','log','api',self.stamp,self.payload,'collection'))
            conn.execute('INSERT INTO investigations(id,user_id,engine,state,created_at,completed_at,report,collection_id) VALUES(?,?,?,?,?,?,?,?)',('report','owner','groq','completed',self.stamp,self.stamp,self.report,'collection'))
            conn.execute('INSERT INTO supplements(id,investigation_id,user_id,state,created_at,result,source_ids) VALUES(?,?,?,?,?,?,?)',('supplement','report','owner','completed',self.stamp,'{"retained":true}','["source"]'))
            conn.execute('INSERT INTO account_roles VALUES(?,?)',('owner','demo'))
            conn.execute('INSERT INTO demo_datasets VALUES(?,?,?)',('v1','digest',self.stamp))
            conn.execute('INSERT INTO demo_cases VALUES(?,?,?,?,?,?)',('fixture','v1',0,'{"reviewed":true}','collection','report'))
            conn.execute('INSERT INTO sentry_connections(id,user_id,installation_id,organization,region,credentials,state,created_at) VALUES(?,?,?,?,?,?,?,?)',('connection','owner','installation','fixture','eu',self.credential,'connected',self.stamp))
            conn.execute('INSERT INTO sentry_imports(id,user_id,connection_id,collection_id,fingerprint,parameters,state,created_at) VALUES(?,?,?,?,?,?,?,?)',('import','owner','connection','collection','fingerprint','{"normalization_version":3}','completed',self.stamp))
            conn.execute('INSERT INTO sentry_source_keys VALUES(?,?,?,?)',('collection','provider-id','content-digest','source'))
            conn.execute('UPDATE evidence_arrivals SET sequence=97')
        return source

    def test_exact_preservation_restart_and_backup_restore(self):
        source=self.source()
        backup=migrate_platform.backup(source,self.directory/'rollback.sqlite3')
        before=hashlib.sha256(backup.read_bytes()).hexdigest()
        result=migrate_platform.transfer(backup,self.database.target,self.database.schema)
        self.assertEqual(len(result['tables']),18)
        self.assertTrue(all(table['exact_match'] for table in result['tables'].values()))
        postgres.close()
        db.init()
        self.assertEqual(db.one('SELECT payload,timestamp FROM evidence'),{'payload':self.payload,'timestamp':self.stamp})
        self.assertEqual(db.one('SELECT report FROM investigations')['report'],self.report)
        saved=db.one('SELECT password_hash FROM users WHERE id=%s',('owner',))['password_hash']
        self.assertEqual(saved,self.password_hash)
        self.assertTrue(auth.hasher.verify(saved,'migration-test-password'))
        credential=db.one('SELECT credentials FROM sentry_connections')['credentials']
        self.assertEqual(credential,self.credential)
        self.assertEqual(self.encryption.decrypt(credential.encode()),b'artificial-sentry-credential')
        self.assertEqual(db.evidence(None,'other',collection_id='collection'),[])
        self.assertEqual(db.evidence(None,'owner',collection_id='collection',trace_id='trace-one')[0]['id'],'source')
        self.assertEqual(db.one('SELECT sequence FROM evidence_arrivals')['sequence'],97)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM email_jobs')['n'],0)
        self.assertEqual(db.one('SELECT COUNT(*) n FROM account_security')['n'],0)
        with self.assertRaisesRegex(ValueError,'empty target'):
            migrate_platform.transfer(backup,self.database.target,self.database.schema)
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),before)
        restored=migrate_platform.backup(backup,self.directory/'restored.sqlite3')
        with closing(sqlite3.connect(restored)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(conn.execute('SELECT report FROM investigations').fetchone()[0],self.report)
            self.assertEqual(conn.execute('SELECT payload FROM evidence').fetchone()[0],self.payload)
            conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',('rollback-user','Rollback','rollback@example.invalid','disabled',self.stamp))

    def test_failed_transfer_rolls_back_schema_and_source_is_reusable(self):
        source=self.source()
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        real=migrate_platform.digest_rows
        calls=0
        def mismatch(rows):
            nonlocal calls
            calls+=1
            return 'injected-mismatch' if calls==2 else real(rows)
        with patch.object(migrate_platform,'digest_rows',side_effect=mismatch):
            with self.assertRaisesRegex(ValueError,'preservation'):
                migrate_platform.transfer(source,self.database.target,self.database.schema)
        with db.connection() as conn:
            self.assertIsNone(conn.execute("SELECT to_regclass('users') AS name").fetchone()['name'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        self.assertTrue(migrate_platform.transfer(source,self.database.target,self.database.schema)['tables']['users']['exact_match'])

    def test_arrival_cursor_cannot_skip_uncommitted_or_rolled_back_writes(self):
        db.init()
        db.execute('INSERT INTO users VALUES(%s,%s,%s,%s,%s)',('owner','Owner','owner@example.invalid','disabled',db.now()))
        db.execute('INSERT INTO runs(id,user_id,scenario,state,created_at) VALUES(%s,%s,%s,%s,%s)',('run','owner','fixture','completed',db.now()))
        inserted,release,second_started=threading.Event(),threading.Event(),threading.Event()
        def first():
            try:
                with db.connection() as conn:
                    conn.execute("INSERT INTO evidence(id,run_id,user_id,kind,service,payload) VALUES('first','run','owner','log','api','{}')")
                    conn.execute('SELECT sequence FROM evidence_arrivals').fetchall()
                    inserted.set()
                    if not release.wait(5):
                        raise AssertionError('Test transaction was not released')
                    raise RuntimeError('intentional rollback')
            except RuntimeError:
                pass
        def second():
            second_started.set()
            db.execute("INSERT INTO evidence(id,run_id,user_id,kind,service,payload) VALUES('second','run','owner','log','api','{}')")
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending=executor.submit(first)
            self.assertTrue(inserted.wait(5))
            later=executor.submit(second)
            try:
                self.assertTrue(second_started.wait(2))
                self.assertEqual(db.all_rows('SELECT * FROM evidence_arrivals'),[])
                self.assertFalse(later.done())
            finally:
                release.set()
            pending.result(timeout=5)
            later.result(timeout=5)
        self.assertEqual(db.all_rows('SELECT sequence,evidence_id FROM evidence_arrivals'),[{'sequence':1,'evidence_id':'second'}])
