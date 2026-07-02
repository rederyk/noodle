# Changelog

Tutte le modifiche degne di nota a noodle. Le versioni seguono `server.py`
(`APP_VERSION`).

## 0.1.0 — non rilasciata

### Security

- **Path traversal chiuso sui nomi progetto.** Un nome progetto ora deve essere
  un singolo segmento di percorso (`[A-Za-z0-9][A-Za-z0-9._ -]{0,63}`, vedi
  `cad_nodes/store.py::validate_graph_id`); nomi con `/`, `..` ecc. ricevono
  400. La validazione copre tutte le superfici (REST, MCP, copilot) perché vive
  in `GraphStore.dir()` + `server.py::project_dir()`.
- **Il container gira come utente non-root** (`noodle`, uid 1000). I progetti
  scritti dal container sono ora editabili dall'host senza sudo. Migrazione da
  un'immagine precedente: `sudo chown -R 1000:1000 projects feedback`.

### Added

- **Packaging e CI.** `pyproject.toml` (installabile, extra `dev` con
  pytest/ruff, config ruff) e workflow GitHub Actions (`.github/workflows/ci.yml`):
  lint ruff, test su Python 3.10/3.12, build dell'immagine Docker.

### Changed

- **Nodo Shell — ora ispessisce le superfici aperte invece di andare in errore.**
  Prima il nodo chiamava sempre `offset(part, openings=part.faces()...)` di
  build123d, che accetta solo solidi: dargli un Loft non-solido (una `Shell`, es. la
  parete di un vaso) sollevava
  `TypeError: Unsupported type <class 'build123d.topology.two_d.Shell'>`.
  Il nodo ora ramifica sul tipo di input:
  - **Solid / Compound di solidi** → svuotato lasciando aperta la faccia superiore
    (+Z) — comportamento invariato;
  - **Shell / Face aperta** (es. un Loft con `solid=false`) → ispessita in una parete
    solida con `Solid.thicken()`.

  **Migrazione:** un nodo Shell già presente in un grafo salvato adotta il nuovo
  comportamento **automaticamente** alla prossima esecuzione (il codice è rigenerato
  dal catalogo al transpile). Se l'editor mostra ancora il comportamento vecchio,
  fai hard-refresh della pagina (Ctrl+Shift+R) oppure **cancella e ri-aggiungi** il
  nodo Shell.
