"""One model configuration shared by agent, health probe and baseline relay."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def number(name: str, default: float, minimum: float = 0.0) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return value


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip().rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("LLM_BASE_URL must be an http(s) endpoint")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Put credentials in LLM_API_KEY or LLM_API_KEY_FILE, not the URL")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    if not path:
        path = "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass(frozen=True)
class Config:
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    ca_bundle: str = ""
    context: int = 16384
    max_tokens: int = 4096
    request_s: float = 85.0
    deadline_s: float = 360.0
    reserve_s: float = 8.0
    max_repairs: int = 3
    tb_repairs: int = 1
    lint_s: float = 40.0
    sim_s: float = 45.0
    synth_s: float = 90.0
    part: str = "xczu3eg-sbva484-1-e"
    period_ns: float = 5.0
    stream: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        model = os.environ.get("LLM_MODEL") or os.environ.get("MODEL_NAME", "")
        if not model.strip():
            raise ValueError("Set LLM_MODEL to the exact served model id")
        if os.environ.get("MODEL_NAME") and os.environ["MODEL_NAME"] != model:
            raise ValueError("MODEL_NAME and LLM_MODEL must identify the same model")
        key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        if os.environ.get("LLM_API_KEY_FILE"):
            key = Path(os.environ["LLM_API_KEY_FILE"]).read_text().strip()
        return cls(
            base_url=canonical_url(os.environ.get("LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1"))),
            model=model.strip(), api_key=key, ca_bundle=os.environ.get("LLM_CA_BUNDLE", ""),
            context=int(number("LLM_CONTEXT_TOKENS", 16384, 2048)),
            max_tokens=int(number("LLM_MAX_TOKENS", 4096, 256)),
            request_s=number("LLM_TIMEOUT_S", 85, 0.1),
            deadline_s=number("AGENT_DEADLINE_S", 360, 1),
            reserve_s=number("AGENT_RESERVE_S", 8),
            max_repairs=int(number("AGENT_MAX_REPAIRS", 3)),
            tb_repairs=int(number("AGENT_TB_REPAIRS", 1)),
            lint_s=number("AGENT_LINT_S", 40, 0.1),
            sim_s=number("AGENT_SIM_S", 45, 0.1),
            synth_s=number("AGENT_SYNTH_S", 90, 0.1),
            part=os.environ.get("RTL_PART", "xczu3eg-sbva484-1-e"),
            period_ns=number("RTL_PERIOD_NS", 5, 0.1),
            stream=os.environ.get("LLM_STREAM", "0") == "1",
        )
