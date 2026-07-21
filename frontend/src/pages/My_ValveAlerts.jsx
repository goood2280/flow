/* My_ValveAlerts.jsx — Valve 파이프라인 알람 판정.
   - Valve 가 S3 로 발행한 알람(valve-alerts/pipeline/*.json)을 표시하고 엔지니어가 판정:
     · RO ppid  : 카테고리 기입 → ppid_knob.csv 다음 Rule 넘버(R{n+1})로 추가
     · 미매칭 step: vehicle/step_id/step_desc 확인 → Vehicle_matching.csv 에 행 추가
   - 반영 시 파일 버전 스냅샷 + S3 업로드 → Valve csv_sync 가 내려받아 재실행 시 알람 소멸.
   - 판정 이력(누가/언제/무엇으로) + ack(보류/반영불필요) 관리.
*/
import { useEffect, useMemo, useState } from "react";
import { sf, postJson, putJson } from "../lib/api";
import { toast } from "../components/Toast";
import { Button, Card, EmptyState, Pill } from "../components/UXKit";

const API = "/api/valve-alerts";

/* 알람 전송 설정 — 알람 JSON(vehicle별)/ack.json 을 읽고 쓰는 S3 위치.
   자격증명·region 등은 저장된 AWS 설정(s3_sync 와 동일 체계)을 그대로 사용하므로
   여기서는 bucket 명과 알람 prefix 만 입력한다. bucket 을 비우면 s3_sync 의
   bucket 설정을 재사용. (로컬 데모용 local_root 는 설정 파일로만 관리 — UI 비노출) */
function TransferSettings({ onSaved }) {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState(null);
  const [storeInfo, setStoreInfo] = useState({ ok: false, store: "" });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const r = await sf(API + "/config");
      setCfg(r.config);
      setStoreInfo({ ok: r.store_ok, store: r.store });
    } catch (e) {
      toast.error(String(e.message || e));
    }
  };
  useEffect(() => { if (open && !cfg) load(); }, [open]);

  const save = async () => {
    setSaving(true);
    try {
      const r = await putJson(API + "/config", cfg);
      setCfg(r.config);
      setStoreInfo({ ok: r.store_ok, store: r.store });
      toast.ok("전송 설정 저장됨" + (r.store_ok ? "" : " — 저장소 연결 확인 필요"));
      onSaved && onSaved();
    } catch (e) {
      toast.error(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const set = (patch) => setCfg(prev => ({ ...prev, ...patch }));
  const setS3 = (patch) => setCfg(prev => ({ ...prev, s3: { ...(prev.s3 || {}), ...patch } }));
  const row = { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 };
  const lab = { fontSize: 12, color: "var(--muted)", minWidth: 130 };

  return (
    <Card
      title="알람 전송 설정 (S3)"
      right={<Button onClick={() => setOpen(o => !o)}>{open ? "접기" : "펼치기"}</Button>}
    >
      {!open ? (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>
          알람 JSON/ack.json 을 읽고 쓰는 S3 위치입니다. 자격증명은 저장된 AWS 설정을 그대로 쓰므로
          bucket 명과 prefix 만 입력하면 됩니다. 룰북 CSV 는 파일탐색기 저장과 같은 S3 동기화로 자동 업로드됩니다.
        </div>
      ) : !cfg ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : (
        <div>
          <div style={row}>
            <span style={lab}>S3 bucket</span>
            <input style={{ ...inputStyle, minWidth: 220 }} value={cfg.s3?.bucket || ""}
              onChange={e => setS3({ bucket: e.target.value })}
              placeholder="비우면 파일탐색기 S3 동기화의 bucket 재사용" />
          </div>
          <div style={row}>
            <span style={lab}>알람 prefix</span>
            <input style={{ ...inputStyle, minWidth: 220 }} value={cfg.alerts_prefix || ""}
              onChange={e => set({ alerts_prefix: e.target.value })} placeholder="valve-alerts" />
            <span style={{ fontSize: 11, color: "var(--muted)" }}>Valve 설정의 alerts.s3_prefix 와 동일하게</span>
          </div>
          <div style={{ ...row, marginTop: 12 }}>
            <Button variant="primary" disabled={saving} onClick={save}>저장</Button>
            <Pill tone={storeInfo.ok ? "ok" : "danger"}>{storeInfo.ok ? "연결됨" : "연결 안 됨"}</Pill>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{storeInfo.store}</span>
          </div>
        </div>
      )}
    </Card>
  );
}

function fmtTs(ts) {
  if (!ts) return "-";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch (_) { return "-"; }
}

function statusTone(a) {
  if (a.decision) return "ok";
  if (a.status === "반영완료") return "ok";
  if (a.status === "미확인예정") return "warn";
  if (a.status === "반영불필요") return "neutral";
  return "danger";
}

function statusLabel(a) {
  if (a.status === "active" && a.decision) return "반영완료";
  return a.status === "active" ? "판정 대기" : a.status;
}

const cellStyle = { padding: "6px 8px", borderBottom: "1px solid var(--line)", verticalAlign: "middle", fontSize: 13 };
const headStyle = { ...cellStyle, fontWeight: 600, color: "var(--muted)", whiteSpace: "nowrap" };
const inputStyle = {
  background: "var(--panel)", color: "var(--text)", border: "1px solid var(--line)",
  borderRadius: 4, padding: "4px 8px", fontSize: 13, minWidth: 110,
};

function HoldButtons({ alert, busy, onAck }) {
  if (alert.status !== "active") {
    return <Button disabled={busy} onClick={() => onAck(alert.id, "active")} title="다시 판정 대기로 되돌리기">해제</Button>;
  }
  return (
    <Button disabled={busy} onClick={() => onAck(alert.id, "반영불필요")} title="반영 불필요 — 재알람 억제 (해제로 복귀 가능)">불필요</Button>
  );
}

export default function My_ValveAlerts() {
  const [data, setData] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [inputs, setInputs] = useState({});   // alert_id -> {category, feature_name, step_desc}
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const [d, dec] = await Promise.all([
        sf(API),
        sf(API + "/decisions?limit=100"),
      ]);
      setData(d);
      setDecisions(dec.decisions || []);
    } catch (e) {
      toast.error(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };
  // 주기 자동 갱신 — Valve 주기 발행/백엔드 폴러와 짝. 입력값(inputs)은 별도 state 라 유지됨.
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const alerts = data?.alerts || [];
  const roAlerts = useMemo(() => alerts.filter(a => a.type === "ro_ppid"), [alerts]);
  const umAlerts = useMemo(() => alerts.filter(a => a.type === "unmatched_step"), [alerts]);

  const setInput = (id, patch) =>
    setInputs(prev => ({ ...prev, [id]: { ...(prev[id] || {}), ...patch } }));

  const act = async (id, fn) => {
    setBusy(id);
    try {
      await fn();
      await load();
    } catch (e) {
      toast.error(String(e.message || e));
    } finally {
      setBusy("");
    }
  };

  const classify = (a) => {
    const v = inputs[a.id] || {};
    const category = (v.category || "").trim();
    if (!category) { toast.error("분류할 KNOB 카테고리를 입력하세요"); return; }
    act(a.id, async () => {
      const r = await postJson(API + "/classify-ppid", {
        id: a.id, category, feature_name: (v.feature_name || "").trim(), note: (v.note || "").trim(),
      });
      toast.ok(`룰 반영: ${r.feature_name} ${r.rule_order} — ${a.ppid} → ${r.category}`);
    });
  };

  const matchStep = (a) => {
    const v = inputs[a.id] || {};
    const step_desc = (v.step_desc ?? a.step_desc ?? "").trim();
    if (!step_desc) { toast.error("step_desc 를 입력하세요"); return; }
    act(a.id, async () => {
      const r = await postJson(API + "/match-step", { id: a.id, step_desc, note: (v.note || "").trim() });
      toast.ok(`매칭 추가: ${r.vehicle} ${r.step_id} → ${r.step_desc}`);
    });
  };

  const ack = (id, status) =>
    act(id, async () => {
      await postJson(API + "/ack", { id, status, note: (inputs[id]?.note || "").trim() });
      toast(status === "active" ? "억제 해제됨" : `상태 기록: ${status}`);
    });

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
      {data && !data.ok && (
        <Card title="연결 오류">
          <div style={{ color: "var(--danger, #d66)", fontSize: 13 }}>{data.error}</div>
          <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 6 }}>
            data/flow-data/valve_alerts.json 의 local_root(로컬 데모) 또는 s3_sync.json 의 bucket 설정을 확인하세요.
          </div>
        </Card>
      )}
      {data?.ok && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Pill tone={data.active ? "danger" : "ok"}>판정 대기 {data.active}건</Pill>
          <Pill tone="neutral">전체 {alerts.length}건</Pill>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>저장소: {data.store}</span>
        </div>
      )}

      <TransferSettings onSaved={load} />

      <Card title={`PPID → KNOB 분류 (RO ppid 알람 ${roAlerts.length}건)`}>
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : roAlerts.length === 0 ? (
          <EmptyState title="RO ppid 알람 없음" hint="knob 매핑에 없는 ppid 가 발견되면 여기에 표시됩니다" />
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                {["상태", "vehicle", "step_id", "step_desc", "ppid", "split 기간", "lot/row", "최초 발견", "분류 (KNOB 카테고리)", "판정"].map(h =>
                  <th key={h} style={headStyle}>{h}</th>)}
              </tr></thead>
              <tbody>
                {roAlerts.map(a => (
                  <tr key={a.id}>
                    <td style={cellStyle}>
                      <Pill tone={statusTone(a)}>{statusLabel(a)}</Pill>
                      {a.decision && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
                        {a.decision.by} · {fmtTs(a.decision.ts)}</div>}
                    </td>
                    <td style={cellStyle}>{a.vehicle}</td>
                    <td style={{ ...cellStyle, fontFamily: "monospace" }}>{a.step_id}</td>
                    <td style={cellStyle}>{a.step_desc}</td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontWeight: 600 }}>{a.ppid}</td>
                    <td style={{ ...cellStyle, fontSize: 11, maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={a.split}>{a.split}</td>
                    <td style={cellStyle}>{a.n_lots}/{a.rows}</td>
                    <td style={cellStyle}>{fmtTs(a.first_seen_ts)}</td>
                    <td style={cellStyle}>
                      <input style={inputStyle} placeholder="예: KNOB_NEW"
                        value={inputs[a.id]?.category ?? ""}
                        onChange={e => setInput(a.id, { category: e.target.value })}
                        disabled={!!a.decision} />
                    </td>
                    <td style={{ ...cellStyle, whiteSpace: "nowrap" }}>
                      <Button variant="primary" disabled={busy === a.id || !!a.decision} onClick={() => classify(a)}>룰 반영</Button>{" "}
                      <HoldButtons alert={a} busy={busy === a.id} onAck={ack} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={`Vehicle Matching — 미매칭 step 알람 ${umAlerts.length}건`}>
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : umAlerts.length === 0 ? (
          <EmptyState title="미매칭 step 알람 없음" hint="vehicle_matching 에 없는 step 이 발견되면 여기에 표시됩니다" />
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                {["상태", "vehicle", "step_id", "step_desc (판정)", "eqp", "lot/row", "최초 발견", "판정"].map(h =>
                  <th key={h} style={headStyle}>{h}</th>)}
              </tr></thead>
              <tbody>
                {umAlerts.map(a => (
                  <tr key={a.id}>
                    <td style={cellStyle}>
                      <Pill tone={statusTone(a)}>{statusLabel(a)}</Pill>
                      {a.decision && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
                        {a.decision.by} · {fmtTs(a.decision.ts)}</div>}
                    </td>
                    <td style={cellStyle}>{a.vehicle}</td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontWeight: 600 }}>{a.step_id}</td>
                    <td style={cellStyle}>
                      <input style={{ ...inputStyle, minWidth: 160 }}
                        value={inputs[a.id]?.step_desc ?? a.step_desc ?? ""}
                        onChange={e => setInput(a.id, { step_desc: e.target.value })}
                        disabled={!!a.decision} />
                    </td>
                    <td style={cellStyle}>{a.eqp_id}{a.eqp_model ? ` (${a.eqp_model})` : ""}</td>
                    <td style={cellStyle}>{a.n_lots}/{a.rows}</td>
                    <td style={cellStyle}>{fmtTs(a.first_seen_ts)}</td>
                    <td style={{ ...cellStyle, whiteSpace: "nowrap" }}>
                      <Button variant="primary" disabled={busy === a.id || !!a.decision} onClick={() => matchStep(a)}>매칭 추가</Button>{" "}
                      <HoldButtons alert={a} busy={busy === a.id} onAck={ack} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="판정 이력">
        {decisions.length === 0 ? (
          <EmptyState title="판정 이력 없음" hint="룰 반영/매칭 추가/보류 처리 내역이 여기에 남습니다" />
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                {["일시", "알람", "판정", "내용", "파일", "판정자"].map(h => <th key={h} style={headStyle}>{h}</th>)}
              </tr></thead>
              <tbody>
                {decisions.map((d, i) => (
                  <tr key={i}>
                    <td style={cellStyle}>{fmtTs(d.ts)}</td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: 11 }}>{d.alert_id}</td>
                    <td style={cellStyle}>
                      <Pill tone={d.action === "classify" || d.action === "match" ? "ok" : "neutral"}>
                        {d.action === "classify" ? "룰 반영" : d.action === "match" ? "매칭 추가" : d.action}
                      </Pill>
                    </td>
                    <td style={cellStyle}>
                      {d.action === "classify"
                        ? `${d.ppid} → ${d.category} (${d.feature_name} ${d.rule_order})`
                        : d.action === "match"
                          ? `${d.step_id} → ${d.step_desc}`
                          : (d.detail || "-")}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: 11 }}>{d.file || "-"}</td>
                    <td style={cellStyle}>{d.by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>
          파일 단위 버전 이력(스냅샷/롤백)은 파일탐색기 › 해당 csv 의 버전 기록에서 확인할 수 있습니다.
        </div>
      </Card>
    </div>
  );
}
