"""Database operations module."""

import importlib
import os
import sqlite3
import tempfile
import threading
from traceback import print_exc

from src.config import logger, _


class Database:
    """Handles all database operations with thread-safe SQLite access."""

    def __init__(self, db_path: str = "./data/storage.db"):
        self.db_path = db_path
        # Check path exists
        if not os.path.exists(os.path.dirname(db_path)):
            os.makedirs(os.path.dirname(db_path))
        # Lock for thread-safe database operations
        self.db_lock = threading.Lock()
        self.upgrade_db()

    def get_connection(self):
        """
        Get a thread-safe database connection.
        
        Returns:
            sqlite3.Connection: A database connection with proper settings for multi-threading
        """
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,  # Wait up to 30 seconds for lock
            check_same_thread=False,  # Allow multi-threaded access
            isolation_level=None  # Autocommit mode for better concurrency
        )
        # Enable Write-Ahead Logging for better concurrent access
        conn.execute('PRAGMA journal_mode=WAL')
        # Busy timeout for waiting on locks
        conn.execute('PRAGMA busy_timeout=30000')
        return conn

    def upgrade_db(self):
        """Upgrade database to the latest version."""
        try:
            db = self.get_connection()
            try:
                db_cursor = db.cursor()
                db_cursor.execute("SELECT value FROM settings WHERE key = 'db_version'")
                current_version = int(db_cursor.fetchone()[0])
            finally:
                db.close()
        except sqlite3.OperationalError:
            current_version = 0

        db_migrate_dir = "./db_migrate"
        files = [f for f in os.listdir(db_migrate_dir) if f.endswith('.py')]
        files.sort(key=lambda x: int(x.split('_')[0]))

        pending = [f for f in files if int(f.split('_')[0]) > current_version]
        backup_path = None
        if pending and current_version > 0:
            try:
                backup_path = self.backup(current_version)
            except Exception:
                # Never migrate without a backup; the data stays untouched at the current version.
                logger.error(_("Failed to back up database before upgrading, aborting"))
                print_exc()
                exit(1)

        for file in files:
            try:
                if (version := int(file.split('_')[0])) > current_version:
                    logger.info(_("Upgrading database to version {}").format(version))
                    module = importlib.import_module(f"db_migrate.{file[:-3]}")
                    module.upgrade(self.db_path)
                    db = self.get_connection()
                    try:
                        db_cursor = db.cursor()
                        db_cursor.execute("UPDATE settings SET value = ? WHERE key = 'db_version'",
                                          (str(version),))
                    finally:
                        db.close()
            except Exception:
                logger.error(_("Failed to upgrade database"))
                if backup_path:
                    logger.error(_("Restore the backup to roll back: {}").format(backup_path))
                print_exc()
                exit(1)

    BACKUPS_TO_KEEP = 3

    def backup(self, version: int) -> str:
        """Keep the first complete backup of each version, including across failed upgrade retries."""
        backup_path = f"{self.db_path}.bak-{version}"
        if os.path.exists(backup_path):
            return backup_path

        directory = os.path.dirname(self.db_path) or "."
        fd, temporary_path = tempfile.mkstemp(
            prefix=os.path.basename(backup_path) + ".", suffix=".tmp", dir=directory)
        os.close(fd)
        try:
            source = self.get_connection()
            try:
                target = sqlite3.connect(temporary_path)
                try:
                    # Include WAL pages, but never publish a partially written backup.
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
            try:
                # Publish atomically without replacing another process's completed backup.
                os.link(temporary_path, backup_path)
            except FileExistsError:
                pass
        finally:
            os.remove(temporary_path)
        logger.info(_("Database backed up to {}").format(backup_path))

        prefix = os.path.basename(self.db_path) + ".bak-"
        backups = sorted(
            (f for f in os.listdir(directory) if f.startswith(prefix) and f[len(prefix):].isdigit()),
            key=lambda f: int(f[len(prefix):]))
        for old_backup in backups[:-self.BACKUPS_TO_KEEP]:
            os.remove(os.path.join(directory, old_backup))
        return backup_path

    def get_setting(self, key: str):
        """Get a setting value from the database."""
        db = self.get_connection()
        try:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (key,))
            result = db_cursor.fetchone()
            return result[0] if result else None
        finally:
            db.close()

    def set_setting(self, key: str, value: str):
        """Set a setting value in the database (insert or update)."""
        db = self.get_connection()
        try:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
            if db_cursor.rowcount == 0:
                db_cursor.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
        finally:
            db.close()

    def get_all_settings(self):
        """Load all settings from the database."""
        db = self.get_connection()
        try:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT key, value FROM settings")
            return {key: value for key, value in db_cursor.fetchall()}
        finally:
            db.close()
