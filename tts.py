"""Text-to-Speech via XTTS v2 Server."""
import io
import logging

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf

from config import XTTS_URL
from text_utils import add_silence_padding

logger = logging.getLogger("chanti")

# Obergrenzen — XTTS hängt sich bei sehr langen Texten oder extremen Antworten auf.
MAX_TEXT_CHARS = 2000
MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20 MB reicht für jede realistische Ansage


def speak(text: str):
    """Spricht Text via XTTS aus. Fängt Fehler ab statt zu crashen."""
    if not isinstance(text, str) or not text.strip():
        return

    # Sehr lange Texte abschneiden — sonst hängt XTTS.
    if len(text) > MAX_TEXT_CHARS:
        logger.info(f"TTS-Text gekürzt von {len(text)} auf {MAX_TEXT_CHARS}")
        text = text[:MAX_TEXT_CHARS]

    try:
        resp = requests.post(
            XTTS_URL,
            data=text.encode("utf-8"),
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        logger.warning("XTTS-Server nicht erreichbar – Sprachausgabe übersprungen")
        return
    except requests.exceptions.Timeout:
        logger.warning("XTTS-Server Timeout – Sprachausgabe übersprungen")
        return
    except requests.exceptions.RequestException as e:
        logger.warning(f"XTTS-Request-Fehler: {type(e).__name__}: {e}")
        return

    if not resp.ok:
        logger.warning(f"XTTS-Server antwortete HTTP {resp.status_code}")
        return
    if not resp.content:
        logger.warning("XTTS-Server lieferte leere Antwort")
        return
    if len(resp.content) > MAX_AUDIO_BYTES:
        logger.warning(f"XTTS-Antwort zu groß ({len(resp.content)} Bytes) – übersprungen")
        return

    try:
        audio, sr = sf.read(io.BytesIO(resp.content))
    except Exception as e:
        logger.warning(f"XTTS-Audio nicht lesbar: {type(e).__name__}: {e}")
        return

    if audio is None or len(audio) == 0:
        return

    try:
        audio = add_silence_padding(audio, sr, pad_seconds=0.4)
    except Exception as e:
        logger.debug(f"Padding fehlgeschlagen, spiele ohne Padding: {e}")

    try:
        sd.play(audio, sr, blocking=True)
    except Exception as e:
        # sd.play kann bei fehlendem Output-Device oder fehlerhaftem Array crashen.
        logger.error(f"TTS-Wiedergabe fehlgeschlagen: {type(e).__name__}: {e}")
