"""Standard-library implementation of the official /v1/health and /v1/solve API."""
from __future__ import annotations

import hmac
import json
import math
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.process import run
from .health import Probe

ROOT = Path(__file__).resolve().parents[1]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address=("127.0.0.1", 7860)):
        if address[0] != "127.0.0.1":
            raise ValueError("Official endpoint must bind 127.0.0.1; expose with rc-tunnel")
        self.token = os.environ.get("FPGACHINA_TOKEN", "")
        if not self.token:
            raise ValueError("Set FPGACHINA_TOKEN before starting the service")
        self.solve_lock, self.probe = threading.Lock(), Probe()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # No request bodies, API keys or bearer headers in console logs.

    def send_json(self, status: int, value: dict):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + self.server.token).encode()):
            self.send_json(401, {"detail": "Invalid bearer token"})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path != "/v1/health":
            self.send_json(404, {"detail": "Unknown endpoint"})
            return
        self.send_json(200, self.server.probe.check())

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != "/v1/solve":
            self.send_json(404, {"detail": "Unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2 * 1024 * 1024:
                raise ValueError("Invalid or oversized body")
            req = json.loads(self.rfile.read(length))
            if not isinstance(req, dict):
                raise ValueError("Expected a JSON object")
            for key in ("task_id", "prompt"):
                if not isinstance(req.get(key), str) or not req[key].strip():
                    raise ValueError(f"Nonempty {key} required")
            if req.get("mode", "agent") not in {"agent", "baseline"}:
                raise ValueError("mode must be agent or baseline")
            if not isinstance(req.get("interface", ""), str) or not isinstance(req.get("nonce", ""), str):
                raise ValueError("interface and nonce must be strings")
            seconds = req.get("deadline_s", 360)
            if type(seconds) not in {int, float} or not math.isfinite(seconds) or not 1 <= seconds <= 3600:
                raise ValueError("deadline_s must be between 1 and 3600")
        except (ValueError, TypeError, OSError) as exc:
            self.send_json(400, {"detail": str(exc)[:160]})
            return
        if not self.server.solve_lock.acquire(blocking=False):
            self.send_json(503, {"detail": "One evaluation solve is already active"})
            return
        try:
            self.send_json(200, solve_request(req))
        finally:
            self.server.solve_lock.release()


def solve_request(req: dict) -> dict:
    started = time.monotonic()
    seconds = float(req.get("deadline_s", 360))
    scratch = os.environ.get("AGENT_TMP") or os.environ.get("EDA_TMP")
    if scratch:
        Path(scratch).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rtl_solve_", dir=scratch) as directory:
        work = Path(directory)
        input_dir, output = work / "input", work / "output"
        input_dir.mkdir(); output.mkdir()
        for key, name in [("prompt", "prompt.txt"), ("interface", "interface.txt")]:
            (input_dir / name).write_text(req.get(key, ""), encoding="utf-8")
        env = dict(os.environ, AGENT_DEADLINE_S=str(max(0.5, seconds - min(2, seconds * 0.1))),
                   RUN_NONCE=req.get("nonce", ""), PYTHONDONTWRITEBYTECODE="1")
        script = "run_baseline.sh" if req.get("mode") == "baseline" else "run.sh"
        rc, log = run(["bash", str(ROOT / script), str(input_dir), str(output)], ROOT,
                      max(0.1, seconds - min(1, seconds * 0.05)), work / "runner.log", env=env)
        def read(name):
            try:
                return (output / name).read_text(encoding="utf-8")
            except OSError:
                return ""
        solution, trace = read("solution.v"), read("trace.jsonl")
        if rc != 0 or not trace:
            trace += json.dumps(dict(tool="submission_runner", rc=rc, event="process_interrupted" if rc == 124 else "process_error")) + "\n"
        return dict(task_id=req["task_id"], solution=solution, trace=trace,
                    elapsed_s=round(time.monotonic() - started, 3))


if __name__ == "__main__":
    server = Server(("127.0.0.1", int(os.environ.get("PORT", "7860"))))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
