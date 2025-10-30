"""Auto-response functionality for BetterForward."""

import re
import sqlite3
from datetime import datetime

import pytz

from src.config import logger, _


class AutoResponseManager:
    """Manages automatic responses to user messages."""

    def __init__(self, db_path: str, time_zone: pytz.timezone):
        self.db_path = db_path
        self.time_zone = time_zone

    def update_time_zone(self, time_zone: pytz.timezone):
        """Update the timezone used for time-based responses."""
        self.time_zone = time_zone

    def match_auto_response(self, text: str):
        """Match text against auto-response patterns."""
        if text is None:
            return None

        current_time = datetime.now(self.time_zone).time()

        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            db_cursor = db.cursor()

            # Check for exact match
            db_cursor.execute(
                "SELECT value, type, start_time, end_time FROM auto_response WHERE key = ? AND is_regex = 0",
                (text,))
            results = db_cursor.fetchall()
            for result in results:
                if self._is_within_time_range(current_time, result['start_time'], result['end_time']):
                    return {"response": result['value'], "type": result['type']}

            # Check for regex match
            db_cursor.execute(
                "SELECT key, value, type, start_time, end_time FROM auto_response WHERE is_regex = 1")
            results = db_cursor.fetchall()
            for row in results:
                try:
                    if re.match(row['key'], text) and self._is_within_time_range(
                            current_time, row['start_time'], row['end_time']):
                        return {"response": row['value'], "type": row['type']}
                except re.error:
                    logger.error(_("Invalid regular expression: {}").format(row['key']))
                    return None
        return None

    def _is_within_time_range(self, current_time, start_time, end_time) -> bool:
        """Check if current time is within the specified range."""
        if start_time is None or end_time is None:
            return True

        start_time = datetime.strptime(start_time, "%H:%M").time()
        end_time = datetime.strptime(end_time, "%H:%M").time()

        if start_time <= end_time:
            return start_time <= current_time <= end_time
        else:
            return current_time >= start_time or current_time <= end_time

    def add_auto_response(self, key: str, value: str, is_regex: bool, response_type: str,
                          start_time: str = None, end_time: str = None):
        """Add a new auto-response."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute(
                "INSERT INTO auto_response (key, value, is_regex, type, start_time, end_time) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, value, is_regex, response_type, start_time, end_time))
            db.commit()

    def delete_auto_response(self, response_id: int):
        """Delete an auto-response."""
        with sqlite3.connect(self.db_path) as db:
            db_cursor = db.cursor()
            db_cursor.execute("DELETE FROM auto_response WHERE id = ?", (response_id,))
            db.commit()

    def get_auto_response(self, response_id: int):
        """Get a specific auto-response."""
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            db_cursor = db.cursor()
            db_cursor.execute(
                "SELECT key, value, is_regex, type, start_time, end_time FROM auto_response "
                "WHERE id = ? LIMIT 1", (response_id,))
            return db_cursor.fetchone()

    def get_auto_responses_paginated(self, page: int = 1, page_size: int = 5):
        """Get paginated list of auto-responses."""
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            db_cursor = db.cursor()
            offset = (page - 1) * page_size

            db_cursor.execute("SELECT COUNT(*) FROM auto_response")
            total_responses = db_cursor.fetchone()[0]
            total_pages = (total_responses + page_size - 1) // page_size

            db_cursor.execute(
                "SELECT id, key, value, is_regex, type, start_time, end_time FROM auto_response "
                "LIMIT ? OFFSET ?", (page_size, offset))
            auto_responses = db_cursor.fetchall()

            return {
                "responses": auto_responses,
                "total": total_responses,
                "total_pages": total_pages,
                "current_page": page
            }
