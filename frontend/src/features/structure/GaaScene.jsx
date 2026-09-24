import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

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

export default function GaaScene({ sceneData, hiddenRoles, selectedRole, onPick }) {
  const host = useRef(null);
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
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(4.4, 3.8, 5.1);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.55, 0);
    controls.enableDamping = false;
    controls.minDistance = 3;
    controls.maxDistance = 18;
    controls.update();
    scene.add(new THREE.AmbientLight(0xffffff, 2.0));
    const light = new THREE.DirectionalLight(0xffffff, 2.3);
    light.position.set(3, 7, 5);
    scene.add(light);
    const axes = new THREE.AxesHelper(1.2);
    axes.position.set(-2.5, -0.3, 1.5);
    scene.add(axes);
    const geometry = new THREE.BoxGeometry(1, 1, 1);
    const profileGeometries = new Map();
    const meshes = [];
    const materials = [];
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const draw = () => renderer.render(scene, camera);
    controls.addEventListener("change", draw);
    const resize = () => {
      const width = Math.max(1, element.clientWidth);
      const height = Math.max(1, element.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
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
    state.current = { scene, camera, controls, renderer, geometry, profileGeometries,
      meshes, materials, draw, lastSceneData: null };
    resize();
    return () => {
      state.current = null;
      observer.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      meshes.forEach((mesh) => scene.remove(mesh));
      materials.forEach((material) => material.dispose());
      geometry.dispose();
      profileGeometries.forEach((item) => item.dispose());
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  useEffect(() => {
    const view = state.current;
    if (!view) return;
    view.meshes.forEach((mesh) => view.scene.remove(mesh));
    view.materials.forEach((material) => material.dispose());
    view.meshes.length = 0;
    view.materials.length = 0;
    for (const part of sceneData?.parts || []) {
      if (hiddenRoles.includes(part.role)) continue;
      const material = new THREE.MeshStandardMaterial({
        color: part.color,
        transparent: part.opacity < 1,
        opacity: part.opacity,
        roughness: 0.48,
        metalness: part.role === "gate" || part.role === "contact" || part.role === "bitline" ? 0.35 : 0.08,
        emissive: part.role === selectedRole || part.anchor?.status === "matched" ? new THREE.Color(part.color) : new THREE.Color(0x000000),
        emissiveIntensity: part.role === selectedRole ? 0.32 : part.anchor?.status === "matched" ? 0.1 : 0,
      });
      let partGeometry = view.geometry;
      if (part.shape === "profile_box" && Array.isArray(part.profile_widths) && part.profile_widths.length === 3) {
        const key = part.profile_widths.join(",");
        if (!view.profileGeometries.has(key)) {
          view.profileGeometries.set(key, profileBoxGeometry(part.profile_widths));
        }
        partGeometry = view.profileGeometries.get(key);
      }
      const mesh = new THREE.Mesh(partGeometry, material);
      mesh.position.set(...part.center);
      mesh.scale.set(...part.size);
      mesh.userData.role = part.role;
      view.scene.add(mesh);
      view.meshes.push(mesh);
      view.materials.push(material);
    }
    if (sceneData !== view.lastSceneData && sceneData?.parts?.length) {
      const bounds = new THREE.Box3();
      for (const part of sceneData.parts) {
        const center = new THREE.Vector3(...part.center);
        const half = new THREE.Vector3(...part.size).multiplyScalar(0.5);
        bounds.expandByPoint(center.clone().sub(half));
        bounds.expandByPoint(center.add(half));
      }
      const size = bounds.getSize(new THREE.Vector3());
      const center = bounds.getCenter(new THREE.Vector3());
      const tangent = Math.tan(THREE.MathUtils.degToRad(view.camera.fov / 2));
      const aspect = Math.max(1, view.camera.aspect);
      const distance = Math.max(5.5, 1.75 * Math.max(size.y / (2 * tangent),
        Math.max(size.x, size.z) / (2 * tangent * aspect)));
      view.controls.target.copy(center);
      view.camera.position.copy(center).addScaledVector(new THREE.Vector3(1, 0.75, 1.1).normalize(), distance);
      view.controls.minDistance = Math.max(2, distance * 0.42);
      view.controls.maxDistance = Math.max(18, distance * 2.5);
      view.controls.update();
      view.lastSceneData = sceneData;
    }
    view.draw();
  }, [sceneData, hiddenRoles, selectedRole]);

  return <div className="gaa-canvas" ref={host} aria-label="GAA 3D 개념 모델. 마우스로 회전하거나 확대하고 구조를 클릭해 선택하세요.">
    {failure && <div className="gaa-canvas-fallback">{failure}</div>}
  </div>;
}
