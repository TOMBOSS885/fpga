"""Independent development judge. Never imports reference answers into agent prompts."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.tools import RtlToolchain, _run

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def fingerprints():
    return {p.relative_to(ROOT).as_posix(): sha(p) for directory in ('agent', 'skill', 'evaluation')
            for p in sorted((ROOT / directory).rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.md', '.sv', '.txt', '.json') and '__pycache__' not in p.parts}

def judge(task, dest, timeout=120, synthesize=True):
    tool = RtlToolchain()
    metadata = json.loads((task / 'task.json').read_text(encoding='utf-8'))
    result = dict(level='L0', coefficient=0.0, status='CODE_ERROR')
    source = dest / 'solution.v'
    if not source.exists() or not source.read_text(encoding='utf-8').strip():
        return dict(result, status='GENERATION_ERROR')
    if not tool.available:
        return dict(result, level=None, coefficient=None, status='ENVIRONMENT_ERROR')
    cwd = dest / 'judge'
    cwd.mkdir(exist_ok=True)
    # Keep native EDA intermediate paths short on Windows. Upload only console
    # text/metrics, never the binary scratch tree. OS cleans temporary storage.
    scratch = Path(tempfile.mkdtemp(prefix='rtl_eval_'))
    result['stage_exit_codes'] = {}
    deadline = time.monotonic() + timeout
    commands = [('xvlog', [tool.xvlog, '--sv', str(source), str(task / metadata['reference_module']), str(task / metadata['testbench'])]),
                ('xelab', [tool.xelab, metadata['tb_top'], '-s', 'external_eval', '-timescale', '1ps/1ps']),
                ('xsim', [tool.xsim, 'external_eval', '-runall'])]
    for stage, command in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return dict(result, status='JUDGE_TIMEOUT', failed_stage=stage)
        rc, log = _run(command, str(scratch), remaining)
        result['stage_exit_codes'][stage] = rc
        (cwd / (stage + '_console.txt')).write_text(log, encoding='utf-8')
        if tool._license_error(log):
            return dict(result, level=None, coefficient=None, status='LICENSE_ERROR', failed_stage=stage)
        if rc != 0 or re.search(r'(?:^|\s)(?:ERROR|FATAL):', log):
            if rc < 0 or rc >= 2147483648:
                return dict(result, level=None, coefficient=None, status='TOOL_CRASH', failed_stage=stage)
            return dict(result, status='JUDGE_TIMEOUT' if rc == 124 else 'CODE_ERROR', failed_stage=stage)
        if stage == 'xelab':
            result.update(level='L1', coefficient=0.2)
        if stage == 'xsim':
            verdicts = re.findall(r'^\s*Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples\s*$', log, re.MULTILINE)
            if len(verdicts) != 1:
                return dict(result, level=None, coefficient=None, status='VERDICT_UNAVAILABLE')
            errors, checks = map(int, verdicts[0])
            if checks <= 0:
                return dict(result, level=None, coefficient=None, status='VERDICT_UNAVAILABLE')
            result.update(mismatches=errors, samples=checks)
            if errors or 'TB_FAILURE' in log or 'TIMEOUT' in log:
                return dict(result, status='FUNCTION_ERROR', failed_stage=stage)
            result.update(level='L2', coefficient=0.7)
    if not synthesize:
        return dict(result, status='OK_SIM')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return dict(result, status='JUDGE_TIMEOUT', failed_stage='synth')
    metrics_path = cwd / 'synth_result.json'
    command = [tool.vivado, '-mode', 'batch', '-nojournal', '-log', 'synth.log', '-source',
               str(ROOT / 'selftest/judge/l3_synth.tcl'), '-tclargs', metadata['part'], str(source),
               metadata['top'], str(metadata['period_ns']), str(metrics_path)]
    rc, log = _run(command, str(scratch), remaining)
    result['stage_exit_codes']['synth'] = rc
    (cwd / 'synth_console.txt').write_text(log, encoding='utf-8')
    if tool._license_error(log):
        return dict(result, level=None, coefficient=None, status='LICENSE_ERROR')
    if rc < 0 or rc >= 2147483648:
        return dict(result, level=None, coefficient=None, status='TOOL_CRASH', failed_stage='synth')
    metrics = json.loads(metrics_path.read_text(encoding='utf-8')) if metrics_path.exists() else {}
    if rc == 0 and metrics.get('status') == 'OK':
        return dict(result, level='L3', coefficient=1.0, status='OK', synthesis=metrics)
    return dict(result, status='SYNTH_ERROR', failed_stage='synth')

def service_json(url):
    with urllib.request.urlopen(url, timeout=15) as reply:
        return json.load(reply)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['public', 'regression'], default='public')
    parser.add_argument('--output', required=True)
    parser.add_argument('--task', action='append')
    parser.add_argument('--mode', action='append', choices=['baseline', 'agent', 'no_skills'])
    parser.add_argument('--timeout', type=int, default=160)
    parser.add_argument('--fixture-health', action='store_true')
    args = parser.parse_args()
    dest = Path(args.output).resolve()
    dest.mkdir(parents=True, exist_ok=False)
    tasks_root = ROOT / ('tasks' if args.suite == 'public' else 'evaluation/dataset')
    tasks = [p for p in sorted(tasks_root.iterdir()) if (p / 'task.json').exists() and (not args.task or p.name in args.task)]
    if not tasks:
        raise RuntimeError('No selected tasks')
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    baseline = ROOT / 'example/baseline.py'
    if baseline.read_bytes() != (ROOT / 'baseline.py').read_bytes():
        raise RuntimeError('Baseline differs from preserved official example')
    report = dict(development_only=True, suite=args.suite, samples_per_mode=1,
        split='public smoke' if args.suite == 'public' else 'known development regression; NOT unseen holdout',
        input_isolation='Only prompt.txt and interface.txt copied to generation input; reference code not sent to model. This is input separation, not OS filesystem sandboxing.',
        source_sha256=fingerprints(), baseline_sha256=sha(baseline), results=[])
    if not args.fixture_health:
        url = env.get('LLM_BASE_URL', 'http://127.0.0.1:11435/v1').rstrip('/')
        model = env.get('LLM_MODEL', 'qwen2.5-coder:7b-instruct-q4_K_M')
        available = service_json(url + '/models')['data']
        if model not in [m['id'] for m in available]:
            raise RuntimeError('Requested model is not available')
        env.update(LLM_BACKEND='openai', LLM_BASE_URL=url, LLM_MODEL=model, MODEL_NAME=model)
        report.update(model=model, base_url=url, config={k:env.get(k) for k in
            ('AGENT_DEADLINE_S','AGENT_MAX_ROUNDS','LLM_TIMEOUT_S','VIVADO_BIN')}, server_models=available)
        try:
            report['ollama_tags'] = service_json(url.rsplit('/v1', 1)[0] + '/api/tags')
            report['ollama_before'] = service_json(url.rsplit('/v1', 1)[0] + '/api/ps')
        except Exception:
            report['server_observation'] = 'Not an Ollama server or optional metadata unavailable'
    started = time.monotonic()
    for task in tasks:
        isolated = dest / task.name / 'input'
        isolated.mkdir(parents=True)
        for name in ('prompt.txt', 'interface.txt'):
            if (task / name).exists():
                (isolated / name).write_bytes((task / name).read_bytes())
        report.setdefault('task_sha256', {})[task.name] = {p.name:sha(p) for p in task.iterdir() if p.is_file()}
        modes = ['positive', 'stuck_zero'] if args.fixture_health else args.mode or ['baseline', 'agent']
        for mode in modes:
            output = dest / task.name / mode
            output.mkdir()
            generation_started = time.monotonic()
            if args.fixture_health:
                if mode == 'positive':
                    source = (task / 'positive_fixture.sv').read_text(encoding='utf-8')
                else:
                    from agent.tools import parse_interface_contract
                    top, ports, _ = parse_interface_contract((task / 'interface.txt').read_text(encoding='utf-8'), '')
                    decls = ','.join('{} {}{}'.format(d, '[{}:0] '.format(w-1) if w>1 else '', n) for d,w,n in ports)
                    body = '\n'.join('assign {}=0;'.format(n) for d,w,n in ports if d == 'output')
                    source = 'module {}({}); {} endmodule'.format(top, decls, body)
                (output / 'solution.v').write_text(source, encoding='utf-8')
                rc, log = 0, 'Independent fixture health check; no model call.'
            else:
                mode_env = env.copy()
                mode_env['AGENT_DISABLE_SKILLS'] = '1' if mode == 'no_skills' else '0'
                command = [sys.executable, str(baseline), str(isolated), str(output), 'rtl'] if mode == 'baseline' else [sys.executable, '-m', 'agent.main', '--input', str(isolated), '--output', str(output)]
                try:
                    process = subprocess.run(command, cwd=str(ROOT), env=mode_env, timeout=args.timeout,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
                    rc, log = process.returncode, process.stdout
                except subprocess.TimeoutExpired:
                    rc, log = 124, 'Generation exceeded development wall-clock timeout'
            generation_elapsed = round(time.monotonic()-generation_started,3)
            (output / 'generation.txt').write_text(log, encoding='utf-8')
            outcome = dict(level='L0', coefficient=0.0, status='GENERATION_TIMEOUT') if rc == 124 else judge(task, output, synthesize=not args.fixture_health)
            trace_path = output / 'trace.jsonl'
            trace = [json.loads(line) for line in trace_path.read_text(encoding='utf-8').splitlines()] if trace_path.exists() else []
            responses = [e for e in trace if e.get('tool') == 'llm']
            outcome.update(task=task.name, mode=mode, generation_rc=rc, generation_elapsed_s=generation_elapsed,
                total_elapsed_s=round(time.monotonic()-generation_started,3), model_calls=len(responses),
                successful_model_calls=sum(e.get('event')=='response' or ('finish' in e and not e.get('empty_content')) for e in responses),
                solution_sha256=sha(output/'solution.v') if (output/'solution.v').exists() else None,
                internal_finish=next((e for e in reversed(trace) if e.get('event')=='finished'), None))
            report['results'].append(outcome)
            (dest/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print('{} / {}: {} {}'.format(task.name, mode, outcome['level'], outcome['status']), flush=True)
    report['elapsed_total_s'] = round(time.monotonic()-started,3)
    report['source_changed_during_run'] = report['source_sha256'] != fingerprints()
    if not args.fixture_health:
        try:
            report['ollama_after'] = service_json(url.rsplit('/v1',1)[0]+'/api/ps')
        except Exception:
            pass
    (dest/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if args.fixture_health:
        return 0 if all(r['level']=='L2' if r['mode']=='positive' else r['status']=='FUNCTION_ERROR' for r in report['results']) else 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
