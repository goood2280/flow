import { useState, useEffect, useMemo, useRef } from "react";
import Loading from "../../components/Loading";
import { Button, statusPalette, buildSeriesColors, SERIES_COLOR_LIMIT } from "../../components/UXKit";
import { FlowPlotlyChart, WipStackedBar } from "../../components/PlotlyChart";
import { PageGearButton } from "../../components/PageGear";
import ProductOrderEditor from "../../components/ProductOrderEditor";
import { toast } from "../../components/Toast";
import { sf } from "../../lib/api";
import { useUserRole } from "../../lib/permissions";
import { mergeProductOrder } from "../../lib/productOrder";

// v9.2 (2026-07-12): 대시보드 = WIP × Split 현황 단일 화면.
// 기존 차트 보드(저장 차트 그리드/에디터/스케줄러 UI)는 퇴역 —
// 원본 전체는 archive/dashboard_chartboard_2026_07_12/My_Dashboard.full.jsx 참조.
// 차트 보드용 백엔드 API(/api/dashboard/charts 등)는 아직 남아 있다.
const API = "/api/dashboard";
const BAD = statusPalette.bad;

// 차트가 "접히는 선(fold)" 위에서 끝나도록 남은 높이를 실측해 넘긴다.
// 기준은 뷰포트가 아니라 스크롤 컨테이너 안에서의 위치(offset) 다 — 뷰포트
// 기준으로 재면 아래로 스크롤할수록 top 이 작아져 차트가 계속 길어진다.
// 상단 필터바 줄바꿈/데이터 도착으로 카드가 내려가도 따라가야 하는데
// ResizeObserver 는 위치 변화를 알려주지 않아 200ms 폴링으로 재고,
// 값이 실제로 바뀔 때만 갱신한다.
function scrollParent(el) {
  for (let p = el?.parentElement; p; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY;
    if (oy === "auto" || oy === "scroll") return p;
  }
  return null;
}
// scale: fold 까지 남은 높이를 그대로 쓰지 않고 그 비율만큼만 쓴다 —
// 차트가 화면을 꽉 채우면 세로로 과하게 커서 구간 비교가 오히려 어렵다.
function useFoldHeight(ref, { min = 260, gap = 12, scale = 1 } = {}) {
  const [h, setH] = useState(420);
  useEffect(() => {
    let last = 0;
    let box = null; // 스크롤 부모는 마운트 후 고정 — 매 tick getComputedStyle 워크 방지
    const calc = () => {
      const el = ref.current;
      if (!el) return;
      if (!box || !box.isConnected) box = scrollParent(el);
      let offset;
      let viewH;
      if (box) {
        // 스크롤과 무관한 컨테이너 내부 오프셋.
        offset = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
        viewH = box.clientHeight;
      } else {
        offset = el.getBoundingClientRect().top + (window.scrollY || 0);
        viewH = window.innerHeight;
      }
      const next = Math.max(min, Math.round((viewH - offset - gap) * scale));
      if (Math.abs(next - last) > 2) { last = next; setH(next); }
    };
    calc();
    window.addEventListener("resize", calc);
    const timer = setInterval(calc, 500);
    return () => { window.removeEventListener("resize", calc); clearInterval(timer); };
  }, [ref, min, gap, scale]);
  return h;
}

/* ═══ WIP × Split 현황 (latest cache 기반) ═══ */
const WIP_BIN_CHOICES = [];
// X축 기준 — step_id 숫자 구간 / step_desc 앞머리 숫자(예: FAB_1.0 STI → 1.0).
const AXIS_CHOICES = [
  { value: "step_desc", label: "step_desc (앞 숫자)" },
  { value: "step_id", label: "step_id 구간" },
];
const NORM_CHOICES = [
  { value: "count", label: "물량" },
  { value: "percent", label: "비중 100%" },
];
const OTHER_PREFIX = "기타";
const EMPTY_ARR = [];
const nf = (n) => Number(n || 0).toLocaleString();

// 차트 높이 = fold 까지 남은 높이의 70%.
const CHART_HEIGHT_SCALE = 0.7;

// ── 화면 설정(톱니바퀴) ─────────────────────────────────────────────────
// 사용자별 로컬 설정이라 서버에 저장하지 않는다. 기본 제외 문자는 "Z" —
// Z 로 시작하는 root_lot 은 물량 통계에서 빼고 보는 것이 기본이다.
// 서버는 파라미터가 비면 아무것도 제외하지 않으므로 기본값은 여기가 유일한 출처다.
const SETTINGS_KEY = "flow_dashboard_settings";
const DEFAULT_SETTINGS = { excludeRootPrefix: "Z" };

function loadSettings() {
  try {
    const raw = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
    if (raw && typeof raw === "object") {
      return { ...DEFAULT_SETTINGS, ...raw, excludeRootPrefix: String(raw.excludeRootPrefix ?? DEFAULT_SETTINGS.excludeRootPrefix) };
    }
  } catch { /* 손상된 값은 기본값으로 */ }
  return { ...DEFAULT_SETTINGS };
}
function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch { /* 저장 실패는 무시 */ }
}

const microLabel = { fontSize: 10, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--text-secondary)", fontWeight: 600 };
const cardStyle = { border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" };
const numFont = { fontVariantNumeric: "tabular-nums" };
const selStyle = {
  padding: "5px 8px", border: "1px solid var(--border)", borderRadius: 7,
  background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 12.5, minWidth: 118,
};
const th = { padding: "6px 10px", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: "var(--bg-secondary)", fontWeight: 600, fontSize: 11, color: "var(--text-secondary)" };
const td = { padding: "5px 10px", borderBottom: "1px solid var(--border)" };

// 채움색 위에 얹는 글자색 — 밝은 색이면 잉크, 어두운 색이면 흰색.
function inkOn(hex) {
  const h = String(hex || "").replace("#", "");
  if (h.length !== 6) return "#fff";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.55 ? "#171717" : "#ffffff";
}

function FilterField({ children, title, dim = false }) {
  return (
    <label style={{ display: "grid", gap: 3, opacity: dim ? 0.45 : 1 }}>
      <span style={microLabel}>{title}</span>
      {children}
    </label>
  );
}

function Stat({ title, value, sub }) {
  return (
    <div style={{ display: "grid", gap: 1 }}>
      <span style={microLabel}>{title}</span>
      <span style={{ fontSize: 16, fontWeight: 650, lineHeight: 1.15, color: "var(--text-primary)", ...numFont }}>{value}</span>
      {sub ? <span style={{ fontSize: 10.5, color: "var(--text-secondary)", ...numFont }}>{sub}</span> : null}
    </div>
  );
}

function Dot({ color }) {
  return <span style={{ width: 8, height: 8, borderRadius: 2, background: color, display: "inline-block", flex: "0 0 auto" }} />;
}

function Segmented({ value, onChange, options }) {
  return (
    <div style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 7, overflow: "hidden" }}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          style={{
            padding: "3px 10px", fontSize: 11.5, cursor: "pointer", border: "none",
            background: value === o.value ? "var(--bg-hover)" : "transparent",
            color: value === o.value ? "var(--text-primary)" : "var(--text-secondary)",
            fontWeight: value === o.value ? 650 : 400,
          }}
        >{o.label}</button>
      ))}
    </div>
  );
}

// Split 기준 열 선택 — 열이 수백 개라 네이티브 select 로는 찾기가 어렵다.
// 입력창은 검색 필터, 그 아래는 지금까지처럼 그룹(KNOB/MASK/FAB/기타)별 스크롤 목록.
function SplitColSelect({ value, groups, onChange, style }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  // 필터는 그룹 구조를 유지한 채 걸고, 키보드 이동용으로 평평한 목록도 같이 만든다.
  const { shown, flat } = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out = [];
    const list = [];
    for (const [g, cols] of Object.entries(groups || {})) {
      const hit = needle ? cols.filter((c) => String(c).toLowerCase().includes(needle)) : cols;
      if (!hit.length) continue;
      out.push([g, hit]);
      list.push(...hit);
    }
    return { shown: out, flat: list };
  }, [groups, q]);

  // 필터가 바뀌면 하이라이트는 항상 첫 항목으로.
  useEffect(() => { setHi(0); }, [q, open]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!boxRef.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const pick = (c) => {
    setOpen(false);
    setQ("");
    inputRef.current?.blur();
    if (c !== value) onChange(c);
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      const n = flat.length;
      if (!n) return;
      setHi((i) => (i + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
    } else if (e.key === "Enter") {
      if (e.nativeEvent?.isComposing || e.keyCode === 229) return; // 한글 조합 중 Enter 무시
      e.preventDefault();
      if (open && flat[hi]) pick(flat[hi]);
    } else if (e.key === "Escape") {
      setOpen(false);
      setQ("");
      inputRef.current?.blur();
    }
  };

  return (
    <div ref={boxRef} style={{ position: "relative", ...style }}>
      <input
        ref={inputRef}
        value={open ? q : (value || "")}
        placeholder={open ? (value || "열 이름 검색") : "열 선택"}
        onFocus={() => { setOpen(true); setQ(""); }}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onKeyDown={onKeyDown}
        style={{ ...selStyle, width: "100%", minWidth: 0, boxSizing: "border-box", cursor: open ? "text" : "pointer" }}
      />
      {open && (
        <div
          // FilterField 가 <label> 이라 목록 클릭이 label 로 올라가면 input 이 다시
          // 포커스를 받아 방금 닫은 드롭다운이 곧바로 열린다 — 여기서 끊는다.
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute", top: "calc(100% + 3px)", left: 0, minWidth: "100%", width: "max-content", maxWidth: 420,
            maxHeight: 260, overflow: "auto", zIndex: 40, ...cardStyle, boxShadow: "0 6px 18px rgba(0,0,0,.18)",
          }}
        >
          {shown.length === 0 ? (
            <div style={{ padding: "8px 10px", fontSize: 12, color: "var(--text-secondary)" }}>검색 결과 없음</div>
          ) : shown.map(([g, cols]) => (
            <div key={g}>
              <div style={{ ...microLabel, padding: "5px 10px 3px", position: "sticky", top: 0, background: "var(--bg-secondary)" }}>{g}</div>
              {cols.map((c) => {
                const idx = flat.indexOf(c);
                const active = idx === hi;
                return (
                  <div
                    key={c}
                    ref={active ? (el) => el?.scrollIntoView({ block: "nearest" }) : undefined}
                    onMouseEnter={() => setHi(idx)}
                    onMouseDown={(e) => { e.preventDefault(); pick(c); }}
                    style={{
                      padding: "5px 10px", fontSize: 12.5, cursor: "pointer", whiteSpace: "nowrap",
                      background: active ? "var(--bg-hover)" : "transparent",
                      color: c === value ? "var(--text-primary)" : "var(--text-secondary)",
                      fontWeight: c === value ? 650 : 400,
                    }}
                  >{c}</div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// 대시보드 화면 설정 — 지금은 root_lot_id 제외 하나뿐이지만, 앞으로 늘어날
// "이 화면을 어떻게 볼지" 항목의 단일 진입점이다.
function SettingsMenu({ excludeRootPrefix, onChangeExclude, excludedWafers = 0, canManage = false, products = [], productOrder = [], onSaveProductOrder, productOrderBusy = false }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(excludeRootPrefix);
  const boxRef = useRef(null);
  const active = !!String(excludeRootPrefix || "").trim();

  // 밖에서 값이 바뀌거나 패널을 다시 열면 초안을 현재 값으로 되돌린다.
  useEffect(() => { setDraft(excludeRootPrefix); }, [excludeRootPrefix, open]);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!boxRef.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // 타이핑마다 다시 조회하면 안 되므로 Enter/포커스 아웃에서만 반영한다.
  const apply = (v) => {
    const next = String(v ?? "").trim();
    setDraft(next);
    if (next !== String(excludeRootPrefix || "")) onChangeExclude(next);
  };

  return (
    <div ref={boxRef} style={{ position: "relative", display: "flex", alignItems: "center" }}>
      {/* 톱니는 공용 PageGearButton 으로 통일한다 (40x40 원형 ⚙️).
          예전엔 이 페이지만 30x30 라운드 사각 + SVG 아이콘이라 혼자 달라 보였다. */}
      <span style={{ position: "relative", display: "inline-flex" }}>
        <PageGearButton
          title="대시보드 설정"
          position="inline"
          onClick={() => setOpen((v) => !v)}
        />
        {/* 필터가 켜져 있으면 패널을 열지 않아도 보이게 */}
        {active && (
          <span aria-hidden="true" style={{
            position: "absolute", top: 1, right: 1, width: 8, height: 8, borderRadius: "50%",
            background: "var(--accent)", border: "1px solid var(--bg-secondary)", pointerEvents: "none",
          }} />
        )}
      </span>
      {open && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute", top: "calc(100% + 6px)", right: 0, width: 380, zIndex: 50,
            padding: "10px 12px 12px", ...cardStyle, boxShadow: "0 6px 18px rgba(0,0,0,.18)",
          }}
        >
          <div style={{ fontSize: 12.5, fontWeight: 650, marginBottom: 8 }}>대시보드 설정</div>
          <label style={{ display: "grid", gap: 4 }}>
            <span style={microLabel}>ROOT_LOT_ID 제외 (시작 문자)</span>
            <div style={{ display: "flex", gap: 5 }}>
              <input
                value={draft}
                placeholder="예: Z"
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter") return;
                  if (e.nativeEvent?.isComposing || e.keyCode === 229) return; // 한글 조합 중 Enter 무시
                  e.preventDefault();
                  apply(draft);
                }}
                onBlur={() => apply(draft)}
                style={{ ...selStyle, flex: 1, minWidth: 0, boxSizing: "border-box" }}
              />
              <Button variant="subtle" onClick={() => apply("")} disabled={!active}>해제</Button>
            </div>
          </label>
          <div style={{ fontSize: 10.5, color: "var(--text-secondary)", marginTop: 6, lineHeight: 1.5 }}>
            해당 문자로 시작하는 root_lot_id를 집계에서 뺍니다. 대소문자는 구분하지 않고,
            콤마로 여러 문자를 넣을 수 있습니다(예: <code>Z,Y</code>). 비우면 제외하지 않습니다.
            {active && excludedWafers > 0 && (
              <div style={{ marginTop: 4, color: "var(--text-primary)", ...numFont }}>
                현재 {nf(excludedWafers)} wafer 제외 중
              </div>
            )}
          </div>
          {canManage && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
              <ProductOrderEditor products={products} productOrder={productOrder}
                onSave={onSaveProductOrder} busy={productOrderBusy}/>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WipSplitPanel({ user }) {
  const role = useUserRole(user);
  const canManage = role.canManagePage("dashboard");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [product, setProduct] = useState("");
  const [catalogProducts, setCatalogProducts] = useState([]);
  const [productOrder, setProductOrder] = useState([]);
  const [productOrderBusy, setProductOrderBusy] = useState(false);
  const [lotType, setLotType] = useState("ALL");
  const [binSize, setBinSize] = useState(30000);
  const [binInput, setBinInput] = useState("30000");
  const [splitCol, setSplitCol] = useState("");
  // 제품 '전체' — 서버가 split 조인을 건너뛰고 제품 자체를 색 구분 축으로 돌려준다.
  const isAllProducts = String(product || "").toUpperCase() === "ALL";
  const [axis, setAxis] = useState("step_desc");
  const [norm, setNorm] = useState("count");
  const [settings, setSettings] = useState(loadSettings);
  const excludeRootPrefix = settings.excludeRootPrefix;
  // 세그먼트 클릭 드릴다운 — 해당 구간×split 의 root_lot/wafer 목록.
  const [drill, setDrill] = useState(null);
  const [drillCopied, setDrillCopied] = useState(false);
  const dark = typeof document !== "undefined" && (document.documentElement.classList.contains("dark") || localStorage.getItem("hol_dark") === "true");
  const chartBoxRef = useRef(null);
  // gap = 차트 아래 안내문(≈14px) + 카드 하단 패딩/여백.
  // scale 0.7 = fold 까지 채우던 예전 높이의 70% (min 도 같은 비율로 낮춘다).
  const chartH = useFoldHeight(chartBoxRef, { gap: 28, scale: CHART_HEIGHT_SCALE, min: 200 });
  // 리본은 폭을 알아야 어느 조각에 이름을 넣을 수 있는지 정할 수 있다.
  const ribbonRef = useRef(null);
  const [ribbonW, setRibbonW] = useState(0);
  const [ribbonMounted, setRibbonMounted] = useState(false);
  useEffect(() => {
    // 리본은 데이터가 온 뒤에야 렌더되므로 mount 시점 한 번만 재면 항상 0 이다.
    const el = ribbonRef.current;
    if (!el) return undefined;
    const upd = () => setRibbonW(el.clientWidth || 0);
    upd();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(upd) : null;
    if (ro) ro.observe(el);
    window.addEventListener("resize", upd);
    return () => { if (ro) ro.disconnect(); window.removeEventListener("resize", upd); };
  }, [ribbonMounted]);

  // ex 는 톱니바퀴 설정값 — 기본 인자라 호출 시점의 현재 값이 그대로 쓰인다.
  const fetchData = (p, b, s, a, lt, ex = excludeRootPrefix) => {
    setLoading(true);
    setErr("");
    setDrill(null); // 필터가 바뀌면 이전 슬라이스의 드릴다운은 더 이상 맞지 않는다
    const q = new URLSearchParams();
    if (p) q.set("product", p);
    q.set("bin_size", String(b || 30000));
    if (s) q.set("split_col", s);
    q.set("axis", a || "step_desc");
    if (lt && lt !== "ALL") q.set("lot_type", lt);
    if (String(ex || "").trim()) q.set("exclude_root_prefix", String(ex).trim());
    sf(`${API}/wip-split?${q.toString()}`)
      .then((d) => {
        setData(d);
        setProduct(d.product || "");
        setLotType(d.lot_type || "ALL");
        setSplitCol(d.split_col || "");
        if (d.axis) setAxis(d.axis);
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    fetchData("", binSize, "", axis, lotType);
    // SplitTable·LOT 관리와 동일한 실제 ML_TABLE_* 파일 카탈로그를 제품 정본으로 쓴다.
    sf("/api/splittable/products").then(d => {
      const rows=(d.products||[]).map(p=>String(p?.name||"").replace(/^ML_TABLE_/i,"").trim()).filter(Boolean);
      const order=Array.isArray(d.product_order)?d.product_order:[];
      setProductOrder(order);
      setCatalogProducts(mergeProductOrder(rows,order));
    }).catch(()=>setCatalogProducts([]));
  }, []);

  // 자유 입력 bin 간격 적용 — 1~100000 정수로 보정, 같은 값 재입력은 무시.
  const applyBin = (val) => {
    const b = Math.max(1, Math.min(100000, Math.round(Number(val) || 0)));
    if (!b) { setBinInput(String(binSize)); return; }
    setBinInput(String(b));
    if (b === binSize) return;
    setBinSize(b);
    fetchData(product, b, splitCol, axis, lotType);
  };

  const bins = data?.bins || EMPTY_ARR;
  const splitValues = data?.split_values || EMPTY_ARR;
  const unassigned = data?.unassigned_label || "(미지정)";
  const grandTotal = data?.total_wafers || 0;
  const availableProducts = useMemo(() => mergeProductOrder([...(data?.products || EMPTY_ARR),...catalogProducts], productOrder), [data?.products,catalogProducts,productOrder]);

  // ── 차트용 계열 정리 ────────────────────────────────────────────────
  // 색은 20슬롯까지만 검증돼 있다(UXKit.categoricalSeries). 그보다 많으면 색을
  // 새로 만들지 않고 물량 하위를 "기타"로 접는다 — 원값은 아래 표에 그대로 남는다.
  // 색·스택 순서는 물량 순위가 아니라 "이름순"으로 고정한다: 필터를 바꿔도
  // 같은 split 값이 같은 색을 유지해야 읽는 사람이 헷갈리지 않는다.
  const chart = useMemo(() => {
    const named = splitValues.filter((v) => v !== unassigned);
    const keep = named.slice(0, SERIES_COLOR_LIMIT);           // 백엔드가 물량 내림차순으로 준다
    const keepSet = new Set(keep);
    const foldedCount = named.length - keep.length;
    const otherLabel = foldedCount ? `${OTHER_PREFIX} (${foldedCount}종)` : "";
    const series = [...keep].sort((a, b) => String(a).localeCompare(String(b)));
    if (otherLabel) series.push(otherLabel);
    if (splitValues.includes(unassigned)) series.push(unassigned);
    // x 눈금은 구간 시작값만 — "100000~100999" 는 세로로 40px 를 더 먹는다.
    // 원래 구간 표기는 hover(full)로 넘긴다.
    const cbins = bins.map((b) => {
      const splits = {};
      let rest = 0;
      for (const [k, v] of Object.entries(b.splits || {})) {
        if (k === unassigned || keepSet.has(k)) splits[k] = Number(v || 0);
        else rest += Number(v || 0);
      }
      if (rest && otherLabel) splits[otherLabel] = rest;
      const short = axis === "step_id" && Number(b.bin) >= 0 ? String(b.bin) : String(b.label);
      return { ...b, splits, label: short, full: b.label };
    });
    return { series, bins: cbins, otherLabel, foldedCount };
  }, [bins, splitValues, unassigned, axis]);

  const colorMap = useMemo(
    () => buildSeriesColors(chart.series, { dark, missingLabel: unassigned, otherLabel: chart.otherLabel }),
    [chart.series, chart.otherLabel, dark, unassigned],
  );
  const foldedColor = colorMap[chart.otherLabel] || "var(--text-secondary)";
  const colorOf = (v) => colorMap[v] || foldedColor;

  // 구분(KNOB/MASK/FAB)은 원본 ML_TABLE 의 열 접두어에서, '신규 캐시'는 FAB 매칭
  // 캐시가 제품마다 들고 있는 축(ppid/설비/챔버/레티클…)에서 온다. 신규 제품은
  // 원본에 split 열이 아직 없어 캐시 축만 뜨는 경우가 정상이다.
  const groupedSplitCols = useMemo(() => {
    const groups = { KNOB: [], MASK: [], FAB: [], "신규 캐시": [], 기타: [] };
    const options = data?.split_options;
    if (Array.isArray(options) && options.length) {
      for (const o of options) {
        const g = String(o?.group || "").toUpperCase();
        if (g === "KNOB") groups.KNOB.push(o.col);
        else if (g === "MASK") groups.MASK.push(o.col);
        else if (g === "FAB") groups.FAB.push(o.col);
        else if (g === "CACHE") groups["신규 캐시"].push(o.col);
        else groups["기타"].push(o.col);
      }
      return groups;
    }
    for (const c of data?.split_cols || []) {
      const u = String(c).toUpperCase();
      if (u.startsWith("KNOB_")) groups.KNOB.push(c);
      else if (u.startsWith("MASK_")) groups.MASK.push(c);
      else if (u.startsWith("FAB_")) groups.FAB.push(c);
      else groups["기타"].push(c);
    }
    return groups;
  }, [data]);

  // 전체 비중 리본 — "전체 wafer 중 이 split 이 몇 %" 를 차트 위에서 바로 읽는다.
  // 순서는 차트 스택과 동일하게 둔다: 리본이 곧 "모든 구간을 합친 한 개의 막대"라
  // 색 순서가 같아야 어느 구간과도 눈으로 바로 대조된다.
  const ribbon = useMemo(() => {
    const totals = {};
    for (const b of chart.bins) for (const [k, v] of Object.entries(b.splits || {})) totals[k] = (totals[k] || 0) + Number(v || 0);
    return chart.series
      .map((v) => ({ v, n: totals[v] || 0, pct: grandTotal ? ((totals[v] || 0) / grandTotal) * 100 : 0 }))
      .filter((r) => r.n > 0);
  }, [chart, grandTotal]);
  // 우측 하단 파이는 위 스택 차트와 같은 색/기타 묶음을 쓴다. PPID가 20종을
  // 넘을 때 파이에 비슷한 작은 조각을 무한히 만들지 않고, 원값은 왼쪽 상세표에 남긴다.
  const sharePieGroups = useMemo(() => ribbon.map(({ v, n, pct }) => ({
    label: v,
    value: n,
    count: n,
    percent: Number(pct.toFixed(1)),
    color: colorMap[v] || foldedColor,
  })), [ribbon, colorMap, foldedColor]);

  const matchedPct = grandTotal ? Math.round(((data?.matched_wafers ?? 0) / grandTotal) * 100) : 0;

  // 설정 변경은 로컬에 저장하고 바로 같은 필터로 다시 조회한다.
  const changeExclude = (next) => {
    const s = { ...settings, excludeRootPrefix: next };
    setSettings(s);
    saveSettings(s);
    fetchData(product, binSize, splitCol, axis, lotType, next);
  };
  const saveProductOrder = (next) => {
    setProductOrderBusy(true);
    return sf(`${API}/product-order`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product_order:next})})
      .then(result => {
        const saved = result.product_order || next;
        setProductOrder(saved);
        setCatalogProducts(current => mergeProductOrder(current, saved));
        toast.ok("제품 선택 순서를 저장했습니다.");
      })
      .catch(error => toast.error(`제품 순서 저장 실패: ${error.message || error}`))
      .finally(() => setProductOrderBusy(false));
  };

  // 드릴다운 조회 — summary 와 같은 필터 파라미터에 bin/split 만 더한다.
  const openDrill = ({ bin, label, split }) => {
    const q = new URLSearchParams();
    if (product) q.set("product", product);
    q.set("bin_size", String(binSize));
    if (splitCol) q.set("split_col", splitCol);
    q.set("axis", axis);
    q.set("bin", String(bin));
    if (lotType && lotType !== "ALL") q.set("lot_type", lotType);
    if (split) q.set("split", split);
    // 차트와 같은 슬라이스여야 목록 합계가 막대와 맞는다.
    if (String(excludeRootPrefix || "").trim()) q.set("exclude_root_prefix", String(excludeRootPrefix).trim());
    setDrillCopied(false);
    setDrill({ label, split, loading: true, rows: EMPTY_ARR, total: 0, truncated: false, err: "" });
    sf(`${API}/wip-split/lots?${q.toString()}`)
      .then((d) => setDrill({ label, split, loading: false, rows: d.rows || EMPTY_ARR, total: d.total_wafers || 0, truncated: !!d.truncated, err: "" }))
      .catch((e) => setDrill({ label, split, loading: false, rows: EMPTY_ARR, total: 0, truncated: false, err: e.message || String(e) }));
  };
  const drillGroups = useMemo(() => {
    if (!drill?.rows?.length) return EMPTY_ARR;
    const m = new Map();
    for (const r of drill.rows) {
      const root = String(r.root_lot_id || "-");
      if (!m.has(root)) m.set(root, []);
      m.get(root).push(String(r.wafer_id || ""));
    }
    return Array.from(m.entries()).map(([root, wafers]) => ({ root, wafers }));
  }, [drill]);
  const copyDrill = () => {
    const tsv = ["root_lot_id\twafer_id", ...(drill?.rows || []).map((r) => `${r.root_lot_id || ""}\t${r.wafer_id || ""}`)].join("\n");
    navigator.clipboard?.writeText(tsv).then(() => {
      setDrillCopied(true);
      setTimeout(() => setDrillCopied(false), 1600);
    });
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 10, paddingBottom: 16 }}>
      {/* 필터 + 요약 한 줄 — 아래 모든 카드가 같은 슬라이스를 본다 */}
      <div style={{ display: "flex", gap: 14, alignItems: "end", flexWrap: "wrap", padding: "8px 12px", ...cardStyle, background: "var(--bg-tertiary)" }}>
        <FilterField title="Product">
          <select style={selStyle} value={product} onChange={(e) => {const next=e.target.value;setProduct(next);fetchData(next, binSize, "", axis, lotType);}}>
            {/* 전체 = 제품 필터 없이 전 제품을 제품별 색으로 나눠 본다 */}
            <option value="ALL">전체</option>
            {availableProducts.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </FilterField>
        <FilterField title="Lot Type">
          <select style={selStyle} value={lotType} onChange={(e) => { setLotType(e.target.value); fetchData(product, binSize, "", axis, e.target.value); }}>
            <option value="ALL">전체</option>
            {(data?.lot_types || EMPTY_ARR).map((lt) => <option key={lt} value={lt}>{lt}</option>)}
          </select>
        </FilterField>
        <FilterField title="X축">
          <select style={selStyle} value={axis} onChange={(e) => { setAxis(e.target.value); fetchData(product, binSize, splitCol, e.target.value, lotType); }}>
            {AXIS_CHOICES.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
        </FilterField>
        <FilterField title="STEP BIN 간격" dim={axis !== "step_id"}>
          <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
            <input
              type="number"
              min={1}
              max={100000}
              step={5}
              value={binInput}
              disabled={axis !== "step_id"}
              onChange={(e) => setBinInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { if (e.nativeEvent?.isComposing || e.keyCode === 229) return; applyBin(binInput); } }}
              onBlur={() => applyBin(binInput)}
              style={{ ...selStyle, width: 78, minWidth: 0, ...numFont }}
            />
            <div style={{ display: "flex", gap: 3 }}>
              {WIP_BIN_CHOICES.map((b) => (
                <button
                  key={b}
                  type="button"
                  disabled={axis !== "step_id"}
                  onClick={() => applyBin(b)}
                  style={{
                    padding: "4px 7px", fontSize: 11.5, borderRadius: 6, cursor: "pointer", ...numFont,
                    border: `1px solid ${binSize === b ? "var(--text-secondary)" : "var(--border)"}`,
                    background: binSize === b ? "var(--bg-hover)" : "var(--bg-secondary)",
                    color: binSize === b ? "var(--text-primary)" : "var(--text-secondary)",
                    fontWeight: binSize === b ? 650 : 400,
                  }}
                >{nf(b)}</button>
              ))}
            </div>
          </div>
        </FilterField>
        {isAllProducts ? (
          <FilterField title="Split 기준 열">
            <div style={{ fontSize: 11.5, color: "var(--text-secondary)", lineHeight: 1.5, maxWidth: 200 }}>
              전체 보기는 <b>제품별 색 구분</b>으로 표시합니다.
            </div>
          </FilterField>
        ) : (
          <FilterField title="Split 기준 열">
            <SplitColSelect
              value={splitCol}
              groups={groupedSplitCols}
              onChange={(c) => fetchData(product, binSize, c, axis, lotType)}
              style={{ minWidth: 168 }}
            />
          </FilterField>
        )}
        {data && !isAllProducts && !(data.split_cols || []).length && (
          // 신규 제품이 latest cache 에는 떴는데 나눌 축이 하나도 없는 경우 —
          // 무엇이 없어서 못 나누는지 화면에서 바로 알려준다.
          <div style={{ fontSize: 11.5, color: "var(--text-secondary)", maxWidth: 320, lineHeight: 1.5 }}>
            이 제품은 나눌 수 있는 split 축이 없습니다
            {!data.has_ml_table && " — 원본 ML_TABLE 파일 없음"}
            {data.has_ml_table && !data.has_match_cache && " — 원본에 KNOB_/MASK_ 열이 없고 FAB 매칭 캐시도 아직 없음"}
            . 캐시 관리에서 이 제품의 FAB 매칭 캐시를 만들면 설비·PPID 축으로는 바로 나눠 볼 수 있습니다.
          </div>
        )}
        <Button variant="subtle" onClick={() => fetchData(product, binSize, splitCol, axis, lotType)} disabled={loading}>{loading ? "조회 중…" : "새로고침"}</Button>
        <div style={{ marginLeft: "auto", display: "flex", gap: 20, alignItems: "flex-end", flexWrap: "wrap" }}>
          <Stat title="총 WAFER" value={nf(grandTotal)} sub={`${product || "-"} · latest cache`} />
          <Stat title="SPLIT 매칭" value={`${matchedPct}%`} sub={`${nf(data?.matched_wafers ?? 0)} wafer`} />
          <Stat title="STEP 구간" value={nf(bins.length)} sub={axis === "step_desc" ? "step_desc 그룹" : `간격 ${nf(data?.bin_size ?? binSize)}`} />
          <Stat title="캐시 갱신" value={<span style={{ fontSize: 12.5, fontWeight: 600 }}>{(data?.generated_at || "-").replace("T", " ").slice(5, 16)}</span>} />
          <SettingsMenu
            excludeRootPrefix={excludeRootPrefix}
            onChangeExclude={changeExclude}
            excludedWafers={data?.excluded_wafers ?? 0}
            canManage={canManage}
            products={availableProducts}
            productOrder={productOrder}
            onSaveProductOrder={saveProductOrder}
            productOrderBusy={productOrderBusy}
          />
        </div>
      </div>

      {err && <div style={{ padding: 12, ...cardStyle, color: BAD.fg, fontSize: 13 }}>{err}</div>}

      {/* 빈 상태 — 응답은 왔는데 wafer 가 0 이면 차트가 백지로만 보인다.
          왜 비었고 무엇을 하면 채워지는지 화면에서 바로 알려준다. */}
      {!loading && !err && data && grandTotal === 0 && (
        <div style={{ ...cardStyle, padding: 20, display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start" }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>표시할 WIP 물량이 없습니다</div>
          <div style={{ fontSize: 12.5, color: "var(--text-secondary)", lineHeight: 1.6 }}>
            이 화면은 SplitTable 의 <strong>latest 랏 캐시</strong>를 읽습니다. 캐시가 아직 만들어지지 않았거나,
            현재 필터(제품 {product || "전체"} · 랏 구분 {lotType || "ALL"})에 해당하는 wafer 가 없을 때 비어 보입니다.
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: "var(--text-secondary)", lineHeight: 1.7 }}>
            <li>SplitTable → 캐시 관리에서 스캔을 실행하면 latest 캐시가 생성됩니다.</li>
            <li>필터를 걸어 둔 상태라면 제품/랏 구분을 전체로 되돌려 보세요.</li>
            <li>캐시가 있는데도 비어 있으면 톱니바퀴의 root 제외 설정을 확인하세요.</li>
          </ul>
          <Button variant="subtle" onClick={() => fetchData(product, binSize, splitCol, axis, lotType)} disabled={loading}>
            다시 조회
          </Button>
        </div>
      )}

      {/* 차트 — 헤더 + 전체 비중 리본 + 구간별 스택 */}
      <div style={{ ...cardStyle, padding: "8px 12px 6px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 6 }}>
          <div style={{ fontSize: 12.5, fontWeight: 650, whiteSpace: "nowrap" }}>
            STEP 구간별 WAFER 물량
            <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}> · {isAllProducts ? "제품별" : (splitCol || "split 없음")}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 10.5, color: "var(--text-secondary)" }}>
            <span>
              세로 {norm === "percent" ? "구간 내 비중" : "WAFER"} · 가로 {axis === "step_desc" ? "STEP_DESC 앞 숫자" : `step_id 구간 시작값 (간격 ${nf(data?.bin_size ?? binSize)})`}
              {chart.foldedCount ? ` · 하위 ${chart.foldedCount}종 기타` : ""}
              {data?.exclude_root_prefix ? ` · ${data.exclude_root_prefix}* root_lot 제외 ${nf(data.excluded_wafers || 0)}` : ""}
            </span>
            <Segmented value={norm} onChange={setNorm} options={NORM_CHOICES} />
          </div>
        </div>

        {/* 전체 비중 리본 — 전체 wafer 대비 각 split 의 몫 */}
        {ribbon.length > 1 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
            <span style={{ ...microLabel, whiteSpace: "nowrap" }}>전체 비중</span>
            <div
              ref={(el) => { ribbonRef.current = el; if (el && !ribbonMounted) setRibbonMounted(true); }}
              style={{ display: "flex", gap: 1, height: 20, borderRadius: 4, overflow: "hidden", flex: 1, minWidth: 0 }}
            >
              {ribbon.map(({ v, n, pct }) => {
                const c = colorOf(v);
                // 조각 폭에 실제로 들어갈 때만 이름을 넣는다 — 잘린 글자는 안 넣느니만 못하다.
                const wPx = (ribbonW * pct) / 100;
                const text = wPx >= String(v).length * 6 + 30 ? `${v} ${pct.toFixed(0)}%` : wPx >= 34 ? `${pct.toFixed(0)}%` : "";
                return (
                  <div
                    key={v}
                    title={`${v} · ${nf(n)} wafer · ${pct.toFixed(1)}%`}
                    style={{
                      width: `${pct}%`, background: c, display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 9.5, fontWeight: 600, color: inkOn(c), overflow: "hidden", whiteSpace: "nowrap", ...numFont,
                    }}
                  >{text}</div>
                );
              })}
            </div>
            <span style={{ fontSize: 10.5, color: "var(--text-secondary)", whiteSpace: "nowrap", ...numFont }}>
              {ribbon.length}종 · 최대 {Math.max(...ribbon.map((r) => r.pct)).toFixed(1)}%
            </span>
          </div>
        )}

        <div ref={chartBoxRef}>
          {loading && !data ? <Loading /> : (
            <div style={{ opacity: loading ? 0.55 : 1, transition: "opacity .15s" }}>
              <WipStackedBar
                bins={chart.bins}
                splitValues={chart.series}
                dark={dark}
                unassignedLabel={unassigned}
                otherLabel={chart.otherLabel}
                norm={norm}
                height={chartH}
                axis={axis}
                onSegmentClick={openDrill}
              />
            </div>
          )}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", textAlign: "right", marginTop: -2 }}>
          막대 클릭 = 구간×split lot/wafer 목록 · 레전드 클릭 = 해당 split 숨김 · 더블클릭 = 단독 보기
        </div>
      </div>

      {/* 드릴다운 — 클릭한 구간×split 의 root_lot/wafer 전량 */}
      {drill && (
        <div style={{ ...cardStyle, padding: "10px 12px 12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
            <div style={{ fontSize: 12.5, fontWeight: 650 }}>
              구간 {drill.label}
              <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}> · {splitCol || "split"} = {drill.split || "(전체)"}</span>
            </div>
            {!drill.loading && !drill.err && (
              <span style={{ fontSize: 11, color: "var(--text-secondary)", ...numFont }}>
                {nf(drill.total)} wafer · root lot {nf(drillGroups.length)}개{drill.truncated ? ` · 상위 ${nf(drill.rows.length)}건만 표시` : ""}
              </span>
            )}
            <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              <Button variant="subtle" onClick={copyDrill} disabled={drill.loading || !drill.rows.length}>
                {drillCopied ? "복사됨" : "TSV 복사"}
              </Button>
              <Button variant="subtle" onClick={() => setDrill(null)}>닫기</Button>
            </div>
          </div>
          {drill.loading ? (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", padding: "6px 0" }}>조회 중…</div>
          ) : drill.err ? (
            <div style={{ fontSize: 12, color: BAD.fg }}>{drill.err}</div>
          ) : !drill.rows.length ? (
            <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>해당 구간에 wafer가 없습니다.</div>
          ) : (
            <div style={{ overflow: "auto", maxHeight: 300 }}>
              <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12, ...numFont }}>
                <thead>
                  <tr>
                    <th style={{ ...th, textAlign: "left" }}>ROOT_LOT_ID</th>
                    <th style={{ ...th, textAlign: "right" }}>WAFER</th>
                    <th style={{ ...th, textAlign: "left" }}>WAFER_ID</th>
                  </tr>
                </thead>
                <tbody>
                  {drillGroups.map(({ root, wafers }) => (
                    <tr key={root}>
                      <td style={{ ...td, textAlign: "left", fontFamily: "monospace", whiteSpace: "nowrap" }}>{root}</td>
                      <td style={{ ...td, textAlign: "right", fontWeight: 650 }}>{nf(wafers.length)}</td>
                      <td style={{ ...td, textAlign: "left", fontFamily: "monospace", color: "var(--text-secondary)" }}>{wafers.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* 표 + 비중 (fold 아래 — 상세 확인용) */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(240px, 1fr)", gap: 10, alignItems: "start" }}>
        <div style={{ ...cardStyle, padding: "10px 0 0" }}>
          <div style={{ fontSize: 12.5, fontWeight: 650, padding: "0 12px 8px" }}>
            BIN별 상세 <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}>· split 값 {splitValues.length}종 전량</span>
          </div>
          <div style={{ overflow: "auto", maxHeight: 360 }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12, ...numFont }}>
              <thead>
                <tr>
                  <th style={{ ...th, textAlign: "left" }}>{axis === "step_desc" ? "STEP_DESC" : "STEP BIN"}</th>
                  <th style={{ ...th, textAlign: "right" }}>WAFER</th>
                  {splitValues.map((sv) => (
                    <th key={sv} style={{ ...th, textAlign: "right", whiteSpace: "nowrap" }}>
                      <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}><Dot color={colorOf(sv)} />{sv}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bins.map((b) => (
                  <tr key={b.bin}>
                    <td style={{ ...td, textAlign: "left", color: "var(--text-primary)" }}>{b.label}</td>
                    <td style={{ ...td, textAlign: "right", fontWeight: 650 }}>{nf(b.total)}</td>
                    {splitValues.map((sv) => {
                      const n = Number(b.splits?.[sv] || 0);
                      const pct = b.total ? Math.round((n / b.total) * 100) : 0;
                      return (
                        <td key={sv} style={{ ...td, textAlign: "right", color: n ? "var(--text-primary)" : "var(--border)" }}>
                          {n ? <>{nf(n)} <span style={{ color: "var(--text-secondary)" }}>{pct}%</span></> : "–"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div style={{ ...cardStyle, padding: "10px 12px 12px", minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 650, marginBottom: 4 }}>
            {isAllProducts ? "제품" : (splitCol || "SPLIT")} 전체 비중
          </div>
          {sharePieGroups.length ? <>
            <div aria-label={`${isAllProducts ? "제품" : (splitCol || "SPLIT")}별 wafer 비중 파이차트`}>
              <FlowPlotlyChart
                chart={{ chart_type: "pie", groups: sharePieGroups, y_label: "WAFER", hide_title: true }}
                cfg={{ chart_type: "pie", hide_title: true, show_legend: false, compact: true, use_svg: true }}
                height={245}
                dark={dark}
              />
            </div>
            <div style={{ display: "grid", gap: 5, maxHeight: 190, overflow: "auto", paddingRight: 2 }}>
              {sharePieGroups.map(({ label, count, percent, color }) => (
                <div key={label} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12 }}>
                  <Dot color={color} />
                  <span title={label} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>{label}</span>
                  <span style={{ color: "var(--text-secondary)", whiteSpace: "nowrap", ...numFont }}>{nf(count)} · {percent.toFixed(1)}%</span>
                </div>
              ))}
            </div>
            {chart.foldedCount > 0 && (
              <div style={{ marginTop: 7, fontSize: 10.5, color: "var(--text-secondary)", lineHeight: 1.45 }}>
                하위 {chart.foldedCount}종은 기타 조각으로 합산했습니다. 개별 값은 왼쪽 상세표에서 확인할 수 있습니다.
              </div>
            )}
          </> : (
            <div style={{ minHeight: 245, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)", fontSize: 12 }}>
              표시할 비중 데이터가 없습니다.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function My_Dashboard({ user }) {
  return (
    <div style={{ padding: "12px 16px", background: "var(--bg-primary)", color: "var(--text-primary)", maxWidth: "none", margin: 0, height: "100%", minHeight: 0, overflow: "auto", boxSizing: "border-box" }}>
      <WipSplitPanel user={user} />
    </div>
  );
}
