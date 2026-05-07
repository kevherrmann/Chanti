import json
from pathlib import Path

from game_world_model import WorldModel, choose_exploration_plan


def state_with_scan(tmp_path, cells, pos=(10, 5, 20), heading=0):
    return {
        "chanti": {"x": pos[0], "y": pos[1], "z": pos[2], "heading": heading},
        "perception": {
            "local_scan": {
                "radius": 2,
                "cells": cells,
            }
        },
    }


def cell(dx, dz, walkable=True, boden="gras", fuesse="luft", kopf="luft"):
    return {
        "dx": dx,
        "dz": dz,
        "x": 10 + dx,
        "y": 5,
        "z": 20 + dz,
        "boden": boden,
        "fuesse": fuesse,
        "kopf": kopf,
        "walkable": walkable,
    }


def test_update_from_state_persists_seen_cells_and_visit_count(tmp_path):
    path = tmp_path / "world_map.json"
    model = WorldModel(path)
    state = state_with_scan(tmp_path, [cell(0, 1), cell(1, 0, walkable=False, fuesse="erde")])

    model.update_from_state(state)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["visited"]["10,5,20"]["count"] == 1
    assert saved["cells"]["10,5,21"]["walkable"] is True
    assert saved["cells"]["11,5,20"]["walkable"] is False


def test_choose_exploration_plan_moves_to_unvisited_walkable_neighbor_ahead(tmp_path):
    path = tmp_path / "world_map.json"
    model = WorldModel(path)
    state = state_with_scan(tmp_path, [cell(0, 1), cell(1, 0, walkable=False, fuesse="erde")], heading=0)
    model.update_from_state(state)

    plan = choose_exploration_plan(state, model)

    assert plan["_explorer_policy"] is True
    assert plan["plan"][0]["action"] == "move_forward"
    assert plan["plan"][0]["args"] == {"steps": 1}


def test_choose_exploration_plan_turns_toward_unvisited_walkable_neighbor(tmp_path):
    path = tmp_path / "world_map.json"
    model = WorldModel(path)
    # Heading north (0°). North is blocked/visited, east is unvisited and walkable.
    first_state = state_with_scan(tmp_path, [cell(0, 1), cell(1, 0)], heading=0)
    model.update_from_state(first_state)
    model.mark_visited(10, 5, 21)

    plan = choose_exploration_plan(first_state, model)

    assert [step["action"] for step in plan["plan"]] == ["turn_left", "look_around"]
    assert plan["plan"][0]["args"] == {"degrees": 90}


def test_choose_exploration_plan_returns_none_without_scan_or_frontier(tmp_path):
    model = WorldModel(tmp_path / "world_map.json")
    state = state_with_scan(tmp_path, [cell(0, 1, walkable=False, fuesse="erde")], heading=0)
    model.update_from_state(state)

    assert choose_exploration_plan(state, model) is None
    assert choose_exploration_plan({"chanti": {"x": 1, "y": 2, "z": 3}}, model) is None
