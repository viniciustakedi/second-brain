#!/usr/bin/env python3
"""
indexer.py — Indexes your Obsidian vault into ChromaDB via HTTP.
Runs once at startup inside Docker.
"""

import os
import sys
import hashlib
import argparse
import re
from pathlib import Path
from datetime import datetime

import chromadb
from chromadb.config import Settings
import ollama
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

console = Console()

VAULT_PATH  = os.getenv("VAULT_PATH", "/vault")
CHROMA_HOST = os.getenv("CHROMA_HOST", "http://chroma:8000")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")


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


def chunk_text(text: str, max_chars: int = 1500) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) <= max_chars:
            current += para + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
            current = para + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:max_chars]]


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
