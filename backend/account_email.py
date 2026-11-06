"""Single-use email ownership and account recovery flows."""
import asyncio
import secrets
import time
from argon2.exceptions import VerificationError

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from . import auth, config, db, email_service

RECOVERY_RESPONSE = "If this address belongs to a regular account, a password reset email will arrive shortly."


def issue(user, purpose):
    if auth.is_demo(user) or not email_service.configured():
        return False
    stamp = time.time()
    with db.connection() as conn:
        db.lock(conn, "account-email:"+user["id"])
        if purpose == "verify" and conn.execute("SELECT verified_at FROM account_security WHERE user_id=%s AND verified_at IS NOT NULL", (user["id"],)).fetchone():
            return False
        recent = conn.execute("SELECT created_at FROM auth_tokens WHERE user_id=%s AND purpose=%s AND created_at>%s ORDER BY created_at DESC", (user["id"],purpose,stamp-3600)).fetchall()
        if len(recent) >= 5 or (recent and recent[0]["created_at"] > stamp-60):
            return False
        token = secrets.token_urlsafe(32)
        expires = stamp + (86400 if purpose == "verify" else 1800)
        token_digest = auth.digest(token)
        conn.execute("INSERT INTO auth_tokens(digest,user_id,purpose,created_at,expires) VALUES(%s,%s,%s,%s,%s)", (token_digest,user["id"],purpose,stamp,expires))
        link = config.PUBLIC_URL + "/#auth/"+purpose+"/"+token
        text = ("Confirm your email address for TraceDesk. This link expires in 24 hours.\n\n" if purpose == "verify" else
                "Reset your TraceDesk password. This link expires in 30 minutes.\n\n")
        text += link + "\n\nOpen the link and confirm the action. If you did not request this email, you can ignore it."
        email_service.enqueue(conn,user,purpose,text,"auth:"+token_digest,expires)
    return True


async def forgot_password(request):
    auth.throttle(request)
    data = await auth.body(request)
    email = str(data.get("email", "")).strip().lower()[:201]
    user = await asyncio.to_thread(db.one, "SELECT * FROM users WHERE email=%s", (email,))
    if user:
        await asyncio.to_thread(issue,user,"reset")
    return JSONResponse({"message": RECOVERY_RESPONSE})


async def request_verification(request):
    user = auth.current(request)
    auth.csrf(request)
    auth.throttle(request)
    await auth.body(request)
    if auth.is_demo(user):
        raise HTTPException(403, "Demo Account is read-only.")
    if not email_service.configured():
        raise HTTPException(503, "Account email is unavailable. Your saved history remains accessible.")
    await asyncio.to_thread(issue,user,"verify")
    return JSONResponse({"message": "If verification is needed, an email will arrive shortly. Allow one minute before resending."})


def token_digest(value):
    if not isinstance(value,str) or not 32 <= len(value) <= 128 or not all(c.isalnum() or c in "_-" for c in value):
        raise HTTPException(400, "This link is invalid or expired. Request a new one.")
    return auth.digest(value)


def consume(digest, purpose, password_hash=None, password=None):
    stamp = time.time()
    with db.connection() as conn:
        db.lock(conn, "auth-token-consumption")
        token = conn.execute("SELECT t.* FROM auth_tokens t WHERE digest=%s AND purpose=%s AND expires>%s AND consumed_at IS NULL AND NOT EXISTS (SELECT 1 FROM account_roles r WHERE r.user_id=t.user_id)", (digest,purpose,stamp)).fetchone()
        if not token:
            raise HTTPException(400, "This link is invalid or expired. Request a new one.")
        db.lock(conn, "account-email:"+token["user_id"])
        user = conn.execute("SELECT * FROM users WHERE id=%s", (token["user_id"],)).fetchone()
        if password is not None:
            try:
                same_password = auth.hasher.verify(user["password_hash"], password)
            except VerificationError:
                same_password = False
            if same_password:
                raise HTTPException(400, "Choose a password different from your current password.")
        # PostgreSQL migration uses the same lock as session issuance, closing a
        # race where login verified the old password just before the reset.
        if password_hash is not None:
            conn.execute("UPDATE users SET password_hash=%s WHERE id=%s", (password_hash,user["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=%s", (user["id"],))
            conn.execute("DELETE FROM sentry_oauth_states WHERE user_id=%s", (user["id"],))
            conn.execute("UPDATE auth_tokens SET consumed_at=%s WHERE user_id=%s AND consumed_at IS NULL", (stamp,user["id"]))
            conn.execute("UPDATE email_jobs SET state='cancelled',payload=NULL,lease_until=NULL WHERE user_id=%s AND kind IN ('verify','reset') AND state IN ('queued','sending')", (user["id"],))
            email_service.enqueue(conn,user,"password_changed",
                "Your TraceDesk password was changed. All previous sessions were signed out.\n\nIf you did not make this change, use password recovery immediately and contact "+email_service.REPLY_TO+".",
                "password-changed:"+digest,stamp+3600)
        else:
            conn.execute("UPDATE auth_tokens SET consumed_at=%s WHERE digest=%s", (stamp,digest))
        conn.execute("INSERT INTO account_security(user_id,verified_at) VALUES(%s,%s) ON CONFLICT(user_id) DO UPDATE SET verified_at=COALESCE(account_security.verified_at,excluded.verified_at)", (user["id"],db.now()))


async def confirm_verification(request):
    auth.throttle(request)
    data = await auth.body(request)
    # GET never consumes a token; only an explicit form submission reaches here.
    await asyncio.to_thread(consume,token_digest(data.get("token")),"verify")
    return JSONResponse({"message": "Email verified. You can now import evidence and investigate."})


async def reset_password(request):
    auth.throttle(request)
    data = await auth.body(request)
    digest = token_digest(data.get("token"))
    password = data.get("password")
    if not isinstance(password,str) or not 10 <= len(password) <= 128:
        raise HTTPException(400, "Use a password between 10 and 128 characters.")
    hashed = await asyncio.to_thread(auth.hasher.hash,password)
    await asyncio.to_thread(consume,digest,"reset",hashed,password)
    response = JSONResponse({"message": "Password changed and email verified. Sign in again with your new password."})
    response.delete_cookie(auth.COOKIE,path="/")
    return response
