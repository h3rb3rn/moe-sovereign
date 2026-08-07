FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

WORKDIR /app

# System libs for WeasyPrint (HTML→PDF rendering)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-xlib-2.0-0 \
    libffi8 libglib2.0-0 fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY mcp_server/requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt \
 && python -m pip check

COPY mcp_server/server.py .
COPY mcp_server/pm_connector.py .
COPY graph_rag/ ./graph_rag/

EXPOSE 8003

CMD ["python", "server.py"]
