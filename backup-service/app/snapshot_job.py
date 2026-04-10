"""Full vault snapshot: staging copy → zip → checksum → DB update → optional iCloud copy."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple

from rich.console import Console

from .config import BackupConfig
from .db import BackupDB

console = Console()


def _exclude_patterns_from_env() -> list[str]:
    raw = os.environ.get("BACKUP_EXCLUDE_GLOBS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _should_exclude(rel_posix: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(Path(rel_posix).name, pat):
            return True
    return False


def _copy_vault_tree(
    src: Path,
    dst: Path,
    patterns: list[str],
    log: Callable[[str], None],
) -> Tuple[int, list[str]]:
    """Copy vault into staging. Returns (file_count, relative paths posix)."""
    dst.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    count = 0
    for root, dirnames, filenames in os.walk(src, followlinks=False):
        root_path = Path(root)
        for d in list(dirnames):
            rel = (root_path / d).relative_to(src).as_posix()
            if _should_exclude(rel, patterns):
                dirnames.remove(d)

        for name in filenames:
            fp = root_path / name
            try:
                rel = fp.relative_to(src).as_posix()
            except ValueError:
                continue
            if _should_exclude(rel, patterns):
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(fp, target, follow_symlinks=False)
            except OSError as e:
                log(f"skip copy (error): {rel} — {e}")
                continue
            files.append(rel)
            count += 1
    return count, files


def _write_manifest(
    path: Path,
    *,
    kind: str,
    run_id: str,
    file_count: int,
    patterns: list[str],
    vault_root: str,
) -> None:
    payload = {
        "schema_version": 1,
        "kind": kind,
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "vault_root": vault_root,
        "file_count": file_count,
        "exclude_globs": patterns,
        "notes": "Vectors (Chroma) and Ollama models are not included; re-run full index after restore.",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _zip_directory(staging_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(zip_path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(staging_dir):
            r = Path(root)
            for name in files:
                fp = r / name
                arc = fp.relative_to(staging_dir).as_posix()
                zf.write(fp, arcname=arc)
    os.replace(tmp, zip_path)


def copy_zip_to_icloud(
    cfg: BackupConfig,
    local_zip: Path,
    sidecar: Path,
    zip_name: str,
    log: Callable[[str], None],
) -> Tuple[bool, str]:
    """Copy zip + sha256 sidecar to iCloud backup dir. Returns (ok, error_message)."""
    try:
        cfg.icloud_backup_dir.mkdir(parents=True, exist_ok=True)
        dest_zip = cfg.icloud_backup_dir / zip_name
        dest_tmp = dest_zip.with_suffix(dest_zip.suffix + ".part")
        shutil.copy2(local_zip, dest_tmp)
        os.replace(dest_tmp, dest_zip)
        if sidecar.exists():
            dest_side = cfg.icloud_backup_dir / sidecar.name
            stmp = dest_side.with_suffix(dest_side.suffix + ".part")
            shutil.copy2(sidecar, stmp)
            os.replace(stmp, dest_side)
        return True, ""
    except OSError as e:
        log(f"iCloud copy I/O error: {e}")
        return False, str(e)


def run_full_snapshot(
    cfg: BackupConfig,
    db: BackupDB,
    *,
    do_copy_icloud: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Returns run_id on success (zip built). iCloud copy may fail → status copy_failed with retry later.
    """
    log = log or (lambda m: console.print(f"[dim]{m}[/dim]"))

    if not cfg.vault_path.is_dir():
        log(f"ERROR: vault not found: {cfg.vault_path}")
        return None

    cfg.snapshot_local_dir.mkdir(parents=True, exist_ok=True)
    cfg.icloud_backup_dir.mkdir(parents=True, exist_ok=True)
    cfg.staging_root.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M")
    zip_name = f"brain-full-{ts}.zip"
    local_zip = cfg.snapshot_local_dir / zip_name
    staging = cfg.staging_root / run_id
    vault_copy = staging / "vault"

    patterns = _exclude_patterns_from_env()

    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        log(f"snapshot {run_id}: staging copy from {cfg.vault_path}")
        t0 = time.perf_counter()
        file_count, _paths = _copy_vault_tree(cfg.vault_path, vault_copy, patterns, log)
        _write_manifest(
            staging / "manifest.json",
            kind="full",
            run_id=run_id,
            file_count=file_count,
            patterns=patterns,
            vault_root=str(cfg.vault_path),
        )
        log(f"snapshot {run_id}: copied {file_count} files in {time.perf_counter() - t0:.1f}s")

        db.insert_snapshot_pending(run_id, "full", zip_name, str(local_zip))  # status building

        log(f"snapshot {run_id}: zipping → {local_zip.name}")
        t1 = time.perf_counter()
        _zip_directory(staging, local_zip)
        zip_size = local_zip.stat().st_size
        digest = _sha256_file(local_zip)
        sidecar = local_zip.with_suffix(".zip.sha256")
        sidecar.write_text(f"{digest}  {zip_name}\n", encoding="utf-8")

        db.mark_snapshot_built(run_id, digest, zip_size)
        log(
            f"snapshot {run_id}: zip OK ({zip_size / 1024 / 1024:.2f} MiB) sha256={digest[:16]}… "
            f"in {time.perf_counter() - t1:.1f}s"
        )

        if do_copy_icloud:
            ok, err = copy_zip_to_icloud(cfg, local_zip, sidecar, zip_name, log)
            if ok:
                icloud_zip = cfg.icloud_backup_dir / zip_name
                db.mark_snapshot_copy_ok(run_id, str(icloud_zip))
                log(f"snapshot {run_id}: iCloud copy OK → {icloud_zip}")
            else:
                db.mark_snapshot_copy_failed(run_id, err or "unknown")
                log(f"snapshot {run_id}: iCloud copy FAILED — {err}")
        else:
            db.mark_snapshot_completed_local_only(run_id)
            log(f"snapshot {run_id}: completed (local only, no iCloud copy)")

        return run_id
    except Exception as e:
        rid = run_id or "unknown"
        console.print(f"[red]snapshot {rid}: FATAL {e}[/red]")
        if run_id:
            try:
                db.mark_snapshot_failed(run_id, str(e))
            except Exception:
                pass
        return None
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def retry_failed_copies(
    cfg: BackupConfig,
    db: BackupDB,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    log = log or (lambda m: console.print(f"[dim]{m}[/dim]"))
    for row in db.list_copy_failed():
        zp = Path(row.local_zip_path)
        sidecar = zp.with_suffix(".zip.sha256")
        if not zp.is_file():
            log(f"retry skip: missing file {zp}")
            continue
        ok, err = copy_zip_to_icloud(cfg, zp, sidecar, row.zip_name, log)
        if ok:
            db.mark_snapshot_copy_ok(row.run_id, str(cfg.icloud_backup_dir / row.zip_name))
            log(f"retry OK run_id={row.run_id}")
        else:
            db.mark_snapshot_copy_failed(row.run_id, err or "retry failed")
