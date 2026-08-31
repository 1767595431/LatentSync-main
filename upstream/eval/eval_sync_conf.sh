#!/bin/bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}:${ROOT}/upstream:${PYTHONPATH:-}"
python -m eval.eval_sync_conf --video_path "video_out.mp4"
