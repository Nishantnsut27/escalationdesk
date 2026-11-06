"""Explicit live check: one scenario/investigation per invocation; consumes Groq quota."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from psycopg import sql
from backend import db, postgres
from backend.lab import ports


@contextmanager
def isolated_runtime(directory):
    target = postgres.test_target()
    schema = "operator_live_" + secrets.token_hex(12)
    directory = directory / schema
    directory.mkdir(parents=True)
    previous = db.DATA, db._target, db._schema
    with postgres.connection(target) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    db.DATA, db._target, db._schema = directory, target, schema
    port = ports()
    environment = {**os.environ, "DESK_DATABASE_URL": "", "DESK_TEST_DATABASE_URL": target,
                   "DESK_TEST_SCHEMA": schema, "DESK_DATA_DIR": str(directory), "DESK_PORT": str(port),
                   "DESK_ENABLE_LAB": "1", "DESK_PUBLIC_URL": "", "RESEND_API_KEY": "",
                   "SENTRY_REFERENCE_ENABLED": "0", "PYTHONUNBUFFERED": "1"}
    try:
        with (directory / "launcher.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "backend.launcher"], cwd=ROOT, env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                print(json.dumps({"isolated_schema": schema, "artifacts": str(directory)}), flush=True)
                yield f"http://127.0.0.1:{port}"
            finally:
                if process.poll() is None:
                    (directory / "stop.request").write_text("stop", encoding="utf-8")
                    process.wait(timeout=30)
    finally:
        db.DATA, db._target, db._schema = previous
        # Retain the explicit operator schema and capture for later source review.

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["pool", "slow", "healthy"])
    parser.add_argument("--supplement", action="store_true", help="Explicitly request one further-analysis supplement after the Groq report.")
    args = parser.parse_args()
    directory = ROOT / ".cache" / "live-verification"
    directory.mkdir(parents=True, exist_ok=True)
    with isolated_runtime(directory) as base_url:
        check(args, directory, base_url)


def check(args, directory, base_url):
    # Disposable local test identity; the same configured Groq account is always
    # used by the server. Keep this local password in memory only.
    account = {"name": "Live verification", "email": "live-" + secrets.token_hex(5) + "@example.test",
               "password": secrets.token_urlsafe(24)}
    with httpx.Client(base_url=base_url, trust_env=False, timeout=20) as client:
        ready_by = time.monotonic() + 60
        while True:
            try:
                client.get("/api/health").raise_for_status()
                break
            except httpx.RequestError:
                if time.monotonic() >= ready_by:
                    raise SystemExit("Isolated runtime is not ready; inspect its retained launcher log.") from None
                time.sleep(.5)
        if args.supplement:
            probe = client.get("/api/investigations/runtime-check/evidence")
            if probe.status_code != 401:
                raise SystemExit("Restart the runtime with the saved-report endpoints before consuming live quota.")
        response = client.post("/api/auth/signup", json=account)
        response.raise_for_status()
        # Only this newly created test-project schema gets a verified fixture.
        # Normal accounts must always complete the real email confirmation flow.
        owner = response.json()["user"]["id"]
        db.execute("INSERT INTO account_security(user_id,verified_at) VALUES(%s,%s)", (owner, db.now()))
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        before = len(client.get("/api/incidents").json())
        response = client.post(f"/api/scenarios/{args.scenario}/start", json={})
        response.raise_for_status()
        run_id = response.json()["id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            run = client.get("/api/runs/" + run_id).json()
            if run["run"]["state"] not in {"starting", "running", "resetting"}:
                break
            time.sleep(1)
        assert run["run"]["state"] == "completed", run["run"]
        incidents = client.get("/api/incidents").json()
        if args.scenario == "healthy":
            assert run["run"]["failures"] == 0 and len(incidents) == before
            (directory / ("healthy-" + run_id + ".json")).write_text(json.dumps(run, indent=2), encoding="utf-8")
            print(json.dumps({"scenario": "healthy", "run_id": run_id, "new_incidents": 0, "failures": 0}), flush=True)
            return
        incident = next(i for i in incidents if i["run_id"] == run_id)
        response = client.post(f"/api/incidents/{incident['id']}/investigations", json={"engine": "groq"})
        response.raise_for_status()
        investigation_id = response.json()["id"]
        deadline = time.monotonic() + 360
        previous = None
        while time.monotonic() < deadline:
            result = client.get("/api/investigations/" + investigation_id).json()
            state = (result["state"], result["usage"].get("requests"), len(result["activity"]))
            if state != previous:
                print(json.dumps({"scenario": args.scenario, "id": investigation_id, "state": state[0],
                                  "attempts": state[1], "tool_calls": state[2], "retry_at": result["usage"].get("retry_at")}), flush=True)
                previous = state
            if result["state"] not in {"queued", "running", "quota_waiting"}:
                break
            time.sleep(1)
        if result.get("report"):
            client.post("/api/auth/logout", json={}).raise_for_status()
            login = client.post("/api/auth/login", json=account)
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf"]
            refs = set(result["report"]["evidence_ids"])
            for hypothesis in result["report"]["hypotheses"]:
                refs.update(hypothesis["evidence_ids"])
            assert refs <= set(result["usage"]["supplied_evidence_ids"])
            for ref in refs:
                assert client.get("/api/evidence/" + ref).status_code == 200
            # Reload the saved detail through the account-scoped API.
            saved = client.get("/api/incidents/" + incident["id"]).json()
            assert any(i["id"] == investigation_id and i["report"] == result["report"] for i in saved["investigations"])
        # Retain the original result even if a later endpoint or assertion fails.
        (directory / (investigation_id + ".json")).write_text(json.dumps(
            {"scenario": args.scenario, "run_id": run_id, "result": result, "evidence": run["evidence"]}, indent=2), encoding="utf-8")
        supplement = None
        if args.supplement and result["state"] == "completed":
            original = client.get("/api/investigations/" + investigation_id).json()
            assert original.get("supplements", []) == [], "Reading a report must never start further analysis."
            scope = client.get(f"/api/investigations/{investigation_id}/evidence").json()
            response = client.post(f"/api/investigations/{investigation_id}/supplements", json={})
            response.raise_for_status()
            supplement_id = response.json()["id"]
            deadline = time.monotonic() + 360
            previous = None
            while time.monotonic() < deadline:
                saved = client.get("/api/investigations/" + investigation_id).json()
                supplement = next(item for item in saved["supplements"] if item["id"] == supplement_id)
                state = (supplement["state"], supplement["usage"].get("requests"))
                if state != previous:
                    print(json.dumps({"supplement_id": supplement_id, "state": state[0], "attempts": state[1],
                                      "retry_at": supplement["usage"].get("retry_at")}), flush=True)
                    previous = state
                if state[0] not in {"queued", "running", "quota_waiting"}:
                    break
                time.sleep(1)
            assert {k: v for k, v in saved.items() if k != "supplements"} == {k: v for k, v in original.items() if k != "supplements"}
            assert set(supplement["source_ids"]) <= set(scope["source_ids"])
            if supplement["result"]:
                for explanation in supplement["result"]["explanations"]:
                    for ref in explanation["supporting_citations"] + explanation["conflicting_citations"]:
                        assert ref in supplement["source_ids"]
                        assert client.get("/api/evidence/" + ref).status_code == 200
                # Repeated submission is idempotent, even after completion.
                duplicate = client.post(f"/api/investigations/{investigation_id}/supplements", json={})
                assert duplicate.json()["id"] == supplement_id
            client.post("/api/auth/logout", json={}).raise_for_status()
            login = client.post("/api/auth/login", json=account)
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf"]
            revisited = client.get("/api/investigations/" + investigation_id).json()
            assert revisited["report"] == original["report"]
            assert next(item for item in revisited["supplements"] if item["id"] == supplement_id) == supplement
            print(json.dumps({"supplement": supplement}), flush=True)
        artifact = {"scenario": args.scenario, "run_id": run_id, "result": result, "evidence": run["evidence"], "supplement": supplement}
        (directory / (investigation_id + ".json")).write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps({"state": result["state"], "report": result.get("report"), "error": result.get("error"),
                          "usage": result["usage"]}), flush=True)
        if result["state"] != "completed" or (args.supplement and (not supplement or supplement["state"] != "completed")):
            raise SystemExit(1)

if __name__ == "__main__":
    main()
