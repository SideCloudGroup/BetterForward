import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        db_cursor.execute("""
                       CREATE TABLE IF NOT EXISTS topics (
                           id INTEGER PRIMARY KEY,
                           user_id INTEGER,
                           thread_id INTEGER
                       )
                   """)
        db_cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON topics(user_id)")
        db_cursor.execute("CREATE INDEX IF NOT EXISTS idx_thread_id ON topics(thread_id)")
        db_cursor.execute("""
                       CREATE TABLE IF NOT EXISTS auto_response (
                           id INTEGER PRIMARY KEY,
                           key TEXT NOT NULL,
                           value TEXT NOT NULL
                       )
                   """)
        db_cursor.execute(
            "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT NOT NULL)")
        db_cursor.execute("INSERT INTO settings (key, value) VALUES ('db_version', '20240501')")
        conn.commit()
