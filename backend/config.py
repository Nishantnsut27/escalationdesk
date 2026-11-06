import os
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Process environment wins, followed by the root file, then backend-only secrets.
for env_file in (ROOT / ".env", ROOT / "backend" / ".env"):
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

DATA = Path(os.getenv("DESK_DATA_DIR", str(ROOT / "data"))).resolve()
PORT = int(os.getenv("DESK_PORT", "8000"))
INTERNAL_KEY = os.getenv("DESK_INTERNAL_KEY", "")
ORIGINS = {f"http://127.0.0.1:{PORT}", "http://127.0.0.1:5173",
           f"http://localhost:{PORT}", "http://localhost:5173"}
PUBLIC_URL = os.getenv("DESK_PUBLIC_URL", "").rstrip("/")
if PUBLIC_URL:
    public = urlsplit(PUBLIC_URL)
    if public.scheme != "https" or not public.hostname or public.username or public.password or public.path or public.query or public.fragment:
        raise ValueError("DESK_PUBLIC_URL must be an HTTPS origin, without a path or credentials.")
    ORIGINS.add(PUBLIC_URL)
SENTRY_CLIENT_ID = os.getenv("SENTRY_CLIENT_ID", "").strip()
SENTRY_CLIENT_SECRET = os.getenv("SENTRY_CLIENT_SECRET", "").strip()
SENTRY_APP_SLUG = os.getenv("SENTRY_APP_SLUG", "").strip()
CONNECTION_KEY = os.getenv("DESK_CONNECTION_KEY", "").strip()
ENABLE_LAB = os.getenv("DESK_ENABLE_LAB") == "1"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_PROXY_URL = os.getenv("GROQ_PROXY_URL", "").strip()
JEV_KEY = os.getenv("OPENROUTER_API_KEY", "")
JEV_MODEL = os.getenv("JEV_MODEL", "typesafe/jev-1.14")
JEV_PROXY_URL = os.getenv("JEV_PROXY_URL", "").strip()
SERVICES = {"gateway": PORT + 1, "order": PORT + 2, "payment": PORT + 3}

def service_url(name):
    return f"http://127.0.0.1:{SERVICES[name]}"

