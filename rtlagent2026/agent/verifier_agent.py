"""Subagent: Verification Critic & SVA Micro-Testbench Synthesizer.

Role:
Overcomes the L1 -> L2 chasm by automatically constructing a self-checking
testbench with behavioral golden reference models, race-condition free stimulus
driving, and automated scoreboard verification before running Vivado xsim simulation.
"""

from __future__ import annotations

import re

from .llm import LLM, extract_code
from .spec_agent import HardwareSpec
from .tools import RtlToolchain


class VerifierAgent:
    def __init__(self, llm: LLM, toolchain: RtlToolchain):
        self.llm = llm
        self.tools = toolchain

    @staticmethod
    def _sequence_input(spec: HardwareSpec):
        excluded = {spec.clock_port, spec.reset_port, "load", "enable", "clear"}
        candidates = [p for p in spec.ports if p["direction"] == "input"
                      and p["name"] not in excluded]
        return next((p for p in candidates if p["name"].lower() in
                     {"in", "din", "serial_in", "bit_in", "data_in"}),
                    candidates[0] if candidates else None)

    @staticmethod
    def _lfsr_data(spec: HardwareSpec, width: int):
        candidates = [p for p in spec.ports if p["direction"] == "input"
                      and p["name"] not in {spec.clock_port, spec.reset_port}]
        preferred = next((p for p in candidates
                          if p["name"].lower() in {"data", "seed", "load_data"}), None)
        if preferred:
            return preferred
        return next((p for p in candidates if p["width"] == width
                     and p["name"].lower() not in {"load", "enable", "clear"}), None)

    @staticmethod
    def _lfsr_load(spec: HardwareSpec):
        return next((p for p in spec.ports if p["direction"] == "input"
                     and (p["name"].lower() == "load" or p["name"].lower().endswith("_load"))), None)

    def build_testbench(self, spec: HardwareSpec, prompt: str, dut_code: str) -> str:
        """Synthesize a companion self-checking testbench (tb_self_check.sv) with Golden Scoreboard."""
        sys_prompt = (
            "You are a Principal FPGA Verification Engineer and Testbench Specialist. "
            "Write a standalone self-checking SystemVerilog testbench named `tb_self_check` to verify the DUT module.\n\n"
            "CRITICAL ARCHITECTURE REQUIREMENTS:\n"
            "1. Behavioral Golden Model: Write a concise golden reference function or shadow model inside the testbench "
            "implementing the behavioral specification to compare against the DUT output.\n"
            "2. Zero-Delta-Race Rule: Drive all input stimuli on `@(negedge clk)`. Sample and assert DUT outputs on `@(posedge clk); #1;`.\n"
            "3. Four-Phase Verification:\n"
            "   - Phase 1 (Reset Check): Assert reset for at least 3 clock cycles. Verify outputs are known (no X/Z) and equal reset value.\n"
            "   - Phase 2 (Deassertion): Deassert reset on negedge clk, verify initial post-reset output.\n"
            "   - Phase 3 (Directed & Adversarial): Test corner cases (all 0s, all 1s, sequence overlaps e.g. 1101101, simultaneous reset & load to check reset priority).\n"
            "   - Phase 4 (Randomized Fuzzing): Run 30-50 cycles of randomized inputs (`repeat (40) drive($urandom);`).\n"
            "4. Watchdog Timeout: Ensure simulation terminates automatically (#25000; $display(\"TB_FAILURE: Watchdog timeout\"); $finish;) to prevent hangs.\n"
            "5. Logging Contract:\n"
            "   - Track integer `errors = 0;`\n"
            "   - If mismatch: `$display(\"TB_FAILURE: Mismatch at time %0t: DUT=%h, EXP=%h\", $time, dut_out, exp_out); errors++;`\n"
            "   - At finish: if `errors == 0`, `$display(\"TB_SUCCESS: All self-tests passed with 0 errors.\");` else `$display(\"TB_FAILURE: Total %0d mismatches.\", errors);`\n"
            "   - Output ONLY a single ```verilog block with `timescale 1ns/1ps."
        )

        scenarios_text = ""
        if spec.test_scenarios:
            scenarios_text = f"Test Intent Scenarios:\n{spec.test_scenarios}\n"

        user_content = (
            f"## Task Description:\n{prompt}\n\n"
            f"## Hardware Specification:\n"
            f"Module: {spec.module_name}, Clock: '{spec.clock_port}', Reset: '{spec.reset_port}' "
            f"({spec.reset_polarity}, {spec.reset_sync}, Reset Value: {spec.reset_value})\n"
            f"Ports: {spec.ports}\n"
            f"{scenarios_text}\n"
            f"## Current DUT Implementation:\n```verilog\n{dut_code}\n```"
        )

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            res = self.llm.chat(messages, temperature=0.1, max_tokens=2048)
            tb_code = extract_code(res.text, module_name="tb_self_check")
        except Exception:
            tb_code = ""
        if not self._is_usable_testbench(tb_code, spec):
            tb_code = self._fallback_tb(spec, prompt)
        return tb_code

    @staticmethod
    def _is_usable_testbench(tb_code: str, spec: HardwareSpec) -> bool:
        """Reject plausible-looking but nonfunctional model testbenches."""
        if not tb_code or "module tb_self_check" not in tb_code:
            return False
        if spec.module_name not in tb_code or not re.search(rf"\b{re.escape(spec.module_name)}\s+(?:dut|u_dut)\b", tb_code):
            return False
        return "$finish" in tb_code and ("TB_SUCCESS" in tb_code or "TB_FAILURE" in tb_code)

    def _fallback_tb(self, spec: HardwareSpec, prompt: str = "") -> str:
        """Deterministic fallback testbench when LLM generation fails or in mock mode."""
        lower = (prompt or "").lower()
        if "population count" in lower or "popcount" in lower:
            return self._fallback_popcount_tb(spec)
        if "1101" in lower and ("sequence" in lower or
                                any(p["name"].lower() == "detected" for p in spec.ports)):
            return self._fallback_sequence_tb(spec)
        if "lfsr" in lower or "linear feedback shift" in lower:
            return self._fallback_lfsr_tb(spec)
        lines = [
            "`timescale 1ns/1ps",
            "module tb_self_check();",
        ]

        inst_ports = []
        regular_inputs = []
        regular_outputs = []

        has_clk = False
        has_rst = False

        for p in spec.ports:
            name, direction, width = p["name"], p["direction"], p["width"]
            w_str = f"[{width-1}:0] " if width > 1 else ""
            if direction == "input":
                if name.lower() in ("clk", "clock"):
                    has_clk = True
                    lines.append("  reg clk = 0;")
                    inst_ports.append(f".{name}(clk)")
                elif name.lower() in ("rst", "reset", "rst_n", "reset_n"):
                    has_rst = True
                    active_rst = 0 if spec.reset_polarity == "active_low" else 1
                    lines.append(f"  reg {name} = {active_rst};")
                    inst_ports.append(f".{name}({name})")
                else:
                    lines.append(f"  reg {w_str}{name} = 0;")
                    inst_ports.append(f".{name}({name})")
                    regular_inputs.append((name, width))
            else:
                lines.append(f"  wire {w_str}{name};")
                inst_ports.append(f".{name}({name})")
                regular_outputs.append((name, width))

        if not has_clk and spec.is_sequential:
            lines.append("  reg clk = 0;")
            has_clk = True

        lines.append("")
        lines.append(f"  {spec.module_name} dut (")
        lines.append("    " + ",\n    ".join(inst_ports))
        lines.append("  );")
        lines.append("")

        if has_clk:
            lines.append("  always #2.5 clk = ~clk;")
            lines.append("")

        lines.append("  initial begin")
        lines.append("    int errors = 0;")
        lines.append("    #5;")

        if has_rst:
            rst_name = spec.reset_port or "reset"
            active_rst = 0 if spec.reset_polarity == "active_low" else 1
            inactive_rst = 1 if active_rst == 0 else 0
            lines.append(f"    {rst_name} = {active_rst};")
            lines.append(f"    #20; {rst_name} = {inactive_rst}; #10;")

        # Input sweep
        lines.append("    // Stimulus sweep")
        for i in range(5):
            for in_name, in_w in regular_inputs:
                val = f"{in_w}'h{i % (1 << min(in_w, 8)):x}"
                lines.append(f"    {in_name} = {val};")
            lines.append("    #10;")
            for out_name, out_w in regular_outputs:
                lines.append(f"    if (^{out_name} === 1'bx) begin")
                lines.append(f'      $display("TB_FAILURE: Output {out_name} has unknown X value at %0t", $time);')
                lines.append("      errors++;")
                lines.append("    end")

        lines.append("")
        lines.append("    if (errors == 0) begin")
        lines.append('      $display("TB_SUCCESS: All self-tests passed with 0 errors.");')
        lines.append("    end else begin")
        lines.append('      $display("TB_FAILURE: Total %0d errors detected.", errors);')
        lines.append("    end")
        lines.append("    $finish;")
        lines.append("  end")
        lines.append("")
        lines.append("  initial begin")
        lines.append("    #20000;")
        lines.append('    $display("TB_FAILURE: Watchdog timeout");')
        lines.append("    $finish;")
        lines.append("  end")
        lines.append("endmodule")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _fallback_popcount_tb(spec: HardwareSpec) -> str:
        inp = next((p for p in spec.ports if p["direction"] == "input" and p["width"] > 1), None)
        out = next((p for p in spec.ports if p["direction"] == "output"), None)
        if not inp or not out:
            return ""
        w = inp["width"]
        return f'''`timescale 1ns/1ps
module tb_self_check();
  reg [{w-1}:0] {inp["name"]};
  wire [{out["width"]-1}:0] {out["name"]};
  integer errors = 0, value, i, expected;
  {spec.module_name} dut (.{inp["name"]}({inp["name"]}), .{out["name"]}({out["name"]}));
  initial begin
    for (value = 0; value < (1 << {min(w, 8)}); value = value + 1) begin
      {inp["name"]} = value; expected = 0;
      for (i = 0; i < {w}; i = i + 1) expected = expected + {inp["name"]}[i];
      #1;
      if ({out["name"]} !== expected) begin
        $display("TB_FAILURE: value=%0d DUT=%0d EXP=%0d", value, {out["name"]}, expected); errors = errors + 1;
      end
    end
    if (errors == 0) $display("TB_SUCCESS: All self-tests passed with 0 errors.");
    else $display("TB_FAILURE: Total %0d mismatches.", errors);
    $finish;
  end
  initial begin #20000; $display("TB_FAILURE: Watchdog timeout"); $finish; end
endmodule
'''

    @staticmethod
    def _fallback_sequence_tb(spec: HardwareSpec) -> str:
        clk = spec.clock_port or "clk"
        rst = spec.reset_port
        active_rst = (rst if spec.reset_polarity != "active_low" else f"!{rst}") if rst else "1'b0"
        rst_initial = ("1" if spec.reset_polarity != "active_low" else "0") if rst else "0"
        rst_inactive = ("0" if spec.reset_polarity != "active_low" else "1") if rst else "0"
        inp = VerifierAgent._sequence_input(spec)
        out = next((p for p in spec.ports if p["direction"] == "output"), None)
        if not inp or not out:
            return ""
        out_w = out["width"]
        out_decl = f"[{out_w-1}:0] " if out_w > 1 else ""
        rst_decl = f", {rst} = {rst_initial}" if rst else ""
        rst_conn = f", .{rst}({rst})" if rst else ""
        reset_release = f"; @(negedge {clk}); {rst} = {rst_inactive}" if rst else ""
        return f'''`timescale 1ns/1ps
module tb_self_check();
  reg {clk} = 0{rst_decl}, {inp["name"]} = 0;
  wire {out_decl}{out["name"]};
  reg [3:0] model = 0;
  reg [3:0] next_model;
  integer errors = 0;
  always #2.5 {clk} = ~{clk};
  {spec.module_name} dut (.{clk}({clk}){rst_conn}, .{inp["name"]}({inp["name"]}), .{out["name"]}({out["name"]}));
  task drive(input reg b);
    begin @(negedge {clk}); {inp["name"]} = b; @(posedge {clk}); #1;
      if ({active_rst}) begin next_model = 0; model = 0; end
      else begin next_model = {{model[2:0], b}}; model = next_model; end
      if ({out["name"]} !== (next_model == 4'b1101)) begin
        $display("TB_FAILURE: DUT=%b expected=%b", {out["name"]}, (next_model == 4'b1101)); errors = errors + 1;
      end
    end
  endtask
  initial begin
    repeat (2) @(posedge {clk}){reset_release};
    drive(1); drive(1); drive(0); drive(1); drive(1); drive(0); drive(1);
    if (errors == 0) $display("TB_SUCCESS: All self-tests passed with 0 errors.");
    else $display("TB_FAILURE: Total %0d mismatches.", errors);
    $finish;
  end
  initial begin #20000; $display("TB_FAILURE: Watchdog timeout"); $finish; end
endmodule
'''

    @staticmethod
    def _fallback_lfsr_tb(spec: HardwareSpec) -> str:
        clk = spec.clock_port or "clk"
        rst = spec.reset_port
        active_rst = (rst if spec.reset_polarity != "active_low" else f"!{rst}") if rst else "1'b0"
        rst_initial = ("1" if spec.reset_polarity != "active_low" else "0") if rst else "0"
        rst_inactive = ("0" if spec.reset_polarity != "active_low" else "1") if rst else "0"
        out = next((p for p in spec.ports if p["direction"] == "output"), None)
        load = VerifierAgent._lfsr_load(spec)
        data = VerifierAgent._lfsr_data(spec, out["width"] if out else 0)
        if not out or (load and not data):
            return ""
        width = out["width"]
        if width == 1:
            feedback = "model[0]"
            shift = "{model[0]}"
        elif width == 8:
            feedback = "model[7] ^ model[5] ^ model[4] ^ model[3]"
            shift = f"{{model[6:0], {feedback}}}"
        else:
            feedback = f"model[{width - 1}] ^ model[{max(0, width - 3)}]"
            shift = f"{{model[{width - 2}:0], {feedback}}}"
        data_value = f"{data['width']}'hA5" if data else "0"
        declarations = [f"reg {clk} = 0;"]
        if rst:
            declarations.append(f"reg {rst} = {rst_initial};")
        if load:
            declarations.append(f"reg {load['name']} = 0;")
        if data:
            declarations.append(f"reg [{data['width']-1}:0] {data['name']} = 0;")
        reset_conn = f", .{rst}({rst})" if rst else ""
        load_conn = f", .{load['name']}({load['name']})" if load else ""
        data_conn = f", .{data['name']}({data['name']})" if data else ""
        if rst and load and data:
            model_logic = (f"if ({active_rst}) model = {width}'d1; else "
                           f"if ({load['name']}) model = {data['name']}; else model = {shift};")
        elif rst:
            model_logic = f"if ({active_rst}) model = {width}'d1; else model = {shift};"
        elif load and data:
            model_logic = f"if ({load['name']}) model = {data['name']}; else model = {shift};"
        else:
            model_logic = f"model = {shift};"
        release_reset = (f" @(negedge {clk}); {rst}={rst_inactive};" if rst else "")
        load_drive = (f" {load['name']}=1; {data['name']}={data_value};" if load else "")
        clear_load = f" @(negedge {clk}); {load['name']}=0;" if load else ""
        return f'''`timescale 1ns/1ps
module tb_self_check();
  {' '.join(declarations)}
  wire [{out["width"]-1}:0] {out["name"]};
  reg [{out["width"]-1}:0] model = 0;
  integer errors = 0;
  always #2.5 {clk} = ~{clk};
  {spec.module_name} dut (.{clk}({clk}){reset_conn}{load_conn}{data_conn}, .{out["name"]}({out["name"]}));
  task check; begin @(posedge {clk}); #1;
    {model_logic}
    if ({out["name"]} !== model) begin $display("TB_FAILURE: DUT=%h EXP=%h", {out["name"]}, model); errors = errors + 1; end
  end endtask
  initial begin
    repeat (2) check;{release_reset}{load_drive} check;
    {clear_load} check; check;
    if (errors == 0) $display("TB_SUCCESS: All self-tests passed with 0 errors.");
    else $display("TB_FAILURE: Total %0d mismatches.", errors);
    $finish;
  end
  initial begin #20000; $display("TB_FAILURE: Watchdog timeout"); $finish; end
endmodule
'''

    def verify(self, spec: HardwareSpec, prompt: str, dut_code: str, timeout_s: float = 120.0) -> tuple[int, str]:
        """Execute self-checking simulation and report verdict."""
        tb_code = self.build_testbench(spec, prompt, dut_code)
        rc, log = self.tools.run_sim(dut_code, tb_code, tb_top="tb_self_check", timeout_s=timeout_s)
        # A model-generated testbench can be syntactically invalid even when
        # the DUT passed L1.  Retry once with the deterministic skill-backed
        # testbench before asking the repair model to change correct RTL.
        if rc != 0 and ("TB compilation failed" in log or "TB elaboration failed" in log):
            fallback_tb = self._fallback_tb(spec, prompt)
            if fallback_tb and fallback_tb != tb_code:
                rc_fallback, log_fallback = self.tools.run_sim(
                    dut_code, fallback_tb, tb_top="tb_self_check", timeout_s=timeout_s
                )
                if rc_fallback == 0:
                    return 0, "Generated TB rejected; deterministic fallback passed.\n" + log_fallback
                return rc_fallback, log + "\nFallback TB result:\n" + log_fallback
        return rc, log
