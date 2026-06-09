import sqlite3


DEFAULT_PERMISSION_SETTINGS = {
    "permission_default_photo": "enable",
    "permission_default_sticker": "enable",
    "permission_default_video": "enable",
    "permission_default_voice": "enable",
    "permission_default_file": "enable",
    "permission_default_link": "enable",
    "permission_default_username": "enable",
    "permission_restricted_reply_enabled": "enable",
    "permission_restricted_reply_message": "您不被允许发送“{permission}”类型消息，请先联系对方解限。",
}


def _insert_setting_if_missing(db_cursor, key, value):
    db_cursor.execute(
        """
        INSERT INTO settings (key, value)
        SELECT ?, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM settings WHERE key = ?
        )
        """,
        (key, value, key),
    )


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()

        for key, value in DEFAULT_PERMISSION_SETTINGS.items():
            _insert_setting_if_missing(db_cursor, key, value)

        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_permission_overrides (
                user_id INTEGER NOT NULL,
                permission_key TEXT NOT NULL,
                override TEXT NOT NULL CHECK (override IN ('allow', 'deny')),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, permission_key)
            )
        """)

        conn.commit()
