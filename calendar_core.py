"""Kalender-Kern: JSON-Persistenz, Upcoming-Logik, Recurring-Auflösung.

Datenformat pro Event:
    {
        "id": "<8-stellige hex-id>",
        "title": "Mamas Geburtstag",
        "date": "2026-05-15",        # ISO YYYY-MM-DD
        "time": "14:00" | null,      # HH:MM oder null
        "recurring": "yearly" | null,
        "created": "2026-04-16T20:00:00"
    }
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chanti")

# Pfad kann über Umgebungsvariable überschrieben werden (für Tests)
CALENDAR_FILE = Path(os.environ.get(
    "CHANTI_CALENDAR_FILE",
    str(Path.home() / "chanti" / "calendar.json")
))

# Lock für read-modify-write-Operationen (add/delete/cleanup).
# Reine Reads (load_events, list_all_sorted, get_upcoming) brauchen
# keinen Lock — atomic replace garantiert konsistenten Dateistand.
_lock = threading.Lock()


# ─────────────────────────── I/O ───────────────────────────

def _ensure_file() -> None:
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CALENDAR_FILE.exists():
        CALENDAR_FILE.write_text("[]", encoding="utf-8")


def load_events() -> list[dict]:
    """Lädt alle Events aus der JSON-Datei."""
    _ensure_file()
    try:
        data = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.error(f"{CALENDAR_FILE} ist keine Liste, setze zurück")
            return []
        return data
    except json.JSONDecodeError as e:
        logger.error(f"calendar.json kaputt: {e}")
        return []


def save_events(events: list[dict]) -> None:
    """Schreibt atomar (temp-file + rename), damit bei Crash nichts verloren geht."""
    _ensure_file()
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=CALENDAR_FILE.parent, prefix=".calendar.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CALENDAR_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ─────────────────────────── CRUD ───────────────────────────

def _new_id() -> str:
    return secrets.token_hex(4)


def add_event(title: str, date_iso: str, time_hm: Optional[str] = None,
              recurring: Optional[str] = None) -> dict:
    """Fügt ein Event hinzu. Validiert Eingaben. Gibt das neue Event zurück."""
    title = (title or "").strip()
    if not title:
        raise ValueError("Titel darf nicht leer sein")

    # Datum validieren
    try:
        datetime.strptime(date_iso, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Ungültiges Datum: {date_iso!r} (erwartet YYYY-MM-DD)")

    # Uhrzeit validieren
    if time_hm:
        try:
            datetime.strptime(time_hm, "%H:%M")
        except ValueError:
            raise ValueError(f"Ungültige Uhrzeit: {time_hm!r} (erwartet HH:MM)")
    else:
        time_hm = None

    # Recurring validieren
    if recurring not in (None, "", "yearly"):
        raise ValueError(f"Ungültiges recurring: {recurring!r}")
    if not recurring:
        recurring = None

    event = {
        "id": _new_id(),
        "title": title,
        "date": date_iso,
        "time": time_hm,
        "recurring": recurring,
        "created": datetime.now().replace(microsecond=0).isoformat(),
    }
    with _lock:
        events = load_events()
        events.append(event)
        save_events(events)
    return event


def delete_event(event_id: str) -> bool:
    with _lock:
        events = load_events()
        new = [e for e in events if e.get("id") != event_id]
        if len(new) == len(events):
            return False
        save_events(new)
    return True


def cleanup_past_events() -> int:
    """Löscht Einmal-Termine deren Datum in der Vergangenheit liegt.
    Recurring-Events bleiben. Gibt Anzahl gelöschter Events zurück.
    Read-modify-write unter Lock damit parallele add_event-Calls nicht verloren gehen."""
    today = date.today()
    with _lock:
        events = load_events()
        kept = []
        removed = 0
        for e in events:
            if e.get("recurring"):
                kept.append(e)
                continue
            try:
                ev_date = datetime.strptime(e["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                kept.append(e)  # kaputte Events in Ruhe lassen
                continue
            if ev_date < today:
                removed += 1
            else:
                kept.append(e)
        if removed:
            save_events(kept)
            logger.info(f"Kalender-Cleanup: {removed} vergangene Events gelöscht")
    return removed


# ─────────────────────── Upcoming-Logik ───────────────────────

@dataclass
class OccurrenceHit:
    """Ein konkretes Auftreten eines Events im Fenster."""
    event: dict
    occurrence_date: date   # konkretes Datum (bei recurring das diesjährige)

    def days_until(self, reference: date) -> int:
        return (self.occurrence_date - reference).days


def _next_occurrence(event: dict, reference: date) -> Optional[date]:
    """Gibt das nächste Auftreten eines Events >= reference zurück.
    Bei recurring='yearly' der nächste Jahrestag; sonst das Originaldatum
    wenn es in der Zukunft oder heute liegt, sonst None."""
    try:
        base = datetime.strptime(event["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None

    if event.get("recurring") == "yearly":
        # Versuche diesjähriges Datum
        try:
            this_year = base.replace(year=reference.year)
        except ValueError:
            # 29. Februar in Nicht-Schaltjahr → auf 28. Februar fallen lassen
            this_year = base.replace(year=reference.year, day=28)
        if this_year >= reference:
            return this_year
        # nächstes Jahr
        try:
            return base.replace(year=reference.year + 1)
        except ValueError:
            return base.replace(year=reference.year + 1, day=28)

    # Einmal-Termin
    return base if base >= reference else None


def get_upcoming(days: int = 2, reference: Optional[date] = None) -> list[OccurrenceHit]:
    """Alle Events die heute bis heute+days anstehen.
    Sortiert nach Datum, dann Uhrzeit."""
    reference = reference or date.today()
    window_end = reference + timedelta(days=days)
    hits: list[OccurrenceHit] = []
    for e in load_events():
        occ = _next_occurrence(e, reference)
        if occ is None:
            continue
        if reference <= occ <= window_end:
            hits.append(OccurrenceHit(event=e, occurrence_date=occ))
    hits.sort(key=lambda h: (h.occurrence_date, h.event.get("time") or "00:00"))
    return hits


def list_all_sorted() -> list[dict]:
    """Alle Events sortiert nach nächstem Auftreten (für Widget-Anzeige)."""
    today = date.today()
    events = load_events()

    def sort_key(e: dict):
        occ = _next_occurrence(e, today)
        # Vergangene einmalige Events ans Ende
        if occ is None:
            try:
                base = datetime.strptime(e["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                return (date.max, "99:99")
            return (date.max - timedelta(days=1), base.isoformat())
        return (occ, e.get("time") or "00:00")

    return sorted(events, key=sort_key)


def format_hit_for_human(hit: OccurrenceHit, reference: Optional[date] = None) -> str:
    """Formatiert einen Hit für Telegram/Chat-Ausgabe."""
    reference = reference or date.today()
    delta = hit.days_until(reference)
    if delta == 0:
        when = "Heute"
    elif delta == 1:
        when = "Morgen"
    elif delta == 2:
        when = "Übermorgen"
    else:
        when = hit.occurrence_date.strftime("%d.%m.%Y")

    time = hit.event.get("time")
    title = hit.event.get("title", "(ohne Titel)")
    if time:
        return f"{when} {time}: {title}"
    return f"{when}: {title}"
