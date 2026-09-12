import { useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import { sf } from "../../lib/api";
import "./HomeDataChat.css";

const PAGE_SIZE = 50;
const API_HISTORY_MESSAGES = 20;
const API_HISTORY_CHARS = 4000;

const FEATURE_PAGE_MAP = {
  splittable: "splittable",
  "splittable.plan": "splittable",
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

function DataTable({ table }) {
  const rows = resultRows(table);
  const columns = resultColumns(table, rows);
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pages - 1);
  const pageRows = rows.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);
  const total = Number(table?.total ?? table?.row_count ?? rows.length);

  useEffect(() => setPage(0), [table]);
  if (!rows.length || !columns.length) return null;

  return (
    <div className="home-data-chat__table-wrap">
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
          <span>{safePage + 1} / {pages} · {total}행</span>
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

function responseContext(previous, response) {
  if (response?.context && typeof response.context === "object") return response.context;
  const tool = response?.tool && typeof response.tool === "object" ? response.tool : {};
  const next = { ...(previous || {}) };
  const table = tableFromTool(tool);
  if (table) next.table = table;
  if (tool.chart_result && typeof tool.chart_result === "object") next.chart_result = tool.chart_result;
  if (tool.definition_code) next.definition_code = tool.definition_code;
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

function formatBubbleContent(content) {
  if (typeof content !== "string") return content;
  if (content.startsWith("[도메인 해석 가이드]") && content.includes("────────────────────────────────────────")) {
    const [guidePart, ...rest] = content.split("────────────────────────────────────────");
    const bodyPart = rest.join("────────────────────────────────────────").trim();
    const guideLines = guidePart.replace("[도메인 해석 가이드]", "").trim();
    return (
      <div className="home-data-chat__guided-message">
        <div className="home-data-chat__guide-card">
          <div className="home-data-chat__guide-header">
            <span className="home-data-chat__guide-badge">도메인 해석 가이드</span>
          </div>
          <div className="home-data-chat__guide-body">{guideLines}</div>
        </div>
        {bodyPart && <div className="home-data-chat__guided-body">{bodyPart}</div>}
      </div>
    );
  }
  return content;
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
  };
  const titles = {
    splittable: "SplitTable 계획 배정 & 레시피",
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

function ModelStatus() {
  const [model, setModel] = useState({});
  const [checking, setChecking] = useState(false);
  const active = useRef(true);
  const busy = useRef(false);
  const refresh = async (probe = false) => {
    if (busy.current) return;
    busy.current = true;
    if (probe) setChecking(true);
    try {
      const result = await sf(probe ? "/api/home-agent/probe" : "/api/home-agent/status", { method: probe ? "POST" : "GET" });
      if (active.current) setModel(result.model || {});
    } catch {
      if (active.current) setModel({ status: "unknown" });
    } finally {
      busy.current = false;
      if (active.current) setChecking(false);
    }
  };
  useEffect(() => {
    active.current = true;
    refresh();
    const timer = setInterval(() => refresh(), 30000);
    return () => { active.current = false; clearInterval(timer); };
  }, []);
  const labels = { connected: "연결됨", disconnected: "연결 끊김", disabled: "사용 안 함", unconfigured: "미설정" };
  return (
    <div className="home-data-chat__model-bar">
      <span>현재 모델 · {model.model || labels[model.status] || "확인 중"}</span>
      {model.model && <span>{labels[model.status] || "확인 불가"}</span>}
      <button type="button" onClick={() => refresh(true)} disabled={checking}>{checking ? "검사 중…" : "연결 검사"}</button>
    </div>
  );
}

function WorkspaceSplitTable({ tool }) {
  const splitView = tool.split_view || tool.table;
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
      </div>
      {splitView && <DataTable table={splitView} />}
    </div>
  );
}

function WorkspaceLocation({ tool }) {
  const ctx = tool.context || {};
  const table = tool.table;
  const rows = resultRows(table);
  const firstRow = rows[0] || {};
  return (
    <div className="home-workspace__location-container">
      <div className="home-workspace__card-grid">
        <div className="home-workspace__card">
          <div className="home-workspace__card-label">제품 / Root Lot</div>
          <div className="home-workspace__card-value">{ctx.product || firstRow.product || "-"} · {ctx.root_lot_id || firstRow.root_lot_id || "-"}</div>
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
      {table && <DataTable table={table} />}
    </div>
  );
}

function WorkspaceDashboard({ tool }) {
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
      {table && <DataTable table={table} />}
    </div>
  );
}

function WorkspaceTracker({ tool }) {
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
      {table && <DataTable table={table} />}
    </div>
  );
}

function WorkspaceYieldMap({ tool }) {
  const ctx = tool.context || {};
  const table = tool.table;
  return (
    <div className="home-workspace__yieldmap-container">
      <div className="home-workspace__meta-bar">
        <span className="home-workspace__meta-item">제품: <strong>{ctx.product || "-"}</strong></span>
        {ctx.root_lot_id && <span className="home-workspace__meta-item">Root Lot: <strong>{ctx.root_lot_id}</strong></span>}
        {ctx.bin_name && <span className="home-workspace__meta-item">선택 BIN: <strong>{ctx.bin_name}</strong></span>}
      </div>
      {table && <DataTable table={table} />}
    </div>
  );
}

function LiveFeatureWorkspace({ workspace, onClose, onMaximize, isMaximized, onDecision, loading, onNavigate }) {
  if (!workspace || !workspace.tool) return null;
  const { feature, tool, lastUpdated, isMutated } = workspace;
  const info = featureInfo(feature, tool);
  const targetPage = FEATURE_PAGE_MAP[feature] || FEATURE_PAGE_MAP[tool.action];
  const pageName = FEATURE_PAGE_NAMES[feature] || FEATURE_PAGE_NAMES[tool.action] || "전체 화면";

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
          {tool.approval?.status === "pending" && (
            <div className="home-workspace__actions-inline">
              <button type="button" className="home-workspace__btn is-approve" disabled={loading} onClick={() => onDecision("승인하겠다 진행하겠다")}>
                승인하고 반영
              </button>
              <button type="button" className="home-workspace__btn is-cancel" disabled={loading} onClick={() => onDecision("취소")}>
                취소
              </button>
            </div>
          )}
          {targetPage && onNavigate && (
            <button type="button" className="home-workspace__tool-btn" onClick={() => onNavigate(targetPage)} title={`${pageName} 페이지로 이동`}>
              ↗ {pageName}
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

      <div className="home-workspace__body">
        {notices.length > 0 && (
          <div className="home-workspace__notices">
            {notices.map((notice) => <div key={notice}>{notice}</div>)}
          </div>
        )}

        {tool.chart_result && (
          <div className="home-workspace__chart">
            <FlowPlotlyChart chart={tool.chart_result} cfg={tool.chart_result} dark={false} />
          </div>
        )}

        {feature === "splittable" || tool.action?.startsWith("splittable") ? (
          <WorkspaceSplitTable tool={tool} />
        ) : feature === "location" || tool.action?.startsWith("lot_progress") ? (
          <WorkspaceLocation tool={tool} />
        ) : feature === "dashboard" || tool.action?.startsWith("dashboard") ? (
          <WorkspaceDashboard tool={tool} />
        ) : feature === "tracker" || tool.action?.startsWith("tracker") ? (
          <WorkspaceTracker tool={tool} />
        ) : feature === "yield_map" || tool.action?.startsWith("yield_map") ? (
          <WorkspaceYieldMap tool={tool} />
        ) : tool.table ? (
          <DataTable table={tool.table} />
        ) : null}
      </div>
    </aside>
  );
}

export default function HomeDataChat({ user, onNavigate }) {
  const username = user?.username || "guest";
  const admin = user?.role === "admin";
  const [prompt, setPrompt] = useState("");
  const [chatState, setChatState] = useState(() => loadChatState(username));
  const [loading, setLoading] = useState(false);
  const [conversations, setConversations] = useState([]);
  const [conversationError, setConversationError] = useState("");
  const [conversationLoading, setConversationLoading] = useState(false);

  const [activeWorkspace, setActiveWorkspace] = useState(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [workspaceMaximized, setWorkspaceMaximized] = useState(false);

  const scrollRef = useRef(null);
  const requestVersionRef = useRef(0);
  const conversationGenerationRef = useRef(0);
  const conversationAbortRef = useRef(null);
  const mutationTimerRef = useRef(null);

  useEffect(() => {
    requestVersionRef.current += 1;
    conversationGenerationRef.current += 1;
    conversationAbortRef.current?.abort();
    conversationAbortRef.current = null;
    setLoading(false);
    setConversations([]);
    setConversationError("");
    setConversationLoading(false);
    setPrompt("");
    setChatState(loadChatState(username));
    setActiveWorkspace(null);
  }, [username]);

  useEffect(() => {
    if (!admin) return undefined;
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
  }, [admin, username]);

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
        const feature = tool.feature || (tool.chart_result ? "chart" : (tool.table ? "table" : null));
        if (feature) {
          setActiveWorkspace({
            feature,
            tool,
            lastUpdated: new Date().toLocaleTimeString(),
            isMutated: false,
          });
          setWorkspaceOpen(true);
        }
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
    setPrompt("");
    setActiveWorkspace(null);
    try { sessionStorage.removeItem(storageKeys(username).chartTransfer); } catch {}
    setChatState(emptyChatState(username));
  };

  const focusWorkspace = (tool) => {
    if (!tool) return;
    const feature = tool.feature || (tool.chart_result ? "chart" : (tool.table ? "table" : null));
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
    if (!admin || !value || loading || conversationLoading) return;

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

      const tool = response?.tool && typeof response.tool === "object" ? response.tool : null;
      const feature = tool?.feature || (tool?.chart_result ? "chart" : (tool?.table ? "table" : null));

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
      }

      setChatState((current) => ({
        ...current,
        conversationId: response?.conversation_id || current.conversationId,
        context: responseContext(current.context, response),
        messages: [...current.messages, {
          id: `${Date.now()}-assistant`,
          role: "assistant",
          content: answerText(response),
          response: withoutContext(response),
        }],
        updatedAt: Date.now(),
      }));
      refreshConversations();
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
    }
  };

  if (!admin) return null;

  const hasActiveWorkspace = workspaceOpen && activeWorkspace && activeWorkspace.tool;

  return (
    <section className={`home-data-chat${hasActiveWorkspace ? " has-workspace" : ""}`} aria-label="데이터 채팅">
      <ModelStatus key={username} />
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
                <div className="home-data-chat__empty-title">Flow 데이터 어시스턴트</div>
                <div className="home-data-chat__empty-desc">
                  반도체 실무 발화(Lot .1 표기, Left 5 Root Lot, EVT 제품 약칭, 구어체 스플릿 배정, 수율 맵, 트래커 이슈 등)를 자동 번역하여 우측 라이브 작업창에 실시간 반영합니다.
                </div>
                <div className="home-data-chat__sample-chips">
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("PRODA A1001 지금 어디에 있어?")}>
                    📍 PRODA A1001 위치 조회
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("prodA A1005.1 5.0 PC 스플릿 wafer 1~6 ABC 넣고 나머지는 ABB로 깔아줘")}>
                    ✏️ 5.0 PC 스플릿 계획 배정
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("PRODA A1001 수율 맵 보여줘")}>
                    🗺️ PRODA A1001 수율 맵
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("ET 트래커 이슈 목록 보여줘")}>
                    🎯 ET 트래커 이슈 목록
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("prodA A1005.1 스플릿테이블 보여줘")}>
                    📋 prodA A1005.1 KNOB 조회
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("prodA A1005.1 PC 커스텀 세트로 보여줘")}>
                    ⚙️ prodA A1005.1 PC 커스텀 세트
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("내 관심 랏 보여줘")}>
                    ⭐ 내 관심 랏 목록
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("최근 공정 인폼 보여줘")}>
                    📢 최근 공정 인폼
                  </button>
                  <button type="button" className="home-data-chat__chip" onClick={() => setPrompt("대시보드 요약 보여줘")}>
                    📊 대시보드 지표 요약
                  </button>
                </div>
              </div>
            )}
            {chatState.messages.map((message) => {
              const msgTool = message.response?.tool;
              const msgFeature = msgTool?.feature || (msgTool?.chart_result ? "chart" : (msgTool?.table ? "table" : null));
              const info = msgFeature ? featureInfo(msgFeature, msgTool) : null;
              const isCurrent = activeWorkspace && activeWorkspace.tool === msgTool;

              return (
                <article key={message.id} className={`home-data-chat__message is-${message.role}${message.error ? " is-error" : ""}`}>
                  <div className="home-data-chat__bubble">{formatBubbleContent(message.content)}</div>

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

                      {msgTool.approval?.status === "pending" && (
                        <div className="home-data-chat__actions" aria-label="스플릿 변경 승인">
                          <button type="button" disabled={loading} onClick={() => submit(null, "승인하겠다 진행하겠다")}>
                            승인하고 반영
                          </button>
                          <button type="button" disabled={loading} onClick={() => submit(null, "취소")}>
                            취소
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
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
              placeholder="반도체 실무 발화로 질문하세요 (예: PRODA A1001 수율 맵, prodA A1005.1 스플릿 레시피)"
              rows={1}
              maxLength={API_HISTORY_CHARS}
              disabled={loading || conversationLoading}
              aria-label="데이터 질문"
            />
            <button type="submit" disabled={loading || conversationLoading || !prompt.trim()} aria-label="메시지 보내기">↑</button>
          </form>
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
          />
        )}
      </div>
    </section>
  );
}
