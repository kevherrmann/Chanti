"""Skill: Browser öffnen"""
import subprocess
import logging
from urllib.parse import urlparse

logger = logging.getLogger("chanti")

# Nur http und https zulassen. Kein file://, ssh://, javascript: etc.
_ALLOWED_SCHEMES = {"http", "https"}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "browser_open",
        "description": "Öffnet eine http(s)-URL im Standard-Browser. Nur für öffentliche Webseiten gedacht.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Die URL die geöffnet werden soll, z.B. https://youtube.com oder https://google.com"
                }
            },
            "required": ["url"]
        }
    }
}


def execute(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        return "Ungültige URL: leer."

    url = url.strip()

    # Wenn kein Schema dran ist, https:// prefixen (alte Komfortfunktion).
    if "://" not in url:
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
    except ValueError:
        return f"Ungültige URL: {url}"

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"Ungültiges Schema '{parsed.scheme}'. Nur http/https erlaubt."

    if not parsed.netloc:
        return f"Ungültige URL: {url}"

    # Control-Chars raus (paranoid — xdg-open hat früher gerne gemeckert).
    if any(ord(c) < 0x20 for c in url):
        return "Ungültige URL: enthält Steuerzeichen."

    try:
        # stdin/stdout/stderr schließen damit der Kindprozess Chanti nicht blockiert
        # und wir keine Zombies sammeln.
        subprocess.Popen(
            ["xdg-open", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Öffne {url} im Browser."
    except FileNotFoundError:
        return "xdg-open nicht gefunden – läuft das System ohne Desktop?"
    except OSError as e:
        logger.warning(f"xdg-open Fehler: {e}")
        return f"Fehler beim Öffnen: {e}"
