from game_brain import guard_plan_against_current_state


def state_with_target(target):
    return {
        "perception": {
            "interaktion": {"ziel_vor_mir": target},
            "vor_mir": {"schritt_1": {"boden": "gras", "fuesse": target, "kopf": "luft"}},
        }
    }


def test_guard_replaces_place_forward_when_target_is_not_air():
    plan = [
        {"action": "inventory_status", "args": {}},
        {"action": "place_forward", "args": {"item": "default:dry_dirt"}},
        {"action": "look_around", "args": {}},
    ]

    guarded, changed = guard_plan_against_current_state(plan, state_with_target("gras"))

    assert changed is True
    assert [step["action"] for step in guarded] == ["inventory_status", "turn_right", "look_around"]
    assert guarded[1]["args"] == {"degrees": 90}


def test_guard_keeps_place_forward_when_target_is_air():
    plan = [{"action": "place_forward", "args": {"item": "default:dry_dirt"}}]

    guarded, changed = guard_plan_against_current_state(plan, state_with_target("luft"))

    assert changed is False
    assert guarded == plan


def test_guard_keeps_non_place_actions_even_when_target_is_not_air():
    plan = [
        {"action": "dig_forward", "args": {}},
        {"action": "look_around", "args": {}},
    ]

    guarded, changed = guard_plan_against_current_state(plan, state_with_target("gras"))

    assert changed is False
    assert guarded == plan
