"""Text-to-Speech via XTTS v2 Server."""
import requests
import sounddevice as sd
import soundfile as sf
import numpy as np
import io
import logging
from config import XTTS_URL
from text_utils import add_silence_padding

logger = logging.getLogger("chanti")


def speak(text: str):
    """Spricht Text via XTTS aus. Fängt Fehler ab statt zu crashen."""
    if not text.strip():
        return
    try:
        resp = requests.post(XTTS_URL, data=text.encode("utf-8"), timeout=60)
        resp.raise_for_status()
        audio, sr = sf.read(io.BytesIO(resp.content))
        audio = add_silence_padding(audio, sr, pad_seconds=0.4)
        sd.play(audio, sr, blocking=True)
    except requests.exceptions.ConnectionError:
        logger.warning("XTTS-Server nicht erreichbar – Sprachausgabe übersprungen")
    except requests.exceptions.Timeout:
        logger.warning("XTTS-Server Timeout – Sprachausgabe übersprungen")
    except Exception as e:
        logger.error(f"TTS Fehler: {e}")
