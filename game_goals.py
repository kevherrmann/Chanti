"""Ziel-/Curriculum-Schicht für Chantis Voxel-Welt.

Diese Schicht sitzt über Explorer/Reflex-Policy: Chanti soll nicht nur laufen,
sondern kleine Lernziele verfolgen: Blocktypen testen, Material sammeln,
Bauplatz finden und eine erste Mini-Struktur bauen.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from game_world_model import WorldModel

GOALS_FILE = Path(__file__).parent / "data" / "world_goals.json"
AIR_NAMES = {"luft", "air", "default:air", ""}
BAD_DIG_TARGETS = AIR_NAMES | {"ignore", "unknown", "?"}
COLLECT_TARGET_COUNT = 5
SAMPLE_TARGET_COUNT = 2
BUILD_LINE_TARGET = 3


class GoalState:
    def __init__(self, path: Path | str = GOALS_FILE):
        self.path = Path(path)
        self.data: dict[str, Any] = _default_data()
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
            base = _default_data()
            base.update(parsed)
            if not isinstance(base.get("tested_blocks"), list):
                base["tested_blocks"] = []
            if not isinstance(base.get("build_line"), dict):
                base["build_line"] = {"placed": 0, "target": BUILD_LINE_TARGET}
            self.data = base

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def update_from_results(self, results: list[dict[str, Any]]) -> None:
        self.load()
        changed = False
        for result in results or []:
            action = result.get("action")
            success = bool(result.get("success"))
            reason = str(result.get("reason") or "")
            if action == "dig_forward" and success:
                block = _parse_dug_block(reason)
                if block and block not in self.data["tested_blocks"]:
                    self.data["tested_blocks"].append(block)
                    changed = True
            if action == "place_forward" and success:
                line = self.data.setdefault("build_line", {"placed": 0, "target": BUILD_LINE_TARGET})
                line["placed"] = int(line.get("placed", 0)) + 1
                changed = True
        self._advance_if_needed()
        if changed:
            self.save()

    def summary(self) -> str:
        self.load()
        tested = len(self.data.get("tested_blocks") or [])
        goal = self.data.get("current_goal", "sample_blocks")
        line = self.data.get("build_line") or {}
        placed = int(line.get("placed", 0))
        target = int(line.get("target", BUILD_LINE_TARGET))
        return f"Ziel={goal}; getestete Blocktypen={tested}; Baulinie={placed}/{target}"

    def _advance_if_needed(self) -> None:
        goal = self.data.get("current_goal")
        if goal == "sample_blocks" and len(self.data.get("tested_blocks") or []) >= SAMPLE_TARGET_COUNT:
            self.data["current_goal"] = "collect_material"
        line = self.data.get("build_line") or {}
        if goal == "build_line" and int(line.get("placed", 0)) >= int(line.get("target", BUILD_LINE_TARGET)):
            self.data["current_goal"] = "explore_area"


def choose_goal_plan(
    world_state: dict[str, Any],
    model: WorldModel,
    goals: GoalState,
    recent_events: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    goals.load()
    recent_events = recent_events or []
    goal = goals.data.get("current_goal") or "sample_blocks"
    inventory = _inventory(world_state)
    target = _target_ahead(world_state)

    # Ziel automatisch weiterdrehen, wenn die Voraussetzung schon erfüllt ist.
    if goal == "sample_blocks" and len(goals.data.get("tested_blocks") or []) >= SAMPLE_TARGET_COUNT:
        goal = goals.data["current_goal"] = "collect_material"
    if goal == "collect_material" and _inventory_total(inventory) >= COLLECT_TARGET_COUNT:
        spot = model.find_build_spot(size=3) if hasattr(model, "find_build_spot") else None
        if spot:
            goals.data["build_spot"] = spot
            goals.data["current_goal"] = goal = "build_line"
            goals.save()
        else:
            goals.data["current_goal"] = goal = "find_build_spot"
            goals.save()

    if goal == "sample_blocks":
        if _is_diggable_target(target) and not _recent_action(recent_events, "dig_forward", last=2):
            return _plan(
                "Lernziel: Ich teste diesen Block und merke, was daraus wird.",
                goal,
                [
                    {"action": "dig_forward", "args": {}},
                    {"action": "inventory_status", "args": {}},
                    {"action": "look_around", "args": {}},
                ],
            )
        return None

    if goal == "collect_material":
        if _inventory_total(inventory) >= COLLECT_TARGET_COUNT:
            return None
        if _is_diggable_target(target) and not _recent_action(recent_events, "dig_forward", last=2):
            return _plan(
                "Lernziel: Ich sammle Material für einen ersten Bauversuch.",
                goal,
                [
                    {"action": "dig_forward", "args": {}},
                    {"action": "inventory_status", "args": {}},
                    {"action": "look_around", "args": {}},
                ],
            )
        return None

    if goal == "find_build_spot":
        spot = model.find_build_spot(size=3) if hasattr(model, "find_build_spot") else None
        if spot:
            goals.data["build_spot"] = spot
            goals.data["current_goal"] = "build_line"
            goals.save()
            goal = "build_line"
        else:
            return None

    if goal == "build_line":
        line = goals.data.setdefault("build_line", {"placed": 0, "target": BUILD_LINE_TARGET})
        if int(line.get("placed", 0)) >= int(line.get("target", BUILD_LINE_TARGET)):
            goals.data["current_goal"] = "explore_area"
            goals.save()
            return None
        item = _first_inventory_item(inventory)
        if not item:
            goals.data["current_goal"] = "collect_material"
            goals.save()
            return None
        if _is_air(target):
            return _plan(
                "Lernziel: Ich baue eine erste kleine Blockreihe.",
                goal,
                [
                    {"action": "place_forward", "args": {"item": item}},
                    {"action": "inventory_status", "args": {}},
                    {"action": "look_around", "args": {}},
                ],
            )
        return _plan(
            "Lernziel: Ich suche freie Luft für meine Blockreihe.",
            goal,
            [
                {"action": "turn_right", "args": {"degrees": 90}},
                {"action": "look_around", "args": {}},
            ],
        )

    return None


def _default_data() -> dict[str, Any]:
    return {
        "version": 1,
        "current_goal": "sample_blocks",
        "tested_blocks": [],
        "build_spot": None,
        "build_line": {"placed": 0, "target": BUILD_LINE_TARGET},
    }


def _plan(thought: str, goal: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"_goal_policy": True, "goal": goal, "thought": thought[:120], "plan": steps[:5]}


def _inventory(world_state: dict[str, Any]) -> dict[str, Any]:
    chanti = world_state.get("chanti") or {}
    inv = world_state.get("inventory") or chanti.get("inventory") or {}
    return inv if isinstance(inv, dict) else {}


def _inventory_total(inventory: dict[str, Any]) -> int:
    total = 0
    for count in inventory.values():
        try:
            total += int(float(count))
        except (TypeError, ValueError):
            pass
    return total


def _first_inventory_item(inventory: dict[str, Any]) -> Optional[str]:
    for item, count in inventory.items():
        try:
            if float(count) > 0:
                return str(item)
        except (TypeError, ValueError):
            continue
    return None


def _target_ahead(world_state: dict[str, Any]) -> Any:
    perception = world_state.get("perception") or {}
    interaction = perception.get("interaktion") or {}
    return interaction.get("ziel_vor_mir", "?")


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _is_air(value: Any) -> bool:
    return _norm(value) in AIR_NAMES


def _is_diggable_target(value: Any) -> bool:
    return _norm(value) not in BAD_DIG_TARGETS


def _recent_action(events: list[dict[str, Any]], action: str, last: int = 2) -> bool:
    return any(event.get("action") == action for event in events[-last:])


def _parse_dug_block(reason: str) -> Optional[str]:
    match = re.search(r"abgebaut\s+([^\s]+)", reason)
    if match:
        return match.group(1)
    return None
