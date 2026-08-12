/* My_Reformatize.jsx — 업무 > ET 다운로드.
   auto report 의 reformatize 흐름을 화면으로 제공:
   DB ET 제품 선택 → data_root/reformatter/<vehicle>_reformatter.csv 규칙으로
   shot 단위 index 값 계산 → 테이블 조회(페이지) + CSV 다운로드.
   톱니바퀴(⚙️)에서 한 번에 조회할 행 수 / 다운로드 최대 행을 설정.

   관리자 전용 🧪 ADDP 수식 테스트: vehicle CSV 를 고치기 전에 새 ADDP ITEM(alias)
   + ADDP Form 을 실제 ET 데이터로 계산해 보고 CSV 로 추출한다.
   수식은 기존 alias 와 raw item 을 {이름} 으로 참조 (auto report 와 동일). */
import { useEffect, useMemo, useState } from "react";
import { sf, postJson, dl, qs } from "../lib/api";
import { toast } from "../components/Toast";
import Loading from "../components/Loading";
import PageGear from "../components/PageGear";
import usePolling from "../hooks/usePolling";
import { Banner, Button, EmptyState, PageShell, Pill } from "../components/UXKit";

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
  lot_filter: "", step_filter: "", step_seq_filter: "", wafer_filter: "", site_cnt_filter: "",
  days: "", date_from: "", date_to: "",
};

function filterBody(filters) {
  return {
    lot_filter: filters.lot_filter.trim(),
    step_filter: filters.step_filter.trim(),
    step_seq_filter: filters.step_seq_filter.trim(),
    wafer_filter: filters.wafer_filter.trim(),
    site_cnt_filter: filters.site_cnt_filter.trim(),
    days: Number(filters.days) || 0,
    date_from: filters.date_from,
    date_to: filters.date_to,
  };
}

function hasAnyFilter(filters) {
  const f = filterBody(filters);
  return Boolean(f.lot_filter || f.step_filter || f.step_seq_filter || f.wafer_filter || f.site_cnt_filter
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
    a.download = filename || "download.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
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
              {c}
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
    if (!hasAnyFilter(filters)) { toast.warn("다운로드는 필터가 필요합니다 — 상단 조회 조건에서 기간·lot 등 필터를 지정하세요"); return; }
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

export default function My_Reformatize({ user }) {
  const isAdmin = user?.role === "admin";
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [settings, setSettings] = useState({ page_rows: 500, max_download_rows: 100000, value_col: "", scale_applied: false });
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
  // 진행 폴링은 공용 훅에 위임한다 — 타이머 정리·연속 실패 중단(무한 로딩 방지)이
  // 화면마다 재구현되던 부분. 다운로드와 조회는 서로 독립이라 인스턴스를 나눈다.
  const poll = usePolling();
  const runPoll = usePolling();

  const clearJob = () => { poll.stop(); setDlJob(null); setDlBusy(false); };

  // 완료된 작업의 CSV 를 실제로 내려받는다 (이 시점에 downloads.jsonl 기록).
  const fetchResult = (job) => {
    setDlJob({ ...job, phase: "파일 저장 중" });
    dl(API + "/download/file" + qs({ job_id: job.job_id }), job.filename || `${job.product || product}_reformatize.csv`)
      .then(() => toast.ok(`다운로드 완료 — ${(job.rows || 0).toLocaleString()}행 · 이력은 관리자 > 다운로드 탭에 기록됩니다`))
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
      if (list.length && !product) setProduct(list[0].product);
    }).catch(e => toast.error("제품 목록 로딩 실패: " + (e.message || e)));
    sf(API + "/settings").then(d => setSettings(s => ({ ...s, ...d }))).catch(() => {});
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
    setItemList([]); setSelItems(new Set()); setHiddenItems(new Set());
    if (!product || !selected?.vehicle_csv) return;
    sf(API + "/items?product=" + encodeURIComponent(product))
      .then(d => {
        const items = d.items || [];
        setItemList(items);
        setHiddenItems(new Set(items.filter(it => it.hidden).map(it => it.alias)));
      })
      .catch(() => {});
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

  const setF = (patch) => setFilters(f => ({ ...f, ...patch }));
  const onFilterEnter = (e) => {
    if (e.key !== "Enter") return;
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return;
    run(0);
  };

  const run = (nextOffset = 0) => {
    if (!product) { toast.warn("제품을 선택하세요"); return; }
    if (itemList.length && selItems.size === 0) { toast.warn("Index 항목을 하나 이상 선택하세요"); return; }
    const token = `run${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    setBusy(true);
    setRunPhase(null);
    // 계산과 병렬로 진행 상황을 폴링한다. 실패해도 조회는 그대로 진행되고
    // 로딩창은 기본 문구로 남는다 — 진행 표시 때문에 조회를 깨지 않는다.
    runPoll.start(() => sf(API + "/run/progress" + qs({ token })), {
      intervalMs: RUN_POLL_MS,
      maxErrors: 5,
      onData: (d) => { if (d?.active) setRunPhase(d); },
      onError: () => runPoll.stop(),
    });
    postJson(API + "/run", { product, offset: nextOffset, limit: settings.page_rows, ...filterBody(filters), items: selArray(), agg, progress_token: token })
      .then(d => { setResult(d); setOffset(d.offset || 0); })
      .catch(e => toast.error(e.message || "조회 실패"))
      .finally(() => { runPoll.stop(); setRunPhase(null); setBusy(false); });
  };

  const download = () => {
    if (!product) return;
    if (itemList.length && selItems.size === 0) { toast.warn("Index 항목을 하나 이상 선택하세요"); return; }
    if (!hasAnyFilter(filters)) {
      toast.warn("필터 없이 전체 다운로드는 허용되지 않습니다 — 기간(tkout_time)·root_lot_id·step_id·step_seq 등 필터를 지정해 행 수를 줄이세요");
      return;
    }
    // 다운로드는 대기열 작업으로 등록한다 — 여러 명이 동시에 걸어도 서버는
    // 한 건씩 처리하고, 화면은 job 상태를 폴링해 진행을 보여준다.
    setDlBusy(true);
    postJson(API + "/download/start", { product, ...filterBody(filters), items: selArray(), agg })
      .then(d => {
        setDlJob(d);
        if (d.duplicate) toast.warn("같은 조건의 다운로드가 이미 진행 중입니다 — 그 작업을 이어서 표시합니다");
        else if (d.ahead > 0) toast.ok(`다른 다운로드가 진행 중이라 대기열에 등록했습니다 (앞에 ${d.ahead}건)`);
        pollJob(d.job_id);
      })
      .catch(e => { clearJob(); toast.error(e.message || "다운로드 요청 실패"); });
  };

  const saveSettings = () => {
    const form = gearForm || settings;
    postJson(API + "/settings", {
      page_rows: Number(form.page_rows) || 500,
      max_download_rows: Number(form.max_download_rows) || 100000,
      value_col: String(form.value_col || "").trim(),
      scale_applied: !!form.scale_applied,
    }).then(d => {
      setSettings({ page_rows: d.page_rows, max_download_rows: d.max_download_rows,
                    value_col: d.value_col || "", scale_applied: !!d.scale_applied });
      setGearForm(null);
      toast.ok("설정 저장됨");
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

      <div style={{ padding: "24px 32px" }}>
      <details style={{ marginBottom: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-secondary)", padding: "10px 14px" }}>
        <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>
          사용 가이드
        </summary>
        <ol style={{ margin: "10px 0 2px", paddingLeft: 22, color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.8 }}>
          <li><b style={{ color: "var(--text-primary)" }}>제품 선택</b> — DB ET 제품을 고르고 적용되는 reformatter 규칙 CSV를 확인합니다.</li>
          <li><b style={{ color: "var(--text-primary)" }}>조회 범위 지정</b> — 최근 N일 또는 시작~종료일을 입력하고, 필요하면 root_lot_id·step_id·step_seq·wafer_id·total_site_cnt로 더 좁힙니다.</li>
          <li><b style={{ color: "var(--text-primary)" }}>Index 선택</b> — 아래 목록에서 필요한 REAL·ADDP Index를 하나 이상 선택합니다.</li>
          <li><b style={{ color: "var(--text-primary)" }}>집계 방식 선택</b> — shot 원본이 필요하면 shot raw를, PGM 단위 요약이 필요하면 MAX·MIN·MEDIAN·AVG·STD·P90·P10 중 하나를 선택합니다.</li>
          <li><b style={{ color: "var(--text-primary)" }}>조회 또는 다운로드</b> — 화면 확인은 조회, 전체 결과 저장은 CSV 다운로드를 사용합니다. CSV 다운로드에는 필터가 하나 이상 필요합니다.</li>
          {isAdmin && <li><b style={{ color: "var(--text-primary)" }}>ADDP 수식 테스트</b> — 새 alias와 수식을 입력하면 같은 필터와 집계 방식으로 미리 계산하고 테스트 CSV를 받을 수 있습니다.</li>}
        </ol>
      </details>

      {/* 조회 조건 */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>제품</span>
        <select value={product} onChange={e => { setProduct(e.target.value); setResult(null); setOffset(0); }} style={{ ...inputStyle, minWidth: 160 }}>
          {products.length === 0 && <option value="">제품 없음</option>}
          {products.map(p => <option key={p.product} value={p.product}>{p.product}</option>)}
        </select>
        {selected && !selected.vehicle_csv && (
          <Pill tone="warn" title="data_root/reformatter/ 에 <vehicle>_reformatter.csv 를 추가하세요">규칙 CSV 없음</Pill>
        )}
        <Button variant="primary" disabled={busy || !product} onClick={() => run(0)}>{busy ? "계산 중…" : "조회"}</Button>
        <Button disabled={dlBusy || !product || !selected?.vehicle_csv} onClick={download}
          title={hasAnyFilter(filters) ? "필터 적용 결과를 CSV 로 다운로드 (여러 명이 동시에 걸면 순서대로 처리됩니다)" : "다운로드는 필터가 하나 이상 필요합니다"}>
          {dlBusy ? (dlJob?.state === "queued" ? "대기 중…" : "다운로드 준비 중…") : "⬇ CSV 다운로드"}
        </Button>
        {!hasAnyFilter(filters) && (
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            ⬇ 다운로드는 필터(기간·lot 등)를 하나 이상 지정해야 합니다
          </span>
        )}
      </div>

      {/* 필터 — tkout_time 기간 + root_lot_id/step_id/step_seq/wafer_id/total_site_cnt.
          최근 N일과 시작~종료일은 상호 배타 (한쪽 입력 시 다른 쪽 초기화). */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
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
          placeholder="total_site_cnt (쉼표=OR, 일치)" style={{ ...inputStyle, width: 180 }} />
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>집계</span>
        <select value={agg} onChange={e => { setAgg(e.target.value); setResult(null); setOffset(0); }}
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

      {/* Index 항목 선택 — REAL(scale factor)/ADDP(form·참조) 확인 후 뽑을 항목 선택 */}
      <ItemSelectPanel
        items={itemList}
        selected={selItems}
        onToggle={(alias) => setSelItems(prev => {
          const key = aliasKey(alias);
          const s = new Set([...prev].filter(a => aliasKey(a) !== key));
          if (s.size === prev.size) s.add(alias);           // 없던 항목이면 추가
          return s;
        })}
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
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>한 번에 조회할 행 수</div>
            <input type="number" min={10} max={5000}
              value={(gearForm || settings).page_rows}
              onChange={e => setGearForm({ ...(gearForm || settings), page_rows: e.target.value })}
              style={{ ...inputStyle, width: "100%" }} />
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>테이블 한 페이지에 표시할 행 수 (10~5,000)</div>
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>조회·CSV 다운로드 최대 raw 행</div>
            <input type="number" min={100} max={1000000}
              value={(gearForm || settings).max_download_rows}
              onChange={e => setGearForm({ ...(gearForm || settings), max_download_rows: e.target.value })}
              style={{ ...inputStyle, width: "100%" }} />
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>초과 시 pivot 전 중단됩니다. 기간·lot 등 필터를 조정하거나 상한을 올리세요 (100~1,000,000)</div>
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
