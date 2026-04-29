# Zentrales Logging ganz zuerst konfigurieren, BEVOR irgendein anderes
# Chanti-Modul oder FastAPI/uvicorn sich am 'chanti'-Logger bedient.
from logging_setup import setup_logging
setup_logging()

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from llm import chat as llm_chat
try:
    from tts import speak
except (OSError, ImportError) as e:
    import logging
    logging.getLogger("chanti").warning(f"TTS nicht verfügbar: {e}. Chanti läuft ohne Sprachausgabe.")
    def speak(*args, **kwargs):
        return None
from memory import (SOUL_FILE, USER_FILE, MEMORY_FILE,
                    load_system_prompt, load_recent_context, log_conversation,
                    parse_and_execute_commands, cleanup_old_logs)
from skills_loader import load_skills, reload_if_changed, get_tools, get_executors
from text_utils import clean_for_tts
# NEU ▼ Kalender
import calendar_core
from calendar_startup import reminder_startup_task
# NEU ▲
# NEU ▼ Leads
import leads_core
import leads_db
from leads_analyzer import website as _leads_website
# NEU ▼ Game-Bridge (Chanti-Welt)
import game_bridge_http
import game_diary
import game_brain
from telegram_notify import send_telegram
# NEU ▲
from pathlib import Path as _LeadPath
# NEU ▲
import asyncio
import base64
import subprocess
import os
import json
import logging
import threading
import time
from collections import deque

logger = logging.getLogger("chanti")

# ---------------------------------------------------------------------------
# Konfiguration & State
# ---------------------------------------------------------------------------

_API_KEY = os.environ.get("CHANTI_API_KEY", "")

# Hot-Reload: statt bei jedem Request stat() zu machen, läuft ein
# Background-Task alle 5 Sekunden durch die Skill-Files.
_HOT_RELOAD_INTERVAL = 5.0

# Einfaches In-Memory Rate-Limit für /chat und /notify — schützt vor
# versehentlichen Endlosschleifen (z.B. n8n-Workflow-Bug, Telegram-Bot spammt).
_RATE_LIMIT_MAX = 30        # max Requests
_RATE_LIMIT_WINDOW = 60.0   # pro 60 Sekunden
_rate_buckets: dict[str, deque] = {}
_rate_lock = threading.Lock()

# System-Prompt wird bei Änderung von SOUL/USER/MEMORY live neu geladen.
_prompt_lock = threading.Lock()
_prompt_mtimes: dict[str, float] = {}
_current_prompt: str = ""

# Wakeword und Screenshot-Basispfad (Leads)
_LEADS_SCREENSHOTS_BASE = (_LeadPath.home() / "chanti" / "data" / "screenshots").resolve()


def _check_auth(request: Request):
    if not _API_KEY:
        return
    # n8n läuft auf demselben Server und ruft via 127.0.0.1 auf → keine Auth nötig
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return
    # Browser-Cookie ODER Bearer-Token akzeptieren
    cookie_token = request.cookies.get("chanti_auth", "")
    header_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if cookie_token == _API_KEY or header_token == _API_KEY:
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _rate_limit(key: str):
    """Rolling-Window-Rate-Limit. Wirft 429 bei Überschreitung."""
    now = time.monotonic()
    with _rate_lock:
        q = _rate_buckets.setdefault(key, deque())
        # alte Einträge rauswerfen
        while q and q[0] < now - _RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= _RATE_LIMIT_MAX:
            retry = _RATE_LIMIT_WINDOW - (now - q[0])
            raise HTTPException(
                status_code=429,
                detail=f"Zu viele Anfragen, bitte {retry:.0f}s warten.",
                headers={"Retry-After": str(max(1, int(retry)))},
            )
        q.append(now)


# ---------------------------------------------------------------------------
# System-Prompt Live-Reload
# ---------------------------------------------------------------------------

def _prompt_files_mtimes() -> dict[str, float]:
    out = {}
    for p in (SOUL_FILE, USER_FILE, MEMORY_FILE):
        try:
            out[str(p)] = p.stat().st_mtime if p.exists() else 0.0
        except OSError:
            out[str(p)] = 0.0
    return out


def _refresh_prompt_if_changed() -> str:
    """Prüft ob SOUL/USER/MEMORY geändert wurden und lädt den Prompt neu.
    Gibt den aktuellen Prompt zurück (thread-safe)."""
    global _current_prompt, _prompt_mtimes
    current = _prompt_files_mtimes()
    with _prompt_lock:
        if current != _prompt_mtimes or not _current_prompt:
            _current_prompt = load_system_prompt()
            _prompt_mtimes = current
            logger.info(f"System-Prompt neu geladen ({len(_current_prompt)} Zeichen)")
        return _current_prompt


# ---------------------------------------------------------------------------
# Whisper Lazy-Loading
# ---------------------------------------------------------------------------

_whisper = None
_whisper_lock = threading.Lock()


def get_whisper():
    global _whisper
    if _whisper is None:
        with _whisper_lock:
            if _whisper is None:
                logger.info("Lade Whisper…")
                from faster_whisper import WhisperModel as _WhisperModel
                _whisper = _WhisperModel("base", device="cpu", compute_type="int8")
                logger.info("Whisper bereit")
    return _whisper


def _load_chat_html():
    from pathlib import Path
    return (Path(__file__).parent / "chat.html").read_text(encoding="utf-8")


def _trim_image_messages(history: list[dict], keep_last: int = 4) -> None:
    """Base64-Bilder aus älteren User-Messages entfernen, damit das
    Context-Fenster nicht explodiert. Die letzten `keep_last` Multimodal-
    Messages bleiben unangetastet — die davor werden in reinen Text
    umgewandelt (Platzhalter '[früheres Bild]').
    """
    # Indizes aller Multimodal-User-Messages sammeln
    idxs = [
        i for i, m in enumerate(history)
        if m.get("role") == "user" and isinstance(m.get("content"), list)
    ]
    if len(idxs) <= keep_last:
        return
    to_trim = idxs[:-keep_last]
    for i in to_trim:
        parts = history[i]["content"]
        text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
        text = " ".join(t for t in text_parts if t).strip()
        history[i]["content"] = (
            f"[früheres Bild entfernt] {text}".strip()
            if text else "[früheres Bild entfernt]"
        )


# ---------------------------------------------------------------------------
# FastAPI Lifespan (ersetzt @app.on_event)
# ---------------------------------------------------------------------------

active_connections: list[WebSocket] = []
_active_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    _refresh_prompt_if_changed()
    load_skills()
    cleanup_old_logs(keep_days=30)
    leads_db.init_db()
    # Game-Session-End-Handler: wenn der Spieler disconnected, erzeuge
    # Tagebuch-Eintrag und sende Telegram-Zusammenfassung.
    async def _on_game_session_end(report: dict):
        dur = report.get("duration_seconds", 0.0)
        logger.info(f"Game-Session beendet — Dauer {dur:.0f}s, "
                    f"deaktiviere Brain + schreibe Tagebuch…")
        try:
            await game_brain.on_session_end(report)
        except Exception as e:
            logger.error(f"Brain-Deaktivierung fehlgeschlagen: {e}",
                         exc_info=True)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, game_diary.generate_and_store, report
            )
        except Exception as e:
            logger.error(f"Tagebuch-Generierung fehlgeschlagen: {e}",
                         exc_info=True)
            return

        summary = result.get("summary") or "Session beendet."
        dur_min = dur / 60.0
        tg_text = f"Chanti-Welt · {dur_min:.1f} min\n{summary}"
        sent = await loop.run_in_executor(None, send_telegram, tg_text)
        if sent:
            logger.info("Session-Ende-Telegram gesendet")
        else:
            logger.warning("Session-Ende-Telegram NICHT gesendet")


    # Brain mit HTTP-Bridge (Luanti) verdrahten
    game_brain.configure(send_to_game_callable=game_bridge_http.send_to_game)
    game_bridge_http.register_session_end_handler(_on_game_session_end)
    game_bridge_http.register_session_start_handler(game_brain.on_session_start)
    game_bridge_http.register_plan_result_handler(game_brain.on_plan_result)
    game_bridge_http.start_watchdog()
    # Kalender-Reminder-Task
    rem_task = asyncio.create_task(reminder_startup_task())
    logger.info("Kalender-Reminder-Task eingeplant")

    # Daily-Pulse-Task: täglicher proaktiver Check (Kalender, Inaktivität, News)
    from daily_pulse import daily_pulse_task
    pulse_task = asyncio.create_task(daily_pulse_task())
    logger.info("Daily-Pulse-Task eingeplant")

    # Screenshot-Cleanup einmalig
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, _leads_website.cleanup_old_screenshots, 30
        )
    except Exception as e:
        logger.warning(f"Screenshot-Cleanup Fehler: {e}")

    # Hot-Reload als Background-Task
    async def _hot_reload_loop():
        while True:
            try:
                await asyncio.sleep(_HOT_RELOAD_INTERVAL)
                await asyncio.get_running_loop().run_in_executor(None, reload_if_changed)
                # Prompt-Refresh ist billig (3 stat()-Calls)
                await asyncio.get_running_loop().run_in_executor(
                    None, _refresh_prompt_if_changed
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Hot-Reload Fehler: {e}")

    hot_task = asyncio.create_task(_hot_reload_loop())

    try:
        yield
    finally:
        # SHUTDOWN
        hot_task.cancel()
        rem_task.cancel()
        pulse_task.cancel()
        for t in (hot_task, rem_task, pulse_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(lifespan=lifespan)
app.include_router(game_bridge_http.router)

# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

async def broadcast_notify(message: str):
    # Kopie der Connection-Liste ziehen, damit parallele Disconnects
    # die Iteration nicht sprengen.
    with _active_lock:
        targets = list(active_connections)
    for ws in targets:
        try:
            await ws.send_json({"type": "message", "text": message})
        except Exception:
            pass
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: speak(clean_for_tts(message)))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_LOGIN_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Chanti Login</title>
<style>
body{background:#0a0a0a;color:#eee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
form{background:#1a1a1a;padding:2rem;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.5)}
input{width:100%;padding:.7rem;margin:.5rem 0;background:#2a2a2a;border:1px solid #444;color:#eee;border-radius:4px;box-sizing:border-box}
button{width:100%;padding:.7rem;background:#4a90e2;color:#fff;border:0;border-radius:4px;cursor:pointer;font-size:1rem}
button:hover{background:#3a7bc8}
.err{color:#ff6b6b;margin-top:.5rem;font-size:.9rem}
</style></head>
<body><form method="POST" action="/login">
<h2 style="margin-top:0">Chanti</h2>
<input type="password" name="password" placeholder="Passwort" autofocus required>
<button type="submit">Einloggen</button>
{err}
</form></body></html>"""


@app.get("/")
async def index(request: Request):
    if not _API_KEY:
        return HTMLResponse(_load_chat_html())
    if request.cookies.get("chanti_auth", "") == _API_KEY:
        return HTMLResponse(_load_chat_html())
    return HTMLResponse(_LOGIN_PAGE.replace("{err}", ""))


@app.post("/login")
async def login(request: Request):
    from fastapi.responses import RedirectResponse
    form = await request.form()
    password = form.get("password", "")
    if password == _API_KEY and _API_KEY:
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(
            "chanti_auth", _API_KEY,
            max_age=60*60*24*30,   # 30 Tage
            httponly=True,
            samesite="lax",
        )
        return resp
    return HTMLResponse(_LOGIN_PAGE.replace("{err}", '<div class="err">Falsches Passwort</div>'), status_code=401)


@app.get("/logout")
async def logout():
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("chanti_auth")
    return resp


@app.post("/chat")
async def chat_endpoint(request: Request):
    _check_auth(request)
    _rate_limit(f"chat:{request.client.host if request.client else 'unknown'}")
    data = await request.json()
    text = data.get("message", "")
    if not text:
        return {"response": "Keine Nachricht erhalten."}

    soul = _refresh_prompt_if_changed()
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
    _rate_limit(f"notify:{request.client.host if request.client else 'unknown'}")
    data = await request.json()
    message = data.get("message", "")
    if message:
        asyncio.create_task(broadcast_notify(message))
    return {"ok": True}


# ── Kalender-REST ────────────────────────────────────────────────────────────

@app.get("/calendar/events")
async def calendar_list(request: Request):
    _check_auth(request)
    return {"events": calendar_core.list_all_sorted()}


@app.post("/calendar/events")
async def calendar_create(request: Request):
    _check_auth(request)
    data = await request.json()
    try:
        event = calendar_core.add_event(
            title=data.get("title", ""),
            date_iso=data.get("date", ""),
            time_hm=data.get("time") or None,
            recurring=data.get("recurring") or None,
        )
        return {"ok": True, "event": event}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/calendar/events/{event_id}")
async def calendar_delete(event_id: str, request: Request):
    _check_auth(request)
    ok = calendar_core.delete_event(event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Event nicht gefunden")
    return {"ok": True}


# ── Leads-UI + REST ─────────────────────────────────────────────────────────

@app.get("/leads")
async def leads_ui():
    path = _LeadPath(__file__).parent / "leads.html"
    return HTMLResponse(path.read_text(encoding="utf-8"))


def _run_in_thread(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, lambda: func(*args, **kwargs))


@app.get("/leads/stats")
async def leads_stats(request: Request):
    _check_auth(request)
    return leads_db.count_by_status()


@app.get("/leads/runs")
async def leads_runs(request: Request):
    _check_auth(request)
    return {"runs": leads_db.list_runs(limit=50)}


@app.post("/leads/search")
async def leads_search(request: Request):
    _check_auth(request)
    data = await request.json()
    branche = (data.get("branche") or "").strip()
    ort = (data.get("ort") or "").strip()
    try:
        count = int(data.get("count") or 10)
        radius_km = int(data.get("radius_km") or 15)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="count/radius_km muss Zahl sein")
    if not branche or not ort:
        raise HTTPException(status_code=400, detail="branche und ort sind pflicht")
    count = max(1, min(count, 50))
    radius_km = max(1, min(radius_km, 200))
    try:
        result = await _run_in_thread(leads_core.run_search, branche, ort, count, radius_km)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:300])


@app.get("/leads/companies")
async def leads_list(request: Request, status: str = "all",
                     min_score: float = 0, search: str = ""):
    _check_auth(request)
    return {"companies": leads_db.list_companies(
        status=status, min_score=min_score if min_score > 0 else None,
        search=search or None,
    )}


@app.get("/leads/companies/{company_id}")
async def leads_get(company_id: int, request: Request):
    _check_auth(request)
    full = leads_db.get_company_full(company_id)
    if not full:
        raise HTTPException(status_code=404, detail="Firma nicht gefunden")
    return full


@app.put("/leads/companies/{company_id}")
async def leads_update(company_id: int, request: Request):
    _check_auth(request)
    data = await request.json()
    if not leads_db.get_company(company_id):
        raise HTTPException(status_code=404, detail="Firma nicht gefunden")
    leads_db.update_company_fields(company_id, data)
    return {"ok": True}


@app.post("/leads/companies/{company_id}/analyze")
async def leads_analyze(company_id: int, request: Request):
    _check_auth(request)
    try:
        return await _run_in_thread(leads_core.analyze_company, company_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Analyze {company_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)[:300])


@app.post("/leads/companies/{company_id}/research")
async def leads_research(company_id: int, request: Request):
    _check_auth(request)
    try:
        return await _run_in_thread(leads_core.research_company, company_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Research {company_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)[:300])


@app.post("/leads/companies/{company_id}/draft-email")
async def leads_draft(company_id: int, request: Request):
    _check_auth(request)
    data = await request.json()
    stil = data.get("stil") or "formell"
    sender = (data.get("sender_name") or "Kevin").strip()
    try:
        return await _run_in_thread(leads_core.draft_email, company_id, stil, sender)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Draft {company_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)[:300])


@app.put("/leads/emails/{email_id}")
async def leads_email_update(email_id: int, request: Request):
    _check_auth(request)
    data = await request.json()
    if not leads_db.get_email(email_id):
        raise HTTPException(status_code=404, detail="Mail nicht gefunden")
    leads_db.update_email(email_id,
                          subject=data.get("subject"),
                          body_text=data.get("body_text"))
    return {"ok": True}


@app.post("/leads/companies/{company_id}/send")
async def leads_send(company_id: int, request: Request):
    _check_auth(request)
    data = await request.json()
    email_id = data.get("email_id")
    if not email_id:
        raise HTTPException(status_code=400, detail="email_id fehlt")
    try:
        return await _run_in_thread(leads_core.send_email, company_id, int(email_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Send {company_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)[:300])


@app.delete("/leads/companies/{company_id}")
async def leads_delete(company_id: int, request: Request):
    _check_auth(request)
    if not leads_core.delete_company(company_id):
        raise HTTPException(status_code=404, detail="Firma nicht gefunden")
    return {"ok": True}


@app.get("/leads/companies/{company_id}/screenshot")
async def leads_screenshot(company_id: int, request: Request):
    _check_auth(request)
    full = leads_db.get_company_full(company_id)
    if not full:
        raise HTTPException(status_code=404)
    wa = full.get("website_analysis") or {}
    raw_path = wa.get("screenshot_path")
    if not raw_path:
        raise HTTPException(status_code=404, detail="Kein Screenshot")

    # Screenshots dürfen NUR aus dem Screenshots-Verzeichnis geliefert werden.
    # Auch wenn der Pfad aus der DB kommt — nicht vertrauen.
    try:
        resolved = _LeadPath(raw_path).resolve(strict=False)
        resolved.relative_to(_LEADS_SCREENSHOTS_BASE)
    except (ValueError, OSError):
        logger.warning(f"Screenshot-Pfad-Abweisung für company {company_id}: {raw_path}")
        raise HTTPException(status_code=403, detail="Pfad nicht erlaubt")

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Screenshot-Datei nicht gefunden")
    return FileResponse(str(resolved), media_type="image/png")


# ── Wakeword Notifications ───────────────────────────────────────────────────

@app.post("/wakeword")
async def wakeword_event(request: Request):
    data = await request.json()
    event = data.get("event", "idle")
    with _active_lock:
        targets = list(active_connections)
    for ws in targets:
        try:
            await ws.send_json({"type": "wakeword", "event": event, **data})
        except Exception:
            pass
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    with _active_lock:
        active_connections.append(websocket)

    soul = _refresh_prompt_if_changed()
    history = [{"role": "system", "content": soul}]
    history.extend(load_recent_context(n=3))
    logger.info(f"Session gestartet, {len(history)-1} Kontext-Messages geladen")

    async def process_text(text: str, use_tts: bool = False,
                           image_data_url: str | None = None):
        # System-Prompt aktualisieren falls SOUL/USER/MEMORY geändert
        history[0] = {"role": "system", "content": _refresh_prompt_if_changed()}

        if image_data_url:
            # Groq-Multimodal-Format (OpenAI-kompatibel).
            # Text darf leer sein — dann setzt Modell eigenen Default.
            user_content = [
                {"type": "text", "text": text or "Was siehst du auf diesem Bild?"},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
            history.append({"role": "user", "content": user_content})
            # Für Log/Anzeige: Kurzform ohne Base64
            log_user_text = f"[Bild angehängt] {text}".strip()
        else:
            history.append({"role": "user", "content": text})
            log_user_text = text

        loop = asyncio.get_running_loop()
        response_raw = await loop.run_in_executor(
            None, lambda: llm_chat(history, tools=get_tools(), executors=get_executors())
        )

        response = parse_and_execute_commands(response_raw)
        log_conversation(log_user_text, response)
        history.append({"role": "assistant", "content": response})

        # History trimmen: System behalten, max 20 Nachrichten danach.
        # Wichtig: Bilder sind groß — bei Multimodal-Messages behalten wir
        # nur die letzten 4, um das Context-Fenster nicht zu sprengen.
        if len(history) > 21:
            history[1:] = history[-20:]
        _trim_image_messages(history, keep_last=4)

        await websocket.send_json({"type": "message", "text": response})
        if use_tts:
            await loop.run_in_executor(None, lambda: speak(clean_for_tts(response)))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except ValueError as e:
                logger.warning(f"JSON Fehler: {e}")
                continue

            logger.debug(f"Empfangen: type={data.get('type')}")

            if data.get("type") == "text":
                try:
                    await process_text(data["text"], use_tts=False)
                except Exception as e:
                    logger.error(f"FEHLER in process_text: {e}", exc_info=True)
                    try:
                        await websocket.send_json(
                            {"type": "message", "text": f"Interner Fehler: {e}"}
                        )
                    except Exception:
                        pass

            elif data.get("type") == "text_with_image":
                try:
                    img = data.get("image", "")
                    # Nur data:image/...;base64,... akzeptieren
                    if not isinstance(img, str) or not img.startswith("data:image/"):
                        await websocket.send_json({
                            "type": "message",
                            "text": "Ungültiges Bildformat.",
                        })
                        continue
                    # Hartes Limit serverseitig: ~6 MB Base64 ≈ 4.5 MB Binary
                    if len(img) > 6 * 1024 * 1024:
                        await websocket.send_json({
                            "type": "message",
                            "text": "Bild zu groß (max ~4 MB).",
                        })
                        continue
                    await process_text(
                        data.get("text", "") or "",
                        use_tts=False,
                        image_data_url=img,
                    )
                except Exception as e:
                    logger.error(f"FEHLER in process_text (image): {e}", exc_info=True)
                    try:
                        await websocket.send_json(
                            {"type": "message", "text": f"Interner Fehler: {e}"}
                        )
                    except Exception:
                        pass

            elif data.get("type") == "audio":
                tmp_path = None
                wav_path = None
                try:
                    import tempfile
                    audio_bytes = base64.b64decode(data["data"])
                    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                        f.write(audio_bytes)
                        tmp_path = f.name
                    # WAV-Pfad via tempfile.mktemp statt String-Replace
                    wav_path = tempfile.mktemp(suffix=".wav")
                    try:
                        subprocess.run(
                            ["ffmpeg", "-i", tmp_path, "-ar", "16000", "-ac", "1",
                             wav_path, "-y", "-loglevel", "quiet"],
                            check=True, timeout=30,
                        )
                    except FileNotFoundError:
                        logger.error("ffmpeg nicht installiert")
                        await websocket.send_json({
                            "type": "message",
                            "text": "ffmpeg ist auf diesem System nicht installiert.",
                        })
                        continue
                    except subprocess.TimeoutExpired:
                        logger.warning("ffmpeg timeout")
                        await websocket.send_json({
                            "type": "message",
                            "text": "Audio-Konvertierung hat zu lange gedauert.",
                        })
                        continue
                    except subprocess.CalledProcessError as e:
                        logger.warning(f"ffmpeg fehlgeschlagen: {e}")
                        await websocket.send_json({
                            "type": "message",
                            "text": "Audio konnte nicht verarbeitet werden.",
                        })
                        continue

                    segments, _ = get_whisper().transcribe(
                        wav_path, language="de", beam_size=1
                    )
                    text = " ".join(s.text for s in segments).strip()

                    if text:
                        await websocket.send_json({"type": "transcript", "text": text})
                        await process_text(text, use_tts=True)
                    else:
                        await websocket.send_json({
                            "type": "message",
                            "text": "Ich habe dich nicht verstanden, Kevin.",
                        })
                except Exception as e:
                    logger.error(f"FEHLER bei Audio: {e}", exc_info=True)
                finally:
                    for p in (tmp_path, wav_path):
                        if p and os.path.exists(p):
                            try:
                                os.unlink(p)
                            except OSError:
                                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"KRITISCHER FEHLER: {e}", exc_info=True)
    finally:
        with _active_lock:
            if websocket in active_connections:
                active_connections.remove(websocket)
