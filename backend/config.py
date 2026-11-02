import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env_file = ROOT / ".env"
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
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
SERVICES = {"gateway": PORT + 1, "order": PORT + 2, "payment": PORT + 3}

def service_url(name):
    return f"http://127.0.0.1:{SERVICES[name]}"

