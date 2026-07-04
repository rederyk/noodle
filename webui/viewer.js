// ════════════════════════════════════════════════════════════════════
// CadViewer — the shared Three.js viewport for BOTH webui pages
// (the /ui code view and the /nodes editor).
//
// It owns the common, page-agnostic parts: a Z-up CAD scene (grid = XY
// construction plane, origin marker, lights), an orbit camera, the navigation
// ViewHelper, the animate loop, framing/resize, and — crucially — the
// multi-mesh preview renderer that turns an execution `view.previews` dict into
// coloured meshes / polylines / point spheres. This is the "good" render path;
// previously it lived only in nodes.html, so /ui got a worse single-STL view.
//
// Page-specific behaviour stays OUT of here and hooks onto the exposed objects:
//   - the node editor attaches its TransformControls gizmo to viewer.scene/camera
//     and its click-to-select picking via viewer.pick();
//   - both pages supply colorOf/wireOf/onEmpty callbacks to renderPreviews.
// ════════════════════════════════════════════════════════════════════
import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { ViewHelper } from 'three/addons/helpers/ViewHelper.js';

// distinct, legible-on-dark palette; terminals cycle through it by default
export const PALETTE = ['#2dd4a0', '#3b82f6', '#f59e0b', '#e94560', '#a855f7',
  '#fb923c', '#22d3ee', '#84cc16', '#ec4899', '#eab308', '#14b8a6', '#8b5cf6',
  '#f43f5e', '#38bdf8'];

export function nodeNum(id) { const m = /(\d+)/.exec(id || ''); return m ? parseInt(m[1]) : 0; }

// ── pure data -> THREE object builders (no app/page state) ──
function geomFromData(m) {
  const g = new THREE.BufferGeometry();
  const verts = m.vertices, tris = m.triangles;
  const pos = new Float32Array(verts.length * 3);
  for (let i = 0; i < verts.length; i++) { pos[3 * i] = verts[i][0]; pos[3 * i + 1] = verts[i][1]; pos[3 * i + 2] = verts[i][2]; }
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const idx = new Uint32Array(tris.length * 3);
  for (let i = 0; i < tris.length; i++) { idx[3 * i] = tris[i][0]; idx[3 * i + 1] = tris[i][1]; idx[3 * i + 2] = tris[i][2]; }
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeVertexNormals();
  return g;
}
export function meshFromData(m, color) {
  return new THREE.Mesh(geomFromData(m),
    new THREE.MeshStandardMaterial({ color, roughness: .4, metalness: .12, side: THREE.DoubleSide }));
}
function lineFromPolylines(polys, color) {
  const segs = [];
  for (const poly of polys)
    for (let i = 0; i + 1 < poly.length; i++) { segs.push(poly[i], poly[i + 1]); }
  const g = new THREE.BufferGeometry();
  const pos = new Float32Array(segs.length * 3);
  for (let i = 0; i < segs.length; i++) { pos[3 * i] = segs[i][0]; pos[3 * i + 1] = segs[i][1]; pos[3 * i + 2] = segs[i][2]; }
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, linewidth: 2 }));
}
function spheresFromData(pts, color, r) {
  const geo = new THREE.SphereGeometry(r, 12, 12);
  const mat = new THREE.MeshStandardMaterial({ color, roughness: .45, metalness: .1 });
  const im = new THREE.InstancedMesh(geo, mat, pts.length);
  const m = new THREE.Matrix4();
  for (let i = 0; i < pts.length; i++) { m.setPosition(pts[i][0], pts[i][1], pts[i][2]); im.setMatrixAt(i, m); }
  im.instanceMatrix.needsUpdate = true;
  im.userData.points = pts;           // keep coords for picking / edit mode
  return im;
}
function previewsExtent(previews, ids) {
  const box = new THREE.Box3(); let any = false;
  for (const id of ids) {
    const e = previews[id]; if (!e || !e.bbox) continue;
    box.expandByPoint(new THREE.Vector3(...e.bbox.min));
    box.expandByPoint(new THREE.Vector3(...e.bbox.max)); any = true;
  }
  if (!any) return 50;
  const s = new THREE.Vector3(); box.getSize(s);
  return Math.max(s.length(), 1);
}
function objFromPreview(p, color, opts, scale) {
  if (p.mesh) {
    const obj = meshFromData(p.mesh, color);
    if (opts && opts.wireframe) { obj.material.wireframe = true; obj.material.metalness = 0; }
    return obj;
  }
  if (p.polylines) return lineFromPolylines(p.polylines, color);
  if (p.points) {
    const r = Math.min(Math.max(scale * 0.012, 0.4), scale * 0.05);
    return spheresFromData(p.points, color, r);
  }
  return null;
}

export class CadViewer {
  constructor(canvas, { background = 0x0b0e14 } = {}) {
    this.canvas = canvas;
    this.currentMesh = null;
    this._framed = false;
    const c = canvas.parentElement;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(background);
    // Z-up (build123d/CAD convention): the grid IS the XY construction plane,
    // fixed at the world origin — it never follows the geometry.
    const grid = new THREE.GridHelper(200, 40, 0x2a3346, 0x161b25);
    grid.rotation.x = Math.PI / 2; scene.add(grid);
    // origin marker so (0,0,0) is always visible even when shapes move off it
    scene.add(new THREE.Mesh(new THREE.SphereGeometry(0.8, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0xe94560 })));
    const previewGroup = new THREE.Group(); scene.add(previewGroup);
    const ax = new THREE.AxesHelper(20); ax.material.transparent = true; ax.material.opacity = .6; scene.add(ax);
    scene.add(new THREE.AmbientLight(0x404060, 2.5));
    const key = new THREE.DirectionalLight(0xffffff, 2); key.position.set(40, 60, 80); scene.add(key);
    const fill = new THREE.DirectionalLight(0x8888cc, .8); fill.position.set(-40, -20, 40); scene.add(fill);

    const camera = new THREE.PerspectiveCamera(40, c.clientWidth / c.clientHeight, .1, 5000);
    camera.up.set(0, 0, 1);                 // Z is up
    camera.position.set(60, -60, 45);

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setSize(c.clientWidth, c.clientHeight);
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.autoClear = false;             // ViewHelper overlays after the main render

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true; controls.dampingFactor = .08;

    const viewHelper = new ViewHelper(camera, canvas);
    const clock = new THREE.Clock();

    Object.assign(this, { scene, camera, renderer, controls, previewGroup, grid, viewHelper, _clock: clock });
    this._stl = new STLLoader();
    this._ray = new THREE.Raycaster();
    this._mouse = new THREE.Vector2();

    canvas.addEventListener('dblclick', () => this.frame());
    this._loop();
  }

  _loop() {
    const tick = () => {
      requestAnimationFrame(tick);
      const dt = this._clock.getDelta();
      if (this.viewHelper.animating) this.viewHelper.update(dt);
      this.controls.update();
      this.renderer.clear();
      this.renderer.render(this.scene, this.camera);
      this.viewHelper.render(this.renderer);
    };
    tick();
  }

  resize() {
    const c = this.canvas.parentElement;
    this.camera.aspect = c.clientWidth / c.clientHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(c.clientWidth, c.clientHeight);
  }

  setGrid(on) { this.grid.visible = on; }

  // Re-target + re-distance the camera to fit the shown geometry WITHOUT moving
  // it: keep the current view direction, just frame the real bounds.
  frame() {
    const box = new THREE.Box3();
    if (this.previewGroup.children.length) box.setFromObject(this.previewGroup);
    else if (this.currentMesh) box.setFromObject(this.currentMesh);
    if (box.isEmpty()) return;
    const ctr = new THREE.Vector3(); box.getCenter(ctr);
    const size = new THREE.Vector3(); box.getSize(size);
    const md = Math.max(size.x, size.y, size.z) || 10, d = md * 2.0;
    let dir = new THREE.Vector3().subVectors(this.camera.position, this.controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(1, -1, 0.8);
    dir.normalize().multiplyScalar(d);
    this.controls.target.copy(ctr);
    this.camera.position.copy(ctr).add(dir);
    this.controls.update();
  }

  _clearPreviewGroup() {
    for (let i = this.previewGroup.children.length - 1; i >= 0; i--) {
      const c = this.previewGroup.children[i]; this.previewGroup.remove(c);
      c.geometry.dispose(); c.material.dispose();
    }
    this.previewGroup.position.set(0, 0, 0);
  }

  _dropMesh() {
    if (this.currentMesh) {
      this.scene.remove(this.currentMesh);
      this.currentMesh.geometry.dispose(); this.currentMesh.material.dispose();
      this.currentMesh = null;
    }
  }

  // Draw one mesh/polyline/point object per drawable preview entry. Returns
  // {order, colors, meshes, size}. The caller supplies:
  //   colorOf(id, order) -> hex   (default: palette by order index)
  //   wireOf(id) -> bool          (default: false)
  //   onEmpty()                   (called when nothing is drawable; e.g. STL fallback)
  renderPreviews(previews, { colorOf, wireOf, onEmpty } = {}) {
    previews = previews || {};
    this._clearPreviewGroup();
    const drawable = e => e && (e.mesh || e.polylines || e.points);
    const order = Object.keys(previews).filter(k => drawable(previews[k]))
      .sort((a, b) => nodeNum(a) - nodeNum(b));   // stable order for colour slots
    if (!order.length) { if (onEmpty) onEmpty(); return { order: [], colors: {}, meshes: {}, size: null }; }
    this._dropMesh();
    const scale = previewsExtent(previews, order);
    const colors = {}, meshes = {};
    for (const id of order) {
      const color = colorOf ? colorOf(id, order) : PALETTE[order.indexOf(id) % PALETTE.length];
      const obj = objFromPreview(previews[id], color, { wireframe: wireOf ? wireOf(id) : false }, scale);
      if (!obj) continue;
      obj.userData.nodeId = id;
      this.previewGroup.add(obj);
      colors[id] = color; meshes[id] = obj;
    }
    const box = new THREE.Box3().setFromObject(this.previewGroup);
    const size = new THREE.Vector3(); box.getSize(size);
    if (!this._framed) { this.frame(); this._framed = true; }
    return { order, colors, meshes, size };
  }

  // Fallback single-STL load (used when an execution produced no per-node mesh).
  loadSTL(url, { onSize } = {}) {
    this._stl.load(url, (geo) => {
      this._dropMesh();
      geo.computeVertexNormals();
      this.currentMesh = new THREE.Mesh(geo,
        new THREE.MeshStandardMaterial({ color: 0xe94560, roughness: .35, metalness: .15, side: THREE.DoubleSide }));
      this.scene.add(this.currentMesh);          // real coords — no recentering
      if (!this._framed) { this.frame(); this._framed = true; }
      if (onSize) {
        geo.computeBoundingBox(); const s = new THREE.Vector3(); geo.boundingBox.getSize(s);
        onSize(s);
      }
    }, undefined, (err) => { if (onSize) onSize(null, err); });
  }

  clear() { this._clearPreviewGroup(); this._dropMesh(); }
  resetFraming() { this._framed = false; }

  // Raycast the previews under a screen point; returns the owning graph node id
  // (obj.userData.nodeId) of the nearest hit, or null. For click-to-select.
  pick(clientX, clientY) {
    const r = this.canvas.getBoundingClientRect();
    this._mouse.x = ((clientX - r.left) / r.width) * 2 - 1;
    this._mouse.y = -((clientY - r.top) / r.height) * 2 + 1;
    this._ray.setFromCamera(this._mouse, this.camera);
    this._ray.params.Line.threshold = 0.5;
    this._ray.params.Points.threshold = 3;
    const hits = this._ray.intersectObjects(this.previewGroup.children, false)
      .filter(h => h.object.visible !== false);   // don't pick hidden (e.g. isolated) shapes
    return (hits.length && hits[0].object.userData.nodeId) || null;
  }
}
