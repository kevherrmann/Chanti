from pathlib import Path
from datetime import date
import re

BASE = Path.home() / "chanti"

USER_FILE   = BASE / "USER.md"
MEMORY_FILE = BASE / "MEMORY.md"
SOUL_FILE   = BASE / "SOUL.md"
IDENTITY_FILE = BASE / "IDENTITY.md"
TOOLS_FILE  = BASE / "TOOLS.md"
LOG_DIR     = BASE / "memory"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def load_system_prompt() -> str:
    """Kombiniert alle MD-Dateien zum System-Prompt."""
    parts = [
        _read(SOUL_FILE),
        _read(IDENTITY_FILE),
        f"## Was Chanti über Kevin weiß\n{_read(USER_FILE)}",
        f"## Langzeit-Gedächtnis\n{_read(MEMORY_FILE)}",
        _read(TOOLS_FILE),
    ]
    return "\n\n---\n\n".join(p for p in parts if p)


# ── USER.md ──────────────────────────────────────────────────────────────────

def _read_user_facts() -> list[str]:
    if not USER_FILE.exists():
        return []
    lines = USER_FILE.read_text(encoding="utf-8").splitlines()
    return [l for l in lines if l.startswith("- ")]


def _write_user_facts(facts: list[str]):
    header = "# USER – Was Chanti über Kevin weiß\n"
    USER_FILE.write_text(header + "\n".join(facts) + "\n", encoding="utf-8")


def add_user_fact(fact: str):
    """Fügt einen Fakt zu USER.md hinzu (max 30, keine Duplikate)."""
    fact = fact.strip().lstrip("- ")
    line = f"- {fact}"
    facts = _read_user_facts()
    if line in facts:
        return
    facts.append(line)
    facts = facts[-30:]
    _write_user_facts(facts)


def correct_user_fact(old: str, new: str):
    """Ersetzt einen veralteten Fakt in USER.md."""
    old_line = f"- {old.strip().lstrip('- ')}"
    new_line = f"- {new.strip().lstrip('- ')}"
    facts = _read_user_facts()
    facts = [new_line if f == old_line else f for f in facts]
    if new_line not in facts:
        facts.append(new_line)
    _write_user_facts(facts)


# ── MEMORY.md ────────────────────────────────────────────────────────────────

def add_memory_event(event: str):
    """Fügt ein Ereignis zu MEMORY.md hinzu."""
    entry = f"- [{date.today().isoformat()}] {event.strip()}"
    current = _read(MEMORY_FILE)
    MEMORY_FILE.write_text(current + "\n" + entry + "\n", encoding="utf-8")


# ── Tages-Log ────────────────────────────────────────────────────────────────

def log_conversation(user_text: str, assistant_text: str):
    """Schreibt einen Gesprächseintrag in memory/YYYY-MM-DD.md."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{date.today().isoformat()}.md"
    entry = f"\n### {date.today().isoformat()}\n**Kevin:** {user_text}\n**Chanti:** {assistant_text}\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(entry)


# ── Parser für Chantis Selbst-Befehle ────────────────────────────────────────

def parse_and_execute_commands(text: str) -> str:
    """
    Sucht in Chantis Antwort nach [MERKE:], [KORRIGIERE:], [EREIGNIS:]
    und führt sie aus. Gibt den bereinigten Text zurück.
    """
    # [MERKE: Fakt]
    for m in re.findall(r'\[MERKE:\s*(.+?)\]', text, re.IGNORECASE):
        add_user_fact(m)

    # [KORRIGIERE: alt → neu]
    for m in re.findall(r'\[KORRIGIERE:\s*(.+?)\s*(?:→|->)\s*(.+?)\]', text, re.IGNORECASE):
        correct_user_fact(m[0], m[1])

    # [EREIGNIS: Beschreibung]
    for m in re.findall(r'\[EREIGNIS:\s*(.+?)\]', text, re.IGNORECASE):
        add_memory_event(m)

    # Befehle aus dem sichtbaren Text entfernen
    clean = re.sub(r'\[(MERKE|KORRIGIERE|EREIGNIS):[^\]]+\]', '', text, flags=re.IGNORECASE)
    return clean.strip()
