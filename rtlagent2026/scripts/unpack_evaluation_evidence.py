"""Restore packed text evidence. Never overwrite differing existing files."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',default='experiments/evaluation_bundle_20261006/evidence.jsonl')
    args=parser.parse_args()
    bundle=ROOT/args.bundle
    records=[json.loads(line) for line in bundle.read_text(encoding='utf-8').splitlines()]
    allowed=(ROOT/'experiments').resolve()
    checked=[]
    for record in records:
        target=(ROOT/record['path']).resolve()
        if allowed not in target.parents:
            raise ValueError('Evidence path escapes experiments directory')
        data=record['text'].encode('utf-8')
        if hashlib.sha256(data).hexdigest()!=record['sha256']:
            raise ValueError('Evidence digest mismatch: '+record['path'])
        if target.exists() and target.read_bytes()!=data:
            raise ValueError('Refusing to overwrite changed evidence: '+record['path'])
        checked.append((target,data))
    for target,data in checked:
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():target.write_bytes(data)
    print('Verified/restored',len(checked),'text evidence files')

if __name__=='__main__':main()
