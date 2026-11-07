"""Versioned, explicit failure signatures. A tag identifies a symptom, not a cause."""
import json
import re

VERSION = "signatures-v1"
# Patterns use syntax shared by Python and PostgreSQL regular expressions.
CATALOG = (
    ("null_reference", "application_exception", "Null reference / NPE", r"NullPointerException|null reference|nullreferenceexception|cannot (read|set) propert[^\n]*(null|undefined)|NoneType[^\n]*has no attribute"),
    ("type_error", "application_exception", "Type error", r"TypeError|type mismatch|invalid cast|ClassCastException"),
    ("unhandled_exception", "application_exception", "Unhandled exception", r"unhandled exception|uncaught exception|Exception in thread|Traceback \(most recent call last\)"),
    ("out_of_memory", "resource_exhaustion", "Out of memory", r"OutOfMemoryError|out[ -]of[ -]memory|OOMKilled|(^|[^a-z])OOM([^a-z]|$)|MemoryError"),
    ("heap_exhaustion", "resource_exhaustion", "Heap exhaustion", r"heap (space|exhaust|limit)|heap[^\n]*(exhausted|allocation failed)|allocation failed[^\n]*heap"),
    ("gc_pressure", "resource_exhaustion", "Explicit GC pressure", r"GC overhead limit exceeded|GC pressure|garbage collection pressure|GC thrashing"),
    ("connection_exhaustion", "resource_exhaustion", "Connection exhaustion", r"connection pool[^\n]*(exhaust|saturat|full)|too many connections|connection acquisition timed out|no (available|free) connections"),
    ("thread_exhaustion", "resource_exhaustion", "Thread exhaustion", r"unable to create[^\n]*thread|thread pool[^\n]*(exhaust|saturat)|no available threads"),
    ("file_handle_exhaustion", "resource_exhaustion", "File-handle exhaustion", r"too many open files|file (handle|descriptor)[^\n]*exhaust|EMFILE|ENFILE"),
    ("request_timeout", "latency_timeout", "Request timeout", r"request[^\n]*(timed out|timeout)|deadline exceeded|TimeoutError|ReadTimeout|request_timeout"),
    ("dependency_timeout", "latency_timeout", "Dependency timeout", r"(upstream|downstream|dependency)[^\n]*(timed out|timeout)|dependency_timeout"),
    ("database_timeout", "latency_timeout", "Database timeout", r"(database|query|statement|lock wait)[^\n]*(timed out|timeout)|database_timeout"),
    ("service_unavailable", "dependency_failure", "Unavailable service", r"service unavailable|upstream unavailable|dependency unavailable|no healthy upstream|ServiceUnavailable"),
    ("upstream_failure", "dependency_failure", "Explicit upstream failure", r"(upstream|downstream|dependency)[^\n]*(failed|failure|error response|HTTP 5[0-9][0-9])|bad gateway"),
    ("crash_loop", "deployment_lifecycle", "Crash loop", r"CrashLoopBackOff|crash[ -]loop|back-off restarting failed container"),
    ("readiness_failure", "deployment_lifecycle", "Failed readiness / startup check", r"(readiness|startup|liveness) (probe|check)[^\n]*(fail|error)|failed (readiness|startup)"),
    ("pod_termination", "deployment_lifecycle", "Explicit pod termination", r"pod[^\n]*(terminated|terminating|evicted|killed)|container[^\n]*(terminated|OOMKilled)|SIGTERM received"),
    ("missing_schema", "database_query", "Missing schema", r"no such (table|column)|undefined (table|column)|relation[^\n]*does not exist|unknown column|schema[^\n]*(missing|does not exist)"),
    ("query_rejected", "database_query", "Rejected query", r"SQLSyntaxError|syntax error at or near|query (rejected|failed)|constraint violation|duplicate key value violates"),
    ("deadlock", "database_query", "Deadlock", r"deadlock detected|deadlock found|DeadlockLoser|SQLSTATE[^\n]*40P01"),
    ("dns_failure", "network_connectivity", "DNS failure", r"ENOTFOUND|EAI_AGAIN|DNS[^\n]*(fail|error)|name or service not known|name resolution[^\n]*fail"),
    ("tls_failure", "network_connectivity", "TLS failure", r"TLS[^\n]*(fail|error)|SSL[^\n]*(fail|error)|certificate[^\n]*(expired|verify failed|invalid)|CERT_HAS_EXPIRED"),
    ("connection_refused", "network_connectivity", "Connection refused", r"ECONNREFUSED|connection refused|ConnectError"),
    ("connection_reset", "network_connectivity", "Connection reset", r"ECONNRESET|connection reset|broken pipe"),
    ("authentication_rejected", "authentication_access", "Authentication rejected", r"authentication (failed|rejected)|invalid credentials|unauthorized|permission denied|access denied"),
    ("configuration_error", "configuration", "Configuration error", r"missing (required )?(configuration|config|environment variable)|invalid configuration|ConfigurationError"),
    ("validation_error", "data_validation", "Validation error", r"ValidationError|validation failed|invalid input|JSONDecodeError|ValueError"),
    ("filesystem_error", "storage_filesystem", "Filesystem error", r"FileNotFoundError|no such file or directory|read-only file system|no space left on device|ENOSPC"),
    ("messaging_failure", "jobs_messaging", "Messaging / job failure", r"message[^\n]*(delivery failed|rejected)|job (failed|failure)|queue[^\n]*(full|unavailable)|dead.letter"),
    ("throttled", "rate_limit_quota", "Throttling", r"rate limit[^\n]*(exceed|reach)|too many requests|throttl|quota exceeded|HTTP 429"),
)
COMPILED = [(key, family, re.compile(pattern, re.I)) for key, family, _, pattern in CATALOG]
LABELS = {key: label for key, _, label, _ in CATALOG} | {"unknown": "Unclassified", "mixed": "Multiple error families"}


def signature_text(row):
    parts = [row.get(key) for key in ("message", "exception_type", "error_type", "error", "stack_trace", "exception")]
    return "\n".join(value if isinstance(value, str) else json.dumps(value) for value in parts if value) or str(row.get("original", ""))


def detect(row):
    text = signature_text(row)
    matches = [(key, family) for key, family, pattern in COMPILED if pattern.search(text)]
    if row.get("exception_handled") is False and not any(key == "unhandled_exception" for key, _ in matches):
        matches.append(("unhandled_exception", "application_exception"))
    tags = [key for key, _ in matches]
    if len({family for _, family in matches}) > 1:
        tags.append("mixed")
    return tags or ["unknown"]


def catalogue():
    return [{"id": key, "family": family, "label": label, "version": VERSION} for key, family, label, _ in CATALOG] + [
        {"id": key, "family": key, "label": LABELS[key], "version": VERSION} for key in ("mixed", "unknown")]


def migration_sql():
    """Freeze this catalogue with migration 3; later versions require a new migration."""
    def quoted(value):
        return "'" + value.replace("'", "''") + "'"
    values = ",\n".join("(" + ",".join(quoted(value) for value in (VERSION, key, family, label, pattern)) + ")"
                         for key, family, label, pattern in CATALOG)
    return "INSERT INTO error_signatures(version,id,family,label,pattern) VALUES\n" + values + ";\n"
