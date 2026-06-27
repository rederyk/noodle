# Piano — Parametri editabili dal codice (param ↔ code, non distruttivo)

> Obiettivo: nella vista codice (`/ui`) **individuare, evidenziare e rendere
> editabili** i parametri dei nodi direttamente sul codice build123d generato, in
> modo **non distruttivo**. Estensione naturale: il nodo **CodeBlock** può
> dichiarare propri parametri (`#@param`) che diventano parametri di prima classe
> del grafo (slider nel nodo, span editabili nel codice, input cablabili). Così il
> CodeBlock diventa un **nodo parametrico definito dall'utente** — il ponte tra
> codice libero e grafo parametrico.

## 0. Principio cardine: non distruttivo

Il codice mostrato in `/ui` è **generato** dal transpiler (`transpile(graph)`),
non scritto a mano. Quindi:

1. **Non si fa parsing del testo modificato a mano** per ricostruire il grafo. Solo
   **modifiche strutturate di valori** tornano indietro.
2. Editare un valore = aggiornare un parametro in `graph.json` → **ri-transpilare**
   → ri-renderizzare. Il codice si **rigenera**; nient'altro viene toccato.
3. Per il CodeBlock: il **testo del codice autorato non viene mai riscritto** dalla
   vista codice. I default restano nel testo; gli override vivono in un namespace
   separato e si sovrappongono al transpile.
4. Un parametro **pilotato da un wire** è mostrato in sola lettura con la sua
   provenienza (niente conflitti silenziosi tra literal e wire).

Tutto ciò si innesta sulle API esistenti: `cad_nodes/api.py::set_param`
(`graph.node(id).params.update(...)` + save) è già la via strutturata e
round-trippa con editor a nodi, MCP e copilot.

---

## 1. Layer 1 — Source map dal transpiler (param ↔ span di codice)

Il transpiler è l'unica fonte di verità su *dove* finisce ogni parametro. Oggi:

- `_param_values(node, ndef)` (transpiler.py:679) rende ogni parametro con
  `format_param(p, value)` (transpiler.py:591) → un token-literal.
- `_substitute(template, values)` (transpiler.py:608) sostituisce `{name}` /
  `{name!r}` nel `code_template`.
- ogni riga emessa porta già `# @node:<id> (Tipo)` via `_annot(node)`
  (transpiler.py:27) → granularità a livello di **nodo** già disponibile.

Serve granularità a livello di **parametro**. Meccanismo robusto a sentinelle:

1. **Transpile strumentato** (nuova modalità `Transpiler.run(emit_map=True)`):
   quando si sostituisce un placeholder che è un *parametro* (non un socket/var),
   avvolgi il valore in sentinelle invisibili, es. `\x00{spanid}\x01{value}\x02`,
   registrando `spanid → (node_id, param_name)`.
   - Implementazione: `_param_values` produce valori già "taggati" oppure
     `_substitute` riceve un set di chiavi-parametro e nel suo `repl()` avvolge solo
     quelle. (I socket/var restano nudi: non sono editabili come literal.)
2. **Post-processing dopo `run()`**: scandisci il testo finale, calcola per ogni
   span `(line, col_start, col_end)`, **rimuovi le sentinelle**, e produci la map.
   - Misurando sul **testo finale** la mappa è immune a indentazione, `@node`,
     blocchi `with` annidati dei gruppi e wrapping `_guard`/`_fanout`.
   - Le sentinelle sono caratteri che non compaiono mai nel codice (NUL ecc.).

**Entry della source map** (una per parametro editabile):
```jsonc
{
  "id": "s12",
  "node_id": "box_3", "node_type": "Box",
  "param": "width",
  "line": 42, "col0": 31, "col1": 33,      // posizione nel testo finale
  "value": 20, "kind": "float",            // dal catalog Param
  "meta": { "min": 0, "max": 200, "step": 0.1, "options": null },
  "wired": false                            // true se un input pilota il param
}
```

`meta` e `kind` vengono dal `catalog.Param` del nodo; `wired` si ricava da
`graph.inputs_of(node_id)` (se esiste un socket omonimo connesso → params-as-input,
cfr. transpiler.py:769-781 e CLAUDE.md §5b).

**Endpoint**: estendere `GET /api/graph/{name}/code` con `?map=1` →
`{ "code": "...", "params": [ ...source map... ] }` (retro-compatibile: senza
`map` resta solo `code`).

---

## 2. Layer 2 — Vista codice: evidenziazione + edit inline (`webui/index.html`)

Ace è in sola lettura (scelta della vista codice). Aggiungiamo:

- **Highlight**: per ogni entry usa un *Ace marker* (sottolineatura/sfondo tenue)
  = "parametro editabile". Stile distinto per `wired` (provenienza, non editabile).
- **Hover**: tooltip con `node_type · node_id · param` → lega codice ↔ grafo.
- **Click su uno span** → micro-editor inline per `kind`:
  - `float`/`int` → input numerico clampato a `min/max/step` (+ drag-scrub opz.);
  - `bool` → toggle;
  - `select` → dropdown con le opzioni (mappa display ↔ token via `code_map`);
  - `str` → campo testo.
- **Commit** → `PATCH /api/graph/{name}/param  {node_id, param, value}` (vedi §4) →
  refetch `code?map=1` → re-render STL. Debounce per lo scrubbing live.
- **Span wired**: sola lettura, tooltip "pilotato da <nodo a monte> — modifica lì
  o scollega il wire".
- **Cross-view linking** (bonus): click sullo span / sul tag `@node` → seleziona il
  nodo nell'editor `/nodes` via `BroadcastChannel`/`localStorage`.

Essendo gli edit strutturati (`set_param`) e il codice rigenerato, è **non
distruttivo** e coerente con editor a nodi / copilot / MCP.

---

## 3. Layer 3 — CodeBlock parametrico (la parte "potente")

Oggi: `CodeBlock` (catalog.py:963) ha un solo param `code` (testo `raw=True`) +
6 input generici `in_0..in_5`; il transpile è speciale (`_emit_codeblock`,
transpiler.py:736: avvolge il codice in una funzione e passa gli input).

Lo rendiamo capace di **dichiarare parametri nominati** nel proprio codice, che il
sistema fa emergere come: (1) span editabili nella vista codice, (2) widget dinamici
sul nodo, (3) input socket dinamici cablabili (params-as-inputs → fan-out).

### 3.1 Sintassi di dichiarazione (annotazione, parsing statico e sicuro)
```python
radius = 5.0      #@param min=1 max=20 step=0.5
count  = 6        #@param int min=1 max=12
mode   = "fillet" #@param select=fillet,chamfer
label  = "hi"     #@param str
result = Box(radius*2, radius*2, count)   # usa i param dichiarati
```
Regole:
- solo assegnazioni semplici top-level `nome = <literal>  #@param ...`;
- il literal è il **default**; tipo dedotto dal literal o forzato
  dall'annotazione; `min/max/step/options` dall'annotazione;
- parser = scansione a righe + **`ast.literal_eval`** sul solo RHS (mai `exec`) +
  regex sull'annotazione. **Sicuro e non distruttivo.**

### 3.2 Modello a override (non distruttivo)
- Il **testo autorato conserva i default** — mai riscritto.
- Gli override stanno in un namespace separato del nodo, es.
  `node.params["_cb"] = { "radius": 7.5, "count": 8 }` (solo i modificati;
  assente = usa il default in-codice).
- In `_emit_codeblock`, **dopo** le righe del codice utente, si iniettano le
  riassegnazioni per i soli param overriddati (es. `radius = 7.5`) all'interno della
  funzione generata: gli override vincono **senza toccare il testo**. Il default
  in-codice resta come documentazione/fallback.
- La vista codice mostra il valore **effettivo** (override o default) come span
  editabile; l'edit imposta l'override (target `_cb.<nome>`) via lo stesso PATCH.

### 3.3 Esposizione come widget/input nell'editor a nodi (`webui/nodes.html`)
- `GET /api/nodes` è statico: i param dichiarati sono **per-istanza**, quindi
  servono per-nodo. Nuovo endpoint `POST /api/graph/{name}/codeblock/{node_id}/scan`
  (o includere lo schema dichiarato nella risposta di `/code` e `/execute`) →
  ritorna lo schema dei param dichiarati di quel CodeBlock.
- L'editor a nodi renderizza widget dinamici da quello schema (come fa coi param di
  catalogo) e **opzionalmente** socket di input dinamici col nome del param,
  riusando la macchina esistente "params-as-inputs" + multi-connect/fan-out
  (CLAUDE.md §5b, `onConnectionsChange`/`fromGraphJSON`).
- Cablare l'input di un param dichiarato sovrascrive il literal (e disabilita
  l'editor inline su quello span, marcandolo `wired`) — identico ai params-as-inputs
  dei nodi built-in.

### 3.4 Perché rende potente il CodeBlock
Scrivi build123d arbitrario, esponi qualche `#@param`, e ottieni subito: slider sul
nodo, valori editabili nella vista codice, e input cablabili che partecipano al
fan-out (`Range → CodeBlock.count` scatena N copie) — senza uscire dal modello
parametrico. Il CodeBlock diventa un nodo parametrico definito dall'utente.

---

## 4. API e validazione

- **`PATCH /api/graph/{name}/param`** `{node_id, param, value}` → wrapper su
  `api.set_param` con **validazione/clamp** dalla `catalog.Param` (min/max/step/
  type/options). Per i param dichiarati del CodeBlock il target è `_cb.<nome>` e i
  vincoli vengono dall'annotazione `#@param`.
- **`GET /api/graph/{name}/code?map=1`** → `{code, params[]}` (+ `codeblocks[]`
  con gli schemi dichiarati, opzionale).
- **`POST /api/graph/{name}/codeblock/{node_id}/scan`** → schema param dichiarati.
- Riuso del worker caldo (`executor.py`) per il re-render; per il refresh della sola
  vista codice basta ri-transpilare (economico), eseguire solo al commit.

---

## 5. Garanzie non-distruttive (esplicite)

1. Codice generato in sola lettura **tranne** gli span-parametro noti; il testo
   libero non viene mai riscritto.
2. Param built-in: edit → `set_param` (esistente, strutturato) → ri-transpile.
3. Param dichiarati del CodeBlock: edit scrive **override** in namespace separato;
   il testo del CodeBlock non è toccato; i default restano nel sorgente.
4. La regola copilot sui CodeBlock pre-esistenti resta valida: la vista codice
   modifica **valori**, non il *corpo* del codice → consentito; l'edit del corpo
   resta nell'editor a nodi (`set_code`/`copy_node`).
5. I param `wired` sono in sola lettura con provenienza → niente conflitti
   literal↔wire.

---

## 6. Fasi

- **Fase 0 — Source map (backend).** ✅ FATTO. `transpile_with_map()` /
  `run(emit_map=True)` (sentinelle + post-processing su testo finale), `?map=1` su
  `/code`. Test: offset per Box, param `wired` → nessuno span, CodeBlock.
- **Fase 1 — Highlight (read-only).** ✅ FATTO. Marker Ace per tipo + hover/tooltip
  con provenienza nella vista codice (stile terminale hacker).
- **Fase 2 — Edit inline param built-in.** ✅ FATTO. `PATCH .../param` con
  coerce+clamp, editor inline per kind (numero+slider, bool, select, str),
  re-render debounced, span `wired` read-only. Round-trip con l'editor a nodi.
- **Fase 3 — Parser `#@param` + override CodeBlock.** ✅ FATTO. Parser statico
  (`ast.literal_eval`), namespace override `_cb`, iniezione effective-value nel
  transpile, span editabili `_cb.<nome>`, endpoint `scan`.
- **Fase 4 — Widget/input dinamici del CodeBlock nell'editor a nodi.** ✅ FATTO.
  `parseCbParams` (mirror JS) → widget dinamici (slider/toggle/combo/text) +
  socket di input dinamici per ogni `#@param`; wired → widget disabilitato +
  fan-out (`Range → CodeBlock.teeth` = N copie); modale ✎ Edit code; backend
  `_emit_codeblock` passa `<name>__arg` e avvolge in `_fanout`; `validate()`
  accetta i socket dichiarati per-istanza.
- **Fase 5 — Cross-view linking + rifiniture.** ✅ FATTO. `BroadcastChannel
  'cadstudio:link'`: click su uno span → `locate` → `/nodes` seleziona + flash del
  nodo (mappa `nodeByGraphId` allineata agli id su disco al load/save); selezione
  in `/nodes` → `reveal` → `/ui` scrolla e lampeggia la riga. Scrub-drag numerico
  sugli span (drag = scrub live, click = popup), Tab/Shift+Tab tra gli span,
  Ctrl+Z (undo degli edit via PATCH), refetch della map su window focus.

---

## 7. Casi limite e rischi

- **Collisioni di valore** (due param che formattano allo stesso literal, es. due
  `20`): risolte dagli **offset a sentinella**, non da ricerca testuale.
- **Gruppi/annidati e lambda fannate**: gli span sono misurati sul testo finale →
  indentazione/wrapping ok; un valore fannato compare dentro `_fanout({...})` ma è
  comunque un'unica occorrenza mappabile. I param diventati nomi-socket della lambda
  sono `wired` → non literal, non editabili.
- **Param `raw=True`/espressioni** (es. `CodeBlock.code`): l'intera espressione è il
  valore; non si sotto-parsa (eccetto i `#@param` del CodeBlock col parser dedicato).
- **`select`**: mappa opzione visibile ↔ token codice via `code_map`.
- **Costo re-render per edit**: debounce + worker caldo; refresh vista = solo
  ri-transpile, esecuzione solo al commit.
- **Map stantia** dopo edit esterni (copilot/MCP/altro tab): la vista codice rifà
  fetch di `code?map=1` su focus/poll con un controllo di versione del grafo (si
  appoggia al polling backend già presente).
- **Sicurezza**: il parser dei `#@param` non esegue **mai** codice utente — solo
  `ast.literal_eval` sul literal RHS e regex sull'annotazione.

---

## 8. File toccati (anteprima)

| File | Modifica |
|---|---|
| `cad_nodes/transpiler.py` | source map a sentinelle; override-injection in `_emit_codeblock`; helper scan `#@param` |
| `cad_nodes/api.py` | helper validazione/clamp; ops override + scan CodeBlock (`set_param` resta ok) |
| `server.py` | `/code?map=1`, `PATCH /api/graph/{name}/param`, endpoint scan CodeBlock |
| `webui/index.html` | marker Ace, editor inline, edit→PATCH→refresh, linking |
| `webui/nodes.html` | widget/socket dinamici dei param dichiarati del CodeBlock (Fase 4) |
| `tests/` | source-map, parser `#@param`, precedenza override |

---

## 9. Esempio end-to-end (CodeBlock)

Codice autorato nel CodeBlock:
```python
teeth = 12     #@param int min=6 max=40
mod   = 2.0    #@param min=0.5 max=5 step=0.1
result = make_gear(teeth, mod)     # funzione build123d dell'utente
```
- Editor a nodi: compaiono slider `teeth` e `mod` + (opz.) input `teeth`/`mod`.
- Vista codice `/ui`: `12` e `2.0` evidenziati ed editabili; cambi `12`→`20` →
  `PATCH .../param {_cb.teeth: 20}` → override salvato, testo intatto → re-render.
- Cabli `Range(8..16) → CodeBlock.teeth`: lo span `teeth` diventa `wired`
  (read-only) e il nodo **fa fan-out** producendo una lista di ingranaggi.
