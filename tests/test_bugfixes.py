"""Regression tests for TGuard, auto responses and spam topic reset."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.bot import TGBot
from src.database import Database
from src.handlers.message_handler import MessageHandler
from src.utils.captcha import CaptchaManager
from tests.helpers import init_core_db, make_cache, make_message


GROUP_ID = -1001234567890


@pytest.fixture
def handler(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_core_db(db_path)
    return MessageHandler(bot=MagicMock(), group_id=GROUP_ID, db_path=db_path, cache=make_cache(),
                          captcha_manager=MagicMock(), auto_response_manager=MagicMock())


def test_media_auto_response_is_shown_as_media_in_group(handler):
    handler._send_auto_response(GROUP_ID, {"type": "photo", "response": "PHOTO_ID"},
                                thread_id=55, label="[Auto Response]")
    handler.bot.send_photo.assert_called_once_with(GROUP_ID, photo="PHOTO_ID", caption="[Auto Response]",
                                                   message_thread_id=55)
    handler.bot.send_message.assert_not_called()


def test_tguard_verification_uses_configured_db_path(tmp_path):
    db_path = str(tmp_path / "custom.db")
    init_core_db(db_path)
    cache = make_cache({"tguard_token_101": "tok", "setting_tguard_api_url": "https://tguard.test"})
    captcha = CaptchaManager(MagicMock(), cache, GROUP_ID, db_path)

    response = SimpleNamespace(status_code=200, json=lambda: {"completed": True})
    with patch("src.utils.captcha.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = response
        assert captcha.check_tguard_verification_status(101) is True

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT user_id FROM verified_users").fetchall() == [(101,)]


def test_reset_spam_topic_succeeds_with_not_null_settings(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_core_db(db_path)
    database = Database.__new__(Database)
    database.db_path = db_path
    database.set_setting("spam_topic", "42")

    bot = TGBot.__new__(TGBot)
    bot.database = database
    bot.cache = make_cache({"spam_topic_id": 42})
    bot._create_spam_topic = MagicMock(side_effect=lambda: database.set_setting("spam_topic", "99"))

    assert bot.reset_spam_topic() is True
    assert database.get_setting("spam_topic") == "99"
