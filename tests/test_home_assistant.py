"""Tests für home_assistant.py — Mock von requests.post."""
import pytest
import requests as real_requests

from conftest import load_skill


class FakeResp:
    def __init__(self, code=200):
        self.status_code = code
    def raise_for_status(self):
        if self.status_code >= 400:
            raise real_requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self)


@pytest.fixture
def ha(fake_config, monkeypatch):
    # Skill lädt at-import-time from config — fake_config muss vorher greifen
    home_assistant = load_skill("home_assistant")

    calls = []
    mode = {"v": "ok"}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        m = mode["v"]
        if m == "conn_err":
            raise real_requests.exceptions.ConnectionError("offline")
        if m == "timeout":
            raise real_requests.exceptions.Timeout("slow")
        if m == "partial":
            eid = (json or {}).get("entity_id", "")
            if eid == "light.nachttischlampe_links":
                raise real_requests.exceptions.ConnectionError("nur diese eine")
            return FakeResp(200)
        return FakeResp(200)

    monkeypatch.setattr(real_requests, "post", fake_post)
    return home_assistant, calls, mode


def test_single_lamp_on(ha):
    home_assistant, calls, _ = ha
    result = home_assistant.execute(lampe="ringlampe")
    assert "eingeschaltet" in result.lower()
    assert len(calls) == 1
    assert "turn_on" in calls[0]["url"]


def test_single_lamp_off(ha):
    home_assistant, calls, _ = ha
    home_assistant.execute(lampe="ringlampe", aktion="aus")
    assert "turn_off" in calls[0]["url"]


def test_color_and_brightness_single_call(ha):
    """Farbe + Helligkeit müssen in EINEM HA-Call landen, nicht zwei."""
    home_assistant, calls, _ = ha
    home_assistant.execute(lampe="ringlampe", farbe="rot", helligkeit=50)
    assert len(calls) == 1
    payload = calls[0]["json"]
    assert "hs_color" in payload
    assert "brightness" in payload


def test_unknown_color_does_not_call_ha(ha):
    home_assistant, calls, _ = ha
    result = home_assistant.execute(lampe="ringlampe", farbe="regenbogen")
    assert "unbekannt" in result.lower()
    assert len(calls) == 0


def test_invalid_brightness_rejected(ha):
    home_assistant, calls, _ = ha
    assert "Helligkeit" in home_assistant.execute(lampe="ringlampe", helligkeit=200)
    assert "Helligkeit" in home_assistant.execute(lampe="ringlampe", helligkeit=-5)
    assert "Helligkeit" in home_assistant.execute(lampe="ringlampe", helligkeit="viel")
    assert len(calls) == 0


def test_unknown_lamp(ha):
    home_assistant, calls, _ = ha
    result = home_assistant.execute(lampe="lampe im keller")
    assert "nicht gefunden" in result.lower()
    assert len(calls) == 0


def test_group_partial_failure_still_processes_rest(ha):
    """Eine kaputte Lampe darf die anderen 3 nicht blockieren."""
    home_assistant, calls, mode = ha
    mode["v"] = "partial"
    result = home_assistant.execute(lampe="alle lampen")
    # Alle 4 müssen versucht worden sein
    assert len(calls) == 4
    # Result enthält Teilerfolg-Meldung
    assert "fehlgeschlagen" in result.lower()


def test_complete_ha_offline(ha):
    home_assistant, calls, mode = ha
    mode["v"] = "conn_err"
    result = home_assistant.execute(lampe="nachttischlampen")
    assert "nicht erreichbar" in result.lower() or "fehler" in result.lower()


def test_weiss_alias_works(ha):
    """'weiss' (ohne Umlaut) muss wie 'weiß' funktionieren."""
    home_assistant, calls, _ = ha
    home_assistant.execute(lampe="ringlampe", farbe="weiss")
    assert "color_temp" in calls[0]["json"]
