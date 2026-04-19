"""Low-Level-Client für den Blender-MCP-Socket.

Der Blender-MCP-Server (Ahuja-Addon) horcht auf 127.0.0.1:9876 und erwartet
pro Verbindung genau eine JSON-Message. Er schließt die Connection nach der
Antwort. Das Framing ist "eine Message pro TCP-Connection", also lesen wir
bis EOF — recv(65536) wie vorher schneidet Antworten ab.
"""
import json
import logging
import socket
from typing import Any

logger = logging.getLogger("chanti")

BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876

# Gesamt-Timeout für eine Command-Runde (connect + send + vollständig lesen).
DEFAULT_TIMEOUT = 15.0
# Max. Antwortgröße, um OOM bei Fehler im Server zu verhindern.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MB
# Kleinere Timeouts nur für Availability-Check.
PING_TIMEOUT = 2.0


def _recv_all(sock: socket.socket, limit: int) -> bytes:
    """Liest bis EOF oder bis 'limit' erreicht ist."""
    buf = bytearray()
    while True:
        try:
            chunk = sock.recv(8192)
        except socket.timeout:
            raise
        if not chunk:
            return bytes(buf)
        buf.extend(chunk)
        if len(buf) > limit:
            raise OSError(f"Antwort zu groß (>{limit} Bytes)")


def send_command(cmd_type: str, params: dict | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Schickt einen strukturierten Befehl an den Blender-MCP-Server.

    Gibt immer ein Dict mit mindestens 'status' zurück.
    'success' | 'error'. Bei 'error' enthält 'message' eine Beschreibung.
    """
    payload = {"type": cmd_type, "params": params or {}}
    try:
        data = (json.dumps(payload) + "\n").encode("utf-8")
    except (TypeError, ValueError) as e:
        return {"status": "error", "message": f"Ungültige Params: {e}"}

    try:
        with socket.create_connection((BLENDER_HOST, BLENDER_PORT),
                                      timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(data)
            # Halb-schließen signalisiert dem Server "ich bin fertig mit Senden".
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            raw = _recv_all(s, MAX_RESPONSE_BYTES)
    except ConnectionRefusedError:
        return {"status": "error",
                "message": "Blender läuft nicht oder MCP-Addon ist nicht aktiv."}
    except socket.timeout:
        return {"status": "error",
                "message": f"Blender antwortet nicht innerhalb {timeout}s."}
    except OSError as e:
        logger.warning(f"Blender-Socket-Fehler: {e}")
        return {"status": "error", "message": f"Netzwerkfehler: {e}"}

    if not raw:
        return {"status": "error", "message": "Keine Antwort von Blender."}

    try:
        resp = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return {"status": "error",
                "message": f"Antwort nicht parsebar: {e}"}

    if not isinstance(resp, dict):
        return {"status": "error", "message": "Antwort hat falsches Format."}
    resp.setdefault("status", "error")
    return resp


def execute(code: str) -> dict:
    """Führt Python-Code in Blender aus.

    ACHTUNG: Diese Funktion bleibt für interne Nutzung durch den
    Blender-Skill-Wrapper, NICHT für direktes Exposen ans LLM.
    """
    return send_command("execute_code", {"code": code})


def is_running() -> bool:
    """Schnell-Check: antwortet der Blender-MCP-Port?"""
    try:
        with socket.create_connection((BLENDER_HOST, BLENDER_PORT),
                                      timeout=PING_TIMEOUT):
            return True
    except (OSError, socket.timeout):
        return False


def get_scene_info() -> dict:
    """Gibt strukturierte Infos über die aktuelle Blender-Szene zurück."""
    return send_command("get_scene_info")


def get_object_info(name: str) -> dict:
    return send_command("get_object_info", {"name": name})
