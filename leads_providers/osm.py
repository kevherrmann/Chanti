"""OpenStreetMap-basierte Firmensuche via Overpass API + Nominatim Geocoding.

Portiert aus dem alten lead_generator.py, aufgeräumt.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger("chanti")


# OSM-Tag-Mapping für häufige Branchen (aus altem Code)
BRANCH_TAGS: dict[str, list[tuple[str, str]]] = {
    # Handwerk & Bau
    "bau":           [("craft", "builder"), ("craft", "construction"), ("shop", "hardware"), ("craft", "carpenter"), ("craft", "roofer")],
    "zimmerei":      [("craft", "carpenter"), ("craft", "joiner"), ("craft", "builder")],
    "dachdecker":    [("craft", "roofer")],
    "maler":         [("craft", "painter")],
    "schreiner":     [("craft", "carpenter"), ("craft", "joiner")],
    "elektriker":    [("craft", "electrician")],
    "klempner":      [("craft", "plumber")],
    "heizung":       [("craft", "hvac")],
    "sanitär":       [("craft", "plumber"), ("shop", "bathroom")],
    "handwerk":      [("craft", "electrician"), ("craft", "plumber"), ("craft", "painter"), ("craft", "carpenter"), ("craft", "locksmith")],
    "garten":        [("craft", "gardener"), ("shop", "garden_centre")],
    "reinigung":     [("shop", "laundry"), ("shop", "dry_cleaning"), ("craft", "cleaning")],
    # Gastronomie & Hotellerie
    "gastronomie":   [("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "bar"), ("amenity", "fast_food"), ("amenity", "pub")],
    "restaurant":    [("amenity", "restaurant")],
    "café":          [("amenity", "cafe")],
    "bar":           [("amenity", "bar"), ("amenity", "pub")],
    "hotel":         [("tourism", "hotel"), ("tourism", "guest_house"), ("tourism", "hostel")],
    "bäcker":        [("shop", "bakery")],
    "metzger":       [("shop", "butcher")],
    "eiscafé":       [("amenity", "ice_cream")],
    # Gesundheit & Körper
    "arzt":          [("amenity", "doctors"), ("amenity", "clinic")],
    "zahnarzt":      [("amenity", "dentist")],
    "apotheke":      [("amenity", "pharmacy")],
    "tierarzt":      [("amenity", "veterinary")],
    "optiker":       [("shop", "optician")],
    "friseur":       [("shop", "hairdresser"), ("shop", "beauty")],
    "kosmetik":      [("shop", "beauty"), ("shop", "cosmetics"), ("shop", "massage")],
    "fitness":       [("leisure", "fitness_centre"), ("leisure", "sports_centre")],
    "massage":       [("shop", "massage"), ("amenity", "spa")],
    "physiotherapie":[("amenity", "physiotherapist"), ("healthcare", "physiotherapist")],
    # Tiere
    "tierhandlung":  [("shop", "pet"), ("shop", "pet_food")],
    "hundepension":  [("amenity", "animal_boarding")],
    "reitschule":    [("leisure", "horse_riding"), ("amenity", "riding_school")],
    # Büro & Dienstleistung
    "rechtsanwalt":  [("office", "lawyer"), ("office", "notary")],
    "steuerberater": [("office", "accountant"), ("office", "tax_advisor")],
    "versicherung":  [("office", "insurance")],
    "immobilien":    [("office", "estate_agent")],
    "bank":          [("amenity", "bank")],
    "it":            [("office", "it"), ("shop", "computer")],
    "werbeagentur":  [("office", "advertising_agency")],
    "fotograf":      [("shop", "photo_studio"), ("craft", "photographer")],
    "drucker":       [("shop", "copyshop"), ("craft", "printer")],
    "architekt":     [("office", "architect")],
    "unternehmensberatung": [("office", "consulting"), ("office", "company")],
    # Bildung
    "schule":        [("amenity", "school")],
    "fahrschule":    [("amenity", "driving_school")],
    "kita":          [("amenity", "kindergarten"), ("amenity", "childcare")],
    "musikschule":   [("amenity", "music_school")],
    "tattoo":        [("shop", "tattoo"), ("shop", "piercing")],
    # Handel
    "blumen":        [("shop", "florist")],
    "schmuck":       [("shop", "jewelry"), ("shop", "watches")],
    "möbel":         [("shop", "furniture"), ("shop", "interior_decoration")],
    "autowerkstatt": [("shop", "car_repair"), ("craft", "car_repair")],
    "autohandel":    [("shop", "car"), ("shop", "car_dealer")],
}


def map_branch_to_tags(branche: str) -> list[tuple[str, str]]:
    """Mappt Branchenname auf OSM-Tags (Substring/Fuzzy-Match)."""
    b = branche.lower().strip()
    for key, tags in BRANCH_TAGS.items():
        if key in b or b in key:
            return tags
    for word in b.split():
        if len(word) < 4:
            continue
        for key, tags in BRANCH_TAGS.items():
            if word in key or key in word:
                return tags
    # Generischer Fallback
    return [("office", "company"), ("shop", "yes")]


def geocode(ort: str) -> Optional[tuple[float, float]]:
    """Ort → (lat, lon) via Nominatim."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{ort}, Deutschland", "format": "json", "limit": 1},
            headers={"User-Agent": "Chanti-LeadTool/2.0"},
            timeout=10,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.error(f"Nominatim-Fehler für {ort!r}: {e}")
    return None


def overpass_search(lat: float, lon: float, tags: list[tuple[str, str]],
                    radius_m: int = 15000) -> list[dict]:
    """Sucht OSM-Elemente im Radius. Gibt normalisierte Firmen-Dicts zurück."""
    parts = []
    for k, v in tags:
        f = f'["{k}"="{v}"]'
        parts.append(f'  node{f}(around:{radius_m},{lat},{lon});')
        parts.append(f'  way{f}(around:{radius_m},{lat},{lon});')
        parts.append(f'  relation{f}(around:{radius_m},{lat},{lon});')
    query = "[out:json][timeout:30];\n(\n" + "\n".join(parts) + "\n);\nout center tags;"

    try:
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=40,
            headers={"User-Agent": "Chanti-LeadTool/2.0"},
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except (requests.RequestException, ValueError) as e:
        logger.error(f"Overpass-Fehler: {e}")
        return []

    seen: set[str] = set()
    results: list[dict] = []
    for el in elements:
        t = el.get("tags", {}) or {}
        name = (t.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        lat_el = el.get("lat") or (el.get("center") or {}).get("lat")
        lon_el = el.get("lon") or (el.get("center") or {}).get("lon")
        results.append({
            "name":    name,
            "address": _build_address(t),
            "phone":   t.get("phone") or t.get("contact:phone") or "",
            "email":   t.get("email") or t.get("contact:email") or "",
            "website": t.get("website") or t.get("contact:website") or "",
            "lat":     lat_el,
            "lon":     lon_el,
            "city":    t.get("addr:city") or "",
        })
    return results


def _build_address(tags: dict) -> str:
    street = tags.get("addr:street", "")
    number = tags.get("addr:housenumber", "")
    post = tags.get("addr:postcode", "")
    city = tags.get("addr:city", "")
    parts = []
    if street:
        parts.append(f"{street} {number}".strip())
    if post or city:
        parts.append(f"{post} {city}".strip())
    return ", ".join(parts)


def search_with_expanding_radius(ort: str, branche: str, target_count: int,
                                 start_radius_km: int = 15) -> tuple[list[dict], int]:
    """Sucht mit automatisch wachsendem Radius bis target_count*2 erreicht.
    Gibt (companies, used_radius_km) zurück."""
    coords = geocode(ort)
    if not coords:
        return [], 0
    lat, lon = coords
    tags = map_branch_to_tags(branche)

    radii_km = [start_radius_km, start_radius_km * 2, start_radius_km * 4, start_radius_km * 6]
    last_companies: list[dict] = []
    used = start_radius_km
    for rkm in radii_km:
        used = rkm
        last_companies = overpass_search(lat, lon, tags, radius_m=rkm * 1000)
        if len(last_companies) >= target_count * 2:
            break
    return last_companies, used
