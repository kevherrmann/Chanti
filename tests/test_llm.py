"""Tests für llm.py — Tool-Call-Parsing, Retry-Logik, Redaction."""
import json

import pytest


@pytest.fixture
def llm(fake_config):
    import importlib
    import llm
    importlib.reload(llm)
    return llm


def test_redact_keeps_short(llm):
    assert llm._redact({"q": "hi"}) == '{"q": "hi"}'


def test_redact_truncates_long(llm):
    long_val = "x" * 500
    out = llm._redact({"q": long_val})
    assert out.endswith("…")
    assert len(out) <= 310


def test_redact_handles_non_serializable(llm):
    class Obj:
        pass
    # Darf nicht crashen
    out = llm._redact({"x": Obj()})
    assert isinstance(out, str)


def test_parse_failed_generation_simple(llm):
    raw = '<function=web_search{"query":"foo"}>'
    executors = {"web_search": lambda **k: f"searched: {k['query']}"}
    assert llm._parse_failed_generation(raw, executors) == "searched: foo"


def test_parse_failed_generation_nested_json(llm):
    """Der alte Regex-Parser brach bei verschachteltem JSON ab.
    Balanced-brace-Parser muss das korrekt handhaben."""
    raw = ('<function=blender{"action":"create","primitive":"cube",'
           '"location":[1,2,3],"nested":{"a":"b"}}>')
    got = {}
    def fake(**k):
        got.update(k)
        return "ok"
    llm._parse_failed_generation(raw, {"blender": fake})
    assert got["nested"] == {"a": "b"}
    assert got["location"] == [1, 2, 3]


def test_parse_failed_generation_brace_in_string(llm):
    """'}' in einem String-Wert darf nicht zu frühem Abbruch führen."""
    raw = '<function=x{"q":"wie heißt {der} bundeskanzler?"}>'
    captured = {}
    def fake(**k):
        captured.update(k)
        return "ok"
    result = llm._parse_failed_generation(raw, {"x": fake})
    assert result == "ok"
    assert captured["q"] == "wie heißt {der} bundeskanzler?"


def test_parse_failed_generation_returns_none_for_broken(llm):
    assert llm._parse_failed_generation("<function=x{nicht json}>", {"x": lambda **k: None}) is None
    assert llm._parse_failed_generation("no function tag here", {}) is None


def test_parse_failed_generation_unknown_tool(llm):
    raw = '<function=nonexistent{"a":1}>'
    assert llm._parse_failed_generation(raw, {}) is None


def test_parse_failed_generation_tool_exception_is_reported(llm):
    raw = '<function=boom{"a":1}>'
    def bad(**k):
        raise RuntimeError("kaputt")
    result = llm._parse_failed_generation(raw, {"boom": bad})
    # Darf nicht crashen, muss klar als Tool-Fehler gekennzeichnet sein
    assert result is not None
    assert "Tool-Fehler" in result or "RuntimeError" in result


def test_extract_content_handles_missing_keys(llm):
    # Leeres/defektes JSON soll nicht crashen
    assert llm._extract_content({}) == ""
    assert llm._extract_content({"choices": []}) == ""
    assert llm._extract_content({"choices": [{"message": {}}]}) == ""
