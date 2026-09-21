import { useEffect, useMemo, useRef, useState } from "react";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows } from "../../components/SpreadsheetPasteGrid";
import { Banner, Button } from "../../components/ui";
import { sf } from "../../lib/api";
import { canManagePage } from "../../lib/permissions";
import "./ProductStructure.css";

const API = "/api/product-wiki/structure";
const COLUMNS = ["module", "path", "step_ids", "description"];
const TRANSITION_COLUMNS = ["order", "module", "from_path", "to_path", "relation", "description", "step_ids"];
const EMPTY_MODULE = "모듈 미지정";

const text = (value) => value == null ? "" : String(value).trim();
const splitStepIds = (value) => Array.isArray(value)
  ? value.map(text).filter(Boolean)
  : text(value).split(/[;,\n]+/).map(text).filter(Boolean);
const pathParts = (value) => text(value).split("/").map(text).filter(Boolean);
const naturalStepParts = (value) => text(value).match(/\d+|\D+/g) || [];
// Compare numeric chunks naturally, but always render the original step_id;
// leading zeroes are part of the identifier and must not be normalized away.
const naturalStepCompare = (left, right) => {
  const a = naturalStepParts(left);
  const b = naturalStepParts(right);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if (index >= a.length) return -1;
    if (index >= b.length) return 1;
    const leftPart = a[index];
    const rightPart = b[index];
    const leftNumeric = /^\d+$/.test(leftPart);
    const rightNumeric = /^\d+$/.test(rightPart);
    if (leftNumeric && rightNumeric) {
      const leftNumber = BigInt(leftPart);
      const rightNumber = BigInt(rightPart);
      if (leftNumber < rightNumber) return -1;
      if (leftNumber > rightNumber) return 1;
      continue;
    }
    if (leftNumeric !== rightNumeric) return leftNumeric ? -1 : 1;
    const lexicalCompare = leftPart.localeCompare(rightPart, undefined, { sensitivity: "base" });
    if (lexicalCompare) return lexicalCompare;
  }
  return text(left).localeCompare(text(right), undefined, { sensitivity: "base" });
};
const normalizedFullPath = (module, path) => {
  const moduleName = text(module) || EMPTY_MODULE;
  const parts = pathParts(path);
  return [moduleName, ...parts].join("/");
};
const rowForGrid = (row = {}) => ({
  module: text(row.module),
  path: text(row.path),
  step_ids: splitStepIds(row.step_ids).join(", "),
  description: text(row.description),
});
const filledGridRows = (rows) => (rows || []).filter((row) => COLUMNS.some((column) => text(row?.[column])));
const rowsForSave = (rows) => filledGridRows(rows).map((row) => ({
  module: text(row.module) || EMPTY_MODULE,
  path: text(row.path),
  step_ids: [...new Set(splitStepIds(row.step_ids))],
  description: text(row.description),
}));
const rowForTransitionGrid = (row = {}) => ({
  order: row.order == null ? "" : text(row.order),
  module: text(row.module),
  from_path: text(row.from_path),
  to_path: text(row.to_path),
  relation: text(row.relation),
  description: text(row.description),
  step_ids: splitStepIds(row.step_ids).join(", "),
});
const filledTransitionRows = (rows) => (rows || []).filter((row) => TRANSITION_COLUMNS.some((column) => text(row?.[column])));
const transitionsForSave = (rows) => filledTransitionRows(rows).map((row) => ({
  order: text(row.order) ? Number(row.order) : null,
  module: text(row.module),
  from_path: text(row.from_path),
  to_path: text(row.to_path),
  relation: text(row.relation),
  description: text(row.description),
  step_ids: [...new Set(splitStepIds(row.step_ids))],
}));

function makeNode(type, label, key, extra = {}) {
  return { type, label, key, children: new Map(), descriptions: [], ...extra };
}

function stepLookup(mappingRows) {
  const exact = new Map();
  const byId = new Map();
  (mappingRows || []).forEach((row) => {
    const moduleName = text(row.module) || EMPTY_MODULE;
    const stepId = text(row.step_id);
    if (!stepId) return;
    const value = { module: moduleName, stepId, stepDesc: text(row.step_desc) };
    const exactKey = `${moduleName}\u0000${stepId}`;
    exact.set(exactKey, [...(exact.get(exactKey) || []), value]);
    byId.set(stepId, [...(byId.get(stepId) || []), value]);
  });
  return { exact, byId };
}

function sourceLabel(stepId, sources) {
  const descriptions = [...new Set((sources || []).map((source) => source.stepDesc).filter(Boolean))];
  if (descriptions.length === 1) return descriptions[0];
  if (descriptions.length > 1) return `복수 매칭 ${descriptions.length}건`;
  return stepId;
}

function sortedChildren(node) {
  const minimumStepId = (item) => {
    if (item.type === "step") return item.stepId;
    let minimum = "";
    for (const descendant of item.children.values()) {
      const stepId = minimumStepId(descendant);
      if (stepId && (!minimum || naturalStepCompare(stepId, minimum) < 0)) minimum = stepId;
    }
    return minimum;
  };
  return [...node.children.values()]
    .map((child, index) => ({ child, index }))
    .sort((left, right) => {
      if (left.child.type === "step" && right.child.type === "step") {
        return naturalStepCompare(left.child.stepId, right.child.stepId) || left.index - right.index;
      }
      const leftStepId = minimumStepId(left.child);
      const rightStepId = minimumStepId(right.child);
      if (leftStepId && rightStepId) {
        return naturalStepCompare(leftStepId, rightStepId) || left.index - right.index;
      }
      // Structural insertion order is meaningful; only sibling stage nodes
      // with a known stage are reordered by step_id.
      return left.index - right.index;
    })
    .map(({ child }) => child);
}

function buildTree(product, rows, mappingRows) {
  const root = makeNode("product", product, `product:${product}`);
  const lookup = stepLookup(mappingRows);
  const assigned = new Set();
  const moduleNode = (moduleName) => {
    if (!root.children.has(moduleName)) root.children.set(moduleName, makeNode("module", moduleName, `module:${moduleName}`, { module: moduleName }));
    return root.children.get(moduleName);
  };

  (rows || []).forEach((row, rowIndex) => {
    const moduleName = text(row.module) || EMPTY_MODULE;
    const parts = pathParts(row.path);
    let parent = moduleNode(moduleName);
    let fullPath = moduleName;
    (parts.length ? parts.slice(0, 5) : ["구조 미지정"]).forEach((part, level) => {
      fullPath = `${fullPath}/${part}`;
      const childKey = `path:${fullPath}`;
      if (!parent.children.has(childKey)) parent.children.set(childKey, makeNode("path", part, childKey, { module: moduleName, fullPath, level }));
      parent = parent.children.get(childKey);
    });
    const description = text(row.description);
    if (description && !parent.descriptions.includes(description)) parent.descriptions.push(description);
    splitStepIds(row.step_ids).forEach((stepId) => {
      assigned.add(stepId);
      const sources = lookup.exact.get(`${moduleName}\u0000${stepId}`) || lookup.byId.get(stepId) || [];
      const childKey = `step:${fullPath}:${stepId}:${rowIndex}`;
      parent.children.set(childKey, makeNode("step", sourceLabel(stepId, sources), childKey, {
        module: moduleName,
        fullPath,
        stepId,
        sources,
        descriptions: description ? [description] : [],
      }));
    });
  });

  (mappingRows || []).forEach((row) => {
    const stepId = text(row.step_id);
    if (!stepId || assigned.has(stepId)) return;
    const moduleName = text(row.module) || EMPTY_MODULE;
    const module = moduleNode(moduleName);
    const pathKey = `unassigned:${moduleName}`;
    if (!module.children.has(pathKey)) module.children.set(pathKey, makeNode("unassigned", "구조 미지정", pathKey, {
      module: moduleName,
      fullPath: `${moduleName}/구조 미지정`,
    }));
    const parent = module.children.get(pathKey);
    const childKey = `step:${moduleName}:unassigned:${stepId}`;
    const source = { module: moduleName, stepId, stepDesc: text(row.step_desc) };
    const existing = parent.children.get(childKey);
    if (existing) {
      existing.sources.push(source);
      existing.label = sourceLabel(stepId, existing.sources);
    } else {
      parent.children.set(childKey, makeNode("step", sourceLabel(stepId, [source]), childKey, {
        module: moduleName,
        fullPath: parent.fullPath,
        stepId,
        sources: [source],
        unassigned: true,
      }));
    }
  });

  return root;
}

function descendants(node, predicate) {
  const found = [];
  const visit = (item) => {
    if (predicate(item)) found.push(item);
    item.children?.forEach(visit);
  };
  visit(node);
  return found;
}

function matchesLink(node, link) {
  const linkedSteps = new Set((link?.step_ids || []).map(text));
  const linkedPaths = new Set((link?.paths || []).map((path) => pathParts(path).join("/")));
  const steps = descendants(node, (item) => item.type === "step").map((item) => item.stepId);
  if (steps.some((stepId) => linkedSteps.has(stepId))) return true;
  const fullPath = text(node.fullPath);
  if (fullPath && (linkedPaths.has(fullPath) || linkedPaths.has(fullPath.split("/").slice(1).join("/")))) return true;
  if (node.type === "module") return [...linkedPaths].some((path) => path === node.module || path.startsWith(`${node.module}/`));
  return node.type === "product" && (linkedSteps.size > 0 || linkedPaths.size > 0);
}

function TreeBranch({ node, selectedKey, onSelect }) {
  const children = sortedChildren(node);
  return <li className={`pws-tree-item pws-tree-item--${node.type}`}>
    <button
      type="button"
      className={`pws-node${selectedKey === node.key ? " is-selected" : ""}`}
      aria-pressed={selectedKey === node.key}
      onClick={() => onSelect(node.key)}
    >
      <span className="pws-node-kind" aria-hidden="true">{({ product: "P", module: "M", path: "S", unassigned: "?", step: "ID" })[node.type]}</span>
      <span>{node.label}</span>
      {node.type === "step" && <code>{node.stepId}</code>}
    </button>
    {children.length > 0 && <ul className="pws-tree">
      {children.map((child) => <TreeBranch key={child.key} node={child} selectedKey={selectedKey} onSelect={onSelect} />)}
    </ul>}
  </li>;
}

function findNode(root, key) {
  if (root.key === key) return root;
  for (const child of root.children.values()) {
    const found = findNode(child, key);
    if (found) return found;
  }
  return null;
}

function StructureDetail({ node, entries, entryLinks, onSelectEntry }) {
  if (!node) return <div className="pws-detail-empty">구조나 Step을 선택하면 근거와 연결된 기록을 확인할 수 있습니다.</div>;
  const steps = descendants(node, (item) => item.type === "step");
  const seenSources = new Set();
  const sourceSteps = steps.flatMap((step) => step.sources?.length
    ? step.sources.map((source, index) => ({ ...source, fromSource: true, key: `${step.key}:${index}` }))
    : [{ stepId: step.stepId, stepDesc: "", module: step.module, fromSource: false, key: step.key }])
    .filter((step) => {
      const key = `${step.stepId}\u0000${step.stepDesc}\u0000${step.module}\u0000${step.fromSource}`;
      if (seenSources.has(key)) return false;
      seenSources.add(key);
      return true;
    })
    .sort((left, right) => naturalStepCompare(left.stepId, right.stepId));
  const descriptions = [...new Set(descendants(node, (item) => item.descriptions?.length).flatMap((item) => item.descriptions))];
  const related = (entries || []).filter((entry) => matchesLink(node, entryLinks?.[entry.id]));
  return <section className="pws-detail" aria-live="polite">
    <p className="pws-eyebrow">{({ product: "제품", module: "모듈", path: "구조", unassigned: "미지정 구조", step: "매칭 Step" })[node.type]}</p>
    <h4>{node.type === "path" || node.type === "unassigned" ? node.fullPath : node.label}</h4>
    {descriptions.length > 0 && <div className="pws-detail-section"><h5>설명</h5>{descriptions.map((description) => <p key={description}>{description}</p>)}</div>}
    {sourceSteps.length > 0 && <div className="pws-detail-section">
      <h5>연결된 원본 Step</h5>
      <ul className="pws-step-list">{sourceSteps.map((step) => <li key={step.key}><code>{step.stepId}</code><span>{step.fromSource ? step.stepDesc || "설명 없음" : "현재 원본에서 찾을 수 없음"}{step.module && step.module !== node.module ? ` · ${step.module}` : ""}</span></li>)}</ul>
      {sourceSteps.some((step) => step.fromSource) && <p className="pws-source-note">원본 · Vehicle_matching.csv</p>}
    </div>}
    {related.length > 0 && <div className="pws-detail-section"><h5>관련 제품 기록</h5><ul className="pws-entry-list">
      {related.map((entry) => <li key={entry.id}><button type="button" onClick={() => onSelectEntry?.(entry.id)}>{entry.title || entry.id}</button><code>{entry.id}</code></li>)}
    </ul></div>}
    {!descriptions.length && !steps.length && !related.length && <p className="pws-muted">이 노드에 연결된 상세 정보가 없습니다.</p>}
  </section>;
}

function transitionPath(module, path, fallback) {
  const value = text(path);
  return value ? `${text(module) || EMPTY_MODULE}/${value}` : fallback;
}

function transitionMatchesEntry(change, link) {
  if (!link) return false;
  const linkedPaths = (link.paths || []).map((path) => pathParts(path).join("/"));
  const endpoints = [change.from_path, change.to_path]
    .map((path) => text(path) && `${text(change.module) || EMPTY_MODULE}/${text(path)}`)
    .filter(Boolean);
  if (endpoints.some((endpoint) => linkedPaths.some((path) => path === endpoint || path.startsWith(`${endpoint}/`) || endpoint.startsWith(`${path}/`)))) return true;
  return splitStepIds(change.step_ids).some((stepId) => (link.step_ids || []).map(text).includes(stepId));
}

function sortedTransitions(transitions) {
  return (transitions || []).map((change, index) => ({ change, index })).sort((left, right) => {
    const a = left.change.order == null ? Number.POSITIVE_INFINITY : Number(left.change.order);
    const b = right.change.order == null ? Number.POSITIVE_INFINITY : Number(right.change.order);
    return a - b || left.index - right.index;
  });
}

function TransitionMap({ transitions, entries, entryLinks, onSelectEntry }) {
  const items = sortedTransitions(transitions);
  if (!items.length) return null;
  return <section className="pws-transition-section" aria-label="구조 변화와 생성 순서">
    <div className="pws-section-heading"><div><h3>구조 변화와 생성 순서</h3><p>관리자가 입력한 변화 관계만 표시합니다. 빈 이전 구조는 생성, 빈 다음 구조는 제거를 뜻합니다.</p></div><span>{items.length}건</span></div>
    <ol className="pws-transition-list">
      {items.map(({ change, index }) => {
        const related = (entries || []).filter((entry) => transitionMatchesEntry(change, entryLinks?.[entry.id]));
        return <li key={`${change.module}-${change.from_path}-${change.to_path}-${index}`} className="pws-transition-item">
          <div className="pws-transition-route">
            <span>{transitionPath(change.module, change.from_path, "시작 없음 · 생성")}</span>
            <span className="pws-transition-arrow" aria-hidden="true">→</span>
            <span>{transitionPath(change.module, change.to_path, "최종 제거")}</span>
          </div>
          <div className="pws-transition-meta"><strong>{change.relation || "구조 변화"}</strong><span>{change.module || EMPTY_MODULE}</span>{change.order != null && <span>순서 {change.order}</span>}</div>
          {change.description && <p>{change.description}</p>}
          {change.step_ids?.length > 0 && <div className="pws-transition-evidence"><span>근거 Step</span><code>{splitStepIds(change.step_ids).join(", ")}</code></div>}
          <div className="pws-transition-evidence"><span>연결 기록</span>{related.length > 0 ? <ul className="pws-entry-list">{related.slice(0, 5).map((entry) => <li key={entry.id}><button type="button" onClick={() => onSelectEntry?.(entry.id)}>{entry.title || entry.id}</button><code>{entry.id}</code></li>)}</ul> : <span className="pws-muted">연결된 제품 기록 없음</span>}</div>
        </li>;
      })}
    </ol>
  </section>;
}

function KnowledgeOverview({ rows, mappingRows, transitions, entries, entryLinks }) {
  const linkedEntries = (entries || []).filter((entry) => {
    const link = entryLinks?.[entry.id];
    return (link?.step_ids?.length || 0) + (link?.paths?.length || 0) > 0;
  }).length;
  const modules = [...new Set([...(rows || []).map((row) => text(row.module)), ...(mappingRows || []).map((row) => text(row.module))].filter(Boolean))];
  return <section className="pws-knowledge" aria-label="제품 지식 개요">
    <div className="pws-section-heading"><div><h3>제품 지식 개요</h3><p>원본 매칭, 관리자가 확정한 구조, 변화 관계, 연결된 제품 기록을 한 문서에서 확인합니다.</p></div><span>{modules.length}개 모듈</span></div>
    <dl className="pws-knowledge-grid">
      <div><dt>확정 구조</dt><dd>{(rows || []).length}개</dd></div>
      <div><dt>원본 매칭 Step</dt><dd>{(mappingRows || []).length}개</dd></div>
      <div><dt>구조 변화</dt><dd>{(transitions || []).length}건</dd></div>
      <div><dt>연결 기록</dt><dd>{linkedEntries}건</dd></div>
    </dl>
    <p className="pws-source-note">확정되지 않은 구조·순서·인과관계는 자동으로 만들지 않고 미지정 또는 미확인 상태로 남깁니다.</p>
  </section>;
}

function RevisionPreview({ title, data }) {
  return <div className="pws-revision-preview">
    <strong>{title}</strong>
    <span>Revision {data?.revision ?? "—"} · 구조 {(data?.rows || []).length}행 · 변화 {(data?.transitions || []).length}건</span>
    <ul>{(data?.rows || []).slice(0, 8).map((row, index) => <li key={`${row.module}-${row.path}-${index}`}><code>{normalizedFullPath(row.module, row.path)}</code> · {splitStepIds(row.step_ids).join(", ") || "Step 없음"}</li>)}</ul>
    {(data?.rows || []).length > 8 && <span>외 {(data.rows.length - 8).toLocaleString()}행</span>}
    {(data?.transitions || []).length > 0 && <p className="pws-revision-transitions">변화 예시: {(data.transitions || []).slice(0, 3).map((change) => `${change.from_path || "생성"} → ${change.to_path || "제거"}`).join(" · ")}</p>}
  </div>;
}

const when = (value) => value ? new Date(value).toLocaleString("ko-KR") : "—";
const author = (value) => typeof value === "object" && value !== null ? value.name || value.username || "—" : value || "—";

export default function ProductStructure({ product, user, entries = [], onSelectEntry, onEditingChange }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [draftRows, setDraftRows] = useState(() => normalizeSpreadsheetRows([], COLUMNS, { minRows: 10, maxRows: 500 }));
  const [draftTransitions, setDraftTransitions] = useState(() => normalizeSpreadsheetRows([], TRANSITION_COLUMNS, { minRows: 6, maxRows: 500 }));
  const [baseRevision, setBaseRevision] = useState(null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [latestReview, setLatestReview] = useState(null);
  const [selectedKey, setSelectedKey] = useState("");
  const [seedWarning, setSeedWarning] = useState("");
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historySnapshot, setHistorySnapshot] = useState(null);
  const generation = useRef(0);
  const fetchAbort = useRef(null);
  const saveAbort = useRef(null);
  const historyAbort = useRef(null);
  const editingChange = useRef(onEditingChange);

  useEffect(() => { editingChange.current = onEditingChange; }, [onEditingChange]);
  useEffect(() => { editingChange.current?.(editing); }, [editing]);
  useEffect(() => () => editingChange.current?.(false), []);

  async function loadLatest(forReview = false) {
    if (!product) return;
    const ticket = generation.current;
    fetchAbort.current?.abort();
    const controller = new AbortController();
    fetchAbort.current = controller;
    setLoading(true);
    setError("");
    try {
      const next = await sf(`${API}?product=${encodeURIComponent(product)}`, { signal: controller.signal, cache: "no-store" });
      if (controller.signal.aborted || ticket !== generation.current) return;
      if (forReview && editing) setLatestReview(next);
      else setData(next);
    } catch (err) {
      if (err.name !== "AbortError" && ticket === generation.current) setError(err.message || "제품 구조를 불러오지 못했습니다.");
    } finally {
      if (!controller.signal.aborted && ticket === generation.current) setLoading(false);
    }
  }

  useEffect(() => {
    generation.current += 1;
    fetchAbort.current?.abort();
    saveAbort.current?.abort();
    historyAbort.current?.abort();
    setData(null);
    setError("");
    setNotice("");
    setEditing(false);
    setDraftTransitions(normalizeSpreadsheetRows([], TRANSITION_COLUMNS, { minRows: 6, maxRows: 500 }));
    setConflict(false);
    setLatestReview(null);
    setSelectedKey("");
    setSeedWarning("");
    setHistory(null);
    setHistoryLoading(false);
    setHistoryError("");
    setHistorySnapshot(null);
    setSaving(false);
    if (product) loadLatest(false);
    else setLoading(false);
    return () => { fetchAbort.current?.abort(); saveAbort.current?.abort(); historyAbort.current?.abort(); };
    // loadLatest intentionally follows the product generation owned by this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [product]);

  const mappingRows = data?.mapping?.rows || [];
  const tree = useMemo(() => data ? buildTree(product, data.rows || [], mappingRows) : null, [data, mappingRows, product]);
  const selectedNode = useMemo(() => tree ? findNode(tree, selectedKey) : null, [tree, selectedKey]);
  const validation = useMemo(() => filledGridRows(draftRows).flatMap((row, index) => {
    const messages = [];
    if (!text(row.path)) messages.push(`${index + 1}행: 구조 경로를 입력하세요.`);
    if (pathParts(row.path).length > 5) messages.push(`${index + 1}행: 구조 경로는 최대 5단계까지 입력할 수 있습니다.`);
    return messages;
  }), [draftRows]);
  const transitionValidation = useMemo(() => filledTransitionRows(draftTransitions).flatMap((row, index) => {
    const messages = [];
    if (!text(row.module)) messages.push(`${index + 1}번째 변화: 모듈을 입력하세요.`);
    if (!text(row.from_path) && !text(row.to_path)) messages.push(`${index + 1}번째 변화: 이전 구조 또는 다음 구조를 입력하세요.`);
    ["from_path", "to_path"].forEach((column) => {
      if (pathParts(row[column]).length > 5) messages.push(`${index + 1}번째 변화: 경로는 최대 5단계까지 입력할 수 있습니다.`);
    });
    if (text(row.order) && (!/^\d+$/.test(text(row.order)) || Number(row.order) < 1 || Number(row.order) > 500)) messages.push(`${index + 1}번째 변화: 순서는 1~500 정수여야 합니다.`);
    return messages;
  }), [draftTransitions]);
  const allValidation = [...validation, ...transitionValidation];

  function beginEditing() {
    if (!data || saving) return;
    setDraftRows(normalizeSpreadsheetRows((data.rows || []).map(rowForGrid), COLUMNS, { minRows: 10, maxRows: 500 }));
    setDraftTransitions(normalizeSpreadsheetRows((data.transitions || []).map(rowForTransitionGrid), TRANSITION_COLUMNS, { minRows: 6, maxRows: 500 }));
    setBaseRevision(data.revision);
    setConflict(false);
    setLatestReview(null);
    setError("");
    setNotice("");
    setSeedWarning("");
    setEditing(true);
  }

  function stopEditing() {
    if (saving) return;
    setEditing(false);
    setConflict(false);
    setLatestReview(null);
    setError("");
    setSeedWarning("");
  }

  function fillFromMatching() {
    const current = filledGridRows(draftRows).map((row) => ({ ...row }));
    const existingSteps = new Set(current.flatMap((row) => splitStepIds(row.step_ids)));
    const rowByPath = new Map(current.map((row, index) => [`${text(row.module) || EMPTY_MODULE}\u0000${text(row.path)}`, index]));
    let addedSteps = 0;
    const matchingByStep = new Map();
    mappingRows.forEach((row) => {
      const stepId = text(row.step_id);
      if (stepId) matchingByStep.set(stepId, [...(matchingByStep.get(stepId) || []), row]);
    });
    matchingByStep.forEach((sourceRows, stepId) => {
      if (!stepId || existingSteps.has(stepId)) return;
      const modules = [...new Set(sourceRows.map((row) => text(row.module)).filter(Boolean))];
      const descriptions = [...new Set(sourceRows.map((row) => text(row.step_desc)).filter(Boolean))];
      const moduleName = modules.length === 1 ? modules[0] : EMPTY_MODULE;
      const draftPath = (descriptions.length === 1 ? descriptions[0] : descriptions.length > 1 ? `복수 매칭 · ${stepId}` : stepId).replace(/\s*\/\s*/g, " · ");
      const key = `${moduleName}\u0000${draftPath}`;
      const existingIndex = rowByPath.get(key);
      if (existingIndex != null) {
        const target = current[existingIndex];
        target.step_ids = [...splitStepIds(target.step_ids), stepId].join(", ");
      } else {
        rowByPath.set(key, current.length);
        current.push({ module: moduleName, path: draftPath, step_ids: stepId, description: "" });
      }
      existingSteps.add(stepId);
      addedSteps += 1;
    });
    if (!addedSteps) {
      setNotice("매칭 원본의 모든 Step이 현재 초안에 포함되어 있습니다.");
      setSeedWarning("");
      return;
    }
    if (current.length > 500) {
      setSeedWarning(`초안이 500행 제한을 넘게 되어 매칭 ${addedSteps}개 Step을 추가하지 않았습니다. 기존 행을 구조물 기준으로 합친 뒤 다시 시도하세요.`);
      return;
    }
    setDraftRows(normalizeSpreadsheetRows(current, COLUMNS, { minRows: 10, maxRows: 500 }));
    setSeedWarning("공정명을 임시 구조명으로 가져왔습니다. 구조물 기준으로 묶고 이름을 수정하세요.");
    setNotice(`${addedSteps}개 Step을 로컬 초안에 추가했습니다. 아직 저장되지 않았습니다.`);
  }

  async function loadHistory() {
    if (historyLoading || !product) return;
    const ticket = generation.current;
    historyAbort.current?.abort();
    const controller = new AbortController();
    historyAbort.current = controller;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const result = await sf(`${API}/history?product=${encodeURIComponent(product)}`, { signal: controller.signal, cache: "no-store" });
      if (!controller.signal.aborted && ticket === generation.current) setHistory(result.history || []);
    } catch (err) {
      if (err.name !== "AbortError" && ticket === generation.current) setHistoryError(err.message || "구조 이력을 불러오지 못했습니다.");
    } finally {
      if (!controller.signal.aborted && ticket === generation.current) setHistoryLoading(false);
    }
  }

  function editSnapshot(snapshot) {
    if (!data || saving) return;
    setDraftRows(normalizeSpreadsheetRows((snapshot.rows || []).map(rowForGrid), COLUMNS, { minRows: 10, maxRows: 500 }));
    setDraftTransitions(normalizeSpreadsheetRows((snapshot.transitions || []).map(rowForTransitionGrid), TRANSITION_COLUMNS, { minRows: 6, maxRows: 500 }));
    setBaseRevision(data.revision);
    setConflict(false);
    setLatestReview(null);
    setError("");
    setSeedWarning("");
    setNotice(`Revision ${snapshot.revision}의 구조를 로컬 초안으로 가져왔습니다. 현재 Revision ${data.revision} 기준으로 검토 후 저장하세요.`);
    setEditing(true);
  }

  async function save(event) {
    event.preventDefault();
    if (saving || conflict || allValidation.length || !product) return;
    const ticket = generation.current;
    const controller = new AbortController();
    saveAbort.current = controller;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await sf(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product, expected_revision: baseRevision, rows: rowsForSave(draftRows), transitions: transitionsForSave(draftTransitions) }),
        signal: controller.signal,
      });
      if (controller.signal.aborted || ticket !== generation.current) return;
      setData(next);
      setEditing(false);
      setConflict(false);
      setLatestReview(null);
      setNotice("제품 구조를 저장했습니다.");
    } catch (err) {
      if (controller.signal.aborted || ticket !== generation.current) return;
      if (err.status === 409) setConflict(true);
      setError(err.message || "제품 구조를 저장하지 못했습니다.");
    } finally {
      if (!controller.signal.aborted && ticket === generation.current) setSaving(false);
    }
  }

  function rebaseDraft() {
    if (!latestReview) return;
    setData(latestReview);
    setBaseRevision(latestReview.revision);
    setLatestReview(null);
    setConflict(false);
    setError("");
    setNotice("최신 Revision을 기준으로 전환했습니다. 로컬 초안은 유지되었습니다. 최신 변경을 초안에 반영했는지 검토한 뒤 저장하세요.");
  }

  if (!product) return null;
  return <details className="pws-disclosure" open>
    <summary>제품 지식 개요 · 구조 · 연결도</summary>
    <div className="pws-content">
      {error && <Banner tone="danger"><span role="alert">{error}</span>{editing && <Button disabled={loading || saving} onClick={() => loadLatest(true)}>최신본 다시 불러오기</Button>}</Banner>}
      {notice && <Banner tone="info"><span role="status">{notice}</span></Banner>}
      {seedWarning && <Banner tone="warning"><span role="status">{seedWarning}</span></Banner>}
      {data?.mapping?.warning && <Banner tone="warning">{data.mapping.warning}</Banner>}
      {conflict && <Banner tone="warning">다른 사용자가 제품 구조를 수정했습니다. 로컬 초안은 유지됩니다. 최신본을 불러와 차이를 검토한 뒤 기준 Revision을 전환하세요. <Button disabled={loading || saving} onClick={() => loadLatest(true)}>{loading ? "최신본 확인 중…" : "최신본 불러오기"}</Button></Banner>}
      {latestReview && <section className="pws-conflict-review" aria-label="구조 Revision 검토">
        <h3>최신본과 로컬 초안 검토</h3>
        <div className="pws-revision-grid"><RevisionPreview title="서버 최신본" data={latestReview} /><RevisionPreview title="로컬 초안" data={{ revision: baseRevision, rows: rowsForSave(draftRows), transitions: transitionsForSave(draftTransitions) }} /></div>
        <p>서버 최신 변경 중 필요한 내용을 아래 초안에 직접 반영하세요. 기준 전환은 초안 내용을 바꾸지 않습니다.</p>
        <Button variant="primary" disabled={saving} onClick={rebaseDraft}>검토 완료 · 최신 Revision 기준으로 전환</Button>
      </section>}
      {loading && !data && <div className="pws-state" role="status">제품 구조를 불러오는 중…</div>}
      {!loading && !data && <div className="pws-state"><p>제품 구조를 표시할 수 없습니다.</p><Button onClick={() => loadLatest(false)}>다시 불러오기</Button></div>}
      {data && (editing ? <form className="pws-editor" onSubmit={save}>
        <div className="pws-heading"><div><h3>제품 구조 편집</h3><p>모듈 아래 구조 경로와 연결할 CSV Step ID를 입력하세요. 경로는 <code>Contact/Plug</code>처럼 슬래시로 구분합니다.</p></div><span>기준 Revision {baseRevision ?? "—"}</span></div>
        <div className="pws-editor-actions"><Button disabled={saving || mappingRows.length === 0} onClick={fillFromMatching}>매칭으로 초안 채우기</Button><span>공정명을 임시 구조명으로 가져옵니다. 구조물 기준으로 묶고 이름을 수정하세요. Excel·Google Sheets의 여러 셀을 그대로 붙여넣을 수 있습니다.</span></div>
        <SpreadsheetPasteGrid
          columns={COLUMNS}
          rows={draftRows}
          onChange={setDraftRows}
          ariaLabel="제품 구조 편집 표"
          aliases={{ 모듈: "module", 구조_경로: "path", "구조 경로": "path", 경로: "path", step_id: "step_ids", "step id": "step_ids", "step ids": "step_ids", "Step ID": "step_ids", 설명: "description" }}
          columnLabels={{ module: "모듈", path: "구조 경로", step_ids: "Step ID", description: "설명" }}
          placeholders={{ module: "예: MOL", path: "예: Contact/Plug", step_ids: "예: STEP-101, STEP-102", description: "구조 설명" }}
          disabled={saving}
          minRows={10}
          maxRows={500}
          maxHeight={365}
          minTableWidth={760}
          borderRadius={0}
        />
        <div className="pws-transition-editor">
          <div className="pws-heading"><div><h3>구조 변화 · 생성 순서</h3><p>이전 구조가 없어지거나 다음 구조가 생기는 관계를 관리자가 직접 입력하세요. 빈 이전 구조는 생성, 빈 다음 구조는 제거입니다. 순서·인과관계는 자동 추정하지 않습니다.</p></div><span>{filledTransitionRows(draftTransitions).length}건</span></div>
          <SpreadsheetPasteGrid
            columns={TRANSITION_COLUMNS}
            rows={draftTransitions}
            onChange={setDraftTransitions}
            ariaLabel="구조 변화 편집 표"
            aliases={{ 순서: "order", 모듈: "module", 이전_구조: "from_path", "이전 구조": "from_path", 다음_구조: "to_path", "다음 구조": "to_path", 관계: "relation", 설명: "description", 변화_설명: "description", "변화 설명·영향": "description", step_id: "step_ids", "Step ID": "step_ids" }}
            columnLabels={{ order: "순서", module: "모듈", from_path: "이전 구조", to_path: "다음 구조", relation: "관계", description: "변화 설명·영향", step_ids: "근거 Step ID" }}
            placeholders={{ order: "예: 1", module: "예: MOL", from_path: "예: Contact", to_path: "예: Contact/Plug", relation: "예: 충전 후 생성", description: "제거·충전·Split 영향·수정 근거", step_ids: "예: STEP-101" }}
            disabled={saving}
            minRows={6}
            maxRows={500}
            maxHeight={320}
            minTableWidth={1120}
            borderRadius={0}
          />
        </div>
        {allValidation.length > 0 && <Banner tone="danger"><span role="alert">{allValidation.slice(0, 5).join(" ")}{allValidation.length > 5 ? ` 외 ${allValidation.length - 5}건` : ""}</span></Banner>}
        <div className="pws-form-actions"><Button disabled={saving} onClick={stopEditing}>취소</Button><Button type="submit" variant="primary" disabled={saving || conflict || allValidation.length > 0}>{saving ? "저장 중…" : "저장"}</Button></div>
      </form> : <>
        <div className="pws-heading"><div><h3>{product}</h3><p>원본 매칭, 확정 구조, 관리자 입력 변화 관계, 연결 기록을 하나의 제품 지식 문서로 봅니다. 자동으로 순서나 인과관계를 만들지 않습니다.</p></div><div className="pws-heading-actions"><Button disabled={loading} onClick={() => loadLatest(false)}>{loading ? "새로고침 중…" : "새로고침"}</Button>{canManagePage(user, "productwiki") && <Button onClick={beginEditing}>구조 편집</Button>}</div></div>
        <div className="pws-meta">Revision {data.revision ?? "—"} · 구조 {(data.rows || []).length}행 · 매칭 {mappingRows.length}개 Step · 변화 {(data.transitions || []).length}건</div>
        <KnowledgeOverview rows={data.rows || []} mappingRows={mappingRows} transitions={data.transitions || []} entries={entries} entryLinks={data.entry_links || {}} />
        {tree && tree.children.size > 0 ? <div className="pws-layout">
          <div className="pws-tree-scroll" aria-label="제품 구조 계층"><ul className="pws-tree pws-tree-root"><TreeBranch node={tree} selectedKey={selectedKey} onSelect={setSelectedKey} /></ul></div>
          <StructureDetail node={selectedNode} entries={entries} entryLinks={data.entry_links || {}} onSelectEntry={onSelectEntry} />
        </div> : <div className="pws-state">저장된 구조나 매칭 Step이 없습니다.{canManagePage(user, "productwiki") && " 구조 편집에서 직접 입력하거나 매칭으로 초안을 채울 수 있습니다."}</div>}
        <TransitionMap transitions={data.transitions || []} entries={entries} entryLinks={data.entry_links || {}} onSelectEntry={onSelectEntry} />
        {canManagePage(user, "productwiki") && <details className="pws-history" onToggle={(event) => { if (event.currentTarget.open && history === null) loadHistory(); }}>
          <summary>구조 이력</summary>
          <p className="pws-muted">최근 저장 이력 최대 30건을 표시합니다.</p>
          {historyError && <Banner tone="danger"><span role="alert">{historyError}</span> <Button disabled={historyLoading} onClick={loadHistory}>다시 불러오기</Button></Banner>}
          {historyLoading && <p className="pws-muted" role="status">구조 이력을 불러오는 중…</p>}
          {history && history.length === 0 && <p className="pws-muted">저장된 구조 이력이 없습니다.</p>}
          {history && history.length > 0 && <div className="pws-history-layout"><ol className="pws-history-list">{history.map((snapshot) => <li key={`${snapshot.revision}-${snapshot.updated_at}`}><button type="button" className={historySnapshot?.revision === snapshot.revision ? "is-selected" : ""} onClick={() => setHistorySnapshot(snapshot)}><strong>Revision {snapshot.revision}</strong><span>{author(snapshot.updated_by)} · {when(snapshot.updated_at)} · {(snapshot.rows || []).length}행</span></button></li>)}</ol>
            {historySnapshot && <div className="pws-history-detail"><RevisionPreview title={`Revision ${historySnapshot.revision}`} data={historySnapshot} /><Button variant="primary" onClick={() => editSnapshot(historySnapshot)}>이 구조로 편집</Button></div>}
          </div>}
        </details>}
      </>)}
    </div>
  </details>;
}
