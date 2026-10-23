"""Rejudge frozen artifacts with current external judge; do not regenerate RTL."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evaluation.run_evaluation import judge,sha

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    args=parser.parse_args()
    run=Path(args.run).resolve()
    destination=run/'rejudge.json'
    if destination.exists():raise RuntimeError('Refusing to overwrite previous rejudgment')
    initial=json.loads((run/'results.json').read_text(encoding='utf-8'))
    report=dict(development_only=True,regenerated=False,
                reason='Audit all frozen public artifacts after shortening Windows EDA scratch paths; retain original outcomes, including failures.',
                judge_sha256=sha(ROOT/'evaluation/run_evaluation.py'),results=[])
    for row in initial['results']:
        source=run/row['task']/row['mode']/'solution.v'
        out=run/row['task']/row['mode']/'rejudge'
        out.mkdir()
        (out/'solution.v').write_bytes(source.read_bytes())
        current=judge(ROOT/'tasks'/row['task'],out)
        current.update(task=row['task'],mode=row['mode'],original=row,
                       unchanged_solution=sha(out/'solution.v')==row['solution_sha256'])
        report['results'].append(current)
        destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(row['task'],row['mode'],current['level'],current['status'],flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
