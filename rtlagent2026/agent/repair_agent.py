"""Subagent: Diagnostic & Surgical Repairer.

Role:
Pinpoints root causes from tool diagnostics (syntax, elaboration, simulation assertion,
or synthesis latch errors), applies DiffPatcher for surgical in-place edits, and falls
back to full regeneration only when structural collapse occurs.
"""

from __future__ import annotations

import re
from .diff_patcher import DiffPatcher
from .diagnostic_pruner import DiagnosticPruner
from .llm import LLM, extract_code
from .skills import Skill
from .spec_agent import HardwareSpec


class RepairAgent:
    def __init__(self, llm: LLM):
        self.llm = llm

    def repair(
        self,
        spec: HardwareSpec,
        broken_code: str,
        stage: str,
        error_log: str,
        skills: list[Skill] | None = None,
        force_full_rewrite: bool = False,
    ) -> str:
        """Apply targeted surgical repair based on toolchain diagnostics and skills."""
        pruned_log = DiagnosticPruner.prune(error_log, broken_code)

        # Attempt Level 2: Search/Replace Diff Patch first unless forced full rewrite
        if not force_full_rewrite and len(broken_code.splitlines()) > 15:
            patched = self._repair_diff(spec, broken_code, stage, pruned_log, skills)
            if patched and patched != broken_code:
                return patched

        # Fallback Level 4: Full Code Block Regeneration
        return self._repair_full(spec, broken_code, stage, pruned_log, skills)

    def _get_stage_advice(self, stage: str) -> str:
        if stage == "lint":
            return (
                "- FIX FOCUS (Lint): Semicolons, undeclared signals, vector range boundaries, "
                "or net/reg type mismatch (e.g. assigning to wire inside always block)."
            )
        elif stage == "simulation":
            return (
                "- FIX FOCUS (Simulation): Review reset priority, FSM next-state logic, "
                "shift register width/tap direction, and registered output timing."
            )
        elif stage == "synthesis":
            return (
                "- FIX FOCUS (Synthesis): Prevent latches (Synth 8-327) by assigning default "
                "values to all outputs at line 1 of every always @(*) block and adding default: in case statements."
            )
        elif stage == "preflight":
            return (
                "- FIX FOCUS (Preflight): remove testbench-only constructs from the DUT "
                "(initial, delays, $display/$finish, real/time, fork/join) while preserving the module contract."
            )
        return "- FIX FOCUS: Reconcile code with the formal hardware specification."

    def _repair_diff(
        self,
        spec: HardwareSpec,
        broken_code: str,
        stage: str,
        pruned_log: str,
        skills: list[Skill] | None = None,
    ) -> str:
        """Surgical Search/Replace Diff repair."""
        advice = self._get_stage_advice(stage)

        sys_prompt = (
            "You are an Expert RTL Surgical Debugger. "
            "Fix the specific error by outputting ONLY ONE OR TWO SEARCH/REPLACE diff blocks.\n\n"
            "DIFF FORMAT:\n"
            "<<<<<<< SEARCH\n"
            "exact lines to replace\n"
            "=======\n"
            "corrected replacement lines\n"
            ">>>>>>> REPLACE\n\n"
            "RULES:\n"
            "1. The SEARCH block must character-for-character match lines in the Current RTL Code.\n"
            "2. Make the minimal necessary fix (fix off-by-one, add missing default assignment, fix semicolon, adjust sensitivity).\n"
            f"3. {advice}\n"
            "4. Do not rewrite unaffected blocks."
        )

        user_content = (
            f"## Verification Failure Stage: {stage.upper()}\n\n"
            f"## Diagnostic Log:\n```\n{pruned_log}\n```\n\n"
            f"## Current RTL Code:\n```verilog\n{broken_code}\n```\n"
        )

        messages = [{"role": "system", "content": sys_prompt}]
        if skills:
            skill_text = "\n\n".join(s.render(2000) for s in skills)
            messages.append({"role": "system", "content": f"Hardware Domain Skills:\n\n{skill_text}"})
        messages.append({"role": "user", "content": user_content})

        try:
            res = self.llm.chat(messages, temperature=0.0, max_tokens=1024)
        except Exception:
            return broken_code
        ok, patched, _ = DiffPatcher.apply_patch(broken_code, res.text)
        if ok:
            return patched
        return broken_code

    def _repair_full(
        self,
        spec: HardwareSpec,
        broken_code: str,
        stage: str,
        pruned_log: str,
        skills: list[Skill] | None = None,
    ) -> str:
        """Full module replacement fallback."""
        advice = self._get_stage_advice(stage)

        sys_prompt = (
            "You are a Principal RTL Debugging and Optimization Specialist. "
            "Perform a complete fix on the given Verilog implementation that failed verification.\n\n"
            "REPAIR GUIDELINES:\n"
            "1. Focus strictly on the root cause indicated in the tool log. Do NOT alter working logic.\n"
            f"2. Header contract must match: module {spec.module_name} with ports {spec.ports}.\n"
            f"3. {advice}\n"
            "4. Output ONLY the complete repaired module inside a single ```verilog block."
        )

        user_content = (
            f"## Verification Failure Stage: {stage.upper()}\n\n"
            f"## Diagnostic Log:\n```\n{pruned_log}\n```\n\n"
            f"## Current RTL Code:\n```verilog\n{broken_code}\n```\n"
        )

        messages = [{"role": "system", "content": sys_prompt}]
        if skills:
            skill_text = "\n\n".join(s.render(2500) for s in skills)
            messages.append({"role": "system", "content": f"Hardware Domain Skills:\n\n{skill_text}"})
        messages.append({"role": "user", "content": user_content})

        try:
            res = self.llm.chat(messages, temperature=0.0, max_tokens=2048)
            fixed_code = extract_code(res.text, module_name=spec.module_name)
        except Exception:
            fixed_code = ""
        return fixed_code or broken_code
