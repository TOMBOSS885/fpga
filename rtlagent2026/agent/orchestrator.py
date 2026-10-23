"""Advanced Multi-Agent Orchestration Engine for RTL Agent.

Integrates:
- Spec Architect (SpecAgent)
- RTL Coder (CoderAgent)
- Verification Critic (VerifierAgent)
- Diagnostic Repairer (RepairAgent)
- Deterministic Interface Guard (DeterministicGuard)
- Quality Monotonicity Guard (QualityRollbackGuard)
- Dynamic Budget Controller (DynamicBudgetController)
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import time

from .budget_controller import DynamicBudgetController
from .coder_agent import CoderAgent
from .deterministic_guard import DeterministicGuard
from .diagnostic_pruner import DiagnosticPruner
from .llm import LLM
from .oscillation_guard import QualityRollbackGuard
from .repair_agent import RepairAgent
from .skills import Skill, load_skills, select_skills
from .spec_agent import SpecAgent
from .tools import RtlToolchain
from .verifier_agent import VerifierAgent

DEADLINE_S = float(os.environ.get("AGENT_DEADLINE_S", "360"))
RESERVE_S = float(os.environ.get("AGENT_RESERVE_S", "20"))
MAX_ROUNDS = int(os.environ.get("AGENT_MAX_ROUNDS", "4"))


class TraceLogger:
    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "w", encoding="utf-8")
        self._seq = 0

    def log(self, **fields) -> None:
        self._seq += 1
        fields.setdefault("trace_schema", 1)
        fields.setdefault("seq", self._seq)
        fields.setdefault("ts", round(time.time(), 3))
        self._fh.write(json.dumps(fields, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


class MultiAgentOrchestrator:
    def __init__(self, skill_dir: str):
        self.llm = LLM()
        self.tools = RtlToolchain()
        self.skills = [] if os.environ.get("AGENT_DISABLE_SKILLS") == "1" else load_skills(skill_dir)

        # Initialize subagents
        self.spec_agent = SpecAgent(self.llm)
        self.coder_agent = CoderAgent(self.llm)
        self.verifier_agent = VerifierAgent(self.llm, self.tools)
        self.repair_agent = RepairAgent(self.llm)

        # Initialize guards
        self.guard = DeterministicGuard()
        self.pruner = DiagnosticPruner()
        self.quality = QualityRollbackGuard()

    @staticmethod
    def _solution_sha256(code: str) -> str:
        return hashlib.sha256((code or "").encode("utf-8")).hexdigest()

    def _finish(
        self,
        trace: TraceLogger,
        budget: DynamicBudgetController,
        code: str,
        level: int,
        reason: str,
    ) -> str:
        """Record the exact artifact delivered to the submission endpoint.

        Tool-level trace entries describe intermediate candidates.  The final
        digest closes the audit chain by making it possible to compare the
        returned solution with the candidate that reached the best milestone.
        """
        trace.log(
            tool="orchestrator",
            event="finished",
            delivered_level=level,
            verification_scope="internal_milestone_not_official_score",
            bytes=len(code or ""),
            solution_sha256=self._solution_sha256(code),
            reason=reason,
            elapsed_total_s=round(budget.elapsed_s, 2),
        )
        return code

    def solve(self, prompt: str, interface: str, trace: TraceLogger, top_override: str = "") -> str:
        self.quality = QualityRollbackGuard()
        budget = DynamicBudgetController(deadline_s=DEADLINE_S, reserve_s=RESERVE_S, max_rounds=MAX_ROUNDS)
        self.llm.set_deadline(budget.deadline_at)
        self.llm.set_trace_hook(trace.log)

        trace.log(
            tool="orchestrator",
            event="start",
            architecture="advanced_multi_agent",
            llm=self.llm.describe(),
            vivado_available=self.tools.available,
            skills_loaded=[s.name for s in self.skills],
            budget={"deadline_s": DEADLINE_S, "reserve_s": RESERVE_S, "max_rounds": MAX_ROUNDS},
        )

        # ------------------------------------------------ Phase 1: Spec Architect
        t0 = time.time()
        spec = self.spec_agent.analyze(prompt, interface)
        requested_top = top_override.strip()
        if requested_top and re.fullmatch(r"[A-Za-z_]\w*", requested_top):
            # The CLI override is an explicit caller contract.  Keep the
            # parsed port list and all behavioral deductions, but make every
            # downstream artifact use the requested top module consistently.
            spec.module_name = requested_top
        trace.log(
            tool="spec_architect",
            event="spec_derived",
            spec=spec.to_dict(),
            top_override=requested_top or None,
            top_override_valid=not requested_top or bool(re.fullmatch(r"[A-Za-z_]\w*", requested_top)),
            elapsed_s=round(time.time() - t0, 3),
        )

        # ------------------------------------------------ Phase 2: RTL Coder (Initial Draft)
        t0 = time.time()
        # Feed design intent into skill selection as well as the literal word
        # "initial".  This activates arithmetic/shift/reset skills before the
        # first draft, where they have the largest effect on pass@1.
        initial_skills = select_skills(self.skills, "initial", prompt, spec.core_logic_summary,
                                       spec.recommended_pattern, " ".join(spec.control_priorities))
        trace.log(tool="skill_selector", phase="initial", selected=[s.name for s in initial_skills])
        current_code = self.coder_agent.generate(spec, prompt, initial_skills)
        trace.log(
            tool="rtl_coder",
            round=1,
            event="code_generated",
            code_len=len(current_code),
            elapsed_s=round(time.time() - t0, 3),
        )

        # ------------------------------------------------ Iterative Multi-Agent TDD Loop
        while budget.should_continue():
            rnd = budget.next_round()

            # --- STAGE 0: Shift-Left Deterministic Contract Guard ---
            ok_if, msg_if, current_code = self.guard.enforce_contract(current_code, spec.module_name, spec.ports)
            trace.log(tool="check_interface", round=rnd, rc=(0 if ok_if else 1),
                      code_sha256=hashlib.sha256(current_code.encode()).hexdigest()[:12], excerpt=msg_if[:300])

            if not ok_if:
                matched_skills = select_skills(self.skills, msg_if, "VRFC 10-3180")
                trace.log(tool="skill_selector", phase="interface_repair", round=rnd,
                          selected=[s.name for s in matched_skills])
                current_code = self.repair_agent.repair(
                    spec, current_code, "interface", msg_if, matched_skills, force_full_rewrite=True
                )
                continue

            # --- STAGE 0.5: deterministic synthesizability preflight ---
            rc_pre, log_pre = self.tools.preflight(current_code, spec.module_name)
            trace.log(tool="preflight", round=rnd, rc=rc_pre, excerpt=log_pre[:500])
            if rc_pre != 0:
                matched_skills = select_skills(self.skills, log_pre, "synthesis", "non-synthesizable")
                trace.log(tool="skill_selector", phase="preflight_repair", round=rnd,
                          selected=[s.name for s in matched_skills])
                current_code = self.repair_agent.repair(
                    spec, current_code, "preflight", log_pre, matched_skills, force_full_rewrite=False
                )
                continue

            # If toolchain not available (dry run or mock mode), return verified contract
            if not self.tools.available:
                # Keep offline/mock mode useful, but do not label an untested
                # draft as L3.  The contract-checked artifact is the only
                # honest deliverable when Vivado is absent.
                trace.log(tool="orchestrator", event="accept", reason="no_toolchain_available", round=rnd,
                          note=self.tools.reason)
                return self._finish(trace, budget, current_code, 0, "no_toolchain_available")

            # --- STAGE 1: xvlog + xelab Single Module Lint (L1) ---
            t_lint = budget.allocate_timeout("lint")
            rc_lint, log_lint = self.tools.lint(current_code, spec.module_name, timeout_s=t_lint)
            clean_lint_log = self.pruner.prune(log_lint, current_code)
            trace.log(tool="lint", round=rnd, rc=rc_lint, excerpt=clean_lint_log[:1000])

            if rc_lint < 0:
                best_code, level = self.quality.get_best_deliverable(current_code, final_level=0)
                return self._finish(trace, budget, best_code, level, "lint_environment_error")
            if rc_lint != 0:
                matched_skills = select_skills(self.skills, clean_lint_log)
                trace.log(tool="skill_selector", phase="lint_repair", round=rnd,
                          selected=[s.name for s in matched_skills])
                current_code = self.repair_agent.repair(
                    spec, current_code, "lint", clean_lint_log, matched_skills, force_full_rewrite=False
                )
                continue

            self.quality.record_attempt(current_code, current_level=1, round_num=rnd)
            # --- STAGE 2: Micro-Testbench Self-Checking Sim (L2) ---
            t_sim = budget.allocate_timeout("sim")
            t0 = time.time()
            rc_sim, log_sim = self.verifier_agent.verify(spec, prompt, current_code, timeout_s=t_sim)
            clean_sim_log = self.pruner.prune(log_sim, current_code)
            trace.log(
                tool="verifier_critic",
                round=rnd,
                rc=rc_sim,
                excerpt=clean_sim_log[:1000],
                elapsed_s=round(time.time() - t0, 3),
            )

            if rc_sim < 0:
                trace.log(tool="orchestrator", event="environment_error", stage="simulation",
                          rc=rc_sim, excerpt=clean_sim_log[:500])
                best_code, level = self.quality.get_best_deliverable(current_code, final_level=1)
                reason = "local_verification_unavailable" if rc_sim == -3 else "simulation_environment_error"
                return self._finish(trace, budget, best_code, level, reason)

            if rc_sim != 0:
                is_osc, _ = self.quality.record_attempt(current_code, current_level=1, round_num=rnd)
                matched_skills = select_skills(self.skills, clean_sim_log, "Mismatches", "ASSERTION")
                trace.log(tool="skill_selector", phase="simulation_repair", round=rnd,
                          selected=[s.name for s in matched_skills], oscillation=is_osc)
                current_code = self.repair_agent.repair(
                    spec, current_code, "simulation", clean_sim_log, matched_skills, force_full_rewrite=is_osc
                )
                continue

            # Reached L2: record milestone
            self.quality.record_attempt(current_code, current_level=2, round_num=rnd)

            # --- STAGE 3: Vivado Out-of-Context Synthesis (L3) ---
            t_synth = budget.allocate_timeout("synth")
            t0 = time.time()
            rc_synth, log_synth = self.tools.synth(current_code, spec.module_name, timeout_s=t_synth)
            clean_synth_log = self.pruner.prune(log_synth, current_code)
            trace.log(
                tool="synth",
                round=rnd,
                rc=rc_synth,
                excerpt=clean_synth_log[:1000],
                elapsed_s=round(time.time() - t0, 3),
            )

            if rc_synth < 0:
                trace.log(tool="orchestrator", event="environment_error", stage="synthesis",
                          rc=rc_synth, excerpt=clean_synth_log[:500])
                best_code, level = self.quality.get_best_deliverable(current_code, final_level=2)
                return self._finish(trace, budget, best_code, level, "synthesis_environment_error")

            if rc_synth == 0:
                self.quality.record_attempt(current_code, current_level=3, round_num=rnd)
                trace.log(
                    tool="orchestrator",
                    event="accept",
                    status="L3_SYNTHESIS_SUCCESS",
                    round=rnd,
                    elapsed_total_s=round(budget.elapsed_s, 2),
                )
                # AGGRESSIVE EARLY EXIT: maximize Cost score
                return self._finish(trace, budget, current_code, 3, "synthesis_success")

            # Synthesis failure (latches, multi-driven nets, etc.)
            matched_skills = select_skills(self.skills, clean_synth_log, "Synth 8-327", "inferring latch")
            trace.log(tool="skill_selector", phase="synthesis_repair", round=rnd,
                      selected=[s.name for s in matched_skills])
            current_code = self.repair_agent.repair(
                spec, current_code, "synthesis", clean_synth_log, matched_skills, force_full_rewrite=False
            )

        # Monotonic fallback to best verified deliverable
        best_code, level = self.quality.get_best_deliverable(current_code, final_level=0)
        return self._finish(trace, budget, best_code, level, "budget_exhausted")
