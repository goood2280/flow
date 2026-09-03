/* My_ValveAlerts.jsx — 개발 서버 FAB 매칭 검사.
   - 개발 worker가 FAB 제품을 하나씩 순회하며 처음 보는 step_id/ppid/reticle_id를 표시한다.
   - step_id는 Vehicle_matching.csv, ppid는 ppid_knob.csv, reticle_id는 mask_info.csv에
     엔지니어 판정으로 반영한다 (reticle_id→mask 규칙은 전 제품 공용이며
     매칭채우기가 product/step_id/step_desc 메타데이터를 보강할 수 있다).
   - 판정 이력(누가/언제/무엇으로) + 반영불필요 상태를 관리한다.
*/
import { useEffect, useMemo, useRef, useState } from "react";
import { sf, postJson, putJson } from "../../lib/api";
import { toast } from "../../components/Toast";
import { Button, Card, EmptyState, Filter, LinkBtn, Pill } from "../../components/UXKit";
import PageGear from "../../components/PageGear";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows, spreadsheetTextFromRows } from "../../components/SpreadsheetPasteGrid";
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
  const canonical = input => String(input || "").trim().replace(/^ML_TABLE_/i, "").toLocaleLowerCase();
  return canonical(value) === canonical(selectedProduct);
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

function DecisionSpreadsheet({ title, columns, sourceRows, aliases, columnLabels,
                               editableColumn, disabled, onRowsChange }) {
  const rows = useMemo(() => normalizeSpreadsheetRows(sourceRows, columns, {
    minRows: sourceRows.length,
    maxRows: sourceRows.length,
  }), [sourceRows, columns]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(spreadsheetTextFromRows(sourceRows, columns));
      toast.ok(`${title}: ${sourceRows.length}행을 스프레드시트 형식으로 복사했습니다`);
    } catch (error) {
      toast.error(`클립보드 복사 실패: ${String(error?.message || error)}`);
    }
  };
  if (!sourceRows.length) return null;
  return (
    <div style={{ marginBottom: 12, display: "grid", gap: 8 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <Button style={compactButtonStyle} onClick={copy}>표 복사</Button>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>
          읽기 전용 열로 대상을 확인하고 <b>{columnLabels[editableColumn] || editableColumn}</b> 열에
          Excel 값을 한 열로 붙여넣으세요. 값이 있는 행은 즉시 반영대기가 됩니다.
        </span>
      </div>
      <SpreadsheetPasteGrid
        ariaLabel={`${title} 일괄 분류 스프레드시트`}
        columns={columns}
        rows={rows}
        onChange={onRowsChange}
        aliases={aliases}
        columnLabels={columnLabels}
        readOnlyColumns={columns.filter(column => column !== editableColumn)}
        disabled={disabled}
        minRows={sourceRows.length}
        maxRows={sourceRows.length}
        maxHeight={360}
        minTableWidth={Math.max(620, columns.length * 175)}
      />
    </div>
  );
}

function discoveryText(alert) {
  const example = (alert.examples || [])[0] || {};
  const lotId = example.lot_id || example.root_lot_id || alert.lot_id || alert.root_lot_id || "-";
  const waferId = example.wafer_id || alert.wafer_id || "-";
  return `${alert.n_lots || 0} lot · ${alert.rows || 0} row · lot_id ${lotId} · wafer_id ${waferId} · ${fmtTs(alert.first_seen_ts)}`;
}

/* 미매칭 step 의 function step 추천 (backend/core/valve_step_advisor.py).
   - 동일 AREA를 필수 조건으로 두고 PPID → EQP → 설비모델 순으로 고르며,
   - step_desc는 선택된 step_id의 Vehicle_matching.csv 행에서 가져온다.
   판정 입력칸에 값을 넣어주는 보조일 뿐, 반영은 사람이 누른다. */
const METHOD_LABEL = {
  ai: "AI 판단", ppid: "동일 PPID", eqp_id: "동일 EQP",
  eqp_model: "동일 설비모델", area: "동일 AREA",
  signature: "FAB 근거", distance: "step_id 근접", none: "근거 없음",
};

function recommendationText(alert) {
  const r = alert.recommendation;
  if (!r) return "추천 대기";
  const confidence = r.llm?.applied ? ` · AI ${Math.round((Number(r.confidence) || 0) * 100)}%` : "";
  const picked = r.picked_step_id ? ` · ${r.picked_step_id}` : "";
  return `${r.step_desc || "-"} · ${METHOD_LABEL[r.method] || r.method || "근거 없음"}${picked}${confidence}`;
}

function matchingEvidenceText(alert, extraCols) {
  return [
    alert.eqp_id && `eqp_id ${alert.eqp_id}`,
    alert.eqp_model && `eqp_model ${alert.eqp_model}`,
    alert.area && `area ${alert.area}`,
    (alert.ppids || []).length && `ppid ${(alert.ppids || []).join(", ")}`,
    ...extraCols.map(column => alert[column] && `${column} ${alert[column]}`),
  ].filter(Boolean).join(" · ");
}

export default function My_ValveAlerts({ user }) {
  const [data, setData] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [inputs, setInputs] = useState({});   // alert_id -> {category, feature_name, step_desc}
  const [queued, setQueued] = useState({});   // alert_id -> true (일괄 반영 대기)
  const [batchNote, setBatchNote] = useState("");
  const [planAnomalyNote, setPlanAnomalyNote] = useState("");
  const [selectedPlanAnomalies, setSelectedPlanAnomalies] = useState({});
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
  const planAnomalies = useMemo(() => data?.plan_anomalies?.items || [], [data?.plan_anomalies?.items]);
  const products = useMemo(() => {
    const names = new Map();
    for (const product of data?.scanner?.product_list || []) {
      const value = String(product || "").trim();
      if (value) names.set(value.toLocaleLowerCase(), value);
    }
    for (const alert of alerts) {
      for (const product of alertProducts(alert)) names.set(product.toLocaleLowerCase(), product);
    }
    for (const item of planAnomalies) {
      const product = String(item.product_key || item.product || "").trim();
      if (product) names.set(product.toLocaleLowerCase(), product);
    }
    return Array.from(names.values()).sort((a, b) => a.localeCompare(b, "ko", { numeric: true }));
  }, [alerts, data?.scanner?.product_list, planAnomalies]);
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
  const visiblePlanAnomalies = useMemo(() => selectedProduct
    ? planAnomalies.filter(item => productMatches(item.product_key || item.product, selectedProduct)
      || productMatches(item.product, selectedProduct))
    : planAnomalies,
  [planAnomalies, selectedProduct]);
  const readyPlanAnomalies = useMemo(
    () => visiblePlanAnomalies.filter(item => item.ready), [visiblePlanAnomalies]);
  const checkedPlanAnomalies = useMemo(
    () => planAnomalies.filter(item => selectedPlanAnomalies[item.id]),
    [planAnomalies, selectedPlanAnomalies]);
  const alertsById = useMemo(
    () => new Map(alerts.map(alert => [alert.id, alert])),
    [alerts]);
  // decisions는 최신순이다. 같은 알람을 불필요→취소→불필요로 여러 번 바꿔도
  // 판정 이력에서는 현재 상태를 만든 가장 최근 ACK 한 건만 취소할 수 있어야 한다.
  const latestAckByAlert = useMemo(() => {
    const latest = new Map();
    for (const decision of decisions) {
      if (decision.type === "ack" && decision.alert_id && !latest.has(decision.alert_id)) {
        latest.set(decision.alert_id, decision);
      }
    }
    return latest;
  }, [decisions]);
  useEffect(() => {
    if (selectedProduct && !products.some(product => productMatches(product, selectedProduct))) {
      setSelectedProduct("");
    }
  }, [products, selectedProduct]);
  useEffect(() => {
    const current = new Set(planAnomalies.map(item => item.id));
    setSelectedPlanAnomalies(prev => Object.fromEntries(
      Object.entries(prev).filter(([id, checked]) => checked && current.has(id))));
  }, [planAnomalies]);
  // FAB 검사기가 제공하는 추가 근거 열. eqp는 아래 고정 열에서 표시한다.
  const extraCols = useMemo(
    () => (data?.alert_cols || []).filter(c => c !== "eqp_id" && c !== "eqp_model"),
    [data]);
  const editableRoAlerts = useMemo(
    () => roAlerts.filter(alert => alert.status === "active" && !alert.decision),
    [roAlerts]);
  const editableStepAlerts = useMemo(
    () => umAlerts.filter(alert => alert.status === "active" && !alert.decision),
    [umAlerts]);
  const editableMaskAlerts = useMemo(
    () => maskAlerts.filter(alert => alert.status === "active" && !alert.decision),
    [maskAlerts]);
  const roCsvRows = useMemo(() => editableRoAlerts.map(alert => ({
    status: queued[alert.id] ? "반영대기" : "입력대기",
    product: alert.product || alert.vehicle || "",
    feature_name: alert.feature_name || "",
    step: [alert.step_id, alert.step_desc].filter(Boolean).join(" · "),
    ppid: alert.ppid || "",
    discovery: discoveryText(alert),
    category: inputs[alert.id]?.category || "",
  })), [editableRoAlerts, inputs, queued]);
  const stepCsvRows = useMemo(() => editableStepAlerts.map(alert => ({
    status: queued[alert.id] ? "반영대기" : "입력대기",
    product: alert.product || alert.vehicle || "",
    step_id: alert.step_id || "",
    recommendation: recommendationText(alert),
    evidence: matchingEvidenceText(alert, extraCols),
    discovery: discoveryText(alert),
    step_desc: inputs[alert.id]?.step_desc ?? alert.step_desc ?? "",
  })), [editableStepAlerts, extraCols, inputs, queued]);
  const maskCsvRows = useMemo(() => editableMaskAlerts.map(alert => ({
    status: queued[alert.id] ? "반영대기" : "입력대기",
    products: alertProducts(alert).join(", "),
    reticle_id: alert.reticle_id || "",
    step_ids: (alert.step_ids || (alert.step_id ? [alert.step_id] : [])).join(", "),
    discovery: discoveryText(alert),
    mask: inputs[alert.id]?.mask || "",
  })), [editableMaskAlerts, inputs, queued]);

  const updateDecisionValues = (targetAlerts, rows, field) => {
    setInputs(prev => {
      const next = { ...prev };
      targetAlerts.forEach((alert, index) => {
        next[alert.id] = { ...(next[alert.id] || {}), [field]: rows[index]?.[field] ?? "" };
      });
      return next;
    });
    setQueued(prev => {
      const next = { ...prev };
      targetAlerts.forEach((alert, index) => {
        if (String(rows[index]?.[field] || "").trim()) next[alert.id] = true;
        else delete next[alert.id];
      });
      return next;
    });
  };

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

  const forceScan = () =>
    act("__force_scan__", async () => {
      const result = await postJson(API + "/poll", {});
      const message = result.message || "개발 worker에 다음 제품 강제 검사를 요청했습니다";
      if (result.scanner_alive === false) toast.error(message);
      else toast.ok(message);
    });

  const queuedAlerts = useMemo(() => alerts.filter(a => queued[a.id]), [alerts, queued]);
  const applyBatch = () => {
    if (!queuedAlerts.length) { toast.error("일괄 반영할 항목을 선택하세요"); return; }
    const changes = [];
    for (const a of queuedAlerts) {
      const v = inputs[a.id] || {};
      if (a.type === "ro_ppid") {
        const category = (v.category || "").trim();
        if (!category) { toast.error(`${a.ppid}: KNOB 분류를 입력하세요`); return; }
        changes.push({ type: "classify_ppid", id: a.id, category,
          feature_name: (v.feature_name ?? a.feature_name ?? "").trim(),
          note: (v.note || "").trim() });
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

  const clearQueuedValues = () => {
    setInputs(prev => {
      const next = { ...prev };
      queuedAlerts.forEach(alert => {
        const field = alert.type === "ro_ppid" ? "category"
          : alert.type === "missing_reticle" ? "mask" : "step_desc";
        next[alert.id] = { ...(next[alert.id] || {}), [field]: "" };
      });
      return next;
    });
    setQueued({});
  };

  const togglePlanAnomaly = (id, checked) => setSelectedPlanAnomalies(prev => {
    const next = { ...prev };
    if (checked) next[id] = true;
    else delete next[id];
    return next;
  });
  const allVisiblePlanAnomaliesChecked = readyPlanAnomalies.length > 0
    && readyPlanAnomalies.every(item => selectedPlanAnomalies[item.id]);
  const toggleAllPlanAnomalies = () => setSelectedPlanAnomalies(prev => {
    const next = { ...prev };
    if (allVisiblePlanAnomaliesChecked) readyPlanAnomalies.forEach(item => delete next[item.id]);
    else readyPlanAnomalies.forEach(item => { next[item.id] = true; });
    return next;
  });
  const applyPlanAnomalies = () => {
    if (!checkedPlanAnomalies.length) { toast.error("반영할 SplitTable plan 이상항목을 선택하세요"); return; }
    if (!planAnomalyNote.trim()) { toast.error("반영 코멘트를 입력하세요"); return; }
    act("__plan_anomalies__", async () => {
      const result = await postJson(API + "/plan-anomalies/apply", {
        ids: checkedPlanAnomalies.map(item => item.id), note: planAnomalyNote.trim(),
      });
      const version = result.version_meta?.display_version || result.version_meta?.version || "새 버전";
      toast.ok(`${result.count}건을 ppid_knob.csv에 반영했습니다 · ${version}`);
      setSelectedPlanAnomalies({});
      setPlanAnomalyNote("");
    });
  };

  const ack = (id, status) =>
    act(id, async () => {
      await postJson(API + "/ack", { id, status, note: (inputs[id]?.note || "").trim() });
      toast(status === "active" ? "불필요 처리가 취소되어 판정 대기로 돌아갔습니다" : `상태 기록: ${status}`);
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
            style={{ width: "18ch", minWidth: "18ch", maxWidth: "100%", flex: "0 1 18ch" }}
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
          {data.scanner?.scanner_alive !== undefined && (
            <Button
              style={compactButtonStyle}
              disabled={!canManage || !!busy || !!data.scanner?.scanning?.product}
              onClick={forceScan}
              title={data.scanner?.scanning?.product
                ? "현재 제품 검사가 끝난 뒤 실행할 수 있습니다"
                : "자동 검사 대기 시간을 건너뛰고 다음 제품을 즉시 검사합니다"}
            >
              {busy === "__force_scan__" ? "실행 요청 중…" : "강제 실행"}
            </Button>
          )}
          {!!data.scanner?.scan_request_hint && (
            <span style={{ color: "var(--muted)", fontSize: 12 }}>{data.scanner.scan_request_hint}</span>
          )}
        </div>
      )}

      <Card
        title="매칭 변경 일괄 반영"
        right={<Pill tone={queuedAlerts.length ? "warn" : "neutral"}>반영대기 {queuedAlerts.length}건</Pill>}
      >
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            분류 값을 입력하거나 Excel 열을 붙여넣으면 자동으로 반영대기가 됩니다. 일괄반영 시 같은 CSV는 버전 1개로, 여러 CSV는 같은 배치 ID로 기록됩니다.
          </span>
          <input style={{ ...inputStyle, minWidth: 260, flex: 1 }} placeholder="배치 메모(선택)"
            value={batchNote} onChange={e => setBatchNote(e.target.value)} />
          <Button variant="primary" disabled={!queuedAlerts.length || busy === "__batch__"} onClick={applyBatch}>
            {busy === "__batch__" ? "일괄 반영 중…" : `${queuedAlerts.length}건 일괄 반영`}
          </Button>
          {!!queuedAlerts.length && <Button disabled={busy === "__batch__"} onClick={clearQueuedValues}>대기값 초기화</Button>}
        </div>
      </Card>

      <Card
        title="PPID 룰북 (ppid_knob.csv)"
        right={<Pill tone={editableRoAlerts.length ? "danger" : "neutral"}>RO PPID · {editableRoAlerts.length}건</Pill>}
      >
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : editableRoAlerts.length === 0 ? (
          <EmptyState title="판정 대기 RO ppid 없음" hint="knob 매핑에 없는 ppid가 발견되면 여기에 표시됩니다" />
        ) : (
          <DecisionSpreadsheet
            title="PPID 룰북"
            columns={["status", "product", "feature_name", "step", "ppid", "discovery", "category"]}
            sourceRows={roCsvRows}
            aliases={{ value: "ppid", "기능명": "feature_name", "분류": "category" }}
            columnLabels={{ status: "상태", product: "제품", feature_name: "기능명", step: "대상 step", ppid: "미매칭 PPID", discovery: "발견 근거", category: "KNOB 분류" }}
            editableColumn="category"
            disabled={!canManage || !!busy}
            onRowsChange={rows => updateDecisionValues(editableRoAlerts, rows, "category")}
          />
        )}
      </Card>

      <Card
        title="매칭테이블 (Vehicle_matching.csv)"
        right={<Pill tone={editableStepAlerts.length ? "danger" : "neutral"}>미매칭 step · {editableStepAlerts.length}건</Pill>}
      >
        <div
          title="추천 function step은 동일 area의 매칭 완료 step만 후보로 두고, FAB의 동일 PPID → 동일 eqp_id → 동일 eqp_model → step_id 근접 순서로 선택합니다. step_desc는 선택된 step_id의 Vehicle_matching.csv 값이며, 최종 반영은 사용자가 확인합니다."
          style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
        >
          동일 AREA 안에서 PPID → EQP → 설비모델 순으로 매칭된 step_desc를 추천합니다. 추천을 확인해 판정 function step 열에 입력하면 반영대기에 들어갑니다.
        </div>
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : editableStepAlerts.length === 0 ? (
          <EmptyState title="판정 대기 미매칭 step 없음" hint="vehicle_matching에 없는 step이 발견되면 여기에 표시됩니다" />
        ) : (
          <DecisionSpreadsheet
            title="Vehicle 매칭테이블"
            columns={["status", "product", "step_id", "recommendation", "evidence", "discovery", "step_desc"]}
            sourceRows={stepCsvRows}
            aliases={{ "스텝": "step_id", "function_step": "step_desc", "판정_step": "step_desc" }}
            columnLabels={{ status: "상태", product: "제품", step_id: "미매칭 step_id", recommendation: "추천 function step", evidence: "추천 근거", discovery: "발견 근거", step_desc: "판정 function step" }}
            editableColumn="step_desc"
            disabled={!canManage || !!busy}
            onRowsChange={rows => updateDecisionValues(editableStepAlerts, rows, "step_desc")}
          />
        )}
      </Card>

      <Card
        title="마스크 룰북 (mask_info.csv)"
        right={<Pill tone={editableMaskAlerts.length ? "danger" : "neutral"}>미등록 reticle · {editableMaskAlerts.length}건</Pill>}
      >
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
          FAB DB의 reticle_id 중 mask_info.csv의 reticle_id 열에 없는 값입니다.
          mask_info.csv는 제품 구분 없이 reticle_id·mask 2열이라 같은 reticle이 여러 제품에서 발견돼도 한 줄로 묶입니다.
        </div>
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div> : editableMaskAlerts.length === 0 ? (
          <EmptyState title="판정 대기 미등록 reticle 없음" hint="mask_info.csv에 없는 reticle_id가 발견되면 여기에 표시됩니다" />
        ) : (
          <DecisionSpreadsheet
            title="마스크 룰북"
            columns={["status", "products", "reticle_id", "step_ids", "discovery", "mask"]}
            sourceRows={maskCsvRows}
            aliases={{ "reticle": "reticle_id", "마스크": "mask" }}
            columnLabels={{ status: "상태", products: "발견 제품", reticle_id: "RETICLE ID", step_ids: "발견 step", discovery: "발견 근거", mask: "mask 이름" }}
            editableColumn="mask"
            disabled={!canManage || !!busy}
            onRowsChange={rows => updateDecisionValues(editableMaskAlerts, rows, "mask")}
          />
        )}
      </Card>

      <Card
        title="SplitTable plan 이상항목들"
        right={<Pill tone={visiblePlanAnomalies.length ? "danger" : "neutral"}>
          plan 불일치 · {visiblePlanAnomalies.length}건
        </Pill>}
      >
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, lineHeight: 1.5 }}>
          KNOB plan과 실제 PPID가 다른 항목입니다. 같은 제품·KNOB·plan·PPID는 여러 lot/wafer에서 발견돼도 한 줄로 묶입니다.
          선택해 반영하면 실제 PPID를 plan 이름으로 <b>ppid_knob.csv</b>에 추가하거나 기존 분류를 수정합니다.
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
          <Button style={compactButtonStyle} disabled={!canManage || !readyPlanAnomalies.length || !!busy}
            onClick={toggleAllPlanAnomalies}>
            {allVisiblePlanAnomaliesChecked ? "현재 목록 선택 해제" : `반영 가능 ${readyPlanAnomalies.length}건 전체 선택`}
          </Button>
          <input
            style={{ ...inputStyle, minWidth: 280, flex: 1 }}
            placeholder="반영 코멘트(필수)"
            value={planAnomalyNote}
            disabled={!canManage || busy === "__plan_anomalies__"}
            onChange={event => setPlanAnomalyNote(event.target.value)}
          />
          <Button variant="primary"
            disabled={!canManage || !checkedPlanAnomalies.length || !planAnomalyNote.trim() || !!busy}
            onClick={applyPlanAnomalies}>
            {busy === "__plan_anomalies__" ? "반영 중…" : `선택 ${checkedPlanAnomalies.length}건 PPID 룰 반영`}
          </Button>
        </div>
        {loading ? <div style={{ color: "var(--muted)" }}>불러오는 중…</div>
          : visiblePlanAnomalies.length === 0 ? (
            <EmptyState title="SplitTable plan 이상항목 없음" hint="KNOB plan과 실제 PPID가 달라지면 여기에 표시됩니다" />
          ) : (
            <ScrollTable
              rows={visiblePlanAnomalies}
              minWidth={1120}
              columns={["선택", "제품", "KNOB 항목", "plan 이름", "실제 PPID", "적용 공정", "발견 위치", "현재 분류", "반영 방식"]}
              renderRow={item => {
                const locations = (item.locations || []).map(location =>
                  `${location.root_lot_id || "-"}${location.wafer_id ? ` WF${location.wafer_id}` : ""}`).join(", ");
                return (
                  <tr key={item.id}>
                    <td style={nowrapCell}>
                      <input type="checkbox" checked={!!selectedPlanAnomalies[item.id]}
                        disabled={!canManage || !item.ready || !!busy}
                        aria-label={`${item.feature_name} ${item.actual_ppid} 반영 선택`}
                        onChange={event => togglePlanAnomaly(item.id, event.target.checked)} />
                    </td>
                    <td style={nowrapCell}>{item.product_key || item.product || "-"}</td>
                    <td style={compactCell} title={item.column || ""}>{item.feature_name || item.column || "-"}</td>
                    <td style={{ ...cellStyle, fontWeight: 700, color: "var(--accent)" }}>{item.plan || "-"}</td>
                    <td style={{ ...cellStyle, fontFamily: "monospace" }}>{item.actual_ppid || "-"}</td>
                    <td style={compactCell} title={(item.step_ids || []).join(", ")}>
                      {item.step_desc || "-"}{(item.step_ids || []).length ? ` · ${(item.step_ids || []).join(", ")}` : ""}
                    </td>
                    <td style={compactCell} title={locations}>
                      {locations || "-"}{item.occurrences > (item.locations || []).length ? ` 외 ${item.occurrences - item.locations.length}건` : ""}
                    </td>
                    <td style={compactCell}>{(item.current_categories || []).join(", ") || "-"}</td>
                    <td style={nowrapCell} title={item.reason || ""}>
                      {item.ready ? (
                        <Pill tone={item.mode === "update" ? "warn" : "ok"}>
                          {item.mode === "update" ? "기존 룰 수정" : "새 룰 추가"}
                        </Pill>
                      ) : <Pill tone="danger">공정 확인 필요</Pill>}
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
            columns={["일시", "제품", "배치", "알람", "판정", "내용", "파일", "판정자", "작업"]}
            renderRow={(d, i) => {
              const currentAlert = alertsById.get(d.alert_id);
              const canCancelUnnecessary = canManage
                && d.action === "반영불필요"
                && latestAckByAlert.get(d.alert_id) === d
                && currentAlert?.status === "반영불필요";
              return (
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
                      <Pill tone={["classify", "match", "add_mask", "plan_knob"].includes(d.action) ? "ok" : "neutral"}>
                        {d.action === "classify" ? "룰 반영"
                          : d.action === "match" ? "매칭 추가"
                            : d.action === "add_mask" ? "마스크 추가"
                              : d.action === "plan_knob" ? "plan PPID 반영" : d.action}
                      </Pill>
                    </td>
                    <td style={cellStyle}>
                      {d.action === "plan_knob"
                        ? `${d.feature_name}: ${d.ppid} → ${d.category} (${d.change_mode === "update" ? "수정" : "추가"})`
                        : d.action === "classify"
                        ? `${d.ppid} → ${d.category} (${d.feature_name} ${d.rule_order})`
                        : d.action === "match"
                          ? `${d.step_id} → ${d.step_desc}`
                          : d.action === "add_mask"
                            ? `${d.reticle_id} → ${d.mask}`
                            : (d.detail || "-")}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: 11 }}>{d.file || "-"}</td>
                    <td style={cellStyle}>{d.by}</td>
                    <td style={nowrapCell}>
                      {canCancelUnnecessary ? (
                        <Button style={compactButtonStyle} disabled={busy === d.alert_id}
                          onClick={() => ack(d.alert_id, "active")}
                          title="불필요 판정을 취소하고 다시 판정 대기로 되돌리기">
                          불필요 취소
                        </Button>
                      ) : d.type === "ack" && d.action === "active" ? (
                        <span style={{ color: "var(--muted)", fontSize: 12 }}>취소됨</span>
                      ) : "-"}
                    </td>
                  </tr>
              );
            }}
          />
        )}
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>
          파일 단위 버전 이력(스냅샷/롤백)은 파일탐색기 › 해당 csv 의 버전 기록에서 확인할 수 있습니다.
        </div>
      </Card>
    </div>
  );
}
