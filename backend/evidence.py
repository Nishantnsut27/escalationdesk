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
    "search_logs": {**COMMON, "trace_id": TRACE, "request_id": {"type": "string", "maxLength": 128},
                    "error_type": {"type": "string", "description": "Explicit local error signature identifier."},
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
        "get_trace": "Read bounded spans in one trace; matching logs/errors are also collected automatically.",
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
        self.collection = db.one("SELECT * FROM collections WHERE id=%s AND user_id=%s", (collection_id, owner)) if collection_id else None
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
        self.last_excerpts = []
        self.selection = {}

    def snapshot(self):
        from .selection import fetch_owned
        return fetch_owned(self)

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
        if "request_id" in args and (not isinstance(args["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", args["request_id"])):
            raise ValueError("A recorded request ID is required")
        if "error_type" in args:
            from .signatures import LABELS
            if not isinstance(args["error_type"], str) or args["error_type"] not in LABELS:
                raise ValueError("Unknown error signature")
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
    return {key: value for key, value in row.items() if key not in {
        "original", "collection_id", "run_id", "source_format", "source_id", "source_line", "source_end_line", "source_project_id", "source_connection_id", "source_organization", "signature_version"}
        and not (row["kind"] == "span" and ((key == "message" and value == row.get("name")) or (key == "level" and value == "unknown")))
        and not (key == "source_truncated" and value is False)}

# All source observations remain in storage. Grouping retains a real representative,
# never manufactures a composite source ID or overwrites the original.
def representatives(rows):
    groups = {}
    for row in rows:
        shape = {k: v for k, v in row.items() if k not in {
            "id", "timestamp", "started_at", "trace_id", "request_id", "span_id", "parent_id", "duration_ms",
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
                                      or r.get("http_status", 0) >= 400 or r.get("exception_type")
                                      or r.get("duration_ms", 0) >= 2000), r.get("timestamp") or ""))
        if bucket[0]["kind"] == "metric":
            keys = {key for row in bucket for key, value in row.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                    and key not in {"requests", "failures", "acquisition_failures", "repetitions", "source_line"}}
            selected = []
            for key in sorted(keys):
                samples = [row for row in bucket if isinstance(row.get(key), (int, float))]
                for sample in (max(samples, key=lambda r: r[key]), min(samples, key=lambda r: r[key])):
                    if sample not in selected:
                        selected.append(sample)
            bucket[:] = selected + [row for row in bucket if row not in selected]
            continue
        def state(row):
            return (row.get("status"), row.get("level"), row.get("http_status"),
                    row.get("duration_ms", 0) >= 2000,
                    row.get("pool_in_use") == row.get("pool_capacity") if "pool_capacity" in row else None)
        different = next((i for i, row in enumerate(bucket[1:], 1) if state(row) != state(bucket[0])), None)
        if different is not None:
            bucket.insert(1, bucket.pop(different))
    def bucket_priority(item):
        (kind, _), bucket = item
        if kind == "log" and any(any(key in row for key in ("stack_trace", "timeout_ms", "error", "error_type", "exception_type")) for row in bucket):
            return 0
        if kind == "metric" and any(any(isinstance(value, (int, float)) and key not in {
                "requests", "failures", "acquisition_failures", "repetitions", "source_line"}
                for key, value in row.items()) for row in bucket):
            return 1
        return {"log": 2, "metric": 3, "span": 4}[kind]
    ordered = sorted(buckets.items(), key=bucket_priority)
    return [bucket[i] for i in range(max((len(b) for b in buckets.values()), default=0))
            for _, bucket in ordered if i < len(bucket)]

def compact(rows, budget, priority=(), *, preserve_priority=False):
    from .selection import excerpt
    groups = [row for row in rows if row["id"] in priority]
    groups += representatives([row for row in rows if row["id"] not in priority])
    result = {"records": [], "matched_records": len(rows), "omitted_groups": len(groups),
              "note": "Repeated observations use one cited representative. Omitted sources are not citation candidates."}
    for row in groups:
        if len(encoded(row)) > min(1800, max(700, budget // 2)):
            row = excerpt(row, min(1800, max(700, budget // 2)))
        trial = {**result, "records": result["records"] + [row], "omitted_groups": result["omitted_groups"] - 1}
        if (preserve_priority and row["id"] in priority) or len(encoded(trial)) <= budget - 40:
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
        # Legacy workload server spans carry the response in attributes. Keep
        # that ownership explicit when their duplicate completion log is pruned.
        legacy_server = log["kind"] == "span" and log.get("name") in {
            log["service"]+".checkout", log["service"]+".request"}
        completed_request = log["kind"] == "log" and log.get("message", "").lower() in {
            "request completed", "checkout request completed"}
        attributes = log.get("attributes")
        response_status = log.get("http_status", attributes.get("http_status") if isinstance(attributes, dict) else None)
        if (log.get("span_op") == "http.server" or legacy_server or completed_request) and response_status is not None:
            exchanges.append({"event": "generated_server_response", "service": log["service"],
                              "response_status": response_status, "trace_id": log.get("trace_id"),
                              "sources": [log["id"]]})
            continue
        if log["kind"] != "log":
            continue
        span = spans.get((log["trace_id"], log["span_id"])) if log.get("trace_id") and log.get("span_id") else None
        downstream = log.get("downstream")
        if (not downstream and span and not span.get("span_op") and log["service"] != "unknown" and span.get("service") == log["service"]
                and span.get("name", "").startswith("http.") and span["name"] not in {"http.server", "http.client"}):
            downstream = span["name"][5:]
        if not downstream:
            continue
        if "http_status" in log:
            event = {"event": "received_downstream_error_response" if log["http_status"] >= 400 else "received_downstream_response",
                     "response_status": log["http_status"]}
        elif "timeout_ms" in log:
            event = {"event": "caller_timeout", "explicit_deadline_ms": log["timeout_ms"]}
        elif log.get("error_type") in {"ConnectError", "ConnectTimeout"}:
            event = {"event": "connection_failed_without_http_response", "error_type": log["error_type"]}
        else:
            continue
        exchange = {"caller": log["service"], "downstream": downstream, "trace_id": log.get("trace_id"), **event, "sources": [log["id"]]}
        if span and log.get("trace_id") and log.get("span_id") and "duration_ms" in span:
            exchange.update(measured_elapsed_ms_not_deadline=span["duration_ms"])
            exchange["sources"].append(span["id"])
        exchanges.append(exchange)
    unique = {}
    for exchange in exchanges:
        key = encoded({k: v for k, v in exchange.items() if k not in {"sources", "measured_elapsed_ms_not_deadline"}})
        if key not in unique:
            unique[key] = exchange
        else:
            unique[key]["sources"] = list(dict.fromkeys(unique[key]["sources"] + exchange["sources"]))[:2]
    return list(unique.values())[:6]


def instance_relationships(rows):
    """Expose only equality of explicit instance IDs, never an inferred cause."""
    relationships = []
    for caller in rows:
        target = caller.get("target_instance")
        if not isinstance(target, (str, int)) or isinstance(target, bool):
            continue
        peers = [row for row in rows if row["id"] != caller["id"] and row.get("instance") is not None
                 and str(row["instance"]) == str(target)]
        if peers:
            relationships.append({"target_instance": str(target), "request_source": caller["id"],
                "same_instance_sources": [row["id"] for row in peers[:4]],
                "note": "Shared instance identifier only; lifecycle events may precede or follow this request."})
        if len(relationships) == 2:
            break
    return relationships

def evidence_tables(records):
    tables = {}
    for kind in ("log", "metric", "span"):
        rows = [r for r in records if r["kind"] == kind]
        if rows:
            columns = list(dict.fromkeys(k for row in rows for k in row if k != "kind"))
            tables[kind] = {"columns": columns, "rows": [[r.get(k) for k in columns] for r in rows]}
    return tables


def caller_only_responses(records):
    """Recognize the narrow status-only log contract, without inferring a cause."""
    rows = list(records)
    if not rows or any(row["kind"] != "log" for row in rows):
        return False
    errors = [row for row in rows if row.get("level") == "error" or row.get("http_status", 0) >= 400]
    return bool(errors) and any(row.get("downstream") for row in errors) and all(
        not any(row.get(key) for key in ("stack_trace", "error", "error_type", "exception"))
        and (row.get("downstream") or row.get("message", "").lower() in {"request completed", "checkout request completed"})
        for row in errors)

class EvidenceContext:
    def __init__(self, tools):
        self.tools = tools
        self.records = {}
        self.queries = []
        self.topology = None
        self.trace_sources = set()
        self.selected_trace = None
        self.triage = None
        self.advisory_priority = []
        self.triage_included = "not_requested"

    def add(self, name, args, result):
        if isinstance(result, dict):
            self.records.update({r["id"]: model_observation(self.tools.seen[r["id"]]) if r["id"] in self.tools.seen else r for r in result.get("records", [])})
            if "configured_edges" in result:
                self.topology = result
            if name == "get_trace":
                self.selected_trace = args["trace_id"]
            if self.selected_trace:
                spans = {(r.get("trace_id"),r.get("span_id")): r for r in self.records.values() if r["kind"] == "span"}
                def redundant_status(row):
                    span = spans.get((row.get("trace_id"),row.get("span_id")),{})
                    return (row["kind"] == "log" and row.get("message", "").lower() in {"request completed","checkout request completed"}
                            and row.get("http_status") is not None and row["http_status"] == span.get("attributes",{}).get("http_status"))
                self.trace_sources = {r["id"] for r in self.records.values()
                                      if r.get("trace_id") == self.selected_trace and not redundant_status(r) and (
                                          r["kind"] == "span" or any(key in r for key in (
                                              "http_status", "timeout_ms", "error", "error_type", "exception_type",
                                              "instance", "target_instance", "job_id")) or r.get("level") == "error")}
        self.queries.append({"tool": name, "arguments": args,
                             "returned": len(result.get("records", [])) if isinstance(result, dict) else len(result),
                             "omitted_groups": result.get("omitted_groups", 0) if isinstance(result, dict) else 0,
                             "query_limit_reached": result.get("query_limit_reached", False) if isinstance(result, dict) else False})

    def payload(self, base, system, instruction, budget=INPUT_BYTES):
        self.triage_included = "omitted_to_preserve_evidence" if self.triage else "not_available"
        # Query arguments help select the next tool, but final reports need the
        # observations. Keep the complete query history in saved activity.
        recent = [] if "response_format" in base else self.queries[-6:]
        priority = set(self.trace_sources)
        # Keep one independently successful HTTP observation alongside a failing
        # selected trace. It is counterevidence to blanket failure, not recovery.
        selected = [r for r in self.records.values() if r["id"] in self.trace_sources]
        if any(r.get("http_status", 0) >= 400 or r.get("exception_type") for r in selected):
            success = next((r for r in self.records.values() if r.get("trace_id") and r["trace_id"] != self.selected_trace
                            and 200 <= r.get("http_status", 0) < 400), None)
            if success:
                priority.add(success["id"])
        evidence_budget = budget
        while evidence_budget >= 0:
            # The final table encoding can be smaller than the row encoding used
            # by tools. Fit against the complete request below; never discard a
            # required relationship merely because this intermediate form grew.
            evidence = compact(list(self.records.values()), evidence_budget, priority)
            # Advisory ranking orders already selected evidence; it cannot evict
            # measured extremes or successful/conflicting observations.
            rank = {ref: index for index, ref in enumerate(self.advisory_priority)}
            evidence["records"].sort(key=lambda row: rank.get(row["id"], len(rank)))
            context = {"recent_queries": {
                           "columns": ["tool", "arguments", "returned", "omitted_groups", "query_limit_reached"],
                           "rows": [[q.get(k) for k in ("tool", "arguments", "returned", "omitted_groups", "query_limit_reached")]
                                    for q in recent]},
                       "earlier_queries_summarized": len(self.queries) - len(recent),
                       "query_counts": {name: sum(q["tool"] == name for q in self.queries) for name in TOOL_NAMES},
                       "evidence": {**{k: v for k, v in evidence.items() if k != "records"},
                                    "tables": evidence_tables(evidence["records"]),
                                    "supplied_trace_count": len({row["trace_id"] for row in evidence["records"] if row.get("trace_id")})}, "topology": self.topology,
                       "measured_timing_comparisons": timing_comparisons(evidence["records"]),
                       "observed_http_exchanges": http_exchanges(evidence["records"])}
            identities = instance_relationships(evidence["records"])
            if identities:
                context["explicit_instance_relationships"] = identities
            if self.tools.collection:
                summary = json.loads(self.tools.collection["summary"])
                coverage = {k: v for k, v in summary.items() if k not in {"redaction_note", "input_bytes", "input_lines", "source", "datasets"}
                            and (v != 0 or k == "observations")}
                if "source" in summary:
                    coverage["source"] = {k: v for k, v in summary["source"].items() if k not in {"connection_id", "projects"}}
                if "datasets" in summary:
                    coverage["datasets"] = {name: {k: v for k, v in value.items() if k not in {"cursor", "pages"}}
                                            for name, value in summary["datasets"].items()}
                context["import"] = {"symptom_claim": self.tools.collection["symptom"],
                                     "coverage": coverage}
            if priority and not priority <= {row["id"] for row in evidence["records"]}:
                context["evidence"]["partial_trace"] = True
            if self.tools.selection:
                context["selection"] = {key: value for key, value in self.tools.selection.items() if key != "sources"}
            payload = {**base, "messages": [{"role": "system", "content": system},
                {"role": "user", "content": instruction + "\nOperational data: " + encoded(context).decode()}]}
            supplied = sorted({row["id"] for row in evidence["records"]})
            if "response_format" in payload and supplied:
                payload["response_format"] = deepcopy(payload["response_format"])
                schema = payload["response_format"]["json_schema"]["schema"]
                schema["properties"]["evidence_ids"]["items"]["enum"] = supplied
                schema["properties"]["hypotheses"]["items"]["properties"]["evidence_ids"]["items"]["enum"] = supplied
                if "supporting_evidence" in schema["properties"]:
                    schema["properties"]["supporting_evidence"]["items"]["properties"]["evidence_ids"]["items"]["enum"] = supplied
            excess = len(encoded(payload)) - budget
            if excess <= 0:
                if self.records and not supplied:
                    raise ValueError("No useful observation fits the complete Groq input bound. Import smaller observations.")
                # Advisory hints use spare bytes only: they never evict observations.
                if self.triage:
                    hint = deepcopy(self.triage)
                    hint["priorities"] = [group for group in hint["priorities"] if set(group["source_ids"]) <= set(supplied)]
                    compact_hint = {"note": "Advisory only, not evidence. Test alternatives and conflicting observations.",
                                    "category": hint.get("category"), "priorities": hint["priorities"][:2]}
                    focus_hint = {"note": "Advisory only; test alternatives.", "category": hint.get("category"),
                                  "priority_sources": [source for g in hint["priorities"][:1] for source in g["source_ids"]]}
                    for label, value in (("full", hint), ("compact", compact_hint), ("focus", focus_hint)):
                        augmented = deepcopy(payload)
                        augmented["messages"][1]["content"] += "\nAdvisory initial triage: " + encoded(value).decode()
                        if len(encoded(augmented)) <= budget:
                            payload = augmented
                            self.triage_included = label
                            break
                from .selection import provenance
                self.tools.last_excerpts = provenance(evidence["records"], self.tools.seen, self.tools.selection)
                return payload, set(supplied)
            if not evidence["records"]:
                raise ValueError("Groq input allowance is insufficient for the required request schema.")
            evidence_budget -= max(128, excess)
        raise ValueError("Groq input allowance is insufficient for the required request schema.")
