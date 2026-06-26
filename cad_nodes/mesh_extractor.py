"""
Mesh / view extraction — runs INSIDE the execution subprocess, so it may import
`build123d`. Turns the transpiled graph's `__result__` into:

  - an STL file (for the existing Three.js viewer), and
  - a `view.json` summary: bbox, volume, area, centre, topology counts and a
    tessellated mesh {vertices, triangles} for direct rendering / AI inspection.

Everything is defensive: a missing capability degrades to null rather than
crashing the whole run.
"""

from __future__ import annotations

import json


def _as_shape(result):
    """Normalise __result__ (Part/Sketch/Solid/Compound or list) to one shape."""
    if result is None:
        return None
    if isinstance(result, (list, tuple)):
        from build123d import Compound
        shapes = [s for s in result if s is not None]
        if not shapes:
            return None
        try:
            return Compound(children=shapes)
        except Exception:
            try:
                return Compound.make_compound(shapes)
            except Exception:
                return shapes[0]
    return result


def _num(fn, default=None):
    try:
        v = fn()
        return float(v)
    except Exception:
        return default


def _count(shape, attr):
    try:
        return len(getattr(shape, attr)())
    except Exception:
        return None


def _deflection(shape, linear_frac: float) -> float:
    """Linear tessellation deflection scaled to the shape size, so big and
    small parts get comparable visual quality (and triangle counts)."""
    try:
        bb = shape.bounding_box()
        diag = (bb.size.X ** 2 + bb.size.Y ** 2 + bb.size.Z ** 2) ** 0.5
        return max(diag * linear_frac, 0.05)
    except Exception:
        return 0.5


# How many list items a panel serialises before truncating (Grasshopper shows
# all; this is a generous cap so the panel stays useful without huge payloads).
_PANEL_CAP = 200


def _summarize(value, _depth: int = 0):
    """Structured, JSON-safe rendering of any node output for the Panel widget.

    Standardised by type so the frontend can show it Grasshopper-style:
      scalar -> the value itself
      point  -> {kind:'point', x,y,z}
      plane  -> {kind:'plane', origin:[x,y,z], normal:[x,y,z]}
      shape  -> {kind:'shape', type, volume, area, faces, edges, vertices}
      list   -> {kind:'list', length, items:[...], truncated?}
    """
    try:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        # vector / point / vertex: anything with X,Y,Z components
        if all(hasattr(value, a) for a in ("X", "Y", "Z")):
            return {"kind": "point", "x": _num(lambda: value.X),
                    "y": _num(lambda: value.Y), "z": _num(lambda: value.Z)}
        # plane / frame: origin + normal
        if hasattr(value, "origin") and hasattr(value, "z_dir"):
            o, z = value.origin, value.z_dir
            return {"kind": "plane",
                    "origin": [_num(lambda: o.X), _num(lambda: o.Y), _num(lambda: o.Z)],
                    "normal": [_num(lambda: z.X), _num(lambda: z.Y), _num(lambda: z.Z)]}
        # list / tuple / ShapeList
        if isinstance(value, (list, tuple)) or type(value).__name__ == "ShapeList":
            items = list(value)
            if _depth >= 4:                       # guard against deep nesting
                return {"kind": "list", "length": len(items), "items": []}
            shown = [_summarize(v, _depth + 1) for v in items[:_PANEL_CAP]]
            out = {"kind": "list", "length": len(items), "items": shown}
            if len(items) > _PANEL_CAP:
                out["truncated"] = len(items) - _PANEL_CAP
            return out
        if isinstance(value, dict):
            return {"kind": "dict", "keys": list(value.keys())[:32]}
        # build123d shape (solid / sketch / curve / face / edge …)
        if any(hasattr(value, a) for a in ("volume", "area", "wrapped")):
            return {"kind": "shape", "type": type(value).__name__,
                    "volume": _num(lambda: value.volume),
                    "area": _num(lambda: value.area),
                    "faces": _count(value, "faces"),
                    "edges": _count(value, "edges"),
                    "vertices": _count(value, "vertices")}
        return {"kind": "repr", "type": type(value).__name__, "repr": repr(value)[:200]}
    except Exception:
        return {"kind": "repr", "type": "unknown", "repr": repr(value)[:200]}


def extract_view(shape, linear_frac: float = 0.02, angular: float = 0.4) -> dict:
    shape = _as_shape(shape)
    if shape is None:
        return {"success": False, "error": "no result shape"}

    view: dict = {"success": True, "kind": type(shape).__name__}

    # Bounding box
    try:
        bb = shape.bounding_box()
        view["bbox"] = {
            "min": [bb.min.X, bb.min.Y, bb.min.Z],
            "max": [bb.max.X, bb.max.Y, bb.max.Z],
            "size": [bb.size.X, bb.size.Y, bb.size.Z],
        }
    except Exception:
        view["bbox"] = None

    view["volume"] = _num(lambda: shape.volume)
    view["area"] = _num(lambda: shape.area)

    try:
        c = shape.center()
        view["center"] = [c.X, c.Y, c.Z]
    except Exception:
        view["center"] = None

    view["counts"] = {
        "vertices": _count(shape, "vertices"),
        "edges": _count(shape, "edges"),
        "faces": _count(shape, "faces"),
        "solids": _count(shape, "solids"),
    }

    # Tessellated mesh (LOD: coarse for live preview, scaled to part size)
    try:
        verts, tris = shape.tessellate(_deflection(shape, linear_frac), angular)
        view["mesh"] = {
            "vertices": [[v.X, v.Y, v.Z] for v in verts],
            "triangles": [list(t) for t in tris],
        }
    except Exception as e:
        view["mesh"] = None
        view["mesh_error"] = str(e)

    return view


def _is_point(v) -> bool:
    """A bare point/vector: has X/Y/Z but isn't a topological shape (no edges)."""
    return (hasattr(v, "X") and hasattr(v, "Y") and hasattr(v, "Z")
            and not hasattr(v, "edges") and not hasattr(v, "faces"))


def _points_of(value):
    """If `value` is a point or a flat list of points (Vectors), return them as
    [[x,y,z], …]; else None. Used to draw DivideCurve / ConstructPoint outputs
    as dots, since they have no mesh or wire of their own."""
    items = list(value) if isinstance(value, (list, tuple)) else [value]
    items = [it for it in items if it is not None]
    if items and all(_is_point(it) for it in items):
        return [[float(it.X), float(it.Y), float(it.Z)] for it in items]
    return None


def _polylines_of(shape, n: int = 32):
    """Sample each edge of `shape` into a polyline [[x,y,z], …]. Returns a list
    of polylines (one per edge), or None if the shape has no usable edges. This
    is the render path for curves/wires, which don't tessellate into triangles."""
    try:
        edges = list(shape.edges())
    except Exception:
        return None
    polys = []
    for e in edges:
        try:
            poly = [[(e @ (k / n)).X, (e @ (k / n)).Y, (e @ (k / n)).Z]
                    for k in range(n + 1)]
            polys.append(poly)
        except Exception:
            continue
    return polys or None


def _bbox_of_coords(coords) -> dict | None:
    """Axis-aligned bbox from a flat list of [x,y,z] points."""
    if not coords:
        return None
    xs = [c[0] for c in coords]; ys = [c[1] for c in coords]; zs = [c[2] for c in coords]
    lo = [min(xs), min(ys), min(zs)]; hi = [max(xs), max(ys), max(zs)]
    return {"min": lo, "max": hi, "size": [hi[i] - lo[i] for i in range(3)]}


def _preview_of(value, linear_frac: float = 0.02, angular: float = 0.4) -> dict | None:
    """Compact per-node preview. Three render paths, tried in order:
      - points    : a Vector or list of Vectors -> dots
      - mesh      : a solid/sketch -> tessellated triangles
      - polylines : a curve/wire   -> sampled edges
    Returns None if the value isn't drawable geometry."""
    # 1) bare points (before _as_shape, which can't compound raw Vectors)
    pts = _points_of(value)
    if pts is not None:
        return {"kind": "Points", "points": pts, "bbox": _bbox_of_coords(pts)}

    shape = _as_shape(value)
    if shape is None:
        return None

    # 2) meshable surface/solid
    verts, tris = [], []
    try:
        verts, tris = shape.tessellate(_deflection(shape, linear_frac), angular)
    except Exception:
        verts, tris = [], []
    if verts and tris:
        entry: dict = {"kind": type(shape).__name__,
                       "mesh": {"vertices": [[v.X, v.Y, v.Z] for v in verts],
                                "triangles": [list(t) for t in tris]}}
        try:
            bb = shape.bounding_box()
            entry["bbox"] = {"min": [bb.min.X, bb.min.Y, bb.min.Z],
                             "max": [bb.max.X, bb.max.Y, bb.max.Z],
                             "size": [bb.size.X, bb.size.Y, bb.size.Z]}
        except Exception:
            entry["bbox"] = None
        entry["volume"] = _num(lambda: shape.volume)
        return entry

    # 3) curve / wire -> polylines
    polys = _polylines_of(shape)
    if polys:
        flat = [p for poly in polys for p in poly]
        return {"kind": type(shape).__name__, "polylines": polys,
                "bbox": _bbox_of_coords(flat)}
    return None


def extract_subshapes(shape, kind: str = "edge",
                      linear_frac: float = 0.01, angular: float = 0.3) -> dict:
    """For the interactive picker: a context mesh of `shape` plus its sub-shapes
    (edges as polylines, faces as triangle groups, vertices as points), each
    tagged with an index and a geometric signature for stable re-selection.

    Signature layout (all keep [x,y,z] anchor first, used for nearest-match):
      edge   -> [mid.x, mid.y, mid.z, length, dir.x, dir.y, dir.z]
      face   -> [c.x, c.y, c.z, area, n.x, n.y, n.z]
      vertex -> [x, y, z]
    """
    shape = _as_shape(shape)
    if shape is None:
        return {"success": False, "error": "no shape"}
    out: dict = {"success": True, "kind": kind}

    try:
        verts, tris = shape.tessellate(_deflection(shape, linear_frac), angular)
        out["mesh"] = {"vertices": [[v.X, v.Y, v.Z] for v in verts],
                       "triangles": [list(t) for t in tris]}
    except Exception:
        out["mesh"] = None

    items: list[dict] = []
    if kind == "edge":
        for i, e in enumerate(shape.edges()):
            try:
                n = 18
                poly = [[(e @ (k / n)).X, (e @ (k / n)).Y, (e @ (k / n)).Z]
                        for k in range(n + 1)]
                mid, d = e @ 0.5, e % 0.5
                items.append({"index": i, "polyline": poly,
                              "sig": [mid.X, mid.Y, mid.Z, float(e.length),
                                      d.X, d.Y, d.Z]})
            except Exception:
                continue
    elif kind == "face":
        for i, f in enumerate(shape.faces()):
            try:
                fv, ft = f.tessellate(_deflection(shape, linear_frac), angular)
                c = f.center(); nrm = f.normal_at(c)
                items.append({"index": i,
                              "mesh": {"vertices": [[v.X, v.Y, v.Z] for v in fv],
                                       "triangles": [list(t) for t in ft]},
                              "sig": [c.X, c.Y, c.Z, float(f.area),
                                      nrm.X, nrm.Y, nrm.Z]})
            except Exception:
                continue
    elif kind == "vertex":
        for i, v in enumerate(shape.vertices()):
            try:
                items.append({"index": i, "point": [v.X, v.Y, v.Z],
                              "sig": [v.X, v.Y, v.Z]})
            except Exception:
                continue

    out["items"] = items
    out["count"] = len(items)
    return out


def extract_and_write(result, stl_path: str, view_path: str, panels=None,
                      previews=None, linear_frac: float = 0.02,
                      angular: float = 0.4, errors=None) -> dict:
    """Write STL + view.json. Returns the view dict."""
    shape = _as_shape(result)
    view = extract_view(shape, linear_frac, angular)
    if errors:
        view["node_errors"] = dict(errors)

    if shape is not None and stl_path:
        try:
            from build123d import export_stl
            export_stl(shape, stl_path)
            view["stl"] = stl_path
        except Exception as e:
            view["stl_error"] = str(e)

    if panels:
        view["panels"] = {k: _summarize(v) for k, v in panels.items()}

    if previews:
        out: dict = {}
        for nid, val in previews.items():
            try:
                entry = _preview_of(val, linear_frac, angular)
            except Exception:
                entry = None
            if entry:
                out[nid] = entry
        if out:
            view["previews"] = out

    if view_path:
        with open(view_path, "w") as f:
            json.dump(view, f)
    return view
