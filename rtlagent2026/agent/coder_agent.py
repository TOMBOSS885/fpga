"""Subagent: RTL Coder.

Role:
Translates the formal hardware specification, FSM State Transition Table, and
pattern guidance into synthesizable Verilog/SystemVerilog that strictly conforms
to Vivado rules and the AMD target part (xczu3eg-sbva484-1-e, 5ns clock).
"""

from __future__ import annotations

import re
import os
from .llm import LLM, extract_code
from .skills import Skill
from .spec_agent import HardwareSpec


class CoderAgent:
    def __init__(self, llm: LLM):
        self.llm = llm

    @staticmethod
    def _reset_condition(spec: HardwareSpec) -> str:
        if not spec.reset_port:
            return ""
        return f"!{spec.reset_port}" if spec.reset_polarity == "active_low" else spec.reset_port

    @staticmethod
    def _clock_sensitivity(spec: HardwareSpec) -> str:
        clock = spec.clock_port or "clk"
        if spec.reset_port and spec.reset_sync == "async":
            edge = "negedge" if spec.reset_polarity == "active_low" else "posedge"
            return f"posedge {clock} or {edge} {spec.reset_port}"
        return f"posedge {clock}"

    def _generate_canonical_header(self, spec: HardwareSpec) -> str:
        """Construct exact Verilog module declaration header to constrain LLM generation."""
        port_lines = []
        for p in spec.ports:
            d = p["direction"]
            w = p["width"]
            n = p["name"]
            w_str = f"[{w-1}:0] " if w > 1 else ""
            # Use a procedural-safe declaration.  The contract checker later
            # downgrades it to a net when the model selected a continuous
            # ``assign`` driver.
            type_str = "reg " if d == "output" else ""
            port_lines.append(f"    {d} {type_str}{w_str}{n}")
        return f"module {spec.module_name} (\n" + ",\n".join(port_lines) + "\n);"

    def generate(self, spec: HardwareSpec, prompt: str, injected_skills: list[Skill] | None = None) -> str:
        """Draft synthesizable RTL implementation conforming to spec and FSM plan."""
        canonical_header = self._generate_canonical_header(spec)

        sys_prompt = (
            "You are a Senior Digital ASIC/FPGA Front-End Design Engineer. "
            "Implement a clean, synthesizable Verilog/SystemVerilog module for AMD Vivado (target xczu3eg-sbva484-1-e, 5ns clock period).\n\n"
            "CRITICAL DESIGN RULES:\n"
            "1. EXACT MODULE HEADER: You MUST begin your module declaration with this exact header:\n"
            f"```verilog\n{canonical_header}\n```\n"
            "   Do NOT rename, reorder, omit, or add ports.\n"
            "2. Do NOT write testbench code, initial blocks with delays (#), or $finish.\n"
            "3. Sequential logic must use non-blocking assignments (<=). Combinational logic must use blocking assignments (=).\n"
            "4. For combinational always @(*), ensure ALL branches and signals have default assignments in the first line to prevent unintended latches (Synth 8-327).\n"
            "5. If an FSM is involved, use an idiomatic Three-Process FSM pattern: (1) state register update with reset, (2) combinational next_state logic with default assignment and default case, (3) registered or clean combinational output.\n"
            "6. Respect reset semantics: synchronicity (sync vs async) and polarity (active-high vs active-low).\n"
            "7. Output ONLY a single ```verilog code block. No explanations or prose outside the code block."
        )

        guidance_notes = []
        if spec.recommended_pattern == "shift_register":
            guidance_notes.append(
                "- ARCHITECTURE NOTE: For sequence detection or LFSR, prefer an N-bit shift register `sr <= {sr[N-2:0], in}`. "
                "Shift registers naturally handle overlapping sequences without state explosion or deadlocks."
            )
        if spec.control_priorities:
            guidance_notes.append(
                f"- CONTROL PRIORITY: Strictly evaluate control signals in this order: {' > '.join(spec.control_priorities)}. "
                "Use cascaded `if ... else if ... else`."
            )
        if not spec.is_sequential:
            guidance_notes.append(
                "- COMBINATIONAL DESIGN: This module has no clock or reset. Use continuous assignments (`assign`) or "
                "`always @(*)` with default assignments on line 1."
            )

        fsm_info = ""
        if spec.fsm_states or spec.state_transition_table:
            fsm_info = f"\nFSM States: {spec.fsm_states}\nState Transition Table:\n{spec.state_transition_table}\n"

        guidance_text = "\n".join(guidance_notes)

        spec_summary = (
            f"Module Name: {spec.module_name}\n"
            f"Ports: {spec.ports}\n"
            f"Sequential: {spec.is_sequential} (Clock: '{spec.clock_port}', Reset: '{spec.reset_port}', "
            f"Polarity: {spec.reset_polarity}, Sync: {spec.reset_sync}, Reset Value: {spec.reset_value})\n"
            f"Core Logic: {spec.core_logic_summary}\n"
            f"{fsm_info}"
            f"\nArchitectural Guidance:\n{guidance_text}\n"
        )

        user_content = f"## Original Task Description:\n{prompt}\n\n## Formal Hardware Specification & FSM Plan:\n{spec_summary}\n"

        messages = [{"role": "system", "content": sys_prompt}]

        if injected_skills:
            skill_text = "\n\n".join(s.render(2500) for s in injected_skills)
            messages.append({
                "role": "system",
                "content": f"Apply the following hardware engineering domain skills:\n\n{skill_text}",
            })

        messages.append({"role": "user", "content": user_content})

        try:
            res = self.llm.chat(messages, temperature=0.2, max_tokens=2048)
            code = extract_code(res.text, module_name=spec.module_name)
        except Exception:
            code = ""
        if code and re.search(r"\bmodule\s+[A-Za-z_]\w*\b", code) and "endmodule" in code:
            return code
        # Pattern-specific RTL is useful for the offline mock smoke tests, but
        # using it after a real model failure would make the submitted agent
        # produce benchmark-shaped answers without a substantive model call.
        # Keep that path development-only so the competition artifact remains
        # within the no-hardcoded-answer rule.
        if self._allow_deterministic_fallback():
            return self._fallback_code(spec, prompt)
        return ""

    def _allow_deterministic_fallback(self) -> bool:
        """Return whether pattern-specific fallback RTL is safe to use.

        The bundled mock backend exists for contract and smoke testing.  A
        real submission backend must never silently turn an unavailable or
        malformed model response into a task-shaped answer.
        """
        return self.llm.backend == "mock" and os.environ.get(
            "AGENT_ALLOW_DETERMINISTIC_FALLBACK", "1"
        ).strip().lower() not in {"0", "false", "no", "off"}

    def _fallback_code(self, spec: HardwareSpec, prompt: str) -> str:
        """Produce a small synthesizable fallback when the model is unavailable.

        This is deliberately pattern based and conservative.  It covers the
        recurring RTL task families used for smoke tests and gives the repair
        loop a valid artifact to work on after a transient model failure.
        """
        ports = spec.ports
        header = self._generate_canonical_header(spec)
        lower = (prompt or "").lower()
        names = {p["name"].lower(): p for p in ports}
        if "population count" in lower or "popcount" in lower or "number of bits" in lower and "1" in lower:
            inp = next((p for p in ports if p["direction"] == "input" and p["width"] > 1), None)
            out = next((p for p in ports if p["direction"] == "output"), None)
            if inp and out:
                return (f"{header}\n  integer i;\n  always @(*) begin\n"
                        f"    {out['name']} = {out['width']}'d0;\n"
                        f"    for (i = 0; i < {inp['width']}; i = i + 1) "
                        f"{out['name']} = {out['name']} + {inp['name']}[i];\n"
                        "  end\nendmodule\n")
        if "1101" in lower and any(p["name"].lower() == "detected" for p in ports):
            excluded = {spec.clock_port, spec.reset_port, "load", "enable", "clear"}
            candidates = [p for p in ports if p["direction"] == "input"
                          and p["name"] not in excluded]
            inp = next((p for p in candidates if p["name"].lower() in
                        {"in", "din", "serial_in", "bit_in", "data_in"}),
                       candidates[0] if candidates else None)
            out = next((p for p in ports if p["direction"] == "output"), None)
            if inp and out and spec.clock_port:
                reset_condition = self._reset_condition(spec)
                clock_event = self._clock_sensitivity(spec)
                if reset_condition:
                    reset_branch = (f"    if ({reset_condition}) begin sr <= 4'b0; "
                                    f"{out['name']} <= 1'b0; end\n"
                                    "    else begin")
                else:
                    reset_branch = "    begin"
                return (f"{header}\n  reg [3:0] sr;\n  always @({clock_event}) begin\n"
                        f"{reset_branch} sr <= {{sr[2:0], {inp['name']}}}; "
                        f"{out['name']} <= ({{sr[2:0], {inp['name']}}} == 4'b1101); end\n"
                        "  end\nendmodule\n")
        if "lfsr" in lower or "linear feedback shift" in lower:
            q = next((p for p in ports if p["direction"] == "output" and p["width"] >= 4), None)
            data_candidates = [p for p in ports if p["direction"] == "input"
                               and p["name"] not in {spec.clock_port, spec.reset_port}]
            data = next((p for p in data_candidates if p["name"].lower() in {"data", "seed", "load_data"}), None)
            if data is None:
                data = next((p for p in data_candidates if p["width"] == q["width"]
                             and p["name"].lower() not in {"load", "enable", "clear"}), None)
            load = next((p for p in ports if p["direction"] == "input"
                         and (p["name"].lower() == "load" or p["name"].lower().endswith("_load"))), None)
            if q and data and spec.clock_port:
                w = q["width"]
                msb = w - 1
                # The documented 8-bit taps are preserved; for other widths
                # use a safe maximal-looking feedback expression.
                taps = (f"{q['name']}[7] ^ {q['name']}[5] ^ {q['name']}[4] ^ {q['name']}[3]"
                        if w == 8 else
                        f"{q['name']}[{msb}] ^ {q['name']}[{max(0, msb-2)}]")
                reset_val = "8'h01" if w == 8 else f"{w}'d1"
                load_clause = f" else if ({load['name']}) {q['name']} <= {data['name']};" if load else ""
                shift = f"{{{q['name']}[{msb-1}:0], feedback}}"
                reset_condition = self._reset_condition(spec)
                reset_branch = (f"if ({reset_condition}) {q['name']} <= {reset_val};"
                                if reset_condition else "if (1'b0) " + q["name"] + " <= 1'b0;")
                clock_event = self._clock_sensitivity(spec)
                return (f"{header}\n  wire feedback = {taps};\n  always @({clock_event}) begin\n"
                        f"    {reset_branch}"
                        f"{load_clause} else {q['name']} <= {shift};\n  end\nendmodule\n")
        # Last resort: preserve the exact contract and make outputs known.
        assigns = []
        for p in ports:
            if p["direction"] == "output":
                assigns.append(f"  always @(*) {p['name']} = {p['width']}'d0;")
        return header + ("\n" + "\n".join(assigns) if assigns else "") + "\nendmodule\n"
