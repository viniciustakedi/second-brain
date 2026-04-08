FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    chromadb \
    ollama \
    watchdog \
    rich

COPY watcher.py .
COPY indexer.py .

CMD ["python3", "watcher.py"]
