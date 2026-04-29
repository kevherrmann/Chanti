"""Chanti-Skill: game_status

Chanti kann damit ihren eigenen Zustand in der Chanti-Welt abfragen —
Position, Energie, Tageszeit und was sie um sich herum sieht.

Datenquelle ist der in-memory HTTP-Bridge-State (game_bridge_http.state).
Kein HTTP, kein WebSocket-Call — wir sind im selben Prozess wie die Bridge.
"""
from __future__ import annotations

import time

from game_bridge_http import state as bridge_state


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "game_status",
        "description": (
            "PFLICHT bei jeder Frage zur Chanti-Welt / zum Spiel / zum Game. "
            "Gibt die realen Live-Daten deiner Welt zurück: ob du gerade "
            "verbunden bist (d.h. ob Kevin das Spiel auf seinem PC gestartet "
            "hat), deine Position, was du um dich herum siehst, deine Energie "
            "und die Tageszeit. "
            "RUFE DIESEN SKILL IMMER AUF bei Fragen wie "
            "'Was machst du im Spiel?', 'Bist du in deiner Welt?', "
            "'Wo stehst du gerade?', 'Was siehst du?' oder Ähnlichem. "
            "Wenn der Skill meldet, dass du NICHT verbunden bist, dann bist "
            "du auch wirklich NICHT in der Welt — in diesem Fall NIEMALS "
            "Szenen, Umgebung, Aktivitäten oder Wahrnehmungen aus der Welt "
            "beschreiben. Erfinde keine Erlebnisse. Antworte ehrlich, dass "
            "das Spiel gerade nicht läuft."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def _format_age(ts: float | None) -> str:
    if ts is None:
        return "nie"
    age = time.time() - ts
    if age < 2:
        return "gerade eben"
    if age < 60:
        return f"vor {int(age)} Sekunden"
    if age < 3600:
        return f"vor {int(age // 60)} Minuten"
    return f"vor {int(age // 3600)} Stunden"


def _format_perception(perc: dict) -> str:
    """Formatiert die Wahrnehmungs-Daten lesbar für Chanti."""
    if not perc:
        return "Wahrnehmung unbekannt."

    parts = []
    unter = perc.get("unter_mir")
    if unter:
        parts.append(f"Unter dir: {unter}.")

    vor = perc.get("vor_mir") or {}
    s1 = vor.get("schritt_1") or {}
    if s1:
        parts.append(
            f"Direkt vor dir: Boden={s1.get('boden','?')}, "
            f"Fußhöhe={s1.get('fuesse','?')}, Kopfhöhe={s1.get('kopf','?')}."
        )

    rich = perc.get("himmelsrichtungen") or {}
    if rich:
        parts.append(
            f"Boden in Himmelsrichtungen: "
            f"Nord={rich.get('nord','?')}, Ost={rich.get('ost','?')}, "
            f"Süd={rich.get('sued','?')}, West={rich.get('west','?')}."
        )

    return " ".join(parts)


def execute() -> str:
    if not bridge_state.session_active:
        last = bridge_state.last_state_t
        if last:
            return (
                f"STATUS: NICHT_VERBUNDEN. Das Spiel läuft gerade nicht. "
                f"Die letzte Session war {_format_age(last)}. "
                f"Antworte Kevin ehrlich, dass du momentan nicht in deiner "
                f"Welt bist. Erfinde KEINE Welt-Erlebnisse."
            )
        return (
            "STATUS: NICHT_VERBUNDEN. Das Spiel wurde noch nicht gestartet. "
            "Antworte Kevin ehrlich, dass du noch nie in dieser Welt warst, "
            "er müsste sie auf seinem PC erst starten. "
            "Erfinde KEINE Welt-Erlebnisse."
        )

    last_state = bridge_state.last_state or {}
    session_age = _format_age(bridge_state.session_started_at)

    chanti = last_state.get("chanti") or {}
    stats = last_state.get("stats") or {}
    time_info = last_state.get("time") or {}
    perception = last_state.get("perception") or {}
    last_action = last_state.get("last_action") or {}

    pos_text = (
        f"Position x={chanti.get('x', '?')}, y={chanti.get('y', '?')}, "
        f"z={chanti.get('z', '?')}, Blickrichtung {chanti.get('heading', '?')}°"
        if chanti else "Position unbekannt"
    )

    energy = stats.get("energy", "?")
    energy_max = stats.get("energy_max", 100)
    resting = " (du ruhst gerade)" if stats.get("is_resting") else ""

    hour_str = time_info.get("hour_str", "?")
    phase = time_info.get("phase", "?")

    perception_text = _format_perception(perception)

    lines = [
        "STATUS: VERBUNDEN. Das Spiel läuft gerade.",
        f"Session seit {session_age}.",
        pos_text,
        f"Energie: {energy}/{energy_max}{resting}.",
        f"Tageszeit: {hour_str} ({phase}).",
        perception_text,
        f"Letzte Aktion: {last_action.get('name', '—')}.",
        (
            "Beschreibe Kevin ehrlich was du gerade in der Welt wahrnimmst, "
            "basierend auf diesen Daten. Keine erfundenen Details die nicht "
            "in diesen Daten stehen."
        ),
    ]
    return "\n".join(lines)
