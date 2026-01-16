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

# Install Python, git, build tools and other necessary tools
# AÑADIDO: curl para el healthcheck del start.sh
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    build-essential \
    git \
    wget \
    curl \
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
RUN cd custom_nodes && \
    git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite && \
    git clone --depth 1 https://github.com/yolain/ComfyUI-Easy-Use && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes && \
    git clone --depth 1 https://github.com/Fannovel16/ComfyUI-Frame-Interpolation && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper && \
    git clone --depth 1 https://github.com/princepainter/ComfyUI-PainterI2V && \
    git clone --depth 1 https://github.com/1038lab/ComfyUI-RMBG && \
    git clone --depth 1 https://github.com/cubiq/ComfyUI_essentials && \
    find . -name ".git" -type d -exec rm -rf {} + 2>/dev/null || true

# Pre-install Python 3.12 compatible numba/llvmlite
RUN uv pip install "numba>=0.59.0"
RUN echo "numba>=0.59.0" > /tmp/overrides.txt

# Install dependencies for custom nodes
RUN for node in custom_nodes/*; do \
    if [ -f "$node/requirements.txt" ]; then \
    echo "Installing requirements for $node..."; \
    uv pip install -r "$node/requirements.txt" --override /tmp/overrides.txt || echo "Warning: some deps failed for $node"; \
    fi; \
    done && \
    rm -rf /root/.cache/pip /root/.cache/uv /var/tmp/*

# AÑADIDO: Dependencias críticas faltantes (ONNX para Wan, SAM2 para RMBG)
RUN uv pip install \
    runpod \
    requests \
    websocket-client \
    setuptools \
    timm \
    triton \
    onnx \
    onnxruntime-gpu \
    sageattention \
    "huggingface-hub>=0.19.0" \
    "transparent-background>=1.1.2" \
    "opencv-python>=4.7.0" \
    "protobuf>=3.20.2,<6.0.0" \
    "hydra-core>=1.3.0" \
    "omegaconf>=2.3.0" \
    "iopath>=0.1.9" \
    "rembg[gpu]"

# Install SAM2 from git (Fix for 'No module named sam2')
RUN uv pip install git+https://github.com/facebookresearch/sam2.git

# Copy Handler, Start script and Workflow Template
COPY src/start.sh /start.sh
COPY handler.py /handler.py
COPY ffgo_workflow_v2_api.json /test_input.json
COPY src/extra_model_paths.yaml /comfyui/extra_model_paths.yaml

RUN chmod +x /start.sh

WORKDIR /
CMD ["/start.sh"]