"""Offline provider fixtures. These do not claim live Sentry acceptance."""
import asyncio
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from cryptography.fernet import Fernet
from backend import auth, config, db
from backend.api import create_app
from backend.sentry_client import SentryClient, SentryError, seal, unseal
from backend.sentry_imports import ImportWorker, create_import, parameters, normalize

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'sentry-record-processor.json').read_text())

PROJECT = {"id": "42", "slug": "payment", "name": "Payment"}
TOKEN = "fixture-provider-token-never-returned"


class SentryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.object(db, "DATA", Path(directory.name)))
        self.enterContext(patch.multiple(config, PUBLIC_URL="https://desk.test", CONNECTION_KEY=Fernet.generate_key().decode(),
            SENTRY_CLIENT_ID="fixture-client", SENTRY_CLIENT_SECRET="fixture-secret", SENTRY_APP_SLUG="escalation-desk"))
        db.init()
        for owner in ("alice", "bob", "demo"):
            db.execute("INSERT INTO users VALUES(?,?,?,?,?)", (owner, owner, owner, "hash", db.now()))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (auth.digest(owner), owner, time.time() + 600))
        db.execute("INSERT INTO account_roles VALUES('demo','demo')")
        self.calls, self.jobs = [], []
        self.handler = self.provider
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.handler(r)))
        self.addAsyncCleanup(self.http.aclose)
        self.sentry = SentryClient(self.http)
        self.worker = ImportWorker(self.sentry)
        self.app = create_app()
        self.app.state.sentry = self.sentry
        self.app.state.sentry_importer = self.worker
        self.app.state.investigator = SimpleNamespace(enqueue=self.jobs.append)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def client(self, owner="alice", csrf=True):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://desk.test",
            cookies={auth.COOKIE: owner}, headers={"X-CSRF-Token": auth.digest(owner + ":csrf")} if csrf else {})

    def provider(self, request):
        self.calls.append(request)
        if request.url.path.endswith("/authorizations/"):
            return httpx.Response(200, json={"token": TOKEN, "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()})
        if request.method == "PUT":
            return httpx.Response(200, json={"status": "installed"})
        if request.url.path.endswith("/projects/"):
            return httpx.Response(200, json=[PROJECT])
        if request.url.params.get("dataset") == "logs":
            return httpx.Response(200, json={"data": [self.log()]})
        if request.url.params.get("dataset") == "spans":
            return httpx.Response(200, json={"data": [{"id": "span-record", "span_id": "a"*16, "trace": "b"*32,
                "timestamp": self.now.isoformat(), "span.duration": 1000, "span.op": "db.acquire", "span.status": "deadline_exceeded"}]})
        return httpx.Response(200, json=[{"eventID": "error-one", "projectID": "42", "dateCreated": self.now.isoformat(), "title": "TimeoutError", "entries": [
            {"type": "exception", "data": {"values": [{"type": "TimeoutError", "value": "acquisition timed out", "stacktrace": {"frames": [{"filename": "payment.py", "lineNo": 12, "function": "acquire", "vars": {"secret": "DO-NOT-SAVE"}}]}}]}}],
            "request": {"headers": [["Authorization", "private-header"]]}, "user": {"email": "private@example.test"}}])

    def log(self, id="log-one", **extra):
        return {"id": id, "timestamp": self.now.isoformat(), "message": "Connection acquisition timed out", "severity": "error", "trace": "b"*32, **extra}

    def connection(self, owner="alice", installation="install-one"):
        id = db.uid()
        db.execute("INSERT INTO sentry_connections(id,user_id,installation_id,organization,region,credentials,expires_at,state,projects,created_at) VALUES(?,?,?,?,?,?,?,'connected',?,?)",
            (id, owner, installation, "sample", "us", seal({"token": TOKEN}), time.time() + 3600, json.dumps([PROJECT]), db.now()))
        return db.one("SELECT * FROM sentry_connections WHERE id=?", (id,))

    def options(self, **extra):
        return {"projects": ["42"], "datasets": ["logs", "errors", "spans"], "start": (self.now - timedelta(minutes=1)).isoformat(),
                "end": self.now.isoformat(), "title": "Actual source fixture", **extra}

    def import_job(self, row=None, **extra):
        row = row or self.connection()
        return create_import(row["user_id"], row, parameters(self.options(**extra), row))[0]

    async def authorize(self, client, installation="install-one", **extra):
        started = await client.post("/api/connections/sentry/start", json={"region": "us"})
        self.assertEqual(started.status_code, 200, started.text)
        state = httpx.URL(started.json()["url"]).params["state"]
        params = {"state": state, "installationId": installation, "orgSlug": "sample", "code": "fixture-code", **extra}
        return await client.get("/api/connections/sentry/callback", params=params), params

    async def test_oauth_encrypts_tokens_and_rejects_callback_replay(self):
        async with self.client() as client:
            response, params = await self.authorize(client)
            self.assertEqual(response.status_code, 303, response.text)
            saved = db.one("SELECT * FROM sentry_connections")
            self.assertNotIn(TOKEN, saved["credentials"])
            self.assertEqual(unseal(saved["credentials"])["token"], TOKEN)
            public = (await client.get("/api/connections/sentry")).text
            for secret in (TOKEN, "fixture-secret", saved["credentials"], "installation_id"):
                self.assertNotIn(secret, public)
            count = len(self.calls)
            self.assertEqual((await client.get("/api/connections/sentry/callback", params=params)).status_code, 400)
            self.assertEqual(len(self.calls), count)
        self.assertEqual(self.jobs, [])

    async def test_oauth_expiry_cross_account_and_cancellation(self):
        async with self.client() as alice, self.client("bob") as bob:
            start = (await alice.post("/api/connections/sentry/start", json={"region": "eu"})).json()
            state = httpx.URL(start["url"]).params["state"]
            self.assertEqual((await bob.get("/api/connections/sentry/callback", params={"state": state})).status_code, 400)
            db.execute("UPDATE sentry_oauth_states SET expires=0")
            self.assertEqual((await alice.get("/api/connections/sentry/callback", params={"state": state})).status_code, 400)
            response, _ = await self.authorize(alice, error="access_denied")
            self.assertEqual(response.status_code, 303)
            self.assertIn("cancelled", response.headers["location"])
        self.assertEqual(self.calls, [])

    async def test_installation_cannot_transfer_between_accounts(self):
        self.connection()
        async with self.client("bob") as bob:
            response, _ = await self.authorize(bob)
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.calls, [])

    async def test_auth_csrf_demo_and_project_boundaries(self):
        row = self.connection()
        async with self.client("bob") as bob, self.client(csrf=False) as unsafe, self.client("demo") as demo, self.client() as alice:
            self.assertEqual((await bob.post(f'/api/connections/sentry/{row["id"]}/imports', json=self.options())).status_code, 404)
            self.assertEqual((await unsafe.post('/api/connections/sentry/start', json={"region": "us"})).status_code, 403)
            for path in ('/api/connections/sentry', '/api/connections/sentry/start'):
                self.assertEqual((await demo.get(path) if path.endswith('sentry') else await demo.post(path, json={})).status_code, 403)
            self.assertEqual((await alice.post(f'/api/connections/sentry/{row["id"]}/imports', json=self.options(projects=["999"]))).status_code, 400)
        self.assertEqual(self.calls, [])

    async def test_manual_import_retains_normalized_sources_and_does_not_analyze(self):
        row = self.connection()
        async with self.client() as client:
            first = await client.post(f'/api/connections/sentry/{row["id"]}/imports', json=self.options())
            same = await client.post(f'/api/connections/sentry/{row["id"]}/imports', json=self.options())
            self.assertEqual(first.json()["id"], same.json()["id"])
            self.assertEqual(self.calls, [])
            job = db.one("SELECT * FROM sentry_imports")
            before = await client.post(f'/api/collections/{job["collection_id"]}/investigations', json={})
            self.assertEqual(before.status_code, 409)
            await self.worker.run(job)
            self.assertEqual(db.one("SELECT state FROM sentry_imports")["state"], "completed")
            evidence = db.evidence(None, "alice", collection_id=job["collection_id"])
            self.assertEqual(len(evidence), 3)
            self.assertEqual({e["source_dataset"] for e in evidence}, {"logs", "errors", "spans"})
            saved = json.dumps(evidence)
            for secret in ("DO-NOT-SAVE", "private-header", "private@example.test"):
                self.assertNotIn(secret, saved)
            self.assertIn("payment.py:12", saved)
            calls = len(self.calls)
            await client.get(f'/api/collections/{job["collection_id"]}')
            await client.get('/api/connections/sentry')
            self.assertEqual(len(self.calls), calls)
            await client.post(f'/api/connections/sentry/{row["id"]}/disconnect', json={})
            self.assertIsNone(db.one("SELECT credentials FROM sentry_connections")["credentials"])
            self.assertEqual((await client.get(f'/api/collections/{job["collection_id"]}/evidence')).status_code, 200)
        self.assertEqual(self.jobs, [])

    async def fixture_import(self, **options):
        self.now = datetime(2026, 11, 6, 0, 0, 3, tzinfo=timezone.utc)
        row = self.connection()
        db.execute('UPDATE sentry_connections SET projects=? WHERE id=?', (json.dumps([FIXTURE['project']]), row['id']))
        row = db.one('SELECT * FROM sentry_connections WHERE id=?', (row['id'],))
        def respond(request):
            self.calls.append(request)
            if request.url.path.endswith('/projects/'):
                return httpx.Response(200, json=[FIXTURE['project']])
            dataset = request.url.params.get('dataset', 'errors')
            if dataset != 'errors':
                self.assertIn('service.name', request.url.params.get_list('field'))
                self.assertIn('http.status_code', request.url.params.get_list('field'))
            return httpx.Response(200, json=FIXTURE[dataset] if dataset == 'errors' else {'data': FIXTURE[dataset]})
        self.handler = respond
        job = self.import_job(row, **options)
        await self.worker.run(job)
        self.assertEqual(db.one('SELECT state FROM sentry_imports WHERE id=?', (job['id'],))['state'], 'completed')
        return row, job

    async def test_normalization_version_reimport_preserves_legacy_history(self):
        row, old = await self.fixture_import()
        # Reproduce the pre-version fingerprint and metadata of a saved import.
        options = json.loads(old['parameters'])
        summary = json.loads(db.one('SELECT summary FROM collections WHERE id=?', (old['collection_id'],))['summary'])
        summary.pop('normalization_version')
        db.execute('UPDATE collections SET summary=? WHERE id=?', (json.dumps(summary), old['collection_id']))
        digest = hashlib.sha256(json.dumps([row['id'], options], sort_keys=True).encode()).hexdigest()
        db.execute('UPDATE sentry_imports SET fingerprint=? WHERE id=?', (digest, old['id']))
        source = db.evidence(None, 'alice', collection_id=old['collection_id'])[0]['id']
        db.execute("INSERT INTO investigations(id,user_id,collection_id,engine,state,created_at,completed_at,report,usage) VALUES(?,?,?,'groq','completed',?,?,?,?)",
            ('saved', 'alice', old['collection_id'], db.now(), db.now(), json.dumps({'evidence_ids': [source], 'summary': 'Original report'}), json.dumps({'supplied_evidence_ids': [source]})))
        before = {table: db.all_rows(f'SELECT * FROM {table}') for table in ('collections', 'evidence', 'investigations')}
        fresh, created = create_import('alice', row, options)
        self.assertTrue(created)
        self.assertNotEqual(fresh['collection_id'], old['collection_id'])
        await self.worker.run(fresh)
        same, created = create_import('alice', row, options)
        self.assertFalse(created)
        self.assertEqual(same['id'], fresh['id'])
        self.assertEqual(json.loads(db.one('SELECT summary FROM collections WHERE id=?', (fresh['collection_id'],))['summary'])['normalization_version'], 2)
        db.init()
        for table, rows in before.items():
            for saved in rows:
                self.assertEqual(db.one(f'SELECT * FROM {table} WHERE id=?', (saved['id'],)), saved)
        async with self.client() as alice, self.client('bob') as bob:
            self.assertEqual((await alice.get('/api/investigations/saved/evidence')).status_code, 200)
            for path in (f'/api/evidence/{source}', f'/api/collections/{old["collection_id"]}', '/api/investigations/saved/evidence'):
                self.assertEqual((await bob.get(path)).status_code, 404)
        self.assertEqual(self.jobs, [])

    async def test_trace_followup_and_complete_provider_requests_keep_diagnostic_facts(self):
        from backend.evidence import EvidenceTools, encoded, INPUT_BYTES, TOOL_BYTES
        from backend.investigator import Investigator, collect_baseline, collect_query
        _, job = await self.fixture_import()
        tools = EvidenceTools(None, 'alice', groq=True, collection_id=job['collection_id'])
        activity = []
        def record(name, args, result):
            self.assertLessEqual(len(encoded(result)), TOOL_BYTES)
            activity.append((name, args, result))
        context, seen = collect_baseline(tools, record)
        trace = 'a' * 32
        self.assertEqual(activity[-1][0:2], ('search_logs', {'trace_id': trace}))
        self.assertTrue(all(r['trace_id'] == trace for r in activity[-1][2]['records']))
        self.assertTrue(any(r.get('exception_handled') is True for r in activity[-1][2]['records']))
        retained = deepcopy(context.records)
        collect_query(tools, context, record, seen, 'get_trace', {'trace_id': trace})
        self.assertEqual(context.records, retained)
        self.assertIn('Duplicate query', activity[-1][2]['error'])
        requests = []
        def provider(request):
            self.assertLessEqual(len(request.content), INPUT_BYTES)
            payload = json.loads(request.content)
            requests.append(payload)
            if 'response_format' not in payload:
                return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [{'function': {'name': 'get_trace', 'arguments': json.dumps({'trace_id': trace})}}]}}]})
            data = json.loads(payload['messages'][1]['content'].split('Operational data: ')[1])
            tables = data['evidence']['tables']
            rows = [dict(zip(table['columns'], r)) for table in tables.values() for r in table['rows']]
            error = next(r for r in rows if r.get('exception_type') == 'ValueError')
            self.assertIs(error['exception_handled'], True)
            self.assertEqual(error['parent_id'], '2' * 16)
            self.assertEqual(error['service'], 'record-processor')
            self.assertTrue(any(r.get('http_status') == 422 and r['trace_id'] == trace for r in rows))
            self.assertTrue(any(r.get('http_status') == 200 and r['trace_id'] != trace for r in rows))
            self.assertTrue(all(e['event'] == 'generated_server_response' for e in data['observed_http_exchanges']))
            self.assertNotIn('caller', json.dumps(data['observed_http_exchanges']))
            report = {'assessment': 'supported', 'origin': 'record-processor', 'summary': 'Fixture response for contract validation.',
                'affected_services': ['record-processor'], 'evidence_ids': [error['id']], 'hypotheses': [], 'recommendations': [], 'limitations': []}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(report)}}]})
        with patch('backend.investigator.GROQ_KEY', 'fixture'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
                await Investigator(http).groq(tools, record)
        self.assertEqual(len(requests), 2)

    async def test_trace_without_spans_still_collects_errors_and_does_not_infer_links(self):
        from backend.evidence import EvidenceTools, http_exchanges
        from backend.investigator import collect_baseline, collect_query
        _, job = await self.fixture_import(datasets=['errors', 'logs'])
        tools = EvidenceTools(None, 'alice', groq=True, collection_id=job['collection_id'])
        context, seen = collect_baseline(tools, lambda *args: None)
        collect_query(tools, context, lambda *args: None, seen, 'get_trace', {'trace_id': 'a' * 32})
        self.assertEqual(context.selected_trace, 'a' * 32)
        self.assertTrue(any(context.records[id].get('exception_handled') is True for id in context.trace_sources))
        self.assertEqual(http_exchanges(list(context.records.values())), [])
        self.assertEqual(http_exchanges([{'id': 'log', 'kind': 'log', 'service': 'unknown', 'http_status': 422},
            {'id': 'span', 'kind': 'span', 'service': 'unknown', 'name': 'http.other'}]), [])

    async def test_pagination_dedup_and_untrusted_link_cannot_redirect_token(self):
        job = self.import_job(datasets=["logs"])
        def pages(request):
            if request.url.path.endswith('/projects/'):
                return self.provider(request)
            self.calls.append(request)
            self.assertEqual(request.url.host, "us.sentry.io")
            if not request.url.params.get('cursor'):
                return httpx.Response(200, json={"data": [self.log()]}, headers={"Link": '<https://evil.example/steal?cursor=next>; rel="next"; results="true"'})
            return httpx.Response(200, json={"data": [self.log(), self.log("second")]})
        self.handler = pages
        await self.worker.run(job)
        summary = json.loads(db.one("SELECT summary FROM collections")["summary"])
        self.assertEqual(summary["observations"], 2)
        self.assertEqual(summary["duplicates"], 1)

    async def test_nonadvancing_cursor_retains_partial_snapshot(self):
        job = self.import_job(datasets=["logs"])
        def pages(request):
            if request.url.path.endswith('/projects/'):
                return self.provider(request)
            return httpx.Response(200, json={"data": [self.log()]}, headers={"Link": '<https://us.sentry.io/?cursor=same>; rel="next"; results="true"'})
        self.handler = pages
        await self.worker.run(job)
        self.assertEqual(db.one("SELECT state FROM sentry_imports")["state"], "partial")
        self.assertIn("did not advance", db.one("SELECT summary FROM collections")["summary"])

    async def test_missing_dataset_and_revoked_project_access_are_visible(self):
        job = self.import_job()
        def missing(request):
            if request.url.params.get('dataset') == 'spans':
                return httpx.Response(403, text="private provider error")
            return self.provider(request)
        self.handler = missing
        await self.worker.run(job)
        summary = json.loads(db.one("SELECT summary FROM collections")["summary"])
        self.assertEqual(summary['datasets']['payment:spans']['state'], 'unavailable')
        self.assertEqual(db.one("SELECT state FROM sentry_imports")["state"], "partial")
        self.assertNotIn("private provider error", json.dumps(summary))
        retry, _ = create_import("alice", db.one("SELECT * FROM sentry_connections"), json.loads(job['parameters']), job['id'])
        self.handler = lambda r: httpx.Response(200, json=[])
        await self.worker.run(retry)
        self.assertIn("no longer authorized", db.one("SELECT error FROM sentry_imports WHERE id=?", (retry['id'],))['error'])

    async def test_rate_limit_retries_and_excessive_wait_remains_visible(self):
        job = self.import_job(datasets=["logs"])
        attempts = 0
        def limited(request):
            nonlocal attempts
            if request.url.params.get('dataset'):
                attempts += 1
                if attempts == 1:
                    return httpx.Response(429, headers={'Retry-After': '0'})
            return self.provider(request)
        self.handler = limited
        await self.worker.run(job)
        self.assertEqual(attempts, 2)
        self.assertEqual(db.one("SELECT state FROM sentry_imports")["state"], "completed")

    async def test_refresh_does_not_restore_disconnected_credentials(self):
        row = self.connection()
        db.execute('UPDATE sentry_connections SET expires_at=0')
        def racing(request):
            if request.url.path.endswith('/authorizations/'):
                db.execute("UPDATE sentry_connections SET state='disconnected',credentials=NULL,generation=generation+1")
            return self.provider(request)
        self.handler = racing
        with self.assertRaises(SentryError):
            await self.sentry.token(row['id'])
        self.assertIsNone(db.one('SELECT credentials FROM sentry_connections')['credentials'])

    async def test_refresh_denial_requires_reconnect(self):
        row = self.connection()
        db.execute('UPDATE sentry_connections SET expires_at=0')
        self.handler = lambda r: httpx.Response(401)
        with self.assertRaises(SentryError):
            await self.sentry.token(row['id'])
        saved = db.one('SELECT * FROM sentry_connections')
        self.assertEqual(saved['state'], 'reconnect_required')
        self.assertIsNone(saved['credentials'])

    async def test_signed_uninstall_deduplicates_and_cancels_import(self):
        row = self.connection()
        job = self.import_job(row)
        raw = json.dumps({'action': 'deleted', 'installation': {'uuid': row['installation_id']}}).encode()
        headers = {'Sentry-Hook-Resource': 'installation', 'Sentry-Hook-Signature': hmac.new(config.SENTRY_CLIENT_SECRET.encode(), raw, hashlib.sha256).hexdigest()}
        async with self.client() as client:
            self.assertEqual((await client.post('/api/connections/sentry/webhook', content=raw)).status_code, 401)
            for _ in range(2):
                self.assertEqual((await client.post('/api/connections/sentry/webhook', content=raw, headers=headers)).status_code, 200)
        self.assertEqual(db.one('SELECT count(*) n FROM sentry_deliveries')['n'], 1)
        self.assertEqual(db.one('SELECT state FROM sentry_imports')['state'], 'cancelled')
        self.assertIsNone(db.one('SELECT credentials FROM sentry_connections')['credentials'])
        self.assertEqual(self.jobs, [])

    async def test_restart_and_retry_preserve_previous_sources(self):
        row = self.connection()
        job = self.import_job(row, datasets=['logs'])
        summary = json.loads(db.one('SELECT summary FROM collections')['summary'])
        self.worker.save(job, row, PROJECT, 'logs', [self.log()], summary)
        before = db.evidence(None, 'alice', collection_id=job['collection_id'])
        db.init()
        self.assertEqual(db.one('SELECT state FROM sentry_imports')['state'], 'interrupted')
        async with self.client() as client:
            response = await client.post(f'/api/sentry-imports/{job["id"]}/retry', json={})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertNotEqual(response.json()['collection_id'], job['collection_id'])
        self.assertEqual(db.evidence(None, 'alice', collection_id=job['collection_id']), before)

    async def test_record_bound_and_deadline_keep_partial_or_failed_states(self):
        job = self.import_job(datasets=['logs'])
        self.handler = lambda r: self.provider(r) if r.url.path.endswith('/projects/') else httpx.Response(200, json={'data': [self.log('one'), self.log('two')]})
        with patch('backend.sentry_imports.MAX_RECORDS', 1):
            await self.worker.run(job)
        self.assertEqual(db.one('SELECT state FROM sentry_imports')['state'], 'partial')
        row = db.one('SELECT * FROM sentry_connections')
        retry, _ = create_import('alice', row, json.loads(job['parameters']), job['id'])
        async def slow(*args):
            await asyncio.sleep(10)
        with patch.object(self.sentry, 'projects', slow), patch('backend.sentry_imports.IMPORT_SECONDS', .02):
            await self.worker.run(retry)
        self.assertIn('deadline', db.one('SELECT error FROM sentry_imports WHERE id=?', (retry['id'],))['error'])

    async def test_cancel_and_other_account_cannot_change_or_read_job(self):
        job = self.import_job()
        async with self.client('bob') as bob, self.client() as alice:
            self.assertEqual((await bob.get(f'/api/sentry-imports/{job["id"]}')).status_code, 404)
            self.assertEqual((await bob.post(f'/api/sentry-imports/{job["id"]}/cancel', json={})).status_code, 404)
            self.assertEqual((await alice.post(f'/api/sentry-imports/{job["id"]}/cancel', json={})).status_code, 200)
        await self.worker.run(job)
        self.assertEqual(self.calls, [])
        self.assertEqual(db.one('SELECT state FROM sentry_imports')['state'], 'cancelled')

    async def test_key_loss_and_unconfigured_oauth_fail_without_secret_exposure(self):
        row = self.connection()
        with patch.object(config, 'CONNECTION_KEY', Fernet.generate_key().decode()):
            with self.assertRaisesRegex(SentryError, 'cannot be decrypted'):
                await self.sentry.token(row['id'])
        with patch.object(config, 'SENTRY_CLIENT_SECRET', ''):
            async with self.client() as client:
                self.assertFalse((await client.get('/api/connections/sentry')).json()['configured'])
                self.assertEqual((await client.post('/api/connections/sentry/start', json={'region': 'us'})).status_code, 409)


class NormalizationTests(unittest.TestCase):
    def test_service_http_exception_and_parent_facts_from_captured_shapes(self):
        for dataset in ('errors', 'logs', 'spans'):
            for source in FIXTURE[dataset]:
                row, _, _ = normalize(source, dataset, FIXTURE['project'], {'id': 'connection', 'organization': 'fixture'})
                self.assertEqual(row['service'], 'record-processor')
                self.assertEqual(row['source_project'], 'escalation-test')
                self.assertNotIn('downstream', row)
                if source.get('http.status_code'):
                    self.assertEqual(row['http_status'], int(source['http.status_code']))
        for handled in (True, False, None, 'false'):
            source = deepcopy(FIXTURE['errors'][0])
            source['entries'][0]['data']['values'][0]['mechanism']['handled'] = handled
            row, _, _ = normalize(source, 'errors', FIXTURE['project'], {'id': 'connection', 'organization': 'fixture'})
            self.assertEqual(row['exception_type'], 'ValueError')
            self.assertEqual(row['span_op'], 'record.parse')
            self.assertEqual(row['parent_id'], '2' * 16)
            if isinstance(handled, bool):
                self.assertIs(row['exception_handled'], handled)
            else:
                self.assertNotIn('exception_handled', row)

    def test_missing_service_and_trace_are_unknown_without_project_fallback(self):
        for value in (None, '', 42, {'name': 'invented'}):
            row, _, _ = normalize({'id': 'source', 'service.name': value, 'project.name': 'escalation-test',
                'message': 'token=secret-value', 'http.status_code': True}, 'logs', FIXTURE['project'], {'id': 'connection', 'organization': 'fixture'})
            self.assertEqual(row['service'], 'unknown')
            for key in ('trace_id', 'span_id', 'parent_id', 'downstream', 'http_status', 'exception_handled'):
                self.assertNotIn(key, row)
            self.assertNotIn('secret-value', json.dumps(row))

    def test_sdk_logger_emits_bounded_attributes_without_request_credentials(self):
        from backend import sentry_telemetry
        with patch.object(sentry_telemetry, 'enabled', True), patch.object(sentry_telemetry.logger, 'error') as emit:
            sentry_telemetry.log('worker', 'Request failed', 'error',
                {'http_status': 500, 'trace_id': 'trace', 'authorization': 'private'})
        self.assertEqual(emit.call_args.kwargs['attributes'],
            {'service.name': 'worker', 'http_status': 500, 'w3c.trace_id': 'trace'})

    def test_wrong_project_rejected_and_size_redaction_disclosed(self):
        connection = {'organization': 'sample', 'id': 'connection'}
        with self.assertRaises(ValueError):
            normalize({'eventID': 'id', 'projectID': '999'}, 'errors', PROJECT, connection)
        row, changes, truncated = normalize({'id': 'id', 'message': 'secret_token=private', 'large': 'x'*40000}, 'logs', PROJECT, connection)
        self.assertTrue(truncated)
        self.assertGreater(changes, 0)
        self.assertNotIn('private', json.dumps(row))

    def test_instrumentation_scrubs_sdk_schema_without_fake_trace_ids(self):
        from backend.sentry_telemetry import scrub_event
        event = scrub_event({'request': {'url': 'http://localhost/checkout', 'headers': {'x-internal-key': 'private'}},
            'exception': {'values': [{'type': 'TimeoutError', 'stacktrace': {'frames': [{'filename': 'app.py', 'vars': {'key': 'private'}}]}}]}})
        self.assertNotIn('request', event)
        self.assertNotIn('private', json.dumps(event))
        self.assertEqual(event['exception']['values'][0]['type'], 'TimeoutError')
        self.assertIsNone(scrub_event({'request': {'url': 'http://localhost/control'}}))
