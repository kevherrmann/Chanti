"""Tests für workspace_edit.py — Sandbox, Pfad-Normalisierung, Trennung zu file_edit."""
import pytest

from conftest import load_skill


@pytest.fixture
def we(tmp_home):
    """Lädt workspace_edit frisch, damit BASE auf das Temp-Home zeigt."""
    return load_skill("workspace_edit")


# ---------- Basic ----------

def test_workspace_is_created(we, tmp_home):
    we.execute("list")
    assert (tmp_home / "workspace").is_dir()


def test_read_write_roundtrip(we, tmp_home):
    result = we.execute("write", "hello.py", "print('hi')")
    assert "gespeichert" in result
    assert we.execute("read", "hello.py") == "print('hi')"
    # Landet wirklich im workspace/, nicht im chanti-Root
    assert (tmp_home / "workspace" / "hello.py").exists()
    assert not (tmp_home / "hello.py").exists()


def test_nested_path(we, tmp_home):
    we.execute("write", "src/main.py", "x = 1")
    assert (tmp_home / "workspace" / "src" / "main.py").read_text() == "x = 1"


# ---------- Path-Normalisierung ----------

def test_tilde_workspace_prefix_normalized(we, tmp_home):
    we.execute("write", "~/chanti/workspace/foo.py", "y = 2")
    assert (tmp_home / "workspace" / "foo.py").read_text() == "y = 2"


def test_workspace_prefix_normalized(we, tmp_home):
    # Modell sagt oft 'workspace/foo.py' obwohl das schon die Base ist
    we.execute("write", "workspace/foo.py", "z = 3")
    assert (tmp_home / "workspace" / "foo.py").read_text() == "z = 3"
    # Kein verschachteltes workspace/workspace/
    assert not (tmp_home / "workspace" / "workspace").exists()


def test_tilde_only_rejected(we, tmp_home):
    result = we.execute("read", "~/other/passwd")
    assert "nicht expandiert" in result.lower() or "verweigert" in result.lower()


# ---------- Sicherheit ----------

def test_absolute_path_blocked(we, tmp_home):
    result = we.execute("read", "/etc/passwd")
    assert "verweigert" in result.lower()


def test_dotdot_blocked(we, tmp_home):
    result = we.execute("read", "../SOUL.md")
    assert "verweigert" in result.lower()


def test_symlink_to_outside_blocked(we, tmp_home):
    ws = tmp_home / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / "evil.py").symlink_to("/etc/passwd")
    result = we.execute("read", "evil.py")
    assert "verweigert" in result.lower() or "nicht" in result.lower()


def test_cannot_escape_to_chanti_root(we, tmp_home):
    # Versuch über Symlink rauszukommen
    ws = tmp_home / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / "escape").symlink_to(tmp_home)
    result = we.execute("read", "escape/SOUL.md")
    assert "verweigert" in result.lower()


# ---------- Size-Limit ----------

def test_size_limit_enforced(we, tmp_home):
    huge = "x" * (we.MAX_WRITE_BYTES + 1)
    result = we.execute("write", "huge.py", huge)
    assert "zu groß" in result.lower() or "zu gross" in result.lower()


# ---------- List ----------

def test_list_excludes_venv_and_cache(we, tmp_home):
    ws = tmp_home / "workspace"
    ws.mkdir(exist_ok=True)
    (ws / ".venv").mkdir()
    (ws / ".venv" / "pkg.py").write_text("# venv")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "cache.py").write_text("# cache")
    (ws / "real.py").write_text("print('real')")

    listing = we.execute("list")
    assert "real.py" in listing
    assert ".venv" not in listing
    assert "__pycache__" not in listing


def test_list_includes_json_yaml(we, tmp_home):
    we.execute("write", "config.json", '{"x": 1}')
    we.execute("write", "data.yaml", "x: 1")
    listing = we.execute("list")
    assert "config.json" in listing
    assert "data.yaml" in listing


def test_list_empty_workspace(we, tmp_home):
    listing = we.execute("list")
    assert "leer" in listing.lower()


# ---------- Edge cases ----------

def test_empty_path(we, tmp_home):
    assert "Fehler" in we.execute("read", "")
    assert "Fehler" in we.execute("write", "", "x")


def test_write_without_content(we, tmp_home):
    assert "Fehler" in we.execute("write", "x.py", None)


def test_no_bak_files_in_workspace(we, tmp_home):
    """Anders als file_edit: workspace macht keine Backups."""
    we.execute("write", "x.py", "v1")
    we.execute("write", "x.py", "v2")
    assert not (tmp_home / "workspace" / "x.py.bak").exists()
    assert (tmp_home / "workspace" / "x.py").read_text() == "v2"


# ---------- str_replace ----------

def test_str_replace_basic(we, tmp_home):
    we.execute("write", "hello.py", "print('hi')\nprint('bye')")
    out = we.execute("str_replace", "hello.py",
                     old_str="'hi'", new_str="'hallo'")
    assert "gepatcht" in out
    assert we.execute("read", "hello.py") == "print('hallo')\nprint('bye')"


def test_str_replace_ambiguous_refused(we, tmp_home):
    we.execute("write", "x.py", "x = 1\nx = 2\nx = 3")
    out = we.execute("str_replace", "x.py", old_str="x = ", new_str="y = ")
    assert "eindeutig" in out.lower() or "3-mal" in out


def test_str_replace_no_backup(we, tmp_home):
    we.execute("write", "x.py", "foo")
    we.execute("str_replace", "x.py", old_str="foo", new_str="bar")
    assert not (tmp_home / "workspace" / "x.py.bak").exists()


def test_str_replace_not_found_hint(we, tmp_home):
    we.execute("write", "x.py", "print('hi')")
    out = we.execute("str_replace", "x.py",
                     old_str="print('bye')", new_str="x")
    assert "nicht in der Datei" in out
    assert "read" in out.lower()


# ---------- TOOL_DEFINITION ----------

def test_tool_definition_shape(we):
    td = we.TOOL_DEFINITION
    assert td["function"]["name"] == "workspace_edit"
    assert "workspace" in td["function"]["description"].lower()
    # Muss klar auf Unterschied zu file_edit hinweisen
    assert "file_edit" in td["function"]["description"]
