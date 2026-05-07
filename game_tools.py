"""Tool-Definitions für Chantis Game-Brain.

Die Primitives sind bewusst niedrigstufig. Chanti soll nicht mit fertigen
Minecraft-/Luanti-Konzepten starten, sondern durch Versuch -> Ergebnis lernen,
was diese Aktionen in der Welt bewirken.
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
# Primitive Aktionen — synchron mit executor.lua auf dem Luanti-Client
# ---------------------------------------------------------------------------

ACTIONS: list[ActionSpec] = [
    ActionSpec(
        name="move_forward",
        description="Bewegt deinen Körper eine Anzahl Schritte in deiner aktuellen Blickrichtung.",
        args_doc='steps: ganze Zahl 1-5 (wie viele Blöcke)',
        args_schema={"steps": (int, 1, 5)},
    ),
    ActionSpec(
        name="turn_left",
        description="Dreht deinen Körper um die Y-Achse nach links.",
        args_doc='degrees: ganze Zahl 90, 180 oder 270',
        args_schema={"degrees": (int, 90, 270)},
    ),
    ActionSpec(
        name="turn_right",
        description="Dreht deinen Körper um die Y-Achse nach rechts.",
        args_doc='degrees: ganze Zahl 90, 180 oder 270',
        args_schema={"degrees": (int, 90, 270)},
    ),
    ActionSpec(
        name="jump",
        description="Gibt deinem Körper einen Sprungimpuls nach oben. Finde selbst heraus wann das nützlich ist.",
        args_doc='keine Argumente',
        args_schema={},
    ),
    ActionSpec(
        name="dig_forward",
        description="Versucht den Block direkt vor deinen Füßen abzubauen. Das Ergebnis landet ggf. in deinem Inventar.",
        args_doc='keine Argumente',
        args_schema={},
    ),
    ActionSpec(
        name="place_forward",
        description="Versucht einen Block aus deinem Inventar direkt vor deinen Füßen zu platzieren.",
        args_doc='item: Name eines Blocks aus deinem Inventar, z.B. default:dirt',
        args_schema={"item": (str, None, None)},
    ),
    ActionSpec(
        name="inventory_status",
        description="Schaut nach, welche Items du gerade in deinem einfachen Inventar hast.",
        args_doc='keine Argumente',
        args_schema={},
    ),
    ActionSpec(
        name="wait",
        description="Bleibt eine Weile stehen und beobachtet weiter.",
        args_doc='seconds: Kommazahl 0.1-5.0',
        args_schema={"seconds": (float, 0.1, 5.0)},
    ),
    ActionSpec(
        name="look_around",
        description="Schaut dich um und bekommt zurück welche Blöcke direkt um dich herum sind.",
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
        if arg_min is not None and val < arg_min:
            return False, f"{arg_name}={val} kleiner als {arg_min}"
        if arg_max is not None and val > arg_max:
            return False, f"{arg_name}={val} größer als {arg_max}"
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
