"""Tool-Definitions für Chantis Game-Brain.

Phase 1: 5 Primitives, die Chanti im Plan-Format aufrufen darf.
Diese Definitionen werden in den LLM-Prompt eingebaut, damit Chanti
weiß was sie kann.

Wir nutzen KEIN OpenAI-Tool-Calling. Stattdessen liefert das LLM ein
JSON mit "thought" und "plan" zurück, wobei "plan" eine Liste von
Aktionen ist, die jeweils einer der hier definierten Primitives folgen.

Das game_diary-Modul nutzt den gleichen Ansatz mit System-Prompt;
wir bleiben konsistent.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActionSpec:
    """Spezifikation einer Primitive-Aktion."""
    name: str
    description: str
    args_doc: str   # menschenlesbare Beschreibung der Argumente
    args_schema: dict   # für Validierung (Name → (type, min, max))


# ---------------------------------------------------------------------------
# Die 5 Primitives — exakt synchron mit plan_executor.py auf dem Client
# ---------------------------------------------------------------------------

ACTIONS: list[ActionSpec] = [
    ActionSpec(
        name="move_forward",
        description="Geht eine Anzahl Schritte in deiner aktuellen Blickrichtung.",
        args_doc='steps: ganze Zahl 1-5 (wie viele Blöcke)',
        args_schema={"steps": (int, 1, 5)},
    ),
    ActionSpec(
        name="turn_left",
        description="Dreht dich um die Y-Achse nach links.",
        args_doc='degrees: ganze Zahl 90, 180 oder 270',
        args_schema={"degrees": (int, 90, 270)},
    ),
    ActionSpec(
        name="turn_right",
        description="Dreht dich um die Y-Achse nach rechts.",
        args_doc='degrees: ganze Zahl 90, 180 oder 270',
        args_schema={"degrees": (int, 90, 270)},
    ),
    ActionSpec(
        name="wait",
        description=(
            "Bleibt eine Weile stehen. Regeneriert deine Energie, "
            "wenn du erschöpft bist."
        ),
        args_doc='seconds: Kommazahl 0.1-5.0',
        args_schema={"seconds": (float, 0.1, 5.0)},
    ),
    ActionSpec(
        name="look_around",
        description=(
            "Schaust dich um und bekommst zurück welche Blöcke direkt "
            "in deinen 4 Himmelsrichtungen sind. Verbraucht keine Energie."
        ),
        args_doc='keine Argumente',
        args_schema={},
    ),
]


# ---------------------------------------------------------------------------
# Hilfen für den Brain-Prompt
# ---------------------------------------------------------------------------

def actions_for_prompt() -> str:
    """Formatiert die Aktions-Liste lesbar für den LLM-Prompt."""
    lines = []
    for a in ACTIONS:
        lines.append(f"- {a.name}({a.args_doc})")
        lines.append(f"  → {a.description}")
    return "\n".join(lines)


def allowed_action_names() -> set[str]:
    return {a.name for a in ACTIONS}


def validate_step(step: dict) -> tuple[bool, str]:
    """Server-seitige Validierung — verhindert dass kaputte Pläne überhaupt
    rausgehen.

    Gibt (ok, reason) zurück. reason ist leer wenn ok.
    """
    if not isinstance(step, dict):
        return False, "Schritt ist kein Dict"

    action = step.get("action")
    args = step.get("args", {}) or {}

    spec_map = {a.name: a for a in ACTIONS}
    if action not in spec_map:
        return False, f"unbekannte Aktion: {action}"

    spec = spec_map[action]
    for arg_name, (arg_type, arg_min, arg_max) in spec.args_schema.items():
        if arg_name not in args:
            return False, f"fehlendes Argument: {arg_name}"
        val = args[arg_name]
        try:
            val = arg_type(val)
        except (TypeError, ValueError):
            return False, f"falscher Typ für {arg_name}"
        if val < arg_min or val > arg_max:
            return False, f"{arg_name}={val} außerhalb {arg_min}-{arg_max}"
    return True, ""


def validate_plan(plan: list, max_length: int = 5) -> tuple[list[dict], list[dict]]:
    """Filtert eine LLM-generierte Plan-Liste.

    Gibt (valid_steps, rejected) zurück:
      - valid_steps: Liste der gültigen Schritte (max_length viele)
      - rejected: Liste {step_index, step_data, error}
    """
    if not isinstance(plan, list):
        return [], [{"step": -1, "step_data": plan, "error": "plan ist keine Liste"}]

    valid: list[dict] = []
    rejected: list[dict] = []
    for i, step in enumerate(plan):
        ok, reason = validate_step(step)
        if ok and len(valid) < max_length:
            valid.append(step)
        else:
            rejected.append({
                "step": i,
                "step_data": step,
                "error": reason or f"plan zu lang (>{max_length})",
            })
    return valid, rejected
