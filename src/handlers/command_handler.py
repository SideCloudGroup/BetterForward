"""Command handling module."""

import json
import sqlite3
from datetime import datetime

from telebot import types
from telebot.apihelper import ApiTelegramException, delete_forum_topic, close_forum_topic, reopen_forum_topic
from telebot.types import Message

from src.config import logger, _


class CommandHandler:
    """Handles bot commands."""

    def __init__(self, bot, group_id: int, db_path: str, cache, time_zone, captcha_manager, spam_detector=None):
        self.bot = bot
        self.group_id = group_id
        self.db_path = db_path
        self.cache = cache
        self._time_zone = time_zone  # Keep as fallback
        self.captcha_manager = captcha_manager
        self.spam_detector = spam_detector

    @property
    def time_zone(self):
        """Get current timezone from cache (allows real-time updates)."""
        import pytz
        tz_str = self.cache.get("setting_time_zone")
        if tz_str:
            try:
                return pytz.timezone(tz_str)
            except Exception:
                return self._time_zone
        return self._time_zone

    def check_valid_chat(self, message: Message) -> bool:
        """Check if message is in valid chat context."""
        return message.chat.id == self.group_id and message.message_thread_id is None

    def help_command(self, message: Message, menu_callback):
        """Handle /help and /start commands."""
        if self.check_valid_chat(message):
            menu_callback(message)
        else:
            default_message = self._get_setting('default_message')
            if default_message is None:
                self.bot.send_message(message.chat.id,
                                      _("I'm a bot that forwards messages, so please just tell me what you want to say.") + "\n" +
                                      "Powered by [BetterForward](https://github.com/SideCloudGroup/BetterForward)",
                                      parse_mode="Markdown",
                                      disable_web_page_preview=True)
            else:
                self.bot.send_message(message.chat.id, default_message)

    def ban_user(self, message: Message):
        """Ban a user from sending messages."""
        if message.chat.id == self.group_id and message.message_thread_id is None:
            self.bot.send_message(self.group_id, _("This command is not available in the main chat."))
            return
        if message.chat.id != self.group_id:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))
            return

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            # Get user_id from thread
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                              (message.message_thread_id,))
            if (user_id := db_cursor.fetchone()) is not None:
                user_id = user_id[0]
                # Add to blocked_users table with user info
                username = message.from_user.username if hasattr(message, 'from_user') else None
                # Get user info from the message that triggered ban (we need to get it from the thread)
                # Since we don't have direct access, we'll get it from the first message in thread
                db_cursor.execute(
                    "INSERT OR REPLACE INTO blocked_users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                    (user_id, None, None, None)  # Will be updated when user sends next message
                )
                # Remove user from verified list
                db_cursor.execute("DELETE FROM verified_users WHERE user_id = ?", (user_id,))
                db.commit()
            else:
                self.bot.send_message(self.group_id, _("User not found"),
                                      message_thread_id=message.message_thread_id)
                return

        # Send message with delete thread button
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "🗑️ " + _("Delete This Thread"),
            callback_data=json.dumps({"action": "delete_banned_thread", "thread_id": message.message_thread_id})
        ))

        self.bot.send_message(self.group_id, _("User banned"),
                              message_thread_id=message.message_thread_id,
                              reply_markup=markup)
        close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                          token=self.bot.token)

    def unban_user(self, message: Message, user_id: int = None):
        """Unban a user."""
        if message.chat.id != self.group_id:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))
            return

        if user_id is None:
            if self.check_valid_chat(message):
                if len((msg_split := message.text.split(" "))) != 2:
                    self.bot.reply_to(message, "Invalid command\n"
                                               "Correct usage:```\n"
                                               "/unban <user ID>```", parse_mode="Markdown")
                    return
                user_id = int(msg_split[1])

        if user_id is None:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                # Get user_id from thread
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                                  (message.message_thread_id,))
                if (result := db_cursor.fetchone()) is not None:
                    user_id = result[0]
                    # Remove from blocked_users table
                    db_cursor.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
                    db.commit()
            self.bot.send_message(self.group_id, _("User unbanned"),
                                  message_thread_id=message.message_thread_id)
            try:
                reopen_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                                   token=self.bot.token)
            except ApiTelegramException:
                pass
        else:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                # Check if user exists in blocked_users table
                db_cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = ? LIMIT 1", (user_id,))
                if db_cursor.fetchone() is None:
                    self.bot.send_message(self.group_id, _("User not found"))
                    return

                # Remove from blocked_users table
                db_cursor.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
                db.commit()

                # Try to reopen thread if it exists
                db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (user_id,))
                thread_result = db_cursor.fetchone()
                if thread_result is not None:
                    try:
                        reopen_forum_topic(chat_id=self.group_id, message_thread_id=thread_result[0],
                                           token=self.bot.token)
                    except ApiTelegramException:
                        pass

            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "menu"})))
            if message.from_user.id == self.bot.get_me().id:
                self.bot.edit_message_text(_("User unbanned"), message.chat.id, message.message_id,
                                           reply_markup=markup)
            else:
                self.bot.send_message(self.group_id, _("User unbanned"), reply_markup=markup)

    def terminate_thread(self, thread_id=None, user_id=None):
        """Terminate and delete a thread."""
        original_thread_id = thread_id
        db_user_id = None
        db_thread_id = None

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if thread_id is not None:
                result = db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                                           (thread_id,))
                if (fetched_user_id := result.fetchone()) is not None:
                    db_user_id = fetched_user_id[0]
                    db_thread_id = thread_id
                    db_cursor.execute("DELETE FROM topics WHERE thread_id = ?", (thread_id,))
                    db.commit()
            elif user_id is not None:
                result = db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1",
                                           (user_id,))
                if (fetched_thread_id := result.fetchone()) is not None:
                    db_thread_id = fetched_thread_id[0]
                    db_user_id = user_id
                    db_cursor.execute("DELETE FROM topics WHERE user_id = ?", (user_id,))
                    db.commit()

            if db_user_id and db_thread_id:
                self.cache.delete(f"chat_{db_user_id}_threadid")
                self.cache.delete(f"threadid_{db_thread_id}_userid")
                db_cursor.execute("DELETE FROM messages WHERE topic_id = ?", (db_thread_id,))
                db.commit()

        final_thread_id = original_thread_id if original_thread_id is not None else db_thread_id
        if final_thread_id is not None:
            try:
                delete_forum_topic(chat_id=self.group_id, message_thread_id=final_thread_id,
                                   token=self.bot.token)
            except ApiTelegramException:
                pass

        logger.info(_("Terminating thread") + str(final_thread_id))

    def handle_terminate(self, message: Message):
        """Handle /terminate command."""
        if (message.chat.id == self.group_id) and (
                self.bot.get_chat_member(message.chat.id, message.from_user.id).status in ["administrator", "creator"]):
            user_id = None
            thread_id = None
            if message.message_thread_id is None:
                if len((msg_split := message.text.split(" "))) != 2:
                    self.bot.reply_to(message, "Invalid command\n"
                                               "Correct usage:```\n"
                                               "/terminate <user ID>```", parse_mode="Markdown")
                    return
                user_id = int(msg_split[1])
            else:
                thread_id = message.message_thread_id
            if thread_id == 1:
                self.bot.reply_to(message, _("Cannot terminate main thread"))
                return
            markup = types.InlineKeyboardMarkup()
            confirm_button = types.InlineKeyboardButton(
                f"✅{_('Confirm')}",
                callback_data=json.dumps(
                    {"action": "confirm_terminate", "thread_id": thread_id} if thread_id is not None else
                    {"action": "confirm_terminate", "user_id": user_id}
                )
            )
            cancel_button = types.InlineKeyboardButton(
                f"❌{_('Cancel')}",
                callback_data=json.dumps({"action": "cancel_terminate"})
            )
            markup.add(confirm_button, cancel_button)
            self.bot.reply_to(message, _("Are you sure you want to terminate this thread?"),
                              reply_markup=markup)
        else:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))

    def delete_message(self, message: Message):
        """Delete a forwarded message."""
        if self.check_valid_chat(message):
            return
        if message.reply_to_message is None:
            self.bot.reply_to(message, _("Please reply to the message you want to delete"))
            return

        msg_id = message.reply_to_message.message_id
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = ? LIMIT 1",
                (msg_id, message.chat.id == self.group_id))
            if (result := db_cursor.fetchone()) is None:
                return
            topic_id, forwarded_id = result
            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (user_id := db_cursor.fetchone()) is None or user_id[0] is None:
                    return
                self.bot.delete_message(chat_id=user_id[0], message_id=forwarded_id)
            else:
                self.bot.send_message(chat_id=self.group_id,
                                      text=_("[Alert]") + _("Message deleted by user"),
                                      reply_to_message_id=forwarded_id)
            # Delete the message from the database
            db_cursor.execute("DELETE FROM messages WHERE received_id = ? AND in_group = ?",
                              (msg_id, message.chat.id == self.group_id))
            db.commit()

        # Delete the current message
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.reply_to_message.id)
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    def handle_verify(self, message: Message):
        """Handle /verify command to manually set verification status."""
        if message.chat.id != self.group_id or message.message_thread_id is None:
            return

        command_parts = message.text.split()
        if len(command_parts) != 2 or command_parts[1].lower() not in ["true", "false"]:
            self.bot.send_message(message.chat.id,
                                  _("Invalid command format.\nUse /verify <true/false>"),
                                  message_thread_id=message.message_thread_id)
            return

        verified_status = command_parts[1].lower() == "true"
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ?",
                              (message.message_thread_id,))
            user_id = db_cursor.fetchone()
            if user_id is None:
                self.bot.send_message(message.chat.id, _("User not found"),
                                      message_thread_id=message.message_thread_id)
                return
            user_id = user_id[0]
            if verified_status:
                self.captcha_manager.set_user_verified(user_id, db)
                self.bot.send_message(message.chat.id, _("User verified successfully."),
                                      message_thread_id=message.message_thread_id)
            else:
                self.captcha_manager.remove_user_verification(user_id, db)
                self.bot.send_message(message.chat.id, _("User verification removed."),
                                      message_thread_id=message.message_thread_id)

    def handle_edit(self, message: Message):
        """Handle edited messages."""
        if self.check_valid_chat(message):
            return

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = ? LIMIT 1",
                (message.message_id, message.chat.id == self.group_id))
            if (result := db_cursor.fetchone()) is None:
                return
            topic_id, forwarded_id = result
            edit_time = datetime.now().astimezone(self.time_zone).strftime("%Y-%m-%d %H:%M:%S")
            edited_text = message.text + f"\n\n({_('Edited at')} {edit_time})"

            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (user_id := db_cursor.fetchone()) is None or user_id[0] is None:
                    return
                if message.content_type == "text":
                    self.bot.edit_message_text(chat_id=user_id[0], message_id=forwarded_id,
                                               text=edited_text)
            else:
                if message.content_type == "text":
                    self.bot.edit_message_text(chat_id=self.group_id, message_id=forwarded_id,
                                               text=edited_text)

    def handle_reaction(self, message):
        """Handle message reactions."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT topic_id, received_id FROM messages WHERE forwarded_id = ? LIMIT 1",
                (message.message_id,))
            if (result := db_cursor.fetchone()) is None:
                db_cursor.execute(
                    "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? LIMIT 1",
                    (message.message_id,))
                if (result := db_cursor.fetchone()) is None:
                    return
            topic_id, forwarded_id = result
            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (chat_id := db_cursor.fetchone()) is None or chat_id[0] is None:
                    return
                chat_id = chat_id[0]
            else:
                chat_id = self.group_id
            self.bot.set_message_reaction(chat_id=chat_id, message_id=forwarded_id,
                                          reaction=[message.new_reaction[-1]] if message.new_reaction else [])

    def _get_setting(self, key: str):
        """Get a setting from the database."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (key,))
            result = db_cursor.fetchone()
            return result[0] if result else None
