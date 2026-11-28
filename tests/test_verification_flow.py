"""Mixed-version verification, replay and race regressions."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.handlers.command_handler import CommandHandler
from tests.test_captcha import env, USER_ID, GROUP_ID, check_message
from tests.helpers import make_message, make_api_error, make_quote


@pytest.fixture
def flow(env, monkeypatch):
    env.captcha.db_path = env.db_path
    env.handler.auto_response_manager.match_auto_response.return_value = None
    env.db.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, 555)", (USER_ID,))
    env.db.commit()
    env.cache.set(f"chat_{USER_ID}_threadid", 555)
    env.bot.copy_message.return_value = SimpleNamespace(message_id=900)
    env.command = CommandHandler(env.bot, GROUP_ID, env.db_path, env.cache, None,
                                 env.captcha, message_handler=env.handler)
    env.callback.command_handler = env.command
    env.cache.set("setting_tguard_api_url", "https://example.com")
    env.cache.set("setting_tguard_api_key", "key")
    env.bot.get_me.return_value.username = "example_bot"
    client = MagicMock()
    client.post.return_value.json.return_value = {
        "token": "owned-token", "verification_url": "https://example.com/verify?token=owned-token"}
    client.get.return_value.status_code = 200
    client.get.return_value.json.return_value = {"completed": True, "expired": False}
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    monkeypatch.setattr("src.utils.captcha.httpx.Client", factory)
    env.client = client
    return env


def start_tguard(flow):
    flow.cache.set("setting_captcha", "tguard")
    flow.handler.handle_message(make_message(message_id=1, text="original request"))


def test_legacy_server_confirm_button_replays_original(flow):
    start_tguard(flow)
    flow.client.post.assert_called_once()
    assert flow.client.post.call_args.kwargs["json"]["bot_username"] == "example_bot"
    flow.callback.handle_callback_query(SimpleNamespace(
        id="cb", from_user=SimpleNamespace(id=USER_ID), message=make_message(message_id=10),
        data=json.dumps({"action": "check_tguard"})))
    flow.bot.copy_message.assert_called_once()
    assert flow.bot.copy_message.call_args.kwargs["message_id"] == 1
    assert flow.db.execute("SELECT received_id, forwarded_id FROM messages").fetchall() == [(1, 900)]


@pytest.mark.parametrize("tag", ["tg_campaign", "tg_" + "X" * 32])
def test_tg_prefixed_source_tag_works_during_pending_verification(flow, tag):
    start_tguard(flow)
    flow.command.help_command(make_message(text=f"/start {tag}"), MagicMock())
    assert flow.db.execute("SELECT tag FROM user_tags").fetchone() == (tag,)
    flow.client.get.assert_not_called()
    flow.command.help_command(make_message(text="/start tg_owned-token"), MagicMock())
    assert flow.captcha.is_user_verified(USER_ID, flow.db)
    assert flow.db.execute("SELECT tag FROM user_tags").fetchone() == (tag,)


@pytest.mark.parametrize("method", ["button", "emoji"])
def test_keyboard_delivery_can_retry_after_telegram_failure_or_legacy_pending(flow, method):
    flow.cache.set("setting_captcha", json.dumps({"methods": [method]}))
    flow.captcha.set_pending(USER_ID, method)  # An old failed send left no keyboard ID.
    prompt = make_message(message_id=10)
    flow.bot.send_message.side_effect = [prompt, make_api_error("Service Unavailable", 503), prompt]
    flow.handler.handle_message(make_message(message_id=1))
    assert flow.captcha.get_pending(USER_ID) is None
    flow.bot.send_message.side_effect = None
    flow.handler.handle_message(make_message(message_id=2))
    assert flow.captcha.get_pending(USER_ID)["message_id"] == prompt.message_id
    assert [m.message_id for m in flow.captcha.take_messages(USER_ID)] == [2]


@pytest.mark.parametrize("method", ["button", "emoji", "tguard"])
def test_callback_ack_failure_still_verifies_and_delivers(flow, method):
    flow.cache.set("setting_captcha", json.dumps({"methods": [method]}))
    flow.handler.handle_message(make_message(message_id=1))
    flow.bot.answer_callback_query.side_effect = make_api_error("Query is too old")
    action = "check_tguard" if method == "tguard" else "verify_" + method
    data = {"action": action, "user_id": USER_ID, "i": flow.captcha.get_pending(USER_ID)["answer"]}
    flow.callback.handle_callback_query(SimpleNamespace(
        id="cb", from_user=SimpleNamespace(id=USER_ID), message=make_message(message_id=10),
        data=json.dumps(data)))
    assert flow.captcha.is_user_verified(USER_ID, flow.db)
    assert [c.kwargs["message_id"] for c in flow.bot.copy_message.call_args_list] == [1]


def test_ban_acquires_user_lock_before_sqlite_write(flow, monkeypatch):
    start_tguard(flow)
    user_lock = flow.captcha.user_lock(USER_ID)
    calls = []

    def check_lock_order(user_id):
        if not calls:
            with sqlite3.connect(flow.db_path, timeout=0) as other:
                other.execute("BEGIN IMMEDIATE")
        calls.append(user_id)
        return user_lock

    monkeypatch.setattr(flow.captcha, "user_lock", check_lock_order)
    monkeypatch.setattr("src.handlers.command_handler.close_forum_topic", MagicMock())
    flow.command.ban_user(make_message(chat_id=GROUP_ID, chat_type="supergroup", message_thread_id=555))
    assert calls and all(user_id == USER_ID for user_id in calls)
    assert flow.db.execute("SELECT user_id FROM blocked_users").fetchone() == (USER_ID,)
    assert not flow.captcha.take_messages(USER_ID)


def test_return_link_does_not_change_source_tag_and_concurrent_checks_send_once(flow):
    token = "A" * 32
    flow.client.post.return_value.json.return_value["token"] = token
    start_tguard(flow)
    flow.db.execute("INSERT INTO user_tags (user_id, tag) VALUES (?, 'source')", (USER_ID,))
    flow.db.commit()
    def confirm(_):
        flow.command.help_command(make_message(text=f"/start tg_{token}"), MagicMock())
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(confirm, range(2)))
    assert flow.client.get.call_count == 1
    assert flow.bot.copy_message.call_count == 1
    assert flow.db.execute("SELECT tag FROM user_tags").fetchone()[0] == "source"
    successes = [c for c in flow.bot.send_message.call_args_list
                 if len(c.args) > 1 and str(c.args[1]).startswith("✅ Verification successful")]
    assert len(successes) == 1


@pytest.mark.parametrize("reason", ["wrong-token", "expired", "banned", "stale-button"])
def test_invalid_confirmation_cannot_query_or_replay(flow, reason):
    start_tguard(flow)
    if reason == "expired":
        flow.now[0] += 601
    if reason == "banned":
        flow.db.execute("INSERT INTO blocked_users (user_id) VALUES (?)", (USER_ID,))
        flow.db.commit()
    flow.command.confirm_tguard(USER_ID, token="wrong" if reason == "wrong-token" else "owned-token",
                                message_id=9 if reason == "stale-button" else None)
    flow.client.get.assert_not_called()
    flow.bot.copy_message.assert_not_called()


def test_new_business_message_is_not_swallowed_on_fallback(flow):
    start_tguard(flow)
    flow.handler.handle_message(make_message(message_id=2, text="another question"))
    assert flow.bot.copy_message.call_args.kwargs["message_id"] == 1
    assert any(c.kwargs.get("text") == "another question" for c in flow.bot.send_message.call_args_list)


def test_pending_status_throttled_and_token_reused(flow):
    start_tguard(flow)
    flow.client.get.return_value.json.return_value = {"completed": False}
    for n in range(2, 8):
        flow.handler.handle_message(make_message(message_id=n))
    assert flow.client.get.call_count == 1
    assert flow.client.post.call_count == 1
    assert len(flow.captcha.take_messages(USER_ID)) == 5


def test_server_expiry_and_failed_create_rate_limit(flow):
    flow.client.post.return_value.json.return_value["expires_at"] = "2027-01-15T08:01:00+00:00"
    flow.captcha.generate_captcha(USER_ID, "tguard")
    assert flow.captcha.get_pending(USER_ID)["expires_at"] == flow.now[0] + 60
    flow.captcha.clear_pending(USER_ID)
    with pytest.raises(ValueError):
        flow.captcha.generate_captcha(USER_ID, "tguard")
    assert flow.client.post.call_count == 1


@pytest.mark.parametrize("status_code", [200, 404])
def test_invalidated_server_challenge_cannot_replay_in_a_later_challenge(flow, status_code):
    start_tguard(flow)
    flow.client.get.return_value.status_code = status_code
    flow.client.get.return_value.json.return_value = {"completed": True, "expired": True}
    flow.command.confirm_tguard(USER_ID, "owned-token")
    assert not flow.captcha.is_user_verified(USER_ID, flow.db)
    flow.bot.copy_message.assert_not_called()
    flow.cache.set("setting_captcha", "math")
    flow.handler.handle_message(make_message(message_id=2, text="new request"))
    answer = flow.captcha.get_pending(USER_ID)["answer"]
    flow.handler.handle_message(make_message(message_id=3, text=str(answer)))
    assert [c.kwargs["message_id"] for c in flow.bot.copy_message.call_args_list] == [2]


def test_math_answer_not_replayed_and_deleted_original_not_resurrected(flow):
    flow.handler.handle_message(make_message(message_id=1, text="original"))
    answer = str(flow.captcha.get_pending(USER_ID)["answer"])
    flow.bot.copy_message.side_effect = make_api_error("Bad Request: message to copy not found")
    flow.handler.handle_message(make_message(message_id=2, text=answer))
    assert flow.bot.copy_message.call_count == 1
    flow.bot.forward_message.assert_not_called()
    assert not any(c.kwargs.get("text") in (answer, "original") for c in flow.bot.send_message.call_args_list)


def test_replay_copies_original_without_deleted_reply_target(flow):
    flow.db.execute(
        "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (11, 22, 555, 1)"
    )
    flow.db.commit()
    source = make_message(message_id=22, user_id=0)
    flow.handler.handle_message(make_message(message_id=23, reply_to_message=source))
    answer = str(flow.captcha.get_pending(USER_ID)["answer"])
    flow.bot.copy_message.side_effect = [
        make_api_error("Bad Request: message to be replied not found"),
        SimpleNamespace(message_id=900),
    ]
    flow.handler.handle_message(make_message(message_id=24, text=answer))

    first, retry = (call.kwargs for call in flow.bot.copy_message.call_args_list)
    assert first["reply_to_message_id"] == 11
    assert first["allow_sending_without_reply"] is True
    assert retry == {key: value for key, value in first.items()
                     if key not in ("reply_to_message_id", "allow_sending_without_reply")}
    assert (retry["from_chat_id"], retry["message_id"], retry["message_thread_id"]) == (USER_ID, 23, 555)
    assert flow.db.execute("SELECT forwarded_id FROM messages WHERE received_id = 23").fetchall() == [(900,)]
    flow.bot.forward_message.assert_not_called()


@pytest.mark.parametrize("errors", [[], ["QUOTE_TEXT_INVALID"], ["QUOTE_TEXT_INVALID", "replied message not found"]])
def test_replay_attempts_native_quote_before_copying_without_it(flow, errors):
    message = make_message(message_id=23, quote=make_quote("slice", position=3))
    message._verification_replay = True
    forwarded = SimpleNamespace(message_id=900)
    flow.bot.copy_message.side_effect = [make_api_error(error) for error in errors] + [forwarded]

    result = flow.handler._send_message_by_type(message, message.text, None, GROUP_ID, 555, 11)

    assert result == [forwarded]
    calls = [call.kwargs for call in flow.bot.copy_message.call_args_list]
    assert len(calls) == len(errors) + 1
    reply = calls[0]["reply_parameters"]
    assert (reply.message_id, reply.quote, reply.quote_position) == (11, "slice", 3)
    if errors:
        assert calls[1]["reply_to_message_id"] == 11
        assert "reply_parameters" not in calls[1]
    if len(errors) == 2:
        assert "reply_parameters" not in calls[2] and "reply_to_message_id" not in calls[2]
    flow.bot.send_message.assert_not_called()


def test_button_chatter_keeps_challenge_and_edit_updates_snapshot(flow):
    flow.cache.set("setting_captcha", "button")
    flow.handler.handle_message(make_message(message_id=1))
    pending = flow.captcha.get_pending(USER_ID).copy()
    flow.handler.handle_message(make_message(message_id=2))
    assert flow.captcha.get_pending(USER_ID) == pending
    flow.command.handle_edit(make_message(message_id=1, text="edited"))
    assert [m.text for m in flow.captcha.take_messages(USER_ID)] == ["edited", "hello"]


def test_buffer_deadline_commands_switch_and_album_cap(flow):
    flow.captcha.set_pending(USER_ID, "button", ttl=10, message_id=10)
    flow.captcha.remember_message(make_message(text="/help"))
    assert not flow.captcha.take_messages(USER_ID)
    for n in range(25):
        message = make_message(message_id=n)
        message.media_group_id = "album"
        flow.captcha.remember_message(message)
    assert len(flow.captcha.take_messages(USER_ID)) == 20
    flow.captcha.remember_message(make_message())
    flow.now[0] += 11
    assert not flow.captcha.take_messages(USER_ID)
    flow.captcha.set_pending(USER_ID, "button", message_id=10)
    flow.cache.set("setting_captcha_replay", "disable")
    flow.captcha.remember_message(make_message())
    assert not flow.captcha.take_messages(USER_ID)


def test_replay_rechecks_current_permissions(flow):
    start_tguard(flow)
    flow.handler._get_denied_permissions = MagicMock(return_value=["text"])
    flow.handler._handle_permission_restriction = MagicMock(return_value=True)
    flow.command.confirm_tguard(USER_ID, "owned-token")
    flow.bot.copy_message.assert_not_called()
    flow.handler._handle_permission_restriction.assert_called_once()


def test_failed_create_is_throttled_and_failed_button_does_not_leave_pending(flow):
    flow.bot.send_message.side_effect = RuntimeError("Telegram unavailable")
    with pytest.raises(ValueError):
        flow.captcha.generate_captcha(USER_ID, "tguard")
    assert flow.captcha.get_pending(USER_ID) is None
    with pytest.raises(ValueError):
        flow.captcha.generate_captcha(USER_ID, "tguard")
    assert flow.client.post.call_count == 1


def test_replay_uses_spam_topic_when_detector_rejects(flow):
    start_tguard(flow)
    flow.cache.set("spam_topic_id", 777)
    flow.handler.spam_detector_manager = MagicMock()
    flow.handler.spam_detector_manager.detect_spam.return_value = (True, {})
    flow.command.confirm_tguard(USER_ID, "owned-token")
    assert flow.bot.copy_message.call_args.kwargs["message_thread_id"] == 777
    assert not flow.db.execute("SELECT 1 FROM messages").fetchall()
