import { useEffect, useMemo, useState } from "react";
import { sf } from "../lib/api";
import { waferPanelGrid } from "../lib/chartLayout";
import { WaferMap } from "../pages/My_TegMap";

const palettes = {
  blue_gray_red: {
    label: "P10 Blue · Median Gray · P90 Red",
    css: "linear-gradient(90deg,#2563eb,#94a3b8,#dc2626)",
    stops: [[37, 99, 235], [148, 163, 184], [220, 38, 38]],
  },
  red_gray_blue: {
    label: "P10 Red · Median Gray · P90 Blue",
    css: "linear-gradient(90deg,#dc2626,#94a3b8,#2563eb)",
    stops: [[220, 38, 38], [148, 163, 184], [37, 99, 235]],
  },
  viridis: {
    label: "Viridis",
    css: "linear-gradient(90deg,#440154,#31688e,#35b779,#fde725)",
    stops: [[68, 1, 84], [49, 104, 142], [53, 183, 121], [253, 231, 37]],
  },
  gray: {
    label: "Gray",
    css: "linear-gradient(90deg,#e2e8f0,#0f172a)",
    stops: [[226, 232, 240], [15, 23, 42]],
  },
};

const norm = (value) => String(value || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
const productKey = (value) => norm(value).replace(/^(VEHICLE|VH)/, "");
const mix = (a, b, ratio) => a.map((value, index) => Math.round(value + (b[index] - value) * ratio));

function percentile(values, quantile) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const position = (sorted.length - 1) * quantile;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return sorted[lower];
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}

function compactNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Number(number.toPrecision(6)) : 0;
}

export default function TegValueWaferMap({ vehicle, points = [], panels = null, title = "WF MAP", valueLabel = "value", panelLimit = 25 }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [palette, setPalette] = useState("blue_gray_red");
  const panelRows = useMemo(() => {
    if (Array.isArray(panels) && panels.length) return panels.slice(0, panelLimit);
    return [{ key: "single", label: title, points }];
  }, [panels, points, title, panelLimit]);
  const allValues = useMemo(() => panelRows.flatMap((panel) => (panel.points || []).map((point) => Number(point.value ?? point.y))).filter(Number.isFinite), [panelRows]);
  const rawMin = allValues.length ? Math.min(...allValues) : 0;
  const rawMax = allValues.length ? Math.max(...allValues) : 1;
  const defaultLow = percentile(allValues, 0.1);
  const defaultCenter = percentile(allValues, 0.5);
  const defaultHigh = percentile(allValues, 0.9);
  const [low, setLow] = useState(defaultLow);
  const [center, setCenter] = useState(defaultCenter);
  const [high, setHigh] = useState(defaultHigh);
  useEffect(() => {
    setLow(defaultLow);
    setCenter(defaultCenter);
    setHigh(defaultHigh);
  }, [defaultLow, defaultCenter, defaultHigh]);
  useEffect(() => {
    if (!vehicle) {
      setData(null);
      setError("제품 정보가 없어 TEG 위치조회 WF MAP을 선택할 수 없습니다.");
      return undefined;
    }
    let alive = true;
    setLoading(true);
    setError("");
    const standardMap = () => sf(`/api/filebrowser/chart-builder/radius-layout?product=${encodeURIComponent(vehicle)}`).then((layout) => {
      const geometry = layout.geometry || {};
      const kx = Math.abs(Number(geometry.kx) || 1);
      const ky = Math.abs(Number(geometry.ky) || 1);
      const cx = Number(geometry.cx) || 0;
      const cy = Number(geometry.cy) || 0;
      return {
        vehicle: layout.mask || vehicle,
        source: "Chip_Radius.csv",
        geometry: { ...geometry, fit: "radius", wafer_radius_mm: 150, wafer_edge_mm: 147, shot_w_mm: kx, shot_h_mm: ky, pitch_x: 1, pitch_y: 1 },
        shots: (layout.rows || []).map((shot) => ({ x: Number(shot.shot_x), y: Number(shot.shot_y), r: Number(shot.radius), radius: Number(shot.radius), mm_x: (Number(shot.shot_x) - cx) * kx, mm_y: (Number(shot.shot_y) - cy) * ky })),
        tegs: [],
      };
    });
    sf("/api/teg-map/vehicles").then((list) => {
      const vehicles = list.vehicles || [];
      const matched = vehicles.find((value) => norm(value) === norm(vehicle)) || vehicles.find((value) => productKey(value) === productKey(vehicle));
      if (!matched) return standardMap();
      return sf(`/api/teg-map/map?vehicle=${encodeURIComponent(matched)}`).catch(() => standardMap());
    }).then((map) => {
      if (alive) setData(map);
    }).catch((reason) => {
      if (alive) {
        setData(null);
        setError(reason.message || String(reason));
      }
    }).finally(() => {
      if (alive) setLoading(false);
    });
    return () => { alive = false; };
  }, [vehicle]);
  const color = (value) => {
    const lo = Number(low);
    const mid = Number(center);
    const hi = Number(high);
    const numeric = Number(value);
    let fraction;
    if (numeric <= mid) fraction = 0.5 * (numeric - lo) / Math.max(mid - lo, 1e-12);
    else fraction = 0.5 + 0.5 * (numeric - mid) / Math.max(hi - mid, 1e-12);
    fraction = Math.max(0, Math.min(1, fraction));
    const stops = palettes[palette].stops;
    const position = fraction * (stops.length - 1);
    const index = Math.min(stops.length - 2, Math.floor(position));
    const rgb = mix(stops[index], stops[index + 1], position - index);
    return `rgb(${rgb.join(",")})`;
  };
  if (loading) return <div style={{ padding: 18, color: "#475569" }}>TEG 위치조회 WF MAP을 불러오는 중입니다.</div>;
  if (error || !data) return <div style={{ padding: 14, border: "1px solid #fecaca", borderRadius: 8, background: "#fff7f7", color: "#b91c1c" }}><b>제품 WF MAP을 표시할 수 없습니다.</b><div style={{ marginTop: 5, fontSize: 13 }}>{error || "TEG map payload가 없습니다."}</div></div>;
  const mapKeys = new Set((data.shots || []).map((shot) => `${Number(shot.x)},${Number(shot.y)}`));
  const range = Math.max(1e-12, rawMax - rawMin);
  const step = range / 200;
  const panelCount = Array.isArray(panels) ? panels.length : 1;
  // wafer map 은 정사각이라 칸 폭이 곧 지도 크기다. auto-fit 격자에 맡기면 넓은
  // 화면에서 여덟 칸으로 쪼개져 한 장이 점 무더기가 된다 — 패널 수에 맞춰 열
  // 수를 정하고 격자 전체 폭을 묶어, 여러 장이 같은 크기로 비교되게 한다.
  const grid = waferPanelGrid(panelRows.length);
  return <div style={{ border: "1px solid #d1d5db", borderRadius: 8, background: "#fff", color: "#111827", padding: "10px 12px" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}><strong>{title || "WF MAP"} · {data.vehicle}</strong><span style={{ fontSize: 12, color: "#475569", fontFamily: "monospace" }}>{panelCount > 1 ? `${panelRows.length}/${panelCount} panels · common scale` : "single map"}</span></div>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 10, alignItems: "end", margin: "10px 0" }}>
      <label style={{ fontSize: 12, color: "#475569" }}>Low · P10<input type="range" min={rawMin} max={rawMax} step={step} value={low} onChange={(event) => setLow(Math.min(Number(event.target.value), Number(center)))} style={{ width: "100%" }}/><input aria-label="WF MAP Low" type="number" value={low} step={step} onChange={(event) => setLow(Math.min(Number(event.target.value), Number(center)))} style={{ width: "100%", boxSizing: "border-box" }}/></label>
      <label style={{ fontSize: 12, color: "#475569" }}>Center · median<input type="range" min={rawMin} max={rawMax} step={step} value={center} onChange={(event) => setCenter(Math.max(Number(low), Math.min(Number(event.target.value), Number(high))))} style={{ width: "100%" }}/><input aria-label="WF MAP Center" type="number" value={center} step={step} onChange={(event) => setCenter(Math.max(Number(low), Math.min(Number(event.target.value), Number(high))))} style={{ width: "100%", boxSizing: "border-box" }}/></label>
      <label style={{ fontSize: 12, color: "#475569" }}>High · P90<input type="range" min={rawMin} max={rawMax} step={step} value={high} onChange={(event) => setHigh(Math.max(Number(event.target.value), Number(center)))} style={{ width: "100%" }}/><input aria-label="WF MAP High" type="number" value={high} step={step} onChange={(event) => setHigh(Math.max(Number(event.target.value), Number(center)))} style={{ width: "100%", boxSizing: "border-box" }}/></label>
      <label style={{ fontSize: 12, color: "#475569" }}>Palette<select aria-label="WF MAP Palette" value={palette} onChange={(event) => setPalette(event.target.value)} style={{ width: "100%", height: 28 }}>{Object.entries(palettes).map(([key, item]) => <option key={key} value={key}>{item.label}</option>)}</select></label>
    </div>
    <div style={{ height: 14, borderRadius: 999, background: palettes[palette].css, border: "1px solid #cbd5e1" }}/>
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", fontSize: 11, color: "#475569", marginTop: 2 }}><span>{compactNumber(low)} · P10</span><span style={{ textAlign: "center" }}>{compactNumber(center)} · median · {valueLabel}</span><span style={{ textAlign: "right" }}>{compactNumber(high)} · P90</span></div>
    {panelCount > panelLimit && <div style={{ fontSize: 12, color: "#b45309", marginTop: 8 }}>패널이 많아 정렬된 앞 {panelLimit}개만 표시합니다.</div>}
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${grid.columns},minmax(0,1fr))`, gap: 10, marginTop: 10, maxWidth: grid.maxWidth, marginLeft: "auto", marginRight: "auto" }}>
      {panelRows.map((panel) => {
        const panelPoints = panel.points || [];
        const shotValues = new Map(panelPoints.map((point) => [`${Number(point.x)},${Number(point.y)}`, { value: Number(point.value ?? point.y), n: point.n, label: point.label }]));
        const matched = panelPoints.filter((point) => mapKeys.has(`${Number(point.x)},${Number(point.y)}`)).length;
        return <div key={panel.key || panel.label} style={{ border: panelRows.length > 1 ? "1px solid #cbd5e1" : "none", borderRadius: 8, overflow: "hidden" }}>
          {panelRows.length > 1 && <div style={{ padding: "8px 10px", fontSize: 13, fontWeight: 900, textAlign: "center", background: "#e2e8f0", borderBottom: "2px solid #64748b" }}>{panel.label} · mapped {matched}/{panelPoints.length}</div>}
          {panelRows.length === 1 && <div style={{ textAlign: "right", fontSize: 12, color: "#475569", fontFamily: "monospace" }}>mapped {matched}/{panelPoints.length} shots</div>}
          <div style={{ display: "flex", justifyContent: "center", paddingTop: 6, margin: "0 auto" }}><WaferMap data={data} selectedTegs={new Set()} tegColor={() => "#000"} selectedShot={null} onShotClick={() => {}} nearestShot={null} shotValues={shotValues} valueColor={color} valueLabel={valueLabel} light hideUnmeasured/></div>
        </div>;
      })}
    </div>
  </div>;
}
