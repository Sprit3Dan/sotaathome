FROM nvidia/cuda:12.8.1-devel-ubuntu22.04

ARG AUTORESEARCH_COMMIT=c2450add72cc80317be1fe8111974b892da10944

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-dev python3.11-venv python3.11-distutils \
    && rm -rf /var/lib/apt/lists/*

# ── uv ────────────────────────────────────────────────────────────────────────
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

# ── Clone autoresearch at pinned SHA ──────────────────────────────────────────
RUN git clone https://github.com/karpathy/autoresearch.git /opt/autoresearch-upstream \
    && git -C /opt/autoresearch-upstream checkout ${AUTORESEARCH_COMMIT}

# ── Install autoresearch deps (big layer: torch 2.9.1 cu128, ~3 GB) ───────────
RUN cd /opt/autoresearch-upstream && uv sync --python python3.11

# ── Extra: datasets (for prepare-tinystories.py) ──────────────────────────────
RUN cd /opt/autoresearch-upstream && uv pip install datasets

ENV PATH="/opt/autoresearch-upstream/.venv/bin:${PATH}"

# ── Our helper script ─────────────────────────────────────────────────────────
COPY docker/prepare-tinystories.py /app/prepare-tinystories.py
COPY docker/patch-train.py /app/patch-train.py

# ── Runtime defaults ───────────────────────────────────────────────────────────
ENV AUTORESEARCH_CACHE_DIR=/artifacts/cache \
    AUTORESEARCH_OUTPUT_DIR=/artifacts/output \
    AUTORESEARCH_RUN_ID=default \
    AUTORESEARCH_MAX_ITERATIONS=3 \
    AUTORESEARCH_NUM_SHARDS=3 \
    DEPTH=4 \
    DEVICE_BATCH_SIZE=8

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
