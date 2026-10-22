"""Run the byte-identical official baseline through an authentication-only relay.

No prompt edits, payload rewriting, sampling changes, tools, or retries.
"""
from __future__ import annotations

import http.client
import os
import signal
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from agent.config import Config
from agent.process import Deadline, run

ROOT = Path(__file__).resolve().parents[1]


class Relay(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config):
        self.config = config
        self.deadline = Deadline(config.deadline_s, min(3, config.deadline_s * 0.1))
        super().__init__(("127.0.0.1", 0), RelayHandler)


class RelayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        allowed = (self.command, self.path) in {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}
        if not allowed:
            self.send_error(404)
            return
        config = self.server.config
        target = urlsplit(config.base_url)
        connection = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 2 * 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(length) if self.command == "POST" else None
            # Preserve the official baseline's 300s generation timeout; only
            # the common solve deadline may shorten it, not agent role limits.
            remaining = self.server.deadline.cap(30 if self.command == "GET" else 300)
            if remaining < 0.05:
                self.send_error(504)
                return
            if target.scheme == "https":
                context = ssl.create_default_context(cafile=config.ca_bundle or None)
                connection = http.client.HTTPSConnection(target.hostname, target.port, timeout=remaining, context=context)
            else:
                connection = http.client.HTTPConnection(target.hostname, target.port, timeout=remaining)
            headers = {"Content-Type": "application/json"}
            if config.api_key:
                headers["Authorization"] = "Bearer " + config.api_key
            # Preserve request bytes and upstream response bytes exactly.
            suffix = self.path[len("/v1"):]
            connection.request(self.command, target.path.rstrip("/") + suffix, body, headers)
            response = connection.getresponse()
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                self.send_error(502)
                return
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            try:
                self.send_error(502, "Inference upstream unavailable")
            except OSError:
                pass
        finally:
            if connection:
                connection.close()


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: run_baseline.sh <input_dir> <output_dir>")
    config = Config.from_env()
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ["solution.v", "trace.jsonl"]:
        (output / name).write_text("")
    server = Relay(config)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env = dict(os.environ, MODEL_NAME=config.model, TRACK="rtl",
               LLM_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1")
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        run([sys.executable, str(ROOT / "baseline.py"), str(Path(sys.argv[1]).resolve()), str(output), "rtl"],
            ROOT, server.deadline.remaining, output / "baseline_console.log", env=env)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
