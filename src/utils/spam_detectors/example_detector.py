"""Example spam detector template - DO NOT USE IN PRODUCTION."""

from typing import Optional, Tuple

from telebot.types import Message

from src.utils.spam_detector_base import SpamDetectorBase


class ExampleSpamDetector(SpamDetectorBase):
    """
    Example spam detector implementation.
    
    This is a template showing how to create a new spam detector.
    Copy this file and implement your own detection logic.
    """

    def __init__(self, custom_param=None):
        """
        Initialize your detector.
        
        Args:
            custom_param: Any parameters your detector needs
        """
        self.custom_param = custom_param
        self.enabled = True

    def detect(self, message: Message) -> Tuple[bool, Optional[dict]]:
        """
        Implement your spam detection logic here.
        
        Args:
            message: The Telegram message to check
            
        Returns:
            Tuple of (is_spam, detection_info)
            
        Example implementations:
        
        1. URL detection:
            if 'http' in message.text:
                return True, {"method": "url", "detector": self.get_name()}
                
        2. Length-based:
            if len(message.text) > 500:
                return True, {"method": "length", "detector": self.get_name()}
                
        3. Pattern matching:
            import re
            if re.search(r'\d{10,}', message.text):  # 10+ consecutive digits
                return True, {"method": "pattern", "detector": self.get_name()}
                
        4. External API:
            response = requests.post('https://api.example.com/check', json={'text': message.text})
            if response.json()['is_spam']:
                return True, {
                    "method": "api",
                    "confidence": response.json()['confidence'],
                    "detector": self.get_name()
                }
        """
        # Example: Detect messages with more than 3 emojis
        if message.text:
            import re
            emoji_pattern = re.compile(
                "["
                "\U0001F600-\U0001F64F"  # emoticons
                "\U0001F300-\U0001F5FF"  # symbols & pictographs
                "\U0001F680-\U0001F6FF"  # transport & map symbols
                "\U0001F1E0-\U0001F1FF"  # flags (iOS)
                "]+", flags=re.UNICODE
            )
            emoji_count = len(emoji_pattern.findall(message.text))

            if emoji_count > 3:
                return True, {
                    "method": "emoji_count",
                    "emoji_count": emoji_count,
                    "detector": self.get_name()
                }

        return False, None

    def get_name(self) -> str:
        """Return the name of this detector."""
        return "Example Detector"

    def is_enabled(self) -> bool:
        """Check if this detector is enabled."""
        return self.enabled

    def set_enabled(self, enabled: bool):
        """Enable or disable this detector."""
        self.enabled = enabled


# How to use this detector in bot.py:
"""
from src.utils.spam_detectors.example_detector import ExampleSpamDetector

# In TGBot.__init__():
self.example_detector = ExampleSpamDetector()
self.spam_detector_manager.register_detector(self.example_detector)

# That's it! The detector will now be used automatically.
"""
