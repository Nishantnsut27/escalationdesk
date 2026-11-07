"""Shared validated filters, complete-data aggregates and recorded correlation."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from collections import Counter
import math
import re

from . import db
from .signatures import VERSION, LABELS, catalogue

LEVELS = ("trace", "debug", "info", "warning", "error", "unknown")
KINDS = ("log", "span", "metric")
ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")


def parsed_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if not parsed.tzinfo:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ValueError("Time filters require ISO-8601 timestamps with a timezone.") from None


@dataclass(frozen=True)
class Filters:
    kind: str | None = None
    service: str | None = None
    level: str | None = None
    query: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    error_type: str | None = None

    @classmethod
    def parse(cls, parameters, *, services=None):
        values = dict(parameters)
        if set(values) - set(cls.__dataclass_fields__):
            raise ValueError("Unsupported evidence filter.")
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise ValueError("Evidence filters must contain nonempty text.")
        if values.get("kind") not in (None, *KINDS) or values.get("level") not in (None, *LEVELS):
            raise ValueError("Unknown evidence kind or severity.")
        if "service" in values and (len(values["service"]) > 80 or (services is not None and values["service"] not in services)):
            raise ValueError("Unknown service in this evidence scope.")
        if "error_type" in values and values["error_type"] not in LABELS:
            raise ValueError("Unknown error signature.")
        if "query" in values and not 1 <= len(values["query"]) <= 200:
            raise ValueError("Use a search query of 1-200 characters.")
        for key in ("trace_id", "request_id"):
            if key in values and not ID.fullmatch(values[key]):
                raise ValueError("Use an exact recorded request or trace ID.")
        for key in ("start_time", "end_time"):
            if key in values:
                values[key] = parsed_time(values[key]).isoformat()
        if values.get("start_time") and values.get("end_time") and values["start_time"] > values["end_time"]:
            raise ValueError("Time range start must not follow its end.")
        return cls(**values)

    def values(self):
        return {key: value for key, value in asdict(self).items() if value is not None}


def scope_sql(owner, *, collection_id=None, run_id=None, demo=False, filters=None, include_time=True):
    clauses, args = ["e.user_id=%s"], [owner]
    if collection_id:
        clauses.append("e.collection_id=%s")
        args.append(collection_id)
    if run_id:
        clauses.append("e.run_id=%s")
        args.append(run_id)
    if demo:
        clauses.append("EXISTS (SELECT 1 FROM demo_cases d WHERE d.collection_id=e.collection_id)")
    for key, value in (filters or Filters()).values().items():
        if key in {"kind", "service"}:
            clauses.append(f"e.{key}=%s")
        elif key == "query":
            clauses.append("strpos(lower(COALESCE(i.search_text,'')),lower(%s))>0")
        elif key == "error_type":
            clauses.append("EXISTS (SELECT 1 FROM evidence_error_tags t WHERE t.evidence_id=e.id AND t.version=%s AND t.signature=%s)")
            args.append(VERSION)
        elif key in {"start_time", "end_time"}:
            if not include_time:
                continue
            clauses.append("i.observed_at" + (">=" if key == "start_time" else "<=") + "%s::timestamptz")
        else:
            clauses.append(f"i.{key}=%s")
        args.append(value)
    return " FROM evidence e JOIN evidence_index i ON i.evidence_id=e.id WHERE " + " AND ".join(clauses), args


def page(owner, *, limit=100, offset=0, selected=None, locate=True, **scope):
    statement, args = scope_sql(owner, **scope)
    total = db.one("SELECT COUNT(*) n" + statement, args)["n"]
    selected_match = None
    if selected:
        selected_match = db.one("SELECT position FROM (SELECT e.id,row_number() OVER (ORDER BY i.observed_at NULLS LAST,i.source_line,e.id)-1 position" + statement + ") ranked WHERE id=%s", (*args, selected))
        if selected_match and locate:
            offset = selected_match["position"] // limit * limit
    rows = db.all_rows("SELECT e.*,i.observed_at" + statement + " ORDER BY i.observed_at NULLS LAST,i.source_line,e.id LIMIT %s OFFSET %s", (*args, limit, offset))
    result = {"evidence": [db.public_evidence(row) for row in rows], "total": total,
              "next_offset": offset + limit if offset + limit < total else None, "offset": offset,
              "selected_in_filter": bool(selected_match) if selected else None}
    unknown_statement, unknown_args = scope_sql(owner, **{**scope, "include_time": False})
    result["unknown_timestamps"] = db.one("SELECT COUNT(*) n" + unknown_statement + " AND i.observed_at IS NULL", unknown_args)["n"]
    return result


def histogram(owner, *, buckets=32, **scope):
    # Aggregate every matching log, independently of the row-page limit.
    filters = Filters(**{**scope.pop("filters", Filters()).values(), "kind": "log"})
    statement, args = scope_sql(owner, filters=filters, **scope)
    extent = db.one("SELECT MIN(i.observed_at) first,MAX(i.observed_at) last,COUNT(*) total,COUNT(i.observed_at) timed" + statement, args)
    timeless, timeless_args = scope_sql(owner, filters=filters, include_time=False, **scope)
    unknown = db.one("SELECT COUNT(*) n" + timeless + " AND i.observed_at IS NULL", timeless_args)["n"]
    result = {"buckets": [], "total": extent["total"], "timed": extent["timed"], "unknown_timestamps": unknown,
              "note": "Log observations from saved snapshots. Unknown timestamps are excluded from time buckets."}
    if not extent["first"]:
        return result
    width = max(1, math.ceil((extent["last"] - extent["first"]).total_seconds() / buckets))
    start = extent["first"]
    counts = db.all_rows("SELECT floor(extract(epoch FROM (i.observed_at-%s::timestamptz))/%s)::integer bucket,COALESCE(i.level,'unknown') level,COUNT(*) n" + statement + " AND i.observed_at IS NOT NULL GROUP BY 1,2 ORDER BY 1", (start, width, *args))
    table = {}
    for row in counts:
        table.setdefault(row["bucket"], Counter())[row["level"]] += row["n"]
    for bucket in range(max(table, default=0) + 1):
        stamp = start + timedelta(seconds=bucket * width)
        severities = {level: table.get(bucket, {}).get(level, 0) for level in LEVELS}
        result["buckets"].append({"start": stamp.isoformat(), "end": (stamp + timedelta(seconds=width) - timedelta(microseconds=1)).isoformat(), **severities, "count": sum(severities.values())})
    return result


def recorded_flow(rows, *, scope_label):
    spans = [row for row in rows if row["kind"] == "span"]
    index = {}
    for row in spans:
        if row.get("trace_id") and row.get("span_id"):
            index.setdefault((row["trace_id"], row["span_id"]), []).append(row)
    nodes, edges = [], []
    for row in spans:
        parents = index.get((row.get("trace_id"), row.get("parent_id")), []) if row.get("parent_id") else []
        parent = parents[0] if len(parents) == 1 and parents[0]["id"] != row["id"] else None
        nodes.append({"id": row["id"], "service": row["service"], "operation": row.get("name") or row.get("span_op") or "Unnamed operation",
                      "status": row.get("status", "unknown"), "duration_ms": row.get("duration_ms"), "timestamp": row.get("started_at") or row.get("timestamp"),
                      "span_id": row.get("span_id"), "trace_id": row.get("trace_id"), "parent_span_id": row.get("parent_id"),
                      "missing_parent": bool(row.get("parent_id")) and not parent, "ambiguous_parent": len(parents) > 1})
        if parent:
            edges.append({"id": parent["id"] + ":" + row["id"], "source": parent["id"], "target": row["id"], "relationship": "recorded_parent_span"})
    def order(row):
        try:
            return (0, parsed_time(row.get("timestamp")).isoformat(), row.get("source_line", 0), row["id"])
        except ValueError:
            return (1, "", row.get("source_line", 0), row["id"])
    events = [{key: row.get(key) for key in ("id", "service", "timestamp", "message", "level", "request_id", "trace_id")} for row in sorted(rows, key=order) if row["kind"] == "log"]
    return {"mode": "spans" if spans else "events", "nodes": nodes, "edges": edges, "events": events, "total": len(rows),
            "scope": scope_label, "note": "Full request context: severity, service, time and text filters do not remove intermediate operations. Edges represent recorded parent spans, not proven causes." if spans else
            "Span-based flow is unavailable. These are correlated log events in recorded order; order alone does not establish causation."}


def request_flow(owner, *, filters, **scope):
    correlation = {key: value for key, value in filters.values().items() if key in {"trace_id", "request_id"}}
    if "trace_id" in correlation:
        correlation.pop("request_id", None)
    if not correlation:
        return {"mode": "unselected", "nodes": [], "edges": [], "events": [], "total": 0, "scope": "No request selected",
                "note": "Select an exact recorded trace or request to inspect its context. No relationship is inferred."}
    statement, args = scope_sql(owner, filters=Filters(**correlation), **scope)
    rows = [db.public_evidence(row) for row in db.all_rows("SELECT e.*" + statement + " ORDER BY i.observed_at NULLS LAST,i.source_line,e.id", args)]
    key, value = next(iter(correlation.items()))
    return recorded_flow(rows, scope_label=key.replace("_id", "") + " " + value)


def summary(owner, **scope):
    statement, args = scope_sql(owner, **scope)
    counts = db.one("SELECT COUNT(*) observations,COUNT(DISTINCT e.service) FILTER (WHERE e.service<>'unknown') services,COUNT(*) FILTER (WHERE e.kind='log' AND i.level='error') error_logs,COUNT(*) FILTER (WHERE i.observed_at IS NULL) missing_timestamps,COUNT(*) FILTER (WHERE e.service='unknown') missing_services,COUNT(*) FILTER (WHERE i.trace_id IS NOT NULL OR i.request_id IS NOT NULL) correlated,COUNT(*) FILTER (WHERE e.kind='span') spans,COUNT(*) FILTER (WHERE e.kind='metric') metrics" + statement, args)
    errors = db.all_rows("SELECT e.service,COUNT(*) observations" + statement + " AND e.kind='log' AND i.level='error' GROUP BY e.service ORDER BY observations DESC,e.service LIMIT 12", args)
    tags = db.all_rows("SELECT t.signature,COUNT(*) observations FROM evidence_error_tags t JOIN evidence e ON e.id=t.evidence_id JOIN evidence_index i ON i.evidence_id=e.id WHERE " + statement.split(" WHERE ", 1)[1] + " AND t.version=%s AND e.kind='log' AND (i.level='error' OR t.signature<>'unknown') GROUP BY t.signature ORDER BY observations DESC,t.signature", (*args, VERSION))
    counts["note"] = "Observations from saved snapshots; these are not unique requests or a failure rate."
    timeless, timeless_args = scope_sql(owner, **{**scope, "include_time": False})
    counts["unknown_time_in_scope"] = db.one("SELECT COUNT(*) n" + timeless + " AND i.observed_at IS NULL", timeless_args)["n"]
    return {"counts": counts, "top_services": errors, "signatures": [{**row, "label": LABELS.get(row["signature"], row["signature"])} for row in tags], "catalogue": catalogue()}
