"""Remove old snapshot files (local / iCloud) while keeping the newest completed backup safe."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from .config import BackupConfig
from .db import BackupDB, SnapshotRow


def _newest_completed(rows: list[SnapshotRow]) -> Optional[SnapshotRow]:
    if not rows:
        return None
    return max(rows, key=lambda r: r.finished_ts or r.started_ts)


def _age_sec(row: SnapshotRow) -> float:
    ref = row.finished_ts or row.started_ts
    return time.time() - ref


def apply_retention(
    cfg: BackupConfig,
    db: BackupDB,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Delete old local and iCloud artifacts. The single newest 'completed' snapshot is never touched.
    """
    log = log or (lambda m: None)
    rows = db.list_completed_for_retention()
    if len(rows) <= 1:
        return

    protect = _newest_completed(rows)
    if not protect:
        return

    local_ttl = cfg.retention_local_days * 86400
    icloud_ttl = cfg.retention_icloud_days * 86400

    for row in rows:
        if row.id == protect.id:
            continue
        age = _age_sec(row)
        local_path = Path(row.local_zip_path)
        side_local = local_path.with_suffix(".zip.sha256")

        if age >= local_ttl and local_path.is_file():
            try:
                local_path.unlink()
                log(f"retention: removed local {local_path.name}")
            except OSError as e:
                log(f"retention: local unlink failed {local_path}: {e}")
        if age >= local_ttl and side_local.is_file():
            try:
                side_local.unlink()
            except OSError:
                pass

        if row.icloud_zip_path and age >= icloud_ttl:
            iz = Path(row.icloud_zip_path)
            side_icloud = iz.with_suffix(".zip.sha256") if iz.suffix == ".zip" else Path(f"{iz}.sha256")
            if iz.is_file():
                try:
                    iz.unlink()
                    log(f"retention: removed iCloud mirror {iz.name}")
                except OSError as e:
                    log(f"retention: iCloud unlink failed {iz}: {e}")
            if side_icloud.is_file():
                try:
                    side_icloud.unlink()
                except OSError:
                    pass

        local_gone = not local_path.is_file()
        icloud_gone = not row.icloud_zip_path or not Path(row.icloud_zip_path).is_file()
        if local_gone and icloud_gone:
            db.delete_snapshot_row(row.id)
            log(f"retention: dropped DB row id={row.id} run_id={row.run_id}")
