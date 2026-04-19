"""Lädt alle Skills aus ~/chanti/skills/ mit Hot-Reload Support.

Thread-safe: Ein Lock schützt die globalen Strukturen, damit mehrere
gleichzeitige Chat-Requests (die alle reload_if_changed aufrufen)
sich nicht ins Gehege kommen.
"""
import importlib.util
import sys
import threading
import logging
from pathlib import Path

logger = logging.getLogger("chanti")

SKILLS_DIR = Path(__file__).parent / "skills"

# Globaler State — nur mit _lock anfassen.
_tools: list[dict] = []
_executors: dict = {}
# Pfad → (mtime, tool_name). tool_name brauchen wir, um beim Löschen
# der Datei den passenden Eintrag in _tools/_executors zu entfernen.
_files: dict[str, tuple[float, str]] = {}
_load_errors: dict[str, str] = {}
_lock = threading.Lock()


def _load_skill(skill_file: Path) -> tuple[str, dict, callable] | None:
    """Lädt einen einzelnen Skill. Gibt (name, tool_def, execute_fn) zurück
    oder None bei Fehler. Ein Fehler wird in _load_errors vermerkt."""
    path_str = str(skill_file)
    try:
        module_name = f"skills.{skill_file.stem}"

        # Altes Modul aus sys.modules entfernen für sauberes Reload
        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, skill_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Spec konnte nicht erstellt werden für {skill_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "TOOL_DEFINITION") or not hasattr(module, "execute"):
            raise AttributeError("TOOL_DEFINITION oder execute fehlt")

        tool_def = module.TOOL_DEFINITION
        if not isinstance(tool_def, dict) or "function" not in tool_def:
            raise ValueError("TOOL_DEFINITION hat falsches Format")
        name = tool_def["function"].get("name")
        if not name or not isinstance(name, str):
            raise ValueError("TOOL_DEFINITION.function.name fehlt")
        if not callable(module.execute):
            raise TypeError("execute ist nicht aufrufbar")

        _load_errors.pop(path_str, None)
        return name, tool_def, module.execute

    except Exception as e:
        _load_errors[path_str] = f"{type(e).__name__}: {e}"
        logger.error(f"Skill {skill_file.name} Fehler: {e}")
        return None


def _remove_by_name(name: str) -> bool:
    """Entfernt Tool und Executor mit gegebenem Namen. Caller hält _lock."""
    before = len(_tools)
    _tools[:] = [t for t in _tools if t["function"]["name"] != name]
    _executors.pop(name, None)
    return len(_tools) != before


def load_skills() -> tuple[list[dict], dict]:
    """Lädt alle Skills beim Start. Thread-safe."""
    with _lock:
        _tools.clear()
        _executors.clear()
        _files.clear()
        _load_errors.clear()

        if not SKILLS_DIR.exists():
            logger.warning(f"Skills-Verzeichnis nicht gefunden: {SKILLS_DIR}")
            return list(_tools), dict(_executors)

        for skill_file in sorted(SKILLS_DIR.glob("*.py")):
            if skill_file.name.startswith("_"):
                continue
            result = _load_skill(skill_file)
            if result is None:
                continue
            name, tool_def, execute_fn = result
            _tools.append(tool_def)
            _executors[name] = execute_fn
            try:
                mtime = skill_file.stat().st_mtime
            except OSError:
                mtime = 0.0
            _files[str(skill_file)] = (mtime, name)
            logger.info(f"Skill geladen: {name}")

        logger.info(f"{len(_tools)} Skills geladen: {list(_executors.keys())}")
        if _load_errors:
            logger.warning(f"Skill-Ladefehler: {list(_load_errors.keys())}")
        return list(_tools), dict(_executors)


def reload_if_changed() -> bool:
    """Prüft ob neue, geänderte oder gelöschte Skills vorhanden sind.
    Thread-safe. Gibt True zurück wenn etwas geändert wurde."""
    if not SKILLS_DIR.exists():
        return False

    with _lock:
        changed = False

        # 1) Aktuelle Skill-Dateien einsammeln
        current_files = {
            str(f): f for f in sorted(SKILLS_DIR.glob("*.py"))
            if not f.name.startswith("_")
        }

        # 2) Gelöschte Skills entfernen — per Name, den wir beim Laden gemerkt haben
        removed_paths = set(_files) - set(current_files)
        for path_str in removed_paths:
            _mtime, name = _files.pop(path_str)
            if _remove_by_name(name):
                logger.info(f"Skill entfernt: {name} (Datei gelöscht)")
                changed = True
            _load_errors.pop(path_str, None)

        # 3) Neue/geänderte Skills laden
        for path_str, skill_file in current_files.items():
            try:
                current_mtime = skill_file.stat().st_mtime
            except OSError:
                continue

            known = _files.get(path_str)
            if known and known[0] == current_mtime:
                continue

            result = _load_skill(skill_file)
            if result is None:
                # Fehler beim Laden — mtime trotzdem tracken, damit wir nicht
                # bei jedem Request denselben defekten Skill erneut zu laden
                # versuchen. Name bleibt auf bekanntem Wert (falls vorher geladen).
                old_name = known[1] if known else ""
                _files[path_str] = (current_mtime, old_name)
                continue

            name, tool_def, execute_fn = result
            # Wenn der Skill unter anderem Namen vorher geladen war, altes Tool raus.
            if known and known[1] and known[1] != name:
                _remove_by_name(known[1])
            _remove_by_name(name)  # Duplikate vermeiden
            _tools.append(tool_def)
            _executors[name] = execute_fn
            _files[path_str] = (current_mtime, name)
            logger.info(f"Skill neu geladen: {name}")
            changed = True

        return changed


def get_tools() -> list[dict]:
    """Gibt eine Kopie der Tool-Definitionen zurück (thread-safe)."""
    with _lock:
        return list(_tools)


def get_executors() -> dict:
    """Gibt eine Kopie des Executor-Dicts zurück (thread-safe)."""
    with _lock:
        return dict(_executors)


def get_load_errors() -> dict:
    """Diagnose: welche Skill-Dateien gerade Fehler werfen."""
    with _lock:
        return dict(_load_errors)
