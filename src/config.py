"""Configuration module for BetterForward."""

import argparse
import gettext
import logging
import os
import signal

import telebot.apihelper

# Parse command-line arguments
parser = argparse.ArgumentParser(description="BetterForward - Telegram message forwarding bot")
# Token and group ID may come from the TOKEN / GROUP_ID environment variables so the
# token does not show up in the process list.
parser.add_argument("-token", type=str, default=os.environ.get("TOKEN") or None,
                    required=not os.environ.get("TOKEN"), help="Telegram bot token (or env TOKEN)")
parser.add_argument("-group_id", type=str, default=os.environ.get("GROUP_ID") or None,
                    required=not os.environ.get("GROUP_ID"), help="Group ID (or env GROUP_ID)")
parser.add_argument("-language", type=str, default="en_US", help="Language",
                    choices=["en_US", "zh_CN", "ja_JP"])
parser.add_argument("-tg_api", type=str, required=False, default="", help="Telegram API URL")
parser.add_argument("-workers", type=int, default=5,
                    help="Number of worker threads for message processing (default: 5)")
args = parser.parse_args()

# Setup logging
logger = logging.getLogger()
logger.setLevel("INFO")
BASIC_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
formatter = logging.Formatter(BASIC_FORMAT, DATE_FORMAT)
chlr = logging.StreamHandler()
chlr.setFormatter(formatter)
logger.addHandler(chlr)

# Setup internationalization
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
locale_dir = os.path.join(project_root, "locale")
gettext.bindtextdomain("BetterForward", locale_dir)
gettext.textdomain("BetterForward")
try:
    _ = gettext.translation("BetterForward", locale_dir, languages=[args.language]).gettext
except FileNotFoundError:
    _ = gettext.gettext

# Global stop flag
stop = False

# Setup custom Telegram API URL if provided
if args.tg_api != "":
    telebot.apihelper.API_URL = f"{args.tg_api}/bot{{0}}/{{1}}"


def handle_sigterm(*args):
    """Handle SIGTERM and SIGINT signals."""
    global stop
    stop = True
    raise KeyboardInterrupt()


# Register signal handlers
signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)
