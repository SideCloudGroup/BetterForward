"""Record verification times and keep verification permanent by default."""

import sqlite3
import time


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(verified_users)")}
        if "verified_at" not in columns:
            conn.execute("ALTER TABLE verified_users ADD COLUMN verified_at REAL NOT NULL DEFAULT 0")
            # No historical completion times exist. Give existing users a full period
            # from this upgrade if the admin later enables a validity limit.
            conn.execute("UPDATE verified_users SET verified_at = ?", (time.time(),))
        conn.execute(
            "INSERT INTO settings (key, value) SELECT 'captcha_verification_days', '0' "
            "WHERE NOT EXISTS (SELECT 1 FROM settings WHERE key = 'captcha_verification_days')"
        )
