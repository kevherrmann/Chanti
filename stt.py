"""Speech-to-Text via faster-whisper (Lazy Loading)."""
import logging
import numpy as np
import sounddevice as sd
import resampy

from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_LANGUAGE

# Optional aus config: MIC_DEVICE_INDEX. Default 0.
try:
    from config import MIC_DEVICE_INDEX as DEVICE_INDEX
except ImportError:
    DEVICE_INDEX = 0

logger = logging.getLogger("chanti")

_model = None

NATIVE_SR = 44100
TARGET_SR = 16000

# Harte Obergrenze für eine einzelne Aufnahme. Schützt gegen:
# - User spricht nicht → Schleife lief bisher unbegrenzt
# - Mikro liefert konstantes Rauschen → Buffer wuchs ins Unendliche
MAX_RECORD_SEC = 30.0


def _get_model():
    """Lazy-Loading: Whisper wird erst beim ersten Aufruf geladen."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info("Lade Whisper STT Modell…")
        _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type="int8")
        logger.info("Whisper STT bereit")
    return _model


def record_until_silence(silence_sec: float = 1.5, threshold: float = 0.02,
                         max_seconds: float = MAX_RECORD_SEC) -> np.ndarray:
    """Nimmt auf bis für `silence_sec` Stille erkannt wird oder max_seconds erreicht.
    Gibt Audio als 16 kHz-Array zurück."""
    logger.info("Höre zu…")
    chunks: list[np.ndarray] = []
    silent = 0
    speaking = False
    blocksize = 1024
    max_blocks = int(max_seconds * (NATIVE_SR / blocksize))
    blocks_read = 0
    silence_blocks_needed = int(silence_sec * (NATIVE_SR / blocksize))

    with sd.InputStream(samplerate=NATIVE_SR, channels=1, dtype="float32",
                        device=DEVICE_INDEX, blocksize=blocksize) as stream:
        while blocks_read < max_blocks:
            chunk, _ = stream.read(blocksize)
            level = float(np.abs(chunk).mean())
            chunks.append(chunk)
            blocks_read += 1

            if level > threshold:
                speaking = True
                silent = 0
            elif speaking:
                silent += 1
                if silent > silence_blocks_needed:
                    break
        else:
            logger.warning(f"Aufnahme-Timeout nach {max_seconds}s erreicht")

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks).flatten()
    return resampy.resample(audio, NATIVE_SR, TARGET_SR)


def transcribe(audio_np) -> str:
    if audio_np is None or len(audio_np) == 0:
        return ""
    segments, _ = _get_model().transcribe(
        audio_np, language=WHISPER_LANGUAGE, beam_size=1
    )
    return " ".join(s.text for s in segments).strip()
