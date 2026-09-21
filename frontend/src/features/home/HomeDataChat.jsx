import { useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import SplitTableSnapshotView from "../../components/SplitTableSnapshotView";
import { sf } from "../../lib/api";
import "./HomeDataChat.css";
import TegChatMaps from "./TegChatMaps";

const PAGE_SIZE = 50;
const API_HISTORY_MESSAGES = 20;
const API_HISTORY_CHARS = 4000;

const FEATURE_PAGE_MAP = {
  eta: "lottracker",
  splittable: "splittable",
  "splittable.plan": "splittable",
  "splittable.history": "splittable",
  location: "lotlocation",
  lot_progress: "lotlocation",
  yield_map: "yieldmap",
  "yield_map.map": "yieldmap",
  tracker: "tracker",
  "tracker.issues": "tracker",
  "tracker.issue": "tracker",
  watchlist: "lotmanage",
  "watchlist.lots": "lotmanage",
  informs: "inform",
  "informs.recent": "inform",
  "informs.by_lot": "inform",
  lot_management: "lotmanage",
  "lot_management.table": "lotmanage",
  "lot_management.my_lots": "lotmanage",
  dashboard: "dashboard",
  "dashboard.summary": "dashboard",
  "dashboard.stuck_lots": "dashboard",
  "dashboard.charts": "dashboard",
  chart: "chartbuilder",
  "report.template": "templatereport",
  teg: "tegmap",
  "teg.locations": "tegmap",
  "teg.coordinates": "tegmap",
  "teg.mapfiles": "tegmap",
};

const FEATURE_PAGE_NAMES = {
  splittable: "SplitTable",
  location: "Lot 현위치",
  lot_progress: "Lot 현위치",
  yield_map: "Yield Map",
  tracker: "ET 트래커",
  watchlist: "Lot 관리",
  informs: "모듈 인폼",
  lot_management: "Lot 관리",
  dashboard: "대시보드",
  chart: "차트 빌더",
  "report.template": "Template Report",
  teg: "TEG Map",
};

function asText(value) {
  if (value == null) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function storageKeys(username) {
  const scope = username || "guest";
  return {
    conversation: `flow:home-chat:${scope}`,
    chartTransfer: `flow:chat:chart:${scope}`,
  };
}

function readJson(key, fallback) {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    return value && typeof value === "object" ? value : fallback;
  } catch {
    return fallback;
  }
}

function newConversationId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function emptyChatState(username) {
  return { username, conversationId: newConversationId(), messages: [], context: {}, updatedAt: Date.now() };
}

function loadChatState(username) {
  const keys = storageKeys(username);
  const stored = readJson(keys.conversation, null);
  if (stored?.username === username && Array.isArray(stored.messages)) {
    return {
      username,
      conversationId: stored.conversationId || stored.id || newConversationId(),
      messages: normalizeMessages(stored.messages),
      context: stored.context && typeof stored.context === "object" ? stored.context : {},
      updatedAt: Number(stored.updatedAt) || Date.now(),
    };
  }

  const chartTransfer = readJson(keys.chartTransfer, {});
  return {
    ...emptyChatState(username),
    context: chartTransfer && typeof chartTransfer === "object" ? chartTransfer : {},
  };
}

function persistChatState(state) {
  try {
    sessionStorage.setItem(storageKeys(state.username).conversation, JSON.stringify(state));
  } catch {}
}

function resultRows(table) {
  if (Array.isArray(table?.rows)) return table.rows;
  if (Array.isArray(table)) return table;
  return [];
}

function resultColumns(table, rows) {
  const explicit = Array.isArray(table?.columns) ? table.columns : [];
  if (explicit.length) {
    return explicit.map((column, index) => {
      if (column && typeof column === "object") {
        const key = column.key || column.name || column.field || String(index);
        return { key, label: column.label || column.title || key };
      }
      return { key: asText(column), label: asText(column) };
    });
  }
  return [...new Set(rows.flatMap((row) => (
    row && typeof row === "object" && !Array.isArray(row) ? Object.keys(row) : []
  )))].map((key) => ({ key, label: key }));
}

function downloadTableAsCsv(table, filename = "dataset.csv") {
  const rows = resultRows(table);
  const columns = resultColumns(table, rows);
  if (!rows.length || !columns.length) return;

  const headerRow = columns.map((col) => `"${String(col.label || col.key || "").replace(/"/g, '""')}"`).join(",");
  const dataRows = rows.map((row) =>
    columns.map((col, idx) => {
      const val = asText(Array.isArray(row) ? row[idx] : row?.[col.key]);
      return `"${val.replace(/"/g, '""')}"`;
    }).join(",")
  );

  const csvContent = "\uFEFF" + [headerRow, ...dataRows].join("\r\n");
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", filename.endsWith(".csv") ? filename : `${filename}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function DataTable({ table, downloadName = "dataset", maxPreviewRows = 0, filterText = "" }) {
  const allRows = resultRows(table);
  const columns = resultColumns(table, allRows);
  const [page, setPage] = useState(0);

  // In-memory quick filter
  const rows = useMemo(() => {
    if (!filterText.trim()) return allRows;
    const query = filterText.toLowerCase();
    return allRows.filter((row) => {
      if (row == null) return false;
      if (typeof row === "object") {
        return Object.values(row).some((val) => asText(val).toLowerCase().includes(query));
      }
      return asText(row).toLowerCase().includes(query);
    });
  }, [allRows, filterText]);

  const total = Number(table?.total ?? table?.row_count ?? allRows.length);
  const isCapped = maxPreviewRows > 0 && rows.length > maxPreviewRows;
  const displayRows = isCapped ? rows.slice(0, maxPreviewRows) : rows;

  const pages = Math.max(1, Math.ceil(displayRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pages - 1);
  const pageRows = displayRows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);

  useEffect(() => setPage(0), [table, filterText]);
  if (!allRows.length || !columns.length) return null;

  return (
    <div className="home-data-chat__table-wrap">
      <div className="home-data-chat__table-toolbar">
        <div className="home-data-chat__table-meta">
          <span className="home-data-chat__table-count">
            {filterText ? `검색 결과 ${rows.length}행 (전체 ${total}행)` : `총 ${total}행`}
            {isCapped && <span className="home-data-chat__table-capped-tag">미리보기 {maxPreviewRows}행</span>}
          </span>
        </div>
        <button
          type="button"
          className="home-data-chat__download-btn"
          onClick={() => downloadTableAsCsv(table, `${downloadName}_${Date.now()}.csv`)}
          title="전체 데이터셋을 CSV(Excel 호환)로 다운로드합니다"
        >
          📥 CSV 다운로드
        </button>
      </div>
      <div className="home-data-chat__table-scroll">
        <table className="home-data-chat__table">
          <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
          <tbody>
            {pageRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {columns.map((column, columnIndex) => (
                  <td key={column.key}>{asText(Array.isArray(row) ? row[columnIndex] : row?.[column.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="home-data-chat__pagination">
          <button type="button" disabled={safePage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))} aria-label="이전 표 페이지">←</button>
          <span>{safePage + 1} / {pages} · {displayRows.length}행</span>
          <button type="button" disabled={safePage + 1 >= pages} onClick={() => setPage((value) => Math.min(pages - 1, value + 1))} aria-label="다음 표 페이지">→</button>
        </div>
      )}
    </div>
  );
}

function answerText(response) {
  return asText(response?.reply || response?.answer || response?.tool?.answer || "응답이 없습니다.");
}

function withoutContext(response) {
  if (!response || typeof response !== "object") return {};
  const { context: _context, ...displayResponse } = response;
  return displayResponse;
}

function tableFromTool(tool) {
  if (tool?.table) return tool.table;
  if (tool?.split_view) return tool.split_view;
  if (Array.isArray(tool?.rows)) return { rows: tool.rows, columns: tool.columns, total: tool.total };
  return null;
}

function nativeSplitViewFromTool(tool) {
  const splitView = tool?.split_view;
  const isPlanPreview = tool?.feature === "splittable.plan"
    || String(tool?.action || "").startsWith("splittable.plan")
    || tool?.approval?.status === "pending";
  return !isPlanPreview && Array.isArray(splitView?.headers) && Array.isArray(splitView?.rows)
    ? splitView
    : null;
}

function splitViewSource(tool) {
  const context = tool?.context || {};
  return [context.root_lot_id || context.lot_id, context.custom_name].filter(Boolean).join(" · ");
}

function responseContext(previous, response) {
  if (response?.context && typeof response.context === "object") return response.context;
  const tool = response?.tool && typeof response.tool === "object" ? response.tool : {};
  const next = { ...(previous || {}) };
  const table = tableFromTool(tool);
  if (table) next.table = table;
  if (tool.chart_result && typeof tool.chart_result === "object") next.chart_result = tool.chart_result;
  if (tool.definition_code) next.definition_code = tool.definition_code;
  if (tool.template_code) next.template_code = tool.template_code;
  if (tool.report_template && typeof tool.report_template === "object") next.report_template = tool.report_template;
  return next;
}

function apiHistory(messages) {
  return messages
    .filter((message) => (message.role === "user" || message.role === "assistant") && message.content && !message.error)
    .slice(-API_HISTORY_MESSAGES)
    .map((message) => ({ role: message.role, content: message.content.slice(0, API_HISTORY_CHARS) }));
}

function normalizeMessages(messages) {
  return messages.filter((message) => message && (message.role === "user" || message.role === "assistant"))
    .map((message, index) => ({
      id: message.id || `${Date.now()}-${index}`,
      role: message.role,
      content: asText(message.content),
      ...(message.response ? { response: message.response } : {}),
      ...(message.error ? { error: message.error } : {}),
    }));
}

function ExecutionTraceCard({ trace, rawGuideText = "" }) {
  if (!trace && !rawGuideText) return null;

  const intent = trace?.intent;
  const sources = Array.isArray(trace?.sources) ? trace.sources : [];
  const steps = Array.isArray(trace?.steps) ? trace.steps : [];
  const query = trace?.query;
  const illustrativeQuery = trace?.illustrative_query;

  return (
    <div className="home-data-chat__trace-card" aria-label="의도 해석 및 실행 경로">
      <div className="home-data-chat__trace-header">
        <span className="home-data-chat__trace-badge">의도 해석 및 실행 경로 (Execution Trace)</span>
      </div>

      {intent && (
        <div className="home-data-chat__trace-section">
          <span className="home-data-chat__trace-label">🎯 발화 의도:</span>
          <span className="home-data-chat__trace-intent">{intent}</span>
        </div>
      )}

      {sources.length > 0 && (
        <div className="home-data-chat__trace-section">
          <span className="home-data-chat__trace-label">🗄️ 데이터 원천:</span>
          <div className="home-data-chat__trace-sources">
            {sources.map((src, idx) => (
              <span key={idx} className="home-data-chat__source-pill">{src}</span>
            ))}
          </div>
        </div>
      )}

      {steps.length > 0 && (
        <div className="home-data-chat__trace-section">
            <span className="home-data-chat__trace-label">🧭 실행 설명:</span>
          <div className="home-data-chat__trace-breadcrumbs">
            {steps.map((step, idx) => (
              <span key={idx} className="home-data-chat__breadcrumb-item">
                {idx > 0 && <span className="home-data-chat__breadcrumb-arrow">➔</span>}
                <span className="home-data-chat__step-pill">{step}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {query && (
        <div className="home-data-chat__trace-section">
          <span className="home-data-chat__trace-label">💻 실행 쿼리/파라미터:</span>
          <pre className="home-data-chat__trace-query"><code>{query}</code></pre>
        </div>
      )}

      {illustrativeQuery && !query && (
        <details className="home-data-chat__trace-section">
          <summary>설명용 예시 (실제 SQL 아님)</summary>
          <pre className="home-data-chat__trace-query"><code>{illustrativeQuery}</code></pre>
        </details>
      )}
      {!trace && rawGuideText && (
        <div className="home-data-chat__guide-body">{rawGuideText}</div>
      )}
    </div>
  );
}

function RoutingTraceCard({ trace }) {
  if (!trace || typeof trace !== "object") return null;
  const bindings = trace.bindings && typeof trace.bindings === "object" ? trace.bindings : {};
  const sources = Array.isArray(trace.sources) ? trace.sources : [];
  const missing = Array.isArray(trace.missing) ? trace.missing : [];
  return (
    <details className="home-data-chat__routing-trace" open>
      <summary>라우팅 해석 <span>{trace.rule_title || trace.rule_id || trace.route || "확인"}</span></summary>
      <div className="home-data-chat__routing-trace-body">
        {trace.normalized_question && <div><b>정규화된 질문</b><span>{trace.normalized_question}</span></div>}
        {(trace.route || trace.action) && <div><b>경로</b><span>{[trace.route, trace.action].filter(Boolean).join(" · ")}</span></div>}
        {Object.keys(bindings).length > 0 && <div><b>바인딩</b><span>{Object.entries(bindings).map(([key, value]) => `${key}=${asText(value)}`).join(" · ")}</span></div>}
        {sources.length > 0 && <div><b>원천</b><span>{sources.join(" · ")}</span></div>}
        {missing.length > 0 && <div className="is-missing"><b>추가 입력</b><span>{missing.join(" · ")}</span></div>}
        {trace.status && <div><b>상태</b><span>{trace.status}</span></div>}
      </div>
    </details>
  );
}

function productOptions(tool) {
  const clarification = tool?.clarification;
  if (clarification?.kind && Array.isArray(clarification.options)) {
    return clarification.options
      .map((option) => ({
        label: asText(option?.label ?? option?.value).trim(),
        value: asText(option?.value ?? option?.label).trim(),
      }))
      .filter((option) => option.label && option.value)
      .slice(0, clarification.kind?.startsWith("inline_") ? 100 : 5);
  }

  const missingProduct = Array.isArray(tool?.missing) && tool.missing.some((item) => asText(item).toLowerCase() === "product");
  if (!missingProduct) return [];
  const rows = resultRows(tool?.table || tool?.split_view);
  const values = Array.isArray(tool?.products) ? tool.products : rows;
  return values.map((row) => {
    if (typeof row === "string" || typeof row === "number") return asText(row).trim();
    if (Array.isArray(row)) return asText(row[0]).trim();
    if (row && typeof row === "object") {
      const key = Object.keys(row).find((name) => /^(product|product_name|product_id|name|value)$/i.test(name));
      return key ? asText(row[key]).trim() : "";
    }
    return "";
  }).filter(Boolean).filter((value, index, valuesList) => valuesList.indexOf(value) === index)
    .slice(0, 5)
    .map((value) => ({ label: value, value }));
}

function isProductRequest(tool) {
  return ["product", "custom_set", "split_column", "split_value", "eta_reference", "inline_measure", "inline_time", "inline_lot_scope"].includes(tool?.clarification?.kind)
    || (Array.isArray(tool?.missing) && tool.missing.some((item) => asText(item).toLowerCase() === "product"));
}

function ProductClarification({ tool, onSubmit, disabled = false }) {
  const clarification = tool?.clarification;
  const options = productOptions(tool);
  const canAskOther = clarification?.kind ? clarification.allow_other !== false : true;
  const title = clarification?.title || "제품 선택";
  const placeholder = clarification?.placeholder || "제품명을 입력하세요";
  const [showOther, setShowOther] = useState(false);
  const [value, setValue] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    if (showOther) inputRef.current?.focus();
  }, [showOther]);

  if (!isProductRequest(tool) || (!options.length && !canAskOther)) return null;
  const submitOther = (event) => {
    event.preventDefault();
    const next = value.trim();
    if (!next || disabled) return;
    onSubmit(next);
    setValue("");
  };

  return (
    <div className="home-data-chat__product-clarification" aria-label={title}>
      {options.length > 0 && (
        <div className="home-data-chat__product-options">
          <span className="home-data-chat__product-prompt">{title}</span>
          <div className="home-data-chat__actions">
            {options.map((option) => (
              <button type="button" key={option.value} disabled={disabled} onClick={() => onSubmit(option.value)}>
                {option.label}
              </button>
            ))}
          </div>
        </div>
      )}
      {canAskOther && (
        <div className="home-data-chat__product-other">
          {!showOther ? (
            <button type="button" className="home-data-chat__product-other-toggle" disabled={disabled} onClick={() => setShowOther(true)}>
              기타 직접 입력
            </button>
          ) : (
            <form className="home-data-chat__product-other-form" onSubmit={submitOther}>
              <input
                ref={inputRef}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                placeholder={placeholder}
                disabled={disabled}
                aria-label={placeholder}
              />
              <button type="submit" disabled={disabled || !value.trim()}>확인</button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}

function BubbleContent({ message, onExplore }) {
  const content = message.content || "";
  const tool = message.response?.tool;
  const trace = tool?.execution_trace;
  const routingTrace = message.response?.routing_trace;

  let displayBody = content;
  let rawGuideText = "";

  if (typeof content === "string") {
    if (content.includes("────────────────────────────────────────")) {
      const parts = content.split("────────────────────────────────────────");
      rawGuideText = parts[0].replace("[도메인 해석 및 실행 경로 (Trace)]", "").replace("[도메인 해석 가이드]", "").trim();
      displayBody = parts.slice(1).join("────────────────────────────────────────").trim();
    } else if (content.startsWith("[도메인 해석 및 실행 경로 (Trace)]") || content.startsWith("[도메인 해석 가이드]")) {
      rawGuideText = content;
      displayBody = "";
    }
  }

  const nativeSplitView = nativeSplitViewFromTool(tool);
  const table = nativeSplitView ? null : (tool?.table || tool?.split_view);
  const relatedTegs = Array.isArray(tool?.related_tegs) ? tool.related_tegs : [];
  const currentProduct = tool?.context?.product || "";

  return (
    <div className="home-data-chat__bubble-inner">
      {(trace || rawGuideText) && (
        <ExecutionTraceCard trace={trace} rawGuideText={rawGuideText} />
      )}

      <RoutingTraceCard trace={routingTrace} />

      {displayBody && (
        <div className="home-data-chat__guided-body">{displayBody}</div>
      )}

      {nativeSplitView && !isProductRequest(tool) && (
        <div className="home-data-chat__inline-data-preview">
          <div className="home-data-chat__data-preview-bar">
            <span className="home-data-chat__preview-title">📊 SplitTable 미리보기</span>
          </div>
          <SplitTableSnapshotView
            stView={nativeSplitView}
            product={tool?.context?.product || ""}
            source={splitViewSource(tool)}
            showTitle={false}
            maxHeight={380}
          />
        </div>
      )}

      {table && !isProductRequest(tool) && (
        <div className="home-data-chat__inline-data-preview">
          <div className="home-data-chat__data-preview-bar">
            <span className="home-data-chat__preview-title">📊 추출 데이터셋 미리보기</span>
          </div>
          <DataTable
            table={table}
            downloadName={tool?.feature || "extracted_data"}
            maxPreviewRows={12}
          />
        </div>
      )}

      {relatedTegs.length > 0 && onExplore && (
        <div className="home-data-chat__related-tegs-box">
          <span className="home-data-chat__related-tegs-label">📍 {currentProduct || "제품"}의 다른 TEG 위치 확인하기 (클릭하여 탐색):</span>
          <div className="home-data-chat__related-tegs-list">
            {relatedTegs.map((tegName) => (
              <button
                key={tegName}
                type="button"
                className="home-data-chat__related-teg-chip"
                onClick={() => onExplore(`${currentProduct} ${tegName} TEG 위치 보여줘`)}
                title={`${currentProduct}의 ${tegName} TEG 위치를 즉시 조회합니다`}
              >
                {tegName}
              </button>
            ))}
          </div>
        </div>
      )}

      <TegChatMaps maps={tool?.teg_maps} view={tool?.teg_view} />
    </div>
  );
}

function BatchContent({ response, onExplore, onOpenWorkspace, canRespond }) {
  const questions = Array.isArray(response?.questions) ? response.questions : [];
  if (!questions.length) return null;
  return (
    <div className="home-data-chat__batch" aria-label="여러 질문 결과">
      <div className="home-data-chat__batch-summary">질문 {response.batch?.completed ?? 0}/{response.batch?.total ?? questions.length}개 처리됨</div>
      {questions.map((item, index) => {
        const child = item?.response && typeof item.response === "object" ? item.response : {};
        const status = item?.status || "deferred";
        return (
          <section className={`home-data-chat__batch-item is-${status}`} key={`${index}-${item?.question || "question"}`}>
            <div className="home-data-chat__batch-question"><span>{index + 1}</span>{item?.question || "질문"}<b>{status === "completed" ? "완료" : status === "needs_input" ? "입력 필요" : status === "failed" ? "실패" : "대기"}</b></div>
            {item?.reason && <div className="home-data-chat__batch-reason">{item.reason}</div>}
            {(child.reply || child.answer || child.tool) && <BubbleContent message={{ content: answerText(child), response: child }} onExplore={onExplore} />}
            {child.tool && isProductRequest(child.tool) && (
              <ProductClarification tool={child.tool} onSubmit={onExplore} disabled={!canRespond} />
            )}
            {child.tool && onOpenWorkspace && (child.tool.feature || child.tool.chart_result || child.tool.table) && (
              <button type="button" className="home-data-chat__batch-workspace-btn" onClick={() => onOpenWorkspace(child.tool)}>🖥️ 작업창 열기</button>
            )}
          </section>
        );
      })}
    </div>
  );
}

function featureInfo(feature, tool) {
  const icons = {
    splittable: "📋",
    "splittable.plan": "✏️",
    location: "📍",
    lot_progress: "📍",
    yield_map: "🗺️",
    "yield_map.map": "🗺️",
    tracker: "🎯",
    "tracker.issues": "🎯",
    "tracker.issue": "🎯",
    watchlist: "⭐",
    "watchlist.lots": "⭐",
    informs: "📢",
    "informs.recent": "📢",
    "informs.by_lot": "📢",
    lot_management: "📊",
    "lot_management.table": "📊",
    "lot_management.my_lots": "📊",
    dashboard: "📈",
    "dashboard.summary": "📈",
    "dashboard.stuck_lots": "📈",
    "dashboard.charts": "📈",
    chart: "📉",
    "report.template": "📝",
    teg: "📍",
    "teg.locations": "📍",
    "teg.coordinates": "📍",
    "teg.mapfiles": "🗺️",
  };
  const titles = {
    eta: "Lot 도착·완료 예상 시각",
    splittable: "SplitTable 조회",
    "splittable.history": "SplitTable 변경 이력",
    "splittable.plan": "SplitTable 계획 배정",
    location: "Lot 위치 및 공정 진도",
    lot_progress: "Lot 진행 현황",
    yield_map: "Yield Map (수율 / 웨이퍼 맵)",
    "yield_map.map": "Yield Map (수율 / 웨이퍼 맵)",
    tracker: "ET 이슈 트래커",
    "tracker.issues": "ET 이슈 목록",
    "tracker.issue": "ET 이슈 상세",
    watchlist: "관심 랏 (Watchlist)",
    "watchlist.lots": "관심 랏 목록",
    informs: "공정 모듈 인폼",
    "informs.recent": "최근 공정 인폼",
    "informs.by_lot": "Lot 인폼 스레드",
    lot_management: "랏 관리 (Lot Management)",
    "lot_management.table": "Lot 관리 현황",
    "lot_management.my_lots": "내 관심 랏 현황",
    dashboard: "대시보드 지표 요약",
    "dashboard.summary": "대시보드 요약 지표",
    "dashboard.stuck_lots": "대시보드 정체 랏",
    "dashboard.charts": "대시보드 차트 목록",
    chart: "데이터 차트",
    "report.template": "리포트 템플릿 초안",
    teg: "TEG 위치 및 좌표 조회",
    "teg.locations": "TEG 위치 조회",
    "teg.coordinates": "TEG 좌표 조회",
    "teg.mapfiles": "Mapfile 검증 현황",
  };
  const ctx = tool?.context || {};
  const subtitleParts = [
    ctx.product || "",
    ctx.root_lot_id || ctx.lot_id || "",
    ctx.custom_name ? `${ctx.custom_name} 세트` : "",
    ctx.bin_name ? `BIN ${ctx.bin_name}` : "",
    ctx.category || "",
    ctx.status ? `상태: ${ctx.status}` : "",
  ].filter(Boolean);
  return {
    icon: icons[feature] || "📊",
    title: titles[feature] || titles[tool?.action] || feature || "데이터 뷰",
    subtitle: subtitleParts.join(" · "),
  };
}

function ModelStatus({ refreshKey = 0, probeKey = 0, turnUsage = null }) {
  const [model, setModel] = useState({});
  const [checking, setChecking] = useState(false);
  const active = useRef(true);
  const busy = useRef(false);
  const lastProbeKey = useRef(0);
  const refresh = async (probe = false) => {
    if (busy.current) return;
    busy.current = true;
    if (probe) setChecking(true);
    try {
      const result = await sf(probe ? "/api/home-agent/probe" : "/api/home-agent/status", { method: probe ? "POST" : "GET" });
      if (active.current) setModel(result.model || {});
    } catch {
      if (active.current) setModel({ status: "disconnected", message: "AI 서버 연결을 확인할 수 없습니다. 서버 연결 설정과 운영서버 상태를 확인해 주세요." });
    } finally {
      busy.current = false;
      if (active.current) setChecking(false);
    }
  };
  useEffect(() => {
    active.current = true;
    if (probeKey && lastProbeKey.current !== probeKey) {
      lastProbeKey.current = probeKey;
      refresh(true);
    } else {
      refresh();
    }
    const timer = setInterval(() => refresh(), 30000);
    return () => { active.current = false; clearInterval(timer); };
  }, [probeKey, refreshKey]);
  const labels = { connected: "연결됨", disconnected: "연결 끊김", disabled: "사용 안 함", unconfigured: "미설정" };
  const status = checking ? "checking" : (model.status || "unknown");
  const modelName = [model.provider, model.model].filter(Boolean).join(" · ") || "확인 중";
  return (
    <div className="home-data-chat__model-bar">
      <span className="home-data-chat__model-name">현재 모델 · <strong>{modelName}</strong></span>
      <span className={`home-data-chat__connection is-${status}`} role="status" title={model.message || "모델 연결 상태"}>
        <i aria-hidden="true" />
        {checking ? "검사 중" : (labels[model.status] || "확인 필요")}
      </span>
      <button type="button" onClick={() => refresh(true)} disabled={checking || Number(model.usage?.minute_calls_remaining) <= 0}>{checking ? "검사 중…" : "연결 검사"}</button>
      {turnUsage && <span className="home-data-chat__usage">이번 턴 {turnUsage.llm_calls_used ?? 0}/{turnUsage.llm_call_limit ?? 6} · 잔여 {turnUsage.llm_calls_remaining ?? "—"}</span>}
      {(model.usage || turnUsage) && <span>분당 잔여 {(model.usage || turnUsage).minute_calls_remaining ?? "—"}/{(model.usage || turnUsage).minute_call_limit ?? "—"}</span>}
    </div>
  );
}

function WorkspaceTeg({ tool, filterText = "", onExplore }) {
  const ctx = tool.context || {};
  const table = tool.table;
  const rows = resultRows(table);
  const firstRow = rows[0] || {};
  const product = ctx.product || tool.product || firstRow.product || "-";
  const tegName = tool.teg || ctx.target_teg || firstRow.teg || (Array.isArray(ctx.teg_names) ? ctx.teg_names[0] : "-");
  const relatedTegs = Array.isArray(tool.related_tegs) ? tool.related_tegs : [];

  return (
    <div className="home-workspace__teg-container">
      <div className="home-workspace__meta-bar">
        <span className="home-workspace__meta-item">제품: <strong>{product}</strong></span>
        <span className="home-workspace__meta-item">클릭 확인 TEG: <strong>{tegName}</strong></span>
        {tool.map_name && <span className="home-workspace__meta-item">Mapfile: <strong>{tool.map_name}</strong></span>}
      </div>

      <div className="home-workspace__card-grid">
      <div className="home-workspace__card">
          <div className="home-workspace__card-label">조회 제품</div>
          <div className="home-workspace__card-value">{product}</div>
        </div>
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">확인한 TEG 키</div>
          <div className="home-workspace__card-value is-highlight">{tegName}</div>
        </div>
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">검출된 위치 수</div>
          <div className="home-workspace__card-value">{rows.length}개 위치</div>
        </div>
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">{firstRow.abs_x !== undefined ? "웨이퍼 절대 좌표" : "샷 내 좌표"} (X, Y)</div>
        <div className="home-workspace__card-value">
            {firstRow.abs_x !== undefined
              ? `(${firstRow.abs_x}, ${firstRow.abs_y}) mm`
              : firstRow.ebeam_x !== undefined
                ? `(${firstRow.ebeam_x}, ${firstRow.ebeam_y}) mm`
                : "-"}
          </div>
        </div>
      </div>

      {onExplore && product !== "-" && tegName !== "-" && (
        <div className="home-workspace__quick-explore-bar">
          <span className="home-workspace__explore-label">TEG 다시 조회:</span>
          <button type="button" className="home-workspace__explore-chip"
            onClick={() => onExplore(`${product} ${tegName} TEG 위치 어디있어?`)}>
            위치
          </button>
          <button type="button" className="home-workspace__explore-chip"
            onClick={() => onExplore(`${product} ${tegName} TEG 절대 좌표 알려줘`)}>
            절대 좌표
          </button>
        </div>
      )}

      {relatedTegs.length > 0 && onExplore && (
        <div className="home-workspace__teg-picker">
          <div className="home-workspace__picker-header">
            <span>🖱️ 다른 TEG 클릭하여 바로 찾아가기:</span>
          </div>
          <div className="home-workspace__teg-chips">
            {relatedTegs.map((teg) => (
              <button
                key={teg}
                type="button"
                className={`home-workspace__teg-btn${teg === tegName ? " is-active" : ""}`}
                onClick={() => onExplore(`${product} ${teg} TEG 위치 보여줘`)}
                title={`${product}의 ${teg} TEG 위치로 즉시 전환`}
              >
                {teg}
              </button>
            ))}
          </div>
        </div>
      )}

      <TegChatMaps maps={tool.teg_maps} view={tool.teg_view} />

      {table && (
        <DataTable
          table={table}
          downloadName={`teg_${product}_${tegName}`}
          filterText={filterText}
        />
      )}
    </div>
  );
}

function WorkspaceSplitTable({ tool, filterText = "", onDecision, onNavigate }) {
  const nativeSplitView = nativeSplitViewFromTool(tool);
  const filteredSplitView = useMemo(() => {
    const query = filterText.trim().toLowerCase();
    if (!nativeSplitView || !query) return nativeSplitView;
    return {
      ...nativeSplitView,
      rows: nativeSplitView.rows.filter((row) => asText(row).toLowerCase().includes(query)),
    };
  }, [nativeSplitView, filterText]);
  const table = nativeSplitView ? null : (tool.table || tool.split_view);
  const ctx = tool.context || {};
  return (
    <div className="home-workspace__split-container">
      <div className="home-workspace__meta-bar">
        <span className="home-workspace__meta-item">제품: <strong>{ctx.product || "-"}</strong></span>
        <span className="home-workspace__meta-item">Lot: <strong>{ctx.root_lot_id || ctx.lot_id || "-"}</strong></span>
        {ctx.custom_name && <span className="home-workspace__meta-item">공정: <strong>{ctx.custom_name}</strong></span>}
        {tool.approval?.status === "pending" && (
          <span className="home-workspace__badge is-pending">승인 대기 중</span>
        )}
        {onNavigate && (
          <button
            type="button"
            className="home-workspace__mini-link-btn"
            onClick={() => onNavigate("splittable")}
            title="SplitTable 전체 편집기에서 직접 수정하기"
          >
            ✏️ SplitTable 전체 편집기로 수정 →
          </button>
        )}
      </div>

      {tool.approval?.status === "pending" && onDecision && (
        <div className="home-workspace__quick-decision-banner">
          <span>⚠️ 스플릿 계획 배정이 대기 중입니다. 지금 바로 승인하시겠습니까?</span>
          <div className="home-workspace__actions-inline">
            <button type="button" className="home-workspace__btn is-approve" onClick={() => onDecision("승인하겠다 진행하겠다")}>
              승인하고 반영
            </button>
            <button type="button" className="home-workspace__btn is-cancel" onClick={() => onDecision("취소")}>
              취소
            </button>
          </div>
        </div>
      )}

      {filteredSplitView && (
        <SplitTableSnapshotView
          stView={filteredSplitView}
          product={ctx.product || ""}
          source={splitViewSource(tool)}
          showTitle={false}
          maxHeight={620}
        />
      )}

      {table && (
        <DataTable
          table={table}
          downloadName={`splittable_${ctx.product || "plan"}`}
          filterText={filterText}
        />
      )}
    </div>
  );
}

function WorkspaceLocation({ tool, filterText = "", onExplore }) {
  const ctx = tool.context || {};
  const table = tool.table;
  const rows = resultRows(table);
  const firstRow = rows[0] || {};
  const prod = ctx.product || firstRow.product || "";
  const rootLot = ctx.root_lot_id || firstRow.root_lot_id || "";

  return (
    <div className="home-workspace__location-container">
      <div className="home-workspace__card-grid">
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">제품 / Root Lot</div>
          <div className="home-workspace__card-value">{prod || "-"} · {rootLot || "-"}</div>
        </div>
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">현재 공정 (Step ID)</div>
          <div className="home-workspace__card-value is-highlight">{firstRow.step_id || firstRow.current_step || firstRow.current_step_id || "-"}</div>
        </div>
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">공정 명칭 / 설명</div>
          <div className="home-workspace__card-value">{firstRow.step_desc || firstRow.function_step || firstRow.func_step || "-"}</div>
        </div>
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">웨이퍼 수량 / 상태</div>
          <div className="home-workspace__card-value">{firstRow.qty || firstRow.wafer_count || firstRow.wafers || firstRow.wafer_label || "-"} Wafers</div>
        </div>
      </div>

      {onExplore && prod && (
        <div className="home-workspace__quick-explore-bar">
          <span className="home-workspace__explore-label">연계 찾아가기:</span>
          {rootLot && (
            <button
              type="button"
              className="home-workspace__explore-chip"
              onClick={() => onExplore(`${prod} ${rootLot} 수율 맵 보여줘`)}
            >
              🗺️ 수율 맵 보기
            </button>
          )}
          {rootLot && (
            <button
              type="button"
              className="home-workspace__explore-chip"
              onClick={() => onExplore(`${prod} ${rootLot} 스플릿테이블 보여줘`)}
            >
              📋 스플릿 레시피 보기
            </button>
          )}
          <button
            type="button"
            className="home-workspace__explore-chip"
            onClick={() => onExplore(`${prod} TEG 위치 보여줘`)}
          >
            📍 TEG 위치 조회
          </button>
        </div>
      )}

      {table && (
        <DataTable
          table={table}
          downloadName={`location_${prod}_${rootLot || "lots"}`}
          filterText={filterText}
        />
      )}
    </div>
  );
}

function WorkspaceDashboard({ tool, filterText = "" }) {
  const table = tool.table;
  const rows = resultRows(table);
  const first = rows[0] || {};
  const isSummary = first.wip_lots !== undefined || first.stuck_lots !== undefined;
  return (
    <div className="home-workspace__dashboard-container">
      {isSummary && (
        <div className="home-workspace__card-grid">
          <div className="home-workspace__card">
            <div className="home-workspace__card-label">재공 랏 (WIP Lots)</div>
            <div className="home-workspace__card-value is-highlight">{first.wip_lots ?? "-"}</div>
          </div>
          <div className="home-workspace__card">
            <div className="home-workspace__card-label">정체 랏 (Stuck Lots)</div>
            <div className="home-workspace__card-value is-warn">{first.stuck_lots ?? "-"}</div>
          </div>
          {first.tat !== undefined && (
            <div className="home-workspace__card">
              <div className="home-workspace__card-label">평균 TAT</div>
              <div className="home-workspace__card-value">{first.tat}일</div>
            </div>
          )}
          {first.dpml !== undefined && (
            <div className="home-workspace__card">
              <div className="home-workspace__card-label">DPML</div>
              <div className="home-workspace__card-value">{first.dpml}</div>
            </div>
          )}
        </div>
      )}
      {table && (
        <DataTable
          table={table}
          downloadName="dashboard_data"
          filterText={filterText}
        />
      )}
    </div>
  );
}

function WorkspaceTracker({ tool, filterText = "" }) {
  const table = tool.table;
  return (
    <div className="home-workspace__tracker-container">
      {tool.issue ? (
        <div className="home-workspace__issue-detail">
          <div className="home-workspace__issue-header">
            <span className="home-workspace__badge is-status">{tool.issue.status || "open"}</span>
            <h3>{tool.issue.title || tool.issue.id}</h3>
          </div>
          <div className="home-workspace__meta-bar">
            <span>카테고리: {tool.issue.category || "-"}</span>
            <span>담당자: {tool.issue.username || "-"}</span>
            <span>등록일: {tool.issue.created || tool.issue.updated_at || "-"}</span>
          </div>
        </div>
      ) : null}
      {table && (
        <DataTable
          table={table}
          downloadName="tracker_issues"
          filterText={filterText}
        />
      )}
    </div>
  );
}

function WorkspaceYieldMap({ tool, filterText = "" }) {
  const ctx = tool.context || {};
  const table = tool.table;
  return (
    <div className="home-workspace__yieldmap-container">
      <div className="home-workspace__meta-bar">
        <span className="home-workspace__meta-item">제품: <strong>{ctx.product || "-"}</strong></span>
        {ctx.root_lot_id && <span className="home-workspace__meta-item">Root Lot: <strong>{ctx.root_lot_id}</strong></span>}
        {ctx.bin_name && <span className="home-workspace__meta-item">선택 BIN: <strong>{ctx.bin_name}</strong></span>}
      </div>
      {table && (
        <DataTable
          table={table}
          downloadName={`yieldmap_${ctx.product || "data"}`}
          filterText={filterText}
        />
      )}
    </div>
  );
}

function LiveFeatureWorkspace({ workspace, onClose, onMaximize, isMaximized, onDecision, loading, onNavigate, onExplore }) {
  const [filterText, setFilterText] = useState("");
  if (!workspace || !workspace.tool) return null;
  const { feature, tool, lastUpdated, isMutated } = workspace;
  const info = featureInfo(feature, tool);
  const approvalId = asText(tool.approval?.id).trim();
  const targetPage = FEATURE_PAGE_MAP[feature] || FEATURE_PAGE_MAP[tool.action];
  const pageName = FEATURE_PAGE_NAMES[feature] || FEATURE_PAGE_NAMES[tool.action] || "전체 화면";

  const isTegView = Boolean(
    feature === "teg" ||
    tool.action?.startsWith("teg") ||
    tool.teg ||
    tool.related_tegs?.length ||
    (tool.action === "location" && tool.context?.target_teg)
  );

  const notices = [
    tool.sources?.length ? `출처: ${tool.sources.map(asText).join(" · ")}` : "",
    tool.missing?.length ? `누락: ${tool.missing.map(asText).join(" · ")}` : "",
    tool.warnings?.length ? `주의: ${tool.warnings.map(asText).join(" · ")}` : "",
    tool.blocked ? `차단됨: ${asText(tool.blocked)}` : "",
  ].filter(Boolean);

  return (
    <aside className={`home-data-chat__workspace-pane${isMutated ? " is-mutated" : ""}${isMaximized ? " is-maximized" : ""}`} aria-label="라이브 기능 작업창">
      <div className="home-workspace__header">
        <div className="home-workspace__header-left">
          <span className="home-workspace__icon">{info.icon}</span>
          <div className="home-workspace__titles">
            <div className="home-workspace__title">
              {info.title}
              {isMutated ? (
                <span className="home-workspace__badge is-updated">⚡ 실시간 갱신됨</span>
              ) : (
                <span className="home-workspace__badge is-live">● 실시간 연동 ({lastUpdated})</span>
              )}
            </div>
            {info.subtitle && <div className="home-workspace__subtitle">{info.subtitle}</div>}
          </div>
        </div>

        <div className="home-workspace__header-right">
          {tool.approval?.status === "pending" && approvalId && (
            <div className="home-workspace__actions-inline">
              <button type="button" className="home-workspace__btn is-approve" disabled={loading} onClick={() => onDecision(`승인 ${tool.approval.id}`)}>
                승인하고 반영
              </button>
              <button type="button" className="home-workspace__btn is-cancel" disabled={loading} onClick={() => onDecision(`취소 ${tool.approval.id}`)}>
                취소
              </button>
            </div>
          )}
          {targetPage && onNavigate && (
            <button
              type="button"
              className="home-workspace__tool-btn"
              onClick={() => {
                if (targetPage === "dashboard" && tool?.context?.split_col) {
                  try {
                    sessionStorage.setItem("flow:dashboard:initial_split", JSON.stringify({
                      product: tool.context.product || "",
                      split_col: tool.context.split_col || "",
                    }));
                  } catch {}
                }
                onNavigate(targetPage);
              }}
              title={`${pageName} 페이지로 이동`}
            >
              ↗ {pageName}
            </button>
          )}
          {feature === "chart" && onExplore && (
            <button type="button" className="home-workspace__tool-btn" disabled={loading} onClick={() => onExplore("이 차트로 리포트 템플릿 만들어줘")}>
              📝 리포트 템플릿 초안
            </button>
          )}
          <button type="button" className="home-workspace__tool-btn" onClick={onMaximize} title={isMaximized ? "분할 보기" : "전체 창으로 확대"}>
            {isMaximized ? "⤡ 축소" : "⤢ 전체"}
          </button>
          <button type="button" className="home-workspace__tool-btn" onClick={onClose} title="작업창 접기" aria-label="작업창 접기">
            ✕
          </button>
        </div>
      </div>

      <div className="home-workspace__filter-bar">
        <span className="home-workspace__filter-icon">🔍</span>
        <input
          type="text"
          className="home-workspace__filter-input"
          placeholder="작업창 내부 실시간 필터/검색 (Step ID, 공정명, Knob, 값 등)..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
        />
        {filterText && (
          <button
            type="button"
            className="home-workspace__filter-clear"
            onClick={() => setFilterText("")}
            title="필터 지우기"
          >
            ✕
          </button>
        )}
      </div>

      <div className="home-workspace__body">
        {notices.length > 0 && (
          <div className="home-workspace__notices">
            {notices.map((notice) => <div key={notice}>{notice}</div>)}
          </div>
        )}

        {tool.report_template && (
          <details className="home-workspace__report-template" style={{ width: "100%", boxSizing: "border-box" }}>
            <summary>리포트 템플릿 초안</summary>
            <div><strong>{tool.report_template.name || "Template Report"}</strong> · {(tool.report_template.pages || []).length || 1}페이지</div>
            {(tool.report_template.pages || []).map((page, index) => (
              <div key={page.id || index}>
                {index + 1}. {page.title || `Page ${index + 1}`} · {(page.slots || []).filter((slot) => slot.kind === "chart" || !slot.kind).map((slot) => slot.chart_name || slot.chart_label || slot.chart_id || "차트").join(", ") || "차트 없음"}
              </div>
            ))}
            {tool.template_code && <pre style={{ maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{tool.template_code}</pre>}
          </details>
        )}

        {tool.chart_result && (
          <div className="home-workspace__chart">
            <FlowPlotlyChart chart={tool.chart_result} cfg={tool.chart_result} dark={false} />
          </div>
        )}

        {isTegView ? (
          <WorkspaceTeg tool={tool} filterText={filterText} onExplore={onExplore} />
        ) : feature === "splittable" || tool.action?.startsWith("splittable") ? (
          <WorkspaceSplitTable tool={tool} filterText={filterText} onDecision={onDecision} onNavigate={onNavigate} />
        ) : feature === "location" || tool.action?.startsWith("lot_progress") ? (
          <WorkspaceLocation tool={tool} filterText={filterText} onExplore={onExplore} />
        ) : feature === "dashboard" || tool.action?.startsWith("dashboard") ? (
          <WorkspaceDashboard tool={tool} filterText={filterText} />
        ) : feature === "tracker" || tool.action?.startsWith("tracker") ? (
          <WorkspaceTracker tool={tool} filterText={filterText} />
        ) : feature === "yield_map" || tool.action?.startsWith("yield_map") ? (
          <WorkspaceYieldMap tool={tool} filterText={filterText} />
        ) : tool.table ? (
          <DataTable table={tool.table} filterText={filterText} />
        ) : null}
      </div>
    </aside>
  );
}

export default function HomeDataChat({ user, onNavigate, enabled = false, probeKey = 0 }) {
  const username = user?.username || "guest";
  const [prompt, setPrompt] = useState("");
  const [chatState, setChatState] = useState(() => loadChatState(username));
  const [loading, setLoading] = useState(false);
  const [conversations, setConversations] = useState([]);
  const [conversationError, setConversationError] = useState("");
  const [conversationLoading, setConversationLoading] = useState(false);

  const [activeWorkspace, setActiveWorkspace] = useState(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [workspaceMaximized, setWorkspaceMaximized] = useState(false);
  const [samplePrompts, setSamplePrompts] = useState({ pinned: [], successful: [] });
  const [turnUsage, setTurnUsage] = useState(null);
  const [modelRefreshKey, setModelRefreshKey] = useState(0);

  const scrollRef = useRef(null);
  const requestVersionRef = useRef(0);
  const conversationGenerationRef = useRef(0);
  const conversationAbortRef = useRef(null);
  const mutationTimerRef = useRef(null);
  const submitLockRef = useRef(false);

  const loadSamplePrompts = async () => {
    try {
      const data = await sf("/api/home-agent/sample-prompts");
      if (data?.ok) {
        setSamplePrompts({
          pinned: Array.isArray(data.pinned) ? data.pinned : [],
          successful: Array.isArray(data.successful) ? data.successful : [],
        });
      }
    } catch {}
  };

  useEffect(() => {
    loadSamplePrompts();
  }, []);

  useEffect(() => {
    requestVersionRef.current += 1;
    conversationGenerationRef.current += 1;
    conversationAbortRef.current?.abort();
    conversationAbortRef.current = null;
    setLoading(false);
    setConversations([]);
    setConversationError("");
    setConversationLoading(false);
    submitLockRef.current = false;
    setPrompt("");
    setChatState(loadChatState(username));
    setActiveWorkspace(null);
  }, [username]);

  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    const generation = conversationGenerationRef.current;
    const controller = new AbortController();
    conversationAbortRef.current = controller;
    setConversationLoading(true);
    setConversationError("");
    sf("/api/home-agent/conversations", { signal: controller.signal })
      .then((payload) => {
        if (!active || generation !== conversationGenerationRef.current) return;
        setConversations(Array.isArray(payload?.conversations) ? payload.conversations : []);
      })
      .catch((error) => {
        if (active && generation === conversationGenerationRef.current && error?.name !== "AbortError") setConversationError("대화 목록을 불러오지 못했습니다.");
      })
      .finally(() => {
        if (active && generation === conversationGenerationRef.current) setConversationLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
      if (conversationAbortRef.current === controller) conversationAbortRef.current = null;
    };
  }, [enabled, username]);

  const refreshConversations = async () => {
    const generation = conversationGenerationRef.current;
    try {
      const payload = await sf("/api/home-agent/conversations");
      if (generation === conversationGenerationRef.current) setConversations(Array.isArray(payload?.conversations) ? payload.conversations : []);
    } catch {
      if (generation === conversationGenerationRef.current) setConversationError("대화 목록을 불러오지 못했습니다.");
    }
  };

  const selectConversation = async (conversationId) => {
    if (!conversationId || conversationId === chatState.conversationId || conversationLoading || loading) return;
    const generation = conversationGenerationRef.current + 1;
    conversationGenerationRef.current = generation;
    conversationAbortRef.current?.abort();
    const controller = new AbortController();
    conversationAbortRef.current = controller;
    requestVersionRef.current += 1;
    setConversationLoading(true);
    setConversationError("");
    try {
      const payload = await sf(`/api/home-agent/conversations/${encodeURIComponent(conversationId)}`, { signal: controller.signal });
      if (generation !== conversationGenerationRef.current) return;
      const loadedMessages = normalizeMessages(Array.isArray(payload?.messages) ? payload.messages : []);
      setChatState({ username, conversationId: payload?.id || conversationId, messages: loadedMessages, context: payload?.context && typeof payload.context === "object" ? payload.context : {}, updatedAt: Date.now() });

      // Restore workspace from last message with tool
      const lastToolMsg = [...loadedMessages].reverse().find((m) => m.response?.tool);
      if (lastToolMsg?.response?.tool) {
        const tool = lastToolMsg.response.tool;
        const feature = isProductRequest(tool) ? null : (tool.feature || (tool.chart_result ? "chart" : (tool.table ? "table" : null)));
        if (feature) {
          setActiveWorkspace({
            feature,
            tool,
            lastUpdated: new Date().toLocaleTimeString(),
            isMutated: false,
          });
          setWorkspaceOpen(true);
        } else {
          setActiveWorkspace(null);
          setWorkspaceOpen(false);
        }
      } else {
        setActiveWorkspace(null);
        setWorkspaceOpen(false);
      }
    } catch (error) {
      if (generation === conversationGenerationRef.current && error?.name !== "AbortError") setConversationError("대화를 불러오지 못했습니다.");
    } finally {
      if (conversationAbortRef.current === controller) {
        conversationAbortRef.current = null;
        if (generation === conversationGenerationRef.current) setConversationLoading(false);
      }
    }
  };

  useEffect(() => {
    if (chatState.username === username) persistChatState(chatState);
  }, [chatState, username]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const node = scrollRef.current;
      if (node) node.scrollTop = node.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [chatState.messages, loading]);

  const historyForRequest = useMemo(() => apiHistory(chatState.messages), [chatState.messages]);

  const newConversation = () => {
    requestVersionRef.current += 1;
    conversationGenerationRef.current += 1;
    conversationAbortRef.current?.abort();
    conversationAbortRef.current = null;
    setConversationLoading(false);
    setConversationError("");
    setLoading(false);
    submitLockRef.current = false;
    setPrompt("");
    setActiveWorkspace(null);
    try { sessionStorage.removeItem(storageKeys(username).chartTransfer); } catch {}
    setChatState(emptyChatState(username));
  };

  const focusWorkspace = (tool) => {
    if (!tool) return;
    const feature = isProductRequest(tool) ? null : (tool.feature || (tool.chart_result ? "chart" : (tool.table ? "table" : null)));
    if (!feature) return;
    setActiveWorkspace({
      feature,
      tool,
      lastUpdated: new Date().toLocaleTimeString(),
      isMutated: false,
    });
    setWorkspaceOpen(true);
  };

  const submit = async (event, decision = "") => {
    event?.preventDefault();
    const value = (decision || prompt).trim();
    if (!enabled || !value || loading || conversationLoading || submitLockRef.current) return;
    submitLockRef.current = true;

    const requestVersion = requestVersionRef.current + 1;
    requestVersionRef.current = requestVersion;
    const userMessage = { id: `${Date.now()}-user`, role: "user", content: value };
    const requestContext = chatState.context;
    const requestHistory = historyForRequest;
    setPrompt("");
    setLoading(true);
    setChatState((current) => ({
      ...current,
      messages: [...current.messages, userMessage],
      updatedAt: Date.now(),
    }));

    try {
      const response = await sf("/api/home-agent/orchestrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: chatState.conversationId, prompt: value, top_k: 1, context: requestContext, history: requestHistory }),
      });
      if (requestVersionRef.current !== requestVersion) return;
      if (response?.usage && typeof response.usage === "object") setTurnUsage(response.usage);
      setModelRefreshKey((value) => value + 1);
      const tool = response?.tool && typeof response.tool === "object" ? response.tool : null;
      const feature = isProductRequest(tool) ? null : (tool?.feature || (tool?.chart_result ? "chart" : (tool?.table ? "table" : null)));

      if (feature) {
        setActiveWorkspace((prev) => {
          const isSameFeature = prev?.feature === feature;
          if (mutationTimerRef.current) clearTimeout(mutationTimerRef.current);
          if (isSameFeature) {
            mutationTimerRef.current = setTimeout(() => {
              setActiveWorkspace((curr) => curr ? { ...curr, isMutated: false } : null);
            }, 1400);
          }
          return {
            feature,
            tool,
            lastUpdated: new Date().toLocaleTimeString(),
            isMutated: isSameFeature,
          };
        });
        setWorkspaceOpen(true);
      } else if (decision) {
        setActiveWorkspace(null);
        setWorkspaceOpen(false);
      }

      setChatState((current) => ({
        ...current,
        conversationId: response?.conversation_id || current.conversationId,
        context: responseContext(current.context, response),
        messages: [...current.messages, {
          id: response?.message_id || `${Date.now()}-assistant`,
          role: "assistant",
          content: answerText(response),
          response: withoutContext(response),
        }],
        updatedAt: Date.now(),
      }));
      refreshConversations();

      if (response?.success_prompt) loadSamplePrompts();
    } catch (error) {
      if (requestVersionRef.current !== requestVersion) return;
      setChatState((current) => ({
        ...current,
        messages: [...current.messages, {
          id: `${Date.now()}-error`,
          role: "assistant",
          content: error.message,
          error: true,
        }],
        updatedAt: Date.now(),
      }));
      refreshConversations();
    } finally {
      if (requestVersionRef.current === requestVersion) setLoading(false);
      if (requestVersionRef.current === requestVersion) submitLockRef.current = false;
    }
  };

  if (!enabled) return null;

  const hasActiveWorkspace = workspaceOpen && activeWorkspace && activeWorkspace.tool;

  return (
    <section className={`home-data-chat${hasActiveWorkspace ? " has-workspace" : ""}`} aria-label="데이터 채팅">
      <ModelStatus key={username} refreshKey={modelRefreshKey} probeKey={probeKey} turnUsage={turnUsage} />
      <div className="home-data-chat__conversation-bar">
        <select value={chatState.conversationId} onChange={(event) => selectConversation(event.target.value)} disabled={conversationLoading || loading} aria-label="저장된 대화 선택">
          <option value={chatState.conversationId}>{conversations.find((item) => item.id === chatState.conversationId)?.title || "새 대화"}</option>
          {conversations.filter((conversation) => conversation.id !== chatState.conversationId).map((conversation) => (
            <option key={conversation.id} value={conversation.id}>{conversation.title || "제목 없는 대화"}</option>
          ))}
        </select>
        <button type="button" onClick={newConversation} disabled={loading}>새 대화</button>
        {activeWorkspace && !workspaceOpen && (
          <button type="button" className="home-data-chat__reopen-btn" onClick={() => setWorkspaceOpen(true)}>
            🖥️ 라이브 작업창 열기 ({featureInfo(activeWorkspace.feature, activeWorkspace.tool).title})
          </button>
        )}
        {conversationLoading && <span className="home-data-chat__conversation-status">불러오는 중…</span>}
        {conversationError && <span className="home-data-chat__conversation-error" role="status">{conversationError}</span>}
      </div>

      <div className="home-data-chat__body">
        <div className={`home-data-chat__chat-pane${workspaceMaximized ? " is-hidden" : ""}`}>
          <div className="home-data-chat__conversation" ref={scrollRef} aria-live="polite">
            {chatState.messages.length === 0 && (
              <div className="home-data-chat__empty-wrap">
                {samplePrompts.successful?.length > 0 && (
                  <div className="home-data-chat__success-prompts-section">
                    <div className="home-data-chat__prompts-header">
                      <span className="home-data-chat__prompts-title">⚡ 최근 실제 성공한 질문</span>
                    </div>
                    <div className="home-data-chat__sample-chips">
                      {samplePrompts.successful
                        .slice(0, 8)
                        .map((item) => (
                          <button
                            key={item.id || item.prompt}
                            type="button"
                            className="home-data-chat__chip is-success"
                            onClick={() => setPrompt(item.prompt)}
                            title={`실제 ${item.count || 1}회 성공한 질의 (클릭하여 질문 입력)`}
                          >
                            ⚡ {item.prompt}
                            {item.count > 1 && <span className="home-data-chat__chip-count">({item.count}회)</span>}
                          </button>
                        ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            {(() => {
              const latestMessageId = chatState.messages[chatState.messages.length - 1]?.id;
              return chatState.messages.map((message) => {
              const msgTool = message.response?.tool;
              const tegCandidateGroups = Array.isArray(msgTool?.teg_candidates)
                ? msgTool.teg_candidates.filter((group) => group && Array.isArray(group.candidates) && group.candidates.length)
                : [];
              const splitCandidateGroups = Array.isArray(msgTool?.split_candidates)
                ? msgTool.split_candidates.filter((group) => group && Array.isArray(group.candidates) && group.candidates.length)
                : [];
              const msgFeature = msgTool?.feature || (msgTool?.chart_result ? "chart" : (msgTool?.table ? "table" : null));
              const info = msgFeature && !isProductRequest(msgTool) ? featureInfo(msgFeature, msgTool) : null;
              const isCurrent = activeWorkspace && activeWorkspace.tool === msgTool;
              const canRespond = message.role === "assistant" && message.id === latestMessageId && !loading;
              const isBatchMessage = Array.isArray(message.response?.questions) && message.response.questions.length > 0;

              return (
                <article key={message.id} className={`home-data-chat__message is-${message.role}${message.error ? " is-error" : ""}`}>
                  <div className="home-data-chat__bubble">
                     {isBatchMessage
                       ? <BatchContent response={message.response} onExplore={(query) => submit(null, query)} onOpenWorkspace={focusWorkspace} canRespond={canRespond} />
                       : <BubbleContent message={message} onExplore={(query) => submit(null, query)} />}
                  </div>

                  {message.response?.usage && <div className="home-data-chat__usage">이번 요청 LLM {message.response.usage.llm_calls_used}회 차감 / 최대 {message.response.usage.llm_call_limit}회 · 당시 분당 잔여 {message.response.usage.minute_calls_remaining}회</div>}
                  {message.role === "assistant" && msgTool && (
                    <div className="home-data-chat__message-footer">
                      {info && (
                        <div
                          className={`home-data-chat__workspace-chip${isCurrent ? " is-active" : ""}`}
                          onClick={() => focusWorkspace(msgTool)}
                          role="button"
                          tabIndex={0}
                          aria-label={`${info.title} 작업창에서 보기`}
                        >
                          <span className="home-data-chat__workspace-chip-icon">{info.icon}</span>
                          <div className="home-data-chat__workspace-chip-content">
                            <span className="home-data-chat__workspace-chip-title">{info.title}</span>
                            {info.subtitle && <span className="home-data-chat__workspace-chip-sub">{info.subtitle}</span>}
                          </div>
                          <span className="home-data-chat__workspace-chip-btn">
                            {isCurrent && workspaceOpen ? "작업창 표시 중" : "작업창 열기 →"}
                          </span>
                        </div>
                      )}

                      {splitCandidateGroups.map((group, groupIndex) => (
                        <div className="home-data-chat__candidate-group" key={`split-cand-${groupIndex}`}>
                          <span>{group.title || "Split 조건을 선택하세요"}</span>
                          <div className="home-data-chat__actions" aria-label="Split 후보 선택">
                            {group.candidates.map((cand, candIdx) => {
                              const label = cand.label || cand.value || cand;
                              const promptText = cand.prompt || cand.value || cand;
                              return (
                                <button
                                  type="button"
                                  key={`cand-${candIdx}`}
                                  disabled={loading}
                                  onClick={() => submit(null, promptText)}
                                >
                                  {label}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      ))}

                      {!isBatchMessage && isProductRequest(msgTool) && (
                        <ProductClarification tool={msgTool} onSubmit={(value) => submit(null, value)} disabled={!canRespond} />
                      )}

                      {tegCandidateGroups.map((group, groupIndex) => (
                        <div className="home-data-chat__candidate-group" key={`${asText(group.requested)}-${groupIndex}`}>
                          <span>{group.requested ? `“${asText(group.requested)}” 후보를 선택하세요` : "TEG 후보를 선택하세요"}</span>
                          <div className="home-data-chat__actions" aria-label="TEG 후보 선택">
                            {group.candidates.map((candidate) => {
                              const name = asText(candidate).trim();
                              return name ? <button type="button" key={name} disabled={loading} onClick={() => submit(null, name)}>{name}</button> : null;
                            })}
                          </div>
                        </div>
                      ))}

                      {msgTool.approval?.status === "pending" && asText(msgTool.approval?.id).trim() && (
                        <div className="home-data-chat__actions" aria-label="스플릿 변경 승인">
                          <button type="button" disabled={loading} onClick={() => submit(null, `승인 ${msgTool.approval.id}`)}>
                            승인하고 반영
                          </button>
                          <button type="button" disabled={loading} onClick={() => submit(null, `취소 ${msgTool.approval.id}`)}>
                            취소
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </article>
              );
              });
            })()}
            {loading && (
              <article className="home-data-chat__message is-assistant is-pending" aria-label="응답 작성 중">
                <div className="home-data-chat__typing"><span /><span /><span /></div>
              </article>
            )}
          </div>
          <form onSubmit={submit} className="home-data-chat__composer">
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                const composing = event.nativeEvent?.isComposing || event.keyCode === 229;
                if (event.key === "Enter" && !event.shiftKey && !composing) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder="실제 DB의 제품명과 Lot·공정 조건으로 질문하세요"
              rows={1}
              maxLength={API_HISTORY_CHARS}
              disabled={loading || conversationLoading}
              aria-label="데이터 질문"
            />
            <button type="submit" disabled={loading || conversationLoading || !prompt.trim()} aria-label="메시지 보내기">↑</button>
          </form>
          <div className="home-data-chat__composer-help">여러 질문은 줄바꿈 또는 물음표(?)로 나누세요 · 한 번에 최대 4개</div>
        </div>

        {hasActiveWorkspace && (
          <LiveFeatureWorkspace
            workspace={activeWorkspace}
            onClose={() => { setWorkspaceOpen(false); setWorkspaceMaximized(false); }}
            onMaximize={() => setWorkspaceMaximized((v) => !v)}
            isMaximized={workspaceMaximized}
            onDecision={(value) => submit(null, value)}
            loading={loading}
            onNavigate={onNavigate}
            onExplore={(query) => submit(null, query)}
          />
        )}
      </div>
    </section>
  );
}
