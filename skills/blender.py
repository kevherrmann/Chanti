"""Skill: Blender via MCP steuern"""
import sys
import os
sys.path.insert(0, os.path.expanduser("~/chanti"))
import blender_ctrl as bl

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "blender",
        "description": "Steuert Blender via MCP. Kann Objekte erstellen, Szenen abfragen und Python-Code in Blender ausführen.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Blender Python Code der ausgeführt werden soll, z.B. 'import bpy; bpy.ops.mesh.primitive_cube_add()'"
                }
            },
            "required": ["code"]
        }
    }
}

def execute(code: str) -> str:
    if not bl.is_running():
        return "Blender läuft nicht oder der MCP Server ist nicht aktiv."
    result = bl.execute(code)
    if result.get("status") == "success":
        return f"Ausgeführt. Ergebnis: {result.get('result', 'OK')}"
    return f"Fehler: {result.get('message', 'Unbekannter Fehler')}"
