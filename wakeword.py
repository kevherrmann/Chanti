"""
Wake Word Listener für Chanti via Vosk.
Nutzt einen einzigen permanenten Stream für alles.
"""
import asyncio
import json
import numpy as np
import sounddevice as sd
import resampy
import requests
import sys
import time
from pathlib import Path
from vosk import Model, KaldiRecognizer

VOSK_MODEL        = "/run/media/z0mb1/58BCF437BCF4116C/vosk-model-small-de-0.15"
DEVICE_INDEX      = 0
NATIVE_SR         = 44100
VOSK_SR           = 16000
WAKE_WORDS        = ["chantilly", "hey chantilly", "hei chantilly"]
DEBOUNCE_SEC      = 3.0   # Längere Pause zwischen Erkennungen
SILENCE_SEC       = 2.0
SILENCE_THRESHOLD = 0.025  # Höher = weniger sensitiv
MIN_WAKE_LEVEL    = 0.04   # Mindest-Lautstärke beim Wake Word

sys.path.insert(0, str(Path(__file__).parent))
from tts import speak
from text_utils import clean_for_tts


def transcribe_audio(audio: np.ndarray) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, language="de", beam_size=1)
    return " ".join(s.text for s in segments).strip()


def notify_server(event: str, data: dict = {}):
    """Schickt Event an den Chanti-Server."""
    try:
        requests.post(
            f"http://localhost:8000/wakeword",
            json={"event": event, **data},
            timeout=2
        )
    except Exception:
        pass


async def main():
    print("[Chanti] Lade Vosk Modell...")
    model = Model(VOSK_MODEL)
    recognizer = KaldiRecognizer(model, VOSK_SR,
        '["hey chantilly", "hei chantilly", "chantilly", "[unk]"]')
    recognizer.SetWords(False)

    loop = asyncio.get_event_loop()
    last_detection = 0.0

    state = "listening"
    record_buffer = []
    silent_blocks = 0
    speaking = False
    _vosk_buf = [np.array([], dtype=np.float32)]
    _level_buf = [0.0]  # Durchschnittlicher Level im aktuellen Buffer

    def audio_callback_fixed(indata, frames, time_info, status):
        nonlocal state, record_buffer, silent_blocks, speaking, last_detection

        chunk = indata[:, 0].copy()
        current_level = float(np.abs(chunk).mean())

        if state == "listening":
            _vosk_buf[0] = np.concatenate([_vosk_buf[0], chunk])
            _level_buf[0] = max(_level_buf[0], current_level)

            if len(_vosk_buf[0]) >= int(NATIVE_SR * 0.5):
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
                    print(f"[Vosk] {text} (level: {peak_level:.3f})")

                # Nur auslösen wenn Lautstärke hoch genug
                if any(w in text for w in WAKE_WORDS) and peak_level >= MIN_WAKE_LEVEL:
                    now = time.time()
                    if now - last_detection > DEBOUNCE_SEC:
                        last_detection = now
                        print(f"[Chanti] Wake Word erkannt! (level: {peak_level:.3f})")
                        state = "tts"
                        recognizer.Reset()
                        asyncio.run_coroutine_threadsafe(_activate(), loop)

        elif state == "recording":
            record_buffer.append(chunk.copy())

            if current_level > SILENCE_THRESHOLD:
                speaking = True
                silent_blocks = 0
            elif speaking:
                silent_blocks += 1
                blocks_per_sec = NATIVE_SR / frames
                if silent_blocks > SILENCE_SEC * blocks_per_sec:
                    state = "processing"
                    audio_data = np.concatenate(record_buffer).flatten()
                    record_buffer.clear()
                    silent_blocks = 0
                    speaking = False
                    asyncio.run_coroutine_threadsafe(_process(audio_data), loop)

    async def _activate():
        nonlocal state
        notify_server("listening")
        speak("Ja?")
        await asyncio.sleep(0.5)
        state = "recording"
        print("[Chanti] Höre zu...")

    async def _process(audio_native: np.ndarray):
        nonlocal state
        try:
            notify_server("processing")
            audio_16k = resampy.resample(audio_native, NATIVE_SR, VOSK_SR)
            if len(audio_16k) < VOSK_SR * 0.3:
                notify_server("idle")
                speak("Ich habe dich nicht verstanden.")
                return

            text = await loop.run_in_executor(None, transcribe_audio, audio_16k)
            if not text.strip():
                notify_server("idle")
                speak("Ich habe dich nicht verstanden.")
                return

            print(f"[Chanti] Erkannt: {text}")
            notify_server("transcript", {"text": text})

            resp = requests.post(
                "http://localhost:8000/chat",
                json={"message": text},
                timeout=30
            )
            response = resp.json().get("response", "")
            print(f"[Chanti] Antwort: {response}")
            notify_server("responding", {"text": response})
            speak(clean_for_tts(response))

        except Exception as e:
            print(f"[Chanti] Fehler: {e}")
            speak("Es gab einen Fehler.")
        finally:
            notify_server("idle")
            state = "listening"

    print("[Chanti] Wake Word Listener aktiv – sage 'Hey Chanti'")

    with sd.InputStream(
        samplerate=NATIVE_SR,
        channels=1,
        dtype="float32",
        device=DEVICE_INDEX,
        blocksize=int(NATIVE_SR * 0.1),
        callback=audio_callback_fixed
    ):
        while True:
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
