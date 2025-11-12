"""Main bot class for BetterForward."""

import pytz
from diskcache import Cache
from telebot import types, TeleBot

from src.config import logger, _
from src.database import Database
from src.handlers.admin_handler import AdminHandler
from src.handlers.callback_handler import CallbackHandler
from src.handlers.command_handler import CommandHandler
from src.handlers.message_handler import MessageHandler
from src.utils.auto_response import AutoResponseManager
from src.utils.captcha import CaptchaManager
from src.utils.message_queue import MessageQueueManager
from src.utils.spam_detector_manager import SpamDetectorManager
from src.utils.spam_detectors import KeywordSpamDetector


class TGBot:
    """Main Telegram bot class."""

    def __init__(self, bot_token: str, group_id: str, db_path: str = "./data/storage.db",
                 num_workers: int = 5):
        """
        Initialize the bot.
        
        Args:
            bot_token: Telegram bot token
            group_id: Target group ID
            db_path: Path to SQLite database
            num_workers: Number of worker threads for message processing (default: 5)
        """
        logger.info(_("Starting BetterForward..."))
        self.group_id = int(group_id)
        self.bot = TeleBot(token=bot_token)
        self.db_path = db_path
        self.num_workers = num_workers

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

        # Initialize spam detection system
        self.spam_detector_manager = SpamDetectorManager()
        self.keyword_detector = KeywordSpamDetector()
        self.spam_detector_manager.register_detector(self.keyword_detector)

        # Initialize handlers
        self.message_handler = MessageHandler(
            self.bot, self.group_id, db_path, self.cache,
            self.captcha_manager, self.auto_response_manager,
            spam_detector_manager=self.spam_detector_manager,
            bot_instance=self
        )
        self.command_handler = CommandHandler(
            self.bot, self.group_id, db_path, self.cache,
            self.time_zone, self.captcha_manager
        )
        self.admin_handler = AdminHandler(
            self.bot, self.group_id, db_path, self.cache,
            self.database, self.auto_response_manager,
            spam_keyword_manager=self.keyword_detector,
            bot_instance=self
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

        # Setup multi-threaded message queue
        self.message_queue_manager = MessageQueueManager(
            handler_func=self.message_handler.handle_message,
            num_workers=self.num_workers
        )

        logger.info(_("Message queue initialized with {} workers").format(self.num_workers))

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
        """Update the timezone from cache and propagate to all handlers."""
        tz_str = self.cache.get("setting_time_zone")
        if tz_str:
            self.time_zone = pytz.timezone(tz_str)
            # Update all components that use timezone
            self.auto_response_manager.update_time_zone(self.time_zone)
            self.admin_handler.update_time_zone()
            # command_handler uses property to read from cache, no update needed

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

        # Check and create spam topic if not exists
        self._ensure_spam_topic()

        self.bot.send_message(self.group_id, _("Bot started successfully"))

    def _ensure_spam_topic(self):
        """Ensure spam topic exists, create if not."""
        self._create_or_load_spam_topic()

    def _create_or_load_spam_topic(self):
        """Create or load spam topic."""
        spam_topic_id = self.database.get_setting('spam_topic')

        # If spam topic ID is not set or is None, create a new topic
        if spam_topic_id is None or spam_topic_id == 'None':
            self._create_spam_topic()
        else:
            # Load existing spam topic ID into cache
            try:
                spam_topic_id = int(spam_topic_id)
                self.cache.set("spam_topic_id", spam_topic_id)
                logger.info(_("Spam topic loaded: {}").format(spam_topic_id))
            except (ValueError, TypeError):
                logger.error(_("Invalid spam topic ID in database: {}").format(spam_topic_id))
                self._create_spam_topic()

    def _create_spam_topic(self):
        """Create a new spam topic."""
        try:
            from telebot.apihelper import create_forum_topic
            logger.info(_("Creating spam topic..."))
            topic = create_forum_topic(
                chat_id=self.group_id,
                name="🚫 Spam Messages",
                token=self.bot.token
            )
            spam_topic_id = topic["message_thread_id"]
            self.database.set_setting('spam_topic', str(spam_topic_id))
            self.cache.set("spam_topic_id", spam_topic_id)
            logger.info(_("Spam topic created with ID: {}").format(spam_topic_id))

            # Send a pin message to the spam topic (silently)
            pin_msg = self.bot.send_message(
                self.group_id,
                _("This topic is used to collect spam messages detected by keywords.\n"
                  "Messages here are automatically forwarded from users who sent spam content."),
                message_thread_id=spam_topic_id,
                disable_notification=True
            )
            self.bot.pin_chat_message(self.group_id, pin_msg.message_id)
        except Exception as e:
            logger.error(_("Failed to create spam topic: {}").format(str(e)))
            raise

    def reset_spam_topic(self):
        """Reset spam topic by creating a new one."""
        try:
            # Clear old setting
            self.database.set_setting('spam_topic', None)
            self.cache.delete("spam_topic_id")

            # Create new topic
            self._create_spam_topic()
            return True
        except Exception as e:
            logger.error(_("Failed to reset spam topic: {}").format(str(e)))
            return False

    def push_messages(self, message):
        """Push messages to the queue for processing."""
        self.message_queue_manager.put(message)

    def get_queue_stats(self) -> dict:
        """Get current message queue statistics."""
        return self.message_queue_manager.get_stats()

    def stop(self):
        """Stop the bot and cleanup resources."""
        logger.info(_("Stopping bot..."))
        self.message_queue_manager.stop()
        self.bot.stop_bot()
        logger.info(_("Bot stopped"))
