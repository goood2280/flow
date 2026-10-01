import { useCallback, useEffect, useState } from "react";

import { sf } from "../../lib/api";
import { Banner, Button, Card, EmptyState, LoadingState, PageHeader, PageShell, Pill } from "../../components/ui";

const API = "/api/file-check/report";

const STATUS = {
  ok: { label: "정상", tone: "ok" },
  warning: { label: "확인 필요", tone: "warn" },
  error: { label: "검사 불가", tone: "bad" },
};

const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13 };
const thStyle = {
  padding: "8px 10px", textAlign: "left", color: "var(--text-secondary)",
  background: "var(--bg-secondary)", borderBottom: "1px solid var(--border)",
};
const tdStyle = { padding: "8px 10px", borderBottom: "1px solid var(--border)", verticalAlign: "top" };

function SourceSummary({ source }) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", color: "var(--text-secondary)", fontSize: 13 }}>
        <code>{source.file}</code>
        <span>{source.location}</span>
        <span>{source.row_count.toLocaleString()}행</span>
        <span>step_desc {source.step_desc_count.toLocaleString()}종</span>
      </div>
      {source.alternatives?.map((alternative) => (
        <div key={`${alternative.location}-${alternative.file}`} style={{ color: "var(--text-secondary)", fontSize: 12 }}>
          별도 파일: <code>{alternative.file}</code> · {alternative.location} · {alternative.note}
        </div>
      ))}
    </div>
  );
}

function Diagnostics({ items }) {
  if (!items?.length) return null;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {items.map((item, index) => (
        <Banner key={`${item.code}-${index}`} tone={item.level === "error" ? "danger" : "warning"}>
          {item.message}
          {item.rows?.length ? ` 행: ${item.rows.join(", ")}` : ""}
        </Banner>
      ))}
    </div>
  );
}

function CheckCard({ check }) {
  const meta = STATUS[check.status] || STATUS.error;
  return (
    <Card
      title={check.label}
      right={<Pill tone={meta.tone}>{meta.label}</Pill>}
      bodyStyle={{ display: "grid", gap: 12 }}
    >
      <SourceSummary source={check.source} />
      <Diagnostics items={check.diagnostics} />
      {check.compared && check.missing.length === 0 && (
        <EmptyState title="누락 없음" message="이 파일의 모든 step_desc가 Vehicle_matching.csv에 있습니다." />
      )}
      {check.missing.length > 0 && (
        <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
          <table style={tableStyle}>
            <thead><tr>
              <th style={thStyle}>누락 step_desc</th>
              <th style={thStyle}>원본 CSV 행</th>
            </tr></thead>
            <tbody>
              {check.missing.map((item) => (
                <tr key={item.step_desc}>
                  <td style={{ ...tdStyle, fontFamily: "monospace", fontWeight: 700 }}>{item.step_desc}</td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>{item.source_rows.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function My_FileCheck() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setReport(await sf(API));
    } catch (exc) {
      setError(exc?.message || "파일 점검 결과를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const summary = report ? (STATUS[report.summary.status] || STATUS.error) : null;
  return (
    <PageShell layout="analysis" style={{ display: "grid", gap: 12 }}>
      <PageHeader
        title="파일점검"
        subtitle="ppid_knob.csv와 vm_matching.csv의 step_desc를 Vehicle_matching.csv 기준으로 비교합니다. 파일은 변경하지 않습니다."
        status={summary ? <Pill tone={summary.tone}>{summary.label}</Pill> : null}
        right={<Button variant="primary" onClick={load} disabled={loading}>다시 점검</Button>}
      />
      {loading && !report && <LoadingState />}
      {error && <Banner tone="danger">{error}</Banner>}
      {report && (
        <>
          <Card title="기준 파일" bodyStyle={{ display: "grid", gap: 10 }}>
            <SourceSummary source={report.reference} />
            <Diagnostics items={report.reference.diagnostics} />
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>
              점검 시각 {new Date(report.generated_at).toLocaleString()} · 읽기 전용
            </div>
          </Card>
          {report.checks.map((check) => <CheckCard key={check.id} check={check} />)}
        </>
      )}
    </PageShell>
  );
}
