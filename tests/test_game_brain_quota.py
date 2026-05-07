from game_brain import LOCAL_POLICY_MAX_STREAK, should_use_llm_reorientation


def test_game_brain_uses_long_tokenfree_streak_to_protect_chat_quota():
    assert LOCAL_POLICY_MAX_STREAK >= 50
    assert should_use_llm_reorientation(LOCAL_POLICY_MAX_STREAK - 1) is False
    assert should_use_llm_reorientation(LOCAL_POLICY_MAX_STREAK) is True
