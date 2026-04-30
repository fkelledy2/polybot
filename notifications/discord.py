# notifications/discord.py — S3-4
import logging
import os
import requests

logger = logging.getLogger(__name__)
_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")


def send(message: str) -> None:
    """POST message to Discord webhook. No-op if webhook URL is not set."""
    if not _WEBHOOK:
        return
    try:
        requests.post(_WEBHOOK, json={"content": message}, timeout=5)
    except Exception as e:
        logger.debug(f"Discord notification failed: {e}")
