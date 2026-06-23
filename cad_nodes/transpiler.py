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


def _panel(_id, _value):
    __panels__[_id] = _value
    return _value


def _preview(_id, _value):
    __previews__[_id] = _value
    return _value
"""


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
        lines.append(f"{var} = {fn}({args}){_annot(node)}")

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

        if ndef.outputs:
            var = self._new_var(node.id)
            lines.append(f"{var} = {expr}{_annot(node)}")
        else:  # export / sink statement
            lines.append(f"{expr}{_annot(node)}")

    def _emit_group(self, node, lines: list[str], indent: str = "") -> None:
        ndef = catalog.get(node.type)
        self._counter += 1
        ctx = f"__ctx_{self._counter}"
        values = self._param_values(node, ndef)
        values.update(self._input_values(node.id, ndef))
        values["ctx"] = ctx
        header = _substitute(ndef.code_template["builder"], values)
        lines.append(indent + header + _annot(node))

        children = self.graph.children_of(node.id)
        child_order = toposort([c.id for c in children],
                               [(c.from_node, c.to_node) for c in self.graph.connections
                                if c.from_node in {x.id for x in children}
                                and c.to_node in {x.id for x in children}])
        body_indent = indent + "    "
        for cid in child_order:
            child = self.graph.node(cid)
            cdef = catalog.get(child.type)
            cvals = self._param_values(child, cdef)
            cvals.update(self._input_values(child.id, cdef))
            tmpl = cdef.code_template.get("builder") or cdef.code_template.get("algebra", "")
            lines.append(body_indent + _substitute(tmpl, cvals) + _annot(child))
        if not children:
            lines.append(body_indent + "pass")

        var = self.var_of.setdefault(node.id, f"__out_{self._counter}")
        self.var_of[node.id] = var
        attr = {"part": "part", "sketch": "sketch", "line": "line"}.get(ndef.group_kind, "part")
        lines.append(f"{var} = {ctx}.{attr}{_annot(node)}")

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
            ndef = catalog.get(node.type)
            if ndef.is_group:
                self._emit_group(node, body)
            elif node.type == "CodeBlock":
                self._emit_codeblock(node, body)
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
