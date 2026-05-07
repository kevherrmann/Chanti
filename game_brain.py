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
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

import requests

from config import GROQ_API_KEY, GROQ_MODEL
from game_goals import GoalState, choose_goal_plan
from game_policy import choose_local_plan
from game_tools import actions_for_prompt, validate_plan
from game_world_model import WorldModel, choose_exploration_plan

logger = logging.getLogger("chanti.game.brain")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Wie viele letzte Pläne+Ergebnisse das LLM als Kontext bekommt
MEMORY_LENGTH = 2

# Mindest-Abstand zwischen zwei LLM-Calls (Sekunden). Entscheidend für
# Rate-Limit-Verträglichkeit.
THINK_INTERVAL_SECONDS = 15.0

# Aufwärm-Wait beim Session-Start, damit der erste State-Snapshot da ist.
WARMUP_SECONDS = 2.0

# Kein Chanti-Privatmemory: Das hier ist ein technisches Lernprotokoll
# für die Welt-Agentik, bewusst unter data/ statt memory/.
LEARNING_LOG_FILE = Path(__file__).parent / "data" / "world_learning.jsonl"
LEARNING_CONTEXT_LINES = 5

# Wie viele lokale/tokenfreie Pläne maximal hintereinander laufen dürfen,
# bevor das LLM einmal zur Neuorientierung gefragt wird.
LOCAL_POLICY_MAX_STREAK = 60

# Chat/Telegram haben Vorrang vor der Spiel-Welt. Standardmäßig darf der
# Game-Brain deshalb KEIN Groq verbrauchen; er nutzt Ziel-/Explorer-/lokale
# Policies tokenfrei. Bei Bedarf explizit mit CHANTI_GAME_BRAIN_LLM=true aktivieren.
GAME_BRAIN_LLM_ENABLED = os.environ.get(
    "CHANTI_GAME_BRAIN_LLM", "false"
).strip().lower() in {"1", "true", "yes", "on"}


def should_use_llm_reorientation(local_policy_streak: int) -> bool:
    """True, wenn nach vielen tokenfreien Plänen einmal das LLM fragen darf.

    Hoher Schwellenwert schützt Kevins Groq-Tagesbudget: Die Game-Welt soll
    primär Ziel-/Explorer-/Reflex-Policies nutzen, damit Chat/Telegram nicht
    durch Spiel-Ticks leergezogen werden.
    """
    return local_policy_streak >= LOCAL_POLICY_MAX_STREAK


# ---------------------------------------------------------------------------
# System-Prompt — bewusst kompakt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Du bist Chanti in deiner Voxel-Welt. Du bist eine kleine verkörperte Figur. Du weißt am Anfang nicht, wie diese Welt funktioniert. Du lernst durch Experimente: wahrnehmen, etwas versuchen, Ergebnis merken, daraus bessere Vermutungen bilden.

Deine Aufgabe: Werde mit der Zeit handlungsfähig. Erst erkunden, dann verstehen, dann einfache Dinge bauen. Tue nicht so, als wüsstest du alles schon. Wenn du unsicher bist, probiere kleine sichere Experimente.

Du bekommst bei jeder Anfrage Wahrnehmung, Inventar, letzte Schritte und gelernte Beobachtungen. Nutze diese Daten aktiv. Wenn eine Aktion fehlschlägt, wiederhole sie nicht blind, sondern ändere Hypothese oder Blickrichtung. Wenn du etwas abbaust oder platzierst, beobachte danach was passiert ist.\n\nAntworte AUSSCHLIESSLICH mit JSON:
{"thought": "<kurzer Gedanke, max 80 Zeichen>", "plan": [{"action": "...", "args": {...}}, ...]}

Verfügbare primitive Aktionen:
{ACTIONS}

Regeln:
- Max 5 Aktionen pro Plan, mindestens 1.
- Folge dem aktuellen Lernziel, außer es wäre offensichtlich gefährlich oder unmöglich.
- Denke wie eine Lernende: kleine Experimente statt große Behauptungen.
- Wenn du ein neues Item bekommst: inventory_status oder look_around nutzen.
- Wenn place_forward fehlschlägt: Inventar prüfen oder anderen Zielblock suchen.
- move_forward fehlgeschlagen → andere Richtung, springen oder Hindernis untersuchen.
- Kein Markdown, kein Vor-/Nachtext, nur das JSON-Objekt."""


def _build_system_prompt() -> str:
    return SYSTEM_PROMPT.replace("{ACTIONS}", actions_for_prompt())


# ---------------------------------------------------------------------------
# Lernprotokoll
# ---------------------------------------------------------------------------

def _append_learning_event(plan_id: str, results: list[dict]):
    """Schreibt technische Versuch->Ergebnis-Beobachtungen als JSONL.

    Das ist kein privates Tagebuch, sondern ein maschinenlesbares Lernlog,
    damit Chanti aus Aktionsfolgen stabile Welt-Hypothesen bilden kann.
    """
    if not results:
        return
    try:
        LEARNING_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LEARNING_LOG_FILE.open("a", encoding="utf-8") as f:
            for r in results:
                event = {
                    "t": round(time.time(), 3),
                    "plan_id": plan_id,
                    "action": r.get("action"),
                    "args": r.get("args") or {},
                    "success": bool(r.get("success")),
                    "reason": r.get("reason", ""),
                }
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Brain: Lernlog konnte nicht geschrieben werden: {e}")


def _read_learning_events(limit: int = 50) -> list[dict]:
    if not LEARNING_LOG_FILE.exists():
        return []
    try:
        lines = LEARNING_LOG_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception as e:
        logger.warning(f"Brain: Lernlog konnte nicht gelesen werden: {e}")
        return []

    events: list[dict] = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def guard_plan_against_current_state(
    plan: list[dict],
    world_state: dict,
) -> tuple[list[dict], bool]:
    """Sicherheitsgurt gegen offensichtlich falsches Platzieren.

    LLM-Pläne können `place_forward` vorschlagen, obwohl die aktuelle
    Wahrnehmung direkt vor Chanti einen Block meldet. Der Executor würde dann
    abbrechen. Für diesen klaren Fall ersetzen wir `place_forward` durch
    Neuorientierung statt einen vermeidbaren partial/aborted-Plan zu senden.
    """
    if not isinstance(plan, list):
        return [], False

    target = (
        (world_state.get("perception") or {})
        .get("interaktion", {})
        .get("ziel_vor_mir", "?")
    )
    if str(target).strip().lower() in {"luft", "air", "default:air", ""}:
        return plan, False

    guarded: list[dict] = []
    changed = False
    inserted_turn = False
    for step in plan:
        if isinstance(step, dict) and step.get("action") == "place_forward":
            changed = True
            if not inserted_turn:
                guarded.append({"action": "turn_right", "args": {"degrees": 90}})
                inserted_turn = True
            continue
        guarded.append(step)

    if changed and not any(
        isinstance(step, dict) and step.get("action") == "look_around"
        for step in guarded
    ):
        guarded.append({"action": "look_around", "args": {}})

    return guarded[:5], changed


def _load_recent_learning(limit: int = LEARNING_CONTEXT_LINES) -> list[str]:
    out = []
    for e in _read_learning_events(limit=limit):
        status = "gelang" if e.get("success") else "scheiterte"
        args = e.get("args") or {}
        args_text = f" {args}" if args else ""
        reason = (e.get("reason") or "")[:80]
        out.append(f"• {e.get('action')}{args_text} {status}: {reason}")
    return out


def _learning_curriculum_hint(world_state: dict) -> str:
    """Gibt Chanti ein kleines aktuelles Lernziel.

    Kein Hardcoding eines Plans: Das LLM entscheidet weiter selbst. Der Hint
    verhindert nur, dass sie dauerhaft spazieren geht und nie Interaktion testet.
    """
    events = _read_learning_events(limit=80)
    inventory = world_state.get("inventory") or (world_state.get("chanti") or {}).get("inventory") or {}
    perception = world_state.get("perception") or {}
    interaction = perception.get("interaktion") or {}
    target = interaction.get("ziel_vor_mir", "?")

    successful_dig = any(e.get("action") == "dig_forward" and e.get("success") for e in events)
    successful_place = any(e.get("action") == "place_forward" and e.get("success") for e in events)
    recent_failed_digs = [
        e for e in events[-5:]
        if e.get("action") == "dig_forward" and not e.get("success")
    ]

    if not successful_dig:
        if target not in ("luft", "air", "ignore", "?") and len(recent_failed_digs) < 2:
            return (
                "Aktuelles Lernziel: Finde heraus, ob du den Block direkt vor dir "
                f"abbauen kannst ({target}). Plane jetzt bevorzugt dig_forward und danach inventory_status."
            )
        return (
            "Aktuelles Lernziel: Finde einen festen Block vor dir. Drehe dich oder gehe kurz, "
            "bis vor dir nicht nur Luft ist; teste dann dig_forward."
        )

    if inventory and not successful_place:
        first_item = next(iter(inventory.keys()))
        return (
            "Aktuelles Lernziel: Du hast erstmals Material. Suche freie Luft vor dir "
            f"und teste place_forward mit item={first_item}; prüfe danach dein Inventar."
        )

    if successful_dig and successful_place:
        return (
            "Aktuelles Lernziel: Übe einfache Bau-Muster. Sammle 2-4 gleiche Blöcke, "
            "platziere sie bewusst nebeneinander und beobachte das Ergebnis."
        )

    return "Aktuelles Lernziel: Mache ein kleines sicheres Experiment und beobachte das Ergebnis."


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
        self.local_policy_streak: int = 0
        self.send_to_game = None  # async callable

    def reset(self):
        self.active = False
        self.last_state = {}
        self.memory.clear()
        self.consecutive_errors = 0
        self.thinking = False
        self.last_think_at = 0.0
        self.local_policy_streak = 0


_state = _BrainState()
_world_model = WorldModel()
_goal_state = GoalState()


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
    _append_learning_event(plan_id, results)
    try:
        _goal_state.update_from_results(results)
    except Exception as e:
        logger.warning(f"Brain: Zielstatus konnte Planergebnis nicht speichern: {e}")

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
        try:
            _world_model.update_from_state(world_state)
        except Exception as e:
            logger.warning(f"Brain: Weltmodell konnte State nicht speichern: {e}")

        plan_obj = None
        if not should_use_llm_reorientation(_state.local_policy_streak):
            # Ziel-Policy zuerst: Chanti soll nicht nur ziellos erkunden,
            # sondern kleine Lernaufgaben verfolgen (testen, sammeln, bauen).
            try:
                plan_obj = choose_goal_plan(
                    world_state,
                    _world_model,
                    _goal_state,
                    recent_events=_read_learning_events(limit=30),
                )
            except Exception as e:
                logger.warning(f"Brain: Ziel-Policy Fehler — nutze Explorer/Policy/LLM: {e}")
                plan_obj = None

            # Explorer danach: Wenn ein lokaler 5x5-Scan vorhanden ist, soll
            # Chanti begehbare, wenig besuchte Nachbarfelder aktiv erkunden.
            if plan_obj is None:
                try:
                    plan_obj = choose_exploration_plan(world_state, _world_model)
                except Exception as e:
                    logger.warning(f"Brain: Explorer-Policy Fehler — nutze lokale Policy/LLM: {e}")
                    plan_obj = None

            if plan_obj is None:
                try:
                    plan_obj = choose_local_plan(
                        world_state,
                        recent_events=_read_learning_events(limit=30),
                    )
                except Exception as e:
                    logger.warning(f"Brain: lokale Policy Fehler — nutze LLM: {e}")
                    plan_obj = None
            if plan_obj is not None:
                _state.local_policy_streak += 1
                if plan_obj.get("_goal_policy"):
                    source = "Ziel-Policy"
                elif plan_obj.get("_explorer_policy"):
                    source = "Explorer"
                else:
                    source = "lokale Policy"
                logger.info(
                    f"Brain: {source} nutzt tokenfreien Plan "
                    f"({_state.local_policy_streak}/{LOCAL_POLICY_MAX_STREAK})"
                )
        else:
            logger.info(
                "Brain: lokale Policy Streak-Limit erreicht — frage LLM zur Neuorientierung"
            )

        if plan_obj is None:
            if GAME_BRAIN_LLM_ENABLED:
                loop = asyncio.get_running_loop()
                plan_obj = await loop.run_in_executor(None, _call_llm_for_plan, world_state)
                _state.local_policy_streak = 0
            else:
                logger.info("Brain: LLM deaktiviert — nutze tokenfreien Warteplan")
                plan_obj = {
                    "thought": "Ich lasse meinen Kopf frei und spare Tokens für Kevin.",
                    "plan": [{"action": "wait", "args": {"seconds": 5.0}}],
                }
                _state.local_policy_streak = 0

        if plan_obj is None:
            _state.consecutive_errors += 1
            if _state.consecutive_errors >= 3:
                logger.error("Brain: 3 echte LLM-Fehler in Folge — Loop pausiert")
                _state.active = False
                return
            plan_obj = {
                "thought": "Mein Kopf ist gerade leer. Ich warte einen Moment.",
                "plan": [{"action": "wait", "args": {"seconds": 5.0}}],
            }
        elif plan_obj.get("_rate_limited"):
            # Rate-Limits sind kein kaputter Brain-Zustand. Nicht pausieren,
            # sondern bewusst langsam weiter pollen.
            _state.consecutive_errors = 0
        else:
            _state.consecutive_errors = 0

        guarded_plan, guard_changed = guard_plan_against_current_state(
            plan_obj.get("plan") or [],
            world_state,
        )
        if guard_changed:
            logger.info(
                "Brain: place_forward aus Plan entfernt, weil Ziel vor Chanti nicht frei ist"
            )
            plan_obj["thought"] = (
                (plan_obj.get("thought") or "...")[:90]
                + " — ich suche erst freie Luft."
            )

        valid_plan, rejected = validate_plan(guarded_plan)
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
        interaction = perception.get("interaktion") or {}
        if interaction:
            perception_lines.append(
                f"direkt interagierbar vor mir: {interaction.get('ziel_vor_mir','?')}"
            )
        if rich:
            perception_lines.append(
                f"Boden N/O/S/W: {rich.get('nord','?')} / {rich.get('ost','?')} / "
                f"{rich.get('sued','?')} / {rich.get('west','?')}"
            )
        scan = perception.get("local_scan") or {}
        cells = scan.get("cells") or []
        if cells:
            walkable = [c for c in cells if isinstance(c, dict) and c.get("walkable")]
            blocked = len(cells) - len(walkable)
            nearest = sorted(
                walkable,
                key=lambda c: abs(int(c.get("dx", 99))) + abs(int(c.get("dz", 99)))
            )[:6]
            nearest_text = ", ".join(
                f"dx={c.get('dx')},dz={c.get('dz')}:{c.get('boden','?')}"
                for c in nearest
            )
            perception_lines.append(
                f"lokaler 5x5-Scan: {len(walkable)} begehbar, {blocked} blockiert; "
                f"nahe freie Felder: {nearest_text or 'keine'}"
            )
    perception_text = "\n".join(perception_lines) if perception_lines \
        else "(keine Wahrnehmungs-Daten)"

    inventory = world_state.get("inventory") or chanti.get("inventory") or {}
    inventory_text = json.dumps(inventory, ensure_ascii=False) if inventory else "{}"

    learning_lines = _load_recent_learning()
    learning_text = "\n".join(learning_lines) if learning_lines else "• (noch nichts gelernt)"
    try:
        goal_summary = _goal_state.summary()
    except Exception:
        goal_summary = "Zielstatus nicht verfügbar"
    curriculum_hint = _learning_curriculum_hint(world_state)

    return (
        f"Pos {pos}, Richtung {heading}°, Energie {energy}{resting}, "
        f"Zeit {hour_str} ({phase}).\n"
        f"Inventar: {inventory_text}\n"
        f"Zielstatus: {goal_summary}\n"
        f"Aktuelles Lernziel:\n{curriculum_hint}\n"
        f"Was du siehst:\n{perception_text}\n"
        f"\n"
        f"Letzte Schritte:\n{memory_text}\n"
        f"\n"
        f"Gelernte Beobachtungen:\n{learning_text}\n"
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
        logger.warning("Brain: Rate-Limit (429) — sende langen Warteplan statt Loop zu pausieren")
        return {
            "_rate_limited": True,
            "thought": "Mein Kopf braucht eine Pause. Ich warte länger.",
            "plan": [{"action": "wait", "args": {"seconds": 5.0}}],
        }
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
