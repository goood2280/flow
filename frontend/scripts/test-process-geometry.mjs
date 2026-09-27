import assert from 'node:assert/strict';
import * as THREE from 'three';
import { facetedEpiGeometry, gateShellGeometry } from '../src/features/structure/processGeometry.js';

const gate = gateShellGeometry({ size: [0.9, 1.5, 1.4], profile_widths: [0.85, 0.93, 1],
  metadata: { sheet_holes: [{ y: 0.43, width: 1, thickness: 0.125 }], base_y: 0 } });
const mesh = new THREE.Mesh(gate, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
mesh.updateMatrixWorld();
const ray = new THREE.Raycaster(new THREE.Vector3(-2, 0.43 - 0.75, 0), new THREE.Vector3(1, 0, 0));
assert.equal(ray.intersectObject(mesh).length, 0, 'Metal must leave the nanosheet channel opening clear');
ray.ray.origin.y += 0.18;
assert.ok(ray.intersectObject(mesh).length > 0, 'Metal must wrap above the sheet');
ray.ray.origin.set(-2, 0.43 - 0.75, 0.65);
assert.ok(ray.intersectObject(mesh).length > 0, 'Metal must wrap the sheet side');

for (const depth of [0.8, 1.28, 2.28]) {
  const epi = facetedEpiGeometry({ size: [0.65, 1.8, depth], metadata: { facet_angle_deg: 54.7356, cap_height: 0.3 } });
  const normals = epi.attributes.normal;
  const slopes = [];
  for (let i = 0; i < normals.count; i += 1) {
    const y = Math.abs(normals.getY(i)), z = Math.abs(normals.getZ(i));
    if (normals.getY(i) > 0.01 && z > 0.01 && Math.abs(normals.getX(i)) < 0.01) slopes.push(Math.atan2(z, y) * 180 / Math.PI);
  }
  assert.ok(slopes.some((angle) => Math.abs(angle - 54.7356) < 0.001), 'Facet angle must survive width changes');
  assert.ok([...epi.attributes.position.array].every(Number.isFinite));
  epi.dispose();
}
gate.dispose(); mesh.material.dispose();
console.log('Process geometry: open sheet channels, wrapping metal, and invariant facet angles passed.');
