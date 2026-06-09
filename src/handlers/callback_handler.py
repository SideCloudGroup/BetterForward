"""Callback query handling module."""

import json

from telebot import types

from src.config import logger, _


PERMISSION_ADMIN_ACTIONS = {
    "permission_settings",
    "default_permissions",
    "toggle_permission_default",
    "permission_reply_settings",
    "set_permission_reply_enabled",
    "edit_permission_reply_message",
    "reset_permission_reply_message",
}


class CallbackHandler:
    """Handles callback queries from inline keyboards."""

    def __init__(self, bot, group_id: int, admin_handler, command_handler, captcha_manager, spam_detector=None,
                 db_path: str = "./data/storage.db"):
        self.bot = bot
        self.group_id = group_id
        self.admin_handler = admin_handler
        self.command_handler = command_handler
        self.captcha_manager = captcha_manager
        self.spam_detector = spam_detector
        self.db_path = db_path

    def handle_callback_query(self, call: types.CallbackQuery):
        """Main callback query handler."""
        if call.data == "null":
            logger.error(_("Invalid callback data received"))
            return

        try:
            data = json.loads(call.data)
            action = data["action"]
        except json.JSONDecodeError:
            logger.error(_("Invalid JSON data received"))
            return

        self.bot.answer_callback_query(call.id)

        # User end callbacks
        if action == "verify_button":
            self._handle_verify_button(call, data)
            return

        # Admin end callbacks
        if call.message.chat.id != self.group_id:
            return

        self._handle_admin_callback(call, action, data)

    def _handle_verify_button(self, call: types.CallbackQuery, data: dict):
        """Handle button captcha verification."""
        user_id = data.get("user_id")
        if user_id:
            import sqlite3
            db_path = "./data/storage.db"
            with sqlite3.connect(db_path) as db:
                self.captcha_manager.set_user_verified(user_id, db)
            self.bot.answer_callback_query(call.id)
            self.bot.send_message(user_id, _("Verification successful, you can now send messages"))
            self.bot.delete_message(call.message.chat.id, call.message.message_id)
        else:
            self.bot.answer_callback_query(call.id)
            self.bot.send_message(call.message.chat.id, _("Invalid user ID"))

    def _handle_admin_callback(self, call: types.CallbackQuery, action: str, data: dict):
        """Handle admin callbacks."""
        markup = types.InlineKeyboardMarkup()
        back_button = types.InlineKeyboardButton("⬅️" + _("Back"),
                                                 callback_data=json.dumps({"action": "menu"}))

        if action in PERMISSION_ADMIN_ACTIONS and not self._is_group_admin(call.from_user.id):
            self.bot.answer_callback_query(
                call.id,
                _("This action is only available to admin users."),
                show_alert=True,
            )
            return

        match action:
            case "menu":
                self.admin_handler.menu(call.message, edit=True)
            case "permission_settings":
                self.admin_handler.permission_settings_menu(call.message, edit=True)
            case "default_permissions":
                self.admin_handler.default_permissions_menu(call.message, edit=True)
            case "toggle_permission_default":
                if "key" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.toggle_permission_default(call.message, data["key"])
            case "permission_reply_settings":
                self.admin_handler.permission_reply_settings_menu(call.message, edit=True)
            case "set_permission_reply_enabled":
                if data.get("value") not in ("enable", "disable"):
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.set_permission_reply_enabled(call.message, data["value"])
            case "edit_permission_reply_message":
                self.admin_handler.edit_permission_reply_message(call.message)
            case "reset_permission_reply_message":
                self.admin_handler.reset_permission_reply_message(call.message)
            case "auto_reply":
                self.admin_handler.auto_reply_menu(call.message)
            case "set_auto_response_time":
                self.admin_handler.handle_auto_response_time_callback(call.message, data)
            case "start_add_auto_reply":
                self.admin_handler.add_auto_response(call.message)
            case "add_auto_reply":
                self.admin_handler.process_add_auto_reply(call.message)
            case "manage_auto_reply":
                self.admin_handler.manage_auto_reply(call.message, page=data.get("page", 1))
            case "select_auto_reply":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.select_auto_reply(call.message, data["id"])
            case "delete_auto_reply":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.delete_auto_reply(call.message, data["id"])
            case "ban_user":
                self.admin_handler.manage_ban_user(call.message, page=data.get("page", 1))
            case "unban_user":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.command_handler.unban_user(call.message, user_id=data["id"])
            case "select_ban_user":
                if "id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.select_ban_user(call.message, data["id"])
            case "default_msg":
                self.admin_handler.default_msg_menu(call.message)
            case "edit_default_msg":
                self.admin_handler.edit_default_msg(call.message)
            case "empty_default_msg":
                self.admin_handler.empty_default_msg(call.message)
            case "captcha_settings":
                self.admin_handler.captcha_settings_menu(call.message)
            case "set_captcha":
                self.admin_handler.set_captcha(call.message, data["value"])
            case "tguard_api_settings":
                self.admin_handler.tguard_api_settings_menu(call.message)
            case "set_tguard_api_url":
                self.admin_handler.set_tguard_api_url(call.message)
            case "set_tguard_api_key":
                self.admin_handler.set_tguard_api_key(call.message)
            case "broadcast_message":
                self.admin_handler.broadcast_message(call.message)
            case "confirm_broadcast":
                self.bot.delete_message(self.group_id, call.message.message_id)
                self.admin_handler.confirm_broadcast_message(call)
            case "cancel_broadcast":
                self.bot.delete_message(self.group_id, call.message.message_id)
                self.bot.send_message(self.group_id, _("Broadcast cancelled"))
                self.admin_handler.cancel_broadcast()
            case "time_zone_settings":
                self.admin_handler.time_zone_settings_menu(call.message)
            case "confirm_terminate":
                try:
                    self.command_handler.terminate_thread(thread_id=data.get("thread_id"),
                                                          user_id=data.get("user_id"))
                except Exception:
                    logger.error(_("Failed to terminate the thread"))
                    self.bot.send_message(self.group_id, _("Failed to terminate the thread"))
            case "cancel_terminate":
                self.bot.edit_message_text(_("Operation cancelled"),
                                           call.message.chat.id, call.message.message_id)
            case "delete_banned_thread":
                if "thread_id" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.bot.delete_message(self.group_id, call.message.message_id)
                try:
                    self.command_handler.terminate_thread(thread_id=data["thread_id"])
                    self.bot.send_message(self.group_id, _("Thread deleted"))
                except Exception as e:
                    logger.error(_("Failed to delete thread: {}").format(str(e)))
                    self.bot.send_message(self.group_id, _("Failed to delete thread"))
            case "spam_keywords":
                self.admin_handler.spam_keywords_menu(call.message)
            case "add_spam_keyword":
                self.admin_handler.add_spam_keyword(call.message)
            case "view_spam_keywords":
                self.admin_handler.view_spam_keywords(call.message, page=data.get("page", 1))
            case "select_spam_keyword":
                if "idx" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.select_spam_keyword(call.message, data["idx"])
            case "delete_spam_keyword":
                if "idx" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.delete_spam_keyword(call.message, data["idx"])
            case "blocked_reply_settings":
                self.admin_handler.blocked_reply_settings_menu(call.message)
            case "set_blocked_reply_enabled":
                if "value" not in data:
                    self.bot.delete_message(self.group_id, call.message.message_id)
                    self.bot.send_message(self.group_id, _("Invalid action"), reply_markup=markup)
                    return
                self.admin_handler.set_blocked_reply_enabled(call.message, data["value"])
            case "edit_blocked_reply_message":
                self.admin_handler.edit_blocked_reply_message(call.message)
            case "clear_blocked_reply_message":
                self.admin_handler.clear_blocked_reply_message(call.message)
            case "reset_spam_topic":
                self.admin_handler.reset_spam_topic(call.message)
            case "confirm_reset_spam_topic":
                self.admin_handler.confirm_reset_spam_topic(call.message)
            case "show_host_ip":
                self.admin_handler.show_host_ip(call.message)
            case _:
                logger.error(_("Invalid action received") + action)

    def _is_group_admin(self, user_id: int) -> bool:
        """Return True when the callback user is an admin of the forwarding group."""
        try:
            member = self.bot.get_chat_member(self.group_id, user_id)
            return member.status in ("administrator", "creator")
        except Exception:
            return False
