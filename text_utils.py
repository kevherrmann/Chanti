"""Text-Bereinigung für TTS und Audio-Hilfsfunktionen."""
import re
import numpy as np


def clean_for_tts(text: str) -> str:
    """Bereinigt Text für Sprachausgabe. Behält Satzzeichen für natürliche Pausen."""
    # Markdown entfernen
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#+\s?', '', text)
    text = re.sub(r'`+', '', text)
    # URLs entfernen
    text = re.sub(r'https?://\S+', '', text)
    # Sonderzeichen durch Leerzeichen (aber Punkte, Kommas, !? behalten)
    text = re.sub(r'[:\-–—|/\\]', ' ', text)
    # Nur Auslassungspunkte entfernen (... → Leerzeichen)
    text = re.sub(r'\.{2,}', ' ', text)
    # Alles außer Buchstaben, Zahlen, Satzzeichen
    text = re.sub(r'[^\w\s.,!?äöüÄÖÜß]', '', text)
    # Mehrfache Leerzeichen
    text = re.sub(r' +', ' ', text)
    return text.strip()


def add_silence_padding(audio: np.ndarray, sr: int, pad_seconds: float = 0.4) -> np.ndarray:
    """Fügt Stille am Anfang hinzu um abrupten Start zu vermeiden."""
    silence = np.zeros(int(sr * pad_seconds), dtype=audio.dtype)
    return np.concatenate([silence, audio])
