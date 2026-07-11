"""
Wire types + the cast registry — the single source of truth for the data flow.

Which wire types may connect, and how one coerces into another, is defined ONCE
here. `WIRE_COMPATIBLE` (backend, output -> accepted inputs) and the frontend's
`INPUT_ACCEPTS` (input -> accepted outputs) are both **derived** from this file
(`build_compatible()` / `build_input_accepts()`), so the two tables can no longer
drift apart (the old failure mode — they were hand-maintained in two places).

A connection `src -> dst` is legal when:
  - `src == dst`                 (identity)
  - `dst == data`                (the universal sink: any output -> a data input)
  - `src == data` and `dst` in `_DATA_FEEDS`  (data as a permissive source)
  - `(src, dst)` is a registered **cast** in `CASTS`

`CASTS[(src, dst)]` names the PREAMBLE coercion helper that turns a runtime
`src` value into what a `dst` input expects (e.g. a closed `curve` -> a `surface`
via `_face`). Today the helpers are applied explicitly by node templates; the
names are recorded here so a later phase can apply them automatically at the wire
boundary (and give transforms type-preserving output). `None` = the value is
already acceptable as-is (a pure type widening, no runtime change).

See PLAN_DATA_PROTOCOL.md. The wire ids `geometry`->`solid` and `sketch`->`surface`
were renamed (Python constants WIRE_SOLID / WIRE_SURFACE); the remaining sub-types
(data -> number/int/…, curve -> line/polyline/spline) are advisory tags
(Socket.subtype), not yet split into real wire ids.
"""

from __future__ import annotations

# --- wire type ids (mirror the colour coding in PLAN_NODE_CAD.md) ----------
WIRE_SOLID = "solid"            # Shape / Part / Solid / Compound (the "brep" umbrella)
WIRE_SURFACE = "surface"        # 2D Sketch / Face (a surface)
WIRE_CURVE = "curve"            # Curve / Wire (line | polyline | spline | arc)
WIRE_DATA = "data"              # universal bus: int | float | bool | str | list | domain
WIRE_TREE = "tree"              # data tree (declared; unused for now)
WIRE_PLANE = "plane"            # Plane / Location (a frame)
WIRE_VECTOR = "vector"          # Vector / Point
WIRE_SELECTION = "selection"    # picked sub-shapes (edges/faces/vertices)

WIRE_TYPES: list[str] = [
    WIRE_SOLID, WIRE_SURFACE, WIRE_CURVE, WIRE_PLANE,
    WIRE_VECTOR, WIRE_SELECTION, WIRE_DATA, WIRE_TREE,
]

# --- the cast registry: (src, dst) -> coercion helper name (or None) -------
# Non-identity, non-`data` casts only. Reproduces the previous WIRE_COMPATIBLE
# exactly (verified), now with the coercion each edge implies made explicit.
CASTS: dict[tuple[str, str], str | None] = {
    (WIRE_SURFACE, WIRE_SOLID): None,          # a face IS a (degenerate) solid/brep
    (WIRE_SURFACE, WIRE_PLANE): "_as_plane",      # a planar face -> the plane it lies in
    (WIRE_CURVE, WIRE_SURFACE): "_face",          # closed curve -> filled face
    (WIRE_SOLID, WIRE_PLANE): None,           # transforms treat a plane as geometry…
    (WIRE_PLANE, WIRE_SOLID): None,           # …and a transformed plane flows back
    (WIRE_SELECTION, WIRE_VECTOR): None,         # picked sub-shapes as point origins
}

# A `data` OUTPUT may feed these input types (a list from List/Sort/Item flowing
# back into geometry/vector/plane…). It may NOT synthesise a selection or a tree.
_DATA_FEEDS: set[str] = {
    WIRE_SOLID, WIRE_SURFACE, WIRE_CURVE, WIRE_VECTOR, WIRE_PLANE, WIRE_DATA,
}


def wires_compatible(src_type: str, dst_type: str) -> bool:
    """True if an output of `src_type` may feed an input of `dst_type`."""
    if src_type == dst_type:
        return True
    if dst_type == WIRE_DATA:                    # any output -> a data input
        return True
    if src_type == WIRE_DATA:                    # data as a permissive source
        return dst_type in _DATA_FEEDS
    return (src_type, dst_type) in CASTS


def cast_helper(src_type: str, dst_type: str) -> str | None:
    """Name of the PREAMBLE coercion helper for `src -> dst`, or None when the
    value is already acceptable (identity / pure widening)."""
    return CASTS.get((src_type, dst_type))


def build_compatible() -> dict[str, set[str]]:
    """Derive the output -> {accepted inputs} table (back-compat shape)."""
    return {s: {d for d in WIRE_TYPES if wires_compatible(s, d)} for s in WIRE_TYPES}


def build_input_accepts() -> dict[str, list[str]]:
    """Derive the input -> [accepted outputs] table for the frontend (the inverse
    of build_compatible). Sorted for stable output."""
    return {d: sorted(s for s in WIRE_TYPES if wires_compatible(s, d)) for d in WIRE_TYPES}
