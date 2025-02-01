import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        db_cursor.execute("""
            INSERT INTO settings (key, value) VALUES ('time_zone', 'Europe/London');
        """)
        db_cursor.execute("""
            ALTER TABLE auto_response
            ADD COLUMN start_time TEXT DEFAULT NULL;
        """)
        db_cursor.execute("""
            ALTER TABLE auto_response
            ADD COLUMN end_time TEXT DEFAULT NULL;
        """)
        db_cursor.execute("""
            ALTER TABLE auto_response
            DROP COLUMN topic_action;
        """)
        conn.commit()
