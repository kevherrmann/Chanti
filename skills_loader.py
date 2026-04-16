"""Lädt alle Skills aus ~/chanti/skills/ mit Hot-Reload Support."""
import importlib.util
import importlib
import sys
import logging
from pathlib import Path

logger = logging.getLogger("chanti")

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
        logger.error(f"Skill {skill_file.name} Fehler: {e}")
    return None


def load_skills() -> tuple[list[dict], dict]:
    """Lädt alle Skills beim Start."""
    global _tools, _executors, _mtimes

    _tools = []
    _executors = {}
    _mtimes = {}

    if not SKILLS_DIR.exists():
        logger.warning(f"Skills-Verzeichnis nicht gefunden: {SKILLS_DIR}")
        return _tools, _executors

    for skill_file in sorted(SKILLS_DIR.glob("*.py")):
        if skill_file.name.startswith("_"):
            continue
        result = _load_skill(skill_file)
        if result:
            name, tool_def, execute_fn = result
            _tools.append(tool_def)
            _executors[name] = execute_fn
            _mtimes[str(skill_file)] = skill_file.stat().st_mtime
            logger.info(f"Skill geladen: {name}")

    logger.info(f"{len(_tools)} Skills geladen: {list(_executors.keys())}")
    return _tools, _executors


def reload_if_changed() -> bool:
    """
    Prüft ob neue oder geänderte Skills vorhanden sind.
    Lädt sie nach ohne Neustart. Gibt True zurück wenn etwas geändert wurde.
    """
    if not SKILLS_DIR.exists():
        return False

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
                logger.info(f"Skill neu geladen: {name}")
                changed = True

    return changed


def get_tools() -> list[dict]:
    return _tools


def get_executors() -> dict:
    return _executors
