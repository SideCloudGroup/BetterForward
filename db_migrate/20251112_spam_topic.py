import sqlite3
import logging

logger = logging.getLogger()


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()

        # Add spam_topic setting
        try:
            db_cursor.execute("""
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('spam_topic', NULL)
            """)
            conn.commit()
            logger.info("Added spam_topic setting")
        except Exception as e:
            logger.error(f"Failed to add spam_topic setting: {e}")
            conn.rollback()

        # Create blocked_users table
        try:
            db_cursor.execute("""
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            logger.info("Created blocked_users table")
        except Exception as e:
            logger.error(f"Failed to create blocked_users table: {e}")
            conn.rollback()

        # Migrate existing banned users
        try:
            db_cursor.execute("""
                INSERT OR IGNORE INTO blocked_users (user_id)
                SELECT user_id FROM topics WHERE ban = 1 AND user_id IS NOT NULL
            """)
            migrated = db_cursor.rowcount
            conn.commit()
            logger.info(f"Migrated {migrated} banned users to blocked_users table")
        except Exception as e:
            logger.error(f"Failed to migrate banned users: {e}")
            conn.rollback()

        # Add blocked user auto-reply settings
        try:
            db_cursor.execute("""
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('blocked_user_reply_enabled', 'disable')
            """)
            db_cursor.execute("""
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('blocked_user_reply_message', NULL)
            """)
            conn.commit()
            logger.info("Added blocked user reply settings")
        except Exception as e:
            logger.error(f"Failed to add blocked user reply settings: {e}")
            conn.rollback()

        # Remove ban column from topics table
        try:
            # Get current table schema
            db_cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='topics'")
            result = db_cursor.fetchone()

            if result is None:
                logger.info("Topics table doesn't exist, skipping ban column removal")
                return

            # Check if ban column exists
            db_cursor.execute("PRAGMA table_info(topics)")
            columns = db_cursor.fetchall()
            has_ban_column = any(col[1] == 'ban' for col in columns)

            if not has_ban_column:
                logger.info("Ban column doesn't exist in topics table, skipping removal")
                return

            # Create new table without ban column
            db_cursor.execute("""
                CREATE TABLE topics_new (
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
            logger.info("Successfully removed ban column from topics table")
        except Exception as e:
            logger.error(f"Failed to remove ban column from topics table: {e}")
            # Try to cleanup if table creation failed
            try:
                db_cursor.execute("DROP TABLE IF EXISTS topics_new")
                conn.commit()
            except Exception:
                pass
            conn.rollback()
