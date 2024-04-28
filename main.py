import argparse
import gettext
import logging
import os
import sqlite3

import telebot
from telebot.apihelper import create_forum_topic, delete_forum_topic
from telebot.types import Message

parser = argparse.ArgumentParser(description="")
parser.add_argument("-token", type=str, required=True, help="Telegram bot token")
parser.add_argument("-group_id", type=str, required=True, help="Group ID")
parser.add_argument("-language", type=str, default="en_US", help="Language", choices=["en_US", "zh_CN"])
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


class TGBot:
    def __init__(self, bot_token: str, group_id: str, db_path: str = "./data/storage.db"):
        logger.info(_("Starting BetterForward..."))
        self.group_id = int(group_id)
        self.bot = telebot.TeleBot(bot_token)
        self.bot.message_handler(commands=["start"])(lambda m: True)  # Ignore start command
        self.bot.message_handler(commands=["terminate"])(self.handle_terminate)
        self.bot.message_handler(func=lambda m: True, content_types=["photo", "text", "sticker", "video", "document"])(
            self.handle_messages)
        self.db_path = db_path
        self.init_db()
        self.bot.set_my_commands([
            telebot.types.BotCommand("terminate", _("Terminate a thread")),
        ])
        self.check_permission()
        self.bot.infinity_polling(skip_pending=True, timeout=30)

    def init_db(self):
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("""
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    thread_id INTEGER
                )
            """)
            db_cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON topics(user_id)")
            db_cursor.execute("CREATE INDEX IF NOT EXISTS idx_thread_id ON topics(thread_id)")
            db.commit()

    def terminate_thread(self, thread_id):
        logger.info(_("Terminating thread") + str(thread_id))
        delete_forum_topic(chat_id=self.group_id, message_thread_id=thread_id, token=self.bot.token)
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("DELETE FROM topics WHERE thread_id = ?", (thread_id,))
            db.commit()

    def handle_terminate(self, message: Message):
        if message.chat.id == self.group_id:
            if message.message_thread_id is None:
                if len((msg_split := message.text.split(" "))) != 2:
                    self.bot.reply_to(message, "Invalid command\n"
                                               "Correct usage:```\n"
                                               "/terminate <user ID>```", parse_mode="Markdown")
                    return
                thread_id = int(msg_split[1])
            else:
                thread_id = message.message_thread_id
            if thread_id == 1:
                self.bot.reply_to(message, _("Cannot terminate main thread"))
                return
            try:
                self.terminate_thread(thread_id)
                if message.message_thread_id is None:
                    self.bot.reply_to(message, _("Thread terminated"))
            except Exception:
                self.bot.reply_to(message, _("Failed to terminate the thread"))

    def handle_messages(self, message: Message):
        # Not responding in General topic
        if message.message_thread_id == 1:
            return
        with sqlite3.connect(self.db_path) as db:
            curser = db.cursor()
            if message.chat.id != self.group_id:
                # Forward message to group
                userid = message.from_user.id
                result = curser.execute("SELECT thread_id FROM topics WHERE user_id = ?", (userid,))
                thread_id = result.fetchone()
                if thread_id is None:
                    # Create a new thread
                    try:
                        topic = create_forum_topic(chat_id=self.group_id, name=message.from_user.first_name,
                                                   token=self.bot.token)
                    except Exception as e:
                        print(e)
                        return
                    curser.execute("INSERT INTO topics (user_id, thread_id) VALUES (?, ?)",
                                   (userid, topic["message_thread_id"]))
                    thread_id = topic["message_thread_id"]
                    pin_message = self.bot.send_message(self.group_id,
                                                        f"User ID: {userid}\n"
                                                        f"Full Name: {message.from_user.first_name} {message.from_user.last_name}\n"
                                                        f"Username: @{message.from_user.username}\n",
                                                        message_thread_id=thread_id,
                                                        parse_mode="Markdown")
                    self.bot.pin_chat_message(self.group_id, pin_message.message_id)
                self.bot.forward_message(self.group_id, message.chat.id, message_thread_id=thread_id,
                                         message_id=message.message_id)
            else:
                # Forward message to user
                result = curser.execute("SELECT user_id FROM topics WHERE thread_id = ?", (message.message_thread_id,))
                user_id = result.fetchone()
                if user_id is not None:
                    match message.content_type:
                        case "photo":
                            self.bot.send_photo(chat_id=user_id[0], photo=message.photo[-1].file_id)
                        case "text":
                            self.bot.send_message(chat_id=user_id[0], text=message.text)
                        case "sticker":
                            self.bot.send_sticker(chat_id=user_id[0], sticker=message.sticker.file_id)
                        case "video":
                            self.bot.send_video(chat_id=user_id[0], video=message.video.file_id)
                        case "document":
                            self.bot.send_document(chat_id=user_id[0], document=message.document.file_id)
                else:
                    self.bot.send_message(self.group_id, _("Chat not found, please remove this topic manually"), message_thread_id=message.message_thread_id)

    def check_permission(self):
        chat_member = self.bot.get_chat_member(self.group_id, self.bot.get_me().id)
        if chat_member.can_manage_topics is False:
            self.bot.send_message(self.group_id, _("I don't have permission to manage threads"))
            return False
        else:
            self.bot.send_message(self.group_id, _("Bot started successfully"))
            return True


if __name__ == "__main__":
    if not args.token or not args.group_id:
        logger.error(_("Token or group ID is empty"))
        exit(1)
    bot = TGBot(args.token, args.group_id)
