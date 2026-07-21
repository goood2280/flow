/* TegCheck.jsx — TEG Mapfile 체크 (TEG 위치 조회 페이지의 "TEG Mapfile 체크" 탭).
   설비에서 복사한 레시피 원문을 백엔드(/api/teg-map/inspect)로 보내
   ① 전체 Pattern 의 site 좌표를 작은 WF MAP 카드로 한번에 표시 (클릭 → 확대),
   ② #teg-map 의 module 좌표를 flat 변환(Vertical(R) = 반시계 90° 회전 원복) 후
      정답지(TEG 위치 조회의 Teg_location raw ebeam 값)와 대조해
      🟢 일치 / 🟡 확인필요(ΔX·ΔY 각 3 이내) / 🔴 불일치 / ⚪ 미등록 로 표시.
   오프셋(flat 기본·TEG별·회전 offset)은 ⚙️ 설정의 "TEG Mapfile 체크" 섹션에서 편집.
*/
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { postJson } from "../lib/api";
import { toast } from "../components/Toast";
import { Button, Card, DataTable, EmptyState, Pill, Select, Textarea } from "../components/UXKit";

const API = "/api/teg-map";

const MAP_COLORS = { measure: "#9ca3af", other: "#cbd5e1" };
const SITE_HL = "#dc2626";
const TEG_HL = "#2563eb";
const MAX_CELLS = 400000;    // 렌더 상한 (w*h)
const GRID_LINE_MAX = 6000;  // 이 이상이면 격자선 생략

const STATUS_ICON = { match: "🟢", warning: "🟡", mismatch: "🔴", extended: "🟣", missing: "⚪", noref: "—" };
const FLAT_LABELS = { h: "Horizontal", v_R: "Vertical(R)" };

// 조회되어야 할 TEG 목록 신호등 — 색상 차순 정렬 기준(작을수록 위): 빨강 → 미등록 → 노랑 → 초록.
const LIGHT_COLORS = { red: "#dc2626", gray: "#9ca3af", yellow: "#d99a1a", green: "#2f9e63" };
const LIGHT_RANK = { red: 0, gray: 1, yellow: 2, green: 3 };

function TrafficLight({ color }) {
  const c = LIGHT_COLORS[color] || LIGHT_COLORS.gray;
  return <span style={{ width: 12, height: 12, borderRadius: "50%", background: c,
    display: "inline-block", flexShrink: 0, boxShadow: `0 0 0 2px ${c}33` }} />;
}

function classify(ch) {
  if (ch === "-" || ch === "." || ch === " " || ch === undefined) return "empty";
  if (ch === "t" || ch === "T") return "measure";
  return "other";
}

function cellAt(map, x, y) {
  if (!Number.isInteger(x) || !Number.isInteger(y)) return null;
  if (y < 1 || y > map.h || x < 1 || x > map.w) return null;
  // [TEST_POINT] = top-left 원점 (1,1)=좌상단; #wafer-map = bottom-left (Chip_Radius 좌표계)
  if (map.origin === "top-left") return map.rows[y - 1][x - 1];
  return map.rows[map.h - y][x - 1];   // y=1 = 맵 하단 (Chip_Radius 좌표계: 좌하단 원점)
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

/* ── 웨이퍼 맵 SVG — #wafer-map: 좌하단 = (1,1) (Chip_Radius 좌표계),
   [TEST_POINT]: 좌상단 = (1,1) (origin="top-left").
   행을 같은 색 연속 구간으로 묶어 rect 수를 줄인다 (PoC svg_map 포팅). ── */
function WfSvg({ map, sitesHl = [], tegHl = [], px = 6, showLabels = false }) {
  const { rows, w, h } = map;
  const topLeft = map.origin === "top-left";
  // site 좌표(p.y) → SVG y: top-left 이면 p.y-1 (위가 0), bottom-left 이면 h-p.y
  const hlY = (py) => topLeft ? py - 1 : h - py;
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
      {[[sitesHl, SITE_HL, true], [tegHl, TEG_HL, false]].map(([hl, color, filled], gi) =>
        hl.filter(p => p.x >= 1 && p.x <= w && p.y >= 1 && p.y <= h).map((p, i) => (
          <g key={`${gi}-${i}`}>
            <rect x={p.x - 1} y={hlY(p.y)} width={1} height={1}
              fill={filled ? color : "none"} stroke={color} strokeWidth="0.09" />
            {hlText && (
              <>
                <text x={p.x - 0.5} y={hlY(p.y) + 0.44} fontSize="0.36" textAnchor="middle"
                  fontWeight="bold" fill={color}>{p.label}</text>
                <text x={p.x - 0.5} y={hlY(p.y) + 0.82} fontSize="0.2" textAnchor="middle"
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
  // SVG 는 y 가 아래로 증가 — ebeam +y(위)를 뒤집어 shot 센터가 정확히 (0,0)이 되게 한다.
  const toY = (mm) => oy + (H / 2 - mm) * s;
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
          {/* 칩 격자 — c.x/c.y = 칩 좌하단(mm), y 축 반전이라 top = toY(c.y) - 높이 */}
          {cells.map((c, i) => (
            <rect key={i} x={toX(c.x)} y={toY(c.y) - c.h * s} width={c.w * s} height={c.h * s}
              fill="rgba(47,158,99,0.08)" stroke="#2f9e63" strokeWidth={0.8 / zoom} opacity="0.85" />
          ))}
          {/* TEG — 계산 좌표(mm) 기준. status 별 색: 🔴 불일치=빨강 / 🟡 확인필요=주황 /
              ⚪ 미등록=파랑. 칩(die) 겹침이면 빨간 테두리·⚠ 로 강조 (status 무관). */}
          {rows.map((t, i) => {
            const overlap = t.chip_overlap;
            const color = t.status === "mismatch" ? "#dc2626"
              : t.status === "warning" ? "#d99a1a"
              : t.status === "extended" ? "#7c3aed" : "#2563eb";
            const glyph = t.status === "warning" ? "△ "
              : t.status === "mismatch" ? "✕ "
              : t.status === "extended" ? "⊕ " : t.status === "missing" ? "○ " : "";
            const x = toX(t.mm_x), yBottom = toY(t.mm_y);
            const wpx = Math.max(1.5 / zoom, t.teg_w * s), hpx = Math.max(1.5 / zoom, t.teg_h * s);
            return (
              <g key={i}>
                <rect x={x} y={yBottom - hpx} width={wpx} height={hpx}
                  fill={color} opacity={overlap ? 0.9 : 0.7}
                  stroke={overlap ? "#dc2626" : "none"} strokeWidth={overlap ? 1.4 / zoom : 0} />
                <circle cx={x} cy={yBottom} r={2.4 / zoom} fill={color} stroke="var(--panel)" strokeWidth={0.8 / zoom} />
                <text x={x + wpx + 4 / zoom} y={yBottom - hpx / 2} fontSize={11 / zoom}
                  fill={overlap ? "#dc2626" : "var(--text)"}
                  fontWeight={overlap ? 700 : 400} dominantBaseline="middle">
                  {glyph}{t.name}{overlap ? " ⚠" : ""}
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

/* ── module 이름 후보 chip — 엔지니어마다 이름 위치(module~( / 꼬리표 1·2번째)가
   달라 자동 인식이 틀릴 수 있다. 인식된 토큰은 음영, 다른 토큰 클릭 → 행별 재지정. ── */
function NameCell({ r, ov, onPick }) {
  const active = ov !== undefined ? ov : r.name;
  const cands = (r.candidates && r.candidates.length ? r.candidates : [r.name]).filter(Boolean);
  const pending = ov !== undefined && ov !== r.name;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
      {cands.map(v => {
        const isA = v === active;
        return (
          <span key={v} onClick={() => onPick(r, v)}
            title={isA ? "module 이름으로 인식됨 (다른 토큰 클릭 → 재지정)"
                       : "클릭 → 이 토큰을 module 이름으로 사용"}
            style={{
              fontFamily: "monospace", fontSize: 12, padding: "1px 6px", borderRadius: 4,
              cursor: "pointer", userSelect: "none", whiteSpace: "nowrap",
              background: isA ? "rgba(90,140,255,0.20)" : "transparent",
              border: `1px solid ${isA ? "var(--accent, #5a8cff)" : "var(--line)"}`,
              fontWeight: isA ? 700 : 400,
              color: isA ? "var(--text)" : "var(--muted)",
            }}>{v}</span>
        );
      })}
      {pending && (
        <span style={{ fontSize: 11, color: "#e0a452", fontWeight: 700 }}>재적용 필요</span>
      )}
    </div>
  );
}

/* ── MAIN 그룹 → 위치조회 역반영 — MAIN 은 die 급 큰 사각형 블록, Mapfile 의
   내부 TEG 행들을 ebeam 절대좌표로 원복해 위치 조회에 overlay 로 저장한다.
   같은 그룹이 이미 반영돼 있으면 서버가 exists 를 돌려주고, 사용자 확인 후
   overwrite=true 로 재요청 (다시 검사 시 "다시 반영할지" 묻는 계약). ── */
function MainGroupSection({ vehicle, groups }) {
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState({});   // {group: applied_at} — 이번 세션 반영분

  const apply = async (targets) => {
    if (!vehicle) { toast.error("vehicle 을 먼저 선택하세요"); return; }
    const payload = targets.map(g => ({ group: g.group, tegs: g.tegs }));
    setBusy(true);
    try {
      let r = await postJson(API + "/main-overlay/apply",
        { vehicle, groups: payload, overwrite: false });
      if (!r.ok && (r.exists || []).length) {
        const msg = "이미 위치조회에 반영된 MAIN 그룹이 있습니다:\n"
          + r.exists.map(e => `  · ${e.group} (${e.applied_at || "반영 시각 미상"})`).join("\n")
          + "\n\n이번 Mapfile 기준으로 다시 반영할까요?";
        if (!window.confirm(msg)) { setBusy(false); return; }
        r = await postJson(API + "/main-overlay/apply",
          { vehicle, groups: payload, overwrite: true });
      }
      if (r.ok) {
        toast.success(`위치조회 반영 완료 — ${r.saved.join(", ")}`);
        setApplied(prev => {
          const next = { ...prev };
          r.saved.forEach(g => { next[g] = r.applied_at; });
          return next;
        });
      } else {
        toast.error(r.error || "반영 실패");
      }
    } catch (e) { toast.error(String(e.message || e)); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        MAIN 은 die 급 블록으로, 내부 TEG 정보가 Mapfile 에 같은 그룹명으로 나열됩니다.
        아래 그룹을 반영하면 TEG 위치 조회에서 해당 MAIN 을 고를 때 내부 TEG 위치·모양이 함께 표시됩니다.
        <span style={{ color: "#e0a452" }}> 설비 Mapfile 세팅에서 가져온 값이라 이상이 있을 수
        있습니다 — 위치조회에 반영 시각이 참고문으로 표시됩니다.</span>
      </div>
      {groups.map(g => {
        const at = applied[g.group] || g.applied_at;
        return (
          <div key={g.group} style={{ display: "flex", gap: 10, alignItems: "center",
                                      flexWrap: "wrap", border: "1px solid var(--line)",
                                      borderRadius: 6, padding: "6px 10px", fontSize: 13 }}>
            <span style={{ fontWeight: 700, fontFamily: "monospace" }}>{g.group}</span>
            <Pill tone="neutral" size="sm">내부 TEG {g.tegs.length}개</Pill>
            {g.chip_overlap > 0 && (
              <Pill tone="danger" size="sm"
                title={"칩(die) 위에 겹친 내부 TEG — TEG 는 칩 사이(스크라이브)에 있어야 정상:\n"
                  + g.tegs.filter(t => t.chip_overlap).map(t => t.teg).join(", ")}>
                ⚠ 칩 겹침 {g.chip_overlap}
              </Pill>
            )}
            {at ? (
              <Pill tone="warn" size="sm" title="위치조회에 이미 반영된 그룹 — 다시 반영하면 덮어씁니다">
                기존 반영 {String(at).slice(0, 16).replace("T", " ")}
              </Pill>
            ) : (
              <Pill tone="neutral" size="sm">미반영</Pill>
            )}
            <span style={{ fontSize: 11, color: "var(--muted)", flex: 1, overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {g.tegs.slice(0, 4).map(t => t.teg).join(", ")}{g.tegs.length > 4 ? " …" : ""}
            </span>
            <Button disabled={busy} onClick={() => apply([g])}>위치조회에 반영</Button>
          </div>
        );
      })}
      {groups.length > 1 && (
        <div>
          <Button variant="primary" disabled={busy} onClick={() => apply(groups)}>
            전체 {groups.length}개 그룹 반영
          </Button>
        </div>
      )}
    </div>
  );
}

/* ── TEG 대조 섹션 — flat 선택 + 🟢/🔴/⚪ 대조표 + 맵 표시 ── */
function TegSection({ res, onFlatChange, markerH, setMarkerH, markerV, setMarkerV, onMarkersApply,
                      nameOv, onPickName, pendingCount, onReapply, busy }) {
  const teg = res.teg;
  const { summary } = teg;
  const bad = teg.rows.filter(r => r.status === "mismatch");
  const warn = teg.rows.filter(r => r.status === "warning");
  const extended = teg.rows.filter(r => r.status === "extended");
  // shot 배치도에는 불일치·확인필요(+칩 겹침)만 표시 — 일치·확장체크·미등록은 숨긴다.
  const shotRows = teg.rows.filter(r => r.status === "mismatch" || r.status === "warning" || r.chip_overlap);
  const [showAll, setShowAll] = useState(false);
  const [showMain, setShowMain] = useState(false);
  const [showMatchedTargets, setShowMatchedTargets] = useState(false);
  const [checklistColorSort, setChecklistColorSort] = useState(true);
  const targets = teg.targets || { items: [], matched: 0, missing: 0, total: 0, source: "default" };
  const missingTargets = targets.items.filter(t => !t.matched);
  const matchedTargets = targets.items.filter(t => t.matched);

  // ── "조회되어야 할 TEG 목록" 체크리스트 — 체크 대상 TEG 각각의 신호등.
  //    각 대상 teg 로 매칭된(coordinate check) 행의 상태로 신호등 결정:
  //    위치확인(match/extended)=초록, 확인필요(warning)=노랑, 불일치(mismatch)=빨강,
  //    대상 목록엔 있으나 mapfile 에 없음=회색(미등록). 같은 teg 여러 행이면 최악(빨강 우선).
  const CROW_RANK = { mismatch: 0, warning: 1, extended: 2, match: 3 };
  const rowByRefTeg = useMemo(() => {
    const m = {};
    (teg.rows || []).forEach(r => {
      if (!r.ref_teg) return;
      const cur = m[r.ref_teg];
      if (!cur || (CROW_RANK[r.status] ?? 9) < (CROW_RANK[cur.status] ?? 9)) m[r.ref_teg] = r;
    });
    return m;
  }, [teg.rows]);
  const checklist = useMemo(() => {
    const items = (targets.items || []).map(t => {
      const row = rowByRefTeg[t.teg];
      let light, label;
      if (row) {
        if (row.status === "mismatch") { light = "red"; label = "불일치"; }
        else if (row.status === "warning") { light = "yellow"; label = "확인필요"; }
        else if (row.status === "extended") { light = "green"; label = "확장체크"; }
        else { light = "green"; label = "위치 확인"; }
      } else if (t.matched) {
        light = "green"; label = "확인";   // 대상 목록과 일치(대개 MAIN 등 검사 행 밖)
      } else {
        light = "gray"; label = "mapfile 미등록";
      }
      return { teg: t.teg, top_cell: t.top_cell, matched_by: t.matched_by,
               light, label, row };
    });
    if (checklistColorSort) {
      items.sort((a, b) => (LIGHT_RANK[a.light] - LIGHT_RANK[b.light])
        || String(a.teg).localeCompare(String(b.teg)));
    }
    return items;
  }, [targets.items, rowByRefTeg, checklistColorSort]);
  const lightCounts = checklist.reduce((acc, it) => {
    acc[it.light] = (acc[it.light] || 0) + 1; return acc;
  }, {});
  // 수천 행 대비: 이름 검색 필터 + 점진 렌더 (한 번에 전부 그리면 메인스레드 블로킹)
  const [rowFilter, setRowFilter] = useState("");
  const [rowLimit, setRowLimit] = useState(300);
  useEffect(() => { setRowLimit(300); }, [teg]);
  const flatUsed = res.flat.used;

  const q = rowFilter.trim().toLowerCase();
  const filteredRows = useMemo(() => (q
    ? teg.rows.filter(r =>
        String(r.name || "").toLowerCase().includes(q)
        || (r.candidates || []).some(v => String(v).toLowerCase().includes(q)))
    : teg.rows), [teg.rows, q]);
  const visRows = useMemo(() => filteredRows.slice(0, rowLimit), [filteredRows, rowLimit]);

  const fullCols = [
    { key: "st", label: "", width: 30, render: r => STATUS_ICON[r.status] || "" },
    { key: "name", label: "module_name",
      render: r => <NameCell r={r} ov={nameOv[r.idx]} onPick={onPickName} /> },
    { key: "flat_info", label: "flat",
      render: r => <span title={r.flat_marker ? `마커: ${r.flat_marker}` : '마커 없음'}>
        {r.flat_used === 'h' ? 'H' : r.flat_used === 'v_R' ? 'V(R)' : r.flat_used}
      </span> },
    { key: "orig", label: "Mapfile (x,y)", render: r => `(${r.x},${r.y})` },
    { key: "calc_x", label: "환산X", align: "right" },
    { key: "calc_y", label: "환산Y", align: "right" },
    { key: "matched", label: "매칭 TEG", render: r => {
        if (!r.ref_teg) return "";
        const tag = r.extended ? "확장" : r.match_source === "top_cell" ? "top_cell" : "";
        const seq = r.ref_seq != null ? ` #${r.ref_seq}/${r.ref_total}` : "";
        return (
          <span title={r.extended
            ? `확장체크: ${r.match_token} → ${r.ref_teg} ('01' 제외 재매칭)`
            : r.match_source === "top_cell"
              ? `top_cell '${r.match_token}' 로 ${r.ref_teg} 에 매칭`
              : `teg 이름으로 ${r.ref_teg} 에 매칭`
            + (r.ref_seq != null ? ` — 동명 ${r.ref_total}개 중 ${r.ref_seq}번째` : "")}>
            {r.ref_teg}{seq}{tag ? ` (${tag})` : ""}
          </span>
        );
      } },
    { key: "ref", label: "DB Ebeam (x,y)", render: r =>
        r.status === "missing" ? "없음" : r.status === "noref" ? "-" : `(${r.ref_x},${r.ref_y})` },
    { key: "dx", label: "ΔX", align: "right", render: r => fmtN(r.dx) },
    { key: "dy", label: "ΔY", align: "right", render: r => fmtN(r.dy) },
    ...(res.shot?.checked ? [{
      key: "chip", label: "칩 겹침", render: r => r.chip_overlap
        ? <span style={{ color: "#dc2626", fontWeight: 700 }}>⚠ 겹침</span> : "",
    }] : []),
    { key: "note", label: "비고", render: r => r.rule_note || "" },
  ];
  const overlapRowStyle = (r) => (r.chip_overlap ? { background: "rgba(224,82,82,0.10)" } : {});
  const badCols = [
    { key: "name", label: "module_name" },
    { key: "flat_info", label: "flat",
      render: r => <span title={r.flat_marker ? `마커: ${r.flat_marker}` : '마커 없음'}>
        {r.flat_used === 'h' ? 'H' : r.flat_used === 'v_R' ? 'V(R)' : r.flat_used}
      </span> },
    { key: "x", label: "Map X", align: "right" },
    { key: "calc_x", label: "환산X", align: "right" },
    { key: "ref_x", label: "DB X", align: "right", render: r => fmtN(r.ref_x) },
    { key: "dx", label: "ΔX", align: "right", render: r => fmtN(r.dx) },
    { key: "y", label: "Map Y", align: "right" },
    { key: "calc_y", label: "환산Y", align: "right" },
    { key: "ref_y", label: "DB Y", align: "right", render: r => fmtN(r.ref_y) },
    { key: "dy", label: "ΔY", align: "right", render: r => fmtN(r.dy) },
  ];
  // 칩 격자(die) 검사 결과 — 확인필요 목록에서 "die 밖=거의 문제없음 / die 내=확인필요".
  // 칩 격자 모드(res.shot.checked)에서만 의미가 있어 그 때만 컬럼을 붙인다.
  const dieCol = {
    key: "die", label: "칩 격자(die)", render: r => r.chip_overlap
      ? <span style={{ color: "#dc2626", fontWeight: 700 }}
          title="TEG 사각형이 die(칩) 영역 안에 들어감 — 위치 확인 필요">die 내 (확인필요)</span>
      : <span style={{ color: "var(--ok, #2f9e63)", fontWeight: 700 }}
          title="TEG 가 die 밖(칩 사이 스크라이브)에 있음 — 거의 문제 없음">die 밖 (문제없음)</span>,
  };
  const warnCols = res.shot?.checked ? [...badCols, dieCol] : badCols;

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
              꼬리표에서 기준 PCHK 마커(H_PCHK/V_PCHK/H_PRBCHK/V_PRBCHK)를 찾지 못했습니다 —
              아래에 설비의 기준 마커를 직접 입력하거나 flat 을 수동 선택하세요.
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
          <Pill tone="neutral" title="Vertical(R) = 설비의 반시계 90° 회전 세팅을 원복: (x, y) → (y, -x)">
            V 회전 원복
          </Pill>
        )}
        {(() => {
          const pb = res.pchk_base;
          const fromDb = pb && pb.source === "db";
          return (
            <span style={{ fontSize: 12, color: fromDb ? "var(--ok, #2f9e63)" : "var(--muted)" }}
              title={fromDb
                ? `기준점(dx, dy) = 정답지 ${pb.ref_name} 의 DB Ebeam 좌표. Mapfile 상대좌표에 이 값을 더해 환산X/Y 를 원복합니다.`
                : "정답지에서 기준 PCHK/PRBCHK 을 찾지 못해 ⚙️ 설정의 기본 오프셋을 사용합니다."}>
              기준점 ({FLAT_LABELS[flatUsed]}): x'={res.offset.dx}, y'={res.offset.dy}
              {fromDb
                ? ` · ${pb.ref_name} DB Ebeam 반영`
                : " · ⚙️ 설정 기본 오프셋"}
            </span>
          );
        })()}
      </div>

      {/* 기준 PCHK 마커 미인식 → 사용자 지정 마커 입력 (쉼표로 여러 개) */}
      {res.flat.needs_input && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                      padding: "8px 10px", borderRadius: 8,
                      background: "rgba(224,164,82,0.10)", fontSize: 13 }}>
          <span style={{ fontWeight: 600 }}>기준 마커 직접 입력</span>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            H(가로):
            <input value={markerH} onChange={e => setMarkerH(e.target.value)}
              placeholder="예: H_TPCHK" spellCheck={false}
              style={{ width: 130, fontFamily: "monospace", fontSize: 12 }} />
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            V(세로):
            <input value={markerV} onChange={e => setMarkerV(e.target.value)}
              placeholder="예: V_TPCHK" spellCheck={false}
              style={{ width: 130, fontFamily: "monospace", fontSize: 12 }} />
          </label>
          <Button variant="primary" disabled={!markerH.trim() && !markerV.trim()}
            onClick={onMarkersApply}>마커로 재검사</Button>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            쉼표로 여러 개 입력 가능 — TEG 별 꼬리표에서 이 마커로 flat 을 개별 판정합니다
          </span>
        </div>
      )}
      {!res.flat.needs_input
        && ((res.flat.custom_markers?.h?.length || 0) + (res.flat.custom_markers?.v_R?.length || 0) > 0) && (
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          사용자 지정 마커 적용 중 — H: {(res.flat.custom_markers.h || []).join(", ") || "-"} ·
          V: {(res.flat.custom_markers.v_R || []).join(", ") || "-"}
        </div>
      )}

      {teg.ref_ok && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Pill tone="ok">🟢 일치 {summary.match}</Pill>
          <Pill tone={summary.warning ? "warn" : "neutral"}
            title="ΔX·ΔY 가 각각 3 이내 — 소수점·세팅 차이 정도의 작은 오차일 수 있어 확인이 필요합니다">
            🟡 확인필요 {summary.warning || 0}
          </Pill>
          <Pill tone={summary.mismatch ? "danger" : "neutral"}>🔴 불일치 {summary.mismatch}</Pill>
          <Pill tone={summary.extended ? "warn" : "neutral"}
            title="정답지에 이름이 없어 '01' 을 제외하고 재매칭해 teg 를 찾은 경우 (위치가 아닌 이름 검증)">
            🟣 확장체크 {summary.extended || 0}
          </Pill>
          <Pill tone="neutral">⚪ 정답지 미등록 {summary.missing}</Pill>
          {res.shot?.checked && (
            <Pill tone={summary.chip_overlap ? "danger" : "ok"}>
              ⚠ 칩 겹침 {summary.chip_overlap}
            </Pill>
          )}
        </div>
      )}

      {/* ── 체크 대상 TEG 설정 여부 — 위치 조회에서 지정한 체크 대상 TEG 가 이 Mapfile 의
          module name 목록에 (teg 또는 top_cell 완전 일치로) 있는지. 미설정 = 대상인데
          Mapfile 에 없음. ── */}
      {targets.total > 0 ? (
        <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 700 }}>체크 대상 TEG 설정 여부</span>
            <Pill tone="ok" size="sm">설정됨 {targets.matched}</Pill>
            <Pill tone={targets.missing ? "danger" : "neutral"} size="sm">미설정 {targets.missing}</Pill>
            <Pill tone="neutral" size="sm">
              {targets.source === "config" ? "지정 대상" : "기본(H_/V_)"} {targets.total}개
            </Pill>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              대상 TEG 는 위치 조회 → TEG 목록 → "Mapfile 체크 대상 TEG" 에서 설정
            </span>
          </div>
          {missingTargets.length > 0 ? (
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: "var(--danger, #e05252)", marginBottom: 4 }}>
                🔴 미설정 {missingTargets.length}건 — Mapfile 의 module name 에 teg/top_cell 완전 일치가 없습니다
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {missingTargets.map(t => (
                  <span key={t.teg}
                    title={t.top_cell?.length ? `top_cell: ${t.top_cell.join(", ")}` : "top_cell 없음"}
                    style={{ fontFamily: "monospace", fontSize: 12, padding: "1px 6px", borderRadius: 4,
                             border: "1px solid var(--danger, #e05252)", color: "var(--danger, #e05252)" }}>
                    {t.teg}
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--ok, #2f9e63)" }}>
              🟢 미설정 없음 — 체크 대상 TEG 가 모두 Mapfile 에 있습니다
            </div>
          )}
          {matchedTargets.length > 0 && (
            <div style={{ marginTop: 6 }}>
              <button onClick={() => setShowMatchedTargets(v => !v)}
                style={{ fontSize: 11, color: "var(--accent, #5a8cff)", background: "none",
                         border: "none", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
                {showMatchedTargets ? "▾" : "▸"} 설정됨 {matchedTargets.length}건
              </button>
              {showMatchedTargets && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
                  {matchedTargets.map(t => (
                    <span key={t.teg}
                      title={`Mapfile module "${t.matched_module}" 와 ${t.matched_by === "top_cell" ? "top_cell" : "teg"} 완전 일치`}
                      style={{ fontFamily: "monospace", fontSize: 12, padding: "1px 6px", borderRadius: 4,
                               border: "1px solid var(--ok, #2f9e63)", color: "var(--ok, #2f9e63)" }}>
                      {t.teg}{t.matched_by === "top_cell" ? ` ⟵ ${t.matched_module}` : ""}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ) : res.vehicle ? (
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          체크 대상 TEG 가 없습니다 — 위치 조회 → TEG 목록 → "Mapfile 체크 대상 TEG" 에서 지정하거나,
          teg 이름이 H_/V_ 로 시작하는 TEG 가 있으면 자동으로 대상이 됩니다.
        </div>
      ) : null}

      {/* 행별 이름 재지정 후 일괄 재검사 — 클릭마다 서버를 부르지 않고 모아서 1회 */}
      {pendingCount > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                      padding: "6px 10px", borderRadius: 8,
                      background: "rgba(224,164,82,0.10)", fontSize: 13 }}>
          <Button variant="primary" disabled={busy} onClick={onReapply}>
            이름 재지정 재검사 ({pendingCount}건)
          </Button>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            선택한 이름으로 정답지 대조를 다시 실행합니다
          </span>
        </div>
      )}

      {teg.ref_ok && (bad.length ? (
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--danger, #e05252)", marginBottom: 6 }}>
            🔴 불일치 {bad.length}건 — 정답지와 ΔX·ΔY 가 3 을 초과합니다
          </div>
          <DataTable columns={badCols} rows={bad} maxHeight={260} />
        </div>
      ) : (
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ok, #2f9e63)" }}>🟢 불일치 없음</div>
      ))}

      {teg.ref_ok && warn.length > 0 && (
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#c98a1a", marginBottom: 6 }}>
            🟡 확인필요 {warn.length}건 — ΔX·ΔY 가 각각 3 이내 (소수점·세팅 차이일 수 있음)
            {res.shot?.checked && (() => {
              const outside = warn.filter(r => !r.chip_overlap).length;
              return <span style={{ fontWeight: 400, color: "var(--muted)" }}>
                {" "}· 칩 격자 검사: die 밖 {outside} / die 내 {warn.length - outside}
              </span>;
            })()}
          </div>
          <DataTable columns={warnCols} rows={warn} maxHeight={260} rowStyle={overlapRowStyle} />
        </div>
      )}

      {teg.ref_ok && extended.length > 0 && (
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#7c3aed", marginBottom: 6 }}>
            🟣 확장체크 {extended.length}건 — 정답지 미등록이라 '01' 을 제외하고 재매칭해 teg 를 확인했습니다
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {extended.map((r, i) => (
              <span key={i}
                title={`${r.match_token} → ${r.ref_teg}${r.match_source === "top_cell" ? " (top_cell)" : ""}`}
                style={{ fontFamily: "monospace", fontSize: 12, padding: "1px 6px", borderRadius: 4,
                         border: "1px solid #7c3aed", color: "#7c3aed" }}>
                {r.match_token} → {r.ref_teg}{r.match_source === "top_cell" ? " (top_cell)" : ""}
              </span>
            ))}
          </div>
        </div>
      )}

      <div>
        <button onClick={() => setShowAll(v => !v)}
          style={{ fontSize: 12, color: "var(--accent, #5a8cff)", background: "none",
                   border: "none", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
          {showAll ? "▾" : "▸"} 전체 {teg.rows.length}건
        </button>
        {(showAll || (teg.ref_ok && !bad.length)) && (
          <>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                          margin: "6px 0" }}>
              <input value={rowFilter} onChange={e => setRowFilter(e.target.value)}
                placeholder="이름/후보 검색" spellCheck={false}
                style={{ width: 180, fontSize: 12, fontFamily: "monospace" }} />
              <span style={{ fontSize: 11, color: "var(--muted)" }}>
                {q ? `${filteredRows.length}건 일치 · ` : ""}
                {Math.min(rowLimit, filteredRows.length)}/{filteredRows.length} 표시
                — module_name 의 음영 토큰이 인식된 이름, 다른 토큰 클릭 → 재지정
              </span>
            </div>
            <DataTable columns={fullCols} rows={visRows} maxHeight={320}
              rowStyle={overlapRowStyle} />
            {filteredRows.length > rowLimit && (
              <button onClick={() => setRowLimit(l => l + 500)}
                style={{ fontSize: 12, color: "var(--accent, #5a8cff)", background: "none",
                         border: "none", cursor: "pointer", padding: "4px 0",
                         textDecoration: "underline" }}>
                더 보기 +500 ({rowLimit}/{filteredRows.length})
              </button>
            )}
          </>
        )}
      </div>

      {/* MAIN 으로 인식돼 제외된 행 — 자동 인식이 엉뚱한 토큰(MAIN 포함)을 집은
          경우 여기서 이름을 재지정하면 검사 대상으로 돌아온다 */}
      {teg.excluded_main > 0 && (
        <div>
          <button onClick={() => setShowMain(v => !v)}
            style={{ fontSize: 12, color: "var(--accent, #5a8cff)", background: "none",
                     border: "none", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
            {showMain ? "▾" : "▸"} MAIN 제외 {teg.excluded_main}건 — 이름이 잘못 인식된 행은 여기서 재지정
          </button>
          {showMain && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6,
                          maxHeight: 260, overflow: "auto" }}>
              {(teg.main_rows || []).slice(0, 300).map(r => (
                <div key={r.idx} style={{ display: "flex", gap: 10, alignItems: "center",
                                          fontSize: 12 }}>
                  <span style={{ color: "var(--muted)", fontFamily: "monospace", minWidth: 90 }}>
                    ({r.x}, {r.y})
                  </span>
                  <NameCell r={r} ov={nameOv[r.idx]} onPick={onPickName} />
                </div>
              ))}
              {(teg.main_rows || []).length > 300 && (
                <div style={{ fontSize: 11, color: "var(--muted)" }}>
                  + {teg.main_rows.length - 300}건 더 (상위 300건만 표시)
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* shot 확대 — 칩 격자 위 TEG 배치 (계산 좌표 기준) */}
      {res.shot?.available && (
        <div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
            shot 확대 — {fmtN(res.shot.shot_w_mm)}×{fmtN(res.shot.shot_h_mm)} mm, 계산 좌표(EbeamX/Y × 배율) 기준,
            shot 센터 = ebeam (0,0). 🟢 일치 · 🟣 확장체크 · ⚪ 미등록 TEG 는 숨기고
            <span style={{ color: "#dc2626", fontWeight: 700 }}> ✕ 불일치</span> ·
            <span style={{ color: "#d99a1a", fontWeight: 700 }}> △ 확인필요</span> 만 표시합니다.
            {res.shot.checked ? (
              <> TEG 는 칩 사이(스크라이브)에 있어야 정상 —
                <span style={{ color: "#dc2626", fontWeight: 700 }}> die 안에 겹치면 빨간 테두리·⚠</span>.
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
          {!shotRows.length && (
            <div style={{ fontSize: 12, color: "var(--ok, #2f9e63)", marginBottom: 6 }}>
              🟢 모든 TEG 일치 — 배치도에 표시할 불일치 TEG 가 없습니다.
            </div>
          )}
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
            <ShotView shot={res.shot} rows={shotRows} />
            {/* 조회되어야 할 TEG 목록 — 신호등 체크리스트 (Shot 확대 우측) */}
            <div style={{ minWidth: 240, flex: "0 1 300px", display: "flex",
                          flexDirection: "column", gap: 8 }}>
              <TargetChecklist checklist={checklist} counts={lightCounts}
                colorSort={checklistColorSort} onToggleSort={() => setChecklistColorSort(v => !v)}
                total={targets.total} source={targets.source} shotChecked={res.shot?.checked} />
              {/* 표시 TEG 가 5개 미만이면 각 TEG 의 ebeam 좌표도 함께 표시 */}
              {shotRows.length > 0 && shotRows.length < 5 && (
                <div style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ fontWeight: 700 }}>TEG ebeam 좌표 (표시 중)</div>
                  {shotRows.map((r, i) => (
                    <div key={i} style={{ border: "1px solid var(--line)", borderRadius: 6,
                                          padding: "6px 8px" }}>
                      <div style={{ fontWeight: 600 }}>
                        {STATUS_ICON[r.status] || ""} {r.name}{r.chip_overlap ? " ⚠" : ""}
                      </div>
                      <div style={{ color: "var(--muted)" }}>
                        ebeam_x/y: {r.ref_x !== null && r.ref_x !== undefined
                          ? `(${r.ref_x}, ${r.ref_y})` : "정답지 미등록"}
                      </div>
                      <div style={{ color: "var(--muted)" }}>
                        계산값: ({r.calc_x}, {r.calc_y})
                      </div>
                      {res.shot?.checked && (
                        <div style={{ color: r.chip_overlap ? "#dc2626" : "var(--ok, #2f9e63)",
                                      fontWeight: 600 }}>
                          칩 격자: {r.chip_overlap ? "die 내 (확인필요)" : "die 밖 (문제없음)"}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 조회되어야 할 TEG 목록 — 체크 대상 TEG 각각의 신호등 체크리스트.
   초록=위치확인(match/extended) · 노랑=확인필요 · 빨강=불일치 · 회색=mapfile 미등록.
   색상순(빨강→미등록→노랑→초록) 정렬 토글 지원. ── */
function TargetChecklist({ checklist, counts, colorSort, onToggleSort, total, source, shotChecked }) {
  const cell = { display: "flex", alignItems: "center", gap: 8, padding: "5px 8px",
                 borderBottom: "1px solid var(--line)", fontSize: 12 };
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>조회되어야 할 TEG 목록</span>
        {total > 0 && (
          <button onClick={onToggleSort}
            title="정렬 기준 전환 — 색상순(빨강→노랑→초록) ↔ 이름순"
            style={{ fontSize: 11, color: "var(--accent, #5a8cff)", background: "none",
                     border: "1px solid var(--line)", borderRadius: 4, cursor: "pointer",
                     padding: "1px 6px" }}>
            {colorSort ? "색상순 ↓" : "이름순"}
          </button>
        )}
      </div>
      {total === 0 ? (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>
          조회 대상 TEG 가 없습니다 — 위치 조회 → TEG 목록 → "Mapfile 체크 대상 TEG" 에서 지정하세요.
        </div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 10, fontSize: 11, color: "var(--muted)",
                        marginBottom: 6, flexWrap: "wrap" }}>
            <span><TrafficLight color="green" /> 위치확인 {counts.green || 0}</span>
            <span><TrafficLight color="yellow" /> 확인필요 {counts.yellow || 0}</span>
            <span><TrafficLight color="red" /> 불일치 {counts.red || 0}</span>
            <span><TrafficLight color="gray" /> 미등록 {counts.gray || 0}</span>
          </div>
          <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden",
                        maxHeight: 340, overflowY: "auto" }}>
            {checklist.map(it => (
              <div key={it.teg} style={cell}>
                <TrafficLight color={it.light} />
                <span style={{ fontWeight: 600, fontFamily: "monospace",
                               overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  title={it.matched_by === "top_cell" && it.row
                    ? `top_cell '${it.row.match_token}' 로 매칭`
                    : it.top_cell?.length ? `top_cell: ${it.top_cell.join(", ")}` : ""}>
                  {it.teg}
                </span>
                <span style={{ marginLeft: "auto", fontSize: 11,
                               color: LIGHT_COLORS[it.light], fontWeight: 700 }}>
                  {it.label}
                  {it.row && (it.row.status === "mismatch" || it.row.status === "warning")
                    ? ` (Δ${fmtN(it.row.dx)}, ${fmtN(it.row.dy)})` : ""}
                  {shotChecked && it.row?.chip_overlap ? " ⚠" : ""}
                </span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
            {source === "config" ? "지정 대상" : "기본(H_/V_)"} {total}개 · 초록=위치확인 · 노랑=확인필요 · 빨강=불일치 · 회색=mapfile 미등록
          </div>
        </>
      )}
    </div>
  );
}

export default function TegCheck({ vehicle }) {
  // 원문은 비제어(uncontrolled) — 수만 줄 붙여넣기 시 키 입력/paste 마다
  // 페이지 전체가 리렌더되던 버벅임 제거. 값은 ref 로만 추적, 검사 시점에 읽는다.
  const textRef = useRef("");
  const lastTextRef = useRef("");                  // 마지막 검사 원문 (이름 재지정 무효화 판단)
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [flat, setFlat] = useState(null);          // null = 자동 감지
  const [selPattern, setSelPattern] = useState(null);
  const [px, setPx] = useState(21);                // 작은 맵 셀 크기(px) — 최대(30)의 70%
  const [mapSel, setMapSel] = useState({});        // {패턴 index: 맵 index 재지정}
  // 기준 PCHK 마커가 내장 표기로 안 잡힐 때 사용자가 입력하는 flat 마커 (쉼표 구분)
  const [markerH, setMarkerH] = useState("");
  const [markerV, setMarkerV] = useState("");
  // 행별 module 이름 재지정 {idx: 이름} — 원문이 바뀌면 idx 가 어긋나므로 초기화
  const [nameOv, setNameOv] = useState({});

  const parseMarkers = (s) => String(s || "").split(",").map(t => t.trim()).filter(Boolean);

  const run = async (flatOverride) => {
    const text = textRef.current || "";
    if (!text.trim()) { toast.error("원문을 입력하세요"); return; }
    const useFlat = flatOverride === undefined ? flat : flatOverride;
    const h = parseMarkers(markerH), v = parseMarkers(markerV);
    const markers = (h.length || v.length) ? { h, v_R: v } : null;
    const textChanged = text !== lastTextRef.current;
    const ov = textChanged ? {} : nameOv;
    setBusy(true);
    try {
      const r = await postJson(API + "/inspect",
        { vehicle: vehicle || "", text, flat: useFlat, markers,
          name_overrides: Object.keys(ov).length ? ov : null });
      setRes(r);
      lastTextRef.current = text;
      if (textChanged) setNameOv({});
      if (flatOverride === undefined) { setSelPattern(null); setMapSel({}); }
    } catch (e) { toast.error(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const onFlatChange = (f) => { setFlat(f); run(f); };
  // 마커 재검사 — flat 강제 없이(null) 마커 기반 TEG 별 자동 판정으로 다시 검사
  const onMarkersApply = () => { setFlat(null); run(null); };

  // 이름 chip 클릭 — 자동 인식값을 다시 고르면 재지정 해제, 다른 토큰이면 재지정
  const onPickName = (r, value) => {
    setNameOv(prev => {
      const next = { ...prev };
      if (value === r.auto_name) delete next[r.idx];
      else next[r.idx] = value;
      return next;
    });
  };
  // 아직 서버에 반영 안 된 재지정 수 — 행의 현재 이름과 다른 선택만 집계
  const pendingCount = useMemo(() => {
    if (!res) return 0;
    const nameByIdx = {};
    (res.teg.rows || []).forEach(r => { nameByIdx[r.idx] = r.name; });
    (res.teg.main_rows || []).forEach(r => { nameByIdx[r.idx] = r.name; });
    return Object.entries(nameOv)
      .filter(([i, v]) => nameByIdx[i] !== undefined && nameByIdx[i] !== v).length;
  }, [res, nameOv]);
  const onReapply = () => run(flat);

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
        <Textarea defaultValue="" onChange={e => { textRef.current = e.target.value; }} rows={10}
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
                  <span style={{ color: SITE_HL, fontWeight: 700 }}> ■</span> Pattern site.
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
              <TegSection res={res} onFlatChange={onFlatChange}
                markerH={markerH} setMarkerH={setMarkerH}
                markerV={markerV} setMarkerV={setMarkerV}
                onMarkersApply={onMarkersApply}
                nameOv={nameOv} onPickName={onPickName}
                pendingCount={pendingCount} onReapply={onReapply} busy={busy} />
            )}
          </Card>

          {(res.teg.main_groups || []).length > 0 && (
            <Card title={`MAIN 그룹 → 위치조회 반영 (${res.teg.main_groups.length})`}>
              <MainGroupSection vehicle={vehicle} groups={res.teg.main_groups} />
            </Card>
          )}
        </>
      )}
    </div>
  );
}
