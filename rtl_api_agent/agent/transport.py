"""Disposable HTTP worker. Its parent enforces a total, not idle, timeout."""
from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request

LIMIT = 4 * 1024 * 1024


def exchange(req: dict) -> dict:
    payload = req.get("payload")
    data = json.dumps(payload).encode() if payload is not None else None
    context = ssl.create_default_context(cafile=req.get("ca_bundle") or None)
    headers = {"Content-Type": "application/json"}
    if req.get("api_key"):
        headers["Authorization"] = "Bearer " + req["api_key"]
    request = urllib.request.Request(req["url"], data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=req["timeout"], context=context) as response:
            if "text/event-stream" in response.headers.get("Content-Type", ""):
                chunks, finish, usage, received = [], None, {}, 0
                for line in response:
                    received += len(line)
                    if received > LIMIT:
                        return {"status": 0, "error": "response_too_large"}
                    if not line.startswith(b"data:"):
                        continue
                    datum = line[5:].strip()
                    if datum == b"[DONE]":
                        break
                    event = json.loads(datum)
                    if event.get("error"):
                        return {"status": 0, "error": "stream_error"}
                    usage = event.get("usage") or usage
                    for choice in event.get("choices", [])[:1]:
                        content = choice.get("delta", {}).get("content")
                        if isinstance(content, str):
                            chunks.append(content)
                        finish = choice.get("finish_reason") or finish
                body = {"choices": [{"message": {"content": "".join(chunks)}, "finish_reason": finish}], "usage": usage}
            else:
                raw = response.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    return {"status": 0, "error": "response_too_large"}
                body = json.loads(raw)
            return {"status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        # Never echo server error pages: proxies can include credentials there.
        return {"status": exc.code, "error": "http_error"}
    except Exception as exc:
        return {"status": 0, "error": type(exc).__name__}


if __name__ == "__main__":
    try:
        result = exchange(json.load(sys.stdin))
    except Exception as exc:
        result = {"status": 0, "error": type(exc).__name__}
    print(json.dumps(result))
