/* TegCheck.jsx — TEG Mapfile 체크 (TEG 위치 조회 페이지의 "TEG Mapfile 체크" 탭).
   설비에서 복사한 레시피 원문을 백엔드(/api/teg-map/inspect)로 보내
   ① 전체 Pattern 의 site 좌표를 작은 WF MAP 카드로 한번에 표시 (클릭 → 확대),
   ② #teg-map 의 module 좌표를 flat 변환(Vertical(R) = 반시계 90° 회전 원복) 후
      정답지(TEG 위치 조회의 Teg_location raw ebeam 값)와 대조해
      🟢 일치 / 🟡 확인필요(ΔX·ΔY 각 3 이내) / 🔴 불일치 / ⚪ 미등록 로 표시.
   오프셋(flat 기본·TEG별·회전 offset)은 ⚙️ 설정의 "TEG Mapfile 체크" 섹션에서 편집.
*/
import { useEffect, useMemo, useRef, useState } from "react";
import { postJson } from "../../lib/api";
import { toast } from "../../components/Toast";
import ZoomPanSvg from "../../components/ZoomPanSvg";
import { Button, Card, DataTable, EmptyState, LinkBtn, Pill, Select, Textarea } from "../../components/UXKit";

const API = "/api/teg-map";

const MAP_COLORS = { measure: "#9ca3af", other: "#cbd5e1" };
const SITE_HL = "#dc2626";
const TEG_HL = "#2563eb";
const MAX_CELLS = 400000;    // 렌더 상한 (w*h)
const GRID_LINE_MAX = 6000;  // 이 이상이면 격자선 생략

const STATUS_ICON = { match: "🟢", warning: "🟡", mismatch: "🔴", extended: "🟣", missing: "⚪", noref: "—" };
const LIGHT_ICON = { red: "🔴", yellow: "🟡", purple: "🟣", green: "🟢", gray: "⚪" };
const FLAT_LABELS = { h: "Horizontal", v_R: "Vertical(R)", v_L: "Vertical(L)" };

// 조회되어야 할 TEG 목록 신호등 — 색상 차순 정렬 기준(작을수록 위): 빨강 → 미등록 → 노랑 → 초록.
const LIGHT_COLORS = { red: "#dc2626", gray: "#9ca3af", yellow: "#d99a1a", green: "#2f9e63",
                       purple: "#7c3aed", dim: "#cbd5e1" };
const LIGHT_RANK = { red: 0, gray: 1, yellow: 2, purple: 3, green: 4, dim: 5 };
const RED_EDGE = "#991b1b";   // shot 확대의 빨간불 테두리 (진한 빨강)
// 결과 화면에서 무엇을 볼지 — 대상 TEG(S/L) / MAIN 내부 TEG / 둘 다 (기본)
const VIEW_ALL = "all", VIEW_TARGET = "target", VIEW_MAIN = "main";
const VIEW_OPTS = [
  { key: VIEW_ALL, label: "모두 표시", title: "대상 TEG 와 MAIN 내부 TEG 를 모두 봅니다 (기본)" },
  { key: VIEW_TARGET, label: "대상 TEG만 (S/L TEG)",
    title: "정답지(Teg_location)의 체크 대상 TEG 만 봅니다 — MAIN 내부 TEG 는 숨깁니다" },
  { key: VIEW_MAIN, label: "MAIN TEG만",
    title: "MAIN die 안의 내부 TEG 만 봅니다 — 대상 TEG 는 숨깁니다" },
];

/* 3분기 토글 — 눌린 것만 강조한다 */
function ViewToggle({ value, onChange }) {
  return (
    <div style={{ display: "inline-flex", border: "1px solid var(--line)", borderRadius: 6,
                  overflow: "hidden" }}>
      {VIEW_OPTS.map(o => (
        <button key={o.key} onClick={() => onChange(o.key)} title={o.title}
          style={{ fontSize: 12, padding: "3px 10px", cursor: "pointer",
                   border: "none", borderRight: "1px solid var(--line)",
                   background: value === o.key ? "var(--accent)" : "transparent",
                   color: value === o.key ? "#fff" : "var(--muted)",
                   fontWeight: value === o.key ? 700 : 400 }}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
const CELL_SOURCE_LABEL = { grid: "칩 격자", image: "그림 die", dev_grid: "개발 격자 die" };
const MATCH_RULE_LABELS = { "01strip": "01제거", "reorder": "접두사변환", "split": "분할TEG" };
/* monospace 테두리 칩 — 대상 TEG 나열용 (색 = 테두리+글자 공용) */
function TokenChip({ color = "var(--muted)", title, children }) {
  return (
    <span title={title} style={{ fontFamily: "monospace", fontSize: 12, padding: "1px 6px",
      borderRadius: 4, border: `1px solid ${color}`, color }}>{children}</span>
  );
}

/* 요약 패널 — 결과 화면 상단의 3칸(대상 판정 / 세팅 현황 / MAIN) 공용 틀 */
function MiniPanel({ title, right, children }) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px",
                  minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6,
                    flexWrap: "wrap" }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>{title}</span>
        <span style={{ marginLeft: "auto" }}>{right}</span>
      </div>
      {children}
    </div>
  );
}

/* 라벨 + 칩 나열 한 줄 — 개수가 많으면 접어 두고(collapsed) 클릭으로 편다.
   비어 있으면 empty 문구를 쓰거나 아예 그리지 않는다. */
function ChipRow({ label, color = "var(--muted)", items = [], empty, hint, collapsed = false }) {
  const [open, setOpen] = useState(!collapsed);
  if (!items.length && !empty) return null;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, fontWeight: 700, color }}>{label} {items.length}</span>
        {items.length > 0 && collapsed && (
          <LinkBtn onClick={() => setOpen(v => !v)} style={{ fontSize: 11 }}>
            {open ? "▾ 접기" : "▸ 보기"}
          </LinkBtn>
        )}
        {hint && <span style={{ fontSize: 11, color: "var(--muted)" }}>{hint}</span>}
      </div>
      {!items.length ? (
        <div style={{ fontSize: 11, color: "var(--muted)" }}>{empty}</div>
      ) : open ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 3 }}>
          {items.map(it => (
            <TokenChip key={it.key} color={color} title={it.title}>{it.text}</TokenChip>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* die 관계 한 칸 — 백엔드 die_state("in" | "near" | "out"). 경계 ±허용오차는 '근처'. */
function DieState({ state }) {
  if (state === "in") {
    return <span style={{ color: "var(--danger)", fontWeight: 700 }}
      title="TEG 사각형이 die 안에 깊이 들어감 — TEG 는 칩 사이 스크라이브에 있어야 합니다">
      die 침범
    </span>;
  }
  if (state === "near") {
    return <span style={{ color: "var(--warn)", fontWeight: 700 }}
      title="die 경계에서 허용오차(⚙️ 설정 die_tol) 안쪽/바깥쪽 — 확인 필요">
      경계 근처
    </span>;
  }
  if (state === "out") {
    return <span style={{ color: "var(--ok)", fontWeight: 700 }}
      title="die 밖(칩 사이 스크라이브) — 문제 없음">die 밖</span>;
  }
  return "";
}

function TrafficLight({ color }) {
  const c = LIGHT_COLORS[color] || LIGHT_COLORS.gray;
  return <span style={{ width: 12, height: 12, borderRadius: "50%", background: c,
    display: "inline-block", flexShrink: 0, boxShadow: `0 0 0 2px ${c}33` }} />;
}

/* 카드 안 신호등 요약 — 판정 목록마다 자기 개수를 자기 카드에서 켠다.
   items = [{light, label, n}]. 0 건은 접어 두지 않고 흐리게 남긴다
   (없다는 것도 판정 결과다). */
function LightSummary({ items }) {
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center",
                  marginBottom: 6 }}>
      {items.map(it => (
        <span key={it.light} title={it.title}
          style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11,
                   opacity: it.n ? 1 : 0.45 }}>
          <TrafficLight color={it.light} />
          <b style={{ color: it.light === "dim" || it.light === "gray"
            ? "var(--muted)" : LIGHT_COLORS[it.light] }}>{it.n}</b>
          <span style={{ color: "var(--muted)" }}>{it.label}</span>
        </span>
      ))}
    </div>
  );
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
    return <div style={{ fontSize: 12, color: "var(--danger)" }}>
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
              border: `1px solid ${isSel ? "var(--accent)" : "var(--line)"}`,
              borderRadius: 6, padding: 8, cursor: "pointer",
              background: isSel ? "var(--accent-glow)" : "transparent",
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

/* ── shot 확대 뷰 — die 격자 + 계산 좌표 기준 TEG 배치.
   빨간불/노란불 TEG 만 그린다:
     · 노란불 — 검은 테두리 + 사각형 가운데 검은 이름
     · 빨간불 — 진한 빨간 테두리 + 사각형 가운데 빨간 이름
   TEG 는 shot 대비 아주 작아 기본 배율로는 이름이 안 읽힌다 — 뷰를 크게 잡고
   최대 배율도 올린다 (zoom/pan/핀치는 공용 ZoomPanSvg). ── */
/* 겹치는 TEG 는 가장 위의 것 하나만 남긴다 — 같은 자리에 여러 TEG 가 쌓이면
   사각형과 이름이 포개져 아무것도 못 읽는다. 위(mm_y 큰 것)부터 훑으며 이미
   남긴 사각형과 겹치지 않는 것만 채택한다. mm_y 가 같으면 빨간불을 먼저 남겨
   문제가 다른 색 아래로 숨지 않게 한다.
   **대상 TEG 와 MAIN 내부 TEG 는 따로 걸러야 한다** — 한 덩어리로 돌리면
   수많은 MAIN TEG 가 대상 TEG 를 덮어 정작 봐야 할 것이 사라진다. */
function dropOverlapping(items) {
  const sorted = [...items].sort((a, b) =>
    (b.mm_y - a.mm_y)
    || ((a.light === "red" ? 0 : 1) - (b.light === "red" ? 0 : 1))
    || (a.mm_x - b.mm_x));
  const kept = [];
  sorted.forEach(t => {
    const x1 = t.mm_x + (t.w || 0), y1 = t.mm_y + (t.h || 0);
    const hit = kept.some(k =>
      t.mm_x < k.mm_x + (k.w || 0) && x1 > k.mm_x
      && t.mm_y < k.mm_y + (k.h || 0) && y1 > k.mm_y);
    if (!hit) kept.push(t);
  });
  return kept;
}

function ShotView({ shot, items }) {
  const SIZE = 560;
  const MAX_ZOOM = 60;
  const drawItems = items;
  const W = shot.shot_w_mm, H = shot.shot_h_mm;
  const pad = 0.12;
  const s = SIZE / Math.max(W * (1 + pad * 2), H * (1 + pad * 2));
  const w = W * s, h = H * s;
  const ox = (SIZE - w) / 2, oy = (SIZE - h) / 2;
  const toX = (mm) => ox + (mm + W / 2) * s;
  // SVG 는 y 가 아래로 증가 — ebeam +y(위)를 뒤집어 shot 센터가 정확히 (0,0)이 되게 한다.
  const toY = (mm) => oy + (H / 2 - mm) * s;
  const cells = shot.cells || [];
  // 개발 격자만 좌하단 코너(└)를 찍는다 — 그 점이 MAIN TEG 좌표다.
  const fromImage = shot.cell_source === "dev_grid";

  return (
    <ZoomPanSvg size={SIZE} maxZoom={MAX_ZOOM}>
      {(zoom) => (
        <>
          <rect x={ox} y={oy} width={w} height={h} fill="rgba(128,128,128,0.05)"
            stroke="var(--muted)" strokeWidth={1 / zoom} />
          {/* shot 센터 십자 */}
          <line x1={toX(0) - 5 / zoom} y1={toY(0)} x2={toX(0) + 5 / zoom} y2={toY(0)} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          <line x1={toX(0)} y1={toY(0) - 5 / zoom} x2={toX(0)} y2={toY(0) + 5 / zoom} stroke="var(--muted)" strokeWidth={0.8 / zoom} />
          {/* die 셀 — c.x/c.y = 셀 좌하단(mm), y 축 반전이라 top = toY(c.y) - 높이.
              그림 모드는 좌하단 코너(└)를 함께 찍는다 — 그 점이 MAIN TEG 좌표다. */}
          {cells.map((c, i) => (
            <g key={i}>
              <rect x={toX(c.x)} y={toY(c.y) - c.h * s} width={c.w * s} height={c.h * s}
                fill="rgba(47,158,99,0.08)" stroke="#2f9e63"
                strokeWidth={(fromImage ? 1.2 : 0.8) / zoom} opacity="0.85" />
              {fromImage && (
                <path d={`M${toX(c.x)} ${toY(c.y) - 7 / zoom} L${toX(c.x)} ${toY(c.y)} L${toX(c.x) + 7 / zoom} ${toY(c.y)}`}
                  fill="none" stroke="#2f9e63" strokeWidth={1.6 / zoom} opacity="0.95" />
              )}
              {/* die 이름(MAIN01 …) — 어느 die 인지 알아야 "다른 die 안" 판정이 읽힌다 */}
              {c.name && (
                <text x={toX(c.x) + 3 / zoom} y={toY(c.y) - 3 / zoom} fontSize={10 / zoom}
                  fill="#2f9e63" opacity="0.9" fontWeight={700}>{c.name}</text>
              )}
            </g>
          ))}
          {/* TEG — 계산 좌표(mm) 기준. 빨간불 = 진한 빨간 테두리 + 가운데 빨간 이름,
              그 외 = 검은 테두리 + 가운데 검은 이름. 이름은 사각형 안에 들어가도록
              크기를 맞추므로 확대할수록 커진다. 겹치는 것은 호출부에서 종류별로
              걸러 가장 위의 것만 넘어온다 (dropOverlapping). */}
          {drawItems.map((t) => {
            const red = t.light === "red";
            const stroke = red ? RED_EDGE : "#111827";
            const text = red ? LIGHT_COLORS.red : "#111827";
            const fill = red ? "rgba(220,38,38,0.16)"
              : t.light === "yellow" ? "rgba(217,154,26,0.28)" : "rgba(17,24,39,0.06)";
            const x = toX(t.mm_x), yBottom = toY(t.mm_y);
            const wpx = Math.max(1.5 / zoom, (t.w || 0) * s);
            const hpx = Math.max(1.5 / zoom, (t.h || 0) * s);
            const label = String(t.name || "");
            // 이름이 사각형 안에 들어가는 글자 크기 (평균 글자폭 ≈ 0.58em)
            const fs = Math.min(hpx * 0.62, (wpx * 0.92) / Math.max(1, label.length * 0.58));
            return (
              <g key={t.key}>
                <rect x={x} y={yBottom - hpx} width={wpx} height={hpx}
                  fill={fill} stroke={stroke} strokeWidth={1.6 / zoom} />
                {label && fs * zoom >= 2.5 && (
                  <text x={x + wpx / 2} y={yBottom - hpx / 2} fontSize={fs}
                    textAnchor="middle" dominantBaseline="central"
                    fill={text} fontWeight={700}>{label}</text>
                )}
              </g>
            );
          })}
          <text x={SIZE / 2} y={oy + h + 16 / zoom} fontSize={11 / zoom} fill="var(--muted)" textAnchor="middle">
            {fmtN(Math.round(W * 100) / 100)} mm
          </text>
        </>
      )}
    </ZoomPanSvg>
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
              border: `1px solid ${isA ? "var(--accent)" : "var(--line)"}`,
              fontWeight: isA ? 700 : 400,
              color: isA ? "var(--text)" : "var(--muted)",
            }}>{v}</span>
        );
      })}
      {pending && (
        <span style={{ fontSize: 11, color: "var(--warn)", fontWeight: 700 }}>재적용 필요</span>
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
        toast.ok(`위치조회 반영 완료 — ${r.saved.join(", ")}`);
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
        <span style={{ color: "var(--warn)" }}> 설비 Mapfile 세팅에서 가져온 값이라 이상이 있을 수
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
            {g.red > 0 && (
              <Pill tone="danger" size="sm"
                title={"자기 MAIN die 밖으로 나갔거나 다른 die 에 있는 내부 TEG:\n"
                  + g.tegs.filter(t => t.light === "red")
                      .map(t => `${t.teg} — ${t.light_reason}`).join("\n")}>
                🔴 {g.red}
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
function TegSection({ res, onFlatChange, markerH, setMarkerH, markerV, setMarkerV, markerVL, setMarkerVL, onMarkersApply,
                      nameOv, onPickName, pendingCount, onReapply, busy, view, setView }) {
  const teg = res.teg;
  const { summary } = teg;
  // 신호등은 백엔드 판정(row.light)을 그대로 쓴다 — 정답지에 있는 TEG 는 좌표가
  // 3 을 넘게 어긋나거나 die 를 침범하면 빨간불(불일치)이다.
  const bad = teg.rows.filter(r => r.light === "red");
  const warn = teg.rows.filter(r => r.light === "yellow");
  const extended = teg.rows.filter(r => r.status === "extended");
  // MAIN 내부 TEG(정답지 미등록) 신호등 목록 — 자기 MAIN die 안·경계면 노란불,
  // 다른 die·die 밖이면 빨간불. 대상 TEG 판정과 같은 형식 (빨강 → 노랑 → 회색 순)
  const mainChecklist = useMemo(() => {
    const out = [];
    (teg.main_groups || []).forEach(g => (g.tegs || []).forEach((t, i) => {
      if (!t.light) return;                    // die 블록 자체 행 — 판정 대상 아님
      out.push({ ...t, group: g.group, key: `${g.group}-${i}` });
    }));
    out.sort((a, b) => (LIGHT_RANK[a.light] - LIGHT_RANK[b.light])
      || String(a.teg).localeCompare(String(b.teg)));
    return out;
  }, [teg.main_groups]);
  // 무엇을 볼지 — 대상 TEG(S/L) / MAIN 내부 TEG / 둘 다. 상태는 페이지가 들고 있다
  // (아래 "MAIN 그룹 → 위치조회 반영" 카드도 같은 선택을 따라야 하므로).
  const seeTarget = view !== VIEW_MAIN;
  const seeMain = view !== VIEW_TARGET;
  // shot 배치도 — 기본은 **빨간불만**(지금 고쳐야 할 것). "전체 표시" 를 켜면
  // 대상 TEG 전체와 MAIN 내부 TEG 전체를 그린다. 어느 쪽이든 겹치는 것은
  // 종류별로 따로 걸러 가장 위의 것만 남긴다.
  const [shotAll, setShotAll] = useState(false);
  const shotTargetRows = useMemo(() => (seeTarget
    ? dropOverlapping(teg.rows
        // die_state is an independent geometric verdict. Promote it here as
        // well so a Mapfile-only module cannot disappear from red-only mode
        // when an older/stale backend response still labels it gray.
        .filter(r => shotAll || r.light === "red" || r.die_state === "in")
        .map((r, i) => ({
          ...r,
          light: r.die_state === "in" ? "red" : r.light,
          light_reason: r.die_state === "in" ? (r.light_reason || "die 침범") : r.light_reason,
          key: `r${i}`, w: r.teg_w, h: r.teg_h,
        })))
    : []), [teg.rows, seeTarget, shotAll]);
  const shotMainRows = useMemo(() => (seeMain
    ? dropOverlapping(mainChecklist
        .filter(t => shotAll || t.light === "red")
        .map(t => ({ ...t, name: t.teg, w: t.teg_w, h: t.teg_h })))
    : []), [mainChecklist, seeMain, shotAll]);
  const shotItems = useMemo(
    () => [...shotTargetRows, ...shotMainRows], [shotTargetRows, shotMainRows]);
  const [showAll, setShowAll] = useState(false);
  const [showMain, setShowMain] = useState(false);
  const [showWarn, setShowWarn] = useState(false);
  const [showRule, setShowRule] = useState(false);
  const [checklistColorSort, setChecklistColorSort] = useState(true);
  const targets = teg.targets || { items: [], matched: 0, missing: 0, total: 0, source: "default" };
  const missingTargets = targets.items.filter(t => !t.matched);
  const matchedTargets = targets.items.filter(t => t.matched);
  // 이 Mapfile 의 flat → Teg_location direction. 방향이 다른 대상 TEG 는 애초에
  // 이 원문에 없는 게 정상이라 '미설정' 이 아니라 '판정 불가' 로 가른다.
  const flatDir = res.flat.used === "v_R" ? "v" : res.flat.used === "v_L" ? "v_L" : "h";
  // Mapfile 에만 있고 정답지에 없는 module — 이름 오타/미등록 후보
  const mapfileOnly = teg.rows.filter(r => r.status === "missing");

  // ── "조회되어야 할 TEG 목록" 체크리스트 — 체크 대상 TEG 각각의 신호등.
  //    신호등은 백엔드 판정(row.light)을 그대로 쓴다: 빨강=좌표 불일치 또는 die 침범,
  //    노랑=확인필요, 보라=확장체크, 초록=위치확인. 대상 목록엔 있으나 mapfile 에
  //    없으면 회색(미등록). 같은 teg 여러 행이면 최악(빨강 우선)을 대표로 쓴다.
  const rowByRefTeg = useMemo(() => {
    const m = {};
    (teg.rows || []).forEach(r => {
      if (!r.ref_teg) return;
      const cur = m[r.ref_teg];
      if (!cur || (LIGHT_RANK[r.light] ?? 9) < (LIGHT_RANK[cur.light] ?? 9)) m[r.ref_teg] = r;
    });
    return m;
  }, [teg.rows]);
  // 대상 TEG 의 방향 — 정답지 direction 열이 1순위, 없으면 이름 접두(H_/V_) 폴백.
  const dirOf = (t) => (["v", "v_L", "h"].includes(t.direction))
    ? t.direction
    : (String(t.teg).toUpperCase().startsWith("V_") ? "v"
      : String(t.teg).toUpperCase().startsWith("H_") ? "h" : flatDir);
  const checklist = useMemo(() => {
    const items = (targets.items || []).map(t => {
      const row = rowByRefTeg[t.teg];
      const dir = dirOf(t);
      let light, label;
      if (row) {
        const rl = row.match_rule && row.match_rule !== "exact" ? MATCH_RULE_LABELS[row.match_rule] : null;
        light = row.light || "gray";
        label = `${row.light_reason || "판정 불가"}${rl ? `(${rl})` : ""}`;
      } else if (t.matched) {
        light = "green"; label = "확인";
      } else if (dir !== flatDir) {
        // 반대 방향 TEG — 이 Mapfile(flat 하나 기준)에는 없는 게 정상이다.
        light = "dim"; label = `${dir === "v_L" ? "Vertical(L)" : dir === "v" ? "Vertical(R)" : "Horizontal"} — 판정 불가`;
      } else {
        light = "gray"; label = "Mapfile 미설정";
      }
      return { teg: t.teg, top_cell: t.top_cell, matched_by: t.matched_by,
               direction: dir, light, label, row };
    });
    if (checklistColorSort) {
      items.sort((a, b) => (LIGHT_RANK[a.light] - LIGHT_RANK[b.light])
        || String(a.teg).localeCompare(String(b.teg)));
    }
    return items;
  }, [targets.items, rowByRefTeg, checklistColorSort, flatDir]);
  const lightCounts = checklist.reduce((acc, it) => {
    acc[it.light] = (acc[it.light] || 0) + 1; return acc;
  }, {});
  // 미설정 세분화: 확장체크 통과 / 다른 방향(판정 불가) / 진짜 미설정
  const { extMatchedTargets, otherDirTargets, trulyMissingTargets } = useMemo(() => {
    const ext = [], otherDir = [], truly = [];
    missingTargets.forEach(t => {
      const row = rowByRefTeg[t.teg];
      if (row) {
        ext.push({ ...t, row });
      } else if (dirOf(t) !== flatDir) {
        otherDir.push(t);
      } else {
        truly.push(t);
      }
    });
    return { extMatchedTargets: ext, otherDirTargets: otherDir, trulyMissingTargets: truly };
  }, [missingTargets, rowByRefTeg, flatDir]);
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
    { key: "st", label: "", width: 30,
      render: r => <span title={r.light_reason || ""}>
        {LIGHT_ICON[r.light] || STATUS_ICON[r.status] || ""}
      </span> },
    { key: "name", label: "module_name",
      render: r => <NameCell r={r} ov={nameOv[r.idx]} onPick={onPickName} /> },
    { key: "flat_info", label: "flat",
      render: r => <span title={r.flat_marker ? `마커: ${r.flat_marker}` : '마커 없음'}>
        {r.flat_used === 'h' ? 'H' : r.flat_used === 'v_R' ? 'V(R)' : r.flat_used === 'v_L' ? 'V(L)' : r.flat_used}
      </span> },
    { key: "orig", label: "Mapfile (x,y)", render: r => `(${r.x},${r.y})` },
    { key: "calc_x", label: "환산X", align: "right" },
    { key: "calc_y", label: "환산Y", align: "right" },
    { key: "terms", label: "계산항", render: r => {
        const t = r.coordinate_terms || {};
        const text = `Ocalc=Obase+R·p+Cproduct+Kglobal+Kproduct\n`
          + `Obase=${JSON.stringify(t.base || [])}, Cproduct=${JSON.stringify(t.flat_correction || [])}, `
          + `Kglobal=${JSON.stringify(t.global_module || [0, 0])}, Kproduct=${JSON.stringify(t.product_module || [0, 0])}`;
        return <span title={text} style={{ cursor: "help", color: "var(--accent)" }}>수식·값</span>;
      } },
    { key: "matched", label: "매칭 TEG", render: r => {
        if (!r.ref_teg) return "";
        const ruleLabel = r.match_rule && MATCH_RULE_LABELS[r.match_rule];
        const tag = ruleLabel || (r.match_source === "top_cell" ? "top_cell" : "");
        const seq = r.ref_seq != null ? ` #${r.ref_seq}/${r.ref_total}` : "";
        const ruleDesc = ruleLabel
          ? `확장체크(${ruleLabel}): ${r.match_token} → ${r.ref_teg}`
          : r.match_source === "top_cell"
            ? `top_cell '${r.match_token}' 로 ${r.ref_teg} 에 매칭`
            : `teg 이름으로 ${r.ref_teg} 에 매칭`;
        return (
          <span title={ruleDesc
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
      key: "chip", label: "die", render: r => <DieState state={r.die_state} />,
    }] : []),
    { key: "note", label: "비고", render: r => r.rule_note || "" },
  ];
  const overlapRowStyle = (r) => (r.die_state === "in" ? { background: "rgba(224,82,82,0.10)" } : {});
  const badCols = [
    { key: "name", label: "module_name" },
    { key: "flat_info", label: "flat",
      render: r => <span title={r.flat_marker ? `마커: ${r.flat_marker}` : '마커 없음'}>
        {r.flat_used === 'h' ? 'H' : r.flat_used === 'v_R' ? 'V(R)' : r.flat_used === 'v_L' ? 'V(L)' : r.flat_used}
      </span> },
    { key: "x", label: "Map X", align: "right" },
    { key: "calc_x", label: "환산X", align: "right" },
    { key: "ref_x", label: "DB X", align: "right", render: r => fmtN(r.ref_x) },
    { key: "dx", label: "ΔX", align: "right", render: r => fmtN(r.dx) },
    { key: "y", label: "Map Y", align: "right" },
    { key: "calc_y", label: "환산Y", align: "right" },
    { key: "ref_y", label: "DB Y", align: "right", render: r => fmtN(r.ref_y) },
    { key: "dy", label: "ΔY", align: "right", render: r => fmtN(r.dy) },
    { key: "why", label: "판정", render: r => (
        <span style={{ color: r.light === "red" ? "var(--danger)" : "var(--warn)",
                       fontWeight: 700 }}>{r.light_reason || ""}</span>
      ) },
  ];
  // die 관계 — die 셀을 얻은 모드(res.shot.checked)에서만 의미가 있다.
  const dieCol = { key: "die", label: "die", render: r => <DieState state={r.die_state} /> };
  const warnCols = res.shot?.checked ? [...badCols, dieCol] : badCols;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* 무엇을 볼지 — 대상 TEG(S/L) / MAIN 내부 TEG / 둘 다. 아래 판정 패널·표·
          shot 배치도가 모두 이 선택을 따른다. */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <ViewToggle value={view} onChange={setView} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          {view === VIEW_TARGET
            ? `대상 TEG ${targets.total}개만 보는 중 — MAIN 내부 TEG ${mainChecklist.length}개는 숨김`
            : view === VIEW_MAIN
              ? `MAIN 내부 TEG ${mainChecklist.length}개만 보는 중 — 대상 TEG ${targets.total}개는 숨김`
              : `대상 TEG ${targets.total}개 + MAIN 내부 TEG ${mainChecklist.length}개`}
        </span>
      </div>
      {!teg.ref_ok && (
        <div style={{ fontSize: 13, color: "var(--danger)" }}>
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
        {["h", "v_R", "v_L"].map(f => (
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
        {flatUsed === "v_L" && (
          <Pill tone="neutral" title="Vertical(L) 원복: (x, y) → (-y, x)">V(L) 회전 원복</Pill>
        )}
        {(() => {
          const pb = res.pchk_base;
          const fromDb = pb && pb.source === "db";
          return (
            <span style={{ fontSize: 12, color: fromDb ? "var(--ok)" : "var(--muted)" }}
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
                      background: "var(--warn-50)", fontSize: 13 }}>
          <span style={{ fontWeight: 600 }}>기준 마커 직접 입력</span>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            H(가로):
            <input value={markerH} onChange={e => setMarkerH(e.target.value)}
              placeholder="예: H_TPCHK" spellCheck={false}
              style={{ width: 130, fontFamily: "monospace", fontSize: 12 }} />
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            V(R):
            <input value={markerV} onChange={e => setMarkerV(e.target.value)}
              placeholder="예: V_TPCHK" spellCheck={false}
              style={{ width: 130, fontFamily: "monospace", fontSize: 12 }} />
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            V(L):
            <input value={markerVL} onChange={e => setMarkerVL(e.target.value)}
              placeholder="예: VL_TPCHK" spellCheck={false}
              style={{ width: 130, fontFamily: "monospace", fontSize: 12 }} />
          </label>
          <Button variant="primary" disabled={!markerH.trim() && !markerV.trim() && !markerVL.trim()}
            onClick={onMarkersApply}>마커로 재검사</Button>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            쉼표로 여러 개 입력 가능 — TEG 별 꼬리표에서 이 마커로 flat 을 개별 판정합니다
          </span>
        </div>
      )}
      {!res.flat.needs_input
        && ((res.flat.custom_markers?.h?.length || 0) + (res.flat.custom_markers?.v_R?.length || 0) + (res.flat.custom_markers?.v_L?.length || 0) > 0) && (
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          사용자 지정 마커 적용 중 — H: {(res.flat.custom_markers.h || []).join(", ") || "-"} ·
          V(R): {(res.flat.custom_markers.v_R || []).join(", ") || "-"} · V(L): {(res.flat.custom_markers.v_L || []).join(", ") || "-"}
        </div>
      )}

      {/* ── 한 줄 판정 요약 — 세는 단위는 '정답지 체크 대상 TEG' 다.
          (Mapfile 행 기준 수치는 뒤의 회색 문장에 부수적으로 적는다.) ── */}
      {seeTarget && teg.ref_ok && targets.total > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <Pill tone={lightCounts.green ? "ok" : "neutral"}>🟢 정상 {lightCounts.green || 0}</Pill>
          <Pill tone={lightCounts.red ? "danger" : "neutral"}>🔴 불일치 {lightCounts.red || 0}</Pill>
          <Pill tone={lightCounts.yellow ? "warn" : "neutral"}
            title="ΔX·ΔY 가 각각 3 이내 — 소수점·세팅 차이 정도의 작은 오차">
            🟡 확인필요 {lightCounts.yellow || 0}
          </Pill>
          {lightCounts.purple > 0 && (
            <Pill tone="warn" title="이름 변환 규칙으로 매칭한 것 — 위치가 아닌 이름 검증">
              🟣 확장 {lightCounts.purple}
            </Pill>
          )}
          <Pill tone={lightCounts.gray ? "danger" : "neutral"}
            title="대상인데 이 Mapfile 의 module name 에 없음 — 세팅 누락 후보">
            ⚪ 미설정 {lightCounts.gray || 0}
          </Pill>
          {lightCounts.dim > 0 && (
            <Pill tone="neutral"
              title={`이 Mapfile 은 ${FLAT_LABELS[res.flat.used]} 기준 — 반대 방향 TEG 는 여기에 없는 게 정상입니다`}>
              ⛔ 판정 불가 {lightCounts.dim}
            </Pill>
          )}
          {res.shot?.checked && (
            <>
              <Pill tone={summary.die_in ? "danger" : "ok"}
                title="정답지에 있는 TEG 중 die 안에 깊이 들어간 것 — TEG 는 칩 사이(스크라이브)에 있어야 정상">
                ⚠ die 침범 {summary.die_in || 0}
              </Pill>
              {summary.die_near > 0 && (
                <Pill tone="warn"
                  title="die 경계에서 허용오차(⚙️ 설정 die_tol) 안쪽/바깥쪽 — 확인 필요">
                  die 경계 근처 {summary.die_near}
                </Pill>
              )}
            </>
          )}
          <span style={{ fontSize: 11, color: "var(--muted)" }}>
            Mapfile module {summary.total}행
            {teg.excluded_main ? ` (MAIN ${teg.excluded_main}행 제외)` : ""}
            {summary.missing ? ` · 정답지에 없는 module ${summary.missing}` : ""}
          </span>
        </div>
      )}

      {/* ── 요약 패널 — ① 대상 TEG 판정 ①-2 MAIN 내부 TEG ② Mapfile 세팅 현황 ③ MAIN 종류 ── */}
      <div style={{ display: "grid", gap: 10, alignItems: "start",
                    gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        {seeTarget && <MiniPanel title="① 대상 TEG 판정 (S/L TEG)"
          right={targets.total > 0 && (
            <button onClick={() => setChecklistColorSort(v => !v)}
              title="정렬 기준 전환 — 색상순(빨강→노랑→초록) ↔ 이름순"
              style={{ fontSize: 11, color: "var(--accent)", background: "none",
                       border: "1px solid var(--line)", borderRadius: 4, cursor: "pointer",
                       padding: "1px 6px" }}>
              {checklistColorSort ? "색상순 ↓" : "이름순"}
            </button>
          )}>
          {targets.total > 0 && (
            <LightSummary items={[
              { light: "red", label: "불일치", n: lightCounts.red || 0,
                title: "ΔX·ΔY 가 3 초과이거나 die 안에 깊이 들어감" },
              { light: "yellow", label: "확인필요", n: lightCounts.yellow || 0,
                title: "ΔX·ΔY 각 3 이내 또는 die 경계 근처" },
              { light: "purple", label: "확장체크", n: lightCounts.purple || 0,
                title: "이름 변환 규칙으로 매칭 — 위치가 아닌 이름 검증" },
              { light: "green", label: "정상", n: lightCounts.green || 0 },
              { light: "gray", label: "미설정", n: lightCounts.gray || 0,
                title: "대상인데 이 Mapfile 의 module name 에 없음" },
              { light: "dim", label: "판정 불가", n: lightCounts.dim || 0,
                title: `이 Mapfile 은 ${FLAT_LABELS[res.flat.used]} 기준 — 반대 방향 TEG` },
            ]} />
          )}
          <TargetChecklist checklist={checklist} total={targets.total}
            source={targets.source} shotChecked={res.shot?.checked} />
        </MiniPanel>}

        {/* MAIN 내부 TEG 도 대상 TEG 판정과 같은 신호등 목록으로 본다 —
            정답지에 없어 좌표 대조는 못 하고 '자기 MAIN die 에 있는가'만 본다. */}
        {seeMain && <MiniPanel title="①-2 MAIN 내부 TEG 판정"
          right={<span style={{ fontSize: 11, color: "var(--muted)" }}>
            {mainChecklist.length}개
          </span>}>
          {!mainChecklist.length ? (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              {(teg.main_groups || []).length
                ? "판정할 MAIN 내부 TEG 가 없습니다 (die 정보가 없으면 판정을 건너뜁니다)."
                : "이 Mapfile 에 MAIN 그룹이 없습니다."}
            </div>
          ) : (
            <>
              <LightSummary items={[
                { light: "red", label: "확인 필요", n: mainChecklist.filter(it => it.light === "red").length,
                  title: "다른 MAIN die 안·경계이거나 자기 MAIN die 밖" },
                { light: "yellow", label: "MAIN die 안", n: mainChecklist.filter(it => it.light === "yellow").length,
                  title: "자기 MAIN die 안·경계 근처 — MAIN 내부 TEG 는 원래 die 안에 있으므로 정상" },
                { light: "gray", label: "판정 불가", n: mainChecklist.filter(it => it.light === "gray").length,
                  title: "그 이름의 die 셀을 못 얻음 (크기 미상·격자/그림 모드)" },
              ]} />
              <div style={{ border: "1px solid var(--line)", borderRadius: 6,
                            overflow: "hidden", maxHeight: 300, overflowY: "auto" }}>
                {mainChecklist.map(it => (
                  <div key={it.key} style={{ display: "flex", alignItems: "center", gap: 8,
                                             padding: "5px 8px", fontSize: 12,
                                             borderBottom: "1px solid var(--line)" }}>
                    <TrafficLight color={it.light} />
                    <span style={{ fontWeight: 600, fontFamily: "monospace", overflow: "hidden",
                                   textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={`${it.group} · 환산 (${it.x}, ${it.y})`}>
                      {it.teg}
                    </span>
                    <span style={{ marginLeft: "auto", fontSize: 11, textAlign: "right",
                                   color: it.light === "gray" ? "var(--muted)" : LIGHT_COLORS[it.light],
                                   fontWeight: 700 }}>
                      {it.light_reason}
                    </span>
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                정답지 미등록 · 노랑=자기 MAIN die 안·경계 · 빨강=다른 die·die 밖 · 회색=판정 불가
              </div>
            </>
          )}
        </MiniPanel>}

        {seeTarget && <MiniPanel title="② Mapfile 세팅 현황"
          right={<span style={{ fontSize: 11, color: "var(--muted)" }}>
            {targets.source === "config" ? "지정 대상" : "기본(H_/V_)"} {targets.total}개
          </span>}>
          {targets.total === 0 ? (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              체크 대상 TEG 가 없습니다 — 위치 조회 → TEG 목록 → "Mapfile 체크 대상 TEG" 에서
              지정하거나, 이름이 H_/V_ 로 시작하는 TEG 가 있으면 자동으로 대상이 됩니다.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <ChipRow label="세팅됨" color="var(--ok)" collapsed
                items={matchedTargets.map(t => ({
                  key: t.teg,
                  text: t.matched_by === "top_cell" ? `${t.teg} ⟵ ${t.matched_module}` : t.teg,
                  title: `Mapfile module "${t.matched_module}" 와 ${t.matched_by === "top_cell" ? "top_cell" : "teg"} 완전 일치`,
                }))} />
              <ChipRow label="세팅 안 됨" color="var(--danger)"
                empty="없음 — 대상 TEG 가 모두 Mapfile 에 있습니다"
                items={trulyMissingTargets.map(t => ({
                  key: t.teg, text: t.teg,
                  title: t.top_cell?.length ? `top_cell: ${t.top_cell.join(", ")}` : "top_cell 없음",
                }))} />
              {extMatchedTargets.length > 0 && (
                <ChipRow label="확장체크로 매칭" color="var(--violet)"
                  items={extMatchedTargets.map(t => ({
                    key: t.teg,
                    text: `${t.teg} (${MATCH_RULE_LABELS[t.row?.match_rule] || "확장"})`,
                    title: t.row ? `${t.row.match_token} → ${t.row.ref_teg}` : "",
                  }))} />
              )}
              {otherDirTargets.length > 0 && (
                <ChipRow label={`판정 불가 (${flatDir === "h" ? "Vertical" : "Horizontal"} TEG)`}
                  color="var(--muted)"
                  hint={`이 Mapfile 은 ${FLAT_LABELS[res.flat.used]} 기준이라 반대 방향 TEG 는 없는 게 정상입니다`}
                  items={otherDirTargets.map(t => ({ key: t.teg, text: t.teg }))} />
              )}
              {mapfileOnly.length > 0 && (
                <ChipRow label="Mapfile 에만 있음 (정답지 미등록)" color="var(--muted)" collapsed
                  items={mapfileOnly.slice(0, 60).map((r, i) => ({
                    key: `${r.name}-${i}`, text: r.name,
                    title: `Mapfile (${r.x}, ${r.y}) — 정답지에 같은 이름의 teg/top_cell 이 없습니다`,
                  }))} />
              )}
            </div>
          )}
        </MiniPanel>}

        {seeMain && <MiniPanel title="③ MAIN 안의 TEG 종류"
          right={<span style={{ fontSize: 11, color: "var(--muted)" }}>
            MAIN {(teg.main_groups || []).length}그룹
          </span>}>
          {!(teg.main_groups || []).length ? (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              이 Mapfile 에 MAIN 그룹이 없습니다.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {teg.main_groups.map(g => (
                <div key={g.group}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 700, fontFamily: "monospace", fontSize: 12 }}>{g.group}</span>
                    <Pill tone="neutral" size="sm">내부 TEG {g.tegs.length}종</Pill>
                    {g.red > 0 && (
                      <Pill tone="danger" size="sm"
                        title={g.tegs.filter(t => t.light === "red")
                          .map(t => `${t.teg} — ${t.light_reason}`).join("\n")}>
                        🔴 {g.red}
                      </Pill>
                    )}
                    {g.yellow > 0 && (
                      <Pill tone="warn" size="sm"
                        title={`${g.group} die 안 — 정답지에 없어 좌표 정밀 대조는 못 합니다`}>
                        🟡 {g.yellow}
                      </Pill>
                    )}
                  </div>
                  {/* 이름은 빨간불만 적는다 — 나머지는 "그 MAIN 이 들어있다" 로 충분하다
                      (정상 TEG 이름을 다 늘어놓으면 정작 고칠 것이 묻힌다). */}
                  {g.red > 0 ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 3 }}>
                      {g.tegs.filter(t => t.light === "red").slice(0, 20).map(t => (
                        <TokenChip key={t.teg} color="var(--danger)"
                          title={`(${t.x}, ${t.y}) — ${t.light_reason}`}>
                          {t.teg}
                        </TokenChip>
                      ))}
                      {g.red > 20 && (
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>+{g.red - 20}</span>
                      )}
                    </div>
                  ) : (
                    <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
                      {g.group} 이(가) 들어가 있습니다 — 빨간불 없음
                    </div>
                  )}
                </div>
              ))}
              <div style={{ fontSize: 11, color: "var(--muted)" }}>
                아래 "MAIN 그룹 → 위치조회 반영" 에서 이 내부 TEG 들을 위치 조회에 반영할 수 있습니다.
              </div>
            </div>
          )}
        </MiniPanel>}
      </div>

      {/* 행별 이름 재지정 후 일괄 재검사 — 클릭마다 서버를 부르지 않고 모아서 1회 */}
      {pendingCount > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                      padding: "6px 10px", borderRadius: 8,
                      background: "var(--warn-50)", fontSize: 13 }}>
          <Button variant="primary" disabled={busy} onClick={onReapply}>
            이름 재지정 재검사 ({pendingCount}건)
          </Button>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            선택한 이름으로 정답지 대조를 다시 실행합니다
          </span>
        </div>
      )}

      {/* ── 고쳐야 할 것: 불일치는 항상 펼치고, 확인필요는 접어 둔다 ── */}
      {seeTarget && teg.ref_ok && (bad.length ? (
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--danger)", marginBottom: 6 }}>
            🔴 불일치 {bad.length}건 — ΔX·ΔY 가 3 을 초과하거나 die 안에 들어간 TEG
          </div>
          <DataTable columns={badCols} rows={bad} maxHeight={240} rowStyle={overlapRowStyle} />
        </div>
      ) : (
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ok)" }}>🟢 불일치 없음</div>
      ))}

      {seeTarget && teg.ref_ok && warn.length > 0 && (
        <div>
          <LinkBtn onClick={() => setShowWarn(v => !v)} style={{ color: "var(--warn)" }}>
            {showWarn ? "▾" : "▸"} 🟡 확인필요 {warn.length}건 — ΔX·ΔY 각 3 이내
            {res.shot?.checked
              ? ` (die 경계 근처 ${warn.filter(r => r.die_state === "near").length}`
                + ` / die 밖 ${warn.filter(r => r.die_state === "out").length})`
              : ""}
          </LinkBtn>
          {showWarn && (
            <DataTable columns={warnCols} rows={warn} maxHeight={240} rowStyle={overlapRowStyle} />
          )}
        </div>
      )}

      {seeTarget && <div>
        <LinkBtn onClick={() => setShowAll(v => !v)}>
          {showAll ? "▾" : "▸"} 자세히 — 전체 {teg.rows.length}행
          {extended.length ? ` · 확장체크 ${extended.length}` : ""}
          {teg.excluded_main ? ` · MAIN 제외 ${teg.excluded_main}` : ""}
        </LinkBtn>
        {showAll && (
          <>
            {extended.length > 0 && (
              <div style={{ margin: "6px 0" }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "var(--violet)", marginBottom: 4 }}>
                  🟣 확장체크 {extended.length}건 — 이름 변환 규칙으로 재매칭
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {extended.map((r, i) => (
                    <TokenChip key={i} color="var(--violet)"
                      title={`${r.match_token} → ${r.ref_teg} (${MATCH_RULE_LABELS[r.match_rule] || "확장"})${r.match_source === "top_cell" ? " — top_cell" : ""}`}>
                      {r.match_token} → {r.ref_teg}
                    </TokenChip>
                  ))}
                </div>
              </div>
            )}
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
              <LinkBtn onClick={() => setRowLimit(l => l + 500)} style={{ padding: "4px 0" }}>
                더 보기 +500 ({rowLimit}/{filteredRows.length})
              </LinkBtn>
            )}
          </>
        )}
      </div>}

      {/* MAIN 으로 인식돼 제외된 행 — 자동 인식이 엉뚱한 토큰(MAIN 포함)을 집은
          경우 여기서 이름을 재지정하면 검사 대상으로 돌아온다 ("자세히" 안) */}
      {seeTarget && showAll && teg.excluded_main > 0 && (
        <div>
          <LinkBtn onClick={() => setShowMain(v => !v)}>
            {showMain ? "▾" : "▸"} MAIN 제외 {teg.excluded_main}건 — 이름이 잘못 인식된 행은 여기서 재지정
          </LinkBtn>
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

      {/* shot 확대 — 기본은 빨간불만, "전체 표시" 를 켜면 대상 TEG·MAIN TEG 전부 */}
      {res.shot?.available && (
        <div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                        fontSize: 12, marginBottom: 6 }}>
            <span style={{ fontWeight: 700 }}>shot 확대</span>
            <div style={{ display: "inline-flex", border: "1px solid var(--line)",
                          borderRadius: 6, overflow: "hidden" }}>
              {[[false, "🔴 빨간불만"], [true, "전체 표시"]].map(([v, label]) => (
                <button key={String(v)} onClick={() => setShotAll(v)}
                  title={v ? "대상 TEG 전체와 MAIN 내부 TEG 전체를 그립니다"
                    : "지금 고쳐야 할 빨간불만 그립니다 (기본)"}
                  style={{ fontSize: 12, padding: "3px 10px", cursor: "pointer", border: "none",
                           borderRight: "1px solid var(--line)",
                           background: shotAll === v ? "var(--accent)" : "transparent",
                           color: shotAll === v ? "#fff" : "var(--muted)",
                           fontWeight: shotAll === v ? 700 : 400 }}>
                  {label}
                </button>
              ))}
            </div>
            <span style={{ color: "var(--muted)" }}>
              {fmtN(res.shot.shot_w_mm)}×{fmtN(res.shot.shot_h_mm)} mm · shot 센터 = ebeam (0,0)
              {seeTarget ? ` · 대상 TEG ${shotTargetRows.length}` : ""}
              {seeMain ? ` · MAIN 내부 TEG ${shotMainRows.length}` : ""}
              {" 표시 (겹치면 가장 위의 것만)"}
            </span>
            <Pill tone={res.shot.checked ? "ok" : "warn"} size="sm">
              {res.shot.checked
                ? `die 판정: ${CELL_SOURCE_LABEL[res.shot.cell_source] || res.shot.cell_source} ${(res.shot.cells || []).length}개`
                : "die 판정 건너뜀"}
            </Pill>
            <LinkBtn onClick={() => setShowRule(v => !v)} style={{ fontSize: 11 }}>
              {showRule ? "▾ 표시 규칙" : "▸ 표시 규칙"}
            </LinkBtn>
          </div>
          {showRule && (
            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6, lineHeight: 1.7 }}>
              계산 좌표(EbeamX/Y × 배율) 기준으로 그립니다. 기본은 <b>빨간불만</b>(진한 빨간
              테두리 + 빨간 이름), <b>전체 표시</b>를 켜면 대상 TEG 전체와 MAIN 내부 TEG 전체를
              검은 테두리 + 검은 이름으로 함께 그립니다. 이름은 사각형 가운데에 넣으므로 확대하면
              읽힙니다. <b>겹치는 것은 가장 위의 것 하나만</b> 그리며, 대상 TEG 와 MAIN 내부 TEG 는
              따로 걸러 MAIN 이 대상 TEG 를 덮지 않습니다.
              정답지에 있는 TEG 는 ΔX·ΔY 가 3 을 넘거나 die 안에 깊이 들어가면 빨간불이고,
              둘 다면 사유에 둘 다 적습니다. <b>die 경계에서 허용오차 안쪽/바깥쪽</b>(⚙️ 설정
              die_tol, ebeam raw 단위)은 노란불 '경계 근처' 입니다.
              정답지에 없는 <b>MAINxx</b> TEG 는 자기 MAIN die 안·경계면 노란불,
              다른 MAIN die·die 밖이면 빨간불입니다 (기본 TEG 사이즈 기준).
              {res.shot.checked ? (
                <>
                  {res.shot.cell_source === "image" && <> die 영역 = ⚙️ 설정에 붙여넣은 <b>그림에서 인식한 사각형</b> ({res.shot.image_count ?? 0}개).</>}
                  {res.shot.cell_source === "dev_grid" && <> die 영역 = <b>MAIN TEG 좌표(die 좌하단, └)</b> + MAIN chip 크기 파일의 chip 크기 (MAIN {res.shot.align?.anchors ?? 0}개 중 크기 있는 {res.shot.align?.sized ?? 0}개).</>}
                  {res.shot.cell_source === "grid" && <> die 영역 = ⚙️ 설정의 칩 개수·크기·간격 격자.</>}
                </>
              ) : res.shot.mode === "image" ? (
                <> die 겹침 검사는 건너뜁니다 — 그림에서 die 사각형을 찾지 못했습니다.
                  그림 없이 chip 크기로 판정하려면 표시 방식을 <b>개발 격자</b>로 두세요.</>
              ) : res.shot.mode === "dev_grid" ? (
                <> die 겹침 검사는 건너뜁니다 — {res.shot.align?.anchors
                  ? <>MAIN TEG {res.shot.align.anchors}개는 있지만 <b>MAIN chip 크기 파일</b>에 이 제품 크기가 없습니다</>
                  : <>MAIN 으로 이름 붙은 TEG 좌표가 없습니다</>}.</>
              ) : (
                <> ⚙️ 설정에서 shot 표시 방식을 "칩 격자"·"그림"·"개발 격자" 중 하나로 지정하면
                  die 겹침을 검사합니다.</>
              )}
              {" "}※ 크기(teg_w/teg_h)가 없는 TEG 와 MAIN 내부 TEG 는 ⚙️ 설정의 <b>기본 TEG 사이즈</b>로 그립니다.
            </div>
          )}
          {!shotItems.length && (targets.total > 0 || view === VIEW_MAIN) && (
            <div style={{ fontSize: 12, color: "var(--ok)", marginBottom: 6 }}>
              🟢 배치도에 표시할 {shotAll ? "TEG" : "빨간불 TEG"} 가 없습니다
              {view !== VIEW_ALL ? ` (${view === VIEW_MAIN ? "MAIN TEG" : "대상 TEG"}만 보는 중)` : ""}.
            </div>
          )}
          {!shotItems.length && seeTarget && targets.total === 0 && res.vehicle && (
            <div style={{ fontSize: 12, color: "var(--danger)", marginBottom: 6 }}>
              ⚠ 체크할 TEG가 설정되어 있지 않습니다 — 위치 조회 → TEG 목록 → "Mapfile 체크 대상 TEG" 에서 지정하세요.
            </div>
          )}
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
            <ShotView shot={res.shot} items={shotItems} />
            <div style={{ minWidth: 240, flex: "0 1 300px", display: "flex",
                          flexDirection: "column", gap: 8 }}>
              {/* 배치도에 그린 대상 TEG 가 5개 미만이면 각 TEG 의 ebeam 좌표도 함께 표시 */}
              {shotTargetRows.length > 0 && shotTargetRows.length < 5 && (
                <div style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ fontWeight: 700 }}>TEG ebeam 좌표 (표시 중)</div>
                  {shotTargetRows.map((r, i) => (
                    <div key={i} style={{ border: "1px solid var(--line)", borderRadius: 6,
                                          padding: "6px 8px" }}>
                      <div style={{ fontWeight: 600 }}>
                        {LIGHT_ICON[r.light] || ""} {r.name}
                        {r.light_reason ? ` — ${r.light_reason}` : ""}
                      </div>
                      <div style={{ color: "var(--muted)" }}>
                        ebeam_x/y: {r.ref_x !== null && r.ref_x !== undefined
                          ? `(${r.ref_x}, ${r.ref_y})` : "정답지 미등록"}
                      </div>
                      <div style={{ color: "var(--muted)" }}>
                        계산값: ({r.calc_x}, {r.calc_y})
                      </div>
                      {res.shot?.checked && (
                        <div style={{ fontWeight: 600 }}>
                          {CELL_SOURCE_LABEL[res.shot.cell_source] || "die"}:{" "}
                          <DieState state={r.die_state} />
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

/* ── 대상 TEG 판정 체크리스트 — 정답지 체크 대상 TEG 각각의 신호등.
   초록=위치확인 · 보라=확장체크 · 노랑=확인필요 · 빨강=불일치 ·
   회색=Mapfile 미설정 · 연회색=방향이 달라 이 Mapfile 로는 판정 불가. ── */
function TargetChecklist({ checklist, total, source, shotChecked }) {
  const cell = { display: "flex", alignItems: "center", gap: 8, padding: "5px 8px",
                 borderBottom: "1px solid var(--line)", fontSize: 12 };
  if (total === 0) {
    return (
      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        조회 대상 TEG 가 없습니다 — 위치 조회 → TEG 목록 → "Mapfile 체크 대상 TEG" 에서 지정하세요.
      </div>
    );
  }
  return (
    <>
      <div style={{ border: "1px solid var(--line)", borderRadius: 6, overflow: "hidden",
                    maxHeight: 300, overflowY: "auto" }}>
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
            <span style={{ marginLeft: "auto", fontSize: 11, textAlign: "right",
                           color: it.light === "dim" ? "var(--muted)" : LIGHT_COLORS[it.light],
                           fontWeight: 700 }}>
              {it.label}
              {it.row && (it.light === "red" || it.light === "yellow") && it.row.dx !== null
                ? ` (Δ${fmtN(it.row.dx)}, ${fmtN(it.row.dy)})` : ""}
              {shotChecked && it.row?.die_state === "in" ? " ⚠" : ""}
            </span>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
        {source === "config" ? "지정 대상" : "기본(H_/V_)"} {total}개 · 초록=위치확인 ·
        보라=확장체크 · 노랑=확인필요 · 빨강=불일치 · 회색=미설정 · 연회색=방향 달라 판정 불가
      </div>
    </>
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
  const [markerVL, setMarkerVL] = useState("");
  // 행별 module 이름 재지정 {idx: 이름} — 원문이 바뀌면 idx 가 어긋나므로 초기화
  const [nameOv, setNameOv] = useState({});
  // 결과 화면에서 무엇을 볼지 — 대상 TEG(S/L) / MAIN 내부 TEG / 둘 다(기본)
  const [view, setView] = useState(VIEW_ALL);

  const parseMarkers = (s) => String(s || "").split(",").map(t => t.trim()).filter(Boolean);

  const run = async (flatOverride) => {
    const text = textRef.current || "";
    if (!text.trim()) { toast.error("원문을 입력하세요"); return; }
    const useFlat = flatOverride === undefined ? flat : flatOverride;
    const h = parseMarkers(markerH), v = parseMarkers(markerV), vl = parseMarkers(markerVL);
    const markers = (h.length || v.length || vl.length) ? { h, v_R: v, v_L: vl } : null;
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
                          return <span style={{ color: s === "측정" ? "var(--ok)" : "var(--danger)",
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
                markerVL={markerVL} setMarkerVL={setMarkerVL}
                onMarkersApply={onMarkersApply}
                nameOv={nameOv} onPickName={onPickName}
                pendingCount={pendingCount} onReapply={onReapply} busy={busy}
                view={view} setView={setView} />
            )}
          </Card>

          {view !== VIEW_TARGET && (res.teg.main_groups || []).length > 0 && (
            <Card title={`MAIN 그룹 → 위치조회 반영 (${res.teg.main_groups.length})`}>
              <MainGroupSection vehicle={vehicle} groups={res.teg.main_groups} />
            </Card>
          )}
        </>
      )}
    </div>
  );
}
