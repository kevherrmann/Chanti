"""Tests für memory.py — Fakten-Persistenz, Duplikat-Erkennung, Parser."""
import importlib
import threading

import pytest


@pytest.fixture
def m(tmp_home):
    """Lädt memory frisch, damit BASE auf das Temp-Home zeigt."""
    import memory
    importlib.reload(memory)
    return memory


def test_add_user_fact_persistence(m):
    m.add_user_fact("Ich habe 2 Katzen")
    facts = m._read_user_facts()
    assert len(facts) == 1
    assert "Katzen" in facts[0]


def test_add_user_fact_case_duplicate(m):
    m.add_user_fact("Ich mag Kaffee")
    m.add_user_fact("ich mag kaffee")
    assert len(m._read_user_facts()) == 1


def test_add_user_fact_jaccard_duplicate(m):
    m.add_user_fact("ich habe 2 Katzen die Luna und Felix heißen")
    m.add_user_fact("ich habe zwei katzen die luna und felix heißen")
    # Wörter sind fast identisch (ein Wort unterschiedlich) → Jaccard > 0.8
    assert len(m._read_user_facts()) == 1


def test_blacklist_filters_chanti_self_references(m):
    m.add_user_fact("Chanti ist ein Assistent")
    m.add_user_fact("chanti hat viele Skills")
    assert len(m._read_user_facts()) == 0


def test_blacklist_word_based_not_substring(m):
    """Das Wort 'uhr' in Blacklist darf nicht 'Uhrmacher' blocken."""
    m.add_user_fact("ich arbeite im Uhrmacher-Betrieb")
    assert any("Uhrmacher" in f for f in m._read_user_facts())


def test_blacklist_blocks_date_time(m):
    m.add_user_fact("die uhrzeit ist 19 Uhr")
    m.add_user_fact("das datum heute ist der 17.04.")
    assert len(m._read_user_facts()) == 0


def test_correct_user_fact(m):
    m.add_user_fact("Ich habe 2 Katzen")
    m.correct_user_fact("Ich habe 2 Katzen", "Ich habe 3 Katzen")
    facts = m._read_user_facts()
    assert len(facts) == 1
    assert "3 Katzen" in facts[0]


def test_add_memory_event_deduplicates(m):
    m.add_memory_event("Kevin war in Berlin")
    m.add_memory_event("Kevin war in Berlin")
    m.add_memory_event("kevin war in berlin")
    _, events = m._read_memory_lines()
    assert len(events) == 1


def test_add_memory_empty_is_ignored(m):
    m.add_memory_event("")
    m.add_memory_event("   ")
    _, events = m._read_memory_lines()
    assert len(events) == 0


def test_add_memory_respects_max(m):
    for i in range(m.MAX_MEMORY_EVENTS + 10):
        m.add_memory_event(f"event-{i}")
    _, events = m._read_memory_lines()
    assert len(events) == m.MAX_MEMORY_EVENTS
    # Älteste sollten rausfliegen: der Beginn heißt dann nicht "event-0"
    assert "event-0]" not in events[0]


def test_parse_nested_brackets(m):
    text = "Ok [MERKE: Kevin hat ein [rotes] Auto] [EREIGNIS: Fahrt [nach] Berlin] fertig"
    cleaned = m.parse_and_execute_commands(text)
    # Prä/Postfix muss erhalten sein, Tags raus
    assert cleaned.startswith("Ok")
    assert cleaned.endswith("fertig")
    assert "[MERKE" not in cleaned
    assert "[EREIGNIS" not in cleaned
    assert any("rotes" in f for f in m._read_user_facts())
    _, events = m._read_memory_lines()
    assert any("[nach] Berlin" in e for e in events)


def test_parse_unclosed_bracket_does_not_swallow_rest(m):
    text = "Hallo [MERKE: kaputt ohne Ende weiter geht's"
    cleaned = m.parse_and_execute_commands(text)
    # Rest darf nicht weggeschluckt werden
    assert "weiter geht" in cleaned


def test_parse_correction_arrow_syntax(m):
    m.add_user_fact("ich wohne in Hamburg")
    m.parse_and_execute_commands("[KORRIGIERE: ich wohne in Hamburg → ich wohne in Berlin]")
    facts = m._read_user_facts()
    assert any("Berlin" in f for f in facts)
    assert not any("Hamburg" in f for f in facts)


def test_concurrent_log_writes_not_corrupted(m):
    """log_conversation muss unter Lock laufen — 100 parallele Writes
    sollten 100 komplette '**Kevin:**'-Zeilen ergeben."""
    def worker(n):
        for i in range(20):
            m.log_conversation(f"user-{n}-{i}", f"assistant-{n}-{i}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    from datetime import date
    log = (m.LOG_DIR / f"{date.today().isoformat()}.md").read_text()
    kevin_lines = [l for l in log.splitlines() if l.startswith("**Kevin:**")]
    assert len(kevin_lines) == 100
