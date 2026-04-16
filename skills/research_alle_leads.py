"""
Skill: Alle Leads recherchieren
Liest das neueste leads_*.json und erstellt für jede Firma ein Dossier (JSON + HTML).
"""

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "research_alle_leads",
        "description": "Recherchiert alle Firmen aus der letzten Lead-Suche. Erstellt pro Firma ein Dossier mit Website-Analyse, Kontakt, Social Media und Gesprächsöffner. Nutze dies wenn Kevin sagt: alle Leads recherchieren, recherchiere alle, Dossier fuer alle Leads.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}


def execute() -> str:
    import json
    import re
    import time
    from pathlib import Path
    from datetime import datetime

    # Import aus company_research – liegt im selben skills/ Verzeichnis
    import importlib.util
    skill_dir = Path(__file__).parent
    spec = importlib.util.spec_from_file_location(
        "company_research",
        skill_dir / "company_research.py"
    )
    cr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cr)

    # Letztes Leads-File laden
    leads_dir = Path(__file__).parent.parent / "leads"
    if not leads_dir.exists():
        return "Kein leads/ Verzeichnis gefunden. Bitte zuerst eine Lead-Suche durchführen."

    files = sorted(leads_dir.glob("leads_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return "Keine Lead-Dateien gefunden. Bitte zuerst eine Lead-Suche durchführen."

    with open(files[0], encoding="utf-8") as f:
        data = json.load(f)

    leads       = data.get("leads", [])
    source_file = files[0].name

    if not leads:
        return f"Keine Leads in {source_file} gefunden."

    sek_min = len(leads) * 30
    sek_max = len(leads) * 60
    lines = [
        f"🔍 Recherchiere **{len(leads)} Firmen** aus `{source_file}`",
        f"⏱ Geschätzte Dauer: {sek_min}–{sek_max} Sekunden",
        ""
    ]
    dossiers = []

    for i, lead in enumerate(leads, 1):
        firma = lead.get("firma", "")
        ort   = lead.get("ort", "")

        # Ort aus Adresse extrahieren wenn nicht direkt vorhanden
        if not ort:
            adr = lead.get("adresse", "")
            m   = re.search(r"\d{5}\s+([A-ZÄÖÜ][a-zäöüß]+)", adr)
            ort = m.group(1) if m else ""

        if not firma:
            continue

        dossier = cr._research_single(firma, ort, lead.get("branche", ""))

        # Website-URL aus Lead übernehmen falls Crawler nichts gefunden hat
        lead_url = lead.get("website", {}).get("url", "")
        if lead_url and not dossier["website"].get("url"):
            dossier["website"]["url"] = lead_url

        filepath = cr._save_dossier(dossier)
        dossiers.append(dossier)
        lines += cr._format_dossier(dossier, i)
        time.sleep(1)

    # Übersichts-JSON
    research_dir = Path(__file__).parent.parent / "research"
    research_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    overview_path = research_dir / f"research_overview_{ts}.json"
    with open(overview_path, "w", encoding="utf-8") as f:
        json.dump({
            "quelle":          source_file,
            "recherchiert_am": datetime.now().isoformat(),
            "anzahl":          len(dossiers),
            "dossiers":        dossiers,
        }, f, ensure_ascii=False, indent=2)

    lines.insert(0, f"✅ {len(dossiers)} Dossiers gespeichert unter `research/`\n")
    return "\n".join(lines).strip()
