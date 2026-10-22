import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.process import run
from serve.health import Probe
from serve.measure_vram import measure
from serve.serve_api import Server, solve_request
from support import DUT, PROMPT, TB, inference

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SHA = '537783e39db22079c33c19af475dbdb760a29ff1656a48c481d434131f84fb51'


def fake_eda(path):
    """Protocol fixture only; it does NOT prove RTL semantics."""
    path.mkdir()
    for name in ('xvlog','xelab','xsim','vivado'):
        source='#!/usr/bin/env python3\n'
        source+='print('+repr('TB_RESULT checks=256 errors=0' if name=='xsim' else 'AGENT_SYNTH_SUCCESS' if name=='vivado' else 'Analysis completed')+')\n'
        target=path/name
        target.write_text(source)
        target.chmod(0o755)


class ServiceTests(unittest.TestCase):
    def test_official_baseline_is_byte_identical(self):
        self.assertEqual(hashlib.sha256((ROOT/'baseline.py').read_bytes()).hexdigest(),BASELINE_SHA)

    def test_baseline_relay_preserves_fixed_request_and_single_call(self):
        with inference() as (url,rows), tempfile.TemporaryDirectory() as d:
            work=Path(d); inp=work/'input'; inp.mkdir(); output=work/'out'
            (inp/'prompt.txt').write_text(PROMPT)
            env=dict(os.environ,LLM_BASE_URL=url,LLM_MODEL='test-model',MODEL_NAME='test-model',
                     LLM_API_KEY='secret-upstream',AGENT_DEADLINE_S='10',PYTHONDONTWRITEBYTECODE='1')
            rc,log=run(['bash',str(ROOT/'run_baseline.sh'),str(inp),str(output)],ROOT,12,work/'log',env=env)
            self.assertEqual(rc,0,log)
            posts=[r for r in rows if r[0]=='POST']
            self.assertEqual(len(posts),1)
            payload=json.loads(posts[0][3])
            self.assertEqual((payload['temperature'],payload['top_p'],payload['max_tokens']),(0.0,1.0,8192))
            self.assertEqual(payload['messages'][1]['content'],PROMPT)
            self.assertEqual(posts[0][2]['Authorization'],'Bearer secret-upstream')
            trace=[json.loads(v) for v in (output/'trace.jsonl').read_text().splitlines()]
            self.assertEqual(trace[0]['script_sha256'],BASELINE_SHA)
            self.assertEqual([e['tool'] for e in trace],['baseline_meta','llm'])
            self.assertEqual((output/'solution.v').read_text(),DUT)

    def test_auth_and_mode_validation(self):
        with patch.dict(os.environ,{'FPGACHINA_TOKEN':'submission-only'}):
            server=Server(('127.0.0.1',0))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(url+'/v1/health')
            self.assertEqual(denied.exception.code,401)
            denied.exception.close()
            req=urllib.request.Request(url+'/v1/solve',data=json.dumps({'task_id':'x','prompt':'x','mode':'hls'}).encode(),
                                       headers={'Authorization':'Bearer submission-only','Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as invalid:
                urllib.request.urlopen(req)
            self.assertEqual(invalid.exception.code,400)
            invalid.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_service_refuses_nonloopback_and_missing_token(self):
        with self.assertRaises(ValueError): Server(('0.0.0.0',0))
        with patch.dict(os.environ,{'FPGACHINA_TOKEN':''}):
            with self.assertRaises(ValueError): Server(('127.0.0.1',0))

    def test_complete_file_contract_over_http_model_and_fake_eda(self):
        def answer(payload):
            return TB if 'independent RTL verification' in payload['messages'][0]['content'] else DUT
        with inference(answer) as (url,rows), tempfile.TemporaryDirectory() as d:
            tools=Path(d)/'tools'; fake_eda(tools)
            with patch.dict(os.environ,dict(LLM_BASE_URL=url,LLM_MODEL='test-model',MODEL_NAME='test-model',VIVADO_BIN=str(tools))):
                with patch.dict(os.environ,{'FPGACHINA_TOKEN':'roundtrip-token'}):
                    server=Server(('127.0.0.1',0))
                    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
                    try:
                        req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/solve',
                            data=json.dumps(dict(task_id='test-1',nonce='nonce-123',prompt=PROMPT,mode='agent',deadline_s=40)).encode(),
                            headers={'Authorization':'Bearer roundtrip-token','Content-Type':'application/json'})
                        with urllib.request.urlopen(req,timeout=10) as response:
                            result=json.load(response)
                    finally:
                        server.shutdown(); server.server_close(); thread.join()
            self.assertEqual(result['task_id'],'test-1')
            self.assertEqual(result['solution'],DUT)
            trace=[json.loads(v) for v in result['trace'].splitlines()]
            self.assertEqual(trace[0]['nonce'],'nonce-123')
            names=[t['tool'] for t in trace]
            self.assertIn('xvlog',names); self.assertIn('xelab',names); self.assertIn('xsim',names); self.assertIn('vivado',names)
            self.assertEqual(trace[-1]['internal_level'],3)
            self.assertEqual(len([r for r in rows if r[0]=='POST']),2)

    def test_health_does_not_claim_ready_without_actual_vram_or_tools(self):
        with inference() as (url,_):
            with patch.dict(os.environ,dict(LLM_BASE_URL=url,LLM_MODEL='test-model',MODEL_NAME='test-model')):
                with patch('serve.health.measure',side_effect=ValueError('not AMD')):
                    result=Probe().check()
            self.assertFalse(result['ready'])
            self.assertFalse(result['vram_measured'])
            self.assertTrue(result['model_responding'])

    def test_short_deadline_returns_checkpoint_after_hanging_tool(self):
        with inference() as (url,_), tempfile.TemporaryDirectory() as d:
            tools=Path(d)/'tools'; fake_eda(tools)
            (tools/'xvlog').write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(15)\n')
            with patch.dict(os.environ,dict(LLM_BASE_URL=url,LLM_MODEL='test-model',MODEL_NAME='test-model',VIVADO_BIN=str(tools))):
                started=time.monotonic()
                result=solve_request(dict(task_id='timeout',prompt=PROMPT,mode='agent',deadline_s=4))
            self.assertLess(time.monotonic()-started,5.2)
            self.assertEqual(result['solution'],DUT)
            trace=[json.loads(v) for v in result['trace'].splitlines()]
            self.assertTrue(any(v.get('tool')=='checkpoint' for v in trace))

    def test_health_ready_with_model_tools_and_fresh_measurement(self):
        with inference() as (url,_), tempfile.TemporaryDirectory() as d:
            tools=Path(d)/'tools'; fake_eda(tools)
            metrics=Path(d)/'vram.json'; metrics.write_text(json.dumps(dict(timestamp=time.time(),vram_gb=12.5)))
            with patch.dict(os.environ,dict(LLM_BASE_URL=url,LLM_MODEL='test-model',MODEL_NAME='test-model',VIVADO_BIN=str(tools),VRAM_METRICS_FILE=str(metrics))):
                result=Probe().check()
            self.assertTrue(result['ready'])
            self.assertEqual(result['vram_gb'],12.5)

    def test_model_host_sysfs_memory_measurement(self):
        with tempfile.TemporaryDirectory() as d:
            gpu=Path(d)/'card0/device'; gpu.mkdir(parents=True)
            (gpu/'vendor').write_text('0x1002')
            (gpu/'mem_info_vram_used').write_text(str(12*1024**3))
            (gpu/'mem_info_vram_total').write_text(str(32*1024**3))
            self.assertEqual(measure(Path(d))['vram_gb'],12.0)

    def test_model_failure_leaves_empty_artifact_and_valid_trace(self):
        with inference(status=401) as (url,_), tempfile.TemporaryDirectory() as d:
            work=Path(d); inp=work/'input'; inp.mkdir(); output=work/'out'; (inp/'prompt.txt').write_text(PROMPT)
            env=dict(os.environ,LLM_BASE_URL=url,LLM_MODEL='test-model',MODEL_NAME='test-model')
            rc,_=run(['bash',str(ROOT/'run.sh'),str(inp),str(output)],ROOT,5,work/'log',env=env)
            self.assertEqual(rc,0)
            self.assertEqual((output/'solution.v').read_text(),'')
            trace=[json.loads(v) for v in (output/'trace.jsonl').read_text().splitlines()]
            self.assertEqual(trace[-1]['internal_level'],0)
            self.assertEqual(trace[-1]['reason'],'model_unavailable')
