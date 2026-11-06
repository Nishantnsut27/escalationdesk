"""Check the expected mechanisms against retained actual observations, outside model input."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / ".cache" / "phase8" / "v1"


def verify(case, rows):
    logs = [r for r in rows if r["kind"] == "log"]
    assert len(logs) >= 500 and len({r["id"] for r in rows}) == len(rows)
    assert all(r.get("timestamp") for r in rows)
    messages = "\n".join(r.get("message", "") + " " + r.get("stack_trace", "") + " " + r.get("error", "") for r in logs)
    if case == "pool":
        assert "Connection acquisition timed out" in messages
        assert any(r.get("pool_capacity") == r.get("pool_in_use") == 2 for r in rows)
    elif case == "slow":
        assert any(r.get("name") == "payment.process" and r["duration_ms"] >= 3900 for r in rows)
        assert any(r.get("timeout_ms") == 2000 and r["service"] == "order" for r in rows)
    elif case == "exception":
        assert "TypeError: 'NoneType' object is not subscriptable" in messages
        assert any(r.get("http_status") == 500 for r in logs)
    elif case == "schema":
        assert "no such column: currency" in messages
        assert any(r.get("message") == "Application release activated" and r.get("version") == "v2" for r in logs)
        assert any(r.get("table") == "products" and "currency" not in r["columns"] for r in logs)
    elif case == "unavailable":
        assert "Service process exited" in messages and "Dependency connection failed" in messages
    elif case == "authentication":
        assert any(r.get("http_status") == 401 for r in logs)
        assert "Upstream authentication rejected" in messages
    elif case == "incomplete":
        assert all(r["kind"] == "log" and r["service"] in {"gateway", "order"} for r in rows)
        assert any(r.get("http_status") == 200 for r in logs) and any(r.get("http_status", 0) >= 400 for r in logs)
    elif case == "rollout":
        assert any(r.get("ready") is False and r.get("version") == "v2" for r in logs)
        assert any(r.get("message") == "Readiness probe completed" and r.get("http_status") == 503 for r in logs)
        assert any(len(r.get("target_instances", [])) == 2 for r in logs)
    elif case == "startup":
        assert "CATALOG_REGION is required" in messages
        assert len([r for r in logs if r.get("message") == "Startup attempt exited" and r.get("exit_code") == 1]) == 3
    elif case == "tls":
        assert "certificate has expired" in messages
        assert any(r.get("not_after", "z") < r["timestamp"] for r in logs)
    elif case == "throttling":
        assert any(r.get("http_status") == 429 and r.get("retry_after") == "1" for r in logs)
        assert any(r.get("attempt") == 3 for r in logs) and "Retry scheduled" in messages
    elif case == "backlog":
        depths = [r["queue_depth"] for r in rows if "queue_depth" in r]
        assert max(depths) > 20 and depths[-1] == 0
        assert "Job acknowledged" in messages and "Job enqueued" in messages
    else:
        raise ValueError("Unknown case")
    return {"case": case, "logs": len(logs), "observations": len(rows), "mechanism_checked": True}


if __name__ == "__main__":
    results = []
    for path in sorted(BASE.glob("*/snapshot.json")):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        results.append(verify(snapshot["slug"], snapshot["evidence"]))
    assert len(results) == 12
    (BASE.parent / "capture-verification.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(results), "logs": sum(r["logs"] for r in results), "observations": sum(r["observations"] for r in results)}))
