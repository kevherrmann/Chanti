"""Skill: Eigene Dateien lesen und schreiben (nur ~/chanti/)"""
from pathlib import Path

BASE = Path.home() / "chanti"

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "file_edit",
        "description": "Liest oder schreibt Dateien in Chantis eigenem Verzeichnis (~/chanti/). Nutze dies um deine eigene SOUL.md, USER.md, MEMORY.md, Skills oder andere Konfigurationsdateien zu lesen und zu verbessern.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "list"],
                    "description": "read = Datei lesen, write = Datei schreiben, list = Dateien auflisten"
                },
                "path": {
                    "type": "string",
                    "description": "Relativer Pfad innerhalb ~/chanti/, z.B. 'SOUL.md' oder 'skills/web_browse.py'. Dateinamen sind GROSSGESCHRIEBEN: SOUL.md, USER.md, MEMORY.md, IDENTITY.md, TOOLS.md"
                },
                "content": {
                    "type": "string",
                    "description": "Neuer Inhalt beim Schreiben (nur bei action=write)"
                }
            },
            "required": ["action"]
        }
    }
}

def _resolve_path(path: str) -> Path:
    """Findet die Datei case-insensitiv."""
    target = (BASE / path).resolve()
    # Sicherheitscheck
    if not str(target).startswith(str(BASE.resolve())):
        raise PermissionError("Zugriff verweigert: Nur Dateien innerhalb ~/chanti/ erlaubt.")
    # Wenn Datei direkt gefunden
    if target.exists():
        return target
    # Case-insensitive Suche
    name_lower = Path(path).name.lower()
    search_dir = target.parent
    if search_dir.exists():
        for f in search_dir.iterdir():
            if f.name.lower() == name_lower:
                return f
    return target  # Nicht gefunden – original zurückgeben (für write)


def execute(action: str, path: str = None, content: str = None) -> str:
    if action == "list":
        files = sorted(BASE.rglob("*.py")) + sorted(BASE.rglob("*.md")) + sorted(BASE.rglob("*.sh"))
        return "\n".join(str(f.relative_to(BASE)) for f in files if ".bak" not in str(f))

    if not path:
        return "Fehler: Kein Pfad angegeben."

    try:
        target = _resolve_path(path)
    except PermissionError as e:
        return str(e)

    if action == "read":
        if not target.exists():
            return f"Datei nicht gefunden: {path}"
        return target.read_text(encoding="utf-8")

    if action == "write":
        if content is None:
            return "Fehler: Kein Inhalt zum Schreiben angegeben."
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_suffix(target.suffix + ".bak")
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(content, encoding="utf-8")
        return f"Datei gespeichert: {target.name} ({len(content)} Zeichen)"

    return f"Unbekannte Aktion: {action}"
