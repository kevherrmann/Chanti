"""E-Mail-Generator für Kaltakquise via Groq.

Erzeugt personalisierten Text basierend auf Firma + Scoring-Daten.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests

logger = logging.getLogger("chanti")


STIL_ANWEISUNGEN = {
    "formell": "Sehr geehrte Damen und Herren als Anrede. Sachlich, klar, höflich. Du-Form nicht.",
    "locker":  "Hallo-Anrede mit Firmenname. Freundlich, nahbar, aber professionell. Keine Umgangssprache.",
    "kurz":    "Maximal 6 Sätze gesamt. Sachliche Anrede. Auf den Punkt, kein Small Talk.",
}


def generate(company: dict, website_analysis: Optional[dict],
             reputation: Optional[dict], stil: str = "formell",
             sender_name: str = "Kevin") -> tuple[str, str]:
    """Gibt (subject, body) zurück. Fällt auf Template-Text zurück wenn Groq down."""
    stil = stil if stil in STIL_ANWEISUNGEN else "formell"
    context = _build_context(company, website_analysis, reputation)
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    groq_model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    if groq_key:
        try:
            subject, body = _via_groq(context, stil, sender_name, groq_key, groq_model)
            if subject and body:
                return subject, body
        except Exception as e:
            logger.warning(f"Groq-Draft fehlgeschlagen, nutze Template: {e}")

    return _via_template(context, stil, sender_name)


def _build_context(company: dict, wa: Optional[dict], rep: Optional[dict]) -> dict:
    problems = []
    if wa:
        import json
        raw = wa.get("problems_json")
        if raw:
            try:
                problems = json.loads(raw)
            except (ValueError, TypeError):
                problems = []
        if not wa.get("reachable"):
            problems = problems or ["Website nicht erreichbar"]

    social = []
    if rep:
        if rep.get("social_facebook"):  social.append("Facebook")
        if rep.get("social_instagram"): social.append("Instagram")
        if rep.get("social_linkedin"):  social.append("LinkedIn")

    return {
        "firma":       company.get("name", "").strip(),
        "stadt":       company.get("city") or "",
        "website":     company.get("website_url") or "",
        "problems":    problems,
        "rating":      (rep or {}).get("rating"),
        "reviews":     (rep or {}).get("review_count"),
        "social":      social,
        "domain_age":  (rep or {}).get("domain_age_years"),
        "platform":    (wa or {}).get("platform_detected"),
    }


def _via_groq(ctx: dict, stil: str, sender_name: str,
              api_key: str, model: str) -> tuple[str, str]:
    system = (
        "Du bist ein Webentwickler und schreibst Kaltakquise-E-Mails. "
        "Du schreibst kurz, konkret, auf Deutsch, ohne Marketing-Blabla. "
        "Du beziehst dich auf echte Schwächen der Zielseite. "
        "Du verwendest kein Emoji. Du versprichst nichts konkretes an Ergebnissen. "
        "Dein Ziel ist ein kurzes Gespräch, kein Verkauf in der Mail."
    )

    probs = ", ".join(ctx["problems"]) if ctx["problems"] else "keine konkreten Schwächen identifiziert"
    rating_info = ""
    if ctx["rating"] and ctx["reviews"]:
        rating_info = f"Google-Bewertung: {ctx['rating']}/5 bei {ctx['reviews']} Reviews."

    user = f"""Firma: {ctx['firma']}
Ort: {ctx['stadt']}
Website: {ctx['website'] or 'keine'}
Website-Schwächen: {probs}
{rating_info}
Stil: {stil} ({STIL_ANWEISUNGEN[stil]})

Schreibe eine E-Mail. Absender-Name: {sender_name}.
WICHTIG: Beginne mit einer Zeile `SUBJECT: <Betreff>` und dann einer Leerzeile,
dann der eigentliche Mail-Text. Kein anderes Format. Keine Platzhalter wie [Name].
Keine Signatur mit Kontaktdaten, nur der Name am Ende."""

    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
            "max_tokens": 600,
        },
        timeout=30,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"].strip()
    return _split_subject_body(content)


def _split_subject_body(raw: str) -> tuple[str, str]:
    lines = raw.split("\n")
    subject = ""
    body_start = 0
    for i, line in enumerate(lines):
        m = re.match(r"^\s*subject\s*:\s*(.+)$", line, re.I)
        if m:
            subject = m.group(1).strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    # Leerzeilen am Anfang entfernen
    body = re.sub(r"^\n+", "", body)
    if not subject:
        # Fallback: erste Zeile als Subject
        subject = (lines[0].strip() or "Anfrage")[:100]
        body = "\n".join(lines[1:]).strip() or raw
    return subject[:150], body


def _via_template(ctx: dict, stil: str, sender_name: str) -> tuple[str, str]:
    """Offline-Template wenn Groq nicht verfügbar."""
    firma = ctx["firma"]
    problems = ctx["problems"]
    problem_line = ""
    if problems:
        problem_line = "Mir ist aufgefallen, dass " + problems[0].lower() + "."

    if stil == "kurz":
        subject = f"Ihre Website, {firma}"
        body = (f"Guten Tag,\n\n"
                f"ich bin Webentwickler aus Ibbenbüren. {problem_line}\n\n"
                f"Falls Interesse an einem kurzen Austausch besteht, "
                f"melden Sie sich gerne zurück.\n\n"
                f"Viele Grüße\n{sender_name}")
    elif stil == "locker":
        subject = f"Hallo {firma} – kurze Idee zu Ihrer Website"
        body = (f"Hallo {firma}-Team,\n\n"
                f"ich bin über Ihre Website gestolpert. {problem_line}\n\n"
                f"Als freier Webentwickler aus Ibbenbüren überlege ich mir gerne mit Ihnen "
                f"zusammen, wie man das in den Griff bekommt. Unverbindlich und ohne Druck.\n\n"
                f"Melden Sie sich, wenn's interessant klingt.\n\n"
                f"Beste Grüße\n{sender_name}")
    else:  # formell
        subject = f"Ihre Website: kurzer Hinweis"
        body = (f"Sehr geehrte Damen und Herren,\n\n"
                f"ich bin als freier Webentwickler aus Ibbenbüren auf Ihre Website gestoßen. "
                f"{problem_line}\n\n"
                f"Falls Sie Interesse an einer kurzen, unverbindlichen Einschätzung haben, "
                f"antworten Sie gerne auf diese E-Mail. Ich melde mich dann mit konkreten Punkten.\n\n"
                f"Mit freundlichen Grüßen\n{sender_name}")

    return subject, body
