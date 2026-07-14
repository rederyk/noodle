# PLAN — algorithms as geometry

A track of **explanatory example graphs**: an algorithm rebuilt as a node graph, so
that its geometry *is* its explanation. Drag a slider and the maths redraws.

The first one shipped — `cad_nodes/examples/softmax.json` — set the pattern. This
doc records the pattern, then lists the rest so we can work through them.

Everything is in English — the canvas Notes, the `#@param` names, the 3D labels — like
the rest of the repo. (The first six were drafted in Italian and translated; remember
that `#@param` names are also socket names, so renaming one rewires the graph.)

---

## 1. The pattern

Three bands, left to right, mirrored by three LiteGraph groups:

1. **INPUT** — the knobs. One slider per free variable.
2. **THE ALGORITHM** — the formula/loop unrolled with catalog nodes wherever it can
   be (so the graph *is* the maths), a `CodeBlock` only where a node genuinely
   cannot express it (loops, reductions, recursion).
3. **THE FIGURES** — the same numbers as geometry: bars, discs, surfaces, paths.

**Animation is a slider, not a timeline.** Expose a `t` (or `step`/`k`) parameter,
let the user drag it, and the memo cache re-runs only the dirty subtree (~0.3 s on
the softmax graph). That is the animation. No new engine feature is needed.

**Numbers on screen are geometry**: a CodeBlock builds `Text(...)` and extrudes it,
so labels rewrite themselves on every run. `Display` nodes give the exact values on
the canvas side.

### Idioms that make it work

| want | do |
|---|---|
| N shapes from N numbers | wire a **list** into an item-access socket — the node fans out (one `Box` node → 4 bars) |
| a scalar that may be negative | a `CodeBlock` `#@param float min=-5 max=5` — catalog `NumberSlider` is hard-floored at 0 |
| a whole list into a `CodeBlock` | you can't: `in_0..in_5` are item-access and would fan out. Use `ListItem` to pick scalars, or pass a **dict/callable** (a non-list object rides the `data` bus whole) |
| a list *out* of a `CodeBlock` | follow it with `ListCreate`: `CodeBlock` is not in `_LIST_PRODUCERS`, so downstream nodes would not know to fan out |
| a reduction (sum, product) | there is no Sum node: `ListItem` ×N + an `Add` chain — which is also the clearest way to *show* a summation. `Bounds` + `ListLast` gives max |
| the position/size of a shape from data | `ConstructPoint` → `origin`; the value → the matching `_pin` socket (`Box.depth`, `Sphere.radius`) |
| a draggable point in the 3D view | `ConstructPoint` carries a translate gizmo — the user drags the input *in the viewport* |
| serious numerics (least squares, SVD…) | `import numpy as np` **inside** the CodeBlock body — numpy/scipy are in the worker; the PREAMBLE just doesn't import them for you |
| a tube from point A to point B | there is no such node (`Cylinder` has an origin, not a direction): `Plane(origin=(a + b) * 0.5, z_dir=b - a) * Cylinder(r, (b - a).length)` |
| two colours in one figure | colour is **per node**, so split the geometry into two nodes (kept bars / cut bars, positive weights / negative weights) — that is the only lever there is |

### Gotchas paid for already

- **`Box.depth` is the vertical (Z)** dimension, not `height`: the template is
  `Box(width, height, depth)` → build123d `Box(X, Y, Z)`.
- **A zero-height bar is a degenerate solid.** OCCT rejects it, and because the node
  is fanned the error kills the *whole row*. Always `max(value * scale, 0.15)`.
- **`Expression` only works when its input is a list** — the fan-out lambda is what
  binds `x`. Fed a scalar it emits `(math.exp(x))` with `x` unbound →
  `NameError`. *This is an app bug, not a demo constraint — worth fixing in
  `transpiler.py`.*
- **Divide-by-zero knobs** (a temperature, a learning rate) want a `Clamp` behind
  the slider.
- A `Spline` needs ≥ 2 distinct points, and a B-spline forced through a path that
  reverses on itself (a diverging descent) dies with `Standard_ConstructionError`.
  For a discrete walk use `Polyline` — which is the honest shape anyway — and drop
  consecutive points closer than ~0.02 mm: a zero-length segment is a null edge.
- A `CodeBlock` output can be **any Python object**, not just geometry or numbers:
  a dict, a closure. It rides the `data` bus whole (no fan-out, since a CodeBlock
  is not a list producer). That is how one node can own a function and hand it to
  three others.
- Socket names bite: `Extrude` takes `sketch` (not `profile`), `MakeFace` takes
  `edges`, `Bounds` *outputs* `domain`. Validate the graph before writing it —
  `Graph.validate()` names the offending connection.
- **`__result__` is the last GEOMETRY-typed node**, not the last node emitted. If that
  node can legitimately produce nothing — the perceptron's "still wrong" markers, once
  it has converged — the whole run reports `success: false, error: "no result shape"`
  even though every preview rendered fine. Order the graph so the last geometry node
  is one that always exists.
- Geometry is not free: one `Box` fanned into ~500 cubes costs ~2 s, and an L-system
  breeds branches exponentially (8 per pass). Cap the count in the CodeBlock and *say
  so* on the node — a silent truncation reads as a wrong answer.
- **Put two quantities on ONE scale and the figure argues for itself.** The probabilities
  and their running total both live in [0,1], so drawing the staircase in the *same*
  vertical scale as the bars (just in front of them, not in another row) turns the "line
  at p" into a real intersection — the wall lands exactly where the staircase crossed it.
  Two rows with two scales would have been the same numbers and no argument.
- **Too many cycles in one frame is a hairball.** The aliasing scene opened at 2 s ×
  7 Hz = 14 wiggles and read as noise; at 1 s it reads as a wave. Same for a
  default that is *technically* the point but visually empty (the nucleus example first
  opened on the prompt where the answer is certain — one bar, nothing to see). **Pick the
  default that teaches**, and let the slider reach the extremes.
- **Clip a wild function, and say that you clipped it** — a degree-12 polynomial reaches
  10⁵ where the chart is 6 units tall. Clamp the drawn z and put "clipped" in the Note;
  the printed error number carries the truth the geometry can't.
- **Composition is part of the lesson.** An arrow drawn along a cube's edge is
  invisible inside the solid (draw it ~1.35× long so it pokes out, and look at the
  origin corner); a flat scene (epicycles, k-means) is read from above, so its
  labels must lie *on* the XY plane, while a scene with height (bars, a loss
  surface) wants labels standing on `Plane.XZ`, facing the camera.

---

## 2. The list

Status: `[x]` shipped · `[~]` in progress · `[ ]` planned.

### Machine learning

- [x] **Softmax** — `examples/softmax.json`. z/T → exp → sum → normalise, wired node
  by node. Three rows of bars (logits, exp weights, probabilities) + a pie that is
  always exactly full. `T → 0` is argmax, `T → ∞` is uniform; a `wave`/`t` pair
  animates the winner travelling between classes.
- [x] **Gradient descent on a loss surface** — `examples/gradient-descent.json`. A
  height-field surface from `Face.make_surface_from_array_of_points`, the descent
  path as a chain of spheres + a `Polyline` trail, `k` = the step shown. Three
  lessons, all reachable with one slider: `lr` too high → the path zig-zags across
  the ravine and ends up *worse* than it started; `lr` too low → it never arrives;
  drag the starting point (a `ConstructPoint`, so it carries a **gizmo in the
  viewport**) into the wrong basin and it settles in a local minimum. 17 nodes.
  The gradient is a central difference, so the algorithm knows nothing about `f` —
  reshape `f` and the descent still works. **New idiom:** one node owns `f` and
  sends the *callable itself* down a `data` wire (`result = {"f": f, ...}`) to the
  surface, the descent and the labels — one definition, three consumers.
- [x] **Linear algebra: a matrix as a deformation** — `examples/matrix-determinant.json`.
  Nine sliders apply a 3×3 matrix to a unit cube via `Shape.transform_geometry`
  (a *non-rigid* transform: it may stretch and mirror). The catalog's `Volume` node,
  pointed at the deformed solid, reads |det| × L³ — the determinant is not a formula
  to memorise, it is that volume. Shear it and the volume does not move; zero a row
  and it flattens to a plane (det = 0 *is* "not invertible"); flip a sign and space
  turns inside out. The three arrows are the matrix's columns. 10 nodes.
- [x] **Attention** — `examples/attention.json`. The sequel to `softmax`: the same
  function, taken one **row** at a time. A blue grid of raw scores q·k/√d (negative
  bars hang below the plane, exactly like logits), a green grid after the softmax
  where every row sums to 1. The payoff is the `causal` switch: the upper triangle
  vanishes — a token may not look at the future, so those scores go to −∞ *before*
  the exp — and each surviving row **still** sums to 1, the masked attention having
  been redistributed to the past. 16 nodes.
  Still to do from here: top-k / nucleus sampling = cutting the pie at cumulative 0.9.
- [x] **K-means** — `examples/kmeans-voronoi.json`. Lloyd's algorithm, and `Voronoi2D`
  on the centroids gives the decision regions for free. `iterations` = 0 shows the
  random init; drag it up and the centroids migrate into the clusters while the
  regions settle. The inertia readout is the number the algorithm minimises — it
  never goes back up. Wrong `k` still "finds" groups: k-means does not tell you how
  many there are, it asks. 16 nodes.
- [x] **Perceptron** — `examples/perceptron.json`. The first learning machine (1958):
  never told the rule, only told when it guessed wrong, and it leans its line towards
  the point it missed. Drag `epochs` and the red mistakes wink out one by one, until
  it converges and stops *for good* — a perceptron that is never wrong never changes
  again (the convergence theorem, visible). Then add `noise`: no line can be right any
  more, and it never settles, chasing points it cannot win. It has no notion of "good
  enough" — that idea needed a loss function, which is the next example along. 18 nodes.
- [x] **Convolution** — `examples/convolution.json`. The image as a field of columns,
  the 3×3 kernel as nine little columns beside it, the answer on the right. Edge detect
  returns exactly **zero** wherever the picture is flat, so the disc's interior
  collapses and only its rim survives. Some answer columns hang *below* the plane: the
  sign says which side is brighter. It is what a vision network's first layer does —
  the network merely *learns* the nine numbers. 22 nodes.
- [x] **Nucleus sampling (top-p)** — `examples/nucleus-sampling.json`. The sequel to
  `softmax`, and the thing that actually happens at the end of every LLM forward pass:
  the probabilities are not an answer, someone still has to *choose*. The sorted bars,
  the cumulative staircase **in the same vertical scale** so the line at `p` is a real
  intersection, the wall where it crossed, the tail beyond it, and the pie of what
  survives (renormalised, so it is exactly full again). The payoff is one drag: leave
  `p` at 0.9 and change `context`. "The capital of France is ___" → the nucleus is ONE
  word. "She opened the door and saw ___" → it swells to most of the vocabulary. Same p,
  same algorithm; the cut moved because the model's confidence moved. Top-k (mode 1)
  cannot do that, and fails at both ends. 23 nodes.
- [x] **Overfitting** — `examples/overfitting.json`. A degree-`d` polynomial through
  noisy points, plus 40 **held-out** points it never sees. Two bars: the error on what it
  was shown falls forever (it must — that is what fitting means), the error on what it
  was not shown bottoms out around d=5 and then climbs. That U is the bias–variance
  trade-off, and the left bar can never tell you where its bottom is. At `d = n-1` the
  curve passes through every training point, scores **exactly zero**, and has learned
  nothing but the noise. Then `ridge` = 0.02 tames the monster *without removing a single
  coefficient* — and beats every unregularised fit. 20 nodes.
- [x] **A network as an object** — `examples/neural-network.json`. One sphere per neuron,
  one cylinder per weight, radius ∝ |w|, positive and negative in two colours (two nodes:
  colour is per-node). Drag `width` 8→16: you added 8 neurons and *hundreds* of wires —
  weights between two layers are a **product**, so the parameter count is an area, not a
  length. Then drag `prune` to 0.4: most of the tubes vanish and the object still looks
  like itself, because a trained net really is mostly near-zero weights. That is every
  pruned/quantised model you have ever run on a laptop. 12 nodes.

### Statistics & numerics

- [x] **Central limit theorem** — `examples/central-limit.json`. Average `n` uniform
  numbers — a perfectly flat distribution, nothing gaussian anywhere — 4000 times,
  and count where the averages land. n=1 is flat; n=2 is a triangle; by **n=3 the
  bell is there**. Two things happen at once, and together they are the theorem: the
  shape becomes a bell regardless of what you summed, and the width shrinks as
  1/√n. The yellow curve is the gaussian the theorem *predicts* (same μ, same σ),
  not a fit — at n=1 it misses the bars entirely, which is the honest picture.
  13 nodes.
- [x] **Riemann sums** — `examples/riemann-sums.json`. The integral caught in the act
  of being invented: chop, add, be wrong, chop finer. The error readout falls as you
  drag `n` and never reaches zero at any finite n — that sentence, made rigorous, *is*
  the definition. Switching from the left edge to the **midpoint** collapses the error
  at the same n: a smarter place to look beats a bigger n, which is the whole of
  numerical analysis. Even the "exact" value is a staircase, just a very fine one.
  15 nodes.
- [x] **Sampling & aliasing** — `examples/aliasing.json`. A sine, a clock that looks at
  it `fs` times a second, and the **ghost**: the slowest wave through every sample. It
  agrees with the evidence *exactly*, not approximately, so nothing downstream can prefer
  the true one — that is the entire problem, in one picture. Below `fs/2` the ghost takes
  the shape of the original (the sampling theorem, as a congruence you can see); above it,
  7 Hz arrives as a calm, innocent 3 Hz. At `f = fs` every sample catches the same phase
  and the machine records a **flat line** while the wave is still out there. Wagon wheels,
  moiré, and the analogue filter welded in front of every ADC. 20 nodes.

### Algorithms & geometry

- [ ] **Sorting, animated** — bars of differing height, slider `step` replays the
  algorithm's state at step k.
- [x] **Cellular automata** — `examples/cellular-automata.json`. An elementary CA:
  one live cell, one byte of rule, and the generations **stacked along Z** into a
  printable tower of time (one `Box` node fans out into ~500 cubes, ~2 s). Rule 90
  is a Sierpinski triangle out of nothing; rule 30 is chaos so convincing it has
  been used as a random generator, though nothing in it is random; rule 110 is
  Turing complete. Eight bits. 9 nodes.
- [x] **Fourier series / epicycles** — `examples/fourier-epicycles.json`. Each harmonic
  is a circle riding on the tip of the last; the pen (a red sphere) traces the wave,
  unrolled in time to the right — one `Circle` node draws all N circles by fanning out
  over a list of radii and centres. Drag `t` and the wheels turn. One circle = a pure
  sine, and it can do nothing else; add harmonics and the square wave's corners
  sharpen but **never** arrive (the ripples that stay are Gibbs). `forma` swaps square
  / sawtooth / triangle by changing only *which* circles you take and how big they
  are — the machine is the same. 18 nodes.
- [x] **De Casteljau** — `examples/de-casteljau.json`. The construction the CAD kernel
  itself runs: no polynomial, just interpolation repeated until one point is left.
  Drag `t` and the ladder collapses onto the curve, whose trace lies exactly on the
  curve the catalog's `Bezier` node draws — same curve, two ways of saying it. The
  four control points are `ConstructPoint`s, so you **drag them in the viewport** and
  watch the curve get pulled towards the middle two without ever passing through
  them. 25 nodes.
- [x] **L-systems** — `examples/l-system.json`. One letter, one rewrite rule, a turtle
  — and a tree. `depth` adds a whole generation of twigs per notch; `angle` changes the
  species. Every branch is a real tapered cone, so unlike the rest of this family the
  output is genuinely printable. Branches breed exponentially, so `max_branches` caps
  them and the label says when it bit.
- [ ] **Dijkstra / A\*** — a grid of cells, height = cost, the found path lit up.
- [ ] **Perlin noise** — procedural terrain, sliders for octaves and persistence; the
  bridge to generative design proper.

---

## 3. Where they live

Each one is a bundled example: `cad_nodes/examples/<name>.json` + a one-line entry in
`_EXAMPLE_DESCRIPTIONS` (`cad_nodes/store.py`), so `seed_examples()` carries it to a
fresh install. Build them with a script (validate with `Graph.validate()` before
writing), not by hand in the editor — the graphs are 40–60 nodes.
