/* TegGenerate.jsx — Mapfile용 좌표 생성 (TEG 위치 조회 페이지의 "Mapfile용 좌표 생성" 탭).
   TEG Mapfile 체크의 **역방향**이다. 체크가 "설비 원문 → 정답지 대조" 라면
   이쪽은 "정답지(Teg_location) → 설비 좌표" 로, 셋업을 처음 올릴 때
   설비 Mapfile 과 크로스체크할 기준표(좌표표)를 만든다.

   · 기준 PCHK 을 (0, 0) 으로 둔 상대좌표 (체크가 원복하는 그 좌표계)
   · Horizontal/Vertical(R)은 기본 표, Vertical(L)은 L 방향 데이터가 있을 때만 낸다
   · 미리보기는 두 flat 모두 **실제 배치 방향**(wafer 가 horizontal 일 때)으로 그린다.
     R/L vertical 모두 shot 을 돌리지 않고, V TEG 만 서 있는 모양으로 나온다
   · ⚙️ 설정의 TEG(module)별 오프셋도 되돌려 반영하고, 적용 여부를 열로 표시
   · 설비 원문은 만들지 않는다 — 각 TEG 가 어떤 상대좌표가 되는지 **표로만** 본다
*/
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { sf } from "../../lib/api";
import { toast } from "../../components/Toast";
import ZoomPanSvg from "../../components/ZoomPanSvg";
import { Button, Card, DataTable, EmptyState, Pill } from "../../components/UXKit";

const API = "/api/teg-map";
const PCHK_COLOR = "#dc2626";
const TEG_COLOR = "#2563eb";
const SHOT_COLOR = "#64748b";
const DIE_COLOR = "#2f9e63";
const PREVIEW_SIZE = 380;
// TEG 는 shot 대비 아주 작아 기본 배율(×12)로는 이름이 안 읽힌다 —
// Mapfile 체크의 shot 확대와 같은 ×60 까지 연다.
const PREVIEW_MAX_ZOOM = 60;
const CELL_SOURCE_LABEL = { grid: "칩 격자", image: "그림 die", dev_grid: "개발 격자 die" };

function fmtN(v, d = 12) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  const n = Number(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(d).replace(/0+$/, "").replace(/\.$/, "");
}

function _token() {
  try { return JSON.parse(localStorage.getItem("hol_user") || "{}").token || ""; }
  catch (_) { return ""; }
}

/* vehicle 그림 — 그림 모드일 때 미리보기 배경으로 깐다. 실패하면 조용히 없이 그린다. */
async function fetchImageBlobUrl(vehicle) {
  try {
    const res = await fetch(`${API}/image?vehicle=${encodeURIComponent(vehicle)}`,
      { headers: { "X-Session-Token": _token() } });
    if (!res.ok) return null;
    return URL.createObjectURL(await res.blob());
  } catch (_) { return null; }
}

function downloadText(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ── flat 별 미리보기 — 기준 PCHK=(0,0) 인 **실제 배치 방향** 그림.
   서버가 rect/shot 을 회전 없는 좌표(wafer horizontal 기준)로 주므로 두 flat 이
   같은 방향으로 보이고, direction=V 인 TEG 만 서 있는 사각형으로 그려진다. ── */
function FlatPreview({ block, imgUrl }) {
  const rects = useMemo(
    () => [
      ...(block.pchk?.rect ? [{ ...block.pchk.rect, name: block.pchk.teg, pchk: true }] : []),
      ...(block.rows || []).filter(r => r.rect).map(r => ({ ...r.rect, name: r.teg, point: r.first_pad_point })),
    ], [block.rows, block.pchk]);
  const cells = block.cells || [];
  const bounds = useMemo(() => {
    const xs = [0], ys = [0];                       // 기준 PCHK 원점은 항상 포함
    const sh = block.shot;
    if (sh) {
      xs.push(sh.cx - sh.w / 2, sh.cx + sh.w / 2);
      ys.push(sh.cy - sh.h / 2, sh.cy + sh.h / 2);
    }
    rects.forEach(r => {
      xs.push(r.x, r.x + r.w); ys.push(r.y, r.y + r.h);
      if (r.point) { xs.push(r.point.x); ys.push(r.point.y); }
    });
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const span = Math.max(x1 - x0, y1 - y0, 1);
    const pad = span * 0.08;
    return { x0: x0 - pad, x1: x1 + pad, y0: y0 - pad, y1: y1 + pad, span: span + pad * 2 };
  }, [rects, block.shot]);

  const s = PREVIEW_SIZE / bounds.span;
  const ox = (PREVIEW_SIZE - (bounds.x1 - bounds.x0) * s) / 2;
  const oy = (PREVIEW_SIZE - (bounds.y1 - bounds.y0) * s) / 2;
  const toX = (v) => ox + (v - bounds.x0) * s;
  const toY = (v) => oy + (bounds.y1 - v) * s;    // y 위쪽이 +
  const sh = block.shot;

  return (
    <ZoomPanSvg size={PREVIEW_SIZE} maxZoom={PREVIEW_MAX_ZOOM}>
      {(zoom) => (
        <>
          {/* 그림 모드 — 붙여넣은 그림을 shot 사각형에 맞춰 깐다 (미리보기는 회전이 없어 1:1) */}
          {sh && imgUrl && (
            <image href={imgUrl} x={toX(sh.cx - sh.w / 2)} y={toY(sh.cy + sh.h / 2)}
              width={sh.w * s} height={sh.h * s} preserveAspectRatio="none" opacity="0.9" />
          )}
          {sh && (
            <rect x={toX(sh.cx - sh.w / 2)} y={toY(sh.cy + sh.h / 2)}
              width={sh.w * s} height={sh.h * s} fill={imgUrl ? "none" : "rgba(128,128,128,0.05)"}
              stroke={SHOT_COLOR} strokeWidth={1 / zoom} strokeDasharray={`${4 / zoom} ${3 / zoom}`} />
          )}
          {/* die 셀 — ⚙️ 설정의 shot 표시 방식(칩 격자 / 그림에서 인식한 사각형 /
              개발 격자)에서 온다. Mapfile 체크가 die 겹침 판정에 쓰는 바로 그 영역이라
              여기서도 같은 걸 그려 TEG 가 스크라이브에 있는지 눈으로 본다. */}
          {cells.map((c, i) => (
            <rect key={`die${i}`} x={toX(c.x)} y={toY(c.y + c.h)}
              width={Math.max(0.5 / zoom, c.w * s)} height={Math.max(0.5 / zoom, c.h * s)}
              fill="rgba(47,158,99,0.08)" stroke={DIE_COLOR} strokeWidth={0.9 / zoom}
              opacity="0.85" />
          ))}
          {/* TEG 사각형 — 좌하단 기준, 실제 배치 방향 (V 는 서 있는 모양).
              Mapfile 체크의 shot 확대와 같은 표기: 검은 테두리 + 사각형 가운데 이름.
              이름 크기는 사각형에 맞추므로 확대할수록 커진다. */}
          {rects.map((r, i) => {
            const w = Math.max(1.2 / zoom, r.w * s), h = Math.max(1.2 / zoom, r.h * s);
            const x = toX(r.x), yTop = toY(r.y + r.h);
            const nm = String(r.name || "");
            const fs = Math.min(h * 0.62, (w * 0.92) / Math.max(1, nm.length * 0.58));
            return (
              <g key={i}>
                <rect x={x} y={yTop} width={w} height={h}
                  fill={r.pchk ? "rgba(220,38,38,0.10)" : "rgba(37,99,235,0.10)"}
                  stroke={r.pchk ? PCHK_COLOR : "#111827"} strokeWidth={1.4 / zoom} />
                {nm && fs * zoom >= 2.5 && (
                  <text x={x + w / 2} y={yTop + h / 2} fontSize={fs} textAnchor="middle"
                    dominantBaseline="central" fill="#111827" fontWeight={700}>{nm}</text>
                )}
                {r.point && <circle cx={toX(r.point.x)} cy={toY(r.point.y)} r={3 / zoom}
                  fill="#f59e0b" stroke="#92400e" strokeWidth={1 / zoom} />}
              </g>
            );
          })}
          {/* 기준 PCHK = (0, 0) */}
          <g>
            <line x1={toX(0) - 8 / zoom} y1={toY(0)} x2={toX(0) + 8 / zoom} y2={toY(0)}
              stroke={PCHK_COLOR} strokeWidth={1.4 / zoom} />
            <line x1={toX(0)} y1={toY(0) - 8 / zoom} x2={toX(0)} y2={toY(0) + 8 / zoom}
              stroke={PCHK_COLOR} strokeWidth={1.4 / zoom} />
            <circle cx={toX(0)} cy={toY(0)} r={3 / zoom} fill="none" stroke={PCHK_COLOR}
              strokeWidth={1.2 / zoom} />
            <text x={toX(0) + 10 / zoom} y={toY(0) - 6 / zoom} fontSize={11 / zoom}
              fill={PCHK_COLOR} fontWeight="700">{block.pchk?.teg} first pad (0,0)</text>
          </g>
        </>
      )}
    </ZoomPanSvg>
  );
}

/* ── flat 하나(Horizontal 또는 Vertical(R)) 좌표표 + 미리보기 ── */
function FlatBlock({ block, vehicle, scale, imgUrl }) {
  const rows = block.rows || [];
  const vertical = ["v_R", "v_L"].includes(block.flat);
  const flatToken = block.flat === "v_R" ? "VR" : block.flat === "v_L" ? "VL" : "H";
  // 기준 PCHK 을 표 첫 행으로 함께 보여준다 — 이 행이 (0,0) 이라는 게 표의 기준이다.
  const pchkRow = block.pchk && {
    teg: block.pchk.teg, direction: block.pchk.direction,
    ebeam_x: block.pchk.ebeam_x, ebeam_y: block.pchk.ebeam_y,
    x: 0, y: 0, offset_applied: false, top_cell: "", _pchk: true,
    coordinate_terms: block.coordinate_terms,
  };
  const viewRows = pchkRow ? [pchkRow, ...rows] : rows;
  const cols = [
    { key: "teg", label: "TEG", render: r => r._pchk
        ? <b style={{ color: PCHK_COLOR }}>{r.teg} <span style={{ fontWeight: 400 }}>(기준)</span></b>
        : r.teg },
    { key: "direction", label: "방향", width: 52,
      render: r => r.direction === "v_L" ? "V(L)" : r.direction === "v" ? "V(R)" : "H" },
    { key: "ebeam", label: "DB Ebeam (x, y)", align: "right",
      render: r => `(${fmtN(r.ebeam_x)}, ${fmtN(r.ebeam_y)})` },
    { key: "offset", label: "오프셋 적용", render: r => r._pchk
        ? <span style={{ color: "var(--muted)" }}>기준점</span>
        : r.offset_applied
          ? <span style={{ color: "var(--warn)", fontWeight: 700 }}
              title={r.offset_note || "TEG(module)별 오프셋 — ⚙️ 설정에서 편집"}>
              적용 ({fmtN(r.offset_dx)}, {fmtN(r.offset_dy)})
            </span>
          : <span style={{ color: "var(--muted)" }}>-</span> },
    { key: "map", label: "Mapfile (x, y)", align: "right",
      render: r => <b style={r._pchk ? { color: PCHK_COLOR } : undefined}>
        ({fmtN(r.x)}, {fmtN(r.y)})
      </b> },
    { key: "top_cell", label: "top_cell", render: r => r.top_cell || "" },
  ];

  // 표 그대로 내보내기 — 설비 원문이 아니라 좌표표다 (엑셀 붙여넣기용 TSV / CSV).
  const HEAD = ["TEG", "방향", "DB Ebeam X", "DB Ebeam Y", "오프셋 적용",
                "오프셋 X", "오프셋 Y", "Mapfile X", "Mapfile Y", "top_cell"];
  const tableRows = () => [
    [block.pchk?.teg || block.base.ref_name, flatToken,
     block.pchk?.ebeam_x, block.pchk?.ebeam_y, "기준 PCHK", "", "", 0, 0, ""],
    ...rows.map(r => [r.teg, r.direction === "v_L" ? "V(L)" : r.direction === "v" ? "V(R)" : "H", r.ebeam_x, r.ebeam_y,
      r.offset_applied ? "적용" : "", r.offset_applied ? r.offset_dx : "",
      r.offset_applied ? r.offset_dy : "", r.x, r.y, r.top_cell || ""]),
  ];
  const asText = (sep) => [HEAD, ...tableRows()]
    .map(cols => cols.map(v => (v === null || v === undefined ? "" : String(v))).join(sep))
    .join("\n");
  const fileName = `${vehicle || "vehicle"}_${flatToken}_mapfile_coords.csv`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(asText("\t"));
      toast.ok(`${block.label} 표를 복사했습니다 (엑셀에 그대로 붙여넣기)`);
    } catch (e) { toast.error("복사 실패 — 표를 직접 선택해 복사하세요"); }
  };

  return (
    <Card title={`${block.label} — 기준 ${block.pchk?.teg || block.base.ref_name}`}
      right={
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <Pill tone={rows.length ? "ok" : "warn"} size="sm">TEG {rows.length}</Pill>
          {block.offset_count > 0 && (
            <Pill tone="warn" size="sm" title="TEG(module)별 오프셋이 반영된 행 수">
              오프셋 {block.offset_count}
            </Pill>
          )}
          <Button onClick={copy} disabled={!rows.length}>표 복사</Button>
          <Button onClick={() => downloadText(fileName, asText(","))} disabled={!rows.length}>
            CSV 다운로드
          </Button>
        </div>
      }>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
        기준 <b style={{ color: PCHK_COLOR }}>{block.pchk?.teg}</b> DB Ebeam
        ({fmtN(block.pchk?.ebeam_x)}, {fmtN(block.pchk?.ebeam_y)}) → Mapfile <b>(0, 0)</b>
        {" · "}
        {block.base.source === "db"
          ? <span style={{ color: "var(--ok)" }}>정답지 PCHK 기준</span>
          : <span style={{ color: "var(--warn)" }}>⚙️ 설정 기본 오프셋 기준</span>}
        {vertical && <> · 미리보기는 <b>실제 배치 방향</b>(회전 전)이고 V TEG 는 서 있는 모양,
          표의 좌표는 회전된 설비값입니다</>}
      </div>
      {block.coordinate_terms && <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8,
        padding: 8, border: "1px solid var(--line)", borderRadius: 6 }}>
        <b>적용식</b> p = R⁻¹·(Otarget − Obase − Cproduct − Kglobal − Kproduct)<br />
        Obase={JSON.stringify(block.coordinate_terms.global_base)} · Cproduct={JSON.stringify(block.coordinate_terms.product_flat)}
      </div>}
      {block.warning && (
        <div style={{ fontSize: 12, color: "var(--warn)", marginBottom: 8 }}>⚠ {block.warning}</div>
      )}
      {block.pchk_in_shot === false && (
        <div style={{ fontSize: 12, color: "var(--danger)", marginBottom: 8, fontWeight: 600 }}>
          ⚠ 기준 {block.pchk?.teg} 이(가) shot 밖입니다 — 설비가 찍는 점이라 shot 안에 있어야 정상입니다.
          정답지의 ebeam 좌표나 shot 크기(제품 입력값 또는 Chip_Radius fallback)를 확인하세요.
          이 값이 틀리면 아래 좌표가 통째로 밀립니다.
        </div>
      )}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div>
          <FlatPreview block={block} imgUrl={imgUrl} />
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, maxWidth: PREVIEW_SIZE }}>
            <span style={{ color: PCHK_COLOR, fontWeight: 700 }}>＋</span> 기준 PCHK (0,0) ·
            <span style={{ color: TEG_COLOR, fontWeight: 700 }}> ▪</span> TEG (검은 테두리,
            이름은 사각형 가운데 — 확대하면 읽힙니다) ·
            {imgUrl && <> <span style={{ fontWeight: 700 }}>🖼</span> 그림 ·</>}
            <span style={{ color: DIE_COLOR, fontWeight: 700 }}> ▢</span>
            {" "}{CELL_SOURCE_LABEL[block.cell_source] || "die"} {(block.cells || []).length} ·
            <span style={{ color: SHOT_COLOR, fontWeight: 700 }}> ▭</span> shot
            {vertical && " (실제 배치 방향)"}
            {block.shot ? ` ${fmtN(block.shot.w_mm, 2)}×${fmtN(block.shot.h_mm, 2)} mm` : " — shot 크기 미상"}
          </div>
        </div>
        <div style={{ flex: "1 1 460px", minWidth: 380 }}>
          {rows.length ? (
            <DataTable columns={cols} rows={viewRows} maxHeight={300}
              rowStyle={r => (r._pchk ? { background: "rgba(220,38,38,0.06)" } : {})} />
          ) : (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              이 방향(direction {vertical ? "V" : "H"})의 체크 대상 TEG 가 없습니다.
            </div>
          )}
          {(block.other_dir || []).length > 0 && (
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
              다른 방향이라 제외: {block.other_dir.map(t => t.teg).join(", ")}
              {" — Mapfile 은 flat 하나 기준이라 반대 방향 TEG 는 이 표의 대상이 아닙니다."}
            </div>
          )}
          {(block.skipped || []).length > 0 && (
            <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 4 }}>
              정답지에 없어 생성 못 함: {block.skipped.map(s => s.teg).join(", ")}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   MAIN 내부 TEG 좌표 생성
   MAIN(die 급 블록) 안의 TEG 는 정답지에 없어 좌표를 만들 근거가 없다. die 를
   기본 TEG 사이즈 격자로 나눠 자리를 만들고, 칸에 이름을 적으면 그 자리의
   Mapfile 상대좌표(기준 PCHK=(0,0))가 나온다. 여러 MAIN 을 동시에 다룬다.
   ══════════════════════════════════════════════════════════════════════════ */
const GRID_SIZE = 480;                 // MAIN 미리보기 SVG 크기
/* 격자 입력표 — 선으로 칸을 구분한다 (엑셀처럼 보이고, 붙여넣기도 엑셀 블록 그대로) */
const GRID_TH = { border: "1px solid var(--line)", background: "var(--bg-soft, rgba(128,128,128,0.08))",
                  fontSize: 10, color: "var(--muted)", fontWeight: 600, padding: "2px 4px",
                  textAlign: "center", position: "sticky", top: 0 };
const GRID_TD = { border: "1px solid var(--line)", padding: 0 };

/* 격자 칸 입력 — memo 로 감싸 칸 하나를 고쳐도 나머지가 리렌더되지 않게 한다
   (칸이 수백 개가 될 수 있다). 셀 테두리는 표(td)가 그리므로 input 은 테두리 없음.
   엑셀에서 복사한 블록을 붙여넣으면 이 칸을 좌상단으로 삼아 여러 칸이 채워진다. */
const GridCell = memo(function GridCell({ mainName, r, c, value, onChange, onPasteBlock }) {
  return (
    <input value={value || ""} spellCheck={false}
      onChange={e => onChange(mainName, r, c, e.target.value)}
      onPaste={e => onPasteBlock(e, mainName, r, c)}
      title={`행 ${r + 1} · 열 ${c + 1} — 엑셀 블록 붙여넣기 가능`}
      style={{ width: "100%", minWidth: 58, boxSizing: "border-box", background: "transparent",
               fontFamily: "monospace", fontSize: 11, padding: "3px 4px",
               border: "none", outline: "none",
               fontWeight: value ? 700 : 400, textAlign: "center" }} />
  );
});

/* MAIN die + 격자 미리보기 — TEG 는 검정 네모 테두리, 이름은 사각형 가운데.
   die 좌하단이 원점이고 y 는 위가 +. 확대/패닝은 공용 ZoomPanSvg. */
function MainGridPreview({ main, names }) {
  const pad = 0.06;
  const span = Math.max(main.w, main.h) * (1 + pad * 2);
  const s = GRID_SIZE / span;
  const ox = (GRID_SIZE - main.w * s) / 2;
  const oy = (GRID_SIZE - main.h * s) / 2;
  // 셀 mm(절대) → die 로컬 mm → 화면 px. y 는 위가 + 라 뒤집는다.
  const toX = (mm) => ox + (mm - main.x) * s;
  const toY = (mm) => oy + (main.h - (mm - main.y)) * s;
  const cw = main.cell_w * s, ch = main.cell_h * s;

  return (
    <ZoomPanSvg size={GRID_SIZE} maxZoom={PREVIEW_MAX_ZOOM}>
      {(zoom) => (
        <>
          {/* die 외곽 */}
          <rect x={ox} y={oy} width={main.w * s} height={main.h * s}
            fill="rgba(47,158,99,0.06)" stroke={DIE_COLOR} strokeWidth={1.4 / zoom} />
          <text x={ox + 3 / zoom} y={oy - 4 / zoom} fontSize={11 / zoom}
            fill={DIE_COLOR} fontWeight={700}>
            {main.name} · {fmtN(main.w, 3)}×{fmtN(main.h, 3)} mm
          </text>
          {main.cells.map(cell => {
            const nm = names[`${cell.r},${cell.c}`] || "";
            const x = toX(cell.mm_x), yTop = toY(cell.mm_y) - ch;
            const fs = Math.min(ch * 0.62, (cw * 0.92) / Math.max(1, nm.length * 0.58));
            return (
              <g key={`${cell.r},${cell.c}`}>
                <rect x={x} y={yTop} width={Math.max(0.6 / zoom, cw)}
                  height={Math.max(0.6 / zoom, ch)}
                  fill={nm ? "rgba(17,24,39,0.06)" : "none"} stroke="#111827"
                  strokeWidth={(nm ? 1.4 : 0.5) / zoom}
                  strokeDasharray={nm ? undefined : `${2 / zoom} ${2 / zoom}`} />
                {nm && fs * zoom >= 2.5 && (
                  <text x={x + cw / 2} y={yTop + ch / 2} fontSize={fs}
                    textAnchor="middle" dominantBaseline="central"
                    fill="#111827" fontWeight={700}>{nm}</text>
                )}
              </g>
            );
          })}
        </>
      )}
    </ZoomPanSvg>
  );
}

/* MAIN 하나 — 격자 입력 + 미리보기 + 이름을 적은 칸의 좌표표 */
function MainGridBlock({ main, flats, names, onChange, onClear, onPasteBlock }) {
  const named = useMemo(
    () => main.cells
      .map(c => ({ ...c, name: (names[`${c.r},${c.c}`] || "").trim() }))
      .filter(c => c.name),
    [main.cells, names]);
  const byRow = useMemo(() => {
    const m = [];
    main.cells.forEach(c => { (m[c.r] = m[c.r] || [])[c.c] = c; });
    return m;
  }, [main.cells]);

  const HEAD = ["TEG", "행", "열", "DB Ebeam X", "DB Ebeam Y", "Mapfile H X", "Mapfile H Y"];
  const asText = (sep) => [HEAD, ...named.map(c => [
    c.name, c.r + 1, c.c + 1, c.x, c.y, c.h.x, c.h.y,
  ])].map(cols => cols.map(v => (v === null || v === undefined ? "" : String(v))).join(sep))
    .join("\n");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(asText("\t"));
      toast.ok(`${main.name} 내부 TEG ${named.length}개를 복사했습니다`);
    } catch (_) { toast.error("복사 실패 — 표를 직접 선택해 복사하세요"); }
  };

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 10 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                    marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontFamily: "monospace" }}>{main.name}</span>
        <Pill tone="neutral" size="sm">
          die {fmtN(main.w, 3)}×{fmtN(main.h, 3)} mm
        </Pill>
        <Pill tone="neutral" size="sm">
          격자 {main.cols}열 × {main.rows}행 (칸 {fmtN(main.cell_w, 3)}×{fmtN(main.cell_h, 3)} mm)
        </Pill>
        <Pill tone={named.length ? "ok" : "neutral"} size="sm">이름 {named.length}</Pill>
        {main.exact ? (
          <Pill tone="ok" size="sm" title="die 크기가 칸으로 딱 나눠집니다">딱 맞음</Pill>
        ) : (
          <Pill tone="warn" size="sm"
            title="남는 길이 — TEG 사이 거리(gap)를 조절해 0 에 맞추세요">
            남음 X {fmtN(main.remainder_x, 3)} · Y {fmtN(main.remainder_y, 3)} mm
          </Pill>
        )}
        {main.truncated && (
          <Pill tone="danger" size="sm">칸이 너무 많아 잘랐습니다 (상한 초과)</Pill>
        )}
        {!main.cells.length && (
          <Pill tone="danger" size="sm"
            title="die 가 기본 TEG 사이즈보다 작거나 TEG 사이 거리가 너무 큽니다">
            들어가는 칸이 없습니다
          </Pill>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <Button onClick={() => onClear(main.name)} disabled={!named.length}>이름 지우기</Button>
          <Button onClick={copy} disabled={!named.length}>표 복사</Button>
          <Button disabled={!named.length}
            onClick={() => downloadText(`${main.name}_inner_teg.csv`, asText(","))}>
            CSV 다운로드
          </Button>
        </span>
      </div>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div>
          <MainGridPreview main={main} names={names} />
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4,
                        maxWidth: GRID_SIZE }}>
            <span style={{ color: DIE_COLOR, fontWeight: 700 }}>▢</span> MAIN die ·
            <span style={{ fontWeight: 700 }}> ▪</span> TEG 자리(검정 테두리, 이름을 적으면
            사각형 가운데에 표시) · 휠/드래그로 확대·이동
          </div>
        </div>

        <div style={{ flex: "1 1 420px", minWidth: 320 }}>
          {/* 격자 입력 — 화면 위가 die 위쪽이 되도록 행을 역순으로 그린다.
              선으로 구분된 표이고, 엑셀에서 복사한 블록을 그대로 붙여넣을 수 있다. */}
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
            칸에 TEG 이름을 적으면 아래 표와 그림에 좌표가 함께 나옵니다 (아래쪽 행 = die 아래쪽).
            <b> 엑셀에서 복사한 블록을 칸에 붙여넣으면</b> 그 칸을 좌상단으로 여러 칸이 한 번에 채워집니다.
          </div>
          <div style={{ overflow: "auto", maxHeight: 260, border: "1px solid var(--line)",
                        borderRadius: 6 }}>
            <table style={{ borderCollapse: "collapse", width: "100%" }}>
              <thead>
                <tr>
                  <th style={GRID_TH}></th>
                  {Array.from({ length: main.cols }, (_, c) => (
                    <th key={c} style={GRID_TH}>{c + 1}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {byRow.slice().reverse().map((row, i) => {
                  const r = byRow.length - 1 - i;
                  return (
                    <tr key={r}>
                      <th style={GRID_TH}>{r + 1}</th>
                      {(row || []).map(cell => (
                        <td key={`${cell.r},${cell.c}`}
                          style={{ ...GRID_TD,
                                   background: names[`${cell.r},${cell.c}`]
                                     ? "rgba(17,24,39,0.05)" : "transparent" }}>
                          <GridCell mainName={main.name} r={cell.r} c={cell.c}
                            value={names[`${cell.r},${cell.c}`]}
                            onChange={onChange} onPasteBlock={onPasteBlock} />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {named.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <DataTable maxHeight={220} rows={named}
                columns={[
                  { key: "name", label: "TEG" },
                  { key: "pos", label: "행·열", width: 70,
                    render: c => `${c.r + 1} · ${c.c + 1}` },
                  { key: "eb", label: "DB Ebeam (x, y)", align: "right",
                    render: c => `(${fmtN(c.x)}, ${fmtN(c.y)})` },
                  { key: "mh", label: `Mapfile H — 기준 ${flats.h.ref_name}`, align: "right",
                    render: c => <b>({fmtN(c.h.x)}, {fmtN(c.h.y)})</b> },
                ]} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MainGridCard({ vehicle, refreshKey = 0 }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [picked, setPicked] = useState([]);        // 선택된 MAIN 이름들
  const [typed, setTyped] = useState("");          // 직접 입력 (쉼표 구분)
  const [gapX, setGapX] = useState("0");
  const [gapY, setGapY] = useState("0");
  // {MAIN 이름: {"r,c": TEG 이름}} — 사용자가 적은 이름
  const [names, setNames] = useState({});

  const wanted = useMemo(() => {
    const extra = typed.split(",").map(t => t.trim()).filter(Boolean);
    return [...new Set([...picked, ...extra])];
  }, [picked, typed]);

  const load = useCallback(async (mains) => {
    if (!vehicle) { setData(null); return; }
    setBusy(true); setErr("");
    try {
      const r = await sf(`${API}/main-grid?vehicle=${encodeURIComponent(vehicle)}`
        + `&mains=${encodeURIComponent((mains || []).join(","))}`
        + `&gap_x=${Number(gapX) || 0}&gap_y=${Number(gapY) || 0}`);
      setData(r);
    } catch (e) {
      setData(null);
      setErr(e?.status === 404
        ? "백엔드에 이 API 가 아직 없습니다 (/api/teg-map/main-grid). 서버를 재시작해 주세요."
        : String(e.message || e));
    } finally { setBusy(false); }
  }, [vehicle, gapX, gapY]);

  // vehicle 이 바뀌면 선택 가능한 MAIN 목록만 먼저 받아 둔다
  useEffect(() => { setPicked([]); setTyped(""); setNames({}); load([]); }, [vehicle]);   // eslint-disable-line react-hooks/exhaustive-deps
  // 제품 형상/config 저장은 vehicle 문자열을 바꾸지 않는다. 부모가 refreshKey 를
  // 올리면 현재 선택과 입력은 보존한 채 새 기준값으로 격자만 다시 계산한다.
  useEffect(() => {
    if (refreshKey > 0) load(wanted);
  }, [refreshKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  const onChange = useCallback((mainName, r, c, value) => {
    setNames(prev => ({ ...prev, [mainName]: { ...(prev[mainName] || {}), [`${r},${c}`]: value } }));
  }, []);
  const onClear = useCallback((mainName) => {
    setNames(prev => ({ ...prev, [mainName]: {} }));
  }, []);
  /* 엑셀 블록 붙여넣기 — 클립보드의 TSV 를 붙여넣은 칸 기준으로 펼친다.
     표는 위가 die 위쪽(행 번호 큰 쪽)이라, 엑셀의 아래 줄은 행 번호가 하나씩 줄어든다.
     칸 하나짜리(구분자 없는) 붙여넣기는 브라우저 기본 동작에 맡긴다. */
  const onPasteBlock = useCallback((e, mainName, r0, c0) => {
    const text = e.clipboardData?.getData("text/plain") ?? "";
    if (!/[\t\r\n]/.test(text)) return;
    e.preventDefault();
    const lines = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n");
    setNames(prev => {
      const cur = { ...(prev[mainName] || {}) };
      lines.forEach((line, i) => {
        line.split("\t").forEach((v, j) => {
          const r = r0 - i, c = c0 + j;      // 화면 아래로 갈수록 행 번호가 줄어든다
          if (r < 0 || c < 0) return;
          cur[`${r},${c}`] = v.trim();
        });
      });
      return { ...prev, [mainName]: cur };
    });
  }, []);

  // 선택 목록은 이름 오름차순 (MAIN01, MAIN02, … — 숫자도 자연스럽게 정렬)
  const available = useMemo(
    () => [...(data?.available || [])].sort((a, b) =>
      String(a.name).localeCompare(String(b.name), undefined, { numeric: true })),
    [data]);
  const toggle = (name) => setPicked(p =>
    p.includes(name) ? p.filter(x => x !== name) : [...p, name]);

  return (
    <Card title="MAIN 내부 TEG 좌표 생성"
      right={
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <Pill tone={wanted.length ? "ok" : "neutral"} size="sm">MAIN {wanted.length}</Pill>
          <Button variant="primary" disabled={busy || !vehicle} onClick={() => load(wanted)}>
            {busy ? "생성 중…" : "격자 만들기"}
          </Button>
        </div>
      }>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
        MAIN(die 급 블록) 안의 TEG 는 정답지에 없어 좌표를 만들 근거가 없습니다. die 를
        <b> 기본 TEG 사이즈</b>({data ? `${fmtN(data.teg.w, 3)}×${fmtN(data.teg.h, 3)} mm` : "⚙️ 설정"})
        로 x·y 각각 나눠 자리를 만들고, 칸에 이름을 적으면 그 자리의 Mapfile 상대좌표(Horizontal 기준)가
        나옵니다. 딱 떨어지지 않으면 <b>TEG 사이 거리</b>를 넣어 맞추세요. 여러 MAIN 을 동시에 다룰 수 있습니다.
        <br />※ 이 좌표에는 ⚙️ 설정의 TEG(module)별 오프셋이 적용되지 않습니다 — 이름을 나중에
        붙이는 자리라 규칙을 미리 고를 수 없습니다.
      </div>

      {/* MAIN 선택 — 이름 오름차순 목록에서 여러 개를 체크한다 (스크롤) */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap",
                    marginBottom: 8 }}>
        <div style={{ minWidth: 220 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>MAIN 선택</span>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              {available.length}개 · 이름순 · 여러 개 선택 가능
            </span>
            {picked.length > 0 && (
              <button onClick={() => setPicked([])}
                style={{ fontSize: 11, color: "var(--accent)", background: "none",
                         border: "1px solid var(--line)", borderRadius: 4, cursor: "pointer",
                         padding: "0 5px" }}>선택 해제</button>
            )}
          </div>
          {available.length ? (
            <div style={{ border: "1px solid var(--line)", borderRadius: 6, maxHeight: 168,
                          overflowY: "auto" }}>
              {available.map(a => (
                <label key={a.name}
                  title={a.sized ? "" : "Main_chip_info.csv 에 이 MAIN 의 chip 크기가 없어 격자를 만들 수 없습니다"}
                  style={{ display: "flex", alignItems: "center", gap: 6, padding: "3px 8px",
                           borderBottom: "1px solid var(--line)", fontSize: 12,
                           fontFamily: "monospace", opacity: a.sized ? 1 : 0.45,
                           cursor: a.sized ? "pointer" : "not-allowed" }}>
                  <input type="checkbox" disabled={!a.sized}
                    checked={picked.includes(a.name)}
                    onChange={() => toggle(a.name)} />
                  <span style={{ fontWeight: picked.includes(a.name) ? 700 : 400 }}>{a.name}</span>
                  {!a.sized && (
                    <span style={{ marginLeft: "auto", fontSize: 10, fontFamily: "inherit" }}>
                      크기 없음
                    </span>
                  )}
                </label>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              MAIN 으로 이름 붙은 TEG 를 찾지 못했습니다 — 오른쪽에 직접 입력해 보세요
            </div>
          )}
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          <span style={{ fontWeight: 700 }}>직접 입력</span>
          <input value={typed} onChange={e => setTyped(e.target.value)} spellCheck={false}
            placeholder="쉼표로 여러 개"
            style={{ width: 200, fontFamily: "monospace", fontSize: 12 }} />
        </label>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
                    marginBottom: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>TEG 사이 거리 (mm)</span>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
          X <input type="number" step="0.01" min="0" value={gapX}
            onChange={e => setGapX(e.target.value)} style={{ width: 84 }} />
        </label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
          Y <input type="number" step="0.01" min="0" value={gapY}
            onChange={e => setGapY(e.target.value)} style={{ width: 84 }} />
        </label>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          칸 간격 = 기본 TEG 사이즈 + 이 거리. 바꾼 뒤 "격자 만들기" 를 누르세요.
        </span>
      </div>

      {err && <div style={{ fontSize: 12, color: "var(--danger)", marginBottom: 8 }}>⚠ {err}</div>}
      {data && !data.ref_ok && (
        <div style={{ fontSize: 12, color: "var(--warn)", marginBottom: 8 }}>
          ⚠ 정답지를 읽지 못해 기준 PCHK 대신 ⚙️ 설정 오프셋을 씁니다 — {data.ref_error}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {(data?.mains || []).map(m => m.found ? (
          <MainGridBlock key={m.name}
            main={{ ...m, cell_w: data.teg.w, cell_h: data.teg.h }}
            flats={data.flats} names={names[m.name] || {}}
            onChange={onChange} onClear={onClear} onPasteBlock={onPasteBlock} />
        ) : (
          <div key={m.name} style={{ fontSize: 12, color: "var(--danger)",
                                     border: "1px solid var(--line)", borderRadius: 6,
                                     padding: "6px 10px" }}>
            <b style={{ fontFamily: "monospace" }}>{m.name}</b> — {m.error}
          </div>
        ))}
      </div>
      {data && !(data.mains || []).length && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>
          MAIN 을 고르고 "격자 만들기" 를 누르세요.
        </div>
      )}
    </Card>
  );
}

export default function TegGenerate({ vehicle, refreshKey = 0 }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [includeAll, setIncludeAll] = useState(false);
  const [imgUrl, setImgUrl] = useState(null);

  const load = useCallback(async () => {
    if (!vehicle) { setRes(null); return; }
    setBusy(true); setErr("");
    try {
      const r = await sf(`${API}/generate?vehicle=${encodeURIComponent(vehicle)}`
        + `&include_all=${includeAll ? "true" : "false"}`);
      setRes(r);
    } catch (e) {
      setRes(null);
      // 백엔드가 예전 프로세스면 이 라우트가 없어 404 "API not found" 가 온다 —
      // uvicorn 은 자동 재시작이 아니라서 흔한 상황이라 바로 알려 준다.
      setErr(e?.status === 404
        ? "백엔드에 이 API 가 아직 없습니다 (/api/teg-map/generate → API not found). "
          + "서버(uvicorn/python app.py) 프로세스를 재시작해 주세요. "
          + "재시작 후에도 같으면 backend/routers/teg_map.py 가 최신인지 확인하세요."
        : String(e.message || e));
    }
    finally { setBusy(false); }
  }, [vehicle, includeAll, refreshKey]);
  useEffect(() => { load(); }, [load]);

  // 그림 모드 vehicle 이면 미리보기 배경용 그림을 받아 둔다 (blob URL 은 정리)
  useEffect(() => {
    let dead = false, url = null;
    setImgUrl(null);
    if (res?.display?.mode === "image" && res.display.has_image && res.vehicle) {
      fetchImageBlobUrl(res.vehicle).then(u => {
        if (dead) { if (u) URL.revokeObjectURL(u); return; }
        url = u; setImgUrl(u);
      });
    }
    return () => { dead = true; if (url) URL.revokeObjectURL(url); };
  }, [res]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card title="Mapfile 좌표 생성 — 정답지 → 설비 좌표"
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Pill tone={vehicle ? "ok" : "warn"}>{vehicle || "vehicle 미선택"}</Pill>
            <Button onClick={load} disabled={busy || !vehicle}>{busy ? "생성 중…" : "다시 생성"}</Button>
          </div>
        }>
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
          Mapfile 검증 대상 TEG 가 <b>기준 PCHK = (0, 0)</b> 일 때 어떤 상대좌표가 되는지
          표로 보여줍니다. 셋업을 처음 올릴 때 설비 Mapfile 과 크로스체크하는 용도입니다.
          Horizontal / Vertical(R)은 기본 표로 나오고, Vertical(L)은 L 방향 데이터가 있을 때만 나옵니다.
          global 기준점과 제품별 ΔX/ΔY·TEG 오프셋을 반영해
          <b> 오프셋 적용</b> 열로 표시합니다.
          <br />※ Shot Size·Map offset(Odd)은 아래 shot 미리보기 형상에 사용됩니다.
          <b> Mapfile (x, y)는 DB Ebeam − 기준 PCHK − 제품별 ΔX/ΔY·TEG 보정</b>으로 계산됩니다.
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13,
                          cursor: "pointer" }}
            title="direction 이 다른 TEG 도 각 표에 넣습니다 (기본은 방향이 맞는 TEG 만)">
            <input type="checkbox" checked={includeAll}
              onChange={e => setIncludeAll(e.target.checked)} />
            다른 방향 TEG 도 포함
          </label>
          {res && (
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              대상 {res.targets.total}개 ({res.targets.source === "config" ? "지정 대상" : "기본(H_/V_)"})
              {res.geometry_source ? ` · shot 기준 ${res.geometry_source === "product_info" ? "저장 제품정보" : "Chip_Radius fallback"}` : ""}
              {res.ref_path ? ` · 정답지 ${res.ref_path}` : ""}
            </span>
          )}
        </div>
      </Card>

      {!vehicle && <EmptyState icon="📐" title="vehicle 을 선택하세요"
        hint="상단 제품 선택에서 vehicle 을 고르면 그 제품의 Mapfile 좌표표를 만듭니다" />}
      {err && <EmptyState icon="⚠" title="생성하지 못했습니다" hint={err} />}
      {res && !res.ref_ok && (
        <EmptyState icon="⚠" title="정답지를 읽지 못했습니다" hint={res.ref_error} />
      )}
      {res && res.ref_ok && res.targets.total === 0 && (
        <EmptyState icon="⚠" title="체크 대상 TEG 가 없습니다"
          hint="위치 조회 → TEG 목록 → 'Mapfile 검증 대상 TEG' 에서 지정하세요" />
      )}
      {res && res.ref_ok && (res.flats || []).map(b => (
        <FlatBlock key={b.flat} block={b} vehicle={res.vehicle} scale={res.scale}
          imgUrl={imgUrl} />
      ))}

      {vehicle && <MainGridCard vehicle={vehicle} refreshKey={refreshKey} />}
    </div>
  );
}
