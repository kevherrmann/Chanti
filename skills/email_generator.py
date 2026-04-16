"""
Skill: Kaltakquise E-Mail Generator
Liest das neueste Dossier einer Firma und schreibt eine personalisierte E-Mail.
"""

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "email_generator",
        "description": (
            "Schreibt eine personalisierte Kaltakquise-E-Mail für eine Firma basierend "
            "auf ihrem Dossier. Nutze dies wenn Kevin sagt: 'Schreib eine E-Mail für [Firma]' "
            "oder 'Erstelle eine Akquise-Mail für [Firma] in [Ort]'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "firma": {
                    "type": "string",
                    "description": "Name der Firma"
                },
                "ort": {
                    "type": "string",
                    "description": "Stadt der Firma"
                },
                "stil": {
                    "type": "string",
                    "description": "Stil der E-Mail: 'formell', 'locker' oder 'kurz'. Standard: formell"
                }
            },
            "required": ["firma", "ort"]
        }
    }
}


def execute(firma: str, ort: str, stil: str = "formell") -> str:
    import json
    import re
    import requests
    from pathlib import Path

    research_dir = Path(__file__).parent.parent / "research"
    emails_dir   = Path(__file__).parent.parent / "emails"

    # Passendes Dossier suchen
    dossier = None
    if research_dir.exists():
        safe = re.sub(r"[^\w]", "_", firma.lower())[:20]
        # Neueste passende Datei finden
        candidates = sorted(
            research_dir.glob("research_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        for f in candidates:
            if safe[:8] in f.name.lower():
                try:
                    with open(f, encoding="utf-8") as fh:
                        dossier = json.load(fh)
                    break
                except Exception:
                    pass

        # Fallback: einfach das neueste nehmen falls Name nicht matcht
        if not dossier and candidates:
            try:
                with open(candidates[0], encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("firma", "").lower() == firma.lower():
                    dossier = data
            except Exception:
                pass

    # Kontext aus Dossier aufbauen
    if dossier:
        ws       = dossier.get("website", {})
        kon      = dossier.get("kontakt", {})
        soc      = dossier.get("social_media", {})
        beschr   = dossier.get("website_beschreibung", "")
        über_uns = ws.get("über_uns", "")
        leistung = ws.get("leistungen", "")
        score    = ws.get("score", "keine")
        techs    = ", ".join(ws.get("technologie", []))

        score_text = {
            "keine":    "keine Website",
            "schlecht": "eine qualitativ schwache Website",
            "schwach":  "eine verbesserungswürdige Website",
            "gut":      "eine solide Website",
        }.get(score, "eine Website")

        problems = []
        if score in ("keine", "schlecht", "schwach"):
            if not ws.get("hat_ssl"):        problems.append("kein HTTPS")
            if not ws.get("hat_viewport"):   problems.append("nicht mobil-optimiert")
            if ws.get("wörter", 0) < 100:    problems.append("sehr wenig Inhalt")
            if "Jimdo" in techs or "Wix" in techs:
                problems.append(f"veralteter Baukasten ({techs})")
        if not soc.get("facebook_url") and not soc.get("instagram_url"):
            problems.append("kein Social Media")

        kontext = f"""
Firma: {firma}, {ort}
Website-Status: {score_text}
{f"Technologie: {techs}" if techs else ""}
{f"Bekannte Schwächen: {', '.join(problems)}" if problems else ""}
{f"Über die Firma: {beschr}" if beschr else ""}
{f"Leistungen: {leistung[:200]}" if leistung else ""}
{f"Über-uns: {über_uns[:200]}" if über_uns else ""}
Ansprechpartner: {kon.get("email") or "unbekannt"}
"""
    else:
        kontext = f"Firma: {firma}, {ort}\nKein Dossier verfügbar."
        score = "unbekannt"

    # Stil-Anweisung
    stil_map = {
        "formell": "professionell und seriös, Sie-Form, Business-Ton",
        "locker":  "freundlich und direkt, du-Form, modern und sympathisch",
        "kurz":    "sehr kurz (max 5 Sätze), auf den Punkt, kein Fülltext",
    }
    stil_text = stil_map.get(stil.lower(), stil_map["formell"])

    try:
        from config import GROQ_API_KEY, GROQ_MODEL

        prompt = f"""Du bist Kevin, ein Webentwickler aus Ibbenbüren.
Schreibe eine Kaltakquise-E-Mail an diese Firma.

Stil: {stil_text}

Kontext zur Firma:
{kontext}

Anforderungen:
- Betreffzeile am Anfang (Format: "Betreff: ...")
- Bezug auf echte Schwächen/Details der Firma nehmen
- Konkreten Mehrwert nennen, keine leeren Floskeln
- Am Ende: Dein Name Kevin + "Webentwickler aus Ibbenbüren"
- Keine Phrasen wie "Ich hoffe diese E-Mail findet Sie wohlauf"
- Max 150 Wörter (außer bei Stil "formell": max 200)

E-Mail:"""

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json"
            },
            json={
                "model":       GROQ_MODEL,
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens":  500,
            },
            timeout=20
        )

        if not resp.ok:
            return f"Fehler beim Generieren: {resp.status_code}"

        email_text = resp.json()["choices"][0]["message"]["content"].strip()

    except Exception as e:
        return f"Fehler: {e}"

    # E-Mail als TXT speichern
    emails_dir.mkdir(exist_ok=True)
    from datetime import datetime
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w]", "_", firma.lower())[:30]
    filepath  = emails_dir / f"email_{safe_name}_{ts}.txt"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"An: {firma} ({ort})\n")
        f.write(f"Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
        f.write(f"Stil: {stil}\n")
        f.write("-" * 50 + "\n\n")
        f.write(email_text)

    return (
        f"✉️ E-Mail für **{firma}** erstellt und gespeichert: `emails/{filepath.name}`\n\n"
        f"---\n\n{email_text}"
    )
