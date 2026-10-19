"""Vivado-only verification; local milestones are never official scores."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import Contract
from .config import Config
from .process import Deadline, run
from .trace import atomic_write, digest


@dataclass
class Result:
    kind: str
    log: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


def license_error(log: str) -> bool:
    return bool(re.search(r"license.{0,45}(?:checkout failed|not found|unavailable|not available)|"
                          r"(?:failed|unable|could not).{0,40}(?:check.?out|obtain).{0,25}license|"
                          r"no valid license|FLEXnet Licensing error", log, re.I))


def diagnostic(log: str, limit: int = 5000) -> str:
    lines = log.splitlines()
    selected = [line for line in lines if re.search(r"error|fatal|mismatch|warning|timeout|TB_", line, re.I)]
    return "\n".join(selected[:30] + lines[-12:])[:limit]


def verdict(rc: int, log: str) -> Result:
    if rc == 124:
        return Result("timeout", log)
    if rc == 127 or rc < 0 or rc >= 2**31 or license_error(log):
        return Result("environment", log)
    if rc != 0 or re.search(r"TB_FAILURE|ASSERTION\s+FAILED|(?:^|\s)(?:ERROR|FATAL):", log, re.I):
        return Result("mismatch", log)
    values = re.findall(r"^\s*TB_RESULT\s+checks=(\d+)\s+errors=(\d+)\s*$", log, re.M)
    if len(values) != 1 or int(values[0][0]) <= 0:
        return Result("unverified", log)
    return Result("ok" if int(values[0][1]) == 0 else "mismatch", log)


class Toolchain:
    def __init__(self, config: Config, output: Path, trace):
        self.config, self.output, self.trace = config, output, trace
        root = os.environ.get("VIVADO_BIN")
        if not root and os.environ.get("XILINX_VIVADO"):
            root = str(Path(os.environ["XILINX_VIVADO"]) / "bin")
        self.binaries = {}
        for name in ["xvlog", "xelab", "xsim", "vivado"]:
            found = shutil.which(name, path=(root + os.pathsep + os.environ.get("PATH", "")) if root else None)
            self.binaries[name] = found
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
        if not self.binaries[name]:
            rc, log = 127, f"{name} unavailable; check PATH / XILINX_VIVADO / VIVADO_BIN"
        else:
            command = [self.binaries[name], *args]
            if os.name == "nt" and command[0].lower().endswith((".bat", ".cmd")):
                import subprocess
                command = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(command)]
            env = dict(os.environ, XILINX_LOCAL_USER_DATA="NO")
            for secret in ("LLM_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY_FILE", "FPGACHINA_TOKEN"):
                env.pop(secret, None)
            rc, log = run(command, cwd, deadline.remaining, path, env=env)
        self.trace.log(tool=name, phase=phase, rc=rc, code_sha256=digest(code),
                       excerpt=diagnostic(log, 1800), log_file=str(path.relative_to(self.output)))
        return rc, log

    @staticmethod
    def _compile_result(rc: int, log: str, tb: bool = False):
        if rc == 124:
            return Result("timeout", log)
        if rc == 127 or rc < 0 or rc >= 2**31 or license_error(log):
            return Result("environment", log)
        if rc != 0 or re.search(r"(?:^|\s)(?:ERROR|FATAL):", log, re.I):
            return Result("testbench_error" if tb else "design_error", log)
        return Result("ok", log)

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
        return Result("ok", "xvlog + xelab passed")

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
                           code_sha256=digest(code), oracle_sha256=digest(tb),
                           verification_scope="model_generated_local_oracle")
            return result

    def synthesize(self, code: str, contract: Contract, timeout: float) -> Result:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.config.part):
            return Result("environment", "Invalid RTL_PART")
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
            result = self._compile_result(rc, log)
            if result.ok and "AGENT_SYNTH_SUCCESS" not in log:
                return Result("environment", "No terminal synthesis marker; synthesis is unverified\n" + log)
            return result
