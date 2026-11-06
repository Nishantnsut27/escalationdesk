"""Public scenario descriptions; never supplied as investigator evidence."""
CASES = [
    ("pool", "Payment pool exhaustion", "Requests cannot acquire an application database connection.", "Resources"),
    ("slow", "Slow Payment processing", "A caller times out while downstream processing continues.", "Latency"),
    ("exception", "Unhandled application exception", "A request fails with a captured application stack trace.", "Application"),
    ("schema", "Release missing a database migration", "New application code queries a column absent from the installed schema.", "Deployment"),
    ("unavailable", "Unavailable dependency", "A reachable caller cannot connect to its dependency.", "Network"),
    ("authentication", "Rejected upstream authentication", "A dependency rejects a request with an invalid test credential.", "Security"),
    ("incomplete", "Intermittent failures · incomplete evidence", "Successful and failed requests with limited downstream visibility.", "Uncertainty"),
    ("rollout", "Failed rolling deployment", "Traffic reaches a new service version before it is ready.", "Deployment"),
    ("startup", "Startup configuration failure", "A service exits during startup and callers lose access.", "Deployment"),
    ("tls", "Expired TLS certificate", "Certificate verification prevents a dependency connection.", "Security"),
    ("throttling", "API throttling and retries", "A rate-limited dependency rejects repeated attempts.", "Capacity"),
    ("backlog", "Background-job backlog", "Jobs accumulate while a worker stops consuming them.", "Workers"),
]
CATALOG = [dict(id=key, title=title, description=description, category=category) for key, title, description, category in CASES]
CASE_IDS = {item["id"] for item in CATALOG}
ACTIVE = {"starting", "running", "resetting"}
