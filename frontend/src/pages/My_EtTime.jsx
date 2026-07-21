/* My_EtTime.jsx — ET 측정시간 (업무 탭).
   root lot 별로 auto report 의 step_id × PGM(pt) 단위 측정 소요시간을 조회.
   - 측정시간 = tkout_time - tkin_time (백엔드 /api/et-time/measure 집계).
   - PGM(pt) = step_seq(측정점수pt)_중복차수 — auto report Main.py 와 동일 라벨.
   - 같은 (step_id, PGM(pt)) 조합이면 wafer 가 달라도 측정시간은 동일하다는
     업무 규칙 전제 — 편차가 있으면 "min ~ max" 로 표시하고 행을 강조.
*/
import { useEffect, useMemo, useRef, useState } from "react";
import { sf, qs } from "../lib/api";
import { toast } from "../components/Toast";
import Loading from "../components/Loading";
import { Card, EmptyState, Input, PageHeader, PageShell, Pill, Select } from "../components/UXKit";

const API = "/api/et-time";

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
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const lotFetchRef = useRef(0);
  // 📈 장기 추이 — product 만으로 조회 (root lot 불필요)
  const [trend, setTrend] = useState(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendStep, setTrendStep] = useState("");
  const [trendMonths, setTrendMonths] = useState(0);   // 0 = 전체

  useEffect(() => {
    sf(`${API}/products`).then(d => setProducts(d.products || [])).catch(() => {});
  }, []);

  // product 확정 후 root lot 후보 로드 (입력 prefix 반영, 최신 요청만 반영)
  useEffect(() => {
    if (!product.trim()) { setLotOptions([]); return; }
    const seq = ++lotFetchRef.current;
    const t = setTimeout(() => {
      sf(`${API}/lots` + qs({ product: product.trim(), prefix: rootLot.trim() }))
        .then(d => { if (seq === lotFetchRef.current) setLotOptions(d.candidates || []); })
        .catch(() => {});
    }, 250);
    return () => clearTimeout(t);
  }, [product, rootLot]);

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
      <PageHeader title="⏱️ ET 측정시간" />
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
          <div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 4 }}>Root Lot ID</div>
            <Input list="ettime-lots" value={rootLot}
              onChange={e => setRootLot(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") {if(e.nativeEvent?.isComposing||e.keyCode===229)return; search();} }}
              placeholder="예: A0001" style={{ width: 200 }} />
            <datalist id="ettime-lots">
              {lotOptions.map((c, i) => {
                const v = c.root_lot_id || c.value || c.lot_id || c.fab_lot_id || "";
                return v ? <option key={v + i} value={v} /> : null;
              })}
            </datalist>
          </div>
          <button onClick={search} disabled={loading} style={{
            padding: "7px 22px", borderRadius: 6, border: "none", background: "var(--accent)",
            color: "#fff", fontSize: 14, fontWeight: 700, cursor: loading ? "wait" : "pointer",
          }}>조회</button>
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
            <button onClick={() => loadTrend()} disabled={trendLoading || !product.trim()} style={{
              padding: "6px 18px", borderRadius: 6, border: "none", background: "var(--accent)",
              color: "#fff", fontSize: 13, fontWeight: 700, cursor: trendLoading ? "wait" : "pointer",
              opacity: product.trim() ? 1 : 0.5,
            }}>{trendLoading ? "스캔 중…" : "추이 조회"}</button>
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
