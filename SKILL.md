# CAD Studio — nanobot Skill

## Descrizione
Server API per modellazione CAD con due backend: OpenSCAD (CSG testuale) e CadQuery/PythonOCC (B-Rep solido). L'agente può creare progetti, scrivere codice, renderizzare e iterare.

## Servizio
- **Docker**: `cad-studio` container su porta **8090**
- **API base**: `http://192.168.178.21:8090`
- **WebUI**: `http://192.168.178.21:8090/ui`

## Endpoints

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/projects` | Lista progetti |
| POST | `/api/projects/{name}` | Crea progetto (body: `{code, backend}`) |
| GET | `/api/projects/{name}` | Leggi codice progetto |
| PUT | `/api/projects/{name}` | Aggiorna codice |
| DELETE | `/api/projects/{name}` | Elimina progetto |
| POST | `/api/projects/{name}/render` | Renderizza → STL |
| GET | `/api/projects/{name}/download` | Download STL |
| GET | `/api/projects/{name}/params` | Parametri (solo OpenSCAD) |
| PATCH | `/api/projects/{name}/params` | Modifica parametri |
| GET | `/api/backends` | Lista backend disponibili |
| GET | `/health` | Health check |

## Backend: OpenSCAD
- Codice `.scad` con annotazioni parametriche: `// Param: name = value`
- Render via CLI `openscad -o output.stl main.scad`
- Parametri estratti/iniettati automaticamente

### Esempio codice OpenSCAD
```openscad
// Param: width = 20
// Param: height = 30
// Param: depth = 10

difference() {
    cube([width, height, depth]);
    translate([5, 5, -1])
        cylinder(h=depth+2, r=3, $fn=32);
}
```

## Backend: CadQuery
- Codice Python con `import cadquery as cq`
- Per export STL usare la variabile `__output_stl__` pre-iniettata
- Primitive: box, sphere, cylinder, cone
- Booleane: union (+), cut (-), intersection (&)

### Esempio codice CadQuery
```python
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(20, 30, 10)
    .faces(">Z")
    .workplane()
    .hole(6)
)

cq.exporters.export(result, __output_stl__)
```

## Workflow agente
1. `POST /api/projects/{name}` — crea progetto con codice iniziale
2. `POST /api/projects/{name}/render` — renderizza
3. Se errori: leggi stderr, correggi codice, `PUT` + render di nuovo
4. `GET /api/projects/{name}/download` — per ottenere il file STL

## Gestione servizio
```bash
cd ~/projects/cad-studio
docker compose up -d --build   # avvia
docker compose logs -f          # log
docker compose down             # ferma
```
