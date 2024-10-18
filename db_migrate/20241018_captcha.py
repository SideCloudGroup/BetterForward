import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        db_cursor.execute("""
            INSERT INTO settings (key, value) VALUES ('captcha', 'disable')
        """)
        db_cursor.execute("""
            CREATE TABLE verified_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL
            );
        """)
        conn.commit()
