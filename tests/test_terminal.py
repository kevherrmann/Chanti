"""Tests für terminal.py — Whitelist, Sandbox, Timeout, Output-Truncation."""
import sys
from pathlib import Path

import pytest

from conftest import load_skill


@pytest.fixture
def term(tmp_home):
    """Lädt terminal frisch, damit WORKSPACE auf das Temp-Home zeigt."""
    return load_skill("terminal")


# ---------- Happy path ----------

def test_simple_echo_works(term, tmp_home):
    out = term.execute("echo hallo")
    assert "ERFOLGREICH" in out
    assert "hallo" in out


def test_workspace_is_created(term, tmp_home):
    term.execute("echo x")
    assert (tmp_home / "workspace").is_dir()


def test_cwd_relative(term, tmp_home):
    ws = tmp_home / "workspace"
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "hello.txt").write_text("hi")
    out = term.execute("ls", cwd="sub")
    assert "hello.txt" in out
    assert "ERFOLGREICH" in out


def test_python_runs(term, tmp_home):
    out = term.execute(f"{Path(sys.executable).name} -c 'print(1+1)'")
    # Abhängig vom System ist python3 vielleicht nicht der Interpreter —
    # wir nutzen was auch immer auf PATH liegt. Check lockerer:
    if "nicht im PATH" in out:
        pytest.skip("python3 nicht im Test-PATH")
    assert "ERFOLGREICH" in out
    assert "2" in out


# ---------- Whitelist ----------

def test_rm_blocked(term, tmp_home):
    out = term.execute("rm -rf /")
    assert "Abgelehnt" in out or "nicht erlaubt" in out


def test_sudo_blocked(term, tmp_home):
    out = term.execute("sudo ls")
    assert "Abgelehnt" in out or "nicht erlaubt" in out


def test_curl_blocked(term, tmp_home):
    out = term.execute("curl http://evil.example.com")
    assert "Abgelehnt" in out or "nicht erlaubt" in out


def test_chmod_blocked(term, tmp_home):
    out = term.execute("chmod 777 foo")
    assert "Abgelehnt" in out or "nicht erlaubt" in out


def test_absolute_program_path_blocked(term, tmp_home):
    out = term.execute("/bin/ls")
    assert "Abgelehnt" in out or "Pfade" in out


# ---------- Shell-Metazeichen ----------

@pytest.mark.parametrize("cmd", [
    "ls | cat",
    "ls > out.txt",
    "ls ; pwd",
    "ls && pwd",
    "echo $(whoami)",
    "echo `whoami`",
])
def test_shell_metachars_blocked(term, tmp_home, cmd):
    out = term.execute(cmd)
    assert "abgelehnt" in out.lower() or "nicht erlaubt" in out.lower()


def test_cd_has_helpful_hint(term, tmp_home):
    out = term.execute("cd foo")
    assert "cwd" in out.lower()  # Hinweis zeigt auf den Parameter


def test_tilde_in_arg_has_hint(term, tmp_home):
    out = term.execute("mkdir ~/chanti/foo")
    assert "nicht expandiert" in out.lower() or "relativ" in out.lower()


def test_metachar_hint_points_to_file_edit(term, tmp_home):
    # > soll auf file_edit verweisen, damit der Agent weiterkommt
    out = term.execute("echo hi > foo.txt")
    assert "file_edit" in out.lower()


def test_cwd_with_tilde_workspace_is_normalized(term, tmp_home):
    # Häufiger Modell-Fehler: cwd='~/chanti/workspace/' statt leer
    out = term.execute("pwd", cwd="~/chanti/workspace/")
    assert "ERFOLGREICH" in out


def test_cwd_with_tilde_subpath_is_normalized(term, tmp_home):
    (tmp_home / "workspace" / "sub").mkdir(parents=True)
    out = term.execute("pwd", cwd="~/chanti/workspace/sub")
    assert "ERFOLGREICH" in out
    assert "sub" in out


# ---------- cwd ----------

def test_cwd_absolute_blocked(term, tmp_home):
    out = term.execute("ls", cwd="/etc")
    assert "Fehler" in out or "relativ" in out


def test_cwd_escape_blocked(term, tmp_home):
    out = term.execute("ls", cwd="../../")
    assert "Fehler" in out or "nicht erlaubt" in out


def test_cwd_nonexistent(term, tmp_home):
    out = term.execute("ls", cwd="does/not/exist")
    assert "Fehler" in out


def test_cwd_symlink_blocked(term, tmp_home):
    ws = tmp_home / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    outside = tmp_home.parent / "outside_ws"
    outside.mkdir(exist_ok=True)
    (ws / "link").symlink_to(outside)
    out = term.execute("ls", cwd="link")
    assert "Fehler" in out or "Symlink" in out


# ---------- Argument-Blocklist ----------

def test_pip_target_blocked(term, tmp_home):
    out = term.execute("pip install --target /etc/foo something")
    assert "Abgelehnt" in out or "nicht erlaubt" in out


def test_git_clean_blocked(term, tmp_home):
    out = term.execute("git clean -fdx")
    assert "Abgelehnt" in out or "nicht erlaubt" in out


def test_etc_path_in_arg_blocked(term, tmp_home):
    out = term.execute("cat /etc/passwd")
    assert "Abgelehnt" in out or "nicht erlaubt" in out


# ---------- Timeout ----------

def test_timeout_enforced(term, tmp_home, monkeypatch):
    # Timeout runterschrauben damit der Test schnell läuft.
    # Schleife ohne `;` — die Metazeichen-Sperre würde sonst zuerst greifen.
    monkeypatch.setattr(term, "TIMEOUT_SECONDS", 1)
    code = "__import__('time').sleep(5)"
    out = term.execute(f"python3 -c {code!r}")
    if "nicht im PATH" in out:
        pytest.skip("python3 nicht im Test-PATH")
    assert "timeout" in out.lower()


# ---------- Output-Truncation ----------

def test_output_truncated(term, tmp_home, monkeypatch):
    monkeypatch.setattr(term, "MAX_OUTPUT_CHARS", 50)
    out = term.execute("python3 -c 'print(\"x\"*1000)'")
    if "nicht im PATH" in out:
        pytest.skip("python3 nicht im Test-PATH")
    assert "gekürzt" in out


# ---------- Empty / malformed ----------

def test_empty_command(term, tmp_home):
    assert "Fehler" in term.execute("")
    assert "Fehler" in term.execute("   ")


def test_unclosed_quote(term, tmp_home):
    out = term.execute('echo "unclosed')
    assert "Fehler" in out


# ---------- Format ----------

def test_result_has_stdout_stderr_exit(term, tmp_home):
    out = term.execute("echo hi")
    assert ("ERFOLGREICH" in out or "FEHLGESCHLAGEN" in out or "ABGEBROCHEN" in out)
    assert "--- stdout ---" in out
    assert "--- stderr ---" in out


def test_nonzero_exit_reported(term, tmp_home):
    out = term.execute("false")
    assert "FEHLGESCHLAGEN" in out and "exit_code=1" in out


# ---------- TOOL_DEFINITION sanity ----------

def test_tool_definition_shape(term):
    td = term.TOOL_DEFINITION
    assert td["type"] == "function"
    assert td["function"]["name"] == "terminal"
    props = td["function"]["parameters"]["properties"]
    assert "command" in props
    assert "cwd" in props
    assert td["function"]["parameters"]["required"] == ["command"]
