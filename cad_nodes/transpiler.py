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


def _annot(node) -> str:
    """Trailing marker so a runtime traceback line maps back to its node.

    Parsed by the executor (see `_NODE_MARK`); kept as a Python comment so it
    never affects execution.
    """
    return f"  # @node:{node.id} ({node.type})"

PREAMBLE = """\
from build123d import *

# --- runtime helpers (injected by the transpiler) ---
__panels__ = {}
__previews__ = {}
__errors__ = {}


def _panel(_id, _value):
    __panels__[_id] = _value
    return _value


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
"""

# Output wire types that yield a drawable mesh (mirrors the catalog).
_PREVIEWABLE = {catalog.WIRE_GEOMETRY, catalog.WIRE_SKETCH, catalog.WIRE_CURVE}


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
            out[p.name] = format_param(p, node.params.get(p.name, p.default))
        return out

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
        ndef = catalog.get(node.type)
        var = self._new_var(node.id)
        inputs = self._input_values(node.id, ndef)
        user_code = node.params.get("code", "result = None")
        fn = f"__codeblock_{self._counter}"
        args = ", ".join(f"{s.name}={inputs.get(s.name, 'None')}" for s in ndef.inputs)
        lines.append(f"def {fn}({', '.join(s.name for s in ndef.inputs)}):")
        for raw in user_code.splitlines() or ["result = None"]:
            lines.append("    " + raw)
        lines.append("    return result")
        body = [f"{var} = {fn}({args}){_annot(node)}"]
        if self._previewed(node, ndef):
            body.append(f"__previews__[{node.id!r}] = {var}")
        self._guard(lines, body, node)

    def _emit_simple(self, node, lines: list[str]) -> None:
        ndef = catalog.get(node.type)
        values = {}
        values.update(self._param_values(node, ndef))
        values.update(self._input_values(node.id, ndef))
        values["node_id"] = node.id

        template = ndef.code_template.get("algebra")
        if template is None:
            raise ValueError(f"Node {node.type} has no algebra template")
        expr = _substitute(template, values)

        # Optional `origin` input: position the result (point, or a Compound at
        # each of several points). Only wraps when actually connected.
        origin = values.get("origin")
        if origin and origin != "None":
            expr = f"_at({expr}, {origin})"

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
        used_as_source = {c.from_node for c in self.graph.connections}
        geometry_like = {catalog.WIRE_GEOMETRY, catalog.WIRE_SKETCH}
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
        # fallback: last variable produced at all
        return self.var_of[order[-1]] if order and order[-1] in self.var_of else None

    def run(self) -> str:
        warnings = self.graph.validate()
        order = toposort([n.id for n in self.graph.nodes], self.graph.edges())

        body: list[str] = []
        for nid in order:
            node = self.graph.node(nid)
            if node.parent is not None:
                continue  # emitted inside its group
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
        return "\n".join(out) + "\n"


def transpile(graph: Graph) -> str:
    """Convenience: transpile a Graph to build123d source."""
    return Transpiler(graph).run()
