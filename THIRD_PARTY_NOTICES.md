# Third-party notices

cad-studio itself is licensed under the **MIT License** (see `LICENSE`). It builds
on the following third-party components, whose licenses are reproduced/located as
noted. None of these change the MIT license of cad-studio's own source, because
they are used as **dynamically-linked libraries** (Python imports) or as a
**separate process** invoked over a CLI — not statically linked or copied into
this project's source.

## Runtime libraries (Python — see `requirements.txt`)

| Component | Role | License |
|---|---|---|
| **OpenCASCADE Technology (OCCT)** — shipped as the `cadquery-ocp` wheel pulled in by build123d | B-Rep geometry kernel | **LGPL-2.1** (with the OPEN CASCADE exception) |
| build123d | high-level modelling API | Apache-2.0 |
| NumPy | numerics in the worker | BSD-3-Clause |
| SciPy | spatial helpers (Voronoi, etc.) in the worker | BSD-3-Clause |
| FastAPI | HTTP API framework | MIT |
| Starlette | ASGI toolkit (FastAPI dependency) | BSD-3-Clause |
| Uvicorn | ASGI server | BSD-3-Clause |
| python-multipart | multipart parsing | Apache-2.0 |
| mcp | Model Context Protocol SDK | MIT |

### LGPL-2.1 compliance note (OCCT)

OCCT is the only "weak copyleft" dependency. cad-studio does **not** modify OCCT
and links it only dynamically (via the `cadquery-ocp` Python extension). Under
LGPL-2.1 this permits cad-studio to remain MIT-licensed, provided we:

- keep this notice (attribution),
- do not strip OCCT's own license/copyright headers from its distribution, and
- allow users to replace the OCCT library with a modified version — which is
  inherent here, since OCCT is a separately-installed pip wheel the user can
  swap (`pip install -U cadquery-ocp`).

## Frontend (bundled/served from `webui/`)

| Component | Role | License |
|---|---|---|
| three.js | 3D viewport | MIT |
| litegraph.js (node-editor pattern) | node graph UI | MIT |

## Optional / legacy backend

| Component | Role | License |
|---|---|---|
| **OpenSCAD** (CLI binary, used by `backends/openscad.py`) | alternate/legacy backend | **GPL-2.0-or-later** |

OpenSCAD is **not part of the MIT-licensed core** and is invoked as a separate
process ("mere aggregation"), so it does not impose GPL on cad-studio. It is
**not required** for the default build123d pipeline. If you redistribute an image
that bundles the OpenSCAD binary, that binary remains under GPL-2.0+ and you must
honor its terms for that component; cad-studio's own source stays MIT.
