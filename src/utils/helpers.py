"""Helper functions for BetterForward."""

import re


def escape_markdown(text: str) -> str:
    """Escape markdown special characters."""
    escape_chars = r'\*_`\[\]()'
    return re.sub(f'([{escape_chars}])', r'\\\1', text)
