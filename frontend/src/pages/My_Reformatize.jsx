/* My_Reformatize.jsx — 업무 > ET Index 다운로드.
   auto report 의 reformatize 흐름을 화면으로 제공:
   DB ET 제품 선택 → data_root/reformatter/<vehicle>_reformatter.csv 규칙으로
   shot 단위 index 값 계산 → 테이블 조회(페이지) + CSV 다운로드.
   톱니바퀴(⚙️)에서 한 번에 조회할 행 수 / 다운로드 최대 행을 설정.

   관리자 전용 🧪 ADDP 수식 테스트: vehicle CSV 를 고치기 전에 새 ADDP ITEM(alias)
   + ADDP Form 을 실제 ET 데이터로 계산해 보고 CSV 로 추출한다.
   수식은 기존 alias 와 raw item 을 {이름} 으로 참조 (auto report 와 동일). */
import { useEffect, useMemo, useRef, useState } from "react";
import { sf, postJson, dl, qs } from "../lib/api";
import { toast } from "../components/Toast";
import PageGear from "../components/PageGear";
import { Banner, Button, EmptyState, PageHeader, Pill } from "../components/UXKit";

const API = "/api/reformatize";

const cell = { padding: "5px 10px", borderBottom: "1px solid var(--border)", fontSize: 13, whiteSpace: "nowrap" };
const head = {
  ...cell, position: "sticky", top: 0, background: "var(--bg-tertiary)",
  color: "var(--text-secondary)", fontWeight: 700, zIndex: 1,
};
const inputStyle = {
  background: "var(--bg-primary)", color: "var(--text-primary)", border: "1px solid var(--border)",
  borderRadius: 6, padding: "6px 10px", fontSize: 13,
};

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

function ResultTable({ result, highlight }) {
  const hi = useMemo(() => new Set(highlight || []), [highlight]);
  const spec = result.spec || {};
  const [selRule, setSelRule] = useState("");
  const clickable = (c) => hi.has(c) && spec[c];
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
                color: hi.has(c) ? "var(--accent)" : head.color,
                cursor: clickable(c) ? "pointer" : "default",
                textDecoration: clickable(c) ? "underline dotted" : "none",
                background: selRule === c ? "var(--accent-glow)" : head.background,
              }}
              title={clickable(c)
                ? (spec[c].category === "addp"
                    ? `ADDP: ${spec[c].addp_form || ""} — 클릭하여 상세`
                    : `REAL: ${spec[c].itemid || ""}${spec[c].abs ? " abs" : ""} ×${spec[c].scale ?? 1} — 클릭하여 상세`)
                : c}>
              {c}
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
                <td key={c} style={{ ...cell, fontFamily: "monospace", color: hi.has(c) ? "var(--text-primary)" : "var(--text-secondary)", fontWeight: hi.has(c) ? 600 : 400 }}>
                  {hi.has(c) ? fmtVal(row[c]) : String(row[c] ?? "")}
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

/* Index 항목 선택 패널 — vehicle CSV 의 REAL/ADDP 목록에서 뽑을 index 를 고른다.
   REAL 은 raw ITEMID·abs·scale factor, ADDP 는 ADDP Form·참조 컬럼을 함께 표시.
   기본 선택은 없음 — 몇 개만 고르거나 [REPORT ORDER 전체] 로 리포트 대상만 일괄 선택.
   (auto report 와 동일: REPORT ORDER 가 있는 항목만 리포트 item, 빈 항목은 판정용) */
function ItemSelectPanel({ items, selected, onToggle, onSelectSet }) {
  const [open, setOpen] = useState(true);
  if (!items.length) return null;
  const nSel = items.filter(it => selected.has(it.alias)).length;
  const reportItems = items.filter(it => it.report_order !== null && it.report_order !== undefined);
  const mono = { fontFamily: "monospace", fontSize: 12 };
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "10px 14px", marginBottom: 12, background: "var(--bg-secondary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }} onClick={() => setOpen(o => !o)}>
        <span style={{ fontSize: 14, fontWeight: 800 }}>📋 Index 항목 선택</span>
        <Pill tone={nSel === 0 ? "warn" : "accent"}>{nSel} / {items.length}</Pill>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          {nSel === 0 ? "뽑을 항목을 선택하세요 — REPORT ORDER 전체 버튼으로 리포트 대상만 일괄 선택 가능" : "REAL 은 raw × scale factor, ADDP 는 수식·참조 확인"}
        </span>
        <span style={{ marginLeft: "auto", color: "var(--text-secondary)" }}>{open ? "▲ 접기" : "▼ 펼치기"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Button variant="primary" onClick={() => onSelectSet(reportItems.map(it => it.alias))}
              title="auto report 와 동일 — REPORT ORDER 값이 있는 항목 전체 (리포트 대상 item)">
              REPORT ORDER 전체 ({reportItems.length})
            </Button>
            <Button onClick={() => onSelectSet(items.map(it => it.alias))}>전체 선택</Button>
            <Button onClick={() => onSelectSet([])}>전체 해제</Button>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              R.ORD 빈 항목은 리포트 제외(판정용) — 필요하면 개별 선택
            </span>
          </div>
          <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
            <table style={{ borderCollapse: "collapse", width: "100%" }}>
              <thead><tr>
                {["", "구분", "ITEM (alias)", "R.ORD", "계산 상세", "단위/spec"].map((h, i) => (
                  <th key={i} style={{ ...head, whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {items.map(it => (
                  <tr key={it.alias} onClick={() => onToggle(it.alias)}
                    style={{ cursor: "pointer", background: selected.has(it.alias) ? "transparent" : "var(--bg-primary)", opacity: selected.has(it.alias) ? 1 : 0.55 }}>
                    <td style={{ ...cell, width: 30 }}>
                      <input type="checkbox" readOnly checked={selected.has(it.alias)} style={{ accentColor: "var(--accent)" }} />
                    </td>
                    <td style={cell}><Pill tone={it.category === "addp" ? "warn" : "accent"}>{it.category === "addp" ? "ADDP" : "REAL"}</Pill></td>
                    <td style={{ ...cell, ...mono, fontWeight: 700, color: "var(--accent)" }}>{it.alias}</td>
                    <td style={{ ...cell, ...mono, color: it.report_order != null ? "var(--text-primary)" : "var(--text-secondary)" }}>
                      {it.report_order != null ? it.report_order : "-"}
                    </td>
                    <td style={{ ...cell, ...mono, whiteSpace: "normal", wordBreak: "break-all" }}>
                      {it.category === "addp" ? (
                        <>
                          <span>{it.addp_form}</span>
                          {(it.refs || []).length > 0 && (
                            <span style={{ color: "var(--text-secondary)" }}>{"  ← 참조: " + it.refs.join(", ")}</span>
                          )}
                        </>
                      ) : (
                        <span>{it.itemid}{it.abs ? " → abs" : ""} × <b>{it.scale ?? 1}</b><span style={{ color: "var(--text-secondary)" }}> (scale factor)</span></span>
                      )}
                    </td>
                    <td style={{ ...cell, ...mono, color: "var(--text-secondary)" }}>
                      {(it.unit ? `[${it.unit}] ` : "") + (it.speclow != null || it.spechigh != null ? `${it.speclow ?? "-"}~${it.spechigh ?? "-"}` : "")}
                      {it.target != null ? ` (t ${it.target})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

/* 관리자 전용 ADDP 수식 테스트 패널 */
function AddpTestPanel({ product, lotFilter, pageRows }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([{ alias: "", addp_form: "" }]);
  const [help, setHelp] = useState(null);
  const [result, setResult] = useState(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);
  const lastFocus = useRef(0);

  useEffect(() => { setResult(null); setOffset(0); setHelp(null); }, [product]);
  useEffect(() => {
    if (!open || !product || help) return;
    sf(API + "/formula-help?product=" + encodeURIComponent(product))
      .then(setHelp).catch(e => toast.error("도움말 로딩 실패: " + (e.message || e)));
  }, [open, product]);

  const setItem = (i, patch) => setItems(list => list.map((it, j) => j === i ? { ...it, ...patch } : it));
  const validItems = items.filter(it => it.alias.trim() && it.addp_form.trim());

  const insertRef = (name) => {
    setItems(list => {
      const i = Math.min(lastFocus.current, list.length - 1);
      return list.map((it, j) => j === i ? { ...it, addp_form: (it.addp_form || "") + "{" + name + "}" } : it);
    });
  };

  const run = (nextOffset = 0) => {
    if (!validItems.length) { toast.warn("alias 와 ADDP Form 을 입력하세요"); return; }
    setBusy(true);
    postJson(API + "/test", { product, items: validItems, lot_filter: lotFilter, offset: nextOffset, limit: pageRows })
      .then(d => { setResult(d); setOffset(d.offset || 0); })
      .catch(e => toast.error(e.message || "테스트 실패"))
      .finally(() => setBusy(false));
  };

  const download = () => {
    if (!validItems.length) { toast.warn("alias 와 ADDP Form 을 입력하세요"); return; }
    setDlBusy(true);
    dlPost(API + "/test/download", { product, items: validItems, lot_filter: lotFilter }, `${product}_addp_test.csv`)
      .then(() => toast.ok("테스트 CSV 다운로드 완료 — 이력은 관리자 > 다운로드 탭에 기록됩니다"))
      .catch(e => toast.error(e.message || "다운로드 실패"))
      .finally(() => setDlBusy(false));
  };

  const total = result?.total_rows || 0;
  const pageEnd = Math.min(offset + (result?.rows?.length || 0), total);
  const chip = (name, tone) => (
    <span key={name} onClick={() => insertRef(name)} title={`클릭하면 수식에 {${name}} 추가`}
      style={{ cursor: "pointer", fontSize: 12, fontFamily: "monospace", padding: "1px 7px", borderRadius: 999, border: "1px solid var(--border)", color: tone, marginRight: 4, marginBottom: 4, display: "inline-block" }}>
      {name}
    </span>
  );

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
              <input value={it.alias} onChange={e => setItem(i, { alias: e.target.value })} onFocus={() => { lastFocus.current = i; }}
                placeholder="ADDP ITEM (alias) — 예: MY_INDEX" style={{ ...inputStyle, width: 220, fontFamily: "monospace" }} />
              <input value={it.addp_form} onChange={e => setItem(i, { addp_form: e.target.value })} onFocus={() => { lastFocus.current = i; }}
                placeholder="ADDP Form — 예: ({VTH_IDX} - avg({VTH_IDX})) / std({VTH_IDX})"
                style={{ ...inputStyle, flex: 1, fontFamily: "monospace" }} />
              <Button onClick={() => setItems(list => list.length > 1 ? list.filter((_, j) => j !== i) : [{ alias: "", addp_form: "" }])}>✕</Button>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
            <Button onClick={() => setItems(list => [...list, { alias: "", addp_form: "" }])}>＋ 항목 추가</Button>
            <Button variant="primary" disabled={busy || !product} onClick={() => run(0)}>{busy ? "계산 중…" : "테스트 실행"}</Button>
            <Button disabled={dlBusy || !product} onClick={download}>{dlBusy ? "다운로드 중…" : "⬇ 테스트 CSV"}</Button>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>lot 필터·행 수 설정은 상단 조회 조건을 따릅니다</span>
          </div>

          {/* 도움말: 참조 가능한 컬럼 + 함수 */}
          {help && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", borderTop: "1px dashed var(--border)", paddingTop: 8, marginBottom: 10 }}>
              <div style={{ marginBottom: 4 }}>
                <b>참조 가능 index alias</b> (클릭하여 수식에 추가):{" "}
                {(help.columns?.aliases || []).map(a => chip(a, "var(--accent)"))}
              </div>
              <div style={{ marginBottom: 4 }}>
                <b>raw ITEMID</b>: {(help.columns?.raw_items || []).map(a => chip(a, "var(--text-primary)"))}
              </div>
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
  const [lotFilter, setLotFilter] = useState("");
  const [settings, setSettings] = useState({ page_rows: 500, max_download_rows: 100000 });
  const [gearForm, setGearForm] = useState(null);
  const [result, setResult] = useState(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);
  const [itemList, setItemList] = useState([]);          // vehicle CSV 의 REAL/ADDP 항목
  const [selItems, setSelItems] = useState(new Set());   // 선택된 alias (기본 전체)

  useEffect(() => {
    sf(API + "/products").then(d => {
      const list = d.products || [];
      setProducts(list);
      if (list.length && !product) setProduct(list[0].product);
    }).catch(e => toast.error("제품 목록 로딩 실패: " + (e.message || e)));
    sf(API + "/settings").then(d => setSettings(s => ({ ...s, ...d }))).catch(() => {});
  }, []);

  const selected = products.find(p => p.product === product);

  // 제품 변경 시 vehicle CSV 의 REAL/ADDP 항목 목록 로드 — 기본 선택 없음 (직접 고르거나 REPORT ORDER 전체).
  useEffect(() => {
    setItemList([]); setSelItems(new Set());
    if (!product || !selected?.vehicle_csv) return;
    sf(API + "/items?product=" + encodeURIComponent(product))
      .then(d => setItemList(d.items || []))
      .catch(() => {});
  }, [product, selected?.vehicle_csv]);

  const allSelected = itemList.length > 0 && selItems.size === itemList.length;
  const selArray = () => (allSelected ? [] : [...selItems]);   // 전체 선택이면 서버 기본(전체) 사용

  const run = (nextOffset = 0) => {
    if (!product) { toast.warn("제품을 선택하세요"); return; }
    if (itemList.length && selItems.size === 0) { toast.warn("Index 항목을 하나 이상 선택하세요"); return; }
    setBusy(true);
    postJson(API + "/run", { product, offset: nextOffset, limit: settings.page_rows, lot_filter: lotFilter, items: selArray() })
      .then(d => { setResult(d); setOffset(d.offset || 0); })
      .catch(e => toast.error(e.message || "조회 실패"))
      .finally(() => setBusy(false));
  };

  const download = () => {
    if (!product) return;
    if (itemList.length && selItems.size === 0) { toast.warn("Index 항목을 하나 이상 선택하세요"); return; }
    setDlBusy(true);
    dl(API + "/download" + qs({ product, lot_filter: lotFilter, items: selArray().join(",") }), `${product}_reformatize.csv`)
      .then(() => toast.ok("다운로드 완료 — 이력은 관리자 > 다운로드 탭에 기록됩니다"))
      .catch(e => toast.error(e.message || "다운로드 실패"))
      .finally(() => setDlBusy(false));
  };

  const saveSettings = () => {
    const form = gearForm || settings;
    postJson(API + "/settings", {
      page_rows: Number(form.page_rows) || 500,
      max_download_rows: Number(form.max_download_rows) || 100000,
    }).then(d => {
      setSettings({ page_rows: d.page_rows, max_download_rows: d.max_download_rows });
      setGearForm(null);
      toast.ok("설정 저장됨");
    }).catch(e => toast.error(e.message || "설정 저장 실패"));
  };

  const total = result?.total_rows || 0;
  const pageEnd = Math.min(offset + (result?.rows?.length || 0), total);
  const canPrev = offset > 0;
  const canNext = pageEnd < total;

  return (
    <div style={{ padding: "24px 32px", background: "var(--bg-primary)", minHeight: "calc(100vh - 52px)", color: "var(--text-primary)", fontFamily: "'Pretendard',sans-serif" }}>
      <PageHeader
        title="ET Index 다운로드"
        subtitle="DB ET 데이터를 vehicle reformatter 규칙(REAL/ADDP)으로 계산해 shot 단위 index 값을 추출합니다."
        right={<Pill tone="neutral" size="md">{user?.username || "guest"}</Pill>}
        style={{ borderRadius: 10, border: "1px solid var(--border)", marginBottom: 14 }}
      />

      {/* 조회 조건 */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>제품 (DB ET)</span>
        <select value={product} onChange={e => { setProduct(e.target.value); setResult(null); setOffset(0); }} style={{ ...inputStyle, minWidth: 160 }}>
          {products.length === 0 && <option value="">제품 없음</option>}
          {products.map(p => <option key={p.product} value={p.product}>{p.product}</option>)}
        </select>
        {selected && (selected.vehicle_csv
          ? <Pill tone="ok" title="적용되는 reformatter 규칙 파일">{selected.vehicle_csv}</Pill>
          : <Pill tone="warn" title="data_root/reformatter/ 에 <vehicle>_reformatter.csv 를 추가하세요">규칙 CSV 없음</Pill>)}
        <input value={lotFilter} onChange={e => setLotFilter(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") run(0); }}
          placeholder="lot 필터 (root_lot_id 포함 검색)" style={{ ...inputStyle, minWidth: 220 }} />
        <Button variant="primary" disabled={busy || !product} onClick={() => run(0)}>{busy ? "계산 중…" : "조회"}</Button>
        <Button disabled={dlBusy || !product || !selected?.vehicle_csv} onClick={download}>{dlBusy ? "다운로드 중…" : "⬇ CSV 다운로드"}</Button>
      </div>

      {/* Index 항목 선택 — REAL(scale factor)/ADDP(form·참조) 확인 후 뽑을 항목 선택 */}
      <ItemSelectPanel
        items={itemList}
        selected={selItems}
        onToggle={(alias) => setSelItems(prev => { const s = new Set(prev); s.has(alias) ? s.delete(alias) : s.add(alias); return s; })}
        onSelectSet={(aliases) => setSelItems(new Set(aliases))}
      />

      {/* 관리자 전용 ADDP 수식 테스트 */}
      {isAdmin && product && selected?.vehicle_csv && (
        <AddpTestPanel product={product} lotFilter={lotFilter} pageRows={settings.page_rows} />
      )}

      {/* 규칙 에러 */}
      {result?.rule_errors?.length > 0 && (
        <Banner tone="warn" style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>규칙 경고 ({result.rule_errors.length})</div>
          {result.rule_errors.map((e, i) => <div key={i} style={{ fontSize: 12, fontFamily: "monospace" }}>{e}</div>)}
        </Banner>
      )}

      {/* 결과 테이블 */}
      {!result && !busy && (
        <EmptyState icon="🧮" title="제품을 선택하고 조회를 누르세요"
          hint="reformatter 규칙 CSV 의 REAL(abs/scale) → ADDP(수식) 순서로 index 컬럼이 계산됩니다." />
      )}
      {result && (
        <>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
            <Pill tone="accent">{result.vehicle_csv}</Pill>
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

      {/* ⚙️ 페이지 설정 */}
      <PageGear title="ET Index 다운로드 설정" position="bottom-right">
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
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>CSV 다운로드 최대 행</div>
            <input type="number" min={100} max={1000000}
              value={(gearForm || settings).max_download_rows}
              onChange={e => setGearForm({ ...(gearForm || settings), max_download_rows: e.target.value })}
              style={{ ...inputStyle, width: "100%" }} />
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>초과 시 lot 필터를 걸어 행을 줄여야 다운로드됩니다 (100~1,000,000)</div>
          </div>
          <Button variant="primary" onClick={saveSettings}>저장</Button>
        </div>
      </PageGear>
    </div>
  );
}
