import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent.artifacts import extract_sv, module_contract, preflight, task_contract, tb_shape
from agent.client import Client, ModelError, Reply
from agent.config import Config, canonical_url
from agent.process import Deadline, run
from agent.skills import select
from agent.tools import Result, Toolchain, license_error, verdict
from agent.trace import digest
from agent.workflow import Workflow
from support import BAD, DUT, PROMPT, TB, TraceStub, inference

ROOT = Path(__file__).resolve().parents[1]


class SkillTests(unittest.TestCase):
    def test_long_contract_does_not_starve_task_specific_skill(self):
        text,names=select(ROOT/'skill','popcount signed overflow truncation bit count',max_chars=6000)
        self.assertIn('rtl-module-interface-contract',names)
        self.assertIn('rtl-arithmetic-truncation',names)
        self.assertLessEqual(len(text),6000)


class ArtifactTests(unittest.TestCase):
    def test_official_bullets_without_interface(self):
        spec = task_contract(PROMPT)
        self.assertEqual(spec.top, "TopModule")
        self.assertEqual([(p.name, p.width) for p in spec.ports], [("a", 8), ("y", 8)])
        self.assertTrue(preflight(DUT, spec)[0])

    def test_signed_parameterized_shared_ports(self):
        text = 'module TopModule #(parameter W=8) (input signed [W-1:0] a,b, output signed [W:0] y); endmodule'
        p = module_contract(text).ports
        self.assertEqual([(v.name, v.width, v.signed) for v in p], [("a",8,True),("b",8,True),("y",9,True)])

    def test_explicit_signedness_enforced(self):
        iface = 'module TopModule(input signed [7:0] a, output [7:0] y);'
        self.assertFalse(preflight(DUT, task_contract(PROMPT, iface))[0])

    def test_old_style_ports(self):
        p = module_contract('module T(a,b,y); input [3:0] a,b; output [4:0] y; endmodule').ports
        self.assertEqual([v.width for v in p], [4,4,5])

    def test_missing_port_not_silently_accepted(self):
        self.assertFalse(preflight(DUT.replace('output [7:0] y','output [7:0] q'),task_contract(PROMPT))[0])

    def test_full_helper_retained_tb_removed(self):
        helper='module inv(input a,output y); assign y=~a; endmodule'
        text='```sv\n'+helper+'\n'+DUT+'\n'+TB+'```'
        result=extract_sv(text,'TopModule')
        self.assertIn(helper,result)
        self.assertIn(DUT.strip(),result)
        self.assertNotIn('tb_self_check',result)

    def test_complete_code_after_thinking(self):
        self.assertIn(DUT.strip(),extract_sv('<think>module Bad; endmodule</think>\n'+DUT,'TopModule'))
        self.assertEqual(extract_sv('<think>'+DUT,'TopModule'),'')

    def test_macros_and_global_type_retained(self):
        source='`define WIDTH 8\ntypedef logic [7:0] byte_t;\n'+DUT
        self.assertEqual(extract_sv('```sv\n'+source+'```','TopModule'),source)

    def test_incomplete_module_rejected(self):
        self.assertEqual(extract_sv(DUT.replace('endmodule',''),'TopModule'),'')

    def test_testbench_output_alias_and_instance_name(self):
        self.assertTrue(tb_shape(TB,'TopModule'))
        self.assertTrue(tb_shape(TB.replace('y_dut !== ~a','(~a) !== y_dut'),'TopModule'))
        self.assertTrue(tb_shape(TB.replace('y_dut !== ~a','y_dut[7:0] !== ~a'),'TopModule'))

    def test_testbench_cannot_request_shell_or_external_files(self):
        self.assertFalse(tb_shape(TB.replace('a=i;', 'a=i; $system("env");'),'TopModule'))
        self.assertFalse(tb_shape('`include "/run/secrets/key"\n'+TB,'TopModule'))

    def test_bit_type_and_integer_ports(self):
        p=module_contract('module T(input bit [7:0] a, input integer b, output logic y); endmodule').ports
        self.assertEqual([(v.name,v.width) for v in p],[('a',8),('b',32),('y',1)])

    def test_simulation_only_dut_rejected(self):
        self.assertFalse(preflight(DUT.replace('assign y=~a;','initial $finish;'),task_contract(PROMPT))[0])


class TransportTests(unittest.TestCase):
    def test_common_url_forms(self):
        for v in ['https://example.org','https://example.org/v1/','https://example.org/v1/chat/completions']:
            self.assertEqual(canonical_url(v),'https://example.org/v1')
        with self.assertRaises(ValueError): canonical_url('https://key:secret@example.org/v1')

    def test_standard_chat_bearer_and_content_parts(self):
        with inference([{'type':'text','text':DUT}]) as (url,rows):
            trace=TraceStub()
            client=Client(Config(url,'test-model',api_key='private-key'),Deadline(8),trace)
            self.assertEqual(client.chat('system','question','design').text,DUT)
            payload=json.loads(rows[-1][3])
            self.assertEqual(rows[-1][2]['Authorization'],'Bearer private-key')
            self.assertEqual(payload['model'],'test-model')
            self.assertNotIn('private-key',json.dumps(trace.events))

    def test_sse_chat(self):
        with inference(stream=True) as (url,_):
            reply=Client(Config(url,'test-model',stream=True),Deadline(8),TraceStub()).chat('s','u','design')
            self.assertEqual(reply.text,DUT)

    def test_slow_trickle_has_total_wallclock_timeout(self):
        with inference(slow=True) as (url,_):
            started=time.monotonic()
            with self.assertRaises(ModelError):
                Client(Config(url,'test-model',request_s=0.6),Deadline(5),TraceStub()).chat('s','u','design')
            self.assertLess(time.monotonic()-started,1.8)

    def test_http_401_not_retried(self):
        with inference(status=401) as (url,rows):
            with self.assertRaises(ModelError): Client(Config(url,'test-model'),Deadline(5),TraceStub()).chat('s','u','design')
            self.assertEqual(len(rows),1)

    def test_missing_model_not_silently_mocked(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError): Config.from_env()


class VerdictTests(unittest.TestCase):
    def test_license_success_banner_not_error(self):
        self.assertFalse(license_error('A valid Vivado license has been detected. Releasing license: Vivado_Synthesis'))
        self.assertTrue(license_error('A valid license was not found'))
        self.assertTrue(license_error('FLEXnet Licensing error:-15'))

    def test_failure_precedes_success(self):
        for rc,log in [(3,'TB_RESULT checks=8 errors=0'),(0,'TB_FAILURE mismatch\nTB_RESULT checks=8 errors=0'),(0,'ERROR: crashed\nTB_RESULT checks=8 errors=0')]:
            self.assertFalse(verdict(rc,log).ok)

    def test_zero_missing_duplicate_and_error_verdicts(self):
        for log in ['TB_SUCCESS','TB_RESULT checks=0 errors=0','TB_RESULT checks=8 errors=0\nTB_RESULT checks=8 errors=0','TB_RESULT checks=8 errors=2']:
            self.assertFalse(verdict(0,log).ok)
        self.assertTrue(verdict(0,'TB_RESULT checks=256 errors=0').ok)

    def test_timeout_stops_child_tree(self):
        with tempfile.TemporaryDirectory() as d:
            marker=Path(d)/'leak'
            # The child would write a marker after its parent timed out.
            code='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr('import time; from pathlib import Path; time.sleep(0.7); Path('+repr(str(marker))+').touch()')+']); time.sleep(10)'
            rc,_=run([sys.executable,'-c',code],Path(d),0.2,Path(d)/'log')
            self.assertEqual(rc,124)
            time.sleep(0.8)
            self.assertFalse(marker.exists())


class WorkflowTests(unittest.TestCase):
    def perform(self, replies, lint=None, sim=None, synth=None):
        trace=TraceStub()
        class FakeClient:
            def __init__(self): self.roles=[]; self.users=[]
            def chat(self,system,user,role,**kwargs):
                self.roles.append(role); self.users.append(user)
                return Reply(replies.pop(0))
        class FakeTools:
            available=True
            def lint(self,*a): return lint.pop(0) if lint else Result('ok')
            def simulate(self,*a): return sim.pop(0) if sim else Result('ok')
            def synthesize(self,*a): return synth.pop(0) if synth else Result('ok')
        client=FakeClient()
        with tempfile.TemporaryDirectory() as d:
            workflow=Workflow(Config('http://127.0.0.1:1/v1','test-model'),Path(d),trace,client,FakeTools())
            workflow.solve(PROMPT)
            workflow.finish()
            result=json.loads((Path(d)/'result.json').read_text())
            source=(Path(d)/'solution.v').read_text()
        return client,trace,result,source

    def test_complete_loop_and_independent_reference_prompt(self):
        client,trace,result,source=self.perform([DUT,TB])
        self.assertEqual(result['internal_level'],3)
        self.assertEqual(result['solution_sha256'],digest(source))
        self.assertNotIn(DUT.strip(),client.users[1])

    def test_environment_is_not_l1(self):
        _,_,result,_=self.perform([DUT],lint=[Result('environment')])
        self.assertEqual(result['internal_level'],0)

    def test_tb_compile_failure_repairs_only_tb(self):
        client,_,result,source=self.perform([DUT,TB,TB],sim=[Result('testbench_error','testbench syntax'),Result('ok')])
        self.assertEqual(client.roles,['design','tb_generate','tb_repair'])
        self.assertEqual(source,DUT)

    def test_oracle_unavailable_still_runs_synthesis_without_claiming_l3(self):
        _,_,result,_=self.perform([DUT,'module Wrong; endmodule'])
        self.assertEqual(result['internal_level'],1)
        self.assertTrue(result['synthesis_passed'])

    def test_failed_repair_does_not_replace_compiled_candidate(self):
        _,_,result,source=self.perform([DUT,TB,BAD,BAD],sim=[Result('mismatch','bad'),Result('mismatch','bad')],
                                      lint=[Result('ok'),Result('design_error','broken'),Result('environment')])
        self.assertEqual(source,DUT)
        self.assertEqual(result['internal_level'],1)

    def test_compiled_repair_replaces_same_level_failed_candidate(self):
        _,_,result,source=self.perform([BAD,TB,DUT],sim=[Result('mismatch','bad'),Result('ok')])
        self.assertEqual(source,DUT)
        self.assertEqual(result['internal_level'],3)

    def test_wrong_local_oracle_can_be_repaired_without_dut_change(self):
        client,_,result,source=self.perform([DUT,TB,'TARGET: TESTBENCH\n'+TB],sim=[Result('mismatch','expected was wrong'),Result('ok')])
        self.assertEqual(source,DUT)
        self.assertEqual(result['internal_level'],3)
        self.assertIn('fallible local self-test',__import__('agent.prompts',fromlist=['ADJUDICATE']).ADJUDICATE)


@unittest.skipUnless(os.environ.get('RTL_NATIVE_TESTS') == '1','requires native Vivado (RTL_NATIVE_TESTS=1)')
class NativeTests(unittest.TestCase):
    def test_independent_oracle_detects_wrong_rtl(self):
        with tempfile.TemporaryDirectory() as d:
            tool=Toolchain(Config('http://localhost:1/v1','native'),Path(d),TraceStub())
            self.assertTrue(tool.available,'Native tests requested but Vivado tools are missing')
            self.assertTrue(tool.lint(DUT,'TopModule',60).ok)
            self.assertTrue(tool.simulate(DUT,TB,60).ok)
            self.assertEqual(tool.simulate(BAD,TB,60).kind,'mismatch')

    def test_native_synthesis(self):
        with tempfile.TemporaryDirectory() as d:
            tool=Toolchain(Config('http://localhost:1/v1','native'),Path(d),TraceStub())
            self.assertTrue(tool.available,'Native tests requested but Vivado tools are missing')
            self.assertTrue(tool.synthesize(DUT,task_contract(PROMPT),120).ok)
