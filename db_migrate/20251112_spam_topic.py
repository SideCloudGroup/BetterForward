import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        
        # Add spam_topic setting to store the spam topic ID
        db_cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) 
            VALUES ('spam_topic', NULL)
        """)
        
        # Create blocked_users table
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Migrate existing banned users from topics table
        db_cursor.execute("""
            INSERT OR IGNORE INTO blocked_users (user_id)
            SELECT user_id FROM topics WHERE ban = 1 AND user_id IS NOT NULL
        """)
        
        # Add settings for blocked user auto-reply
        db_cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) 
            VALUES ('blocked_user_reply_enabled', 'disable')
        """)
        
        db_cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) 
            VALUES ('blocked_user_reply_message', NULL)
        """)

        # Get current table schema
        db_cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='topics'")
        result = db_cursor.fetchone()

        if result is None:
            # Table doesn't exist, nothing to do
            conn.commit()
            return

        # Check if ban column exists
        db_cursor.execute("PRAGMA table_info(topics)")
        columns = db_cursor.fetchall()
        has_ban_column = any(col[1] == 'ban' for col in columns)

        if not has_ban_column:
            # Column already removed or never existed
            conn.commit()
            return

        # Create new table without ban column
        db_cursor.execute("""
                          CREATE TABLE topics_new
                          (
                              id        INTEGER PRIMARY KEY,
                              user_id   INTEGER,
                              thread_id INTEGER
                          )
                          """)

        # Copy data from old table (excluding ban column)
        db_cursor.execute("""
                          INSERT INTO topics_new (id, user_id, thread_id)
                          SELECT id, user_id, thread_id
                          FROM topics
                          """)

        # Drop old table
        db_cursor.execute("DROP TABLE topics")

        # Rename new table to original name
        db_cursor.execute("ALTER TABLE topics_new RENAME TO topics")

        # Recreate indexes
        db_cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON topics(user_id)")
        db_cursor.execute("CREATE INDEX IF NOT EXISTS idx_thread_id ON topics(thread_id)")
        
        conn.commit()

