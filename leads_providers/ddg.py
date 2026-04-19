"""DuckDuckGo-Fallback falls OSM zu wenig liefert und Brave nicht konfiguriert ist."""
from __future__ import annotations

import logging

logger = logging.getLogger("chanti")

_SKIP_DOMAINS = (
    "gelbeseiten", "11880", "cylex", "meinestadt", "yelp",
    "wikipedia", "facebook", "instagram", "google", "xing.com",
    "pinterest", "tiktok", "youtube",
)


def _ddgs():
    try:
        from ddgs import DDGS  # neuer Name
        return DDGS()
    except ImportError:
        from duckduckgo_search import DDGS
        return DDGS()


def search_companies(branche: str, ort: str, needed: int) -> list[dict]:
    """Sucht per DDG nach Firmen. Filtert Verzeichnis-Seiten raus."""
    try:
        results = list(_ddgs().text(
            f"{branche} {ort} Kontakt Telefon",
            max_results=needed * 3,
            region="de-de",
        ))
    except Exception as e:
        logger.error(f"DDG Fehler: {e}")
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for r in results:
        title = (r.get("title") or "").strip()
        href = r.get("href") or ""
        if not title or title.lower() in seen:
            continue
        if any(d in href.lower() for d in _SKIP_DOMAINS):
            continue
        seen.add(title.lower())
        out.append({
            "name":    title,
            "address": "",
            "phone":   "",
            "email":   "",
            "website": href,
            "lat":     None,
            "lon":     None,
            "city":    ort,
        })
        if len(out) >= needed:
            break
    return out
