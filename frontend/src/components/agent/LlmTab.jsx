import { useEffect, useState } from "react";
import { sf } from "../../lib/api";
import { Banner, Button, DataTable, Panel, Pill } from "../UXKit";
import LlmCfgPanel from "./LlmCfgPanel";

export default function LlmTab({ isAdmin }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = () => {
    setBusy(true);
    setErr("");
    sf("/api/llm/status")
      .then(setStatus)
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setBusy(false));
  };

  useEffect(() => { load(); }, []);

  const cfg = status?.config || {};
  return (
    <div style={{ display: "grid", gap: 12 }}>
      {err && <Banner tone="bad">{err}</Banner>}
      <Panel
        title="연결 상태"
        subtitle="서버에 저장된 redacted LLM runtime 설정입니다."
        right={<div style={{ display: "flex", gap: 8, alignItems: "center" }}><Pill tone={status?.available ? "ok" : "warn"}>{status?.available ? "available" : "unavailable"}</Pill><Button onClick={load} disabled={busy}>{busy ? "확인 중" : "새로고침"}</Button></div>}
      >
        <DataTable
          rows={[
            { key: "provider", value: cfg.provider || "-" },
            { key: "model", value: cfg.model || "-" },
            { key: "endpoint", value: cfg.api_url || "-" },
            { key: "timeout", value: cfg.timeout_s || "-" },
            { key: "auth_mode", value: cfg.auth_mode || "-" },
          ]}
          columns={[
            { key: "key", label: "field", width: 130 },
            { key: "value", label: "value" },
          ]}
        />
      </Panel>

      {!isAdmin && <Banner tone="info">일반 사용자는 연결 상태만 확인할 수 있습니다. 설정 변경과 연결 테스트는 admin 전용입니다.</Banner>}
      <Panel title="설정" subtitle={isAdmin ? "admin은 endpoint/model/timeout과 연결 테스트를 수정할 수 있습니다." : "read-only 상태로 표시합니다."}>
        <LlmCfgPanel readOnly={!isAdmin} />
      </Panel>
    </div>
  );
}
