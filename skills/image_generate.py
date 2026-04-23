"""Skill: Bildgenerierung via Google Gemini 2.5 Flash Image.

Kostenloser API-Tier: bis zu ~500 Bilder/Tag, kein Kreditkartenzwang.
API-Key via Google AI Studio: https://aistudio.google.com/apikey

Flüchtig: Das Bild wird NICHT auf Disk gespeichert, sondern nur im RAM
abgelegt und kann vom Server unter /image/<token> ausgeliefert werden.
"""
import base64
import logging
import secrets
import threading
import time
import uuid

import requests

logger = logging.getLogger("chanti")

try:
    from config import GEMINI_API_KEY
except ImportError:
    GEMINI_API_KEY = ""

GEMINI_MODEL = "gemini-2.5-flash-image"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# Timeout großzügig — Gemini braucht 3–15 Sekunden pro Bild.
REQUEST_TIMEOUT = 60

# In-Memory Store für frisch generierte Bilder.
# Key: token (kurz, websicher). Value: (mime_type, bytes, created_at).
# Gekappt bei MAX_ENTRIES — älteste fliegen raus (flüchtig gewollt).
# Zusätzlich läuft jeder Eintrag nach TTL_SECONDS ab.
_STORE: dict[str, tuple[str, bytes, float]] = {}
_STORE_LOCK = threading.Lock()
MAX_ENTRIES = 20
TTL_SECONDS = 60 * 60  # 1 Stunde


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "image_generate",
        "description": (
            "Erzeugt ein neues Bild aus einer Text-Beschreibung (Text-to-Image). "
            "Nutze das, wenn der User ein Bild gezeichnet/gemalt/generiert haben will, "
            "z.B. 'mal mir einen Drachen' oder 'generiere ein Logo für...'. "
            "NICHT für Bild-Analyse benutzen — dafür kann der User direkt ein Bild hochladen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Detaillierte englische oder deutsche Beschreibung des Bildes. "
                        "Je konkreter (Stil, Beleuchtung, Perspektive, Stimmung), "
                        "desto besser das Ergebnis."
                    ),
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Seitenverhältnis. Einer von: 1:1, 16:9, 9:16, 4:3, 3:4. Default 1:1.",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                },
            },
            "required": ["prompt"],
        },
    },
}


def _prune_store(reserve_slot: bool = False) -> None:
    """Entfernt abgelaufene Einträge und hält die Größe unter MAX_ENTRIES.
    Mit reserve_slot=True wird auf MAX_ENTRIES-1 gekappt, damit nach dem
    direkt folgenden Insert die Grenze noch hält.
    Caller hält _STORE_LOCK."""
    now = time.monotonic()
    expired = [k for k, (_, _, ts) in _STORE.items() if now - ts > TTL_SECONDS]
    for k in expired:
        _STORE.pop(k, None)
    target = MAX_ENTRIES - 1 if reserve_slot else MAX_ENTRIES
    if len(_STORE) <= target:
        return
    # Älteste zuerst entfernen
    ordered = sorted(_STORE.items(), key=lambda kv: kv[1][2])
    overflow = len(_STORE) - target
    for k, _ in ordered[:overflow]:
        _STORE.pop(k, None)


def store_put(mime_type: str, data: bytes) -> str:
    """Legt ein Bild im Store ab und gibt den Token zurück."""
    token = secrets.token_urlsafe(12)
    with _STORE_LOCK:
        _prune_store(reserve_slot=True)
        _STORE[token] = (mime_type, data, time.monotonic())
    return token


def store_get(token: str) -> tuple[str, bytes] | None:
    """Holt ein Bild aus dem Store. None wenn nicht (mehr) vorhanden."""
    with _STORE_LOCK:
        _prune_store()
        entry = _STORE.get(token)
        if entry is None:
            return None
        mime, data, _ = entry
        return mime, data


def execute(prompt: str, aspect_ratio: str = "1:1") -> str:
    if not GEMINI_API_KEY or GEMINI_API_KEY in ("", "DEIN_KEY_HIER"):
        return (
            "Bildgenerierung nicht verfügbar: GEMINI_API_KEY ist nicht gesetzt. "
            "Kevin muss in der .env den Key von https://aistudio.google.com/apikey eintragen."
        )

    if not isinstance(prompt, str) or not prompt.strip():
        return "Bildgenerierung fehlgeschlagen: leerer Prompt."
    prompt = prompt.strip()

    valid_ratios = {"1:1", "16:9", "9:16", "4:3", "3:4"}
    if aspect_ratio not in valid_ratios:
        aspect_ratio = "1:1"

    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            headers={
                "x-goog-api-key": GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        logger.error("Gemini Timeout bei Bildgenerierung")
        return "Bildgenerierung fehlgeschlagen: Timeout (Gemini zu langsam)."
    except requests.exceptions.ConnectionError:
        logger.error("Gemini nicht erreichbar")
        return "Bildgenerierung fehlgeschlagen: Gemini nicht erreichbar."
    except requests.exceptions.RequestException as e:
        logger.error(f"Gemini Netzwerkfehler: {type(e).__name__}: {e}")
        return f"Bildgenerierung fehlgeschlagen: {type(e).__name__}."

    if not resp.ok:
        # Gemini liefert Fehlermeldung meist als JSON mit error.message
        try:
            err_msg = resp.json().get("error", {}).get("message", "")[:200]
        except ValueError:
            err_msg = resp.text[:200]
        logger.warning(f"Gemini HTTP {resp.status_code}: {err_msg}")
        if resp.status_code == 429:
            return "Bildgenerierung fehlgeschlagen: Rate-Limit erreicht (500/Tag)."
        if resp.status_code == 400 and "safety" in err_msg.lower():
            return "Bildgenerierung abgelehnt: Prompt verletzt Safety-Policy."
        return f"Bildgenerierung fehlgeschlagen: HTTP {resp.status_code}."

    try:
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.error(f"Ungültige Gemini-Antwort: {e}")
        return "Bildgenerierung fehlgeschlagen: unerwartete Antwort-Struktur."

    # Erstes inlineData-Part suchen (es kann auch Text-Teile daneben geben)
    inline = None
    for p in parts:
        if isinstance(p, dict) and "inlineData" in p:
            inline = p["inlineData"]
            break
        # Manche Antworten nutzen snake_case
        if isinstance(p, dict) and "inline_data" in p:
            inline = p["inline_data"]
            break
    if inline is None:
        # Häufiger Fall: Modell liefert nur Text-Ablehnung zurück
        text_reasons = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
        reason = " ".join(text_reasons).strip()[:200]
        logger.warning(f"Gemini lieferte kein Bild. Begründung: {reason or '(keine)'}")
        return f"Bildgenerierung fehlgeschlagen: keine Bilddaten zurückgekommen. {reason}".strip()

    mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
    b64 = inline.get("data", "")
    if not b64:
        return "Bildgenerierung fehlgeschlagen: leere Bilddaten."

    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError) as e:
        logger.error(f"Gemini Base64-Dekodierung fehlgeschlagen: {e}")
        return "Bildgenerierung fehlgeschlagen: Bilddaten unlesbar."

    token = store_put(mime, raw)
    logger.info(f"Bild generiert ({len(raw)} Bytes, {mime}) → token={token}")

    # Das ist der Magic-String, den der Server im finalen LLM-Output erkennt
    # und durch eine echte Bild-URL/Markdown-Bildmarkierung ersetzt.
    # Das LLM muss diesen String nur unverändert weiterreichen.
    return (
        f"Bild erfolgreich generiert. "
        f"WICHTIG: Füge GENAU diesen Marker in deine finale Antwort ein, "
        f"damit der User das Bild sieht: [[IMG:{token}]] "
        f"— den Marker exakt so, in eckigen Doppelklammern, unverändert."
    )
