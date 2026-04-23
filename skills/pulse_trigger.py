"""Skill: Manueller Trigger für den Daily-Pulse.

Damit Kevin zum Testen oder 'ich will jetzt das Briefing sehen' nicht bis
18:00 warten muss.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("chanti")


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "pulse_trigger",
        "description": (
            "Führt den Tages-Puls sofort aus: prüft Kalender, Inaktivität und "
            "KI-News, und sendet Ergebnisse an Kevins Telegram. "
            "Nutze dies nur wenn Kevin explizit fragt 'mach den Puls jetzt' oder "
            "'zeig mir das heutige Briefing'. Sonst NICHT von selbst aufrufen — "
            "der Puls läuft automatisch einmal pro Tag um 18:00."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def execute() -> str:
    try:
        import daily_pulse
    except ImportError as e:
        logger.error(f"daily_pulse nicht importierbar: {e}")
        return "Tages-Puls-Modul nicht verfügbar."

    # Wir laufen in einem Skill-Executor-Thread, nicht im Event-Loop.
    # asyncio.run() startet einen eigenen Loop für die Coroutine.
    try:
        asyncio.run(daily_pulse.trigger_now())
        return "Tages-Puls ausgeführt. Falls was zu melden war, ist es via Telegram unterwegs."
    except Exception as e:
        logger.error(f"pulse_trigger: {type(e).__name__}: {e}", exc_info=True)
        return f"Tages-Puls fehlgeschlagen: {type(e).__name__}"
