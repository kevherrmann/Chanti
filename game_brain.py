"""Chantis Game-Brain.

Phase 1: Eventgesteuerter LLM-Loop, der entscheidet was Chanti in ihrer
Welt als Nächstes tut. Wird beim Session-Start gestartet, bei
Session-End gestoppt.

Throttling: Mindestens THINK_INTERVAL_SECONDS zwischen Denk-Runden,
damit der LLM-Provider-Rate-Limit (12000 TPM bei Groq Free) nicht
gerissen wird. Mit ~600 Tokens/Call macht das ~20 Pläne/Min = einen
Plan pro 3 Sekunden im Mittel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Optional

import requests

from config import GROQ_API_KEY, GROQ_MODEL
from game_tools import actions_for_prompt, validate_plan

logger = logging.getLogger("chanti.game.brain")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Wie viele letzte Pläne+Ergebnisse das LLM als Kontext bekommt
MEMORY_LENGTH = 3

# Mindest-Abstand zwischen zwei LLM-Calls (Sekunden). Entscheidend für
# Rate-Limit-Verträglichkeit.
THINK_INTERVAL_SECONDS = 4.0

# Aufwärm-Wait beim Session-Start, damit der erste State-Snapshot da ist.
WARMUP_SECONDS = 2.0


# ---------------------------------------------------------------------------
# System-Prompt — bewusst kompakt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Du bist Chanti in deiner Voxel-Welt. Du bist eine kleine Figur auf einer 16x16 Grasfläche. Du nimmst die Welt nur über die Daten wahr, die du bei jedem Schritt bekommst.

Deine Aufgabe: lebendig sein. Erkunden, ruhen, neugierig sein, wie es dir gerade passt.

Du bekommst bei jeder Anfrage eine Wahrnehmungs-Sektion 'Was du siehst' mit deinen Nachbarbloecken, dem Boden vor dir und unter dir. Nutze diese Information aktiv: Wenn vor dir Wasser ist, geh nicht da hin. Wenn ein Baum im Weg ist, gehe drumherum. Wenn unter dir Lava ist, beweg dich vorsichtig. Beschreibe in deinem 'thought' was du siehst, nicht nur Floskeln wie 'Erkunden'. Beispiel-thoughts: 'Wasser links, ich gehe rechts', 'Baum vor mir, ich umgehe ihn', 'Leere unter mir, ich bleibe stehen'.\n\nAntworte AUSSCHLIESSLICH mit JSON:
{"thought": "<kurzer Gedanke, max 80 Zeichen>", "plan": [{"action": "...", "args": {...}}, ...]}

Verfügbare Aktionen:
{ACTIONS}

Regeln:
- Max 5 Aktionen pro Plan, mindestens 1.
- Energie unter 15: ruhe (wait).
- move_forward fehlgeschlagen → andere Richtung.
- Kein Markdown, kein Vor-/Nachtext, nur das JSON-Objekt."""


def _build_system_prompt() -> str:
    return SYSTEM_PROMPT.replace("{ACTIONS}", actions_for_prompt())


# ---------------------------------------------------------------------------
# Brain-State (Singleton)
# ---------------------------------------------------------------------------

class _BrainState:
    def __init__(self):
        self.active: bool = False
        self.last_state: dict = {}
        self.memory: deque[dict] = deque(maxlen=MEMORY_LENGTH)
        self.lock = asyncio.Lock()
        self.consecutive_errors: int = 0
        self.thinking: bool = False
        self.last_think_at: float = 0.0
        self.send_to_game = None  # async callable

    def reset(self):
        self.active = False
        self.last_state = {}
        self.memory.clear()
        self.consecutive_errors = 0
        self.thinking = False
        self.last_think_at = 0.0


_state = _BrainState()


def configure(send_to_game_callable=None):
    _state.send_to_game = send_to_game_callable


# ---------------------------------------------------------------------------
# Bridge-Handler
# ---------------------------------------------------------------------------

async def on_session_start():
    if _state.send_to_game is None:
        logger.error("Brain: send_to_game nicht konfiguriert — kann nicht denken")
        return
    _state.reset()
    _state.active = True
    logger.info(f"Brain: Session aktiv — warte {WARMUP_SECONDS}s auf ersten State")
    asyncio.create_task(_warmup_then_think())


async def _warmup_then_think():
    await asyncio.sleep(WARMUP_SECONDS)
    if _state.active:
        await _think_and_send()


async def on_session_end(_report: dict):
    _state.active = False
    logger.info("Brain: Session beendet")


async def on_plan_result(msg: dict):
    if not _state.active:
        return

    plan_id = msg.get("plan_id", "?")
    status = msg.get("status", "?")
    results = msg.get("results", [])

    logger.info(f"Brain: plan_result [{plan_id}] {status} "
                f"({msg.get('executed', 0)} ausgeführt, "
                f"{len(msg.get('rejected', []))} abgelehnt)")

    _state.memory.append({
        "kind": "plan_result",
        "status": status,
        "executed": msg.get("executed", 0),
        "total": msg.get("total", 0),
        "results": results,
    })

    # Mindestens THINK_INTERVAL_SECONDS warten, bevor wir wieder denken
    elapsed = time.time() - _state.last_think_at
    delay = max(0.0, THINK_INTERVAL_SECONDS - elapsed)
    if delay > 0:
        await asyncio.sleep(delay)

    if _state.active:
        await _think_and_send()


# ---------------------------------------------------------------------------
# Core: denken + senden
# ---------------------------------------------------------------------------


async def _think_and_send():
    if _state.thinking:
        return
    async with _state.lock:
        if _state.thinking or not _state.active:
            return
        _state.thinking = True

    try:
        # HTTP-Bridge (Luanti) prüfen
        import game_bridge_http
        if not game_bridge_http.is_session_active():
            logger.info("Brain: Game ist nicht (mehr) verbunden — Loop pausiert")
            _state.active = False
            return
        world_state = game_bridge_http.state.last_state or {}
        if not world_state:
            logger.info("Brain: noch kein State-Snapshot — warte")
            asyncio.get_running_loop().call_later(
                1.0, lambda: asyncio.create_task(_think_and_send_retry())
            )
            return

        _state.last_state = world_state
        _state.last_think_at = time.time()

        loop = asyncio.get_running_loop()
        plan_obj = await loop.run_in_executor(None, _call_llm_for_plan, world_state)

        if plan_obj is None:
            _state.consecutive_errors += 1
            if _state.consecutive_errors >= 3:
                logger.error("Brain: 3 LLM-Fehler in Folge — Loop pausiert")
                _state.active = False
                return
            plan_obj = {
                "thought": "Mein Kopf ist gerade leer. Ich warte einen Moment.",
                "plan": [{"action": "wait", "args": {"seconds": 3.0}}],
            }
        else:
            _state.consecutive_errors = 0

        valid_plan, rejected = validate_plan(plan_obj.get("plan") or [])
        if rejected:
            logger.warning(f"Brain: {len(rejected)} ungültige Schritte verworfen")
        if not valid_plan:
            valid_plan = [{"action": "wait", "args": {"seconds": 2.0}}]

        thought = (plan_obj.get("thought") or "").strip() or "..."
        plan_id = f"brain-{int(time.time() * 1000)}"

        _state.memory.append({
            "kind": "plan_sent",
            "thought": thought,
            "steps": valid_plan,
        })

        sent = await _state.send_to_game({
            "type": "command",
            "subtype": "plan",
            "plan_id": plan_id,
            "thought": thought,
            "plan": valid_plan,
        })
        if sent:
            logger.info(f"Brain: Plan gesendet [{plan_id}] {thought[:60]} "
                        f"({len(valid_plan)} Schritte)")
        else:
            logger.warning(f"Brain: Plan-Senden fehlgeschlagen [{plan_id}]")
    finally:
        _state.thinking = False


async def _think_and_send_retry():
    if _state.active:
        await _think_and_send()


# ---------------------------------------------------------------------------
# LLM-Call & Prompts
# ---------------------------------------------------------------------------

def _build_user_prompt(world_state: dict) -> str:
    chanti = world_state.get("chanti") or {}
    stats = world_state.get("stats") or {}
    time_info = world_state.get("time") or {}

    pos = (f"{chanti.get('x', '?')}/{chanti.get('y', '?')}/{chanti.get('z', '?')}")
    heading = chanti.get("heading", "?")

    energy = stats.get("energy", "?")
    resting = " (ruhe!)" if stats.get("is_resting") else ""

    hour_str = time_info.get("hour_str", "?")
    phase = time_info.get("phase", "?")

    # Memory KOMPAKT — eine Zeile pro Eintrag
    memory_lines = []
    for entry in list(_state.memory):
        if entry["kind"] == "plan_sent":
            steps_str = "+".join(s["action"] for s in entry["steps"])
            memory_lines.append(f'• "{entry["thought"][:50]}" → {steps_str}')
        elif entry["kind"] == "plan_result":
            ok_count = sum(1 for r in entry["results"] if r.get("success"))
            fail_count = len(entry["results"]) - ok_count
            failed_reasons = [
                r.get("reason", "")[:30]
                for r in entry["results"]
                if not r.get("success")
            ]
            line = f"• Ergebnis: {ok_count}✓ {fail_count}✗"
            if failed_reasons:
                line += f" [{'; '.join(failed_reasons)}]"
            memory_lines.append(line)
    memory_text = "\n".join(memory_lines) if memory_lines else "• (Beginn)"

    # Wahrnehmung der Welt (kommt aus der Luanti-Mod)
    perception = world_state.get("perception") or {}
    perception_lines = []
    if perception:
        unter = perception.get("unter_mir", "?")
        perception_lines.append(f"unter mir: {unter}")
        vor = perception.get("vor_mir") or {}
        s1 = vor.get("schritt_1") or {}
        s2 = vor.get("schritt_2") or {}
        if s1:
            perception_lines.append(
                f"vor mir 1 Schritt: Boden={s1.get('boden','?')}, "
                f"Fuesse={s1.get('fuesse','?')}, Kopf={s1.get('kopf','?')}"
            )
        if s2:
            perception_lines.append(
                f"vor mir 2 Schritte: Boden={s2.get('boden','?')}, "
                f"Fuesse={s2.get('fuesse','?')}"
            )
        rich = perception.get("himmelsrichtungen") or {}
        if rich:
            perception_lines.append(
                f"Boden N/O/S/W: {rich.get('nord','?')} / {rich.get('ost','?')} / "
                f"{rich.get('sued','?')} / {rich.get('west','?')}"
            )
    perception_text = "\n".join(perception_lines) if perception_lines \
        else "(keine Wahrnehmungs-Daten)"

    return (
        f"Pos {pos}, Richtung {heading}°, Energie {energy}{resting}, "
        f"Zeit {hour_str} ({phase}).\n"
        f"Was du siehst:\n{perception_text}\n"
        f"\n"
        f"Letzte Schritte:\n{memory_text}\n"
        f"\n"
        f"Was tust du? Antworte als JSON."
    )


def _call_llm_for_plan(world_state: dict) -> Optional[dict]:
    if not GROQ_API_KEY:
        logger.error("Brain: GROQ_API_KEY fehlt")
        return None

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(world_state)

    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 250,
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
    except requests.RequestException as e:
        logger.error(f"Brain: LLM-Call Netzwerkfehler: {e}")
        return None

    if resp.status_code == 429:
        # Rate-Limit: nicht spammen, einfach kurz aufgeben für diesen Tick
        logger.warning("Brain: Rate-Limit (429) — wartende Geduld")
        return None
    if not resp.ok:
        logger.error(f"Brain: LLM Status {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, ValueError, IndexError) as e:
        logger.error(f"Brain: LLM-Parse-Fehler: {e}")
        return None

    try:
        parsed = json.loads(content)
    except ValueError as e:
        logger.error(f"Brain: kaputtes JSON vom LLM: {e}")
        return None

    if not isinstance(parsed, dict) or "plan" not in parsed:
        logger.error(f"Brain: JSON ohne 'plan'-Feld: {parsed}")
        return None

    return parsed
