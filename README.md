<p align="center">
  <img src="webui/logo.svg" alt="noodle — nodes → solid" width="340">
</p>

<h1 align="center">noodle</h1>
<p align="center"><em>nodes → solid</em></p>

A **node-based parametric CAD** app, in the spirit of Grasshopper — for people who
want visual, parametric 3D modelling without writing code, with an **AI copilot**
built in from day one. (In node editors the wires between nodes are called
*noodles* — so is this.)

You wire nodes in a browser. The backend transpiles the graph to
[build123d](https://build123d.readthedocs.io) Python, runs it in an isolated
worker, and streams back an STL plus a live mesh preview for the 3D viewport.
The same operations are exposed over an **MCP server**, so any MCP-capable AI
client can read and build graphs too.

![node editor](docs/screenshot.png) <!-- add a screenshot/gif here -->

## Features

- **Visual node graph** with typed wires (geometry / sketch / curve / plane /
  vector / selection / data) and Grasshopper-style **list fan-out**.
- **Parametric primitives & ops**: solids, sketches, booleans, fillet/chamfer,
  shell, loft, sweep, revolve, plus parametric curves → frames → variable loft.
- **Interactive sub-shape selection**: click the edges/faces/vertices you want,
  persisted by geometric signature (survives parameter tweaks).
- **In-app AI copilot**: natural language → graph, over any OpenAI-compatible
  endpoint (free local Ollama by default).
- **MCP server**: drive the same operations from Claude or any MCP client.
- **Live per-node preview** with a per-node "eye" (auto / on / off).

## Quick start (Docker — recommended)

```bash
git clone https://github.com/<you>/cad-studio.git
cd cad-studio
docker compose up -d --build
```

Open the node editor at <http://localhost:8090/nodes> (a read-only build123d
code view of any graph lives at `/ui`). The B-Rep kernel (OpenCASCADE) ships
inside the build123d wheel — **nothing to compile**.

To enable the AI copilot, copy `.env.example` to `.env` and configure a provider
(or run a local [Ollama](https://ollama.com) with a tool-capable model).

> ⚠️ **Security**: the engine executes graph code (including `CodeBlock` /
> `Expression` nodes) as **arbitrary Python in a subprocess** and is **not yet
> sandboxed**. Run it **single-user / locally / trusted only** — do **not** expose
> the port to an untrusted network. Sandboxing is tracked as item **D3** in
> `PLAN_NODE_CAD.md`.

## Develop without Docker

A host virtualenv with build123d lets you transpile and execute graphs directly —
the fastest way to verify an engine change:

```bash
python -m venv .venv-b123d
.venv-b123d/bin/pip install -r requirements.txt
# transpile + run a saved graph headless (see CLAUDE.md §2 for the snippet)
```

Backend Python lives in `cad_nodes/`; the editor is `webui/nodes.html`; the API
is `server.py`. See **`CLAUDE.md`** for the full architecture map and the rules
for adding nodes, and **`PLAN_NODE_CAD.md`** for the design doc + roadmap.

## Tests

```bash
python -m pytest tests/ -v   # pure-Python: toposort, validation, transpiler, api
```

## License

**MIT** — see [`LICENSE`](LICENSE).

This project builds on third-party components with their own licenses, most
notably **OpenCASCADE / OCCT (LGPL-2.1)** via build123d. Because it is used as a
dynamically-linked library, it does not change cad-studio's MIT license. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the full map and the LGPL
compliance note.
