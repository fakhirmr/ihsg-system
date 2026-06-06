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
) -> Optional[int]:
    """
    Send a text message via Telegram Bot API.

    Returns:
        message_id (int) if sent successfully, None otherwise.
    """
    token = TELEGRAM_BOT_TOKEN.strip()
    chat = (chat_id or TELEGRAM_CHAT_ID).strip()

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Message not sent.")
        return None
    if not chat:
        logger.error("TELEGRAM_CHAT_ID is not set. Message not sent.")
        return None

    url = TELEGRAM_API_BASE.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            msg_id: int = data["result"]["message_id"]
            logger.info(f"Telegram message sent to {chat} (id={msg_id}, {len(text)} chars).")
            return msg_id
        else:
            logger.error(f"Telegram API error: {resp.status_code} — {resp.text[:300]}")
            return None
    except requests.RequestException as exc:
        logger.error(f"Telegram request failed: {exc}")
        return None


def delete_message(message_id: int, chat_id: Optional[str] = None) -> bool:
    """Delete a message by its message_id. Returns True on success."""
    token = TELEGRAM_BOT_TOKEN.strip()
    chat = (chat_id or TELEGRAM_CHAT_ID).strip()
    url = TELEGRAM_API_BASE.format(token=token, method="deleteMessage")
    try:
        resp = requests.post(url, json={"chat_id": chat, "message_id": message_id}, timeout=10)
        ok = resp.status_code == 200 and resp.json().get("ok", False)
        if ok:
            logger.info(f"Deleted Telegram message {message_id}")
        else:
            logger.warning(f"Failed to delete message {message_id}: {resp.text[:200]}")
        return ok
    except requests.RequestException as exc:
        logger.error(f"deleteMessage request failed: {exc}")
        return False


def get_recent_channel_posts(limit: int = 100) -> list[dict]:
    """
    Fetch recent channel_post updates via getUpdates.
    Only works if the bot has pending (unread) channel_post updates.
    """
    token = TELEGRAM_BOT_TOKEN.strip()
    chat = TELEGRAM_CHAT_ID.strip()
    url = TELEGRAM_API_BASE.format(token=token, method="getUpdates")
    all_posts: list[dict] = []
    offset: Optional[int] = None

    while True:
        params: dict = {"limit": min(limit, 100), "allowed_updates": ["channel_post"], "timeout": 0}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(url, params=params, timeout=20)
            updates = resp.json().get("result", [])
        except Exception as exc:
            logger.error(f"getUpdates failed: {exc}")
            break

        if not updates:
            break

        for upd in updates:
            post = upd.get("channel_post")
            if post and str(post.get("chat", {}).get("id", "")) == chat.lstrip("-"):
                all_posts.append(post)
            offset = upd["update_id"] + 1

        if len(updates) < 100:
            break

    return all_posts


def send_alert_chunked(text: str, chat_id: Optional[str] = None) -> list[int]:
    """
    Send a long message in chunks of ≤4096 characters (Telegram limit).

    Returns list of message_ids that were sent successfully.
    """
    MAX_LEN = 4096
    if len(text) <= MAX_LEN:
        mid = send_message(text, chat_id=chat_id)
        return [mid] if mid else []

    chunks = [text[i : i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    ids: list[int] = []
    for chunk in chunks:
        mid = send_message(chunk, chat_id=chat_id)
        if mid:
            ids.append(mid)
    return ids
