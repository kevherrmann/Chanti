"""Startup-Task: prüft beim Chanti-Start ob Termine anstehen
und sendet sie per Telegram.

Regeln:
- 5 Minuten nach Serverstart warten (Kevin könnte beim Hochfahren schlafen)
- Zwischen 0:00 und 8:00 Uhr: bis 8:00 Uhr warten
- Fenster: heute + nächste 2 Tage
- Jeden Start neu senden (bewusst kein "notified"-Flag – Kevin will tägliche Erinnerung)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import calendar_core
import telegram_notify

logger = logging.getLogger("chanti")

# Konstanten
STARTUP_DELAY_SEC = 5 * 60      # 5 Minuten
QUIET_HOURS_UNTIL = 8           # bis 8:00 Uhr morgens
REMINDER_WINDOW_DAYS = 2        # heute + 2 Tage


def _seconds_until_wake() -> int:
    """Wenn jetzt <8 Uhr: Sekunden bis heute 8:00. Sonst 0."""
    now = datetime.now()
    if now.hour < QUIET_HOURS_UNTIL:
        wake = now.replace(hour=QUIET_HOURS_UNTIL, minute=0, second=0, microsecond=0)
        return max(0, int((wake - now).total_seconds()))
    return 0


def _build_message(hits: list[calendar_core.OccurrenceHit]) -> str:
    """Baut die Telegram-Nachricht für einen oder mehrere Treffer."""
    today = date.today()
    lines = [calendar_core.format_hit_for_human(h, reference=today) for h in hits]
    if len(hits) == 1:
        header = "Erinnerung:"
    else:
        header = f"Erinnerung – {len(hits)} Termine:"
    return header + "\n" + "\n".join(f"• {l}" for l in lines)


async def reminder_startup_task() -> None:
    """Background-Task die einmal beim Start läuft."""
    try:
        # Vergangene Einmal-Termine aufräumen
        try:
            removed = calendar_core.cleanup_past_events()
            if removed:
                logger.info(f"Startup-Cleanup: {removed} vergangene Events entfernt")
        except Exception as e:
            logger.error(f"Cleanup-Fehler (nicht kritisch): {e}")

        # 1) 5 Minuten warten
        logger.info(f"Kalender-Check: warte {STARTUP_DELAY_SEC}s")
        await asyncio.sleep(STARTUP_DELAY_SEC)

        # 2) Quiet Hours
        quiet = _seconds_until_wake()
        if quiet > 0:
            logger.info(f"Quiet Hours aktiv: warte bis 8:00 ({quiet}s)")
            await asyncio.sleep(quiet)

        # 3) Prüfen
        hits = calendar_core.get_upcoming(days=REMINDER_WINDOW_DAYS)
        if not hits:
            logger.info("Kalender-Check: keine Termine im Fenster")
            return

        msg = _build_message(hits)
        logger.info(f"Kalender-Check: {len(hits)} Termine, sende Telegram")

        # requests ist blockierend, in Thread auslagern
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, telegram_notify.send_telegram, msg)
        if ok:
            logger.info("Telegram-Erinnerung gesendet")
        else:
            logger.warning("Telegram-Erinnerung fehlgeschlagen")

    except asyncio.CancelledError:
        logger.info("Kalender-Task abgebrochen (Shutdown)")
        raise
    except Exception as e:
        logger.error(f"Kalender-Startup-Task Fehler: {e}", exc_info=True)
