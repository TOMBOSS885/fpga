"""Judge regressions without GPU/model calls; native oracle health is separate."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from evaluation import run_evaluation as evaluation

class ExternalJudgeTests(unittest.TestCase):
    def verdict(self, console, simulator_rc=0):
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp) / 'task'
            output = Path(temp) / 'output'
            task.mkdir(); output.mkdir()
            (task/'task.json').write_text(json.dumps(dict(reference_module='ref.sv',testbench='tb.sv',tb_top='tb',top='TopModule',part='xczu3eg-sbva484-1-e',period_ns=5)))
            (output/'solution.v').write_text('module TopModule; endmodule')
            tool = type('ToolStub',(),dict(available=True,xvlog='xvlog',xelab='xelab',xsim='xsim',_license_error=staticmethod(lambda text:False)))()
            with patch.object(evaluation,'RtlToolchain',return_value=tool), patch.object(evaluation,'_run',side_effect=[(0,'compiled'),(0,'elaborated'),(simulator_rc,console)]):
                return evaluation.judge(task,output,synthesize=False)

    def test_single_zero_mismatch_pass(self):
        self.assertEqual(self.verdict('Mismatches: 0 in 12 samples')['level'],'L2')
    def test_mismatch_fails(self):
        self.assertEqual(self.verdict('Mismatches: 1 in 12 samples')['status'],'FUNCTION_ERROR')
    def test_success_marker_alone_does_not_pass(self):
        self.assertEqual(self.verdict('TB_SUCCESS')['status'],'VERDICT_UNAVAILABLE')
    def test_duplicate_verdict_rejected(self):
        self.assertEqual(self.verdict('Mismatches: 0 in 12 samples\nMismatches: 0 in 12 samples')['status'],'VERDICT_UNAVAILABLE')
    def test_zero_samples_rejected(self):
        self.assertEqual(self.verdict('Mismatches: 0 in 0 samples')['status'],'VERDICT_UNAVAILABLE')
    def test_simulator_crash_rejected(self):
        self.assertNotEqual(self.verdict('Mismatches: 0 in 12 samples',9)['level'],'L2')
    def test_failure_marker_overrides_result(self):
        self.assertEqual(self.verdict('TB_FAILURE\nMismatches: 0 in 12 samples')['status'],'FUNCTION_ERROR')

if __name__ == '__main__':
    unittest.main()
