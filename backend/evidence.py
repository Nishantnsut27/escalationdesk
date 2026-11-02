"""Validated, account-scoped queries and bounded model-visible evidence."""
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from . import db

SERVICES = {"gateway", "order", "payment"}
TOOL_NAMES = ("search_logs", "get_metrics", "find_traces", "get_trace", "get_topology")
TOOL_BYTES = 4096
INPUT_BYTES = 10240

def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

COMMON = {
    "service": {"type": "string", "enum": sorted(SERVICES)},
    "start_time": {"type": "string", "description": "Inclusive ISO-8601 timestamp with timezone."},
    "end_time": {"type": "string", "description": "Inclusive ISO-8601 timestamp with timezone."},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
    "offset": {"type": "integer", "minimum": 0, "maximum": 5000},
}
TRACE = {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": "Nonzero W3C trace ID."}
PROPERTIES = {
    "search_logs": {**COMMON, "trace_id": TRACE,
                    "query": {"type": "string", "maxLength": 200, "description": "Literal message substring."},
                    "level": {"type": "string", "enum": ["trace", "debug", "info", "warning", "error", "unknown"]}},
    "get_metrics": COMMON,
    "find_traces": COMMON,
    "get_trace": {**COMMON, "trace_id": TRACE},
    "get_topology": {},
}
DEFINITIONS = [{"type": "function", "function": {
    "name": name, "description": {
        "search_logs": "Read retained logs in this investigation; filter messages/levels or page with offset.",
        "get_metrics": "Read metric samples, including pool usage and capacity.",
        "find_traces": "Find measured operation spans; use get_trace for a complete trace.",
        "get_trace": "Read spans in one trace, preserving parent relationships and timings.",
        "get_topology": "Read configured dependencies; these are not proof of observed failures.",
    }[name], "parameters": {"type": "object", "properties": PROPERTIES[name],
                            "required": ["trace_id"] if name == "get_trace" else [],
                            "additionalProperties": False}}} for name in TOOL_NAMES]

def parse_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        raise ValueError("Time windows require ISO-8601 timestamps with a timezone.") from None

class EvidenceTools:
    def __init__(self, run_id, owner, *, groq=False, collection_id=None):
        self.run_id, self.owner, self.groq = run_id, owner, groq
        self.collection_id = collection_id
        self.collection = db.one("SELECT * FROM collections WHERE id=? AND user_id=?", (collection_id, owner)) if collection_id else None
        if collection_id and not self.collection:
            raise ValueError("Evidence collection is unavailable.")
        self.services = set(json.loads(self.collection["summary"])["services"]) if self.collection else SERVICES
        self.definitions = deepcopy(DEFINITIONS)
        if self.collection:
            for definition in self.definitions:
                properties = definition["function"]["parameters"]["properties"]
                if "service" in properties:
                    properties["service"]["enum"] = sorted(self.services)
                if "trace_id" in properties:
                    properties["trace_id"] = {"type": "string", "maxLength": 128, "description": "Recorded trace ID."}
        self.seen = {}
        self.supplied = set()

    def call(self, name, arguments):
        if name not in TOOL_NAMES or not isinstance(arguments, dict):
            raise ValueError("Unknown tool or invalid arguments")
        if set(arguments) - set(PROPERTIES[name]):
            raise ValueError("Unsupported tool argument")
        args = dict(arguments)
        if "service" in args and (not isinstance(args["service"], str) or args["service"] not in self.services):
            raise ValueError("Invalid service")
        if "trace_id" in args or name == "get_trace":
            trace = args.get("trace_id")
            if not isinstance(trace, str) or not (re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", trace) if self.collection else
                    re.fullmatch(r"[0-9a-f]{32}", trace) and int(trace, 16) != 0):
                raise ValueError("A valid nonzero trace_id is required")
        for field in ("start_time", "end_time"):
            if field in args:
                args[field] = parse_time(args[field])
        if args.get("start_time", "") > args.get("end_time", "~"):
            raise ValueError("Time window start must not follow its end")
        offset = args.get("offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 5000:
            raise ValueError("Offset must be between zero and 5000")
        if "query" in args and (not isinstance(args["query"], str) or not 1 <= len(args["query"]) <= 200):
            raise ValueError("Search query must be 1-200 characters")
        if "level" in args and args["level"] not in PROPERTIES["search_logs"]["level"]["enum"]:
            raise ValueError("Invalid log level")
        limit = args.pop("limit", 20 if self.groq else 100)
        maximum = 50 if self.groq else 100
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= maximum:
            raise ValueError(f"Limit must be between 1 and {maximum}")
        if name == "get_topology":
            return ({"configured_edges": [], "note": "No configured topology supplied. Missing trace links are not inferred."}
                    if self.collection else {"configured_edges": [["gateway", "order"], ["order", "payment"]]})
        kind = {"search_logs": "log", "get_metrics": "metric", "find_traces": "span", "get_trace": "span"}[name]
        rows = db.evidence(self.run_id, self.owner, kind, limit=limit + (1 if self.groq else 0), collection_id=self.collection_id, **args)
        selected = rows[:limit]
        self.seen.update({row["id"]: row for row in selected})
        if not self.groq:
            return selected
        result = compact([model_observation(row) for row in selected], TOOL_BYTES)
        result["query_limit_reached"] = len(rows) > limit
        if self.collection and kind not in json.loads(self.collection["summary"])["kinds"]:
            result["note"] = "This evidence kind was not supplied; it is unavailable, not proof of healthy operation."
        # Reserve space for the limit disclosure in compact().
        return result

def model_observation(row):
    # Original redacted content remains inspectable; avoid repeating it in model input.
    return {key: value for key, value in row.items() if key not in {"original", "source_format", "source_id"}}

# All source observations remain in storage. Grouping retains a real representative,
# never manufactures a composite source ID or overwrites the original.
def representatives(rows):
    groups = {}
    for row in rows:
        shape = {k: v for k, v in row.items() if k not in {
            "id", "timestamp", "started_at", "trace_id", "span_id", "parent_id", "duration_ms",
            "last_latency_ms", "requests", "failures", "acquisition_failures"}}
        for key in ("duration_ms", "last_latency_ms"):
            if key in row:
                shape[key] = int(row[key] // 500)
        for key in ("failures", "acquisition_failures"):
            if key in row:
                shape[key] = row[key] > 0
        key = encoded(shape)
        groups.setdefault(key, []).append(row)
    result = []
    for group in groups.values():
        row = dict(group[0])
        if len(group) > 1:
            row["repetitions"] = len(group)
            row["observed_until"] = group[-1]["timestamp"]
            if "duration_ms" in row:
                row["duration_ms_range"] = [min(r["duration_ms"] for r in group), max(r["duration_ms"] for r in group)]
        result.append(row)
    # Round-robin service/kind groups prevents plentiful logs crowding out metrics
    # and success observations that may conflict with a proposed explanation.
    buckets = {}
    for row in result:
        buckets.setdefault((row["kind"], row["service"]), []).append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda r: (not (r.get("status") == "error" or r.get("level") == "error"
                                      or r.get("duration_ms", 0) >= 2000), r.get("timestamp") or ""))
        def state(row):
            return (row.get("status"), row.get("level"), row.get("http_status"),
                    row.get("duration_ms", 0) >= 2000,
                    row.get("pool_in_use") == row.get("pool_capacity") if "pool_capacity" in row else None)
        different = next((i for i, row in enumerate(bucket[1:], 1) if state(row) != state(bucket[0])), None)
        if different is not None:
            bucket.insert(1, bucket.pop(different))
    return [bucket[i] for i in range(max((len(b) for b in buckets.values()), default=0))
            for bucket in buckets.values() if i < len(bucket)]

def compact(rows, budget, priority=()):
    groups = [row for row in rows if row["id"] in priority]
    groups += representatives([row for row in rows if row["id"] not in priority])
    result = {"records": [], "matched_records": len(rows), "omitted_groups": len(groups),
              "note": "Repeated observations use one cited representative. Omitted sources are not citation candidates."}
    for row in groups:
        trial = {**result, "records": result["records"] + [row], "omitted_groups": result["omitted_groups"] - 1}
        if len(encoded(trial)) <= budget - 40:
            result = trial
    return result

def timing_comparisons(rows):
    """Compare measured durations only when a complete parent path is supplied."""
    spans = {(r.get("trace_id"), r.get("span_id")): r for r in rows if r["kind"] == "span"}
    comparisons = []
    for log in rows:
        if not log.get("downstream") or not isinstance(log.get("timeout_ms"), (int, float)):
            continue
        operations = []
        for span in spans.values():
            if (span["service"] != log["downstream"] or not log.get("trace_id") or not log.get("span_id")
                    or span.get("trace_id") != log.get("trace_id") or not isinstance(span.get("duration_ms"), (int, float))):
                continue
            path, parent = [span["id"]], span.get("parent_id")
            visited = set()
            while parent and parent != log.get("span_id") and parent not in visited:
                visited.add(parent)
                ancestor = spans.get((log["trace_id"], parent))
                if ancestor is None:
                    break
                path.append(ancestor["id"])
                parent = ancestor.get("parent_id")
            if parent == log.get("span_id"):
                operations.append({"source": span["id"], "operation": span.get("name", "Unnamed span"), "duration_ms": span["duration_ms"],
                                   "exceeds_caller_timeout": span["duration_ms"] > log["timeout_ms"],
                                   "relationship_sources": path})
        if operations:
            comparisons.append({"timeout_source": log["id"], "caller": log["service"], "downstream": log["downstream"],
                                "timeout_ms": log["timeout_ms"], "operations": operations[:4]})
        if len(comparisons) == 2:
            break
    return comparisons

def http_exchanges(rows):
    spans = {(r.get("trace_id"), r.get("span_id")): r for r in rows if r["kind"] == "span"}
    exchanges = []
    for log in rows:
        if log["kind"] != "log" or not log.get("downstream"):
            continue
        span = spans.get((log.get("trace_id"), log.get("span_id")))
        if span is None or not log.get("trace_id") or not log.get("span_id") or "duration_ms" not in span:
            continue
        if "http_status" in log:
            event = {"event": "received_downstream_error_response", "response_status": log["http_status"]}
        elif "timeout_ms" in log:
            event = {"event": "caller_timeout", "explicit_deadline_ms": log["timeout_ms"]}
        else:
            continue
        exchanges.append({"caller": log["service"], "downstream": log["downstream"], **event,
                          "measured_elapsed_ms_not_deadline": span["duration_ms"], "sources": [log["id"], span["id"]]})
    return exchanges[:4]

def evidence_tables(records):
    tables = {}
    for kind in ("log", "metric", "span"):
        rows = [r for r in records if r["kind"] == kind]
        if rows:
            columns = list(dict.fromkeys(k for row in rows for k in row if k != "kind"))
            tables[kind] = {"columns": columns, "rows": [[r.get(k) for k in columns] for r in rows]}
    return tables

class EvidenceContext:
    def __init__(self, tools):
        self.tools = tools
        self.records = {}
        self.queries = []
        self.topology = None
        self.trace_sources = set()

    def add(self, name, args, result):
        if isinstance(result, dict):
            self.records.update({r["id"]: r for r in result.get("records", [])})
            if "configured_edges" in result:
                self.topology = result
            if name == "get_trace" and result.get("records"):
                self.trace_sources = {r["id"] for r in result["records"]}
        self.queries.append({"tool": name, "arguments": args,
                             "returned": len(result.get("records", [])) if isinstance(result, dict) else len(result),
                             "omitted_groups": result.get("omitted_groups", 0) if isinstance(result, dict) else 0,
                             "query_limit_reached": result.get("query_limit_reached", False) if isinstance(result, dict) else False})

    def payload(self, base, system, instruction, budget=INPUT_BYTES):
        evidence_budget = budget
        while evidence_budget >= 0:
            evidence = compact(list(self.records.values()), evidence_budget, self.trace_sources)
            context = {"recent_queries": self.queries[-6:],
                       "earlier_queries_summarized": max(0, len(self.queries) - 6),
                       "query_counts": {name: sum(q["tool"] == name for q in self.queries) for name in TOOL_NAMES},
                       "evidence": {**{k: v for k, v in evidence.items() if k != "records"},
                                    "tables": evidence_tables(evidence["records"])}, "topology": self.topology,
                       "measured_timing_comparisons": timing_comparisons(evidence["records"]),
                       "observed_http_exchanges": http_exchanges(evidence["records"])}
            if self.tools.collection:
                context["import"] = {"symptom_claim": self.tools.collection["symptom"],
                                     "coverage": json.loads(self.tools.collection["summary"])}
            payload = {**base, "messages": [{"role": "system", "content": system},
                {"role": "user", "content": instruction + "\nOperational data: " + encoded(context).decode()}]}
            supplied = sorted({row["id"] for row in evidence["records"]})
            if "response_format" in payload and supplied:
                payload["response_format"] = deepcopy(payload["response_format"])
                schema = payload["response_format"]["json_schema"]["schema"]
                schema["properties"]["evidence_ids"]["items"]["enum"] = supplied
                schema["properties"]["hypotheses"]["items"]["properties"]["evidence_ids"]["items"]["enum"] = supplied
            excess = len(encoded(payload)) - budget
            if excess <= 0:
                if self.records and not supplied:
                    raise ValueError("No useful observation fits the complete Groq input bound. Import smaller observations.")
                if self.trace_sources and not self.trace_sources <= set(supplied):
                    raise ValueError("The requested trace group cannot fit intact within the Groq input bound.")
                return payload, set(supplied)
            evidence_budget -= max(128, excess)
        raise ValueError("Groq input allowance is insufficient for the required request schema.")
