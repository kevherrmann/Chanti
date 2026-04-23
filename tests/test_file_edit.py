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


def test_workspace_path_blocked_in_file_edit(fe, tmp_home):
    """file_edit darf nicht in workspace/ schreiben — das gehört workspace_edit."""
    result = fe.execute("write", "workspace/foo.py", "print('x')")
    assert "verweigert" in result.lower()
    assert not (tmp_home / "workspace" / "foo.py").exists()


def test_workspace_read_blocked_in_file_edit(fe, tmp_home):
    # Auch read wird geblockt — konsistente Trennung.
    (tmp_home / "workspace").mkdir(exist_ok=True)
    (tmp_home / "workspace" / "secret.py").write_text("secret")
    result = fe.execute("read", "workspace/secret.py")
    assert "verweigert" in result.lower()


def test_tilde_prefix_handled(fe, tmp_home):
    # ~/chanti/foo.md soll als foo.md geschrieben werden
    result = fe.execute("write", "~/chanti/foo.md", "hi")
    assert "gespeichert" in result
    assert (tmp_home / "foo.md").read_text() == "hi"


def test_tilde_only_rejected(fe, tmp_home):
    result = fe.execute("read", "~/other/passwd")
    assert "nicht expandiert" in result.lower() or "verweigert" in result.lower()


def test_list_hides_workspace(fe, tmp_home):
    ws = tmp_home / "workspace"
    ws.mkdir()
    (ws / "hello.py").write_text("print('hi')")
    (tmp_home / "SOUL.md").write_text("identity")
    listing = fe.execute("list")
    assert "SOUL.md" in listing
    assert "workspace" not in listing
    assert "hello.py" not in listing


# ---------- str_replace ----------

def test_str_replace_basic(fe, tmp_home):
    fe.execute("write", "note.md", "hallo welt\nbye")
    out = fe.execute("str_replace", "note.md", old_str="welt", new_str="mond")
    assert "gepatcht" in out
    assert fe.execute("read", "note.md") == "hallo mond\nbye"


def test_str_replace_missing_old_str(fe, tmp_home):
    fe.execute("write", "note.md", "foo")
    out = fe.execute("str_replace", "note.md", old_str="nicht drin", new_str="x")
    assert "nicht in der Datei" in out


def test_str_replace_ambiguous(fe, tmp_home):
    fe.execute("write", "note.md", "foo\nfoo\nfoo")
    out = fe.execute("str_replace", "note.md", old_str="foo", new_str="bar")
    assert "eindeutig" in out.lower() or "3-mal" in out


def test_str_replace_empty_old_str_rejected(fe, tmp_home):
    fe.execute("write", "note.md", "hi")
    out = fe.execute("str_replace", "note.md", old_str="", new_str="x")
    assert "Fehler" in out


def test_str_replace_file_not_found(fe, tmp_home):
    out = fe.execute("str_replace", "nope.md", old_str="a", new_str="b")
    assert "nicht gefunden" in out.lower()


def test_str_replace_empty_new_str_deletes(fe, tmp_home):
    fe.execute("write", "note.md", "prefix-XYZ-suffix")
    out = fe.execute("str_replace", "note.md", old_str="XYZ-", new_str="")
    assert "gepatcht" in out
    assert fe.execute("read", "note.md") == "prefix-suffix"


def test_str_replace_creates_backup(fe, tmp_home):
    fe.execute("write", "note.md", "a b c")
    fe.execute("str_replace", "note.md", old_str="b", new_str="B")
    bak = tmp_home / "note.md.bak"
    assert bak.exists()
    assert bak.read_text() == "a b c"


def test_str_replace_respects_workspace_block(fe, tmp_home):
    """str_replace darf genau wie write nicht in workspace/ wirken."""
    ws = tmp_home / "workspace"
    ws.mkdir()
    (ws / "foo.py").write_text("print('a')")
    out = fe.execute("str_replace", "workspace/foo.py",
                     old_str="a", new_str="b")
    assert "verweigert" in out.lower()
