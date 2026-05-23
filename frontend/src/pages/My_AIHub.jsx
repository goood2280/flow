// My_AIHub.jsx — flow 본진 v9.1 AI Hub.
// Unit AI 11개 + Function-call 16개를 한 화면 3-pane 으로 카탈로그·관리.
// 백엔드 /api/ai-hub/tools 가 단일 진실이며, 이 페이지는 표시·필터·toggle 만 담당한다.

import { useEffect, useMemo, useState } from "react";
import { sf, postJson } from "../lib/api";

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

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", background: "var(--bg-primary)" }}>
      {/* 오케스트레이터 + 운영 보드 + 스킬 패널 */}
      <OrchestratorPanel />
      <OperationsBoard days={days} onChanged={loadCatalog} />
      <WorkflowMapPanel days={days} />
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


function WorkflowMapPanel({ days }) {
  const [open, setOpen] = useState(false);
  const [focusTag, setFocusTag] = useState("");
  const [map, setMap] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState("");
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
        format,
        days: String(days),
        limit: "40",
        reference_limit: "160",
      });
      if (focusTag) qs.set("focus_tag", focusTag);
      const out = await sf(`/api/ai-hub/workflow-map/export?${qs.toString()}`);
      downloadJson(out.filename || `flow-ai-hub-workflow-map.${format}.json`, out);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setExporting("");
    }
  }

  useEffect(() => { if (open) loadMap(); }, [open, days, focusTag]);

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
        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
          Prompt → Policy → Unit/Function → Wiki/Schema → Improve
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
            <button onClick={() => exportMap("obsidian")} disabled={!!exporting} style={btnGhost}>
              {exporting === "obsidian" ? "내보내는 중" : "Obsidian MD"}
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
              {(map.warnings || []).length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5, margin: "6px 0" }}>
                  {(map.warnings || []).map((w) => <BoardPill key={w.key} tone={w.tone}>{w.message}</BoardPill>)}
                </div>
              )}
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
                <WorkflowNodeDetail node={selected} edges={edges} nodes={nodes} />
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
      <BoardPill tone={counts.tools_disabled_visible ? "bad" : "ok"}>비활성 {counts.tools_disabled_visible || 0}</BoardPill>
      <BoardPill tone={counts.tools_without_refs_visible ? "warn" : "ok"}>근거 없음 {counts.tools_without_refs_visible || 0}</BoardPill>
      <BoardPill tone="neutral">노드 {counts.nodes || 0}</BoardPill>
      <BoardPill tone="neutral">엣지 {counts.edges || 0}</BoardPill>
      {map.focus_tag && <BoardPill tone="info">focus {map.focus_tag}</BoardPill>}
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
  const toneColor = node.tone === "bad" ? "var(--danger)" : node.tone === "ok" ? "var(--ok)" : node.tone === "info" ? "var(--info)" : "var(--text-secondary)";
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
    </button>
  );
}


function WorkflowNodeDetail({ node, edges, nodes }) {
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
