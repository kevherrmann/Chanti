"""Technisches Weltmodell und einfache Explorer-Policy für Chantis Voxel-Welt.

Kein Chanti-Privatmemory: Diese Daten sind maschinenlesbarer Weltzustand und
werden unter data/ gespeichert. Das Modell soll verhindern, dass die lokale
Policy nur rotiert, indem es besuchte/gesichtete Zellen und begehbare neue
Nachbarfelder verfolgt.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

WORLD_MAP_FILE = Path(__file__).parent / "data" / "world_map.json"


class WorldModel:
    def __init__(self, path: Path | str = WORLD_MAP_FILE):
        self.path = Path(path)
        self.data: dict[str, Any] = {"version": 1, "visited": {}, "cells": {}}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(parsed, dict):
            self.data["visited"] = parsed.get("visited") if isinstance(parsed.get("visited"), dict) else {}
            self.data["cells"] = parsed.get("cells") if isinstance(parsed.get("cells"), dict) else {}
            self.data["version"] = parsed.get("version", 1)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    @staticmethod
    def key(x: Any, y: Any, z: Any) -> str:
        return f"{int(round(float(x)))},{int(round(float(y)))},{int(round(float(z)))}"

    def mark_visited(self, x: Any, y: Any, z: Any) -> None:
        self.load()
        k = self.key(x, y, z)
        now = round(time.time(), 3)
        entry = self.data["visited"].get(k, {"count": 0})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_seen"] = now
        self.data["visited"][k] = entry

    def visit_count(self, x: Any, y: Any, z: Any) -> int:
        self.load()
        entry = self.data.get("visited", {}).get(self.key(x, y, z), {})
        try:
            return int(entry.get("count", 0))
        except (TypeError, ValueError):
            return 0

    def update_from_state(self, world_state: dict[str, Any]) -> None:
        self.load()
        chanti = world_state.get("chanti") or {}
        if all(k in chanti for k in ("x", "y", "z")):
            self.mark_visited(chanti["x"], chanti["y"], chanti["z"])

        scan = (world_state.get("perception") or {}).get("local_scan") or {}
        cells = scan.get("cells") or []
        now = round(time.time(), 3)
        for cell in cells:
            if not isinstance(cell, dict) or not all(k in cell for k in ("x", "y", "z")):
                continue
            k = self.key(cell["x"], cell["y"], cell["z"])
            self.data["cells"][k] = {
                "x": int(round(float(cell["x"]))),
                "y": int(round(float(cell["y"]))),
                "z": int(round(float(cell["z"]))),
                "dx": int(cell.get("dx", 0)),
                "dz": int(cell.get("dz", 0)),
                "boden": cell.get("boden", "?"),
                "fuesse": cell.get("fuesse", "?"),
                "kopf": cell.get("kopf", "?"),
                "walkable": bool(cell.get("walkable")),
                "last_seen": now,
            }
        self.save()

    def find_build_spot(self, size: int = 3) -> Optional[dict[str, int]]:
        """Findet eine bekannte begehbare size×size-Fläche.

        Für Phase 4b reicht eine kompakte lokale Suche in bereits gesehenen
        Zellen. Später kann daraus ein echter Planner mit Pfadsuche werden.
        """
        self.load()
        cells = self.data.get("cells", {})
        walkable = {
            (int(c["x"]), int(c["y"]), int(c["z"]))
            for c in cells.values()
            if isinstance(c, dict) and c.get("walkable") and all(k in c for k in ("x", "y", "z"))
        }
        if not walkable:
            return None
        radius = size // 2
        for x, y, z in sorted(walkable):
            ok = True
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    if (x + dx, y, z + dz) not in walkable:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                return {"x": x, "y": y, "z": z, "size": size}
        return None


def choose_exploration_plan(world_state: dict[str, Any], model: WorldModel) -> Optional[dict[str, Any]]:
    """Wählt einen lokalen Erkundungsschritt zu einem unbesuchten Nachbarfeld.

    Nutzt nur die 4 direkt angrenzenden Zellen aus `perception.local_scan`.
    Größere Pfadsuche kommt später; dieser erste Schritt soll Rotation ersetzen,
    wenn ein begehbarer, noch wenig besuchter Nachbar sichtbar ist.
    """
    chanti = world_state.get("chanti") or {}
    perception = world_state.get("perception") or {}
    scan = perception.get("local_scan") or {}
    cells = scan.get("cells") or []
    if not cells or not all(k in chanti for k in ("x", "y", "z")):
        return None

    candidates = []
    for cell in cells:
        if not isinstance(cell, dict) or not bool(cell.get("walkable")):
            continue
        dx = int(cell.get("dx", 999))
        dz = int(cell.get("dz", 999))
        # Für sichere erste Exploration nur direkt angrenzende Kardinalfelder.
        if abs(dx) + abs(dz) != 1:
            continue
        if not all(k in cell for k in ("x", "y", "z")):
            continue
        visits = model.visit_count(cell["x"], cell["y"], cell["z"])
        candidates.append((visits, _direction_priority(dx, dz, chanti.get("heading", 0)), cell))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    _visits, _prio, target = candidates[0]
    desired_heading = _heading_for_delta(int(target["dx"]), int(target["dz"]))
    current_heading = _normalize_heading(chanti.get("heading", 0))
    turn = _smallest_turn(current_heading, desired_heading)

    if abs(turn) <= 1:
        return _plan(
            "Ich sehe ein wenig besuchtes freies Feld und erkunde es.",
            [
                {"action": "move_forward", "args": {"steps": 1}},
                {"action": "look_around", "args": {}},
            ],
        )

    action = "turn_left" if turn < 0 else "turn_right"
    return _plan(
        "Ich richte mich zu einem wenig besuchten freien Feld aus.",
        [
            {"action": action, "args": {"degrees": abs(int(turn))}},
            {"action": "look_around", "args": {}},
        ],
    )


def _plan(thought: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"_explorer_policy": True, "thought": thought[:120], "plan": steps[:5]}


def _normalize_heading(value: Any) -> int:
    try:
        return int(round(float(value))) % 360
    except (TypeError, ValueError):
        return 0


def _heading_for_delta(dx: int, dz: int) -> int:
    # Luanti-Formel im Mod: yaw 0 -> +z, 90 -> -x, 180 -> -z, 270 -> +x.
    if dx == 0 and dz == 1:
        return 0
    if dx == -1 and dz == 0:
        return 90
    if dx == 0 and dz == -1:
        return 180
    if dx == 1 and dz == 0:
        return 270
    return 0


def _smallest_turn(current: int, desired: int) -> int:
    # Positiv = rechts in executor/game_policy, negativ = links.
    return ((desired - current + 540) % 360) - 180


def _direction_priority(dx: int, dz: int, heading: Any) -> int:
    desired = _heading_for_delta(dx, dz)
    return abs(_smallest_turn(_normalize_heading(heading), desired))
