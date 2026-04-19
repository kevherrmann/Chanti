"""Reputation-Analyse: rating/reviews via Brave local search + Social-Media-Links + Impressum-Email.

Benutzt Brave wo möglich, fällt auf Playwright-gestütztes Scraping zurück nur für Impressum.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from leads_providers import brave

logger = logging.getLogger("chanti")

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Impressum-typische Subseiten
_IMPRESSUM_PATHS = [
    "/impressum", "/impressum.html", "/impressum/", "/legal/impressum",
    "/kontakt", "/kontakt.html", "/contact", "/imprint",
]


def collect(firma: str, ort: str, website_url: Optional[str]) -> dict:
    """Sammelt alles was wir über Reputation + Social + Impressum kriegen.

    Gibt ein Dict zurück das in leads_db.upsert_reputation passt."""
    result: dict = {
        "rating": None,
        "review_count": None,
        "has_impressum_email": False,
        "impressum_email": None,
        "social_facebook": None,
        "social_instagram": None,
        "social_linkedin": None,
        "social_other": [],
        "domain_age_years": None,
    }

    # 1. Brave local search → rating/reviews
    if brave.is_configured():
        try:
            rep = brave.find_business_reputation(firma, ort)
            if rep:
                result["rating"] = rep.get("rating")
                result["review_count"] = rep.get("review_count")
        except Exception as e:
            logger.warning(f"Brave reputation failed: {e}")

        # 2. Brave social-profile-Suche
        try:
            social = brave.find_social_profiles(firma, ort)
            result["social_facebook"] = social.get("facebook")
            result["social_instagram"] = social.get("instagram")
            result["social_linkedin"] = social.get("linkedin")
        except Exception as e:
            logger.warning(f"Brave social search failed: {e}")

    # 3. Impressum-Email aus Website ziehen
    if website_url:
        email = _extract_impressum_email(website_url)
        if email:
            result["impressum_email"] = email
            result["has_impressum_email"] = True

    return result


def _extract_impressum_email(base_url: str) -> Optional[str]:
    """Versucht eine Mail-Adresse aus Impressum/Kontakt-Seite zu holen."""
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    # 1. Direkt die Hauptseite prüfen (manche haben Mail im Footer)
    for url in _candidate_urls(base_url):
        email = _fetch_and_find_email(url)
        if email:
            return email
    return None


def _candidate_urls(base: str) -> list[str]:
    """Erzeugt sinnvolle Kandidaten-URLs in Reihenfolge (Impressum zuerst)."""
    urls = []
    for path in _IMPRESSUM_PATHS:
        urls.append(urljoin(base, path))
    urls.append(base)  # Fallback: Hauptseite
    return urls


def _fetch_and_find_email(url: str) -> Optional[str]:
    try:
        r = requests.get(
            url, timeout=8, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Chanti-LeadTool)"}
        )
        if r.status_code >= 400:
            return None
        # mailto: Links bevorzugen
        mailto_match = re.search(r'mailto:([^"\'\s>]+)', r.text, re.I)
        if mailto_match:
            candidate = mailto_match.group(1).strip()
            if _is_plausible_email(candidate):
                return candidate
        # Sonst regex über Text (ohne HTML-Tags)
        plain = re.sub(r"<[^>]+>", " ", r.text)
        for m in _EMAIL_RE.finditer(plain):
            candidate = m.group(0)
            if _is_plausible_email(candidate):
                return candidate
    except requests.RequestException:
        pass
    return None


def _is_plausible_email(email: str) -> bool:
    """Filtert offensichtlich schlechte Kandidaten (Beispiele, Testadressen)."""
    email = email.lower()
    if len(email) > 100:
        return False
    bad_markers = (
        "example.com", "example.org", "domain.com", "yourdomain",
        "privacy@", "noreply@", "no-reply@", "mailer@",
        "sentry.io", "wixpress.com", "@sentry",
    )
    if any(m in email for m in bad_markers):
        return False
    # keine klaren Image-/JS-Müll-Treffer
    if email.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
        return False
    return True
