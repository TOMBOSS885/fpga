#!/usr/bin/env bash
# Start the local inference service.
#
# This is the configuration that must be in effect at evaluation time: local
# open weights, single card, offline. The commercial-API path in agent/llm.py is
# for development only -- the sandbox has no network.
#
#   ./serve/vllm.sh
#   export LLM_BACKEND=openai LLM_BASE_URL=http://localhost:8000/v1 LLM_MODEL=$MODEL
#
# Every value below is a placeholder. Choosing them -- and being able to explain
# why -- is part of what the track scores. Whatever you settle on must match
# model/MODEL.md and section 3 of REPORT.md exactly, including the context
# configuration, because run_baseline.sh has to run against the same service.

set -euo pipefail

MODEL="${MODEL:-/models/Qwen3-8B}"     # local path, not a Hub id: the sandbox is offline
PORT="${PORT:-8000}"
MAX_LEN="${MAX_LEN:-32768}"            # declare this in MODEL.md, it is not a free parameter
GPU_UTIL="${GPU_UTIL:-0.90}"

# Single card. Tensor / pipeline parallelism across cards is not usable on
# gfx1201 (no XGMI, collective comms will not start reliably), and the memory
# budget is per-card anyway. Four cards means four independent instances.
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"

exec python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --served-model-name "$(basename "$MODEL")" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --max-model-len "$MAX_LEN" \
    --gpu-memory-utilization "$GPU_UTIL" \
    --disable-log-requests
