"""Tests für image_generate.py — Gemini-Bildgenerierung.

Mockt requests.post, damit keine echten API-Calls rausgehen.
"""
import base64

import pytest
import requests as real_requests

from conftest import load_skill


# Ein einfaches 1×1-PNG als gültige Test-Bilddaten (hex: 0x89 PNG magic...)
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "89000000017352474200aece1ce90000000d49444154789c63000100000005"
    "00010d0a2db40000000049454e44ae426082"
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()


class FakeResp:
    def __init__(self, code=200, payload=None, text=""):
        self.status_code = code
        self._payload = payload
        self._text = text
        self.ok = 200 <= code < 300
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def image_gen(fake_config, monkeypatch):
    """Lädt den Skill mit gestubbtem config.GEMINI_API_KEY."""
    # fake_config aus conftest kennt GEMINI_API_KEY nicht — hier nachrüsten
    fake_config.GEMINI_API_KEY = "test-gemini-key"
    mod = load_skill("image_generate")
    # Store leeren zwischen Tests, damit die Tests isoliert sind
    with mod._STORE_LOCK:
        mod._STORE.clear()
    return mod


@pytest.fixture
def mock_post(monkeypatch):
    """Stubt requests.post. Tests setzen response in `state['resp']`,
    oder `state['exc']` für eine Exception."""
    state = {"resp": None, "exc": None, "calls": []}

    def fake_post(url, headers=None, json=None, timeout=None):
        state["calls"].append({"url": url, "headers": headers, "json": json})
        if state["exc"]:
            raise state["exc"]
        return state["resp"]

    monkeypatch.setattr(real_requests, "post", fake_post)
    return state


def _gemini_ok_response():
    return {
        "candidates": [{
            "content": {
                "parts": [{
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": _PNG_B64,
                    }
                }]
            }
        }]
    }


# ── Happy Path ────────────────────────────────────────────────────────────

def test_execute_returns_marker_on_success(image_gen, mock_post):
    mock_post["resp"] = FakeResp(200, _gemini_ok_response())
    result = image_gen.execute("ein Drache der Kaffee trinkt")
    assert "[[IMG:" in result
    assert "]]" in result
    # Genau ein Call an Gemini
    assert len(mock_post["calls"]) == 1
    assert "generateContent" in mock_post["calls"][0]["url"]


def test_execute_stores_image_in_memory(image_gen, mock_post):
    mock_post["resp"] = FakeResp(200, _gemini_ok_response())
    result = image_gen.execute("irgendwas")
    # Token extrahieren
    import re
    m = re.search(r"\[\[IMG:([A-Za-z0-9_-]+)\]\]", result)
    assert m is not None
    token = m.group(1)
    # Store sollte den Token kennen
    entry = image_gen.store_get(token)
    assert entry is not None
    mime, data = entry
    assert mime == "image/png"
    assert data == _PNG_BYTES


def test_execute_sends_prompt_and_aspect_ratio(image_gen, mock_post):
    mock_post["resp"] = FakeResp(200, _gemini_ok_response())
    image_gen.execute("prompt x", aspect_ratio="16:9")
    payload = mock_post["calls"][0]["json"]
    assert payload["contents"][0]["parts"][0]["text"] == "prompt x"
    assert payload["generationConfig"]["imageConfig"]["aspectRatio"] == "16:9"


def test_execute_accepts_snake_case_inline_data(image_gen, mock_post):
    """Gemini kann je nach API-Version inlineData oder inline_data liefern."""
    payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": _PNG_B64,
                    }
                }]
            }
        }]
    }
    mock_post["resp"] = FakeResp(200, payload)
    result = image_gen.execute("foo")
    assert "[[IMG:" in result


# ── Validierung ────────────────────────────────────────────────────────────

def test_invalid_aspect_ratio_falls_back_to_1_1(image_gen, mock_post):
    mock_post["resp"] = FakeResp(200, _gemini_ok_response())
    image_gen.execute("prompt", aspect_ratio="99:99")
    sent = mock_post["calls"][0]["json"]
    assert sent["generationConfig"]["imageConfig"]["aspectRatio"] == "1:1"


def test_empty_prompt_rejected(image_gen, mock_post):
    result = image_gen.execute("")
    assert "fehlgeschlagen" in result.lower()
    assert len(mock_post["calls"]) == 0


def test_whitespace_prompt_rejected(image_gen, mock_post):
    result = image_gen.execute("   ")
    assert "fehlgeschlagen" in result.lower()
    assert len(mock_post["calls"]) == 0


def test_missing_api_key(fake_config, monkeypatch):
    fake_config.GEMINI_API_KEY = ""
    mod = load_skill("image_generate")
    called = []
    def fake_post(*a, **kw):
        called.append(1)
        return FakeResp(200, _gemini_ok_response())
    monkeypatch.setattr(real_requests, "post", fake_post)
    result = mod.execute("prompt")
    assert "GEMINI_API_KEY" in result
    assert len(called) == 0  # kein Request raus


def test_placeholder_api_key_rejected(fake_config, monkeypatch):
    fake_config.GEMINI_API_KEY = "DEIN_KEY_HIER"
    mod = load_skill("image_generate")
    called = []
    def fake_post(*a, **kw):
        called.append(1)
        return FakeResp(200, _gemini_ok_response())
    monkeypatch.setattr(real_requests, "post", fake_post)
    result = mod.execute("prompt")
    assert "GEMINI_API_KEY" in result
    assert len(called) == 0


# ── Fehlerfälle ────────────────────────────────────────────────────────────

def test_timeout_handled(image_gen, mock_post):
    mock_post["exc"] = real_requests.exceptions.Timeout("slow")
    result = image_gen.execute("prompt")
    assert "fehlgeschlagen" in result.lower()
    assert "timeout" in result.lower()


def test_connection_error_handled(image_gen, mock_post):
    mock_post["exc"] = real_requests.exceptions.ConnectionError("offline")
    result = image_gen.execute("prompt")
    assert "fehlgeschlagen" in result.lower()
    assert "erreichbar" in result.lower() or "connection" in result.lower()


def test_rate_limit_429(image_gen, mock_post):
    mock_post["resp"] = FakeResp(429, {"error": {"message": "quota exceeded"}})
    result = image_gen.execute("prompt")
    assert "rate-limit" in result.lower()


def test_safety_block_400(image_gen, mock_post):
    mock_post["resp"] = FakeResp(
        400, {"error": {"message": "Request blocked by safety filter"}}
    )
    result = image_gen.execute("prompt")
    assert "safety" in result.lower() or "abgelehnt" in result.lower()


def test_other_http_error(image_gen, mock_post):
    mock_post["resp"] = FakeResp(500, {"error": {"message": "server error"}})
    result = image_gen.execute("prompt")
    assert "fehlgeschlagen" in result.lower()
    assert "500" in result


def test_response_without_image_returns_error(image_gen, mock_post):
    """Gemini kann ohne inlineData antworten (z.B. wenn Modell verweigert)."""
    payload = {
        "candidates": [{
            "content": {
                "parts": [{"text": "Sorry, I cannot generate that."}]
            }
        }]
    }
    mock_post["resp"] = FakeResp(200, payload)
    result = image_gen.execute("prompt")
    assert "keine bilddaten" in result.lower()
    # Begründung sollte mit rausgereicht werden
    assert "sorry" in result.lower()


def test_malformed_response(image_gen, mock_post):
    mock_post["resp"] = FakeResp(200, {"garbage": True})
    result = image_gen.execute("prompt")
    assert "fehlgeschlagen" in result.lower()


def test_invalid_base64(image_gen, mock_post):
    payload = {
        "candidates": [{
            "content": {
                "parts": [{
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": "!!!not-base64!!!",
                    }
                }]
            }
        }]
    }
    mock_post["resp"] = FakeResp(200, payload)
    result = image_gen.execute("prompt")
    assert "unlesbar" in result.lower() or "fehlgeschlagen" in result.lower()


# ── Store ──────────────────────────────────────────────────────────────────

def test_store_unknown_token_returns_none(image_gen):
    assert image_gen.store_get("does-not-exist") is None


def test_store_max_entries_respected(image_gen):
    """Store soll nicht unbegrenzt wachsen."""
    mod = image_gen
    limit = mod.MAX_ENTRIES
    tokens = []
    for i in range(limit + 5):
        t = mod.store_put("image/png", f"img{i}".encode())
        tokens.append(t)
    with mod._STORE_LOCK:
        assert len(mod._STORE) <= limit
    # Älteste Tokens sollten weg sein, neueste noch da
    assert mod.store_get(tokens[0]) is None
    assert mod.store_get(tokens[-1]) is not None


def test_tool_definition_structure(image_gen):
    td = image_gen.TOOL_DEFINITION
    assert td["type"] == "function"
    assert td["function"]["name"] == "image_generate"
    assert "prompt" in td["function"]["parameters"]["properties"]
    assert "prompt" in td["function"]["parameters"]["required"]
