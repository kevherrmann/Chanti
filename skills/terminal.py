"""Skill: Bash-Befehle sicher im Workspace ausführen.

Sandbox: ~/chanti/workspace/   (wird bei Bedarf angelegt)
Whitelist: nur harmlose Tools (siehe ALLOWED_COMMANDS).
Timeout:   30 s pro Befehl.
Output:    auf MAX_OUTPUT_CHARS pro Stream gekürzt.

Der Befehl wird NICHT über eine Shell ausgeführt (shell=False) —
damit sind `|`, `>`, `;`, `&&`, `$(...)`, Backticks und Glob-Expansion
inaktiv. Das ist Absicht: sie öffnen zu viele Schlupflöcher
(rm -rf via Alias, curl via env, ...). Wenn ein Agent eine Pipeline
braucht, muss er sie in ein Python-Script schreiben und das aufrufen.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger("chanti")

BASE = Path.home() / "chanti"
WORKSPACE = BASE / "workspace"

TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 10_000
# Harte Obergrenze für das gesamte Kommando — schützt vor absurd langen
# Argument-Listen die der LLM halluziniert.
MAX_COMMAND_LEN = 4_000

# Whitelist: nur diese Programmnamen (argv[0]) sind erlaubt.
# Bewusst klein gehalten. Alles was Netzwerk macht, Rechte ändert oder
# großflächig löscht bleibt draußen.
ALLOWED_COMMANDS = frozenset({
    # Python / Node Ausführung
    "python", "python3", "pip", "pip3",
    "node", "npm", "npx",
    # Dateisystem lesen
    "ls", "cat", "head", "tail", "wc", "file", "stat",
    "find", "grep", "rg", "tree", "pwd", "echo",
    # Dateisystem schreiben (nicht-destruktiv)
    "mkdir", "cp", "mv", "touch",
    # Dev-Tools
    "pytest", "black", "ruff", "mypy", "flake8",
    "git",  # nur lesend sinnvoll, aber schreiben wäre in workspace/ auch ok
    # Test-Helfer
    "true", "false", "which",
})

# Harte Blockliste von Argument-Tokens. Wird gegen jedes argv[i] (i>=1)
# geprüft. Fängt Dinge ab, die auch mit erlaubten Programmen gefährlich
# wären: `git config --global`, `pip install -e /etc`, absolute Pfade
# außerhalb des Workspaces.
FORBIDDEN_ARG_SUBSTRINGS = (
    "--global",          # git config --global etc.
    "/etc/", "/root/", "/boot/", "/proc/", "/sys/",
    "/var/", "/usr/", "/bin/", "/sbin/", "/dev/",
)

# Gefährliche Flags pro Programm. Leichter Defense-in-Depth —
# die eigentliche Absicherung ist die Sandbox.
FORBIDDEN_FLAGS = {
    "pip":  ("--target", "--prefix", "--root"),
    "pip3": ("--target", "--prefix", "--root"),
    "npm":  ("-g", "--global", "--prefix"),
    "rm":   ("-rf", "-fr", "-r", "-f"),  # rm ist eh nicht whitelisted,
                                         # aber falls jemand es doch freischaltet.
}

# Blocklist für rm-artige Subcommands die auch über erlaubte Tools
# erreichbar wären (z.B. `git clean -fdx`).
FORBIDDEN_SUBCOMMANDS = {
    "git": {"clean", "reset"},  # reset --hard wäre ärgerlich
}


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": (
            "Führt EINEN Shell-Befehl in einer Sandbox unter ~/chanti/workspace/ aus "
            "und gibt stdout, stderr und exit_code zurück.\n\n"
            "WICHTIGE REGELN (sonst Fehlschlag):\n"
            "• Keine Shell-Features: kein |, >, <, ;, &&, $(), Backticks.\n"
            "• Kein `cd`. Jeder Call ist ein eigener Prozess — nutze stattdessen den cwd-Parameter.\n"
            "• Keine ~-Expansion. Pfade sind IMMER relativ zu ~/chanti/workspace/ (das ist das Default-cwd).\n"
            "  Also: command='ls' und cwd='src', NICHT command='ls ~/chanti/workspace/src'.\n"
            "• Zum Datei-Anlegen/Schreiben nimm lieber das Tool `file_edit` (zuverlässiger als `touch` + `echo`).\n"
            "  Nach file_edit liegt die Datei in ~/chanti/, nicht im workspace — kopiere sie mit cp wenn nötig.\n"
            "• Nur Whitelist-Programme: python3, node, npm, pip, pytest, ls, cat, mkdir, cp, mv, grep, find, git, ...\n"
            "• Timeout 30 s, Output auf 10.000 Zeichen pro Stream gekürzt.\n\n"
            "RÜCKGABE: Erste Zeile ist der Status — 'ERFOLGREICH (exit_code=0)' bei Erfolg, "
            "'FEHLGESCHLAGEN' oder 'ABGEBROCHEN' sonst. "
            "Bei Erfolg NICHT den Befehl wiederholen — nimm den stdout als Antwort.\n\n"
            "BEISPIELE:\n"
            "  ✓ {command: 'python3 hello.py'}\n"
            "  ✓ {command: 'pytest -v', cwd: 'myproject'}\n"
            "  ✓ {command: 'ls', cwd: 'src'}\n"
            "  ✗ {command: 'cd src && ls'}              → &&  verboten, nutze cwd\n"
            "  ✗ {command: 'echo foo > out.txt'}        → >   verboten, nutze file_edit\n"
            "  ✗ {command: 'ls ~/chanti/workspace'}     → ~   wird nicht expandiert"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Der auszuführende Befehl, z.B. 'python3 test.py' oder 'pytest -v'. "
                        "Argumente werden wie in einer Shell getrennt (Anführungszeichen beachten)."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Optional: Arbeitsverzeichnis relativ zu ~/chanti/workspace/. "
                        "Default ist das Workspace-Root."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}


# ---------- Hilfsfunktionen ----------

def _ensure_workspace() -> Path:
    """Legt ~/chanti/workspace/ an, falls nötig. Gibt resolved Path zurück."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return WORKSPACE.resolve()


def _resolve_cwd(cwd: str | None) -> Path:
    """Löst cwd relativ zu WORKSPACE auf und verifiziert, dass es drin liegt.

    Tolerant gegenüber zwei häufigen Modell-Eingaben:
    - '~/chanti/workspace/foo' → wird zu 'foo' umgedeutet
    - '' / None / 'workspace' / '.' → Workspace-Root
    """
    ws = _ensure_workspace()
    if not cwd or not cwd.strip() or cwd.strip() in (".", "./"):
        return ws

    cwd = cwd.strip()

    # Tilde freundlich umdeuten statt hart abweisen.
    if cwd.startswith("~/chanti/workspace"):
        cwd = cwd[len("~/chanti/workspace"):].lstrip("/")
        if not cwd:
            return ws
    elif cwd.startswith("~"):
        raise PermissionError(
            "`~` wird nicht expandiert. cwd ist relativ zu ~/chanti/workspace/ "
            "— nutze z.B. 'src' statt '~/chanti/workspace/src'."
        )

    p = Path(cwd)
    if p.is_absolute():
        raise PermissionError("cwd muss relativ zu ~/chanti/workspace/ sein.")
    if ".." in p.parts:
        raise PermissionError("'..' in cwd nicht erlaubt.")

    target = (ws / p).resolve(strict=False)
    try:
        target.relative_to(ws)
    except ValueError:
        raise PermissionError("cwd liegt außerhalb des Workspaces.")

    if target.is_symlink():
        raise PermissionError("cwd darf kein Symlink sein.")
    if not target.exists():
        raise FileNotFoundError(f"cwd existiert nicht: {cwd}")
    if not target.is_dir():
        raise NotADirectoryError(f"cwd ist kein Verzeichnis: {cwd}")
    return target


def _validate_argv(argv: list[str]) -> None:
    """Wirft PermissionError wenn etwas nicht passt."""
    if not argv:
        raise ValueError("Leerer Befehl.")

    prog = argv[0]

    # `cd` ist ein Shell-Builtin und wäre als eigener Prozess sinnlos
    # (CWD persistiert nicht zwischen Tool-Calls). Lieber klarer Hinweis.
    if prog == "cd":
        raise PermissionError(
            "`cd` wirkungslos — jeder Tool-Call ist ein eigener Prozess. "
            "Nutze stattdessen den `cwd`-Parameter von terminal."
        )

    # Programmpfade ablehnen — nur blanker Name. Verhindert /bin/sh-Tricks.
    if "/" in prog or prog.startswith("."):
        raise PermissionError(
            f"Nur Programmnamen erlaubt, keine Pfade: {prog!r}"
        )
    if prog not in ALLOWED_COMMANDS:
        raise PermissionError(
            f"Befehl nicht erlaubt: {prog!r}. "
            f"Erlaubt: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )

    # Verbotene Flags für genau dieses Programm?
    for bad in FORBIDDEN_FLAGS.get(prog, ()):
        if bad in argv[1:]:
            raise PermissionError(f"Flag {bad!r} bei {prog!r} nicht erlaubt.")

    # Verbotene Subcommands (z.B. `git clean`)?
    forbidden_subs = FORBIDDEN_SUBCOMMANDS.get(prog)
    if forbidden_subs and len(argv) >= 2 and argv[1] in forbidden_subs:
        raise PermissionError(f"Subcommand {prog} {argv[1]} nicht erlaubt.")

    # Globale Argument-Substring-Blocklist
    for arg in argv[1:]:
        for bad in FORBIDDEN_ARG_SUBSTRINGS:
            if bad in arg:
                raise PermissionError(
                    f"Argument enthält verbotenes Muster {bad!r}: {arg!r}"
                )

    # `~` wird NICHT expandiert (shell=False). Wenn jemand ~/foo schreibt
    # landet das wörtlich als Ordnername. Lieber früh Bescheid geben.
    for arg in argv[1:]:
        if arg == "~" or arg.startswith("~/") or arg.startswith("~"):
            raise PermissionError(
                f"`~` wird nicht expandiert: {arg!r}. "
                f"Pfade sind relativ zu ~/chanti/workspace/ — nutze z.B. 'src/foo' "
                f"oder setze den `cwd`-Parameter."
            )


def _truncate(text: str, label: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    cut = text[:MAX_OUTPUT_CHARS]
    return cut + f"\n... [{label} gekürzt: {len(text) - MAX_OUTPUT_CHARS} weitere Zeichen]"


def _format_result(cmd_display: str, exit_code: int | str,
                   stdout: str, stderr: str) -> str:
    stdout = _truncate(stdout, "stdout")
    stderr = _truncate(stderr, "stderr")

    # Status-Zeile am Anfang, damit kleine LLMs sofort sehen dass es geklappt hat
    # und nicht erneut probieren. `exit_code=0` ist das eindeutige Erfolgs-Signal.
    if exit_code == 0:
        status = "ERFOLGREICH (exit_code=0)"
    elif isinstance(exit_code, str) and "timeout" in exit_code:
        status = f"ABGEBROCHEN ({exit_code})"
    else:
        status = f"FEHLGESCHLAGEN (exit_code={exit_code})"

    parts = [
        status,
        f"$ {cmd_display}",
        "--- stdout ---",
        stdout if stdout else "(leer)",
        "--- stderr ---",
        stderr if stderr else "(leer)",
    ]
    return "\n".join(parts)


# ---------- Entry point ----------

def execute(command: str, cwd: str | None = None) -> str:
    if not command or not command.strip():
        return "Fehler: Kein Befehl angegeben."
    if len(command) > MAX_COMMAND_LEN:
        return f"Fehler: Befehl zu lang (>{MAX_COMMAND_LEN} Zeichen)."

    # Shell-Metazeichen ablehnen — wir fahren bewusst shell=False.
    # Auch in Einzelargumenten nicht, aber shlex würde `>` z.B. als
    # eigenes Argument abspalten und so durchlassen. Deshalb früh blocken.
    # Fehlermeldung enthält Handlungsanweisung — sonst probiert der Agent
    # endlos Varianten durch.
    _META_HINT = {
        "|": "Pipes gehen nicht. Ruf die Befehle einzeln auf oder nutze ein Python-Script.",
        ">": "Redirects gehen nicht. Zum Datei-Schreiben nutze das Tool `file_edit`.",
        "<": "Redirects gehen nicht.",
        ";": "Mehrere Befehle gehen nicht. Ruf sie nacheinander in getrennten Tool-Calls auf.",
        "&": "`&&` / `&` gehen nicht. Ruf die Befehle nacheinander in getrennten Tool-Calls auf.",
        "$(": "Command-Substitution geht nicht.",
        "`": "Backticks gehen nicht.",
        "\n": "Mehrzeilige Befehle gehen nicht.",
    }
    for meta, hint in _META_HINT.items():
        if meta in command:
            return f"Abgelehnt: Shell-Metazeichen {meta!r} nicht erlaubt. {hint}"

    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"Fehler beim Parsen: {e}"

    try:
        _validate_argv(argv)
    except (PermissionError, ValueError) as e:
        return f"Abgelehnt: {e}"

    try:
        work_dir = _resolve_cwd(cwd)
    except (PermissionError, FileNotFoundError, NotADirectoryError) as e:
        return f"Fehler (cwd): {e}"

    # Minimalistische Env — keine API-Keys aus Parent-Env durchreichen.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        # Python: stdout sofort flushen, nicht puffern.
        "PYTHONUNBUFFERED": "1",
        # pip soll nichts nach ~/.cache schreiben wenn möglich — ist aber
        # nicht kritisch. Hauptsache: keine Secrets im Env.
    }

    cmd_display = " ".join(shlex.quote(a) for a in argv)
    logger.info(f"terminal: {cmd_display}  (cwd={work_dir})")

    try:
        proc = subprocess.run(
            argv,
            cwd=str(work_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        logger.warning(f"terminal Timeout nach {TIMEOUT_SECONDS}s: {cmd_display}")
        return _format_result(
            cmd_display,
            f"timeout ({TIMEOUT_SECONDS}s)",
            stdout,
            stderr + f"\n[Prozess nach {TIMEOUT_SECONDS}s abgebrochen]",
        )
    except FileNotFoundError:
        return f"Fehler: Programm {argv[0]!r} nicht im PATH gefunden."
    except OSError as e:
        return f"Fehler beim Starten: {e}"

    return _format_result(cmd_display, proc.returncode, proc.stdout, proc.stderr)
