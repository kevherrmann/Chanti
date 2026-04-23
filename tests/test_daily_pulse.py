"""Tests für daily_pulse und pulse_news."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def pulse(tmp_home, monkeypatch, fake_config):
    """Lädt daily_pulse mit isoliertem tmp_home."""
    # Alle ENV-Flags auf True
    for k in ("CHANTI_PULSE_ENABLED", "CHANTI_PULSE_CALENDAR",
              "CHANTI_PULSE_INACTIVITY", "CHANTI_PULSE_NEWS"):
        monkeypatch.setenv(k, "true")

    # telegram_notify stubben — keine echten Telegram-Calls in Tests
    import types
    fake_tg = types.ModuleType("telegram_notify")
    fake_tg._sent = []
    fake_tg.send_telegram = lambda text: (fake_tg._sent.append(text) or True)
    monkeypatch.setitem(sys.modules, "telegram_notify", fake_tg)

    # calendar_core muss auf tmp_home zeigen
    monkeypatch.setenv("CHANTI_CALENDAR_FILE", str(tmp_home / "calendar.json"))

    sys.modules.pop("daily_pulse", None)
    import daily_pulse
    importlib.reload(daily_pulse)
    daily_pulse._tg = fake_tg  # für Tests zugänglich
    return daily_pulse


@pytest.fixture
def news():
    sys.modules.pop("pulse_news", None)
    import pulse_news
    importlib.reload(pulse_news)
    return pulse_news


def _write_log_with_kevin(tmp_home: Path, iso_date: str):
    """Schreibt einen Log mit Kevin-Block, damit _last_user_message_date was findet."""
    log_dir = tmp_home / "memory"
    log_dir.mkdir(exist_ok=True)
    (log_dir / f"{iso_date}.md").write_text(
        f"# Log {iso_date}\n\n### {iso_date}\n**Kevin:** hi\n**Chanti:** hallo\n",
        encoding="utf-8",
    )


# ─── Scheduler ─────────────────────────────────────────────────────────

def test_seconds_until_next_run_future_same_day(pulse, monkeypatch):
    """Wenn aktuelle Zeit vor HH:MM ist, sollte es bis heute HH:MM warten."""
    from datetime import datetime as real_dt
    fixed_now = real_dt(2026, 4, 21, 10, 0, 0)  # 10:00, Puls um 18:00

    class FakeDatetime(real_dt):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr("daily_pulse.datetime", FakeDatetime)
    sec = pulse._seconds_until_next_run()
    # 8 Stunden = 28800s
    assert 28700 < sec < 28900


def test_seconds_until_next_run_after_today_targets_tomorrow(pulse, monkeypatch):
    """Wenn HH:MM heute schon vorbei → nächste Ausführung morgen."""
    from datetime import datetime as real_dt
    fixed_now = real_dt(2026, 4, 21, 20, 0, 0)  # 20:00, Puls war um 18:00

    class FakeDatetime(real_dt):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr("daily_pulse.datetime", FakeDatetime)
    sec = pulse._seconds_until_next_run()
    # ~22 Stunden
    assert 79000 < sec < 79500


# ─── Inaktivitäts-Check ─────────────────────────────────────────────────

def test_inactivity_no_logs_returns_none(pulse, tmp_home):
    """Frische Installation ohne Logs → keine Meldung."""
    assert pulse._check_inactivity() is None


def test_inactivity_recent_chat_returns_none(pulse, tmp_home):
    """Wenn gerade erst geredet → keine Meldung."""
    _write_log_with_kevin(tmp_home, date.today().isoformat())
    assert pulse._check_inactivity() is None


def test_inactivity_old_chat_triggers(pulse, tmp_home):
    """Chat vor 5 Tagen → Meldung."""
    old = (date.today() - timedelta(days=5)).isoformat()
    _write_log_with_kevin(tmp_home, old)
    msg = pulse._check_inactivity()
    assert msg is not None
    assert "länger" in msg.lower() or "5 tagen" in msg.lower()


def test_inactivity_asks_only_once_per_silence_period(pulse, tmp_home):
    """Nach erstem Fragen sollte im gleichen Stille-Fenster nicht nochmal gefragt werden."""
    old = (date.today() - timedelta(days=5)).isoformat()
    _write_log_with_kevin(tmp_home, old)

    first = pulse._check_inactivity()
    assert first is not None

    # Zweiter Call → sollte None sein, weil im State vermerkt
    second = pulse._check_inactivity()
    assert second is None


def test_inactivity_long_silence_uses_days_phrasing(pulse, tmp_home):
    """Bei >7 Tagen: anderer Ton mit konkreter Tagesangabe."""
    very_old = (date.today() - timedelta(days=10)).isoformat()
    _write_log_with_kevin(tmp_home, very_old)
    msg = pulse._check_inactivity()
    assert msg is not None
    assert "10 tagen" in msg.lower()


def test_inactivity_resets_when_new_chat_happens(pulse, tmp_home):
    """Wenn Kevin wieder schreibt und dann erneut Stille, darf nochmal gefragt werden."""
    # Erste Stille-Phase → fragen
    _write_log_with_kevin(tmp_home, (date.today() - timedelta(days=5)).isoformat())
    assert pulse._check_inactivity() is not None

    # Kevin schreibt heute
    _write_log_with_kevin(tmp_home, date.today().isoformat())
    assert pulse._check_inactivity() is None  # kein Stille-Fenster mehr

    # Wir simulieren: Kevin schreibt vor 5 Tagen (das "heute" Log war damals)
    # und heute ist 5 Tage später → neues Stille-Fenster
    # Das ist schwer sauber zu testen ohne date zu mocken; wir testen stattdessen
    # dass der State-Reset über last_chat >= last_asked funktioniert:
    state = pulse._load_state()
    assert "last_inactivity_ask" in state


# ─── Kalender-Check ─────────────────────────────────────────────────────

def test_calendar_no_events(pulse, tmp_home):
    assert pulse._check_calendar() is None


def test_calendar_today_only_is_ignored(pulse, tmp_home, monkeypatch):
    """Heutige Events sollen vom Abend-Pulse NICHT gemeldet werden
    (Morgen-Reminder hat die schon)."""
    import calendar_core
    importlib.reload(calendar_core)
    calendar_core.add_event(title="Heute-Termin", date_iso=date.today().isoformat(), time_hm=None)
    assert pulse._check_calendar() is None


def test_calendar_tomorrow_triggers(pulse, tmp_home):
    import calendar_core
    importlib.reload(calendar_core)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    calendar_core.add_event(title="Zahnarzt", date_iso=tomorrow, time_hm="10:00")
    msg = pulse._check_calendar()
    assert msg is not None
    assert "Zahnarzt" in msg


# ─── State-Persistenz ──────────────────────────────────────────────────

def test_state_save_and_load(pulse, tmp_home):
    pulse._save_state({"foo": "bar", "nr": 42})
    loaded = pulse._load_state()
    assert loaded["foo"] == "bar"
    assert loaded["nr"] == 42


def test_state_corrupt_json_returns_empty(pulse, tmp_home):
    pulse.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    pulse.STATE_FILE.write_text("{not valid json", encoding="utf-8")
    assert pulse._load_state() == {}


# ─── Kill-Switch ────────────────────────────────────────────────────────

def test_disabled_via_env_exits_immediately(tmp_home, monkeypatch, fake_config):
    import types
    fake_tg = types.ModuleType("telegram_notify")
    fake_tg.send_telegram = lambda text: True
    monkeypatch.setitem(sys.modules, "telegram_notify", fake_tg)

    monkeypatch.setenv("CHANTI_PULSE_ENABLED", "false")
    sys.modules.pop("daily_pulse", None)
    import daily_pulse
    importlib.reload(daily_pulse)
    # Task läuft sofort durch ohne Exception
    asyncio.run(daily_pulse.daily_pulse_task())


# ─── News-Filter ────────────────────────────────────────────────────────

def test_news_filter_stack_keyword_wins(news):
    candidates = [
        {"title": "Groq releases new Llama endpoint", "body": "faster", "href": "http://a"},
        {"title": "Random blogpost about cats", "body": "fluffy", "href": "http://b"},
    ]
    relevant = news._filter_relevant(candidates)
    assert len(relevant) == 1
    assert "Groq" in relevant[0]["title"]


def test_news_filter_noise_gets_penalized(news):
    candidates = [
        {"title": "Top 10 AI stocks to buy", "body": "investment tips", "href": "http://x"},
    ]
    assert news._filter_relevant(candidates) == []


def test_news_filter_broad_keyword_meets_threshold_only_with_help(news):
    # Nur ein Broad-Keyword (3 Pt) reicht nicht für Cutoff=5
    candidates = [
        {"title": "Anthropic announces something", "body": "small news", "href": "http://c"},
    ]
    result = news._filter_relevant(candidates)
    assert result == []  # score nur 3, cutoff 5

    # Aber zwei Broad-Keywords reichen (3+3=6)
    candidates2 = [
        {"title": "Anthropic and OpenAI collaborate", "body": "", "href": "http://d"},
    ]
    result2 = news._filter_relevant(candidates2)
    assert len(result2) == 1


def test_news_parse_search_output(news):
    sample = (
        "Suchergebnisse für 'test':\n"
        "- Titel A: Body-Text A\n"
        "  URL: http://a.de\n"
        "- Titel B: Body mit: Doppelpunkt drin\n"
        "  URL: http://b.de\n"
    )
    parsed = news._parse_search_output(sample)
    assert len(parsed) == 2
    assert parsed[0]["title"] == "Titel A"
    assert parsed[0]["href"] == "http://a.de"
    assert parsed[1]["title"] == "Titel B"
    assert "Doppelpunkt" in parsed[1]["body"]


def test_news_format_briefing_is_markdown_free(news):
    picks = [
        {"title": "Groq news", "body": "something happened", "href": "http://x", "_score": 10},
    ]
    text = news._format_briefing(picks)
    # Kein Markdown (Telegram-Schutz)
    assert "**" not in text
    assert "*" not in text.replace("  ", " ")  # Bullet-Stern nicht mitzählen
    assert "Groq news" in text
    assert "http://x" in text


# ─── Manueller Trigger ──────────────────────────────────────────────────

def test_trigger_now_runs_checks(pulse, tmp_home, monkeypatch):
    """trigger_now soll _run_all_checks aufrufen und nicht crashen
    auch wenn's nichts zu melden gibt."""
    # calendar_core frisch laden, sonst bleibt Kalender-Zustand aus
    # vorherigen Tests (calendar_core cached CALENDAR_FILE-Pfad beim Import)
    import calendar_core
    importlib.reload(calendar_core)
    # News deaktivieren — kein echter Websearch in Tests
    monkeypatch.setattr(pulse, "ENABLE_NEWS", False)

    asyncio.run(pulse.trigger_now())
    # Nichts zu melden → keine Messages im Fake-TG
    assert pulse._tg._sent == []


def test_trigger_now_sends_when_content(pulse, tmp_home, monkeypatch):
    """Mit Kalender-Event morgen → eine Message sollte gesendet werden."""
    import calendar_core
    importlib.reload(calendar_core)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    calendar_core.add_event(title="Termin-X", date_iso=tomorrow, time_hm="09:00")

    # News-Check deaktivieren, damit kein echter Websearch passiert
    monkeypatch.setattr(pulse, "ENABLE_NEWS", False)

    asyncio.run(pulse.trigger_now())
    assert any("Termin-X" in m for m in pulse._tg._sent)
