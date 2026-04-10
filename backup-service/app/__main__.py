"""CLI: `python -m app` (serve) | snapshot-once | retry | retention."""

from __future__ import annotations

import argparse
import sys

from .config import BackupConfig
from .db import BackupDB
from .main import serve, setup_logging
from .retention import apply_retention
from .snapshot_job import retry_failed_copies, run_full_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Second Brain batch backup service")
    sub = parser.add_subparsers(dest="cmd", help="command")
    sub.required = False

    sub.add_parser("serve", help="watcher + daily snapshot + retry + retention (default)")

    p_once = sub.add_parser("snapshot-once", help="single full snapshot then exit")
    p_once.add_argument(
        "--no-icloud",
        action="store_true",
        help="skip copy to ICLOUD_BACKUP_DIR (local zip only)",
    )

    sub.add_parser("retry", help="retry failed iCloud copies then exit")
    sub.add_parser("retention", help="apply retention policy then exit")

    args = parser.parse_args()
    cmd = args.cmd or "serve"

    if cmd == "serve":
        serve()
        return

    cfg = BackupConfig.from_env()
    log = setup_logging(cfg.log_level)

    db = BackupDB(cfg.state_db_path)
    db.init()

    if cmd == "snapshot-once":
        run_full_snapshot(
            cfg,
            db,
            do_copy_icloud=not getattr(args, "no_icloud", False),
            log=lambda m: log.info(m),
        )
    elif cmd == "retry":
        retry_failed_copies(cfg, db, log=lambda m: log.info(m))
    elif cmd == "retention":
        apply_retention(cfg, db, log=lambda m: log.info(m))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
