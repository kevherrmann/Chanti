"""
Skill: Firmendossier – Company Research
Recherchiert eine einzelne Firma ODER alle Leads aus der letzten Suche.
Quellen: DDG, Playwright (Website-Deep-Crawl), Gelbe Seiten, Social Media.
Speichert pro Firma eine JSON + eine Übersichts-JSON.
"""

import json
import time
import re
import requests
from pathlib import Path
from datetime import datetime

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "company_research",
        "description": "Recherchiert detaillierte Infos zu einer Firma und erstellt ein HTML-Dossier. Aufruf: firma=Name der Firma (z.B. \'Zimmerei Müller\'), ort=Stadt (z.B. \'Ibbenbüren\'). Nutze dieses Tool wenn Kevin sagt: Recherchiere [Firma] in [Ort].",
        "parameters": {
            "type": "object",
            "properties": {
                "firma": {
                    "type": "string",
                    "description": "Nur der Firmenname, z.B. \'Zimmerei Müller\' oder \'Rechtsanwalt Dieter Niermann\'. Kein Ort."
                },
                "ort": {
                    "type": "string",
                    "description": "Nur die Stadt, z.B. \'Lengerich\' oder \'Ibbenbüren\'. Kein Firmenname."
                }
            },
            "required": ["firma", "ort"]
        }
    }
}

# ── Verzeichnisse ──────────────────────────────
LEADS_DIR    = Path(__file__).parent.parent / "leads"
RESEARCH_DIR = Path(__file__).parent.parent / "research"


# ── Groq für Gesprächsöffner ───────────────────
def _llm_gespräch(dossier: dict) -> str:
    """Generiert 2-3 konkrete Kaltakquise-Ansätze basierend auf den Dossier-Daten."""
    try:
        from config import GROQ_API_KEY, GROQ_MODEL
        schwächen = dossier.get("schwächen", [])
        website   = dossier.get("website", {})
        social    = dossier.get("social_media", {})
        bewertung = dossier.get("bewertungen", {})

        kontext = f"""
Firma: {dossier.get('firma', '')}
Ort: {dossier.get('ort', '')}
Branche: {dossier.get('branche_erkannt', 'unbekannt')}
Website-Score: {website.get('score', 'keine')}
Website-Probleme: {', '.join(schwächen) if schwächen else 'keine gefunden'}
Google-Bewertung: {bewertung.get('rating', 'unbekannt')} ({bewertung.get('anzahl_reviews', 0)} Reviews)
Facebook aktiv: {social.get('facebook_aktiv', False)}
Instagram aktiv: {social.get('instagram_aktiv', False)}
Über-uns-Text: {dossier.get('website', {}).get('über_uns', '')[:300]}
"""
        prompt = f"""Du bist Kevin, ein Webentwickler aus Ibbenbüren, der Kaltakquise macht.
Analysiere diese Firma und schreibe 2-3 sehr konkrete, kurze Gesprächsöffner (je 1-2 Sätze).
Beziehe dich auf echte Schwächen die du gefunden hast. Kein Marketing-Blabla.

{kontext}

Format: Nummerierte Liste, deutsch, direkt und professionell."""

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 300,
            },
            timeout=20
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return "Keine Gesprächsöffner generiert."


# ── DDG Recherche ──────────────────────────────
def _ddg_recherche(firma: str, ort: str) -> dict:
    """Mehrere DDG-Suchen für umfassende Infos."""
    result = {"snippets": [], "erwähnungen": [], "news": []}
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        ddg = DDGS()
        queries = [
            (f'"{firma}" {ort}',                   "snippets"),
            (f'"{firma}" {ort} Bewertung Erfahrung', "bewertungen_raw"),
            (f'"{firma}" {ort} News',               "news"),
        ]

        for query, key in queries:
            try:
                hits = list(ddg.text(query, max_results=4, region="de-de"))
                result[key] = [
                    {"titel": h.get("title", ""), "text": h.get("body", "")[:200], "url": h.get("href", "")}
                    for h in hits
                ]
                time.sleep(0.3)
            except Exception:
                result[key] = []

    except Exception:
        pass
    return result


# ── LLM Kontakt-Extraktion ─────────────────────
def _llm_extract(text: str, firma: str, ort: str) -> dict:
    """
    Schickt Seitentext an Groq und lässt das LLM Kontaktdaten extrahieren.
    Zuverlässiger als jeder CSS-Selektor oder Regex.
    """
    empty = {"telefon": "", "adresse": "", "email": "", "kategorien": [], "öffnungszeiten": ""}
    if not text or len(text.strip()) < 20:
        return empty
    try:
        from config import GROQ_API_KEY, GROQ_MODEL
        prompt = f"""Extrahiere aus dem folgenden Text alle Kontaktdaten der Firma "{firma}" in {ort}.
Antworte NUR mit einem JSON-Objekt, kein Text davor oder danach, keine Markdown-Backticks.

Felder:
- telefon: Telefonnummer als String, leer wenn nicht gefunden
- adresse: Vollständige Adresse als String, leer wenn nicht gefunden
- email: E-Mail-Adresse als String, leer wenn nicht gefunden
- öffnungszeiten: Öffnungszeiten als String, leer wenn nicht gefunden
- kategorien: Liste von Branchen/Tätigkeiten (max 5), leere Liste wenn nicht gefunden

Text:
{text[:2000]}

JSON:"""

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 300,
            },
            timeout=15
        )
        if not resp.ok:
            return empty
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Backticks entfernen falls Modell sie trotzdem nutzt
        raw = raw.strip().strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:]
        data = json.loads(raw)
        return {
            "telefon":       str(data.get("telefon", "") or ""),
            "adresse":       str(data.get("adresse", "") or ""),
            "email":         str(data.get("email", "") or ""),
            "öffnungszeiten":str(data.get("öffnungszeiten", "") or ""),
            "kategorien":    list(data.get("kategorien", []) or []),
        }
    except Exception:
        return empty


# ── Gelbe Seiten + LLM-Extraktion ──────────────
def _gelbeseiten_scrape(firma: str, ort: str) -> dict:
    """Lädt Gelbe-Seiten-Treffer und lässt Groq die Kontaktdaten extrahieren."""
    try:
        from playwright.sync_api import sync_playwright
        url = (f"https://www.gelbeseiten.de/suche/"
               f"{requests.utils.quote(firma)}/{requests.utils.quote(ort)}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.goto(url, timeout=15000, wait_until="networkidle")
            page.wait_for_timeout(600)
            text = page.evaluate("""() => {
                document.querySelectorAll('script,style').forEach(e=>e.remove());
                return document.body ? document.body.innerText : '';
            }""")
            browser.close()
        return _llm_extract(text, firma, ort)
    except Exception:
        return {"telefon": "", "adresse": "", "email": "", "kategorien": [], "öffnungszeiten": ""}


# ── Google Rating via n8n ──────────────────────
def _google_rating(firma: str, ort: str) -> dict:
    """
    Ruft den n8n Webhook auf der Google Places API nutzt.
    Sauberer als Playwright-Scraping, kein Consent-Problem.
    n8n läuft auf localhost:5678.
    """
    empty = {"rating": None, "anzahl_reviews": 0, "google_maps_url": "",
             "öffnungszeiten": "", "telefon": "", "adresse": "", "gefunden": False}
    try:
        resp = requests.get(
            "http://localhost:5678/webhook/google-rating",
            params={"firma": firma, "ort": ort},
            timeout=15
        )
        if resp.ok:
            data = resp.json()
            # SerpAPI gibt interne URL zurück – echte Maps URL aus Firmenname bauen
            maps_url = data.get("google_maps_url", "")
            if "serpapi.com" in maps_url or not maps_url:
                maps_url = f"https://www.google.com/maps/search/{requests.utils.quote(firma + ' ' + ort)}"
            return {
                "gefunden":       data.get("gefunden", False),
                "rating":         data.get("rating"),
                "anzahl_reviews": data.get("anzahl_reviews", 0),
                "google_maps_url":maps_url,
                "öffnungszeiten": data.get("öffnungszeiten", ""),
                "telefon":        data.get("telefon", ""),
                "adresse":        data.get("adresse", ""),
                "website":        data.get("website", ""),
            }
    except Exception:
        pass
    return empty


# ── Social Media Check ─────────────────────────
def _social_media_check(firma: str, ort: str, website_text: str = "") -> dict:
    """Prüft ob Facebook/Instagram-Präsenz existiert und wie aktiv sie ist."""
    result = {
        "facebook_url":    "",
        "facebook_aktiv":  False,
        "instagram_url":   "",
        "instagram_aktiv": False,
        "facebook_follower": "",
        "letzter_post":    "",
    }
    try:
        # Erst in Website-Text nach Social-Links suchen
        fb_match = re.search(r'https?://(?:www\.)?facebook\.com/[^\s"\'<>]+', website_text)
        ig_match = re.search(r'https?://(?:www\.)?instagram\.com/[^\s"\'<>]+', website_text)
        if fb_match:
            result["facebook_url"] = fb_match.group(0).rstrip("/")
        if ig_match:
            result["instagram_url"] = ig_match.group(0).rstrip("/")

        # Wenn nicht gefunden: DDG-Suche
        if not result["facebook_url"] or not result["instagram_url"]:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            hits = list(DDGS().text(f'{firma} {ort} Facebook Instagram', max_results=4, region="de-de"))
            for h in hits:
                url = h.get("href", "")
                if not result["facebook_url"] and "facebook.com" in url:
                    result["facebook_url"] = url
                if not result["instagram_url"] and "instagram.com" in url:
                    result["instagram_url"] = url

        # Facebook-Aktivität prüfen via Playwright
        if result["facebook_url"]:
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    page = browser.new_page()
                    page.goto(result["facebook_url"], timeout=12000, wait_until="domcontentloaded")
                    text = page.evaluate("() => document.body ? document.body.innerText : ''")

                    # Datum des letzten Posts suchen
                    date_m = re.search(r"\d+\s*(?:Stunden?|Minuten?|Tagen?|Wochen?|Monaten?)\s*", text)
                    if date_m:
                        result["letzter_post"]   = date_m.group(0).strip()
                        result["facebook_aktiv"] = True
                    # Follower
                    fol_m = re.search(r"([\d\.]+)\s*(?:Follower|Gefällt mir)", text)
                    if fol_m:
                        result["facebook_follower"] = fol_m.group(1)
                    browser.close()
            except Exception:
                pass

    except Exception:
        pass
    return result


# ── LLM Website-Strukturierung ─────────────────
def _llm_structure_website(combined_text: str, firma: str) -> dict:
    """
    Gibt gesammelten Website-Text an Groq.
    LLM extrahiert: über_uns, leistungen, kontakt_text.
    Erkennt "Historie" als Über-uns, "Unsere Arbeiten" als Leistungen, etc.
    """
    empty = {"über_uns": "", "leistungen": "", "kontakt_text": ""}
    if not combined_text.strip():
        return empty
    try:
        from config import GROQ_API_KEY, GROQ_MODEL
        prompt = f"""Analysiere den folgenden Website-Text der Firma "{firma}".
Extrahiere die relevantesten Informationen und antworte NUR mit einem JSON-Objekt.
Keine Backticks, kein Markdown, nur reines JSON.

Felder:
- ueber_uns: Kurze Zusammenfassung wer die Firma ist, was sie macht, Geschichte (max 300 Zeichen). Leer wenn wirklich nichts vorhanden.
- leistungen: Auflistung der Leistungen/Produkte/Angebote (max 300 Zeichen). Leer wenn wirklich nichts vorhanden.
- kontakt_text: Telefon, Adresse, Email, Öffnungszeiten falls im Text vorhanden (max 200 Zeichen). Leer wenn nicht vorhanden.

WICHTIG: "Historie", "Über uns", "Wir", "Das Unternehmen", "Geschichte" → gehört zu ueber_uns.
"Leistungen", "Services", "Angebot", "Produkte", "Was wir tun", "Referenzen" → gehört zu leistungen.
Auch wenn die Seitennamen ungewöhnlich sind, erkenne den Inhalt am Text.

Website-Text:
{combined_text[:3000]}

JSON:"""

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 400,
            },
            timeout=20
        )
        if not resp.ok:
            return empty
        raw = resp.json()["choices"][0]["message"]["content"].strip().strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        return {
            "über_uns":     str(data.get("ueber_uns", "") or ""),
            "leistungen":   str(data.get("leistungen", "") or ""),
            "kontakt_text": str(data.get("kontakt_text", "") or ""),
        }
    except Exception:
        return empty


# ── Website Deep-Crawl ─────────────────────────
def _website_deep_crawl(url: str, firma: str = "") -> dict:
    """
    Crawlt Homepage + findet und liest Unterseiten:
    Über uns, Leistungen, Kontakt, Impressum.
    Erkennt auch Tech-Stack soweit möglich.
    """
    result = {
        "url":          url,
        "score":        "keine",
        "titel":        "",
        "beschreibung": "",
        "über_uns":     "",
        "leistungen":   "",
        "kontakt_text": "",
        "technologie":  [],
        "wörter":       0,
        "unterseiten":  [],
        "hat_ssl":      url.startswith("https") if url else False,
        "hat_viewport": False,
        "hat_nav":      False,
    }

    if not url:
        return result
    if not url.startswith("http"):
        url = "https://" + url
        result["url"] = url

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

            try:
                resp = page.goto(url, timeout=18000, wait_until="networkidle")
                if not resp or resp.status >= 400:
                    browser.close()
                    result["score"] = "keine"
                    return result

                # Kurz warten – JS-lastiges CMS (WordPress etc.) braucht Zeit
                page.wait_for_timeout(800)
                result["titel"]      = page.title()
                result["hat_ssl"]    = url.startswith("https")
                result["hat_viewport"] = bool(page.evaluate("() => !!document.querySelector('meta[name=viewport]')"))
                result["hat_nav"]    = bool(page.evaluate("() => !!document.querySelector('nav, [role=navigation]')"))

                # Meta-Description
                desc = page.evaluate("""() => {
                    const m = document.querySelector('meta[name=description]');
                    return m ? m.content : '';
                }""")
                result["beschreibung"] = desc[:200] if desc else ""

                # Tech-Stack-Hinweise aus HTML
                html = page.content()
                techs = []
                tech_hints = {
                    "WordPress":   ["wp-content", "wp-includes", "wordpress"],
                    "Jimdo":       ["jimdo.com", "jimdosite"],
                    "Wix":         ["wix.com", "wixsite"],
                    "Squarespace": ["squarespace.com"],
                    "TYPO3":       ["typo3"],
                    "Joomla":      ["joomla"],
                    "Shopify":     ["shopify"],
                    "Bootstrap":   ["bootstrap.min.css", "bootstrap.css"],
                    "jQuery":      ["jquery.min.js", "jquery-"],
                    "React":       ["react.js", "react.min.js", "__NEXT_DATA__"],
                }
                for tech, hints in tech_hints.items():
                    if any(h in html.lower() for h in hints):
                        techs.append(tech)
                result["technologie"] = techs

                # Haupttext
                main_text = page.evaluate("""() => {
                    document.querySelectorAll('script,style,nav,footer,header').forEach(e=>e.remove());
                    return document.body ? document.body.innerText : '';
                }""")
                result["wörter"] = len(main_text.split())

                # Alle internen Nav-Links sammeln (kein Keyword-Filter mehr)
                # Playwright liest jeden Link mit seinem Ankertext
                links_with_text = page.evaluate("""() => {
                    const seen = new Set();
                    return Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({
                            href: a.href,
                            text: (a.innerText || a.title || a.getAttribute('aria-label') || '').trim()
                        }))
                        .filter(l => l.href.startsWith(window.location.origin))
                        .filter(l => !l.href.match(/\\.(pdf|jpg|png|gif|svg|zip|webp|mp4)$/i))
                        .filter(l => !l.href.includes('#'))
                        .filter(l => l.text.length > 1)
                        .filter(l => { if (seen.has(l.href)) return false; seen.add(l.href); return true; });
                }""")

                # Alle Unterseiten crawlen (max 8) und Text sammeln
                # LLM entscheidet danach was was ist – kein Keyword-Matching
                visited_urls  = {url}
                all_page_texts = [("homepage", main_text[:800])]

                for entry in links_with_text[:40]:
                    if len(visited_urls) > 9:
                        break
                    link      = entry.get("href", "")
                    link_text = entry.get("text", "").strip()
                    if not link or link in visited_urls:
                        continue
                    # Datenschutz/Login/Admin-Seiten überspringen
                    skip = ["datenschutz", "privacy", "login", "admin", "wp-", "cookie",
                            "impressum", "sitemap", "feed", "xmlrpc"]
                    if any(s in link.lower() for s in skip):
                        continue
                    visited_urls.add(link)
                    try:
                        sub_page = context.new_page()
                        sub_page.goto(link, timeout=10000, wait_until="networkidle")
                        sub_page.wait_for_timeout(400)
                        text = sub_page.evaluate("""() => {
                            document.querySelectorAll('script,style,nav,footer,header').forEach(e=>e.remove());
                            return document.body ? document.body.innerText : '';
                        }""")
                        sub_page.close()
                        clean = " ".join(text.split())
                        if len(clean) > 40:
                            all_page_texts.append((link_text, clean[:600]))
                            result["unterseiten"].append(link)
                    except Exception:
                        pass

                # Gesammelten Text mit LLM strukturieren lassen
                combined = "\n\n".join(
                    f"[Seite: {label}]\n{txt}"
                    for label, txt in all_page_texts
                )
                llm_structure = _llm_structure_website(combined, firma)
                result["über_uns"]     = llm_structure.get("über_uns", "")
                result["leistungen"]   = llm_structure.get("leistungen", "")
                result["kontakt_text"] = llm_structure.get("kontakt_text", "")

                # Website-Score
                problems = []
                if result["wörter"] < 80:    problems.append("kaum Textinhalt")
                if not result["hat_viewport"]: problems.append("nicht mobil-optimiert")
                if not result["hat_ssl"]:     problems.append("kein HTTPS")
                if not result["hat_nav"]:     problems.append("keine Navigation")
                if result["wörter"] < 30:
                    result["score"] = "schlecht"
                elif len(problems) >= 3:
                    result["score"] = "schlecht"
                elif len(problems) >= 1:
                    result["score"] = "schwach"
                else:
                    result["score"] = "gut"

                browser.close()

            except PWTimeout:
                browser.close()
                result["score"] = "schlecht"

    except Exception as e:
        import logging
        logging.getLogger("chanti").error(f"_website_deep_crawl Fehler: {e}", exc_info=True)
        if result["wörter"] > 0:
            # Seite wurde teilweise geladen – Score nicht auf "keine" setzen
            result["score"] = result.get("score") or "schwach"
        else:
            result["score"] = "keine"

    return result


# ── Schwächen zusammenfassen ───────────────────
def _schwächen_analyse(website: dict, social: dict, bewertung: dict) -> list[str]:
    problems = []
    s = website.get("score", "keine")
    if s == "keine":
        problems.append("Keine Website vorhanden")
    else:
        if not website.get("hat_ssl"):        problems.append("Kein HTTPS/SSL")
        if not website.get("hat_viewport"):   problems.append("Nicht mobil-optimiert")
        if website.get("wörter", 0) < 100:    problems.append("Sehr wenig Inhalt")
        if not website.get("über_uns"):       problems.append("Keine Über-uns-Seite")
        if not website.get("leistungen"):     problems.append("Keine Leistungsübersicht")
        techs = website.get("technologie", [])
        if "Jimdo" in techs or "Wix" in techs:
            problems.append(f"Veralteter Baukasten ({', '.join(techs)})")
    if not social.get("facebook_url") and not social.get("instagram_url"):
        problems.append("Kein Social Media")
    elif not social.get("facebook_aktiv"):
        problems.append("Social Media inaktiv")
    rating = bewertung.get("rating")
    if rating and float(rating) < 4.0:
        problems.append(f"Schwache Google-Bewertung ({rating}★)")
    elif not rating:
        problems.append("Kein Google-Eintrag gefunden")
    return problems


# ── Gezielte Seiten-Suche per DDG ──────────────
def _find_and_crawl_page(domain: str, keyword: str) -> str:
    """
    Sucht via DDG nach einer spezifischen Unterseite (z.B. Leistungen)
    auf der Domain und crawlt sie mit Playwright.
    Fallback wenn normaler Nav-Crawl die Seite nicht findet.
    """
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        query = f"site:{domain} {keyword}"
        hits  = list(DDGS().text(query, max_results=3, region="de-de"))
        target_url = ""
        for h in hits:
            url = h.get("href", "")
            if domain in url:
                target_url = url
                break

        if not target_url:
            return ""

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page    = browser.new_page()
            page.goto(target_url, timeout=12000, wait_until="networkidle")
            page.wait_for_timeout(400)
            text = page.evaluate("""() => {
                document.querySelectorAll('script,style,nav,footer,header').forEach(e=>e.remove());
                return document.body ? document.body.innerText : '';
            }""")
            browser.close()
        return " ".join(text.split())[:800]
    except Exception:
        return ""


# ── Website-URL Ermittlung ─────────────────────
def _find_website_url(firma: str, ort: str, ddg_data: dict) -> str:
    """
    Findet die echte Website-URL der Firma.
    Strategie 1: Alle DDG-Ergebnisse durchsuchen (alle Queries, mehr Results)
    Strategie 2: Gezielter DDG-Search nach Homepage
    Strategie 3: URL aus Firmenname konstruieren und prüfen
    """
    skip = ["gelbeseiten", "11880", "facebook", "instagram", "google",
            "wikipedia", "yelp", "cylex", "meinestadt", "das-oertliche",
            "handelsregister", "northdata", "bundesanzeiger", "xing",
            "linkedin", "kununu", "jameda", "doctolib", "tripadvisor",
            "booking.com", "trustpilot", "golocal", "wlw.de"]

    def is_real_website(url: str) -> bool:
        return bool(url) and not any(s in url.lower() for s in skip)

    # Strategie 1: Alle vorhandenen DDG-Ergebnisse durchsuchen
    for key in ("snippets", "bewertungen_raw", "news"):
        for item in ddg_data.get(key, []):
            url = item.get("url", "")
            if is_real_website(url):
                return url

    # Strategie 2: Neue gezielte DDG-Suche mit mehr Ergebnissen
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        ddg = DDGS()
        for query in [
            f'{firma} {ort} Website',
            f'{firma} {ort}',
            f'{firma} Homepage',
        ]:
            try:
                hits = list(ddg.text(query, max_results=10, region="de-de"))
                for h in hits:
                    url = h.get("href", "")
                    if is_real_website(url):
                        return url
                time.sleep(0.3)
            except Exception:
                pass
    except Exception:
        pass

    return ""


# ── Einzelfirma recherchieren ──────────────────
def _research_single(firma: str, ort: str, branche: str = "") -> dict:
    """Vollständige Recherche für eine Firma."""
    dossier = {
        "firma":           firma,
        "ort":             ort,
        "branche_erkannt": branche,
        "recherchiert_am": datetime.now().isoformat(),
    }

    # 1. DDG
    ddg = _ddg_recherche(firma, ort)
    dossier["ddg_infos"] = ddg

    # 2. Gelbe Seiten
    gs = _gelbeseiten_scrape(firma, ort)
    dossier["kontakt"] = gs

    # 3. Website-URL ermitteln (mehrere Strategien)
    website_url = _find_website_url(firma, ort, ddg)

    # 4. Website Deep-Crawl
    website = _website_deep_crawl(website_url, firma)

    # 4b. Fehlende Inhalte per gezielter DDG-Suche nachschlagen
    # Funktioniert auch wenn CMS-Navigation nicht standard-konform ist
    if website_url:
        from urllib.parse import urlparse
        domain = urlparse(website_url).netloc or urlparse(website_url).path.split("/")[0]
        domain = domain.replace("www.", "")

        if not website.get("leistungen"):
            text = _find_and_crawl_page(domain, "Leistungen")
            if text:
                structured = _llm_structure_website(f"[Seite: Leistungen]\n{text}", firma)
                website["leistungen"] = structured.get("leistungen", "")

        if not website.get("über_uns"):
            text = _find_and_crawl_page(domain, "Über uns")
            if text:
                structured = _llm_structure_website(f"[Seite: Über uns]\n{text}", firma)
                website["über_uns"] = structured.get("über_uns", "")

        if not website.get("kontakt_text"):
            text = _find_and_crawl_page(domain, "Kontakt")
            if text:
                structured = _llm_structure_website(f"[Seite: Kontakt]\n{text}", firma)
                website["kontakt_text"] = structured.get("kontakt_text", "")

    dossier["website"] = website

    # 4c. Kontaktdaten aus Website nachbessern wenn Gelbe Seiten leer war
    # Alle gecrawlten Texte zusammenführen und LLM drüberschauen lassen
    gs = dossier["kontakt"]
    if not gs.get("telefon") or not gs.get("adresse"):
        website_texts = " ".join(filter(None, [
            website.get("kontakt_text", ""),
            website.get("über_uns", ""),
            website.get("leistungen", ""),
        ]))
        if website_texts.strip():
            from_web = _llm_extract(website_texts, firma, ort)
            # Nur fehlende Felder auffüllen
            for key in ("telefon", "adresse", "email", "öffnungszeiten"):
                if not gs.get(key) and from_web.get(key):
                    gs[key] = from_web[key]
            if not gs.get("kategorien") and from_web.get("kategorien"):
                gs["kategorien"] = from_web["kategorien"]
        dossier["kontakt"] = gs

    # 5. Social Media
    social = _social_media_check(firma, ort, website_text=str(ddg))
    dossier["social_media"] = social

    # 6. Google Rating + alle verfügbaren Daten mergen
    bewertung = _google_rating(firma, ort)
    dossier["bewertungen"] = bewertung
    # Google-Daten in kontakt übernehmen falls noch leer
    gs = dossier["kontakt"]
    for key in ("telefon", "adresse", "öffnungszeiten"):
        if bewertung.get(key) and not gs.get(key):
            gs[key] = bewertung[key]
    # Website aus Google falls noch keine gefunden
    if bewertung.get("website") and not dossier["website"].get("url"):
        dossier["website"]["url"] = bewertung["website"]
    dossier["kontakt"] = gs

    # 7. Website-Beschreibung via LLM generieren
    dossier["website_beschreibung"] = _llm_website_beschreibung(website, firma)

    return dossier


# ── Letztes Leads-JSON finden ──────────────────
def _get_latest_leads() -> list[dict] | None:
    """Liest das neueste leads_*.json aus dem leads/ Verzeichnis."""
    if not LEADS_DIR.exists():
        return None
    files = sorted(LEADS_DIR.glob("leads_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None
    with open(files[0], encoding="utf-8") as f:
        data = json.load(f)
    return data.get("leads", []), files[0].name



# ── Website-Beschreibung via LLM ───────────────
def _llm_website_beschreibung(website: dict, firma: str) -> str:
    """Kurze prägnante Beschreibung der Firma basierend auf Website-Inhalt."""
    texte = " ".join(filter(None, [
        website.get("über_uns", ""),
        website.get("leistungen", ""),
        website.get("beschreibung", ""),
    ]))
    if not texte.strip():
        return ""
    try:
        from config import GROQ_API_KEY, GROQ_MODEL
        prompt = f"""Schreibe eine kurze, sachliche Beschreibung (2-3 Sätze) der Firma "{firma}"
basierend auf diesem Website-Text. Keine Werbung, nur Fakten.

Text: {texte[:1500]}

Beschreibung:"""
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 150},
            timeout=15
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return ""


# ── HTML One-Pager Generator ───────────────────
def _generate_html(dossier: dict) -> str:
    firma       = dossier.get("firma", "")
    ort         = dossier.get("ort", "")
    ws          = dossier.get("website", {})
    soc         = dossier.get("social_media", {})
    kon         = dossier.get("kontakt", {})
    datum       = dossier.get("recherchiert_am", "")[:10]
    beschreibung = dossier.get("website_beschreibung", "")

    score = ws.get("score", "keine")
    score_colors = {
        "keine":    ("#ff2d78", "KEINE WEBSITE"),
        "schlecht": ("#ff6b2d", "SCHLECHT"),
        "schwach":  ("#f0c040", "SCHWACH"),
        "gut":      ("#00ff88", "GUT"),
    }
    score_color, score_label = score_colors.get(score, ("#6b5f8a", "UNBEKANNT"))

    techs_html = "".join(f'<span class="badge">{t}</span>' for t in ws.get("technologie", []))
    cats       = ", ".join(kon.get("kategorien", [])) or "–"
    über_uns   = ws.get("über_uns",   "")[:400] or "Nicht gefunden"
    leistungen = ws.get("leistungen", "")[:400] or "Nicht gefunden"
    website_url  = ws.get("url", "")
    website_link = f'<a href="{website_url}" target="_blank">{website_url}</a>' if website_url else "–"

    # Website-Flags als Variablen (f-string-safe)
    ssl_color  = "#00ff88" if ws.get("hat_ssl")      else "#ff2d78"
    ssl_text   = "✓ JA"   if ws.get("hat_ssl")      else "✗ NEIN"
    mob_color  = "#00ff88" if ws.get("hat_viewport") else "#ff2d78"
    mob_text   = "✓ JA"   if ws.get("hat_viewport") else "✗ NEIN"

    # Öffnungszeiten: dict-string → lesbares Format
    oez_raw = kon.get("öffnungszeiten", "") or "–"
    if oez_raw.startswith("[{") or oez_raw.startswith("{"):
        try:
            import ast as _ast
            parsed = _ast.literal_eval(oez_raw)
            if isinstance(parsed, list):
                lines = []
                for entry in parsed:
                    if isinstance(entry, dict):
                        for day, hours in entry.items():
                            lines.append(f"{day.capitalize()}: {hours}")
                oez = " · ".join(lines)
            elif isinstance(parsed, dict):
                oez = " · ".join(f"{k.capitalize()}: {v}" for k,v in parsed.items())
            else:
                oez = oez_raw
        except Exception:
            oez = oez_raw
    else:
        oez = oez_raw

    # Social Media – echte Links
    def social_html(url, label, aktiv=False):
        if not url:
            return f'<span style="color:var(--muted);font-size:0.85rem">–</span>'
        color = "#00ff88" if aktiv else "#f0c040"
        status = "AKTIV" if aktiv else "VORHANDEN"
        return (f'<a href="{url}" target="_blank" style="color:{color};font-family:'
                f'\'Share Tech Mono\',monospace;font-size:0.7rem;letter-spacing:2px;'
                f'text-decoration:none;border:1px solid {color}44;padding:2px 8px;'
                f'background:{color}11">{status} ↗</a>')

    fb_html = social_html(soc.get("facebook_url",""),  "Facebook",  soc.get("facebook_aktiv", False))
    ig_html = social_html(soc.get("instagram_url",""), "Instagram", False)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dossier: {firma}</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --neon:#b347ff; --neon2:#00f0ff; --neon3:#ff2d78;
    --bg:#070711; --bg2:#0d0d1a; --bg3:#12122a;
    --border:rgba(179,71,255,0.25); --text:#e0d4ff; --muted:#6b5f8a;
  }}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;padding-bottom:60px;}}
  body::before{{content:'';position:fixed;inset:0;
    background-image:linear-gradient(rgba(179,71,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(179,71,255,0.03) 1px,transparent 1px);
    background-size:40px 40px;pointer-events:none;z-index:0;}}
  .header{{position:relative;background:linear-gradient(180deg,#0d0020 0%,var(--bg2) 100%);
    border-bottom:1px solid var(--border);padding:20px 40px 24px;z-index:1;}}
  .header::after{{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;
    background:linear-gradient(90deg,transparent,var(--neon),var(--neon2),var(--neon3),var(--neon),transparent);
    animation:scanline 3s linear infinite;}}
  @keyframes scanline{{0%{{background-position:-200% 0}}100%{{background-position:200% 0}}}}
  .header-top{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;}}
  .brand{{font-family:'Share Tech Mono',monospace;font-size:0.7rem;letter-spacing:4px;color:var(--muted);}}
  .brand span{{color:var(--neon);}}
  .date{{font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:var(--muted);letter-spacing:2px;}}
  .firma-name{{font-family:'Share Tech Mono',monospace;font-size:clamp(1.6rem,4vw,2.8rem);
    letter-spacing:4px;color:#fff;text-shadow:0 0 20px var(--neon2),0 0 40px var(--neon2);margin-bottom:8px;}}
  .firma-meta{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;}}
  .firma-ort{{font-size:1rem;color:var(--neon2);letter-spacing:2px;}}
  .score-badge{{font-family:'Share Tech Mono',monospace;font-size:0.7rem;letter-spacing:3px;
    padding:4px 12px;border:1px solid {score_color};color:{score_color};box-shadow:0 0 10px {score_color}44;}}
  .website-url a{{font-family:'Share Tech Mono',monospace;font-size:0.75rem;
    color:var(--neon2);text-decoration:none;}}
  .beschreibung{{margin-top:12px;font-size:0.95rem;color:#b0a8cc;line-height:1.6;
    max-width:800px;border-left:2px solid var(--neon);padding-left:12px;}}
  .content{{position:relative;z-index:1;padding:32px 40px;}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:20px;}}
  .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;}}
  @media(max-width:700px){{.grid-2{{grid-template-columns:1fr;}}}}
  .card{{background:var(--bg2);border:1px solid var(--border);padding:20px 24px;position:relative;transition:border-color 0.2s;}}
  .card::before{{content:'';position:absolute;top:-1px;left:-1px;width:20px;height:20px;
    border-top:2px solid var(--neon2);border-left:2px solid var(--neon2);}}
  .card::after{{content:'';position:absolute;bottom:-1px;right:-1px;width:20px;height:20px;
    border-bottom:2px solid var(--neon);border-right:2px solid var(--neon);}}
  .card:hover{{border-color:rgba(179,71,255,0.5);}}
  .card-title{{font-family:'Share Tech Mono',monospace;font-size:0.65rem;letter-spacing:4px;
    color:var(--muted);margin-bottom:16px;display:flex;align-items:center;gap:8px;}}
  .card-title::after{{content:'';flex:1;height:1px;background:linear-gradient(90deg,var(--border),transparent);}}
  .data-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;
    padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.95rem;}}
  .data-row:last-child{{border-bottom:none;}}
  .data-label{{color:var(--muted);font-size:0.85rem;flex-shrink:0;}}
  .data-value{{color:var(--text);text-align:right;word-break:break-word;}}
  .data-value a{{color:var(--neon2);text-decoration:none;}}
  .badge{{display:inline-block;font-family:'Share Tech Mono',monospace;font-size:0.65rem;
    padding:2px 8px;margin:2px;border:1px solid var(--neon);color:var(--neon);background:rgba(179,71,255,0.08);}}
  .no-data{{color:var(--muted);font-size:0.9rem;font-style:italic;}}
  .content-box{{background:var(--bg3);padding:12px 14px;margin-top:4px;border-left:1px solid var(--border);
    font-size:0.85rem;line-height:1.6;color:#a09cbb;max-height:120px;overflow-y:auto;}}
  .content-box::-webkit-scrollbar{{width:3px;}}
  .content-box::-webkit-scrollbar-thumb{{background:var(--neon);}}
  .footer{{position:relative;z-index:1;text-align:center;padding:20px;
    font-family:'Share Tech Mono',monospace;font-size:0.6rem;letter-spacing:3px;
    color:var(--muted);border-top:1px solid var(--border);}}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <div class="brand">CHANTI <span>//</span> LEAD INTELLIGENCE</div>
    <div class="date">RECHERCHIERT: {datum}</div>
  </div>
  <div class="firma-name">{firma.upper()}</div>
  <div class="firma-meta">
    <span class="firma-ort">📍 {ort}</span>
    <span class="score-badge">WEBSITE: {score_label}</span>
    <span class="website-url">{website_link}</span>
  </div>
  {f'<div class="beschreibung">{beschreibung}</div>' if beschreibung else ""}
</div>

<div class="content">

  <!-- Kontakt + Website Analyse + Social Media -->
  <div class="grid">

    <div class="card">
      <div class="card-title">KONTAKT</div>
      <div class="data-row">
        <span class="data-label">Telefon</span>
        <span class="data-value">{kon.get("telefon") or "–"}</span>
      </div>
      <div class="data-row">
        <span class="data-label">Adresse</span>
        <span class="data-value">{kon.get("adresse") or "–"}</span>
      </div>
      <div class="data-row">
        <span class="data-label">E-Mail</span>
        <span class="data-value">{kon.get("email") or "–"}</span>
      </div>
      <div class="data-row">
        <span class="data-label">Kategorien</span>
        <span class="data-value">{cats}</span>
      </div>
      <div class="data-row">
        <span class="data-label">Öffnungszeiten</span>
        <span class="data-value">{oez}</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">WEBSITE ANALYSE</div>
      <div class="data-row">
        <span class="data-label">Score</span>
        <span class="data-value" style="color:{score_color};font-family:'Share Tech Mono',monospace;letter-spacing:2px;">{score_label}</span>
      </div>
      <div class="data-row">
        <span class="data-label">Wörter</span>
        <span class="data-value">{ws.get("wörter", 0)}</span>
      </div>
      <div class="data-row">
        <span class="data-label">HTTPS</span>
        <span class="data-value" style="color:{ssl_color}">{ssl_text}</span>
      </div>
      <div class="data-row">
        <span class="data-label">Mobil</span>
        <span class="data-value" style="color:{mob_color}">{mob_text}</span>
      </div>
      <div class="data-row">
        <span class="data-label">Technologie</span>
        <span class="data-value">{techs_html or '<span class="no-data">unbekannt</span>'}</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">SOCIAL MEDIA</div>
      <div class="data-row">
        <span class="data-label">Facebook</span>
        <span class="data-value">{fb_html}</span>
      </div>
      <div class="data-row">
        <span class="data-label">Instagram</span>
        <span class="data-value">{ig_html}</span>
      </div>
    </div>

  </div>

  <!-- Über uns + Leistungen -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title">ÜBER UNS</div>
      <div class="content-box">{über_uns}</div>
    </div>
    <div class="card">
      <div class="card-title">LEISTUNGEN</div>
      <div class="content-box">{leistungen}</div>
    </div>
  </div>

</div>

<div class="footer">
  CHANTI LEAD INTELLIGENCE // {datum} // KEVIN WEBENTWICKLUNG
</div>

</body>
</html>"""


def _save_dossier(dossier: dict) -> Path:
    RESEARCH_DIR.mkdir(exist_ok=True)
    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name  = re.sub(r"[^\w]", "_", dossier["firma"].lower())[:30]
    safe_ort   = re.sub(r"[^\w]", "_", dossier["ort"].lower())[:15]

    # JSON speichern
    json_path = RESEARCH_DIR / f"research_{safe_name}_{safe_ort}_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dossier, f, ensure_ascii=False, indent=2)

    # HTML One-Pager speichern
    html_path = RESEARCH_DIR / f"research_{safe_name}_{safe_ort}_{ts}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(_generate_html(dossier))

    return json_path




# ── Kurzfassung für Chat ───────────────────────
def _format_dossier(dossier: dict, index: int = 1) -> list[str]:
    lines = []
    ws  = dossier.get("website", {})
    soc = dossier.get("social_media", {})
    bew = dossier.get("bewertungen", {})
    kon = dossier.get("kontakt", {})

    lines.append(f"**{index}. {dossier['firma']}** – {dossier['ort']}")

    # Kontakt
    tel  = kon.get("telefon") or ""
    adr  = kon.get("adresse") or ""
    cats = ", ".join(kon.get("kategorien", []))
    if adr:  lines.append(f"   📍 {adr}")
    if tel:  lines.append(f"   📞 {tel}")
    if cats: lines.append(f"   🏷️  {cats}")

    # Website
    score_icon = {"keine":"🔴","schlecht":"🟠","schwach":"🟡","gut":"🟢"}.get(ws.get("score","keine"),"⚪")
    techs = ", ".join(ws.get("technologie",[])) or "unbekannt"
    lines.append(f"   🌐 {ws.get('url','–')}  {score_icon} {ws.get('score','keine').upper()}")
    if ws.get("technologie"): lines.append(f"   ⚙️  Tech: {techs}")

    # Social
    if soc.get("facebook_url"):
        aktiv = "aktiv" if soc.get("facebook_aktiv") else "inaktiv"
        fol   = f" · {soc['facebook_follower']} Follower" if soc.get("facebook_follower") else ""
        lines.append(f"   📘 Facebook: {aktiv}{fol}")
    else:
        lines.append(f"   📘 Facebook: nicht gefunden")
    if soc.get("instagram_url"):
        lines.append(f"   📸 Instagram: vorhanden")
    else:
        lines.append(f"   📸 Instagram: nicht gefunden")

    # Bewertung
    if bew.get("rating"):
        lines.append(f"   ⭐ Google: {bew['rating']}★ ({bew.get('anzahl_reviews',0)} Reviews)")

    # Schwächen
    if dossier.get("schwächen"):
        lines.append(f"   ⚠️  Schwächen: {', '.join(dossier['schwächen'])}")

    # Gesprächsöffner
    gesp = dossier.get("gesprächsöffner", "")
    if gesp:
        lines.append(f"   💬 Gesprächsöffner:")
        for line in gesp.split("\n"):
            if line.strip():
                lines.append(f"      {line.strip()}")

    lines.append("")
    return lines


# ── Haupt-Funktion ─────────────────────────────
def execute(firma: str, ort: str) -> str:
    dossier  = _research_single(firma, ort)
    filepath = _save_dossier(dossier)
    lines = [
        f"✅ Dossier für **{firma}** gespeichert: `research/{filepath.name}`",
        f"📄 HTML-Report: `research/{filepath.stem}.html`",
        ""
    ]
    lines += _format_dossier(dossier)
    return "\n".join(lines).strip()
