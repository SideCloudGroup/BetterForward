import sqlite3
import logging

logger = logging.getLogger()


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()

        # Add Forward Success Message settings
        try:
            db_cursor.execute("""
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('forward_success_msg_enabled', 'disable')
            """)
            db_cursor.execute("""
                INSERT OR IGNORE INTO settings (key, value) 
                VALUES ('forward_success_msg_content', NULL)
            """)
            conn.commit()
            logger.info("Added Forward Success Message settings")
        except Exception as e:
            logger.error(f"Failed to add blocked user reply settings: {e}")
            conn.rollback()