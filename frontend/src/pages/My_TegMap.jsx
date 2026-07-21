/* My_TegMap.jsx — TEG 위치 조회 (WF MAP).
   - chip layout(Mask, chip_x_adj, chip_y_adj, Chip_Radius) 파일로 wafer geometry fit:
     Chip_Radius = shot 센터 ↔ wafer 원점 거리(mm) → shot 크기(mm)·wafer 중심을 최소자승으로 산출.
   - Teg_location(vehicle,teg,ebeam_x,ebeam_y = shot 센터 기준 TEG 좌하단) 을 겹쳐
     여러 TEG 를 wafer 전체 / shot 확대 뷰로 동시 표시. wafer 원(150mm)과 최외곽선(147mm) 함께 표시.
   - TEG 다중 선택: 체크박스로 여러 TEG 를 동시에 선택/비교 가능. 전체/해제 버튼 제공.
   - 동명 TEG 자동 넘버링: 백엔드에서 같은 이름이 2 개 이상이면 _1, _2, … 접미사를 자동 부여.
   - shot 색: 선택 TEG 전체가 최외곽(147mm) 안이면 초록, 라인에 걸치면 빨강.
   - shot 클릭 → shot 확대 뷰. 그림/칩 격자는 확대 뷰에서만 표시 (wafer 전체 뷰는 shot 판정색+TEG 마커만).
   - vehicle 별 shot 표시 방식(⚙️ 설정): 기본 | 그림(teg_location/ 업로드 이미지) |
     칩 격자(cols×rows, 칩 크기 mm·칩 사이 간격 µm, shot 센터 기준 좌우/상하 대칭 배치).
   - 설정 json·그림 파일은 파일탐색기 위치(DB root)의 teg_location/ 폴더에 저장.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sf, putJson } from "../lib/api";
import { toast } from "../components/Toast";
import PageGear from "../components/PageGear";
import { Button, Card, EmptyState, PageHeader, Pill, Select, TabStrip } from "../components/UXKit";
import TegCheck from "./TegCheck";

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

// MAIN 계열 TEG 판별 — 앞이 글자가 아닌 곳의 MAIN (domain/remain 오탐 제외, 백엔드 is_main 과 동일).
const MAIN_RE = /(?<![A-Za-z])MAIN/i;
function isMainTeg(name) { return MAIN_RE.test(String(name || "")); }

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
  const [chk, setChk] = useState(null);     // TEG Mapfile 체크 오프셋 설정
  const [saving, setSaving] = useState(false);
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await sf(API + "/config");
      setInfo(r);
      // TEG 기본 사이즈는 서버에 mm 로 저장 — UI 는 µm 로 편집.
      setCfg({
        ...r.config,
        teg_default_w_um: Math.round((Number(r.config.teg_default_w) || 0) * 1000),
        teg_default_h_um: Math.round((Number(r.config.teg_default_h) || 0) * 1000),
      });
      const v0 = { mode: "none", cols: 1, rows: 1, chip_w: 0, chip_h: 0, gap_x: 0, gap_y: 0, image: "",
        ...((r.config.vehicles || {})[vehicle] || {}) };
      // 칩 크기·칩 사이 간격 모두 서버 mm ↔ UI µm 변환.
      setVcfg({
        ...v0,
        chip_w_um: Math.round((Number(v0.chip_w) || 0) * 1000),
        chip_h_um: Math.round((Number(v0.chip_h) || 0) * 1000),
        gap_x_um: Math.round((Number(v0.gap_x) || 0) * 1000),
        gap_y_um: Math.round((Number(v0.gap_y) || 0) * 1000),
      });
      // TEG Mapfile 체크 오프셋 — 편집 편의를 위해 평탄화
      const c0 = r.config.check || {};
      const fo = c0.flat_offsets || {};
      setChk({
        v_r_offset: c0.v_r_offset ?? 0,
        h_dx: (fo.h || [0, 0])[0], h_dy: (fo.h || [0, 0])[1],
        v_dx: (fo.v_R || [0, 0])[0], v_dy: (fo.v_R || [0, 0])[1],
        modules: (c0.modules || []).map(m => ({ ...m })),
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
        ebeam_scale: Number(cfg.ebeam_scale) || 0.001,
        wafer_radius_mm: Number(cfg.wafer_radius_mm) || 150.0,
        wafer_edge_mm: Number(cfg.wafer_edge_mm) || 147.0,
        // µm 입력 → mm 저장 (기본 3000×100 µm)
        teg_default_w: (Number(cfg.teg_default_w_um) || 3000) / 1000,
        teg_default_h: (Number(cfg.teg_default_h_um) || 100) / 1000,
      };
      if (chk) {
        patch.check = {
          v_r_offset: Number(chk.v_r_offset) || 0,
          flat_offsets: {
            h: [Number(chk.h_dx) || 0, Number(chk.h_dy) || 0],
            v_R: [Number(chk.v_dx) || 0, Number(chk.v_dy) || 0],
          },
          modules: (chk.modules || [])
            .filter(m => String(m.name || "").trim())
            .map(m => ({
              flat: m.flat === "v_R" ? "v_R" : "h",
              name: String(m.name).trim(),
              dx: Number(m.dx) || 0,
              dy: Number(m.dy) || 0,
              note: String(m.note || "").trim(),
            })),
        };
      }
      if (vehicle && vcfg) {
        const { gap_x_um, gap_y_um, chip_w_um, chip_h_um, ...vrest } = vcfg;
        patch.vehicles = {
          [vehicle]: {
            ...vrest,
            cols: Math.max(1, parseInt(vcfg.cols, 10) || 1),
            rows: Math.max(1, parseInt(vcfg.rows, 10) || 1),
            // 칩 크기·간격 모두 µm 입력 → mm 저장
            chip_w: (Number(chip_w_um) || 0) / 1000,
            chip_h: (Number(chip_h_um) || 0) / 1000,
            gap_x: (Number(gap_x_um) || 0) / 1000,
            gap_y: (Number(gap_y_um) || 0) / 1000,
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

  if (!cfg || !vcfg || !chk) return <div style={{ color: "var(--muted)" }}>불러오는 중…</div>;

  const set = (patch) => setCfg(prev => ({ ...prev, ...patch }));
  const setV = (patch) => setVcfg(prev => ({ ...prev, ...patch }));
  const setC = (patch) => setChk(prev => ({ ...prev, ...patch }));
  const setMod = (i, patch) => setChk(prev => ({
    ...prev, modules: prev.modules.map((m, j) => (j === i ? { ...m, ...patch } : m)),
  }));
  const addMod = () => setChk(prev => ({
    ...prev, modules: [...prev.modules, { flat: "h", name: "", dx: 0, dy: 0, note: "" }],
  }));
  const delMod = (i) => setChk(prev => ({
    ...prev, modules: prev.modules.filter((_, j) => j !== i),
  }));
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
        <span style={{ fontSize: 11, color: "var(--muted)" }}>기본 0.001 = ebeam µm 단위 · Chip_Radius 는 mm 단위 (mm ebeam 파일이면 1)</span>
      </div>
      <div style={row}>
        <span style={lab}>wafer 반경 / 최외곽 (mm)</span>
        <input style={num} type="number" step="any" disabled={dis} value={cfg.wafer_radius_mm}
          onChange={e => set({ wafer_radius_mm: e.target.value })} />
        <span style={{ color: "var(--muted)" }}>/</span>
        <input style={num} type="number" step="any" disabled={dis} value={cfg.wafer_edge_mm}
          onChange={e => set({ wafer_edge_mm: e.target.value })} />
      </div>

      <div style={sect}>TEG 기본 사이즈 (µm) — teg_w/teg_h 열이 없을 때</div>
      <div style={row}>
        <span style={lab}>가로 × 세로 (µm)</span>
        <input style={num} type="number" step="any" min="1" disabled={dis} value={cfg.teg_default_w_um}
          onChange={e => set({ teg_default_w_um: e.target.value })} />
        <span style={{ color: "var(--muted)" }}>×</span>
        <input style={num} type="number" step="any" min="1" disabled={dis} value={cfg.teg_default_h_um}
          onChange={e => set({ teg_default_h_um: e.target.value })} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>기본 3000×100 µm</span>
      </div>

      <div style={sect}>TEG Mapfile 체크 — 오프셋</div>
      <div style={row}>
        <span style={lab}>기본 오프셋 Horizontal</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>x'</span>
        <input style={num} type="number" step="any" disabled={dis} value={chk.h_dx}
          onChange={e => setC({ h_dx: e.target.value })} />
        <span style={{ fontSize: 12, color: "var(--muted)" }}>y'</span>
        <input style={num} type="number" step="any" disabled={dis} value={chk.h_dy}
          onChange={e => setC({ h_dy: e.target.value })} />
      </div>
      <div style={row}>
        <span style={lab}>기본 오프셋 Vertical(R)</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>x'</span>
        <input style={num} type="number" step="any" disabled={dis} value={chk.v_dx}
          onChange={e => setC({ v_dx: e.target.value })} />
        <span style={{ fontSize: 12, color: "var(--muted)" }}>y'</span>
        <input style={num} type="number" step="any" disabled={dis} value={chk.v_dy}
          onChange={e => setC({ v_dy: e.target.value })} />
      </div>
      <div style={row}>
        <span style={lab}>Vertical(R) 회전 offset</span>
        <input style={num} type="number" step="any" disabled={dis} value={chk.v_r_offset}
          onChange={e => setC({ v_r_offset: e.target.value })} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>회전 원복: (x, y) → (y, -x + offset)</span>
      </div>
      <div style={{ ...row, marginBottom: 4 }}>
        <span style={lab}>TEG(module)별 오프셋</span>
        <Button disabled={dis} onClick={addMod}>+ 추가</Button>
      </div>
      {chk.modules.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
          등록된 TEG별 오프셋이 없습니다. 특정 TEG(module)에만 추가 보정이 필요하면 추가하세요.
        </div>
      )}
      {chk.modules.map((m, i) => (
        <div key={i} style={{ ...row, marginBottom: 6 }}>
          <select style={{ ...inputStyle, minWidth: 96 }} disabled={dis} value={m.flat}
            onChange={e => setMod(i, { flat: e.target.value })}>
            <option value="h">Horizontal</option>
            <option value="v_R">Vertical(R)</option>
          </select>
          <input style={{ ...inputStyle, minWidth: 110, width: 130 }} disabled={dis}
            placeholder="TEG(module) 이름" value={m.name}
            onChange={e => setMod(i, { name: e.target.value })} />
          <span style={{ fontSize: 12, color: "var(--muted)" }}>x'</span>
          <input style={num} type="number" step="any" disabled={dis} value={m.dx}
            title="x 오프셋" onChange={e => setMod(i, { dx: e.target.value })} />
          <span style={{ fontSize: 12, color: "var(--muted)" }}>y'</span>
          <input style={num} type="number" step="any" disabled={dis} value={m.dy}
            title="y 오프셋" onChange={e => setMod(i, { dy: e.target.value })} />
          <input style={{ ...inputStyle, flex: 1, minWidth: 90 }} disabled={dis}
            placeholder="비고" value={m.note || ""}
            onChange={e => setMod(i, { note: e.target.value })} />
          <Button disabled={dis} onClick={() => delMod(i)}>삭제</Button>
        </div>
      ))}
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
        적용 순서: flat 변환(Vertical(R) 회전 원복) → 기본 오프셋 → TEG별 오프셋.
        이름이 비어 있는 행은 저장 시 제외됩니다.
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
            <span style={lab}>칩 크기 (µm)</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.chip_w_um}
              onChange={e => setV({ chip_w_um: e.target.value })} />
            <span style={{ color: "var(--muted)" }}>×</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.chip_h_um}
              onChange={e => setV({ chip_h_um: e.target.value })} />
            <span style={{ fontSize: 11, color: "var(--muted)" }}>0 = 균등 분할</span>
          </div>
          <div style={row}>
            <span style={lab}>칩 사이 간격 (µm)</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.gap_x_um}
              onChange={e => setV({ gap_x_um: e.target.value })} />
            <span style={{ color: "var(--muted)" }}>×</span>
            <input style={num} type="number" step="any" min="0" disabled={dis} value={vcfg.gap_y_um}
              onChange={e => setV({ gap_y_um: e.target.value })} />
            <span style={{ fontSize: 11, color: "var(--muted)" }}>좌우 × 위아래 (µm)</span>
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

/* ── wafer 전체 SVG — shot 사각형 + wafer 원/최외곽선 + TEG 마커.
   그림/칩 격자는 shot 확대 뷰에서만 표시. shot 색: 선택 TEG 전체가 최외곽(147mm)
   안에 온전히 들어오면 초록, 하나라도 라인에 걸치거나 밖이면 빨강. ── */
function WaferMap({ data, selectedTegs, tegColor, selectedShot, onShotClick, nearestShot }) {
  const SIZE = 640;
  const geo = data.geometry;
  const mmMode = geo.fit === "radius";

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

  const tegList = data.tegs.filter(t => selectedTegs.has(t.teg));

  // shot 별 최외곽(147mm) 판정 — 선택 TEG 사각형의 네 꼭짓점 중 하나라도
  // edge 원 밖이면 "걸림"(빨강), 전부 안이면 초록. TEG 미선택 시엔 shot 영역
  // 자체로 판정: 전부 안=연파랑, 걸치거나 밖=연노랑.
  const edgeMm = mmMode ? (geo.wafer_edge_mm || 0) : 0;
  const shotEdgeCrossed = (s0) => {
    if (!edgeMm || !tegList.length) return null;
    for (const t of tegList) {
      const x0 = s0.mm_x + t.ebeam_x, y0 = s0.mm_y + t.ebeam_y;
      const x1 = x0 + t.teg_w, y1 = y0 + t.teg_h;
      const maxD = Math.hypot(Math.max(Math.abs(x0), Math.abs(x1)), Math.max(Math.abs(y0), Math.abs(y1)));
      if (maxD > edgeMm) return true;
    }
    return false;
  };
  // shot 사각형(센터 ± W/2, ± H/2)의 가장 먼 꼭짓점이 edge 밖인가
  const shotSelfCrossed = (s0) => {
    if (!edgeMm) return null;
    const maxD = Math.hypot(Math.abs(s0.mm_x) + geo.shot_w_mm / 2, Math.abs(s0.mm_y) + geo.shot_h_mm / 2);
    return maxD > edgeMm;
  };

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
        const crossed = mmMode ? shotEdgeCrossed(s0) : null;
        const selfCrossed = mmMode && crossed === null ? shotSelfCrossed(s0) : null;
        const passFill = crossed !== null
          ? (crossed ? "rgba(224,82,82,0.30)" : "rgba(47,158,99,0.22)")
          : selfCrossed !== null
            ? (selfCrossed ? "rgba(250,204,21,0.28)" : "rgba(96,165,250,0.20)")
            : "rgba(128,128,128,0.06)";
        const passStroke = crossed !== null
          ? (crossed ? "#e05252" : "#2f9e63")
          : selfCrossed !== null
            ? (selfCrossed ? "#c78a1e" : "#3e7bd6")
            : "var(--line)";
        const title = mmMode
          ? `shot (${s0.x}, ${s0.y})\n센터: (${fmt(s0.mm_x)}, ${fmt(s0.mm_y)}) mm\nradius: ${fmt(s0.radius)} mm`
            + (crossed !== null
              ? (crossed ? `\n⚠ TEG 가 최외곽 ${fmt(edgeMm, 0)}mm 라인에 걸림` : `\n✓ TEG 전체가 최외곽 ${fmt(edgeMm, 0)}mm 안`)
              : selfCrossed !== null
                ? (selfCrossed ? `\nshot 영역이 최외곽 ${fmt(edgeMm, 0)}mm 에 걸치거나 밖` : `\nshot 전체가 최외곽 ${fmt(edgeMm, 0)}mm 안`)
                : "")
          : `shot (${s0.x}, ${s0.y})`;
        return (
          <g key={key} onClick={() => onShotClick(s0)} style={{ cursor: "pointer" }}>
            <rect x={cx - shotW / 2} y={cy - shotH / 2} width={shotW} height={shotH}
              fill={isSel ? "rgba(90,140,255,0.28)" : passFill}
              stroke={isSel ? "#5a8cff" : passStroke} strokeWidth={isSel ? 1.6 : 0.7}>
              <title>{title}</title>
            </rect>
            {/* TEG 마커 — 격자/그림은 shot 확대 뷰에서만. 앵커 = 좌하단(점 기준 위로) */}
            {mmMode && tegList.map(t => {
              const ax = s0.mm_x + t.ebeam_x, ay = s0.mm_y + t.ebeam_y;
              const hpx = Math.max(1.5, t.teg_h * mmScale);
              return (
                <rect key={t.teg} x={toX(ax)} y={toY(ay) - hpx}
                  width={Math.max(1.5, t.teg_w * mmScale)} height={hpx}
                  fill={tegColor(t.teg)} opacity="0.9" pointerEvents="none" />
              );
            })}
          </g>
        );
      })}
      {/* 가장 가까운 샷 센터 = 빨간 점 — 실center에서 가장 가까운 shot 표시 */}
      {mmMode && nearestShot && (() => {
        const nx = toX(nearestShot.mm_x), ny = toY(nearestShot.mm_y);
        return (
          <g pointerEvents="none">
            <circle cx={nx} cy={ny} r="5" fill="#e05252" stroke="#fff" strokeWidth="1.2" opacity="0.92" />
            <title>가장 가까운 샷: ({nearestShot.x}, {nearestShot.y}) — radius {fmt(nearestShot.radius)} mm</title>
          </g>
        );
      })()}
    </svg>
  );
}

/* ── shot 확대 SVG — 한 shot 안 TEG 위치·그림·칩 격자 ──
   v9.3.x: 마우스 휠(확대/축소) + 드래그(패닝) + 리셋 버튼 지원.
   핀치 줌은 pointer 이벤트 기반으로 터치 디바이스에서도 동작.       ── */
function ShotZoom({ data, selectedTegs, tegColor, imgUrl }) {
  const SIZE = 380;
  const geo = data.geometry;
  const display = data.display || { mode: "none" };
  const W = geo.fit === "radius" ? geo.shot_w_mm : 1;
  const H = geo.fit === "radius" ? geo.shot_h_mm : 1;
  const pad = 0.12;
  const s = SIZE / Math.max(W * (1 + pad * 2), H * (1 + pad * 2));
  const w = W * s, h = H * s;
  const ox = (SIZE - w) / 2, oy = (SIZE - h) / 2;
  const toX = (mm) => ox + (mm + W / 2) * s;
  // SVG 는 y 가 아래로 증가 — ebeam +y(위)를 뒤집어 shot 센터가 정확히 (0,0)이 되게 한다.
  const toY = (mm) => oy + (H / 2 - mm) * s;
  const tegList = data.tegs.filter(t => selectedTegs.has(t.teg));
  const cellsInfo = display.mode === "grid" ? chipCells(display, W, H) : null;
  const showImage = display.mode === "image" && imgUrl;

  // ── zoom / pan 상태 ──
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const svgRef = useRef(null);
  const dragRef = useRef(null);           // { startX, startY, panX0, panY0 } | null
  const pinchRef = useRef(null);          // { dist0, zoom0 } | null
  const pointersRef = useRef(new Map());  // pointerId → { x, y }

  const ZOOM_MIN = 1, ZOOM_MAX = 12, ZOOM_STEP = 1.15;

  const resetView = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }); }, []);

  // 마우스 휠 → 줌 (커서 중심)
  const onWheel = useCallback((e) => {
    e.preventDefault();
    const rect = svgRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    setZoom(prev => {
      const next = e.deltaY < 0
        ? Math.min(ZOOM_MAX, prev * ZOOM_STEP)
        : Math.max(ZOOM_MIN, prev / ZOOM_STEP);
      const ratio = 1 - next / prev;
      setPan(p => ({ x: p.x + (mx - p.x) * ratio, y: p.y + (my - p.y) * ratio }));
      return next;
    });
  }, []);

  // 핀치 거리 계산
  const pinchDist = (pts) => {
    const arr = [...pts.values()];
    if (arr.length < 2) return 0;
    const dx = arr[0].x - arr[1].x, dy = arr[0].y - arr[1].y;
    return Math.hypot(dx, dy);
  };

  const onPointerDown = useCallback((e) => {
    svgRef.current?.setPointerCapture(e.pointerId);
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2) {
      // 핀치 시작
      pinchRef.current = { dist0: pinchDist(pointersRef.current), zoom0: zoom };
      dragRef.current = null;
    } else if (pointersRef.current.size === 1) {
      dragRef.current = { startX: e.clientX, startY: e.clientY, panX0: pan.x, panY0: pan.y };
    }
  }, [zoom, pan]);

  const onPointerMove = useCallback((e) => {
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2 && pinchRef.current) {
      // 핀치 줌
      const d = pinchDist(pointersRef.current);
      if (pinchRef.current.dist0 > 0) {
        const next = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pinchRef.current.zoom0 * (d / pinchRef.current.dist0)));
        setZoom(next);
      }
    } else if (dragRef.current && pointersRef.current.size === 1) {
      // 팬
      const dx = e.clientX - dragRef.current.startX, dy = e.clientY - dragRef.current.startY;
      setPan({ x: dragRef.current.panX0 + dx, y: dragRef.current.panY0 + dy });
    }
  }, []);

  const onPointerUp = useCallback((e) => {
    pointersRef.current.delete(e.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;
    if (pointersRef.current.size === 0) dragRef.current = null;
  }, []);

  // 휠 이벤트는 passive:false 필요 → ref 방식으로 등록
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const isZoomed = zoom !== 1 || pan.x !== 0 || pan.y !== 0;

  // v9.3.x: early return을 훅 아래로 이동 (Rules-of-Hooks 준수)
  if (geo.fit !== "radius") {
    return <EmptyState icon="⚠" title="Chip_Radius fit 불가" hint="shot 크기(mm)를 알 수 없어 확대 뷰를 그릴 수 없습니다" />;
  }

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <svg ref={svgRef} width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}
        style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6,
                 cursor: isZoomed ? "grab" : "zoom-in", touchAction: "none", userSelect: "none" }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
          {showImage && (
            <image href={imgUrl} x={ox} y={oy} width={w} height={h}
              preserveAspectRatio="none" opacity="0.9" />
          )}
          <rect x={ox} y={oy} width={w} height={h} fill={showImage ? "none" : "rgba(128,128,128,0.05)"}
            stroke="var(--muted)" strokeWidth={1 / zoom} />
          {/* shot 센터 십자 */}
          <line x1={toX(0) - 5 / zoom} y1={toY(0)} x2={toX(0) + 5 / zoom} y2={toY(0)} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          <line x1={toX(0)} y1={toY(0) - 5 / zoom} x2={toX(0)} y2={toY(0) + 5 / zoom} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          {/* 칩 격자 — c.x/c.y = 칩 좌하단(mm), y 축 반전이라 top = toY(c.y) - 높이 */}
          {cellsInfo && cellsInfo.cells.map(c => (
            <g key={c.i}>
              <rect x={toX(c.x)} y={toY(c.y) - c.h * s} width={c.w * s} height={c.h * s}
                fill="rgba(47,158,99,0.08)" stroke="#2f9e63" strokeWidth={0.8 / zoom} opacity="0.85" />
              <text x={toX(c.x + c.w / 2)} y={toY(c.y + c.h / 2)} fontSize={10 / zoom} fill="#2f9e63"
                textAnchor="middle" dominantBaseline="middle" opacity="0.85">{c.i + 1}</text>
            </g>
          ))}
          {/* TEG 직사각형 — MAIN 계열은 모양(패턴) 없이 점(dot)만 표시 */}
          {tegList.map(t => {
            const x = toX(t.ebeam_x), yBottom = toY(t.ebeam_y);
            const wpx = t.teg_w * s, hpx = t.teg_h * s;
            const main = isMainTeg(t.teg);
            const labelX = main ? x + 5 / zoom : x + wpx + 4 / zoom;
            const labelY = main ? yBottom : yBottom - hpx / 2;
            return (
              <g key={t.teg}>
                {!main && (
                  <rect x={x} y={yBottom - hpx} width={wpx} height={hpx} fill={tegColor(t.teg)} opacity="0.75" />
                )}
                <circle cx={x} cy={yBottom} r={2.4 / zoom} fill={tegColor(t.teg)} stroke="var(--panel)" strokeWidth={0.8 / zoom} />
                <text x={labelX} y={labelY} fontSize={11 / zoom} fill="var(--text)" dominantBaseline="middle">
                  {t.teg}
                </text>
              </g>
            );
          })}
          {/* 치수 라벨 */}
          <text x={SIZE / 2} y={oy + h + 16 / zoom} fontSize={11 / zoom} fill="var(--muted)" textAnchor="middle">
            {fmt(W)} mm
          </text>
          <text x={ox - 8 / zoom} y={SIZE / 2} fontSize={11 / zoom} fill="var(--muted)" textAnchor="middle"
            transform={`rotate(-90 ${ox - 8 / zoom} ${SIZE / 2})`}>
            {fmt(H)} mm
          </text>
          {cellsInfo && (
            <text x={ox + 4 / zoom} y={oy - 6 / zoom} fontSize={11 / zoom} fill="#2f9e63">
              칩 {cellsInfo.cols}×{cellsInfo.rows} = {cellsInfo.cols * cellsInfo.rows}개
              {display.chip_w > 0 ? ` · 칩 ${fmt(cellsInfo.cw)}×${fmt(cellsInfo.ch)} mm` : ""}
              {(display.gap_x > 0 || display.gap_y > 0) ? ` · 간격 ${fmt(display.gap_x * 1000, 0)}×${fmt(display.gap_y * 1000, 0)} µm` : ""}
            </text>
          )}
        </g>
      </svg>
      {/* 줌 리셋 버튼 — 줌/패닝 상태일 때만 표시 */}
      {isZoomed && (
        <button onClick={resetView} title="보기 초기화"
          style={{ position: "absolute", top: 6, right: 6, width: 28, height: 28,
                   display: "flex", alignItems: "center", justifyContent: "center",
                   background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 4,
                   cursor: "pointer", fontSize: 14, color: "var(--muted)", opacity: 0.85 }}>
          ↺
        </button>
      )}
      {/* 줌 배율 표시 */}
      {zoom > 1.05 && (
        <span style={{ position: "absolute", bottom: 6, right: 6, fontSize: 11,
                       color: "var(--muted)", background: "var(--panel)", padding: "1px 5px",
                       borderRadius: 3, border: "1px solid var(--line)", opacity: 0.8 }}>
          ×{zoom.toFixed(1)}
        </span>
      )}
    </div>
  );
}

/* ── shot 확대 우측 ebeam 좌표 패널 — 선택 TEG 가 5개 미만일 때만 표시.
   5개 이상이면 공간이 부족하므로 기존대로 배치도만 표시. ── */
function TegCoordInfo({ data, selectedTegs, tegColor }) {
  const tegList = (data?.tegs || []).filter(t => selectedTegs.has(t.teg));
  if (!tegList.length || tegList.length >= 5) return null;
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "8px 10px",
                  fontSize: 12, lineHeight: 1.7, minWidth: 150, alignSelf: "flex-start" }}>
      <div style={{ fontWeight: 700, color: "var(--muted)" }}>TEG 좌표 — ebeam_x / ebeam_y (mm)</div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}
        title="좌표체계 주의: 격자좌표(chip_x/y_adj)·Mapfile 상대좌표가 아닌 ebeam 좌표계입니다">
        shot 센터 기준 TEG 좌하단 (ebeam 좌표계)
      </div>
      {tegList.map(t => (
        <div key={t.teg} style={{ marginBottom: 4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ color: tegColor(t.teg), fontWeight: 700 }}>■</span>
            <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis",
                           whiteSpace: "nowrap", maxWidth: 160 }} title={t.teg}>{t.teg}</span>
          </div>
          <div style={{ color: "var(--muted)", marginLeft: 17 }}>
            ebeam_x {fmt(t.ebeam_x, 3)} · ebeam_y {fmt(t.ebeam_y, 3)}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── 선택 TEG 들의 shot 별 radius 표 — TEG 목록 체크 상태를 그대로 따라감.
   radius = |shot 센터 + TEG 좌하단 offset| (mm). 클라이언트 계산이라 즉시 갱신. ── */
function TegRadiusTable({ data, selectedTegs, tegColor }) {
  const geo = data?.geometry;
  const tegList = (data?.tegs || []).filter(t => selectedTegs.has(t.teg));
  const rows = useMemo(() => {
    if (!geo || geo.fit !== "radius" || !tegList.length) return [];
    const shots = (data.shots || []).filter(s0 => typeof s0.mm_x === "number");
    const out = shots.map(s0 => {
      const radii = {};
      for (const t of tegList) {
        radii[t.teg] = Math.hypot(s0.mm_x + t.ebeam_x, s0.mm_y + t.ebeam_y);
      }
      return { x: s0.x, y: s0.y, radii };
    });
    const first = tegList[0].teg;
    out.sort((a, b) => a.radii[first] - b.radii[first]);
    return out;
  }, [data, geo, tegList]);

  const cell = { padding: "3px 10px", borderBottom: "1px solid var(--line)", fontSize: 12, textAlign: "right" };
  return (
    <Card title="선택 TEG shot별 radius 표">
      {geo?.fit !== "radius" ? (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>Chip_Radius fit 불가 — radius 계산 불가.</div>
      ) : !tegList.length ? (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>TEG 목록에서 TEG 를 선택하면 shot 별 radius(mm) 가 표시됩니다.</div>
      ) : (
        <div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6, display: "flex", gap: 10, flexWrap: "wrap" }}>
            {tegList.map(t => {
              const vals = rows.map(r => r.radii[t.teg]);
              return (
                <span key={t.teg}>
                  <span style={{ color: tegColor(t.teg), fontWeight: 700 }}>■</span> {t.teg}:
                  min {fmt(Math.min(...vals), 3)} · max {fmt(Math.max(...vals), 3)} mm
                </span>
              );
            })}
            <span>· {rows.length} shots ({tegList[0].teg} radius 오름차순)</span>
          </div>
          <div style={{ maxHeight: 300, overflow: "auto", border: "1px solid var(--line)", borderRadius: 4 }}>
            <table style={{ borderCollapse: "collapse", width: "100%" }}>
              <thead><tr>
                <th style={{ ...cell, color: "var(--muted)" }}
                  title="chip_x_adj — shot 격자좌표 (ebeam 좌표계 아님)">shot x (격자)</th>
                <th style={{ ...cell, color: "var(--muted)" }}
                  title="chip_y_adj — shot 격자좌표 (ebeam 좌표계 아님)">shot y (격자)</th>
                {tegList.map(t => (
                  <th key={t.teg} style={{ ...cell, color: tegColor(t.teg) }}>{t.teg} radius (mm)</th>
                ))}
              </tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td style={cell}>{r.x}</td>
                    <td style={cell}>{r.y}</td>
                    {tegList.map(t => (
                      <td key={t.teg} style={{ ...cell, fontWeight: 600 }}>{fmt(r.radii[t.teg], 3)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}

/* ── Mapfile 체크 대상 TEG 설정 — 위치 조회 TEG 목록 아래 접이식 패널.
   위치 조회 vehicle 의 teg 목록(teg 열 기준)을 체크박스로 나열, 체크된 것이
   "TEG Mapfile 체크" 대상이 된다. 기본값 = 이름이 H_/V_ 로 시작하는 것 전부.
   페이지 관리 권한(admin / teg page manager)이 있어야 추가·변경·저장 가능. ── */
function CheckTargetEditor({ vehicle, canEdit }) {
  const [data, setData] = useState(null);
  const [sel, setSel] = useState(new Set());
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    if (!vehicle) { setData(null); return; }
    try {
      const r = await sf(`${API}/check-targets?vehicle=${encodeURIComponent(vehicle)}`);
      setData(r);
      setSel(new Set(r.targets || []));
    } catch (e) { toast.error(String(e.message || e)); }
  }, [vehicle]);
  useEffect(() => { load(); }, [load]);

  if (!vehicle) return null;

  const tegs = data?.tegs || [];
  const savedTargets = data?.targets || [];
  const dirty = !!data && (sel.size !== savedTargets.length
    || savedTargets.some(t => !sel.has(t)));
  const toggle = (name) => {
    if (!canEdit) return;
    setSel(prev => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name); else n.add(name);
      return n;
    });
  };
  const checkDefault = () => {
    if (!canEdit) return;
    setSel(new Set(tegs.filter(t => /^[hv]_/i.test(t.teg)).map(t => t.teg)));
  };
  const save = async () => {
    setSaving(true);
    try {
      const r = await putJson(`${API}/check-targets`, { vehicle, targets: [...sel] });
      setData(r); setSel(new Set(r.targets || []));
      toast.ok("Mapfile 체크 대상 저장됨");
    } catch (e) { toast.error(String(e.message || e)); }
    finally { setSaving(false); }
  };
  const resetDefault = async () => {
    setSaving(true);
    try {
      const r = await putJson(`${API}/check-targets`, { vehicle, targets: null });
      setData(r); setSel(new Set(r.targets || []));
      toast.ok("기본값(H_/V_)으로 초기화됨");
    } catch (e) { toast.error(String(e.message || e)); }
    finally { setSaving(false); }
  };

  const q = filter.trim().toLowerCase();
  const shown = q ? tegs.filter(t =>
    t.teg.toLowerCase().includes(q)
    || (t.top_cell || []).some(c => String(c).toLowerCase().includes(q))) : tegs;

  return (
    <div style={{ marginTop: 10, border: "1px solid var(--line)", borderRadius: 6 }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ display: "flex", alignItems: "center", gap: 6, width: "100%",
                 background: "none", border: "none", cursor: "pointer", color: "var(--text)",
                 padding: "7px 9px", fontSize: 12, fontWeight: 700, textAlign: "left" }}>
        <span style={{ color: "var(--muted)" }}>{open ? "▾" : "▸"}</span>
        Mapfile 체크 대상 TEG
        <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 400, color: "var(--muted)" }}>
          {sel.size}개 {data ? (data.source === "config" ? "· 지정됨" : "· 기본") : ""}
        </span>
      </button>
      {open && (
        <div style={{ padding: "0 9px 9px" }}>
          <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6, marginBottom: 6 }}>
            체크한 TEG 가 "TEG Mapfile 체크" 대상입니다. 기본값 = 이름이 H_/V_ 로 시작하는 것 전부.
            {!canEdit && <span style={{ color: "#c78a1e" }}> · admin / teg 페이지 관리자만 변경·저장할 수 있습니다.</span>}
            {!data?.teg_ok && <span style={{ color: "#e05252" }}> · 이 vehicle 의 Teg_location 데이터가 없습니다.</span>}
          </div>
          {tegs.length > 12 && (
            <input value={filter} onChange={e => setFilter(e.target.value)}
              placeholder="TEG/top_cell 검색" spellCheck={false}
              style={{ ...inputStyle, width: "100%", minWidth: 0, marginBottom: 6, fontSize: 12 }} />
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 1,
                        maxHeight: 260, overflowY: "auto" }}>
            {shown.length === 0 && (
              <span style={{ fontSize: 12, color: "var(--muted)" }}>표시할 TEG 가 없습니다.</span>
            )}
            {shown.map(t => {
              const on = sel.has(t.teg);
              return (
                <label key={t.teg}
                  title={t.top_cell?.length ? `top_cell: ${t.top_cell.join(", ")}` : "top_cell 없음"}
                  style={{ display: "flex", alignItems: "center", gap: 6,
                           padding: "3px 4px", fontSize: 12, borderRadius: 4,
                           cursor: canEdit ? "pointer" : "default",
                           opacity: canEdit ? 1 : 0.8 }}>
                  <input type="checkbox" checked={on} disabled={!canEdit}
                    onChange={() => toggle(t.teg)} />
                  <span style={{ fontWeight: on ? 700 : 400, overflow: "hidden",
                                 textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.teg}</span>
                  {t.top_cell?.length > 0 && (
                    <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)",
                                   overflow: "hidden", textOverflow: "ellipsis",
                                   whiteSpace: "nowrap", maxWidth: 90 }}>
                      {t.top_cell.join(", ")}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
          {canEdit && (
            <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
              <Button variant="primary" disabled={saving || !dirty} onClick={save}>저장</Button>
              <button onClick={checkDefault} disabled={saving}
                style={{ fontSize: 11, color: "var(--accent, #5a8cff)", background: "none",
                         border: "none", cursor: "pointer", textDecoration: "underline" }}>
                H_/V_ 선택
              </button>
              <button onClick={resetDefault} disabled={saving}
                style={{ fontSize: 11, color: "var(--muted)", background: "none",
                         border: "none", cursor: "pointer", textDecoration: "underline" }}>
                기본값으로 초기화
              </button>
              {dirty && <span style={{ fontSize: 11, color: "#e0a452" }}>저장 필요</span>}
            </div>
          )}
        </div>
      )}
    </div>
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
  const [view, setView] = useState("map");   // map=위치 조회 | check=TEG Mapfile 체크

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

  // MAIN overlay — Mapfile 체크에서 역반영된 그룹 메타 {group: {applied_at, count}}
  const mainOverlays = data?.main_overlays || {};
  // 일반 사용자 동시 선택 상한 (전체 렌더 시 502/브라우저 다운 방지). null = 관리자(무제한).
  const maxSel = data?.max_selection ?? null;
  // TEG 다중 선택 — 클릭으로 on/off 토글, 전체/해제 버튼.
  // MAIN 그룹명(정답지에 MAIN 자체가 등록된 경우)을 토글하면 내부 TEG("그룹·이름")도 함께 토글.
  const toggleTeg = (name) => {
    const next = new Set(selectedTegs);
    const turnOn = !next.has(name);
    if (turnOn) next.add(name); else next.delete(name);
    if (mainOverlays[name]) {
      (data?.tegs || []).forEach(t => {
        if (t.overlay_group === name) {
          if (turnOn) next.add(t.teg); else next.delete(t.teg);
        }
      });
    }
    if (turnOn && maxSel != null && next.size > maxSel) {
      toast.error(`일반 사용자는 TEG 를 최대 ${maxSel}개까지 선택할 수 있습니다. (관리자는 제한 없음)`);
      return;
    }
    setSelectedTegs(next);
  };
  const selectAllTegs = () => {
    if (maxSel != null && tegNames.length > maxSel) {
      setSelectedTegs(new Set(tegNames.slice(0, maxSel)));
      toast.error(`일반 사용자는 최대 ${maxSel}개까지만 선택됩니다. (관리자는 제한 없음)`);
    } else {
      setSelectedTegs(new Set(tegNames));
    }
  };
  const deselectAllTegs = () => setSelectedTegs(new Set());

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
            <Button onClick={loadMap}>새로고침</Button>
          </div>
        }
      />

      <TabStrip active={view} onChange={setView}
        items={[{ k: "map", l: "위치 조회" }, { k: "check", l: "TEG Mapfile 체크" }]} />

      {view === "check" && <TegCheck vehicle={vehicle} />}

      {view === "map" && <>
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
                  <Pill tone="warn" title={geo?.fit_note || ""}>
                    Chip_Radius fit 불가 — 격자 좌표로만 표시{geo?.fit_note ? ` (${geo.fit_note})` : ""}
                  </Pill>
                )}
                <Pill tone="neutral">
                  {display.mode === "grid" ? `칩 격자 ${display.cols}×${display.rows}` :
                    display.mode === "image" ? "그림" : "기본"}
                </Pill>
              </div>
            }>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
              shot 클릭 → 확대 뷰. 실선 원 = wafer {fmt(geo?.wafer_radius_mm, 0)}mm,
              점선 원 = 최외곽 {fmt(geo?.wafer_edge_mm, 0)}mm.
              TEG 선택 시: <span style={{ color: "#2f9e63", fontWeight: 700 }}>■ 초록</span> = TEG 전체가 최외곽 안,
              <span style={{ color: "#e05252", fontWeight: 700 }}> ■ 빨강</span> = TEG 가 걸림.
              미선택 시: <span style={{ color: "#3e7bd6", fontWeight: 700 }}>■ 연파랑</span> = shot 전체가 안,
              <span style={{ color: "#c78a1e", fontWeight: 700 }}> ■ 연노랑</span> = shot 이 걸치거나 밖.
              격자/그림은 shot 확대에서만 표시됩니다.
            </div>
            {/* 가장 가까운 샷 센터 — WaferMap 빨간 점 표시 + 실center 차이 계산용 */}
            {(() => {
              const withR = geo?.fit === "radius"
                ? (data.shots || []).filter(s0 => typeof s0.radius === "number")
                : [];
              // eslint-disable-next-line react-hooks/rules-of-hooks
              const _nearestShot = withR.length
                ? withR.reduce((a, b) => (a.radius <= b.radius ? a : b))
                : null;
              return (
            <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
              <WaferMap data={data} selectedTegs={selectedTegs} tegColor={tegColor}
                selectedShot={selectedShot} onShotClick={onShotClick}
                nearestShot={_nearestShot} />
              {/* TEG 목록 — 다중 선택 가능 */}
              <div style={{ minWidth: 170, maxWidth: 240 }}>
                {/* Chip_Radius 계산 정보 — shot 크기 + 가장 가까운 shot 실center 델타(µm) */}
                {geo?.fit === "radius" && (
                    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "8px 10px", marginBottom: 10, fontSize: 12, lineHeight: 1.7 }}>
                      <div style={{ fontWeight: 700, color: "var(--muted)", marginBottom: 2 }}>Chip_Radius 계산 정보</div>
                      <div>shot 크기: <b>{fmt(geo.shot_w_mm, 3)} × {fmt(geo.shot_h_mm, 3)} mm</b></div>
                      {_nearestShot && (
                        <>
                          <div>가장 가까운 샷: <b style={{ color: "#e05252" }}>({_nearestShot.x}, {_nearestShot.y})</b>
                            <span style={{ color: "var(--muted)", marginLeft: 4 }}>(빨간 점)</span></div>
                          <div title="실center(wafer 중심)에서 가장 가까운 샷 센터로 이동하는 Δx, Δy. x 우측↑, y 위↑ 양수.">
                            실center 차이: <b>Δx {fmt(_nearestShot.mm_x * 1000, 1)} · Δy {fmt(-_nearestShot.mm_y * 1000, 1)} µm</b>
                          </div>
                        </>
                      )}
                      <div title="fit 에 사용한 샷 수와 측정 radius − fit radius 최대 잔차">
                        fit: <b>{geo.fit_used}개 샷</b> · 잔차 max <b>{fmt(geo.fit_max_residual_mm, 3)} mm</b>
                      </div>
                      {(geo.fit_dropped || []).length > 0 && (
                        <div style={{ color: "#e05252", marginTop: 2 }}
                          title={"측정 Chip_Radius 가 fit 대비 크게 벗어나 자동 제외된 행 — 원본 CSV 값 확인 필요\n"
                            + (geo.fit_dropped || []).map(d0 =>
                              `(${d0.x}, ${d0.y}) r=${fmt(d0.r, 1)} (잔차 ${fmt(d0.residual_mm, 1)}mm)`).join("\n")}>
                          ⚠ 잘못된 샷이 있는 것 같습니다 — {(geo.fit_dropped || []).slice(0, 4).map(d0 =>
                            `(${d0.x}, ${d0.y})`).join(", ")}
                          {(geo.fit_dropped || []).length > 4 ? ` 외 ${(geo.fit_dropped || []).length - 4}개` : ""}
                          {" "}(Chip_Radius 값 확인 필요)
                        </div>
                      )}
                    </div>
                )}
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "var(--muted)" }}>
                    TEG 목록 ({selectedTegs.size}/{tegNames.length})
                    {maxSel != null && (
                      <span style={{ fontWeight: 400, color: "#c78a1e" }} title="일반 사용자 동시 선택 상한 (관리자는 무제한)"> · 최대 {maxSel}</span>
                    )}
                  </span>
                  {tegNames.length > 1 && (
                    <div style={{ display: "flex", gap: 2 }}>
                      <button onClick={selectAllTegs}
                        style={{ fontSize: 11, color: "var(--accent, #5a8cff)", background: "none",
                          border: "none", cursor: "pointer", padding: "2px 5px", textDecoration: "underline" }}>전체</button>
                      <button onClick={deselectAllTegs}
                        style={{ fontSize: 11, color: "var(--muted)", background: "none",
                          border: "none", cursor: "pointer", padding: "2px 5px", textDecoration: "underline" }}>해제</button>
                    </div>
                  )}
                </div>
                {tegNames.length === 0 && (
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>
                    Teg_location 파일에 이 vehicle 의 TEG 가 없습니다.
                  </span>
                )}
                <div style={{
                  display: "flex", flexDirection: "column", gap: 2,
                  maxHeight: 640 - 24, overflowY: "auto",
                  border: tegNames.length ? "1px solid var(--line)" : "none", borderRadius: 6,
                }}>
                  {tegNames.map(n => {
                    const t = (data.tegs || []).find(x => x.teg === n) || {};
                    const on = selectedTegs.has(n);
                    const ovG = t.overlay_group;
                    return (
                      <button key={n} onClick={() => toggleTeg(n)}
                        title={ovG
                          ? `MAIN ${ovG} 내부 TEG — ${(mainOverlays[ovG]?.applied_at || "").slice(0, 16).replace("T", " ")} Mapfile 기준 반영 (설비 세팅 유래, 이상 가능성 참고)\nebeam_x ${fmt(t.ebeam_x)} · ebeam_y ${fmt(t.ebeam_y)} mm (shot 센터 기준 TEG 좌하단)`
                          : `ebeam_x ${fmt(t.ebeam_x)} · ebeam_y ${fmt(t.ebeam_y)} mm (shot 센터 기준 TEG 좌하단)`}
                        style={{
                          display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
                          border: "none", borderLeft: `3px solid ${on ? tegColor(n) : "transparent"}`,
                          background: on ? "var(--panel)" : "transparent", color: "var(--text)",
                          padding: "5px 8px", fontSize: 13, textAlign: "left",
                          fontWeight: on ? 700 : 400, opacity: on ? 1 : 0.65,
                        }}>
                        <span style={{
                          width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                          border: on ? "none" : "1.5px solid var(--muted)",
                          background: on ? tegColor(n) : "transparent",
                          display: "inline-flex", alignItems: "center", justifyContent: "center",
                          fontSize: 10, color: "#fff", lineHeight: 1,
                        }}>{on ? "✓" : ""}</span>
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n}</span>
                        {ovG && (
                          <span style={{ marginLeft: "auto", fontSize: 10, flexShrink: 0,
                                         color: "#c78a1e", border: "1px solid #c78a1e",
                                         borderRadius: 4, padding: "0 4px" }}>Mapfile</span>
                        )}
                      </button>
                    );
                  })}
                </div>
                {/* MAIN overlay 참고문 — 설비 Mapfile 세팅 유래 값이라 이상 가능성 안내 */}
                {Object.keys(mainOverlays).length > 0 && (
                  <div style={{ marginTop: 8, padding: "6px 8px", borderRadius: 6,
                                background: "rgba(199,138,30,0.10)", fontSize: 11,
                                lineHeight: 1.6, color: "var(--muted)" }}>
                    <div style={{ fontWeight: 700, color: "#c78a1e" }}>
                      ⓘ MAIN 내부 TEG — Mapfile 기준 반영
                    </div>
                    {Object.entries(mainOverlays).map(([g, m]) => (
                      <div key={g}>
                        {g}: {(m.applied_at || "").slice(0, 16).replace("T", " ") || "반영 시각 미상"} 반영
                        · {m.count}개
                      </div>
                    ))}
                    <div>
                      TEG Mapfile 체크에서 가져온 설비 세팅 기준 값입니다 — 세팅 이상 가능성이
                      있으니 참고용으로 확인하세요.
                    </div>
                  </div>
                )}
                {/* Mapfile 체크 대상 TEG 설정 — 관리자만 편집 가능 */}
                <CheckTargetEditor vehicle={vehicle} canEdit={canEdit} />
              </div>
            </div>
              );
            })()}
          </Card>

          {/* 우: shot 확대 + radius */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: "1 1 400px", minWidth: 400 }}>
            <Card title={selectedShot ? `shot 확대 — (${selectedShot.x}, ${selectedShot.y})` : "shot 확대 — shot 내 TEG 배치"}>
              <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
                <ShotZoom data={data} selectedTegs={selectedTegs} tegColor={tegColor} imgUrl={imgUrl} />
                {/* 선택 TEG 5개 미만일 때만 우측에 ebeam 좌표 표시 */}
                <TegCoordInfo data={data} selectedTegs={selectedTegs} tegColor={tegColor} />
              </div>
            </Card>
            <TegRadiusTable data={data} selectedTegs={selectedTegs} tegColor={tegColor} />
          </div>
        </div>
      )}
      </>}

      <PageGear title="TEG 위치 조회 설정" canEdit={canEdit} position="bottom-right">
        <GearSettings vehicle={vehicle} canEdit={canEdit}
          onSaved={() => { loadVehicles(); loadMap(); }} />
      </PageGear>
    </div>
  );
}
