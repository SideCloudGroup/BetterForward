import sqlite3
import logging

logger = logging.getLogger()


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        try:
            db_cursor.execute("PRAGMA table_info(topics)")
            columns = [col[1] for col in db_cursor.fetchall()]
            if "note" in columns:
                logger.info("topics.note column already exists, skipping")
                return
            db_cursor.execute("ALTER TABLE topics ADD COLUMN note TEXT")
            conn.commit()
            logger.info("Added topics.note column")
        except Exception as e:
            logger.error(f"Failed to add topics.note: {e}")
            conn.rollback()
            raise
