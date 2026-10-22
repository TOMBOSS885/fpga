"""Budgeted feedback loop with persistent best-candidate rollback."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path

from . import prompts
from .artifacts import TaskProfile, analyze_task, extract_sv, preflight, task_contract, tb_shape
from .client import Client, ModelError
from .config import Config
from .process import Deadline
from .skills import select
from .tools import Result, Toolchain, diagnostic
from .trace import atomic_write, digest

ROOT = Path(__file__).resolve().parents[1]


class Workflow:
    def __init__(self, config: Config, output: Path, trace, client=None, tools=None):
        self.config, self.output, self.trace = config, output, trace
        self.budget = Deadline(config.deadline_s, min(config.reserve_s, config.deadline_s * 0.1))
        self.client = client or Client(config, self.budget, trace)
        self.tools = tools or Toolchain(config, output, trace)
        self.best, self.rank, self.level = "", (-1, False), 0
        self.reason = "not_started"
        self.last_target = "dut"
        self.compiled = set()
        self.profile: TaskProfile | None = None
        self.stage = "idle"
        self.last_failure: dict | None = None

    def set_stage(self, phase: str, **fields):
        self.stage = phase
        self.trace.log(tool="workflow", event="stage", phase=phase,
                       remaining_s=round(self.budget.remaining, 3), **fields)

    def observe(self, result: Result, phase: str):
        fields = dict(phase=phase, kind=result.kind, category=result.category,
                      diagnostic_code=result.code)
        if not result.ok:
            self.last_failure = fields
        self.trace.log(tool="workflow", event="checked", **fields)

    def checkpoint(self, code: str, level: int, synth: bool = False):
        rank = (level, synth)
        if rank >= self.rank:
            self.best, self.rank, self.level = code, rank, level
            atomic_write(self.output / "solution.v", code)
            self.trace.log(tool="checkpoint", internal_level=level, synthesis_passed=synth,
                           code_sha256=digest(code), verification_scope="internal_not_official_score")

    def generate(self, prompt: str, interface: str, phase: str, code="", evidence="", tb="",
                 category="", diagnostic_code="") -> str:
        reserve = min(12.0, self.budget.remaining * 0.1)
        if self.budget.remaining < 2:
            raise ModelError("solve_budget_exhausted")
        self.set_stage(phase)
        skills, names = select(ROOT / "skill", prompt,
                               max_chars=3500 if phase.startswith("tb") else 6000,
                               profile=self.profile, phase=phase, failure=evidence,
                               failure_category=category)
        self.trace.log(tool="skill_selector", phase=phase, selected=names,
                       profile=self.profile.compact() if self.profile else "unavailable")
        user = f"## Immutable task specification\n{prompt}\n\n## Explicit interface (when supplied)\n{interface or '(in task text)'}\n"
        if self.profile:
            user += "\n## Parsed RTL task profile (heuristic; specification wins)\n" + self.profile.compact() + "\n"
            user += "\n## RTL checks (apply only as specified; do not invent behavior)\n" + self.profile.checks() + "\n"
        system = prompts.CODER
        if phase.startswith("tb"):
            # The initial independent reference never sees the DUT source.
            system = prompts.TB_REPAIR if tb else prompts.VERIFIER
            if tb:
                user += "\n## Previous testbench\n" + tb
            user += "\n## Verification notes\n" + skills
        else:
            if phase == "synth_repair":
                system = prompts.SYNTH_REPAIR
            else:
                system = prompts.ADJUDICATE if code and tb else prompts.REPAIR if code else prompts.CODER
            user += "\n## Relevant skills\n" + skills
            if code:
                user += "\n## Current complete DUT\n" + code
            if tb and evidence:
                user += "\n## Local self-test (not an authoritative hidden test)\n" + tb
        if evidence:
            user += f"\n## Diagnostic route\ncategory={category or 'unspecified'}; code={diagnostic_code or 'unspecified'}\n"
            user += "\n## Tool evidence\n" + diagnostic(evidence)
        reply = self.client.chat(system, user, phase, reserve=reserve,
                                 max_tokens=min(3072, self.config.max_tokens) if phase.startswith("tb") else None)
        self.last_target = "testbench" if phase.startswith("tb") or (tb and re.search(r"(?im)^\s*TARGET:\s*TESTBENCH\b", reply.text)) else "dut"
        is_tb = self.last_target == "testbench"
        target = "tb_self_check" if is_tb else self.contract.top
        result = extract_sv(reply.text, target, testbench=is_tb)
        if not result:
            raise ModelError("no_complete_target_module_in_model_response")
        if reply.finish == "length":
            self.trace.log(tool="artifact", event="output_truncated", role=phase,
                           note="Only complete modules are retained")
        self.trace.log(tool="artifact", event="model_artifact", target=self.last_target,
                       code_sha256=digest(result), phase=phase,
                       profile=self.profile.compact() if self.profile else "unavailable")
        atomic_write(self.output / ("testbench.sv" if is_tb else "candidate.sv"), result)
        return result

    def solve(self, prompt: str, interface: str = "") -> str:
        self.contract = task_contract(prompt, interface)
        self.profile = analyze_task(prompt, interface, self.contract)
        self.trace.log(tool="workflow", event="start", architecture="budgeted_rtl_tool_loop",
                       contract=asdict(self.contract), model=self.config.model,
                       deadline_s=self.config.deadline_s, toolchain_available=self.tools.available,
                       task_sha256=digest(prompt + "\n" + interface),
                       profile=self.profile.compact(), profile_data=asdict(self.profile))
        if not prompt.strip():
            self.reason = "empty_prompt"
            return self.best
        code = self.generate(prompt, interface, "design")
        # Preserve even the initial model artifact before any slow verification.
        self.checkpoint(code, 0)
        tb, tb_attempts = "", 0
        seen = set()
        for revision in range(self.config.max_repairs + 1):
            self.reason = "repair_budget_exhausted"
            self.set_stage("preflight", revision=revision)
            ok, message = preflight(code, self.contract)
            self.trace.log(tool="check_interface", phase="preflight", rc=0 if ok else 1,
                           category="interface", diagnostic_code="ok" if ok else "contract_preflight",
                           code_sha256=digest(code), excerpt=message[:1800])
            failure = Result("design_error", message, "interface", "contract_preflight", "preflight")
            if ok:
                self.set_stage("compile", revision=revision)
                if digest(code) in self.compiled:
                    failure = Result("ok", "Identical source already passed xvlog/xelab in this solve",
                                     "compile", "cache_hit", "lint")
                    self.trace.log(tool="lint_cache", event="reuse", code_sha256=digest(code))
                else:
                    failure = self.tools.lint(code, self.contract.top, self.budget.cap(self.config.lint_s))
                self.observe(failure, "compile")
                if failure.ok:
                    self.compiled.add(digest(code))
                    self.checkpoint(code, 1)
                    local_pass = False
                    # TB generation has a separate model budget; do not charge
                    # it against the xvlog/xelab/xsim stage's timeout.
                    if not tb and self.budget.remaining > 18:
                        try:
                            tb = self.generate(prompt, interface, "tb_generate")
                        except ModelError as exc:
                            self.trace.log(tool="verifier", event="unavailable", reason=str(exc))
                    failure = Result("unverified", "No independent local testbench", "simulation", "no_oracle", "simulation")
                    if not tb or self.budget.remaining <= 2:
                        self.observe(failure, "simulation")
                    while tb and self.budget.remaining > 2:
                        self.set_stage("simulation", revision=revision)
                        failure = self.tools.simulate(code, tb, self.budget.cap(self.config.sim_s, reserve=5)) if tb_shape(tb, self.contract.top) else Result("testbench_error", "Testbench lacks DUT instance/comparison/terminal verdict", "simulation", "invalid_shape", "simulation")
                        self.observe(failure, "simulation")
                        if failure.kind not in {"testbench_error", "unverified"} or tb_attempts >= self.config.tb_repairs:
                            break
                        tb_attempts += 1
                        try:
                            tb = self.generate(prompt, interface, "tb_repair", evidence=failure.log, tb=tb,
                                               category=failure.category, diagnostic_code=failure.code)
                        except ModelError:
                            break
                    if failure.ok:
                        self.checkpoint(code, 2)
                        local_pass = True
                    if failure.kind in {"environment", "timeout"}:
                        self.reason = "simulation_" + failure.kind
                        break
                    # A missing/broken local oracle must not prevent checking
                    # synthesizability of an otherwise compiled candidate.
                    if local_pass or failure.kind in {"unverified", "testbench_error"}:
                        self.set_stage("synthesis", revision=revision)
                        synthesis = self.tools.synthesize(code, self.contract, self.budget.cap(self.config.synth_s))
                        self.observe(synthesis, "synthesis")
                        if synthesis.ok:
                            self.checkpoint(code, 3 if local_pass else 1, synth=True)
                            self.reason = "locally_verified" if local_pass else "compiled_and_synthesized_unverified_function"
                            break
                        failure = synthesis
                    elif failure.kind == "mismatch":
                        failure.log = "LOCAL ORACLE MISMATCH: first check the specification; the self-test may be wrong.\n" + failure.log
            else:
                self.observe(failure, "preflight")
            if failure.kind in {"environment", "timeout"}:
                self.reason = failure.kind
                break
            fingerprint = (digest(code), digest(tb), failure.kind, digest(diagnostic(failure.log)))
            if fingerprint in seen:
                self.reason = "repeated_candidate_and_error"
                break
            seen.add(fingerprint)
            if revision >= self.config.max_repairs or self.budget.remaining < 6:
                break
            repair_phase = self._repair_phase(failure)
            repaired = self.generate(prompt, interface, repair_phase, code, failure.log,
                                     tb=tb if failure.kind == "mismatch" else "",
                                     category=failure.category, diagnostic_code=failure.code)
            if self.last_target == "testbench":
                if tb_attempts >= self.config.tb_repairs:
                    self.reason = "oracle_repair_budget_exhausted"
                    break
                tb, tb_attempts = repaired, tb_attempts + 1
            else:
                code = repaired
        return self.best

    @staticmethod
    def _repair_phase(failure: Result) -> str:
        """Route a failure to a narrow prompt while retaining old trace labels."""
        if failure.kind == "testbench_error":
            return "tb_repair"
        if failure.kind == "mismatch":
            return "dut_repair"
        if failure.category == "synthesis" or failure.phase == "synthesis":
            return "synth_repair"
        if failure.category in {"interface", "syntax", "elaboration"}:
            return "dut_compile_repair"
        return "dut_repair"

    def finish(self):
        atomic_write(self.output / "solution.v", self.best)
        record = dict(internal_level=self.level, synthesis_passed=self.rank[1],
                      solution_sha256=digest(self.best), reason=self.reason,
                      last_stage=self.stage, last_failure=self.last_failure,
                      task_profile=asdict(self.profile) if self.profile else None,
                      elapsed_s=round(time.monotonic() - self.budget.started, 3),
                      verification_scope="model_generated_local_oracle_not_official_score")
        atomic_write(self.output / "result.json", json.dumps(record, indent=2) + "\n")
        self.trace.log(tool="workflow", event="finished", **record)
