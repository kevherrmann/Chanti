"""Tests für file_edit.py — Path-Traversal, Symlink-Block, Size-Limit."""
import os
from pathlib import Path

import pytest

from conftest import load_skill


@pytest.fixture
def fe(tmp_home):
    """Lädt file_edit frisch, damit BASE auf das Temp-Home zeigt."""
    return load_skill("file_edit")


def test_read_write_roundtrip(fe, tmp_home):
    result = fe.execute("write", "SOUL.md", "hallo welt")
    assert "gespeichert" in result
    read_back = fe.execute("read", "SOUL.md")
    assert read_back == "hallo welt"


def test_case_insensitive_read(fe, tmp_home):
    fe.execute("write", "SOUL.md", "ich bin SOUL")
    # Wird per case-insensitive-Lookup gefunden
    assert "SOUL" in fe.execute("read", "soul.md")


def test_absolute_path_blocked(fe, tmp_home):
    result = fe.execute("read", "/etc/passwd")
    assert "verweigert" in result.lower()


def test_dotdot_blocked(fe, tmp_home):
    result = fe.execute("read", "../../etc/passwd")
    assert "verweigert" in result.lower()


def test_symlink_to_outside_blocked(fe, tmp_home):
    # Symlink nach ~/chanti/evil.md der auf /etc/passwd zeigt
    (tmp_home / "evil.md").symlink_to("/etc/passwd")
    result = fe.execute("read", "evil.md")
    assert "verweigert" in result.lower() or "nicht" in result.lower()


def test_symlink_in_parent_blocked(fe, tmp_home):
    outside = tmp_home.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "file.md").write_text("SECRET")
    (tmp_home / "subdir").symlink_to(outside)

    result = fe.execute("read", "subdir/file.md")
    assert "verweigert" in result.lower()


def test_size_limit_enforced(fe, tmp_home):
    huge = "x" * (fe.MAX_WRITE_BYTES + 1)
    result = fe.execute("write", "huge.md", huge)
    assert "zu groß" in result.lower() or "zu gross" in result.lower()


def test_backup_is_created_on_overwrite(fe, tmp_home):
    fe.execute("write", "SOUL.md", "v1")
    fe.execute("write", "SOUL.md", "v2")
    bak = tmp_home / "SOUL.md.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == "v1"


def test_list_excludes_backups(fe, tmp_home):
    fe.execute("write", "TEST.md", "content")
    fe.execute("write", "TEST.md", "content2")
    listing = fe.execute("list")
    assert "TEST.md" in listing
    assert ".bak" not in listing


def test_list_excludes_venv_cache_dirs(fe, tmp_home):
    (tmp_home / ".venv").mkdir()
    (tmp_home / ".venv" / "some.py").write_text("# should be hidden")
    (tmp_home / "__pycache__").mkdir()
    (tmp_home / "__pycache__" / "cache.py").write_text("# should be hidden")
    (tmp_home / "real.py").write_text("# visible")

    listing = fe.execute("list")
    assert ".venv" not in listing
    assert "__pycache__" not in listing
    assert "real.py" in listing


def test_empty_path_handled(fe, tmp_home):
    assert "Fehler" in fe.execute("read", "")
    assert "Fehler" in fe.execute("read", None)


def test_write_without_content_errors(fe, tmp_home):
    assert "Fehler" in fe.execute("write", "x.md", None)
