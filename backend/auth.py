import asyncio
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from . import db
from .config import PUBLIC_URL

hasher = PasswordHasher()
dummy_hash = hasher.hash(secrets.token_urlsafe(32))
attempts = defaultdict(deque)
COOKIE = "desk_session"

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def public_user(row):
    role = db.one("SELECT role FROM account_roles WHERE user_id=?", (row["id"],))
    return {**{key: row[key] for key in ("id", "name", "email")},
            "role": "demo" if role else "regular", "read_only": bool(role)}

def is_demo(user):
    return bool(db.one("SELECT role FROM account_roles WHERE user_id=?", (user["id"],)))

async def demo_login(request):
    from .demo import DEMO_ID, ready
    throttle(request)
    data = await body(request)
    password = data.get("password", "")
    user = db.one("SELECT * FROM users WHERE id=?", (DEMO_ID,))
    if not isinstance(password, str) or len(password) > 128:
        raise HTTPException(401, "Demo password is incorrect.")
    if not ready():
        raise HTTPException(503, "The recorded demo is not installed yet. Regular accounts remain available.")
    try:
        await asyncio.to_thread(hasher.verify, user["password_hash"], password)
    except VerificationError:
        raise HTTPException(401, "Demo password is incorrect.")
    return session_response(user, request.cookies.get(COOKIE, ""))

def current(request):
    token = request.cookies.get(COOKIE, "")
    row = db.one("SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE digest=? AND expires>?",
                 (digest(token), time.time()))
    if not row:
        raise HTTPException(401, "Sign in to continue.")
    return row

def csrf(request):
    token = request.cookies.get(COOKIE, "")
    if not token or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), digest(token + ":csrf")):
        raise HTTPException(403, "Your session could not be verified. Refresh and try again.")

async def body(request):
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "Send JSON data.")
    raw = await request.body()
    if len(raw) > 8192:
        raise HTTPException(413, "Request is too large.")
    try:
        value = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON.")
    if not isinstance(value, dict):
        raise HTTPException(400, "Expected a JSON object.")
    return value

def throttle(request):
    ip = request.client.host if request.client else "local"
    queue = attempts[ip]
    stamp = time.monotonic()
    while queue and queue[0] < stamp - 300:
        queue.popleft()
    if len(queue) >= 12:
        raise HTTPException(429, "Too many attempts. Try again in five minutes.")
    queue.append(stamp)

def session_response(user, old_token=""):
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE digest=? OR expires<?", (digest(old_token), time.time()))
    db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), user["id"], time.time() + 86400))
    response = JSONResponse({"user": public_user(user), "csrf": digest(token + ":csrf")})
    # OAuth returns through a top-level GET; CSRF still protects every ordinary write.
    response.set_cookie(COOKIE, token, max_age=86400, httponly=True, samesite="lax" if PUBLIC_URL else "strict", secure=bool(PUBLIC_URL), path="/")
    response.headers["Cache-Control"] = "no-store"
    return response

async def signup(request):
    throttle(request)
    data = await body(request)
    email = str(data.get("email", "")).strip().lower()
    name = str(data.get("name", "")).strip()
    password = data.get("password", "")
    if not 1 <= len(name) <= 60 or not re.fullmatch(r"[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+", email) or len(email) > 200:
        raise HTTPException(400, "Enter your name and a valid email address.")
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise HTTPException(400, "Use a password between 10 and 128 characters.")
    password_hash = await asyncio.to_thread(hasher.hash, password)
    user = {"id": db.uid(), "name": name, "email": email}
    try:
        db.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user["id"], name, email, password_hash, db.now()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Unable to create this account. Try signing in.")
    return session_response(user, request.cookies.get(COOKIE, ""))

async def login(request):
    throttle(request)
    data = await body(request)
    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")
    if not isinstance(password, str) or len(password) > 128:
        raise HTTPException(401, "Email or password is incorrect.")
    user = db.one("SELECT * FROM users WHERE email=?", (email,))
    try:
        await asyncio.to_thread(hasher.verify, user["password_hash"] if user else dummy_hash, password)
    except VerificationError:
        raise HTTPException(401, "Email or password is incorrect.")
    if not user:
        raise HTTPException(401, "Email or password is incorrect.")
    return session_response(user, request.cookies.get(COOKIE, ""))

async def me(request):
    user = current(request)
    response = JSONResponse({"user": public_user(user), "csrf": digest(request.cookies[COOKIE] + ":csrf")})
    response.headers["Cache-Control"] = "no-store"
    return response

async def logout(request):
    current(request)
    csrf(request)
    db.execute("DELETE FROM sessions WHERE digest=?", (digest(request.cookies.get(COOKIE, "")),))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response

