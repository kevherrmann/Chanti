"""Tests für text_utils.py — TTS-Bereinigung, Silence-Padding."""
import numpy as np

import text_utils as tu


def test_markdown_stripped():
    assert tu.clean_for_tts("**fett** und *kursiv*") == "fett und kursiv"


def test_code_backticks_stripped():
    assert "code" in tu.clean_for_tts("das ist `code`")
    assert "`" not in tu.clean_for_tts("das ist `code`")


def test_headers_stripped():
    assert tu.clean_for_tts("# Überschrift\ntext") == "Überschrift text"


def test_urls_removed():
    assert "example" not in tu.clean_for_tts("siehe https://example.com hier")


def test_percent_becomes_prozent():
    assert "Prozent" in tu.clean_for_tts("Helligkeit auf 50%")


def test_ampersand_becomes_und():
    assert "und" in tu.clean_for_tts("Max & Moritz")


def test_euro_becomes_word():
    assert "Euro" in tu.clean_for_tts("Das kostet 50€")


def test_umlauts_preserved():
    assert tu.clean_for_tts("äöü ÄÖÜ ß") == "äöü ÄÖÜ ß"


def test_multiple_spaces_collapsed():
    assert tu.clean_for_tts("   viel    Platz   ") == "viel Platz"


def test_ellipsis_becomes_space():
    result = tu.clean_for_tts("Warte... und dann")
    assert "..." not in result
    assert "Warte" in result and "dann" in result


def test_padding_normal():
    a = np.ones(100, dtype=np.float32)
    padded = tu.add_silence_padding(a, 16000, pad_seconds=0.5)
    assert len(padded) == 100 + 16000 // 2


def test_padding_clipped_to_max():
    a = np.ones(100, dtype=np.float32)
    padded = tu.add_silence_padding(a, 16000, pad_seconds=1000)
    # Max 2s Padding → 32000 samples bei 16kHz
    assert len(padded) <= 100 + 16000 * 2


def test_padding_negative_ignored():
    a = np.ones(100, dtype=np.float32)
    padded = tu.add_silence_padding(a, 16000, pad_seconds=-5)
    assert len(padded) == 100


def test_padding_zero_is_noop():
    a = np.ones(100, dtype=np.float32)
    padded = tu.add_silence_padding(a, 16000, pad_seconds=0)
    assert len(padded) == 100
