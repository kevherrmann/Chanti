"""Skill: Chantis eigene Konfigurations-Dateien lesen/schreiben (~/chanti/).

Dieses Tool ist für Chantis *Identität*: SOUL.md, USER.md, MEMORY.md,
Skills, Config. Für Code den sie schreibt oder Experimente — nimm das
workspace_edit-Tool. workspace/ ist in diesem Tool absichtlich gesperrt,
damit Identitäts-Files und User-Code sauber getrennt bleiben.
"""
from pathlib import Path
import logging
import os

logger = logging.getLogger("chanti")


def _default_base() -> Path:
    explicit = os.environ.get("CHANTI_HOME") or os.environ.get("CHANTI_BASE")
    if explicit:
        return Path(explicit).expanduser()
    home_base = Path.home() / "chanti"
    if os.environ.get("PYTEST_CURRENT_TEST") and home_base.exists():
        return home_base
    if (home_base / "SOUL.md").exists():
        return home_base
    return Path(__file__).resolve().parents[1]


BASE = _default_base()
# Harte Obergrenze für einzelne Write-Calls: 2 MB.
# Schützt vor Disk-Full-DoS durch Halluzinationen oder Prompt-Injection.
MAX_WRITE_BYTES = 2 * 1024 * 1024
# Verzeichnisse die bei `list` ausgeschlossen werden.
# workspace/ ist Domain von workspace_edit — hier nicht zeigen damit Chanti
# die Trennung auch visuell versteht.
LIST_EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", "node_modules",
    ".git", "data", "workspace",
}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "file_edit",
        "description": (
            "Liest/schreibt Chantis eigene Konfigurationsdateien in ~/chanti/ "
            "(SOUL.md, USER.md, MEMORY.md, IDENTITY.md, TOOLS.md, skills/*.py, ...). "
            "NICHT für Code den du schreibst oder testest — dafür gibt es `workspace_edit`. "
            "Pfade sind relativ zu ~/chanti/, z.B. 'SOUL.md' oder 'skills/web_browse.py'. "
            "Pfade die mit 'workspace/' beginnen werden abgelehnt.\n\n"
            "Actions: read (ganze Datei), write (ganze Datei ersetzen), "
            "str_replace (nur einen Textblock ersetzen — effizienter für kleine Änderungen), "
            "list (alle Dateien)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "str_replace", "list"],
                    "description": (
                        "read = Datei lesen, write = ganze Datei ersetzen, "
                        "str_replace = Textblock ersetzen (braucht old_str + new_str), "
                        "list = Dateien auflisten"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Relativer Pfad innerhalb ~/chanti/, z.B. 'SOUL.md' oder 'skills/web_browse.py'. Identitäts-Dateien sind GROSSGESCHRIEBEN: SOUL.md, USER.md, MEMORY.md, IDENTITY.md, TOOLS.md"
                },
                "content": {
                    "type": "string",
                    "description": "Neuer Inhalt beim Schreiben (nur bei action=write). Maximal 2 MB."
                },
                "old_str": {
                    "type": "string",
                    "description": "Bei action=str_replace: der EXAKTE Textblock der ersetzt werden soll. Muss in der Datei genau EINMAL vorkommen, sonst wird abgelehnt."
                },
                "new_str": {
                    "type": "string",
                    "description": "Bei action=str_replace: der neue Text. Leer = Löschen."
                }
            },
            "required": ["action"]
        }
    }
}


def _inside_base(p: Path) -> bool:
    """Prüft ob ein Pfad (nach resolve) innerhalb BASE liegt."""
    try:
        p.resolve(strict=False).relative_to(BASE.resolve())
        return True
    except ValueError:
        return False


def _resolve_path(path: str) -> Path:
    """Findet die Datei case-insensitiv mit striktem Path-Traversal- und Symlink-Schutz."""
    if not path or not path.strip():
        raise ValueError("Leerer Pfad.")

    path = path.strip()

    # `~` wird NICHT expandiert. Entweder wir deuten den häufigen Modell-Fehler
    # `~/chanti/...` freundlich um, oder wir weisen mit klarem Hinweis ab.
    # Sonst landet wörtlich ein Ordner namens `~` in ~/chanti/.
    if path.startswith("~/chanti/"):
        path = path[len("~/chanti/"):]
        if not path:
            raise ValueError("Leerer Pfad nach ~/chanti/.")
    elif path.startswith("~"):
        raise PermissionError(
            "`~` wird nicht expandiert. Pfade sind relativ zu ~/chanti/ "
            "— also z.B. 'SOUL.md' oder 'workspace/hello.py'."
        )

    # Absolute Pfade und '..' direkt ablehnen — nur relativ zu BASE.
    p = Path(path)
    if p.is_absolute():
        raise PermissionError("Zugriff verweigert: Absolute Pfade nicht erlaubt.")
    if ".." in p.parts:
        raise PermissionError("Zugriff verweigert: '..' nicht erlaubt.")

    # workspace/ gehört workspace_edit — hier sperren, damit Identität und
    # User-Code getrennt bleiben.
    if p.parts and p.parts[0] == "workspace":
        raise PermissionError(
            "Zugriff verweigert: 'workspace/' gehört dem workspace_edit-Tool. "
            "Nutze `workspace_edit` für Code-Dateien, `file_edit` nur für "
            "Chantis eigene Config (SOUL.md, skills/, etc.)."
        )

    target = (BASE / p).resolve(strict=False)

    # 1) Pfad muss innerhalb BASE liegen (nach resolve, inkl. Symlink-Auflösung).
    if not _inside_base(target):
        raise PermissionError("Zugriff verweigert: Nur Dateien innerhalb ~/chanti/ erlaubt.")

    # 2) Weder das Ziel selbst noch ein Zwischenverzeichnis darf ein Symlink sein.
    if target.is_symlink():
        raise PermissionError("Zugriff verweigert: Symlinks nicht erlaubt.")
    base_resolved = BASE.resolve()
    parent = target.parent
    while True:
        if parent == base_resolved or parent == parent.parent:
            break
        if parent.is_symlink():
            raise PermissionError("Zugriff verweigert: Symlinks im Pfad nicht erlaubt.")
        parent = parent.parent

    # Wenn Datei direkt gefunden
    if target.exists():
        return target

    # Case-insensitive Suche im Ziel-Ordner
    name_lower = p.name.lower()
    search_dir = target.parent
    if search_dir.exists():
        for f in search_dir.iterdir():
            if f.is_symlink():
                continue
            if f.name.lower() == name_lower:
                return f
    return target  # Nicht gefunden – original zurückgeben (für write)


def _list_files() -> str:
    out = []
    base_resolved = BASE.resolve()
    for root, dirs, files in os.walk(BASE, followlinks=False):
        # Exclude-Dirs in-place filtern, damit os.walk nicht absteigt
        dirs[:] = [d for d in dirs if d not in LIST_EXCLUDE_DIRS and not d.startswith(".")]
        root_path = Path(root)
        try:
            root_path.resolve().relative_to(base_resolved)
        except ValueError:
            continue
        for f in files:
            if not f.endswith((".py", ".md", ".sh")):
                continue
            if f.endswith(".bak"):
                continue
            full = root_path / f
            # Symlinks in der Liste ausblenden — sie sind eh nicht lesbar.
            if full.is_symlink():
                continue
            rel = full.relative_to(BASE)
            out.append(str(rel))
    out.sort()
    return "\n".join(out) if out else "Keine Dateien gefunden."


def _do_str_replace(target: Path, old_str: str, new_str: str) -> str:
    """Führt str_replace auf target aus. Strikte Regel: old_str muss genau
    einmal in der Datei vorkommen. Erst dann ist die Ersetzung eindeutig."""
    if old_str is None or old_str == "":
        return "Fehler: old_str darf nicht leer sein."
    if new_str is None:
        new_str = ""

    if not target.exists():
        return f"Datei nicht gefunden: {target.relative_to(BASE)}"
    if not target.is_file():
        return f"Kein regulärer Datei-Pfad: {target.relative_to(BASE)}"

    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "Datei ist keine UTF-8 Text-Datei."
    except OSError as e:
        return f"Fehler beim Lesen: {e}"

    count = text.count(old_str)
    if count == 0:
        # Kurzen Ausschnitt zurückgeben damit der Agent weiß was tatsächlich
        # in der Datei steht — sonst rät er im Blindflug.
        return (f"old_str nicht in der Datei gefunden. "
                f"Tipp: die Datei ist {len(text)} Zeichen lang. "
                f"Nutze 'read' um den exakten Text zu sehen.")
    if count > 1:
        return (f"old_str kommt {count}-mal vor — muss eindeutig sein. "
                f"Nimm einen größeren Textblock mit mehr Kontext.")

    new_content = text.replace(old_str, new_str, 1)
    new_bytes = new_content.encode("utf-8")
    if len(new_bytes) > MAX_WRITE_BYTES:
        return f"Fehler: Datei würde zu groß werden ({len(new_bytes)} Bytes)."

    # Backup + atomic write — gleiches Muster wie bei action=write.
    try:
        backup = target.with_suffix(target.suffix + ".bak")
        backup.write_bytes(target.read_bytes())
    except OSError as e:
        logger.warning(f"Backup fehlgeschlagen ({target.name}): {e}")

    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(new_bytes)
        tmp.replace(target)
    except OSError as e:
        return f"Fehler beim Schreiben: {e}"

    logger.info(f"Datei gepatcht: {target.relative_to(BASE)} "
                f"(-{len(old_str)} +{len(new_str)} Zeichen)")
    return (f"Datei gepatcht: {target.name} "
            f"(-{len(old_str)} +{len(new_str)} Zeichen)")


def execute(action: str, path: str = None, content: str = None,
            old_str: str = None, new_str: str = None) -> str:
    if action == "list":
        return _list_files()

    if not path:
        return "Fehler: Kein Pfad angegeben."

    try:
        target = _resolve_path(path)
    except (PermissionError, ValueError) as e:
        return str(e)

    if action == "read":
        if not target.exists():
            return f"Datei nicht gefunden: {path}"
        if not target.is_file():
            return f"Kein regulärer Datei-Pfad: {path}"
        try:
            text = target.read_text(encoding="utf-8")
            logger.info(f"Datei gelesen: {target.relative_to(BASE)} ({len(text)} Zeichen)")
            return text
        except UnicodeDecodeError:
            return f"Datei {path} ist keine UTF-8 Text-Datei."
        except OSError as e:
            return f"Fehler beim Lesen: {e}"

    if action == "str_replace":
        return _do_str_replace(target, old_str, new_str)

    if action == "write":
        if content is None:
            return "Fehler: Kein Inhalt zum Schreiben angegeben."

        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_WRITE_BYTES:
            return (f"Fehler: Inhalt zu groß "
                    f"({len(content_bytes)} Bytes, Limit {MAX_WRITE_BYTES}).")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"Fehler beim Anlegen des Ordners: {e}"

        if not _inside_base(target):
            return "Zugriff verweigert: Ziel liegt außerhalb ~/chanti/."

        # Bestehende Datei sichern.
        if target.exists() and target.is_file():
            try:
                backup = target.with_suffix(target.suffix + ".bak")
                backup.write_bytes(target.read_bytes())
            except OSError as e:
                logger.warning(f"Backup fehlgeschlagen ({target.name}): {e}")

        try:
            # Atomic write: erst in Temp, dann umbenennen.
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(content_bytes)
            tmp.replace(target)
        except OSError as e:
            return f"Fehler beim Schreiben: {e}"

        logger.info(f"Datei gespeichert: {target.relative_to(BASE)} ({len(content_bytes)} Bytes)")
        return f"Datei gespeichert: {target.name} ({len(content)} Zeichen)"

    return f"Unbekannte Aktion: {action}"
