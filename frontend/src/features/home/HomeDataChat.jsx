import { useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import { sf } from "../../lib/api";
import "./HomeDataChat.css";

const PAGE_SIZE = 50;
const MAX_STORED_MESSAGES = 24;
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

function emptyChatState(username) {
  return { username, messages: [], context: {}, updatedAt: Date.now() };
}

function loadChatState(username) {
  const keys = storageKeys(username);
  const stored = readJson(keys.conversation, null);
  if (stored?.username === username && Array.isArray(stored.messages)) {
    return {
      username,
      messages: stored.messages.slice(-MAX_STORED_MESSAGES),
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

export default function HomeDataChat({ user }) {
  const username = user?.username || "guest";
  const admin = user?.role === "admin";
  const [prompt, setPrompt] = useState("");
  const [chatState, setChatState] = useState(() => loadChatState(username));
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef(null);
  const requestVersionRef = useRef(0);

  useEffect(() => {
    requestVersionRef.current += 1;
    setLoading(false);
    setPrompt("");
    setChatState(loadChatState(username));
  }, [username]);

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
    setLoading(false);
    setPrompt("");
    try { sessionStorage.removeItem(storageKeys(username).chartTransfer); } catch {}
    setChatState(emptyChatState(username));
  };

  const submit = async (event, decision = "") => {
    event?.preventDefault();
    const value = (decision || prompt).trim();
    if (!admin || !value || loading) return;

    const requestVersion = requestVersionRef.current + 1;
    requestVersionRef.current = requestVersion;
    const userMessage = { id: `${Date.now()}-user`, role: "user", content: value };
    const requestContext = chatState.context;
    const requestHistory = historyForRequest;
    setPrompt("");
    setLoading(true);
    setChatState((current) => ({
      ...current,
      messages: [...current.messages, userMessage].slice(-MAX_STORED_MESSAGES),
      updatedAt: Date.now(),
    }));

    try {
      const response = await sf("/api/home-agent/orchestrate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: value, top_k: 1, context: requestContext, history: requestHistory }),
      });
      if (requestVersionRef.current !== requestVersion) return;

      setChatState((current) => ({
        ...current,
        context: responseContext(current.context, response),
        messages: [...current.messages, {
          id: `${Date.now()}-assistant`,
          role: "assistant",
          content: answerText(response),
          response: withoutContext(response),
        }].slice(-MAX_STORED_MESSAGES),
        updatedAt: Date.now(),
      }));
    } catch (error) {
      if (requestVersionRef.current !== requestVersion) return;
      setChatState((current) => ({
        ...current,
        messages: [...current.messages, {
          id: `${Date.now()}-error`,
          role: "assistant",
          content: error.message,
          error: true,
        }].slice(-MAX_STORED_MESSAGES),
        updatedAt: Date.now(),
      }));
    } finally {
      if (requestVersionRef.current === requestVersion) setLoading(false);
    }
  };

  if (!admin) return null;

  return (
    <section className="home-data-chat" aria-label="데이터 채팅">
      <div className="home-data-chat__actions">
        <button type="button" onClick={newConversation}>새 대화</button>
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
          disabled={loading}
          aria-label="데이터 질문"
        />
        <button type="submit" disabled={loading || !prompt.trim()} aria-label="메시지 보내기">↑</button>
      </form>
    </section>
  );
}
