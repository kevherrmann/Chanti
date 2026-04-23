"""Tests für recall_index und den recall-Skill.

Wir stubben sentence_transformers mit einer Fake-Klasse, damit keine
118 MB gezogen werden und jeder Test-Lauf in Millisekunden durch ist.
Die Fake-Embeddings sind deterministisch (Hash-basiert), sodass gleiche
Strings gleiche Vektoren ergeben — das reicht für die Such-Logik-Tests.
"""
from __future__ import annotations

import hashlib
import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import load_skill


# ---------- Fake sentence_transformers + sqlite_vec ----------

class _FakeModel:
    """Deterministic pseudo-embeddings: gleiche/ähnliche Strings → ähnliche Vektoren.

    Wir zerlegen den Text in Wörter und addieren für jedes Wort einen
    deterministischen Basis-Vektor. Das gibt echten semantischen Zusammenhang
    für die Test-Ergebnisse: 'agent loop' matcht gut gegen 'agent loop debug',
    aber schlecht gegen 'totally unrelated'.
    """

    _word_cache: dict = {}

    def _word_vec(self, word: str):
        import numpy as np
        if word in self._word_cache:
            return self._word_cache[word]
        seed = int(hashlib.sha256(word.encode()).hexdigest()[:16], 16)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(384).astype("float32")
        self._word_cache[word] = v
        return v

    def encode(self, texts, normalize_embeddings=False, show_progress_bar=False):
        import numpy as np
        out = []
        for t in texts:
            words = t.lower().split()
            if not words:
                v = np.zeros(384, dtype="float32")
            else:
                v = np.sum([self._word_vec(w) for w in words], axis=0).astype("float32")
            if normalize_embeddings:
                n = float((v * v).sum() ** 0.5)
                if n > 0:
                    v = v / n
            out.append(v)
        return np.array(out)


@pytest.fixture
def recall_setup(tmp_home, monkeypatch):
    """Setzt Fake-Modell + frischen Import von recall_index auf."""
    # sentence_transformers-Modul stubben
    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = lambda *a, **kw: _FakeModel()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    # sqlite_vec ist nicht in jeder Test-Umgebung da; wir versuchen es echt
    # zu importieren, sonst skip.
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        pytest.skip("sqlite-vec nicht installiert")

    # recall_index frisch laden, damit BASE/DB_PATH auf tmp_home zeigen
    sys.modules.pop("recall_index", None)
    import recall_index
    importlib.reload(recall_index)

    # Log-Verzeichnis anlegen
    (tmp_home / "memory").mkdir(exist_ok=True)
    return recall_index


def _write_log(tmp_home: Path, iso_date: str, blocks: list[tuple[str, str]]):
    """Schreibt einen Tages-Log im memory.py-Format."""
    log_dir = tmp_home / "memory"
    log_dir.mkdir(exist_ok=True)
    content = [f"# Log {iso_date}"]
    for user, assistant in blocks:
        content.append(f"\n### {iso_date}")
        content.append(f"**Kevin:** {user}")
        content.append(f"**Chanti:** {assistant}")
    (log_dir / f"{iso_date}.md").write_text("\n".join(content), encoding="utf-8")


def _backdate_chunks(ri, hours_ago: float):
    """Setzt indexed_at aller Chunks zurück, damit der min_age_hours-Filter
    sie nicht ausblendet."""
    import sqlite3
    conn = sqlite3.connect(str(ri.DB_PATH))
    try:
        past = (datetime.now() - timedelta(hours=hours_ago)).timestamp()
        conn.execute("UPDATE chunks SET indexed_at = ?", (past,))
        conn.commit()
    finally:
        conn.close()


# ---------- recall_index Tests ----------

def test_reindex_empty_logs(recall_setup):
    assert recall_setup.reindex_if_changed() == 0


def test_reindex_adds_chunks(recall_setup, tmp_home):
    _write_log(tmp_home, "2026-04-18", [
        ("wie gehts dir?", "gut, und dir?"),
        ("ich hab ne neue katze", "wie heißt sie?"),
    ])
    added = recall_setup.reindex_if_changed()
    assert added == 2
    st = recall_setup.stats()
    assert st["chunks"] == 2
    assert st["files"] == 1


def test_reindex_is_incremental(recall_setup, tmp_home):
    _write_log(tmp_home, "2026-04-18", [("hi", "hallo")])
    assert recall_setup.reindex_if_changed() == 1
    # Zweiter Lauf fügt nichts hinzu
    assert recall_setup.reindex_if_changed() == 0


def test_reindex_rebuilds_changed_file(recall_setup, tmp_home):
    log_date = "2026-04-18"
    _write_log(tmp_home, log_date, [("hi", "hallo")])
    recall_setup.reindex_if_changed()
    # Datei ändern (2 Blöcke statt 1)
    import time; time.sleep(0.01)  # mtime-Tick sicherstellen
    _write_log(tmp_home, log_date, [
        ("hi", "hallo"),
        ("noch was", "klar"),
    ])
    added = recall_setup.reindex_if_changed()
    assert added == 2
    assert recall_setup.stats()["chunks"] == 2  # nicht 3 — alte wurden ersetzt


def test_search_empty_index_returns_empty(recall_setup):
    assert recall_setup.search("irgendwas") == []


def test_search_hides_recent_by_default(recall_setup, tmp_home):
    _write_log(tmp_home, "2026-04-18", [("hi", "hallo")])
    recall_setup.reindex_if_changed()
    # indexed_at ist jetzt, min_age_hours Default=24 → kein Treffer
    assert recall_setup.search("hi") == []


def test_search_finds_old_chunks(recall_setup, tmp_home):
    _write_log(tmp_home, "2026-04-10", [("mein Lieblingsbuch", "oh welches?")])
    recall_setup.reindex_if_changed()
    _backdate_chunks(recall_setup, hours_ago=48)
    results = recall_setup.search("Lieblingsbuch")
    assert len(results) == 1
    assert "Lieblingsbuch" in results[0]["user"]
    assert results[0]["date"] == "2026-04-10"


def test_search_respects_days_back(recall_setup, tmp_home):
    # Ein alter Eintrag (>30 Tage) und ein mittelalter (20 Tage)
    old_date = (datetime.now() - timedelta(days=40)).date().isoformat()
    mid_date = (datetime.now() - timedelta(days=20)).date().isoformat()
    _write_log(tmp_home, old_date, [("katze Luna", "süß")])
    _write_log(tmp_home, mid_date, [("hund Bello", "wie alt?")])
    recall_setup.reindex_if_changed()
    _backdate_chunks(recall_setup, hours_ago=48)

    # days_back=30 → nur mid_date kommt durch
    r = recall_setup.search("haustier", days_back=30)
    dates = {x["date"] for x in r}
    assert old_date not in dates


def test_empty_query_returns_empty(recall_setup):
    assert recall_setup.search("") == []
    assert recall_setup.search("   ") == []


def test_stats_on_empty_db(recall_setup):
    s = recall_setup.stats()
    assert s["chunks"] == 0
    assert s["files"] == 0
    assert s["oldest_date"] is None


# ---------- recall Skill Tests ----------

@pytest.fixture
def recall_skill(recall_setup, tmp_home):
    """Lädt den recall-Skill. recall_setup stellt sicher dass recall_index
    bereit ist."""
    # conftest.load_skill importiert aus skills/ — Skill heißt 'recall'
    return load_skill("recall")


def test_skill_tool_definition(recall_skill):
    td = recall_skill.TOOL_DEFINITION
    assert td["function"]["name"] == "recall"
    assert "query" in td["function"]["parameters"]["properties"]
    assert td["function"]["parameters"]["required"] == ["query"]


def test_skill_schema_accepts_int_and_string_for_numeric_params(recall_skill):
    """Groq validiert Schema hart — Scout schickt manchmal Strings statt Ints.
    Beide Typen müssen im Schema erlaubt sein."""
    props = recall_skill.TOOL_DEFINITION["function"]["parameters"]["properties"]
    for key in ("days_back", "max_results"):
        t = props[key]["type"]
        # Liste mit beiden Typen, Reihenfolge egal
        assert isinstance(t, list), f"{key}.type muss Liste sein, war {t}"
        assert "integer" in t and "string" in t, f"{key}.type: {t}"


def test_skill_empty_query(recall_skill):
    assert "Fehler" in recall_skill.execute(query="")
    assert "Fehler" in recall_skill.execute(query=None)


def test_skill_no_results_message(recall_skill, tmp_home):
    out = recall_skill.execute(query="gibts nicht")
    assert "Keine alten Gespräche" in out or "nicht gefunden" in out.lower()


def test_skill_formats_results(recall_skill, recall_setup, tmp_home, monkeypatch):
    _write_log(tmp_home, "2026-04-10", [
        ("erzähl mal über das Agent-Projekt", "das ist chanti"),
    ])
    recall_setup.reindex_if_changed()
    _backdate_chunks(recall_setup, hours_ago=48)
    # Fake-Embeddings liefern ungenaue Scores; für diesen Format-Test
    # reicht es wenn überhaupt etwas durchkommt.
    monkeypatch.setattr(recall_skill, "SCORE_CUTOFF", 2.0)
    out = recall_skill.execute(query="agent projekt")
    assert "2026-04-10" in out
    assert "Kevin fragte" in out
    assert "Du antwortetest" in out


def test_skill_coerces_string_params(recall_skill, recall_setup, tmp_home):
    """LLMs schicken manchmal Strings für Ints — soll nicht crashen."""
    _write_log(tmp_home, "2026-04-10", [("hallo", "hi")])
    recall_setup.reindex_if_changed()
    _backdate_chunks(recall_setup, hours_ago=48)
    # max_results="3" (String) → soll wie int(3) wirken
    out = recall_skill.execute(query="hallo", max_results="3", days_back="30")
    # Nur kein Crash, kein spezifischer Erwartungswert
    assert "Fehler" not in out or "nicht gefunden" in out.lower() or "Keine" in out


def test_skill_caps_max_results(recall_skill, recall_setup, tmp_home):
    # 3 Einträge an verschiedenen Tagen
    for i, d in enumerate(["2026-04-01", "2026-04-02", "2026-04-03"]):
        _write_log(tmp_home, d, [(f"frage{i}", f"antwort{i}")])
    recall_setup.reindex_if_changed()
    _backdate_chunks(recall_setup, hours_ago=48)
    # User fragt nach 100, Tool cappt auf MAX_RESULTS_CAP=10
    out = recall_skill.execute(query="frage", max_results=100)
    # Mehr als 10 sollten's sowieso nicht sein — aber Hauptsache: kein Crash
    assert "Gefunden" in out or "Keine" in out


def test_skill_handles_missing_library(recall_skill, monkeypatch, tmp_home):
    """Wenn sentence_transformers fehlt, soll der Skill eine klare Meldung geben."""
    import recall_index

    def _boom(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'sentence_transformers'")
    monkeypatch.setattr(recall_index, "search", _boom)

    out = recall_skill.execute(query="egal")
    assert "nicht einsatzbereit" in out.lower() or "bibliothek" in out.lower()


# ---------- include_today ----------

def test_include_today_default_hides_recent_then_retries(recall_skill, recall_setup, tmp_home, monkeypatch):
    """Wenn Default leer ist, wird automatisch nochmal gesucht.
    Also finden wir hier den heutigen Chunk — mit Retry-Marker."""
    monkeypatch.setattr(recall_skill, "SCORE_CUTOFF", 2.0)
    today = datetime.now().date().isoformat()
    _write_log(tmp_home, today, [("heute-Thema", "heute-Antwort")])
    recall_setup.reindex_if_changed()
    # KEIN _backdate_chunks — die Chunks sind frisch
    out = recall_skill.execute(query="heute-Thema")
    # Retry greift, Thema wird gefunden, Marker ist drin
    assert "heute-Thema" in out
    assert "Inkl. heutiger" in out or "heutiger Gespräche" in out


def test_include_today_true_shows_recent(recall_skill, recall_setup, tmp_home, monkeypatch):
    """Mit include_today=True werden auch frische Chunks gefunden."""
    monkeypatch.setattr(recall_skill, "SCORE_CUTOFF", 2.0)
    today = datetime.now().date().isoformat()
    _write_log(tmp_home, today, [("heute-Thema", "heute-Antwort")])
    recall_setup.reindex_if_changed()
    out = recall_skill.execute(query="heute-Thema", include_today=True)
    assert "heute-Thema" in out
    assert today in out


def test_include_today_accepts_string_boolean(recall_skill, recall_setup, tmp_home):
    """LLMs schicken manchmal 'true' als String. Soll wie Bool True wirken."""
    today = datetime.now().date().isoformat()
    _write_log(tmp_home, today, [("heute-Thema", "heute-Antwort")])
    recall_setup.reindex_if_changed()
    out = recall_skill.execute(query="heute-Thema", include_today="true")
    assert "heute-Thema" in out


def test_include_today_schema_has_boolean(recall_skill):
    """Schema muss include_today als Boolean ausweisen — und String zulassen,
    weil Scout gern '"true"' statt true schickt."""
    props = recall_skill.TOOL_DEFINITION["function"]["parameters"]["properties"]
    assert "include_today" in props
    t = props["include_today"]["type"]
    assert isinstance(t, list)
    assert "boolean" in t and "string" in t


# ---------- Auto-Retry ----------

def test_auto_retry_when_default_empty(recall_skill, recall_setup, tmp_home):
    """Wenn im Default-Modus (24h-Filter) nichts gefunden wird, soll das
    Tool automatisch nochmal ohne Filter suchen. So findet Chanti auch
    ohne explizit nach 'heute' zu fragen, was heute besprochen wurde."""
    today = datetime.now().date().isoformat()
    _write_log(tmp_home, today, [("agent loop debugging", "jepp, fixen wir")])
    recall_setup.reindex_if_changed()
    # Kein _backdate — Chunks sind frisch (<24h)
    # User ruft OHNE include_today auf — das ist der Szenario-Trigger
    out = recall_skill.execute(query="agent loop")
    assert "agent loop" in out.lower()
    assert today in out
    # Result muss markieren dass Retry griff, damit Chanti die Formulierung
    # anpassen kann
    assert "heutiger Gespräche" in out or "Inkl. heutiger" in out


def test_no_retry_when_include_today_already_true(recall_skill, recall_setup, tmp_home):
    """Wenn include_today schon true war und trotzdem nichts kam, kein Retry
    (da ist nix zu retryen)."""
    out = recall_skill.execute(query="gibts-nicht", include_today=True)
    assert "Keine" in out
    # Kein Retry-Hinweis im leeren Ergebnis
    assert "heutiger Gespräche" not in out


def test_no_retry_marker_when_default_finds_results(recall_skill, recall_setup, tmp_home):
    """Wenn Default-Modus Treffer findet, KEIN Retry — also auch kein Hinweis."""
    _write_log(tmp_home, "2026-04-10", [("altes thema xyz", "ja genau")])
    recall_setup.reindex_if_changed()
    _backdate_chunks(recall_setup, hours_ago=48)
    out = recall_skill.execute(query="altes thema")
    assert "altes thema" in out.lower()
    # KEIN Retry-Marker
    assert "heutiger Gespräche" not in out
    assert "Inkl. heutiger" not in out


# ---------- Score-Cutoff / Qualität ----------

def test_weak_matches_get_warning_header(recall_skill, recall_setup, tmp_home):
    """Wenn alle Treffer nur schwach verwandt sind, soll Output warnen."""
    import recall_index

    fake_results = [
        {"date": "2026-04-10", "user": "völlig anderes Thema",
         "assistant": "ja ja", "score": 0.95},
    ]

    def fake_search(**kw):
        return fake_results
    original = recall_index.search
    recall_index.search = fake_search
    try:
        out = recall_skill.execute(query="spezifische frage")
    finally:
        recall_index.search = original

    assert "ACHTUNG" in out or "schwach verwandt" in out.lower()


def test_noise_matches_filtered_out(recall_skill, recall_setup, tmp_home):
    """Treffer mit score >= SCORE_CUTOFF werden komplett rausgefiltert."""
    import recall_index

    # Ein Treffer unter Cutoff, einer drüber (pure Zufall)
    fake_results = [
        {"date": "2026-04-10", "user": "gutes match",
         "assistant": "ja", "score": 0.6},
        {"date": "2026-04-09", "user": "totales rauschen",
         "assistant": "hmm", "score": 1.5},
    ]

    def fake_search(**kw):
        return fake_results
    original = recall_index.search
    recall_index.search = fake_search
    try:
        out = recall_skill.execute(query="irgendwas")
    finally:
        recall_index.search = original

    assert "gutes match" in out
    assert "totales rauschen" not in out


def test_only_noise_treated_as_empty(recall_skill, recall_setup, tmp_home):
    """Wenn alle Treffer Rauschen sind, soll das wie 'nichts gefunden' wirken —
    inkl. dem Hinweis für Chanti, nichts zu erfinden."""
    import recall_index

    fake_results = [
        {"date": "2026-04-10", "user": "totaler müll", "assistant": "a", "score": 1.5},
        {"date": "2026-04-09", "user": "auch müll", "assistant": "b", "score": 1.3},
    ]

    def fake_search(**kw):
        return fake_results
    original = recall_index.search
    recall_index.search = fake_search
    try:
        out = recall_skill.execute(query="spezifisch")
    finally:
        recall_index.search = original

    assert "Keine" in out or "nicht gefunden" in out.lower()
    assert "erfinde nichts" in out.lower() or "ehrlich" in out.lower()
