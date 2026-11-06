"""Offline installation of reviewed controlled recordings. No provider access."""
import hashlib
import json
from pathlib import Path
from . import db
from .catalog import CASE_IDS
from .config import ROOT
from .investigator import validate_report

DEMO_ID = "system-recorded-demo"
BUNDLE = ROOT / "demo" / "v1" / "bundle.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def ready():
    return bool(case_count() and db.one(
        "SELECT 1 FROM users JOIN account_roles ON users.id=account_roles.user_id WHERE users.id=? AND role='demo'",
        (DEMO_ID,)))


def case_count():
    return db.one("SELECT COUNT(*) n FROM demo_cases")["n"]


def validate(bundle):
    cases = bundle.get("cases", [])
    slugs = {case["slug"] for case in cases}
    if bundle.get("version") != "v1" or not cases or not slugs <= CASE_IDS or len(slugs) != len(cases):
        raise ValueError("The demo bundle must contain distinct, reviewed cases from the versioned catalog.")
    identities = set()
    for case in bundle["cases"]:
        rows, investigation = case["evidence"], case["investigation"]
        ids = {row["id"] for row in rows}
        if len(ids) != len(rows) or sum(row["kind"] == "log" for row in rows) < 500:
            raise ValueError("Each recording needs 500 distinct captured log records.")
        all_ids = ids | {case["collection"]["id"], investigation["id"]}
        if identities & all_ids:
            raise ValueError("Demo identities conflict.")
        identities.update(all_ids)
        if case["evidence_digest"] != digest(rows) or investigation["state"] != "completed" or investigation["engine"] != "groq":
            raise ValueError("Unfinished or changed recording.")
        review = case["review"]
        if review.get("accepted") is not True or not review.get("notes") or not review.get("reviewed_at") or review.get("report_digest") != digest(investigation) or review.get("evidence_digest") != digest(rows):
            raise ValueError("Each report needs a current source review.")
        supplied = set(investigation["usage"].get("supplied_evidence_ids", []))
        if not supplied or not supplied <= ids:
            raise ValueError("Recorded supplied sources are missing.")
        tools = type("SavedScope", (), {"services": {r["service"] for r in rows}, "supplied": supplied, "groq": True})()
        validate_report(investigation["report"], tools)
        for entry in investigation["activity"]:
            if not set(entry.get("evidence_ids", [])) <= ids:
                raise ValueError("Tool sources are missing.")
        triage = investigation["usage"].get("triage", {})
        if not set(triage.get("source_ids", [])) <= ids:
            raise ValueError("Triage sources are missing.")
        if not investigation.get("completed_at") or not investigation["usage"].get("model"):
            raise ValueError("Provider provenance is missing.")
        if case["slug"] == "incomplete" and investigation["report"]["assessment"] != "inconclusive":
            raise ValueError("The restricted-evidence case must be inconclusive.")
    return digest(bundle)


def install(path=BUNDLE):
    from .auth import hasher
    path = Path(path)
    if not path.exists():
        return False
    bundle = json.loads(path.read_text(encoding="utf-8"))
    checksum = validate(bundle)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT digest FROM demo_datasets WHERE version=?", (bundle["version"],)).fetchone()
        if previous:
            if previous["digest"] == checksum:
                return True
            supplied = {case["slug"]: digest(case) for case in bundle["cases"]}
            for row in conn.execute("SELECT slug,metadata FROM demo_cases"):
                if supplied.get(row["slug"]) != json.loads(row["metadata"]).get("case_digest"):
                    raise ValueError("An installed demo cannot be removed or rewritten by a bundle update.")
        else:
            if conn.execute("SELECT 1 FROM users WHERE id=? OR email=?", (DEMO_ID, "demo@escalationdesk.invalid")).fetchone():
                raise ValueError("Reserved demo identity conflicts with an existing account.")
            conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (DEMO_ID, "Demo Account", "demo@escalationdesk.invalid", hasher.hash("Demo@login"), db.now()))
            conn.execute("INSERT INTO account_roles VALUES(?, 'demo')", (DEMO_ID,))
            conn.execute("INSERT INTO demo_datasets VALUES(?,?,?)", (bundle["version"], checksum, db.now()))
        for position, case in enumerate(bundle["cases"]):
            if conn.execute("SELECT 1 FROM demo_cases WHERE slug=?", (case["slug"],)).fetchone():
                continue
            collection, investigation = case["collection"], case["investigation"]
            conn.execute("INSERT INTO collections(id,user_id,title,symptom,source_type,created_at,fingerprint,summary) VALUES(?,?,?,?,?,?,?,?)",
                (collection["id"], DEMO_ID, case["title"], collection["symptom"], "recorded_demo", collection["created_at"], case["evidence_digest"], json.dumps(collection["summary"])))
            for row in case["evidence"]:
                conn.execute("INSERT INTO evidence(id,collection_id,user_id,kind,service,timestamp,payload) VALUES(?,?,?,?,?,?,?)",
                    (row["id"], collection["id"], DEMO_ID, row["kind"], row["service"], row.get("timestamp"), json.dumps(row)))
            conn.execute("INSERT INTO investigations(id,collection_id,user_id,engine,state,created_at,completed_at,report,activity,usage) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (investigation["id"], collection["id"], DEMO_ID, "groq", "completed", investigation["created_at"], investigation["completed_at"],
                 json.dumps(investigation["report"]), json.dumps(investigation["activity"]), json.dumps(investigation["usage"])))
            metadata = {key: case[key] for key in ("slug", "title", "description", "category")}
            metadata.update(version=bundle["version"], case_digest=digest(case), recorded_at=collection["created_at"], collection_id=collection["id"], investigation_id=investigation["id"],
                            counts={kind: sum(row["kind"] == kind for row in case["evidence"]) for kind in ("log", "span", "metric")})
            conn.execute("INSERT INTO demo_cases VALUES(?,?,?,?,?,?)", (case["slug"], bundle["version"], position, json.dumps(metadata), collection["id"], investigation["id"]))
        conn.execute("UPDATE demo_datasets SET digest=? WHERE version=?", (checksum, bundle["version"]))
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Demo installation failed relationship validation.")
    return True
