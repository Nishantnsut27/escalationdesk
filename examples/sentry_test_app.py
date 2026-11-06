"""Loopback-only telemetry test application, separate from Escalation Desk."""
import argparse
import asyncio
import os

import sentry_sdk
from sentry_sdk import logger
from sentry_sdk.transport import HttpTransport
import uvicorn
from sentry_sdk.integrations.starlette import StarletteIntegration
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from backend import config  # Load the same ignored environment files as the workspace.
from backend.sentry_telemetry import scrub_event


class VerificationTransport(HttpTransport):
    def __init__(self, options):
        self.dropped = []
        super().__init__(options)

    def on_dropped_event(self, reason):
        self.dropped.append(reason)


def create_app(run_id):
    dsn = os.getenv("SENTRY_TEST_DSN", "").strip()
    if not dsn:
        raise RuntimeError("Set SENTRY_TEST_DSN for the dedicated escalation-test project.")
    sentry_sdk.init(
        dsn=dsn, environment="verification", release="escalation-test@1",
        send_default_pii=False, include_local_variables=False, traces_sample_rate=1.0,
        default_integrations=False, integrations=[StarletteIntegration()],
        transport=VerificationTransport,
        before_send=scrub_event, before_send_transaction=scrub_event,
        before_send_log=lambda value, hint: scrub_event(value),
    )
    sentry_sdk.set_tag("verification.run_id", run_id)
    sentry_sdk.set_tag("service.name", "record-processor")
    sentry_sdk.set_attribute("verification.run_id", run_id)
    sentry_sdk.set_attribute("service.name", "record-processor")

    async def health(request):
        return JSONResponse({"status": "ok", "run_id": run_id})

    async def process(request):
        payload = await request.json()
        attributes = {"verification.run_id": run_id, "service.name": "record-processor"}
        logger.info("Record processing started", attributes=attributes)
        with sentry_sdk.start_span(op="record.parse", name="Parse integer value") as span:
            span.set_data("verification.run_id", run_id)
            span.set_data("service.name", "record-processor")
            try:
                value = int(payload["value"])
            except (ValueError, TypeError, KeyError) as exc:
                span.set_status("invalid_argument")
                event_id = sentry_sdk.capture_exception(exc)
                logger.error("Record processing failed: {error_type}",
                    error_type=type(exc).__name__, attributes=attributes)
                return JSONResponse({"status": "rejected", "event_id": event_id}, status_code=422)
        logger.info("Record processing completed", attributes=attributes)
        return JSONResponse({"status": "processed", "value": value})

    async def flush(request):
        await asyncio.to_thread(sentry_sdk.flush, timeout=15)
        return JSONResponse({"status": "flushed", "dropped": sentry_sdk.get_client().transport.dropped})

    return Starlette(routes=[Route("/health", health), Route("/process", process, methods=["POST"]),
                            Route("/flush", flush, methods=["POST"])])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    uvicorn.run(create_app(args.run_id), host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
