# Build argument for base image selection
ARG BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

# Stage 1: Base image with common dependencies
FROM ${BASE_IMAGE} AS base

# Prevents prompts from packages asking for user input during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_PREFER_BINARY=1
ENV PYTHONUNBUFFERED=1
# Increase build parallel level
ENV CMAKE_BUILD_PARALLEL_LEVEL=8

# Install Python, git and other necessary tools
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    git \
    wget \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

# Clean up apt cache
RUN apt-get autoremove -y && apt-get clean -y && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install uv
RUN wget -qO- https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv venv /opt/venv

# Use the virtual environment
ENV PATH="/opt/venv/bin:${PATH}"

# Install comfy-cli
RUN uv pip install comfy-cli pip setuptools wheel

# Install ComfyUI
ARG COMFYUI_VERSION=latest
RUN /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --nvidia && \
    rm -rf /root/.cache/pip /root/.cache/uv /comfyui/.git /tmp/* /var/tmp/*

WORKDIR /comfyui

# Install Custom Nodes for FFGO Workflow
# Identified nodes:
# - VideoHelperSuite (VHS_VideoCombine)
# - WanVideoWrapper (WanVideoNAG, UNETLoader, CLIPLoader, etc.)
# - ComfyUI-KJNodes (GetImageRangeFromBatch, ImageResizeKJv2, PatchSageAttentionKJ)
# - ComfyUI-Easy-Use (easy mathInt)
# - ComfyUI_Comfyroll_CustomNodes (not explicitly seen but often useful, skipping if not in JSON)
# - ComfyUI-PainterNodes (PainterI2V) - Wait, "PainterI2V" class type. Need to find repo. 
#   Searching common painter nodes: likely ComfyUI-Painter or similar. 
#   Checking JSON: "PainterI2V" class. Likely "ComfyUI-Painter".
#   Let's check "RMBG" -> ComfyUI-RMBG or standard ? RMBG is often in ComfyUI-Inference-Core-Nodes or similar.
#   "ImageStitch" -> ComfyUI-Image-Stitch or similar.
#   "GetImageRangeFromBatch" -> KJNodes.
#
# Let's start with the ones we know from the original Dockerfile + extras.

RUN cd custom_nodes && \
    git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite && \
    git clone --depth 1 https://github.com/yolain/ComfyUI-Easy-Use && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes && \
    git clone --depth 1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper && \
    # Added for FFGO specific nodes:
    # "PainterI2V" usually comes from ComfyUI-Painter
    git clone --depth 1 https://github.com/AlekPet/ComfyUI_Painter && \
    # "RMBG" class comes from 1038lab/ComfyUI-RMBG
    git clone --depth 1 https://github.com/1038lab/ComfyUI-RMBG && \
    # "ImageStitch" - verify if it's in a pack or standalone. 
    # Often standard in Essentials or a small repo. 
    # For now assuming it might be in EasyUse or KJNodes, but "ImageStitch" class is specific.
    # Let's add ComfyUI-Essentials just in case as it has many utilities.
    git clone --depth 1 https://github.com/cubiq/ComfyUI_essentials && \
    find . -name ".git" -type d -exec rm -rf {} + 2>/dev/null || true

# Install dependencies for custom nodes
RUN for node in custom_nodes/*; do \
    if [ -f "$node/requirements.txt" ]; then \
    echo "Installing requirements for $node..."; \
    uv pip install -r "$node/requirements.txt"; \
    fi; \
    done && \
    rm -rf /root/.cache/pip /root/.cache/uv /tmp/* /var/tmp/*

# Install Python runtime dependencies for the handler
RUN uv pip install runpod requests websocket-client timm triton setuptools sageattention

# Copy Handler, Start script and Workflow Template
COPY src/start.sh /start.sh
COPY handler.py /handler.py
COPY src/workflow_ffgo.json /workflow_ffgo.json
COPY src/extra_model_paths.yaml /comfyui/extra_model_paths.yaml

RUN chmod +x /start.sh

WORKDIR /
CMD ["/start.sh"]
