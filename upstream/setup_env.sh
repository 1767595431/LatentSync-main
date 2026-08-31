#!/bin/bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

conda create -y -n latentsync python=3.10.13
conda activate latentsync

conda install -y -c conda-forge ffmpeg

pip install -r requirements.txt

sudo apt -y install libgl1

huggingface-cli download ByteDance/LatentSync-1.6 whisper/tiny.pt --local-dir checkpoints
huggingface-cli download ByteDance/LatentSync-1.6 latentsync_unet.pt --local-dir checkpoints
