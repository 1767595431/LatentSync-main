#!/bin/bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}:${ROOT}/upstream:${PYTHONPATH:-}"
python -m eval.eval_syncnet_acc --config_path "configs/syncnet/syncnet_16_pixel_attn.yaml"
