# CAD Studio — nanobot Skill

## Descrizione
CAD parametrico **a nodi**. Si compone un grafo di nodi; il backend lo transpila
in **build123d** (Python), lo esegue in un worker isolato e restituisce STL +
mesh per il viewport. Unico motore geometrico: build123d (OpenCASCADE). Nessun
OpenSCAD/CadQuery.

## Servizio
- **Docker**: container `cad-studio` su porta **8090**
- **API base**: `http://localhost:8090`
- **Editor a nodi**: `http://localhost:8090/nodes`
- **Vista codice** (build123d generato dal grafo, sola lettura): `http://localhost:8090/ui`

## Endpoints principali

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/nodes` | Catalogo nodi (tipi, socket, parametri) |
| GET | `/api/projects` | Lista progetti (i grafi hanno `backend: "nodegraph"`) |
| POST | `/api/graph/{name}` | Crea/sovrascrive un grafo (body: `{name, nodes, connections}`) |
| GET | `/api/graph/{name}` | Leggi il grafo |
| GET | `/api/graph/{name}/code` | Codice build123d transpilato dal grafo |
| POST | `/api/graph/{name}/execute` | Esegui il grafo → view + STL + errori per-nodo |
| POST | `/api/projects/{name}/render` | Esegui e produci `output.stl` |
| GET | `/api/projects/{name}/download` | Download STL |
| GET | `/api/graph/{name}/export/{fmt}` | Export (es. `step`) |
| DELETE | `/api/projects/{name}` | Elimina progetto |
| POST | `/api/copilot/chat` | Copilot NL → modifica il grafo |
| GET | `/api/system/health` · `/api/system/logs` · POST `/api/system/restart` | Salute / log backend / riavvio |
| GET | `/health` | Health check |

## Come un agente costruisce un modello
Un grafo è `{nodes, connections}`. Ogni nodo ha `id`, `type` (dal catalogo
`/api/nodes`), `params` e `position`; ogni connessione collega
`from_node/from_socket` → `to_node/to_socket` (wire tipizzati).

Vie consigliate, in ordine:
1. **MCP server** (`mcp_server.py` → `cad_nodes.api`): `add_node`, `connect`,
   `set_param`, `delete_node`, `execute`, `transpile` — la sorgente di verità,
   condivisa anche dal copilot.
2. **Copilot**: `POST /api/copilot/chat` con linguaggio naturale.
3. **HTTP diretto**: `POST /api/graph/{name}` con il JSON del grafo, poi
   `POST /api/graph/{name}/execute`; in caso di errori leggi `node_errors`
   (mappa `node_id → messaggio`) e correggi.

## Gestione servizio
```bash
cd ~/projects/cad-studio
docker compose up -d --build   # avvia
docker compose logs -f          # log
docker restart cad-studio       # dopo modifiche a server.py
docker compose down             # ferma
```
