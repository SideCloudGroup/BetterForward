"""Mixed-version verification, replay and race regressions."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.handlers.command_handler import CommandHandler
from tests.test_captcha import env, USER_ID, GROUP_ID, check_message
from tests.helpers import make_message, make_api_error


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


def test_return_link_does_not_change_source_tag_and_concurrent_checks_send_once(flow):
    start_tguard(flow)
    flow.db.execute("INSERT INTO user_tags (user_id, tag) VALUES (?, 'source')", (USER_ID,))
    flow.db.commit()
    def confirm(_):
        flow.command.help_command(make_message(text="/start tg_owned-token"), MagicMock())
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


def test_expired_server_result_is_not_accepted(flow):
    start_tguard(flow)
    flow.client.get.return_value.json.return_value = {"completed": True, "expired": True}
    flow.command.confirm_tguard(USER_ID, "owned-token")
    assert not flow.captcha.is_user_verified(USER_ID, flow.db)
    flow.bot.copy_message.assert_not_called()


def test_math_answer_not_replayed_and_deleted_original_not_resurrected(flow):
    flow.handler.handle_message(make_message(message_id=1, text="original"))
    answer = str(flow.captcha.get_pending(USER_ID)["answer"])
    flow.bot.copy_message.side_effect = make_api_error("Bad Request: message to copy not found")
    flow.handler.handle_message(make_message(message_id=2, text=answer))
    assert flow.bot.copy_message.call_count == 1
    flow.bot.forward_message.assert_not_called()
    assert not any(c.kwargs.get("text") in (answer, "original") for c in flow.bot.send_message.call_args_list)


def test_button_chatter_keeps_challenge_and_edit_updates_snapshot(flow):
    flow.cache.set("setting_captcha", "button")
    flow.handler.handle_message(make_message(message_id=1))
    pending = flow.captcha.get_pending(USER_ID).copy()
    flow.handler.handle_message(make_message(message_id=2))
    assert flow.captcha.get_pending(USER_ID) == pending
    flow.command.handle_edit(make_message(message_id=1, text="edited"))
    assert [m.text for m in flow.captcha.take_messages(USER_ID)] == ["edited", "hello"]


def test_buffer_deadline_commands_switch_and_album_cap(flow):
    flow.captcha.set_pending(USER_ID, "button", ttl=10)
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
    flow.captcha.set_pending(USER_ID, "button")
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
