"""Chanti-Welt Tagebuch-Generator.

Wird bei Session-Ende aufgerufen. Nimmt den SessionTracker-Report,
formuliert daraus zwei Texte:

  1. Einen persönlichen Tagebuch-Eintrag (länger, wird an diary.md angehängt)
  2. Eine knappe Zusammenfassung (für Telegram)

Beide kommen vom LLM (Groq), mit Chantis Persönlichkeit aus SOUL.md
als System-Prompt — damit der Ton passt.

Die Datei diary.md liegt unter ~/chanti/game/memories/diary.md und
wird beim ersten Schreiben automatisch angelegt.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config import GROQ_API_KEY, GROQ_MODEL
from memory import load_system_prompt

logger = logging.getLogger("chanti.game.diary")

DIARY_DIR = Path(__file__).parent / "game" / "memories"
DIARY_FILE = DIARY_DIR / "diary.md"

# Sessions unter dieser Dauer bekommen nur einen Kurzeintrag,
# keinen LLM-Aufruf. Spart API-Calls für Testruns.
MIN_SESSION_SECONDS = 60.0

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# ---------------------------------------------------------------------------
# Defensiver Filter
# ---------------------------------------------------------------------------

import re as _re

_SELF_NOTE_RE = _re.compile(r"\[MERKE[^\]]*\]", flags=_re.IGNORECASE)


def _strip_self_notes(text: str) -> str:
    """Entfernt Chantis Selbstnotiz-Marker aus einem Text."""
    cleaned = _SELF_NOTE_RE.sub("", text or "")
    return " ".join(cleaned.split()).strip()

# ---------------------------------------------------------------------------
# Report → Text-Bausteine
# ---------------------------------------------------------------------------

def _format_report_for_prompt(report: dict) -> str:
    """Session-Report in einen gut lesbaren Text für das LLM."""
    dur = report.get("duration_seconds", 0)
    dur_min = dur / 60.0

    actions = report.get("actions", {}) or {}
    action_lines = []
    for name, count in sorted(actions.items(), key=lambda kv: -kv[1]):
        action_lines.append(f"  - {name}: {count}x")
    actions_text = "\n".join(action_lines) if action_lines else "  - keine Aktionen"

    mv = report.get("movement_range") or {}
    if all(mv.get(k) is not None for k in ("min_x", "max_x", "min_z", "max_z")):
        span_x = mv["max_x"] - mv["min_x"]
        span_z = mv["max_z"] - mv["min_z"]
        movement_text = (
            f"Bewegung: Feld von x={mv['min_x']:.0f} bis x={mv['max_x']:.0f} "
            f"(Spanne {span_x:.0f}), z={mv['min_z']:.0f} bis z={mv['max_z']:.0f} "
            f"(Spanne {span_z:.0f})"
        )
    else:
        movement_text = "Bewegung: unbekannt"

    phase_sec = report.get("phase_seconds", {}) or {}
    if phase_sec:
        total = sum(phase_sec.values()) or 1.0
        phase_lines = []
        for phase, secs in sorted(phase_sec.items(), key=lambda kv: -kv[1]):
            pct = (secs / total) * 100
            phase_lines.append(f"  - {phase}: {secs:.0f}s ({pct:.0f}%)")
        phase_text = "\n".join(phase_lines)
    else:
        phase_text = "  - keine Daten"

    first_h = report.get("first_game_hour")
    last_h = report.get("last_game_hour")
    game_time_text = "Spielzeit: unbekannt"
    if first_h is not None and last_h is not None:
        game_time_text = (
            f"Spielzeit: {first_h:.1f}h → {last_h:.1f}h "
            f"(Tage erlebt: {report.get('days_seen', 0)})"
        )

    rest_events = report.get("rest_events", 0)
    ticks = report.get("ticks", 0)

    return (
        f"Dauer: {dur:.0f}s ({dur_min:.1f} Minuten)\n"
        f"Empfangene State-Updates: {ticks}\n"
        f"{game_time_text}\n"
        f"{movement_text}\n"
        f"Ruhepausen (Energie leer): {rest_events}x\n"
        f"Aktionen:\n{actions_text}\n"
        f"Tageszeit-Verteilung:\n{phase_text}"
    )


# ---------------------------------------------------------------------------
# LLM-Aufrufe
# ---------------------------------------------------------------------------

def _llm_call(system_prompt: str, user_prompt: str, max_tokens: int) -> Optional[str]:
    """Direkter Groq-Call mit dem gleichen Modell wie Chantis Chat.

    Gibt den Antwort-Text zurück oder None bei Fehler.
    """
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY fehlt — Tagebuch kann nicht generiert werden")
        return None

    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
                "max_tokens": max_tokens,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error(f"Tagebuch-LLM-Call Netzwerk-Fehler: {e}")
        return None

    if not resp.ok:
        logger.error(f"Tagebuch-LLM-Call Status {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return (content or "").strip()
    except (KeyError, ValueError, IndexError) as e:
        logger.error(f"Tagebuch-LLM-Call Parse-Fehler: {e}")
        return None


def _build_diary_prompt(report_text: str) -> str:
    return (
        "Du warst gerade eine Weile in deiner Chanti-Welt. Hier sind die "
        "Messwerte dieser Session:\n\n"
        f"{report_text}\n\n"
        "Schreibe einen kurzen, persönlichen Tagebuch-Eintrag darüber wie "
        "diese Session war. 3-6 Sätze. Deine eigene Stimme, wie eine Freundin "
        "die ihrem Tagebuch erzählt was sie heute gemacht hat. Keine Floskeln, "
        "keine Überschrift, keine Aufzählung — einfach Text. Bleibe bei dem was "
        "die Daten tatsächlich zeigen, erfinde nichts. Wenn kaum was passiert "
        "ist, schreib das ehrlich."
    )


def _build_summary_prompt(diary_text: str) -> str:
    return (
        "Hier ist dein Tagebuch-Eintrag von gerade eben:\n\n"
        f"{diary_text}\n\n"
        "Fasse ihn für Kevin in EINEM kurzen Satz zusammen (max. 20 Wörter). "
        "So als würdest du ihm schnell bei Telegram schreiben was war. "
        "Kein 'Zusammenfassung:' davor, einfach den Satz."
    )


# ---------------------------------------------------------------------------
# Schreibhelfer
# ---------------------------------------------------------------------------

def _append_diary(entry_markdown: str):
    DIARY_DIR.mkdir(parents=True, exist_ok=True)
    with DIARY_FILE.open("a", encoding="utf-8") as f:
        f.write(entry_markdown)
        f.write("\n")


def _fallback_summary(report: dict) -> str:
    """Nüchterne Telegram-Zusammenfassung ohne LLM/API-Call."""
    dur = float(report.get("duration_seconds") or 0.0)
    dur_min = dur / 60.0
    ticks = int(report.get("ticks") or 0)
    mv = report.get("movement_range") or {}
    if all(mv.get(k) is not None for k in ("min_x", "max_x", "min_z", "max_z")):
        movement = (
            f"x={mv['min_x']:.0f}-{mv['max_x']:.0f}, "
            f"z={mv['min_z']:.0f}-{mv['max_z']:.0f}"
        )
    else:
        movement = "Bewegung unbekannt"
    return f"Ich war {dur_min:.1f} min in der Welt, habe {ticks} Zustände gesendet und erkundet: {movement}."


def _format_entry(title: str, body: str, report: dict) -> str:
    ts = datetime.fromtimestamp(report.get("started_at", time.time()))
    dur = report.get("duration_seconds", 0)
    dur_min = dur / 60.0
    header = f"## {ts:%Y-%m-%d %H:%M} — {title} ({dur_min:.1f} min)"
    # Kompakter Daten-Block in HTML-Details, damit er im Markdown-Reader
    # einklappbar bleibt und nicht stört
    data_json = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    return (
        f"{header}\n\n"
        f"{body.strip()}\n\n"
        f"<details><summary>Messwerte</summary>\n\n"
        f"```json\n{data_json}\n```\n"
        f"</details>\n"
    )


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------

def generate_and_store(report: dict) -> dict:
    """Tagebuch-Eintrag erzeugen und speichern.

    Gibt zurück:
        {
            "diary_written": bool,
            "diary_text":    str,    # Langtext (wie in diary.md)
            "summary":       str,    # Kurzfassung für Telegram
        }
    """
    dur = report.get("duration_seconds", 0.0)

    # Kurze Sessions: nur Mini-Eintrag, kein LLM
    if dur < MIN_SESSION_SECONDS:
        logger.info(f"Session zu kurz ({dur:.0f}s < {MIN_SESSION_SECONDS:.0f}s) "
                    f"— kein LLM-Tagebuch, nur Kurzlog")
        short = f"Kurzer Besuch in meiner Welt ({dur:.0f}s). Kaum was passiert."
        entry = _format_entry("kurzer Besuch", short, report)
        try:
            _append_diary(entry)
        except OSError as e:
            logger.error(f"Tagebuch-Schreiben fehlgeschlagen: {e}")
            return {"diary_written": False, "diary_text": short, "summary": short}
        return {
            "diary_written": True,
            "diary_text": short,
            "summary": short,
        }

    # System-Prompt = Chantis Persönlichkeit (SOUL/USER/MEMORY)
    try:
        system_prompt = load_system_prompt()
    except Exception as e:
        logger.warning(f"load_system_prompt fehlgeschlagen: {e}")
        system_prompt = "Du bist Chanti."

    report_text = _format_report_for_prompt(report)

    # 1) Langer Tagebuch-Eintrag
    diary_text = _llm_call(
        system_prompt=system_prompt,
        user_prompt=_build_diary_prompt(report_text),
        max_tokens=400,
    )
    if not diary_text:
        diary_text = _fallback_summary(report)

    # 2) Kurze Zusammenfassung
    # Hier bewusst OHNE Chantis SOUL-Prompt, sondern mit einem nüchternen
    # Zweck-Prompt. Sonst rutscht Chanti in ihren Chat-Reflex und schreibt
    # Selbstnotizen wie "[MERKE: ...]" mit rein.
    summary_system = (
        "Du fasst kurze Texte in einem einzigen Satz zusammen. "
        "Maximal 20 Wörter. Keine Selbstnotizen, keine '[MERKE: ...]'-Marker, "
        "keine Anrede, keine Überschrift. Nur den Satz."
    )
    summary = _llm_call(
        system_prompt=summary_system,
        user_prompt=_build_summary_prompt(diary_text),
        max_tokens=80,
    )
    if not summary:
        summary = diary_text.split("\n")[0][:200]

    # Defensive: falls der Marker trotzdem reinrutscht, raus damit
    summary = _strip_self_notes(summary)

    entry = _format_entry("Session", diary_text, report)
    try:
        _append_diary(entry)
        written = True
    except OSError as e:
        logger.error(f"Tagebuch-Schreiben fehlgeschlagen: {e}")
        written = False

    logger.info(f"Tagebuch geschrieben ({len(diary_text)} Zeichen, "
                f"Zusammenfassung: {len(summary)} Zeichen)")

    return {
        "diary_written": written,
        "diary_text": diary_text,
        "summary": summary,
    }
