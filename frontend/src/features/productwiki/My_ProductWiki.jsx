import { useEffect, useMemo, useRef, useState } from "react";
import { sf } from "../../lib/api";
import { canManagePage } from "../../lib/permissions";
import { Banner, Button, Input, PageHeader, PageShell, Select, Textarea } from "../../components/ui";
import "./My_ProductWiki.css";

const API = "/api/product-wiki";
const KINDS = {
  issue: "이슈",
  structure: "소자 구조",
  split: "Split 실험",
  decision: "의사결정",
  fact: "확인된 사실",
  opinion: "의견 · 가설",
};
const STATUS = {
  open: "등록",
  investigating: "검토 중",
  validated: "검증 완료",
  closed: "종료",
};
const FIELDS = [
  ["purpose", "목적", "이 실험 또는 판단이 필요한 이유"],
  ["expected_effect", "기대 효과", "예상하는 변화와 검증 기준"],
  ["observed_effect", "관찰 결과", "실제로 확인한 변화 · 아직 확인되지 않은 부분"],
  ["evidence", "근거", "측정 결과, 보고서 경로, 근거 링크 등을 기록하세요"],
];

const asText = (entry) =>
  entry.source_text ||
  [
    entry.title,
    entry.body,
    ...[["structure", "소자 구조"], ["split", "Split"], ["occurred_on", "발생 · 관찰일"], ...FIELDS]
      .filter(([key]) => entry[key])
      .map(([key, label]) => `${label}: ${entry[key]}`),
    entry.status ? `상태: ${STATUS[entry.status] || entry.status}` : "",
    entry.lot_ids?.length ? `관련 Lot: ${entry.lot_ids.join(", ")}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");

const anchor = (id) => `pw-record-${id}`;
const when = (value) => (value ? new Date(value).toLocaleDateString("ko-KR") : "—");
const author = (value) => (typeof value === "object" && value !== null ? value.name || value.username || "—" : value || "—");
const post = (path, body) =>
  sf(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

function ProductOverview({ entries, onSelect }) {
  return (
    <article className="pw-article" aria-label="제품 위키 본문">
      {!entries.length && (
        <div className="pw-empty">
          표시할 위키 기록이 없습니다.
          <br />
          필터 조건을 변경하거나 새 기록을 등록해 보세요.
        </div>
      )}
      {entries.map((entry, index) => {
        const activeFields = FIELDS.filter(([key]) => entry[key] && !entry.body?.includes(entry[key]));
        return (
          <section className="pw-article-card" id={anchor(entry.id)} key={entry.id}>
            <div className="pw-card-header">
              <h2 className="pw-card-title">
                <a href={`#${anchor(entry.id)}`} className="pw-section-number">
                  {index + 1}.
                </a>{" "}
                {entry.title}
              </h2>
              <div className="pw-badge-row">
                <span className={`pw-kind pw-kind-${entry.kind}`}>{KINDS[entry.kind] || entry.kind}</span>
                {entry.status && (
                  <span className={`pw-status-badge pw-status-${entry.status}`}>
                    {STATUS[entry.status] || entry.status}
                  </span>
                )}
              </div>
            </div>

            <p className="pw-byline">
              작성: {author(entry.author)} · 발생/관찰일: {entry.occurred_on || when(entry.created_at)}
              {entry.updated_at && entry.updated_at !== entry.created_at ? ` · 수정: ${when(entry.updated_at)}` : ""}
            </p>

            <div className="pw-prose">{entry.body}</div>

            {activeFields.length > 0 && (
              <div className="pw-field-grid">
                {activeFields.map(([key, label]) => (
                  <div key={key} className="pw-field-box">
                    <div className="pw-field-title">
                      {key === "purpose" && "🎯 "}
                      {key === "expected_effect" && "💡 "}
                      {key === "observed_effect" && "🔍 "}
                      {key === "evidence" && "📑 "}
                      {label}
                    </div>
                    <div className="pw-field-content">{entry[key]}</div>
                  </div>
                ))}
              </div>
            )}

            <div className="pw-meta-bar">
              <div className="pw-context-tags">
                {entry.structure && <span className="pw-tag-pill">구조: {entry.structure}</span>}
                {entry.split && (
                  <span className="pw-tag-pill">Split: {entry.split.replace(/^split\s*/i, "")}</span>
                )}
                {entry.lot_ids?.map((id) => (
                  <a
                    key={id}
                    className="pw-tag-pill pw-lot-link"
                    href={`/splittable?product=${encodeURIComponent(entry.product || "")}&lot=${encodeURIComponent(id)}`}
                  >
                    Lot {id} ↗
                  </a>
                ))}
              </div>
              <button type="button" className="pw-text-link" onClick={() => onSelect(entry.id)}>
                원문 · 수정 이력 보기
              </button>
            </div>
          </section>
        );
      })}
    </article>
  );
}

export default function My_ProductWiki({ user }) {
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [newProduct, setNewProduct] = useState("");
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [notice, setNotice] = useState("");
  const [savedAnchor, setSavedAnchor] = useState("");
  const reading = useRef(null);
  const [selected, setSelected] = useState("");
  const [draft, setDraft] = useState(null);
  const [editRevision, setEditRevision] = useState(null);
  const [stale, setStale] = useState(false);
  const [conflictDoc, setConflictDoc] = useState(null);
  const [busy, setBusy] = useState("");
  const [report, setReport] = useState(null);
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const [historyCursor, setHistoryCursor] = useState(null);
  const [sources, setSources] = useState(null);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const historyTicket = useRef(0);
  const generation = useRef(0);
  const mounted = useRef(true);

  const isAdmin = user?.role === "admin" || canManagePage(user, "productwiki");

  useEffect(() => {
    mounted.current = true;
    sf(`${API}/products`)
      .then((data) => {
        if (!mounted.current) return;
        setProducts(data.products || []);
        setProduct((current) => current || data.products?.[0] || "");
      })
      .catch((err) => {
        if (mounted.current) setError(err.message);
      })
      .finally(() => {
        if (mounted.current) setCatalogLoading(false);
      });
    return () => {
      mounted.current = false;
      generation.current += 1;
    };
  }, []);

  useEffect(() => {
    if (draft || selected || report) reading.current?.scrollIntoView({ block: "start" });
  }, [!!draft, selected, report?.id]);

  useEffect(() => {
    if (savedAnchor) document.getElementById(anchor(savedAnchor))?.scrollIntoView({ block: "start" });
  }, [savedAnchor]);

  async function load(name, ticket, reviewConflict = false) {
    setLoading(true);
    try {
      const data = await sf(`${API}/product?product=${encodeURIComponent(name)}`);
      if (mounted.current && generation.current === ticket) {
        setDoc(data);
        if (reviewConflict) setConflictDoc(data);
      }
    } catch (err) {
      if (mounted.current && generation.current === ticket) setError(err.message);
    } finally {
      if (mounted.current && generation.current === ticket) setLoading(false);
    }
  }

  useEffect(() => {
    if (!product) return;
    const ticket = ++generation.current;
    setDoc(null);
    setError("");
    setDraft(null);
    setSelected("");
    setReport(null);
    setHistory(null);
    setSnapshot(null);
    setHistoryLoading(false);
    historyTicket.current += 1;
    setStale(false);
    setConflictDoc(null);
    setBusy("");
    setQuery("");
    setKindFilter("all");
    setStatusFilter("all");
    setNotice("");
    setSavedAnchor("");
    setSources(null);
    setSourcesLoading(false);
    setHistoryCursor(null);
    load(product, ticket);
  }, [product]);

  function chooseProduct(name) {
    if (!name || name === product || draft || busy) return;
    generation.current += 1;
    setProduct(name);
  }

  const entries = doc?.entries || [];
  const visible = useMemo(() => {
    return entries
      .filter((entry) => {
        if (kindFilter !== "all" && entry.kind !== kindFilter) return false;
        if (statusFilter !== "all" && entry.status !== statusFilter) return false;
        if (!query.trim()) return true;
        const q = query.trim().toLowerCase();
        return [
          entry.title,
          entry.body,
          entry.source_text,
          entry.structure,
          entry.split,
          entry.evidence,
          ...(entry.lot_ids || []),
        ]
          .join(" ")
          .toLowerCase()
          .includes(q);
      })
      .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  }, [entries, query, kindFilter, statusFilter]);

  const current = entries.find((entry) => entry.id === selected);
  const canEdit = current && (current.author === user?.username || isAdmin);

  function selectEntry(id) {
    historyTicket.current += 1;
    setHistory(null);
    setSnapshot(null);
    setHistoryLoading(false);
    setHistoryCursor(null);
    setSelected(id);
    setReport(null);
  }

  async function showHistory(older = false) {
    const ticket = ++historyTicket.current;
    const productTicket = generation.current;
    setHistoryLoading(true);
    setError("");
    try {
      const data = await sf(
        `${API}/history?product=${encodeURIComponent(product)}&entry_id=${encodeURIComponent(selected)}${
          older && historyCursor != null ? `&before_revision=${encodeURIComponent(historyCursor)}` : ""
        }`
      );
      if (mounted.current && ticket === historyTicket.current && productTicket === generation.current) {
        setHistory((old) => (older ? [...(old || []), ...(data.history || [])] : data.history || []));
        setHistoryCursor(data.next_before_revision ?? null);
      }
    } catch (err) {
      if (mounted.current && ticket === historyTicket.current && productTicket === generation.current)
        setError(err.message);
    } finally {
      if (mounted.current && ticket === historyTicket.current && productTicket === generation.current)
        setHistoryLoading(false);
    }
  }

  async function showSources() {
    const ticket = generation.current;
    setSourcesLoading(true);
    try {
      const data = await sf(`${API}/sources?product=${encodeURIComponent(product)}`);
      if (mounted.current && ticket === generation.current) setSources(data);
    } catch (err) {
      if (mounted.current && ticket === generation.current) setError(err.message);
    } finally {
      if (mounted.current && ticket === generation.current) setSourcesLoading(false);
    }
  }

  function edit(entry) {
    setReport(null);
    setStale(false);
    setConflictDoc(null);
    setSavedAnchor("");
    setEditRevision(doc.revision);
    setDraft({ id: entry?.id || "", text: entry ? asText(entry) : "" });
    setNotice("");
  }
  function field(key, value) {
    setDraft((old) => ({ ...old, [key]: value }));
  }

  async function save(event) {
    event.preventDefault();
    if (busy || stale || !doc || !draft) return;
    const ticket = generation.current;
    setBusy("save");
    setError("");
    try {
      const data = await post("/intake", {
        product,
        expected_revision: editRevision,
        text: draft.text,
        entry_id: draft.id,
      });
      if (!mounted.current || generation.current !== ticket) return;
      setDoc(data);
      setDraft(null);
      selectEntry("");
      setQuery("");
      setNotice(data.intake_warning || "기록을 정리해 제품 위키에 반영했습니다.");
      setSavedAnchor(data.saved_entry_id || "");
      setProducts((old) => [...new Set([...old, product])].sort());
    } catch (err) {
      if (!mounted.current || generation.current !== ticket) return;
      if (err.status === 409) {
        setStale(true);
        setConflictDoc(null);
      }
      setError(err.message);
    } finally {
      if (mounted.current && generation.current === ticket) setBusy("");
    }
  }

  async function summarize(useAi) {
    if (busy || !doc) return;
    const ticket = generation.current;
    setBusy(useAi ? "ai" : "basic");
    setError("");
    try {
      const data = await post("/report", { product, use_ai: useAi });
      if (!mounted.current || generation.current !== ticket) return;
      setReport(data);
      setSelected("");
      setDraft(null);
      setDoc((old) => ({ ...old, reports: [data, ...(old.reports || []).filter((item) => item.id !== data.id)] }));
    } catch (err) {
      if (mounted.current && generation.current === ticket) setError(err.message);
    } finally {
      if (mounted.current && generation.current === ticket) setBusy("");
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([report.body], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${product.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")}_PI_${report.id}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <PageShell className="product-wiki">
      <PageHeader title="제품_Wiki" />

      {/* 제품 선택 바 */}
      <section className="pw-product-bar" aria-label="제품 선택">
        <label>
          제품
          <Select
            aria-label="제품 선택"
            value={product}
            disabled={catalogLoading || !!draft || !!busy}
            onChange={(event) => chooseProduct(event.target.value)}
          >
            <option value="">제품을 선택하세요</option>
            {[...new Set([...products, ...(product ? [product] : [])])].map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </label>
        <details className="pw-new-product-disclosure">
          <summary>새 제품</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              chooseProduct(newProduct.trim());
              setNewProduct("");
            }}
            className="pw-new-product"
          >
            <Input
              aria-label="새 제품명"
              placeholder="새 제품명 입력"
              disabled={!!draft || !!busy}
              value={newProduct}
              onChange={(event) => setNewProduct(event.target.value)}
              maxLength={120}
            />
            <Button type="submit" disabled={!newProduct.trim() || !!draft || !!busy}>
              제품 열기
            </Button>
          </form>
        </details>
      </section>

      {error && <Banner tone="danger"><span role="alert">{error}</span></Banner>}
      {notice && <Banner tone="info"><span role="status">{notice}</span></Banner>}
      {stale && (
        <Banner tone="warning">
          다른 사용자가 제품 기록을 수정했습니다. 입력 내용은 유지됩니다. 최신 기록을 확인한 뒤 다시 저장하세요.{" "}
          <Button disabled={loading} onClick={() => load(product, generation.current, true)}>
            최신 기록 확인
          </Button>
        </Banner>
      )}

      {!product && <div className="pw-empty">제품을 선택하거나 새 제품명을 입력해 Wiki 기록을 시작하세요.</div>}
      {loading && <div role="status" className="pw-empty">제품 Wiki를 불러오는 중…</div>}
      {product && !loading && !doc && <Button onClick={() => load(product, generation.current)}>다시 불러오기</Button>}

      {doc && !loading && (
        <>
          {/* 위키 문서 헤더 */}
          <header className="pw-document-header">
            <div>
              <h2>{product} Wiki</h2>
              <p className="pw-muted">
                공식 제품 문서 · {entries.length}개 기록
                {entries[0] ? ` · 최근 수정 ${when(entries[0].updated_at)}` : ""}
              </p>
            </div>
            <div className="pw-actions">
              {isAdmin && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    window.location.href = "/admin?tab=product_admin";
                  }}
                  title="관리자: Step ID, 모듈 구조, 별칭 매핑 관리로 이동"
                >
                  ⚙ 관리자: 공정 구조 & 별칭 관리
                </Button>
              )}
              {(current || report) && (
                <Button disabled={!!busy || !!draft} onClick={() => selectEntry("")}>
                  문서로 돌아가기
                </Button>
              )}
              <Button variant="primary" disabled={!!busy || !!draft} onClick={() => edit(null)}>
                + 새 이슈 · 기록 등록
              </Button>
            </div>
          </header>

          {/* 위키 워크스페이스 */}
          <div className={`pw-workspace ${draft || current || report ? "pw-focused" : ""}`}>
            {/* 좌측 목차 (TOC) */}
            {!draft && !current && !report && (
              <aside className="pw-toc" aria-label="문서 목차">
                <h2>목차</h2>
                <Input
                  aria-label="문서 검색"
                  placeholder="문서에서 찾기"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />

                <nav>
                  {visible.map((entry, index) => (
                    <a key={entry.id} href={`#${anchor(entry.id)}`} className="pw-toc-item">
                      <span>
                        {index + 1}. {entry.title}
                      </span>
                      <span className={`pw-kind pw-kind-${entry.kind} pw-toc-kind-badge`}>
                        {KINDS[entry.kind] || entry.kind}
                      </span>
                    </a>
                  ))}
                </nav>
                <p className="pw-muted" style={{ marginTop: 8 }}>
                  총 {visible.length}개 항목 표시
                </p>
              </aside>
            )}

            {/* 본문 뷰 */}
            <section ref={reading} className="pw-reading" aria-label="제품 문서">
              {draft ? (
                <form onSubmit={save} className="pw-editor">
                  <div className="pw-section-heading">
                    <h2>{draft.id ? "위키 기록 수정" : "새 이슈 · 위키 기록 등록"}</h2>
                  </div>
                  <p className="pw-editor-help">
                    이슈 현상, 소자 구조 변경, Split 실험 결과, 회의 의사결정 등을 자유롭게 적거나 붙여넣으세요.
                    <br />
                    제목, 유형, 모듈, 관련 Lot, 목적 및 관찰 결과는 AI와 백엔드가 자동으로 정돈하여 위키에 저장합니다.
                  </p>

                  {stale && conflictDoc && (
                    <section className="pw-conflict-review" aria-label="최신 기록 확인">
                      <h3>다른 사용자가 저장한 최신 내용</h3>
                      {(draft.id
                        ? conflictDoc.entries.filter((entry) => entry.id === draft.id)
                        : conflictDoc.entries.slice(0, 3)
                      ).map((entry) => (
                        <div key={entry.id}>
                          <strong>{entry.title}</strong>
                          <p className="pw-prose">{entry.body}</p>
                        </div>
                      ))}
                      <Button
                        type="button"
                        onClick={() => {
                          setEditRevision(conflictDoc.revision);
                          setStale(false);
                          setConflictDoc(null);
                          setError("");
                        }}
                      >
                        최신 내용 확인 완료 · 계속
                      </Button>
                    </section>
                  )}

                  <label>
                    기록 내용
                    <Textarea
                      autoFocus
                      required
                      disabled={!!busy}
                      rows={14}
                      maxLength={40000}
                      value={draft.text}
                      onChange={(event) => field("text", event.target.value)}
                      placeholder={
                        "예: [이슈] Gate Poly Etch 후 CD 산포 과다 불량\n" +
                        "N28nm 라인에서 ST1000 공정 후 웨이퍼 에지에서 CD 산포 3.2nm로 상승함.\n" +
                        "관련 Lot: L81001, L81002\n" +
                        "목적: Gate CD 균일도 확보\n" +
                        "기대효과: CD 산포 1.5nm 이내 개선\n" +
                        "관찰결과: Cl2/HBr 가스 비율 조정 시 불량 80% 감소 확인\n" +
                        "근거: In-line CD-SEM 측정 데이터"
                      }
                    />
                  </label>
                  <p className="pw-muted">
                    입력한 원문은 그대로 보존되며 수정 이력(Audit Log)으로 영구 추적됩니다.
                  </p>
                  <div className="pw-actions">
                    <Button
                      type="button"
                      disabled={!!busy}
                      onClick={() => {
                        setDraft(null);
                        setStale(false);
                        setError("");
                      }}
                    >
                      취소
                    </Button>
                    <Button type="submit" variant="primary" disabled={!!busy || stale || !draft.text.trim()}>
                      {busy === "save" ? "정리하고 위키에 반영 중…" : "위키에 저장"}
                    </Button>
                  </div>
                </form>
              ) : report ? (
                <>
                  <div className="pw-section-heading">
                    <h2>PI 요약 보고서</h2>
                    <Button onClick={download}>Markdown 다운로드</Button>
                  </div>
                  <p className="pw-muted">
                    {report.mode === "ai" ? "AI 초안" : "기본 요약"} · {when(report.created_at)} · 기준 Revision{" "}
                    {report.source_revision}
                    {report.created_by || report.author ? ` · 생성 ${author(report.created_by || report.author)}` : ""}
                  </p>
                  {report.source_revision !== doc.revision && (
                    <Banner tone="warning">이 보고서는 이전 Revision의 기록으로 작성되었습니다.</Banner>
                  )}
                  {report.warning && <Banner tone="warning">{report.warning}</Banner>}
                  <pre className="pw-report">{report.body}</pre>
                </>
              ) : current ? (
                <>
                  <div className="pw-section-heading">
                    <span className={`pw-kind pw-kind-${current.kind}`}>{KINDS[current.kind]}</span>
                    {canEdit && (
                      <Button disabled={!!busy} onClick={() => edit(current)}>
                        기록 수정
                      </Button>
                    )}
                  </div>
                  <h2 className="pw-detail-title">{current.title}</h2>
                  <p className="pw-muted">
                    <code>{current.id}</code> · {STATUS[current.status]} · {current.structure || "구조 미지정"}
                    {current.split ? ` · Split ${current.split}` : ""}
                  </p>
                  <div className="pw-audit pw-audit-prominent">
                    <strong>발생 · 관찰일 {current.occurred_on || "미기록"}</strong>
                    <br />
                    작성 {author(current.author)} · {when(current.created_at)}
                    <br />
                    최종 수정 {author(current.updated_by || current.author)} · {when(current.updated_at)}
                  </div>
                  <div className="pw-prose">{current.source_text || asText(current)}</div>

                  {FIELDS.map(([key, label]) =>
                    current[key] ? (
                      <div key={key} className="pw-detail-block">
                        <h3>{label}</h3>
                        <div className="pw-prose">{current[key]}</div>
                      </div>
                    ) : null
                  )}

                  {current.lot_ids?.length > 0 && (
                    <div className="pw-detail-block">
                      <h3>관련 Lot · 원본 Split Table</h3>
                      <div className="pw-entry-tags">
                        {current.lot_ids.map((id) => (
                          <a
                            key={id}
                            href={`/splittable?product=${encodeURIComponent(product)}&lot=${encodeURIComponent(id)}`}
                          >
                            {id} ↗
                          </a>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 수정 이력 */}
                  <div className="pw-detail-block">
                    <div className="pw-section-heading">
                      <h2>수정 이력</h2>
                      <Button disabled={historyLoading} onClick={() => showHistory()}>
                        {historyLoading ? "이력 조회 중…" : "이력 조회"}
                      </Button>
                    </div>
                    {history && !history.length && <p className="pw-muted">저장된 이력이 없습니다.</p>}
                    {history?.map((item, index) => (
                      <article key={`${item.revision}-${index}`} className="pw-history-item">
                        <div className="pw-section-heading">
                          <strong>Revision {item.revision}</strong>
                          <Button onClick={() => setSnapshot(snapshot === item ? null : item)}>
                            {snapshot === item ? "원문 닫기" : "당시 기록 보기"}
                          </Button>
                        </div>
                        <p className="pw-muted">
                          {author(item.actor)} · {when(item.at)}
                        </p>
                        {(item.changes || []).map((change, n) => (
                          <div className="pw-history-change" key={`${change.field}-${n}`}>
                            <strong>{change.field}</strong>
                            <div>
                              <span>변경 전: </span>
                              <pre>{String(change.before ?? "—")}</pre>
                            </div>
                            <div>
                              <span>변경 후: </span>
                              <pre>{String(change.after ?? "—")}</pre>
                            </div>
                          </div>
                        ))}
                        {snapshot === item && (
                          <div className="pw-history-snapshot">
                            <h3>{item.entry?.title || "당시 기록 원문"}</h3>
                            <div className="pw-prose">{item.entry?.body}</div>
                          </div>
                        )}
                      </article>
                    ))}
                    {historyCursor != null && (
                      <Button disabled={historyLoading} onClick={() => showHistory(true)}>
                        이전 이력 더 보기
                      </Button>
                    )}
                  </div>
                </>
              ) : (
                <>
                  {/* 위키 상단 카테고리 / 상태 필터 바 */}
                  <div className="pw-filter-container">
                    <div className="pw-filter-group">
                      <span className="pw-filter-label">유형:</span>
                      <button
                        type="button"
                        className={`pw-filter-chip ${kindFilter === "all" ? "active" : ""}`}
                        onClick={() => setKindFilter("all")}
                      >
                        전체 ({entries.length})
                      </button>
                      {Object.entries(KINDS).map(([k, label]) => {
                        const count = entries.filter((e) => e.kind === k).length;
                        return (
                          <button
                            key={k}
                            type="button"
                            className={`pw-filter-chip ${kindFilter === k ? "active" : ""}`}
                            onClick={() => setKindFilter(k)}
                          >
                            {label} ({count})
                          </button>
                        );
                      })}
                    </div>

                    <div className="pw-filter-group">
                      <span className="pw-filter-label">상태:</span>
                      <button
                        type="button"
                        className={`pw-filter-chip ${statusFilter === "all" ? "active" : ""}`}
                        onClick={() => setStatusFilter("all")}
                      >
                        전체
                      </button>
                      {Object.entries(STATUS).map(([s, label]) => {
                        const count = entries.filter((e) => e.status === s).length;
                        return (
                          <button
                            key={s}
                            type="button"
                            className={`pw-filter-chip ${statusFilter === s ? "active" : ""}`}
                            onClick={() => setStatusFilter(s)}
                          >
                            {label} ({count})
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* 정돈된 Wiki Article 본문 */}
                  <ProductOverview
                    entries={visible.map((entry) => ({ ...entry, product }))}
                    onSelect={selectEntry}
                  />
                </>
              )}

              {/* 요약 보고서 드롭다운 */}
              {!draft && (
                <details className="pw-summary">
                  <summary>PI 종합 요약 보고서</summary>
                  <div className="pw-section-heading">
                    <h2>제품 전체 요약</h2>
                    <span className="pw-muted">현재 저장된 위키 기록 기준</span>
                  </div>
                  <p className="pw-muted">
                    구조, Split, 이슈와 판단 근거를 종합하여 보고서로 모읍니다.
                  </p>
                  <div className="pw-actions">
                    <Button disabled={!!busy || !entries.length} onClick={() => summarize(false)}>
                      {busy === "basic" ? "생성 중…" : "기본 요약 생성"}
                    </Button>
                    <Button
                      variant="primary"
                      disabled={!!busy || !entries.length}
                      onClick={() => summarize(true)}
                    >
                      {busy === "ai" ? "AI 요약 중…" : "AI 요약 생성"}
                    </Button>
                  </div>
                  {(doc.reports || []).length > 0 && (
                    <label className="pw-report-history">
                      이전 보고서
                      <Select
                        aria-label="이전 보고서 선택"
                        value={report?.id || ""}
                        disabled={!!busy}
                        onChange={(event) => {
                          setReport(doc.reports.find((item) => item.id === event.target.value) || null);
                          setSelected("");
                        }}
                      >
                        <option value="">보고서를 선택하세요</option>
                        {doc.reports.map((item) => (
                          <option key={item.id} value={item.id}>
                            {when(item.created_at)} · {item.mode === "ai" ? "AI" : "기본"} · Rev{" "}
                            {item.source_revision}
                          </option>
                        ))}
                      </Select>
                    </label>
                  )}
                </details>
              )}
            </section>
          </div>

          {/* 연결된 Split / Lot 자료 */}
          <details className="pw-sources">
            <summary>연결된 Split · Lot 원본 자료</summary>
            <div className="pw-section-heading">
              <h2>연결된 Split · Lot 원본</h2>
              <Button disabled={sourcesLoading} onClick={showSources}>
                {sourcesLoading ? "조회 중…" : "원본 조회"}
              </Button>
            </div>
            {sources?.warning && <Banner tone="warning">{sources.warning}</Banner>}
            {sources && !sources.lot_tables?.length && (
              <p className="pw-muted">연결된 원본 테이블이 없습니다.</p>
            )}
            {sources?.lot_tables?.map((table, n) => (
              <div key={`${table.product}-${table.version}-${n}`} className="pw-detail-block">
                <h3>
                  {table.product} · Version {table.version}
                </h3>
                <p className="pw-muted">
                  {author(table.updated_by)} · {when(table.updated_at)} · 전체 {(table.rows || []).length}행
                </p>
                <div className="pw-source-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Lot ID</th>
                        <th>목적</th>
                        <th>비고</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(table.rows || []).slice(0, 30).map((row, i) => (
                        <tr key={row.id || i}>
                          <td>
                            <a
                              href={`/splittable?product=${encodeURIComponent(
                                table.product || product
                              )}&lot=${encodeURIComponent(row.cells?.lot_id || "")}`}
                            >
                              {String(row.cells?.lot_id || "—")}
                            </a>
                          </td>
                          <td>{String(row.cells?.purpose || "—")}</td>
                          <td>{String(row.cells?.comment || "—")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </details>
        </>
      )}
    </PageShell>
  );
}
