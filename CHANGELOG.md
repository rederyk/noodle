# Changelog

Tutte le modifiche degne di nota a cad-studio. Le versioni seguono `server.py`
(`APP_VERSION`).

## 0.1.0 — non rilasciata

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
