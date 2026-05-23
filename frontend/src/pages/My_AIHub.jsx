// My_AIHub.jsx — flow 본진 v9.1 AI Hub.
// Unit AI 11개 + Function-call 16개를 한 화면 3-pane 으로 카탈로그·관리.
// 백엔드 /api/ai-hub/tools 가 단일 진실이며, 이 페이지는 표시·필터·toggle 만 담당한다.

import { useEffect, useMemo, useState } from "react";
import { sf, postJson, dl } from "../lib/api";

const KIND_LABELS = {
  unit_ai: "Unit AI",
  function: "Function",
};

const KIND_COLORS = {
  unit_ai: "var(--accent)",
  function: "var(--info)",
};

export default function My_AIHub() {
  const [days, setDays] = useState(30);
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({ unit_ai: 0, function: 0, enabled: 0, total: 0 });
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [kindFilter, setKindFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [enabledOnly, setEnabledOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [history, setHistory] = useState([]);
  const [runbookFocus, setRunbookFocus] = useState(null);
  const [readinessFocus, setReadinessFocus] = useState(null);
  const [opsPanelFocus, setOpsPanelFocus] = useState(null);

  async function loadCatalog() {
    setLoading(true);
    setErr("");
    try {
      const res = await sf(`/api/ai-hub/tools?days=${days}`);
      setItems(res.items || []);
      setCounts(res.counts || {});
      setIsAdmin(!!res.is_admin);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadCatalog(); }, [days]);

  async function loadHistory(name) {
    setHistory([]);
    try {
      const res = await sf(`/api/ai-hub/tools/${encodeURIComponent(name)}/history?days=${days}&limit=20`);
      setHistory(res.items || []);
    } catch (_) { /* ignore */ }
  }

  async function toggle(name, enabled) {
    if (!isAdmin) return;
    try {
      await postJson(`/api/ai-hub/tools/${encodeURIComponent(name)}/toggle`, { enabled });
      setItems((arr) => arr.map((it) => it.name === name ? withEnabledState(it, enabled) : it));
      if (selected && selected.name === name) setSelected(withEnabledState(selected, enabled));
    } catch (e) {
      alert(`토글 실패: ${e.message || e}`);
    }
  }

  const allTags = useMemo(() => {
    const bag = new Map();
    for (const it of items) {
      for (const t of (it.tags || [])) bag.set(t, (bag.get(t) || 0) + 1);
    }
    return [...bag.entries()].sort((a, b) => b[1] - a[1]).map(([t]) => t);
  }, [items]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((it) => {
      if (kindFilter && it.kind !== kindFilter) return false;
      if (tagFilter && !(it.tags || []).includes(tagFilter)) return false;
      if (enabledOnly && !it.enabled) return false;
      if (q) {
        const hay = (it.name + " " + it.title + " " + (it.description || "")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [items, kindFilter, tagFilter, enabledOnly, search]);

  function openDetail(it) {
    setSelected(it);
    loadHistory(it.name);
  }

  function focusRunbookIssue(row) {
    if (!row?.key) return;
    setRunbookFocus((prev) => ({
      issue: row.key,
      title: row.title || row.key,
      nonce: (prev?.nonce || 0) + 1,
    }));
  }

  function focusReadinessBacklog(row) {
    if (!row?.id) return;
    setReadinessFocus((prev) => ({
      id: row.id,
      title: row.title || row.target || row.id,
      nonce: (prev?.nonce || 0) + 1,
    }));
  }

  function focusOpsSummaryCard(card) {
    if (!card?.key) return;
    const title = card.label || card.key;
    if (card.key === "readiness") {
      setReadinessFocus((prev) => ({ title, nonce: (prev?.nonce || 0) + 1 }));
      return;
    }
    if (card.key === "workflow_runbook") {
      setRunbookFocus((prev) => ({ title, nonce: (prev?.nonce || 0) + 1 }));
      return;
    }
    setOpsPanelFocus((prev) => ({ target: card.key, title, nonce: (prev?.nonce || 0) + 1 }));
  }

  function focusTimelineEvent(row) {
    const category = row?.category || "";
    setOpsPanelFocus((prev) => ({
      target: "timeline",
      category,
      title: category ? `운영 이벤트: ${category}` : "운영 이벤트",
      nonce: (prev?.nonce || 0) + 1,
    }));
  }

  function focusWorkflowMapWarning(row) {
    const title = row?.title || row?.key || "워크플로우 지도";
    const route = row?.route || "";
    if (route.includes("/workflow-runbook")) {
      setRunbookFocus((prev) => ({
        issue: workflowWarningToRunbookIssue(row?.key),
        title: `지도 경고: ${title}`,
        nonce: (prev?.nonce || 0) + 1,
      }));
      return;
    }
    if (route.includes("/wiki-health")) {
      setOpsPanelFocus((prev) => ({
        target: "wiki",
        title: `지도 경고: ${title}`,
        nonce: (prev?.nonce || 0) + 1,
      }));
      return;
    }
    if (route.includes("/deep-eval-report")) {
      setOpsPanelFocus((prev) => ({
        target: "deep_eval",
        title: `지도 경고: ${title}`,
        nonce: (prev?.nonce || 0) + 1,
      }));
      return;
    }
    if (route.includes("/tools")) {
      setKindFilter("");
      setEnabledOnly(false);
      setSearch((row?.items || [])[0] || "");
      return;
    }
    setOpsPanelFocus((prev) => ({
      target: "workflow_map",
      warning: row?.key || "",
      title: `지도 경고: ${title}`,
      nonce: (prev?.nonce || 0) + 1,
    }));
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", background: "var(--bg-primary)" }}>
      {/* 오케스트레이터 + 운영 보드 + 스킬 패널 */}
      <OpsSnapshotPanel
        days={days}
        onSummaryCard={focusOpsSummaryCard}
        onReadinessBacklog={focusReadinessBacklog}
        onRunbookIssue={focusRunbookIssue}
        onWorkflowMapWarning={focusWorkflowMapWarning}
        onTimelineEvent={focusTimelineEvent}
      />
      <OrchestratorPanel />
      <ReadinessPanel days={days} focusIntent={readinessFocus} onChanged={loadCatalog} />
      <DeepEvalPanel focusIntent={opsPanelFocus?.target === "deep_eval" ? opsPanelFocus : null} />
      <WikiHealthPanel focusIntent={opsPanelFocus?.target === "wiki" ? opsPanelFocus : null} />
      <OperationsBoard days={days} onChanged={loadCatalog} />
      <WorkflowRunbookPanel days={days} focusIntent={runbookFocus} />
      <TimelinePanel days={days} focusIntent={opsPanelFocus?.target === "timeline" ? opsPanelFocus : null} />
      <WorkflowMapPanel days={days} focusIntent={opsPanelFocus?.target === "workflow_map" ? opsPanelFocus : null} onWarningSelect={focusWorkflowMapWarning} />
      <SkillsPanel />

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      {/* 좌측 필터 패널 */}
      <aside style={{ width: 240, borderRight: "1px solid var(--border)", padding: 16, overflowY: "auto",
        background: "var(--bg-secondary)" }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: "var(--text-primary)" }}>
          AI 허브
        </div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 16, lineHeight: 1.5 }}>
          Unit AI <b>{counts.unit_ai}</b> · Function <b>{counts.function}</b><br/>
          Enabled <b>{counts.enabled}</b> / {counts.total}
        </div>

        <input
          type="text"
          placeholder="검색 (이름·설명)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={inputStyle}
        />

        <div style={sectionLabelStyle}>종류</div>
        <FilterRadio value={kindFilter} setValue={setKindFilter} options={[
          { value: "", label: "전체" },
          { value: "unit_ai", label: "Unit AI (11)" },
          { value: "function", label: "Function (16)" },
        ]} />

        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", margin: "12px 0 8px" }}>
          <input type="checkbox" checked={enabledOnly} onChange={(e) => setEnabledOnly(e.target.checked)} />
          Enabled 만
        </label>

        <div style={sectionLabelStyle}>기간</div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={selectStyle}>
          <option value={7}>최근 7일</option>
          <option value={30}>최근 30일</option>
          <option value={90}>최근 90일</option>
        </select>

        <div style={sectionLabelStyle}>태그 ({allTags.length})</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          <TagChip label="전체" active={!tagFilter} onClick={() => setTagFilter("")} />
          {allTags.map((t) => (
            <TagChip key={t} label={t} active={tagFilter === t} onClick={() => setTagFilter(t)} />
          ))}
        </div>
      </aside>

      {/* 중앙 그리드 */}
      <main style={{ flex: 1, overflowY: "auto", padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            {filtered.length} / {items.length} 도구
          </div>
          <button onClick={loadCatalog} style={btnGhost}>↻ 새로고침</button>
        </div>
        {err && <div style={{ color: "var(--danger)", padding: 8, fontSize: 12 }}>{err}</div>}
        {loading ? (
          <div style={{ color: "var(--text-secondary)", padding: 24 }}>로딩 중…</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
            {filtered.map((it) => (
              <Card
                key={it.kind + ":" + it.name}
                item={it}
                selected={selected && selected.name === it.name}
                onClick={() => openDetail(it)}
                isAdmin={isAdmin}
                onToggle={(en) => toggle(it.name, en)}
              />
            ))}
            {filtered.length === 0 && <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>조건에 맞는 도구가 없습니다.</div>}
          </div>
        )}
      </main>

      {/* 우측 상세 */}
      <aside style={{ width: 380, borderLeft: "1px solid var(--border)", padding: 16, overflowY: "auto",
        background: "var(--bg-secondary)" }}>
        {!selected ? (
          <div style={{ color: "var(--text-secondary)", fontSize: 12, lineHeight: 1.6 }}>
            카드를 선택하면 입력 스키마, 데이터 소스, 호출 이력을 볼 수 있습니다.
          </div>
        ) : (
          <Detail item={selected} history={history} days={days} />
        )}
      </aside>
      </div>
    </div>
  );
}


function withEnabledState(item, enabled) {
  const flow = item.management_flow || {};
  const nodes = (flow.nodes || []).map((node) => (
    node.id === "guardrail" ? { ...node, state: enabled ? "enabled" : "disabled" } : node
  ));
  return { ...item, enabled, management_flow: { ...flow, nodes } };
}


function workflowWarningToRunbookIssue(key) {
  return {
    workflow_missing_tools: "missing_tools",
    workflow_empty_templates: "no_steps",
    workflow_incomplete_steps: "incomplete_steps",
    missing_evidence: "no_evidence",
    disabled_tools: "disabled_tools",
  }[key] || key || "";
}


function OpsSnapshotPanel({ days, onSummaryCard, onReadinessBacklog, onRunbookIssue, onWorkflowMapWarning, onTimelineEvent }) {
  const [open, setOpen] = useState(true);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState("");
  const [err, setErr] = useState("");

  async function loadSnapshot() {
    setLoading(true);
    setErr("");
    try {
      const out = await sf(`/api/ai-hub/ops-snapshot?days=${days}&limit=6`);
      setData(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function exportOps(link) {
    const key = link?.key || link?.format || "";
    setExporting(key);
    setErr("");
    try {
      await dl(link.href, link.filename);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setExporting("");
    }
  }

  useEffect(() => { if (open) loadSnapshot(); }, [open, days]);

  const cards = data?.summary_cards || [];
  const links = data?.export_links || [];
  const statusTone = data?.status === "ok" ? "ok" : data?.status === "bad" ? "bad" : "warn";
  const statusText = data?.status === "ok" ? "정상" : data?.status === "bad" ? "문제" : "점검";
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flexWrap: "wrap" }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 900, color: "var(--text-primary)", whiteSpace: "nowrap" }}>운영 스냅샷</div>
        <BoardPill tone={data ? statusTone : "neutral"}>{data ? statusText : "대기"}</BoardPill>
        {data && <BoardPill tone="neutral">{relTime(data.generated_at)}</BoardPill>}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, minWidth: 0 }}>
          {cards.slice(0, 4).map((card) => <BoardPill key={card.key} tone={card.tone}>{card.label} {card.value}</BoardPill>)}
        </div>
        <div style={{ flex: 1 }} />
        {open && links.map((link) => (
          <button key={link.key} onClick={() => exportOps(link)} disabled={!!exporting || !link.href} style={btnGhost}>
            {exporting === link.key ? "내보내는 중" : link.label}
          </button>
        ))}
        {open && <button onClick={loadSnapshot} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!data && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : data ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 8 }}>
              <OpsSnapshotSummary data={data} onSelect={onSummaryCard} />
              <OpsSnapshotActions rows={data.top_actions || []} onSelect={onReadinessBacklog} />
              <OpsSnapshotRunbookQueue rows={data.runbook_action_queue || []} onSelect={onRunbookIssue} />
              <OpsSnapshotWorkflowMapWarnings rows={data.workflow_map_warnings || []} onSelect={onWorkflowMapWarning} />
              <OpsSnapshotEvents rows={data.recent_events || []} onSelect={onTimelineEvent} />
            </div>
          ) : (
            <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, fontSize: 11, color: "var(--text-secondary)" }}>
              스냅샷 없음
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function OpsSnapshotSummary({ data, onSelect }) {
  const cardStyle = {
    border: "0",
    borderTop: "1px dashed var(--border)",
    background: "transparent",
    color: "inherit",
    font: "inherit",
    padding: "6px 0 0",
    textAlign: "left",
    minWidth: 0,
    width: "100%",
  };
  function cardContent(card) {
    return (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{card.label}</span>
          <BoardPill tone={card.tone}>{card.value}</BoardPill>
        </div>
        <div style={{ marginTop: 3, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {card.detail}
        </div>
      </>
    );
  }
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 10, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 900, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {data.headline}
        </div>
        <BoardPill tone={data.status === "ok" ? "ok" : data.status === "bad" ? "bad" : "warn"}>{data.status}</BoardPill>
      </div>
      <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "repeat(2, minmax(120px, 1fr))", gap: 6 }}>
        {(data.summary_cards || []).map((card) => (
          onSelect && card.key ? (
            <button
              key={card.key}
              type="button"
              onClick={() => onSelect(card)}
              style={{ ...cardStyle, cursor: "pointer" }}
              title={`${card.label} 패널 열기`}
            >
              {cardContent(card)}
            </button>
          ) : (
            <div key={card.key} style={cardStyle}>
              {cardContent(card)}
            </div>
          )
        ))}
      </div>
    </div>
  );
}


function OpsSnapshotActions({ rows, onSelect }) {
  const visible = rows.slice(0, 6);
  const rowStyle = {
    border: "0",
    borderTop: "1px dashed var(--border)",
    background: "transparent",
    color: "inherit",
    font: "inherit",
    padding: "5px 0 0",
    textAlign: "left",
    width: "100%",
  };
  function rowContent(row) {
    return (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
          <div style={{ minWidth: 0, fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.title}</div>
          <BoardPill tone={row.tone}>{row.severity || "low"}</BoardPill>
        </div>
        <div style={{ marginTop: 2, fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.target}</div>
        <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.action || row.detail}</div>
      </>
    );
  }
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>상위 개선</div>
        <BoardPill tone={rows.length ? "warn" : "ok"}>{rows.length}</BoardPill>
      </div>
      {visible.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>대기 항목 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 170, overflowY: "auto" }}>
          {visible.map((row) => (
            onSelect && row.id ? (
              <button
                key={row.id || row.title}
                type="button"
                onClick={() => onSelect(row)}
                style={{ ...rowStyle, cursor: "pointer" }}
                title="운영 준비도에서 이 개선 항목 보기"
              >
                {rowContent(row)}
              </button>
            ) : (
              <div key={row.id || row.title} style={rowStyle}>
                {rowContent(row)}
              </div>
            )
          ))}
        </div>
      )}
    </div>
  );
}


function OpsSnapshotRunbookQueue({ rows, onSelect }) {
  const visible = rows.slice(0, 6);
  const rowStyle = {
    border: "0",
    borderTop: "1px dashed var(--border)",
    background: "transparent",
    color: "inherit",
    font: "inherit",
    padding: "5px 0 0",
    textAlign: "left",
    width: "100%",
  };
  function rowContent(row) {
    return (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
          <div style={{ minWidth: 0, fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.title || row.key}</div>
          <BoardPill tone={row.tone}>{row.count || 0}</BoardPill>
        </div>
        <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.detail || row.route}</div>
        <div style={{ marginTop: 3, display: "flex", flexWrap: "wrap", gap: 4 }}>
          {(row.workflows || []).slice(0, 3).map((item) => <Tag key={item.key}>{item.title || item.key}</Tag>)}
          {(row.count || 0) > (row.workflows || []).slice(0, 3).length && <Tag>+{(row.count || 0) - (row.workflows || []).slice(0, 3).length}</Tag>}
        </div>
      </>
    );
  }
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>Runbook 조치 큐</div>
        <BoardPill tone={rows.length ? "warn" : "ok"}>{rows.length}</BoardPill>
      </div>
      {visible.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>대기 조치 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 170, overflowY: "auto" }}>
          {visible.map((row) => (
            onSelect && row.key ? (
              <button
                key={row.key || row.title}
                type="button"
                onClick={() => onSelect(row)}
                style={{ ...rowStyle, cursor: "pointer" }}
                title="Workflow Runbook에서 이 issue로 보기"
              >
                {rowContent(row)}
              </button>
            ) : (
              <div key={row.key || row.title} style={rowStyle}>
                {rowContent(row)}
              </div>
            )
          ))}
        </div>
      )}
    </div>
  );
}


function OpsSnapshotWorkflowMapWarnings({ rows, onSelect }) {
  const visible = rows.slice(0, 6);
  const rowStyle = {
    border: "0",
    borderTop: "1px dashed var(--border)",
    background: "transparent",
    color: "inherit",
    font: "inherit",
    padding: "5px 0 0",
    textAlign: "left",
    width: "100%",
  };
  function rowContent(row) {
    return (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
          <div style={{ minWidth: 0, fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.title || row.key}</div>
          <BoardPill tone={row.tone || "neutral"}>{row.item_count || 0}</BoardPill>
        </div>
        <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.message || row.route}</div>
        {row.action && (
          <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-primary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
            조치 {row.action}
          </div>
        )}
        <div style={{ marginTop: 3, display: "flex", flexWrap: "wrap", gap: 4 }}>
          {(row.items || []).slice(0, 4).map((item) => <Tag key={item}>{item}</Tag>)}
          {(row.item_count || 0) > (row.items || []).slice(0, 4).length && <Tag>+{(row.item_count || 0) - (row.items || []).slice(0, 4).length}</Tag>}
        </div>
      </>
    );
  }
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>지도 경고</div>
        <BoardPill tone={rows.length ? "warn" : "ok"}>{rows.length}</BoardPill>
      </div>
      {visible.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>지도 경고 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 170, overflowY: "auto" }}>
          {visible.map((row) => (
            onSelect && row.key ? (
              <button
                key={row.key || row.title}
                type="button"
                onClick={() => onSelect(row)}
                style={{ ...rowStyle, cursor: "pointer" }}
                title="조치 대상 패널 열기"
              >
                {rowContent(row)}
              </button>
            ) : (
              <div key={row.key || row.title} style={rowStyle}>
                {rowContent(row)}
              </div>
            )
          ))}
        </div>
      )}
    </div>
  );
}


function OpsSnapshotEvents({ rows, onSelect }) {
  const visible = rows.slice(0, 6);
  const rowStyle = {
    border: "0",
    borderTop: "1px dashed var(--border)",
    background: "transparent",
    color: "inherit",
    font: "inherit",
    padding: "5px 0 0",
    textAlign: "left",
    width: "100%",
  };
  function rowContent(row) {
    return (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
          <div style={{ minWidth: 0, fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.title || row.action}</div>
          <BoardPill tone={row.tone || "neutral"}>{row.category || "event"}</BoardPill>
        </div>
        <div style={{ marginTop: 2, display: "flex", flexWrap: "wrap", gap: 4 }}>
          <Tag>{row.username || "unknown"}</Tag>
          <Tag>{relTime(row.timestamp)}</Tag>
          {row.meta && <Tag>{row.meta}</Tag>}
        </div>
        {row.detail && (
          <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.detail}</div>
        )}
      </>
    );
  }
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>최근 이벤트</div>
        <BoardPill tone="neutral">{rows.length}</BoardPill>
      </div>
      {visible.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>최근 이벤트 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 170, overflowY: "auto" }}>
          {visible.map((row) => (
            onSelect ? (
              <button
                key={row.id || `${row.timestamp}:${row.action}`}
                type="button"
                onClick={() => onSelect(row)}
                style={{ ...rowStyle, cursor: "pointer" }}
                title="운영 타임라인에서 이 category로 보기"
              >
                {rowContent(row)}
              </button>
            ) : (
              <div key={row.id || `${row.timestamp}:${row.action}`} style={rowStyle}>
                {rowContent(row)}
              </div>
            )
          ))}
        </div>
      )}
    </div>
  );
}


function DeepEvalPanel({ focusIntent }) {
  const [open, setOpen] = useState(true);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [focusNonce, setFocusNonce] = useState(0);
  const [err, setErr] = useState("");

  async function loadReport() {
    setLoading(true);
    setErr("");
    try {
      const out = await sf("/api/ai-hub/deep-eval-report");
      setData(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!focusIntent?.nonce) return;
    setOpen(true);
    setFocusNonce(focusIntent.nonce || 0);
  }, [focusIntent?.nonce]);

  useEffect(() => { if (open) loadReport(); }, [open, focusNonce]);

  async function runDeepEval() {
    if (!data?.is_admin) return;
    setRunning(true);
    setErr("");
    try {
      const out = await postJson("/api/ai-hub/deep-eval-report/run", { cleanup_knowledge: false, min_cases: 80 });
      setData({ ...(out.report || out), is_admin: true });
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setRunning(false);
    }
  }

  const summary = data?.summary || {};
  const statusTone = !data ? "neutral" : data.status === "pass" ? "ok" : data.status === "missing" ? "warn" : "bad";
  const statusText = data?.status === "pass" ? "통과" : data?.status === "missing" ? "리포트 없음" : data?.status === "invalid" ? "손상" : data?.status === "fail" ? "실패" : "대기";
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>Agent 검증 리포트</div>
        {focusIntent?.nonce && <BoardPill tone="info">focus {focusIntent.title || "Agent 검증"}</BoardPill>}
        <BoardPill tone={statusTone}>{statusText}</BoardPill>
        {data?.exists && (
          <>
            <BoardPill tone={Number(summary.failed || 0) ? "bad" : "ok"}>{summary.passed || 0}/{summary.total || 0}</BoardPill>
            <BoardPill tone="neutral">{relTime(data.generated_at || data.updated_at)}</BoardPill>
          </>
        )}
        <div style={{ flex: 1 }} />
        {open && data?.is_admin && (
          <button onClick={runDeepEval} disabled={running} style={btnGhost}>{running ? "검증 중..." : "검증 실행"}</button>
        )}
        {open && <button onClick={loadReport} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!data && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : data?.exists ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
              <DeepEvalSummary report={data} />
              <DeepEvalGroups groups={data.groups || {}} catalog={data.catalog || {}} />
              <DeepEvalFailures rows={data.failed_results || []} status={data.status} />
              <DeepEvalCases rows={data.result_samples || []} total={data.result_count || 0} />
            </div>
          ) : data ? (
            <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 800, color: "var(--text-primary)" }}>최근 리포트 없음</div>
              <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-secondary)", fontFamily: "JetBrains Mono, monospace", wordBreak: "break-all" }}>
                {data.path}
              </div>
            </div>
          ) : (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>검증 리포트를 불러오면 최근 Agent semantic/wiki/sql 상태를 볼 수 있습니다.</div>
          )}
        </div>
      )}
    </div>
  );
}


function DeepEvalSummary({ report }) {
  const summary = report.summary || {};
  const failed = Number(summary.failed || 0);
  const tone = failed ? "var(--danger)" : "var(--ok)";
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <div style={{ fontSize: 28, lineHeight: 1, fontWeight: 900, color: tone }}>{summary.passed || 0}</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", fontWeight: 800 }}>passed / {summary.total || 0}</div>
      </div>
      <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
        <Tag>{failed} failed</Tag>
        <Tag>{report.result_count || 0} assertions</Tag>
        <Tag>{report.cleanup_knowledge ? "wiki cleanup" : "wiki kept"}</Tag>
      </div>
      <div style={{ marginTop: 7, fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", wordBreak: "break-all" }}>
        {report.doc_id || "-"}
      </div>
      <div style={{ marginTop: 4, fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", wordBreak: "break-all" }}>
        {report.path}
      </div>
    </div>
  );
}


function DeepEvalGroups({ groups, catalog }) {
  const entries = Object.entries(groups || {});
  const sourceViews = Array.isArray(catalog.source_views) ? catalog.source_views : [];
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)", marginBottom: 5 }}>검증 영역</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(90px, 1fr))", gap: 5 }}>
        {entries.map(([name, row]) => (
          <div key={name} style={{ borderTop: "1px dashed var(--border)", paddingTop: 5 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 5, alignItems: "center" }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>{name}</span>
              <BoardPill tone={Number(row.failed || 0) ? "bad" : "ok"}>{row.passed}/{row.total}</BoardPill>
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 7, display: "flex", flexWrap: "wrap", gap: 4 }}>
        <Tag>{catalog.semantic_prompt_cases || 0} semantic prompts</Tag>
        <Tag>{catalog.sql_answer_cases || 0} sql answers</Tag>
        {sourceViews.slice(0, 6).map((name) => <Tag key={name}>{name}</Tag>)}
      </div>
    </div>
  );
}


function DeepEvalFailures({ rows, status }) {
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>실패 assertion</div>
        <BoardPill tone={rows.length ? "bad" : "ok"}>{rows.length}</BoardPill>
      </div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>
          {status === "pass" ? "실패 항목 없음" : "실패 detail 없음"}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 130, overflowY: "auto" }}>
          {rows.slice(0, 6).map((row) => (
            <div key={row.name} style={{ borderTop: "1px dashed var(--border)", paddingTop: 5 }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.name}</div>
              <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.detail}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function DeepEvalCases({ rows, total }) {
  const visible = rows.slice(0, 14);
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>검증 케이스</div>
        <BoardPill tone="neutral">{rows.length}/{total || rows.length}</BoardPill>
      </div>
      {visible.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>케이스 detail 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 190, overflowY: "auto" }}>
          {visible.map((row) => (
            <div key={row.name} style={{ borderTop: "1px dashed var(--border)", paddingTop: 5 }}>
              <div style={{ display: "grid", gridTemplateColumns: "48px 62px 1fr", gap: 5, alignItems: "center" }}>
                <BoardPill tone={row.ok ? "ok" : "bad"}>{row.ok ? "PASS" : "FAIL"}</BoardPill>
                <Tag>{row.group || "case"}</Tag>
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>
                  {row.name}
                </span>
              </div>
              {row.detail && (
                <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                  {row.detail}
                </div>
              )}
            </div>
          ))}
          {rows.length > visible.length && <div style={{ fontSize: 10, color: "var(--muted)" }}>+{rows.length - visible.length} more</div>}
        </div>
      )}
    </div>
  );
}


function WikiHealthPanel({ focusIntent }) {
  const [open, setOpen] = useState(true);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [focusNonce, setFocusNonce] = useState(0);
  const [err, setErr] = useState("");

  async function loadHealth() {
    setLoading(true);
    setErr("");
    try {
      const out = await sf("/api/ai-hub/wiki-health?limit=12");
      setData(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!focusIntent?.nonce) return;
    setOpen(true);
    setFocusNonce(focusIntent.nonce || 0);
  }, [focusIntent?.nonce]);

  useEffect(() => { if (open) loadHealth(); }, [open, focusNonce]);

  const counts = data?.counts || {};
  const statusTone = !data ? "neutral" : data.status === "pass" ? "ok" : data.status === "missing" ? "warn" : "bad";
  const statusText = data?.status === "pass" ? "정상" : data?.status === "warn" ? "점검 필요" : data?.status === "missing" ? "지식 없음" : "대기";
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>Agent Wiki 상태</div>
        {focusIntent?.nonce && <BoardPill tone="info">focus {focusIntent.title || "Agent Wiki"}</BoardPill>}
        <BoardPill tone={statusTone}>{statusText}</BoardPill>
        {data && (
          <>
            <BoardPill tone="info">문서 {counts.docs || 0}</BoardPill>
            <BoardPill tone="info">소스 {counts.sources || 0}</BoardPill>
            <BoardPill tone={Number(counts.lint_issues || 0) ? "warn" : "ok"}>lint {counts.lint_issues || 0}</BoardPill>
            <BoardPill tone="neutral">graph {counts.graph_nodes || 0}/{counts.graph_edges || 0}</BoardPill>
          </>
        )}
        <div style={{ flex: 1 }} />
        {open && <button onClick={loadHealth} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!data && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : data ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
              <WikiHealthSummary data={data} />
              <WikiHealthPages rows={data.recent_pages || []} />
              <WikiHealthLog rows={data.recent_log || []} lint={data.lint || {}} />
            </div>
          ) : (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>Agent Wiki 상태를 불러오면 LLM Wiki/Obsidian 근거 품질을 볼 수 있습니다.</div>
          )}
        </div>
      )}
    </div>
  );
}


function WikiHealthSummary({ data }) {
  const counts = data.counts || {};
  const tone = data.status === "pass" ? "var(--ok)" : data.status === "missing" ? "var(--warn)" : "var(--danger)";
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 10, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <div style={{ fontSize: 28, lineHeight: 1, fontWeight: 900, color: tone }}>{counts.agent_wiki_pages || 0}</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", fontWeight: 800 }}>agent wiki</div>
      </div>
      <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
        <Tag>{counts.docs || 0} docs</Tag>
        <Tag>{counts.schema_docs || 0} schema docs</Tag>
        <Tag>{counts.sources || 0} sources</Tag>
        <Tag>{counts.wiki_log || 0} logs</Tag>
      </div>
      <div style={{ marginTop: 7, fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", wordBreak: "break-all" }}>
        {data.summary}
      </div>
      <div style={{ marginTop: 4, fontSize: 10, color: "var(--muted)" }}>{relTime(data.generated_at)}</div>
    </div>
  );
}


function WikiHealthPages({ rows }) {
  const visible = rows.slice(0, 8);
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>최근 Wiki 페이지</div>
        <BoardPill tone="neutral">{rows.length}</BoardPill>
      </div>
      {visible.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>등록된 페이지 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 170, overflowY: "auto" }}>
          {visible.map((row) => (
            <div key={row.doc_id} style={{ borderTop: "1px dashed var(--border)", paddingTop: 5 }}>
              <div style={{ display: "grid", gridTemplateColumns: "74px 1fr", gap: 6, alignItems: "center" }}>
                <Tag>{row.kind || "wiki"}</Tag>
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>{row.title || row.doc_id}</span>
              </div>
              <div style={{ marginTop: 2, fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.doc_id}</div>
              {row.summary && <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.summary}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function WikiHealthLog({ rows, lint }) {
  const counts = lint.counts || {};
  const lintIssueCount = Number(counts.broken_links || 0) + Number(counts.missing_sources || 0) + Number(counts.stale_summaries || 0) + Number(counts.contradiction_candidates || 0);
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>Wiki lint / 변경 로그</div>
        <BoardPill tone={lintIssueCount ? "warn" : "ok"}>{lintIssueCount}</BoardPill>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
        <Tag>broken {counts.broken_links || 0}</Tag>
        <Tag>missing {counts.missing_sources || 0}</Tag>
        <Tag>stale {counts.stale_summaries || 0}</Tag>
        <Tag>orphan {counts.orphan_pages || 0}</Tag>
      </div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>최근 변경 로그 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 140, overflowY: "auto" }}>
          {rows.slice(0, 8).map((row) => (
            <div key={row.log_id || `${row.created_at}:${row.action}:${row.doc_id}`} style={{ borderTop: "1px dashed var(--border)", paddingTop: 5 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 5, minWidth: 0 }}>
                <Tag>{row.action || "wiki"}</Tag>
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>{row.title || row.doc_id || row.message}</span>
              </div>
              <div style={{ marginTop: 2, fontSize: 10, color: "var(--muted)" }}>{row.actor || "-"} · {relTime(row.created_at)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function ReadinessPanel({ days, focusIntent, onChanged }) {
  const [open, setOpen] = useState(true);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState("");
  const [activeBacklogId, setActiveBacklogId] = useState("");
  const [focusNonce, setFocusNonce] = useState(0);
  const [err, setErr] = useState("");

  async function loadReadiness() {
    setLoading(true);
    setErr("");
    try {
      const out = await sf(`/api/ai-hub/readiness?days=${days}`);
      setData(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function runBacklogAction(row, action) {
    if (!data?.is_admin || !action?.endpoint || action.method !== "POST") return;
    if (action.confirm && !confirm(`${action.label} 처리할까요? ${row.target || row.title}`)) return;
    const key = `${row.id}:${action.id}`;
    setActionBusy(key);
    setErr("");
    try {
      await postJson(action.endpoint, action.body || {});
      await loadReadiness();
      if (onChanged) await onChanged();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setActionBusy("");
    }
  }

  useEffect(() => {
    if (!focusIntent?.nonce) return;
    setOpen(true);
    setActiveBacklogId(focusIntent.id || "");
    setFocusNonce(focusIntent.nonce || 0);
  }, [focusIntent?.nonce]);

  useEffect(() => { if (open) loadReadiness(); }, [open, days, focusNonce]);

  const score = Number(data?.score || 0);
  const backlog = data?.backlog || [];
  const focusLabel = focusIntent?.nonce ? (focusIntent.title || activeBacklogId || "운영 준비도") : "";
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>운영 준비도</div>
        {data && (
          <>
            <BoardPill tone={score >= 85 ? "ok" : score >= 65 ? "warn" : "bad"}>{score}점 · {data.level}</BoardPill>
            <BoardPill tone={backlog.length ? "warn" : "ok"}>개선 {backlog.length}</BoardPill>
            {focusLabel && <BoardPill tone="info">focus {focusLabel}</BoardPill>}
          </>
        )}
        <div style={{ flex: 1 }} />
        {open && <button onClick={loadReadiness} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!data && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : data ? (
            <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 220px) minmax(0, 1fr) minmax(260px, 1.2fr)", gap: 10 }}>
              <ReadinessGauge score={score} level={data.level} counts={data.counts || {}} />
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(120px, 1fr))", gap: 6 }}>
                {(data.checks || []).map((check) => (
                  <div key={check.key} style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                      <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>{check.label}</div>
                      <BoardPill tone={check.tone}>{check.score}</BoardPill>
                    </div>
                    <div style={{ marginTop: 5, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35 }}>{check.detail}</div>
                  </div>
                ))}
              </div>
              <ReadinessBacklog
                rows={backlog}
                activeId={activeBacklogId}
                canManage={!!data.is_admin}
                actionBusy={actionBusy}
                onAction={runBacklogAction}
              />
            </div>
          ) : (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>준비도 데이터를 불러오면 운영 상태와 개선 백로그를 볼 수 있습니다.</div>
          )}
        </div>
      )}
    </div>
  );
}


function ReadinessGauge({ score, level, counts }) {
  const tone = score >= 85 ? "var(--ok)" : score >= 65 ? "var(--warn)" : "var(--danger)";
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <div style={{ fontSize: 30, lineHeight: 1, fontWeight: 900, color: tone }}>{score}</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", fontWeight: 800 }}>readiness</div>
      </div>
      <div style={{ marginTop: 4, height: 6, background: "var(--bg-primary)", borderRadius: 999, overflow: "hidden", border: "1px solid var(--border)" }}>
        <div style={{ width: `${Math.max(0, Math.min(100, score))}%`, height: "100%", background: tone }} />
      </div>
      <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-primary)", fontWeight: 800 }}>{level}</div>
      <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
        <Tag>{counts.tools_enabled || 0}/{counts.tools_total || 0} tools</Tag>
        <Tag>{counts.tools_without_refs || 0} no refs</Tag>
        <Tag>{counts.semantic_proposals_pending || 0} semantic</Tag>
        <Tag>{counts.skill_candidates || 0} skill q</Tag>
      </div>
    </div>
  );
}


function ReadinessBacklog({ rows, activeId, canManage, actionBusy, onAction }) {
  const visible = (rows || []).slice(0, 8);
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>개선 백로그</div>
        <BoardPill tone={rows.length ? "warn" : "ok"}>{rows.length}</BoardPill>
      </div>
      {visible.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>즉시 처리할 개선 항목이 없습니다.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 170, overflowY: "auto" }}>
          {visible.map((row) => {
            const active = activeId === row.id;
            return (
              <div
                key={row.id}
                style={{
                  borderTop: "1px dashed var(--border)",
                  borderLeft: active ? "2px solid var(--accent)" : "2px solid transparent",
                  background: active ? "var(--accent-glow)" : "transparent",
                  padding: "5px 0 0 5px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                  <div style={{ minWidth: 0, fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {row.title}
                  </div>
                  <BoardPill tone={row.severity === "high" ? "bad" : row.severity === "medium" ? "warn" : "neutral"}>{row.severity}</BoardPill>
                </div>
                <div style={{ marginTop: 2, fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.target}</div>
                <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.action}</div>
                {canManage && (row.actions || []).length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 5 }}>
                    {(row.actions || []).map((action) => {
                      const busy = actionBusy === `${row.id}:${action.id}`;
                      return (
                        <button
                          key={action.id}
                          type="button"
                          onClick={() => onAction(row, action)}
                          disabled={busy}
                          style={boardActionStyle(action.tone)}
                        >
                          {busy ? "처리 중" : action.label}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


function WorkflowRunbookPanel({ days, focusIntent }) {
  const [open, setOpen] = useState(false);
  const [focusTag, setFocusTag] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [issueFilter, setIssueFilter] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [bootstrapBusy, setBootstrapBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState("");
  const [actionResult, setActionResult] = useState(null);
  const [focusNonce, setFocusNonce] = useState(0);
  const [err, setErr] = useState("");

  async function loadRunbook() {
    setLoading(true);
    setErr("");
    try {
      const qs = new URLSearchParams({ days: String(days), limit: "40" });
      if (focusTag) qs.set("focus_tag", focusTag);
      if (statusFilter) qs.set("status", statusFilter);
      if (issueFilter) qs.set("issue", issueFilter);
      const out = await sf(`/api/ai-hub/workflow-runbook?${qs.toString()}`);
      setData(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function runAction(row, action) {
    if (!action?.endpoint || action.method !== "POST") return;
    const key = `${row.key}:${action.id}`;
    setActionBusy(key);
    setErr("");
    setActionResult(null);
    try {
      const out = await postJson(action.endpoint, action.body || {});
      setActionResult({ key: row.key, payload: out });
      await loadRunbook();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setActionBusy("");
    }
  }

  async function runBootstrap(action) {
    if (!data?.is_admin || !action?.endpoint || action.method !== "POST") return;
    setBootstrapBusy(true);
    setErr("");
    setActionResult(null);
    try {
      await postJson(action.endpoint, action.body || {});
      await loadRunbook();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBootstrapBusy(false);
    }
  }

  useEffect(() => {
    if (!focusIntent?.nonce) return;
    setOpen(true);
    setIssueFilter(focusIntent.issue || "");
    setFocusNonce(focusIntent.nonce || 0);
  }, [focusIntent?.nonce]);

  useEffect(() => { if (open) loadRunbook(); }, [open, days, focusTag, statusFilter, issueFilter, focusNonce]);

  const counts = data?.counts || {};
  const rows = data?.items || [];
  const topTags = data?.top_tags || [];
  const issueOptions = data?.issue_options || [];
  const nextActionQueue = data?.next_action_queue || [];
  const runbookActions = data?.is_admin ? (data?.actions || []) : [];
  const focusLabel = focusIntent?.nonce ? (focusIntent.title || focusIntent.issue || "Workflow Runbook") : "";
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>Workflow Runbook</div>
        {focusLabel && <BoardPill tone="info">focus {focusLabel}</BoardPill>}
        {data && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            <BoardPill tone="neutral">표시 {counts.workflows || 0}/{counts.workflows_total ?? counts.workflow_templates_total ?? 0}</BoardPill>
            <BoardPill tone="ok">ready {counts.ready || 0}</BoardPill>
            <BoardPill tone="warn">attention {counts.attention || 0}</BoardPill>
            <BoardPill tone="bad">blocked {counts.blocked || 0}</BoardPill>
            <BoardPill tone="neutral">조치 {counts.next_actions || 0}</BoardPill>
          </div>
        )}
        <div style={{ flex: 1 }} />
        {open && (
          <>
            <select
              value={focusTag}
              onChange={(e) => setFocusTag(e.target.value)}
              style={{ ...selectStyle, width: 170, marginBottom: 0, padding: "4px 8px" }}
            >
              <option value="">전체 태그</option>
              {topTags.map((row) => (
                <option key={row.tag} value={row.tag}>{row.tag} ({row.count})</option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              style={{ ...selectStyle, width: 132, marginBottom: 0, padding: "4px 8px" }}
            >
              <option value="">전체 상태</option>
              <option value="ready">ready</option>
              <option value="attention">attention</option>
              <option value="blocked">blocked</option>
            </select>
            <select
              value={issueFilter}
              onChange={(e) => setIssueFilter(e.target.value)}
              style={{ ...selectStyle, width: 180, marginBottom: 0, padding: "4px 8px" }}
            >
              <option value="">전체 issue</option>
              {issueOptions.map((row) => (
                <option key={row.key} value={row.key}>{row.label} ({row.count})</option>
              ))}
            </select>
            {runbookActions.map((action) => (
              <button key={action.id} onClick={() => runBootstrap(action)} disabled={bootstrapBusy} style={boardActionStyle(action.tone)}>
                {bootstrapBusy ? "생성 중" : action.label}
              </button>
            ))}
            <button onClick={loadRunbook} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>
          </>
        )}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!data && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : data ? (
            <>
              {nextActionQueue.length > 0 && (
                <WorkflowNextActionQueue
                  rows={nextActionQueue}
                  activeIssue={issueFilter}
                  onFilter={(key) => setIssueFilter(key)}
                />
              )}
              <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, overflowX: "auto" }}>
                <div style={{ minWidth: 920, display: "grid", gridTemplateColumns: "110px minmax(180px, 1.4fr) 84px 1fr 120px 1.2fr 84px", gap: 0, padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 10, fontWeight: 800, color: "var(--text-secondary)", textTransform: "uppercase" }}>
                  <div>상태</div>
                  <div>Workflow</div>
                  <div>Step</div>
                  <div>Tools</div>
                  <div>검증</div>
                  <div>Issues</div>
                  <div>Action</div>
                </div>
                <div style={{ minWidth: 920, maxHeight: 280, overflowY: "auto" }}>
                  {rows.length === 0 ? (
                    <div style={{ padding: 12, fontSize: 11, color: "var(--text-secondary)" }}>등록된 workflow template 없음</div>
                  ) : rows.map((row) => (
                    <WorkflowRunbookRow
                      key={row.key}
                      row={row}
                      actionBusy={actionBusy}
                      actionResult={actionResult}
                      onAction={runAction}
                    />
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>Runbook을 열면 workflow별 준비 상태를 불러옵니다.</div>
          )}
        </div>
      )}
    </div>
  );
}


function WorkflowNextActionQueue({ rows, activeIssue, onFilter }) {
  return (
    <div style={{ marginBottom: 8, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 6 }}>
      {rows.slice(0, 6).map((row) => {
        const workflows = row.workflows || [];
        const active = activeIssue === row.key;
        return (
          <button
            key={row.key}
            type="button"
            onClick={() => onFilter(active ? "" : row.key)}
            style={{
              textAlign: "left",
              border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
              background: active ? "var(--accent-glow)" : "var(--bg-card)",
              borderRadius: 4,
              padding: "7px 8px",
              cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
              <BoardPill tone={row.tone}>{row.count || 0}</BoardPill>
              <div style={{ fontSize: 11, fontWeight: 850, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.title || row.key}</div>
            </div>
            <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {row.detail || row.route || "-"}
            </div>
            <div style={{ marginTop: 4, display: "flex", flexWrap: "wrap", gap: 4 }}>
              {workflows.slice(0, 3).map((item) => <Tag key={item.key}>{item.title || item.key}</Tag>)}
              {(row.count || 0) > workflows.slice(0, 3).length && <Tag>+{(row.count || 0) - workflows.slice(0, 3).length}</Tag>}
            </div>
          </button>
        );
      })}
    </div>
  );
}


function WorkflowRunbookRow({ row, actionBusy, actionResult, onAction }) {
  const issues = row.issues || [];
  const nextActions = row.next_actions || [];
  const nextAction = nextActions[0] || null;
  return (
    <div style={{ borderBottom: "1px dashed var(--border)" }}>
      <div style={{ display: "grid", gridTemplateColumns: "110px minmax(180px, 1.4fr) 84px 1fr 120px 1.2fr 84px", gap: 0, padding: "7px 8px", alignItems: "center", fontSize: 11 }}>
        <div><BoardPill tone={row.tone}>{row.status}</BoardPill></div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 850, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.title || row.key}</div>
          <div style={{ marginTop: 2, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {row.shared ? "shared" : "personal"}{row.owner ? ` · ${row.owner}` : ""} · {row.key}
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          <Tag>{row.step_count || 0}</Tag>
          <Tag>{row.evidence_count || 0} refs</Tag>
        </div>
        <div style={{ minWidth: 0, display: "flex", flexWrap: "wrap", gap: 4 }}>
          {(row.tool_names || []).slice(0, 5).map((name) => <Tag key={name}>{name}</Tag>)}
          {(row.tool_names || []).length > 5 && <Tag>+{row.tool_names.length - 5}</Tag>}
        </div>
        <div style={{ minWidth: 0, color: "var(--text-secondary)" }}>
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.last_status || "미검증"}</div>
          <div style={{ color: "var(--muted)", fontSize: 10 }}>{row.run_count || 0} checks</div>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {issues.length === 0 ? <BoardPill tone="ok">ready</BoardPill> : issues.slice(0, 4).map((issue) => (
              <BoardPill key={issue.key} tone={issue.tone}>{issue.label}</BoardPill>
            ))}
          </div>
          {nextAction && (
            <div style={{ marginTop: 4, color: "var(--muted)", fontSize: 10, lineHeight: 1.3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              다음: {nextAction.title}{nextAction.detail ? ` · ${nextAction.detail}` : ""}{nextActions.length > 1 ? ` 외 ${nextActions.length - 1}` : ""}
            </div>
          )}
        </div>
        <div>
          {(row.actions || []).slice(0, 1).map((action) => {
            const busy = actionBusy === `${row.key}:${action.id}`;
            return (
              <button key={action.id} type="button" onClick={() => onAction(row, action)} disabled={busy} style={btnGhost}>
                {busy ? "실행 중" : action.label}
              </button>
            );
          })}
        </div>
      </div>
      {actionResult?.key === row.key && (
        <div style={{ padding: "0 8px 8px 118px" }}>
          <WorkflowActionResult payload={actionResult.payload} />
        </div>
      )}
    </div>
  );
}


function WorkflowMapPanel({ days, focusIntent, onWarningSelect }) {
  const [open, setOpen] = useState(false);
  const [focusTag, setFocusTag] = useState("");
  const [map, setMap] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState("");
  const [nodeActionBusy, setNodeActionBusy] = useState("");
  const [nodeActionResult, setNodeActionResult] = useState(null);
  const [focusNonce, setFocusNonce] = useState(0);
  const [err, setErr] = useState("");

  async function loadMap() {
    setLoading(true);
    setErr("");
    try {
      const qs = new URLSearchParams({
        days: String(days),
        limit: "40",
        reference_limit: "160",
      });
      if (focusTag) qs.set("focus_tag", focusTag);
      const out = await sf(`/api/ai-hub/workflow-map?${qs.toString()}`);
      setMap(out);
      if (selectedId && !(out.nodes || []).some((node) => node.id === selectedId)) {
        setSelectedId("");
      }
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function exportMap(format) {
    setExporting(format);
    setErr("");
    try {
      const qs = new URLSearchParams({
        format: format === "obsidian_zip" ? "obsidian" : format,
        days: String(days),
        limit: "40",
        reference_limit: "160",
      });
      if (focusTag) qs.set("focus_tag", focusTag);
      if (format === "obsidian_zip") {
        await dl(`/api/ai-hub/workflow-map/export/download?${qs.toString()}`, "flow-ai-hub-workflow-map.obsidian.zip");
        return;
      }
      const out = await sf(`/api/ai-hub/workflow-map/export?${qs.toString()}`);
      downloadJson(out.filename || `flow-ai-hub-workflow-map.${format}.json`, out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setExporting("");
    }
  }

  async function runNodeAction(node, action) {
    if (!node?.id || !action?.endpoint || action.method !== "POST") return;
    const key = `${node.id}:${action.id}`;
    setNodeActionBusy(key);
    setErr("");
    try {
      const out = await postJson(action.endpoint, action.body || {});
      setNodeActionResult({ nodeId: node.id, actionId: action.id, payload: out });
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setNodeActionBusy("");
    }
  }

  useEffect(() => {
    if (!focusIntent?.nonce) return;
    setOpen(true);
    setFocusNonce(focusIntent.nonce || 0);
  }, [focusIntent?.nonce]);

  useEffect(() => { if (open) loadMap(); }, [open, days, focusTag, focusNonce]);
  useEffect(() => { setNodeActionResult(null); }, [selectedId]);

  const nodes = map?.nodes || [];
  const edges = map?.edges || [];
  const selected = nodes.find((node) => node.id === selectedId) || null;
  const topTags = map?.top_tags || [];
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>워크플로우 지도</div>
        {focusIntent?.nonce && <BoardPill tone="info">focus {focusIntent.title || "워크플로우 지도"}</BoardPill>}
        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
          Prompt/Workflow → Policy → Unit/Function → Wiki/Schema → Improve
        </div>
        <div style={{ flex: 1 }} />
        {open && (
          <>
            <select
              value={focusTag}
              onChange={(e) => setFocusTag(e.target.value)}
              style={{ ...selectStyle, width: 170, marginBottom: 0, padding: "4px 8px" }}
            >
              <option value="">전체 태그</option>
              {topTags.map((row) => (
                <option key={row.tag} value={row.tag}>{row.tag} ({row.count})</option>
              ))}
            </select>
            <button onClick={loadMap} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>
            <button onClick={() => exportMap("n8n")} disabled={!!exporting} style={btnGhost}>
              {exporting === "n8n" ? "내보내는 중" : "n8n JSON"}
            </button>
            <button onClick={() => exportMap("obsidian_zip")} disabled={!!exporting} style={btnGhost}>
              {exporting === "obsidian_zip" ? "내보내는 중" : "Obsidian ZIP"}
            </button>
          </>
        )}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!map && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : map ? (
            <>
              <WorkflowMapSummary map={map} />
              <WorkflowMapWarnings rows={map.warnings || []} focusedKey={focusIntent?.warning || ""} onSelect={onWarningSelect} />
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 260px", gap: 8, alignItems: "stretch" }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(130px, 1fr))", gap: 6, overflowX: "auto" }}>
                  {(map.stages || []).map((stage) => (
                    <WorkflowStageColumn
                      key={stage.id}
                      stage={stage}
                      nodes={nodes.filter((node) => node.stage === stage.id)}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                    />
                  ))}
                </div>
                <WorkflowNodeDetail
                  node={selected}
                  edges={edges}
                  nodes={nodes}
                  onAction={runNodeAction}
                  actionBusy={nodeActionBusy}
                  actionResult={nodeActionResult}
                />
              </div>
            </>
          ) : (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>지도를 열면 현재 도구/지식 관계를 불러옵니다.</div>
          )}
        </div>
      )}
    </div>
  );
}


function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}


function WorkflowMapSummary({ map }) {
  const counts = map.counts || {};
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 6 }}>
      <BoardPill tone="info">도구 {counts.tools_visible}/{counts.tools_total}</BoardPill>
      <BoardPill tone="info">워크플로우 {counts.workflow_templates_visible || 0}</BoardPill>
      <BoardPill tone={counts.tools_disabled_visible ? "bad" : "ok"}>비활성 {counts.tools_disabled_visible || 0}</BoardPill>
      <BoardPill tone={counts.tools_without_refs_visible ? "warn" : "ok"}>근거 없음 {counts.tools_without_refs_visible || 0}</BoardPill>
      <BoardPill tone={counts.deep_eval_failed ? "bad" : counts.deep_eval_total ? "ok" : "warn"}>DeepEval {counts.deep_eval_total || 0} · fail {counts.deep_eval_failed || 0}</BoardPill>
      <BoardPill tone="neutral">노드 {counts.nodes || 0}</BoardPill>
      <BoardPill tone="neutral">엣지 {counts.edges || 0}</BoardPill>
      {map.focus_tag && <BoardPill tone="info">focus {map.focus_tag}</BoardPill>}
    </div>
  );
}


function WorkflowMapWarnings({ rows, focusedKey, onSelect }) {
  const visible = rows.slice(0, 8);
  if (!visible.length) return null;
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, marginBottom: 8 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 6 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)" }}>경고 큐</div>
        <BoardPill tone="warn">{rows.length}</BoardPill>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 6 }}>
        {visible.map((row) => {
          const focused = focusedKey && focusedKey === row.key;
          const items = row.items || [];
          return (
            <div
              key={row.key || row.message}
              style={{
                border: focused ? "1px solid var(--accent)" : "1px solid var(--border)",
                background: focused ? "var(--bg-secondary)" : "transparent",
                borderRadius: 4,
                padding: 7,
                minWidth: 0,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.key || "warning"}</div>
                <BoardPill tone={row.tone || "neutral"}>{items.length}</BoardPill>
              </div>
              <div style={{ marginTop: 3, fontSize: 10, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{row.message}</div>
              {row.action && (
                <div style={{ marginTop: 3, fontSize: 10, color: "var(--text-primary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                  조치 {row.action}
                </div>
              )}
              {onSelect && (
                <button
                  type="button"
                  onClick={() => onSelect({ ...row, title: row.key || "workflow map warning" })}
                  style={{ ...btnGhost, marginTop: 5, padding: "3px 7px" }}
                  title="조치 대상 패널 열기"
                >
                  조치 보기
                </button>
              )}
              <div style={{ marginTop: 4, display: "flex", flexWrap: "wrap", gap: 4 }}>
                {items.slice(0, 4).map((item) => <Tag key={item}>{item}</Tag>)}
                {items.length > 4 && <Tag>+{items.length - 4}</Tag>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


function WorkflowStageColumn({ stage, nodes, selectedId, onSelect }) {
  const stageNode = nodes.find((node) => node.type === "stage");
  const childNodes = nodes.filter((node) => node.type !== "stage");
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, minWidth: 130, padding: 7 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: "var(--text-primary)", marginBottom: 2 }}>
        {stage.title}
      </div>
      <div style={{ fontSize: 10, color: "var(--muted)", lineHeight: 1.35, minHeight: 28, marginBottom: 6 }}>
        {stage.detail}
      </div>
      {stageNode && (
        <WorkflowNodeButton node={stageNode} selected={selectedId === stageNode.id} onSelect={onSelect} />
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 270, overflowY: "auto", marginTop: 5 }}>
        {childNodes.map((node) => (
          <WorkflowNodeButton key={node.id} node={node} selected={selectedId === node.id} onSelect={onSelect} />
        ))}
        {childNodes.length === 0 && (
          <div style={{ fontSize: 10, color: "var(--muted)", padding: "6px 0" }}>연결 항목 없음</div>
        )}
      </div>
    </div>
  );
}


function WorkflowNodeButton({ node, selected, onSelect }) {
  const toneColor = node.tone === "bad" ? "var(--danger)" : node.tone === "ok" ? "var(--ok)" : node.tone === "warn" ? "var(--warn)" : node.tone === "info" ? "var(--info)" : "var(--text-secondary)";
  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      style={{
        width: "100%",
        textAlign: "left",
        border: "1px solid " + (selected ? "var(--accent)" : "var(--border)"),
        background: selected ? "var(--accent-glow)" : "var(--bg-primary)",
        color: "var(--text-primary)",
        borderRadius: 4,
        padding: "5px 6px",
        cursor: "pointer",
        opacity: node.enabled === false ? 0.62 : 1,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 5, alignItems: "center" }}>
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11, fontWeight: 750 }}>
          {node.label}
        </span>
        <span style={{ color: toneColor, fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>{node.type}</span>
      </div>
      {node.type === "tool" && (
        <div style={{ marginTop: 2, fontSize: 9, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {node.kind} · {(node.metrics?.count || 0)} calls
        </div>
      )}
      {node.type === "workflow" && (
        <div style={{ marginTop: 2, fontSize: 9, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {(node.shared ? "shared" : "personal")} · {(node.metrics?.steps || 0)} steps{node.metrics?.last_status ? ` · ${node.metrics.last_status}` : ""}
        </div>
      )}
      {node.type === "deep_eval" && (
        <div style={{ marginTop: 2, fontSize: 9, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {node.metrics?.status || "missing"} · {(node.metrics?.passed || 0)}/{(node.metrics?.total || 0)} passed
        </div>
      )}
    </button>
  );
}


function WorkflowNodeDetail({ node, edges, nodes, onAction, actionBusy, actionResult }) {
  if (!node) {
    return (
      <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5 }}>
        노드를 선택하면 연결된 입력/출력 엣지와 관리 근거를 확인할 수 있습니다.
      </div>
    );
  }
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const incoming = edges.filter((edge) => edge.to === node.id);
  const outgoing = edges.filter((edge) => edge.from === node.id);
  const nodeActions = node.type === "workflow" ? [] : (node.actions || []);
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, fontSize: 11, color: "var(--text-primary)", minWidth: 0 }}>
      <div style={{ fontSize: 12, fontWeight: 850, marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.label}</div>
      <div style={{ color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", wordBreak: "break-all", marginBottom: 6 }}>{node.id}</div>
      <div style={{ color: "var(--text-secondary)", lineHeight: 1.45, marginBottom: 8 }}>{node.detail}</div>
      {node.type === "tool" && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
          <Tag>{node.kind}</Tag>
          <Tag>{node.enabled ? "enabled" : "disabled"}</Tag>
          <Tag>{node.metrics?.count || 0} calls</Tag>
          <Tag>{node.metrics?.users || 0} users</Tag>
        </div>
      )}
      {node.type === "workflow" && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
            <Tag>{node.shared ? "shared" : "personal"}</Tag>
            <Tag>{node.metrics?.steps || 0} steps</Tag>
            <Tag>{node.metrics?.run_count || 0} checks</Tag>
            {node.metrics?.last_status && <Tag>{node.metrics.last_status}</Tag>}
            {node.owner && <Tag>{node.owner}</Tag>}
          </div>
          {(node.actions || []).length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
              {(node.actions || []).map((action) => {
                const busy = actionBusy === `${node.id}:${action.id}`;
                return (
                  <button
                    key={action.id}
                    type="button"
                    onClick={() => onAction && onAction(node, action)}
                    disabled={busy}
                    style={btnGhost}
                  >
                    {busy ? "실행 중" : action.label}
                  </button>
                );
              })}
            </div>
          )}
          {actionResult?.nodeId === node.id && <WorkflowActionResult payload={actionResult.payload} />}
          {(node.steps || []).length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 3, marginBottom: 8 }}>
              {(node.steps || []).slice(0, 8).map((step) => (
                <div key={`${node.id}:${step.index}`} style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 5, alignItems: "center", color: "var(--text-secondary)" }}>
                  <Tag>{(step.index || 0) + 1}</Tag>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "JetBrains Mono, monospace" }}>
                    {step.unit_ai}.{step.action || "step"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {node.type === "deep_eval" && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
          <Tag>{node.metrics?.status || "missing"}</Tag>
          <Tag>{node.metrics?.passed || 0}/{node.metrics?.total || 0} passed</Tag>
          <Tag>{node.metrics?.failed || 0} failed</Tag>
          {node.metrics?.path && <Tag>{node.metrics.path}</Tag>}
        </div>
      )}
      {nodeActions.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
          {nodeActions.map((action) => {
            const busy = actionBusy === `${node.id}:${action.id}`;
            return (
              <button
                key={action.id}
                type="button"
                onClick={() => onAction && onAction(node, action)}
                disabled={busy}
                style={btnGhost}
              >
                {busy ? "실행 중" : action.label}
              </button>
            );
          })}
        </div>
      )}
      {actionResult?.nodeId === node.id && node.type !== "workflow" && <WorkflowActionResult payload={actionResult.payload} />}
      {(node.tags || []).length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
          {(node.tags || []).slice(0, 10).map((tag) => <Tag key={tag}>{tag}</Tag>)}
        </div>
      )}
      <WorkflowEdgeList title="입력" edges={incoming} other={(edge) => byId.get(edge.from)} />
      <WorkflowEdgeList title="출력" edges={outgoing} other={(edge) => byId.get(edge.to)} />
    </div>
  );
}


function WorkflowActionResult({ payload }) {
  if (payload?.report?.summary || payload?.summary) {
    const report = payload.report || payload;
    const summary = report.summary || {};
    return (
      <div style={{ border: "1px solid var(--border)", background: "var(--bg-primary)", borderRadius: 4, padding: 6, marginBottom: 8 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          <Tag>{report.status || payload.status || "updated"}</Tag>
          <Tag>{summary.passed || 0}/{summary.total || 0} passed</Tag>
          <Tag>{summary.failed || 0} failed</Tag>
        </div>
      </div>
    );
  }
  const execution = payload?.execution || {};
  const steps = Array.isArray(execution.steps) ? execution.steps : [];
  const statuses = [...new Set(steps.map((step) => step.status).filter(Boolean))];
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-primary)", borderRadius: 4, padding: 6, marginBottom: 8 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 5 }}>
        <Tag>{execution.dry_run ? "dry-run" : "execute"}</Tag>
        <Tag>{steps.length} steps</Tag>
        <Tag>{execution.confirm_required ? "confirm" : "guarded"}</Tag>
      </div>
      {statuses.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {statuses.slice(0, 6).map((status) => <Tag key={status}>{status}</Tag>)}
        </div>
      )}
    </div>
  );
}


function WorkflowEdgeList({ title, edges, other }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontSize: 10, fontWeight: 800, color: "var(--text-secondary)", textTransform: "uppercase", marginBottom: 3 }}>{title}</div>
      {edges.length === 0 ? (
        <div style={{ fontSize: 10, color: "var(--muted)" }}>없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {edges.slice(0, 12).map((edge, i) => {
            const node = other(edge) || {};
            return (
              <div key={`${edge.from}:${edge.to}:${i}`} style={{ display: "grid", gridTemplateColumns: "54px 1fr", gap: 5, alignItems: "center" }}>
                <Tag>{edge.label || edge.kind}</Tag>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-secondary)" }}>{node.label || edge.from || edge.to}</span>
              </div>
            );
          })}
          {edges.length > 12 && <div style={{ fontSize: 10, color: "var(--muted)" }}>+{edges.length - 12} more</div>}
        </div>
      )}
    </div>
  );
}


function OperationsBoard({ days, onChanged }) {
  const [open, setOpen] = useState(true);
  const [board, setBoard] = useState(null);
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState("");
  const [err, setErr] = useState("");

  async function loadBoard() {
    setLoading(true);
    setErr("");
    try {
      const out = await sf(`/api/ai-hub/board?days=${days}&limit=6`);
      setBoard(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function runAction(item, action) {
    if (!action?.endpoint || action.method !== "POST") return;
    if (action.confirm && !confirm(`${action.label} 처리할까요? ${item.title || item.id}`)) return;
    const key = `${item.id}:${action.id}`;
    setActionBusy(key);
    setErr("");
    try {
      await postJson(action.endpoint, action.body || {});
      await loadBoard();
      if (onChanged) await onChanged();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setActionBusy("");
    }
  }

  useEffect(() => { if (open) loadBoard(); }, [open, days]);

  const health = board?.health || [];
  const lanes = board?.lanes || [];
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>운영 보드</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {health.map((h) => <BoardPill key={h.key} tone={h.tone}>{h.label} {h.value}</BoardPill>)}
        </div>
        <div style={{ flex: 1 }} />
        {open && <button onClick={loadBoard} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!board && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 8 }}>
              {lanes.map((lane) => (
                <BoardLane
                  key={lane.id}
                  lane={lane}
                  canManage={!!board?.is_admin}
                  actionBusy={actionBusy}
                  onAction={runAction}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function BoardLane({ lane, canManage, actionBusy, onAction }) {
  const items = lane.items || [];
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, minWidth: 0, padding: 8 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 6 }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: "var(--text-primary)" }}>{lane.title}</div>
        <BoardPill tone={lane.tone}>{lane.count}</BoardPill>
      </div>
      <div style={{ fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", marginBottom: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {lane.target}
      </div>
      {items.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "8px 0" }}>대기 항목 없음</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 170, overflowY: "auto" }}>
          {items.map((item) => (
            <div key={item.id} style={{ minWidth: 0, borderTop: "1px dashed var(--border)", paddingTop: 6 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                <div style={{ minWidth: 0, fontSize: 12, fontWeight: 700, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {item.title || item.id}
                </div>
                <Tag>{item.status}</Tag>
              </div>
              <div style={{ marginTop: 2, fontSize: 10, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {item.meta || relTime(item.updated_at)}
              </div>
              {item.detail && (
                <div style={{ marginTop: 2, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                  {item.detail}
                </div>
              )}
              {canManage && (item.actions || []).length > 0 && (
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 5 }}>
                  {(item.actions || []).map((action) => {
                    const busy = actionBusy === `${item.id}:${action.id}`;
                    return (
                      <button
                        key={action.id}
                        type="button"
                        onClick={() => onAction(item, action)}
                        disabled={busy}
                        style={boardActionStyle(action.tone)}
                      >
                        {busy ? "처리 중" : action.label}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function boardActionStyle(tone) {
  const color = tone === "bad" ? "var(--danger)" : tone === "ok" ? "var(--ok)" : "var(--text-secondary)";
  const bg = tone === "bad" ? "var(--danger-50)" : tone === "ok" ? "var(--ok-50)" : "var(--bg-primary)";
  return {
    border: "1px solid var(--border)",
    background: bg,
    color,
    borderRadius: 3,
    fontSize: 10,
    fontWeight: 800,
    padding: "2px 7px",
    cursor: "pointer",
  };
}


function TimelinePanel({ days, focusIntent }) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState("");
  const [focusNonce, setFocusNonce] = useState(0);
  const [err, setErr] = useState("");

  async function loadTimeline() {
    setLoading(true);
    setErr("");
    try {
      const qs = new URLSearchParams({ days: String(days), limit: "30" });
      if (category) qs.set("category", category);
      const out = await sf(`/api/ai-hub/timeline?${qs.toString()}`);
      setData(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function exportOps(format) {
    setExporting(format);
    setErr("");
    try {
      const qs = new URLSearchParams({
        format,
        days: String(days),
        limit: "40",
        reference_limit: "160",
      });
      const filename = format === "n8n" ? "flow-ai-hub-operations.n8n.json" : "flow-ai-hub-operations.obsidian.zip";
      await dl(`/api/ai-hub/ops-export/download?${qs.toString()}`, filename);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setExporting("");
    }
  }

  useEffect(() => {
    if (!focusIntent?.nonce) return;
    setOpen(true);
    setCategory(focusIntent.category || "");
    setFocusNonce(focusIntent.nonce || 0);
  }, [focusIntent?.nonce]);

  useEffect(() => { if (open) loadTimeline(); }, [open, days, category, focusNonce]);

  const items = data?.items || [];
  const counts = data?.counts || {};
  const categories = ["workflow", "wiki", "semantic", "validation", "tool", "skill"];
  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>운영 타임라인</div>
        {focusIntent?.nonce && <BoardPill tone="info">focus {focusIntent.title || "운영 타임라인"}</BoardPill>}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {Object.entries(counts).slice(0, 5).map(([key, count]) => <BoardPill key={key} tone="neutral">{key} {count}</BoardPill>)}
        </div>
        <div style={{ flex: 1 }} />
        {open && (
          <>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              style={{ ...selectStyle, width: 150, marginBottom: 0, padding: "4px 8px" }}
            >
              <option value="">전체 이벤트</option>
              {categories.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <button onClick={() => exportOps("obsidian")} disabled={!!exporting} style={btnGhost}>{exporting === "obsidian" ? "내보내는 중" : "운영 ZIP"}</button>
            <button onClick={() => exportOps("n8n")} disabled={!!exporting} style={btnGhost}>{exporting === "n8n" ? "내보내는 중" : "운영 n8n"}</button>
            <button onClick={loadTimeline} disabled={loading} style={btnGhost}>{loading ? "갱신 중..." : "새로고침"}</button>
          </>
        )}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginBottom: 6 }}>{err}</div>}
          {!data && loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중...</div>
          ) : items.length === 0 ? (
            <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, fontSize: 11, color: "var(--text-secondary)" }}>
              최근 운영 이벤트 없음
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 6, maxHeight: 220, overflowY: "auto" }}>
              {items.map((item) => <TimelineItem key={item.id} item={item} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function TimelineItem({ item }) {
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-card)", borderRadius: 4, padding: 8, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <div style={{ minWidth: 0, fontSize: 12, fontWeight: 800, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.title || item.action}
        </div>
        <BoardPill tone={item.tone || "neutral"}>{item.category}</BoardPill>
      </div>
      <div style={{ marginTop: 3, display: "flex", flexWrap: "wrap", gap: 4 }}>
        <Tag>{item.username || "unknown"}</Tag>
        <Tag>{relTime(item.timestamp)}</Tag>
        {item.meta && <Tag>{item.meta}</Tag>}
      </div>
      {item.detail && (
        <div style={{ marginTop: 4, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.35, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {item.detail}
        </div>
      )}
      <div style={{ marginTop: 4, fontSize: 10, color: "var(--muted)", fontFamily: "JetBrains Mono, monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {item.action}
      </div>
    </div>
  );
}


function BoardPill({ tone, children }) {
  const colors = {
    ok: ["rgba(22,163,74,0.14)", "var(--ok)"],
    warn: ["rgba(245,158,11,0.16)", "var(--warn)"],
    bad: ["rgba(239,68,68,0.14)", "var(--danger)"],
    info: ["rgba(59,130,246,0.14)", "var(--info)"],
    neutral: ["var(--bg-primary)", "var(--text-secondary)"],
  };
  const [bg, color] = colors[tone] || colors.neutral;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", padding: "1px 7px", borderRadius: 999, border: "1px solid var(--border)", background: bg, color, fontSize: 10, fontWeight: 800 }}>
      {children}
    </span>
  );
}


function OrchestratorPanel() {
  const [open, setOpen] = useState(true);
  const [prompt, setPrompt] = useState("PROD_A 의 root_lot 별 ET 평균");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState("");

  async function run() {
    setRunning(true);
    setErr("");
    setResult(null);
    try {
      const out = await postJson("/api/home-agent/orchestrate", { prompt, top_k: 2 });
      setResult(out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>🎯 홈 에이전트</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
          자연어 prompt → 적합한 단위 AI 자동 선택 + 실행 트레이스
        </div>
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              type="text"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(); } }}
              placeholder="예: lot SQL JOIN, ET 차트, 인폼 메일, 회의 일정…"
              style={{ ...inputStyle, flex: 1, marginBottom: 0 }}
            />
            <button onClick={run} disabled={running || !prompt.trim()} style={btnPrimary}>
              {running ? "분석 중…" : "▶ 도구 선택"}
            </button>
          </div>
          {err && <div style={{ color: "var(--danger)", fontSize: 11, marginTop: 6 }}>{err}</div>}
          {result && (
            <div style={{ marginTop: 8, padding: 8, background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 4 }}>
              <div style={{ fontSize: 12, color: "var(--text-primary)", marginBottom: 6 }}>
                {result.reply}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>
                매칭 키워드: {(result.meta?.matched_terms || []).join(", ") || "(없음)"} · 후보 {result.meta?.candidate_count ?? 0}개
              </div>
              {(result.trace || []).length > 0 && (
                <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 11, marginTop: 6 }}>
                  <thead>
                    <tr style={{ color: "var(--text-secondary)" }}>
                      <th style={traceTh}>도구</th>
                      <th style={traceTh}>kind</th>
                      <th style={traceTh}>신뢰도</th>
                      <th style={traceTh}>ok</th>
                      <th style={traceTh}>ms</th>
                      <th style={traceTh}>결과 미리보기</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trace.map((tr, i) => (
                      <tr key={i}>
                        <td style={traceTd}>{tr.title} <span style={{ color: "var(--muted)", fontFamily: "JetBrains Mono, monospace" }}>({tr.tool})</span></td>
                        <td style={traceTd}>{tr.kind}</td>
                        <td style={traceTd}>{(tr.confidence * 100).toFixed(0)}%</td>
                        <td style={{ ...traceTd, color: tr.ok ? "var(--ok)" : "var(--muted)" }}>{tr.ok ? "✓" : "—"}</td>
                        <td style={traceTd}>{tr.ms}</td>
                        <td style={{ ...traceTd, maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{tr.result_preview}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const traceTh = { textAlign: "left", padding: "3px 6px", fontWeight: 700, borderBottom: "1px solid var(--border)" };
const traceTd = { padding: "3px 6px", borderBottom: "1px dashed var(--border)", color: "var(--text-primary)" };
const btnPrimary = {
  padding: "5px 12px", fontSize: 12, fontWeight: 700,
  border: "1px solid var(--accent)", borderRadius: 4,
  background: "var(--accent)", color: "#fff", cursor: "pointer",
};


function SkillsPanel() {
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState([]);
  const [skills, setSkills] = useState([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(false);
  const [mining, setMining] = useState(false);
  const [err, setErr] = useState("");

  async function loadAll() {
    setLoading(true);
    setErr("");
    try {
      const [cs, sk] = await Promise.all([
        sf("/api/skills/candidates"),
        sf("/api/skills/list"),
      ]);
      setCandidates(cs.items || []);
      setSkills(sk.items || []);
      setIsAdmin(!!cs.is_admin);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { if (open) loadAll(); }, [open]);

  async function mineNow() {
    if (!isAdmin) return;
    setMining(true);
    setErr("");
    try {
      const out = await postJson("/api/skills/mine", { days: 30, window_sec: 300, min_freq: 3, min_users: 2 });
      alert(`스캔: ${out.scanned_events}건 · 후보: ${out.candidate_count}건`);
      await loadAll();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setMining(false);
    }
  }

  async function approve(c) {
    if (!isAdmin) return;
    const title = prompt(`정식 스킬 제목 (선택)`, c.title || c.key);
    if (title === null) return;
    try {
      await postJson(`/api/skills/candidates/${encodeURIComponent(c.key)}/approve`, { title });
      await loadAll();
    } catch (e) {
      alert(`승인 실패: ${e.message || e}`);
    }
  }
  async function reject(c) {
    if (!isAdmin) return;
    if (!confirm(`거부하시겠습니까? ${c.title}`)) return;
    try {
      await postJson(`/api/skills/candidates/${encodeURIComponent(c.key)}/reject`);
      await loadAll();
    } catch (e) {
      alert(`거부 실패: ${e.message || e}`);
    }
  }

  return (
    <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => setOpen((v) => !v)} style={{ ...btnGhost, padding: "3px 8px" }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ fontSize: 13, fontWeight: 800, color: "var(--text-primary)" }}>💡 스킬</div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
          누적 로그에서 자주 쓰인 도구 시퀀스를 스킬로 응축
        </div>
        <div style={{ flex: 1 }} />
        {open && isAdmin && (
          <button onClick={mineNow} disabled={mining} style={btnGhost}>
            {mining ? "마이닝 중…" : "🔍 즉시 마이닝"}
          </button>
        )}
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {err && <div style={{ color: "var(--danger)", fontSize: 11 }}>{err}</div>}
          {loading ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>로딩 중…</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <SkillColumn
                title={`후보 (${candidates.length})`}
                items={candidates}
                emptyHint="누적 로그가 부족합니다. 동일 시퀀스를 2명 이상이 3번 이상 실행하면 자동 등장합니다."
                renderActions={isAdmin ? (c) => (
                  <div style={{ display: "flex", gap: 4 }}>
                    <button onClick={() => approve(c)} style={btnGhost}>승인</button>
                    <button onClick={() => reject(c)} style={btnGhost}>거부</button>
                  </div>
                ) : null}
                showFreq
              />
              <SkillColumn
                title={`정식 스킬 (${skills.length})`}
                items={skills}
                emptyHint="아직 등록된 스킬이 없습니다. SQL 작업대에서 '스킬로 저장' 또는 후보 승인으로 추가됩니다."
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function SkillColumn({ title, items, emptyHint, renderActions, showFreq }) {
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 4 }}>{title}</div>
      {items.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--muted)", padding: 8, background: "var(--bg-card)", borderRadius: 4, lineHeight: 1.5 }}>{emptyHint}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 220, overflowY: "auto" }}>
          {items.map((c) => (
            <div key={c.key} style={{ background: "var(--bg-card)", padding: 8, border: "1px solid var(--border)", borderRadius: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: 12, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</div>
                  <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "JetBrains Mono, monospace" }}>{c.key}</div>
                </div>
                {renderActions && renderActions(c)}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4, lineHeight: 1.4, wordBreak: "break-all" }}>
                {c.description || (c.steps && c.steps.length ? c.steps.map(s => s.action || s.tool_name).join(" → ") : "")}
              </div>
              {showFreq && (
                <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                  freq {c.freq} · users {(c.users || []).length} · last {(c.last_seen || "").slice(0, 16)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function Card({ item, selected, onClick, isAdmin, onToggle }) {
  const dim = !item.enabled;
  return (
    <div
      onClick={onClick}
      style={{
        cursor: "pointer",
        border: "1px solid " + (selected ? "var(--accent)" : "var(--border)"),
        background: "var(--bg-card)",
        borderRadius: 6,
        padding: 12,
        opacity: dim ? 0.55 : 1,
        boxShadow: selected ? "0 0 0 1px var(--accent)" : "none",
        transition: "border-color 0.12s",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <KindBadge kind={item.kind} />
            <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {item.title}
            </div>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "JetBrains Mono, monospace",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {item.name}
          </div>
        </div>
        {isAdmin && (
          <label
            onClick={(e) => e.stopPropagation()}
            style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--text-secondary)" }}
          >
            <input
              type="checkbox"
              checked={!!item.enabled}
              onChange={(e) => onToggle(e.target.checked)}
            />
            on
          </label>
        )}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 8, minHeight: 32, lineHeight: 1.45 }}>
        {item.description || <span style={{ color: "var(--muted)" }}>(설명 없음)</span>}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
        {(item.tags || []).slice(0, 4).map((t) => <Tag key={t}>{t}</Tag>)}
        {(item.tags || []).length > 4 && <Tag>+{item.tags.length - 4}</Tag>}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, fontSize: 11, color: "var(--muted)" }}>
        <span>{item.count_30d ?? 0} 호출 · {item.user_count_30d ?? 0} 명</span>
        <span>{relTime(item.last_run)}</span>
      </div>
    </div>
  );
}


function Detail({ item, history, days }) {
  return (
    <div style={{ fontSize: 12, color: "var(--text-primary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
        <KindBadge kind={item.kind} />
        <div style={{ fontWeight: 800, fontSize: 14 }}>{item.title}</div>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", fontFamily: "JetBrains Mono, monospace", marginBottom: 12, wordBreak: "break-all" }}>
        {item.name}
      </div>
      <div style={{ marginBottom: 12, lineHeight: 1.5 }}>
        {item.description || <span style={{ color: "var(--muted)" }}>(설명 없음)</span>}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 16 }}>
        {(item.tags || []).map((t) => <Tag key={t}>{t}</Tag>)}
      </div>

      <Stat label="enabled" value={item.enabled ? "ON" : "OFF"} accent={item.enabled ? "var(--ok)" : "var(--muted)"} />
      <Stat label={`최근 ${days}일 호출`} value={item.count_30d ?? 0} />
      <Stat label={`최근 ${days}일 사용자`} value={item.user_count_30d ?? 0} />
      <Stat label="마지막 실행" value={relTime(item.last_run) || "-"} />

      <ManagementFlow flow={item.management_flow} />
      <KnowledgeRefs refs={item.knowledge_refs} />

      {item.kind === "function" && (
        <Section title="Input Schema">
          {(item.required_args && item.required_args.length > 0) ? (
            <div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>required:</div>
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {item.required_args.map((a) => <li key={a} style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11 }}>{a}</li>)}
              </ul>
            </div>
          ) : (
            <div style={{ color: "var(--muted)" }}>required 인자 없음</div>
          )}
          {item.few_shot && item.few_shot.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>예시:</div>
              {item.few_shot.slice(0, 2).map((s, i) => (
                <div key={i} style={{ background: "var(--bg-card)", padding: 6, borderRadius: 4, marginBottom: 4, fontSize: 11, lineHeight: 1.4 }}>
                  <div style={{ color: "var(--text-secondary)" }}>“{s.prompt}”</div>
                  <pre style={{ margin: "2px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-all", fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: "var(--text-primary)" }}>{JSON.stringify(s.arguments, null, 0)}</pre>
                </div>
              ))}
            </div>
          )}
        </Section>
      )}

      {item.kind === "unit_ai" && (
        <Section title={`Data Sources (${item.data_sources?.length || 0})`}>
          {(item.data_sources || []).length === 0 && <div style={{ color: "var(--muted)" }}>등록된 데이터 소스 없음</div>}
          {(item.data_sources || []).map((ds, i) => (
            <div key={i} style={{ background: "var(--bg-card)", padding: 6, borderRadius: 4, marginBottom: 4 }}>
              <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>{ds.kind}</div>
              <div style={{ fontSize: 11, fontFamily: "JetBrains Mono, monospace", color: "var(--text-primary)", wordBreak: "break-all" }}>{ds.path}</div>
              {ds.description && <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>{ds.description}</div>}
              {(ds.columns || []).length > 0 && (
                <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                  컬럼: {ds.columns.map((c) => c.name).join(", ")}
                </div>
              )}
            </div>
          ))}
        </Section>
      )}

      {item.kind === "unit_ai" && item.handler_ref && item.handler_ref.module && (
        <Section title="Handler">
          <div style={{ fontSize: 11, fontFamily: "JetBrains Mono, monospace", color: "var(--text-secondary)" }}>
            {item.handler_ref.module}.{item.handler_ref.function}
            {item.handler_ref.lineno ? `:${item.handler_ref.lineno}` : ""}
          </div>
          {item.handler_ref.description && (
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4, lineHeight: 1.4 }}>{item.handler_ref.description}</div>
          )}
        </Section>
      )}

      <Section title={`최근 호출 (${history.length})`}>
        {history.length === 0 && <div style={{ color: "var(--muted)" }}>호출 이력 없음</div>}
        {history.map((h, i) => (
          <div key={i} style={{ background: "var(--bg-card)", padding: 6, borderRadius: 4, marginBottom: 4, fontSize: 11 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontWeight: 600 }}>{h.username || "-"}</span>
              <span style={{ color: "var(--muted)" }}>{relTime(h.timestamp)}</span>
            </div>
            <div style={{ color: "var(--text-secondary)", marginTop: 2, fontFamily: "JetBrains Mono, monospace", fontSize: 10 }}>{h.action}</div>
            {h.detail && <div style={{ color: "var(--text-secondary)", marginTop: 2, lineHeight: 1.4 }}>{h.detail}</div>}
          </div>
        ))}
      </Section>
    </div>
  );
}


function ManagementFlow({ flow }) {
  const nodes = flow?.nodes || [];
  if (!nodes.length) return null;
  return (
    <Section title="Management Flow">
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6 }}>
        {nodes.map((node, idx) => (
          <div key={node.id || idx}>
            <div style={{ display: "grid", gridTemplateColumns: "70px 1fr", gap: 8, alignItems: "start" }}>
              <div style={{
                fontSize: 10,
                textTransform: "uppercase",
                color: node.state === "disabled" ? "var(--danger)" : "var(--text-secondary)",
                fontWeight: 800,
                letterSpacing: 0.4,
              }}>
                {node.kind}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: "var(--text-primary)" }}>{node.label}</div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.45, wordBreak: "break-word" }}>
                  {node.detail}
                </div>
              </div>
            </div>
            {idx < nodes.length - 1 && (
              <div style={{ marginLeft: 32, height: 12, borderLeft: "1px solid var(--border)" }} />
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}


function KnowledgeRefs({ refs }) {
  if (!refs) return null;
  const rows = [
    { label: "Wiki docs", values: refs.wiki_doc_ids || [] },
    { label: "Graph nodes", values: refs.graph_node_ids || [] },
    { label: "Relations", values: refs.relation_ids || [] },
    { label: "Column keys", values: refs.column_catalog_keys || [] },
    { label: "Required args", values: refs.required_args || [] },
  ].filter((row) => row.values.length > 0);
  const hasFeature = !!refs.feature_md;
  if (!rows.length && !hasFeature && !refs.data_source_count && !refs.few_shot_count) return null;
  return (
    <Section title="Wiki / Graph Refs">
      {hasFeature && (
        <div style={{ fontSize: 11, fontFamily: "JetBrains Mono, monospace", color: "var(--text-secondary)", wordBreak: "break-all", marginBottom: 6 }}>
          feature: {refs.feature_md}
        </div>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: rows.length ? 6 : 0 }}>
        {Number(refs.data_source_count || 0) > 0 && <Tag>{refs.data_source_count} data sources</Tag>}
        {Number(refs.few_shot_count || 0) > 0 && <Tag>{refs.few_shot_count} examples</Tag>}
      </div>
      {rows.map((row) => (
        <div key={row.label} style={{ marginTop: 6 }}>
          <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 3 }}>{row.label}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {row.values.slice(0, 12).map((value) => (
              <Tag key={`${row.label}:${value}`}>{String(value)}</Tag>
            ))}
            {row.values.length > 12 && <Tag>+{row.values.length - 12}</Tag>}
          </div>
        </div>
      ))}
    </Section>
  );
}


function Section({ title, children }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 6 }}>
        {title}
      </div>
      <div>{children}</div>
    </div>
  );
}


function Stat({ label, value, accent }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px dashed var(--border)" }}>
      <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>{label}</span>
      <span style={{ color: accent || "var(--text-primary)", fontWeight: 600, fontSize: 12 }}>{value}</span>
    </div>
  );
}


function KindBadge({ kind }) {
  return (
    <span style={{
      display: "inline-block", padding: "1px 6px", borderRadius: 3,
      background: KIND_COLORS[kind] || "var(--muted)",
      color: "#fff", fontSize: 10, fontWeight: 700, letterSpacing: 0.4,
    }}>{KIND_LABELS[kind] || kind}</span>
  );
}


function Tag({ children }) {
  return (
    <span style={{ display: "inline-block", padding: "1px 6px", border: "1px solid var(--border)",
      borderRadius: 3, fontSize: 10, color: "var(--text-secondary)", background: "var(--bg-primary)" }}>
      {children}
    </span>
  );
}


function TagChip({ label, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: "2px 8px",
        border: "1px solid " + (active ? "var(--accent)" : "var(--border)"),
        background: active ? "var(--accent-glow)" : "var(--bg-primary)",
        color: active ? "var(--accent)" : "var(--text-secondary)",
        borderRadius: 12,
        fontSize: 11,
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}


function FilterRadio({ value, setValue, options }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {options.map((o) => (
        <label key={o.value} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, cursor: "pointer", color: "var(--text-primary)" }}>
          <input type="radio" checked={value === o.value} onChange={() => setValue(o.value)} />
          {o.label}
        </label>
      ))}
    </div>
  );
}


function relTime(ts) {
  if (!ts) return "";
  try {
    const t = new Date(ts);
    const diff = (Date.now() - t.getTime()) / 1000;
    if (diff < 60) return "방금";
    if (diff < 3600) return Math.floor(diff / 60) + "분 전";
    if (diff < 86400) return Math.floor(diff / 3600) + "시간 전";
    if (diff < 86400 * 30) return Math.floor(diff / 86400) + "일 전";
    return t.toISOString().slice(0, 10);
  } catch (_) { return ""; }
}


const inputStyle = {
  width: "100%",
  padding: "6px 8px",
  fontSize: 12,
  border: "1px solid var(--border)",
  borderRadius: 4,
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  marginBottom: 12,
};

const selectStyle = { ...inputStyle, marginBottom: 12 };

const sectionLabelStyle = {
  fontSize: 11, fontWeight: 700, color: "var(--text-secondary)",
  textTransform: "uppercase", letterSpacing: 0.6,
  margin: "12px 0 6px",
};

const btnGhost = {
  padding: "4px 10px",
  border: "1px solid var(--border)",
  background: "transparent",
  color: "var(--text-secondary)",
  borderRadius: 4,
  fontSize: 11,
  cursor: "pointer",
};
