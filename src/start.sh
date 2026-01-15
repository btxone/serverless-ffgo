#!/usr/bin/env bash

# Use libtcmalloc for better memory management
TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1)"
export LD_PRELOAD="${TCMALLOC}"

echo "worker-comfyui: Starting ComfyUI"

# Allow operators to tweak verbosity; default is DEBUG.
: "${COMFY_LOG_LEVEL:=DEBUG}"

# Serve the API and don't shutdown the container
if [ "$SERVE_API_LOCALLY" == "true" ]; then
    python -u /comfyui/main.py --disable-auto-launch --disable-metadata --listen --verbose "${COMFY_LOG_LEVEL}" --log-stdout &

    echo "worker-comfyui: Starting RunPod Handler"
    python -u /handler.py --rp_serve_api --rp_api_host=0.0.0.0
else
    echo "========= CUSTOM NODE LOAD LOG ========="
    python -u /comfyui/main.py \
      --disable-auto-launch \
      --disable-metadata \
      --verbose DEBUG \
      --log-stdout \
      2>&1 | tee /workspace/comfy_start.log &

    echo "worker-comfyui: Starting RunPod Handler"
    python -u /handler.py
fi