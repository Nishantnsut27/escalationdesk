"""One resumable provider workload, with graceful pause between attempts."""
import json
from contextlib import contextmanager
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / ".cache" / "phase8" / "v1"


def request_pause(directory=DIRECTORY):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pause.request").write_text("pause", encoding="utf-8")
    print("Pause requested. The current attempt will finish and be saved; no next attempt will start.", flush=True)


@contextmanager
def workload(directory=DIRECTORY):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "analysis.lock"
    try:
        handle = lock.open("x")
    except FileExistsError:
        raise ValueError("Another provider workload is active. Pause it and wait for its saved result before resuming.") from None
    try:
        (directory / "pause.request").unlink(missing_ok=True)
        yield lambda: (directory / "pause.request").exists()
    finally:
        handle.close()
        lock.unlink(missing_ok=True)


def paused(check):
    if not check():
        return False
    print(json.dumps({"state": "paused", "message": "Progress retained. Rerun the command to resume; demo analysis also requires --resume."}), flush=True)
    return True
