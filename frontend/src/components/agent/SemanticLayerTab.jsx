// SemanticLayerTab — AgentV2의 "시멘틱 레이어" 항목 본체 (M7 + P3-wire-up).
// 5가지 sub-view 한 페이지: DB/파일 인벤토리 / 스키마 관계 / 운영 Wiki / 컬럼 카탈로그 / 어휘 사전.
// 스키마 관계와 운영 Wiki는 기존 AgentLegacy의 panel을 재사용 (코드 복제 X).
import { useEffect, useMemo, useState } from "react";
import { sf, postJson, putJson } from "../../lib/api";
import { Banner, Button, EmptyState, Field, TabStrip } from "../UXKit";
import { AgentWikiPanel, SchemaRelationsPanel } from "./AgentLegacy";
import Loading from "../Loading";

const SUB_TABS = [
  { k: "inventory", l: "DB / 파일 인벤토리" },
  { k: "relations", l: "스키마 관계" },
  { k: "wiki", l: "운영 Wiki" },
  { k: "columns", l: "컬럼 카탈로그" },
  { k: "lexicon", l: "어휘 사전" },
  { k: "queue", l: "제안 큐" },
];

export default function SemanticLayerTab({ user, canManageWiki }) {
  const [tab, setTab] = useState("inventory");

  return (
    <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}>
      <header>
        <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>🧠 시멘틱 레이어</h2>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
          단위 AI들이 자연어 prompt를 데이터로 연결할 때 쓰는 의미 자원입니다.
          DB·파일 인벤토리 / 스키마 관계 / agent_wiki / 컬럼 카탈로그 / 어휘 사전 (회사 용어 alias) 다섯 가지를 한 화면에서 운영합니다.
        </p>
      </header>
      <TabStrip items={SUB_TABS} active={tab} onChange={setTab} />
      <div>
        {tab === "inventory" && <SourceInventoryView />}
        {tab === "relations" && <SchemaRelationsPanel canManage={!!canManageWiki} />}
        {tab === "wiki" && <AgentWikiPanel canManage={!!canManageWiki} />}
        {tab === "columns" && <ColumnCatalogView />}
        {tab === "lexicon" && <LexiconView canManage={!!canManageWiki} />}
        {tab === "queue" && <ProposalQueueView canManage={!!canManageWiki} />}
      </div>
    </div>
  );
}

// ── 제안 큐 (P4-wire-up) ─────────────────────────────
function ProposalQueueView({ canManage }) {
  const [status, setStatus] = useState("pending");
  const [proposals, setProposals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);

  const reload = () => {
    setBusy(true); setErr("");
    sf(`/api/agent/semantic/proposals?status=${encodeURIComponent(status)}&limit=200`)
      .then((d) => setProposals(Array.isArray(d?.proposals) ? d.proposals : []))
      .catch((e) => setErr(e?.message || "제안 큐 로딩 실패"))
      .finally(() => setBusy(false));
  };

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [status]);

  const decide = (id, decision) => {
    if (!canManage) return;
    setBusy(true); setMsg("");
    postJson("/api/agent/semantic/proposals/decide", { id, status: decision })
      .then((d) => {
        setMsg(decision === "approved" && d?.applied?.upserted
          ? `승인됨 → '${d.applied.canonical}'에 반영`
          : (decision === "approved" ? "승인됨 (lexicon 미반영)" : "거절됨"));
        reload();
      })
      .catch((e) => setErr(e?.message || "결정 실패"))
      .finally(() => setBusy(false));
  };

  const runBatch = () => {
    if (!canManage) return;
    setBatchBusy(true); setMsg("");
    postJson("/api/agent/semantic/proposals/run-batch", {})
      .then((d) => { setMsg(`activity log batch: ${d?.enqueued || 0}건 추가`); reload(); })
      .catch((e) => setErr(e?.message || "batch 실패"))
      .finally(() => setBatchBusy(false));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <TabStrip
          items={[{ k: "pending", l: "대기" }, { k: "approved", l: "승인" }, { k: "rejected", l: "거절" }]}
          active={status}
          onChange={setStatus}
        />
        {canManage && <Button onClick={runBatch} disabled={batchBusy}>{batchBusy ? "batch 실행 중..." : "activity log batch"}</Button>}
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>총 {proposals.length}건</span>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
        회의/인폼/트래커 코멘트 저장 시 자동으로 추출된 후보 어휘입니다. 승인하면 어휘 사전에 즉시 반영됩니다 — mapping은 기존 canonical에 alias로, new_canonical은 새 키로 추가됩니다.
      </p>
      {err && <Banner tone="warn">{err}</Banner>}
      {msg && <Banner tone="info">{msg}</Banner>}
      {busy ? <Loading text="로딩..." size="md" /> : (
        proposals.length === 0 ? <EmptyState title="비어있음" hint="이 상태의 제안이 없습니다." /> : (
          <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
                  <th style={lexTh()}>term</th>
                  <th style={lexTh()}>category</th>
                  <th style={lexTh()}>canonical match</th>
                  <th style={lexTh()}>confidence</th>
                  <th style={lexTh()}>rationale</th>
                  <th style={lexTh()}>origin</th>
                  <th style={lexTh()}>created</th>
                  {canManage && status === "pending" && <th style={lexTh()}>actions</th>}
                </tr>
              </thead>
              <tbody>
                {proposals.map((p) => (
                  <tr key={p.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={lexTd("180px", true)}><code>{p.term || "—"}</code></td>
                    <td style={lexTd("130px")}>
                      <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 3, background: catColor(p.category) + "22", color: catColor(p.category), fontWeight: 700 }}>{p.category || "—"}</span>
                    </td>
                    <td style={lexTd("140px", true)}>{p.canonical_match || "—"}</td>
                    <td style={lexTd("80px", true)}>{Number(p.confidence || 0).toFixed(2)}</td>
                    <td style={lexTd("280px")}>{p.rationale || "—"}</td>
                    <td style={lexTd("160px")}>{(p.origin && (p.origin.kind + (p.origin.ref ? ` · ${String(p.origin.ref).slice(0, 24)}` : ""))) || "—"}</td>
                    <td style={lexTd("160px", true)}>{p.created_at || "—"}</td>
                    {canManage && status === "pending" && (
                      <td style={lexTd("180px")}>
                        <Button onClick={() => decide(p.id, "approved")} style={{ fontSize: 11, padding: "2px 8px" }}>승인</Button>
                        <Button onClick={() => decide(p.id, "rejected")} style={{ fontSize: 11, padding: "2px 8px", marginLeft: 4 }}>거절</Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}

function catColor(category) {
  switch (String(category || "")) {
    case "mapping": return "#10b981";
    case "new_canonical": return "#60a5fa";
    case "conflict": return "#f97316";
    case "reject": return "#9ca3af";
    default: return "#9ca3af";
  }
}

// ── 어휘 사전 (P3-wire-up) ───────────────────────────
function LexiconView({ canManage }) {
  const [scope, setScope] = useState("alias-groups");
  const [data, setData] = useState({ rows: [], seed_keys: [], disk_keys: [] });
  const [changes, setChanges] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [valuesInput, setValuesInput] = useState("");

  const endpoint = scope === "alias-groups" ? "/api/agent/semantic/alias-groups" : "/api/agent/semantic/intent-hints";

  const reload = () => {
    setBusy(true); setErr("");
    Promise.all([
      sf(endpoint),
      sf("/api/agent/semantic/changes?limit=50").catch(() => ({ changes: [] })),
    ]).then(([d, c]) => {
      setData({ rows: d?.rows || [], seed_keys: d?.seed_keys || [], disk_keys: d?.disk_keys || [] });
      setChanges(Array.isArray(c?.changes) ? c.changes : []);
    }).catch((e) => setErr(e?.message || "어휘 사전 로딩 실패"))
      .finally(() => setBusy(false));
  };

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [scope]);

  const save = () => {
    if (!canManage) return;
    const key = keyInput.trim();
    if (!key) { setErr("key를 입력하세요"); return; }
    const values = valuesInput.split(/[,\n]+/).map((s) => s.trim()).filter(Boolean);
    setBusy(true); setErr("");
    putJson(endpoint, { key, values })
      .then(() => { setKeyInput(""); setValuesInput(""); reload(); })
      .catch((e) => setErr(e?.message || "저장 실패"))
      .finally(() => setBusy(false));
  };

  const remove = (key) => {
    if (!canManage) return;
    if (!window.confirm(`'${key}' 디스크 override를 삭제할까요? (seed 값은 그대로 유지됩니다)`)) return;
    setBusy(true); setErr("");
    postJson(`${endpoint}/delete`, { key })
      .then(() => reload())
      .catch((e) => setErr(e?.message || "삭제 실패"))
      .finally(() => setBusy(false));
  };

  const fillFromRow = (row) => {
    setKeyInput(row.key || "");
    setValuesInput((row.effective || []).join(", "));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <TabStrip
          items={[{ k: "alias-groups", l: "Alias 그룹" }, { k: "intent-hints", l: "Intent hint" }]}
          active={scope}
          onChange={setScope}
        />
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          seed {data.seed_keys.length}개 / disk override {data.disk_keys.length}개
        </span>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
        Seed는 코드 안에 박힌 기본 사전, disk는 admin이 추가/수정한 override입니다. seed 키 삭제는 불가 — 값을 빈 리스트로 upsert하면 무력화됩니다. 모든 변경은 `data/flow-data/semantic/changes.jsonl`에 audit됩니다.
      </p>
      {err && <Banner tone="warn">{err}</Banner>}
      {canManage && (
        <section style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", borderRadius: 6, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>{scope === "alias-groups" ? "Alias 그룹 upsert" : "Intent hint upsert"}</div>
          <Field label={scope === "alias-groups" ? "canonical key (예: oxide)" : "intent key (예: knob_analysis)"}>
            <input
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              placeholder={scope === "alias-groups" ? "wafer_id, knob, oxide ..." : "knob_analysis, meeting_recall ..."}
              style={{ width: 260, padding: "4px 8px", fontSize: 13, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" }}
            />
          </Field>
          <Field label={scope === "alias-groups" ? "aliases (콤마 또는 줄바꿈으로 구분)" : "required canonicals (콤마 또는 줄바꿈)"}>
            <textarea
              value={valuesInput}
              onChange={(e) => setValuesInput(e.target.value)}
              rows={2}
              placeholder={scope === "alias-groups" ? "wafer, wf, 웨이퍼, shot" : "knob, semantic_layer"}
              style={{ width: "100%", padding: 8, fontSize: 13, fontFamily: "inherit", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)" }}
            />
          </Field>
          <div>
            <Button onClick={save} disabled={busy}>{busy ? "저장 중..." : "저장"}</Button>
          </div>
        </section>
      )}
      <section>
        {busy ? <Loading text="로딩..." size="md" /> : (
          data.rows.length === 0 ? <EmptyState title="비어있음" hint="아직 등록된 항목이 없습니다." /> : (
            <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
                    <th style={lexTh()}>key</th>
                    <th style={lexTh()}>seed</th>
                    <th style={lexTh()}>disk override</th>
                    <th style={lexTh()}>effective</th>
                    <th style={lexTh()}>source</th>
                    {canManage && <th style={lexTh()}>actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.key} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={lexTd("160px", true)}><code>{row.key}</code></td>
                      <td style={lexTd("240px")}>{(row.seed || []).join(", ") || "—"}</td>
                      <td style={lexTd("240px")}>{row.disk === null || row.disk === undefined ? <span style={{ color: "var(--text-secondary)" }}>—</span> : ((row.disk || []).join(", ") || <span style={{ color: "var(--text-secondary)" }}>(empty)</span>)}</td>
                      <td style={lexTd("240px")}>{(row.effective || []).join(", ") || "—"}</td>
                      <td style={lexTd("90px")}>
                        <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 3, background: row.source === "disk" ? "#fbbf2422" : "#9ca3af22", color: row.source === "disk" ? "#a16207" : "#374151", fontWeight: 700 }}>{row.source}</span>
                      </td>
                      {canManage && (
                        <td style={lexTd("160px")}>
                          <Button onClick={() => fillFromRow(row)} style={{ fontSize: 11, padding: "2px 6px" }}>edit</Button>
                          {row.source === "disk" && <Button onClick={() => remove(row.key)} style={{ fontSize: 11, padding: "2px 6px", marginLeft: 4 }}>delete</Button>}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </section>
      {changes.length > 0 && (
        <section>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>변경 이력 ({changes.length})</div>
          <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, maxHeight: 200 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
                  <th style={lexTh()}>ts</th>
                  <th style={lexTh()}>scope</th>
                  <th style={lexTh()}>key</th>
                  <th style={lexTh()}>before</th>
                  <th style={lexTh()}>after</th>
                  <th style={lexTh()}>by</th>
                </tr>
              </thead>
              <tbody>
                {changes.slice().reverse().map((c, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={lexTd("160px", true)}>{c.timestamp || "—"}</td>
                    <td style={lexTd("120px", true)}>{c.scope || "—"}</td>
                    <td style={lexTd("140px", true)}><code>{c.key || "—"}</code></td>
                    <td style={lexTd("200px")}>{(c.before || []).join(", ") || "—"}</td>
                    <td style={lexTd("200px")}>{(c.after || []).join(", ") || "—"}</td>
                    <td style={lexTd("90px")}>{c.by || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

const lexTh = () => ({ textAlign: "left", padding: "6px 10px", fontWeight: 700, fontSize: 11 });
const lexTd = (width = "auto", mono = false) => ({ padding: "6px 10px", verticalAlign: "top", width, fontFamily: mono ? "monospace" : "inherit" });

function SourceInventoryView() {
  const [sources, setSources] = useState([]);
  const [meta, setMeta] = useState({ total: 0, relations_total: 0 });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    sf("/api/agent/source-inventory")
      .then((d) => {
        if (cancelled) return;
        setSources((d && d.sources) || []);
        setMeta({ total: d?.total || 0, relations_total: d?.relations_total || 0 });
      })
      .catch((e) => !cancelled && setErr(e?.message || "데이터 소스 인벤토리 로딩 실패"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div style={{ padding: 24 }}><Loading text="인벤토리 로딩..." size="md" /></div>;
  if (err) return <div style={{ padding: 12 }}><Banner tone="warn">{err}</Banner></div>;
  if (sources.length === 0) return <EmptyState title="등록된 데이터 소스가 없습니다" hint="schema_relations.json에 source를 추가하면 여기 나타납니다" />;

  const dbs = sources.filter((s) => s.source_type === "db");
  const files = sources.filter((s) => s.source_type !== "db");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
        총 데이터 소스 {meta.total}개 · 등록된 join relation {meta.relations_total}개. 각 source가 다른 source와 어떻게 연결되는지 한눈에 봅니다. 행 클릭은 아직 비활성 — 편집은 '스키마 관계' 탭에서.
      </p>
      <SourceGroup title="DB 소스" sources={dbs} />
      <SourceGroup title="파일 소스" sources={files} />
    </div>
  );
}

function SourceGroup({ title, sources }) {
  if (sources.length === 0) return null;
  return (
    <div>
      <h3 style={{ fontSize: 13, fontWeight: 700, margin: "0 0 6px", color: "var(--accent)" }}>{title} ({sources.length})</h3>
      <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)" }}>
              <th style={th()}>label</th>
              <th style={th()}>source_id</th>
              <th style={th()}>join 회수</th>
              <th style={th()}>canonical join keys</th>
              <th style={th()}>연결된 다른 소스</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={`${s.source_type}:${s.label}:${s.source_id}`} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={td("160px", true)}>{s.label}</td>
                <td style={td("220px", true)}>{s.source_id || "—"}</td>
                <td style={td("80px")}>{s.relation_count}</td>
                <td style={td("200px")}>{(s.join_keys || []).join(", ") || "—"}</td>
                <td style={td()}>{(s.connects_to || []).join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ColumnCatalogView() {
  const [items, setItems] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    sf("/api/agent/column-catalog")
      .then((d) => !cancelled && setItems((d && d.items) || []))
      .catch((e) => !cancelled && setErr(e?.message || "컬럼 카탈로그 로딩 실패"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) =>
      String(it.name || "").toLowerCase().includes(q) ||
      String(it.meaning || "").toLowerCase().includes(q) ||
      (it.used_by || []).some((u) => String(u).toLowerCase().includes(q))
    );
  }, [items, filter]);

  if (loading) return <div style={{ padding: 24 }}><Loading text="컬럼 카탈로그 로딩..." size="md" /></div>;
  if (err) return <div style={{ padding: 12 }}><Banner tone="warn">{err}</Banner></div>;
  if (items.length === 0) return <EmptyState title="등록된 컬럼이 없습니다" hint="ColumnDoc은 backend/core/flowi_units/registry.py에서 정의됩니다" />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="컬럼/의미/사용처 검색"
          style={{ padding: "4px 8px", fontSize: 13, borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", minWidth: 240 }}
        />
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{filtered.length} / {items.length}</span>
      </div>
      <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", color: "var(--text-secondary)", position: "sticky", top: 0 }}>
              <th style={th()}>이름</th>
              <th style={th()}>의미</th>
              <th style={th()}>예시</th>
              <th style={th()}>사용처 (unit AI)</th>
              <th style={th()}>Wiki doc</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c) => (
              <tr key={c.name} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={td("160px", true)}><code>{c.name}</code></td>
                <td style={td()}>{c.meaning || <span style={{ color: "var(--text-secondary)" }}>(미작성)</span>}</td>
                <td style={td("160px")}>{(c.sample_values || []).slice(0, 4).join(", ") || "—"}</td>
                <td style={td("200px")}>{(c.used_by || []).join(", ")}</td>
                <td style={td("180px", true)}>{c.wiki_doc_id || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 11, color: "var(--text-secondary)", margin: 0 }}>
        ※ ColumnDoc은 코드에서 관리됩니다 (backend/core/flowi_units/schema_columns.py 와 각 unit AI 모듈). 의미 추가/수정은 코드 PR로 합니다. Wiki schema_doc kind(4개)는 M7에서 중복으로 deprecate되었습니다.
      </p>
    </div>
  );
}

const th = () => ({ textAlign: "left", padding: "6px 10px", fontWeight: 700, fontSize: 11 });
const td = (width = "auto", mono = false) => ({ padding: "6px 10px", verticalAlign: "top", width, fontFamily: mono ? "monospace" : "inherit" });
