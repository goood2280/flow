import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import { sf } from "../../lib/api";

// Keep the home chat read-only, but render the same geometry as the native TEG
// page.  The page module owns the SVG implementation; loading these exports on
// demand avoids making every Home visit pay for the full TEG page bundle.
const NativeWaferMap = lazy(() => import("../teg/My_TegMap").then((module) => ({ default: module.WaferMap })));
const NativeShotZoom = lazy(() => import("../teg/My_TegMap").then((module) => ({ default: module.ShotZoom })));

const rect = (x, y, w, h, color = "#94a3b8") => ({ type: "rect", x0: x, y0: y, x1: x+w, y1: y+h,
  line: { color, width: 1 }, fillcolor: "rgba(0,0,0,0)", layer: "below" });

export default function TegChatMaps({ maps, view }) {
  if (view?.geometry?.fit === "radius" && view?.shots?.length && view?.tegs?.length) return <NativeTegView key={`${view.product}:${view.selected_tegs?.join(",")}`} view={view} />;
  if (!maps) return null;
  if (!maps.available) return <p role="status">{maps.unavailable_reason}</p>;
  const wafer = maps.wafer;
  const shot = maps.within_shot;
  const r = wafer.radius_mm;
  const waferShapes = [{ type: "circle", x0: -r, y0: -r, x1: r, y1: r, line: { color: "#64748b", width: 2 }, layer: "below" },
    ...wafer.shots.map((s) => rect(s.center_x_mm-wafer.shot_width_mm/2, s.center_y_mm-wafer.shot_height_mm/2, wafer.shot_width_mm, wafer.shot_height_mm))];
  const waferPoints = wafer.shots.flatMap((s) => shot.tegs.map((t) => ({
    x: s.center_x_mm+t.x_mm, y: s.center_y_mm+t.y_mm, color: t.teg, label: `${t.teg} · Shot (${s.shot_x}, ${s.shot_y})`,
  })));
  const shotShapes = [rect(shot.x_min_mm, shot.y_min_mm, shot.x_max_mm-shot.x_min_mm, shot.y_max_mm-shot.y_min_mm, "#64748b"),
    ...shot.tegs.filter((t) => t.geometry_available).map((t) => rect(t.x_mm, t.y_mm, t.width_mm, t.height_mm, "#2563eb"))];
  const shotPoints = shot.tegs.map((t) => ({ x: t.x_mm, y: t.y_mm, color: t.teg, label: t.teg }));
  const config = { chart_type: "scatter", color_by: "teg", x_label: "X (mm)", y_label: "Y (mm)", show_legend: true };
  const boundsX = [shot.x_min_mm, shot.x_max_mm, ...shot.tegs.flatMap((t) => [t.x_mm, t.x_mm+(t.width_mm||0)])];
  const boundsY = [shot.y_min_mm, shot.y_max_mm, ...shot.tegs.flatMap((t) => [t.y_mm, t.y_mm+(t.height_mm||0)])];
  return <section aria-label="TEG 위치 그림" style={{ display: "grid", gap: 12 }}>
    <h4>Wafer map · 선택한 TEG</h4>
    <FlowPlotlyChart chart={{ ...config, chart_type: "scatter", points: waferPoints }} cfg={config}
      geometryOverlay={{ shapes: waferShapes, xRange: [-r*1.05, r*1.05], yRange: [-r*1.05, r*1.05] }} />
    <h4>샷 내 위치 · 실제 크기</h4>
    <FlowPlotlyChart chart={{ ...config, chart_type: "scatter", points: shotPoints }} cfg={config}
      geometryOverlay={{ shapes: shotShapes, xRange: [Math.min(...boundsX)-1, Math.max(...boundsX)+1], yRange: [Math.min(...boundsY)-1, Math.max(...boundsY)+1] }} />
    <div className="home-teg-view__coordinates">
      <strong>선택된 TEG 좌표</strong>
      {shot.tegs.map((teg) => <span key={teg.teg}>{teg.teg} · ({teg.x_mm}, {teg.y_mm}) mm</span>)}
    </div>
    {shot.tegs.filter((t) => !t.geometry_available).map((t) => <p key={t.teg}>{t.teg}: {t.unavailable_reason}</p>)}
  </section>;
}

function NativeTegView({ view }) {
  const [selectedShot, setSelectedShot] = useState(null);
  const data = view?.data || view;
  const tegs = Array.isArray(data?.tegs) ? data.tegs : [];
  const selectedNames = Array.isArray(view?.selected_tegs) && view.selected_tegs.length
    ? view.selected_tegs
    : tegs.map((item) => item.teg).filter(Boolean);
  const selectedTegs = useMemo(() => new Set(selectedNames), [selectedNames.join("\u0000")]);
  const colors = ["#e05252", "#3e7bd6", "#2f9e63", "#c78a1e", "#8a5fd0", "#d0568f", "#1fa0a8"];
  const colorMap = useMemo(() => new Map(
    tegs.map((item, index) => [item.teg, colors[index % colors.length]]),
  ), [tegs]);
  const tegColor = (name) => colorMap.get(name) || colors[0];
  const selectedRows = tegs.filter((item) => selectedTegs.has(item.teg));
  const [shapeData, setShapeData] = useState(null);
  const [shapeLoading, setShapeLoading] = useState(false);
  useEffect(() => {
    let active = true;
    const mode = data?.display?.mode;
    if (!["image", "dev_grid"].includes(mode) || !data?.vehicle) {
      setShapeData(null);
      return () => { active = false; };
    }
    setShapeLoading(true);
    sf(`/api/teg-map/image/shapes?vehicle=${encodeURIComponent(data.vehicle)}`)
      .then((result) => { if (active) setShapeData(result || null); })
      .catch(() => { if (active) setShapeData(null); })
      .finally(() => { if (active) setShapeLoading(false); });
    return () => { active = false; };
  }, [data?.display?.mode, data?.vehicle]);
  const dieCells = data?.display?.mode === "dev_grid"
    ? (shapeData?.dev_cells || [])
    : data?.display?.mode === "image"
      ? (shapeData?.image_cells || [])
      : [];
  if (!data?.geometry || !data?.shots?.length || !tegs.length) return null;

  return (
    <Suspense fallback={<p role="status">TEG 위치 화면을 불러오는 중…</p>}>
      <section aria-label="TEG 위치 그림" style={{ display: "grid", gap: 12 }}>
        <h4>TEG 위치 조회 · 웨이퍼 맵</h4>
        <div style={{ overflowX: "auto" }}>
          <NativeWaferMap data={data} selectedTegs={selectedTegs} tegColor={tegColor}
            selectedShot={selectedShot} onShotClick={setSelectedShot} light />
        </div>
        <h4>샷 확대 · 격자와 TEG 위치</h4>
        <div className="home-teg-view__shot-row">
          <div style={{ overflowX: "auto" }}>
            <NativeShotZoom data={data} selectedTegs={selectedTegs} tegColor={tegColor} dieCells={dieCells} size={480} />
            {shapeLoading && <small>샷 격자를 불러오는 중…</small>}
          </div>
          <div className="home-teg-view__coordinates">
            <strong>선택된 TEG 좌표</strong>
            {selectedRows.map((teg) => (
              <span key={teg.teg}>
                {teg.teg} · ({Number(teg.ebeam_x).toLocaleString(undefined, { maximumFractionDigits: 6 })}, {Number(teg.ebeam_y).toLocaleString(undefined, { maximumFractionDigits: 6 })}) mm
              </span>
            ))}
          </div>
        </div>
      </section>
    </Suspense>
  );
}
