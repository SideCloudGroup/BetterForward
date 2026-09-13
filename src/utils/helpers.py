"""Helper functions for BetterForward."""

import re

from src.config import _, logger


START_PAYLOAD_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


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


def build_user_info_pin_text(user_id: int, first_name: str, last_name: str | None,
                             username: str | None, tag: str | None = None) -> str:
    """Build markdown text for the pinned user info message in a topic."""
    username_display = _("Not set") if username is None else f"@{username}"
    last_name_part = "" if last_name is None else f" {last_name}"
    full_name = escape_markdown(f"{first_name}{last_name_part}")
    text = (
        f"{_('User ID')}: [{user_id}](tg://openmessage?user_id={user_id})\n"
        f"{_('Full Name')}: {full_name}\n"
        f"{_('Username')}: {escape_markdown(username_display)}\n"
    )
    if tag:
        text += f"{_('Tag')}: {escape_markdown(tag)}\n"
    return text


def send_and_pin_user_info(bot, group_id: int, thread_id: int, text: str) -> None:
    """Send user info message in a topic and pin it."""
    try:
        pin_message = bot.send_message(group_id, text, message_thread_id=thread_id, parse_mode='markdown')
        bot.pin_chat_message(group_id, pin_message.message_id)
    except Exception as e:
        logger.warning(f"Failed to pin info message in thread {thread_id}: {e}")
