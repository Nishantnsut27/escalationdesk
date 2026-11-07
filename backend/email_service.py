"""Durable authentication email delivery and authenticated delivery receipts."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from . import config, db

SENDER = "TraceDesk <notifications@mail.tracedesk.nishantraj.me>"
REPLY_TO = "yournishantraj@gmail.com"
EVENTS = {"email.sent", "email.delivered", "email.delivery_delayed", "email.failed", "email.bounced", "email.complained"}
MAX_ATTEMPTS = 5
logger = logging.getLogger(__name__)


def webhook_url():
    return config.API_URL + "/api/email/webhook" if config.API_URL else None


def encryption():
    # Domain separation keeps queued mail ciphertext distinct from Sentry tokens.
    key = os.environ.get("DESK_EMAIL_KEY") or config.CONNECTION_KEY
    if not key:
        raise ValueError("Configure DESK_EMAIL_KEY or retain DESK_CONNECTION_KEY for encrypted email jobs.")
    try:
        raw = base64.urlsafe_b64decode(key)
        if len(raw) != 32:
            raise ValueError()
        return Fernet(base64.urlsafe_b64encode(hmac.digest(raw, b"tracedesk:email:v1", "sha256")))
    except (ValueError, TypeError):
        raise ValueError("Invalid email encryption key.") from None


def configured():
    return bool(os.environ.get("RESEND_API_KEY") and config.PUBLIC_URL and
                (os.environ.get("DESK_EMAIL_KEY") or config.CONNECTION_KEY))


def enqueue(conn, user, kind, text, dedupe_key, expires):
    stamp = time.time()
    identifier = db.uid()
    subject = {"verify": "Verify your TraceDesk email", "reset": "Reset your TraceDesk password",
               "password_changed": "Your TraceDesk password was changed"}[kind]
    # Plain-text authentication messages have no tracked HTML links or pixels.
    payload = {"from": SENDER, "to": [user["email"]], "reply_to": REPLY_TO,
               "subject": subject, "text": text}
    sealed = encryption().encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()
    conn.execute("INSERT INTO email_jobs(id,user_id,kind,dedupe_key,payload,created_at,expires,state,next_attempt) VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',%s) ON CONFLICT(dedupe_key) DO NOTHING",
                 (identifier,user["id"],kind,dedupe_key,sealed,stamp,min(expires,stamp+23*3600),stamp))


def verify_webhook(raw, headers, *, secret=None, now=None):
    secret = os.environ.get("RESEND_WEBHOOK_SECRET", "") if secret is None else secret
    if not secret:
        raise HTTPException(503, "Email webhook is not configured.")
    identifier = headers.get("svix-id", "")
    stamp = headers.get("svix-timestamp", "")
    signatures = headers.get("svix-signature", "")
    try:
        if not identifier or len(identifier) > 200 or abs((time.time() if now is None else now)-int(stamp)) > 300:
            raise ValueError()
        if not secret.startswith("whsec_"):
            raise ValueError()
        key = base64.b64decode(secret[6:], validate=True)
        if len(key) < 16:
            raise ValueError()
        expected = base64.b64encode(hmac.digest(key, identifier.encode()+b"."+stamp.encode()+b"."+raw, "sha256")).decode()
        if not any(hmac.compare_digest(signature, "v1,"+expected) for signature in signatures.split()):
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return identifier, value
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(400, "Invalid email webhook signature or payload.") from None


def delivery_update(conn, provider_id):
    latest = conn.execute("SELECT event_type,occurred_at FROM email_deliveries WHERE provider_id=%s ORDER BY occurred_at DESC,id DESC LIMIT 1", (provider_id,)).fetchone()
    if latest:
        conn.execute("UPDATE email_jobs SET delivery_status=%s,delivery_at=%s WHERE provider_id=%s AND (delivery_at IS NULL OR delivery_at<=%s)",
                     (latest["event_type"],latest["occurred_at"],provider_id,latest["occurred_at"]))


async def webhook(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 262144:
            raise HTTPException(413, "Email webhook is too large.")
    identifier, value = verify_webhook(bytes(raw), request.headers)
    event = value.get("type")
    if event not in EVENTS:
        return JSONResponse({"ok": True})
    try:
        provider_id = value["data"]["email_id"]
        stamp = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 200 or stamp.tzinfo is None:
            raise ValueError()
        occurred = stamp.astimezone(timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(400, "Invalid email delivery event.") from None
    await asyncio.to_thread(record_delivery, identifier, provider_id, event, occurred)
    return JSONResponse({"ok": True})


def record_delivery(identifier, provider_id, event, occurred):
    with db.connection() as conn:
        conn.execute("INSERT INTO email_deliveries(id,provider_id,event_type,occurred_at,received_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                     (identifier,provider_id,event,occurred,time.time()))
        delivery_update(conn, provider_id)


class EmailWorker:
    def __init__(self, client):
        self.client, self.task = client, None
        self.event = asyncio.Event()

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def loop(self):
        while True:
            self.event.clear()
            try:
                await self.process_one()
            except Exception:
                # A transient storage failure must not kill the durable worker.
                # Never log exceptions that could contain credentials or links.
                logger.warning("Email delivery worker could not process its queue; it will retry.")
            try:
                await asyncio.wait_for(self.event.wait(), timeout=5)
            except TimeoutError:
                pass

    def claim(self):
        stamp = time.time()
        with db.connection() as conn:
            db.lock(conn, "email-queue")
            conn.execute("UPDATE email_jobs SET state='expired',payload=NULL,lease_until=NULL WHERE payload IS NOT NULL AND expires<=%s", (stamp,))
            conn.execute("UPDATE email_jobs SET state='failed',payload=NULL,lease_until=NULL,last_error='Delivery retry limit reached.' WHERE state IN ('queued','sending') AND attempts>=%s AND (lease_until IS NULL OR lease_until<=%s)", (MAX_ATTEMPTS,stamp))
            if not configured():
                return False
            row = conn.execute("SELECT * FROM email_jobs WHERE payload IS NOT NULL AND attempts<%s AND expires>%s AND next_attempt<=%s AND (state='queued' OR (state='sending' AND lease_until<=%s)) ORDER BY created_at,id LIMIT 1",
                               (MAX_ATTEMPTS,stamp,stamp,stamp)).fetchone()
            if not row:
                return False
            job = dict(row)
            conn.execute("UPDATE email_jobs SET state='sending',attempts=attempts+1,lease_until=%s WHERE id=%s", (stamp+60,job["id"]))
        return job

    async def process_one(self):
        job = await asyncio.to_thread(self.claim)
        if not job:
            return False
        retry, error, provider_id = False, None, None
        delay = min(600, 15 * 2**job["attempts"])
        try:
            payload = json.loads(encryption().decrypt(job["payload"].encode()))
            response = await self.client.post("https://api.resend.com/emails", json=payload,
                headers={"Authorization": "Bearer "+os.environ["RESEND_API_KEY"],
                         "Idempotency-Key": "tracedesk/"+job["id"]}, timeout=20)
            if 200 <= response.status_code < 300:
                provider_id = response.json().get("id")
                if not isinstance(provider_id,str) or not 1 <= len(provider_id) <= 200:
                    raise ValueError()
            else:
                error = "Email provider returned HTTP " + str(response.status_code) + "."
                retry = response.status_code == 429 or response.status_code >= 500
                if response.status_code == 409:
                    retry = response.json().get("name") == "concurrent_idempotent_requests"
                if response.status_code == 429:
                    from .quota import delay_seconds
                    wait = delay_seconds(response.headers.get("retry-after", ""), time.time())
                    if wait is not None:
                        delay = max(delay,wait)
        except httpx.RequestError:
            retry, error = True, "Email provider could not be reached."
        except (ValueError, KeyError, InvalidToken):
            error = "Email payload or provider response is invalid."
        stamp = time.time()
        if retry and (job["attempts"]+1 >= MAX_ATTEMPTS or stamp+delay >= job["expires"]):
            retry = False
        await asyncio.to_thread(self.finish,job,provider_id,retry,stamp+delay,error)
        return True

    def finish(self,job,provider_id,retry,next_attempt,error):
        with db.connection() as conn:
            if provider_id:
                conn.execute("UPDATE email_jobs SET state=CASE WHEN state='cancelled' THEN state ELSE 'sent' END,payload=NULL,lease_until=NULL,provider_id=%s,last_error=NULL WHERE id=%s", (provider_id,job["id"]))
                delivery_update(conn,provider_id)
            elif retry:
                conn.execute("UPDATE email_jobs SET state='queued',lease_until=NULL,next_attempt=%s,last_error=%s WHERE id=%s AND state='sending'", (next_attempt,error,job["id"]))
            else:
                conn.execute("UPDATE email_jobs SET state='failed',payload=NULL,lease_until=NULL,last_error=%s WHERE id=%s AND state='sending'", (error,job["id"]))
