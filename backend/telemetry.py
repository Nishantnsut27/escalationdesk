import json
import os
import secrets
import time
from datetime import datetime, timezone
from .config import DATA
from . import sentry_telemetry

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def emit(service, run_id, kind, **payload):
    event = {"id": secrets.token_hex(12), "run_id": run_id, "service": service,
             "kind": kind, "timestamp": timestamp(), **payload}
    directory = DATA / "telemetry"
    directory.mkdir(parents=True, exist_ok=True)
    # Rolling versions write separate journals; cross-process text appends can interleave on Windows.
    instance = os.getenv("LAB_PORT", "")
    filename = f"{service}-{instance}.jsonl" if instance.isdigit() else f"{service}.jsonl"
    with (directory / filename).open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")
    if kind == "log":
        sentry_telemetry.log(service, payload.get("message", ""), payload.get("level", "info"), payload)
    return event

class Span:
    """Record real operation durations with W3C trace/parent identifiers."""
    def __init__(self, service, run_id, name, trace_id=None, parent_id=None):
        self.service, self.run_id, self.name = service, run_id, name
        self.trace_id = trace_id or secrets.token_hex(16)
        self.span_id = secrets.token_hex(8)
        self.parent_id = parent_id
        self.status = "ok"
        self.attributes = {}

    def __enter__(self):
        self.started = time.perf_counter()
        self.started_at = timestamp()
        self.sentry_span = sentry_telemetry.start_span(self.service, self.name, self.trace_id, self.span_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.status = "error"
        emit(self.service, self.run_id, "span", name=self.name, trace_id=self.trace_id,
             span_id=self.span_id, parent_id=self.parent_id, started_at=self.started_at,
             duration_ms=round((time.perf_counter() - self.started) * 1000, 2),
             status=self.status, attributes=self.attributes)
        sentry_telemetry.finish_span(self.sentry_span, exc_type, exc, tb, self.status)

    def carrier(self):
        return f"00-{self.trace_id}-{self.span_id}-01"

    def log(self, level, message, **fields):
        emit(self.service, self.run_id, "log", level=level, message=message,
             trace_id=self.trace_id, span_id=self.span_id, **fields)

def parent(header):
    parts = header.split("-")
    if len(parts) == 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
        try:
            int(parts[1], 16)
            int(parts[2], 16)
            return parts[1], parts[2]
        except ValueError:
            pass
    return None, None

