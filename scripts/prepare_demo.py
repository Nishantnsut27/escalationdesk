"""Capture real workloads, explicitly analyze, review, export, or install the recorded demo."""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import db, demo
from backend.catalog import CATALOG, CASE_IDS
from backend.lab import LabRuntime, summary, store_rows

BASE = ROOT / ".cache" / "phase8" / "v1"
OWNER = "phase8-operator"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def export_bundle(cases, path=None):
    """Append reviewed cases without replacing any previously published case."""
    from scripts.verify_demo_captures import verify
    path = Path(path) if path is not None else demo.BUNDLE
    additions = {"version": "v1", "cases": cases}
    demo.validate(additions)
    for case in cases:
        verify(case["slug"], case["evidence"])
    bundle = read(path) if path.exists() else {"version": "v1", "cases": []}
    if bundle["cases"]:
        demo.validate(bundle)
    published = {case["slug"]: case for case in bundle["cases"]}
    for case in cases:
        previous = published.get(case["slug"])
        if previous is not None:
            if demo.digest(previous) != demo.digest(case):
                raise ValueError("A published demo cannot be rewritten. Retain its original case and report.")
        else:
            bundle["cases"].append(case)
    checksum = demo.validate(bundle)
    # Validation and serialization finish before replacing the published file.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".bundle-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(bundle, file, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"bundle": str(path), "digest": checksum, "cases": len(bundle["cases"])}


def query_plan(snapshot, attempts):
    rows = {row["id"]: row for row in snapshot["evidence"]}
    compatible = [attempt for attempt in attempts if attempt.get("activity")
                  and set(attempt["usage"].get("supplied_evidence_ids", [])) <= rows.keys()]
    if not compatible:
        raise ValueError("No prior query plan exists for this capture.")
    def coverage(attempt):
        traces = {source for entry in attempt["activity"] if entry["tool"] == "get_trace" for source in entry.get("evidence_ids", [])}
        errors = any(rows.get(source, {}).get("status") == "error" for source in traces)
        sources = {source for entry in attempt["activity"] for source in entry.get("evidence_ids", [])}
        return errors, len(traces), len(sources), attempt["created_at"]
    # A later quota failure may have only baseline reads. Preserve the most
    # informative recorded trace plan instead of discarding earlier queries.
    return max(compatible, key=coverage)


async def capture(case, recapture=False):
    from scripts.verify_demo_captures import verify
    directory = BASE / case
    snapshot = directory / "snapshot.json"
    capture_version = 1
    if recapture and snapshot.exists():
        previous = read(snapshot)
        capture_version = previous.get("capture_version", 1) + 1
        archive = directory / "retained" / previous["evidence_digest"]
        archive.mkdir(parents=True, exist_ok=True)
        for name in ("snapshot.json", "selected.json", "review.json"):
            source = directory / name
            if source.exists():
                source.replace(archive / name)
    if snapshot.exists():
        data = read(snapshot)
        if demo.digest(data["evidence"]) != data["evidence_digest"]:
            raise ValueError("Existing snapshot changed; retain it and use a new dataset version.")
        print(json.dumps({"case": case, "state": "already captured", "logs": data["collection"]["summary"]["input_lines"]}), flush=True)
        return
    shipped = ROOT / "demo" / "v1" / "recordings.json"
    if shipped.exists() and not recapture:
        archived = next((item for item in read(shipped)["cases"] if item["slug"] == case), None)
        if archived:
            if demo.digest(archived["evidence"]) != archived["evidence_digest"]:
                raise ValueError("Shipped recording checksum does not match.")
            verify(case, archived["evidence"])
            save(snapshot, archived)
            print(json.dumps({"case": case, "state": "restored shipped recording", "logs": archived["collection"]["summary"]["input_lines"]}), flush=True)
            return
    stamp = db.uid()
    runtime = LabRuntime(case, stamp, directory / "executions" / stamp, lambda rows: None, recording=True)
    async with asyncio.timeout(360):
        await runtime.execute()
    rows = runtime.rows
    if sum(row["kind"] == "log" for row in rows) < 500:
        raise ValueError("The case did not capture 500 log records.")
    verify(case, rows)
    meta = next(item for item in CATALOG if item["id"] == case)
    data = {"slug": case, "capture_version": capture_version, **{k: meta[k] for k in ("title", "description", "category")}, "evidence": rows, "evidence_digest": demo.digest(rows),
            "collection": {"id": db.uid(), "created_at": rows[0]["timestamp"], "symptom": "Investigate the supplied operational observations.", "summary": summary(rows, case == "incomplete")}}
    save(snapshot, data)
    print(json.dumps({"case": case, "state": "captured", "logs": sum(r["kind"] == "log" for r in rows), "observations": len(rows), "requests": runtime.requests, "failures": runtime.failures}), flush=True)


def install_preparation(snapshot):
    collection = snapshot["collection"]
    if db.one("SELECT id FROM collections WHERE id=?", (collection["id"],)):
        return
    db.execute("INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(?,?,?,?,?,?,?,?)",
        (collection["id"], OWNER, "Captured observations", collection["symptom"], "live_scenario", collection["created_at"], snapshot["evidence_digest"], json.dumps(collection["summary"])))
    store_rows(collection["id"], OWNER, snapshot["evidence"])


async def analyze(cases, live, retry, questions=None, reuse_queries=False):
    from backend.investigator import Investigator
    from backend.provider import groq_http_client, jev_http_client
    from backend.triage import JevTriage
    if not live:
        print("Analysis requires --live; no provider request made.")
        return
    db.DATA = BASE / "preparation"
    db.init()
    db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?,?,?)", (OWNER, "Operator", "operator@evaluation.invalid", "login-disabled", db.now()))
    class PreparationInvestigator(Investigator):
        async def groq(self, tools, record, usage=None, progress=lambda state: None):
            question = (questions or {}).get(self.case)
            if question:
                if not isinstance(question, str) or len(question) > 500:
                    raise ValueError("Operator symptom questions must be at most 500 characters.")
                tools.collection = {**tools.collection, "symptom": question}
                usage["operator_question"] = question
            if reuse_queries:
                usage["query_plan_attempt"] = self.plan_attempt
            return await super().groq(tools, record, usage, progress, replay_queries=self.plan if reuse_queries else None)

    async with groq_http_client() as groq, jev_http_client() as jev:
        worker = PreparationInvestigator(groq, triage=JevTriage(jev))
        for case in cases:
            directory = BASE / case
            snapshot = read(directory / "snapshot.json")
            install_preparation(snapshot)
            current = directory / "selected.json"
            if current.exists() and not retry:
                print(json.dumps({"case": case, "state": "saved attempt retained; use --retry for a new attempt"}), flush=True)
                continue
            identifier = db.uid()
            worker.case = case
            if reuse_queries:
                attempts = [read(path) for path in (directory / "attempts").glob("*.json")]
                previous = query_plan(snapshot, attempts)
                worker.plan = [{key: entry[key] for key in ("tool", "arguments")} for entry in previous["activity"]]
                worker.plan_attempt = previous["id"]
            db.execute("INSERT INTO investigations(id,collection_id,user_id,engine,state,created_at,usage) VALUES(?,?,?,?,?,?,?)",
                (identifier, snapshot["collection"]["id"], OWNER, "groq", "queued", db.now(), json.dumps({"triage": {"requested": True, "state": "pending"}})))
            await worker.investigate(identifier)
            row = db.one("SELECT * FROM investigations WHERE id=?", (identifier,))
            result = {k: row[k] for k in ("id", "engine", "state", "created_at", "completed_at", "error")}
            result.update({k: json.loads(row[k]) if row[k] else None for k in ("report", "usage", "activity")})
            save(directory / "attempts" / f"{identifier}.json", result)
            save(current, result)
            print(json.dumps({"case": case, "id": identifier, "state": result["state"], "error": result["error"]}), flush=True)
            if result["state"] == "failed" and result["usage"].get("retry_at"):
                print(json.dumps({"state": "preparation deferred", "retry_at": result["usage"]["retry_at"],
                                  "reason": "Provider cooldown; remaining cases were not sent to either provider."}), flush=True)
                break


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "analyze", "select", "review", "export", "install"])
    parser.add_argument("--case", choices=sorted(CASE_IDS) + ["all"], nargs="+", default=["all"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--recapture", action="store_true", help="Retain the previous draft capture and reports, then record a new capture version.")
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--questions-file", type=Path, help="Optional JSON case-to-symptom questions; never expected answers or fault controls. Retained with provider provenance.")
    parser.add_argument("--attempt", help="Existing completed provider attempt ID to select without changing its content.")
    parser.add_argument("--reuse-queries", action="store_true", help="Replay a retained read-only query plan for this fixed capture, then request fresh Jev triage and a Groq report.")
    args = parser.parse_args()
    cases = [item["id"] for item in CATALOG] if "all" in args.case else list(dict.fromkeys(args.case))
    BASE.mkdir(parents=True, exist_ok=True)
    lock = BASE / ("capture.lock" if args.action == "capture" else "analysis.lock")
    try:
        handle = lock.open("x")
    except FileExistsError:
        raise SystemExit("Another command for this preparation stage is active; inspect the retained lock if a previous command was interrupted.")
    try:
        if args.action == "capture":
            for case in cases:
                await capture(case, args.recapture)
        elif args.action == "analyze":
            await analyze(cases, args.live, args.retry, read(args.questions_file) if args.questions_file else None, args.reuse_queries)
        elif args.action == "review":
            if len(cases) != 1 or not args.notes_file:
                raise ValueError("Review one case with --notes-file after inspecting its actual report and evidence.")
            directory = BASE / cases[0]
            investigation = read(directory / "selected.json")
            snapshot = read(directory / "snapshot.json")
            if investigation["state"] != "completed":
                raise ValueError("Cannot accept a failed investigation.")
            notes = args.notes_file.read_text(encoding="utf-8").strip()
            if len(notes) < 80:
                raise ValueError("Record meaningful source-based review notes.")
            save(directory / "review.json", {"accepted": True, "reviewed_at": db.now(), "notes": notes,
                 "report_digest": demo.digest(investigation), "evidence_digest": snapshot["evidence_digest"]})
        elif args.action == "select":
            if len(cases) != 1 or not args.attempt or not args.attempt.isalnum():
                raise ValueError("Select one case and an existing attempt ID.")
            directory = BASE / cases[0]
            attempt = read(directory / "attempts" / f"{args.attempt}.json")
            if attempt["state"] != "completed":
                raise ValueError("Only a completed provider response can be selected.")
            snapshot_ids = {row["id"] for row in read(directory / "snapshot.json")["evidence"]}
            if not set(attempt["usage"].get("supplied_evidence_ids", [])) <= snapshot_ids:
                raise ValueError("Attempt belongs to an earlier capture.")
            save(directory / "selected.json", attempt)
        elif args.action == "export":
            cases = [{**read(BASE / case / "snapshot.json"), "investigation": read(BASE / case / "selected.json"), "review": read(BASE / case / "review.json")} for case in cases]
            print(json.dumps(export_bundle(cases)))
        else:
            db.init()
            print(json.dumps({"installed": demo.install()}))
    finally:
        handle.close()
        lock.unlink()


if __name__ == "__main__":
    asyncio.run(main())
