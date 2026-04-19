"""Tests für blender.py — Code-Generierung und Injection-Abwehr."""
import ast
import sys
import types

import pytest

from conftest import load_skill


@pytest.fixture
def blender(monkeypatch):
    """Importiert blender.py mit einem Stub für blender_ctrl."""
    fake = types.ModuleType("blender_ctrl")
    fake.is_running = lambda: True
    fake.send_command = lambda t, p=None: {
        "status": "success",
        "result": {"output": "stub-ok"},
    }
    fake.get_scene_info = lambda: {
        "status": "success",
        "result": {"name": "Scene", "objects": [
            {"name": "Cube", "type": "MESH", "location": [0, 0, 0]},
        ]},
    }
    monkeypatch.setitem(sys.modules, "blender_ctrl", fake)
    return load_skill("blender")


# ── Code-Generierung ──

def test_create_cube_generates_valid_python(blender):
    code, _ = blender._build_create("cube", "MyCube", (1.0, 2.0, 3.0), 2.0, "rot")
    ast.parse(code)
    assert "primitive_cube_add" in code
    assert '"MyCube"' in code


def test_create_sphere_uses_radius(blender):
    code, _ = blender._build_create("sphere", None, (0, 0, 0), 1.5, None)
    ast.parse(code)
    assert "uv_sphere_add" in code
    assert "radius=1.5" in code


def test_all_primitives_generate_valid_python(blender):
    for prim in blender.PRIMITIVES:
        code, _ = blender._build_create(prim, None, (0, 0, 0), 1.0, None)
        ast.parse(code)


def test_delete_quotes_name_safely(blender):
    code = blender._build_delete("MyObj")
    ast.parse(code)
    assert '"MyObj"' in code


def test_transform_generates_valid_python(blender):
    code = blender._build_transform("X", (1, 2, 3), (0, 90, 0), (2, 2, 2))
    ast.parse(code)
    assert "math.radians(90)" in code


def test_set_color_hex_and_name(blender):
    assert blender._parse_color("#ff0000") == (1.0, 0.0, 0.0)
    assert blender._parse_color("rot") == (1.0, 0.0, 0.0)
    assert blender._parse_color("weiss") == blender._parse_color("weiß")
    assert blender._parse_color("nonsense") is None
    assert blender._parse_color(None) is None


# ── Injection-Abwehr ──

@pytest.mark.parametrize("bad_name", [
    "'; import os; os.system('rm'); #",
    'foo"); os.system("x',
    "foo\nimport os",
    "../../etc/passwd",
    "x" * 200,
    "",
    None,
    123,
])
def test_invalid_names_are_rejected(blender, bad_name):
    result = blender.execute("delete", name=bad_name)
    assert "Fehler" in result or "nötig" in result


def test_valid_name_pattern(blender):
    assert blender._valid_name("MyCube") is True
    assert blender._valid_name("cube-01") is True
    assert blender._valid_name("cube.001") is True
    assert blender._valid_name("cube_123") is True
    assert blender._valid_name("has space") is False
    assert blender._valid_name("has'quote") is False
    assert blender._valid_name("has\nnewline") is False


# ── Validierungs-Cases ──

def test_action_required(blender):
    assert "action fehlt" in blender.execute()


def test_unknown_primitive_rejected(blender):
    result = blender.execute(action="create", primitive="donut")
    assert "unbekannt" in result.lower()


def test_unknown_light_type_rejected(blender):
    result = blender.execute(action="add_light", light_type="LASER")
    assert "unbekannt" in result.lower() or "LASER" in result


def test_unknown_color_rejected_in_set_color(blender):
    result = blender.execute(action="set_color", name="Cube", color="rainbow")
    assert "unbekannt" in result.lower()


def test_huge_coords_are_clipped(blender):
    """Der Validator _num clippt Koordinaten auf +/- _MAX_COORD.
    Läuft durch execute() → _vec3 → _num."""
    x = blender._num(1e10, default=0, limit=blender._MAX_COORD)
    assert x == blender._MAX_COORD
    x = blender._num(-1e10, default=0, limit=blender._MAX_COORD)
    assert x == -blender._MAX_COORD
    # NaN → Default
    assert blender._num(float("nan"), default=0, limit=100) == 0.0
    # Nicht-Zahlen → Default
    assert blender._num("foo", default=7.5, limit=100) == 7.5


def test_zero_scale_falls_back_to_default(blender):
    """scale=[0,0,0] ist ein Aua — soll auf (1,1,1) zurückfallen."""
    result = blender.execute(action="transform", name="Cube",
                             scale=[0, 0, 0])
    # Kein Fehler, Objekt bleibt sichtbar
    assert "Transformiert" in result or "stub-ok" in result
