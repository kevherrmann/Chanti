"""
Skill: Lead-Generator
Findet Unternehmen in einer Branche/Region, analysiert deren Website-Qualität
mit Playwright und speichert strukturierte Leads als JSON.
"""

import json
import time
import requests
from pathlib import Path
from datetime import datetime

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "lead_generator",
        "description": (
            "Findet potenzielle Kunden (Unternehmen) in einer bestimmten Branche und Region. "
            "Prüft automatisch ob diese keine oder eine schlechte Website haben und speichert "
            "die Ergebnisse als JSON-Datei. Nutze dies wenn Kevin nach Leads oder potenziellen "
            "Kunden sucht, z.B. 'Such mir 5 Baufirmen in Ibbenbüren'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "branche": {
                    "type": "string",
                    "description": "Branche oder Bereich, z.B. 'Baubereich', 'Gastronomie', 'Friseur', 'Handwerk', 'Autowerkstatt'"
                },
                "ort": {
                    "type": "string",
                    "description": "Stadt oder Region, z.B. 'Ibbenbüren', 'Münster', 'Osnabrück'"
                },
                "anzahl": {
                    "type": "string",
                    "description": "Gewünschte Anzahl an Unternehmen als Zahl, z.B. '5' oder '10'. Standard: 5"
                }
            },
            "required": ["branche", "ort"]
        }
    }
}

# ──────────────────────────────────────────────
# OSM-Tag-Mapping für häufige Branchen
# ──────────────────────────────────────────────
BRANCH_TAGS: dict[str, list[tuple[str, str]]] = {
    # ── Handwerk & Bau ──
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
    "garten":        [("craft", "gardener"), ("shop", "garden_centre"), ("landuse", "greenhouse_horticulture")],
    "reinigung":     [("shop", "laundry"), ("shop", "dry_cleaning"), ("craft", "cleaning")],

    # ── Gastronomie & Hotellerie ──
    "gastronomie":   [("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "bar"), ("amenity", "fast_food"), ("amenity", "pub")],
    "restaurant":    [("amenity", "restaurant")],
    "café":          [("amenity", "cafe")],
    "bar":           [("amenity", "bar"), ("amenity", "pub")],
    "hotel":         [("tourism", "hotel"), ("tourism", "guest_house"), ("tourism", "hostel"), ("tourism", "motel")],
    "bäcker":        [("shop", "bakery")],
    "metzger":       [("shop", "butcher")],
    "eiscafé":       [("amenity", "ice_cream")],

    # ── Gesundheit & Körper ──
    "arzt":          [("amenity", "doctors"), ("amenity", "clinic"), ("amenity", "hospital")],
    "zahnarzt":      [("amenity", "dentist")],
    "apotheke":      [("amenity", "pharmacy")],
    "tierarzt":      [("amenity", "veterinary")],
    "optiker":       [("shop", "optician")],
    "friseur":       [("shop", "hairdresser"), ("shop", "beauty")],
    "kosmetik":      [("shop", "beauty"), ("shop", "cosmetics"), ("shop", "massage")],
    "fitness":       [("leisure", "fitness_centre"), ("leisure", "sports_centre"), ("leisure", "gym")],
    "yoga":          [("leisure", "yoga"), ("sport", "yoga"), ("amenity", "studio")],
    "massage":       [("shop", "massage"), ("amenity", "spa")],
    "physiotherapie":[("amenity", "physiotherapist"), ("healthcare", "physiotherapist")],

    # ── Tiere & Haustiere ──
    "tierhandlung":  [("shop", "pet"), ("shop", "pet_food")],
    "hundepension":  [("amenity", "animal_boarding"), ("shop", "pet"), ("amenity", "veterinary")],
    "tierpension":   [("amenity", "animal_boarding"), ("amenity", "animal_shelter")],
    "hundetraining": [("amenity", "animal_training"), ("sport", "dog_racing")],
    "pferdehof":     [("leisure", "horse_riding"), ("amenity", "riding_school")],
    "reitschule":    [("leisure", "horse_riding"), ("amenity", "riding_school")],

    # ── Büro & Dienstleistungen ──
    "rechtsanwalt":  [("office", "lawyer"), ("office", "notary")],
    "steuerberater": [("office", "accountant"), ("office", "tax_advisor")],
    "versicherung":  [("office", "insurance")],
    "immobilien":    [("office", "estate_agent")],
    "bank":          [("amenity", "bank"), ("amenity", "bureau_de_change")],
    "it":            [("office", "it"), ("shop", "computer"), ("craft", "computer")],
    "werbeagentur":  [("office", "advertising_agency"), ("office", "company")],
    "fotograf":      [("shop", "photo_studio"), ("craft", "photographer")],
    "drucker":       [("shop", "copyshop"), ("craft", "printer")],
    "architekt":     [("office", "architect")],
    "unternehmensberatung": [("office", "consulting"), ("office", "company")],

    # ── Bildung & Freizeit ──
    "schule":        [("amenity", "school"), ("amenity", "driving_school"), ("amenity", "language_school")],
    "fahrschule":    [("amenity", "driving_school")],
    "kita":          [("amenity", "kindergarten"), ("amenity", "childcare")],
    "musikschule":   [("amenity", "music_school"), ("amenity", "studio")],
    "tattoo":        [("shop", "tattoo"), ("shop", "piercing")],

    # ── Handel & Einzelhandel ──
    "blumen":        [("shop", "florist")],
    "schmuck":       [("shop", "jewelry"), ("shop", "jewellery"), ("shop", "watches")],
    "möbel":         [("shop", "furniture"), ("shop", "interior_decoration")],
    "autowerkstatt": [("shop", "car_repair"), ("craft", "car_repair")],
    "autohandel":    [("shop", "car"), ("shop", "car_dealer")],
}


def _get_osm_tags(branche: str) -> list[tuple[str, str]]:
    """Mappt Branchenname auf OSM-Tags. Fuzzy-Match mit Wortteilen."""
    b = branche.lower().strip()

    # 1. Exakter Match oder Substring
    for key, tags in BRANCH_TAGS.items():
        if key in b or b in key:
            return tags

    # 2. Wortweise Match – "Hundepension Lengerich" → findet "hundepension"
    words = b.split()
    for word in words:
        if len(word) < 4:
            continue
        for key, tags in BRANCH_TAGS.items():
            if word in key or key in word:
                return tags

    # 3. Kein Match → DDG-Fallback wird die Arbeit machen
    return [("office", "company"), ("shop", "yes"), ("amenity", "community_centre")]


# ──────────────────────────────────────────────
# Geocoding via Nominatim (OpenStreetMap)
# ──────────────────────────────────────────────
def _geocode(ort: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": ort + ", Deutschland", "format": "json", "limit": 1},
            headers={"User-Agent": "Chanti-LeadGenerator/1.0 (private-assistant)"},
            timeout=10
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Overpass API – Firmen im Umkreis suchen
# ──────────────────────────────────────────────
def _overpass_search(lat: float, lon: float, tags: list[tuple[str, str]], radius: int = 15000) -> list[dict]:
    """Sucht Unternehmen im Umkreis via Overpass API."""
    # Jede Tag-Kombination als Union-Block
    parts = []
    for k, v in tags:
        filter_str = f'["{k}"="{v}"]'
        parts.append(f'  node{filter_str}(around:{radius},{lat},{lon});')
        parts.append(f'  way{filter_str}(around:{radius},{lat},{lon});')
        parts.append(f'  relation{filter_str}(around:{radius},{lat},{lon});')

    query = "[out:json][timeout:30];\n(\n" + "\n".join(parts) + "\n);\nout center tags;"

    try:
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=40
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception:
        return []

    seen = set()
    results = []

    for el in elements:
        t = el.get("tags", {})
        name = (t.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        # Koordinaten: node direkt, way/relation via center
        lat_el = el.get("lat") or (el.get("center") or {}).get("lat")
        lon_el = el.get("lon") or (el.get("center") or {}).get("lon")

        results.append({
            "name":     name,
            "adresse":  _build_address(t),
            "telefon":  t.get("phone") or t.get("contact:phone") or "",
            "email":    t.get("email") or t.get("contact:email") or "",
            "website":  t.get("website") or t.get("contact:website") or "",
            "lat":      lat_el,
            "lon":      lon_el,
        })

    return results


def _build_address(tags: dict) -> str:
    street  = tags.get("addr:street", "")
    number  = tags.get("addr:housenumber", "")
    post    = tags.get("addr:postcode", "")
    city    = tags.get("addr:city", "")
    parts = []
    if street:
        parts.append(f"{street} {number}".strip())
    if post or city:
        parts.append(f"{post} {city}".strip())
    return ", ".join(parts)


# ──────────────────────────────────────────────
# DuckDuckGo Fallback
# ──────────────────────────────────────────────
def _ddg_fallback(branche: str, ort: str, needed: int) -> list[dict]:
    """Wenn Overpass zu wenig liefert: DuckDuckGo-Suche."""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        query = f'{branche} {ort} Kontakt Telefon'
        raw = list(DDGS().text(query, max_results=needed * 3, region="de-de"))

        results = []
        seen = set()
        for r in raw:
            title = r.get("title", "").strip()
            href  = r.get("href", "")
            if not title or title.lower() in seen:
                continue
            # Verzeichnis-Seiten überspringen
            skip_domains = ["gelbeseiten", "11880", "cylex", "meinestadt", "yelp",
                            "wikipedia", "facebook", "instagram", "google"]
            if any(d in href for d in skip_domains):
                continue
            seen.add(title.lower())
            results.append({
                "name":    title,
                "adresse": "",
                "telefon": "",
                "email":   "",
                "website": href,
                "lat":     None,
                "lon":     None,
            })
            if len(results) >= needed:
                break
        return results
    except Exception:
        return []


# ──────────────────────────────────────────────
# Kontaktdaten nachschlagen (DDG + Verzeichnisse)
# ──────────────────────────────────────────────
def _enrich_contact(firma: str, ort: str) -> dict:
    """
    Sucht fehlende Kontaktdaten (Telefon, Adresse) per DuckDuckGo nach.
    Nutzt gezielt Verzeichnisse wie Gelbe Seiten / Das Örtliche.
    """
    enriched = {"telefon": "", "adresse": "", "email": ""}
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        # Gezielte Suche in Branchenverzeichnissen
        query = f'"{firma}" {ort} Telefon'
        results = list(DDGS().text(query, max_results=5, region="de-de"))

        import re
        phone_pattern = re.compile(
            r"(?:Tel\.?|Telefon|Fon|Phone|☎|📞)?\s*"
            r"(\+49[\s\-]?|0)[\d\s\-/]{7,15}"
        )
        # Typisches deutsches Adressmuster: Straße Hausnr, PLZ Ort
        addr_pattern = re.compile(
            r"[A-ZÄÖÜ][a-zäöüß\-]+(?:straße|str\.|gasse|weg|allee|platz|ring|damm)"
            r"\s*\d{1,4}\w?,?\s*\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+"
        )

        for r in results:
            text = r.get("body", "") + " " + r.get("title", "")

            if not enriched["telefon"]:
                m = phone_pattern.search(text)
                if m:
                    # Normalisieren: nur Ziffern, Leerzeichen, +, -
                    raw = m.group(0).strip()
                    enriched["telefon"] = re.sub(r"[^\d\s\+\-/]", "", raw).strip()

            if not enriched["adresse"]:
                m = addr_pattern.search(text)
                if m:
                    enriched["adresse"] = m.group(0).strip()

            if not enriched["email"]:
                m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
                if m:
                    enriched["email"] = m.group(0)

            # Wenn alles gefunden, früh abbrechen
            if enriched["telefon"] and enriched["adresse"]:
                break

        # Fallback: Gelbe Seiten direkt per Playwright durchsuchen
        if not enriched["telefon"]:
            enriched.update(_enrich_from_directory(firma, ort))

    except Exception:
        pass
    return enriched


def _enrich_from_directory(firma: str, ort: str) -> dict:
    """Scrapt Gelbe Seiten / Das Örtliche für Kontaktdaten."""
    result = {"telefon": "", "adresse": ""}
    try:
        from playwright.sync_api import sync_playwright
        import re

        search_url = (
            f"https://www.gelbeseiten.de/suche/{firma.replace(' ', '%20')}/"
            f"{ort.replace(' ', '%20')}"
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(search_url, timeout=12000, wait_until="domcontentloaded")

            # Ersten Treffer nehmen
            try:
                page.wait_for_selector("[data-wipe-name]", timeout=5000)
                entry = page.locator("[data-wipe-name]").first

                tel = entry.locator("[class*=telefon], [itemprop=telephone]").first
                if tel.count():
                    result["telefon"] = tel.inner_text().strip()

                adr = entry.locator("[itemprop=address], [class*=adresse]").first
                if adr.count():
                    result["adresse"] = " ".join(adr.inner_text().split()).strip()

            except Exception:
                pass
            browser.close()
    except Exception:
        pass
    return result



# ──────────────────────────────────────────────
# Website-Analyse via Playwright
# ──────────────────────────────────────────────
def _analyze_website(url: str) -> dict:
    """
    Tiefe Website-Analyse mit Playwright.
    Gibt Score zurück: keine / schlecht / schwach / gut
    """
    if not url:
        return {
            "score":      "keine",
            "details":    "Keine Website-URL vorhanden",
            "titel":      "",
            "erreichbar": False,
            "url":        ""
        }

    if not url.startswith("http"):
        url = "https://" + url

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        # Playwright nicht installiert → einfacher requests-Fallback
        return _analyze_website_requests(url)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

            try:
                response = page.goto(url, timeout=15000, wait_until="domcontentloaded")
                http_status = response.status if response else 0

                if http_status >= 400:
                    browser.close()
                    return {"score": "keine", "details": f"HTTP {http_status}", "titel": "", "erreichbar": False, "url": url}

                title = page.title() or ""

                # Text-Inhalt ohne Boilerplate
                text = page.evaluate("""() => {
                    document.querySelectorAll('script, style, nav, footer, header').forEach(e => e.remove());
                    return document.body ? document.body.innerText : '';
                }""")
                word_count = len(text.split()) if text else 0

                checks = {
                    "has_viewport":    page.evaluate("() => !!document.querySelector('meta[name=viewport]')"),
                    "has_contact":     page.evaluate("""() => {
                        const t = document.body ? document.body.innerText.toLowerCase() : '';
                        return t.includes('kontakt') || t.includes('telefon') || t.includes('impressum') || t.includes('contact');
                    }"""),
                    "has_images":      page.evaluate("() => document.querySelectorAll('img').length > 0"),
                    "has_ssl":         url.startswith("https"),
                    "has_nav":         page.evaluate("() => !!document.querySelector('nav, [role=navigation]')"),
                    "word_count":      word_count,
                }

                browser.close()

                # ── Scoring ──
                problems = []
                if word_count < 80:
                    problems.append("kaum Textinhalt")
                if not checks["has_viewport"]:
                    problems.append("nicht mobil-optimiert")
                if not checks["has_contact"]:
                    problems.append("keine Kontaktinformationen")
                if not checks["has_images"]:
                    problems.append("keine Bilder")
                if not checks["has_ssl"]:
                    problems.append("kein HTTPS")
                if not checks["has_nav"]:
                    problems.append("keine Navigation erkannt")

                if word_count < 30:
                    score = "schlecht"
                elif len(problems) >= 3:
                    score = "schlecht"
                elif len(problems) >= 1:
                    score = "schwach"
                else:
                    score = "gut"

                return {
                    "score":      score,
                    "details":    ", ".join(problems) if problems else "Solide Website",
                    "titel":      title,
                    "wörter":     word_count,
                    "erreichbar": True,
                    "url":        url,
                    "checks":     checks,
                }

            except PWTimeout:
                browser.close()
                return {"score": "schlecht", "details": "Seite lädt zu langsam (Timeout)", "titel": "", "erreichbar": False, "url": url}
            except Exception as e:
                browser.close()
                return {"score": "keine", "details": f"Fehler: {type(e).__name__}", "titel": "", "erreichbar": False, "url": url}

    except Exception as e:
        return {"score": "keine", "details": f"Playwright-Fehler: {e}", "titel": "", "erreichbar": False, "url": url}


def _analyze_website_requests(url: str) -> dict:
    """Fallback wenn Playwright nicht verfügbar."""
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        text = r.text
        word_count = len(text.split())
        has_viewport = "viewport" in text
        has_contact  = any(x in text.lower() for x in ["kontakt", "telefon", "impressum"])
        problems = []
        if word_count < 200:
            problems.append("sehr wenig Inhalt")
        if not has_viewport:
            problems.append("nicht mobil-optimiert")
        if not has_contact:
            problems.append("keine Kontaktinfos")
        score = "schlecht" if len(problems) >= 2 else ("schwach" if problems else "gut")
        return {"score": score, "details": ", ".join(problems) or "OK", "titel": "", "erreichbar": True, "url": url}
    except Exception as e:
        return {"score": "keine", "details": str(e), "titel": "", "erreichbar": False, "url": url}


# ──────────────────────────────────────────────
# Potenzial-Begründung
# ──────────────────────────────────────────────
def _potenzial(website_check: dict, branche: str) -> str:
    score   = website_check.get("score", "keine")
    details = website_check.get("details", "")
    if score == "keine":
        return f"Kein Webauftritt – maximales Potenzial für einen Neustart mit professioneller Website"
    if score == "schlecht":
        return f"Website vorhanden, aber qualitativ schwach ({details}) – dringender Modernisierungsbedarf"
    if score == "schwach":
        return f"Website hat klare Schwachstellen: {details} – Optimierungspotenzial vorhanden"
    return "Website wirkt solide – dennoch möglicher Bedarf bei SEO, Performance oder Redesign"


# ──────────────────────────────────────────────
# Haupt-Funktion
# ──────────────────────────────────────────────
def execute(branche: str, ort: str, anzahl=5) -> str:
    """Führt die komplette Lead-Recherche durch und speichert das Ergebnis."""

    # Llama übergibt anzahl manchmal als String → immer zu int casten
    try:
        anzahl = int(anzahl)
    except (ValueError, TypeError):
        anzahl = 5
    anzahl = max(1, min(anzahl, 20))  # Sicherheitslimit

    # 1. Geocoding
    coords = _geocode(ort)
    if not coords:
        return f"Fehler: Konnte den Ort '{ort}' nicht finden."
    lat, lon = coords

    # 2. Overpass-Suche mit automatisch wachsendem Radius
    osm_tags = _get_osm_tags(branche)
    companies = []
    # Radien: 15km → 30km → 50km → 80km
    # Stoppt sobald mindestens anzahl*2 Kandidaten gefunden (genug Pool für "gut"-Filter)
    used_radius = 15000
    for radius in [15000, 30000, 50000, 80000]:
        companies = _overpass_search(lat, lon, osm_tags, radius=radius)
        used_radius = radius
        if len(companies) >= anzahl * 2:
            break

    existing_names = {c["name"].lower() for c in companies}

    # 3. DDG-Fallback wenn Overpass auch bei 80km zu wenig liefert
    if len(companies) < anzahl:
        needed = anzahl * 2 - len(companies)
        for c in _ddg_fallback(branche, ort, needed):
            if c["name"].lower() not in existing_names:
                companies.append(c)
                existing_names.add(c["name"].lower())

    if not companies:
        return f"Keine Unternehmen im Bereich '{branche}' in '{ort}' gefunden."

    # 4. Website-Analyse – Pool abarbeiten bis anzahl Non-"gut" Leads gefunden
    # Wir holen bis zu anzahl*4 Kandidaten damit wir genug haben nach dem Filter
    pool = companies[:anzahl * 4]
    leads = []
    skipped_gut = 0

    for company in pool:
        if len(leads) >= anzahl:
            break

        website_result = _analyze_website(company.get("website", ""))

        # "gut" → überspringen, aber zählen für den Report
        if website_result["score"] == "gut":
            skipped_gut += 1
            time.sleep(0.3)
            continue

        # Kontaktdaten anreichern wenn Telefon oder Adresse fehlt
        telefon = company.get("telefon", "")
        adresse = company.get("adresse", "")
        email   = company.get("email", "")

        if not telefon or not adresse:
            enriched = _enrich_contact(company["name"], ort)
            telefon  = telefon or enriched.get("telefon", "")
            adresse  = adresse or enriched.get("adresse", "")
            email    = email   or enriched.get("email", "")

        lead = {
            "firma":       company["name"],
            "adresse":     adresse,
            "telefon":     telefon,
            "email":       email,
            "website": {
                "url":        company.get("website") or website_result.get("url", ""),
                "score":      website_result["score"],
                "details":    website_result["details"],
                "titel":      website_result.get("titel", ""),
                "erreichbar": website_result.get("erreichbar", False),
            },
            "potenzial":   _potenzial(website_result, branche),
            "koordinaten": {
                "lat": company.get("lat"),
                "lon": company.get("lon"),
            },
        }
        leads.append(lead)
        time.sleep(0.5)

    # 5. JSON speichern
    output_dir = Path(__file__).parent.parent / "leads"
    output_dir.mkdir(exist_ok=True)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_ort = ort.lower().replace(" ", "_").replace("/", "-")
    safe_br  = branche.lower().replace(" ", "_").replace("/", "-")
    filename = f"leads_{safe_ort}_{safe_br}_{ts}.json"
    filepath = output_dir / filename

    export = {
        "meta": {
            "generiert_am": datetime.now().isoformat(),
            "branche":      branche,
            "ort":          ort,
            "koordinaten":  {"lat": lat, "lon": lon},
            "anzahl":       len(leads),
        },
        "leads": leads,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    # 6. Zusammenfassung für den Chat
    count_keine    = sum(1 for l in leads if l["website"]["score"] == "keine")
    count_schlecht = sum(1 for l in leads if l["website"]["score"] in ("schlecht", "schwach"))

    radius_km = used_radius // 1000
    radius_info = f"({radius_km}km Radius)" if radius_km > 15 else "(15km Radius)"

    lines = [
        f"✅ {len(leads)} qualifizierte Leads im Bereich **{branche}** in **{ort}** {radius_info}.",
        f"📊 {count_keine} ohne Website · {count_schlecht} mit schwacher/schlechter Website",
    ]
    if skipped_gut:
        lines.append(f"⏭️  {skipped_gut} Firmen mit guter Website übersprungen")
    lines += ["💾 Gespeichert: `leads/{filename}`", ""]

    for i, lead in enumerate(leads, 1):
        ws = lead["website"]
        score_icon = {"keine": "🔴", "schlecht": "🟠", "schwach": "🟡", "gut": "🟢"}.get(ws["score"], "⚪")
        lines.append(f"**{i}. {lead['firma']}**")
        if lead["adresse"]:
            lines.append(f"   📍 {lead['adresse']}")
        if lead["telefon"]:
            lines.append(f"   📞 {lead['telefon']}")
        if lead["email"]:
            lines.append(f"   ✉️  {lead['email']}")
        if ws["url"]:
            lines.append(f"   🌐 {ws['url']}  {score_icon} {ws['score'].upper()} – {ws['details']}")
        else:
            lines.append(f"   🌐 Keine Website  {score_icon}")
        lines.append(f"   💡 {lead['potenzial']}")
        lines.append("")

    return "\n".join(lines).strip()
