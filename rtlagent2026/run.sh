#!/usr/bin/env bash
# RTL Agent Execution Entry Point
# Contract: run.sh <input_dir> <output_dir> OR run.sh --input <input_dir> --output <output_dir>
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${HERE}:${PYTHONPATH:-}"

IN=""
OUT=""

if [ "$#" -ge 2 ] && [ "${1:0:1}" != "-" ]; then
    IN="$1"
    OUT="$2"
else
    while [ $# -gt 0 ]; do
        case "$1" in
            --input)  IN="$2"; shift 2 ;;
            --output) OUT="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
fi

if [ -z "$IN" ] || [ -z "$OUT" ]; then
    echo "Usage: run.sh <input_dir> <output_dir>" >&2
    exit 1
fi

mkdir -p "$OUT"

# Execute agent
python3 -m agent.main --input "$IN" --output "$OUT"
rc=$?

# Safety net: ensure solution.v and trace.jsonl exist
[ -f "$OUT/solution.v" ] || : > "$OUT/solution.v"
[ -f "$OUT/trace.jsonl" ] || : > "$OUT/trace.jsonl"

if [ $rc -ne 0 ]; then
    echo "run.sh: agent returned $rc, generated safety fallback" >&2
fi

exit 0
