"""Regression tests for large reply template menus and previews."""

import html
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.handlers.admin_handler import AdminHandler
from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from tests.helpers import init_core_db, make_cache, make_message


GROUP_ID = -1001234567890


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "templates.db")
    init_core_db(db_path)
    bot = MagicMock()
    bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
    cache = make_cache()
    admin = AdminHandler(bot, GROUP_ID, db_path, cache, MagicMock(), MagicMock())
    commands = CommandHandler(bot, GROUP_ID, db_path, cache, None, MagicMock())
    callbacks = CallbackHandler(bot, GROUP_ID, admin, commands, MagicMock(), db_path=db_path)
    return SimpleNamespace(db_path=db_path, bot=bot, admin=admin, commands=commands,
                           callbacks=callbacks)


def _message(**kwargs):
    return make_message(chat_id=GROUP_ID, chat_type="supergroup", user_id=1, **kwargs)


def _seed(env, count, content="x" * 80):
    with sqlite3.connect(env.db_path) as db:
        db.executemany("INSERT INTO reply_templates (content) VALUES (?)", [(content,)] * count)


def _buttons(env):
    markup = env.bot.edit_message_text.call_args.kwargs["reply_markup"]
    return [json.loads(button.callback_data) for row in markup.keyboard for button in row]


def _click(env, data):
    env.callbacks.handle_callback_query(SimpleNamespace(
        id="callback", data=json.dumps(data), from_user=SimpleNamespace(id=1), message=_message()))


@pytest.mark.parametrize("length", [4080, 4096])
def test_long_template_detail_fits_limit_without_changing_content(env, length):
    content = "a" * length
    _seed(env, 1, content)

    env.admin.select_reply_template(_message(), 1)

    text = env.bot.edit_message_text.call_args.args[0]
    assert len(text) <= 4096
    assert text.endswith("…\n\nSelect an action:")
    assert any(button["action"] == "delete_reply_template" for button in _buttons(env))
    with sqlite3.connect(env.db_path) as db:
        assert db.execute("SELECT content FROM reply_templates").fetchone()[0] == content


def test_many_templates_are_paginated_and_callbacks_preserve_numbering(env):
    _seed(env, 50)
    env.admin.reply_templates_menu(_message())
    seen_ids = []
    for page in range(1, 6):
        text = env.bot.edit_message_text.call_args.args[0]
        assert len(text) <= 4096
        assert f"Page: {page}/5" in text
        buttons = _buttons(env)
        selections = [b for b in buttons if b["action"] == "select_reply_template"]
        assert len(selections) == 10
        seen_ids.extend(b["id"] for b in selections)
        assert all(b["page"] == page for b in selections)
        if page < 5:
            _click(env, next(b for b in buttons if b["action"] == "reply_templates"
                             and b["page"] == page + 1))
    assert seen_ids == list(range(1, 51))
    assert "41. " in text and "50. " in text

    _click(env, selections[-1])
    back = next(b for b in _buttons(env) if b["action"] == "reply_templates")
    assert back["page"] == 5
    _click(env, back)
    assert "Page: 5/5" in env.bot.edit_message_text.call_args.args[0]


def test_deleting_last_template_on_page_clamps_to_remaining_page(env):
    _seed(env, 11)
    _click(env, {"action": "delete_reply_template", "id": 11, "page": 2})
    text = env.bot.edit_message_text.call_args.args[0]
    assert "Page: 1/1" in text
    assert "10. " in text
    with sqlite3.connect(env.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM reply_templates").fetchone()[0] == 10


def test_topic_template_listing_splits_without_losing_entries(env):
    content = "<template>" * 8
    _seed(env, 60, content)
    env.commands.handle_template_reply(_message(text="/r", message_thread_id=55))
    replies = env.bot.reply_to.call_args_list
    assert len(replies) > 1
    assert all(len(call.args[1]) <= 4096 for call in replies)
    listing = html.unescape("".join(call.args[1] for call in replies))
    for number in range(1, 61):
        assert f"{number}. {content}" in listing
    env.bot.send_message.assert_not_called()
