/* My_Reformatize.jsx — 업무 > ET 다운로드.
   auto report 의 reformatize 흐름을 화면으로 제공:
   DB ET 제품 선택 → data_root/reformatter/<vehicle>_reformatter.csv 규칙으로
   shot 단위 index 값 계산 → 테이블 조회(페이지) + CSV 다운로드.
   톱니바퀴(⚙️)에서 한 번에 조회할 행 수 / 다운로드 최대 행을 설정.

   관리자 전용 🧪 ADDP 수식 테스트: vehicle CSV 를 고치기 전에 새 ADDP ITEM(alias)
   + ADDP Form 을 실제 ET 데이터로 계산해 보고 CSV 로 추출한다.
   수식은 기존 alias 와 raw item 을 {이름} 으로 참조 (auto report 와 동일). */
import { useEffect, useMemo, useRef, useState } from "react";
import { sf, postJson, dl, qs, responseDownloadFilename } from "../../lib/api";
import { toast } from "../../components/Toast";
import Loading from "../../components/Loading";
import PageGear from "../../components/PageGear";
import usePolling from "../../hooks/usePolling";
import { Banner, Button, EmptyState, PageShell, Pill } from "../../components/UXKit";
import { canManagePage } from "../../lib/permissions";
import { copyHistoryShareLink, historyIdFromLocation, historyShareUrl, setCachedShareBaseUrl } from "../../lib/historyShare";

const API = "/api/reformatize";

/* 다운로드 대기열 — 여러 사람이 동시에 걸면 서버가 한 건씩 처리한다.
   화면은 job 상태를 폴링해 "몇 번째 대기 / 지금 무슨 단계"를 계속 보여준다.
   (아무 표시 없이 멈춰 보이면 사용자가 다시 눌러 상황이 더 나빠진다) */
const DL_POLL_MS = 1200;
const DL_STEPS = ["대기", "parquet 읽기", "Index 계산", "CSV 생성"];

function dlStepIndex(job) {
  if (!job) return 0;
  if (job.state === "queued") return 0;
  const p = String(job.phase || "");
  // 순서 주의 — "규칙 CSV 확인 중"과 "대상 parquet 찾는 중"이 CSV/파일 키워드에
  // 먼저 걸려 읽기 단계인데도 'CSV 생성'으로 보이던 오표시를 막는다.
  if (p.includes("찾는 중") || p.includes("읽는 중") || p.includes("캐시") || p.includes("규칙")) return 1;
  if (p.includes("집계") || p.includes("결과 표") || p.includes("pivot")) return 2;
  if (p.includes("CSV") || p.includes("파일")) return 3;
  return 1;
}

function dlHeadline(job) {
  if (!job) return "다운로드 준비 중";
  if (job.state === "queued") {
    return job.ahead > 0
      ? `다운로드 대기 중 — 앞에 ${job.ahead}건`
      : "다운로드 대기 중 — 곧 시작합니다";
  }
  const pct = job.percent === null || job.percent === undefined ? "" : ` ${job.percent}%`;
  return (job.phase || "처리 중") + pct;
}

/* 조회 진행 — 조회도 오래 걸리는 구간은 다운로드와 같다(수백 개 parquet 중
   어디를 뒤지고 있나). /run 은 동기 POST 라 폴링할 job 이 없어서, 화면이 만든
   1회용 토큰으로 진행 상황만 따로 폴링한다. 폴링이 실패해도 조회 자체는
   계속된다 — 진행 표시가 없어질 뿐이다. */
const RUN_POLL_MS = 600;
const RUN_STEPS = ["규칙 확인", "parquet 검색", "parquet 읽기", "Index 계산"];

function runStepIndex(p) {
  const t = String(p?.phase || "");
  if (!t) return 0;
  if (t.includes("집계") || t.includes("결과 표") || t.includes("계산")) return 3;
  if (t.includes("읽는 중") || t.includes("캐시")) return 2;
  if (t.includes("찾는 중")) return 1;
  return 0;
}

/* "대상 parquet 찾는 중: PRODA_2025_07_23.parquet (3/120개)" 처럼 파일명이 붙은
   문구는 헤드라인이 길어진다. 앞부분만 헤드라인으로 쓰고 파일명·진행 수는
   아래 상세 줄로 내린다. */
function runHeadline(p) {
  const t = String(p?.phase || "").trim();
  if (!t) return "ET index 계산 중";
  const i = t.indexOf(": ");
  return i > 0 ? t.slice(0, i) : t;
}

function runDetail(p) {
  const t = String(p?.phase || "").trim();
  const i = t.indexOf(": ");
  return i > 0 ? t.slice(i + 2) : "";
}

/* 조회/다운로드 필터 — 서버 Filters 모델과 1:1.
   days(최근 N일)와 date_from/to(기간)는 상호 배타 — 한쪽 입력 시 다른 쪽을 비운다. */
const EMPTY_FILTERS = {
  lot_filter: "", step_filter: "", step_seq_filter: "", wafer_filter: "", site_cnt_filter: "", point_cnt_filter: "",
  days: "", date_from: "", date_to: "",
};

function filterBody(filters) {
  return {
    lot_filter: filters.lot_filter.trim(),
    step_filter: filters.step_filter.trim(),
    step_seq_filter: filters.step_seq_filter.trim(),
    wafer_filter: filters.wafer_filter.trim(),
    site_cnt_filter: filters.site_cnt_filter.trim(),
    point_cnt_filter: filters.point_cnt_filter.trim(),
    days: Number(filters.days) || 0,
    date_from: filters.date_from,
    date_to: filters.date_to,
  };
}

function hasAnyFilter(filters) {
  const f = filterBody(filters);
  return Boolean(f.lot_filter || f.step_filter || f.step_seq_filter || f.wafer_filter || f.site_cnt_filter || f.point_cnt_filter
    || f.days > 0 || f.date_from || f.date_to);
}

const cell = { padding: "5px 10px", borderBottom: "1px solid var(--border)", fontSize: 13, whiteSpace: "nowrap" };
const head = {
  ...cell, position: "sticky", top: 0, background: "var(--bg-tertiary)",
  color: "var(--text-secondary)", fontWeight: 700, zIndex: 1,
};
const inputStyle = {
  background: "var(--bg-primary)", color: "var(--text-primary)", border: "1px solid var(--border)",
  borderRadius: 6, padding: "6px 10px", fontSize: 13,
};

/* Index 항목 선택 표는 파일탐색기(Base 편집 그리드)와 같은 격자로 그린다 — 열이 11개라
   가로줄만 있으면 어느 값이 어느 열인지 눈으로 못 따라간다. borderCollapse:collapse 는
   sticky thead 의 테두리가 스크롤 시 사라져서 separate + borderSpacing:0 를 쓴다
   (파일탐색기 baseEditTable 과 동일한 이유). */
const gridTable = { width: "100%", borderCollapse: "separate", borderSpacing: 0 };
const gridCell = { ...cell, borderRight: "1px solid var(--border)" };
const gridHead = { ...head, borderRight: "1px solid var(--border)" };

function fmtVal(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Number.isInteger(n)) return String(n);
  return Math.abs(n) >= 1000 ? n.toFixed(1) : n.toPrecision(5).replace(/\.?0+$/, "");
}

// POST body 로 CSV 를 받아 저장 (테스트 다운로드용 — lib/api dl 은 GET 전용).
function dlPost(url, body, filename) {
  const tk = (() => { try { return JSON.parse(localStorage.getItem("hol_user") || "{}").token || ""; } catch (_) { return ""; } })();
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(tk ? { "X-Session-Token": tk } : {}) },
    body: JSON.stringify(body || {}),
  }).then(async (r) => {
    if (!r.ok) {
      let detail = "다운로드 실패 (HTTP " + r.status + ")";
      try { const b = await r.json(); detail = b.detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = responseDownloadFilename(r, filename || "download.csv");
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
}

/* 헤더 클릭 시 표시되는 index 규칙 상세 — 어떤 ADDP form 인지, 무엇을 참조했는지. */
function RuleInfoBar({ alias, rule, onSelect, onClose }) {
  if (!rule) return null;
  const isAddp = rule.category === "addp";
  const chip = (name) => (
    <span key={name} onClick={() => onSelect && onSelect(name)}
      title={onSelect ? "클릭하면 이 컬럼의 규칙 보기" : name}
      style={{ cursor: onSelect ? "pointer" : "default", fontFamily: "monospace", fontSize: 12, padding: "1px 7px", borderRadius: 999, border: "1px solid var(--accent)", color: "var(--accent)", marginRight: 4, display: "inline-block" }}>
      {"{" + name + "}"}
    </span>
  );
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", padding: "8px 12px", marginBottom: 8, borderRadius: 8, border: "1px solid var(--accent)", background: "var(--accent-glow)", fontSize: 13 }}>
      <Pill tone={isAddp ? "warn" : "accent"}>{isAddp ? "ADDP" : "REAL"}</Pill>
      <b style={{ fontFamily: "monospace" }}>{alias}</b>
      {isAddp ? (
        <>
          <span style={{ color: "var(--text-secondary)" }}>ADDP Form:</span>
          <code style={{ fontFamily: "monospace", background: "var(--bg-primary)", padding: "2px 8px", borderRadius: 5, border: "1px solid var(--border)" }}>{rule.addp_form || "-"}</code>
          {(rule.refs || []).length > 0 && (
            <span style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
              <span style={{ color: "var(--text-secondary)" }}>참조:</span>
              {(rule.refs || []).map(r => chip(r))}
            </span>
          )}
        </>
      ) : (
        <>
          <span style={{ color: "var(--text-secondary)" }}>raw ITEMID:</span>
          <code style={{ fontFamily: "monospace", background: "var(--bg-primary)", padding: "2px 8px", borderRadius: 5, border: "1px solid var(--border)" }}>{rule.itemid || "-"}</code>
          <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
            {rule.abs ? "abs " : ""}× {rule.scale ?? 1}
          </span>
        </>
      )}
      {(rule.unit || rule.speclow != null || rule.spechigh != null) && (
        <span style={{ color: "var(--text-secondary)", fontFamily: "monospace" }}>
          {rule.unit ? `[${rule.unit}] ` : ""}spec {rule.speclow ?? "-"} ~ {rule.spechigh ?? "-"}{rule.target != null ? ` (target ${rule.target})` : ""}
        </span>
      )}
      <span onClick={onClose} style={{ marginLeft: "auto", cursor: "pointer", color: "var(--text-secondary)", padding: "0 4px" }}>✕</span>
    </div>
  );
}

/* 의존성 트리 — ADDP 재귀 참조를 시각화 */
function DepTreeNode({ node, depth = 0 }) {
  const indent = depth * 20;
  if (!node) return null;
  const catColor = node.category === "addp" ? "#e67e22" : node.category === "real" ? "#27ae60" : "var(--text-secondary)";
  const label = node.derived_from ? `${node.alias} (← ${node.derived_from})` : node.alias;
  return (
    <>
      <div style={{ marginLeft: indent, padding: "2px 0", fontSize: 12, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: catColor, fontWeight: 700, minWidth: 40 }}>
          {node.category === "addp" ? "ADDP" : node.category === "real" ? "REAL" : node.derived_from ? "↳" : "REF"}
        </span>
        <span style={{ fontWeight: 600 }}>{label}</span>
        {node.category === "real" && (
          <span style={{ color: "var(--text-secondary)" }}>
            ITEMID={node.itemid} {node.absolute ? "abs " : ""}×{node.scale ?? 1}
          </span>
        )}
        {node.category === "addp" && node.addp_form && (
          <span style={{ color: "var(--text-secondary)", maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            = {node.addp_form}
          </span>
        )}
        {node.circular && <span style={{ color: "#e74c3c" }}>(순환 참조)</span>}
      </div>
      {(node.children || []).map((ch, i) => <DepTreeNode key={i} node={ch} depth={depth + 1} />)}
    </>
  );
}

function DepTreePanel({ tree }) {
  const [open, setOpen] = useState(false);
  if (!tree || !tree.length) return null;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", marginBottom: 8, background: "var(--bg-secondary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }} onClick={() => setOpen(o => !o)}>
        <span style={{ fontWeight: 700 }}>🌳 의존성 트리</span>
        <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>ADDP 재귀 참조 · raw data 의존 관계</span>
        <span style={{ marginLeft: "auto", color: "var(--text-secondary)" }}>{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 6, maxHeight: 260, overflow: "auto" }}>
          {tree.map((node, i) => <DepTreeNode key={i} node={node} />)}
        </div>
      )}
    </div>
  );
}

function ResultTable({ result, highlight }) {
  const hi = useMemo(() => new Set(highlight || []), [highlight]);
  const spec = result.spec || {};
  const roles = result.col_roles || {};
  const [selRule, setSelRule] = useState("");
  const clickable = (c) => (hi.has(c) || roles[c] === "dep" || roles[c] === "selected") && spec[c];

  const colColor = (c) => {
    const role = roles[c];
    if (role === "selected") return "var(--accent)";
    if (role === "dep") return "#e67e22";
    if (role === "raw") return "#27ae60";
    return head.color;
  };
  const cellColor = (c) => {
    const role = roles[c];
    if (role === "selected") return "var(--text-primary)";
    if (role === "dep") return "#e67e22";
    if (role === "raw") return "#27ae60";
    return "var(--text-secondary)";
  };
  const cellWeight = (c) => {
    const role = roles[c];
    if (role === "selected") return 700;
    if (role === "dep") return 600;
    return 400;
  };

  return (
    <>
      <RuleInfoBar alias={selRule} rule={spec[selRule]} onClose={() => setSelRule("")}
        onSelect={(name) => { if (spec[name]) setSelRule(name); }} />
      <div style={{ background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", overflow: "auto", maxHeight: "calc(100vh - 300px)" }}>
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead><tr>
          {(result.columns || []).map(c => (
            <th key={c}
              onClick={() => { if (clickable(c)) setSelRule(prev => prev === c ? "" : c); }}
              style={{
                ...head,
                color: colColor(c),
                cursor: clickable(c) ? "pointer" : "default",
                textDecoration: clickable(c) ? "underline dotted" : "none",
                background: selRule === c ? "var(--accent-glow)" : head.background,
              }}
              title={clickable(c)
                ? (spec[c].category === "addp"
                    ? `ADDP: ${spec[c].addp_form || ""} — 클릭하여 상세`
                    : `REAL: ${spec[c].itemid || ""}${spec[c].abs ? " abs" : ""} ×${spec[c].scale ?? 1} — 클릭하여 상세`)
                : roles[c] === "raw" ? `raw ITEMID 원본 값` : c}>
              {c === "shot_count" ? "PGM point 수" : c}
              {roles[c] === "raw" && <span style={{ fontSize: 9, opacity: 0.6 }}> (raw)</span>}
              {roles[c] === "dep" && <span style={{ fontSize: 9, opacity: 0.6 }}> (dep)</span>}
            </th>
          ))}
        </tr></thead>
        <tbody>
          {(result.rows || []).length === 0 && (
            <tr><td colSpan={(result.columns || []).length || 1} style={{ ...cell, textAlign: "center", color: "var(--text-secondary)", padding: 24 }}>결과 없음</td></tr>
          )}
          {(result.rows || []).map((row, i) => (
            <tr key={i}>
              {(result.columns || []).map(c => (
                <td key={c} style={{ ...cell, fontFamily: "monospace", color: cellColor(c), fontWeight: cellWeight(c) }}>
                  {(roles[c] === "selected" || roles[c] === "dep" || roles[c] === "raw") ? fmtVal(row[c]) : String(row[c] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </>
  );
}

/* reformatter 테이블의 열 정의 — thead 렌더·정렬 키를 한곳에서 관리한다.
   tbody 셀은 아래 표에서 이 순서 그대로 손으로 그린다 (순서 변경 시 함께 고칠 것). */
const ITEM_COLUMNS = [
  { key: "category", label: "구분" },
  { key: "itemid", label: "ITEMID" },
  { key: "alias", label: "ALIAS" },
  { key: "abs", label: "ABS" },
  { key: "scale", label: "SCALE", num: true },
  { key: "addp_form", label: "ADDP FORM" },
  { key: "unit", label: "UNIT" },
  { key: "speclow", label: "SPEC LOW", num: true },
  { key: "spechigh", label: "SPEC HIGH", num: true },
  { key: "target", label: "TARGET", num: true },
  { key: "report_order", label: "R.ORD", num: true },
];

const EMPTY_ITEM_FILTERS = { q: "", category: "", visibility: "", report: "", selection: "" };

function hasItemFilter(f) {
  return Boolean(f.q.trim() || f.category || f.visibility || f.report || f.selection);
}

/* ALIAS 는 이 패널의 행 식별자다 — 공개/비공개(hidden)와 선택(selected) 상태가
   모두 alias 로 키를 잡는다. 값이 CSV 에서 오므로 앞뒤 공백이 섞일 수 있고,
   그대로 비교하면 같은 항목이 행마다 다르게 분류된다. 비교는 항상 이 키로 한다. */
const aliasKey = (a) => String(a ?? "").trim();
const aliasKeySet = (values) => new Set([...(values || [])].map(aliasKey));

/* auto report 대상 = REPORT ORDER 값이 있는 항목. 버튼과 R.ORD 필터가 같은
   기준을 써야 "리포트 항목만" 필터 결과와 일괄 선택 개수가 어긋나지 않는다. */
const hasReportOrder = (it) =>
  it.report_order !== null && it.report_order !== undefined && it.report_order !== "";

/* Index 항목 선택 패널 — vehicle CSV(reformatter 테이블)를 **원본 컬럼 순서 그대로**
   보여주고 뽑을 index 를 고른다 (구분·ITEMID·ALIAS·ABS·SCALE·ADDP FORM·UNIT·SPEC·R.ORD).
   기본 선택은 없음 — 몇 개만 고르거나 열 필터로 좁혀 [＋ 표시 항목 선택]으로 일괄 선택.
   (auto report 와 동일: REPORT ORDER 가 있는 항목만 리포트 item, 빈 항목은 판정용)
   관리자는 공개 컬럼(👁/🚫)으로 reformatter 별 유저 비공개 항목을 지정할 수 있다.

   열 필터 — 항목이 수백 개라 "공개만", "ADDP만", "리포트 항목만" 처럼 열 값으로
   좁혀 보고 그대로 일괄 선택할 수 있어야 한다. 행 순서는 서버가 CSV 원본 순서로
   주며 기본값도 그 순서다 — 정렬은 헤더를 눌렀을 때만(같은 값끼리 모아 보기용)
   적용되고 [원본 순서]로 되돌린다. */
function ItemSelectPanel({ items, selected, onToggle, isAdmin, hidden, onToggleHidden, onHideAll, onShowAll }) {
  const [filters, setFilters] = useState(EMPTY_ITEM_FILTERS);
  const [sort, setSort] = useState({ key: "", dir: "asc" });
  const setF = (patch) => setFilters(f => ({ ...f, ...patch }));

  // 공개/선택 상태는 alias 로만 조회한다 (원본 문자열 비교 금지 — 위 aliasKey 주석).
  const hiddenKeys = useMemo(() => aliasKeySet(hidden), [hidden]);
  const selectedKeys = useMemo(() => aliasKeySet(selected), [selected]);

  /* 행마다 alias 키를 한 번만 계산해 붙인다. `__key` 는 React key 용 —
     alias 를 그대로 key 로 쓰면 CSV 에 같은 alias 가 두 번 있을 때 key 가
     충돌해서, 필터로 빠져야 할 행이 화면에 그대로 남는다(공개만 필터에
     비공개 행이 섞여 보이던 원인). 서버가 중복 alias 를 지우지만 화면도
     자체적으로 안전하게 둔다. */
  const rows = useMemo(() => items.map((it, i) => {
    const key = aliasKey(it.alias);
    return { ...it, __alias: key, __key: `${key}#${i}` };
  }), [items]);

  const q = filters.q.trim().toLowerCase();
  const filteredItems = useMemo(() => rows.filter(it => {
    if (q && ![it.category, it.itemid, it.alias, it.addp_form, it.unit]
      .some(v => String(v || "").toLowerCase().includes(q))) return false;
    if (filters.category && it.category !== filters.category) return false;
    if (filters.visibility) {
      const isHidden = hiddenKeys.has(it.__alias);
      if (filters.visibility === "hidden" ? !isHidden : isHidden) return false;
    }
    if (filters.report) {
      const hasOrd = hasReportOrder(it);
      if (filters.report === "yes" ? !hasOrd : hasOrd) return false;
    }
    if (filters.selection) {
      const isSel = selectedKeys.has(it.__alias);
      if (filters.selection === "on" ? !isSel : isSel) return false;
    }
    return true;
  }), [rows, q, filters.category, filters.visibility, filters.report, filters.selection, hiddenKeys, selectedKeys]);

  // 정렬 키가 없으면 CSV 원본 순서 그대로 둔다.
  const visibleItems = useMemo(() => {
    if (!sort.key) return filteredItems;
    const col = ITEM_COLUMNS.find(c => c.key === sort.key);
    const val = (it) => {
      if (sort.key === "hidden") return hiddenKeys.has(it.__alias) ? 1 : 0;
      if (sort.key === "abs") return it.category === "real" && it.abs ? 1 : 0;
      if (sort.key === "category") return it.category === "addp" ? "ADDP" : "REAL";
      return it[sort.key];
    };
    const blank = (v) => v === null || v === undefined || v === "";
    return [...filteredItems].sort((a, b) => {
      const x = val(a), y = val(b);
      if (blank(x) && blank(y)) return 0;
      if (blank(x)) return 1;        // 빈 값은 방향과 무관하게 항상 뒤로
      if (blank(y)) return -1;
      const r = col?.num
        ? Number(x) - Number(y)
        : String(x).localeCompare(String(y), undefined, { numeric: true });
      return sort.dir === "desc" ? -r : r;
    });
  }, [filteredItems, sort, hiddenKeys]);

  if (!items.length) return null;
  const nSel = rows.filter(it => selectedKeys.has(it.__alias)).length;
  const filterOn = hasItemFilter(filters);
  const nHidden = rows.filter(it => hiddenKeys.has(it.__alias)).length;
  const mono = { fontFamily: "monospace", fontSize: 12 };
  const selectStyle = { ...inputStyle, padding: "5px 8px", fontSize: 12 };
  const toggleSort = (key) => setSort(s => (
    s.key !== key ? { key, dir: "asc" }
      : s.dir === "asc" ? { key, dir: "desc" }
        : { key: "", dir: "asc" }        // 3번째 클릭 = 원본 순서로 복귀
  ));
  const sortMark = (key) => (sort.key !== key ? "" : sort.dir === "asc" ? " ▲" : " ▼");
  const columns = [...ITEM_COLUMNS, ...(isAdmin ? [{ key: "hidden", label: "공개" }] : [])];

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "10px 14px", marginBottom: 12, background: "var(--bg-secondary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 800 }}>📋 Index 항목 선택</span>
        <Pill tone={nSel === 0 ? "warn" : "accent"}>{nSel} / {items.length}</Pill>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          {nSel === 0 ? "뽑을 항목을 선택하세요" : `전체 reformatter alias 중 ${nSel}개 선택됨`}
        </span>
      </div>
        <div style={{ marginTop: 10 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center", flexWrap: "wrap" }}>
            {/* AUTO REPORT 항목전체·전체 선택·전체 해제 버튼 제거됨 — 표시 항목 선택/해제로 대체 */}
            {isAdmin && <Button onClick={onHideAll} title="일반 사용자에게 전체 Index를 숨긴 뒤 필요한 항목만 공개">🚫 전체 비공개</Button>}
            {isAdmin && hidden.size > 0 && <Button onClick={onShowAll} title="모든 Index를 일반 사용자에게 공개">👁 전체 공개</Button>}
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              R.ORD 빈 항목은 리포트 제외(판정용) — 필요하면 개별 선택
              {isAdmin ? " · 공개 컬럼(👁/🚫)으로 유저 비공개 항목 지정 (기본 전부 공개)" : ""}
            </span>
          </div>

          {/* 열 필터 — 값으로 좁혀 보고, 좁힌 결과를 그대로 선택/해제한다 */}
          <div style={{ display: "flex", gap: 6, marginBottom: 8, alignItems: "center", flexWrap: "wrap",
                        padding: "7px 9px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-primary)" }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-secondary)" }}>🔎 열 필터</span>
            <input value={filters.q} onChange={e => setF({ q: e.target.value })}
              placeholder="검색 (ALIAS·ITEMID·수식·UNIT)"
              style={{ ...inputStyle, minWidth: 220, padding: "5px 9px", fontSize: 12 }} />
            <select value={filters.category} onChange={e => setF({ category: e.target.value })}
              title="구분 열" style={selectStyle}>
              <option value="">구분 전체</option>
              <option value="real">REAL 만</option>
              <option value="addp">ADDP 만</option>
            </select>
            <select value={filters.report} onChange={e => setF({ report: e.target.value })}
              title="R.ORD 열 — auto report 대상 여부" style={selectStyle}>
              <option value="">전체</option>
              <option value="yes">Auto report 항목</option>
            </select>
            {isAdmin && (
              <select value={filters.visibility} onChange={e => setF({ visibility: e.target.value })}
                title="공개 열 — 일반 사용자에게 보이는지" style={selectStyle}>
                <option value="">공개 전체</option>
                <option value="public">공개만 ({items.length - nHidden})</option>
                <option value="hidden">비공개만 ({nHidden})</option>
              </select>
            )}
            <select value={filters.selection} onChange={e => setF({ selection: e.target.value })}
              title="현재 선택 상태" style={selectStyle}>
              <option value="">선택 전체</option>
              <option value="on">선택됨만</option>
              <option value="off">미선택만</option>
            </select>
            {(filterOn || sort.key) && (
              <Button onClick={() => { setFilters(EMPTY_ITEM_FILTERS); setSort({ key: "", dir: "asc" }); }}>
                필터 초기화
              </Button>
            )}
            {sort.key && (
              <Pill tone="neutral" title="헤더를 한 번 더 누르면 CSV 원본 순서로 돌아갑니다">
                {(columns.find(c => c.key === sort.key) || {}).label} {sort.dir === "asc" ? "▲" : "▼"}
              </Pill>
            )}
            {/* 일괄 선택 버튼은 전부 제거됨 — 선택은 행 체크박스로만 한다. */}
            <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
              표시 {visibleItems.length} / {items.length}
            </span>
          </div>

          <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
            <table style={gridTable}>
              <thead><tr>
                <th style={{ ...gridHead, whiteSpace: "nowrap" }}></th>
                {columns.map(c => (
                  <th key={c.key} onClick={() => toggleSort(c.key)}
                    title={`${c.label} 열로 정렬 (같은 값끼리 모아 보기) — 다시 누르면 역순, 한 번 더 누르면 원본 순서`}
                    style={{ ...gridHead, whiteSpace: "nowrap", cursor: "pointer", userSelect: "none",
                             color: sort.key === c.key ? "var(--accent)" : gridHead.color }}>
                    {c.label}{sortMark(c.key)}
                  </th>
                ))}
              </tr></thead>
              <tbody>
                {visibleItems.map(it => (
                  <tr key={it.__key} onClick={() => onToggle(it.alias)}
                    style={{ cursor: "pointer", background: selectedKeys.has(it.__alias) ? "transparent" : "var(--bg-primary)", opacity: selectedKeys.has(it.__alias) ? 1 : 0.55 }}>
                    <td style={{ ...gridCell, width: 30 }}>
                      <input type="checkbox" readOnly checked={selectedKeys.has(it.__alias)} style={{ accentColor: "var(--accent)" }} />
                    </td>
                    <td style={{ ...gridCell, ...mono, fontWeight: 700, color: it.category === "addp" ? "#e67e22" : "#27ae60" }}>
                      {it.category === "addp" ? "ADDP" : "REAL"}
                    </td>
                    <td style={{ ...gridCell, ...mono, color: "#000" }}>{it.itemid || ""}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000", whiteSpace: "nowrap" }}>{it.alias}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000", textAlign: "center" }}>{it.category === "real" && it.abs ? "Y" : ""}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000", textAlign: "right" }}>{it.category === "real" ? (it.scale ?? 1) : ""}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000", whiteSpace: "normal", wordBreak: "break-all", minWidth: 220 }}>
                      {it.category === "addp" ? it.addp_form : ""}
                    </td>
                    <td style={{ ...gridCell, ...mono, color: "#000" }}>{it.unit || ""}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000", textAlign: "right" }}>{it.speclow ?? ""}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000", textAlign: "right" }}>{it.spechigh ?? ""}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000", textAlign: "right" }}>{it.target ?? ""}</td>
                    <td style={{ ...gridCell, ...mono, color: "#000" }}>{it.report_order ?? ""}</td>
                    {isAdmin && (
                      <td style={{ ...gridCell, textAlign: "center", width: 56 }}
                        onClick={(e) => { e.stopPropagation(); onToggleHidden(it.alias); }}
                        title={hiddenKeys.has(it.__alias) ? "비공개 — 일반 유저 목록에서 숨김 (클릭하여 공개)" : "공개 (클릭하여 비공개)"}>
                        <span style={{ fontSize: 14, cursor: "pointer", opacity: hiddenKeys.has(it.__alias) ? 1 : 0.7 }}>
                          {hiddenKeys.has(it.__alias) ? "🚫" : "👁"}
                        </span>
                      </td>
                    )}
                  </tr>
                ))}
                {visibleItems.length === 0 && (
                  <tr><td colSpan={columns.length + 1}
                    style={{ ...gridCell, borderRight: "none", padding: 18, textAlign: "center", color: "var(--text-secondary)" }}>
                    조건에 맞는 Index 가 없습니다 — 열 필터를 조정하거나 초기화하세요
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
    </div>
  );
}

/* 관리자 전용 ADDP 수식 테스트 패널 — 필터는 상단 조회 조건(filters)을 그대로 따른다 */
function AddpTestPanel({ product, filters, pageRows, agg }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([{ alias: "", addp_form: "" }]);
  const [help, setHelp] = useState(null);
  const [result, setResult] = useState(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);

  useEffect(() => { setResult(null); setOffset(0); setHelp(null); }, [product, agg]);
  useEffect(() => {
    if (!open || !product || help) return;
    sf(API + "/formula-help?product=" + encodeURIComponent(product))
      .then(setHelp).catch(e => toast.error("도움말 로딩 실패: " + (e.message || e)));
  }, [open, product]);

  const setItem = (i, patch) => setItems(list => list.map((it, j) => j === i ? { ...it, ...patch } : it));
  const validItems = items.filter(it => it.alias.trim() && it.addp_form.trim());

  const run = (nextOffset = 0) => {
    if (!validItems.length) { toast.warn("alias 와 ADDP Form 을 입력하세요"); return; }
    setBusy(true);
    postJson(API + "/test", { product, items: validItems, ...filterBody(filters), agg, offset: nextOffset, limit: pageRows })
      .then(d => { setResult(d); setOffset(d.offset || 0); })
      .catch(e => toast.error(e.message || "테스트 실패"))
      .finally(() => setBusy(false));
  };

  const download = () => {
    if (!validItems.length) { toast.warn("alias 와 ADDP Form 을 입력하세요"); return; }
    setDlBusy(true);
    dlPost(API + "/test/download", { product, items: validItems, ...filterBody(filters), agg }, `${product}_addp_test.csv`)
      .then(() => toast.ok("테스트 CSV 다운로드 완료 — 이력은 관리자 > 다운로드 탭에 기록됩니다"))
      .catch(e => toast.error(e.message || "다운로드 실패"))
      .finally(() => setDlBusy(false));
  };

  const total = result?.total_rows || 0;
  const pageEnd = Math.min(offset + (result?.rows?.length || 0), total);

  return (
    <div style={{ border: "1px dashed var(--accent)", borderRadius: 10, padding: "10px 14px", marginBottom: 12, background: "var(--bg-secondary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }} onClick={() => setOpen(o => !o)}>
        <span style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)" }}>🧪 ADDP 수식 테스트</span>
        <Pill tone="warn">관리자 전용</Pill>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          새 ADDP ITEM 수식을 vehicle CSV 반영 전에 실제 ET 데이터로 검증
        </span>
        <span style={{ marginLeft: "auto", color: "var(--text-secondary)" }}>{open ? "▲ 접기" : "▼ 펼치기"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          {/* 항목 편집 */}
          {items.map((it, i) => (
            <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
              <input value={it.alias} onChange={e => setItem(i, { alias: e.target.value })}
                placeholder="ADDP ITEM (alias) — 예: MY_INDEX" style={{ ...inputStyle, width: 220, fontFamily: "monospace" }} />
              <input value={it.addp_form} onChange={e => setItem(i, { addp_form: e.target.value })}
                placeholder="ADDP Form — 예: ({VTH_IDX} - avg({VTH_IDX})) / std({VTH_IDX})"
                style={{ ...inputStyle, flex: 1, fontFamily: "monospace" }} />
              <Button onClick={() => setItems(list => list.length > 1 ? list.filter((_, j) => j !== i) : [{ alias: "", addp_form: "" }])}>✕</Button>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
            <Button onClick={() => setItems(list => [...list, { alias: "", addp_form: "" }])}>＋ 항목 추가</Button>
            <Button variant="primary" disabled={busy || !product} onClick={() => run(0)}>{busy ? "계산 중…" : "테스트 실행"}</Button>
            <Button disabled={dlBusy || !product} onClick={download}>{dlBusy ? "다운로드 중…" : "⬇ 테스트 CSV"}</Button>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>필터(기간·lot·step 등)·행 수 설정은 상단 조회 조건을 따릅니다</span>
          </div>

          {/* 도움말: 수식 함수 */}
          {help && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", borderTop: "1px dashed var(--border)", paddingTop: 8, marginBottom: 10 }}>
              <details>
                <summary style={{ cursor: "pointer" }}>사용 가능한 함수 ({(help.functions || []).length})</summary>
                <table style={{ borderCollapse: "collapse", marginTop: 4 }}>
                  <tbody>
                    {(help.functions || []).map(f => (
                      <tr key={f.name}>
                        <td style={{ padding: "2px 10px 2px 0", fontFamily: "monospace", whiteSpace: "nowrap" }}>{f.name}</td>
                        <td style={{ padding: "2px 0" }}>{f.desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
              <details style={{ marginTop: 4 }}>
                <summary style={{ cursor: "pointer" }}>
                  🔧 매뉴얼 함수 — MA_Window 등 row 단위 ({(help.manual_functions || []).length})
                </summary>
                <div style={{ margin: "4px 0" }}>
                  auto report 의 MA_Window 계열은 내장, 새 함수는{" "}
                  <code style={{ fontFamily: "monospace", background: "var(--bg-primary)", padding: "1px 6px", borderRadius: 4 }}>{help.manual_file || "reformatter/manual_functions.py"}</code>
                  {" "}에 파이썬 함수로 정의하면 저장 즉시 수식에서 호출 가능합니다 (예: <code style={{ fontFamily: "monospace" }}>my_index({"{VTH_N}"}, {"{VTH_P}"})</code>).
                </div>
                <table style={{ borderCollapse: "collapse", marginTop: 4 }}>
                  <tbody>
                    {(help.manual_functions || []).map(f => (
                      <tr key={f.name}>
                        <td style={{ padding: "2px 10px 2px 0", fontFamily: "monospace", whiteSpace: "nowrap" }}>
                          {f.kind === "manual" ? "📄 " : ""}{f.name}
                        </td>
                        <td style={{ padding: "2px 0" }}>{f.desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </div>
          )}

          {/* 테스트 결과 */}
          {result?.rule_errors?.length > 0 && (
            <Banner tone="warn" style={{ marginBottom: 8 }}>
              {result.rule_errors.map((e, i) => <div key={i} style={{ fontSize: 12, fontFamily: "monospace" }}>{e}</div>)}
            </Banner>
          )}
          {result?.notice && (
            <Banner tone="info" style={{ marginBottom: 8 }}>ℹ️ {result.notice}</Banner>
          )}
          {result && (
            <>
              <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 6 }}>
                <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                  테스트 컬럼 {result.test_columns?.length || 0}개 · 전체 {total.toLocaleString()}행 · {result.elapsed_ms}ms
                </span>
                <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
                  <Button disabled={offset <= 0 || busy} onClick={() => run(Math.max(0, offset - pageRows))}>← 이전</Button>
                  <span style={{ fontSize: 13, fontFamily: "monospace", color: "var(--text-secondary)" }}>
                    {total ? `${(offset + 1).toLocaleString()}–${pageEnd.toLocaleString()} / ${total.toLocaleString()}` : "0"}
                  </span>
                  <Button disabled={pageEnd >= total || busy} onClick={() => run(offset + pageRows)}>다음 →</Button>
                </span>
              </div>
              <ResultTable result={result} highlight={result.test_columns} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function historyTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || "") : date.toLocaleString("ko-KR", { hour12: false });
}

/* ── ET 검색식 파서 & 포맷터 ───────────────────────── */
function formatReformatizeExpression({ product, filters, selItems, itemList, agg }) {
  const f = filterBody(filters);
  const allSelected = itemList?.length > 0 && selItems?.size === itemList.length;
  const lines = [`PRODUCT = ${product || "미선택"}`];
  if (f.days > 0) lines.push(`days = ${f.days}`);
  if (f.date_from) lines.push(`date_from = ${f.date_from}`);
  if (f.date_to) lines.push(`date_to = ${f.date_to}`);
  if (f.lot_filter) lines.push(`root_lot_id = ${f.lot_filter}`);
  if (f.step_filter) lines.push(`step_id = ${f.step_filter}`);
  if (f.step_seq_filter) lines.push(`step_seq = ${f.step_seq_filter}`);
  if (f.wafer_filter) lines.push(`wafer_id = ${f.wafer_filter}`);
  if (f.site_cnt_filter) lines.push(`total_site_cnt = ${f.site_cnt_filter}`);
  if (f.point_cnt_filter) lines.push(`point_cnt = ${f.point_cnt_filter}`);
  lines.push(`ITEMS = ${allSelected ? "ALL" : selItems?.size > 0 ? [...selItems].join(", ") : "미선택"}`);
  lines.push(`AGG = ${agg ? agg.toUpperCase() : "SHOT RAW"}`);
  return lines.join("\n");
}

function parseReformatizeExpression(rawText) {
  if (!rawText || !rawText.trim()) return null;
  const lines = rawText.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");

  const parsed = {
    product: undefined,
    filters: {},
    items: undefined,
    agg: undefined,
  };
  let hasFilterKeys = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("Q1") || trimmed.startsWith("TABLE =") || trimmed.startsWith("REFORMATTER =")) {
      continue;
    }

    let effectiveLine = trimmed;
    if (effectiveLine.startsWith("#")) {
      effectiveLine = effectiveLine.slice(1).trim();
    }

    const kvMatch = effectiveLine.match(/^([a-zA-Z0-9_\uAC00-\uD7A3\s]+?)\s*[:=]\s*(.+)$/);
    if (kvMatch) {
      const key = kvMatch[1].trim().toLowerCase();
      const val = kvMatch[2].trim().replace(/^['"]|['"]$/g, "");

      if (key === "product" || key === "제품" || key === "prod") {
        parsed.product = val;
      } else if (key === "items" || key === "item" || key === "항목" || key === "index") {
        const up = val.toUpperCase();
        if (up === "ALL" || val === "전체") {
          parsed.items = "ALL";
        } else if (val === "미선택" || up === "NONE" || val === "") {
          parsed.items = [];
        } else {
          parsed.items = val.split(",").map(s => s.trim().replace(/^['"]|['"]$/g, "")).filter(Boolean);
        }
      } else if (key === "agg" || key === "집계" || key === "aggregation" || key === "method") {
        const up = val.toUpperCase();
        if (up === "SHOT RAW" || up === "RAW" || up === "" || val === "미선택") {
          parsed.agg = "";
        } else {
          parsed.agg = val.toLowerCase();
        }
      } else if (key === "recent_days" || key === "days" || key === "day" || key === "recent_day" || key === "최근 n일" || key === "최근일수" || key === "최근" || key === "일수") {
        const num = parseInt(val, 10);
        if (!isNaN(num) && num > 0) {
          parsed.filters.days = String(num);
          parsed.filters.date_from = "";
          parsed.filters.date_to = "";
          hasFilterKeys = true;
        } else if (val === "" || val === "0") {
          parsed.filters.days = "";
          hasFilterKeys = true;
        }
      } else if (key === "date_from" || key === "start_date" || key === "from" || key === "시작일" || key === "시작") {
        parsed.filters.date_from = val;
        parsed.filters.days = "";
        hasFilterKeys = true;
      } else if (key === "date_to" || key === "end_date" || key === "to" || key === "종료일" || key === "종료") {
        parsed.filters.date_to = val;
        parsed.filters.days = "";
        hasFilterKeys = true;
      } else if (key === "root_lots" || key === "root_lot_id" || key === "lot_filter" || key === "lots" || key === "lot" || key === "lot_id" || key === "root_lot" || key === "랏") {
        parsed.filters.lot_filter = val;
        hasFilterKeys = true;
      } else if (key === "wafers" || key === "wafer_id" || key === "wafer_filter" || key === "wafer" || key === "웨이퍼") {
        parsed.filters.wafer_filter = val;
        hasFilterKeys = true;
      } else if (key === "step_filter" || key === "step_id" || key === "steps" || key === "step" || key === "스텝") {
        parsed.filters.step_filter = val;
        hasFilterKeys = true;
      } else if (key === "step_seq_filter" || key === "step_seq" || key === "seq") {
        parsed.filters.step_seq_filter = val;
        hasFilterKeys = true;
      } else if (key === "site_cnt_filter" || key === "site_cnt" || key === "total_site_cnt" || key === "site" || key === "sites") {
        parsed.filters.site_cnt_filter = val;
        hasFilterKeys = true;
      } else if (key === "point_cnt_filter" || key === "point_cnt" || key === "shot_count" || key === "point" || key === "points" || key === "pgm_pt") {
        parsed.filters.point_cnt_filter = val;
        hasFilterKeys = true;
      } else if (key === "sql") {
        const fromM = val.match(/tkout_time\s*>=\s*['"]?([0-9\-]+)/i);
        const toM = val.match(/tkout_time\s*<=\s*['"]?([0-9\-]+)/i);
        if (fromM) { parsed.filters.date_from = fromM[1]; parsed.filters.days = ""; hasFilterKeys = true; }
        if (toM) { parsed.filters.date_to = toM[1]; parsed.filters.days = ""; hasFilterKeys = true; }
      } else if (key === "filter") {
        const parts = val.split("|").map(s => s.trim());
        const col = parts[0]?.toLowerCase();
        const vpart = parts.find(p => p.startsWith("values="))?.slice(7)?.trim() || parts[2] || "";
        if (col === "step_id") { parsed.filters.step_filter = vpart; hasFilterKeys = true; }
        else if (col === "step_seq") { parsed.filters.step_seq_filter = vpart; hasFilterKeys = true; }
        else if (col === "total_site_cnt") { parsed.filters.site_cnt_filter = vpart; hasFilterKeys = true; }
        else if (col === "shot_count") { parsed.filters.point_cnt_filter = vpart; hasFilterKeys = true; }
        else if (col === "root_lot_id") { parsed.filters.lot_filter = vpart; hasFilterKeys = true; }
        else if (col === "wafer_id") { parsed.filters.wafer_filter = vpart; hasFilterKeys = true; }
      }
    } else {
      const fromM = effectiveLine.match(/tkout_time\s*>=\s*['"]?([0-9\-]+)/i);
      const toM = effectiveLine.match(/tkout_time\s*<=\s*['"]?([0-9\-]+)/i);
      if (fromM) { parsed.filters.date_from = fromM[1]; parsed.filters.days = ""; hasFilterKeys = true; }
      if (toM) { parsed.filters.date_to = toM[1]; parsed.filters.days = ""; hasFilterKeys = true; }
    }
  }

  return { parsed, hasFilterKeys };
}

/* ── Reformatize 검색이력 패널 ───────────────────────── */
function ReformatizeHistoryPanel({
  user,
  history,
  historySearch,
  setHistorySearch,
  historyBusy,
  loadHistory,
  onLoadEntry,
  pinBusy,
  togglePin,
  copyExpression,
  copyKey,
  copyLink,
}) {
  const isAdmin = user?.role === "admin" || canManagePage(user, "reformatize");

  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 8,
      background: "var(--bg-secondary)",
      padding: "8px 12px",
      marginBottom: 8,
    }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
        justifyContent: "space-between",
        marginBottom: 5,
        paddingBottom: 5,
        borderBottom: "1px solid var(--border)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>
            검색이력
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <input
            value={historySearch}
            onChange={e => setHistorySearch(e.target.value)}
            placeholder="🔍 고유키(RH-), 제품, Index, 작성자 검색…"
            style={{ ...inputStyle, width: 230, padding: "2px 8px", fontSize: 11, height: 25 }}
          />
          <Button
            onClick={() => loadHistory(historySearch)}
            disabled={historyBusy}
            style={{ padding: "2px 7px", fontSize: 11, height: 25 }}
            title="이력 새로고침"
          >
            {historyBusy ? "조회 중…" : "🔄"}
          </Button>
        </div>
      </div>

      {history.length === 0 ? (
        <div style={{ padding: "10px 8px", textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>
          {historySearch.trim() ? "검색 조건에 맞는 검색식 이력이 없습니다." : "아직 검색 이력이 없습니다. 아래 폼에서 조회하거나 다운로드하면 자동으로 이력이 보관됩니다."}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 220, overflowY: "auto", paddingRight: 4 }}>
          {history.map((entry, idx) => {
            const isPinned = Boolean(entry.pinned);
            const searchNo = entry.seq ?? (history.length - idx);
            return (
              <details
                key={entry.history_id}
                style={{
                  flexShrink: 0,
                  border: `1px solid ${isPinned ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: 6,
                  background: isPinned
                    ? "color-mix(in srgb, var(--accent-glow) 45%, var(--bg-primary))"
                    : "var(--bg-primary)",
                  overflow: "hidden",
                }}
              >
                <summary
                  style={{
                    cursor: "pointer",
                    padding: "4px 10px",
                    minHeight: 32,
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    flexWrap: "nowrap",
                    listStyle: "none",
                    userSelect: "none",
                  }}
                >
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 800,
                      color: isPinned ? "var(--accent)" : "var(--text-secondary)",
                      minWidth: 28,
                      flexShrink: 0,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 2,
                    }}
                    title={isPinned ? `관리자 고정 식 (검색 #${searchNo})` : `검색 #${searchNo}`}
                  >
                    {isPinned ? `📌 #${searchNo}` : `#${searchNo}`}
                  </span>
                  <b
                    style={{
                      fontSize: 12,
                      color: "var(--text-primary)",
                      maxWidth: 260,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      flexShrink: 1,
                    }}
                    title={entry.name}
                  >
                    {entry.name}
                  </b>
                  <code
                    style={{
                      fontSize: 10,
                      fontFamily: "monospace",
                      color: "var(--text-secondary)",
                      background: "var(--bg-tertiary)",
                      padding: "1px 4px",
                      borderRadius: 3,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 2,
                      flexShrink: 0,
                    }}
                  >
                    {entry.history_id}
                    <button
                      type="button"
                      onClick={e => { e.preventDefault(); e.stopPropagation(); copyKey(entry); }}
                      title="고유키 복사"
                      style={{ border: "none", background: "none", cursor: "pointer", padding: 0, fontSize: 10 }}
                    >
                      📋
                    </button>
                  </code>
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      color: Number(entry.reuse_count || 0) > 1 ? "var(--text-primary)" : "var(--text-secondary)",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 3,
                      flexShrink: 0,
                    }}
                    title={`실제 조회/검색 횟수: ${Number(entry.reuse_count || 0).toLocaleString()}회`}
                  >
                    <span style={{ color: "#ef4444", fontSize: 11 }}>❤️</span>
                    <span>{Number(entry.reuse_count || 0).toLocaleString()}회</span>
                  </span>
                  {entry.status === "error" ? (
                    <span
                      style={{
                        fontSize: 10.5,
                        fontWeight: 800,
                        color: "#ef4444",
                        background: "color-mix(in srgb, #ef4444 14%, transparent)",
                        border: "1px solid #ef4444",
                        padding: "1px 6px",
                        borderRadius: 4,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        whiteSpace: "nowrap",
                      }}
                      title={`실패 사유: ${entry.error_message || "오류"}`}
                    >
                      ❌ 실패
                    </span>
                  ) : (
                    <span
                      style={{
                        fontSize: 10.5,
                        fontWeight: 700,
                        color: "#10b981",
                        background: "color-mix(in srgb, #10b981 12%, transparent)",
                        border: "1px solid #10b981",
                        padding: "1px 6px",
                        borderRadius: 4,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        whiteSpace: "nowrap",
                      }}
                      title="성공"
                    >
                      ✓ 성공
                    </span>
                  )}
                  {entry.status === "error" && entry.error_message && (
                    <span
                      style={{
                        fontSize: 11,
                        color: "#ef4444",
                        fontWeight: 600,
                        maxWidth: 240,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={`실패 사유: ${entry.error_message}`}
                    >
                      사유: {entry.error_message}
                    </span>
                  )}
                  <b style={{ fontSize: 11, color: "var(--accent)", flexShrink: 0 }}>{entry.username || "anonymous"}</b>
                  <span style={{ fontSize: 10, color: "var(--text-secondary)", flexShrink: 0 }}>
                    {historyTime(entry.last_used_at || entry.timestamp)}
                  </span>
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-secondary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      flex: 1,
                      minWidth: 100,
                    }}
                  >
                    {entry.product} · Index {entry.items?.length ? `${entry.items.length}개` : "전체"} · {entry.agg ? entry.agg.toUpperCase() : "RAW"}
                  </span>
                  <div style={{ marginLeft: "auto", display: "flex", gap: 4, alignItems: "center", flexShrink: 0 }}>
                    {isAdmin && (
                      <button
                        type="button"
                        disabled={pinBusy === entry.history_id}
                        title={entry.pinned ? "고정 해제" : "고정"}
                        onClick={e => { e.preventDefault(); e.stopPropagation(); togglePin(entry); }}
                        style={{
                          background: "var(--bg-secondary)",
                          border: "1px solid var(--border)",
                          borderRadius: 3,
                          padding: "2px 5px",
                          fontSize: 10,
                          cursor: "pointer",
                          color: "var(--text-primary)",
                          opacity: pinBusy === entry.history_id ? 0.55 : 1,
                        }}
                      >
                        {pinBusy === entry.history_id ? "…" : entry.pinned ? "고정 해제" : "고정"}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={e => { e.preventDefault(); e.stopPropagation(); copyLink(entry); }}
                      title="이 검색 조건을 여는 공유 링크 복사"
                      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 3, padding: "2px 5px", fontSize: 10, cursor: "pointer", color: "var(--text-primary)" }}
                    >
                      공유 링크
                    </button>
                    {isAdmin && (
                      <button
                        type="button"
                        onClick={e => { e.preventDefault(); e.stopPropagation(); copyExpression(entry); }}
                        title="차트생성 형식 검색식 복사"
                        style={{
                          background: "var(--bg-secondary)",
                          border: "1px solid var(--border)",
                          borderRadius: 3,
                          padding: "2px 5px",
                          fontSize: 10,
                          cursor: "pointer",
                          color: "var(--text-primary)",
                        }}
                      >
                        식복사
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={e => { e.preventDefault(); e.stopPropagation(); onLoadEntry(entry); }}
                      title="이 검색식을 아래 폼에 그대로 채웁니다"
                      style={{
                        background: "var(--accent)",
                        color: "#ffffff",
                        border: "none",
                        borderRadius: 3,
                        padding: "2px 7px",
                        fontSize: 10,
                        fontWeight: 700,
                        cursor: "pointer",
                      }}
                    >
                      폼 불러오기
                    </button>
                  </div>
                </summary>
                {entry.status === "error" && entry.error_message && (
                  <div style={{
                    padding: "6px 10px",
                    margin: "4px 8px 6px",
                    borderRadius: 6,
                    background: "color-mix(in srgb, #ef4444 10%, var(--bg-primary))",
                    border: "1px solid #ef4444",
                    color: "#ef4444",
                    fontSize: 11.5,
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 6,
                  }}>
                    <span style={{ fontWeight: 800, whiteSpace: "nowrap" }}>⚠️ 실패 사유:</span>
                    <span style={{ fontFamily: "monospace", wordBreak: "break-word" }}>{entry.error_message}</span>
                  </div>
                )}
                {isAdmin && (
                  <div style={{ padding: "6px 10px 8px", borderTop: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                      <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text-secondary)" }}>
                        차트생성 호환 검색식 상세 (고유키: {entry.history_id})
                      </span>
                      <button
                        type="button"
                        onClick={() => copyExpression(entry)}
                        style={{
                          background: "none",
                          border: "none",
                          color: "var(--accent)",
                          cursor: "pointer",
                          fontSize: 10,
                          fontWeight: 700,
                        }}
                      >
                        식복사 📋
                      </button>
                    </div>
                    <pre
                      style={{
                        margin: 0,
                        padding: 6,
                        borderRadius: 4,
                        background: "var(--bg-primary)",
                        border: "1px solid var(--border)",
                        fontFamily: "monospace",
                        fontSize: 10.5,
                        lineHeight: 1.4,
                        color: "var(--text-primary)",
                        maxHeight: 130,
                        overflowY: "auto",
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {entry.expression}
                    </pre>
                  </div>
                )}
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function My_Reformatize({ user }) {
  const isAdmin = user?.role === "admin";
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [settings, setSettings] = useState({ page_rows: 500, max_download_mb: 500, max_download_rows: 100000, value_col: "", scale_applied: false, share_base_url: "" });
  const [gearForm, setGearForm] = useState(null);
  const [result, setResult] = useState(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);
  const [itemList, setItemList] = useState([]);          // vehicle CSV 의 REAL/ADDP 항목
  const [selItems, setSelItems] = useState(new Set());   // 선택된 alias (기본 없음)
  const [hiddenItems, setHiddenItems] = useState(new Set()); // (admin) 유저 비공개 alias
  const [agg, setAgg] = useState("");                    // ""=shot raw, max/min/median/avg/std/p90/p10
  const [dlJob, setDlJob] = useState(null);              // 진행 중인 다운로드 작업(대기열)
  const [runPhase, setRunPhase] = useState(null);        // 조회 진행 상황 {phase, done, total}
  const [history, setHistory] = useState([]);
  const [historySearch, setHistorySearch] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [pinBusy, setPinBusy] = useState("");
  const pendingItems = useRef(null);
  const [sharedLink, setSharedLink] = useState("");
  const [loadedHistoryId, setLoadedHistoryId] = useState("");
  const [exprText, setExprText] = useState("");
  const [exprDirty, setExprDirty] = useState(false);
  const [lastSource, setLastSource] = useState(null);
  const [searchNotice, setSearchNotice] = useState("");

  // 진행 폴링은 공용 훅에 위임한다 — 타이머 정리·연속 실패 중단(무한 로딩 방지)이
  // 화면마다 재구현되던 부분. 다운로드와 조회는 서로 독립이라 인스턴스를 나눈다.
  const poll = usePolling();
  const runPoll = usePolling();

  const clearJob = () => { poll.stop(); setDlJob(null); setDlBusy(false); };

  const loadHistory = (query = historySearch) => {
    setHistoryBusy(true);
    return sf(API + "/history" + qs({ limit: 500, q: String(query || "").trim() }))
      .then(d => setHistory(d.history || []))
      .catch(err => toast.error(`이력 조회 실패: ${err.message || err}`))
      .finally(() => setHistoryBusy(false));
  };

  useEffect(() => {
    const timer = window.setTimeout(() => loadHistory(historySearch), 250);
    return () => window.clearTimeout(timer);
  }, [historySearch]);

  const togglePin = async (entry) => {
    const historyId = String(entry?.history_id || "").trim();
    if (!historyId) return;
    setPinBusy(historyId);
    try {
      await postJson(API + `/history/${encodeURIComponent(historyId)}/pin`, { pinned: !entry.pinned });
      toast.ok(entry.pinned ? "고정을 해제했습니다." : "공용 검색식 상단에 고정했습니다.");
      await loadHistory(historySearch);
    } catch (err) {
      toast.error(err.message || String(err));
    } finally {
      setPinBusy("");
    }
  };

  const copyExpression = async (entry) => {
    try {
      await navigator.clipboard.writeText(entry.expression || "");
      toast.ok(`[${entry.history_id}] 검색식을 복사했습니다.`);
    } catch (_e) {
      toast.error("클립보드 복사 권한이 없습니다.");
    }
  };

  const copyKey = async (entry) => {
    try {
      await navigator.clipboard.writeText(entry.history_id || "");
      toast.ok(`고유키 [${entry.history_id}] 를 복사했습니다.`);
    } catch (_e) {
      toast.error("클립보드 복사 권한이 없습니다.");
    }
  };

  const copyLink = async (entry) => {
    try {
      const latest = await sf(API + "/settings");
      const baseUrl = latest.share_base_url || "";
      setSharedLink(historyShareUrl("/reformatize", entry.history_id, baseUrl));
      try {
        await copyHistoryShareLink("/reformatize", entry.history_id, baseUrl);
        toast.ok(`[${entry.history_id}] 공유 링크를 복사했습니다.`);
      } catch (_e) { toast.warn("공유 링크를 생성했습니다. 아래 주소를 선택해 복사하세요."); }
    } catch (e) { toast.error(e.message || "공유 링크 생성 실패"); }
  };

  const loadHistoryEntry = async (entry) => {
    if (!entry) return;
    const nextProduct = entry.product || product;
    const items = entry.items || [];
    const entryFilters = entry.filters || {};
    const entryAgg = entry.agg || "";

    // 빈 items는 저장된 이력에서 ALL을 뜻한다. 목록이 늦게 와도 유지한다.
    pendingItems.current = { product: nextProduct, items };
    const nextSel = new Set(items.length ? items : nextProduct === product ? itemList.map(it => it.alias) : []);
    setSelItems(nextSel);
    setProduct(nextProduct);
    const nextFilters = {
      ...Object.fromEntries(Object.keys(EMPTY_FILTERS).map(key => [key, String(entryFilters[key] ?? "")])),
      days: entryFilters.days ? String(entryFilters.days) : "",
    };
    setFilters(nextFilters);
    setAgg(entryAgg);
    setLoadedHistoryId(entry.history_id || "");
    setResult(null);
    setOffset(0);

    // 검색식과 필터식 둘 다 즉시 완벽히 채워주고, dirty=false 로 동기화
    const expr = entry.expression || formatReformatizeExpression({
      product: nextProduct,
      filters: nextFilters,
      selItems: nextSel,
      itemList,
      agg: entryAgg,
    });
    setExprText(expr);
    setExprDirty(false);
    setLastSource("filter");
    setSearchNotice("");
    toast.ok(`[${entry.history_id}] 검색식과 필터식을 모두 불러왔습니다. [조회]를 눌러 검색하세요.`);

    document.getElementById("reformatize-form-anchor")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // 폼 상태(제품, 필터, Index, 집계) 변경 시 검색식 텍스트 동기화 (직접 타이핑 중이 아닐 때)
  useEffect(() => {
    if (!exprDirty) {
      setExprText(formatReformatizeExpression({ product, filters, selItems, itemList, agg }));
    }
  }, [product, filters, selItems, itemList, agg, exprDirty]);

  // 검색식 또는 고유키 직접 입력 후 [적용] 시 파싱하여 아래 폼과 검색식에 반영
  const handleApplyExpression = async () => {
    const rawText = String(exprText || "").trim();
    if (!rawText) {
      toast.warn("적용할 검색식 또는 고유키를 입력하세요.");
      return;
    }

    // 0. 고유키 (RH-XXXXXXXX) 또는 순번 (#1 등) 입력 감지
    const keyMatch = rawText.match(/(?:#|history_id=)?(RH-[0-9A-Fa-f]{8})\b/i);
    const seqMatch = !keyMatch && rawText.match(/^#?(\d+)$/);

    if (keyMatch || seqMatch) {
      const targetKey = keyMatch ? keyMatch[1].toUpperCase() : null;
      const targetSeq = seqMatch ? Number(seqMatch[1]) : null;

      // 1) 로컬 history 목록에서 탐색
      let foundEntry = history.find(e => {
        if (targetKey && String(e.history_id || "").toUpperCase() === targetKey) return true;
        if (targetSeq !== null && Number(e.seq) === targetSeq) return true;
        return false;
      });

      // 2) 로컬에 없고 targetKey가 있으면 서버 API 조회
      if (!foundEntry && targetKey) {
        try {
          const res = await sf(API + "/history" + qs({ history_id: targetKey, limit: 100 }));
          foundEntry = (res.history || []).find(item => String(item?.history_id || "").toUpperCase() === targetKey);
        } catch (_err) {
          // fallback
        }
      }

      if (foundEntry) {
        await loadHistoryEntry(foundEntry);
        toast.ok(`[${foundEntry.history_id}] 고유키에 맞는 검색식과 필터를 모두 반영했습니다. [조회]를 누르면 검색이 시작됩니다.`);
        return;
      }
    }

    const res = parseReformatizeExpression(rawText);
    if (!res) {
      toast.warn("적용할 검색식 내용이 없습니다.");
      return;
    }
    const { parsed } = res;

    // 1. 제품
    let nextProd = product;
    if (parsed.product) {
      const match = products.find(p => p.product.toUpperCase() === parsed.product.toUpperCase());
      nextProd = match ? match.product : parsed.product;
      if (nextProd !== product) {
        pendingItems.current = {
          product: nextProd,
          items: parsed.items === "ALL" ? [] : (Array.isArray(parsed.items) ? parsed.items : []),
        };
        setProduct(nextProd);
      }
    }

    // 2. Index 항목
    let nextSel = selItems;
    if (parsed.items !== undefined) {
      if (parsed.items === "ALL") {
        nextSel = new Set(itemList.map(it => it.alias));
        setSelItems(nextSel);
      } else if (Array.isArray(parsed.items)) {
        nextSel = new Set(parsed.items);
        setSelItems(nextSel);
      }
    }

    // 3. 필터: 검색식 내용을 아래 필터 입력창들에 즉각 동기화 반영
    const nextFilters = { ...EMPTY_FILTERS };
    if (parsed.filters.days) {
      nextFilters.days = parsed.filters.days;
      nextFilters.date_from = "";
      nextFilters.date_to = "";
    } else if (parsed.filters.date_from || parsed.filters.date_to) {
      nextFilters.date_from = parsed.filters.date_from || "";
      nextFilters.date_to = parsed.filters.date_to || "";
      nextFilters.days = "";
    }
    for (const k of ["lot_filter", "step_filter", "step_seq_filter", "wafer_filter", "site_cnt_filter", "point_cnt_filter"]) {
      if (parsed.filters[k] !== undefined) {
        nextFilters[k] = parsed.filters[k];
      }
    }
    setFilters(nextFilters);

    // 4. 집계
    let nextAgg = agg;
    if (parsed.agg !== undefined) {
      nextAgg = parsed.agg;
      setAgg(nextAgg);
    }

    // 파싱된 조건으로 정돈된 검색식 텍스트 동기화
    const formattedExpr = formatReformatizeExpression({
      product: nextProd,
      filters: nextFilters,
      selItems: nextSel,
      itemList,
      agg: nextAgg,
    });
    setExprText(formattedExpr);

    setResult(null);
    setOffset(0);
    setExprDirty(false);
    setLastSource("filter");
    toast.ok("검색식을 아래 필터에 반영했습니다. [조회]를 누르면 검색이 시작됩니다.");
  };

  const handleCopyExpression = async () => {
    try {
      await navigator.clipboard.writeText(exprText || "");
      toast.ok("검색식을 복사했습니다.");
    } catch (_e) {
      toast.error("클립보드 복사 권한이 없습니다.");
    }
  };

  useEffect(() => {
    const historyId = historyIdFromLocation(/^RH-[0-9A-F]{8}$/i);
    if (!historyId) return;
    let alive = true;
    sf(API + "/history" + qs({ history_id: historyId, limit: 1000 }))
      .then(d => {
        if (!alive) return;
        const entry = (d.history || []).find(item => String(item?.history_id || "").toUpperCase() === historyId.toUpperCase());
        if (!entry) throw new Error("공유된 ET 검색 이력을 찾지 못했습니다.");
        loadHistoryEntry(entry);
        toast.ok(`[${historyId}] 공유 검색 조건을 불러왔습니다. 조회 또는 다운로드를 직접 시작하세요.`);
      })
      .catch(err => { if (alive) toast.error(err.message || String(err)); });
    return () => { alive = false; };
  }, []);

  // 완료된 작업의 CSV 를 실제로 내려받는다 (이 시점에 downloads.jsonl 기록).
  const fetchResult = (job) => {
    setDlJob({ ...job, phase: "파일 저장 중" });
    dl(API + "/download/file" + qs({ job_id: job.job_id }), job.filename || `${job.product || product}_reformatize.csv`)
      .then(() => {
        toast.ok(`다운로드 완료 — ${(job.rows || 0).toLocaleString()}행 · 이력은 관리자 > 다운로드 탭에 기록됩니다`);
        setTimeout(() => loadHistory(historySearch), 500);
      })
      .catch(e => toast.error(e.message || "다운로드 실패"))
      .finally(clearJob);
  };

  const pollJob = (jobId) => {
    poll.start(() => sf(API + "/download/status" + qs({ job_id: jobId })), {
      intervalMs: DL_POLL_MS,
      maxErrors: 5,                    // 연속 실패 시 로딩창을 닫는다(무한 로딩 금지)
      onData: (d) => {
        setDlJob(d);
        if (d.state === "ready") { poll.stop(); fetchResult(d); }
        else if (d.state === "error") { clearJob(); toast.error(d.error || "다운로드 실패"); }
        else if (d.state !== "queued" && d.state !== "running") {
          clearJob();
          if (d.state === "expired") toast.warn("결과가 만료되었습니다 — 다시 시도해 주세요");
        }
      },
      onError: (e) => { clearJob(); toast.error("진행 상황 확인 실패: " + (e?.message || e || "")); },
    });
  };

  const cancelDownload = () => {
    const id = dlJob?.job_id;
    clearJob();
    if (!id) return;
    postJson(API + "/download/cancel", { job_id: id }).catch(() => {});
    toast.warn("다운로드를 취소했습니다");
  };

  useEffect(() => {
    sf(API + "/products").then(d => {
      const list = d.products || [];
      setProducts(list);
      if (list.length) setProduct(current => current || list[0].product);
    }).catch(e => toast.error("제품 목록 로딩 실패: " + (e.message || e)));
    sf(API + "/settings").then(d => {
      setSettings(s => ({ ...s, ...d }));
      if (d.share_base_url) setCachedShareBaseUrl(d.share_base_url);
    }).catch(() => {});
    // 새로고침·재진입으로 화면이 다시 뜬 경우 진행 중인 내 다운로드를 이어서 표시.
    sf(API + "/download/queue").then(d => {
      const mine = (d.jobs || []).find(j => j.state === "queued" || j.state === "running");
      if (mine) { setDlBusy(true); setDlJob(mine); pollJob(mine.job_id); }
    }).catch(() => {});
  }, []);

  const selected = products.find(p => p.product === product);

  // 제품 변경 시 vehicle CSV 의 REAL/ADDP 항목 목록 로드.
  // 기본 선택 없음 — 필요한 항목만 골라 가볍게 조회하도록 유도한다.
  // 서버가 유저에게는 비공개 항목을 제외하고, 관리자에게는 hidden 플래그를 준다.
  useEffect(() => {
    let alive = true;
    setItemList([]);
    setHiddenItems(new Set());
    if (!product || !selected?.vehicle_csv) return;
    sf(API + "/items?product=" + encodeURIComponent(product))
      .then(d => {
        if (!alive) return;
        const items = d.items || [];
        setItemList(items);
        setHiddenItems(new Set(items.filter(it => it.hidden).map(it => it.alias)));
        if (pendingItems.current?.product === product) {
          const savedItems = pendingItems.current.items;
          setSelItems(new Set(savedItems.length ? savedItems : items.map(it => it.alias)));
          pendingItems.current = null;
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [product, selected?.vehicle_csv]);

  // (admin) 항목 공개/비공개 토글 — 즉시 저장, 실패 시 원복
  const saveVisibility = (next, successMessage) => {
    const prev = hiddenItems;
    setHiddenItems(next);
    return postJson(API + "/visibility", { product, hidden: [...next] })
      .then(d => {
        setHiddenItems(new Set(d.hidden || []));
        toast.ok(successMessage);
      })
      .catch(e => { setHiddenItems(prev); toast.error(e.message || "공개 설정 저장 실패"); });
  };

  // 저장된 목록에 공백만 다른 표기가 섞여 있어도 같은 alias 로 다루도록 키로 지운다.
  const toggleHidden = (alias) => {
    const key = aliasKey(alias);
    const next = new Set([...hiddenItems].filter(a => aliasKey(a) !== key));
    const nowHidden = next.size === hiddenItems.size;   // 지운 게 없으면 = 공개였다
    if (nowHidden) next.add(alias);
    saveVisibility(next, nowHidden ? `'${alias}' 를 유저에게 비공개로 설정했습니다` : `'${alias}' 를 공개로 되돌렸습니다`);
  };
  const hideAllItems = () => {
    if (!confirm("이 제품의 Index 항목 전체를 일반 사용자에게 숨길까요? 이후 필요한 항목만 👁 버튼으로 공개할 수 있습니다.")) return;
    saveVisibility(new Set(itemList.map(it => it.alias)), `전체 ${itemList.length}개 항목을 비공개로 설정했습니다`);
  };
  const showAllItems = () => {
    if (!confirm("이 제품의 모든 Index 항목을 일반 사용자에게 공개할까요?")) return;
    saveVisibility(new Set(), "모든 항목을 공개했습니다");
  };

  const allSelected = itemList.length > 0 && selItems.size === itemList.length;
  const selArray = () => (allSelected ? [] : [...selItems]);   // 전체 선택이면 서버 기본(전체) 사용

  const setF = (patch) => {
    setLastSource("filter");
    setFilters(f => ({ ...f, ...patch }));
  };

  const resolveEffectiveQuery = () => {
    let effProduct = product;
    let effFilters = { ...filters };
    let effItems = selArray();
    let effAgg = agg;
    let effHistoryId = loadedHistoryId;

    if (exprDirty && exprText.trim()) {
      const res = parseReformatizeExpression(exprText);
      if (res) {
        const { parsed, hasFilterKeys } = res;
        if (lastSource === "filter") {
          // 아래 필터를 우선 적용: 아래 필터에 비어있는 항목만 검색식에서 보충
          if (!effProduct && parsed.product) {
            const match = products.find(p => p.product.toUpperCase() === parsed.product.toUpperCase());
            effProduct = match ? match.product : parsed.product;
          }
          if (hasFilterKeys) {
            if (!effFilters.days && !effFilters.date_from && !effFilters.date_to) {
              if (parsed.filters.days) effFilters.days = parsed.filters.days;
              else if (parsed.filters.date_from || parsed.filters.date_to) {
                effFilters.date_from = parsed.filters.date_from || "";
                effFilters.date_to = parsed.filters.date_to || "";
              }
            }
            for (const k of ["lot_filter", "step_filter", "step_seq_filter", "wafer_filter", "site_cnt_filter", "point_cnt_filter"]) {
              if (!String(effFilters[k] || "").trim() && parsed.filters[k]) {
                effFilters[k] = parsed.filters[k];
              }
            }
          }
          if (effItems.length === 0 && parsed.items && parsed.items !== "ALL") {
            effItems = Array.isArray(parsed.items) ? parsed.items : [];
          }
          if (!effAgg && parsed.agg) {
            effAgg = parsed.agg;
          }
        } else {
          // 검색식을 직접 고친 경우: 검색식 내용을 폼과 쿼리에 반영
          if (parsed.product) {
            const match = products.find(p => p.product.toUpperCase() === parsed.product.toUpperCase());
            effProduct = match ? match.product : parsed.product;
            if (effProduct !== product) {
              pendingItems.current = {
                product: effProduct,
                items: parsed.items === "ALL" ? [] : (Array.isArray(parsed.items) ? parsed.items : []),
              };
              setProduct(effProduct);
            }
          }
          if (parsed.items !== undefined) {
            if (parsed.items === "ALL") {
              effItems = [];
              setSelItems(new Set(itemList.map(it => it.alias)));
            } else if (Array.isArray(parsed.items)) {
              effItems = parsed.items;
              setSelItems(new Set(parsed.items));
            }
          }
          if (hasFilterKeys) {
            const nextF = { ...EMPTY_FILTERS };
            if (parsed.filters.days) {
              nextF.days = parsed.filters.days;
            } else if (parsed.filters.date_from || parsed.filters.date_to) {
              nextF.date_from = parsed.filters.date_from || "";
              nextF.date_to = parsed.filters.date_to || "";
            }
            for (const k of ["lot_filter", "step_filter", "step_seq_filter", "wafer_filter", "site_cnt_filter", "point_cnt_filter"]) {
              if (parsed.filters[k] !== undefined) {
                nextF[k] = parsed.filters[k];
              }
            }
            effFilters = nextF;
            setFilters(nextF);
          }
          if (parsed.agg !== undefined) {
            effAgg = parsed.agg;
            setAgg(parsed.agg);
          }
          effHistoryId = "";
          setLoadedHistoryId("");
        }
      }
      setExprDirty(false);
    }

    return { effProduct, effFilters, effItems, effAgg, effHistoryId };
  };

  const onFilterEnter = (e) => {
    if (e.key !== "Enter") return;
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return;
    run(0);
  };

  const run = (nextOffset = 0) => {
    const { effProduct, effFilters, effItems, effAgg, effHistoryId } = resolveEffectiveQuery();
    if (!effProduct) { toast.warn("제품을 선택하세요"); return; }
    if (itemList.length && effItems.length === 0 && !allSelected) { toast.warn("Index 항목을 하나 이상 선택하세요"); return; }
    const token = `run${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    setBusy(true);
    setRunPhase(null);
    runPoll.start(() => sf(API + "/run/progress" + qs({ token })), {
      intervalMs: RUN_POLL_MS,
      maxErrors: 5,
      onData: (d) => { if (d?.active) setRunPhase(d); },
      onError: () => runPoll.stop(),
    });
    postJson(API + "/run", {
      product: effProduct,
      offset: nextOffset,
      limit: settings.page_rows,
      ...filterBody(effFilters),
      items: effItems,
      agg: effAgg,
      history_id: effHistoryId,
      progress_token: token,
    })
      .then(d => {
        setResult(d);
        setOffset(d.offset || 0);
        setSearchNotice("아래 필터식이 반영되어 검색되었습니다");
        toast.ok("아래 필터식이 반영되어 검색되었습니다");
        loadHistory(historySearch);
      })
      .catch(e => {
        toast.error(e.message || "조회 실패");
        loadHistory(historySearch);
      })
      .finally(() => { runPoll.stop(); setRunPhase(null); setBusy(false); });
  };

  const download = () => {
    const { effProduct, effFilters, effItems, effAgg, effHistoryId } = resolveEffectiveQuery();
    if (!effProduct) return;
    if (itemList.length && effItems.length === 0 && !allSelected) { toast.warn("Index 항목을 하나 이상 선택하세요"); return; }
    setDlBusy(true);
    postJson(API + "/download/start", {
      product: effProduct,
      ...filterBody(effFilters),
      items: effItems,
      agg: effAgg,
      history_id: effHistoryId,
    })
      .then(d => {
        setDlJob(d);
        if (d.duplicate) toast.warn("같은 조건의 다운로드가 이미 진행 중입니다 — 그 작업을 이어서 표시합니다");
        else if (d.ahead > 0) toast.ok(`다른 다운로드가 진행 중이라 대기열에 등록했습니다 (앞에 ${d.ahead}건)`);
        loadHistory(historySearch);
        pollJob(d.job_id);
      })
      .catch(e => {
        clearJob();
        toast.error(e.message || "다운로드 요청 실패");
        loadHistory(historySearch);
      });
  };

  const saveSettings = () => {
    const form = gearForm || settings;
    postJson(API + "/settings", {
      page_rows: Number(form.page_rows) || 500,
      max_download_mb: Number(form.max_download_mb) || 500,
      value_col: String(form.value_col || "").trim(),
      scale_applied: !!form.scale_applied,
      share_base_url: String(form.share_base_url || "").trim(),
    }).then(d => {
      setSettings({
        page_rows: d.page_rows,
        max_download_mb: d.max_download_mb ?? 500,
        max_download_rows: d.max_download_rows,
        value_col: d.value_col || "",
        scale_applied: !!d.scale_applied,
        share_base_url: d.share_base_url || "",
      });
      setCachedShareBaseUrl(d.share_base_url || "");
      setGearForm(null);
      toast.ok("공통 공유 기본 주소 및 ET 다운로드 설정을 저장했습니다");
    }).catch(e => toast.error(e.message || "설정 저장 실패"));
  };

  const total = result?.total_rows || 0;
  const pageEnd = Math.min(offset + (result?.rows?.length || 0), total);
  const canPrev = offset > 0;
  const canNext = pageEnd < total;

  return (
    <PageShell>
      <Banner tone="warn" style={{ borderRadius: 0, borderBottom: "1px solid var(--warn-line)", lineHeight: 1.45 }}>
        <b>주의사항</b> · 최근 N일을 필요한 만큼 작게 줄이고
        root_lot_id로 랏을 검색하면 읽는 parquet 수와 계산 행이 크게 줄어 훨씬 빨라집니다.
        필요한 Index만 선택하면 속도가 더 좋아집니다.
      </Banner>

      <div style={{ padding: "16px 24px" }}>
      <details style={{ marginBottom: 8, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-secondary)", padding: "8px 12px" }}>
        <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>
          ET 다운로드 사용 가이드 (2가지 검색 방식)
        </summary>
        <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 10 }}>
          <div style={{ padding: "10px 12px", background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 6 }}>
            <div style={{ fontSize: 12.5, fontWeight: 800, color: "var(--accent)", marginBottom: 6, display: "flex", alignItems: "center", gap: 5 }}>
              <span>📌</span>
              <span>방법 1. 아래 필터 폼을 이용하는 방법 (일반 사용자 권장)</span>
            </div>
            <ol style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.65, color: "var(--text-secondary)" }}>
              <li><b style={{ color: "var(--text-primary)" }}>제품 및 기간 지정</b>: 제품을 선택하고 최근 N일 또는 시작~종료일을 설정합니다.</li>
              <li><b style={{ color: "var(--text-primary)" }}>세부 필터 입력</b>: root_lot_id, step_id, step_seq, wafer_id 등을 원하는 만큼 입력합니다. (필터 변경 시 상단 검색식 텍스트도 실시간으로 함께 만들어집니다.)</li>
              <li><b style={{ color: "var(--text-primary)" }}>Index 및 집계 선택</b>: 필요한 REAL/ADDP 항목을 선택하고 집계 방식(shot raw 또는 요약 집계)을 선택합니다.</li>
              <li><b style={{ color: "var(--text-primary)" }}>조회 또는 다운로드</b>: <b style={{ color: "var(--text-primary)" }}>[조회]</b> 버튼을 누르면 화면에 표가 나타나며, <b style={{ color: "var(--text-primary)" }}>[⬇ CSV 다운로드]</b>로 전체 결과를 저장할 수 있습니다.</li>
            </ol>
          </div>
          <div style={{ padding: "10px 12px", background: "var(--bg-primary)", border: "1px solid var(--border)", borderRadius: 6 }}>
            <div style={{ fontSize: 12.5, fontWeight: 800, color: "#2563eb", marginBottom: 6, display: "flex", alignItems: "center", gap: 5 }}>
              <span>⚡</span>
              <span>방법 2. 상단 검색식·고유키를 입력/수정하여 진행하는 방법 (공유/고급)</span>
            </div>
            <ol style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, lineHeight: 1.65, color: "var(--text-secondary)" }}>
              <li><b style={{ color: "var(--text-primary)" }}>검색식 직접 편집</b>: 상단 검색식 창에 조건문(예: PRODUCT, days, root_lot_id 등)을 직접 타이핑하여 수정합니다.</li>
              <li><b style={{ color: "var(--text-primary)" }}>고유키(이력 ID) 입력 지원</b>: 복사한 검색식 전문뿐만 아니라, 이력 고유키(예: <code style={{ fontFamily: "monospace", color: "var(--accent)" }}>RH-B570659B</code> 또는 순번 <code style={{ fontFamily: "monospace" }}>#1</code>)를 검색식 창에 입력해도 됩니다.</li>
              <li><b style={{ color: "var(--text-primary)" }}>[✓ 적용] 클릭</b>: <b style={{ color: "var(--text-primary)" }}>[✓ 적용]</b> 버튼(또는 Ctrl+Enter)을 누르면 검색식/고유키의 조건이 아래 필터 폼에 채워지고 정돈된 검색식으로 변환됩니다. (이때는 조회가 바로 시작되지 않습니다.)</li>
              <li><b style={{ color: "var(--text-primary)" }}>[조회] 클릭</b>: 아래 필터에 반영된 내용을 검토한 후 <b style={{ color: "var(--text-primary)" }}>[조회]</b> 버튼을 눌러 검색을 시작합니다.</li>
            </ol>
          </div>
        </div>
        {isAdmin && (
          <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--text-secondary)", borderTop: "1px dashed var(--border)", paddingTop: 6 }}>
            🛠 <b style={{ color: "var(--text-primary)" }}>관리자 전용 수식 테스트</b>: [새 ADDP 수식 테스트] 섹션에서 신규 alias와 수식을 작성해 vehicle CSV 반영 전 미리 검증할 수 있습니다.
          </div>
        )}
      </details>

      {/* 검색이력 패널 */}
      <ReformatizeHistoryPanel
        user={user}
        history={history}
        historySearch={historySearch}
        setHistorySearch={setHistorySearch}
        historyBusy={historyBusy}
        loadHistory={loadHistory}
        onLoadEntry={loadHistoryEntry}
        pinBusy={pinBusy}
        togglePin={togglePin}
        copyExpression={copyExpression}
        copyKey={copyKey}
        copyLink={copyLink}
      />

      {/* 조회 조건 */}
      {sharedLink && <Banner tone="info" style={{ marginBottom: 8 }}>
        <label>공유 링크 <input aria-label="생성된 공유 링크" readOnly value={sharedLink} onFocus={e => e.target.select()} style={{ ...inputStyle, width: "100%" }} /></label>
        <div>공유 기본 주소는 우측 아래 ⚙ ET 다운로드 설정에서 변경할 수 있습니다.</div>
      </Banner>}
      <div id="reformatize-form-anchor" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>제품</span>
        <select value={product} onChange={e => { pendingItems.current = null; setSelItems(new Set()); setLoadedHistoryId(""); setProduct(e.target.value); setResult(null); setOffset(0); setLastSource("filter"); }} style={{ ...inputStyle, minWidth: 160 }}>
          {products.length === 0 && <option value="">제품 없음</option>}
          {products.map(p => <option key={p.product} value={p.product}>{p.product}</option>)}
        </select>
        {selected && !selected.vehicle_csv && (
          <Pill tone="warn" title="data_root/reformatter/ 에 <vehicle>_reformatter.csv 를 추가하세요">규칙 CSV 없음</Pill>
        )}
        <Button variant="primary" disabled={busy || !product} onClick={() => run(0)}>{busy ? "계산 중…" : "조회"}</Button>
        <Button disabled={dlBusy || !product || !selected?.vehicle_csv} onClick={download}
          title="필터 적용 결과를 CSV 로 다운로드 (여러 명이 동시에 걸면 순서대로 처리됩니다)">
          {dlBusy ? (dlJob?.state === "queued" ? "대기 중…" : "다운로드 준비 중…") : "⬇ CSV 다운로드"}
        </Button>
      </div>

      {/* 검색식 편집 및 반영 바 — 항상 열려 있고 직접 값 입력 후 적용 가능. 필터 영역을 가리지 않도록 콤팩트하게 구성 */}
      <div style={{
        marginBottom: 8,
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--bg-secondary)",
        padding: "6px 10px",
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4, flexWrap: "wrap", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 12, fontWeight: 800, color: "var(--text-primary)" }}>
              검색식
            </span>
            {loadedHistoryId && (
              <span style={{ fontSize: 11, color: "var(--accent)", fontWeight: 700 }}>
                · 이력 {loadedHistoryId}
              </span>
            )}
            <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
              (직접 수정 후 [적용] 시 아래 필터에 반영)
            </span>
          </div>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <Button
              variant="primary"
              onClick={handleApplyExpression}
              style={{ padding: "2px 10px", fontSize: 11, fontWeight: 700 }}
              title="검색식의 내용을 아래 필터에 반영합니다 (Ctrl+Enter). 실제 검색은 [조회] 버튼을 눌러야 실행됩니다."
            >
              ✓ 적용
            </Button>
            <Button
              onClick={handleCopyExpression}
              style={{ padding: "2px 8px", fontSize: 11 }}
              title="검색식 복사"
            >
              📋 복사
            </Button>
          </div>
        </div>
        <textarea
          value={exprText}
          onChange={e => {
            setExprDirty(true);
            setLastSource("expr");
            setExprText(e.target.value);
          }}
          onKeyDown={e => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
              e.preventDefault();
              handleApplyExpression();
            }
          }}
          aria-label="현재 ET 검색식"
          placeholder="PRODUCT = ...&#10;days = 7&#10;root_lot_id = ...&#10;ITEMS = ALL&#10;AGG = SHOT RAW"
          style={{
            ...inputStyle,
            width: "100%",
            height: 48,
            minHeight: 38,
            maxHeight: 120,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11.5,
            lineHeight: 1.35,
            padding: "4px 8px",
            resize: "vertical",
            display: "block",
            boxSizing: "border-box",
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
          }}
        />
      </div>

      {/* 필터 — tkout_time 기간 + root_lot_id/step_id/step_seq/wafer_id/total_site_cnt/PGM point 수.
          최근 N일과 시작~종료일은 상호 배타 (한쪽 입력 시 다른 쪽 초기화). */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>필터</span>
        <input type="number" min={1} value={filters.days}
          onChange={e => setF({ days: e.target.value, date_from: "", date_to: "" })}
          onKeyDown={onFilterEnter}
          placeholder="최근 N일" title="데이터 최신 tkout_time 기준 최근 N일 (기간 지정과 동시 사용 불가)"
          style={{ ...inputStyle, width: 90 }} />
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>또는</span>
        <input type="date" value={filters.date_from}
          onChange={e => setF({ date_from: e.target.value, days: "" })}
          title="tkout_time 시작일 (포함)" style={inputStyle} />
        <span style={{ color: "var(--text-secondary)" }}>~</span>
        <input type="date" value={filters.date_to}
          onChange={e => setF({ date_to: e.target.value, days: "" })}
          title="tkout_time 종료일 (포함)" style={inputStyle} />
        <input value={filters.lot_filter} onChange={e => setF({ lot_filter: e.target.value })}
          onKeyDown={onFilterEnter}
          placeholder="root_lot_id (쉼표=OR, 포함)" style={{ ...inputStyle, minWidth: 190 }} />
        <input value={filters.step_filter} onChange={e => setF({ step_filter: e.target.value })}
          onKeyDown={onFilterEnter}
          placeholder="step_id (쉼표=OR, 포함)" style={{ ...inputStyle, minWidth: 160 }} />
        <input value={filters.step_seq_filter} onChange={e => setF({ step_seq_filter: e.target.value })}
          onKeyDown={onFilterEnter}
          placeholder="step_seq (쉼표=OR, 포함)" style={{ ...inputStyle, minWidth: 170 }} />
        <input value={filters.wafer_filter} onChange={e => setF({ wafer_filter: e.target.value })}
          onKeyDown={onFilterEnter}
          placeholder="wafer_id (쉼표=OR, 포함)" style={{ ...inputStyle, width: 160 }} />
        <input value={filters.site_cnt_filter} onChange={e => setF({ site_cnt_filter: e.target.value })}
          onKeyDown={onFilterEnter}
          placeholder="raw total_site_cnt (정확 일치)" title="원본 parquet의 total_site_cnt 열 값으로 필터링합니다. 화면 PGM의 (npt)와는 별개입니다."
          style={{ ...inputStyle, width: 190 }} />
        <input value={filters.point_cnt_filter} onChange={e => setF({ point_cnt_filter: e.target.value })}
          onKeyDown={onFilterEnter}
          placeholder="PGM point 수 (예: 25,49)" title="조회 결과 PGM(25pt)의 실제 포인트 수로 PGM package 전체를 필터링합니다. 쉼표는 OR입니다."
          style={{ ...inputStyle, width: 190 }} />
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>집계</span>
        <select value={agg} onChange={e => { setAgg(e.target.value); setResult(null); setOffset(0); setLastSource("filter"); }}
          title="root_lot_id × wafer_id × step_id × PGM(pt) 그룹으로 index 를 집계해서 추출"
          style={{ ...inputStyle, width: 170 }}>
          <option value="">shot raw (chip_x_pos × chip_y_pos별)</option>
          {["max", "min", "median", "avg", "std", "p90", "p10"].map(m => (
            <option key={m} value={m}>{m.toUpperCase()}</option>
          ))}
        </select>
        {hasAnyFilter(filters) && (
          <Button onClick={() => setFilters(EMPTY_FILTERS)}>필터 초기화</Button>
        )}
      </div>

      {/* 아래 필터식 반영 안내 배너 */}
      {searchNotice && (
        <div
          style={{
            margin: "0 0 8px",
            padding: "6px 12px",
            borderRadius: 6,
            background: "color-mix(in srgb, var(--accent) 12%, var(--bg-secondary))",
            border: "1px solid color-mix(in srgb, var(--accent) 35%, var(--border))",
            color: "var(--text-primary)",
            fontSize: 12,
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span style={{ fontSize: 13, color: "var(--accent)" }}>ℹ️</span>
          <span>{searchNotice}</span>
          {result?.total_rows !== undefined && (
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-secondary)", fontWeight: 500 }}>
              (조회 결과: {result.total_rows.toLocaleString()}행 · {result.elapsed_ms || 0}ms)
            </span>
          )}
        </div>
      )}

      {/* Index 항목 선택 — REAL(scale factor)/ADDP(form·참조) 확인 후 뽑을 항목 선택 */}
      <ItemSelectPanel
        items={itemList}
        selected={selItems}
        onToggle={(alias) => {
          setLastSource("filter");
          setSelItems(prev => {
            const key = aliasKey(alias);
            const s = new Set([...prev].filter(a => aliasKey(a) !== key));
            if (s.size === prev.size) s.add(alias);           // 없던 항목이면 추가
            return s;
          });
        }}
        isAdmin={isAdmin}
        hidden={hiddenItems}
        onToggleHidden={toggleHidden}
        onHideAll={hideAllItems}
        onShowAll={showAllItems}
      />

      {/* 관리자 전용 ADDP 수식 테스트 */}
      {isAdmin && product && selected?.vehicle_csv && (
        <AddpTestPanel product={product} filters={filters} pageRows={settings.page_rows} agg={agg} />
      )}

      {/* 규칙 에러 */}
      {result?.rule_errors?.length > 0 && (
        <Banner tone="warn" style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>규칙 경고 ({result.rule_errors.length})</div>
          {result.rule_errors.map((e, i) => <div key={i} style={{ fontSize: 12, fontFamily: "monospace" }}>{e}</div>)}
        </Banner>
      )}

      {/* 경량 조회 안내 — 데이터가 커서 최신 일부만 보여줄 때 */}
      {result?.notice && (
        <Banner tone="info" style={{ marginBottom: 12 }}>ℹ️ {result.notice}</Banner>
      )}

      {/* 의존성 트리 */}
      {result?.dep_tree?.length > 0 && <DepTreePanel tree={result.dep_tree} />}

      {/* 결과 테이블 */}
      {!result && !busy && (
        <EmptyState icon="🧮" title="제품을 선택하고 조회를 누르세요"
          hint={settings.scale_applied
            ? "설정: 원본 값에 scale 이 이미 곱해진 것으로 간주 — REAL 은 scale 없이, ADDP(수식)만 계산합니다."
            : "reformatter 규칙 CSV 의 REAL(abs/scale) → ADDP(수식) 순서로 index 컬럼이 계산됩니다."} />
      )}
      {result && (
        <>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
            <Pill tone="accent">{result.vehicle_csv}</Pill>
            {settings.scale_applied && (
              <Pill tone="warn" title="톱니바퀴 설정: 원본 값에 REAL scale 이 이미 곱해져 있어 다시 곱하지 않습니다">scale 기적용 원본</Pill>
            )}
            <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              index {result.index_columns?.length || 0}개 · 전체 {total.toLocaleString()}행 · {result.elapsed_ms}ms
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
              <Button disabled={!canPrev || busy} onClick={() => run(Math.max(0, offset - settings.page_rows))}>← 이전</Button>
              <span style={{ fontSize: 13, fontFamily: "monospace", color: "var(--text-secondary)" }}>
                {total ? `${(offset + 1).toLocaleString()}–${pageEnd.toLocaleString()} / ${total.toLocaleString()}` : "0"}
              </span>
              <Button disabled={!canNext || busy} onClick={() => run(offset + settings.page_rows)}>다음 →</Button>
            </span>
          </div>
          <ResultTable result={result} highlight={result.index_columns} />
        </>
      )}

      {/* 조회 로딩창 — 서버가 보고하는 실제 단계(어떤 parquet 을 몇 개 중 몇 번째로
          보고 있는지)를 그대로 띄운다. 숫자가 올라가는 게 보여야 멈춘 것과 구분된다. */}
      {busy && !dlJob && (
        <Loading overlay size="lg" text={runHeadline(runPhase)}
          steps={RUN_STEPS} activeStep={runPhase ? runStepIndex(runPhase) : null}>
          {runPhase && (runDetail(runPhase) || runPhase.total) && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, marginTop: 4 }}>
              {runDetail(runPhase) && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace",
                              maxWidth: 340, textAlign: "center", wordBreak: "break-all" }}>
                  {runDetail(runPhase)}
                </div>
              )}
              {runPhase.total > 0 && (
                <div style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
                  parquet {Math.min(Number(runPhase.done || 0) + 1, runPhase.total).toLocaleString()}
                  {" / "}{Number(runPhase.total).toLocaleString()}개
                </div>
              )}
            </div>
          )}
        </Loading>
      )}

      {/* 다운로드 대기열 진행창 — 대기 순번·현재 단계·경과 시간 + 취소 */}
      {dlJob && (
        <Loading overlay size="lg" text={dlHeadline(dlJob)} steps={DL_STEPS}
          activeStep={dlStepIndex(dlJob)}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, marginTop: 4 }}>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace", textAlign: "center" }}>
              {dlJob.product || product}
              {dlJob.state === "queued" && dlJob.current
                ? ` · 진행 중: ${dlJob.current.product || "다른 작업"} (${Math.round(dlJob.current.elapsed_sec || 0)}초)`
                : ""}
              {dlJob.queue_len > 0 ? ` · 대기열 ${dlJob.queue_len}건` : ""}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", fontFamily: "monospace" }}>
              경과 {Math.round(dlJob.total_sec || 0)}초
              {dlJob.rows ? ` · ${Number(dlJob.rows).toLocaleString()}행` : ""}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", maxWidth: 320, textAlign: "center" }}>
              여러 명이 동시에 요청하면 순서대로 처리됩니다. 계산은 격리 프로세스에서 실행되며 메모리 보호 기준 또는 180초를 넘으면 자동 중단됩니다. 중단 안내가 나오면 최근 N일·LOT·STEP 필터를 좁혀 주세요.
            </div>
            <Button onClick={cancelDownload}>취소</Button>
          </div>
        </Loading>
      )}

      {/* ⚙️ 페이지 설정 */}
      <PageGear title="ET 다운로드 설정" position="bottom-right">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <label style={{ fontSize: 13, fontWeight: 700 }}>공유 기본 주소
              <input type="url" placeholder="https://flow.example.com" value={(gearForm || settings).share_base_url || ""}
                onChange={e => setGearForm({ ...(gearForm || settings), share_base_url: e.target.value })}
                style={{ ...inputStyle, width: "100%", marginTop: 4 }} />
            </label>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>다른 사용자가 접속할 서버 주소를 저장하세요. ET 검색이력뿐만 아니라 인폼/이슈 등 메일 알림 및 타 페이지 공유 링크에도 공통 적용되며, 비워 두면 현재 접속 주소를 사용합니다.</div>
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>한 번에 조회할 행 수</div>
            <input type="number" min={10} max={5000}
              value={(gearForm || settings).page_rows}
              onChange={e => setGearForm({ ...(gearForm || settings), page_rows: e.target.value })}
              style={{ ...inputStyle, width: "100%" }} />
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>테이블 한 페이지에 표시할 행 수 (10~5,000)</div>
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>조회·CSV 다운로드 최대 용량 (MB)</div>
            <input type="number" min={10} max={10000}
              value={(gearForm || settings).max_download_mb ?? 500}
              onChange={e => setGearForm({ ...(gearForm || settings), max_download_mb: e.target.value })}
              style={{ ...inputStyle, width: "100%" }} />
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
              조회 및 CSV 다운로드 시 생성되는 최종 결과의 최대 허용 용량(MB)입니다. 초과 시 용량초과 안내와 함께 안전하게 중단됩니다. (10~10,000 MB)
            </div>
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>value 열 이름</div>
            <input type="text" placeholder="자동 감지 (value / et_value / meas_value)"
              value={(gearForm || settings).value_col || ""}
              onChange={e => setGearForm({ ...(gearForm || settings), value_col: e.target.value })}
              style={{ ...inputStyle, width: "100%" }} />
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
              ET 원본 parquet 의 측정값 열 이름. 비워 두면 자동 감지합니다. 지정하면 그 열만 사용합니다.
            </div>
          </div>
          <div>
            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, cursor: "pointer" }}>
              <input type="checkbox" style={{ marginTop: 3 }}
                checked={!!(gearForm || settings).scale_applied}
                onChange={e => setGearForm({ ...(gearForm || settings), scale_applied: e.target.checked })} />
              <span>
                <span style={{ fontSize: 13, fontWeight: 700 }}>scale factor 가 이미 곱해진 원본</span>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
                  원본 값(예: 제품_시간.parquet 의 et_value)에 reformatter 의 REAL scale 이 이미
                  적용된 경우 켭니다. REAL 단계에서 scale 을 다시 곱하지 않고, ADDP 수식은
                  그 값으로 평소처럼 계산합니다.
                </div>
              </span>
            </label>
          </div>
          <Button variant="primary" onClick={saveSettings}>저장</Button>
        </div>
      </PageGear>
      </div>
    </PageShell>
  );
}
