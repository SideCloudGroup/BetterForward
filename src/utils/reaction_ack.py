"""Short-lived ❤ reaction that confirms a message was processed."""

import threading

from telebot.apihelper import ApiTelegramException
from telebot.types import ReactionTypeEmoji

from src.config import logger, _

ACK_EMOJI = "❤"
ACK_SECONDS = 3


class ReactionAck:
    """Set a ❤ reaction on a message and remove it again after a few seconds.

    A bot holds at most one reaction per message, so any other reaction the bot
    sets on the same message (a mirrored reaction) must call release() first;
    the pending removal is then skipped.
    """

    def __init__(self, bot, delay: float = ACK_SECONDS):
        self.bot = bot
        self.delay = delay
        self._pending: dict[tuple[int, int], threading.Event] = {}
        self._lock = threading.Lock()

    def ack(self, chat_id: int, message_id: int) -> None:
        """React with ❤ now and schedule its removal."""
        # Publish before the request so release() can wait for an in-flight heart.
        # Keep network I/O outside the registry lock to avoid blocking other messages.
        token = threading.Event()
        with self._lock:
            self._pending[(chat_id, message_id)] = token
        sent = False
        try:
            self.bot.set_message_reaction(chat_id=chat_id, message_id=message_id,
                                          reaction=[ReactionTypeEmoji(ACK_EMOJI)])
            sent = True
        except ApiTelegramException as e:
            logger.warning(_("Failed to add processed reaction to message {}: {}").format(message_id, e))
            return
        finally:
            if not sent:
                with self._lock:
                    if self._pending.get((chat_id, message_id)) is token:
                        del self._pending[(chat_id, message_id)]
            token.set()

        timer = threading.Timer(self.delay, self._clear, args=(chat_id, message_id, token))
        timer.daemon = True
        timer.start()

    def release(self, chat_id: int, message_id: int) -> None:
        """Order the next reaction after any in-flight heart and cancel its removal."""
        with self._lock:
            token = self._pending.get((chat_id, message_id))
        if token is not None:
            token.wait()
            with self._lock:
                if self._pending.get((chat_id, message_id)) is token:
                    del self._pending[(chat_id, message_id)]

    def _clear(self, chat_id: int, message_id: int, token) -> None:
        # Hold the lock through the API call so a concurrent release() + new reaction
        # is always applied after this removal, never wiped by it.
        with self._lock:
            if self._pending.get((chat_id, message_id)) is not token:
                return
            del self._pending[(chat_id, message_id)]
            try:
                self.bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[])
            except ApiTelegramException as e:
                logger.warning(_("Failed to remove processed reaction from message {}: {}").format(message_id, e))
