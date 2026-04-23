"""Website-Analyse via Playwright: Check, Platform-Detection, Screenshot.

Bei fehlendem Playwright: Fallback auf requests (weniger Details, kein Screenshot)."""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("chanti")

SCREENSHOT_DIR = Path(os.environ.get(
    "CHANTI_SCREENSHOT_DIR",
    str(Path.home() / "chanti" / "data" / "screenshots"),
))

# Fingerabdrücke für Baukasten-Plattformen
_PLATFORM_FINGERPRINTS = [
    ("wix",        [r"wixstatic\.com", r"wix\.com", r"_wix_"]),
    ("jimdo",      [r"jimdo\.com", r"jimdofree\.com", r"jimcdn\.com"]),
    ("squarespace",[r"squarespace\.com", r"static1\.squarespace"]),
    ("weebly",     [r"weebly\.com", r"weeblycloud"]),
    ("webnode",    [r"webnode\.", r"wbn\."]),
    ("ionos",      [r"ionos\.de/homepage", r"mywebsite-editor"]),
    ("wordpress",  [r"wp-content/", r"wp-includes/", r"/wp-json/"]),
    ("shopify",    [r"cdn\.shopify\.com", r"shopifycloud"]),
    ("typo3",      [r"/typo3/", r"typo3conf/"]),
    ("joomla",     [r"/components/com_", r"joomla"]),
    ("elementor",  [r"elementor-frontend", r"elementor/assets"]),
]

_UNDER_CONSTRUCTION_MARKERS = [
    "under construction", "im aufbau", "baustelle", "coming soon",
    "demnächst verfügbar", "website in arbeit", "diese seite wird",
    "we are working", "wir arbeiten", "page not yet",
]


def _detect_platform(html: str) -> Optional[str]:
    html_low = html.lower()
    for platform, patterns in _PLATFORM_FINGERPRINTS:
        for p in patterns:
            if re.search(p, html_low):
                return platform
    return None


def _is_under_construction(text: str, word_count: int) -> bool:
    if word_count < 30:
        return True
    low = text.lower()
    return any(m in low for m in _UNDER_CONSTRUCTION_MARKERS)


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    # Kein Schema? Dann http:// voranstellen — NICHT https.
    # Wenn die Seite https unterstützt, leitet der Server selbst um und wir
    # sehen das an der finalen URL. Wenn wir blind https erzwingen, täuschen
    # wir uns selbst: Seiten ohne HTTPS werfen dann einfach Connection-Errors,
    # obwohl sie unter http erreichbar wären — und Seiten MIT HTTPS werden
    # als "has_ssl" markiert ohne dass wir's verifiziert haben.
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _screenshot_path_for(company_id: int) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR / f"company_{company_id}.png"


def analyze(url: str, company_id: int, take_screenshot: bool = True) -> dict:
    """Haupt-Einstieg. Gibt strukturiertes Analyse-Dict zurück."""
    if not url:
        return _empty_result("Keine URL")
    url = _normalize_url(url)

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # noqa: F401
        return _analyze_playwright(url, company_id, take_screenshot)
    except ImportError:
        logger.warning("Playwright nicht installiert, Fallback auf requests")
        return _analyze_requests(url)


def _empty_result(reason: str) -> dict:
    return {
        "reachable": False,
        "http_status": None,
        "title": "",
        "word_count": 0,
        "has_viewport": False,
        "has_ssl": False,
        "has_contact": False,
        "has_nav": False,
        "has_images": False,
        "platform_detected": None,
        "under_construction": False,
        "problems": [reason],
        "raw_checks": {},
        "screenshot_path": None,
    }


def _analyze_playwright(url: str, company_id: int, take_screenshot: bool) -> dict:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    screenshot_path: Optional[str] = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                try:
                    response = page.goto(url, timeout=15000, wait_until="domcontentloaded")
                except PWTimeout:
                    return {**_empty_result("Timeout beim Laden"), "reachable": False}
                except Exception as e:
                    return {**_empty_result(f"Ladefehler: {type(e).__name__}"), "reachable": False}

                http_status = response.status if response else 0
                if http_status >= 400:
                    return {**_empty_result(f"HTTP {http_status}"), "http_status": http_status}

                # kurz warten damit spät geladene Inhalte da sind
                try:
                    page.wait_for_load_state("networkidle", timeout=3500)
                except PWTimeout:
                    pass

                # has_ssl an der FINALEN URL nach Redirects prüfen, nicht an der
                # eingegebenen. Sonst wird 'http://example.com' → Auto-Redirect
                # auf https fälschlich als "kein HTTPS" gemeldet.
                final_url = page.url or url
                has_ssl = final_url.startswith("https://")

                title = (page.title() or "").strip()
                html = page.content()
                text = page.evaluate("""() => {
                    document.querySelectorAll('script, style, noscript').forEach(e => e.remove());
                    return document.body ? document.body.innerText : '';
                }""") or ""
                word_count = len(text.split())

                has_viewport = bool(page.evaluate(
                    "() => !!document.querySelector('meta[name=\"viewport\"]')"))
                has_contact = any(k in text.lower() for k in
                                  ("kontakt", "telefon", "impressum", "contact", "e-mail", "email"))
                has_images = bool(page.evaluate(
                    "() => Array.from(document.querySelectorAll('img'))"
                    ".some(i => i.naturalWidth > 50)"))
                has_nav = bool(page.evaluate(
                    "() => !!document.querySelector('nav, [role=\"navigation\"], header')"))

                platform = _detect_platform(html)
                under_construction = _is_under_construction(text, word_count)

                # Screenshot
                if take_screenshot:
                    try:
                        path = _screenshot_path_for(company_id)
                        page.screenshot(path=str(path), full_page=False,
                                        type="png", timeout=10000)
                        screenshot_path = str(path)
                    except Exception as e:
                        logger.warning(f"Screenshot fehlgeschlagen: {e}")

                problems = _derive_problems(
                    word_count, has_viewport, has_ssl, has_contact, has_nav, has_images,
                    under_construction, platform,
                )

                return {
                    "reachable": True,
                    "http_status": http_status,
                    "title": title,
                    "word_count": word_count,
                    "has_viewport": has_viewport,
                    "has_ssl": has_ssl,
                    "has_contact": has_contact,
                    "has_nav": has_nav,
                    "has_images": has_images,
                    "platform_detected": platform,
                    "under_construction": under_construction,
                    "problems": problems,
                    "raw_checks": {
                        "url": url,
                        "text_sample": text[:400],
                    },
                    "screenshot_path": screenshot_path,
                }
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Playwright-Fehler: {e}")
        return _empty_result(f"Browser-Fehler: {type(e).__name__}")


def _analyze_requests(url: str) -> dict:
    """Fallback ohne Browser. Weniger aussagekräftig, kein Screenshot."""
    try:
        r = requests.get(
            url, timeout=10, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Chanti-LeadTool)"}
        )
    except requests.RequestException as e:
        return {**_empty_result(f"Nicht erreichbar: {type(e).__name__}"),
                "reachable": False}

    html = r.text or ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    word_count = len(text.split())

    has_viewport = bool(re.search(r"<meta[^>]+name=['\"]viewport['\"]", html, re.I))
    has_contact = any(k in text.lower() for k in
                      ("kontakt", "telefon", "impressum", "contact", "e-mail", "email"))
    has_images = bool(re.search(r"<img\s", html, re.I))
    has_nav = bool(re.search(r"<(nav|header)\b", html, re.I))
    has_ssl = r.url.startswith("https://")
    platform = _detect_platform(html)
    under_construction = _is_under_construction(text, word_count)
    title_match = re.search(r"<title[^>]*>([^<]{0,200})</title>", html, re.I)
    title = title_match.group(1).strip() if title_match else ""

    problems = _derive_problems(
        word_count, has_viewport, has_ssl, has_contact, has_nav, has_images,
        under_construction, platform,
    )

    return {
        "reachable": r.status_code < 400,
        "http_status": r.status_code,
        "title": title,
        "word_count": word_count,
        "has_viewport": has_viewport,
        "has_ssl": has_ssl,
        "has_contact": has_contact,
        "has_nav": has_nav,
        "has_images": has_images,
        "platform_detected": platform,
        "under_construction": under_construction,
        "problems": problems,
        "raw_checks": {"url": r.url, "fallback": "requests"},
        "screenshot_path": None,
    }


def _derive_problems(word_count: int, has_viewport: bool, has_ssl: bool,
                     has_contact: bool, has_nav: bool, has_images: bool,
                     under_construction: bool, platform: Optional[str]) -> list[str]:
    problems: list[str] = []
    if under_construction:
        problems.append("Seite wirkt im Aufbau / Platzhalter")
    if word_count < 80:
        problems.append("kaum Textinhalt")
    if not has_viewport:
        problems.append("nicht mobil-optimiert")
    if not has_ssl:
        problems.append("kein HTTPS")
    if not has_contact:
        problems.append("keine Kontaktinformationen erkennbar")
    if not has_nav:
        problems.append("keine Navigation erkannt")
    if not has_images:
        problems.append("keine Bilder")
    if platform in ("wix", "jimdo", "webnode", "ionos"):
        problems.append(f"Baukasten-Plattform ({platform})")
    return problems


def cleanup_old_screenshots(max_age_days: int = 30) -> int:
    """Löscht Screenshots die älter als max_age_days sind."""
    if not SCREENSHOT_DIR.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for f in SCREENSHOT_DIR.glob("company_*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"Screenshot-Cleanup: {removed} Dateien entfernt")
    return removed
