import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import threading
import json
from pathlib import Path
import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser(description="Run Escalation Desk and its three reference services.")
    parser.add_argument("--service", choices=["platform", "gateway", "order", "payment"])
    parser.add_argument("--stop", action="store_true", help="Stop the runtime using this data directory.")
    args = parser.parse_args()
    from .config import PORT, SERVICES, DATA, ENABLE_LAB
    stop_file = DATA / "stop.request"
    runtime_file = DATA / "runtime.json"
    if args.stop:
        if not runtime_file.exists():
            raise SystemExit("No runtime record found for this data directory.")
        stop_file.write_text("stop", encoding="utf-8")
        print("Shutdown requested.")
        return
    if args.service:
        if os.getenv("LAB_LEASE"):
            def watch_lease():
                lease = Path(os.environ["LAB_LEASE"])
                while True:
                    time.sleep(2)
                    try:
                        alive = time.time() - lease.stat().st_mtime < 15
                    except OSError:
                        alive = False
                    if not alive:
                        os._exit(75)
            threading.Thread(target=watch_lease, daemon=True).start()
        if args.service == "platform":
            from .api import create_app
            app, port = create_app(), PORT
        else:
            from .services import create_service
            app, port = create_service(args.service), SERVICES[args.service]
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        return
    for port in [PORT, *(SERVICES.values() if ENABLE_LAB else [])]:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is occupied. Stop the previous runtime or change DESK_PORT.")
    environment = os.environ.copy()
    environment["DESK_INTERNAL_KEY"] = secrets.token_urlsafe(32)
    environment["PYTHONUNBUFFERED"] = "1"
    DATA.mkdir(parents=True, exist_ok=True)
    stop_file.unlink(missing_ok=True)
    children = []
    logs = []
    stopping = False

    def stop_signal(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)

    def read_commands():
        for line in sys.stdin:
            if line.strip().lower() == "stop":
                stop_signal()
                return

    threading.Thread(target=read_commands, daemon=True).start()
    try:
        for name in (["payment", "order", "gateway"] if ENABLE_LAB else []) + ["platform"]:
            log = (DATA / f"{name}.log").open("a", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen([sys.executable, "-m", "backend.launcher", "--service", name],
                cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            children.append(process)
            port = PORT if name == "platform" else SERVICES[name]
            path = "/api/health" if name == "platform" else "/health"
            # First startup also installs the saved demo over the remote database.
            deadline = time.monotonic() + (120 if name == "platform" else 40)
            last_readiness = "not contacted"
            with httpx.Client(trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} could not start. See {DATA / (name + '.log')}")
                    try:
                        response = client.get(f"http://127.0.0.1:{port}{path}", timeout=5 if name == "platform" else 1)
                        last_readiness = f"HTTP {response.status_code}"
                        if response.status_code == 200:
                            break
                    except httpx.RequestError as exc:
                        last_readiness = type(exc).__name__
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"{name} did not become ready on port {port}: {last_readiness}.")
        print(f"TraceDesk is ready: http://127.0.0.1:{PORT}", flush=True)
        print("Workspace is running. Press Ctrl+C to stop.", flush=True)
        runtime_file.write_text(json.dumps({"pid": os.getpid(), "port": PORT}), encoding="utf-8")
        while not stopping:
            if stop_file.exists():
                stopping = True
                break
            if any(process.poll() is not None for process in children):
                raise RuntimeError("A service stopped unexpectedly. Check the logs in the data directory.")
            time.sleep(0.4)
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for log in logs:
            log.close()
        runtime_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)

if __name__ == "__main__":
    main()

