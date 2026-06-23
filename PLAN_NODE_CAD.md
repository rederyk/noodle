# CAD Studio v2 — Node-Based CAD con build123d + MCP nativo

## Filosofia

> Tutti i nodi sono rappresentabili in codice, ma non il contrario.

I nodi sono **blocchi di codice build123d preimpostati**, organizzati visivamente come Grasshopper. Il grafo genera codice Python eseguibile, modificabile e ispezionabile. Un nodo `CodeBlock` permette di scrivere codice libero — che a sua volta può diventare un nuovo nodo salvato nel catalogo.

Due modalità coesistono:
- **Algebra mode** (funzionale): `result = Box(10,20,30) + Cylinder(5, 40) - Sphere(10)`
- **Builder mode** (contestuale Grasshopper-style): `with BuildPart()... extrude()... fillet()...`

La node UI espone entrambe e le traduce automaticamente.

---

## Architettura

```
┌──────────────────────────────────────────────────────────────┐
│                       BROWSER                                │
│  ┌──────────────────────┐    ┌─────────────────────┐        │
│  │  Litegraph.js        │    │  Three.js Viewer    │        │
│  │  (nodi, wire,        │    │  (rotazione, zoom,  │        │
│  │   gruppi, pannelli)  │    │   selezione, sez.)  │        │
│  └──────┬───────────────┘    └────────┬────────────┘        │
│         │ graph JSON (fetch/WS)       │ mesh data            │
└─────────┼─────────────────────────────┼──────────────────────┘
          │                             │
┌─────────▼─────────────────────────────▼──────────────────────┐
│                   FASTAPI :8090                               │
│                                                              │
│  ┌────────────────────┐  ┌────────────────────┐             │
│  │  REST API           │  │  MCP Server        │            │
│  │  /api/graph/*       │  │  (FastMCP SDK v2)  │            │
│  │  /api/code          │  │  SSE transport     │            │
│  │  /api/export        │  │  per AI agent      │            │
│  └────────┬───────────┘  └─────────┬──────────┘             │
│           │                        │                          │
│  ┌────────▼────────────────────────▼─────────┐              │
│  │  Graph Runtime Engine                      │             │
│  │  - topological sort                        │             │
│  │  - node→build123d code transpiler          │             │
│  │  - builder context nesting                 │             │
│  │  - data tree management                    │             │
│  │  - mesh extraction → view JSON             │             │
│  └────────────────┬───────────────────────────┘             │
│                   │                                          │
│  ┌────────────────▼───────────────────────────┐             │
│  │  build123d (kernel OpenCASCADE)            │             │
│  │  - primitives, booleans, operations        │             │
│  │  - export: STEP, STL, 3MF, glTF, SVG, DXF │             │
│  └────────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────────┘
```

---

## Catalogo dei Nodi

### 1. Primitive 3D

| Nodo | Parametri | Output | Code |
|---|---|---|---|
| `Box` | w, h, d (`float`) | Shape | `Box(10, 20, 5)` |
| `Cylinder` | radius, height | Shape | `Cylinder(5, 20)` |
| `Sphere` | radius | Shape | `Sphere(10)` |
| `Cone` | bottom_radius, top_radius, height | Shape | `Cone(5, 2, 15)` |
| `Torus` | major_radius, minor_radius | Shape | `Torus(15, 3)` |
| `Wedge` | xsize, ysize, zsize, xmin, xmax, zmin, zmax | Shape | `Wedge(20,10,15,0,10,0,5)` |
| `Frustum` | xsize, ysize, zsize, xoff, yoff | Shape | `Frustum(10,10,15,3,3)` |

### 2. Primitive 2D (Sketch/Curve)

| Nodo | Parametri | Output | Code |
|---|---|---|---|
| `Rectangle` | width, height | Sketch | `Rectangle(30, 20)` |
| `RoundedRectangle` | w, h, radius | Sketch | `RoundedRectangle(30,20, 3)` |
| `Circle` | radius | Sketch | `Circle(10)` |
| `Ellipse` | x_radius, y_radius | Sketch | `Ellipse(8, 5)` |
| `Polygon` | radius, sides | Sketch | `Polygon(10, 6)` |
| `Star` | outer_r, inner_r, points | Sketch | `Star(10, 4, 5)` |
| `SlotCenterToCenter` | width, height | Sketch | `SlotCenterToCenter(15, 5)` |
| `SlotCenterPoint` | width, height | Sketch | `SlotCenterPoint(15, 5)` |
| `Line` | start, end (point tuples) | Curve | `Line((0,0,0), (10,0,0))` |
| `Polyline` | points (list) | Curve | `Polyline([(0,0),(10,0),(10,10)])` |
| `Bezier` | control_points | Curve | `Bezier([p0, p1, p2, p3])` |
| `Spline` | points, tangents | Curve | `Spline(points, tangents)` |
| `Text` | text_string, font_size | Sketch | `Text("Hello", font_size=10)` |

### 3. Operazioni 2D→3D

| Nodo | Input | Parametri | Output | Code |
|---|---|---|---|---|
| `Extrude` | sketch | amount, taper | Part | `extrude(sketch, amount=15)` |
| `Revolve` | sketch | angle_deg, axis | Part | `revolve(sketch, 360, Axis.Z)` |
| `Sweep` | sketch, path | — | Part | `sweep(sketch, path)` |
| `Loft` | sketches[] | — | Part | `loft(*sections)` |
| `Thicken` | sketch | thickness | Part | `thicken(sketch, 2.5)` |
| `MakeFace` | edges | — | Part | `make_face(wire)` |
| `Solid` | faces[] | — | Part | `solid(faces)` |

### 4. Booleane (CSG)

| Nodo | Input | Output | Code |
|---|---|---|---|
| `Union` | A, B (o più) | Shape | `A + B` |
| `Subtract` | A, B | Shape | `A - B` |
| `Intersect` | A, B | Shape | `A * B` |
| `BooleanMulti` | shapes[] | Shape | `fuse(*shapes)` — unione N oggetti |

### 5. Modificatori

| Nodo | Input | Parametri | Output | Code |
|---|---|---|---|---|
| `Fillet` | part | radius, [edges] | Shape | `fillet(part, radius=2)` |
| `Chamfer` | part | length, [edges] | Shape | `chamfer(part, length=1.5)` |
| `Offset` | shape | amount (float) | Shape | `offset(shape, amount=2)` |
| `Shell` | part | thickness | Shape | `shell(part, thickness=1)` |
| `Split` | shape | plane | Shape | `split(shape, Plane.XY)` |
| `Section` | shape | plane | Sketch | `section(shape, Plane.XY)` |
| `Draft` | part | angle, neutral_plane | Shape | `draft(part, 5, Plane.XY)` |

### 6. Trasformazioni

| Nodo | Input | Parametri | Output | Code |
|---|---|---|---|---|
| `Move` | shape | x, y, z | Shape | `Pos(x,y,z) * shape` |
| `Rotate` | shape | angle, axis, [origin] | Shape | `Rot(angle, axis) * shape` |
| `Scale` | shape | factor (x,y,z) | Shape | `scale(shape, (sx,sy,sz))` |
| `Mirror` | shape | plane | Shape | `mirror(shape, Plane.XZ)` |
| `Align` | shape | ref_point, target_point | Shape | `Pos(target - ref) * shape` |
| `ArrayLinear` | shape | count, step_vector | Shape[] | `[Pos(i*v) * shape for i in range(n)]` |
| `ArrayPolar` | shape | count, angle | Shape[] | `[Rot(i*a/n, Axis.Z) * shape for i...]` |

### 7. Plane / Location (Grasshopper-style)

| Nodo | Parametri | Output | Code |
|---|---|---|---|
| `PlaneOrigin` | — | Plane | `Plane.XY` |
| `PlaneXYZ` | x, y, z | Plane | `Plane((x,y,z))` |
| `PlaneNormal` | origin, normal_vector | Plane | `Plane(origin, normal)` |
| `PlaneRotated` | plane, angle, axis | Plane | `Plane(origin, x_dir, z_dir)` |
| `MovePlane` | plane, translation | Plane | `plane.offset(translation)` |

### 8. Vettori e Punti

| Nodo | Input | Parametri | Output | Code |
|---|---|---|---|---|
| `Point3D` | x, y, z | — | tuple | `(x, y, z)` |
| `Vector` | x, y, z | — | tuple | `Vector(x, y, z)` |
| `CrossProduct` | v1, v2 | — | Vector | `v1.cross(v2)` |
| `DotProduct` | v1, v2 | — | float | `v1.dot(v2)` |
| `Normalize` | v | — | Vector | `v.normalized()` |
| `Distance` | p1, p2 | — | float | `p1.distance_to(p2)` |
| `Midpoint` | p1, p2 | — | tuple | `(p1 + p2) / 2` |
| `DeconstructVector` | v | — | x, y, z | `v.x, v.y, v.z` |
| `ConstructVector` | x, y, z | — | Vector | `Vector(x, y, z)` |

### 9. Liste e Data Trees (Grasshopper data management)

Questi sono nodi **fondamentali** per mantenere la filosofia Grasshopper — tutto scorre in liste e alberi.

| Categoria | Nodo | Input | Parametri | Output | Code |
|---|---|---|---|---|---|
| **Creazione** | `ListCreate` | items[] (N slot) | — | list | `[item_0, item_1, ...]` |
| | `ListRange` | — | start, count, step | list | `list(range(start, start+count*step, step))` |
| | `ListSeries` | — | start, step, count | list | `[start + i*step for i in range(count)]` |
| | `ListRepeat` | item | n | list | `[item] * n` |
| | `ListRandom` | — | count, seed, min, max | list | `[random.uniform(min,max) for _ in range(n)]` |
| **Accesso** | `ListItem` | list | index (int) | any | `list[idx]` |
| | `ListSlice` | list | start, stop, step | list | `list[start:stop:step]` |
| | `ListFirst` | list | — | any | `list[0]` |
| | `ListLast` | list | — | any | `list[-1]` |
| | `ListLength` | list | — | int | `len(list)` |
| **Mutazione** | `ListShift` | list | offset | list | `list[offset:] + list[:offset]` |
| | `ListReverse` | list | — | list | `list[::-1]` |
| | `ListSort` | list | key (str) | list | `sorted(list, key=lambda x: x.key)` |
| | `ListFilter` | list | condition_callable | list | `[x for x in list if cond(x)]` |
| | `ListMap` | list | expr_code | list | `[expr(x) for x in list]` |
| | `ListUnique` | list | — | list | `list(set(list))` |
| | `ListFlatten` | nested_list | depth | list | flatten fino a N livelli |
| **Combinazione** | `Concat` | list1, list2 | — | list | `list1 + list2` |
| | `Zip` | list1, list2 | — | list | `list(zip(list1, list2))` |
| | `CrossRef` | list1, list2 | — | list | prodotto cartesiano tra due liste |
| | `Graft` | list | — | tree | `[[item] for item in list]` (ogni item→branch) |
| | `UnflattenTree` | flat_list | structure | tree | ricostruisce albero da struttura |
| **Data Tree** | `TreeCreate` | branches[] | — | tree | `{0: [...], 1: [...]}` |
| | `TreeBranch` | tree, path | — | list | `tree[path]` |
| | `TreePaths` | tree | — | list | `sorted(tree.keys())` |
| | `TreeFlatten` | tree | — | list | flatten completo |
| | `TreeGraft` | list | — | tree | ogni elemento diventa un ramo |
| | `TreeSimplify` | tree | — | tree | rimuove path ridondanti |
| | `TreeTrim` | tree | depth | tree | tronca path a N livelli |
| | `TreeMerge` | tree1, tree2 | — | tree | merge con ricongiunzione rami |
| **Match** | `ListMatch` | list, pattern | — | list | pattern matching su stringhe |
| | `ListMember` | item, list | — | bool | `item in list` |
| | `ListIndexOf` | item, list | — | int | `list.index(item)` |

### 10. Matematica

| Nodo | Input | Output | Code |
|---|---|---|---|
| `Add` | a, b | float | `a + b` |
| `Subtract` | a, b | float | `a - b` |
| `Multiply` | a, b | float | `a * b` |
| `Divide` | a, b | float | `a / b` |
| `Power` | base, exp | float | `base ** exp` |
| `Sin/Cos/Tan` | angle_deg | float | `math.sin(math.radians(angle))` |
| `ArcSin/Cos/Tan` | value | float (deg) | `math.degrees(math.asin(v))` |
| `Log` | x, base | float | `math.log(x, base)` |
| `Round` | x, decimals | float | `round(x, decimals)` |
| `Floor/Ceil` | x | int | `math.floor(x)` |
| `Min/Max` | values[] | float | `min(values)` |
| `Clamp` | x, min, max | float | `max(min, min(x, max))` |
| `Remap` | x, src_min, src_max, tgt_min, tgt_max | float | `tgt_min + (x-src_min)/(src_max-src_min)*(tgt_max-tgt_min)` |
| `GCD/LCM` | a, b | int | `math.gcd(a, b)` |
| `Expression` | variabili[] | expr_string | float | `eval(expr, {"x": x, "y": y, ...})` |
| `Random` | count, min, max, seed | list | random con seed deterministico |
| `Noise2D/3D` | x, y, [z] | float | Perlin/Simplex noise |

### 11. Pannelli e Visual Debug (Grasshopper philosophy)

| Nodo | Input | Output | Funzione |
|---|---|---|---|
| `Panel` | any (data) | text | **Mostra il valore** come testo live — ispezione dati. Equivale al panel giallo di GH |
| `TextTag` | string, position | 3D text | Mostra testo nel viewport 3D |
| `GeometryPreview` | shape | viewport | Forza la visualizzazione shape nel viewport (anche nodi intermedi) |
| `BoundingBox` | shape | box_shape + data | Mostra bbox e ne espone min, max, size, center |
| `Measure` | shape | dict | Volume, area, centro di massa, momenti di inerzia |
| `PrintLine` | any | — | `print(value)` nei log del server |
| `Inspect` | any | tree | Mostra struttura dati (tipo, lunghezza, profondità) |
| `Watch3D` | shape | mesh JSON | Anteprima 3D dedicata con colori diversi per shape |

I pannelli sono cruciali per:
- Debug visuale del flusso dati
- Capire cosa passa tra i nodi
- Ispezionare liste e data trees
- Misurare geometria (volume, bbox, massa)

### 12. Input / Parametri

| Nodo | Widget | Output | Code |
|---|---|---|---|
| `NumberSlider` | slider | float | `5.0` |
| `IntegerSlider` | slider | int | `5` |
| `NumberInput` | text field | float | `10.0` |
| `BooleanToggle` | checkbox | bool | `True` |
| `ColorPicker` | color picker | tuple | `(255, 0, 0)` |
| `Dropdown` | select menu | str | `"option_2"` |
| `FilePath` | file dialog | str | `"/path/to/file.step"` |
| `StringInput` | text | str | `"label"` |
| `PointPicker` | 3D click | tuple | `(10.0, 5.0, 0.0)` |
| `CurvePicker` | select edges | Curve[] | edge selezionate nel viewport |
| `FacePicker` | select faces | Face[] | facce selezionate nel viewport |
| `DatePicker` | calendar | str | `"2026-06-22"` |

### 13. Export / I/O

| Nodo | Input | Parametri | Output | Code |
|---|---|---|---|---|
| `ExportSTEP` | shape | path | file | `export_step(shape, path)` |
| `ExportSTL` | shape | path | file | `export_stl(shape, path)` |
| `Export3MF` | shape | path | file | `export_3mf(shape, path)` |
| `ExportglTF` | shape | path | file | `export_gltf(shape, path)` |
| `ExportSVG` | shape | path, plane | file | `export_svg(shape, path)` |
| `ExportDXF` | shape | path, plane | file | `export_dxf(shape, path)` |
| `ImportSTEP` | path | — | Shape | `import_step(path)` |
| `ImportSTL` | path | — | Shape | `import_stl(path)` |
| `ImportSVG` | path | — | Sketch | `import_svg(path)` |

### 14. Gruppi e Contesto (Builder Mode)

| Nodo | Contenuto | Output | Code |
|---|---|---|---|
| `BuildPart` | figli (subnodi) | Part | `with BuildPart() as ctx: ...` |
| `BuildSketch` | figli | Sketch | `with BuildSketch(plane) as ctx: ...` |
| `BuildLine` | figli | Wire | `with BuildLine() as ctx: ...` |
| `Add` | shape | — | `add(shape)` — aggiunge a contesto attivo |

I nodi gruppo possono essere annidati:

```
[BuildPart: label="Flangia"]                    with BuildPart() as bp:
  ├─ [BuildSketch: plane=Plane.XY]                with BuildSketch() as sk:
  │   └─ [Circle: r=20]                              Circle(20)
  ├─ [Extrude: amount=10]                          extrude(amount=10)
  ├─ [BuildSketch: plane=Plane.XY.offset(5)]      with BuildSketch(Plane.XY.offset(5)):
  │   └─ [Circle: r=10]                              Circle(10)
  └─ [Extrude: amount=15, mode=SUBTRACT]          extrude(amount=15, mode=Mode.SUBTRACT)
```

Nota: `mode=SUBTRACT` è un concetto Grasshopper che build123d supporta nativamente come `Mode.SUBTRACT` nelle operazioni Booleane in builder mode.

### 15. CodeBlock — Il Nodo Universale

```python
# NODO: CodeBlock
# Inputs: in_0, in_1, ... (wire collegati da altri nodi)
# Parametri: code_text (editor di testo, syntax highlighting Python)
# Output: result

# Il codice utente può referenziare input come variabili:
radius = in_0 if in_0 else 10
height = in_1 if in_1 else 30

with BuildPart() as custom:
    Cylinder(radius, height)
    fillet(vertices().group_by(Axis.Z)[0], radius = radius * 0.1)

result = custom.part
```

Un CodeBlock può essere salvato come nuovo nodo nel catalogo (es. `CustomScrew`, `ThreadedHole`).

---

## Node Definition (JSON Schema)

Ogni nodo è definito da questo schema:

```json
{
  "type": "Extrude",
  "category": "operations",
  "label": "Estrudi",
  "icon": "extrude.png",

  "inputs": [
    {"name": "shape", "type": "Shape", "wire_type": "geometry", "required": true, "multiple": false}
  ],

  "params": [
    {"name": "amount", "label": "Altezza", "type": "float", "default": 10.0, "min": 0.1, "max": 500,
     "widget": "slider", "step": 0.5},
    {"name": "taper", "label": "Angolo sformo", "type": "float", "default": 0.0, "min": -45, "max": 45,
     "widget": "slider", "step": 0.5, "optional": true},
    {"name": "mode", "label": "Modalità", "type": "select", "default": "union",
     "options": ["union", "subtract", "intersect", "new_body"]}
  ],

  "outputs": [
    {"name": "result", "type": "Part", "wire_type": "geometry"}
  ],

  "code_template": {
    "algebra": "extrude({shape}, amount={amount}, taper={taper})",
    "builder": "extrude(amount={amount}, taper={taper})"
  },

  "imports": ["from build123d import *"],

  "python_callable": "extrude",
  "description": "Estrude uno sketch 2D in un solido 3D di altezza specificata"
}
```

I `wire_type` categorizzano i tipi di connessione:

| wire_type | Colore connessione | Descrizione |
|---|---|---|
| `geometry` | 🟢 verde | Shape 3D / Part |
| `sketch` | 🔵 blu | Sketch 2D |
| `curve` | 🟡 giallo | Curve / Wire |
| `data` | ⚪ grigio | List, float, int, str |
| `tree` | 🟣 viola | Data tree |
| `plane` | 🟠 arancione | Plane / Location |
| `vector` | 🔴 rosso | Vector / Point |

---

## Graph → Code Transpiler

### Algoritmo

```
1. Topological sort del DAG (Kahn algorithm)
2. Identifica Group nodes (BuildPart, BuildSketch, BuildLine)
   → nested blocks with indentation
3. Per ogni nodo non-group:
   a. Leggi wire_type delle connessioni → algebra o builder mode
   b. Sostituisci placeholder {input_name} con variabili __out_N
   c. Assegna output a variabile __out_N
4. Per Group nodes:
   a. Apri `with BuildPart() as __ctx_N:`
   b. Ricorsione sui figli
   c. Output = `__ctx_N.part` o `.sketch` o `.wire`
5. Inietta imports in testa
6. Aggiungi export (se nodi Export presenti)
```

### Esempio — Flangia con fori

Grafo:

```
                     ┌────────────────────────┐
        ┌────────────┤  NumberSlider {value:5} ├────┐
        │            └────────────────────────┘    │
        │                                          │
┌───────▼──────┐                     ┌─────────────▼──────┐
│  Circle(r=20)│                     │  Circle(r=5)        │
└───────┬──────┘                     └────────┬────────────┘
        │ sketch                              │ sketch
        ▼                                      ▼
┌───────▼──────┐                     ┌────────▼──────────┐
│  Extrude(10) │                     │  Extrude(10)       │
└───────┬──────┘                     └────────┬──────────┘
        │ part                               │ part
        │                                     │
        └──────────────┬─────────────────────┘
                       ▼
              ┌────────▼────────┐
              │  Subtract(B-A)  │
              └────────┬────────┘
                       │ part
                       ▼
              ┌────────▼────────┐
              │  ExportSTEP     │
              └─────────────────┘
```

Codice generato:

```python
from build123d import *

# Nodi Parameter
__out_01 = 5.0  # NumberSlider

# Nodi Primitive
__out_02 = Circle(radius=20)           # Circle esterno
__out_03 = Circle(radius=__out_01)     # Circle foro (parametro dal slider)

# Nodi Operation
__out_04 = extrude(__out_02, amount=10)  # Base
__out_05 = extrude(__out_03, amount=10)  # Foro

# Boolena
__out_06 = __out_04 - __out_05          # Sottrai foro dalla base

# Export
export_step(__out_06, "flangia.step")

# Output per viewport
__result__ = __out_06
```

### Esempio — Builder Mode con CodeBlock

Grafo con `BuildPart` e `CodeBlock`:

```
[BuildPart: label="Supporto"]
  ├── [CodeBlock: code="...custom..."]
  └── [Fillet: radius=2]
```

Codice generato:

```python
from build123d import *

# CodeBlock custom
with BuildPart() as __ctx_00:
    # (codice utente dal CodeBlock)
    with BuildSketch(Plane.XY) as __ctx_01:
        Rectangle(40, 20)
    extrude(amount=10)
    with BuildSketch(Plane.XY.offset(10)) as __ctx_02:
        Rectangle(20, 10)
    extrude(amount=5)
__out_00 = __ctx_00.part

# Fillet
__out_01 = fillet(__out_00, radius=2)

__result__ = __out_01
```

---

## MCP Interface — Per l'AI Agent

### Resources

| Resource | JSON Output |
|---|---|
| `cad://nodes` | Catalogo nodi completo (tipo, parametri, input/output, descrizione) |
| `cad://nodes/{type}` | Dettaglio di un singolo tipo nodo |
| `cad://graph/{id}` | Graph completo: `{nodes, connections, params}` |
| `cad://graph/{id}/code` | `{code: "Python generato..."}` |
| `cad://graph/{id}/view` | `{status, bbox, volume, area, centro, facce, vertici, edges, solidi}` |
| `cad://graph/{id}/view/mesh` | `{vertices: [...], triangles: [...]}` — mesh completa per rendering |
| `cad://graph/{id}/export/{fmt}` | URL per scaricare STEP/STL/3MF/glTF |
| `cad://graph/{id}/panels` | Output di tutti i nodi Panel nel grafo |

### Tools

```python
# Graph lifecycle
@mcp.tool()
def cad_create_graph(name: str, description: str = "") -> str:
    """Crea un nuovo grafo vuoto. Restituisce graph_id."""

@mcp.tool()
def cad_add_node(graph_id: str, node_type: str, params: dict,
                  position: tuple[float,float]) -> str:
    """Aggiunge un nodo al grafo. params = {param_name: value}.
    position = (x, y) in pixel sul canvas.
    Restituisce node_id."""

@mcp.tool()
def cad_connect(graph_id: str, from_node_id: str, from_socket: str,
                to_node_id: str, to_socket: str) -> bool:
    """Collega output di un nodo a input di un altro."""

@mcp.tool()
def cad_set_param(graph_id: str, node_id: str, params: dict) -> bool:
    """Modifica parametri di un nodo esistente."""

@mcp.tool()
def cad_set_code(graph_id: str, node_id: str, code: str) -> bool:
    """Imposta il codice di un nodo CodeBlock."""

@mcp.tool()
def cad_delete_node(graph_id: str, node_id: str) -> bool:
    """Rimuove un nodo e le sue connessioni."""

@mcp.tool()
def cad_delete_connection(graph_id: str, connection_id: str) -> bool:
    """Rimuove una connessione specifica."""

# Execution
@mcp.tool()
def cad_execute(graph_id: str) -> dict:
    """Esegue il grafo completo.
    Returns: {success, errors, warnings, code, view_summary, panels}"""

# Inspection
@mcp.tool()
def cad_get_view(graph_id: str, format: str = "json") -> dict:
    """Vista 3D strutturata per AI. format: json|mesh|screenshot"""

@mcp.tool()
def cad_get_panel(graph_id: str, panel_id: str) -> dict:
    """Legge il valore corrente di un nodo Panel (debug)."""

# Export
@mcp.tool()
def cad_export(graph_id: str, format: str = "step") -> str:
    """Esporta il modello. format: step|stl|3mf|gltf|svg|dxf.
    Restituisce URL del file."""

@mcp.tool()
def cad_import(graph_id: str, file_path: str, format: str = "step") -> str:
    """Importa un modello esistente come nodo iniziale."""

# Node catalog management
@mcp.tool()
def cad_save_codeblock_as_node(graph_id: str, codeblock_id: str,
                                name: str, category: str) -> bool:
    """Salva un CodeBlock come nuovo tipo nodo nel catalogo."""

@mcp.tool()
def cad_get_node_catalog(filter_category: str = "") -> list:
    """Lista tipi nodo disponibili, opzionalmente filtrati per categoria."""

# Data management
@mcp.tool()
def cad_list_data(graph_id: str, node_id: str = "") -> dict:
    """Ispeziona i dati che scorrono in un nodo o in tutto il grafo."""
```

### Prompt Templates

```python
@mcp.prompt()
def cad_design(descrizione: str) -> str:
    return f"""Progetta un pezzo meccanico per: "{descrizione}"

1. **Analizza** la descrizione: che forma base? fori? bordi smussati?
2. **Crea un grafo** con `cad_create_graph`
3. **Aggiungi primitive** (Box, Cylinder, Circle...) con parametri iniziali
4. **Connetti** con Booleane (Union, Subtract)
5. **Applica modificatori** (Fillet, Chamfer) per dettagli
6. **Aggiungi pannelli** Panel per ispezionare dimensioni intermedie
7. **Esegui** con `cad_execute` e leggi il view JSON
8. **Itera**: regola parametri con `cad_set_param` finché le misure sono corrette
9. **Esporta** in STEP con `cad_export`

Mostra sempre il view JSON dopo ogni esecuzione per verificare le dimensioni."""

@mcp.prompt()
def cad_modify(istruzioni: str) -> str:
    return f"""Modifica il modello corrente seguendo: "{istruzioni}"

1. **Leggi** il view JSON del grafo corrente (`cad_get_view`)
2. **Analizza** volume, bbox, facce per capire cosa modificare
3. **Regola parametri** esistenti o **aggiungi nuovi nodi**
4. **Esegui** e verifica il risultato
5. **Conferma** le modifiche con `cad_export` solo se soddisfacente"""

@mcp.prompt()
def cad_analyze() -> str:
    return """Analizza il modello corrente:

1. Esegui il grafo se non ancora eseguito
2. Leggi view JSON: volume, superficie, bbox, centro di massa
3. Identifica: quante facce? quanti solidi? edge count?
4. Se ci sono nodi Panel, leggine i valori
5. Dai un riassunto strutturale del modello"""
```

---

## Piano di Implementazione

### Fase 0 — Setup (30 min)
- [ ] Dockerfile: `build123d` → sostituisce `cadquery==2.7.0`
- [ ] Aggiungi `mcp`, `numpy` al requirements
- [ ] Crea cartella `cad_nodes/` con struttura modulare

### Fase 1 — Core Engine (2-3 giorni)
- [ ] `cad_nodes/node_catalog.py`: catalogo nodi in JSON (tutti i tipi sopra)
- [ ] `cad_nodes/graph.py`: Graph model + serialization JSON
- [ ] `cad_nodes/toposort.py`: Kahn topological sort del DAG
- [ ] `cad_nodes/transpiler.py`: graph → codice build123d Python
- [ ] `cad_nodes/executor.py`: esecuzione codice in subprocess venv
- [ ] `cad_nodes/mesh_extractor.py`: Shape → view JSON (bbox, volume, vertici, facce)
- [ ] Test CLI: crea grafo manuale → transpila → esegui → ispeziona output

### Fase 2 — MCP Server (1-2 giorni)
- [ ] `mcp_server.py`: FastMCP con tutti i tools sopra
- [ ] Lifecycle: init engine → context condiviso
- [ ] Resources: graph, code, view, export
- [ ] Tools: graph CRUD + execute + export
- [ ] Prompts: design, modify, analyze
- [ ] Test: connetti con MCP Inspector, verifica tutti i tools

### Fase 3 — REST API + Viewport (1 giorno)
- [ ] Aggiorna `server.py` con nuovi endpoint:
  - `POST /api/graph` — crea
  - `GET /api/graph/{id}` — leggi
  - `POST /api/graph/{id}/node` — aggiungi nodo
  - `POST /api/graph/{id}/connect` — connetti
  - `POST /api/graph/{id}/execute` — esegui
  - `GET /api/graph/{id}/view` — vista 3D JSON
  - `GET /api/graph/{id}/export/{format}` — download
- [ ] WebSocket `/ws/graph/{id}` — stream esecuzione (log, errori)
- [ ] Serve frontend static

### Fase 4 — Frontend Litegraph.js (3-4 giorni)
- [ ] Scaffold frontend (HTML+JS, Litegraph.js + Three.js)
- [ ] Registra tutti i nodi CAD nel Litegraph registry
- [ ] Widget parametri: slider, input, dropdown, color picker
- [ ] 3D Viewer Three.js: STL/glTF loader, OrbitControls, selezione facce
- [ ] Pannelli Panel: display valori in tempo reale
- [ ] Codice generato: pannello laterale syntax-highlighted
- [ ] Auto-save grafo su localStorage
- [ ] Tema scuro (stile Grasshopper/ComfyUI)

### Fase 5 — CodeBlock + User Nodes (1 giorno)
- [ ] Editor Python nel nodo CodeBlock (CodeMirror o Monaco)
- [ ] Salva CodeBlock come nuovo tipo nodo
- [ ] Catalogo utente (JSON, caricabile)

### Fase 6 — Integrazione nanobot (1 giorno)
- [ ] Skill CAD: `/cad` comandi in chat
- [ ] MCP auto-avvio con CAD Studio
- [ ] Notifica su Matrix quando modello pronto
- [ ] `/cad status`, `/cad export`, `/cad design "descrizione"`

---

## Data Flow Architecture (dettaglio)

### Esecuzione Grafo

```
Input: graph JSON

1. Validate (check connessioni, tipi compatibili)
2. Topological sort (detect cicli)
3. Per ogni Group node:
   ├─ Raccogli figli ricorsivamente
   └─ Genera blocco `with BuildX() as ctx_N:`
4. Per ogni leaf node:
   ├─ Genera variabile __out_N = <code_template>
   └─ Rimpiazza placeholder con __out_M delle connessioni
5. Aggiungi linea `__result__ = <ultimo output>`
6. Aggiungi imports in testa
7. Esegui in subprocess Python (sandboxato)
8. Cattura stdout/stderr
9. Estrai mesh da risultato → view JSON
10. Se nodi Export presenti → salva file

Output: {success, code, stdout, stderr, view, panels, exports}
```

### List Handling

Le liste scorrono nei wire come qualsiasi altro tipo. Un nodo `Extrude` collegato a un output `Circle` che emette una lista di cerchi → estrude tutti i cerchi:

```
Circle(r=5) ──list──> Extrude(amount=10) ──list──> ExportSTEP
```

Il runtime rileva automaticamente che l'input è una lista e applica la funzione a ogni elemento (map automatico), mantenendo la struttura dati — esattamente come fa Grasshopper.

Un nodo `Panel` collegato a un output lista mostra:

```
Panel: __out_03
Type: list[Shape]
Length: 5
[0]: Shape(vertices=42, faces=20)
[1]: Shape(vertices=42, faces=20)
...
```

---

## Grasshopper vs build123d: Mappatura Concettuale

| Grasshopper | build123d (questo sistema) |
|---|---|
| `Number Slider` | `NumberSlider` nodo |
| `Circle` (params/primitive) | `Circle(r)` nodo |
| `Extrude` (surface/freeform) | `Extrude(sketch, amount)` |
| `Boolean Union` (sets/math) | `Union(A, B)` → `A + B` |
| `Fillet` (surface/fillet) | `Fillet(part, radius)` |
| `Panel` (params/input) | `Panel(any)` |
| `List` / `List Item` | `ListCreate` / `ListItem` |
| `Graft` / `Flatten` (tree) | `TreeGraft` / `TreeFlatten` |
| `Point` (vec/pt) | `Point3D(x, y, z)` |
| `Move` (transform/euclidean) | `Move(shape, x, y, z)` |
| `Rotate` (transform/euclidean) | `Rotate(shape, angle, axis)` |
| `Series` (sets/sequence) | `ListSeries(start, step, count)` |
| `Random` (sets/sequence) | `ListRandom(count, min, max, seed)` |
| `Construct Domain` | — (gestito da min/max nei parametri) |
| `Bounds` (analysis) | `BoundingBox(shape)` |
| `Volume` (analysis) | `Measure(shape)` — volume, area, centro |
| `Data` (params/primitive) | `Panel` + `NumberInput` + `StringInput` |
| `Dispatch` (sets/list) | `ListFilter(list, condition)` |
| `Merge` (sets/list) | `Concat(list1, list2)` |
| `Cross Reference` (sets/list) | `CrossRef(list1, list2)` |

---

## Note Tecniche

### Python Sandbox
- Esecuzione in subprocess con `venv` dedicato
- Timeout configurabile (default 120s)
- `build123d` import automatico con `from build123d import *`
- Sicurezza: nessun accesso a filesystem fuori da `/tmp/outputs/`
- `eval` limitato per Expression node (solo math + numpy)

### build123d Details
- `from build123d import *` include: Box, Sphere, Cylinder, Cone, Torus, extrude, fillet, chamfer, Pos, Rot, Plane, Axis, Mode, Vector, scale, mirror, offset, sweep, loft, section, split, thicken, make_face, solid, export_step, export_stl, export_3mf, export_gltf, import_step, import_stl
- Builder mode: `BuildPart`, `BuildSketch`, `BuildLine`, `add()`
- Algebra mode: funzioni pure e operator `+`, `-`, `*`
- Selectors: `vertices()`, `edges()`, `faces()`, `wires()`, `solids()` + `.group_by(Axis.Z)` + `.min`/`.max`

### Performance Note
- build123d (OCCT kernel) è già 5-8× più veloce di OpenSCAD/CGAL
- Per preview rapide: mesh estrazione via OCCT → JSON compresso (solo vertici+triangoli)
- Per export: STEP è diretto dal kernel, STL via tassellazione
- Data trees: liste Python native, per N>100000 usare numpy o lazy evaluation

---

## Roadmap / Suggerimenti (post-Fase 4)

> Visione: rimanere **semplici** e **integrati con l'AI fin da subito**, con la
> massima personalizzazione. Target: chi oggi usa Grasshopper a livello
> amatoriale. Potenziale progetto opensource.

### A. Anteprima 3D in tempo reale (no "Run" manuale)
Oggi bisogna premere **Run** per vedere il risultato. Obiettivo: ri-eseguire
automaticamente (debounce ~300-500ms) al cambio di un parametro/connessione e
aggiornare il viewer Three.js.
- Backend: l'engine è già subprocess-per-run; per il realtime serve un worker
  persistente con build123d caricato (evita il costo di import a ogni run) e
  uno stream WebSocket `/ws/graph/{id}` (già previsto in Fase 3) che spinge
  mesh/errori.
- Frontend: hook su `lgraph.onNodeConnectionChange` / widget callback → run
  debounced → `loadSTL`/mesh diretta dal `view.mesh` (già prodotta) invece di
  rifare il download STL.
- Incrementale: rieseguire solo il sotto-grafo a valle del nodo cambiato.

### B. Errori topologici precisi + auto-fallback
Casi tipici da gestire bene: **fillet/chamfer con raggio troppo grande**,
**boolean che annulla la geometria** (risultato vuoto), sketch auto-intersecanti,
revolve di profilo che attraversa l'asse (vedi nodo Revolve).
- Mappare l'eccezione OCCT (es. `StdFail_NotDone`, `BRep_API: command not done`)
  → messaggio in linguaggio umano + **nodo colpevole evidenziato** sul canvas.
  Serve correlare l'errore alla riga di codice generata → al `node_id` (il
  transpiler già annota `# {node.type}`; aggiungere `# node={id}`).
- Rilevare risultato vuoto: `volume≈0` / `solids==0` dopo un boolean → warning
  "questa operazione elimina tutta la geometria".
- **Auto-fallback su min/max**: per parametri numerici con `min`/`max` nel
  catalogo (già presenti), se il valore fa fallire l'op, ritentare con un
  bisection verso un valore valido (es. fillet: dimezzare il raggio finché
  passa) e proporlo all'utente.

### C. Chat AI integrata (API OpenAI-compatible)
Pannello chat nella UI che parla con un endpoint OpenAI-compatible
(configurabile: OpenAI / locale / Anthropic via proxy). L'infrastruttura MCP
esiste già (`mcp_server.py`): gli stessi tool (`cad_add_node`, `cad_connect`,
`cad_execute`, `cad_get_view`...) diventano le function/tool della chat.
- Capacità: **leggere** il grafo corrente, **creare/modificare** workflow e
  nodi, **diagnosticare** i casi limite del punto B e proporre/applicare il fix.
- Personalizzazione: salvare CodeBlock come nuovi nodi (Fase 5) e lasciare che
  l'AI ne generi di nuovi su richiesta.
- Tenere il provider astratto (base_url + api_key + model) per non legarsi a un
  fornitore.

### Note di stato (giugno 2026)
- ✅ Export STEP reale via `GET /api/graph/{name}/export/{fmt}` (step|stl|gltf).
- ✅ Tab **Panels** nella UI: mostra i valori dei nodi Panel dopo l'esecuzione.
- ✅ Nodo **Shell** ora svuota davvero (era un no-op placeholder).
- ✅ **Revolve**: default asse `Y` (in-plane); il profilo va spostato off-axis.
- ⚠️ Builder mode (`BuildPart`/`BuildSketch`) ancora non collegabile dalla UI
  (manca la gestione del campo `parent` in Litegraph).
- ⚠️ Map automatico su liste (stile Grasshopper) non ancora implementato.
- ⚠️ Esecuzione non sandboxata (CodeBlock/Expression = codice arbitrario):
  da chiudere prima di esporre la porta o l'MCP a terzi.

---

## Live Preview Roadmap (cambio di filosofia: stile Grasshopper)

> Da "Run → un risultato → Export" a "ogni nodo mostra il suo risultato in
> preview, live; il modello reale si materializza con Bake/Export".

Allineamenti alla realtà del codice:
- I "nodi che renderizzano" si determinano **automaticamente** dal `wire_type`
  dell'output (`geometry`/`sketch`/`curve`); data/math/plane/vector/panel non
  disegnano. Niente classificazione manuale.
- Preview on/off = flag booleano **per-nodo** nel graph JSON (come `parent`),
  non un parametro di catalogo. Default ON per i nodi-geometria.
- Real-time richiede un **worker persistente** (build123d resta importato): il
  subprocess a freddo (~1-3s/run) non basta per la fluidità per-keystroke.
- "Bake" = materializzare su file il nodo designato come Output; non esiste una
  scena persistente tipo Rhino. Preview = effimero; Bake/Export = reale.
- Costo onesto: meshare ogni nodo a ogni modifica è caro su grafi grandi →
  mitigazione (cache + incrementale + solo-preview-ON) va in LP6, non all'inizio.

### LP1+LP2 — Occhio per-nodo (stile Grasshopper) + viewer multi-mesh ✅
> Modello finale (dopo aver provato il nodo Preview dedicato): **occhio su ogni
> nodo geometrico**, 3 stati — `auto` (default, mostra solo i terminali = i
> risultati finali), `on` (forza, anche intermedio), `off` (nascondi, anche un
> finale). Niente nodo Preview dedicato. "Cosa si vede" (occhio) e "quando si
> ricalcola" (Live/Run) sono ortogonali; l'occhio applica subito.
- `graph.Node.preview` tri-stato: `None`=auto, `True`/`False`=esplicito.
- transpiler: `_previewed()` emette `__previews__[id] = var` per i nodi disegnati;
  auto = output non consumato da altri nodi (terminale).
- `mesh_extractor`: tassella ogni shape in `__previews__` →
  `view["previews"] = {id: {kind, bbox, volume, mesh}}`, ognuno in try/except.
- Three.js disegna N mesh da `view.previews`, colore stabile per nodo; fallback
  all'STL del risultato se nessun nodo è in preview.
- Possibili estensioni: selezione nodo ↔ evidenzia mesh; "mostra solo finali".

### LP3 — Auto-run debounced (sull'executor attuale)
- Debounce ~400ms su cambio widget/connessione/nodo → save+execute, con
  `AbortController` per annullare il run in volo. Toggle "Live" on/off + Run
  manuale.

### LP4 — Worker di esecuzione persistente (vero real-time)
- Processo Python long-lived con build123d pre-importato; WebSocket
  `/ws/graph/{id}` che streamma stato, preview incrementali ed errori (riusa
  `error_detail` + highlight). ⚠️ riemerge il tema sandbox.

### LP5 — Output esplicito + allineamento Bake/Export
- Sostituisce l'euristica `_pick_result` con un flag/nodo "Output" esplicito;
  Export ed endpoint STEP puntano a quello. "Bake" = esporta il nodo Output.

### LP6 — Performance: incrementale + cache
- Hash per nodo (tipo+param+hash monte); ri-tassella solo il sotto-grafo
  cambiato; cache mesh; mesh solo dei nodi con preview ON; LOD (grezzo live,
  fine all'export).

### Decisioni prese
- Live **opt-in** con toggle (default off finché non c'è LP4).
- Bake = **nodo/flag Output esplicito + Export** (riusa ciò che esiste).

### Backlog QoL (dopo la live preview)
- Bypass node (muta un nodo, pass-through).
- Group node (Litegraph groups + wiring builder-mode via `parent`).
- Input+slider unificato e fluido che si apre col valore già selezionato.
- Auto-fix raggio fillet (bisection su min/max); chat AI (C).