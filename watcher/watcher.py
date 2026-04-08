#!/usr/bin/env python3
"""
watcher.py — Watches /vault for .md changes and auto re-indexes.
Runs as a long-lived container service.
"""

import os
import sys
import time
from pathlib import Path

from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
from rich.console import Console

# Reuse indexer functions
sys.path.insert(0, "/app")
from indexer import get_collection, index_file, VAULT_PATH

console = Console()
DEBOUNCE = 2.0


class VaultHandler(FileSystemEventHandler):
    def __init__(self):
        self._pending: dict[str, float] = {}

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._pending[event.src_path] = time.time()

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._pending[event.src_path] = time.time()

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._remove(event.src_path)

    def on_moved(self, event):
        if not event.is_directory and event.dest_path.endswith(".md"):
            self._remove(event.src_path)
            self._pending[event.dest_path] = time.time()

    def _remove(self, filepath: str):
        try:
            collection = get_collection()
            rel = str(Path(filepath).relative_to(VAULT_PATH))
            ids = collection.get(where={"source": rel})["ids"]
            if ids:
                collection.delete(ids=ids)
                console.print(f"[red]🗑  Removed:[/red] [dim]{rel}[/dim]")
        except Exception as e:
            console.print(f"[red]Error removing: {e}[/red]")

    def flush(self):
        now = time.time()
        ready = [p for p, ts in self._pending.items() if now - ts >= DEBOUNCE]
        for filepath in ready:
            del self._pending[filepath]
            path = Path(filepath)
            if path.exists():
                collection = get_collection()
                if index_file(path, collection, force=True):
                    try:
                        rel = path.relative_to(VAULT_PATH)
                        console.print(f"[green]✓  Indexed:[/green] [dim]{rel}[/dim]")
                    except ValueError:
                        console.print(f"[green]✓  Indexed:[/green] [dim]{path.name}[/dim]")


def main():
    vault = Path(VAULT_PATH)
    if not vault.exists():
        console.print(f"[red]Vault not found: {VAULT_PATH}[/red]")
        sys.exit(1)

    console.print()
    console.print("[bold blue]🧠 Watcher running[/bold blue]")
    console.print(f"[dim]Watching: {VAULT_PATH}[/dim]")
    console.print()

    handler = VaultHandler()
    observer = PollingObserver(timeout=5)
    observer.schedule(handler, str(vault), recursive=True)
    observer.start()

    console.print("[green]Watching for changes...[/green]")

    try:
        while True:
            handler.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
