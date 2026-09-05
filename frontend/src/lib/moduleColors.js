// Shared Inform/SplitTable module-color contract.
// Explicit Inform settings win. Known modules keep their established colors;
// unknown modules receive a stable, random-looking color derived from the name.
export const DEFAULT_MODULE_COLORS = Object.freeze({
  GATE: "#ef4444",
  STI: "#f59e0b",
  PC: "#f59e0b",
  MOL: "#10b981",
  BEOL: "#3b82f6",
  ET: "#8b5cf6",
  EDS: "#ec4899",
  "S-D Epi": "#14b8a6",
  Spacer: "#06b6d4",
  Well: "#a855f7",
  MASK: "#6b7280",
  FAB: "#334155",
  KNOB: "#0ea5e9",
  "기타": "#6b7280",
});

const AUTO_PALETTE = Object.freeze([
  "#6366f1", "#f59e0b", "#ec4899", "#10b981", "#3b82f6",
  "#ef4444", "#8b5cf6", "#06b6d4", "#e25822", "#84cc16",
  "#a855f7", "#14b8a6", "#e11d48", "#0ea5e9", "#d946ef",
]);

function normalizedConfiguredColor(name, configured) {
  if (!configured || typeof configured !== "object") return "";
  const target = String(name || "").trim().toLowerCase();
  for (const [rawName, rawColor] of Object.entries(configured)) {
    if (String(rawName || "").trim().toLowerCase() !== target) continue;
    const color = String(rawColor || "").trim();
    return /^#[0-9a-f]{6}$/i.test(color) ? color.toLowerCase() : "";
  }
  return "";
}

export function automaticModuleColor(name) {
  const clean = String(name || "기타").trim() || "기타";
  const known = DEFAULT_MODULE_COLORS[clean];
  if (known) return known;
  let hash = 2166136261;
  for (const char of clean) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return AUTO_PALETTE[(hash >>> 0) % AUTO_PALETTE.length];
}

export function moduleColor(name, configured = {}) {
  return normalizedConfiguredColor(name, configured) || automaticModuleColor(name);
}

export function moduleTextColor(color) {
  const match = /^#([0-9a-f]{6})$/i.exec(String(color || ""));
  if (!match) return "#ffffff";
  const n = Number.parseInt(match[1], 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return (r * 299 + g * 587 + b * 114) / 1000 >= 160 ? "#111827" : "#ffffff";
}
