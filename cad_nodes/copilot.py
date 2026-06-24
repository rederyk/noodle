"""
CAD Copilot — an in-app assistant that builds/edits node graphs from natural
language by calling the same `cad_nodes.api` functions the MCP server exposes.

Provider-agnostic over the OpenAI-compatible chat-completions wire format, so it
works with a FREE local model by default (Ollama) and with any keyed provider
that speaks the same format (Groq, OpenRouter, Gemini's OpenAI endpoint, …).

Configuration (env):
  COPILOT_BASE_URL   OpenAI-compatible base, e.g. https://api.groq.com/openai/v1
                     (unset → local Ollama)
  COPILOT_API_KEY    bearer token for the above (unset for local Ollama)
  COPILOT_MODEL      model id (default: qwen2.5 for Ollama)
  OLLAMA_HOST        Ollama base (default http://host.docker.internal:11434)

The web server stays build123d-free: this module only orchestrates HTTP + the
(already build123d-free) api layer; geometry runs in the execution worker.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from . import api, catalog
from .graph import ValidationError
from .store import GraphStore

MAX_STEPS = 16


# ── provider resolution ─────────────────────────────────────────────────────
def _provider() -> dict:
    base = os.environ.get("COPILOT_BASE_URL", "").rstrip("/")
    if base:
        return {"base": base, "key": os.environ.get("COPILOT_API_KEY", ""),
                "model": os.environ.get("COPILOT_MODEL", ""), "local": False}
    host = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434").rstrip("/")
    return {"base": host + "/v1", "key": "",
            "model": os.environ.get("COPILOT_MODEL", "qwen2.5"), "local": True}


def copilot_status() -> dict:
    p = _provider()
    return {"provider": "ollama" if p["local"] else "openai-compatible",
            "base_url": p["base"], "model": p["model"], "keyed": bool(p["key"])}


def _chat(provider: dict, messages: list, tools: list) -> dict:
    """One OpenAI-compatible /chat/completions call. Returns the message dict."""
    body = json.dumps({
        "model": provider["model"], "messages": messages,
        "tools": tools, "tool_choice": "auto",
        "temperature": 0.2, "stream": False,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if provider["key"]:
        headers["Authorization"] = "Bearer " + provider["key"]
    req = urllib.request.Request(provider["base"] + "/chat/completions",
                                 data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]


# ── tool surface (bound to one graph) ───────────────────────────────────────
def _compact_catalog() -> str:
    lines = []
    for t in sorted(catalog.REGISTRY):
        d = catalog.REGISTRY[t]
        ins = ", ".join(f"{s.name}:{s.wire_type}{'' if s.required else '?'}" for s in d.inputs) or "-"
        outs = ", ".join(f"{s.name}:{s.wire_type}" for s in d.outputs) or "-"
        ps = ", ".join(p.name for p in d.params) or "-"
        lines.append(f"{t} [{d.category}] in:({ins}) out:({outs}) params:({ps})")
    return "\n".join(lines)


def _lean_view(v: dict) -> dict:
    if not isinstance(v, dict):
        return {"success": False}
    out = {"success": v.get("success"), "kind": v.get("kind"),
           "volume": v.get("volume"), "counts": v.get("counts")}
    ne = v.get("node_errors") or {}
    if ne:
        out["node_errors"] = {k: (e.get("message") if isinstance(e, dict) else e)
                              for k, e in ne.items()}
    if v.get("warnings"):
        out["warnings"] = v["warnings"]
    return out


def _make_tools(store: GraphStore, graph_id: str, state: dict):
    """Return (openai_tool_specs, dispatch(name, args)->str)."""
    def graph_summary():
        g = store.load(graph_id)
        return {
            "nodes": [{"id": n.id, "type": n.type, "params": n.params} for n in g.nodes],
            "connections": [{"from": f"{c.from_node}.{c.from_socket}",
                             "to": f"{c.to_node}.{c.to_socket}"} for c in g.connections],
        }

    def do(name, a):
        if name == "get_graph":
            return graph_summary()
        if name == "get_node_def":
            return api.get_node_def(a["node_type"])
        if name == "add_node":
            n = len(store.load(graph_id).nodes)
            pos = (120 + (n % 6) * 230, 160 + (n // 6) * 180)
            nid = api.add_node(store, graph_id, a["node_type"],
                               a.get("params") or {}, position=pos)
            state.setdefault("created", set()).add(nid)  # safe to fully edit this session
            state["changed"] = True
            return {"node_id": nid}
        if name == "copy_node":
            g = store.load(graph_id)
            src = g.node(a["node_id"])  # KeyError if missing -> surfaced to model
            n = len(g.nodes)
            pos = (120 + (n % 6) * 230, 160 + (n // 6) * 180)
            nid = api.add_node(store, graph_id, src.type, dict(src.params), position=pos)
            state.setdefault("created", set()).add(nid)
            state["changed"] = True
            return {"node_id": nid, "type": src.type}
        if name == "connect":
            cid = api.connect(store, graph_id, a["from_node"], a["from_socket"],
                              a["to_node"], a["to_socket"])
            state["changed"] = True
            return {"connection_id": cid}
        if name == "set_param":
            # Guardrail: never silently rewrite the code of a custom (CodeBlock)
            # node that already existed before this session. Composing workflows
            # and configuring freshly-created custom nodes is fine; mutating an
            # existing one must go through copy_node + the user's go-ahead.
            node = store.load(graph_id).node(a["node_id"])
            params = a.get("params") or {}
            if (node.type == "CodeBlock" and "code" in params
                    and a["node_id"] not in state.get("created", ())):
                return {"error": "refused: editing the code of an existing custom "
                        "node is not allowed. Warn the user, then call copy_node "
                        "and modify the copy, leaving the original intact."}
            api.set_param(store, graph_id, a["node_id"], params)
            state["changed"] = True
            return {"ok": True}
        if name == "delete_node":
            api.delete_node(store, graph_id, a["node_id"])
            state["changed"] = True
            return {"ok": True}
        if name == "execute":
            return _lean_view(api.execute(store, graph_id))
        return {"error": f"unknown tool {name}"}

    def dispatch(name, args):
        try:
            return json.dumps(do(name, args))
        except (ValidationError, ValueError, KeyError) as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        except Exception as e:  # noqa: BLE001 — surface to the model so it can adapt
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    def fn(name, desc, props, required):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        }}

    s = {"type": "string"}
    specs = [
        fn("get_graph", "List the current nodes (id, type, params) and connections.", {}, []),
        fn("get_node_def", "Get the inputs/outputs/params of one node type.",
           {"node_type": s}, ["node_type"]),
        fn("add_node", "Add a node. Returns its node_id. params is a dict of param->value. "
           "Use type 'CodeBlock' to create a custom node from scratch (its `code` param holds build123d code).",
           {"node_type": s, "params": {"type": "object"}}, ["node_type"]),
        fn("copy_node", "Duplicate an existing node (same type and params/code) and return the new node_id. "
           "This is how you modify a custom node: copy it, then edit the COPY — never the original.",
           {"node_id": s}, ["node_id"]),
        fn("connect", "Wire from_node.from_socket -> to_node.to_socket. Errors on incompatible wires.",
           {"from_node": s, "from_socket": s, "to_node": s, "to_socket": s},
           ["from_node", "from_socket", "to_node", "to_socket"]),
        fn("set_param", "Set parameters on a node. params is a dict of param->value.",
           {"node_id": s, "params": {"type": "object"}}, ["node_id", "params"]),
        fn("delete_node", "Delete a node and its connections.", {"node_id": s}, ["node_id"]),
        fn("execute", "Run the graph; returns volume, counts, and any per-node errors.", {}, []),
    ]
    return specs, dispatch


SYSTEM = """You are CAD Copilot, an assistant inside a node-based parametric CAD app built on build123d.
You edit ONE graph by calling tools. Geometry flows through typed wires:
  geometry (solids), sketch (2D), curve, vector (points), selection (picked sub-shapes), data.
Scope — you ONLY assemble graphs; you never touch the application:
- You CANNOT and MUST NOT modify the app's code or the definition/behaviour of any
  built-in node type. You have no tool for that — never claim to have done it.
- What you may do freely: compose workflows by adding/connecting/parameterising the
  existing catalog nodes, and create brand-new CUSTOM nodes from scratch (add a
  'CodeBlock' node and write its `code`).
- A custom node is a CodeBlock. You may NOT edit the `code` of a custom node that
  already existed before this conversation. If the user wants to change such a node:
  (1) tell the user you will leave the original untouched and work on a copy,
  (2) call copy_node to duplicate it, (3) edit ONLY the copy. set_param will refuse
  an in-place code edit of a pre-existing custom node — that is expected, not a bug.
- Don't delete nodes you didn't create without saying so.

Rules:
- Call get_graph first to see what already exists; reuse nodes, don't duplicate.
- Use ONLY node types from the catalog below, with their exact socket names and wire types.
  If unsure about a node's sockets/params, call get_node_def.
- Build minimally: primitives (Box, Cylinder, Circle…) -> operations (Extrude, Revolve) ->
  booleans (Union/Subtract/Intersect) -> modifiers (Fillet, Chamfer). To place a primitive,
  wire a ConstructPoint into its optional `origin` input, or set its params.
- After making changes, call execute and check for node_errors; if any, fix and execute again.
- When done, reply briefly in the user's language: what you built and the result (volume/errors).
  Keep the final reply short — the user sees the graph update visually.

Node catalog (type [category] in:(socket:wire) out:(socket:wire) params):
%s
"""


def run_chat(graph_id: str, messages: list, store: GraphStore | None = None) -> dict:
    """Drive the tool-use loop. `messages` is the chat history [{role, content}].
    Returns {reply, changed, steps, error?}."""
    store = store or GraphStore()
    if not store.exists(graph_id):
        return {"reply": "", "changed": False, "error": f"no graph {graph_id!r}"}

    provider = _provider()
    state = {"changed": False}
    tools, dispatch = _make_tools(store, graph_id, state)
    convo = [{"role": "system", "content": SYSTEM % _compact_catalog()}] + list(messages)

    steps = 0
    try:
        while steps < MAX_STEPS:
            steps += 1
            msg = _chat(provider, convo, tools)
            convo.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                return {"reply": msg.get("content") or "", "changed": state["changed"], "steps": steps}
            for call in calls:
                fn = call["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                result = dispatch(fn["name"], args)
                convo.append({"role": "tool", "tool_call_id": call.get("id", ""),
                              "name": fn["name"], "content": result})
        return {"reply": "(reached the step limit — the graph may be partially built)",
                "changed": state["changed"], "steps": steps}
    except urllib.error.HTTPError as e:   # subclass of URLError — catch first
        detail = e.read().decode(errors="replace")[:400]
        return {"reply": "", "changed": state["changed"],
                "error": f"LLM provider error {e.code}: {detail}"}
    except urllib.error.URLError as e:
        hint = (" Is Ollama running? Start it with `ollama serve` and pull a tool-capable "
                "model, e.g. `ollama pull qwen2.5`.") if provider["local"] else ""
        return {"reply": "", "changed": state["changed"],
                "error": f"LLM provider unreachable ({provider['base']}): {e}.{hint}"}
