import sqlite3
import time
from pathlib import Path

import pytest

from backend.backup import perform_sqlite_backup, prune_old_backups
from backend.database import initialize_database


def test_sqlite_backup_creates_valid_copy(tmp_path):
    src_db = tmp_path / "source.sqlite3"
    backup_dir = tmp_path / "backups"
    initialize_database(src_db)

    # Insert test data into source
    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, msg TEXT)")
    conn.execute("INSERT INTO test_table (msg) VALUES ('backup_verified')")
    conn.commit()
    conn.close()

    backup_file, pruned = perform_sqlite_backup(
        src_db_path=src_db,
        backup_dir=backup_dir,
        retention_days=7,
    )

    assert backup_file.is_file()
    assert backup_file.name.startswith("market_")
    assert backup_file.name.endswith(".sqlite3")
    assert pruned == 0

    # Verify data inside the backup
    b_conn = sqlite3.connect(backup_file)
    row = b_conn.execute("SELECT msg FROM test_table WHERE id = 1").fetchone()
    b_conn.close()
    assert row[0] == "backup_verified"


def test_prune_old_backups_deletes_files_older_than_retention(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    old_file = backup_dir / "market_20261001_000000.sqlite3"
    old_file.write_text("old_data", encoding="utf-8")
    # Set modification time to 10 days ago
    ten_days_ago = time.time() - (10 * 86400)
    import os
    os.utime(old_file, (ten_days_ago, ten_days_ago))

    new_file = backup_dir / "market_20261105_000000.sqlite3"
    new_file.write_text("recent_data", encoding="utf-8")

    pruned = prune_old_backups(backup_dir, retention_days=7)
    assert pruned == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_backup_raises_when_source_missing(tmp_path):
    missing_db = tmp_path / "nonexistent.sqlite3"
    with pytest.raises(FileNotFoundError):
        perform_sqlite_backup(missing_db, tmp_path / "backups")

