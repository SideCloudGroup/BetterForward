import argparse
import gettext
import importlib
import json
import logging
import os
import queue
import random
import re
import signal
import sqlite3
import threading
from traceback import print_exc

from diskcache import Cache
from telebot import types, TeleBot
from telebot.apihelper import create_forum_topic, close_forum_topic, ApiTelegramException, delete_forum_topic, \
    reopen_forum_topic
from telebot.types import Message, MessageReactionUpdated

parser = argparse.ArgumentParser(description="")
parser.add_argument("-token", type=str, required=True, help="Telegram bot token")
parser.add_argument("-group_id", type=str, required=True, help="Group ID")
parser.add_argument("-language", type=str, default="en_US", help="Language", choices=["en_US", "zh_CN", "ja_JP"])
args = parser.parse_args()

logger = logging.getLogger()
logger.setLevel("INFO")
BASIC_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
formatter = logging.Formatter(BASIC_FORMAT, DATE_FORMAT)
chlr = logging.StreamHandler()
chlr.setFormatter(formatter)
logger.addHandler(chlr)

project_root = os.path.dirname(os.path.abspath(__file__))
locale_dir = os.path.join(project_root, "locale")
gettext.bindtextdomain("BetterForward", locale_dir)
gettext.textdomain("BetterForward")
try:
    _ = gettext.translation("BetterForward", locale_dir, languages=[args.language]).gettext
except FileNotFoundError:
    _ = gettext.gettext

stop = False


def handle_sigterm(*args):
    global stop
    stop = True
    raise KeyboardInterrupt()


def escape_markdown(text):
    escape_chars = r'\*_`\[\]()'
    return re.sub(f'([{escape_chars}])', r'\\\1', text)


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)


class TGBot:
    def __init__(self, bot_token: str, group_id: str, db_path: str = "./data/storage.db"):
        logger.info(_("Starting BetterForward..."))
        self.group_id = int(group_id)
        self.bot = TeleBot(token=bot_token)
        self.bot.edited_message_handler(func=lambda m: True)(self.handle_edit)
        self.bot.message_handler(commands=["start", "help"])(self.help)
        self.bot.message_handler(commands=["ban"])(self.ban_user)
        self.bot.message_handler(commands=["unban"])(self.unban_user)
        self.bot.message_handler(commands=["terminate"])(self.handle_terminate)
        self.bot.message_handler(commands=["delete"])(self.delete_message)
        self.bot.message_handler(commands=["verify"])(self.handle_verify)
        self.bot.message_handler(func=lambda m: True, content_types=["photo", "text", "sticker", "video", "document"])(
            self.push_messages)
        self.bot.message_reaction_handler(func=lambda message: True)(self.handle_reaction)
        self.bot.callback_query_handler(func=lambda call: True)(self.callback_query)
        self.db_path = db_path
        # Check path exists
        if not os.path.exists(os.path.dirname(db_path)):
            os.makedirs(os.path.dirname(db_path))
        self.upgrade_db()
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
        self.cache = Cache()
        self.load_settings()
        self.check_permission()
        self.message_queue = queue.Queue()
        self.message_processor = threading.Thread(target=self.process_messages)
        self.message_processor.start()
        self.bot.infinity_polling(skip_pending=True, timeout=30,
                                  allowed_updates=['message', 'edited_message', 'callback_query', 'my_chat_member',
                                                   'message_reaction', 'message_reaction_count', ])

    def check_valid_chat(self, message: Message):
        return message.chat.id == self.group_id and message.message_thread_id is None

    def upgrade_db(self):
        try:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                db_cursor.execute("SELECT value FROM settings WHERE key = 'db_version'")
                current_version = int(db_cursor.fetchone()[0])
        except sqlite3.OperationalError:
            current_version = 0
        db_migrate_dir = "./db_migrate"
        files = [f for f in os.listdir(db_migrate_dir) if f.endswith('.py')]
        files.sort(key=lambda x: int(x.split('_')[0]))
        for file in files:
            try:
                if (version := int(file.split('_')[0])) > current_version:
                    logger.info(_("Upgrading database to version {}").format(version))
                    module = importlib.import_module(f"db_migrate.{file[:-3]}")
                    module.upgrade(self.db_path)
                    with sqlite3.connect(self.db_path) as db:
                        db_cursor = db.cursor()
                        db_cursor.execute("UPDATE settings SET value = ? WHERE key = 'db_version'", (str(version),))
                        db.commit()
            except Exception:
                logger.error(_("Failed to upgrade database"))
                print_exc()
                exit(1)

    def load_settings(self):
        # Load settings
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT key, value FROM settings")
            for key, value in db_cursor.fetchall():
                self.cache.set(f"setting_{key}", value)

    def generate_captcha(self, user_id: int, type="math"):
        match type:
            case "math":
                num1 = random.randint(1, 10)
                num2 = random.randint(1, 10)
                answer = num1 + num2
                self.cache.set(f"captcha_{user_id}", answer, 300)
                return f"{num1} + {num2} = ?"
            case "button":
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("Click to verify", callback_data=json.dumps(
                    {"action": "verify_button", "user_id": user_id})))
                self.bot.send_message(user_id, _("Please click the button to verify."), reply_markup=markup)
            case _:
                raise ValueError(_("Invalid captcha setting"))

    def handle_button_captcha(self, call: types.CallbackQuery):
        data = json.loads(call.data)
        if data.get("action") == "verify_button":
            user_id = data.get("user_id")
            if user_id:
                self.cache.set(f"verified_{user_id}", True, 1800)  # 设置用户为已验证
                self.bot.answer_callback_query(call.id)
                self.bot.send_message(user_id, _("Verification successful, you can now send messages"))
                self.bot.delete_message(call.message.chat.id, call.message.message_id)
                with sqlite3.connect(self.db_path) as db:
                    db_cursor = db.cursor()
                    db_cursor.execute("INSERT INTO verified_users (user_id) VALUES (?)", (user_id,))
                    db.commit()
                self.cache.set(f"verified_{user_id}", True, 1800)
            else:
                self.bot.answer_callback_query(call.id)
                self.bot.send_message(call.message.chat.id, _("Invalid user ID"))

    # Get thread_id to terminate when needed
    def terminate_thread(self, thread_id=None, user_id=None):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if thread_id is not None:
                result = db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (thread_id,))
                if (user_id := result.fetchone()) is not None:
                    user_id = user_id[0]
                    db_cursor.execute("DELETE FROM topics WHERE thread_id = ?", (thread_id,))
                    db.commit()
            elif user_id is not None:
                result = db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (user_id,))
                if (thread_id := result.fetchone()[0]) is not None:
                    db_cursor.execute("DELETE FROM topics WHERE user_id = ?", (user_id,))
                    db.commit()
            self.cache.delete(f"chat_{user_id}_threadid")
            self.cache.delete(f"threadid_{thread_id}_userid")
            try:
                delete_forum_topic(chat_id=self.group_id, message_thread_id=thread_id, token=self.bot.token)
            except ApiTelegramException:
                pass
            db_cursor.execute("DELETE FROM messages WHERE topic_id = ?", (thread_id,))
        logger.info(_("Terminating thread") + str(thread_id))

    # To terminate and totally delete the topic
    def handle_terminate(self, message: Message):
        if message.chat.id == self.group_id:
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
            try:
                self.terminate_thread(thread_id=thread_id, user_id=user_id)
                if message.message_thread_id is None:
                    self.bot.reply_to(message, _("Thread terminated"))
            except Exception:
                logger.error(_("Failed to terminate the thread") + str(thread_id))
                self.bot.reply_to(message, _("Failed to terminate the thread"))
        else:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))

    def match_auto_response(self, text):
        if text is None:
            return None
        with sqlite3.connect(self.db_path) as db:
            # Check for exact match
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT value, topic_action, type FROM auto_response WHERE key = ? AND is_regex = 0 LIMIT 1",
                (text,))
            result = db_cursor.fetchone()
            if result is not None:
                return {"response": result[0], "topic_action": result[1], "type": result[2]}

            # Check for regex
            db_cursor.execute("SELECT key, value, topic_action, type FROM auto_response WHERE is_regex = 1")
            result = db_cursor.fetchall()
            for row in result:
                try:
                    if re.match(row[0], text):
                        return {"response": row[1], "topic_action": row[2], "type": row[3]}
                except re.error:
                    logger.error(_("Invalid regular expression: {}").format(row[0]))
                    return None
            return None

    # Push messages to the queue
    def push_messages(self, message: Message):
        self.message_queue.put(message)

    # Main message handler
    def handle_message(self, message: Message, retry=False):
        # Not responding in General topic
        if self.check_valid_chat(message):
            return
        with sqlite3.connect(self.db_path) as db:
            curser = db.cursor()
            if message.chat.id != self.group_id:
                logger.info(
                    _("Received message from {}, content: {}, type: {}").format(message.from_user.id, message.text,
                                                                                message.content_type))
                if self.cache.get("setting_captcha") != "disable":
                    # Captcha Handler
                    if (captcha := self.cache.get(f"captcha_{message.from_user.id}")) is not None:
                        if message.text != str(captcha):
                            logger.info(_("User {} entered an incorrect answer").format(message.from_user.id))
                            self.bot.send_message(message.chat.id, _("The answer is incorrect, please try again"))
                            return
                        logger.info(_("User {} passed the captcha").format(message.from_user.id))
                        self.bot.send_message(message.chat.id, _("Verification successful, you can now send messages"))
                        curser.execute("INSERT INTO verified_users (user_id) VALUES (?)",
                                       (message.from_user.id,))
                        self.cache.delete(f"captcha_{message.from_user.id}")
                        self.cache.set(f"verified_{message.from_user.id}", True, 1800)
                        return

                    # Check if the user is verified
                    verified = self.cache.get(f"verified_{message.from_user.id}")
                    if verified is None:
                        result = curser.execute("SELECT 1 FROM verified_users WHERE user_id = ? LIMIT 1",
                                                (message.from_user.id,))
                        if result.fetchone() is not None:
                            verified = True
                        else:
                            verified = False

                    if not verified:
                        logger.info(_("User {} is not verified").format(message.from_user.id))
                        match self.cache.get("setting_captcha"):
                            case "button":
                                self.generate_captcha(message.from_user.id, self.cache.get("setting_captcha"))
                                return
                            case "math":
                                captcha = self.generate_captcha(message.from_user.id, self.cache.get("setting_captcha"))
                                self.bot.send_message(message.chat.id,
                                                      _("Captcha is enabled. Please solve the following question and send the result directly\n") + captcha)
                                return
                            case _:
                                logger.error(_("Invalid captcha setting"))
                                self.bot.send_message(self.group_id,
                                                      _("Invalid captcha setting") + f": {self.cache.get('setting_captcha')}")
                                return
                    else:
                        self.cache.set(f"verified_{message.from_user.id}", verified, 1800)

                # Check if the user is banned
                if (result := curser.execute("SELECT ban FROM topics WHERE user_id = ? LIMIT 1",
                                             (message.from_user.id,)).fetchone()) and result[0] == 1:
                    logger.info(_("User {} is banned").format(message.from_user.id))
                    return
                # Auto response
                topic_action = False
                auto_response = None
                if (auto_response_result := self.match_auto_response(message.text)) is not None:
                    if not retry:
                        match auto_response_result["type"]:
                            case "text":
                                self.bot.send_message(message.chat.id,
                                                      auto_response_result["response"])
                            case "photo":
                                self.bot.send_photo(message.chat.id,
                                                    photo=auto_response_result["response"])
                            case "sticker":
                                self.bot.send_sticker(message.chat.id,
                                                      sticker=auto_response_result["response"])
                            case "video":
                                self.bot.send_video(message.chat.id,
                                                    video=auto_response_result["response"])
                            case "document":
                                self.bot.send_document(message.chat.id,
                                                       document=auto_response_result["response"])
                            case _:
                                logger.error(_("Unsupported message type") + auto_response_result["type"])
                    if auto_response_result["topic_action"]:
                        topic_action = True
                        auto_response = auto_response_result["response"]
                    else:
                        return
                # Forward message to group
                userid = message.from_user.id
                if (thread_id := self.cache.get(f"chat_{userid}_threadid")) is None:
                    result = curser.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (userid,))
                    thread_id = result.fetchone()
                    if thread_id is None:
                        # Create a new thread
                        logger.info(_("Creating a new thread for user {}").format(userid))
                        try:
                            topic = create_forum_topic(chat_id=self.group_id, name=message.from_user.first_name,
                                                       token=self.bot.token)
                        except Exception as e:
                            logger.error(e)
                            return
                        curser.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, ?)",
                                       (userid, topic["message_thread_id"]))
                        db.commit()
                        thread_id = topic["message_thread_id"]
                        username = _(
                            "Not set") if message.from_user.username is None else f"@{message.from_user.username}"
                        last_name = "" if message.from_user.last_name is None else f" {message.from_user.last_name}"
                        pin_message = self.bot.send_message(self.group_id,
                                                            f"User ID: [{userid}](tg://openmessage?user_id={userid})\n"
                                                            f"Full Name: {escape_markdown(f"{message.from_user.first_name}{last_name}")}\n"
                                                            f"Username: {escape_markdown(username)}\n",
                                                            message_thread_id=thread_id, parse_mode='markdown')
                        self.bot.pin_chat_message(self.group_id, pin_message.message_id)
                    else:
                        thread_id = thread_id[0]
                    self.cache.set(f"chat_{userid}_threadid", thread_id)
                try:
                    reply_id = None
                    if message.reply_to_message is not None:
                        if message.reply_to_message.from_user.id == message.from_user.id:
                            curser.execute(
                                "SELECT forwarded_id FROM messages WHERE received_id = ? AND topic_id = ? AND in_group = ? LIMIT 1",
                                (message.reply_to_message.message_id, thread_id, False,))
                        else:
                            curser.execute(
                                "SELECT received_id FROM messages WHERE forwarded_id = ? AND topic_id = ? AND in_group = ? LIMIT 1",
                                (message.reply_to_message.message_id, thread_id, True,))
                        if (result := curser.fetchone()) is not None:
                            reply_id = int(result[0])
                    match message.content_type:
                        case "photo":
                            fwd_msg = self.bot.send_photo(chat_id=self.group_id,
                                                          photo=message.photo[-1].file_id,
                                                          caption=message.caption,
                                                          message_thread_id=thread_id,
                                                          reply_to_message_id=reply_id)
                        case "text":
                            fwd_msg = self.bot.send_message(chat_id=self.group_id,
                                                            text=message.text,
                                                            message_thread_id=thread_id,
                                                            reply_to_message_id=reply_id)
                        case "sticker":
                            fwd_msg = self.bot.send_sticker(chat_id=self.group_id,
                                                            sticker=message.sticker.file_id,
                                                            message_thread_id=thread_id,
                                                            reply_to_message_id=reply_id)
                        case "video":
                            fwd_msg = self.bot.send_video(chat_id=self.group_id,
                                                          video=message.video.file_id,
                                                          caption=message.caption,
                                                          message_thread_id=thread_id,
                                                          reply_to_message_id=reply_id)
                        case "document":
                            fwd_msg = self.bot.send_document(chat_id=self.group_id,
                                                             document=message.document.file_id,
                                                             caption=message.caption,
                                                             message_thread_id=thread_id,
                                                             reply_to_message_id=reply_id)
                        case _:
                            logger.error(_("Unsupported message type") + message.content_type)
                            return
                    curser.execute(
                        "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
                        (message.message_id, fwd_msg.message_id, thread_id, False,))
                except ApiTelegramException as e:
                    if not retry:
                        self.terminate_thread(thread_id=thread_id)
                        return self.handle_message(message, retry=True)
                    else:
                        logger.error(_("Failed to forward message from user {}".format(message.from_user.id)))
                        logger.error(e)
                        self.bot.send_message(self.group_id,
                                              _("Failed to forward message from user {}".format(message.from_user.id)),
                                              message_thread_id=None)
                        self.bot.forward_message(self.group_id, message.chat.id, message_id=message.message_id)
                        return
                if topic_action:
                    self.bot.send_message(self.group_id, _("[Auto Response]") + auto_response,
                                          message_thread_id=thread_id)
            else:
                # Forward message to user
                if (user_id := self.cache.get(f"threadid_{message.message_thread_id}_userid")) is None:
                    result = curser.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                                            (message.message_thread_id,))
                    user_id = result.fetchone()[0]
                    self.cache.set(f"threadid_{message.message_thread_id}_userid", user_id)
                if user_id is not None:
                    reply_id = None
                    if message.reply_to_message is not None:
                        if message.reply_to_message.from_user.id == message.from_user.id:
                            curser.execute(
                                "SELECT forwarded_id FROM messages WHERE received_id = ? AND topic_id = ? AND in_group = ? LIMIT 1",
                                (message.reply_to_message.message_id, message.message_thread_id, True,))
                        else:
                            curser.execute(
                                "SELECT received_id FROM messages WHERE forwarded_id = ? AND topic_id = ? AND in_group = ? LIMIT 1",
                                (message.reply_to_message.message_id, message.message_thread_id, False,))
                        if (result := curser.fetchone()) is not None:
                            reply_id = int(result[0])
                    match message.content_type:
                        case "photo":
                            fwd_msg = self.bot.send_photo(chat_id=user_id,
                                                          photo=message.photo[-1].file_id,
                                                          caption=message.caption,
                                                          reply_to_message_id=reply_id)
                        case "text":
                            fwd_msg = self.bot.send_message(chat_id=user_id,
                                                            text=message.text,
                                                            reply_to_message_id=reply_id)
                        case "sticker":
                            fwd_msg = self.bot.send_sticker(chat_id=user_id,
                                                            sticker=message.sticker.file_id,
                                                            reply_to_message_id=reply_id)
                        case "video":
                            fwd_msg = self.bot.send_video(chat_id=user_id,
                                                          video=message.video.file_id,
                                                          caption=message.caption,
                                                          reply_to_message_id=reply_id)
                        case "document":
                            fwd_msg = self.bot.send_document(chat_id=user_id,
                                                             document=message.document.file_id,
                                                             caption=message.caption,
                                                             reply_to_message_id=reply_id)
                        case _:
                            logger.error(_("Unsupported message type") + message.content_type)
                            return
                    curser.execute(
                        "INSERT INTO messages (received_id, forwarded_id, topic_id, in_group) VALUES (?, ?, ?, ?)",
                        (message.message_id, fwd_msg.message_id, message.message_thread_id, True,))
                else:
                    self.bot.send_message(self.group_id, _("Chat not found, please remove this topic manually"),
                                          message_thread_id=message.message_thread_id)
                    close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                                      token=self.bot.token)

    # Process messages in the queue
    def process_messages(self):
        while stop is False:
            try:
                message = self.message_queue.get(timeout=1)
                self.handle_message(message)
            except queue.Empty:
                continue  # Skip to the next iteration if the queue is empty
            except Exception as e:
                logger.error(_("Failed to process message: {}").format(e))
        self.bot.stop_bot()

    def check_permission(self):
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

    def add_auto_response(self, message: Message):
        if not self.check_valid_chat(message):
            return
        msg = self.bot.edit_message_text(text=_(
            "Let's set up an automatic response.\nSend /cancel to cancel this operation.\n\n"
            "Please send the keywords or regular expression that should trigger this response."),
            chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.add_auto_response_type)

    def add_auto_response_type(self, message: Message):
        # 选择是否是正则表达式
        if not self.check_valid_chat(message):
            return
        if isinstance(message.text, str) and message.text == "/cancel":
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        self.cache.set("auto_response_key", message.text, 300)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅" + _("Yes"),
                                              callback_data=json.dumps(
                                                  {"action": "set_auto_reply_type", "regex": True})))
        markup.add(types.InlineKeyboardButton("❌" + _("No"),
                                              callback_data=json.dumps(
                                                  {"action": "set_auto_reply_type", "regex": False})))
        markup.add(
            types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "auto_reply"})))
        help_text = _("Trigger: {}").format(self.cache.get("auto_response_key")) + "\n\n"
        help_text += _("Is this a regular expression?")
        self.bot.send_message(text=help_text, chat_id=self.group_id, reply_markup=markup)

    def add_auto_response_value(self, message: Message):
        if not self.check_valid_chat(message):
            return
        if isinstance(message.text, str) and message.text == "/cancel":
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if self.cache.get("auto_response_regex") is True:
            try:
                re.compile(message.text)
            except re.error:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "auto_reply"})))
                self.bot.edit_message_text(text=_("Invalid regular expression"), chat_id=self.group_id,
                                           message_id=message.message_id, reply_markup=markup)
                return
        msg = self.bot.edit_message_text(text=_("Please send the response content. It can be text, stickers, photos "
                                                "and so on."),
                                         chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.add_auto_response_topic_action)

    def add_auto_response_topic_action(self, message: Message):
        if not self.check_valid_chat(message):
            return
        if isinstance(message.text, str) and message.text == "/cancel":
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            self.cache.delete("auto_response_key")
            return
        if self.cache.get("auto_response_key") is None:
            self.bot.send_message(self.group_id, _("The operation has timed out. Please initiate the process again."))
            return
        self.cache.set("auto_response_key", self.cache.get("auto_response_key"), 300)
        match message.content_type:
            case "photo":
                self.cache.set("auto_response_value", message.photo[-1].file_id, 300)
                self.cache.set("auto_response_type", "photo", 300)
            case "text":
                self.cache.set("auto_response_value", message.text, 300)
                self.cache.set("auto_response_type", "text", 300)
            case "sticker":
                self.cache.set("auto_response_value", message.sticker.file_id, 300)
                self.cache.set("auto_response_type", "sticker", 300)
            case "video":
                self.cache.set("auto_response_value", message.video.file_id, 300)
                self.cache.set("auto_response_type", "video", 300)
            case "document":
                self.cache.set("auto_response_value", message.document.file_id, 300)
                self.cache.set("auto_response_type", "document", 300)
            case _:
                self.bot.send_message(self.group_id, _("Unsupported message type"))
                return
        self.cache.set("auto_response_regex", self.cache.get("auto_response_regex"), 300)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅" + _("Forward message"),
                                              callback_data=json.dumps(
                                                  {"action": "add_auto_reply", "topic_action": True})))
        markup.add(types.InlineKeyboardButton("❌" + _("Do not forward message"),
                                              callback_data=json.dumps(
                                                  {"action": "add_auto_reply", "topic_action": False})))
        markup.add(
            types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "add_auto_reply"})))
        help_text = ""
        help_text += _("Trigger: {}").format(self.cache.get("auto_response_key")) + "\n"
        help_text += _("Response: {}").format(
            self.cache.get("auto_response_value") if self.cache.get("auto_response_type") == "text" else self.cache.get(
                "auto_response_type")) + "\n"
        help_text += _("Is regex: {}").format("✅" if self.cache.get("auto_response_regex") else "❌") + "\n\n"
        self.bot.send_message(self.group_id, help_text + _("Do you want to forward the message to the user?"),
                              reply_markup=markup,
                              message_thread_id=None)

    def process_add_auto_reply(self, message: Message, data: dict):
        key = self.cache.pop("auto_response_key")
        value = self.cache.pop("auto_response_value")
        is_regex = self.cache.pop("auto_response_regex")
        type = self.cache.pop("auto_response_type")
        markup = types.InlineKeyboardMarkup()
        back_button = types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"}))
        if "topic_action" not in data or None in [key, value, is_regex, type]:
            self.bot.delete_message(self.group_id, message.id)
            self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
            return
        topic_action = data["topic_action"]
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "INSERT INTO auto_response (key, value, topic_action, is_regex, type) VALUES (?, ?, ?, ?, ?)",
                (key, value, topic_action, is_regex, type))
            db.commit()
        markup.add(back_button)
        self.bot.edit_message_text(_("Auto reply added"), message.chat.id, message.message_id, reply_markup=markup)

    def menu(self, message, edit=False):
        if not self.check_valid_chat(message):
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💬" + _("Auto Reply"),
                                              callback_data=json.dumps({"action": "auto_reply"})))
        markup.add(types.InlineKeyboardButton("📙" + _("Default Message"),
                                              callback_data=json.dumps({"action": "default_msg"})))
        markup.add(types.InlineKeyboardButton("⛔" + _("Banned Users"),
                                              callback_data=json.dumps({"action": "ban_user"})))
        markup.add(types.InlineKeyboardButton("🔒" + _("Captcha Settings"),
                                              callback_data=json.dumps({"action": "captcha_settings"})))
        markup.add(types.InlineKeyboardButton("📢" + _("Broadcast Message"),
                                              callback_data=json.dumps({"action": "broadcast_message"})))
        if edit:
            self.bot.edit_message_text(_("Menu"), message.chat.id, message.message_id, reply_markup=markup)
        else:
            self.bot.send_message(self.group_id, _("Menu"), reply_markup=markup, message_thread_id=None)

    def help(self, message: Message):
        if self.check_valid_chat(message):
            self.menu(message)
        else:
            if (response := self.get_setting('default_message')) is None:
                self.bot.send_message(message.chat.id, _("I'm a bot that forwards messages, so please just tell me "
                                                         "what you want to say.") + "\n" +
                                      "Powered by [BetterForward](https://github.com/SideCloudGroup/BetterForward)",
                                      parse_mode="Markdown",
                                      disable_web_page_preview=True)
            else:
                self.bot.send_message(message.chat.id, response)

    def get_setting(self, key):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (key,))
            result = db_cursor.fetchone()
            return result[0] if result else None

    def manage_auto_reply(self, message: Message, page: int = 1, page_size: int = 5):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            markup = types.InlineKeyboardMarkup()
            back_button = types.InlineKeyboardButton("⬅️" + _("Back"),
                                                     callback_data=json.dumps({"action": "auto_reply"}))

            # Calculate pagination
            offset = (page - 1) * page_size
            db_cursor.execute("SELECT COUNT(*) FROM auto_response")
            total_responses = db_cursor.fetchone()[0]
            total_pages = (total_responses + page_size - 1) // page_size

            # Fetch data with limits
            db_cursor.execute("SELECT id, key, value, topic_action, is_regex, type FROM auto_response LIMIT ? OFFSET ?",
                              (page_size, offset))
            auto_responses = db_cursor.fetchall()

            text = _("Auto Reply List:") + "\n" + _("Total: {}").format(total_responses) + "\n" + _("Page: {}").format(
                page) + "/" + str(total_pages) + "\n\n"
            id_buttons = []
            for auto_response in auto_responses:
                text += "-" * 20 + "\n"
                text += f"ID: {auto_response[0]}\n"
                text += _("Trigger: {}").format(auto_response[1]) + "\n"
                text += _("Response: {}").format(
                    auto_response[2] if auto_response[5] == "text" else auto_response[5]) + "\n"
                text += _("Forward message: {}").format("✅" if auto_response[3] else "❌") + "\n"
                text += _("Is regex: {}").format("✅" if auto_response[4] else "❌") + "\n\n"
                id_buttons.append(types.InlineKeyboardButton(text=auto_response[0],
                                                             callback_data=json.dumps({"action": "select_auto_reply",
                                                                                       "id": auto_response[0]})))

            # Add ID buttons in a single row
            if id_buttons:
                markup.row(*id_buttons)

            # Add pagination buttons
            if 1 < page < total_pages:
                markup.row(
                    types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                               callback_data=json.dumps(
                                                   {"action": "manage_auto_reply", "page": page - 1})),
                    types.InlineKeyboardButton("➡️" + _("Next Page"),
                                               callback_data=json.dumps(
                                                   {"action": "manage_auto_reply", "page": page + 1}))
                )
            elif page > 1:
                markup.add(types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                                      callback_data=json.dumps(
                                                          {"action": "manage_auto_reply", "page": page - 1})))
            elif page < total_pages:
                markup.add(types.InlineKeyboardButton("➡️" + _("Next Page"),
                                                      callback_data=json.dumps(
                                                          {"action": "manage_auto_reply", "page": page + 1})))

            # Add back button in a separate row
            markup.add(back_button)
            self.bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)

    def select_auto_reply(self, message: Message, id: int):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT key, value, topic_action, is_regex, type FROM auto_response WHERE id = ? LIMIT 1",
                              (id,))
            auto_response = db_cursor.fetchone()
            if auto_response is None:
                self.bot.send_message(self.group_id, _("Auto reply not found"))
                return
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌" + _("Delete"),
                                                  callback_data=json.dumps({"action": "delete_auto_reply", "id": id})))
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "manage_auto_reply"})))
            text = _("Trigger: {}").format(auto_response[0]) + "\n"
            text += _("Response: {}").format(
                auto_response[1] if auto_response[4] == "text" else auto_response[4]) + "\n"
            text += _("Forward message: {}").format("✅" if auto_response[2] else "❌") + "\n"
            text += _("Is regex: {}").format("✅" if auto_response[3] else "❌") + "\n\n"
            self.bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)

    def delete_auto_reply(self, message: Message, id: int):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("DELETE FROM auto_response WHERE id = ?", (id,))
            db.commit()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "manage_auto_reply"})))
        self.bot.edit_message_text(_("Auto reply deleted"), chat_id=message.chat.id, message_id=message.id,
                                   reply_markup=markup)

    def ban_user(self, message: Message):
        if message.chat.id == self.group_id and message.message_thread_id is None:
            self.bot.send_message(self.group_id, _("This command is not available in the main chat."))
            return
        if message.chat.id != self.group_id:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users."))
            return
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE topics SET ban = 1 WHERE thread_id = ?", (message.message_thread_id,))
            # Remove user from verified list
            db_cursor.execute("DELETE FROM verified_users WHERE user_id = ?", (message.from_user.id,))
            db.commit()
        self.bot.send_message(self.group_id, _("User banned"), message_thread_id=message.message_thread_id)
        close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id, token=self.bot.token)

    def unban_user(self, message: Message, user_id: int = None):
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
                db_cursor.execute("UPDATE topics SET ban = 0 WHERE thread_id = ?", (message.message_thread_id,))
                db.commit()
            self.bot.send_message(self.group_id, _("User unbanned"), message_thread_id=message.message_thread_id)
            try:
                reopen_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                                   token=self.bot.token)
            except ApiTelegramException:
                pass
        else:
            with sqlite3.connect(self.db_path) as db:
                db_cursor = db.cursor()
                # Check user exists
                db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (user_id,))
                thread_id = db_cursor.fetchone()
                if thread_id is None:
                    self.bot.send_message(self.group_id, _("User not found"))
                    return
                db_cursor.execute("UPDATE topics SET ban = 0 WHERE user_id = ?", (user_id,))
                db.commit()
            try:
                reopen_forum_topic(chat_id=self.group_id, message_thread_id=thread_id,
                                   token=self.bot.token)
            except ApiTelegramException:
                pass
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"})))
            if message.from_user.id == self.bot.get_me().id:
                self.bot.edit_message_text(_("User unbanned"), message.chat.id, message.message_id, reply_markup=markup)
            else:
                self.bot.send_message(self.group_id, _("User unbanned"), reply_markup=markup)

    def manage_ban_user(self, message: Message):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            markup = types.InlineKeyboardMarkup()
            back_button = types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"}))
            db_cursor.execute("SELECT user_id FROM topics WHERE ban = 1")
            banned_users = db_cursor.fetchall()
            text = _("Banned User List:") + "\n"
            for user in banned_users:
                text += "-" * 20 + "\n"
                text += f"User ID: {user[0]}\n"
                markup.add(types.InlineKeyboardButton(text=user[0],
                                                      callback_data=json.dumps(
                                                          {"action": "select_ban_user", "id": user[0]})))
            markup.add(back_button)
            self.bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)

    def select_ban_user(self, message: Message, id: int):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (id,))
            thread_id = db_cursor.fetchone()
            if thread_id is None:
                self.bot.send_message(self.group_id, _("User not found"))
                return
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("❌" + _("Unban"),
                                                  callback_data=json.dumps({"action": "unban_user", "id": id}))
                       )
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "ban_user"})))
            self.bot.edit_message_text(f"User ID: {id}", message.chat.id, message.message_id, reply_markup=markup)

    def default_msg_menu(self, message: Message):
        if not self.check_valid_chat(message):
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✏️" + _("Edit Message"),
                                              callback_data=json.dumps({"action": "edit_default_msg"})))
        markup.add(types.InlineKeyboardButton("🔄️" + _("Set to Default"),
                                              callback_data=json.dumps({"action": "empty_default_msg"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"})))
        self.bot.edit_message_text(_("Default Message") +
                                   "\n" +
                                   _("The default message is an auto-reply to the commands /help and /start"),
                                   message.chat.id, message.message_id, reply_markup=markup)

    def empty_default_msg(self, message: Message):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE settings SET value = NULL WHERE key = 'default_message'")
            db.commit()
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"}))
                   )
        self.bot.edit_message_text(_("Default message has been restored."), message.chat.id, message.message_id,
                                   reply_markup=markup)

    def edit_default_msg(self, message: Message):
        msg = self.bot.edit_message_text(text=_(
            "Let's set up the default message.\nSend /cancel to cancel this operation.\n\n"
            "Please send me the response."),
            chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.edit_default_msg_handle)

    def edit_default_msg_handle(self, message: Message):
        if not isinstance(message.text, str):
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        if message.text == "/cancel":
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE settings SET value = ? WHERE key = 'default_message'", (message.text,))
            db.commit()
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"}))
                   )
        self.bot.send_message(self.group_id, _("Default message has been updated."), reply_markup=markup)

    def callback_query(self, call: types.CallbackQuery):
        if call.data == "null":
            logger.error(_("Invalid callback data received"))
            return
        try:
            data = json.loads(call.data)
            action = data["action"]
        except json.JSONDecodeError:
            logger.error(_("Invalid JSON data received"))
            return

        # User end
        match action:
            case "verify_button":
                self.handle_button_captcha(call)
                return

        # Admin end
        if call.message.chat.id != self.group_id or call.message.message_thread_id is not None:
            return
        markup = types.InlineKeyboardMarkup()
        back_button = types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"}))
        match action:
            case "menu":
                self.menu(call.message, edit=True)
            case "auto_reply":
                markup.add(types.InlineKeyboardButton("➕" + _("Add Auto Reply"),
                                                      callback_data=json.dumps({"action": "start_add_auto_reply"}))
                           )
                markup.add(types.InlineKeyboardButton("⚙️" + _("Manage Existing Auto Reply"),
                                                      callback_data=json.dumps({"action": "manage_auto_reply"}))
                           )
                markup.add(back_button)
                self.bot.edit_message_text(_("Auto Reply"), call.message.chat.id, call.message.message_id,
                                           reply_markup=markup)
            case "set_auto_reply_type":
                if "regex" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.cache.set("auto_response_regex", data["regex"], 300)
                self.add_auto_response_value(call.message)
            case "start_add_auto_reply":
                self.add_auto_response(call.message)
            case "add_auto_reply":
                self.process_add_auto_reply(call.message, data)
            case "manage_auto_reply":
                self.manage_auto_reply(call.message, page=data.get("page", 1))
            case "select_auto_reply":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.select_auto_reply(call.message, data["id"])
            case "delete_auto_reply":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.delete_auto_reply(call.message, data["id"])
            case "ban_user":
                self.manage_ban_user(call.message)
            case "unban_user":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.unban_user(call.message, user_id=data["id"])
            case "select_ban_user":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.select_ban_user(call.message, data["id"])
            case "default_msg":
                self.default_msg_menu(call.message)
            case "edit_default_msg":
                self.edit_default_msg(call.message)
            case "empty_default_msg":
                self.empty_default_msg(call.message)
            case "captcha_settings":
                self.captcha_settings_menu(call.message)
            case "set_captcha":
                self.set_captcha(call.message, data["value"])
            case "broadcast_message":
                self.broadcast_message(call.message)
            case "confirm_broadcast":
                self.bot.delete_message(self.group_id, call.message.message_id)
                self.confirm_broadcast_message(call)
            case "cancel_broadcast":
                self.bot.delete_message(self.group_id, call.message.message_id)
                self.bot.send_message(self.group_id, _("Broadcast cancelled"))
                self.cache.delete("broadcast_content")
                self.cache.delete("broadcast_content_type")
            case _:
                logger.error(_("Invalid action received") + action)
        return

    def captcha_settings_menu(self, message: Message):
        captcha_list = {
            _("Disable Captcha"): "disable",
            _("Math Captcha"): "math",
            _("Button Captcha"): "button",
        }
        if not self.check_valid_chat(message):
            return
        markup = types.InlineKeyboardMarkup()
        for key, value in captcha_list.items():
            icon = "✅" + _("(Selected) ") if self.get_setting("captcha") == value else "⚪"
            markup.add(types.InlineKeyboardButton(icon + key,
                                                  callback_data=json.dumps({"action": "set_captcha", "value": value})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"})))
        self.bot.edit_message_text(_("Captcha Settings") + "\n", message.chat.id, message.message_id,
                                   reply_markup=markup)

    def set_captcha(self, message: Message, value: str):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE settings SET value = ? WHERE key = 'captcha'", (value,))
            db.commit()
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"})))
        self.cache.set("setting_captcha", value)
        self.bot.edit_message_text(_("Captcha settings updated"), message.chat.id, message.message_id,
                                   reply_markup=markup)

    def handle_edit(self, message: Message):
        if self.check_valid_chat(message):
            return
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = ? LIMIT 1",
                (message.message_id, message.chat.id == self.group_id,))
            if (result := db_cursor.fetchone()) is None:
                return
            topic_id, forwarded_id = result
            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (user_id := db_cursor.fetchone()[0]) is None:
                    return
                match message.content_type:
                    case "text":
                        self.bot.edit_message_text(chat_id=user_id, message_id=forwarded_id, text=message.text)
            else:
                match message.content_type:
                    case "text":
                        self.bot.edit_message_text(chat_id=self.group_id, message_id=forwarded_id,
                                                   text=message.text + "\n\n" + _("(edited)"))

    def delete_message(self, message: Message):
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
                (msg_id, message.chat.id == self.group_id,))
            if (result := db_cursor.fetchone()) is None:
                return
            topic_id, forwarded_id = result
            if message.chat.id == self.group_id:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                if (user_id := db_cursor.fetchone()[0]) is None:
                    return
                self.bot.delete_message(chat_id=user_id, message_id=forwarded_id)
            else:
                self.bot.delete_message(chat_id=self.group_id, message_id=forwarded_id)

            # Delete the message from the database
            db_cursor.execute("DELETE FROM messages WHERE received_id = ? AND in_group = ?",
                              (msg_id, message.chat.id == self.group_id))
            db.commit()

        # Delete the current message
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.reply_to_message.id)
        self.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    def broadcast_message(self, message: Message):
        if not self.check_valid_chat(message):
            return
        msg = self.bot.edit_message_text(text=_(
            "Please send the content you want to broadcast.\nSend /cancel to cancel this operation."),
            chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.handle_broadcast_message)

    def handle_broadcast_message(self, message: Message):
        if (isinstance(message.text, str) and message.text.startswith("/cancel")) or not self.check_valid_chat(message):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return

        content_type = message.content_type
        if content_type == "text":
            content = message.text
        elif content_type == "photo":
            content = message.photo[-1].file_id
        elif content_type == "document":
            content = message.document.file_id
        elif content_type == "video":
            content = message.video.file_id
        elif content_type == "sticker":
            content = message.sticker.file_id
        else:
            self.bot.send_message(self.group_id, _("Unsupported message type"))
            return

        # Store the message content and type in cache
        self.cache.set("broadcast_content", content, 300)
        self.cache.set("broadcast_content_type", content_type, 300)

        # Send preview message with confirmation button
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅" + _("Confirm"), callback_data=json.dumps({"action": "confirm_broadcast"})))
        markup.add(
            types.InlineKeyboardButton("❌" + _("Cancel"), callback_data=json.dumps({"action": "cancel_broadcast"})))

        if content_type == "text":
            self.bot.send_message(self.group_id, content, reply_markup=markup)
        elif content_type == "photo":
            self.bot.send_photo(self.group_id, content, reply_markup=markup)
        elif content_type == "document":
            self.bot.send_document(self.group_id, content, reply_markup=markup)
        elif content_type == "video":
            self.bot.send_video(self.group_id, content, reply_markup=markup)
        elif content_type == "sticker":
            self.bot.send_sticker(self.group_id, content, reply_markup=markup)

    def confirm_broadcast_message(self, call: types.CallbackQuery):
        content = self.cache.get("broadcast_content")
        content_type = self.cache.get("broadcast_content_type")

        if content is None or content_type is None:
            self.bot.send_message(self.group_id, _("The operation has timed out. Please initiate the process again."))
            return

        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT user_id, thread_id FROM topics")
            users = db_cursor.fetchall()
            for user in users:
                user_id, thread_id = user
                try:
                    if content_type == "text":
                        self.bot.send_message(user_id, content)
                    elif content_type == "photo":
                        self.bot.send_photo(user_id, content)
                    elif content_type == "document":
                        self.bot.send_document(user_id, content)
                    elif content_type == "video":
                        self.bot.send_video(user_id, content)
                    elif content_type == "sticker":
                        self.bot.send_sticker(user_id, content)
                except ApiTelegramException as e:
                    self.bot.send_message(self.group_id, _("Failed to send message to user {}").format(user_id))
                    logger.error(_("Failed to send message to user {}").format(user_id))

        self.bot.send_message(self.group_id, _("Broadcast message sent successfully."))
        self.cache.delete("broadcast_content")
        self.cache.delete("broadcast_content_type")

    def handle_verify(self, message: Message):
        if message.chat.id != self.group_id or message.message_thread_id is None:
            return

        command_parts = message.text.split()
        if len(command_parts) != 2 or command_parts[1].lower() not in ["true", "false"]:
            self.bot.send_message(message.chat.id, _("Invalid command format.\nUse /verify <true/false>"),
                                  message_thread_id=message.message_thread_id)
            return

        verified_status = command_parts[1].lower() == "true"
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ?", (message.message_thread_id,))
            user_id = db_cursor.fetchone()
            if user_id is None:
                self.bot.send_message(message.chat.id, _("User not found"), message_thread_id=message.message_thread_id)
                return
            user_id = user_id[0]
            if verified_status:
                db_cursor.execute("INSERT OR REPLACE INTO verified_users (user_id) VALUES (?)", (user_id,))
                self.bot.send_message(message.chat.id, _("User verified successfully."),
                                      message_thread_id=message.message_thread_id)
                self.cache.set("verified_users", user_id, 300)
            else:
                db_cursor.execute("DELETE FROM verified_users WHERE user_id = ?", (user_id,))
                self.bot.send_message(message.chat.id, _("User verification removed."),
                                      message_thread_id=message.message_thread_id)
                self.cache.delete("verified_users")

            db.commit()

    def handle_reaction(self, message: MessageReactionUpdated):
        if message.chat.id == self.group_id and message.chat.is_forum:
            return
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            in_group = not (message.chat.id == self.group_id)
            db_cursor.execute(
                "SELECT topic_id, received_id FROM messages WHERE forwarded_id = ? AND in_group = ? LIMIT 1",
                (message.message_id, in_group)
            )
            result = db_cursor.fetchone()
            if result is None:
                db_cursor.execute(
                    "SELECT topic_id, forwarded_id FROM messages WHERE received_id = ? AND in_group = ? LIMIT 1",
                    (message.message_id, not in_group)
                )
                result = db_cursor.fetchone()
            if result is None:
                return
            topic_id, forwarded_id = result
            if in_group:
                self.bot.set_message_reaction(chat_id=self.group_id, message_id=forwarded_id,
                                              reaction=[message.new_reaction[-1]] if message.new_reaction else [])
            else:
                db_cursor.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1", (topic_id,))
                user_id = db_cursor.fetchone()
                self.bot.set_message_reaction(chat_id=user_id, message_id=forwarded_id,
                                              reaction=[message.new_reaction[-1]] if message.new_reaction else [])


if __name__ == "__main__":
    if not args.token or not args.group_id:
        logger.error(_("Token or group ID is empty"))
        exit(1)
    try:
        bot = TGBot(args.token, args.group_id)
    except KeyboardInterrupt:
        logger.info(_("Exiting..."))
        exit(0)
