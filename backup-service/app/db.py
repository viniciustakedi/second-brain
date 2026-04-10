"""SQLite persistence: filesystem events and snapshot lifecycle."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Iterable, Optional


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS fs_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    src_path TEXT NOT NULL,
    dest_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_fs_events_ts ON fs_events(ts);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    zip_name TEXT NOT NULL,
    local_zip_path TEXT NOT NULL,
    icloud_zip_path TEXT,
    status TEXT NOT NULL,
    zip_sha256 TEXT,
    bytes_size INTEGER,
    started_ts REAL NOT NULL,
    finished_ts REAL,
    error_message TEXT,
    copy_attempts INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snapshots_status ON snapshots(status);
CREATE INDEX IF NOT EXISTS idx_snapshots_started ON snapshots(started_ts);
"""


@dataclass
class SnapshotRow:
    id: int
    run_id: str
    kind: str
    zip_name: str
    local_zip_path: str
    icloud_zip_path: Optional[str]
    status: str
    zip_sha256: Optional[str]
    bytes_size: Optional[int]
    started_ts: float
    finished_ts: Optional[float]
    error_message: Optional[str]
    copy_attempts: int


class BackupDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._path, timeout=60)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def log_fs_event(
        self,
        event_type: str,
        src_path: str,
        dest_path: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO fs_events (ts, event_type, src_path, dest_path) VALUES (?,?,?,?)",
                (time.time(), event_type, src_path, dest_path),
            )

    def insert_snapshot_pending(
        self,
        run_id: str,
        kind: str,
        zip_name: str,
        local_zip_path: str,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshots (
                    run_id, kind, zip_name, local_zip_path, status, started_ts, copy_attempts
                ) VALUES (?,?,?,?, 'building', ?, 0)
                """,
                (run_id, kind, zip_name, local_zip_path, time.time()),
            )
            return int(cur.lastrowid)

    def mark_snapshot_built(
        self,
        run_id: str,
        zip_sha256: str,
        bytes_size: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET status = 'pending_copy', zip_sha256 = ?, bytes_size = ?, finished_ts = ?
                WHERE run_id = ?
                """,
                (zip_sha256, bytes_size, time.time(), run_id),
            )

    def mark_snapshot_failed(self, run_id: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET status = 'failed', error_message = ?, finished_ts = ?
                WHERE run_id = ?
                """,
                (message[:4000], time.time(), run_id),
            )

    def mark_snapshot_copy_ok(self, run_id: str, icloud_zip_path: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET status = 'completed', icloud_zip_path = ?, finished_ts = ?, copy_attempts = copy_attempts + 1
                WHERE run_id = ?
                """,
                (icloud_zip_path, time.time(), run_id),
            )

    def mark_snapshot_completed_local_only(self, run_id: str) -> None:
        """Mark completed when no remote copy is requested (e.g. snapshot-once --no-icloud)."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET status = 'completed', icloud_zip_path = NULL, finished_ts = ?
                WHERE run_id = ?
                """,
                (time.time(), run_id),
            )

    def mark_snapshot_copy_failed(self, run_id: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET status = 'copy_failed', error_message = ?, copy_attempts = copy_attempts + 1, finished_ts = ?
                WHERE run_id = ?
                """,
                (message[:4000], time.time(), run_id),
            )

    def list_copy_failed(self) -> list[SnapshotRow]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE status = 'copy_failed' ORDER BY started_ts ASC"
            ).fetchall()
            return [self._row_to_snapshot(r) for r in rows]

    def get_last_completed(self) -> Optional[SnapshotRow]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE status = 'completed'
                ORDER BY finished_ts DESC LIMIT 1
                """
            ).fetchone()
            return self._row_to_snapshot(row) if row else None

    def list_completed_for_retention(self) -> list[SnapshotRow]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE status = 'completed' ORDER BY finished_ts ASC"
            ).fetchall()
            return [self._row_to_snapshot(r) for r in rows]

    def delete_snapshot_row(self, snap_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM snapshots WHERE id = ?", (snap_id,))

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> SnapshotRow:
        return SnapshotRow(
            id=row["id"],
            run_id=row["run_id"],
            kind=row["kind"],
            zip_name=row["zip_name"],
            local_zip_path=row["local_zip_path"],
            icloud_zip_path=row["icloud_zip_path"],
            status=row["status"],
            zip_sha256=row["zip_sha256"],
            bytes_size=row["bytes_size"],
            started_ts=row["started_ts"],
            finished_ts=row["finished_ts"],
            error_message=row["error_message"],
            copy_attempts=row["copy_attempts"],
        )
