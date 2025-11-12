"""Message handling module."""

import html
import sqlite3
import time

from telebot.apihelper import ApiTelegramException, create_forum_topic
from telebot.formatting import apply_html_entities
from telebot.types import Message

from src.config import logger, _
from src.utils.helpers import escape_markdown


class MessageHandler:
    """Handles message forwarding between users and group."""

    def __init__(self, bot, group_id: int, db_path: str, cache, captcha_manager, auto_response_manager,
                 spam_detector_manager=None):
        self.bot = bot
        self.group_id = group_id
        self.db_path = db_path
        self.cache = cache
        self.captcha_manager = captcha_manager
        self.auto_response_manager = auto_response_manager
        self.spam_detector_manager = spam_detector_manager

    def check_valid_chat(self, message: Message) -> bool:
        """Check if message is in valid chat context."""
        return message.chat.id == self.group_id and message.message_thread_id is None

    def handle_message(self, message: Message):
        """Main message handler."""
        # Not responding in General topic
        if self.check_valid_chat(message):
            return

        if message.text:
            msg_text = apply_html_entities(message.text, message.entities, None) if message.entities else html.escape(
                message.text)
        else:
            msg_text = None

        if message.caption:
            msg_caption = apply_html_entities(message.caption, message.entities,
                                              None) if message.entities else html.escape(message.caption)
        else:
            msg_caption = None

        with sqlite3.connect(self.db_path) as db:
            cursor = db.cursor()

            if message.chat.id != self.group_id:
                self._handle_user_message(message, msg_text, msg_caption, cursor, db)
            else:
                self._handle_group_message(message, msg_text, msg_caption, cursor, db)

    def _handle_user_message(self, message: Message, msg_text: str, msg_caption: str, cursor, db):
        """Handle messages from users."""
        start_time = time.time()

        logger.info(
            _("Received message from {}, content: {}, type: {}").format(
                message.from_user.id, message.text, message.content_type))

        # Captcha handler
        if not self._check_captcha(message, cursor, db):
            processing_time = (time.time() - start_time) * 1000
            logger.info(_("Message from user {} blocked by captcha ({:.2f}ms)").format(
                message.from_user.id, processing_time))
            return

        # Check if the user is blocked
        is_blocked = cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = ? LIMIT 1",
                                    (message.from_user.id,)).fetchone() is not None
        
        if is_blocked:
            processing_time = (time.time() - start_time) * 1000
            logger.info(_("Message from blocked user {} rejected ({:.2f}ms)").format(
                message.from_user.id, processing_time))
            
            # Send auto-reply if enabled
            if self.cache.get("setting_blocked_user_reply_enabled") == "enable":
                reply_message = self.cache.get("setting_blocked_user_reply_message")
                if reply_message:
                    try:
                        self.bot.send_message(message.chat.id, reply_message)
                        logger.info(_("Sent auto-reply to blocked user {}").format(message.from_user.id))
                    except Exception as e:
                        logger.error(_("Failed to send auto-reply to blocked user {}: {}").format(
                            message.from_user.id, str(e)))
            
            return

        # Check for spam using detector manager
        is_spam_detected = False
        spam_info = None

        if self.spam_detector_manager:
            is_spam_detected, spam_info = self.spam_detector_manager.detect_spam(message)

            if is_spam_detected:
                # Get spam topic ID from cache
                spam_topic_id = self.cache.get("spam_topic_id")
                if spam_topic_id is None:
                    # Fallback to main topic if spam topic not configured
                    spam_topic_id = None
                    logger.warning(_("Spam topic not configured, using main topic"))

                # Forward directly to spam topic without creating user thread
                fwd_msg = self._send_message_by_type(message, msg_text, msg_caption,
                                                     self.group_id, spam_topic_id, None, silent=True)

                # Build alert message based on detection info
                alert_msg = f"🚫 {_('[Spam Detected]')}\n"
                alert_msg += f"{_('User ID')}: {message.from_user.id}\n"

                if spam_info:
                    if "detector" in spam_info:
                        alert_msg += f"{_('Detector')}: {spam_info['detector']}\n"
                    if "method" in spam_info:
                        alert_msg += f"{_('Method')}: {spam_info['method']}\n"
                    if "matched" in spam_info:
                        alert_msg += f"{_('Matched')}: {spam_info['matched']}\n"
                    if "confidence" in spam_info:
                        alert_msg += f"{_('Confidence')}: {spam_info['confidence']:.2%}\n"

                self.bot.send_message(
                    self.group_id,
                    alert_msg,
                    message_thread_id=spam_topic_id,
                    reply_to_message_id=fwd_msg.message_id,
                    disable_notification=True
                )

                # Log processing time
                processing_time = (time.time() - start_time) * 1000
                logger.info(_("Spam message from user {} processed in {:.2f}ms (matched: {})").format(
                    message.from_user.id, processing_time, spam_info.get('matched', 'unknown')))

                # Done, return early
                return

        # Auto response
        auto_response = self._handle_auto_response(message)

        # Forward message to group
        thread_id = self._get_or_create_thread(message, cursor, db)
        if thread_id is None:
            return

        # Forward the message
        fwd_msg = self._forward_to_group(message, msg_text, msg_caption, thread_id, cursor)
        if fwd_msg is None:
            return

        if auto_response is not None:
            self.bot.send_message(self.group_id, _("[Auto Response]") + auto_response,
                                  message_thread_id=thread_id)

        # Log processing time
        processing_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        logger.info(_("Message from user {} processed in {:.2f}ms").format(
            message.from_user.id, processing_time))

    def _check_captcha(self, message: Message, cursor, db) -> bool:
        """Check and handle captcha verification."""
        if self.cache.get("setting_captcha") == "disable":
            return True

        # Captcha Handler
        if (captcha := self.cache.get(f"captcha_{message.from_user.id}")) is not None:
            if not self.captcha_manager.verify_captcha(message.from_user.id, message.text):
                logger.info(_("User {} entered an incorrect answer").format(message.from_user.id))
                self.bot.send_message(message.chat.id, _("The answer is incorrect, please try again"))
                return False
            logger.info(_("User {} passed the captcha").format(message.from_user.id))
            self.bot.send_message(message.chat.id, _("Verification successful, you can now send messages"))
            self.captcha_manager.set_user_verified(message.from_user.id, db)
            self.cache.delete(f"captcha_{message.from_user.id}")
            return False

        # Check if the user is verified
        if not self.captcha_manager.is_user_verified(message.from_user.id, db):
            logger.info(_("User {} is not verified").format(message.from_user.id))
            match self.cache.get("setting_captcha"):
                case "button":
                    self.captcha_manager.generate_captcha(message.from_user.id,
                                                          self.cache.get("setting_captcha"))
                    return False
                case "math":
                    captcha = self.captcha_manager.generate_captcha(message.from_user.id,
                                                                    self.cache.get("setting_captcha"))
                    self.bot.send_message(message.chat.id,
                                          _("Captcha is enabled. Please solve the following question and send the result directly\n") + captcha)
                    return False
                case _:
                    logger.error(_("Invalid captcha setting"))
                    self.bot.send_message(self.group_id,
                                          _("Invalid captcha setting") + f": {self.cache.get('setting_captcha')}")
                    return False
        return True

    def _handle_auto_response(self, message: Message):
        """Handle automatic responses."""
        if (auto_response_result := self.auto_response_manager.match_auto_response(message.text)) is not None:
            match auto_response_result["type"]:
                case "text":
                    self.bot.send_message(message.chat.id, auto_response_result["response"])
                case "photo":
                    self.bot.send_photo(message.chat.id, photo=auto_response_result["response"])
                case "sticker":
                    self.bot.send_sticker(message.chat.id, sticker=auto_response_result["response"])
                case "video":
                    self.bot.send_video(message.chat.id, video=auto_response_result["response"])
                case "document":
                    self.bot.send_document(message.chat.id, document=auto_response_result["response"])
                case _:
                    logger.error(_("Unsupported message type") + auto_response_result["type"])
            return auto_response_result["response"]
        return None

    def _get_or_create_thread(self, message: Message, cursor, db) -> int:
        """Get or create a thread for the user."""
        userid = message.from_user.id
        if (thread_id := self.cache.get(f"chat_{userid}_threadid")) is None:
            result = cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (userid,))
            thread_id = result.fetchone()
            if thread_id is None:
                # Create a new thread
                logger.info(_("Creating a new thread for user {}").format(userid))
                try:
                    topic = create_forum_topic(chat_id=self.group_id,
                                               name=f"{message.from_user.first_name} | {userid}",
                                               token=self.bot.token)
                except Exception as e:
                    logger.error(e)
                    return None
                cursor.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, ?)",
                               (userid, topic["message_thread_id"]))
                db.commit()
                thread_id = topic["message_thread_id"]

                # Send and pin user info message asynchronously to avoid blocking
                try:
                    username = _("Not set") if message.from_user.username is None else f"@{message.from_user.username}"
                    last_name = "" if message.from_user.last_name is None else f" {message.from_user.last_name}"
                    pin_message = self.bot.send_message(self.group_id,
                                                        f"User ID: [{userid}](tg://openmessage?user_id={userid})\n"
                                                        f"Full Name: {escape_markdown(f'{message.from_user.first_name}{last_name}')}\n"
                                                        f"Username: {escape_markdown(username)}\n",
                                                        message_thread_id=thread_id, parse_mode='markdown')
                    self.bot.pin_chat_message(self.group_id, pin_message.message_id)
                except Exception as e:
                    # Don't fail message forwarding if pinning fails
                    logger.warning(f"Failed to pin info message for user {userid}: {e}")
            else:
                thread_id = thread_id[0]
            self.cache.set(f"chat_{userid}_threadid", thread_id)
        return thread_id

    def _forward_to_group(self, message: Message, msg_text: str, msg_caption: str,
                          thread_id: int, cursor) -> Message:
        """Forward a message to the group."""
        try:
            reply_id = self._get_reply_id(message, thread_id, cursor, in_group=False)
            fwd_msg = self._send_message_by_type(message, msg_text, msg_caption,
                                                 self.group_id, thread_id, reply_id)
            cursor.execute(
                "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
                (message.message_id, fwd_msg.message_id, thread_id, False))
            return fwd_msg
        except ApiTelegramException as e:
            if "message thread not found" in str(e):
                cursor.execute("DELETE FROM topics WHERE thread_id = ?", (thread_id,))
                cursor.connection.commit()
                self.cache.delete(f"threadid_{thread_id}_userid")
                self.cache.delete(f"chat_{message.from_user.id}_threadid")
                # Re-queue the message
                return None
            logger.error(_("Failed to forward message from user {}").format(message.from_user.id))
            logger.error(e)
            self.bot.send_message(self.group_id,
                                  _("Failed to forward message from user {}").format(message.from_user.id),
                                  message_thread_id=None)
            self.bot.forward_message(self.group_id, message.chat.id, message_id=message.message_id)
            return None

    def _handle_group_message(self, message: Message, msg_text: str, msg_caption: str, cursor, db):
        """Handle messages from group to users."""
        if (user_id := self.cache.get(f"threadid_{message.message_thread_id}_userid")) is None:
            result = cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                                    (message.message_thread_id,))
            user_id = result.fetchone()
            user_id = user_id[0] if user_id is not None else None

        if user_id is not None:
            self.cache.set(f"threadid_{message.message_thread_id}_userid", user_id)
            reply_id = self._get_reply_id(message, message.message_thread_id, cursor, in_group=True)

            try:
                fwd_msg = self._send_message_by_type(message, msg_text, msg_caption,
                                                     user_id, None, reply_id)
                cursor.execute(
                    "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
                    (message.message_id, fwd_msg.message_id, message.message_thread_id, True))
            except ApiTelegramException as e:
                logger.error(_("Failed to forward message to user {}").format(user_id))
                logger.error(e)
                self.bot.send_message(self.group_id,
                                      _("[Alert]") + _("Failed to forward message to user {}").format(
                                          user_id) + "\n" + str(e),
                                      message_thread_id=message.message_thread_id)
        else:
            self.bot.send_message(self.group_id, _("Chat not found, please remove this topic manually"),
                                  message_thread_id=message.message_thread_id)
            try:
                from telebot.apihelper import close_forum_topic
                close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                                  token=self.bot.token)
            except ApiTelegramException:
                pass

    def _get_reply_id(self, message: Message, topic_id: int, cursor, in_group: bool):
        """Get the reply message ID if replying to a message."""
        if message.reply_to_message is None:
            return None

        if message.reply_to_message.from_user.id == message.from_user.id:
            cursor.execute(
                "SELECT forwarded_id FROM messages WHERE received_id = ? AND topic_id = ? AND in_group = ? LIMIT 1",
                (message.reply_to_message.message_id, topic_id, in_group))
        else:
            cursor.execute(
                "SELECT received_id FROM messages WHERE forwarded_id = ? AND topic_id = ? AND in_group = ? LIMIT 1",
                (message.reply_to_message.message_id, topic_id, not in_group))

        if (result := cursor.fetchone()) is not None:
            return int(result[0])
        return None

    def _send_message_by_type(self, message: Message, msg_text: str, msg_caption: str,
                              chat_id: int, thread_id: int = None, reply_id: int = None,
                              silent: bool = False) -> Message:
        """Send a message based on its type."""
        match message.content_type:
            case "photo":
                return self.bot.send_photo(chat_id=chat_id, photo=message.photo[-1].file_id,
                                           caption=msg_caption, message_thread_id=thread_id,
                                           reply_to_message_id=reply_id, parse_mode='HTML',
                                           disable_notification=silent)
            case "text":
                return self.bot.send_message(chat_id=chat_id, text=msg_text,
                                             message_thread_id=thread_id,
                                             reply_to_message_id=reply_id, parse_mode='HTML',
                                             disable_notification=silent)
            case "sticker":
                return self.bot.send_sticker(chat_id=chat_id, sticker=message.sticker.file_id,
                                             message_thread_id=thread_id,
                                             reply_to_message_id=reply_id,
                                             disable_notification=silent)
            case "video":
                return self.bot.send_video(chat_id=chat_id, video=message.video.file_id,
                                           caption=msg_caption, message_thread_id=thread_id,
                                           reply_to_message_id=reply_id, parse_mode='HTML',
                                           disable_notification=silent)
            case "document":
                return self.bot.send_document(chat_id=chat_id, document=message.document.file_id,
                                              caption=msg_caption, message_thread_id=thread_id,
                                              reply_to_message_id=reply_id, parse_mode='HTML',
                                              disable_notification=silent)
            case "audio":
                return self.bot.send_audio(chat_id=chat_id, audio=message.audio.file_id,
                                           caption=msg_caption, message_thread_id=thread_id,
                                           reply_to_message_id=reply_id, parse_mode='HTML',
                                           disable_notification=silent)
            case "voice":
                return self.bot.send_voice(chat_id=chat_id, voice=message.voice.file_id,
                                           caption=msg_caption, message_thread_id=thread_id,
                                           reply_to_message_id=reply_id, parse_mode='HTML',
                                           disable_notification=silent)
            case "animation":
                return self.bot.send_animation(chat_id=chat_id, animation=message.animation.file_id,
                                               caption=msg_caption, message_thread_id=thread_id,
                                               reply_to_message_id=reply_id, parse_mode='HTML',
                                               disable_notification=silent)
            case "contact":
                return self.bot.send_contact(chat_id=chat_id,
                                             phone_number=message.contact.phone_number,
                                             first_name=message.contact.first_name,
                                             last_name=message.contact.last_name,
                                             message_thread_id=thread_id,
                                             reply_to_message_id=reply_id,
                                             disable_notification=silent)
            case _:
                logger.error(_("Unsupported message type") + message.content_type)
                raise ValueError(_("Unsupported message type") + message.content_type)
