import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_id INTEGER NOT NULL,
                forwarded_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                in_group BOOLEAN NOT NULL
            )
        """)
        conn.commit()
