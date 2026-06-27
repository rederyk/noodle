# CAD Studio — Design del guscio applicativo

> Deliverable di **product/UX design** per il guscio attorno al canvas a nodi e al
> viewport 3D. Non ridisegna l'editor né il viewport; organizza top bar, sessione,
> controlli di sistema e pannelli. Implementabile in HTML/CSS semplice (single file),
> tema dark "CAD", desktop-first, single-user/locale.

Indice:
1. Principi & decisioni (le 5 domande aperte)
2. Information Architecture — mappa dei controlli
3. Wireframe top bar (normale · edit gizmo · restart)
4. Wireframe pannelli & dialog (stato documento · console log · restart · ripristino bozza · command palette)
5. Design system (tassonomia bottoni, stati, feedback)
6. Token visivi & griglia
7. Note di implementazione / endpoint backend

---

## 1. Principi & decisioni

**Principi guida**
- **Gerarchia prima della densità.** L'occhio deve trovare in <300ms: *Run*, *stato
  documento*, *salute backend*. Tutto il resto è secondario.
- **Contestualità over clutter.** I controlli rari o modali non vivono in barra:
  vanno in un menu Sistema, nella command palette, o in una toolbar contestuale che
  appare solo quando serve.
- **Grammatica visiva coerente.** Ogni controllo appartiene a *una* delle 5 classi
  (azione primaria · secondaria · toggle · sistema/distruttiva · contestuale) e ne
  eredita forma, peso e stati. Niente bottoni "speciali".
- **Documento ≠ Sistema.** Le azioni sul documento (salva, undo) e le azioni di
  sistema (restart backend) hanno trattamento visivo distinto: il sistema è più
  "freddo"/tecnico e vive a destra, lontano dalle azioni frequenti.

### Decisioni sulle 5 domande aperte

**D1 — Autosave: locale e/o server? → ENTRAMBI, con ruoli distinti.**
- **Bozza locale** (`localStorage`/IndexedDB), debounce **~600 ms** dopo ogni
  modifica del grafo, *indipendente* dalla modalità Live. È la **rete di sicurezza**:
  sopravvive a refresh, crash del tab e backend offline. Chiave per progetto, con
  `{graphHash, savedToDiskHash, ts}`.
- **Autosave server** (`PUT /api/graph/{name}`), debounce **~3 s** di inattività +
  flush su `blur`/`visibilitychange`. È la **fonte di verità** su disco.
- **Conflitto al reload:** confronto `bozzaLocale.savedToDiskHash` con l'hash del
  grafo su disco.
  - Identici e bozza == disco → apri silenziosamente.
  - Bozza più recente del disco, disco invariato → **applica la bozza** e marca
    "modifiche non salvate" (nessun prompt: è semplice recupero).
  - Disco cambiato *sotto* la bozza (es. modificato via MCP/copilot/altro tab) →
    **prompt "Ripristina bozza / Scarta e apri da disco"** con diff sintetico
    (n. nodi/connessioni aggiunti/rimossi). Vedi §4.4.
- Perché entrambi: il server da solo non protegge da backend offline; il locale da
  solo non condivide con MCP/copilot. I due insieme coprono entrambi i fallimenti.

**D2 — Controlli gizmo contestuali: overlay viewport o toolbar trasformabile? →
TOOLBAR CONTESTUALE FLOTTANTE sul viewport.**
- Quando si entra in *Edit on canvas* compare una **barra flottante in alto-centro
  del viewport** (non nella top bar globale) con `Drag · Snap · World/Local · ✓ Done`.
- Trattamento visivo da "modalità": bordo/accento `--accent-edit`, così è chiaro che
  sei in uno stato modale. Esce con *Done*/Esc.
- Perché qui e non in top bar: i controlli appartengono *spazialmente* al gizmo nel
  viewport; tenerli sempre in barra crea rumore quando il 90% del tempo non editi.
  La top bar globale non deve mai "cambiare forma" — la modalità vive dove agisce.

**D3 — Console log: unica filtrabile o due pannelli? → UNA console unificata con
filtro sorgente.**
- Un solo tab **Console** con filtri `Sorgente: [Tutto ▾] (Client · Backend)` +
  `Livello: [info|warn|error]` + pausa/clear/copia/auto-scroll.
- Il vecchio "Log" client e i nuovi log backend (uvicorn + worker) confluiscono qui,
  taggati per sorgente con colore di bordo a sinistra della riga.
- **Correlazione errori-nodo:** una riga di errore worker con `node_id` è cliccabile
  → seleziona/centra il nodo colpevole sul canvas (già supportato lato esecuzione via
  `__errors__`/`node_errors`). Vedi §4.2.
- Perché unire: meno tab, una sola "verità temporale" (client e backend interlacciati
  cronologicamente aiutano il debug), e i filtri rendono banale isolare una sorgente.

**D4 — Restart backend: bottone diretto o menu? → DENTRO un menu "Sistema" (⚙),
con conferma; ma l'INDICATORE DI SALUTE è sempre visibile in barra.**
- Restart è raro + sensibile → vive nel popover *Sistema* (⚙ a destra), con dialog di
  conferma (§4.3). Non merita spazio permanente in barra.
- La **salute backend** (pallino di stato) invece è *sempre* visibile accanto al Run,
  perché è informazione ad alta frequenza di consultazione.
- Durante il restart: dialog mostra `Riavvio → in attesa di health → pronto`, *Run* è
  disabilitato, la top bar entra in stato "backend restarting" (§3.3).

**D5 — Command palette (Ctrl+K): quanto spingere? → CANALE PRIMARIO per le azioni
rare; sostituisce la riga di hint.**
- `Ctrl+K` apre una palette che indicizza **tutte** le azioni (incl. rare: Export
  STEP, Restart backend, Apri console, Rinomina/Duplica progetto, toggle Grid…), ognuna
  con la sua scorciatoia mostrata a destra.
- Questo permette di **togliere dalla barra** tutto ciò che non è alta-frequenza, e di
  **eliminare la riga di hint testuale**: la discoverability passa da palette + tooltip
  + pannello "?" delle scorciatoie.
- La barra resta minimale; la palette è la "long tail". Doppio-click su canvas resta la
  ricerca *nodi* (dominio diverso, non confonderli).

---

## 2. Information Architecture — mappa dei controlli

Cinque zone funzionali. In top bar restano solo Progetto, Esecuzione e Sistema
(compresso). Vista → overlay viewport. Contestuali → toolbar modale. Long-tail →
command palette.

```
┌─ TOP BAR (sempre) ─────────────────────────────────────────────────────────────┐
│ ZONA PROGETTO/SESSIONE        ZONA ESECUZIONE            ZONA SISTEMA/UTILITY     │
│ • app mark "CAD Studio"       • ▶ Run  (primaria)        • ⌘K palette             │
│ • progetto ▾ (apri/cerca)     • ◌ Live (toggle)          • ⚙ Sistema ▾           │
│ • ● stato documento           • status pill esecuzione      └ Restart backend     │
│ • ↶ ↷ undo/redo               • ◉ salute backend (dot)      └ Console log         │
│                                                            └ Impostazioni        │
│                                                          • ⬇ Export ▾ (STL/STEP)  │
│                                                          • ⧉ Code UI              │
│                                                          • ? scorciatoie          │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ OVERLAY VIEWPORT (sempre, ancorato al viewport) ─┐   ┌─ TOOLBAR CONTESTUALE ──────┐
│ ZONA VISTA (angolo del viewport)                  │   │ (solo in Edit on canvas)   │
│ • ⛶ Fit   • ▦ Grid   • cubo assi (nav)           │   │ ✥ Drag · ⌗ Snap ·          │
│ • overlay statistiche dimensioni                  │   │ 🌐 World/Local · ✓ Done     │
│ • legenda colori-per-nodo                         │   └────────────────────────────┘
└───────────────────────────────────────────────────┘

┌─ COMMAND PALETTE (Ctrl+K) — long tail di TUTTE le azioni ──────────────────────┐
│ rinomina/duplica/elimina progetto · export STEP · restart backend · apri console │
│ · toggle grid/live · fit · group/bypass · ogni azione con la sua shortcut         │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ PANNELLO DATI (tab, in basso) ────────────────────────────────────────────────┐
│ Code · View/Data · Panels · ⛌ Console[badge errori] · 🤖 AI                       │
└──────────────────────────────────────────────────────────────────────────────────┘

PER-NODO (sul canvas, invariati): 👁 preview · ＋ multi-input · Select edges/faces ·
Edit on canvas · Edit text · menu contestuale (bypass/wireframe/colore)
BARRA SELEZIONE FLOTTANTE (2+ nodi, invariata): bypass · group · clone · delete
```

**Regole di collocazione**
- *Alta frequenza, globale* → top bar (Run, Live, stato doc, salute backend, undo/redo).
- *Alta frequenza, spaziale al viewport* → overlay viewport (Fit, Grid, nav).
- *Modale/temporanea* → toolbar contestuale che appare/scompare (gizmo).
- *Bassa frequenza* → menu Sistema ⚙ e/o command palette (restart, console, export, settings).
- *Per-oggetto* → sul nodo/selezione (invariato).

---

## 3. Wireframe — Top bar

### 3.1 Stato normale (documento salvato)

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ ◆ CAD Studio │ [ bracket-clip ▾ ] ● Saved   ↶ ↷ ┊ ▶ Run  ◌ Live  ⦿ Ready ┊ ◉  ⌘K ⚙ ⬇▾ ⧉ ?│
│  app mark      progetto       doc-status undo    PRIMARY toggle  run-pill  │  sistema/utility │
└───────────────────────────────────────────────────────────────────────────────────────────┘
 ◉ = salute backend (verde = online).   ┊ = separatore di gruppo.
```

Dettaglio zone (mockup ad alta fedeltà del raggruppamento):

```
PROGETTO ───────────────┐  ESECUZIONE ──────────────────┐  SISTEMA ───────────────┐
┌─────────────────────┐ │  ┌──────┐ ┌──────┐ ┌────────┐ │  ┌──┐┌──┐┌──┐┌────┐┌──┐ │
│◆ CAD Studio          │ │  │▶ Run │ │◌ Live│ │⦿ Ready │ │  │◉ ││⌘K││⚙ ││⬇ ▾││? │ │
│[bracket-clip ▾] ●Saved│ │  └──────┘ └──────┘ └────────┘ │  └──┘└──┘└──┘└────┘└──┘ │
│  ↶  ↷                 │ │   filled   toggle   run-pill   │  health palette sys exp │
└─────────────────────┘ │                                │                          │
```

- **● Saved**: punto + label, classe *doc-status* (vedi §4.1 per gli stati).
- **▶ Run**: unica azione *primaria* (filled accent). `Ctrl+Enter`.
- **◌ Live**: *toggle*; acceso = `● Live` con accento.
- **⦿ Ready**: *run status pill* (Ready/running…/done ✓/error).
- **◉**: *salute backend* (dot sempre presente). Tooltip: "Backend online · 12ms".
- **⚙ Sistema**: popover (Restart backend · Console · Impostazioni).
- **⬇▾ Export**: menu (STL · STEP). **⧉ Code UI**: link. **? **: pannello scorciatoie.
- **⌘K**: apre command palette.

### 3.2 Stato "Edit gizmo" contestuale

La top bar globale **non cambia**. Compare la toolbar contestuale sul viewport:

```
TOP BAR: ...invariata (Run resta disponibile)...

         ┌─────────────────── viewport ───────────────────────────────┐
         │            ┌─────────────────────────────────────────┐      │
         │            │ ✏ Editing: "Move.offset"                 │      │  ← barra flottante
         │            │ ✥ Drag  ⌗ Snap  🌐 World ▾   │   ✓ Done  │      │     accento --accent-edit
         │            └─────────────────────────────────────────┘      │
         │                                                              │
         │                     [ gizmo translate/rotate/scale ]         │
         │                                                              │
         └──────────────────────────────────────────────────────────────┘
```

- Bordo/accento distinto (`--accent-edit`) = "sei in modalità".
- `✥ Drag`, `⌗ Snap` = toggle; `🌐 World/Local` = toggle a due stati con label.
- `✓ Done` (o `Esc`) chiude la modalità e la toolbar sparisce.

### 3.3 Stato "backend in restart"

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ ◆ CAD Studio │ [ bracket-clip ▾ ] ● Saved   ↶ ↷ ┊ ▷ Run  ◌ Live  ◍ Backend restarting… ┊ ◌ ... │
│                                              (disabled)        (pill in stato sistema)    (amber)│
└───────────────────────────────────────────────────────────────────────────────────────────┘
   ▷ Run = disabilitato (grigio).   ◌ salute = amber pulsante.   pill = "Backend restarting…"
   Un banner sottile sotto la barra: ⟳ Riavvio backend… in attesa di health (3s)   [ Annulla ]
```

- *Run* disabilitato (loading/disabled), tooltip "Backend non disponibile".
- Salute backend → **amber pulsante** durante `offline→connecting`, poi verde a `ready`.
- Banner di sistema (riga sottile, colore "sistema") con progress testuale e i tre
  stadi: `Riavvio → in attesa di health → pronto`. Sparisce ad health OK con toast
  "Backend pronto ✓".

---

## 4. Wireframe — Pannelli & dialog

### 4.1 Stato documento / sessione (componente `doc-status`)

Componente compatto in top bar, 4 stati mutuamente esclusivi + menu progetto:

```
● Saved            (verde tenue)  — disco == memoria
● Unsaved          (amber)        — pending changes;   tooltip "Salva: Ctrl+S"
◍ Saving…          (spinner)      — autosave/manuale in corso
⚠ Save failed      (rosso)        — click → retry + dettaglio in toast

[ bracket-clip ▾ ]  ← click apre il menu progetto:
   ┌──────────────────────────────────────┐
   │ 🔍 Cerca progetto…                     │
   │ ────────────────────────────────────  │
   │ ● bracket-clip            (corrente)   │
   │   voronoi-lamp                         │
   │   variable-loft                        │
   │ ────────────────────────────────────  │
   │ ＋ Nuovo…            Ctrl+N            │
   │ ✎ Rinomina…                            │
   │ ⧉ Duplica                              │
   │ 🗑 Elimina…           (rosso)           │
   └──────────────────────────────────────┘
```

- Guardia su azioni distruttive con modifiche pendenti (Nuovo / cambio progetto /
  elimina): dialog "Hai modifiche non salvate — Salva / Scarta / Annulla".

### 4.2 Console log unificata (tab "Console")

```
┌─ Code · View/Data · Panels · ⛌ Console ⑵ · 🤖 AI ─────────────────────────────────┐
│ Sorgente:[Tutto ▾]  Livello:[● info ● warn ● error]   ⏸ Pausa  🗑 Clear  ⧉ Copia ☑Auto│
│ ───────────────────────────────────────────────────────────────────────────────── │
│ │ 12:03:41 ·client· ▶ Run requested                                                 │
│ │ 12:03:41 ·backend· INFO  uvicorn  POST /api/graph/bracket-clip/execute            │
│ │ 12:03:42 ·backend· INFO  worker   meshing 4 solids…                               │
│ ▍│ 12:03:42 ·backend· ERROR worker   node n7 (Fillet): radius too large  → [n7 ↗]   │  ← rosso, cliccabile
│ │ 12:03:42 ·client· ⚠ node n7 highlighted on canvas                                 │
│ ───────────────────────────────────────────────────────────────────────────────── │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- Riga taggata `·client·` / `·backend·` con bordo-sinistro colorato per sorgente.
- Filtri sorgente + livello combinabili. **Pausa** congela l'auto-scroll; **Clear**
  pulisce; **Copia** copia il buffer filtrato.
- `[n7 ↗]` su un errore worker → seleziona e centra il nodo sul canvas (correlazione
  con `node_errors`).
- Badge `⑵` sul tab = conteggio errori non letti (azzerato all'apertura del tab).

### 4.3 Dialog di restart backend (da menu ⚙ Sistema)

```
   menu ⚙:                          dialog conferma:
   ┌──────────────────────┐         ┌──────────────────────────────────────────┐
   │ ◉ Backend: online     │         │  Riavviare il backend?                    │
   │ ─────────────────────│         │                                            │
   │ ⟳ Restart backend…    │  ──▶    │  Interrompe l'esecuzione in corso e ricarica│
   │ ⛌ Apri console        │         │  il codice Python. ~3–5 s di indisponibilità.│
   │ ⚙ Impostazioni        │         │  L'app è locale: nessun altro utente colpito.│
   └──────────────────────┘         │                                            │
                                     │              [ Annulla ]  [ ⟳ Riavvia ]    │
                                     └──────────────────────────────────────────┘
                                          secondaria      sistema (non rossa: non
                                                          è distruttiva sui dati)
   stato durante (vedi §3.3): banner + Run disabilitato + health amber pulsante.
```

- *Riavvia* è classe **sistema** (non destructive-rosso: non perde dati, solo
  riavvia un processo). La distruttività vera (Elimina progetto) è rossa.

### 4.4 Ripristino bozza al reload

Appare *solo* quando il disco è cambiato sotto la bozza locale (D1):

```
┌──────────────────────────────────────────────────────────────────┐
│  Modifiche non salvate trovate                                     │
│                                                                    │
│  Hai una bozza locale di "bracket-clip" più recente della versione │
│  su disco, ma il file su disco è cambiato nel frattempo.           │
│                                                                    │
│   Bozza locale (browser)        Versione su disco                  │
│   • 12 nodi, 14 connessioni     • 10 nodi, 11 connessioni          │
│   • salvata 12:01 (non su disco)• salvata 11:58                    │
│                                                                    │
│         [ Apri da disco (scarta bozza) ]   [ Ripristina bozza ]    │
│              secondaria                        primaria             │
└──────────────────────────────────────────────────────────────────┘
```

- Caso semplice (disco invariato, bozza più recente) → nessun dialog: la bozza viene
  applicata e lo stato è `● Unsaved`.

### 4.5 Command palette (Ctrl+K)

```
┌──────────────────────────────────────────────────────────────────┐
│ ⌘  > rest|                                                         │
│ ────────────────────────────────────────────────────────────────│
│  ⟳  Restart backend…                                     ⚙        │
│  ⛌  Apri console backend                                          │
│  ⬇  Export STEP                                                    │
│  ▦  Toggle griglia                                       G        │
│  ⛶  Fit alla vista                                       F        │
│  ✎  Rinomina progetto…                                            │
│ ────────────────────────────────────────────────────────────────│
│  ↑↓ naviga · ⏎ esegui · esc chiudi                                │
└──────────────────────────────────────────────────────────────────┘
```

- Fuzzy match su nome azione; mostra la scorciatoia a destra (così la palette *insegna*
  le shortcut). Sostituisce la riga di hint testuale.

---

## 5. Design system

### 5.1 Tassonomia bottoni (5 classi)

```
CLASSE            ASPETTO                          USO                       ESEMPI
──────────────────────────────────────────────────────────────────────────────────────
Primaria          fill accent, label+icona,        UNA per area, l'azione    ▶ Run
                  peso alto                         dominante
Secondaria        ghost/outline, low-fill on       azioni frequenti non      💾 Save, ⛶ Fit,
                  hover                             dominanti                 ⬇ Export
Toggle            pill; OFF=outline tenue,          stato on/off persistente  ◌ Live, ▦ Grid,
                  ON=fill accent + dot pieno                                  ⌗ Snap, 🌐 World
Sistema/Distrutt. muto/freddo; distruttiva = bordo  azioni di sistema o       ⟳ Restart(sys),
                  rosso solo in conferma            irreversibili             🗑 Elimina(destr.)
Contestuale       come toggle/secondaria ma con     compaiono solo in         barra gizmo
                  accento --accent-edit; appare/    contesto                  (Drag/Snap/World)
                  scompare
```

Regole trasversali:
- **Icona + label** per le azioni in barra; **solo icona + tooltip** per la zona
  utility compressa (⚙ ⌘K ? ⧉) — ma sempre `aria-label` + tooltip.
- Una sola primaria visibile per zona. Mai due fill-accent affiancati.
- Le distruttive non sono mai rosse "a riposo": il rosso compare nel dialog di conferma.

### 5.2 Stati per ogni bottone

```
            default     hover            active/pressed   disabled        loading
Primaria    fill 100%   fill +8% bright  fill -8% / inset  fill 30% + cur  spinner + "…"
Secondaria  outline     bg ghost 8%      bg ghost 14%      opacity 40%     spinner inline
Toggle OFF  outline     bg ghost 8%      —                 opacity 40%     —
Toggle ON   fill accent fill +8%         —                 fill accent 40% pulse accent
Sistema     muted txt   bg ghost         bg ghost 14%      opacity 40%     spinner
Distruttiva muted txt   bg red-ghost     bg red-ghost 14%  opacity 40%     spinner red
```

- **Focus visibile** (tastiera): ring `2px --focus` su tutti, sempre.
- **Loading** disabilita il click e mostra spinner; il layout non deve "saltare"
  (riserva larghezza).

### 5.3 Pattern di stato / feedback

```
COMPONENTE          STATI / VARIANTI                                  POSIZIONE
──────────────────────────────────────────────────────────────────────────────────
Run status pill     Ready · ◍ running… · done ✓ · ⚠ error(n)          top bar (esecuzione)
Salute backend dot  ◉verde online · ◍amber busy/connecting ·          top bar (accanto Run)
                    ◌rosso offline · ◌grigio sconosciuto
Doc-status          ●Saved · ●Unsaved · ◍Saving… · ⚠Save failed       top bar (progetto)
Toast               info / success / warn / error(persist+azione)     bottom-right, stack
Badge su tab        numero (errori console, # panel)                  sui tab del pannello
Banner di sistema   restart in corso (3 stadi) · backend offline      riga sotto la top bar
```

- **Toast** non bloccanti, auto-dismiss 4s (gli error persistono finché non chiusi e
  offrono un'azione, es. "Apri console").
- **Banner** solo per condizioni di sistema persistenti (restart/offline), non per
  eventi una-tantum (quelli sono toast).
- **Mappatura colori semantici** (riusa gli accenti wire del tema dove sensato):
  verde=ok, amber=in corso/attenzione, rosso=errore/offline, accent=primario/attivo,
  accent-edit=modalità contestuale, "sistema"=grigio-azzurro freddo.

---

## 6. Token visivi & griglia

```
SPAZIATURA   base 4px → 4/8/12/16/24.  Gap intra-gruppo 8px; gap inter-zona 16px + separatore.
ALTEZZA BAR  40px (compatta, CAD-dense). Bottoni 28px h. Pill 22px h. Dot 8–10px.
SEPARATORI   ┊ verticale 1px @ 40% opacity tra le zone funzionali.
OVERFLOW     quando lo spazio finisce: la zona utility collassa per priorità →
             prima spariscono ⧉/? dentro "…", poi Export sotto ⚙. Run/stato/salute
             non collassano MAI.
DENSITÀ      label brevi (Run, Live, Fit), tooltip per l'esteso. Icone monospaziate.

COLORI (dark "CAD", indicativi)
  --bg          #0b0d10   (quasi nero)
  --panel       #14171c
  --line        #232830
  --txt         #e6e9ef   --txt-dim #8b93a1
  --accent      #4ea1ff   (primario/attivo — allinea al colore preview)
  --accent-edit #c08bff   (modalità gizmo contestuale)
  --ok #46c46a   --warn #e0a23a   --err #e5484d   --sys #7c8aa5
  --focus #6fb0ff (ring)
```

Accessibilità: contrasto testo/sfondo ≥ WCAG AA (4.5:1) per label, ≥3:1 per icone/UI;
stati non veicolati dal solo colore (sempre icona/forma+colore: dot pieno vs vuoto,
label "online/offline"); navigazione tastiera completa con focus ring; `aria-live` per
toast e cambi di salute backend.

---

## 7. Note di implementazione (per chi costruirà)

Resta una web app statica leggera (HTML/CSS, niente framework). I componenti sopra
sono `div`/`button` con classi; popover/dialog sono `<dialog>` nativo o overlay css.

**Nuovi endpoint backend richiesti** (oltre a quanto esiste):
- `POST /api/system/restart` → riavvia il processo (il container è single-user/local;
  in pratica chiude e lascia che il supervisor/docker `--restart` lo rialzi, oppure
  un re-exec di uvicorn). Risponde subito; l'UI poi fa polling.
- `GET /api/system/health` → `{status, uptime, latency_ms}` per il dot di salute
  (poll ogni ~3s; durante restart copre `offline→connecting→ready`).
- `GET /api/system/logs?source=&level=&since=` (o **SSE/WebSocket**
  `/api/system/logs/stream`) → stream di uvicorn+worker per la console unificata.
  Lo streaming è preferibile per l'auto-scroll live; il polling è il fallback.
- Autosave usa l'esistente writer del server (`store.py` via `/api/graph/{name}`);
  la bozza locale è puramente client (`localStorage`/IndexedDB), nessun endpoint.

**Invarianti preservati:** canvas a nodi + viewport 3D restano i protagonisti
centrali; occhio per-nodo, gizmo *Edit on canvas*, *Edit text* del Panel, barra di
selezione flottante e tutte le scorciatoie esistenti trovano posto coerente (overlay
viewport / toolbar contestuale / palette) senza sparire.
```
```
