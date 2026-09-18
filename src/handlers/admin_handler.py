"""Admin functionality handling module."""

import json
import re
import sqlite3
import httpx
from datetime import datetime

import pytz
from telebot import types
from telebot.apihelper import ApiTelegramException
from telebot.types import Message

from src.config import logger, _
from src.utils.auto_response import looks_like_regex
from src.utils.captcha import (
    CAPTCHA_METHODS,
    QA_MAX_ITEMS,
    method_configured,
    method_unready_reason,
    next_qa_id,
    parse_captcha_config,
    parse_qa_answers,
    save_captcha_config,
)
from src.version import VERSION
from src.utils.permissions import (
    DEFAULT_RESTRICTED_REPLY_MESSAGE,
    DISABLE,
    ENABLE,
    get_default_restricted_reply_message,
    list_permission_keys,
    permission_label,
    permission_menu_label,
)


class AdminHandler:
    """Handles administrative functions like settings, menus, and broadcasts."""

    def __init__(self, bot, group_id: int, db_path: str, cache, database, auto_response_manager,
                 spam_keyword_manager=None, bot_instance=None, permission_manager=None):
        self.bot = bot
        self.group_id = group_id
        self.db_path = db_path
        self.cache = cache
        self.database = database
        self.auto_response_manager = auto_response_manager
        self.spam_keyword_manager = spam_keyword_manager
        self.bot_instance = bot_instance
        self.permission_manager = permission_manager or getattr(bot_instance, "permission_manager", None)
        # Get timezone from cache
        tz_str = self.cache.get("setting_time_zone")
        self.time_zone = pytz.timezone(tz_str) if tz_str else pytz.UTC

    OPERATOR_CACHE_KEY = "admin_operator_id"

    def check_valid_chat(self, message: Message) -> bool:
        """Check if message is in valid chat context."""
        return message.chat.id == self.group_id and message.message_thread_id is None

    def set_operator(self, user_id: int):
        """Remember which admin started the current multi-step admin flow."""
        self.cache.set(self.OPERATOR_CACHE_KEY, int(user_id), 600)

    def _is_group_admin(self, user_id: int) -> bool:
        try:
            return self.bot.get_chat_member(self.group_id, user_id).status in ("administrator", "creator")
        except Exception:
            return False

    def _accept_admin_step(self, message: Message, next_handler) -> bool:
        """Accept next-step input only from the active admin operator."""
        if not self.check_valid_chat(message):
            return False
        operator_id = self.cache.get(self.OPERATOR_CACHE_KEY)
        if operator_id is not None and message.from_user.id != operator_id:
            self.bot.register_next_step_handler(message, next_handler)
            return False
        if not self._is_group_admin(message.from_user.id):
            self.bot.register_next_step_handler(message, next_handler)
            return False
        return True

    def update_time_zone(self):
        """Update the timezone from cache and propagate to auto_response_manager."""
        tz_str = self.cache.get("setting_time_zone")
        if tz_str:
            self.time_zone = pytz.timezone(tz_str)
            self.auto_response_manager.update_time_zone(self.time_zone)
        else:
            self.time_zone = pytz.UTC

    def menu(self, message: Message, edit: bool = False):
        """Display the main admin menu."""
        if not self.check_valid_chat(message):
            return
        # When opened from /help, require admin; callback path already authenticated.
        if not edit and not self._is_group_admin(message.from_user.id):
            return
        if not edit:
            self.set_operator(message.from_user.id)

        markup = types.InlineKeyboardMarkup()
        buttons = [
            types.InlineKeyboardButton("💬" + _("Auto Reply"),
                                       callback_data=json.dumps({"action": "auto_reply"})),
            types.InlineKeyboardButton("📙" + _("Default Message"),
                                       callback_data=json.dumps({"action": "default_msg"})),
            types.InlineKeyboardButton("⛔" + _("Banned Users"),
                                       callback_data=json.dumps({"action": "ban_user"})),
            types.InlineKeyboardButton("🚫" + _("Spam Keywords"),
                                       callback_data=json.dumps({"action": "spam_keywords"})),
            types.InlineKeyboardButton("🚷" + _("Blocked User Reply"),
                                       callback_data=json.dumps({"action": "blocked_reply_settings"})),
            types.InlineKeyboardButton("🔐" + _("Permission Settings"),
                                       callback_data=json.dumps({"action": "permission_settings"})),
            types.InlineKeyboardButton("🔒" + _("Captcha Settings"),
                                       callback_data=json.dumps({"action": "captcha_settings"})),
            types.InlineKeyboardButton("🌍" + _("Time Zone Settings"),
                                       callback_data=json.dumps({"action": "time_zone_settings"})),
            types.InlineKeyboardButton("📢" + _("Broadcast Message"),
                                       callback_data=json.dumps({"action": "broadcast_message"})),
            types.InlineKeyboardButton("📡" + _("Show Host IP Info"),
                                       callback_data=json.dumps({"action": "show_host_ip"})),
            types.InlineKeyboardButton("🏷️" + _("Show Version"),
                                       callback_data=json.dumps({"action": "show_version"}))
        ]

        for i in range(0, len(buttons), 2):
            markup.row(*buttons[i:i + 2])

        if edit:
            self.bot.edit_message_text(_("Menu"), message.chat.id, message.message_id,
                                       reply_markup=markup)
        else:
            self.bot.send_message(self.group_id, _("Menu"), reply_markup=markup,
                                  message_thread_id=None)

    # Permission Management
    def permission_settings_menu(self, message: Message, edit: bool = False):
        """Display permission settings submenu."""
        if not self.check_valid_chat(message):
            return

        if self.permission_manager is None:
            self._send_or_edit_permission_menu_message(
                message, _("Permission settings are not available"), None, edit)
            return

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "⚙️ " + _("Default Permission Settings"),
            callback_data=json.dumps({"action": "default_permissions"})
        ))
        markup.add(types.InlineKeyboardButton(
            "✏️ " + _("Permission Restriction Reply Message"),
            callback_data=json.dumps({"action": "permission_reply_settings"})
        ))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))

        text = _("Permission Settings") + "\n\n" + _("Configure global permission policy and restriction replies.")
        self._send_or_edit_permission_menu_message(message, text, markup, edit)

    def default_permissions_menu(self, message: Message, edit: bool = False):
        """Display global default permission settings."""
        if not self.check_valid_chat(message):
            return

        if self.permission_manager is None:
            self._send_or_edit_permission_menu_message(
                message, _("Permission settings are not available"), None, edit)
            return

        markup = types.InlineKeyboardMarkup()
        text = _("Default Permission Settings") + "\n\n"
        text += _("Current global default permissions:") + "\n"

        for permission_key in list_permission_keys():
            current_value = self.permission_manager.get_global_default_value(permission_key)
            is_enabled = current_value == ENABLE
            status_text = _("Enabled") if is_enabled else _("Disabled")
            text += f"{permission_menu_label(permission_key)}: {status_text}\n"
            button_prefix = "✅ " if is_enabled else "❌ "
            markup.add(types.InlineKeyboardButton(
                f"{button_prefix}{permission_menu_label(permission_key)}: {status_text}",
                callback_data=json.dumps({
                    "action": "toggle_permission_default",
                    "key": permission_key,
                })
            ))

        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "permission_settings"})))

        self._send_or_edit_permission_menu_message(message, text, markup, edit)

    def toggle_permission_default(self, message: Message, permission_key: str):
        """Toggle one global default permission setting."""
        if not self.check_valid_chat(message):
            return

        if self.permission_manager is None:
            self._send_or_edit_permission_menu_message(
                message, _("Permission settings are not available"), None, True)
            return

        current_value = self.permission_manager.get_global_default_value(permission_key)
        next_value = DISABLE if current_value == ENABLE else ENABLE
        self.permission_manager.set_global_default(permission_key, next_value)
        self.default_permissions_menu(message, edit=True)

    def permission_reply_settings_menu(self, message: Message, edit: bool = False):
        """Display permission restriction reply settings."""
        if not self.check_valid_chat(message):
            return

        if self.permission_manager is None:
            self._send_or_edit_permission_menu_message(
                message, _("Permission settings are not available"), None, edit)
            return

        current_enabled = self.permission_manager.get_restricted_reply_enabled_value()
        current_message = self.permission_manager.get_restricted_reply_message()

        markup = types.InlineKeyboardMarkup()
        if current_enabled == ENABLE:
            markup.add(types.InlineKeyboardButton(
                "🔕 " + _("Disable Reply"),
                callback_data=json.dumps({"action": "set_permission_reply_enabled", "value": DISABLE})
            ))
        else:
            markup.add(types.InlineKeyboardButton(
                "🔔 " + _("Enable Reply"),
                callback_data=json.dumps({"action": "set_permission_reply_enabled", "value": ENABLE})
            ))

        markup.add(types.InlineKeyboardButton(
            "✏️ " + _("Edit Reply Message"),
            callback_data=json.dumps({"action": "edit_permission_reply_message"})
        ))
        markup.add(types.InlineKeyboardButton(
            "🔄 " + _("Reset Reply Message"),
            callback_data=json.dumps({"action": "reset_permission_reply_message"})
        ))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "permission_settings"})))

        status_text = _("Enabled") if current_enabled == ENABLE else _("Disabled")
        example_label = permission_label("photo")
        text = _("Permission Restriction Reply Message") + "\n\n"
        text += _("Status: {}").format(status_text) + "\n"
        text += _("Current message: {}").format(current_message) + "\n\n"
        text += _("{permission} is used to render the permission variable, for example \"{}\".").replace(
            "{}", example_label)

        self._send_or_edit_permission_menu_message(message, text, markup, edit)

    def set_permission_reply_enabled(self, message: Message, value: str):
        """Toggle permission restriction replies."""
        if value not in (ENABLE, DISABLE):
            self.bot.send_message(self.group_id, _("Invalid action"))
            return
        if self.permission_manager is None:
            self._send_or_edit_permission_menu_message(
                message, _("Permission settings are not available"), None, True)
            return

        self.permission_manager.set_restricted_reply_enabled(value)
        self.permission_reply_settings_menu(message, edit=True)

    def edit_permission_reply_message(self, message: Message):
        """Start editing permission restriction reply message."""
        example_label = permission_label("photo")
        msg = self.bot.edit_message_text(
            text=_("Please send the message to reply when a permission blocks a user message.\n"
                   "Send /cancel to cancel this operation.\n\n"
                   "{permission} is used to render the permission variable, for example \"{}\".").replace(
                "{}", example_label),
            chat_id=self.group_id,
            message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.process_permission_reply_message)

    def process_permission_reply_message(self, message: Message):
        """Process permission restriction reply message editing."""
        if not self._accept_admin_step(message, self.process_permission_reply_message):
            return
        if not self.check_valid_chat(message):
            logger.warning(
                f"Permission reply edit from wrong context: chat_id={message.chat.id}, thread_id={message.message_thread_id}")
            return

        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return

        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return

        reply_message = message.text.strip()
        self.permission_manager.set_restricted_reply_message(reply_message)

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "permission_reply_settings"})))
        self.bot.send_message(self.group_id,
                              _("Permission restriction reply message updated: {}").format(reply_message),
                              reply_markup=markup)

    def reset_permission_reply_message(self, message: Message):
        """Reset permission restriction reply message to the default template."""
        if self.permission_manager is None:
            self._send_or_edit_permission_menu_message(
                message, _("Permission settings are not available"), None, True)
            return

        self.permission_manager.set_restricted_reply_message(DEFAULT_RESTRICTED_REPLY_MESSAGE)
        self.permission_reply_settings_menu(message, edit=True)

    def _send_or_edit_permission_menu_message(self, message: Message, text: str, markup, edit: bool):
        if edit:
            self.bot.edit_message_text(text, message.chat.id, message.message_id,
                                       reply_markup=markup)
        else:
            self.bot.send_message(text=text,
                                  chat_id=message.chat.id,
                                  message_thread_id=None,
                                  reply_markup=markup)

    # Auto Reply Management
    def auto_reply_menu(self, message: Message):
        """Display auto reply management menu."""
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("➕" + _("Add Auto Reply"),
                                              callback_data=json.dumps({"action": "start_add_auto_reply"})))
        markup.add(types.InlineKeyboardButton("⚙️" + _("Manage Existing Auto Reply"),
                                              callback_data=json.dumps({"action": "manage_auto_reply"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))
        self.bot.send_message(text=_("Auto Reply"),
                              chat_id=message.chat.id,
                              message_thread_id=None,
                              reply_markup=markup)

    def add_auto_response(self, message: Message):
        """Start the process of adding an auto response."""
        if not self.check_valid_chat(message):
            return
        msg = self.bot.edit_message_text(
            text=_("Let's set up an automatic response.\nSend /cancel to cancel this operation.\n\n"
                   "Please send the keywords or regular expression that should trigger this response."),
            chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.add_auto_response_type)

    def add_auto_response_type(self, message: Message):
        """Process the trigger pattern for auto response."""
        if not self._accept_admin_step(message, self.add_auto_response_type):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return

        self.cache.set("auto_response_key", message.text, 300)
        # Plain keywords stay exact matches; only metacharacter patterns become regex.
        self.cache.set("auto_response_regex", looks_like_regex(message.text), 300)
        self.add_auto_response_value(message)

    def add_auto_response_value(self, message: Message):
        """Get the response content for auto response."""
        if not self.check_valid_chat(message):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return

        if self.cache.get("auto_response_regex") is True:
            try:
                re.compile(self.cache.get("auto_response_key"))
            except re.error:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "auto_reply"})))
                self.bot.send_message(
                    text=_("Invalid regular expression"),
                    chat_id=self.group_id,
                    message_thread_id=None,
                    reply_markup=markup)
                return

        msg = self.bot.send_message(
            text=_("Please send the response content. It can be text, stickers, photos and so on."),
            chat_id=self.group_id,
            message_thread_id=None)
        self.bot.register_next_step_handler(msg, self.add_auto_response_time)

    def add_auto_response_time(self, message: Message):
        """Get time restrictions for auto response."""
        if not self._accept_admin_step(message, self.add_auto_response_time):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if self.cache.get("auto_response_key") is None:
            self.bot.send_message(self.group_id,
                                  _("The operation has timed out. Please initiate the process again."))
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
        markup.add(types.InlineKeyboardButton("✅" + _("Yes"),
                                              callback_data=json.dumps({"action": "set_auto_response_time",
                                                                        "value": "yes"})))
        markup.add(types.InlineKeyboardButton("❌" + _("No"),
                                              callback_data=json.dumps({"action": "set_auto_response_time",
                                                                        "value": "no"})))
        self.bot.send_message(self.group_id,
                              _("Do you want to set a start and end time for this auto response?"),
                              reply_markup=markup)

    def handle_auto_response_time_callback(self, message: Message, data: dict):
        """Handle callback for auto response time setting."""
        self.bot.delete_message(self.group_id, message.message_id)
        if data["value"] == "no":
            self.cache.set("auto_response_start_time", None, 300)
            self.cache.set("auto_response_end_time", None, 300)
            self.process_add_auto_reply(message)
        elif data["value"] == "yes":
            msg = self.bot.send_message(self.group_id,
                                        _("Please enter the start time in HH:MM format (24-hour clock):"))
            self.bot.register_next_step_handler(msg, self.set_auto_response_start_time)

    def set_auto_response_start_time(self, message: Message):
        """Set the start time for auto response."""
        if not self._accept_admin_step(message, self.set_auto_response_start_time):
            return
        try:
            start_time = datetime.strptime(message.text, "%H:%M").time()
            self.cache.set("auto_response_start_time", start_time, 300)
            msg = self.bot.send_message(self.group_id,
                                        _("Please enter the end time in HH:MM format (24-hour clock):"))
            self.bot.register_next_step_handler(msg, self.set_auto_response_end_time)
        except ValueError:
            msg = self.bot.send_message(self.group_id,
                                        _("Invalid time format.") + "\n" +
                                        _("Please enter the start time in HH:MM format (24-hour clock):"))
            self.bot.register_next_step_handler(msg, self.set_auto_response_start_time)

    def set_auto_response_end_time(self, message: Message):
        """Set the end time for auto response."""
        if not self._accept_admin_step(message, self.set_auto_response_end_time):
            return
        try:
            end_time = datetime.strptime(message.text, "%H:%M").time()
            self.cache.set("auto_response_end_time", end_time, 300)
            self.process_add_auto_reply(message)
        except ValueError:
            msg = self.bot.send_message(self.group_id,
                                        _("Invalid time format.") + "\n" +
                                        _("Please enter the end time in HH:MM format (24-hour clock):"))
            self.bot.register_next_step_handler(msg, self.set_auto_response_end_time)

    def process_add_auto_reply(self, message: Message):
        """Process and save the auto reply."""
        key = self.cache.pop("auto_response_key")
        value = self.cache.pop("auto_response_value")
        is_regex = self.cache.pop("auto_response_regex")
        response_type = self.cache.pop("auto_response_type")
        start_time = self.cache.pop("auto_response_start_time")
        end_time = self.cache.pop("auto_response_end_time")

        if start_time is not None and end_time is not None:
            start_time = start_time.strftime("%H:%M")
            end_time = end_time.strftime("%H:%M")

        markup = types.InlineKeyboardMarkup()
        back_button = types.InlineKeyboardButton("⬅️" + _("Back"),
                                                 callback_data=json.dumps({"action": "menu"}))
        if None in [key, value, is_regex, response_type]:
            self.bot.delete_message(self.group_id, message.message_id)
            self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
            return

        self.auto_response_manager.add_auto_response(key, value, is_regex, response_type,
                                                     start_time, end_time)
        markup.add(back_button)
        self.bot.send_message(text=_("Auto reply added"),
                              chat_id=message.chat.id,
                              message_thread_id=None,
                              reply_markup=markup)

    def manage_auto_reply(self, message: Message, page: int = 1, page_size: int = 5):
        """Display paginated list of auto replies."""
        result = self.auto_response_manager.get_auto_responses_paginated(page, page_size)

        markup = types.InlineKeyboardMarkup()
        back_button = types.InlineKeyboardButton("⬅️" + _("Back"),
                                                 callback_data=json.dumps({"action": "auto_reply"}))

        text = _("Auto Reply List:") + "\n"
        text += _("Total: {}").format(result["total"]) + "\n"
        text += _("Page: {}").format(page) + "/" + str(result["total_pages"]) + "\n\n"

        id_buttons = []
        for auto_response in result["responses"]:
            text += "-" * 20 + "\n"
            text += f"ID: {auto_response['id']}\n"
            text += _("Trigger: {}").format(auto_response['key']) + "\n"
            text += _("Response: {}").format(
                auto_response['value'] if auto_response['type'] == "text" else auto_response['type']) + "\n"
            text += _("Is regex: {}").format("✅" if auto_response['is_regex'] else "❌") + "\n"
            text += _("Active time: ")
            if auto_response['start_time'] and auto_response['end_time']:
                text += "{}~{}".format(auto_response['start_time'], auto_response['end_time']) + "\n\n"
            else:
                text += _("Disabled") + "\n\n"
            id_buttons.append(types.InlineKeyboardButton(
                text=f"#{auto_response['id']}",
                callback_data=json.dumps({"action": "select_auto_reply", "id": auto_response['id']})))

        if id_buttons:
            markup.row(*id_buttons)

        # Add pagination buttons
        if 1 < page < result["total_pages"]:
            markup.row(
                types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                           callback_data=json.dumps({"action": "manage_auto_reply",
                                                                     "page": page - 1})),
                types.InlineKeyboardButton("➡️" + _("Next Page"),
                                           callback_data=json.dumps({"action": "manage_auto_reply",
                                                                     "page": page + 1})))
        elif page > 1:
            markup.add(types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                                  callback_data=json.dumps({"action": "manage_auto_reply",
                                                                            "page": page - 1})))
        elif page < result["total_pages"]:
            markup.add(types.InlineKeyboardButton("➡️" + _("Next Page"),
                                                  callback_data=json.dumps({"action": "manage_auto_reply",
                                                                            "page": page + 1})))

        markup.add(back_button)
        self.bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)

    def select_auto_reply(self, message: Message, response_id: int):
        """Display details of a specific auto reply."""
        auto_response = self.auto_response_manager.get_auto_response(response_id)
        if auto_response is None:
            self.bot.send_message(self.group_id, _("Auto reply not found"))
            return

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌" + _("Delete"),
                                              callback_data=json.dumps({"action": "delete_auto_reply",
                                                                        "id": response_id})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "manage_auto_reply"})))

        text = _("Trigger: {}").format(auto_response["key"]) + "\n"
        text += _("Response: {}").format(
            auto_response["value"] if auto_response["type"] == "text" else auto_response["type"]) + "\n"
        text += _("Is regex: {}").format("✅" if auto_response["is_regex"] else "❌") + "\n"
        text += _("Active time: ")
        if auto_response['start_time'] and auto_response['end_time']:
            text += "{}~{}".format(auto_response['start_time'], auto_response['end_time']) + "\n\n"
        else:
            text += _("Disabled") + "\n\n"

        self.bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)

    def delete_auto_reply(self, message: Message, response_id: int):
        """Delete an auto reply."""
        self.auto_response_manager.delete_auto_response(response_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "manage_auto_reply"})))
        self.bot.edit_message_text(_("Auto reply deleted"), chat_id=message.chat.id,
                                   message_id=message.message_id, reply_markup=markup)

    # Ban User Management
    def manage_ban_user(self, message: Message, page: int = 1, page_size: int = 10):
        """Display list of banned users with pagination."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()

            # Get total count
            db_cursor.execute("SELECT COUNT(*) FROM blocked_users")
            total = db_cursor.fetchone()[0]
            total_pages = (total + page_size - 1) // page_size if total > 0 else 1
            page = max(1, min(page, total_pages))

            # Query from blocked_users table with pagination
            offset = (page - 1) * page_size
            db_cursor.execute("""
                              SELECT user_id, username, first_name, last_name, blocked_at
                              FROM blocked_users
                              ORDER BY blocked_at DESC LIMIT ?
                              OFFSET ?
                              """, (page_size, offset))
            banned_users = db_cursor.fetchall()

            markup = types.InlineKeyboardMarkup()
            back_button = types.InlineKeyboardButton("⬅️" + _("Back"),
                                                     callback_data=json.dumps({"action": "menu"}))

            text = _("Banned User List:") + "\n"
            text += _("Total: {}").format(total) + "\n"
            text += _("Page: {}").format(page) + "/" + str(total_pages) + "\n\n"

            if not banned_users:
                text += _("No banned users") + "\n"
            else:
                for user in banned_users:
                    user_id, username, first_name, last_name, blocked_at = user
                    text += "-" * 20 + "\n"
                    text += f"User ID: {user_id}\n"

                    # Display name info if available
                    if first_name or last_name:
                        full_name = f"{first_name or ''} {last_name or ''}".strip()
                        text += f"{_('Name')}: {full_name}\n"
                    if username:
                        text += f"{_('Username')}: @{username}\n"

                    text += f"{_('Blocked at')}: {blocked_at}\n"

                    markup.add(types.InlineKeyboardButton(
                        text=f"ID: {user_id}",
                        callback_data=json.dumps({"action": "select_ban_user", "id": user_id})))

            # Add pagination buttons
            if 1 < page < total_pages:
                markup.row(
                    types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                               callback_data=json.dumps({"action": "ban_user", "page": page - 1})),
                    types.InlineKeyboardButton("➡️" + _("Next Page"),
                                               callback_data=json.dumps({"action": "ban_user", "page": page + 1})))
            elif page > 1:
                markup.add(types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                                      callback_data=json.dumps(
                                                          {"action": "ban_user", "page": page - 1})))
            elif page < total_pages:
                markup.add(types.InlineKeyboardButton("➡️" + _("Next Page"),
                                                      callback_data=json.dumps(
                                                          {"action": "ban_user", "page": page + 1})))

            markup.add(back_button)
            self.bot.send_message(text=text,
                                  chat_id=message.chat.id,
                                  message_thread_id=None,
                                  reply_markup=markup)

    def select_ban_user(self, message: Message, user_id: int):
        """Display options for a banned user."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            # Get user info from blocked_users
            db_cursor.execute(
                "SELECT username, first_name, last_name, blocked_at FROM blocked_users WHERE user_id = ? LIMIT 1",
                (user_id,)
            )
            user_info = db_cursor.fetchone()

            if user_info is None:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "ban_user"})))
                self.bot.edit_message_text(_("User not found"), message.chat.id, message.message_id,
                                           reply_markup=markup)
                return

            username, first_name, last_name, blocked_at = user_info

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌" + _("Unban"),
                                              callback_data=json.dumps({"action": "unban_user",
                                                                        "id": user_id})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "ban_user"})))

        # Build user info text
        text = _("Blocked User Details") + "\n\n"
        text += f"User ID: {user_id}\n"
        if first_name or last_name:
            full_name = f"{first_name or ''} {last_name or ''}".strip()
            text += f"{_('Name')}: {full_name}\n"
        if username:
            text += f"{_('Username')}: @{username}\n"
        text += f"{_('Blocked at')}: {blocked_at}\n"

        self.bot.edit_message_text(text, message.chat.id, message.message_id,
                                   reply_markup=markup)

    # Default Message Settings
    def default_msg_menu(self, message: Message):
        """Display default message settings menu."""
        if not self.check_valid_chat(message):
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✏️" + _("Edit Message"),
                                              callback_data=json.dumps({"action": "edit_default_msg"})))
        markup.add(types.InlineKeyboardButton("🔄️" + _("Set to Default"),
                                              callback_data=json.dumps({"action": "empty_default_msg"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))
        self.bot.send_message(
            text=_("Default Message") + "\n" +
                 _("The default message is an auto-reply to the commands /help and /start"),
            chat_id=message.chat.id,
            message_thread_id=None,
            reply_markup=markup)

    def edit_default_msg(self, message: Message):
        """Start editing default message."""
        msg = self.bot.edit_message_text(
            text=_("Let's set up the default message.\nSend /cancel to cancel this operation.\n\n"
                   "Please send me the response."),
            chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.edit_default_msg_handle)

    def edit_default_msg_handle(self, message: Message):
        """Handle default message editing."""
        if not self._accept_admin_step(message, self.edit_default_msg_handle):
            return
        if not isinstance(message.text, str):
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return

        self.database.set_setting('default_message', message.text)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))
        self.bot.send_message(self.group_id, _("Default message has been updated."),
                              reply_markup=markup)

    def empty_default_msg(self, message: Message):
        """Reset default message to default."""
        self.database.set_setting('default_message', None)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))
        self.bot.edit_message_text(_("Default message has been restored."),
                                   message.chat.id, message.message_id, reply_markup=markup)

    # Captcha Settings
    def _captcha_method_labels(self):
        return {
            "math": _("Math Captcha"),
            "button": _("Button Captcha"),
            "emoji": _("Emoji Captcha"),
            "qa": _("Custom Q&A Captcha"),
            "sticker": _("Sticker Captcha"),
            "tguard": _("TGuard Captcha"),
        }

    def _load_captcha_config(self):
        raw = self.cache.get("setting_captcha")
        if raw is None:
            raw = self.database.get_setting("captcha")
        return parse_captcha_config(raw)

    def _save_captcha_config(self, config):
        return save_captcha_config(self.database, self.cache, config)

    def captcha_settings_menu(self, message: Message, edit: bool = False):
        """Display captcha settings hub."""
        if not self.check_valid_chat(message):
            return

        config = self._load_captcha_config()
        enabled = set(config.get("methods") or [])
        labels = self._captcha_method_labels()
        markup = types.InlineKeyboardMarkup()
        disable_icon = "✅" + _("(Selected) ") if not enabled else "⚪"
        markup.add(types.InlineKeyboardButton(
            disable_icon + _("Disable Captcha"),
            callback_data=json.dumps({"action": "toggle_captcha", "v": "disable"})))
        for method in CAPTCHA_METHODS:
            icon = "✅" + _("(Selected) ") if method in enabled else "⚪"
            markup.add(types.InlineKeyboardButton(
                icon + labels[method],
                callback_data=json.dumps({"action": "toggle_captcha", "v": method})))
        markup.add(types.InlineKeyboardButton(
            "✏️" + _("Configure Custom Q&A"),
            callback_data=json.dumps({"action": "captcha_qa_settings"})))
        markup.add(types.InlineKeyboardButton(
            "🎫" + _("Configure Sticker Captcha"),
            callback_data=json.dumps({"action": "captcha_sticker_settings"})))
        markup.add(types.InlineKeyboardButton(
            "🛡️" + _("TGuard API Settings"),
            callback_data=json.dumps({"action": "tguard_api_settings"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))

        if enabled:
            current = ", ".join(labels[method] for method in CAPTCHA_METHODS if method in enabled)
        else:
            current = _("Disabled")
        text = _("Captcha Settings") + "\n\n"
        text += _("Enabled methods are chosen at random when verifying a new user.") + "\n"
        text += _("Current: {}").format(current)
        self._send_or_edit_permission_menu_message(message, text, markup, edit)

    def toggle_captcha(self, message: Message, value: str):
        """Toggle a captcha method or disable all methods."""
        if not self.check_valid_chat(message):
            return
        config = self._load_captcha_config()
        methods = list(config.get("methods") or [])
        if value == "disable":
            config["methods"] = []
            self._save_captcha_config(config)
            self.captcha_settings_menu(message, edit=True)
            return
        if value not in CAPTCHA_METHODS:
            return
        if value in methods:
            methods = [method for method in methods if method != value]
        else:
            if not method_configured(config, value, self.cache):
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "captcha_settings"})))
                self.bot.edit_message_text(
                    method_unready_reason(value),
                    message.chat.id, message.message_id, reply_markup=markup)
                return
            methods.append(value)
        config["methods"] = [method for method in CAPTCHA_METHODS if method in set(methods)]
        self._save_captcha_config(config)
        self.captcha_settings_menu(message, edit=True)

    def captcha_qa_settings_menu(self, message: Message, edit: bool = True):
        """Display custom Q&A captcha questions."""
        if not self.check_valid_chat(message):
            return
        config = self._load_captcha_config()
        items = config.get("qa") or []
        markup = types.InlineKeyboardMarkup()
        text = _("Custom Q&A") + "\n\n"
        if not items:
            text += _("No custom questions yet.") + "\n"
        else:
            for item in items:
                answers = " | ".join(item.get("answers") or [])
                text += f"#{item['id']} {item['question']}\n"
                text += _("Answers: {}").format(answers) + "\n\n"
                label = item["question"]
                if len(label) > 24:
                    label = label[:24] + "…"
                markup.add(types.InlineKeyboardButton(
                    "🗑️" + _("Delete: {}").format(label),
                    callback_data=json.dumps({"action": "captcha_qa_del", "id": item["id"]})))
        if len(items) < QA_MAX_ITEMS:
            markup.add(types.InlineKeyboardButton(
                "➕" + _("Add Question"),
                callback_data=json.dumps({"action": "captcha_qa_add"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "captcha_settings"})))
        self._send_or_edit_permission_menu_message(message, text, markup, edit)

    def start_add_captcha_qa(self, message: Message):
        """Start adding a custom Q&A item."""
        if not self.check_valid_chat(message):
            return
        config = self._load_captcha_config()
        if len(config.get("qa") or []) >= QA_MAX_ITEMS:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "captcha_qa_settings"})))
            self.bot.edit_message_text(_("Maximum number of questions reached."),
                                       message.chat.id, message.message_id, reply_markup=markup)
            return
        msg = self.bot.edit_message_text(
            _("Please send the question.\nSend /cancel to cancel this operation."),
            message.chat.id, message.message_id)
        self.bot.register_next_step_handler(msg, self.process_captcha_qa_question)

    def process_captcha_qa_question(self, message: Message):
        """Store the custom question and ask for answers."""
        if not self._accept_admin_step(message, self.process_captcha_qa_question):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if message.content_type != "text" or not (message.text or "").strip():
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        self.cache.set("captcha_qa_question", message.text.strip(), 300)
        msg = self.bot.send_message(
            self.group_id,
            _("Please send the accepted answers. One per line, or separate with |.\n"
              "Send /cancel to cancel this operation."))
        self.bot.register_next_step_handler(msg, self.process_captcha_qa_answers)

    def process_captcha_qa_answers(self, message: Message):
        """Save a custom Q&A item."""
        if not self._accept_admin_step(message, self.process_captcha_qa_answers):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.cache.delete("captcha_qa_question")
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        question = self.cache.get("captcha_qa_question")
        if not question:
            self.bot.send_message(self.group_id,
                                  _("The operation has timed out. Please initiate the process again."))
            return
        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        answers = parse_qa_answers(message.text)
        if not answers:
            self.bot.send_message(self.group_id, _("Answers cannot be empty."))
            return
        config = self._load_captcha_config()
        if len(config.get("qa") or []) >= QA_MAX_ITEMS:
            self.cache.delete("captcha_qa_question")
            self.bot.send_message(self.group_id, _("Maximum number of questions reached."))
            return
        config.setdefault("qa", []).append({
            "id": next_qa_id(config),
            "question": question,
            "answers": answers,
        })
        self._save_captcha_config(config)
        self.cache.delete("captcha_qa_question")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "captcha_qa_settings"})))
        self.bot.send_message(self.group_id, _("Question added."), reply_markup=markup)

    def delete_captcha_qa(self, message: Message, item_id: int):
        """Delete a custom Q&A item."""
        if not self.check_valid_chat(message):
            return
        config = self._load_captcha_config()
        remaining = [item for item in config.get("qa") or [] if int(item.get("id")) != int(item_id)]
        config["qa"] = remaining
        if not remaining:
            config["methods"] = [method for method in config.get("methods") or [] if method != "qa"]
        self._save_captcha_config(config)
        self.captcha_qa_settings_menu(message, edit=True)

    def captcha_sticker_settings_menu(self, message: Message, edit: bool = True):
        """Display sticker captcha settings."""
        if not self.check_valid_chat(message):
            return
        config = self._load_captcha_config()
        sticker = config.get("sticker") or {}
        mode = sticker.get("mode") if sticker.get("mode") in ("any", "match") else "any"
        has_target = bool(sticker.get("file_id") and sticker.get("file_unique_id"))
        markup = types.InlineKeyboardMarkup()
        if mode == "match":
            markup.add(types.InlineKeyboardButton(
                "🔄" + _("Switch to any sticker"),
                callback_data=json.dumps({"action": "captcha_sticker_mode", "v": "any"})))
        else:
            markup.add(types.InlineKeyboardButton(
                "🎯" + _("Switch to match specific sticker"),
                callback_data=json.dumps({"action": "captcha_sticker_mode", "v": "match"})))
        markup.add(types.InlineKeyboardButton(
            "📌" + _("Set target sticker"),
            callback_data=json.dumps({"action": "captcha_sticker_set"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "captcha_settings"})))
        text = _("Sticker Captcha Settings") + "\n\n"
        text += (_("Mode: Match specific sticker") if mode == "match" else _("Mode: Any sticker")) + "\n"
        text += _("Target sticker: set") if has_target else _("Target sticker: not set")
        self._send_or_edit_permission_menu_message(message, text, markup, edit)

    def set_captcha_sticker_mode(self, message: Message, mode: str):
        """Switch sticker captcha mode."""
        if not self.check_valid_chat(message) or mode not in ("any", "match"):
            return
        config = self._load_captcha_config()
        sticker = config.setdefault("sticker", {"mode": "any", "file_id": None, "file_unique_id": None})
        sticker["mode"] = mode
        if mode == "match" and not (sticker.get("file_id") and sticker.get("file_unique_id")):
            config["methods"] = [method for method in config.get("methods") or [] if method != "sticker"]
        config["sticker"] = sticker
        self._save_captcha_config(config)
        self.captcha_sticker_settings_menu(message, edit=True)

    def start_set_captcha_sticker(self, message: Message):
        """Ask admin to send the target sticker."""
        if not self.check_valid_chat(message):
            return
        msg = self.bot.edit_message_text(
            _("Please send the sticker that users must send back.\n"
              "Send /cancel to cancel this operation."),
            message.chat.id, message.message_id)
        self.bot.register_next_step_handler(msg, self.process_captcha_sticker)

    def process_captcha_sticker(self, message: Message):
        """Save the target sticker for match mode."""
        if not self._accept_admin_step(message, self.process_captcha_sticker):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if message.content_type != "sticker" or message.sticker is None:
            self.bot.send_message(self.group_id, _("Please send a sticker."))
            return
        config = self._load_captcha_config()
        config["sticker"] = {
            "mode": (config.get("sticker") or {}).get("mode") or "any",
            "file_id": message.sticker.file_id,
            "file_unique_id": message.sticker.file_unique_id,
        }
        self._save_captcha_config(config)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "captcha_sticker_settings"})))
        self.bot.send_message(self.group_id, _("Target sticker updated."), reply_markup=markup)

    def set_captcha(self, message: Message, value: str):
        """Backward-compatible alias for toggling captcha methods."""
        self.toggle_captcha(message, value)

    # TGuard API Settings
    def tguard_api_settings_menu(self, message: Message, edit: bool = False):
        """Display TGuard API settings menu."""
        if not self.check_valid_chat(message):
            return

        current_url = self.database.get_setting('tguard_api_url') or _("Not set")
        current_key = self.database.get_setting('tguard_api_key') or _("Not set")

        if current_key != _("Not set") and len(current_key) > 8:
            masked_key = current_key[:4] + "*" * (len(current_key) - 8) + current_key[-4:]
        else:
            masked_key = current_key

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔗" + _("Set API URL"),
                                              callback_data=json.dumps({"action": "set_tguard_api_url"})))
        markup.add(types.InlineKeyboardButton("🔑" + _("Set API Key"),
                                              callback_data=json.dumps({"action": "set_tguard_api_key"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "captcha_settings"})))

        text = _("TGuard API Settings") + "\n\n"
        text += _("API URL: {}").format(current_url) + "\n"
        text += _("API Key: {}").format(masked_key) + "\n"
        self._send_or_edit_permission_menu_message(message, text, markup, edit)

    def set_tguard_api_url(self, message: Message):
        """Start setting TGuard API URL."""
        if not self.check_valid_chat(message):
            return
        msg = self.bot.edit_message_text(
            text=_("Please send the TGuard API URL (e.g., https://example.com).\n"
                   "Send /cancel to cancel this operation."),
            chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.process_tguard_api_url)

    def process_tguard_api_url(self, message: Message):
        """Process TGuard API URL setting."""
        if not self._accept_admin_step(message, self.process_tguard_api_url):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        
        api_url = message.text.strip()
        # Basic URL validation
        if not (api_url.startswith("http://") or api_url.startswith("https://")):
            self.bot.send_message(self.group_id, _("Invalid URL format. Please enter a valid URL starting with http:// or https://"))
            return
        
        self.database.set_setting('tguard_api_url', api_url)
        self.cache.set("setting_tguard_api_url", api_url)
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "tguard_api_settings"})))
        self.bot.send_message(self.group_id, _("TGuard API URL updated: {}").format(api_url),
                              reply_markup=markup)

    def set_tguard_api_key(self, message: Message):
        """Start setting TGuard API Key."""
        if not self.check_valid_chat(message):
            return
        msg = self.bot.edit_message_text(
            text=_("Please send the TGuard API Key.\n"
                   "Send /cancel to cancel this operation."),
            chat_id=self.group_id, message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.process_tguard_api_key)

    def process_tguard_api_key(self, message: Message):
        """Process TGuard API Key setting."""
        if not self._accept_admin_step(message, self.process_tguard_api_key):
            return
        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return
        
        api_key = message.text.strip()
        if not api_key:
            self.bot.send_message(self.group_id, _("API key cannot be empty"))
            return
        
        self.database.set_setting('tguard_api_key', api_key)
        self.cache.set("setting_tguard_api_key", api_key)
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "tguard_api_settings"})))
        self.bot.send_message(self.group_id, _("TGuard API Key updated successfully."),
                              reply_markup=markup)

    # Time Zone Settings
    def time_zone_settings_menu(self, message: Message):
        """Display time zone settings menu."""
        if not self.check_valid_chat(message):
            return
        current_time_zone = self.database.get_setting('time_zone')
        msg = self.bot.send_message(
            text=_("Current time zone: {}.\nPlease enter the new time zone (e.g., Europe/London):\n\n"
                   "Send /cancel to cancel this operation.").format(current_time_zone),
            chat_id=message.chat.id,
            message_thread_id=None)
        self.bot.register_next_step_handler(msg, self.validate_time_zone)

    def validate_time_zone(self, message: Message):
        """Validate and set time zone."""
        if not self._accept_admin_step(message, self.validate_time_zone):
            return
        time_zone = message.text
        if (not isinstance(message.text, str)) or message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return
        if time_zone in pytz.all_timezones:
            self.set_time_zone(message, time_zone)
        else:
            msg = self.bot.send_message(message.chat.id, _("Invalid time zone. Please try again:"))
            self.bot.register_next_step_handler(msg, self.validate_time_zone)

    def set_time_zone(self, message: Message, value: str):
        """Set the time zone."""
        self.database.set_setting('time_zone', value)
        self.cache.set("setting_time_zone", value)

        # Update timezone for all components
        self.time_zone = pytz.timezone(value)
        self.auto_response_manager.update_time_zone(self.time_zone)

        # Update bot instance timezone if available
        if self.bot_instance:
            self.bot_instance.update_self_time_zone()

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))
        self.bot.send_message(message.chat.id, _("Time zone updated to {}").format(value),
                              reply_markup=markup)

    # Broadcast Message
    def broadcast_message(self, message: Message):
        """Start broadcast message process."""
        if not self.check_valid_chat(message):
            return
        msg = self.bot.send_message(
            text=_("Please send the content you want to broadcast.\nSend /cancel to cancel this operation."),
            chat_id=self.group_id, message_thread_id=None)
        self.bot.register_next_step_handler(msg, self.handle_broadcast_message)

    def show_host_ip(self, message: Message):
        """Show host IP information."""
        if not self.check_valid_chat(message):
            return
        try:
            headers = {
                "User-Agent": "curl/8.4.0",
                "Accept": "*/*"
            }
            with httpx.Client(http2=True, headers=headers, verify=True) as client:
                res = client.get("https://1.1.1.1/cdn-cgi/trace", timeout=5)
            res.raise_for_status()
            data = {}
            for line in res.text.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)  # 只分割第一个=
                    data[key] = value
            ip = data.get('ip', _('Unknown'))
            country = data.get('loc', _('Unknown'))
            city = data.get('colo', _('Unknown'))
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to retrieve IP information: {e}")
            self.bot.send_message(self.group_id, _("Failed to retrieve IP information"))
            return
        except httpx.RequestError as e:
            logger.error(f"Failed to retrieve IP information: {e}")
            self.bot.send_message(self.group_id, _("Failed to retrieve IP information"))
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                callback_data=json.dumps({"action": "menu"})))
        self.bot.send_message(text=_("Host IP Information") + "\n\n" +
                                _("Version: {}").format(VERSION) + "\n" +
                                _("IP Address: {}").format(ip) + "\n" +
                                _("Country: {}").format(country) + "\n" +
                                _("City: {}").format(city),
                                chat_id=message.chat.id,
                                message_thread_id=None,
                                reply_markup=markup)

    def show_version(self, message: Message):
        """Show the embedded application version."""
        if not self.check_valid_chat(message):
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))
        self.bot.send_message(
            text=_("Version Information") + "\n\n" + _("Version: {}").format(VERSION),
            chat_id=message.chat.id,
            message_thread_id=None,
            reply_markup=markup,
        )

    def handle_broadcast_message(self, message: Message):
        """Handle broadcast message content."""
        if not self._accept_admin_step(message, self.handle_broadcast_message):
            return
        if (isinstance(message.text, str) and message.text.startswith("/cancel")) or \
                not self.check_valid_chat(message):
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
        markup.add(types.InlineKeyboardButton("✅" + _("Confirm"),
                                              callback_data=json.dumps({"action": "confirm_broadcast"})))
        markup.add(types.InlineKeyboardButton("❌" + _("Cancel"),
                                              callback_data=json.dumps({"action": "cancel_broadcast"})))

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

    def confirm_broadcast_message(self, call):
        """Confirm and send broadcast message."""
        content = self.cache.get("broadcast_content")
        content_type = self.cache.get("broadcast_content_type")

        if content is None or content_type is None:
            self.bot.send_message(self.group_id,
                                  _("The operation has timed out. Please initiate the process again."))
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
                    self.bot.send_message(self.group_id,
                                          _("Failed to send message to user {}").format(user_id))
                    logger.error(_("Failed to send message to user {}").format(user_id))

        self.bot.send_message(self.group_id, _("Broadcast message sent successfully."))
        self.cache.delete("broadcast_content")
        self.cache.delete("broadcast_content_type")

    def cancel_broadcast(self):
        """Cancel broadcast operation."""
        self.cache.delete("broadcast_content")
        self.cache.delete("broadcast_content_type")

    # Spam Keywords Management
    def spam_keywords_menu(self, message: Message):
        """Display spam keywords management menu."""
        if not self.spam_keyword_manager:
            self.bot.send_message(self.group_id, _("Spam keywords management is not available"))
            return

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("➕" + _("Add Keyword"),
                                              callback_data=json.dumps({"action": "add_spam_keyword"})))
        markup.add(types.InlineKeyboardButton("📋" + _("View Keywords"),
                                              callback_data=json.dumps({"action": "view_spam_keywords"})))
        markup.add(types.InlineKeyboardButton("🔄" + _("Reset Spam Topic"),
                                              callback_data=json.dumps({"action": "reset_spam_topic"})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))

        keyword_count = self.spam_keyword_manager.get_keyword_count()
        spam_topic_id = self.cache.get("spam_topic_id")

        text = _("Spam Keywords Management") + "\n\n"
        text += _("Total keywords: {}").format(keyword_count) + "\n"
        text += _("Spam Topic ID: {}").format(spam_topic_id if spam_topic_id else _("Not set")) + "\n\n"
        text += _("Messages containing these keywords will be forwarded to the spam topic silently.")

        self.bot.send_message(text=text,
                              chat_id=message.chat.id,
                              message_thread_id=None,
                              reply_markup=markup)

    def add_spam_keyword(self, message: Message):
        """Start the process of adding a spam keyword."""
        if not self.check_valid_chat(message):
            return

        msg = self.bot.edit_message_text(
            text=_("Please send the keyword you want to add to the spam filter.\n"
                   "Send /cancel to cancel this operation."),
            chat_id=self.group_id,
            message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.process_add_spam_keyword)

    def process_add_spam_keyword(self, message: Message):
        """Process adding a spam keyword."""
        if not self._accept_admin_step(message, self.process_add_spam_keyword):
            return
        # Must be in the correct group and main topic
        if not self.check_valid_chat(message):
            logger.warning(
                f"Keyword add attempt from wrong context: chat_id={message.chat.id}, thread_id={message.message_thread_id}")
            return

        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return

        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return

        keyword = message.text.strip()
        if not keyword:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "spam_keywords"})))
            self.bot.send_message(self.group_id, _("Keyword cannot be empty"), reply_markup=markup)
            return

        try:
            result = self.spam_keyword_manager.add_keyword(keyword)

            if result:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "spam_keywords"})))
                self.bot.send_message(self.group_id,
                                      _("Keyword added: {}").format(keyword),
                                      reply_markup=markup)
            else:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "spam_keywords"})))
                self.bot.send_message(self.group_id,
                                      _("Keyword already exists or is invalid"),
                                      reply_markup=markup)
        except Exception as e:
            logger.error(f"Error adding spam keyword: {e}")
            from traceback import print_exc
            print_exc()
            self.bot.send_message(self.group_id,
                                  _("Failed to add keyword: {}").format(str(e)))

    def view_spam_keywords(self, message: Message, page: int = 1, page_size: int = 10):
        """Display paginated list of spam keywords."""
        if not self.spam_keyword_manager:
            return

        keywords = self.spam_keyword_manager.get_all_keywords()
        total = len(keywords)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1

        # Ensure page is valid
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_keywords = keywords[start_idx:end_idx]

        # Store keywords in cache for callback access
        self.cache.set("spam_keywords_page", keywords, 300)

        markup = types.InlineKeyboardMarkup()
        back_button = types.InlineKeyboardButton("⬅️" + _("Back"),
                                                 callback_data=json.dumps({"action": "spam_keywords"}))

        text = _("Spam Keywords List:") + "\n"
        text += _("Total: {}").format(total) + "\n"
        text += _("Page: {}").format(page) + "/" + str(total_pages) + "\n\n"

        if not page_keywords:
            text += _("No keywords found") + "\n"
        else:
            keyword_buttons = []
            for idx, keyword in enumerate(page_keywords, start=start_idx):
                text += f"{idx + 1}. {keyword}\n"
                # Use index instead of keyword to avoid callback_data size limit
                keyword_buttons.append(types.InlineKeyboardButton(
                    text=f"#{idx + 1}",
                    callback_data=json.dumps({"action": "select_spam_keyword", "idx": idx})))

            # Add keyword selection buttons (max 5 per row)
            for i in range(0, len(keyword_buttons), 5):
                markup.row(*keyword_buttons[i:i + 5])

        # Add pagination buttons
        if 1 < page < total_pages:
            markup.row(
                types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                           callback_data=json.dumps({"action": "view_spam_keywords",
                                                                     "page": page - 1})),
                types.InlineKeyboardButton("➡️" + _("Next Page"),
                                           callback_data=json.dumps({"action": "view_spam_keywords",
                                                                     "page": page + 1})))
        elif page > 1:
            markup.add(types.InlineKeyboardButton("⬅️" + _("Previous Page"),
                                                  callback_data=json.dumps({"action": "view_spam_keywords",
                                                                            "page": page - 1})))
        elif page < total_pages:
            markup.add(types.InlineKeyboardButton("➡️" + _("Next Page"),
                                                  callback_data=json.dumps({"action": "view_spam_keywords",
                                                                            "page": page + 1})))

        markup.add(back_button)
        self.bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)

    def select_spam_keyword(self, message: Message, idx: int):
        """Display options for a specific spam keyword."""
        # Get keyword from cache
        keywords = self.cache.get("spam_keywords_page")
        if keywords is None or idx >= len(keywords):
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "view_spam_keywords"})))
            self.bot.edit_message_text(_("Keyword not found or expired"),
                                       message.chat.id, message.message_id,
                                       reply_markup=markup)
            return

        keyword = keywords[idx]

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌" + _("Delete"),
                                              callback_data=json.dumps({"action": "delete_spam_keyword",
                                                                        "idx": idx})))
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "view_spam_keywords"})))

        text = _("Keyword: {}").format(keyword) + "\n"
        text += _("Select an action:")

        self.bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)

    def delete_spam_keyword(self, message: Message, idx: int):
        """Delete a spam keyword."""
        # Get keyword from cache
        keywords = self.cache.get("spam_keywords_page")
        if keywords is None or idx >= len(keywords):
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "view_spam_keywords"})))
            self.bot.edit_message_text(_("Keyword not found or expired"),
                                       message.chat.id, message.message_id,
                                       reply_markup=markup)
            return

        keyword = keywords[idx]

        if self.spam_keyword_manager.remove_keyword(keyword):
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "view_spam_keywords"})))
            self.bot.edit_message_text(_("Keyword deleted: {}").format(keyword),
                                       chat_id=message.chat.id,
                                       message_id=message.message_id,
                                       reply_markup=markup)
        else:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "view_spam_keywords"})))
            self.bot.edit_message_text(_("Failed to delete keyword"),
                                       chat_id=message.chat.id,
                                       message_id=message.message_id,
                                       reply_markup=markup)

    # Blocked User Reply Settings
    def blocked_reply_settings_menu(self, message: Message):
        """Display blocked user auto-reply settings menu."""
        if not self.check_valid_chat(message):
            return

        current_enabled = self.database.get_setting('blocked_user_reply_enabled')
        current_message = self.database.get_setting('blocked_user_reply_message')

        markup = types.InlineKeyboardMarkup()

        # Enable/Disable toggle
        if current_enabled == 'enable':
            markup.add(types.InlineKeyboardButton(
                "🔕 " + _("Disable Auto Reply"),
                callback_data=json.dumps({"action": "set_blocked_reply_enabled", "value": "disable"})
            ))
        else:
            markup.add(types.InlineKeyboardButton(
                "🔔 " + _("Enable Auto Reply"),
                callback_data=json.dumps({"action": "set_blocked_reply_enabled", "value": "enable"})
            ))

        # Edit message button
        markup.add(types.InlineKeyboardButton(
            "✏️ " + _("Edit Reply Message"),
            callback_data=json.dumps({"action": "edit_blocked_reply_message"})
        ))

        # Clear message button
        markup.add(types.InlineKeyboardButton(
            "🗑️ " + _("Clear Reply Message"),
            callback_data=json.dumps({"action": "clear_blocked_reply_message"})
        ))

        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "menu"})))

        text = _("Blocked User Auto Reply Settings") + "\n\n"
        text += _("Status: {}").format(_("Enabled") if current_enabled == 'enable' else _("Disabled")) + "\n"
        text += _("Current message: {}").format(
            current_message if current_message else _("Not set (no reply will be sent)")
        ) + "\n\n"
        text += _("When enabled, blocked users will receive this message when they try to send messages.")

        self.bot.send_message(text=text,
                              chat_id=message.chat.id,
                              message_thread_id=None,
                              reply_markup=markup)

    def set_blocked_reply_enabled(self, message: Message, value: str):
        """Toggle blocked user auto-reply."""
        self.database.set_setting('blocked_user_reply_enabled', value)
        self.cache.set("setting_blocked_user_reply_enabled", value)

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "blocked_reply_settings"})))

        status_text = _("Enabled") if value == "enable" else _("Disabled")
        self.bot.edit_message_text(_("Blocked user auto-reply has been {}.").format(status_text),
                                   message.chat.id, message.message_id,
                                   reply_markup=markup)

    def edit_blocked_reply_message(self, message: Message):
        """Start editing blocked user reply message."""
        msg = self.bot.edit_message_text(
            text=_("Please send the message to reply to blocked users.\n"
                   "Send /cancel to cancel this operation.\n\n"
                   "Note: You can send an empty message to disable auto-reply."),
            chat_id=self.group_id,
            message_id=message.message_id)
        self.bot.register_next_step_handler(msg, self.process_edit_blocked_reply_message)

    def process_edit_blocked_reply_message(self, message: Message):
        """Process blocked user reply message editing."""
        if not self._accept_admin_step(message, self.process_edit_blocked_reply_message):
            return
        if not self.check_valid_chat(message):
            logger.warning(
                f"Blocked reply edit from wrong context: chat_id={message.chat.id}, thread_id={message.message_thread_id}")
            return

        if isinstance(message.text, str) and message.text.startswith("/cancel"):
            self.bot.send_message(self.group_id, _("Operation cancelled"))
            return

        if message.content_type != "text":
            self.bot.send_message(self.group_id, _("Invalid input"))
            return

        reply_message = message.text.strip()
        # Allow empty message to disable reply
        if not reply_message:
            reply_message = None

        self.database.set_setting('blocked_user_reply_message', reply_message)
        self.cache.set("setting_blocked_user_reply_message", reply_message)

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "blocked_reply_settings"})))

        if reply_message:
            self.bot.send_message(self.group_id,
                                  _("Blocked user reply message updated: {}").format(reply_message),
                                  reply_markup=markup)
        else:
            self.bot.send_message(self.group_id,
                                  _("Blocked user reply message cleared. No auto-reply will be sent."),
                                  reply_markup=markup)

    def clear_blocked_reply_message(self, message: Message):
        """Clear blocked user reply message."""
        self.database.set_setting('blocked_user_reply_message', None)
        self.cache.set("setting_blocked_user_reply_message", None)

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                              callback_data=json.dumps({"action": "blocked_reply_settings"})))
        self.bot.edit_message_text(_("Blocked user reply message cleared."),
                                   message.chat.id, message.message_id,
                                   reply_markup=markup)

    # Spam Topic Management
    def reset_spam_topic(self, message: Message):
        """Reset spam topic."""
        if not self.bot_instance:
            self.bot.send_message(self.group_id, _("Bot instance not available"))
            return

        # Confirm action
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "✅" + _("Confirm Reset"),
            callback_data=json.dumps({"action": "confirm_reset_spam_topic"})
        ))
        markup.add(types.InlineKeyboardButton(
            "❌" + _("Cancel"),
            callback_data=json.dumps({"action": "spam_keywords"})
        ))

        self.bot.edit_message_text(
            _("Are you sure you want to reset the spam topic?\n"
              "This will create a new spam topic. The old topic will not be deleted."),
            message.chat.id, message.message_id,
            reply_markup=markup)

    def confirm_reset_spam_topic(self, message: Message):
        """Confirm and execute spam topic reset."""
        if not self.bot_instance:
            self.bot.edit_message_text(_("Bot instance not available"),
                                       message.chat.id, message.message_id)
            return

        self.bot.edit_message_text(_("Resetting spam topic..."),
                                   message.chat.id, message.message_id)

        try:
            if self.bot_instance.reset_spam_topic():
                spam_topic_id = self.cache.get("spam_topic_id")
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "spam_keywords"})))
                self.bot.edit_message_text(
                    _("Spam topic reset successfully.\nNew Topic ID: {}").format(spam_topic_id),
                    message.chat.id, message.message_id,
                    reply_markup=markup)
            else:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                      callback_data=json.dumps({"action": "spam_keywords"})))
                self.bot.edit_message_text(_("Failed to reset spam topic"),
                                           message.chat.id, message.message_id,
                                           reply_markup=markup)
        except Exception as e:
            logger.error(f"Error resetting spam topic: {e}")
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("⬅️" + _("Back"),
                                                  callback_data=json.dumps({"action": "spam_keywords"})))
            self.bot.edit_message_text(_("Failed to reset spam topic: {}").format(str(e)),
                                       message.chat.id, message.message_id,
                                       reply_markup=markup)
