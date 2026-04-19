"""Gemeinsame pytest-Fixtures für alle Chanti-Tests."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

# Nur den Chanti-Root in den Python-Path. NICHT skills/ — sonst würde
# Python Module-Namen aus skills/ (z.B. calendar.py) mit Standard-Library
# kollidieren (from calendar import timegm etc.).
_ROOT = Path(__file__).resolve().parent.parent
_SKILLS = _ROOT / "skills"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def load_skill(name: str):
    """Lädt einen Skill aus ~/chanti/skills/ wie es der skills_loader tut —
    mit Modul-Präfix 'chanti_skill_' damit keine Name-Kollision mit
    Standard-Library-Modulen (calendar, json, ...) entsteht."""
    path = _SKILLS / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"Skill-Datei nicht gefunden: {path}")
    module_name = f"chanti_skill_{name}"
    # Alte Version aus sys.modules werfen, damit importlib.reload-Tricks
    # im Test funktionieren
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Setzt HOME auf ein Temp-Verzeichnis und legt ~/chanti/ dort an.

    Alle Module die `Path.home() / "chanti"` nutzen (memory, file_edit,
    calendar_core, leads_db) arbeiten damit im isolierten Sandkasten.
    """
    home = tmp_path
    chanti = home / "chanti"
    chanti.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    # Path.home() cached nicht — liest HOME jedesmal frisch.
    return chanti


@pytest.fixture
def fake_config(monkeypatch):
    """Stubt das config-Modul mit harmlosen Default-Werten.

    Viele Skills importieren at-module-time aus config (HA_TOKEN, GROQ_KEY, ...).
    Ohne echtes config.py würde der Import scheitern.
    """
    import types
    cfg = types.ModuleType("config")
    cfg.HA_URL = "http://ha.test"
    cfg.HA_TOKEN = "test-token"
    cfg.GROQ_API_KEY = "test-key"
    cfg.GROQ_MODEL = "test-model"
    cfg.GROQ_MODEL_TOOLS = "test-model-tools"
    cfg.XTTS_URL = "http://xtts.test"
    cfg.WHISPER_MODEL = "base"
    cfg.WHISPER_DEVICE = "cpu"
    cfg.WHISPER_LANGUAGE = "de"
    cfg.VOSK_MODEL_PATH = "/nonexistent"
    monkeypatch.setitem(sys.modules, "config", cfg)
    return cfg
