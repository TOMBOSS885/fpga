"""Vendor-neutral chat client; no SDK, tool-calling or JSON-mode dependency."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .process import Deadline, run

ROOT = Path(__file__).resolve().parents[1]


class ModelError(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    finish: str | None = None


def request(config: Config, path: str, payload: dict | None, timeout: float) -> dict:
    if timeout < 0.05:
        raise ModelError("request_budget_exhausted")
    args = dict(url=config.base_url + path, payload=payload, api_key=config.api_key,
                ca_bundle=config.ca_bundle, timeout=timeout)
    with tempfile.TemporaryDirectory(prefix="rtl_http_") as work:
        rc, text = run([sys.executable, "-B", "-m", "agent.transport"], ROOT, timeout,
                       Path(work) / "response.json", json.dumps(args).encode())
    if rc != 0:
        raise ModelError("request_timeout" if rc == 124 else "transport_failed")
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ModelError("invalid_transport_response") from exc


class Client:
    def __init__(self, config: Config, deadline: Deadline, trace):
        self.config, self.deadline, self.trace = config, deadline, trace

    def chat(self, system: str, user: str, role: str, reserve: float = 0,
             max_tokens: int | None = None) -> Reply:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # Estimate only. Never truncate the specification or RTL to fit context.
        estimated_input = len((system + user).encode()) // 3 + 128
        tokens = min(max_tokens or self.config.max_tokens,
                     self.config.context - estimated_input - 256)
        if tokens < 256:
            raise ModelError("configured_context_too_small; increase LLM_CONTEXT_TOKENS")
        payload = dict(model=self.config.model, messages=messages, temperature=0.1,
                       top_p=1.0, max_tokens=tokens, stream=self.config.stream)
        call_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        for attempt in range(2):
            timeout = self.deadline.cap(self.config.request_s, reserve)
            started = time.monotonic()
            self.trace.log(tool="llm", event="request", role=role, request_sha256=call_id,
                           attempt=attempt + 1, model=self.config.model, max_tokens=tokens,
                           timeout_s=round(timeout, 3))
            try:
                response = request(self.config, "/chat/completions", payload, timeout)
                status = response.get("status", 0)
                if status != 200:
                    if status in {429, 502, 503, 504} and attempt == 0 and self.deadline.remaining > reserve + 2:
                        self.trace.log(tool="llm", event="retry", role=role, http_status=status)
                        continue
                    raise ModelError(f"model_http_status_{status}")
                body = response["body"]
                choice = body["choices"][0]
                content = choice["message"].get("content")
                if isinstance(content, list):
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
                if not isinstance(content, str) or not content.strip():
                    raise ModelError(f"empty_content; finish={choice.get('finish_reason')}")
                usage = body.get("usage") or {}
                self.trace.log(tool="llm", event="response", role=role,
                               finish=choice.get("finish_reason"), tokens_in=usage.get("prompt_tokens"),
                               tokens_out=usage.get("completion_tokens"),
                               response_sha256=hashlib.sha256(content.encode()).hexdigest(),
                               elapsed_s=round(time.monotonic() - started, 3))
                return Reply(content, choice.get("finish_reason"))
            except Exception as exc:
                self.trace.log(tool="llm", event="error", role=role,
                               error=str(exc)[:220] if isinstance(exc, ModelError) else type(exc).__name__,
                               elapsed_s=round(time.monotonic() - started, 3))
                if isinstance(exc, ModelError):
                    raise
                raise ModelError("malformed_model_response") from exc
        raise ModelError("model_unavailable")
