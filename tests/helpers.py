"""Shared test helpers."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from telebot.apihelper import ApiTelegramException


def make_api_error(description: str, error_code: int = 400) -> ApiTelegramException:
    payload = {"ok": False, "error_code": error_code, "description": description}
    return ApiTelegramException("test", payload, payload)


def make_user(user_id: int = 101, first_name: str = "Alice", username: str = "alice"):
    return SimpleNamespace(
        id=user_id,
        first_name=first_name,
        last_name=None,
        username=username,
    )


def make_message(
    *,
    message_id: int = 1,
    chat_id: int = 101,
    chat_type: str = "private",
    user_id: int = 101,
    text: str = "hello",
    content_type: str = "text",
    message_thread_id=None,
    entities=None,
    caption=None,
    caption_entities=None,
    reply_to_message=None,
    **extra,
):
    user = make_user(user_id=user_id)
    message = SimpleNamespace(
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        from_user=user,
        text=text,
        content_type=content_type,
        message_thread_id=message_thread_id,
        entities=entities,
        caption=caption,
        caption_entities=caption_entities,
        reply_to_message=reply_to_message,
        photo=extra.get("photo"),
        sticker=extra.get("sticker"),
        video=extra.get("video"),
        document=extra.get("document"),
        audio=extra.get("audio"),
        voice=extra.get("voice"),
        animation=extra.get("animation"),
        contact=extra.get("contact"),
    )
    return message


def make_cache(initial=None):
    store = dict(initial or {})

    cache = MagicMock()
    cache.get.side_effect = lambda key, default=None: store.get(key, default)
    cache.set.side_effect = lambda key, value, *args, **kwargs: store.__setitem__(key, value)
    cache.delete.side_effect = lambda key: store.pop(key, None)
    cache._store = store
    return cache


def init_core_db(db_path: str):
    """Create minimal schema used by core message/permission flows."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE topics (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                thread_id INTEGER,
                note TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_id INTEGER NOT NULL,
                forwarded_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                in_group BOOLEAN NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE blocked_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE verified_users (
                user_id INTEGER PRIMARY KEY
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE auto_response (
                id INTEGER PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                is_regex INTEGER DEFAULT 0,
                type TEXT DEFAULT 'text',
                start_time TEXT,
                end_time TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE user_permission_overrides (
                user_id INTEGER NOT NULL,
                permission_key TEXT NOT NULL,
                override TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, permission_key)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE user_tags (
                user_id INTEGER PRIMARY KEY,
                tag TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for key, value in (
            ("permission_default_photo", "enable"),
            ("permission_default_sticker", "enable"),
            ("permission_default_video", "enable"),
            ("permission_default_voice", "enable"),
            ("permission_default_file", "enable"),
            ("permission_default_link", "enable"),
            ("permission_default_username", "enable"),
            ("permission_restricted_reply_enabled", "enable"),
            (
                "permission_restricted_reply_message",
                'You are not allowed to send "{permission}" type messages. '
                "Please contact the other party to lift the restriction.",
            ),
            ("captcha", "disable"),
        ):
            cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
