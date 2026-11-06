"""Opt-in reference-service instrumentation; platform credentials are never exported."""
import os
import sentry_sdk
from sentry_sdk import logger
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from .sentry_imports import source_scrub
from .imports import redact_object

enabled = False


def scrub_event(event, hint=None):
    # Health, control and platform traffic are outside the recorded workload.
    request = event.get("request") or {}
    if request.get("url", "").split("?")[0].endswith(("/health", "/metrics", "/control")):
        return None
    return redact_object(source_scrub(event))[0]


def initialize(service):
    global enabled
    dsn = os.getenv("SENTRY_" + service.upper() + "_DSN", "").strip()
    if not dsn or os.getenv("SENTRY_REFERENCE_ENABLED") != "1":
        return
    sentry_sdk.init(dsn=dsn, environment="reference-verification", send_default_pii=False,
        include_local_variables=False, traces_sample_rate=1.0, enable_logs=True,
        default_integrations=False, integrations=[StarletteIntegration(), HttpxIntegration()],
        before_send=scrub_event, before_send_transaction=scrub_event,
        before_send_log=lambda log, hint: redact_object(source_scrub(log))[0])
    sentry_sdk.set_tag("service.name", service)
    sentry_sdk.set_attribute("service.name", service)
    enabled = True


def log(service, message, level, fields):
    if enabled:
        allowed = {key: value for key, value in fields.items() if key in {
            "http_status", "timeout_ms", "downstream", "trace_id", "span_id", "duration_ms"}}
        # Retain W3C identifiers as attributes; the SDK supplies its own actual trace lineage.
        attrs = {"service.name": service, **{("w3c." + k if k in {"trace_id", "span_id"} else k): v for k, v in allowed.items()}}
        getattr(logger, level if level in {"trace", "debug", "info", "warning", "error", "fatal"} else "info")(
            message, attributes=attrs)


def start_span(service, name, trace_id, span_id):
    if not enabled:
        return None
    span = sentry_sdk.start_span(op="reference.operation", name=name)
    span.__enter__()
    span.set_data("service.name", service)
    span.set_data("w3c.trace_id", trace_id)
    span.set_data("w3c.span_id", span_id)
    return span


def finish_span(span, exc_type, exc, tb, status):
    if span is not None:
        if status == "error" or exc_type:
            span.set_status("internal_error")
        if exc:
            sentry_sdk.capture_exception(exc)
        span.__exit__(exc_type, exc, tb)


def flush():
    if enabled:
        sentry_sdk.flush(timeout=3)
