/* 저장된 Mapfile 검사 snapshot을 빠르게 보여 주고, 선택한 버전을 검증 탭으로 연다. */
import { useCallback, useEffect, useRef, useState } from "react";
import { sf } from "../../lib/api";
import { toast } from "../../components/Toast";
import { Button, Card, EmptyState, Pill } from "../../components/UXKit";

const API = "/api/teg-map";
const POLL_MS = 2000;
const LIGHTS = {
  green: { label: "정상", color: "#2f9e63", bg: "rgba(47,158,99,.12)", icon: "🟢" },
  blue: { label: "갱신 중", color: "#2563eb", bg: "rgba(37,99,235,.12)", icon: "🔵" },
  yellow: { label: "확인 필요", color: "#b7791f", bg: "rgba(217,154,26,.12)", icon: "🟡" },
  red: { label: "불일치", color: "#dc2626", bg: "rgba(220,38,38,.12)", icon: "🔴" },
  none: { label: "해당 없음", color: "#6b7280", bg: "rgba(107,114,128,.08)", icon: "⚪" },
  gray: { label: "대기", color: "#6b7280", bg: "rgba(107,114,128,.08)", icon: "⚪" },
};

function TrafficPill({ light, prefix = "" }) {
  const item = LIGHTS[light] || LIGHTS.gray;
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 8px",
    borderRadius: 12, fontSize: 11, fontWeight: 700, color: item.color, background: item.bg,
    border: `1px solid ${item.color}`, whiteSpace: "nowrap" }}>
    {item.icon} {prefix}{item.label}
  </span>;
}

function shortVersion(signature) {
  const value = String(signature || "");
  const hash = value.includes(":sha256:") ? value.split(":sha256:").pop() : value;
  return hash ? hash.slice(0, 10) : "-";
}

function formatTime(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").replace(/[+-]\d\d:\d\d$/, "");
}

function CommentSummary({ summary }) {
  const count = Number(summary?.count || 0);
  if (!count) return <span style={{ color: "var(--muted)" }}>코멘트 없음</span>;
  const latest = summary.latest || {};
  return <div style={{ display: "grid", gap: 2, minWidth: 180 }}>
    <span style={{ fontSize: 11, fontWeight: 700 }}>💬 {count}개 · {latest.author || "작성자 미상"}</span>
    <span title={latest.text || ""} style={{ color: "var(--muted)", overflow: "hidden",
      textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 280 }}>{latest.text || "-"}</span>
  </div>;
}

function MapfileTable({ files, openingKey, initialFilename, onOpen }) {
  if (!files?.length) return null;
  return <div style={{ overflowX: "auto" }}>
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
      <thead><tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left", color: "var(--muted)" }}>
        <th style={{ padding: "8px 10px" }}>파일</th>
        <th style={{ padding: "8px 10px", width: 92 }}>종합</th>
        <th style={{ padding: "8px 10px", width: 110 }}>S/L</th>
        <th style={{ padding: "8px 10px", width: 110 }}>Main</th>
        <th style={{ padding: "8px 10px", width: 105 }}>버전</th>
        <th style={{ padding: "8px 10px", minWidth: 220 }}>검증 요약</th>
        <th style={{ padding: "8px 10px", minWidth: 200 }}>최근 코멘트</th>
        <th style={{ padding: "8px 10px", width: 145 }}>검증 시각</th>
      </tr></thead>
      <tbody>{files.map(file => {
        const filename = file.rel_path || file.filename;
        const opening = openingKey === `${filename}\0${file.signature || ""}`;
        const highlighted = initialFilename && [filename, file.filename].includes(initialFilename);
        return <tr key={`${filename}:${file.signature || ""}`} style={{ borderBottom: "1px solid var(--line)",
          background: highlighted ? "var(--bg-hover)" : "transparent" }}>
          <td style={{ padding: "8px 10px", minWidth: 220 }}>
            <button type="button" onClick={() => onOpen(file)} disabled={opening}
              title="이 저장 버전을 Mapfile 검증 탭에서 엽니다"
              style={{ border: 0, padding: 0, background: "transparent", color: "var(--accent)",
                cursor: opening ? "wait" : "pointer", fontWeight: 700, textAlign: "left" }}>
              {opening ? "여는 중…" : <>{file.filename} <span style={{ whiteSpace: "nowrap" }}>상세보기 →</span></>}
            </button>
          </td>
          <td style={{ padding: "8px 10px" }}><TrafficPill light={file.traffic_light} /></td>
          <td style={{ padding: "8px 10px" }}><TrafficPill light={file.sl?.light} /></td>
          <td style={{ padding: "8px 10px" }}><TrafficPill light={file.main?.light} /></td>
          <td style={{ padding: "8px 10px", fontFamily: "monospace" }} title={file.signature || ""}>
            {shortVersion(file.signature)}
          </td>
          <td style={{ padding: "8px 10px", color: file.error ? "var(--danger)" : "var(--text)", lineHeight: 1.45 }}>
            {file.comment || file.error || "검사 대기"}
          </td>
          <td style={{ padding: "8px 10px" }}><CommentSummary summary={file.comment_summary} /></td>
          <td style={{ padding: "8px 10px", color: "var(--muted)", whiteSpace: "nowrap" }}>
            {formatTime(file.verified_at || file.checked_at)}
          </td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}

export default function TegMapfileTraffic({ vehicle, onOpenCheck, initialFilename = "" }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [openingKey, setOpeningKey] = useState("");
  const requestRef = useRef({ id: 0, controller: null });
  const openRequestRef = useRef({ id: 0, controller: null });

  const loadTraffic = useCallback(async ({ quiet = false } = {}) => {
    if (!vehicle) return;
    requestRef.current.controller?.abort();
    const controller = new AbortController();
    const id = requestRef.current.id + 1;
    requestRef.current = { id, controller };
    if (!quiet) setLoading(true);
    try {
      const result = await sf(`${API}/mapfile-traffic?vehicle=${encodeURIComponent(vehicle)}`, { signal: controller.signal });
      if (requestRef.current.id === id) setData(result);
    } catch (error) {
      if (!controller.signal.aborted && requestRef.current.id === id) {
        toast.error(`신호등 조회 실패: ${error.message || error}`);
      }
    } finally {
      if (requestRef.current.id === id) {
        requestRef.current.controller = null;
        if (!quiet) setLoading(false);
      }
    }
  }, [vehicle]);

  useEffect(() => {
    setData(null);
    loadTraffic();
    return () => {
      requestRef.current.controller?.abort();
      openRequestRef.current.controller?.abort();
    };
  }, [loadTraffic]);

  useEffect(() => {
    if (!data?.refreshing) return undefined;
    const timer = window.setInterval(() => loadTraffic({ quiet: true }), POLL_MS);
    return () => window.clearInterval(timer);
  }, [data?.refreshing, loadTraffic]);

  const openInCheck = async file => {
    const filename = file.rel_path || file.filename;
    const key = `${filename}\0${file.signature || ""}`;
    openRequestRef.current.controller?.abort();
    const controller = new AbortController();
    const id = openRequestRef.current.id + 1;
    openRequestRef.current = { id, controller };
    setOpeningKey(key);
    try {
      const query = new URLSearchParams({ vehicle, filename, version: file.signature || "" });
      const result = await sf(`${API}/mapfile-traffic/content?${query.toString()}`, { signal: controller.signal });
      if (controller.signal.aborted || openRequestRef.current.id !== id) return;
      onOpenCheck?.(result.content, result.filename || filename, {
        vehicle,
        filename: result.filename || filename,
        signature: result.signature || file.signature || "",
      });
    } catch (error) {
      if (!controller.signal.aborted && openRequestRef.current.id === id) {
        toast.error(`파일 원문 로드 실패: ${error.message || error}`);
      }
    } finally {
      if (openRequestRef.current.id === id) {
        openRequestRef.current.controller = null;
        setOpeningKey("");
      }
    }
  };

  if (!vehicle) return <EmptyState icon="🚦" title="제품을 선택해 주세요" />;
  const groups = data?.groups?.length
    ? data.groups
    : (data?.files?.length ? [{ key: "all", label: "Mapfile", files: data.files, overall_light: data.overall_light }] : []);
  return <div style={{ display: "grid", gap: 14 }}>
    <Card title="GitHub 주기검사" right={<div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      {data?.refreshing && <Pill tone="info">백그라운드 갱신 중</Pill>}
      <TrafficPill light={data?.overall_light} prefix="전체 " />
      <Button size="sm" disabled={loading} onClick={() => loadTraffic()}>
        {loading ? "조회 중…" : "새로고침"}
      </Button>
    </div>}>
      <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap", fontSize: 12 }}>
        <span><b>{data?.product_code || vehicle}</b></span>
        <span style={{ color: "var(--muted)" }}>마지막 검사 {formatTime(data?.checked_at)}</span>
        {loading && <span style={{ color: "var(--muted)" }}>불러오는 중…</span>}
        {data?.error && <span style={{ color: "var(--danger)" }}>{data.error}</span>}
      </div>
    </Card>
    {!loading && !groups.length && <EmptyState icon="🚦" title="저장된 검사 결과가 없습니다"
      hint="백그라운드 검사가 끝나면 자동으로 표시됩니다." />}
    {groups.map(group => <Card key={group.key || group.label}
      title={`${group.label || group.key} · ${group.files?.length || 0}개`}
      right={<TrafficPill light={group.overall_light} />}>
      {group.files?.length
        ? <MapfileTable files={group.files} openingKey={openingKey} initialFilename={initialFilename} onOpen={openInCheck} />
        : <div style={{ color: "var(--muted)", fontSize: 12 }}>Mapfile이 없습니다.</div>}
    </Card>)}
  </div>;
}
