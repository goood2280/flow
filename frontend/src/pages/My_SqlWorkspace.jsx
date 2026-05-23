// My_SqlWorkspace.jsx — flow 본진 v9.1 SQL 작업대.
// 여러 SQL 셀을 named view 로 등록 → 마지막 셀이 join/집계 → 결과 표 + 차트 + 스킬 저장.
// 백엔드 /api/sql-workspace/run 가 단일 DuckDB connection 으로 안전 검증 후 실행.

import { useEffect, useMemo, useState } from "react";
import { sf, postJson } from "../lib/api";

const DEFAULT_CELLS = [
  {
    name: "lot_meta",
    sql: "-- 첫 view: lot_meta\nSELECT * FROM (VALUES ('A1','R1'), ('A2','R2')) AS t(lot_id, root_lot_id)",
  },
  {
    name: "et_results",
    sql: "-- 두 번째 view: et_results\nSELECT * FROM (VALUES ('A1',10.0), ('A1',20.0), ('A2',30.0)) AS t(lot_id, value)",
  },
  {
    name: "",
    sql: "-- 마지막 셀 = final SELECT (이름 비움)\nSELECT m.root_lot_id, AVG(e.value) AS avg_v\nFROM lot_meta m JOIN et_results e USING(lot_id)\nGROUP BY m.root_lot_id\nORDER BY 1",
  },
];

export default function My_SqlWorkspace() {
  const [cells, setCells] = useState(DEFAULT_CELLS);
  const [rowLimit, setRowLimit] = useState(5000);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [cellTrace, setCellTrace] = useState([]);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [err, setErr] = useState("");
  const [filterText, setFilterText] = useState("");

  // 차트 옵션
  const [showChart, setShowChart] = useState(false);
  const [chartX, setChartX] = useState("");
  const [chartY, setChartY] = useState("");
  const [chartKind, setChartKind] = useState("bar");

  // Skill 저장 모달
  const [skillModal, setSkillModal] = useState(null);

  function updateCell(i, patch) {
    setCells((arr) => arr.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }
  function addCell() {
    setCells((arr) => [...arr.slice(0, -1), { name: `view_${arr.length}`, sql: "SELECT 1 AS a" }, arr[arr.length - 1]]);
  }
  function removeCell(i) {
    if (cells.length <= 1) return;
    setCells((arr) => arr.filter((_, idx) => idx !== i));
  }
  function moveCell(i, dir) {
    setCells((arr) => {
      const next = [...arr];
      const j = i + dir;
      if (j < 0 || j >= next.length) return next;
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  async function run() {
    setRunning(true);
    setErr("");
    setResult(null);
    setCellTrace([]);
    setShowChart(false);
    try {
      const body = {
        cells: cells.map((c, i) => ({
          // final cell = name 비움 — 사용자가 빈칸 두면 그대로 null
          name: (i === cells.length - 1) ? null : (c.name || null),
          sql: c.sql || "",
        })),
        row_limit: rowLimit,
      };
      const res = await postJson("/api/sql-workspace/run", body);
      setResult(res.result);
      setCellTrace(res.cells || []);
      setElapsedMs(res.elapsed_ms || 0);
      if (res.result?.columns?.length) {
        setChartX(res.result.columns[0]);
        const numCol = res.result.columns.find((c) =>
          res.result.rows.some((r) => typeof r[c] === "number")
        );
        setChartY(numCol || res.result.columns[1] || "");
      }
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setRunning(false);
    }
  }

  const filteredRows = useMemo(() => {
    if (!result?.rows) return [];
    const q = filterText.trim().toLowerCase();
    if (!q) return result.rows;
    return result.rows.filter((r) =>
      Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(q))
    );
  }, [result, filterText]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-primary)", overflow: "hidden" }}>
      {/* 헤더 */}
      <div style={headerBar}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>🧪 SQL 작업대</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
          여러 SELECT 를 named view 로 등록 후 마지막 셀에서 JOIN/집계
        </div>
        <div style={{ flex: 1 }} />
        <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 4 }}>
          행 상한
          <input
            type="number"
            value={rowLimit}
            onChange={(e) => setRowLimit(Math.max(1, Math.min(50000, Number(e.target.value) || 5000)))}
            style={{ ...inputStyle, width: 80, marginBottom: 0 }}
          />
        </label>
        <button onClick={run} disabled={running} style={btnPrimary}>
          {running ? "실행 중…" : "▶ 전체 실행"}
        </button>
        <button onClick={addCell} style={btnGhost}>＋ 셀 추가</button>
      </div>

      {/* 본문 */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* 좌측: 셀 리스트 */}
        <div style={{ width: "55%", overflowY: "auto", padding: 12, borderRight: "1px solid var(--border)" }}>
          {cells.map((c, i) => {
            const isFinal = i === cells.length - 1;
            const trace = cellTrace[i];
            return (
              <div key={i} style={cellBox(isFinal)}>
                <div style={cellHeader}>
                  <span style={cellBadge(isFinal)}>{isFinal ? "FINAL" : `view ${i + 1}`}</span>
                  {!isFinal && (
                    <input
                      type="text"
                      value={c.name || ""}
                      onChange={(e) => updateCell(i, { name: e.target.value })}
                      placeholder="view 이름 (영문/숫자/_)"
                      style={{ ...inputStyle, flex: 1, marginBottom: 0 }}
                    />
                  )}
                  {isFinal && <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>name 없이 → SELECT 결과만 반환</span>}
                  <div style={{ display: "flex", gap: 4 }}>
                    <button onClick={() => moveCell(i, -1)} style={btnIcon} title="위로">↑</button>
                    <button onClick={() => moveCell(i, +1)} style={btnIcon} title="아래로">↓</button>
                    <button onClick={() => removeCell(i)} style={btnIcon} title="삭제" disabled={cells.length <= 1}>✕</button>
                  </div>
                </div>
                <textarea
                  value={c.sql}
                  onChange={(e) => updateCell(i, { sql: e.target.value })}
                  rows={6}
                  style={textareaStyle}
                  placeholder={isFinal ? "최종 SELECT (앞 view 들 참조 가능)" : "SELECT ... FROM read_parquet(...) ..."}
                />
                {trace && (
                  <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
                    {trace.kind === "view"
                      ? `✓ view '${trace.name}' · ${trace.rowcount?.toLocaleString()} 행 · ${trace.ms}ms`
                      : `✓ final · ${trace.rowcount?.toLocaleString()} 행${trace.truncated ? " (truncated)" : ""} · ${trace.ms}ms`}
                  </div>
                )}
              </div>
            );
          })}
          {err && (
            <div style={errorBox}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>실행 오류</div>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, whiteSpace: "pre-wrap" }}>{err}</div>
            </div>
          )}
        </div>

        {/* 우측: 결과 + 차트 + 저장 */}
        <div style={{ flex: 1, overflowY: "auto", padding: 12, background: "var(--bg-secondary)" }}>
          {!result ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>
              ▶ 실행을 누르면 결과가 여기 나타납니다. 마지막 셀의 SELECT 결과만 반환됩니다.
            </div>
          ) : (
            <>
              <div style={resultHeader}>
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  {result.rowcount?.toLocaleString()} 행 · {result.columns?.length || 0} 컬럼 · {elapsedMs}ms
                  {result.truncated && <span style={{ color: "var(--warn)", marginLeft: 6 }}>(상한 초과 — 잘림)</span>}
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button onClick={() => setShowChart((v) => !v)} style={btnGhost}>
                    {showChart ? "차트 닫기" : "📊 차트"}
                  </button>
                  <button onClick={() => setSkillModal({ key: "", title: "", description: "" })} style={btnGhost}>
                    💾 스킬로 저장
                  </button>
                </div>
              </div>

              {showChart && (
                <ChartPanel
                  columns={result.columns || []}
                  rows={filteredRows}
                  x={chartX}
                  setX={setChartX}
                  y={chartY}
                  setY={setChartY}
                  kind={chartKind}
                  setKind={setChartKind}
                />
              )}

              <input
                type="text"
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                placeholder="🔍 결과에서 값 찾기"
                style={{ ...inputStyle, marginTop: 8, marginBottom: 8 }}
              />

              <ResultTable columns={result.columns || []} rows={filteredRows} />
            </>
          )}
        </div>
      </div>

      {skillModal && (
        <SkillSaveModal
          cells={cells}
          state={skillModal}
          onClose={() => setSkillModal(null)}
        />
      )}
    </div>
  );
}


function ResultTable({ columns, rows }) {
  if (!columns?.length) return <div style={{ color: "var(--muted)" }}>(빈 결과)</div>;
  return (
    <div style={{ maxHeight: 480, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-card)" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>
        <thead>
          <tr style={{ position: "sticky", top: 0, background: "var(--bg-secondary)", zIndex: 1 }}>
            {columns.map((c) => (
              <th key={c} style={thStyle}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={i % 2 ? { background: "var(--bg-primary)" } : undefined}>
              {columns.map((c) => (
                <td key={c} style={tdStyle}>{formatCell(r[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function ChartPanel({ columns, rows, x, setX, y, setY, kind, setKind }) {
  const xVals = rows.map((r) => r[x]);
  const yVals = rows.map((r) => r[y]);
  const max = Math.max(...yVals.filter((v) => typeof v === "number"), 0);

  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 6, padding: 12, marginBottom: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        <label style={chartCtrlLabel}>X<select value={x} onChange={(e) => setX(e.target.value)} style={selectStyle}>{columns.map((c) => <option key={c}>{c}</option>)}</select></label>
        <label style={chartCtrlLabel}>Y<select value={y} onChange={(e) => setY(e.target.value)} style={selectStyle}>{columns.map((c) => <option key={c}>{c}</option>)}</select></label>
        <label style={chartCtrlLabel}>형식
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={selectStyle}>
            <option value="bar">막대</option>
            <option value="line">선</option>
          </select>
        </label>
      </div>
      {/* 간단한 SVG 차트 — Plotly 없이 기본 막대/선 */}
      <svg width="100%" height={240} viewBox={`0 0 ${Math.max(420, xVals.length * 32)} 240`}
        style={{ background: "var(--bg-primary)", borderRadius: 4 }}>
        {kind === "bar" && xVals.map((xv, i) => {
          const yv = typeof yVals[i] === "number" ? yVals[i] : 0;
          const h = max > 0 ? (yv / max) * 190 : 0;
          return (
            <g key={i}>
              <rect x={i * 32 + 10} y={210 - h} width={22} height={h} fill="var(--accent)" rx={2} />
              <text x={i * 32 + 21} y={228} textAnchor="middle" fontSize="10" fill="var(--text-secondary)">{String(xv).slice(0, 6)}</text>
              <text x={i * 32 + 21} y={208 - h} textAnchor="middle" fontSize="9" fill="var(--text-primary)">{typeof yv === "number" ? yv.toFixed(1) : ""}</text>
            </g>
          );
        })}
        {kind === "line" && (
          <polyline
            fill="none"
            stroke="var(--accent)"
            strokeWidth={2}
            points={xVals.map((_, i) => {
              const yv = typeof yVals[i] === "number" ? yVals[i] : 0;
              const cx = i * 32 + 21;
              const cy = max > 0 ? 210 - (yv / max) * 190 : 210;
              return `${cx},${cy}`;
            }).join(" ")}
          />
        )}
      </svg>
    </div>
  );
}


function SkillSaveModal({ cells, state, onClose }) {
  const [key, setKey] = useState(state.key || "");
  const [title, setTitle] = useState(state.title || "");
  const [description, setDescription] = useState(state.description || "");
  const [shared, setShared] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errMsg, setErrMsg] = useState("");

  async function save() {
    setSaving(true);
    setErrMsg("");
    try {
      const body = {
        key: key.trim() || "untitled_skill",
        title: title.trim() || key,
        description: description.trim(),
        shared,
        cells: cells.map((c, i) => ({
          name: (i === cells.length - 1) ? null : (c.name || null),
          sql: c.sql,
        })),
      };
      const res = await postJson("/api/sql-workspace/save-skill", body);
      alert(`스킬 저장됨: ${res.skill.key}`);
      onClose();
    } catch (e) {
      setErrMsg(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={modalBackdrop} onClick={onClose}>
      <div style={modalBox} onClick={(e) => e.stopPropagation()}>
        <div style={{ fontWeight: 800, fontSize: 14, marginBottom: 12 }}>스킬로 저장</div>
        <label style={modalLabel}>키 (영문/숫자/_)</label>
        <input value={key} onChange={(e) => setKey(e.target.value)} style={inputStyle} placeholder="monthly_lot_avg_et" />
        <label style={modalLabel}>제목</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} style={inputStyle} placeholder="월별 Lot 평균 ET" />
        <label style={modalLabel}>설명</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} style={textareaStyle} placeholder="언제 쓸지, 어떤 파라미터를 바꾸면 좋을지" />
        <label style={{ ...modalLabel, display: "flex", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          다른 사용자와 공유
        </label>
        {errMsg && <div style={{ color: "var(--danger)", fontSize: 11, margin: "4px 0" }}>{errMsg}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 6, marginTop: 12 }}>
          <button onClick={onClose} style={btnGhost}>취소</button>
          <button onClick={save} disabled={saving || !key} style={btnPrimary}>{saving ? "저장 중…" : "저장"}</button>
        </div>
      </div>
    </div>
  );
}


function formatCell(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isFinite(v) ? (Math.abs(v) >= 1 ? v.toLocaleString() : v) : String(v);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}


// ── styles ──
const headerBar = {
  display: "flex", alignItems: "center", gap: 8,
  padding: "8px 12px", borderBottom: "1px solid var(--border)",
  background: "var(--bg-secondary)",
};
const inputStyle = {
  padding: "4px 8px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "var(--bg-primary)", color: "var(--text-primary)",
  marginBottom: 6,
};
const selectStyle = { ...inputStyle, marginBottom: 0 };
const textareaStyle = {
  width: "100%", padding: 8, fontSize: 12,
  fontFamily: "JetBrains Mono, monospace",
  border: "1px solid var(--border)", borderRadius: 4,
  background: "var(--bg-card)", color: "var(--text-primary)",
  resize: "vertical", lineHeight: 1.5,
  marginBottom: 4,
};
const btnPrimary = {
  padding: "5px 12px", fontSize: 12, fontWeight: 700,
  border: "1px solid var(--accent)", borderRadius: 4,
  background: "var(--accent)", color: "#fff", cursor: "pointer",
};
const btnGhost = {
  padding: "5px 10px", fontSize: 12,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "transparent", color: "var(--text-primary)", cursor: "pointer",
};
const btnIcon = {
  padding: "3px 7px", fontSize: 11,
  border: "1px solid var(--border)", borderRadius: 3,
  background: "var(--bg-card)", color: "var(--text-secondary)", cursor: "pointer",
};
const cellBox = (isFinal) => ({
  marginBottom: 10, padding: 8,
  border: "1px solid " + (isFinal ? "var(--accent)" : "var(--border)"),
  borderRadius: 6, background: "var(--bg-card)",
});
const cellHeader = { display: "flex", alignItems: "center", gap: 6, marginBottom: 6, flexWrap: "wrap" };
const cellBadge = (isFinal) => ({
  display: "inline-block", padding: "2px 8px", borderRadius: 3,
  background: isFinal ? "var(--accent)" : "var(--info)",
  color: "#fff", fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
});
const errorBox = {
  marginTop: 8, padding: 10,
  border: "1px solid var(--danger)", borderRadius: 6,
  background: "var(--danger-50)", color: "var(--text-primary)",
};
const resultHeader = { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 };
const thStyle = { textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--border)", fontWeight: 700, color: "var(--text-secondary)" };
const tdStyle = { padding: "4px 8px", borderBottom: "1px solid var(--border)", color: "var(--text-primary)" };
const chartCtrlLabel = { display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-secondary)" };
const modalBackdrop = {
  position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
  zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center",
};
const modalBox = {
  background: "var(--bg-secondary)", border: "1px solid var(--border)",
  borderRadius: 8, padding: 20, minWidth: 380, maxWidth: 520,
};
const modalLabel = { display: "block", fontSize: 11, color: "var(--text-secondary)", marginTop: 8, marginBottom: 4 };
