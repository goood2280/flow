import { useEffect, useMemo, useState } from "react";
import { Banner, Button, Pill } from "../../components/UXKit";
import { sf, authSrc } from "../../lib/api";
import StructureModelWorkspace from "../structure/StructureModelWorkspace";
import "./DomainKnowledgePanel.css";

const API = "/api/admin/domain-knowledge";

function asText(value) {
  return value == null ? "" : String(value);
}

function cleanDocument(data) {
  return {
    title: asText(data?.title),
    body: asText(data?.body),
    editing_guidelines: asText(data?.editing_guidelines),
    version: Number(data?.version) || 0,
    updated_at: asText(data?.updated_at),
    updated_by: asText(data?.updated_by),
    llm_available: Boolean(data?.llm_available),
  };
}

function formatDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (!Number.isNaN(parsed.getTime())) return parsed.toLocaleString("ko-KR");
  return asText(value).replace("T", " ").slice(0, 19);
}

function headingId(index) {
  return `domain-knowledge-heading-${index}`;
}

function readHeadings(markdown) {
  let index = 0;
  return asText(markdown).split(/\r?\n/).flatMap((line) => {
    const match = line.match(/^(#{1,4})\s+(.+?)\s*#*\s*$/);
    if (!match) return [];
    const item = { id: headingId(index), level: match[1].length, text: match[2] };
    index += 1;
    return [item];
  });
}

function safeHref(value) {
  const href = asText(value).trim();
  if (/^(https?:|mailto:)/i.test(href)) return href;
  return "";
}

function InlineMarkdown({ text }) {
  const source = asText(text);
  const token = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g;
  const pieces = [];
  let cursor = 0;
  let match;
  while ((match = token.exec(source))) {
    if (match.index > cursor) pieces.push(source.slice(cursor, match.index));
    const value = match[0];
    if (value.startsWith("`")) {
      pieces.push(<code key={`${match.index}-code`}>{value.slice(1, -1)}</code>);
    } else if (value.startsWith("**")) {
      pieces.push(<strong key={`${match.index}-strong`}>{value.slice(2, -2)}</strong>);
    } else {
      const link = value.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = safeHref(link?.[2]);
      pieces.push(href
        ? <a key={`${match.index}-link`} href={href} target="_blank" rel="noopener noreferrer">{link[1]}</a>
        : <span key={`${match.index}-text`}>{link?.[1] || value}</span>);
    }
    cursor = match.index + value.length;
  }
  if (cursor < source.length) pieces.push(source.slice(cursor));
  return pieces;
}

function MarkdownPreview({ markdown, prefix = "", emptyText = "미리 볼 본문이 없습니다." }) {
  const lines = asText(markdown).split(/\r?\n/);
  let headingIndex = 0;
  let inFence = false;
  let fenceLines = [];
  const nodes = [];
  let skipTo = -1;

  const flushFence = (key) => {
    if (!fenceLines.length && !inFence) return;
    nodes.push(<pre key={`code-${key}`}><code>{fenceLines.join("\n")}</code></pre>);
    fenceLines = [];
  };

  lines.forEach((line, index) => {
    if (index <= skipTo) return;
    if (!inFence && line.trim().startsWith("|") && /^\s*\|[\s:|\-]+\|\s*$/.test(lines[index + 1] || "")) {
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
      const rows = [];
      let next = index + 2;
      while (next < lines.length && lines[next].trim().startsWith("|")) rows.push(cells(lines[next++]));
      skipTo = next - 1;
      nodes.push(<div className="dkp-table-scroll" key={`table-${index}`}><table><thead><tr>{cells(line).map((cell, i) => <th key={i}><InlineMarkdown text={cell}/></th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><InlineMarkdown text={cell}/></td>)}</tr>)}</tbody></table></div>);
      return;
    }
    const img = !inFence && line.match(/^!\[([^\]]*)\]\((\/api\/admin\/domain-knowledge\/assets\/[a-f0-9]{32}\.png)\)$/);
    if (img) { nodes.push(<figure key={`image-${index}`}><img src={authSrc(img[2])} alt={img[1]} loading="lazy"/><figcaption>{img[1]}</figcaption></figure>); return; }
    if (/^```/.test(line.trim())) {
      if (inFence) flushFence(index);
      inFence = !inFence;
      return;
    }
    if (inFence) {
      fenceLines.push(line);
      return;
    }
    const heading = line.match(/^(#{1,4})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const level = heading[1].length;
      const id = prefix + headingId(headingIndex++);
      const Tag = `h${level}`;
      nodes.push(<Tag id={id} key={`heading-${index}`}><InlineMarkdown text={heading[2]}/></Tag>);
      return;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
    if (bullet) {
      nodes.push(<div className="dkp-bullet" key={`bullet-${index}`}><span aria-hidden="true">•</span><div><InlineMarkdown text={bullet[1]}/></div></div>);
      return;
    }
    const ordered = line.match(/^\s*(\d+)\.\s+(.+)$/);
    if (ordered) {
      nodes.push(<div className="dkp-bullet" key={`ordered-${index}`}><span>{ordered[1]}.</span><div><InlineMarkdown text={ordered[2]}/></div></div>);
      return;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      nodes.push(<blockquote key={`quote-${index}`}><InlineMarkdown text={quote[1]}/></blockquote>);
      return;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      nodes.push(<hr key={`rule-${index}`}/>);
      return;
    }
    if (line.trim()) nodes.push(<p key={`paragraph-${index}`}><InlineMarkdown text={line.trim()}/></p>);
  });
  if (inFence || fenceLines.length) flushFence(lines.length);
  return <div className="dkp-markdown">{nodes.length ? nodes : <div className="dkp-empty">{emptyText}</div>}</div>;
}

function ErrorNotice({ error, onClose }) {
  if (!error) return null;
  return <Banner tone="bad" onClose={onClose}>{error}</Banner>;
}

export default function DomainKnowledgePanel() {
  const [document, setDocument] = useState(null);
  const [form, setForm] = useState({ title: "", body: "", editing_guidelines: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState("");
  const [notice, setNotice] = useState("");
  const [instruction, setInstruction] = useState("");
  const [sectionHeading, setSectionHeading] = useState("");
  const [aiDraft, setAiDraft] = useState(null);
  const [draftReviewed, setDraftReviewed] = useState(false);
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedRevision, setSelectedRevision] = useState(null);
  const [editing, setEditing] = useState(false);
  const [references, setReferences] = useState(null);
  const [referencesLoading, setReferencesLoading] = useState(false);
  const [uploading, setUploading] = useState(false);

  const loadReferences = async () => {
    setReferencesLoading(true);
    try { setReferences(await sf(`${API}/references`)); }
    catch (err) { setError(err.message); }
    finally { setReferencesLoading(false); }
  };
  const uploadImage = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 8000000) { setError("이미지는 최대 8MB입니다."); return; }
    setUploading(true);
    try {
      const data = new FormData(); data.append("file", file);
      const asset = await sf(`${API}/assets`, { method: "POST", body: data });
      const name = asset.name.replace(/[\[\]\r\n]/g, " ");
      setForm((value) => ({ ...value, body: value.body + `\n\n![${name}](${asset.url})\n\n출처·구조 버전·관찰 방향: 확인 필요\n` }));
      setEditing(true);
      setNotice("구조 이미지를 본문 끝에 추가했습니다. 관련 절로 옮기고 설명을 작성한 뒤 저장하세요.");
    } catch (err) { setError(err.message); }
    finally { setUploading(false); }
  };
  const readInternalNote = async (event) => {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    if (file.size > 80000) { setError("내부 지식 파일은 UTF-8 텍스트 80KB 이내로 선택하세요."); return; }
    const text = await file.text();
    if (text.length > 19000) { setError("내부 지식은 19,000자 이내로 나누어 추가하세요."); return; }
    setInstruction(`다음 내부 지식을 관련 절에 통합하고 근거와 예외를 보존해 주세요.\n출처: ${file.name}\n\n${text}`);
    setNotice("내부 지식 파일을 편집 요청에 불러왔습니다. AI 초안 만들기를 누르세요.");
  };
  const insertStructure = () => {
    setEditing(true);
    setForm((value) => ({ ...value, body: value.body + "\n\n## 구조 지식: 구조명을 입력하세요\n\n### 범위와 근거\n제품·공정 시점·구조 버전: 확인 필요\n원본 GDS/도면/계측 출처: 확인 필요\n실측 구조 또는 개념도 여부: 확인 필요\n\n### 좌표와 레이어\n원점·X/Y/Z축 방향·길이 단위·Notch 방향: 확인 필요\n아래에서 위로 재료·레이어·역할·검증된 치수: 확인 필요\n\n### 3D 구조\n연결 관계·구조 설명 및 이미지: 확인 필요\n\n### X축 컷\n절단면(XZ/YZ)·고정 좌표·절단선·관찰 방향: 확인 필요\n\n### Y축 컷\n절단면(XZ/YZ)·고정 좌표·절단선·관찰 방향: 확인 필요\n\n### Top view\n관찰 방향·표시 레이어·가려진 구조·축 방향: 확인 필요\n\n### 계측과 연결\nTEG/DUT·CD/OVL/HT/THK 측정 위치·Item ID·단위: 확인 필요\n" }));
  };

  const dirty = Boolean(document) && (
    form.title !== document.title
    || form.body !== document.body
    || form.editing_guidelines !== document.editing_guidelines
  );
  const headings = useMemo(() => readHeadings(form.body), [form.body]);
  const draftStale = Boolean(aiDraft) && (
    aiDraft.sourceTitle !== form.title
    || aiDraft.sourceBody !== form.body
    || aiDraft.sourceGuidelines !== form.editing_guidelines
    || aiDraft.sourceSection !== sectionHeading
    || aiDraft.sourceInstruction !== instruction
    || aiDraft.base_version !== document?.version
  );

  const assignDocument = (data) => {
    const next = cleanDocument(data);
    setDocument(next);
    setForm({ title: next.title, body: next.body, editing_guidelines: next.editing_guidelines });
    setSelectedRevision(null);
    setAiDraft(null);
    setDraftReviewed(false);
    setConflict("");
    setSectionHeading("");
    return next;
  };

  const load = async ({ askBeforeDiscard = false } = {}) => {
    if (askBeforeDiscard && dirty && !window.confirm("저장하지 않은 변경을 버리고 최신 내용을 불러올까요?")) return;
    setLoading(true);
    setError("");
    try {
      assignDocument(await sf(API));
    } catch (err) {
      setError(err.message || "기본지식을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!document || saving || !dirty) return;
    if (!form.title.trim()) {
      setError("문서 제목을 입력하세요.");
      return;
    }
    setSaving(true);
    setError("");
    setConflict("");
    setNotice("");
    try {
      const saved = await sf(API, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: form.title,
          body: form.body,
          editing_guidelines: form.editing_guidelines,
          base_version: document.version,
        }),
      });
      const next = assignDocument(saved);
      setNotice(`버전 ${next.version}으로 저장했습니다.`);
      setHistory(null);
    } catch (err) {
      if (err.status === 409) {
        setConflict(err.message || "다른 관리자가 먼저 저장했습니다. 현재 편집 내용은 유지됩니다.");
      } else {
        setError(err.message || "저장하지 못했습니다.");
      }
    } finally {
      setSaving(false);
    }
  };

  const createAiDraft = async () => {
    if (!document || previewing || !document.llm_available || !instruction.trim()) return;
    setPreviewing(true);
    setError("");
    setNotice("");
    try {
      const source = {
        sourceTitle: form.title,
        sourceBody: form.body,
        sourceGuidelines: form.editing_guidelines,
        sourceInstruction: instruction,
        sourceSection: sectionHeading,
      };
      const draft = await sf(`${API}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: form.title,
          body: form.body,
          editing_guidelines: form.editing_guidelines,
          base_version: document.version,
          instruction: instruction.trim(),
          section_heading: sectionHeading,
        }),
      });
      setAiDraft({ ...source, title: asText(draft?.title), body: asText(draft?.body), base_version: Number(draft?.base_version) });
      setDraftReviewed(false);
    } catch (err) {
      if (err.status === 409) setConflict(err.message || "초안 생성 기준 버전이 최신 버전과 다릅니다.");
      else setError(err.message || "AI 초안을 만들지 못했습니다.");
    } finally {
      setPreviewing(false);
    }
  };

  const applyDraft = () => {
    if (!aiDraft || !draftReviewed || draftStale) return;
    setForm((current) => ({ ...current, title: aiDraft.title, body: aiDraft.body }));
    setEditing(true);
    setAiDraft(null);
    setDraftReviewed(false);
    setNotice("AI 초안을 편집기에 반영했습니다. 내용을 확인한 뒤 저장하세요.");
  };

  const loadHistory = async () => {
    if (historyLoading || history !== null) return;
    setHistoryLoading(true);
    try {
      const data = await sf(`${API}/history`);
      setHistory(Array.isArray(data?.items) ? data.items : []);
    } catch (err) {
      setError(err.message || "변경 이력을 불러오지 못했습니다.");
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const loadRevision = async (version) => {
    if (!document) return;
    if (dirty && !window.confirm("저장하지 않은 변경을 버리고 선택한 이전 버전을 편집기에 불러올까요?")) return;
    setHistoryLoading(true);
    setError("");
    try {
      const old = cleanDocument(await sf(`${API}/history/${version}`));
      setForm({ title: old.title, body: old.body, editing_guidelines: old.editing_guidelines });
      setSelectedRevision(old.version || version);
      setAiDraft(null);
      setDraftReviewed(false);
      setNotice(`버전 ${old.version || version}을 편집기에 불러왔습니다. 저장하면 최신 버전 다음으로 기록됩니다.`);
    } catch (err) {
      setError(err.message || "이전 버전을 불러오지 못했습니다.");
    } finally {
      setHistoryLoading(false);
    }
  };

  if (loading && !document) return <div className="dkp-state">기본지식을 불러오는 중…</div>;
  if (!document) return <div className="dkp-panel"><ErrorNotice error={error} onClose={() => setError("")}/><Button onClick={() => load()}>다시 시도</Button></div>;

  return (
    <section className="dkp-panel" aria-label="기본지식 관리">
      <header className="dkp-header">
        <div>
          <div className="dkp-eyebrow">DOMAIN KNOWLEDGE</div>
          <h2>기본지식</h2>
          <p>공정설계 기본지식·내부 지식·구조 도면을 관리합니다. 저장한 내용은 홈 데이터챗의 LLM 해석에 반영됩니다.</p>
        </div>
        <div className="dkp-header-actions">
          <Button variant="subtle" onClick={() => window.document.getElementById("domain-knowledge-add")?.scrollIntoView({ behavior: "smooth" })}>내부 지식·AI 편집</Button>
          <Button variant="subtle" onClick={() => setEditing(!editing)}>{editing ? "읽기 모드" : "직접 편집"}</Button>
          <Pill tone={document.llm_available ? "ok" : "neutral"} size="md">LLM {document.llm_available ? "설정됨" : "설정 필요"}</Pill>
          <Button variant="subtle" disabled={loading || saving || previewing} onClick={() => load({ askBeforeDiscard: true })}>{loading ? "불러오는 중…" : "최신 내용"}</Button>
          <Button variant="primary" disabled={saving || loading || previewing || uploading || !dirty || !form.title.trim()} onClick={save}>{saving ? "저장 중…" : "저장"}</Button>
        </div>
      </header>

      <div className="dkp-meta">
        <span>현재 버전 <b>{document.version}</b></span>
        <span>최근 수정 {formatDateTime(document.updated_at)}</span>
        <span>수정자 {document.updated_by || "—"}</span>
        {dirty && <Pill tone="warn">저장 안 됨</Pill>}
        {selectedRevision != null && <Pill tone="info">이전 버전 {selectedRevision} 편집 중</Pill>}
      </div>

      <ErrorNotice error={error} onClose={() => setError("")}/>
      {conflict && <Banner tone="warn" onClose={() => setConflict("")}>
        <div className="dkp-notice-row"><span><b>버전 충돌:</b> {conflict} 편집 중인 내용은 그대로 유지했습니다.</span><Button variant="subtle" onClick={() => load({ askBeforeDiscard: true })}>최신 내용 확인</Button></div>
      </Banner>}
      {notice && <Banner tone="ok" onClose={() => setNotice("")}>{notice}</Banner>}

      <StructureModelWorkspace admin/>

      <div className={`dkp-workspace ${editing ? "is-editing" : "is-reading"}`}>
        <aside className="dkp-toc" aria-label="문서 목차">
          <div className="dkp-section-title">목차</div>
          {headings.length ? headings.map((heading) => (
            <button key={heading.id} className="dkp-toc-link" style={{ paddingLeft: 10 + (heading.level - 1) * 14 }} onClick={() => window.document.getElementById(heading.id)?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              {heading.text}
            </button>
          )) : <div className="dkp-muted">본문에 # 제목을 추가하면 목차가 만들어집니다.</div>}
        </aside>

        {editing && <div className="dkp-editor">
          <div className="dkp-section-title">문서 편집</div>
          <label className="dkp-field"><span>제목</span><input value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} disabled={saving}/></label>
          <label className="dkp-field"><span>본문 <small>Markdown</small></span><textarea className="dkp-body-input" value={form.body} onChange={(event) => setForm((current) => ({ ...current, body: event.target.value }))} disabled={saving}/></label>
          <label className="dkp-field"><span>LLM 편집 지침</span><textarea value={form.editing_guidelines} onChange={(event) => setForm((current) => ({ ...current, editing_guidelines: event.target.value }))} rows={5} disabled={saving} placeholder="문서 편집 시 지켜야 할 문체, 범위, 금지 사항을 적으세요."/></label>
        </div>}

        <div className="dkp-preview-column">
          <div className="dkp-section-title">{dirty ? "수정 중인 문서" : "기본지식 Wiki"}</div>
          <article className="dkp-preview-card">
            <h1 className="dkp-document-title">{form.title || "제목 없음"}</h1>
            <MarkdownPreview markdown={form.body}/>
          </article>
        </div>
      </div>

      <section className="dkp-ai" id="domain-knowledge-add">
        <div className="dkp-section-title">내부 지식·구조 자료 추가</div>
        <div className="dkp-actions">
          <Button disabled={saving || uploading} onClick={insertStructure}>3D·단면·Top view 틀 추가</Button>
          <label className="dkp-file-label">{uploading ? "이미지 업로드 중…" : "구조 이미지 첨부"}<input aria-label="구조 이미지 첨부" type="file" accept=".png,.jpg,.jpeg,.webp,.gif" disabled={saving || uploading} onChange={uploadImage}/></label>
          <label className="dkp-file-label">내부 지식 파일 불러오기<input aria-label="내부 지식 파일 불러오기" type="file" accept=".md,.txt" disabled={previewing} onChange={readInternalNote}/></label>
        </div>
        <p className="dkp-muted">이미지(PNG/JPG/WEBP/GIF, 8MB)와 설명을 함께 저장합니다. 현재 AI는 텍스트 설명과 DB 참고자료를 사용하며 이미지 자체를 판독하지 않습니다.</p>
      </section>
      <details className="dkp-history">
        <summary>Flow DB·파일 참고자료</summary>
        <p className="dkp-muted">AI 편집 시 현재 DB의 컬럼과 일부 기준정보 샘플을 자동으로 참고합니다. 원본 DB는 수정하지 않습니다.</p>
        <Button disabled={referencesLoading} onClick={loadReferences}>{referencesLoading ? "확인 중…" : "DB·파일 참고 갱신"}</Button>
        {references?.warnings?.map((warning, i) => <p className="dkp-muted" key={i}>{warning}</p>)}
        {references?.sources?.map((source, i) => <details key={i}><summary>{source.name} · {source.kind}</summary><p className="dkp-muted">{source.description}</p><p>{source.columns.join(", ")}</p>{source.examples?.length > 0 && <pre className="dkp-reference-sample">{JSON.stringify(source.examples, null, 2)}</pre>}</details>)}
      </details>
      <section className="dkp-ai">
        <div className="dkp-ai-heading">
          <div><div className="dkp-section-title">AI 편집 초안</div><p>요청을 바탕으로 초안만 만듭니다. 검토 후 편집기에 반영해도 저장되지는 않습니다.</p></div>
          {!document.llm_available && <Pill tone="neutral">LLM 설정 필요</Pill>}
        </div>
        <label className="dkp-field"><span>수정 범위</span><select value={sectionHeading} disabled={previewing} onChange={(event) => setSectionHeading(event.target.value)}><option value="">전체 문서</option>{headings.filter((h) => h.level === 2).map((h) => <option key={h.id} value={`## ${h.text}`}>{h.text}</option>)}</select><small>긴 문서는 절을 선택하면 다른 절을 그대로 보존하면서 수정할 수 있습니다.</small></label>
        <label className="dkp-field"><span>편집 요청</span><textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={4} disabled={!document.llm_available || previewing} placeholder="예: 중복 설명을 정리하고, 안전 규칙은 별도 항목으로 분리해 주세요."/></label>
        <div className="dkp-actions"><Button variant="primary" disabled={!document.llm_available || previewing || saving || !instruction.trim()} onClick={createAiDraft}>{previewing ? "초안 생성 중…" : "AI 초안 만들기"}</Button></div>
        {aiDraft && <div className="dkp-draft">
          <div className="dkp-draft-head"><div><b>AI 초안 미리보기</b><span>기준 버전 {aiDraft.base_version}</span></div>{draftStale && <Pill tone="warn">편집 후 변경됨</Pill>}</div>
          <h3>{aiDraft.title || "제목 없음"}</h3>
          <MarkdownPreview prefix="draft-" markdown={aiDraft.body} emptyText="AI가 빈 본문을 반환했습니다."/>
          <label className="dkp-review-check"><input type="checkbox" checked={draftReviewed} onChange={(event) => setDraftReviewed(event.target.checked)} disabled={draftStale}/><span>초안 내용을 검토했습니다.</span></label>
          {draftStale && <div className="dkp-muted">초안 생성 뒤 원문·지침·요청 또는 기준 버전이 바뀌었습니다. 새 초안을 만드세요.</div>}
          <div className="dkp-actions"><Button disabled={!draftReviewed || draftStale} onClick={applyDraft}>편집기에 반영</Button></div>
        </div>}
      </section>

      <details className="dkp-history" onToggle={(event) => { if (event.currentTarget.open) loadHistory(); }}>
        <summary>변경 이력</summary>
        {historyLoading && <div className="dkp-muted">이력을 불러오는 중…</div>}
        {history && history.length === 0 && <div className="dkp-muted">저장된 이력이 없습니다.</div>}
        {history?.map((item) => <div className="dkp-history-row" key={item.version}>
          <div><b>v{item.version}</b><span>{item.title || "제목 없음"}</span><small>{formatDateTime(item.updated_at)} · {item.updated_by || "—"}</small></div>
          <Button variant="subtle" disabled={historyLoading || saving || previewing || uploading || item.version === document.version} onClick={() => loadRevision(item.version)}>{item.version === document.version ? "현재 버전" : "편집기에 불러오기"}</Button>
        </div>)}
      </details>
    </section>
  );
}
