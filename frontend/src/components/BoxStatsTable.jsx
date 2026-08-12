import { useMemo, useState } from "react";
import { BOX_STAT_FIELDS, BOX_STATS_COLUMN_LIMIT, DEFAULT_BOX_STATS, boxStatsAlignment, formatBoxStat } from "../lib/boxStats";

/* box plot 아래에 붙는 상자별 통계표.
 *
 * 그림은 median 과 IQR 만 말해 주는데, 상자를 비교할 때 실제로 묻는 것은 "n 은
 * 몇이고 산포는 어느 쪽이 큰가"다. Spotfire 처럼 통계는 행, 상자는 열로 깔고
 * 어떤 값을 볼지 고르게 한다.
 *
 * geometry({left, plotWidth, width})를 받으면 열을 그림의 x 눈금과 같은 자리에
 * 맞춘다 — 상자 바로 아래에서 그 상자의 숫자를 읽을 수 있어야 하기 때문이다.
 * 없으면(폭을 못 잰 첫 렌더 등) 그냥 일반 표로 떨어진다.
 *
 * boxes: [{ label, stats }] — stats 는 lib/boxStats.js 가 만든 모양.
 */
const COLUMN_LIMIT = BOX_STATS_COLUMN_LIMIT;

export default function BoxStatsTable({ boxes = [], valueLabel = "value", defaultKeys = DEFAULT_BOX_STATS, geometry = null }) {
  const [selected, setSelected] = useState(() => new Set(defaultKeys));
  const shown = useMemo(() => boxes.slice(0, COLUMN_LIMIT), [boxes]);
  const fields = BOX_STAT_FIELDS.filter((field) => selected.has(field.key));
  if (!boxes.length) return null;

  const toggle = (key) => setSelected((old) => {
    const next = new Set(old);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });

  // 정렬 판정은 lib/boxStats 한 곳에서 — 차트의 x 눈금 표시 여부도 같은 판정을 쓴다.
  const { aligned, slot, left, width } = boxStatsAlignment(geometry, boxes.length);
  const nameWidth = aligned ? Math.max(left, 88) : 96;

  const cell = { padding: "5px 8px", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap", textAlign: "center", fontFamily: "'JetBrains Mono',monospace", overflow: "hidden", textOverflow: "ellipsis" };
  const headCell = { ...cell, fontWeight: 800, background: "#f1f5f9", color: "#0f172a", borderBottom: "2px solid #cbd5e1" };
  const nameCell = { ...cell, textAlign: "right", fontWeight: 800, color: "#0f172a", fontFamily: "inherit", paddingRight: 10 };

  const rows = [
    { key: "__head", name: "통계", head: true, values: shown.map((box) => box.label) },
    ...fields.map((field) => ({ key: field.key, name: field.label, head: false, values: shown.map((box) => formatBoxStat(box.stats?.[field.key], field)) })),
  ];

  return (
    <div style={{ marginTop: 4, width: aligned ? width : "100%", maxWidth: "100%", margin: "4px auto 0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", padding: "0 0 8px" }}>
        <strong style={{ fontSize: 12, color: "#0f172a" }}>통계표</strong>
        <span style={{ fontSize: 12, color: "#64748b" }}>{valueLabel} · 상자 {boxes.length.toLocaleString()}개</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 4, flexWrap: "wrap" }}>
          {BOX_STAT_FIELDS.map((field) => {
            const on = selected.has(field.key);
            return <button
              type="button"
              key={field.key}
              onClick={() => toggle(field.key)}
              style={{ border: `1px solid ${on ? "#2563eb" : "#cbd5e1"}`, borderRadius: 999, padding: "3px 9px", fontSize: 11, fontWeight: 800, cursor: "pointer", background: on ? "#dbeafe" : "#fff", color: on ? "#1d4ed8" : "#475569" }}
            >{field.label}</button>;
          })}
        </span>
      </div>
      {fields.length === 0
        ? <div style={{ padding: "10px 0", fontSize: 12, color: "#64748b" }}>표시할 통계를 하나 이상 선택해 주세요.</div>
        : <div style={{ overflowX: aligned ? "visible" : "auto", border: "1px solid #cbd5e1", borderRadius: 8, background: "#fff" }}>
          <div style={{ minWidth: aligned ? undefined : 320 }}>
            {rows.map((row) => (
              <div
                key={row.key}
                style={{
                  display: "grid",
                  gridTemplateColumns: aligned
                    ? `${nameWidth}px repeat(${shown.length}, ${slot}px)`
                    : `${nameWidth}px repeat(${shown.length}, minmax(72px, 1fr))`,
                  fontSize: 12,
                  color: "#334155",
                  background: row.head ? "#f1f5f9" : "#fff",
                }}
              >
                <div style={row.head ? { ...nameCell, ...headCell, textAlign: "right" } : nameCell}>{row.name}</div>
                {row.values.map((value, index) => (
                  <div key={`${row.key}-${shown[index]?.key || index}`} style={row.head ? headCell : cell} title={row.head ? String(value) : undefined}>{value}</div>
                ))}
              </div>
            ))}
          </div>
        </div>}
      {boxes.length > COLUMN_LIMIT
        ? <div style={{ padding: "7px 2px 0", fontSize: 12, color: "#b45309" }}>상자가 {COLUMN_LIMIT}개를 넘어 앞 {COLUMN_LIMIT}개만 실었습니다.</div>
        : !aligned && <div style={{ padding: "7px 2px 0", fontSize: 12, color: "#64748b" }}>상자가 많아 한 칸에 숫자가 들어가지 않아, 그림 눈금에 맞추지 않고 표로만 싣습니다.</div>}
    </div>
  );
}
