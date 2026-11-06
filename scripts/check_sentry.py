"""Send a bounded set of real HTTP requests to the dedicated Sentry test app."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    run_id = uuid.uuid4().hex
    directory = ROOT / ".cache" / "sentry-verification" / run_id
    directory.mkdir(parents=True)
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    started = datetime.now(timezone.utc).isoformat()
    with (directory / "test-app.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "examples.sentry_test_app", "--port", str(port), "--run-id", run_id],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20) as client:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Test app failed to start. Inspect its local log.")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError("Test app readiness timed out.")
                responses = []
                for value in ["10", "invalid-record", "20", "another-invalid-record", "30", "invalid-value"]:
                    response = client.post("/process", json={"value": value})
                    expected = 200 if value.isdigit() else 422
                    if response.status_code != expected:
                        raise RuntimeError(f"Unexpected HTTP status {response.status_code}.")
                    responses.append({"http_status": response.status_code, **response.json()})
                flushed = client.post("/flush")
                flushed.raise_for_status()
            result = {"run_id": run_id, "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
                      "project": "escalation-test", "environment": "verification", "responses": responses,
                      "expected_errors": 3, "expected_logs": 12,
                      "transport": flushed.json(),
                      "delivery": "SDK flushed; confirm arrival independently in Sentry"}
            (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result, indent=2))
            if result["transport"].get("dropped"):
                raise RuntimeError("Sentry transport dropped telemetry: " + ", ".join(result["transport"]["dropped"]))
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
