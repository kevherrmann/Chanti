"""Direkter Telegram-Polling-Bot für Chanti.

Fallback/Alternative zum n8n-Telegram-Webhook:
- liest TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID aus .env
- nimmt nur Nachrichten aus TELEGRAM_CHAT_ID an
- ruft die lokale Chanti-API /chat auf
- sendet die Antwort zurück in Telegram
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s telegram_bot: %(message)s",
)
log = logging.getLogger("telegram_bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CHANTI_URL = os.environ.get("CHANTI_CHAT_URL", "http://127.0.0.1:8000/chat").strip()
API_BASE = f"https://api.telegram.org/bot{TOKEN}"
OFFSET_FILE = ROOT / "data" / "telegram_bot_offset.json"


def _load_offset() -> int | None:
    try:
        return int(json.loads(OFFSET_FILE.read_text()).get("offset"))
    except Exception:
        return None


def _save_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def _tg(method: str, **payload):
    response = requests.post(f"{API_BASE}/{method}", json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data.get("result")


def _ask_chanti(text: str) -> str:
    response = requests.post(CHANTI_URL, json={"message": text}, timeout=120)
    response.raise_for_status()
    data = response.json()
    answer = (data.get("response") or "").strip()
    return answer or "Ich habe gerade keine Antwort bekommen."


def _handle_message(message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        log.warning("Ignoriere Nachricht aus nicht erlaubtem Chat %s", chat_id)
        return

    text = (message.get("text") or "").strip()
    if not text:
        _tg("sendMessage", chat_id=chat_id, text="Ich kann aktuell nur Textnachrichten beantworten.")
        return

    log.info("Nachricht von %s: %r", chat_id, text[:120])
    try:
        _tg("sendChatAction", chat_id=chat_id, action="typing")
    except Exception as exc:
        log.warning("sendChatAction fehlgeschlagen: %s", exc)

    try:
        answer = _ask_chanti(text)
    except Exception as exc:
        log.exception("Chanti-API Fehler")
        answer = f"Chanti-API ist gerade nicht erreichbar: {exc}"

    # Telegram Limit: 4096 Zeichen. Auf sichere Chunks splitten.
    for i in range(0, len(answer), 3900):
        _tg("sendMessage", chat_id=chat_id, text=answer[i:i + 3900])


def main() -> int:
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN fehlt")
    if not ALLOWED_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID fehlt; Bot antwortet allen Chats")

    me = requests.get(f"{API_BASE}/getMe", timeout=15).json()
    log.info("Gestartet für Bot: %s", me.get("result", {}).get("username"))

    # Falls n8n/ngrok noch als Telegram-Webhook eingetragen ist, deaktivieren.
    # Polling und Webhook schließen sich bei Telegram gegenseitig aus.
    try:
        deleted = requests.post(
            f"{API_BASE}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=15,
        ).json()
        log.info("Webhook deaktiviert: %s", deleted)
    except Exception as exc:
        log.warning("Webhook konnte nicht deaktiviert werden: %s", exc)

    offset = _load_offset()
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            result = requests.post(f"{API_BASE}/getUpdates", json=params, timeout=60).json()
            if not result.get("ok"):
                log.warning("getUpdates nicht ok: %s", result)
                time.sleep(5)
                continue
            for update in result.get("result", []):
                offset = int(update["update_id"]) + 1
                _save_offset(offset)
                if "message" in update:
                    _handle_message(update["message"])
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.exception("Polling-Loop Fehler: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
