"""Bounded local imports. No provider calls or scenario/incident creation."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import PurePath
from starlette.exceptions import HTTPException
from . import db

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 5000
MAX_RECORD_BYTES = 32768
MAX_SERVICES = 32
SECRET_KEY = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credentials?|private[_-]?key)")
SECRET_PAIR = re.compile(r"""(?ix)(?<![\w.-])(["']?(?:[\w.-]{0,80}(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential)[\w.-]{0,80})["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;&}\]]+)""")
BEARER = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S)
COOKIE_HEADER = re.compile(r"(?im)^(\s*(?:authorization|(?:set-)?cookie)\s*:\s*).*$")
URL_AUTH = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")
LEVEL = re.compile(r"(?i)(?:^|[\s\[])(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)(?:[\s:\]]|$)")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?")
CONTINUATION = re.compile(r"^\s|^Traceback |^(?:[\w.]+(?:Error|Exception)|Caused by:|During handling|The above exception)")
ID_VALUE = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")


def redact(text):
    count = 0
    for pattern, replacement in (
        (PRIVATE_KEY, "[REDACTED PRIVATE KEY]"), (COOKIE_HEADER, r"\1[REDACTED]"), (BEARER, "[REDACTED AUTHORIZATION]"),
        (JWT, "[REDACTED TOKEN]"), (URL_AUTH, r"\1[REDACTED]@"),
        (SECRET_PAIR, r'\1"[REDACTED]"'),
    ):
        text, changed = pattern.subn(replacement, text)
        count += changed
    return text, count


def redact_object(value, depth=0):
    if depth > 30:
        return "[NESTED CONTENT OMITTED]", 1
    if isinstance(value, dict):
        result, count = {}, 0
        for key, item in value.items():
            if SECRET_KEY.search(key):
                result[key], changes = "[REDACTED]", 1
            else:
                result[key], changes = redact_object(item, depth + 1)
            count += changes
        return result, count
    if isinstance(value, list):
        items, count = [], 0
        for item in value:
            cleaned, changed = redact_object(item, depth + 1)
            items.append(cleaned)
            count += changed
        return items, count
    if isinstance(value, str):
        return redact(value)
    return value, 0


def timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value / 1000 if value >= 100_000_000_000 else value, timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _json(text):
    return json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite number")))


def parse_logs(content, default_service="unknown"):
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(400, "Paste logs or select a nonempty log file.")
    try:
        size = len(content.encode("utf-8"))
    except UnicodeError:
        raise HTTPException(400, "Logs must contain valid UTF-8 text.") from None
    if size > MAX_BYTES:
        raise HTTPException(413, "Import at most 1 MiB of log text at a time.")
    if "\0" in content:
        raise HTTPException(400, "Binary files are not supported. Import UTF-8 log text.")
    if not isinstance(default_service, str) or not re.fullmatch(r"[\w.-]{1,80}", default_service):
        raise HTTPException(400, "Service labels use 1-80 letters, digits, dots, underscores or hyphens.")
    # Redact PEM blocks before record splitting so unindented key lines cannot
    # become separately retained observations. Preserve source line positions.
    content, private_keys = PRIVATE_KEY.subn(lambda match: "[REDACTED PRIVATE KEY]" + "\n" * match.group().count("\n"), content)
    groups = []
    for number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        if groups and CONTINUATION.search(line) and not line.lstrip().startswith("{") and not STAMP.match(line.lstrip()):
            groups[-1][1] += "\n" + line
        else:
            groups.append([number, line])
        if len(groups) > MAX_RECORDS:
            raise HTTPException(413, "Import at most 5,000 observations at a time.")
        if len(groups[-1][1].encode("utf-8")) > MAX_RECORD_BYTES:
            raise HTTPException(413, "One observation exceeds 32 KiB. Split the input at an observation boundary.")
    rows, source_ids = [], {}
    counts = dict(input_bytes=size, input_lines=len(content.splitlines()), observations=0,
                  duplicates=0, conflicting_ids=0, malformed_records=0, redactions=private_keys,
                  missing_timestamps=0, missing_services=0, invalid_fields=0)
    for number, raw in groups:
        obj = None
        if raw.lstrip().startswith(("{", "[")):
            try:
                obj = _json(raw)
                if not isinstance(obj, dict):
                    obj = None
                    counts["malformed_records"] += 1
            except (ValueError, RecursionError):
                counts["malformed_records"] += 1
        if obj is not None:
            obj, changes = redact_object(obj)
            cleaned = json.dumps(obj, ensure_ascii=False)
        else:
            cleaned, changes = redact(raw)
        counts["redactions"] += changes
        obj = obj or {}
        value = obj.get("timestamp", obj.get("@timestamp", obj.get("time", obj.get("ts"))))
        if value is None and not obj:
            match = STAMP.search(cleaned)
            value = match.group(0) if match else None
        recorded_at = timestamp(value)
        if not recorded_at:
            counts["missing_timestamps"] += 1
        service = obj.get("service", obj.get("service_name", default_service))
        if isinstance(service, dict):
            service = service.get("name", default_service)
        if not isinstance(service, str) or not re.fullmatch(r"[\w.-]{1,80}", service):
            service = default_service
            counts["invalid_fields"] += 1
        if service == "unknown":
            counts["missing_services"] += 1
        kind = obj.get("kind", "log")
        if not isinstance(kind, str) or kind not in {"log", "span", "metric"}:
            kind = "log"
            counts["invalid_fields"] += 1
        message = obj.get("message", obj.get("msg"))
        if not isinstance(message, str) or not message:
            message = cleaned
        payload = {"message": message, "original": cleaned, "source_line": number,
                   "source_format": "json" if obj else "text"}
        level = str(obj.get("level", obj.get("severity", ""))).lower()
        if not level:
            match = LEVEL.search(cleaned)
            level = match.group(1).lower() if match else "unknown"
        payload["level"] = {"warn": "warning", "fatal": "error", "critical": "error"}.get(level, level)
        if payload["level"] not in {"trace", "debug", "info", "warning", "error", "unknown"}:
            payload["level"] = "unknown"
            counts["invalid_fields"] += 1
        if value is not None and not recorded_at:
            payload["timestamp_note"] = "Source timestamp missing timezone or invalid; no telemetry time inferred."
        for key in ("trace_id", "span_id", "parent_id", "request_id"):
            if key in obj:
                candidate = obj[key]
                if isinstance(candidate, str) and ID_VALUE.fullmatch(candidate):
                    payload[key] = candidate
                else:
                    counts["invalid_fields"] += 1
        for key in ("name", "downstream", "status", "span_op", "exception_type"):
            if isinstance(obj.get(key), str) and len(obj[key]) <= 160:
                payload[key] = obj[key]
        if "exception_handled" in obj:
            if isinstance(obj["exception_handled"], bool):
                payload["exception_handled"] = obj["exception_handled"]
            elif obj["exception_handled"] is not None:
                counts["invalid_fields"] += 1
        for key in ("duration_ms", "timeout_ms", "http_status", "pool_capacity", "pool_in_use",
                    "requests", "failures", "acquisition_failures", "last_latency_ms"):
            if key not in obj:
                continue
            number_value = obj[key]
            if isinstance(number_value, (int, float)) and not isinstance(number_value, bool) and 0 <= number_value <= 1e15 and math.isfinite(number_value):
                payload[key] = number_value
            else:
                counts["invalid_fields"] += 1
        if "started_at" in obj:
            start = timestamp(obj["started_at"])
            if start:
                payload["started_at"] = start
            else:
                counts["invalid_fields"] += 1
        source_id = obj.get("id", obj.get("source_id"))
        if isinstance(source_id, (str, int)) and not isinstance(source_id, bool):
            source_id = str(source_id)
            if len(source_id) <= 200:
                payload["source_id"] = source_id
                versions = source_ids.setdefault(source_id, set())
                if cleaned in versions:
                    counts["duplicates"] += 1
                    continue
                if versions:
                    counts["conflicting_ids"] += 1
                versions.add(cleaned)
        rows.append({"id": db.uid(), "kind": kind, "service": service, "timestamp": recorded_at, **payload})
    services = sorted({row["service"] for row in rows})
    if len(services) > MAX_SERVICES:
        raise HTTPException(413, "Import at most 32 distinct services per collection.")
    counts["observations"] = len(rows)
    counts["services"] = services
    counts["kinds"] = sorted({row["kind"] for row in rows})
    counts["coverage"] = "Only the supplied text; no telemetry was fetched."
    counts["redaction_note"] = "Common credential fields/patterns are redacted; review the saved content before sending evidence to model providers."
    return rows, counts


async def read_import(request):
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "Send JSON with UTF-8 log content.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES * 6 + 8192:
            raise HTTPException(413, "Import request is too large.")
        chunks.append(chunk)
    try:
        value = _json(b"".join(chunks).decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, "Invalid import JSON.") from None
    if not isinstance(value, dict) or set(value) - {"content", "title", "symptom", "filename", "service"}:
        raise HTTPException(400, "Use content, title, symptom, filename and service fields only.")
    return value


def create_collection(owner, data):
    filename = data.get("filename")
    if filename is not None and (not isinstance(filename, str) or len(filename) > 200 or
            "/" in filename or "\\" in filename or PurePath(filename).suffix.lower() not in {".log", ".txt", ".jsonl"}):
        raise HTTPException(400, "Choose a .log, .txt or .jsonl file.")
    title = data.get("title", filename or "Imported logs")
    symptom = data.get("symptom", "")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise HTTPException(400, "Use a title between 1 and 120 characters.")
    if not isinstance(symptom, str) or len(symptom) > 2000:
        raise HTTPException(400, "The optional symptom must be at most 2,000 characters.")
    rows, summary = parse_logs(data.get("content"), data.get("service", "unknown"))
    title, title_changes = redact(title.strip())
    symptom, symptom_changes = redact(symptom)
    filename, filename_changes = redact(filename) if filename else (None, 0)
    summary["redactions"] += title_changes + symptom_changes + filename_changes
    fingerprint = hashlib.sha256(json.dumps([title, symptom, filename,
        [{k: v for k, v in row.items() if k != "id"} for row in rows]], sort_keys=True).encode()).hexdigest()
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id FROM collections WHERE user_id=? AND fingerprint=?", (owner, fingerprint)).fetchone()
        if existing:
            return existing["id"], False
        collection_id = db.uid()
        conn.execute("INSERT INTO collections VALUES(?,?,?,?,?,?,?,?,?)",
                     (collection_id, owner, title, symptom, filename, "file" if filename else "paste",
                      db.now(), fingerprint, json.dumps(summary)))
        for row in rows:
            payload = {k: v for k, v in row.items() if k not in {"id", "kind", "service", "timestamp"}}
            conn.execute("""INSERT INTO evidence(id,user_id,kind,service,timestamp,payload,collection_id)
                         VALUES(?,?,?,?,?,?,?)""", (row["id"], owner, row["kind"], row["service"],
                         row["timestamp"], json.dumps(payload), collection_id))
    return collection_id, True


def public_collection(row):
    return {**{k: v for k, v in row.items() if k not in {"user_id", "fingerprint", "summary"}},
            "summary": json.loads(row["summary"])}
