/* TegCheck.jsx — TEG 설비값 검사 (TEG 위치 조회 페이지의 "설비값 검사" 탭).
   설비에서 복사한 레시피 원문을 백엔드(/api/teg-map/inspect)로 보내
   ① 전체 Pattern 의 site 좌표를 작은 WF MAP 카드로 한번에 표시 (클릭 → 확대),
   ② #teg-map 의 module 좌표를 flat 변환(v_R = 반시계 90° 회전 원복) 후
      정답지(TEG 위치 조회의 Teg_location raw ebeam 값)와 대조해 🟢/🔴/⚪ 로 표시.
*/
import { useMemo, useState } from "react";
import { postJson } from "../lib/api";
import { toast } from "../components/Toast";
import { Button, Card, DataTable, EmptyState, Pill, Select, Textarea } from "../components/UXKit";

const API = "/api/teg-map";

const MAP_COLORS = { measure: "#f97316", other: "#cbd5e1" };
const SITE_HL = "#dc2626";
const TEG_HL = "#2563eb";
const MAX_CELLS = 400000;    // 렌더 상한 (w*h)
const GRID_LINE_MAX = 6000;  // 이 이상이면 격자선 생략

const STATUS_ICON = { match: "🟢", mismatch: "🔴", missing: "⚪", noref: "—" };

function classify(ch) {
  if (ch === "-" || ch === "." || ch === " " || ch === undefined) return "empty";
  if (ch === "t" || ch === "T") return "measure";
  return "other";
}

function cellAt(map, x, y) {
  if (!Number.isInteger(x) || !Number.isInteger(y)) return null;
  if (y < 1 || y > map.h || x < 1 || x > map.w) return null;
  return map.rows[y - 1][x - 1];
}

function siteStatus(map, x, y) {
  const ch = cellAt(map, x, y);
  if (ch === null) return "범위밖";
  return classify(ch) === "measure" ? "측정" : "빈칸";
}

function fmtN(v) {
  if (v === null || v === undefined) return "-";
  return String(v);
}

/* ── 웨이퍼 맵 SVG — 좌상단 = (1,1), x→오른쪽, y→아래.
   행을 같은 색 연속 구간으로 묶어 rect 수를 줄인다 (PoC svg_map 포팅). ── */
function WfSvg({ map, sitesHl = [], tegHl = [], px = 6, showLabels = false }) {
  const { rows, w, h } = map;
  const rects = useMemo(() => {
    const out = [];
    rows.forEach((row, y) => {
      let s = 0, k = classify(row[0]);
      for (let i = 1; i <= row.length; i++) {
        const k2 = i < row.length ? classify(row[i]) : null;
        if (k2 !== k) {
          if (k !== "empty") out.push({ x: s, y, w: i - s, k });
          s = i; k = k2;
        }
      }
    });
    return out;
  }, [rows]);

  if (w * h > MAX_CELLS) {
    return <div style={{ fontSize: 12, color: "var(--danger, #e05252)" }}>
      맵이 너무 큽니다 ({w}×{h}={w * h} 셀) — 파싱 오류일 수 있습니다.
    </div>;
  }

  const showGrid = px >= 8 && w * h <= GRID_LINE_MAX;
  let gridD = "";
  if (showGrid) {
    for (let x = 0; x <= w; x++) gridD += `M${x} 0V${h}`;
    for (let y = 0; y <= h; y++) gridD += `M0 ${y}H${w}`;
  }
  const hlText = showLabels;   // 강조 라벨은 확대 뷰에서만

  return (
    <svg viewBox={`0 0 ${w} ${h}`} width={w * px} height={h * px}
      style={{ maxWidth: "100%", height: "auto", background: "#fff",
               border: "1px solid var(--line)", borderRadius: 4, display: "block" }}>
      {rects.map((r, i) => (
        <rect key={i} x={r.x} y={r.y} width={r.w} height={1} fill={MAP_COLORS[r.k]} />
      ))}
      {showGrid && <path d={gridD} stroke="#94a3b8" strokeWidth="0.02" fill="none" />}
      {showLabels && w * h <= 2000 && rows.map((row, y) =>
        [...row].map((ch, x) => classify(ch) !== "empty" && (
          <text key={`${x},${y}`} x={x + 0.5} y={y + 0.68} fontSize="0.5"
            textAnchor="middle" fill="#0f172a">{ch}</text>
        )))}
      {[[sitesHl, SITE_HL], [tegHl, TEG_HL]].map(([hl, color], gi) =>
        hl.filter(p => p.x >= 1 && p.x <= w && p.y >= 1 && p.y <= h).map((p, i) => (
          <g key={`${gi}-${i}`}>
            <rect x={p.x - 1} y={p.y - 1} width={1} height={1}
              fill="none" stroke={color} strokeWidth="0.09" />
            {hlText && (
              <>
                <text x={p.x - 0.5} y={p.y - 0.56} fontSize="0.36" textAnchor="middle"
                  fontWeight="bold" fill={color}>{p.label}</text>
                <text x={p.x - 0.5} y={p.y - 0.18} fontSize="0.2" textAnchor="middle"
                  fill={color}>({p.x},{p.y})</text>
              </>
            )}
          </g>
        )))}
    </svg>
  );
}

/* ── Pattern 카드 그리드 — 전체 pattern 의 WF MAP 을 작게 한번에 표시 ── */
function PatternGrid({ res, px, selected, onSelect, mapFor }) {
  const maps = res.maps;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "flex-start" }}>
      {res.patterns.map((p, i) => {
        const map = mapFor(i);
        const ok = p.points.filter(pt => siteStatus(map, pt.x, pt.y) === "측정").length;
        const allOk = ok === p.points.length;
        const isSel = selected === i;
        return (
          <div key={p.name} onClick={() => onSelect(isSel ? null : i)}
            style={{
              border: `1px solid ${isSel ? "var(--accent, #5a8cff)" : "var(--line)"}`,
              borderRadius: 6, padding: 8, cursor: "pointer",
              background: isSel ? "var(--panel)" : "transparent",
            }}>
            <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 700, maxWidth: 160,
                             overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                title={p.name}>{p.name}</span>
              <Pill tone={allOk ? "ok" : "danger"} size="sm">{ok}/{p.points.length}</Pill>
            </div>
            <WfSvg map={map} px={px}
              sitesHl={p.points.map(pt => ({ x: pt.x, y: pt.y, label: pt.pt }))} />
            {maps.length > 1 && (
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                {map.name} · {map.w}×{map.h}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── TEG 대조 섹션 — flat 선택 + 🟢/🔴/⚪ 대조표 + 맵 표시 ── */
function TegSection({ res, onFlatChange, mapIdx, setMapIdx }) {
  const teg = res.teg;
  const { summary } = teg;
  const bad = teg.rows.filter(r => r.status === "mismatch");
  const [showAll, setShowAll] = useState(false);
  const flatUsed = res.flat.used;

  const fullCols = [
    { key: "st", label: "", width: 30, render: r => STATUS_ICON[r.status] || "" },
    { key: "name", label: "module_name" },
    { key: "orig", label: "원본 (x,y)", render: r => `(${r.x},${r.y})` },
    { key: "calc_x", label: "EbeamX", align: "right" },
    { key: "calc_y", label: "EbeamY", align: "right" },
    { key: "ref", label: "정답지 (x,y)", render: r =>
        r.status === "missing" ? "없음" : r.status === "noref" ? "-" : `(${r.ref_x},${r.ref_y})` },
    { key: "note", label: "비고", render: r => r.rule_note || "" },
  ];
  const badCols = [
    { key: "name", label: "module_name" },
    { key: "calc_x", label: "계산 X", align: "right" },
    { key: "ref_x", label: "정답 X", align: "right", render: r => fmtN(r.ref_x) },
    { key: "dx", label: "ΔX", align: "right", render: r => fmtN(r.dx) },
    { key: "calc_y", label: "계산 Y", align: "right" },
    { key: "ref_y", label: "정답 Y", align: "right", render: r => fmtN(r.ref_y) },
    { key: "dy", label: "ΔY", align: "right", render: r => fmtN(r.dy) },
  ];

  const map = res.maps[Math.min(mapIdx, res.maps.length - 1)];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {!teg.ref_ok && (
        <div style={{ fontSize: 13, color: "var(--danger, #e05252)" }}>
          정답지를 못 읽었습니다 — {teg.ref_error}
        </div>
      )}
      {teg.ref_ok && (
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          정답지: {teg.ref_path} · {teg.ref_count}건 (TEG 위치 조회의 Teg_location raw ebeam 값)
        </div>
      )}

      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        {res.flat.detected
          ? <Pill tone="ok" title={res.flat.why}>Flat 자동 감지: {res.flat.detected}</Pill>
          : <span style={{ fontSize: 12, color: "var(--muted)" }}>
              꼬리표에서 H_PCHK / V_PCHK 를 찾지 못해 수동 선택입니다.
            </span>}
        {["h", "v_R"].map(f => (
          <label key={f} style={{ display: "inline-flex", alignItems: "center", gap: 4,
                                  fontSize: 13, cursor: "pointer" }}>
            <input type="radio" name="teg-check-flat" checked={flatUsed === f}
              onChange={() => onFlatChange(f)} />
            {f}
          </label>
        ))}
        {flatUsed === "v_R" && (
          <Pill tone="neutral" title="v_R = 설비의 반시계 90° 회전 세팅을 원복: (x, y) → (y, -x + offset)">
            {res.v_r_note}
          </Pill>
        )}
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          {res.offset.known
            ? `${res.vehicle} / ${flatUsed} 오프셋: x'=${res.offset.dx}, y'=${res.offset.dy}`
            : `'${res.vehicle || "(제품명 없음)"}' 는 PCHK_OFFSETS 미등록 — 기본 오프셋 (0, 0)`}
        </span>
      </div>

      {teg.ref_ok && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Pill tone="ok">🟢 일치 {summary.match}</Pill>
          <Pill tone={summary.mismatch ? "danger" : "neutral"}>🔴 불일치 {summary.mismatch}</Pill>
          <Pill tone="neutral">⚪ 정답지 미등록 {summary.missing}</Pill>
        </div>
      )}

      {teg.ref_ok && (bad.length ? (
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--danger, #e05252)", marginBottom: 6 }}>
            🔴 불일치 {bad.length}건 — 정답지와 다릅니다
          </div>
          <DataTable columns={badCols} rows={bad} maxHeight={260} />
        </div>
      ) : (
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ok, #2f9e63)" }}>🟢 불일치 없음</div>
      ))}

      <div>
        <button onClick={() => setShowAll(v => !v)}
          style={{ fontSize: 12, color: "var(--accent, #5a8cff)", background: "none",
                   border: "none", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
          {showAll ? "▾" : "▸"} 전체 {teg.rows.length}건
        </button>
        {(showAll || (teg.ref_ok && !bad.length)) && (
          <DataTable columns={fullCols} rows={teg.rows} maxHeight={320} />
        )}
      </div>

      {res.maps.length > 0 && (
        <div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>계산 좌표 맵 표시 (범위 안만)</span>
            {res.maps.length > 1 && (
              <Select value={mapIdx} onChange={e => setMapIdx(Number(e.target.value))}>
                {res.maps.map((m, i) => <option key={i} value={i}>{m.name}</option>)}
              </Select>
            )}
          </div>
          <WfSvg map={map} px={14} showLabels
            tegHl={teg.rows.map(r => ({ x: r.calc_x, y: r.calc_y, label: r.name }))} />
        </div>
      )}
    </div>
  );
}

export default function TegCheck({ vehicle }) {
  const [text, setText] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [flat, setFlat] = useState(null);          // null = 자동 감지
  const [selPattern, setSelPattern] = useState(null);
  const [mapIdx, setMapIdx] = useState(0);
  const [px, setPx] = useState(5);                 // 작은 맵 셀 크기(px)
  const [mapSel, setMapSel] = useState({});        // {패턴 index: 맵 index 재지정}

  const run = async (flatOverride) => {
    if (!text.trim()) { toast.error("원문을 입력하세요"); return; }
    const useFlat = flatOverride === undefined ? flat : flatOverride;
    setBusy(true);
    try {
      const r = await postJson(API + "/inspect", { vehicle: vehicle || "", text, flat: useFlat });
      setRes(r);
      if (flatOverride === undefined) { setSelPattern(null); setMapIdx(0); setMapSel({}); }
    } catch (e) { toast.error(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const onFlatChange = (f) => { setFlat(f); run(f); };

  const maps = res?.maps || [];
  // 패턴 i 의 맵: 기본은 순서 짝(pattern i ↔ map i), 확대 뷰에서 재지정 가능
  const mapFor = (i) => maps[mapSel[i] ?? Math.min(i, maps.length - 1)];
  const selMap = selPattern !== null && maps.length ? mapFor(selPattern) : null;
  const selPat = selPattern !== null ? res.patterns[selPattern] : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card title="설비 원문 입력"
        right={<Pill tone={vehicle ? "ok" : "warn"}>{vehicle || "vehicle 미선택"}</Pill>}>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
          설비 화면의 레시피 원문(#wafer-map / &lt;SITES&gt; / #teg-map 포함)을 그대로 붙여넣고
          검사를 누르세요. 정답지는 위 vehicle 의 TEG 위치 조회 데이터(Teg_location)입니다.
        </div>
        <Textarea value={text} onChange={e => setText(e.target.value)} rows={10}
          placeholder={"1 #wafer-map ...\n2 !\n3 --ttt--\n..."}
          style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }} />
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
          <Button variant="primary" disabled={busy} onClick={() => { setFlat(null); run(null); }}>
            {busy ? "검사 중…" : "검사"}
          </Button>
          <span style={{ fontSize: 12, color: "var(--muted)", marginLeft: "auto" }}>맵 크기</span>
          <input type="range" min="2" max="10" step="1" value={px}
            onChange={e => setPx(Number(e.target.value))} style={{ width: 110 }} />
        </div>
      </Card>

      {!res && <EmptyState icon="🔍" title="원문을 넣고 검사를 눌러주세요"
        hint="전체 Pattern 의 WF MAP 과 TEG 좌표 대조 결과가 표시됩니다" />}

      {res && (
        <>
          <Card title={`Wafer Map — Pattern 전체 (${res.patterns.length})`}>
            {!maps.length ? (
              <EmptyState icon="⚠" title="#wafer-map 의 ! ~ ! 블록을 찾지 못했습니다" />
            ) : !res.patterns.length ? (
              <EmptyState icon="⚠" title="<SITES> 의 Pattern 을 찾지 못했습니다" />
            ) : (
              <>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
                  <span style={{ color: MAP_COLORS.measure, fontWeight: 700 }}>■</span> 측정 샷(t) ·
                  <span style={{ color: SITE_HL, fontWeight: 700 }}> □</span> Pattern site.
                  카드를 클릭하면 확대해서 pt 라벨과 상태표를 볼 수 있습니다.
                </div>
                <PatternGrid res={res} px={px} selected={selPattern} onSelect={setSelPattern}
                  mapFor={mapFor} />
              </>
            )}
          </Card>

          {selPat && selMap && (
            <Card title={`Pattern 확대 — ${selPat.name}`}
              right={
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {maps.length > 1 && (
                    <Select value={mapSel[selPattern] ?? Math.min(selPattern, maps.length - 1)}
                      onChange={e => setMapSel(prev => ({ ...prev, [selPattern]: Number(e.target.value) }))}>
                      {maps.map((m, i) => <option key={i} value={i}>{m.name}</option>)}
                    </Select>
                  )}
                  <Button onClick={() => setSelPattern(null)}>닫기</Button>
                </div>
              }>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
                <WfSvg map={selMap} px={16} showLabels
                  sitesHl={selPat.points.map(pt => ({ x: pt.x, y: pt.y, label: pt.pt }))} />
                <div style={{ minWidth: 240, flex: "1 1 240px" }}>
                  <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
                    {selMap.name} · {selMap.w}×{selMap.h} · pt {selPat.points.length}개
                  </div>
                  <DataTable maxHeight={420}
                    columns={[
                      { key: "pt", label: "pt", align: "right" },
                      { key: "x", label: "x", align: "right" },
                      { key: "y", label: "y", align: "right" },
                      { key: "st", label: "상태", render: r => {
                          const s = siteStatus(selMap, r.x, r.y);
                          return <span style={{ color: s === "측정" ? "var(--ok, #2f9e63)" : "var(--danger, #e05252)",
                                                fontWeight: 600 }}>{s}</span>;
                        } },
                    ]}
                    rows={selPat.points} />
                </div>
              </div>
            </Card>
          )}

          <Card title="TEG 설비값 대조 — 정답지(TEG 위치 조회)">
            {!res.teg.rows.length ? (
              <EmptyState icon="⚠" title="#teg-map 에서 module 행을 찾지 못했습니다" />
            ) : (
              <TegSection res={res} onFlatChange={onFlatChange}
                mapIdx={mapIdx} setMapIdx={setMapIdx} />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
