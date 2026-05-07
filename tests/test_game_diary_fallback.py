import game_diary


def long_report():
    return {
        "started_at": 1777540000.0,
        "duration_seconds": 700.0,
        "ticks": 120,
        "actions": {"ausführen": 20},
        "movement_range": {"min_x": 10, "max_x": 15, "min_z": 20, "max_z": 28},
        "phase_seconds": {"tag": 650.0},
        "first_game_hour": 8.0,
        "last_game_hour": 10.0,
        "days_seen": 0,
        "rest_events": 0,
    }


def test_generate_and_store_uses_human_summary_when_llm_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(game_diary, "DIARY_DIR", tmp_path)
    monkeypatch.setattr(game_diary, "DIARY_FILE", tmp_path / "diary.md")
    monkeypatch.setattr(game_diary, "_llm_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(game_diary, "load_system_prompt", lambda: "Du bist Chanti.")

    result = game_diary.generate_and_store(long_report())

    assert result["diary_written"] is True
    assert "LLM" not in result["summary"]
    assert "nicht geantwortet" not in result["summary"]
    assert "11.7 min" in result["summary"]
    assert "x=10-15" in result["summary"]
