import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        
        # Add spam_topic setting to store the spam topic ID
        db_cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) 
            VALUES ('spam_topic', NULL)
        """)
        
        conn.commit()

