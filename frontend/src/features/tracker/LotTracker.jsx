import { useMemo, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import { PageShell } from "../../components/ui";
import { sf } from "../../lib/api";

const card = { border: "1px solid var(--border)", borderRadius: 8, padding: 16, background: "var(--bg-secondary)" };
const input = { padding: 9, border: "1px solid var(--border)", borderRadius: 5, background: "var(--bg-primary)", color: "var(--text-primary)" };

function chartPoint(point, series, timeKey = "tkout_time") {
  return {
    ...point,
    x: point.step_label || point.step_number,
    y: point.elapsed_days,
    series,
    time: point[timeKey],
    label: point.step_desc || point.step_id,
  };
}

export default function LotTracker() {
  const [form, setForm] = useState({ lot_id: "", product: "", reference_lot_id: "", target_step_id: "" });
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async (event) => {
    event.preventDefault();
    if (!form.lot_id.trim()) {
      setError("lot_id를 입력하세요.");
      return;
    }
    setBusy(true);
    setError("");
    setData(null);
    try {
      const query = new URLSearchParams(Object.entries(form).filter(([, value]) => value.trim()));
      const response = await sf(`/api/lot-tracker?${query}`);
      setData(response);
      if (!response?.ok) setError(response?.note || "LOT 이력을 찾지 못했습니다.");
    } catch (err) {
      setError(err.message || "LOT 이력을 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const lot = data?.lot;
  const reference = data?.reference;
  const forecast = data?.forecast;
  const chartPoints = useMemo(() => [
    ...(lot?.points || []).map((point) => chartPoint(point, `${lot.lot_id} 실제`)),
    ...(reference?.points || []).map((point) => chartPoint(point, `${reference.lot_id} 참고`)),
    ...(forecast?.points || []).map((point) => chartPoint(point, `${lot?.lot_id || "LOT"} 예측`, "eta")),
  ].filter((point) => String(point.x ?? "").trim() && Number.isFinite(Number(point.y))), [lot, reference, forecast]);

  return (
    <PageShell layout="analysis" style={{ padding: 24, maxWidth: 1200, margin: "0 auto", display: "grid", gap: 16 }}>
      <div>
        <h1 style={{ margin: 0 }}>LOT Tracker</h1>
        <p style={{ color: "var(--text-secondary)" }}>FAB TKOUT 이력과 참고 LOT의 실제 구간 속도로 목표 공정 도착 시점을 계산합니다.</p>
      </div>

      <form onSubmit={load} style={{ ...card, display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: 8 }}>
        {[
          ["lot_id", "LOT ID *", "정확한 FAB lot_id"],
          ["product", "Product", "중복 LOT일 때 입력"],
          ["reference_lot_id", "참고 LOT", "속도 기준 lot_id"],
          ["target_step_id", "목표 STEP", "예: AA200000"],
        ].map(([key, title, placeholder]) => (
          <label key={key} style={{ display: "grid", gap: 4, fontSize: 12 }}>
            {title}
            <input value={form[key]} placeholder={placeholder} onChange={(event) => setForm({ ...form, [key]: event.target.value })} style={input} />
          </label>
        ))}
        <button type="submit" disabled={busy} style={{ alignSelf: "end", padding: "10px 16px", border: 0, borderRadius: 5, background: "var(--accent)", color: "white", fontWeight: 700, cursor: "pointer" }}>
          {busy ? "조회 중…" : "조회"}
        </button>
      </form>

      {error && <div style={{ ...card, color: "var(--danger)" }}>{error}</div>}
      {lot && (
        <>
          <section style={{ ...card, display: "flex", flexWrap: "wrap", gap: "10px 24px" }}>
            <b>{lot.lot_id} · {lot.product || "제품 미상"}</b>
            <span>현재: {lot.current_step_id || "-"} {lot.current_step_desc || ""}</span>
            <span>최근 TKOUT: {lot.current_time || "-"}</span>
            <span>DPML: <b>{lot.dpml ?? "-"}</b> 일/Mask Layer ({lot.mask_layer_count || 0} layers)</span>
            {(lot.current_steps || []).length > 1 && (
              <span style={{ width: "100%", color: "var(--text-secondary)", fontSize: 12 }}>
                Wafer 현재 위치: {lot.current_steps.map((row) => `${row.step_id} ${row.step_desc || ""} (${row.wafer_count})`).join(" · ")}
              </span>
            )}
          </section>

          <section style={card}>
            {chartPoints.length ? (
              <FlowPlotlyChart
                chart={{ chart_type: "line", points: chartPoints, x_label: "Step number (00.0)", y_label: "Elapsed days", color_by: "series", title: "LOT progress" }}
                cfg={{ chart_type: "line", color_by: "series", height: 430 }}
              />
            ) : <div>step_desc 앞 숫자(00.0)가 매칭된 이력이 없습니다.</div>}
          </section>

          {forecast && (
            <section style={{ ...card, display: "grid", gap: 8 }}>
              <b>도착 예측 · {forecast.target_step_id || "-"} {forecast.target_step_desc || ""}</b>
              <div style={{ fontSize: 24, fontWeight: 800 }}>{forecast.eta || "계산 불가"}</div>
              <div>남은 기간 {forecast.remaining_days ?? "-"}일 · 참고 LOT 소요 {forecast.reference_days ?? "-"}일</div>
              <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>계산 근거: {forecast.basis || "-"}</div>
            </section>
          )}

          {data.note && <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>데이터 시차: {data.note}</div>}
        </>
      )}
    </PageShell>
  );
}
