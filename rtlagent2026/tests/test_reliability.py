"""Run from rtlagent2026: python -m unittest discover -s tests -v.

No model service required. Native tests opt in with RTL_NATIVE_TESTS=1.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import tools, orchestrator
from agent.spec_agent import HardwareSpec
from agent.verifier_agent import VerifierAgent

DUT = 'module Top(input [7:0] a, output [7:0] y); assign y=~a; endmodule'
BAD_DUT = 'module Top(input [7:0] a, output [7:0] y); assign y=0; endmodule'
SPEC = HardwareSpec({'module_name': 'Top', 'ports': [
    {'name': 'a', 'direction': 'input', 'width': 8},
    {'name': 'y', 'direction': 'output', 'width': 8}]})
ORACLE = '''`timescale 1ns/1ps
module tb_self_check;
reg [7:0] a; wire [7:0] y; integer i,errors=0,checks=0;
Top dut(.a(a),.y(y));
initial begin
for(i=0;i<256;i=i+1) begin a=i; #1; checks=checks+1;
if(y !== (~a)) errors=errors+1; end
$display("TB_RESULT checks=%0d errors=%0d",checks,errors);
$finish; end
initial begin #20000; $display("TB_FAILURE: watchdog"); $finish; end
endmodule
'''

def stub_tool():
    tool = tools.RtlToolchain()
    tool.reason = ''
    return tool

class ToolVerdictTests(unittest.TestCase):
    def verdict(self, rc, log):
        with patch.object(tools, '_run', side_effect=[(0, 'compile'), (0, 'elab'), (rc, log)]):
            return stub_tool().run_sim(DUT, ORACLE)[0]

    def test_valid_license_is_not_failure(self):
        for log in ('A valid Vivado Design Suite BASIC license has been detected.',
                    'Releasing license: Vivado_Synthesis', 'INFO: Checking license'):
            self.assertFalse(tools.RtlToolchain._license_error(log))

    def test_explicit_license_failures(self):
        for log in ('A valid license was not found', 'license checkout failed',
                    'Could not obtain the necessary license', 'FLEXnet Licensing error:-15'):
            self.assertTrue(tools.RtlToolchain._license_error(log))

    def test_successful_synthesis_with_license_messages(self):
        with patch.object(tools, '_run', return_value=(0, 'A valid license has been detected\nReleasing license: Vivado_Synthesis\nAGENT_SYNTH_SUCCESS')):
            self.assertEqual(stub_tool().synth(DUT, 'Top')[0], 0)

    def test_nonzero_synthesis_is_not_success(self):
        with patch.object(tools, '_run', return_value=(1, 'AGENT_SYNTH_SUCCESS')):
            self.assertEqual(stub_tool().synth(DUT, 'Top')[0], 1)

    def test_mixed_verdict_rejected(self):
        self.assertEqual(self.verdict(0, 'TB_FAILURE: mismatch\nTB_RESULT checks=1 errors=0'), 1)

    def test_nonzero_exit_rejected(self):
        self.assertEqual(self.verdict(9, 'TB_RESULT checks=1 errors=0'), 1)

    def test_legacy_success_is_not_proof(self):
        self.assertEqual(self.verdict(0, 'TB_SUCCESS'), -3)

    def test_zero_comparisons_not_verified(self):
        self.assertEqual(self.verdict(0, 'TB_RESULT checks=0 errors=0'), -3)

    def test_multiple_results_not_verified(self):
        self.assertEqual(self.verdict(0, 'TB_RESULT checks=1 errors=0\nTB_RESULT checks=2 errors=0'), -3)

    def test_structured_success(self):
        self.assertEqual(self.verdict(0, 'TB_RESULT checks=256 errors=0'), 0)

    def test_structured_failure(self):
        self.assertEqual(self.verdict(0, 'TB_RESULT checks=256 errors=255'), 1)

    def test_compile_license_failure_classified(self):
        with patch.object(tools, '_run', return_value=(1, 'A valid license was not found')):
            self.assertEqual(stub_tool().lint(DUT, 'Top')[0], -2)
            self.assertEqual(stub_tool().run_sim(DUT, ORACLE)[0], -2)

    def test_lint_consumes_one_stage_budget(self):
        with patch.object(tools, '_run', return_value=(0, 'done')) as run, patch.object(tools.time, 'monotonic', side_effect=[100, 104]):
            stub_tool().lint(DUT, 'Top', timeout_s=10)
            self.assertEqual(run.call_args_list[1].args[2], 6)

class VerifierTests(unittest.TestCase):
    def test_reference_generation_does_not_receive_dut_source(self):
        llm = type('LLMStub', (), {'backend': 'openai'})()
        from agent.llm import LLMResult
        with patch.object(llm, 'chat', return_value=LLMResult(ORACLE), create=True) as chat:
            VerifierAgent(llm, None).build_testbench(SPEC, 'invert input', BAD_DUT)
            self.assertNotIn(BAD_DUT, str(chat.call_args))

    def test_zero_comparison_tb_rejected(self):
        tb = 'module tb_self_check; Top dut(); initial begin $display("TB_RESULT checks=1 errors=0"); $finish; end endmodule'
        self.assertFalse(VerifierAgent._is_usable_testbench(tb, SPEC))

    def test_actual_comparison_shape_accepted(self):
        self.assertTrue(VerifierAgent._is_usable_testbench(ORACLE, SPEC))

    def test_generic_fallback_disabled(self):
        llm = type('MockLLM', (), {'backend': 'mock'})()
        self.assertEqual(VerifierAgent(llm, None)._fallback_tb(SPEC, 'Invert input a'), '')

    def test_real_backend_does_not_use_benchmark_fixture(self):
        llm = type('RealLLM', (), {'backend': 'openai'})()
        self.assertEqual(VerifierAgent(llm, None)._fallback_tb(SPEC, 'popcount'), '')

    def test_missing_tb_does_not_call_simulator(self):
        verifier = VerifierAgent(None, None)
        with patch.object(verifier, 'build_testbench', return_value=''):
            self.assertEqual(verifier.verify(SPEC, 'invert', BAD_DUT)[0], -3)

    def test_generation_timeout_does_not_launch_simulator(self):
        verifier = VerifierAgent(None, None)
        with patch.object(verifier, 'build_testbench', return_value=ORACLE), patch('agent.verifier_agent.time.monotonic', side_effect=[100, 112]):
            self.assertEqual(verifier.verify(SPEC, 'invert', DUT, timeout_s=10)[0], -3)

class MilestoneTests(unittest.TestCase):
    def solve(self, lint_rc=1, available=True, sim_rc=0, synth_rc=0):
        with patch.dict(os.environ, {'LLM_BACKEND': 'mock'}), patch.object(orchestrator, 'MAX_ROUNDS', 1):
            subject = orchestrator.MultiAgentOrchestrator(str(Path(__file__).resolve().parents[1] / 'skill'))
            subject.spec_agent.analyze = lambda *args: SPEC
            subject.coder_agent.generate = lambda *args: DUT
            subject.repair_agent.repair = lambda *args, **kwargs: BAD_DUT
            tool = type('FakeTools', (), {'available': available, 'reason': 'test',
                'preflight': lambda *a: (0, 'shape only'),
                'lint': lambda *a, **k: (lint_rc, 'lint'),
                'synth': lambda *a, **k: (synth_rc, 'synth')})()
            subject.tools = tool
            subject.verifier_agent.verify = lambda *a, **k: (sim_rc, 'sim')
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'trace.jsonl'
                trace = orchestrator.TraceLogger(str(path))
                try:
                    code = subject.solve('invert input', '', trace)
                finally:
                    trace.close()
                events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                return code, events[-1]

    def test_failed_lint_never_claims_l1(self):
        self.assertEqual(self.solve(lint_rc=1)[1]['delivered_level'], 0)

    def test_missing_toolchain_never_claims_l1(self):
        self.assertEqual(self.solve(available=False)[1]['delivered_level'], 0)

    def test_lint_environment_failure_stops_without_repair(self):
        code, finish = self.solve(lint_rc=-2)
        self.assertEqual(code, DUT)
        self.assertEqual(finish['delivered_level'], 0)
        self.assertEqual(finish['reason'], 'lint_environment_error')

    def test_unknown_functional_verification_keeps_l1(self):
        self.assertEqual(self.solve(lint_rc=0, sim_rc=-3)[1]['delivered_level'], 1)

    def test_failed_simulation_rolls_back_to_compiled_candidate(self):
        code, finish = self.solve(lint_rc=0, sim_rc=1)
        self.assertEqual(code, DUT)
        self.assertEqual(finish['delivered_level'], 1)

    def test_synthesis_success_is_internal_not_official(self):
        finish = self.solve(lint_rc=0)[1]
        self.assertEqual(finish['delivered_level'], 3)
        self.assertEqual(finish['verification_scope'], 'internal_milestone_not_official_score')

@unittest.skipUnless(os.environ.get('RTL_NATIVE_TESTS') == '1', 'opt in to real Vivado with RTL_NATIVE_TESTS=1')
class NativeVivadoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = tools.RtlToolchain()
        if not cls.tool.available:
            raise unittest.SkipTest(cls.tool.reason)

    def test_correct_inverter(self):
        rc, log = self.tool.run_sim(DUT, ORACLE, timeout_s=120)
        self.assertEqual(rc, 0, log)
        self.assertIn('256', log)

    def test_stuck_zero_mutant(self):
        rc, log = self.tool.run_sim(BAD_DUT, ORACLE, timeout_s=120)
        self.assertEqual(rc, 1, log)
        self.assertIn('255', log)

    def test_valid_native_synthesis(self):
        rc, log = self.tool.synth(DUT, 'Top', timeout_s=180)
        self.assertEqual(rc, 0, log)

if __name__ == '__main__':
    unittest.main()
