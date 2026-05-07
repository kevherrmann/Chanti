"""Tokenfreie lokale Policy für Chantis Voxel-Welt.

Diese Schicht entscheidet einfache, sichere Primitive ohne LLM-Call.
Sie ersetzt kein Brain, sondern fängt offensichtliche Fälle ab:
- Material + Luft vor ihr -> platzieren
- Block direkt vor ihr -> abbauen
- Luft / unklar -> kleine Erkundung

Die Policy bleibt bewusst konservativ. Wenn sie unsicher ist, gibt sie None
zurück; dann darf game_brain.py wie bisher das LLM fragen.
"""
from __future__ import annotations

from typing import Any, Optional


AIR_NAMES = {"luft", "air", "default:air", ""}
UNKNOWN_NAMES = {"?", None}
BAD_DIG_TARGETS = AIR_NAMES | {"ignore", "unknown"}
BAD_PLACE_TARGETS = {"ignore", "unknown", "?"}


def choose_local_plan(world_state: dict[str, Any], recent_events: Optional[list[dict[str, Any]]] = None) -> Optional[dict[str, Any]]:
    """Gibt einen lokal erzeugten Plan zurück oder None.

    Rückgabeformat ist absichtlich kompatibel zum LLM-Planobjekt:
    {"thought": str, "plan": [{"action": ..., "args": ...}], "_local_policy": True}
    """
    if not isinstance(world_state, dict) or not world_state:
        return None

    recent_events = recent_events or []
    inventory = _inventory(world_state)
    perception = world_state.get("perception") or {}
    target = _target_ahead(perception)
    front = _front_step(perception, "schritt_1")

    last_productive = _last_successful_productive_action(recent_events)
    failed_place_items = _recent_failed_place_items(recent_events, limit=8)

    # Anti-Pingpong: Wenn sie gerade einen Block platziert hat, steht dieser
    # danach direkt vor ihr. Den sofort wieder abzubauen ist keine neue
    # Erkenntnis, sondern eine Endlosschleife. Erst Perspektive/Ort wechseln.
    if last_productive == "place_forward" and _is_diggable_target(target):
        return _plan(
            "Ich habe gerade platziert; ich baue es nicht sofort wieder ab.",
            [
                {"action": "turn_right", "args": {"degrees": 90}},
                {"action": "look_around", "args": {}},
            ],
        )

    # Nach erfolgreichem Platzieren und anschließend freiem Blick nicht sofort
    # den nächsten Block setzen. Sonst baut Chanti eine Wand um sich herum und
    # bleibt lokal auf "immer weiter platzieren" hängen. Erst neuen Standort
    # suchen, wenn der Schritt vor ihr begehbar ist.
    if last_productive == "place_forward" and _is_air(target):
        if _front_walkable(front) and not _recent_action(recent_events, "move_forward", last=2):
            return _plan(
                "Ich habe gerade gebaut; ich laufe zu einem neuen Platz.",
                [
                    {"action": "move_forward", "args": {"steps": 1}},
                    {"action": "look_around", "args": {}},
                ],
            )
        return _plan(
            "Ich habe gerade gebaut; ich ändere erst die Perspektive.",
            [
                {"action": "turn_right", "args": {"degrees": 90}},
                {"action": "look_around", "args": {}},
            ],
        )

    # Anti-Pingpong: Wenn sie gerade abgebaut hat, ist vor ihr Luft. Sofort in
    # dasselbe Loch zurückzuplatzieren macht den Fortschritt rückgängig.
    if last_productive == "dig_forward" and inventory and _is_air(target):
        return _plan(
            "Ich habe gerade abgebaut; ich suche erst einen neuen Platz.",
            [
                {"action": "turn_right", "args": {"degrees": 90}},
                {"action": "look_around", "args": {}},
            ],
        )

    # Wenn Platzieren mehrfach am gleichen/veralteten Item scheitert, nicht
    # blind denselben Plan wiederholen. Das passiert z.B. wenn der State noch
    # default:dry_dirt enthält, der Executor aber meldet: nicht im Inventar.
    if _recent_failures(recent_events, "place_forward", limit=6) >= 2:
        if _front_walkable(front) and not _recent_action(recent_events, "move_forward", last=2):
            return _plan(
                "Platzieren scheiterte mehrfach: ich laufe erst aus der Ecke.",
                [
                    {"action": "move_forward", "args": {"steps": 1}},
                    {"action": "look_around", "args": {}},
                ],
            )
        return _plan(
            "Platzieren scheiterte mehrfach: ich prüfe Inventar und drehe mich.",
            [
                {"action": "inventory_status", "args": {}},
                {"action": "turn_right", "args": {"degrees": 90}},
                {"action": "look_around", "args": {}},
            ],
        )

    # 1) Sicherer produktiver Fall: Material + Luft, aber nicht direkt nach
    # einem erfolgreichen Abbau an exakt derselben Stelle.
    if inventory and _is_air(target):
        item = _first_inventory_item(inventory, skip=failed_place_items)
        if item:
            return _plan(
                "Ich habe Material und vor mir ist Luft: ich platziere.",
                [
                    {"action": "place_forward", "args": {"item": item}},
                    {"action": "inventory_status", "args": {}},
                    {"action": "look_around", "args": {}},
                ],
            )
        return _plan(
            "Mein Inventar-State wirkt unsicher: ich prüfe ihn neu.",
            [
                {"action": "inventory_status", "args": {}},
                {"action": "look_around", "args": {}},
            ],
        )

    # 2) Direkt vor ihr ist ein echter Block: abbauen statt LLM fragen, aber
    # nicht den gerade selbst platzierten Block.
    if _is_diggable_target(target):
        # Wenn exakt diese Situation gerade mehrfach scheiterte, lieber kurz drehen.
        if _recent_failures(recent_events, "dig_forward", limit=4) >= 2:
            return _plan(
                "Abbauen scheiterte mehrfach: ich ändere Blickrichtung.",
                [
                    {"action": "turn_right", "args": {"degrees": 90}},
                    {"action": "look_around", "args": {}},
                ],
            )
        return _plan(
            f"Vor mir ist {target}: ich teste Abbauen.",
            [
                {"action": "dig_forward", "args": {}},
                {"action": "inventory_status", "args": {}},
                {"action": "look_around", "args": {}},
            ],
        )

    # 3) Sie sucht Material: wenn ein Schritt nach vorne frei wirkt, vorsichtig gehen.
    if not inventory and _front_walkable(front) and not _recent_action(recent_events, "move_forward", last=1):
        return _plan(
            "Vor mir ist frei: ich erkunde einen Schritt weiter.",
            [
                {"action": "move_forward", "args": {"steps": 1}},
                {"action": "look_around", "args": {}},
            ],
        )

    # 4) Nichts sinnvoll direkt vor ihr: drehen und Wahrnehmung erneuern.
    if _is_air(target):
        direction = "turn_left" if _recent_action(recent_events, "turn_right", last=2) else "turn_right"
        return _plan(
            "Direkt vor mir ist nur Luft: ich schaue in eine andere Richtung.",
            [
                {"action": direction, "args": {"degrees": 90}},
                {"action": "look_around", "args": {}},
            ],
        )

    # 5) Perception fehlt/unklar: lokale Policy kann nur beobachten.
    if target in UNKNOWN_NAMES:
        return _plan(
            "Meine Wahrnehmung ist unklar: ich schaue mich um.",
            [{"action": "look_around", "args": {}}],
        )

    return None


def _plan(thought: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"_local_policy": True, "thought": thought[:120], "plan": steps[:5]}


def _inventory(world_state: dict[str, Any]) -> dict[str, Any]:
    chanti = world_state.get("chanti") or {}
    inv = world_state.get("inventory") or chanti.get("inventory") or {}
    return inv if isinstance(inv, dict) else {}


def _first_inventory_item(inventory: dict[str, Any], skip: Optional[set[str]] = None) -> Optional[str]:
    skip = skip or set()
    for item, count in inventory.items():
        item_name = str(item)
        if item_name in skip:
            continue
        try:
            if float(count) > 0:
                return item_name
        except (TypeError, ValueError):
            continue
    return None


def _target_ahead(perception: dict[str, Any]) -> Any:
    interaction = perception.get("interaktion") or {}
    return interaction.get("ziel_vor_mir", "?")


def _front_step(perception: dict[str, Any], key: str) -> dict[str, Any]:
    vor = perception.get("vor_mir") or {}
    step = vor.get(key) or {}
    return step if isinstance(step, dict) else {}


def _norm(name: Any) -> str:
    if name is None:
        return ""
    return str(name).strip().lower()


def _is_air(name: Any) -> bool:
    return _norm(name) in AIR_NAMES


def _is_diggable_target(name: Any) -> bool:
    return _norm(name) not in BAD_DIG_TARGETS


def _front_walkable(step: dict[str, Any]) -> bool:
    feet = _norm(step.get("fuesse"))
    head = _norm(step.get("kopf"))
    return feet in AIR_NAMES and head in AIR_NAMES


def _recent_failures(events: list[dict[str, Any]], action: str, limit: int = 5) -> int:
    return sum(
        1
        for event in events[-limit:]
        if event.get("action") == action and not bool(event.get("success"))
    )


def _recent_action(events: list[dict[str, Any]], action: str, last: int = 1) -> bool:
    return any(event.get("action") == action for event in events[-last:])


def _last_successful_productive_action(events: list[dict[str, Any]]) -> Optional[str]:
    """Letzte erfolgreiche Weltveränderung, Debug-Aktionen ignorieren."""
    productive = {"dig_forward", "place_forward"}
    for event in reversed(events):
        action = event.get("action")
        if action in productive and bool(event.get("success")):
            return str(action)
    return None


def _recent_failed_place_items(events: list[dict[str, Any]], limit: int = 8) -> set[str]:
    """Items, die laut Executor zuletzt nicht platzierbar waren.

    Wichtig gegen stale State: Die HTTP-State-Nachricht kann noch ein Item im
    Inventar zeigen, obwohl `place_forward` bereits "nicht im Inventar" meldete.
    Dann darf die lokale Policy nicht denselben Item-Namen endlos wiederholen.
    """
    failed: set[str] = set()
    for event in events[-limit:]:
        if event.get("action") != "place_forward" or bool(event.get("success")):
            continue
        args = event.get("args") or {}
        item = args.get("item")
        reason = str(event.get("reason") or "")
        if item and ("nicht im Inventar" in reason or "Ziel ist nicht frei" in reason):
            failed.add(str(item))
    return failed
