"""
Node catalog — declarative definitions of every node type.

Each node is a `NodeDef`: its sockets (inputs/outputs), parameters (widgets +
defaults) and a `code_template` that the transpiler turns into build123d code.

The catalog is data, not logic: adding the rest of the ~150 nodes from
PLAN_NODE_CAD.md is pure data-entry against this same shape. The subset here
covers every category so the engine is exercisable end-to-end.

Template substitution (see transpiler.py):
  - {socket_name}  -> the variable holding the upstream node's output
  - {param_name}   -> the formatted parameter value

Group nodes (BuildPart / BuildSketch / BuildLine) are flagged with
`is_group=True` and handled specially by the transpiler (nested `with` blocks).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Wire types — mirror the colour coding in PLAN_NODE_CAD.md
# ---------------------------------------------------------------------------
WIRE_GEOMETRY = "geometry"  # Shape / Part / Solid
WIRE_SKETCH = "sketch"      # 2D Sketch
WIRE_CURVE = "curve"        # Curve / Wire
WIRE_DATA = "data"          # list, float, int, str
WIRE_TREE = "tree"          # data tree
WIRE_PLANE = "plane"        # Plane / Location
WIRE_VECTOR = "vector"      # Vector / Point
WIRE_SELECTION = "selection"  # a set of sub-shapes (edges/faces/vertices) picked off a parent

# Which wire types may legally connect to which (output -> accepted inputs).
# Geometry-ish types are kept distinct but `data` is the permissive fallback.
WIRE_COMPATIBLE: dict[str, set[str]] = {
    # geometry <-> plane are interchangeable so the transform nodes (Move /
    # Rotate / Scale) treat a Plane like any other geometry: a Plane can be
    # transformed, and a transformed result can flow back into a plane input.
    WIRE_GEOMETRY: {WIRE_GEOMETRY, WIRE_DATA, WIRE_PLANE},
    WIRE_SKETCH: {WIRE_SKETCH, WIRE_GEOMETRY, WIRE_DATA},
    WIRE_CURVE: {WIRE_CURVE, WIRE_SKETCH, WIRE_DATA},
    # `data` is the universal wire: it already accepts anything as a sink, and
    # is also accepted everywhere as a source — so a list manipulated by the
    # List/Sort/Item nodes can flow back into a geometry/vector/plane input.
    # Mismatches (a number into a solid op) surface as a guarded per-node error.
    WIRE_DATA: {WIRE_DATA, WIRE_GEOMETRY, WIRE_SKETCH, WIRE_CURVE, WIRE_VECTOR, WIRE_PLANE},
    WIRE_TREE: {WIRE_TREE, WIRE_DATA},
    WIRE_PLANE: {WIRE_PLANE, WIRE_DATA, WIRE_GEOMETRY},
    WIRE_VECTOR: {WIRE_VECTOR, WIRE_DATA},
    # a selection (sub-shapes) is also usable as raw data, and as point origins
    WIRE_SELECTION: {WIRE_SELECTION, WIRE_DATA, WIRE_VECTOR},
}


def wires_compatible(src_type: str, dst_type: str) -> bool:
    """True if an output of `src_type` may feed an input of `dst_type`."""
    return dst_type in WIRE_COMPATIBLE.get(src_type, {src_type})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Socket:
    name: str
    wire_type: str = WIRE_DATA
    required: bool = True
    multiple: bool = False  # accepts many connections (e.g. ListCreate, Loft)
    # Grasshopper-style data access. Item-access inputs FAN OUT: a list on the
    # wire (from several connections, or a list-producing upstream) runs the node
    # once per item -> a list output. list_access inputs consume the whole list
    # as one value (List/Sort/Item… and every `multiple` collector).
    list_access: bool = False


@dataclass
class Param:
    name: str
    type: str = "float"  # float | int | bool | str | select
    label: str = ""
    default: Any = 0.0
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    widget: str = "input"  # input | slider | checkbox | select | color | text
    options: list[str] = field(default_factory=list)
    # Map a select value -> raw code expression (e.g. "Z" -> "Axis.Z").
    code_map: dict[str, str] = field(default_factory=dict)
    # Render the value verbatim (no quoting) — for params that are code.
    raw: bool = False
    optional: bool = False


@dataclass
class NodeDef:
    type: str
    category: str
    label: str = ""
    inputs: list[Socket] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    outputs: list[Socket] = field(default_factory=list)
    # code_template["algebra"] is required; ["builder"] used inside group nodes.
    code_template: dict[str, str] = field(default_factory=dict)
    imports: list[str] = field(default_factory=lambda: ["from build123d import *"])
    description: str = ""
    is_group: bool = False
    # For group nodes: how to read the result out of the context manager var.
    group_kind: Optional[str] = None  # "part" | "sketch" | "line"
    # On-canvas direct manipulation (PLAN_NODE_CAD.md §E). When set, the editor
    # shows an "Edit on canvas" toggle that drives a viewport gizmo bound to the
    # named params. Shape:
    #   {"kind": "translate", "binds": ["x","y","z"],
    #    "anchor": "params"|"preview", "lock": [<socket names>]}
    # binds  = the three params the gizmo writes (X/Y/Z order).
    # anchor = where the handle rests: "params" (the bound values ARE the point,
    #          e.g. ConstructPoint) or "preview" (the node's result bbox centre,
    #          e.g. Move — its params are a delta, not a world position).
    # lock   = input sockets that, when wired, make the value upstream-computed →
    #          the gizmo is read-only (you edit the producer node instead).
    gizmo: Optional[dict] = None

    def param(self, name: str) -> Optional[Param]:
        for p in self.params:
            if p.name == name:
                return p
        return None

    def input(self, name: str) -> Optional[Socket]:
        for s in self.inputs:
            if s.name == name:
                return s
        return None

    def output(self, name: str) -> Optional[Socket]:
        for s in self.outputs:
            if s.name == name:
                return s
        return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
REGISTRY: dict[str, NodeDef] = {}


def register(node: NodeDef) -> NodeDef:
    if node.type in REGISTRY:
        raise ValueError(f"Duplicate node type: {node.type}")
    if not node.label:
        node.label = node.type
    REGISTRY[node.type] = node
    return node


def get(node_type: str) -> NodeDef:
    try:
        return REGISTRY[node_type]
    except KeyError:
        raise KeyError(f"Unknown node type: {node_type!r}")


def as_json() -> list[dict]:
    """Serialise the whole catalog (for `cad://nodes` / frontend registry)."""
    return [asdict(REGISTRY[t]) for t in sorted(REGISTRY)]


# Convenience shorthands for terse definitions below ------------------------
def _geo(name="result"):
    return [Socket(name, WIRE_GEOMETRY)]


def _sk(name="result"):
    return [Socket(name, WIRE_SKETCH)]


def _data(name="result"):
    return [Socket(name, WIRE_DATA)]


def _cv(name="result"):
    return [Socket(name, WIRE_CURVE)]


def _origin_in():
    # optional point input; when wired, positions the primitive there (default 0,0,0)
    return [Socket("origin", WIRE_VECTOR, required=False)]


def _pin(*names):
    """Optional data inputs named like params: when wired, the socket overrides
    the widget (params-as-inputs); feed a list to fan the node out (one copy per
    value). Unwired, the node falls back to the param widget. Zero transpiler
    cost — `_emit_simple` already resolves a same-named param/socket."""
    return [Socket(n, WIRE_DATA, required=False) for n in names]


def _f(name, default=0.0, lo=None, hi=None, step=0.5, label="", widget="slider"):
    return Param(name, "float", label or name, default, lo, hi, step, widget)


def _i(name, default=0, lo=None, hi=None, label="", widget="slider"):
    return Param(name, "int", label or name, default, lo, hi, 1, widget)


# ===========================================================================
# 1. Primitives 3D
# ===========================================================================
register(NodeDef("Box", "primitives_3d", "Box",
    inputs=_origin_in() + _pin("width", "height", "depth"),
    params=[_f("width", 10, 0.1, 500), _f("height", 10, 0.1, 500), _f("depth", 10, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Box({width}, {height}, {depth})"},
    description="Solid box of given width, height, depth."))

register(NodeDef("Cylinder", "primitives_3d", "Cylinder",
    inputs=_origin_in() + _pin("radius", "height"),
    params=[_f("radius", 5, 0.1, 500), _f("height", 20, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Cylinder({radius}, {height})"},
    description="Solid cylinder."))

register(NodeDef("Sphere", "primitives_3d", "Sphere",
    inputs=_origin_in() + _pin("radius"),
    params=[_f("radius", 10, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Sphere({radius})"},
    description="Solid sphere."))

register(NodeDef("Cone", "primitives_3d", "Cone",
    inputs=_origin_in() + _pin("bottom_radius", "top_radius", "height"),
    params=[_f("bottom_radius", 5, 0, 500), _f("top_radius", 0, 0, 500), _f("height", 15, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Cone({bottom_radius}, {top_radius}, {height})"},
    description="Truncated cone / frustum."))

register(NodeDef("Torus", "primitives_3d", "Torus",
    inputs=_origin_in() + _pin("major_radius", "minor_radius"),
    params=[_f("major_radius", 15, 0.1, 500), _f("minor_radius", 3, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Torus({major_radius}, {minor_radius})"},
    description="Torus / ring."))

register(NodeDef("ConstructPoint", "vector", "Construct Point",
    inputs=[Socket("x", WIRE_DATA, required=False),
            Socket("y", WIRE_DATA, required=False),
            Socket("z", WIRE_DATA, required=False)],
    params=[_f("x", 0, label="x"), _f("y", 0, label="y"), _f("z", 0, label="z")],
    outputs=[Socket("point", WIRE_VECTOR)],
    gizmo={"kind": "translate", "binds": ["x", "y", "z"],
           "anchor": "params", "lock": ["x", "y", "z"]},
    code_template={"algebra": "Vector({x}, {y}, {z})"},
    description="A point (vector) from X, Y, Z. Each coordinate can be a widget "
                "OR an input — wire a list of numbers (e.g. from Range) into x to "
                "build a list of points. Feed `point` into a primitive's origin."))

# ===========================================================================
# 2. Primitives 2D (sketch / curve)
# ===========================================================================
register(NodeDef("Rectangle", "primitives_2d", "Rectangle",
    inputs=_origin_in() + _pin("width", "height"),
    params=[_f("width", 30, 0.1, 500), _f("height", 20, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Rectangle({width}, {height})"},
    description="2D rectangle sketch."))

register(NodeDef("RoundedRectangle", "primitives_2d", "Rounded Rectangle",
    inputs=_origin_in() + _pin("width", "height", "radius"),
    params=[_f("width", 30, 0.1, 500), _f("height", 20, 0.1, 500), _f("radius", 3, 0, 250)],
    outputs=_sk(),
    code_template={"algebra": "RectangleRounded({width}, {height}, {radius})"},
    description="Rectangle with filleted corners."))

register(NodeDef("Circle", "primitives_2d", "Circle",
    inputs=_origin_in() + _pin("radius"),
    params=[_f("radius", 10, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Circle({radius})"},
    description="2D circle sketch."))

register(NodeDef("Ellipse", "primitives_2d", "Ellipse",
    inputs=_origin_in() + _pin("x_radius", "y_radius"),
    params=[_f("x_radius", 8, 0.1, 500), _f("y_radius", 5, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Ellipse({x_radius}, {y_radius})"},
    description="2D ellipse sketch."))

register(NodeDef("Polygon", "primitives_2d", "Regular Polygon",
    inputs=_origin_in() + _pin("radius", "sides"),
    params=[_f("radius", 10, 0.1, 500), _i("sides", 6, 3, 64)],
    outputs=_sk(),
    code_template={"algebra": "RegularPolygon({radius}, {sides})"},
    description="Regular N-sided polygon."))

register(NodeDef("Text", "primitives_2d", "Text",
    inputs=_origin_in(),
    params=[Param("text", "str", "text", "Hello", widget="input"),
            _f("font_size", 10, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Text({text}, font_size={font_size})"},
    description="Text as a 2D sketch."))

# ===========================================================================
# 2b. Curves (WIRE_CURVE producers) — lines, arcs, splines along which profiles
# are placed / lofted. Point inputs are vectors and fan out.
# ===========================================================================
register(NodeDef("Line", "curves", "Line",
    inputs=[Socket("start", WIRE_VECTOR, required=False),
            Socket("end", WIRE_VECTOR, required=False)],
    outputs=_cv(),
    code_template={"algebra": "Line(_pt({start}), _pt({end}))"},
    description="Straight line between two points. Wire ConstructPoint/Vector into "
                "start and end; feed lists to fan out (one line per pair)."))

register(NodeDef("Polyline", "curves", "Polyline",
    inputs=[Socket("points", WIRE_VECTOR, multiple=True)],
    params=[Param("closed", "bool", "closed", False, widget="checkbox")],
    outputs=_cv(),
    code_template={"algebra": "Polyline(*_curve_points([{points}]), close={closed})"},
    description="Open or closed polyline through a sequence of points. Wire "
                "several points and/or a single list of points."))

register(NodeDef("Arc3pt", "curves", "Arc (3 point)",
    inputs=[Socket("start", WIRE_VECTOR, required=False),
            Socket("mid", WIRE_VECTOR, required=False),
            Socket("end", WIRE_VECTOR, required=False)],
    outputs=_cv(),
    code_template={"algebra": "ThreePointArc(_pt({start}), _pt({mid}), _pt({end}))"},
    description="Circular arc through three points (start, a mid point, end)."))

register(NodeDef("ArcCenter", "curves", "Arc (center)",
    inputs=[Socket("center", WIRE_VECTOR, required=False)] + _pin("radius", "start_angle", "end_angle"),
    params=[_f("radius", 10, 0.1, 500), _f("start_angle", 0, -360, 360),
            _f("end_angle", 90, -360, 360)],
    outputs=_cv(),
    code_template={"algebra": "CenterArc(_pt({center}) or (0, 0, 0), {radius}, {start_angle}, {end_angle})"},
    description="Circular arc by centre point, radius and start/end angle "
                "(degrees, CCW). Centre defaults to the origin if unwired."))

register(NodeDef("Spline", "curves", "Spline",
    inputs=[Socket("points", WIRE_VECTOR, multiple=True)],
    outputs=_cv(),
    code_template={"algebra": "Spline(*_curve_points([{points}]))"},
    description="Smooth spline (B-spline) through a sequence of points. Wire "
                "several points and/or a single list of points."))

# --- curve evaluation & division (the frames layer) ---
register(NodeDef("DivideCurve", "curves", "Divide Curve",
    inputs=[Socket("curve", WIRE_CURVE)] + _pin("count"),
    params=[_i("count", 8, 1, 500, label="count")],
    outputs=[Socket("frames", WIRE_PLANE)],
    code_template={"algebra": "_curve_frames({curve}, {count})"},
    description="Divide a curve into N evenly spaced frames (planes) whose Z axis "
                "is the curve tangent. Feed `frames` into ToPlane to seat profiles "
                "for a Loft, or into Plane Origin to get the points. List output — "
                "downstream fans out (one profile/shape per frame)."))

register(NodeDef("EvaluateCurve", "curves", "Evaluate Curve",
    inputs=[Socket("curve", WIRE_CURVE)] + _pin("t"),
    params=[_f("t", 0.0, 0.0, 1.0, 0.01, label="t")],
    outputs=[Socket("frame", WIRE_PLANE)],
    code_template={"algebra": "_eval_frame({curve}, {t})"},
    description="The frame (plane) at parameter t in [0,1] along a curve, Z aligned "
                "to the tangent. Wire a list of t values (e.g. from Range) to get "
                "many frames (fans out)."))

register(NodeDef("CurveEndpoints", "curves", "Curve Endpoints",
    inputs=[Socket("curve", WIRE_CURVE)],
    outputs=[Socket("points", WIRE_VECTOR)],
    code_template={"algebra": "_curve_endpoints({curve})"},
    description="The start and end of a curve as a 2-item list [start, end] of "
                "points."))

register(NodeDef("CurveLength", "curves", "Curve Length",
    inputs=[Socket("curve", WIRE_CURVE)],
    outputs=_data("length"),
    code_template={"algebra": "_curve_length({curve})"},
    description="Total length of a curve (a number)."))

# ===========================================================================
# 3. Operations 2D -> 3D
# ===========================================================================
register(NodeDef("Extrude", "operations", "Extrude",
    inputs=[Socket("sketch", WIRE_SKETCH)] + _pin("amount", "taper"),
    params=[_f("amount", 10, 0.1, 500), _f("taper", 0, -45, 45),
            Param("both", "bool", "both", False, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "extrude({sketch}, amount={amount}, taper={taper}, both={both})",
                   "builder": "extrude(amount={amount}, taper={taper}, both={both})"},
    description="Extrude a 2D sketch into a 3D solid (along its normal). `both` "
                "extrudes symmetrically in both directions."))

register(NodeDef("Revolve", "operations", "Revolve",
    inputs=[Socket("sketch", WIRE_SKETCH)] + _pin("angle"),
    params=[_f("angle", 360, 1, 360),
            Param("axis", "select", "axis", "Y", widget="select",
                  options=["X", "Y", "Z"],
                  code_map={"X": "Axis.X", "Y": "Axis.Y", "Z": "Axis.Z"})],
    outputs=_geo(),
    code_template={"algebra": "revolve({sketch}, axis={axis}, revolution_arc={angle})",
                   "builder": "revolve(axis={axis}, revolution_arc={angle})"},
    description="Revolve a sketch around an in-plane axis (X or Y). The profile "
                "must sit off the axis (use a Move node), like a lathe — a "
                "profile crossing the axis is invalid geometry."))

register(NodeDef("Loft", "operations", "Loft",
    inputs=[Socket("sections", WIRE_SKETCH, multiple=True)],
    params=[Param("ruled", "bool", "ruled", False, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "_loft([{sections}], {ruled})",
                   "builder": "loft(ruled={ruled})"},
    description="Loft a solid through an ordered list of sections. Wire several "
                "sketches OR a single list (e.g. ToPlane over Divide Curve frames) "
                "for a variable-section solid. `ruled` = straight skin between "
                "sections instead of a smooth one."))

register(NodeDef("Sweep", "operations", "Sweep",
    inputs=[Socket("section", WIRE_SKETCH), Socket("path", WIRE_CURVE)],
    params=[Param("is_frenet", "bool", "is_frenet", False, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "_sweep({section}, {path}, {is_frenet})"},
    description="Sweep a profile (section) along a path curve into a solid. The "
                "profile is auto-seated perpendicular to the path start. `is_frenet` "
                "uses the curve's natural frame (for twisting paths)."))

register(NodeDef("Thicken", "operations", "Thicken",
    inputs=[Socket("sketch", WIRE_SKETCH)],
    params=[_f("thickness", 2.5, 0.1, 100)],
    outputs=_geo(),
    code_template={"algebra": "thicken({sketch}, {thickness})"},
    description="Give a surface/sketch a thickness."))

register(NodeDef("MakeFace", "operations", "Make Face",
    inputs=[Socket("edges", WIRE_CURVE)],
    outputs=_sk(),
    code_template={"algebra": "make_face({edges})", "builder": "make_face()"},
    description="Build a face from a closed wire."))

register(NodeDef("PopulateGeometry", "operations", "Populate Geometry",
    inputs=[Socket("region", WIRE_SKETCH, required=False)],
    params=[_i("count", 40, 1, 5000, label="count"),
            _i("seed", 1, 0, 100000, label="seed", widget="input"),
            _f("width", 100, 1, 5000, label="width", widget="input"),
            _f("height", 100, 1, 5000, label="height", widget="input")],
    outputs=[Socket("points", WIRE_VECTOR)],
    code_template={"algebra": "_populate({count}, {seed}, {width}, {height}, {region})"},
    description="Scatter `count` random points (z=0), deterministic per `seed`, "
                "inside the `region` rectangle if wired (else a width x height "
                "box). Feed into Voronoi2D, an origin, or Move."))

register(NodeDef("Voronoi2D", "operations", "Voronoi 2D",
    inputs=[Socket("points", WIRE_VECTOR, list_access=True),
            Socket("boundary", WIRE_SKETCH, required=False)],
    params=[_f("scale", 0.9, 0.05, 1.0, 0.05, label="scale")],
    outputs=[Socket("cells", WIRE_SKETCH)],
    code_template={"algebra": "_voronoi2d({points}, {boundary}, {scale})"},
    description="Polygonal Voronoi cells from a set of `points`, clipped to the "
                "`boundary` rectangle (a Rectangle sketch). `scale` shrinks each "
                "cell toward its centre to leave a frame (1.0 = cells share "
                "edges). Fan out downstream (Extrude / MapToSurface) per cell."))

register(NodeDef("DivideSurface", "operations", "Divide Surface",
    inputs=[Socket("surface", WIRE_GEOMETRY)],
    params=[_i("u_count", 6, 1, 500, label="u"), _i("v_count", 6, 1, 500, label="v")],
    outputs=[Socket("points", WIRE_VECTOR)],
    code_template={"algebra": "_divide_surface({surface}, {u_count}, {v_count})"},
    description="Sample a u x v grid of points on a surface (the largest face of "
                "the input). Feed `points` into a primitive's origin or Move to "
                "scatter geometry across the surface."))

register(NodeDef("MapToSurface", "operations", "Map To Surface",
    inputs=[Socket("shapes", WIRE_SKETCH, list_access=True),
            Socket("surface", WIRE_GEOMETRY),
            Socket("boundary", WIRE_SKETCH, required=False)],
    params=[_f("width", 100, 1, 5000, label="width", widget="input"),
            _f("height", 100, 1, 5000, label="height", widget="input")],
    outputs=[Socket("mapped", WIRE_SKETCH)],
    code_template={"algebra": "_map_to_surface({shapes}, {surface}, {boundary}, {width}, {height})"},
    description="Wrap flat 2D shapes onto a surface: each shape's centroid maps "
                "from the 2D domain (the `boundary` rectangle, else width x "
                "height) to the surface's UV, and is re-seated on the tangent "
                "plane there. Then Extrude (along the normal) for radial "
                "cutters/bosses. Use the SAME boundary as Voronoi2D."))

# ===========================================================================
# 4. Booleans (CSG)
# ===========================================================================
register(NodeDef("Union", "boolean", "Union",
    inputs=[Socket("a", WIRE_GEOMETRY), Socket("b", WIRE_GEOMETRY)],
    outputs=_geo(),
    code_template={"algebra": "({a} + {b})"},
    description="Boolean union A + B."))

register(NodeDef("Subtract", "boolean", "Subtract",
    # `b` is list-access: a LIST of tools (e.g. a fanned set of cutters) is
    # subtracted wholesale (A - [b0, b1, …]), not fanned. `a` still fans.
    inputs=[Socket("a", WIRE_GEOMETRY), Socket("b", WIRE_GEOMETRY, list_access=True)],
    outputs=_geo(),
    code_template={"algebra": "({a} - {b})"},
    description="Boolean difference A - B. B may be a single shape or a whole "
                "list of tools (all subtracted)."))

register(NodeDef("Intersect", "boolean", "Intersect",
    inputs=[Socket("a", WIRE_GEOMETRY), Socket("b", WIRE_GEOMETRY)],
    outputs=_geo(),
    code_template={"algebra": "({a} & {b})"},
    description="Boolean intersection A & B."))

register(NodeDef("BooleanMulti", "boolean", "Union (N)",
    inputs=[Socket("shapes", WIRE_GEOMETRY, multiple=True)],
    outputs=_geo(),
    code_template={"algebra": "Part() + [{shapes}]"},
    description="Fuse an arbitrary number of shapes."))

# ===========================================================================
# 5. Modifiers
# ===========================================================================
register(NodeDef("Fillet", "modifiers", "Fillet",
    inputs=[Socket("part", WIRE_GEOMETRY)] + _pin("radius"),
    params=[_f("radius", 2, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "fillet({part}.edges(), radius={radius})"},
    description="Round all edges of a part."))

register(NodeDef("Chamfer", "modifiers", "Chamfer",
    inputs=[Socket("part", WIRE_GEOMETRY)] + _pin("length"),
    params=[_f("length", 1.5, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "chamfer({part}.edges(), length={length})"},
    description="Bevel all edges of a part."))

# ===========================================================================
# 5b. Sub-shape selection (pick edges/faces in the 3D picker, operate on them)
# ===========================================================================
# SelectEdge carries the picked set in params["selection"] = {kind, indices,
# sigs}; the transpiler special-cases it (see _emit_select) and resolves the
# set at run time by nearest-anchor matching, so it survives param tweaks.
register(NodeDef("SelectEdge", "select", "Select Edge",
    inputs=[Socket("geometry", WIRE_GEOMETRY)],
    outputs=[Socket("selection", WIRE_SELECTION)],
    code_template={"algebra": ""},  # handled by the transpiler, not a template
    description="Pick specific edges of a shape in the 3D picker; outputs the "
                "selected edges for a targeted operation."))

register(NodeDef("SelectFace", "select", "Select Face",
    inputs=[Socket("geometry", WIRE_GEOMETRY)],
    outputs=[Socket("selection", WIRE_SELECTION)],
    code_template={"algebra": ""},  # handled by the transpiler, not a template
    description="Pick specific faces of a shape in the 3D picker; outputs the "
                "selected faces for a targeted operation."))

register(NodeDef("SelectVertex", "select", "Select Vertex",
    inputs=[Socket("geometry", WIRE_GEOMETRY)],
    outputs=[Socket("selection", WIRE_SELECTION)],
    code_template={"algebra": ""},  # handled by the transpiler, not a template
    description="Pick specific vertices of a shape in the 3D picker; outputs the "
                "selected vertices."))

register(NodeDef("FilletSelectedEdges", "modifiers", "Fillet Selected Edges",
    inputs=[Socket("part", WIRE_GEOMETRY), Socket("edges", WIRE_SELECTION)],
    params=[_f("radius", 2, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "fillet({edges}, radius={radius})"},
    description="Round only the edges chosen by a Select Edge node."))

register(NodeDef("ChamferSelectedEdges", "modifiers", "Chamfer Selected Edges",
    inputs=[Socket("part", WIRE_GEOMETRY), Socket("edges", WIRE_SELECTION)],
    params=[_f("length", 1.5, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "chamfer({edges}, length={length})"},
    description="Bevel only the edges chosen by a Select Edge node."))

register(NodeDef("ExtrudeSelectedFace", "modifiers", "Push / Pull Face",
    inputs=[Socket("part", WIRE_GEOMETRY), Socket("faces", WIRE_SELECTION)],
    params=[_f("amount", 5, -200, 200)],
    outputs=_geo(),
    code_template={"algebra": "_pushpull({part}, {faces}, {amount})"},
    description="Push/pull the faces chosen by a Select Face node along their "
                "normal: positive grows a boss, negative carves a pocket."))

register(NodeDef("Shell", "modifiers", "Shell",
    inputs=[Socket("part", WIRE_GEOMETRY),
            Socket("thickness", WIRE_DATA, required=False)],
    params=[_f("thickness", 1, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "offset({part}, amount=-{thickness}, openings={part}.faces().sort_by(Axis.Z)[-1])"},
    description="Hollow out a part with the given wall thickness, leaving the top (+Z) face open."))

register(NodeDef("Offset", "modifiers", "Offset",
    inputs=[Socket("shape", WIRE_GEOMETRY)] + _pin("amount"),
    params=[_f("amount", 2, -100, 100)],
    outputs=_geo(),
    code_template={"algebra": "offset({shape}, amount={amount})"},
    description="Offset a shape outward/inward."))

register(NodeDef("Section", "modifiers", "Section",
    inputs=[Socket("shape", WIRE_GEOMETRY),
            Socket("plane", WIRE_PLANE, required=False)],
    outputs=_sk(),
    code_template={"algebra": "_section({shape}, {plane})"},
    description="Planar cross-section of a shape, cut by the Plane wired into "
                "`plane` (e.g. from a Bounding Plane node, which sets both "
                "orientation and position). Defaults to the XY plane through the "
                "global origin when nothing is wired."))

# ===========================================================================
# 6. Transforms
# ===========================================================================
register(NodeDef("Move", "transform", "Move",
    inputs=[Socket("shape", WIRE_GEOMETRY),
            Socket("offset", WIRE_VECTOR, required=False)],
    params=[_f("x", 0, -500, 500), _f("y", 0, -500, 500), _f("z", 0, -500, 500)],
    outputs=_geo(),
    gizmo={"kind": "translate", "binds": ["x", "y", "z"],
           "anchor": "preview", "lock": ["offset"]},
    code_template={"algebra": "_move({shape}, {offset}, {x}, {y}, {z})"},
    description="Translate a shape (or a plane). Wire a vector into `offset` to "
                "drive the position; feed a LIST of vectors to scatter the shape "
                "to each position (one moved copy per vector)."))

register(NodeDef("Rotate", "transform", "Rotate",
    inputs=[Socket("shape", WIRE_GEOMETRY)] + _pin("angle"),
    params=[_f("angle", 90, -360, 360),
            Param("axis", "select", "axis", "Z", widget="select",
                  options=["X", "Y", "Z"],
                  code_map={"X": "Axis.X", "Y": "Axis.Y", "Z": "Axis.Z"})],
    outputs=_geo(),
    gizmo={"kind": "rotate", "binds": ["angle"], "axisParam": "axis",
           "anchor": "origin", "lock": ["angle"]},
    code_template={"algebra": "_rotate({shape}, {axis}, {angle})"},
    description="Rotate a shape (or a plane) around a global axis."))

register(NodeDef("Scale", "transform", "Scale",
    inputs=[Socket("shape", WIRE_GEOMETRY)] + _pin("factor"),
    params=[_f("factor", 2, 0.01, 100)],
    outputs=_geo(),
    gizmo={"kind": "scale", "binds": ["factor"], "anchor": "preview", "lock": ["factor"]},
    code_template={"algebra": "scale({shape}, {factor})"},
    description="Uniform scale (about the shape's own centre)."))

register(NodeDef("ToPlane", "transform", "To Plane",
    inputs=[Socket("shape", WIRE_SKETCH), Socket("plane", WIRE_PLANE)],
    outputs=_sk(),
    code_template={"algebra": "_to_plane({shape}, {plane})"},
    description="Re-seat a 2D profile onto a plane/frame (its local XY comes to lie "
                "in the plane). Wire a list of frames (Divide Curve) — and optionally "
                "a list of profiles — and it zips one profile per frame, oriented "
                "perpendicular to the curve, ready to Loft."))

register(NodeDef("Mirror", "transform", "Mirror",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[Param("plane", "select", "plane", "XZ", widget="select",
                  options=["XY", "XZ", "YZ"],
                  code_map={"XY": "Plane.XY", "XZ": "Plane.XZ", "YZ": "Plane.YZ"})],
    outputs=_geo(),
    code_template={"algebra": "mirror({shape}, {plane})"},
    description="Mirror across a plane."))

register(NodeDef("ArrayLinear", "transform", "Linear Array",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[_i("count", 3, 1, 200), _f("dx", 20, -500, 500),
            _f("dy", 0, -500, 500), _f("dz", 0, -500, 500)],
    outputs=[Socket("result", WIRE_GEOMETRY)],
    code_template={"algebra": "[Pos({dx}*i, {dy}*i, {dz}*i) * {shape} for i in range({count})]"},
    description="Repeat a shape along a vector -> list."))

# ===========================================================================
# 7. Planes
# ===========================================================================
register(NodeDef("PlaneOrigin", "plane", "Plane (origin)",
    params=[Param("plane", "select", "plane", "XY", widget="select",
                  options=["XY", "XZ", "YZ"],
                  code_map={"XY": "Plane.XY", "XZ": "Plane.XZ", "YZ": "Plane.YZ"})],
    outputs=[Socket("plane", WIRE_PLANE)],
    code_template={"algebra": "{plane}"},
    description="A base plane."))

register(NodeDef("BoundingPlane", "plane", "Bounding Plane",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[Param("orientation", "select", "orientation", "XY", widget="select",
                  options=["XY", "XZ", "YZ"],
                  code_map={"XY": "Plane.XY", "XZ": "Plane.XZ", "YZ": "Plane.YZ"}),
            _f("position", 0.5, 0.0, 1.0, 0.01, label="position")],
    outputs=[Socket("plane", WIRE_PLANE)],
    code_template={"algebra": "_bbox_plane({shape}, {orientation}, {position})"},
    description="A Plane parallel to the chosen base plane (XY/XZ/YZ), centred on "
                "the input geometry's bounding box and slid along its normal by "
                "position (0=min, 0.5=centre, 1=max). A real plane on the wire — "
                "feed it to Section or any plane input; works for any geometry."))

register(NodeDef("DeconstructPlane", "plane", "Plane Origin",
    inputs=[Socket("plane", WIRE_PLANE)],
    outputs=[Socket("point", WIRE_VECTOR)],
    code_template={"algebra": "_plane_origin({plane})"},
    description="The origin point of a plane/frame. Wire a list of frames (e.g. "
                "from Divide Curve) to get the points along the curve (fans out)."))

# ===========================================================================
# 8. Vectors & points
# ===========================================================================
register(NodeDef("Vector", "vector", "Vector",
    inputs=[Socket("x", WIRE_DATA, required=False),
            Socket("y", WIRE_DATA, required=False),
            Socket("z", WIRE_DATA, required=False)],
    params=[_f("x", 0, -1000, 1000, widget="input"),
            _f("y", 0, -1000, 1000, widget="input"),
            _f("z", 0, -1000, 1000, widget="input")],
    outputs=[Socket("vector", WIRE_VECTOR)],
    gizmo={"kind": "translate", "binds": ["x", "y", "z"],
           "anchor": "params", "lock": ["x", "y", "z"]},
    code_template={"algebra": "Vector({x}, {y}, {z})"},
    description="A 3D vector. Each component is a widget OR an input; wire a list "
                "of numbers into a component to build a list of vectors."))

# ===========================================================================
# 9. Lists / data trees (subset)
# ===========================================================================
register(NodeDef("ListCreate", "data", "List",
    inputs=[Socket("items", WIRE_DATA, required=False, multiple=True)],
    outputs=_data(),
    code_template={"algebra": "[{items}]"},
    description="Collect inputs into a list."))

register(NodeDef("ListRange", "data", "Range",
    params=[_f("start", 0, widget="input"), _i("count", 5, 0, 100000),
            _f("step", 1, widget="input")],
    outputs=_data(),
    code_template={"algebra": "[{start} + i*{step} for i in range({count})]"},
    description="Numeric series start, start+step, ..."))

register(NodeDef("ListItem", "data", "List Item",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    params=[_i("index", 0, 0, 100000, widget="input")],
    outputs=_data(),
    code_template={"algebra": "{list}[{index}]"},
    description="Pick an item by index (negative counts from the end)."))

register(NodeDef("ListLength", "data", "List Length",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    outputs=_data(),
    code_template={"algebra": "len({list})"},
    description="Number of items in a list."))

register(NodeDef("ListFirst", "data", "List First",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    outputs=_data(),
    code_template={"algebra": "{list}[0]"},
    description="The first item."))

register(NodeDef("ListLast", "data", "List Last",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    outputs=_data(),
    code_template={"algebra": "{list}[-1]"},
    description="The last item."))

register(NodeDef("ListReverse", "data", "List Reverse",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    outputs=_data(),
    code_template={"algebra": "list(reversed({list}))"},
    description="Reverse the order of a list."))

register(NodeDef("ListSlice", "data", "List Slice",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    params=[_i("start", 0, -100000, 100000, widget="input"),
            _i("stop", 0, -100000, 100000, widget="input"),
            _i("step", 1, -100000, 100000, widget="input")],
    outputs=_data(),
    code_template={"algebra": "_slice({list}, {start}, {stop}, {step})"},
    description="Sub-list list[start:stop:step]. stop=0 means 'to the end'."))

register(NodeDef("ListFlatten", "data", "List Flatten",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    outputs=_data(),
    code_template={"algebra": "_flatten({list})"},
    description="Flatten nested lists into one flat list."))

register(NodeDef("ListSort", "data", "List Sort",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    params=[Param("by", "select", "by", "X", widget="select",
                  options=["X", "Y", "Z", "length", "area", "volume", "radius", "value"])],
    outputs=_data(),
    code_template={"algebra": "_sort({list}, {by})"},
    description="Sort a list. Shapes sort by position (X/Y/Z) or metric "
                "(length/area/volume/radius); points by component; numbers by value."))

register(NodeDef("Concat", "data", "Concat",
    inputs=[Socket("a", WIRE_DATA, list_access=True),
            Socket("b", WIRE_DATA, list_access=True)],
    outputs=_data(),
    code_template={"algebra": "(list({a}) + list({b}))"},
    description="Join two lists end to end."))

# --- domains, series, remap (parametric glue) ---
register(NodeDef("ConstructDomain", "data", "Domain",
    inputs=_pin("min", "max"),
    params=[_f("min", 0, widget="input"), _f("max", 1, widget="input")],
    outputs=_data("domain"),
    code_template={"algebra": "[{min}, {max}]"},
    description="A numeric domain [min, max] (a 2-item list). Feed into Remap or "
                "Divide Domain."))

register(NodeDef("Series", "data", "Series",
    inputs=_pin("start", "step", "count"),
    params=[_f("start", 0, widget="input"), _f("step", 1, widget="input"),
            _i("count", 10, 0, 100000)],
    outputs=_data(),
    code_template={"algebra": "[{start} + i*{step} for i in range(int({count}))]"},
    description="Arithmetic series: start, start+step, … (count items). Drives "
                "params downstream (e.g. into Circle.radius) — fans out."))

register(NodeDef("DivideDomain", "data", "Divide Domain",
    inputs=[Socket("domain", WIRE_DATA, list_access=True)] + _pin("count"),
    params=[_i("count", 10, 1, 100000)],
    outputs=_data(),
    code_template={"algebra": "_divide_domain({domain}, {count})"},
    description="N values evenly spanning a domain [min, max], endpoints "
                "inclusive. Fans out downstream."))

register(NodeDef("Remap", "data", "Remap",
    inputs=[Socket("value", WIRE_DATA),
            Socket("source", WIRE_DATA, required=False, list_access=True),
            Socket("target", WIRE_DATA, required=False, list_access=True)],
    params=[_f("source_min", 0, widget="input"), _f("source_max", 1, widget="input"),
            _f("target_min", 0, widget="input"), _f("target_max", 10, widget="input")],
    outputs=_data(),
    code_template={"algebra": "_remap({value}, {source}, {target}, {source_min}, "
                              "{source_max}, {target_min}, {target_max})"},
    description="Remap value(s) from a source range to a target range. Wire a "
                "[min,max] domain into source/target to override the widgets. Fans "
                "out over a list of values (Series → Remap → radius)."))

register(NodeDef("Bounds", "data", "Bounds",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    outputs=_data("domain"),
    code_template={"algebra": "_bounds({list})"},
    description="The [min, max] domain spanning a list of numbers."))

# ===========================================================================
# 10. Math
# ===========================================================================
def _binop(type_, label, op):
    register(NodeDef(type_, "math", label,
        inputs=[Socket("a", WIRE_DATA), Socket("b", WIRE_DATA)],
        outputs=_data(),
        code_template={"algebra": f"({{a}} {op} {{b}})"},
        description=f"{label}."))


_binop("Add", "Add", "+")
_binop("SubtractNum", "Subtract (num)", "-")
_binop("Multiply", "Multiply", "*")
_binop("Divide", "Divide", "/")
_binop("Power", "Power", "**")

register(NodeDef("Clamp", "math", "Clamp",
    inputs=[Socket("x", WIRE_DATA)],
    params=[_f("min", 0, widget="input"), _f("max", 10, widget="input")],
    outputs=_data(),
    code_template={"algebra": "max({min}, min({x}, {max}))"},
    description="Clamp x to [min, max]."))

register(NodeDef("Expression", "math", "Expression",
    inputs=[Socket("x", WIRE_DATA, required=False), Socket("y", WIRE_DATA, required=False)],
    params=[Param("expr", "str", "expr", "x + y", widget="input", raw=True)],
    outputs=_data(),
    code_template={"algebra": "({expr})"},
    description="Evaluate an expression of inputs x, y."))

# ===========================================================================
# 11. Panels / inspection
# ===========================================================================
register(NodeDef("Panel", "panel", "Panel",
    inputs=[Socket("value", WIRE_DATA, required=False, list_access=True)],
    params=[Param("text", "str", "text", "", widget="text"),
            Param("mode", "select", "mode", "friendly", widget="select",
                  options=["friendly", "json", "build123d"])],
    outputs=_data("value"),
    code_template={"algebra": "_panel({node_id!r}, {value}, {text}, {mode})"},
    description="Dual-mode Panel. Wire a value to INSPECT it (multi-line, "
                "list-aware, passes through). Leave it unwired and type into "
                "`text` to use it as a data SOURCE — one item per line (several "
                "lines = a list that fans out). `mode` picks the syntax: "
                "friendly ('0,0,0' -> Vector), json, or build123d (eval)."))

register(NodeDef("BoundingBox", "panel", "Bounding Box",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    outputs=[Socket("box", WIRE_GEOMETRY)],
    code_template={"algebra": "{shape}.bounding_box()"},
    description="Bounding box of a shape."))

# ===========================================================================
# 12. Inputs / parameters
# ===========================================================================
register(NodeDef("NumberSlider", "input", "Number Slider",
    params=[_f("value", 5, 0, 100, widget="slider")],
    outputs=_data(),
    code_template={"algebra": "{value}"},
    description="A float value."))

register(NodeDef("IntegerSlider", "input", "Integer Slider",
    params=[_i("value", 5, 0, 100, widget="slider")],
    outputs=_data(),
    code_template={"algebra": "{value}"},
    description="An int value."))

register(NodeDef("NumberInput", "input", "Number",
    params=[_f("value", 10, widget="input")],
    outputs=_data(),
    code_template={"algebra": "{value}"},
    description="A typed float value."))

register(NodeDef("BooleanToggle", "input", "Boolean",
    params=[Param("value", "bool", "value", True, widget="checkbox")],
    outputs=_data(),
    code_template={"algebra": "{value}"},
    description="A boolean value."))

register(NodeDef("StringInput", "input", "String",
    params=[Param("value", "str", "value", "label", widget="input")],
    outputs=_data(),
    code_template={"algebra": "{value}"},
    description="A string value."))

# ===========================================================================
# 13. Export / IO
# ===========================================================================
register(NodeDef("ExportSTEP", "export", "Export STEP",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[Param("path", "str", "path", "output.step", widget="input")],
    outputs=[],
    code_template={"algebra": "export_step({shape}, {path})"},
    description="Write the shape to a STEP file."))

register(NodeDef("ExportSTL", "export", "Export STL",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[Param("path", "str", "path", "output.stl", widget="input")],
    outputs=[],
    code_template={"algebra": "export_stl({shape}, {path})"},
    description="Write the shape to an STL file."))

# ===========================================================================
# 14. Group nodes (builder mode)
# ===========================================================================
register(NodeDef("BuildPart", "group", "Build Part",
    outputs=_geo("part"),
    is_group=True, group_kind="part",
    code_template={"builder": "with BuildPart() as {ctx}:"},
    description="Builder-mode part context; children run inside it."))

register(NodeDef("BuildSketch", "group", "Build Sketch",
    inputs=[Socket("plane", WIRE_PLANE, required=False)],
    outputs=_sk("sketch"),
    is_group=True, group_kind="sketch",
    code_template={"builder": "with BuildSketch({plane}) as {ctx}:"},
    description="Builder-mode sketch context; children run inside it."))

# ===========================================================================
# 15. CodeBlock — the universal node
# ===========================================================================
register(NodeDef("CodeBlock", "code", "Code Block",
    inputs=[Socket(f"in_{i}", WIRE_DATA, required=False) for i in range(6)],
    params=[Param("code", "str", "code",
                  "result = Box(in_0 or 10, in_1 or 10, 5)", widget="text", raw=True)],
    outputs=_data("result"),
    code_template={},  # handled specially by the transpiler
    description="Arbitrary build123d code; must assign `result`. Inputs in_0..in_5 "
                "(all optional) are available as variables; unconnected ones are None. "
                "Declare parameters with a `#@param` annotation — e.g. "
                "`teeth = 12  #@param int min=6 max=40` — to get a live knob on the "
                "node, an editable span in the code view, and a same-named input "
                "socket that fans out (Range -> CodeBlock.teeth = one result each). "
                "Overrides live in the `_cb` param namespace; the source is never rewritten."))
