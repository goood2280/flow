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

export default function My_EtTime() {
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [rootLot, setRootLot] = useState("");
  const [lotOptions, setLotOptions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const lotFetchRef = useRef(0);

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

  const rows = data?.rows || [];
  // step_id 가 바뀌는 행에만 step 라벨을 보여주기 위한 계산
  const stepFirstIdx = useMemo(() => {
    const seen = new Set(); const first = new Set();
    rows.forEach((r, i) => { if (!seen.has(r.step_id)) { seen.add(r.step_id); first.add(i); } });
    return first;
  }, [rows]);

  return (
    <PageShell>
      <PageHeader
        title="⏱️ ET 측정시간"
        subtitle="root lot 별 step_id × PGM(pt) 측정 소요시간 (tkout_time − tkin_time)"
      />
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
              <Pill tone="info">총 측정시간 {data.total_duration_text || "-"}</Pill>
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
    </PageShell>
  );
}
