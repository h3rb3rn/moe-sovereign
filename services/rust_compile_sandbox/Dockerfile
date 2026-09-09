FROM rust:1-slim@sha256:cc0448b41c3b7b7fea44f5dc50eacba729a56db365b65b7bd5e8a82d5b3db078

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 1002 --gid 0 --home-dir /app --shell /usr/sbin/nologin compilecheck

WORKDIR /app

COPY services/rust_compile_sandbox/requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY services/rust_compile_sandbox/app.py .

RUN mkdir -p /tmp/rust-compile-sandbox && chown -R compilecheck:0 /tmp/rust-compile-sandbox /app \
 && chmod -R g=u /app /tmp/rust-compile-sandbox

USER 1002:0

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
