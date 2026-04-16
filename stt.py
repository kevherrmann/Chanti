"""Speech-to-Text via faster-whisper (Lazy Loading)."""
import sounddevice as sd
import numpy as np
import resampy
import logging
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_LANGUAGE

logger = logging.getLogger("chanti")

_model = None

DEVICE_INDEX = 0
NATIVE_SR = 44100
TARGET_SR = 16000


def _get_model():
    """Lazy-Loading: Whisper wird erst beim ersten Aufruf geladen."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info("Lade Whisper STT Modell...")
        _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type="int8")
        logger.info("Whisper STT bereit")
    return _model


def record_until_silence(silence_sec=1.5, threshold=0.02):
    logger.info("Höre zu...")
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
    segments, _ = _get_model().transcribe(audio_np, language=WHISPER_LANGUAGE, beam_size=1)
    return " ".join(s.text for s in segments).strip()
