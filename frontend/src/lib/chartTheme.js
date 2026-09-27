/* Shared chart look for FlowPlotlyChart, Template Report and PPTX capture.
   Report-grade data visualisation: the app typeface, a dark x/y frame that
   survives a projector (1.5px axis lines, outside ticks, light grid), markers
   ringed in a thin near-black line, left-aligned titles, a legend
   above the plot, and the validated categorical palette from UXKit (adjacent
   colours stay distinguishable for colour-vision deficiency). Charts and the
   report legend must take series colours from here so they always match. */
import { categoricalSeries } from "../components/UXKit";

export const CHART_FONT_FAMILY =
  '"Pretendard Variable", Pretendard, "IBM Plex Sans KR", "IBM Plex Sans", "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif';

const LIGHT = {
  bg: "#ffffff",
  title: "#161616",
  subtitle: "#6f6f6f",
  axisTitle: "#262626",
  tick: "#393939",
  axisLine: "#262626",
  grid: "#e8e8e8",
  markerLine: "rgba(17,17,17,0.85)",
  zero: "#a8a8a8",
  hoverBg: "#ffffff",
  hoverBorder: "#c6c6c6",
  reference: "#da1e28",
  highlightFill: "rgba(241,194,27,0.16)",
  highlightLine: "rgba(178,134,0,0.6)",
  highlightText: "#8e6a00",
};

const DARK = {
  bg: "#161616",
  title: "#f4f4f4",
  subtitle: "#a8a8a8",
  axisTitle: "#e0e0e0",
  tick: "#c6c6c6",
  axisLine: "#c6c6c6",
  grid: "#2e2e2e",
  markerLine: "rgba(0,0,0,0.9)",
  zero: "#6f6f6f",
  hoverBg: "#262626",
  hoverBorder: "#525252",
  reference: "#fa4d56",
  highlightFill: "rgba(241,194,27,0.14)",
  highlightLine: "rgba(241,194,27,0.6)",
  highlightText: "#f1c21b",
};

export function chartColors(dark = false) {
  return dark ? DARK : LIGHT;
}

export function chartSeries(dark = false) {
  return dark ? categoricalSeries.dark : categoricalSeries.light;
}

export function seriesColor(index, dark = false) {
  const palette = chartSeries(dark);
  return palette[((index % palette.length) + palette.length) % palette.length];
}

/* Axis defaults shared by x and y. Callers spread their own range/type after. */
export function axisStyle(theme, { titleSize = 14, tickSize = 12, lineWidth = 1.5, showGrid = true } = {}) {
  return {
    titleFont: { size: titleSize, color: theme.axisTitle, family: CHART_FONT_FAMILY },
    tickfont: { size: tickSize, color: theme.tick, family: CHART_FONT_FAMILY },
    showgrid: showGrid,
    gridcolor: theme.grid,
    gridwidth: 1,
    zerolinecolor: theme.zero,
    zerolinewidth: 1,
    showline: true,
    linecolor: theme.axisLine,
    linewidth: lineWidth,
    ticks: "outside",
    ticklen: 5,
    tickwidth: 1.2,
    tickcolor: theme.axisLine,
    mirror: false,
    automargin: true,
  };
}
