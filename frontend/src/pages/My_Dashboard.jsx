import { useState, useEffect, useMemo, useRef } from "react";
import Loading from "../components/Loading";
import { Button, statusPalette, buildSeriesColors, SERIES_COLOR_LIMIT } from "../components/UXKit";
import { WipStackedBar } from "../components/PlotlyChart";
import { sf as apiSf } from "../lib/api";

// v9.2 (2026-07-12): 대시보드 = WIP × Split 현황 단일 화면.
// 기존 차트 보드(저장 차트 그리드/에디터/스케줄러 UI)는 퇴역 —
// 원본 전체는 archive/dashboard_chartboard_2026_07_12/My_Dashboard.full.jsx 참조.
// 차트 보드용 백엔드 API(/api/dashboard/charts 등)는 아직 남아 있다.
const API = "/api/dashboard";
const sf = (url, o) => apiSf(url, o);
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
    if ((oy === "auto" || oy === "scroll") && p.scrollHeight > p.clientHeight + 1) return p;
    if (oy === "auto" || oy === "scroll") return p;
  }
  return null;
}
function useFoldHeight(ref, { min = 260, gap = 12 } = {}) {
  const [h, setH] = useState(420);
  useEffect(() => {
    let last = 0;
    const calc = () => {
      const el = ref.current;
      if (!el) return;
      const box = scrollParent(el);
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
      const next = Math.max(min, Math.round(viewH - offset - gap));
      if (Math.abs(next - last) > 2) { last = next; setH(next); }
    };
    calc();
    window.addEventListener("resize", calc);
    const timer = setInterval(calc, 200);
    return () => { window.removeEventListener("resize", calc); clearInterval(timer); };
  }, [ref, min, gap]);
  return h;
}

/* ═══ WIP × Split 현황 (latest cache 기반) ═══ */
const WIP_BIN_CHOICES = [1000, 10000, 100000];
// X축 기준 — step_id 숫자 구간 / step_desc 앞머리 숫자(예: FAB_1.0 STI → 1.0).
const AXIS_CHOICES = [
  { value: "step_id", label: "step_id 구간" },
  { value: "step_desc", label: "step_desc (앞 숫자)" },
];
const NORM_CHOICES = [
  { value: "count", label: "물량" },
  { value: "percent", label: "비중 100%" },
];
const OTHER_PREFIX = "기타";
const nf = (n) => Number(n || 0).toLocaleString();

const microLabel = { fontSize: 10, letterSpacing: "0.05em", textTransform: "uppercase", color: "var(--text-secondary)", fontWeight: 600 };
const cardStyle = { border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" };
const numFont = { fontVariantNumeric: "tabular-nums" };

// 채움색 위에 얹는 글자색 — 밝은 색이면 잉크, 어두운 색이면 흰색.
function inkOn(hex) {
  const h = String(hex || "").replace("#", "");
  if (h.length !== 6) return "#fff";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.55 ? "#171717" : "#ffffff";
}

function Field({ children, title, dim = false }) {
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

function WipSplitPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [product, setProduct] = useState("");
  const [binSize, setBinSize] = useState(1000);
  const [binInput, setBinInput] = useState("1000");
  const [splitCol, setSplitCol] = useState("");
  const [axis, setAxis] = useState("step_id");
  const [norm, setNorm] = useState("count");
  const dark = typeof document !== "undefined" && (document.documentElement.classList.contains("dark") || localStorage.getItem("hol_dark") === "true");
  const chartBoxRef = useRef(null);
  // gap = 차트 아래 안내문(≈14px) + 카드 하단 패딩/여백.
  const chartH = useFoldHeight(chartBoxRef, { gap: 28 });
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

  const fetchData = (p, b, s, a) => {
    setLoading(true);
    setErr("");
    const q = new URLSearchParams();
    if (p) q.set("product", p);
    q.set("bin_size", String(b || 1000));
    if (s) q.set("split_col", s);
    q.set("axis", a || "step_id");
    sf(`${API}/wip-split?${q.toString()}`)
      .then((d) => {
        setData(d);
        setProduct(d.product || "");
        setSplitCol(d.split_col || "");
        if (d.axis) setAxis(d.axis);
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { fetchData("", binSize, "", axis); }, []);

  // 자유 입력 bin 간격 적용 — 1~100000 정수로 보정, 같은 값 재입력은 무시.
  const applyBin = (val) => {
    const b = Math.max(1, Math.min(100000, Math.round(Number(val) || 0)));
    if (!b) { setBinInput(String(binSize)); return; }
    setBinInput(String(b));
    if (b === binSize) return;
    setBinSize(b);
    fetchData(product, b, splitCol, axis);
  };

  const bins = data?.bins || [];
  const splitValues = data?.split_values || [];
  const unassigned = data?.unassigned_label || "(미지정)";
  const totalBySplit = useMemo(() => {
    const acc = {};
    for (const b of bins) for (const [k, v] of Object.entries(b.splits || {})) acc[k] = (acc[k] || 0) + Number(v || 0);
    return acc;
  }, [bins]);
  const grandTotal = data?.total_wafers || 0;

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

  const groupedSplitCols = useMemo(() => {
    const groups = { KNOB: [], MASK: [], FAB: [], 기타: [] };
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
  // 아래 상세 패널은 읽기 편하게 물량 순으로.
  const shareRows = useMemo(() => {
    const rows = splitValues.map((v) => ({ v, n: totalBySplit[v] || 0, pct: grandTotal ? ((totalBySplit[v] || 0) / grandTotal) * 100 : 0 }));
    rows.sort((a, b) => b.n - a.n);
    return rows;
  }, [splitValues, totalBySplit, grandTotal]);
  const shareMax = shareRows.length ? shareRows[0].n : 0;

  const selStyle = {
    padding: "5px 8px", border: "1px solid var(--border)", borderRadius: 7,
    background: "var(--bg-secondary)", color: "var(--text-primary)", fontSize: 12.5, minWidth: 118,
  };
  const matchedPct = grandTotal ? Math.round(((data?.matched_wafers ?? 0) / grandTotal) * 100) : 0;
  const th = { padding: "6px 10px", borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: "var(--bg-secondary)", fontWeight: 600, fontSize: 11, color: "var(--text-secondary)" };
  const td = { padding: "5px 10px", borderBottom: "1px solid var(--border)" };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 10, paddingBottom: 16 }}>
      {/* 필터 + 요약 한 줄 — 아래 모든 카드가 같은 슬라이스를 본다 */}
      <div style={{ display: "flex", gap: 14, alignItems: "end", flexWrap: "wrap", padding: "8px 12px", ...cardStyle, background: "var(--bg-tertiary)" }}>
        <Field title="Product">
          <select style={selStyle} value={product} onChange={(e) => fetchData(e.target.value, binSize, "", axis)}>
            {(data?.products || (product ? [product] : [])).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
        <Field title="X축">
          <select style={selStyle} value={axis} onChange={(e) => { setAxis(e.target.value); fetchData(product, binSize, splitCol, e.target.value); }}>
            {AXIS_CHOICES.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
        </Field>
        <Field title="STEP BIN 간격" dim={axis !== "step_id"}>
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
        </Field>
        <Field title="Split 기준 열">
          <select style={{ ...selStyle, minWidth: 168 }} value={splitCol} onChange={(e) => fetchData(product, binSize, e.target.value, axis)}>
            {Object.entries(groupedSplitCols).map(([g, cols]) => cols.length ? (
              <optgroup key={g} label={g}>
                {cols.map((c) => <option key={c} value={c}>{c}</option>)}
              </optgroup>
            ) : null)}
          </select>
        </Field>
        <Button variant="subtle" onClick={() => fetchData(product, binSize, splitCol, axis)} disabled={loading}>{loading ? "조회 중…" : "새로고침"}</Button>
        <div style={{ marginLeft: "auto", display: "flex", gap: 20, alignItems: "flex-end", flexWrap: "wrap" }}>
          <Stat title="총 WAFER" value={nf(grandTotal)} sub={`${product || "-"} · latest cache`} />
          <Stat title="SPLIT 매칭" value={`${matchedPct}%`} sub={`${nf(data?.matched_wafers ?? 0)} wafer`} />
          <Stat title="STEP 구간" value={nf(bins.length)} sub={axis === "step_desc" ? "step_desc 그룹" : `간격 ${nf(data?.bin_size ?? binSize)}`} />
          <Stat title="캐시 갱신" value={<span style={{ fontSize: 12.5, fontWeight: 600 }}>{(data?.generated_at || "-").replace("T", " ").slice(5, 16)}</span>} />
        </div>
      </div>

      {err && <div style={{ padding: 12, ...cardStyle, color: BAD.fg, fontSize: 13 }}>{err}</div>}

      {/* 차트 — 헤더 + 전체 비중 리본 + 구간별 스택 */}
      <div style={{ ...cardStyle, padding: "8px 12px 6px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 6 }}>
          <div style={{ fontSize: 12.5, fontWeight: 650, whiteSpace: "nowrap" }}>
            STEP 구간별 WAFER 물량
            <span style={{ fontWeight: 400, color: "var(--text-secondary)" }}> · {splitCol || "split 없음"}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 10.5, color: "var(--text-secondary)" }}>
            <span>
              세로 {norm === "percent" ? "구간 내 비중" : "WAFER"} · 가로 {axis === "step_desc" ? "STEP_DESC 앞 숫자" : `step_id 구간 시작값 (간격 ${nf(data?.bin_size ?? binSize)})`}
              {chart.foldedCount ? ` · 하위 ${chart.foldedCount}종 기타` : ""}
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
              />
            </div>
          )}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-secondary)", textAlign: "right", marginTop: -2 }}>
          레전드 클릭 = 해당 split 숨김 · 더블클릭 = 단독 보기
        </div>
      </div>

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

        <div style={{ ...cardStyle, padding: "10px 12px 12px" }}>
          <div style={{ fontSize: 12.5, fontWeight: 650, marginBottom: 8 }}>SPLIT 전체 비중</div>
          <div style={{ display: "grid", gap: 7 }}>
            {shareRows.map(({ v, n, pct }) => (
              <div key={v} style={{ display: "grid", gap: 3 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12 }}>
                  <Dot color={colorOf(v)} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>{v}</span>
                  <span style={{ color: "var(--text-secondary)", ...numFont }}>{nf(n)} · {pct.toFixed(1)}%</span>
                </div>
                <div style={{ height: 3, borderRadius: 2, background: "var(--bg-tertiary)", overflow: "hidden" }}>
                  <div style={{ width: `${shareMax ? (n / shareMax) * 100 : 0}%`, height: "100%", background: colorOf(v), borderRadius: 2 }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function My_Dashboard() {
  return (
    <div style={{ padding: "12px 16px", background: "var(--bg-primary)", color: "var(--text-primary)", maxWidth: "none", margin: 0, height: "100%", minHeight: 0, overflow: "auto", boxSizing: "border-box" }}>
      <WipSplitPanel />
    </div>
  );
}
