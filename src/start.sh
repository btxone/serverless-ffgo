#!/usr/bin/env bash
set -e

# ===============================
# Memory allocator
# ===============================
TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1 || true)"
if [ -n "$TCMALLOC" ]; then
  export LD_PRELOAD="$TCMALLOC"
fi

echo "worker-comfyui: Starting FFGO Serverless"

# ===============================
# Logging
# ===============================
: "${COMFY_LOG_LEVEL:=INFO}"
LOG_FILE="/workspace/comfy_start.log"

# ===============================
# Start ComfyUI
# ===============================
echo "worker-comfyui: Launching ComfyUI with extra_model_paths.yaml"

# RunComfyUI in background
python -u /comfyui/main.py \
  --disable-auto-launch \
  --disable-metadata \
  --listen 127.0.0.1 \
  --port 8188 \
  --extra-model-paths-config /comfyui/extra_model_paths.yaml \
  --verbose "${COMFY_LOG_LEVEL}" \
  2>&1 | tee "$LOG_FILE" &

# ===============================
# Wait for ComfyUI API
# ===============================
echo "worker-comfyui: Waiting for ComfyUI API..."
# Loop wait until curl succeeds
for i in {1..120}; do
  if curl -s http://127.0.0.1:8188/ > /dev/null; then
    echo "worker-comfyui: ComfyUI is up"
    break
  fi
  if [ $i -eq 120 ]; then
    echo "worker-comfyui: Timed out waiting for ComfyUI"
    exit 1
  fi
  sleep 1
done

# ===============================
# Start RunPod handler
# ===============================
echo "worker-comfyui: Starting RunPod handler"
python -u /handler.py