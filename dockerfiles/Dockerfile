# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_TOOL_BIN_DIR=/usr/local/bin
ENV UV_TOOL_DIR=/opt/uv-tools

WORKDIR /workspace

# System packages
RUN apt-get update && apt-get install -y \
    git \
    curl \
    libc6 \
    && rm -rf /var/lib/apt/lists/*

# Node + Mermaid
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @mermaid-js/mermaid-cli \
    && npm cache clean --force

# Non-root user
RUN groupadd --system --gid 999 sage \
    && useradd --system --gid 999 --uid 999 --create-home sage

# Source layer
COPY . .

# Install the CLI globally so `sage` is on PATH for any user.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv tool install --no-cache .

RUN chown -R sage:sage /workspace
USER sage
ENV PATH="/usr/local/bin:/home/sage/.local/bin:$PATH"

ENTRYPOINT ["sage"]
