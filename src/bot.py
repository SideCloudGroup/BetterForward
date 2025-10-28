"""Main bot class for BetterForward."""

import queue
import threading

import pytz
from diskcache import Cache
from telebot import types, TeleBot
from telebot.util import antiflood

from src.config import logger, _
from src.database import Database
from src.handlers.admin_handler import AdminHandler
from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.handlers.message_handler import MessageHandler
from src.utils.auto_response import AutoResponseManager
from src.utils.captcha import CaptchaManager


class TGBot:
    """Main Telegram bot class."""

    def __init__(self, bot_token: str, group_id: str, db_path: str = "./data/storage.db"):
        logger.info(_("Starting BetterForward..."))
        self.group_id = int(group_id)
        self.bot = TeleBot(token=bot_token)
        self.db_path = db_path

        # Initialize database
        self.database = Database(db_path)

        # Initialize cache
        self.cache = Cache()

        # Load settings into cache
        self.load_settings()

        # Initialize timezone
        tz_str = self.cache.get("setting_time_zone")
        self.time_zone = pytz.timezone(tz_str) if tz_str else pytz.UTC

        # Initialize managers
        self.captcha_manager = CaptchaManager(self.bot, self.cache)
        self.auto_response_manager = AutoResponseManager(db_path, self.time_zone)

        # Initialize handlers
        self.message_handler = MessageHandler(
            self.bot, self.group_id, db_path, self.cache,
            self.captcha_manager, self.auto_response_manager
        )
        self.command_handler = CommandHandler(
            self.bot, self.group_id, db_path, self.cache,
            self.time_zone, self.captcha_manager
        )
        self.admin_handler = AdminHandler(
            self.bot, self.group_id, db_path, self.cache,
            self.database, self.auto_response_manager
        )
        self.callback_handler = CallbackHandler(
            self.bot, self.group_id, self.admin_handler,
            self.command_handler, self.captcha_manager
        )

        # Register handlers
        self._register_handlers()

        # Set bot commands
        self._set_bot_commands()

        # Delete webhook
        self.bot.delete_webhook()

        # Check permissions
        self.check_permission()

        # Setup message queue and processor
        self.message_queue = queue.Queue()
        self.message_processor = threading.Thread(target=self.process_messages)
        self.message_processor.start()

        # Start polling
        self.bot.infinity_polling(
            skip_pending=True,
            timeout=5,
            allowed_updates=['message', 'edited_message', 'callback_query',
                             'my_chat_member', 'message_reaction', 'message_reaction_count']
        )

    def _register_handlers(self):
        """Register all bot handlers."""
        # Edited message handler
        self.bot.edited_message_handler(func=lambda m: True)(self.command_handler.handle_edit)

        # Command handlers
        self.bot.message_handler(commands=["start", "help"])(
            lambda m: self.command_handler.help_command(m, self.admin_handler.menu))
        self.bot.message_handler(commands=["ban"])(self.command_handler.ban_user)
        self.bot.message_handler(commands=["unban"])(self.command_handler.unban_user)
        self.bot.message_handler(commands=["terminate"])(self.command_handler.handle_terminate)
        self.bot.message_handler(commands=["delete"])(self.command_handler.delete_message)
        self.bot.message_handler(commands=["verify"])(self.command_handler.handle_verify)

        # Message handler (for all message types)
        self.bot.message_handler(
            func=lambda m: True,
            content_types=["photo", "text", "sticker", "video", "document",
                           "voice", "audio", "animation", "contact"]
        )(self.push_messages)

        # Reaction handler
        self.bot.message_reaction_handler(func=lambda message: True)(
            self.command_handler.handle_reaction)

        # Callback query handler
        self.bot.callback_query_handler(func=lambda call: True)(
            self.callback_handler.handle_callback_query)

    def _set_bot_commands(self):
        """Set bot commands for different scopes."""
        self.bot.set_my_commands([
            types.BotCommand("delete", _("Delete a message")),
            types.BotCommand("help", _("Show help")),
        ], scope=types.BotCommandScopeAllPrivateChats())

        self.bot.set_my_commands([
            types.BotCommand("help", _("Show help")),
            types.BotCommand("ban", _("Ban a user")),
            types.BotCommand("unban", _("Unban a user")),
            types.BotCommand("delete", _("Delete a message")),
            types.BotCommand("terminate", _("Terminate a thread")),
            types.BotCommand("verify", _("Set verified status")),
        ], scope=types.BotCommandScopeChat(self.group_id))

    def load_settings(self):
        """Load settings from database into cache."""
        settings = self.database.get_all_settings()
        for key, value in settings.items():
            self.cache.set(f"setting_{key}", value)

    def update_self_time_zone(self):
        """Update the timezone from cache."""
        tz_str = self.cache.get("setting_time_zone")
        if tz_str:
            self.time_zone = pytz.timezone(tz_str)
            self.auto_response_manager.update_time_zone(self.time_zone)

    def check_permission(self):
        """Check if bot has necessary permissions."""
        if not self.bot.get_chat(self.group_id).is_forum:
            logger.error(_("Topic function is not enabled in this group"))
            self.bot.send_message(self.group_id, _("Topic function is not enabled in this group"))

        chat_member = self.bot.get_chat_member(self.group_id, self.bot.get_me().id)
        permissions = {
            _("Manage Topics"): chat_member.can_manage_topics,
            _("Delete Messages"): chat_member.can_delete_messages
        }

        for key, value in permissions.items():
            if value is False:
                logger.error(_("Bot doesn't have {} permission").format(key))
                self.bot.send_message(self.group_id, _("Bot doesn't have {} permission").format(key))

        self.bot.send_message(self.group_id, _("Bot started successfully"))

    def push_messages(self, message):
        """Push messages to the queue for processing."""
        self.message_queue.put(message)

    def process_messages(self):
        """Process messages from the queue."""
        global stop
        while not stop:
            try:
                message = self.message_queue.get(timeout=1)
                antiflood(self.message_handler.handle_message, message)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(_("Failed to process message: {}").format(e))
                from traceback import print_exc
                print_exc()
        self.bot.stop_bot()
