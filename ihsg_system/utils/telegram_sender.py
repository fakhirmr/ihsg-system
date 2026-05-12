"""
IHSG Trading System — Telegram Sender
Sends messages to a Telegram bot using the Bot API (no external library needed).
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


def send_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> bool:
    """
    Send a text message via Telegram Bot API.

    Args:
        text:                    Message body (supports HTML or Markdown).
        chat_id:                 Destination chat/channel ID. Defaults to config value.
        parse_mode:              'HTML' or 'MarkdownV2'.
        disable_web_page_preview: Suppress URL previews.

    Returns:
        True if message was sent successfully, False otherwise.
    """
    token = TELEGRAM_BOT_TOKEN.strip()
    chat = (chat_id or TELEGRAM_CHAT_ID).strip()

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Message not sent.")
        return False
    if not chat:
        logger.error("TELEGRAM_CHAT_ID is not set. Message not sent.")
        return False

    url = TELEGRAM_API_BASE.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"Telegram message sent to {chat} ({len(text)} chars).")
            return True
        else:
            logger.error(
                f"Telegram API error: {resp.status_code} — {resp.text[:300]}"
            )
            return False
    except requests.RequestException as exc:
        logger.error(f"Telegram request failed: {exc}")
        return False


def send_alert_chunked(text: str, chat_id: Optional[str] = None) -> bool:
    """
    Send a long message in chunks of ≤4096 characters (Telegram limit).

    Returns True if ALL chunks were sent successfully.
    """
    MAX_LEN = 4096
    if len(text) <= MAX_LEN:
        return send_message(text, chat_id=chat_id)

    chunks = [text[i : i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    success = True
    for chunk in chunks:
        if not send_message(chunk, chat_id=chat_id):
            success = False
    return success
