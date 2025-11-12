"""Keyword-based spam detector."""

import json
import os
import re
import threading
from typing import List, Optional, Tuple

from telebot.types import Message

from src.config import logger
from src.utils.spam_detector_base import SpamDetectorBase


class KeywordSpamDetector(SpamDetectorBase):
    """Spam detector using keyword matching with optimized regex."""

    def __init__(self, keywords_file: str = "./data/spam_keywords.json"):
        self.keywords_file = keywords_file
        self.lock = threading.Lock()
        self._ensure_file_exists()

        # Cache for optimized matching
        self._keyword_pattern = None
        self._keywords_cache = None
        self._cache_timestamp = 0

    def _ensure_file_exists(self):
        """Ensure the keywords file exists."""
        if not os.path.exists(os.path.dirname(self.keywords_file)):
            os.makedirs(os.path.dirname(self.keywords_file))

        if not os.path.exists(self.keywords_file):
            with open(self.keywords_file, 'w', encoding='utf-8') as f:
                json.dump({"keywords": []}, f, ensure_ascii=False, indent=2)

    def _load_keywords(self) -> dict:
        """Load keywords from JSON file."""
        try:
            with open(self.keywords_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load keywords: {e}")
            return {"keywords": []}

    def _save_keywords(self, data: dict):
        """Save keywords to JSON file."""
        try:
            with open(self.keywords_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # Invalidate cache when keywords are modified
            self._invalidate_cache()
        except Exception as e:
            logger.error(f"Failed to save keywords: {e}")

    def _invalidate_cache(self):
        """Invalidate the compiled regex pattern cache."""
        # Note: This method assumes the caller already holds self.lock
        self._keyword_pattern = None
        self._keywords_cache = None
        self._cache_timestamp = 0

    def _build_pattern(self, keywords: List[str]) -> Optional[re.Pattern]:
        """
        Build an optimized regex pattern for all keywords.
        Uses fuzzy matching (substring match) and case-insensitive matching.
        """
        if not keywords:
            return None

        # Escape special regex characters in keywords
        escaped_keywords = [re.escape(kw) for kw in keywords]

        # Join with OR operator for fuzzy/substring matching
        # No word boundaries - will match anywhere in the text
        pattern_str = '|'.join(escaped_keywords)

        try:
            return re.compile(pattern_str, re.IGNORECASE)
        except re.error as e:
            logger.error(f"Failed to compile keyword pattern: {e}")
            return None

    def detect(self, message: Message) -> Tuple[bool, Optional[dict]]:
        """
        Check if message contains any spam keyword.
        
        Args:
            message: Telegram message to check
            
        Returns:
            Tuple of (is_spam, info_dict)
            - is_spam: True if spam detected
            - info_dict: {"method": "keyword", "matched": "keyword"}
        """
        # Only check text messages
        if not message.text:
            return False, None

        matched_keyword = self._check_message_text(message.text)

        if matched_keyword:
            return True, {
                "method": "keyword",
                "matched": matched_keyword,
                "detector": self.get_name()
            }

        return False, None

    def _check_message_text(self, text: str) -> Optional[str]:
        """
        Check if text contains any spam keyword using optimized regex matching.
        Returns the matched keyword if found, None otherwise.
        """
        if not text:
            return None

        # Get current file modification time
        try:
            current_mtime = os.path.getmtime(self.keywords_file)
        except OSError:
            current_mtime = 0

        # Rebuild pattern if cache is invalid or file was modified
        if (self._keyword_pattern is None or
                self._keywords_cache is None or
                current_mtime != self._cache_timestamp):

            keywords = self.get_all_keywords()
            if not keywords:
                return None

            with self.lock:
                self._keywords_cache = keywords
                self._keyword_pattern = self._build_pattern(keywords)
                self._cache_timestamp = current_mtime

        if self._keyword_pattern is None:
            return None

        # Use regex to find match (single pass, very fast)
        match = self._keyword_pattern.search(text)
        if match:
            # Find which keyword was matched by comparing with cached keywords
            matched_text = match.group(0)
            for keyword in self._keywords_cache:
                if keyword.lower() == matched_text.lower() or keyword.lower() in matched_text.lower():
                    return keyword
            # If exact match not found, return the matched text
            return matched_text

        return None

    def get_name(self) -> str:
        """Get the detector name."""
        return "Keyword Detector"

    # Management methods for keywords
    def add_keyword(self, keyword: str) -> bool:
        """Add a spam keyword."""
        with self.lock:
            data = self._load_keywords()
            keyword = keyword.strip()

            if not keyword:
                return False

            if keyword in data["keywords"]:
                return False

            data["keywords"].append(keyword)
            self._save_keywords(data)
            logger.info(f"Added spam keyword: {keyword}")
            return True

    def remove_keyword(self, keyword: str) -> bool:
        """Remove a spam keyword."""
        with self.lock:
            data = self._load_keywords()

            if keyword not in data["keywords"]:
                return False

            data["keywords"].remove(keyword)
            self._save_keywords(data)
            logger.info(f"Removed spam keyword: {keyword}")
            return True

    def get_all_keywords(self) -> List[str]:
        """Get all spam keywords."""
        data = self._load_keywords()
        return data.get("keywords", [])

    def get_keyword_count(self) -> int:
        """Get total number of keywords."""
        return len(self.get_all_keywords())
