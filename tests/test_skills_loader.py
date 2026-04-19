"""Tests für skills_loader.py — Lade-Logik, Hot-Reload, Concurrency."""
import threading
from pathlib import Path

import pytest

import skills_loader as sl


GOOD_SKILL = '''
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "__NAME__",
        "description": "test skill",
        "parameters": {"type": "object", "properties": {}}
    }
}
def execute(**k):
    return "ok from __NAME__"
'''


def _write_skill(path: Path, name: str) -> None:
    path.write_text(GOOD_SKILL.replace("__NAME__", name), encoding="utf-8")


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """Leitet SKILLS_DIR auf ein Temp-Verzeichnis um."""
    d = tmp_path / "skills"
    d.mkdir()
    monkeypatch.setattr(sl, "SKILLS_DIR", d)
    # State zurücksetzen
    sl._tools.clear()
    sl._executors.clear()
    sl._files.clear()
    sl._load_errors.clear()
    return d


def test_initial_load_valid_skill(skills_dir):
    _write_skill(skills_dir / "hello.py", "hello")
    tools, execs = sl.load_skills()
    assert len(tools) == 1
    assert "hello" in execs
    assert execs["hello"]() == "ok from hello"


def test_initial_load_tracks_broken_skill(skills_dir):
    (skills_dir / "broken.py").write_text("this is not valid python", encoding="utf-8")
    tools, execs = sl.load_skills()
    assert len(tools) == 0
    errors = sl.get_load_errors()
    assert any("broken.py" in k for k in errors)


def test_reload_detects_new_skill(skills_dir):
    _write_skill(skills_dir / "a.py", "a")
    sl.load_skills()
    assert "a" in sl.get_executors()

    _write_skill(skills_dir / "b.py", "b")
    assert sl.reload_if_changed() is True
    assert sorted(sl.get_executors().keys()) == ["a", "b"]


def test_reload_removes_deleted_skill(skills_dir):
    _write_skill(skills_dir / "foo.py", "foo")
    _write_skill(skills_dir / "bar.py", "bar")
    sl.load_skills()
    assert "foo" in sl.get_executors()

    (skills_dir / "foo.py").unlink()
    assert sl.reload_if_changed() is True
    assert "foo" not in sl.get_executors()
    assert "bar" in sl.get_executors()


def test_reload_picks_up_fixed_skill(skills_dir):
    (skills_dir / "wip.py").write_text("syntax error!!", encoding="utf-8")
    sl.load_skills()
    assert "wip" not in sl.get_executors()

    _write_skill(skills_dir / "wip.py", "wip")
    assert sl.reload_if_changed() is True
    assert "wip" in sl.get_executors()
    assert sl.get_load_errors() == {}


def test_reload_no_op_when_nothing_changed(skills_dir):
    _write_skill(skills_dir / "x.py", "x")
    sl.load_skills()
    assert sl.reload_if_changed() is False


def test_underscore_prefix_files_ignored(skills_dir):
    _write_skill(skills_dir / "_private.py", "private")
    _write_skill(skills_dir / "public.py", "public")
    sl.load_skills()
    assert "private" not in sl.get_executors()
    assert "public" in sl.get_executors()


def test_concurrent_reload_does_not_crash(skills_dir):
    _write_skill(skills_dir / "t.py", "t")
    sl.load_skills()

    errors = []

    def worker():
        try:
            for _ in range(30):
                sl.reload_if_changed()
                _ = sl.get_tools()
                _ = sl.get_executors()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert "t" in sl.get_executors()


def test_getters_return_copies(skills_dir):
    _write_skill(skills_dir / "x.py", "x")
    sl.load_skills()
    # Caller-Mutation darf internen State nicht beschädigen
    tools = sl.get_tools()
    tools.clear()
    execs = sl.get_executors()
    execs.clear()
    assert "x" in sl.get_executors()
    assert len(sl.get_tools()) == 1
