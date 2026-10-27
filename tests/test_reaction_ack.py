"""Tests for the short-lived ❤ reaction on processed messages."""

import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.handlers.message_handler import MessageHandler
from src.utils.reaction_ack import ReactionAck
from tests.helpers import init_core_db, make_api_error, make_cache, make_message


GROUP_ID = -1001234567890
USER_ID = 101
THREAD_ID = 55
DELAY = 0.05


def _reactions(bot):
    """List of (chat_id, message_id, emojis) for every set_message_reaction call."""
    return [(c.kwargs["chat_id"], c.kwargs["message_id"], [r.emoji for r in c.kwargs["reaction"]])
            for c in bot.set_message_reaction.call_args_list]


def _wait_for_timers():
    time.sleep(DELAY * 4)


def test_reaction_added_then_removed():
    bot = MagicMock()
    ack = ReactionAck(bot, delay=DELAY)
    ack.ack(USER_ID, 1)
    assert _reactions(bot) == [(USER_ID, 1, ["❤"])]
    _wait_for_timers()
    assert _reactions(bot) == [(USER_ID, 1, ["❤"]), (USER_ID, 1, [])]


def test_release_keeps_the_next_reaction():
    bot = MagicMock()
    ack = ReactionAck(bot, delay=DELAY)
    ack.ack(USER_ID, 1)
    ack.release(USER_ID, 1)
    _wait_for_timers()
    assert _reactions(bot) == [(USER_ID, 1, ["❤"])]


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_core_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, ?)", (USER_ID, THREAD_ID))
        conn.commit()
    cache = make_cache({"setting_captcha": "disable"})
    bot = MagicMock()
    counter = iter(range(1000, 2000))
    bot.send_message.side_effect = lambda *args, **kwargs: SimpleNamespace(message_id=next(counter))
    auto_response = MagicMock()
    auto_response.match_auto_response.return_value = None
    permission_manager = MagicMock()
    permission_manager.resolve_permission.return_value = True
    handler = MessageHandler(bot=bot, group_id=GROUP_ID, db_path=db_path, cache=cache,
                             captcha_manager=MagicMock(), auto_response_manager=auto_response,
                             permission_manager=permission_manager,
                             reaction_ack=ReactionAck(bot, delay=DELAY))
    return SimpleNamespace(bot=bot, cache=cache, db_path=db_path, handler=handler)


def _user_message(message_id):
    return make_message(message_id=message_id, chat_id=USER_ID, user_id=USER_ID, text="hi")


def _admin_message(message_id):
    return make_message(message_id=message_id, chat_id=GROUP_ID, chat_type="supergroup",
                        message_thread_id=THREAD_ID, user_id=1, text="reply")


def test_user_and_admin_messages_get_temporary_heart(env):
    env.handler.handle_message(_user_message(1))
    env.handler.handle_message(_admin_message(50))
    _wait_for_timers()
    assert sorted(_reactions(env.bot)) == sorted([
        (USER_ID, 1, ["❤"]), (USER_ID, 1, []),
        (GROUP_ID, 50, ["❤"]), (GROUP_ID, 50, []),
    ])


def test_blocked_user_message_gets_no_heart(env):
    with sqlite3.connect(env.db_path) as conn:
        conn.execute("INSERT INTO blocked_users (user_id) VALUES (?)", (USER_ID,))
        conn.commit()
    env.handler.handle_message(_user_message(1))
    _wait_for_timers()
    env.bot.set_message_reaction.assert_not_called()


def test_mirrored_reaction_is_not_wiped_by_heart_removal(env):
    """An admin reacting within the ❤ window replaces the ❤ on the user's message for good."""
    from src.handlers.command_handler import CommandHandler

    commands = CommandHandler(env.bot, GROUP_ID, env.db_path, env.cache, None, MagicMock(),
                              message_handler=env.handler)
    env.handler.handle_message(_user_message(1))  # forwarded into the group as message 1000
    commands.handle_reaction(SimpleNamespace(chat=SimpleNamespace(id=GROUP_ID), message_id=1000,
                                             new_reaction=[SimpleNamespace(emoji="👍")]))
    _wait_for_timers()

    user_side = [c.kwargs["reaction"] for c in env.bot.set_message_reaction.call_args_list
                 if c.kwargs["chat_id"] == USER_ID]
    assert [[r.emoji for r in reaction] for reaction in user_side] == [["❤"], ["👍"]]


@pytest.mark.parametrize("failure", [False, True])
def test_mirror_waits_for_inflight_ack_and_is_not_removed(monkeypatch, failure):
    """A mirror arriving before the heart API returns must be applied afterwards."""
    entered, response, mirror_started, mirrored = (threading.Event() for _ in range(4))
    calls = []
    timers = []
    errors = []

    def set_reaction(**kwargs):
        emojis = [r.emoji for r in kwargs["reaction"]]
        calls.append(emojis)
        if emojis == ["❤"]:
            entered.set()
            if not response.wait(3):
                raise TimeoutError("ack response was not released")
            if failure:
                raise make_api_error("Bad Request: reaction is unavailable")

    def timer(delay, function, args):
        timers.append((function, args))
        return MagicMock()

    monkeypatch.setattr("src.utils.reaction_ack.threading.Timer", timer)
    bot = MagicMock()
    bot.set_message_reaction.side_effect = set_reaction
    ack = ReactionAck(bot)

    def acknowledge():
        try:
            ack.ack(USER_ID, 1)
        except Exception as error:
            errors.append(error)

    def mirror():
        try:
            mirror_started.set()
            ack.release(USER_ID, 1)
            bot.set_message_reaction(chat_id=USER_ID, message_id=1,
                                     reaction=[SimpleNamespace(emoji="👍")])
            mirrored.set()
        except Exception as error:
            errors.append(error)

    ack_thread = threading.Thread(target=acknowledge, daemon=True)
    mirror_thread = threading.Thread(target=mirror, daemon=True)
    ack_thread.start()
    try:
        assert entered.wait(3)
        mirror_thread.start()
        assert mirror_started.wait(3)
        assert not mirrored.wait(0.05), "mirror overtook the in-flight heart request"
    finally:
        response.set()
        ack_thread.join(timeout=3)
        if mirror_thread.ident is not None:
            mirror_thread.join(timeout=3)
    assert not ack_thread.is_alive() and not mirror_thread.is_alive()
    assert not errors
    assert mirrored.is_set()
    assert len(timers) == (0 if failure else 1)
    for callback, args in timers:
        callback(*args)
    assert calls == [["❤"], ["👍"]]
