"""Captcha functionality for BetterForward."""

import json
import random
import sqlite3
import time

import httpx
from diskcache import Cache
from telebot import types

from src.config import _, logger

CAPTCHA_METHODS = ("math", "button", "emoji", "qa", "sticker", "tguard")
STICKER_MODES = ("any", "match")
QA_MAX_ITEMS = 10
PENDING_TTL = 300
PENDING_KEY_PREFIX = "captcha_pending_"
VERIFICATION_DAYS_SETTING = "captcha_verification_days"
EMOJI_POOL = (
    "🍎", "🍌", "🍇", "🍉", "🍊", "🍋", "🍓", "🍑",
    "🥝", "🍍", "🥥", "🍒", "🥕", "🌽", "🥑", "🍆",
)
LEGACY_CAPTCHA_VALUES = {
    "disable": [],
    "math": ["math"],
    "button": ["button"],
    "tguard": ["tguard"],
}


def default_captcha_config():
    return {
        "methods": [],
        "qa": [],
        "sticker": {
            "mode": "any",
            "file_id": None,
            "file_unique_id": None,
        },
    }


def _normalize_methods(methods):
    selected = {item for item in methods if item in CAPTCHA_METHODS}
    return [item for item in CAPTCHA_METHODS if item in selected]


def _normalize_qa(items):
    normalized = []
    if not isinstance(items, list):
        return normalized
    next_id = 1
    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        raw_answers = item.get("answers") or []
        if isinstance(raw_answers, str):
            raw_answers = [raw_answers]
        answers = []
        seen = set()
        for answer in raw_answers:
            text = str(answer).strip()
            if not text:
                continue
            key = normalize_answer(text)
            if key in seen:
                continue
            seen.add(key)
            answers.append(text)
        if not question or not answers:
            continue
        try:
            item_id = int(item.get("id") or next_id)
        except (TypeError, ValueError):
            item_id = next_id
        next_id = max(next_id, item_id + 1)
        normalized.append({
            "id": item_id,
            "question": question,
            "answers": answers,
        })
        if len(normalized) >= QA_MAX_ITEMS:
            break
    return normalized


def _normalize_sticker(sticker):
    data = {
        "mode": "any",
        "file_id": None,
        "file_unique_id": None,
    }
    if not isinstance(sticker, dict):
        return data
    mode = sticker.get("mode")
    data["mode"] = mode if mode in STICKER_MODES else "any"
    file_id = sticker.get("file_id")
    unique_id = sticker.get("file_unique_id")
    data["file_id"] = str(file_id) if file_id else None
    data["file_unique_id"] = str(unique_id) if unique_id else None
    return data


def parse_captcha_config(raw):
    config = default_captcha_config()
    if raw is None or raw == "":
        return config
    if isinstance(raw, dict):
        parsed = raw
    else:
        text = str(raw).strip()
        if text in LEGACY_CAPTCHA_VALUES:
            config["methods"] = list(LEGACY_CAPTCHA_VALUES[text])
            return config
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return config
    if isinstance(parsed, list):
        config["methods"] = _normalize_methods(parsed)
        return config
    if not isinstance(parsed, dict):
        return config
    config["methods"] = _normalize_methods(parsed.get("methods") or [])
    config["qa"] = _normalize_qa(parsed.get("qa") or [])
    config["sticker"] = _normalize_sticker(parsed.get("sticker") or {})
    return config


def dump_captcha_config(config):
    normalized = parse_captcha_config(config if isinstance(config, dict) else default_captcha_config())
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def save_captcha_config(database, cache, config):
    raw = dump_captcha_config(config)
    database.set_setting("captcha", raw)
    cache.set("setting_captcha", raw)
    return parse_captcha_config(raw)


def next_qa_id(config):
    items = config.get("qa") or []
    if not items:
        return 1
    return max(int(item["id"]) for item in items) + 1


def parse_qa_answers(text):
    answers = []
    seen = set()
    for line in (text or "").splitlines():
        for part in line.split("|"):
            value = part.strip()
            if not value:
                continue
            key = normalize_answer(value)
            if key in seen:
                continue
            seen.add(key)
            answers.append(value)
    return answers


def normalize_answer(value):
    return str(value).strip().casefold()


def verification_days(raw):
    """Read the validity setting; missing or invalid values keep permanent validity."""
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def tguard_configured(cache):
    return bool(cache.get("setting_tguard_api_url") and cache.get("setting_tguard_api_key"))


def method_configured(config, method, cache):
    if method == "qa":
        return bool(config.get("qa"))
    if method == "sticker":
        sticker = config.get("sticker") or {}
        if sticker.get("mode") != "match":
            return True
        return bool(sticker.get("file_id") and sticker.get("file_unique_id"))
    if method == "tguard":
        return tguard_configured(cache)
    return method in CAPTCHA_METHODS


def available_captcha_methods(config, cache):
    return [method for method in config.get("methods") or [] if method_configured(config, method, cache)]


def pick_captcha_type(config, cache):
    methods = available_captcha_methods(config, cache)
    if not methods:
        return None
    return random.choice(methods)


def method_unready_reason(method):
    if method == "qa":
        return _("Custom Q&A requires at least one question. Please add a question first.")
    if method == "sticker":
        return _("Sticker captcha requires a target sticker. Please set it first.")
    if method == "tguard":
        return _("TGuard Captcha requires API URL and API Key to be configured.\n"
                 "Please configure them in TGuard API Settings first.")
    return _("Invalid captcha setting")


class CaptchaManager:
    """Manages captcha generation and verification."""

    def __init__(self, bot, cache: Cache, group_id: int = None):
        self.bot = bot
        self.cache = cache
        self.group_id = group_id

    def get_config(self):
        return parse_captcha_config(self.cache.get("setting_captcha"))

    def pending_key(self, user_id: int):
        return f"{PENDING_KEY_PREFIX}{user_id}"

    def get_pending(self, user_id: int):
        pending = self.cache.get(self.pending_key(user_id))
        return pending if isinstance(pending, dict) and pending.get("type") else None

    def set_pending(self, user_id: int, captcha_type: str, answer=None, ttl: int = PENDING_TTL,
                    message_id=None):
        self.cache.set(self.pending_key(user_id), {
            "type": captcha_type, "answer": answer, "message_id": message_id,
        }, ttl)

    def is_current_callback(self, user_id: int, captcha_type: str, message_id: int) -> bool:
        """Only accept callbacks from the message for the active challenge."""
        pending = self.get_pending(user_id)
        return bool(pending and pending.get("type") == captcha_type
                    and message_id is not None and pending.get("message_id") == message_id)

    def clear_pending(self, user_id: int):
        self.cache.delete(self.pending_key(user_id))
        self.cache.delete(f"tguard_token_{user_id}")

    def generate_captcha(self, user_id: int, captcha_type: str = "math", config=None):
        """Generate a captcha for the user."""
        config = parse_captcha_config(config) if config is not None else self.get_config()
        match captcha_type:
            case "math":
                return self._generate_math_captcha(user_id)
            case "button":
                return self._generate_button_captcha(user_id)
            case "emoji":
                return self._generate_emoji_captcha(user_id)
            case "qa":
                return self._generate_qa_captcha(user_id, config)
            case "sticker":
                return self._generate_sticker_captcha(user_id, config)
            case "tguard":
                return self._generate_tguard_captcha(user_id)
            case _:
                raise ValueError(_("Invalid captcha setting"))

    def _generate_math_captcha(self, user_id: int):
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        answer = num1 + num2
        self.set_pending(user_id, "math", answer)
        question = f"{num1} + {num2} = ?"
        self.bot.send_message(
            user_id,
            _("Captcha is enabled. Please solve the following question and send the result directly\n") + question,
        )
        return question

    def _generate_button_captcha(self, user_id: int):
        self.set_pending(user_id, "button", None)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "Click to verify",
            callback_data=json.dumps({"action": "verify_button", "user_id": user_id})
        ))
        message = self.bot.send_message(user_id, _("Please click the button to verify."),
                                        reply_markup=markup)
        self.set_pending(user_id, "button", message_id=message.message_id)
        return None

    def _generate_emoji_captcha(self, user_id: int):
        target = random.choice(EMOJI_POOL)
        decoys = random.sample([item for item in EMOJI_POOL if item != target], 5)
        options = decoys + [target]
        random.shuffle(options)
        self.set_pending(user_id, "emoji", options.index(target))
        markup = types.InlineKeyboardMarkup()
        row = []
        for index, emoji in enumerate(options):
            row.append(types.InlineKeyboardButton(
                emoji,
                callback_data=json.dumps({"action": "verify_emoji", "u": user_id, "i": index},
                                         separators=(",", ":"))
            ))
            if len(row) == 3:
                markup.row(*row)
                row = []
        if row:
            markup.row(*row)
        message = self.bot.send_message(
            user_id,
            _("Please click the matching emoji: {}").format(target),
            reply_markup=markup,
        )
        self.set_pending(user_id, "emoji", options.index(target), message_id=message.message_id)
        return None

    def _generate_qa_captcha(self, user_id: int, config):
        questions = config.get("qa") or []
        if not questions:
            raise ValueError(_("Custom Q&A requires at least one question. Please add a question first."))
        item = random.choice(questions)
        self.set_pending(user_id, "qa", item.get("answers") or [])
        self.bot.send_message(
            user_id,
            _("Please solve this question and send the answer:\n{}").format(item["question"]),
        )
        return item["question"]

    def _generate_sticker_captcha(self, user_id: int, config):
        sticker = config.get("sticker") or {}
        mode = sticker.get("mode") if sticker.get("mode") in STICKER_MODES else "any"
        if mode == "match":
            file_id = sticker.get("file_id")
            unique_id = sticker.get("file_unique_id")
            if not file_id or not unique_id:
                raise ValueError(_("Sticker captcha requires a target sticker. Please set it first."))
            self.set_pending(user_id, "sticker", unique_id)
            self.bot.send_sticker(user_id, file_id)
            self.bot.send_message(user_id, _("Please send this sticker back to verify."))
            return None
        self.set_pending(user_id, "sticker", "any")
        self.bot.send_message(user_id, _("Please send any sticker to verify."))
        return None

    def _generate_tguard_captcha(self, user_id: int):
        """Generate TGuard verification request."""
        api_url = self.cache.get("setting_tguard_api_url")
        api_key = self.cache.get("setting_tguard_api_key")

        if not api_url or not api_key:
            error_msg = _("TGuard API URL or Key not configured")
            logger.error(error_msg)
            if self.group_id:
                try:
                    self.bot.send_message(
                        self.group_id,
                        f"⚠️ {error_msg}",
                        message_thread_id=None
                    )
                except Exception:
                    pass
            raise ValueError(_("TGuard API not configured"))

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    f"{api_url.rstrip('/')}/api/verification/create",
                    json={"user_id": user_id},
                    headers={"X-API-Key": api_key}
                )
                response.raise_for_status()
                data = response.json()

                token = data.get("token")
                verification_url = data.get("verification_url")

                if not token or not verification_url:
                    error_msg = _("Invalid response from TGuard API")
                    logger.error(error_msg)
                    if self.group_id:
                        try:
                            self.bot.send_message(
                                self.group_id,
                                f"⚠️ TGuard验证错误：{error_msg}",
                                message_thread_id=None
                            )
                        except Exception:
                            pass
                    raise ValueError(error_msg)

                self.set_pending(user_id, "tguard", None, ttl=600)
                self.cache.set(f"tguard_token_{user_id}", token, 600)

                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(
                    _("🔐 Complete Verification"),
                    web_app=types.WebAppInfo(url=verification_url)
                ))

                self.bot.send_message(
                    user_id,
                    _("Please complete the verification by clicking the button below.\n"
                      "After completing verification, send a message to check your verification status."),
                    reply_markup=markup
                )
                return None
        except httpx.HTTPStatusError as e:
            error_msg = _("TGuard API error: {}").format(e)
            logger.error(error_msg)
            if self.group_id:
                try:
                    self.bot.send_message(
                        self.group_id,
                        f"⚠️ TGuard API错误：{str(e)}\n用户ID：{user_id}",
                        message_thread_id=None
                    )
                except Exception:
                    pass
            raise ValueError(_("Failed to create verification request"))
        except Exception as e:
            error_msg = _("TGuard verification error: {}").format(e)
            logger.error(error_msg)
            if self.group_id:
                try:
                    self.bot.send_message(
                        self.group_id,
                        f"⚠️ TGuard验证系统错误：{str(e)}\n用户ID：{user_id}",
                        message_thread_id=None
                    )
                except Exception:
                    pass
            raise ValueError(_("Failed to create verification request"))

    def check_tguard_verification_status(self, user_id: int, db=None) -> bool:
        """
        Check TGuard verification status immediately.
        Called when user sends a message to check if verification is completed.
        Returns True if verification is completed, False otherwise.
        """
        token = self.cache.get(f"tguard_token_{user_id}")
        if not token:
            return False

        api_url = self.cache.get("setting_tguard_api_url")
        if not api_url:
            return False

        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(
                    f"{api_url.rstrip('/')}/api/v1/verification-status/{token}"
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("completed"):
                        if db is not None:
                            self.set_user_verified(user_id, db)
                        else:
                            with sqlite3.connect("./data/storage.db") as fallback_db:
                                self.set_user_verified(user_id, fallback_db)
                        try:
                            self.bot.send_message(user_id, _("✅ Verification successful! You can now send messages."))
                        except Exception:
                            pass
                        self.clear_pending(user_id)
                        logger.info(_("User {} completed TGuard verification").format(user_id))
                        return True
                elif response.status_code == 404:
                    logger.warning(_("TGuard verification token expired for user {}").format(user_id))
                    self.clear_pending(user_id)
                    return False
        except Exception as e:
            logger.error(_("Error checking TGuard verification status: {}").format(e))

        return False

    def verify_text_answer(self, user_id: int, answer: str) -> bool:
        pending = self.get_pending(user_id)
        if not pending or pending.get("type") not in ("math", "qa"):
            return False
        submitted = normalize_answer(answer or "")
        if not submitted:
            return False
        expected = pending.get("answer")
        if pending["type"] == "math":
            return submitted == normalize_answer(expected)
        answers = expected if isinstance(expected, list) else [expected]
        accepted = {normalize_answer(item) for item in answers if item is not None}
        return submitted in accepted

    def verify_sticker_answer(self, user_id: int, sticker) -> bool:
        pending = self.get_pending(user_id)
        if not pending or pending.get("type") != "sticker" or sticker is None:
            return False
        expected = pending.get("answer")
        if expected == "any":
            return True
        return getattr(sticker, "file_unique_id", None) == expected

    def verify_emoji_choice(self, user_id: int, index) -> bool:
        pending = self.get_pending(user_id)
        if not pending or pending.get("type") != "emoji":
            return False
        try:
            return int(index) == int(pending.get("answer"))
        except (TypeError, ValueError):
            return False

    def is_user_verified(self, user_id: int, db) -> bool:
        """Check the latest validity setting even when verification is cached."""
        verified = self.cache.get(f"verified_{user_id}")
        # Older releases cached a boolean, which contains no verification time.
        if not isinstance(verified, dict):
            row = db.execute("SELECT verified_at FROM verified_users WHERE user_id = ? LIMIT 1",
                             (user_id,)).fetchone()
            verified = {"verified_at": row[0]} if row else {}
            self.cache.set(f"verified_{user_id}", verified, 1800)
        if "verified_at" not in verified:
            return False
        days = verification_days(self.cache.get(f"setting_{VERIFICATION_DAYS_SETTING}"))
        return days == 0 or time.time() - verified["verified_at"] < days * 86400

    def set_user_verified(self, user_id: int, db):
        """Mark a user as verified."""
        verified_at = time.time()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO verified_users (user_id, verified_at) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET verified_at = excluded.verified_at",
            (user_id, verified_at),
        )
        db.commit()
        self.cache.set(f"verified_{user_id}", {"verified_at": verified_at}, 1800)
        self.clear_pending(user_id)

    def remove_user_verification(self, user_id: int, db):
        """Remove user verification status."""
        cursor = db.cursor()
        cursor.execute("DELETE FROM verified_users WHERE user_id = ?", (user_id,))
        db.commit()
        self.cache.delete(f"verified_{user_id}")
        self.clear_pending(user_id)
