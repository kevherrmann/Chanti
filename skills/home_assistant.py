"""Skill: Home Assistant Lampen steuern"""
import requests
import sys
import os
sys.path.insert(0, os.path.expanduser("~/chanti"))
from config import HA_URL, HA_TOKEN

HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json"
}

LAMPEN = {
    "nachttischlampe links":  "light.nachttischlampe_links",
    "nachttischlampe rechts": "light.nachttischlampe_rechts",
    "nachttischlampen":       ["light.nachttischlampe_links", "light.nachttischlampe_rechts"],
    "festtagsbeleuchtung":    "switch.festtagesbeleuchtung_schalter_1",
    "ringlampe":              "light.ringlampe",
    "alle lampen":            ["light.nachttischlampe_links", "light.nachttischlampe_rechts",
                               "switch.festtagesbeleuchtung_schalter_1", "light.ringlampe"]
}

FARBEN = {
    "rot":    {"hs_color": [0, 100]},
    "grün":   {"hs_color": [120, 100]},
    "blau":   {"hs_color": [240, 100]},
    "gelb":   {"hs_color": [60, 100]},
    "lila":   {"hs_color": [270, 100]},
    "pink":   {"hs_color": [300, 100]},
    "orange": {"hs_color": [30, 100]},
    "cyan":   {"hs_color": [180, 100]},
    "weiß":   {"color_temp": 4000},
    "warm":   {"color_temp": 2700},
    "kalt":   {"color_temp": 6500},
}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "home_assistant",
        "description": "Steuert Lampen im Smart Home. Kann Lampen an/ausschalten, Farbe und Helligkeit setzen.",
        "parameters": {
            "type": "object",
            "properties": {
                "lampe": {
                    "type": "string",
                    "description": f"Name der Lampe. Mögliche Werte: {', '.join(LAMPEN.keys())}"
                },
                "aktion": {
                    "type": "string",
                    "enum": ["an", "aus"],
                    "description": "An oder aus schalten"
                },
                "farbe": {
                    "type": "string",
                    "description": f"Farbe setzen. Mögliche Werte: {', '.join(FARBEN.keys())}"
                },
                "helligkeit": {
                    "type": "integer",
                    "description": "Helligkeit in Prozent (0-100)"
                }
            },
            "required": ["lampe"]
        }
    }
}

def _call(entity_id, service, extra={}):
    if isinstance(entity_id, list):
        for e in entity_id:
            _call(e, service, extra)
        return
    domain = "switch" if entity_id.startswith("switch.") else "light"
    requests.post(f"{HA_URL}/api/services/{domain}/{service}",
                  headers=HEADERS, json={"entity_id": entity_id, **extra}, timeout=10)

def execute(lampe: str, aktion: str = None, farbe: str = None, helligkeit: int = None) -> str:
    lampe_key = lampe.lower().strip()
    entity_id = LAMPEN.get(lampe_key)
    if not entity_id:
        return f"Lampe '{lampe}' nicht gefunden."

    if farbe:
        params = FARBEN.get(farbe.lower(), {})
        _call(entity_id, "turn_on", params)
        return f"{lampe.capitalize()} auf {farbe} gesetzt."

    if helligkeit is not None:
        _call(entity_id, "turn_on", {"brightness": int(helligkeit * 2.55)})
        return f"{lampe.capitalize()} auf {helligkeit}% Helligkeit."

    if aktion == "aus":
        _call(entity_id, "turn_off")
        return f"{lampe.capitalize()} ausgeschaltet."

    _call(entity_id, "turn_on")
    return f"{lampe.capitalize()} eingeschaltet."
