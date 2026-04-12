import requests
import sounddevice as sd
import soundfile as sf
import numpy as np
import io
from config import XTTS_URL
from text_utils import add_silence_padding

def speak(text: str):
    resp = requests.post(XTTS_URL, data=text.encode("utf-8"), timeout=60)
    audio, sr = sf.read(io.BytesIO(resp.content))
    audio = add_silence_padding(audio, sr, pad_seconds=0.4)
    sd.play(audio, sr, blocking=True)
