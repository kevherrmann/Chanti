import re
import subprocess
import requests
from duckduckgo_search import DDGS
from config import HA_URL, HA_TOKEN

HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json"
}

LAMPEN = {
    "nachttischlampe links": "light.nachttischlampe_links",
    "nachttischlampe rechts": "light.nachttischlampe_rechts",
    "nachttischlampen": ["light.nachttischlampe_links", "light.nachttischlampe_rechts"],
    "festtagsbeleuchtung": "switch.festtagesbeleuchtung_schalter_1",
    "ringlampe": "light.ringlampe",
    "alle lampen": [
        "light.nachttischlampe_links",
        "light.nachttischlampe_rechts",
        "switch.festtagesbeleuchtung_schalter_1",
        "light.ringlampe"
    ]
}

FARBEN = {
    "rot":      {"hs_color": [0, 100]},
    "grün":     {"hs_color": [120, 100]},
    "blau":     {"hs_color": [240, 100]},
    "gelb":     {"hs_color": [60, 100]},
    "lila":     {"hs_color": [270, 100]},
    "pink":     {"hs_color": [300, 100]},
    "orange":   {"hs_color": [30, 100]},
    "cyan":     {"hs_color": [180, 100]},
    "weiß":     {"color_temp": 4000},
    "warm":     {"color_temp": 2700},
    "kalt":     {"color_temp": 6500},
}

def _ha_turn_on(entity_id, extra={}):
    if isinstance(entity_id, list):
        for eid in entity_id:
            _ha_turn_on(eid, extra)
        return
    domain = "switch" if entity_id.startswith("switch.") else "light"
    requests.post(
        f"{HA_URL}/api/services/{domain}/turn_on",
        headers=HA_HEADERS,
        json={"entity_id": entity_id, **extra},
        timeout=10
    )

def _ha_turn_off(entity_id):
    if isinstance(entity_id, list):
        for eid in entity_id:
            _ha_turn_off(eid)
        return
    domain = "switch" if entity_id.startswith("switch.") else "light"
    requests.post(
        f"{HA_URL}/api/services/{domain}/turn_off",
        headers=HA_HEADERS,
        json={"entity_id": entity_id},
        timeout=10
    )

def _open_browser(url: str):
    subprocess.Popen(["xdg-open", url])

def detect_and_execute(text: str) -> str | None:
    t = text.lower().strip()

    # Lampen steuern
    for lampe_name, entity_id in LAMPEN.items():
        if lampe_name in t:
            # Farbe setzen
            for farbe_name, farbe_params in FARBEN.items():
                if farbe_name in t:
                    _ha_turn_on(entity_id, farbe_params)
                    return f"{lampe_name.capitalize()} auf {farbe_name}."

            # Helligkeit setzen
            m = re.search(r"(\d+)\s*(?:prozent|%)", t)
            if m:
                brightness = int(int(m.group(1)) * 2.55)
                _ha_turn_on(entity_id, {"brightness": brightness})
                return f"{lampe_name.capitalize()} auf {m.group(1)} Prozent."

            # An/Aus
            if any(w in t for w in ["an", "ein", "anmachen", "einschalten", "anschalten"]):
                _ha_turn_on(entity_id)
                return f"{lampe_name.capitalize()} an."
            elif any(w in t for w in ["aus", "ausschalten", "ausmachen"]):
                _ha_turn_off(entity_id)
                return f"{lampe_name.capitalize()} aus."

    # Blender
    blender_result = handle_blender(text)
    if blender_result:
        return blender_result

    # Websuche
    m = re.search(r"^(?:such(?:e|t)|google|search)\s+(?:nach\s+)?(.+)$", t)
    if m:
        query = m.group(1).strip()
        try:
            results = list(DDGS().text(query, max_results=3, region="de-de"))
            if not results:
                return "Ich habe dazu nichts gefunden."
            antwort = f"Hier was ich gefunden habe zu '{query}':\n"
            for r in results:
                antwort += f"- {r['title']}: {r['href']}\n"
            return antwort.strip()
        except Exception as e:
            return f"Suche fehlgeschlagen: {e}"

    # YouTube suche
    m = re.search(r"(?:such(?:e|t)|spiel(?:e|t)?|zeig)\s+(?:auf\s+youtube|youtube)\s+(?:nach\s+)?(.+)|(?:öffne?|zeig)\s+youtube\s+und\s+such(?:e|t)?\s+(?:nach\s+)?(.+)", t)
    if m:
        query = (m.group(1) or m.group(2)).strip()
        url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
        _open_browser(url)
        return f"Ich suche auf YouTube nach {query}."

    # Browser öffnen
    m = re.search(r"(?:öffne?|zeig|geh zu|besuche?)\s+(?:die\s+)?(?:seite\s+)?(?:von\s+)?(.+)", t)
    if m:
        target = m.group(1).strip().rstrip(".")
        if not target.startswith("http"):
            url = f"https://{target}" if "." in target else f"https://www.google.com/search?q={target}"
        else:
            url = target
        _open_browser(url)
        return f"Ich öffne {target} für dich."

    return None

# Blender Aktionen
def handle_blender(text: str) -> str | None:
    t = text.lower().strip()

    import re
    import blender_ctrl as bl

    if not any(w in t for w in ["blender", "würfel", "kugel", "objekt", "szene", "render"]):
        return None

    if not bl.is_running():
        return "Blender läuft nicht oder der MCP Server ist nicht aktiv."

    # Würfel erstellen
    if "würfel" in t:
        result = bl.execute("import bpy; bpy.ops.mesh.primitive_cube_add(location=(0,0,0))")
        return "Würfel erstellt." if result.get("status") == "success" else f"Fehler: {result.get('message')}"

    # Kugel erstellen
    if "kugel" in t:
        result = bl.execute("import bpy; bpy.ops.mesh.primitive_uv_sphere_add(location=(0,0,0))")
        return "Kugel erstellt." if result.get("status") == "success" else f"Fehler: {result.get('message')}"

    # Szene abfragen
    if "szene" in t or "objekte" in t:
        result = bl.execute("import bpy; result = list(bpy.context.scene.objects.keys())")
        return f"Objekte in der Szene: {result.get('result', [])}"

    # Alles löschen
    if "alles löschen" in t or "szene leeren" in t:
        result = bl.execute("import bpy; bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()")
        return "Szene geleert."

    return None
