# syntax=docker/dockerfile:1.7
# MoE Sovereign Orchestrator — multi-stage, non-root, OCI-compliant.
# Single artifact for Docker, Podman (rootless), Kubernetes, OpenShift, LXC.

# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Install into an isolated prefix we can copy into the runtime stage.
RUN pip install --prefix=/install -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

# OCI image labels (Harbor/Quay scanners and k8s tooling look these up).
LABEL org.opencontainers.image.title="moe-sovereign-orchestrator" \
      org.opencontainers.image.description="MoE Sovereign FastAPI/LangGraph orchestrator" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/moe-sovereign/moe-infra" \
      org.opencontainers.image.vendor="MoE Sovereign"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/usr/local/bin:$PATH \
    MOE_PROFILE=team \
    MOE_LOGS_DIR=/app/logs \
    MOE_SKILLS_DIR=/app/skills \
    MOE_CACHE_DIR=/app/cache \
    MOE_EXPERTS_DIR=/app/configs/experts

# Non-root user. UID/GID 1001 is a fixed fallback; OpenShift will inject its
# own namespace-range UID and still work because we do not rely on $HOME.
RUN groupadd --system --gid 1001 moe \
 && useradd  --system --uid 1001 --gid 1001 \
              --home-dir /app --shell /sbin/nologin moe

# Copy pre-built site-packages from the builder stage (no compilers at runtime).
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application source with explicit ownership so readOnlyRootFilesystem
# mounts (emptyDir for logs/cache) remain writable while the rest is immutable.
COPY --chown=1001:0 . /app

# Writable paths must exist before USER switch, then be chgrp'd to GID 0 so
# OpenShift's random-UID + GID-0 convention can write to them.
RUN mkdir -p "$MOE_LOGS_DIR" "$MOE_SKILLS_DIR" "$MOE_CACHE_DIR" \
 && chgrp -R 0 /app \
 && chmod -R g=u /app

USER 1001:0

EXPOSE 8000

# Healthcheck uses stdlib only so it works on read-only rootfs without curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

# Exec form — forwards SIGTERM cleanly during `kubectl delete pod`.
CMD ["python", "-u", "main.py"]
