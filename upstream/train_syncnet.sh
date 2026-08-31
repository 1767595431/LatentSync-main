#!/bin/bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}:${ROOT}/upstream:${PYTHONPATH:-}"

torchrun --nnodes=1 --nproc_per_node=1 --master_port=25678 -m scripts.train_syncnet \
    --config_path "configs/syncnet/syncnet_16_pixel_attn.yaml"
