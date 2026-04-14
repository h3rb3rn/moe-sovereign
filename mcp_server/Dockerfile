FROM python:3.11-slim

WORKDIR /app

COPY mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/server.py .
COPY graph_rag/ ./graph_rag/

EXPOSE 8003

CMD ["python", "server.py"]
