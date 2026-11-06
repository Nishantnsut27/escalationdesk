"""Sentry's public-integration OAuth and bounded, allowlisted REST transport."""
import asyncio
import json
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit
import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from . import config, db

HOSTS = {"us": "https://us.sentry.io", "eu": "https://de.sentry.io"}
CONTROL = "https://sentry.io"
SLUG = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
INSTALLATION = re.compile(r"[A-Za-z0-9-]{1,100}\Z")
MAX_RESPONSE = 2 * 1024 * 1024


class SentryError(Exception):
    def __init__(self, message, status=None, retry_after=0):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def cipher():
    try:
        return Fernet(config.CONNECTION_KEY.encode())
    except (ValueError, TypeError):
        raise SentryError("Sentry credential encryption is not configured. Ask the server operator to configure DESK_CONNECTION_KEY.") from None


def configured():
    try:
        cipher()
        return bool(config.PUBLIC_URL and config.SENTRY_CLIENT_ID and config.SENTRY_CLIENT_SECRET
                    and SLUG.fullmatch(config.SENTRY_APP_SLUG))
    except SentryError:
        return False


def seal(tokens):
    return cipher().encrypt(json.dumps(tokens).encode()).decode()


def unseal(value):
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except (InvalidToken, ValueError, AttributeError):
        raise SentryError("Saved Sentry credentials cannot be decrypted. Restore the encryption key or reconnect.") from None


def next_cursor(header, current, seen):
    # Only carry the opaque cursor; never follow a provider-supplied URL with a bearer token.
    for part in header.split(","):
        if re.search(r'rel="?next"?', part) and re.search(r'results="?true"?', part):
            match = re.search(r'<([^>]+)>', part)
            value = parse_qs(urlsplit(match.group(1)).query).get("cursor", [None])[0] if match else None
            if not value or len(value) > 1000 or value == current or value in seen:
                raise SentryError("Sentry pagination did not advance; retained results are partial.")
            return value
    return None


def retry_delay(value):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 60


class SentryClient:
    def __init__(self, http):
        self.http = http
        self.locks = {}

    async def request(self, method, host, path, *, token=None, params=None, payload=None):
        if host not in {CONTROL, *HOSTS.values()} or not path.startswith("/api/0/") or ".." in path:
            raise SentryError("Invalid Sentry API destination.")
        try:
            async with self.http.stream(method, host + path, params=params, json=payload,
                    headers={"Authorization": "Bearer " + token} if token else {}, timeout=15,
                    follow_redirects=False) as response:
                if response.status_code >= 300:
                    status = response.status_code
                    messages = {401: "Sentry authorization expired or was revoked. Reconnect Sentry.",
                        403: "Sentry denied access. Check integration permissions and dataset availability.",
                        404: "Sentry resource or dataset is unavailable.", 429: "Sentry rate limit reached."}
                    raise SentryError(messages.get(status, "Sentry could not complete the request (HTTP %s)." % status),
                                      status, retry_delay(response.headers.get("Retry-After")) if status == 429 else 0)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise SentryError("Sentry response exceeded 2 MiB; use a smaller import window.")
                    chunks.append(chunk)
                try:
                    data = json.loads(b"".join(chunks)) if size else {}
                except (ValueError, RecursionError):
                    raise SentryError("Sentry returned invalid JSON.") from None
                return data, response.headers.get("Link", "")
        except httpx.RequestError:
            raise SentryError("Sentry could not be reached. Check connectivity and retry.") from None

    async def exchange(self, installation, code):
        value, _ = await self.request("POST", CONTROL,
            f"/api/0/sentry-app-installations/{installation}/authorizations/", payload={
                "grant_type": "authorization_code", "code": code,
                "client_id": config.SENTRY_CLIENT_ID, "client_secret": config.SENTRY_CLIENT_SECRET})
        return self.valid_tokens(value)

    @staticmethod
    def valid_tokens(value):
        try:
            if not isinstance(value, dict) or not isinstance(value.get("token"), str) or not 1 <= len(value["token"]) <= 4096:
                raise ValueError()
            expiry = datetime.fromisoformat(value["expiresAt"]).timestamp()
            if expiry <= time.time():
                raise ValueError()
            return {"token": value["token"]}, expiry
        except (ValueError, TypeError, KeyError, OverflowError):
            raise SentryError("Sentry returned invalid authorization credentials.") from None

    async def token(self, connection_id, force=False):
        async with self.locks.setdefault(connection_id, asyncio.Lock()):
            row = db.one("SELECT * FROM sentry_connections WHERE id=%s", (connection_id,))
            if not row or row["state"] != "connected" or not row["credentials"]:
                raise SentryError("This Sentry connection is disconnected. Reconnect to import.", 401)
            tokens = unseal(row["credentials"])
            if not force and row["expires_at"] > time.time() + 60:
                return tokens["token"]
            now = int(time.time())
            assertion = jwt.encode({"iss": config.SENTRY_CLIENT_ID, "sub": config.SENTRY_CLIENT_ID,
                "iat": now, "exp": now + 60, "jti": db.uid()}, config.SENTRY_CLIENT_SECRET, algorithm="HS256")
            try:
                value, _ = await self.request("POST", CONTROL,
                    f'/api/0/sentry-app-installations/{row["installation_id"]}/authorizations/', token=assertion,
                    payload={"grant_type": "urn:sentry:params:oauth:grant-type:jwt-bearer"})
                tokens, expiry = self.valid_tokens(value)
            except SentryError as exc:
                if exc.status in {401, 403, 404}:
                    db.execute("UPDATE sentry_connections SET state='reconnect_required', credentials=NULL, error=%s WHERE id=%s AND generation=%s",
                               (str(exc), connection_id, row["generation"]))
                raise
            with db.connection() as conn:
                changed = conn.execute("UPDATE sentry_connections SET credentials=%s,expires_at=%s WHERE id=%s AND generation=%s AND state='connected' RETURNING id",
                                      (seal(tokens), expiry, connection_id, row["generation"])).fetchone()
            if not changed:
                raise SentryError("Sentry was disconnected during authorization.", 401)
            return tokens["token"]

    async def get(self, row, path, params=None):
        for retry in (False, True):
            token = await self.token(row["id"], force=retry)
            try:
                return await self.request("GET", HOSTS[row["region"]], path, token=token, params=params)
            except SentryError as exc:
                if exc.status != 401 or retry:
                    raise

    async def projects(self, organization, region, token):
        result, cursor, seen = [], None, set()
        for _ in range(20):
            data, link = await self.request("GET", HOSTS[region], f"/api/0/organizations/{organization}/projects/",
                                           token=token, params={"cursor": cursor} if cursor else {})
            if not isinstance(data, list):
                raise SentryError("Sentry returned an invalid project list.")
            for item in data:
                if not isinstance(item, dict) or not str(item.get("id", "")).isdigit() or not SLUG.fullmatch(str(item.get("slug", ""))):
                    raise SentryError("Sentry returned invalid project identifiers.")
                result.append({"id": str(item["id"]), "slug": item["slug"], "name": str(item.get("name", item["slug"]))[:120]})
            cursor = next_cursor(link, cursor, seen)
            if cursor is None:
                return result
            seen.add(cursor)
        raise SentryError("Project list exceeds the supported pagination limit. No partial selection was saved.")
