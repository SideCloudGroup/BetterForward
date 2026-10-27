import sqlite3


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()
        # Per-user activity: first_seen is NULL for users who contacted the bot before this upgrade;
        # language is the last Telegram client language seen.
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                first_seen INTEGER,
                last_seen INTEGER,
                message_count INTEGER NOT NULL DEFAULT 0,
                language TEXT
            )
        """)
        # Daily message counters; day is YYYY-MM-DD in the configured time zone.
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                day TEXT PRIMARY KEY,
                incoming INTEGER NOT NULL DEFAULT 0,
                outgoing INTEGER NOT NULL DEFAULT 0
            )
        """)
        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS reply_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL
            )
        """)
        # Backfill message counts for existing users (split chunks counted once).
        db_cursor.execute("""
            INSERT OR IGNORE INTO user_stats (user_id, message_count)
            SELECT t.user_id,
                   (SELECT COUNT(DISTINCT m.received_id) FROM messages m
                    WHERE m.topic_id = t.thread_id AND m.in_group = 0)
            FROM topics t WHERE t.user_id IS NOT NULL
        """)
        conn.commit()
