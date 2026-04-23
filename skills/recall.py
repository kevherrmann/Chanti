"""Skill: Semantische Suche über alte Gespräche.

Chanti nutzt das wenn Kevin auf Vergangenes anspielt ('damals', 'neulich',
'das Ding von letztem Monat') oder wenn in USER.md/MEMORY.md nichts
zum Thema steht aber es klingt als hätten sie schon mal darüber geredet.

Technisch dünner Wrapper — die Arbeit macht recall_index.search.
"""
from __future__ import annotations

import logging

import recall_index

logger = logging.getLogger("chanti")

MAX_RESULTS_CAP = 10
DEFAULT_RESULTS = 5
DEFAULT_DAYS_BACK = 365


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "Semantische Suche über Chantis und Kevins alte Gespräche. "
            "Findet Gesprächs-Ausschnitte zu einem Thema, auch wenn Kevin "
            "andere Worte benutzt als damals. Nutze das wenn Kevin auf etwas "
            "anspielt was er früher erwähnt hat ('das Projekt von neulich', "
            "'wie hieß nochmal…', 'damals als…') und du dich nicht sicher "
            "erinnerst. Auch gut wenn USER.md oder MEMORY.md nichts dazu sagen "
            "aber es klingt als hättet ihr schon mal darüber gesprochen. "
            "Gibt die besten Treffer zurück mit Datum, Kevins Frage und "
            "deiner damaligen Antwort. "
            "Fasse die Ergebnisse IN DEINEN EIGENEN WORTEN zusammen, zitiere "
            "nicht wörtlich — du erinnerst dich, du liest nicht vor."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Das Thema/Stichwort nach dem du suchst. Kein ganzer "
                        "Satz nötig — Schlüsselbegriffe reichen. "
                        "Beispiel: 'agent loop debugging', 'kevins katze tierarzt', "
                        "'groq api rate limit'."
                    ),
                },
                "days_back": {
                    # Manche Modelle (z.B. Llama-4-Scout) schicken Zahlen als String.
                    # Groq validiert das Schema hart — also akzeptieren wir beides
                    # und konvertieren in execute().
                    "type": ["integer", "string"],
                    "description": (
                        f"Wie weit zurück suchen, in Tagen. Default {DEFAULT_DAYS_BACK}. "
                        "Für neuere Themen eher 30, für 'damals' auch mal 365."
                    ),
                },
                "max_results": {
                    "type": ["integer", "string"],
                    "description": (
                        f"Wie viele Treffer zurückgeben. Default {DEFAULT_RESULTS}, "
                        f"Maximum {MAX_RESULTS_CAP}. Weniger ist besser — "
                        "zu viele Treffer verwirren nur."
                    ),
                },
                "include_today": {
                    # Scout schickt Booleans auch als Strings "true"/"false" —
                    # gleiches Problem wie bei den Int-Feldern. Beides akzeptieren,
                    # execute() konvertiert.
                    "type": ["boolean", "string"],
                    "description": (
                        "Ob auch heutige Gespräche in die Suche einbezogen werden. "
                        "Default false — normale Suche ignoriert die letzten 24h, "
                        "damit du dich nicht im gleichen Turn selbst zitierst. "
                        "Setze auf true wenn Kevin explizit auf etwas anspielt "
                        "das heute schon im Gespräch war ('worüber haben wir "
                        "vorhin geredet', 'das Ding von eben'), oder wenn der "
                        "letzte Default-Aufruf nichts brachte und du denkst es "
                        "könnte heute besprochen worden sein."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


# Cosine Distance: 0 = identisch, ~1 = unrelated, 2 = gegensätzlich.
# Unter 0.5 = starker Treffer. 0.5–0.8 = verwandt. Darüber = Rauschen.
# Wir filtern >1.0 komplett raus (oft reine Zufallstreffer bei leeren Themen)
# und markieren 0.8–1.0 als schwach, damit Chanti sich nicht darauf beruft.
SCORE_CUTOFF = 1.0
SCORE_WEAK = 0.8


def _format_results(query: str, results: list[dict], retried: bool = False) -> str:
    # Harte Filter: alles oberhalb SCORE_CUTOFF ist praktisch Rauschen und
    # sollte Chanti nicht als "Erinnerung" präsentiert werden.
    strong_results = [r for r in results if r["score"] < SCORE_CUTOFF]

    if not strong_results:
        return (f"Keine alten Gespräche zu '{query}' gefunden. "
                f"Entweder habt ihr noch nicht darüber geredet, oder zu lang her. "
                f"Sag Kevin ehrlich dass du dich nicht erinnerst — "
                f"erfinde nichts.")

    all_weak = all(r["score"] >= SCORE_WEAK for r in strong_results)

    header = f"Gefunden: {len(strong_results)} Treffer zu '{query}'."
    if retried:
        header += " (Inkl. heutiger Gespräche, da nichts Älteres gefunden.)"
    if all_weak:
        header += (" ACHTUNG: Alle Treffer sind nur schwach verwandt — "
                   "das Thema wurde vermutlich NICHT genau so besprochen. "
                   "Sag Kevin das ehrlich statt zu erfinden dass ihr drüber geredet habt.")

    lines = [header, ""]
    for i, r in enumerate(strong_results, 1):
        quality = "hoch" if r["score"] < 0.5 else ("mittel" if r["score"] < SCORE_WEAK else "schwach")
        lines.append(f"--- Treffer {i} [{r['date']}, Relevanz {quality}] ---")
        lines.append(f"Kevin fragte: {r['user']}")
        lines.append(f"Du antwortetest: {r['assistant']}")
        lines.append("")
    return "\n".join(lines).strip()


def execute(query: str = None, days_back: int = DEFAULT_DAYS_BACK,
            max_results: int = DEFAULT_RESULTS,
            include_today: bool = False) -> str:
    if not query or not str(query).strip():
        return "Fehler: query darf nicht leer sein."

    # Input sanitizen — LLMs schicken gerne mal Strings für Ints
    try:
        days_back = int(days_back)
    except (TypeError, ValueError):
        days_back = DEFAULT_DAYS_BACK
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = DEFAULT_RESULTS

    # Booleans kommen von LLMs auch mal als Strings "true"/"false" oder 1/0
    if isinstance(include_today, str):
        include_today = include_today.strip().lower() in ("true", "1", "yes", "ja")
    else:
        include_today = bool(include_today)

    days_back = max(1, min(days_back, 3650))  # 1 Tag bis 10 Jahre
    max_results = max(1, min(max_results, MAX_RESULTS_CAP))

    # include_today=True → min_age_hours=0 (alle Chunks), sonst Default 24h
    min_age_hours = 0 if include_today else recall_index.MIN_AGE_HOURS

    try:
        results = recall_index.search(
            query=str(query).strip(),
            days_back=days_back,
            max_results=max_results,
            min_age_hours=min_age_hours,
        )

        # Auto-Retry: Wenn der Default-Modus nichts fand (24h-Filter aktiv),
        # probier nochmal ohne Filter. Sicher, weil "keine alten Treffer"
        # bedeutet dass der aktuelle Turn sich sowieso nicht selbst zitieren
        # kann — er wäre sonst schon im ersten Versuch aufgetaucht.
        retried = False
        if not results and not include_today and min_age_hours > 0:
            logger.info(f"recall: Default leer für '{query}', retry mit include_today=true")
            results = recall_index.search(
                query=str(query).strip(),
                days_back=days_back,
                max_results=max_results,
                min_age_hours=0,
            )
            retried = bool(results)
    except ModuleNotFoundError as e:
        # Wird ausgelöst wenn sentence_transformers oder sqlite_vec fehlen
        logger.error(f"recall: Abhängigkeit fehlt: {e}")
        return (f"Tool-Fehler: recall ist nicht einsatzbereit "
                f"(fehlende Bibliothek: {e.name}). Sag Kevin Bescheid.")
    except Exception as e:
        logger.error(f"recall search fehlgeschlagen: {type(e).__name__}: {e}",
                     exc_info=True)
        return f"Tool-Fehler beim Suchen: {type(e).__name__}"

    return _format_results(query, results, retried=retried)
