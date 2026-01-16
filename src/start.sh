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
# Ensure persistent model paths
# ===============================
echo "worker-comfyui: Preparing /workspace model directories"

mkdir -p /workspace/models/{checkpoints,diffusion_models,text_encoders,clip_vision,vae,loras}

# Optional: quick visibility
echo "worker-comfyui: /workspace/models structure:"
find /workspace/models -maxdepth 2 -type d

# ===============================
# Logging
# ===============================
: "${COMFY_LOG_LEVEL:=DEBUG}"
LOG_FILE="/workspace/comfy_start.log"

# ===============================
# Start ComfyUI
# ===============================
echo "worker-comfyui: Launching ComfyUI with extra_model_paths.yaml"

python -u /comfyui/main.py \
  --disable-auto-launch \
  --disable-metadata \
  --extra-model-paths-config /comfyui/extra_model_paths.yaml \
  --verbose "${COMFY_LOG_LEVEL}" \
  --log-stdout \
  2>&1 | tee "$LOG_FILE" &

# ===============================
# Wait for ComfyUI API
# ===============================
echo "worker-comfyui: Waiting for ComfyUI API..."
for i in {1..60}; do
  if curl -s http://127.0.0.1:8188/ > /dev/null; then
    echo "worker-comfyui: ComfyUI is up"
    break
  fi
  sleep 1
done

# ===============================
# Start RunPod handler
# ===============================
echo "worker-comfyui: Starting RunPod handler"
python -u /handler.py
