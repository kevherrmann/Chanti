from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse
from llm import chat as llm_chat
from tts import speak
from memory import (load_system_prompt, load_recent_context, log_conversation,
                    parse_and_execute_commands, cleanup_old_logs)
from skills_loader import load_skills, reload_if_changed, get_tools, get_executors
from text_utils import clean_for_tts
import asyncio
import base64
import subprocess
import os
import json
import logging
import traceback

logger = logging.getLogger("chanti")

# --- Optionaler API-Key Schutz für HTTP-Endpoints ---
_API_KEY = os.environ.get("CHANTI_API_KEY", "")


def _check_auth(request: Request):
    """Prüft API-Key wenn CHANTI_API_KEY gesetzt ist."""
    if not _API_KEY:
        return
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if token != _API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# --- Whisper Lazy-Loading ---
_whisper = None


def get_whisper():
    global _whisper
    if _whisper is None:
        logger.info("Lade Whisper...")
        from faster_whisper import WhisperModel as _WhisperModel
        _whisper = _WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("Whisper bereit")
    return _whisper


def _load_chat_html():
    from pathlib import Path
    return (Path(__file__).parent / "chat.html").read_text(encoding="utf-8")


app = FastAPI()
active_connections: list[WebSocket] = []

soul = load_system_prompt()
logger.info(f"System-Prompt geladen ({len(soul)} Zeichen)")

load_skills()
cleanup_old_logs(keep_days=30)


async def broadcast_notify(message: str):
    for ws in active_connections:
        try:
            await ws.send_json({"type": "message", "text": message})
        except Exception:
            pass
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: speak(clean_for_tts(message))
    )


@app.get("/")
async def index():
    return HTMLResponse(_load_chat_html())


@app.post("/chat")
async def chat_endpoint(request: Request):
    _check_auth(request)
    data = await request.json()
    text = data.get("message", "")
    if not text:
        return {"response": "Keine Nachricht erhalten."}
    reload_if_changed()
    history = [{"role": "system", "content": soul}]
    history.extend(load_recent_context(n=5))
    history.append({"role": "user", "content": text})
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None, lambda: llm_chat(history, tools=get_tools(), executors=get_executors())
    )
    clean_response = parse_and_execute_commands(response)
    log_conversation(text, clean_response)
    return {"response": clean_response}


@app.post("/notify")
async def notify(request: Request):
    _check_auth(request)
    data = await request.json()
    message = data.get("message", "")
    if message:
        asyncio.create_task(broadcast_notify(message))
    return {"ok": True}


@app.post("/wakeword")
async def wakeword_event(request: Request):
    data = await request.json()
    event = data.get("event", "idle")
    for ws in active_connections:
        try:
            await ws.send_json({"type": "wakeword", "event": event, **data})
        except Exception:
            pass
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)

    history = [{"role": "system", "content": soul}]
    history.extend(load_recent_context(n=3))
    logger.info(f"Session gestartet, {len(history)-1} Kontext-Messages geladen")

    async def process_text(text: str, use_tts: bool = False):
        reload_if_changed()
        history.append({"role": "user", "content": text})

        loop = asyncio.get_running_loop()
        response_raw = await loop.run_in_executor(
            None, lambda: llm_chat(history, tools=get_tools(), executors=get_executors())
        )

        response = parse_and_execute_commands(response_raw)
        log_conversation(text, response)
        history.append({"role": "assistant", "content": response})

        if len(history) > 21:
            history[1:] = history[-20:]

        await websocket.send_json({"type": "message", "text": response})
        if use_tts:
            await loop.run_in_executor(
                None, lambda: speak(clean_for_tts(response))
            )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except Exception as e:
                logger.warning(f"JSON Fehler: {e}")
                continue

            logger.debug(f"Empfangen: type={data.get('type')}")

            if data.get('type') == 'text':
                try:
                    await process_text(data['text'], use_tts=False)
                except Exception as e:
                    logger.error(f"FEHLER in process_text: {e}", exc_info=True)
                    try:
                        await websocket.send_json({"type": "message", "text": f"Interner Fehler: {e}"})
                    except Exception:
                        pass

            elif data.get('type') == 'audio':
                try:
                    import tempfile
                    audio_bytes = base64.b64decode(data['data'])
                    with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
                        f.write(audio_bytes)
                        tmp_path = f.name
                    wav_path = tmp_path.replace('.webm', '.wav')
                    try:
                        # Fix #3: subprocess.run statt os.system
                        subprocess.run(
                            ['ffmpeg', '-i', tmp_path, '-ar', '16000', '-ac', '1',
                             wav_path, '-y', '-loglevel', 'quiet'],
                            check=True, timeout=30
                        )
                        segments, _ = get_whisper().transcribe(wav_path, language="de", beam_size=1)
                        text = " ".join(s.text for s in segments).strip()
                    finally:
                        os.unlink(tmp_path)
                        if os.path.exists(wav_path):
                            os.unlink(wav_path)
                    if text:
                        await websocket.send_json({"type": "transcript", "text": text})
                        await process_text(text, use_tts=True)
                    else:
                        await websocket.send_json({"type": "message", "text": "Ich habe dich nicht verstanden, Kevin."})
                except Exception as e:
                    logger.error(f"FEHLER bei Audio: {e}", exc_info=True)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"KRITISCHER FEHLER: {e}", exc_info=True)
    finally:
        if websocket in active_connections:
            active_connections.remove(websocket)
