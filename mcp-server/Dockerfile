FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    chromadb \
    ollama \
    mcp \
    rich \
    starlette \
    uvicorn \
    PyJWT \
    python-multipart

COPY mcp-server/mcp_server.py .
COPY indexer/indexer.py .

CMD ["python3", "mcp_server.py"]
