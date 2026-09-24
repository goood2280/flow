import { useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart, WipStackedBar } from "../../components/PlotlyChart";
import TegValueWaferMap from "../../components/TegValueWaferMap";
import SplitTableSnapshotView from "../../components/SplitTableSnapshotView";
import { sf } from "../../lib/api";
import "./HomeDataChat.css";
import TegChatMaps from "./TegChatMaps";
import InterpretationPanel from "./InterpretationPanel";
import HomeDownloadJob from "./HomeDownloadJob";
import { reportChartChoices, toggleReportChart } from "./reportCharts";

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

function isProductRequest(tool) {
  return ["product", "custom_set", "split_column", "split_value", "eta_reference", "inline_measure", "inline_time", "inline_lot_scope"].includes(tool?.clarification?.kind)
    || (Array.isArray(tool?.missing) && tool.missing.some((item) => asText(item).toLowerCase() === "product"));
}

function isHumanInLoopTool(tool) {
  if (!tool || typeof tool !== "object") return false;
  // A report proposal already has a reviewable layout, even before approval.
  if (tool.feature === "report.template" && tool.report_template) return false;
  const hasCandidates = (groups) => Array.isArray(groups)
    && groups.some((group) => Array.isArray(group?.candidates) && group.candidates.length > 0);
  return isProductRequest(tool)
    || Boolean(tool.needs_input)
    || Boolean(tool.clarification?.kind)
    || hasCandidates(tool.teg_candidates)
    || hasCandidates(tool.split_candidates)
    || tool.approval?.status === "pending"
    || (Array.isArray(tool.missing) && tool.missing.length > 0);
}

function BubbleContent({ message }) {
  // 왼쪽 대화 기록은 답변 텍스트만 남긴다. 해석·원천·쿼리·선택지는
  // InterpretationPanel(왼쪽 상단)이, 데이터는 오른쪽 결과창이 담당한다.
  const content = message.content || "";
  let displayBody = content;
  if (typeof content === "string") {
    if (content.includes("────────────────────────────────────────")) {
      const parts = content.split("────────────────────────────────────────");
      displayBody = parts.slice(1).join("────────────────────────────────────────").trim();
    } else if (content.startsWith("[도메인 해석 및 실행 경로 (Trace)]") || content.startsWith("[도메인 해석 가이드]")) {
      displayBody = "";
    }
  }
  if (!displayBody) return null;
  return (
    <div className="home-data-chat__bubble-inner">
      <div className="home-data-chat__guided-body">{displayBody}</div>
    </div>
  );
}

function BatchContent({ response }) {
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
            {(child.reply || child.answer || child.tool) && <BubbleContent message={{ content: answerText(child), response: child }} />}
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
    inline: "🧪",
    "inline.values": "🧪",
    "inline.radius_plot": "🧪",
    eta: "⏰",
    reformatize: "📥",
    ettime: "⏱️",
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
    dashboard: "대시보드 물량",
    "dashboard.wip": "대시보드 물량",
    "dashboard.summary": "대시보드 요약 지표",
    "dashboard.stuck_lots": "대시보드 정체 랏",
    "dashboard.charts": "대시보드 차트 목록",
    inline: "Inline 측정",
    "inline.values": "Inline 측정값",
    "inline.radius_plot": "Inline Radius Plot",
    reformatize: "ET DATA 추출",
    ettime: "ET 측정시간",
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
    title: tool?.query_scope?.family === "VM" && feature === "inline" ? "VM 가상계측" : titles[feature] || titles[tool?.action] || feature || "데이터 뷰",
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

function WorkspaceTeg({ tool }) {
  return (
    <div className="home-workspace__teg-container">
      <TegChatMaps maps={tool.teg_maps} view={tool.teg_view} />
    </div>
  );
}

function WorkspaceSplitTable({ tool }) {
  const nativeSplitView = nativeSplitViewFromTool(tool);
  const table = nativeSplitView ? null : (tool.table || tool.split_view);
  const ctx = tool.context || {};
  return (
    <div className="home-workspace__split-container">
      <div className="home-workspace__meta-bar">
        <span className="home-workspace__meta-item">제품: <strong>{ctx.product || "-"}</strong></span>
        <span className="home-workspace__meta-item">Lot: <strong>{ctx.root_lot_id || ctx.lot_id || "-"}</strong></span>
        {ctx.custom_name && <span className="home-workspace__meta-item">공정: <strong>{ctx.custom_name}</strong></span>}
      </div>

      {nativeSplitView && (
        <SplitTableSnapshotView
          stView={nativeSplitView}
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
        />
      )}
    </div>
  );
}

function WorkspaceLocation({ tool }) {
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

      {table && (
        <DataTable
          table={table}
          downloadName={`location_${prod}_${rootLot || "lots"}`}
        />
      )}
    </div>
  );
}

function WorkspaceDashboard({ tool }) {
  const initialChart = tool.chart_result?.kind === "dashboard_wip_split" ? tool.chart_result : null;
  const [dashboard, setDashboard] = useState(initialChart);
  const [splitValue, setSplitValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setDashboard(initialChart);
    setSplitValue("");
    setError("");
  }, [initialChart]);

  const changeSplit = async (splitCol) => {
    if (!dashboard || loading) return;
    setSplitValue("");
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({
        product: dashboard.product || "",
        bin_size: String(dashboard.bin_size || 30000),
        split_col: splitCol,
        axis: dashboard.axis || "step_desc",
        exclude_root_prefix: dashboard.exclude_root_prefix || "",
      });
      const next = await sf(`/api/dashboard/wip-split?${query.toString()}`);
      setDashboard({
        ...dashboard,
        ...next,
        kind: "dashboard_wip_split",
        chart_type: "wip_stacked",
        title: `${next.product || dashboard.product || "ALL"} WIP × Split Dashboard`,
      });
    } catch (err) {
      setError(err?.message || String(err));
    } finally {
      setLoading(false);
    }
  };

  if (dashboard) {
    const options = Array.isArray(dashboard.split_options) ? dashboard.split_options : [];
    const columns = options.length
      ? options.map((option) => option?.col).filter(Boolean)
      : (Array.isArray(dashboard.split_cols) ? dashboard.split_cols : []);
    const values = Array.isArray(dashboard.split_values) ? dashboard.split_values : [];
    const selectedValue = values.includes(splitValue) ? splitValue : "";
    const bins = dashboard.bins || [];
    const shownBins = selectedValue ? bins.map((bin) => ({
      ...bin,
      splits: { [selectedValue]: Number(bin.splits?.[selectedValue] || 0) },
    })) : bins;
    const selectedTotal = selectedValue
      ? bins.reduce((sum, bin) => sum + Number(bin.splits?.[selectedValue] || 0), 0)
      : Number(dashboard.total_wafers || 0);
    return (
      <div className="home-workspace__dashboard-container">
        <div className="home-workspace__dashboard-toolbar">
          <div>
            <span>제품</span>
            <strong>{dashboard.product || "ALL"}</strong>
          </div>
          <label>
            <span>Split 기준 열</span>
            <select value={dashboard.split_col || ""} disabled={loading || !columns.length} onChange={(event) => changeSplit(event.target.value)}>
              {columns.map((column) => <option key={column} value={column}>{column}</option>)}
            </select>
          </label>
          <label>
            <span>Split 값별 물량</span>
            <select aria-label="Split 값별 물량" value={selectedValue} disabled={loading || !values.length} onChange={(event) => setSplitValue(event.target.value)}>
              <option value="">전체 Split</option>
              {values.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <div className="home-workspace__dashboard-stat">
            <span>총 WAFER</span>
            <strong>{Number(dashboard.total_wafers || 0).toLocaleString()}</strong>
          </div>
          <div className="home-workspace__dashboard-stat">
            <span>SPLIT 매칭</span>
            <strong>{dashboard.total_wafers ? `${Math.round((Number(dashboard.matched_wafers || 0) / Number(dashboard.total_wafers)) * 100)}%` : "0%"}</strong>
          </div>
          {selectedValue && <div className="home-workspace__dashboard-stat">
            <span>선택 Split WAFER</span>
            <strong>{selectedTotal.toLocaleString()}</strong>
          </div>}
          {loading && <span className="home-workspace__dashboard-loading">조회 중…</span>}
        </div>
        {error && <div className="home-workspace__dashboard-error">{error}</div>}
        <div className="home-workspace__dashboard-chart" aria-label="STEP 구간별 WAFER 물량 막대 차트">
          <div className="home-workspace__dashboard-chart-title">STEP 구간별 WAFER 물량 · {dashboard.split_col || "split 없음"}{selectedValue ? ` = ${selectedValue}` : ""}</div>
          <WipStackedBar
            bins={shownBins}
            splitValues={selectedValue ? [selectedValue] : values}
            unassignedLabel={dashboard.unassigned_label || "(미지정)"}
            axis={dashboard.axis || "step_desc"}
            height={420}
          />
        </div>
      </div>
    );
  }

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
        />
      )}
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
      {table && (
        <DataTable
          table={table}
          downloadName="tracker_issues"
        />
      )}
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
      {table && (
        <DataTable
          table={table}
          downloadName={`yieldmap_${ctx.product || "data"}`}
        />
      )}
    </div>
  );
}

function LiveFeatureWorkspace({ workspace, onClose, onMaximize, isMaximized }) {
  // 오른쪽 결과창: 제목 + 데이터만. 해석·선택지·이동·필터는 왼쪽 패널이 담당한다.
  if (!workspace || !workspace.tool) return null;
  const { feature, tool } = workspace;
  const info = featureInfo(feature, tool);

  const isTegView = Boolean(
    feature === "teg" ||
    tool.action?.startsWith("teg") ||
    tool.teg ||
    tool.related_tegs?.length ||
    (tool.action === "location" && tool.context?.target_teg)
  );

  return (
    <aside className={`home-data-chat__workspace-pane${isMaximized ? " is-maximized" : ""}`} aria-label="결과">
      <div className="home-workspace__header">
        <div className="home-workspace__header-left">
          <span className="home-workspace__icon">{info.icon}</span>
          <div className="home-workspace__titles">
            <div className="home-workspace__title">{info.title}</div>
            {!isTegView && info.subtitle && <div className="home-workspace__subtitle">{info.subtitle}</div>}
          </div>
        </div>

        <div className="home-workspace__header-right">
          <button type="button" className="home-workspace__tool-btn" onClick={onMaximize} title={isMaximized ? "분할 보기" : "전체 창으로 확대"}>
            {isMaximized ? "⤡ 축소" : "⤢ 전체"}
          </button>
          <button type="button" className="home-workspace__tool-btn" onClick={onClose} title="결과창 접기" aria-label="결과창 접기">
            ✕
          </button>
        </div>
      </div>

      <div className="home-workspace__body">
        {tool.report_template && <section className="home-workspace__report-preview" aria-label="리포트 구성">
          <h3>{tool.report_template.name}</h3>
          {(tool.report_template.pages || []).map((page, index) => <div key={page.id || index}>
            <strong>{index + 1}페이지 · {page.title}</strong>
            <ol>{(page.slots || []).map(slot => <li key={slot.position}>{slot.chart_name || slot.chart_label || slot.title || `차트 ${slot.position}`}</li>)}</ol>
          </div>)}
          {tool.approval?.status === "applied" && tool.report_template.id && <a href={`/templatereport?template_id=${encodeURIComponent(tool.report_template.id)}`} target="_blank" rel="noreferrer">Template Report에서 실행·다운로드 ↗</a>}
        </section>}
        {!tool.report_template && tool.chart_result && tool.chart_result.kind !== "dashboard_wip_split" && (
          <div className="home-workspace__chart">
            {tool.chart_result.chart_type === "wafer_map" ? <TegValueWaferMap
              key={tool.saved_chart?.id || tool.chart_result.title}
              vehicle={tool.chart_result.product} panels={tool.chart_result.panels} points={tool.chart_result.points}
              title={tool.chart_result.title} valueLabel={tool.chart_result.y_label}
              mapData={tool.chart_result.wafer_geometry} scaleValues={tool.chart_result.scale_values}
              palette={tool.chart_result.wafer_palette} low={tool.chart_result.wafer_low}
              center={tool.chart_result.wafer_center} high={tool.chart_result.wafer_high}
            /> : <FlowPlotlyChart chart={tool.chart_result} cfg={tool.chart_result} dark={false} />}
          </div>
        )}

        {(tool.chart_panels || []).map((panel, index) => (
          panel?.chart && (
            <div className="home-workspace__chart" key={panel.title || index}>
              {panel.title && <div className="home-workspace__dashboard-chart-title">{panel.title}</div>}
              <FlowPlotlyChart chart={panel.chart} cfg={panel.chart} dark={false} />
            </div>
          )
        ))}

        {tool.saved_chart?.id && <div className="home-workspace__saved-chart" role="status">
          <span>차트생성 이력에 저장됨 · Template report에서 재사용할 수 있습니다.</span>
          <a href={`/chartbuilder?history_id=${encodeURIComponent(tool.saved_chart.id)}`} target="_blank" rel="noreferrer">저장된 차트 열기 ↗</a>
        </div>}
        {tool.download_job?.job_id && (
          <HomeDownloadJob key={tool.download_job.job_id} job={tool.download_job} />
        )}

        {isTegView ? (
          <WorkspaceTeg tool={tool} />
        ) : feature === "splittable" || tool.action?.startsWith("splittable") ? (
          <WorkspaceSplitTable tool={tool} />
        ) : feature === "location" || tool.action?.startsWith("lot_progress") ? (
          <WorkspaceLocation tool={tool} />
        ) : feature === "dashboard" || tool.action?.startsWith("dashboard") ? (
          <WorkspaceDashboard tool={tool} />
        ) : feature === "tracker" || tool.action?.startsWith("tracker") ? (
          <WorkspaceTracker tool={tool} />
        ) : feature === "yield_map" || tool.action?.startsWith("yield_map") ? (
          <WorkspaceYieldMap tool={tool} />
        ) : tool.table && !tool.download_job?.job_id ? (
          <DataTable table={tool.table} />
        ) : null}
      </div>
    </aside>
  );
}

export default function HomeDataChat({ user, onNavigate, enabled = false, probeKey = 0, onClose }) {
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
    const restored = loadChatState(username);
    setChatState(restored);
    restoreWorkspace(restored.messages);
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

  const restoreWorkspace = (messages) => {
    const lastToolMsg = [...messages].reverse().find((m) => m.response?.tool);
    const tool = lastToolMsg?.response?.tool;
    const feature = !tool || isHumanInLoopTool(tool) ? null : (tool.feature || (tool.chart_result ? "chart" : (tool.table ? "table" : null)));
    setActiveWorkspace(feature ? { feature, tool, lastUpdated: new Date().toLocaleTimeString(), isMutated: false } : null);
    setWorkspaceOpen(Boolean(feature));
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

      restoreWorkspace(loadedMessages);
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
  const selectedReportCharts = Array.isArray(chatState.context.selected_report_charts) ? chatState.context.selected_report_charts : [];
  const reportChoices = reportChartChoices(chatState.messages, selectedReportCharts);
  const toggleChart = (chart) => setChatState(current => ({
    ...current,
    context: { ...current.context, selected_report_charts: toggleReportChart(current.context.selected_report_charts || [], chart) },
    updatedAt: Date.now(),
  }));

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

  const submit = async (event, decision = "") => {
    event?.preventDefault();
    const value = (decision || prompt).trim();
    if (!enabled || !value || loading || conversationLoading || submitLockRef.current) return;
    submitLockRef.current = true;

    const requestVersion = requestVersionRef.current + 1;
    requestVersionRef.current = requestVersion;
    const userMessage = { id: `${Date.now()}-user`, role: "user", content: value };
    const requestContext = { ...chatState.context, selected_report_charts: selectedReportCharts };
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
      const feature = isHumanInLoopTool(tool) ? null : (tool?.feature || (tool?.chart_result ? "chart" : (tool?.table ? "table" : null)));

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
      } else if (decision || isHumanInLoopTool(tool)) {
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
  const assistantResponses = chatState.messages.filter(
    (message) => message.role === "assistant" && message.response && !message.error
  );
  const latestResponse = assistantResponses.length
    ? assistantResponses[assistantResponses.length - 1].response
    : null;

  return (
    <section className={`home-data-chat${hasActiveWorkspace ? " has-workspace" : ""}`} aria-label="데이터 채팅">
      <div className="home-data-chat__conversation-bar">
        <div className="home-data-chat__conversation-controls">
          <select value={chatState.conversationId} onChange={(event) => selectConversation(event.target.value)} disabled={conversationLoading || loading} aria-label="저장된 대화 선택">
            <option value={chatState.conversationId}>{conversations.find((item) => item.id === chatState.conversationId)?.title || "새 대화"}</option>
            {conversations.filter((conversation) => conversation.id !== chatState.conversationId).map((conversation) => (
              <option key={conversation.id} value={conversation.id}>{conversation.title || "제목 없는 대화"}</option>
            ))}
          </select>
          <button type="button" onClick={newConversation} disabled={loading}>새 대화</button>
          <ModelStatus key={username} refreshKey={modelRefreshKey} probeKey={probeKey} turnUsage={turnUsage} />
          {activeWorkspace && !workspaceOpen && (
            <button type="button" className="home-data-chat__reopen-btn" onClick={() => setWorkspaceOpen(true)}>
              🖥️ 결과창 열기 ({featureInfo(activeWorkspace.feature, activeWorkspace.tool).title})
            </button>
          )}
          {conversationLoading && <span className="home-data-chat__conversation-status">불러오는 중…</span>}
          {conversationError && <span className="home-data-chat__conversation-error" role="status">{conversationError}</span>}
        </div>
        {onClose && <button type="button" className="home-data-chat__close-btn" onClick={onClose} aria-label="Flow-i 종료하고 홈으로 돌아가기">종료</button>}
      </div>

      <div className="home-data-chat__body">
        <div className={`home-data-chat__chat-pane${workspaceMaximized ? " is-hidden" : ""}`}>
          <InterpretationPanel
            response={latestResponse}
            disabled={loading || conversationLoading}
            onSubmit={(value) => submit(null, value)}
          />
          {reportChoices.length > 0 && <section className="home-data-chat__report-charts" aria-label="리포트 차트 선택">
            <details>
              <summary>리포트에 담을 차트 · {selectedReportCharts.length}개 선택</summary>
              <p>수정한 버전을 선택하세요. 선택 순서대로 배치하며 최대 24개까지 담을 수 있습니다.</p>
              <div className="home-data-chat__report-chart-list">
                {reportChoices.map(chart => <label key={chart.id}>
                  <input type="checkbox" checked={selectedReportCharts.some(item => item.id === chart.id)}
                    disabled={loading || conversationLoading || (selectedReportCharts.length >= 24 && !selectedReportCharts.some(item => item.id === chart.id))}
                    onChange={() => toggleChart(chart)} />
                  <span>{chart.name}</span>
                </label>)}
              </div>
            </details>
            <button type="button" disabled={loading || conversationLoading || !selectedReportCharts.length}
              onClick={() => submit(null, "선택한 차트로 보고서 템플릿 만들어줘")}>선택한 {selectedReportCharts.length}개로 Template Report 만들기</button>
          </section>}
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
              return chatState.messages.map((message) => {
              const isBatchMessage = Array.isArray(message.response?.questions) && message.response.questions.length > 0;

              return (
                <article key={message.id} className={`home-data-chat__message is-${message.role}${message.error ? " is-error" : ""}`}>
                  <div className="home-data-chat__speaker">{message.role === "user" ? "나" : "Flow"}</div>
                  <div className="home-data-chat__bubble">
                     {isBatchMessage
                       ? <BatchContent response={message.response} />
                       : <BubbleContent message={message} />}
                  </div>

                  {message.response?.usage && <div className="home-data-chat__usage">이번 요청 LLM {message.response.usage.llm_calls_used}회 차감 / 최대 {message.response.usage.llm_call_limit}회 · 당시 분당 잔여 {message.response.usage.minute_calls_remaining}회</div>}
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

        {hasActiveWorkspace ? (
          <LiveFeatureWorkspace
            workspace={activeWorkspace}
            onClose={() => { setWorkspaceOpen(false); setWorkspaceMaximized(false); }}
            onMaximize={() => setWorkspaceMaximized((v) => !v)}
            isMaximized={workspaceMaximized}
          />
        ) : (
          latestResponse && isHumanInLoopTool(latestResponse.tool) && (
            <aside className="home-data-chat__workspace-pane is-idle" aria-label="결과 대기">
              <div className="home-workspace__body">
                <div className="home-workspace__idle">왼쪽에서 선택을 마치면 여기에 결과가 표시됩니다.</div>
              </div>
            </aside>
          )
        )}
      </div>
    </section>
  );
}
