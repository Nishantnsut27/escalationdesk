"""Explicit live check: one scenario/investigation per invocation; consumes Groq quota."""
import argparse
import json
from pathlib import Path
import secrets
import time
import httpx

ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=["pool", "slow", "healthy"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    directory = ROOT / ".cache" / "live-verification"
    directory.mkdir(parents=True, exist_ok=True)
    # Disposable local test identity; the same configured Groq account is always
    # used by the server. Keep this local password in memory only.
    account = {"name": "Live verification", "email": "live-" + secrets.token_hex(5) + "@example.test",
               "password": secrets.token_urlsafe(24)}
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=15) as client:
        ready_by = time.monotonic() + 30
        while True:
            try:
                client.get("/api/health").raise_for_status()
                break
            except httpx.RequestError:
                if time.monotonic() >= ready_by:
                    raise SystemExit("Local runtime is not ready; start the launcher first.") from None
                time.sleep(.5)
        response = client.post("/api/auth/signup", json=account)
        response.raise_for_status()
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
        artifact = {"scenario": args.scenario, "run_id": run_id, "result": result, "evidence": run["evidence"]}
        (directory / (investigation_id + ".json")).write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps({"state": result["state"], "report": result.get("report"), "error": result.get("error"),
                          "usage": result["usage"]}), flush=True)
        if result["state"] != "completed":
            raise SystemExit(1)

if __name__ == "__main__":
    main()
