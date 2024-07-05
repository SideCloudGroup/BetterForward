import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        db_cursor.execute("""
            CREATE TABLE settings_dg_tmp (
                id INTEGER PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT
            );
        """)
        db_cursor.execute("""
            INSERT INTO settings_dg_tmp(id, key, value)
            SELECT id, key, value FROM settings;
        """)
        db_cursor.execute("""
            DROP TABLE settings;
        """)
        db_cursor.execute("""
            ALTER TABLE settings_dg_tmp RENAME TO settings;
        """)
        db_cursor.execute("""
            INSERT INTO settings (key, value) VALUES ('default_message', NULL)
        """)
        conn.commit()
