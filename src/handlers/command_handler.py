"""Command handling module."""

import html
import json
import sqlite3
import time
from datetime import datetime

import pytz

from telebot import types
from telebot.apihelper import (
    ApiTelegramException,
    close_forum_topic,
    delete_forum_topic,
    edit_forum_topic,
    reopen_forum_topic,
)
from telebot.types import Message

from src.config import logger, _
from src.utils.captcha import VERIFICATION_DAYS_SETTING, verification_days
from src.utils.helpers import build_user_info_pin_text, parse_start_payload, send_and_pin_user_info, to_html
from src.utils.message_split import split_caption, split_html_text
from src.utils.message_permissions import classify_message_permissions
from src.utils.stats import get_summary, get_user_stats
from src.utils.permissions import (
    ALLOW,
    DENY,
    ENABLE,
    join_permission_labels,
    list_permission_command_keys,
    list_permission_keys,
    parse_permission_keys,
    permission_label,
    permission_menu_label,
)


class CommandHandler:
    """Handles bot commands."""

    def __init__(self, bot, group_id: int, db_path: str, cache, time_zone, captcha_manager, spam_detector=None,
                 permission_manager=None, message_handler=None, queue_stats=None):
        self.bot = bot
        self.group_id = group_id
        self.db_path = db_path
        self.cache = cache
        self._time_zone = time_zone  # Keep as fallback
        self.captcha_manager = captcha_manager
        self.spam_detector = spam_detector
        self.permission_manager = permission_manager
        self.message_handler = message_handler
        # Callable returning MessageQueueManager.get_stats(); optional.
        self.queue_stats = queue_stats

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

    def _is_group_admin(self, user_id: int, chat_id: int | None = None) -> bool:
        """Return True when the user is an administrator of the forwarding group."""
        chat_id = chat_id or self.group_id
        try:
            return self.bot.get_chat_member(chat_id, user_id).status in ("administrator", "creator")
        except ApiTelegramException:
            return False

    def _reply_command_error(self, message: Message, err: str | None):
        """Reply with an error message when err is set; stay silent otherwise."""
        if err:
            self.bot.reply_to(message, err)

    def _user_thread_id(self, db_cursor, user_id: int):
        """Return the topic thread id mapped to a user, or None."""
        db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (user_id,))
        row = db_cursor.fetchone()
        return row[0] if row else None

    def _find_forwarded(self, db_cursor, chat_id: int, received_id: int):
        """Find the forwarded copies of a message received in chat_id.

        Private-chat message ids are only unique per user, so lookups for user
        messages are scoped to that user's topic.

        Returns (topic_id, forwarded_ids, target_chat_id) or None.
        """
        if chat_id == self.group_id:
            db_cursor.execute(
                "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = 1 ORDER BY id",
                (received_id,))
            rows = db_cursor.fetchall()
            if not rows:
                return None
            topic_id = rows[0][0]
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
            if (user_id := db_cursor.fetchone()) is None or user_id[0] is None:
                return None
            return topic_id, [row[1] for row in rows], user_id[0]

        if (topic_id := self._user_thread_id(db_cursor, chat_id)) is None:
            return None
        db_cursor.execute(
            "SELECT forwarded_id FROM messages WHERE received_id = ? AND in_group = 0 AND topic_id = ? ORDER BY id",
            (received_id, topic_id))
        rows = db_cursor.fetchall()
        if not rows:
            return None
        return topic_id, [row[0] for row in rows], self.group_id

    def help_command(self, message: Message, menu_callback):
        """Handle /help and /start commands."""
        if self.check_valid_chat(message):
            if not self._is_group_admin(message.from_user.id):
                return
            menu_callback(message)
        else:
            if getattr(message.chat, "type", None) == "private":
                self._save_start_tag(message)
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
        if message.chat.id != self.group_id or not self._is_group_admin(message.from_user.id):
            return
        if message.message_thread_id is None:
            self.bot.send_message(self.group_id, _("This command is not available in the main chat."))
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
                # Remove user from verified list (DB + cache)
                self.captcha_manager.remove_user_verification(user_id, db)
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

    def unban_user(self, message: Message, user_id: int = None, operator_id: int = None):
        """Unban a user."""
        actor_id = operator_id if operator_id is not None else message.from_user.id
        if message.chat.id != self.group_id or not self._is_group_admin(actor_id):
            return

        if user_id is None:
            if self.check_valid_chat(message):
                if len((msg_split := message.text.split(" "))) != 2:
                    self.bot.reply_to(message, "Invalid command\n"
                                               "Correct usage:```\n"
                                               "/unban <user ID>```", parse_mode="Markdown")
                    return
                try:
                    user_id = int(msg_split[1])
                except ValueError:
                    self.bot.reply_to(message, "Invalid command\n"
                                               "Correct usage:```\n"
                                               "/unban <user ID>```", parse_mode="Markdown")
                    return

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
                else:
                    self.bot.send_message(self.group_id, _("User not found"),
                                          message_thread_id=message.message_thread_id)
                    return
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
        if message.chat.id == self.group_id and self._is_group_admin(message.from_user.id):
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

    def delete_message(self, message: Message):
        """Delete a forwarded message (all split chunks when present)."""
        if self.check_valid_chat(message):
            return
        if message.reply_to_message is None:
            self.bot.reply_to(message, _("Please reply to the message you want to delete"))
            return

        msg_id = message.reply_to_message.message_id
        in_group = message.chat.id == self.group_id
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if (found := self._find_forwarded(db_cursor, message.chat.id, msg_id)) is None:
                return
            topic_id, forwarded_ids, target_chat_id = found
            if in_group:
                for forwarded_id in forwarded_ids:
                    try:
                        self.bot.delete_message(chat_id=target_chat_id, message_id=forwarded_id)
                    except ApiTelegramException:
                        pass
            else:
                self.bot.send_message(chat_id=self.group_id,
                                      text=_("[Alert]") + _("Message deleted by user"),
                                      message_thread_id=topic_id,
                                      reply_to_message_id=forwarded_ids[0])
            db_cursor.execute("DELETE FROM messages WHERE received_id = ? AND in_group = ? AND topic_id = ?",
                              (msg_id, in_group, topic_id))
            db.commit()

        # Delete the current message
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.reply_to_message.id)
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    def handle_verify(self, message: Message):
        """Handle /verify command to manually set verification status."""
        ok, err = self._admin_topic_command_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
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

    def _admin_topic_command_access_ok(self, message: Message) -> tuple[bool, str | None]:
        """Admin-only topic commands: silent for non-admins and private/wrong chat."""
        if message.chat.id != self.group_id:
            return False, None
        if not self._is_group_admin(message.from_user.id):
            return False, None
        tid = message.message_thread_id
        if tid is None:
            return False, _("Use this command inside a user topic.")
        if tid == 1:
            return False, _("Cannot use this command in the main thread")
        return True, None

    def _topic_note_set_access_ok(self, message: Message) -> tuple[bool, str | None]:
        """Place + administrator for /setnote."""
        return self._admin_topic_command_access_ok(message)

    def _topic_admin_access_ok(self, message: Message) -> tuple[bool, str | None]:
        """Place + administrator for topic admin commands."""
        return self._admin_topic_command_access_ok(message)

    def handle_refresh(self, message: Message):
        """Refresh pinned user info in the current topic."""
        ok, err = self._topic_admin_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
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
            # get_chat does not return the client language; use the last one seen on a message.
            language_code = (get_user_stats(db_cursor, user_id) or {}).get("language")

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

        pin_text = build_user_info_pin_text(
            user_id, chat.first_name, chat.last_name, chat.username,
            language_code=language_code, tag=self._get_user_tag(user_id))
        send_and_pin_user_info(self.bot, self.group_id, thread_id, pin_text)
        self.bot.reply_to(message, _("User info refreshed."))

    def handle_setnote(self, message: Message):
        """Set or clear topic note (multiline body); empty body clears."""
        ok, err = self._topic_note_set_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
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

    def _format_time(self, timestamp: int | None) -> str:
        if timestamp is None:
            return _("Unknown")
        return datetime.fromtimestamp(timestamp, self.time_zone or pytz.UTC).strftime("%Y-%m-%d %H:%M:%S")

    def handle_stats(self, message: Message):
        """Show bot-wide statistics (admins, anywhere in the group)."""
        if message.chat.id != self.group_id or not self._is_group_admin(message.from_user.id):
            return
        days = verification_days(self.cache.get(f"setting_{VERIFICATION_DAYS_SETTING}"))
        verified_since = time.time() - days * 86400 if days else 0
        with sqlite3.connect(self.db_path) as db:
            summary = get_summary(db.cursor(), self.time_zone, verified_since)

        lines = [
            "📊 " + _("Statistics"),
            "",
            _("Users: {}").format(summary["users"]),
            _("New users today: {}").format(summary["new_users_today"]),
            _("Active users today: {}").format(summary["active_users_today"]),
            _("Messages today: {} received / {} replied").format(
                summary["incoming_today"], summary["outgoing_today"]),
            _("Banned users: {}").format(summary["banned_users"]),
            _("Verified users: {}").format(summary["verified_users"]),
        ]
        if self.queue_stats is not None:
            queue = self.queue_stats()
            lines += [
                "",
                _("Queue: {} waiting, {} users processing, {} workers").format(
                    queue["main_queue_size"] + queue["total_queued_messages"],
                    queue["processing_users_count"], queue["workers_count"]),
            ]
        self.bot.reply_to(message, "\n".join(lines))

    def handle_info(self, message: Message):
        """Show profile and activity of the user behind the current topic."""
        ok, err = self._topic_admin_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
            return

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                              (message.message_thread_id,))
            if (row := db_cursor.fetchone()) is None or row[0] is None:
                self.bot.reply_to(message, _("User not found"))
                return
            user_id = row[0]
            stats = get_user_stats(db_cursor, user_id) or {}
            banned = db_cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = ?",
                                       (user_id,)).fetchone() is not None
            verified = self.captcha_manager.is_user_verified(user_id, db)

        lines = ["👤 " + _("User Info"), "", _("User ID") + f": {user_id}"]
        try:
            chat = self.bot.get_chat(user_id)
            full_name = chat.first_name + (f" {chat.last_name}" if chat.last_name else "")
            lines.append(_("Full Name") + f": {full_name}")
            lines.append(_("Username") + ": " + (f"@{chat.username}" if chat.username else _("Not set")))
        except ApiTelegramException as e:
            logger.warning(f"Failed to fetch user info for {user_id}: {e}")
        lines += [
            _("First contact: {}").format(self._format_time(stats.get("first_seen"))),
            _("Last message: {}").format(self._format_time(stats.get("last_seen"))),
            _("Messages sent: {}").format(stats.get("message_count", 0)),
            _("Verified: {}").format(_("Yes") if verified else _("No")),
            _("Banned: {}").format(_("Yes") if banned else _("No")),
        ]
        self.bot.reply_to(message, "\n".join(lines))

    def _get_templates(self, db_cursor) -> list[str]:
        return [row[0] for row in db_cursor.execute("SELECT content FROM reply_templates ORDER BY id")]

    def handle_template_reply(self, message: Message):
        """/r N sends reply template N to the topic's user; /r alone lists templates."""
        ok, err = self._topic_admin_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
            return

        arg = self._permission_command_args(message.text).strip()
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            templates = self._get_templates(db_cursor)
            if not arg:
                if not templates:
                    self.bot.reply_to(message, _("No reply templates. Add them in /help → Reply Templates."))
                else:
                    listing = "\n".join(f"{i}. {t if len(t) <= 80 else t[:80] + '…'}"
                                         for i, t in enumerate(templates, start=1))
                    self.bot.reply_to(message, _("Reply templates:") + "\n" + listing + "\n\n" +
                                      _("Usage: /r <number>"))
                return
            if not arg.isdigit() or not 1 <= int(arg) <= len(templates):
                self.bot.reply_to(message, _("Template not found. Send /r to list templates."))
                return
            content = templates[int(arg) - 1]

            thread_id = message.message_thread_id
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (thread_id,))
            if (row := db_cursor.fetchone()) is None or row[0] is None:
                self.bot.reply_to(message, _("User not found"))
                return
            user_id = row[0]

            try:
                sent = self.bot.send_message(user_id, content)
            except ApiTelegramException as e:
                self.bot.reply_to(message, _("[Alert]") + _("Failed to forward message to user {}").format(
                    user_id) + "\n" + str(e))
                return
            # Persist delivery before the separate echo request. If the echo fails,
            # the original command remains a usable reply/reaction/delete target.
            db_cursor.execute(
                "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
                (message.message_id, sent.message_id, thread_id, True))
            mapping_id = db_cursor.lastrowid
            if self.message_handler is not None:
                self.message_handler.after_reply_sent(db_cursor, user_id, thread_id)
            db.commit()
            # Keep one echo per delivered message. At the limit, omit only the decoration.
            echo_text = "📝 " + content if len(content) <= 4094 else content
            try:
                echo = self.bot.send_message(self.group_id, echo_text, message_thread_id=thread_id)
            except ApiTelegramException as e:
                logger.error("Template delivered to user %s, but topic echo failed: %s", user_id, e)
                return
            db_cursor.execute("UPDATE messages SET received_id = ? WHERE id = ?", (echo.message_id, mapping_id))
            db.commit()
        try:
            self.bot.delete_message(self.group_id, message.message_id)
        except ApiTelegramException:
            pass

    def allow_permissions(self, message: Message):
        """Grant explicit permission overrides for the current topic's user."""
        self._handle_permission_override_command(message, ALLOW)

    def disallow_permissions(self, message: Message):
        """Deny explicit permission overrides for the current topic's user."""
        self._handle_permission_override_command(message, DENY)

    def show_user_permissions(self, message: Message):
        """Show effective permission status for the current topic's user."""
        if self.permission_manager is None:
            self.bot.reply_to(message, _("Permission settings are not available"))
            return

        ok, err = self._permission_command_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
            return

        user_id = self._user_id_for_topic(message.message_thread_id)
        if user_id is None:
            self.bot.reply_to(message, _("User not found"))
            return

        lines = [_("Permissions for user {}:").format(user_id), ""]
        overrides = self.permission_manager.get_user_overrides(user_id)

        for permission_key in list_permission_keys():
            global_default = self.permission_manager.get_global_default_value(permission_key)
            global_text = _("Enabled") if global_default == ENABLE else _("Disabled")
            override = overrides.get(permission_key)
            if override == ALLOW:
                override_text = _("Allowed")
            elif override == DENY:
                override_text = _("Disallowed")
            else:
                override_text = _("Inherit")

            effective = _("Allowed") if self.permission_manager.resolve_permission(
                user_id, permission_key) else _("Denied")

            lines.append(
                _("{}: global {}, override {}, effective {}").format(
                    permission_menu_label(permission_key),
                    global_text,
                    override_text,
                    effective,
                )
            )

        self.bot.reply_to(message, "\n".join(lines))

    def reset_user_permissions(self, message: Message):
        """Clear per-user permission overrides for the current topic's user."""
        if self.permission_manager is None:
            self.bot.reply_to(message, _("Permission settings are not available"))
            return

        ok, err = self._permission_command_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
            return

        user_id = self._user_id_for_topic(message.message_thread_id)
        if user_id is None:
            self.bot.reply_to(message, _("User not found"))
            return

        valid_keys, unknown_values = parse_permission_keys(self._permission_command_args(message.text))
        if not valid_keys and not unknown_values:
            self.bot.reply_to(message, self._reset_permission_command_usage())
            return

        if unknown_values:
            self.bot.reply_to(
                message,
                _("Unknown permission keys: {}").format(", ".join(unknown_values)) + "\n" +
                self._reset_permission_command_usage()
            )
            return

        for permission_key in valid_keys:
            self.permission_manager.clear_user_override(user_id, permission_key)

        labels = join_permission_labels(
            [permission_label(permission_key) for permission_key in valid_keys]
        )
        self.bot.reply_to(
            message,
            _("Reset permissions for user {}: {}").format(user_id, labels)
        )

    def _handle_permission_override_command(self, message: Message, override: str):
        if self.permission_manager is None:
            self.bot.reply_to(message, _("Permission settings are not available"))
            return

        ok, err = self._permission_command_access_ok(message)
        if not ok:
            self._reply_command_error(message, err)
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

        labels = join_permission_labels(
            [permission_label(permission_key) for permission_key in valid_keys]
        )
        action_text = _("Allowed") if override == ALLOW else _("Disallowed")
        self.bot.reply_to(
            message,
            _("{} permissions for user {}: {}").format(action_text, user_id, labels)
        )

    def _permission_command_access_ok(self, message: Message) -> tuple[bool, str | None]:
        return self._admin_topic_command_access_ok(message)

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

    def _reset_permission_command_usage(self) -> str:
        return _("Usage: /resetpermissions photo video link (multiple keys allowed). "
                 "Use all to reset all permission overrides.") + "\n" + \
            _("Valid permission keys: {}").format(", ".join(list_permission_command_keys()))

    def handle_edit(self, message: Message):
        """Sync an edited text, caption or replaced media to all forwarded copies."""
        if self.check_valid_chat(message):
            return

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if (found := self._find_forwarded(db_cursor, message.chat.id, message.message_id)) is None:
                return
            if message.chat.id != self.group_id:
                if db_cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = ?",
                                     (message.from_user.id,)).fetchone() is not None:
                    return
                if self.permission_manager is not None and any(
                    not self.permission_manager.resolve_permission(message.from_user.id, key)
                    for key in classify_message_permissions(message)
                ):
                    return
            topic_id, forwarded_ids, target_chat_id = found
            edit_time = datetime.now().astimezone(self.time_zone).strftime("%Y-%m-%d %H:%M:%S")
            suffix = html.escape(f"\n\n({_('Edited at')} {edit_time})")
            # Group-side copies live in the user's topic; user-side copies have no thread.
            thread_id = topic_id if target_chat_id == self.group_id else None

            try:
                if message.content_type == "text":
                    text_ids = forwarded_ids
                    chunks = split_html_text(to_html(message.text, message.entities) + suffix)
                else:
                    caption, chunks = split_caption(
                        (to_html(message.caption, getattr(message, "caption_entities", None)) or "") + suffix)
                    if (media := self._input_media(message, caption)) is not None:
                        # Covers replaced media as well as caption-only edits.
                        self._edit_ignoring_unmodified(
                            self.bot.edit_message_media, media=media, chat_id=target_chat_id,
                            message_id=forwarded_ids[0])
                    else:
                        self._edit_ignoring_unmodified(
                            self.bot.edit_message_caption, caption=caption, chat_id=target_chat_id,
                            message_id=forwarded_ids[0], parse_mode="HTML")
                    text_ids = forwarded_ids[1:]

                self._sync_text_chunks(db_cursor, message.message_id, target_chat_id, thread_id,
                                       topic_id, text_ids, chunks, forwarded_ids[-1])
                db.commit()
            except ApiTelegramException as e:
                logger.error(_("Failed to sync edited message {}: {}").format(message.message_id, e))

    # content_type -> (InputMedia class, attribute holding the file). Voice notes cannot be replaced.
    _EDITABLE_MEDIA = {
        "photo": (types.InputMediaPhoto, "photo"),
        "video": (types.InputMediaVideo, "video"),
        "animation": (types.InputMediaAnimation, "animation"),
        "audio": (types.InputMediaAudio, "audio"),
        "document": (types.InputMediaDocument, "document"),
    }

    @classmethod
    def _input_media(cls, message: Message, caption: str | None):
        """Build the InputMedia for an edited media message, or None when it cannot be replaced."""
        if (spec := cls._EDITABLE_MEDIA.get(message.content_type)) is None:
            return None
        media_cls, attr = spec
        file = getattr(message, attr, None)
        if not file:
            return None
        file_id = file[-1].file_id if attr == "photo" else file.file_id
        return media_cls(media=file_id, caption=caption, parse_mode="HTML")

    @staticmethod
    def _edit_ignoring_unmodified(edit_func, **kwargs):
        """Call a Telegram edit method, treating "message is not modified" as success."""
        try:
            edit_func(**kwargs)
        except ApiTelegramException as e:
            if "message is not modified" not in str(e).lower():
                raise

    def _sync_text_chunks(self, db_cursor, received_id: int, chat_id: int, thread_id, topic_id: int,
                          existing_ids: list[int], chunks: list[str], last_id: int):
        """Make the forwarded text chunks match `chunks`: edit, append, or delete as needed."""
        in_group = chat_id != self.group_id
        for forwarded_id, chunk in zip(existing_ids, chunks):
            self._edit_ignoring_unmodified(
                self.bot.edit_message_text, text=chunk, chat_id=chat_id,
                message_id=forwarded_id, parse_mode="HTML")

        reply_to = existing_ids[-1] if existing_ids else last_id
        for chunk in chunks[len(existing_ids):]:
            sent = self.bot.send_message(chat_id=chat_id, text=chunk, message_thread_id=thread_id,
                                         reply_to_message_id=reply_to, parse_mode="HTML")
            reply_to = sent.message_id
            db_cursor.execute(
                "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
                (received_id, sent.message_id, topic_id, in_group))

        for forwarded_id in existing_ids[len(chunks):]:
            try:
                self.bot.delete_message(chat_id=chat_id, message_id=forwarded_id)
            except ApiTelegramException:
                pass
            db_cursor.execute("DELETE FROM messages WHERE forwarded_id = ? AND topic_id = ? AND in_group = ?",
                              (forwarded_id, topic_id, in_group))

    def handle_reaction(self, message):
        """Mirror message reactions to the other side."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if message.chat.id == self.group_id:
                # Reacted to a user's message forwarded into the group, or to an admin's own reply.
                db_cursor.execute(
                    "SELECT topic_id, received_id FROM messages WHERE forwarded_id = ? AND in_group = 0 LIMIT 1",
                    (message.message_id,))
                result = db_cursor.fetchone()
                if result is None:
                    db_cursor.execute(
                        "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = 1 LIMIT 1",
                        (message.message_id,))
                    result = db_cursor.fetchone()
                if result is None:
                    return
                topic_id, target_id = result
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (chat_id := db_cursor.fetchone()) is None or chat_id[0] is None:
                    return
                chat_id = chat_id[0]
            else:
                if (topic_id := self._user_thread_id(db_cursor, message.chat.id)) is None:
                    return
                db_cursor.execute(
                    "SELECT received_id FROM messages WHERE forwarded_id = ? AND in_group = 1 AND topic_id = ? LIMIT 1",
                    (message.message_id, topic_id))
                result = db_cursor.fetchone()
                if result is None:
                    db_cursor.execute(
                        "SELECT forwarded_id FROM messages WHERE received_id = ? AND in_group = 0 AND topic_id = ? "
                        "LIMIT 1",
                        (message.message_id, topic_id))
                    result = db_cursor.fetchone()
                if result is None:
                    return
                target_id = result[0]
                chat_id = self.group_id
            reaction_ack = getattr(self.message_handler, "reaction_ack", None)
            if reaction_ack is not None:
                reaction_ack.release(chat_id, target_id)
            self.bot.set_message_reaction(chat_id=chat_id, message_id=target_id,
                                          reaction=[message.new_reaction[-1]] if message.new_reaction else [])

    def _save_start_tag(self, message: Message):
        """Persist a valid /start deep-link payload for the user."""
        tag = parse_start_payload(message.text)
        if tag is None:
            return
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """INSERT INTO user_tags (user_id, tag, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(user_id) DO UPDATE SET
                     tag = excluded.tag,
                     updated_at = CURRENT_TIMESTAMP""",
                (message.from_user.id, tag),
            )
            db.commit()

    def _get_user_tag(self, user_id: int) -> str | None:
        """Return the stored start tag for a user, if any."""
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT tag FROM user_tags WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
            return row[0] if row else None

    def _get_setting(self, key: str):
        """Get a setting from the database."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (key,))
            result = db_cursor.fetchone()
            return result[0] if result else None
