# PLAN — algorithms as geometry

A track of **explanatory example graphs**: an algorithm rebuilt as a node graph, so
that its geometry *is* its explanation. Drag a slider and the maths redraws.

The first one shipped — `cad_nodes/examples/softmax.json` — set the pattern. This
doc records the pattern, then lists the rest so we can work through them.

Audience note: the canvas Notes inside the example graphs are written in **Italian**
(they are for the user, who reads Italian); this plan and the code comments stay in
English, like the rest of the repo.

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
  always exactly full. `T → 0` is argmax, `T → ∞` is uniform; an `onda`/`t` pair
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
  on the centroids gives the decision regions for free. `iterazioni` = 0 shows the
  random init; drag it up and the centroids migrate into the clusters while the
  regions settle. The inertia readout is the number the algorithm minimises — it
  never goes back up. Wrong `k` still "finds" groups: k-means does not tell you how
  many there are, it asks. 16 nodes.
- [ ] **Perceptron / decision boundary** — two clouds of points, the boundary as a
  plane that tilts with the weights. With a small MLP the boundary becomes a curved
  surface.
- [ ] **Convolution** — an input height-field, a 3×3 kernel as nine sliders, the
  output height-field beside it: blur, sharpen, edge-detect by moving nine numbers.
- [ ] **Overfitting** — noisy points and a degree-`d` polynomial fit; raise `d` and
  watch the curve contort to touch every point.
- [ ] **A network as an object** — neurons as spheres, weights as cylinders with
  radius ∝ |w|. `Range` → fan-out builds the layers; the parameter count becomes
  visible mass.

### Statistics & numerics

- [x] **Central limit theorem** — `examples/central-limit.json`. Average `n` uniform
  numbers — a perfectly flat distribution, nothing gaussian anywhere — 4000 times,
  and count where the averages land. n=1 is flat; n=2 is a triangle; by **n=3 the
  bell is there**. Two things happen at once, and together they are the theorem: the
  shape becomes a bell regardless of what you summed, and the width shrinks as
  1/√n. The yellow curve is the gaussian the theorem *predicts* (same μ, same σ),
  not a fit — at n=1 it misses the bars entirely, which is the honest picture.
  13 nodes.
- [ ] **Riemann sums** — bars under a curve, slider `n`; `Volume`/`Area` reads the
  convergence error.
- [ ] **Sampling & aliasing** — a sine and its samples; below Nyquist the ghost wave
  appears.

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
- [ ] **L-systems / fractal trees** — a recursive CodeBlock, slider `depth`. The
  output is a printable object.
- [ ] **Dijkstra / A\*** — a grid of cells, height = cost, the found path lit up.
- [ ] **Perlin noise** — procedural terrain, sliders for octaves and persistence; the
  bridge to generative design proper.

---

## 3. Where they live

Each one is a bundled example: `cad_nodes/examples/<name>.json` + a one-line entry in
`_EXAMPLE_DESCRIPTIONS` (`cad_nodes/store.py`), so `seed_examples()` carries it to a
fresh install. Build them with a script (validate with `Graph.validate()` before
writing), not by hand in the editor — the graphs are 40–60 nodes.
