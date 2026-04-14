"""Skill: DuckDuckGo Websuche"""

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

def execute(query: str) -> str:
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        results = list(DDGS().text(query, max_results=4, region="de-de"))
        if not results:
            return "Keine Ergebnisse gefunden."
        output = f"Suchergebnisse für '{query}':\n"
        for r in results:
            output += f"- {r['title']}: {r['body'][:200]}\n  URL: {r['href']}\n"
        return output.strip()
    except Exception as e:
        return f"Suche fehlgeschlagen: {e}"
