// The 3D view: chunks into BatchedMeshes, highlighting, hiding, fitting (ADR 14, decision 6).
//
// Each chunk is parsed by GLTFLoader and put into two BatchedMeshes, one opaque and one
// transparent, with one instance per primitive: file B's 12,774 primitives are then 76
// draw calls, not 14,492 (ADR 14, P6). A map from each GlobalId to its instances lets a
// selection light an element, and an aggregate through its parts. A chunk holds one
// storey's elements, so a storey is hidden by hiding its chunks' group.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const BACKGROUND = 0xeef0f3;
/** A clicked element: the page's teal, which no group of an answer uses (selection.py). */
export const ACCENT = '#0e8192';
const GHOST = new THREE.Color(0xd5d9de);
const SPACE_ALPHA = 0.25;
const TINT_WEIGHT = 0.6;
const CLICK_SLOP_PX = 5;

/** The tints of the disciplines that have one; ARC, and any other, keep their materials' colours. */
export const TINTS = { MEP: '#2f7ed8', ELE: '#e0a100', PLU: '#16a085' };

const elementKey = (modelId, globalId) => `${modelId}/${globalId}`;

export class SceneView extends EventTarget {
  /** @param {HTMLElement} host */
  constructor(host) {
    super();
    this.host = host;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(BACKGROUND);
    host.append(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8d8d8d, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(0.5, 1, 0.8);
    this.scene.add(sun);
    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
    this.camera.position.set(20, 15, 20);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.zoomToCursor = true;
    this.controls.addEventListener('change', () => this.requestRender());
    this.controls.addEventListener('start', () => {
      this.moved = true;
    });

    // No normals in the chunks: flat shading computes them per face.
    const material = { flatShading: true, side: THREE.DoubleSide };
    this.materials = {
      opaque: new THREE.MeshLambertMaterial(material),
      transparent: new THREE.MeshLambertMaterial({ ...material, transparent: true, depthWrite: false }),
    };
    this.loader = new GLTFLoader();
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.reset();

    const canvas = this.renderer.domElement;
    canvas.addEventListener('pointerdown', (event) => {
      this.down = { x: event.clientX, y: event.clientY };
    });
    canvas.addEventListener('pointerup', (event) => {
      const down = this.down;
      this.down = null;
      if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > CLICK_SLOP_PX) return;
      this.dispatchEvent(new CustomEvent('pick', { detail: this.pick(event.clientX, event.clientY) }));
    });
    new ResizeObserver(() => this.resize()).observe(host);
    this.resize();
  }

  reset() {
    this.models = new Map(); // model id → { group, tint }
    this.chunks = []; // { modelId, kind, storey, group, batches, instances }
    this.elements = new Map(); // "model id/GlobalId" → [{ batch, id }]
    this.classes = new Map(); // "model id/GlobalId" → its IFC class, from the node's extras
    this.selected = [];
    this.lit = { drawn: 0, classes: new Set() }; // what the last highlight drew
    this.ghosted = false;
    this.only = false;
    this.moved = false;
  }

  /** Remove every model, and free what the GPU holds of them. */
  clear() {
    for (const chunk of this.chunks) {
      for (const batch of chunk.batches) batch.dispose();
    }
    this.root.clear();
    this.reset();
    this.requestRender();
  }

  /**
   * Add a model's group, moved by `offset`: its origin less the first model's, in the
   * file's coordinates (x, y, z, Z up), which the chunks' Y-up frame turns into (x, z, -y).
   */
  addModel(modelId, discipline, offset) {
    const group = new THREE.Group();
    group.position.set(offset[0], offset[2], -offset[1]);
    this.root.add(group);
    const tint = Object.hasOwn(TINTS, discipline) ? new THREE.Color(TINTS[discipline]) : null;
    this.models.set(modelId, { group, tint });
  }

  /** Parse one chunk's GLB and add it to its model. Returns the chunk's record. */
  async addChunk(modelId, chunk, buffer) {
    const model = this.models.get(modelId);
    const gltf = await this.loader.parseAsync(buffer, '');
    gltf.scene.updateMatrixWorld(true);
    const space = chunk.kind !== 'elements';
    const parts = { opaque: [], transparent: [] };
    const materials = new Set();
    gltf.scene.traverse((object) => {
      if (!object.isMesh) return;
      // The element nodes above the primitive, innermost first: an aggregate's parts are
      // its children. GLTFLoader keeps a node's own name in userData.name.
      const owners = [];
      for (let node = object; node; node = node.parent) {
        if (node.userData.ifc_class === undefined) continue;
        owners.push(node.userData.name);
        this.classes.set(elementKey(modelId, node.userData.name), String(node.userData.ifc_class));
      }
      if (owners.length === 0) return;
      materials.add(object.material);
      const transparent = space || object.material.transparent || object.material.opacity < 1;
      parts[transparent ? 'transparent' : 'opaque'].push({ object, owners });
    });

    const record = { modelId, kind: chunk.kind, storey: chunk.storey_name ?? '', group: new THREE.Group(), batches: [], instances: 0 };
    for (const [kind, meshes] of Object.entries(parts)) {
      if (meshes.length === 0) continue;
      let vertices = 0;
      let indices = 0;
      for (const { object } of meshes) {
        vertices += object.geometry.attributes.position.count;
        indices += object.geometry.index.count;
      }
      const batch = new THREE.BatchedMesh(meshes.length, vertices, indices, this.materials[kind]);
      batch.userData.owners = [];
      batch.userData.colours = [];
      for (const { object, owners } of meshes) {
        const id = batch.addInstance(batch.addGeometry(object.geometry));
        batch.setMatrixAt(id, object.matrixWorld);
        const colour = colourOf(object.material, model.tint, space);
        batch.setColorAt(id, colour);
        batch.userData.colours[id] = colour;
        batch.userData.owners[id] = { modelId, globalId: owners[0] };
        for (const globalId of owners) {
          const key = elementKey(modelId, globalId);
          if (!this.elements.has(key)) this.elements.set(key, []);
          this.elements.get(key).push({ batch, id });
        }
        object.geometry.dispose();
      }
      batch.computeBoundingBox();
      batch.computeBoundingSphere();
      record.group.add(batch);
      record.batches.push(batch);
      record.instances += meshes.length;
    }
    for (const material of materials) material.dispose();
    model.group.add(record.group);
    this.chunks.push(record);
    this.requestRender();
    return record;
  }

  /** Show the chunks for which `visible(chunk)` is true, and hide the others. */
  applyVisibility(visible) {
    for (const chunk of this.chunks) chunk.group.visible = visible(chunk);
    this.requestRender();
  }

  /** The element under a point of the window, as { modelId, globalId }, or null. */
  pick(clientX, clientY) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    // The raycaster tests hidden objects too: only the visible batches are given to it.
    const batches = [];
    this.root.traverseVisible((object) => {
      if (object.isBatchedMesh) batches.push(object);
    });
    const [hit] = this.raycaster.intersectObjects(batches, false);
    return hit ? hit.object.userData.owners[hit.batchId] : null;
  }

  /** Light the elements of `keys` ([model id, GlobalId] pairs) in the accent colour: a click. */
  select(keys) {
    return this.highlight(keys.length ? [{ colour: ACCENT, keys }] : []);
  }

  /**
   * Light each group of `groups` ([{colour, keys: [[model id, GlobalId], …]}]) in its
   * colour. With `ghost`, everything else is drawn opaque light grey, so the selection
   * stands out without see-through walls; without it, the rest keeps its colours.
   * Returns how many of the keys are drawn: an aggregate counts once, through its parts.
   * `lit` keeps that count and the drawn objects' classes, for the answer's wording.
   */
  highlight(groups, ghost = false) {
    this.restore();
    const colour = new THREE.Vector4();
    let drawn = 0;
    const classes = new Set();
    for (const group of groups) {
      const tint = new THREE.Color(group.colour);
      for (const [modelId, globalId] of group.keys) {
        const key = elementKey(modelId, globalId);
        const instances = this.elements.get(key);
        if (!instances) continue;
        drawn += 1;
        if (this.classes.has(key)) classes.add(this.classes.get(key));
        for (const instance of instances) {
          const { batch, id } = instance;
          colour.set(tint.r, tint.g, tint.b, Math.max(batch.userData.colours[id].w, 0.85));
          batch.setColorAt(id, colour);
          this.selected.push(instance);
        }
      }
    }
    if (ghost) {
      this.ghosted = true;
      const keep = new Set(this.selected.map(({ batch, id }) => `${batch.id}:${id}`));
      this.eachInstance((batch, id) => {
        if (keep.has(`${batch.id}:${id}`)) return;
        colour.set(GHOST.r, GHOST.g, GHOST.b, batch.userData.colours[id].w);
        batch.setColorAt(id, colour);
      });
    }
    this.showOnly(this.only && this.selected.length > 0);
    this.lit = { drawn, classes };
    return drawn;
  }

  /** Give every instance lit or greyed its own colour back. */
  restore() {
    if (this.ghosted) {
      this.eachInstance((batch, id) => batch.setColorAt(id, batch.userData.colours[id]));
    } else {
      for (const { batch, id } of this.selected) batch.setColorAt(id, batch.userData.colours[id]);
    }
    this.selected = [];
    this.ghosted = false;
    this.requestRender();
  }

  eachInstance(visit) {
    for (const chunk of this.chunks) {
      for (const batch of chunk.batches) {
        for (let id = 0; id < batch.userData.owners.length; id++) visit(batch, id);
      }
    }
  }

  /** Hide everything but the selection, or show everything again. */
  showOnly(only) {
    this.only = only;
    const keep = new Set(this.selected.map(({ batch, id }) => `${batch.id}:${id}`));
    for (const chunk of this.chunks) {
      for (const batch of chunk.batches) {
        for (let id = 0; id < batch.userData.owners.length; id++) {
          batch.setVisibleAt(id, !only || keep.has(`${batch.id}:${id}`));
        }
      }
    }
    this.requestRender();
  }

  /** Frame what is shown. */
  fit() {
    const box = new THREE.Box3();
    this.root.updateMatrixWorld(true);
    this.root.traverseVisible((object) => {
      if (object.isBatchedMesh) box.expandByObject(object);
    });
    this.frame(box);
  }

  /** Frame the selection, at least `margin` metres around it. */
  fitSelection(margin = 0.5) {
    const box = new THREE.Box3();
    const part = new THREE.Box3();
    const matrix = new THREE.Matrix4();
    this.root.updateMatrixWorld(true);
    for (const { batch, id } of this.selected) {
      batch.getBoundingBoxAt(batch.getGeometryIdAt(id), part);
      part.applyMatrix4(batch.getMatrixAt(id, matrix)).applyMatrix4(batch.matrixWorld);
      box.union(part);
    }
    this.frame(box, margin);
  }

  frame(box, margin = 0.5) {
    if (box.isEmpty()) return;
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, margin);
    const direction = this.camera.position.clone().sub(this.controls.target);
    if (direction.lengthSq() === 0) direction.set(1, 0.75, 1);
    direction.normalize();
    // The narrower of the two angles of view: on a phone held upright, the horizontal one.
    const vertical = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const horizontal = Math.atan(Math.tan(vertical) * this.camera.aspect);
    const distance = (radius / Math.sin(Math.min(vertical, horizontal))) * 1.05;
    this.camera.position.copy(centre).addScaledVector(direction, distance);
    this.camera.near = Math.max(distance / 1000, 0.01);
    this.camera.far = distance + radius * 20;
    this.camera.updateProjectionMatrix();
    this.controls.maxDistance = radius * 20;
    this.controls.target.copy(centre);
    this.controls.update();
    this.requestRender();
  }

  resize() {
    const width = Math.max(this.host.clientWidth, 1);
    const height = Math.max(this.host.clientHeight, 1);
    this.renderer.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.requestRender();
  }

  /** Draw once on the next frame: the view renders only when something changed. */
  requestRender() {
    if (this.pending) return;
    this.pending = true;
    requestAnimationFrame(() => {
      this.pending = false;
      this.renderer.render(this.scene, this.camera);
    });
  }

  /** What the view holds and draws now, for the status line and the measurements. */
  stats() {
    this.renderer.render(this.scene, this.camera);
    let instances = 0;
    let vertices = 0;
    let bytes = 0;
    for (const chunk of this.chunks) {
      for (const batch of chunk.batches) {
        instances += batch.userData.owners.length;
        vertices += batch.geometry.attributes.position.count;
        bytes += batch.geometry.attributes.position.array.byteLength + batch.geometry.index.array.byteLength;
      }
    }
    const gl = this.renderer.getContext();
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      three: `r${THREE.REVISION}`,
      chunks: this.chunks.length,
      instances,
      vertices,
      geometry_mb: Math.round(bytes / 1e5) / 10,
      draw_calls: this.renderer.info.render.calls,
      triangles: this.renderer.info.render.triangles,
      js_heap_mb: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 2 ** 20) : null,
      canvas: `${this.renderer.domElement.width}x${this.renderer.domElement.height}`,
      gpu: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null,
    };
  }

  /**
   * Orbit the camera around its target for `seconds`, drawing every frame, and report the
   * frames per second with stats(). The camera goes back where it was.
   */
  async measure(seconds = 10) {
    const target = this.controls.target.clone();
    const start = this.camera.position.clone();
    const offset = start.clone().sub(target);
    const radius = Math.hypot(offset.x, offset.z);
    const angle0 = Math.atan2(offset.z, offset.x);
    let frames = 0;
    let worst = 0;
    const began = performance.now();
    let last = began;
    const ended = await new Promise((resolve) => {
      const step = (now) => {
        const angle = angle0 + (now - began) / 4000;
        this.camera.position.set(target.x + radius * Math.cos(angle), start.y, target.z + radius * Math.sin(angle));
        this.camera.lookAt(target);
        this.renderer.render(this.scene, this.camera);
        frames += 1;
        worst = Math.max(worst, now - last);
        last = now;
        if (now - began < seconds * 1000) requestAnimationFrame(step);
        else resolve(now);
      };
      requestAnimationFrame(step);
    });
    this.camera.position.copy(start);
    this.camera.lookAt(target);
    this.controls.update();
    return {
      seconds,
      fps: Math.round(((frames * 1000) / (ended - began)) * 10) / 10,
      worst_frame_ms: Math.round(worst),
      ...this.stats(),
    };
  }
}

/** An instance's colour: its material's, tinted by discipline; rooms see-through. */
function colourOf(material, tint, space) {
  const colour = material.color.clone();
  if (tint) colour.lerp(tint, TINT_WEIGHT);
  const alpha = space ? Math.min(material.opacity, SPACE_ALPHA) : material.opacity;
  return new THREE.Vector4(colour.r, colour.g, colour.b, alpha);
}
