#!/usr/bin/env bash
set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${HERE}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE=1
if [ "$#" -eq 2 ]; then
    IN="$1"; OUT="$2"
elif [ "$#" -eq 4 ] && [ "$1" = --input ] && [ "$3" = --output ]; then
    IN="$2"; OUT="$4"
else
    echo 'Usage: run.sh <input_dir> <output_dir>' >&2
    exit 2
fi
mkdir -p "$OUT"
# Create deliverables before Python imports/configuration can fail.
: > "$OUT/solution.v"
: > "$OUT/trace.jsonl"
exec python3 -m agent.main --input "$IN" --output "$OUT"
