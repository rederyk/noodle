# PLAN_TRACE_IMAGE — PNG → curve, editor raster tipo Illustrator (note)

Stato: **Fase 1 implementata + verificata** (2026-07-09). Discussione originale
2026-07-08. Nodo `TraceImage` (`catalog.py`), `_emit_vectorize` + helper
`_trace_curves` (`transpiler.py`), test transpiler in `tests/test_engine.py`.
Verifica end-to-end nel worker build123d: contorno 100×80 px con `scale=0.5` →
profilo 50×40 mm, riempito in face ed estruso, `success: True`, nessuna immagine
letta a runtime. Restano Fase 2 (sidecar vision) e Fase 3 (modal editor).

## Idea

Portare in noodle una pipeline **immagine → curve** stile Illustrator: carichi un
PNG/JPG, togli lo sfondo (rembg), rifinisci la maschera con **bacchetta magica**
(flood-fill) e **penna**, **quoti** l'immagine (dai una scala reale in mm) e ne
**estrai i contorni come curve** build123d, pronte per `MakeFace → Extrude`.

Il vincolo di design che tiene tutto insieme è lo **stesso del Select Edge**: il
grafo dev'essere **ri-eseguibile in modo deterministico**. Quindi rembg /
bacchetta / penna / tracciamento girano **solo in modalità edit** e il loro
risultato — i contorni tracciati + la scala — viene **congelato nei param del
nodo** (come i pick di Select Edge stanno in `params.selection`). Ad ogni rerun
il worker ricostruisce solo curve dai dati congelati: **nessuna immagine letta,
nessuna dipendenza pesante nel worker build123d**.

Linea di taglio: *ciò che è manuale/irripetibile si congela in un artefatto;
ciò che è un numero regolabile resta un parametro del nodo.*

## Il modello: `TraceImage` = "Select Edge per immagini"

Mappatura 1:1 sul pattern click-pick esistente (`feat/predicate-selectors`):

| Select Edge | TraceImage |
|---|---|
| `code_template = ""` (special-case) | `code_template = ""` (special-case) |
| `node.selectionData` (JS) | `node.traceData` (JS) |
| `params.selection` (serialize) | `params.trace` (serialize) |
| modal `#picker` (raycast 3D) | modal `#tracer` (canvas 2D) |
| `_emit_select` → `_select_subshapes(...)` | `_emit_vectorize` → `_trace_curves(...)` |
| output `WIRE_SELECTION` | output `WIRE_CURVE` |

Un **solo nodo auto-contenuto** (come `ImportSVG`): param asset `.png/.jpg` +
bottone `✎ Edit mask` + artefatto congelato + output `curve`.

## L'artefatto congelato (`node.traceData → params.trace`)

```jsonc
{
  "image": "assets/foo.png",        // sorgente, per ri-aprire l'edit
  "contours": [                     // loop chiusi, coordinate in PIXEL
    { "pts": [[x,y], …], "closed": true,  "hole": false },
    { "pts": [[x,y], …], "closed": true,  "hole": true  }   // fori interni
  ],
  "scale": 0.1234,                  // mm/pixel — dalla quotatura 2 punti
  "calib": { "p0":[x,y], "p1":[x,y], "mm": 50 },   // per ri-editare la quota
  "trace": { "threshold":128, "simplify":1.5, "min_area":20 }  // solo re-edit/display
}
```

Serializza a `params.trace` in `toGraphJSON` (nodes.html), ripristina in
`fromGraphJSON`, il bottone si rilabella `✎ 3 contorni` (come `updateSelectBtn`).

## Il transpiler (zero dipendenze a runtime)

`_emit_vectorize` (specchio di `_emit_select`) inietta `contours` e `scale` come
**letterali** e chiama l'helper di PREAMBLE `_trace_curves(contours, scale)`, che:

- scala i pixel per `scale` (mm/pixel) e **ribalta la Y** (la Y immagine cresce
  verso il basso, quella CAD verso l'alto) → pezzo dritto, non specchiato;
- costruisce una `Polyline(..., close=True)` per contorno (i punti sono già
  semplificati a monte); ritorna un `Wire` singolo o una `ShapeList` di `Wire`;
- i loop chiusi riempiono in `face` via il `_face` esistente, come ogni
  primitiva 2D. **Il worker build123d resta pulito.**

## Il servizio vision (sidecar opzionale — "rembg a parte")

Container separato `noodle-vision` (rembg + opencv), esposto via `VISION_URL`.
`server.py` fa da **proxy solo in edit mode**:
- `POST /api/vision/rembg` → maschera alpha
- `POST /api/vision/trace` (maschera + soglia/semplifica/area-min) → contorni px

Sidecar spento → l'edit mostra "vision non disponibile", ma **tutti i grafi
salvati si eseguono lo stesso** (usano i param congelati). Immagine base leggera.

## La modalità edit (modal 2D, specchio di `#picker`)

Overlay `#tracer` con canvas 2D. Lifecycle come `openSelectionPicker/closePicker`:

1. **Apri** → carica `assets/foo.png` nel canvas.
2. **rembg** → "togli sfondo" (sidecar) → maschera alpha.
3. **Bacchetta magica** → flood-fill su soglia colore (JS puro), add/remove regioni.
4. **Penna** → brush add/erase sulla maschera.
5. **Traccia** → contorni (sidecar), slider `soglia/semplifica/area-min`, anteprima live.
6. **Quota (2 punti)** → clic p0/p1 + mm reali → `scale = mm / dist_px`.
7. **Conferma** → congela in `node.traceData`, `runGraph()`.

## Piano a fasi

1. **Backend nodo + transpiler** — ✅ *fatto* (2026-07-09). `TraceImage` NodeDef
   (`catalog.py`), dispatch + `_emit_vectorize` + helper `_trace_curves`
   (`transpiler.py`), test transpiler in `tests/test_engine.py`.
2. **Sidecar vision** — *non ancora*. Container `noodle-vision` (rembg+opencv) +
   2 endpoint + proxy in `server.py`. NB: il minimo per usare la Fase 3 è già
   fatto — `.png/.jpg/.jpeg` in `_IMPORT_NODE_BY_EXT` + `_LIB_MEDIA` (server.py)
   e `accept=` (library.html). Resta solo il servizio rembg vero e proprio; il
   bottone ✂ Remove bg lo chiama e degrada con grazia se assente.
3. **Modal editor** in `nodes.html` — ✅ *fatto + verificato* (2026-07-09). Overlay
   `#tracer` (canvas 2D), tool bacchetta (flood-fill), penna/gomma (brush),
   quota a 2 punti (mm/px), invert, ✂ Remove bg (sidecar, opzionale). Tracciamento
   contorni **in JS puro** (marching-squares → stitch loop → RDP → classificazione
   outer/foro per **parità di contenimento**, non per winding). Commit congela in
   `node.traceData` → `params.trace`. Il tutto è **client-side**, funziona senza
   sidecar (solo rembg lo richiede). Verificato headless (playwright+chromium): un
   PNG rettangolo-con-foro → 2 contorni (1 outer, 1 hole), 0 errori JS, il grafo
   esegue in build123d (bbox 120×80×5). Nota Fase 1: i loop `hole` escono come
   wire chiusi separati (come i path SVG) — la sottrazione face-con-fori resta un
   affinamento futuro (Make Face a valle).

## Nodo companion: `RefImage` (immagine di riferimento) — ✅ fatto (2026-07-09)

Nodo **solo-editor** che mette un'immagine su un piano nel viewport 3D come
riferimento di modellazione (blueprint underlay da ricalcare/allineare). NON è
geometria build123d: il transpiler lo salta come `Note`, e `nodes.html` disegna
un quad texturizzato nella scena condivisa del `CadViewer` (helper
`syncRefImages()` — riconcilia i mesh per id nodo, cache texture per URL).
Params: `path` (asset png/jpg), `plane` (XY/XZ/YZ), `width` (mm, altezza dal
rapporto d'aspetto), centro `x/y/z`, `opacity`. Aggiornamento live su cambio
param senza ri-eseguire il grafo; pulizia del quad alla rimozione del nodo.
Verificato headless (playwright): quad in scena al load con aspect corretto,
reorientamento piano live, cleanup su delete, 0 errori JS. `RefImage` e
`TraceImage` condividono gli asset png/jpg della library.

**Allineamento 1:1 (2026-07-09).** Con `align_to_trace` (default on), se un
`TraceImage` usa lo stesso file il quad si aggancia automaticamente a scala ed
estensione della geometria tracciata (override di plane/width/centro manuali).
Reso possibile da un'origine STABILE in `_trace_curves`: il flip Y ora usa
l'ALTEZZA IMMAGINE (`imgH`, salvata nell'artefatto con `imgW`) invece del max-Y
dei contorni — angolo immagine → mm (0,0). Il RefImage copre allora il rettangolo
`[0..imgW·s]×[0..imgH·s]` su XY, centrato, e i due combaciano. NB: la callback di
load texture ri-chiama `syncRefImages()` (non forza la dimensione manuale), se no
sovrascriverebbe l'allineamento in modo asincrono. Verificato: quad 120×80 @
(60,40) su un tracciato a scala 0.5.

**Quotatura con Shift (2026-07-09).** In modalità 📏 Scale, tenendo Shift la linea
si blocca a 0°/90° (snap dell'asse col delta minore al primo punto), con anteprima
tratteggiata live che segue il cursore. Verificato: p1 con Shift eredita la Y di p0.

## Da rifinire (dopo Fase 1)

- Fori: i loop `hole` come inner boundary di `make_face` (oggi escono come wire
  chiusi separati, come i path SVG — nesting gestito a valle da MakeFace).
- Spline vs Polyline come opzione di tracciamento (angoli vs curve morbide).
- Semplificazione lato worker mai necessaria: i punti arrivano già ridotti.
