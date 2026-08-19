/* My_ValveAlerts.jsx — 개발 서버 FAB 매칭 검사.
   - 개발 worker가 FAB 제품을 하나씩 순회하며 처음 보는 step_id/ppid/reticle_id를 표시한다.
   - step_id는 Vehicle_matching.csv, ppid는 ppid_knob.csv, reticle_id는 mask_info.csv에
     엔지니어 판정으로 반영한다 (mask_info.csv는 제품 구분 없는 reticle_id,mask 2열).
   - 판정 이력(누가/언제/무엇으로) + 반영불필요 상태를 관리한다.
*/
import { useEffect, useMemo, useRef, useState } from "react";
import { sf, postJson, putJson } from "../../lib/api";
import { toast } from "../../components/Toast";
import { Button, Card, EmptyState, Filter, LinkBtn, Pill } from "../../components/UXKit";
import PageGear from "../../components/PageGear";
import { canManagePage } from "../../lib/permissions";

const API = "/api/valve-alerts";

function fmtTs(ts) {
  if (!ts) return "-";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch (_) { return "-"; }
}

function alertProducts(alert) {
  const values = alert?.products?.length
    ? alert.products
    : [alert?.product || alert?.vehicle];
  return Array.from(new Set(values.map(value => String(value || "").trim()).filter(Boolean)));
}

function productMatches(value, selectedProduct) {
  return String(value || "").trim().toLocaleLowerCase() === selectedProduct.toLocaleLowerCase();
}

function alertForProduct(alert, selectedProduct) {
  if (!selectedProduct || !alertProducts(alert).some(product => productMatches(product, selectedProduct))) return alert;
  if (alert.type !== "missing_reticle") return alert;
  const evidence = (alert.product_evidence || []).find(item => productMatches(item.product, selectedProduct));
  if (!evidence) return { ...alert, product: selectedProduct, products: [selectedProduct] };
  return {
    ...alert,
    ...evidence,
    product: evidence.product || selectedProduct,
    products: [evidence.product || selectedProduct],
  };
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

const cellStyle = { padding: "7px 8px", borderBottom: "1px solid var(--line)", verticalAlign: "middle", fontSize: 13, textAlign: "left" };
// th 는 브라우저 기본이 center 라 td(left)와 어긋나 보인다 — 명시적으로 left 통일.
const headStyle = { ...cellStyle, fontWeight: 600, color: "var(--muted)", whiteSpace: "nowrap" };
const nowrapCell = { ...cellStyle, whiteSpace: "nowrap" };
const compactCell = {
  ...nowrapCell,
  maxWidth: 320,
  overflow: "hidden",
  textOverflow: "ellipsis",
};
const compactButtonStyle = { padding: "3px 8px", fontSize: 12 };
const inlineMetaStyle = { color: "var(--muted)", fontSize: 12 };
// 열이 좁은 화면에서 짓눌리지 않게 테이블에 minWidth 를 주고 컨테이너가 가로 스크롤을 받는다.
const tableStyle = (minWidth) => ({ width: "100%", borderCollapse: "collapse", minWidth });
const inputStyle = {
  background: "var(--bg-secondary)", color: "var(--text-primary)", border: "1px solid var(--line)",
  borderRadius: 4, padding: "4px 8px", fontSize: 13, minWidth: 110, height: 28,
};
// 표가 자체 스크롤 박스 안에 있으므로 머리행은 붙여둔다 — 아래로 내려도 열 이름이 보인다.
const stickyHeadStyle = {
  ...headStyle, position: "sticky", top: 0, zIndex: 1,
  background: "var(--bg-tertiary)", boxShadow: "inset 0 -1px 0 var(--line)",
};

const ROW_PAGE = 50;
const VISIBLE_ROWS = 10;
// 실측 전 첫 페인트용 근사치(행 높이 46px + 머리행 34px). 곧 실제 높이로 교체된다.
const ESTIMATED_ROW_HEIGHT = 46;

/* 알람이 수백 건이면 카드 하나가 화면 수십 개 길이가 된다.
   표를 자체 스크롤 박스에 넣어 한 번에 10행만 보이게 하고, 나머지는 그 박스 안에서
   스크롤로 본다. 렌더 자체는 50건씩 이어 붙인다(끝까지 내리면 자동 확장).
   스크롤 이벤트가 억제되는 환경을 위해 '더 보기'도 남긴다. */
function ScrollTable({ rows, columns, renderRow, minWidth, visibleRows = VISIBLE_ROWS }) {
  const [limit, setLimit] = useState(ROW_PAGE);
  const boxRef = useRef(null);
  const [maxHeight, setMaxHeight] = useState(
    visibleRows * ESTIMATED_ROW_HEIGHT + ESTIMATED_ROW_HEIGHT);
  const total = rows.length;
  const shown = useMemo(() => rows.slice(0, limit), [rows, limit]);
  const remaining = Math.max(0, total - shown.length);
  // 60초 자동 재조회로 목록이 바뀌어도 한도를 되돌리지 않는다 — 보고 있던
  // 위치가 튀지 않게, 줄어든 경우는 slice 가 알아서 처리한다.
  const grow = () => setLimit(current => Math.min(total, current + ROW_PAGE));
  const onScroll = event => {
    if (!remaining) return;
    const box = event.currentTarget;
    if (box.scrollTop + box.clientHeight >= box.scrollHeight - 200) grow();
  };

  // 행 높이는 표마다(입력칸·추천 셀 유무) 다르다 — vh 로 잡으면 화면 크기에 따라
  // 6행이 보이기도 20행이 보이기도 한다. 실제 행 높이를 재서 딱 visibleRows 만큼만
  // 보이도록 고정한다.
  useEffect(() => {
    const box = boxRef.current;
    const head = box?.querySelector("thead");
    const row = box?.querySelector("tbody tr");
    if (!head || !row) return;
    const measured = head.offsetHeight + row.offsetHeight * visibleRows;
    if (measured > 0) setMaxHeight(measured);
  }, [visibleRows, total, columns.length]);

  return (
    <>
      <div ref={boxRef} onScroll={onScroll} style={{ overflow: "auto", maxHeight }}>
        <table style={tableStyle(minWidth)}>
          <thead><tr>
            {columns.map(column => <th key={column} style={stickyHeadStyle}>{column}</th>)}
          </tr></thead>
          <tbody>{shown.map(renderRow)}</tbody>
        </table>
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 8,
                    fontSize: 12, color: "var(--muted)" }}>
        <span>전체 {total}건 중 {shown.length}건 표시</span>
        {!!remaining && <LinkBtn onClick={grow}>더 보기 (남은 {remaining}건)</LinkBtn>}
      </div>
    </>
  );
}

function StepExceptionSettings({ products = [], canManage, onChanged }) {
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    sf(API + "/config")
      .then(r => setRules(r.config?.step_exceptions || []))
      .catch(e => toast.error(String(e.message || e)))
      .finally(() => setLoading(false));
  }, []);

  const update = (index, patch) => setRules(prev => prev.map((rule, i) => i === index ? { ...rule, ...patch } : rule));
  const add = () => setRules(prev => [...prev, {
    id: `exception-${Date.now()}`, enabled: true, product: "", column: "eqp_model",
    operator: "contains", value: "", note: "",
  }]);
  const remove = index => setRules(prev => prev.filter((_, i) => i !== index));
  const save = async () => {
    if (!canManage) return;
    const invalid = rules.find(rule => !(rule.value || "").trim());
    if (invalid) { toast.error("예외 규칙의 비교값을 입력하세요"); return; }
    setSaving(true);
    try {
      const result = await putJson(API + "/config", { step_exceptions: rules });
      setRules(result.config?.step_exceptions || []);
      toast.ok("미매칭 step 예외 규칙이 저장되었습니다");
      await onChanged?.();
    } catch (e) {
      toast.error(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <div style={{ fontSize: 14, fontWeight: 800, flex: 1 }}>미매칭 step 예외 규칙</div>
        <Button style={compactButtonStyle} disabled={!canManage || loading || saving} onClick={add}>예외 추가</Button>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>
        미등록 step_id의 FAB 행 중 하나라도 아래 조건과 일치하면 해당 step_id 전체를 알람에서 제외합니다.
        비교 열은 EQP MODEL · EQP ID · AREA · PPID 네 가지이며, <b>제품을 비우면 전 제품에 적용</b>됩니다.
      </div>
      {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : rules.length === 0 ? (
        <EmptyState title="등록된 step 예외 없음" hint="PPID·설비 조건으로 제외하려면 ‘예외 추가’를 누르세요" />
      ) : (
        <div style={{ display: "grid", gap: 8, overflowX: "auto" }}>
        <datalist id="fab-matching-products">
          {products.map(product => <option key={product} value={product} />)}
        </datalist>
        {rules.map((rule, index) => (
          <div key={rule.id || index} style={{
            display: "grid", gap: 7, padding: 10, border: "1px solid var(--border)",
            borderRadius: 6, background: "var(--bg-primary)",
          }}>
            <div style={{ display: "grid", gridTemplateColumns: "auto minmax(0,1fr) auto", gap: 7, alignItems: "center" }}>
              <input type="checkbox" checked={rule.enabled !== false} disabled={!canManage || saving}
                onChange={e => update(index, { enabled: e.target.checked })} title="규칙 사용" />
              <input list="fab-matching-products" style={{ ...inputStyle, width: "100%", minWidth: 0, boxSizing: "border-box" }} placeholder="제품(비우면 전체)"
                value={rule.product || ""} disabled={!canManage || saving}
                onChange={e => update(index, { product: e.target.value })} />
              <Button style={compactButtonStyle} disabled={!canManage || saving} onClick={() => remove(index)}>삭제</Button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7 }}>
              <select style={{ ...inputStyle, width: "100%", minWidth: 0 }} value={rule.column || "ppid"}
                disabled={!canManage || saving} onChange={e => update(index, { column: e.target.value })}>
                <option value="eqp_model">EQP MODEL</option>
                <option value="eqp_id">EQP ID</option>
                <option value="area">AREA</option>
                <option value="ppid">PPID</option>
              </select>
              <select style={{ ...inputStyle, width: "100%", minWidth: 0 }} value={rule.operator || "contains"}
                disabled={!canManage || saving} onChange={e => update(index, { operator: e.target.value })}>
                <option value="contains">포함</option>
                <option value="starts_with">시작</option>
                <option value="eq">일치</option>
              </select>
            </div>
            <input style={{ ...inputStyle, width: "100%", minWidth: 0, boxSizing: "border-box" }} placeholder="비교값"
              value={rule.value || ""} disabled={!canManage || saving}
              onChange={e => update(index, { value: e.target.value })} />
            <input style={{ ...inputStyle, width: "100%", minWidth: 0, boxSizing: "border-box" }} placeholder="메모"
              value={rule.note || ""} disabled={!canManage || saving}
              onChange={e => update(index, { note: e.target.value })} />
          </div>
        ))}
        </div>
      )}
      <div style={{ marginTop: 10 }}>
        <Button variant="primary" disabled={!canManage || saving || loading} onClick={save}>예외 규칙 저장</Button>
      </div>
    </div>
  );
}

function MatchingScannerSettings({ config = {}, scanner = {}, canManage, onChanged }) {
  const [enabled, setEnabled] = useState(config.enabled !== false);
  const [intervalMinutes, setIntervalMinutes] = useState(
    Math.max(0.5, Number(config.scan_interval_seconds || 7200) / 60));
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    setEnabled(config.enabled !== false);
    setIntervalMinutes(Math.max(0.5, Number(config.scan_interval_seconds || 7200) / 60));
  }, [config.enabled, config.scan_interval_seconds]);

  const save = async () => {
    if (!canManage) return;
    const seconds = Math.round(Number(intervalMinutes) * 60);
    if (!Number.isFinite(seconds) || seconds < 30) {
      toast.error("검사 간격은 30초 이상으로 입력하세요");
      return;
    }
    setSaving(true);
    try {
      await putJson(API + "/config", { enabled, scan_interval_seconds: seconds });
      toast.ok(`자동 검사 설정 저장 · 제품당 ${seconds < 60 ? `${seconds}초` : `${seconds / 60}분`}`);
      await onChanged?.();
    } catch (e) {
      toast.error(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const runNow = async () => {
    if (!canManage) return;
    setRunning(true);
    try {
      const result = await postJson(API + "/poll", {});
      const message = result.message || "개발 worker에 다음 제품 검사를 요청했습니다";
      // 검사기가 없는데 "등록했습니다" 만 뜨면 요청이 사라진 것처럼 보인다.
      if (result.scanner_alive === false) toast.error(message);
      else toast.ok(message);
      await onChanged?.();
    } catch (e) {
      toast.error(String(e.message || e));
    } finally {
      setRunning(false);
    }
  };

  const busyScan = scanner.scanning || {};
  const scannerStateLabel = scanner.scanner_alive === undefined
    ? "확인 불가"
    : !scanner.scanner_alive
    ? "미기동"
    : busyScan.product
      ? `${busyScan.product} 검사 중`
        + (busyScan.files_total ? ` · 전체 ${busyScan.files_total}개 파일 중 ${busyScan.files_done}개` : "")
        + (busyScan.elapsed_seconds ? ` · ${Math.round(busyScan.elapsed_seconds / 60)}분 경과` : "")
      : "대기(정상)";
  const scannerTone = scanner.scanner_alive === false
    ? "var(--danger, #d66)"
    : busyScan.product ? "var(--warn, #d90)" : "var(--text-primary)";
  const waitingText = scanner.scan_waiting_seconds >= 60
    ? ` · ${Math.round(scanner.scan_waiting_seconds / 60)}분째`
    : "";

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.55 }}>
        대용량 FAB 검사는 개발 worker에서 제품을 하나씩 수행합니다. 운영 API에서 수동 검사를
        눌러도 Parquet를 직접 읽지 않고 공유 요청만 등록합니다.
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14 }}>
        <input type="checkbox" checked={enabled} disabled={!canManage || saving}
          onChange={e => setEnabled(e.target.checked)} />
        자동 검사 사용
      </label>
      <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
        <span style={{ color: "var(--text-secondary)" }}>제품 1개 검사 간격(분)</span>
        <input type="number" min="0.5" step="0.5" value={intervalMinutes}
          disabled={!canManage || saving} onChange={e => setIntervalMinutes(e.target.value)}
          style={{ ...inputStyle, width: "100%", boxSizing: "border-box" }} />
        <span style={{ color: "var(--muted)", fontSize: 11 }}>
          한 번에 제품 하나를 검사한 뒤 이 시간만큼 기다립니다. 최소 0.5분입니다.
        </span>
      </label>
      <Button variant="primary" disabled={!canManage || saving} onClick={save}>
        {saving ? "저장 중…" : "자동 검사 설정 저장"}
      </Button>
      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14, display: "grid", gap: 8 }}>
        <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          검사기 상태: <b style={{ color: scannerTone }}>{scannerStateLabel}</b>
          {scanner.scanner_host ? <span style={inlineMetaStyle}> · {scanner.scanner_host}</span> : null}
          <br />
          다음 제품: <b>{scanner.next_product || "-"}</b><br />
          요청 상태: {scanner.scan_requested
            ? `대기 중${waitingText}`
            : "대기 없음"}<br />
          현재 서버: {scanner.execution_enabled_here ? "개발 worker (검사 실행 가능)" : "운영 API (요청만 전달)"}
        </div>
        {/* "왜 안 도는가" 를 화면에서 끝낸다 — 검사기가 죽었는지, 살아 있는데
            다른 제품을 오래 검사 중인지가 여기서 갈린다. */}
        {!!scanner.scan_request_hint && (
          <div style={{ fontSize: 12, color: scanner.scanner_alive ? "var(--muted)" : "var(--danger, #d66)" }}>
            {scanner.scan_request_hint}
          </div>
        )}
        {!!scanner.last_error && (
          <div style={{ fontSize: 12, color: "var(--danger, #d66)" }}>최근 오류: {scanner.last_error}</div>
        )}
        {/* 요청이 걸려 있어도 다시 누를 수 있다 — 예전에는 버튼이 잠겨서,
            한 번 요청이 물리면 화면에서 할 수 있는 게 없었다. */}
        <Button disabled={!canManage || running} onClick={runNow}>
          {running ? "요청 중…" : scanner.scan_requested ? "검사 재요청" : "지금 다음 제품 검사"}
        </Button>
      </div>
    </div>
  );
}

/* 단계 정체 — Valve 가 파이프라인 전 구간이 며칠째 안 늘고 있는지를 같은 알람 채널로
   보낸다 (payload.health + type=stage_stall). 여기서 고칠 수 있는 건이 아니라
   "Valve 쪽 파이프라인을 봐야 한다"는 신호라 판정 버튼이 없다.
   raw·event 는 데이터 날짜, 그 뒤는 마지막 산출 시각, s3 는 마지막 전송 성공 시각이 기준.
   scope=global(SEND_FORM·S3 전송)은 제품에 매이지 않아 표에 한 줄만 그린다. */
const STAGE_ORDER = ["raw", "event", "feature", "wide", "flow", "send", "s3"];

function PipelineHealth({ vehicles, loading }) {
  const [showAll, setShowAll] = useState(false);
  const rows = useMemo(() => {
    const out = [];
    const seenGlobal = new Set();
    for (const v of vehicles || []) {
      const h = v.health;
      if (!h) continue;
      const stages = [...(h.stages || [])].sort(
        (a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage)
          || String(a.source).localeCompare(String(b.source)));
      for (const s of stages) {
        // SEND_FORM 은 전 제품을 합쳐 만든다 — 제품별 health 에 똑같이 들어 있으므로
        // 표에는 한 줄만 (vehicle 칸은 '전 제품').
        if (s.scope === "global") {
          const key = `${s.stage}|${s.source}`;
          if (seenGlobal.has(key)) continue;
          seenGlobal.add(key);
          out.push({ ...s, vehicle: "전 제품", product: "" });
          continue;
        }
        out.push({ ...s, vehicle: h.vehicle || v.vehicle, product: h.product });
      }
    }
    return out;
  }, [vehicles]);

  const stalled = rows.filter(r => r.stalled);
  // cascade = 앞 단계가 밀린 여파. Valve 는 원인 단계만 알람으로 보내고 여파는
  // health 에만 싣는다 — 원인 하나에 알람 셋이 뜨는 걸 막기 위해서다.
  const roots = stalled.filter(r => !r.cascade);
  const shown = showAll ? rows : stalled;
  // 구 Valve(schema 2 이하)는 health 를 안 보낸다 — 없는 걸 "정상"으로 보여주지 않는다.
  const supported = (vehicles || []).some(v => v.health);
  if (!loading && !supported) return null;
  const threshold = rows[0]?.threshold_days;

  return (
    <Card
      title="Valve 파이프라인 상태"
      right={
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Pill tone={stalled.length ? "danger" : "ok"}>
            {stalled.length ? `정체 ${stalled.length}단계 · 원인 ${roots.length}건` : "전 단계 정상"}
          </Pill>
          <label style={{ ...inlineMetaStyle, display: "flex", gap: 4, alignItems: "center" }}>
            <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
            정상 단계도 표시
          </label>
        </span>
      }
    >
      <div style={{ ...inlineMetaStyle, marginBottom: 8 }}>
        raw → event → feature → ML_TABLE → flow 발행본 → SEND_FORM(prefix 분리) → S3 전송 순입니다.
        raw·event 는 데이터 날짜, 그 뒤는 마지막 산출 시각, S3 는 마지막 전송 성공 시각 기준
        {threshold ? ` (임계 ${threshold}일)` : ""}. 주황색은 앞 단계가 밀린 여파라
        원인 단계를 고치면 같이 풀립니다. 정체는 Valve 쪽 파이프라인 점검 신호입니다.
      </div>
      {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : shown.length === 0 ? (
        <EmptyState title="정체된 단계 없음"
          hint="제품별 raw/event/feature 가 1일 넘게 멈추면 여기에 표시됩니다" />
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={tableStyle(880)}>
            <thead><tr>
              {["제품", "단계", "소스", "최신 데이터", "지연", "앞 단계 대비", "마지막 산출", "사유"].map(h =>
                <th key={h} style={headStyle}>{h}</th>)}
            </tr></thead>
            <tbody>
              {shown.map(r => (
                <tr key={`${r.vehicle}|${r.stage}|${r.source}`} style={r.stalled ? undefined : { opacity: 0.55 }}>
                  <td style={nowrapCell}>
                    <b>{r.vehicle}</b>
                    {r.product && r.product !== r.vehicle && <span style={inlineMetaStyle}> · {r.product}</span>}
                  </td>
                  <td style={nowrapCell}>
                    <Pill tone={!r.stalled ? "ok" : r.cascade ? "warn" : "danger"}>
                      {r.label || r.stage}
                    </Pill>
                  </td>
                  <td style={{ ...nowrapCell, fontFamily: "monospace" }}>{r.source || "-"}</td>
                  <td style={{ ...nowrapCell, fontFamily: "monospace" }}>{r.latest_date || "-"}</td>
                  <td style={{ ...nowrapCell, fontFamily: "monospace" }}>
                    {r.lag_days == null ? "-" : `${r.lag_days}일`}
                  </td>
                  <td style={{ ...nowrapCell, fontFamily: "monospace" }}>
                    {r.behind_days == null ? "-" : `${r.behind_of} −${r.behind_days}일`}
                  </td>
                  <td style={{ ...nowrapCell, fontFamily: "monospace", fontSize: 11 }}>
                    {fmtTs(r.last_write_ts)}
                  </td>
                  <td style={compactCell} title={r.reason}>
                    {r.stalled ? r.reason : <span style={inlineMetaStyle}>정상</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function decisionTitle(a) {
  return a.decision ? `${a.decision.by} · ${fmtTs(a.decision.ts)}` : "";
}

/* 미매칭 step 의 function step 추천 (backend/core/valve_step_advisor.py).
   - AI(GPT OSS 120B)가 붙어 있으면 근거를 정리해 최종 선택을 받고,
   - 붙어 있지 않으면 "AI 미적용"을 띄우고 FAB 시그니처/step_id 근접도만으로 고른다.
   판정 입력칸에 값을 넣어주는 보조일 뿐, 반영은 사람이 누른다. */
const METHOD_LABEL = {
  ai: "AI 판단", ppid: "동일 PPID", signature: "FAB 근거", distance: "step_id 근접", none: "근거 없음",
};

function RecoCell({ alert, busy, onApply, onRecheck }) {
  const r = alert.recommendation;
  const disabled = busy || !!alert.decision;
  // 반영불필요/미확인예정 건은 자동 검사 대상이 아니다 (판정 대기 건만 본다).
  const skipped = alert.status !== "active";
  if (!r) {
    return (
      <div style={{ display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          {skipped ? "검사 대상 아님" : "미검사"}
        </span>
        {!skipped && (
          <Button style={compactButtonStyle} disabled={disabled} onClick={() => onRecheck(alert)}>추천</Button>
        )}
      </div>
    );
  }
  const aiOn = !!r.llm?.applied;
  const pct = Math.round((Number(r.confidence) || 0) * 100);
  const picked = r.picked_step_id ? ` · ${r.picked_step_id}` : "";
  return (
    <div
      title={[r.reason, !aiOn && r.llm?.error].filter(Boolean).join("\n")}
      style={{ display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap", minWidth: 330 }}
    >
      <span style={{ fontWeight: 600 }}>{r.step_desc || "—"}</span>
      <Pill tone={aiOn ? "ok" : "warn"} style={{ fontSize: 11, padding: "1px 5px" }}>
        {aiOn ? `AI ${pct}%` : "AI 미적용"}
      </Pill>
      <span style={{ fontSize: 11, color: "var(--muted)" }}>
        {METHOD_LABEL[r.method] || r.method}{picked}
      </span>
      {r.step_desc && (
        <Button style={compactButtonStyle} disabled={disabled} onClick={() => onApply(alert, r.step_desc)}
          title="판정 입력칸에 채우기">적용</Button>
      )}
      <Button style={compactButtonStyle} disabled={disabled} onClick={() => onRecheck(alert, true)}
        title="근거를 다시 모아 추천을 새로 받는다">재검사</Button>
    </div>
  );
}

function HoldButtons({ alert, busy, onAck }) {
  if (alert.status !== "active") {
    return <Button style={compactButtonStyle} disabled={busy} onClick={() => onAck(alert.id, "active")} title="다시 판정 대기로 되돌리기">해제</Button>;
  }
  return (
    <Button style={compactButtonStyle} disabled={busy} onClick={() => onAck(alert.id, "반영불필요")} title="반영 불필요 — 재알람 억제 (해제로 복귀 가능)">불필요</Button>
  );
}

export default function My_ValveAlerts({ user }) {
  const [data, setData] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [inputs, setInputs] = useState({});   // alert_id -> {category, feature_name, step_desc}
  const [queued, setQueued] = useState({});   // alert_id -> true (일괄 반영 대기)
  const [batchNote, setBatchNote] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedProduct, setSelectedProduct] = useState("");
  const canManage = canManagePage(user, "valve");

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
  // 개발 worker의 제품별 검사 상태를 주기적으로 갱신한다. 입력값은 별도 state라 유지된다.
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const alerts = data?.alerts || [];
  const products = useMemo(() => {
    const names = new Map();
    for (const product of data?.scanner?.product_list || []) {
      const value = String(product || "").trim();
      if (value) names.set(value.toLocaleLowerCase(), value);
    }
    for (const alert of alerts) {
      for (const product of alertProducts(alert)) names.set(product.toLocaleLowerCase(), product);
    }
    return Array.from(names.values()).sort((a, b) => a.localeCompare(b, "ko", { numeric: true }));
  }, [alerts, data?.scanner?.product_list]);
  const productCounts = useMemo(() => Object.fromEntries(products.map(product => [
    product,
    alerts.filter(alert => alertProducts(alert).some(value => productMatches(value, product))).length,
  ])), [alerts, products]);
  const visibleAlerts = useMemo(() => selectedProduct
    ? alerts
      .filter(alert => alertProducts(alert).some(product => productMatches(product, selectedProduct)))
      .map(alert => alertForProduct(alert, selectedProduct))
    : alerts,
  [alerts, selectedProduct]);
  const roAlerts = useMemo(() => visibleAlerts.filter(a => a.type === "ro_ppid"), [visibleAlerts]);
  const umAlerts = useMemo(() => visibleAlerts.filter(a => a.type === "unmatched_step"), [visibleAlerts]);
  const maskAlerts = useMemo(() => visibleAlerts.filter(a => a.type === "missing_reticle"), [visibleAlerts]);
  const visibleActive = useMemo(
    () => visibleAlerts.filter(a => a.status === "active" && !a.decision).length,
    [visibleAlerts]);
  const visibleDecisions = useMemo(() => selectedProduct
    ? decisions.filter(decision => {
      const decisionProducts = alertProducts(decision);
      return decisionProducts.some(product => productMatches(product, selectedProduct))
        || decision.action === "add_mask" || decision.type === "missing_reticle";
    })
    : decisions,
  [decisions, selectedProduct]);
  useEffect(() => {
    if (selectedProduct && !products.some(product => productMatches(product, selectedProduct))) {
      setSelectedProduct("");
    }
  }, [products, selectedProduct]);
  // FAB 검사기가 제공하는 추가 근거 열. eqp는 아래 고정 열에서 표시한다.
  const extraCols = useMemo(
    () => (data?.alert_cols || []).filter(c => c !== "eqp_id" && c !== "eqp_model"),
    [data]);
  const fmtExamples = (a) => (a.examples || [])
    .map(e => [e.root_lot_id, e.wafer_id].filter(Boolean).join("·")).join(", ");

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

  const queueClassify = (a) => {
    const v = inputs[a.id] || {};
    const category = (v.category || "").trim();
    if (!category) { toast.error("분류할 KNOB 카테고리를 입력하세요"); return; }
    setQueued(prev => ({ ...prev, [a.id]: !prev[a.id] }));
  };

  const queueMatchStep = (a) => {
    const v = inputs[a.id] || {};
    const step_desc = (v.step_desc ?? a.step_desc ?? "").trim();
    if (!step_desc) { toast.error("step_desc 를 입력하세요"); return; }
    setQueued(prev => ({ ...prev, [a.id]: !prev[a.id] }));
  };

  const queueAddMask = (a) => {
    const v = inputs[a.id] || {};
    if (!(v.mask || "").trim()) { toast.error("mask 이름을 입력하세요"); return; }
    setQueued(prev => ({ ...prev, [a.id]: !prev[a.id] }));
  };

  const queuedAlerts = useMemo(() => visibleAlerts.filter(a => queued[a.id]), [visibleAlerts, queued]);
  const applyBatch = () => {
    if (!queuedAlerts.length) { toast.error("일괄 반영할 항목을 선택하세요"); return; }
    const changes = [];
    for (const a of queuedAlerts) {
      const v = inputs[a.id] || {};
      if (a.type === "ro_ppid") {
        const category = (v.category || "").trim();
        if (!category) { toast.error(`${a.ppid}: KNOB 분류를 입력하세요`); return; }
        changes.push({ type: "classify_ppid", id: a.id, category,
          feature_name: (v.feature_name || "").trim(), note: (v.note || "").trim() });
      } else if (a.type === "missing_reticle") {
        const mask = (v.mask || "").trim();
        if (!mask) { toast.error(`${a.reticle_id}: mask 이름을 입력하세요`); return; }
        changes.push({ type: "add_mask", id: a.id, mask, note: (v.note || "").trim() });
      } else {
        const step_desc = (v.step_desc ?? a.step_desc ?? "").trim();
        if (!step_desc) { toast.error(`${a.step_id}: 판정 step을 입력하세요`); return; }
        changes.push({ type: "match_step", id: a.id, step_desc, note: (v.note || "").trim() });
      }
    }
    act("__batch__", async () => {
      const r = await postJson(API + "/batch-apply", { changes, note: batchNote.trim() });
      const versions = Object.entries(r.files || {}).map(([file, info]) =>
        `${file} ${info.version_meta?.display_version || info.version_meta?.version || "새 버전"}`);
      toast.ok(`${r.batch_id}: ${r.count}건 일괄 반영${versions.length ? ` — ${versions.join(", ")}` : ""}`);
      setQueued({});
      setBatchNote("");
      setInputs(prev => {
        const next = { ...prev };
        changes.forEach(change => delete next[change.id]);
        return next;
      });
    });
  };

  const ack = (id, status) =>
    act(id, async () => {
      await postJson(API + "/ack", { id, status, note: (inputs[id]?.note || "").trim() });
      toast(status === "active" ? "억제 해제됨" : `상태 기록: ${status}`);
    });

  // 추천 → 판정 입력칸 채우기 (반영은 사람이 누른다)
  const applyReco = (a, step_desc) => {
    setInput(a.id, { step_desc });
    toast(`추천 반영: ${step_desc} — 확인 후 '매칭 추가'를 누르세요`);
  };

  const recheck = (a, force = false) =>
    act(a.id, async () => {
      const r = await postJson(API + "/recommend", { id: a.id, force });
      const rec = (r.records || [])[0] || {};
      if (rec.step_desc) {
        toast.ok(`추천: ${rec.step_desc}` + (rec.llm?.applied ? "" : " (AI 미적용)"));
      } else {
        toast(rec.reason || "추천할 근거를 찾지 못했습니다");
      }
    });

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
      <PageGear title="매칭알람 설정" canEdit={canManage} position="bottom-left">
        <div style={{ display: "grid", gap: 20 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 10 }}>자동 검사 설정</div>
            <MatchingScannerSettings config={data?.config || {}} scanner={data?.scanner || {}}
              canManage={canManage} onChanged={load} />
          </div>
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 18 }}>
            <StepExceptionSettings products={products} canManage={canManage} onChanged={load} />
          </div>
        </div>
      </PageGear>
      {data && !data.ok && (
        <Card title="검사 오류">
          <div style={{ color: "var(--danger, #d66)", fontSize: 13 }}>{data.error}</div>
          <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 6 }}>
            개발 서버의 FAB 경로와 worker 역할 설정을 확인하세요.
          </div>
        </Card>
      )}
      {data?.ok && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 6,
          background: "var(--bg-secondary)",
        }}>
          <label htmlFor="matching-alert-product" style={{ fontSize: 13, fontWeight: 700 }}>제품</label>
          <Filter
            id="matching-alert-product"
            aria-label="매칭알람 제품 선택"
            value={selectedProduct}
            onChange={event => setSelectedProduct(event.target.value)}
            placeholder={`전체 제품 (${alerts.length})`}
            options={products.map(product => ({ value: product, label: `${product} (${productCounts[product] || 0})` }))}
            style={{ minWidth: 220 }}
          />
          <Pill tone={visibleActive ? "danger" : "ok"}>판정 대기 {visibleActive}건</Pill>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            {selectedProduct ? `${selectedProduct} 알람만 표시 중` : `${products.length}개 제품의 알람을 표시 중`}
          </span>
        </div>
      )}
      {data?.ok && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Pill tone="neutral">{selectedProduct || "전체 제품"} · {visibleAlerts.length}건</Pill>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            최근 검사: {data.scanner?.last_product || "-"} · {fmtTs(data.scanner?.last_scan_ts)}
          </span>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            FAB DB: {(data.scanner?.source_roots || []).join(", ") || "폴더설정 확인 필요"}
          </span>
          {/* 검사기가 실제로 도는지는 설정 기어를 열지 않아도 보여야 한다.
              필드가 아예 없는 구버전 응답에서는 아무것도 단정하지 않는다. */}
          {data.scanner?.scanner_alive === undefined ? null
            : data.scanner.scanner_alive
              ? (data.scanner?.scanning?.product
                ? <Pill tone="warn">{data.scanner.scanning.product} 검사 중</Pill>
                : <Pill tone="ok">검사기 대기</Pill>)
              : <Pill tone="danger">검사기 미기동</Pill>}
          {!!data.scanner?.scan_request_hint && (
            <span style={{ color: "var(--muted)", fontSize: 12 }}>{data.scanner.scan_request_hint}</span>
          )}
        </div>
      )}

      <Card
        title="매칭 변경 일괄 반영"
        right={<Pill tone={queuedAlerts.length ? "warn" : "neutral"}>선택 {queuedAlerts.length}건</Pill>}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            각 행을 반영 대기에 넣은 뒤 한 번에 저장합니다. 같은 CSV의 변경은 버전 1개로, 여러 CSV는 같은 배치 ID로 기록됩니다.
          </span>
          <input style={{ ...inputStyle, minWidth: 260, flex: 1 }} placeholder="배치 메모(선택)"
            value={batchNote} onChange={e => setBatchNote(e.target.value)} />
          <Button variant="primary" disabled={!queuedAlerts.length || busy === "__batch__"} onClick={applyBatch}>
            {busy === "__batch__" ? "일괄 반영 중…" : `${queuedAlerts.length}건 일괄 반영`}
          </Button>
          {!!queuedAlerts.length && <Button disabled={busy === "__batch__"} onClick={() => setQueued({})}>선택 초기화</Button>}
        </div>
      </Card>

      <Card
        title="PPID 룰북 (ppid_knob.csv)"
        right={<Pill tone={roAlerts.length ? "danger" : "neutral"}>RO PPID · {roAlerts.length}건</Pill>}
      >
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : roAlerts.length === 0 ? (
          <EmptyState title="RO ppid 알람 없음" hint="knob 매핑에 없는 ppid 가 발견되면 여기에 표시됩니다" />
        ) : (
          <ScrollTable
            rows={roAlerts}
            minWidth={1120}
            columns={["상태", "제품", "대상", "미매칭 PPID", "발견", "KNOB 분류", "작업"]}
            renderRow={a => (
                  <tr key={a.id}>
                    <td style={nowrapCell} title={decisionTitle(a)}>
                      <Pill tone={statusTone(a)} style={{ fontSize: 12 }}>{statusLabel(a)}</Pill>
                    </td>
                    <td style={{ ...nowrapCell, fontWeight: 700 }}>{a.product || a.vehicle || "-"}</td>
                    <td style={compactCell} title={[a.vehicle, a.step_id, a.step_desc].filter(Boolean).join(" · ")}>
                      <b>{a.feature_name || a.split || "-"}</b>
                      <span style={inlineMetaStyle}> · {a.step_id} · {a.step_desc || "-"}</span>
                    </td>
                    <td style={{ ...nowrapCell, fontFamily: "monospace", fontWeight: 700 }}>{a.ppid}</td>
                    <td style={compactCell} title={a.split}>
                      <span>{a.n_lots} lot · {a.rows} row</span>
                      <span style={inlineMetaStyle}> · {fmtTs(a.first_seen_ts)} · {a.split}</span>
                    </td>
                    <td style={nowrapCell}>
                      <input style={inputStyle} placeholder="예: KNOB_NEW"
                        aria-label={`${a.ppid} KNOB 분류`}
                        value={inputs[a.id]?.category ?? ""}
                        onChange={e => setInput(a.id, { category: e.target.value })}
                        disabled={!!a.decision} />
                    </td>
                    <td style={nowrapCell}>
                      <Button style={compactButtonStyle} variant={queued[a.id] ? "primary" : undefined}
                        disabled={!!busy || !!a.decision} onClick={() => queueClassify(a)}>
                        {queued[a.id] ? "대기 해제" : "반영 대기"}
                      </Button>{" "}
                      <HoldButtons alert={a} busy={busy === a.id} onAck={ack} />
                    </td>
                  </tr>
            )}
          />
        )}
      </Card>

      <Card
        title="매칭테이블 (Vehicle_matching.csv)"
        right={<Pill tone={umAlerts.length ? "danger" : "neutral"}>미매칭 step · {umAlerts.length}건</Pill>}
      >
        <div
          title="추천 function step은 같은 PPID를 쓰는 매칭 완료 step의 step_desc를 우선 제시합니다. 같은 PPID가 없을 때는 FAB의 eqp_id·eqp_model·area와 step_id 근접도를 비교하며, 최종 반영은 사용자가 확인합니다."
          style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
        >
          동일 PPID의 매칭 완료 step_desc를 우선 추천합니다. 적용 후 ‘매칭 추가’로 확정하세요.
        </div>
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : umAlerts.length === 0 ? (
          <EmptyState title="미매칭 step 알람 없음" hint="vehicle_matching 에 없는 step 이 발견되면 여기에 표시됩니다" />
        ) : (
          <ScrollTable
            rows={umAlerts}
            minWidth={1340}
            columns={["상태", "제품", "대상", "추천 function step", "근거", "발견", "판정 step", "작업"]}
            renderRow={a => (
                  <tr key={a.id}>
                    <td style={nowrapCell} title={decisionTitle(a)}>
                      <Pill tone={statusTone(a)} style={{ fontSize: 12 }}>{statusLabel(a)}</Pill>
                    </td>
                    <td style={{ ...nowrapCell, fontWeight: 700 }}>{a.product || a.vehicle || "-"}</td>
                    <td style={nowrapCell}>
                      <b>{a.vehicle}</b>
                      <span style={{ ...inlineMetaStyle, fontFamily: "monospace" }}> · {a.step_id}</span>
                    </td>
                    <td style={nowrapCell}>
                      <RecoCell alert={a} busy={busy === a.id}
                        onApply={applyReco} onRecheck={recheck} />
                    </td>
                    <td
                      style={{ ...compactCell, fontFamily: "monospace", fontSize: 11 }}
                      title={[
                        // 예외 규칙에 그대로 쓸 수 있게 후보값 전체를 툴팁에 편다.
                        (a.eqp_models || []).length && `eqp_model ${a.eqp_models.join(", ")}`,
                        (a.eqp_ids || []).length && `eqp_id ${a.eqp_ids.join(", ")}`,
                        (a.areas || []).length && `area ${a.areas.join(", ")}`,
                        (a.ppids || []).length && `ppid ${a.ppids.join(", ")}`,
                        ...extraCols.map(c => `${c} ${a[c] || "-"}`),
                        fmtExamples(a) && `lot·wafer ${fmtExamples(a)}`,
                      ].filter(Boolean).join("\n")}
                    >
                      {a.eqp_id || "eqp -"}{a.eqp_model ? ` (${a.eqp_model})` : ""}
                      {a.area ? ` · area ${a.area}` : ""}
                      {extraCols.map(c => <span key={c}> · {c} {a[c] || "-"}</span>)}
                      {fmtExamples(a) && <span style={inlineMetaStyle}> · {fmtExamples(a)}</span>}
                    </td>
                    <td style={nowrapCell}>
                      {a.n_lots} lot · {a.rows} row
                      <span style={inlineMetaStyle}> · {fmtTs(a.first_seen_ts)}</span>
                    </td>
                    <td style={nowrapCell}>
                      <input style={{ ...inputStyle, minWidth: 150 }}
                        aria-label={`${a.vehicle} ${a.step_id} 판정 step`}
                        value={inputs[a.id]?.step_desc ?? a.step_desc ?? ""}
                        onChange={e => setInput(a.id, { step_desc: e.target.value })}
                        disabled={!!a.decision} />
                    </td>
                    <td style={nowrapCell}>
                      <Button style={compactButtonStyle} variant={queued[a.id] ? "primary" : undefined}
                        disabled={!!busy || !!a.decision} onClick={() => queueMatchStep(a)}>
                        {queued[a.id] ? "대기 해제" : "반영 대기"}
                      </Button>{" "}
                      <HoldButtons alert={a} busy={busy === a.id} onAck={ack} />
                    </td>
                  </tr>
            )}
          />
        )}
      </Card>

      <Card
        title="마스크 룰북 (mask_info.csv)"
        right={<Pill tone={maskAlerts.length ? "danger" : "neutral"}>미등록 reticle · {maskAlerts.length}건</Pill>}
      >
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
          FAB DB의 reticle_id 중 mask_info.csv의 reticle_id 열에 없는 값입니다.
          mask_info.csv는 제품 구분 없이 reticle_id·mask 2열이라 같은 reticle이 여러 제품에서 발견돼도 한 줄로 묶입니다.
        </div>
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : maskAlerts.length === 0 ? (
          <EmptyState title="미등록 reticle 알람 없음" hint="mask_info.csv에 없는 reticle_id 가 발견되면 여기에 표시됩니다" />
        ) : (
          <ScrollTable
            rows={maskAlerts}
            minWidth={1000}
            columns={["상태", "RETICLE ID", "발견 제품", "발견 step", "발견", "mask 이름", "작업"]}
            renderRow={a => {
                  const products = (a.products || [a.product]).filter(Boolean);
                  const steps = a.step_ids || (a.step_id ? [a.step_id] : []);
                  return (
                    <tr key={a.id}>
                      <td style={nowrapCell} title={decisionTitle(a)}>
                        <Pill tone={statusTone(a)} style={{ fontSize: 12 }}>{statusLabel(a)}</Pill>
                      </td>
                      <td style={{ ...nowrapCell, fontFamily: "monospace", fontWeight: 700 }}>{a.reticle_id}</td>
                      <td style={compactCell} title={products.join(", ")}>{products.join(", ") || "-"}</td>
                      <td style={{ ...compactCell, fontFamily: "monospace", fontSize: 11 }}
                        title={steps.join(", ")}>
                        {steps.slice(0, 3).join(", ") || "-"}
                        {steps.length > 3 && <span style={inlineMetaStyle}> 외 {steps.length - 3}</span>}
                      </td>
                      <td style={compactCell}>
                        <span>{a.n_lots} lot · {a.rows} row</span>
                        <span style={inlineMetaStyle}> · {fmtTs(a.first_seen_ts)}</span>
                      </td>
                      <td style={nowrapCell}>
                        <input style={inputStyle} placeholder="예: MASK_A_v1"
                          aria-label={`${a.reticle_id} mask 이름`}
                          value={inputs[a.id]?.mask ?? ""}
                          onChange={e => setInput(a.id, { mask: e.target.value })}
                          disabled={!!a.decision} />
                      </td>
                      <td style={nowrapCell}>
                        <Button style={compactButtonStyle} variant={queued[a.id] ? "primary" : undefined}
                          disabled={!!busy || !!a.decision} onClick={() => queueAddMask(a)}>
                          {queued[a.id] ? "대기 해제" : "반영 대기"}
                        </Button>{" "}
                        <HoldButtons alert={a} busy={busy === a.id} onAck={ack} />
                      </td>
                    </tr>
                  );
            }}
          />
        )}
      </Card>

      <Card title="판정 이력">
        {visibleDecisions.length === 0 ? (
          <EmptyState title="판정 이력 없음" hint="룰 반영/매칭 추가/보류 처리 내역이 여기에 남습니다" />
        ) : (
          <ScrollTable
            rows={visibleDecisions}
            minWidth={820}
            columns={["일시", "제품", "배치", "알람", "판정", "내용", "파일", "판정자"]}
            renderRow={(d, i) => (
                  <tr key={i}>
                    <td style={nowrapCell}>{fmtTs(d.ts)}</td>
                    <td style={compactCell} title={alertProducts(d).join(", ")}>
                      {alertProducts(d).join(", ") || "-"}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: 11 }} title={d.batch_id || ""}>
                      {d.batch_id ? d.batch_id.split("-").slice(-1)[0] : "-"}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: 11 }}>{d.alert_id}</td>
                    <td style={cellStyle}>
                      <Pill tone={["classify", "match", "add_mask"].includes(d.action) ? "ok" : "neutral"}>
                        {d.action === "classify" ? "룰 반영"
                          : d.action === "match" ? "매칭 추가"
                            : d.action === "add_mask" ? "마스크 추가" : d.action}
                      </Pill>
                    </td>
                    <td style={cellStyle}>
                      {d.action === "classify"
                        ? `${d.ppid} → ${d.category} (${d.feature_name} ${d.rule_order})`
                        : d.action === "match"
                          ? `${d.step_id} → ${d.step_desc}`
                          : d.action === "add_mask"
                            ? `${d.reticle_id} → ${d.mask}`
                            : (d.detail || "-")}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: 11 }}>{d.file || "-"}</td>
                    <td style={cellStyle}>{d.by}</td>
                  </tr>
            )}
          />
        )}
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>
          파일 단위 버전 이력(스냅샷/롤백)은 파일탐색기 › 해당 csv 의 버전 기록에서 확인할 수 있습니다.
        </div>
      </Card>
    </div>
  );
}
