import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        db_cursor.execute("ALTER TABLE auto_response ADD COLUMN topic_action BOOLEAN DEFAULT 0")
        conn.commit()
