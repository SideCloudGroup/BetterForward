"""
BetterForward - A Telegram bot for forwarding messages with topic support.
Entry point for the application.
"""

from src.config import args, logger, _
from src.bot import TGBot


if __name__ == "__main__":
    if not args.token or not args.group_id:
        logger.error(_("Token or group ID is empty"))
        exit(1)
    try:
        bot = TGBot(args.token, args.group_id)
    except KeyboardInterrupt:
        logger.info(_("Exiting..."))
        exit(0)
