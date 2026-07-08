"""
Transpiler: Graph -> build123d Python source.

Primary path is *algebra mode*: a flat sequence of assignments
`__out_N = <code_template>` in topological order, with placeholders replaced by
upstream variables (inputs) and formatted parameter values.

Group nodes (BuildPart/BuildSketch) emit nested `with ... as __ctx_N:` blocks;
their children are emitted with the *builder* template, indented inside the
block.

The generated module exposes `__result__` (the shape to preview) and
`__panels__` (a dict of Panel-node values for inspection).
"""

from __future__ import annotations

import re

from . import catalog
from .graph import Graph
from .toposort import toposort

_PLACEHOLDER = re.compile(r"\{(\w+)(![rs])?\}")

# Invisible sentinels used only by the instrumented transpile (run(emit_map=True)).
# A parameter value that lands in the generated source is wrapped as
#   <A><span-id><SEP><literal><B>
# so that after the whole program is built we can scan the FINAL text, measure
# each value's (row, col) — immune to indentation / group nesting / fan-out
# wrapping — strip the sentinels, and emit a param<->code source map. These are
# NUL-class control chars that never occur in real Python source.
_SP_A = "\x00"
_SP_SEP = "\x1f"
_SP_B = "\x01"
_SP_RE = re.compile(_SP_A + r"(\d+)" + _SP_SEP + r"([^" + _SP_B + r"]*)" + _SP_B)

# A `#@param` annotation on a CodeBlock declaration line, e.g.
#   teeth = 12   #@param int min=6 max=40
#   mode  = "a"  #@param select=a,b,c
_CB_PARAM = re.compile(r"#@param\b(.*)$")


def _parse_annot(annot: str) -> dict:
    """Parse the `#@param ...` tail into {type?, min?, max?, step?, options?}."""
    spec: dict = {}
    for tok in annot.strip().split():
        if "=" in tok:
            key, _, val = tok.partition("=")
            key, val = key.strip(), val.strip()
            if key in ("min", "max", "step"):
                try:
                    spec[key] = float(val)
                except ValueError:
                    pass
            elif key in ("select", "options"):
                opts = [o for o in val.split(",") if o]
                if opts:
                    spec["options"] = opts
                    spec.setdefault("type", "select")
        elif tok in ("int", "float", "bool", "str", "select"):
            spec["type"] = tok
    return spec


def _parse_cb_line(line: str) -> dict | None:
    """A single `name = <literal>  #@param ...` declaration -> a param schema,
    or None. Uses `ast.literal_eval` on the RHS literal only — never `exec`."""
    import ast
    m = _CB_PARAM.search(line)
    if not m:
        return None
    code_part = line[: m.start()]
    if "=" not in code_part:
        return None
    name, _, rhs = code_part.partition("=")
    name, rhs = name.strip(), rhs.strip()
    if not name.isidentifier():
        return None
    try:
        default = ast.literal_eval(rhs)
    except Exception:
        return None
    spec = _parse_annot(m.group(1))
    kind = spec.get("type")
    if kind is None:
        if isinstance(default, bool):
            kind = "bool"
        elif isinstance(default, int):
            kind = "int"
        elif isinstance(default, float):
            kind = "float"
        else:
            kind = "str"
    return {"name": name, "type": kind, "default": default,
            "min": spec.get("min"), "max": spec.get("max"),
            "step": spec.get("step"), "options": spec.get("options")}


def parse_codeblock_params(code: str) -> list[dict]:
    """Every `#@param` declaration in a CodeBlock's source, in order."""
    out, seen = [], set()
    for line in (code or "").splitlines():
        decl = _parse_cb_line(line)
        if decl and decl["name"] not in seen:
            seen.add(decl["name"])
            out.append(decl)
    return out


def _cb_literal(value, kind: str) -> str:
    """Render a CodeBlock param override value as a Python source literal."""
    try:
        if kind == "bool":
            return "True" if value else "False"
        if kind == "int":
            return str(int(value))
        if kind == "float":
            return repr(float(value))
    except (TypeError, ValueError):
        pass
    return repr(value)


def _annot(node) -> str:
    """Trailing marker so a runtime traceback line maps back to its node.

    Parsed by the executor (see `_NODE_MARK`); kept as a Python comment so it
    never affects execution.
    """
    return f"  # @node:{node.id} ({node.type})"

PREAMBLE = """\
import math
import os
import random

from build123d import *

# --- runtime helpers (injected by the transpiler) ---
__panels__ = {}
__previews__ = {}
__errors__ = {}


def _out(_path):
    \"\"\"Resolve an Export node's path into the project's exports/ folder.
    The worker runs with cwd = the project dir, so exports land in
    projects/<name>/exports/. The path is sandboxed to a basename (any
    directory components / traversal are stripped) so an Export node can
    never write outside its own project. Feeds the global file library.\"\"\"
    _name = os.path.basename(str(_path).strip()) or "output"
    os.makedirs("exports", exist_ok=True)
    return os.path.join("exports", _name)


def _panel(_id, _value, _text="", _mode="friendly"):
    \"\"\"Dual-mode Panel. When a value is wired it DISPLAYS it (pass-through);
    when nothing is wired it is a data SOURCE that parses its own text into
    value(s) on the wire. Multi-line text -> a list (fans out downstream).\"\"\"
    if _value is not None:
        __panels__[_id] = _value
        return _value
    _parsed = _panel_parse(_text, _mode)
    __panels__[_id] = _parsed
    return _parsed


def _probe(_id, _value):
    \"\"\"Typed container / legend: record the value for the inspector (Panels tab)
    and pass it through UNCHANGED. One per wire type — colours the wire and lets
    you label/inspect what flows, without altering the data. (Cast/transform role
    comes later — see PLAN_DATA_PROTOCOL.md.)\"\"\"
    __panels__[_id] = _value
    return _value


def _panel_one(_line, _mode):
    \"\"\"Parse a single line into a value per the chosen syntax.\"\"\"
    import json as _json
    _s = _line.strip()
    if _mode == "build123d":
        return eval(_s, globals())              # build123d expression (arbitrary)
    if _mode == "json":
        return _json.loads(_s)
    # friendly: JSON scalar/array first, else "x, y, z" numbers -> Vector, else str
    try:
        return _json.loads(_s)
    except Exception:
        pass
    import re as _re
    _parts = [p for p in _re.split(r"[,\\s]+", _s.strip("()[] ")) if p]
    try:
        _nums = [float(p) for p in _parts]
    except ValueError:
        return _s
    if len(_nums) == 3:
        return Vector(*_nums)
    if len(_nums) == 2:
        return Vector(_nums[0], _nums[1], 0)
    if len(_nums) == 1:
        return _nums[0]
    return _nums


def _panel_parse(_text, _mode="friendly"):
    \"\"\"Text -> value(s). One item per non-empty line; >1 line -> a list.\"\"\"
    if not _text or not str(_text).strip():
        return None
    if _mode == "json":
        import json as _json
        try:
            return _json.loads(_text)           # whole block as one JSON document
        except Exception:
            pass
    _lines = [ln for ln in str(_text).splitlines() if ln.strip()]
    if not _lines:
        return None
    _vals = []
    for _ln in _lines:
        try:
            _vals.append(_panel_one(_ln, _mode))
        except Exception as _e:
            _vals.append("⚠ " + str(_e))
    return _vals[0] if len(_vals) == 1 else _vals


def _select_subshapes(_shape, _kind, _indices, _sigs):
    \"\"\"Resolve a picked sub-shape set against a (possibly recomputed) shape.

    Matching is by nearest anchor point (edge midpoint / face centre / vertex),
    so a selection survives upstream parameter tweaks even when OCC re-orders
    the sub-shapes. Falls back to raw indices when no signatures were stored.\"\"\"
    if _shape is None:
        return ShapeList([])
    _get = {"edge": getattr(_shape, "edges", None),
            "face": getattr(_shape, "faces", None),
            "vertex": getattr(_shape, "vertices", None)}.get(_kind)
    subs = list(_get()) if _get else []

    def _anchor(s):
        try:
            if _kind == "edge":
                v = s @ 0.5
            elif _kind == "face":
                v = s.center()
            else:
                v = s
            return (v.X, v.Y, v.Z)
        except Exception:
            return None

    anchors = [_anchor(s) for s in subs]
    chosen, used = [], set()
    for want in (_sigs or []):
        wx, wy, wz = want[0], want[1], want[2]
        best, best_d = None, None
        for j, a in enumerate(anchors):
            if j in used or a is None:
                continue
            d = (a[0] - wx) ** 2 + (a[1] - wy) ** 2 + (a[2] - wz) ** 2
            if best_d is None or d < best_d:
                best_d, best = d, j
        if best is not None:
            used.add(best)
            chosen.append(subs[best])
    if not _sigs:
        for idx in (_indices or []):
            if 0 <= idx < len(subs) and idx not in used:
                used.add(idx)
                chosen.append(subs[idx])
    return ShapeList(chosen)


def _origin_points(_o):
    \"\"\"Normalise a vector / vertex / tuple / list / ShapeList into [Vector, ...].\"\"\"
    if _o is None:
        return []
    items = list(_o) if isinstance(_o, (list, tuple)) else [_o]
    out = []
    for it in items:
        if it is None:
            continue
        if hasattr(it, "X") and hasattr(it, "Y") and hasattr(it, "Z"):
            out.append(Vector(it.X, it.Y, it.Z))
        elif isinstance(it, (list, tuple)) and len(it) >= 3:
            out.append(Vector(it[0], it[1], it[2]))
        elif hasattr(it, "center"):
            c = it.center()
            out.append(Vector(c.X, c.Y, c.Z))
    return out


def _deconstruct(_s):
    \"\"\"Explode anything into a flat list of points (Vectors) — the one coherent
    point extractor: a solid/surface/curve -> ALL its vertices; a plane/frame ->
    its origin; a selection / list -> each item's points; a vertex/point/tuple ->
    itself. Output is a list, so downstream fans out.\"\"\"
    if _s is None:
        return []
    out = []
    for it in (_s if _is_seq(_s) else [_s]):
        if it is None:
            continue
        if hasattr(it, "X") and hasattr(it, "Y") and hasattr(it, "Z"):
            out.append(Vector(it.X, it.Y, it.Z))           # already a point / vertex
            continue
        if isinstance(it, (list, tuple)) and len(it) >= 3 and not hasattr(it, "vertices"):
            out.append(Vector(it[0], it[1], it[2]))        # a raw (x, y, z)
            continue
        if hasattr(it, "origin") and not hasattr(it, "vertices"):
            o = it.origin                                  # a plane / frame -> origin
            out.append(Vector(o.X, o.Y, o.Z))
            continue
        verts = None
        try:
            verts = list(it.vertices())                    # a shape -> its vertices
        except Exception:
            verts = None
        if verts:
            for v in verts:
                c = v.center() if hasattr(v, "center") else v
                out.append(Vector(c.X, c.Y, c.Z))
        elif hasattr(it, "center"):
            c = it.center()
            out.append(Vector(c.X, c.Y, c.Z))
    return out


def _explode(_s, _kind):
    \"\"\"Explode shape(s) into their sub-shapes of a given kind: 'edge' -> the
    constituent Edges (curves), 'face' -> the constituent Faces (surfaces). The
    companion to _deconstruct (points). Accepts a single shape or a list /
    selection; returns a flat list, so downstream fans out (one run per edge /
    face). A bare edge/face is passed through unchanged.\"\"\"
    if _s is None:
        return []
    out = []
    for it in (_s if _is_seq(_s) else [_s]):
        if it is None:
            continue
        subs = None
        try:
            subs = list(getattr(it, _kind + "s")())        # .edges() / .faces()
        except Exception:
            subs = None
        if subs:
            out.extend(subs)
    return out


def _at(_shape, _origin):
    \"\"\"Place _shape at _origin. One point -> a moved copy; many points -> a
    Compound with a copy at each (so a Select Vertex can seed N primitives).\"\"\"
    if _shape is None or _origin is None:
        return _shape
    pts = _origin_points(_origin)
    if not pts:
        return _shape
    if len(pts) == 1:
        return _shape.moved(Pos(pts[0].X, pts[0].Y, pts[0].Z))
    return Compound(children=[_shape.moved(Pos(p.X, p.Y, p.Z)) for p in pts])


def _pushpull(_part, _faces, _amount):
    \"\"\"Push/pull selected faces along their normal: positive grows a boss,
    negative carves a pocket.\"\"\"
    if _part is None or not _faces or not _amount:
        return _part
    prism = extrude(_faces, amount=_amount)
    return (_part + prism) if _amount > 0 else (_part - prism)


def _shell(_part, _thickness):
    \"\"\"Give a part a wall of thickness _thickness.
    - Solid / Compound-of-solids: hollow it out, leaving the top (+Z) face open.
    - Shell / Face (an open surface, e.g. a non-solid Loft): thicken the surface
      into a solid wall — there is no enclosed volume to hollow, so 'openings'
      does not apply (offset() rejects a bare Shell, which used to raise
      'Unsupported type ... Shell').\"\"\"
    if _part is None or not _thickness:
        return _part
    solids = list(_part.solids()) if isinstance(_part, Compound) else \\
             ([_part] if isinstance(_part, Solid) else [])
    if solids:
        top = _part.faces().sort_by(Axis.Z)[-1]
        return offset(_part, amount=-_thickness, openings=top)
    # open surface: thicken it into a solid wall
    surf = _part if isinstance(_part, (Shell, Face)) else _face(_part)
    return Solid.thicken(surf, _thickness)


def _bbox_plane(_shape, _plane, _t=0.5):
    \"\"\"A reusable Plane, parallel to the chosen base plane (XY/XZ/YZ), centred
    on _shape's bounding box and slid along its own normal to the _t position
    in [0,1] (0 = min side, 0.5 = centre, 1 = max side). This is a first-class
    Plane on the wire — feed it to Section, BuildSketch, or anything that takes
    a plane. Works for any geometry, since the position comes from its box.\"\"\"
    pl = _plane if isinstance(_plane, Plane) else Plane(_plane)
    if _shape is None:
        return pl
    try:
        bb = _shape.bounding_box()
        n = pl.z_dir
        lo, hi = Vector(bb.min), Vector(bb.max)
        c = (lo + hi) * 0.5
        target = lo.dot(n) + (hi.dot(n) - lo.dot(n)) * float(_t)
        pl = Plane(origin=(c.X, c.Y, c.Z), x_dir=pl.x_dir, z_dir=pl.z_dir)
        pl = pl.offset(target - pl.origin.dot(n))
    except Exception:
        pass  # degenerate / empty shape: fall back to the base plane
    return pl


def _section(_shape, _plane=None):
    \"\"\"Planar cross-section of _shape, cut by the Plane wired into _plane
    (e.g. from a Bounding Plane node, which carries both orientation and
    position). Defaults to Plane.XY through the global origin when nothing is
    wired.\"\"\"
    if _shape is None:
        return None
    pl = _plane if isinstance(_plane, Plane) else Plane.XY
    return section(_shape, section_by=pl)


def _rotate(_obj, _axis, _angle):
    \"\"\"Rotate any spatial object — Shape OR Plane — by _angle degrees about a
    global axis. Uses Location algebra (Rot * obj) so it is polymorphic: a plane
    rotates just like a solid (build123d Planes have no .rotate()).\"\"\"
    if _obj is None:
        return None
    d = _axis.direction
    return Rot(d.X * _angle, d.Y * _angle, d.Z * _angle) * _obj


def _scale(_shape, _factor=1.0, _x=1.0, _y=1.0, _z=1.0):
    \"\"\"Scale a shape. `factor` is the uniform multiplier; `x/y/z` add per-axis
    factors on top (all 1.0 -> uniform). by=(factor*x, factor*y, factor*z).\"\"\"
    if _shape is None:
        return None
    f = float(_factor)
    return scale(_shape, by=(f * float(_x), f * float(_y), f * float(_z)))


def _mirror(_shape, _plane, _copy=False):
    \"\"\"Mirror a shape across a plane. With `copy`, keep the original too so the
    result is symmetric (original + reflection).\"\"\"
    if _shape is None:
        return None
    m = mirror(_shape, about=_plane)
    if not _copy:
        return m
    try:
        return _shape + m
    except Exception:
        return Compound(children=[_shape, m])


def _al(_centered, _n=3):
    \"\"\"An align tuple for a primitive: CENTER on every axis when centred, else MIN
    (corner / base at the origin).\"\"\"
    a = Align.CENTER if _centered else Align.MIN
    return tuple([a] * int(_n))


_FANOUT_MAX = 2000  # guard: an absurd list (e.g. a runaway Range count) must
                    # surface as a per-node error, not freeze/crash the worker.


def _is_seq(_v):
    \"\"\"A value the engine should iterate over for fan-out. Lists/tuples and
    build123d ShapeLists count; a single Vector/Shape/Plane does NOT (a Vector
    is itself iterable, so test for the list-like containers explicitly).\"\"\"
    return isinstance(_v, (list, tuple, ShapeList))


def _fanout(_fn, _kw):
    \"\"\"Grasshopper-style data matching. _kw maps each item-access input name to
    its value. Any value that is a sequence makes the node run once per item;
    scalars are broadcast to every run; shorter lists reuse their last item
    (longest-list match). Returns a single value when nothing was a list, else a
    list of results.\"\"\"
    cols = {k: list(v) for k, v in _kw.items() if _is_seq(v)}
    if not cols:
        return _fn(**_kw)
    n = max((len(v) for v in cols.values()), default=0)
    if n > _FANOUT_MAX:
        raise ValueError(
            f"fan-out of {n} copies exceeds the {_FANOUT_MAX} limit "
            f"(check the list feeding this node, e.g. a Range count)")
    out = []
    for i in range(n):
        call = {}
        for k, v in _kw.items():
            if k in cols:
                seq = cols[k]
                call[k] = seq[i] if i < len(seq) else (seq[-1] if seq else None)
            else:
                call[k] = v
        out.append(_fn(**call))
    return out


def _flatten(_x):
    out = []
    for it in (_x or []):
        out.extend(_flatten(it)) if _is_seq(it) else out.append(it)
    return out


def _slice(_lst, _start, _stop, _step):
    _lst = list(_lst or [])
    stop = None if _stop == 0 else _stop
    return _lst[_start:stop:(_step or 1)]


def _sort(_items, _by="X"):
    \"\"\"Sort a list. build123d shapes use ShapeList ordering (by position along
    an axis, or by a metric); points sort by component; anything else by value.\"\"\"
    items = list(_items or [])
    if not items:
        return items
    head = items[0]
    if hasattr(head, "wrapped") or hasattr(head, "volume") or hasattr(head, "center"):
        axis = {"X": Axis.X, "Y": Axis.Y, "Z": Axis.Z}.get(_by)
        metric = {"length": SortBy.LENGTH, "area": SortBy.AREA,
                  "volume": SortBy.VOLUME, "radius": SortBy.RADIUS}.get(_by)
        try:
            if axis is not None:
                return ShapeList(items).sort_by(axis)
            if metric is not None:
                return ShapeList(items).sort_by(metric)
        except Exception:
            pass
    if hasattr(head, "X") and hasattr(head, "Y") and hasattr(head, "Z"):
        comp = {"X": "X", "Y": "Y", "Z": "Z"}.get(_by, "X")
        return sorted(items, key=lambda v: getattr(v, comp))
    try:
        return sorted(items)
    except Exception:
        return items


def _move(_shape, _offset, _x, _y, _z):
    \"\"\"Translate a shape/plane. By the wired `offset` vector when present
    (item-access: a list of vectors scatters the shape to each), else by the
    x/y/z widgets.\"\"\"
    if _shape is None:
        return None
    if _offset is None:
        v = Vector(_x, _y, _z)
    elif hasattr(_offset, "X"):
        v = _offset
    else:
        v = Vector(*list(_offset)[:3])
    return Pos(v.X, v.Y, v.Z) * _shape


def _pt(_v):
    \"\"\"Coerce one vector/vertex/tuple to a Vector (first item if given many);
    None passes through. Lets curve nodes accept any point-like input.\"\"\"
    pts = _origin_points(_v)
    return pts[0] if pts else None


def _curve_points(_items):
    \"\"\"Flatten curve point inputs into [Vector, ...]. Accepts several wired
    points AND/OR list(s) of points, so Polyline/Spline take either shape.\"\"\"
    return _origin_points(_flatten(_items))


def _curve_draw(_points, _mode="polyline", _closed=False, _plane=None):
    \"\"\"Build a curve from local (u, v) points authored with the on-canvas
    ✎ Draw tool (Curve on Plane node), then re-seat it onto `_plane` (Plane.XY/
    XZ/YZ by default, or any wired Plane — dynamic frames included). Mirrors
    _to_plane: the curve is built flat in local XY, then `plane * curve`.\"\"\"
    pts = [(float(p[0]), float(p[1]), 0.0)
           for p in (_points or []) if p and len(p) >= 2]
    if len(pts) < 2:
        return None
    if _mode == "spline":
        crv = Spline(*pts, periodic=bool(_closed))
    else:
        crv = Polyline(*pts, close=bool(_closed))
    pl = _plane if isinstance(_plane, Plane) else Plane.XY
    return pl * crv


def _outline(_s):
    \"\"\"The boundary of a 2D primitive as a closed curve (Wire). A filled
    sketch/face is reduced to its outer wire(s) so primitives read as curves;
    feed a Surface (Make Face) node to fill it back into a face. Anything that is
    not face-like (already a curve) passes straight through.\"\"\"
    if _s is None:
        return None
    try:
        ws = list(_s.wires()) if hasattr(_s, "wires") else []
    except Exception:
        ws = []
    if len(ws) == 1:
        return ws[0]
    if ws:
        return ShapeList(ws)
    return _s


def _face(_s):
    \"\"\"Coerce a shape to a face/sketch. A closed curve (Wire/Edge) is filled with
    make_face; anything already face-like passes through. Used where an op needs a
    face but may receive a curve primitive.\"\"\"
    if _s is None:
        return None
    try:
        if hasattr(_s, "faces") and len(_s.faces()) > 0:
            return _s
    except Exception:
        pass
    try:
        return make_face(_s)
    except Exception:
        return _s


def _classify(_it):
    \"\"\"Coarse geometric kind of a runtime value: 'solid' | 'surface' | 'curve' |
    'point' | 'other'. Drives the container filter/transform gate (_gate).\"\"\"
    if _it is None:
        return "other"
    if hasattr(_it, "X") and hasattr(_it, "Y") and hasattr(_it, "Z"):
        return "point"
    if isinstance(_it, (list, tuple)) and len(_it) >= 3 and not hasattr(_it, "vertices"):
        return "point"
    if hasattr(_it, "origin") and hasattr(_it, "z_dir"):
        return "plane"                       # a Plane / frame (no faces/edges)
    def _cnt(name):
        try:
            return len(getattr(_it, name)())
        except Exception:
            return 0
    if _cnt("solids") > 0:
        return "solid"
    if _cnt("faces") > 0:
        return "surface"
    if _cnt("edges") > 0 or _cnt("wires") > 0:
        return "curve"
    if _cnt("vertices") > 0:
        return "point"
    return "other"


def _is_planar(_f):
    \"\"\"True if a face is planar (the Surface transform keeps only these).\"\"\"
    try:
        return "PLANE" in str(_f.geom_type).upper()
    except Exception:
        return True


def _polyline_through(_pts):
    \"\"\"Join >=2 points into one open polyline (the curve transform of points).\"\"\"
    vs = _deconstruct(_pts)
    if len(vs) < 2:
        return None
    try:
        return Polyline(*[(v.X, v.Y, v.Z) for v in vs])
    except Exception:
        return None


def _gate(_id, _v, _kind, _mode):
    \"\"\"Container filter/transform gate — the typed transformer. `_kind` is the
    container's OWN type ('surface' | 'curve'); `_mode` is 'filter' | 'transform'.
      - filter:    keep only inputs already of that kind.
      - transform: also coerce/extract everything compatible into it —
          surface: fill closed curves (_face); a solid's PLANAR faces.
          curve:   a surface's outer outline; a solid's edges (wireframe);
                   >=2 points joined into one polyline.
    Always returns a flat LIST (so downstream fans out) and records it for the
    Panels tab (_probe). Single source of truth for the container transformer.\"\"\"
    items = [it for it in _flatten([_v]) if it is not None]
    out = []
    if _kind == "surface":
        for it in items:
            k = _classify(it)
            if k == "surface":
                try:
                    out.extend(it.faces())
                except Exception:
                    out.append(it)
            elif _mode == "transform" and k == "curve":
                f = _face(it)
                if f is not None:
                    out.append(f)
            elif _mode == "transform" and k == "solid":
                try:
                    out.extend([f for f in it.faces() if _is_planar(f)])
                except Exception:
                    pass
    elif _kind == "curve":
        pts = [it for it in items if _classify(it) == "point"]
        if _mode == "transform" and len(pts) >= 2:
            pl = _polyline_through(pts)
            if pl is not None:
                out.append(pl)
        for it in items:
            k = _classify(it)
            if k == "curve":
                try:
                    ws = list(it.wires())
                    out.extend(ws if ws else [it])
                except Exception:
                    out.append(it)
            elif _mode == "transform" and k == "surface":
                try:
                    for f in it.faces():
                        w = f.outer_wire() if hasattr(f, "outer_wire") else None
                        out.append(w if w is not None else f)
                except Exception:
                    out.append(it)
            elif _mode == "transform" and k == "solid":
                try:
                    out.extend(it.edges())
                except Exception:
                    pass
    elif _kind == "point":
        # filter: keep only points; transform: explode anything into its points
        # (vertices of shapes, a plane's origin, …) — same rule as Deconstruct.
        for it in items:
            if _mode == "transform" or _classify(it) == "point":
                out.extend(_deconstruct(it))
    elif _kind == "solid":
        # filter: keep solids (a compound stays whole); transform: explode a
        # compound into its individual solids.
        for it in items:
            if _classify(it) != "solid":
                continue
            if _mode == "transform":
                try:
                    out.extend(it.solids())
                except Exception:
                    out.append(it)
            else:
                out.append(it)
    elif _kind == "plane":
        # filter: keep planes/frames; transform: read the Plane of each PLANAR
        # face of a surface or solid.
        for it in items:
            k = _classify(it)
            if k == "plane":
                out.append(it)
            elif _mode == "transform" and k in ("surface", "solid"):
                try:
                    out.extend([Plane(f) for f in it.faces() if _is_planar(f)])
                except Exception:
                    pass
    elif _kind == "selection":
        # A selection is ONE ShapeList consumed whole (Fillet/Chamfer/…), so this
        # returns a single ShapeList, not a fan-out list. filter: keep loose
        # sub-shapes (edges/faces/vertices); transform: also pull a solid's
        # faces/edges/vertices in. Assemble a pickable selection programmatically.
        sel = []
        for it in items:
            k = _classify(it)
            if k in ("curve", "surface", "point"):
                sel.append(it)
            elif _mode == "transform" and k == "solid":
                try:
                    sel.extend(it.faces()); sel.extend(it.edges()); sel.extend(it.vertices())
                except Exception:
                    pass
        res = ShapeList(sel)
        _probe(_id, res)
        return res
    _probe(_id, out)
    return out


def _as_curve(_c):
    \"\"\"Coerce an input to a 1D curve (Edge/Wire) exposing location_at. Passes a
    curve through; otherwise takes the first edge of a shape.\"\"\"
    if _c is None:
        return None
    if hasattr(_c, "location_at"):
        return _c
    try:
        es = list(_c.edges())
        return es[0] if es else None
    except Exception:
        return None


def _curve_frames(_curve, _count=8):
    \"\"\"N evenly spaced frames (Planes) along a curve; each frame's local Z axis
    is the curve tangent, so a 2D profile seated on it (ToPlane) sits perpendicular
    to the curve — ready to Loft. Returns a list of Planes.\"\"\"
    c = _as_curve(_curve)
    if c is None:
        return []
    n = max(int(_count), 1)
    out = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.5
        try:
            out.append(Plane(c.location_at(t)))
        except Exception:
            pass
    return out


def _eval_frame(_curve, _t=0.0):
    \"\"\"The frame (Plane) at parameter t in [0,1] on a curve (Z = tangent).\"\"\"
    c = _as_curve(_curve)
    if c is None:
        return None
    try:
        return Plane(c.location_at(min(max(float(_t), 0.0), 1.0)))
    except Exception:
        return None


def _plane_origin(_p):
    \"\"\"The origin point (Vector) of a Plane/Location; a point passes through.\"\"\"
    if _p is None:
        return None
    if hasattr(_p, "origin"):
        o = _p.origin
        return Vector(o.X, o.Y, o.Z)
    return _pt(_p)


def _curve_endpoints(_curve):
    \"\"\"[start, end] points of a curve as a 2-item list of Vectors.\"\"\"
    c = _as_curve(_curve)
    if c is None:
        return []
    try:
        return [Vector(c @ 0.0), Vector(c @ 1.0)]
    except Exception:
        return []


def _curve_length(_curve):
    c = _as_curve(_curve)
    try:
        return float(c.length) if c is not None else 0.0
    except Exception:
        return 0.0


def _divide_domain(_dom, _count=10):
    \"\"\"N values evenly spanning a domain [min, max], endpoints inclusive.\"\"\"
    d = list(_dom) if _is_seq(_dom) else [0.0, 1.0]
    a = float(d[0]) if d else 0.0
    b = float(d[-1]) if len(d) > 1 else a + 1.0
    n = max(int(_count), 1)
    if n == 1:
        return [(a + b) / 2.0]
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _bounds(_lst):
    \"\"\"The [min, max] domain spanning a flat list of numbers.\"\"\"
    nums = [float(x) for x in _flatten(_lst) if isinstance(x, (int, float))]
    return [min(nums), max(nums)] if nums else [0.0, 1.0]


def _remap(_v, _src, _tgt, _smin, _smax, _tmin, _tmax):
    \"\"\"Linearly remap _v from a source range to a target range. A [min,max]
    domain wired into _src/_tgt overrides the scalar fallbacks.\"\"\"
    a, b = (_src[0], _src[1]) if _is_seq(_src) and len(_src) >= 2 else (_smin, _smax)
    c, d = (_tgt[0], _tgt[1]) if _is_seq(_tgt) and len(_tgt) >= 2 else (_tmin, _tmax)
    a, b, c, d = float(a), float(b), float(c), float(d)
    if b == a:
        return c
    return c + (float(_v) - a) / (b - a) * (d - c)


def _section_wire(_s):
    \"\"\"The outline Wire of a loft/extrude section (a face/sketch -> its outer wire;
    a curve passes through). Used for open-surface (un-capped) lofts.\"\"\"
    if _s is None:
        return None
    if isinstance(_s, Wire):
        return _s
    try:
        ws = list(_s.wires())
        if ws:
            return ws[0]
    except Exception:
        pass
    try:
        es = list(_s.edges())
        if es:
            return Wire(es)
    except Exception:
        pass
    return None


def _wrap_shape(_s):
    \"\"\"Wrap a raw OCP TopoDS_Shape into the matching build123d class.\"\"\"
    if _s is None:
        return None
    from OCP.TopAbs import TopAbs_ShapeEnum as _T
    cls = {_T.TopAbs_SOLID: Solid, _T.TopAbs_SHELL: Shell, _T.TopAbs_FACE: Face,
           _T.TopAbs_COMPOUND: Compound, _T.TopAbs_COMPSOLID: Compound,
           _T.TopAbs_WIRE: Wire, _T.TopAbs_EDGE: Edge}.get(_s.ShapeType())
    return cls(_s) if cls else None


def _wire_normal(_w):
    \"\"\"Unit normal of a planar wire (via its filled face); falls back to +Z.\"\"\"
    try:
        n = make_face(_w).faces()[0].normal_at()
        return Vector(n.X, n.Y, n.Z)
    except Exception:
        return Vector(0, 0, 1)


def _loft(_sections, _ruled=False, _solid=True, _smoothing=False,
          _continuity="C2", _parametrization="chord", _max_degree=8,
          _start=None, _end=None):
    \"\"\"Loft (skin) through an ordered list of sections, exposing the full OCC
    ThruSections control set. Sections may be several wired sketches/faces/curves
    AND/OR a single list (e.g. ToPlane over Divide Curve). Settings:
      ruled           - straight skin between sections (vs a smooth surface).
      solid           - cap the ends into a solid (vs an open shell of the outlines).
      smoothing       - use the approximating algorithm instead of exact
                        interpolation (smooth path); pair with continuity/max_degree.
      continuity      - C0|C1|C2|G1|G2 of the lofted surface (smoothing).
      parametrization - uniform|chord|centripetal: how sections are spaced in the
                        smooth interpolation (changes the bulge between sections).
      max_degree      - cap on the result surface's B-spline U-degree.
      _start / _end   - optional points: the loft starts/ends at a tip (a cone cap).
    Falls back to build123d's high-level loft if the builder fails.\"\"\"
    secs = [s for s in _flatten([_sections]) if s is not None]
    n_caps = (1 if _start is not None else 0) + (1 if _end is not None else 0)
    if len(secs) < 1 or len(secs) + n_caps < 2:
        return None
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.GeomAbs import GeomAbs_Shape as _G
    from OCP.Approx import Approx_ParametrizationType as _P
    _cont = {"C0": _G.GeomAbs_C0, "C1": _G.GeomAbs_C1, "C2": _G.GeomAbs_C2,
             "G1": _G.GeomAbs_G1, "G2": _G.GeomAbs_G2}.get(_continuity, _G.GeomAbs_C2)
    _par = {"uniform": _P.Approx_IsoParametric, "chord": _P.Approx_ChordLength,
            "centripetal": _P.Approx_Centripetal}.get(_parametrization, _P.Approx_ChordLength)
    def _vtx(_v):
        return Vertex(_v.X, _v.Y, _v.Z) if hasattr(_v, "X") else Vertex(*_v)
    try:
        b = BRepOffsetAPI_ThruSections(bool(_solid), bool(_ruled), 1e-6)
        b.SetSmoothing(bool(_smoothing))
        b.SetContinuity(_cont)
        b.SetParType(_par)
        if _max_degree:
            b.SetMaxDegree(int(_max_degree))
        b.CheckCompatibility(True)          # reparametrize wires -> no smooth-loft twist
        if _start is not None:
            b.AddVertex(_vtx(_start).wrapped)
        n = 0
        for s in secs:
            w = _section_wire(s)
            if w is not None:
                b.AddWire(w.wrapped)
                n += 1
        if _end is not None:
            b.AddVertex(_vtx(_end).wrapped)
        if n < 1:
            return None
        b.Build()
        out = _wrap_shape(b.Shape())
        if out is not None:
            return out
    except Exception:
        pass
    if _solid:                              # fallback: high-level loft of the faces
        faces = [f for f in (_face(s) for s in secs) if f is not None]
        if len(faces) >= 2:
            return loft(faces, ruled=bool(_ruled))
    return None


def _extrude(_profile, _amount, _taper=0.0, _both=False, _solid=True):
    \"\"\"Extrude a 2D profile along its normal. With `solid` (default) the profile is
    filled into a face and the result is a solid; with `solid` off the profile's
    outline is extruded into an open surface (a wall / ribbon; `taper` ignored).\"\"\"
    if _profile is None:
        return None
    if _solid:
        prof = _face(_profile)
        return extrude(prof, amount=_amount, taper=_taper, both=_both) if prof is not None else None
    w = _section_wire(_profile)
    if w is None:
        return None
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    nrm = _wire_normal(w)
    a = float(_amount)
    src = w
    if _both:
        src = w.moved(Pos(-nrm.X * a, -nrm.Y * a, -nrm.Z * a))
        a *= 2.0
    return _wrap_shape(BRepPrimAPI_MakePrism(src.wrapped, gp_Vec(nrm.X * a, nrm.Y * a, nrm.Z * a)).Shape())


def _revolve(_profile, _axis, _angle=360, _solid=True):
    \"\"\"Revolve a 2D profile around an in-plane axis. With `solid` (default) the
    profile is filled into a face for a solid of revolution; with `solid` off the
    outline is revolved into an open surface.\"\"\"
    if _profile is None:
        return None
    if _solid:
        prof = _face(_profile)
        return revolve(prof, axis=_axis, revolution_arc=_angle) if prof is not None else None
    w = _section_wire(_profile)
    if w is None:
        return None
    import math
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    ax = _axis.wrapped if hasattr(_axis, "wrapped") else _axis
    return _wrap_shape(BRepPrimAPI_MakeRevol(w.wrapped, ax, math.radians(float(_angle))).Shape())


def _sweep(_section, _path, _frenet=False, _solid=True):
    \"\"\"Sweep a profile along a path curve. The profile is seated on the path's
    start frame (perpendicular to the curve) so a flat XY sketch sweeps cleanly.
    With `solid` (default) the profile is a face and the sweep is a solid; with
    `solid` off the outline is swept into an open surface (a tube wall).\"\"\"
    if _section is None or _path is None:
        return None
    p = _as_curve(_path)
    if _solid:
        sec = _to_plane(_face(_section), _eval_frame(p, 0.0))
        return sweep(sec, path=p, is_frenet=bool(_frenet))
    sec = _to_plane(_section_wire(_section), _eval_frame(p, 0.0))
    if sec is None:
        return None
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    spine = p if isinstance(p, Wire) else Wire([p])
    return _wrap_shape(BRepOffsetAPI_MakePipe(spine.wrapped, sec.wrapped).Shape())


def _to_plane(_shape, _plane):
    \"\"\"Re-seat a 2D profile onto a plane/frame: its local XY comes to lie in the
    plane (Plane * shape). Used to place loft/sweep sections on curve frames.\"\"\"
    if _shape is None or _plane is None:
        return _shape
    pl = _plane if isinstance(_plane, Plane) else Plane(_plane)
    return pl * _shape


def _domain2d(_region, _w, _h, _pts=None):
    \"\"\"(x0, y0, x1, y1) of a 2D domain: a region face's bounding box if given,
    else the points' extent, else a 0..w x 0..h box.\"\"\"
    if _region is not None:
        try:
            bb = _region.bounding_box()
            return bb.min.X, bb.min.Y, bb.max.X, bb.max.Y
        except Exception:
            pass
    if _pts is not None and len(_pts):
        import numpy as np
        P = np.asarray(_pts, dtype=float)
        return P[:, 0].min(), P[:, 1].min(), P[:, 0].max(), P[:, 1].max()
    return 0.0, 0.0, float(_w), float(_h)


def _populate(_count=40, _seed=1, _width=100.0, _height=100.0, _region=None):
    \"\"\"Scatter `count` random points (z=0), deterministic per `seed`, inside the
    `region` rectangle's bounds if wired, else a 0..width x 0..height box.\"\"\"
    import numpy as np
    x0, y0, x1, y1 = _domain2d(_region, _width, _height)
    rng = np.random.RandomState(int(_seed))
    xs = rng.uniform(x0, x1, int(_count)); ys = rng.uniform(y0, y1, int(_count))
    return [Vector(float(x), float(y), 0.0) for x, y in zip(xs, ys)]


def _voronoi2d(_points, _boundary=None, _scale=0.9):
    \"\"\"Polygonal Voronoi cells from a point set, clipped to the `boundary`
    rectangle (its bounding box). `scale` shrinks each cell toward its centre to
    leave a frame between cells (1.0 = cells touch / share edges). Sites are
    mirrored across the boundary so the kept cells are finite and clipped.\"\"\"
    import numpy as np
    from scipy.spatial import Voronoi
    pts = _origin_points(_points)
    if len(pts) < 2:
        return ShapeList([])
    P = np.array([(p.X, p.Y) for p in pts], dtype=float)
    x0, y0, x1, y1 = _domain2d(_boundary, 100.0, 100.0, P)
    pad = []
    for (x, y) in P:
        pad += [(2 * x0 - x, y), (2 * x1 - x, y), (x, 2 * y0 - y), (x, 2 * y1 - y)]
    vor = Voronoi(np.vstack([P, pad]))
    s = float(_scale)
    faces = []
    for i in range(len(P)):                        # original sites only
        reg = vor.regions[vor.point_region[i]]
        if not reg or -1 in reg:
            continue
        poly = vor.vertices[reg]
        cx, cy = poly[:, 0].mean(), poly[:, 1].mean()
        vs = [Vector(cx + s * (x - cx), cy + s * (y - cy), 0) for x, y in poly]
        try:
            faces.append(make_face(Polyline(*vs, close=True)))
        except Exception:
            pass
    return ShapeList(faces)


def _map_to_surface(_shapes, _surface, _boundary=None, _width=100.0, _height=100.0):
    \"\"\"Lay flat shapes onto a surface: each shape's centroid maps from the 2D
    domain (the `boundary` rectangle, or 0..width x 0..height) to the surface's
    UV, and the shape is re-seated on the tangent plane there. Extrude the result
    (along the normal) to cut/boss radially.\"\"\"
    if _surface is None or not _shapes:
        return ShapeList([])
    faces = list(_surface.faces()) if hasattr(_surface, "faces") else [_surface]
    if not faces:
        return ShapeList([])
    face = max(faces, key=lambda f: getattr(f, "area", 0.0))
    x0, y0, x1, y1 = _domain2d(_boundary, _width, _height)
    dx = (x1 - x0) or 1.0; dy = (y1 - y0) or 1.0
    items = _shapes if isinstance(_shapes, (list, tuple, ShapeList)) else [_shapes]
    out = []
    for sh in items:
        if sh is None:
            continue
        try:
            c = sh.center()
            u = min(max((c.X - x0) / dx, 0.0), 1.0)
            v = min(max((c.Y - y0) / dy, 0.0), 1.0)
            P = face.position_at(u, v)
            tpl = Plane(origin=P, z_dir=face.normal_at(P))
            out.append(tpl.location * (Pos(-c.X, -c.Y, 0) * sh))
        except Exception:
            pass
    return ShapeList(out)


def _divide_surface(_shape, _u=6, _v=6):
    \"\"\"Sample a u x v grid of points on a surface. Uses the largest face of the
    input shape and build123d's parametric position_at(u, v) (u, v in 0..1).\"\"\"
    if _shape is None:
        return []
    faces = list(_shape.faces()) if hasattr(_shape, "faces") else [_shape]
    if not faces:
        return []
    face = max(faces, key=lambda f: getattr(f, "area", 0.0))
    nu, nv = max(int(_u), 1), max(int(_v), 1)
    out = []
    for i in range(nu):
        for j in range(nv):
            u = i / (nu - 1) if nu > 1 else 0.5
            v = j / (nv - 1) if nv > 1 else 0.5
            try:
                out.append(face.position_at(u, v))
            except Exception:
                pass
    return out


def _gm_pts(_pts):
    \"\"\"Normalise GraphMapper control points to [(x, y, hi, ho), ...] where hi/ho
    are absolute in-/out-handle coords (or None). Accepts plain [x, y] pairs or
    {p, hi, ho} anchor dicts; sorts by x (a function curve is single-valued).\"\"\"
    _out = []
    for _a in (_pts or []):
        if isinstance(_a, dict):
            _p, _hi, _ho = _a.get("p"), _a.get("hi"), _a.get("ho")
        else:
            _p, _hi, _ho = _a, None, None
        if not _p:
            continue
        _out.append((float(_p[0]), float(_p[1]),
                     tuple(_hi) if _hi else None, tuple(_ho) if _ho else None))
    _out.sort(key=lambda _r: _r[0])
    return _out


def _gm_bezier(_x, _pts):
    \"\"\"y at x along a chained cubic Bezier (anchors _pts = (x, y, hi, ho)). Finds
    the segment bracketing x, then solves X(s)=x by bisection (the curve is
    x-monotonic for a function) and returns Y(s). Missing handles fall back to the
    1/3 and 2/3 chord points (a straight segment).\"\"\"
    for _i in range(len(_pts) - 1):
        _x0, _y0, _, _ho = _pts[_i]
        _x1, _y1, _hi, _ = _pts[_i + 1]
        if not (_x0 <= _x <= _x1):
            continue
        _c0 = _ho if _ho else (_x0 + (_x1 - _x0) / 3.0, _y0 + (_y1 - _y0) / 3.0)
        _c1 = _hi if _hi else (_x0 + 2 * (_x1 - _x0) / 3.0, _y0 + 2 * (_y1 - _y0) / 3.0)
        _lo, _hs = 0.0, 1.0
        for _ in range(48):
            _s = (_lo + _hs) / 2.0
            _mt = 1 - _s
            _xs = (_mt**3 * _x0 + 3 * _mt**2 * _s * _c0[0]
                   + 3 * _mt * _s**2 * _c1[0] + _s**3 * _x1)
            if _xs < _x:
                _lo = _s
            else:
                _hs = _s
        _s = (_lo + _hs) / 2.0
        _mt = 1 - _s
        return (_mt**3 * _y0 + 3 * _mt**2 * _s * _c0[1]
                + 3 * _mt * _s**2 * _c1[1] + _s**3 * _y1)
    return _pts[-1][1] if _x >= _pts[-1][0] else _pts[0][1]


def _graphmap(_t, _pts, _mode="smooth"):
    \"\"\"Evaluate an editable function curve f(x)->y at parameter(s) _t along x.
    _pts are control points ([x, y] or {p, hi, ho}); modes: linear | smooth
    (monotone cubic, no overshoot) | bezier (per-segment cubic with tangent
    handles). Scalar in -> scalar out, list in -> list out (so it fans out).\"\"\"
    import numpy as _np
    _p = _gm_pts(_pts)
    _scalar = not isinstance(_t, (list, tuple))
    _ts = _np.atleast_1d(_np.asarray(_t, dtype=float))
    if len(_p) < 2:
        _ys = _np.full_like(_ts, _p[0][1] if _p else 0.0)
        return float(_ys[0]) if _scalar else _ys.tolist()
    _xs = _np.array([_q[0] for _q in _p])
    _ys = _np.array([_q[1] for _q in _p])
    _tc = _np.clip(_ts, _xs[0], _xs[-1])
    if _mode == "linear":
        _o = _np.interp(_tc, _xs, _ys)
    elif _mode == "bezier":
        _o = _np.array([_gm_bezier(float(_v), _p) for _v in _tc])
    else:  # smooth: monotone cubic interpolation (no overshoot)
        try:
            from scipy.interpolate import PchipInterpolator as _P
            _o = _P(_xs, _ys)(_tc)
        except Exception:
            _o = _np.interp(_tc, _xs, _ys)
    return float(_o[0]) if _scalar else _o.tolist()


def _array_polar(_shape, _count=6, _angle=360.0, _axis=None):
    \"\"\"Repeat a shape around a global axis -> list. A full 360 spaces `count`
    items evenly (no overlap at the seam); a partial angle includes both
    endpoints. Rotation is about the axis through the origin — Move the shape
    off-axis first for the classic bolt-circle.\"\"\"
    if _shape is None:
        return None
    _n = max(1, int(_count))
    _a = float(_angle)
    _ax = _axis if _axis is not None else Axis.Z
    if abs(_a) >= 360.0:
        _step = _a / _n
    else:
        _step = _a / (_n - 1) if _n > 1 else 0.0
    _d = _ax.direction
    return [Rot(_d.X * _step * _i, _d.Y * _step * _i, _d.Z * _step * _i) * _shape
            for _i in range(_n)]


def _align(_shape, _ref=None, _target=None):
    \"\"\"Translate a shape so `ref` (default: its bounding-box centre) lands on
    `target` (default: the global origin). Unwired = centre at origin.\"\"\"
    if _shape is None:
        return None
    _r = _pt(_ref)
    if _r is None:
        _bb = _shape.bounding_box()
        _r = (_bb.min + _bb.max) / 2
    _t = _pt(_target)
    if _t is None:
        _t = Vector(0, 0, 0)
    _v = _t - _r
    return Pos(_v.X, _v.Y, _v.Z) * _shape


def _split(_shape, _plane=None, _keep=None):
    \"\"\"Split a shape by a plane, keeping the requested side(s). Defaults to the
    XY plane / Keep.TOP.\"\"\"
    if _shape is None:
        return None
    _pl = _plane if isinstance(_plane, Plane) else Plane.XY
    return split(_shape, bisect_by=_pl, keep=_keep if _keep is not None else Keep.TOP)


def _plane_normal(_origin=None, _normal=None):
    \"\"\"A Plane from an origin point and a normal (z) direction.\"\"\"
    _o = _pt(_origin)
    _n = _pt(_normal)
    return Plane(origin=(_o if _o is not None else Vector(0, 0, 0)),
                 z_dir=(_n if _n is not None else Vector(0, 0, 1)))


def _plane_offset(_plane, _dist=0.0):
    \"\"\"Slide a plane along its own normal by `dist`.\"\"\"
    _pl = _plane if isinstance(_plane, Plane) else Plane.XY
    return _pl.offset(float(_dist))


def _star(_outer=10.0, _inner=5.0, _points=5):
    \"\"\"A star outline: 2*points vertices alternating between the outer and
    inner radius, first spike pointing +Y.\"\"\"
    _n = max(2, int(_points))
    _pts = []
    for _i in range(2 * _n):
        _r = float(_outer) if _i % 2 == 0 else float(_inner)
        _a = math.pi * _i / _n + math.pi / 2
        _pts.append((_r * math.cos(_a), _r * math.sin(_a)))
    return _outline(Polygon(*_pts))


def _repeat(_v, _n=2):
    \"\"\"Repeat a value (or a whole list, concatenated) n times -> list.\"\"\"
    _items = list(_v) if _is_seq(_v) else [_v]
    return _items * max(0, int(_n))


def _shift(_lst, _offset=1, _wrap=True):
    \"\"\"Cyclically shift a list by `offset` (wrap), or drop from one end.\"\"\"
    _items = list(_lst) if _is_seq(_lst) else [_lst]
    if not _items:
        return _items
    _o = int(_offset)
    if _wrap:
        _o %= len(_items)
        return _items[_o:] + _items[:_o]
    return _items[_o:] if _o >= 0 else _items[:_o]


def _dispatch(_lst, _pattern=True, _invert=False):
    \"\"\"Keep the items where the (cycled) boolean pattern is True — the GH
    Dispatch/Cull idiom. `invert` keeps the False side instead.\"\"\"
    _items = list(_lst) if _is_seq(_lst) else [_lst]
    _pat = [bool(_p) for _p in (_pattern if _is_seq(_pattern) else [_pattern])]
    if not _pat:
        _pat = [True]
    return [_x for _i, _x in enumerate(_items)
            if _pat[_i % len(_pat)] != bool(_invert)]


def _unique(_lst):
    \"\"\"Drop duplicate items, keeping first occurrence (hashables by value,
    shapes by identity).\"\"\"
    _seen, _out = set(), []
    for _x in (list(_lst) if _is_seq(_lst) else [_lst]):
        _k = _x if isinstance(_x, (int, float, str, bool, tuple)) else id(_x)
        if _k not in _seen:
            _seen.add(_k)
            _out.append(_x)
    return _out


def _randlist(_count=10, _lo=0.0, _hi=1.0, _seed=1):
    \"\"\"`count` uniform random numbers in [lo, hi], deterministic per seed.\"\"\"
    _rng = random.Random(int(_seed))
    return [_rng.uniform(float(_lo), float(_hi)) for _ in range(max(0, int(_count)))]


def _export_3mf(_shape, _path):
    \"\"\"Write the shape to a 3MF file (via build123d's Mesher).\"\"\"
    _m = Mesher()
    _m.add_shape(_shape)
    _m.write(_path)
    return _shape


def _export_2d(_shape, _path, _fmt="svg"):
    \"\"\"Write a 2D shape (sketch/curve, or a Section of a solid) to SVG or DXF.
    The drawing is the XY projection — seat the geometry with ToPlane/Section
    first.\"\"\"
    _exp = ExportSVG() if _fmt == "svg" else ExportDXF()
    _exp.add_shape(_shape)
    _exp.write(_path)
    return _shape
"""

# Output wire types that yield a drawable preview (mesh for solids/sketches,
# polylines for curves, dots for points). Mirrors the mesh_extractor render paths.
_PREVIEWABLE = {catalog.WIRE_SOLID, catalog.WIRE_SURFACE, catalog.WIRE_CURVE,
                catalog.WIRE_VECTOR}

# Node types whose output is always a Python list at runtime. Feeding one of
# these into an item-access input makes the consumer fan out. (A fanned node is
# added to this set dynamically as the graph is walked, so lists propagate.)
_LIST_PRODUCERS = {
    "ArrayLinear", "ArrayPolar", "ListCreate", "ListRange", "ListSeries", "ListRepeat",
    "ListSlice", "ListReverse", "ListSort", "ListFlatten", "Concat",
    "ListShift", "ListFilter", "ListUnique", "Random",
    "Voronoi2D", "DivideSurface", "PopulateGeometry", "MapToSurface",
    "DivideCurve", "CurveEndpoints", "Deconstruct",
    "DeconstructEdges", "DeconstructFaces",
    "Surface", "Curve", "Point",   # gated containers always emit a list (filter/transform)
    "Geometry", "Plane",           # (Selection returns one ShapeList, not a fan-out list)
    "Series", "DivideDomain",
    "Input",    # source-mode multi-line text -> a list
    "Display",  # pass-through preserves list-ness
}


def format_param(pdef: catalog.Param, value) -> str:
    """Render a parameter value as a Python source literal/expression."""
    if value is None:
        value = pdef.default
    if pdef.type == "select":
        return pdef.code_map.get(value, repr(value))
    if pdef.raw:
        return str(value)
    if pdef.type == "bool":
        return "True" if value else "False"
    if pdef.type == "str":
        return repr(value)
    if pdef.type == "int":
        return str(int(value))
    return repr(float(value))


def _substitute(template: str, values: dict) -> str:
    """Replace {name} / {name!r} placeholders using `values`. Unknown -> None."""
    def repl(m: re.Match) -> str:
        key, conv = m.group(1), m.group(2)
        val = values.get(key, "None")
        if conv == "!r":
            return repr(val)
        return str(val)

    return _PLACEHOLDER.sub(repl, template)


class Transpiler:
    def __init__(self, graph: Graph):
        self.graph = graph
        self.var_of: dict[str, str] = {}
        self._counter = 0
        # Source-map instrumentation (off unless run(emit_map=True)).
        self._emit_map = False
        self._span_meta: dict[int, dict] = {}
        # node ids whose output is a list at runtime (static producers + any node
        # that gets fanned out). Filled as nodes are emitted in topological order.
        self._produces_list: set[str] = set()
        # A node is an "extremity" unless its geometry is passed onward to a node
        # that *continues* the geometry (feeds a geometry-class input AND itself
        # produces geometry). Feeding a Panel/inspector (data input) or an Export
        # (no output) doesn't count — those nodes stay visible by default.
        by_id = {n.id: n for n in graph.nodes}
        self._consumed: set[str] = set()
        for c in graph.connections:
            dst = by_id.get(c.to_node)
            if dst is None:
                continue
            ddef = catalog.get(dst.type)
            inp = ddef.input(c.to_socket)
            if inp is None or inp.wire_type not in _PREVIEWABLE:
                continue  # fed into a data/inspector input
            if ddef.outputs and ddef.outputs[0].wire_type in _PREVIEWABLE:
                self._consumed.add(c.from_node)  # geometry genuinely continues

    def _previewed(self, node, ndef: catalog.NodeDef) -> bool:
        """Whether this node draws in the viewport. Per-node eye:
        True/False force it; None (auto) shows only terminal geometry nodes
        (those whose output isn't consumed downstream)."""
        previewable = (node.type == "CodeBlock" or
                       (ndef.outputs and ndef.outputs[0].wire_type in _PREVIEWABLE))
        if not previewable:
            return False
        eye = getattr(node, "preview", None)
        if eye is True:
            return True
        if eye is False:
            return False
        return node.id not in self._consumed  # auto: terminal only

    def _new_var(self, node_id: str, prefix: str = "__out_") -> str:
        self._counter += 1
        var = f"{prefix}{self._counter}"
        self.var_of[node_id] = var
        return var

    def _input_values(self, node_id: str, ndef: catalog.NodeDef) -> dict[str, str]:
        """Map each input socket -> the source variable expression."""
        feeds = self.graph.inputs_of(node_id)
        out: dict[str, str] = {}
        for sock in ndef.inputs:
            srcs = feeds.get(sock.name, [])
            vars_ = [self.var_of.get(fn, "None") for (fn, _fs) in srcs]
            if sock.multiple:
                out[sock.name] = ", ".join(vars_)
            else:
                out[sock.name] = vars_[0] if vars_ else "None"
        return out

    def _param_values(self, node, ndef: catalog.NodeDef) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in ndef.params:
            formatted = format_param(p, node.params.get(p.name, p.default))
            out[p.name] = self._wrap_param(node, p, formatted)
        return out

    def _register_span(self, meta: dict, literal: str) -> str:
        """Wrap a literal in source-map sentinels and remember its metadata."""
        sid = len(self._span_meta)
        self._span_meta[sid] = meta
        return f"{_SP_A}{sid}{_SP_SEP}{literal}{_SP_B}"

    def _wrap_param(self, node, pdef: catalog.Param, formatted: str) -> str:
        """Tag a built-in param's emitted literal so it becomes an editable span.
        Skipped for raw/code params (whole-expression, not a single literal) and
        when not building a map. A wired params-as-input overwrites this value
        before substitution, so its sentinel never reaches the final text — which
        is exactly why wired params get no editable span (the literal isn't there)."""
        if not self._emit_map or pdef.raw or pdef.type not in (
                "float", "int", "bool", "str", "select"):
            return formatted
        return self._register_span({
            "node_id": node.id, "node_type": node.type, "param": pdef.name,
            "kind": pdef.type, "label": pdef.label or pdef.name,
            "value": node.params.get(pdef.name, pdef.default),
            "min": pdef.min, "max": pdef.max, "step": pdef.step,
            "options": list(pdef.options) if pdef.options else None,
        }, formatted)

    def _guard(self, lines: list[str], body: list[str], node) -> None:
        """Wrap a node's statement(s) in try/except so one node's runtime error
        is recorded in __errors__ and doesn't abort the rest of the workflow."""
        lines.append("try:")
        for bl in body:
            if bl:
                lines.append("    " + bl)
        lines.append("except Exception as _e:")
        var = self.var_of.get(node.id)
        if var:
            lines.append(f"    {var} = None")
        lines.append(f"    __errors__[{node.id!r}] = f\"{{type(_e).__name__}}: {{_e}}\"")

    def _emit_bypass(self, node, lines: list[str]) -> None:
        """Bypassed node: skip its operation and pass an upstream value straight
        through to its output (Grasshopper/ComfyUI semantics). Prefer an input
        whose wire type matches the first output; else the first connected input.
        A node with no output simply disappears from the program."""
        ndef = catalog.get(node.type)
        if not ndef.outputs:
            return
        var = self._new_var(node.id)
        out_wt = ndef.outputs[0].wire_type
        inputs = self._input_values(node.id, ndef)
        chosen = None
        for sock in ndef.inputs:
            v = inputs.get(sock.name, "None")
            if not v or v == "None":
                continue
            v = v.split(",")[0].strip()  # passthrough takes the first feed
            if sock.wire_type == out_wt:
                chosen = v
                break
            if chosen is None:
                chosen = v
        lines.append(f"{var} = {chosen or 'None'}{_annot(node)}  # bypassed")

    def _emit_select(self, node, lines: list[str]) -> None:
        """Sub-shape selector (SelectEdge/Face/Vertex): resolve the picked set
        against the upstream shape at run time via the injected helper."""
        ndef = catalog.get(node.type)
        var = self._new_var(node.id)
        src = self._input_values(node.id, ndef).get("geometry", "None")
        sel = node.params.get("selection") or {}
        default_kind = {"SelectFace": "face", "SelectVertex": "vertex"}.get(node.type, "edge")
        kind = sel.get("kind", default_kind)
        indices = sel.get("indices", []) or []
        sigs = sel.get("sigs", []) or []
        body = [f"{var} = _select_subshapes({src}, {kind!r}, {indices!r}, {sigs!r}){_annot(node)}"]
        self._guard(lines, body, node)

    def _emit_codeblock(self, node, lines: list[str]) -> None:
        """A CodeBlock transpiles like TWO connected nodes: a params node and a
        code node. Declared `#@param`s become the function's named ARGUMENTS — so
        the body stays pure user code (the `#@param` declaration lines are dropped
        from it) and every param value appears exactly once, at the call site, as
        an editable literal. The code body itself is exposed as an editable `code`
        input (a span on the function name)."""
        ndef = catalog.get(node.type)
        var = self._new_var(node.id)
        feeds = self.graph.inputs_of(node.id)
        inputs = self._input_values(node.id, ndef)        # the static in_0..in_5
        user_code = node.params.get("code", "result = None")
        overrides = node.params.get("_cb") or {}
        declared = parse_codeblock_params(user_code)
        names = [d["name"] for d in declared]
        fn = f"__codeblock_{self._counter}"

        # Resolve each param's call-site argument. A same-named wired socket drives
        # it (params-as-inputs) and fans the whole block out if it carries a list
        # (Range -> CodeBlock.teeth => one result per value); otherwise it is the
        # effective value (override, else the in-code default) as an editable span.
        fan: dict[str, str] = {}          # name -> list expr fed to _fanout
        call_arg: dict[str, str] = {}     # name -> expr passed as <name>=... at the call
        for d in declared:
            name = d["name"]
            srcs = feeds.get(name, [])
            vars_ = [self.var_of.get(fid, "None") for (fid, _fs) in srcs]
            if vars_:
                maybe_list = len(vars_) >= 2 or any(
                    fid in self._produces_list for (fid, _fs) in srcs)
                if maybe_list:
                    fan[name] = f"[{', '.join(vars_)}]" if len(vars_) >= 2 else vars_[0]
                    call_arg[name] = name        # bound by the _fanout lambda
                else:
                    call_arg[name] = vars_[0]
            else:
                value = overrides.get(name, d["default"])
                literal = _cb_literal(value, d["type"])
                if self._emit_map:
                    literal = self._register_span({
                        "node_id": node.id, "node_type": node.type,
                        "param": "_cb." + name, "kind": d["type"], "label": name,
                        "value": value, "min": d["min"], "max": d["max"],
                        "step": d["step"], "options": d["options"],
                    }, literal)
                call_arg[name] = literal

        # def <fn>(in_0..in_5, <params...>):  — the function name doubles as the
        # editable `code` span (click it in the code view to edit the whole body).
        fn_tok = fn
        if self._emit_map:
            fn_tok = self._register_span({
                "node_id": node.id, "node_type": node.type, "param": "code",
                "kind": "code", "label": "code", "value": user_code,
                "min": None, "max": None, "step": None, "options": None,
            }, fn)
        sig = [s.name for s in ndef.inputs] + names
        lines.append(f"def {fn_tok}({', '.join(sig)}):")
        emitted = False
        for raw in user_code.splitlines() or ["result = None"]:
            if _parse_cb_line(raw) is not None:
                continue                 # a #@param declaration -> now an argument
            lines.append("    " + raw)
            emitted = True
        if not emitted:
            lines.append("    pass")
        lines.append("    return result")

        in_kw = ", ".join(f"{s.name}={inputs.get(s.name, 'None')}" for s in ndef.inputs)
        param_kw = "".join(f", {n}={call_arg[n]}" for n in names)
        if fan:
            lam = f"lambda {', '.join(fan)}: {fn}({in_kw}{param_kw})"
            kw = "{" + ", ".join(f"{n!r}: {v}" for n, v in fan.items()) + "}"
            call = f"_fanout({lam}, {kw})"
            self._produces_list.add(node.id)   # fanned -> output is a list
        else:
            call = f"{fn}({in_kw}{param_kw})"

        body = [f"{var} = {call}{_annot(node)}"]
        if self._previewed(node, ndef):
            body.append(f"__previews__[{node.id!r}] = {var}")
        self._guard(lines, body, node)

    def _cast(self, src, sock, var: str) -> str:
        """Auto-apply a boundary cast when the effective upstream type needs one to
        match this input's type (e.g. a curve into a sketch input -> _face(var)).
        Skipped for `raw` sockets (the node coerces by itself) and identity edges.
        The registry of casts lives in cad_nodes/casts.py."""
        if getattr(sock, "raw", False):
            return var
        eff = self.graph.effective_output_type(src[0], src[1])
        helper = catalog.cast_helper(eff, sock.wire_type)
        return f"{helper}({var})" if helper else var

    def _emit_simple(self, node, lines: list[str]) -> None:
        ndef = catalog.get(node.type)
        feeds = self.graph.inputs_of(node.id)

        # Classify each input. Item-access inputs that may carry a list at run
        # time (>=2 connections, or fed by a list-producing node) FAN OUT: they
        # become lambda parameters and _fanout maps the node over the items.
        subs = self._param_values(node, ndef)
        subs["node_id"] = node.id
        fan: dict[str, str] = {}  # input name -> value expr passed to _fanout
        for sock in ndef.inputs:
            srcs = feeds.get(sock.name, [])
            vars_ = [self.var_of.get(fn, "None") for (fn, _fs) in srcs]
            if sock.multiple:                       # collector: whole list
                subs[sock.name] = ", ".join(vars_)
            elif sock.list_access:                  # consumes the list as-is
                subs[sock.name] = vars_[0] if vars_ else "None"
            elif not vars_:
                # Unconnected: fall back to a same-named param (params-as-inputs —
                # wire the socket to override/drive the widget), else None.
                if ndef.param(sock.name) is None:
                    subs[sock.name] = "None"
            else:
                maybe_list = len(vars_) >= 2 or any(
                    fn in self._produces_list for (fn, _fs) in srcs)
                if maybe_list:
                    fan[sock.name] = f"[{', '.join(vars_)}]" if len(vars_) >= 2 else vars_[0]
                    subs[sock.name] = sock.name     # bound by the lambda
                else:
                    subs[sock.name] = self._cast(srcs[0], sock, vars_[0])

        template = ndef.code_template.get("algebra")
        if template is None:
            raise ValueError(f"Node {node.type} has no algebra template")
        expr = _substitute(template, subs)

        # Optional `origin` input positions the result; it is item-access too, so
        # it may be a lambda-bound name (fanned) or a direct value.
        origin = subs.get("origin")
        if origin and origin != "None":
            expr = f"_at({expr}, {origin})"

        if fan:
            lam = f"lambda {', '.join(fan)}: {expr}"
            kw = "{" + ", ".join(f"{n!r}: {v}" for n, v in fan.items()) + "}"
            expr = f"_fanout({lam}, {kw})"
            self._produces_list.add(node.id)        # fanned -> output is a list
        elif node.type in _LIST_PRODUCERS:
            self._produces_list.add(node.id)

        if ndef.outputs:
            var = self._new_var(node.id)
            body = [f"{var} = {expr}{_annot(node)}"]
            if self._previewed(node, ndef):
                body.append(f"__previews__[{node.id!r}] = {var}")
            self._guard(lines, body, node)
        else:  # export / sink statement
            self._guard(lines, [f"{expr}{_annot(node)}"], node)

    def _emit_group(self, node, lines: list[str], indent: str = "") -> None:
        ndef = catalog.get(node.type)
        self._counter += 1
        ctx = f"__ctx_{self._counter}"
        var = f"__out_{self._counter}"
        self.var_of[node.id] = var
        values = self._param_values(node, ndef)
        values.update(self._input_values(node.id, ndef))
        values["ctx"] = ctx
        header = _substitute(ndef.code_template["builder"], values)

        # Build the block at relative indent; _guard shifts it under try/except.
        body = [header + _annot(node)]
        children = self.graph.children_of(node.id)
        child_order = toposort([c.id for c in children],
                               [(c.from_node, c.to_node) for c in self.graph.connections
                                if c.from_node in {x.id for x in children}
                                and c.to_node in {x.id for x in children}])
        for cid in child_order:
            child = self.graph.node(cid)
            cdef = catalog.get(child.type)
            cvals = self._param_values(child, cdef)
            cvals.update(self._input_values(child.id, cdef))
            tmpl = cdef.code_template.get("builder") or cdef.code_template.get("algebra", "")
            body.append("    " + _substitute(tmpl, cvals) + _annot(child))
        if not children:
            body.append("    pass")

        attr = {"part": "part", "sketch": "sketch", "line": "line"}.get(ndef.group_kind, "part")
        body.append(f"{var} = {ctx}.{attr}{_annot(node)}")
        if self._previewed(node, ndef):
            body.append(f"__previews__[{node.id!r}] = {var}")
        self._guard(lines, body, node)

    def _pick_result(self, order: list[str]) -> str | None:
        # A connection into a pure sink (a node with no outputs — every Export
        # node) does NOT "consume" the shape: the upstream node is still the
        # meaningful end of the chain. Otherwise wiring geometry into an Export
        # node hides it from the preview/quick-export, which then falls back to
        # some dangling node (e.g. an unconnected Import). See feedback
        # 20260707-231423 ("export step non va piu").
        def _has_outputs(nid: str) -> bool:
            try:
                return bool(catalog.get(self.graph.node(nid).type).outputs)
            except Exception:
                return True
        used_as_source = {c.from_node for c in self.graph.connections
                          if _has_outputs(c.to_node)}
        geometry_like = {catalog.WIRE_SOLID, catalog.WIRE_SURFACE}
        candidates = []
        for nid in order:
            if nid not in self.var_of:
                continue
            ndef = catalog.get(self.graph.node(nid).type)
            if ndef.outputs and ndef.outputs[0].wire_type in geometry_like:
                candidates.append(nid)
        sinks = [n for n in candidates if n not in used_as_source]
        chosen = (sinks or candidates)
        if chosen:
            return self.var_of[chosen[-1]]
        # fallback: the last variable actually produced (skip nodes that emit no
        # var — Note annotations, export sinks — which can sit last in topo order)
        for nid in reversed(order):
            if nid in self.var_of:
                return self.var_of[nid]
        return None

    def _extract_spans(self, text: str) -> tuple[str, list[dict]]:
        """Strip the source-map sentinels from `text`, returning the clean source
        and a list of param spans with their (row, col0, col1) in that clean text.
        Rows/cols are 0-based to match Ace editor coordinates."""
        clean: list[str] = []
        spans: list[dict] = []
        row = col = i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == _SP_A:
                m = _SP_RE.match(text, i)
                if m:
                    sid, literal = int(m.group(1)), m.group(2)
                    clean.append(literal)
                    meta = dict(self._span_meta.get(sid, {}))
                    meta.update(row=row, col0=col, col1=col + len(literal))
                    spans.append(meta)
                    col += len(literal)
                    i = m.end()
                    continue
            clean.append(ch)
            if ch == "\n":
                row += 1
                col = 0
            else:
                col += 1
            i += 1
        return "".join(clean), spans

    def run(self, emit_map: bool = False):
        """Transpile the graph. With emit_map, return (source, param_spans) where
        each span maps a code location back to a node param for inline editing."""
        self._emit_map = emit_map
        warnings = self.graph.validate()
        order = toposort([n.id for n in self.graph.nodes], self.graph.edges())

        body: list[str] = []
        for nid in order:
            node = self.graph.node(nid)
            if node.parent is not None:
                continue  # emitted inside its group
            if node.type == "Note":
                continue  # canvas annotation only — never emitted
            if getattr(node, "bypassed", False):
                self._emit_bypass(node, body)
                continue
            ndef = catalog.get(node.type)
            if ndef.is_group:
                self._emit_group(node, body)
            elif node.type == "CodeBlock":
                self._emit_codeblock(node, body)
            elif node.type in ("SelectEdge", "SelectFace", "SelectVertex"):
                self._emit_select(node, body)
            else:
                self._emit_simple(node, body)

        result_var = self._pick_result(order)

        out = [PREAMBLE, "", "# --- nodes ---"]
        if warnings:
            out.append("# warnings: " + "; ".join(warnings))
        out.extend(body)
        out.append("")
        out.append("# --- result for preview ---")
        out.append(f"__result__ = {result_var}" if result_var else "__result__ = None")
        text = "\n".join(out) + "\n"
        if emit_map:
            return self._extract_spans(text)
        return text


def transpile(graph: Graph) -> str:
    """Convenience: transpile a Graph to build123d source."""
    return Transpiler(graph).run()


def transpile_with_map(graph: Graph) -> tuple[str, list[dict]]:
    """Transpile + a param<->code source map (see Transpiler._extract_spans)."""
    return Transpiler(graph).run(emit_map=True)
