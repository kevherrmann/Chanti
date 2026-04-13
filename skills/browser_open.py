"""Skill: Browser öffnen oder YouTube suchen"""
import subprocess

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
    subprocess.Popen(["xdg-open", url])
    return f"Öffne {url} im Browser."
