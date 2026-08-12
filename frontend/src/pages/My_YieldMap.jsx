import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { putJson, sf } from "../lib/api";
import { toast } from "../components/Toast";
import { Button, Card, EmptyState, Input, PageHeader, Pill, Select } from "../components/UXKit";


const API = "/api/yield-map";
const FALLBACK_COLOR = "#D1D5DB";
const FIELD_LABELS = [
  ["x", "chip X (chip_x_pos)"], ["y", "chip Y (chip_y_pos)"], ["bin", "BIN"], ["msr", "MSR"],
  ["lot", "LOT ID"], ["wafer", "WAFER ID"], ["product", "제품"],
];
const inputLabel = { fontSize: 12, color: "var(--muted)", display: "flex", flexDirection: "column", gap: 4 };
function safeColor(value) {
  const text = String(value || "").toUpperCase();
  return /^#[0-9A-F]{6}$/.test(text) ? text : FALLBACK_COLOR;
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


function YieldDieMap({ rows, colors }) {
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
    return { xAxis, yAxis, cell, width, height, ox, oy };
  }, [rows]);
  if (!model) return <EmptyState icon="◫" title="표시할 die 좌표가 없습니다" />;
  const { xAxis, yAxis, cell, width, height, ox, oy } = model;
  return (
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}
      style={{ width: "min(100%, 700px)", height: "auto", background: "var(--bg-card)", border: "1px solid var(--line)", borderRadius: 8 }}>
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
            <title>{`die (${row.x}, ${row.y})\nBIN: ${row.bin || "(빈 값)"}${row.msr != null ? `\nMSR: ${row.msr}` : ""}${row.lot != null ? `\nLOT: ${row.lot}` : ""}${row.wafer != null ? `\nWAFER: ${row.wafer}` : ""}`}</title>
          </rect>
        );
      })}
      <text x={SIZE / 2} y={SIZE - 9} textAnchor="middle" fontSize="11" fill="var(--muted)">
        chip_x_pos {xAxis.min} → {xAxis.max} (오른쪽으로 증가)
      </text>
      <text x="13" y={SIZE / 2} textAnchor="middle" fontSize="11" fill="var(--muted)"
        transform={`rotate(-90 13 ${SIZE / 2})`}>chip_y_pos {yAxis.min} (위) → {yAxis.max} (아래)</text>
    </svg>
  );
}


export default function My_YieldMap({ user }) {
  const [boot, setBoot] = useState(null);
  const [product, setProduct] = useState("");
  const [config, setConfig] = useState({ source: "", fields: {}, bin_colors: {} });
  const [preview, setPreview] = useState(null);
  const [map, setMap] = useState(null);
  const [lotId, setLotId] = useState("");
  const [waferId, setWaferId] = useState("");
  const [binRows, setBinRows] = useState([{ bin: "", color: "#94A3B8" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const previewRequest = useRef(0);

  const loadBootstrap = useCallback(async () => {
    setError("");
    try {
      const data = await sf(`${API}/bootstrap`);
      setBoot(data);
      setProduct(current => current || data.products?.[0] || "");
    } catch (err) { setError(String(err.message || err)); }
  }, []);
  useEffect(() => { loadBootstrap(); }, [loadBootstrap]);

  useEffect(() => {
    if (!boot || !product) return;
    previewRequest.current += 1;
    const saved = boot.configs?.[product] || { source: "", fields: {}, bin_colors: {} };
    setConfig({ source: "", fields: {}, bin_colors: {}, ...saved });
    setBinRows(binRowsFromConfig(saved));
    setPreview(null);
    setMap(null);
    setLotId("");
    setWaferId("");
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
  const updateBinRow = (index, patch) => setBinRows(current => current.map((row, i) => i === index ? { ...row, ...patch } : row));
  const deleteBinRow = index => setBinRows(current => {
    const next = current.filter((_, i) => i !== index);
    return next.length ? next : [{ bin: "", color: "#94A3B8" }];
  });
  const pasteBinTable = (event, startIndex) => {
    const text = event.clipboardData?.getData("text/plain") || "";
    if (!text.includes("\t") && !text.includes("\n")) return;
    event.preventDefault();
    let pasted = text.replace(/\r/g, "").split("\n").filter(line => line.trim() !== "")
      .map(line => line.split("\t"));
    if (pasted.length && /^bin(?:\s*name)?$/i.test(String(pasted[0][0] || "").trim())) pasted = pasted.slice(1);
    const rows = pasted.map(cells => ({
      bin: String(cells[0] || "").trim(),
      color: String(cells[1] || "#94A3B8").trim().toUpperCase(),
    })).filter(row => row.bin || row.color);
    if (!rows.length) return;
    setBinRows(current => {
      const next = [...current];
      rows.forEach((row, offset) => { next[startIndex + offset] = row; });
      return next.filter(Boolean);
    });
  };
  const binColorMap = useMemo(() => binColorsFromRows(binRows), [binRows]);

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
        ...config, bin_map: binMap, bin_colors: binColorMap,
      });
      setConfig(data.config);
      setBinRows(binRowsFromConfig(data.config));
      setBoot(current => ({ ...current, configs: { ...(current.configs || {}), [product]: data.config } }));
      toast.ok(`${product} Yield Map 설정 저장됨`);
    } catch (err) { setError(String(err.message || err)); }
    finally { setBusy(false); }
  };

  const loadMap = async () => {
    if (!product) return;
    setBusy(true); setError("");
    try {
      const q = new URLSearchParams({ product });
      if (lotId.trim()) q.set("lot_id", lotId.trim());
      if (waferId.trim()) q.set("wafer_id", waferId.trim());
      const data = await sf(`${API}/map?${q.toString()}`);
      setMap(data);
      setBinRows(current => {
        const existing = new Set(current.map(row => String(row.bin || "")));
        const discovered = (data.bins || []).filter(item => !existing.has(String(item.bin)))
          .map(item => ({ bin: String(item.bin), color: "#D1D5DB" }));
        return discovered.length ? [...current.filter(row => row.bin), ...discovered] : current;
      });
    } catch (err) { setMap(null); setError(String(err.message || err)); }
    finally { setBusy(false); }
  };

  const canEdit = !!boot?.can_edit || user?.role === "admin";
  const sourceOptions = (boot?.sources || []).filter(source => !source.products?.length
    || source.products.some(name => String(name).toLowerCase() === String(product).toLowerCase()));

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <PageHeader title="Yield Map"
        subtitle="제품별 TEG die 좌표에 BIN/MSR TABLE 값을 매칭하고 제품별 BIN 컬러로 표시합니다"
        right={<div style={{ display: "flex", gap: 8 }}>
          <Select value={product} onChange={event => setProduct(event.target.value)} style={{ minWidth: 170 }}>
            {(boot?.products || []).map(name => <option key={name} value={name}>{name}</option>)}
          </Select>
          <Button onClick={loadBootstrap}>새로고침</Button>
        </div>} />

      {error && <div style={{ padding: "9px 12px", border: "1px solid var(--danger)", borderRadius: 6, color: "var(--danger)", fontSize: 13 }}>{error}</div>}
      {boot && !boot.products?.length && <EmptyState icon="◫" title="TEG 제품이 없습니다" hint="TEG 위치 조회의 Chip_Radius 제품이 먼저 필요합니다." />}

      {product && <Card title={`제품별 BIN Map 설정 — ${product}`}
        right={<Button variant="primary" disabled={!canEdit || busy || !config.source} onClick={save}>설정 저장</Button>}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 12 }}>
          <label style={{ ...inputLabel, minWidth: 260 }}>BIN/MSR TABLE
            <Select value={config.source || ""} disabled={!canEdit} onChange={event => {
              const source = event.target.value;
              setConfig(current => ({ ...current, source, fields: {} }));
              setPreview(null); setMap(null);
            }}>
              <option value="">TABLE 선택</option>
              {sourceOptions.map(source => <option key={source.id} value={source.id}>{source.name} · {source.id}</option>)}
            </Select>
          </label>
          {config.source && <Button disabled={busy} onClick={() => loadPreview()}>열 자동 매칭</Button>}
          <Pill tone={sourceOptions.length ? "ok" : "warn"}>BIN/MSR 후보 {sourceOptions.length}개</Pill>
        </div>
        {!sourceOptions.length && <div style={{ color: "var(--warn)", fontSize: 12, marginBottom: 10 }}>
          DB root에서 이름에 BIN 또는 MSR가 포함된 TABLE을 찾지 못했습니다.
        </div>}
        {config.source && <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
          {FIELD_LABELS.map(([key, label]) => <label key={key} style={inputLabel}>{label}
            <Select value={config.fields?.[key] || ""} disabled={!canEdit} onChange={event => setField(key, event.target.value)}>
              <option value="">미사용</option>
              {(preview?.columns || []).map(column => <option key={column} value={column}>{column}</option>)}
            </Select>
          </label>)}
        </div>}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 7 }}>
          <b style={{ fontSize: 12 }}>BIN MAP · 제품별 저장</b>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>Excel의 BIN·BIN COLOR 두 열을 복사해 첫 셀에 붙여넣을 수 있습니다.</span>
        </div>
        <div style={{ maxWidth: 620, border: "1px solid var(--line)", borderRadius: 6, overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead><tr style={{ background: "var(--bg-secondary)" }}>
              <th style={{ padding: "7px 8px", borderBottom: "1px solid var(--line)", textAlign: "left", width: 60 }}>No.</th>
              <th style={{ padding: "7px 8px", borderBottom: "1px solid var(--line)", textAlign: "left" }}>BIN</th>
              <th style={{ padding: "7px 8px", borderBottom: "1px solid var(--line)", textAlign: "left" }}>BIN COLOR (#RRGGBB)</th>
              <th style={{ padding: "7px 8px", borderBottom: "1px solid var(--line)", width: 52 }}></th>
            </tr></thead>
            <tbody>{binRows.map((row, index) => <tr key={index}>
              <td style={{ padding: "5px 8px", borderBottom: "1px solid var(--line)", color: "var(--muted)" }}>{index + 1}</td>
              <td style={{ padding: 4, borderBottom: "1px solid var(--line)" }}>
                <Input value={row.bin} disabled={!canEdit} onPaste={event => pasteBinTable(event, index)}
                  onChange={event => updateBinRow(index, { bin: event.target.value })} style={{ width: "100%" }} />
              </td>
              <td style={{ padding: 4, borderBottom: "1px solid var(--line)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                  <input type="color" value={safeColor(row.color)} disabled={!canEdit}
                    onChange={event => updateBinRow(index, { color: event.target.value.toUpperCase() })} />
                  <Input value={row.color} disabled={!canEdit} onPaste={event => pasteBinTable(event, index)}
                    onChange={event => updateBinRow(index, { color: event.target.value.toUpperCase() })}
                    style={{ width: 120, borderColor: row.bin && !/^#[0-9A-F]{6}$/i.test(row.color) ? "var(--danger)" : undefined }} />
                </div>
              </td>
              <td style={{ padding: 4, borderBottom: "1px solid var(--line)", textAlign: "center" }}>
                {canEdit && <Button onClick={() => deleteBinRow(index)}>삭제</Button>}
              </td>
            </tr>)}</tbody>
          </table>
        </div>
        {canEdit && <Button onClick={() => setBinRows(current => [...current, { bin: "", color: "#94A3B8" }])}
          style={{ marginTop: 7 }}>+ 행 추가</Button>}
      </Card>}

      {product && <Card title="Yield Die Map" right={map && <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <Pill tone="ok">Net Die {map.net_die || 0}개</Pill>
        <Pill tone="neutral">rows {map.rows?.length || 0} · {map.source}</Pill>
      </div>}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
          <label style={inputLabel}>LOT ID <Input value={lotId} onChange={event => setLotId(event.target.value)} placeholder="선택 필터" /></label>
          <label style={inputLabel}>WAFER ID <Input value={waferId} onChange={event => setWaferId(event.target.value)} placeholder="선택 필터" /></label>
          <Button variant="primary" disabled={busy || !config.source} onClick={loadMap}>{busy ? "조회 중…" : "Map 조회"}</Button>
        </div>
        {!map ? <EmptyState icon="◫" title="BIN/MSR die map을 조회해 주세요"
          hint="제품별 TABLE과 die X/Y/BIN 열을 저장한 뒤 LOT/WAFER 조건으로 조회합니다." /> :
          <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
            <YieldDieMap rows={map.rows || []} colors={binColorMap} />
            <div style={{ minWidth: 180, display: "flex", flexDirection: "column", gap: 6 }}>
              <b style={{ fontSize: 12 }}>BIN 범례</b>
              {(map.bins || []).map(item => <div key={item.bin} style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
                <span style={{ width: 14, height: 14, border: "1px solid var(--line)", background: safeColor(binColorMap?.[item.bin]) }} />
                <b>{item.bin || "(빈 값)"}</b><span style={{ color: "var(--muted)" }}>{item.count} die</span>
              </div>)}
              {map.overflow && <span style={{ color: "var(--warn)", fontSize: 11 }}>최대 100,000개까지만 표시했습니다.</span>}
            </div>
          </div>}
      </Card>}
    </div>
  );
}
