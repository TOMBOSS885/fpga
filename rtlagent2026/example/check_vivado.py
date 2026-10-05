"""Compile and synthesize existing RTL, without calling a model or changing it."""

import argparse
import json
import os
from pathlib import Path
import tempfile

from agent.tools import PART, RtlToolchain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("solution", type=Path)
    parser.add_argument("--top", default="TopModule")
    parser.add_argument("--timeout", type=float, default=300,
                        help="Seconds allowed for each check (default: 300)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not args.solution.is_file():
        parser.error("solution file does not exist")
    source = args.solution.read_text(encoding="utf-8-sig")
    os.environ["AGENT_KEEP_WORK"] = "1"
    rtl = RtlToolchain()
    result = {"solution": str(args.solution.resolve()), "backend": rtl.backend, "part": PART,
              "version": rtl.version, "available": rtl.available,
              "reason": rtl.reason, "functional_simulation": "not performed", "stages": {}}
    print(json.dumps({k: v for k, v in result.items() if k != "stages"}, ensure_ascii=False), flush=True)
    for name in ("lint", "synth"):
        if not rtl.available:
            break
        rc, log = getattr(rtl, name)(source, args.top, args.timeout)
        result["stages"][name] = {"rc": rc, "log": log, "workdir": rtl.last_work}
        print(f"{name}: rc={rc}\n{log}\nWork directory: {rtl.last_work}", flush=True)
        if rc != 0:
            break
    report_root = Path(__file__).resolve().parent.parent / ".vivado-work"
    report_root.mkdir(exist_ok=True)
    report = Path(tempfile.mkdtemp(prefix="check_", dir=report_root)) / "report.json"
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {report}")
    return 0 if result["stages"].get("synth", {}).get("rc") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
