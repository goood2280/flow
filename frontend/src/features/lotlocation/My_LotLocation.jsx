import { useMemo, useState } from "react";
import SpreadsheetPasteGrid, {
  normalizeSpreadsheetRows,
} from "../../components/SpreadsheetPasteGrid";
import { toast } from "../../components/Toast";
import {
  Button,
  Card,
  Input,
  PageShell,
  Pill,
  Toolbar,
} from "../../components/ui";
import { sf } from "../../lib/api";

const LOT_INPUT_COLUMNS = ["lot_id"];
const SAMPLE_LOT_IDS = ["A1022A.2", "A1027C.1", "B1008A.1"];

function createRowsFromLotList(lotList, minRows = 16, maxRows = 1000) {
  const rows = (lotList || []).map((lot) => ({ lot_id: String(lot || "").trim() }));
  return normalizeSpreadsheetRows(rows, LOT_INPUT_COLUMNS, { minRows, maxRows });
}

function extractLotIdsFromRows(rows) {
  const result = [];
  const seen = new Set();
  const headerKeys = new Set(["LOT_ID", "LOTID", "LOT ID", "ROOT_LOT_ID", "ROOT_LOT"]);

  for (const row of rows || []) {
    const raw = String(row?.lot_id || "").trim();
    if (!raw) continue;
    const tokens = raw.split(/[\r\n\t,;\s]+/).map((s) => s.trim()).filter(Boolean);
    for (const token of tokens) {
      const upper = token.toUpperCase();
      if (headerKeys.has(upper)) continue;
      if (!seen.has(upper)) {
        seen.add(upper);
        result.push(token);
      }
    }
  }
  return result;
}

export default function My_LotLocation() {
  const [inputRows, setInputRows] = useState(() => createRowsFromLotList([], 16));
  const [matchRoot, setMatchRoot] = useState(true);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [resultData, setResultData] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [rawTextModalOpen, setRawTextModalOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const parsedLots = useMemo(() => extractLotIdsFromRows(inputRows), [inputRows]);

  const handleQuery = async () => {
    if (!parsedLots.length) {
      toast.warn("스프레드시트에 조회할 LOT ID를 1개 이상 입력하세요.");
      return;
    }
    setLoading(true);
    try {
      const resp = await sf("/api/lot-location/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lot_ids: parsedLots,
          match_root: matchRoot,
        }),
      });
      setResultData(resp);
      const matchedWafer = resp?.stats?.matched_wafer_count || 0;
      const matchedLot = resp?.stats?.matched_lot_count || 0;
      if (matchedWafer > 0) {
        toast.ok(`${matchedLot}개 LOT, 총 ${matchedWafer}개 웨이퍼 현위치를 조회했습니다.`);
      } else {
        toast.warn("일치하는 WIP 캐시 데이터가 없습니다.");
      }
    } catch (err) {
      toast.error(err?.message || "랏 현위치 조회 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setInputRows(createRowsFromLotList([], 16));
    setResultData(null);
    setSearchQuery("");
  };

  const handleLoadSample = () => {
    setInputRows(createRowsFromLotList(SAMPLE_LOT_IDS, 16));
    setResultData(null);
    toast.ok("샘플 LOT 3건을 스프레드시트에 불러왔습니다.");
  };

  const handleAddRows = () => {
    setInputRows((prev) => {
      const current = prev.map((r) => ({ ...r }));
      for (let i = 0; i < 10; i += 1) {
        current.push({ lot_id: "" });
      }
      return normalizeSpreadsheetRows(current, LOT_INPUT_COLUMNS, { minRows: current.length, maxRows: 2000 });
    });
  };

  const handleApplyBulkText = () => {
    if (!bulkText.trim()) {
      setRawTextModalOpen(false);
      return;
    }
    const tokens = bulkText
      .split(/[\r\n\t,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const valid = [];
    const seen = new Set();
    const headers = new Set(["LOT_ID", "LOTID", "LOT ID", "ROOT_LOT_ID", "ROOT_LOT"]);
    for (const t of tokens) {
      const up = t.toUpperCase();
      if (!headers.has(up) && !seen.has(up)) {
        seen.add(up);
        valid.push(t);
      }
    }
    setInputRows(createRowsFromLotList(valid, Math.max(16, valid.length + 5)));
    setBulkText("");
    setRawTextModalOpen(false);
    toast.ok(`${valid.length}개 LOT을 스프레드시트에 입력했습니다.`);
  };

  const handleCopySpreadsheet = async () => {
    if (!filteredItems.length) {
      toast.warn("복사할 결과 데이터가 없습니다.");
      return;
    }
    const headers = ["#", "lot_id", "root_lot_id", "wafer_id", "현step_id", "현 step_desc", "제품", "lot_type", "최근 이동 시간"];
    const rows = filteredItems.map((item, idx) => [
      idx + 1,
      item.lot_id || "",
      item.root_lot_id || "",
      item.wafer_id || "",
      item.current_step_id || "",
      item.step_desc || "",
      item.product || "",
      item.lot_type || "",
      item.tkout_time ? item.tkout_time.replace("T", " ") : "",
    ]);
    const tsvContent = [headers.join("\t"), ...rows.map((r) => r.join("\t"))].join("\n");
    try {
      await navigator.clipboard.writeText(tsvContent);
      toast.ok(`결과 ${filteredItems.length}개 행을 스프레드시트 형식(TSV)으로 복사했습니다. Excel에 바로 붙여넣으세요.`);
    } catch {
      toast.error("클립보드 복사에 실패했습니다.");
    }
  };

  const handleExportCsv = async () => {
    if (!parsedLots.length) {
      toast.warn("다운로드할 LOT 데이터가 없습니다.");
      return;
    }
    setDownloading(true);
    try {
      const userToken = (() => {
        try {
          const u = JSON.parse(localStorage.getItem("hol_user") || "null");
          return u?.token || "";
        } catch {
          return "";
        }
      })();

      const headers = { "Content-Type": "application/json" };
      if (userToken) {
        headers["X-Session-Token"] = userToken;
      }

      const res = await fetch("/api/lot-location/export-csv", {
        method: "POST",
        headers,
        body: JSON.stringify({
          lot_ids: parsedLots,
          match_root: matchRoot,
        }),
      });

      if (!res.ok) {
        throw new Error(`CSV 다운로드 실패 (HTTP ${res.status})`);
      }

      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") || "";
      let filename = "lot_locations.csv";
      const match = disposition.match(/filename="?([^";]+)"?/);
      if (match && match[1]) {
        filename = match[1];
      }

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.ok("CSV 파일이 다운로드되었습니다.");
    } catch (err) {
      toast.error(err?.message || "CSV 다운로드 중 오류가 발생했습니다.");
    } finally {
      setDownloading(false);
    }
  };

  const items = resultData?.items || [];
  const stats = resultData?.stats;

  const filteredItems = useMemo(() => {
    if (!searchQuery.trim()) return items;
    const q = searchQuery.trim().toLowerCase();
    return items.filter(
      (item) =>
        String(item.lot_id || "").toLowerCase().includes(q) ||
        String(item.root_lot_id || "").toLowerCase().includes(q) ||
        String(item.wafer_id || "").toLowerCase().includes(q) ||
        String(item.current_step_id || "").toLowerCase().includes(q) ||
        String(item.step_desc || "").toLowerCase().includes(q) ||
        String(item.product || "").toLowerCase().includes(q) ||
        String(item.lot_type || "").toLowerCase().includes(q),
    );
  }, [items, searchQuery]);

  return (
    <PageShell layout="workflow" className="lot-location-page">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(320px, 380px) 1fr",
          gap: 16,
          alignItems: "start",
        }}
      >
        {/* 좌측: 입력 스프레드시트 패널 */}
        <Card style={{ padding: 14, borderRadius: 0, border: "1px solid var(--border)" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 10,
              paddingBottom: 8,
              borderBottom: "1px solid var(--border-subtle)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <strong style={{ fontSize: 13, letterSpacing: "0.2px" }}>입력 스프레드시트</strong>
              <Pill tone="info" size="sm">
                {parsedLots.length}건 감지
              </Pill>
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              <Button size="compact" variant="ghost" onClick={handleLoadSample} style={{ borderRadius: 0, fontSize: 11 }}>
                예시
              </Button>
              <Button size="compact" variant="ghost" onClick={() => setRawTextModalOpen(true)} style={{ borderRadius: 0, fontSize: 11 }}>
                일괄입력
              </Button>
              <Button size="compact" variant="ghost" onClick={handleClear} style={{ borderRadius: 0, fontSize: 11 }}>
                비우기
              </Button>
            </div>
          </div>

          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8, lineHeight: 1.4 }}>
            Excel에서 복사한 LOT ID 열을 셀에 클릭 후 <b>Ctrl+V</b>로 붙여넣으세요.
          </div>

          <SpreadsheetPasteGrid
            ariaLabel="LOT ID 입력 스프레드시트"
            columns={LOT_INPUT_COLUMNS}
            rows={inputRows}
            onChange={setInputRows}
            aliases={{ lot: "lot_id", "lot id": "lot_id", lotid: "lot_id", "root_lot": "lot_id", "root_lot_id": "lot_id" }}
            columnLabels={{ lot_id: "LOT ID" }}
            placeholders={{ lot_id: "예: A1022A.2" }}
            showRowNumbers={true}
            minRows={16}
            maxRows={1000}
            maxHeight={480}
            minTableWidth={300}
          />

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 10, flexWrap: "wrap", gap: 8 }}>
            <Button size="compact" variant="ghost" onClick={handleAddRows} style={{ borderRadius: 0, fontSize: 11 }}>
              + 10행 추가
            </Button>

            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={matchRoot}
                onChange={(e) => setMatchRoot(e.target.checked)}
              />
              <span>Root LOT 매칭</span>
            </label>
          </div>

          <div style={{ marginTop: 12 }}>
            <Button
              variant="primary"
              onClick={handleQuery}
              disabled={loading || !parsedLots.length}
              style={{ width: "100%", borderRadius: 0, height: 36, fontSize: 13, fontWeight: 600 }}
            >
              {loading ? "WIP 조회 중…" : `🔍 현위치 확인 (${parsedLots.length}건)`}
            </Button>
          </div>
        </Card>

        {/* 우측: 결과 스프레드시트 패널 */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* 결과 요약 및 필터 툴바 */}
          <Toolbar style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10, borderRadius: 0, border: "1px solid var(--border)", padding: "8px 12px" }}>
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ fontWeight: 600, fontSize: 12, color: "var(--text-secondary)" }}>WIP 결과:</span>
              <Pill tone="neutral">요청 {stats ? stats.requested_count : parsedLots.length}건</Pill>
              {stats && (
                <>
                  <Pill tone="ok">확인 LOT {stats.matched_lot_count}건</Pill>
                  <Pill tone="info">웨이퍼 {stats.matched_wafer_count}개</Pill>
                  {stats.unmatched_lots?.length > 0 && (
                    <Pill tone="warn" title={`미확인 LOT: ${stats.unmatched_lots.join(", ")}`}>
                      미확인 {stats.unmatched_lots.length}건
                    </Pill>
                  )}
                </>
              )}
            </div>

            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <Input
                placeholder="결과 내 검색 (Lot, Step, Desc 등)"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{ width: 220, borderRadius: 0, height: 32, fontSize: 12 }}
              />
              <Button
                variant="secondary"
                size="compact"
                onClick={handleCopySpreadsheet}
                disabled={!items.length}
                style={{ borderRadius: 0, height: 32, fontSize: 12 }}
              >
                📋 표 복사
              </Button>
              <Button
                variant="secondary"
                size="compact"
                onClick={handleExportCsv}
                disabled={downloading || !items.length}
                style={{ borderRadius: 0, height: 32, fontSize: 12 }}
              >
                {downloading ? "다운로드 중…" : "📥 CSV 다운로드"}
              </Button>
            </div>
          </Toolbar>

          {/* 미확인 랏 알림 배너 */}
          {stats?.unmatched_lots?.length > 0 && (
            <div
              style={{
                padding: "8px 12px",
                border: "1px solid var(--border-subtle)",
                background: "var(--bg-secondary)",
                fontSize: 12,
                display: "flex",
                alignItems: "center",
                gap: 8,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontWeight: 600, color: "var(--warn)" }}>⚠ WIP 미확인 LOT ({stats.unmatched_lots.length}건):</span>
              {stats.unmatched_lots.map((lot) => (
                <Pill key={lot} tone="neutral" size="sm">
                  {lot}
                </Pill>
              ))}
            </div>
          )}

          {/* 결과 스프레드시트 뷰어 그리드 */}
          <Card style={{ padding: 0, borderRadius: 0, border: "1px solid var(--border)", overflow: "hidden" }}>
            <div style={{ overflow: "auto", maxHeight: "68vh" }}>
              <table
                aria-label="랏 현위치 결과 스프레드시트"
                style={{
                  width: "100%",
                  minWidth: 760,
                  tableLayout: "fixed",
                  borderCollapse: "separate",
                  borderSpacing: 0,
                  fontSize: 12,
                  fontFamily: "var(--font-mono, monospace)",
                }}
              >
                <colgroup>
                  <col style={{ width: 46 }} />
                  <col style={{ width: 140 }} />
                  <col style={{ width: 110 }} />
                  <col style={{ width: 75 }} />
                  <col style={{ width: 120 }} />
                  <col style={{ width: 190 }} />
                  <col style={{ width: 100 }} />
                  <col style={{ width: 85 }} />
                  <col style={{ width: 160 }} />
                </colgroup>
                <thead>
                  <tr>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 3,
                        padding: "8px 6px",
                        textAlign: "center",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-secondary)",
                        fontWeight: 600,
                      }}
                    >
                      #
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "left",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      lot_id
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "left",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      root_lot_id
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "center",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      wafer_id
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "left",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      현step_id
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "left",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      현 step_desc
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "left",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      제품
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "left",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      lot_type
                    </th>
                    <th
                      style={{
                        position: "sticky",
                        top: 0,
                        zIndex: 2,
                        padding: "8px 9px",
                        textAlign: "left",
                        background: "var(--bg-tertiary)",
                        borderRight: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        color: "var(--text-primary)",
                        fontWeight: 600,
                      }}
                    >
                      최근 이동 시간
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.length > 0 ? (
                    filteredItems.map((row, idx) => (
                      <tr key={`${row.lot_id}-${row.wafer_id}-${idx}`}>
                        <th
                          scope="row"
                          style={{
                            padding: "6px 6px",
                            textAlign: "center",
                            color: "var(--text-secondary)",
                            background: "var(--bg-secondary)",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            fontWeight: 500,
                            userSelect: "none",
                          }}
                        >
                          {idx + 1}
                        </th>
                        <td
                          style={{
                            padding: "6px 9px",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            fontWeight: 600,
                            color: "var(--accent)",
                          }}
                        >
                          {row.lot_id}
                        </td>
                        <td
                          style={{
                            padding: "6px 9px",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            color: "var(--text-secondary)",
                          }}
                        >
                          {row.root_lot_id || "-"}
                        </td>
                        <td
                          style={{
                            padding: "6px 9px",
                            textAlign: "center",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            fontWeight: 600,
                          }}
                        >
                          {row.wafer_id}
                        </td>
                        <td
                          style={{
                            padding: "6px 9px",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            color: "var(--text-primary)",
                            fontWeight: 600,
                          }}
                        >
                          {row.current_step_id || "-"}
                        </td>
                        <td
                          style={{
                            padding: "6px 9px",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            color: "var(--text-primary)",
                            fontWeight: 600,
                            fontFamily: "var(--font-sans, sans-serif)",
                          }}
                        >
                          {row.step_desc || "-"}
                        </td>
                        <td
                          style={{
                            padding: "6px 9px",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            color: "var(--text-secondary)",
                          }}
                        >
                          {row.product || "-"}
                        </td>
                        <td
                          style={{
                            padding: "6px 9px",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            color: "var(--text-secondary)",
                            fontSize: 11,
                          }}
                        >
                          {row.lot_type || "-"}
                        </td>
                        <td
                          style={{
                            padding: "6px 9px",
                            borderRight: "1px solid var(--border)",
                            borderBottom: "1px solid var(--border)",
                            color: "var(--text-secondary)",
                            fontSize: 11,
                          }}
                        >
                          {row.tkout_time ? row.tkout_time.replace("T", " ") : "-"}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td
                        colSpan={9}
                        style={{
                          padding: 56,
                          textAlign: "center",
                          color: "var(--text-secondary)",
                          fontFamily: "var(--font-sans, sans-serif)",
                          background: "var(--bg-primary)",
                        }}
                      >
                        {loading
                          ? "WIP Parquet 캐시에서 웨이퍼별 공정 위치를 조회하고 있습니다…"
                          : resultData
                            ? "검색 필터와 일치하는 행이 없습니다."
                            : "좌측 스프레드시트에 LOT ID를 붙여넣고 [현위치 확인] 버튼을 누르세요."}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {filteredItems.length > 0 && (
              <div
                style={{
                  padding: "8px 14px",
                  borderTop: "1px solid var(--border)",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  background: "var(--bg-secondary)",
                }}
              >
                <span>
                  표시 중: <strong>{filteredItems.length}</strong>행 / 전체 {items.length}개 웨이퍼
                </span>
                <div style={{ display: "flex", gap: 6 }}>
                  <Button
                    size="compact"
                    variant="secondary"
                    onClick={handleCopySpreadsheet}
                    style={{ borderRadius: 0, fontSize: 11 }}
                  >
                    표 복사 (TSV)
                  </Button>
                  <Button
                    size="compact"
                    variant="secondary"
                    onClick={handleExportCsv}
                    disabled={downloading}
                    style={{ borderRadius: 0, fontSize: 11 }}
                  >
                    CSV 다운로드
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* 일괄 텍스트 입력 모달 */}
      {rawTextModalOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 9999,
            background: "rgba(0, 0, 0, 0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          onClick={() => setRawTextModalOpen(false)}
        >
          <div
            style={{
              width: 520,
              maxWidth: "92vw",
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong style={{ fontSize: 14 }}>LOT ID 일괄 텍스트 입력</strong>
              <button
                type="button"
                onClick={() => setRawTextModalOpen(false)}
                style={{
                  background: "transparent",
                  border: 0,
                  cursor: "pointer",
                  fontSize: 18,
                  color: "var(--text-secondary)",
                }}
              >
                ×
              </button>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              줄바꿈, 쉼표, 공백 등으로 구분된 LOT ID 텍스트를 붙여넣으면 스프레드시트에 행으로 변환됩니다.
            </div>
            <textarea
              rows={8}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder="예:&#10;A1022A.2&#10;A1027C.1&#10;B1008A.1"
              style={{
                width: "100%",
                padding: "8px 10px",
                border: "1px solid var(--border)",
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                fontFamily: "var(--font-mono, monospace)",
                fontSize: 12,
                boxSizing: "border-box",
                borderRadius: 0,
              }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Button variant="secondary" onClick={() => setRawTextModalOpen(false)} style={{ borderRadius: 0 }}>
                취소
              </Button>
              <Button variant="primary" onClick={handleApplyBulkText} style={{ borderRadius: 0 }}>
                스프레드시트에 반영
              </Button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
}
