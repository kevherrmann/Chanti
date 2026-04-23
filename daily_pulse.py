"""Tages-Puls: einmal pro Tag schaut Chanti proaktiv nach.

Drei Checks, jeder kann stumm bleiben:
1. Kalender: Termine in den nächsten 24h?
2. Inaktivität: Kevin länger nicht geschrieben?
3. KI-News: gibt's was Neues im Stack oder der Szene?

Philosophie: lieber schweigen als nerven. Jeder Check entscheidet selbst
ob er was zu sagen hat. Wenn alle drei still sind, passiert nichts.

Kill-Switch: via Umgebungsvariable CHANTI_PULSE_ENABLED=false komplett
deaktivierbar. Einzelne Auslöser via CHANTI_PULSE_* Flags.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import calendar_core
import telegram_notify

logger = logging.getLogger("chanti")

BASE = Path.home() / "chanti"
LOG_DIR = BASE / "memory"
STATE_FILE = BASE / "data" / "pulse_state.json"

# ─── Defaults, alle per ENV überschreibbar ─────────────────────────────────

DEFAULT_HOUR = int(os.environ.get("CHANTI_PULSE_HOUR", "18"))
DEFAULT_MINUTE = int(os.environ.get("CHANTI_PULSE_MINUTE", "0"))

# Inaktivitäts-Schwelle — nach wie vielen Tagen ohne Nachricht meldet sie sich?
INACTIVITY_DAYS = int(os.environ.get("CHANTI_PULSE_INACTIVITY_DAYS", "3"))

# Kalender-Fenster für die Abend-Vorschau (morgen + übermorgen)
CALENDAR_LOOKAHEAD_DAYS = 2

# Master-Kill-Switch
ENABLED = os.environ.get("CHANTI_PULSE_ENABLED", "true").lower() != "false"

# Einzel-Switches
ENABLE_CALENDAR = os.environ.get("CHANTI_PULSE_CALENDAR", "true").lower() != "false"
ENABLE_INACTIVITY = os.environ.get("CHANTI_PULSE_INACTIVITY", "true").lower() != "false"
ENABLE_NEWS = os.environ.get("CHANTI_PULSE_NEWS", "true").lower() != "false"


# ─── Scheduler ─────────────────────────────────────────────────────────────

def _seconds_until_next_run() -> float:
    """Sekunden bis zum nächsten Run um HH:MM. Wenn die Zeit heute schon
    vorbei ist, bis morgen um die Zeit."""
    now = datetime.now()
    target = now.replace(hour=DEFAULT_HOUR, minute=DEFAULT_MINUTE,
                         second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def daily_pulse_task() -> None:
    """Background-Task die ewig läuft und täglich um HH:MM feuert."""
    if not ENABLED:
        logger.info("Daily-Pulse: deaktiviert via ENV")
        return

    logger.info(f"Daily-Pulse: aktiv, feuert täglich um {DEFAULT_HOUR:02d}:{DEFAULT_MINUTE:02d}")

    while True:
        try:
            wait_sec = _seconds_until_next_run()
            logger.info(f"Daily-Pulse: nächster Run in {int(wait_sec)}s")
            await asyncio.sleep(wait_sec)

            # Run
            try:
                await _run_all_checks()
            except Exception as e:
                # Ein gescheiterter Run soll nicht den Task killen
                logger.error(f"Daily-Pulse Run-Fehler: {type(e).__name__}: {e}",
                             exc_info=True)

            # Kurze Pause damit wir nicht sofort denselben Slot nochmal triggern
            await asyncio.sleep(90)

        except asyncio.CancelledError:
            logger.info("Daily-Pulse: abgebrochen (Shutdown)")
            raise
        except Exception as e:
            logger.error(f"Daily-Pulse Scheduler-Fehler: {e}", exc_info=True)
            # Nicht aufgeben — eine Minute warten und weiter
            await asyncio.sleep(60)


async def _run_all_checks() -> None:
    """Ruft alle drei Checks und sendet jeweils wenn was da ist.

    Jeder Check wird in einem Executor gelaufen (sie nutzen requests/
    file-IO und würden sonst den Event-Loop blockieren)."""
    loop = asyncio.get_running_loop()
    messages: list[str] = []

    if ENABLE_CALENDAR:
        try:
            msg = await loop.run_in_executor(None, _check_calendar)
            if msg:
                messages.append(msg)
        except Exception as e:
            logger.warning(f"Daily-Pulse Kalender-Check: {e}")

    if ENABLE_INACTIVITY:
        try:
            msg = await loop.run_in_executor(None, _check_inactivity)
            if msg:
                messages.append(msg)
        except Exception as e:
            logger.warning(f"Daily-Pulse Inaktivitäts-Check: {e}")

    if ENABLE_NEWS:
        try:
            msg = await loop.run_in_executor(None, _check_news)
            if msg:
                messages.append(msg)
        except Exception as e:
            logger.warning(f"Daily-Pulse News-Check: {e}")

    if not messages:
        logger.info("Daily-Pulse: nichts zu melden (alle Checks still)")
        return

    # Nachrichten einzeln senden statt zusammen — Kalender, Inaktivität
    # und News haben verschiedene Charaktere und sollten nicht verschmelzen.
    # Plus: Telegram hat Längenlimit, einzeln ist robuster.
    for msg in messages:
        try:
            ok = await loop.run_in_executor(None, telegram_notify.send_telegram, msg)
            if ok:
                logger.info(f"Daily-Pulse: gesendet ({len(msg)} chars)")
            else:
                logger.warning("Daily-Pulse: Telegram-Send fehlgeschlagen")
        except Exception as e:
            logger.warning(f"Daily-Pulse Send-Fehler: {e}")


# ─── Check 1: Kalender ─────────────────────────────────────────────────────

def _check_calendar() -> str | None:
    """Abend-Vorschau: was steht morgen und übermorgen an?

    Kein doppelter Alarm: die bestehende calendar_startup-Reminder-Logik
    feuert beim Server-Start. Wir feuern abends 18:00 und schauen auf
    'ab morgen'. So überlappt sich das nicht.
    """
    try:
        hits = calendar_core.get_upcoming(days=CALENDAR_LOOKAHEAD_DAYS)
    except Exception as e:
        logger.warning(f"calendar_core.get_upcoming: {e}")
        return None

    # Nur Events ab morgen — heutige hat der Morgen-Reminder schon gemeldet
    tomorrow = date.today() + timedelta(days=1)
    future_hits = [h for h in hits if h.occurrence_date >= tomorrow]
    if not future_hits:
        return None

    lines = []
    for h in future_hits:
        lines.append("• " + calendar_core.format_hit_for_human(h, reference=date.today()))

    if len(future_hits) == 1:
        header = "Kleiner Ausblick:"
    else:
        header = f"Ausblick – {len(future_hits)} Termine:"
    return header + "\n" + "\n".join(lines)


# ─── Check 2: Inaktivität ──────────────────────────────────────────────────

def _check_inactivity() -> str | None:
    """Wenn Kevin seit X Tagen nichts geschrieben hat, einmal kurz nachfragen.

    'Einmal' ist wichtig: wir merken uns wann wir zuletzt gefragt haben,
    damit's nicht jeden Tag wieder kommt. State liegt in pulse_state.json."""
    last_chat = _last_user_message_date()
    if last_chat is None:
        # Noch nie geredet? Dann auch nicht nachfragen — wahrscheinlich
        # frische Installation.
        return None

    days_since = (date.today() - last_chat).days
    if days_since < INACTIVITY_DAYS:
        return None

    # Schon im aktuellen Stille-Fenster gefragt?
    state = _load_state()
    last_asked_iso = state.get("last_inactivity_ask")
    if last_asked_iso:
        try:
            last_asked = date.fromisoformat(last_asked_iso)
            # Wenn wir nach dem letzten Chat gefragt haben → nicht nochmal
            if last_asked >= last_chat:
                return None
        except ValueError:
            pass

    # Fragen und merken
    state["last_inactivity_ask"] = date.today().isoformat()
    _save_state(state)

    if days_since < 7:
        return f"Hey, hab dich länger nicht gehört — alles okay bei dir?"
    else:
        return (f"Hey Kevin, wir haben seit {days_since} Tagen nicht mehr geredet. "
                f"Wollte mich mal melden — alles in Ordnung?")


def _last_user_message_date() -> date | None:
    """Findet das Datum der letzten Log-Datei die tatsächlich Content hat."""
    if not LOG_DIR.exists():
        return None
    logs = sorted(LOG_DIR.glob("*.md"), reverse=True)
    for log_file in logs:
        try:
            # Datei muss ein Kevin-Block enthalten, nicht nur Header
            text = log_file.read_text(encoding="utf-8")
            if "**Kevin:**" not in text:
                continue
            return datetime.strptime(log_file.stem, "%Y-%m-%d").date()
        except (OSError, ValueError):
            continue
    return None


# ─── Check 3: KI-News ──────────────────────────────────────────────────────

def _check_news() -> str | None:
    """Delegiert an pulse_news. Der entscheidet selbst ob was zu melden ist."""
    try:
        import pulse_news
    except ImportError as e:
        logger.warning(f"pulse_news nicht verfügbar: {e}")
        return None

    # Throttle: maximal alle 24h News senden, selbst wenn der Job öfter läuft
    # (falls wir später mal mehrmals am Tag triggern wollen).
    state = _load_state()
    last_news_iso = state.get("last_news_sent")
    if last_news_iso:
        try:
            last_news = datetime.fromisoformat(last_news_iso)
            if (datetime.now() - last_news) < timedelta(hours=20):
                logger.info("Daily-Pulse: News schon heute gesendet, skip")
                return None
        except ValueError:
            pass

    briefing = pulse_news.build_briefing()
    if not briefing:
        return None

    state["last_news_sent"] = datetime.now().isoformat()
    _save_state(state)
    return briefing


# ─── State ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        import json
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"pulse_state laden: {e}")
        return {}


def _save_state(state: dict) -> None:
    try:
        import json
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"pulse_state speichern: {e}")


# ─── Manueller Trigger (für Debug/Test) ────────────────────────────────────

async def trigger_now() -> None:
    """Ruft den Job sofort auf, ohne auf HH:MM zu warten.
    Nützlich für Testing. Kann aus einem Skill aufgerufen werden."""
    logger.info("Daily-Pulse: manuell getriggert")
    await _run_all_checks()
