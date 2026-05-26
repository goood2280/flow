import { useEffect, useMemo, useRef, useState } from "react";
import { sf } from "../lib/api";

const S = {
  width: "100%",
  padding: "7px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--bg-card)",
  color: "var(--text-primary)",
  fontSize: 14,
  boxSizing: "border-box",
};

const DEF = {
  waferRadius: 150,
  wfCenterX: 0,
  wfCenterY: 0,
  refShotX: 0,
  refShotY: 0,
  refShotCenterX: 0,
  refShotCenterY: 0,
  shotPitchX: 28,
  shotPitchY: 30,
  shotSizeX: 27.2,
  shotSizeY: 29.2,
  scribeLaneX: 0.8,
  scribeLaneY: 0.8,
  edgeExclusionMm: 3,
  tegSizeX: 1.2,
  tegSizeY: 0.6,
  offsetXMm: 0,
  offsetYMm: 0,
  chipCols: 3,
  chipRows: 2,
  chipWidth: 3.6,
  chipHeight: 4.8,
  scribePattern: [
    { positionRow: 0, type: "full" },
    { positionRow: 1, type: "full" },
    { positionRow: 2, type: "full" },
  ],
  chipOrigin: "shot_lower_left",
  chipOffsetX: 0,
  chipOffsetY: 0,
  tegText: "TEG_TOP,13.6,29.6\nTEG_RIGHT,27.6,14.6\nTEG_LEFT,-0.4,14.6\nTEG_BOTTOM,13.6,-0.4",
};

const DEF_TEG_ROWS = [
  { no: 101, name: "TEG_TOP", x: 13.6, y: 29.6, flat: 0 },
  { no: 102, name: "TEG_RIGHT", x: 27.6, y: 14.6, flat: 90 },
  { no: 103, name: "TEG_LEFT", x: -0.4, y: 14.6, flat: 90 },
  { no: 104, name: "TEG_BOTTOM", x: 13.6, y: -0.4, flat: 0 },
];

const TECH_COLUMNS = [
  { key: "tech", label: "tech" },
  { key: "module", label: "module" },
  { key: "step", label: "step" },
  { key: "note", label: "note" },
];

function num(v, d = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function parseTegs(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, idx) => {
      const [name, x, y] = line.split(",").map((s) => s.trim());
      return { id: idx + 1, no: idx + 1, name: name || `TEG_${idx + 1}`, x: num(x), y: num(y) };
    });
}

function normalizeTegRows(rows) {
  const base = Array.isArray(rows) && rows.length ? rows : DEF_TEG_ROWS;
  return base.map((row, idx) => ({
    id: String(row?.id || row?.name || row?.label || `TEG_${idx + 1}`),
    no: Number(row?.no) || idx + 1,
    name: String(row?.name || row?.label || `TEG_${idx + 1}`),
    x: num(row?.x ?? row?.dx_mm, 0),
    y: num(row?.y ?? row?.dy_mm, 0),
    flat: Number(row?.flat) === 90 ? 90 : 0,
  }));
}

function toTegDefinitions(rows) {
  return normalizeTegRows(rows).map((row) => ({
    id: String(row.id || row.name || `TEG_${row.no}`),
    label: String(row.name || `TEG_${row.no}`),
    dx_mm: num(row.x, 0),
    dy_mm: num(row.y, 0),
  }));
}

function buildCfgFromSaved(saved) {
  const out = { ...DEF };
  const src = saved && typeof saved === "object" ? saved : {};
  for (const k of Object.keys(DEF)) {
    if (k === "tegText") continue;
    if (src[k] !== undefined && src[k] !== null && src[k] !== "") out[k] = src[k];
  }
  out.scribePattern = Array.isArray(src?.scribePattern) && src.scribePattern.length
    ? src.scribePattern.map((row, idx) => ({ positionRow: Number(row?.positionRow ?? idx), type: row?.type === "half" ? "half" : "full" }))
    : Array.from({ length: Math.max(1, Number(src?.chipRows || DEF.chipRows)) + 1 }, (_, idx) => ({ positionRow: idx, type: "full" }));
  return out;
}

function shotCenter(shotX, shotY, cfg) {
  return {
    x: cfg.refShotCenterX + (shotX - cfg.refShotX) * cfg.shotPitchX,
    y: cfg.refShotCenterY + (shotY - cfg.refShotY) * cfg.shotPitchY,
  };
}

function localToAbs(localX, localY, center, cfg) {
  if (cfg.chipOrigin === "shot_center") {
    return { x: center.x + localX, y: center.y + localY };
  }
  return {
    x: center.x - cfg.shotSizeX / 2 + localX,
    y: center.y - cfg.shotSizeY / 2 + localY,
  };
}

function chipRectAbs(localX, localY, center, cfg) {
  const p = localToAbs(localX, localY, center, cfg);
  return { x: p.x, y: p.y, w: cfg.chipWidth, h: cfg.chipHeight };
}

function inWafer(x, y, cfg) {
  const dx = x - cfg.wfCenterX;
  const dy = y - cfg.wfCenterY;
  return Math.sqrt(dx * dx + dy * dy) <= cfg.waferRadius;
}

function rectInsideWafer(rect, cfg) {
  const corners = [
    { x: rect.x, y: rect.y },
    { x: rect.x + rect.w, y: rect.y },
    { x: rect.x, y: rect.y + rect.h },
    { x: rect.x + rect.w, y: rect.y + rect.h },
  ];
  return corners.every((p) => inWafer(p.x, p.y, cfg));
}

function tegFootprintRect(localX, localY, center, cfg, flat = 0) {
  const p = localToAbs(localX, localY, center, cfg);
  const rotated = Number(flat) === 90;
  return {
    x: p.x,
    y: p.y,
    w: rotated ? cfg.tegSizeY : cfg.tegSizeX,
    h: rotated ? cfg.tegSizeX : cfg.tegSizeY,
    flat: rotated ? 90 : 0,
  };
}

function rectIntersectsWafer(rect, cfg) {
  const corners = [
    { x: rect.x, y: rect.y },
    { x: rect.x + rect.w, y: rect.y },
    { x: rect.x, y: rect.y + rect.h },
    { x: rect.x + rect.w, y: rect.y + rect.h },
    { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 },
  ];
  if (corners.some((p) => inWafer(p.x, p.y, cfg))) return true;
  const dx = Math.max(Math.abs((rect.x + rect.w / 2) - cfg.wfCenterX) - rect.w / 2, 0);
  const dy = Math.max(Math.abs((rect.y + rect.h / 2) - cfg.wfCenterY) - rect.h / 2, 0);
  return Math.sqrt(dx * dx + dy * dy) <= cfg.waferRadius;
}

function distanceFromWfCenter(x, y, cfg) {
  const dx = x - cfg.wfCenterX;
  const dy = y - cfg.wfCenterY;
  return Math.sqrt(dx * dx + dy * dy);
}

function fmt(v) {
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
}

function csvCell(v) {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadRowsCsv(filename, rows, columns) {
  const header = columns.map((c) => csvCell(c.label || c.key)).join(",");
  const body = rows.map((row) => columns.map((c) => csvCell(row[c.key])).join(",")).join("\n");
  const blob = new Blob([header + "\n" + body], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function savedProductKey() {
  try {
    return localStorage.getItem("flow_waferlayout_product") || "";
  } catch (_) {
    return "";
  }
}

function shotLabel(shot) {
  if (!shot) return "-";
  const row = Number(shot.gridShotY);
  const col = Number(shot.gridShotX);
  if (!Number.isFinite(row) || !Number.isFinite(col)) return "-";
  return `Shot (${row},${col})`;
}

function normalizeScribePattern(pattern, chipRows) {
  const rows = Math.max(1, Math.floor(num(chipRows, 1)));
  const base = Array.from({ length: rows + 1 }, (_, idx) => {
    const found = Array.isArray(pattern) ? pattern.find((item) => Number(item?.positionRow) === idx) : null;
    return { positionRow: idx, type: found?.type === "half" ? "half" : "full" };
  });
  return base;
}

function buildLaneRows(cfg) {
  const chipRows = Math.max(1, Math.floor(num(cfg.chipRows, 1)));
  const pattern = normalizeScribePattern(cfg.scribePattern, chipRows);
  const scribeHeights = pattern.map((row) => row.type === "half" ? cfg.tegSizeY / 2 : cfg.tegSizeY);
  const laneRows = [];
  let cursor = 0;
  for (let idx = 0; idx < chipRows; idx += 1) {
    laneRows.push({ kind: "scribe", index: idx, type: pattern[idx]?.type || "full", mmHeight: scribeHeights[idx], yMm: cursor });
    cursor += scribeHeights[idx];
    laneRows.push({ kind: "chip", index: idx, mmHeight: cfg.chipHeight, yMm: cursor });
    cursor += cfg.chipHeight;
  }
  laneRows.push({ kind: "scribe", index: chipRows, type: pattern[chipRows]?.type || "full", mmHeight: scribeHeights[chipRows], yMm: cursor });
  return {
    laneRows,
    stackHeight: cursor + scribeHeights[chipRows],
    stackWidth: cfg.chipCols * cfg.chipWidth,
    pattern,
  };
}

function parseTegPaste(text, prevRows) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return null;
  const first = lines[0].split("\t").map((v) => v.trim().toLowerCase());
  const hasHeader = ["no", "name", "x", "y", "flat"].some((key) => first.includes(key));
  const body = hasHeader ? lines.slice(1) : lines;
  const nextNo = (prevRows || []).reduce((m, row) => Math.max(m, Number(row?.no) || 0), 0);
  const rows = body
    .map((line, idx) => line.split("\t").map((v) => v.trim()))
    .filter((parts) => parts.some(Boolean))
    .map((parts, idx) => {
      if (parts.length >= 5) {
        return {
          id: Date.now() + idx,
          no: Number(parts[0]) || nextNo + idx + 1,
          name: parts[1] || `TEG_${nextNo + idx + 1}`,
          x: num(parts[2], 0),
          y: num(parts[3], 0),
          flat: Number(parts[4]) === 90 ? 90 : 0,
        };
      }
      if (parts.length === 4) {
        return {
          id: Date.now() + idx,
          no: Number(parts[0]) || nextNo + idx + 1,
          name: parts[1] || `TEG_${nextNo + idx + 1}`,
          x: num(parts[2], 0),
          y: num(parts[3], 0),
          flat: 0,
        };
      }
      if (parts.length === 3) {
        return {
          id: Date.now() + idx,
          no: nextNo + idx + 1,
          name: parts[0] || `TEG_${nextNo + idx + 1}`,
          x: num(parts[1], 0),
          y: num(parts[2], 0),
          flat: 0,
        };
      }
      return null;
    })
    .filter(Boolean);
  return rows.length ? rows : null;
}

function techHeaderKey(raw) {
  const s = String(raw || "").trim().toLowerCase().replace(/\s+/g, "_");
  if (["product", "제품"].includes(s)) return "product";
  if (["tech", "tech_id", "technology", "테크", "테크명"].includes(s)) return "tech";
  if (["module", "모듈"].includes(s)) return "module";
  if (["step", "step_id", "공정"].includes(s)) return "step";
  if (["note", "description", "비고", "설명"].includes(s)) return "note";
  return "";
}

function normalizeTechRows(rows, product = "") {
  return (Array.isArray(rows) ? rows : [])
    .map((row, idx) => ({
      id: String(row?.id || `tech_${Date.now()}_${idx}`),
      product: String(product || row?.product || ""),
      tech: String(row?.tech || row?.tech_id || row?.technology || row?.["테크"] || ""),
      module: String(row?.module || row?.["모듈"] || ""),
      step: String(row?.step || row?.step_id || row?.["공정"] || ""),
      note: String(row?.note || row?.description || row?.["비고"] || row?.["설명"] || ""),
    }))
    .filter((row) => row.tech.trim() || row.module.trim() || row.step.trim() || row.note.trim());
}

function parseTechPaste(text, product, prevRows = []) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return [];
  const sep = text.includes("\t") ? "\t" : ",";
  const split = (line) => line.split(sep).map((part) => part.trim());
  const first = split(lines[0]);
  const headers = first.map(techHeaderKey);
  const hasHeader = headers.some(Boolean);
  const body = hasHeader ? lines.slice(1) : lines;
  const next = (prevRows || []).length;
  const rows = body.map((line, idx) => {
    const parts = split(line);
    const row = { id: `tech_${Date.now()}_${next + idx}`, product };
    if (hasHeader) {
      headers.forEach((key, colIdx) => {
        if (key && key !== "product") row[key] = parts[colIdx] || "";
      });
    } else if (parts.length >= 5) {
      row.tech = parts[1] || "";
      row.module = parts[2] || "";
      row.step = parts[3] || "";
      row.note = parts.slice(4).join(" ");
    } else {
      row.tech = parts[0] || "";
      row.module = parts[1] || "";
      row.step = parts[2] || "";
      row.note = parts.slice(3).join(" ");
    }
    return row;
  });
  return normalizeTechRows(rows, product);
}

function Input({ label, value, onChange, type = "number" }) {
  return (
    <label style={{ display: "grid", gap: 4 }}>
      <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{label}</span>
      <input value={value} onChange={onChange} type={type} style={S} />
    </label>
  );
}

function Mini({ label, value, tone = "var(--accent)" }) {
  return (
    <div style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)" }}>
      <div style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 18, fontWeight: 800, color: tone, fontFamily: "monospace" }}>{value}</div>
    </div>
  );
}

export default function My_WaferLayout() {
  const [cfg, setCfg] = useState(DEF);
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState(savedProductKey);
  const productRef = useRef(product);
  const [tegRows, setTegRows] = useState(DEF_TEG_ROWS);
  const [isAdmin, setIsAdmin] = useState(false);
  const [layoutCache, setLayoutCache] = useState({});
  const [layoutLoading, setLayoutLoading] = useState(false);
  const [tegSearch, setTegSearch] = useState("");
  const [viewMode, setViewMode] = useState("shot");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [selectedTegNos, setSelectedTegNos] = useState([]);
  const [msg, setMsg] = useState("");
  const [techRows, setTechRows] = useState([]);
  const [techLoading, setTechLoading] = useState(false);
  const [techMsg, setTechMsg] = useState("");
  const applyLayout = (wl) => {
    setCfg((prev) => ({ ...prev, ...buildCfgFromSaved(wl || {}) }));
    setTegRows(normalizeTegRows(wl?.teg_definitions || wl?.tegs));
  };
  useEffect(() => {
    productRef.current = product;
    if (product) {
      try { localStorage.setItem("flow_waferlayout_product", product); } catch (_) {}
    }
  }, [product]);
  useEffect(() => {
    try {
      const raw = localStorage.getItem("hol_user");
      const parsed = raw ? JSON.parse(raw) : null;
      setIsAdmin(parsed?.role === "admin");
    } catch (_) {
      setIsAdmin(false);
    }
  }, []);
  const loadProductLayout = (nextProduct, { force = false } = {}) => {
    if (!nextProduct) return;
    if (!force && layoutCache[nextProduct]) {
      if (productRef.current === nextProduct) applyLayout(layoutCache[nextProduct]);
      return;
    }
    setLayoutLoading(true);
    sf("/api/waferlayout/grid?product=" + encodeURIComponent(nextProduct))
      .then((d) => {
        const wl = d?.wafer_layout || {};
        setLayoutCache((prev) => ({ ...prev, [nextProduct]: wl }));
        if (productRef.current === nextProduct) applyLayout(wl);
      })
      .catch(() => {
        const wl = { ...DEF, tegs: DEF_TEG_ROWS };
        setLayoutCache((prev) => ({ ...prev, [nextProduct]: wl }));
        if (productRef.current === nextProduct) applyLayout(wl);
      })
      .finally(() => setLayoutLoading(false));
  };
  useEffect(() => {
    sf("/api/catalog/product/list")
      .then((d) => {
        const list = (d.products || []).map((x) => x.product).filter(Boolean);
        setProducts(list);
        const saved = savedProductKey();
        const next = (saved && (!list.length || list.includes(saved))) ? saved : (list[0] || "PRODUCT_A0");
        productRef.current = next;
        setProduct(next);
        loadProductLayout(next, { force: true });
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (product) loadProductLayout(product);
  }, [product]);
  const loadTechList = (nextProduct) => {
    if (!nextProduct) { setTechRows([]); return; }
    setTechLoading(true);
    sf("/api/waferlayout/tech-list?product=" + encodeURIComponent(nextProduct))
      .then((d) => setTechRows(normalizeTechRows(d?.rows || [], nextProduct)))
      .catch(() => setTechRows([]))
      .finally(() => setTechLoading(false));
  };
  useEffect(() => {
    loadTechList(product);
  }, [product]);
  const data = useMemo(() => {
    const c = {
      waferRadius: num(cfg.waferRadius, 150),
      wfCenterX: num(cfg.wfCenterX, 0),
      wfCenterY: num(cfg.wfCenterY, 0),
      refShotX: num(cfg.refShotX, 0),
      refShotY: num(cfg.refShotY, 0),
      refShotCenterX: num(cfg.refShotCenterX, 0),
      refShotCenterY: num(cfg.refShotCenterY, 0),
      shotPitchX: num(cfg.shotPitchX, 28),
      shotPitchY: num(cfg.shotPitchY, 30),
      shotSizeX: num(cfg.shotSizeX, 27.2),
      shotSizeY: num(cfg.shotSizeY, 29.2),
      scribeLaneX: num(cfg.scribeLaneX, 0.8),
      scribeLaneY: num(cfg.scribeLaneY, 0.8),
      edgeExclusionMm: Math.max(0, num(cfg.edgeExclusionMm, 3)),
      tegSizeX: Math.max(0, num(cfg.tegSizeX, 1.2)),
      tegSizeY: Math.max(0, num(cfg.tegSizeY, 0.6)),
      offsetXMm: num(cfg.offsetXMm, 0),
      offsetYMm: num(cfg.offsetYMm, 0),
      chipCols: Math.max(1, Math.floor(num(cfg.chipCols, 6))),
      chipRows: Math.max(1, Math.floor(num(cfg.chipRows, 4))),
      chipWidth: num(cfg.chipWidth, 3.6),
      chipHeight: num(cfg.chipHeight, 4.8),
      chipOrigin: cfg.chipOrigin === "shot_center" ? "shot_center" : "shot_lower_left",
      chipOffsetX: Math.floor(num(cfg.chipOffsetX, 0)),
      chipOffsetY: Math.floor(num(cfg.chipOffsetY, 0)),
      scribePattern: normalizeScribePattern(cfg.scribePattern, cfg.chipRows),
    };
    c.shotSizeX = Math.min(c.shotSizeX, c.shotPitchX);
    c.shotSizeY = Math.min(c.shotSizeY, c.shotPitchY);
    const usableCfg = { ...c, waferRadius: Math.max(0, c.waferRadius - c.edgeExclusionMm) };
    const tegs = normalizeTegRows(tegRows);
    const chipW = Math.max(0.1, c.chipWidth);
    const chipH = Math.max(0.1, c.chipHeight);
    const laneLayout = buildLaneRows({ ...c, chipWidth: chipW, chipHeight: chipH });
    const maxShotX = Math.ceil((c.waferRadius + Math.abs(c.refShotCenterX - c.wfCenterX)) / Math.max(1, c.shotPitchX)) + 2;
    const maxShotY = Math.ceil((c.waferRadius + Math.abs(c.refShotCenterY - c.wfCenterY)) / Math.max(1, c.shotPitchY)) + 2;
    const shots = [];
    const chips = [];
    const allChipSlots = [];
    const tegPoints = [];
    let fullShots = 0;
    let partialShots = 0;
    for (let sy = -maxShotY; sy <= maxShotY; sy += 1) {
      for (let sx = -maxShotX; sx <= maxShotX; sx += 1) {
        const center = shotCenter(sx, sy, c);
        const pitchRect = {
          x: center.x - c.shotPitchX / 2,
          y: center.y - c.shotPitchY / 2,
          w: c.shotPitchX,
          h: c.shotPitchY,
        };
        const shotBody = {
          x: center.x - c.shotSizeX / 2,
          y: center.y - c.shotSizeY / 2,
          w: c.shotSizeX,
          h: c.shotSizeY,
        };
        const corners = [
          { x: shotBody.x, y: shotBody.y },
          { x: shotBody.x + shotBody.w, y: shotBody.y },
          { x: shotBody.x, y: shotBody.y + shotBody.h },
          { x: shotBody.x + shotBody.w, y: shotBody.y + shotBody.h },
        ];
        const fullShot = rectInsideWafer(shotBody, c);
        const usableShot = rectInsideWafer(shotBody, usableCfg);
        const keep = fullShot || rectIntersectsWafer(pitchRect, c) || inWafer(center.x, center.y, c);
        if (!keep) continue;
        const shot = { shotX: sx, shotY: sy, center, corners, pitchRect, shotBody, fullShot, usableShot };
        shots.push(shot);
        if (usableShot) fullShots += 1;
        else if (fullShot) partialShots += 1;
        const x0 = (c.chipOrigin === "shot_center" ? -(laneLayout.stackWidth) / 2 : 0) + c.offsetXMm;
        const y0 = (c.chipOrigin === "shot_center" ? -(laneLayout.stackHeight) / 2 : 0) + c.offsetYMm;
        for (let cy = 0; cy < c.chipRows; cy += 1) {
          const chipRow = laneLayout.laneRows.find((row) => row.kind === "chip" && row.index === cy);
          const chipLocalY = y0 + (chipRow?.yMm || 0);
          for (let cx = 0; cx < c.chipCols; cx += 1) {
            const localX = x0 + cx * chipW;
            const localY = chipLocalY;
            const abs = chipRectAbs(localX, localY, center, { ...c, chipWidth: chipW, chipHeight: chipH });
            const chipInside = rectInsideWafer(abs, usableCfg);
            const chipTouch = rectIntersectsWafer(abs, c);
            const chipBase = {
              shotX: sx, shotY: sy, chipX: cx, chipY: cy,
              x: abs.x, y: abs.y, w: abs.w, h: abs.h,
              centerX: abs.x + abs.w / 2, centerY: abs.y + abs.h / 2,
              fullShot, chipInside, chipTouch,
              edgeCut: chipTouch && !chipInside,
              isInWafer: chipInside,
            };
            allChipSlots.push(chipBase);
            if (!chipTouch) continue;
            chips.push(chipBase);
          }
        }
        tegs.forEach((teg) => {
          const abs = localToAbs(teg.x + c.offsetXMm, teg.y + c.offsetYMm, center, c);
          const tegRect = tegFootprintRect(teg.x + c.offsetXMm, teg.y + c.offsetYMm, center, c, teg.flat);
          if (!rectIntersectsWafer(tegRect, c)) return;
          const edgeMargin = c.waferRadius - distanceFromWfCenter(abs.x, abs.y, c);
          const lowerLeftDist = Math.sqrt(Math.max(0, abs.x - shotBody.x) ** 2 + Math.max(0, abs.y - shotBody.y) ** 2);
          const tegInsideUsable = rectInsideWafer(tegRect, usableCfg);
          tegPoints.push({ ...teg, shotX: sx, shotY: sy, x: abs.x, y: abs.y, fullShot, usableShot, edgePreferred: edgeMargin > c.edgeExclusionMm, edgeMargin, tegRect, lowerLeftDist, tegInsideUsable });
        });
      }
    }
    const shotMinX = shots.length ? Math.min(...shots.map((s) => s.shotX)) : 0;
    const shotMaxX = shots.length ? Math.max(...shots.map((s) => s.shotX)) : 0;
    const shotMinY = shots.length ? Math.min(...shots.map((s) => s.shotY)) : 0;
    const shotMaxY = shots.length ? Math.max(...shots.map((s) => s.shotY)) : 0;
    const usableOnly = shots.filter((s) => s.usableShot);
    const xKeys = [...new Set(usableOnly.map((s) => s.shotX))].sort((a, b) => a - b);
    const yKeys = [...new Set(usableOnly.map((s) => s.shotY))].sort((a, b) => b - a);
    const shotCoordMap = new Map();
    usableOnly.forEach((s) => {
      shotCoordMap.set(`${s.shotX}|${s.shotY}`, {
        etShotX: xKeys.indexOf(s.shotX) + 1,
        etShotY: yKeys.indexOf(s.shotY) + 1,
      });
    });
    const allShotXKeys = [...new Set(shots.map((s) => s.shotX))].sort((a, b) => a - b);
    const allShotYKeys = [...new Set(shots.map((s) => s.shotY))].sort((a, b) => b - a);
    const shotsLabeled = shots.map((s) => {
      const et = shotCoordMap.get(`${s.shotX}|${s.shotY}`) || null;
      return {
        ...s,
        etShotX: et?.etShotX || null,
        etShotY: et?.etShotY || null,
        gridShotX: allShotXKeys.indexOf(s.shotX) + 1,
        gridShotY: allShotYKeys.indexOf(s.shotY) + 1,
        isInWafer: s.usableShot,
      };
    });
    const chipXCenters = [...new Set(allChipSlots.filter((chip) => chip.chipTouch).map((chip) => Number(chip.centerX.toFixed(4))))].sort((a, b) => a - b);
    const chipYCenters = [...new Set(allChipSlots.filter((chip) => chip.chipTouch).map((chip) => Number(chip.centerY.toFixed(4))))].sort((a, b) => b - a);
    const chipsLabeled = chips.map((chip) => {
      const cx = Number(chip.centerX.toFixed(4));
      const cy = Number(chip.centerY.toFixed(4));
      const globalChipX = c.chipOffsetX + chipXCenters.indexOf(cx) + 1;
      const globalChipY = c.chipOffsetY + chipYCenters.indexOf(cy) + 1;
      const shotEt = shotCoordMap.get(`${chip.shotX}|${chip.shotY}`) || null;
      const shotGrid = shotsLabeled.find((s) => s.shotX === chip.shotX && s.shotY === chip.shotY) || null;
      return {
        ...chip,
        globalChipX,
        globalChipY,
        etShotX: shotEt?.etShotX || null,
        etShotY: shotEt?.etShotY || null,
        gridShotX: shotGrid?.gridShotX || null,
        gridShotY: shotGrid?.gridShotY || null,
      };
    });
    const summarizeChipBounds = (source) => {
      if (!source.length) return null;
      const centerChip = source.reduce((best, chip) => {
        const score = distanceFromWfCenter(chip.centerX, chip.centerY, c);
        if (!best || score < best.score) return { chip, score };
        return best;
      }, null)?.chip || null;
      return {
        count: source.length,
        minX: Math.min(...source.map((chip) => chip.x)),
        maxX: Math.max(...source.map((chip) => chip.x + chip.w)),
        minY: Math.min(...source.map((chip) => chip.y)),
        maxY: Math.max(...source.map((chip) => chip.y + chip.h)),
        minGlobalChipX: Math.min(...source.map((chip) => chip.globalChipX)),
        maxGlobalChipX: Math.max(...source.map((chip) => chip.globalChipX)),
        minGlobalChipY: Math.min(...source.map((chip) => chip.globalChipY)),
        maxGlobalChipY: Math.max(...source.map((chip) => chip.globalChipY)),
        centerChipKey: centerChip ? `${centerChip.globalChipX}|${centerChip.globalChipY}` : "",
        centerChip: centerChip ? {
          globalChipX: centerChip.globalChipX,
          globalChipY: centerChip.globalChipY,
          centerX: centerChip.centerX,
          centerY: centerChip.centerY,
          gridShotX: centerChip.gridShotX,
          gridShotY: centerChip.gridShotY,
          etShotX: centerChip.etShotX,
          etShotY: centerChip.etShotY,
          localChipX: centerChip.chipX,
          localChipY: centerChip.chipY,
        } : null,
      };
    };
    const chipBounds = {
      inside: summarizeChipBounds(chipsLabeled.filter((chip) => chip.chipInside)),
      touch: summarizeChipBounds(chipsLabeled.filter((chip) => chip.chipTouch)),
    };
    const shotSample = shotsLabeled.find((s) => s.usableShot) || shotsLabeled[0] || null;
    return {
      cfg: c, tegs, shots: shotsLabeled, chips: chipsLabeled, tegPoints,
      shotMinX, shotMaxX, shotMinY, shotMaxY, usableShots: fullShots, edgeShots: partialShots,
      chipW, chipH, shotSample, laneLayout, chipBounds,
    };
  }, [cfg, tegRows]);
  const filteredTegs = useMemo(() => {
    const q = String(tegSearch || "").trim().toLowerCase();
    const sel = new Set(selectedTegNos.map((v) => Number(v)));
    if (!q && !sel.size) return [];
    return data.tegPoints.filter((t) => sel.has(Number(t.no)) || String(t.name || "").toLowerCase().includes(q) || String(t.no || "").includes(q));
  }, [data.tegPoints, tegSearch, selectedTegNos]);
  const usableChips = useMemo(() => data.chips.filter((c) => c.chipInside).length, [data.chips]);
  const filteredChips = useMemo(() => data.chips.filter((c) => c.chipTouch), [data.chips]);
  const shotAllRows = useMemo(() => data.shots.filter((s) => !s.usableShot), [data.shots]);
  const chipShotSummary = useMemo(() => {
    const grouped = new Map();
    data.chips.forEach((chip) => {
      if (!chip.chipInside) return;
      const key = `${chip.shotX}|${chip.shotY}`;
      const shot = data.shots.find((s) => s.shotX === chip.shotX && s.shotY === chip.shotY);
      if (!grouped.has(key)) {
        grouped.set(key, {
          shotX: chip.shotX,
          shotY: chip.shotY,
          etShotX: chip.etShotX,
          etShotY: chip.etShotY,
          gridShotX: chip.gridShotX,
          gridShotY: chip.gridShotY,
          usableShot: !!shot?.usableShot,
          chips: [],
        });
      }
      grouped.get(key).chips.push(`${chip.globalChipX},${chip.globalChipY}`);
    });
    return [...grouped.values()]
      .map((row) => ({ ...row, chipCount: row.chips.length, chipList: row.chips.join(" · ") }))
      .sort((a, b) => (a.gridShotY || 999) - (b.gridShotY || 999) || (a.gridShotX || 999) - (b.gridShotX || 999) || a.shotY - b.shotY || a.shotX - b.shotX);
  }, [data.chips, data.shots]);
  const selectedTegSet = useMemo(() => new Set(selectedTegNos.map((v) => Number(v))), [selectedTegNos]);
  const selectedTegDefs = useMemo(() => tegRows.filter((r) => selectedTegSet.has(Number(r.no))), [tegRows, selectedTegSet]);
  const toggleTegNo = (no) => {
    const n = Number(no);
    if (!Number.isFinite(n)) return;
    setSelectedTegNos((prev) => prev.some((v) => Number(v) === n) ? prev.filter((v) => Number(v) !== n) : [...prev, n]);
  };
  const edgeQualifiedRows = useMemo(() => {
    if (!selectedTegDefs.length) return [];
    const usableCfg = { ...data.cfg, waferRadius: Math.max(0, data.cfg.waferRadius - data.cfg.edgeExclusionMm) };
    return data.shots
      .filter((shot) => !shot.usableShot)
      .map((shot) => {
        const tegDetails = selectedTegDefs.map((teg) => {
          const rect = tegFootprintRect(teg.x + data.cfg.offsetXMm, teg.y + data.cfg.offsetYMm, shot.center, data.cfg, teg.flat);
          return {
            id: String(teg.id || teg.name || teg.no),
            no: Number(teg.no),
            name: teg.name,
            inside: rectInsideWafer(rect, usableCfg),
          };
        });
        const alive = tegDetails.length > 0 && tegDetails.every((row) => row.inside);
        return alive ? {
          shot_x: shot.gridShotX,
          shot_y: shot.gridShotY,
          raw_shot_x: shot.shotX,
          raw_shot_y: shot.shotY,
          teg_ids: tegDetails.map((row) => row.id),
          teg_names: tegDetails.map((row) => row.name),
        } : null;
      })
      .filter(Boolean);
  }, [data.shots, data.cfg, selectedTegDefs]);
  const edgeCandidateMap = useMemo(() => {
    const map = new Map();
    edgeQualifiedRows.forEach((row) => {
      map.set(`${row.raw_shot_x}|${row.raw_shot_y}`, row);
    });
    return map;
  }, [edgeQualifiedRows]);
  const edgeShotCoverage = useMemo(() => ({
    edgeShots: edgeQualifiedRows.length,
    coveredShots: edgeQualifiedRows.length,
    rows: edgeQualifiedRows.map((row) => ({
      shotX: row.shot_x,
      shotY: row.shot_y,
      rawShotX: row.raw_shot_x,
      rawShotY: row.raw_shot_y,
      tegIds: row.teg_ids || [],
    })),
  }), [edgeQualifiedRows]);
  const shotAllDisplayRows = useMemo(() => {
    if (viewMode !== "shot_all") return data.shots;
    if (!selectedTegDefs.length) return shotAllRows;
    return data.shots.filter((shot) => edgeCandidateMap.has(`${shot.shotX}|${shot.shotY}`));
  }, [data.shots, shotAllRows, viewMode, selectedTegDefs, edgeCandidateMap]);
  const chipMappingRows = useMemo(() => (
    filteredChips
      .map((chip) => ({
        chipX: chip.globalChipX,
        chipY: chip.globalChipY,
        shotX: chip.gridShotX || "",
        shotY: chip.gridShotY || "",
        status: chip.chipInside ? "inside" : "edge-cut",
      }))
      .sort((a, b) => (a.shotY || 999) - (b.shotY || 999)
        || (a.shotX || 999) - (b.shotX || 999)
        || (a.chipY || 999) - (b.chipY || 999)
        || (a.chipX || 999) - (b.chipX || 999))
  ), [filteredChips]);
  const sampleSelectedTeg = useMemo(() => {
    if (!selectedTegDefs.length || !data.shotSample) return null;
    const usableCfg = { ...data.cfg, waferRadius: Math.max(0, data.cfg.waferRadius - data.cfg.edgeExclusionMm) };
    const teg = selectedTegDefs[0];
    const p = localToAbs(teg.x, teg.y, data.shotSample.center, data.cfg);
    const rect = tegFootprintRect(teg.x, teg.y, data.shotSample.center, data.cfg, teg.flat);
    return {
      ...teg,
      lowerLeftDist: Math.sqrt(Math.max(0, p.x - data.shotSample.shotBody.x) ** 2 + Math.max(0, p.y - data.shotSample.shotBody.y) ** 2),
      usable: rectInsideWafer(rect, usableCfg),
    };
  }, [selectedTegDefs, data.shotSample, data.cfg]);
  const shotSamplePreview = useMemo(() => {
    const preview = buildLaneRows(data.cfg);
    const snappedTegs = normalizeTegRows(tegRows)
      .map((teg) => {
        const lane = preview.laneRows.find((row) => row.kind === "scribe" && teg.y >= row.yMm && teg.y <= (row.yMm + row.mmHeight));
        return { ...teg, laneIndex: lane ? lane.index : null, laneType: lane?.type || null, laneStartMm: lane?.yMm || null, laneHeightMm: lane?.mmHeight || null };
      })
      .sort((a, b) => {
        if ((a.laneIndex ?? 999) !== (b.laneIndex ?? 999)) return (a.laneIndex ?? 999) - (b.laneIndex ?? 999);
        return a.x - b.x;
      });
    return { ...preview, snappedTegs, chipCols: data.cfg.chipCols };
  }, [data.cfg, tegRows]);
  const saveLayout = () => {
    if (!product) return;
    const wafer_layout = {
      ...buildCfgFromSaved(cfg),
      teg_definitions: toTegDefinitions(tegRows),
      tegs: tegRows.map((r) => ({ no: Number(r.no) || 0, name: String(r.name || ""), x: num(r.x, 0), y: num(r.y, 0), flat: Number(r.flat) === 90 ? 90 : 0 })),
    };
    sf("/api/waferlayout/grid", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product, ...wafer_layout }),
    })
      .then(() => {
        setLayoutCache((prev) => ({ ...prev, [product]: wafer_layout }));
        setMsg("제품 layout 저장됨");
        setTimeout(() => setMsg(""), 2200);
      })
      .catch((e) => {
        setMsg("저장 실패: " + (e?.message || "unknown"));
        setTimeout(() => setMsg(""), 2600);
      });
  };

  const W = 500;
  const H = 500;
  const scale = (W * 0.42) / Math.max(1, data.cfg.waferRadius);
  const px = (x) => W / 2 + (x - data.cfg.wfCenterX) * scale;
  const py = (y) => H / 2 - (y - data.cfg.wfCenterY) * scale;
  const shotSampleViewW = 520;
  const shotSampleW = 450;
  const shotSampleH = Math.max(140, shotSampleW * (Math.max(1, data.cfg.shotSizeY) / Math.max(1, data.cfg.shotSizeX)));
  const shotSampleX = 36;
  const shotSampleY = 24;
  const lanePadX = Math.min(shotSampleW * 0.16, shotSampleW * ((Math.max(0, data.cfg.shotPitchX - data.cfg.shotSizeX)) / (2 * Math.max(1, data.cfg.shotPitchX))));
  const lanePadY = Math.min(shotSampleH * 0.16, shotSampleH * ((Math.max(0, data.cfg.shotPitchY - data.cfg.shotSizeY)) / (2 * Math.max(1, data.cfg.shotPitchY))));
  const bodyX = shotSampleX + lanePadX;
  const bodyY = shotSampleY + lanePadY;
  const bodyW = Math.max(40, shotSampleW - lanePadX * 2);
  const bodyH = Math.max(40, shotSampleH - lanePadY * 2);
  const paneCard = {
    padding: 14,
    borderRadius: 8,
    border: "1px solid var(--border)",
    background: "var(--bg-secondary)",
    minWidth: 0,
  };
  const sheetWrap = {
    border: "1px solid var(--border)",
    borderRadius: 8,
    overflow: "auto",
    background: "var(--bg-card)",
  };
  const sheetHead = {
    textAlign: "left",
    padding: "8px 10px",
    fontSize: 14,
    color: "var(--text-secondary)",
    fontFamily: "monospace",
    borderBottom: "1px solid var(--border)",
    position: "sticky",
    top: 0,
    background: "var(--bg-secondary)",
    zIndex: 1,
    whiteSpace: "nowrap",
  };
  const sheetCell = {
    padding: 0,
    borderBottom: "1px solid var(--border)",
    borderRight: "1px solid var(--border)",
    background: "rgba(255,255,255,0.55)",
  };
  const sheetInput = {
    width: "100%",
    padding: "9px 10px",
    border: "none",
    background: "transparent",
    color: "var(--text-primary)",
    fontSize: 14,
    outline: "none",
    boxSizing: "border-box",
    fontFamily: "monospace",
  };
  const handleTegPaste = (e) => {
    const text = e.clipboardData?.getData("text/plain");
    if (!text || !text.includes("\t")) return;
    const rows = parseTegPaste(text, tegRows);
    if (!rows) return;
    e.preventDefault();
    setTegRows(rows);
  };
  const handleTechPaste = (e) => {
    const text = e.clipboardData?.getData("text/plain");
    if (!text || (!text.includes("\t") && !text.includes(","))) return;
    const rows = parseTechPaste(text, product, techRows);
    if (!rows.length) return;
    e.preventDefault();
    setTechRows((prev) => normalizeTechRows([...prev, ...rows], product));
  };
  const updateTechRow = (idx, key, value) => {
    setTechRows((prev) => prev.map((row, i) => i === idx ? { ...row, [key]: value, product } : row));
  };
  const addTechRow = () => {
    setTechRows((prev) => [...prev, { id: `tech_${Date.now()}`, product, tech: "", module: "", step: "", note: "" }]);
  };
  const saveTechList = () => {
    if (!product || !isAdmin) return;
    setTechMsg("저장 중...");
    sf("/api/waferlayout/tech-list", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product, rows: normalizeTechRows(techRows, product) }),
    })
      .then((d) => {
        setTechRows(normalizeTechRows(d?.rows || [], product));
        setTechMsg("tech list 저장됨");
        setTimeout(() => setTechMsg(""), 2200);
      })
      .catch((e) => {
        setTechMsg("저장 실패: " + (e?.message || "unknown"));
        setTimeout(() => setTechMsg(""), 2600);
      });
  };
  const chipCsvColumns = [
    { key: "chipX", label: "chip_x" },
    { key: "chipY", label: "chip_y" },
    { key: "shotX", label: "shot_x" },
    { key: "shotY", label: "shot_y" },
    { key: "status", label: "status" },
  ];
  const chipInsideBounds = data.chipBounds?.inside || null;
  const chipTouchBounds = data.chipBounds?.touch || null;
  const centerChip = chipInsideBounds?.centerChip || chipTouchBounds?.centerChip || null;
  const centerChipKey = chipInsideBounds?.centerChipKey || chipTouchBounds?.centerChipKey || "";
  const mmRange = (bounds, a, b) => bounds ? `${fmt(bounds[a])} ~ ${fmt(bounds[b])} mm` : "-";
  const chipRange = (bounds, a, b) => bounds ? `${bounds[a]} ~ ${bounds[b]}` : "-";

  return (
    <div style={{ padding: "12px 14px", background: "var(--bg-primary)", color: "var(--text-primary)", height: "100%", overflow: "auto", boxSizing: "border-box" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-secondary)" }}>웨이퍼 레이아웃</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select value={product} onChange={(e) => {
            const next = e.target.value;
            setProduct(next);
          }} style={{ ...S, width: 180 }}>
            {(products.length ? products : ["PRODUCT_A0"]).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <div style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 10, overflow: "hidden" }}>
            {["shot", "shot_all", "chip"].map((m) => (
              <button key={m} onClick={() => setViewMode(m)} style={{ padding: "8px 12px", border: "none", background: viewMode === m ? "var(--accent)" : "var(--bg-card)", color: viewMode === m ? "#fff" : "var(--text-primary)", cursor: "pointer", fontWeight: 700 }}>
                {m === "shot" ? "샷 보기" : m === "shot_all" ? "샷 전체" : "칩 보기"}
              </button>
            ))}
          </div>
          <button onClick={saveLayout} disabled={!isAdmin} style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "var(--accent)", color: "#fff", cursor: isAdmin ? "pointer" : "not-allowed", fontWeight: 700, opacity: isAdmin ? 1 : 0.5 }}>제품 설정 저장</button>
          <button onClick={() => setShowAdvanced((v) => !v)} style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border)", background: showAdvanced ? "var(--accent-glow)" : "var(--bg-card)", color: showAdvanced ? "var(--accent)" : "var(--text-primary)", cursor: "pointer", fontWeight: 700 }}>
            {showAdvanced ? "상세 접기" : "상세 설정"}
          </button>
          {!!msg && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{msg}</span>}
          {layoutLoading && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>loading…</span>}
        </div>
      </div>

      <div style={{ marginBottom: 10, display: "flex", flexWrap: "wrap", gap: 8 }}>
        <span style={{ padding: "6px 10px", borderRadius: 999, background: "rgba(37,99,235,0.10)", border: "1px solid rgba(37,99,235,0.20)", color: "#1d4ed8", fontSize: 14, fontFamily: "monospace", fontWeight: 700 }}>
          제품: {product || "-"}
        </span>
        <span style={{ padding: "6px 10px", borderRadius: 999, background: "rgba(15,118,110,0.08)", border: "1px solid rgba(15,118,110,0.18)", color: "#0f766e", fontSize: 14, fontFamily: "monospace" }}>
          보기: {viewMode === "shot" ? "샷" : viewMode === "shot_all" ? "샷 전체" : "칩"}
        </span>
        <span style={{ padding: "6px 10px", borderRadius: 999, background: "rgba(249,115,22,0.08)", border: "1px solid rgba(249,115,22,0.18)", color: "#b45309", fontSize: 14, fontFamily: "monospace" }}>
          엣지 제외: {fmt(data.cfg.edgeExclusionMm)} mm
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(280px, 360px)", gap: 12, alignItems: "start" }}>
        <div style={{ display: "grid", gap: 10, position: "sticky", top: 8, alignSelf: "start", minWidth: 0, gridColumn: 2, maxHeight: "calc(100vh - 150px)", overflow: "auto", paddingRight: 2 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <Mini label="Usable Shots" value={data.usableShots} tone="#2563eb" />
            <Mini label="Usable Chips" value={usableChips} tone="#0f766e" />
            <Mini label="TEG Points" value={data.tegPoints.length} tone="#7c3aed" />
            <Mini label="Edge / Non-usable" value={data.edgeShots} tone="#f97316" />
          </div>

          {viewMode === "chip" && <div style={paneCard}>
            <div style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)", marginBottom: 10 }}>칩 좌표 검증</div>
            <div style={{ display: "grid", gap: 7, fontSize: 14, lineHeight: 1.55, color: "var(--text-secondary)" }}>
              <div>
                <b style={{ color: "var(--text-primary)" }}>Center chip</b>{" "}
                {centerChip ? `G(${centerChip.globalChipX},${centerChip.globalChipY}) / center (${fmt(centerChip.centerX)}, ${fmt(centerChip.centerY)}) mm / Shot (${centerChip.gridShotY || "-"},${centerChip.gridShotX || "-"})` : "-"}
              </div>
              <div>
                <b style={{ color: "var(--text-primary)" }}>Inside chip range</b>{" "}
                X {chipRange(chipInsideBounds, "minGlobalChipX", "maxGlobalChipX")} / Y {chipRange(chipInsideBounds, "minGlobalChipY", "maxGlobalChipY")}
              </div>
              <div>
                <b style={{ color: "var(--text-primary)" }}>Inside edge</b>{" "}
                X {mmRange(chipInsideBounds, "minX", "maxX")} / Y {mmRange(chipInsideBounds, "minY", "maxY")}
              </div>
              <div>
                <b style={{ color: "var(--text-primary)" }}>WF touch edge</b>{" "}
                X {mmRange(chipTouchBounds, "minX", "maxX")} / Y {mmRange(chipTouchBounds, "minY", "maxY")}
              </div>
            </div>
          </div>}

          <div style={paneCard}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>TEG Pick</div>
              <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{selectedTegDefs.length} selected</span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {data.tegs.map((row) => {
                const active = selectedTegSet.has(Number(row.no));
                return (
                  <button
                    key={row.id}
                    onClick={() => toggleTegNo(row.no)}
                    title={`${row.name} · x ${fmt(row.x)}, y ${fmt(row.y)}`}
                    style={{
                      padding: "5px 8px",
                      borderRadius: 4,
                      border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                      background: active ? "var(--accent-glow)" : "var(--bg-card)",
                      color: active ? "var(--accent)" : "var(--text-primary)",
                      cursor: "pointer",
                      fontSize: 14,
                      fontFamily: "monospace",
                      fontWeight: active ? 800 : 600,
                    }}
                  >
                    {row.no}:{row.name}
                  </button>
                );
              })}
            </div>
            {viewMode === "shot_all" && (
              <div style={{ marginTop: 8, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                TEG를 선택하면 edge shot 중 선택 TEG가 모두 3mm usable band 안에 완전히 들어가는 shot만 표시합니다.
              </div>
            )}
          </div>

          {viewMode === "shot" && <div style={paneCard}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>제품별 Tech List</div>
              <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{techRows.length} rows</span>
            </div>
            <div style={{ ...sheetWrap, maxHeight: 260 }} onPaste={handleTechPaste}>
              <table style={{ width: "100%", borderCollapse: "separate", borderSpacing: 0 }}>
                <thead>
                  <tr>
                    {TECH_COLUMNS.map((col) => (
                      <th key={col.key} style={{ ...sheetHead, borderRight: "1px solid var(--border)" }}>{col.label}</th>
                    ))}
                    <th style={{ ...sheetHead, borderRight: "none", width: 54 }}>삭제</th>
                  </tr>
                </thead>
                <tbody>
                  {techRows.length === 0 && (
                    <tr>
                      <td colSpan={TECH_COLUMNS.length + 1} style={{ padding: "14px 10px", color: "var(--text-secondary)", fontSize: 14, textAlign: "center" }}>
                        엑셀에서 tech / module / step / note 컬럼을 복사해 붙여넣을 수 있습니다.
                      </td>
                    </tr>
                  )}
                  {techRows.map((row, idx) => (
                    <tr key={row.id || idx}>
                      {TECH_COLUMNS.map((col) => (
                        <td key={col.key} style={{ ...sheetCell, minWidth: col.key === "note" ? 160 : 96 }}>
                          <input
                            value={row[col.key] || ""}
                            onChange={(e) => updateTechRow(idx, col.key, e.target.value)}
                            readOnly={!isAdmin}
                            style={{ ...sheetInput, cursor: isAdmin ? "text" : "default" }}
                          />
                        </td>
                      ))}
                      <td style={{ ...sheetCell, width: 54, borderRight: "none", textAlign: "center", background: "rgba(248,113,113,0.06)" }}>
                        <button
                          disabled={!isAdmin}
                          onClick={() => setTechRows((prev) => prev.filter((_, i) => i !== idx))}
                          style={{ width: "100%", padding: "9px 0", border: "none", background: "transparent", cursor: isAdmin ? "pointer" : "not-allowed", color: "#dc2626", fontWeight: 700, opacity: isAdmin ? 1 : 0.4 }}
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: 10, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>
                저장 위치: flow-data/waferlayout/product_tech_lists.csv
              </span>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                {techLoading && <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>loading...</span>}
                {techMsg && <span style={{ fontSize: 14, color: techMsg.includes("실패") ? "#dc2626" : "#16a34a" }}>{techMsg}</span>}
                <button disabled={!isAdmin} onClick={addTechRow}
                  style={{ padding: "6px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-card)", color: "var(--text-primary)", cursor: isAdmin ? "pointer" : "not-allowed", fontSize: 14, fontWeight: 700, opacity: isAdmin ? 1 : 0.5 }}>
                  + 행
                </button>
                <button disabled={!isAdmin} onClick={saveTechList}
                  style={{ padding: "6px 10px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", cursor: isAdmin ? "pointer" : "not-allowed", fontSize: 14, fontWeight: 700, opacity: isAdmin ? 1 : 0.5 }}>
                  저장
                </button>
              </div>
            </div>
          </div>}

          {showAdvanced && <div style={paneCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>Wafer / Shot Geometry</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <Input label="wafer_radius" value={cfg.waferRadius} onChange={(e) => setCfg((p) => ({ ...p, waferRadius: e.target.value }))} />
              <Input label="shot_pitch_x" value={cfg.shotPitchX} onChange={(e) => setCfg((p) => ({ ...p, shotPitchX: e.target.value }))} />
              <Input label="shot_pitch_y" value={cfg.shotPitchY} onChange={(e) => setCfg((p) => ({ ...p, shotPitchY: e.target.value }))} />
              <Input label="shot_size_x" value={cfg.shotSizeX} onChange={(e) => setCfg((p) => ({ ...p, shotSizeX: e.target.value }))} />
              <Input label="shot_size_y" value={cfg.shotSizeY} onChange={(e) => setCfg((p) => ({ ...p, shotSizeY: e.target.value }))} />
              <Input label="scribe_lane_x" value={cfg.scribeLaneX} onChange={(e) => setCfg((p) => ({ ...p, scribeLaneX: e.target.value }))} />
              <Input label="scribe_lane_y" value={cfg.scribeLaneY} onChange={(e) => setCfg((p) => ({ ...p, scribeLaneY: e.target.value }))} />
              <Input label="edge_exclusion_mm" value={cfg.edgeExclusionMm} onChange={(e) => setCfg((p) => ({ ...p, edgeExclusionMm: e.target.value }))} />
              <Input label="teg_size_x" value={cfg.tegSizeX} onChange={(e) => setCfg((p) => ({ ...p, tegSizeX: e.target.value }))} />
              <Input label="teg_size_y" value={cfg.tegSizeY} onChange={(e) => setCfg((p) => ({ ...p, tegSizeY: e.target.value }))} />
              <Input label="offset_x_mm" value={cfg.offsetXMm} onChange={(e) => setCfg((p) => ({ ...p, offsetXMm: e.target.value }))} />
              <Input label="offset_y_mm" value={cfg.offsetYMm} onChange={(e) => setCfg((p) => ({ ...p, offsetYMm: e.target.value }))} />
              <Input label="ref_shot_x" value={cfg.refShotX} onChange={(e) => setCfg((p) => ({ ...p, refShotX: e.target.value }))} />
              <Input label="ref_shot_y" value={cfg.refShotY} onChange={(e) => setCfg((p) => ({ ...p, refShotY: e.target.value }))} />
              <Input label="ref_center_x" value={cfg.refShotCenterX} onChange={(e) => setCfg((p) => ({ ...p, refShotCenterX: e.target.value }))} />
              <Input label="ref_center_y" value={cfg.refShotCenterY} onChange={(e) => setCfg((p) => ({ ...p, refShotCenterY: e.target.value }))} />
            </div>
          </div>}

          {showAdvanced && <div style={paneCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>Chip / TEG Geometry</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 10 }}>
              <Input label="chip_cols" value={cfg.chipCols} onChange={(e) => setCfg((p) => ({ ...p, chipCols: e.target.value }))} />
              <Input label="chip_rows" value={cfg.chipRows} onChange={(e) => setCfg((p) => ({ ...p, chipRows: e.target.value }))} />
              <Input label="chip_width" value={cfg.chipWidth} onChange={(e) => setCfg((p) => ({ ...p, chipWidth: e.target.value }))} />
              <Input label="chip_height" value={cfg.chipHeight} onChange={(e) => setCfg((p) => ({ ...p, chipHeight: e.target.value }))} />
              <Input label="chip_offset_x" value={cfg.chipOffsetX} onChange={(e) => setCfg((p) => ({ ...p, chipOffsetX: e.target.value }))} />
              <Input label="chip_offset_y" value={cfg.chipOffsetY} onChange={(e) => setCfg((p) => ({ ...p, chipOffsetY: e.target.value }))} />
            </div>
            <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
              <div style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>scribe_pattern (full = teg_h, half = teg_h/2)</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
                {normalizeScribePattern(cfg.scribePattern, cfg.chipRows).map((row, idx) => (
                  <label key={idx} style={{ display: "grid", gap: 4 }}>
                    <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>lane {idx}</span>
                    <select value={row.type} onChange={(e) => setCfg((p) => ({ ...p, scribePattern: normalizeScribePattern(p.scribePattern, p.chipRows).map((lane, laneIdx) => laneIdx === idx ? { ...lane, type: e.target.value === "half" ? "half" : "full" } : lane) }))} style={S}>
                      <option value="full">full</option>
                      <option value="half">half</option>
                    </select>
                  </label>
                ))}
              </div>
            </div>
            <label style={{ display: "grid", gap: 4, marginBottom: 10 }}>
              <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>chip_origin_mode</span>
              <select value={cfg.chipOrigin} onChange={(e) => setCfg((p) => ({ ...p, chipOrigin: e.target.value }))} style={S}>
                <option value="shot_lower_left">shot lower-left = 0,0</option>
                <option value="shot_center">shot center = 0,0</option>
              </select>
            </label>
            <label style={{ display: "grid", gap: 4, marginTop: 10 }}>
              <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>TEG search</span>
              <input value={tegSearch} onChange={(e) => setTegSearch(e.target.value)} placeholder="예: 101 / TOP / RIGHT" style={S} />
            </label>
          </div>}

          {showAdvanced && <div style={paneCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>Quick Reading</div>
            <div style={{ display: "grid", gap: 6, fontSize: 14, lineHeight: 1.65, color: "var(--text-secondary)" }}>
              <div>ref shot `{cfg.refShotX},{cfg.refShotY}` 의 center 가 `{cfg.refShotCenterX},{cfg.refShotCenterY}` 로 놓이고, 나머지 shot은 pitch 기준으로 펼쳐집니다.</div>
              <div>shot view는 usable shot만 봅니다. wafer 안에 들어와도 끝 3mm exclusion에 닿는 shot은 비사용 shot으로 취급합니다.</div>
              <div>shot view(all)은 wafer에 걸리는 shot까지 모두 보고, full square shot grid 기준 좌상단 `(1,1)` 번호를 붙입니다.</div>
              <div>chip view는 usable chip 과 edge-cut chip 을 같이 보여주며, edge-cut chip 은 빨간색으로 강조합니다.</div>
              <div>Shot View(All) 좌표와 Chip View 좌표는 둘 다 full square grid 기준이며, 좌상단이 `1,1` 입니다.</div>
              <div>chip 좌표는 wafer 전체 chip grid 에 `chip_offset_x/y` 를 더한 값입니다.</div>
            </div>
          </div>}

          {showAdvanced && <div style={paneCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>Recommended Input Rule</div>
            <div style={{ display: "grid", gap: 6, fontSize: 14, lineHeight: 1.7, color: "var(--text-secondary)" }}>
              <div>1. wafer center 는 가능하면 `(0,0)` 으로 둡니다.</div>
              <div>2. shot 은 `pitch` 기준 셀로 배치하고, `shot_size` 는 거의 pitch와 같게 두어 shot이 거의 붙어 보이게 합니다.</div>
              <div>3. shot은 wafer edge exclusion을 적용해 usable shot을 정하고, Shot View(All)에서는 edge/partial shot도 같이 봅니다.</div>
              <div>4. TEG 좌표가 있으면 scribe lane의 lower-left representative 로 보고, `teg_size_x/y`로 footprint를 판단합니다.</div>
              <div>5. 선택한 TEG 조합이 edge shot에서도 공통으로 다 들어가는지 계산할 수 있습니다.</div>
            </div>
          </div>}

          {showAdvanced && <div style={paneCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>TEG Coordinate Guide</div>
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(59,130,246,0.18)", background: "rgba(59,130,246,0.06)", fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.7 }}>
                Shot 중심을 원점 `(0,0)` 으로 둡니다. `dx_mm` 는 오른쪽이 `+`, `dy_mm` 는 위쪽이 `+` 입니다.
              </div>
              <svg viewBox="0 0 180 120" width="100%" style={{ borderRadius: 10, border: "1px solid var(--border)", background: "var(--bg-card)" }}>
                <rect x="36" y="24" width="92" height="68" rx="8" fill="rgba(37,99,235,0.06)" stroke="rgba(37,99,235,0.35)" />
                <line x1="82" y1="18" x2="82" y2="100" stroke="rgba(15,23,42,0.25)" strokeDasharray="4,4" />
                <line x1="26" y1="58" x2="138" y2="58" stroke="rgba(15,23,42,0.25)" strokeDasharray="4,4" />
                <line x1="82" y1="58" x2="118" y2="58" stroke="#1d4ed8" strokeWidth="2" />
                <polygon points="118,58 110,54 110,62" fill="#1d4ed8" />
                <line x1="82" y1="58" x2="82" y2="28" stroke="#f97316" strokeWidth="2" />
                <polygon points="82,28 78,36 86,36" fill="#f97316" />
                <text x="122" y="54" fontSize="10" fill="#1d4ed8" style={{ fontFamily: "monospace", fontWeight: 700 }}>dx_mm +</text>
                <text x="88" y="24" fontSize="10" fill="#c2410c" style={{ fontFamily: "monospace", fontWeight: 700 }}>dy_mm +</text>
                <text x="86" y="72" fontSize="10" fill="var(--text-secondary)" style={{ fontFamily: "monospace" }}>(0,0)</text>
              </svg>
            </div>
          </div>}

          {showAdvanced && isAdmin && (
            <div style={paneCard}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>TEG Table</div>
                <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>product 선택 후 제품별 TEG를 저장합니다</span>
              </div>
              <div style={{ ...sheetWrap, maxHeight: 300 }} onPaste={handleTegPaste}>
                <table style={{ width: "100%", borderCollapse: "separate", borderSpacing: 0 }}>
                  <thead>
                    <tr>
                      {["id", "label", "dx_mm", "dy_mm", ""].map((h) => (
                        <th key={h} style={{ ...sheetHead, borderRight: h ? "1px solid var(--border)" : "none" }}>{h || "삭제"}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tegRows.map((row, idx) => (
                      <tr key={row.id || idx}>
                        <td style={{ ...sheetCell, width: 100 }}><input value={row.id} onChange={(e) => setTegRows((prev) => prev.map((r, i) => i === idx ? { ...r, id: e.target.value } : r))} style={sheetInput} /></td>
                        <td style={{ ...sheetCell, minWidth: 150 }}><input value={row.name} onChange={(e) => setTegRows((prev) => prev.map((r, i) => i === idx ? { ...r, name: e.target.value } : r))} style={sheetInput} /></td>
                        <td style={{ ...sheetCell, width: 110 }}><input value={row.x} onChange={(e) => setTegRows((prev) => prev.map((r, i) => i === idx ? { ...r, x: e.target.value } : r))} style={sheetInput} /></td>
                        <td style={{ ...sheetCell, width: 110 }}><input value={row.y} onChange={(e) => setTegRows((prev) => prev.map((r, i) => i === idx ? { ...r, y: e.target.value } : r))} style={sheetInput} /></td>
                        <td style={{ ...sheetCell, width: 60, borderRight: "none", textAlign: "center", background: "rgba(248,113,113,0.06)" }}>
                          <button onClick={() => setTegRows((prev) => prev.filter((_, i) => i !== idx))} style={{ width: "100%", padding: "9px 0", border: "none", background: "transparent", cursor: "pointer", color: "#dc2626", fontWeight: 700 }}>✕</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end" }}>
                <button
                  onClick={() => setTegRows((prev) => [...prev, { id: `TEG_${prev.length + 1}`, no: prev.length + 1, name: `TEG_${prev.length + 1}`, x: 0, y: 0, flat: 0 }])}
                  style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)", cursor: "pointer", fontWeight: 700 }}
                >
                  + TEG 추가
                </button>
              </div>
            </div>
          )}

          {showAdvanced && <div style={paneCard}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>Selected TEG Check</div>
            <div style={{ display: "grid", gap: 6, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.7 }}>
              {sampleSelectedTeg ? (
                <>
                  <div>sample shot: {shotLabel(data.shotSample)}</div>
                  <div>TEG: {sampleSelectedTeg.id || sampleSelectedTeg.no}:{sampleSelectedTeg.name}</div>
                  <div>shot 좌하단까지 거리: {fmt(sampleSelectedTeg.lowerLeftDist)} mm</div>
                  <div>TEG size: {fmt(data.cfg.tegSizeX)} x {fmt(data.cfg.tegSizeY)} mm</div>
                  <div>3mm usable band: {sampleSelectedTeg.usable ? "inside" : "out / overlap"}</div>
                </>
              ) : (
                <div>TEG를 하나 이상 선택하면 샘플 shot 기준 거리와 3mm band 판정을 보여줍니다.</div>
              )}
              {viewMode === "shot_all" && (
                <>
                  <div style={{ display: "grid", gap: 6 }}>
                    <div style={{ fontSize: 14, color: "var(--text-secondary)" }}>Shot View All 에서 edge shot 후보를 계산할 TEG를 선택합니다.</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {tegRows.map((row) => (
                        <label key={row.id} style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "6px 8px", borderRadius: 999, border: "1px solid var(--border)", background: selectedTegSet.has(Number(row.no)) ? "rgba(249,115,22,0.10)" : "var(--bg-card)", cursor: "pointer" }}>
                          <input type="checkbox" checked={selectedTegSet.has(Number(row.no))} onChange={() => toggleTegNo(row.no)} />
                          <span style={{ fontSize: 14, fontFamily: "monospace" }}>{row.id || row.name}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                  <div>edge candidate shot count: {edgeShotCoverage.edgeShots}</div>
                  <div>완전 내부 shot 은 제외하고, 선택한 TEG가 모두 3mm usable band 안에 들어가는 edge shot만 남깁니다.</div>
                </>
              )}
            </div>
            {!!selectedTegDefs.length && viewMode === "shot_all" && (
              <div style={{ marginTop: 10, maxHeight: 220, overflow: "auto", border: "1px solid var(--border)", borderRadius: 10, background: "rgba(255,255,255,0.6)" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["Grid Shot", "Raw Shot", "TEG IDs"].map((h) => (
                        <th key={h} style={{ textAlign: "left", padding: "6px 8px", fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: "var(--bg-secondary)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(edgeShotCoverage.rows || []).slice(0, 60).map((row, idx) => (
                      <tr key={idx}>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace", fontWeight: 700 }}>{row.shotX && row.shotY ? `${row.shotY},${row.shotX}` : "-"}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>{row.rawShotX},{row.rawShotY}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace", color: "var(--text-secondary)" }}>
                          {(row.tegIds || []).join(", ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(380px, 1fr) minmax(360px, 0.95fr)", gap: 12, minWidth: 0, gridColumn: 1, gridRow: 1, alignItems: "start" }}>
          <div style={{ ...paneCard, overflow: "hidden" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>WF View</div>
            <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", borderRadius: 12, background: "linear-gradient(180deg, rgba(59,130,246,0.03), rgba(15,23,42,0.02))" }}>
              <defs>
                <clipPath id="wfClip">
                  <circle cx={W / 2} cy={H / 2} r={Math.max(0, (data.cfg.waferRadius - data.cfg.edgeExclusionMm) * scale)} />
                </clipPath>
              </defs>
              <circle cx={W / 2} cy={H / 2} r={data.cfg.waferRadius * scale} fill="rgba(37,99,235,0.04)" stroke="rgba(37,99,235,0.35)" strokeWidth="2" />
              <line x1={W / 2 - 14} y1={H / 2} x2={W / 2 + 14} y2={H / 2} stroke="rgba(15,23,42,0.25)" strokeDasharray="4,4" />
              <line x1={W / 2} y1={H / 2 - 14} x2={W / 2} y2={H / 2 + 14} stroke="rgba(15,23,42,0.25)" strokeDasharray="4,4" />
              {(viewMode === "shot" ? data.shots.filter((s) => s.usableShot) : viewMode === "shot_all" ? shotAllDisplayRows : []).map((s, idx) => {
                const x = px(s.shotBody.x);
                const y = py(s.shotBody.y + s.shotBody.h);
                const w = s.shotBody.w * scale;
                const h = s.shotBody.h * scale;
                const isRef = s.shotX === data.cfg.refShotX && s.shotY === data.cfg.refShotY;
                const edgeCandidate = edgeCandidateMap.get(`${s.shotX}|${s.shotY}`);
                const isHighlightedEdge = viewMode === "shot_all" && !!edgeCandidate;
                const tegTooltip = edgeCandidate?.teg_ids?.length ? `이 shot 은 TEG ${edgeCandidate.teg_ids.join(", ")} 가 모두 3mm 안에 들어갑니다` : shotLabel(s);
                return (
                  <g key={idx}>
                    <rect
                      x={x}
                      y={y}
                      width={w}
                      height={h}
                      rx="2"
                      fill={isHighlightedEdge ? "rgba(249,115,22,0.18)" : isRef ? "rgba(249,115,22,0.20)" : s.usableShot ? "rgba(15,118,110,0.08)" : "rgba(239,68,68,0.08)"}
                      stroke={isHighlightedEdge ? "#f97316" : isRef ? "#f97316" : s.usableShot ? "rgba(15,118,110,0.35)" : "rgba(220,38,38,0.45)"}
                      strokeWidth={isHighlightedEdge ? "2.2" : isRef ? "1.8" : s.usableShot ? "0.8" : "1.0"}
                      strokeDasharray={isHighlightedEdge ? "none" : s.usableShot ? "none" : "4,3"}
                    >
                      <title>{tegTooltip}</title>
                    </rect>
                    <text x={px(s.center.x)} y={py(s.center.y)} textAnchor="middle" dominantBaseline="middle" fontSize={isHighlightedEdge ? "9" : "8"} fill={isHighlightedEdge ? "#c2410c" : "var(--text-secondary)"} style={{ fontFamily: "monospace", fontWeight: isHighlightedEdge ? 800 : 400 }}>
                      {`${s.gridShotY},${s.gridShotX}`}
                    </text>
                  </g>
                );
              })}
              {viewMode === "chip" ? <g clipPath="url(#wfClip)">{filteredChips.slice(0, 2200).map((c, idx) => {
                const isCenterChip = `${c.globalChipX}|${c.globalChipY}` === centerChipKey;
                return (
                  <rect
                    key={idx}
                    x={px(c.x)}
                    y={py(c.y + c.h)}
                    width={Math.max(0.8, c.w * scale - 0.15)}
                    height={Math.max(0.8, c.h * scale - 0.15)}
                    fill={isCenterChip ? "rgba(249,115,22,0.34)" : c.edgeCut ? "rgba(239,68,68,0.82)" : c.fullShot ? "rgba(99,102,241,0.10)" : "rgba(99,102,241,0.22)"}
                    stroke={isCenterChip ? "#f97316" : c.edgeCut ? "#991b1b" : c.fullShot ? "rgba(99,102,241,0.15)" : "rgba(67,56,202,0.45)"}
                    strokeWidth={isCenterChip ? "1.8" : c.edgeCut ? "0.6" : c.fullShot ? "0.2" : "0.4"}
                  >
                    <title>{`${shotLabel(c)} / center (${fmt(c.centerX)}, ${fmt(c.centerY)}) mm / edge X ${fmt(c.x)}~${fmt(c.x + c.w)}, Y ${fmt(c.y)}~${fmt(c.y + c.h)}`}</title>
                  </rect>
                );
              })}</g> : null}
              <circle cx={W / 2} cy={H / 2} r={Math.max(0, (data.cfg.waferRadius - data.cfg.edgeExclusionMm) * scale)} fill="none" stroke="rgba(249,115,22,0.35)" strokeWidth="1.2" strokeDasharray="6,4" />
              {viewMode !== "chip" && (
                <text x={24} y={32} fontSize="10" fill="var(--text-secondary)" style={{ fontFamily: "monospace" }}>
                  TEG overlay는 Shot Sample에서만 표시됩니다.
                </text>
              )}
              <text x={W / 2 + 8} y={H / 2 - 8} fontSize="10" fill="#1d4ed8" style={{ fontFamily: "monospace", fontWeight: 700 }}>WF Center</text>
            </svg>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 12, minWidth: 0 }}>
            <div style={{ ...paneCard, overflow: "hidden" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>Shot Sample</div>
              {data.shotSample ? (
                <svg viewBox={`0 0 ${shotSampleViewW} ${Math.max(300, shotSampleH + 78)}`} width="100%" style={{ display: "block" }}>
                  <rect x={shotSampleX} y={shotSampleY} width={shotSampleW} height={shotSampleH} rx="6" fill="rgba(15,23,42,0.03)" stroke="rgba(15,23,42,0.15)" />
                  <rect x={bodyX} y={bodyY} width={bodyW} height={bodyH} rx="6" fill="rgba(15,118,110,0.08)" stroke="rgba(15,118,110,0.45)" />
                  <line x1={shotSampleX + shotSampleW / 2} y1={shotSampleY} x2={shotSampleX + shotSampleW / 2} y2={shotSampleY + shotSampleH} stroke="rgba(15,23,42,0.22)" strokeDasharray="4,4" />
                  <line x1={shotSampleX} y1={shotSampleY + shotSampleH / 2} x2={shotSampleX + shotSampleW} y2={shotSampleY + shotSampleH / 2} stroke="rgba(15,23,42,0.22)" strokeDasharray="4,4" />
                  {(() => {
                    const layoutW = Math.max(0.1, shotSamplePreview.stackWidth);
                    const layoutH = Math.max(0.1, shotSamplePreview.stackHeight);
                    const unitX = bodyW / Math.max(layoutW, data.cfg.shotSizeX || layoutW);
                    const unitY = bodyH / Math.max(layoutH, data.cfg.shotSizeY || layoutH);
                    const contentW = layoutW * unitX;
                    const contentH = layoutH * unitY;
                    const startX = bodyX + (bodyW - contentW) / 2;
                    const startY = bodyY + (bodyH - contentH) / 2;
                    return (
                      <>
                        {shotSamplePreview.laneRows.map((row) => {
                          const y = startY + contentH - (row.yMm + row.mmHeight) * unitY;
                          const h = Math.max(1, row.mmHeight * unitY);
                          return (
                            <g key={`${row.kind}-${row.index}`}>
                              <rect
                                x={startX}
                                y={y}
                                width={contentW}
                                height={h}
                                fill={row.kind === "scribe" ? (row.type === "half" ? "rgba(249,115,22,0.10)" : "rgba(37,99,235,0.10)") : "transparent"}
                                stroke={row.kind === "scribe" ? (row.type === "half" ? "rgba(249,115,22,0.35)" : "rgba(37,99,235,0.28)") : "none"}
                                strokeDasharray={row.kind === "scribe" && row.type === "half" ? "4,3" : "none"}
                              />
                              {row.kind === "scribe" && (
                                <text x={startX + 6} y={y + Math.max(10, h - 4)} fontSize="8" fill={row.type === "half" ? "#b45309" : "#1d4ed8"} style={{ fontFamily: "monospace", fontWeight: 700 }}>
                                  {`lane${row.index}:${row.type}`}
                                </text>
                              )}
                            </g>
                          );
                        })}
                        {Array.from({ length: data.cfg.chipRows }).flatMap((_, ry) =>
                          Array.from({ length: data.cfg.chipCols }).map((__, rx) => {
                            const chipRow = shotSamplePreview.laneRows.find((row) => row.kind === "chip" && row.index === ry);
                            if (!chipRow) return null;
                            const x = startX + rx * data.cfg.chipWidth * unitX + 1;
                            const y = startY + contentH - (chipRow.yMm + chipRow.mmHeight) * unitY + 1;
                            const w = Math.max(1, data.cfg.chipWidth * unitX - 2);
                            const h = Math.max(1, data.cfg.chipHeight * unitY - 2);
                            return <rect key={`${rx}-${ry}`} x={x} y={y} width={w} height={h} fill="rgba(99,102,241,0.14)" stroke="rgba(99,102,241,0.30)" />;
                          })
                        )}
                        {data.tegs
                          .filter((teg) => {
                            if (selectedTegSet.size && selectedTegSet.has(Number(teg.no))) return true;
                            if (!tegSearch) return false;
                            return String(teg.name || "").toLowerCase().includes(String(tegSearch).toLowerCase()) || String(teg.no || "").includes(String(tegSearch));
                          })
                          .map((t) => {
                            const rotated = Number(t.flat) === 90;
                            const twMm = rotated ? data.cfg.tegSizeY : data.cfg.tegSizeX;
                            const thMm = rotated ? data.cfg.tegSizeX : data.cfg.tegSizeY;
                            const localX = Number(t.x || 0) + data.cfg.offsetXMm;
                            const localY = Number(t.y || 0) + data.cfg.offsetYMm;
                            const tx = data.cfg.chipOrigin === "shot_center"
                              ? bodyX + bodyW / 2 + localX * (bodyW / Math.max(1, data.cfg.shotSizeX))
                              : bodyX + localX * (bodyW / Math.max(1, data.cfg.shotSizeX));
                            const ty = data.cfg.chipOrigin === "shot_center"
                              ? bodyY + bodyH / 2 - (localY + thMm) * (bodyH / Math.max(1, data.cfg.shotSizeY))
                              : bodyY + bodyH - (localY + thMm) * (bodyH / Math.max(1, data.cfg.shotSizeY));
                            const tw = Math.max(4, twMm * (bodyW / Math.max(1, data.cfg.shotSizeX)));
                            const th = Math.max(3, thMm * (bodyH / Math.max(1, data.cfg.shotSizeY)));
                            const active = selectedTegSet.has(Number(t.no));
                            const inBody = tx >= bodyX && tx + tw <= bodyX + bodyW && ty >= bodyY && ty + th <= bodyY + bodyH;
                            return (
                              <g key={t.id} onClick={() => toggleTegNo(t.no)} style={{ cursor: "pointer" }}>
                                <rect x={tx} y={ty} width={tw} height={th} fill={active ? "rgba(249,115,22,0.82)" : inBody ? "rgba(16,185,129,0.58)" : "rgba(239,68,68,0.60)"} stroke={active ? "#c2410c" : inBody ? "#065f46" : "#991b1b"} strokeWidth={active ? 2 : 1} />
                                <circle cx={tx} cy={ty + th} r={active ? 3.6 : 2.6} fill={active ? "#c2410c" : "#111827"} />
                                <text x={Math.min(tx + tw + 5, bodyX + bodyW - 74)} y={Math.max(ty - 5, bodyY + 10)} fontSize={active ? "10" : "9"} fill={active ? "#c2410c" : inBody ? "#065f46" : "#991b1b"} style={{ fontFamily: "monospace", fontWeight: active ? 800 : 700 }}>
                                  {`${t.no}:${t.name}`}
                                </text>
                                <title>{`${t.name} local x=${fmt(localX)} y=${fmt(localY)} · ${inBody ? "inside shot body" : "outside shot body"}`}</title>
                              </g>
                            );
                          })}
                      </>
                    );
                  })()}
                  <text x={shotSampleX + 6} y="18" fontSize="10" fill="var(--text-secondary)" style={{ fontFamily: "monospace" }}>pitch cell vs shot body size ratio</text>
                  <text x={shotSampleX + shotSampleW / 2 + 4} y={shotSampleY + shotSampleH / 2 - 6} fontSize="10" fill="#1d4ed8" style={{ fontFamily: "monospace", fontWeight: 700 }}>
                    {shotLabel(data.shotSample)}
                  </text>
                  <text x={shotSampleX + 6} y={Math.max(280, shotSampleH + 36)} fontSize="9" fill="#065f46" style={{ fontFamily: "monospace" }}>
                    {`chips ${data.cfg.chipCols}x${data.cfg.chipRows} / chip ${fmt(data.cfg.chipWidth)}x${fmt(data.cfg.chipHeight)} mm / stack ${fmt(shotSamplePreview.stackHeight)} mm`}
                  </text>
                  <text x={shotSampleX + 6} y={Math.max(294, shotSampleH + 54)} fontSize="9" fill="#92400e" style={{ fontFamily: "monospace" }}>
                    TEG rectangle is projected from exact local x/y into the shot body. Click a TEG to keep it selected.
                  </text>
                </svg>
              ) : (
                <div style={{ fontSize: 14, color: "var(--text-secondary)" }}>shot sample 없음</div>
              )}
            </div>

            <div style={{ ...paneCard, overflow: "hidden" }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", marginBottom: 10 }}>TEG Catalog</div>
              <div style={{ maxHeight: 300, overflow: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["no", "name", "local_x", "local_y", "flat"].map((h) => (
                        <th key={h} style={{ textAlign: "left", padding: "6px 8px", fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: "var(--bg-secondary)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.tegs.map((t) => {
                      const active = selectedTegSet.has(Number(t.no));
                      return (
                      <tr key={t.id} onClick={() => toggleTegNo(t.no)} style={{ cursor: "pointer", background: active ? "var(--accent-glow)" : "transparent" }}>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace", color: active ? "var(--accent)" : "#1d4ed8", fontWeight: 800 }}>{active ? "● " : ""}{t.no}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace", color: active ? "var(--accent)" : "#991b1b", fontWeight: 700 }}>{t.name}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>{fmt(t.x)}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>{fmt(t.y)}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>{t.flat||0}</td>
                      </tr>
                    );})}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div style={{ ...paneCard, overflow: "hidden", gridColumn: "1 / -1" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace" }}>
                {viewMode === "chip" ? "Chip -> Shot Map" : viewMode === "shot_all" ? "Shot View(All) Coverage" : "Shot Summary"}
              </div>
              {viewMode === "chip" && (
                <button
                  onClick={() => downloadRowsCsv(`wf_chip_shot_map_${product || "product"}.csv`, chipMappingRows, chipCsvColumns)}
                  style={{ padding: "5px 10px", borderRadius: 4, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", cursor: "pointer", fontSize: 14, fontWeight: 700 }}
                >
                  CSV
                </button>
              )}
            </div>
            {viewMode === "chip" ? (
              <div style={{ maxHeight: 360, overflow: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["Chip X", "Chip Y", "Shot X", "Shot Y", "Status"].map((h) => (
                        <th key={h} style={{ textAlign: "left", padding: "6px 8px", fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: "var(--bg-secondary)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {chipMappingRows.map((row, idx) => (
                      <tr key={idx}>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace", fontWeight: 700 }}>{row.chipX}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace", fontWeight: 700 }}>{row.chipY}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>{row.shotX || "-"}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace" }}>{row.shotY || "-"}</td>
                        <td style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14, fontFamily: "monospace", color: row.status === "inside" ? "#0f766e" : "#dc2626", fontWeight: 700 }}>{row.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : viewMode === "shot_all" ? (
              <div style={{ display: "grid", gap: 6, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.7 }}>
                <div>wafer에 걸리는 전체 shot: {data.shots.length}</div>
                <div>usable shot: {data.shots.filter((s) => s.usableShot).length}</div>
                <div>partial/edge shot: {shotAllRows.length}</div>
                <div>표시 좌표는 full square shot grid 기준이며 좌상단이 1,1 입니다.</div>
                {!!selectedTegDefs.length && <div>선택 TEG 조합이 모두 3mm 안에 들어가는 edge shot: {edgeShotCoverage.coveredShots}</div>}
                {!!selectedTegDefs.length && <div>현재 WF View는 해당 edge shot만 남긴 상태입니다.</div>}
                {!selectedTegDefs.length && <div>TEG를 선택하면 edge shot 중 선택 TEG가 모두 살아남는 shot만 남깁니다.</div>}
              </div>
            ) : (
              <div style={{ display: "grid", gap: 6, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.7 }}>
                <div>Shot View는 공정상 바로 쓰는 usable shot만 보여줍니다.</div>
                <div>Edge/partial shot 검토는 `Shot View(All)`에서 확인합니다.</div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
