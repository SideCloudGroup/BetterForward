"""Helper functions for BetterForward."""

import html
import re

from telebot.formatting import apply_html_entities

from src.config import _, logger


START_PAYLOAD_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def to_html(text: str | None, entities) -> str | None:
    """Render message text/caption with its entities as Telegram HTML."""
    if text is None:
        return None
    return apply_html_entities(text, entities, None) if entities else html.escape(text)


def escape_markdown(text: str) -> str:
    """Escape markdown special characters."""
    escape_chars = r'\*_`\[\]()'
    return re.sub(f'([{escape_chars}])', r'\\\1', text)


def parse_start_payload(text: str | None) -> str | None:
    """Extract a valid Telegram /start deep-link payload, or None."""
    if not text:
        return None
    parts = text.split(maxsplit=1)
    cmd = parts[0].split('@', 1)[0].lower()
    if cmd != '/start' or len(parts) < 2:
        return None
    payload = parts[1].strip()
    if not START_PAYLOAD_RE.fullmatch(payload):
        return None
    return payload


# Telegram language_code (IETF tag) -> flag. Codes without an obvious country get no flag.
LANGUAGE_FLAGS = {
    "en": "🇬🇧", "zh": "🇨🇳", "zh-hans": "🇨🇳", "ja": "🇯🇵", "ko": "🇰🇷", "ru": "🇷🇺", "uk": "🇺🇦",
    "be": "🇧🇾", "kk": "🇰🇿", "uz": "🇺🇿", "de": "🇩🇪", "fr": "🇫🇷", "es": "🇪🇸", "it": "🇮🇹",
    "pt": "🇵🇹", "pt-br": "🇧🇷", "nl": "🇳🇱", "pl": "🇵🇱", "cs": "🇨🇿", "sv": "🇸🇪", "ro": "🇷🇴",
    "hu": "🇭🇺", "el": "🇬🇷", "tr": "🇹🇷", "ar": "🇸🇦", "fa": "🇮🇷", "he": "🇮🇱", "hi": "🇮🇳",
    "vi": "🇻🇳", "th": "🇹🇭", "id": "🇮🇩", "ms": "🇲🇾",
}


def format_language(language_code: str | None) -> str:
    """Format a Telegram language_code as "en 🇬🇧"."""
    if not language_code:
        return _("Unknown")
    flag = LANGUAGE_FLAGS.get(language_code.lower())
    return f"{language_code} {flag}" if flag else language_code


def build_user_info_pin_text(user_id: int, first_name: str, last_name: str | None,
                             username: str | None, language_code: str | None = None,
                             tag: str | None = None) -> str:
    """Build markdown text for the pinned user info message in a topic."""
    username_display = _("Not set") if username is None else f"@{username}"
    last_name_part = "" if last_name is None else f" {last_name}"
    full_name = escape_markdown(f"{first_name}{last_name_part}")
    lines = [
        f"• ID: [{user_id}](tg://openmessage?user_id={user_id})",
        f"• {_('Full Name')}: {full_name}",
        f"• {_('Username')}: {escape_markdown(username_display)}",
        f"• {_('Language')}: {escape_markdown(format_language(language_code))}",
    ]
    if tag:
        lines.append(f"• {_('Tag')}: {escape_markdown(tag)}")
    lines.append(f"#id{user_id}")
    return "\n".join(lines)


def send_and_pin_user_info(bot, group_id: int, thread_id: int, text: str) -> None:
    """Send user info message in a topic and pin it."""
    try:
        pin_message = bot.send_message(group_id, text, message_thread_id=thread_id, parse_mode='markdown')
        bot.pin_chat_message(group_id, pin_message.message_id)
    except Exception as e:
        logger.warning(f"Failed to pin info message in thread {thread_id}: {e}")
