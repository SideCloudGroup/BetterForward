"""Base class for spam detection implementations."""

from abc import ABC, abstractmethod
from typing import Optional, Tuple

from telebot.types import Message


class SpamDetectorBase(ABC):
    """
    Abstract base class for spam detectors.
    
    All spam detection implementations should inherit from this class
    and implement the detect() method.
    """

    @abstractmethod
    def detect(self, message: Message) -> Tuple[bool, Optional[dict]]:
        """
        Detect if a message is spam.
        
        Args:
            message: The Telegram message to check
            
        Returns:
            Tuple containing:
            - bool: True if spam detected, False otherwise
            - dict or None: Additional information about the detection
                           (e.g., matched keyword, confidence score, reason)
                           
        Example return values:
            (True, {"method": "keyword", "matched": "spam_word"})
            (True, {"method": "ml", "confidence": 0.95})
            (False, None)
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """
        Get the name of this spam detector.
        
        Returns:
            str: A human-readable name for this detector
        """
        pass

    def is_enabled(self) -> bool:
        """
        Check if this detector is enabled.
        
        Returns:
            bool: True if enabled, False otherwise
        """
        return True
