import re
import numpy as np

def clean_for_tts(text: str) -> str:
    # Markdown entfernen
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#+\s?', '', text)
    text = re.sub(r'`+', '', text)
    # URLs entfernen
    text = re.sub(r'https?://\S+', '', text)
    # Satzzeichen bereinigen
    text = re.sub(r'[:\-–—|/\\]', ' ', text)
    # Punkte nur entfernen wenn am Satzende oder nach Abkürzungen
    text = re.sub(r'\.{2,}', ' ', text)  # ... entfernen
    text = re.sub(r'(?<=[a-zäöüA-ZÄÖÜ])\. ', ' ', text)  # Satzende-Punkte durch Leerzeichen
    text = re.sub(r'\.$', '', text)  # letzter Punkt
    # Alles außer Buchstaben, Zahlen, Komma, Ausrufezeichen, Fragezeichen
    text = re.sub(r'[^\w\s,!?äöüÄÖÜß]', '', text)
    # Mehrfache Leerzeichen
    text = re.sub(r' +', ' ', text)
    return text.strip()

def add_silence_padding(audio: np.ndarray, sr: int, pad_seconds: float = 0.4) -> np.ndarray:
    silence = np.zeros(int(sr * pad_seconds), dtype=audio.dtype)
    return np.concatenate([silence, audio])
