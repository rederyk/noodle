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

# Which wire types may legally connect to which (output -> accepted inputs).
# Geometry-ish types are kept distinct but `data` is the permissive fallback.
WIRE_COMPATIBLE: dict[str, set[str]] = {
    WIRE_GEOMETRY: {WIRE_GEOMETRY, WIRE_DATA},
    WIRE_SKETCH: {WIRE_SKETCH, WIRE_GEOMETRY, WIRE_DATA},
    WIRE_CURVE: {WIRE_CURVE, WIRE_SKETCH, WIRE_DATA},
    WIRE_DATA: {WIRE_DATA},
    WIRE_TREE: {WIRE_TREE, WIRE_DATA},
    WIRE_PLANE: {WIRE_PLANE, WIRE_DATA},
    WIRE_VECTOR: {WIRE_VECTOR, WIRE_DATA},
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


def _f(name, default=0.0, lo=None, hi=None, step=0.5, label="", widget="slider"):
    return Param(name, "float", label or name, default, lo, hi, step, widget)


def _i(name, default=0, lo=None, hi=None, label="", widget="slider"):
    return Param(name, "int", label or name, default, lo, hi, 1, widget)


# ===========================================================================
# 1. Primitives 3D
# ===========================================================================
register(NodeDef("Box", "primitives_3d", "Box",
    params=[_f("width", 10, 0.1, 500), _f("height", 10, 0.1, 500), _f("depth", 10, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Box({width}, {height}, {depth})"},
    description="Solid box of given width, height, depth."))

register(NodeDef("Cylinder", "primitives_3d", "Cylinder",
    params=[_f("radius", 5, 0.1, 500), _f("height", 20, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Cylinder({radius}, {height})"},
    description="Solid cylinder."))

register(NodeDef("Sphere", "primitives_3d", "Sphere",
    params=[_f("radius", 10, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Sphere({radius})"},
    description="Solid sphere."))

register(NodeDef("Cone", "primitives_3d", "Cone",
    params=[_f("bottom_radius", 5, 0, 500), _f("top_radius", 0, 0, 500), _f("height", 15, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Cone({bottom_radius}, {top_radius}, {height})"},
    description="Truncated cone / frustum."))

register(NodeDef("Torus", "primitives_3d", "Torus",
    params=[_f("major_radius", 15, 0.1, 500), _f("minor_radius", 3, 0.1, 500)],
    outputs=_geo(),
    code_template={"algebra": "Torus({major_radius}, {minor_radius})"},
    description="Torus / ring."))

# ===========================================================================
# 2. Primitives 2D (sketch / curve)
# ===========================================================================
register(NodeDef("Rectangle", "primitives_2d", "Rectangle",
    params=[_f("width", 30, 0.1, 500), _f("height", 20, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Rectangle({width}, {height})"},
    description="2D rectangle sketch."))

register(NodeDef("RoundedRectangle", "primitives_2d", "Rounded Rectangle",
    params=[_f("width", 30, 0.1, 500), _f("height", 20, 0.1, 500), _f("radius", 3, 0, 250)],
    outputs=_sk(),
    code_template={"algebra": "RectangleRounded({width}, {height}, {radius})"},
    description="Rectangle with filleted corners."))

register(NodeDef("Circle", "primitives_2d", "Circle",
    params=[_f("radius", 10, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Circle({radius})"},
    description="2D circle sketch."))

register(NodeDef("Ellipse", "primitives_2d", "Ellipse",
    params=[_f("x_radius", 8, 0.1, 500), _f("y_radius", 5, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Ellipse({x_radius}, {y_radius})"},
    description="2D ellipse sketch."))

register(NodeDef("Polygon", "primitives_2d", "Regular Polygon",
    params=[_f("radius", 10, 0.1, 500), _i("sides", 6, 3, 64)],
    outputs=_sk(),
    code_template={"algebra": "RegularPolygon({radius}, {sides})"},
    description="Regular N-sided polygon."))

register(NodeDef("Text", "primitives_2d", "Text",
    params=[Param("text", "str", "text", "Hello", widget="input"),
            _f("font_size", 10, 0.1, 500)],
    outputs=_sk(),
    code_template={"algebra": "Text({text}, font_size={font_size})"},
    description="Text as a 2D sketch."))

# ===========================================================================
# 3. Operations 2D -> 3D
# ===========================================================================
register(NodeDef("Extrude", "operations", "Extrude",
    inputs=[Socket("sketch", WIRE_SKETCH)],
    params=[_f("amount", 10, 0.1, 500), _f("taper", 0, -45, 45)],
    outputs=_geo(),
    code_template={"algebra": "extrude({sketch}, amount={amount}, taper={taper})",
                   "builder": "extrude(amount={amount}, taper={taper})"},
    description="Extrude a 2D sketch into a 3D solid."))

register(NodeDef("Revolve", "operations", "Revolve",
    inputs=[Socket("sketch", WIRE_SKETCH)],
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
    outputs=_geo(),
    code_template={"algebra": "loft([{sections}])",
                   "builder": "loft()"},
    description="Loft through a list of sketch sections."))

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

# ===========================================================================
# 4. Booleans (CSG)
# ===========================================================================
register(NodeDef("Union", "boolean", "Union",
    inputs=[Socket("a", WIRE_GEOMETRY), Socket("b", WIRE_GEOMETRY)],
    outputs=_geo(),
    code_template={"algebra": "({a} + {b})"},
    description="Boolean union A + B."))

register(NodeDef("Subtract", "boolean", "Subtract",
    inputs=[Socket("a", WIRE_GEOMETRY), Socket("b", WIRE_GEOMETRY)],
    outputs=_geo(),
    code_template={"algebra": "({a} - {b})"},
    description="Boolean difference A - B."))

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
    inputs=[Socket("part", WIRE_GEOMETRY)],
    params=[_f("radius", 2, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "fillet({part}.edges(), radius={radius})"},
    description="Round all edges of a part."))

register(NodeDef("Chamfer", "modifiers", "Chamfer",
    inputs=[Socket("part", WIRE_GEOMETRY)],
    params=[_f("length", 1.5, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "chamfer({part}.edges(), length={length})"},
    description="Bevel all edges of a part."))

register(NodeDef("Shell", "modifiers", "Shell",
    inputs=[Socket("part", WIRE_GEOMETRY)],
    params=[_f("thickness", 1, 0.05, 100)],
    outputs=_geo(),
    code_template={"algebra": "offset({part}, amount=-{thickness}, openings={part}.faces().sort_by(Axis.Z)[-1])"},
    description="Hollow out a part with the given wall thickness, leaving the top (+Z) face open."))

register(NodeDef("Offset", "modifiers", "Offset",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[_f("amount", 2, -100, 100)],
    outputs=_geo(),
    code_template={"algebra": "offset({shape}, amount={amount})"},
    description="Offset a shape outward/inward."))

register(NodeDef("Section", "modifiers", "Section",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[Param("plane", "select", "plane", "XY", widget="select",
                  options=["XY", "XZ", "YZ"],
                  code_map={"XY": "Plane.XY", "XZ": "Plane.XZ", "YZ": "Plane.YZ"})],
    outputs=_sk(),
    code_template={"algebra": "section({shape}, section_by={plane})"},
    description="Planar cross-section of a shape."))

# ===========================================================================
# 6. Transforms
# ===========================================================================
register(NodeDef("Move", "transform", "Move",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[_f("x", 0, -500, 500), _f("y", 0, -500, 500), _f("z", 0, -500, 500)],
    outputs=_geo(),
    code_template={"algebra": "(Pos({x}, {y}, {z}) * {shape})"},
    description="Translate a shape."))

register(NodeDef("Rotate", "transform", "Rotate",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[_f("angle", 90, -360, 360),
            Param("axis", "select", "axis", "Z", widget="select",
                  options=["X", "Y", "Z"],
                  code_map={"X": "Axis.X", "Y": "Axis.Y", "Z": "Axis.Z"})],
    outputs=_geo(),
    code_template={"algebra": "{shape}.rotate({axis}, {angle})"},
    description="Rotate a shape around an axis."))

register(NodeDef("Scale", "transform", "Scale",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    params=[_f("factor", 2, 0.01, 100)],
    outputs=_geo(),
    code_template={"algebra": "scale({shape}, {factor})"},
    description="Uniform scale."))

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

# ===========================================================================
# 8. Vectors & points
# ===========================================================================
register(NodeDef("Vector", "vector", "Vector",
    params=[_f("x", 0, -1000, 1000, widget="input"),
            _f("y", 0, -1000, 1000, widget="input"),
            _f("z", 0, -1000, 1000, widget="input")],
    outputs=[Socket("vector", WIRE_VECTOR)],
    code_template={"algebra": "Vector({x}, {y}, {z})"},
    description="A 3D vector."))

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
    inputs=[Socket("list", WIRE_DATA)],
    params=[_i("index", 0, 0, 100000, widget="input")],
    outputs=_data(),
    code_template={"algebra": "{list}[{index}]"},
    description="Pick an item by index."))

register(NodeDef("ListLength", "data", "List Length",
    inputs=[Socket("list", WIRE_DATA)],
    outputs=_data(),
    code_template={"algebra": "len({list})"},
    description="Number of items in a list."))

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
    inputs=[Socket("value", WIRE_DATA)],
    outputs=_data("value"),
    code_template={"algebra": "_panel({node_id!r}, {value})"},
    description="Inspect a value live (passes it through)."))

register(NodeDef("BoundingBox", "panel", "Bounding Box",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    outputs=[Socket("box", WIRE_GEOMETRY)],
    code_template={"algebra": "{shape}.bounding_box()"},
    description="Bounding box of a shape."))

register(NodeDef("Preview", "panel", "Preview",
    inputs=[Socket("shape", WIRE_GEOMETRY)],
    outputs=[Socket("shape", WIRE_GEOMETRY)],
    code_template={"algebra": "_preview({node_id!r}, {shape})"},
    description="Draw the wired geometry in the 3D viewport (passes it through). "
                "Like a Grasshopper preview: only what you wire here is shown."))

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
    inputs=[Socket("in_0", WIRE_DATA, required=False),
            Socket("in_1", WIRE_DATA, required=False)],
    params=[Param("code", "str", "code",
                  "result = Box(in_0 or 10, in_1 or 10, 5)", widget="text", raw=True)],
    outputs=_data("result"),
    code_template={},  # handled specially by the transpiler
    description="Arbitrary build123d code; must assign `result`."))
