import { useEffect, useMemo, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import SpreadsheetPasteGrid, {
  normalizeSpreadsheetRows,
} from "../../components/SpreadsheetPasteGrid";
import {
  Card,
  PageShell,
  Pill,
} from "../../components/ui";
import { sf } from "../../lib/api";

const REF_COLUMNS = ["lot_id"];

function parseRefRows(rawText) {
  const ids = String(rawText || "")
    .split(/[,;\s\n\r]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
    .slice(0, 5);
  const rows = ids.map((id) => ({ lot_id: id }));
  return normalizeSpreadsheetRows(rows, REF_COLUMNS, { minRows: 5, maxRows: 5 });
}

function refTextFromRows(rows) {
  return (rows || [])
    .map((r) => String(r.lot_id || "").trim().toUpperCase())
    .filter(Boolean)
    .slice(0, 5)
    .join(", ");
}

const inputStyle = {
  width: "100%",
  boxSizing: "border-box",
  padding: "9px 12px",
  border: "1px solid var(--border)",
  borderRadius: 6,
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  fontSize: 14,
  outline: "none",
};

const metricCardStyle = {
  flex: "1 1 200px",
  padding: "14px 16px",
  borderRadius: 8,
  background: "var(--bg-primary)",
  border: "1px solid var(--border)",
  display: "grid",
  gap: 4,
};

function isLithoPhotoPoint(point) {
  if (point.is_litho_photo) return true;
  const desc = String(point.step_desc || "").trim();
  return /(?:litho|photo|lithography|photolitho)/i.test(desc);
}

function cleanLayerNumber(val) {
  if (val == null) return "";
  const s = String(val).trim();
  // 02.0 -> 2.0, 00.0 -> 0.0, 01.5 -> 1.5 (적힌 숫자 그대로 표기)
  const m = s.match(/^0*(\d+)(?:\.(\d+))?$/);
  if (m) {
    const major = m[1];
    const minor = m[2] !== undefined ? m[2] : "0";
    return `${major}.${minor}`;
  }
  return s;
}

function getMaskLayerLabel(point) {
  if (point.layer_label && point.layer_label !== point.step_id) {
    return cleanLayerNumber(point.layer_label);
  }
  const desc = String(point.step_desc || "");
  const m = desc.match(/(?:^|\b)(\d{1,3})(?:\.(\d+))?/);
  if (m) {
    const major = parseInt(m[1], 10);
    const minor = m[2] !== undefined ? m[2] : "0";
    return `${major}.${minor}`;
  }
  if (point.mask_layer != null) {
    return `${parseInt(point.mask_layer, 10)}.0`;
  }
  return cleanLayerNumber(point.step_label) || point.step_id;
}

function formatDateTime(raw) {
  if (!raw) return "";
  const s = String(raw).trim();
  return s.includes("T") ? s.replace("T", " ").replace(/\.\d+/, "") : s;
}

function formatElapsedDuration(daysVal) {
  if (daysVal == null || isNaN(daysVal)) return "-";
  const num = Number(daysVal);
  const isNeg = num < 0;
  const totalMin = Math.round(Math.abs(num) * 24 * 60);
  const d = Math.floor(totalMin / 1440);
  const h = Math.floor((totalMin % 1440) / 60);
  const m = totalMin % 60;
  const prefix = isNeg ? "-" : "";
  return `${prefix}${d}d ${h}h ${m}m`;
}

function chartPoint(point, series) {
  const maskLabel = getMaskLayerLabel(point);
  const timeVal = formatDateTime(point.tkout_time || point.eta || point.time);
  return {
    ...point,
    x: maskLabel,
    x_label: maskLabel,
    y: timeVal,
    series,
    label: point.step_desc ? `${maskLabel} · ${point.step_id} (${point.step_desc})` : `${maskLabel} · ${point.step_id}`,
  };
}

export default function LotTracker() {
  const [form, setForm] = useState({
    product: "PRODA",
    lot_id: "DEMO-LOT-01",
    reference_lot_id: "DEMO-REF-01, DEMO-REF-02, DEMO-REF-03",
    target_step_id: "",
  });
  const [refRows, setRefRows] = useState(() =>
    parseRefRows("DEMO-REF-01, DEMO-REF-02, DEMO-REF-03")
  );
  const [showOptional, setShowOptional] = useState(true);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleRefRowsChange = (nextRows) => {
    setRefRows(nextRows);
    const joined = refTextFromRows(nextRows);
    setForm((prev) => ({ ...prev, reference_lot_id: joined }));
  };

  const handleClearRefs = () => {
    const emptyRows = normalizeSpreadsheetRows([], REF_COLUMNS, { minRows: 5, maxRows: 5 });
    setRefRows(emptyRows);
    setForm((prev) => ({ ...prev, reference_lot_id: "" }));
  };

  const handleLoadDemoRefs = () => {
    const demoRows = parseRefRows("DEMO-REF-01, DEMO-REF-02, DEMO-REF-03, DEMO-REF-04, DEMO-REF-05");
    setRefRows(demoRows);
    setForm((prev) => ({ ...prev, reference_lot_id: refTextFromRows(demoRows) }));
  };

  const executeSearch = async (searchForm) => {
    const targetForm = searchForm || form;
    if (!targetForm.product.trim()) {
      setError("Product를 입력하세요.");
      return;
    }
    if (!targetForm.lot_id.trim()) {
      setError("lot id를 입력하세요.");
      return;
    }
    setBusy(true);
    setError("");
    setData(null);
    try {
      const query = new URLSearchParams();
      for (const [k, v] of Object.entries(targetForm)) {
        if (v && v.trim()) query.set(k, v.trim());
      }
      const response = await sf(`/api/lot-tracker?${query}`);
      setData(response);
      if (!response?.ok) {
        setError(response?.note || "LOT 이력을 찾지 못했습니다.");
      }
    } catch (err) {
      setError(err.message || "LOT 이력을 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    executeSearch();
  }, []);

  const load = (event) => {
    if (event) event.preventDefault();
    executeSearch();
  };

  const lot = data?.lot;
  const forecast = data?.forecast;

  const chartPoints = useMemo(() => {
    if (!lot) return [];
    const filterLitho = (pts) => {
      const filtered = (pts || []).filter(isLithoPhotoPoint);
      return filtered.length > 0 ? filtered : (pts || []);
    };

    const lotPts = filterLitho(lot.points);
    // forecast points 중 anchor(현재 완료점)와 litho/photo 공정 필터링
    const forecastPts = (forecast?.points || []).filter(
      (p) => p.is_anchor || isLithoPhotoPoint(p) || p.step_id === forecast?.target_step_id
    );

    return [
      ...lotPts.map((point) => chartPoint(point, `${lot.lot_id}`)),
      ...forecastPts.map((point) => chartPoint(point, `${lot.lot_id} (도착 예측)`)),
    ].filter((point) => point.x && point.y);
  }, [lot, forecast]);

  // X축은 숫자순으로 정렬되지만, 숫자에 따라 길이가 늘어나지 않는 균등 간격의 category(string) 축
  const categoryOrder = useMemo(() => {
    const set = new Set();
    for (const p of chartPoints) {
      if (p.x) set.add(String(p.x));
    }
    return Array.from(set).sort((a, b) => {
      const na = parseFloat(a);
      const nb = parseFloat(b);
      if (Number.isFinite(na) && Number.isFinite(nb)) {
        return na - nb;
      }
      return a.localeCompare(b);
    });
  }, [chartPoints]);

  return (
    <PageShell layout="analysis" style={{ padding: "16px 24px", maxWidth: 1280, margin: "0 auto", display: "grid", gap: 14 }}>
      {/* ── Search Bar: Minimal & Clean ── */}
      <Card style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}>
        <form onSubmit={load} style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 12, alignItems: "end" }}>
            <div style={{ display: "grid", gap: 5 }}>
              <label style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>Product</label>
              <input
                value={form.product}
                placeholder="Product"
                onChange={(event) => setForm({ ...form, product: event.target.value })}
                style={inputStyle}
              />
            </div>

            <div style={{ display: "grid", gap: 5 }}>
              <label style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>lot id</label>
              <input
                value={form.lot_id}
                placeholder="lot id"
                onChange={(event) => setForm({ ...form, lot_id: event.target.value })}
                style={inputStyle}
              />
            </div>

            <button
              type="submit"
              disabled={busy}
              style={{
                height: 38,
                padding: "0 24px",
                borderRadius: 6,
                border: 0,
                background: "var(--accent)",
                color: "#fff",
                fontWeight: 700,
                fontSize: 14,
                cursor: busy ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {busy ? "조회 중…" : "조회"}
            </button>
          </div>

          {/* 도착 예측 시점 옵션 */}
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            <button
              type="button"
              onClick={() => setShowOptional(!showOptional)}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--text-secondary)",
                fontSize: 13,
                cursor: "pointer",
                padding: "2px 0",
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                fontWeight: 600,
              }}
            >
              <span>{showOptional ? "▼" : "▶"}</span>
              <span>도착 예측 시점 옵션</span>
            </button>

            {showOptional && (
              <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16, background: "var(--bg-primary)", padding: 14, borderRadius: 8, border: "1px solid var(--border)" }}>
                {/* Left: Spreadsheet Grid for Excel Paste */}
                <div style={{ display: "grid", gap: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <label style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
                      참고 lot id
                    </label>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        type="button"
                        onClick={handleLoadDemoRefs}
                        style={{ fontSize: 11, padding: "2px 8px", background: "var(--bg-tertiary)", border: "1px solid var(--border)", borderRadius: 4, cursor: "pointer", color: "var(--text-secondary)" }}
                      >
                        5개 데모 입력
                      </button>
                      <button
                        type="button"
                        onClick={handleClearRefs}
                        style={{ fontSize: 11, padding: "2px 8px", background: "var(--bg-tertiary)", border: "1px solid var(--border)", borderRadius: 4, cursor: "pointer", color: "var(--text-secondary)" }}
                      >
                        비우기
                      </button>
                    </div>
                  </div>
                  <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                    <SpreadsheetPasteGrid
                      ariaLabel="참고 lot id"
                      columns={REF_COLUMNS}
                      rows={refRows}
                      onChange={handleRefRowsChange}
                      aliases={{ lot: "lot_id", "lot id": "lot_id", lotid: "lot_id", reference_lot_id: "lot_id" }}
                      columnLabels={{ lot_id: "참고 lot id" }}
                      placeholders={{ lot_id: "예: DEMO-REF-01" }}
                      showRowNumbers={true}
                      minRows={5}
                      maxRows={5}
                      maxHeight={205}
                      minTableWidth={260}
                    />
                  </div>
                </div>

                {/* Right: Target Step ID */}
                <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
                  <div style={{ display: "grid", gap: 5 }}>
                    <label style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>목표 STEP ID</label>
                    <input
                      value={form.target_step_id}
                      placeholder="예: AA800100"
                      onChange={(event) => setForm({ ...form, target_step_id: event.target.value })}
                      style={inputStyle}
                    />
                    <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                      선택된 목표 step까지 참고 LOT들의 평균 진행 시간을 계산하여 도착 예정일을 예측합니다.
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </form>
      </Card>

      {/* ── Error Message ── */}
      {error && (
        <div style={{ padding: "12px 16px", borderRadius: 8, background: "var(--danger-50)", border: "1px solid var(--danger-line)", color: "var(--danger)", fontSize: 14 }}>
          {error}
        </div>
      )}

      {/* ── Search Results ── */}
      {lot && (
        <>
          {/* Summary KPI Banner */}
          <section style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            <div style={metricCardStyle}>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>조회 대상 LOT</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "var(--text-primary)" }}>{lot.lot_id}</div>
              <div style={{ fontSize: 12, color: "var(--accent)" }}>Product: <b>{lot.product || "-"}</b></div>
            </div>

            <div style={metricCardStyle}>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>현재 공정 (Current Step)</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>{lot.current_step_id || "-"}</div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {lot.current_step_desc || "공정명 없음"}
              </div>
            </div>

            <div style={metricCardStyle}>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>최근 TKOUT 완료</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)" }}>{formatDateTime(lot.current_time) || "-"}</div>
            </div>

            <div style={metricCardStyle}>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>DPML</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "var(--ok)" }}>
                {lot.dpml != null ? lot.dpml : "-"}
              </div>
            </div>
          </section>

          {/* ── Main Progress Chart ── */}
          <Card
            title={`${lot.lot_id} 진행 현황 차트`}
            right={
              forecast ? (
                <span style={{ fontSize: 12, color: "var(--accent)", fontWeight: 600 }}>● 실선: 완료 / ┄ 점선: 예측</span>
              ) : null
            }
            style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}
          >
            {chartPoints.length ? (
              <FlowPlotlyChart
                chart={{
                  chart_type: "line",
                  points: chartPoints,
                  x_label: "layer",
                  y_label: "tkout time",
                  color_by: "series",
                  title: `${lot.lot_id} (${lot.product})`,
                }}
                cfg={{
                  chart_type: "line",
                  color_by: "series",
                  x_label: "layer",
                  y_label: "tkout time",
                  x_type: "category",
                  category_array: categoryOrder,
                  y_scale: "date",
                  color_map: {
                    [`${lot.lot_id}`]: "#2563eb",
                    [`${lot.lot_id} (도착 예측)`]: "#2563eb",
                  },
                  line_dash_map: {
                    [`${lot.lot_id}`]: "solid",
                    [`${lot.lot_id} (도착 예측)`]: "dash",
                  },
                  xaxis: {
                    tickangle: -45,
                  },
                  use_svg: true,
                  height: 440,
                }}
              />
            ) : (
              <div style={{ padding: 32, textAlign: "center", color: "var(--text-secondary)" }}>
                step_desc에 매칭된 Lithography / Photo 공정이 없습니다.
              </div>
            )}
          </Card>

          {/* ── Forecast Section (Multi-reference Lot Average) ── */}
          {forecast && (
            <Card
              title={`도착 예정 예측 결과 · 목표 ${forecast.target_step_id || "-"} ${forecast.target_step_desc || ""}`}
              right={
                forecast.ref_lot_count ? (
                  <Pill tone="accent" size="sm">
                    {forecast.ref_lot_count}개 참고 LOT 평균 소요시간 반영
                  </Pill>
                ) : null
              }
              style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}
            >
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16, alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>예상 도착 시점 (ETA)</div>
                  <div style={{ fontSize: 24, fontWeight: 800, color: "var(--accent)" }}>{formatDateTime(forecast.eta) || "계산 불가"}</div>
                </div>
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>평균 소요 기간</div>
                  <div style={{ fontSize: 20, fontWeight: 800, color: "var(--ok)" }}>{forecast.remaining_days ?? "-"} 일</div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>{forecast.basis || "-"}</div>
                </div>
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>참고 LOT별 소요 기간</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {(forecast.ref_summaries || []).map((ref) => (
                      <span
                        key={ref.lot_id}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 4,
                          padding: "3px 8px",
                          borderRadius: 4,
                          background: "var(--bg-primary)",
                          border: "1px solid var(--border)",
                          fontSize: 12,
                          fontFamily: "var(--font-mono)",
                        }}
                      >
                        <span style={{ fontWeight: 600 }}>{ref.lot_id}</span>:
                        <span style={{ color: "var(--accent)", fontWeight: 700 }}>{ref.remaining_days}d</span>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </Card>
          )}

          {/* ── Step History Detail Table (Completed + Projected) ── */}
          <Card
            title="상세 이력"
            style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}
          >
            <div style={{ maxHeight: 440, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, textAlign: "left" }}>
                <thead>
                  <tr style={{ background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border)", color: "var(--text-secondary)", position: "sticky", top: 0 }}>
                    <th style={{ padding: "8px 12px", width: 50 }}>#</th>
                    <th style={{ padding: "8px 12px" }}>STEP ID</th>
                    <th style={{ padding: "8px 12px" }}>공정명</th>
                    <th style={{ padding: "8px 12px" }}>tkout time</th>
                    <th style={{ padding: "8px 12px", textAlign: "right" }}>경과 시간</th>
                  </tr>
                </thead>
                <tbody>
                  {(lot.points || []).map((point, index) => {
                    const isLitho = isLithoPhotoPoint(point);
                    return (
                      <tr
                        key={point.step_id + index}
                        style={{
                          borderBottom: "1px solid var(--border)",
                          background: isLitho
                            ? "var(--accent-glow)"
                            : index % 2 === 0
                            ? "var(--bg-primary)"
                            : "var(--bg-secondary)",
                        }}
                      >
                        <td style={{ padding: "8px 12px", color: "var(--text-secondary)" }}>{index + 1}</td>
                        <td style={{ padding: "8px 12px", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                          {point.step_id}
                          {isLitho && <span style={{ marginLeft: 6, color: "var(--accent)", fontSize: 11, fontWeight: 700 }}>● Photo</span>}
                        </td>
                        <td style={{ padding: "8px 12px" }}>{point.step_desc || "-"}</td>
                        <td style={{ padding: "8px 12px", fontFamily: "var(--font-mono)" }}>{formatDateTime(point.tkout_time) || "-"}</td>
                        <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                          {formatElapsedDuration(point.elapsed_days)}
                        </td>
                      </tr>
                    );
                  })}

                  {/* 예측 미래 공정 행 */}
                  {(forecast?.points || []).filter(p => !p.is_anchor).map((point, index) => {
                    return (
                      <tr
                        key={"forecast_" + point.step_id + index}
                        style={{
                          borderBottom: "1px dashed var(--accent)",
                          background: "var(--bg-secondary)",
                        }}
                      >
                        <td style={{ padding: "8px 12px", color: "var(--accent)", fontWeight: 700 }}>예측</td>
                        <td style={{ padding: "8px 12px", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                          {point.step_id}
                          <span style={{ marginLeft: 6, color: "var(--accent)", fontSize: 11, fontWeight: 700, border: "1px dashed var(--accent)", padding: "1px 4px", borderRadius: 3 }}>도착 예측</span>
                        </td>
                        <td style={{ padding: "8px 12px", color: "var(--text-primary)" }}>{point.step_desc || "-"}</td>
                        <td style={{ padding: "8px 12px", fontFamily: "var(--font-mono)", color: "var(--accent)", fontWeight: 700 }}>
                          {formatDateTime(point.eta || point.tkout_time) || "-"}
                        </td>
                        <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}>
                          {formatElapsedDuration(point.elapsed_days)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Footer note */}
          {data.note && (
            <div style={{ color: "var(--text-secondary)", fontSize: 12, padding: "4px 8px" }}>
              ℹ️ {data.note}
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}
