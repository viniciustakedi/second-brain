"""Filesystem watcher: persist create/modify/delete/move events to SQLite."""

from __future__ import annotations

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from .config import BackupConfig
from .db import BackupDB


class BackupJournalHandler(FileSystemEventHandler):
    def __init__(self, db: BackupDB) -> None:
        super().__init__()
        self._db = db

    def on_created(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._db.log_fs_event("created", event.src_path, None)

    def on_modified(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._db.log_fs_event("modified", event.src_path, None)

    def on_deleted(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._db.log_fs_event("deleted", event.src_path, None)

    def on_moved(self, event):  # noqa: ANN001
        if event.is_directory:
            return
        self._db.log_fs_event("moved", event.src_path, getattr(event, "dest_path", None))


def start_backup_observer(cfg: BackupConfig, db: BackupDB) -> PollingObserver:
    """Long-lived polling observer (works well on Docker Desktop / network mounts)."""
    handler = BackupJournalHandler(db)
    observer = PollingObserver(timeout=float(__import__("os").environ.get("BACKUP_POLL_TIMEOUT", "5")))
    observer.schedule(handler, str(cfg.vault_path), recursive=True)
    observer.start()
    return observer
