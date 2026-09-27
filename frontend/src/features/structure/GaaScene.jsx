import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { facetedEpiGeometry, gateShellGeometry } from "./processGeometry";

// Studio-clay look: soft matte solids on a light cyclorama, soft shadows and
// ambient occlusion once the view settles. Hue stays the backend part colour so
// the side legend keeps matching; only how light interacts with it changes:
// metals keep a satin sheen, everything else is matte clay with a soft sheen.
const METALS = new Set(["gate", "inner_gate", "contact", "mol", "beol", "bitline", "well_tap"]);
const DIELECTRICS = new Set(["spacer", "field", "sdb", "rx", "highk"]);
const CLAY = { sheen: 0.35, sheenRoughness: 0.8, sheenColor: 0xffffff };
const SURFACE = {
  metal: { metalness: 0.35, roughness: 0.36, envMapIntensity: 1.0 },
  silicon: { metalness: 0.0, roughness: 0.5, clearcoat: 0.15, ...CLAY },
  epi: { metalness: 0.0, roughness: 0.55, ...CLAY },
  dielectric: { metalness: 0.0, roughness: 0.32, clearcoat: 0.2, ...CLAY },
  substrate: { metalness: 0.0, roughness: 0.72, ...CLAY },
  well: { metalness: 0.0, roughness: 0.66, ...CLAY },
};
const STUDIO_BACKGROUND = 0xeef1f5;
// Ambient occlusion is rendered only after the camera stops moving.
const AO_SETTLE_MS = 160;

function surfaceFor(role) {
  if (METALS.has(role)) return SURFACE.metal;
  if (DIELECTRICS.has(role)) return SURFACE.dielectric;
  if (role === "channel") return SURFACE.silicon;
  if (role === "source" || role === "drain") return SURFACE.epi;
  if (role === "substrate") return SURFACE.substrate;
  return SURFACE.well;
}

function profileBoxGeometry([bottom, middle, top]) {
  const vertices = [];
  const quad = (a, b, c, d) => vertices.push(...a, ...b, ...c, ...a, ...c, ...d);
  const levels = [[bottom, -0.5], [middle, 0], [top, 0.5]];
  for (let i = 0; i < 2; i += 1) {
    const [lowerWidth, lowerY] = levels[i];
    const [upperWidth, upperY] = levels[i + 1];
    quad([-lowerWidth / 2, lowerY, 0.5], [lowerWidth / 2, lowerY, 0.5],
      [upperWidth / 2, upperY, 0.5], [-upperWidth / 2, upperY, 0.5]);
    quad([lowerWidth / 2, lowerY, -0.5], [-lowerWidth / 2, lowerY, -0.5],
      [-upperWidth / 2, upperY, -0.5], [upperWidth / 2, upperY, -0.5]);
    quad([-lowerWidth / 2, lowerY, -0.5], [-lowerWidth / 2, lowerY, 0.5],
      [-upperWidth / 2, upperY, 0.5], [-upperWidth / 2, upperY, -0.5]);
    quad([lowerWidth / 2, lowerY, 0.5], [lowerWidth / 2, lowerY, -0.5],
      [upperWidth / 2, upperY, -0.5], [upperWidth / 2, upperY, 0.5]);
  }
  quad([-bottom / 2, -0.5, -0.5], [bottom / 2, -0.5, -0.5],
    [bottom / 2, -0.5, 0.5], [-bottom / 2, -0.5, 0.5]);
  quad([-top / 2, 0.5, 0.5], [top / 2, 0.5, 0.5],
    [top / 2, 0.5, -0.5], [-top / 2, 0.5, -0.5]);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  geometry.computeVertexNormals();
  return geometry;
}

// Etched contacts/vias use a radial TCD/MCD/BCD profile.
function profileCylinderGeometry([bottom, middle, top], segments) {
  return new THREE.LatheGeometry([
    new THREE.Vector2(0, -0.5),
    new THREE.Vector2(bottom / 2, -0.5),
    new THREE.Vector2(middle / 2, 0),
    new THREE.Vector2(top / 2, 0.5),
    new THREE.Vector2(0, 0.5),
  ], segments);
}

function roundedRadius(role, size) {
  const smallest = Math.min(...size);
  // Nanosheets are thin plates with rounded edges; other blocks get a small bevel.
  return role === "channel" ? smallest * 0.45 : Math.min(0.035, smallest * 0.12);
}

// Measured once per session: a slow first frame (integrated GPU, remote desktop)
// turns shadows off so rotating the model stays smooth.
const SLOW_FRAME_MS = 40;

const NO_ROLES = [];
// Inline parameter overlay colours: connected, anchor not found, not connected.
const INLINE_STATUS = {
  matched: { line: "#2e8540", text: "#9be3ad" },
  missing: { line: "#c0392b", text: "#ffb4a8" },
  unlinked: { line: "#6b7280", text: "#d5dce6" },
};

export default function GaaScene({ sceneData, hiddenRoles, selectedRole, onPick, previewRoles = NO_ROLES,
  cameraPreset = "isometric", latchHighlight = false, cutAxis = "z", cutPosition = 50,
  showLabels = false, showSubLabels = false, showInline = false, showEdges = true, resetToken = 0, guardRing = false }) {
  const host = useRef(null);
  const axisHost = useRef(null);
  const state = useRef(null);
  const onPickRef = useRef(onPick);
  const [failure, setFailure] = useState("");
  onPickRef.current = onPick;

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
    } catch {
      setFailure("이 환경에서 WebGL을 사용할 수 없습니다. 아래 구조 목록으로 확인하세요.");
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.NeutralToneMapping;
    renderer.toneMappingExposure = 1.0;
    renderer.localClippingEnabled = true;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.VSMShadowMap;
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(STUDIO_BACKGROUND);
    // Studio reflections for metals, computed once (a few ms) and reused.
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = environment;
    scene.environmentIntensity = 0.6;
    pmrem.dispose();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(4.4, 3.8, 5.1);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.55, 0);
    controls.enableDamping = false;
    controls.minDistance = 3;
    controls.maxDistance = 18;
    controls.update();
    scene.add(new THREE.HemisphereLight(0xffffff, 0xc3c9d3, 1.25));
    const key = new THREE.DirectionalLight(0xfff6ee, 2.5);
    key.position.set(3.5, 7, 4.5);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.02;
    key.shadow.radius = 10;
    key.shadow.blurSamples = 16;
    scene.add(key, key.target);
    const fill = new THREE.DirectionalLight(0xdfe8ff, 0.45);
    fill.position.set(-4, 3, -3);
    scene.add(fill);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShadowMaterial({ opacity: 0.2 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);
    const cylinderGeometry = new THREE.CylinderGeometry(0.5, 0.5, 1, 24);
    const unitBox = new THREE.BoxGeometry(1, 1, 1);
    const geometries = new Map();
    const meshes = [];
    const materials = [];
    const overlays = [];
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const ambientOcclusion = new GTAOPass(scene, camera, 1, 1);
    ambientOcclusion.updateGtaoMaterial({ radius: 0.35, distanceExponent: 1.4, thickness: 1.2, scale: 1.2, samples: 12 });
    composer.addPass(ambientOcclusion);
    composer.addPass(new OutputPass());
    let aoEnabled = true;
    let aoTimer = 0;
    let slowChecked = false;
    const draw = () => {
      renderer.render(scene, camera);
      window.clearTimeout(aoTimer);
      if (aoEnabled) aoTimer = window.setTimeout(() => composer.render(), AO_SETTLE_MS);
      const inverse = camera.quaternion.clone().invert();
      [[1, 0, 0], [0, 1, 0], [0, 0, 1]].forEach((direction, index) => {
        const vector = new THREE.Vector3(...direction).applyQuaternion(inverse);
        const group = axisHost.current?.children[index];
        if (!group) return;
        group.children[0].setAttribute("x2", 54 + vector.x * 32);
        group.children[0].setAttribute("y2", 54 - vector.y * 32);
        group.children[1].setAttribute("x", 54 + vector.x * 43);
        group.children[1].setAttribute("y", 58 - vector.y * 43);
      });
      if (!slowChecked && meshes.length) {
        // The first frame compiles shaders; time the second one to the GPU finish.
        slowChecked = true;
        const started = performance.now();
        renderer.render(scene, camera);
        renderer.getContext().finish();
        if (performance.now() - started > SLOW_FRAME_MS) {
          aoEnabled = false;
          window.clearTimeout(aoTimer);
          renderer.shadowMap.enabled = false;
          renderer.setPixelRatio(1);
          materials.forEach((material) => { material.needsUpdate = true; });
          renderer.render(scene, camera);
        }
      }
    };
    controls.addEventListener("change", draw);
    const resize = () => {
      const width = Math.max(1, element.clientWidth);
      const height = Math.max(1, element.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
      composer.setPixelRatio(renderer.getPixelRatio());
      composer.setSize(width, height);
      draw();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    let down = null;
    const pointerDown = (event) => { down = [event.clientX, event.clientY]; };
    const pointerUp = (event) => {
      if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(meshes)[0];
      if (hit) onPickRef.current?.(hit.object.userData.role);
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", pointerUp);
    state.current = { scene, camera, controls, renderer, cylinderGeometry, unitBox, geometries,
      meshes, materials, overlays, draw, ground, key, lastSceneData: null, lastPreset: null, lastReset: null };
    resize();
    return () => {
      state.current = null;
      window.clearTimeout(aoTimer);
      observer.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      meshes.forEach((mesh) => scene.remove(mesh));
      overlays.forEach((item) => { scene.remove(item); item.geometry?.dispose(); item.material.map?.dispose(); item.material.dispose(); });
      materials.forEach((material) => material.dispose());
      cylinderGeometry.dispose();
      unitBox.dispose();
      geometries.forEach((item) => item.dispose());
      ground.geometry.dispose();
      ground.material.dispose();
      environment.dispose();
      ambientOcclusion.dispose();
      composer.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  useEffect(() => {
    const view = state.current;
    if (!view) return;
    view.meshes.forEach((mesh) => view.scene.remove(mesh));
    view.overlays.forEach((item) => { view.scene.remove(item); item.geometry?.dispose(); item.material.map?.dispose(); item.material.dispose(); });
    view.overlays.length = 0;
    view.materials.forEach((material) => material.dispose());
    view.meshes.length = 0;
    view.materials.length = 0;
    const used = new Set();
    const cached = (key, make) => {
      used.add(key);
      if (!view.geometries.has(key)) view.geometries.set(key, make());
      return view.geometries.get(key);
    };
    const cut = cameraPreset === "cut";
    const axisIndex = { x: 0, y: 1, z: 2 }[cutAxis];
    const parts = sceneData?.parts || [];
    const minimum = Math.min(...parts.map((part) => part.center[axisIndex] - part.size[axisIndex] / 2));
    const maximum = Math.max(...parts.map((part) => part.center[axisIndex] + part.size[axisIndex] / 2));
    const normal = new THREE.Vector3();
    normal.setComponent(axisIndex, -1);
    const cutPlane = new THREE.Plane(normal, minimum + (maximum - minimum) * cutPosition / 100);
    for (const part of sceneData?.parts || []) {
      if (hiddenRoles.includes(part.role)) continue;
      if (part.role === "guard_ring" && !guardRing) continue;
      const activePath = latchHighlight && sceneData?.view === "latchup"
        && ["latch_path", "nwell", "pwell", "source", "drain"].includes(part.role);
      const color = part.role === "latch_path" && !latchHighlight ? "#788396" : part.color;
      const transparent = part.opacity < 1;
      // Parts a pending LLM edit would change glow in the accent colour.
      const previewed = previewRoles.includes(part.role);
      const material = new THREE.MeshPhysicalMaterial({
        color: previewed ? new THREE.Color(color).lerp(new THREE.Color("#e25822"), 0.65) : color,
        ...surfaceFor(part.role),
        transparent,
        opacity: part.opacity,
        // Translucent shells (gate, spacers) must not hide what is inside them.
        depthWrite: !transparent || part.opacity >= 0.9,
        emissive: activePath || previewed || part.role === selectedRole || part.anchor?.status === "matched"
          ? new THREE.Color(activePath ? "#ff633d" : previewed ? "#e25822" : part.color) : new THREE.Color(0x000000),
        emissiveIntensity: activePath ? 0.34 : previewed ? 0.3 : part.role === selectedRole ? 0.3 : part.anchor?.status === "matched" ? 0.1 : 0,
        clippingPlanes: cut ? [cutPlane] : [],
        side: cut ? THREE.DoubleSide : THREE.FrontSide,
      });
      let geometry;
      let scaled = true;
      if (["faceted_epi", "gate_shell"].includes(part.shape)) {
        geometry = cached(`${part.shape}:${JSON.stringify([part.size, part.profile_widths, part.metadata])}`,
          () => part.shape === "faceted_epi" ? facetedEpiGeometry(part) : gateShellGeometry(part));
        scaled = false;
      } else if (["profile_box", "tapered_cylinder"].includes(part.shape)
          && Array.isArray(part.profile_widths) && part.profile_widths.length === 3) {
        const segments = 32;
        geometry = cached(`${part.shape}:${segments}:${part.profile_widths.join(",")}`, () => (
          part.shape === "tapered_cylinder"
            ? profileCylinderGeometry(part.profile_widths, segments)
            : profileBoxGeometry(part.profile_widths)));
      } else if (part.shape === "cylinder") {
        geometry = view.cylinderGeometry;
      } else if (part.size.every((value) => value > 0)) {
        // Rounded blocks are built at their real size so bevels stay round.
        const radius = roundedRadius(part.role, part.size);
        geometry = cached(`round:${part.size.join(",")}:${radius.toFixed(4)}`,
          () => new RoundedBoxGeometry(part.size[0], part.size[1], part.size[2], 2, radius));
        scaled = false;
      } else {
        geometry = view.unitBox;
      }
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(...part.center);
      if (scaled) mesh.scale.set(...part.size);
      mesh.castShadow = !transparent || part.opacity >= 0.8;
      mesh.receiveShadow = part.role === "substrate" || part.role === "rx" || part.role === "field";
      mesh.renderOrder = transparent ? 2 : 1;
      mesh.userData.role = part.role;
      view.scene.add(mesh);
      view.meshes.push(mesh);
      view.materials.push(material);
      if (showEdges && !["substrate", "field", "rx", "latch_path"].includes(part.role)) {
        const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 25),
          new THREE.LineBasicMaterial({ color: part.role === selectedRole ? "#ffffff" : "#172435",
            transparent: true, opacity: 0.22, clippingPlanes: cut ? [cutPlane] : [] }));
        edges.position.copy(mesh.position);
        edges.scale.copy(mesh.scale);
        view.scene.add(edges);
        view.overlays.push(edges);
      }
    }
    const addLabel = ({ text, position, color, anchor, align, leaderColor }) => {
      if (cut && cutPlane.distanceToPoint(new THREE.Vector3(...position)) < 0) return;
      const canvas = document.createElement("canvas");
      canvas.width = 512; canvas.height = 48;
      const context = canvas.getContext("2d");
      context.font = "600 26px sans-serif";
      const width = Math.min(1000, context.measureText(text).width + 24);
      canvas.width = Math.ceil(width);
      context.font = "600 26px sans-serif";
      context.fillStyle = "rgba(12,22,36,.85)";
      context.fillRect(0, 0, canvas.width, 48);
      context.fillStyle = color || "#e9f2ff";
      context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText(text, canvas.width / 2, 24, canvas.width - 12);
      const texture = new THREE.CanvasTexture(canvas);
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, sizeAttenuation: false }));
      sprite.position.set(...position);
      sprite.scale.set(0.035 * canvas.width / 48, 0.035, 1);
      if (align === "right") sprite.center.set(1, 0.5);
      else if (align === "left") sprite.center.set(0, 0.5);
      sprite.renderOrder = 8;
      view.scene.add(sprite); view.overlays.push(sprite);
      if (anchor) {
        const leader = new THREE.Line(new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(...anchor), new THREE.Vector3(...position)]),
        // Leaders sit on the light studio background, so the default is dark ink.
        new THREE.LineBasicMaterial({ color: leaderColor || "#4b5563", transparent: true, opacity: 0.75 }));
        view.scene.add(leader); view.overlays.push(leader);
      }
    };
    const addLine = (points, color) => {
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points.map((point) => new THREE.Vector3(...point))),
        new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.95 }));
      line.renderOrder = 7;
      view.scene.add(line); view.overlays.push(line);
    };
    if (showLabels) {
      for (const annotation of sceneData?.annotations || []) {
        if (annotation.role && hiddenRoles.includes(annotation.role)) continue;
        if (sceneData?.type === "sram" && sceneData?.view === "gaa"
            && annotation.role === "channel" && !hiddenRoles.includes("beol")) continue;
        if (annotation.role === "guard_ring" && !guardRing) continue;
        addLabel({ ...annotation, leaderColor: annotation.color });
      }
    }
    if (showSubLabels) {
      for (const annotation of sceneData?.sub_annotations || []) {
        if (annotation.role && hiddenRoles.includes(annotation.role)) continue;
        addLabel(annotation);
      }
    }
    if (showInline) {
      for (const marker of sceneData?.inline_markers || []) {
        const palette = INLINE_STATUS[marker.status] || INLINE_STATUS.unlinked;
        if (marker.kind === "dimension") {
          const [x, low, z] = marker.from;
          const high = marker.to[1];
          addLine([marker.from, marker.to], palette.line);
          addLine([[x - 0.06, low, z], [x + 0.06, low, z]], palette.line);
          addLine([[x - 0.06, high, z], [x + 0.06, high, z]], palette.line);
          addLabel({ text: marker.text, position: marker.position, color: palette.text, align: marker.align });
        } else if (!hiddenRoles.includes(marker.role)) {
          addLabel({ text: marker.text, position: marker.position, color: palette.text, align: marker.align,
            anchor: marker.point, leaderColor: palette.line });
        }
      }
    }
    // Slider edits create new sizes every step; drop geometries no longer shown.
    for (const [key, geometry] of view.geometries) {
      if (!used.has(key)) { geometry.dispose(); view.geometries.delete(key); }
    }
    if (sceneData?.parts?.length) {
      const bounds = new THREE.Box3();
      for (const part of sceneData.parts) {
        if (hiddenRoles.includes(part.role) || (part.role === "guard_ring" && !guardRing)) continue;
        const center = new THREE.Vector3(...part.center);
        const half = new THREE.Vector3(...part.size).multiplyScalar(0.5);
        bounds.expandByPoint(center.clone().sub(half));
        bounds.expandByPoint(center.add(half));
      }
      if (bounds.isEmpty()) { view.draw(); return; }
      const size = bounds.getSize(new THREE.Vector3());
      const center = bounds.getCenter(new THREE.Vector3());
      // Shadow catcher just under the model; the light frustum hugs the model.
      view.ground.position.set(center.x, bounds.min.y - 0.002, center.z);
      view.ground.scale.set(size.x * 3 + 2, size.z * 3 + 2, 1);
      const reach = Math.max(size.x, size.y, size.z) * 1.1 + 0.5;
      Object.assign(view.key.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 0.1, far: 30 });
      view.key.shadow.camera.updateProjectionMatrix();
      view.key.target.position.copy(center);
      view.key.position.copy(center).add(new THREE.Vector3(3.5, 7, 4.5));
      if (`${sceneData?.type}/${sceneData?.variant}/${sceneData?.view}` !== view.lastSceneData || cameraPreset !== view.lastPreset || resetToken !== view.lastReset) {
        const tangent = Math.tan(THREE.MathUtils.degToRad(view.camera.fov / 2));
        const aspect = Math.max(0.3, view.camera.aspect);
        view.controls.target.copy(center);
        view.camera.up.set(0, cameraPreset === "top" ? 0 : 1, cameraPreset === "top" ? -1 : 0);
        const direction = cameraPreset === "top" ? new THREE.Vector3(0, 1, 0.001)
          : cameraPreset === "front" ? new THREE.Vector3(0, 0, 1)
          : cameraPreset === "side" ? new THREE.Vector3(1, 0, 0)
          : cameraPreset === "cut" ? new THREE.Vector3(0.35, 0.22, 1)
            : new THREE.Vector3(1, 0.75, 1.1);
        direction.normalize();
        const right = new THREE.Vector3().crossVectors(view.camera.up, direction).normalize();
        const up = new THREE.Vector3().crossVectors(direction, right).normalize();
        const projected = (vector) => Math.abs(vector.x) * size.x + Math.abs(vector.y) * size.y + Math.abs(vector.z) * size.z;
        const distance = Math.max(3, 1.12 * Math.max(projected(up) / (2 * tangent), projected(right) / (2 * tangent * aspect)) + projected(direction) / 2);
        view.camera.position.copy(center).addScaledVector(direction.normalize(), distance);
        view.controls.minDistance = Math.max(2, distance * 0.42);
        view.controls.maxDistance = Math.max(18, distance * 2.5);
        view.controls.update();
        view.lastSceneData = `${sceneData?.type}/${sceneData?.variant}/${sceneData?.view}`;
        view.lastPreset = cameraPreset;
        view.lastReset = resetToken;
      }
    }
    view.draw();
  }, [sceneData, hiddenRoles, selectedRole, previewRoles, cameraPreset, latchHighlight, cutAxis, cutPosition, showLabels, showSubLabels, showInline, showEdges, resetToken, guardRing]);

  useEffect(() => {
    if (!latchHighlight || sceneData?.view !== "latchup") return;
    let bright = false;
    const timer = window.setInterval(() => {
      const view = state.current;
      if (!view) return;
      view.meshes.forEach((mesh) => {
        if (mesh.userData.role === "latch_path") mesh.material.emissiveIntensity = bright ? 0.72 : 0.28;
      });
      bright = !bright;
      view.draw();
    }, 450);
    return () => window.clearInterval(timer);
  }, [latchHighlight, sceneData?.view]);

  return <div className="gaa-canvas" ref={host} aria-label="GAA 3D 개념 모델. 마우스로 회전하거나 확대하고 구조를 클릭해 선택하세요.">
    <svg className="gaa-axis-gizmo" viewBox="0 0 108 108" aria-label="X 소스 드레인, Y 높이, Z 폭 방향" ref={axisHost}>
      {[["X", "#ff918b"], ["Y", "#9ddd8f"], ["Z", "#82c7ff"]].map(([name, color]) =>
        <g key={name} stroke={color} fill={color}><line x1="54" y1="54" x2="54" y2="54" strokeWidth="2"/><text x="54" y="54" stroke="none" textAnchor="middle">{name}</text></g>)}
    </svg>
    {failure && <div className="gaa-canvas-fallback">{failure}</div>}
  </div>;
}
