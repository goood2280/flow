/* My_TegMap.jsx — TEG 위치 조회 (WF MAP).
   - chip layout(Mask, chip_x_adj, chip_y_adj, Chip_Radius) 파일로 wafer geometry fit:
     Chip_Radius = shot 센터 ↔ wafer 원점 거리(mm) → shot 크기(mm)·wafer 중심을 최소자승으로 산출.
   - Teg_location(vehicle,teg,ebeam_x,ebeam_y = shot 센터 기준 TEG 좌하단) 을 겹쳐
     여러 TEG 를 wafer 전체 / shot 확대 뷰로 동시 표시. wafer 원(150mm)과 최외곽선(147mm) 함께 표시.
   - shot 클릭 → 해당 shot 에서 각 TEG 좌하단의 실좌표(mm)·원점 radius 표시.
   - vehicle 별 shot 표시 방식(⚙️ 설정): 기본 | 그림(teg_location/ 업로드 이미지) |
     칩 격자(cols×rows, 칩 크기·칩 사이 간격 mm, shot 센터 기준 좌우/상하 대칭 배치).
   - 설정 json·그림 파일은 파일탐색기 위치(DB root)의 teg_location/ 폴더에 저장.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sf, putJson } from "../lib/api";
import { toast } from "../components/Toast";
import PageGear from "../components/PageGear";
import { Button, Card, EmptyState, PageHeader, Pill, Select } from "../components/UXKit";

const API = "/api/teg-map";

const TEG_COLORS = [
  "#e05252", "#3e7bd6", "#2f9e63", "#c78a1e", "#8a5fd0",
  "#d0568f", "#1fa0a8", "#8a8f2a", "#c06030", "#5a6ed0",
];

const inputStyle = {
  background: "var(--panel)", color: "var(--text)", border: "1px solid var(--line)",
  borderRadius: 4, padding: "4px 8px", fontSize: 13, minWidth: 110,
};

function fmt(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return Number(v).toFixed(d);
}

/* 좌표 단위 — 내부 계산은 mm 기준, 표시만 변환. 기본값은 um(마이크로미터). */
const UNIT_FACTOR = { mm: 1, um: 1000 };
const UNIT_LABEL = { mm: "mm", um: "µm" };
const UNIT_DECIMALS = { mm: 3, um: 1 };

function _token() {
  try { return JSON.parse(localStorage.getItem("hol_user") || "{}").token || ""; }
  catch (_) { return ""; }
}

async function fetchImageBlobUrl(vehicle) {
  const res = await fetch(`${API}/image?vehicle=${encodeURIComponent(vehicle)}`,
    { headers: { "X-Session-Token": _token() } });
  if (!res.ok) return null;
  return URL.createObjectURL(await res.blob());
}

async function uploadImage(vehicle, file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/image?vehicle=${encodeURIComponent(vehicle)}`,
    { method: "POST", body: fd, headers: { "X-Session-Token": _token() } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || body.error || "업로드 실패");
  return body;
}

async function deleteImage(vehicle) {
  const res = await fetch(`${API}/image?vehicle=${encodeURIComponent(vehicle)}`,
    { method: "DELETE", headers: { "X-Session-Token": _token() } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || "삭제 실패");
  return body;
}

/* shot 센터 기준 칩 셀 배치 (mm) — 좌우/상하 대칭.
   chip_w/h = 0 이면 shot 을 cols/rows 로 균등 분할(간격 제외). 반환 좌표는 칩 좌하단. */
function chipCells(display, W, H) {
  const cols = Math.max(1, display.cols || 1);
  const rows = Math.max(1, display.rows || 1);
  const gx = Math.max(0, display.gap_x || 0);
  const gy = Math.max(0, display.gap_y || 0);
  let cw = display.chip_w || 0;
  let ch = display.chip_h || 0;
  if (cw <= 0) cw = Math.max((W - (cols - 1) * gx) / cols, 0.001);
  if (ch <= 0) ch = Math.max((H - (rows - 1) * gy) / rows, 0.001);
  const bw = cols * cw + (cols - 1) * gx;
  const bh = rows * ch + (rows - 1) * gy;
  const x0 = -bw / 2, y0 = -bh / 2;
  const cells = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      cells.push({ x: x0 + c * (cw + gx), y: y0 + r * (ch + gy), w: cw, h: ch, i: r * cols + c });
    }
  }
  return { cells, cw, ch, bw, bh, cols, rows };
}

/* ── ⚙️ 설정 드로어 내용 — 파일/기본값 + vehicle 표시 설정 ── */
function GearSettings({ vehicle, canEdit, onSaved }) {
  const [info, setInfo] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [vcfg, setVcfg] = useState(null);   // 현재 vehicle 표시 설정
  const [saving, setSaving] = useState(false);
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await sf(API + "/config");
      setInfo(r);
      setCfg(r.config);
      setVcfg({
        mode: "none", cols: 1, rows: 1, chip_w: 0, chip_h: 0, gap_x: 0, gap_y: 0, image: "",
        ...((r.config.vehicles || {})[vehicle] || {}),
      });
    } catch (e) { toast.error(String(e.message || e)); }
  }, [vehicle]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const patch = {
        layout_file: cfg.layout_file,
        teg_file: cfg.teg_file,
        ebeam_scale: Number(cfg.ebeam_scale) || 1.0,
        wafer_radius_mm: Number(cfg.wafer_radius_mm) || 150.0,
        wafer_edge_mm: Number(cfg.wafer_edge_mm) || 147.0,
        teg_default_w: Number(cfg.teg_default_w) || 2.0,
        teg_default_h: Number(cfg.teg_default_h) || 2.0,
      };
      if (vehicle && vcfg) {
        patch.vehicles = {
          [vehicle]: {
            ...vcfg,
            cols: Math.max(1, parseInt(vcfg.cols, 10) || 1),
            rows: Math.max(1, parseInt(vcfg.rows, 10) || 1),
            chip_w: Number(vcfg.chip_w) || 0,
            chip_h: Number(vcfg.chip_h) || 0,
            gap_x: Number(vcfg.gap_x) || 0,
            gap_y: Number(vcfg.gap_y) || 0,
          },
        };
      }
      const r = await putJson(API + "/config", patch);
      setInfo(r);
      setCfg(r.config);
      toast.ok("설정 저장됨");
      onSaved && onSaved();
    } catch (e) { toast.error(String(e.message || e)); }
    finally { setSaving(false); }
  };

  const onUpload = async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    try {
      await uploadImage(vehicle, f);
      toast.ok("그림 업로드됨");
      await load();
      onSaved && onSaved();
    } catch (err) { toast.error(String(err.message || err)); }
    finally { if (fileRef.current) fileRef.current.value = ""; }
  };

  const onDeleteImage = async () => {
    try {
      await deleteImage(vehicle);
      toast.ok("그림 삭제됨");
      await load();
      onSaved && onSaved();
    } catch (err) { toast.error(String(err.message || err)); }
  };

  if (!cfg || !vcfg) return <div style={{ color: "var(--muted)" }}>불러오는 중…</div>;

  const set = (patch) => setCfg(prev => ({ ...prev, ...patch }));
  const setV = (patch) => setVcfg(prev => ({ ...prev, ...patch }));
  const row = { display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: 8 };
  const lab = { fontSize: 12, color: "var(--muted)", minWidth: 120 };
  const num = { ...inputStyle, minWidth: 64, width: 72 };
  const sect = { fontSize: 12, fontWeight: 700, color: "var(--muted)", margin: "14px 0 8px", borderTop: "1px solid var(--line)", paddingTop: 10 };
  const dis = !canEdit;

  return (
    <div style={{ fontSize: 13 }}>
      <div style={{ ...sect, borderTop: "none", paddingTop: 0, marginTop: 0 }}>데이터 파일 (파일탐색기 Files 위치)</div>
      <div style={row}>
        <span style={lab}>chip layout 파일</span>
        <input style={{ ...inputStyle, flex: 1 }} list="teg-file-candidates" disabled={dis}
          value={cfg.layout_file || ""} onChange={e => set({ layout_file: e.target.value })} />
        <Pill tone={info?.layout_ok ? "ok" : "danger"}>{info?.layout_ok ? "있음" : "없음"}</Pill>
      </div>
      <div style={row}>
        <span style={lab}>Teg_location 파일</span>
        <input style={{ ...inputStyle, flex: 1 }} list="teg-file-candidates" disabled={dis}
          value={cfg.teg_file || ""} onChange={e => set({ teg_file: e.target.value })} />
        <Pill tone={info?.teg_ok ? "ok" : "danger"}>{info?.teg_ok ? "있음" : "없음"}</Pill>
      </div>
      <datalist id="teg-file-candidates">
        {(info?.files || []).map(f => <option key={f} value={f} />)}
      </datalist>
      <div style={row}>
        <span style={lab}>ebeam 배율 (→mm)</span>
        <input style={num} type="number" step="any" disabled={dis} value={cfg.ebeam_scale}
          onChange={e => set({ ebeam_scale: e.target.value })} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>um 파일이면 0.001</span>
      </div>
      <div style={row}>
        <span style={lab}>wafer 반경 / 최외곽 (mm)</span>
        <input style={num} type="number" step="any" disabled={dis} value={cfg.wafer_radius_mm}
          onChange={e => set({ wafer_radius_mm: e.target.value })} />
        <span style={{ color: "var(--muted)" }}>/</span>
        <input style={num} type="number" step="any" disabled={dis} value={cfg.wafer_edge_mm}
          onChange={e => set({ wafer_edge_mm: e.target.value })} />
      </div>

      <div style={sect}>TEG 기본 사이즈 (mm) — teg_w/teg_h 열이 없을 때</div>
      <div style={row}>
        <span style={lab}>가로 × 세로</span>
        <input style={num} type="number" step="any" min="0.01" disabled={dis} value={cfg.teg_default_w}
          onChange={e => set({ teg_default_w: e.target.value })} />
        <span style={{ color: "var(--muted)" }}>×</span>
        <input style={num} type="number" step="any" min="0.01" disabled={dis} value={cfg.teg_default_h}
          onChange={e => set({ teg_default_h: e.target.value })} />
      </div>

      <div style={sect}>shot 표시 방식 — {vehicle || "(vehicle 선택)"}</div>
      <div style={row}>
        {[["none", "기본"], ["image", "그림"], ["grid", "칩 격자"]].map(([m, label]) => (
          <label key={m} style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: dis ? "default" : "pointer" }}>
            <input type="radio" name="teg-veh-mode" disabled={dis}
              checked={vcfg.mode === m} onChange={() => setV({ mode: m })} />
            {label}
          </label>
        ))}
      </div>
      {vcfg.mode === "grid" && (
        <>
          <div style={row}>
            <span style={lab}>칩 개수 (가로×세로)</span>
            <input style={num} type="number" min="1" disabled={dis} value={vcfg.cols}
              onChange={e => setV({ cols: e.target.value })} />
            <span style={{ color: "var(--muted)" }}>×</span>
            <input style={num} type="number" min="1" disabled={dis} value={vcfg.rows}
              onChange={e => setV({ rows: e.target.value })} />
          </div>
          <div style={row}>
            <span style={lab}>칩 크기 (mm)</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.chip_w}
              onChange={e => setV({ chip_w: e.target.value })} />
            <span style={{ color: "var(--muted)" }}>×</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.chip_h}
              onChange={e => setV({ chip_h: e.target.value })} />
            <span style={{ fontSize: 11, color: "var(--muted)" }}>0 = 균등 분할</span>
          </div>
          <div style={row}>
            <span style={lab}>칩 사이 간격 (mm)</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.gap_x}
              onChange={e => setV({ gap_x: e.target.value })} />
            <span style={{ color: "var(--muted)" }}>×</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.gap_y}
              onChange={e => setV({ gap_y: e.target.value })} />
            <span style={{ fontSize: 11, color: "var(--muted)" }}>좌우 × 위아래</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
            칩 블록은 shot 센터 기준 좌우/상하 대칭으로 배치됩니다.
          </div>
        </>
      )}
      {vcfg.mode === "image" && (
        <div style={{ marginBottom: 8 }}>
          <div style={row}>
            <span style={lab}>그림 파일</span>
            {vcfg.image
              ? <Pill tone="ok">{vcfg.image}</Pill>
              : <span style={{ fontSize: 12, color: "var(--muted)" }}>업로드된 그림 없음</span>}
          </div>
          <div style={row}>
            <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.gif,.webp" disabled={dis}
              onChange={onUpload} style={{ fontSize: 12 }} />
            {vcfg.image && <Button disabled={dis} onClick={onDeleteImage}>삭제</Button>}
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            그림은 {info?.teg_dir || "teg_location/"} 에 저장되고 각 shot 안에 표시됩니다.
          </div>
        </div>
      )}

      <div style={{ marginTop: 14, display: "flex", gap: 8, alignItems: "center" }}>
        <Button variant="primary" disabled={saving || dis} onClick={save}>저장</Button>
        {!canEdit && <span style={{ fontSize: 11, color: "var(--muted)" }}>admin / teg 페이지 관리자만 저장 가능</span>}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 10 }}>
        설정 저장 위치: {info?.teg_dir || ""}
      </div>
    </div>
  );
}

/* ── wafer 전체 SVG — shot 사각형 + wafer 원/최외곽선 + TEG 마커 + 그림/칩 격자 ── */
function WaferMap({ data, selectedTegs, tegColor, selectedShot, onShotClick, imgUrl }) {
  const SIZE = 640;
  const geo = data.geometry;
  const mmMode = geo.fit === "radius";
  const display = data.display || { mode: "none" };

  const { toX, toY, shotW, shotH, waferR, edgeR, mmScale } = useMemo(() => {
    if (mmMode) {
      const R = geo.wafer_radius_mm;
      const pad = Math.max(geo.shot_w_mm, geo.shot_h_mm);
      const s = SIZE / (2 * (R + pad));
      return {
        toX: (mm) => SIZE / 2 + mm * s,
        // chip_y 작은 값이 위에 오도록 y축 reverse (WF MAP 관례) — mm 이 클수록 아래.
        toY: (mm) => SIZE / 2 + mm * s,
        shotW: geo.shot_w_mm * s,
        shotH: geo.shot_h_mm * s,
        waferR: R * s,
        edgeR: (geo.wafer_edge_mm || 0) * s,
        mmScale: s,
      };
    }
    const xs = data.shots.map(s0 => s0.x), ys = data.shots.map(s0 => s0.y);
    const x0 = Math.min(...xs) - geo.pitch_x, x1 = Math.max(...xs) + geo.pitch_x;
    const y0 = Math.min(...ys) - geo.pitch_y, y1 = Math.max(...ys) + geo.pitch_y;
    const s = SIZE / Math.max(x1 - x0, y1 - y0);
    return {
      toX: (x) => (x - x0) * s,
      // chip_y 작은 값이 위에 (y축 reverse).
      toY: (y) => (y - y0) * s,
      shotW: geo.pitch_x * s,
      shotH: geo.pitch_y * s,
      waferR: 0, edgeR: 0, mmScale: 1,
    };
  }, [data, mmMode]);

  const cellsInfo = useMemo(() => (
    mmMode && display.mode === "grid"
      ? chipCells(display, geo.shot_w_mm, geo.shot_h_mm)
      : null
  ), [display, mmMode, geo]);

  const tegList = data.tegs.filter(t => selectedTegs.has(t.teg));
  const showImage = mmMode && display.mode === "image" && imgUrl;

  return (
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}
      style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6 }}>
      {/* wafer 경계 원 + 최외곽선(점선) */}
      {mmMode && (
        <>
          <circle cx={SIZE / 2} cy={SIZE / 2} r={waferR} fill="none" stroke="#9aa4b0" strokeWidth="1.2" />
          {edgeR > 0 && (
            <circle cx={SIZE / 2} cy={SIZE / 2} r={edgeR} fill="none" stroke="#c78a1e"
              strokeWidth="1" strokeDasharray="6 4" />
          )}
          <line x1={SIZE / 2 - 6} y1={SIZE / 2} x2={SIZE / 2 + 6} y2={SIZE / 2} stroke="#9aa4b0" strokeWidth="0.8" />
          <line x1={SIZE / 2} y1={SIZE / 2 - 6} x2={SIZE / 2} y2={SIZE / 2 + 6} stroke="#9aa4b0" strokeWidth="0.8" />
        </>
      )}
      {data.shots.map(s0 => {
        const cx = mmMode ? toX(s0.mm_x) : toX(s0.x);
        const cy = mmMode ? toY(s0.mm_y) : toY(s0.y);
        const key = `${s0.x},${s0.y}`;
        const isSel = selectedShot && selectedShot.x === s0.x && selectedShot.y === s0.y;
        const title = mmMode
          ? `shot (${s0.x}, ${s0.y})\n센터: (${fmt(s0.mm_x)}, ${fmt(s0.mm_y)}) mm\nradius: ${fmt(s0.radius)} mm`
          : `shot (${s0.x}, ${s0.y})`;
        return (
          <g key={key} onClick={() => onShotClick(s0)} style={{ cursor: "pointer" }}>
            {showImage && (
              <image href={imgUrl} x={cx - shotW / 2} y={cy - shotH / 2} width={shotW} height={shotH}
                preserveAspectRatio="none" opacity="0.85" pointerEvents="none" />
            )}
            <rect x={cx - shotW / 2} y={cy - shotH / 2} width={shotW} height={shotH}
              fill={isSel ? "rgba(90,140,255,0.28)" : showImage ? "none" : "rgba(128,128,128,0.06)"}
              stroke={isSel ? "#5a8cff" : "var(--line)"} strokeWidth={isSel ? 1.6 : 0.7}>
              <title>{title}</title>
            </rect>
            {/* 칩 격자 — shot 센터 기준 대칭 블록 */}
            {cellsInfo && cellsInfo.cells.map(c => (
              <rect key={c.i}
                x={toX(s0.mm_x + c.x)} y={toY(s0.mm_y + c.y)}
                width={c.w * mmScale} height={c.h * mmScale}
                fill="rgba(47,158,99,0.10)" stroke="#2f9e63" strokeWidth="0.4"
                opacity="0.8" pointerEvents="none" />
            ))}
            {/* TEG 마커 */}
            {mmMode && tegList.map(t => {
              const ax = s0.mm_x + t.ebeam_x, ay = s0.mm_y + t.ebeam_y;
              return (
                <rect key={t.teg} x={toX(ax)} y={toY(ay)}
                  width={Math.max(1.5, t.teg_w * mmScale)} height={Math.max(1.5, t.teg_h * mmScale)}
                  fill={tegColor(t.teg)} opacity="0.9" pointerEvents="none" />
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

/* ── shot 확대 SVG — 한 shot 안 TEG 위치·그림·칩 격자 ── */
function ShotZoom({ data, selectedTegs, tegColor, imgUrl }) {
  const SIZE = 380;
  const geo = data.geometry;
  if (geo.fit !== "radius") {
    return <EmptyState icon="⚠" title="Chip_Radius fit 불가" hint="shot 크기(mm)를 알 수 없어 확대 뷰를 그릴 수 없습니다" />;
  }
  const display = data.display || { mode: "none" };
  const W = geo.shot_w_mm, H = geo.shot_h_mm;
  const pad = 0.12;
  const s = SIZE / Math.max(W * (1 + pad * 2), H * (1 + pad * 2));
  const w = W * s, h = H * s;
  const ox = (SIZE - w) / 2, oy = (SIZE - h) / 2;
  const toX = (mm) => ox + (mm + W / 2) * s;         // shot 센터 기준 mm
  // chip_y 작은 값이 위에 (y축 reverse) — WaferMap 과 동일 방향.
  const toY = (mm) => oy + (mm + H / 2) * s;
  const tegList = data.tegs.filter(t => selectedTegs.has(t.teg));
  const cellsInfo = display.mode === "grid" ? chipCells(display, W, H) : null;
  const showImage = display.mode === "image" && imgUrl;

  return (
    <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}
      style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6 }}>
      {showImage && (
        <image href={imgUrl} x={ox} y={oy} width={w} height={h}
          preserveAspectRatio="none" opacity="0.9" />
      )}
      <rect x={ox} y={oy} width={w} height={h} fill={showImage ? "none" : "rgba(128,128,128,0.05)"}
        stroke="var(--muted)" strokeWidth="1" />
      {/* shot 센터 십자 */}
      <line x1={toX(0) - 5} y1={toY(0)} x2={toX(0) + 5} y2={toY(0)} stroke="var(--muted)" strokeWidth="0.8" />
      <line x1={toX(0)} y1={toY(0) - 5} x2={toX(0)} y2={toY(0) + 5} stroke="var(--muted)" strokeWidth="0.8" />
      {/* 칩 격자 — 칩 사각형 + 번호 */}
      {cellsInfo && cellsInfo.cells.map(c => (
        <g key={c.i}>
          <rect x={toX(c.x)} y={toY(c.y)} width={c.w * s} height={c.h * s}
            fill="rgba(47,158,99,0.08)" stroke="#2f9e63" strokeWidth="0.8" opacity="0.85" />
          <text x={toX(c.x + c.w / 2)} y={toY(c.y + c.h / 2)} fontSize="10" fill="#2f9e63"
            textAnchor="middle" dominantBaseline="middle" opacity="0.85">{c.i + 1}</text>
        </g>
      ))}
      {/* TEG 직사각형 (좌하단 = ebeam_x/y) */}
      {tegList.map(t => {
        const x = toX(t.ebeam_x), y = toY(t.ebeam_y);   // 반전 후 rect 상단 = 작은 mm(ebeam 좌하단)
        return (
          <g key={t.teg}>
            <rect x={x} y={y} width={t.teg_w * s} height={t.teg_h * s} fill={tegColor(t.teg)} opacity="0.75" />
            <circle cx={toX(t.ebeam_x)} cy={toY(t.ebeam_y)} r="2.4" fill={tegColor(t.teg)} stroke="var(--panel)" strokeWidth="0.8" />
            <text x={x + t.teg_w * s + 4} y={y + t.teg_h * s / 2} fontSize="11" fill="var(--text)" dominantBaseline="middle">
              {t.teg}
            </text>
          </g>
        );
      })}
      {/* 치수 라벨 */}
      <text x={SIZE / 2} y={oy + h + 16} fontSize="11" fill="var(--muted)" textAnchor="middle">
        {fmt(W)} mm
      </text>
      <text x={ox - 8} y={SIZE / 2} fontSize="11" fill="var(--muted)" textAnchor="middle"
        transform={`rotate(-90 ${ox - 8} ${SIZE / 2})`}>
        {fmt(H)} mm
      </text>
      {cellsInfo && (
        <text x={ox + 4} y={oy - 6} fontSize="11" fill="#2f9e63">
          칩 {cellsInfo.cols}×{cellsInfo.rows} = {cellsInfo.cols * cellsInfo.rows}개
          {display.chip_w > 0 ? ` · 칩 ${fmt(cellsInfo.cw)}×${fmt(cellsInfo.ch)} mm` : ""}
          {(display.gap_x > 0 || display.gap_y > 0) ? ` · 간격 ${fmt(display.gap_x)}×${fmt(display.gap_y)} mm` : ""}
        </text>
      )}
    </svg>
  );
}

/* ── 선택 shot 의 TEG 좌하단 실좌표·radius 표 ── */
function RadiusPanel({ data, shot, selectedTegs, unit = "um" }) {
  const geo = data.geometry;
  const f = UNIT_FACTOR[unit] || 1;
  const ul = UNIT_LABEL[unit] || "mm";
  const dec = UNIT_DECIMALS[unit] ?? 3;
  if (!shot) return <div style={{ fontSize: 12, color: "var(--muted)" }}>wafer map 에서 shot 을 클릭하면 TEG 좌하단의 실좌표({ul})와 원점 radius 를 계산합니다.</div>;
  if (geo.fit !== "radius") return <div style={{ fontSize: 12, color: "var(--muted)" }}>Chip_Radius fit 불가 — radius 계산 불가.</div>;
  const rows = data.tegs.filter(t => selectedTegs.has(t.teg)).map(t => {
    const ax = shot.mm_x + t.ebeam_x, ay = shot.mm_y + t.ebeam_y;
    return { teg: t.teg, ax, ay, radius: Math.hypot(ax, ay) };
  });
  if (!rows.length) return <div style={{ fontSize: 12, color: "var(--muted)" }}>표시할 TEG 를 선택하세요.</div>;
  const cell = { padding: "4px 10px", borderBottom: "1px solid var(--line)", fontSize: 13, textAlign: "right" };
  return (
    <div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
        shot ({shot.x}, {shot.y}) — 센터 ({fmt(shot.mm_x * f, dec)}, {fmt(shot.mm_y * f, dec)}) {ul} · 센터 radius {fmt(shot.radius * f, dec)} {ul}
      </div>
      <table style={{ borderCollapse: "collapse" }}>
        <thead><tr>
          <th style={{ ...cell, textAlign: "left", color: "var(--muted)" }}>TEG</th>
          <th style={{ ...cell, color: "var(--muted)" }}>좌하단 X ({ul})</th>
          <th style={{ ...cell, color: "var(--muted)" }}>좌하단 Y ({ul})</th>
          <th style={{ ...cell, color: "var(--muted)" }}>원점 radius ({ul})</th>
        </tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.teg}>
              <td style={{ ...cell, textAlign: "left", fontWeight: 600 }}>{r.teg}</td>
              <td style={cell}>{fmt(r.ax * f, dec)}</td>
              <td style={cell}>{fmt(r.ay * f, dec)}</td>
              <td style={{ ...cell, fontWeight: 700 }}>{fmt(r.radius * f, dec)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── 특정 TEG 의 shot 별 radius 전체 표 (백엔드 계산) ── */
function TegRadiusTable({ vehicle, tegNames, unit = "um" }) {
  const [teg, setTeg] = useState("");
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState(false);
  const f = UNIT_FACTOR[unit] || 1;
  const ul = UNIT_LABEL[unit] || "mm";
  const dec = UNIT_DECIMALS[unit] ?? 3;

  useEffect(() => { setRows(null); setTeg(""); }, [vehicle]);

  const load = async (name) => {
    setTeg(name);
    setRows(null);
    if (!name) return;
    try {
      const r = await sf(`${API}/radius?vehicle=${encodeURIComponent(vehicle)}&teg=${encodeURIComponent(name)}`);
      setRows(r.rows || []);
    } catch (e) { toast.error(String(e.message || e)); }
  };

  const cell = { padding: "3px 10px", borderBottom: "1px solid var(--line)", fontSize: 12, textAlign: "right" };
  return (
    <Card title="TEG shot별 radius 표"
      right={<Button onClick={() => setOpen(o => !o)}>{open ? "접기" : "펼치기"}</Button>}>
      {!open ? (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>TEG 하나를 선택하면 모든 shot 에서의 좌하단 실좌표·radius 를 표로 보여줍니다.</div>
      ) : (
        <div>
          <div style={{ marginBottom: 8 }}>
            <Select value={teg} onChange={e => load(e.target.value)} style={{ minWidth: 160 }}>
              <option value="">TEG 선택…</option>
              {tegNames.map(n => <option key={n} value={n}>{n}</option>)}
            </Select>
          </div>
          {teg && !rows && <div style={{ color: "var(--muted)", fontSize: 12 }}>계산 중…</div>}
          {rows && (
            <>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
                min {fmt(rows[0]?.radius * f, dec)} {ul} · max {fmt(rows[rows.length - 1]?.radius * f, dec)} {ul} · {rows.length} shots (radius 오름차순)
              </div>
              <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--line)", borderRadius: 4 }}>
                <table style={{ borderCollapse: "collapse", width: "100%" }}>
                  <thead><tr>
                    <th style={{ ...cell, color: "var(--muted)" }}>shot x</th>
                    <th style={{ ...cell, color: "var(--muted)" }}>shot y</th>
                    <th style={{ ...cell, color: "var(--muted)" }}>좌하단 X ({ul})</th>
                    <th style={{ ...cell, color: "var(--muted)" }}>좌하단 Y ({ul})</th>
                    <th style={{ ...cell, color: "var(--muted)" }}>radius ({ul})</th>
                  </tr></thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i}>
                        <td style={cell}>{r.shot_x}</td>
                        <td style={cell}>{r.shot_y}</td>
                        <td style={cell}>{fmt(r.abs_x * f, dec)}</td>
                        <td style={cell}>{fmt(r.abs_y * f, dec)}</td>
                        <td style={{ ...cell, fontWeight: 600 }}>{fmt(r.radius * f, dec)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

export default function My_TegMap({ user }) {
  const [vehicles, setVehicles] = useState(null);   // null=로딩, []=없음
  const [vehicle, setVehicle] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [selectedTegs, setSelectedTegs] = useState(new Set());
  const [selectedShot, setSelectedShot] = useState(null);
  const [imgUrl, setImgUrl] = useState(null);
  const [coordUnit, setCoordUnit] = useState("um");   // 좌표 단위 기본값: µm

  const canEdit = user?.role === "admin" || (user?.page_manager || []).includes("teg");

  const loadVehicles = useCallback(async () => {
    try {
      const r = await sf(API + "/vehicles");
      setVehicles(r.vehicles || []);
      if ((r.vehicles || []).length && !vehicle) setVehicle(r.vehicles[0]);
    } catch (e) {
      setVehicles([]);
      setErr(String(e.message || e));
    }
  }, [vehicle]);
  useEffect(() => { loadVehicles(); }, []);

  const loadMap = useCallback(async () => {
    if (!vehicle) return;
    setErr("");
    try {
      const r = await sf(`${API}/map?vehicle=${encodeURIComponent(vehicle)}`);
      setData(r);
      setSelectedShot(null);
      // 기본: 첫 번째 TEG 하나만 선택 (전체 동시 표시는 마커가 겹쳐 혼잡).
      const firstTeg = (r.tegs || [])[0];
      setSelectedTegs(new Set(firstTeg ? [firstTeg.teg] : []));
    } catch (e) {
      setData(null);
      setErr(String(e.message || e));
    }
  }, [vehicle]);
  useEffect(() => { loadMap(); }, [loadMap]);

  // vehicle 그림 (mode=image) — blob URL 로 로드
  useEffect(() => {
    let revoked = false;
    let url = null;
    setImgUrl(null);
    if (data?.display?.mode === "image" && data?.display?.has_image) {
      fetchImageBlobUrl(data.vehicle).then(u => {
        if (revoked) { if (u) URL.revokeObjectURL(u); return; }
        url = u;
        setImgUrl(u);
      });
    }
    return () => {
      revoked = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [data]);

  const tegNames = useMemo(() => (data?.tegs || []).map(t => t.teg), [data]);
  const tegColor = useCallback((name) => {
    const i = tegNames.indexOf(name);
    return TEG_COLORS[(i >= 0 ? i : 0) % TEG_COLORS.length];
  }, [tegNames]);

  const toggleTeg = (name) => setSelectedTegs(prev => {
    const next = new Set(prev);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });

  const onShotClick = (s0) => {
    setSelectedShot(prev => (prev && prev.x === s0.x && prev.y === s0.y) ? null : s0);
  };

  const geo = data?.geometry;
  const display = data?.display || { mode: "none" };

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <PageHeader
        title="TEG 위치 조회"
        subtitle="chip layout(Chip_Radius)으로 wafer geometry 를 계산하고 Teg_location 의 TEG 를 WF MAP 위에 표시합니다"
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Select value={vehicle} onChange={e => setVehicle(e.target.value)} style={{ minWidth: 150 }}>
              {(vehicles || []).map(v => <option key={v} value={v}>{v}</option>)}
            </Select>
            <Select value={coordUnit} onChange={e => setCoordUnit(e.target.value)} style={{ minWidth: 80 }} title="좌표 단위">
              <option value="um">µm</option>
              <option value="mm">mm</option>
            </Select>
            <Button onClick={loadMap}>새로고침</Button>
          </div>
        }
      />

      {vehicles && vehicles.length === 0 && (
        <EmptyState icon="📐" title="chip layout 파일이 없습니다"
          hint="파일탐색기 Files 위치(DB root)에 Mask/chip_x_adj/chip_y_adj/Chip_Radius 열이 있는 파일을 넣고, ⚙️ 설정에서 파일명을 지정하세요" />
      )}
      {err && vehicles && vehicles.length > 0 && (
        <EmptyState icon="⚠" title="WF MAP 을 불러오지 못했습니다" hint={err} />
      )}

      {data && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
          {/* 좌: wafer 전체 */}
          <Card title={`WF MAP — ${data.vehicle}`}
            right={
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {geo?.fit === "radius" ? (
                  <Pill tone="ok" title={`wafer 중심 격자좌표 (${fmt(geo.cx, 3)}, ${fmt(geo.cy, 3)})`}>
                    shot {fmt(geo.shot_w_mm, 2)}×{fmt(geo.shot_h_mm, 2)} mm
                  </Pill>
                ) : (
                  <Pill tone="warn">Chip_Radius fit 불가 — 격자 좌표로만 표시</Pill>
                )}
                <Pill tone="neutral">
                  {display.mode === "grid" ? `칩 격자 ${display.cols}×${display.rows}` :
                    display.mode === "image" ? "그림" : "기본"}
                </Pill>
              </div>
            }>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
              shot 클릭 → 확대 뷰·TEG radius 계산. 실선 원 = wafer {fmt(geo?.wafer_radius_mm, 0)}mm,
              점선 원 = 최외곽 {fmt(geo?.wafer_edge_mm, 0)}mm. 표시 방식은 우하단 ⚙️ 에서 변경합니다.
            </div>
            <WaferMap data={data} selectedTegs={selectedTegs} tegColor={tegColor}
              selectedShot={selectedShot} onShotClick={onShotClick} imgUrl={imgUrl} />
            {/* TEG 선택 legend */}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
              {tegNames.length === 0 && (
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  Teg_location 파일에 이 vehicle 의 TEG 가 없습니다.
                </span>
              )}
              {tegNames.map(n => {
                const on = selectedTegs.has(n);
                return (
                  <button key={n} onClick={() => toggleTeg(n)}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer",
                      border: `1px solid ${on ? tegColor(n) : "var(--line)"}`, borderRadius: 12,
                      background: on ? "var(--panel)" : "transparent", color: "var(--text)",
                      padding: "3px 10px", fontSize: 12, opacity: on ? 1 : 0.5,
                    }}>
                    <span style={{ width: 10, height: 10, background: tegColor(n), borderRadius: 2, display: "inline-block" }} />
                    {n}
                  </button>
                );
              })}
            </div>
          </Card>

          {/* 우: shot 확대 + radius */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: "1 1 400px", minWidth: 400 }}>
            <Card title={selectedShot ? `shot 확대 — (${selectedShot.x}, ${selectedShot.y})` : "shot 확대 — shot 내 TEG 배치"}>
              <ShotZoom data={data} selectedTegs={selectedTegs} tegColor={tegColor} imgUrl={imgUrl} />
            </Card>
            <Card title="TEG 좌하단 실좌표 · 원점 radius">
              <RadiusPanel data={data} shot={selectedShot} selectedTegs={selectedTegs} unit={coordUnit} />
            </Card>
            <TegRadiusTable vehicle={vehicle} tegNames={tegNames} unit={coordUnit} />
          </div>
        </div>
      )}

      <PageGear title="TEG 위치 조회 설정" canEdit={canEdit} position="bottom-right">
        <GearSettings vehicle={vehicle} canEdit={canEdit}
          onSaved={() => { loadVehicles(); loadMap(); }} />
      </PageGear>
    </div>
  );
}
