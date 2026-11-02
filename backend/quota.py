"""Single-account quota state shared by the investigation worker."""
import asyncio
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

def delay_seconds(value, now):
    if value is None:
        return None
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError):
        pass
    pieces = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", value)
    if pieces and "".join(a + b for a, b in pieces) == value:
        return sum(float(n) * {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}[u] for n, u in pieces)
    try:
        return max(0, parsedate_to_datetime(value).timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        return None

def integer(value):
    try:
        n = int(value)
        return n if n >= 0 else None
    except (ValueError, TypeError):
        return None

class GroqQuota:
    def __init__(self, *, clock=time.time, sleep=asyncio.sleep):
        self.clock, self.sleep = clock, sleep
        self.buckets = {}
        self.blocked_until = 0
        self.category = "unknown"
        self.full_token_limit = None

    def observe(self, response):
        now = self.clock()
        diagnostic = {}
        for kind in ("tokens", "requests"):
            limit = integer(response.headers.get("x-ratelimit-limit-" + kind))
            remaining = integer(response.headers.get("x-ratelimit-remaining-" + kind))
            reset = delay_seconds(response.headers.get("x-ratelimit-reset-" + kind), now)
            if limit is not None or remaining is not None:
                self.buckets[kind] = {"limit": limit, "remaining": remaining,
                                      "reset_at": now + reset if reset is not None else None}
                diagnostic[kind] = self.buckets[kind].copy()
            if kind == "tokens" and limit is not None:
                self.full_token_limit = limit
        if response.status_code in {413, 429}:
            # Inspect only allowlisted scalars. Raw error text can contain account
            # identifiers, echoed prompts or secrets and must never be persisted.
            try:
                message = response.json().get("error", {}).get("message", "")
                if not isinstance(message, str):
                    message = ""
            except (ValueError, AttributeError):
                message = ""
            category = re.search(r"\b(TPM|TPD|RPM|RPD)\b", message, re.I)
            self.category = category.group(1).upper() if category else "unknown"
            allowance = re.search(r"\bLimit\s*:?\s*(\d+)", message, re.I)
            requested = re.search(r"\bRequested\s*:?\s*(\d+)", message, re.I)
            if allowance and self.category in {"TPM", "TPD"}:
                full_allowance = int(allowance.group(1))
                if self.category == "TPM":
                    self.full_token_limit = full_allowance
                if requested and int(requested.group(1)) > full_allowance:
                    self.full_token_limit = min(self.full_token_limit or full_allowance, full_allowance)
                    diagnostic["request_exceeds_full_allowance"] = True
                    diagnostic["requested_tokens"] = int(requested.group(1))
            timings = [delay_seconds(response.headers.get("retry-after"), now)]
            # Groq's standard token window is TPM and request window is RPD.
            # A minute-token reset is not the reset for an exhausted daily quota.
            relevant = {"TPM": "tokens", "RPD": "requests"}.get(self.category)
            for kind, bucket in self.buckets.items():
                if (kind == relevant or (self.category == "unknown" and bucket["remaining"] == 0)) and bucket["reset_at"] is not None:
                    timings.append(max(0, bucket["reset_at"] - now))
            usable = [n for n in timings if n is not None]
            delay = max(usable) if usable else 60
            self.blocked_until = max(self.blocked_until, now + delay)
            diagnostic.update(limit_category=self.category, retry_at=self.iso(self.blocked_until))
        return diagnostic

    @staticmethod
    def iso(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def required_wait(self, tokens):
        now = self.clock()
        until, reason = self.blocked_until, "Groq rate limit (" + self.category + ")"
        for kind, bucket in self.buckets.items():
            needed = tokens if kind == "tokens" else 1
            reset = bucket["reset_at"]
            if reset is not None and now >= reset:
                continue
            if bucket["remaining"] is not None and bucket["remaining"] < needed:
                candidate = reset if reset is not None else now + 60
                if candidate > until:
                    until, reason = candidate, "Waiting for Groq " + kind + " allowance (conservative estimate)"
        return max(0, until - now), reason

    async def wait(self, seconds, reason, usage, progress, deadline):
        if seconds <= 0:
            return
        retry_at = self.iso(self.clock() + seconds)
        usage["retry_at"] = retry_at
        usage["wait_reason"] = reason
        if usage.get("quota_wait_seconds", 0) + seconds > 120:
            raise ValueError(f"Groq quota will not recover within the 120-second waiting budget. Next retry: {retry_at}.")
        if self.clock() + seconds >= deadline:
            raise ValueError("Groq quota wait would exceed the five-minute execution deadline.")
        usage["progress"] = "quota_waiting"
        progress("quota_waiting")
        started = self.clock()
        try:
            # Short sleeps keep cancellation and shutdown responsive.
            remaining = seconds
            while remaining > 0:
                await self.sleep(min(remaining, 30))
                remaining = seconds - (self.clock() - started)
        finally:
            usage["quota_wait_seconds"] = round(usage.get("quota_wait_seconds", 0) + self.clock() - started, 3)
        usage.pop("retry_at", None)
        usage.pop("wait_reason", None)
        usage["progress"] = "running"
        progress("running")
