"""Skill: Home Assistant Lampen steuern"""
import requests
import logging
from config import HA_URL, HA_TOKEN

logger = logging.getLogger("chanti")

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
    "gruen":  {"hs_color": [120, 100]},
    "blau":   {"hs_color": [240, 100]},
    "gelb":   {"hs_color": [60, 100]},
    "lila":   {"hs_color": [270, 100]},
    "pink":   {"hs_color": [300, 100]},
    "orange": {"hs_color": [30, 100]},
    "cyan":   {"hs_color": [180, 100]},
    "weiß":   {"color_temp": 4000},
    "weiss":  {"color_temp": 4000},
    "warm":   {"color_temp": 2700},
    "kalt":   {"color_temp": 6500},
}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "home_assistant",
        "description": (
            "Steuert Lampen im Smart Home. Kann Lampen an/ausschalten, "
            "Farbe und Helligkeit setzen. Farbe und Helligkeit können "
            "gleichzeitig gesetzt werden. Wenn weder aktion, farbe noch "
            "helligkeit gesetzt sind, wird die Lampe eingeschaltet."
        ),
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
                    "description": "An oder aus schalten. Wird ignoriert wenn farbe oder helligkeit gesetzt ist."
                },
                "farbe": {
                    "type": "string",
                    "description": f"Farbe setzen. Mögliche Werte: {', '.join(sorted(set(FARBEN.keys())))}"
                },
                "helligkeit": {
                    "type": "integer",
                    "description": "Helligkeit in Prozent (1-100). Kann mit farbe kombiniert werden."
                }
            },
            "required": ["lampe"]
        }
    }
}


def _post(entity_id: str, service: str, payload: dict) -> None:
    """Ein einzelner HA-Call. Wirft bei Fehler."""
    domain = "switch" if entity_id.startswith("switch.") else "light"
    resp = requests.post(
        f"{HA_URL}/api/services/{domain}/{service}",
        headers=HEADERS,
        json={"entity_id": entity_id, **payload},
        timeout=10,
    )
    resp.raise_for_status()


def _call_entities(entities, service: str, payload: dict) -> tuple[list, list]:
    """Ruft service auf einer oder mehreren Entities auf. Sammelt Erfolge und Fehler.
    Eine kaputte Lampe darf die anderen nicht blockieren."""
    if isinstance(entities, str):
        entities = [entities]

    ok, errs = [], []
    for eid in entities:
        try:
            _post(eid, service, payload)
            ok.append(eid)
        except requests.exceptions.ConnectionError:
            errs.append((eid, "HA nicht erreichbar"))
            logger.warning(f"HA nicht erreichbar bei {eid}")
        except requests.exceptions.Timeout:
            errs.append((eid, "Timeout"))
            logger.warning(f"HA-Timeout bei {eid}")
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            errs.append((eid, f"HTTP {code}"))
            logger.warning(f"HA-HTTP-Fehler bei {eid}: {e}")
        except requests.exceptions.RequestException as e:
            errs.append((eid, type(e).__name__))
            logger.warning(f"HA-Fehler bei {eid}: {e}")
    return ok, errs


def _format_result(lampe_label: str, was: str, ok: list, errs: list) -> str:
    """Erzeugt eine menschenlesbare Rückmeldung."""
    total = len(ok) + len(errs)
    if not errs:
        return f"{lampe_label.capitalize()} {was}."
    if not ok:
        grund = errs[0][1] if len(set(e[1] for e in errs)) == 1 else "mehrere Fehler"
        return f"Fehler bei {lampe_label}: {grund}."
    # Teilerfolg
    details = ", ".join(e[1] for e in errs)
    return (f"{lampe_label.capitalize()} {was} — "
            f"aber {len(errs)} von {total} fehlgeschlagen ({details}).")


def execute(lampe: str, aktion: str = None, farbe: str = None,
            helligkeit: int = None) -> str:
    if not isinstance(lampe, str):
        return "Fehler: lampe muss ein String sein."
    lampe_key = lampe.lower().strip()
    entity_id = LAMPEN.get(lampe_key)
    if not entity_id:
        return f"Lampe '{lampe}' nicht gefunden."

    # Helligkeit validieren
    brightness = None
    if helligkeit is not None:
        try:
            h = int(helligkeit)
        except (TypeError, ValueError):
            return f"Ungültige Helligkeit: {helligkeit!r}."
        if h < 1 or h > 100:
            return "Helligkeit muss zwischen 1 und 100 liegen."
        brightness = int(h * 2.55)

    # Farbe validieren
    color_payload = None
    if farbe:
        color_payload = FARBEN.get(farbe.lower().strip())
        if color_payload is None:
            return (f"Farbe '{farbe}' unbekannt. "
                    f"Erlaubt: {', '.join(sorted(set(FARBEN.keys())))}.")

    # Ausschalten hat Vorrang, aber nur wenn weder Farbe noch Helligkeit gesetzt sind.
    # (Farbe/Helligkeit zu setzen während man ausschaltet macht keinen Sinn.)
    if aktion == "aus" and color_payload is None and brightness is None:
        ok, errs = _call_entities(entity_id, "turn_off", {})
        return _format_result(lampe_key, "ausgeschaltet", ok, errs)

    # Sonst: alles in einem turn_on-Call zusammenfassen.
    payload: dict = {}
    was_parts = []
    if color_payload is not None:
        payload.update(color_payload)
        was_parts.append(f"auf {farbe.lower().strip()}")
    if brightness is not None:
        payload["brightness"] = brightness
        was_parts.append(f"auf {helligkeit}% Helligkeit")

    if was_parts:
        was = " und ".join(was_parts) + " gesetzt"
    else:
        was = "eingeschaltet"

    ok, errs = _call_entities(entity_id, "turn_on", payload)
    return _format_result(lampe_key, was, ok, errs)
