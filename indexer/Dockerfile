FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    chromadb \
    ollama \
    rich \
    httpx

COPY indexer.py .
COPY search.py .

CMD ["python3", "indexer.py", "--full"]
