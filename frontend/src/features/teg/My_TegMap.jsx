/* My_TegMap.jsx — TEG 위치 조회 (WF MAP).
   - chip layout(Mask, chip_x_adj, chip_y_adj, Chip_Radius) 파일로 wafer geometry fit:
     Chip_Radius = shot 센터 ↔ wafer 원점 거리(mm) → shot 크기(mm)·wafer 중심을 최소자승으로 산출.
   - Teg_location(vehicle,teg,ebeam_x,ebeam_y = shot 센터 기준 TEG 좌하단) 을 겹쳐
     여러 TEG 를 wafer 전체 / shot 확대 뷰로 동시 표시. wafer 원(150mm)과 최외곽선(147mm) 함께 표시.
   - TEG 다중 선택: 체크박스로 여러 TEG 를 동시에 선택/비교 가능. 전체/해제 버튼 제공.
   - 동명 TEG 자동 넘버링: 백엔드에서 같은 이름이 2 개 이상이면 _1, _2, … 접미사를 자동 부여.
   - shot 색: 선택 TEG 전체가 최외곽(147mm) 안이면 초록, 라인에 걸치면 빨강.
   - full shot 체크박스: layout 파일에 있는 shot 만 보는 게 기본. 켜면 같은 shot 크기로
     격자를 연장해 wafer 를 빠짐없이 덮는 자리까지 표시(점선) — 실제 노광 시 최외곽에
     걸리는 shot 에서 TEG 가 어디 놓이는지 보기 위한 것.
   - shot 클릭 → shot 확대 뷰. 그림/칩 격자는 확대 뷰에서만 표시 (wafer 전체 뷰는 shot 판정색+TEG 마커만).
   - vehicle 별 shot 표시 방식(⚙️ 설정): 기본 | 그림(teg_location/ 업로드 이미지) |
     칩 격자(cols×rows, 칩 크기 mm·칩 사이 간격 µm, shot 센터 기준 좌우/상하 대칭 배치).
   - 설정 json·그림 파일은 파일탐색기 위치(DB root)의 teg_location/ 폴더에 저장.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sf, putJson } from "../../lib/api";
import { toast } from "../../components/Toast";
import PageGear from "../../components/PageGear";
import ZoomPanSvg from "../../components/ZoomPanSvg";
import { Button, Card, EmptyState, LinkBtn, PageHeader, Pill, Select, TabStrip } from "../../components/UXKit";
import TegCheck from "./TegCheck";
import TegGenerate from "./TegGenerate";
import My_FileBrowser from "../filebrowser/My_FileBrowser";

const API = "/api/teg-map";

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

// MAIN 계열 TEG 판별 — 앞이 글자가 아닌 곳의 MAIN (domain/remain 오탐 제외, 백엔드 is_main 과 동일).
const MAIN_RE = /(?<![A-Za-z])MAIN/i;
function isMainTeg(name) { return MAIN_RE.test(String(name || "")); }

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
   chip layout 파일에는 보통 최외곽(147mm) 안에 온전히 들어오는 shot 만 들어 있다.
   "full shot" 모드는 그 격자(pitch)를 그대로 연장해 **같은 shot 크기로 wafer 를
   빠짐없이 덮는** 자리까지 만들어 낸다 — 실제로 노광하면 어디에 shot 이 놓이고
   그때 TEG 가 최외곽에 걸리는지 보기 위한 것이다.
   - 격자 위상은 실제 shot 하나를 앵커로 삼아 유지한다 (파일의 격자와 정확히 겹침).
   - wafer 원(wafer_radius_mm)과 **면으로** 겹치는 자리만 남긴다. 꼭짓점만 스치는
     자리는 넣지 않는다 (겹침 깊이 TOUCH_TOL 이하 = 사실상 점 접촉).
   - 파일에 없어 새로 만든 자리는 synthetic:true — 화면에서 점선으로 구분한다. */
const FULL_SHOT_TOUCH_TOL = 0.01;    // mm — 이보다 얕게 걸치면 "점으로 스침"으로 보고 제외
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
  const px = Math.abs(geo.pitch_x) || 0, py = Math.abs(geo.pitch_y) || 0;
  const stepX = px * Math.abs(geo.kx), stepY = py * Math.abs(geo.ky);   // mm 단위 격자 간격
  const W = Math.abs(geo.shot_w_mm), H = Math.abs(geo.shot_h_mm);
  const R = Number(geo.wafer_radius_mm) || 0;
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
  const out = real.filter(s0 => Math.abs(Number(s0.x)) > 1e-9 && Math.abs(Number(s0.y)) > 1e-9);
  for (let i = -nx; i <= nx; i++) {
    for (let j = -ny; j <= ny; j++) {
      const x = Math.round((anchor.x + i * px) * 1e6) / 1e6;
      const y = Math.round((anchor.y + j * py) * 1e6) / 1e6;
      const k = gridKey(x, y);
      if (seen.has(k)) continue;
      if (Math.abs(x) <= 1e-9 || Math.abs(y) <= 1e-9) continue;
      const mmx = (x - geo.cx) * geo.kx;
      const mmy = (y - geo.cy) * geo.ky;
      // shot 사각형과 wafer 원의 겹침 — 사각형에서 원 중심까지의 최단거리로 판정
      const dx = Math.max(0, Math.abs(mmx) - W / 2);
      const dy = Math.max(0, Math.abs(mmy) - H / 2);
      if (Math.hypot(dx, dy) >= R - FULL_SHOT_TOUCH_TOL) continue;
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
        // 화면에서 편집하지 않지만 저장 시 함께 돌려보내야 한다 — 빼면 서버가
        // 기본값으로 덮어써 사용자 지정 flat 마커가 조용히 지워진다.
        custom_markers: c0.custom_markers || {},
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
        // µm 입력 → mm 저장 (기본 3000×100 µm)
        teg_default_w: (Number(cfg.teg_default_w_um) || 3000) / 1000,
        teg_default_h: (Number(cfg.teg_default_h_um) || 100) / 1000,
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
          custom_markers: chk.custom_markers || {},
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
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: -2, marginBottom: 6 }}>
        가로(Horizontal) 기준으로 입력하세요 — direction=V 인 TEG 는 이 값을 세워서
        (가로↔세로 바꿔) 그립니다. teg_w/teg_h 열이 있으면 <b>파일 값을 그대로</b> 쓰며,
        V 행은 파일에 이미 세운 크기로 들어 있는 규약이라 다시 뒤집지 않습니다.
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
          ebeam raw 단위 (ΔX/ΔY 와 같은 공간). die 경계에서 이만큼 들어가거나 나간 정도는
          '침범'이 아니라 <b>경계 근처(확인필요)</b>로 봅니다. 0 이면 조금이라도 닿으면 침범.
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

      <div style={sect}>shot 표시 방식 — {vehicle || "(vehicle 선택)"}</div>
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
   그림/칩 격자는 shot 확대 뷰에서만 표시. shot 색: 선택 TEG 전체가 최외곽(147mm)
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

  // shot 별 최외곽(147mm) 판정 — 선택 TEG 사각형의 네 꼭짓점 중 하나라도
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
const COORD_PANEL_W = 190;   // 패널 폭 (shot 확대에 남길 공간 계산용)

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
      <div style={{ fontWeight: 700, color: "var(--muted)" }}>TEG 좌표 — ebeam_x / ebeam_y (mm)</div>
      <div style={{ fontSize: 11, color: "var(--muted)" }} title={DIR_TIP}>
        크기는 Teg_location 값 그대로 (V 는 파일에 이미 세운 크기)
      </div>
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
          <div style={{ color: "var(--muted)", marginLeft: 17 }} title={DIR_TIP}>
            방향 <b style={{ color: isVertical(t) ? "var(--warn)" : "var(--text-primary)" }}>
              {isVertical(t) ? "V (세움)" : "H (가로)"}
            </b>
            {" · "}{fmt(t.teg_w, 3)} × {fmt(t.teg_h, 3)} mm
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
      if (maxD > edge + 1e-9) continue;
      dies.push({
        key: `${gridKey(shot.x, shot.y)}:${i}`,
        x, y, w, h, shotX: shot.x, shotY: shot.y,
      });
      if (dies.length >= FULL_CHIP_MAX) return { dies, overflow: true };
    }
  }
  return { dies, overflow: false };
}

function CalculationDiagram({ kind }) {
  const ink = "var(--text-primary)", muted = "var(--muted)", accent = "var(--accent)";
  const blue = "#3e7bd6", green = "#2f9e63", red = "#e05252", gold = "#c78a1e", purple = "#8a5fd0";
  const text = { fontSize: 11, fill: ink, fontFamily: "Pretendard, sans-serif" };
  const small = { ...text, fontSize: 9, fill: muted };
  const axis = <>
    <line x1="28" y1="92" x2="412" y2="92" stroke={muted} strokeWidth="1" />
    <line x1="220" y1="166" x2="220" y2="16" stroke={muted} strokeWidth="1" />
    <path d="M412 92 l-7 -4 v8z M220 16 l-4 7 h8z" fill={muted} />
  </>;
  let drawing = null;
  if (kind === "fit") drawing = <>
    {axis}
    {[[78,43],[145,61],[307,46],[350,126],[105,132],[280,139]].map(([x,y], i) =>
      <g key={i}><rect x={x-18} y={y-11} width="36" height="22" rx="2" fill="rgba(62,123,214,.12)" stroke={blue}/><circle cx={x} cy={y} r="2.5" fill={blue}/></g>)}
    <circle cx="234" cy="101" r="5" fill={red}/><text x="242" y="114" style={text}>fit center (cx, cy)</text>
    <line x1="234" y1="101" x2="307" y2="46" stroke={red} strokeWidth="2" strokeDasharray="5 3" />
    <text x="270" y="69" style={{...text, fill:red}}>Chip_Radius r</text>
    <text x="397" y="108" style={small}>shot x</text><text x="228" y="26" style={small}>shot y</text>
  </>;
  if (kind === "size") drawing = <>
    <rect x="76" y="42" width="112" height="82" fill="rgba(62,123,214,.10)" stroke={blue} strokeWidth="2" />
    <rect x="230" y="42" width="112" height="82" fill="rgba(62,123,214,.10)" stroke={blue} strokeWidth="2" />
    <circle cx="132" cy="83" r="4" fill={blue}/><circle cx="286" cy="83" r="4" fill={blue}/>
    <line x1="132" y1="24" x2="286" y2="24" stroke={gold} strokeWidth="2"/><path d="M132 24 l8 -4 v8z M286 24 l-8 -4 v8z" fill={gold}/>
    <text x="191" y="17" style={{...text, fill:gold}}>pitch_x × kx</text>
    <line x1="76" y1="143" x2="188" y2="143" stroke={green} strokeWidth="2"/><path d="M76 143 l8 -4 v8z M188 143 l-8 -4 v8z" fill={green}/>
    <text x="99" y="160" style={{...text, fill:green}}>shot width</text>
    <line x1="57" y1="42" x2="57" y2="124" stroke={purple} strokeWidth="2"/><path d="M57 42 l-4 8 h8z M57 124 l-4 -8 h8z" fill={purple}/>
    <text x="20" y="88" transform="rotate(-90 20 88)" style={{...text, fill:purple}}>shot height</text>
    <circle cx="220" cy="92" r="5" fill={red}/><line x1="220" y1="92" x2="286" y2="83" stroke={red} strokeDasharray="4 3"/><text x="240" y="110" style={{...small, fill:red}}>Δcenter</text>
  </>;
  if (kind === "teg") drawing = <>
    <circle cx="130" cy="90" r="70" fill="none" stroke={muted} strokeWidth="2"/><circle cx="130" cy="90" r="61" fill="none" stroke={gold} strokeDasharray="5 4"/>
    <line x1="60" y1="90" x2="200" y2="90" stroke={muted}/><line x1="130" y1="20" x2="130" y2="160" stroke={muted}/>
    <rect x="150" y="49" width="34" height="25" fill="rgba(62,123,214,.15)" stroke={blue}/><circle cx="167" cy="62" r="3" fill={blue}/>
    <rect x="167" y="53" width="12" height="5" fill="rgba(47,158,99,.55)" stroke={green}/><circle cx="167" cy="58" r="2.5" fill={red}/>
    <line x1="167" y1="62" x2="167" y2="58" stroke={green} strokeWidth="2"/><text x="185" y="54" style={{...small, fill:green}}>ebeam offset</text>
    <line x1="130" y1="90" x2="167" y2="58" stroke={red} strokeDasharray="5 3"/><text x="140" y="82" style={{...small, fill:red}}>abs radius</text>
    <g transform="translate(242 30)"><rect width="170" height="120" rx="6" fill="rgba(62,123,214,.06)" stroke={blue}/><line x1="85" y1="15" x2="85" y2="105" stroke={muted}/><line x1="25" y1="60" x2="145" y2="60" stroke={muted}/><rect x="102" y="39" width="42" height="10" fill="rgba(47,158,99,.55)" stroke={green}/><circle cx="102" cy="49" r="3" fill={red}/><text x="91" y="76" style={small}>shot center (0,0)</text><text x="108" y="33" style={{...small, fill:green}}>TEG 좌하단</text></g>
  </>;
  if (kind === "vertical") drawing = <>
    <g transform="translate(28 24)"><line x1="68" y1="112" x2="68" y2="12" stroke={muted}/><line x1="18" y1="62" x2="140" y2="62" stroke={muted}/><rect x="80" y="50" width="48" height="12" fill="rgba(62,123,214,.45)" stroke={blue}/><text x="82" y="45" style={{...text, fill:blue}}>Horizontal</text><text x="120" y="78" style={small}>(x, y)</text></g>
    <path d="M175 52 C220 8 270 15 292 53" fill="none" stroke={purple} strokeWidth="3"/><path d="M292 53 l-10 -2 5 -8z" fill={purple}/><text x="199" y="18" style={{...text, fill:purple}}>시계 90° 원복</text>
    <g transform="translate(278 24)"><line x1="68" y1="112" x2="68" y2="12" stroke={muted}/><line x1="18" y1="62" x2="140" y2="62" stroke={muted}/><rect x="68" y="14" width="12" height="48" fill="rgba(138,95,208,.45)" stroke={purple}/><text x="86" y="35" style={{...text, fill:purple}}>Vertical(R)</text><text x="83" y="78" style={small}>(y, -x)</text></g>
    <text x="105" y="155" style={text}>V(R) 비교: (x,y) → (y,-x)</text><text x="275" y="155" style={text}>역산: (X,Y) → (-Y,X)</text>
    <text x="118" y="173" style={{...small, fill:red}}>90° = V(L, 노치 왼쪽) · 위 V(R) 식과 별도</text>
  </>;
  if (kind === "full") {
    const cells = [];
    for (let r=0; r<7; r++) for (let c=0; c<11; c++) {
      const x=55+c*30, y=22+r*23, axisCell=c===5 || r===3;
      cells.push(<rect key={`${r}-${c}`} x={x} y={y} width="28" height="21" fill={axisCell ? "transparent" : "rgba(62,123,214,.10)"} stroke={axisCell ? red : blue} strokeWidth={axisCell ? 1.2 : .7} strokeDasharray={axisCell ? "4 3" : "none"} opacity={axisCell ? .45 : 1}/>);
    }
    drawing=<>{cells}<circle cx="219" cy="91" r="82" fill="none" stroke={ink} strokeWidth="2"/><circle cx="219" cy="91" r="4" fill={red}/><text x="230" y="104" style={{...small, fill:red}}>anchor 근처 center</text><text x="60" y="174" style={{...text, fill:blue}}>점선/연장 격자 = synthetic shot</text><text x="273" y="174" style={{...text, fill:red}}>빨간 0축 = 표시 제외</text></>;
  }
  return <svg viewBox="0 0 440 180" role="img" aria-label={`${kind} 계산 도식`} style={{ width: "100%", maxHeight: 210, display: "block", margin: "4px 0 12px", borderRadius: 7, border: "1px solid var(--line)", background: "var(--bg-primary)" }}>{drawing}</svg>;
}

/* 관리자 전용: 실제 구현과 같은 계산식을 한곳에서 설명한다. */
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


function CalculationGuide({ data }) {
  const geo = data?.geometry;
  const model = data?.coordinate_model || {};
  const panel = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--bg-card)", fontSize: 13, lineHeight: 1.75 };
  const formula = { fontFamily: "monospace", color: "var(--accent)" };
  const sections = [
    ["1. Chip_Radius → 실center", "fit", <>
      shot 격자좌표를 <span style={formula}>(x,y)</span>, wafer 중심을 <span style={formula}>(cx,cy)</span>,
      격자→mm 배율을 <span style={formula}>(kx,ky)</span>로 두고
      <span style={formula}> r²=kx²(x-cx)²+ky²(y-cy)²</span>를 선형 최소자승 fit합니다.
      최소 6개 shot이 필요하고, 잔차가 큰 Chip_Radius 행은 최대 20%까지 제외해 재fit합니다.
      {geo?.fit === "radius" && <small style={{ display: "block", color: "var(--muted)" }}>
        현재 center=({fmt(geo.cx, 6)}, {fmt(geo.cy, 6)}), scale=({fmt(geo.kx, 6)}, {fmt(geo.ky, 6)}) mm/격자
      </small>}
    </>],
    ["2. shot width / height · center 차이", "size", <>
      축별 고유 shot 좌표의 양수 간격 중앙값을 pitch로 잡아
      <span style={formula}> width=pitch_x×kx</span>, <span style={formula}>height=pitch_y×ky</span>로 계산합니다.
      shot 실좌표는 <span style={formula}>mm_x=(x-cx)kx, mm_y=(y-cy)ky</span>입니다.
      가장 radius가 작은 실제 shot의 center 차이를 <span style={formula}>Δx=mm_x×1000, Δy=-mm_y×1000 µm</span>로 표시합니다.
      layout y가 아래쪽 양수라 화면 Cartesian y에는 음수가 붙습니다.
      {geo?.fit === "radius" && <small style={{ display: "block", color: "var(--muted)" }}>
        현재 shot={fmt(geo.shot_w_mm, 4)}×{fmt(geo.shot_h_mm, 4)} mm, pitch=({fmt(geo.pitch_x, 4)}, {fmt(geo.pitch_y, 4)})
      </small>}
    </>],
    ["3. TEG 절대 위치 · edge 판정", "teg", <>
      ebeam 좌표는 shot center (0,0) 기준 TEG 좌하단입니다.
      <span style={formula}> abs_x=mm_x+ebeam_x, abs_y=-mm_y+ebeam_y</span>,
      <span style={formula}> radius=√(abs_x²+abs_y²)</span>로 계산합니다. TEG 사각형 네 꼭짓점이
      최외곽선 안에 모두 있는지 검사해 shot을 초록/빨강으로 표시합니다. SVG는 y가 아래로 증가하므로 렌더링 때만 다시 뒤집습니다.
    </>],
    ["4. R/L 회전과 동일 형상 기준", "vertical", <>
      direction(flat_zone)을 먼저 읽어 V/Vertical/v_R/270°는 Vertical(R), H/Horizontal/0°/180°는 horizontal로 봅니다.
      <span style={formula}> 90°는 Vertical(L, v_L)</span>로 별도 처리하고, 두 vertical 모두 Horizontal 표준 좌표로 정규화합니다.
      값이 없으면 TEG 이름의 <span style={formula}>V_</span> 접두로 역산합니다.
      파일의 teg_w/teg_h는 이미 실제 배치 방향이므로 다시 교환하지 않고, 크기 열 둘 다 없어 기본 가로 크기를 쓸 때만 vertical이면 width/height를 교환합니다.
      <span style={formula}> R_H(u,v)=(u,v), R_R(u,v)=(v,-u), R_L(u,v)=(-v,u)</span>입니다.
      PCHK와 대상 TEG는 같은 기준점 형상으로 보고, 다른 제품의 위치 차이는 제품 H/R/L ΔX·ΔY로 조정합니다.
      w/h는 사각형 크기와 die 겹침에만 반영되고 좌표 원점을 이동시키지 않습니다.
    </>],
    ["5. 전체 보정 수식 · 현재 제품값", "teg", <>
      검사 계산은 <span style={formula}>{model.formula?.inspect || "Ocalc=Obase+R(flat)·p+Cproduct+Kglobal+Kproduct"}</span>입니다.
      좌표 생성은 이 식의 정확한 역함수를 사용합니다.
      <small style={{ display: "block", color: "var(--muted)", whiteSpace: "pre-wrap" }}>
        현재 global base: {JSON.stringify(model.global_flat_base || {})}{"\n"}
        현재 제품 flat 보정: {JSON.stringify(model.product_flat_corrections || {})}{"\n"}
        TEG 보정 규칙: global {model.global_module_count || 0}개 + product {model.product_module_count || 0}개{"\n"}
        형상 정책: {model.shape_policy || "w/h는 크기·겹침에만 사용, 위치 차이는 제품 ΔX/ΔY로 보정"}
      </small>
    </>],
    ["6. full shot", "full", <>
      wafer 중심에 가장 가까운 실제 shot을 anchor로 동일 pitch 격자를 연장하고, shot 사각형이 wafer 원과 면적으로 겹치는 자리만 synthetic shot으로 만듭니다.
      layout에 없는 자리는 점선이며, full shot에서는 <span style={formula}>shot x=0 또는 y=0</span>인 축상의 shot을 표시하지 않습니다.
    </>],
  ];
  return <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: 12 }}>
    {sections.map(([title, kind, body]) => <div key={title} style={panel}><b style={{ display: "block", marginBottom: 6 }}>{title}</b><CalculationDiagram kind={kind}/>{body}</div>)}
  </div>;
}

function InlineShotPicker({ data, selected, onToggle, tableName="" }) {
  if (!data?.shots?.length) return <EmptyState icon="⌖" title="제품 map이 없습니다" hint="상단에서 제품을 선택해 주세요" />;
  const geo = data.geometry || {}, mmMode = geo.fit === "radius";
  const size = 620, pad = 30, shots = data.shots;
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
      {namedCount !== selected.size && <span style={{ color: "var(--warn)" }}>■ 이름 미입력 {selected.size - namedCount}</span>}
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
        const clipped = name.length > 20 ? `${name.slice(0, 19)}…` : name;
        const rows = clipped.length > 11 ? [clipped.slice(0, 11), clipped.slice(11)] : [clipped];
        const labelSize = Math.max(8, Math.min(12, shotPxH / (rows.length + 0.5), shotPxW / Math.max(5, Math.min(11, clipped.length || 5)) * 1.55));
        return <g key={key} onClick={() => onToggle(s)} style={{ cursor: "pointer" }}>
          <rect x={p.x - shotPxW / 2} y={p.y - shotPxH / 2} width={shotPxW} height={shotPxH} rx="1.5"
            fill={on ? (named ? "rgba(62,123,214,.48)" : "rgba(199,138,30,.35)") : "rgba(62,123,214,.08)"}
            stroke={on ? (named ? "#3e7bd6" : "#c78a1e") : "var(--muted)"} strokeWidth={on ? 2.2 : .7} />
          {on && <>
            <circle cx={p.x} cy={p.y - shotPxH / 2 + 4} r="2.6" fill={named ? "#3e7bd6" : "#c78a1e"} pointerEvents="none" />
            <text x={p.x} y={p.y - ((rows.length - 1) * labelSize) / 2} textAnchor="middle" dominantBaseline="middle"
              fontSize={labelSize} fontWeight="800" fill="var(--text-primary)" stroke="var(--bg-primary)" strokeWidth="3"
              paintOrder="stroke" strokeLinejoin="round" pointerEvents="none">
              {(named ? rows : ["이름 입력"]).map((line, i) => <tspan key={i} x={p.x}
                dy={i === 0 ? 0 : labelSize * 1.05}>{line}</tspan>)}
            </text>
          </>}
          <title>{name ? `${name}\n` : ""}shot (${s.x}, ${s.y}){on && !named ? "\n이름을 입력해 주세요" : ""}</title>
        </g>;
      })}
    </svg>
  </div>;
}

/* 관리자 전용 Inline ↔ ET 좌표 매칭 기준 TABLE 편집기. */
function InlineMapSetting({ data, vehicle, onVehicleChange }) {
  const [tables, setTables] = useState([]), [tableName, setTableName] = useState("");
  const [selected, setSelected] = useState(new Map()), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [activeTableName, setActiveTableName] = useState(""), [dirty, setDirty] = useState(false);
  const draftVehicleRef = useRef(vehicle || "");
  const refresh = useCallback(async () => {
    try { const r = await sf(`${API}/inline-map-settings`); setTables(r.tables || []); setError(""); }
    catch (e) { setError(String(e.message || e)); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (!vehicle) return;
    if (!draftVehicleRef.current) { draftVehicleRef.current = vehicle; return; }
    if (draftVehicleRef.current === vehicle) return;
    draftVehicleRef.current = vehicle;
    setTableName("");
    setActiveTableName("");
    setSelected(new Map());
    setDirty(false);
  }, [vehicle]);
  const toggle = s => {
    const key = gridKey(Number(s.x), Number(s.y));
    setSelected(prev => { const next = new Map(prev); next.has(key) ? next.delete(key) : next.set(key, ""); return next; });
    setDirty(true);
  };
  const rename = (key, value) => { setSelected(prev => { const next = new Map(prev); next.set(key, value); return next; }); setDirty(true); };
  const chosen = useMemo(() => (data?.shots || []).filter(s => selected.has(gridKey(Number(s.x), Number(s.y)))), [data, selected]);
  const load = table => {
    draftVehicleRef.current = table.vehicle;
    setTableName(table.table_name);
    setActiveTableName(table.table_name);
    setDirty(false);
    setSelected(new Map((table.shots || []).map(s => [gridKey(s.shot_x, s.shot_y), s.name || ""])));
    if (table.vehicle !== vehicle) onVehicleChange(table.vehicle);
  };
  const save = async () => {
    const shots = chosen.map(s => ({ shot_x: Number(s.x), shot_y: Number(s.y), name: String(selected.get(gridKey(Number(s.x), Number(s.y))) || "").trim() }));
    if (!vehicle) return toast.error("제품을 먼저 선택해 주세요");
    if (!tableName.trim()) return toast.error("TABLE 이름을 입력해 주세요");
    if (!shots.length || shots.some(s => !s.name)) return toast.error("선택한 모든 shot의 이름을 입력해 주세요");
    setBusy(true);
    try {
      const cleanName = tableName.trim();
      const r = await putJson(`${API}/inline-map-settings`, { table_name: cleanName, vehicle, shots });
      const saved = (r.tables || []).find(t => t.table_name === cleanName);
      setTables(r.tables || []);
      setTableName(cleanName);
      setActiveTableName(cleanName);
      setSelected(new Map(((saved && saved.shots) || shots).map(s => [gridKey(s.shot_x, s.shot_y), s.name || ""])));
      setDirty(false);
      setError("");
      toast.ok("Inline map 설정 저장됨 · WF MAP에 위치와 이름이 표시됩니다");
    }
    catch (e) { setError(String(e.message || e)); toast.error(String(e.message || e)); }
    finally { setBusy(false); }
  };
  const remove = async table => {
    if (!window.confirm(`${table.table_name} TABLE을 삭제할까요?`)) return;
    setBusy(true);
    try {
      const r = await sf(`${API}/inline-map-settings?table_name=${encodeURIComponent(table.table_name)}`, { method: "DELETE" });
      setTables(r.tables || []); if (tableName === table.table_name) { setTableName(""); setActiveTableName(""); setSelected(new Map()); setDirty(false); } toast.ok("TABLE 삭제됨");
    } catch (e) { setError(String(e.message || e)); toast.error(String(e.message || e)); }
    finally { setBusy(false); }
  };
  return <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
    {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}
    <Card title={`Inline map setting — ${vehicle || "제품 미선택"}`} right={<Pill tone="warn">global admin only · DB/credential</Pill>}>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>제품 map의 shot을 선택하고 위치 이름을 입력한 뒤 TABLE 이름으로 저장합니다. 이후 Inline 위치좌표와 ET 좌표 매칭 기준으로 사용합니다.</div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(360px,2fr) minmax(280px,1fr)", gap: 14, alignItems: "start" }}>
        <InlineShotPicker data={data} selected={selected} onToggle={toggle} tableName={tableName.trim()} />
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <b style={{ fontSize: 12 }}>TABLE 이름</b>
          <input value={tableName} onChange={e => { setTableName(e.target.value); setDirty(true); }} placeholder="예: INLINE_MAP_PROD_A" style={inputStyle} />
          <b style={{ fontSize: 12, marginTop: 4 }}>선택 shot ({chosen.length})</b>
          <div style={{ maxHeight: 430, overflowY: "auto", display: "flex", flexDirection: "column", gap: 5 }}>
            {chosen.map(s => { const key = gridKey(Number(s.x), Number(s.y)); return <div key={key} style={{ display: "grid", gridTemplateColumns: "90px 1fr 22px", gap: 5, alignItems: "center" }}>
              <span style={{ fontFamily: "monospace", fontSize: 12 }}>({s.x}, {s.y})</span>
              <input value={selected.get(key) || ""} onChange={e => rename(key, e.target.value)} placeholder="위치 이름" style={{ ...inputStyle, minWidth: 0 }} />
              <button onClick={() => toggle(s)} title="선택 해제" style={{ border: 0, background: "transparent", color: "var(--danger)", cursor: "pointer" }}>×</button>
            </div>; })}
            {!chosen.length && <span style={{ color: "var(--muted)", fontSize: 12 }}>왼쪽 map에서 shot을 선택하세요.</span>}
          </div>
          <Button variant="primary" disabled={busy} onClick={save}>{busy ? "저장 중…" : "TABLE 저장"}</Button>
          {activeTableName && <div style={{ fontSize: 11, color: dirty ? "var(--warn)" : "var(--ok)" }}>
            {dirty ? `${activeTableName} · 저장되지 않은 변경 있음` : `${activeTableName} · 저장된 위치/이름 표시 중`}
          </div>}
        </div>
      </div>
    </Card>
    <Card title={`저장된 TABLE (${tables.length})`}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {tables.map(table => <div key={table.table_name} style={{ display: "flex", alignItems: "center", gap: 8,
          border: `1px solid ${activeTableName === table.table_name ? "#3e7bd6" : "var(--line)"}`, borderRadius: 6, padding: "7px 9px",
          background: activeTableName === table.table_name ? "rgba(62,123,214,.08)" : "transparent" }}>
          <b>{table.table_name}</b><Pill tone="neutral">{table.vehicle}</Pill><span style={{ color: "var(--muted)", fontSize: 12 }}>{table.shots?.length || 0} shots · {table.updated_by || "-"}</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 5 }}><Button onClick={() => load(table)}>{table.vehicle === vehicle ? "불러오기" : `${table.vehicle} 선택`}</Button><Button variant="danger" disabled={busy} onClick={() => remove(table)}>삭제</Button></span>
        </div>)}
        {!tables.length && <span style={{ color: "var(--muted)", fontSize: 12 }}>저장된 TABLE이 없습니다.</span>}
      </div>
    </Card>
  </div>;
}

export default function My_TegMap({ user }) {
  const [vehicles, setVehicles] = useState(null);   // null=로딩, []=없음
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

  const canEdit = user?.role === "admin" || (user?.page_manager || []).includes("teg");
  const tegPageTokens = Array.isArray(user?.tabs)
    ? user.tabs
    : String(user?.tabs || "").split(",");
  const canEditReferenceFiles = canEdit || tegPageTokens.some(token => String(token || "").trim().split(":")[0] === "teg");

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

  const tegNames = useMemo(() => (data?.tegs || []).map(t => t.teg), [data]);
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
  // full shot — 파일에 있는 shot 은 그대로 두고, 같은 크기로 wafer 를 덮는 자리를 덧붙인다.
  // 지도와 radius 표가 같은 목록을 보게 한 곳에서 만들어 둘 다에 넘긴다.
  const mapData = useMemo(() => {
    if (!data || (!fullShot && !fullChip)) return data;
    const shots = buildFullShots(data);
    return shots === data.shots ? data : { ...data, shots };
  }, [data, fullShot, fullChip]);
  const addedShots = (mapData?.shots || []).filter(s0 => s0.synthetic).length;
  const fullChipResult = useMemo(() => fullChip
    ? buildFullChipDies(mapData, fullChipCells)
    : { dies: [], overflow: false }, [fullChip, mapData, fullChipCells]);
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
        items={[{ k: "map", l: "위치 조회" }, { k: "check", l: "TEG Mapfile 체크" },
                { k: "gen", l: "Mapfile용 좌표 생성" },
                { k: "files", l: "기준 파일" },
                ...(canEdit ? [{ k: "logic", l: "계산 로직" }] : []),
                ...(user?.role === "admin" ? [
                  { k: "inline", l: "Inline map setting" },
                ] : [])]} />

      {view === "check" && <TegCheck vehicle={vehicle} />}
      {view === "gen" && <TegGenerate vehicle={vehicle} />}
      {view === "files" && <ReferenceFiles user={user} canEdit={canEditReferenceFiles} onSaved={() => { loadVehicles(); loadMap(); }} />}
      {view === "logic" && canEdit && <CalculationGuide data={data} />}
      {view === "inline" && user?.role === "admin" &&
        <InlineMapSetting data={data} vehicle={vehicle} onVehicleChange={setVehicle} />}

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
                <label style={{
                  display: "flex", alignItems: "center", gap: 4, fontSize: 12,
                  color: geo?.fit === "radius" ? "var(--text-primary)" : "var(--muted)",
                  cursor: geo?.fit === "radius" ? "pointer" : "not-allowed",
                }}
                  title={geo?.fit === "radius"
                    ? "layout 파일에 없는 자리까지 같은 shot 크기로 격자를 연장해 wafer 전체를 덮어 표시합니다."
                      + " 추가된 자리는 점선입니다 (꼭짓점만 스치는 자리는 제외)."
                    : "Chip_Radius fit 이 되어야 shot 크기를 알 수 있어 사용할 수 없습니다"}>
                  <input type="checkbox" checked={fullShot} disabled={geo?.fit !== "radius"}
                    onChange={e => toggleFullShot(e.target.checked)} />
                  full shot
                  {fullShot && (addedShots > 0 ? (
                    <span style={{ color: "var(--muted)" }}>+{addedShots}</span>
                  ) : (
                    <span style={{ color: "var(--muted)" }}
                      title="더 채울 자리가 없거나(이미 wafer 를 덮음), shot 크기·pitch 로 계산한 격자가 너무 촘촘해 표시하지 않았습니다">
                      추가 없음
                    </span>
                  ))}
                </label>
                <Button variant={fullChip ? "primary" : "subtle"}
                  disabled={!fullChipAvailable}
                  onClick={() => setFullChip(v => !v)}
                  title={fullChipAvailable
                    ? `칩/개발 격자의 die를 모든 shot에 펼치고 네 꼭짓점이 최외곽 ${fmt(geo?.wafer_edge_mm, 0)}mm 안인 die만 표시합니다.`
                    : display.mode === "dev_grid"
                      ? "개발 격자 MAIN die가 없거나 Main_chip_info 크기를 확인 중입니다."
                      : "칩 격자 또는 개발 격자 제품에서만 사용할 수 있습니다."}>
                  full chip{fullChip ? ` ${fullChipResult.dies.length}` : ""}
                </Button>
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
                    display.mode === "image" ? "그림" :
                    display.mode === "dev_grid" ? "개발 격자" : "기본"}
                </Pill>
              </div>
            }>
            {/* 범례 — maxWidth 없이 두면 flex 아이템(카드)이 이 한 줄의 max-content 폭으로
                부풀어 shot 확대 쪽이 밀리고 옆이 통째로 빈다. 지도 폭에 맞춰 접는다. */}
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, maxWidth: 640 }}>
              shot 클릭 → 확대 뷰. 실선 원 = wafer {fmt(geo?.wafer_radius_mm, 0)}mm,
              점선 원 = 최외곽 {fmt(geo?.wafer_edge_mm, 0)}mm.
              TEG 선택 시: <span style={{ color: "#2f9e63", fontWeight: 700 }}>■ 초록</span> = TEG 전체가 최외곽 안,
              <span style={{ color: "#e05252", fontWeight: 700 }}> ■ 빨강</span> = TEG 가 걸림.
              미선택 시: <span style={{ color: "#3e7bd6", fontWeight: 700 }}>■ 연파랑</span> = shot 전체가 안,
              <span style={{ color: "#c78a1e", fontWeight: 700 }}> ■ 연노랑</span> = shot 이 걸치거나 밖.
              격자/그림은 shot 확대에서만 표시됩니다.
              {fullShot && (
                <> <b>full shot</b>: 점선 = layout 파일에 없는 자리(격자 연장).
                  TEG 선택 시 판정색은 같고, 미선택 시 걸치는 자리는 색을 칠하지 않습니다.</>
              )}
              {fullChip && (
                <> <b> full chip</b>: 칩/개발 격자의 die 중 네 꼭짓점이 모두 최외곽 {fmt(geo?.wafer_edge_mm, 0)}mm 안인
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
                      <div style={{ fontWeight: 700, color: "var(--muted)", marginBottom: 2 }}>Chip_Radius 계산 정보</div>
                      <div>shot 크기: <b>{fmt(geo.shot_w_mm, 3)} × {fmt(geo.shot_h_mm, 3)} mm</b></div>
                      {nearestShot && (
                        <>
                          <div>가장 가까운 샷: <b style={{ color: "#e05252" }}>({nearestShot.x}, {nearestShot.y})</b>
                            <span style={{ color: "var(--muted)", marginLeft: 4 }}>(빨간 점)</span></div>
                          <div title="실center(wafer 중심)에서 가장 가까운 샷 센터로 이동하는 Δx, Δy. x 우측↑, y 위↑ 양수.">
                            실center 차이: <b>Δx {fmt(nearestShot.mm_x * 1000, 1)} · Δy {fmt(-nearestShot.mm_y * 1000, 1)} µm</b>
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
                        title={`ebeam_x ${fmt(t.ebeam_x)} · ebeam_y ${fmt(t.ebeam_y)} mm (shot 센터 기준 TEG 좌하단)`
                          + `\n방향 ${directionLabel(t)} — ${isVertical(t) ? "세움" : "가로"} · `
                          + `${fmt(t.teg_w, 3)} × ${fmt(t.teg_h, 3)} mm\n${DIR_TIP}`}
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
          onSaved={() => { loadVehicles(); loadMap(); }} />
      </PageGear>
    </div>
  );
}
