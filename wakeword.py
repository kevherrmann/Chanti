"""Wake Word Listener für Chanti via Vosk.

Nutzt einen einzigen permanenten Audio-Stream.

Thread-Safety:
- Der sounddevice-Audio-Callback läuft in einem separaten OS-Thread.
- Die State-Variablen (state, record_buffer, ...) werden zwischen diesem
  Thread und der asyncio-Loop geteilt. Wir schützen sie mit _state_lock.
- asyncio.run_coroutine_threadsafe() ist der einzige saubere Weg, vom
  Callback-Thread in die Loop zu springen.
"""
import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np
import requests
import resampy
import sounddevice as sd

logger = logging.getLogger("chanti")

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

try:
    from config import VOSK_MODEL_PATH
except ImportError:
    VOSK_MODEL_PATH = str(Path.home() / "vosk-model-small-de-0.15")

try:
    from config import MIC_DEVICE_INDEX as DEVICE_INDEX
except ImportError:
    DEVICE_INDEX = 0

NATIVE_SR         = 44100
VOSK_SR           = 16000
WAKE_WORDS        = ["chantilly", "hey chantilly", "hei chantilly"]
DEBOUNCE_SEC      = 3.0
SILENCE_SEC       = 2.0
SILENCE_THRESHOLD = 0.025
MIN_WAKE_LEVEL    = 0.04
# Harte Obergrenze für eine einzelne Aufnahme (nach Wake-Word).
# Schützt vor Endlos-Recording bei Dauer-Rauschen/defektem Mikro.
MAX_RECORD_SEC    = 30.0

sys.path.insert(0, str(Path(__file__).parent))
from tts import speak
from text_utils import clean_for_tts
# Whisper aus stt.py wiederverwenden statt zweite Instanz laden
from stt import transcribe as _stt_transcribe, _get_model as _stt_get_model


def notify_server(event: str, data: dict | None = None):
    """Schickt Event an den Chanti-Server."""
    if data is None:
        data = {}
    try:
        requests.post(
            "http://localhost:8000/wakeword",
            json={"event": event, **data},
            timeout=2,
        )
    except requests.exceptions.RequestException as e:
        logger.debug(f"notify_server({event}) fehlgeschlagen: {type(e).__name__}")


def _send_chat(text: str) -> str:
    """Schickt Text an /chat und gibt die Antwort zurück (oder Fehlermeldung)."""
    try:
        resp = requests.post(
            "http://localhost:8000/chat",
            json={"message": text},
            timeout=30,
        )
    except requests.exceptions.Timeout:
        logger.error("Chat-Request Timeout")
        return "Entschuldigung, der Server antwortet nicht."
    except requests.exceptions.ConnectionError:
        logger.error("Chat-Server nicht erreichbar")
        return "Der Chanti-Server ist gerade nicht erreichbar."
    except requests.exceptions.RequestException as e:
        logger.error(f"Chat-Request-Fehler: {type(e).__name__}: {e}")
        return "Ein Netzwerkfehler ist aufgetreten."

    if not resp.ok:
        logger.error(f"Chat-Server HTTP {resp.status_code}: {resp.text[:200]}")
        return "Der Server hat mit einem Fehler geantwortet."

    try:
        return resp.json().get("response", "") or ""
    except ValueError:
        logger.error("Chat-Server lieferte kein JSON")
        return "Die Antwort vom Server war unlesbar."


async def main():
    # Vosk-Model-Pfad prüfen — sonst crasht Model() mit Stack-Trace
    if not Path(VOSK_MODEL_PATH).exists():
        logger.error(f"Vosk-Modell nicht gefunden: {VOSK_MODEL_PATH}")
        return

    logger.info("Lade Vosk Modell…")
    # Import erst hier, damit der ModuleNotFoundError früh klar wird
    from vosk import Model, KaldiRecognizer
    model = Model(VOSK_MODEL_PATH)
    recognizer = KaldiRecognizer(
        model, VOSK_SR,
        '["hey chantilly", "hei chantilly", "chantilly", "[unk]"]',
    )
    recognizer.SetWords(False)

    # Whisper schon mal anwerfen (lazy-loading in stt.py), damit der erste
    # Call nach dem Wake-Word nicht den Ladevorgang auslöst.
    try:
        _stt_get_model()
    except Exception as e:
        logger.warning(f"Whisper konnte nicht vorab geladen werden: {e}")

    loop = asyncio.get_running_loop()

    # ── Shared State (zwischen Audio-Callback-Thread und asyncio-Loop) ──
    _state_lock = threading.Lock()
    state = {"mode": "listening"}           # "listening" | "tts" | "recording" | "processing"
    record_buffer: list[np.ndarray] = []
    silent_blocks = {"n": 0}
    speaking = {"v": False}
    last_detection = {"t": 0.0}
    _vosk_buf = [np.array([], dtype=np.float32)]
    _level_buf = [0.0]
    record_start = {"t": 0.0}
    # Max-Blocks für Recording — wird im Callback anhand frames gesetzt.
    max_record_blocks = {"n": 0}

    def audio_callback(indata, frames, time_info, status):
        # Läuft im sounddevice-Audio-Thread.
        chunk = indata[:, 0].copy()
        current_level = float(np.abs(chunk).mean())

        with _state_lock:
            mode = state["mode"]

        if mode == "listening":
            _vosk_buf[0] = np.concatenate([_vosk_buf[0], chunk])
            _level_buf[0] = max(_level_buf[0], current_level)

            if len(_vosk_buf[0]) < int(NATIVE_SR * 0.5):
                return

            peak_level = _level_buf[0]
            _level_buf[0] = 0.0

            audio_16k = resampy.resample(_vosk_buf[0], NATIVE_SR, VOSK_SR)
            _vosk_buf[0] = np.array([], dtype=np.float32)
            audio_bytes = (audio_16k * 32768).astype(np.int16).tobytes()

            if recognizer.AcceptWaveform(audio_bytes):
                text = json.loads(recognizer.Result()).get("text", "").lower()
            else:
                text = json.loads(recognizer.PartialResult()).get("partial", "").lower()

            if text:
                logger.debug(f"[Vosk] {text} (level: {peak_level:.3f})")

            if any(w in text for w in WAKE_WORDS) and peak_level >= MIN_WAKE_LEVEL:
                now = time.monotonic()
                with _state_lock:
                    if now - last_detection["t"] <= DEBOUNCE_SEC:
                        return
                    if state["mode"] != "listening":
                        return  # Doppel-Race: anderer Callback war schneller
                    last_detection["t"] = now
                    state["mode"] = "tts"
                    recognizer.Reset()
                logger.info(f"Wake Word erkannt! (level: {peak_level:.3f})")
                asyncio.run_coroutine_threadsafe(_activate(), loop)

        elif mode == "recording":
            with _state_lock:
                if not record_buffer:
                    record_start["t"] = time.monotonic()
                    # Max-Blocks = MAX_RECORD_SEC / Blockdauer
                    if frames > 0:
                        max_record_blocks["n"] = int(MAX_RECORD_SEC * NATIVE_SR / frames)
                    else:
                        max_record_blocks["n"] = 0
                record_buffer.append(chunk.copy())

                # Harter Max-Dauer-Check — immer greifen, auch wenn nie Stille
                elapsed = time.monotonic() - record_start["t"]
                too_long = (elapsed >= MAX_RECORD_SEC or
                            (max_record_blocks["n"] > 0 and
                             len(record_buffer) >= max_record_blocks["n"]))

                if current_level > SILENCE_THRESHOLD:
                    speaking["v"] = True
                    silent_blocks["n"] = 0
                elif speaking["v"]:
                    silent_blocks["n"] += 1

                blocks_per_sec = NATIVE_SR / max(frames, 1)
                silence_reached = (speaking["v"] and
                                   silent_blocks["n"] > SILENCE_SEC * blocks_per_sec)

                if silence_reached or too_long:
                    if too_long and not silence_reached:
                        logger.warning(f"Recording-Timeout nach {elapsed:.1f}s")
                    if not record_buffer:
                        return
                    state["mode"] = "processing"
                    audio_data = np.concatenate(record_buffer).flatten()
                    record_buffer.clear()
                    silent_blocks["n"] = 0
                    speaking["v"] = False
                    asyncio.run_coroutine_threadsafe(_process(audio_data), loop)

        # mode in ("tts", "processing"): Audio ignorieren, aber Callback muss durchlaufen

    async def _activate():
        notify_server("listening")
        try:
            await loop.run_in_executor(None, speak, "Ja?")
        except Exception as e:
            logger.warning(f"TTS-Activation fehlgeschlagen: {e}")
        await asyncio.sleep(0.3)
        with _state_lock:
            state["mode"] = "recording"
        logger.info("Höre zu…")

    async def _process(audio_native: np.ndarray):
        try:
            notify_server("processing")
            audio_16k = resampy.resample(audio_native, NATIVE_SR, VOSK_SR)
            if len(audio_16k) < VOSK_SR * 0.3:
                notify_server("idle")
                await loop.run_in_executor(None, speak, "Ich habe dich nicht verstanden.")
                return

            text = await loop.run_in_executor(None, _stt_transcribe, audio_16k)
            if not text or not text.strip():
                notify_server("idle")
                await loop.run_in_executor(None, speak, "Ich habe dich nicht verstanden.")
                return

            logger.info(f"Erkannt: {text}")
            notify_server("transcript", {"text": text})

            response = await loop.run_in_executor(None, _send_chat, text)
            logger.info(f"Antwort: {response}")
            notify_server("responding", {"text": response})
            if response.strip():
                await loop.run_in_executor(None, speak, clean_for_tts(response))

        except Exception as e:
            logger.error(f"Wakeword-Prozess-Fehler: {e}", exc_info=True)
            try:
                await loop.run_in_executor(None, speak, "Es gab einen Fehler.")
            except Exception:
                pass
        finally:
            notify_server("idle")
            with _state_lock:
                state["mode"] = "listening"

    logger.info("Wake Word Listener aktiv – sage 'Hey Chanti'")

    with sd.InputStream(
        samplerate=NATIVE_SR,
        channels=1,
        dtype="float32",
        device=DEVICE_INDEX,
        blocksize=int(NATIVE_SR * 0.1),
        callback=audio_callback,
    ):
        try:
            while True:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.info("Wake Word Listener beendet")
            raise


if __name__ == "__main__":
    from logging_setup import setup_logging
    setup_logging(log_file="wakeword.log")
    asyncio.run(main())
