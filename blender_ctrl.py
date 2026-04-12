import socket
import json

BLENDER_HOST = "127.0.0.1"
BLENDER_PORT = 9876

def execute(code: str) -> dict:
    try:
        cmd = {
            "type": "execute_code",
            "params": {"code": code}
        }
        s = socket.socket()
        s.settimeout(10)
        s.connect((BLENDER_HOST, BLENDER_PORT))
        s.sendall((json.dumps(cmd) + "\n").encode())
        response = s.recv(65536)
        s.close()
        return json.loads(response.decode())
    except Exception as e:
        return {"status": "error", "message": str(e)}

def is_running() -> bool:
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((BLENDER_HOST, BLENDER_PORT))
        s.close()
        return True
    except:
        return False
