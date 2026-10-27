"""The bot ignores groups other than the forwarding group."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from telebot import TeleBot

from src.bot import TGBot
from src.handlers.message_handler import MessageHandler
from tests.helpers import init_core_db, make_cache, make_message


GROUP_ID = -1001234567890
OTHER_GROUP_ID = -1009999999999


def test_message_from_other_group_is_ignored_even_with_captcha(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_core_db(db_path)
    bot = MagicMock()
    captcha = MagicMock()
    captcha.is_user_verified.return_value = False
    handler = MessageHandler(bot=bot, group_id=GROUP_ID, db_path=db_path,
                             cache=make_cache({"setting_captcha": "math"}),
                             captcha_manager=captcha, auto_response_manager=MagicMock())

    handler.handle_message(make_message(chat_id=OTHER_GROUP_ID, chat_type="supergroup", user_id=101,
                                        text="hello everyone"))

    bot.send_message.assert_not_called()
    captcha.generate_captcha.assert_not_called()


def test_all_message_handlers_skip_other_groups():
    bot = TGBot.__new__(TGBot)
    bot.group_id = GROUP_ID
    bot.bot = TeleBot("0000000000:TEST_TOKEN_FOR_UNIT_TESTS", threaded=False)
    bot.command_handler = MagicMock()
    bot.admin_handler = MagicMock()
    bot.callback_handler = MagicMock()
    bot._register_handlers()

    handlers = (bot.bot.message_handlers + bot.bot.edited_message_handlers
                + bot.bot.message_reaction_handlers)
    other_group = SimpleNamespace(chat=SimpleNamespace(id=OTHER_GROUP_ID, type="supergroup"))
    forwarding_group = SimpleNamespace(chat=SimpleNamespace(id=GROUP_ID, type="supergroup"))
    private = SimpleNamespace(chat=SimpleNamespace(id=101, type="private"))
    for handler in handlers:
        relevant = handler["filters"]["func"]
        assert not relevant(other_group)
        assert relevant(forwarding_group)
        assert relevant(private)
