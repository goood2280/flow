import { useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import { sf } from "../../lib/api";
import "./HomeDataChat.css";

const PAGE_SIZE = 50;
const API_HISTORY_MESSAGES = 20;
const API_HISTORY_CHARS = 4000;

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
  // getRandomValues also works on the internal HTTP deployment.
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

  // The unstamped ChartBuilder transfer may seed a fresh conversation. Once a
  // conversation exists, its newer server context always wins over this value.
  const chartTransfer = readJson(keys.chartTransfer, {});
  return {
    ...emptyChatState(username),
    context: chartTransfer && typeof chartTransfer === "object" ? chartTransfer : {},
  };
}

function persistChatState(state) {
  try {
    sessionStorage.setItem(storageKeys(state.username).conversation, JSON.stringify(state));
  } catch {
    // Keep the active conversation usable if browser storage is unavailable.
  }
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

  // Compatibility for a server that has not adopted the full context contract.
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

function MessageArtifacts({ response, pendingId, loading, onDecision }) {
  const tool = response?.tool && typeof response.tool === "object" ? response.tool : {};
  const table = tableFromTool(tool);
  const notices = [
    tool.sources?.length ? `출처: ${tool.sources.map(asText).join(" · ")}` : "",
    tool.missing?.length ? `누락: ${tool.missing.map(asText).join(" · ")}` : "",
    tool.warnings?.length ? `주의: ${tool.warnings.map(asText).join(" · ")}` : "",
    tool.blocked ? `차단됨: ${asText(tool.blocked)}` : "",
  ].filter(Boolean);

  return (
    <>
      {notices.length > 0 && <div className="home-data-chat__notices">{notices.map((notice) => <div key={notice}>{notice}</div>)}</div>}
      {table && <DataTable table={table} />}
      {tool.approval?.status === "pending" && tool.approval.id === pendingId && (
        <div className="home-data-chat__actions" aria-label="스플릿 변경 승인">
          <button type="button" disabled={loading} onClick={() => onDecision("승인하겠다 진행하겠다")}>승인하고 반영</button>
          <button type="button" disabled={loading} onClick={() => onDecision("취소")}>취소</button>
        </div>
      )}
      {tool.chart_result && (
        <div className="home-data-chat__chart">
          <FlowPlotlyChart chart={tool.chart_result} cfg={tool.chart_result} dark={false} />
        </div>
      )}
    </>
  );
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
  return <div className="home-data-chat__model-bar">
    <span>현재 모델 · {model.model || labels[model.status] || "확인 중"}</span>
    {model.model && <span>{labels[model.status] || "확인 불가"}</span>}
    <button type="button" onClick={() => refresh(true)} disabled={checking}>{checking ? "검사 중…" : "연결 검사"}</button>
  </div>;
}

export default function HomeDataChat({ user }) {
  const username = user?.username || "guest";
  const admin = user?.role === "admin";
  const [prompt, setPrompt] = useState("");
  const [chatState, setChatState] = useState(() => loadChatState(username));
  const [loading, setLoading] = useState(false);
  const [conversations, setConversations] = useState([]);
  const [conversationError, setConversationError] = useState("");
  const [conversationLoading, setConversationLoading] = useState(false);
  const scrollRef = useRef(null);
  const requestVersionRef = useRef(0);
  const conversationGenerationRef = useRef(0);
  const conversationAbortRef = useRef(null);

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
      setChatState({ username, conversationId: payload?.id || conversationId, messages: normalizeMessages(Array.isArray(payload?.messages) ? payload.messages : []), context: payload?.context && typeof payload.context === "object" ? payload.context : {}, updatedAt: Date.now() });
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
    try { sessionStorage.removeItem(storageKeys(username).chartTransfer); } catch {}
    setChatState(emptyChatState(username));
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

  return (
    <section className="home-data-chat" aria-label="데이터 채팅">
      <ModelStatus key={username} />
      <div className="home-data-chat__conversation-bar">
        <select value={chatState.conversationId} onChange={(event) => selectConversation(event.target.value)} disabled={conversationLoading || loading} aria-label="저장된 대화 선택">
          <option value={chatState.conversationId}>{conversations.find((item) => item.id === chatState.conversationId)?.title || "새 대화"}</option>
          {conversations.filter((conversation) => conversation.id !== chatState.conversationId).map((conversation) => (
            <option key={conversation.id} value={conversation.id}>{conversation.title || "제목 없는 대화"}</option>
          ))}
        </select>
        <button type="button" onClick={newConversation} disabled={loading}>새 대화</button>
        {conversationLoading && <span className="home-data-chat__conversation-status">불러오는 중…</span>}
        {conversationError && <span className="home-data-chat__conversation-error" role="status">{conversationError}</span>}
      </div>

      <div className="home-data-chat__conversation" ref={scrollRef} aria-live="polite">
        {chatState.messages.length === 0 && <div className="home-data-chat__empty">무엇을 도와드릴까요?</div>}
        {chatState.messages.map((message) => (
          <article key={message.id} className={`home-data-chat__message is-${message.role}${message.error ? " is-error" : ""}`}>
            <div className="home-data-chat__bubble">{message.content}</div>
            {message.role === "assistant" && message.response && <MessageArtifacts response={message.response}
              pendingId={chatState.context.last_action === "splittable.plan" ? chatState.context.pending_split_id : null}
              loading={loading} onDecision={(value) => submit(null, value)} />}
          </article>
        ))}
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
          placeholder="메시지를 입력하세요"
          rows={1}
          maxLength={API_HISTORY_CHARS}
          disabled={loading || conversationLoading}
          aria-label="데이터 질문"
        />
        <button type="submit" disabled={loading || conversationLoading || !prompt.trim()} aria-label="메시지 보내기">↑</button>
      </form>
    </section>
  );
}
