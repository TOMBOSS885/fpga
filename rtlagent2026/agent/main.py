"""Top-level CLI entry point for RTL Agent V1."""

from __future__ import annotations

import argparse
import os
import sys

from .orchestrator import MultiAgentOrchestrator, TraceLogger
from .coder_agent import CoderAgent
from .llm import LLM
from .spec_agent import HardwareSpec, SpecAgent
from .tools import parse_interface_contract

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_DIR = os.path.join(ROOT, "skill")


def read_task(input_dir: str) -> tuple[str, str]:
    def _read(fname: str) -> str:
        p = os.path.join(input_dir, fname)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return fh.read().strip()
        return ""
    return _read("prompt.txt"), _read("interface.txt")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AMD FPGA 2026 - RTL Multi-Agent V1")
    ap.add_argument("--input", required=True, help="Directory containing prompt.txt and optional interface.txt")
    ap.add_argument("--output", required=True, help="Directory to emit solution.v and trace.jsonl")
    ap.add_argument("--top", default="", help="Optional top module override")
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    sol_path = os.path.join(args.output, "solution.v")
    trace_path = os.path.join(args.output, "trace.jsonl")

    trace = TraceLogger(trace_path)
    code = ""

    try:
        prompt, interface = read_task(args.input)
        orchestrator = MultiAgentOrchestrator(SKILL_DIR)
        code = orchestrator.solve(prompt, interface, trace, top_override=args.top)
    except Exception as exc:
        trace.log(tool="agent", event="exception", error=f"{type(exc).__name__}: {exc}"[:400])
        print(f"RTL Agent Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        # Keep the submission contract useful after an unexpected orchestration
        # exception.  Both subagents contain deterministic fallbacks.
        try:
            prompt, interface = read_task(args.input)
            # Build the fallback spec without contacting the model.  This path
            # is intentionally available only for the offline mock backend.
            # A real submission must not synthesize a task-shaped answer after
            # a model failure, because that would bypass the model call.
            fallback_llm = LLM()
            if fallback_llm.backend != "mock":
                trace.log(
                    tool="agent",
                    event="fallback_suppressed",
                    reason="deterministic_fallback_disabled_for_real_backend",
                    backend=fallback_llm.backend,
                )
                code = ""
            else:
                top, raw_ports, _ = parse_interface_contract(interface, prompt)
                ports = [{"name": n, "direction": d, "width": w} for d, w, n in raw_ports]
                names = {p["name"].lower() for p in ports}
                sequential = any(n in names for n in ("clk", "clock"))
                fallback_spec = HardwareSpec({
                    "module_name": args.top.strip() or top or "TopModule",
                    "ports": ports,
                    "is_sequential": sequential,
                    "clock_port": next((p["name"] for p in ports if p["name"].lower() in ("clk", "clock")), ""),
                    "reset_port": next((p["name"] for p in ports if p["name"].lower() in ("reset", "rst", "reset_n", "rst_n")), ""),
                    "reset_polarity": "active_low" if any(p["name"].lower() in ("rst_n", "reset_n") for p in ports) else "active_high",
                    "reset_sync": "sync",
                })
                code = CoderAgent(fallback_llm)._fallback_code(fallback_spec, prompt)
                trace.log(tool="agent", event="fallback_solution", bytes=len(code))
        except Exception as fallback_exc:
            trace.log(tool="agent", event="fallback_exception", error=f"{type(fallback_exc).__name__}: {fallback_exc}"[:400])
    finally:
        with open(sol_path, "w", encoding="utf-8") as fh:
            fh.write(code or "")
        trace.log(tool="agent", event="done", bytes=len(code or ""))
        trace.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
