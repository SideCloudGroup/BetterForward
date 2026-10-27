"""Upgrade-path tests for database migrations."""

import importlib
import os
import sqlite3
from unittest.mock import MagicMock

import pytest

from src.database import Database


LATEST_VERSION = str(max(int(f.split("_")[0]) for f in os.listdir("db_migrate") if f.endswith(".py")))


def _columns(db_path: str, table: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return [col[1] for col in conn.execute(f"PRAGMA table_info({table})")]


def _db_version(db_path: str) -> str:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT value FROM settings WHERE key = 'db_version'").fetchone()[0]


def test_fresh_database_runs_all_migrations(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    Database(db_path)
    assert _db_version(db_path) == LATEST_VERSION
    assert "language" in _columns(db_path, "user_stats")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT 1 FROM settings WHERE key = 'read_receipt'").fetchone() is None


def _migrate_up_to(db_path: str, last_version: int):
    for name in sorted(f[:-3] for f in os.listdir("db_migrate") if f.endswith(".py")):
        if int(name.split("_")[0]) <= last_version:
            importlib.import_module(f"db_migrate.{name}").upgrade(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'db_version'", (str(last_version),))
        conn.commit()


def test_upgrade_backs_up_database_first(tmp_path):
    db_path = str(tmp_path / "storage.db")
    _migrate_up_to(db_path, 20260803)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO topics (user_id, thread_id) VALUES (101, 55)")
        conn.commit()

    Database(db_path)

    backup = tmp_path / "storage.db.bak-20260803"
    assert backup.exists()
    with sqlite3.connect(str(backup)) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key = 'db_version'").fetchone() == ("20260803",)
        assert conn.execute("SELECT user_id FROM topics").fetchall() == [(101,)]
    assert _db_version(db_path) == LATEST_VERSION
    assert "language" in _columns(db_path, "user_stats")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT user_id FROM user_stats").fetchall() == [(101,)]


def test_database_from_newer_test_build_starts_without_migrating(tmp_path):
    """Test databases from earlier dev builds may carry a version above the latest migration."""
    db_path = str(tmp_path / "storage.db")
    Database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE settings SET value = '20261023' WHERE key = 'db_version'")
        conn.commit()
    Database(db_path)
    assert _db_version(db_path) == "20261023"
    assert not list(tmp_path.glob("storage.db.bak-*"))


def test_no_backup_for_fresh_or_current_database(tmp_path):
    db_path = str(tmp_path / "storage.db")
    Database(db_path)
    Database(db_path)
    assert not list(tmp_path.glob("storage.db.bak-*"))


def test_old_backups_are_pruned(tmp_path):
    db_path = str(tmp_path / "storage.db")
    Database(db_path)
    database = Database(db_path)
    for version in (1, 2, 3, 4, 5):
        database.backup(version)
    assert sorted(p.name for p in tmp_path.glob("storage.db.bak-*")) == [
        "storage.db.bak-3", "storage.db.bak-4", "storage.db.bak-5"]


def test_failed_backup_aborts_before_migrating(tmp_path, monkeypatch):
    db_path = str(tmp_path / "storage.db")
    _migrate_up_to(db_path, 20260803)

    def broken_backup(self, version):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "backup", broken_backup)
    try:
        Database(db_path)
    except SystemExit:
        pass
    else:
        raise AssertionError("expected exit")
    assert _db_version(db_path) == "20260803"


def test_upgrade_retry_keeps_original_backup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "storage.db")
    _migrate_up_to(db_path, 20261001)
    migration = importlib.import_module("db_migrate.20261021_stats_templates")

    def fail_after_schema_change(path):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS partial_upgrade (id INTEGER)")
        raise RuntimeError("migration failed after DDL")

    monkeypatch.setattr(migration, "upgrade", fail_after_schema_change)
    for _ in range(2):
        with pytest.raises(SystemExit):
            Database(db_path)
        with sqlite3.connect(db_path + ".bak-20261001") as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='partial_upgrade'").fetchone() is None
            assert conn.execute("SELECT value FROM settings WHERE key='db_version'").fetchone() == ("20261001",)


def test_incomplete_backup_is_not_published(tmp_path, monkeypatch):
    db_path = str(tmp_path / "storage.db")
    database = Database(db_path)
    source = MagicMock()
    source.backup.side_effect = OSError("disk full")
    monkeypatch.setattr(database, "get_connection", lambda: source)

    with pytest.raises(OSError, match="disk full"):
        database.backup(int(LATEST_VERSION))

    assert not list(tmp_path.glob("storage.db.bak-*"))
    source.close.assert_called_once()
