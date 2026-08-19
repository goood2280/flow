/* My_EtTime.jsx — ET 측정시간 (업무 탭).
   root lot 별로 auto report 의 step_id × PGM(pt) 단위 측정 소요시간을 조회.
   - 측정시간 = tkout_time - tkin_time (백엔드 /api/et-time/measure 집계).
   - PGM(pt) = step_seq(측정점수pt)_중복차수 — auto report Main.py 와 동일 라벨.
   - 같은 (step_id, PGM(pt)) 조합이면 wafer 가 달라도 측정시간은 동일하다는
     업무 규칙 전제 — 편차가 있으면 "min ~ max" 로 표시하고 행을 강조.
*/
import { useEffect, useMemo, useRef, useState } from "react";
import { sf, qs } from "../../lib/api";
import { toast } from "../../components/Toast";
import Loading from "../../components/Loading";
import { Banner, Button, Card, EmptyState, Input, PageShell, Pill, Select } from "../../components/UXKit";

const API = "/api/et-time";
// SplitTable 과 동일 규칙: 제품 선택 시 root lot 전체 목록을 한 번만 받아 두고
// 이후 키 입력은 로컬 부분일치 필터로만 처리한다 (서버 재요청 없음).
const ROOT_LOT_CACHE_LIMIT_MAX = 50000;
const LOT_DROP_MAX = 50;

const thStyle = {
  padding: "8px 10px", textAlign: "left", fontSize: 13, fontWeight: 700,
  color: "var(--text-secondary)", borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap", position: "sticky", top: 0, background: "var(--bg-secondary)",
};
const tdStyle = {
  padding: "6px 10px", fontSize: 13, borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap", fontFamily: "'JetBrains Mono',monospace",
};

function fmtTime(v) {
  const t = String(v || "");
  return t ? t.slice(0, 19).replace("T", " ") : "-";
}

function fmtDur(sec) {
  const s = Math.round(Number(sec) || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${s}s`;
}

/* 월별 평균 측정시간 추이 — 단일 시리즈 라인 차트 (accent 색, 축 1개).
   평균 PGM 수·major 의뢰 형태는 스케일이 달라 축을 늘리지 않고
   포인트 툴팁과 아래 표에서 보여준다. */
function TrendChart({ series }) {
  if (!series?.length) return null;
  const W = 760, H = 220, P = { l: 58, r: 20, t: 16, b: 30 };
  const maxSec = Math.max(...series.map(p => p.avg_duration_sec), 1);
  // y 상한을 보기 좋은 단위(분)로 올림
  const yMax = Math.max(60, Math.ceil(maxSec / 300) * 300);
  const x = i => P.l + (W - P.l - P.r) * (series.length === 1 ? 0.5 : i / (series.length - 1));
  const y = v => P.t + (H - P.t - P.b) * (1 - v / yMax);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => yMax * f);
  const path = series.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.avg_duration_sec).toFixed(1)}`).join(" ");
  // x 라벨은 겹치지 않게 최대 8개만
  const stepN = Math.max(1, Math.ceil(series.length / 8));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: W, display: "block" }} role="img"
      aria-label="월별 평균 측정시간 추이">
      {ticks.map(t => (
        <g key={t}>
          <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth="1" />
          <text x={P.l - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-secondary)">{fmtDur(t)}</text>
        </g>
      ))}
      {series.map((p, i) => (i % stepN === 0 || i === series.length - 1) && (
        <text key={p.month} x={x(i)} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--text-secondary)">{p.month}</text>
      ))}
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" />
      {series.map((p, i) => (
        <g key={p.month}>
          <circle cx={x(i)} cy={y(p.avg_duration_sec)} r="4" fill="var(--accent)" stroke="var(--bg-card)" strokeWidth="2" />
          <circle cx={x(i)} cy={y(p.avg_duration_sec)} r="10" fill="transparent">
            <title>{`${p.month}\n평균 측정시간 ${p.avg_duration_text} (WF ${p.wafers})\n평균 PGM ${p.avg_pgm_count}개\nmajor: ${p.major} (${p.major_share}%)`}</title>
          </circle>
        </g>
      ))}
      {/* 마지막 값 직접 라벨 */}
      <text x={Math.min(x(series.length - 1) + 8, W - 4)} y={y(series[series.length - 1].avg_duration_sec) - 8}
        textAnchor={series.length === 1 ? "middle" : "end"} fontSize="12" fontWeight="700" fill="var(--text-primary)">
        {series[series.length - 1].avg_duration_text}
      </text>
    </svg>
  );
}

export default function My_EtTime() {
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [rootLot, setRootLot] = useState("");
  const [lotOptions, setLotOptions] = useState([]);
  const [lotBusy, setLotBusy] = useState(false);
  const [lotMsg, setLotMsg] = useState("");
  const [showLotDrop, setShowLotDrop] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const lotFetchRef = useRef(0);
  const lotBoxRef = useRef(null);
  // 📈 장기 추이 — product 만으로 조회 (root lot 불필요)
  const [trend, setTrend] = useState(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendStep, setTrendStep] = useState("");
  const [trendMonths, setTrendMonths] = useState(0);   // 0 = 전체

  useEffect(() => {
    sf(`${API}/products`).then(d => setProducts(d.products || [])).catch(() => {});
  }, []);

  // product 확정 후 root lot 후보를 **한 번만** 로드 (SplitTable 과 동일 규칙).
  //   서버 응답은 {value,type,...} 객체 배열 — root_lot_id 문자열로 정규화한다.
  //   키 입력마다 재요청하지 않고 아래 filteredLots 의 로컬 부분일치로 좁힌다.
  useEffect(() => {
    const seq = ++lotFetchRef.current;
    let retryTimer = null;
    let disposed = false;
    if (!product.trim()) { setLotOptions([]); setLotBusy(false); setLotMsg(""); return; }
    setLotBusy(true); setLotMsg(""); setLotOptions([]);
    const url = `${API}/lots` + qs({ product: product.trim(), col: "root_lot_id", limit: ROOT_LOT_CACHE_LIMIT_MAX });
    const MAX_CACHE_RETRY = 30;
    const fetchLots = attempt => sf(url)
      .then(d => {
        if (disposed || seq !== lotFetchRef.current) return;
        const vals = [...new Set((d.candidates || []).map(c => (
          typeof c === "string" ? c : (c?.root_lot_id || c?.value || c?.lot_id || c?.fab_lot_id || "")
        )).map(v => String(v).trim()).filter(Boolean))];
        const lookup = d.lookup_cache || {};
        const preparing = d.provisional === true || d.complete === false
          || lookup.queued === true || lookup.status === "queued" || lookup.status === "running"
          || d.match_mode === "lookup_cache_preparing";
        setLotOptions(vals);
        setLotBusy(false);
        setLotMsg(vals.length
          ? (preparing ? "Lot 후보 캐시를 갱신 중입니다. 현재 목록 또는 직접 입력으로 조회할 수 있습니다." : "")
          : (preparing ? "Lot 후보 캐시 준비 중입니다. 직접 입력해 조회할 수 있습니다." : "Lot 후보가 없습니다."));
        if (preparing && attempt < MAX_CACHE_RETRY) {
          retryTimer = setTimeout(() => fetchLots(attempt + 1), 2000);
        }
      })
      .catch(() => {
        if (disposed || seq !== lotFetchRef.current) return;
        setLotOptions([]); setLotMsg("Lot 후보 캐시를 불러오지 못했습니다. 직접 입력해 조회해 주세요."); setLotBusy(false);
      });
    fetchLots(0);
    return () => { disposed = true; if (retryTimer) clearTimeout(retryTimer); };
  }, [product]);

  // 드롭다운 바깥 클릭 시 닫기 (SplitTable 과 동일 동작)
  useEffect(() => {
    const h = e => { if (lotBoxRef.current && !lotBoxRef.current.contains(e.target)) setShowLotDrop(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  // 입력값은 prefix 가 아니라 부분일치로 거른다 — datalist(앞글자 매칭)와 달리
  // 중간 문자열로도 찾을 수 있다.
  const filteredLots = useMemo(() => {
    const f = rootLot.trim().toUpperCase();
    const base = f ? lotOptions.filter(v => v.toUpperCase().includes(f)) : lotOptions;
    return base.slice(0, LOT_DROP_MAX);
  }, [lotOptions, rootLot]);

  const search = () => {
    const p = product.trim(), r = rootLot.trim();
    if (!p) { toast.warn("product 를 입력하세요"); return; }
    if (!r) { toast.warn("root lot id 를 입력하세요"); return; }
    setLoading(true); setError(""); setData(null);
    sf(`${API}/measure` + qs({ product: p, root_lot_id: r }))
      .then(d => setData(d))
      .catch(e => setError(e?.message || "조회 실패"))
      .finally(() => setLoading(false));
  };

  const loadTrend = (m = trendMonths) => {
    const p = product.trim();
    if (!p) { toast.warn("product 를 선택하세요"); return; }
    setTrendLoading(true);
    sf(`${API}/trend` + qs({ product: p, months: m || 0 }))
      .then(d => {
        setTrend(d);
        setTrendStep(s => (d.steps || []).includes(s) ? s : (d.steps?.[0] || ""));
      })
      .catch(e => { setTrend(null); toast.error(e?.message || "추이 조회 실패"); })
      .finally(() => setTrendLoading(false));
  };

  const rows = data?.rows || [];
  // step_id 가 바뀌는 행에만 step 라벨을 보여주기 위한 계산
  const stepFirstIdx = useMemo(() => {
    const seen = new Set(); const first = new Set();
    rows.forEach((r, i) => { if (!seen.has(r.step_id)) { seen.add(r.step_id); first.add(i); } });
    return first;
  }, [rows]);
  // step_id 별 측정시간 소계 (백엔드 step_totals)
  const stepTotalMap = useMemo(() => {
    const m = new Map();
    (data?.step_totals || []).forEach(t => m.set(t.step_id, t.duration_text));
    return m;
  }, [data]);

  return (
    <PageShell>
      <Banner tone="warn" style={{ borderRadius: 0, borderBottom: "1px solid var(--warn-line)", lineHeight: 1.45 }}>
        <b>주의사항</b> · Reformatter에 등록된 항목만 확인합니다. PCHK 항목이 포함되어 있어 대부분의 DCOP는 스캔되지만,
        PCHK이 찍히지 않은 경우 일부 DCOP가 누락될 수 있습니다.
      </Banner>
      <Card style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 4 }}>
              Product <span style={{ opacity: 0.7 }}>(현재 ET DB {products.length}개)</span>
            </div>
            <Select value={product} onChange={e => { setProduct(e.target.value); setRootLot(""); setData(null); }}
              style={{ width: 200 }}>
              <option value="">— 선택 —</option>
              {products.map(p => <option key={p} value={p}>{p}</option>)}
            </Select>
          </div>
          <div style={{ position: "relative" }} ref={lotBoxRef}>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 4 }}>
              Root Lot ID{lotOptions.length > 0 && <span style={{ opacity: 0.7 }}>{` (${lotOptions.length}개)`}</span>}
            </div>
            <Input value={rootLot}
              onChange={e => { setRootLot(e.target.value); setShowLotDrop(true); }}
              onFocus={() => setShowLotDrop(true)}
              onKeyDown={e => { if (e.key === "Enter") {if(e.nativeEvent?.isComposing||e.keyCode===229)return; setShowLotDrop(false); search();} }}
              placeholder="입력 또는 선택" style={{ width: 200 }} />
            {showLotDrop && (filteredLots.length > 0 || lotBusy || lotMsg) && (
              <div style={{
                position: "absolute", zIndex: 20, top: "100%", left: 0, width: 200, marginTop: 2,
                maxHeight: 180, overflow: "auto", border: "1px solid var(--border)",
                borderRadius: 6, background: "var(--bg-card)",
              }}>
                {lotBusy && <div style={{ padding: "7px 10px", fontSize: 14, color: "var(--text-secondary)" }}>Lot 후보 조회 중...</div>}
                {!lotBusy && filteredLots.length === 0 && lotMsg && (
                  <div style={{ padding: "7px 10px", fontSize: 14, color: "var(--danger)", lineHeight: 1.4 }}>{lotMsg}</div>
                )}
                {!lotBusy && filteredLots.map(l => (
                  <div key={l} onMouseDown={() => { setRootLot(l); setShowLotDrop(false); }}
                    style={{ padding: "6px 10px", fontSize: 14, cursor: "pointer", borderBottom: "1px solid var(--border)", color: "var(--text-primary)" }}
                    onMouseEnter={e => e.currentTarget.style.background = "var(--bg-hover)"}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>{l}</div>
                ))}
              </div>
            )}
          </div>
          <Button variant="primary" onClick={search} disabled={loading}
            style={{ padding: "7px 22px", cursor: loading ? "wait" : "pointer" }}>조회</Button>
          {data && (
            <div style={{ display: "flex", gap: 8, marginLeft: "auto", alignItems: "center", flexWrap: "wrap" }}>
              <Pill tone="neutral">STEP {data.step_count}</Pill>
              <Pill tone="neutral">PGM(pt) {data.pgm_count}</Pill>
            </div>
          )}
        </div>
      </Card>

      {loading && <Loading text="ET DB 집계 중..." />}
      {!loading && error && (
        <Card><div style={{ color: "var(--danger)", fontSize: 14, whiteSpace: "pre-wrap" }}>{error}</div></Card>
      )}
      {!loading && !error && data && rows.length === 0 && (
        <EmptyState icon="⏱" title="측정 데이터 없음"
          hint={`${data.product} / ${data.root_lot_id} 에 해당하는 ET 측정 이력이 없습니다.`} />
      )}
      {!loading && rows.length > 0 && (
        <Card padding={0}>
          <div style={{ overflow: "auto", maxHeight: "calc(100vh - 300px)" }}>
            <table style={{ borderCollapse: "collapse", width: "100%" }}>
              <thead>
                <tr>
                  <th style={thStyle}>STEP_ID</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>STEP 측정시간</th>
                  <th style={thStyle}>PGM(pt)</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>측정시간</th>
                  <th style={thStyle}>tkin_time (최초)</th>
                  <th style={thStyle}>tkout_time (최종)</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>WF 수</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>측정 pt</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.step_id + "|" + r.pgm} style={{
                    background: r.duration_uniform === false && r.duration_sec !== null
                      ? "var(--warn-50)" : "transparent",
                  }}>
                    <td style={{ ...tdStyle, borderTop: stepFirstIdx.has(i) && i > 0 ? "2px solid var(--border)" : undefined }}>
                      {stepFirstIdx.has(i) ? (
                        <span>
                          <b>{r.step_id}</b>
                          {r.function_step && <span style={{ color: "var(--text-secondary)", marginLeft: 6, fontFamily: "'Pretendard',sans-serif" }}>{r.function_step}</span>}
                        </span>
                      ) : <span style={{ color: "var(--text-secondary)" }}>〃</span>}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700,
                      borderTop: stepFirstIdx.has(i) && i > 0 ? "2px solid var(--border)" : undefined }}>
                      {stepFirstIdx.has(i) ? (stepTotalMap.get(r.step_id) || "-") : ""}
                    </td>
                    <td style={tdStyle}>{r.pgm}</td>
                    <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: "var(--accent)" }}
                      title={r.duration_uniform === false ? "wafer 간 측정시간 편차 있음" : undefined}>
                      {r.duration_text || "-"}{r.duration_uniform === false && " ⚠"}
                    </td>
                    <td style={tdStyle}>{fmtTime(r.tkin_min)}</td>
                    <td style={tdStyle}>{fmtTime(r.tkout_max)}</td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{r.wafer_count}</td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{r.pt}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 12px", fontSize: 13, color: "var(--text-secondary)", borderTop: "1px solid var(--border)" }}>
            측정시간 = tkout_time − tkin_time. 같은 step_id 의 PGM(pt) 는 동일한 측정시간을 갖는 것이 정상이며,
            wafer 간 편차가 1초 이상이면 ⚠ 와 함께 min ~ max 범위로 표시됩니다.
          </div>
        </Card>
      )}
      {!loading && !error && !data && (
        <EmptyState icon="⏱" title="Product 와 Root Lot ID 를 입력해 조회하세요"
          hint="ET DB(1.RAWDATA_DB_ET)에서 auto report 의 PGM(pt) 단위로 측정 소요시간을 집계합니다." />
      )}

      {/* 📈 장기 측정시간 추이 — product 만으로 조회. 의뢰서 항목 감축·PGM 제외에 따라
          step 평균 측정시간이 수개월에 걸쳐 어떻게 줄어드는지 확인. */}
      <Card style={{ marginTop: 14 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: trend ? 10 : 0 }}>
          <span style={{ fontSize: 14, fontWeight: 800 }}>📈 측정시간 추이</span>
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            product 전체를 가볍게 스캔해 step 별 월 평균 측정시간(wafer 당 합)을 봅니다 — root lot 불필요
          </span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            <Select value={trendMonths} onChange={e => { const m = Number(e.target.value); setTrendMonths(m); if (trend) loadTrend(m); }}
              style={{ width: 110 }}>
              <option value={0}>전체 기간</option>
              <option value={6}>최근 6개월</option>
              <option value={12}>최근 12개월</option>
              <option value={24}>최근 24개월</option>
            </Select>
            <Button variant="primary" onClick={() => loadTrend()} disabled={trendLoading || !product.trim()}
              style={{ padding: "6px 18px", fontSize: 13, cursor: trendLoading ? "wait" : "pointer" }}>
              {trendLoading ? "스캔 중…" : "추이 조회"}</Button>
          </span>
        </div>
        {trend && (
          <>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
              <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>STEP</span>
              <Select value={trendStep} onChange={e => setTrendStep(e.target.value)} style={{ width: 180 }}>
                {(trend.steps || []).map(s => <option key={s} value={s}>{s}</option>)}
              </Select>
              <Pill tone="neutral">{trend.product}</Pill>
              <Pill tone="neutral">월 {trend.trend?.[trendStep]?.length || 0}개</Pill>
            </div>
            <TrendChart series={trend.trend?.[trendStep] || []} />
            <div style={{ overflow: "auto", marginTop: 6 }}>
              <table style={{ borderCollapse: "collapse", width: "100%" }}>
                <thead><tr>
                  <th style={thStyle}>월</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>WF 수</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>평균 측정시간</th>
                  <th style={{ ...thStyle, textAlign: "right" }}>평균 PGM 수</th>
                  <th style={thStyle}>major 의뢰 (step_seq 조합)</th>
                </tr></thead>
                <tbody>
                  {(trend.trend?.[trendStep] || []).map(p => (
                    <tr key={p.month}>
                      <td style={tdStyle}>{p.month}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{p.wafers}</td>
                      <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: "var(--accent)" }}>{p.avg_duration_text}</td>
                      <td style={{ ...tdStyle, textAlign: "right" }}>{p.avg_pgm_count}</td>
                      <td style={{ ...tdStyle, whiteSpace: "normal", wordBreak: "break-all" }}>
                        {p.major}<span style={{ color: "var(--text-secondary)" }}>{` (${p.major_share}%)`}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </PageShell>
  );
}
