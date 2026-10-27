"""Verification expiry and challenge lifecycle regressions."""

import importlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from diskcache import Cache

from src.database import Database
from src.handlers.admin_handler import AdminHandler
from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.handlers.message_handler import MessageHandler
from src.utils.captcha import CaptchaManager, parse_captcha_config, pick_captcha_type
from tests.helpers import init_core_db, make_cache, make_message

GROUP_ID = -1001234567890
USER_ID = 101
DAY = 86400


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = [1800000000.0]
    monkeypatch.setattr("src.utils.captcha.time.time", lambda: now[0])
    db_path = str(tmp_path / "storage.db")
    init_core_db(db_path)
    cache = make_cache({"setting_captcha": "math"})
    bot = MagicMock()
    bot.send_message.return_value = make_message(message_id=10)
    bot.get_chat_member.return_value = SimpleNamespace(status="administrator")
    captcha = CaptchaManager(bot, cache, GROUP_ID)
    handler = MessageHandler(bot, GROUP_ID, db_path, cache, captcha, MagicMock())
    callback = CallbackHandler(bot, GROUP_ID, MagicMock(), MagicMock(), captcha, db_path=db_path)
    with sqlite3.connect(db_path) as db:
        yield SimpleNamespace(now=now, db=db, db_path=db_path, cache=cache, bot=bot,
                              captcha=captcha, handler=handler, callback=callback)


def check_message(env, **kwargs):
    return env.handler._check_captcha(make_message(**kwargs), env.db.cursor(), env.db)


def click(env, action, message_id=10, **data):
    user_key = "u" if action == "verify_emoji" else "user_id"
    call = SimpleNamespace(id="cb", from_user=SimpleNamespace(id=USER_ID),
                           message=make_message(message_id=message_id),
                           data=json.dumps({"action": action, user_key: USER_ID, **data}))
    env.callback.handle_callback_query(call)


@pytest.mark.parametrize("days", [None, "0", ""])
def test_default_verification_is_permanent(env, days):
    env.cache.set("setting_captcha_verification_days", days)
    env.captcha.set_user_verified(USER_ID, env.db)
    env.now[0] += 3650 * DAY
    assert check_message(env)
    env.cache.delete(f"verified_{USER_ID}")
    assert env.captcha.is_user_verified(USER_ID, env.db)


def test_expiry_boundary_and_setting_changes_apply_to_cached_users(env):
    env.captcha.set_user_verified(USER_ID, env.db)
    env.cache.set("setting_captcha_verification_days", "7")
    env.now[0] += 7 * DAY - 1
    assert env.captcha.is_user_verified(USER_ID, env.db)
    env.now[0] += 1
    assert not env.captcha.is_user_verified(USER_ID, env.db)
    env.cache.set("setting_captcha_verification_days", "30")
    assert env.captcha.is_user_verified(USER_ID, env.db)
    env.cache.set("setting_captcha_verification_days", "1")
    assert not env.captcha.is_user_verified(USER_ID, env.db)
    env.cache.set("setting_captcha_verification_days", "0")
    assert env.captcha.is_user_verified(USER_ID, env.db)


def test_legacy_boolean_cache_cannot_bypass_expiry(env):
    env.captcha.set_user_verified(USER_ID, env.db)
    env.cache.set(f"verified_{USER_ID}", True)
    env.cache.set("setting_captcha_verification_days", "1")
    env.now[0] += DAY
    assert not env.captcha.is_user_verified(USER_ID, env.db)
    env.cache.set("verified_999", True)
    assert not env.captcha.is_user_verified(999, env.db)


@pytest.mark.parametrize("method", ["math", "button", "emoji", "qa", "sticker", "tguard"])
def test_expired_user_reverifies_with_each_method(env, method, monkeypatch):
    config = {"methods": [method], "qa": [{"question": "Colour?", "answers": ["Blue"]}],
              "sticker": {"mode": "match", "file_id": "file", "file_unique_id": "unique"}}
    env.cache.set("setting_captcha", json.dumps(config))
    env.cache.set("setting_captcha_verification_days", "1")
    env.cache.set("setting_tguard_api_url", "https://example.com")
    env.cache.set("setting_tguard_api_key", "test")
    client = MagicMock()
    client.post.return_value.json.return_value = {"token": "token", "verification_url": "https://example.com/v"}
    client.get.return_value.status_code = 200
    client.get.return_value.json.return_value = {"completed": True}
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    monkeypatch.setattr("src.utils.captcha.httpx.Client", factory)
    env.captcha.set_user_verified(USER_ID, env.db)
    env.now[0] += DAY
    assert not check_message(env)
    pending = env.captcha.get_pending(USER_ID)
    assert pending["type"] == method
    if method == "math":
        assert not check_message(env, text=str(pending["answer"]))
    elif method == "qa":
        assert not check_message(env, text="  bLuE  ")
    elif method == "sticker":
        assert not check_message(env, text=None, content_type="sticker",
                                 sticker=SimpleNamespace(file_unique_id="unique"))
    elif method == "button":
        click(env, "verify_button")
    elif method == "emoji":
        click(env, "verify_emoji", i=pending["answer"])
    else:
        assert check_message(env)
        assert client.post.call_count == 1
    assert env.captcha.get_pending(USER_ID) is None
    assert check_message(env)
    assert env.db.execute("SELECT verified_at FROM verified_users WHERE user_id = ?",
                          (USER_ID,)).fetchone()[0] == env.now[0]
    env.now[0] += DAY
    assert not env.captcha.is_user_verified(USER_ID, env.db)


def test_manual_verify_renews_and_revocation_clears_status(env):
    handler = CommandHandler(env.bot, GROUP_ID, env.db_path, env.cache, None, env.captcha)
    env.db.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, 555)", (USER_ID,))
    env.db.commit()
    env.captcha.set_user_verified(USER_ID, env.db)
    env.now[0] += 30 * DAY
    handler.handle_verify(make_message(chat_id=GROUP_ID, chat_type="supergroup", user_id=1,
                                       message_thread_id=555, text="/verify true"))
    env.cache.set("setting_captcha_verification_days", "7")
    assert env.captcha.is_user_verified(USER_ID, env.db)
    assert env.db.execute("SELECT verified_at FROM verified_users").fetchone()[0] == env.now[0]
    handler.handle_verify(make_message(chat_id=GROUP_ID, chat_type="supergroup", user_id=1,
                                       message_thread_id=555, text="/verify false"))
    assert not env.captcha.is_user_verified(USER_ID, env.db)


@pytest.mark.parametrize("method", ["button", "emoji"])
def test_stale_callback_cannot_replace_active_challenge(env, method):
    env.captcha.generate_captcha(USER_ID, method)
    old_answer = env.captcha.get_pending(USER_ID)["answer"]
    env.captcha.set_pending(USER_ID, "qa", ["secret"])
    env.bot.reset_mock()
    click(env, "verify_" + method, i=old_answer)
    assert not env.captcha.is_user_verified(USER_ID, env.db)
    assert env.captcha.get_pending(USER_ID)["type"] == "qa"
    assert env.bot.answer_callback_query.call_args.kwargs["show_alert"]
    env.bot.send_message.assert_not_called()


@pytest.mark.parametrize("method", ["button", "emoji"])
def test_old_message_and_replayed_callback_rejected(env, method):
    env.captcha.generate_captcha(USER_ID, method)
    old_answer = env.captcha.get_pending(USER_ID)["answer"]
    env.bot.send_message.return_value = make_message(message_id=11)
    env.captcha.generate_captcha(USER_ID, method)
    click(env, "verify_" + method, i=old_answer)
    assert not env.captcha.is_user_verified(USER_ID, env.db)
    pending = env.captcha.get_pending(USER_ID)
    click(env, "verify_" + method, message_id=11, i=pending["answer"])
    assert env.captcha.is_user_verified(USER_ID, env.db)
    verified_at = env.db.execute("SELECT verified_at FROM verified_users").fetchone()[0]
    env.now[0] += DAY
    click(env, "verify_" + method, message_id=11, i=pending["answer"])
    assert env.db.execute("SELECT verified_at FROM verified_users").fetchone()[0] == verified_at


def test_expired_button_challenge_rejected_with_real_cache(tmp_path):
    with Cache(str(tmp_path / "cache")) as cache:
        captcha = CaptchaManager(MagicMock(), cache)
        captcha.set_pending(USER_ID, "button", ttl=-1, message_id=10)
        assert not captcha.is_current_callback(USER_ID, "button", 10)


def test_migration_preserves_legacy_users_and_is_repeatable(tmp_path, monkeypatch):
    db_path = str(tmp_path / "old.db")
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE verified_users (user_id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO verified_users VALUES (?)", (USER_ID,))
        db.execute("CREATE TABLE settings (key TEXT UNIQUE, value TEXT)")
    module = importlib.import_module("db_migrate.20261001_verification_expiry")
    monkeypatch.setattr(module.time, "time", lambda: 1800000000.0)
    module.upgrade(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT user_id, verified_at FROM verified_users").fetchone() == (USER_ID, 1800000000.0)
        assert db.execute("SELECT value FROM settings").fetchone()[0] == "0"
        db.execute("UPDATE settings SET value = '7'")
    monkeypatch.setattr(module.time, "time", lambda: 1900000000.0)
    module.upgrade(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT verified_at FROM verified_users").fetchone()[0] == 1800000000.0
        assert db.execute("SELECT value FROM settings").fetchall() == [("7",)]


def test_fresh_database_runs_all_migrations(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    assert database.get_setting("captcha_verification_days") == "0"
    with database.get_connection() as db:
        assert "verified_at" in {row[1] for row in db.execute("PRAGMA table_info(verified_users)")}


@pytest.fixture
def admin(env):
    database = MagicMock()
    handler = AdminHandler(env.bot, GROUP_ID, env.db_path, env.cache, database, MagicMock())
    handler.set_operator(1)
    return handler


@pytest.mark.parametrize("text", ["0", "7", "30", " 7 "])
def test_admin_saves_valid_days(env, admin, text):
    message = make_message(chat_id=GROUP_ID, chat_type="supergroup", user_id=1, text=text)
    admin.process_captcha_verification_days(message)
    expected = str(int(text))
    admin.database.set_setting.assert_called_once_with("captcha_verification_days", expected)
    assert env.cache.get("setting_captcha_verification_days") == expected


@pytest.mark.parametrize("text", ["-1", "1.5", "abc", "", "+7", "7 days"])
def test_admin_retries_invalid_days(env, admin, text):
    admin.process_captcha_verification_days(make_message(chat_id=GROUP_ID, user_id=1, text=text))
    admin.database.set_setting.assert_not_called()
    env.bot.register_next_step_handler.assert_called_once()


def test_admin_can_cancel_and_other_users_cannot_change_days(env, admin):
    admin.process_captcha_verification_days(make_message(chat_id=GROUP_ID, user_id=2, text="7"))
    admin.database.set_setting.assert_not_called()
    admin.process_captcha_verification_days(make_message(chat_id=GROUP_ID, user_id=1, text="/cancel"))
    admin.database.set_setting.assert_not_called()


def test_random_selection_excludes_unconfigured_methods(env, monkeypatch):
    config = parse_captcha_config({"methods": ["math", "emoji", "qa", "sticker", "tguard"],
                                   "sticker": {"mode": "match"}})
    choose = MagicMock(return_value="emoji")
    monkeypatch.setattr("src.utils.captcha.random.choice", choose)
    assert pick_captcha_type(config, env.cache) == "emoji"
    choose.assert_called_once_with(["math", "emoji"])


@pytest.mark.parametrize("legacy,methods", [("disable", []), ("math", ["math"]),
                                           ("button", ["button"]), ("tguard", ["tguard"])])
def test_legacy_captcha_settings_remain_supported(legacy, methods):
    assert parse_captcha_config(legacy)["methods"] == methods
