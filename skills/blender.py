"""Skill: Blender via MCP strukturiert steuern.

Das LLM schickt strukturierte Parameter. Dieser Skill baut daraus validierten
Python-Code und schickt ihn via blender_ctrl an den MCP-Server. Das LLM hat
KEINEN direkten Code-Execution-Zugriff mehr — alle User-Eingaben werden
geparst und escaped.
"""
import json
import re
import blender_ctrl as bl

# ---------------------------------------------------------------------------
# Validierung / Konstanten
# ---------------------------------------------------------------------------

# Blender-Objektnamen: Buchstaben, Zahlen, Unterstrich, Bindestrich, Punkt.
# Keine Anführungszeichen, keine Newlines → keine Code-Injection möglich.
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")

# Maximalwerte um absurde Eingaben abzufangen.
_MAX_COORD = 1000.0
_MAX_SCALE = 1000.0
_MAX_SIZE = 100.0

PRIMITIVES = {
    "cube":     "bpy.ops.mesh.primitive_cube_add",
    "sphere":   "bpy.ops.mesh.primitive_uv_sphere_add",
    "cylinder": "bpy.ops.mesh.primitive_cylinder_add",
    "cone":     "bpy.ops.mesh.primitive_cone_add",
    "plane":    "bpy.ops.mesh.primitive_plane_add",
    "torus":    "bpy.ops.mesh.primitive_torus_add",
    "monkey":   "bpy.ops.mesh.primitive_monkey_add",
}

LIGHT_TYPES = {"POINT", "SUN", "SPOT", "AREA"}

# Farbnamen → sRGB 0..1. Bewusst dieselben Namen wie im home_assistant-Skill,
# damit das LLM konsistent bleibt.
COLORS = {
    "rot":     (1.0, 0.0, 0.0),
    "grün":    (0.0, 1.0, 0.0),
    "gruen":   (0.0, 1.0, 0.0),
    "blau":    (0.0, 0.0, 1.0),
    "gelb":    (1.0, 1.0, 0.0),
    "lila":    (0.6, 0.0, 0.8),
    "pink":    (1.0, 0.4, 0.7),
    "orange":  (1.0, 0.5, 0.0),
    "cyan":    (0.0, 1.0, 1.0),
    "weiß":    (1.0, 1.0, 1.0),
    "weiss":   (1.0, 1.0, 1.0),
    "schwarz": (0.0, 0.0, 0.0),
    "grau":    (0.5, 0.5, 0.5),
    "braun":   (0.4, 0.2, 0.1),
}


def _valid_name(name: str) -> bool:
    return isinstance(name, str) and bool(_NAME_RE.match(name))


def _num(val, default=0.0, limit=_MAX_COORD) -> float:
    """Parst eine Zahl defensiv und clippt auf +/- limit."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return float(default)
    if f != f:  # NaN
        return float(default)
    if f > limit:
        return limit
    if f < -limit:
        return -limit
    return f


def _vec3(v, default=(0.0, 0.0, 0.0), limit=_MAX_COORD) -> tuple[float, float, float]:
    """Liefert ein (x,y,z)-Tupel aus Liste/Dict/None."""
    if v is None:
        return tuple(default)
    if isinstance(v, dict):
        return (_num(v.get("x"), default[0], limit),
                _num(v.get("y"), default[1], limit),
                _num(v.get("z"), default[2], limit))
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return (_num(v[0], default[0], limit),
                _num(v[1], default[1], limit),
                _num(v[2], default[2], limit))
    return tuple(default)


def _resolve_color(color) -> tuple[float, float, float] | None:
    """Akzeptiert Farbnamen (str) oder [r,g,b] (0..1)."""
    if color is None:
        return None
    if isinstance(color, str):
        return COLORS.get(color.lower().strip())
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        return (_num(color[0], 0, 1.0),
                _num(color[1], 0, 1.0),
                _num(color[2], 0, 1.0))
    return None


# ---------------------------------------------------------------------------
# Tool-Definition für das LLM
# ---------------------------------------------------------------------------

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "blender",
        "description": (
            "Steuert Blender strukturiert über ein MCP-Addon. "
            "Objekte erstellen, löschen, bewegen, einfärben, Lichter setzen, "
            "Kamera setzen, Szene abfragen. "
            "Kein freier Python-Code — nur die aufgelisteten Aktionen. "
            "Blender muss laufen und das MCP-Addon muss aktiv sein."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create", "delete", "clear_scene",
                        "transform", "set_color",
                        "add_light", "set_camera",
                        "get_scene",
                    ],
                    "description": (
                        "create = Primitive anlegen (cube/sphere/...); "
                        "delete = ein Objekt löschen; "
                        "clear_scene = alle Mesh-/Licht-/Kamera-Objekte löschen; "
                        "transform = Position/Rotation/Skalierung setzen; "
                        "set_color = Material mit Farbe setzen; "
                        "add_light = Lichtquelle hinzufügen; "
                        "set_camera = Kamera positionieren; "
                        "get_scene = Szene-Info zurückgeben."
                    ),
                },
                "primitive": {
                    "type": "string",
                    "enum": list(PRIMITIVES.keys()),
                    "description": "Art des Primitivs (nur bei action=create).",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Name des Objekts (Buchstaben/Zahlen/_/-/., max 60). "
                        "Bei create: Zielname; bei delete/transform/set_color: "
                        "existierender Objektname."
                    ),
                },
                "location": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[x, y, z] Position in Blender-Units. Default [0,0,0].",
                },
                "rotation": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[x, y, z] Rotation in Grad. Default [0,0,0].",
                },
                "scale": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[x, y, z] Skalierung. Default [1,1,1].",
                },
                "size": {
                    "type": "number",
                    "description": "Größe bei create (1.0 = Standard).",
                },
                "color": {
                    "type": "string",
                    "description": (
                        "Farbname (rot, blau, grün, ...) oder hex wie '#ff0000'. "
                        "Nur bei create und set_color."
                    ),
                },
                "light_type": {
                    "type": "string",
                    "enum": ["POINT", "SUN", "SPOT", "AREA"],
                    "description": "Lichtart bei action=add_light.",
                },
                "energy": {
                    "type": "number",
                    "description": "Helligkeit des Lichts (Watt). Default 100 für POINT, 5 für SUN.",
                },
                "target": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[x, y, z] Punkt auf den die Kamera schaut (nur set_camera).",
                },
            },
            "required": ["action"],
        },
    },
}


# ---------------------------------------------------------------------------
# Code-Bausteine (sicher zusammengesetzt, keine User-Strings im Python-Code)
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_str: str) -> tuple[float, float, float] | None:
    h = hex_str.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return None


def _parse_color(color) -> tuple[float, float, float] | None:
    if color is None:
        return None
    if isinstance(color, str) and color.startswith("#"):
        return _hex_to_rgb(color)
    return _resolve_color(color)


def _code_set_color(var: str, rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    return (
        f"_mat = bpy.data.materials.new(name={var}.name + '_mat')\n"
        f"_mat.use_nodes = True\n"
        f"_bsdf = _mat.node_tree.nodes.get('Principled BSDF')\n"
        f"if _bsdf:\n"
        f"    _bsdf.inputs['Base Color'].default_value = ({r:.4f}, {g:.4f}, {b:.4f}, 1.0)\n"
        f"{var}.data.materials.clear()\n"
        f"{var}.data.materials.append(_mat)\n"
    )


def _build_create(primitive: str, name: str | None, location, size: float,
                  color) -> tuple[str, str]:
    op = PRIMITIVES[primitive]
    x, y, z = location
    sz = max(0.001, min(size, _MAX_SIZE))

    # Je nach Primitive: radius vs size vs nichts. Einheitlich: location + size.
    if primitive in ("sphere", "cylinder", "cone", "torus"):
        # diese Ops nehmen 'radius' statt 'size'
        call = f"{op}(radius={sz}, location=({x}, {y}, {z}))"
    elif primitive == "plane":
        call = f"{op}(size={sz}, location=({x}, {y}, {z}))"
    elif primitive == "monkey":
        call = f"{op}(size={sz}, location=({x}, {y}, {z}))"
    else:  # cube
        call = f"{op}(size={sz}, location=({x}, {y}, {z}))"

    lines = [
        "import bpy",
        call,
        "_obj = bpy.context.active_object",
    ]
    if name and _valid_name(name):
        lines.append(f"_obj.name = {json.dumps(name)}")

    rgb = _parse_color(color)
    if rgb:
        lines.append(_code_set_color("_obj", rgb))

    lines.append("_result_name = _obj.name")
    return "\n".join(lines), primitive


def _build_delete(name: str) -> str:
    safe = json.dumps(name)
    return (
        "import bpy\n"
        f"_obj = bpy.data.objects.get({safe})\n"
        "if _obj is None:\n"
        f"    raise ValueError('Objekt nicht gefunden: ' + {safe})\n"
        "bpy.data.objects.remove(_obj, do_unlink=True)\n"
        f"_result_name = {safe}\n"
    )


def _build_clear_scene() -> str:
    return (
        "import bpy\n"
        "for _obj in list(bpy.data.objects):\n"
        "    if _obj.type in {'MESH', 'LIGHT', 'CAMERA', 'CURVE', 'EMPTY'}:\n"
        "        bpy.data.objects.remove(_obj, do_unlink=True)\n"
        "_result_name = 'scene_cleared'\n"
    )


def _build_transform(name: str, location, rotation, scale) -> str:
    safe = json.dumps(name)
    lx, ly, lz = location
    rx, ry, rz = rotation
    sx, sy, sz = scale
    return (
        "import bpy, math\n"
        f"_obj = bpy.data.objects.get({safe})\n"
        "if _obj is None:\n"
        f"    raise ValueError('Objekt nicht gefunden: ' + {safe})\n"
        f"_obj.location = ({lx}, {ly}, {lz})\n"
        f"_obj.rotation_euler = (math.radians({rx}), math.radians({ry}), math.radians({rz}))\n"
        f"_obj.scale = ({sx}, {sy}, {sz})\n"
        f"_result_name = {safe}\n"
    )


def _build_set_color(name: str, rgb: tuple[float, float, float]) -> str:
    safe = json.dumps(name)
    color_code = _code_set_color("_obj", rgb)
    return (
        "import bpy\n"
        f"_obj = bpy.data.objects.get({safe})\n"
        "if _obj is None:\n"
        f"    raise ValueError('Objekt nicht gefunden: ' + {safe})\n"
        "if not hasattr(_obj, 'data') or _obj.data is None:\n"
        f"    raise ValueError('Objekt hat kein Material-Ziel: ' + {safe})\n"
        + color_code +
        f"_result_name = {safe}\n"
    )


def _build_add_light(name: str | None, light_type: str, location, energy: float) -> str:
    lt = light_type.upper()
    if lt not in LIGHT_TYPES:
        lt = "POINT"
    x, y, z = location
    lines = [
        "import bpy",
        f"bpy.ops.object.light_add(type={json.dumps(lt)}, location=({x}, {y}, {z}))",
        "_obj = bpy.context.active_object",
        f"_obj.data.energy = {energy}",
    ]
    if name and _valid_name(name):
        lines.append(f"_obj.name = {json.dumps(name)}")
    lines.append("_result_name = _obj.name")
    return "\n".join(lines)


def _build_set_camera(location, target) -> str:
    lx, ly, lz = location
    tx, ty, tz = target
    return (
        "import bpy\n"
        "from mathutils import Vector\n"
        "_cam = None\n"
        "for _o in bpy.data.objects:\n"
        "    if _o.type == 'CAMERA':\n"
        "        _cam = _o\n"
        "        break\n"
        "if _cam is None:\n"
        "    _cam_data = bpy.data.cameras.new('Camera')\n"
        "    _cam = bpy.data.objects.new('Camera', _cam_data)\n"
        "    bpy.context.scene.collection.objects.link(_cam)\n"
        "bpy.context.scene.camera = _cam\n"
        f"_cam.location = ({lx}, {ly}, {lz})\n"
        f"_target = Vector(({tx}, {ty}, {tz}))\n"
        "_direction = _target - _cam.location\n"
        "_cam.rotation_mode = 'QUATERNION'\n"
        "_cam.rotation_quaternion = _direction.to_track_quat('-Z', 'Y')\n"
        "_cam.rotation_mode = 'XYZ'\n"
        "_result_name = _cam.name\n"
    )


# ---------------------------------------------------------------------------
# Execute — vom Skill-Loader aufgerufen
# ---------------------------------------------------------------------------

def _run_code(code: str) -> str:
    """Schickt Code zu Blender und formatiert das Ergebnis als String."""
    # Echo des erzeugten Objektnamens auf stdout, damit der MCP-Server
    # den String im 'result' zurückreicht.
    full = code + "\nprint(_result_name)\n"
    resp = bl.send_command("execute_code", {"code": full})
    if resp.get("status") == "success":
        res = resp.get("result")
        # Ahuja-Addon gibt typischerweise {"executed": True, "output": "<stdout>"}
        if isinstance(res, dict):
            out = (res.get("output") or "").strip()
            if out:
                return out
            return "ok"
        if isinstance(res, str) and res.strip():
            return res.strip()
        return "ok"
    return f"Fehler: {resp.get('message', 'unbekannt')}"


def execute(action: str = None, **kwargs) -> str:
    if not action:
        return "Fehler: action fehlt."
    if not bl.is_running():
        return "Blender läuft nicht oder das MCP-Addon ist nicht aktiv."

    try:
        if action == "create":
            prim = (kwargs.get("primitive") or "cube").lower()
            if prim not in PRIMITIVES:
                return f"Unbekanntes Primitiv: {prim}. Erlaubt: {', '.join(PRIMITIVES)}"
            name = kwargs.get("name")
            if name is not None and not _valid_name(name):
                return "Ungültiger Name (nur Buchstaben/Zahlen/_/-/., max 60 Zeichen)."
            loc = _vec3(kwargs.get("location"))
            size = _num(kwargs.get("size", 2.0), default=2.0, limit=_MAX_SIZE)
            code, _ = _build_create(prim, name, loc, size, kwargs.get("color"))
            out = _run_code(code)
            return f"{prim.capitalize()} erstellt: {out}"

        if action == "delete":
            name = kwargs.get("name")
            if not name or not _valid_name(name):
                return "Fehler: Gültiger Objektname nötig."
            out = _run_code(_build_delete(name))
            return f"Gelöscht: {out}"

        if action == "clear_scene":
            out = _run_code(_build_clear_scene())
            return "Szene geleert." if "cleared" in out else out

        if action == "transform":
            name = kwargs.get("name")
            if not name or not _valid_name(name):
                return "Fehler: Gültiger Objektname nötig."
            loc = _vec3(kwargs.get("location"))
            rot = _vec3(kwargs.get("rotation"), limit=3600.0)
            scl_default = (1.0, 1.0, 1.0)
            scl = _vec3(kwargs.get("scale"), default=scl_default, limit=_MAX_SCALE)
            # Falls scale komplett fehlt, würde _vec3 (0,0,0) liefern für list=None → ok via default.
            # Aber bei scale=[0,0,0] aus LLM wäre das tödlich; filtern:
            if scl == (0.0, 0.0, 0.0):
                scl = scl_default
            out = _run_code(_build_transform(name, loc, rot, scl))
            return f"Transformiert: {out}"

        if action == "set_color":
            name = kwargs.get("name")
            if not name or not _valid_name(name):
                return "Fehler: Gültiger Objektname nötig."
            rgb = _parse_color(kwargs.get("color"))
            if not rgb:
                return f"Farbe unbekannt. Erlaubt: {', '.join(COLORS)} oder #rrggbb."
            out = _run_code(_build_set_color(name, rgb))
            return f"Farbe gesetzt: {out}"

        if action == "add_light":
            name = kwargs.get("name")
            if name is not None and not _valid_name(name):
                return "Ungültiger Name."
            lt = (kwargs.get("light_type") or "POINT").upper()
            if lt not in LIGHT_TYPES:
                return f"Unbekannter Lichttyp: {lt}. Erlaubt: {', '.join(LIGHT_TYPES)}"
            loc = _vec3(kwargs.get("location"), default=(0.0, 0.0, 5.0))
            default_energy = 5.0 if lt == "SUN" else 100.0
            energy = _num(kwargs.get("energy", default_energy),
                          default=default_energy, limit=100000.0)
            out = _run_code(_build_add_light(name, lt, loc, energy))
            return f"Licht ({lt}) erstellt: {out}"

        if action == "set_camera":
            loc = _vec3(kwargs.get("location"), default=(7.0, -7.0, 5.0))
            target = _vec3(kwargs.get("target"), default=(0.0, 0.0, 0.0))
            out = _run_code(_build_set_camera(loc, target))
            return f"Kamera gesetzt: {out}"

        if action == "get_scene":
            resp = bl.get_scene_info()
            if resp.get("status") != "success":
                return f"Fehler: {resp.get('message', 'unbekannt')}"
            info = resp.get("result", {})
            # Kompakte, LLM-freundliche Darstellung — nicht das ganze JSON.
            if not isinstance(info, dict):
                return json.dumps(info, ensure_ascii=False)[:1500]
            objs = info.get("objects") or []
            lines = [f"Szene: {info.get('name', '?')} ({len(objs)} Objekte)"]
            for o in objs[:30]:
                nm = o.get("name", "?")
                tp = o.get("type", "?")
                loc = o.get("location") or [0, 0, 0]
                lines.append(f"- {nm} [{tp}] @ ({loc[0]:.2f}, {loc[1]:.2f}, {loc[2]:.2f})")
            if len(objs) > 30:
                lines.append(f"... und {len(objs) - 30} weitere")
            return "\n".join(lines)

        return f"Unbekannte action: {action}"

    except Exception as e:
        return f"Fehler in blender-Skill: {type(e).__name__}: {e}"
