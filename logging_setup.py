"""Zentrales Logging-Setup für Chanti.

Wird einmal beim Start von server.py und wakeword.py aufgerufen.
Sorgt dafür dass alle `logger = logging.getLogger("chanti")`-Aufrufe
konsistent auf stdout und in eine rotierende Log-Datei schreiben.

Nutzung (in server.py und wakeword.py, ganz oben):
    from logging_setup import setup_logging
    setup_logging()
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

# Log-Verzeichnis — kann per Env überschrieben werden
LOG_DIR = Path(os.environ.get(
    "CHANTI_LOG_DIR",
    str(Path.home() / "chanti" / "logs"),
))

# Rotation: max 5 Dateien à 5 MB
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5

# Default-Level — per Env "CHANTI_LOG_LEVEL=DEBUG" überschreibbar
DEFAULT_LEVEL = os.environ.get("CHANTI_LOG_LEVEL", "INFO").upper()

_FORMAT = "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging(level: str | int | None = None,
                  log_file: str | None = "chanti.log") -> None:
    """Konfiguriert den 'chanti'-Logger.

    Idempotent: mehrfacher Aufruf ist safe (z.B. bei Reload-Szenarien).

    Args:
        level: "DEBUG" | "INFO" | "WARNING" | ... oder logging-Konstante.
               None → aus CHANTI_LOG_LEVEL oder "INFO".
        log_file: Dateiname im LOG_DIR. None = nur stdout.
    """
    global _initialized

    logger = logging.getLogger("chanti")

    # Level bestimmen
    if level is None:
        level = DEFAULT_LEVEL
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(level)

    # Bestehende Handler entfernen (macht setup_logging idempotent)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # Stdout-Handler
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setFormatter(formatter)
    logger.addHandler(stdout_h)

    # Datei-Handler (rotierend)
    if log_file:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_path = LOG_DIR / log_file
            file_h = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_h.setFormatter(formatter)
            logger.addHandler(file_h)
        except OSError as e:
            # Log-Datei kann nicht geschrieben werden — stdout reicht.
            logger.warning(f"Log-Datei konnte nicht geöffnet werden: {e}")

    # Propagation abschalten: verhindert Doppel-Logs wenn Uvicorn einen
    # Root-Handler installiert.
    logger.propagate = False

    # Uvicorn/FastAPI-Spam dämpfen — WARNING reicht für uns.
    for noisy in ("uvicorn.access", "httpx", "urllib3.connectionpool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _initialized = True
    logger.debug(f"Logging initialisiert (Level={logging.getLevelName(level)}, Datei={log_file})")


def get_logger() -> logging.Logger:
    """Convenience: ruft setup_logging() falls nötig und gibt den Logger zurück."""
    if not _initialized:
        setup_logging()
    return logging.getLogger("chanti")
