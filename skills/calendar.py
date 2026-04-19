"""Skill: Kalender abfragen (nur lesend).

Anlegen und Löschen läuft über das Widget im Chat-UI, nicht per Sprache –
das ist bei deutschen Datumsangaben robuster.

Chanti kann damit Fragen wie "Was steht diese Woche an?" oder
"Habe ich heute Termine?" beantworten.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# calendar_core liegt im Chanti-Root, skills/ ist ein Unterordner
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import calendar_core  # noqa: E402


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "calendar_query",
        "description": (
            "Fragt Kevins persönlichen Kalender ab. Nutze dies wenn Kevin fragt "
            "was ansteht, ob heute/morgen/diese Woche Termine sind, oder wann "
            "ein bestimmter Geburtstag ist. Nur lesend – Termine anlegen oder "
            "löschen macht Kevin über das Widget im Chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": (
                        "Wie viele Tage ab heute abgefragt werden sollen. "
                        "0 = nur heute, 1 = heute+morgen, 7 = diese Woche, "
                        "30 = dieser Monat. Default: 7."
                    ),
                    "minimum": 0,
                    "maximum": 366,
                },
            },
            "required": [],
        },
    },
}


def execute(days: int = 7) -> str:
    try:
        days = max(0, min(int(days), 366))
    except (TypeError, ValueError):
        days = 7

    today = date.today()
    hits = calendar_core.get_upcoming(days=days, reference=today)

    if not hits:
        if days == 0:
            return "Heute steht nichts an."
        return f"In den nächsten {days} Tagen steht nichts an."

    lines = [calendar_core.format_hit_for_human(h, reference=today) for h in hits]
    header = (
        "Heute" if days == 0
        else f"In den nächsten {days} Tagen"
    )
    return f"{header} ({len(hits)} Termin{'e' if len(hits) != 1 else ''}):\n" + "\n".join(f"- {l}" for l in lines)
