import json
from pathlib import Path

from game_goals import GoalState, choose_goal_plan
from game_world_model import WorldModel


def state(target="luft", inventory=None):
    if inventory is None:
        inventory = {}
    return {
        "chanti": {"x": 10, "y": 5, "z": 20, "heading": 0},
        "inventory": inventory,
        "perception": {
            "interaktion": {"ziel_vor_mir": target},
            "vor_mir": {"schritt_1": {"boden": "gras", "fuesse": target, "kopf": "luft"}},
            "local_scan": {"cells": []},
        },
    }


def make_model(tmp_path):
    model = WorldModel(tmp_path / "world_map.json")
    # 3x3 walkable patch around 10/5/20
    cells = []
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            cells.append({
                "dx": dx, "dz": dz, "x": 10 + dx, "y": 5, "z": 20 + dz,
                "boden": "gras", "fuesse": "luft", "kopf": "luft", "walkable": True,
            })
    model.update_from_state({"chanti": {"x": 10, "y": 5, "z": 20}, "perception": {"local_scan": {"cells": cells}}})
    return model


def test_sample_blocks_digs_untried_solid_target(tmp_path):
    goals = GoalState(tmp_path / "world_goals.json")
    model = make_model(tmp_path)

    plan = choose_goal_plan(state(target="erde"), model, goals, recent_events=[])

    assert plan["_goal_policy"] is True
    assert plan["goal"] == "sample_blocks"
    assert [step["action"] for step in plan["plan"]] == ["dig_forward", "inventory_status", "look_around"]


def test_goal_state_records_dig_results_as_tested_blocks(tmp_path):
    goals = GoalState(tmp_path / "world_goals.json")

    goals.update_from_results([
        {"action": "dig_forward", "success": True, "reason": "abgebaut default:dry_dirt -> default:dry_dirt"}
    ])

    saved = json.loads((tmp_path / "world_goals.json").read_text(encoding="utf-8"))
    assert "default:dry_dirt" in saved["tested_blocks"]


def test_collect_material_digs_until_inventory_has_target_amount(tmp_path):
    goals = GoalState(tmp_path / "world_goals.json")
    goals.data["current_goal"] = "collect_material"
    model = make_model(tmp_path)

    plan = choose_goal_plan(state(target="gras", inventory={"default:dry_dirt": 2}), model, goals, recent_events=[])

    assert plan["goal"] == "collect_material"
    assert plan["plan"][0]["action"] == "dig_forward"


def test_enough_material_finds_build_spot_and_starts_build_line(tmp_path):
    goals = GoalState(tmp_path / "world_goals.json")
    goals.data["current_goal"] = "collect_material"
    model = make_model(tmp_path)

    plan = choose_goal_plan(state(target="luft", inventory={"default:dry_dirt": 5}), model, goals, recent_events=[])

    assert plan["goal"] == "build_line"
    assert goals.data["current_goal"] == "build_line"
    assert goals.data["build_spot"] is not None
    assert plan["plan"][0]["action"] == "place_forward"


def test_build_line_counts_successful_placements(tmp_path):
    goals = GoalState(tmp_path / "world_goals.json")
    goals.data["current_goal"] = "build_line"
    goals.data["build_line"] = {"placed": 0, "target": 3}

    goals.update_from_results([
        {"action": "place_forward", "success": True, "reason": "platziert default:dry_dirt"}
    ])

    assert goals.data["build_line"]["placed"] == 1
