"""Permission policy helpers for BetterForward."""

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional, Tuple


ENABLE = "enable"
DISABLE = "disable"
ALLOW = "allow"
DENY = "deny"
ALL_PERMISSION_KEY = "all"

SETTING_PREFIX = "permission_default_"
RESTRICTED_REPLY_ENABLED_KEY = "permission_restricted_reply_enabled"
RESTRICTED_REPLY_MESSAGE_KEY = "permission_restricted_reply_message"
DEFAULT_RESTRICTED_REPLY_MESSAGE = "您不被允许发送“{permission}”类型消息，请先联系对方解限。"


@dataclass(frozen=True)
class PermissionDefinition:
    key: str
    label: str
    menu_label: str


PERMISSION_DEFINITIONS = {
    "photo": PermissionDefinition("photo", "图片", "图片权限"),
    "sticker": PermissionDefinition("sticker", "贴图&动图", "贴图&动图权限"),
    "video": PermissionDefinition("video", "视频", "视频权限"),
    "voice": PermissionDefinition("voice", "语音", "语音权限"),
    "file": PermissionDefinition("file", "文件", "文件权限"),
    "link": PermissionDefinition("link", "链接", "链接权限"),
    "username": PermissionDefinition("username", "用户名", "用户名权限"),
}

PERMISSION_KEYS = tuple(PERMISSION_DEFINITIONS.keys())
PERMISSION_COMMAND_KEYS = PERMISSION_KEYS + (ALL_PERMISSION_KEY,)


class UnknownPermissionKey(ValueError):
    """Raised when a permission key is not in the canonical registry."""


def list_permission_keys() -> Tuple[str, ...]:
    """Return canonical permission keys in menu order."""
    return PERMISSION_KEYS


def list_permission_command_keys() -> Tuple[str, ...]:
    """Return permission keys accepted by topic permission commands."""
    return PERMISSION_COMMAND_KEYS


def normalize_permission_key(value) -> str:
    """Normalize administrator command input to a canonical key candidate."""
    return str(value or "").strip().lower()


def is_permission_key(value) -> bool:
    """Return True when value names a known permission key."""
    return normalize_permission_key(value) in PERMISSION_DEFINITIONS


def require_permission_key(value) -> str:
    """Return a normalized key or raise UnknownPermissionKey."""
    key = normalize_permission_key(value)
    if key not in PERMISSION_DEFINITIONS:
        raise UnknownPermissionKey(f"Unknown permission key: {value}")
    return key


def permission_label(value) -> str:
    """Return the user-facing noun label for a permission key."""
    return PERMISSION_DEFINITIONS[require_permission_key(value)].label


def permission_menu_label(value) -> str:
    """Return the admin menu label for a permission key."""
    return PERMISSION_DEFINITIONS[require_permission_key(value)].menu_label


def parse_permission_keys(values) -> Tuple[List[str], List[str]]:
    """
    Parse one or more command permission keys.

    Returns a tuple of (valid_keys, unknown_values). Duplicate valid keys are
    removed while preserving the administrator's order.
    """
    valid = []
    unknown = []
    seen = set()

    for raw_value in _iter_permission_tokens(values):
        key = normalize_permission_key(raw_value)
        if not key:
            continue
        if key == ALL_PERMISSION_KEY:
            for permission_key in PERMISSION_KEYS:
                if permission_key not in seen:
                    valid.append(permission_key)
                    seen.add(permission_key)
        elif key in PERMISSION_DEFINITIONS:
            if key not in seen:
                valid.append(key)
                seen.add(key)
        else:
            unknown.append(str(raw_value))

    return valid, unknown


def _iter_permission_tokens(values):
    if isinstance(values, str):
        normalized = values.replace(",", " ").replace("，", " ")
        yield from normalized.split()
        return

    if values is None:
        return

    if isinstance(values, Iterable):
        for value in values:
            if isinstance(value, str) and ("," in value or "，" in value or " " in value.strip()):
                yield from _iter_permission_tokens(value)
            else:
                yield value
        return

    yield values


def _setting_key(permission_key: str) -> str:
    return f"{SETTING_PREFIX}{require_permission_key(permission_key)}"


def _enabled_to_setting_value(enabled) -> str:
    if isinstance(enabled, bool):
        return ENABLE if enabled else DISABLE

    value = str(enabled or "").strip().lower()
    if value not in (ENABLE, DISABLE):
        raise ValueError("Permission default must be 'enable' or 'disable'")
    return value


def _setting_value_to_bool(value) -> bool:
    return str(value or ENABLE).strip().lower() == ENABLE


def _require_override_value(value) -> str:
    override = str(value or "").strip().lower()
    if override not in (ALLOW, DENY):
        raise ValueError("Permission override must be 'allow' or 'deny'")
    return override


class PermissionManager:
    """Read, write, and resolve BetterForward permission policy."""

    def __init__(self, db_path: str = "./data/storage.db", cache=None, database=None):
        self.db_path = db_path
        self.cache = cache
        self.database = database

    def get_global_default_value(self, permission_key: str) -> str:
        """Return the durable setting value, either 'enable' or 'disable'."""
        key = _setting_key(permission_key)
        value = self._get_setting(key, ENABLE)
        if value not in (ENABLE, DISABLE):
            return ENABLE
        return value

    def get_global_default(self, permission_key: str) -> bool:
        """Return True when the global default allows this permission."""
        return _setting_value_to_bool(self.get_global_default_value(permission_key))

    def set_global_default(self, permission_key: str, enabled) -> str:
        """Set a permission's global default to 'enable' or 'disable'."""
        key = _setting_key(permission_key)
        value = _enabled_to_setting_value(enabled)
        self._set_setting(key, value)
        return value

    def get_user_overrides(self, user_id: int) -> dict:
        """Return all explicit allow/deny overrides for a user."""
        with self._connect() as db:
            cursor = db.cursor()
            cursor.execute(
                """
                SELECT permission_key, override
                FROM user_permission_overrides
                WHERE user_id = ?
                """,
                (int(user_id),),
            )
            return {
                key: override
                for key, override in cursor.fetchall()
                if key in PERMISSION_DEFINITIONS and override in (ALLOW, DENY)
            }

    def get_all_user_overrides(self, user_id: int) -> dict:
        """Alias for callers that prefer explicit wording."""
        return self.get_user_overrides(user_id)

    def get_user_override(self, user_id: int, permission_key: str) -> Optional[str]:
        """Return 'allow', 'deny', or None when the user inherits global policy."""
        key = require_permission_key(permission_key)
        with self._connect() as db:
            cursor = db.cursor()
            cursor.execute(
                """
                SELECT override
                FROM user_permission_overrides
                WHERE user_id = ? AND permission_key = ?
                LIMIT 1
                """,
                (int(user_id), key),
            )
            row = cursor.fetchone()

        if row is None:
            return None
        override = row[0]
        if override not in (ALLOW, DENY):
            return None
        return override

    def set_user_override(self, user_id: int, permission_key: str, override: str) -> str:
        """Set an explicit per-user override to 'allow' or 'deny'."""
        key = require_permission_key(permission_key)
        override = _require_override_value(override)

        with self._connect() as db:
            cursor = db.cursor()
            cursor.execute(
                """
                UPDATE user_permission_overrides
                SET override = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND permission_key = ?
                """,
                (override, int(user_id), key),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO user_permission_overrides (user_id, permission_key, override)
                    VALUES (?, ?, ?)
                    """,
                    (int(user_id), key, override),
                )
            db.commit()

        return override

    def clear_user_override(self, user_id: int, permission_key: str):
        """Remove a user override so the user inherits the global default."""
        key = require_permission_key(permission_key)
        with self._connect() as db:
            cursor = db.cursor()
            cursor.execute(
                """
                DELETE FROM user_permission_overrides
                WHERE user_id = ? AND permission_key = ?
                """,
                (int(user_id), key),
            )
            db.commit()

    def resolve_permission(self, user_id: int, permission_key: str) -> bool:
        """Resolve effective policy: deny, allow, then global default."""
        override = self.get_user_override(user_id, permission_key)
        if override == DENY:
            return False
        if override == ALLOW:
            return True
        return self.get_global_default(permission_key)

    def is_allowed(self, user_id: int, permission_key: str) -> bool:
        """Alias for resolve_permission."""
        return self.resolve_permission(user_id, permission_key)

    def get_restricted_reply_enabled_value(self) -> str:
        """Return 'enable' or 'disable' for restricted-message replies."""
        value = self._get_setting(RESTRICTED_REPLY_ENABLED_KEY, ENABLE)
        if value not in (ENABLE, DISABLE):
            return ENABLE
        return value

    def get_restricted_reply_enabled(self) -> bool:
        """Return True when blocked messages should receive a reply."""
        return _setting_value_to_bool(self.get_restricted_reply_enabled_value())

    def set_restricted_reply_enabled(self, enabled) -> str:
        """Set restricted-message replies to 'enable' or 'disable'."""
        value = _enabled_to_setting_value(enabled)
        self._set_setting(RESTRICTED_REPLY_ENABLED_KEY, value)
        return value

    def get_restricted_reply_message(self) -> str:
        """Return the configured restricted-message reply template."""
        return self._get_setting(RESTRICTED_REPLY_MESSAGE_KEY, DEFAULT_RESTRICTED_REPLY_MESSAGE)

    def set_restricted_reply_message(self, message: str) -> str:
        """Set the restricted-message reply template."""
        value = str(message)
        self._set_setting(RESTRICTED_REPLY_MESSAGE_KEY, value)
        return value

    def format_restricted_reply(self, permissions) -> Optional[str]:
        """
        Format a restricted-message reply.

        Returns None when no-reply mode is configured with
        permission_restricted_reply_enabled='disable'. Only registry labels are
        interpolated; raw user message text is never used.
        """
        if not self.get_restricted_reply_enabled():
            return None

        labels = self._permission_labels_for_reply(permissions)
        label_text = "、".join(labels) if labels else "未知"
        template = self.get_restricted_reply_message() or DEFAULT_RESTRICTED_REPLY_MESSAGE
        return template.replace("{permission}", label_text)

    def _permission_labels_for_reply(self, permissions) -> List[str]:
        labels = []
        seen = set()

        for value in _iter_permission_tokens(permissions):
            text = str(value or "").strip()
            if not text:
                continue

            key = normalize_permission_key(text)
            if key in PERMISSION_DEFINITIONS:
                label = PERMISSION_DEFINITIONS[key].label
            else:
                label = self._label_to_registered_label(text)

            if label and label not in seen:
                labels.append(label)
                seen.add(label)

        return labels

    def _label_to_registered_label(self, label: str) -> Optional[str]:
        for definition in PERMISSION_DEFINITIONS.values():
            if label in (definition.label, definition.menu_label):
                return definition.label
        return None

    @contextmanager
    def _connect(self):
        if self.database is not None:
            db = self.database.get_connection()
        else:
            db = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        try:
            yield db
        finally:
            db.close()

    def _cache_get(self, setting_key: str):
        if self.cache is None:
            return None
        return self.cache.get(f"setting_{setting_key}")

    def _cache_set(self, setting_key: str, value: str):
        if self.cache is not None:
            self.cache.set(f"setting_{setting_key}", value)

    def _get_setting(self, setting_key: str, default=None):
        cached_value = self._cache_get(setting_key)
        if cached_value is not None:
            return cached_value

        if self.database is not None:
            value = self.database.get_setting(setting_key)
        else:
            with self._connect() as db:
                cursor = db.cursor()
                cursor.execute("SELECT value FROM settings WHERE key = ? LIMIT 1", (setting_key,))
                row = cursor.fetchone()
                value = row[0] if row else None

        if value is None:
            value = default
        if value is not None:
            self._cache_set(setting_key, value)
        return value

    def _set_setting(self, setting_key: str, value: str):
        with self._connect() as db:
            cursor = db.cursor()
            cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (value, setting_key))
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (setting_key, value),
                )
            db.commit()
        self._cache_set(setting_key, value)
