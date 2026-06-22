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


def _summarize(value):
    """JSON-safe summary of an arbitrary panel value."""
    try:
        if isinstance(value, (int, float, str, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return {"type": "list", "length": len(value),
                    "items": [_summarize(v) for v in list(value)[:8]]}
        if isinstance(value, dict):
            return {"type": "dict", "keys": list(value.keys())[:16]}
        # build123d shapes / vectors
        for attr in ("volume", "area"):
            if hasattr(value, attr):
                return {"type": type(value).__name__,
                        "volume": _num(lambda: getattr(value, "volume")),
                        "area": _num(lambda: getattr(value, "area"))}
        return {"type": type(value).__name__, "repr": repr(value)[:120]}
    except Exception:
        return {"type": "unknown", "repr": repr(value)[:120]}


def extract_view(shape) -> dict:
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

    # Tessellated mesh
    try:
        verts, tris = shape.tessellate(0.2)
        view["mesh"] = {
            "vertices": [[v.X, v.Y, v.Z] for v in verts],
            "triangles": [list(t) for t in tris],
        }
    except Exception as e:
        view["mesh"] = None
        view["mesh_error"] = str(e)

    return view


def extract_and_write(result, stl_path: str, view_path: str, panels=None) -> dict:
    """Write STL + view.json. Returns the view dict."""
    shape = _as_shape(result)
    view = extract_view(shape)

    if shape is not None and stl_path:
        try:
            from build123d import export_stl
            export_stl(shape, stl_path)
            view["stl"] = stl_path
        except Exception as e:
            view["stl_error"] = str(e)

    if panels:
        view["panels"] = {k: _summarize(v) for k, v in panels.items()}

    if view_path:
        with open(view_path, "w") as f:
            json.dump(view, f)
    return view
