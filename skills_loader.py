"""Lädt alle Skills aus ~/chanti/skills/ mit Hot-Reload Support."""
import importlib.util
import importlib
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"

# Globaler State
_tools: list[dict] = []
_executors: dict = {}
_mtimes: dict = {}


def _load_skill(skill_file: Path) -> tuple[str, dict, callable] | None:
    """Lädt einen einzelnen Skill. Gibt (name, tool_def, execute_fn) zurück."""
    try:
        module_name = f"skills.{skill_file.stem}"

        # Altes Modul aus sys.modules entfernen für sauberes Reload
        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, skill_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "TOOL_DEFINITION") and hasattr(module, "execute"):
            name = module.TOOL_DEFINITION["function"]["name"]
            return name, module.TOOL_DEFINITION, module.execute
    except Exception as e:
        print(f"[Chanti] Skill {skill_file.name} Fehler: {e}")
    return None


def load_skills() -> tuple[list[dict], dict]:
    """Lädt alle Skills beim Start."""
    global _tools, _executors, _mtimes

    _tools = []
    _executors = {}
    _mtimes = {}

    for skill_file in sorted(SKILLS_DIR.glob("*.py")):
        if skill_file.name.startswith("_"):
            continue
        result = _load_skill(skill_file)
        if result:
            name, tool_def, execute_fn = result
            _tools.append(tool_def)
            _executors[name] = execute_fn
            _mtimes[str(skill_file)] = skill_file.stat().st_mtime
            print(f"[Chanti] Skill geladen: {name}")

    print(f"[Chanti] {len(_tools)} Skills geladen: {list(_executors.keys())}")
    return _tools, _executors


def reload_if_changed() -> bool:
    """
    Prüft ob neue oder geänderte Skills vorhanden sind.
    Lädt sie nach ohne Neustart. Gibt True zurück wenn etwas geändert wurde.
    """
    changed = False

    for skill_file in sorted(SKILLS_DIR.glob("*.py")):
        if skill_file.name.startswith("_"):
            continue

        path_str = str(skill_file)
        current_mtime = skill_file.stat().st_mtime

        if path_str not in _mtimes or _mtimes[path_str] != current_mtime:
            result = _load_skill(skill_file)
            if result:
                name, tool_def, execute_fn = result
                # Alten Eintrag ersetzen oder neu hinzufügen
                _tools[:] = [t for t in _tools if t["function"]["name"] != name]
                _tools.append(tool_def)
                _executors[name] = execute_fn
                _mtimes[path_str] = current_mtime
                print(f"[Chanti] Skill neu geladen: {name}")
                changed = True

    return changed


def get_tools() -> list[dict]:
    return _tools


def get_executors() -> dict:
    return _executors
