/* TegCheck.jsx — TEG Mapfile 체크 (TEG 위치 조회 페이지의 "TEG Mapfile 체크" 탭).
   설비에서 복사한 레시피 원문을 백엔드(/api/teg-map/inspect)로 보내
   ① 전체 Pattern 의 site 좌표를 작은 WF MAP 카드로 한번에 표시 (클릭 → 확대),
   ② #teg-map 의 module 좌표를 flat 변환(Vertical(R) = 반시계 90° 회전 원복) 후
      정답지(TEG 위치 조회의 Teg_location raw ebeam 값)와 대조해 🟢/🔴/⚪ 로 표시.
   오프셋(flat 기본·TEG별·회전 offset)은 ⚙️ 설정의 "TEG Mapfile 체크" 섹션에서 편집.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
const FLAT_LABELS = { h: "Horizontal", v_R: "Vertical(R)" };

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

/* ── shot 확대 뷰 — 칩 격자 + 계산 좌표 기준 TEG 배치.
   TEG 는 칩 사이(스크라이브)에 있어야 정상 — 칩 위에 겹치면 빨간색.
   마우스 휠(확대/축소) + 드래그(패닝) + 핀치 줌 + 리셋 버튼 (위치 조회 ShotZoom 과 동일). ── */
function ShotView({ shot, rows }) {
  const SIZE = 380;
  const W = shot.shot_w_mm, H = shot.shot_h_mm;
  const pad = 0.12;
  const s = SIZE / Math.max(W * (1 + pad * 2), H * (1 + pad * 2));
  const w = W * s, h = H * s;
  const ox = (SIZE - w) / 2, oy = (SIZE - h) / 2;
  const toX = (mm) => ox + (mm + W / 2) * s;
  const toY = (mm) => oy + (mm + H / 2) * s;
  const cells = shot.cells || [];

  // ── zoom / pan 상태 ──
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const svgRef = useRef(null);
  const dragRef = useRef(null);           // { startX, startY, panX0, panY0 } | null
  const pinchRef = useRef(null);          // { dist0, zoom0 } | null
  const pointersRef = useRef(new Map());  // pointerId → { x, y }

  const ZOOM_MIN = 1, ZOOM_MAX = 12, ZOOM_STEP = 1.15;
  const resetView = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }); }, []);

  // 마우스 휠 → 줌 (커서 중심)
  const onWheel = useCallback((e) => {
    e.preventDefault();
    const rect = svgRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    setZoom(prev => {
      const next = e.deltaY < 0
        ? Math.min(ZOOM_MAX, prev * ZOOM_STEP)
        : Math.max(ZOOM_MIN, prev / ZOOM_STEP);
      const ratio = 1 - next / prev;
      setPan(p => ({ x: p.x + (mx - p.x) * ratio, y: p.y + (my - p.y) * ratio }));
      return next;
    });
  }, []);

  const pinchDist = (pts) => {
    const arr = [...pts.values()];
    if (arr.length < 2) return 0;
    return Math.hypot(arr[0].x - arr[1].x, arr[0].y - arr[1].y);
  };
  const onPointerDown = useCallback((e) => {
    svgRef.current?.setPointerCapture(e.pointerId);
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2) {
      pinchRef.current = { dist0: pinchDist(pointersRef.current), zoom0: zoom };
      dragRef.current = null;
    } else if (pointersRef.current.size === 1) {
      dragRef.current = { startX: e.clientX, startY: e.clientY, panX0: pan.x, panY0: pan.y };
    }
  }, [zoom, pan]);
  const onPointerMove = useCallback((e) => {
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2 && pinchRef.current) {
      const d = pinchDist(pointersRef.current);
      if (pinchRef.current.dist0 > 0) {
        setZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pinchRef.current.zoom0 * (d / pinchRef.current.dist0))));
      }
    } else if (dragRef.current && pointersRef.current.size === 1) {
      setPan({ x: dragRef.current.panX0 + (e.clientX - dragRef.current.startX),
               y: dragRef.current.panY0 + (e.clientY - dragRef.current.startY) });
    }
  }, []);
  const onPointerUp = useCallback((e) => {
    pointersRef.current.delete(e.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;
    if (pointersRef.current.size === 0) dragRef.current = null;
  }, []);

  // 휠 이벤트는 passive:false 필요 → ref 방식으로 등록
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const isZoomed = zoom !== 1 || pan.x !== 0 || pan.y !== 0;

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <svg ref={svgRef} width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}
        style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6,
                 cursor: isZoomed ? "grab" : "zoom-in", touchAction: "none", userSelect: "none" }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
          <rect x={ox} y={oy} width={w} height={h} fill="rgba(128,128,128,0.05)"
            stroke="var(--muted)" strokeWidth={1 / zoom} />
          {/* shot 센터 십자 */}
          <line x1={toX(0) - 5 / zoom} y1={toY(0)} x2={toX(0) + 5 / zoom} y2={toY(0)} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          <line x1={toX(0)} y1={toY(0) - 5 / zoom} x2={toX(0)} y2={toY(0) + 5 / zoom} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          {/* 칩 격자 */}
          {cells.map((c, i) => (
            <rect key={i} x={toX(c.x)} y={toY(c.y)} width={c.w * s} height={c.h * s}
              fill="rgba(47,158,99,0.08)" stroke="#2f9e63" strokeWidth={0.8 / zoom} opacity="0.85" />
          ))}
          {/* TEG — 계산 좌표(mm) 기준, 칩 겹침은 빨간색 */}
          {rows.map((t, i) => {
            const color = t.chip_overlap ? "#dc2626" : "#2563eb";
            const x = toX(t.mm_x), yBottom = toY(t.mm_y);
            const wpx = Math.max(1.5 / zoom, t.teg_w * s), hpx = Math.max(1.5 / zoom, t.teg_h * s);
            return (
              <g key={i}>
                <rect x={x} y={yBottom - hpx} width={wpx} height={hpx}
                  fill={color} opacity={t.chip_overlap ? 0.9 : 0.75} />
                <circle cx={x} cy={yBottom} r={2.4 / zoom} fill={color} stroke="var(--panel)" strokeWidth={0.8 / zoom} />
                <text x={x + wpx + 4 / zoom} y={yBottom - hpx / 2} fontSize={11 / zoom}
                  fill={t.chip_overlap ? "#dc2626" : "var(--text)"}
                  fontWeight={t.chip_overlap ? 700 : 400} dominantBaseline="middle">
                  {t.name}{t.chip_overlap ? " ⚠" : ""}
                </text>
              </g>
            );
          })}
          <text x={SIZE / 2} y={oy + h + 16 / zoom} fontSize={11 / zoom} fill="var(--muted)" textAnchor="middle">
            {fmtN(Math.round(W * 100) / 100)} mm
          </text>
        </g>
      </svg>
      {/* 줌 리셋 버튼 — 줌/패닝 상태일 때만 표시 */}
      {isZoomed && (
        <button onClick={resetView} title="보기 초기화"
          style={{ position: "absolute", top: 6, right: 6, width: 28, height: 28,
                   display: "flex", alignItems: "center", justifyContent: "center",
                   background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 4,
                   cursor: "pointer", fontSize: 14, color: "var(--muted)", opacity: 0.85 }}>
          ↺
        </button>
      )}
      {zoom > 1.05 && (
        <span style={{ position: "absolute", bottom: 6, right: 6, fontSize: 11,
                       color: "var(--muted)", background: "var(--panel)", padding: "1px 5px",
                       borderRadius: 3, border: "1px solid var(--line)", opacity: 0.8 }}>
          ×{zoom.toFixed(1)}
        </span>
      )}
    </div>
  );
}

/* ── TEG 대조 섹션 — flat 선택 + 🟢/🔴/⚪ 대조표 + 맵 표시 ── */
function TegSection({ res, onFlatChange }) {
  const teg = res.teg;
  const { summary } = teg;
  const bad = teg.rows.filter(r => r.status === "mismatch");
  const [showAll, setShowAll] = useState(false);
  const flatUsed = res.flat.used;

  const fullCols = [
    { key: "st", label: "", width: 30, render: r => STATUS_ICON[r.status] || "" },
    { key: "name", label: "module_name" },
    { key: "orig", label: "Mapfile 원본 (x,y)", render: r => `(${r.x},${r.y})` },
    { key: "calc_x", label: "Mapfile X", align: "right" },
    { key: "calc_y", label: "Mapfile Y", align: "right" },
    { key: "ref", label: "Ebeam (x,y)", render: r =>
        r.status === "missing" ? "없음" : r.status === "noref" ? "-" : `(${r.ref_x},${r.ref_y})` },
    ...(res.shot?.checked ? [{
      key: "chip", label: "칩 겹침", render: r => r.chip_overlap
        ? <span style={{ color: "#dc2626", fontWeight: 700 }}>⚠ 겹침</span> : "",
    }] : []),
    { key: "note", label: "비고", render: r => r.rule_note || "" },
  ];
  const overlapRowStyle = (r) => (r.chip_overlap ? { background: "rgba(224,82,82,0.10)" } : {});
  const badCols = [
    { key: "name", label: "module_name" },
    { key: "calc_x", label: "Mapfile X", align: "right" },
    { key: "ref_x", label: "EbeamX", align: "right", render: r => fmtN(r.ref_x) },
    { key: "dx", label: "ΔX", align: "right", render: r => fmtN(r.dx) },
    { key: "calc_y", label: "Mapfile Y", align: "right" },
    { key: "ref_y", label: "EbeamY", align: "right", render: r => fmtN(r.ref_y) },
    { key: "dy", label: "ΔY", align: "right", render: r => fmtN(r.dy) },
  ];

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
          ? <Pill tone="ok" title={res.flat.why}>
              Flat 자동 감지: {FLAT_LABELS[res.flat.detected] || res.flat.detected}
            </Pill>
          : <span style={{ fontSize: 12, color: "var(--muted)" }}>
              꼬리표에서 H_PCHK / V_PCHK 를 찾지 못해 수동 선택입니다.
            </span>}
        {["h", "v_R"].map(f => (
          <label key={f} style={{ display: "inline-flex", alignItems: "center", gap: 4,
                                  fontSize: 13, cursor: "pointer" }}>
            <input type="radio" name="teg-check-flat" checked={flatUsed === f}
              onChange={() => onFlatChange(f)} />
            {FLAT_LABELS[f]}
          </label>
        ))}
        {flatUsed === "v_R" && (
          <Pill tone="neutral" title="Vertical(R) = 설비의 반시계 90° 회전 세팅을 원복: (x, y) → (y, -x + offset)">
            {res.v_r_note}
          </Pill>
        )}
        <span style={{ fontSize: 12, color: "var(--muted)" }}
          title="⚙️ 설정 > TEG Mapfile 체크 — 오프셋 에서 편집">
          기본 오프셋 ({FLAT_LABELS[flatUsed]}): x'={res.offset.dx}, y'={res.offset.dy}
        </span>
      </div>

      {teg.ref_ok && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Pill tone="ok">🟢 일치 {summary.match}</Pill>
          <Pill tone={summary.mismatch ? "danger" : "neutral"}>🔴 불일치 {summary.mismatch}</Pill>
          <Pill tone="neutral">⚪ 정답지 미등록 {summary.missing}</Pill>
          {res.shot?.checked && (
            <Pill tone={summary.chip_overlap ? "danger" : "ok"}>
              ⚠ 칩 겹침 {summary.chip_overlap}
            </Pill>
          )}
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
          <DataTable columns={fullCols} rows={teg.rows} maxHeight={320}
            rowStyle={overlapRowStyle} />
        )}
      </div>

      {/* shot 확대 — 칩 격자 위 TEG 배치 (계산 좌표 기준) */}
      {res.shot?.available && (
        <div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
            shot 확대 — {fmtN(res.shot.shot_w_mm)}×{fmtN(res.shot.shot_h_mm)} mm, 계산 좌표(EbeamX/Y × 배율) 기준.
            {res.shot.checked ? (
              <> TEG 는 칩 사이(스크라이브)에 있어야 정상 —
                <span style={{ color: "#dc2626", fontWeight: 700 }}> 칩 위에 겹치면 빨간색</span>.
              </>
            ) : (
              <> ⚙️ 설정에서 이 vehicle 의 shot 표시 방식을 "칩 격자"로 지정하면 칩 겹침을 검사합니다.</>
            )}
          </div>
          {res.shot.checked && summary.chip_overlap > 0 && (
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--danger, #e05252)", marginBottom: 6 }}>
              ⚠ 칩 위에 걸친 TEG {summary.chip_overlap}건 — {
                teg.rows.filter(r => r.chip_overlap).map(r => r.name).join(", ")}
            </div>
          )}
          <ShotView shot={res.shot} rows={teg.rows} />
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
  const [px, setPx] = useState(21);                // 작은 맵 셀 크기(px) — 최대(30)의 70%
  const [mapSel, setMapSel] = useState({});        // {패턴 index: 맵 index 재지정}

  const run = async (flatOverride) => {
    if (!text.trim()) { toast.error("원문을 입력하세요"); return; }
    const useFlat = flatOverride === undefined ? flat : flatOverride;
    setBusy(true);
    try {
      const r = await postJson(API + "/inspect", { vehicle: vehicle || "", text, flat: useFlat });
      setRes(r);
      if (flatOverride === undefined) { setSelPattern(null); setMapSel({}); }
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
      <Card title="Mapfile 원문 입력"
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
          <input type="range" min="2" max="30" step="1" value={px}
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
                <WfSvg map={selMap} px={Math.max(16, px)} showLabels
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

          <Card title="TEG Mapfile 대조 결과">
            {!res.teg.rows.length ? (
              <EmptyState icon="⚠" title="#teg-map 에서 module 행을 찾지 못했습니다" />
            ) : (
              <TegSection res={res} onFlatChange={onFlatChange} />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
