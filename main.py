import argparse
import gettext
import importlib
import json
import logging
import os
import queue
import re
import signal
import sqlite3
import threading
from traceback import print_exc

from telebot import types, TeleBot
from telebot.apihelper import create_forum_topic, close_forum_topic, ApiTelegramException, delete_forum_topic, \
    reopen_forum_topic
from telebot.types import Message

from cache import CacheHelper

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


def handle_sigterm(*args):
    raise KeyboardInterrupt()


signal.signal(signal.SIGTERM, handle_sigterm)


class TGBot:
    def __init__(self, bot_token: str, group_id: str, db_path: str = "./data/storage.db"):
        logger.info(_("Starting BetterForward..."))
        self.group_id = int(group_id)
        self.bot = TeleBot(bot_token)
        self.bot.message_handler(commands=["help"])(self.help)
        self.bot.message_handler(commands=["ban"])(self.ban_user)
        self.bot.message_handler(commands=["unban"])(self.unban_user)
        self.bot.message_handler(commands=["terminate"])(self.handle_terminate)
        self.bot.message_handler(func=lambda m: True, content_types=["photo", "text", "sticker", "video", "document"])(
            self.push_messages)
        self.bot.callback_query_handler(func=lambda call: True)(self.callback_query)
        self.db_path = db_path
        self.upgrade_db()
        self.bot.set_my_commands([
            types.BotCommand("help", _("Show help")),
            types.BotCommand("ban", _("Ban a user")),
            types.BotCommand("unban", _("Unban a user")),
            types.BotCommand("terminate", _("Terminate a thread")),
        ])
        self.cache = CacheHelper()
        self.check_permission()
        self.message_queue = queue.Queue()
        self.message_processor = threading.Thread(target=self.process_messages)
        self.message_processor.start()
        self.bot.infinity_polling(skip_pending=True, timeout=30)

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

    # Get thread_id to terminate when needed
    def terminate_thread(self, thread_id=None, user_id=None):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            if thread_id is not None:
                db_cursor.execute("DELETE FROM topics WHERE thread_id = ?", (thread_id,))
                db.commit()
            elif user_id is not None:
                result = db_cursor.execute("SELECT thread_id FROM topics WHERE user_id = ? LIMIT 1", (user_id,))
                if (thread_id := result.fetchone()[0]) is not None:
                    db_cursor.execute("DELETE FROM topics WHERE user_id = ?", (user_id,))
                    db.commit()
            try:
                delete_forum_topic(chat_id=self.group_id, message_thread_id=thread_id, token=self.bot.token)
            except ApiTelegramException:
                pass
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
                if re.match(row[0], text):
                    return {"response": row[1], "topic_action": row[2], "type": row[3]}
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
                    if auto_response_result["topic_action"]:
                        topic_action = True
                        auto_response = auto_response_result["response"]
                # Forward message to group
                userid = message.from_user.id
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
                                                        f"User ID: {userid}\n"
                                                        f"Full Name: {message.from_user.first_name}{last_name}\n"
                                                        f"Username: {username}\n",
                                                        message_thread_id=thread_id)
                    self.bot.pin_chat_message(self.group_id, pin_message.message_id)
                else:
                    thread_id = thread_id[0]
                try:
                    self.bot.forward_message(self.group_id, message.chat.id, message_thread_id=thread_id,
                                             message_id=message.message_id)
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
                result = curser.execute("SELECT user_id FROM topics WHERE thread_id = ? LIMIT 1",
                                        (message.message_thread_id,))
                user_id = result.fetchone()
                if user_id is not None:
                    match message.content_type:
                        case "photo":
                            self.bot.send_photo(chat_id=user_id[0], photo=message.photo[-1].file_id,
                                                caption=message.caption)
                        case "text":
                            self.bot.send_message(chat_id=user_id[0], text=message.text)
                        case "sticker":
                            self.bot.send_sticker(chat_id=user_id[0], sticker=message.sticker.file_id)
                        case "video":
                            self.bot.send_video(chat_id=user_id[0], video=message.video.file_id,
                                                caption=message.caption)
                        case "document":
                            self.bot.send_document(chat_id=user_id[0], document=message.document.file_id,
                                                   caption=message.caption)
                        case _:
                            logger.error(_("Unsupported message type") + message.content_type)
                else:
                    self.bot.send_message(self.group_id, _("Chat not found, please remove this topic manually"),
                                          message_thread_id=message.message_thread_id)
                    close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id,
                                      token=self.bot.token)

    # Process messages in the queue
    def process_messages(self):
        while True:
            try:
                self.handle_message(self.message_queue.get())
            except:
                logger.error(_("Failed to process message"))
                print_exc()

    def check_permission(self):
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
        if message.text == "/cancel":
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        self.cache.set("auto_response_key", message.text, 300)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅" + _("Yes"),
                                              callback_data=json.dumps(
                                                  {"action": "set_auto_reply_type", "regex": 1})))
        markup.add(types.InlineKeyboardButton("❌" + _("No"),
                                              callback_data=json.dumps(
                                                  {"action": "set_auto_reply_type", "regex": 0})))
        markup.add(
            types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "auto_reply"})))
        help_text = _("Trigger: {}").format(self.cache.get("auto_response_key")) + "\n\n"
        help_text += _("Is this a regular expression?")
        self.bot.send_message(text=help_text, chat_id=self.group_id, reply_markup=markup)

    def add_auto_response_value(self, message: Message):
        if not self.check_valid_chat(message):
            return
        if message.text == "/cancel":
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        msg = self.bot.edit_message_text(text=_("Please send the response content. It can be text, stickers, photos "
                                                "and so on."),
                                         chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.add_auto_response_topic_action)

    def add_auto_response_topic_action(self, message: Message):
        if not self.check_valid_chat(message):
            return
        if message.text == "/cancel":
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
                                                  {"action": "add_auto_reply", "topic_action": 1})))
        markup.add(types.InlineKeyboardButton("❌" + _("Do not forward message"),
                                              callback_data=json.dumps(
                                                  {"action": "add_auto_reply", "topic_action": 0})))
        markup.add(
            types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "add_auto_response"})))
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
        key = self.cache.pull("auto_response_key")
        value = self.cache.pull("auto_response_value")
        is_regex = self.cache.pull("auto_response_regex")
        type = self.cache.pull("auto_response_type")
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
        markup.add(types.InlineKeyboardButton("⛔" + _("Banned Users"),
                                              callback_data=json.dumps({"action": "ban_user"})))
        if edit:
            self.bot.edit_message_text(_("Menu"), message.chat.id, message.message_id, reply_markup=markup)
        else:
            self.bot.send_message(self.group_id, _("Menu"), reply_markup=markup, message_thread_id=None)

    def help(self, message: Message):
        if self.check_valid_chat(message):
            self.menu(message)
        else:
            self.bot.send_message(message.chat.id, _("This command is only available to admin users.") + "\n" +
                                  "Powered by [BetterForward](https://github.com/SideCloudGroup/BetterForward).",
                                  parse_mode="Markdown",
                                  disable_web_page_preview=True)

    def manage_auto_reply(self, message: Message):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            markup = types.InlineKeyboardMarkup()
            back_button = types.InlineKeyboardButton("⬅️" + _("Back"), callback_data=json.dumps({"action": "menu"}))
            db_cursor.execute("SELECT id, key, value, topic_action, is_regex, type FROM auto_response")
            auto_responses = db_cursor.fetchall()
            text = _("Auto Reply List:\n")
            for auto_response in auto_responses:
                text += "-" * 20 + "\n"
                text += f"ID: {auto_response[0]}\n"
                text += _("Trigger: {}").format(auto_response[1]) + "\n"
                text += _("Response: {}").format(
                    auto_response[2] if auto_response[5] == "text" else auto_response[5]) + "\n"
                text += _("Forward message: {}").format("✅" if auto_response[3] else "❌") + "\n"
                text += _("Is regex: {}").format("✅" if auto_response[4] else "❌") + "\n\n"
                markup.add(types.InlineKeyboardButton(text=auto_response[1],
                                                      callback_data=json.dumps({"action": "select_auto_reply",
                                                                                "id": auto_response[0]})))
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
            return
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("UPDATE topics SET ban = 1 WHERE thread_id = ?", (message.message_thread_id,))
            db.commit()
        self.bot.send_message(self.group_id, _("User banned"), message_thread_id=message.message_thread_id)
        close_forum_topic(chat_id=self.group_id, message_thread_id=message.message_thread_id, token=self.bot.token)

    def unban_user(self, message: Message, user_id: int = None):
        if message.chat.id != self.group_id:
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
                                                  callback_data=json.dumps({"action": "manage_ban_user"})))
            self.bot.edit_message_text(f"User ID: {id}", message.chat.id, message.message_id, reply_markup=markup)

    def callback_query(self, call: types.CallbackQuery):
        if call.data == "null":
            logger.error(_("Invalid callback data received"))
            return
        if call.message.chat.id != self.group_id or call.message.message_thread_id is not None:
            return
        try:
            data = json.loads(call.data)
            action = data["action"]
        except json.JSONDecodeError:
            logger.error(_("Invalid JSON data received"))
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
                self.manage_auto_reply(call.message)
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
            case _:
                logger.error(_("Invalid action received"))
        return


if __name__ == "__main__":
    if not args.token or not args.group_id:
        logger.error(_("Token or group ID is empty"))
        exit(1)
    try:
        bot = TGBot(args.token, args.group_id)
    except KeyboardInterrupt:
        logger.info(_("Exiting..."))
        exit(0)
