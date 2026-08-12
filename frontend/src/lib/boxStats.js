/* lib/boxStats.js — box plot 아래에 붙는 통계표의 계산부.
 *
 * Spotfire 의 box plot 은 그림 아래에 상자별 통계를 표로 깔고, 어떤 값을 볼지
 * 고르게 한다. 그림은 median/IQR 만 보여주는데 실제 판단에는 n·표준편차·CV 가
 * 같이 필요해서다. 여기서는 그 값들을 한 번에 계산하고, 표시 여부는 화면이
 * 고르게 한다(components/BoxStatsTable.jsx).
 *
 * 인접값(LAV/UAV)은 Tukey 정의를 쓴다 — 울타리(Q1-1.5IQR, Q3+1.5IQR) 자체가
 * 아니라 그 안쪽에 실제로 존재하는 마지막 표본이다. plotly box 의 수염 끝과
 * 같은 값이라 표와 그림이 어긋나지 않는다.
 */

export const BOX_STAT_FIELDS = [
  { key: "n", label: "Count", integer: true },
  { key: "max", label: "Max" },
  { key: "uav", label: "UAV" },
  { key: "q3", label: "Q3" },
  { key: "median", label: "Median" },
  { key: "q1", label: "Q1" },
  { key: "lav", label: "LAV" },
  { key: "min", label: "Min" },
  { key: "mean", label: "Mean" },
  { key: "std", label: "StdDev" },
  { key: "iqr", label: "IQR" },
  { key: "range", label: "Range" },
  { key: "p10", label: "P10" },
  { key: "p90", label: "P90" },
  { key: "cv", label: "CV%" },
  { key: "outliers", label: "Outliers", integer: true },
];

export const DEFAULT_BOX_STATS = ["n", "median", "mean", "std", "q1", "q3"];

const number = (value) => {
  const n = Number(value);
  return Number.isFinite(n) && String(value ?? "").trim() !== "" ? n : null;
};

function quantile(sorted, q) {
  if (!sorted.length) return null;
  const position = (sorted.length - 1) * q;
  const low = Math.floor(position);
  const high = Math.ceil(position);
  return sorted[low] + (sorted[high] - sorted[low]) * (position - low);
}

export function computeBoxStats(rawValues) {
  const values = (rawValues || []).map(number).filter((value) => value != null);
  const n = values.length;
  if (!n) return { n: 0 };
  const sorted = [...values].sort((a, b) => a - b);
  const q1 = quantile(sorted, 0.25);
  const median = quantile(sorted, 0.5);
  const q3 = quantile(sorted, 0.75);
  const iqr = q3 - q1;
  const mean = sorted.reduce((sum, value) => sum + value, 0) / n;
  // 표본 표준편차(n-1). 상자 하나에 표본이 1개면 정의되지 않는다.
  const std = n > 1 ? Math.sqrt(sorted.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (n - 1)) : null;
  const lowFence = q1 - 1.5 * iqr;
  const highFence = q3 + 1.5 * iqr;
  const inside = sorted.filter((value) => value >= lowFence && value <= highFence);
  return {
    n,
    min: sorted[0],
    max: sorted[n - 1],
    q1,
    median,
    q3,
    iqr,
    mean,
    std,
    range: sorted[n - 1] - sorted[0],
    lav: inside.length ? inside[0] : sorted[0],
    uav: inside.length ? inside[inside.length - 1] : sorted[n - 1],
    p10: quantile(sorted, 0.1),
    p90: quantile(sorted, 0.9),
    cv: mean !== 0 && std != null ? (std / Math.abs(mean)) * 100 : null,
    outliers: n - inside.length,
  };
}

/* 이미 집계돼 내려온 5수 요약(flow-i dashboard_box)을 같은 모양으로 맞춘다.
 * 원 표본이 없으므로 표본에서만 나오는 값(LAV/UAV/P10/P90/이상치 수)은 비운다. */
export function boxStatsFromSummary(box) {
  const q1 = number(box?.q1);
  const q3 = number(box?.q3);
  const min = number(box?.min);
  const max = number(box?.max);
  const mean = number(box?.mean);
  const std = number(box?.std ?? box?.stdev ?? box?.sd);
  return {
    n: number(box?.n) ?? 0,
    min,
    max,
    q1,
    median: number(box?.median),
    q3,
    iqr: q1 != null && q3 != null ? q3 - q1 : null,
    mean,
    std,
    range: min != null && max != null ? max - min : null,
    cv: mean != null && std != null && mean !== 0 ? (std / Math.abs(mean)) * 100 : null,
  };
}

/* 화면의 box 하나하나를 짚어낸다. FlowPlotlyChart 는 색 계열마다 trace 를 만들고
 * plotly 가 그 안에서 x 값마다 상자를 쪼개므로, 상자의 정체는 (색 값, x 값)이다. */
export function boxBucketsFromPoints(points, colorBy = "") {
  const buckets = new Map();
  for (const point of points || []) {
    const y = number(point?.y ?? point?.median ?? point?.avg ?? point?.value);
    if (y == null) continue;
    const x = String(point?.x_label ?? point?.tkout_time ?? point?.time ?? point?.x ?? "");
    const group = colorBy ? String(point?.color_value ?? point?.color ?? point?.series ?? "").trim() || "missing" : "";
    const key = `${group}${x}`;
    if (!buckets.has(key)) buckets.set(key, { key, x, group, values: [] });
    buckets.get(key).values.push(y);
  }
  // 표의 열 순서는 그림의 x 순서와 같아야 한다. x 가 전부 숫자면 숫자로 정렬한다
  // — 문자 정렬에 맡기면 음수가 -1,-2,-3 으로 뒤집혀 그림과 어긋난다.
  const rows = [...buckets.values()];
  const numericX = rows.every((bucket) => number(bucket.x) != null);
  return rows
    .sort((a, b) => a.group.localeCompare(b.group)
      || (numericX ? number(a.x) - number(b.x) : a.x.localeCompare(b.x, undefined, { numeric: true })))
    .map((bucket) => ({
      key: bucket.key,
      label: bucket.group ? `${bucket.group} · ${bucket.x}` : bucket.x || "(전체)",
      stats: computeBoxStats(bucket.values),
    }));
}

/* 상자(=x 카테고리)의 표시 순서. 전부 숫자면 숫자로, 아니면 자연 정렬.
 * 그림의 categoryarray 와 표의 열 순서가 같은 규칙을 써야 둘이 맞물린다. */
export function sortCategoryValues(values) {
  const list = [...new Set((values || []).map((value) => String(value ?? "")))].filter((value) => value !== "");
  const allNumeric = list.length > 0 && list.every((value) => number(value) != null);
  return list.sort((a, b) => (allNumeric ? number(a) - number(b) : a.localeCompare(b, undefined, { numeric: true })));
}

/* 통계표를 그림의 x 눈금에 맞춰 붙일 수 있는지 판정한다.
 *
 * 상자 밑에 그 상자의 숫자가 오는 게 제일 좋지만, 상자가 스물다섯 개쯤 되면 한 칸이
 * 24px 라 "13.2477" 이 "1…" 로 잘린다. 그럴 때는 정렬을 포기하고 가로 스크롤 표로
 * 떨어뜨리는 편이 낫다. 화면(표)과 차트(x 눈금 표시 여부)가 같은 판정을 써야 하므로
 * 판정은 이 함수 하나에서만 한다. */
export const BOX_STATS_COLUMN_LIMIT = 60;
const MIN_ALIGNED_SLOT = 46;

export function boxStatsAlignment(geometry, boxCount) {
  const usable = boxCount > 0 && boxCount <= BOX_STATS_COLUMN_LIMIT;
  const slot = usable && geometry?.plotWidth ? geometry.plotWidth / boxCount : 0;
  return {
    aligned: slot >= MIN_ALIGNED_SLOT,
    slot,
    left: geometry?.left || 0,
    width: geometry?.width || 0,
  };
}

export function formatBoxStat(value, field) {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  if (field?.integer) return Number(value).toLocaleString();
  const abs = Math.abs(Number(value));
  if (abs !== 0 && (abs < 0.001 || abs >= 1e7)) return Number(value).toExponential(3);
  return Number(Number(value).toFixed(4)).toLocaleString(undefined, { maximumFractionDigits: 4 });
}
