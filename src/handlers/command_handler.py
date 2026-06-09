"""Command handling module."""

import json
import sqlite3
from datetime import datetime

from telebot import types
from telebot.apihelper import ApiTelegramException, delete_forum_topic, close_forum_topic, reopen_forum_topic
from telebot.types import Message

from src.config import logger, _
from src.utils.permissions import (
    ALLOW,
    DENY,
    list_permission_command_keys,
    parse_permission_keys,
    permission_label,
)


class CommandHandler:
    """Handles bot commands."""

    def __init__(self, bot, group_id: int, db_path: str, cache, time_zone, captcha_manager, spam_detector=None,
                 permission_manager=None):
        self.bot = bot
        self.group_id = group_id
        self.db_path = db_path
        self.cache = cache
        self._time_zone = time_zone  # Keep as fallback
        self.captcha_manager = captcha_manager
        self.spam_detector = spam_detector
        self.permission_manager = permission_manager

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

    _GETNOTE_MAX_LEN = 3500

    def _parse_setnote_body(self, text: str | None) -> str | None:
        """Return text to store, or None to clear note (NULL)."""
        if not text:
            return None
        bot_username = (self.bot.get_me().username or "").lower()
        parts = text.split("\n", 1)
        line0 = parts[0]
        cont = parts[1] if len(parts) > 1 else None
        l0 = line0.lower()
        prefixes = ["/setnote"]
        if bot_username:
            prefixes.append(f"/setnote@{bot_username}")
        prefixes.sort(key=len, reverse=True)
        n = 0
        for p in prefixes:
            if l0.startswith(p.lower()):
                n = len(p)
                break
        if n == 0:
            return None
        first_rest = line0[n:].lstrip()
        if cont is not None:
            body = (first_rest + "\n" + cont) if first_rest else cont
        else:
            body = first_rest
        if not body.strip():
            return None
        return body

    def _topic_note_reply(self, message: Message, text: str):
        self.bot.reply_to(message, text)

    def _topic_note_place_ok(self, message: Message) -> tuple[bool, str | None]:
        """Valid group + user topic (not General); any member may pass this for /getnote."""
        if message.chat.id != self.group_id:
            return False, _("This command can only be used in the forwarding group.")
        tid = message.message_thread_id
        if tid is None:
            return False, _("Use this command inside a user topic.")
        if tid == 1:
            return False, _("Cannot use this command in the main thread")
        return True, None

    def _topic_note_set_access_ok(self, message: Message) -> tuple[bool, str | None]:
        """Place + administrator for /setnote."""
        ok, err = self._topic_note_place_ok(message)
        if not ok:
            return False, err
        if self.bot.get_chat_member(message.chat.id, message.from_user.id).status not in (
                "administrator", "creator"):
            return False, _("Only administrators can set or clear topic notes.")
        return True, None

    def _topic_admin_access_ok(self, message: Message) -> tuple[bool, str | None]:
        """Place + administrator for topic admin commands."""
        ok, err = self._topic_note_place_ok(message)
        if not ok:
            return False, err
        if self.bot.get_chat_member(message.chat.id, message.from_user.id).status not in (
                "administrator", "creator"):
            return False, _("Only administrators can refresh user info.")
        return True, None

    def handle_refresh(self, message: Message):
        """Refresh pinned user info in the current topic."""
        ok, err = self._topic_admin_access_ok(message)
        if not ok:
            self._topic_note_reply(message, err)
            return

        thread_id = message.message_thread_id
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (thread_id,))
            row = db_cursor.fetchone()
        if row is None:
            self.bot.reply_to(message, _("User not found"))
            return
        user_id = row[0]

        try:
            chat = self.bot.get_chat(user_id)
        except ApiTelegramException as e:
            self.bot.reply_to(message, _("Failed to fetch user info: {}").format(str(e)))
            return

        try:
            edit_forum_topic(chat_id=self.group_id, message_thread_id=thread_id,
                             name=f"{chat.first_name} | {user_id}", token=self.bot.token)
        except ApiTelegramException as e:
            logger.warning(f"Failed to update topic name for user {user_id}: {e}")

        pin_text = build_user_info_pin_text(user_id, chat.first_name, chat.last_name, chat.username)
        send_and_pin_user_info(self.bot, self.group_id, thread_id, pin_text)
        self.bot.reply_to(message, _("User info refreshed."))

    def handle_setnote(self, message: Message):
        """Set or clear topic note (multiline body); empty body clears."""
        ok, err = self._topic_note_set_access_ok(message)
        if not ok:
            self._topic_note_reply(message, err)
            return
        tid = message.message_thread_id
        body = self._parse_setnote_body(message.text)
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT 1 FROM topics WHERE thread_id = ? LIMIT 1", (tid,))
            if db_cursor.fetchone() is None:
                self.bot.reply_to(message, _("User not found"))
                return
            db_cursor.execute("UPDATE topics SET note = ? WHERE thread_id = ?", (body, tid))
            db.commit()
        if body is None:
            self.bot.reply_to(message, _("Note cleared."))
        else:
            self.bot.reply_to(message, _("Note saved."))

    def handle_getnote(self, message: Message):
        """Show topic note."""
        ok, err = self._topic_note_place_ok(message)
        if not ok:
            self._topic_note_reply(message, err)
            return
        tid = message.message_thread_id
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT note FROM topics WHERE thread_id = ? LIMIT 1", (tid,))
            row = db_cursor.fetchone()
        if row is None:
            self.bot.reply_to(message, _("User not found"))
            return
        note = row[0]
        if note is None or not str(note).strip():
            self.bot.reply_to(message, _("No note set for this topic."))
            return
        note = str(note)
        if len(note) > self._GETNOTE_MAX_LEN:
            note = note[: self._GETNOTE_MAX_LEN] + "\n" + _("(truncated)")
        self.bot.reply_to(message, note)

    def allow_permissions(self, message: Message):
        """Grant explicit permission overrides for the current topic's user."""
        self._handle_permission_override_command(message, ALLOW)

    def disallow_permissions(self, message: Message):
        """Deny explicit permission overrides for the current topic's user."""
        self._handle_permission_override_command(message, DENY)

    def _handle_permission_override_command(self, message: Message, override: str):
        if self.permission_manager is None:
            self.bot.reply_to(message, _("Permission settings are not available"))
            return

        ok, err = self._permission_command_access_ok(message)
        if not ok:
            self.bot.reply_to(message, err)
            return

        user_id = self._user_id_for_topic(message.message_thread_id)
        if user_id is None:
            self.bot.reply_to(message, _("User not found"))
            return

        valid_keys, unknown_values = parse_permission_keys(self._permission_command_args(message.text))
        if not valid_keys and not unknown_values:
            self.bot.reply_to(message, self._permission_command_usage())
            return

        if unknown_values:
            self.bot.reply_to(
                message,
                _("Unknown permission keys: {}").format(", ".join(unknown_values)) + "\n" +
                self._permission_command_usage()
            )
            return

        for permission_key in valid_keys:
            self.permission_manager.set_user_override(user_id, permission_key, override)

        labels = "、".join(permission_label(permission_key) for permission_key in valid_keys)
        action_text = _("Allowed") if override == ALLOW else _("Disallowed")
        self.bot.reply_to(
            message,
            _("{} permissions for user {}: {}").format(action_text, user_id, labels)
        )

    def _permission_command_access_ok(self, message: Message) -> tuple[bool, str | None]:
        if message.chat.id != self.group_id:
            return False, _("This command is only available to admin users.")
        if message.message_thread_id is None:
            return False, _("Use this command inside a user topic.")
        if message.message_thread_id == 1:
            return False, _("Cannot use this command in the main thread")
        if self.bot.get_chat_member(message.chat.id, message.from_user.id).status not in (
                "administrator", "creator"):
            return False, _("This command is only available to admin users.")
        return True, None

    def _user_id_for_topic(self, thread_id: int):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (thread_id,))
            row = db_cursor.fetchone()
        return row[0] if row else None

    def _permission_command_args(self, text: str | None) -> str:
        if not text:
            return ""
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    def _permission_command_usage(self) -> str:
        return _("Usage: /allow photo video link or /disallow photo video link "
                 "(multiple keys allowed). Use all to grant/deny all permissions.") + "\n" + \
            _("Valid permission keys: {}").format(", ".join(list_permission_command_keys()))

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
