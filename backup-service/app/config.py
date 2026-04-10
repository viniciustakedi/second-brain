"""Environment-driven configuration (Mac, Windows, Linux via Docker bind mounts)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BackupConfig:
    vault_path: Path
    snapshot_local_dir: Path
    icloud_backup_dir: Path
    state_db_path: Path
    # Daily snapshot time (local container TZ)
    snapshot_hour: int
    snapshot_minute: int
    retention_local_days: int
    retention_icloud_days: int
    retry_failed_copy_interval_sec: int
    log_level: str
    staging_root: Path

    @classmethod
    def from_env(cls) -> BackupConfig:
        vault = Path(os.environ.get("VAULT_PATH", "/vault")).resolve()
        snap_local = Path(
            os.environ.get("SNAPSHOT_LOCAL_DIR", "/snapshots/local")
        ).resolve()
        icloud = Path(
            os.environ.get("ICLOUD_BACKUP_DIR", "/snapshots/icloud")
        ).resolve()
        state_db = Path(
            os.environ.get("STATE_DB_PATH", "/state/backup_state.sqlite")
        ).resolve()

        time_s = os.environ.get("BACKUP_SNAPSHOT_TIME", "23:59").strip()
        parts = time_s.replace(";", ":").split(":")
        hour = int(parts[0]) if parts else 23
        minute = int(parts[1]) if len(parts) > 1 else 59

        staging = Path(
            os.environ.get("BACKUP_STAGING_DIR", str(snap_local / ".staging"))
        ).resolve()

        return cls(
            vault_path=vault,
            snapshot_local_dir=snap_local,
            icloud_backup_dir=icloud,
            state_db_path=state_db,
            snapshot_hour=max(0, min(23, hour)),
            snapshot_minute=max(0, min(59, minute)),
            retention_local_days=max(1, int(os.environ.get("BACKUP_RETENTION_LOCAL_DAYS", "14"))),
            retention_icloud_days=max(1, int(os.environ.get("BACKUP_RETENTION_ICLOUD_DAYS", "30"))),
            retry_failed_copy_interval_sec=max(
                60, int(os.environ.get("BACKUP_RETRY_COPY_INTERVAL_SEC", "900"))
            ),
            log_level=os.environ.get("BACKUP_LOG_LEVEL", "INFO").upper(),
            staging_root=staging,
        )
