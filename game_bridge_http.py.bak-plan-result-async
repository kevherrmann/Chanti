"""HTTP-Bridge für Chanti-Welt (Luanti-Variante).

Im Gegensatz zur WebSocket-Bridge (game_bridge.py) nutzt diese hier
reine HTTP-Endpoints. Hintergrund: Luanti-Mods können nur HTTP-Requests
machen, kein WebSocket. Daher polling-basiert.

Endpoints:
  POST /game/state       — Mod sendet aktuellen Welt-State
  GET  /game/poll        — Mod fragt nach Pending-Plan (Long-Poll-light)
  POST /game/plan_result — Mod meldet Ausführungs-Resultat
  GET  /game/http_status — HTTP-Bridge-Status (Debug)

Session-Detection: Sobald der erste State kommt → Session-Start. Bleibt
30s lang kein State mehr → Session-Ende (Tagebuch + Telegram).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException, Request

from config import GAME_WS_TOKEN  # gleiches Token wiederverwenden

logger = logging.getLogger("chanti.game.http")

router = APIRouter(prefix="/game", tags=["game-http"])


# ---------------------------------------------------------------------------
# Session-Tracker (analog zum WebSocket-Bridge-Tracker)
# ---------------------------------------------------------------------------

class _SessionTracker:
    def __init__(self, started_at: float):
        self.started_at = started_at
        self.ended_at: Optional[float] = None
        self.ticks_seen = 0
        self.action_counts: Counter = Counter()
        self.rest_events = 0
        self._was_resting = False
        self.last_state: dict = {}
        self.last_state_t: Optional[float] = None
        # Bewegung
        self.min_x = self.max_x = self.min_z = self.max_z = None  # type: ignore
        # Tageszeit-Phasen
        self.phase_seconds: Counter = Counter()
        self._last_phase_t: Optional[float] = None
        self.first_game_hour: Optional[float] = None
        self.last_game_hour: Optional[float] = None
        self.days_seen = 0

    def ingest_state(self, state: dict):
        self.last_state = state or {}
        self.last_state_t = time.time()
        self.ticks_seen += 1

        last_action = (state.get("last_action") or {}).get("name")
        if last_action and last_action != "—":
            self.action_counts[last_action] += 1

        chanti = state.get("chanti") or {}
        if "x" in chanti and "z" in chanti:
            x, z = chanti["x"], chanti["z"]
            self.min_x = x if self.min_x is None else min(self.min_x, x)
            self.max_x = x if self.max_x is None else max(self.max_x, x)
            self.min_z = z if self.min_z is None else min(self.min_z, z)
            self.max_z = z if self.max_z is None else max(self.max_z, z)

        stats = state.get("stats") or {}
        resting = bool(stats.get("is_resting", False))
        if resting and not self._was_resting:
            self.rest_events += 1
        self._was_resting = resting

        time_info = state.get("time") or {}
        phase = time_info.get("phase")
        now = time.time()
        if phase:
            if self._last_phase_t is not None:
                delta = now - self._last_phase_t
                if 0 < delta < 60:
                    self.phase_seconds[phase] += delta
            self._last_phase_t = now

        hour = time_info.get("hour")
        if hour is not None:
            if self.first_game_hour is None:
                self.first_game_hour = float(hour)
            self.last_game_hour = float(hour)

        day = time_info.get("day")
        if isinstance(day, int) and day > self.days_seen:
            self.days_seen = day

    def close(self, ended_at: float):
        self.ended_at = ended_at

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return max(0.0, end - self.started_at)

    def report(self) -> dict:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": round(self.duration_seconds, 1),
            "ticks": self.ticks_seen,
            "actions": dict(self.action_counts),
            "rest_events": self.rest_events,
            "movement_range": {
                "min_x": self.min_x, "max_x": self.max_x,
                "min_z": self.min_z, "max_z": self.max_z,
            },
            "phase_seconds": {k: round(v, 1) for k, v in self.phase_seconds.items()},
            "first_game_hour": self.first_game_hour,
            "last_game_hour": self.last_game_hour,
            "days_seen": self.days_seen,
            "last_state": self.last_state,
        }


# ---------------------------------------------------------------------------
# Bridge-State
# ---------------------------------------------------------------------------

# Wie lange kein State = Session beendet
SESSION_TIMEOUT_SECONDS = 30.0


class _HttpBridgeState:
    def __init__(self):
        self.session_active = False
        self.session_started_at: Optional[float] = None
        self.last_state: dict = {}
        self.last_state_t: Optional[float] = None
        self.tracker: Optional[_SessionTracker] = None
        # Pending-Plan: vom Brain abgelegt, von der Mod abgeholt
        self.pending_plan: Optional[dict] = None
        self.lock = asyncio.Lock()


state = _HttpBridgeState()


# ---------------------------------------------------------------------------
# Handler-Registry
# ---------------------------------------------------------------------------

_session_end_handler: Optional[Callable[[dict], Awaitable[None]]] = None
_session_start_handler: Optional[Callable[[], Awaitable[None]]] = None
_plan_result_handler: Optional[Callable[[dict], Awaitable[None]]] = None


def register_session_end_handler(h: Callable[[dict], Awaitable[None]]):
    global _session_end_handler
    _session_end_handler = h
    logger.info("HTTP-Bridge: Session-End-Handler registriert")


def register_session_start_handler(h: Callable[[], Awaitable[None]]):
    global _session_start_handler
    _session_start_handler = h
    logger.info("HTTP-Bridge: Session-Start-Handler registriert")


def register_plan_result_handler(h: Callable[[dict], Awaitable[None]]):
    global _plan_result_handler
    _plan_result_handler = h
    logger.info("HTTP-Bridge: Plan-Result-Handler registriert")


# ---------------------------------------------------------------------------
# Plan-Senden (vom Brain genutzt)
# ---------------------------------------------------------------------------

async def send_to_game(message: dict[str, Any]) -> bool:
    """Vom Brain aufgerufen — legt einen Plan ins Pending-Slot.

    Wenn schon ein Plan wartet, wird er überschrieben. Das Brain sollte
    eigentlich nie zwei gleichzeitig schicken (es wartet auf Result).
    """
    if not state.session_active:
        return False
    async with state.lock:
        state.pending_plan = message
    return True


def is_session_active() -> bool:
    return state.session_active


# ---------------------------------------------------------------------------
# Hintergrund-Task: Session-Timeout-Erkennung
# ---------------------------------------------------------------------------

async def _watchdog_loop():
    """Prüft regelmäßig ob die Session nicht zu lange still war."""
    while True:
        await asyncio.sleep(5.0)
        if not state.session_active:
            continue
        if state.last_state_t is None:
            continue
        if time.time() - state.last_state_t > SESSION_TIMEOUT_SECONDS:
            await _close_session("Timeout — kein State seit 30s")


async def _close_session(reason: str):
    async with state.lock:
        if not state.session_active:
            return
        logger.info(f"HTTP-Bridge: Session-Ende ({reason})")
        report: Optional[dict] = None
        if state.tracker:
            state.tracker.close(ended_at=time.time())
            report = state.tracker.report()
        state.session_active = False
        state.session_started_at = None
        state.tracker = None
        state.pending_plan = None

    if report and _session_end_handler:
        try:
            await _session_end_handler(report)
        except Exception as e:
            logger.error(f"Session-End-Handler Fehler: {e}", exc_info=True)


def start_watchdog():
    """Wird von server.py beim Lifespan-Startup aufgerufen."""
    asyncio.create_task(_watchdog_loop())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _check_token(payload: dict) -> str:
    """Prüft Token aus Request-Body. Gibt 'OK' oder Fehlermeldung zurück."""
    token = payload.get("token", "")
    if not GAME_WS_TOKEN:
        return "Game-Bridge ist nicht konfiguriert (kein Token im .env)"
    if token != GAME_WS_TOKEN:
        return "Falsches Token"
    return "OK"


@router.post("/state")
async def post_state(request: Request):
    """Mod sendet aktuellen Welt-State. Bei erstem Request: Session-Start."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    auth = _check_token(payload)
    if auth != "OK":
        raise HTTPException(status_code=403, detail=auth)

    world_state = payload.get("state") or {}

    async with state.lock:
        first_state = not state.session_active
        if first_state:
            state.session_active = True
            state.session_started_at = time.time()
            state.tracker = _SessionTracker(started_at=state.session_started_at)
            state.pending_plan = None
            logger.info("HTTP-Bridge: Session gestartet")

        state.last_state = world_state
        state.last_state_t = time.time()
        if state.tracker:
            state.tracker.ingest_state(world_state)

    if first_state and _session_start_handler:
        try:
            await _session_start_handler()
        except Exception as e:
            logger.error(f"Session-Start-Handler Fehler: {e}", exc_info=True)

    return {"ok": True}


@router.get("/poll")
async def get_poll(token: str = ""):
    """Mod fragt nach pending Plan."""
    if not GAME_WS_TOKEN:
        raise HTTPException(status_code=503, detail="Game bridge disabled")
    if token != GAME_WS_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    async with state.lock:
        plan = state.pending_plan
        state.pending_plan = None  # Plan abgeholt → Slot frei

    if plan is None:
        return {"plan": None}
    return plan


@router.post("/plan_result")
async def post_plan_result(request: Request):
    """Mod meldet Ausführungs-Resultat."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    auth = _check_token(payload)
    if auth != "OK":
        raise HTTPException(status_code=403, detail=auth)

    if _plan_result_handler:
        try:
            await _plan_result_handler(payload)
        except Exception as e:
            logger.error(f"Plan-Result-Handler Fehler: {e}", exc_info=True)

    return {"ok": True}


@router.get("/http_status")
async def http_status():
    """Debug-Endpoint."""
    return {
        "session_active": state.session_active,
        "session_started_at": state.session_started_at,
        "last_state_t": state.last_state_t,
        "pending_plan": bool(state.pending_plan),
        "last_state_keys": list(state.last_state.keys()) if state.last_state else [],
    }
