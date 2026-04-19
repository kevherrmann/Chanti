"""Telegram-Benachrichtigungen direkt über die Bot-API.

Benötigt in .env:
    TELEGRAM_BOT_TOKEN=123456:ABCDEF...
    TELEGRAM_CHAT_ID=123456789
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("chanti")

_API_BASE = "https://api.telegram.org"


def _get_credentials() -> tuple[str, str] | None:
    """Liest Token + Chat-ID aus der Umgebung. config.py hat .env bereits geladen."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def send_telegram(text: str, timeout: float = 10.0) -> bool:
    """Sendet eine Textnachricht. Gibt True zurück bei Erfolg."""
    creds = _get_credentials()
    if not creds:
        logger.warning("Telegram nicht konfiguriert (TELEGRAM_BOT_TOKEN/CHAT_ID fehlen)")
        return False
    token, chat_id = creds

    url = f"{_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        if r.status_code == 200 and r.json().get("ok"):
            return True
        logger.error(f"Telegram send failed: {r.status_code} {r.text[:200]}")
        return False
    except requests.RequestException as e:
        logger.error(f"Telegram send exception: {e}")
        return False
