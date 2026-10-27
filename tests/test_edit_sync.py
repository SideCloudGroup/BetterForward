"""Tests for syncing edited messages and reactions to forwarded copies."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.handlers.command_handler import CommandHandler
from tests.helpers import init_core_db, make_api_error, make_cache, make_message


GROUP_ID = -1001234567890


@pytest.fixture
def edit_env(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_core_db(db_path)
    bot = MagicMock()
    bot.send_message.side_effect = lambda **kwargs: SimpleNamespace(message_id=900)
    handler = CommandHandler(bot, GROUP_ID, db_path, make_cache(), None, MagicMock())
    return handler, bot, db_path


def _seed(db_path: str, topics=(), messages=()):
    with sqlite3.connect(db_path) as conn:
        conn.executemany("INSERT INTO topics (user_id, thread_id) VALUES (?, ?)", topics)
        conn.executemany(
            "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
            messages)
        conn.commit()


def _forwarded_ids(db_path: str, received_id: int):
    with sqlite3.connect(db_path) as conn:
        return [row[0] for row in conn.execute(
            "SELECT forwarded_id FROM messages WHERE received_id = ? ORDER BY id", (received_id,))]


def test_edit_caption_of_media_does_not_crash(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(10, 201, 55, False)])

    message = make_message(message_id=10, chat_id=101, text=None, content_type="photo",
                           caption="new <caption>")
    handler.handle_edit(message)

    bot.edit_message_caption.assert_called_once()
    kwargs = bot.edit_message_caption.call_args.kwargs
    assert kwargs["chat_id"] == GROUP_ID
    assert kwargs["message_id"] == 201
    assert kwargs["caption"].startswith("new &lt;caption&gt;")
    assert kwargs["parse_mode"] == "HTML"
    bot.edit_message_text.assert_not_called()


def test_edit_scoped_to_sender_topic(edit_env):
    """Private-chat message ids collide across users; only the sender's copy is edited."""
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55), (102, 66)],
          messages=[(10, 301, 66, False), (10, 201, 55, False)])

    handler.handle_edit(make_message(message_id=10, chat_id=101, user_id=101, text="fixed"))

    bot.edit_message_text.assert_called_once()
    assert bot.edit_message_text.call_args.kwargs["message_id"] == 201


def test_edit_shrinking_long_text_deletes_surplus_chunks(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(10, 201, 55, False), (10, 202, 55, False)])

    handler.handle_edit(make_message(message_id=10, chat_id=101, text="short now"))

    bot.edit_message_text.assert_called_once()
    assert bot.edit_message_text.call_args.kwargs["message_id"] == 201
    bot.delete_message.assert_called_once_with(chat_id=GROUP_ID, message_id=202)
    assert _forwarded_ids(db_path, 10) == [201]


def test_edit_growing_text_appends_chunks(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(10, 201, 55, False)])

    handler.handle_edit(make_message(message_id=10, chat_id=101, text="word " * 1500))

    bot.edit_message_text.assert_called_once()
    assert len(bot.edit_message_text.call_args.kwargs["text"]) <= 4096
    bot.send_message.assert_called_once()
    sent = bot.send_message.call_args.kwargs
    assert sent["message_thread_id"] == 55
    assert sent["reply_to_message_id"] == 201
    assert _forwarded_ids(db_path, 10) == [201, 900]


def test_edit_from_group_goes_to_user(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(20, 401, 55, True)])

    handler.handle_edit(make_message(message_id=20, chat_id=GROUP_ID, chat_type="supergroup",
                                     message_thread_id=55, user_id=1, text="admin edit"))

    kwargs = bot.edit_message_text.call_args.kwargs
    assert kwargs["chat_id"] == 101
    assert kwargs["message_id"] == 401


def test_reaction_from_user_scoped_to_sender_topic(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55), (102, 66)],
          messages=[(10, 301, 66, False), (10, 201, 55, False)])

    reaction = SimpleNamespace(chat=SimpleNamespace(id=101), message_id=10, new_reaction=["👍"])
    handler.handle_reaction(reaction)

    bot.set_message_reaction.assert_called_once_with(chat_id=GROUP_ID, message_id=201, reaction=["👍"])


def test_edit_handler_registered_for_caption_edits():
    """telebot defaults edited_message_handler to text only; media caption edits must be routed too."""
    from telebot import TeleBot

    from src.bot import TGBot

    bot = TGBot.__new__(TGBot)
    bot.bot = TeleBot("0000000000:TEST_TOKEN_FOR_UNIT_TESTS", threaded=False)
    bot.command_handler = MagicMock()
    bot.admin_handler = MagicMock()
    bot.callback_handler = MagicMock()
    bot._register_handlers()

    content_types = bot.bot.edited_message_handlers[0]["filters"]["content_types"]
    for content_type in ("text", "photo", "video", "document", "audio", "voice", "animation"):
        assert content_type in content_types


def test_replaced_photo_is_synced_with_new_file(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(10, 201, 55, False)])

    photo = [SimpleNamespace(file_id="SMALL"), SimpleNamespace(file_id="NEW_PHOTO")]
    handler.handle_edit(make_message(message_id=10, chat_id=101, text=None, content_type="photo",
                                     caption="new pic", photo=photo))

    kwargs = bot.edit_message_media.call_args.kwargs
    assert (kwargs["chat_id"], kwargs["message_id"]) == (GROUP_ID, 201)
    assert kwargs["media"].type == "photo"
    assert kwargs["media"].media == "NEW_PHOTO"
    assert kwargs["media"].caption.startswith("new pic")
    assert kwargs["media"].parse_mode == "HTML"
    bot.edit_message_caption.assert_not_called()


def test_admin_replacing_photo_with_video_reaches_user(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(20, 401, 55, True)])

    handler.handle_edit(make_message(message_id=20, chat_id=GROUP_ID, chat_type="supergroup",
                                     message_thread_id=55, user_id=1, text=None, content_type="video",
                                     caption=None, video=SimpleNamespace(file_id="NEW_VIDEO")))

    kwargs = bot.edit_message_media.call_args.kwargs
    assert (kwargs["chat_id"], kwargs["message_id"]) == (101, 401)
    assert (kwargs["media"].type, kwargs["media"].media) == ("video", "NEW_VIDEO")


def test_voice_edit_falls_back_to_caption(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(10, 201, 55, False)])

    handler.handle_edit(make_message(message_id=10, chat_id=101, text=None, content_type="voice",
                                     caption="note", voice=SimpleNamespace(file_id="VOICE")))

    bot.edit_message_media.assert_not_called()
    assert bot.edit_message_caption.call_args.kwargs["caption"].startswith("note")


@pytest.mark.parametrize("content", [
    {"text": None, "content_type": "video", "video": SimpleNamespace(file_id="NEW_VIDEO")},
    {"text": None, "content_type": "photo", "caption": "https://example.com",
     "photo": [SimpleNamespace(file_id="PHOTO")]},
    {"text": "https://example.com"},
])
def test_user_edits_recheck_permissions(edit_env, content):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(10, 201, 55, False)])
    handler.permission_manager = MagicMock()
    handler.permission_manager.resolve_permission.side_effect = lambda user, key: key not in ("video", "link")

    handler.handle_edit(make_message(message_id=10, chat_id=101, **content))

    bot.edit_message_media.assert_not_called()
    bot.edit_message_caption.assert_not_called()
    bot.edit_message_text.assert_not_called()
    bot.send_message.assert_not_called()
    assert _forwarded_ids(db_path, 10) == [201]


def test_blocked_user_cannot_edit_existing_forward(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(10, 201, 55, False)])
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO blocked_users (user_id) VALUES (101)")
    handler.handle_edit(make_message(message_id=10, chat_id=101, text="blocked edit"))
    bot.edit_message_text.assert_not_called()


def test_user_permissions_do_not_restrict_admin_edits(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)], messages=[(20, 401, 55, True)])
    handler.permission_manager = MagicMock()
    handler.permission_manager.resolve_permission.return_value = False
    handler.handle_edit(make_message(message_id=20, chat_id=GROUP_ID, chat_type="supergroup",
                                    message_thread_id=55, user_id=1, text="https://example.com"))
    bot.edit_message_text.assert_called_once()
    handler.permission_manager.resolve_permission.assert_not_called()


@pytest.mark.parametrize("description, code", [
    ("Too Many Requests", 429),
    ("Bad Request: message can't be deleted", 400),
])
def test_shrinking_edit_keeps_failed_deletion_mapped_for_retry(edit_env, description, code):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)],
          messages=[(10, 201, 55, False), (10, 202, 55, False)])
    bot.delete_message.side_effect = make_api_error(description, code)
    message = make_message(message_id=10, chat_id=101, text="short")

    handler.handle_edit(message)
    assert _forwarded_ids(db_path, 10) == [201, 202]

    bot.delete_message.side_effect = None
    handler.handle_edit(message)
    assert _forwarded_ids(db_path, 10) == [201]
    assert bot.delete_message.call_count == 2


def test_shrinking_edit_removes_mapping_for_already_deleted_chunk(edit_env):
    handler, bot, db_path = edit_env
    _seed(db_path, topics=[(101, 55)],
          messages=[(10, 201, 55, False), (10, 202, 55, False)])
    bot.delete_message.side_effect = make_api_error("Bad Request: message to delete not found")
    handler.handle_edit(make_message(message_id=10, chat_id=101, text="short"))
    assert _forwarded_ids(db_path, 10) == [201]
