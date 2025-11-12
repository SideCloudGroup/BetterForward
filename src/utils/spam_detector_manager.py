"""Spam detector manager to coordinate multiple spam detectors."""

from typing import List, Optional, Tuple

from telebot.types import Message

from src.config import logger, _
from src.utils.spam_detector_base import SpamDetectorBase


class SpamDetectorManager:
    """
    Manages multiple spam detectors and coordinates spam detection.
    
    Detectors are checked in order, and the first one to detect spam
    will stop the checking process.
    """

    def __init__(self):
        self.detectors: List[SpamDetectorBase] = []

    def register_detector(self, detector: SpamDetectorBase):
        """
        Register a spam detector.
        
        Args:
            detector: A spam detector instance that implements SpamDetectorBase
        """
        if not isinstance(detector, SpamDetectorBase):
            raise TypeError(f"Detector must be an instance of SpamDetectorBase, got {type(detector)}")

        self.detectors.append(detector)
        logger.info(_("Registered spam detector: {}").format(detector.get_name()))

    def unregister_detector(self, detector: SpamDetectorBase):
        """
        Unregister a spam detector.
        
        Args:
            detector: The detector instance to remove
        """
        if detector in self.detectors:
            self.detectors.remove(detector)
            logger.info(_("Unregistered spam detector: {}").format(detector.get_name()))

    def detect_spam(self, message: Message) -> Tuple[bool, Optional[dict]]:
        """
        Check if a message is spam using all registered detectors.
        
        Args:
            message: The message to check
            
        Returns:
            Tuple of (is_spam, detection_info)
            - is_spam: True if any detector identifies it as spam
            - detection_info: Information from the detector that identified the spam
                             None if not spam
        """
        for detector in self.detectors:
            # Skip disabled detectors
            if not detector.is_enabled():
                continue

            try:
                is_spam, info = detector.detect(message)

                if is_spam:
                    logger.info(_("Spam detected by {}: {}").format(
                        detector.get_name(),
                        info if info else "no details"
                    ))
                    return True, info

            except Exception as e:
                logger.error(_("Error in spam detector {}: {}").format(
                    detector.get_name(), str(e)
                ))
                continue

        return False, None

    def get_detector_by_name(self, name: str) -> Optional[SpamDetectorBase]:
        """
        Get a registered detector by name.
        
        Args:
            name: The name of the detector
            
        Returns:
            The detector instance or None if not found
        """
        for detector in self.detectors:
            if detector.get_name() == name:
                return detector
        return None

    def get_all_detectors(self) -> List[SpamDetectorBase]:
        """
        Get all registered detectors.
        
        Returns:
            List of all detector instances
        """
        return self.detectors.copy()

    def get_detector_count(self) -> int:
        """
        Get the number of registered detectors.
        
        Returns:
            Number of registered detectors
        """
        return len(self.detectors)

    def clear_detectors(self):
        """Remove all registered detectors."""
        self.detectors.clear()
        logger.info(_("All spam detectors cleared"))
