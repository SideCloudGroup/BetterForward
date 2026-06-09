"""Message classification helpers for permission enforcement."""

import re


CONTENT_TYPE_PERMISSION_MAP = {
    "photo": "photo",
    "sticker": "sticker",
    "animation": "sticker",
    "video": "video",
    "voice": "voice",
    "document": "file",
}

LINK_ENTITY_TYPES = {"url", "text_link"}
USERNAME_ENTITY_TYPES = {"mention"}

RAW_LINK_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
RAW_USERNAME_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9][A-Za-z0-9_]{0,31}\b")


def classify_message_permissions(message) -> tuple[str, ...]:
    """Return canonical permission keys required by a Telegram-like message."""
    permissions = []
    media_permission = CONTENT_TYPE_PERMISSION_MAP.get(getattr(message, "content_type", None))
    if media_permission:
        permissions.append(media_permission)

    if _message_has_link(message):
        permissions.append("link")

    if _message_has_username(message):
        permissions.append("username")

    return tuple(_dedupe(permissions))


def _message_has_link(message) -> bool:
    return any(
        _has_entity_type(entities, LINK_ENTITY_TYPES) or _has_raw_link(text)
        for text, entities in _message_text_parts(message)
    )


def _message_has_username(message) -> bool:
    return any(
        _has_entity_type(entities, USERNAME_ENTITY_TYPES) or _has_raw_username(text)
        for text, entities in _message_text_parts(message)
    )


def _message_text_parts(message):
    yield getattr(message, "text", None), getattr(message, "entities", None)
    yield getattr(message, "caption", None), getattr(message, "caption_entities", None)


def _has_entity_type(entities, entity_types: set[str]) -> bool:
    if not entities:
        return False
    return any(getattr(entity, "type", None) in entity_types for entity in entities)


def _has_raw_link(text) -> bool:
    if not text:
        return False
    return RAW_LINK_RE.search(text) is not None


def _has_raw_username(text) -> bool:
    if not text:
        return False
    return RAW_USERNAME_RE.search(text) is not None


def _dedupe(values):
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            yield value
