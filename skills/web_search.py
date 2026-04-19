"""Skill: DuckDuckGo Websuche"""
import logging

logger = logging.getLogger("chanti")

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Sucht im Internet nach aktuellen Informationen. Nutze dies für Fakten, News oder wenn du etwas nicht weißt.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Der Suchbegriff"
                }
            },
            "required": ["query"]
        }
    }
}


def _get_ddgs_class():
    """Lädt DDGS aus ddgs oder duckduckgo_search. Gibt die Klasse zurück."""
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        pass
    from duckduckgo_search import DDGS  # Fallback, älterer Paketname
    return DDGS


def execute(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        return "Suche fehlgeschlagen: leerer Suchbegriff."
    query = query.strip()

    try:
        DDGS = _get_ddgs_class()
    except ImportError as e:
        logger.error(f"DDGS-Lib nicht installiert: {e}")
        return "Suche fehlgeschlagen: DDGS-Lib nicht installiert."

    try:
        # Context-Manager schließt interne HTTP-Session sauber.
        # `timeout` wird vom ddgs-Konstruktor akzeptiert; ältere Versionen
        # ignorieren das Argument leise — wir fangen TypeError ab.
        try:
            ctx = DDGS(timeout=10)
        except TypeError:
            ctx = DDGS()

        with ctx as ddgs:
            raw = ddgs.text(query, max_results=4, region="de-de")
            results = list(raw) if raw else []
    except Exception as e:
        # DDGS wirft bei Rate-Limits, Netzwerkproblemen, Schema-Änderungen
        # verschiedene Exceptions. Wir loggen Typ + Message für Debugging.
        logger.warning(f"web_search '{query}': {type(e).__name__}: {e}")
        return f"Suche fehlgeschlagen: {type(e).__name__}."

    if not results:
        return f"Keine Ergebnisse für '{query}' gefunden."

    lines = [f"Suchergebnisse für '{query}':"]
    for r in results:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "(ohne Titel)").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or r.get("url") or "").strip()
        if len(body) > 200:
            # Bei Wortgrenze abschneiden statt mittendrin.
            cut = body[:200].rsplit(" ", 1)[0]
            body = cut + "…"
        lines.append(f"- {title}: {body}")
        if href:
            lines.append(f"  URL: {href}")
    return "\n".join(lines)
