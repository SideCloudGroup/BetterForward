import json
import sqlite3


LEGACY_CAPTCHA_VALUES = {
    "disable": [],
    "math": ["math"],
    "button": ["button"],
    "tguard": ["tguard"],
}

DEFAULT_CAPTCHA_CONFIG = {
    "methods": [],
    "qa": [],
    "sticker": {
        "mode": "any",
        "file_id": None,
        "file_unique_id": None,
    },
}


def _legacy_captcha_config(raw):
    config = {
        "methods": [],
        "qa": [],
        "sticker": {
            "mode": "any",
            "file_id": None,
            "file_unique_id": None,
        },
    }
    if raw is None or raw == "":
        return config
    text = str(raw).strip()
    if text in LEGACY_CAPTCHA_VALUES:
        config["methods"] = list(LEGACY_CAPTCHA_VALUES[text])
        return config
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return config
    if isinstance(parsed, list):
        config["methods"] = [item for item in ("math", "button", "emoji", "qa", "sticker", "tguard")
                             if item in parsed]
        return config
    if isinstance(parsed, dict):
        methods = parsed.get("methods") or []
        config["methods"] = [item for item in ("math", "button", "emoji", "qa", "sticker", "tguard")
                             if item in methods]
        qa_items = []
        for item in parsed.get("qa") or []:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answers = item.get("answers") or []
            if isinstance(answers, str):
                answers = [answers]
            answers = [str(answer).strip() for answer in answers if str(answer).strip()]
            if question and answers:
                qa_items.append({
                    "id": int(item.get("id") or len(qa_items) + 1),
                    "question": question,
                    "answers": answers,
                })
        config["qa"] = qa_items
        sticker = parsed.get("sticker") or {}
        if isinstance(sticker, dict):
            mode = sticker.get("mode") if sticker.get("mode") in ("any", "match") else "any"
            config["sticker"] = {
                "mode": mode,
                "file_id": sticker.get("file_id") or None,
                "file_unique_id": sticker.get("file_unique_id") or None,
            }
        return config
    return config


def upgrade(db_path):
    with sqlite3.connect(db_path) as conn:
        db_cursor = conn.cursor()

        db_cursor.execute(
            """
            DELETE FROM settings
            WHERE id NOT IN (
                SELECT MAX(id) FROM settings GROUP BY key
            )
            """
        )
        db_cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_settings_key ON settings(key)")

        db_cursor.execute("SELECT value FROM settings WHERE key = 'captcha' LIMIT 1")
        row = db_cursor.fetchone()
        captcha_json = json.dumps(_legacy_captcha_config(row[0] if row else None),
                                  ensure_ascii=False, separators=(",", ":"))
        if row:
            db_cursor.execute("UPDATE settings SET value = ? WHERE key = 'captcha'", (captcha_json,))
        else:
            db_cursor.execute(
                "INSERT INTO settings (key, value) VALUES ('captcha', ?)",
                (captcha_json,),
            )

        db_cursor.execute("""
            CREATE TABLE IF NOT EXISTS verified_users_new (
                user_id INTEGER PRIMARY KEY
            )
        """)
        db_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='verified_users'")
        if db_cursor.fetchone():
            db_cursor.execute("""
                INSERT OR IGNORE INTO verified_users_new (user_id)
                SELECT user_id FROM verified_users WHERE user_id IS NOT NULL
            """)
            db_cursor.execute("DROP TABLE verified_users")
        db_cursor.execute("ALTER TABLE verified_users_new RENAME TO verified_users")

        conn.commit()
