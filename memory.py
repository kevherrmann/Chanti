"""Chantis Gedächtnis: System-Prompt, Kontext, Fakten, Tages-Logs."""
from pathlib import Path
from datetime import date
import re
import threading
import logging
import tempfile

logger = logging.getLogger("chanti")

BASE = Path.home() / "chanti"

USER_FILE     = BASE / "USER.md"
MEMORY_FILE   = BASE / "MEMORY.md"
SOUL_FILE     = BASE / "SOUL.md"
IDENTITY_FILE = BASE / "IDENTITY.md"
TOOLS_FILE    = BASE / "TOOLS.md"
LOG_DIR       = BASE / "memory"

# Obergrenzen
MAX_USER_FACTS = 30
MAX_MEMORY_EVENTS = 200

# Ein Lock pro Datei-Typ schützt vor concurrent write/read. Wakeword + WS
# können parallel loggen, deshalb brauchen wir das.
_user_lock = threading.Lock()
_memory_lock = threading.Lock()
_log_lock = threading.Lock()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""
    except OSError as e:
        logger.warning(f"_read({path}) fehlgeschlagen: {e}")
        return ""


def _safe_write(path: Path, content: str):
    """Atomisches Schreiben: tmp-Datei → rename. Verhindert Datenverlust bei Crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


# ── System-Prompt ─────────────────────────────────────────────────────────────

def _read_tools_header() -> str:
    """Liest aus TOOLS.md nur den Agent-relevanten Teil: alles bis einschließlich
    'Anti-Patterns'. Danach kommt Home-Assistant / Blender / Kalender — deren
    Infos stecken eh in den Tool-Descriptions, sie hier doppelt in den System-
    Prompt zu stopfen kostet nur Tokens."""
    text = _read(TOOLS_FILE)
    if not text:
        return ""
    # Schneide bei der ersten H2-Überschrift die NICHT zu den Agent-Basics gehört.
    # Unsere Agent-Abschnitte: Entscheidung, Deine zwei Datei-Tools, Workflow-Muster, Anti-Patterns.
    # Erste "fremde" H2 ist "## Home Assistant".
    marker = "\n## Home Assistant"
    idx = text.find(marker)
    return text[:idx].rstrip() if idx != -1 else text


def load_system_prompt() -> str:
    """Kompakter System-Prompt: SOUL + USER-Fakten + MEMORY-Ereignisse +
    Agent-Regeln aus TOOLS.md. IDENTITY wird weggelassen um Tokens zu sparen —
    das steckt in den Tool-Definitions selbst."""
    from datetime import datetime
    today = datetime.now().strftime("%A, %d. %B %Y")

    soul   = f"Heute ist {today}.\n\n" + _read(SOUL_FILE)
    user   = _read(USER_FILE)
    memory = _read(MEMORY_FILE)
    tools  = _read_tools_header()

    parts = [soul]
    if tools:
        parts.append(tools)
    if user:
        parts.append(f"## Was du über Kevin weißt\n{user}")
    if memory:
        lines = [l for l in memory.splitlines() if l.strip().startswith("-")]
        recent = "\n".join(lines[-10:])
        if recent:
            parts.append(f"## Wichtige Ereignisse\n{recent}")

    return "\n\n".join(parts)


# ── Tages-Kontext für Chat-History ───────────────────────────────────────────

def load_recent_context(n: int = 3) -> list[dict]:
    """Lädt die letzten n Gespräche von heute als Chat-Messages."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{date.today().isoformat()}.md"
    if not log_file.exists():
        return []

    try:
        content = log_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"load_recent_context: {e}")
        return []

    blocks = re.findall(
        r'\*\*Kevin:\*\*\s*(.+?)\n\*\*Chanti:\*\*\s*(.+?)(?=\n###|\Z)',
        content,
        re.DOTALL,
    )

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
    _safe_write(USER_FILE, header + "\n".join(facts) + "\n")


def _normalize(s: str) -> str:
    """Für Duplikat-Vergleich: lowercase, Leading-Dash weg, Whitespace normalisiert."""
    return re.sub(r"\s+", " ", s.lower().lstrip("- ").strip())


def _is_duplicate(new: str, existing: list[str]) -> bool:
    """Einfacher Duplikat-Check: exakt oder sehr ähnlich (80% Wort-Overlap)."""
    new_clean = _normalize(new)
    if not new_clean:
        return True  # leerer Fakt = immer "Duplikat"
    new_words = set(new_clean.split())
    for e in existing:
        e_clean = _normalize(e)
        if new_clean == e_clean:
            return True
        e_words = set(e_clean.split())
        if len(new_words) > 2 and len(e_words) > 2:
            union = len(new_words | e_words)
            if union == 0:
                continue
            jaccard = len(new_words & e_words) / union
            if jaccard >= 0.8:
                return True
    return False


# Fakten die nicht in USER.md gespeichert werden sollen.
# Wort-basierter Match (nicht substring), damit "uhr" nicht in "Uhrzeigersinn" matcht.
_FACT_BLACKLIST_WORDS = {
    "datum", "uhrzeit", "heute", "uhr", "tag",
    "gedächtnis", "persistentes",
}
# Diese Phrasen (substring) sind Selbstbezüge von Chanti und gehören nicht in USER.md
_FACT_BLACKLIST_PHRASES = (
    "chanti kann", "chanti ist", "chanti hat", "chanti erinnert",
)


def _is_blacklisted(fact: str) -> bool:
    low = fact.lower()
    if any(p in low for p in _FACT_BLACKLIST_PHRASES):
        return True
    words = set(re.findall(r"\w+", low))
    return bool(words & _FACT_BLACKLIST_WORDS)


def add_user_fact(fact: str):
    """Fügt einen Fakt zu USER.md hinzu. Max MAX_USER_FACTS, keine Duplikate."""
    fact = fact.strip().lstrip("- ").strip()
    if not fact:
        return
    if _is_blacklisted(fact):
        logger.debug(f"Fakt gefiltert: {fact[:50]}")
        return
    line = f"- {fact}"
    with _user_lock:
        facts = _read_user_facts()
        if _is_duplicate(line, facts):
            return
        facts.append(line)
        _write_user_facts(facts[-MAX_USER_FACTS:])


def correct_user_fact(old: str, new: str):
    """Ersetzt einen veralteten Fakt."""
    old_line = f"- {old.strip().lstrip('- ').strip()}"
    new_line = f"- {new.strip().lstrip('- ').strip()}"
    with _user_lock:
        facts = _read_user_facts()
        replaced = False
        for i, f in enumerate(facts):
            if _normalize(f) == _normalize(old_line):
                facts[i] = new_line
                replaced = True
                break
        if not replaced:
            # Fuzzy-Fallback: am ähnlichsten ersetzen
            for i, f in enumerate(facts):
                if _is_duplicate(old_line, [f]):
                    facts[i] = new_line
                    replaced = True
                    break
        if not replaced:
            facts.append(new_line)
        _write_user_facts(facts[-MAX_USER_FACTS:])


# ── MEMORY.md ────────────────────────────────────────────────────────────────

def _read_memory_lines() -> tuple[list[str], list[str]]:
    """Gibt (header_lines, event_lines) zurück. Event-Lines sind '- [date] text'."""
    if not MEMORY_FILE.exists():
        return [], []
    lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines()
    events = [l for l in lines if l.strip().startswith("- ")]
    header = [l for l in lines if not l.strip().startswith("- ")]
    return header, events


def _write_memory(header: list[str], events: list[str]):
    content = "\n".join(header).rstrip() + "\n\n" + "\n".join(events) + "\n"
    _safe_write(MEMORY_FILE, content)


_MEMORY_DATE_PREFIX = re.compile(r"^\s*-?\s*\[\d{4}-\d{2}-\d{2}\]\s*")


def _memory_text(line: str) -> str:
    """Extrahiert den reinen Ereignis-Text (ohne '- ' und Datum-Prefix)."""
    return _normalize(_MEMORY_DATE_PREFIX.sub("", line))


def add_memory_event(event: str):
    """Fügt ein datiertes Ereignis zu MEMORY.md hinzu.
    Duplikat-Check gegen den Ereignis-Text, nicht gegen substring der ganzen Datei."""
    event = event.strip()
    if not event:
        return
    new_text = _normalize(event)

    with _memory_lock:
        header, events = _read_memory_lines()
        if not header:
            header = ["# MEMORY – Wichtige Ereignisse"]

        for existing in events:
            if _memory_text(existing) == new_text:
                return  # exaktes Duplikat

        entry = f"- [{date.today().isoformat()}] {event}"
        events.append(entry)
        # Älteste rauswerfen wenn über Limit
        if len(events) > MAX_MEMORY_EVENTS:
            events = events[-MAX_MEMORY_EVENTS:]
        _write_memory(header, events)


# ── Tages-Log ────────────────────────────────────────────────────────────────

def log_conversation(user_text: str, assistant_text: str):
    """Schreibt Gespräch in memory/YYYY-MM-DD.md. Thread-safe."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{date.today().isoformat()}.md"
    entry = (f"\n### {date.today().isoformat()}\n"
             f"**Kevin:** {user_text}\n"
             f"**Chanti:** {assistant_text}\n")
    with _log_lock:
        try:
            if not log_file.exists():
                log_file.write_text(f"# Log {date.today().isoformat()}\n",
                                    encoding="utf-8")
            with log_file.open("a", encoding="utf-8") as f:
                f.write(entry)
        except OSError as e:
            logger.warning(f"log_conversation fehlgeschlagen: {e}")


# ── Parser für Chantis Selbst-Befehle ────────────────────────────────────────

# Matcht auch verschachtelte eckige Klammern in Fakten, indem bis zum
# passenden schließenden ] außerhalb von Klammern gesucht wird.
# Einfacher Weg: balanced-bracket-Parser statt Regex.

def _extract_tagged(text: str, tag: str) -> list[str]:
    """Findet alle [TAG: ...] Blöcke, erlaubt verschachtelte []."""
    results = []
    i = 0
    prefix = f"[{tag}:"
    pl = len(prefix)
    t_low = text.lower()
    while True:
        start = t_low.find(prefix.lower(), i)
        if start == -1:
            break
        depth = 1
        j = start + pl
        while j < len(text) and depth > 0:
            c = text[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth == 0:
            results.append(text[start + pl:j].strip())
            i = j + 1
        else:
            break
    return results


def parse_and_execute_commands(text: str) -> str:
    """Parst [MERKE:], [KORRIGIERE:], [EREIGNIS:] aus Chantis Antwort,
    führt sie aus und entfernt die Tags aus dem sichtbaren Text."""
    for m in _extract_tagged(text, "MERKE"):
        try:
            add_user_fact(m)
        except Exception as e:
            logger.warning(f"add_user_fact fehlgeschlagen: {e}")

    for m in _extract_tagged(text, "KORRIGIERE"):
        parts = re.split(r"\s*(?:→|->)\s*", m, maxsplit=1)
        if len(parts) == 2:
            try:
                correct_user_fact(parts[0], parts[1])
            except Exception as e:
                logger.warning(f"correct_user_fact fehlgeschlagen: {e}")

    for m in _extract_tagged(text, "EREIGNIS"):
        try:
            add_memory_event(m)
        except Exception as e:
            logger.warning(f"add_memory_event fehlgeschlagen: {e}")

    # Tags aus sichtbarem Text entfernen — mit balanced-bracket-Logik.
    return _strip_tags(text, ["MERKE", "KORRIGIERE", "EREIGNIS"]).strip()


def _strip_tags(text: str, tags: list[str]) -> str:
    """Entfernt alle [TAG: ...] Blöcke (inkl. verschachtelter []) aus Text."""
    out = []
    i = 0
    lowered = text.lower()
    tag_prefixes = [f"[{t.lower()}:" for t in tags]
    while i < len(text):
        matched = None
        for p in tag_prefixes:
            if lowered.startswith(p, i):
                matched = p
                break
        if matched is None:
            out.append(text[i])
            i += 1
            continue
        # Überspringe bis zum passenden schließenden ]
        depth = 1
        j = i + len(matched)
        while j < len(text) and depth > 0:
            c = text[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth == 0:
            i = j + 1
        else:
            # Nicht geschlossen — belass es, sonst verschlucken wir den Rest.
            out.append(text[i])
            i += 1
    return "".join(out)


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
        except (ValueError, OSError):
            pass
    if deleted:
        logger.info(f"{deleted} alte Log-Dateien gelöscht")
