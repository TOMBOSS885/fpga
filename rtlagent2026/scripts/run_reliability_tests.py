"""Run unit/native checks and save a machine-readable, non-scoring report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import unittest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true', help='also launch real Vivado tests')
    parser.add_argument('--output', default='experiments/reliability_fix/results.json')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ['RTL_NATIVE_TESTS'] = '1' if args.native else '0'
    sys.dont_write_bytecode = True
    observations = []
    class RecordingResult(unittest.TextTestResult):
        def startTest(self, test):
            self.started_at = time.monotonic()
            super().startTest(test)
        def note(self, test, status, detail=''):
            observations.append({'test': test.id(), 'status': status,
                                 'elapsed_s': round(time.monotonic() - self.started_at, 3),
                                 'detail': detail})
        def addSuccess(self, test):
            super().addSuccess(test)
            self.note(test, 'pass')
        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.note(test, 'fail', self._exc_info_to_string(err, test))
        def addError(self, test, err):
            super().addError(test, err)
            self.note(test, 'error', self._exc_info_to_string(err, test))
        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.note(test, 'skip', reason)
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    source_paths = ['agent/tools.py', 'agent/orchestrator.py', 'agent/verifier_agent.py', 'agent/llm.py', 'tests/test_reliability.py', 'tests/test_evaluation.py', 'evaluation/run_evaluation.py', 'scripts/run_reliability_tests.py']
    payload = {'scope': 'Reliability regression only; NOT a model evaluation or official L0-L3 score',
        'upstream_base_commit': '868b44dc1e0ffb0e951394462978e4fe66566eb9',
        'platform': platform.system(), 'python': platform.python_version(),
        'native_requested': args.native, 'tests_run': result.testsRun,
        'passed': sum(o['status'] == 'pass' for o in observations),
        'skipped': len(result.skipped), 'failures': len(result.failures), 'errors': len(result.errors),
        'source_sha256': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in source_paths},
        'tests': observations}
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print('Report:', output)
    native_skips = args.native and any(o['status'] == 'skip' for o in observations)
    return 0 if result.wasSuccessful() and not native_skips else 1

if __name__ == '__main__':
    raise SystemExit(main())
