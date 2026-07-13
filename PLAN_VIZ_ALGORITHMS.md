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
- [ ] **Linear algebra: a matrix as a deformation** — 9 sliders apply a 3×3 matrix to
  a unit cube. The `Volume` node reads the **determinant**; det = 0 flattens the
  cube into a plane. Eigenvectors are the edges that do not rotate.
- [ ] **Attention** — QKᵀ as a grid of columns, then softmax *per row*: the rows
  normalise one by one. Extends to top-k / nucleus sampling = cutting the pie at
  cumulative 0.9. Natural sequel to the softmax example.
- [ ] **K-means** — points from `Random`, centroids as spheres, and `Voronoi2D` on
  the centroids gives the decision regions for free (already a catalog node).
  Sliders: `k`, `iteration`.
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

- [ ] **Central limit theorem** — a histogram of bars; slider `n` = how many uniforms
  are summed. Flat at n=1, a bell by n=5.
- [ ] **Riemann sums** — bars under a curve, slider `n`; `Volume`/`Area` reads the
  convergence error.
- [ ] **Sampling & aliasing** — a sine and its samples; below Nyquist the ghost wave
  appears.

### Algorithms & geometry

- [ ] **Sorting, animated** — bars of differing height, slider `step` replays the
  algorithm's state at step k.
- [ ] **Cellular automata** — Game of Life / Rule 30, with generations **stacked
  along Z**: a printable tower of time.
- [ ] **Fourier series / epicycles** — circles on circles drawing a curve; slider
  `harmonics`. Fan-out was made for this.
- [ ] **De Casteljau** — the Bézier construction at parameter `t`, the interpolation
  lines collapsing onto the point. Almost obligatory in a CAD app.
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
