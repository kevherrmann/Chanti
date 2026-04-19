"""Skill: Lead-Statistiken und Top-Leads per Sprache abfragen.

Nur lesend. Alle Mutationen (Suche, Analyse, Mail, Versand) laufen über das UI –
per Sprache wäre das zu fehleranfällig.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import leads_db  # noqa: E402

_VALID_STATUS = {"all", "new", "analyzed", "qualified", "researched",
                 "drafted", "sent", "failed", "rejected"}


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "leads_status",
        "description": (
            "Gibt einen Überblick über Kevins Lead-Pipeline: wie viele Firmen in welchem "
            "Status sind, und optional die Top-Leads nach Score. Nur lesend. "
            "Anlegen, analysieren, Mails schreiben und versenden macht Kevin über das "
            "Lead-UI im Browser (/leads)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Wie viele Top-Leads (nach Score) zusätzlich zur Stat aufgelistet werden sollen. 0 = nur Stats. Default: 5.",
                    "minimum": 0,
                    "maximum": 20,
                },
                "status": {
                    "type": "string",
                    "description": "Filter für die Top-Liste: 'all', 'new', 'analyzed', 'researched', 'drafted', 'sent'. Default: 'all'.",
                },
            },
            "required": [],
        },
    },
}


def execute(top_n: int = 5, status: str = "all") -> str:
    try:
        top_n = max(0, min(int(top_n), 20))
    except (ValueError, TypeError):
        top_n = 5

    status = (status or "all").strip().lower()
    if status not in _VALID_STATUS:
        return (f"Unbekannter Status '{status}'. "
                f"Erlaubt: {', '.join(sorted(_VALID_STATUS))}.")

    counts = leads_db.count_by_status()
    lines = [
        f"Pipeline-Stand:",
        f"- Alle: {counts.get('all', 0)}",
    ]
    for s in ("new", "analyzed", "researched", "drafted", "sent", "failed"):
        n = counts.get(s, 0)
        if n:
            lines.append(f"- {s}: {n}")

    if top_n > 0:
        top = leads_db.list_companies(status=status, limit=top_n)
        if top:
            lines.append("")
            lines.append(f"Top-{top_n} nach Score:")
            for c in top:
                score = c.get("total_score")
                score_str = f"{score:.0f}" if score is not None else "–"
                lines.append(f"- [{score_str}] {c['name']} ({c.get('city') or '?'}) · {c.get('status')}")

    return "\n".join(lines)
