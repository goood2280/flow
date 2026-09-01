/* My_TegMap.jsx — TEG 위치 조회 (WF MAP).
   - chip layout(Mask, chip_x_adj, chip_y_adj, Chip_Radius) 파일로 wafer geometry fit:
     Chip_Radius = shot 센터 ↔ wafer 원점 거리(mm) → shot 크기(mm)·wafer 중심을 최소자승으로 산출.
   - Teg_location(vehicle,teg,ebeam_x,ebeam_y = shot 센터 기준 TEG 좌하단) 을 겹쳐
     여러 TEG 를 wafer 전체 / shot 확대 뷰로 동시 표시. wafer 원과 제품별 최외곽선을 함께 표시.
   - TEG 다중 선택: 체크박스로 여러 TEG 를 동시에 선택/비교 가능. 전체/해제 버튼 제공.
   - 동명 TEG 자동 넘버링: 백엔드에서 같은 이름이 2 개 이상이면 _1, _2, … 접미사를 자동 부여.
   - shot 색: 선택 TEG 전체가 제품별 최외곽 안이면 초록, 라인에 걸치면 빨강.
   - full shot 체크박스: layout 파일에 있는 shot 만 보는 게 기본. 켜면 같은 shot 크기로
     격자를 연장해 wafer 를 빠짐없이 덮는 자리까지 표시(점선) — 실제 노광 시 최외곽에
     걸리는 shot 에서 TEG 가 어디 놓이는지 보기 위한 것.
   - shot 클릭 → shot 확대 뷰. 그림/칩 격자는 확대 뷰에서만 표시 (wafer 전체 뷰는 shot 판정색+TEG 마커만).
   - vehicle 별 shot 표시 방식(⚙️ 설정): 기본 | 그림(teg_location/ 업로드 이미지) |
     칩 격자(cols×rows, 칩 크기 mm·칩 사이 간격 µm, shot 센터 기준 좌우/상하 대칭 배치).
   - 설정 json·그림 파일은 파일탐색기 위치(DB root)의 teg_location/ 폴더에 저장.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sf, postJson, putJson } from "../../lib/api";
import { toast } from "../../components/Toast";
import Modal from "../../components/Modal";
import PageGear from "../../components/PageGear";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows, spreadsheetTextFromRows } from "../../components/SpreadsheetPasteGrid";
import ZoomPanSvg from "../../components/ZoomPanSvg";
import { Button, Card, EmptyState, LinkBtn, Pill, Select, TabStrip } from "../../components/UXKit";
import TegCheck from "./TegCheck";
import TegGenerate from "./TegGenerate";
import My_FileBrowser from "../filebrowser/My_FileBrowser";

const API = "/api/teg-map";
const MAPFILE_DEPARTMENT_COLUMNS = ["match", "label"];

const TEG_COLORS = [
  "#e05252", "#3e7bd6", "#2f9e63", "#c78a1e", "#8a5fd0",
  "#d0568f", "#1fa0a8", "#8a8f2a", "#c06030", "#5a6ed0",
];

const inputStyle = {
  background: "var(--bg-card)", color: "var(--text-primary)", border: "1px solid var(--line)",
  borderRadius: 4, padding: "4px 8px", fontSize: 13, minWidth: 110,
};

function fmt(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return Number(v).toFixed(d);
}

// MAIN 계열은 정확히 MAIN + 숫자 두 자리(MAIN01, MAIN02, ...)만 인정한다.
const MAIN_RE = /^MAIN\d{2}$/i;
function isMainTeg(name) { return MAIN_RE.test(String(name || "").trim()); }

/* Teg_location의 업무 순서는 보존하되 MAIN 항목끼리만 이름 뒤 숫자의 자연순으로
   보인다. 일반 TEG를 함께 정렬하면 파일에 정한 공정 순서가 깨지므로 MAIN이 있던
   자리만 MAIN01, MAIN02, ... 순으로 치환한다. */
function tegListNames(tegs) {
  const names = (tegs || []).map(item => String(item?.teg || ""));
  const mains = names.filter(isMainTeg).sort((left, right) =>
    left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }));
  let mainIndex = 0;
  return names.map(name => isMainTeg(name) ? mains[mainIndex++] : name);
}

/* TEG 방향 — 백엔드가 Teg_location direction 열(없으면 V_ 이름 접두)로 판정해
   내려준다. 크기(teg_w/teg_h)는 파일에 실제 배치 방향 그대로 들어 있어 vertical 은
   이미 세운 값(가로 1000×50 → vertical 50×1000)이다 — 화면은 그대로 그린다. */
function isVertical(t) { return ["v", "v_L"].includes(String(t?.flat_zone || "h")); }
function directionLabel(t) { return String(t?.flat_zone || "h") === "v_L" ? "V(L)" : isVertical(t) ? "V(R)" : "H"; }
const DIR_TIP = "방향 — Teg_location 의 direction 열(없으면 TEG 이름 접두 V_/H_) 기준."
  + " 숫자 flat_zone 은 270°=V(R), 90°=V(L·노치 왼쪽)로 구분합니다."
  + " teg_w/teg_h 는 파일에 실제 배치 방향 그대로 있어 V 는 이미 세운 크기입니다"
  + " (크기 열이 없어 ⚙️ 기본 사이즈로 채울 때만 V 를 세워서 적용합니다).";

/* TEG 사각형 크기(mm) — MAIN(die 급 블록)은 패턴 크기(teg_w/teg_h)가 아니라
   MAIN chip 크기 파일의 chip 크기로 그린다. 크기표에 없으면 null (점만 찍는다). */
function tegBox(t) {
  if (isMainTeg(t.teg)) {
    return (t.chip_w > 0 && t.chip_h > 0) ? { w: t.chip_w, h: t.chip_h, die: true } : null;
  }
  return { w: t.teg_w, h: t.teg_h, die: false };
}

/* Convert the two source coordinate systems to one Cartesian wafer system.
   - shot.mm_y (from chip_y_adj): down-positive
   - teg.ebeam_y (inside each shot): up-positive
   Every shot's ebeam origin is its geometric center. */
function waferTegCartesianPosition(shot, teg) {
  return {
    x: shot.mm_x + teg.ebeam_x,
    y: -shot.mm_y + teg.ebeam_y,
  };
}

/* ── full shot 격자 ──
   chip layout 파일의 격자(pitch)를 이전과 같이 연장하되, 판정 원만 wafer 150mm가
   아니라 제품별 최외곽(기본 147mm)을 쓴다. shot 사각형이 이 원 안에 걸리거나
   경계에 정확히 닿으면 남기고, 147mm 밖에 완전히 떨어진 shot만 제외한다.
   - 격자 위상은 실제 shot 하나를 앵커로 삼아 유지한다 (파일의 격자와 정확히 겹침).
    - 제품 최외곽 원(wafer_edge_mm)과 겹치거나 정확히 닿는 자리까지 남긴다.
   - 파일에 없어 새로 만든 자리는 synthetic:true — 화면에서 점선으로 구분한다. */
const FULL_SHOT_EDGE_EPS = 1e-9;     // mm — 경계 접촉을 포함하기 위한 float 오차 허용치
// 안전 상한 — pitch 를 잘못 잡았거나 shot 이 비상식적으로 작으면 격자가 폭발한다.
// wafer 를 덮는 데 필요한 shot 수(≈ πR²/격자면적)를 먼저 어림해 넘으면 그냥 원래
// 목록을 돌려준다 (화면이 수만 개 사각형으로 굳는 것을 막는다).
const FULL_SHOT_MAX = 6000;

function gridKey(x, y) {
  return `${Math.round(x * 1e6) / 1e6},${Math.round(y * 1e6) / 1e6}`;
}

function buildFullShots(data) {
  const geo = data?.geometry;
  const real = data?.shots || [];
  if (!geo || geo.fit !== "radius" || !real.length) return real;
  const gridCols = Math.max(0, Math.trunc(Number(geo.grid_cols) || 0));
  const gridRows = Math.max(0, Math.trunc(Number(geo.grid_rows) || 0));
  if (gridCols > 0 && gridRows > 0) {
    if (gridCols * gridRows > FULL_SHOT_MAX) return real;
    const seen = new Set(real.map(s0 => gridKey(s0.x, s0.y)));
    const gW = Math.abs(Number(geo.shot_w_mm) || 0);
    const gH = Math.abs(Number(geo.shot_h_mm) || 0);
    const gR = Number(geo.wafer_edge_mm || geo.wafer_radius_mm) || 0;
    const out = real.filter(s0 => shotIntersectsWaferEdge(s0, geo));
    for (let x = 1; x <= gridCols; x += 1) {
      for (let y = 1; y <= gridRows; y += 1) {
        const key = gridKey(x, y);
        if (seen.has(key)) continue;
        const mmx = (x - geo.cx) * geo.kx;
        const mmy = (y - geo.cy) * geo.ky;
        if (gR > 0) {
          const dx = Math.max(0, Math.abs(mmx) - gW / 2);
          const dy = Math.max(0, Math.abs(mmy) - gH / 2);
          if (Math.hypot(dx, dy) > gR + FULL_SHOT_EDGE_EPS) continue;
        }
        out.push({
          x, y, synthetic: true,
          mm_x: Math.round(mmx * 1e4) / 1e4,
          mm_y: Math.round(mmy * 1e4) / 1e4,
          radius: Math.round(Math.hypot(mmx, mmy) * 1e4) / 1e4,
        });
      }
    }
    return out;
  }
  const px = Math.abs(geo.pitch_x) || 0, py = Math.abs(geo.pitch_y) || 0;
  const stepX = px * Math.abs(geo.kx), stepY = py * Math.abs(geo.ky);   // mm 단위 격자 간격
  const W = Math.abs(geo.shot_w_mm), H = Math.abs(geo.shot_h_mm);
  const R = Number(geo.wafer_edge_mm || geo.wafer_radius_mm) || 0;
  if (!(stepX > 0) || !(stepY > 0) || !(W > 0) || !(H > 0) || !(R > 0)) return real;

  // 앵커 = 실제 shot 중 wafer 중심에 가장 가까운 것 (격자 위상 기준점)
  const anchor = real.reduce((a, b) => {
    const ra = typeof a.radius === "number" ? a.radius : Infinity;
    const rb = typeof b.radius === "number" ? b.radius : Infinity;
    return rb < ra ? b : a;
  });
  if (Math.PI * R * R / (stepX * stepY) > FULL_SHOT_MAX) return real;
  const nx = Math.ceil((R + W) / stepX) + 1;
  const ny = Math.ceil((R + H) / stepY) + 1;

  const seen = new Set(real.map(s0 => gridKey(s0.x, s0.y)));
  // full shot에서는 x=0 또는 y=0 축상의 shot을 그리지 않는다. 실제 layout shot도
  // 같은 표시 규칙을 적용하되 seen에는 남겨 synthetic shot으로 다시 생기지 않게 한다.
  const out = real.filter(s0 => Math.abs(Number(s0.x)) > 1e-9
    && Math.abs(Number(s0.y)) > 1e-9
    && shotIntersectsWaferEdge(s0, geo));
  for (let i = -nx; i <= nx; i++) {
    for (let j = -ny; j <= ny; j++) {
      const x = Math.round((anchor.x + i * px) * 1e6) / 1e6;
      const y = Math.round((anchor.y + j * py) * 1e6) / 1e6;
      const k = gridKey(x, y);
      if (seen.has(k)) continue;
      if (Math.abs(x) <= 1e-9 || Math.abs(y) <= 1e-9) continue;
      const mmx = (x - geo.cx) * geo.kx;
      const mmy = (y - geo.cy) * geo.ky;
      // shot 사각형과 147mm 원의 겹침 — 사각형에서 원 중심까지의 최단거리로 판정
      const dx = Math.max(0, Math.abs(mmx) - W / 2);
      const dy = Math.max(0, Math.abs(mmy) - H / 2);
      if (Math.hypot(dx, dy) > R + FULL_SHOT_EDGE_EPS) continue;
      seen.add(k);
      out.push({
        x, y, synthetic: true,
        mm_x: Math.round(mmx * 1e4) / 1e4,
        mm_y: Math.round(mmy * 1e4) / 1e4,
        radius: Math.round(Math.hypot(mmx, mmy) * 1e4) / 1e4,
      });
    }
  }
  return out;
}

function shotIntersectsWaferEdge(shot, geometry) {
  const edge = Number(geometry?.wafer_edge_mm || geometry?.wafer_radius_mm) || 147;
  const halfW = Math.abs(Number(geometry?.shot_w_mm) || 0) / 2;
  const halfH = Math.abs(Number(geometry?.shot_h_mm) || 0) / 2;
  const mmX = shot?.mm_x != null
    ? Number(shot.mm_x)
    : (Number(shot?.x) - Number(geometry?.cx || 0)) * Number(geometry?.kx || 0);
  const mmY = shot?.mm_y != null
    ? Number(shot.mm_y)
    : (Number(shot?.y) - Number(geometry?.cy || 0)) * Number(geometry?.ky || 0);
  if (![edge, halfW, halfH, mmX, mmY].every(Number.isFinite)) return false;
  const dx = Math.max(0, Math.abs(mmX) - halfW);
  const dy = Math.max(0, Math.abs(mmY) - halfH);
  return Math.hypot(dx, dy) <= edge + FULL_SHOT_EDGE_EPS;
}

function shotInsideWaferEdge(shot, geometry) {
  const edge = Number(geometry?.wafer_edge_mm || geometry?.wafer_radius_mm) || 147;
  const halfW = Math.abs(Number(geometry?.shot_w_mm) || 0) / 2;
  const halfH = Math.abs(Number(geometry?.shot_h_mm) || 0) / 2;
  const mmX = shot?.mm_x != null
    ? Number(shot.mm_x)
    : (Number(shot?.x) - Number(geometry?.cx || 0)) * Number(geometry?.kx || 0);
  const mmY = shot?.mm_y != null
    ? Number(shot.mm_y)
    : (Number(shot?.y) - Number(geometry?.cy || 0)) * Number(geometry?.ky || 0);
  if (![edge, halfW, halfH, mmX, mmY].every(Number.isFinite)) return false;
  return Math.hypot(Math.abs(mmX) + halfW, Math.abs(mmY) + halfH)
    <= edge + FULL_SHOT_EDGE_EPS;
}

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

/* 그림에서 인식한 die 사각형 — 실패해도 화면을 막지 않는다(die 판정만 빠진다). */
async function fetchShapes(vehicle) {
  try {
    return await sf(`${API}/image/shapes?vehicle=${encodeURIComponent(vehicle)}`);
  } catch (_) { return null; }
}

async function deleteImage(vehicle) {
  const res = await fetch(`${API}/image?vehicle=${encodeURIComponent(vehicle)}`,
    { method: "DELETE", headers: { "X-Session-Token": _token() } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || "삭제 실패");
  return body;
}

/* 클립보드 이벤트에서 그림 파일 하나 꺼내기 — 없으면 null.
   붙여넣은 그림은 이름이 없거나 확장자가 없을 수 있어 MIME 으로 이름을 지어준다
   (백엔드도 매직 바이트로 다시 확인하므로 둘 중 하나만 맞아도 저장된다). */
const PASTE_EXT = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp" };
function clipboardImage(e) {
  const items = (e.clipboardData && e.clipboardData.items) || [];
  for (const it of items) {
    if (it.kind !== "file" || !String(it.type || "").startsWith("image/")) continue;
    const blob = it.getAsFile();
    if (!blob) continue;
    const ext = PASTE_EXT[it.type] || "png";
    return new File([blob], `pasted.${ext}`, { type: it.type || "image/png" });
  }
  return null;
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
  const [uploading, setUploading] = useState(false);
  const [shapes, setShapes] = useState(null);   // 그림에서 인식한 die 사각형 요약
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await sf(API + "/config");
      setInfo(r);
      setCfg(r.config);
      const storedVehicleCfg = ((r.config.vehicles || {})[vehicle] || {});
      const v0 = { mode: "none", cols: 1, rows: 1, chip_w: 0, chip_h: 0, gap_x: 0, gap_y: 0, image: "",
        ...storedVehicleCfg,
        wafer_edge_mm: Number(storedVehicleCfg.wafer_edge_mm)
          || Number(r.config.wafer_edge_mm) || 147 };
      // 칩 크기·칩 사이 간격 모두 서버 mm ↔ UI µm 변환.
      setVcfg({
        ...v0,
        chip_w_um: Math.round((Number(v0.chip_w) || 0) * 1000),
        chip_h_um: Math.round((Number(v0.chip_h) || 0) * 1000),
        gap_x_um: Math.round((Number(v0.gap_x) || 0) * 1000),
        gap_y_um: Math.round((Number(v0.gap_y) || 0) * 1000),
        // 제품별 값이 없는 구버전 설정은 전역 기본값을 그대로 상속한다.
        teg_default_w_um: Math.round((Number(storedVehicleCfg.teg_default_w)
          || Number(r.config.teg_default_w) || 3) * 1000),
        teg_default_h_um: Math.round((Number(storedVehicleCfg.teg_default_h)
          || Number(r.config.teg_default_h) || 0.1) * 1000),
      });
      // TEG Mapfile 체크 오프셋 — 편집 편의를 위해 평탄화
      const c0 = r.config.check || {};
      const fo = c0.flat_offsets || {};
      const pc0 = ((c0.products || {})[vehicle] || {});
      const pfo = pc0.flat_corrections || {};
      setChk({
        h_dx: (fo.h || [0, 0])[0], h_dy: (fo.h || [0, 0])[1],
        v_dx: (fo.v_R || [0, 0])[0], v_dy: (fo.v_R || [0, 0])[1],
        vl_dx: (fo.v_L || [0, 0])[0], vl_dy: (fo.v_L || [0, 0])[1],
        p_h_dx: (pfo.h || [0, 0])[0], p_h_dy: (pfo.h || [0, 0])[1],
        p_v_dx: (pfo.v_R || [0, 0])[0], p_v_dy: (pfo.v_R || [0, 0])[1],
        p_vl_dx: (pfo.v_L || [0, 0])[0], p_vl_dy: (pfo.v_L || [0, 0])[1],
        die_tol: c0.die_tol ?? 3.0,
        mapfile_department_rows: normalizeSpreadsheetRows(
          (c0.mapfile_departments || []).map(value => typeof value === "string"
            ? { match: value, label: value }
            : { match: value?.match || "", label: value?.label || value?.match || "" }),
          MAPFILE_DEPARTMENT_COLUMNS, { minRows: 4, maxRows: 100 }),
        // 화면에서 편집하지 않지만 저장 시 함께 돌려보내야 한다 — 빼면 서버가
        // 기본값으로 덮어써 사용자 지정 flat 마커가 조용히 지워진다.
        custom_markers: c0.custom_markers || {},
        extension_macros: c0.extension_macros || {},
        modules: (c0.modules || []).map(m => ({ ...m })),
        product_modules: (pc0.modules || []).map(m => ({ ...m })),
        products: c0.products || {},
      });
      // 그림이 없어도 조회한다 — MAIN chip 크기 파일만으로 die 가 나올 수 있다.
      setShapes(["image", "dev_grid"].includes(v0.mode) && vehicle ? await fetchShapes(vehicle) : null);
    } catch (e) { toast.error(String(e.message || e)); }
  }, [vehicle]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const patch = {
        layout_file: cfg.layout_file,
        teg_file: cfg.teg_file,
        main_chip_file: cfg.main_chip_file,
        ebeam_scale: Number(cfg.ebeam_scale) || 0.001,
        wafer_radius_mm: Number(cfg.wafer_radius_mm) || 150.0,
        wafer_edge_mm: Number(cfg.wafer_edge_mm) || 147.0,
      };
      if (chk) {
        const productsWithoutFirstPad = Object.fromEntries(Object.entries(chk.products || {}).map(([key, value]) => {
          const { first_pad_default, pchk_first_pad_default, first_pad_modules, ...rest } = value || {};
          return [key, rest];
        }));
        patch.check = {
          flat_offsets: {
            h: [Number(chk.h_dx) || 0, Number(chk.h_dy) || 0],
            v_R: [Number(chk.v_dx) || 0, Number(chk.v_dy) || 0],
            v_L: [Number(chk.vl_dx) || 0, Number(chk.vl_dy) || 0],
          },
          // 같은 형상의 PCHK/TEG 기준점을 사용한다. 레거시 first-pad 값은 계산에
          // 쓰지 않으며 저장 시 비워 제품 ΔX/ΔY가 유일한 형상 위치 보정이 되게 한다.
          first_pad_default: [0, 0],
          pchk_first_pad_default: [0, 0],
          die_tol: Math.max(0, Number(chk.die_tol) || 0),
          mapfile_departments: (chk.mapfile_department_rows || [])
            .map(row => ({
              match: String(row.match || "").trim(),
              label: String(row.label || row.match || "").trim(),
            }))
            .filter(row => row.match),
          custom_markers: chk.custom_markers || {},
          extension_macros: chk.extension_macros || {},
          modules: (chk.modules || [])
            .filter(m => String(m.name || "").trim())
            .map(m => ({
              flat: ["v_R", "v_L"].includes(m.flat) ? m.flat : "h",
              name: String(m.name).trim(),
              dx: Number(m.dx) || 0,
              dy: Number(m.dy) || 0,
              note: String(m.note || "").trim(),
            })),
          first_pad_modules: [],
          products: {
            ...productsWithoutFirstPad,
            ...(vehicle ? { [vehicle]: {
              flat_corrections: {
                h: [Number(chk.p_h_dx) || 0, Number(chk.p_h_dy) || 0],
                v_R: [Number(chk.p_v_dx) || 0, Number(chk.p_v_dy) || 0],
                v_L: [Number(chk.p_vl_dx) || 0, Number(chk.p_vl_dy) || 0],
              },
              modules: (chk.product_modules || []).filter(m => String(m.name || "").trim())
                .map(m => ({ flat: ["v_R", "v_L"].includes(m.flat) ? m.flat : "h",
                  name: String(m.name).trim(), dx: Number(m.dx) || 0, dy: Number(m.dy) || 0,
                  note: String(m.note || "").trim() })),
            }} : {}),
          },
        };
      }
      if (vehicle && vcfg) {
        const { gap_x_um, gap_y_um, chip_w_um, chip_h_um, wafer_edge_mm,
          teg_default_w_um, teg_default_h_um, ...vrest } = vcfg;
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
            wafer_edge_mm: Number(wafer_edge_mm) || 147,
            // 현재 선택 제품에만 저장. 기존 전역값은 구버전 제품의 fallback으로 유지한다.
            teg_default_w: (Number(teg_default_w_um) || 3000) / 1000,
            teg_default_h: (Number(teg_default_h_um) || 100) / 1000,
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

  const putImage = useCallback(async (file, how) => {
    if (!file) return;
    if (!vehicle) { toast.error("vehicle 을 먼저 선택하세요"); return; }
    setUploading(true);
    try {
      const r = await uploadImage(vehicle, file);
      setShapes(r.shapes || null);
      const n = r.shapes && r.shapes.count;
      toast.ok(`그림 ${how} — ${r.image}` + (n ? ` · die 사각형 ${n}개 인식` : ""));
      await load();
      onSaved && onSaved();
    } catch (err) { toast.error(String(err.message || err)); }
    finally { setUploading(false); }
  }, [vehicle, load, onSaved]);

  const onUpload = async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    await putImage(f, "업로드됨");
    if (fileRef.current) fileRef.current.value = "";
  };

  // 드로어가 열려 있고 그림 모드일 때는 어디에 포커스가 있든 Ctrl+V 로 바로 붙여넣는다.
  // 클립보드에 그림이 없으면 아무것도 하지 않으므로 텍스트 붙여넣기는 그대로 동작한다.
  const pasteEnabled = canEdit && vcfg && vcfg.mode === "image" && !!vehicle;
  useEffect(() => {
    if (!pasteEnabled) return;
    const onPaste = (e) => {
      const f = clipboardImage(e);
      if (!f) return;
      e.preventDefault();
      putImage(f, "붙여넣어짐");
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [pasteEnabled, putImage]);

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
  const setList = (key, next) => setChk(prev => ({ ...prev, [key]: next }));
  const updateList = (key, i, patch) => setChk(prev => ({
    ...prev, [key]: (prev[key] || []).map((m, j) => (j === i ? { ...m, ...patch } : m)),
  }));
  const addList = (key, value) => setChk(prev => ({ ...prev, [key]: [...(prev[key] || []), value] }));
  const deleteList = (key, i) => setList(key, (chk[key] || []).filter((_, j) => j !== i));
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
      <div style={row}>
        <span style={lab} title="vehicle, chip_name, chipsize_x, chipsize_y (µm) — 그림 모드에서 MAIN die 크기">
          MAIN chip 크기 파일
        </span>
        <input style={{ ...inputStyle, flex: 1 }} list="teg-file-candidates" disabled={dis}
          value={cfg.main_chip_file || ""} onChange={e => set({ main_chip_file: e.target.value })} />
        <Pill tone={info?.main_chip_ok ? "ok" : "warn"}>{info?.main_chip_ok ? "있음" : "없음(선택)"}</Pill>
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: -2, marginBottom: 6 }}>
        MAIN chip 크기 파일 열: vehicle, chip_name, chipsize_x, chipsize_y (µm).
        있으면 그림 모드의 die 를 MAIN TEG 좌표(좌하단)에 이 크기로 그립니다 — 없으면 그림에서 인식한 크기로 그립니다.
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
        <span style={lab}>wafer 반경 / 기본 최외곽 (mm)</span>
        <input style={num} type="number" step="any" disabled={dis} value={cfg.wafer_radius_mm}
          onChange={e => set({ wafer_radius_mm: e.target.value })} />
        <span style={{ color: "var(--muted)" }}>/</span>
        <input style={num} type="number" step="any" disabled={dis} value={cfg.wafer_edge_mm}
          onChange={e => set({ wafer_edge_mm: e.target.value })} />
      </div>

      <div style={sect}>제품별 TEG 기본 사이즈 (µm) — teg_w/teg_h 열이 없을 때</div>
      <div style={row}>
        <span style={lab}>{vehicle || "현재 제품"} 가로 × 세로</span>
        <input style={num} type="number" step="any" min="1" disabled={dis} value={vcfg.teg_default_w_um}
          onChange={e => setV({ teg_default_w_um: e.target.value })} />
        <span style={{ color: "var(--muted)" }}>×</span>
        <input style={num} type="number" step="any" min="1" disabled={dis} value={vcfg.teg_default_h_um}
          onChange={e => setV({ teg_default_h_um: e.target.value })} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>이 제품에만 저장</span>
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: -2, marginBottom: 6 }}>
        가로(Horizontal) 기준으로 입력하세요 — direction=V 인 TEG 는 이 값을 세워서
        (가로↔세로 바꿔) 그립니다. teg_w/teg_h 열이 있으면 <b>파일 값을 그대로</b> 쓰며,
        V 행은 파일에 이미 세운 크기로 들어 있는 규약이라 다시 뒤집지 않습니다.
      </div>

      <div style={sect}>Mapfile 검증 — 오프셋</div>
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
          <b style={{ color: "var(--text-primary)" }}>Mapfile 부서 구분</b> — 포함값은 TEG 이름 또는 top_cell에서 찾고,
          표시명은 세팅됨·세팅 안 됨 목록의 그룹 제목으로 사용합니다. 예: <code>DVC → DVC_TEAM</code>, <code>SRAM → SRAM_A</code>.
        </div>
        <SpreadsheetPasteGrid columns={MAPFILE_DEPARTMENT_COLUMNS}
          rows={chk.mapfile_department_rows || []}
          onChange={rows => setC({ mapfile_department_rows: rows })}
          disabled={dis} minRows={4} maxRows={100} maxHeight={190} minTableWidth={420}
          columnLabels={{ match: "포함값", label: "표시명" }}
          placeholders={{ match: "DVC", label: "DVC_TEAM" }}
          aliases={{ keyword: "match", department: "label", name: "label" }}
          ariaLabel="Mapfile 부서 구분" />
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 5 }}>
          한 부서에 포함값이 여러 개면 쉼표로 입력하거나 같은 표시명을 여러 행에 반복하세요.
          위에서 먼저 일치한 포함값을 사용하며 표시명이 비어 있으면 첫 포함값을 그대로 표시합니다.
        </div>
      </div>
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
        <span style={lab}>기본 오프셋 Vertical(L)</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>x'</span>
        <input style={num} type="number" step="any" disabled={dis} value={chk.vl_dx}
          onChange={e => setC({ vl_dx: e.target.value })} />
        <span style={{ fontSize: 12, color: "var(--muted)" }}>y'</span>
        <input style={num} type="number" step="any" disabled={dis} value={chk.vl_dy}
          onChange={e => setC({ vl_dy: e.target.value })} />
      </div>
      <div style={{ ...row, padding: 8, border: "1px solid var(--line)", borderRadius: 6 }}>
        <b style={{ minWidth: 120 }}>제품 보정 — {vehicle || "제품 선택"}</b>
        {[["H", "p_h"], ["V(R)", "p_v"], ["V(L)", "p_vl"]].map(([label, key]) => <span key={key}
          style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <small>{label} ΔX/ΔY</small>
          <input style={num} type="number" step="any" disabled={dis || !vehicle} value={chk[`${key}_dx`]}
            onChange={e => setC({ [`${key}_dx`]: e.target.value })} />
          <input style={num} type="number" step="any" disabled={dis || !vehicle} value={chk[`${key}_dy`]}
            onChange={e => setC({ [`${key}_dy`]: e.target.value })} />
        </span>)}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
        기존 기본 오프셋은 그대로 유지하고 회전 원복 후 Horizontal 표준 좌표계의 제품 보정 ΔX/ΔY를 추가합니다.
        제품값이 0이면 기존 계산과 동일하며 기준 PCHK 자신에는 더하지 않습니다.
      </div>
      <div style={row}>
        <span style={lab}>die 겹침 허용오차</span>
        <input style={num} type="number" step="any" min="0" disabled={dis} value={chk.die_tol}
          onChange={e => setC({ die_tol: e.target.value })} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          ebeam raw 단위 (ΔX/ΔY 와 같은 공간). 경계선이 정확히 맞닿거나 die 안쪽으로
          이 값 이하만 걸친 것은 <b>정상 허용</b>합니다. 이 값을 넘는 실제 침범만 경고합니다.
        </span>
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
            <option value="v_L">Vertical(L)</option>
          </select>
          <input style={{ ...inputStyle, minWidth: 110, width: 130 }} disabled={dis}
            placeholder="TEG(module) 이름" value={m.name}
            onChange={e => setMod(i, { name: e.target.value })} />
          <span style={{ fontSize: 12, color: "var(--muted)" }} title="TEG(H 관점) x 오프셋 — 양수 입력 = 빼기">x</span>
          <input style={num} type="number" step="any" disabled={dis} value={m.dx}
            title="TEG(H 관점) x 오프셋 — 양수=빼기. V면 실좌표 y에 반영" onChange={e => setMod(i, { dx: e.target.value })} />
          <span style={{ fontSize: 12, color: "var(--muted)" }} title="TEG(H 관점) y 오프셋 — 양수 입력 = 빼기">y</span>
          <input style={num} type="number" step="any" disabled={dis} value={m.dy}
            title="TEG(H 관점) y 오프셋 — 양수=빼기. V면 실좌표 -x에 반영" onChange={e => setMod(i, { dy: e.target.value })} />
          <input style={{ ...inputStyle, flex: 1, minWidth: 90 }} disabled={dis}
            placeholder="비고" value={m.note || ""}
            onChange={e => setMod(i, { note: e.target.value })} />
          <Button disabled={dis} onClick={() => delMod(i)}>삭제</Button>
        </div>
      ))}
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
        적용 순서: flat 변환(Vertical(R) 회전 원복) → 기본 오프셋 → TEG별 오프셋.<br />
        TEG별 오프셋은 항상 Horizontal(TEG) 관점으로 입력합니다. 양수 = 빼기.<br />
        Vertical TEG: TEG x → 실좌표 y, TEG y → 실좌표 -x 로 축 변환 적용.
        이름이 비어 있는 행은 저장 시 제외됩니다.
      </div>

      <div style={{ ...row, marginTop: 8 }}><span style={lab}>제품별 TEG 보정</span>
        <Button disabled={dis || !vehicle} onClick={() => addList("product_modules", { flat: "h", name: "", dx: 0, dy: 0, note: "" })}>+ 추가</Button></div>
      {(chk.product_modules || []).map((m, i) => <div key={i} style={row}>
        <select style={inputStyle} disabled={dis} value={m.flat} onChange={e => updateList("product_modules", i, { flat: e.target.value })}>
          <option value="h">H</option><option value="v_R">V(R)</option><option value="v_L">V(L)</option>
        </select>
        <input style={{ ...inputStyle, width: 150 }} disabled={dis} placeholder="TEG 이름" value={m.name}
          onChange={e => updateList("product_modules", i, { name: e.target.value })} />
        <small>ΔX</small><input style={num} type="number" disabled={dis} value={m.dx}
          onChange={e => updateList("product_modules", i, { dx: e.target.value })} />
        <small>ΔY</small><input style={num} type="number" disabled={dis} value={m.dy}
          onChange={e => updateList("product_modules", i, { dy: e.target.value })} />
        <input style={{ ...inputStyle, flex: 1 }} disabled={dis} value={m.note || ""} placeholder="비고"
          onChange={e => updateList("product_modules", i, { note: e.target.value })} />
        <Button disabled={dis} onClick={() => deleteList("product_modules", i)}>삭제</Button>
      </div>)}

      <div style={sect}>제품별 wafer · shot 설정 — {vehicle || "(vehicle 선택)"}</div>
      <div style={row}>
        <span style={lab}>제품 최외곽 반경 (mm)</span>
        <input style={num} type="number" step="any" min="0.001"
          max={Number(cfg.wafer_radius_mm) || 150} disabled={dis || !vehicle}
          value={vcfg.wafer_edge_mm ?? 147}
          onChange={e => setV({ wafer_edge_mm: e.target.value })} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          기본 147 · WF MAP, full shot, full chip 판정에 함께 적용
        </span>
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: -2, marginBottom: 8 }}>
        이 제품만 146 mm처럼 다르게 설정할 수 있습니다. full shot은 이 반경의 원과 겹치거나 경계에 닿는 shot을 표시합니다.
      </div>
      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--muted)", marginBottom: 8 }}>shot 표시 방식</div>
      <div style={row}>
        {[["none", "기본", "TEG 만 표시"],
          ["image", "그림", "붙여넣은 그림 (shot 확대에서 그림/격자 전환 가능)"],
          ["grid", "칩 격자", "칩 개수·크기·간격으로 계산한 격자"],
          ["dev_grid", "개발 격자", "MAIN TEG 좌표에 MAIN chip 크기 파일의 chip 크기로 그린 die"],
        ].map(([m, label, hint]) => (
          <label key={m} title={hint}
            style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: dis ? "default" : "pointer" }}>
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
            {uploading && <span style={{ fontSize: 12, color: "var(--muted)" }}>저장 중…</span>}
          </div>
          {/* 붙여넣기 영역 — 캡처한 die 배치도를 파일로 저장하지 않고 바로 넣는다.
              클릭해서 포커스를 준 뒤 Ctrl+V, 또는 드로어 어디서든 Ctrl+V. */}
          <div tabIndex={dis ? -1 : 0} onPaste={dis ? undefined : (e) => {
            const f = clipboardImage(e);
            if (!f) return;
            e.preventDefault();
            putImage(f, "붙여넣어짐");
          }}
            style={{
              border: "1px dashed var(--line)", borderRadius: 6, padding: "10px 12px",
              marginBottom: 8, fontSize: 12, color: "var(--muted)", textAlign: "center",
              cursor: dis ? "default" : "text", outline: "none",
            }}>
            📋 여기에 <b style={{ color: "var(--text-primary)" }}>Ctrl+V</b> 로 그림을 붙여넣으세요
            <div style={{ fontSize: 11, marginTop: 2 }}>
              붙여넣으면 바로 저장되고 shot 안에 표시됩니다 (파일 선택도 가능)
            </div>
          </div>
          <div style={row}>
            <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.gif,.webp" disabled={dis}
              onChange={onUpload} style={{ fontSize: 12 }} />
            {vcfg.image && <Button disabled={dis} onClick={onDeleteImage}>삭제</Button>}
          </div>
          {shapes && (
            <div style={{ fontSize: 11, marginBottom: 6,
                          color: shapes.count ? "var(--ok)" : "var(--warn)" }}>
              {shapes.count
                ? `▢ 그림에서 사각형 ${shapes.count}개 인식`
                  + (shapes.source === "grid" ? " (맞닿은 die 를 경계선으로 복원)" : "")
                  + " — shot 확대에서 [격자] 로 보면 이 사각형이 그려지고, Mapfile 체크는 이걸로 die 겹침을 판정합니다"
                : "▢ 사각형을 찾지 못했습니다 — 테두리가 뚜렷한 사각형(맞닿아 있어도 됩니다) 그림이어야 격자로 볼 수 있습니다."
                  + " 그림 없이 chip 크기로 그리려면 표시 방식을 '개발 격자' 로 두세요."}
            </div>
          )}
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            그림은 {info?.teg_dir || "teg_location/"} 에 저장되고 각 shot 안에 표시됩니다 (제품당 1장 — 새로 올리면 이전 그림은 지워집니다).
            shot 확대에서 <b>그림</b>과 <b>격자</b>(인식한 사각형)를 전환해 볼 수 있습니다. 색은 판정에 쓰지 않습니다.
          </div>
        </div>
      )}
      {vcfg.mode === "dev_grid" && (
        <div style={{ marginBottom: 8 }}>
          {shapes && (() => {
            const n = (shapes.dev_cells || []).length;
            const al = shapes.align || {};
            return (
              <div style={{ fontSize: 11, marginBottom: 6, color: n ? "var(--ok)" : "var(--warn)" }}>
                {n
                  ? `▢ die ${n}개 — MAIN TEG ${al.anchors || n}개 좌하단에 MAIN chip 크기 파일의 chip 크기로 그립니다`
                    + " — Mapfile 체크에서 TEG 가 die 안에 있는지 판정합니다"
                  : "▢ die 없음 — "
                    + (al.anchors
                      ? `MAIN TEG ${al.anchors}개는 있지만 MAIN chip 크기 파일에 이 제품 크기가 없습니다`
                      : "MAIN 으로 이름 붙은 TEG 좌표가 없습니다 (Teg_location 또는 Mapfile 체크 역반영)")
                    + ". die 겹침 판정은 건너뜁니다."}
              </div>
            );
          })()}
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            개발 격자는 그림 없이 <b>MAIN TEG 좌표(die 좌하단) + MAIN chip 크기 파일(chip 크기, µm)</b>로 die 를 그립니다.
            크기표에 없는 제품은 사각형을 그리지 않습니다.
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
   그림/칩 격자는 shot 확대 뷰에서만 표시. shot 색: 선택 TEG 전체가 제품별 최외곽
   안에 온전히 들어오면 초록, 하나라도 라인에 걸치거나 밖이면 빨강.
   data.shots 에 synthetic:true 가 섞여 들어오면(full shot 격자) 점선으로 그린다. ── */
export function WaferMap({ data, selectedTegs, tegColor, selectedShot, onShotClick, nearestShot, shotValues=null, valueColor=null, valueLabel="value", light=false, hideUnmeasured=false, fullChipDies=null }) {
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
        // Cartesian +Y is up; SVG +Y is down.
        toY: (mm) => SIZE / 2 - mm * s,
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

  // shot 별 제품 최외곽 판정 — 선택 TEG 사각형의 네 꼭짓점 중 하나라도
  // edge 원 밖이면 "걸림"(빨강), 전부 안이면 초록. TEG 미선택 시엔 shot 영역
  // 자체로 판정: 전부 안=연파랑, 걸치거나 밖=연노랑.
  const edgeMm = mmMode ? (geo.wafer_edge_mm || 0) : 0;
  const fullChipMode = Array.isArray(fullChipDies);
  const shotEdgeCrossed = (s0) => {
    if (!edgeMm || !tegList.length) return null;
    for (const t of tegList) {
      const box = tegBox(t) || { w: 0, h: 0 };
      const anchor = waferTegCartesianPosition(s0, t);
      const x0 = anchor.x, y0 = anchor.y;
      const x1 = x0 + box.w, y1 = y0 + box.h;
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
      style={{ background: light ? "#ffffff" : "var(--bg-card)", border: `1px solid ${light ? "#cbd5e1" : "var(--line)"}`, borderRadius: 6, maxWidth:"100%", height:"auto" }}>
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
      {fullChipMode && fullChipDies.map(die => (
        <rect key={die.key} x={toX(die.x)} y={toY(die.y + die.h)}
          width={Math.max(0.6, die.w * mmScale)} height={Math.max(0.6, die.h * mmScale)}
          fill="rgba(47,158,99,0.16)" stroke="#2f9e63" strokeWidth="0.55"
          pointerEvents="none">
          <title>{`die · shot (${die.shotX}, ${die.shotY})\nwafer 좌하단 (${fmt(die.x, 3)}, ${fmt(die.y, 3)}) mm\n최외곽 ${fmt(edgeMm, 0)}mm 안`}</title>
        </rect>
      ))}
      {data.shots.map(s0 => {
        const cx = mmMode ? toX(s0.mm_x) : toX(s0.x);
        // chip_y_adj grows downward, so negate it before Cartesian rendering.
        const cy = mmMode ? toY(-s0.mm_y) : toY(s0.y);
        const key = `${s0.x},${s0.y}`;
        const measured = shotValues instanceof Map ? shotValues.get(key) : null;
        if (hideUnmeasured && !measured) return null;
        const isSel = selectedShot && selectedShot.x === s0.x && selectedShot.y === s0.y;
        const crossed = mmMode ? shotEdgeCrossed(s0) : null;
        const selfCrossed = mmMode && crossed === null ? shotSelfCrossed(s0) : null;
        // full shot 격자로 만들어 낸 자리 — TEG 판정색(초록/빨강)은 그대로 쓰되,
        // TEG 미선택 시 최외곽에 걸치는 건 연노랑으로 칠하지 않는다 (덮개 격자는
        // 거의 전부가 걸리는 자리라 노랑으로 채우면 화면이 노랗기만 하다).
        const syn = !!s0.synthetic;
        const passFill = crossed !== null
          ? (crossed ? "rgba(224,82,82,0.30)" : "rgba(47,158,99,0.22)")
          : selfCrossed !== null
            ? (selfCrossed
              ? (syn ? "rgba(128,128,128,0.05)" : "rgba(250,204,21,0.28)")
              : "rgba(96,165,250,0.20)")
            : "rgba(128,128,128,0.06)";
        const passStroke = crossed !== null
          ? (crossed ? "#e05252" : "#2f9e63")
          : selfCrossed !== null
            ? (selfCrossed ? (syn ? "var(--line)" : "#c78a1e") : "#3e7bd6")
            : "var(--line)";
        const valueLine = measured ? `\n${valueLabel}: ${measured.value}${measured.n!=null?` (n=${measured.n})`:""}` : "\n측정값 없음";
        const title = (mmMode
          ? `shot (${s0.x}, ${s0.y})\nwafer 위치: (${fmt(s0.mm_x)}, ${fmt(-s0.mm_y)}) mm\nshot 내부 ebeam 원점: (0, 0)\nradius: ${fmt(s0.radius)} mm`
            + (crossed !== null
              ? (crossed ? `\n⚠ TEG 가 최외곽 ${fmt(edgeMm, 0)}mm 라인에 걸림` : `\n✓ TEG 전체가 최외곽 ${fmt(edgeMm, 0)}mm 안`)
              : selfCrossed !== null
                ? (selfCrossed ? `\nshot 영역이 최외곽 ${fmt(edgeMm, 0)}mm 에 걸치거나 밖` : `\nshot 전체가 최외곽 ${fmt(edgeMm, 0)}mm 안`)
                : "")
            + (syn ? "\n※ layout 파일에 없는 자리 — full shot 격자로 채운 shot" : "")
          : `shot (${s0.x}, ${s0.y})`) + (shotValues ? valueLine : "");
        const measuredFill = measured && valueColor ? valueColor(measured.value) : null;
        return (
          <g key={key} onClick={() => onShotClick(s0)} style={{ cursor: "pointer" }}>
            <rect x={cx - shotW / 2} y={cy - shotH / 2} width={shotW} height={shotH}
              fill={isSel ? "rgba(90,140,255,0.16)" : (fullChipMode ? "transparent" : (measuredFill || (shotValues ? "#f1f5f9" : passFill)))}
              stroke={isSel ? "#5a8cff" : (fullChipMode ? "rgba(148,163,184,0.55)" : (measuredFill ? "#334155" : passStroke))} strokeWidth={isSel ? 1.6 : (fullChipMode ? 0.35 : 0.7)}
              strokeDasharray={syn && !isSel ? "3 2" : undefined}>
              <title>{title}</title>
            </rect>
            {/* TEG 마커 — 격자/그림은 shot 확대 뷰에서만. 앵커 = 좌하단(점 기준 위로) */}
            {mmMode && tegList.map(t => {
              const box = tegBox(t);
              const { x: ax, y: ay } = waferTegCartesianPosition(s0, t);
              if (!box) {   // MAIN 인데 chip 크기가 없음 — 점만
                return <circle key={t.teg} cx={toX(ax)} cy={toY(ay)} r="1.6"
                  fill={tegColor(t.teg)} opacity="0.9" pointerEvents="none" />;
              }
              const hpx = Math.max(1.5, box.h * mmScale);
              return (
                <rect key={t.teg} x={toX(ax)} y={toY(ay) - hpx}
                  width={Math.max(1.5, box.w * mmScale)} height={hpx}
                  fill={tegColor(t.teg)} opacity={box.die ? 0.35 : 0.9}
                  stroke={box.die ? tegColor(t.teg) : "none"} strokeWidth={box.die ? 0.6 : 0}
                  pointerEvents="none" />
              );
            })}
          </g>
        );
      })}
      {/* 가장 가까운 샷 센터 = 빨간 점 — 실center에서 가장 가까운 shot 표시 */}
      {mmMode && nearestShot && (() => {
        const nx = toX(nearestShot.mm_x), ny = toY(-nearestShot.mm_y);
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
   zoom/pan/핀치 로직은 공용 ZoomPanSvg 로 통합 (TegCheck ShotView 와 공유).
   크기는 카드 폭에 맞춰 늘린다 — 고정 380 이면 넓은 화면에서 옆이 통째로 비었다. ── */
const SHOT_ZOOM_MIN = 380;
const SHOT_ZOOM_MAX = 820;
const SHOT_ZOOM_SIDE_MIN = 480;   // 이보다 작아질 바에는 좌표 패널을 아래로 내린다

/* 요소 실제 폭 추적 — shot 확대를 카드 폭에 맞추기 위한 것.
   콜백 ref 를 쓴다: 대상 div 는 data 가 온 뒤에야 마운트되므로 useRef + 빈 deps 로는
   effect 가 돌 때 아직 null 이라 영영 관측이 안 붙는다.
   ResizeObserver 가 없는 환경에서는 첫 폭만 재고 그대로 둔다. */
function useBoxWidth() {
  const [node, setNode] = useState(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return;
    const measure = () => setWidth(node.getBoundingClientRect().width);
    measure();
    // 창 크기 변경은 별도로도 잡는다 — ResizeObserver 가 없거나 통지가 늦는 브라우저가 있다.
    window.addEventListener("resize", measure);
    let ro = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(entries => {
        const w = entries[0]?.contentRect?.width;
        if (w) setWidth(w);
      });
      ro.observe(node);
    }
    return () => {
      window.removeEventListener("resize", measure);
      if (ro) ro.disconnect();
    };
  }, [node]);
  return [setNode, width];
}

export function ShotZoom({ data, selectedTegs, tegColor, imgUrl, dieCells, showPicture, size }) {
  const SIZE = size || SHOT_ZOOM_MIN;
  const geo = data.geometry;
  const display = data.display || { mode: "none" };
  if (geo.fit !== "radius") {
    return <EmptyState icon="⚠" title="Chip_Radius fit 불가" hint="shot 크기(mm)를 알 수 없어 확대 뷰를 그릴 수 없습니다" />;
  }
  const W = geo.shot_w_mm, H = geo.shot_h_mm;
  const pad = 0.12;
  const s = SIZE / Math.max(W * (1 + pad * 2), H * (1 + pad * 2));
  const w = W * s, h = H * s;
  const ox = (SIZE - w) / 2, oy = (SIZE - h) / 2;
  const toX = (mm) => ox + (mm + W / 2) * s;
  // SVG 는 y 가 아래로 증가 — ebeam +y(위)를 뒤집어 shot 센터가 정확히 (0,0)이 되게 한다.
  const toY = (mm) => oy + (H / 2 - mm) * s;
  const tegList = data.tegs.filter(t => selectedTegs.has(t.teg));
  const cellsInfo = display.mode === "grid" ? chipCells(display, W, H) : null;
  // die 셀과 그림 표시 여부는 호출측이 정한다 (그림 모드는 그림/격자 전환).
  const cells = dieCells || [];
  const showImage = !!(showPicture && imgUrl);
  // 개발 격자만 좌하단 코너(└)를 찍는다 — 그 점이 MAIN TEG 좌표다.
  const devGrid = display.mode === "dev_grid";

  return (
    <ZoomPanSvg size={SIZE}>
      {(zoom) => (
        <>
          {showImage && (
            <image href={imgUrl} x={ox} y={oy} width={w} height={h}
              preserveAspectRatio="none" opacity="0.9" />
          )}
          <rect x={ox} y={oy} width={w} height={h} fill={showImage ? "none" : "rgba(128,128,128,0.05)"}
            stroke="var(--muted)" strokeWidth={1 / zoom} />
          {/* shot 센터 십자 */}
          <line x1={toX(0) - 5 / zoom} y1={toY(0)} x2={toX(0) + 5 / zoom} y2={toY(0)} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          <line x1={toX(0)} y1={toY(0) - 5 / zoom} x2={toX(0)} y2={toY(0) + 5 / zoom} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          {/* die 사각형 — Mapfile 체크가 die 판정에 쓰는 바로 그 영역. */}
          {cells.map((c, i) => (
            <g key={`die${i}`}>
              <rect x={toX(c.x)} y={toY(c.y) - c.h * s} width={c.w * s} height={c.h * s}
                fill="rgba(47,158,99,0.06)" stroke="#2f9e63" strokeWidth={1.2 / zoom}
                opacity="0.9" />
              {devGrid && (
                <path d={`M${toX(c.x)} ${toY(c.y) - 7 / zoom} L${toX(c.x)} ${toY(c.y)} L${toX(c.x) + 7 / zoom} ${toY(c.y)}`}
                  fill="none" stroke="#2f9e63" strokeWidth={1.6 / zoom} opacity="0.95" />
              )}
            </g>
          ))}
          {/* 칩 격자 — c.x/c.y = 칩 좌하단(mm), y 축 반전이라 top = toY(c.y) - 높이 */}
          {cellsInfo && cellsInfo.cells.map(c => (
            <g key={c.i}>
              <rect x={toX(c.x)} y={toY(c.y) - c.h * s} width={c.w * s} height={c.h * s}
                fill="rgba(47,158,99,0.08)" stroke="#2f9e63" strokeWidth={0.8 / zoom} opacity="0.85" />
              <text x={toX(c.x + c.w / 2)} y={toY(c.y + c.h / 2)} fontSize={10 / zoom} fill="#2f9e63"
                textAnchor="middle" dominantBaseline="middle" opacity="0.85">{c.i + 1}</text>
            </g>
          ))}
          {/* TEG 직사각형 — MAIN 계열은 die 급 블록이라 MAIN chip 크기 파일의 chip
              크기로 그린다(좌하단 = TEG 좌표). 크기표에 없으면 점만 찍는다. */}
          {tegList.map(t => {
            const box = tegBox(t);
            const x = toX(t.ebeam_x), yBottom = toY(t.ebeam_y);
            const wpx = box ? box.w * s : 0, hpx = box ? box.h * s : 0;
            const labelX = box ? x + wpx + 4 / zoom : x + 5 / zoom;
            const labelY = box ? yBottom - hpx / 2 : yBottom;
            return (
              <g key={t.teg}>
                {box && (
                  <rect x={x} y={yBottom - hpx} width={wpx} height={hpx}
                    fill={tegColor(t.teg)} opacity={box.die ? 0.18 : 0.75}
                    stroke={box.die ? tegColor(t.teg) : "none"}
                    strokeWidth={box.die ? 1.2 / zoom : 0} />
                )}
                <circle cx={x} cy={yBottom} r={2.4 / zoom} fill={tegColor(t.teg)} stroke="var(--bg-card)" strokeWidth={0.8 / zoom} />
                <text x={labelX} y={labelY} fontSize={11 / zoom} fill="var(--text-primary)" dominantBaseline="middle">
                  {t.teg}{box?.die ? ` (die ${fmt(box.w)}×${fmt(box.h)} mm)` : ""}
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
        </>
      )}
    </ZoomPanSvg>
  );
}

/* ── shot 확대 우측 ebeam 좌표 패널 — 선택 TEG 가 5개 미만일 때만 표시.
   5개 이상이면 공간이 부족하므로 기존대로 배치도만 표시.
   표시 조건·폭은 shot 확대 크기 계산과 공유한다 (한쪽만 바뀌면 배치가 어긋난다). ── */
const COORD_PANEL_MAX = 5;   // 이 개수 이상이면 패널을 접는다
const COORD_PANEL_W = 230;   // 제목과 (ebeam_x,ebeam_y)가 한 줄에 보이는 폭

function coordPanelCount(data, selectedTegs) {
  return (data?.tegs || []).filter(t => selectedTegs.has(t.teg)).length;
}

function TegCoordInfo({ data, selectedTegs, tegColor }) {
  const tegList = (data?.tegs || []).filter(t => selectedTegs.has(t.teg));
  if (!tegList.length || tegList.length >= COORD_PANEL_MAX) return null;
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "8px 10px",
                  fontSize: 12, lineHeight: 1.7, width: COORD_PANEL_W, flexShrink: 0,
                  alignSelf: "flex-start" }}>
      <div style={{ fontWeight: 700, color: "var(--muted)", whiteSpace: "nowrap" }}>
        TEG 좌표 — (ebeam_x,ebeam_y) µm
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
        shot 좌하단 좌표 기준
      </div>
      {tegList.map(t => (
        <div key={t.teg} style={{ marginBottom: 4 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ color: tegColor(t.teg), fontWeight: 700 }}>■</span>
            <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis",
                           whiteSpace: "nowrap", maxWidth: 160 }} title={t.teg}>{t.teg}</span>
          </div>
          <div style={{ color: "var(--muted)", marginLeft: 17 }}>
            ({fmt(Number(t.ebeam_x) * 1000, 3)},{fmt(Number(t.ebeam_y) * 1000, 3)}) µm
          </div>
          <div style={{ color: "var(--muted)", marginLeft: 17 }}>
            <b style={{ color: isVertical(t) ? "var(--warn)" : "var(--text-primary)" }}>
              {directionLabel(t)}
            </b>
            {" · 사이즈 "}{fmt(Number(t.teg_w) * 1000, 1)} × {fmt(Number(t.teg_h) * 1000, 1)}
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
        const p = waferTegCartesianPosition(s0, t);
        radii[t.teg] = Math.hypot(p.x, p.y);
      }
      return { x: s0.x, y: s0.y, radii, synthetic: !!s0.synthetic };
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
            {rows.some(r => r.synthetic) && (
              <span title="full shot 격자로 채운 자리 — layout 파일에는 없는 shot 입니다">
                · <b>＋</b> 표시 {rows.filter(r => r.synthetic).length}개 = full shot 격자
              </span>
            )}
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
                  <tr key={i} style={r.synthetic ? { opacity: 0.75 } : undefined}
                    title={r.synthetic ? "full shot 격자로 채운 자리 — layout 파일에 없는 shot" : undefined}>
                    <td style={cell}>{r.synthetic ? <span style={{ color: "var(--muted)" }}>＋ </span> : null}{r.x}</td>
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
      toast.ok("Mapfile 검증 대상 저장됨");
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
        Mapfile 검증 대상 TEG
        <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 400, color: "var(--muted)" }}>
          {sel.size}개 {data ? (data.source === "config" ? "· 지정됨" : "· 기본") : ""}
        </span>
      </button>
      {open && (
        <div style={{ padding: "0 9px 9px" }}>
          <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6, marginBottom: 6 }}>
            체크한 TEG 가 "Mapfile 검증" 대상입니다. 기본값 = 이름이 H_/V_ 로 시작하는 것 전부.
            {!canEdit && <span style={{ color: "var(--warn)" }}> · admin / teg 페이지 관리자만 변경·저장할 수 있습니다.</span>}
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
              <LinkBtn onClick={checkDefault} disabled={saving} style={{ fontSize: 11 }}>H_/V_ 선택</LinkBtn>
              <LinkBtn tone="muted" onClick={resetDefault} disabled={saving} style={{ fontSize: 11 }}>기본값으로 초기화</LinkBtn>
              {dirty && <span style={{ fontSize: 11, color: "var(--warn)" }}>저장 필요</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const FULL_CHIP_MAX = 50000;

/* shot 내부 die 셀을 wafer 전체 격자에 반복하고, 네 꼭짓점이 모두 최외곽선 안인
   die만 남긴다. grid 셀과 dev_grid MAIN 셀이 같은 좌하단 좌표 규약을 쓰므로
   이 함수 하나로 full chip을 그릴 수 있다. */
function buildFullChipDies(data, localCells) {
  const geo = data?.geometry;
  const shots = data?.shots || [];
  const cells = localCells || [];
  const edge = Number(geo?.wafer_edge_mm) || 0;
  if (geo?.fit !== "radius" || !shots.length || !cells.length || !(edge > 0)) {
    return { dies: [], overflow: false };
  }
  const dies = [];
  for (const shot of shots) {
    for (let i = 0; i < cells.length; i++) {
      const cell = cells[i];
      const w = Number(cell.w) || 0, h = Number(cell.h) || 0;
      if (!(w > 0) || !(h > 0)) continue;
      const x = Number(shot.mm_x) + Number(cell.x || 0);
      const y = -Number(shot.mm_y) + Number(cell.y || 0);
      const maxD = Math.max(
        Math.hypot(x, y), Math.hypot(x + w, y),
        Math.hypot(x, y + h), Math.hypot(x + w, y + h),
      );
      // 최외곽선에 정확히 닿는 die도 제외한다. 공정 유효 영역은 경계 포함(<=)이
      // 아니라 네 꼭짓점이 모두 선 안쪽(<)에 여유를 두고 들어온 경우만 센다.
      if (maxD >= edge - 1e-9) continue;
      dies.push({
        key: `${gridKey(shot.x, shot.y)}:${i}`,
        x, y, w, h, shotX: shot.x, shotY: shot.y,
      });
      if (dies.length >= FULL_CHIP_MAX) return { dies, overflow: true };
    }
  }
  return { dies, overflow: false };
}

function LegacyReferenceFiles({ canEdit, onSaved }) {
  const [files, setFiles] = useState([]);
  const [kind, setKind] = useState("teg_location");
  const [table, setTable] = useState(null);
  const [page, setPage] = useState(0);
  const [saving, setSaving] = useState(false);
  const pageSize = 100;
  const loadFiles = useCallback(async () => {
    try { const r = await sf(`${API}/reference-files`); setFiles(r.files || []); }
    catch (e) { toast.error(String(e.message || e)); }
  }, []);
  const loadTable = useCallback(async () => {
    try {
      const r = await sf(`${API}/reference-file?kind=${encodeURIComponent(kind)}`);
      setTable({ ...r, rows: (r.rows || []).map(row => [...row]) }); setPage(0);
    } catch (e) { setTable(null); toast.error(String(e.message || e)); }
  }, [kind]);
  useEffect(() => { loadFiles(); }, [loadFiles]);
  useEffect(() => { loadTable(); }, [loadTable]);
  const updateCell = (ri, ci, value) => setTable(prev => ({ ...prev,
    rows: prev.rows.map((row, i) => i === ri ? row.map((v, j) => j === ci ? value : v) : row) }));
  const save = async () => {
    setSaving(true);
    try {
      await putJson(`${API}/reference-file`, { kind, columns: table.columns, rows: table.rows,
        note: "TEG 위치 조회 기준 파일 소탭 편집", expected_modified_ns: table.source_modified_ns });
      toast.ok(`${table.name} 저장됨 · ${table.rows.length}행`); await loadTable(); await loadFiles();
      onSaved && onSaved();
    } catch (e) { toast.error(String(e.message || e)); }
    finally { setSaving(false); }
  };
  const start = page * pageSize, shown = (table?.rows || []).slice(start, start + pageSize);
  return <Card title="TEG 기준 파일"
    right={<div style={{ display: "flex", gap: 6 }}>
      <Button onClick={loadTable}>다시 읽기</Button>
      {canEdit && <Button variant="primary" disabled={!table?.editable || saving} onClick={save}>{saving ? "저장 중…" : "저장"}</Button>}
    </div>}>
    <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
      {(files.length ? files : [{ kind: "chip_radius" }, { kind: "main_chip_info" }, { kind: "teg_location" }]).map(f =>
        <Button key={f.kind} variant={kind === f.kind ? "primary" : "subtle"} onClick={() => setKind(f.kind)}>
          {f.kind === "chip_radius" ? "Chip_Radius" : f.kind === "main_chip_info" ? "Main_chip_info" : "Teg_location"}
        </Button>)}
      <span style={{ fontSize: 11, color: "var(--muted)", alignSelf: "center" }}>
        파일탐색기 권한과 분리된 TEG 전용 allowlist · 열람은 TEG 사용자, 저장은 TEG 관리자
      </span>
    </div>
    {!table ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : <>
      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>{table.path} · {table.rows.length.toLocaleString()}행</div>
      <div style={{ overflow: "auto", maxHeight: 560, border: "1px solid var(--line)", borderRadius: 6 }}>
        <table style={{ borderCollapse: "collapse", minWidth: "100%", fontSize: 12 }}><thead><tr>
          <th style={{ position: "sticky", top: 0, background: "var(--bg-card)", padding: 6 }}>#</th>
          {table.columns.map(c => <th key={c} style={{ position: "sticky", top: 0, background: "var(--bg-card)", padding: 6, whiteSpace: "nowrap" }}>{c}</th>)}
          {canEdit && <th style={{ position: "sticky", top: 0, background: "var(--bg-card)" }}>삭제</th>}
        </tr></thead><tbody>{shown.map((row, local) => { const ri = start + local; return <tr key={ri}>
          <td style={{ padding: 4, color: "var(--muted)" }}>{ri + 1}</td>
          {table.columns.map((_, ci) => <td key={ci} style={{ padding: 2 }}><input style={{ ...inputStyle, minWidth: 110, width: "100%" }}
            disabled={!canEdit} value={row[ci] ?? ""} onChange={e => updateCell(ri, ci, e.target.value)} /></td>)}
          {canEdit && <td><Button onClick={() => setTable(prev => ({ ...prev, rows: prev.rows.filter((_, i) => i !== ri) }))}>삭제</Button></td>}
        </tr>; })}</tbody></table>
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8 }}>
        <Button disabled={page <= 0} onClick={() => setPage(p => p - 1)}>이전</Button>
        <span>{page + 1} / {Math.max(1, Math.ceil(table.rows.length / pageSize))}</span>
        <Button disabled={start + pageSize >= table.rows.length} onClick={() => setPage(p => p + 1)}>다음</Button>
        {canEdit && <Button onClick={() => { setTable(prev => ({ ...prev, rows: [...prev.rows, prev.columns.map(() => "")] })); setPage(Math.floor(table.rows.length / pageSize)); }}>+ 행 추가</Button>}
      </div>
    </>}
  </Card>;
}

function ReferenceFiles({ user, canEdit, onSaved }) {
  const [files, setFiles] = useState(null);
  const [error, setError] = useState("");
  const loadFiles = useCallback(async () => {
    setError("");
    try {
      const result = await sf(`${API}/reference-files`);
      setFiles((result.files || []).filter(file => file.exists !== false));
    } catch (e) {
      setFiles([]);
      setError(String(e.message || e));
    }
  }, []);
  useEffect(() => { loadFiles(); }, [loadFiles]);

  if (files === null) return <div style={{ padding: 20, color: "var(--muted)" }}>기준파일을 불러오는 중…</div>;
  if (error) return <EmptyState icon="⚠" title="기준파일을 불러오지 못했습니다" hint={error} />;
  return <My_FileBrowser
    user={user}
    embeddedBaseFiles={files}
    embeddedTitle="TEG 기준파일"
    embeddedCanEdit={canEdit}
    onBaseFileChanged={() => { loadFiles(); if (onSaved) onSaved(); }}
  />;
}


const PRODUCT_NODE_ADMIN_COLUMNS = ["current_product", "product_name", "node_path"];
const NODE_ACCESS_ADMIN_COLUMNS = ["root_node", "users", "departments"];

function splitAccessValues(value) {
  return [...new Set(String(value || "").split(",").map(item => item.trim()).filter(Boolean))];
}

function ProductAccessAdmin({ onSaved }) {
  const [productRows, setProductRows] = useState(() => normalizeSpreadsheetRows([], PRODUCT_NODE_ADMIN_COLUMNS));
  const [originalProducts, setOriginalProducts] = useState({});
  const [accessRows, setAccessRows] = useState(() => normalizeSpreadsheetRows([], NODE_ACCESS_ADMIN_COLUMNS));
  const [knownUsers, setKnownUsers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setBusy(true); setError("");
    try {
      const result = await sf(`${API}/product-access`);
      setKnownUsers(result.users || []);
      setOriginalProducts(Object.fromEntries((result.products || []).map(item => [item.vehicle, {
        product_name: item.vehicle, node_path: item.node_path === "미분류" ? "" : item.node_path,
      }])));
      setProductRows(normalizeSpreadsheetRows((result.products || []).map(item => ({
        current_product: item.vehicle, product_name: item.vehicle,
        node_path: item.node_path === "미분류" ? "" : item.node_path,
      })), PRODUCT_NODE_ADMIN_COLUMNS));
      setAccessRows(normalizeSpreadsheetRows(Object.entries(result.node_access || {}).map(([root, rule]) => ({
        root_node: root,
        users: (rule.users || []).join(", "),
        departments: (rule.departments || []).join(", "),
      })), NODE_ACCESS_ADMIN_COLUMNS));
    } catch (e) { setError(String(e.message || e)); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const save = async () => {
    setBusy(true); setError("");
    try {
      const identityRows = productRows.filter(row => String(row.current_product || "").trim());
      for (const row of identityRows) {
        const currentProduct = String(row.current_product).trim();
        const productName = String(row.product_name || "").trim();
        const nodePath = String(row.node_path || "").trim();
        const original = originalProducts[currentProduct] || {};
        if (productName !== original.product_name || nodePath !== original.node_path) {
          await putJson(`${API}/products/${encodeURIComponent(currentProduct)}/identity`, {
            vehicle: productName, node_path: nodePath,
          });
        }
      }
      const node_access = Object.fromEntries(accessRows
        .filter(row => String(row.root_node || "").trim())
        .map(row => [String(row.root_node).trim(), {
          users: splitAccessValues(row.users), departments: splitAccessValues(row.departments),
        }]));
      await putJson(`${API}/product-access`, { product_nodes: {}, node_access });
      toast.ok("제품명·분류와 접근 권한을 저장했습니다");
      await load();
      if (onSaved) await onSaved();
    } catch (e) { setError(String(e.message || e)); setBusy(false); }
  };
  return <div style={{ display: "grid", gap: 12 }}>
    <Card title="제품 노드 · 관리자" right={<Pill tone="warn">SSO 부서 연동 준비</Pill>}>
      <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6, marginBottom: 9 }}>
        기존 제품명은 변경 대상을 찾는 키이므로 그대로 두고, 변경 제품명과 제품 분류를 수정합니다.
        예: <b>2나노 / 2나노A</b>로 저장하면 선택 화면에는 <b>2나노 / 2나노A / 제품명</b>으로 표시됩니다.
        제품명 변경은 Chip_Radius·Teg_location·Main_chip_info·제품 설정에도 함께 반영됩니다.
      </div>
      <SpreadsheetPasteGrid columns={PRODUCT_NODE_ADMIN_COLUMNS} rows={productRows} onChange={setProductRows}
        columnLabels={{ current_product: "기존 제품명", product_name: "변경 제품명", node_path: "제품 분류" }}
        ariaLabel="제품별 이름과 분류" minRows={10} maxRows={1000} maxHeight={365} />
    </Card>
    <Card title="최상위 노드 접근 권한 · 관리자">
      <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6, marginBottom: 9 }}>
        규칙 행이 없는 노드는 모든 TEG 사용자가 볼 수 있습니다. 행을 추가한 노드는 허용 사용자 또는 허용 부서 중 하나가 일치해야 보입니다.
        값은 쉼표로 구분하며, 비어 있는 규칙 행은 관리자 외 전원 차단입니다. SSO 연결 후 department/dept/org claim이 허용 부서와 자동 비교됩니다.
      </div>
      <SpreadsheetPasteGrid columns={NODE_ACCESS_ADMIN_COLUMNS} rows={accessRows} onChange={setAccessRows}
        ariaLabel="최상위 노드 접근 권한" minRows={10} maxRows={500} maxHeight={365} />
      <div style={{ marginTop: 7, fontSize: 11, color: "var(--muted)" }}>현재 승인 사용자: {knownUsers.join(", ") || "없음"}</div>
    </Card>
    {error && <div style={{ color: "var(--danger)", fontSize: 12 }}>{error}</div>}
    <div style={{ display: "flex", justifyContent: "flex-end", gap: 7 }}>
      <Button onClick={load} disabled={busy}>다시 불러오기</Button>
      <Button variant="primary" onClick={save} disabled={busy}>{busy ? "저장 중…" : "제품·권한 저장"}</Button>
    </div>
  </div>;
}


function InlineShotPicker({ data, selected, onToggle, tableName="" }) {
  if (!data?.shots?.length) return <EmptyState icon="⌖" title="제품 map이 없습니다" hint="상단에서 제품을 선택해 주세요" />;
  const geo = data.geometry || {}, mmMode = geo.fit === "radius";
  const size = 620, pad = 30, shots = buildFullShots(data);
  let minX, maxX, minY, maxY, w, h;
  if (mmMode) {
    const R = Number(geo.wafer_radius_mm) || 150;
    minX = -R; maxX = R; minY = -R; maxY = R; w = Math.abs(geo.shot_w_mm); h = Math.abs(geo.shot_h_mm);
  } else {
    const xs = shots.map(s => Number(s.x)), ys = shots.map(s => Number(s.y));
    minX = Math.min(...xs) - 1; maxX = Math.max(...xs) + 1; minY = Math.min(...ys) - 1; maxY = Math.max(...ys) + 1;
    w = Math.abs(geo.pitch_x || 1); h = Math.abs(geo.pitch_y || 1);
  }
  const scale = Math.min((size - 2 * pad) / Math.max(maxX - minX, 1), (size - 2 * pad) / Math.max(maxY - minY, 1));
  const pos = s => mmMode
    ? { x: size / 2 + s.mm_x * scale, y: size / 2 + s.mm_y * scale }
    : { x: pad + (Number(s.x) - minX) * scale, y: pad + (Number(s.y) - minY) * scale };
  const shotPxW = Math.max(w * scale, 2), shotPxH = Math.max(h * scale, 2);
  const namedCount = [...selected.values()].filter(v => String(v || "").trim()).length;
  return <div style={{ minWidth: 0 }}>
    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 7, fontSize: 12 }}>
      <b>WF MAP 미리보기</b>
      {tableName && <Pill tone="neutral">{tableName}</Pill>}
      <span style={{ color: "#3e7bd6", fontWeight: 700 }}>■ 설정 위치 {selected.size}</span>
      {namedCount !== selected.size && <span style={{ color: "var(--warn)" }}>■ subitem_id 미입력 {selected.size - namedCount}</span>}
      {mmMode && <><span style={{ color: "#2f9e63", fontWeight: 700 }}>■ {fmt(geo.wafer_edge_mm, 1)}mm 이내</span>
        <span style={{ color: "#e05252", fontWeight: 700 }}>■ {fmt(geo.wafer_edge_mm, 1)}mm 경계/외곽</span></>}
      <span style={{ color: "var(--muted)" }}>shot 클릭 → 선택/해제</span>
    </div>
    <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${tableName || "Inline map"} WF MAP 위치 미리보기`}
      style={{ width: "100%", maxWidth: size, border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg-primary)" }}>
      {mmMode && <>
        <circle cx={size / 2} cy={size / 2} r={(geo.wafer_radius_mm || 150) * scale} fill="none" stroke="var(--muted)" />
        {Number(geo.wafer_edge_mm) > 0 && <circle cx={size / 2} cy={size / 2} r={Number(geo.wafer_edge_mm) * scale}
          fill="none" stroke="#c78a1e" strokeWidth=".8" strokeDasharray="6 4" />}
        <line x1={size / 2 - 5} y1={size / 2} x2={size / 2 + 5} y2={size / 2} stroke="var(--muted)" />
        <line x1={size / 2} y1={size / 2 - 5} x2={size / 2} y2={size / 2 + 5} stroke="var(--muted)" />
      </>}
      {shots.map(s => {
        const key = gridKey(Number(s.x), Number(s.y)), p = pos(s), on = selected.has(key);
        const name = String(selected.get(key) || "").trim(), named = !!name;
        const insideEdge = !mmMode || shotInsideWaferEdge(s, geo);
        const clipped = name.length > 20 ? `${name.slice(0, 19)}…` : name;
        const rows = clipped.length > 11 ? [clipped.slice(0, 11), clipped.slice(11)] : [clipped];
        const labelSize = Math.max(8, Math.min(12, shotPxH / (rows.length + 0.5), shotPxW / Math.max(5, Math.min(11, clipped.length || 5)) * 1.55));
        return <g key={key} onClick={() => onToggle(s)} style={{ cursor: "pointer" }}>
          <rect x={p.x - shotPxW / 2} y={p.y - shotPxH / 2} width={shotPxW} height={shotPxH} rx="1.5"
            fill={on ? (named ? (insideEdge ? "rgba(47,158,99,.50)" : "rgba(224,82,82,.48)") : "rgba(199,138,30,.35)")
              : (insideEdge ? "rgba(47,158,99,.10)" : "rgba(224,82,82,.11)")}
            stroke={on ? (named ? (insideEdge ? "#2f9e63" : "#e05252") : "#c78a1e") : (insideEdge ? "#2f9e63" : "#e05252")}
            strokeDasharray={s.synthetic ? "4 2" : undefined} strokeWidth={on ? 2.2 : .7} />
          {on && <>
            <circle cx={p.x} cy={p.y - shotPxH / 2 + 4} r="2.6" fill={named ? "#3e7bd6" : "#c78a1e"} pointerEvents="none" />
            <text x={p.x} y={p.y - ((rows.length - 1) * labelSize) / 2} textAnchor="middle" dominantBaseline="middle"
              fontSize={labelSize} fontWeight="800" fill="var(--text-primary)" stroke="var(--bg-primary)" strokeWidth="3"
              paintOrder="stroke" strokeLinejoin="round" pointerEvents="none">
              {(named ? rows : ["subitem_id"]).map((line, i) => <tspan key={i} x={p.x}
                dy={i === 0 ? 0 : labelSize * 1.05}>{line}</tspan>)}
            </text>
          </>}
          <title>{name ? `${name}\n` : ""}shot (${s.x}, ${s.y}) · {insideEdge ? `${fmt(geo.wafer_edge_mm, 1)}mm 이내` : `${fmt(geo.wafer_edge_mm, 1)}mm 경계/외곽`}{s.synthetic ? " · full shot" : ""}{on && !named ? "\nsubitem_id를 입력해 주세요" : ""}</title>
        </g>;
      })}
    </svg>
  </div>;
}

/* 관리자 전용 Inline ↔ ET 좌표 매칭 기준 TABLE 편집기. */
const INLINE_SHOT_COLUMNS = ["shot_x", "shot_y", "subitem_id"];

function formatInlineUpdatedAt(value) {
  if (!value) return "변경 시각 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ko-KR", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function InlineMapSetting({ data, vehicle, onVehicleChange }) {
  const [tables, setTables] = useState([]), [tableName, setTableName] = useState("");
  const [comment, setComment] = useState("");
  const [selected, setSelected] = useState(new Map()), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [activeTableName, setActiveTableName] = useState(""), [dirty, setDirty] = useState(false);
  const draftVehicleRef = useRef(vehicle || "");
  const refresh = useCallback(async () => {
    try {
      const settings = await sf(`${API}/inline-map-settings`);
      setTables(settings.tables || []);
      setError("");
    }
    catch (e) { setError(String(e.message || e)); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (!vehicle) return;
    if (!draftVehicleRef.current) { draftVehicleRef.current = vehicle; return; }
    if (draftVehicleRef.current === vehicle) return;
    draftVehicleRef.current = vehicle;
    setTableName("");
    setComment("");
    setActiveTableName("");
    setSelected(new Map());
    setDirty(false);
  }, [vehicle]);
  const toggle = s => {
    const key = gridKey(Number(s.x), Number(s.y));
    setSelected(prev => { const next = new Map(prev); next.has(key) ? next.delete(key) : next.set(key, ""); return next; });
    setDirty(true);
  };
  const selectableShots = useMemo(() => buildFullShots(data), [data]);
  const chosen = useMemo(() => selectableShots.filter(s => selected.has(gridKey(Number(s.x), Number(s.y)))), [selectableShots, selected]);
  const shotRows = useMemo(() => chosen.map(s => ({
    shot_x: String(s.x), shot_y: String(s.y),
    subitem_id: String(selected.get(gridKey(Number(s.x), Number(s.y))) || ""),
  })), [chosen, selected]);
  const updateShotRows = rows => {
    setSelected(prev => {
      const next = new Map(prev);
      chosen.forEach((shot, index) => {
        const key = gridKey(Number(shot.x), Number(shot.y));
        next.set(key, String(rows[index]?.subitem_id || ""));
      });
      return next;
    });
    setDirty(true);
  };
  const load = table => {
    draftVehicleRef.current = table.vehicle;
    setTableName(table.table_name);
    setComment(table.comment || "");
    setActiveTableName(table.table_name);
    setDirty(false);
    setSelected(new Map((table.shots || []).map(s => [gridKey(s.shot_x, s.shot_y), s.subitem_id || s.name || ""])));
    if (table.vehicle !== vehicle) onVehicleChange(table.vehicle);
  };
  const save = async () => {
    const shots = chosen.map(s => ({ shot_x: Number(s.x), shot_y: Number(s.y), subitem_id: String(selected.get(gridKey(Number(s.x), Number(s.y))) || "").trim() }));
    if (!vehicle) return toast.error("제품을 먼저 선택해 주세요");
    if (!tableName.trim()) return toast.error("TABLE 이름을 입력해 주세요");
    if (!shots.length || shots.some(s => !s.subitem_id)) return toast.error("선택한 모든 shot의 subitem_id를 입력해 주세요");
    if (!comment.trim()) return toast.error("저장 comment를 입력해 주세요");
    setBusy(true);
    try {
      const cleanName = tableName.trim();
      const r = await putJson(`${API}/inline-map-settings`, { table_name: cleanName, vehicle, shots, comment: comment.trim() });
      const saved = (r.tables || []).find(t => t.table_name === cleanName);
      setTables(r.tables || []);
      setTableName(cleanName);
      setActiveTableName(cleanName);
      setSelected(new Map(((saved && saved.shots) || shots).map(s => [gridKey(s.shot_x, s.shot_y), s.subitem_id || s.name || ""])));
      setComment((saved && saved.comment) || comment.trim());
      setDirty(false);
      setError("");
      toast.ok("Inline map 설정 저장됨 · WF MAP에서 subitem_id가 shot 위치에 매칭됩니다");
    }
    catch (e) { setError(String(e.message || e)); toast.error(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const remove = async table => {
    if (!window.confirm(`${table.table_name} TABLE을 삭제할까요?`)) return;
    setBusy(true);
    try {
      const r = await sf(`${API}/inline-map-settings?table_name=${encodeURIComponent(table.table_name)}`, { method: "DELETE" });
      setTables(r.tables || []); if (tableName === table.table_name) { setTableName(""); setComment(""); setActiveTableName(""); setSelected(new Map()); setDirty(false); } toast.ok("TABLE 삭제됨");
    } catch (e) { setError(String(e.message || e)); toast.error(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const visibleTables = useMemo(() => tables.filter(table =>
    String(table.vehicle || "").trim().toLowerCase() === String(vehicle || "").trim().toLowerCase()
  ), [tables, vehicle]);
  return <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
    {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}
    <Card title={`Inline map setting — ${vehicle || "제품 미선택"}`} right={<Pill tone="warn">global admin only · DB/credential</Pill>}>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>제품 map의 shot을 선택하고 INLINE DB의 subitem_id를 입력한 뒤 TABLE 이름으로 저장합니다. WF MAP은 원천 shot 좌표 대신 이 매칭테이블을 기준으로 값을 배치합니다.</div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(360px,2fr) minmax(280px,1fr)", gap: 14, alignItems: "start" }}>
        <InlineShotPicker data={data} selected={selected} onToggle={toggle} tableName={tableName.trim()} />
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <b style={{ fontSize: 12 }}>TABLE 이름</b>
          <input value={tableName} onChange={e => { setTableName(e.target.value); setDirty(true); }} placeholder="예: INLINE_MAP_PROD_A" style={inputStyle} />
          <b style={{ fontSize: 12, marginTop: 4 }}>저장 comment</b>
          <textarea value={comment} onChange={e => { setComment(e.target.value); setDirty(true); }}
            placeholder="변경 이유나 map 용도를 입력해 주세요" maxLength={1000}
            style={{ ...inputStyle, minHeight: 64, resize: "vertical", fontFamily: "inherit" }} />
          <b style={{ fontSize: 12, marginTop: 4 }}>선택 shot ({chosen.length})</b>
          {chosen.length ? <>
            <div style={{ color: "var(--muted)", fontSize: 11, lineHeight: 1.5 }}>
              첫 번째 subitem_id 셀을 선택한 뒤 Excel 이름 열을 붙여넣으면 아래 행에 한 번에 입력됩니다.
              shot 해제는 왼쪽 map에서 다시 클릭하세요.
            </div>
            <SpreadsheetPasteGrid columns={INLINE_SHOT_COLUMNS} rows={shotRows} onChange={updateShotRows}
              readOnlyColumns={["shot_x", "shot_y"]} minRows={chosen.length} maxRows={chosen.length}
              columnLabels={{ shot_x: "shot X", shot_y: "shot Y", subitem_id: "포인트 이름 (subitem_id)" }}
              aliases={{ x: "shot_x", y: "shot_y", name: "subitem_id", point_name: "subitem_id" }}
              placeholders={{ subitem_id: "Excel 이름 열 붙여넣기" }} ariaLabel="선택 shot 포인트 이름"
              maxHeight={430} minTableWidth={380} />
          </> : <span style={{ color: "var(--muted)", fontSize: 12 }}>왼쪽 map에서 shot을 선택하세요.</span>}
          <Button variant="primary" disabled={busy} onClick={save}>{busy ? "저장 중…" : "TABLE 저장"}</Button>
          {activeTableName && <div style={{ fontSize: 11, color: dirty ? "var(--warn)" : "var(--ok)" }}>
            {dirty ? `${activeTableName} · 저장되지 않은 변경 있음` : `${activeTableName} · 저장된 shot/subitem_id 표시 중`}
          </div>}
        </div>
      </div>
    </Card>
    <Card title={`저장된 TABLE (${visibleTables.length})`}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {visibleTables.map(table => <div key={table.table_name} style={{ display: "flex", alignItems: "center", gap: 10,
          border: `1px solid ${activeTableName === table.table_name ? "#3e7bd6" : "var(--line)"}`, borderRadius: 6, padding: "7px 9px",
          background: activeTableName === table.table_name ? "rgba(62,123,214,.08)" : "transparent" }}>
          <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 3 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <b>{table.table_name}</b>
              <span style={{ color: "var(--muted)", fontSize: 12 }}>
                {table.shots?.length || 0} shots · {table.updated_by || "작성자 없음"} · {formatInlineUpdatedAt(table.updated_at)}
              </span>
            </div>
            <span style={{ color: table.comment ? "var(--text-primary)" : "var(--muted)", fontSize: 12, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {table.comment || "comment 없음"}
            </span>
          </div>
          <span style={{ marginLeft: "auto", display: "flex", gap: 5, flexShrink: 0 }}><Button onClick={() => load(table)}>불러오기</Button><Button variant="danger" disabled={busy} onClick={() => remove(table)}>삭제</Button></span>
        </div>)}
        {!visibleTables.length && <span style={{ color: "var(--muted)", fontSize: 12 }}>이 제품에 저장된 TABLE이 없습니다.</span>}
      </div>
    </Card>
  </div>;
}

const PRODUCT_INFO_COLUMNS = ["Item", "X", "Y"];
const TEG_LOCATION_COLUMNS = ["teg", "top_cell", "direction", "ebeam_X", "ebeam_Y", "teg_w", "teg_h"];

function normalizedTegDirection(value) {
  const key = String(value || "").trim().toLowerCase().replace(/\s+/g, "");
  if (["h", "horizontal"].includes(key)) return "h";
  if (["v(r)", "vr", "v_r", "vertical(r)"].includes(key)) return "v_R";
  if (["v(l)", "vl", "v_l", "vertical(l)"].includes(key)) return "v_L";
  return String(value || "").trim();
}

function ProductCreateModal({ open, onClose, onCreated }) {
  const [step, setStep] = useState(1);
  const [productRows, setProductRows] = useState(() => normalizeSpreadsheetRows([], PRODUCT_INFO_COLUMNS));
  const [preview, setPreview] = useState(null);
  const [vehicle, setVehicle] = useState("");
  const [nodePath, setNodePath] = useState("");
  const [tegRows, setTegRows] = useState(() => normalizeSpreadsheetRows([], TEG_LOCATION_COLUMNS));
  const [mainChip, setMainChip] = useState({ chip_name: "", chipsize_x: "", chipsize_y: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    setStep(1); setProductRows(normalizeSpreadsheetRows([], PRODUCT_INFO_COLUMNS)); setPreview(null); setVehicle(""); setNodePath("");
    setTegRows(normalizeSpreadsheetRows([], TEG_LOCATION_COLUMNS));
    setMainChip({ chip_name: "", chipsize_x: "", chipsize_y: "" });
    setBusy(false); setError("");
  }, [open]);
  const text = useMemo(() => spreadsheetTextFromRows(productRows, PRODUCT_INFO_COLUMNS), [productRows]);
  const tegPayload = useMemo(() => tegRows
    .filter(row => TEG_LOCATION_COLUMNS.some(column => String(row?.[column] || "").trim()))
    .map(row => ({
      teg: row.teg,
      top_cell: row.top_cell,
      direction: normalizedTegDirection(row.direction),
      ebeam_x: row.ebeam_X,
      ebeam_y: row.ebeam_Y,
      teg_w: row.teg_w,
      teg_h: row.teg_h,
    })), [tegRows]);
  const inspect = async () => {
    setBusy(true); setError("");
    try {
      const result = await postJson(`${API}/product-preview`, { text, vehicle });
      setPreview(result); setStep(3);
    } catch (e) { setError(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const create = async () => {
    setBusy(true); setError("");
    try {
      const result = await postJson(`${API}/products`, {
        text, vehicle, node_path: nodePath, tegs: tegPayload,
        main_chip: preview?.one_by_one ? mainChip : null,
      });
      toast.ok(`${result.vehicle} 제품 생성됨 · Chip_Radius ${result.shot_count} shots`);
      if (onCreated) await onCreated(result);
      onClose();
    } catch (e) { setError(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const values = preview?.values || {};
  const mainReady = !preview?.one_by_one || Object.values(mainChip).every(value => String(value || "").trim());
  const valueRows = [
    ["Chip Size(um)", values.chip_size_x_um, values.chip_size_y_um],
    ["S/L Size(um)", values.sl_size_x_um, values.sl_size_y_um],
    ["Shot", values.shot_cols, values.shot_rows],
    ["Shot Size(um)", values.shot_size_x_um, values.shot_size_y_um],
    ["Map offset(Odd)(um)", values.map_offset_odd_x, values.map_offset_odd_y],
    ...(values.rc_cols && values.rc_rows ? [["R/C Count", values.rc_cols, values.rc_rows]] : []),
  ];
  const th = { padding: "6px 8px", textAlign: "left", borderBottom: "1px solid var(--line)", fontSize: 12 };
  const td = { padding: "6px 8px", borderBottom: "1px solid var(--line)", fontSize: 12 };
  return <Modal open={open} onClose={busy ? undefined : onClose} title="제품 추가" width={920} maxHeight="92vh">
    {step === 1 ? <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ border: "1px solid var(--line)", borderRadius: 7, padding: "9px 11px", background: "var(--bg-hover)" }}>
        <b style={{ display: "block", fontSize: 13, marginBottom: 3 }}>1. 제품 분류와 제품명</b>
        <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.55 }}>
          먼저 제품의 분류와 이름을 확정합니다. 다음 단계에서 만드는 Chip_Radius, Teg_location,
          Main_chip_info, TEG_Product_Info에는 이 제품명이 동일한 조인 키로 들어갑니다.
        </div>
      </div>
      <section style={{ display: "grid", gap: 8, border: "1px solid var(--line)", borderRadius: 7, padding: 12 }}>
        <label style={{ fontSize: 12, fontWeight: 700 }}>제품 분류</label>
        <input aria-label="제품 분류" value={nodePath} onChange={e => setNodePath(e.target.value)}
          placeholder="예: 2나노 / 2나노A" style={{ ...inputStyle, width: "100%" }} />
        <label style={{ fontSize: 12, fontWeight: 700, marginTop: 3 }}>제품명 (vehicle)</label>
        <input aria-label="제품명" value={vehicle} onChange={e => setVehicle(e.target.value)}
          placeholder="예: VH_PRODUCT_A" style={{ ...inputStyle, width: "100%" }} />
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          제품은 `{nodePath || "제품 분류"} / {vehicle || "제품명"}` 구조로 저장되며 접근 권한은 첫 분류 노드를 기준으로 적용됩니다.
        </div>
      </section>
      {error && <div style={{ color: "var(--danger)", fontSize: 12 }}>{error}</div>}
      <div className="ds-modal__actions">
        <Button onClick={onClose} disabled={busy}>취소</Button>
        <Button variant="primary" onClick={() => { setError(""); setStep(2); }}
          disabled={!vehicle.trim() || !nodePath.trim()}>제품 형상 입력 →</Button>
      </div>
    </div> : step === 2 ? <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ border: "1px solid var(--line)", borderRadius: 7, padding: "9px 11px", background: "var(--bg-hover)" }}>
        <b style={{ display: "block", fontSize: 13, marginBottom: 3 }}>2. 제품 형상 붙여넣기</b>
        <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.55 }}>
          <b>{nodePath} / {vehicle}</b>의 YMS Photomap generator 표를 붙여넣어 주세요. Chip Size, S/L Size,
          Shot, Shot Size, Map offset(Odd)를 읽어 wafer edge 안의 Shot과 radius를 계산합니다.
          R/C Count가 있으면 full-shot 사각형의 X/Y 개수로 사용하고 좌상단을 (1, 1)로 잡습니다.
          Map offset X/Y는 shot 번호가 아니라 wafer 실 center 기준 물리 거리(µm)로 해석합니다.
          이 단계에서는 아직 파일을 저장하지 않습니다.
        </div>
      </div>
      <SpreadsheetPasteGrid columns={PRODUCT_INFO_COLUMNS} rows={productRows} onChange={setProductRows}
        ariaLabel="YMS Photomap 제품 정보" minRows={10} maxRows={200} maxHeight={365} />
      {error && <div style={{ color: "var(--danger)", fontSize: 12 }}>{error}</div>}
      <div className="ds-modal__actions">
        <Button onClick={() => { setStep(1); setError(""); }} disabled={busy}>← 제품 정보</Button>
        <Button onClick={onClose} disabled={busy}>취소</Button>
        <Button variant="primary" onClick={inspect} disabled={busy || !text.trim()}>{busy ? "계산 중…" : "Shot/Radius 계산 →"}</Button>
      </div>
    </div> : <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ border: "1px solid var(--line)", borderRadius: 7, padding: "9px 11px", background: "var(--bg-hover)" }}>
        <b style={{ display: "block", fontSize: 13, marginBottom: 3 }}>3. 계산 결과와 TEG 정보 확인</b>
        <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.55 }}>
          <b>{nodePath} / {vehicle}</b>의 계산 결과와 Teg_location 정보를 확인해 주세요. 마지막 저장 버튼을 눌렀을 때만
          Chip_Radius.csv와 관련 기준 파일에 함께 반영됩니다.
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12, alignItems: "start" }}>
        <section style={{ border: "1px solid var(--line)", borderRadius: 7, padding: 10 }}>
          <b style={{ display: "block", marginBottom: 7 }}>인식한 제품 정보</b>
          <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
            <table style={{ borderCollapse: "collapse", width: "100%" }}><thead><tr>
              <th style={th}>Item</th><th style={th}>x</th><th style={th}>y</th>
            </tr></thead><tbody>{valueRows.map(row => <tr key={row[0]}>
              <td style={td}>{row[0]}</td><td style={td}>{row[1]}</td><td style={td}>{row[2]}</td>
            </tr>)}</tbody></table>
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>
            Chip_Radius.csv 추가 예정: {preview.shot_count} shots · Shot의 네 꼭짓점이 {preview.wafer_edge_mm}mm 안에
            모두 들어오는 경우만 포함하며 경계에 딱 맞닿는 Shot도 포함합니다. · {preview.one_by_one
              ? "Shot 1×1 · Main 정보 필수"
              : `칩 격자 ${values.shot_cols}×${values.shot_rows} 자동 설정`}
          </div>
        </section>
        <section style={{ display: "flex", flexDirection: "column", gap: 8, border: "1px solid var(--line)", borderRadius: 7, padding: 10 }}>
          <b style={{ fontSize: 12 }}>저장 대상</b>
          <div style={{ fontSize: 12, lineHeight: 1.6 }}><b>제품 분류</b> · {nodePath}<br/><b>제품명</b> · {vehicle}</div>
          {preview.one_by_one && <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 10 }}>
            <b style={{ display: "block", fontSize: 12, marginBottom: 3 }}>Shot 1×1 · Main 정보 필수</b>
            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 7 }}>chip_name과 X/Y 크기를 받아 Main_chip_info.csv에 추가합니다.</div>
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr 1fr", gap: 6 }}>
              <input aria-label="Main chip_name" value={mainChip.chip_name} onChange={e => setMainChip(v => ({ ...v, chip_name: e.target.value }))}
                placeholder="chip_name" style={{ ...inputStyle, width: "100%" }} />
              <input aria-label="Main chipsize_x" type="number" step="any" value={mainChip.chipsize_x} onChange={e => setMainChip(v => ({ ...v, chipsize_x: e.target.value }))}
                placeholder="chipsize_x" style={{ ...inputStyle, width: "100%" }} />
              <input aria-label="Main chipsize_y" type="number" step="any" value={mainChip.chipsize_y} onChange={e => setMainChip(v => ({ ...v, chipsize_y: e.target.value }))}
                placeholder="chipsize_y" style={{ ...inputStyle, width: "100%" }} />
            </div>
          </div>}
          {!preview.one_by_one && <div style={{ fontSize: 11, color: "var(--muted)" }}>Shot이 1×1이 아니므로 Main_chip_info는 생성하지 않습니다.</div>}
        </section>
      </div>
      <section style={{ display: "grid", gap: 8, border: "1px solid var(--line)", borderRadius: 7, padding: 10 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
          <b style={{ fontSize: 12 }}>Teg_location 붙여넣기</b>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>Excel의 7열 표를 그대로 붙여넣으면 Teg_location.csv에 제품명과 함께 추가됩니다.</span>
          <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 800 }}>{tegPayload.length}개 입력</span>
        </div>
        <SpreadsheetPasteGrid columns={TEG_LOCATION_COLUMNS} rows={tegRows} onChange={setTegRows}
          ariaLabel="Teg_location 추가 행" minRows={10} maxRows={200} maxHeight={365} />
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          direction은 H, V(R), V(L)을 사용할 수 있습니다. ebeam/teg 크기 값은 기존 Teg_location.csv의 단위를 그대로 사용합니다.
        </div>
      </section>
      {error && <div style={{ color: "var(--danger)", fontSize: 12 }}>{error}</div>}
      <div className="ds-modal__actions">
        <Button onClick={() => { setStep(2); setError(""); }} disabled={busy}>← 제품 형상 수정</Button>
        <Button onClick={onClose} disabled={busy}>취소</Button>
        <Button variant="primary" onClick={create} disabled={busy || !vehicle.trim() || !nodePath.trim() || !tegPayload.length || !mainReady}>{busy ? "저장 중…" : "최종 저장 및 제품 생성"}</Button>
      </div>
    </div>}
  </Modal>;
}

function ProductGeometryModal({ open, vehicle, onClose, onSaved }) {
  const [rows, setRows] = useState(() => normalizeSpreadsheetRows([], PRODUCT_INFO_COLUMNS));
  const [preview, setPreview] = useState(null);
  const [productName, setProductName] = useState("");
  const [nodePath, setNodePath] = useState("");
  const [originalIdentity, setOriginalIdentity] = useState({ productName: "", nodePath: "" });
  const [busy, setBusy] = useState(false);
  const [storedInfo, setStoredInfo] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let active = true;
    setRows(normalizeSpreadsheetRows([], PRODUCT_INFO_COLUMNS));
    setPreview(null); setStoredInfo(null); setProductName(vehicle || ""); setNodePath("");
    setOriginalIdentity({ productName: vehicle || "", nodePath: "" });
    setBusy(true); setError("");
    sf(`${API}/products/${encodeURIComponent(vehicle)}/geometry`).then(result => {
      if (!active) return;
      setStoredInfo(Boolean(result.exists));
      const loadedName = String(result.vehicle || vehicle || "");
      const loadedPath = String(result.node_path || "");
      setProductName(loadedName); setNodePath(loadedPath);
      setOriginalIdentity({ productName: loadedName, nodePath: loadedPath });
      setRows(normalizeSpreadsheetRows(result.rows || [], PRODUCT_INFO_COLUMNS));
    }).catch(e => {
      if (active) setError(String(e.message || e));
    }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [open, vehicle]);
  const text = useMemo(() => spreadsheetTextFromRows(rows, PRODUCT_INFO_COLUMNS), [rows]);
  const identityDirty = productName.trim() !== originalIdentity.productName
    || nodePath.trim() !== originalIdentity.nodePath;
  const inspect = async () => {
    setBusy(true); setError("");
    try { setPreview(await postJson(`${API}/product-preview`, { text, vehicle })); }
    catch (e) { setPreview(null); setError(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!preview && !identityDirty) return;
    setBusy(true); setError("");
    try {
      let result = null;
      if (preview) {
        result = await putJson(`${API}/products/${encodeURIComponent(vehicle)}/geometry`, { text });
      }
      if (identityDirty) {
        result = await putJson(`${API}/products/${encodeURIComponent(vehicle)}/identity`, {
          vehicle: productName.trim(), node_path: nodePath.trim(),
        });
      }
      const savedVehicle = result?.vehicle || productName.trim() || vehicle;
      toast.ok(preview
        ? `${savedVehicle} config 변경됨 · Chip_Radius ${preview.shot_count} shots 재생성`
        : `${savedVehicle} 제품명·분류 변경됨`);
      if (onSaved) await onSaved(result);
      onClose();
    } catch (e) { setError(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const values = preview?.values || {};
  const valueRows = [
    ["Chip Size(um)", values.chip_size_x_um, values.chip_size_y_um],
    ["S/L Size(um)", values.sl_size_x_um, values.sl_size_y_um],
    ["Shot", values.shot_cols, values.shot_rows],
    ["Shot Size(um)", values.shot_size_x_um, values.shot_size_y_um],
    ["Map offset(Odd)(um)", values.map_offset_odd_x, values.map_offset_odd_y],
    ...(values.rc_cols && values.rc_rows ? [["R/C Count", values.rc_cols, values.rc_rows]] : []),
  ];
  const cell = { padding: "6px 8px", borderBottom: "1px solid var(--line)", fontSize: 12 };
  return <Modal open={open} onClose={busy ? undefined : onClose} title={`${vehicle || "제품"} config 변경`} width={900} maxHeight="92vh">
    <div style={{ display: "grid", gap: 11 }}>
      <div style={{ border: "1px solid var(--line)", borderRadius: 7, padding: "9px 11px", background: "var(--bg-hover)" }}>
        <b style={{ display: "block", fontSize: 13, marginBottom: 3 }}>제품명·분류와 Item / X / Y 변경</b>
        <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.55 }}>
          {storedInfo === true
            ? "현재 TEG_Product_Info.csv에 저장된 값을 불러왔습니다. 필요한 셀을 바로 변경할 수 있습니다. "
            : storedInfo === false
              ? "아직 Item/X/Y 원본이 없는 제품입니다. YMS Photomap generator의 표를 붙여넣어 주세요. "
              : "현재 저장 정보를 불러오는 중입니다. "}
          Shot Size와 Map offset(Odd)을 µm 원값 그대로 저장합니다. Map offset은 shot 좌표가 아니라
          wafer 실 center에서 기준 shot center까지의 X/Y 물리 거리입니다.
          R/C Count는 full-shot X/Y 개수이며 좌상단 좌표는 (1, 1)입니다. 계산에 쓰지 않는 Item도 삭제하지 않고 함께 저장합니다.
          저장 후 WF MAP의 shot 크기·실center는 Chip_Radius fit으로 다시 추정하지 않고 이 원값으로 계산합니다.
        </div>
      </div>
      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 9, border: "1px solid var(--line)", borderRadius: 7, padding: 10 }}>
        <label style={{ display: "grid", gap: 5, fontSize: 12, fontWeight: 700 }}>
          제품 분류
          <input aria-label="변경할 제품 분류" value={nodePath}
            onChange={e => { setNodePath(e.target.value); setError(""); }}
            placeholder="예: 2나노 / 2나노A" style={{ ...inputStyle, width: "100%" }} />
        </label>
        <label style={{ display: "grid", gap: 5, fontSize: 12, fontWeight: 700 }}>
          제품명 (vehicle)
          <input aria-label="변경할 제품명" value={productName}
            onChange={e => { setProductName(e.target.value); setError(""); }}
            placeholder="예: VH_PRODUCT_A" style={{ ...inputStyle, width: "100%" }} />
        </label>
        <div style={{ gridColumn: "1 / -1", fontSize: 11, color: "var(--muted)", lineHeight: 1.55 }}>
          제품명을 바꾸면 Chip_Radius, Teg_location, Main_chip_info, TEG_Product_Info와 제품별 Mapfile/Inline 설정의 vehicle도 함께 변경됩니다.
        </div>
      </section>
      <SpreadsheetPasteGrid columns={PRODUCT_INFO_COLUMNS} rows={rows}
        onChange={next => { setRows(next); setPreview(null); setError(""); }}
        ariaLabel={`${vehicle} config Item X Y`} minRows={10} maxRows={200} maxHeight={365} />
      {preview && <section style={{ border: "1px solid var(--line)", borderRadius: 7, padding: 10 }}>
        <b style={{ display: "block", marginBottom: 7 }}>변경 내용 확인</b>
        <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden" }}>
          <table style={{ borderCollapse: "collapse", width: "100%" }}><thead><tr>
            {PRODUCT_INFO_COLUMNS.map(column => <th key={column} style={{ ...cell, textAlign: "left" }}>{column}</th>)}
          </tr></thead><tbody>{valueRows.map(row => <tr key={row[0]}>
            {row.map((value, index) => <td key={index} style={cell}>{value}</td>)}
          </tr>)}</tbody></table>
        </div>
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>
          이 제품의 기존 Chip_Radius 행을 교체하고 <b>{preview.shot_count}개 shot</b>을 다시 생성합니다.
          chip_x_adj / chip_y_adj는 정수 격자로 저장하고 radius는 {preview.radius_decimals}자리 정밀도로 기록합니다.
          TEG_Product_Info.csv와 Chip_Radius.csv 변경본은 모두 EDM 이력에 반영됩니다.
        </div>
      </section>}
      {error && <div style={{ color: "var(--danger)", fontSize: 12 }}>{error}</div>}
      <div className="ds-modal__actions">
        <Button onClick={onClose} disabled={busy}>취소</Button>
        {!preview && <Button onClick={inspect} disabled={busy || !text.trim()}>{busy ? "계산 중…" : "형상 변경 계산"}</Button>}
        {(preview || identityDirty) && <Button variant="primary" onClick={save}
          disabled={busy || !productName.trim() || !nodePath.trim()}>
          {busy ? "저장 중…" : preview ? "전체 config 저장" : "제품명·분류 저장"}
        </Button>}
      </div>
    </div>
  </Modal>;
}

export default function My_TegMap({ user }) {
  const [vehicles, setVehicles] = useState(null);   // null=로딩, []=없음
  const [productCatalog, setProductCatalog] = useState([]);
  const [vehicle, setVehicle] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [selectedTegs, setSelectedTegs] = useState(new Set());
  const [selectedShot, setSelectedShot] = useState(null);
  const [fullShot, setFullShot] = useState(false);   // wafer 전체를 덮는 shot 격자 표시
  const [fullChip, setFullChip] = useState(false);   // 최외곽 안의 grid/dev_grid die 전체 표시
  const [imgUrl, setImgUrl] = useState(null);
  const [imgShapes, setImgShapes] = useState(null); // /image/shapes 응답 (그림 격자 + 개발 격자)
  const [view, setView] = useState("map");   // map=위치 조회 | check=TEG Mapfile 체크
  const [productOpen, setProductOpen] = useState(false);
  const [geometryOpen, setGeometryOpen] = useState(false);
  // 같은 vehicle 의 config/기준 파일이 바뀌어도 자식의 vehicle prop 은 그대로다.
  // 명시적 revision 으로 Mapfile 생성 응답을 무효화한다.
  const [referenceRevision, setReferenceRevision] = useState(0);

  const canEdit = user?.role === "admin" || (user?.page_manager || []).includes("teg");
  const isAdmin = user?.role === "admin";
  const tegPageTokens = Array.isArray(user?.tabs)
    ? user.tabs
    : String(user?.tabs || "").split(",");
  const canEditReferenceFiles = canEdit || tegPageTokens.some(token => String(token || "").trim().split(":")[0] === "teg");
  useEffect(() => {
    if (view === "files" || (!isAdmin && ["access", "inline"].includes(view))) setView("map");
  }, [isAdmin, view]);

  const loadVehicles = useCallback(async (preferred = "") => {
    try {
      const r = await sf(API + "/vehicles");
      const list = r.vehicles || [];
      setProductCatalog(r.products || list.map(name => ({ vehicle: name, node_path: "미분류", root_node: "미분류" })));
      setVehicles(list);
      setVehicle(current => (preferred && list.includes(preferred))
        ? preferred : (current && list.includes(current) ? current : (list[0] || "")));
    } catch (e) {
      setVehicles([]);
      setProductCatalog([]);
      setErr(String(e.message || e));
    }
  }, []);
  const productGroups = useMemo(() => {
    const groups = new Map();
    for (const item of productCatalog) {
      const path = item.node_path || "미분류";
      if (!groups.has(path)) groups.set(path, []);
      groups.get(path).push(item.vehicle);
    }
    return [...groups.entries()];
  }, [productCatalog]);
  useEffect(() => { loadVehicles(); }, []);

  const loadMap = useCallback(async (requestedVehicle = vehicle) => {
    const target = typeof requestedVehicle === "string" ? requestedVehicle : vehicle;
    if (!target) return;
    setErr("");
    try {
      const r = await sf(`${API}/map?vehicle=${encodeURIComponent(target)}`);
      setData(r);
      setSelectedShot(null);
      // 기본은 아무것도 선택하지 않는다 — 볼 TEG 를 직접 고르는 화면이고,
      // 임의의 첫 TEG 가 켜져 있으면 그게 기준인 줄 오해하게 된다.
      setSelectedTegs(new Set());
    } catch (e) {
      setData(null);
      setErr(String(e.message || e));
    }
  }, [vehicle]);
  useEffect(() => { loadMap(); }, [loadMap]);

  // vehicle 그림 + die 사각형 — 그림 격자(image_cells)와 개발 격자(dev_cells) 둘 다 받는다.
  useEffect(() => {
    let revoked = false;
    let url = null;
    setImgUrl(null);
    setImgShapes(null);
    if (["image", "dev_grid"].includes(data?.display?.mode)) {
      if (data?.display?.has_image) {
        fetchImageBlobUrl(data.vehicle).then(u => {
          if (revoked) { if (u) URL.revokeObjectURL(u); return; }
          url = u;
          setImgUrl(u);
        });
      }
      // 그림이 없어도 조회한다 — 개발 격자는 chip 크기표만으로도 그릴 수 있다.
      fetchShapes(data.vehicle).then(s => {
        if (!revoked) setImgShapes(s || null);
      });
    }
    return () => {
      revoked = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [data]);

  // 그림 모드 shot 확대 보기 — "image"(그림) / "grid"(그림에서 인식한 격자).
  // 격자를 고른 채 인식 결과가 없는 vehicle 로 넘어가면 빈 화면이 되므로 그림으로 되돌린다.
  const [shotView, setShotView] = useState("image");
  const shotMode = data?.display?.mode;
  const gridView = shotView === "grid" && (imgShapes?.image_cells || []).length > 0;
  const dieCells = useMemo(() => {
    if (shotMode === "dev_grid") return imgShapes?.dev_cells || [];
    if (shotMode === "image" && gridView) return imgShapes?.image_cells || [];
    return [];
  }, [shotMode, gridView, imgShapes]);
  const showPicture = shotMode === "image" && !gridView;

  const tegNames = useMemo(() => tegListNames(data?.tegs), [data]);
  const tegColor = useCallback((name) => {
    const i = tegNames.indexOf(name);
    return TEG_COLORS[(i >= 0 ? i : 0) % TEG_COLORS.length];
  }, [tegNames]);

  // shot 확대는 카드 폭에 맞춰 키운다. 우측 좌표 패널은 옆에 두고도 배치도가 충분히
  // 클 때만 자리를 내주고, 좁으면 패널을 아래로 흘려보내고 폭을 전부 쓴다 —
  // 좁은 폭에서 억지로 나눠 쓰면 배치도만 작아지고 옆은 그대로 빈다.
  const [shotBoxRef, shotBoxW] = useBoxWidth();
  const nCoord = coordPanelCount(data, selectedTegs);
  const shotZoomSize = useMemo(() => {
    const box = shotBoxW || 0;
    const side = COORD_PANEL_W + 12;
    const coordShown = nCoord > 0 && nCoord < COORD_PANEL_MAX;
    const reserved = (coordShown && box - side >= SHOT_ZOOM_SIDE_MIN) ? side : 0;
    return Math.round(Math.max(SHOT_ZOOM_MIN, Math.min(SHOT_ZOOM_MAX, box - reserved)));
  }, [shotBoxW, nCoord]);

  // 일반 사용자 동시 선택 상한 (전체 렌더 시 502/브라우저 다운 방지). null = 관리자(무제한).
  const maxSel = data?.max_selection ?? null;
  // TEG 다중 선택 — 클릭으로 on/off 토글, 전체/해제 버튼.
  const toggleTeg = (name) => {
    const next = new Set(selectedTegs);
    const turnOn = !next.has(name);
    if (turnOn) next.add(name); else next.delete(name);
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
  const fullChipCells = useMemo(() => {
    if (display.mode === "grid" && geo?.fit === "radius") {
      return chipCells(display, geo.shot_w_mm, geo.shot_h_mm).cells;
    }
    if (display.mode === "dev_grid") return dieCells;
    return [];
  }, [display, geo, dieCells]);
  const fullChipAvailable = geo?.fit === "radius"
    && ["grid", "dev_grid"].includes(display.mode)
    && fullChipCells.length > 0;
  useEffect(() => {
    if (!fullChipAvailable) setFullChip(false);
  }, [fullChipAvailable, vehicle]);
  // full chip도 full shot과 같은 147mm 교차 shot 격자를 쓰고, 그 안에서 die를
  // 다시 edge 완전 포함으로 거른다.
  const extendedShotData = useMemo(() => {
    if (!data || !fullChip) return data;
    const shots = buildFullShots(data);
    return shots === data.shots ? data : { ...data, shots };
  }, [data, fullChip]);
  // full shot 화면은 제품별 edge 원과 겹치거나 정확히 닿는 shot을 사용한다.
  const mapData = useMemo(() => {
    if (!data) return data;
    if (!fullShot) return extendedShotData;
    const shots = buildFullShots(data);
    return shots === data.shots ? data : { ...data, shots };
  }, [data, extendedShotData, fullShot]);
  const fullShotCounts = useMemo(() => {
    const shots = mapData?.shots || [];
    const geometry = mapData?.geometry;
    return {
      inside: shots.filter(shot => shotInsideWaferEdge(shot, geometry)).length,
      total: shots.length,
    };
  }, [mapData]);
  const fullChipResult = useMemo(() => fullChip
    ? buildFullChipDies(extendedShotData, fullChipCells)
    : { dies: [], overflow: false }, [fullChip, extendedShotData, fullChipCells]);
  const toggleFullShot = (on) => {
    setFullShot(on);
    if (!on && selectedShot?.synthetic) setSelectedShot(null);
  };
  // 가장 가까운 샷 센터 — WaferMap 빨간 점 표시 + 실center 차이 계산용
  // (파일에 실제로 있는 shot 기준 — full shot 격자는 fit 근거가 아니다)
  const nearestShot = useMemo(() => {
    if (geo?.fit !== "radius") return null;
    const withR = (data?.shots || []).filter(s0 => typeof s0.radius === "number");
    return withR.length ? withR.reduce((a, b) => (a.radius <= b.radius ? a : b)) : null;
  }, [data, geo]);

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 7, alignItems: "center", flexWrap: "nowrap", flex: "1 1 auto", minWidth: 0 }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: "var(--muted)" }}>제품 선택</span>
          <Select value={vehicle} onChange={e => setVehicle(e.target.value)}
            style={{ width: 240, minWidth: 170, flex: "0 0 240px" }}>
            {productGroups.map(([path, names]) => <optgroup key={path} label={path}>
              {names.map(name => <option key={name} value={name}>{name}</option>)}
            </optgroup>)}
          </Select>
          {canEditReferenceFiles && <Button variant="primary" onClick={() => setProductOpen(true)}>+ 제품 추가</Button>}
          <Button onClick={() => loadMap()}>새로고침</Button>
        </div>
        {canEditReferenceFiles && data &&
          <Button onClick={() => setGeometryOpen(true)}>config 변경</Button>}
      </div>
      <ProductCreateModal open={productOpen} onClose={() => setProductOpen(false)}
        onCreated={async result => {
          await loadVehicles(result.vehicle);
          await loadMap(result.vehicle);
          setReferenceRevision(value => value + 1);
        }} />
      <ProductGeometryModal open={geometryOpen} vehicle={vehicle}
        onClose={() => setGeometryOpen(false)}
        onSaved={async result => {
          const target = result?.vehicle || vehicle;
          await loadVehicles(target);
          await loadMap(target);
          setReferenceRevision(value => value + 1);
        }} />

      <TabStrip active={view} onChange={setView}
        items={[{ k: "map", l: "위치 조회" }, { k: "check", l: "Mapfile 검증" },
                { k: "gen", l: "Mapfile 좌표 생성" },
                ...(isAdmin ? [
                  { k: "access", l: "제품 권한 · 관리자" },
                  { k: "inline", l: "Inline map setting · 관리자" },
                ] : [])]} />

      {view === "check" && <TegCheck vehicle={vehicle} refreshKey={referenceRevision} canEdit={canEdit} />}
      {view === "gen" && <TegGenerate vehicle={vehicle} refreshKey={referenceRevision} />}
      {view === "access" && isAdmin && <ProductAccessAdmin onSaved={loadVehicles} />}
      {view === "inline" && isAdmin &&
        <InlineMapSetting data={data} vehicle={vehicle} onVehicleChange={setVehicle} />}

      {view === "map" && <>
      {vehicles && vehicles.length === 0 && (
        <EmptyState icon="📐" title={isAdmin ? "등록된 제품이 없습니다" : "접근 가능한 제품이 없습니다"}
          hint={isAdmin ? "왼쪽 상단의 제품 추가에서 Item/x/y 표를 붙여넣거나, fallback용 Chip_Radius 파일을 설정하세요" : "상위 제품 노드 권한을 관리자에게 요청해 주세요"} />
      )}
      {err && vehicles && vehicles.length > 0 && (
        <EmptyState icon="⚠" title="WF MAP 을 불러오지 못했습니다" hint={err} />
      )}

      {data && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
          {/* 좌: wafer 전체 */}
          <Card title={data.vehicle}
            right={
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <label style={{
                  display: "flex", alignItems: "center", gap: 4, fontSize: 12,
                  color: geo?.fit === "radius" ? "var(--text-primary)" : "var(--muted)",
                  cursor: geo?.fit === "radius" ? "pointer" : "not-allowed",
                }}
                  title={geo?.fit === "radius"
                    ? `같은 shot 크기로 격자를 연장하고 제품 최외곽 ${fmt(geo?.wafer_edge_mm, 1)}mm 원과 겹치거나 경계에 정확히 닿는 shot까지 표시합니다.`
                      + " 추가된 자리는 점선입니다."
                    : "Chip_Radius fit 이 되어야 shot 크기를 알 수 있어 사용할 수 없습니다"}>
                  <input type="checkbox" checked={fullShot} disabled={geo?.fit !== "radius"}
                    onChange={e => toggleFullShot(e.target.checked)} />
                  full shot
                  {fullShot && <span style={{ color: "var(--muted)" }}
                      title={`shot 전체가 제품 최외곽 ${fmt(geo?.wafer_edge_mm, 1)}mm 안인 수 / 경계 접촉·교차를 포함한 full shot 전체 수`}>
                    {fullShotCounts.inside}/{fullShotCounts.total}
                  </span>}
                </label>
                <label style={{
                  display: "flex", alignItems: "center", gap: 4, fontSize: 12,
                  color: fullChipAvailable ? "var(--text-primary)" : "var(--muted)",
                  cursor: fullChipAvailable ? "pointer" : "not-allowed",
                }}
                  title={fullChipAvailable
                    ? `칩/개발 격자의 die를 모든 shot에 펼치고 네 꼭짓점이 최외곽 ${fmt(geo?.wafer_edge_mm, 0)}mm 선에 닿지 않고 모두 안쪽인 die만 표시합니다.`
                    : display.mode === "dev_grid"
                      ? "개발 격자 MAIN die가 없거나 Main_chip_info 크기를 확인 중입니다."
                      : "칩 격자 또는 개발 격자 제품에서만 사용할 수 있습니다."}>
                  <input type="checkbox" checked={fullChip} disabled={!fullChipAvailable}
                    onChange={e => setFullChip(e.target.checked)} />
                  full chip{fullChip ? ` ${fullChipResult.dies.length}` : ""}
                </label>
                {geo?.fit !== "radius" && (
                  <Pill tone="warn" title={geo?.fit_note || ""}>
                    Chip_Radius fit 불가 — 격자 좌표로만 표시{geo?.fit_note ? ` (${geo.fit_note})` : ""}
                  </Pill>
                )}
              </div>
            }>
            {/* 범례 — maxWidth 없이 두면 flex 아이템(카드)이 이 한 줄의 max-content 폭으로
                부풀어 shot 확대 쪽이 밀리고 옆이 통째로 빈다. 지도 폭에 맞춰 접는다. */}
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, maxWidth: 640 }}>
              shot 클릭 → 확대 뷰. 실선 원 = wafer {fmt(geo?.wafer_radius_mm, 0)}mm,
              점선 원 = 제품 최외곽 {fmt(geo?.wafer_edge_mm, 1)}mm.
              <br />
              TEG 선택 시: <span style={{ color: "#2f9e63", fontWeight: 700 }}>■ 초록</span> = TEG 전체가 최외곽 안,
              <span style={{ color: "#e05252", fontWeight: 700 }}> ■ 빨강</span> = TEG 가 걸림.
              미선택 시: <span style={{ color: "#3e7bd6", fontWeight: 700 }}>■ 연파랑</span> = shot 전체가 안,
              <span style={{ color: "#c78a1e", fontWeight: 700 }}> ■ 연노랑</span> = shot 이 걸치거나 밖.
              격자/그림은 shot 확대에서만 표시됩니다.
              {fullShot && (
                <> <b>full shot {fullShotCounts.inside}/{fullShotCounts.total}</b>: 앞 숫자는 shot 전체가 제품 최외곽 {fmt(geo?.wafer_edge_mm, 1)}mm 안에 들어온 수, 뒤 숫자는 경계에 닿거나 교차하는 자리까지 포함한 전체 수입니다. 점선 = layout 파일에 없는 자리(격자 연장).</>
              )}
              {fullChip && (
                <> <b> full chip</b>: 칩/개발 격자의 die 중 네 꼭짓점이 최외곽 {fmt(geo?.wafer_edge_mm, 0)}mm 선에 닿지 않고 모두 안쪽인
                  {" "}{fullChipResult.dies.length}개만 표시합니다.
                  {fullChipResult.overflow && <> 화면 보호를 위해 최대 {FULL_CHIP_MAX}개까지만 표시했습니다.</>}
                </>
              )}
            </div>
            <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
              <WaferMap data={mapData} selectedTegs={selectedTegs} tegColor={tegColor}
                selectedShot={selectedShot} onShotClick={onShotClick}
                nearestShot={nearestShot} fullChipDies={fullChip ? fullChipResult.dies : null} />
              {/* TEG 목록 — 다중 선택 가능 */}
              <div style={{ minWidth: 170, maxWidth: 240 }}>
                {/* Chip_Radius 계산 정보 — shot 크기 + 가장 가까운 shot 실center 델타(µm) */}
                {geo?.fit === "radius" && (
                    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: "8px 10px", marginBottom: 10, fontSize: 12, lineHeight: 1.7 }}>
                      <div style={{ fontWeight: 700, color: "var(--muted)", marginBottom: 2 }}>
                        {data.layout_source === "product_info" ? "제품 추가 정보 계산" : "Chip_Radius 계산 정보"}
                      </div>
                      <div>shot 크기: <b>{fmt(geo.shot_w_mm, 3)} × {fmt(geo.shot_h_mm, 3)} mm</b></div>
                      {data.layout_source === "product_info" && (
                        <div title="config에 저장된 Map offset(Odd) 원값 — wafer 실 center에서 기준 shot center까지의 물리 거리">
                          config Map offset: <b>X {fmt(geo.map_offset_odd_x_um, 1)} · Y {fmt(geo.map_offset_odd_y_um, 1)} µm</b>
                        </div>
                      )}
                      {nearestShot && (
                        <>
                          <div>가장 가까운 샷: <b style={{ color: "#e05252" }}>({nearestShot.x}, {nearestShot.y})</b>
                            <span style={{ color: "var(--muted)", marginLeft: 4 }}>(빨간 점)</span></div>
                          <div title="실center(wafer 중심)에서 가장 가까운 샷 센터로 이동하는 Δx, Δy. x 우측↑, y 위↑ 양수.">
                            실center 차이: <b>Δx {fmt(nearestShot.mm_x * 1000, 1)} · Δy {fmt(-nearestShot.mm_y * 1000, 1)} µm</b>
                          </div>
                        </>
                      )}
                      {data.layout_source === "product_info" ? (
                        <div title="Shot Size(mm)와 Map offset(Odd)(µm)을 단위 변환해 직접 계산합니다.">
                          직접 계산: <b>{geo.fit_used}개 샷</b> · Shot Size(mm) / Map offset(µm)
                        </div>
                      ) : (
                        <div title="fit 에 사용한 샷 수와 측정 radius − fit radius 최대 잔차">
                          fit: <b>{geo.fit_used}개 샷</b> · 잔차 max <b>{fmt(geo.fit_max_residual_mm, 3)} mm</b>
                        </div>
                      )}
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
                      <span style={{ fontWeight: 400, color: "var(--warn)" }} title="일반 사용자 동시 선택 상한 (관리자는 무제한)"> · 최대 {maxSel}</span>
                    )}
                  </span>
                  {tegNames.length > 1 && (
                    <div style={{ display: "flex", gap: 2 }}>
                      <LinkBtn onClick={selectAllTegs} style={{ fontSize: 11, padding: "2px 5px" }}>전체</LinkBtn>
                      <LinkBtn tone="muted" onClick={deselectAllTegs} style={{ fontSize: 11, padding: "2px 5px" }}>해제</LinkBtn>
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
                    return (
                      <button key={n} onClick={() => toggleTeg(n)}
                        title={`(${fmt(Number(t.ebeam_x) * 1000, 3)},${fmt(Number(t.ebeam_y) * 1000, 3)}) µm · shot 좌하단 좌표 기준`
                          + `\n${directionLabel(t)} · 사이즈 ${fmt(Number(t.teg_w) * 1000, 1)}`
                          + ` × ${fmt(Number(t.teg_h) * 1000, 1)}`}
                        style={{
                          display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
                          border: "none", borderLeft: `3px solid ${on ? tegColor(n) : "transparent"}`,
                          background: on ? "var(--bg-hover)" : "transparent", color: "var(--text-primary)",
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
                        {/* vertical TEG 표시 — 세워서 그린다는 걸 목록에서 바로 보이게 */}
                        {isVertical(t) && (
                          <span style={{ marginLeft: "auto", fontSize: 10, flexShrink: 0,
                                         color: "#8a5fd0", border: "1px solid #8a5fd0",
                                         borderRadius: 4, padding: "0 4px" }}>{directionLabel(t)}</span>
                        )}
                      </button>
                    );
                  })}
                </div>
                {/* Mapfile 체크 대상 TEG 설정 — 관리자만 편집 가능 */}
                <CheckTargetEditor vehicle={vehicle} canEdit={canEdit} />
              </div>
            </div>
          </Card>

          {/* 우: shot 확대 + radius */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: "1 1 400px", minWidth: 400 }}>
            <Card title={selectedShot ? `shot 확대 — (${selectedShot.x}, ${selectedShot.y})` : "shot 확대 — shot 내 TEG 배치"}
              right={shotMode === "image" ? (
                /* 그림 모드 — 붙여넣은 그림 그대로 볼지, 거기서 인식한 격자로 볼지 */
                <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  {[["image", "그림"], ["grid", `격자${(imgShapes?.image_cells || []).length
                    ? ` ${imgShapes.image_cells.length}` : ""}`]].map(([v, label]) => (
                    <Button key={v} variant={shotView === v ? "primary" : "subtle"}
                      disabled={v === "grid" && !(imgShapes?.image_cells || []).length}
                      title={v === "grid"
                        ? "그림에서 인식한 die 사각형만 선으로 표시 (Mapfile 체크가 쓰는 영역)"
                        : "붙여넣은 그림 그대로 표시"}
                      onClick={() => setShotView(v)}>{label}</Button>
                  ))}
                </div>
              ) : null}>
              <div ref={shotBoxRef}
                style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
                <ShotZoom data={data} selectedTegs={selectedTegs} tegColor={tegColor}
                  imgUrl={imgUrl} dieCells={dieCells} showPicture={showPicture}
                  size={shotZoomSize} />
                {/* 선택 TEG 5개 미만일 때만 우측에 ebeam 좌표 표시 */}
                <TegCoordInfo data={data} selectedTegs={selectedTegs} tegColor={tegColor} />
              </div>
            </Card>
            <TegRadiusTable data={mapData} selectedTegs={selectedTegs} tegColor={tegColor} />
          </div>
        </div>
      )}
      </>}

      <PageGear title="TEG 위치 조회 설정" canEdit={canEdit} position="bottom-right">
        <GearSettings vehicle={vehicle} canEdit={canEdit}
          onSaved={async () => {
            await loadVehicles(vehicle);
            await loadMap(vehicle);
            setReferenceRevision(value => value + 1);
          }} />
      </PageGear>
    </div>
  );
}
