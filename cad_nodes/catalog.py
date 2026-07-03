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
# Wire types + compatibility — the single source of truth is cads_nodes/casts.py.
# Re-exported here so existing `catalog.WIRE_*` / `wires_compatible` keep working;
# WIRE_COMPATIBLE is now DERIVED from the cast registry (no hand-maintained copy).
# ---------------------------------------------------------------------------
from .casts import (  # noqa: E402,F401
    WIRE_SOLID, WIRE_SURFACE, WIRE_CURVE, WIRE_DATA, WIRE_TREE,
    WIRE_PLANE, WIRE_VECTOR, WIRE_SELECTION, WIRE_TYPES,
    wires_compatible, cast_helper, build_compatible, build_input_accepts,
)

WIRE_COMPATIBLE: dict[str, set[str]] = build_compatible()


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
    # Per-socket widening: extra OUTPUT wire types this input accepts, BEYOND what
    # the global WIRE_COMPATIBLE table allows. Lets one polymorphic input (e.g. a
    # transform / Select that works on any shape at runtime) take a curve without
    # globally permitting curve -> every geometry input. See PLAN_DATA_PROTOCOL.md.
    accepts: list[str] = field(default_factory=list)
    # raw=True: the NODE handles any type coercion itself (e.g. Extrude/Loft pick
    # face vs outline by their `solid` flag), so the transpiler must NOT auto-apply
    # a boundary cast to this input — it would strip the info the node needs.
    raw: bool = False
    # Advisory sub-type within a wire type, for legend/disambiguation only (NOT
    # validation — connections still gate on wire_type). Splits the opaque `data`
    # bus (number/integer/boolean/text/list/domain) and the `curve` family
    # (line/polyline/spline/arc). See PLAN_DATA_PROTOCOL.md §4d (tag, don't split).
    subtype: str = ""


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
    # Soft cap: the slider drags only up to soft_max (a sane default range), while
    # a typed value can still reach the hard `max`. Guards against an accidental
    # scrub blowing up fan-out (e.g. ListRange count) without limiting power use.
    soft_max: Optional[float] = None


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
    # output_follows: name of the INPUT socket whose effective wire type this
    # node's FIRST output mirrors (type-preserving / polymorphic nodes — e.g. a
    # transform: move a curve -> get a curve back, not a generic geometry). The
    # graph validator and the editor resolve it up the chain; the runtime already
    # preserves the kind. See PLAN_DATA_PROTOCOL.md.
    output_follows: Optional[str] = None
    # subtype_follows: name of the INPUT socket whose effective *subtype* this
    # node's FIRST output inherits when its own subtype is unset — i.e. a
    # pass-through that preserves the advisory tag (a Curve/Panel container, a
    # transform). Defaults to `output_follows` when None. Advisory only (legend);
    # never gates a connection. See PLAN_DATA_PROTOCOL.md §4d.
    subtype_follows: Optional[str] = None

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
        raise KeyError(f"Unknown node type: {node_type!r}") from None


def as_json() -> list[dict]:
    """Serialise the whole catalog (for `cad://nodes` / frontend registry)."""
    return [asdict(REGISTRY[t]) for t in sorted(REGISTRY)]


# Convenience shorthands for terse definitions below ------------------------
def _geo(name="result"):
    return [Socket(name, WIRE_SOLID)]


def _sk(name="result"):
    return [Socket(name, WIRE_SURFACE)]


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


def _f(name, default=0.0, lo=None, hi=None, step=0.5, label="", widget="slider",
       soft_max=None):
    return Param(name, "float", label or name, default, lo, hi, step, widget,
                 soft_max=soft_max)


def _i(name, default=0, lo=None, hi=None, label="", widget="slider",
       soft_max=None):
    return Param(name, "int", label or name, default, lo, hi, 1, widget,
                 soft_max=soft_max)


# ===========================================================================
# 1. Primitives 3D
# ===========================================================================
register(NodeDef("Box", "primitives_3d", "Box",
    inputs=_origin_in() + _pin("width", "height", "depth"),
    params=[_f("width", 10, 0.1, 500), _f("height", 10, 0.1, 500), _f("depth", 10, 0.1, 500),
            Param("centered", "bool", "centered", True, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "Box({width}, {height}, {depth}, align=_al({centered}))"},
    description="Solid box of given width, height, depth. `centered` aligns it on "
                "its centre; turn it off to seat its corner at the origin."))

register(NodeDef("Cylinder", "primitives_3d", "Cylinder",
    inputs=_origin_in() + _pin("radius", "height"),
    params=[_f("radius", 5, 0.1, 500), _f("height", 20, 0.1, 500),
            _f("arc", 360, 1, 360, label="arc"),
            Param("centered", "bool", "centered", True, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "Cylinder({radius}, {height}, arc_size={arc}, align=_al({centered}))"},
    description="Solid cylinder. `arc` < 360 makes a wedge / pie slice. `centered` "
                "aligns it on its centre; off seats its base at the origin."))

register(NodeDef("Sphere", "primitives_3d", "Sphere",
    inputs=_origin_in() + _pin("radius"),
    params=[_f("radius", 10, 0.1, 500), _f("arc", 360, 1, 360, label="arc")],
    outputs=_geo(),
    code_template={"algebra": "Sphere({radius}, arc_size3={arc})"},
    description="Solid sphere. `arc` < 360 sweeps only part of the way around "
                "(a wedge)."))

register(NodeDef("Cone", "primitives_3d", "Cone",
    inputs=_origin_in() + _pin("bottom_radius", "top_radius", "height"),
    params=[_f("bottom_radius", 5, 0, 500), _f("top_radius", 0, 0, 500), _f("height", 15, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Cone({bottom_radius}, {top_radius}, {height})"},
    description="Truncated cone / frustum."))

register(NodeDef("Torus", "primitives_3d", "Torus",
    inputs=_origin_in() + _pin("major_radius", "minor_radius"),
    params=[_f("major_radius", 15, 0.1, 500), _f("minor_radius", 3, 0.1, 500),
            _f("arc", 360, 1, 360, label="arc")],
    outputs=_geo(),
    code_template={"algebra": "Torus({major_radius}, {minor_radius}, major_angle={arc})"},
    description="Torus / ring. `arc` < 360 makes a partial ring (a C-shape)."))

register(NodeDef("Wedge", "primitives_3d", "Wedge",
    inputs=_origin_in() + _pin("xsize", "ysize", "zsize"),
    params=[_f("xsize", 10, 0.1, 500), _f("ysize", 10, 0.1, 500), _f("zsize", 10, 0.1, 500),
            _f("xmin", 2, 0, 500), _f("zmin", 2, 0, 500),
            _f("xmax", 8, 0, 500), _f("zmax", 8, 0, 500)],
    outputs=_geo(),
    code_template={"algebra": "Wedge({xsize}, {ysize}, {zsize}, {xmin}, {zmin}, {xmax}, {zmax})"},
    description="Wedge: a box whose top face shrinks to the rectangle "
                "[xmin..xmax] x [zmin..zmax] (ramps, tapered blocks). Keep "
                "0 <= xmin < xmax <= xsize and likewise for z."))

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
    outputs=_cv(),
    code_template={"algebra": "_outline(Rectangle({width}, {height}))"},
    description="2D rectangle as a closed curve. Feed a Surface (Make Face) node "
                "to fill it into a face."))

register(NodeDef("RoundedRectangle", "primitives_2d", "Rounded Rectangle",
    inputs=_origin_in() + _pin("width", "height", "radius"),
    params=[_f("width", 30, 0.1, 500), _f("height", 20, 0.1, 500), _f("radius", 3, 0, 250)],
    outputs=_cv(),
    code_template={"algebra": "_outline(RectangleRounded({width}, {height}, {radius}))"},
    description="Rectangle with filleted corners, as a closed curve. Feed a Surface "
                "(Make Face) node to fill it into a face."))

register(NodeDef("Circle", "primitives_2d", "Circle",
    inputs=_origin_in() + _pin("radius"),
    params=[_f("radius", 10, 0.1, 500)],
    outputs=_cv(),
    code_template={"algebra": "_outline(Circle({radius}))"},
    description="2D circle as a closed curve. Feed a Surface (Make Face) node to "
                "fill it into a face."))

register(NodeDef("Ellipse", "primitives_2d", "Ellipse",
    inputs=_origin_in() + _pin("x_radius", "y_radius"),
    params=[_f("x_radius", 8, 0.1, 500), _f("y_radius", 5, 0.1, 500)],
    outputs=_cv(),
    code_template={"algebra": "_outline(Ellipse({x_radius}, {y_radius}))"},
    description="2D ellipse as a closed curve. Feed a Surface (Make Face) node to "
                "fill it into a face."))

register(NodeDef("Polygon", "primitives_2d", "Regular Polygon",
    inputs=_origin_in() + _pin("radius", "sides"),
    params=[_f("radius", 10, 0.1, 500), _i("sides", 6, 3, 64),
            _f("rotation", 0, -360, 360, label="rotation"),
            Param("major", "bool", "to vertices", True, widget="checkbox")],
    outputs=_cv(),
    code_template={"algebra": "_outline(RegularPolygon({radius}, {sides}, major_radius={major}, rotation={rotation}))"},
    description="Regular N-sided polygon, as a closed curve. `to vertices` (default) "
                "measures `radius` to the corners (circumscribed); turn it off to "
                "measure to the edge midpoints (inscribed). `rotation` spins it. "
                "Feed a Surface (Make Face) node to fill it into a face."))

register(NodeDef("Star", "primitives_2d", "Star",
    inputs=_origin_in() + _pin("outer_radius", "inner_radius", "points"),
    params=[_f("outer_radius", 10, 0.1, 500), _f("inner_radius", 5, 0.1, 500),
            _i("points", 5, 2, 64)],
    outputs=_cv(),
    code_template={"algebra": "_star({outer_radius}, {inner_radius}, {points})"},
    description="Star with `points` spikes alternating between the outer and "
                "inner radius, as a closed curve. Feed a Surface (Make Face) node "
                "to fill it into a face."))

register(NodeDef("Slot", "primitives_2d", "Slot",
    inputs=_origin_in() + _pin("separation", "height"),
    params=[_f("separation", 20, 0.1, 500, label="separation"),
            _f("height", 6, 0.1, 500),
            _f("rotation", 0, -360, 360, label="rotation")],
    outputs=_cv(),
    code_template={"algebra": "_outline(SlotCenterToCenter({separation}, {height}, rotation={rotation}))"},
    description="Slot (oblong hole): two half-circles of diameter `height` whose "
                "centres sit `separation` apart, as a closed curve. Feed a Surface "
                "(Make Face) node, then Extrude/Subtract for the classic slot cut."))

register(NodeDef("Text", "primitives_2d", "Text",
    inputs=_origin_in(),
    params=[Param("text", "str", "text", "Hello", widget="input"),
            _f("font_size", 10, 0.1, 500),
            Param("font", "str", "font", "Arial", widget="input"),
            Param("style", "select", "style", "regular", widget="select",
                  options=["regular", "bold", "italic", "bolditalic"],
                  code_map={"regular": "FontStyle.REGULAR", "bold": "FontStyle.BOLD",
                            "italic": "FontStyle.ITALIC", "bolditalic": "FontStyle.BOLDITALIC"}),
            Param("align", "select", "align", "center", widget="select",
                  options=["left", "center", "right"],
                  code_map={"left": "(TextAlign.LEFT, TextAlign.CENTER)",
                            "center": "(TextAlign.CENTER, TextAlign.CENTER)",
                            "right": "(TextAlign.RIGHT, TextAlign.CENTER)"})],
    outputs=_sk(),
    code_template={"algebra": "Text({text}, font_size={font_size}, font={font}, font_style={style}, text_align={align})"},
    description="Text as a 2D sketch. Choose the `font`, `style` (bold / italic) "
                "and horizontal `align`."))

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

register(NodeDef("Bezier", "curves", "Bezier",
    inputs=[Socket("points", WIRE_VECTOR, multiple=True)],
    outputs=_cv(),
    code_template={"algebra": "Bezier(*_curve_points([{points}]))"},
    description="Bezier curve from control points: it starts at the first point, "
                "ends at the last, and is pulled toward (without touching) the "
                "ones in between. Unlike Spline it does NOT pass through them."))

register(NodeDef("Helix", "curves", "Helix",
    inputs=_pin("pitch", "height", "radius"),
    params=[_f("pitch", 5, 0.1, 500), _f("height", 25, 0.1, 500),
            _f("radius", 8, 0.1, 500),
            _f("cone_angle", 0, -60, 60, label="cone angle"),
            Param("lefthand", "bool", "left-handed", False, widget="checkbox")],
    outputs=_cv(),
    code_template={"algebra": "Helix({pitch}, {height}, {radius}, cone_angle={cone_angle}, lefthand={lefthand})"},
    description="Helix around the Z axis: `pitch` is the climb per turn, `height` "
                "the total height. `cone angle` tapers it (a spiral on a cone). "
                "Feed into Sweep.path for springs and thread-like features."))

register(NodeDef("CurveOnPlane", "curves", "Curve on Plane",
    inputs=[Socket("plane", WIRE_PLANE, required=False)],
    params=[Param("plane", "select", "plane", "XY", widget="select",
                  options=["XY", "XZ", "YZ"],
                  code_map={"XY": "Plane.XY", "XZ": "Plane.XZ", "YZ": "Plane.YZ"}),
            Param("mode", "select", "mode", "polyline", widget="select",
                  options=["polyline", "spline"]),
            Param("closed", "bool", "closed", False, widget="checkbox"),
            Param("points", "curve3d", "points", [], widget="curve3d", raw=True)],
    outputs=_cv(),
    code_template={"algebra": "_curve_draw({points}, {mode}, {closed}, {plane})"},
    description="Draw a curve by clicking points directly in the 3D viewport "
                "(✎ Draw button). Points are authored flat in the plane's local "
                "(u, v) and re-seated by `plane`: pick XY/XZ/YZ, or wire in any "
                "Plane (Plane Origin, Bounding Plane, Divide Curve frames…) to "
                "place/orient the curve dynamically. Wiring `plane` locks the "
                "XY/XZ/YZ picker (on the node and in ✎ Draw) since the wire now "
                "drives it."))

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
    inputs=[Socket("sketch", WIRE_SURFACE, raw=True)] + _pin("amount", "taper"),
    params=[_f("amount", 10, 0.1, 500), _f("taper", 0, -45, 45),
            Param("both", "bool", "both", False, widget="checkbox"),
            Param("solid", "bool", "solid", True, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "_extrude({sketch}, {amount}, {taper}, {both}, {solid})",
                   "builder": "extrude(amount={amount}, taper={taper}, both={both})"},
    description="Extrude a 2D profile along its normal. `both` extrudes "
                "symmetrically. `solid` (default) fills the profile for a solid; "
                "turn it off to extrude just the outline into an open surface "
                "(wall / ribbon)."))

register(NodeDef("Revolve", "operations", "Revolve",
    inputs=[Socket("sketch", WIRE_SURFACE, raw=True)] + _pin("angle"),
    params=[_f("angle", 360, 1, 360),
            Param("axis", "select", "axis", "Y", widget="select",
                  options=["X", "Y", "Z"],
                  code_map={"X": "Axis.X", "Y": "Axis.Y", "Z": "Axis.Z"}),
            Param("solid", "bool", "solid", True, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "_revolve({sketch}, {axis}, {angle}, {solid})",
                   "builder": "revolve(axis={axis}, revolution_arc={angle})"},
    description="Revolve a profile around an in-plane axis (X or Y). The profile "
                "must sit off the axis (use a Move node), like a lathe — a "
                "profile crossing the axis is invalid geometry. `solid` (default) "
                "fills the profile for a solid of revolution; turn it off to "
                "revolve just the outline into an open surface."))

register(NodeDef("Loft", "operations", "Loft",
    inputs=[Socket("sections", WIRE_SURFACE, multiple=True, raw=True),
            Socket("start_point", WIRE_VECTOR, required=False),
            Socket("end_point", WIRE_VECTOR, required=False)],
    params=[Param("ruled", "bool", "ruled", False, widget="checkbox"),
            Param("solid", "bool", "solid", True, widget="checkbox"),
            Param("smoothing", "bool", "smoothing", False, widget="checkbox"),
            Param("continuity", "select", "continuity", "C2", widget="select",
                  options=["C0", "C1", "C2", "G1", "G2"]),
            Param("parametrization", "select", "parametrization", "chord",
                  widget="select", options=["uniform", "chord", "centripetal"]),
            Param("max_degree", "int", "max degree", 8, 1, 25, widget="slider")],
    outputs=_geo(),
    code_template={"algebra": "_loft([{sections}], {ruled}, {solid}, {smoothing}, "
                              "{continuity}, {parametrization}, {max_degree}, "
                              "{start_point}, {end_point})",
                   "builder": "loft(ruled={ruled})"},
    description="Loft / skin through an ordered list of sections. Wire several "
                "sketches OR a single list (e.g. ToPlane over Divide Curve frames). "
                "`ruled` = straight skin vs a smooth surface; `solid` caps the ends "
                "(off = open shell of the outlines). Smooth controls: `smoothing` "
                "uses the approximating algorithm, `continuity` (C0–G2) and "
                "`max_degree` shape its quality, `parametrization` "
                "(uniform/chord/centripetal) changes the bulge between sections. "
                "Optional `start_point`/`end_point` loft to a tip (a cone cap)."))

register(NodeDef("Sweep", "operations", "Sweep",
    inputs=[Socket("section", WIRE_SURFACE, raw=True), Socket("path", WIRE_CURVE)],
    params=[Param("is_frenet", "bool", "is_frenet", False, widget="checkbox"),
            Param("solid", "bool", "solid", True, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "_sweep({section}, {path}, {is_frenet}, {solid})"},
    description="Sweep a profile (section) along a path curve. The profile is "
                "auto-seated perpendicular to the path start. `is_frenet` uses the "
                "curve's natural frame (for twisting paths). `solid` (default) "
                "fills the profile for a solid; turn it off to sweep just the "
                "outline into an open surface (a tube wall)."))

register(NodeDef("Thicken", "operations", "Thicken",
    inputs=[Socket("sketch", WIRE_SURFACE, raw=True)],
    params=[_f("thickness", 2.5, 0.1, 100)],
    outputs=_geo(),
    code_template={"algebra": "thicken(_face({sketch}), {thickness})"},
    description="Give a surface/sketch a thickness."))

register(NodeDef("MakeFace", "operations", "Surface (Make Face)",
    inputs=[Socket("edges", WIRE_CURVE)],
    outputs=_sk(),
    code_template={"algebra": "make_face({edges})", "builder": "make_face()"},
    description="Patch / boundary surface: fill a closed curve (Circle, Rectangle, "
                "Polyline, …) into a 2D face, ready for Extrude / Revolve / Loft. "
                "Fans out over a list of curves (one face each)."))

register(NodeDef("PopulateGeometry", "operations", "Populate Geometry",
    inputs=[Socket("region", WIRE_SURFACE, required=False)],
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
            Socket("boundary", WIRE_SURFACE, required=False)],
    params=[_f("scale", 0.9, 0.05, 1.0, 0.05, label="scale")],
    outputs=[Socket("cells", WIRE_SURFACE)],
    code_template={"algebra": "_voronoi2d({points}, {boundary}, {scale})"},
    description="Polygonal Voronoi cells from a set of `points`, clipped to the "
                "`boundary` rectangle (a Rectangle sketch). `scale` shrinks each "
                "cell toward its centre to leave a frame (1.0 = cells share "
                "edges). Fan out downstream (Extrude / MapToSurface) per cell."))

register(NodeDef("DivideSurface", "operations", "Divide Surface",
    inputs=[Socket("surface", WIRE_SOLID)],
    params=[_i("u_count", 6, 1, 500, label="u"), _i("v_count", 6, 1, 500, label="v")],
    outputs=[Socket("points", WIRE_VECTOR)],
    code_template={"algebra": "_divide_surface({surface}, {u_count}, {v_count})"},
    description="Sample a u x v grid of points on a surface (the largest face of "
                "the input). Feed `points` into a primitive's origin or Move to "
                "scatter geometry across the surface."))

register(NodeDef("MapToSurface", "operations", "Map To Surface",
    inputs=[Socket("shapes", WIRE_SURFACE, list_access=True),
            Socket("surface", WIRE_SOLID),
            Socket("boundary", WIRE_SURFACE, required=False)],
    params=[_f("width", 100, 1, 5000, label="width", widget="input"),
            _f("height", 100, 1, 5000, label="height", widget="input")],
    outputs=[Socket("mapped", WIRE_SURFACE)],
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
    inputs=[Socket("a", WIRE_SOLID), Socket("b", WIRE_SOLID)],
    outputs=_geo(),
    code_template={"algebra": "({a} + {b})"},
    description="Boolean union A + B."))

register(NodeDef("Subtract", "boolean", "Subtract",
    # `b` is list-access: a LIST of tools (e.g. a fanned set of cutters) is
    # subtracted wholesale (A - [b0, b1, …]), not fanned. `a` still fans.
    inputs=[Socket("a", WIRE_SOLID), Socket("b", WIRE_SOLID, list_access=True)],
    outputs=_geo(),
    code_template={"algebra": "({a} - {b})"},
    description="Boolean difference A - B. B may be a single shape or a whole "
                "list of tools (all subtracted)."))

register(NodeDef("Intersect", "boolean", "Intersect",
    inputs=[Socket("a", WIRE_SOLID), Socket("b", WIRE_SOLID)],
    outputs=_geo(),
    code_template={"algebra": "({a} & {b})"},
    description="Boolean intersection A & B."))

register(NodeDef("BooleanMulti", "boolean", "Union (N)",
    inputs=[Socket("shapes", WIRE_SOLID, multiple=True)],
    outputs=_geo(),
    code_template={"algebra": "Part() + _flatten([{shapes}])"},
    description="Fuse an arbitrary number of shapes. List inputs (e.g. an "
                "ArrayLinear output) are flattened, so it also collapses a "
                "list of solids into one part."))

# ===========================================================================
# 5. Modifiers
# ===========================================================================
register(NodeDef("Fillet", "modifiers", "Fillet",
    inputs=[Socket("part", WIRE_SOLID)] + _pin("radius"),
    params=[_f("radius", 2, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "fillet({part}.edges(), radius={radius})"},
    description="Round all edges of a part."))

register(NodeDef("Chamfer", "modifiers", "Chamfer",
    inputs=[Socket("part", WIRE_SOLID)] + _pin("length"),
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
    inputs=[Socket("geometry", WIRE_SOLID, accepts=[WIRE_CURVE])],
    outputs=[Socket("selection", WIRE_SELECTION)],
    code_template={"algebra": ""},  # handled by the transpiler, not a template
    description="Pick specific edges of a shape in the 3D picker; outputs the "
                "selected edges for a targeted operation."))

register(NodeDef("SelectFace", "select", "Select Face",
    inputs=[Socket("geometry", WIRE_SOLID, accepts=[WIRE_CURVE])],
    outputs=[Socket("selection", WIRE_SELECTION)],
    code_template={"algebra": ""},  # handled by the transpiler, not a template
    description="Pick specific faces of a shape in the 3D picker; outputs the "
                "selected faces for a targeted operation."))

register(NodeDef("SelectVertex", "select", "Select Vertex",
    inputs=[Socket("geometry", WIRE_SOLID, accepts=[WIRE_CURVE])],
    outputs=[Socket("selection", WIRE_SELECTION)],
    code_template={"algebra": ""},  # handled by the transpiler, not a template
    description="Pick specific vertices of a shape in the 3D picker; outputs the "
                "selected vertices."))

register(NodeDef("FilletSelectedEdges", "modifiers", "Fillet Selected Edges",
    inputs=[Socket("part", WIRE_SOLID), Socket("edges", WIRE_SELECTION)],
    params=[_f("radius", 2, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "fillet({edges}, radius={radius})"},
    description="Round only the edges chosen by a Select Edge node."))

register(NodeDef("ChamferSelectedEdges", "modifiers", "Chamfer Selected Edges",
    inputs=[Socket("part", WIRE_SOLID), Socket("edges", WIRE_SELECTION)],
    params=[_f("length", 1.5, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "chamfer({edges}, length={length})"},
    description="Bevel only the edges chosen by a Select Edge node."))

register(NodeDef("ExtrudeSelectedFace", "modifiers", "Push / Pull Face",
    inputs=[Socket("part", WIRE_SOLID), Socket("faces", WIRE_SELECTION)],
    params=[_f("amount", 5, -200, 200)],
    outputs=_geo(),
    code_template={"algebra": "_pushpull({part}, {faces}, {amount})"},
    description="Push/pull the faces chosen by a Select Face node along their "
                "normal: positive grows a boss, negative carves a pocket."))

register(NodeDef("Shell", "modifiers", "Shell",
    inputs=[Socket("part", WIRE_SOLID),
            Socket("thickness", WIRE_DATA, required=False)],
    params=[_f("thickness", 1, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "_shell({part}, {thickness})"},
    description="Hollow out a solid with the given wall thickness, leaving the top "
                "(+Z) face open. An open surface (e.g. a non-solid Loft) is thickened "
                "into a solid wall instead."))

register(NodeDef("Offset", "modifiers", "Offset",
    inputs=[Socket("shape", WIRE_SOLID)] + _pin("amount"),
    params=[_f("amount", 2, -100, 100),
            Param("kind", "select", "corners", "arc", widget="select",
                  options=["arc", "intersection", "tangent"],
                  code_map={"arc": "Kind.ARC", "intersection": "Kind.INTERSECTION",
                            "tangent": "Kind.TANGENT"})],
    outputs=_geo(),
    code_template={"algebra": "offset({shape}, amount={amount}, kind={kind})"},
    description="Offset a shape outward/inward. `corners` sets how convex corners "
                "are filled: arc (rounded), intersection (sharp), or tangent."))

register(NodeDef("Section", "modifiers", "Section",
    inputs=[Socket("shape", WIRE_SOLID),
            Socket("plane", WIRE_PLANE, required=False)],
    outputs=_sk(),
    code_template={"algebra": "_section({shape}, {plane})"},
    description="Planar cross-section of a shape, cut by the Plane wired into "
                "`plane` (e.g. from a Bounding Plane node, which sets both "
                "orientation and position). Defaults to the XY plane through the "
                "global origin when nothing is wired."))

register(NodeDef("Split", "modifiers", "Split",
    inputs=[Socket("shape", WIRE_SOLID),
            Socket("plane", WIRE_PLANE, required=False)],
    params=[Param("keep", "select", "keep", "top", widget="select",
                  options=["top", "bottom", "both"],
                  code_map={"top": "Keep.TOP", "bottom": "Keep.BOTTOM",
                            "both": "Keep.BOTH"})],
    outputs=_geo(),
    code_template={"algebra": "_split({shape}, {plane}, {keep})"},
    description="Cut a shape in two with a plane and keep the side above it "
                "(`top`), below it (`bottom`) or `both` halves. Wire a Bounding "
                "Plane / Plane node to place the cut; defaults to XY through the "
                "origin. Unlike Section (a 2D slice), Split keeps solid geometry."))

# ===========================================================================
# 6. Transforms
# ===========================================================================
register(NodeDef("Move", "transform", "Move",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_CURVE]),
            Socket("offset", WIRE_VECTOR, required=False)],
    params=[_f("x", 0, -500, 500), _f("y", 0, -500, 500), _f("z", 0, -500, 500)],
    outputs=_geo(),
    gizmo={"kind": "translate", "binds": ["x", "y", "z"],
           "anchor": "preview", "lock": ["offset"]},
    code_template={"algebra": "_move({shape}, {offset}, {x}, {y}, {z})"},
    output_follows="shape",
    description="Translate a shape (or a plane). Wire a vector into `offset` to "
                "drive the position; feed a LIST of vectors to scatter the shape "
                "to each position (one moved copy per vector)."))

register(NodeDef("Rotate", "transform", "Rotate",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_CURVE])] + _pin("angle"),
    params=[_f("angle", 90, -360, 360),
            Param("axis", "select", "axis", "Z", widget="select",
                  options=["X", "Y", "Z"],
                  code_map={"X": "Axis.X", "Y": "Axis.Y", "Z": "Axis.Z"})],
    outputs=_geo(),
    gizmo={"kind": "rotate", "binds": ["angle"], "axisParam": "axis",
           "anchor": "origin", "lock": ["angle"]},
    code_template={"algebra": "_rotate({shape}, {axis}, {angle})"},
    output_follows="shape",
    description="Rotate a shape (or a plane) around a global axis."))

register(NodeDef("Scale", "transform", "Scale",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_CURVE])] + _pin("factor"),
    params=[_f("factor", 2, 0.01, 100),
            _f("x", 1, 0.01, 100, label="x"), _f("y", 1, 0.01, 100, label="y"),
            _f("z", 1, 0.01, 100, label="z")],
    outputs=_geo(),
    gizmo={"kind": "scale", "binds": ["factor"], "anchor": "preview", "lock": ["factor"]},
    code_template={"algebra": "_scale({shape}, {factor}, {x}, {y}, {z})"},
    output_follows="shape",
    description="Scale a shape. `factor` is the uniform multiplier; set `x`/`y`/`z` "
                "(default 1) for a non-uniform scale on top of it."))

register(NodeDef("ToPlane", "transform", "To Plane",
    inputs=[Socket("shape", WIRE_SURFACE, raw=True), Socket("plane", WIRE_PLANE)],
    outputs=_sk(),
    code_template={"algebra": "_to_plane({shape}, {plane})"},
    description="Re-seat a 2D profile onto a plane/frame (its local XY comes to lie "
                "in the plane). Wire a list of frames (Divide Curve) — and optionally "
                "a list of profiles — and it zips one profile per frame, oriented "
                "perpendicular to the curve, ready to Loft."))

register(NodeDef("Mirror", "transform", "Mirror",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_CURVE])],
    params=[Param("plane", "select", "plane", "XZ", widget="select",
                  options=["XY", "XZ", "YZ"],
                  code_map={"XY": "Plane.XY", "XZ": "Plane.XZ", "YZ": "Plane.YZ"}),
            Param("copy", "bool", "copy", False, widget="checkbox")],
    outputs=_geo(),
    code_template={"algebra": "_mirror({shape}, {plane}, {copy})"},
    description="Mirror across a plane. `copy` keeps the original too, so the "
                "result is symmetric (original + reflection)."))

register(NodeDef("ArrayLinear", "transform", "Linear Array",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_CURVE])],
    params=[_i("count", 3, 1, 200), _f("dx", 20, -500, 500),
            _f("dy", 0, -500, 500), _f("dz", 0, -500, 500)],
    outputs=[Socket("result", WIRE_SOLID)],
    code_template={"algebra": "[Pos({dx}*i, {dy}*i, {dz}*i) * {shape} for i in range({count})]"},
    description="Repeat a shape along a vector -> list."))

register(NodeDef("ArrayPolar", "transform", "Polar Array",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_CURVE])] + _pin("count", "angle"),
    params=[_i("count", 6, 1, 200), _f("angle", 360, -360, 360),
            Param("axis", "select", "axis", "Z", widget="select",
                  options=["X", "Y", "Z"],
                  code_map={"X": "Axis.X", "Y": "Axis.Y", "Z": "Axis.Z"})],
    outputs=[Socket("result", WIRE_SOLID)],
    code_template={"algebra": "_array_polar({shape}, {count}, {angle}, {axis})"},
    description="Repeat a shape around a global axis -> list (bolt circles, "
                "spokes). A full 360 spaces `count` copies evenly; a partial "
                "`angle` fans them between 0 and angle. Rotation is about the "
                "axis through the origin — Move the shape off-axis first."))

register(NodeDef("Align", "transform", "Align",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_CURVE]),
            Socket("ref", WIRE_VECTOR, required=False),
            Socket("target", WIRE_VECTOR, required=False)],
    outputs=_geo(),
    code_template={"algebra": "_align({shape}, {ref}, {target})"},
    output_follows="shape",
    description="Translate a shape so `ref` (default: its bounding-box centre) "
                "lands on `target` (default: the origin). Unwired it just centres "
                "the shape; wire points to snap a feature onto a location."))

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
    inputs=[Socket("shape", WIRE_SOLID)],
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

register(NodeDef("PlaneNormal", "plane", "Plane (normal)",
    inputs=[Socket("origin", WIRE_VECTOR, required=False),
            Socket("normal", WIRE_VECTOR, required=False)],
    outputs=[Socket("plane", WIRE_PLANE)],
    code_template={"algebra": "_plane_normal({origin}, {normal})"},
    description="A Plane from an origin point and a normal (z) direction — the "
                "free-form way to orient work planes. Defaults: origin (0,0,0), "
                "normal +Z. Wire lists to get many planes (fans out)."))

register(NodeDef("MovePlane", "plane", "Move Plane",
    inputs=[Socket("plane", WIRE_PLANE)] + _pin("distance"),
    params=[_f("distance", 5, -500, 500)],
    outputs=[Socket("plane", WIRE_PLANE)],
    code_template={"algebra": "_plane_offset({plane}, {distance})"},
    description="Slide a plane along its own normal by `distance`. Wire a list "
                "of distances (Range) to get a stack of parallel planes (fans "
                "out) — e.g. many Sections through a part."))

register(NodeDef("DeconstructPlane", "plane", "Plane Origin",
    inputs=[Socket("plane", WIRE_PLANE)],
    outputs=[Socket("point", WIRE_VECTOR)],
    code_template={"algebra": "_plane_origin({plane})"},
    description="The origin point of a plane/frame. Wire a list of frames (e.g. "
                "from Divide Curve) to get the points along the curve (fans out)."))

register(NodeDef("Deconstruct", "vector", "Deconstruct (points)",
    inputs=[Socket("shape", WIRE_SOLID, list_access=True,
                   accepts=[WIRE_CURVE, WIRE_SELECTION, WIRE_VECTOR, WIRE_PLANE])],
    outputs=[Socket("points", WIRE_VECTOR)],
    code_template={"algebra": "_deconstruct({shape})"},
    description="Explode anything into its points — the one coherent point "
                "extractor: a solid/surface/curve gives ALL its vertices; a "
                "plane/frame its origin; a selection the picked points; a point "
                "itself. Outputs a list (fans out). Pairs with Divide Curve "
                "(frames) and Select Vertex (interactive pick)."))

register(NodeDef("DeconstructEdges", "vector", "Deconstruct Edges",
    inputs=[Socket("shape", WIRE_SOLID, list_access=True,
                   accepts=[WIRE_SURFACE, WIRE_CURVE, WIRE_SELECTION])],
    outputs=[Socket("edges", WIRE_CURVE)],
    code_template={"algebra": "_explode({shape}, 'edge')"},
    description="Explode a shape into its edges (curves) — the companion of "
                "Deconstruct (points): a solid/surface/curve gives ALL its "
                "constituent edges as a list (fans out). Feed into Sweep.path, "
                "Move, or any curve input."))

register(NodeDef("DeconstructFaces", "vector", "Deconstruct Faces",
    inputs=[Socket("shape", WIRE_SOLID, list_access=True,
                   accepts=[WIRE_SURFACE, WIRE_SELECTION])],
    outputs=[Socket("faces", WIRE_SURFACE)],
    code_template={"algebra": "_explode({shape}, 'face')"},
    description="Explode a shape into its faces (surfaces): a solid/surface "
                "gives ALL its constituent faces as a list (fans out). Feed into "
                "Extrude, Thicken, or any surface input."))

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

register(NodeDef("CrossProduct", "vector", "Cross Product",
    inputs=[Socket("a", WIRE_VECTOR), Socket("b", WIRE_VECTOR)],
    outputs=[Socket("vector", WIRE_VECTOR)],
    code_template={"algebra": "_pt({a}).cross(_pt({b}))"},
    description="Cross product a x b: the vector perpendicular to both. Feed "
                "into Plane (normal) to build a plane from two directions."))

register(NodeDef("DotProduct", "vector", "Dot Product",
    inputs=[Socket("a", WIRE_VECTOR), Socket("b", WIRE_VECTOR)],
    outputs=_data(),
    code_template={"algebra": "_pt({a}).dot(_pt({b}))"},
    description="Dot product a . b (a number). Zero = perpendicular; negative = "
                "pointing away from each other."))

register(NodeDef("Normalize", "vector", "Normalize",
    inputs=[Socket("vector", WIRE_VECTOR)],
    outputs=[Socket("vector", WIRE_VECTOR)],
    code_template={"algebra": "_pt({vector}).normalized()"},
    description="The unit vector (length 1) in the same direction."))

register(NodeDef("VectorLength", "vector", "Vector Length",
    inputs=[Socket("vector", WIRE_VECTOR)],
    outputs=_data("length"),
    code_template={"algebra": "_pt({vector}).length"},
    description="The length (magnitude) of a vector — also the distance of a "
                "point from the origin."))

register(NodeDef("Distance", "vector", "Distance",
    inputs=[Socket("a", WIRE_VECTOR), Socket("b", WIRE_VECTOR)],
    outputs=_data(),
    code_template={"algebra": "(_pt({a}) - _pt({b})).length"},
    description="Distance between two points (a number). Fans out over lists — "
                "e.g. distances from a grid of points to an attractor, into "
                "Remap, into a radius."))

register(NodeDef("Midpoint", "vector", "Midpoint",
    inputs=[Socket("a", WIRE_VECTOR), Socket("b", WIRE_VECTOR)],
    outputs=[Socket("point", WIRE_VECTOR)],
    code_template={"algebra": "((_pt({a}) + _pt({b})) / 2)"},
    description="The point halfway between two points."))

# ===========================================================================
# 9. Lists / data trees (subset)
# ===========================================================================
register(NodeDef("ListCreate", "data", "List",
    inputs=[Socket("items", WIRE_DATA, required=False, multiple=True)],
    outputs=_data(),
    code_template={"algebra": "[{items}]"},
    description="Collect inputs into a list."))

register(NodeDef("ListRange", "data", "Range",
    params=[_f("start", 0, widget="input"),
            _i("count", 5, 0, 100000, soft_max=200),
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

register(NodeDef("ListRepeat", "data", "List Repeat",
    inputs=[Socket("value", WIRE_DATA, list_access=True)] + _pin("count"),
    params=[_i("count", 3, 0, 100000, soft_max=200)],
    outputs=_data(),
    code_template={"algebra": "_repeat({value}, {count})"},
    description="Repeat a value `count` times -> list (a whole list is "
                "concatenated `count` times). The fan-out multiplier."))

register(NodeDef("ListShift", "data", "List Shift",
    inputs=[Socket("list", WIRE_DATA, list_access=True)] + _pin("offset"),
    params=[_i("offset", 1, -100000, 100000, widget="input"),
            Param("wrap", "bool", "wrap", True, widget="checkbox")],
    outputs=_data(),
    code_template={"algebra": "_shift({list}, {offset}, {wrap})"},
    description="Shift a list by `offset` positions. `wrap` (default) cycles "
                "items around; off drops them from the end instead. Shift a copy "
                "against the original to pair each item with its neighbour."))

register(NodeDef("ListFilter", "data", "List Filter",
    inputs=[Socket("list", WIRE_DATA, list_access=True),
            Socket("pattern", WIRE_DATA, required=False, list_access=True)],
    params=[Param("invert", "bool", "invert", False, widget="checkbox")],
    outputs=_data(),
    code_template={"algebra": "_dispatch({list}, {pattern}, {invert})"},
    description="Keep the items where the boolean `pattern` is True (the "
                "pattern cycles: [True, False] keeps every other item — the "
                "Grasshopper Dispatch/Cull idiom). `invert` keeps the False side."))

register(NodeDef("ListUnique", "data", "List Unique",
    inputs=[Socket("list", WIRE_DATA, list_access=True)],
    outputs=_data(),
    code_template={"algebra": "_unique({list})"},
    description="Drop duplicate items, keeping the first occurrence of each."))

register(NodeDef("Random", "data", "Random",
    inputs=_pin("count", "min", "max", "seed"),
    params=[_i("count", 10, 0, 100000, soft_max=200),
            _f("min", 0, widget="input"), _f("max", 1, widget="input"),
            _i("seed", 1, 0, 100000, widget="input")],
    outputs=_data(),
    code_template={"algebra": "_randlist({count}, {min}, {max}, {seed})"},
    description="`count` uniform random numbers in [min, max], deterministic "
                "per `seed` — jitter for radii/heights/rotations (fans out)."))

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

register(NodeDef("GraphMapper", "data", "Graph Mapper",
    inputs=_pin("t"),
    params=[_f("t", 0.0, 0.0, 1.0, 0.01, label="t"),
            Param("mode", "select", "mode", "smooth", widget="select",
                  options=["smooth", "linear", "bezier"]),
            Param("curve", "curve", "curve",
                  [{"p": [0.0, 0.0], "hi": None, "ho": None},
                   {"p": [1.0, 1.0], "hi": None, "ho": None}],
                  widget="curve", raw=True)],
    outputs=_data(),
    code_template={"algebra": "_graphmap({t}, {curve}, {mode})"},
    description="An editable function curve f(x)->y drawn on the node. Wire a t "
                "(or a list/Range of t) into it to sample the curve — smooth "
                "(monotone, no overshoot), linear, or bezier with tangent handles. "
                "Fans out over a list of t (Divide Curve t → radii → variable "
                "loft). Pipe the output through Remap to reach a target range."))

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


def _mathfn(type_, label, expr, desc):
    """A one-input math function node; fans out over a list of x."""
    register(NodeDef(type_, "math", label,
        inputs=[Socket("x", WIRE_DATA)],
        outputs=_data(),
        code_template={"algebra": expr},
        description=desc))


_mathfn("Sin", "Sin", "math.sin(math.radians({x}))", "Sine of x (degrees).")
_mathfn("Cos", "Cos", "math.cos(math.radians({x}))", "Cosine of x (degrees).")
_mathfn("Tan", "Tan", "math.tan(math.radians({x}))", "Tangent of x (degrees).")
_mathfn("ArcSin", "ArcSin", "math.degrees(math.asin({x}))",
        "Inverse sine -> degrees (x in [-1, 1]).")
_mathfn("ArcCos", "ArcCos", "math.degrees(math.acos({x}))",
        "Inverse cosine -> degrees (x in [-1, 1]).")
_mathfn("ArcTan", "ArcTan", "math.degrees(math.atan({x}))",
        "Inverse tangent -> degrees.")
_mathfn("Sqrt", "Sqrt", "math.sqrt({x})", "Square root of x.")
_mathfn("Abs", "Abs", "abs({x})", "Absolute value of x.")
_mathfn("Floor", "Floor", "math.floor({x})", "Round x down to an integer.")
_mathfn("Ceil", "Ceil", "math.ceil({x})", "Round x up to an integer.")

register(NodeDef("Round", "math", "Round",
    inputs=[Socket("x", WIRE_DATA)],
    params=[_i("decimals", 0, 0, 12, widget="input")],
    outputs=_data(),
    code_template={"algebra": "round({x}, {decimals})"},
    description="Round x to `decimals` decimal places."))

_binop("Modulo", "Modulo", "%")

register(NodeDef("Min", "math", "Min",
    inputs=[Socket("a", WIRE_DATA), Socket("b", WIRE_DATA)],
    outputs=_data(),
    code_template={"algebra": "min({a}, {b})"},
    description="The smaller of a and b. For a whole list use Bounds (its "
                "domain is [min, max])."))

register(NodeDef("Max", "math", "Max",
    inputs=[Socket("a", WIRE_DATA), Socket("b", WIRE_DATA)],
    outputs=_data(),
    code_template={"algebra": "max({a}, {b})"},
    description="The larger of a and b. For a whole list use Bounds (its "
                "domain is [min, max])."))

# ===========================================================================
# 11. Panels / inspection
# ===========================================================================
register(NodeDef("ToAgent", "data", "To Agent",
    inputs=[Socket("value", WIRE_DATA, required=False, list_access=True)],
    params=[Param("label", "str", "label", "", widget="input"),
            Param("date", "str", "date", "", widget="input")],
    outputs=_data("value"),
    subtype_follows="value",
    code_template={"algebra": "{value}"},
    description="Provenance tag for the AI agent/copilot. Whatever you wire in "
                "passes through UNCHANGED, but the node registers label + date + "
                "location (workflow, node id, upstream source) in the agent's "
                "index, so you can say 'retro-engineer part X in workflow Y'. "
                "Tag an ImportSTEP/ImportSTL to hand the agent a file; `date` "
                "is stamped automatically when the graph is saved."))

register(NodeDef("Panel", "panel", "Panel",
    inputs=[Socket("value", WIRE_DATA, required=False, list_access=True)],
    params=[Param("text", "str", "text", "", widget="text"),
            Param("mode", "select", "mode", "friendly", widget="select",
                  options=["friendly", "json", "build123d"])],
    outputs=_data("value"),
    subtype_follows="value",   # inspector pass-through: keep the upstream tag
    code_template={"algebra": "_panel({node_id!r}, {value}, {text}, {mode})"},
    description="Dual-mode Panel. Wire a value to INSPECT it (multi-line, "
                "list-aware, passes through). Leave it unwired and type into "
                "`text` to use it as a data SOURCE — one item per line (several "
                "lines = a list that fans out). `mode` picks the syntax: "
                "friendly ('0,0,0' -> Vector), json, or build123d (eval)."))

register(NodeDef("BoundingBox", "panel", "Bounding Box",
    inputs=[Socket("shape", WIRE_SOLID)],
    outputs=[Socket("box", WIRE_SOLID)],
    code_template={"algebra": "{shape}.bounding_box()"},
    description="Bounding box of a shape."))

register(NodeDef("Volume", "panel", "Volume",
    inputs=[Socket("shape", WIRE_SOLID)],
    outputs=_data("volume"),
    code_template={"algebra": "{shape}.volume"},
    description="Volume of a solid (model units cubed). Wire into a Panel to "
                "read it."))

register(NodeDef("Area", "panel", "Area",
    inputs=[Socket("shape", WIRE_SOLID, accepts=[WIRE_SURFACE])],
    outputs=_data("area"),
    code_template={"algebra": "{shape}.area"},
    description="Total surface area of a shape (for a solid: all its faces)."))

register(NodeDef("CenterOfMass", "panel", "Center of Mass",
    inputs=[Socket("shape", WIRE_SOLID)],
    outputs=[Socket("point", WIRE_VECTOR)],
    code_template={"algebra": "{shape}.center(CenterOf.MASS)"},
    description="The centre of mass of a shape, as a point — wire into Align.ref "
                "to balance a part on a location, or into a Panel to read it."))

# ===========================================================================
# 11b. Containers / legend — one typed pass-through per wire type. They colour
# the wire, label the graph (a legend) and inspect the value (Panels tab) without
# changing it. A cast/convert mode is planned (PLAN_DATA_PROTOCOL.md).
# ===========================================================================
# What a gated container (filter/transform) extracts in transform mode, for the
# node description. The runtime rules live in transpiler `_gate`.
_GATE_DESC = {
    "surface":   "fills closed curves and pulls the PLANAR faces out of solids",
    "curve":     "outlines surfaces, pulls the edges out of solids and joins points",
    "point":     "explodes any shape into its points (vertices, a plane's origin, …)",
    "solid":     "explodes a compound into its individual solids",
    "plane":     "reads the Plane of each planar face of a surface or solid",
    "selection": "also pulls a solid's faces/edges/vertices into the selection",
}


def _container(type_: str, label: str, wire: str,
               gate: str | None = None,
               accepts: list[str] | None = None) -> NodeDef:
    if gate:
        # filter/transform gate targeting the container's OWN type. `filter` lets
        # only values already of that type through; `transform` also coerces /
        # extracts everything compatible into it. Output is always a list (it may
        # explode a shape), so downstream fans out. Input is a `multiple` collector
        # widened (via `accepts`) to take the coercible source types.
        return register(NodeDef(type_, "container", label,
            inputs=[Socket("value", wire, required=False, multiple=True,
                           accepts=accepts or [])],
            params=[Param("mode", "select", "mode", "filter",
                          widget="select", options=["filter", "transform"])],
            outputs=[Socket("value", wire)],
            subtype_follows="value",   # filter keeps the upstream tag; transform drops it
            code_template={"algebra": f"_gate({{node_id!r}}, [{{value}}], '{gate}', {{mode}})"},
            description=f"{label} container — a typed filter / transformer. "
                        f"`filter` (default) passes only values that are already "
                        f"{label.lower()}s; `transform` also coerces what's "
                        f"compatible ({_GATE_DESC.get(gate, '')}). Always outputs a "
                        f"list (fans out) and shows the result in the Panels tab."))
    return register(NodeDef(type_, "container", label,
        inputs=[Socket("value", wire, required=False, list_access=True)],
        outputs=[Socket("value", wire)],
        subtype_follows="value",   # legend pass-through: keep the upstream tag
        code_template={"algebra": "_probe({node_id!r}, {value})"},
        description=f"{label} container / legend: a typed pass-through. Wire a "
                    f"{label.lower()} through it to colour the wire, label the "
                    f"graph and inspect the value in the Panels tab — unchanged."))

_container("Geometry", "Geometry", WIRE_SOLID, gate="solid")
_container("Surface", "Surface", WIRE_SURFACE, gate="surface",
           accepts=[WIRE_SOLID, WIRE_CURVE])
_container("Curve", "Curve", WIRE_CURVE, gate="curve",
           accepts=[WIRE_SOLID, WIRE_SURFACE, WIRE_VECTOR])
_container("Point", "Point", WIRE_VECTOR, gate="point",
           accepts=[WIRE_SOLID, WIRE_SURFACE, WIRE_CURVE, WIRE_SELECTION, WIRE_PLANE])
_container("Plane", "Plane", WIRE_PLANE, gate="plane",
           accepts=[WIRE_SURFACE, WIRE_SOLID])
_container("Selection", "Selection", WIRE_SELECTION, gate="selection",
           accepts=[WIRE_CURVE, WIRE_SURFACE, WIRE_SOLID, WIRE_VECTOR])

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
# 12b. Import / IO
# ===========================================================================
# Load an external model file into geometry. `path` is an "asset" picker: it lists
# the files already imported into the project's library (projects/<name>/assets/),
# filtered by the extensions in `options`, and stores a project-relative path
# (e.g. "assets/part.step") — which resolves because the worker runs with the
# project dir as cwd. Files get there via the UI upload (POST .../asset|import).
def _asset(name, exts):
    return Param("path", "str", "file", "", widget="asset", options=exts)

register(NodeDef("ImportSTEP", "import", "Import STEP",
    params=[_asset("path", [".step", ".stp"])],
    outputs=_geo(),
    code_template={"algebra": "import_step({path})"},
    description="Load a STEP file as solid geometry (Compound)."))

register(NodeDef("ImportSTL", "import", "Import STL",
    params=[_asset("path", [".stl"])],
    outputs=_geo(),
    code_template={"algebra": "import_stl({path})"},
    description="Load an STL mesh as a Face (good for reference; limited for solid ops)."))

register(NodeDef("ImportSVG", "import", "Import SVG",
    params=[_asset("path", [".svg"])],
    outputs=_cv(),
    code_template={"algebra": "import_svg({path})"},
    description="Load 2D profiles from an SVG file (wires/faces) to extrude or build on."))

register(NodeDef("ImportDXF", "import", "Import DXF",
    params=[_asset("path", [".dxf"])],
    outputs=_cv(),
    code_template={"algebra": "import_dxf({path})"},
    description="Load 2D profiles from a DXF file (wires) to extrude or build on."))

# ===========================================================================
# 13. Export / IO
# ===========================================================================
register(NodeDef("ExportSTEP", "export", "Export STEP",
    inputs=[Socket("shape", WIRE_SOLID)],
    params=[Param("path", "str", "path", "output.step", widget="input")],
    outputs=[],
    code_template={"algebra": "export_step({shape}, {path})"},
    description="Write the shape to a STEP file."))

register(NodeDef("ExportSTL", "export", "Export STL",
    inputs=[Socket("shape", WIRE_SOLID)],
    params=[Param("path", "str", "path", "output.stl", widget="input")],
    outputs=[],
    code_template={"algebra": "export_stl({shape}, {path})"},
    description="Write the shape to an STL file."))

register(NodeDef("Export3MF", "export", "Export 3MF",
    inputs=[Socket("shape", WIRE_SOLID)],
    params=[Param("path", "str", "path", "output.3mf", widget="input")],
    outputs=[],
    code_template={"algebra": "_export_3mf({shape}, {path})"},
    description="Write the shape to a 3MF file (the modern 3D-printing format: "
                "mesh + units in one file)."))

register(NodeDef("ExportGLTF", "export", "Export glTF",
    inputs=[Socket("shape", WIRE_SOLID)],
    params=[Param("path", "str", "path", "output.gltf", widget="input")],
    outputs=[],
    code_template={"algebra": "export_gltf({shape}, {path})"},
    description="Write the shape to a glTF file (web/AR viewers, three.js)."))

register(NodeDef("ExportSVG", "export", "Export SVG",
    inputs=[Socket("shape", WIRE_SURFACE, accepts=[WIRE_CURVE])],
    params=[Param("path", "str", "path", "output.svg", widget="input")],
    outputs=[],
    code_template={"algebra": "_export_2d({shape}, {path}, 'svg')"},
    description="Write 2D geometry (a sketch, curve, or a Section of a solid) "
                "to an SVG drawing — the XY projection. Laser cutting, plotting, "
                "documentation."))

register(NodeDef("ExportDXF", "export", "Export DXF",
    inputs=[Socket("shape", WIRE_SURFACE, accepts=[WIRE_CURVE])],
    params=[Param("path", "str", "path", "output.dxf", widget="input")],
    outputs=[],
    code_template={"algebra": "_export_2d({shape}, {path}, 'dxf')"},
    description="Write 2D geometry (a sketch, curve, or a Section of a solid) "
                "to a DXF drawing — the XY projection. CNC/laser toolchains."))

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


# ===========================================================================
# Output sub-types (advisory tags) — split the opaque `data` bus and the `curve`
# family for legend/disambiguation in the editor. Applied to the first output of
# each producer. Compatibility is unaffected (gates on wire_type only).
# See PLAN_DATA_PROTOCOL.md §4d.
# ===========================================================================
_OUTPUT_SUBTYPES = {
    # data -> number / integer / boolean / text
    "NumberSlider": "number", "NumberInput": "number", "IntegerSlider": "integer",
    "BooleanToggle": "boolean", "StringInput": "text",
    "Add": "number", "SubtractNum": "number", "Multiply": "number",
    "Divide": "number", "Power": "number", "Clamp": "number", "Expression": "number",
    "Remap": "number", "CurveLength": "number", "ListLength": "integer",
    "Sin": "number", "Cos": "number", "Tan": "number",
    "ArcSin": "number", "ArcCos": "number", "ArcTan": "number",
    "Sqrt": "number", "Abs": "number", "Round": "number",
    "Modulo": "number", "Min": "number", "Max": "number",
    "Floor": "integer", "Ceil": "integer",
    "DotProduct": "number", "Distance": "number", "VectorLength": "number",
    "Volume": "number", "Area": "number",
    # data -> list / domain
    "ListCreate": "list", "ListRange": "list", "ListReverse": "list",
    "ListSort": "list", "ListSlice": "list", "ListFlatten": "list",
    "Concat": "list", "Series": "list", "DivideDomain": "list",
    "ListRepeat": "list", "ListShift": "list", "ListFilter": "list",
    "ListUnique": "list", "Random": "list",
    "ConstructDomain": "domain", "Bounds": "domain",
    # curve -> line / polyline / spline / arc
    "Line": "line", "Polyline": "polyline", "Spline": "spline", "Bezier": "spline",
    "Helix": "spline", "Arc3pt": "arc", "ArcCenter": "arc",
}
for _t, _st in _OUTPUT_SUBTYPES.items():
    _d = REGISTRY.get(_t)
    if _d and _d.outputs:
        _d.outputs[0].subtype = _st
