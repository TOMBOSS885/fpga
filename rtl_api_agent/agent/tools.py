"""Vivado-only verification; local milestones are never official scores."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .artifacts import Contract
from .config import Config
from .process import Deadline, run
from .trace import atomic_write, digest
from .vivado import discover_vivado_bin, runtime_env


@dataclass
class Result:
    kind: str
    log: str = ""
    category: str = ""
    code: str = ""
    phase: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


def license_error(log: str) -> bool:
    return bool(re.search(r"license.{0,45}(?:checkout failed|not found|unavailable|not available)|"
                          r"(?:failed|unable|could not).{0,40}(?:check.?out|obtain).{0,25}license|"
                          r"no valid license|FLEXnet Licensing error", log, re.I))


def diagnostic(log: str, limit: int = 5000) -> str:
    lines = log.splitlines()
    selected = [line for line in lines if re.search(r"error|fatal|mismatch|warning|timeout|TB_|VRFC|Synth|DRC|license", line, re.I)]
    # Keep the first occurrence of a diagnostic and a small tail for context;
    # Vivado often repeats the same message once per elaborated instance.
    compact = []
    seen = set()
    for line in selected + lines[-12:]:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        compact.append(line)
    return "\n".join(compact[:42])[:limit]


def classify_log(log: str, phase: str = "") -> tuple[str, str]:
    """Normalize vendor diagnostics into a repair routing category and code."""
    text = log or ""
    if license_error(text):
        return "environment", "license"
    if re.search(r"wall-clock budget exhausted|timed out|^\s*killed\s*$", text, re.I | re.M):
        return "environment", "timeout"
    if re.search(r"error while loading shared libraries|cannot open shared object file|"
                 r"GLIBC[^\n]*not found|unexpected exception when evaluating tcl command|"
                 r"segmentation fault|internal (?:fatal )?exception", text, re.I):
        return "environment", "runtime_failure"
    vendor_code = re.search(r"\[((?:VRFC|XSIM|Synth|DRC|Common)\s+[^\]\n]+)\]", text, re.I)
    patterns = [
        ("interface", r"cannot find port|port .* not found|not found while processing module instance|module .* not found"),
        ("syntax", r"syntax error|parse error|unexpected token|near '[^'\n]*'"),
        ("elaboration", r"static elaboration|elaborat(?:e|ion)|unresolved reference|circular dependency"),
        ("simulation", r"TB_FAILURE|ASSERTION\s+FAILED|mismatch|xsim.*error"),
        ("synthesis", r"Synth \d|DRC|timing|inferring latch|multi-driven|unsupported construct"),
    ]
    for category, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return category, vendor_code[1] if vendor_code else match[0][:80]
    if re.search(r"VRFC 10-\d+", text, re.I):
        return "syntax", vendor_code[1] if vendor_code else "vrfc_failure"
    if phase == "synthesis":
        return "synthesis", "vivado_failure"
    if phase == "simulation":
        return "simulation", "simulator_failure"
    return "tool", "nonzero_exit"


def verdict(rc: int, log: str) -> Result:
    if rc == 124:
        return Result("timeout", log, "environment", "timeout", "simulation")
    if rc == 127 or rc < 0 or rc >= 2**31 or license_error(log):
        return Result("environment", log, "environment", "license" if license_error(log) else "tool_missing", "simulation")
    category, code = classify_log(log, "simulation")
    if category == "environment":
        return Result("environment", log, category, code, "simulation")
    if rc != 0 or re.search(r"TB_FAILURE|ASSERTION\s+FAILED|(?:^|\s)(?:ERROR|FATAL):", log, re.I):
        return Result("mismatch", log, category, code, "simulation")
    values = re.findall(r"^\s*TB_RESULT\s+checks=(\d+)\s+errors=(\d+)\s*$", log, re.M)
    if len(values) != 1 or int(values[0][0]) <= 0:
        return Result("unverified", log, "simulation", "missing_verdict", "simulation")
    kind = "ok" if int(values[0][1]) == 0 else "mismatch"
    return Result(kind, log, "simulation", "tb_errors" if kind != "ok" else "verified", "simulation")


class Toolchain:
    def __init__(self, config: Config, output: Path, trace):
        self.config, self.output, self.trace = config, output, trace
        root = discover_vivado_bin(dict(os.environ))
        self.binaries = {}
        for name in ["xvlog", "xelab", "xsim", "vivado"]:
            # Keep all stages on the selected installation. An explicit bad
            # path must not silently mix binaries from another Vivado version.
            found = shutil.which(name, path=root if root else os.environ.get("PATH", ""))
            self.binaries[name] = found
        self.vivado_bin = Path(self.binaries["vivado"]).resolve().parent if self.binaries["vivado"] else None
        self.counter = 0

    @property
    def available(self):
        return all(self.binaries.values())

    def _work(self):
        base = os.environ.get("EDA_TMP") or os.environ.get("AGENT_TMP")
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix="rtl_", dir=base)

    def _execute(self, name: str, args: list[str], cwd: Path, deadline: Deadline,
                 phase: str, code: str) -> tuple[int, str]:
        self.counter += 1
        path = self.output / "logs" / f"{self.counter:03}_{phase}_{name}.log"
        started = time.monotonic()
        self.trace.log(tool=name, event="started", phase=phase, status="running",
                       code_sha256=digest(code), timeout_s=round(deadline.remaining, 3))
        if not self.binaries[name]:
            rc, log = 127, f"{name} unavailable; check PATH / XILINX_VIVADO / VIVADO_BIN"
        else:
            command = [self.binaries[name], *args]
            if os.name == "nt" and command[0].lower().endswith((".bat", ".cmd")):
                import subprocess
                command = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(command)]
            env = runtime_env(dict(os.environ), self.vivado_bin)
            for secret in ("LLM_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY_FILE", "FPGACHINA_TOKEN"):
                env.pop(secret, None)
            rc, log = run(command, cwd, deadline.remaining, path, env=env)
        if name == "xsim":
            result = verdict(rc, log)
        elif phase == "synthesis":
            result = self._synthesis_result(rc, log)
        else:
            result = self._compile_result(rc, log, tb=phase == "simulation", phase=phase)
        self.trace.log(tool=name, event="finished", phase=phase, rc=rc, code_sha256=digest(code),
                       status="passed" if result.ok else "failed", kind=result.kind,
                       category=result.category, diagnostic_code=result.code,
                       elapsed_s=round(time.monotonic() - started, 3),
                       excerpt=diagnostic(log, 1800), log_file=str(path.relative_to(self.output)))
        return rc, log

    @staticmethod
    def _compile_result(rc: int, log: str, tb: bool = False, phase: str = "lint"):
        phase = "simulation" if tb else phase
        if rc == 124:
            return Result("timeout", log, "environment", "timeout", phase)
        if rc == 127 or rc < 0 or rc >= 2**31 or license_error(log):
            return Result("environment", log, "environment", "license" if license_error(log) else "tool_missing", phase)
        category, code = classify_log(log, phase)
        if category == "environment":
            return Result("environment", log, category, code, phase)
        if rc != 0 or re.search(r"(?:^|\s)(?:ERROR|FATAL):", log, re.I):
            return Result("testbench_error" if tb else "design_error", log, category, code, phase)
        return Result("ok", log, "synthesis" if phase == "synthesis" else "compile", "ok", phase)

    @staticmethod
    def _synthesis_result(rc: int, log: str) -> Result:
        result = Toolchain._compile_result(rc, log, phase="synthesis")
        # Vivado echoes Tcl commands. Seeing the puts command is not evidence
        # that synthesis reached it; require the emitted line itself.
        if result.ok and not re.search(r"^\s*AGENT_SYNTH_SUCCESS\s*$", log, re.M):
            return Result("environment", "No terminal synthesis marker; synthesis is unverified\n" + log,
                          "environment", "missing_synthesis_marker", "synthesis")
        return result

    def lint(self, code: str, top: str, timeout: float) -> Result:
        budget = Deadline(timeout)
        with self._work() as directory:
            work = Path(directory)
            atomic_write(work / "solution.sv", code)
            for name, args in [("xvlog", ["--sv", "solution.sv"]),
                               ("xelab", [top, "-s", "dut_lint", "-timescale", "1ps/1ps"])]:
                rc, log = self._execute(name, args, work, budget, "lint", code)
                result = self._compile_result(rc, log)
                if not result.ok:
                    return result
        return Result("ok", "xvlog + xelab passed", "compile", "ok", "lint")

    def simulate(self, code: str, tb: str, timeout: float) -> Result:
        budget = Deadline(timeout)
        with self._work() as directory:
            work = Path(directory)
            atomic_write(work / "solution.sv", code)
            atomic_write(work / "tb_self_check.sv", tb)
            for name, args in [("xvlog", ["--sv", "solution.sv", "tb_self_check.sv"]),
                               ("xelab", ["tb_self_check", "-s", "self_check", "-timescale", "1ps/1ps"])]:
                rc, log = self._execute(name, args, work, budget, "simulation", code)
                result = self._compile_result(rc, log, tb=True)
                if not result.ok:
                    return result
            rc, log = self._execute("xsim", ["self_check", "-runall"], work, budget, "simulation", code)
            result = verdict(rc, log)
            self.trace.log(tool="simulation_verdict", kind=result.kind,
                           phase=result.phase, category=result.category, diagnostic_code=result.code,
                           code_sha256=digest(code), oracle_sha256=digest(tb),
                           verification_scope="model_generated_local_oracle")
            return result

    def synthesize(self, code: str, contract: Contract, timeout: float) -> Result:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.config.part):
            return Result("environment", "Invalid RTL_PART", "environment", "invalid_part", "synthesis")
        clocks = [p.name for p in contract.ports if p.direction == "input" and re.search(r"clk|clock", p.name, re.I)]
        with self._work() as directory:
            work = Path(directory)
            atomic_write(work / "solution.sv", code)
            constraints = "\n".join(f"create_clock -name {{{clk}}} -period {self.config.period_ns} [get_ports {{{clk}}}]" for clk in clocks)
            script = (f"read_verilog -sv solution.sv\nsynth_design -top {{{contract.top}}} "
                      f"-part {{{self.config.part}}} -mode out_of_context\n{constraints}\n"
                      "report_utilization\nputs {AGENT_SYNTH_SUCCESS}\nexit\n")
            atomic_write(work / "synth.tcl", script)
            rc, log = self._execute("vivado", ["-mode", "batch", "-nojournal", "-log", "vivado.log", "-source", "synth.tcl"],
                                    work, Deadline(timeout), "synthesis", code)
            return self._synthesis_result(rc, log)
