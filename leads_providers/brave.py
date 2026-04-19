"""Brave Search API.

Endpoints:
  - /web/search    : allgemeine Websuche
  - /local/search  : Google-Maps-ähnliche Business-Daten (rating, reviews, open hours)

API-Key kommt aus env BRAVE_API_KEY (via config.py geladen).
Rate-Limit Free-Tier: 1 req/s, 2000/Monat.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger("chanti")

_API_BASE = "https://api.search.brave.com/res/v1"
_LAST_CALL_TS = 0.0
_MIN_INTERVAL = 1.05  # Free-Tier: 1 req/s, leicht drüber


def _throttle() -> None:
    """Hält 1 req/s-Limit ein."""
    global _LAST_CALL_TS
    elapsed = time.time() - _LAST_CALL_TS
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_CALL_TS = time.time()


def _headers() -> dict[str, str]:
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BRAVE_API_KEY fehlt in der .env")
    return {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": key,
    }


def is_configured() -> bool:
    return bool(os.environ.get("BRAVE_API_KEY", "").strip())


def web_search(query: str, count: int = 10, country: str = "DE",
               lang: str = "de") -> list[dict]:
    """Allgemeine Websuche. Gibt Liste von {title, url, description} zurück."""
    _throttle()
    try:
        r = requests.get(
            f"{_API_BASE}/web/search",
            headers=_headers(),
            params={
                "q": query,
                "count": max(1, min(count, 20)),
                "country": country,
                "search_lang": lang,
                "safesearch": "moderate",
            },
            timeout=15,
        )
        if r.status_code == 429:
            logger.warning("Brave Rate-Limit erreicht")
            return []
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("web", {}) or {}).get("results", []) or []:
            results.append({
                "title": item.get("title", "").strip(),
                "url": item.get("url", "").strip(),
                "description": item.get("description", "").strip(),
            })
        return results
    except requests.RequestException as e:
        logger.error(f"Brave web_search failed: {e}")
        return []


def local_search(query: str, count: int = 10) -> list[dict]:
    """Lokale Business-Suche. Liefert rating, reviews, Adresse, Telefon, Website.

    Query-Beispiel: 'Zimmerei Müller Lengerich'
    """
    _throttle()
    try:
        r = requests.get(
            f"{_API_BASE}/local/search",
            headers=_headers(),
            params={
                "q": query,
                "count": max(1, min(count, 20)),
                "country": "DE",
                "search_lang": "de",
            },
            timeout=15,
        )
        if r.status_code == 429:
            logger.warning("Brave Rate-Limit (local) erreicht")
            return []
        if r.status_code == 404:
            # Brave liefert 404 wenn keine lokalen Treffer
            return []
        r.raise_for_status()
        data = r.json()
        results = []
        for item in (data.get("results") or []):
            rating_obj = item.get("rating") or {}
            coords = item.get("coordinates") or {}
            contact = item.get("contact") or {}
            phones = contact.get("telephone") or []
            emails = contact.get("email") or []
            results.append({
                "name": item.get("title", "").strip(),
                "address": (item.get("postal_address") or {}).get("displayAddress", "")
                           or item.get("address", "") or "",
                "phone": (phones[0] if phones else "") or "",
                "email": (emails[0] if emails else "") or "",
                "website": item.get("web_url", "") or item.get("url", ""),
                "rating": rating_obj.get("ratingValue"),
                "review_count": rating_obj.get("ratingCount"),
                "lat": coords.get("latitude"),
                "lon": coords.get("longitude"),
            })
        return results
    except requests.RequestException as e:
        logger.error(f"Brave local_search failed: {e}")
        return []


def find_business_reputation(firma: str, ort: str) -> Optional[dict]:
    """Sucht eine konkrete Firma und gibt rating + review_count + Social-Links zurück.
    Fallback über web_search wenn local_search nichts liefert."""
    # 1. Local-Search versuchen
    locals_ = local_search(f"{firma} {ort}", count=3)
    for loc in locals_:
        if _name_matches(loc["name"], firma):
            return {
                "rating": loc.get("rating"),
                "review_count": loc.get("review_count"),
                "verified_name": loc["name"],
                "address_from_brave": loc.get("address"),
                "phone_from_brave": loc.get("phone"),
                "website_from_brave": loc.get("website"),
            }
    return None


def find_social_profiles(firma: str, ort: Optional[str] = None) -> dict:
    """Sucht Social-Media-Profile per Websuche.
    Gibt {facebook, instagram, linkedin, other: []} zurück."""
    query = f'"{firma}" {ort or ""} facebook OR instagram OR linkedin'
    results = web_search(query, count=10)
    found = {
        "facebook": None,
        "instagram": None,
        "linkedin": None,
        "other": [],
    }
    for r in results:
        url = r["url"].lower()
        if "facebook.com" in url and not found["facebook"]:
            found["facebook"] = r["url"]
        elif "instagram.com" in url and not found["instagram"]:
            found["instagram"] = r["url"]
        elif "linkedin.com" in url and not found["linkedin"]:
            found["linkedin"] = r["url"]
    return found


def _name_matches(a: str, b: str) -> bool:
    """Lockerer Name-Vergleich: zwei Tokens (≥4 Zeichen) aus b müssen in a vorkommen."""
    a = a.lower()
    tokens = [t for t in b.lower().split() if len(t) >= 4]
    if not tokens:
        return b.lower() in a
    matches = sum(1 for t in tokens if t in a)
    return matches >= min(len(tokens), 2)
