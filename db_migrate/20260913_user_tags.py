import sqlite3
import logging

logger = logging.getLogger()


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        try:
            db_cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_tags (
                    user_id INTEGER PRIMARY KEY,
                    tag TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db_cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_tags_tag ON user_tags(tag)"
            )
            conn.commit()
            logger.info("Created user_tags table")
        except Exception as e:
            logger.error(f"Failed to create user_tags: {e}")
            conn.rollback()
            raise
