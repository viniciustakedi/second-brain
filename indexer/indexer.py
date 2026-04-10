#!/usr/bin/env python3
"""
indexer.py — Indexes your Obsidian vault into ChromaDB via HTTP.
Run on demand: `make index` or `docker compose run --rm indexer` (not on every `compose up`).
"""

import os
import sys
import hashlib
import argparse
import re
from pathlib import Path
from datetime import datetime

import time
import httpx
import chromadb
from chromadb.config import Settings
import ollama
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

console = Console()

VAULT_PATH  = os.getenv("VAULT_PATH", "/vault")
CHROMA_HOST = os.getenv("CHROMA_HOST", "http://chroma:8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text-v2-moe")


def wait_for_services(retries: int = 20, delay: float = 3.0):
    # Use chromadb client directly — works across API versions
    for attempt in range(1, retries + 1):
        try:
            client = chromadb.HttpClient(
                host=CHROMA_HOST.replace("http://", "").split(":")[0],
                port=int(CHROMA_HOST.split(":")[-1]),
                settings=Settings(anonymized_telemetry=False)
            )
            client.heartbeat()
            console.print("[dim]✓ Chroma ready[/dim]")
            break
        except Exception:
            if attempt == retries:
                console.print(f"[red]Chroma not reachable after {retries} attempts. Exiting.[/red]")
                sys.exit(1)
            console.print(f"[dim]Waiting for Chroma... ({attempt}/{retries})[/dim]")
            time.sleep(delay)

    for attempt in range(1, retries + 1):
        try:
            httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5).raise_for_status()
            console.print("[dim]✓ Ollama ready[/dim]")
            break
        except Exception:
            if attempt == retries:
                console.print(f"[red]Ollama not reachable after {retries} attempts. Exiting.[/red]")
                sys.exit(1)
            console.print(f"[dim]Waiting for Ollama... ({attempt}/{retries})[/dim]")
            time.sleep(delay)


def get_collection():
    client = chromadb.HttpClient(
        host=CHROMA_HOST.replace("http://", "").split(":")[0],
        port=int(CHROMA_HOST.split(":")[-1]),
        settings=Settings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection(
        name="second_brain",
        metadata={"hnsw:space": "cosine"}
    )


def file_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


def embed_text(text: str) -> list[float]:
    client = ollama.Client(host=OLLAMA_HOST)
    response = client.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]


def parse_frontmatter(content: str) -> tuple[dict, str]:
    meta = {}
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            for line in content[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            return meta, content[end+3:].strip()
    return meta, content


def extract_tags(content: str) -> list[str]:
    return re.findall(r'#([a-zA-Z][a-zA-Z0-9_/-]*)', content)


def chunk_text(text: str, max_chars: int = 600, hard_limit: int = 400) -> list[str]:
    # Level 1: split by paragraphs, accumulate up to max_chars
    para_chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 <= max_chars:
            current += para + "\n\n"
        else:
            if current:
                para_chunks.append(current.strip())
            current = para + "\n\n"
    if current.strip():
        para_chunks.append(current.strip())
    if not para_chunks:
        para_chunks = [text]

    # Level 2: chunks still over hard_limit get split at sentence boundaries
    final_chunks: list[str] = []
    for chunk in para_chunks:
        if len(chunk) <= hard_limit:
            final_chunks.append(chunk)
            continue
        parts = re.split(r'(?<=[.!?]) |\n', chunk)
        current = ""
        for part in parts:
            if not part:
                continue
            candidate = (current + " " + part).lstrip() if current else part
            if len(candidate) <= hard_limit:
                current = candidate
            else:
                if current:
                    final_chunks.append(current.strip())
                # Level 3: hard split if a single part exceeds hard_limit
                while len(part) > hard_limit:
                    final_chunks.append(part[:hard_limit])
                    part = part[hard_limit:]
                current = part
        if current.strip():
            final_chunks.append(current.strip())

    return final_chunks or [text[:hard_limit]]


def index_file(filepath: Path, collection, force: bool = False) -> bool:
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            return False

        content_hash = file_hash(content)
        rel_path = str(filepath.relative_to(VAULT_PATH))

        if not force:
            existing = collection.get(where={"source": rel_path}, include=["metadatas"])
            if existing["ids"] and existing["metadatas"][0].get("hash") == content_hash:
                return False

        existing_ids = collection.get(where={"source": rel_path})["ids"]
        if existing_ids:
            collection.delete(ids=existing_ids)

        _, body = parse_frontmatter(content)
        tags = extract_tags(content)
        chunks = chunk_text(body)

        for i, chunk in enumerate(chunks):
            collection.add(
                ids=[f"{rel_path}::chunk_{i}"],
                embeddings=[embed_text(chunk)],
                documents=[chunk],
                metadatas=[{
                    "source":     rel_path,
                    "title":      filepath.stem,
                    "hash":       content_hash,
                    "tags":       ",".join(tags),
                    "chunk":      i,
                    "total":      len(chunks),
                    "folder":     str(filepath.parent.relative_to(VAULT_PATH)),
                    "indexed_at": datetime.now().isoformat(),
                }]
            )
        return True
    except Exception as e:
        console.print(f"[red]Error indexing {filepath.name}: {e}[/red]")
        return False


def run_index(full: bool = False):
    console.print()
    console.print("[bold blue]🧠 Indexer starting...[/bold blue]")
    console.print(f"[dim]Vault:  {VAULT_PATH}[/dim]")
    console.print(f"[dim]Chroma: {CHROMA_HOST}[/dim]")
    console.print(f"[dim]Ollama: {OLLAMA_HOST}[/dim]")
    console.print()

    wait_for_services()

    vault = Path(VAULT_PATH)
    if not vault.exists():
        console.print(f"[red]Vault not found at {VAULT_PATH}[/red]")
        sys.exit(1)

    collection = get_collection()
    files = list(vault.rglob("*.md"))

    if not files:
        console.print("[yellow]No markdown files found.[/yellow]")
        return

    indexed = skipped = 0

    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), TextColumn("{task.completed}/{task.total}"),
                  console=console) as progress:
        task = progress.add_task("Indexing...", total=len(files))
        for f in files:
            progress.update(task, description=f"[dim]{f.stem[:45]}[/dim]")
            if index_file(f, collection, force=full):
                indexed += 1
            else:
                skipped += 1
            progress.advance(task)

    console.print()
    console.print(f"[green]✓ Indexed:[/green] {indexed}  [dim]Skipped: {skipped}  Total chunks: {collection.count()}[/dim]")
    console.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    run_index(full=args.full)
