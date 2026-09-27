import * as THREE from "three";

// Coordinates in these extrusions are physical scene units, so the facet angle
// survives changes to sheet width and epi height (unlike a scaled cone).
function extrudeAcrossX(shape, width) {
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: width, bevelEnabled: false, steps: 1, curveSegments: 1 });
  geometry.translate(0, 0, -width / 2);
  geometry.rotateY(Math.PI / 2);
  return geometry;
}

export function facetedEpiGeometry(part) {
  const [width, height, depth] = part.size;
  const angle = (part.metadata?.facet_angle_deg ?? 54.7356) * Math.PI / 180;
  const cap = Math.min(part.metadata?.cap_height ?? height * 0.2, height * 0.4, depth * 0.4 * Math.tan(angle));
  const inset = cap / Math.tan(angle);
  const foot = Math.min(height * 0.12, depth * 0.12);
  const outline = new THREE.Shape();
  const points = [
    [-depth / 2 + foot, -height / 2], [depth / 2 - foot, -height / 2],
    [depth / 2, -height / 2 + foot], [depth / 2, height / 2 - cap],
    [depth / 2 - inset, height / 2], [-depth / 2 + inset, height / 2],
    [-depth / 2, height / 2 - cap], [-depth / 2, -height / 2 + foot],
  ];
  points.forEach(([x, y], index) => index ? outline.lineTo(x, y) : outline.moveTo(x, y));
  outline.closePath();
  const geometry = extrudeAcrossX(outline, width);
  // Explicit CD overrides control the etched X profile; the YZ growth facets
  // remain crystallographic and independent of those widths.
  taperX(geometry, height, part.profile_widths);
  return geometry;
}

function taperX(geometry, height, widths = [1, 1, 1]) {
  const positions = geometry.attributes.position;
  for (let i = 0; i < positions.count; i += 1) {
    const t = THREE.MathUtils.clamp(positions.getY(i) / height + 0.5, 0, 1) * 2;
    const lower = t < 1 ? 0 : 1;
    const ratio = THREE.MathUtils.lerp(widths[lower], widths[lower + 1], t - lower);
    positions.setX(i, positions.getX(i) * ratio);
  }
  positions.needsUpdate = true;
  geometry.computeVertexNormals();
}

export function gateShellGeometry(part) {
  const [width, height, depth] = part.size;
  const outline = new THREE.Shape();
  outline.moveTo(-depth / 2, -height / 2);
  outline.lineTo(depth / 2, -height / 2);
  outline.lineTo(depth / 2, height / 2);
  outline.lineTo(-depth / 2, height / 2);
  outline.closePath();
  for (const sheet of part.metadata?.sheet_holes || []) {
    const y = sheet.y - (part.metadata?.base_y ?? 0) - height / 2;
    const clearance = part.metadata?.hole_clearance ?? 0.012;
    const halfWidth = Math.min(sheet.width / 2 + clearance, depth / 2 - 0.001);
    const halfHeight = sheet.thickness / 2 + clearance;
    if (y - halfHeight <= -height / 2 || y + halfHeight >= height / 2) continue;
    const hole = new THREE.Path();
    hole.moveTo(-halfWidth, y - halfHeight);
    hole.lineTo(-halfWidth, y + halfHeight);
    hole.lineTo(halfWidth, y + halfHeight);
    hole.lineTo(halfWidth, y - halfHeight);
    hole.closePath();
    outline.holes.push(hole);
  }
  const geometry = extrudeAcrossX(outline, width);
  taperX(geometry, height, part.profile_widths);
  return geometry;
}
