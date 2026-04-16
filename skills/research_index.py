"""
Skill: Research Index
Erstellt eine übersichtliche Index-HTML aller Dossiers im research/ Ordner.
"""

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "research_index",
        "description": (
            "Erstellt eine übersichtliche HTML-Übersichtsseite aller recherchierten Firmen. "
            "Nutze dies wenn Kevin sagt: 'Zeig mir alle Dossiers', 'Erstelle einen Überblick' "
            "oder 'Mach eine Übersicht aller Leads'."
        ),
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
    from pathlib import Path
    from datetime import datetime

    research_dir = Path(__file__).parent.parent / "research"

    if not research_dir.exists():
        return "Kein research/ Verzeichnis gefunden. Bitte zuerst Firmen recherchieren."

    files = sorted(
        research_dir.glob("research_*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    # Nur Einzel-Dossiers, keine overview-Files
    files = [f for f in files if "overview" not in f.name]

    if not files:
        return "Keine Dossiers gefunden. Bitte zuerst Firmen recherchieren."

    dossiers = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            d["_html_file"] = f.stem + ".html"
            dossiers.append(d)
        except Exception:
            pass

    # Score-Statistik
    scores = [d.get("website", {}).get("score", "keine") for d in dossiers]
    count  = {s: scores.count(s) for s in ["keine", "schlecht", "schwach", "gut"]}

    # Karten-HTML generieren
    cards_html = ""
    for d in dossiers:
        firma  = d.get("firma", "–")
        ort    = d.get("ort", "–")
        datum  = d.get("recherchiert_am", "")[:10]
        ws     = d.get("website", {})
        kon    = d.get("kontakt", {})
        beschr = d.get("website_beschreibung", "")
        score  = ws.get("score", "keine")

        score_colors = {
            "keine":    ("#ff2d78", "KEINE WEBSITE"),
            "schlecht": ("#ff6b2d", "SCHLECHT"),
            "schwach":  ("#f0c040", "SCHWACH"),
            "gut":      ("#00ff88", "GUT"),
        }
        sc, sl = score_colors.get(score, ("#6b5f8a", "?"))

        tel  = kon.get("telefon", "–")
        cats = ", ".join(kon.get("kategorien", [])[:2]) or "–"
        html_link = d["_html_file"]

        techs = " ".join(
            f'<span class="badge">{t}</span>'
            for t in ws.get("technologie", [])
        )

        cards_html += f"""
    <div class="card" onclick="window.open('{html_link}','_blank')">
      <div class="card-header">
        <div class="card-name">{firma.upper()}</div>
        <span class="score-pill" style="color:{sc};border-color:{sc}44;background:{sc}11">{sl}</span>
      </div>
      <div class="card-ort">📍 {ort}</div>
      {f'<div class="card-beschr">{beschr[:120]}...</div>' if beschr else ""}
      <div class="card-data">
        <span class="data-item">📞 {tel}</span>
        <span class="data-item">🏷 {cats}</span>
      </div>
      {f'<div class="card-techs">{techs}</div>' if techs else ""}
      <div class="card-footer">
        <span class="card-date">{datum}</span>
        <span class="card-link">DOSSIER ÖFFNEN ↗</span>
      </div>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chanti – Lead Intelligence Index</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --neon:#b347ff; --neon2:#00f0ff; --neon3:#ff2d78;
    --bg:#070711; --bg2:#0d0d1a; --bg3:#12122a;
    --border:rgba(179,71,255,0.25); --text:#e0d4ff; --muted:#6b5f8a;
  }}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;}}
  body::before{{content:'';position:fixed;inset:0;
    background-image:linear-gradient(rgba(179,71,255,0.03) 1px,transparent 1px),
    linear-gradient(90deg,rgba(179,71,255,0.03) 1px,transparent 1px);
    background-size:40px 40px;pointer-events:none;z-index:0;}}

  .header{{position:relative;background:linear-gradient(180deg,#0d0020,var(--bg2));
    border-bottom:1px solid var(--border);padding:24px 40px;z-index:1;}}
  .header::after{{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;
    background:linear-gradient(90deg,transparent,var(--neon),var(--neon2),var(--neon3),var(--neon),transparent);
    animation:scan 3s linear infinite;}}
  @keyframes scan{{0%{{background-position:-200% 0}}100%{{background-position:200% 0}}}}
  .header-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;}}
  .brand{{font-family:'Share Tech Mono',monospace;font-size:0.7rem;letter-spacing:4px;color:var(--muted);}}
  .brand span{{color:var(--neon);}}
  .title{{font-family:'Share Tech Mono',monospace;font-size:2rem;letter-spacing:6px;color:#fff;
    text-shadow:0 0 20px var(--neon2),0 0 40px var(--neon2);}}
  .subtitle{{font-size:0.85rem;color:var(--muted);letter-spacing:2px;margin-top:6px;}}

  .stats{{display:flex;gap:24px;margin-top:20px;flex-wrap:wrap;}}
  .stat{{text-align:center;}}
  .stat-val{{font-family:'Share Tech Mono',monospace;font-size:1.4rem;}}
  .stat-label{{font-size:0.6rem;letter-spacing:2px;color:var(--muted);}}

  .search-bar{{position:relative;z-index:1;padding:20px 40px;}}
  .search-input{{width:100%;max-width:400px;background:var(--bg3);border:1px solid var(--border);
    color:var(--text);padding:10px 16px;font-family:'Rajdhani',sans-serif;font-size:1rem;
    outline:none;transition:border-color 0.2s;}}
  .search-input:focus{{border-color:var(--neon);box-shadow:0 0 12px rgba(179,71,255,0.2);}}
  .search-input::placeholder{{color:var(--muted);}}

  .filter-bar{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;}}
  .filter-btn{{font-family:'Share Tech Mono',monospace;font-size:0.65rem;letter-spacing:2px;
    padding:4px 12px;border:1px solid var(--border);color:var(--muted);background:transparent;
    cursor:pointer;transition:all 0.2s;}}
  .filter-btn:hover,.filter-btn.active{{border-color:var(--neon);color:var(--neon);
    background:rgba(179,71,255,0.08);}}

  .grid{{position:relative;z-index:1;padding:20px 40px;
    display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px;}}

  .card{{background:var(--bg2);border:1px solid var(--border);padding:20px;
    cursor:pointer;transition:all 0.2s;position:relative;}}
  .card::before{{content:'';position:absolute;top:-1px;left:-1px;width:16px;height:16px;
    border-top:2px solid var(--neon2);border-left:2px solid var(--neon2);}}
  .card::after{{content:'';position:absolute;bottom:-1px;right:-1px;width:16px;height:16px;
    border-bottom:2px solid var(--neon);border-right:2px solid var(--neon);}}
  .card:hover{{border-color:rgba(179,71,255,0.6);transform:translateY(-2px);
    box-shadow:0 8px 30px rgba(179,71,255,0.15);}}
  .card-header{{display:flex;justify-content:space-between;align-items:flex-start;
    gap:8px;margin-bottom:8px;}}
  .card-name{{font-family:'Share Tech Mono',monospace;font-size:0.85rem;letter-spacing:2px;
    color:#fff;line-height:1.3;flex:1;}}
  .score-pill{{font-family:'Share Tech Mono',monospace;font-size:0.6rem;letter-spacing:2px;
    padding:2px 8px;border:1px solid;flex-shrink:0;}}
  .card-ort{{font-size:0.85rem;color:var(--neon2);margin-bottom:8px;}}
  .card-beschr{{font-size:0.82rem;color:var(--muted);line-height:1.4;margin-bottom:10px;
    border-left:1px solid var(--border);padding-left:8px;}}
  .card-data{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px;}}
  .data-item{{font-size:0.8rem;color:var(--text);}}
  .card-techs{{margin-bottom:8px;}}
  .badge{{display:inline-block;font-family:'Share Tech Mono',monospace;font-size:0.6rem;
    padding:1px 6px;margin:2px;border:1px solid var(--neon);color:var(--neon);
    background:rgba(179,71,255,0.08);}}
  .card-footer{{display:flex;justify-content:space-between;align-items:center;
    margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.04);}}
  .card-date{{font-family:'Share Tech Mono',monospace;font-size:0.6rem;color:var(--muted);}}
  .card-link{{font-family:'Share Tech Mono',monospace;font-size:0.6rem;letter-spacing:2px;
    color:var(--neon2);}}

  .empty{{position:relative;z-index:1;text-align:center;padding:60px;
    color:var(--muted);font-family:'Share Tech Mono',monospace;letter-spacing:2px;}}

  .footer{{position:relative;z-index:1;text-align:center;padding:20px;
    font-family:'Share Tech Mono',monospace;font-size:0.6rem;
    letter-spacing:3px;color:var(--muted);border-top:1px solid var(--border);}}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <div class="brand">CHANTI <span>//</span> LEAD INTELLIGENCE</div>
    <div style="font-family:'Share Tech Mono',monospace;font-size:0.7rem;color:var(--muted)">
      GENERIERT: {datetime.now().strftime('%d.%m.%Y %H:%M')}
    </div>
  </div>
  <div class="title">LEAD INDEX</div>
  <div class="subtitle">{len(dossiers)} FIRMEN RECHERCHIERT</div>
  <div class="stats">
    <div class="stat">
      <div class="stat-val" style="color:#ff2d78">{count['keine']}</div>
      <div class="stat-label">KEINE WEBSITE</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#ff6b2d">{count['schlecht']}</div>
      <div class="stat-label">SCHLECHT</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#f0c040">{count['schwach']}</div>
      <div class="stat-label">SCHWACH</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#00ff88">{count['gut']}</div>
      <div class="stat-label">GUT</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:var(--neon2)">{len(dossiers)}</div>
      <div class="stat-label">GESAMT</div>
    </div>
  </div>
</div>

<div class="search-bar">
  <input class="search-input" id="search" placeholder="FIRMA SUCHEN_" oninput="filterCards()">
  <div class="filter-bar">
    <button class="filter-btn active" onclick="setFilter('alle', this)">ALLE</button>
    <button class="filter-btn" onclick="setFilter('keine', this)">KEINE WEBSITE</button>
    <button class="filter-btn" onclick="setFilter('schlecht', this)">SCHLECHT</button>
    <button class="filter-btn" onclick="setFilter('schwach', this)">SCHWACH</button>
    <button class="filter-btn" onclick="setFilter('gut', this)">GUT</button>
  </div>
</div>

<div class="grid" id="grid">
{cards_html}
</div>

<div class="footer">
  CHANTI LEAD INTELLIGENCE // KEVIN WEBENTWICKLUNG // {len(dossiers)} DOSSIERS
</div>

<script>
  let activeFilter = 'alle';

  function setFilter(filter, btn) {{
    activeFilter = filter;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    filterCards();
  }}

  function filterCards() {{
    const q = document.getElementById('search').value.toLowerCase();
    document.querySelectorAll('.card').forEach(card => {{
      const text  = card.innerText.toLowerCase();
      const score = card.querySelector('.score-pill')?.innerText.toLowerCase() || '';
      const matchSearch = !q || text.includes(q);
      const matchFilter = activeFilter === 'alle' || score.includes(activeFilter.replace('_',' '));
      card.style.display = matchSearch && matchFilter ? '' : 'none';
    }});
  }}
</script>
</body>
</html>"""

    # Speichern
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path  = research_dir / f"index_{ts}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return (
        f"✅ Index erstellt: `research/index_{ts}.html`\n\n"
        f"📊 {len(dossiers)} Firmen · "
        f"{count['keine']} ohne Website · "
        f"{count['schlecht']} schlecht · "
        f"{count['schwach']} schwach · "
        f"{count['gut']} gut"
    )
