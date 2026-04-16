"""Skill: Browser öffnen oder YouTube suchen"""
import subprocess
import logging
from urllib.parse import urlparse

logger = logging.getLogger("chanti")

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "browser_open",
        "description": "Öffnet eine URL im Browser oder sucht auf YouTube.",
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
    if not url.startswith("http"):
        url = f"https://{url}"
    # Basis-Validierung: muss eine echte URL sein
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"Ungültige URL: {url}"
    try:
        subprocess.Popen(["xdg-open", url])
        return f"Öffne {url} im Browser."
    except FileNotFoundError:
        return "xdg-open nicht gefunden – läuft das System ohne Desktop?"
    except Exception as e:
        return f"Fehler beim Öffnen: {e}"
