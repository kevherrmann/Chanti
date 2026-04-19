"""Domain-Alter via RDAP (modernes WHOIS-Nachfolgerprotokoll, kein Key nötig).

Hinweis: Für .de-Domains liefert DENIC nur eingeschränkte Daten ohne Registrierungsdatum.
Deshalb als Fallback archive.org Wayback-Machine: Erstes Capture ~= Mindest-Alter.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("chanti")


def estimate_domain_age_years(website_url: Optional[str]) -> Optional[float]:
    """Gibt ungefähres Alter in Jahren zurück, oder None wenn unbekannt."""
    if not website_url:
        return None

    host = _extract_host(website_url)
    if not host:
        return None

    # 1. RDAP versuchen
    age = _via_rdap(host)
    if age is not None:
        return age

    # 2. Wayback-Machine als Fallback
    return _via_wayback(host)


def _extract_host(url: str) -> Optional[str]:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _via_rdap(host: str) -> Optional[float]:
    """rdap.org leitet an die zuständige Registry weiter."""
    try:
        r = requests.get(
            f"https://rdap.org/domain/{host}",
            timeout=8,
            headers={"Accept": "application/rdap+json"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        for event in data.get("events", []) or []:
            if event.get("eventAction") == "registration":
                iso = event.get("eventDate")
                if iso:
                    dt = _parse_iso(iso)
                    if dt:
                        return _years_since(dt)
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"RDAP Fehler für {host}: {e}")
    return None


def _via_wayback(host: str) -> Optional[float]:
    """archive.org: wann wurde die Domain erstmals gespeichert?"""
    try:
        r = requests.get(
            "https://archive.org/wayback/available",
            params={"url": host, "timestamp": "1990"},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        closest = (data.get("archived_snapshots") or {}).get("closest")
        if not closest or not closest.get("timestamp"):
            return None
        ts = closest["timestamp"]  # YYYYMMDDhhmmss
        dt = datetime.strptime(ts[:8], "%Y%m%d")
        return _years_since(dt)
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.debug(f"Wayback Fehler für {host}: {e}")
    return None


def _parse_iso(iso: str) -> Optional[datetime]:
    try:
        # Mit/ohne Zeitzone tolerieren
        iso = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(iso).replace(tzinfo=None)
    except ValueError:
        return None


def _years_since(dt: datetime) -> float:
    delta = datetime.now() - dt
    return round(delta.days / 365.25, 1)
