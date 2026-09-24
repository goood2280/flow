import { useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import SpreadsheetPasteGrid, {
  normalizeSpreadsheetRows,
} from "../../components/SpreadsheetPasteGrid";
import {
  Card,
  PageShell,
  Pill,
} from "../../components/ui";
import { postJson, sf } from "../../lib/api";
import { buildDpmlComparison } from "./dpmlComparison";

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
    product: "",
    lot_id: "",
    reference_lot_id: "",
    target_step_id: "",
  });
  const [refRows, setRefRows] = useState(() =>
    parseRefRows("")
  );
  const [showOptional, setShowOptional] = useState(true);
  const [comparisonDpml, setComparisonDpml] = useState("");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [presetSteps, setPresetSteps] = useState([]);
  const [presetBusy, setPresetBusy] = useState(false);
  const [presetMsg, setPresetMsg] = useState("");
  const [newStepId, setNewStepId] = useState("");
  const [newStepDesc, setNewStepDesc] = useState("");
  const [managedLots, setManagedLots] = useState([]);
  const [lotFilter, setLotFilter] = useState("");
  const helperReqId = useRef(0);

  const trimProduct = String(form.product || "").trim();

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

  // 제품별 목표 STEP 목록 + 랏관리 LOT 목록을 불러온다 (제안 리스트용).
  const fetchHelpers = async (productKey) => {
    const key = String(productKey || "").trim();
    const my = helperReqId.current + 1;
    helperReqId.current = my;
    if (!key) {
      setPresetSteps([]);
      setManagedLots([]);
      return;
    }
    try {
      const ps = await sf(`/api/lot-tracker/preset-steps?product=${encodeURIComponent(key)}`);
      if (helperReqId.current !== my) return;
      setPresetSteps(Array.isArray(ps?.steps) ? ps.steps : []);
    } catch (_) {
      if (helperReqId.current === my) setPresetSteps([]);
    }
    try {
      const t = await sf(`/api/lot-management/table?product=${encodeURIComponent(key)}&include_status=false`);
      if (helperReqId.current !== my) return;
      const seen = new Set();
      const lots = [];
      for (const row of t?.rows || []) {
        const v = row?.values || {};
        const lid = String(v.lot_id || "").trim();
        if (!lid) continue;
        const k = lid.toUpperCase();
        if (seen.has(k)) continue;
        seen.add(k);
        lots.push({ lot_id: lid, purpose: String(v.purpose || "").trim() });
      }
      setManagedLots(lots);
    } catch (_) {
      if (helperReqId.current === my) setManagedLots([]);
    }
  };

  useEffect(() => {
    if (!trimProduct) {
      setPresetSteps([]);
      setManagedLots([]);
      return;
    }
    const timer = setTimeout(() => { fetchHelpers(trimProduct); }, 400);
    return () => clearTimeout(timer);
  }, [trimProduct]);

  const executeSearch = async (searchForm) => {
    const targetForm = searchForm || form;
    if (!targetForm.lot_id.trim()) {
      setError("lot id를 입력하세요.");
      return;
    }
    setBusy(true);
    setError("");
    setData(null);
    fetchHelpers(targetForm.product);
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

  const dpmlPoints = useMemo(
    () => buildDpmlComparison(chartPoints, comparisonDpml),
    [chartPoints, comparisonDpml]
  );
  const dpmlSeries = dpmlPoints[0]?.series;
  const invalidDpml = comparisonDpml !== "" && (!Number.isFinite(Number(comparisonDpml)) || Number(comparisonDpml) <= 0);

  // 목표 STEP 제안: 제품별 저장 목록에서 입력값으로 필터 (자유 입력도 그대로 가능).
  const stepSuggestions = useMemo(() => {
    const q = String(form.target_step_id || "").trim().toLowerCase();
    return (presetSteps || [])
      .filter((s) => !q
        || String(s.step_id || "").toLowerCase().includes(q)
        || String(s.step_desc || "").toLowerCase().includes(q))
      .slice(0, 8);
  }, [presetSteps, form.target_step_id]);

  // 참고 LOT 제안: 랏관리 LOT(lot_id + purpose)에서 그리드에 없는 것만.
  const refGridIds = useMemo(() => new Set(
    (refRows || []).map((r) => String(r.lot_id || "").trim().toUpperCase()).filter(Boolean)
  ), [refRows]);

  const lotSuggestions = useMemo(() => {
    const q = String(lotFilter || "").trim().toLowerCase();
    return (managedLots || [])
      .filter((l) => !refGridIds.has(String(l.lot_id || "").toUpperCase()))
      .filter((l) => !q
        || String(l.lot_id || "").toLowerCase().includes(q)
        || String(l.purpose || "").toLowerCase().includes(q))
      .slice(0, 8);
  }, [managedLots, lotFilter, refGridIds]);

  const addRefLot = (lotId) => {
    const id = String(lotId || "").trim().toUpperCase();
    if (!id || refGridIds.has(id)) return;
    const next = (refRows || []).map((r) => ({ ...(r || {}) }));
    const idx = next.findIndex((r) => !String(r.lot_id || "").trim());
    if (idx < 0) return;
    next[idx] = { ...next[idx], lot_id: id };
    handleRefRowsChange(next);
  };

  const savePresets = async (next) => {
    const key = String(form.product || "").trim();
    if (!key) {
      setPresetMsg("product를 먼저 입력하세요.");
      return;
    }
    setPresetBusy(true);
    setPresetMsg("");
    try {
      const res = await postJson("/api/lot-tracker/preset-steps", { product: key, steps: next });
      setPresetSteps(Array.isArray(res?.steps) ? res.steps : next);
      setPresetMsg("저장됨");
    } catch (err) {
      setPresetMsg(err?.message || "저장 실패");
    } finally {
      setPresetBusy(false);
    }
  };

  const addPresetStep = () => {
    const sid = String(newStepId || "").trim().toUpperCase();
    if (!sid) return;
    if ((presetSteps || []).some((s) => String(s.step_id || "").toUpperCase() === sid)) {
      setPresetMsg("이미 등록된 step입니다.");
      return;
    }
    const next = [...(presetSteps || []), { step_id: sid, step_desc: String(newStepDesc || "").trim() }];
    setPresetSteps(next);
    setNewStepId("");
    setNewStepDesc("");
    savePresets(next);
  };

  const removePresetStep = (sid) => {
    const key = String(sid || "").toUpperCase();
    const next = (presetSteps || []).filter((s) => String(s.step_id || "").toUpperCase() !== key);
    setPresetSteps(next);
    savePresets(next);
  };

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
              <label style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>Product (선택)</label>
              <input
                value={form.product}
                placeholder="비워두면 FAB DB에서 검색"
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
                      placeholders={{ lot_id: "참고 lot id (직접 입력 가능)" }}
                      showRowNumbers={true}
                      minRows={5}
                      maxRows={5}
                      maxHeight={205}
                      minTableWidth={260}
                    />
                  </div>
                  {trimProduct && (
                    <div style={{ display: "grid", gap: 4 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>
                        랏관리 LOT에서 추가
                      </div>
                      {managedLots.length > 0 ? (
                        <>
                          <input
                            value={lotFilter}
                            placeholder="검색 (lot_id·purpose)"
                            onChange={(event) => setLotFilter(event.target.value)}
                            style={{ ...inputStyle, padding: "6px 10px", fontSize: 13 }}
                          />
                          {lotSuggestions.length > 0 ? (
                            <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                              {lotSuggestions.map((l) => (
                                <button
                                  key={l.lot_id}
                                  type="button"
                                  onClick={() => addRefLot(l.lot_id)}
                                  style={{
                                    display: "block", width: "100%", textAlign: "left",
                                    padding: "6px 10px", background: "transparent", border: 0,
                                    borderBottom: "1px solid var(--border)", cursor: "pointer",
                                    fontSize: 13, fontFamily: "var(--font-mono)", color: "var(--text-primary)",
                                  }}
                                >
                                  {l.lot_id}
                                  {l.purpose ? (
                                    <span style={{ color: "var(--text-secondary)" }}>({l.purpose})</span>
                                  ) : null}
                                </button>
                              ))}
                            </div>
                          ) : (
                            <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                              조건에 맞는 추가 가능한 LOT이 없습니다.
                            </div>
                          )}
                        </>
                      ) : (
                        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                          랏관리에 등록된 LOT이 없습니다. 그리드에 직접 입력도 가능합니다.
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Right: Target Step ID */}
                <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
                  <div style={{ display: "grid", gap: 5 }}>
                    <label htmlFor="lot-tracker-dpml" style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>DPML 비교선 (일/Mask Layer)</label>
                    <input
                      id="lot-tracker-dpml"
                      type="number"
                      step="any"
                      min="0"
                      value={comparisonDpml}
                      placeholder="예: 1.5 (비워두면 숨김)"
                      onChange={(event) => setComparisonDpml(event.target.value)}
                      aria-invalid={invalidDpml}
                      aria-describedby="lot-tracker-dpml-help"
                      style={inputStyle}
                    />
                    <div id="lot-tracker-dpml-help" style={{ fontSize: 11, color: invalidDpml ? "var(--danger)" : "var(--text-secondary)" }}>
                      {invalidDpml ? "DPML은 0보다 큰 숫자를 입력하세요." : "첫 완료 Photo 시점부터 표시된 레이어마다 입력한 일수를 더한 검정 점선입니다. 참고 LOT 없이도 비교할 수 있으며, 값을 바꾸면 즉시 반영됩니다."}
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: 5 }}>
                    <label style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>목표 STEP ID</label>
                    <input
                      value={form.target_step_id}
                      placeholder="예: AA800100 (직접 입력 가능)"
                      onChange={(event) => setForm({ ...form, target_step_id: event.target.value })}
                      style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                    />
                    {stepSuggestions.length > 0 && (
                      <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                        {stepSuggestions.map((s) => (
                          <button
                            key={s.step_id}
                            type="button"
                            onClick={() => setForm((prev) => ({ ...prev, target_step_id: s.step_id }))}
                            style={{
                              display: "block", width: "100%", textAlign: "left",
                              padding: "6px 10px", background: "transparent", border: 0,
                              borderBottom: "1px solid var(--border)", cursor: "pointer",
                              fontSize: 13, fontFamily: "var(--font-mono)", color: "var(--text-primary)",
                            }}
                          >
                            {s.step_id}
                            {s.step_desc ? (
                              <span style={{ color: "var(--text-secondary)" }}>({s.step_desc})</span>
                            ) : null}
                          </button>
                        ))}
                      </div>
                    )}
                    <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                      선택된 목표 step까지 참고 LOT들의 평균 진행 시간을 계산하여 도착 예정일을 예측합니다.
                      목표 step은 참고 LOT에 있고 현재 공정보다 뒤쪽 단계여야 ETA가 계산됩니다.
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: 5 }}>
                    <label style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
                      목표 STEP 목록 관리 (제품별 저장)
                    </label>
                    {trimProduct ? (
                      <>
                        <div style={{ display: "flex", gap: 6 }}>
                          <input
                            value={newStepId}
                            placeholder="step_id"
                            onChange={(event) => setNewStepId(event.target.value)}
                            style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
                          />
                          <input
                            value={newStepDesc}
                            placeholder="step_desc (선택)"
                            onChange={(event) => setNewStepDesc(event.target.value)}
                            style={inputStyle}
                          />
                          <button
                            type="button"
                            onClick={addPresetStep}
                            disabled={presetBusy}
                            style={{
                              padding: "0 14px", borderRadius: 6, border: "1px solid var(--border)",
                              background: "var(--bg-tertiary)", cursor: presetBusy ? "not-allowed" : "pointer",
                              fontWeight: 700, fontSize: 13, whiteSpace: "nowrap",
                            }}
                          >
                            추가
                          </button>
                        </div>
                        {presetSteps.length > 0 && (
                          <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                            {presetSteps.map((s) => (
                              <div
                                key={s.step_id}
                                style={{
                                  display: "flex", alignItems: "center", justifyContent: "space-between",
                                  padding: "6px 10px", borderBottom: "1px solid var(--border)",
                                  fontSize: 13, fontFamily: "var(--font-mono)",
                                }}
                              >
                                <span>
                                  {s.step_id}
                                  {s.step_desc ? (
                                    <span style={{ color: "var(--text-secondary)" }}>({s.step_desc})</span>
                                  ) : null}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => removePresetStep(s.step_id)}
                                  disabled={presetBusy}
                                  style={{
                                    fontSize: 11, padding: "2px 8px", background: "transparent",
                                    border: "1px solid var(--border)", borderRadius: 4,
                                    cursor: presetBusy ? "not-allowed" : "pointer", color: "var(--text-secondary)",
                                  }}
                                >
                                  삭제
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                        {presetMsg && (
                          <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>{presetMsg}</div>
                        )}
                      </>
                    ) : (
                      <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                        product를 입력하면 해당 제품의 목표 STEP 목록을 등록할 수 있습니다 (관리자·lottracker 담당자만 저장).
                      </div>
                    )}
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
              <span style={{ fontSize: 12, color: "var(--text-secondary)", fontWeight: 600 }}>
                ● 파란 실선: 완료{forecast ? " / 파란 점선: 참고 LOT 예측" : ""}{dpmlSeries ? " / 검정 점선: DPML 비교" : ""}
              </span>
            }
            style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)" }}
          >
            {chartPoints.length ? (
              <FlowPlotlyChart
                chart={{
                  chart_type: "line",
                  points: [...chartPoints, ...dpmlPoints],
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
                    ...(dpmlSeries ? { [dpmlSeries]: "#000000" } : {}),
                  },
                  line_dash_map: {
                    [`${lot.lot_id}`]: "solid",
                    [`${lot.lot_id} (도착 예측)`]: "dash",
                    ...(dpmlSeries ? { [dpmlSeries]: "dash" } : {}),
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
                  {!forecast.eta && (
                    <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 4 }}>
                      목표 STEP까지 ETA를 계산하지 못했습니다. 목표 step이 참고 LOT에 있고 현재 공정보다 뒤쪽 단계인지 확인하세요.
                    </div>
                  )}
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
