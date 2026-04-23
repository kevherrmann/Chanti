"""Skill: Dateien im Workspace lesen/schreiben (~/chanti/workspace/).

Für Code, Experimente, alles was Chanti für Tasks produziert oder testet.
Arbeitet im selben Verzeichnis wie das `terminal`-Tool — damit sind
Schreiben und Ausführen konsistent, ohne dass das Modell Pfade jonglieren
muss. Für Chantis eigene Konfiguration (SOUL.md etc.) gibt es `file_edit`.
"""
from pathlib import Path
import logging
import os

logger = logging.getLogger("chanti")

BASE = Path.home() / "chanti" / "workspace"
MAX_WRITE_BYTES = 2 * 1024 * 1024
# In workspace/ sind .venv / __pycache__ / node_modules besonders häufig
# (Chanti installiert ja Pakete und läuft Tests). Listing ohne Müll zeigt
# dem Modell nur den interessanten Code.
LIST_EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", "node_modules",
    ".git", "dist", "build", ".pytest_cache", ".mypy_cache",
}
# Erweiterte Extension-Liste — im Workspace landet auch JSON/YAML/TOML/Text.
LIST_EXTENSIONS = (
    ".py", ".md", ".sh", ".txt",
    ".json", ".yaml", ".yml", ".toml",
    ".js", ".ts", ".jsx", ".tsx",
    ".html", ".css",
)

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "workspace_edit",
        "description": (
            "Liest/schreibt Dateien im Workspace (~/chanti/workspace/) — "
            "dort wo du Code schreibst, testest und ausführst. "
            "Arbeitet im selben Verzeichnis wie das `terminal`-Tool: "
            "was du hier schreibst kannst du direkt mit terminal ausführen "
            "(z.B. `python3 script.py`). "
            "NICHT für Chantis eigene Config (SOUL.md etc.) — dafür `file_edit`. "
            "Pfade sind relativ zu ~/chanti/workspace/, z.B. 'hello.py' oder 'src/main.py'.\n\n"
            "Actions: read (ganze Datei), write (ganze Datei ersetzen), "
            "str_replace (nur einen Textblock ersetzen — effizienter für Bugfixes), "
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
                        "list = Workspace auflisten"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Relativer Pfad innerhalb ~/chanti/workspace/, z.B. 'hello.py' oder 'src/main.py'. Wird case-sensitiv behandelt."
                },
                "content": {
                    "type": "string",
                    "description": "Neuer Inhalt beim Schreiben (nur bei action=write). Maximal 2 MB."
                },
                "old_str": {
                    "type": "string",
                    "description": "Bei action=str_replace: der EXAKTE Textblock der ersetzt werden soll. Muss in der Datei genau EINMAL vorkommen."
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


def _ensure_base() -> Path:
    """Stellt sicher, dass ~/chanti/workspace/ existiert."""
    BASE.mkdir(parents=True, exist_ok=True)
    return BASE


def _inside_base(p: Path) -> bool:
    try:
        p.resolve(strict=False).relative_to(BASE.resolve())
        return True
    except ValueError:
        return False


def _resolve_path(path: str) -> Path:
    """Path-Traversal- und Symlink-Schutz, freundlich zu `~`-Unfug des Modells."""
    if not path or not path.strip():
        raise ValueError("Leerer Pfad.")

    path = path.strip()
    _ensure_base()

    # Häufige Modell-Eingabe 'workspace/foo.py' oder '~/chanti/workspace/foo.py'
    # tolerant umdeuten statt hart abweisen.
    if path.startswith("~/chanti/workspace/"):
        path = path[len("~/chanti/workspace/"):]
    elif path.startswith("workspace/"):
        path = path[len("workspace/"):]
    elif path.startswith("~"):
        raise PermissionError(
            "`~` wird nicht expandiert. Pfade sind relativ zu ~/chanti/workspace/."
        )

    if not path:
        raise ValueError("Leerer Pfad nach Normalisierung.")

    p = Path(path)
    if p.is_absolute():
        raise PermissionError("Zugriff verweigert: Absolute Pfade nicht erlaubt.")
    if ".." in p.parts:
        raise PermissionError("Zugriff verweigert: '..' nicht erlaubt.")

    target = (BASE / p).resolve(strict=False)
    if not _inside_base(target):
        raise PermissionError("Zugriff verweigert: Nur Dateien innerhalb ~/chanti/workspace/ erlaubt.")

    # Symlink-Check: weder Ziel noch Zwischen-Ordner dürfen Symlinks sein.
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

    return target


def _list_files() -> str:
    _ensure_base()
    out = []
    base_resolved = BASE.resolve()
    for root, dirs, files in os.walk(BASE, followlinks=False):
        dirs[:] = [d for d in dirs if d not in LIST_EXCLUDE_DIRS and not d.startswith(".")]
        root_path = Path(root)
        try:
            root_path.resolve().relative_to(base_resolved)
        except ValueError:
            continue
        for f in files:
            if not f.endswith(LIST_EXTENSIONS):
                continue
            if f.endswith(".bak"):
                continue
            full = root_path / f
            if full.is_symlink():
                continue
            rel = full.relative_to(BASE)
            out.append(str(rel))
    out.sort()
    return "\n".join(out) if out else "Workspace ist leer."


def _do_str_replace(target: Path, old_str: str, new_str: str) -> str:
    """str_replace ohne Backup — im Workspace gehört Versionierung nach git."""
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

    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(new_bytes)
        tmp.replace(target)
    except OSError as e:
        return f"Fehler beim Schreiben: {e}"

    logger.info(f"workspace gepatcht: {target.relative_to(BASE)} "
                f"(-{len(old_str)} +{len(new_str)} Zeichen)")
    return (f"Datei gepatcht: {target.relative_to(BASE)} "
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
            logger.info(f"workspace gelesen: {target.relative_to(BASE)} ({len(text)} Zeichen)")
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
            return "Zugriff verweigert: Ziel liegt außerhalb ~/chanti/workspace/."

        # Anders als file_edit: KEIN .bak-Backup im Workspace. Dort passiert
        # eh viel Rewrite, Backups müllen nur und haben keinen Wert —
        # richtige Versionierung gehört in git.
        try:
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(content_bytes)
            tmp.replace(target)
        except OSError as e:
            return f"Fehler beim Schreiben: {e}"

        logger.info(f"workspace gespeichert: {target.relative_to(BASE)} ({len(content_bytes)} Bytes)")
        return f"Datei gespeichert: {target.relative_to(BASE)} ({len(content)} Zeichen)"

    return f"Unbekannte Aktion: {action}"
