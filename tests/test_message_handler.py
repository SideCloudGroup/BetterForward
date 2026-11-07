"""Core message forwarding and stale-topic compensation tests."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from telebot.types import Message

from src.handlers.message_handler import MessageHandler
from src.utils.permissions import PermissionManager
from tests.helpers import (
    init_core_db,
    make_api_error,
    make_cache,
    make_message,
    make_quote,
)


GROUP_ID = -1001234567890


@pytest.fixture
def handler_env(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_core_db(db_path)
    cache = make_cache({"setting_captcha": "disable"})
    bot = MagicMock()
    bot.token = "TEST_TOKEN"
    captcha = MagicMock()
    captcha.is_user_verified.return_value = True
    auto_response = MagicMock()
    auto_response.match_auto_response.return_value = None
    permission_manager = PermissionManager(db_path=db_path, cache=cache)

    handler = MessageHandler(
        bot=bot,
        group_id=GROUP_ID,
        db_path=db_path,
        cache=cache,
        captcha_manager=captcha,
        auto_response_manager=auto_response,
        permission_manager=permission_manager,
    )
    return handler, bot, cache, db_path


def _seed_topic(db_path: str, user_id: int, thread_id: int):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO topics (user_id, thread_id) VALUES (?, ?)",
            (user_id, thread_id),
        )
        conn.execute(
            "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
            (11, 22, thread_id, False),
        )
        conn.commit()


def test_is_topic_missing_error_detects_known_markers():
    assert MessageHandler._is_topic_missing_error(
        make_api_error("Bad Request: message thread not found")
    )
    assert MessageHandler._is_topic_missing_error(make_api_error("TOPIC_DELETED"))
    assert MessageHandler._is_topic_missing_error(make_api_error("Bad Request: topic not found"))
    assert not MessageHandler._is_topic_missing_error(make_api_error("Forbidden: bot was blocked"))


def test_forward_user_message_to_existing_topic(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 101
    thread_id = 555
    _seed_topic(db_path, user_id, thread_id)
    cache.set(f"chat_{user_id}_threadid", thread_id)

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 9001
    bot.send_message.return_value = forwarded

    message = make_message(message_id=77, chat_id=user_id, user_id=user_id, text="hi")
    handler.handle_message(message)

    bot.send_message.assert_called()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == GROUP_ID
    assert kwargs["message_thread_id"] == thread_id
    assert kwargs["text"] == "hi"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT received_id, forwarded_id, topic_id, in_group FROM messages WHERE received_id = ?",
            (77,),
        ).fetchone()
    assert row == (77, 9001, thread_id, 0)


def test_stale_topic_compensation_recreates_and_retries(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 202
    stale_thread_id = 111
    new_thread_id = 222
    _seed_topic(db_path, user_id, stale_thread_id)
    cache.set(f"chat_{user_id}_threadid", stale_thread_id)
    cache.set(f"threadid_{stale_thread_id}_userid", user_id)

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 8002
    bot.send_message.side_effect = [
        make_api_error("Bad Request: message thread not found"),
        forwarded,
    ]
    pin_message = MagicMock(spec=Message)
    pin_message.message_id = 1

    message = make_message(message_id=88, chat_id=user_id, user_id=user_id, text="retry me")

    with patch(
        "src.handlers.message_handler.create_forum_topic",
        return_value={"message_thread_id": new_thread_id},
    ) as create_topic, patch(
        "src.handlers.message_handler.send_and_pin_user_info"
    ) as pin_info:
        handler.handle_message(message)

    create_topic.assert_called_once()
    pin_info.assert_called_once()
    assert bot.send_message.call_count == 2
    retry_kwargs = bot.send_message.call_args_list[1].kwargs
    assert retry_kwargs["message_thread_id"] == new_thread_id
    assert retry_kwargs["text"] == "retry me"
    assert retry_kwargs.get("reply_to_message_id") is None

    assert cache.get(f"chat_{user_id}_threadid") == new_thread_id
    assert cache.get(f"threadid_{stale_thread_id}_userid") is None

    with sqlite3.connect(db_path) as conn:
        topics = conn.execute("SELECT user_id, thread_id FROM topics").fetchall()
        stale_msgs = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE topic_id = ?",
            (stale_thread_id,),
        ).fetchone()[0]
        new_msg = conn.execute(
            "SELECT received_id, forwarded_id, topic_id FROM messages WHERE topic_id = ?",
            (new_thread_id,),
        ).fetchone()

    assert topics == [(user_id, new_thread_id)]
    assert stale_msgs == 0
    assert new_msg == (88, 8002, new_thread_id)
    bot.forward_message.assert_not_called()


def test_group_message_forwards_to_mapped_user(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 303
    thread_id = 777
    _seed_topic(db_path, user_id, thread_id)

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 5001
    bot.send_message.return_value = forwarded

    message = make_message(
        message_id=66,
        chat_id=GROUP_ID,
        chat_type="supergroup",
        user_id=999,
        text="reply to user",
        message_thread_id=thread_id,
    )
    handler.handle_message(message)

    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == user_id
    assert kwargs["message_thread_id"] is None
    assert kwargs["text"] == "reply to user"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT received_id, forwarded_id, topic_id, in_group FROM messages WHERE received_id = ?",
            (66,),
        ).fetchone()
    assert row == (66, 5001, thread_id, 1)


def test_long_group_text_is_split_when_forwarding_to_user(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 505
    thread_id = 888
    _seed_topic(db_path, user_id, thread_id)

    long_text = ("hello world\n" * 400)
    assert len(long_text) > 4096

    sent = []

    def _send_message(**kwargs):
        msg = MagicMock(spec=Message)
        msg.message_id = 7000 + len(sent)
        sent.append(kwargs)
        return msg

    bot.send_message.side_effect = _send_message

    message = make_message(
        message_id=99,
        chat_id=GROUP_ID,
        chat_type="supergroup",
        user_id=999,
        text=long_text,
        message_thread_id=thread_id,
    )
    handler.handle_message(message)

    assert len(sent) >= 2
    assert all(call["chat_id"] == user_id for call in sent)
    assert all(len(call["text"]) <= 4096 for call in sent)
    assert "".join(call["text"] for call in sent) == long_text
    assert sent[0].get("reply_to_message_id") is None
    assert all(call.get("reply_to_message_id") is None for call in sent[1:])

    alert_calls = [
        call for call in bot.send_message.call_args_list
        if call.kwargs.get("chat_id") == GROUP_ID
        or (call.args and call.args[0] == GROUP_ID)
    ]
    assert alert_calls == []

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT forwarded_id FROM messages WHERE received_id = ? AND topic_id = ? ORDER BY id",
            (99, thread_id),
        ).fetchall()
    assert len(rows) == len(sent)
    assert [row[0] for row in rows] == [7000 + i for i in range(len(sent))]


def test_partial_long_text_resumes_after_stale_topic(handler_env):
    """Already-sent chunks must not be resent when topic is recreated mid-split."""
    handler, bot, cache, db_path = handler_env
    user_id = 606
    stale_thread_id = 333
    new_thread_id = 444
    _seed_topic(db_path, user_id, stale_thread_id)
    cache.set(f"chat_{user_id}_threadid", stale_thread_id)

    long_text = ("hello world\n" * 400)
    assert len(long_text) > 4096

    first = MagicMock(spec=Message)
    first.message_id = 8100
    resumed = MagicMock(spec=Message)
    resumed.message_id = 8101

    bot.send_message.side_effect = [
        first,
        make_api_error("Bad Request: TOPIC_DELETED"),
        resumed,
    ]

    message = make_message(
        message_id=101,
        chat_id=user_id,
        user_id=user_id,
        text=long_text,
    )

    with patch(
        "src.handlers.message_handler.create_forum_topic",
        return_value={"message_thread_id": new_thread_id},
    ), patch("src.handlers.message_handler.send_and_pin_user_info"):
        handler.handle_message(message)

    # 1 success on stale topic + 1 failure + 1 resume on new topic (not a full resend).
    assert bot.send_message.call_count == 3
    first_text = bot.send_message.call_args_list[0].kwargs["text"]
    resume_call = bot.send_message.call_args_list[2].kwargs
    assert resume_call["message_thread_id"] == new_thread_id
    assert resume_call.get("reply_to_message_id") is None
    assert first_text + resume_call["text"] == long_text
    assert len(resume_call["text"]) <= 4096
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT forwarded_id, topic_id FROM messages WHERE received_id = ? ORDER BY id",
            (101,),
        ).fetchall()
    # Only chunks delivered on the new topic are persisted.
    assert rows == [(8101, new_thread_id)]
    bot.forward_message.assert_not_called()


def test_denied_permission_blocks_forward(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 404
    permission_manager = PermissionManager(db_path=db_path, cache=cache)
    permission_manager.set_user_override(user_id, "link", "deny")
    handler.permission_manager = permission_manager

    message = make_message(
        message_id=55,
        chat_id=user_id,
        user_id=user_id,
        text="visit https://spam.example",
    )
    handler.handle_message(message)

    bot.send_message.assert_called()
    # Restriction reply goes to the private chat, not the forum group.
    assert all(
        call.kwargs.get("chat_id") != GROUP_ID and call.args[:1] != (GROUP_ID,)
        for call in bot.send_message.call_args_list
    )
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert count == 0


def test_excerpt_quote_detects_manual_partial_quote():
    message = make_message(quote=make_quote("only this part", position=4, is_manual=True))
    quote = MessageHandler._excerpt_quote(message)
    assert quote is not None
    assert quote.text == "only this part"

    auto_quote = make_message(quote=make_quote("preview", is_manual=False))
    assert MessageHandler._excerpt_quote(auto_quote) is None
    assert MessageHandler._excerpt_quote(make_message()) is None
    assert MessageHandler._excerpt_quote(make_message(quote=make_quote(""))) is None


def test_full_reply_from_user_maps_to_source_message(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 101
    thread_id = 555
    admin_msg_id = 11
    user_copy_id = 22
    _seed_topic(db_path, user_id, thread_id)
    cache.set(f"chat_{user_id}_threadid", thread_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
            (admin_msg_id, user_copy_id, thread_id, True),
        )
        conn.commit()

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 9002
    bot.send_message.return_value = forwarded

    source = make_message(message_id=user_copy_id, chat_id=user_id, user_id=0, text="whole source")
    message = make_message(
        message_id=80,
        chat_id=user_id,
        user_id=user_id,
        text="full reply",
        reply_to_message=source,
    )
    handler.handle_message(message)

    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == GROUP_ID
    assert kwargs["reply_to_message_id"] == admin_msg_id
    assert "reply_parameters" not in kwargs
    assert kwargs["text"] == "full reply"


def test_manual_quote_from_user_forwards_excerpt(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 101
    thread_id = 555
    admin_msg_id = 11
    user_copy_id = 22
    _seed_topic(db_path, user_id, thread_id)
    cache.set(f"chat_{user_id}_threadid", thread_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
            (admin_msg_id, user_copy_id, thread_id, True),
        )
        conn.commit()

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 9003
    bot.send_message.return_value = forwarded

    source = make_message(
        message_id=user_copy_id,
        chat_id=user_id,
        user_id=0,
        text="Hello world, this is a long message",
    )
    message = make_message(
        message_id=81,
        chat_id=user_id,
        user_id=user_id,
        text="got it",
        reply_to_message=source,
        quote=make_quote("this is a long", position=13, is_manual=True),
    )
    handler.handle_message(message)

    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == GROUP_ID
    assert "reply_to_message_id" not in kwargs
    reply = kwargs["reply_parameters"]
    assert reply.message_id == admin_msg_id
    assert reply.quote == "this is a long"
    assert reply.quote_position == 13
    assert kwargs["text"] == "got it"


def test_auto_quote_keeps_full_reply_behavior(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 101
    thread_id = 555
    admin_msg_id = 11
    user_copy_id = 22
    _seed_topic(db_path, user_id, thread_id)
    cache.set(f"chat_{user_id}_threadid", thread_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
            (admin_msg_id, user_copy_id, thread_id, True),
        )
        conn.commit()

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 9004
    bot.send_message.return_value = forwarded

    source = make_message(message_id=user_copy_id, chat_id=user_id, user_id=0, text="long original")
    message = make_message(
        message_id=82,
        chat_id=user_id,
        user_id=user_id,
        text="swipe reply",
        reply_to_message=source,
        quote=make_quote("long original", position=0, is_manual=False),
    )
    handler.handle_message(message)

    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["reply_to_message_id"] == admin_msg_id
    assert "reply_parameters" not in kwargs
    assert kwargs["text"] == "swipe reply"


def test_group_manual_quote_forwards_excerpt_to_user(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 303
    thread_id = 777
    user_msg_id = 11
    group_copy_id = 22
    _seed_topic(db_path, user_id, thread_id)

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 5002
    bot.send_message.return_value = forwarded

    source = make_message(
        message_id=group_copy_id,
        chat_id=GROUP_ID,
        chat_type="supergroup",
        user_id=0,
        text="user said several things here",
        message_thread_id=thread_id,
    )
    message = make_message(
        message_id=67,
        chat_id=GROUP_ID,
        chat_type="supergroup",
        user_id=999,
        text="about this part",
        message_thread_id=thread_id,
        reply_to_message=source,
        quote=make_quote("several things", position=10, is_manual=True),
    )
    handler.handle_message(message)

    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == user_id
    reply = kwargs["reply_parameters"]
    assert reply.message_id == user_msg_id
    assert reply.quote == "several things"
    assert reply.quote_position == 10
    assert kwargs["text"] == "about this part"


def test_unmapped_quote_embeds_excerpt_as_blockquote(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 101
    thread_id = 555
    _seed_topic(db_path, user_id, thread_id)
    cache.set(f"chat_{user_id}_threadid", thread_id)

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 9005
    bot.send_message.return_value = forwarded

    source = make_message(message_id=404, chat_id=user_id, user_id=0, text="not in mapping")
    message = make_message(
        message_id=83,
        chat_id=user_id,
        user_id=user_id,
        text="still reply",
        reply_to_message=source,
        quote=make_quote("not in", position=0, is_manual=True),
    )
    handler.handle_message(message)

    kwargs = bot.send_message.call_args.kwargs
    assert "reply_to_message_id" not in kwargs
    assert "reply_parameters" not in kwargs
    assert kwargs["text"] == "<blockquote>not in</blockquote>\nstill reply"


def test_invalid_quote_falls_back_to_embed_and_full_reply(handler_env):
    handler, bot, cache, db_path = handler_env
    user_id = 101
    thread_id = 555
    admin_msg_id = 11
    user_copy_id = 22
    _seed_topic(db_path, user_id, thread_id)
    cache.set(f"chat_{user_id}_threadid", thread_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
            (admin_msg_id, user_copy_id, thread_id, True),
        )
        conn.commit()

    forwarded = MagicMock(spec=Message)
    forwarded.message_id = 9006
    bot.send_message.side_effect = [
        make_api_error("Bad Request: QUOTE_TEXT_INVALID"),
        forwarded,
    ]

    source = make_message(message_id=user_copy_id, chat_id=user_id, user_id=0, text="original")
    message = make_message(
        message_id=84,
        chat_id=user_id,
        user_id=user_id,
        text="comment",
        reply_to_message=source,
        quote=make_quote("missing slice", position=2, is_manual=True),
    )
    handler.handle_message(message)

    assert bot.send_message.call_count == 2
    retry = bot.send_message.call_args.kwargs
    assert retry["reply_to_message_id"] == admin_msg_id
    assert "reply_parameters" not in retry
    assert retry["text"] == "<blockquote>missing slice</blockquote>\ncomment"
