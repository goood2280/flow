// Bakes one home app icon as a light isometric 3D render.
// The shapes come from HomeAppIcons.jsx (served as ./icon-shapes.js by render.py),
// re-implemented here as solids: same world units, same tones, real lighting.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import buildShapes from "./icon-shapes.js";

const q = new URLSearchParams(location.search);
const KEY = q.get("key");
const GROUP = q.get("group") || "work";
const SIZE = Number(q.get("size") || 240);

// Tile families mirror layouts.css: data = steel body + brand accent,
// work = brand body + steel accent, system = graphite body + brand accent.
const STEEL = "#4f75a8", BRAND = "#e8653a", GRAPHITE = "#6b6e75";
const TONES = {
  c: { data: STEEL, work: BRAND, system: GRAPHITE }[GROUP],
  b: { data: BRAND, work: STEEL, system: BRAND }[GROUP],
  p: "#f6f6f4",
  k: "#4b5058",
};
const mix = (a, b, t) => new THREE.Color(a).lerp(new THREE.Color(b), t);
// Face letters (t/l/r) only matter for flat decals, where the SVG used them to
// fake light; solids get their shading from the lights instead.
function toneColor(cls) {
  const m = /hi-([cbpk])([tlr])?/.exec(cls || "");
  if (!m) return new THREE.Color(TONES.c);
  const base = TONES[m[1]];
  if (m[2] === "t") return mix(base, "#ffffff", m[1] === "p" ? 0 : 0.35);
  if (m[2] === "r") return mix(base, "#000000", m[1] === "p" ? 0.2 : 0.22);
  return new THREE.Color(base);
}
const LINE_COLORS = {
  "hi-line": () => mix(TONES.c, "#000000", 0.3),
  "hi-ink": () => new THREE.Color("#393939"),
  "hi-brand-line": () => new THREE.Color(TONES.b),
  "hi-fold": () => new THREE.Color("#c6c6c6"),
};
const isLine = (cls) => Object.keys(LINE_COLORS).some((k) => (cls || "").includes(k));
const lineColor = (cls) => LINE_COLORS[Object.keys(LINE_COLORS).find((k) => cls.includes(k))]();

const materials = new Map();
function material(color) {
  const key = color.getHexString();
  if (!materials.has(key)) {
    materials.set(key, new THREE.MeshPhysicalMaterial({
      color, roughness: 0.36, metalness: 0, clearcoat: 0.55, clearcoatRoughness: 0.2,
    }));
  }
  return materials.get(key);
}

// ---------- scene ----------
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(SIZE, SIZE);
renderer.setClearColor(0x000000, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.VSMShadowMap;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.55;
// Lit from the top-left like the SVG set: tops brightest, front-left faces
// (+Z here) mid, front-right faces (+X) darkest.
scene.add(new THREE.HemisphereLight(0xffffff, 0xb9c0cc, 1.1));
const key = new THREE.DirectionalLight(0xfff6ee, 2.4);
key.position.set(2, 20, 11);
key.castShadow = true;
key.shadow.mapSize.set(1024, 1024);
key.shadow.radius = 10;
key.shadow.blurSamples = 20;
key.shadow.bias = -0.0005;
Object.assign(key.shadow.camera, { left: -20, right: 20, top: 20, bottom: -20, near: 1, far: 60 });
scene.add(key);
const group = new THREE.Group();
scene.add(group);

// World (x → right-down, y → left-down, z → up) maps to three (X = x, Y = z, Z = y);
// an orthographic camera on the (1, 1, 1) diagonal reproduces the SVG projection.
function add(geometry, color, position = [0, 0, 0]) {
  const mesh = new THREE.Mesh(geometry, material(color));
  mesh.position.set(position[0], position[2], position[1]);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  group.add(mesh);
  return mesh;
}
const roundFor = (...sizes) => Math.min(0.9, 0.3 * Math.min(...sizes));
const DECAL = 0.2;
// Decals that share a plane stack in draw order (the SVG painter's order),
// each a hair above the previous one so they never z-fight.
const layers = new Map();
const lift = (plane) => { const n = layers.get(plane) || 0; layers.set(plane, n + 1); return n * 0.08; };

function shapeOf(points) {
  const s = new THREE.Shape();
  points.forEach(([u, v], i) => (i ? s.lineTo(u, v) : s.moveTo(u, v)));
  s.closePath();
  return s;
}
// Extrude an outline drawn in the (x, y) plane upward by h from z.
function extrudeUp(points, z, h, bevel = 0) {
  const g = new THREE.ExtrudeGeometry(shapeOf(points), {
    depth: Math.max(0.01, h - 2 * bevel), bevelEnabled: bevel > 0, bevelSize: bevel, bevelThickness: bevel,
    bevelSegments: 3, curveSegments: 24,
  });
  g.rotateX(Math.PI / 2);                 // (u, v, w) → (u, -w, v)
  g.translate(0, z + h - bevel, 0);
  return g;
}
// Extrude an outline drawn in the (x, z) plane toward the viewer (+y) by d from y.
function extrudeFront(points, y, d, bevel = 0) {
  const g = new THREE.ExtrudeGeometry(shapeOf(points), {
    depth: Math.max(0.01, d - 2 * bevel), bevelEnabled: bevel > 0, bevelSize: bevel, bevelThickness: bevel,
    bevelSegments: 3, curveSegments: 24,
  });
  g.translate(0, 0, y + bevel);
  return g;
}
function tube(worldPoints, width, color) {
  const pts = worldPoints.map(([x, y, z]) => new THREE.Vector3(x, z, y));
  const r = Math.max(0.16, width * 0.42);
  for (let i = 0; i < pts.length - 1; i += 1) {
    const a = pts[i], b = pts[i + 1];
    const len = a.distanceTo(b);
    const g = new THREE.CapsuleGeometry(r, len, 6, 12);
    const m = add(g, color);
    m.position.copy(a).add(b).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
  }
}

// ---------- primitives with the HomeAppIcons.jsx signatures ----------
const P = {};
P.box = (tone, bx, by, bz, bw, bd, bh) => {
  const r = roundFor(bw, bd, bh);
  add(new RoundedBoxGeometry(bw, bh, bd, 3, r), toneColor(`hi-${tone}l`), [bx + bw / 2, by + bd / 2, bz + bh / 2]);
  return [];
};
P.cylinder = (tone, x, y, cz, cr, ch) => {
  const rd = roundFor(cr * 2, ch);
  const prof = [new THREE.Vector2(0, 0)];
  for (let i = 0; i <= 6; i += 1) {
    const a = -Math.PI / 2 + (i / 6) * (Math.PI / 2);
    prof.push(new THREE.Vector2(cr - rd + rd * Math.cos(a), rd + rd * Math.sin(a)));
  }
  for (let i = 0; i <= 6; i += 1) {
    const a = (i / 6) * (Math.PI / 2);
    prof.push(new THREE.Vector2(cr - rd + rd * Math.cos(a), ch - rd + rd * Math.sin(a)));
  }
  prof.push(new THREE.Vector2(0, ch));
  add(new THREE.LatheGeometry(prof, 64), toneColor(`hi-${tone}l`), [x, y, cz]);
  return [];
};
P.sphere = (tone, x, y, z, r) => { add(new THREE.SphereGeometry(r, 48, 32), toneColor(`hi-${tone}l`), [x, y, z]); return []; };
P.prismZ = (tone, outline, pz, ph) => { add(extrudeUp(outline, pz, ph, roundFor(ph) * 0.6), toneColor(`hi-${tone}l`)); return []; };
P.prismY = (tone, outline, py, pd) => { add(extrudeFront(outline, py, pd, roundFor(pd) * 0.6), toneColor(`hi-${tone}l`)); return []; };
P.drum = P.prismY;
P.top = (cls, z, points) => {
  const dz = lift(`t${z}`);
  if (isLine(cls)) tube([...points, points[0]].map(([x, y]) => [x, y, z + DECAL + dz]), 0.5, lineColor(cls));
  else add(extrudeUp(points, z + dz, DECAL), toneColor(cls));
  return {};
};
P.frontLeft = (cls, y, points) => { add(extrudeFront(points, y - 0.02 + lift(`f${y}`), DECAL), toneColor(cls)); return {}; };
P.topLine = (cls, z, points, width = 0.9) => { tube(points.map(([x, y]) => [x, y, z + DECAL]), width, lineColor(cls)); return {}; };
P.frontLeftLine = (cls, y, points, width = 0.9) => { tube(points.map(([x, z]) => [x, y + DECAL, z]), width, lineColor(cls)); return {}; };
P.disc = (cls, x, y, z, r) => {
  if (isLine(cls)) {
    const ring = new THREE.TorusGeometry(r, 0.3, 12, 64);
    ring.rotateX(Math.PI / 2);
    add(ring, lineColor(cls), [x, y, z + DECAL]);
  } else {
    add(new THREE.CylinderGeometry(r, r, DECAL, 64), toneColor(cls), [x, y, z + DECAL / 2]);
  }
  return {};
};
// Screen-space helpers: 2D-only items are rebuilt per icon in OVERRIDES below.
const COS30 = Math.sqrt(3) / 2;
P.project = ([x, y, z]) => [(x - y) * COS30, (x + y) / 2 - z];
P.ellipse = () => ({});
P.polyline = () => ({});
P.polygon = () => ({});
P.poly3 = (cls, points) => { add(extrudeUp(points.map(([x, y]) => [x, y]), points[0][2], DECAL), toneColor(cls)); return {}; };
P.pt = ([x, y]) => `${x} ${y}`;
P.rect = (a, b, w, h) => [[a, b], [a + w, b], [a + w, b + h], [a, b + h]];
P.grid = (count, start, size, gap) => Array.from({ length: count }, (_, i) => start + i * (size + gap));
P.circle = (cx, cy, r, from = 0, to = Math.PI * 2, steps = 40) =>
  Array.from({ length: steps + 1 }, (_, i) => {
    const a = from + ((to - from) * i) / steps;
    return [cx + r * Math.sin(a), cy + r * Math.cos(a)];
  });
P.gear = (teeth, outer, inner) => {
  const points = [];
  const step = (Math.PI * 2) / teeth;
  for (let i = 0; i < teeth; i += 1) {
    const a = i * step;
    for (const [r, da] of [[inner, -0.3], [outer, -0.18], [outer, 0.18], [inner, 0.3]]) {
      points.push([r * Math.cos(a + da * step * 1.6), r * Math.sin(a + da * step * 1.6)]);
    }
  }
  return points;
};

// A screen offset (sx, sy) around a world anchor, kept in the camera-facing plane.
const screenToWorld = ([ax, ay, az], sx, sy) => [ax + sx / (2 * COS30), ay - sx / (2 * COS30), az - sy];

// Icons whose SVG parts are drawn in screen space get explicit solids here.
const OVERRIDES = {
  chartbuilder: (base) => {
    base();
    for (const [x, z] of [[3, 2.4], [4.4, 5.6], [5.9, 3.4], [7.3, 7.6], [8.8, 4.8], [10.3, 9]]) {
      add(new THREE.SphereGeometry(0.75, 24, 16), toneColor("hi-cl"), [x, 0.9 + 0.5, z]);
    }
  },
  lotlocation: (base) => {
    base();
    const [x, y] = [10.1, 2.8];
    add(new THREE.ConeGeometry(2.1, 6.4, 40), toneColor("hi-bl"), [x, y, 6.6]).rotation.x = Math.PI;
    add(new THREE.SphereGeometry(2.9, 40, 28), toneColor("hi-bl"), [x, y, 10.6]);
    add(new THREE.SphereGeometry(1.25, 28, 20), toneColor("hi-pt"), [x + 1.2, y + 1.2, 11.2]);
  },
  valve: (base) => {
    base();
    const first = group.children.length;
    for (const [dx, dy] of [[-1, -0.35], [-0.55, -1], [0.55, -1], [1, -0.35]]) {
      const a = screenToWorld([0, 0, 7.2], dx * 7.6, dy * 5.4);
      const b = screenToWorld([0, 0, 7.2], dx * 9.6, dy * 6.8);
      tube([a, b], 1.2, lineColor("hi-brand-line"));
    }
    // The rays float in the air; their ground shadows would read as stray dashes.
    group.children.slice(first).forEach((mesh) => { mesh.castShadow = false; });
  },
};

const { ICONS, FALLBACK } = buildShapes(P);
const draw = () => (ICONS[KEY] || FALLBACK)();
(OVERRIDES[KEY] || ((base) => base()))(draw);

// ---------- ground shadow + camera fit ----------
const ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), new THREE.ShadowMaterial({ opacity: 0.16 }));
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.01;
ground.receiveShadow = true;
scene.add(ground);

const bounds = new THREE.Box3().setFromObject(group);
const center = bounds.getCenter(new THREE.Vector3());
const dir = new THREE.Vector3(1, 1, 1).normalize();
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 400);
camera.position.copy(center).addScaledVector(dir, 100);
camera.lookAt(center);
camera.updateMatrixWorld();
let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
  const v = new THREE.Vector3(x, y, z).applyMatrix4(camera.matrixWorldInverse);
  minX = Math.min(minX, v.x); maxX = Math.max(maxX, v.x); minY = Math.min(minY, v.y); maxY = Math.max(maxY, v.y);
}
// Same fit rule as the SVG: a square frame around the larger extent, 3% padding each side.
const half = Math.max(maxX - minX, maxY - minY) * 0.53;
const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
Object.assign(camera, { left: cx - half, right: cx + half, top: cy + half, bottom: cy - half });
camera.updateProjectionMatrix();

renderer.render(scene, camera);
window.__done = true;
