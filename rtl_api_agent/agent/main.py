"""File contract: always emit solution.v and trace.jsonl, including failures."""
from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from .client import ModelError
from .config import Config
from .trace import Trace, atomic_write, digest
from .workflow import Workflow


class Interrupted(BaseException):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    atomic_write(output / "solution.v", "")
    trace = Trace(output / "trace.jsonl")
    trace.log(tool="run_meta", track="rtl", nonce=os.environ.get("RUN_NONCE", ""))
    workflow = None

    def interrupted(signum, frame):
        raise Interrupted()
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        config = Config.from_env()
        workflow = Workflow(config, output, trace)
        input_dir = Path(args.input)
        prompt = (input_dir / "prompt.txt").read_text(encoding="utf-8")
        interface = (input_dir / "interface.txt").read_text(encoding="utf-8") if (input_dir / "interface.txt").is_file() else ""
        workflow.solve(prompt, interface)
    except (Interrupted, KeyboardInterrupt):
        if workflow:
            workflow.reason = "terminated_checkpoint_preserved"
        trace.log(tool="agent", event="interrupted")
    except Exception as exc:
        if workflow:
            workflow.reason = "model_unavailable" if isinstance(exc, ModelError) else "agent_error"
        trace.log(tool="agent", event="error", error=str(exc)[:300] if isinstance(exc, (ModelError, ValueError)) else type(exc).__name__)
    finally:
        signal.signal(signal.SIGTERM, previous)
        if workflow:
            workflow.finish()
        else:
            trace.log(tool="workflow", event="finished", internal_level=0,
                      solution_sha256=digest(""), reason="configuration_error")
        trace.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
