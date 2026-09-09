FROM rust:1-slim@sha256:cc0448b41c3b7b7fea44f5dc50eacba729a56db365b65b7bd5e8a82d5b3db078

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 1003 --gid 0 --home-dir /app --shell /usr/sbin/nologin loomcheck

WORKDIR /app

COPY services/rust_loom_sandbox/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Vendor the pinned `loom` dependency tree at build time, while this stage
# still has network access -- the runtime container's network is
# egress-blocked (rust_loom_internal, internal: true), so a request-time
# `cargo test` must never need to reach crates.io. `cargo build --release`
# both fetches and compiles loom once here; every real request only
# recompiles the tiny user-submitted src/lib.rs against this cached build.
COPY services/rust_loom_sandbox/scaffold /app/scaffold
RUN cd /app/scaffold && cargo build --release

COPY services/rust_loom_sandbox/app.py .

RUN chown -R loomcheck:0 /app \
 && chmod -R g=u /app

USER 1003:0

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
