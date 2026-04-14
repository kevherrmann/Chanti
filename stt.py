from faster_whisper import WhisperModel
import sounddevice as sd
import numpy as np
import resampy
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_LANGUAGE

model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type="int8")

DEVICE_INDEX = 0
NATIVE_SR = 44100
TARGET_SR = 16000

def record_until_silence(silence_sec=1.5, threshold=0.02):
    print("🎤 Ich höre...")
    chunks, silent, speaking = [], 0, False
    blocksize = 1024
    with sd.InputStream(samplerate=NATIVE_SR, channels=1, dtype="float32",
                        device=DEVICE_INDEX, blocksize=blocksize) as stream:
        while True:
            chunk, _ = stream.read(blocksize)
            level = np.abs(chunk).mean()
            chunks.append(chunk)

            if level > threshold:
                speaking = True
                silent = 0
            elif speaking:
                silent += 1
                if silent > silence_sec * (NATIVE_SR / blocksize):
                    break

    audio = np.concatenate(chunks).flatten()
    return resampy.resample(audio, NATIVE_SR, TARGET_SR)

def transcribe(audio_np) -> str:
    segments, _ = model.transcribe(audio_np, language=WHISPER_LANGUAGE, beam_size=1)
    return " ".join(s.text for s in segments).strip()
