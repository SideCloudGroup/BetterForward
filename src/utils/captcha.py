"""Captcha functionality for BetterForward."""

import json
import random

from diskcache import Cache
from telebot import types

from src.config import _


class CaptchaManager:
    """Manages captcha generation and verification."""

    def __init__(self, bot, cache: Cache):
        self.bot = bot
        self.cache = cache

    def generate_captcha(self, user_id: int, captcha_type: str = "math"):
        """Generate a captcha for the user."""
        match captcha_type:
            case "math":
                num1 = random.randint(1, 10)
                num2 = random.randint(1, 10)
                answer = num1 + num2
                self.cache.set(f"captcha_{user_id}", answer, 300)
                return f"{num1} + {num2} = ?"
            case "button":
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(
                    "Click to verify",
                    callback_data=json.dumps({"action": "verify_button", "user_id": user_id})
                ))
                self.bot.send_message(user_id, _("Please click the button to verify."),
                                      reply_markup=markup)
                return None
            case _:
                raise ValueError(_("Invalid captcha setting"))

    def verify_captcha(self, user_id: int, answer: str) -> bool:
        """Verify a captcha answer."""
        captcha = self.cache.get(f"captcha_{user_id}")
        if captcha is None:
            return False
        return str(answer) == str(captcha)

    def is_user_verified(self, user_id: int, db) -> bool:
        """Check if a user is verified."""
        verified = self.cache.get(f"verified_{user_id}")
        if verified is None:
            cursor = db.cursor()
            result = cursor.execute("SELECT 1 FROM verified_users WHERE user_id = ? LIMIT 1",
                                    (user_id,))
            verified = result.fetchone() is not None
            self.cache.set(f"verified_{user_id}", verified, 1800)
        return verified

    def set_user_verified(self, user_id: int, db):
        """Mark a user as verified."""
        cursor = db.cursor()
        cursor.execute("INSERT OR REPLACE INTO verified_users (user_id) VALUES (?)", (user_id,))
        db.commit()
        self.cache.set(f"verified_{user_id}", True, 1800)

    def remove_user_verification(self, user_id: int, db):
        """Remove user verification status."""
        cursor = db.cursor()
        cursor.execute("DELETE FROM verified_users WHERE user_id = ?", (user_id,))
        db.commit()
        self.cache.delete(f"verified_{user_id}")
