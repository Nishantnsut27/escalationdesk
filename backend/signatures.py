"""Versioned, explicit failure signatures. A tag identifies a symptom, not a cause."""
import json
import re

from .signatures_v1 import CATALOG as V1_CATALOG

VERSION = "signatures-v2"
# Shared Python/PostgreSQL regex syntax; explicit symptoms only.
CATALOG = V1_CATALOG + (
    ("image_pull_failure", "deployment_lifecycle", "Image-pull failure", r"ImagePullBackOff|ErrImagePull|failed to pull image|failed to (resolve|fetch)[^\n]*image"),
    ("failed_scheduling", "deployment_lifecycle", "Failed scheduling", r"FailedScheduling|failed to schedule|[0-9]+/[0-9]+ nodes are available:|[0-9]+/[0-9]+ nodes are unavailable:"),
    ("container_configuration", "configuration", "Container configuration failure", r"CreateContainerConfigError|CreateContainerError|RunContainerError|failed to (create|start)[^\n]*container"),
    ("missing_configuration", "configuration", "Missing configuration", r"missing (required )?(configuration|config|environment variable)|configmap[^\n]*(not found|does not exist)|secret[^\n]*(not found|does not exist)|couldn.t find key[^\n]*(ConfigMap|Secret)"),
    ("volume_mount_failure", "storage_filesystem", "Volume-mount failure", r"FailedMount|FailedAttachVolume|MountVolume[^\n]*(failed|error)|Unable to attach or mount volumes|failed to mount[^\n]*volume"),
    ("disk_exhaustion", "storage_filesystem", "Disk exhaustion", r"ENOSPC|no space left on device|disk quota exceeded|DiskPressure|disk[^\n]*(full|exhausted)|ephemeral-storage[^\n]*insufficient"),
    ("address_conflict", "network_connectivity", "Port / address conflict", r"EADDRINUSE|address already in use|port[^\n]*already (allocated|in use)"),
    ("database_connection_limit", "resource_exhaustion", "Database connection limit", r"too many connections|too many clients already|remaining connection slots are reserved|SQLSTATE[^\n]*53300|too_many_connections"),
)
COMPILED = [(key, family, re.compile(pattern, re.I)) for key, family, _, pattern in CATALOG]
LABELS = {key: label for key, _, label, _ in CATALOG} | {"unknown": "Unclassified", "mixed": "Multiple error families"}


def signature_text(row):
    parts = [row.get(key) for key in ("message", "exception_type", "error_type", "error", "stack_trace", "exception")]
    return "\n".join(value if isinstance(value, str) else json.dumps(value) for value in parts if value) or str(row.get("original", ""))


def detect(row):
    text = signature_text(row)
    matches = [(key, family) for key, family, pattern in COMPILED if pattern.search(text)]
    if row.get("exception_handled") is False and not any(key == "unhandled_exception" for key, _ in matches):
        matches.append(("unhandled_exception", "application_exception"))
    tags = [key for key, _ in matches]
    if len({family for _, family in matches}) > 1:
        tags.append("mixed")
    return tags or ["unknown"]


def catalogue():
    return [{"id": key, "family": family, "label": label, "version": VERSION} for key, family, label, _ in CATALOG] + [
        {"id": key, "family": key, "label": LABELS[key], "version": VERSION} for key in ("mixed", "unknown")]


def migration_sql():
    """Generate additive versioned catalogue rows; migration 3 uses the frozen v1 module."""
    def quoted(value):
        return "'" + value.replace("'", "''") + "'"
    values = ",\n".join("(" + ",".join(quoted(value) for value in (VERSION, key, family, label, pattern)) + ")"
                         for key, family, label, pattern in CATALOG)
    return "INSERT INTO error_signatures(version,id,family,label,pattern) VALUES\n" + values + ";\n"
