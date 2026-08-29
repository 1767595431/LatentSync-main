#!/usr/bin/env bash
set -euo pipefail
cd /root/AI/wav2lip/LatentSync-main
export PYTHONPATH="/root/AI/wav2lip/LatentSync-main:${PYTHONPATH:-}"
exec /usr/local/miniconda3/envs/latentsync/bin/python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8765
