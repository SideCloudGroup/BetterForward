"""Message and user activity statistics."""

import time
from datetime import datetime

import pytz


def _today(time_zone) -> str:
    return datetime.now(time_zone or pytz.UTC).strftime("%Y-%m-%d")


def _day_start(time_zone) -> int:
    now = datetime.now(time_zone or pytz.UTC)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def record_incoming(cursor, user_id: int, time_zone=None, language_code: str | None = None) -> None:
    """Count one message from a user and remember their client language."""
    now = int(time.time())
    cursor.execute(
        "INSERT INTO user_stats (user_id, first_seen, last_seen, message_count, language) VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen, "
        "message_count = message_count + 1, language = COALESCE(excluded.language, language)",
        (user_id, now, now, language_code))
    cursor.execute(
        "INSERT INTO daily_stats (day, incoming) VALUES (?, 1) "
        "ON CONFLICT(day) DO UPDATE SET incoming = incoming + 1",
        (_today(time_zone),))


def record_outgoing(cursor, time_zone=None) -> None:
    """Count one admin reply sent to a user."""
    cursor.execute(
        "INSERT INTO daily_stats (day, outgoing) VALUES (?, 1) "
        "ON CONFLICT(day) DO UPDATE SET outgoing = outgoing + 1",
        (_today(time_zone),))


def get_summary(cursor, time_zone=None, verified_since: float = 0) -> dict:
    """Return overall counts plus today's activity.

    verified_since: only count verifications at or after this Unix time (expired ones are excluded).
    """

    def scalar(sql, params=()):
        return cursor.execute(sql, params).fetchone()[0] or 0

    today = cursor.execute("SELECT incoming, outgoing FROM daily_stats WHERE day = ?",
                           (_today(time_zone),)).fetchone() or (0, 0)
    return {
        "users": scalar("SELECT COUNT(DISTINCT user_id) FROM topics WHERE user_id IS NOT NULL"),
        "new_users_today": scalar("SELECT COUNT(*) FROM user_stats WHERE first_seen >= ?",
                                  (_day_start(time_zone),)),
        "active_users_today": scalar("SELECT COUNT(*) FROM user_stats WHERE last_seen >= ?",
                                     (_day_start(time_zone),)),
        "incoming_today": today[0],
        "outgoing_today": today[1],
        "banned_users": scalar("SELECT COUNT(*) FROM blocked_users"),
        "verified_users": scalar("SELECT COUNT(*) FROM verified_users WHERE verified_at >= ?",
                                 (verified_since,)),
    }


def get_user_stats(cursor, user_id: int) -> dict | None:
    """Return first_seen, last_seen, message_count and language for a user, or None."""
    row = cursor.execute(
        "SELECT first_seen, last_seen, message_count, language FROM user_stats WHERE user_id = ?",
        (user_id,)).fetchone()
    if row is None:
        return None
    return {"first_seen": row[0], "last_seen": row[1], "message_count": row[2], "language": row[3]}
