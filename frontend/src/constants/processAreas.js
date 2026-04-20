/**
 * constants/processAreas.js — Process Area (공정 영역) taxonomy for 2nm GAA Nanosheet logic flow.
 *
 * Source: FabCanvas_domain.txt [6] Process Area Tagging (학계 공개 정보 기반).
 * Used by the matching-table UI (area dropdown + color chip) and by any rollup view
 * that groups SHAP importance / ML features by process region.
 *
 * Keep this list in sync with backend constant `PROCESS_AREAS` in
 * backend/core/domain.py — the API `/api/match/area-rollup` validates against it.
 */

// Canonical ordered list — the UI dropdown and the rollup response honor this order.
export const PROCESS_AREAS = [
  "STI",
  "Well/VT",
  "PC",
  "Gate",
  "Spacer",
  "S/D Epi",
  "MOL",
  "BEOL-M1",
  "BEOL-M2",
  "BEOL-M3",
  "BEOL-M4",
  "BEOL-M5",
  "BEOL-M6",
];

// Fixed colors per area — chip background in matching-table UI and legend swatches.
// Palette chosen to keep FEOL (earth tones) / MOL (amber) / BEOL (blue gradient) visually grouped.
export const PROCESS_AREA_COLORS = {
  "STI":      "#64748b", // slate (isolation)
  "Well/VT":  "#8b5cf6", // violet (implant)
  "PC":       "#f97316", // orange (patterning)
  "Gate":     "#ef4444", // red (HKMG — critical)
  "Spacer":   "#ec4899", // pink
  "S/D Epi":  "#10b981", // green (epi)
  "MOL":      "#f59e0b", // amber (contact)
  "BEOL-M1":  "#0ea5e9",
  "BEOL-M2":  "#2563eb",
  "BEOL-M3":  "#4f46e5",
  "BEOL-M4":  "#7c3aed",
  "BEOL-M5":  "#a21caf",
  "BEOL-M6":  "#be185d",
};

// Short human description (tooltip on dropdown / legend).
export const PROCESS_AREA_DESCRIPTIONS = {
  "STI":      "Shallow Trench Isolation",
  "Well/VT":  "Well & Vth implant",
  "PC":       "Poly / Nanosheet release patterning",
  "Gate":     "HKMG — High-K / Metal Gate",
  "Spacer":   "Gate spacer",
  "S/D Epi":  "Source/Drain Epitaxy",
  "MOL":      "Middle of Line (Contact / Via-to-Gate)",
  "BEOL-M1":  "Back End of Line — Metal 1",
  "BEOL-M2":  "Back End of Line — Metal 2",
  "BEOL-M3":  "Back End of Line — Metal 3",
  "BEOL-M4":  "Back End of Line — Metal 4",
  "BEOL-M5":  "Back End of Line — Metal 5",
  "BEOL-M6":  "Back End of Line — Metal 6",
};

export function areaColor(area) {
  return PROCESS_AREA_COLORS[area] || "#475569";
}

export function isValidArea(area) {
  return area == null || area === "" || PROCESS_AREAS.includes(area);
}
