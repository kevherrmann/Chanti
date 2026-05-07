"""KI-News-Check für den Tages-Puls.

Workflow:
1. Websuche nach KI-News der letzten 7 Tage (DDGS direkt, mit timelimit)
2. Domain-Filter: Help-Sites raus, News-Sites bevorzugt
3. Stack-/Broad-/Robotik-Relevanz-Check
4. Kurz-Briefing-Format
5. Gibt Telegram-fertigen Text zurück oder None wenn nichts zu melden

Bewusst konservativ: wenn Zweifel → nichts senden. Ein tägliches Schweigen
ist besser als ein tägliches Rauschen.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("chanti")

# ─── Keywords ─────────────────────────────────────────────────────────────

# Was Chanti technisch nutzt. Wenn's hier Updates/Änderungen gibt, ist das
# für Kevin direkt interessant.
STACK_KEYWORDS = (
    "groq", "llama", "meta-llama",
    "sentence-transformers", "minilm",
    "playwright", "whisper", "faster-whisper",
    "vosk", "xtts", "sqlite-vec",
    "fastapi", "n8n",
)

# Broad: die großen Player in der LLM-Welt + Robotik.
BROAD_KEYWORDS = (
    # LLM-Player
    "anthropic", "openai", "gpt-", "claude", "gemini",
    "deepseek", "mistral", "qwen",
    "open source llm", "local llm", "open-source model",
    # Robotik
    "humanoid", "robot", "boston dynamics", "figure ai",
    "tesla bot", "optimus", "unitree", "agility robotics",
    "embodied ai", "robotics",
)

# Rauschen-Filter. Artikel-Titel die diese Begriffe enthalten sind fast nie
# technische News, eher Marketing/Stock/Hype/SEO.
NOISE_KEYWORDS = (
    "stock", "aktie", "börs", "investment", "ipo",
    "course", "tutorial", "guide to", "how to use",
    "top 10", "best ai", "awesome",
    "gpt wrapper", "earn money", "make money",
)

# ─── Domain-Filter ────────────────────────────────────────────────────────

# Domains die NIE News liefern. Hard-block.
BLOCKED_DOMAINS = (
    "stackoverflow.com",
    "stackexchange.com",
    "github.com",          # meist Issues/Code, keine News
    "gitlab.com",
    "reddit.com",          # zu viel Rauschen
    "quora.com",
    "youtube.com",         # Video, kein lesbares Briefing
    "medium.com",          # zu viel Hype/Tutorial
    "dev.to",
    "hashnode.com",
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    # Nicht-englischsprachige Plattformen (zu viel Rauschen für uns)
    "zhihu.com",
    "weibo.com",
    "baidu.com",
    "csdn.net",
    "jianshu.com",
)

# Bevorzugte News-Domains. Hits hier = +5 Punkte Bonus.
NEWS_DOMAINS = (
    "techcrunch.com",
    "theverge.com",
    "arstechnica.com",
    "venturebeat.com",
    "wired.com",
    "technologyreview.com",
    "ieee.org",
    "spectrum.ieee.org",
    "thenextweb.com",
    "engadget.com",
    "heise.de",
    "golem.de",
    "t3n.de",
    "anthropic.com",       # Anbieter-Blogs sind direkte Quellen
    "openai.com",
    "ai.meta.com",
    "huggingface.co",
    "deepmind.google",
)

# ─── Limits ───────────────────────────────────────────────────────────────

MAX_CANDIDATES = 20
MAX_FINAL_PICKS = 5

# DDGS Such-Konfiguration
SEARCH_TIMELIMIT = "w"   # "d"=Tag, "w"=Woche, "m"=Monat
SEARCH_REGION = "us-en"  # weltweit, nicht "de-de" (mehr Treffer)
SEARCH_MAX_RESULTS = 8   # pro Query


def build_briefing() -> Optional[str]:
    """Orchestriert den News-Check. Gibt Telegram-Text zurück oder None."""
    try:
        candidates = _fetch_candidates()
    except Exception as e:
        logger.warning(f"pulse_news: Fetch fehlgeschlagen: {type(e).__name__}: {e}")
        return None

    if not candidates:
        logger.info("pulse_news: keine Kandidaten gefunden")
        return None

    relevant = _filter_relevant(candidates)
    if not relevant:
        logger.info(
            f"pulse_news: {len(candidates)} Kandidaten, "
            f"aber nichts Relevantes nach Filterung"
        )
        return None

    picks = relevant[:MAX_FINAL_PICKS]
    try:
        text = _format_briefing(picks)
    except Exception as e:
        logger.warning(f"pulse_news: Format fehlgeschlagen: {e}")
        return None

    return text


def _get_ddgs_class():
    """Lädt DDGS aus ddgs (neu) oder duckduckgo_search (alt)."""
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        return DDGS


def _fetch_candidates() -> list[dict]:
    """Holt News-Kandidaten direkt via DDGS, nicht über web_search-Skill.

    So können wir timelimit, region und max_results selbst kontrollieren.
    """
    try:
        DDGS = _get_ddgs_class()
    except ImportError as e:
        logger.error(f"pulse_news: DDGS-Lib nicht installiert: {e}")
        return []

    # Spezifische Such-Anfragen die News liefern, nicht Stack-Overflow
    queries = [
        # Aktuelle LLM-Releases / Announcements
        "new LLM model release announcement",
        # Lokale Modelle
        "open source local LLM new model",
        # Robotik
        "humanoid robot announcement",
        # Anthropic/OpenAI/Meta direkt
        "anthropic OR openai OR meta AI announcement",
    ]

    import time
    all_results: list[dict] = []
    for i, q in enumerate(queries):
        # Kurz warten zwischen Queries — DDGS rate-limit
        if i > 0:
            time.sleep(2)
        try:
            try:
                ctx = DDGS(timeout=15)
            except TypeError:
                ctx = DDGS()

            with ctx as ddgs:
                # timelimit="w" = letzte Woche
                raw = ddgs.text(
                    q,
                    max_results=SEARCH_MAX_RESULTS,
                    region=SEARCH_REGION,
                    timelimit=SEARCH_TIMELIMIT,
                )
                results = list(raw) if raw else []
                for r in results:
                    if isinstance(r, dict):
                        all_results.append(r)
        except Exception as e:
            logger.warning(
                f"pulse_news: Suche '{q}' fehlgeschlagen: "
                f"{type(e).__name__}: {e}"
            )

    # Duplikate via URL entfernen
    seen = set()
    unique: list[dict] = []
    for r in all_results:
        url = (r.get("href") or r.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(r)

    return unique[:MAX_CANDIDATES]


def _domain_of(url: str) -> str:
    """Extrahiert die Domain aus einer URL."""
    if not url:
        return ""
    url = url.lower()
    # https://www.example.com/path → example.com
    if "://" in url:
        url = url.split("://", 1)[1]
    if "/" in url:
        url = url.split("/", 1)[0]
    # www. weg
    if url.startswith("www."):
        url = url[4:]
    return url


def _filter_relevant(candidates: list[dict]) -> list[dict]:
    """Scort nach Stack-/Broad-/Domain-Bezug, filtert Noise + Bad-Domains.

    - Stack-Hit:    +10 Punkte (sehr relevant)
    - Broad-Hit:    +3 Punkte
    - News-Domain:  +5 Punkte (bevorzugte Quelle)
    - Noise-Hit:    -5 Punkte
    - Blocked-Domain: komplett raus

    Cutoff: ab 5 Punkten relevant.
    """
    scored: list[tuple[int, dict]] = []
    for c in candidates:
        url = (c.get("href") or c.get("url") or "").strip()
        domain = _domain_of(url)

        # Hard-Block für bestimmte Domains
        if any(bad in domain for bad in BLOCKED_DOMAINS):
            continue

        text = (c.get("title", "") + " " + c.get("body", "")).lower()
        score = 0
        hit_kw = []

        for kw in STACK_KEYWORDS:
            if kw in text:
                score += 10
                hit_kw.append(kw)
        for kw in BROAD_KEYWORDS:
            if kw in text:
                score += 3
                hit_kw.append(kw)
        for kw in NOISE_KEYWORDS:
            if kw in text:
                score -= 5

        # Bonus für News-Domains
        if any(news in domain for news in NEWS_DOMAINS):
            score += 5
            hit_kw.append(f"@{domain}")

        if score >= 5:
            c = dict(c)
            c["_score"] = score
            c["_matched"] = hit_kw[:4]
            c["_domain"] = domain
            scored.append((score, c))

    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored]


def _format_briefing(picks: list[dict]) -> str:
    """Baut die Telegram-Nachricht. Bewusst kurz, kein Markdown."""
    date_str = datetime.now().strftime("%d.%m.%Y")
    lines = [f"KI-News-Briefing ({date_str})", ""]
    for p in picks:
        title = p.get("title", "").strip()
        body = p.get("body", "").strip()
        href = (p.get("href") or p.get("url") or "").strip()
        # Body auf ~140 Zeichen kürzen
        if len(body) > 140:
            body = body[:140].rsplit(" ", 1)[0] + "…"
        lines.append(f"• {title}")
        if body:
            lines.append(f"  {body}")
        if href:
            lines.append(f"  {href}")
        lines.append("")

    lines.append("Frag mich wenn du mehr dazu wissen willst.")
    return "\n".join(lines).strip()
