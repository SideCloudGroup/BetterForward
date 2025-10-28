"""Database operations module."""

import importlib
import os
import sqlite3
from traceback import print_exc

from src.config import logger, _


class Database:
    """Handles all database operations."""

    def __init__(self, db_path: str = "./data/storage.db"):
        self.db_path = db_path
        # Check path exists
        if not os.path.exists(os.path.dirname(db_path)):
            os.makedirs(os.path.dirname(db_path))
        self.upgrade_db()

    def upgrade_db(self):
        """Upgrade database to the latest version."""
        try:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                db_cursor.execute("SELECT value FROM settings WHERE key = 'db_version'")
                current_version = int(db_cursor.fetchone()[0])
        except sqlite3.OperationalError:
            current_version = 0

        db_migrate_dir = "./db_migrate"
        files = [f for f in os.listdir(db_migrate_dir) if f.endswith('.py')]
        files.sort(key=lambda x: int(x.split('_')[0]))

        for file in files:
            try:
                if (version := int(file.split('_')[0])) > current_version:
                    logger.info(_("Upgrading database to version {}").format(version))
                    module = importlib.import_module(f"db_migrate.{file[:-3]}")
                    module.upgrade(self.db_path)
                    with sqlite3.connect(self.db_path) as db:
                        db_cursor = db.cursor()
                        db_cursor.execute("UPDATE settings SET value = ? WHERE key = 'db_version'",
                                          (str(version),))
                        db.commit()
            except Exception:
                logger.error(_("Failed to upgrade database"))
                print_exc()
                exit(1)

    def get_setting(self, key: str):
        """Get a setting value from the database."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (key,))
            result = db_cursor.fetchone()
            return result[0] if result else None

    def set_setting(self, key: str, value: str):
        """Set a setting value in the database."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
            db.commit()

    def get_all_settings(self):
        """Load all settings from the database."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT key, value FROM settings")
            return {key: value for key, value in db_cursor.fetchall()}
