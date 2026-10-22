"""Tests for /stats, /info and reply templates."""

import importlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.handlers.command_handler import CommandHandler
from src.handlers.message_handler import MessageHandler
from tests.helpers import init_core_db, make_api_error, make_cache, make_message


GROUP_ID = -1001234567890
USER_ID = 101
THREAD_ID = 55


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_core_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, ?)", (USER_ID, THREAD_ID))
        conn.commit()

    cache = make_cache({"setting_captcha": "disable"})
    bot = MagicMock()
    bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
    bot.get_chat.return_value = SimpleNamespace(first_name="Alice", last_name=None, username="alice")
    counter = iter(range(1000, 2000))
    bot.send_message.side_effect = lambda *args, **kwargs: SimpleNamespace(message_id=next(counter))

    auto_response = MagicMock()
    auto_response.match_auto_response.return_value = None
    permission_manager = MagicMock()
    permission_manager.resolve_permission.return_value = True
    message_handler = MessageHandler(bot=bot, group_id=GROUP_ID, db_path=db_path, cache=cache,
                                     captcha_manager=MagicMock(), auto_response_manager=auto_response,
                                     permission_manager=permission_manager)
    captcha = MagicMock()
    captcha.is_user_verified.return_value = True
    queue_stats = {"main_queue_size": 1, "total_queued_messages": 2, "processing_users_count": 1,
                   "workers_count": 5}
    command_handler = CommandHandler(bot, GROUP_ID, db_path, cache, None, captcha,
                                     message_handler=message_handler, queue_stats=lambda: queue_stats)
    return SimpleNamespace(db_path=db_path, cache=cache, bot=bot, messages=message_handler,
                           commands=command_handler)


def _user_message(message_id: int, text: str = "hi"):
    return make_message(message_id=message_id, chat_id=USER_ID, user_id=USER_ID, text=text)


def _admin_message(message_id: int, text: str):
    return make_message(message_id=message_id, chat_id=GROUP_ID, chat_type="supergroup",
                        message_thread_id=THREAD_ID, user_id=1, text=text)


def _replied_text(bot) -> str:
    return bot.reply_to.call_args.args[1]


def test_stats_counts_incoming_and_outgoing(env):
    env.messages.handle_message(_user_message(1))
    env.messages.handle_message(_user_message(2))
    env.messages.handle_message(_admin_message(50, "hello back"))

    env.commands.handle_stats(make_message(chat_id=GROUP_ID, chat_type="supergroup", user_id=1,
                                           text="/stats"))
    text = _replied_text(env.bot)
    assert "Users: 1" in text
    assert "New users today: 1" in text
    assert "Messages today: 2 received / 1 replied" in text
    assert "Queue: 3 waiting, 1 users processing, 5 workers" in text


def test_stats_ignored_for_non_admin(env):
    env.bot.get_chat_member.return_value = SimpleNamespace(status="member")
    env.commands.handle_stats(make_message(chat_id=GROUP_ID, chat_type="supergroup", user_id=2,
                                           text="/stats"))
    env.bot.reply_to.assert_not_called()


def test_info_shows_profile_and_activity(env):
    env.messages.handle_message(_user_message(1))
    env.commands.handle_info(_admin_message(60, "/info"))

    text = _replied_text(env.bot)
    assert f"User ID: {USER_ID}" in text
    assert "Username: @alice" in text
    assert "Messages sent: 1" in text
    assert "Verified: Yes" in text
    assert "Banned: No" in text
    assert "First contact: Unknown" not in text


def test_template_reply_sends_to_user_and_maps_echo(env):
    with sqlite3.connect(env.db_path) as conn:
        conn.executemany("INSERT INTO reply_templates (content) VALUES (?)", [("First",), ("Second",)])
        conn.commit()

    env.commands.handle_template_reply(_admin_message(70, "/r 2"))

    first_call, echo_call = env.bot.send_message.call_args_list
    assert first_call.args == (USER_ID, "Second")
    assert echo_call.args == (GROUP_ID, "📝 Second")
    env.bot.delete_message.assert_called_once_with(GROUP_ID, 70)
    with sqlite3.connect(env.db_path) as conn:
        assert conn.execute("SELECT received_id, forwarded_id, in_group FROM messages").fetchall() == [
            (1001, 1000, 1)]
        assert conn.execute("SELECT outgoing FROM daily_stats").fetchone() == (1,)


def test_template_reply_unknown_number(env):
    env.commands.handle_template_reply(_admin_message(70, "/r 3"))
    assert "Template not found" in _replied_text(env.bot)
    env.bot.send_message.assert_not_called()


@pytest.mark.parametrize("length", [4094, 4095, 4096])
def test_template_echo_fits_message_limit(env, length):
    content = "a" * length
    with sqlite3.connect(env.db_path) as conn:
        conn.execute("INSERT INTO reply_templates (content) VALUES (?)", (content,))
    sent_texts = []
    def send(chat_id, text, **kwargs):
        if len(text) > 4096:
            raise make_api_error("Bad Request: message is too long")
        sent_texts.append((chat_id, text))
        return SimpleNamespace(message_id=1000 + len(sent_texts))
    env.bot.send_message.side_effect = send

    env.commands.handle_template_reply(_admin_message(70, "/r 1"))

    assert sent_texts == [(USER_ID, content), (GROUP_ID, "📝 " + content if length <= 4094 else content)]
    with sqlite3.connect(env.db_path) as conn:
        assert conn.execute("SELECT received_id, forwarded_id FROM messages").fetchall() == [(1002, 1001)]
        assert conn.execute("SELECT outgoing FROM daily_stats").fetchone() == (1,)


def test_template_echo_failure_preserves_delivery_and_command_mapping(env):
    with sqlite3.connect(env.db_path) as conn:
        conn.execute("INSERT INTO reply_templates (content) VALUES ('Delivered')")
    def send(chat_id, text, **kwargs):
        if chat_id == GROUP_ID:
            # Delivery must already be durable before the independent echo request.
            with sqlite3.connect(env.db_path) as conn:
                assert conn.execute("SELECT received_id, forwarded_id FROM messages").fetchall() == [(70, 1000)]
                assert conn.execute("SELECT outgoing FROM daily_stats").fetchone() == (1,)
            raise make_api_error("Too Many Requests", 429)
        return SimpleNamespace(message_id=1000)
    env.bot.send_message.side_effect = send

    env.commands.handle_template_reply(_admin_message(70, "/r 1"))

    env.bot.delete_message.assert_not_called()
    with sqlite3.connect(env.db_path) as conn:
        assert conn.execute("SELECT received_id, forwarded_id FROM messages").fetchall() == [(70, 1000)]


def test_migration_backfills_existing_users(env):
    with sqlite3.connect(env.db_path) as conn:
        conn.executemany(
            "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
            [(1, 201, THREAD_ID, 0), (1, 202, THREAD_ID, 0), (2, 203, THREAD_ID, 0), (9, 204, THREAD_ID, 1)])
        conn.commit()

    importlib.import_module("db_migrate.20261021_stats_templates").upgrade(env.db_path)

    with sqlite3.connect(env.db_path) as conn:
        assert conn.execute("SELECT user_id, first_seen, message_count FROM user_stats").fetchall() == [
            (USER_ID, None, 2)]


def test_pin_text_format():
    from src.utils.helpers import build_user_info_pin_text

    text = build_user_info_pin_text(7398097664, "Alice", "Smith", "alice_s", "en")
    assert text == (
        "• ID: [7398097664](tg://openmessage?user_id=7398097664)\n"
        "• Full Name: Alice Smith\n"
        "• Username: @alice\\_s\n"
        "• Language: en 🇬🇧\n"
        "#id7398097664"
    )


def test_pin_text_unknown_and_unmapped_language():
    from src.utils.helpers import build_user_info_pin_text

    assert "• Language: Unknown\n" in build_user_info_pin_text(1, "A", None, None, None)
    assert "• Language: zh-hant\n" in build_user_info_pin_text(1, "A", None, None, "zh-hant")


def test_refresh_uses_last_seen_language(env, monkeypatch):
    user_message = _user_message(1)
    user_message.from_user.language_code = "ja"
    env.messages.handle_message(user_message)

    pinned = []
    monkeypatch.setattr("src.handlers.command_handler.send_and_pin_user_info",
                        lambda bot, group_id, thread_id, text: pinned.append(text))
    monkeypatch.setattr("src.handlers.command_handler.edit_forum_topic", lambda **kwargs: None)
    env.commands.handle_refresh(_admin_message(80, "/refresh"))

    assert "• Language: ja 🇯🇵\n" in pinned[0]


def test_stats_excludes_expired_verifications(env):
    import time

    with sqlite3.connect(env.db_path) as conn:
        conn.executemany("INSERT INTO verified_users (user_id, verified_at) VALUES (?, ?)",
                         [(1, time.time()), (2, time.time() - 40 * 86400)])
        conn.commit()
    env.cache.set("setting_captcha_verification_days", "30")
    env.commands.handle_stats(make_message(chat_id=GROUP_ID, chat_type="supergroup", user_id=1,
                                           text="/stats"))
    assert "Verified users: 1" in _replied_text(env.bot)
