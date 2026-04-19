"""Tests für calendar_core.py — Events, Recurring, Race-Safety."""
import threading
from datetime import date, timedelta

import pytest


@pytest.fixture
def cc(tmp_path, monkeypatch):
    """Setzt calendar-Datei auf Temp und reloadet das Modul."""
    cal_file = tmp_path / "cal.json"
    monkeypatch.setenv("CHANTI_CALENDAR_FILE", str(cal_file))
    import importlib
    import calendar_core
    importlib.reload(calendar_core)
    return calendar_core


def test_add_and_list(cc):
    ev = cc.add_event("Test-Termin", "2030-01-15", "14:00")
    assert ev["id"]
    assert ev["title"] == "Test-Termin"
    events = cc.load_events()
    assert len(events) == 1


def test_add_validates_date(cc):
    with pytest.raises(ValueError):
        cc.add_event("x", "2030-13-99")


def test_add_validates_time(cc):
    with pytest.raises(ValueError):
        cc.add_event("x", "2030-01-01", "25:00")


def test_add_validates_title_nonempty(cc):
    with pytest.raises(ValueError):
        cc.add_event("   ", "2030-01-01")


def test_delete(cc):
    ev = cc.add_event("x", "2030-01-01")
    assert cc.delete_event(ev["id"]) is True
    assert cc.delete_event(ev["id"]) is False


def test_recurring_yearly_picks_next_occurrence(cc):
    """Runder Geburtstag — muss immer in der Zukunft liegen."""
    today = date.today()
    past_day = today - timedelta(days=10)
    ev = cc.add_event("Opas Geburtstag", past_day.isoformat(),
                      recurring="yearly")
    events = cc.load_events()
    hit = cc._next_occurrence(events[0], today)
    assert hit is not None
    assert hit >= today


def test_recurring_feb29_falls_back_to_28(cc):
    """29. Februar in Nicht-Schaltjahr → 28. Februar."""
    ev = cc.add_event("Schalttag", "2020-02-29", recurring="yearly")
    # 2023 war kein Schaltjahr
    occ = cc._next_occurrence(ev, date(2023, 2, 1))
    assert occ == date(2023, 2, 28)


def test_get_upcoming_respects_window(cc):
    today = date.today()
    cc.add_event("heute", today.isoformat())
    cc.add_event("morgen", (today + timedelta(days=1)).isoformat())
    cc.add_event("in 5 tagen", (today + timedelta(days=5)).isoformat())
    cc.add_event("in 100 tagen", (today + timedelta(days=100)).isoformat())

    hits = cc.get_upcoming(days=2)
    titles = [h.event["title"] for h in hits]
    assert "heute" in titles
    assert "morgen" in titles
    assert "in 5 tagen" not in titles


def test_cleanup_past_events(cc):
    today = date.today()
    cc.add_event("gestern", (today - timedelta(days=1)).isoformat())
    cc.add_event("heute", today.isoformat())
    cc.add_event("jährlich", (today - timedelta(days=100)).isoformat(),
                 recurring="yearly")

    removed = cc.cleanup_past_events()
    assert removed == 1
    titles = [e["title"] for e in cc.load_events()]
    assert "gestern" not in titles
    assert "heute" in titles
    assert "jährlich" in titles  # recurring bleibt


def test_format_hit_human_readable(cc):
    today = date.today()
    ev = cc.add_event("Test", today.isoformat(), "10:00")
    hit = cc.get_upcoming(days=0)[0]
    text = cc.format_hit_for_human(hit)
    assert "Heute" in text
    assert "10:00" in text
    assert "Test" in text


def test_concurrent_add_and_cleanup_no_data_loss(cc):
    """Klassischer read-modify-write-Race: 10 Threads adden,
    3 cleanup parallel. Mit Lock darf nichts verloren gehen."""
    def adder(tid):
        for i in range(10):
            cc.add_event(f"ev-{tid}-{i}", f"2030-06-{(i % 28) + 1:02d}")

    def cleaner():
        for _ in range(5):
            cc.cleanup_past_events()

    threads = [threading.Thread(target=adder, args=(t,)) for t in range(10)]
    threads += [threading.Thread(target=cleaner) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = cc.load_events()
    assert len(events) == 100
    ids = [e["id"] for e in events]
    assert len(set(ids)) == 100, "IDs müssen unique sein"
