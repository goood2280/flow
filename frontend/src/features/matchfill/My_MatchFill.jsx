/* My_MatchFill.jsx — 매칭 테이블 product / module 채우기.
 *
 * Vehicle_matching / Inline_matching 의 `product` 열을 raw DB(FAB/INLINE) 제품 폴더
 * 스캔으로 채운다. vm_matching 은 product 를 채우지 않는다 — 제품 귀속과 module 은
 * Vehicle_matching.csv 가 step_desc 로 이미 정해 두므로 그쪽 module 을 그대로 가져온다.
 * 검사는 파일을 건드리지 않고 **제안**만 만들며, 관리자가 행 단위로 확인한 뒤
 * "반영"에서만 CSV 가 바뀐다.
 *
 * step_id 앞 두 글자로 검사할 제품을 좁히는 규칙(prefix_rules)은 엔지니어가 여기서 관리한다.
 */
import { useEffect, useMemo, useState } from "react";
import { sf, postJson } from "../../lib/api";
import { canManagePage } from "../../lib/permissions";
import { toast } from "../../components/Toast";
import Loading from "../../components/Loading";
import { Btn, Input, Pill, PageHeader, PageShell, statusPalette } from "../../components/UXKit";

const API = "/api/matching-fill";
const OK = statusPalette.ok;
const WARN = statusPalette.warn;
const BAD = statusPalette.bad;
const INFO = statusPalette.info;

const STATUS_META = {
  fill: { label: "채움", tone: OK, hint: "비어 있던 product 를 새로 채웁니다" },
  change: { label: "변경", tone: WARN, hint: "기존 값과 다릅니다 — 확인 후 반영하세요" },
  same: { label: "동일", tone: INFO, hint: "이미 같은 값이라 반영 대상이 아닙니다" },
  miss: { label: "미검출", tone: BAD, hint: "어느 제품 DB 에서도 찾지 못했습니다" },
};

const th = {
  padding: "7px 10px", textAlign: "left", fontSize: 13, fontWeight: 800,
  color: "var(--text-secondary)", borderBottom: "1px solid var(--border)",
  whiteSpace: "nowrap", position: "sticky", top: 0, background: "var(--bg-secondary)", zIndex: 1,
};
const td = {
  padding: "5px 10px", fontSize: 13, borderBottom: "1px solid var(--border)",
  fontFamily: "'JetBrains Mono',monospace", whiteSpace: "nowrap",
};

function StatusPill({ status }) {
  const m = STATUS_META[status] || STATUS_META.miss;
  return (
    <span title={m.hint} style={{
      display: "inline-flex", padding: "2px 9px", borderRadius: 999, fontSize: 12, fontWeight: 800,
      border: `1px solid ${m.tone.fg}`, color: m.tone.fg, background: m.tone.bg,
    }}>{m.label}</span>
  );
}

/* step_id 앞 두 글자 → 검사할 제품. 규칙이 없으면 전체 제품을 본다. */
function PrefixRules({ rules, products, canEdit, onSave }) {
  const [draft, setDraft] = useState(() => (rules || []).map(r => ({ ...r })));
  useEffect(() => { setDraft((rules || []).map(r => ({ ...r }))); }, [rules]);
  const setField = (i, key, v) => setDraft(d => d.map((r, j) => (j === i ? { ...r, [key]: v } : r)));
  const toggleProduct = (i, p) => setDraft(d => d.map((r, j) => {
    if (j !== i) return r;
    const cur = r.products || [];
    return { ...r, products: cur.includes(p) ? cur.filter(x => x !== p) : [...cur, p] };
  }));
  return (
    <section style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)", padding: 12, display: "grid", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b>검사 범위 규칙</b>
        <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
          step_id 앞 두 글자가 이 값이면 지정한 제품만 검사합니다 (규칙 없으면 전체 제품).
        </span>
      </div>
      {draft.map((rule, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", padding: 8, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
          <Input value={rule.prefix || ""} disabled={!canEdit} placeholder="AA"
            onChange={e => setField(i, "prefix", e.target.value.toUpperCase().slice(0, 8))}
            style={{ width: 80, fontFamily: "monospace", fontWeight: 800 }} />
          <span style={{ color: "var(--text-secondary)" }}>→</span>
          {(products || []).map(p => {
            const on = (rule.products || []).includes(p);
            return (
              <button key={p} type="button" disabled={!canEdit} onClick={() => toggleProduct(i, p)}
                style={{ padding: "4px 10px", borderRadius: 999, cursor: canEdit ? "pointer" : "default", fontFamily: "monospace", fontWeight: 800,
                  border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                  background: on ? "var(--accent)" : "var(--bg-card)", color: on ? "#fff" : "var(--text-secondary)" }}>
                {p}
              </button>
            );
          })}
          {(rule.products || []).filter(p => !(products || []).includes(p)).map(p => (
            <span key={p} title="현재 DB 에 없는 제품" style={{ padding: "4px 10px", borderRadius: 999, border: `1px dashed ${WARN.fg}`, color: WARN.fg, fontFamily: "monospace" }}>{p}</span>
          ))}
          <Btn size="sm" variant="danger" disabled={!canEdit} style={{ marginLeft: "auto" }}
            onClick={() => setDraft(d => d.filter((_, j) => j !== i))}>×</Btn>
        </div>
      ))}
      {draft.length === 0 && <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>규칙 없음 — 모든 제품을 검사합니다.</div>}
      <div style={{ display: "flex", gap: 6 }}>
        <Btn size="sm" variant="outline" disabled={!canEdit}
          onClick={() => setDraft(d => [...d, { prefix: "", products: [], targets: [] }])}>+ 규칙 추가</Btn>
        <Btn size="sm" variant="primary" disabled={!canEdit}
          onClick={() => onSave(draft.filter(r => String(r.prefix || "").trim() && (r.products || []).length))}>저장</Btn>
      </div>
    </section>
  );
}

/* step_id 앞 영문자 2글자별 번호 구간표 — 100000 PC, 200000 RPMG 를 넣으면
   100000~199999 는 PC, 200000 부터는 RPMG 가 된다(다음 구간 직전까지). */
function ModuleRules({ rules, canEdit, onSave }) {
  const [draft, setDraft] = useState(() => (rules || []).map(r => ({ ...r, breaks: (r.breaks || []).map(b => ({ ...b })) })));
  useEffect(() => { setDraft((rules || []).map(r => ({ ...r, breaks: (r.breaks || []).map(b => ({ ...b })) }))); }, [rules]);
  const setPrefix = (i, v) => setDraft(d => d.map((r, j) => (j === i ? { ...r, prefix: v.toUpperCase().slice(0, 8) } : r)));
  const setBreak = (i, k, key, v) => setDraft(d => d.map((r, j) => (j !== i ? r : {
    ...r, breaks: r.breaks.map((b, m) => (m === k ? { ...b, [key]: v } : b)),
  })));
  const rangeText = (breaks, k) => {
    const sorted = [...(breaks || [])].map(b => ({ ...b, from: Number(b.from) || 0 })).sort((a, b) => a.from - b.from);
    const idx = sorted.findIndex(b => String(b.from) === String(Number(breaks[k].from) || 0));
    if (idx < 0) return "";
    const next = sorted[idx + 1];
    return next ? `${sorted[idx].from} ~ ${next.from - 1}` : `${sorted[idx].from} ~`;
  };
  return (
    <section style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)", padding: 12, display: "grid", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b>module 구간표</b>
        <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
          step_id 앞 영문자 2글자가 같은 것끼리, 뒤 숫자의 시작 번호로 구간을 나눕니다 (다음 시작 직전까지).
        </span>
      </div>
      {draft.map((rule, i) => (
        <div key={i} style={{ display: "grid", gap: 6, padding: 8, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Input value={rule.prefix || ""} disabled={!canEdit} placeholder="AA"
              onChange={e => setPrefix(i, e.target.value)}
              style={{ width: 80, fontFamily: "monospace", fontWeight: 800 }} />
            <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>로 시작하는 step</span>
            <Btn size="sm" variant="danger" disabled={!canEdit} style={{ marginLeft: "auto" }}
              onClick={() => setDraft(d => d.filter((_, j) => j !== i))}>×</Btn>
          </div>
          {(rule.breaks || []).map((b, k) => (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <Input value={b.from ?? ""} disabled={!canEdit} placeholder="100000"
                onChange={e => setBreak(i, k, "from", e.target.value.replace(/[^0-9]/g, ""))}
                style={{ width: 110, fontFamily: "monospace" }} />
              <span style={{ color: "var(--text-secondary)" }}>→</span>
              <Input value={b.module || ""} disabled={!canEdit} placeholder="PC"
                onChange={e => setBreak(i, k, "module", e.target.value)}
                style={{ width: 140, fontWeight: 800 }} />
              <span style={{ color: "var(--text-secondary)", fontFamily: "monospace", fontSize: 13 }}>
                {rangeText(rule.breaks, k)}
              </span>
              <Btn size="sm" variant="ghost" disabled={!canEdit}
                onClick={() => setDraft(d => d.map((r, j) => (j === i ? { ...r, breaks: r.breaks.filter((_, m) => m !== k) } : r)))}>×</Btn>
            </div>
          ))}
          <Btn size="sm" variant="outline" disabled={!canEdit} style={{ justifySelf: "start" }}
            onClick={() => setDraft(d => d.map((r, j) => (j === i ? { ...r, breaks: [...(r.breaks || []), { from: "", module: "" }] } : r)))}>
            + 구간 추가
          </Btn>
        </div>
      ))}
      {draft.length === 0 && <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>구간표 없음 — module 채우기를 하려면 먼저 등록하세요.</div>}
      <div style={{ display: "flex", gap: 6 }}>
        <Btn size="sm" variant="outline" disabled={!canEdit}
          onClick={() => setDraft(d => [...d, { prefix: "", breaks: [{ from: "", module: "" }] }])}>+ prefix 추가</Btn>
        <Btn size="sm" variant="primary" disabled={!canEdit}
          onClick={() => onSave(draft
            .map(r => ({ prefix: r.prefix, breaks: (r.breaks || []).filter(b => String(b.from).trim() && String(b.module).trim()) }))
            .filter(r => String(r.prefix || "").trim() && r.breaks.length))}>저장</Btn>
      </div>
    </section>
  );
}

export default function My_MatchFill({ user }) {
  const canManage = canManagePage(user, "matchfill");
  const [targets, setTargets] = useState([]);
  const [dbRoot, setDbRoot] = useState("");
  const [active, setActive] = useState("vehicle");
  const [column, setColumn] = useState("product");   // 채울 열: product | module
  const [settings, setSettings] = useState({ prefix_rules: [], module_rules: [], max_files_per_product: 0 });
  const [proposal, setProposal] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [skip, setSkip] = useState([]);          // 반영 제외 행 index
  const [onlyChanges, setOnlyChanges] = useState(true);
  const [search, setSearch] = useState("");

  const current = targets.find(t => t.target === active) || null;
  // 대상마다 채울 수 있는 열이 다르다 — vm_matching 은 product 를 채우지 않는다
  // (제품 귀속은 Vehicle_matching.csv 가 step_desc 로 이미 정한다).
  // 아래 effect 의 의존성이라 참조가 매 렌더 바뀌지 않게 memo 한다.
  const fillColumns = useMemo(
    () => current?.fill_columns || ["product", "module"],
    [current],
  );
  // module 원천: step 번호 구간표(step_range) vs Vehicle_matching step_desc.
  const moduleSource = current?.module_source || "step_range";
  const byStepDesc = moduleSource === "vehicle_step_desc";

  const loadTargets = () => sf(`${API}/targets`).then(d => {
    setTargets(d.targets || []);
    setDbRoot(d.db_root || "");
  }).catch(e => toast.error("대상 조회 실패: " + (e.message || e)));

  useEffect(() => {
    loadTargets();
    sf(`${API}/settings`).then(setSettings).catch(() => {});
  }, []);

  // 대상이 바뀌어 지금 고른 열을 그 대상에서 못 채우면 허용된 첫 열로 되돌린다
  // (vm_matching 을 고른 채 product 탭에 남아 있으면 검사가 400 으로 떨어진다).
  useEffect(() => {
    if (current && !fillColumns.includes(column)) setColumn(fillColumns[0] || "module");
  }, [current, fillColumns, column]);

  useEffect(() => {
    setSkip([]);
    sf(`${API}/proposal?target=${encodeURIComponent(active)}&column=${encodeURIComponent(column)}`)
      .then(d => setProposal(d.proposal || null))
      .catch(() => setProposal(null));
  }, [active, column]);

  const runScan = () => {
    setScanning(true);
    postJson(`${API}/scan`, { target: active, column })
      .then(d => {
        setProposal(d.proposal);
        setSkip([]);
        const c = d.proposal?.counts || {};
        toast.ok(`검사 완료 — 채움 ${c.fill || 0} · 변경 ${c.change || 0} · 동일 ${c.same || 0} · 미검출 ${c.miss || 0}`);
        loadTargets();
      })
      .catch(e => toast.error("검사 실패: " + (e.message || e)))
      .finally(() => setScanning(false));
  };

  const applyProposal = () => {
    const n = applyRows.length;
    if (!n) { toast.warn("반영할 행이 없습니다."); return; }
    if (!window.confirm(`${current?.file} 에 ${n}행을 반영합니다.\n이 파일은 SplitTable·Valve·인폼이 함께 읽습니다. 진행할까요?`)) return;
    setApplying(true);
    postJson(`${API}/apply`, { target: active, column, skip_rows: skip })
      .then(d => {
        toast.ok(`${d.file} ${d.changed}행 반영됨${d.added_column ? " (product 열 새로 생성)" : ""}`);
        setProposal(p => (p ? { ...p, applied: true } : p));
        loadTargets();
      })
      .catch(e => toast.error("반영 실패: " + (e.message || e)))
      .finally(() => setApplying(false));
  };

  const discard = () => {
    postJson(`${API}/discard`, { target: active, column })
      .then(() => { setProposal(null); setSkip([]); loadTargets(); })
      .catch(e => toast.error("폐기 실패: " + (e.message || e)));
  };

  const saveRules = (rules) => {
    postJson(`${API}/settings`, { prefix_rules: rules })
      .then(setSettings)
      .then(() => toast.ok("검사 범위 규칙 저장됨"))
      .catch(e => toast.error("규칙 저장 실패: " + (e.message || e)));
  };
  const saveModuleRules = (rules) => {
    postJson(`${API}/settings`, { module_rules: rules })
      .then(setSettings)
      .then(() => toast.ok("module 구간표 저장됨"))
      .catch(e => toast.error("구간표 저장 실패: " + (e.message || e)));
  };

  const rows = proposal?.rows || [];
  const applyRows = useMemo(
    () => rows.filter(r => ["fill", "change"].includes(r.status) && !skip.includes(r.i)),
    [rows, skip],
  );
  const visibleRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows.filter(r => {
      if (onlyChanges && !["fill", "change"].includes(r.status)) return false;
      if (!needle) return true;
      const hay = [...Object.values(r.id || {}), ...Object.values(r.keys || {}), r.current, r.proposed]
        .join(" ").toLowerCase();
      return hay.includes(needle);
    });
  }, [rows, onlyChanges, search]);
  const idCols = Object.keys(rows[0]?.id || {});

  return (
    <PageShell>
      <PageHeader
        title="매칭 채우기"
        subtitle={dbRoot ? `DB: ${dbRoot}` : ""}
        right={<span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          {!canManage && <Pill tone="warn">읽기 전용 — 검사·반영은 관리자</Pill>}
        </span>}
      />
      <div style={{ padding: 16, display: "grid", gap: 12 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {targets.map(t => {
            const on = t.target === active;
            return (
              <button key={t.target} type="button" onClick={() => setActive(t.target)}
                style={{ padding: "8px 14px", borderRadius: 8, cursor: "pointer", fontWeight: 800, fontSize: 14,
                  border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                  background: on ? "var(--accent)" : "var(--bg-card)", color: on ? "#fff" : "var(--text-secondary)" }}>
                {t.label}
                <span style={{ marginLeft: 7, fontFamily: "monospace", opacity: 0.85 }}>
                  {t.csv_exists ? `${t.csv_rows}행` : "파일 없음"}
                </span>
              </button>
            );
          })}
        </div>

        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontWeight: 900 }}>채울 열</span>
          {[["product", "product (DB 스캔)"],
            ["module", byStepDesc ? "module (Vehicle_matching step_desc)" : "module (step 번호 구간)"]]
            .filter(([key]) => fillColumns.includes(key))
            .map(([key, label]) => {
            const on = column === key;
            return (
              <button key={key} type="button" onClick={() => setColumn(key)}
                style={{ padding: "6px 12px", borderRadius: 8, cursor: "pointer", fontWeight: 800, fontSize: 14,
                  border: "1px solid " + (on ? "var(--accent)" : "var(--border)"),
                  background: on ? "var(--accent)" : "var(--bg-card)", color: on ? "#fff" : "var(--text-secondary)" }}>
                {label}
              </button>
            );
          })}
        </div>

        {current && (
          <section style={{ display: "grid", gap: 8, padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)" }}>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <b style={{ fontFamily: "monospace" }}>{current.file}</b>
              <Pill tone={current.csv_exists ? "ok" : "bad"}>{current.csv_exists ? `${current.csv_rows}행` : "CSV 없음"}</Pill>
              <Pill tone={(column === "product" ? current.has_product_col : current.has_module_col) ? "ok" : "warn"}>
                {(column === "product" ? current.has_product_col : current.has_module_col)
                  ? `${column} 열 있음` : `${column} 열 없음 — 맨 왼쪽에 생성`}
              </Pill>
              {column === "product" ? (
                <>
                  <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                    매칭 키: <b style={{ fontFamily: "monospace" }}>{(current.keys || []).join(" + ") || "-"}</b>
                  </span>
                  <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                    검사 제품: <b style={{ fontFamily: "monospace" }}>{(current.products || []).join(", ") || "없음"}</b>
                  </span>
                </>
              ) : byStepDesc ? (
                <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                  기준: <b style={{ fontFamily: "monospace" }}>step_desc</b> 이름이 같은{" "}
                  <b style={{ fontFamily: "monospace" }}>Vehicle_matching.csv</b> 행의 module (DB 는 읽지 않습니다)
                </span>
              ) : (
                <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                  기준: <b style={{ fontFamily: "monospace" }}>step_id</b> 앞 2글자 + 뒤 번호 구간 (DB 는 읽지 않습니다)
                </span>
              )}
              <Btn variant="primary" disabled={!canManage || scanning || !current.csv_exists}
                style={{ marginLeft: "auto" }} onClick={runScan}>
                {scanning ? "검사 중..." : "검사 실행"}
              </Btn>
            </div>
            {scanning && <Loading text={column === "product"
              ? "raw DB 제품 폴더를 스캔하는 중..."
              : byStepDesc ? "Vehicle_matching 의 step_desc 를 대조하는 중..." : "step 번호 구간을 대조하는 중..."} size="sm" />}
            {column === "module" && !byStepDesc && !current.has_step_col && current.csv_exists && (
              <div style={{ color: WARN.fg, fontSize: 13 }}>이 파일에는 step_id 열이 없어 module 구간을 정할 수 없습니다.</div>
            )}
            {column === "module" && byStepDesc && !current.has_step_desc_col && current.csv_exists && (
              <div style={{ color: WARN.fg, fontSize: 13 }}>이 파일에는 step_desc 열이 없어 Vehicle_matching 과 맞출 수 없습니다.</div>
            )}
            {byStepDesc && (
              <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                {current.label} 는 product 를 채우지 않습니다 — 제품 귀속은 Vehicle_matching.csv 가 step_desc 로 이미 정합니다.
              </div>
            )}
            {!current.csv_exists && (
              <div style={{ color: WARN.fg, fontSize: 13 }}>
                {current.file} 이 DB 루트에 없습니다. 파일을 먼저 올린 뒤 검사하세요.
              </div>
            )}
          </section>
        )}

        {/* 규칙 편집기는 규칙을 쓰는 모드에서만 보여준다 — Vehicle_matching step_desc
            기준 module 채우기는 구간표를 아예 읽지 않는다. */}
        {column === "product"
          ? <PrefixRules rules={settings.prefix_rules} products={current?.products || []}
              canEdit={canManage} onSave={saveRules} />
          : byStepDesc ? null
          : <ModuleRules rules={settings.module_rules} canEdit={canManage} onSave={saveModuleRules} />}

        {proposal && (
          <section style={{ display: "grid", gap: 8, padding: 12, border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-card)" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <b>검사 결과</b>
              <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                {(proposal.scanned_at || "").replace("T", " ")} · {proposal.scanned_by || "-"}
              </span>
              {Object.entries(proposal.counts || {}).map(([k, v]) => (
                <span key={k} style={{ display: "inline-flex", gap: 5, alignItems: "center", fontSize: 13 }}>
                  <StatusPill status={k} /><b style={{ fontFamily: "monospace" }}>{v}</b>
                </span>
              ))}
              {proposal.applied && <Pill tone="ok">반영됨 {(proposal.applied_at || "").replace("T", " ")}</Pill>}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <label style={{ display: "inline-flex", gap: 6, alignItems: "center", fontWeight: 700, cursor: "pointer" }}>
                <input type="checkbox" checked={onlyChanges} onChange={e => setOnlyChanges(e.target.checked)} />
                반영 대상만 보기
              </label>
              <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="검색 (step_id / item / 제품)"
                style={{ width: 260, fontFamily: "monospace" }} />
              <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>{visibleRows.length} / {rows.length} 행</span>
            </div>
            <div style={{ maxHeight: "52vh", overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg-primary)" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ ...th, width: 44 }}>반영</th>
                    {idCols.map(c => <th key={c} style={th}>{c}</th>)}
                    <th style={th}>현재 {column}</th>
                    <th style={th}>제안 {column}</th>
                    <th style={th}>상태</th>
                    <th style={th}>{column === "product" ? "검사 범위" : byStepDesc ? "맞춘 step_desc" : "step 번호"}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map(r => {
                    const target = ["fill", "change"].includes(r.status);
                    const on = target && !skip.includes(r.i);
                    return (
                      <tr key={r.i} style={{ background: on ? "var(--accent-glow)" : "transparent" }}>
                        <td style={{ ...td, textAlign: "center" }}>
                          <input type="checkbox" checked={on} disabled={!target || proposal.applied}
                            onChange={() => setSkip(s => (s.includes(r.i) ? s.filter(x => x !== r.i) : [...s, r.i]))} />
                        </td>
                        {idCols.map(c => <td key={c} style={td} title={r.id?.[c]}>{r.id?.[c] || "-"}</td>)}
                        <td style={{ ...td, color: "var(--text-secondary)" }}>{r.current || "-"}</td>
                        <td style={{ ...td, fontWeight: 800, color: r.proposed ? "var(--text-primary)" : "var(--text-secondary)" }}>
                          {r.proposed || "-"}
                        </td>
                        <td style={td}><StatusPill status={r.status} /></td>
                        <td style={{ ...td, color: "var(--text-secondary)" }}>
                          {(r.scoped || []).length ? r.scoped.join(", ") : "전체"}
                        </td>
                      </tr>
                    );
                  })}
                  {visibleRows.length === 0 && (
                    <tr><td colSpan={idCols.length + 5} style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)" }}>
                      표시할 행이 없습니다
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                반영 대상 <b style={{ fontFamily: "monospace", color: "var(--text-primary)" }}>{applyRows.length}</b>행
                {proposal.add_column ? ` · ${column} 열을 맨 왼쪽에 새로 만듭니다` : ""}
              </span>
              <Btn variant="ghost" disabled={!canManage} onClick={discard} style={{ marginLeft: "auto" }}>제안 폐기</Btn>
              <Btn variant="primary" disabled={!canManage || applying || proposal.applied || applyRows.length === 0}
                onClick={applyProposal}>
                {applying ? "반영 중..." : proposal.applied ? "반영 완료" : `확인 후 반영 (${applyRows.length}행)`}
              </Btn>
            </div>
          </section>
        )}

        {!proposal && current?.csv_exists && (
          <div style={{ padding: 24, textAlign: "center", color: "var(--text-secondary)", border: "1px dashed var(--border)", borderRadius: 10 }}>
            아직 검사 결과가 없습니다. <b>검사 실행</b>을 누르면 {column === "product"
              ? "raw DB 를 훑어"
              : byStepDesc ? "Vehicle_matching 의 step_desc 로" : "step 번호 구간표로"} {column} 후보를 만듭니다 (파일은 바뀌지 않습니다).
          </div>
        )}
      </div>
    </PageShell>
  );
}
