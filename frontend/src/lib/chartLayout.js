/* lib/chartLayout.js — 차트 한 장의 "보기 좋은" 크기를 한곳에서 정한다.
 *
 * 예전에는 호출부마다 height 를 상수로 박아 넣었다(ChartBuilder 470, flow-i 430,
 * Trellis 350). 그런데 폭은 컨테이너를 그대로 따라가므로 flow-i 처럼 1760px 까지
 * 넓어지는 화면에서는 Corr scatter 가 1700×430(약 4:1)로 납작해져, 점구름이 옆으로
 * 눌린 띠가 되어 상관이 눈에 안 들어왔다.
 *
 * 그래서 "종류별 비율 + 종류별 최대폭"으로 바꾼다. 비율만 두고 폭을 화면 끝까지
 * 늘리면 Corr 가 1760×1240 이 되어 한 화면에 안 들어오므로, 최대폭이 함께 있어야
 * 한다. 실제 크기는 컨테이너 폭을 재서 계산한다 — 그래야 글자·마커가 확대되지
 * 않고 항상 제 픽셀 크기로 그려진다.
 */
import { useEffect, useMemo, useRef, useState } from "react";

const clamp = (value, low, high) => Math.max(low, Math.min(high, Math.round(value)));

/* aspect = 폭 ÷ 높이. 눈에 익은 화면 비율(4:3=1.33, 3:2=1.5, 16:10=1.6, 16:9=1.78)
 * 안에서 고른다 — 여기서 벗어난 3:1, 4:1 짜리 띠가 "납작하다"고 느껴지던 것이다.
 * 최대폭도 함께 두어, 화면이 넓다고 차트가 끝까지 늘어나지 않게 한다. */
export const CHART_LAYOUTS = {
  // 상관은 점구름의 기울기로 읽는다 — 가장 정사각에 가까운 4:3.
  corr: { aspect: 4 / 3, maxWidth: 780, minHeight: 320, maxHeight: 620, columnPx: 0 },
  // 시간축은 가로가 길어야 흐름이 분리돼 보이지만, 16:9 면 충분하다.
  trend: { aspect: 16 / 9, maxWidth: 1040, minHeight: 300, maxHeight: 560, columnPx: 0 },
  radius: { aspect: 16 / 9, maxWidth: 960, minHeight: 300, maxHeight: 540, columnPx: 0 },
  box: { aspect: 3 / 2, maxWidth: 1000, minHeight: 320, maxHeight: 560, columnPx: 78 },
  bar: { aspect: 16 / 10, maxWidth: 960, minHeight: 300, maxHeight: 520, columnPx: 68 },
  // 가로 막대는 높이가 막대 개수로 정해진다(rows 옵션).
  bar_horizontal: { aspect: 3 / 2, maxWidth: 900, minHeight: 300, maxHeight: 780, columnPx: 0 },
  // 원은 정사각이어야 조각 각도가 왜곡되지 않는다. 레전드 자리로 살짝 가로 여유.
  pie: { aspect: 1.15, maxWidth: 520, minHeight: 300, maxHeight: 520, columnPx: 0 },
  // Trellis 패널 — 격자 한 칸 안에서 여러 장을 나란히 비교하는 용도.
  panel: { aspect: 4 / 3, maxWidth: 700, minHeight: 240, maxHeight: 430, columnPx: 0 },
  panel_wide: { aspect: 16 / 9, maxWidth: 760, minHeight: 220, maxHeight: 400, columnPx: 0 },
};

const DEFAULT_LAYOUT = "corr";

// chart_type 과 몇 가지 플래그로 위 표의 키를 고른다. ChartBuilder 의 "Trend" 는
// chart_type 이 scatter 이고 trend_grain 으로만 구분되므로 그것까지 본다.
export function chartLayoutKind(chartType, flags = {}) {
  if (flags.panel) return flags.trend || flags.trendGrain ? "panel_wide" : "panel";
  const grain = String(flags.trendGrain || "");
  if (grain === "radius") return "radius";
  if (grain || flags.trend) return "trend";
  const type = String(chartType || "").replace("dashboard_", "");
  if (type === "pie" || type === "donut") return "pie";
  if (type === "box") return "box";
  if (type === "bar_horizontal") return "bar_horizontal";
  if (type === "bar") return "bar";
  if (type === "line" || type === "trend") return "trend";
  return DEFAULT_LAYOUT;
}

/**
 * 컨테이너 폭에서 실제 차트 상자 크기를 낸다.
 * @param {string} kind CHART_LAYOUTS 의 키
 * @param {number} containerWidth 실측 폭(px). 0 이면 최대폭으로 그린다.
 * @param {object} options rows(가로 막대 개수), columns(세로 카테고리 개수)
 */
export function chartBoxFor(kind, containerWidth, options = {}) {
  const spec = CHART_LAYOUTS[kind] || CHART_LAYOUTS[DEFAULT_LAYOUT];
  const available = Math.max(260, Math.round(Number(containerWidth) || spec.maxWidth));
  let width = Math.min(available, spec.maxWidth);
  // 카테고리가 서너 개뿐인데 최대폭을 다 쓰면 막대/박스가 허허벌판에 뜬다.
  const columns = Number(options.columns) || 0;
  if (columns > 0 && spec.columnPx > 0) {
    width = Math.min(width, Math.max(420, 170 + columns * spec.columnPx));
  }
  const rows = Number(options.rows) || 0;
  const height = rows > 0
    ? clamp(rows * (options.rowPx || 26) + (options.chromePx || 104), spec.minHeight, spec.maxHeight)
    : clamp(width / spec.aspect, spec.minHeight, spec.maxHeight);
  return { width, height };
}

// 컨테이너 실제 폭 관측 — 비율로 높이를 내려면 픽셀 폭이 필요하다.
export function useElementWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => setWidth(el.clientWidth || 0);
    update();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", update);
      return () => window.removeEventListener("resize", update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// 관측 + 계산을 한 번에. [ref, {width, height}] 를 돌려준다.
export function useChartBox(kind, options = {}) {
  const [ref, width] = useElementWidth();
  const { rows = 0, columns = 0, rowPx = 0, chromePx = 0 } = options;
  const box = useMemo(
    () => chartBoxFor(kind, width, { rows, columns, rowPx, chromePx }),
    [kind, width, rows, columns, rowPx, chromePx],
  );
  return [ref, box];
}

/* WF MAP 비교 격자 — wafer map 은 정사각이라 폭이 곧 크기다. auto-fit 격자에
 * 맡기면 패널이 여덟 칸으로 쪼개져 한 장이 210px 짜리 점 무더기가 된다. 패널
 * 수에 맞춰 열 수를 정하고(대략 정사각 배치) 한 장이 읽히는 크기를 유지한다. */
export function waferPanelGrid(panelCount, { minPanel = 260, maxPanel = 460 } = {}) {
  const count = Math.max(1, Number(panelCount) || 1);
  if (count === 1) return { columns: 1, maxWidth: 520 };
  const columns = Math.min(5, Math.max(2, Math.ceil(Math.sqrt(count))));
  return { columns, maxWidth: columns * maxPanel, minPanel };
}
