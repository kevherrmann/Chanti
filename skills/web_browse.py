"""Skill: Webseite aufrufen und Inhalt lesen via Playwright"""
from playwright.sync_api import sync_playwright

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_browse",
        "description": "Ruft eine Webseite auf und liest ihren Textinhalt. Nutze dies um Seiten zu analysieren, Informationen zu finden oder Links zu entdecken.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Die vollständige URL der Seite, z.B. https://mcpservers.org"
                }
            },
            "required": ["url"]
        }
    }
}

def execute(url: str) -> str:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            # Text extrahieren, Boilerplate reduzieren
            text = page.evaluate("""() => {
                // Skripte und Styles entfernen
                document.querySelectorAll('script, style, nav, footer').forEach(e => e.remove());
                return document.body.innerText;
            }""")
            browser.close()
            # Auf 3000 Zeichen begrenzen um Token zu sparen
            text = " ".join(text.split())
            if len(text) > 3000:
                text = text[:3000] + "... [gekürzt]"
            return text if text.strip() else "Seite konnte nicht gelesen werden."
    except Exception as e:
        return f"Fehler beim Aufrufen von {url}: {e}"
