"""KI-News-Check für den Tages-Puls.

Workflow:
1. Websuche nach KI-News der letzten 24-48h
2. Stack-Relevanz-Check: interessiert Kevin das überhaupt?
3. Kurz-Briefing via LLM (nur wenn wirklich was dran ist)
4. Gibt Telegram-fertigen Text zurück oder None wenn nichts zu melden

Bewusst konservativ: wenn Zweifel → nichts senden. Ein tägliches Schweigen
ist besser als ein tägliches Rauschen.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("chanti")

# Was Chanti technisch nutzt. Wenn's hier Updates/Änderungen gibt, ist das
# für Kevin direkt interessant. Liste bewusst kurz — sonst Rauschen.
STACK_KEYWORDS = (
    "groq", "llama", "meta-llama",
    "sentence-transformers", "minilm",
    "playwright", "whisper", "faster-whisper",
    "vosk", "xtts", "sqlite-vec",
    "fastapi", "n8n",
)

# Broad: die großen Player in der LLM-Welt. Für "allgemein wichtige" News.
BROAD_KEYWORDS = (
    "anthropic", "openai", "gpt-", "claude", "gemini",
    "deepseek", "mistral", "qwen",
    "open source llm", "local llm",
)

# Rauschen-Filter. Artikel-Titel die diese Begriffe enthalten sind fast nie
# technische News, eher Marketing/Stock/Hype.
NOISE_KEYWORDS = (
    "stock", "aktie", "börs", "investment", "ipo",
    "course", "tutorial", "guide to",
    "top 10", "best ai", "awesome",
    "gpt wrapper",
)

MAX_CANDIDATES = 12
MAX_FINAL_PICKS = 4


def build_briefing() -> Optional[str]:
    """Orchestriert den News-Check. Gibt Telegram-Text zurück oder None.

    Wenn irgendwas schiefgeht (Netzwerk, keine Relevanz, etc.) → None.
    Nie eine halb-kaputte Nachricht senden.
    """
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
        logger.info(f"pulse_news: {len(candidates)} Kandidaten, aber nichts Relevantes für Stack")
        return None

    # Top-Picks für's Briefing
    picks = relevant[:MAX_FINAL_PICKS]
    try:
        text = _format_briefing(picks)
    except Exception as e:
        logger.warning(f"pulse_news: Format fehlgeschlagen: {e}")
        return None

    return text


def _fetch_candidates() -> list[dict]:
    """Holt News-Kandidaten über web_search. Gibt Liste mit dict(title, body, href)."""
    # Imports lazy, damit pulse_news auch ohne web_search-Skill ladbar bleibt.
    try:
        import importlib.util
        from pathlib import Path as _P
        # web_search ist ein Skill — wir laden das Modul direkt
        skill_path = _P(__file__).parent / "skills" / "web_search.py"
        if not skill_path.exists():
            logger.warning("pulse_news: skills/web_search.py nicht gefunden")
            return []
        spec = importlib.util.spec_from_file_location("_pulse_web_search", skill_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        logger.warning(f"pulse_news: web_search-Skill nicht ladbar: {e}")
        return []

    # Zwei Suchen — eine stack-spezifisch, eine allgemein.
    # Wir holen parallel nicht — DDGS rate-limitet. Lieber nacheinander.
    queries = [
        "Groq Llama new model release",
        "AI news today",
    ]

    all_results: list[dict] = []
    for q in queries:
        try:
            raw_text = mod.execute(query=q)
            all_results.extend(_parse_search_output(raw_text))
        except Exception as e:
            logger.warning(f"pulse_news: Suche '{q}' fehlgeschlagen: {e}")

    # Duplikate via URL entfernen
    seen = set()
    unique: list[dict] = []
    for r in all_results:
        url = r.get("href", "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(r)

    return unique[:MAX_CANDIDATES]


def _parse_search_output(text: str) -> list[dict]:
    """Parst das Output-Format von web_search.execute zurück in dicts.
    web_search gibt Zeilen zurück: '- Titel: Body' und '  URL: ...'
    """
    results: list[dict] = []
    current: dict = {}
    for line in text.split("\n"):
        line = line.rstrip()
        if line.startswith("- "):
            if current:
                results.append(current)
            body = line[2:]
            # Titel und Body sind durch ': ' getrennt — aber Titel kann auch ':' enthalten.
            # Heuristik: erstes ': ' nach mindestens 3 Zeichen Titel.
            idx = body.find(": ")
            if idx >= 3:
                current = {"title": body[:idx].strip(), "body": body[idx + 2:].strip()}
            else:
                current = {"title": body.strip(), "body": ""}
        elif line.strip().startswith("URL: "):
            current["href"] = line.strip()[5:].strip()
        # andere Zeilen (Header) ignorieren
    if current:
        results.append(current)
    return results


def _filter_relevant(candidates: list[dict]) -> list[dict]:
    """Scort nach Stack-Bezug + Broad-Bezug, filtert Noise raus.

    Stack-Hit = 10 Punkte (sehr relevant)
    Broad-Hit = 3 Punkte (allgemein interessant)
    Noise-Hit = -5 Punkte (Abstrafung)

    Cutoff: ab 5 Punkten wird's als 'relevant' markiert.
    """
    scored: list[tuple[int, dict]] = []
    for c in candidates:
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
        if score >= 5:
            c = dict(c)  # Kopie
            c["_score"] = score
            c["_matched"] = hit_kw[:3]
            scored.append((score, c))

    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored]


def _format_briefing(picks: list[dict]) -> str:
    """Baut die Telegram-Nachricht. Bewusst kurz, keine Markdown-Formatierung
    (Kevin hatte Telegram-Parse-Probleme mit Markdown-Entities)."""
    date_str = datetime.now().strftime("%d.%m.%Y")
    lines = [f"KI-News-Briefing ({date_str})", ""]
    for p in picks:
        title = p.get("title", "").strip()
        body = p.get("body", "").strip()
        href = p.get("href", "").strip()
        # Body auf ~120 Zeichen kürzen
        if len(body) > 120:
            body = body[:120].rsplit(" ", 1)[0] + "…"
        lines.append(f"• {title}")
        if body:
            lines.append(f"  {body}")
        if href:
            lines.append(f"  {href}")
        lines.append("")

    lines.append("Frag mich wenn du mehr dazu wissen willst.")
    return "\n".join(lines).strip()
