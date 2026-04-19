"""Skill: Webseite aufrufen und Inhalt lesen via Playwright"""
import ipaddress
import socket
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

# Private/Reserved IP-Bereiche — verhindert SSRF auf lokale Services
# (Home Assistant, n8n, Router, Metadata-Endpoints, etc.).
_BLOCKED_HOSTNAMES = {"localhost", "ip6-localhost", "ip6-loopback"}

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_browse",
        "description": "Ruft eine Webseite auf und liest ihren Textinhalt. Nutze dies um Seiten zu analysieren, Informationen zu finden oder Links zu entdecken. Nur öffentliche http(s)-URLs.",
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


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _validate_url(url: str) -> tuple[bool, str]:
    """Prüft ob URL öffentlich und http(s) ist. Gibt (ok, message) zurück."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "Ungültige URL."

    if parsed.scheme not in ("http", "https"):
        return False, f"Nur http/https erlaubt, nicht {parsed.scheme!r}."

    host = (parsed.hostname or "").lower()
    if not host:
        return False, "URL enthält keinen Host."

    if host in _BLOCKED_HOSTNAMES:
        return False, "Zugriff auf lokale Hosts nicht erlaubt."

    # Wenn host schon eine IP ist: direkt prüfen.
    try:
        ipaddress.ip_address(host)
        if _is_private_ip(host):
            return False, "Zugriff auf private/lokale IP-Adressen nicht erlaubt."
        return True, ""
    except ValueError:
        pass

    # Hostname auflösen und alle A/AAAA-Records prüfen.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return False, f"DNS-Fehler: {e}"
    for info in infos:
        ip = info[4][0]
        if _is_private_ip(ip):
            return False, f"Host {host} zeigt auf private IP {ip}."
    return True, ""


def execute(url: str) -> str:
    ok, msg = _validate_url(url)
    if not ok:
        return f"URL abgelehnt: {msg}"

    browser = None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=_UA)
                page = context.new_page()
                # Gesamt-Timeout greift auch für evaluate().
                page.set_default_timeout(20000)
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                text = page.evaluate("""() => {
                    document.querySelectorAll(
                        'script, style, nav, footer, header, aside, noscript, iframe'
                    ).forEach(e => e.remove());
                    return document.body ? document.body.innerText : '';
                }""")
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

        text = " ".join((text or "").split())
        if not text.strip():
            return "Seite konnte nicht gelesen werden (leerer Textinhalt)."
        if len(text) > 3000:
            text = text[:3000] + "... [gekürzt]"
        return text
    except Exception as e:
        # Kurze, nicht-leckende Fehlermeldung. Stacktrace landet im Logger des Servers.
        return f"Fehler beim Aufrufen von {url}: {type(e).__name__}: {e}"
