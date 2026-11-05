import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.settings import DB_PATH

ROOT = Path(__file__).resolve().parents[1]
BACKUPS_DIR = ROOT / "backups"
DEFAULT_RETENTION_DAYS = 7
LOGGER = logging.getLogger(__name__)


def prune_old_backups(backup_dir: Path, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    cutoff_time = time.time() - (retention_days * 86400)
    pruned_count = 0
    if not backup_dir.is_dir():
        return 0

    for item in backup_dir.glob("market_*.sqlite3"):
        if item.is_file():
            try:
                if item.stat().st_mtime < cutoff_time:
                    item.unlink()
                    pruned_count += 1
                    LOGGER.info("pruned_old_backup file=%s", item.name)
            except OSError as exc:
                LOGGER.warning("failed_to_prune_backup file=%s error=%s", item.name, type(exc).__name__)

    return pruned_count


def perform_sqlite_backup(
    src_db_path: Path = DB_PATH,
    backup_dir: Path = BACKUPS_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> tuple[Path, int]:
    if not src_db_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {src_db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"market_{timestamp}.sqlite3"

    LOGGER.info("starting_sqlite_backup src=%s dest=%s", src_db_path, backup_file)
    src_conn = sqlite3.connect(src_db_path, timeout=30)
    dst_conn = sqlite3.connect(backup_file, timeout=30)
    try:
        src_conn.backup(dst_conn)
        LOGGER.info("sqlite_backup_completed size_bytes=%s", backup_file.stat().st_size)
    finally:
        dst_conn.close()
        src_conn.close()

    pruned = prune_old_backups(backup_dir, retention_days)
    return backup_file, pruned


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raw_retention = os.getenv("BACKUP_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        retention_days = int(raw_retention)
    except ValueError:
        retention_days = DEFAULT_RETENTION_DAYS

    try:
        backup_file, pruned = perform_sqlite_backup(
            src_db_path=DB_PATH,
            backup_dir=BACKUPS_DIR,
            retention_days=retention_days,
        )
        print(f"Backup successful: {backup_file.name} (Pruned {pruned} old files)")
    except Exception as exc:
        LOGGER.error("backup_failed error=%s", type(exc).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()

