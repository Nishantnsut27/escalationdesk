from database import IsolatedDatabase, verify_users
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet
from backend import account_email, auth, config, db, email_service
from backend.api import create_app


def signed(value, identifier="delivery-one", stamp=None):
    raw = json.dumps(value,separators=(",", ":")).encode()
    stamp = str(int(time.time()) if stamp is None else stamp)
    key = base64.b64decode(os.environ["RESEND_WEBHOOK_SECRET"][6:])
    signature = base64.b64encode(hmac.digest(key,identifier.encode()+b"."+stamp.encode()+b"."+raw,"sha256")).decode()
    return raw,{"svix-id":identifier,"svix-timestamp":stamp,"svix-signature":"v1,"+signature}


class AccountEmailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/".cache"))
        self.enterContext(IsolatedDatabase(Path(directory)))
        self.enterContext(patch.object(auth,"PUBLIC_URL",""))
        self.enterContext(patch.object(config,"PUBLIC_URL","https://app.example.test"))
        self.enterContext(patch.dict(os.environ,{"RESEND_API_KEY":"fixture-key", "DESK_EMAIL_KEY":Fernet.generate_key().decode(),
                                              "RESEND_WEBHOOK_SECRET":"whsec_"+base64.b64encode(secrets.token_bytes(32)).decode()}))
        db.init()
        auth.attempts.clear()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        session = (await self.client.post("/api/auth/signup",json={"name":"Alice","email":"alice@example.test","password":"original-password"})).json()
        self.user = session["user"]
        self.client.headers["x-csrf-token"] = session["csrf"]

    def link_token(self, kind):
        job = db.one("SELECT * FROM email_jobs WHERE kind=%s ORDER BY created_at DESC",(kind,))
        payload = json.loads(email_service.encryption().decrypt(job["payload"].encode()))
        return payload["text"].split("/#auth/"+kind+"/")[1].split()[0],job,payload

    async def test_verification_requires_explicit_action_single_use_and_gates_writes(self):
        self.assertFalse(self.user["email_verified"])
        token,job,payload = self.link_token("verify")
        self.assertNotIn(token,json.dumps(db.all_rows("SELECT * FROM auth_tokens")))
        self.assertNotIn(token,job["payload"])
        self.assertNotIn("html",payload)
        self.assertEqual(payload["reply_to"],email_service.REPLY_TO)
        for path in ("/api/imports","/api/connections/sentry/start","/api/collections/x/investigations","/api/investigations/x/supplements"):
            self.assertEqual((await self.client.post(path,json={})).status_code,403,path)
        self.assertEqual((await self.client.get("/api/collections")).status_code,200)
        self.assertIn((await self.client.get("/api/auth/verification-confirmation",params={"token":token})).status_code,(404,405))
        self.assertFalse((await self.client.get("/api/auth/me")).json()["user"]["email_verified"])
        result = await self.client.post("/api/auth/verification-confirmation",json={"token":token})
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue((await self.client.get("/api/auth/me")).json()["user"]["email_verified"])
        self.assertEqual((await self.client.post("/api/auth/verification-confirmation",json={"token":token})).status_code,400)

    async def test_non_enumerating_recovery_throttle_demo_and_expiry(self):
        db.execute("INSERT INTO users VALUES(%s,%s,%s,%s,%s)",("demo","Demo","demo@example.test","disabled",db.now()))
        db.execute("INSERT INTO account_roles VALUES(%s,'demo')",("demo",))
        outputs=[]
        for email in ("alice@example.test","unknown@example.test","demo@example.test","alice@example.test"):
            response=await self.client.post("/api/auth/forgot-password",json={"email":email})
            outputs.append((response.status_code,response.json()))
        self.assertTrue(all(output==outputs[0] for output in outputs))
        self.assertEqual(db.one("SELECT COUNT(*) n FROM auth_tokens WHERE purpose='reset'")["n"],1)
        token,_,_=self.link_token("reset")
        db.execute("UPDATE auth_tokens SET expires=0 WHERE purpose='reset'")
        self.assertEqual((await self.client.post("/api/auth/reset-password",json={"token":token,"password":"new-password-long"})).status_code,400)
        self.assertEqual((await self.client.get("/api/auth/me")).status_code,200)

    async def test_concurrent_reset_one_winner_revokes_sessions_and_other_links(self):
        await self.client.post("/api/auth/forgot-password",json={"email":self.user["email"]})
        token,_,_=self.link_token("reset")
        old_user=db.one("SELECT * FROM users WHERE id=%s",(self.user["id"],))
        auth.session_response(old_user)
        db.execute("INSERT INTO auth_tokens VALUES(%s,%s,%s,%s,%s,NULL)",(auth.digest("another-token"),self.user["id"],"reset",time.time(),time.time()+1800))
        password_hash=auth.hasher.hash("a-different-password")
        results=await asyncio.gather(*(asyncio.to_thread(account_email.consume,auth.digest(token),"reset",password_hash) for _ in range(2)),return_exceptions=True)
        self.assertEqual(sum(result is None for result in results),1)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM sessions WHERE user_id=%s",(self.user["id"],))["n"],0)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM auth_tokens WHERE consumed_at IS NULL")["n"],0)
        self.assertIsNotNone(db.one("SELECT verified_at FROM account_security")["verified_at"])
        self.assertEqual((await self.client.get("/api/auth/me")).status_code,401)
        with self.assertRaises(Exception):
            auth.session_response(old_user)
        confirmation=db.one("SELECT * FROM email_jobs WHERE kind='password_changed'")
        self.assertNotIn("a-different-password",email_service.encryption().decrypt(confirmation["payload"].encode()).decode())
        self.assertTrue(auth.hasher.verify(db.one("SELECT password_hash FROM users WHERE id=%s",(self.user["id"],))["password_hash"],"a-different-password"))

    async def test_reset_rejects_current_password_without_consuming_link(self):
        await self.client.post("/api/auth/forgot-password", json={"email": self.user["email"]})
        token, _, _ = self.link_token("reset")
        before = db.one("SELECT password_hash FROM users WHERE id=%s", (self.user["id"],))
        result = await self.client.post("/api/auth/reset-password", json={"token": token, "password": "original-password"})
        self.assertEqual(result.status_code, 400)
        self.assertIn("different from your current password", result.json()["error"])
        self.assertEqual(before, db.one("SELECT password_hash FROM users WHERE id=%s", (self.user["id"],)))
        self.assertIsNone(db.one("SELECT consumed_at FROM auth_tokens WHERE digest=%s", (auth.digest(token),))["consumed_at"])
        self.assertEqual((await self.client.get("/api/auth/me")).status_code, 200)
        result = await self.client.post("/api/auth/reset-password", json={"token": token, "password": "a-new-password-here"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual((await self.client.get("/api/auth/me")).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": self.user["email"], "password": "original-password"})).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/login", json={"email": self.user["email"], "password": "a-new-password-here"})).status_code, 200)
        self.assertEqual((await self.client.post("/api/auth/reset-password", json={"token": token, "password": "another-new-password"})).status_code, 400)

    async def test_delivery_retries_retain_idempotency_and_erase_link(self):
        token,job,_=self.link_token("verify")
        calls=[]
        def respond(request):
            calls.append(request)
            return httpx.Response(503) if len(calls)==1 else httpx.Response(200,json={"id":"provider-message"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            worker=email_service.EmailWorker(client)
            await worker.process_one()
            waiting=db.one("SELECT * FROM email_jobs WHERE id=%s",(job["id"],))
            self.assertEqual(waiting["state"],"queued")
            self.assertIsNotNone(waiting["payload"])
            db.execute("UPDATE email_jobs SET next_attempt=0")
            await worker.process_one()
        self.assertEqual(calls[0].headers["idempotency-key"],calls[1].headers["idempotency-key"])
        self.assertEqual(calls[0].content,calls[1].content)
        saved=db.one("SELECT * FROM email_jobs WHERE id=%s",(job["id"],))
        self.assertEqual(saved["state"],"sent")
        self.assertIsNone(saved["payload"])
        self.assertNotIn(token,json.dumps(saved))

    async def test_webhooks_are_signed_deduplicated_and_never_verify_account(self):
        _,job,_=self.link_token("verify")
        db.execute("UPDATE email_jobs SET provider_id=%s WHERE id=%s",("message",job["id"]))
        raw,headers=signed({"type":"email.delivered","created_at":"2026-11-06T09:00:00Z","data":{"email_id":"message"}})
        for _ in range(2):
            response=await self.client.post("/api/email/webhook",content=raw,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM email_deliveries")["n"],1)
        self.assertEqual(db.one("SELECT delivery_status FROM email_jobs WHERE id=%s",(job["id"],))["delivery_status"],"email.delivered")
        self.assertFalse((await self.client.get("/api/auth/me")).json()["user"]["email_verified"])
        self.assertEqual((await self.client.post("/api/email/webhook",content=raw+b" ",headers=headers)).status_code,400)
        raw,headers=signed({"type":"email.sent"},stamp=int(time.time())-301)
        self.assertEqual((await self.client.post("/api/email/webhook",content=raw,headers=headers)).status_code,400)

    async def test_restarts_do_not_send_bulk_verification_and_expiry_erases_payload(self):
        before=db.all_rows("SELECT * FROM auth_tokens")
        db.init()
        self.assertEqual(before,db.all_rows("SELECT * FROM auth_tokens"))
        db.execute("UPDATE email_jobs SET expires=0")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:self.fail("Expired mail must not be sent"))) as client:
            await email_service.EmailWorker(client).process_one()
        self.assertEqual(db.one("SELECT state,payload FROM email_jobs"),{"state":"expired","payload":None})

    async def test_verification_expiry_and_resend_throttle(self):
        token,_,_=self.link_token('verify')
        for _ in range(2):
            self.assertEqual((await self.client.post('/api/auth/verification-request',json={})).status_code,200)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM auth_tokens WHERE purpose='verify'")['n'],1)
        db.execute("UPDATE auth_tokens SET expires=0,created_at=%s WHERE purpose='verify'",(time.time()-65,))
        self.assertEqual((await self.client.post('/api/auth/verification-confirmation',json={'token':token})).status_code,400)
        await self.client.post('/api/auth/verification-request',json={})
        new_token,_,_=self.link_token('verify')
        self.assertNotEqual(new_token,token)
        self.assertEqual((await self.client.post('/api/auth/verification-confirmation',json={'token':new_token})).status_code,200)

    async def test_retry_limit_and_permanent_failure_erase_payloads(self):
        _,job,_=self.link_token('verify')
        calls=[]
        def unavailable(request):
            calls.append(request.headers['idempotency-key'])
            return httpx.Response(429,headers={'Retry-After':'30'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
            worker=email_service.EmailWorker(client)
            for _ in range(email_service.MAX_ATTEMPTS):
                db.execute('UPDATE email_jobs SET next_attempt=0')
                await worker.process_one()
            self.assertFalse(await worker.process_one())
        row=db.one('SELECT * FROM email_jobs WHERE id=%s',(job['id'],))
        self.assertEqual((row['state'],row['attempts'],row['payload']),('failed',5,None))
        self.assertEqual(len(set(calls)),1)
        await self.client.post('/api/auth/forgot-password',json={'email':self.user['email']})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(403))) as client:
            await email_service.EmailWorker(client).process_one()
        self.assertEqual(db.one("SELECT state,payload,attempts FROM email_jobs WHERE kind='reset'"),{'state':'failed','payload':None,'attempts':1})

    async def test_reset_during_delivery_cannot_requeue_consumed_link(self):
        _,verification,_=self.link_token('verify')
        await self.client.post('/api/auth/forgot-password',json={'email':self.user['email']})
        token,_,_=self.link_token('reset')
        password=auth.hasher.hash('new-password-after-reset')
        async def reset_while_sending(request):
            await asyncio.to_thread(account_email.consume,auth.digest(token),'reset',password)
            return httpx.Response(503)
        async with httpx.AsyncClient(transport=httpx.MockTransport(reset_while_sending)) as client:
            await email_service.EmailWorker(client).process_one()
        row=db.one('SELECT state,payload FROM email_jobs WHERE id=%s',(verification['id'],))
        self.assertEqual(row,{'state':'cancelled','payload':None})
        self.assertEqual(db.one("SELECT COUNT(*) n FROM auth_tokens WHERE consumed_at IS NULL")['n'],0)

    async def test_webhook_before_send_receipt_and_reordered_events(self):
        _,job,_=self.link_token('verify')
        raw,headers=signed({'type':'email.delivered','created_at':'2026-11-06T09:00:00Z','data':{'email_id':'early-message'}},identifier='early-delivery')
        self.assertEqual((await self.client.post('/api/email/webhook',content=raw,headers=headers)).status_code,200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'id':'early-message'}))) as client:
            await email_service.EmailWorker(client).process_one()
        raw,headers=signed({'type':'email.sent','created_at':'2026-11-06T08:59:00Z','data':{'email_id':'early-message'}},identifier='late-sent-event')
        self.assertEqual((await self.client.post('/api/email/webhook',content=raw,headers=headers)).status_code,200)
        self.assertEqual(db.one('SELECT delivery_status FROM email_jobs WHERE id=%s',(job['id'],))['delivery_status'],'email.delivered')
        self.assertFalse((await self.client.get('/api/auth/me')).json()['user']['email_verified'])
