"""Blender MCP Kommunikation via Socket."""
import socket
import json
import logging

logger = logging.getLogger("chanti")

BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876


def execute(code: str) -> dict:
    """Führt Python-Code in Blender aus via Socket."""
    s = socket.socket()
    s.settimeout(10)
    try:
        cmd = {
            "type": "execute_code",
            "params": {"code": code}
        }
        s.connect((BLENDER_HOST, BLENDER_PORT))
        s.sendall((json.dumps(cmd) + "\n").encode())
        response = s.recv(65536)
        return json.loads(response.decode())
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        s.close()


def is_running() -> bool:
    """Prüft ob Blender MCP-Server erreichbar ist."""
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((BLENDER_HOST, BLENDER_PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()
