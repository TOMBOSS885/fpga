"""LLM client for multi-agent RTL workflow.

Supports:
- LLM_BACKEND=mock: for local dry-run, unit tests, and verifying contracts.
- LLM_BACKEND=openai: for local vLLM (submission requirement) or commercial API (development).
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import time
from typing import Callable


class LLMError(RuntimeError):
    pass


class LLMResult:
    def __init__(self, text: str, tokens_in: int = 0, tokens_out: int = 0, elapsed_s: float = 0.0):
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.elapsed_s = elapsed_s


class LLM:
    def __init__(self) -> None:
        self.backend = os.environ.get("LLM_BACKEND", "mock").strip().lower()
        self.model = os.environ.get("LLM_MODEL", "mock-model")
        self.base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "4096"))
        self.temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
        # A stalled local server must leave time for Vivado feedback and a
        # final artifact.  Teams can raise this explicitly for slower cards.
        self.timeout_s = float(os.environ.get("LLM_TIMEOUT_S", "90"))
        self.deadline_at: float | None = None
        self.trace_hook: Callable[..., None] | None = None

        if self.backend == "openai" and not self.base_url:
            raise LLMError("LLM_BACKEND=openai requires LLM_BASE_URL")

    def describe(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "base_url": self.base_url or None,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "deadline_bound": self.deadline_at is not None,
        }

    def set_deadline(self, deadline_at: float | None) -> None:
        """Bind requests to the solve-level wall-clock deadline."""
        self.deadline_at = deadline_at

    def set_trace_hook(self, hook: Callable[..., None] | None) -> None:
        """Attach an optional structured observer without coupling to tracing."""
        self.trace_hook = hook

    def remaining_s(self) -> float | None:
        if self.deadline_at is None:
            return None
        return max(0.0, self.deadline_at - time.time())

    def chat(self, messages: list[dict], temperature: float | None = None, max_tokens: int | None = None) -> LLMResult:
        temp = self.temperature if temperature is None else temperature
        tokens = self.max_tokens if max_tokens is None else max_tokens

        t0 = time.time()
        remaining = self.remaining_s()
        if remaining is not None and remaining <= 0.05:
            exc = LLMError("solve deadline exhausted before LLM request")
            self._emit_trace(
                event="error", elapsed_s=0.0, tokens_in=0, tokens_out=0,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise exc
        try:
            if self.backend == "mock":
                res = self._chat_mock(messages)
            elif self.backend == "openai":
                request_timeout = self.timeout_s if remaining is None else min(self.timeout_s, max(0.1, remaining))
                res = self._chat_openai(messages, temp, tokens, timeout_s=request_timeout)
            else:
                raise LLMError(f"unknown LLM_BACKEND: {self.backend!r}")
        except Exception as exc:
            self._emit_trace(
                event="error", elapsed_s=round(time.time() - t0, 3),
                error=f"{type(exc).__name__}: {exc}"[:300],
                tokens_in=0, tokens_out=0,
            )
            raise
        res.elapsed_s = round(time.time() - t0, 3)
        self._emit_trace(
            event="response", elapsed_s=res.elapsed_s,
            tokens_in=res.tokens_in, tokens_out=res.tokens_out,
        )
        return res

    def _emit_trace(self, **fields: object) -> None:
        if self.trace_hook is None:
            return
        try:
            self.trace_hook(tool="llm", backend=self.backend, model=self.model, **fields)
        except Exception:
            # Tracing must never make a model response fail.
            pass

    def _chat_openai(self, messages: list[dict], temp: float, max_tok: int,
                     timeout_s: float | None = None) -> LLMResult:
        import requests

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tok,
            "temperature": temp,
            "stream": False,
        }

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=self.timeout_s if timeout_s is None else timeout_s,
        )
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape: {body}") from exc

        # Some OpenAI-compatible local servers return content parts instead of
        # one string when reasoning or structured output is enabled.
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        else:
            text = str(content)

        usage = body.get("usage") or {}
        return LLMResult(
            text=text,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )

    def _chat_mock(self, messages: list[dict]) -> LLMResult:
        """Deterministic mock response for offline validation."""
        full_prompt = "\n".join(m.get("content", "") for m in messages)
        task_text = messages[-1].get("content", "") if messages else full_prompt
        # Spec prompts carry the raw task after this marker; using only that
        # portion avoids treating words from the system instructions as ports.
        if "## Problem Description:" in task_text:
            task_text = task_text.split("## Problem Description:", 1)[1]
            if "## Interface:" in task_text:
                task_text = task_text.split("## Interface:", 1)[0]

        def mock_top(text: str) -> str:
            # Prefer the task's explicit name.  Do not capture prose such as
            # "module for AMD Vivado" as a Verilog module called `for`.
            candidates = re.findall(r"\bmodule\s+(?:named\s+)?([A-Za-z_]\w*)\s*\(", text, re.IGNORECASE)
            if not candidates:
                candidates = re.findall(r"\bmodule\s+(?:named\s+)?([A-Za-z_]\w*)", text, re.IGNORECASE)
            stop = {"for", "with", "the", "named", "implement", "a", "an", "that",
                    "header", "declaration", "name", "called", "output"}
            for name in candidates:
                if name.lower() not in stop:
                    return name
            return "TopModule"

        def mock_ports(text: str) -> list[tuple[str, str, int]]:
            ports = []
            seen = set()
            for line in text.splitlines():
                m = re.search(
                    r"(?:^|[-*]\s*)\b(input|output|inout)\s+(?:wire\s+|reg\s+|logic\s+)?"
                    r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+)?([A-Za-z_]\w*)"
                    r"(?:\s*\(\s*(\d+)\s*(?:bits?|位)\s*\))?",
                    line, re.IGNORECASE,
                )
                if not m:
                    continue
                direction = m.group(1).lower()
                msb, lsb, name, bits = m.groups()[1:]
                width = abs(int(msb) - int(lsb)) + 1 if msb and lsb else int(bits or 1)
                if name not in seen:
                    seen.add(name)
                    ports.append((direction, name, width))
            return ports

        top_name = mock_top(task_text)
        ports = mock_ports(task_text)

        # Mock: detect if this is a Verifier / Testbench request
        if "tb_self_check" in full_prompt or "Testbench" in full_prompt or "tester" in full_prompt.lower():
            tb_code = textwrap.dedent("""\
                `timescale 1ns/1ps
                module tb_self_check();
                  reg clk = 0;
                  always #2.5 clk = ~clk;
                  initial begin
                    #20;
                    $display("TB_SUCCESS: All self-tests passed with 0 errors.");
                    $finish;
                  end
                endmodule
            """)
            return LLMResult(text=f"```verilog\n{tb_code}```", tokens_in=100, tokens_out=50)

        # Mock: detect if this is an Architect Spec request
        if "JSON" in full_prompt and "spec" in full_prompt.lower():
            ports_json = [{"name": name, "direction": direction, "width": width}
                          for direction, name, width in ports]

            mock_spec = {
                "module_name": top_name,
                "ports": ports_json or [{"name": "clk", "direction": "input", "width": 1}],
                "is_sequential": any("clk" in name.lower() or name.lower() == "clock" for _, name, _ in ports),
                "clock_port": next((name for _, name, _ in ports if name.lower() in ("clk", "clock")), ""),
                "reset_port": next((name for _, name, _ in ports if name.lower() in ("reset", "rst", "reset_n", "rst_n")), ""),
                "reset_polarity": "active_low" if any(name.lower() in ("rst_n", "reset_n") for _, name, _ in ports) else "active_high",
                "reset_sync": "async" if "asynchronous" in full_prompt.lower() else "sync",
                "summary": "Mock specification"
            }
            return LLMResult(text=f"```json\n{json.dumps(mock_spec, indent=2)}\n```", tokens_in=80, tokens_out=40)

        # Mock: generate standard RTL module stub
        # Generate useful deterministic RTL for the bundled smoke tasks.  This
        # keeps offline contract tests meaningful while the real submission
        # still uses the local open-weight model.
        port_lines = []
        for direction, name, width in ports:
            w_str = f"[{width-1}:0] " if width > 1 else ""
            typ = "reg " if direction == "output" and any(n.lower() in ("clk", "clock") for _, n, _ in ports) else ""
            port_lines.append(f"  {direction} {typ}{w_str}{name}")
        header = ",\n".join(port_lines) or "  input clk,\n  output reg [7:0] q"
        lower = task_text.lower()
        if "population count" in lower or "popcount" in lower:
            body = "  integer i;\n  always @(*) begin\n    out = 4'd0;\n    for (i = 0; i < 8; i = i + 1) out = out + in[i];\n  end"
        elif "1101" in lower and "detected" in lower:
            body = "  reg [3:0] sr;\n  always @(posedge clk) begin\n    if (reset) begin sr <= 4'b0; detected <= 1'b0; end\n    else begin sr <= {sr[2:0], in}; detected <= ({sr[2:0], in} == 4'b1101); end\n  end"
        elif "linear feedback shift" in lower or "lfsr" in lower:
            body = "  wire feedback = q[7] ^ q[5] ^ q[4] ^ q[3];\n  always @(posedge clk) begin\n    if (reset) q <= 8'h01; else if (load) q <= data; else q <= {q[6:0], feedback};\n  end"
        else:
            body = "  // deterministic mock placeholder"
        stub = f"module {top_name} (\n{header}\n);\n{body}\nendmodule\n"
        return LLMResult(text=f"```verilog\n{stub}\n```", tokens_in=120, tokens_out=60)


def extract_code(text: str, tag: str = "verilog", module_name: str | None = None) -> str:
    """Extract one requested RTL artifact from a model response.

    ``module_name`` prevents a response containing both a DUT and a sample
    testbench from selecting the wrong fenced block.  The old API remains
    compatible for callers that do not know the expected top name.
    """
    text = text or ""
    candidates = [m.group(1) for m in re.finditer(r"```(?:verilog|systemverilog|sv)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)]
    candidates.append(text)
    for candidate in candidates:
        if module_name:
            m = re.search(
                rf"\bmodule\s+{re.escape(module_name)}\b.*?\bendmodule\b",
                candidate, re.IGNORECASE | re.DOTALL,
            )
            if m:
                return m.group(0).strip() + "\n"
        else:
            m = re.search(r"\bmodule\s+\w+\b.*?\bendmodule\b", candidate, re.DOTALL)
            if m:
                return m.group(0).strip() + "\n"
    return ""


def extract_json(text: str) -> dict:
    """Extract JSON object from response."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    raw = m.group(1) if m else text
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Best effort slice { ... }
        s = raw.find("{")
        e = raw.rfind("}")
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(raw[s:e+1])
            except Exception:
                pass
        return {}
