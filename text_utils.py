"""Text-Bereinigung für TTS und Audio-Hilfsfunktionen."""
import re
import numpy as np

# Max Silence-Padding (Sekunden). Begrenzt, damit niemand 100s Stille anhängt.
_MAX_PAD_SEC = 2.0


def clean_for_tts(text: str) -> str:
    """Bereinigt Text für Sprachausgabe. Behält Satzzeichen für natürliche Pausen."""
    # Markdown entfernen
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"#+\s?", "", text)
    text = re.sub(r"`+", "", text)
    # URLs entfernen
    text = re.sub(r"https?://\S+", "", text)
    # Häufige Zeichen in Worte überführen BEVOR wir Sonderzeichen strippen
    text = text.replace("%", " Prozent ")
    text = text.replace("&", " und ")
    text = text.replace("€", " Euro ")
    text = text.replace("$", " Dollar ")
    # Dashes/Pipes/Slashes → Leerzeichen
    text = re.sub(r"[:\-–—|/\\]", " ", text)
    # Auslassungspunkte → Pause (Leerzeichen)
    text = re.sub(r"\.{2,}", " ", text)
    # Rest: nur Buchstaben, Zahlen, normale Satzzeichen, Umlaute, ß
    text = re.sub(r"[^\w\s.,!?äöüÄÖÜß]", "", text)
    # Mehrfache Leerzeichen zusammenziehen
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def add_silence_padding(audio: np.ndarray, sr: int,
                        pad_seconds: float = 0.4) -> np.ndarray:
    """Fügt Stille am Anfang hinzu um abrupten Start zu vermeiden.
    Begrenzt auf _MAX_PAD_SEC damit nicht versehentlich minutenlang gepaddet wird."""
    pad_seconds = max(0.0, min(float(pad_seconds), _MAX_PAD_SEC))
    if pad_seconds <= 0 or sr <= 0:
        return audio
    silence = np.zeros(int(sr * pad_seconds), dtype=audio.dtype)
    return np.concatenate([silence, audio])
