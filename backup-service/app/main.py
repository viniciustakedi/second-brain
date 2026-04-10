"""Run backup watcher + scheduled full snapshot + retry + retention."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from rich.logging import RichHandler

from .config import BackupConfig
from .db import BackupDB
from .fs_watcher import start_backup_observer
from .retention import apply_retention
from .snapshot_job import retry_failed_copies, run_full_snapshot


def setup_logging(level: str) -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    h = RichHandler(rich_tracebacks=True, show_path=False)
    h.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(h)
    return logging.getLogger("brain-backup")


def serve() -> None:
    cfg = BackupConfig.from_env()
    log = setup_logging(cfg.log_level)
    db = BackupDB(cfg.state_db_path)
    db.init()

    log.info(
        "brain-backup serve | vault=%s | snapshots=%s | icloud=%s | db=%s | daily=%02d:%02d TZ=%s",
        cfg.vault_path,
        cfg.snapshot_local_dir,
        cfg.icloud_backup_dir,
        cfg.state_db_path,
        cfg.snapshot_hour,
        cfg.snapshot_minute,
        os.environ.get("TZ", "UTC"),
    )

    observer = start_backup_observer(cfg, db)
    log.info("filesystem journal watcher started (polling)")

    def mklog() -> Callable[[str], None]:
        return lambda m: log.info(m)

    def job_full() -> None:
        log.info("scheduled full snapshot starting")
        t0 = time.perf_counter()
        run_full_snapshot(cfg, db, do_copy_icloud=True, log=mklog())
        apply_retention(cfg, db, log=mklog())
        log.info("snapshot job finished in %.1fs", time.perf_counter() - t0)

    def job_retry() -> None:
        retry_failed_copies(cfg, db, log=mklog())

    def job_retention_only() -> None:
        apply_retention(cfg, db, log=mklog())

    sched = BackgroundScheduler(timezone=os.environ.get("TZ") or "UTC")
    sched.add_job(
        job_full,
        CronTrigger(hour=cfg.snapshot_hour, minute=cfg.snapshot_minute),
        id="daily_full_snapshot",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    sched.add_job(
        job_retry,
        IntervalTrigger(seconds=cfg.retry_failed_copy_interval_sec),
        id="retry_icloud_copy",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        job_retention_only,
        CronTrigger(hour=3, minute=17),
        id="retention_sweep",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    log.info("scheduler started")

    if os.environ.get("BACKUP_RUN_ON_START", "").lower() in ("1", "true", "yes"):
        log.info("BACKUP_RUN_ON_START: running snapshot now")
        job_full()
    else:
        job_retry()
        job_retention_only()

    def shutdown(*_args: object) -> None:
        log.info("shutting down…")
        sched.shutdown(wait=False)
        observer.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()
