import { FlowPlotlyChart } from "../../components/PlotlyChart";

const rect = (x, y, w, h, color = "#94a3b8") => ({ type: "rect", x0: x, y0: y, x1: x+w, y1: y+h,
  line: { color, width: 1 }, fillcolor: "rgba(0,0,0,0)", layer: "below" });

export default function TegChatMaps({ maps }) {
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
    <small>실제 shot 중심과 TEG 기준 좌표(mm)를 표시합니다. 점은 TEG 기준점이고, 사각형은 기준 파일에 있는 실제 크기입니다.</small>
    {shot.tegs.filter((t) => !t.geometry_available).map((t) => <p key={t.teg}>{t.teg}: {t.unavailable_reason}</p>)}
  </section>;
}
