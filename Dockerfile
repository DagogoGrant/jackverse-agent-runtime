FROM python:3.12-slim-bookworm

WORKDIR /app

# Install Node.js, npm, and official MCP filesystem server for existing-server interoperability
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g @modelcontextprotocol/server-filesystem@2026.8.31 && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency specifications first
COPY pyproject.toml README.md ./

# Install CPU-only PyTorch first to prevent massive CUDA/CUDNN download bloat
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install package dependencies
RUN pip install --no-cache-dir .

# Pre-download and cache pinned Snowflake Arctic-Embed-S model weights
ENV HF_HOME=/root/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('Snowflake/snowflake-arctic-embed-s', revision='e596f507467533e48a2e17c007f0e1dacc837b33')"

# Enforce complete offline execution for neural embedding inference
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

# Copy application source, configuration, scripts, and tests
COPY src/ ./src
COPY config/ ./config
COPY tests/ ./tests
COPY scripts/ ./scripts

# Copy documentation
COPY docs/ ./docs

# Create workspace and sandbox directory
RUN mkdir -p /app/workspace/mcp_external_demo

# Install the harness package
RUN pip install --no-cache-dir --no-deps .

ENV PYTHONPATH=/app/src

# Expose Prometheus metrics endpoint
EXPOSE 9101

CMD ["sleep", "infinity"]
