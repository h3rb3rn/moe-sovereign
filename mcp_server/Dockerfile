FROM python:3.11-slim

WORKDIR /app

# System libs for WeasyPrint (HTML→PDF rendering)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-xlib-2.0-0 \
    libffi8 libglib2.0-0 fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/server.py .
COPY graph_rag/ ./graph_rag/

EXPOSE 8003

CMD ["python", "server.py"]
