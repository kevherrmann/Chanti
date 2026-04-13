from pathlib import Path
from datetime import date
import re

BASE = Path.home() / "chanti"

USER_FILE     = BASE / "USER.md"
MEMORY_FILE   = BASE / "MEMORY.md"
SOUL_FILE     = BASE / "SOUL.md"
IDENTITY_FILE = BASE / "IDENTITY.md"
TOOLS_FILE    = BASE / "TOOLS.md"
LOG_DIR       = BASE / "memory"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# ── System-Prompt ─────────────────────────────────────────────────────────────

def load_system_prompt() -> str:
    """
    Kompakter System-Prompt: SOUL + USER-Fakten + MEMORY-Ereignisse.
    IDENTITY und TOOLS werden weggelassen um Token zu sparen.
    """
    soul    = _read(SOUL_FILE)
    user    = _read(USER_FILE)
    memory  = _read(MEMORY_FILE)

    parts = [soul]
    if user:
        parts.append(f"## Was du über Kevin weißt\n{user}")
    if memory:
        # Nur die letzten 10 Ereignisse laden
        lines = [l for l in memory.splitlines() if l.strip().startswith("-")]
        recent = "\n".join(lines[-10:])
        if recent:
            parts.append(f"## Wichtige Ereignisse\n{recent}")

    return "\n\n".join(parts)


# ── Tages-Kontext für Chat-History ───────────────────────────────────────────

def load_recent_context(n: int = 3) -> list[dict]:
    """
    Lädt die letzten n Gespräche von heute als Chat-Messages.
    Gibt eine Liste von {"role": ..., "content": ...} zurück.
    Wird VOR der aktuellen Nachricht in die History injiziert.
    """
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{date.today().isoformat()}.md"
    if not log_file.exists():
        return []

    content = log_file.read_text(encoding="utf-8")
    # Blöcke parsen: ### Datum\n**Kevin:** ...\n**Chanti:** ...
    blocks = re.findall(
        r'\*\*Kevin:\*\*\s*(.+?)\n\*\*Chanti:\*\*\s*(.+?)(?=\n###|\Z)',
        content,
        re.DOTALL
    )

    # Nur die letzten n Paare
    recent = blocks[-n:] if len(blocks) >= n else blocks

    messages = []
    for user_msg, assistant_msg in recent:
        messages.append({"role": "user",      "content": user_msg.strip()})
        messages.append({"role": "assistant", "content": assistant_msg.strip()})

    return messages


# ── USER.md ──────────────────────────────────────────────────────────────────

def _read_user_facts() -> list[str]:
    if not USER_FILE.exists():
        return []
    return [l.strip() for l in USER_FILE.read_text(encoding="utf-8").splitlines()
            if l.strip().startswith("- ")]


def _write_user_facts(facts: list[str]):
    header = "# USER – Was Chanti über Kevin weiß\n\n"
    USER_FILE.write_text(header + "\n".join(facts) + "\n", encoding="utf-8")


def _is_duplicate(new: str, existing: list[str]) -> bool:
    """Einfacher Duplikat-Check: exakt oder sehr ähnlich (80% Wort-Overlap)."""
    new_clean = new.lower().lstrip("- ").strip()
    new_words = set(new_clean.split())
    for e in existing:
        e_clean = e.lower().lstrip("- ").strip()
        if new_clean == e_clean:
            return True
        e_words = set(e_clean.split())
        if len(new_words) > 2 and len(e_words) > 2:
            overlap = len(new_words & e_words) / max(len(new_words), len(e_words))
            if overlap >= 0.8:
                return True
    return False


def add_user_fact(fact: str):
    """Fügt einen Fakt zu USER.md hinzu. Max 30, keine Duplikate."""
    fact = fact.strip().lstrip("- ")
    if not fact:
        return
    line = f"- {fact}"
    facts = _read_user_facts()
    if _is_duplicate(line, facts):
        return
    facts.append(line)
    _write_user_facts(facts[-30:])


def correct_user_fact(old: str, new: str):
    """Ersetzt einen veralteten Fakt."""
    old_line = f"- {old.strip().lstrip('- ')}"
    new_line = f"- {new.strip().lstrip('- ')}"
    facts = _read_user_facts()
    replaced = False
    for i, f in enumerate(facts):
        if f == old_line or _is_duplicate(old_line, [f]):
            facts[i] = new_line
            replaced = True
            break
    if not replaced:
        facts.append(new_line)
    _write_user_facts(facts[-30:])


# ── MEMORY.md ────────────────────────────────────────────────────────────────

def add_memory_event(event: str):
    """Fügt ein datiertes Ereignis zu MEMORY.md hinzu."""
    entry = f"- [{date.today().isoformat()}] {event.strip()}"
    current = _read(MEMORY_FILE)
    # Duplikat-Check
    if event.strip().lower() in current.lower():
        return
    MEMORY_FILE.write_text(current + "\n" + entry + "\n", encoding="utf-8")


# ── Tages-Log ────────────────────────────────────────────────────────────────

def log_conversation(user_text: str, assistant_text: str):
    """Schreibt Gespräch in memory/YYYY-MM-DD.md."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{date.today().isoformat()}.md"
    if not log_file.exists():
        log_file.write_text(f"# Log {date.today().isoformat()}\n", encoding="utf-8")
    entry = f"\n### {date.today().isoformat()}\n**Kevin:** {user_text}\n**Chanti:** {assistant_text}\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(entry)


# ── Parser für Chantis Selbst-Befehle ────────────────────────────────────────

def parse_and_execute_commands(text: str) -> str:
    """
    Parst [MERKE:], [KORRIGIERE:], [EREIGNIS:] aus Chantis Antwort,
    führt sie aus und entfernt die Tags aus dem sichtbaren Text.
    """
    for m in re.findall(r'\[MERKE:\s*(.+?)\]', text, re.IGNORECASE):
        add_user_fact(m)

    for m in re.findall(r'\[KORRIGIERE:\s*(.+?)\s*(?:→|->)\s*(.+?)\]', text, re.IGNORECASE):
        correct_user_fact(m[0], m[1])

    for m in re.findall(r'\[EREIGNIS:\s*(.+?)\]', text, re.IGNORECASE):
        add_memory_event(m)

    clean = re.sub(r'\[(MERKE|KORRIGIERE|EREIGNIS):[^\]]+\]', '', text, flags=re.IGNORECASE)
    return clean.strip()


# ── Tages-Logs Cleanup ───────────────────────────────────────────────────────

def cleanup_old_logs(keep_days: int = 30):
    """Löscht Tages-Logs die älter als keep_days sind."""
    from datetime import datetime, timedelta
    if not LOG_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = 0
    for log_file in LOG_DIR.glob("*.md"):
        try:
            file_date = datetime.strptime(log_file.stem, "%Y-%m-%d")
            if file_date < cutoff:
                log_file.unlink()
                deleted += 1
        except ValueError:
            pass
    if deleted:
        print(f"[Chanti] {deleted} alte Log-Dateien gelöscht")
