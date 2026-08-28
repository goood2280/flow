import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { postJson, putJson, sf } from "../../lib/api";
import { toast } from "../../components/Toast";
import { Button, Card, EmptyState, Input, Pill, Select, TabStrip } from "../../components/UXKit";
import PageGear from "../../components/PageGear";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows } from "../../components/SpreadsheetPasteGrid";


const API = "/api/yield-map";
const FALLBACK_COLOR = "#D1D5DB";
const BIN_TABLE_VISIBLE_ROWS = 10;
const BIN_GRID_COLUMNS = ["bin", "color"];
const DEFAULT_MAP_COLUMNS = 5;
const MAP_COLUMNS_STORAGE_KEY = "flow:yieldmap:columns";
const DEFAULT_SHOT_LAYOUT = { enabled: false, cols: 1, rows: 1, origin_x: 0, origin_y: 0, good_bins: ["1"] };
const AUTO_BIN_COLORS = ["#22C55E", "#EF4444", "#3B82F6", "#F59E0B", "#8B5CF6", "#06B6D4", "#EC4899", "#64748B"];
const DATA_KIND_OPTIONS = [
  ["yield", "Yield · BIN/Die"], ["et", "ET · Shot"], ["inline", "Inline · subitem_id"],
];
const FIELD_LABELS = [
  ["x", "chip X (chip_x_pos)"], ["y", "chip Y (chip_y_pos)"], ["bin", "BIN"],
  ["lot", "ROOT LOT ID"], ["wafer", "WAFER ID"],
];
const SHOT_FIELD_LABELS = [
  ["lot", "ROOT LOT ID"], ["wafer", "WAFER ID"],
  ["shot_x", "Shot X 좌표"], ["shot_y", "Shot Y 좌표"],
  ["value", "측정값"], ["item", "ITEM ID"], ["subitem", "SUBITEM ID"],
  ["step", "STEP ID"], ["step_seq", "STEP SEQ"], ["tkout", "TKOUT TIME"],
  ["split", "SPLIT"],
];
const inputLabel = { fontSize: 12, color: "var(--muted)", display: "flex", flexDirection: "column", gap: 4 };
function safeColor(value) {
  const text = String(value || "").toUpperCase();
  return /^#[0-9A-F]{6}$/.test(text) ? text : FALLBACK_COLOR;
}

function initialMapColumns() {
  try {
    const value = Number(window.localStorage.getItem(MAP_COLUMNS_STORAGE_KEY));
    return value >= 3 && value <= 10 ? value : DEFAULT_MAP_COLUMNS;
  } catch {
    return DEFAULT_MAP_COLUMNS;
  }
}

function shotLayoutFromConfig(config) {
  const value = config?.shot_layout || {};
  return { ...DEFAULT_SHOT_LAYOUT, ...value, good_bins: Array.isArray(value.good_bins) ? value.good_bins : DEFAULT_SHOT_LAYOUT.good_bins };
}

function binRowsFromColors(colors) {
  const rows = Object.entries(colors || {}).map(([bin, color]) => ({ bin, color: String(color || "").toUpperCase() }));
  return rows.length ? rows : [{ bin: "", color: "#94A3B8" }];
}

function binRowsFromConfig(config) {
  if (Array.isArray(config?.bin_map) && config.bin_map.length) {
    return config.bin_map.map(row => ({
      bin: String(row?.bin || ""),
      color: String(row?.bin_color || row?.color || "#94A3B8").toUpperCase(),
    }));
  }
  return binRowsFromColors(config?.bin_colors);
}

function binColorsFromRows(rows) {
  const out = {};
  for (const row of rows || []) {
    const bin = String(row.bin || "").trim();
    const color = String(row.color || "").trim().toUpperCase();
    if (bin && /^#[0-9A-F]{6}$/.test(color)) out[bin] = color;
  }
  return out;
}

function spreadsheetBinRows(rows) {
  return normalizeSpreadsheetRows(rows, BIN_GRID_COLUMNS, { minRows: BIN_TABLE_VISIBLE_ROWS, maxRows: 200 });
}

function naturalTextCompare(left, right) {
  return String(left || "").localeCompare(String(right || ""), undefined, { numeric: true, sensitivity: "base" });
}

function normalizedProductKey(value) {
  return String(value || "").trim().toLowerCase()
    .replace(/^product=/, "")
    .replace(/^(?:ml|et|inline)_table_/, "")
    .replace(/[\s_-]+/g, "");
}

function geometryForProduct(catalog, product) {
  const exact = (catalog || []).find(row => String(row.vehicle || "").toLowerCase() === String(product || "").toLowerCase());
  if (exact) return exact;
  const key = normalizedProductKey(product);
  return key ? (catalog || []).find(row => normalizedProductKey(row.vehicle) === key) || null : null;
}

function matchingValues(rows, key) {
  return [...new Set((rows || []).map(row => String(row?.[key] || "").trim()).filter(Boolean))].sort(naturalTextCompare);
}

function rulesForProduct(rows, product) {
  return (rows || []).filter(row => String(row.product || "").toLowerCase() === String(product || "").toLowerCase());
}

function rulesForValue(rows, key, value) {
  return (rows || []).filter(row => String(row[key] || "").toLowerCase() === String(value || "").toLowerCase());
}

function ruleUsable(rule, vehicle) {
  return !!rule?.available && String(rule.vehicle || "").toLowerCase() === String(vehicle || "").toLowerCase();
}

function waferTrellisFromMap(map) {
  if (!map) return [];
  const groups = new Map();
  const ensure = value => {
    const waferId = String(value ?? "").trim();
    if (!groups.has(waferId)) groups.set(waferId, { waferId, rows: [], shots: [] });
    return groups.get(waferId);
  };
  (map.wafer_ids || []).forEach(ensure);
  (map.rows || []).forEach(row => ensure(row.wafer).rows.push(row));
  (map.shot_rows || []).forEach(shot => ensure(shot.wafer).shots.push(shot));
  return [...groups.values()]
    .sort((a, b) => naturalTextCompare(a.waferId, b.waferId))
    .map(group => ({ ...group, label: group.waferId || "(미지정)" }));
}

function axisModel(values) {
  const unique = [...new Set(values)].sort((a, b) => a - b);
  if (!unique.length) return null;
  const diffs = unique.slice(1).map((value, index) => value - unique[index]).filter(value => value > 1e-9);
  const step = unique.every(Number.isInteger) ? 1 : (diffs.length ? Math.min(...diffs) : 1);
  let indexes = unique.map(value => Math.round((value - unique[0]) / step));
  let count = (indexes[indexes.length - 1] || 0) + 1;
  // 비정상적으로 큰 좌표 간격은 SVG 폭을 폭발시키지 않고 순서만 보존한다.
  if (count > 500) {
    indexes = unique.map((_, index) => index);
    count = unique.length;
  }
  return { min: unique[0], max: unique[unique.length - 1], count,
    index: new Map(unique.map((value, i) => [value, indexes[i]])) };
}


function YieldDieMap({ rows, colors, shots = [] }) {
  const SIZE = 700, PAD = 38;
  const model = useMemo(() => {
    if (!rows?.length) return null;
    const xs = rows.map(row => Number(row.x)).filter(Number.isFinite);
    const ys = rows.map(row => Number(row.y)).filter(Number.isFinite);
    if (!xs.length || !ys.length) return null;
    const xAxis = axisModel(xs), yAxis = axisModel(ys);
    if (!xAxis || !yAxis) return null;
    const n = Math.max(xAxis.count, yAxis.count, 1);
    const cell = Math.max(1.4, (SIZE - PAD * 2) / n);
    const width = xAxis.count * cell, height = yAxis.count * cell;
    const ox = (SIZE - width) / 2, oy = (SIZE - height) / 2;
    const status = new Map(shots.map(shot => [`${shot.shot_x}:${shot.shot_y}`, shot]));
    const grouped = new Map();
    rows.forEach(row => {
      if (row.shot_x == null || row.shot_y == null) return;
      const key = `${row.shot_x}:${row.shot_y}`;
      const box = grouped.get(key) || { key, shot_x: row.shot_x, shot_y: row.shot_y, xs: [], ys: [], status: status.get(key) };
      box.xs.push(Number(row.x)); box.ys.push(Number(row.y)); grouped.set(key, box);
    });
    const shotBoxes = [...grouped.values()].map(box => ({
      ...box,
      x0: xAxis.index.get(Math.min(...box.xs)), x1: xAxis.index.get(Math.max(...box.xs)),
      y0: yAxis.index.get(Math.min(...box.ys)), y1: yAxis.index.get(Math.max(...box.ys)),
    }));
    return { xAxis, yAxis, cell, width, height, ox, oy, shotBoxes };
  }, [rows, shots]);
  if (!model) return <EmptyState icon="◫" title="표시할 die 좌표가 없습니다" />;
  const { xAxis, yAxis, cell, width, height, ox, oy, shotBoxes } = model;
  return (
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}
      style={{ width: "100%", maxWidth: 700, height: "auto", background: "var(--bg-card)", border: "1px solid var(--line)", borderRadius: 8 }}>
      <ellipse cx={SIZE / 2} cy={SIZE / 2} rx={width / 2 + cell * 0.8} ry={height / 2 + cell * 0.8}
        fill="none" stroke="var(--muted)" strokeWidth="1" opacity="0.6" />
      {rows.map((row, index) => {
        const xi = xAxis.index.get(Number(row.x)), yi = yAxis.index.get(Number(row.y));
        if (xi == null || yi == null) return null;
        // chip_x_pos 최소값은 좌측, chip_y_pos 최소값은 상단. X는 우측으로,
        // Y는 아래쪽으로 갈수록 커지는 BIN DB 좌표계를 그대로 사용한다.
        const x = ox + xi * cell, y = oy + yi * cell;
        const color = safeColor(colors?.[String(row.bin)]);
        return (
          <rect key={`${row.x}:${row.y}:${index}`} x={x} y={y}
            width={Math.max(0.8, cell - 0.5)} height={Math.max(0.8, cell - 0.5)}
            fill={color} stroke="rgba(15,23,42,0.28)" strokeWidth="0.35">
            <title>{`die (${row.x}, ${row.y})${row.shot_x != null ? `\nshot (${row.shot_x}, ${row.shot_y}) · in-shot (${row.die_x_in_shot}, ${row.die_y_in_shot})` : ""}\nBIN: ${row.bin || "(빈 값)"}${row.lot != null ? `\nLOT: ${row.lot}` : ""}${row.wafer != null ? `\nWAFER: ${row.wafer}` : ""}`}</title>
          </rect>
        );
      })}
      {shotBoxes.map(box => box.x0 == null || box.y0 == null ? null : <rect key={`shot-${box.key}`}
        x={ox + box.x0 * cell - 0.3} y={oy + box.y0 * cell - 0.3}
        width={(box.x1 - box.x0 + 1) * cell} height={(box.y1 - box.y0 + 1) * cell}
        fill="none" stroke={box.status?.is_full_shot ? "#0F172A" : "#F59E0B"}
        strokeWidth={box.status?.is_full_shot ? 1.15 : 0.8} strokeDasharray={box.status?.is_full_shot ? undefined : "3 2"}>
        <title>{`shot (${box.shot_x}, ${box.shot_y}) · ${box.status?.is_full_shot ? `Full Shot · Yield ${box.status?.shot_yield ?? "-"}%` : `Partial · ${box.status?.total_die || 0}/${box.status?.expected_die || 0} die`}`}</title>
      </rect>)}
      <text x={SIZE / 2} y={SIZE - 9} textAnchor="middle" fontSize="11" fill="var(--muted)">
        chip_x_pos {xAxis.min} → {xAxis.max} (오른쪽으로 증가)
      </text>
      <text x="13" y={SIZE / 2} textAnchor="middle" fontSize="11" fill="var(--muted)"
        transform={`rotate(-90 13 ${SIZE / 2})`}>chip_y_pos {yAxis.min} (위) → {yAxis.max} (아래)</text>
    </svg>
  );
}

function numericColor(value, minValue, maxValue) {
  const valueNumber = Number(value), lo = Number(minValue), hi = Number(maxValue);
  const ratio = Number.isFinite(valueNumber) && Number.isFinite(lo) && Number.isFinite(hi)
    ? Math.max(0, Math.min(1, hi === lo ? 0.5 : (valueNumber - lo) / (hi - lo))) : 0.5;
  const hue = 225 - ratio * 225;
  return `hsl(${hue} 78% 48%)`;
}

function WfGeometryMap({ kind, rows, geometry, colors, renderMode = "shot", anchorTeg = "", shotLayout = null,
  interpolationMethod = "idw" }) {
  const SIZE = 620, PAD = 26;
  const clipId = `wf-clip-${useId().replace(/:/g, "_")}`;
  const model = useMemo(() => {
    if (!geometry?.shots?.length) return null;
    const geo = geometry.geometry || {}, radius = Number(geo.wafer_radius_mm) || 150;
    const scale = (SIZE - PAD * 2) / (radius * 2);
    const shotW = Math.abs(Number(geo.shot_w_mm) || Number(geo.pitch_x) || 1);
    const shotH = Math.abs(Number(geo.shot_h_mm) || Number(geo.pitch_y) || 1);
    const byShot = new Map(geometry.shots.map(shot => [`${Number(shot.x)}:${Number(shot.y)}`, shot]));
    const anchor = (geometry.tegs || []).find(teg => teg.teg === anchorTeg || teg.teg_src === anchorTeg);
    const anchorX = anchor ? (Number(anchor.ebeam_x) || 0) + (Number(anchor.teg_w) || 0) / 2 : 0;
    const anchorY = anchor ? (Number(anchor.ebeam_y) || 0) + (Number(anchor.teg_h) || 0) / 2 : 0;
    const numericRows = (rows || []).filter(row => Number.isFinite(Number(row.value)) && byShot.has(`${Number(row.shot_x)}:${Number(row.shot_y)}`));
    const samples = numericRows.map(row => {
      const shot = byShot.get(`${Number(row.shot_x)}:${Number(row.shot_y)}`);
      return { ...row, mmX: Number(shot.mm_x) + anchorX, mmY: Number(shot.mm_y) + anchorY };
    });
    const values = samples.map(row => Number(row.value));
    const min = values.length ? Math.min(...values) : null, max = values.length ? Math.max(...values) : null;
    const display = geometry.display || {}, cols = Math.max(1, Math.min(40, Number(display.cols) || 5));
    const chipRows = Math.max(1, Math.min(40, Number(display.rows) || 5));
    const gaussianSigma = Math.max(radius / 2.5, Math.max(shotW, shotH) * 3);
    const interpolate = (x, y) => {
      if (!samples.length) return null;
      if (interpolationMethod === "nearest") {
        let nearest = samples[0], nearestDistance = Infinity;
        for (const sample of samples) {
          const distance2 = (sample.mmX - x) ** 2 + (sample.mmY - y) ** 2;
          if (distance2 < nearestDistance) { nearest = sample; nearestDistance = distance2; }
        }
        return Number(nearest.value);
      }
      let weighted = 0, weights = 0;
      for (const sample of samples) {
        const distance2 = (sample.mmX - x) ** 2 + (sample.mmY - y) ** 2;
        if (distance2 < 1e-8) return Number(sample.value);
        const weight = interpolationMethod === "gaussian"
          ? Math.exp(-distance2 / (2 * gaussianSigma ** 2))
          : 1 / distance2;
        weighted += Number(sample.value) * weight; weights += weight;
      }
      if (weights) return weighted / weights;
      let nearest = samples[0], nearestDistance = Infinity;
      for (const sample of samples) {
        const distance2 = (sample.mmX - x) ** 2 + (sample.mmY - y) ** 2;
        if (distance2 < nearestDistance) { nearest = sample; nearestDistance = distance2; }
      }
      return Number(nearest.value);
    };
    return { geo, radius, scale, shotW, shotH, byShot, anchorX, anchorY, samples, min, max, cols, chipRows, interpolate };
  }, [rows, geometry, anchorTeg, interpolationMethod]);
  if (!model) return <EmptyState icon="◫" title="TEG 위치조회 WF geometry가 없습니다" />;
  const { geo, radius, scale, shotW, shotH, byShot, samples, min, max, cols, chipRows, interpolate } = model;
  const sx = mmX => SIZE / 2 + Number(mmX) * scale;
  const sy = mmY => SIZE / 2 - Number(mmY) * scale;
  const waferRows = rows || [];
  return <div>
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label="동일 크기 WF MAP"
      style={{ width: "100%", height: "auto", background: "var(--bg-card)", border: "1px solid var(--line)", borderRadius: 8 }}>
      <defs><clipPath id={clipId}><circle cx={SIZE / 2} cy={SIZE / 2} r={radius * scale} /></clipPath></defs>
      <circle cx={SIZE / 2} cy={SIZE / 2} r={radius * scale} fill="var(--bg-primary)" stroke="var(--muted)" strokeWidth="1.2" />
      {Number(geo.wafer_edge_mm) > 0 && <circle cx={SIZE / 2} cy={SIZE / 2} r={Number(geo.wafer_edge_mm) * scale}
        fill="none" stroke="#C78A1E" strokeWidth=".7" strokeDasharray="5 4" />}
      {kind === "yield" && waferRows.map((row, index) => {
        const shot = byShot.get(`${Number(row.shot_x)}:${Number(row.shot_y)}`);
        if (!shot) return null;
        const layoutCols = Math.max(1, Number(shotLayout?.cols) || Number(geometry.display?.cols) || 1);
        const layoutRows = Math.max(1, Number(shotLayout?.rows) || Number(geometry.display?.rows) || layoutCols);
        const dieX = Number(row.die_x_in_shot) || 0, dieY = Number(row.die_y_in_shot) || 0;
        const w = shotW / layoutCols, h = shotH / layoutRows;
        const mmX = Number(shot.mm_x) - shotW / 2 + (dieX + .5) * w;
        const mmY = Number(shot.mm_y) + shotH / 2 - (dieY + .5) * h;
        return <rect key={`${row.x}:${row.y}:${index}`} x={sx(mmX - w / 2)} y={sy(mmY + h / 2)}
          width={Math.max(.6, w * scale - .2)} height={Math.max(.6, h * scale - .2)} fill={safeColor(colors?.[String(row.bin)])} stroke="rgba(15,23,42,.24)" strokeWidth=".25">
          <title>{`die (${row.x}, ${row.y}) · shot (${row.shot_x}, ${row.shot_y})\nBIN ${row.bin}`}</title>
        </rect>;
      })}
      {kind !== "yield" && renderMode === "shot" && samples.map((sample, index) => {
        const shot = byShot.get(`${Number(sample.shot_x)}:${Number(sample.shot_y)}`);
        return <rect key={`${sample.wafer}:${sample.shot_x}:${sample.shot_y}:${index}`}
          x={sx(Number(shot.mm_x) - shotW / 2)} y={sy(Number(shot.mm_y) + shotH / 2)}
          width={shotW * scale} height={shotH * scale} fill={numericColor(sample.value, min, max)} stroke="#0F172A" strokeWidth=".7">
          <title>{`shot (${sample.shot_x}, ${sample.shot_y})\nvalue ${Number(sample.value).toPrecision(6)}\nsamples ${sample.sample_count || 1}`}</title>
        </rect>;
      })}
      {kind !== "yield" && renderMode === "die" && (() => {
        const w = shotW / cols, h = shotH / chipRows;
        const anchorShot = [...geometry.shots].sort((a, b) => (Number(a.mm_x) ** 2 + Number(a.mm_y) ** 2) - (Number(b.mm_x) ** 2 + Number(b.mm_y) ** 2))[0];
        const originX = Number(anchorShot?.mm_x || 0) - shotW / 2 + w / 2;
        const originY = Number(anchorShot?.mm_y || 0) - shotH / 2 + h / 2;
        const minCol = Math.floor((-radius - originX) / w) - 1, maxCol = Math.ceil((radius - originX) / w) + 1;
        const minRow = Math.floor((-radius - originY) / h) - 1, maxRow = Math.ceil((radius - originY) / h) + 1;
        const cells = [];
        for (let col = minCol; col <= maxCol; col += 1) for (let row = minRow; row <= maxRow; row += 1) {
          const mmX = originX + col * w, mmY = originY + row * h;
          if (mmX ** 2 + mmY ** 2 > radius ** 2) continue;
          const value = interpolate(mmX, mmY);
          if (value == null) continue;
          cells.push(<rect key={`die:${col}:${row}`} x={sx(mmX - w / 2)} y={sy(mmY + h / 2)}
            width={Math.max(.5, w * scale - .15)} height={Math.max(.5, h * scale - .15)} fill={numericColor(value, min, max)} stroke="rgba(15,23,42,.18)" strokeWidth=".18">
            <title>{`WF die (${col}, ${row})\n보간값 ${Number(value).toPrecision(6)}`}</title>
          </rect>);
        }
        return <g clipPath={`url(#${clipId})`}>{cells}</g>;
      })()}
      {kind !== "yield" && renderMode === "surface" && <g clipPath={`url(#${clipId})`}>
        {Array.from({ length: 42 * 42 }, (_, index) => {
          const col = index % 42, row = Math.floor(index / 42), cellMm = radius * 2 / 42;
          const mmX = -radius + (col + .5) * cellMm, mmY = radius - (row + .5) * cellMm;
          if (mmX ** 2 + mmY ** 2 > radius ** 2) return null;
          const value = interpolate(mmX, mmY);
          if (value == null) return null;
          return <rect key={`surface:${col}:${row}`} x={sx(mmX - cellMm / 2)} y={sy(mmY + cellMm / 2)}
            width={cellMm * scale + .35} height={cellMm * scale + .35} fill={numericColor(value, min, max)} stroke="none">
            <title>{`WF Contour Map (${mmX.toFixed(1)} mm, ${mmY.toFixed(1)} mm)\n보간값 ${Number(value).toPrecision(6)}`}</title>
          </rect>;
        })}
      </g>}
      {kind !== "yield" && renderMode !== "shot" && samples.map((sample, index) => <circle
        key={`sample:${sample.wafer}:${sample.shot_x}:${sample.shot_y}:${index}`} cx={sx(sample.mmX)} cy={sy(sample.mmY)} r="2.1"
        fill="#0F172A" stroke="#FFFFFF" strokeWidth=".75">
        <title>{`${anchorTeg || "Shot center"} 측정점\nshot (${sample.shot_x}, ${sample.shot_y}) · ${Number(sample.value).toPrecision(6)}`}</title>
      </circle>)}
      <line x1={SIZE / 2 - 5} y1={SIZE / 2} x2={SIZE / 2 + 5} y2={SIZE / 2} stroke="var(--muted)" />
      <line x1={SIZE / 2} y1={SIZE / 2 - 5} x2={SIZE / 2} y2={SIZE / 2 + 5} stroke="var(--muted)" />
    </svg>
    {kind !== "yield" && min != null && <>
      <div style={{ height: 9, marginTop: 6, borderRadius: 99, background: "linear-gradient(90deg,hsl(225 78% 48%),hsl(112 78% 48%),hsl(0 78% 48%))" }} />
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)" }}><span>{Number(min).toPrecision(4)}</span><span>{renderMode === "shot" ? "측정 Shot만" : renderMode === "die" ? `${anchorTeg || "Shot center"} 기준 ${cols}×${chipRows} die · ${interpolationMethod}` : `${anchorTeg || "Shot center"} 기준 WF Contour · ${interpolationMethod}`}</span><span>{Number(max).toPrecision(4)}</span></div>
    </>}
  </div>;
}

const SPLIT_COLORS = ["#2563EB", "#DC2626", "#059669", "#D97706", "#7C3AED", "#0891B2", "#DB2777", "#475569"];
function splitColor(value) {
  const text = String(value || "(미지정)");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) hash = ((hash << 5) - hash + text.charCodeAt(index)) | 0;
  return SPLIT_COLORS[Math.abs(hash) % SPLIT_COLORS.length];
}

function comparisonValue(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toPrecision(6) : "—";
}

function ShotPairScatter({ points, xKey, yKey, xLabel, yLabel, metric }) {
  const paired = useMemo(() => points.filter(row => Number.isFinite(Number(row[xKey]))
    && Number.isFinite(Number(row[yKey]))), [points, xKey, yKey]);
  const model = useMemo(() => {
    if (!paired.length) return null;
    const xs = paired.map(row => Number(row[xKey])), ys = paired.map(row => Number(row[yKey]));
    return { xMin: Math.min(...xs), xMax: Math.max(...xs), yMin: Math.min(...ys), yMax: Math.max(...ys) };
  }, [paired, xKey, yKey]);
  const SIZE = 340, LEFT = 52, RIGHT = 14, TOP = 16, BOTTOM = 44;
  const px = value => LEFT + (Number(value) - model.xMin) / Math.max(model.xMax - model.xMin, 1e-12) * (SIZE - LEFT - RIGHT);
  const py = value => SIZE - BOTTOM - (Number(value) - model.yMin) / Math.max(model.yMax - model.yMin, 1e-12) * (SIZE - TOP - BOTTOM);
  const correlation = metric?.pearson_r;
  return <section style={{ minWidth: 0 }}>
    <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 7 }}>
      <b style={{ fontSize: 12 }}>{yLabel} ↔ {xLabel}</b>
      <Pill tone={correlation == null ? "neutral" : Math.abs(correlation) >= .7 ? "ok" : "neutral"}>
        r {correlation == null ? "계산 불가" : Number(correlation).toFixed(3)} · n {metric?.sample_count || 0}
      </Pill>
    </div>
    {!model ? <EmptyState icon="↗" title="공통 shot 없음" /> : <svg viewBox={`0 0 ${SIZE} ${SIZE}`}
      style={{ width: "100%", border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg-card)" }}>
      <line x1={LEFT} y1={SIZE - BOTTOM} x2={SIZE - RIGHT} y2={SIZE - BOTTOM} stroke="var(--muted)" />
      <line x1={LEFT} y1={TOP} x2={LEFT} y2={SIZE - BOTTOM} stroke="var(--muted)" />
      {paired.map((row, index) => <circle key={`${row.wafer}:${row.shot_x}:${row.shot_y}:${index}`}
        cx={px(row[xKey])} cy={py(row[yKey])} r="3.5" fill={splitColor(row.split)} fillOpacity=".8" stroke="#fff" strokeWidth=".5">
        <title>{`ROOT LOT ${row.root_lot_id || "-"}\nWAFER ${row.wafer_id || row.wafer} · shot (${row.shot_x}, ${row.shot_y})\n${xLabel} ${comparisonValue(row[xKey])}\n${yLabel} ${comparisonValue(row[yKey])}`}</title>
      </circle>)}
      <text x={(LEFT + SIZE - RIGHT) / 2} y={SIZE - 10} textAnchor="middle" fontSize="11" fill="var(--muted)">{xLabel}</text>
      <text x="13" y={(TOP + SIZE - BOTTOM) / 2} textAnchor="middle" fontSize="11" fill="var(--muted)"
        transform={`rotate(-90 13 ${(TOP + SIZE - BOTTOM) / 2})`}>{yLabel}</text>
      <text x={LEFT} y={SIZE - BOTTOM + 14} fontSize="9" fill="var(--muted)">{comparisonValue(model.xMin)}</text>
      <text x={SIZE - RIGHT} y={SIZE - BOTTOM + 14} textAnchor="end" fontSize="9" fill="var(--muted)">{comparisonValue(model.xMax)}</text>
      <text x={LEFT - 4} y={py(model.yMin)} textAnchor="end" fontSize="9" fill="var(--muted)">{comparisonValue(model.yMin)}</text>
      <text x={LEFT - 4} y={py(model.yMax) + 3} textAnchor="end" fontSize="9" fill="var(--muted)">{comparisonValue(model.yMax)}</text>
    </svg>}
  </section>;
}

function ShotComparisonChart({ data }) {
  const points = data?.points || [];
  if (!points.length) return <EmptyState icon="↗" title="Yield와 같은 wafer/shot 좌표로 JOIN된 비교점이 없습니다" />;
  const yieldLabel = `${data.selected_bin || "Yield"} · shot avg`;
  const etLabel = `ET · ${data.selected_item || "value"}`;
  const inlineLabel = `Inline · ${data.selected_inline_item || "value"}`;
  return <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12, alignItems: "start" }}>
      <ShotPairScatter points={points} xKey="et_value" yKey="yield_value" xLabel={etLabel} yLabel={yieldLabel} metric={data.similarity?.yield_et} />
      <ShotPairScatter points={points} xKey="inline_value" yKey="yield_value" xLabel={inlineLabel} yLabel={yieldLabel} metric={data.similarity?.yield_inline} />
      <ShotPairScatter points={points} xKey="inline_value" yKey="et_value" xLabel={inlineLabel} yLabel={etLabel} metric={data.similarity?.et_inline} />
    </div>
    <div style={{ maxHeight: 430, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
        <thead style={{ position: "sticky", top: 0, background: "var(--bg-secondary)", zIndex: 1 }}><tr>
          {['root_lot_id', 'wafer_id', 'shot_x', 'shot_y', yieldLabel, etLabel, inlineLabel, 'ET n', 'Inline n', 'split'].map(label =>
            <th key={label} style={{ padding: 7, borderBottom: "1px solid var(--line)", textAlign: "left", whiteSpace: "nowrap" }}>{label}</th>)}
        </tr></thead>
        <tbody>{points.map((row, index) => <tr key={`${row.wafer}:${row.shot_x}:${row.shot_y}:${index}`}>
          {[row.root_lot_id || data.root_lot_id, row.wafer_id || row.wafer, row.shot_x, row.shot_y,
            comparisonValue(row.yield_value), comparisonValue(row.et_value),
            comparisonValue(row.inline_value), row.et_sample_count || "—", row.inline_sample_count || "—"].map((value, cell) =>
            <td key={cell} style={{ padding: 6, borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>{value}</td>)}
          <td style={{ padding: 6, borderBottom: "1px solid var(--line)", color: splitColor(row.split), fontWeight: 800 }}>{row.split}</td>
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}

function RelationScatter({ data, pair }) {
  const rows = useMemo(() => (data?.points || []).map(row => ({
    ...row, x: Number(row.values?.[pair?.left_id]), y: Number(row.values?.[pair?.right_id]),
  })).filter(row => Number.isFinite(row.x) && Number.isFinite(row.y)), [data, pair]);
  if (!pair || !rows.length) return <EmptyState icon="↗" title="선택한 두 지표의 공통 shot이 없습니다" />;
  const SIZE = 520, LEFT = 64, RIGHT = 18, TOP = 20, BOTTOM = 52;
  const xMin = Math.min(...rows.map(row => row.x)), xMax = Math.max(...rows.map(row => row.x));
  const yMin = Math.min(...rows.map(row => row.y)), yMax = Math.max(...rows.map(row => row.y));
  const px = value => LEFT + (value - xMin) / Math.max(xMax - xMin, 1e-12) * (SIZE - LEFT - RIGHT);
  const py = value => SIZE - BOTTOM - (value - yMin) / Math.max(yMax - yMin, 1e-12) * (SIZE - TOP - BOTTOM);
  const groups = [...new Set(rows.map(row => String(row.color || "(미지정)")))].sort(naturalTextCompare);
  return <div style={{ display: "grid", gridTemplateColumns: "minmax(360px,620px) minmax(360px,1fr)", gap: 14, alignItems: "start" }}>
    <div>
      <div style={{ display: "flex", gap: 7, marginBottom: 7, flexWrap: "wrap" }}>
        <Pill tone="ok">관계 점수 {pair.relationship_score == null ? "—" : Number(pair.relationship_score).toFixed(4)}</Pill>
        <Pill tone="ok">r {pair.pearson_r == null ? "—" : Number(pair.pearson_r).toFixed(4)}</Pill>
        <Pill tone="neutral">R² {pair.fit?.r2 == null ? "—" : Number(pair.fit.r2).toFixed(4)}</Pill>
        <Pill tone="neutral">n {pair.sample_count || 0}</Pill>
        {pair.threshold?.is_candidate && <Pill tone="warn">변곡 X≈{comparisonValue(pair.threshold.threshold)} · 개선 {(Number(pair.threshold.improvement) * 100).toFixed(1)}%</Pill>}
        {pair.saved?.status && <Pill tone={pair.saved.status === "significant" ? "ok" : "neutral"}>저장: {pair.saved.status}</Pill>}
      </div>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} style={{ width: "100%", border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg-card)" }}>
        <line x1={LEFT} y1={SIZE - BOTTOM} x2={SIZE - RIGHT} y2={SIZE - BOTTOM} stroke="var(--muted)" />
        <line x1={LEFT} y1={TOP} x2={LEFT} y2={SIZE - BOTTOM} stroke="var(--muted)" />
        {pair.threshold?.is_candidate && <>
          <line x1={px(pair.threshold.threshold)} y1={TOP} x2={px(pair.threshold.threshold)} y2={SIZE - BOTTOM}
            stroke="#D97706" strokeWidth="1.4" strokeDasharray="4 4">
            <title>{`변곡 후보 X=${comparisonValue(pair.threshold.threshold)} · 단일 직선 대비 ${(Number(pair.threshold.improvement) * 100).toFixed(1)}% 개선`}</title>
          </line>
          {[['left_fit', 'left_min', 'left_max'], ['right_fit', 'right_min', 'right_max']].map(([fitKey, minKey, maxKey]) => {
            const fit = pair.threshold[fitKey], lo = pair.threshold[minKey], hi = pair.threshold[maxKey];
            if (fit?.slope == null) return null;
            return <line key={fitKey} x1={px(lo)} y1={py(fit.slope * lo + fit.intercept)}
              x2={px(hi)} y2={py(fit.slope * hi + fit.intercept)} stroke="#D97706" strokeWidth="2.4" />;
          })}
        </>}
        {(pair.group_fits || []).map(group => {
          const fit = group.fit || {};
          const groupRows = rows.filter(row => String(row.color || "(미지정)") === String(group.color));
          if (fit.slope == null || !groupRows.length) return null;
          const lo = Math.min(...groupRows.map(row => row.x)), hi = Math.max(...groupRows.map(row => row.x));
          return <line key={`fit:${group.color}`} x1={px(lo)} y1={py(fit.slope * lo + fit.intercept)}
            x2={px(hi)} y2={py(fit.slope * hi + fit.intercept)} stroke={splitColor(group.color)} strokeWidth="2.2" strokeDasharray="5 3">
            <title>{`${group.color} fitting · y=${fit.slope.toPrecision(5)}x ${fit.intercept < 0 ? "-" : "+"} ${Math.abs(fit.intercept).toPrecision(5)} · R² ${fit.r2?.toFixed?.(4) || "—"}`}</title>
          </line>;
        })}
        {rows.map((row, index) => <circle key={`${row.root_lot_id}:${row.wafer_id}:${row.shot_x}:${row.shot_y}:${index}`}
          cx={px(row.x)} cy={py(row.y)} r="4" fill={splitColor(row.color)} fillOpacity=".82" stroke="#fff" strokeWidth=".6">
          <title>{`ROOT LOT ${row.root_lot_id}\nWAFER ${row.wafer_id} · shot (${row.shot_x}, ${row.shot_y})\n${pair.left_label} ${comparisonValue(row.x)}\n${pair.right_label} ${comparisonValue(row.y)}\nColor ${row.color}`}</title>
        </circle>)}
        <text x={(LEFT + SIZE - RIGHT) / 2} y={SIZE - 12} textAnchor="middle" fontSize="12" fill="var(--muted)">{pair.left_label}</text>
        <text x="14" y={(TOP + SIZE - BOTTOM) / 2} textAnchor="middle" fontSize="12" fill="var(--muted)" transform={`rotate(-90 14 ${(TOP + SIZE - BOTTOM) / 2})`}>{pair.right_label}</text>
      </svg>
      <div style={{ display: "flex", gap: 9, flexWrap: "wrap", marginTop: 7 }}>
        {groups.map(group => <span key={group} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11 }}><i style={{ width: 9, height: 9, borderRadius: 99, background: splitColor(group) }} />{group}</span>)}
      </div>
    </div>
    <div style={{ maxHeight: 550, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
        <thead style={{ position: "sticky", top: 0, background: "var(--bg-secondary)", zIndex: 1 }}><tr>
          {['root_lot_id', 'wafer_id', 'shot_x', 'shot_y', pair.left_label, pair.right_label, 'color'].map(label =>
            <th key={label} style={{ padding: 7, borderBottom: "1px solid var(--line)", textAlign: "left", whiteSpace: "nowrap" }}>{label}</th>)}
        </tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{[
          row.root_lot_id, row.wafer_id, row.shot_x, row.shot_y, comparisonValue(row.x), comparisonValue(row.y), row.color,
        ].map((value, cell) => <td key={cell} style={{ padding: 6, borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>{value}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </div>;
}


function RelationMapComparison({ data, pair }) {
  const wafers = useMemo(() => [...new Set((data?.points || [])
    .filter(row => row.values?.[pair?.left_id] != null && row.values?.[pair?.right_id] != null)
    .map(row => String(row.wafer_id || row.wafer || ""))
    .filter(Boolean))].sort(naturalTextCompare).slice(0, 25), [data, pair]);
  const [mapMode, setMapMode] = useState("shot");
  const [mapInterpolation, setMapInterpolation] = useState("idw");
  if (!pair || !data?.geometry || !wafers.length) return <EmptyState icon="◫" title="비교 MAP geometry 또는 공통 wafer가 없습니다" />;
  const metricRows = (metricId, waferId) => (data.points || []).filter(row =>
    String(row.wafer_id || row.wafer || "") === waferId && Number.isFinite(Number(row.values?.[metricId]))
  ).map(row => ({
    wafer: waferId, shot_x: Number(row.shot_x), shot_y: Number(row.shot_y), value: Number(row.values[metricId]),
  }));
  const maps = [
    { id: pair.left_id, label: pair.left_label },
    { id: pair.right_id, label: pair.right_label },
  ];
  return <section style={{ marginBottom: 18 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 8, marginBottom: 9 }}>
      <div><b>ROOT LOT WF MAP 비교</b><div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>지표별 한 행에 같은 WAFER 순서로 최대 25장을 표시합니다.</div></div>
      <div style={{ display: "flex", gap: 7, alignItems: "flex-end", flexWrap: "wrap", justifyContent: "flex-end" }}>
        <Pill tone="ok">WAFER {wafers.length}장</Pill>
        <label style={{ ...inputLabel, minWidth: 145 }}>MAP 표현
          <Select value={mapMode} onChange={event => setMapMode(event.target.value)}>
            <option value="shot">측정 Shot</option><option value="surface">WF 보간</option>
          </Select>
        </label>
        {mapMode === "surface" && <label style={{ ...inputLabel, minWidth: 130 }}>보간 방식
          <Select value={mapInterpolation} onChange={event => setMapInterpolation(event.target.value)}>
            <option value="idw">IDW</option><option value="nearest">Nearest</option>
          </Select>
        </label>}
      </div>
    </div>
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {maps.map(metric => <div key={metric.id} style={{ padding: 10, border: "1px solid var(--line)", borderRadius: 8, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 7, marginBottom: 8 }}><b>{metric.label}</b><Pill tone="neutral">{data.root_lot_id}</Pill></div>
        <div style={{ overflowX: "auto", paddingBottom: 5 }}>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${wafers.length}, minmax(210px, 210px))`, gap: 8, width: "max-content" }}>
            {wafers.map(wafer => <div key={`${metric.id}:${wafer}`} style={{ padding: 7, border: "1px solid var(--line)", borderRadius: 7 }}>
              <div style={{ fontSize: 11, fontWeight: 800, marginBottom: 5 }}>WAFER {wafer}</div>
              <WfGeometryMap kind="et" rows={metricRows(metric.id, wafer)} geometry={data.geometry} renderMode={mapMode} interpolationMethod={mapInterpolation} />
            </div>)}
          </div>
        </div>
      </div>)}
    </div>
  </section>;
}


export default function My_YieldMap({ user }) {
  const [boot, setBoot] = useState(null);
  const [pageTab, setPageTab] = useState("map");
  const [dataKind, setDataKind] = useState("yield");
  const [product, setProduct] = useState("");
  const [shotProduct, setShotProduct] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [itemId, setItemId] = useState("");
  const [etItemSource, setEtItemSource] = useState("raw");
  const [etDownloadItems, setEtDownloadItems] = useState([]);
  const [etItemsBusy, setEtItemsBusy] = useState(false);
  const [testAddpAlias, setTestAddpAlias] = useState("");
  const [testAddpForm, setTestAddpForm] = useState("");
  const [stepId, setStepId] = useState("");
  const [stepSeq, setStepSeq] = useState("");
  const [inlineTable, setInlineTable] = useState("");
  const [renderMode, setRenderMode] = useState("shot");
  const [interpolationMethod, setInterpolationMethod] = useState("idw");
  const [anchorTeg, setAnchorTeg] = useState("");
  const [dimensionMaps, setDimensionMaps] = useState([]);
  const [dimensionWaferId, setDimensionWaferId] = useState("");
  const [comparisonInlineItem, setComparisonInlineItem] = useState("");
  const [comparisonInlineStep, setComparisonInlineStep] = useState("");
  const [comparisonInlineTable, setComparisonInlineTable] = useState("");
  const [comparisonBin, setComparisonBin] = useState("yield");
  const [comparisonSplitSource, setComparisonSplitSource] = useState("");
  const [comparison, setComparison] = useState(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [relationMetrics, setRelationMetrics] = useState([
    { id: "metric_1", kind: "yield", bin_name: "yield" },
    { id: "metric_2", kind: "et", item_id: "", step_id: "", step_seq: "" },
  ]);
  const [relationResult, setRelationResult] = useState(null);
  const [relationPairId, setRelationPairId] = useState("");
  const [relationOptions, setRelationOptions] = useState({ fab_fields: [], relationships: [] });
  const [relationColorSource, setRelationColorSource] = useState("none");
  const [relationFabField, setRelationFabField] = useState("");
  const [relationProduct, setRelationProduct] = useState("");
  const [comparisonView, setComparisonView] = useState("corr");
  const [relationTkoutFrom, setRelationTkoutFrom] = useState("");
  const [relationTkoutTo, setRelationTkoutTo] = useState("");
  const [config, setConfig] = useState({ source: "", fields: {}, bin_colors: {} });
  const [preview, setPreview] = useState(null);
  const [map, setMap] = useState(null);
  const [rootLotId, setRootLotId] = useState("");
  const [binRows, setBinRows] = useState(() => spreadsheetBinRows([{ bin: "", color: "#94A3B8" }]));
  const [scanPreview, setScanPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mapColumns, setMapColumns] = useState(initialMapColumns);
  const [columnMappings, setColumnMappings] = useState({ et: null, inline: null });
  const [columnMappingBusy, setColumnMappingBusy] = useState(false);
  const previewRequest = useRef(0);

  useEffect(() => {
    try { window.localStorage.setItem(MAP_COLUMNS_STORAGE_KEY, String(mapColumns)); } catch { /* device preference only */ }
  }, [mapColumns]);

  const loadBootstrap = useCallback(async () => {
    setError("");
    try {
      const data = await sf(`${API}/bootstrap`);
      setBoot(data);
      setProduct(current => data.products?.includes(current) ? current : (
        data.products?.find(name => geometryForProduct(data.geometry_products, name)) || data.products?.[0] || ""
      ));
    } catch (err) { setError(String(err.message || err)); }
  }, []);
  useEffect(() => { loadBootstrap(); }, [loadBootstrap]);

  const loadColumnMappings = useCallback(async () => {
    setColumnMappingBusy(true); setError("");
    try {
      const [et, inline] = await Promise.all([
        sf(`${API}/column-mapping?kind=et&scope=database`),
        sf(`${API}/column-mapping?kind=inline&scope=database`),
      ]);
      setColumnMappings({ et, inline });
    } catch (err) {
      setColumnMappings({ et: null, inline: null }); setError(String(err.message || err));
    } finally { setColumnMappingBusy(false); }
  }, []);

  useEffect(() => {
    if (!boot) return;
    setColumnMappings({ et: null, inline: null });
    loadColumnMappings();
  }, [boot, loadColumnMappings]);

  useEffect(() => {
    if (!boot || dataKind === "yield") return;
    const products = boot.shot_sources?.[dataKind] || [];
    setShotProduct(current => products.includes(current) ? current : (
      products.find(name => geometryForProduct(boot.geometry_products, name)) || products[0] || ""
    ));
    setMap(null); setItemId(""); setStepId(""); setStepSeq(""); setInlineTable("");
  }, [boot, dataKind]);

  useEffect(() => {
    if (!boot || pageTab !== "map") return;
    const activeProduct = dataKind === "yield" ? product : shotProduct;
    const matchedVehicle = geometryForProduct(boot.geometry_products, activeProduct)?.vehicle || "";
    setVehicle(matchedVehicle);
    setAnchorTeg(""); setMap(null);
    if (dataKind === "yield") {
      setConfig(current => ({ ...current, vehicle: matchedVehicle }));
    }
  }, [boot, pageTab, dataKind, product, shotProduct]);

  useEffect(() => {
    if (!boot || dataKind !== "inline" || !shotProduct) return;
    const rules = rulesForProduct(boot.inline_matching, shotProduct);
    const items = matchingValues(rules, "item_id");
    const selectedItem = items.includes(itemId) ? itemId : (items[0] || "");
    const itemRules = rulesForValue(rules, "item_id", selectedItem);
    const steps = matchingValues(itemRules, "step_id");
    const selectedStep = steps.includes(stepId) ? stepId : (steps[0] || "");
    const stepRules = rulesForValue(itemRules, "step_id", selectedStep);
    const preferred = stepRules.find(rule => ruleUsable(rule, vehicle)) || stepRules[0];
    setItemId(selectedItem); setStepId(selectedStep);
    setInlineTable(String(preferred?.matching_table || ""));
    setMap(null);
  }, [boot, dataKind, shotProduct, vehicle]);

  useEffect(() => {
    if (!boot || !product) return;
    const rules = rulesForProduct(boot.inline_matching, product);
    const items = matchingValues(rules, "item_id");
    const selectedItem = items.includes(comparisonInlineItem) ? comparisonInlineItem : (items[0] || "");
    const itemRules = rulesForValue(rules, "item_id", selectedItem);
    const steps = matchingValues(itemRules, "step_id");
    const selectedStep = steps.includes(comparisonInlineStep) ? comparisonInlineStep : (steps[0] || "");
    const stepRules = rulesForValue(itemRules, "step_id", selectedStep);
    const preferred = stepRules.find(rule => ruleUsable(rule, vehicle)) || stepRules[0];
    setComparisonInlineItem(selectedItem); setComparisonInlineStep(selectedStep);
    setComparisonInlineTable(String(preferred?.matching_table || ""));
    setComparison(null);
  }, [boot, product, vehicle]);

  useEffect(() => {
    const catalogProduct = dataKind === "et" ? shotProduct : (pageTab === "compare" ? product : "");
    if (!catalogProduct) { setEtDownloadItems([]); return; }
    let active = true;
    setEtItemsBusy(true);
    sf(`${API}/et-items?product=${encodeURIComponent(catalogProduct)}`)
      .then(data => { if (active) setEtDownloadItems(data.items || []); })
      .catch(() => { if (active) setEtDownloadItems([]); })
      .finally(() => { if (active) setEtItemsBusy(false); });
    return () => { active = false; };
  }, [dataKind, shotProduct, product, pageTab]);

  useEffect(() => {
    if (etItemSource !== "et_download") return;
    const aliases = etDownloadItems.map(row => row.alias);
    setItemId(current => aliases.includes(current) ? current : (aliases[0] || ""));
    if (dataKind === "et") setMap(null);
  }, [dataKind, etItemSource, etDownloadItems]);

  useEffect(() => {
    if (!boot || !product) return;
    previewRequest.current += 1;
    const saved = boot.configs?.[product] || { source: "", fields: {}, bin_colors: {} };
    const matchedVehicle = geometryForProduct(boot.geometry_products, product)?.vehicle || "";
    setConfig({ source: "", fields: {}, bin_colors: {}, ...saved, vehicle: matchedVehicle, shot_layout: shotLayoutFromConfig(saved) });
    setVehicle(matchedVehicle);
    setBinRows(spreadsheetBinRows(binRowsFromConfig(saved)));
    setPreview(null);
    setMap(null);
    setScanPreview(null);
    setRootLotId("");
  }, [boot, product]);

  const loadPreview = useCallback(async (source = config.source) => {
    if (!source || !product) return;
    const requestId = ++previewRequest.current;
    setBusy(true); setError("");
    try {
      const data = await sf(`${API}/preview?source=${encodeURIComponent(source)}&product=${encodeURIComponent(product)}`);
      if (requestId !== previewRequest.current) return;
      setPreview(data);
      setConfig(current => ({
        ...current, source,
        fields: { ...(data.detected_fields || {}), ...(current.fields || {}) },
      }));
    } catch (err) {
      if (requestId === previewRequest.current) { setPreview(null); setError(String(err.message || err)); }
    } finally { if (requestId === previewRequest.current) setBusy(false); }
  }, [config.source, product]);

  useEffect(() => { if (config.source) loadPreview(config.source); }, [product, config.source]);

  const setField = (key, value) => setConfig(current => ({
    ...current, fields: { ...(current.fields || {}), [key]: value },
  }));
  const binColorMap = useMemo(() => binColorsFromRows(binRows), [binRows]);
  const waferMaps = useMemo(() => waferTrellisFromMap(map), [map]);

  const copyBinTable = async () => {
    const rows = binRows.filter(row => String(row.bin || "").trim());
    if (!rows.length) {
      setError("복사할 BIN MAP 행이 없습니다.");
      return;
    }
    const text = ["BIN\tBIN COLOR", ...rows.map(row => `${String(row.bin).trim()}\t${String(row.color || "").trim().toUpperCase()}`)].join("\n");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
      toast.ok(`BIN MAP ${rows.length}행을 복사했습니다.`);
    } catch {
      setError("클립보드 복사를 사용할 수 없습니다. BIN 셀을 선택해 직접 복사해 주세요.");
    }
  };

  const scanShotLayout = async () => {
    if (!product || !config.source || !vehicle) return;
    setBusy(true); setError(""); setScanPreview(null);
    try {
      const data = await postJson(`${API}/scan/${encodeURIComponent(product)}`, {
        source: config.source, fields: config.fields || {},
        root_lot_id: rootLotId.trim(),
      });
      setScanPreview(data);
      setConfig(current => ({ ...current, vehicle: data.vehicle, shot_layout: data.layout }));
      setBinRows(current => {
        const existing = new Map(current.filter(row => String(row.bin || "").trim())
          .map(row => [String(row.bin).trim(), String(row.color || "").toUpperCase()]));
        const discovered = (data.bins || []).map((item, index) => ({
          bin: String(item.bin),
          color: existing.get(String(item.bin)) || AUTO_BIN_COLORS[index % AUTO_BIN_COLORS.length],
        }));
        return spreadsheetBinRows(discovered);
      });
    } catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); }
  };

  const save = async () => {
    if (!product || !config.source) return;
    const invalid = binRows.find(row => String(row.bin || "").trim()
      && !/^#[0-9A-F]{6}$/i.test(String(row.color || "").trim()));
    if (invalid) {
      setError(`BIN ${invalid.bin}의 컬러는 #RRGGBB 형식으로 입력해 주세요.`);
      return;
    }
    const names = binRows.map(row => String(row.bin || "").trim()).filter(Boolean);
    const duplicate = names.find((name, index) => names.indexOf(name) !== index);
    if (duplicate) {
      setError(`BIN MAP에 중복 BIN이 있습니다: ${duplicate}`);
      return;
    }
    setBusy(true); setError("");
    try {
      const binMap = binRows.filter(row => String(row.bin || "").trim())
        .map(row => ({ bin: String(row.bin).trim(), bin_color: String(row.color || "").trim().toUpperCase() }));
      const data = await putJson(`${API}/config/${encodeURIComponent(product)}`, {
        ...config, vehicle: config.vehicle || vehicle, shot_layout: shotLayoutFromConfig(config), bin_map: binMap, bin_colors: binColorMap,
      });
      setConfig(data.config);
      setBinRows(spreadsheetBinRows(binRowsFromConfig(data.config)));
      setBoot(current => ({ ...current, configs: { ...(current.configs || {}), [product]: data.config } }));
      toast.ok(`${product} Yield Map 설정 저장됨`);
    } catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); }
  };

  const saveColumnMappings = async () => {
    if (!columnMappings.et || !columnMappings.inline) return;
    setColumnMappingBusy(true); setError("");
    try {
      const saved = {};
      for (const kind of ["et", "inline"]) {
        const mapping = columnMappings[kind];
        saved[kind] = await putJson(`${API}/column-mapping`, {
          product: "", kind, fields: mapping.fields || {},
          value_columns: mapping.value_columns || [], scope: "database",
        });
      }
      setColumnMappings(saved);
      setMap(null); setRelationResult(null); setComparison(null);
      toast.ok("ET · Inline DB 공통 열 매칭 저장됨");
    } catch (err) { setError(String(err.message || err)); }
    finally { setColumnMappingBusy(false); }
  };

  const loadMap = async () => {
    const selectedProduct = dataKind === "yield" ? product : shotProduct;
    if (!selectedProduct) return;
    if (!rootLotId.trim()) {
      setError("ROOT LOT ID를 입력해 주세요.");
      return;
    }
    setBusy(true); setError("");
    try {
      let data;
      if (dataKind === "et" && etItemSource !== "raw") {
        data = await postJson(`${API}/et-index-map`, {
          product: selectedProduct, vehicle, root_lot_id: rootLotId.trim(), step_id: stepId.trim(),
          step_seq: stepSeq.trim(),
          item_alias: etItemSource === "test_addp" ? testAddpAlias.trim() : itemId.trim(),
          item_source: etItemSource, addp_form: etItemSource === "test_addp" ? testAddpForm.trim() : "",
        });
      } else {
        const q = new URLSearchParams({ product: selectedProduct, kind: dataKind });
        q.set("root_lot_id", rootLotId.trim());
        if (dataKind !== "yield") {
          q.set("vehicle", vehicle);
          if (itemId) q.set("item_id", itemId);
          if (stepId) q.set("step_id", stepId);
          if (dataKind === "et" && stepSeq) q.set("step_seq", stepSeq);
          if (dataKind === "inline" && inlineTable) q.set("inline_table", inlineTable);
        }
        data = await sf(`${API}/map?${q.toString()}`);
      }
      setMap(data);
      if (dataKind !== "yield") {
        setItemId(data.selected_item || "");
        setStepId(data.selected_step || "");
        if (dataKind === "et") setStepSeq(data.selected_step_seq || "");
        if (data.inline_table) setInlineTable(data.inline_table);
      }
      if (dataKind === "yield") setBinRows(current => {
        const existing = new Set(current.map(row => String(row.bin || "")));
        const discovered = (data.bins || []).filter(item => !existing.has(String(item.bin)))
          .map(item => ({ bin: String(item.bin), color: "#D1D5DB" }));
        return discovered.length ? spreadsheetBinRows([...current.filter(row => row.bin), ...discovered]) : current;
      });
    } catch (err) { setMap(null); setError(String(err.message || err)); }
    finally { setBusy(false); }
  };

  const canEdit = !!boot?.can_edit || user?.role === "admin";
  const isAdmin = user?.role === "admin";
  const activeProduct = dataKind === "yield" ? product : shotProduct;
  const matchedGeometry = geometryForProduct(boot?.geometry_products, activeProduct);
  const hasBinSettings = !!scanPreview || binRows.some(row => String(row.bin || "").trim());
  const sourceOptions = (boot?.sources || []).filter(source => !source.products?.length
    || source.products.some(name => String(name).toLowerCase() === String(product).toLowerCase()));
  const shotProducts = boot?.shot_sources?.[dataKind] || [];
  const requiredShotFields = {
    et: new Set(["lot", "wafer", "shot_x", "shot_y"]),
    inline: new Set(["lot", "wafer", "subitem"]),
  };
  const selectedEtSpec = etDownloadItems.find(row => row.alias === itemId) || map?.item_spec || null;
  const comparisonProducts = [...new Set([
    ...(boot?.products || []), ...(boot?.shot_sources?.et || []), ...(boot?.shot_sources?.inline || []),
  ])].filter(name => {
    const token = String(name).toLowerCase();
    const sourceCount = Number((boot?.products || []).some(value => String(value).toLowerCase() === token))
      + Number((boot?.shot_sources?.et || []).some(value => String(value).toLowerCase() === token))
      + Number((boot?.shot_sources?.inline || []).some(value => String(value).toLowerCase() === token));
    return sourceCount >= 2;
  });
  const productSourceKinds = relationProduct ? [
    (boot?.products || []).some(value => String(value).toLowerCase() === String(relationProduct).toLowerCase()) && "yield",
    (boot?.shot_sources?.et || []).some(value => String(value).toLowerCase() === String(relationProduct).toLowerCase()) && "et",
    (boot?.shot_sources?.inline || []).some(value => String(value).toLowerCase() === String(relationProduct).toLowerCase()) && "inline",
  ].filter(Boolean) : [];
  const relationInlineRules = rulesForProduct(boot?.inline_matching, relationProduct);
  const mainInlineRules = rulesForProduct(boot?.inline_matching, shotProduct);
  const mainInlineItems = matchingValues(mainInlineRules, "item_id");
  const mainInlineItemRules = rulesForValue(mainInlineRules, "item_id", itemId);
  const mainInlineSteps = matchingValues(mainInlineItemRules, "step_id");
  const mainInlineStepRules = rulesForValue(mainInlineItemRules, "step_id", stepId);
  const mainInlineReady = mainInlineStepRules.some(rule =>
    String(rule.matching_table || "").toLowerCase() === String(inlineTable || "").toLowerCase()
    && ruleUsable(rule, vehicle));
  const comparisonInlineRules = rulesForProduct(boot?.inline_matching, product);
  const comparisonInlineItems = matchingValues(comparisonInlineRules, "item_id");
  const comparisonInlineItemRules = rulesForValue(comparisonInlineRules, "item_id", comparisonInlineItem);
  const comparisonInlineSteps = matchingValues(comparisonInlineItemRules, "step_id");
  const comparisonInlineStepRules = rulesForValue(comparisonInlineItemRules, "step_id", comparisonInlineStep);
  const comparisonInlineReady = comparisonInlineStepRules.some(rule =>
    String(rule.matching_table || "").toLowerCase() === String(comparisonInlineTable || "").toLowerCase()
    && ruleUsable(rule, vehicle));

  useEffect(() => {
    if (pageTab !== "compare" || !boot) return;
    const current = comparisonProducts.find(name => String(name).toLowerCase() === String(relationProduct).toLowerCase());
    if (!current && comparisonProducts.length) setRelationProduct(comparisonProducts[0]);
  }, [pageTab, boot, relationProduct, comparisonProducts.join("|")]);

  useEffect(() => {
    if (pageTab !== "compare" || !boot || !relationProduct) return;
    setVehicle(geometryForProduct(boot.geometry_products, relationProduct)?.vehicle || "");
    setRelationResult(null);
  }, [pageTab, boot, relationProduct]);

  useEffect(() => {
    if (!relationProduct || pageTab !== "compare") return;
    let active = true;
    sf(`${API}/relation-options?product=${encodeURIComponent(relationProduct)}`)
      .then(data => {
        if (!active) return;
        setRelationOptions(data);
        setRelationFabField(current => (data.fab_fields || []).includes(current) ? current : (data.fab_fields?.[0] || ""));
      }).catch(() => { if (active) setRelationOptions({ fab_fields: [], relationships: [] }); });
    const defaults = productSourceKinds.slice(0, 2).map((kind, index) => {
      if (kind !== "inline") return { id: `metric_${index + 1}`, kind, bin_name: "yield", item_id: "", step_id: "", step_seq: "" };
      const item = matchingValues(relationInlineRules, "item_id")[0] || "";
      const itemRules = rulesForValue(relationInlineRules, "item_id", item);
      const step = matchingValues(itemRules, "step_id")[0] || "";
      const rule = rulesForValue(itemRules, "step_id", step).find(row => ruleUsable(row, vehicle))
        || rulesForValue(itemRules, "step_id", step)[0];
      return { id: `metric_${index + 1}`, kind, item_id: item, step_id: step, inline_table: String(rule?.matching_table || "") };
    });
    if (defaults.length >= 2) setRelationMetrics(defaults);
    setRelationResult(null); setRelationPairId("");
    return () => { active = false; };
  }, [pageTab, relationProduct, productSourceKinds.join("|"), vehicle]);

  const relationMetricReady = metric => {
    if (metric.kind === "yield") return true;
    if (metric.kind === "et") return !!String(metric.item_id || "").trim();
    const rules = rulesForValue(
      rulesForValue(relationInlineRules, "item_id", metric.item_id), "step_id", metric.step_id,
    );
    return !!metric.item_id && !!metric.step_id && rules.some(rule =>
      String(rule.matching_table || "").toLowerCase() === String(metric.inline_table || "").toLowerCase()
      && ruleUsable(rule, vehicle));
  };
  const relationReady = relationMetrics.length >= 2 && relationMetrics.every(relationMetricReady)
    && !!rootLotId.trim() && !!vehicle;
  const relationTkoutInvalid = !!relationTkoutFrom && !!relationTkoutTo && relationTkoutFrom > relationTkoutTo;
  const updateRelationMetric = (id, patch) => {
    setRelationMetrics(current => current.map(metric => metric.id === id ? { ...metric, ...patch } : metric));
    setRelationResult(null);
  };
  const makeRelationTarget = id => {
    setRelationMetrics(current => {
      const target = current.find(metric => metric.id === id);
      return target ? [target, ...current.filter(metric => metric.id !== id)] : current;
    });
    setRelationResult(null); setRelationPairId("");
  };
  const addRelationMetric = () => {
    const next = relationMetrics.length + 1;
    const kind = productSourceKinds.find(value => value !== relationMetrics[0]?.kind) || productSourceKinds[0] || "et";
    setRelationMetrics(current => [...current, { id: `metric_${Date.now()}_${next}`, kind, bin_name: "yield", item_id: "", step_id: "", step_seq: "" }].slice(0, 30));
  };
  const loadRelations = async () => {
    if (!relationReady) return;
    setComparisonBusy(true); setError("");
    try {
      const data = await postJson(`${API}/relations`, {
        product: relationProduct, vehicle, root_lot_id: rootLotId.trim(), metrics: relationMetrics,
        color_source: relationColorSource,
        color_field: relationColorSource === "fab" ? relationFabField : "",
        split_source: relationColorSource === "split" ? comparisonSplitSource : "",
        target_metric_id: relationMetrics[0]?.id || "",
        tkout_from: relationTkoutFrom, tkout_to: relationTkoutTo,
      });
      setRelationResult(data); setRelationPairId(data.pairs?.[0]?.id || "");
    } catch (err) { setRelationResult(null); setError(String(err.message || err)); }
    finally { setComparisonBusy(false); }
  };
  const saveRelationStatus = async (pair, status) => {
    try {
      const data = await putJson(`${API}/relationships`, {
        product: relationProduct, left_metric: pair.left_label, right_metric: pair.right_label,
        status, corr: pair.pearson_r, r2: pair.fit?.r2,
      });
      setRelationResult(current => ({ ...current, pairs: (current?.pairs || []).map(row =>
        row.id === pair.id ? { ...row, saved: data.relationship } : row) }));
      toast.ok(status === "significant" ? "유의차 있음으로 저장했습니다." : "관계 분류를 저장했습니다.");
    } catch (err) { setError(String(err.message || err)); }
  };

  const loadComparison = async () => {
    if (!product || !comparisonProducts.some(name => String(name).toLowerCase() === String(product).toLowerCase())
      || !vehicle || !rootLotId.trim()) return;
    setComparisonBusy(true); setError("");
    try {
      const data = await postJson(`${API}/compare/shot-sources`, {
        yield_product: product, et_product: product, inline_product: product, vehicle,
        root_lot_id: rootLotId.trim(), bin_name: comparisonBin.trim() || "yield",
        et_item_id: etItemSource === "test_addp" ? testAddpAlias.trim() : itemId.trim(),
        inline_item_id: comparisonInlineItem.trim(), et_item_source: etItemSource,
        et_addp_form: etItemSource === "test_addp" ? testAddpForm.trim() : "",
        step_id: stepId.trim(), step_seq: stepSeq.trim(),
        inline_step_id: comparisonInlineStep, inline_table: comparisonInlineTable,
        split_source: comparisonSplitSource,
      });
      setComparison(data);
      if (!itemId && data.selected_item) setItemId(data.selected_item);
      if (!comparisonInlineItem && data.selected_inline_item) setComparisonInlineItem(data.selected_inline_item);
      if (data.selected_inline_step) setComparisonInlineStep(data.selected_inline_step);
      if (data.selected_inline_table) setComparisonInlineTable(data.selected_inline_table);
      if (data.selected_step) setStepId(data.selected_step);
      if (data.selected_step_seq) setStepSeq(data.selected_step_seq);
    } catch (err) { setComparison(null); setError(String(err.message || err)); }
    finally { setComparisonBusy(false); }
  };

  const addCurrentDimension = () => {
    if (!map?.rows?.length) return;
    const sourceKind = map.kind || dataKind;
    const metric = sourceKind === "yield" ? (map.selected_bin || "BIN/Die") : (map.selected_item || itemId || "value");
    const selectedStep = map.selected_step || stepId;
    const selectedSeq = map.selected_step_seq || stepSeq;
    const key = [sourceKind, map.product, map.root_lot_id, metric, selectedStep, selectedSeq,
      map.item_source || "raw", map.item_formula || ""].join("|");
    const sourceLabel = map.item_source === "test_addp" ? "TEST ADDP" : map.item_source === "et_download" ? "ET DOWNLOAD" : sourceKind.toUpperCase();
    const stepLabel = sourceKind === "et" && (selectedStep || selectedSeq) ? ` · ${selectedStep || "-"}/${selectedSeq || "-"}` : "";
    const entry = { key, kind: sourceKind, label: `${sourceLabel} · ${metric}${stepLabel}`, map,
      colors: sourceKind === "yield" ? { ...binColorMap } : {} };
    setDimensionMaps(current => {
      const next = [...current.filter(row => row.key !== key), entry].slice(-6);
      return next;
    });
    setDimensionWaferId(current => current || String(map.wafer_ids?.[0] || ""));
    toast.ok(`${entry.label} 차원을 같은 WF 비교에 추가했습니다.`);
  };
  const dimensionWaferIds = useMemo(() => {
    const values = new Set();
    dimensionMaps.forEach(dimension => (dimension.map?.wafer_ids || []).forEach(value => values.add(String(value))));
    return [...values].sort(naturalTextCompare);
  }, [dimensionMaps]);
  useEffect(() => {
    if (!dimensionWaferIds.length) { setDimensionWaferId(""); return; }
    if (!dimensionWaferIds.includes(dimensionWaferId)) setDimensionWaferId(dimensionWaferIds[0]);
  }, [dimensionWaferIds, dimensionWaferId]);

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <TabStrip label="WF MAP 기능" active={pageTab} onChange={setPageTab} items={[
        { k: "map", l: "WF MAP" },
        { k: "compare", l: "비교" },
      ]} />
      {error && <div style={{ padding: "9px 12px", border: "1px solid var(--danger)", borderRadius: 6, color: "var(--danger)", fontSize: 13 }}>{error}</div>}

      {pageTab === "map" && <>
      <div style={{ display: "flex", justifyContent: "flex-start", alignItems: "flex-end", gap: 8, flexWrap: "wrap" }}>
        <label style={{ ...inputLabel, minWidth: 190 }}>데이터 유형
          <Select value={dataKind} onChange={event => { setDataKind(event.target.value); setMap(null); }}>
            {DATA_KIND_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </Select>
        </label>
        <label style={{ ...inputLabel, minWidth: 220 }}>제품
          <Select value={dataKind === "yield" ? product : shotProduct}
            onChange={event => dataKind === "yield" ? setProduct(event.target.value) : setShotProduct(event.target.value)}>
            {(dataKind === "yield" ? (boot?.products || []) : shotProducts).map(name => <option key={name} value={name}>{name}</option>)}
          </Select>
        </label>
        <Button onClick={loadBootstrap}>새로고침</Button>
        <Pill tone={matchedGeometry ? "ok" : "warn"}>{matchedGeometry ? `Full Shot 자동 연결 · ${matchedGeometry.vehicle}` : "같은 제품의 Full Shot 없음"}</Pill>
        {!!boot && <Pill tone="neutral">동일 기준 300 mm WF</Pill>}
      </div>

      {boot && dataKind === "yield" && !boot.products?.length && <EmptyState icon="◫" title="BIN 데이터가 있는 제품이 없습니다" />}

      {dataKind === "yield" && product && <Card title={`제품별 BIN Map 설정 — ${product}`}
        right={<Button variant="primary" disabled={!canEdit || busy || !config.source || !vehicle || !config.shot_layout?.enabled} onClick={save}>설정 저장</Button>}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 12 }}>
          <label style={{ ...inputLabel, minWidth: 260 }}>BIN TABLE
            <Select value={config.source || ""} disabled={!canEdit} onChange={event => {
              const source = event.target.value;
              setConfig(current => ({ ...current, source, fields: {}, shot_layout: { ...DEFAULT_SHOT_LAYOUT } }));
              setBinRows(spreadsheetBinRows([{ bin: "", color: "#94A3B8" }]));
              setPreview(null); setMap(null); setScanPreview(null);
            }}>
              <option value="">TABLE 선택</option>
              {sourceOptions.map(source => <option key={source.id} value={source.id}>{source.name} · {source.id}</option>)}
            </Select>
          </label>
          {config.source && <Button disabled={busy} onClick={() => loadPreview()}>열 자동 매칭</Button>}
          <Button variant="primary" disabled={!canEdit || busy || !config.source || !vehicle} onClick={scanShotLayout}>
            {busy ? "Scan 중…" : "Scan"}
          </Button>
          <Pill tone={sourceOptions.length ? "ok" : "warn"}>BIN 후보 {sourceOptions.length}개</Pill>
        </div>
        {!sourceOptions.length && <div style={{ color: "var(--warn)", fontSize: 12, marginBottom: 10 }}>
          DB root에서 이름에 BIN이 포함된 TABLE을 찾지 못했습니다.
        </div>}
        {!vehicle && <div style={{ color: "var(--warn)", fontSize: 12, marginBottom: 10 }}>
          {product}과 같은 제품명의 Full Shot geometry가 없어 Scan할 수 없습니다.
        </div>}
        {config.source && <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
          {FIELD_LABELS.map(([key, label]) => <label key={key} style={inputLabel}>{label}
            <Select value={config.fields?.[key] || ""} disabled={!canEdit} onChange={event => setField(key, event.target.value)}>
              <option value="">미사용</option>
              {(preview?.columns || []).map(column => <option key={column} value={column}>{column}</option>)}
            </Select>
          </label>)}
        </div>}
        <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12, marginBottom: 14, background: "var(--bg-secondary)" }}>
          <b style={{ display: "block", fontSize: 12 }}>BIN Scan · Full Shot 자동 매칭</b>
          <span style={{ display: "block", marginTop: 3, fontSize: 11, color: "var(--muted)" }}>
            Scan하면 BIN 목록을 찾고, {product} Full Shot의 칩 배열과 좌표에 자동으로 맞춘 뒤 첫 WAFER를 미리 보여줍니다.
          </span>
          {scanPreview ? <>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 10 }}>
              <Pill tone="ok">BIN {scanPreview.bins?.length || 0}개</Pill>
              <Pill tone="ok">Full Shot {scanPreview.full_shot_count || 0}개</Pill>
              <Pill tone={scanPreview.partial_shot_count ? "warn" : "neutral"}>Partial {scanPreview.partial_shot_count || 0}개</Pill>
              <Pill tone="neutral">Full Shot당 {scanPreview.layout?.expected_die || 0} die</Pill>
              <Pill tone="neutral">geometry 매칭 {Number(scanPreview.matched_rows || 0).toLocaleString()} / {Number(scanPreview.scan_rows || 0).toLocaleString()} die</Pill>
            </div>
            <div style={{ width: "min(100%, 480px)", marginTop: 12 }}>
              <div style={{ marginBottom: 6, fontSize: 11, fontWeight: 800 }}>WAFER {scanPreview.preview_wafer_id || "(미지정)"} · Scan 미리보기</div>
              <WfGeometryMap kind="yield" rows={scanPreview.preview_rows || []} geometry={scanPreview.geometry}
                colors={binColorMap} shotLayout={scanPreview.layout} />
            </div>
          </> : <div style={{ marginTop: 10, fontSize: 11, color: "var(--muted)" }}>
            BIN TABLE과 필수 열을 확인한 뒤 위의 Scan을 눌러 주세요.
          </div>}
        </div>
        {hasBinSettings && <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 10, flexWrap: "wrap", marginBottom: 7 }}>
          <div>
            <b style={{ display: "block", fontSize: 12 }}>BIN MAP · 제품별 저장</b>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>Excel의 BIN·BIN COLOR 두 열을 복사해 첫 BIN 셀에 붙여넣을 수 있습니다.</span>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <Button onClick={copyBinTable}>표 복사</Button>
            <Button variant="primary" disabled={!canEdit || busy || !config.source || !vehicle || !config.shot_layout?.enabled} onClick={save}>BIN MAP 저장</Button>
          </div>
        </div>
        <div style={{ maxWidth: 620 }}>
          <SpreadsheetPasteGrid
            ariaLabel="BIN MAP 스프레드시트"
            columns={BIN_GRID_COLUMNS}
            columnLabels={{ bin: "BIN", color: "BIN COLOR (#RRGGBB)" }}
            aliases={{ BIN: "bin", "BIN NAME": "bin", "BIN COLOR": "color", BIN_COLOR: "color", COLOR: "color" }}
            placeholders={{ bin: "1", color: "#22C55E" }}
            rows={binRows}
            onChange={setBinRows}
            colorColumn="color"
            showRowNumbers={false}
            disabled={!canEdit}
            minRows={BIN_TABLE_VISIBLE_ROWS}
            maxRows={200}
            maxHeight={365}
          />
        </div>
        </>}
      </Card>}

      {(dataKind === "yield" ? product : shotProduct) && <Card title={`WF MAP · ${DATA_KIND_OPTIONS.find(row => row[0] === dataKind)?.[1] || dataKind}`} right={map && <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 11, color: "var(--muted)", paddingRight: 4 }}>
          한 줄 Map 수
          <input type="range" min="3" max="10" step="1" value={mapColumns} aria-label="한 줄 Map 수"
            onChange={event => setMapColumns(Number(event.target.value))} style={{ width: 92 }} />
          <b style={{ minWidth: 24, color: "var(--text-primary)" }}>{mapColumns}개</b>
        </label>
        <Pill tone="ok">Wafer {waferMaps.length}개</Pill>
        {dataKind === "et" && map.selected_step && <Pill tone="neutral">STEP {map.selected_step}</Pill>}
        {dataKind === "et" && map.selected_step_seq && <Pill tone="neutral">DCOP SEQ {map.selected_step_seq}</Pill>}
        <Pill tone="neutral">rows {map.rows?.length || 0} · {map.product || map.source}</Pill>
      </div>}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
          <label style={{ ...inputLabel, minWidth: 250 }}>LOT ID (root_lot_id)
            <Input value={rootLotId} onChange={event => setRootLotId(event.target.value)} placeholder="ROOT LOT ID 입력"
              onKeyDown={event => { if (event.key === "Enter") loadMap(); }} />
          </label>
          {dataKind === "et" && <label style={{ ...inputLabel, minWidth: 205 }}>ET ITEM 유형
            <Select value={etItemSource} onChange={event => { setEtItemSource(event.target.value); setMap(null); }}>
              <option value="raw">원본 ITEM ID</option>
              <option value="et_download">ET Download · REAL/ADDP</option>
              {isAdmin && <option value="test_addp">관리자 · Test ADDP</option>}
            </Select>
          </label>}
          {dataKind === "et" && etItemSource === "et_download" && <label style={{ ...inputLabel, minWidth: 245 }}>ET Download ITEM
            <Select value={itemId} disabled={etItemsBusy || !etDownloadItems.length} onChange={event => { setItemId(event.target.value); setMap(null); }}>
              {!etDownloadItems.length && <option value="">{etItemsBusy ? "ITEM 불러오는 중…" : "매칭 reformatter 없음"}</option>}
              {etDownloadItems.map(row => <option key={row.alias} value={row.alias}>
                {row.category === "addp" ? "ADDP" : "REAL"} · {row.alias}
              </option>)}
            </Select>
          </label>}
          {dataKind === "et" && etItemSource === "test_addp" && <>
            <label style={{ ...inputLabel, minWidth: 190 }}>Test ADDP alias
              <Input value={testAddpAlias} onChange={event => setTestAddpAlias(event.target.value)} placeholder="예: MY_INDEX" />
            </label>
            <label style={{ ...inputLabel, minWidth: 360, flex: "1 1 360px" }}>ADDP Form
              <Input value={testAddpForm} onChange={event => setTestAddpForm(event.target.value)}
                placeholder="예: ({VTH_IDX} - avg({VTH_IDX})) / std({VTH_IDX})" />
            </label>
          </>}
          {dataKind === "et" && etItemSource === "raw" && <label style={{ ...inputLabel, minWidth: 170 }}>원본 ITEM ID
            <Input value={itemId} onChange={event => setItemId(event.target.value)} placeholder="빈 값이면 첫 항목" list="wf-map-items" />
            <datalist id="wf-map-items">{(map?.items || []).map(value => <option key={value} value={value} />)}</datalist>
          </label>}
          {dataKind === "inline" && <label style={{ ...inputLabel, minWidth: 190 }}>Inline ITEM · Matching 규칙
            <Select value={itemId} disabled={!mainInlineItems.length} onChange={event => {
              const nextItem = event.target.value;
              const itemRules = rulesForValue(mainInlineRules, "item_id", nextItem);
              const nextStep = matchingValues(itemRules, "step_id")[0] || "";
              const stepRules = rulesForValue(itemRules, "step_id", nextStep);
              const preferred = stepRules.find(rule => ruleUsable(rule, vehicle)) || stepRules[0];
              setItemId(nextItem); setStepId(nextStep); setInlineTable(String(preferred?.matching_table || "")); setMap(null);
            }}>
              {!mainInlineItems.length && <option value="">Inline Matching 규칙 없음</option>}
              {mainInlineItems.map(value => <option key={value} value={value}>{value}</option>)}
            </Select>
          </label>}
          {dataKind === "et" && <label style={{ ...inputLabel, minWidth: 150 }}>STEP ID · 선택
            <Input value={stepId} onChange={event => { setStepId(event.target.value); setStepSeq(""); }} placeholder={dataKind === "et" ? "미선택 시 첫 STEP" : "전체"} list="wf-map-steps" />
            <datalist id="wf-map-steps">{(map?.steps || []).map(value => <option key={value} value={value} />)}</datalist>
          </label>}
          {dataKind === "inline" && <label style={{ ...inputLabel, minWidth: 180 }}>Inline STEP · Matching 규칙
            <Select value={stepId} disabled={!mainInlineSteps.length} onChange={event => {
              const nextStep = event.target.value;
              const stepRules = rulesForValue(mainInlineItemRules, "step_id", nextStep);
              const preferred = stepRules.find(rule => ruleUsable(rule, vehicle)) || stepRules[0];
              setStepId(nextStep); setInlineTable(String(preferred?.matching_table || "")); setMap(null);
            }}>
              {!mainInlineSteps.length && <option value="">STEP 규칙 없음</option>}
              {mainInlineSteps.map(value => <option key={value} value={value}>{value}</option>)}
            </Select>
          </label>}
          {dataKind === "et" && <label style={{ ...inputLabel, minWidth: 210 }}>STEP SEQ · DCOP 측정꾸러미
            <Input value={stepSeq} onChange={event => setStepSeq(event.target.value)} placeholder="미선택 시 첫 측정꾸러미" list="wf-map-step-seqs" />
            <datalist id="wf-map-step-seqs">{(map?.step_seqs || []).map(value => <option key={value} value={value} />)}</datalist>
          </label>}
          {dataKind === "inline" && <label style={{ ...inputLabel, minWidth: 220 }}>Inline Mapsetting · ITEM별 선택
            <Select value={inlineTable} onChange={event => setInlineTable(event.target.value)}>
              {!mainInlineStepRules.length && <option value="">matching_table 없음</option>}
              {mainInlineStepRules.map(rule => <option key={rule.matching_table} value={rule.matching_table}
                disabled={!ruleUsable(rule, vehicle)}>{rule.matching_table} · {rule.shot_count || 0} shots{ruleUsable(rule, vehicle) ? "" : " · Mapsetting 사용 불가"}</option>)}
            </Select>
          </label>}
          {dataKind !== "yield" && <label style={{ ...inputLabel, minWidth: 190 }}>표현 방식
            <Select value={renderMode} onChange={event => setRenderMode(event.target.value)}>
              <option value="shot">값 있는 Shot 전체 컬러</option>
              <option value="die">WF 전체 · die 단위 보간</option>
              <option value="surface">WF 전체 · Contour Map</option>
            </Select>
          </label>}
          {dataKind !== "yield" && renderMode !== "shot" && <label style={{ ...inputLabel, minWidth: 165 }}>보간 방법
            <Select value={interpolationMethod} onChange={event => setInterpolationMethod(event.target.value)}>
              <option value="idw">IDW · 거리 역가중</option>
              <option value="nearest">Nearest · 최근접</option>
              <option value="gaussian">Gaussian · 부드러운 RBF</option>
            </Select>
          </label>}
          {dataKind !== "yield" && renderMode !== "shot" && <label style={{ ...inputLabel, minWidth: 190 }}>TEG S/L 위치 · 리스트 선택
            <Select value={anchorTeg} onChange={event => setAnchorTeg(event.target.value)}>
              <option value="">Shot center · 기본</option>
              {(map?.geometry?.tegs || []).map(teg => <option key={`${teg.teg}:${teg.src_row}`} value={teg.teg}>{teg.teg}</option>)}
            </Select>
          </label>}
          <Button variant="primary" disabled={busy || !rootLotId.trim()
            || (dataKind === "yield" ? (!config.source || !vehicle || !config.shot_layout?.enabled) : (!vehicle || !shotProduct))
            || (dataKind === "inline" && !mainInlineReady)
            || (dataKind === "et" && etItemSource === "et_download" && !itemId.trim())
            || (dataKind === "et" && etItemSource === "test_addp" && (!testAddpAlias.trim() || !testAddpForm.trim()))}
            onClick={loadMap}>{busy ? "조회 중…" : "WF MAP 조회"}</Button>
          {map?.rows?.length > 0 && <Button onClick={addCurrentDimension}>+ 현재 차원 추가</Button>}
        </div>
        {dataKind !== "yield" && <div style={{ marginBottom: 12, padding: "9px 11px", borderRadius: 7, background: "var(--bg-secondary)", fontSize: 11, color: "var(--muted)" }}>
          <b>Shot 전체 컬러</b>만 값이 있는 shot에 한정됩니다. die 보간과 Contour Map은 측정 shot을 sample point로 사용해 <b>shot이 없는 영역을 포함한 WF 전체</b>를 채웁니다.
          TEG S/L 미선택 시 Shot center가 측정 위치이며, 위치를 선택하면 모든 측정점이 해당 shot 내부 TEG 중심으로 이동합니다.
          {dataKind === "et" && etItemSource === "et_download" && <><br /><b>ET Download REAL·ADDP</b>는 같은 vehicle reformatter, 공개 설정, REAL abs/scale 및 재귀 ADDP 계산식을 그대로 사용합니다.
            {selectedEtSpec?.category === "addp" && <> 현재 수식: <code>{selectedEtSpec.addp_form}</code></>}</>}
          {dataKind === "et" && etItemSource === "test_addp" && <><br /><b>Test ADDP</b>는 관리자 전용이며 vehicle CSV에 저장하지 않고 현재 MAP 조회에서만 계산합니다.</>}
          {dataKind === "et" && <><br />ET Map은 <b>STEP ID + STEP SEQ(DCOP 측정꾸러미)</b> 조합을 분리합니다. 미선택 시 각각 첫 항목을 선택하며 서로 다른 측정꾸러미를 한 Map에 평균하지 않습니다.</>}
          {dataKind === "inline" && <><br />INLINE은 <code>inline_matching.csv</code>의 <b>제품 + STEP + ITEM → matching_table</b> 규칙과 TEG 위치조회의 <b>Inline Mapsetting</b>이 모두 있어야 조회됩니다. 원천 <code>subitem_id</code> 중 선택한 TABLE에 등록된 위치만 shot 좌표로 변환합니다.</>}
        </div>}
        {dataKind === "inline" && !mainInlineReady && <div style={{ margin: "-5px 0 12px", color: "var(--warn)", fontSize: 11 }}>
          {!mainInlineRules.length ? `${shotProduct}에 Inline Matching table이 없어 WF MAP을 조회할 수 없습니다.` :
            "선택한 Inline ITEM·STEP에 연결된 TEG Inline Mapsetting이 없습니다."}
        </div>}
        {!!map?.notice && <div style={{ margin: "-5px 0 12px", color: "var(--warn)", fontSize: 11 }}>{map.notice}</div>}
        {!!map?.rule_errors?.length && <div style={{ margin: "-5px 0 12px", color: "var(--danger)", fontSize: 11 }}>
          {map.rule_errors.join(" · ")}
        </div>}
        {!map ? <EmptyState icon="◫" title="WF MAP을 조회해 주세요"
          hint="ROOT LOT ID를 입력하면 WAFER ID별 Map을 같은 300 mm 기준으로 나눠 표시합니다." /> : !map.rows?.length ?
          <EmptyState icon="◫" title={`${map.root_lot_id || rootLotId}에 매칭되는 ${dataKind.toUpperCase()} 데이터가 없습니다`} /> :
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {dataKind === "yield" && <section style={{ width: "min(100%, 360px)", border: "1px solid var(--line)", borderRadius: 7, overflow: "hidden" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", minHeight: 34, padding: "0 10px", borderBottom: "1px solid var(--line)", background: "var(--bg-secondary)" }}>
                <b style={{ fontSize: 12 }}>BIN 범례</b>
                <span style={{ fontSize: 11, color: "var(--muted)" }}>{map.bins?.length || 0}개</span>
              </div>
              <div style={{ maxHeight: BIN_TABLE_VISIBLE_ROWS * 30, overflowY: "auto", scrollbarGutter: "stable", padding: "4px 0" }}>
                {(map.bins || []).map(item => <div key={item.bin} style={{ display: "grid", gridTemplateColumns: "18px minmax(60px,1fr) auto", alignItems: "center", gap: 7, minHeight: 30, padding: "0 10px", fontSize: 12 }}>
                  <span style={{ width: 14, height: 14, border: "1px solid var(--line)", background: safeColor(binColorMap?.[item.bin]) }} />
                  <b style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{item.bin || "(빈 값)"}</b>
                  <span style={{ color: "var(--muted)" }}>{Number(item.count || 0).toLocaleString()} die</span>
                </div>)}
              </div>
            </section>}

            <div style={{ display: "grid", gridTemplateColumns: `repeat(${mapColumns}, minmax(0, 1fr))`, gap: 14, alignItems: "start" }}>
              {waferMaps.map(wafer => <section key={wafer.waferId} style={{ minWidth: 0, padding: 12, border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg-secondary)" }}>
                <div style={{ marginBottom: 8 }}>
                  <b title={`${map.root_lot_id || rootLotId} #${wafer.label}`} style={{ display: "block", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 13, fontFamily: "var(--font-mono)" }}>
                    {map.root_lot_id || rootLotId} #{wafer.label}
                  </b>
                </div>
                <div style={{ minWidth: 0 }}>
                  {map.geometry ? <WfGeometryMap kind={dataKind} rows={wafer.rows} geometry={map.geometry}
                    colors={binColorMap} renderMode={renderMode} anchorTeg={anchorTeg} shotLayout={map.shot_layout}
                    interpolationMethod={interpolationMethod} /> :
                    <YieldDieMap rows={wafer.rows} colors={binColorMap} shots={wafer.shots} />}
                </div>
              </section>)}
            </div>

            {!!map.full_shot_count && <div style={{ padding: 9, borderRadius: 7, background: "var(--bg-secondary)", fontSize: 11, color: "var(--muted)" }}>
              Chart Builder에서 <b>WF MAP · Full Shot</b> DB를 선택하고 ET/INLINE과
              <code> root_lot_id, wafer_id, shot_x, shot_y</code>로 JOIN하면 <code>shot_yield</code> Corr 분석에 사용할 수 있습니다.
            </div>}
            {map.overflow && <span style={{ color: "var(--warn)", fontSize: 11 }}>최대 100,000개까지만 표시했습니다.</span>}
          </div>}
      </Card>}

      {!!dimensionMaps.length && <Card title={`같은 WF · 다차원 비교 (${dimensionMaps.length})`} right={<div style={{ display: "flex", gap: 7, alignItems: "center" }}>
        <Pill tone="ok">동일 300 mm 기준</Pill><Button onClick={() => setDimensionMaps([])}>비우기</Button>
      </div>}>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 12 }}>
          <label style={{ ...inputLabel, minWidth: 170 }}>같이 볼 WAFER ID
            <Select value={dimensionWaferId} onChange={event => setDimensionWaferId(event.target.value)}>
              {dimensionWaferIds.map(value => <option key={value} value={value}>{value || "(미지정)"}</option>)}
            </Select>
          </label>
          <label style={{ ...inputLabel, minWidth: 190 }}>공통 표현 방식
            <Select value={renderMode} onChange={event => setRenderMode(event.target.value)}>
              <option value="shot">값 있는 Shot 전체 컬러</option>
              <option value="die">WF 전체 · die 단위 보간</option>
              <option value="surface">WF 전체 · Contour Map</option>
            </Select>
          </label>
          {renderMode !== "shot" && <label style={{ ...inputLabel, minWidth: 170 }}>공통 보간 방법
            <Select value={interpolationMethod} onChange={event => setInterpolationMethod(event.target.value)}>
              <option value="idw">IDW · 거리 역가중</option>
              <option value="nearest">Nearest · 최근접</option>
              <option value="gaussian">Gaussian · 부드러운 RBF</option>
            </Select>
          </label>}
          {renderMode !== "shot" && <label style={{ ...inputLabel, minWidth: 200 }}>공통 TEG S/L 위치 · 리스트 선택
            <Select value={anchorTeg} onChange={event => setAnchorTeg(event.target.value)}>
              <option value="">Shot center · 기본</option>
              {(dimensionMaps.find(row => row.map?.geometry?.tegs?.length)?.map?.geometry?.tegs || []).map(teg =>
                <option key={`${teg.teg}:${teg.src_row}`} value={teg.teg}>{teg.teg}</option>)}
            </Select>
          </label>}
        </div>
        <div style={{ padding: "8px 10px", marginBottom: 12, borderRadius: 7, background: "var(--bg-secondary)", color: "var(--muted)", fontSize: 11 }}>
          각 차원은 같은 wafer와 동일한 물리 크기·좌표계로 고정됩니다. ET/Inline Contour Map은 측정 shot 밖까지 포함한 WF 전체를 보간합니다. 차원은 최대 6개까지 고정됩니다.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(3, dimensionMaps.length)}, minmax(0,1fr))`, gap: 12, alignItems: "start" }}>
          {dimensionMaps.map(dimension => {
            const waferRows = (dimension.map?.rows || []).filter(row => String(row.wafer ?? "") === dimensionWaferId);
            return <section key={dimension.key} style={{ minWidth: 0, border: "1px solid var(--line)", borderRadius: 8, padding: 10, background: "var(--bg-secondary)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 8 }}>
                <b style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{dimension.label}</b>
                <Pill tone="neutral">{dimension.map?.product}</Pill>
                <Button style={{ marginLeft: "auto" }} onClick={() => setDimensionMaps(current => current.filter(row => row.key !== dimension.key))}>×</Button>
              </div>
              {!waferRows.length ? <EmptyState icon="◫" title={`WF ${dimensionWaferId} 데이터 없음`} /> : dimension.map?.geometry ?
                <WfGeometryMap kind={dimension.kind} rows={waferRows} geometry={dimension.map.geometry} colors={dimension.colors}
                  renderMode={dimension.kind === "yield" ? "shot" : renderMode} anchorTeg={anchorTeg}
                  shotLayout={dimension.map.shot_layout} interpolationMethod={interpolationMethod} /> :
                <YieldDieMap rows={waferRows} colors={dimension.colors} shots={(dimension.map?.shot_rows || []).filter(row => String(row.wafer ?? "") === dimensionWaferId)} />}
            </section>;
          })}
        </div>
      </Card>}
      </>}

      {pageTab === "compare" && !comparisonProducts.length && <EmptyState icon="↗" title="Yield·ET·Inline에 공통으로 존재하는 제품이 없습니다"
        hint="Yield·ET·Inline 중 두 DB 이상에 같은 제품명이 있어야 Shot Corr. 비교를 실행할 수 있습니다." />}
      {pageTab === "compare" && !!relationProduct && <>
        <TabStrip label="비교 결과 보기" active={comparisonView} onChange={setComparisonView} items={[
          { k: "corr", l: "비교 Corr." }, { k: "map", l: "비교 MAP" },
        ]} />
        <Card title="기준 불량 지표 ↔ ET/Inline Shot 관계 탐색" right={<div style={{ display: "flex", gap: 6 }}>
          <Pill tone="neutral">선택 {relationMetrics.length}개</Pill>
          <Button onClick={addRelationMetric} disabled={relationMetrics.length >= 30}>+ 지표 추가</Button>
        </div>}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
            <label style={{ ...inputLabel, minWidth: 210 }}>비교 제품
              <Select value={relationProduct} onChange={event => setRelationProduct(event.target.value)}>
                {comparisonProducts.map(name => <option key={name} value={name}>{name}</option>)}
              </Select>
            </label>
            <label style={{ ...inputLabel, minWidth: 230 }}>ROOT LOT ID
              <Input value={rootLotId} onChange={event => { setRootLotId(event.target.value); setRelationResult(null); }} placeholder="ROOT LOT ID 입력" />
            </label>
            <label style={{ ...inputLabel, minWidth: 170 }}>ET/Inline TKOUT 시작
              <Input type="date" value={relationTkoutFrom} onChange={event => { setRelationTkoutFrom(event.target.value); setRelationResult(null); }} />
            </label>
            <label style={{ ...inputLabel, minWidth: 170 }}>ET/Inline TKOUT 종료
              <Input type="date" value={relationTkoutTo} onChange={event => { setRelationTkoutTo(event.target.value); setRelationResult(null); }} />
            </label>
            <Pill tone={vehicle ? "ok" : "warn"}>{vehicle ? `Full Shot 자동 연결 · ${vehicle}` : "같은 제품의 Full Shot 없음"}</Pill>
            <label style={{ ...inputLabel, minWidth: 190 }}>마커 컬러링
              <Select value={relationColorSource} onChange={event => { setRelationColorSource(event.target.value); setRelationResult(null); }}>
                <option value="none">컬러링 없음</option><option value="split">Split · ET_TABLE_*</option><option value="fab">FAB · ML_TABLE_*</option>
              </Select>
            </label>
            {relationColorSource === "split" && <label style={{ ...inputLabel, minWidth: 220 }}>Split TABLE
              <Select value={comparisonSplitSource} onChange={event => setComparisonSplitSource(event.target.value)}>
                <option value="">TABLE 선택</option>{(boot?.split_sources || []).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
              </Select>
            </label>}
            {relationColorSource === "fab" && <label style={{ ...inputLabel, minWidth: 240 }}>ML_TABLE FAB 컬럼
              <Select value={relationFabField} disabled={!relationOptions.fab_fields?.length} onChange={event => setRelationFabField(event.target.value)}>
                {!relationOptions.fab_fields?.length && <option value="">FAB 컬럼 없음</option>}
                {(relationOptions.fab_fields || []).map(value => <option key={value} value={value}>{value}</option>)}
              </Select>
            </label>}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {relationMetrics.map((metric, index) => {
              const itemRules = rulesForValue(relationInlineRules, "item_id", metric.item_id);
              const stepRules = rulesForValue(itemRules, "step_id", metric.step_id);
              const inlineItems = matchingValues(relationInlineRules, "item_id");
              const inlineSteps = matchingValues(itemRules, "step_id");
              return <div key={metric.id} style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "flex-end", padding: 9, border: "1px solid var(--line)", borderRadius: 7, background: "var(--bg-secondary)" }}>
                <Pill tone={relationMetricReady(metric) ? "ok" : "warn"}>{index === 0 ? "기준 지표" : `탐색 후보 ${index}`}</Pill>
                <label style={{ ...inputLabel, minWidth: 135 }}>데이터
                  <Select value={metric.kind} onChange={event => updateRelationMetric(metric.id, { kind: event.target.value, item_id: "", step_id: "", step_seq: "", inline_table: "" })}>
                    {productSourceKinds.map(kind => <option key={kind} value={kind}>{kind === "yield" ? "Yield/BIN" : kind.toUpperCase()}</option>)}
                  </Select>
                </label>
                {metric.kind === "yield" && <label style={{ ...inputLabel, minWidth: 205 }}>{index === 0 ? "기준 Yield/BIN · Scan LV/HV" : "Yield 또는 BIN"}
                  <Input value={metric.bin_name || "yield"} onChange={event => updateRelationMetric(metric.id, { bin_name: event.target.value })} placeholder="yield, LV, HV 등" />
                </label>}
                {metric.kind === "et" && <>
                  <label style={{ ...inputLabel, minWidth: 190 }}>ET ITEM ID
                    <Input value={metric.item_id || ""} onChange={event => updateRelationMetric(metric.id, { item_id: event.target.value })} placeholder="ITEM ID" />
                  </label>
                  <label style={{ ...inputLabel, minWidth: 160 }}>ET STEP ID
                    <Input value={metric.step_id || ""} onChange={event => updateRelationMetric(metric.id, { step_id: event.target.value, step_seq: "" })} placeholder="미선택 시 첫 STEP" />
                  </label>
                  <label style={{ ...inputLabel, minWidth: 170 }}>ET STEP SEQ
                    <Input value={metric.step_seq || ""} onChange={event => updateRelationMetric(metric.id, { step_seq: event.target.value })} placeholder="미선택 시 첫 SEQ" />
                  </label>
                </>}
                {metric.kind === "inline" && <>
                  <label style={{ ...inputLabel, minWidth: 180 }}>Inline ITEM
                    <Select value={metric.item_id || ""} disabled={!inlineItems.length} onChange={event => {
                      const item = event.target.value, rules = rulesForValue(relationInlineRules, "item_id", item);
                      const step = matchingValues(rules, "step_id")[0] || "";
                      const candidates = rulesForValue(rules, "step_id", step);
                      const table = candidates.find(row => ruleUsable(row, vehicle)) || candidates[0];
                      updateRelationMetric(metric.id, { item_id: item, step_id: step, inline_table: String(table?.matching_table || "") });
                    }}><option value="">ITEM 선택</option>{inlineItems.map(value => <option key={value} value={value}>{value}</option>)}</Select>
                  </label>
                  <label style={{ ...inputLabel, minWidth: 170 }}>Inline STEP
                    <Select value={metric.step_id || ""} disabled={!inlineSteps.length} onChange={event => {
                      const step = event.target.value, candidates = rulesForValue(itemRules, "step_id", step);
                      const table = candidates.find(row => ruleUsable(row, vehicle)) || candidates[0];
                      updateRelationMetric(metric.id, { step_id: step, inline_table: String(table?.matching_table || "") });
                    }}><option value="">STEP 선택</option>{inlineSteps.map(value => <option key={value} value={value}>{value}</option>)}</Select>
                  </label>
                  <label style={{ ...inputLabel, minWidth: 220 }}>Inline Mapsetting
                    <Select value={metric.inline_table || ""} disabled={!stepRules.length} onChange={event => updateRelationMetric(metric.id, { inline_table: event.target.value })}>
                      <option value="">TABLE 선택</option>{stepRules.map(rule => <option key={rule.matching_table} value={rule.matching_table} disabled={!ruleUsable(rule, vehicle)}>
                        {rule.matching_table} · {rule.shot_count || 0} shots{ruleUsable(rule, vehicle) ? "" : " · 사용 불가"}
                      </option>)}</Select>
                  </label>
                </>}
                {index > 0 && <Button onClick={() => makeRelationTarget(metric.id)}>기준으로 지정</Button>}
                <Button variant="danger" disabled={relationMetrics.length <= 2} onClick={() => { setRelationMetrics(current => current.filter(row => row.id !== metric.id)); setRelationResult(null); }}>삭제</Button>
              </div>;
            })}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}>
            <Button variant="primary" disabled={!relationReady || relationTkoutInvalid || comparisonBusy || (relationColorSource === "split" && !comparisonSplitSource) || (relationColorSource === "fab" && !relationFabField)} onClick={loadRelations}>
              {comparisonBusy ? "관계 계산 중…" : "기준 대비 Corr. 계산"}
            </Button>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>첫 행의 기준 불량 지표와 아래 ET/Inline 후보만 순위를 계산합니다. 공통 root_lot_id·wafer_id·shot_x·shot_y를 사용합니다.</span>
            {relationTkoutInvalid && <Pill tone="warn">TKOUT 시작일이 종료일보다 늦습니다</Pill>}
          </div>
        </Card>
        {relationResult && comparisonView === "corr" && <Card title={`비교 Corr. · 관계성 순위 ${relationResult.pairs?.length || 0}개`} right={<Pill tone="ok">shot union {relationResult.point_count || 0}</Pill>}>
          <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--line)", borderRadius: 8, marginBottom: 14 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}><thead style={{ position: "sticky", top: 0, background: "var(--bg-secondary)" }}><tr>
              {['순위', 'X', 'Y', '관계 점수', '|r|', 'r', 'R²', '변곡 X', '개선', 'n', '분류'].map(label => <th key={label} style={{ padding: 7, textAlign: "left", borderBottom: "1px solid var(--line)" }}>{label}</th>)}
            </tr></thead><tbody>{(relationResult.pairs || []).map((pair, index) => <tr key={pair.id} onClick={() => setRelationPairId(pair.id)} style={{ cursor: "pointer", background: relationPairId === pair.id ? "rgba(62,123,214,.08)" : "transparent" }}>
              {[index + 1, pair.left_label, pair.right_label, pair.relationship_score == null ? "—" : pair.relationship_score.toFixed(4), pair.pearson_r == null ? "—" : Math.abs(pair.pearson_r).toFixed(4), pair.pearson_r == null ? "—" : pair.pearson_r.toFixed(4), pair.fit?.r2 == null ? "—" : pair.fit.r2.toFixed(4), pair.threshold?.is_candidate ? comparisonValue(pair.threshold.threshold) : "—", pair.threshold?.is_candidate ? `${(Number(pair.threshold.improvement) * 100).toFixed(1)}%` : "—", pair.sample_count].map((value, cell) => <td key={cell} style={{ padding: 7, borderBottom: "1px solid var(--line)" }}>{value}</td>)}
              <td style={{ padding: 5, borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>
                <Button onClick={event => { event.stopPropagation(); saveRelationStatus(pair, "significant"); }}>유의차 있음</Button>{" "}
                <Button onClick={event => { event.stopPropagation(); saveRelationStatus(pair, "not_significant"); }}>없음</Button>
                {pair.saved?.status && <Pill tone={pair.saved.status === "significant" ? "ok" : "neutral"}>{pair.saved.status}</Pill>}
              </td>
            </tr>)}</tbody></table>
          </div>
          <RelationScatter data={relationResult} pair={(relationResult.pairs || []).find(row => row.id === relationPairId) || relationResult.pairs?.[0]} />
        </Card>}
        {relationResult && comparisonView === "map" && <Card title="비교 MAP · 선택 지표 공간 패턴" right={<Pill tone="ok">shot union {relationResult.point_count || 0}</Pill>}>
          <label style={{ ...inputLabel, maxWidth: 560, marginBottom: 12 }}>비교할 관계
            <Select value={relationPairId || relationResult.pairs?.[0]?.id || ""} onChange={event => setRelationPairId(event.target.value)}>
              {(relationResult.pairs || []).map((pair, index) => <option key={pair.id} value={pair.id}>{index + 1}. {pair.left_label} ↔ {pair.right_label} · r {pair.pearson_r == null ? "—" : pair.pearson_r.toFixed(4)}</option>)}
            </Select>
          </label>
          <RelationMapComparison data={relationResult} pair={(relationResult.pairs || []).find(row => row.id === relationPairId) || relationResult.pairs?.[0]} />
        </Card>}
        {!relationResult && comparisonView === "map" && <EmptyState icon="◫" title="비교 MAP을 만들 지표가 없습니다" hint="위에서 기준 지표와 ET/Inline 후보를 설정한 뒤 Corr. 계산을 실행해 주세요." />}
      </>}

      {false && pageTab === "compare" && !!product && comparisonProducts.some(name => String(name).toLowerCase() === String(product).toLowerCase()) && <Card title="Shot 비교 분석 · Yield/BIN ↔ ET ↔ Inline" right={comparison && <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Pill tone="ok">Yield↔ET {comparison.yield_et_count || 0} shots</Pill>
        <Pill tone="ok">Yield↔Inline {comparison.yield_inline_count || 0} shots</Pill>
        <Pill tone="neutral">3종 공통 {comparison.triple_count || 0} shots</Pill>
        {comparison.selected_step && <Pill tone="neutral">STEP {comparison.selected_step}</Pill>}
        {comparison.selected_step_seq && <Pill tone="neutral">DCOP SEQ {comparison.selected_step_seq}</Pill>}
      </div>}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
          <label style={{ ...inputLabel, minWidth: 210 }}>비교 제품 · Yield/ET/Inline 공통
            <Select value={product} onChange={event => { setProduct(event.target.value); setComparison(null); }}>
              {comparisonProducts.map(name => <option key={name} value={name}>{name}</option>)}
            </Select>
          </label>
          <label style={{ ...inputLabel, minWidth: 230 }}>ROOT LOT ID
            <Input value={rootLotId} onChange={event => { setRootLotId(event.target.value); setComparison(null); }} placeholder="ROOT LOT ID 입력" />
          </label>
          <Pill tone={vehicle ? "ok" : "warn"}>{vehicle ? `Full Shot 자동 연결 · ${vehicle}` : "같은 제품의 Full Shot 없음"}</Pill>
          <label style={{ ...inputLabel, minWidth: 155 }}>BIN 또는 Yield
            <Input value={comparisonBin} onChange={event => setComparisonBin(event.target.value)} placeholder="yield 또는 BIN 값" list="wf-compare-bins" />
            <datalist id="wf-compare-bins"><option value="yield" />{(map?.bins || []).map(row => <option key={row.bin} value={row.bin} />)}</datalist>
          </label>
          <label style={{ ...inputLabel, minWidth: 205 }}>ET ITEM 유형
            <Select value={etItemSource} onChange={event => setEtItemSource(event.target.value)}>
              <option value="raw">원본 ITEM ID</option>
              <option value="et_download">ET Download · REAL/ADDP</option>
              {isAdmin && <option value="test_addp">관리자 · Test ADDP</option>}
            </Select>
          </label>
          <label style={{ ...inputLabel, minWidth: 220 }}>{etItemSource === "raw" ? "ET 원본 ITEM ID" : etItemSource === "test_addp" ? "ET Test ADDP" : "ET Download REAL/ADDP"}
            {etItemSource === "et_download" ? <Select value={itemId} onChange={event => setItemId(event.target.value)}>
              {etDownloadItems.map(row => <option key={row.alias} value={row.alias}>{row.category === "addp" ? "ADDP" : "REAL"} · {row.alias}</option>)}
            </Select> : etItemSource === "test_addp" ? <Input value={testAddpAlias} onChange={event => setTestAddpAlias(event.target.value)} placeholder="Test ADDP alias" /> :
              <Input value={itemId} onChange={event => setItemId(event.target.value)} placeholder="빈 값이면 첫 항목" list="wf-map-items" />}
          </label>
          <label style={{ ...inputLabel, minWidth: 210 }}>Inline ITEM · Matching 규칙
            <Select value={comparisonInlineItem} disabled={!comparisonInlineItems.length} onChange={event => {
              const nextItem = event.target.value;
              const itemRules = rulesForValue(comparisonInlineRules, "item_id", nextItem);
              const nextStep = matchingValues(itemRules, "step_id")[0] || "";
              const stepRules = rulesForValue(itemRules, "step_id", nextStep);
              const preferred = stepRules.find(rule => ruleUsable(rule, vehicle)) || stepRules[0];
              setComparisonInlineItem(nextItem); setComparisonInlineStep(nextStep);
              setComparisonInlineTable(String(preferred?.matching_table || "")); setComparison(null);
            }}>
              {!comparisonInlineItems.length && <option value="">Inline Matching 규칙 없음</option>}
              {comparisonInlineItems.map(value => <option key={value} value={value}>{value}</option>)}
            </Select>
          </label>
          <label style={{ ...inputLabel, minWidth: 190 }}>Inline STEP · Matching 규칙
            <Select value={comparisonInlineStep} disabled={!comparisonInlineSteps.length} onChange={event => {
              const nextStep = event.target.value;
              const stepRules = rulesForValue(comparisonInlineItemRules, "step_id", nextStep);
              const preferred = stepRules.find(rule => ruleUsable(rule, vehicle)) || stepRules[0];
              setComparisonInlineStep(nextStep); setComparisonInlineTable(String(preferred?.matching_table || "")); setComparison(null);
            }}>
              {!comparisonInlineSteps.length && <option value="">STEP 규칙 없음</option>}
              {comparisonInlineSteps.map(value => <option key={value} value={value}>{value}</option>)}
            </Select>
          </label>
          <label style={{ ...inputLabel, minWidth: 220 }}>Inline Mapsetting · ITEM별 선택
            <Select value={comparisonInlineTable} onChange={event => setComparisonInlineTable(event.target.value)}>
              {!comparisonInlineStepRules.length && <option value="">matching_table 없음</option>}
              {comparisonInlineStepRules.map(rule => <option key={rule.matching_table} value={rule.matching_table}
                disabled={!ruleUsable(rule, vehicle)}>{rule.matching_table} · {rule.shot_count || 0} shots{ruleUsable(rule, vehicle) ? "" : " · Mapsetting 사용 불가"}</option>)}
            </Select>
          </label>
          {etItemSource === "test_addp" && <label style={{ ...inputLabel, minWidth: 330, flex: "1 1 330px" }}>ADDP Form
            <Input value={testAddpForm} onChange={event => setTestAddpForm(event.target.value)}
              placeholder="예: {IDSAT_IDX} / max({IOFF_IDX}, 0.001)" />
          </label>}
          <label style={{ ...inputLabel, minWidth: 160 }}>ET STEP ID
            <Input value={stepId} onChange={event => { setStepId(event.target.value); setStepSeq(""); }} placeholder="미선택 시 첫 STEP" list="wf-compare-steps" />
            <datalist id="wf-compare-steps">{((comparison?.steps || map?.steps) || []).map(value => <option key={value} value={value} />)}</datalist>
          </label>
          <label style={{ ...inputLabel, minWidth: 220 }}>ET STEP SEQ · DCOP 측정꾸러미
            <Input value={stepSeq} onChange={event => setStepSeq(event.target.value)} placeholder="미선택 시 첫 측정꾸러미" list="wf-compare-step-seqs" />
            <datalist id="wf-compare-step-seqs">{((comparison?.step_seqs || map?.step_seqs) || []).map(value => <option key={value} value={value} />)}</datalist>
          </label>
          <label style={{ ...inputLabel, minWidth: 210 }}>ET_TABLE_* split coloring
            <Select value={comparisonSplitSource} onChange={event => setComparisonSplitSource(event.target.value)}>
              <option value="">split 미적용</option>
              {(boot?.split_sources || []).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
            </Select>
          </label>
          <Button variant="primary" disabled={comparisonBusy || !rootLotId.trim() || !vehicle
            || !comparisonInlineReady
            || (etItemSource === "et_download" && !itemId.trim())
            || (etItemSource === "test_addp" && (!testAddpAlias.trim() || !testAddpForm.trim()))}
            onClick={loadComparison}>{comparisonBusy ? "비교 중…" : "Shot 비교"}</Button>
        </div>
        <div style={{ marginBottom: 12, padding: "9px 11px", borderRadius: 7, background: "var(--bg-secondary)", fontSize: 11, color: "var(--muted)" }}>
          선택 BIN은 shot 안 die의 <b>해당 BIN 비율(= 0/1 평균 × 100)</b>로 계산합니다. <b>yield</b>는 제품 설정의 Good BIN 기준 Full Shot Yield를 사용합니다.
          ET와 Inline은 각각 <b>wafer + shot별 평균</b>을 만든 뒤 Yield와 같은 <code>root_lot_id + wafer_id + shot_x + shot_y</code>만 JOIN합니다.
          Inline은 <code>inline_matching.csv</code>의 <b>제품 + STEP + ITEM → matching_table</b> 규칙과 TEG 위치조회의 Inline Mapsetting이 모두 있는 경우에만 비교됩니다.
          선택한 ET_TABLE_*의 split을 점 색상으로 표시합니다. 각 차트의 <b>r</b>은 값의 단위와 크기에 영향받지 않는 WF 패턴 유사도(Pearson 상관계수)입니다.
          {etItemSource !== "raw" && <> ET 값은 현재 선택한 <b>{etItemSource === "test_addp" ? "Test ADDP" : "ET Download REAL/ADDP"}</b> 계산 결과를 사용합니다.</>}
          <> 서로 다른 <b>STEP ID + STEP SEQ(DCOP 측정꾸러미)</b>는 합치지 않습니다.</>
        </div>
        {!comparisonInlineReady && <div style={{ margin: "-5px 0 12px", color: "var(--warn)", fontSize: 11 }}>
          {!comparisonInlineRules.length ? `${product}에 Inline Matching table이 없어 비교할 수 없습니다.` :
            "선택한 Inline ITEM·STEP에 연결된 TEG Inline Mapsetting이 없습니다."}
        </div>}
        {!comparison ? <EmptyState icon="↗" title="shot별 Yield/BIN·ET·Inline 값과 WF 패턴 유사도를 비교해 보세요" /> : <ShotComparisonChart data={comparison} />}
      </Card>}

      <PageGear title="WF MAP DB 실제 열 매핑 시트" canEdit={canEdit} position="bottom-left" width={760}>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontSize: 12, lineHeight: 1.55, color: "var(--muted)" }}>
            기준 의미 열과 ET·Inline DB에 적용된 실제 열을 한 시트에서 비교하고 바로 수정합니다. 저장값은 DB의 모든 제품에 공통 적용됩니다.
          </div>
          {columnMappingBusy && <div style={{ fontSize: 12, color: "var(--muted)" }}>ET · Inline DB 전체 스키마를 확인하는 중…</div>}
          {columnMappings.et && columnMappings.inline && <>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Pill tone="ok">DB 공통 · 모든 제품</Pill>
              <Pill tone="neutral">ET 제품 {columnMappings.et.schema_product_count || 0} · 열 {columnMappings.et.columns?.length || 0}</Pill>
              <Pill tone="neutral">Inline 제품 {columnMappings.inline.schema_product_count || 0} · 열 {columnMappings.inline.columns?.length || 0}</Pill>
            </div>

            <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 7, background: "var(--bg-primary)" }}>
              <table aria-label="DB 공통 실제 열 매핑 스프레드시트" style={{ width: "100%", minWidth: 680, tableLayout: "fixed", borderCollapse: "separate", borderSpacing: 0, fontSize: 12 }}>
                <colgroup><col style={{ width: 170 }} /><col /><col /></colgroup>
                <thead><tr>
                  {["기준 의미 열", "ET 실제 열 · 1.RAWDATA_DB_ET", "Inline 실제 열 · 1.RAWDATA_DB_INLINE"].map(label =>
                    <th key={label} style={{ position: "sticky", top: 0, zIndex: 2, padding: "9px 10px", textAlign: "left", background: "var(--bg-tertiary)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }}>{label}</th>)}
                </tr></thead>
                <tbody>{SHOT_FIELD_LABELS.map(([key, label]) => <tr key={key}>
                  <th scope="row" style={{ padding: "8px 10px", textAlign: "left", background: "var(--bg-secondary)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }}>
                    {label}
                  </th>
                  {["et", "inline"].map(kind => {
                    const mapping = columnMappings[kind];
                    return <td key={kind} style={{ padding: 6, verticalAlign: "top", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }}>
                      <Select aria-label={`${label} ${kind.toUpperCase()} 실제 열`} value={mapping.fields?.[key] || ""} disabled={!canEdit}
                        onChange={event => setColumnMappings(current => ({
                          ...current,
                          [kind]: { ...current[kind], fields: { ...(current[kind]?.fields || {}), [key]: event.target.value } },
                        }))}>
                        <option value="">미사용</option>
                        {(mapping.columns || []).map(column => <option key={column} value={column}>{column}</option>)}
                      </Select>
                      <div style={{ minHeight: 15, marginTop: 3, fontSize: 9, color: "var(--muted)" }}>
                        {requiredShotFields[kind].has(key) ? <b style={{ color: "var(--danger)" }}>필수</b> : "선택"}
                        {mapping.auto_fields?.[key] ? ` · 자동 ${mapping.auto_fields[key]}` : ""}
                      </div>
                    </td>;
                  })}
                </tr>)}</tbody>
              </table>
            </div>

            <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 7, background: "var(--bg-primary)" }}>
              <table aria-label="Wide 형식 조회 지표 열 스프레드시트" style={{ width: "100%", minWidth: 680, tableLayout: "fixed", borderCollapse: "separate", borderSpacing: 0, fontSize: 12 }}>
                <colgroup><col style={{ width: 170 }} /><col /><col /></colgroup>
                <thead><tr>
                  <th style={{ padding: "9px 10px", textAlign: "left", background: "var(--bg-tertiary)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }}>조회 설정</th>
                  <th style={{ padding: "9px 10px", textAlign: "left", background: "var(--bg-tertiary)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }}>ET 실제 지표 열</th>
                  <th style={{ padding: "9px 10px", textAlign: "left", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)" }}>Inline 실제 지표 열</th>
                </tr></thead>
                <tbody><tr>
                  <th scope="row" style={{ padding: "9px 10px", textAlign: "left", verticalAlign: "top", background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}>
                    Wide 형식<br /><span style={{ fontSize: 9, fontWeight: 400, color: "var(--muted)" }}>VTH·IDSAT처럼 지표가 각각의 열인 경우</span>
                  </th>
                  {["et", "inline"].map(kind => {
                    const mapping = columnMappings[kind];
                    return <td key={kind} style={{ padding: 8, verticalAlign: "top", borderRight: "1px solid var(--border)" }}>
                      <div style={{ maxHeight: 155, overflow: "auto", display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 5 }}>
                        {(mapping.value_candidates || []).map(column => <label key={column} style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, fontSize: 11 }}>
                          <input type="checkbox" checked={(mapping.value_columns || []).includes(column)} disabled={!canEdit}
                            onChange={event => setColumnMappings(current => {
                              const values = current[kind]?.value_columns || [];
                              return { ...current, [kind]: { ...current[kind], value_columns: event.target.checked
                                ? [...values, column] : values.filter(value => value !== column) } };
                            })} />
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }} title={column}>{column}</span>
                        </label>)}
                        {!mapping.value_candidates?.length && <span style={{ color: "var(--muted)", fontSize: 10 }}>Long 형식 ITEM/VALUE 사용</span>}
                      </div>
                    </td>;
                  })}
                </tr></tbody>
              </table>
            </div>

            <div style={{ padding: 9, borderRadius: 7, background: "var(--bg-primary)", fontSize: 11, lineHeight: 1.5, color: "var(--muted)" }}>
              ET의 <code>chip_x_pos</code>, <code>chip_y_pos</code>도 Shot X/Y 행에서 선택하면 좌표로 사용됩니다. Long 형식은 ITEM ID·측정값 행을, Wide 형식은 아래 지표 열 체크를 사용합니다.
            </div>
            <div style={{ display: "flex", gap: 7, justifyContent: "flex-end" }}>
              <Button disabled={!canEdit || columnMappingBusy} onClick={() => setColumnMappings(current => ({
                et: { ...current.et, fields: { ...(current.et?.auto_fields || {}) }, value_columns: [...(current.et?.auto_value_columns || [])] },
                inline: { ...current.inline, fields: { ...(current.inline?.auto_fields || {}) }, value_columns: [...(current.inline?.auto_value_columns || [])] },
              }))}>전체 자동 매칭</Button>
              <Button variant="primary" disabled={!canEdit || columnMappingBusy} onClick={saveColumnMappings}>ET · Inline 모두 저장</Button>
            </div>
          </>}
        </div>
      </PageGear>
    </div>
  );
}
