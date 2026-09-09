/* TegMapfileTraffic.jsx — TEG Mapfile 신호등 (Mapfile 좌표생성 옆 소탭).
   DB root/mapfile 폴더에서 제품코드(product_code)로 시작하는 mapfile들을 자동 감지하여
   Mapfile 검증(_tc.inspect)을 수행하고, 각 map 별 S/L 및 Main 신호등을 한눈에 볼 수 있으며
   '상세보기 펼치기'를 통해 불일치·주의 내역을 상세 검토할 수 있는 대시보드.
*/
import { useCallback, useEffect, useState } from "react";
import { sf } from "../../lib/api";
import { toast } from "../../components/Toast";
import { Button, Card, EmptyState, Pill } from "../../components/UXKit";

const API = "/api/teg-map";

const LIGHT_BADGES = {
  green: { label: "정상", color: "#2f9e63", bg: "rgba(47, 158, 99, 0.12)", border: "#2f9e63", icon: "🟢" },
  yellow: { label: "확인 필요", color: "#d99a1a", bg: "rgba(217, 154, 26, 0.12)", border: "#d99a1a", icon: "🟡" },
  red: { label: "불일치", color: "#dc2626", bg: "rgba(220, 38, 38, 0.12)", border: "#dc2626", icon: "🔴" },
  none: { label: "해당 없음", color: "#6b7280", bg: "rgba(107, 114, 128, 0.08)", border: "#9ca3af", icon: "⚪" },
  gray: { label: "대기 / 오류", color: "#9ca3af", bg: "rgba(156, 163, 175, 0.12)", border: "#9ca3af", icon: "⚪" },
};

function fmtBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function TrafficPill({ lightKey, text, style = {} }) {
  const badge = LIGHT_BADGES[lightKey] || LIGHT_BADGES.gray;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 12,
        fontSize: 11,
        fontWeight: 700,
        color: badge.color,
        background: badge.bg,
        border: `1px solid ${badge.border}`,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      <span>{badge.icon}</span>
      <span>{text || badge.label}</span>
    </span>
  );
}

export default function TegMapfileTraffic({ vehicle, onOpenCheck, initialFilename = "" }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [expandedFiles, setExpandedFiles] = useState({}); // { [filename]: boolean }
  const [openingFile, setOpeningFile] = useState("");

  const loadTraffic = useCallback(async (force = false) => {
    if (!vehicle) return;
    setBusy(true);
    try {
      const url = `${API}/mapfile-traffic?vehicle=${encodeURIComponent(vehicle)}${force ? "&force=1" : ""}`;
      const res = await sf(url);
      setData(res);
    } catch (err) {
      toast.error(`신호등 검증 조회 실패: ${err.message || err}`);
    } finally {
      setBusy(false);
    }
  }, [vehicle]);

  useEffect(() => {
    setExpandedFiles(initialFilename ? { [initialFilename]: true } : {});
    loadTraffic(false);
  }, [loadTraffic, initialFilename]);

  const toggleExpand = (filename) => {
    setExpandedFiles((prev) => ({ ...prev, [filename]: !prev[filename] }));
  };

  const handleOpenInCheck = async (filename) => {
    setOpeningFile(filename);
    try {
      const url = `${API}/mapfile-traffic/content?filename=${encodeURIComponent(filename)}`;
      const res = await sf(url);
      if (res && res.content !== undefined) {
        if (onOpenCheck) {
          onOpenCheck(res.content, filename);
        }
      }
    } catch (err) {
      toast.error(`파일 원문 로드 실패: ${err.message || err}`);
    } finally {
      setOpeningFile("");
    }
  };

  if (!vehicle) {
    return <EmptyState icon="🚦" title="제품을 선택해 주세요" hint="상단에서 제품을 선택하면 Mapfile 신호등이 표시됩니다" />;
  }

  const summary = data?.summary || {
    total_files: 0,
    green_files: 0,
    yellow_files: 0,
    red_files: 0,
    gray_files: 0,
    sl_green_files: 0,
    sl_yellow_files: 0,
    sl_red_files: 0,
    main_green_files: 0,
    main_yellow_files: 0,
    main_red_files: 0,
  };
  const overall = LIGHT_BADGES[data?.overall_light || "gray"] || LIGHT_BADGES.gray;
  const productCode = data?.product_code || "";

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <Card
        title="Mapfile 신호등"
        right={
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "4px 12px",
                borderRadius: 14,
                fontSize: 12,
                fontWeight: 700,
                color: overall.color,
                background: overall.bg,
                border: `1px solid ${overall.border}`,
              }}
            >
              {overall.icon} 전체 {overall.label}
            </span>
            <Button onClick={() => loadTraffic(false)} disabled={busy}>
              {busy ? "조회 중…" : "새로고침"}
            </Button>
            <Button variant="secondary" onClick={() => loadTraffic(true)} disabled={busy} title="모든 파일 캐시를 무시하고 1회 전체 재검증">
              전체 재검증
            </Button>
          </div>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
            <div>
              <span style={{ color: "var(--muted)" }}>연결 제품코드: </span>
              {productCode ? (
                <Pill tone="ok"><b>{productCode}</b></Pill>
              ) : (
                <Pill tone="warn">미설정 (제품명 매칭)</Pill>
              )}
            </div>
            <div style={{ display: "flex", gap: 10, marginLeft: "auto", flexWrap: "wrap", fontSize: 12 }}>
              <div style={{ border: "1px solid var(--line)", padding: "2px 8px", borderRadius: 6 }}>
                <span style={{ color: "var(--muted)", marginRight: 6 }}>S/L:</span>
                <span style={{ color: "#2f9e63", fontWeight: 700 }}>🟢 {summary.sl_green_files || 0}</span>{" "}
                <span style={{ color: "#d99a1a", fontWeight: 700 }}>🟡 {summary.sl_yellow_files || 0}</span>{" "}
                <span style={{ color: "#dc2626", fontWeight: 700 }}>🔴 {summary.sl_red_files || 0}</span>
              </div>
              <div style={{ border: "1px solid var(--line)", padding: "2px 8px", borderRadius: 6 }}>
                <span style={{ color: "var(--muted)", marginRight: 6 }}>Main:</span>
                <span style={{ color: "#2f9e63", fontWeight: 700 }}>🟢 {summary.main_green_files || 0}</span>{" "}
                <span style={{ color: "#d99a1a", fontWeight: 700 }}>🟡 {summary.main_yellow_files || 0}</span>{" "}
                <span style={{ color: "#dc2626", fontWeight: 700 }}>🔴 {summary.main_red_files || 0}</span>
              </div>
              <span style={{ color: "var(--muted)", alignSelf: "center" }}>총 {summary.total_files}개 Map</span>
            </div>
          </div>
        </div>
      </Card>

      {!data?.files?.length ? (
        <Card>
          <EmptyState
            icon="📂"
            title="감지된 Mapfile이 없습니다"
            hint={
              productCode
                ? `'${productCode}'(으)로 시작하는 Mapfile이 없습니다.`
                : `'${vehicle}'(으)로 시작하는 Mapfile이 없습니다.`
            }
          />
        </Card>
      ) : (
        <Card title={`Mapfile 검증 목록 (${data.files.length})`}>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left", color: "var(--muted)" }}>
                  <th style={{ padding: "8px 10px", width: 60 }}>종합</th>
                  <th style={{ padding: "8px 10px" }}>파일명</th>
                  <th style={{ padding: "8px 10px", width: 140 }}>S/L 신호등</th>
                  <th style={{ padding: "8px 10px", width: 140 }}>Main 신호등</th>
                  <th style={{ padding: "8px 10px", width: 85 }}>Flat 감지</th>
                  <th style={{ padding: "8px 10px", width: 75 }}>크기</th>
                  <th style={{ padding: "8px 10px", width: 130 }}>파일 수정일시</th>
                  <th style={{ padding: "8px 10px", width: 110, textAlign: "center" }}>상세보기</th>
                  <th style={{ padding: "8px 10px", width: 135, textAlign: "right" }}>작업</th>
                </tr>
              </thead>
              <tbody>
                {data.files.map((file) => {
                  const light = file.traffic_light || "gray";
                  const isExpanded = !!expandedFiles[file.filename];
                  const sl = file.sl || { light: "gray", green: 0, yellow: 0, red: 0, total: 0, missing_targets: 0, issues: [] };
                  const main = file.main || { light: "none", green: 0, yellow: 0, red: 0, total: 0, group_count: 0, issues: [] };
                  const allIssuesCount = (sl.issues?.length || 0) + (main.issues?.length || 0);

                  // Label builders
                  const slLabel = sl.light === "green"
                    ? `정상 (${sl.green})`
                    : sl.light === "red"
                    ? `불일치 (${sl.red})`
                    : sl.light === "yellow"
                    ? `확인필요 (${(sl.yellow || 0) + (sl.missing_targets || 0)})`
                    : sl.light === "none"
                    ? "해당없음"
                    : "대기";

                  const mainLabel = main.light === "green"
                    ? `정상 (${main.green || main.group_count})`
                    : main.light === "red"
                    ? `불일치 (${main.red})`
                    : main.light === "yellow"
                    ? `확인필요 (${main.yellow})`
                    : main.light === "none"
                    ? "해당없음"
                    : "대기";

                  return (
                    <>
                      <tr
                        key={file.filename}
                        style={{
                          borderBottom: "1px solid var(--line)",
                          background: isExpanded ? "var(--bg-hover)" : "transparent",
                          transition: "background 0.15s",
                        }}
                      >
                        {/* 1. 종합 상태 */}
                        <td style={{ padding: "8px 10px" }}>
                          <TrafficPill lightKey={light} text={LIGHT_BADGES[light]?.label} />
                        </td>

                        {/* 2. 파일명 */}
                        <td style={{ padding: "8px 10px", fontWeight: 600 }}>
                          {file.filename}
                          {file.is_cached && (
                            <span style={{ marginLeft: 6, fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>
                              (캐시)
                            </span>
                          )}
                        </td>

                        {/* 3. S/L 신호등 */}
                        <td style={{ padding: "8px 10px" }}>
                          <TrafficPill lightKey={sl.light} text={slLabel} />
                        </td>

                        {/* 4. Main 신호등 */}
                        <td style={{ padding: "8px 10px" }}>
                          <TrafficPill lightKey={main.light} text={mainLabel} />
                        </td>

                        {/* 5. Flat 감지 */}
                        <td style={{ padding: "8px 10px" }}>
                          <Pill tone="neutral">{file.flat_detected || "auto"}</Pill>
                        </td>

                        {/* 6. 크기 */}
                        <td style={{ padding: "8px 10px", color: "var(--muted)" }}>{fmtBytes(file.size)}</td>

                        {/* 7. 수정일시 */}
                        <td style={{ padding: "8px 10px", color: "var(--muted)", fontSize: 11 }}>{file.mtime || "-"}</td>

                        {/* 8. 상세보기 펼치기 버튼 */}
                        <td style={{ padding: "8px 10px", textAlign: "center" }}>
                          <button
                            onClick={() => toggleExpand(file.filename)}
                            style={{
                              cursor: "pointer",
                              border: "1px solid var(--line)",
                              background: isExpanded ? "var(--accent)" : "var(--bg-primary)",
                              color: isExpanded ? "#fff" : allIssuesCount > 0 ? (light === "red" ? "#dc2626" : "#d99a1a") : "var(--text)",
                              padding: "3px 9px",
                              borderRadius: 6,
                              fontWeight: 600,
                              fontSize: 11,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4,
                            }}
                          >
                            <span>{isExpanded ? "▲ 접기" : "상세보기 ▾"}</span>
                            {allIssuesCount > 0 && !isExpanded && (
                              <span style={{ background: light === "red" ? "#dc2626" : "#d99a1a", color: "#fff", borderRadius: "50%", padding: "1px 5px", fontSize: 10 }}>
                                {allIssuesCount}
                              </span>
                            )}
                          </button>
                        </td>

                        {/* 9. 액션 */}
                        <td style={{ padding: "8px 10px", textAlign: "right" }}>
                          <Button
                            size="sm"
                            onClick={() => handleOpenInCheck(file.filename)}
                            disabled={openingFile === file.filename}
                            title="이 Mapfile 원문을 'Mapfile 검증' 탭에 넣고 상세 확인합니다"
                          >
                            {openingFile === file.filename ? "여는 중…" : "검증탭에서 열기 →"}
                          </Button>
                        </td>
                      </tr>

                      {/* 10. 상세보기 펼치기 영역 (S/L 및 Main 상세) */}
                      {isExpanded && (
                        <tr key={`${file.filename}-expanded`} style={{ background: "var(--bg-hover)" }}>
                          <td colSpan={9} style={{ padding: "12px 14px 16px 14px" }}>
                            <div style={{ display: "grid", gap: 12, border: "1px solid var(--line)", borderRadius: 8, background: "var(--bg-primary)", padding: 14 }}>
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--line)", paddingBottom: 8 }}>
                                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                                  <b style={{ fontSize: 13 }}>{file.filename} 상세 검증 결과</b>
                                  <TrafficPill lightKey={sl.light} text={`S/L: ${slLabel}`} />
                                  <TrafficPill lightKey={main.light} text={`Main: ${mainLabel}`} />
                                </div>
                                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                                  <span style={{ fontSize: 11, color: "var(--muted)" }}>검증일시: {file.verified_at || "-"}</span>
                                  <Button size="sm" onClick={() => handleOpenInCheck(file.filename)} disabled={openingFile === file.filename}>
                                    Mapfile 검증 탭에서 열기 →
                                  </Button>
                                  <button
                                    onClick={() => toggleExpand(file.filename)}
                                    style={{ border: "none", background: "transparent", cursor: "pointer", color: "var(--muted)", fontSize: 12, marginLeft: 4 }}
                                  >
                                    ✕
                                  </button>
                                </div>
                              </div>

                              {/* S/L 검증 상세 */}
                              <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 10 }}>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                                    <b style={{ fontSize: 12 }}>1. S/L (Scribe Lane) 검증 결과</b>
                                    <span style={{ fontSize: 11, color: "var(--muted)" }}>
                                      (정상 {sl.green}건 / 주의 {sl.yellow}건 / 불일치 {sl.red}건 / 필수대상 {sl.matched_targets}/{sl.total_targets} 매칭)
                                    </span>
                                  </div>
                                  <TrafficPill lightKey={sl.light} text={sl.light === "green" ? "🟢 S/L 정상" : sl.light === "red" ? "🔴 S/L 불일치" : "🟡 S/L 주의"} />
                                </div>

                                {sl.issues?.length > 0 ? (
                                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                                    <thead>
                                      <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--muted)", textAlign: "left" }}>
                                        <th style={{ padding: "4px 8px", width: 45 }}>상태</th>
                                        <th style={{ padding: "4px 8px", width: 110 }}>구분</th>
                                        <th style={{ padding: "4px 8px", width: 130 }}>Mapfile 모듈명</th>
                                        <th style={{ padding: "4px 8px", width: 130 }}>기준 TEG (정답지)</th>
                                        <th style={{ padding: "4px 8px" }}>판정 사유</th>
                                        <th style={{ padding: "4px 8px", width: 110 }}>ΔX / ΔY</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {sl.issues.map((iss, idx) => (
                                        <tr key={idx} style={{ borderBottom: "1px solid var(--line-light, #eee)" }}>
                                          <td style={{ padding: "4px 8px" }}>
                                            {iss.light === "red" ? "🔴" : "🟡"}
                                          </td>
                                          <td style={{ padding: "4px 8px", color: "var(--muted)" }}>{iss.category}</td>
                                          <td style={{ padding: "4px 8px", fontWeight: 600 }}>{iss.teg_name || "-"}</td>
                                          <td style={{ padding: "4px 8px", color: "var(--muted)" }}>{iss.ref_teg || "-"}</td>
                                          <td style={{ padding: "4px 8px", color: iss.light === "red" ? "#dc2626" : "#d99a1a" }}>
                                            {iss.reason || iss.status}
                                          </td>
                                          <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                                            {iss.delta ? `ΔX: ${iss.delta[0] ?? "-"}, ΔY: ${iss.delta[1] ?? "-"}` : "-"}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                ) : (
                                  <div style={{ fontSize: 12, color: "#2f9e63", padding: "6px 4px" }}>
                                    🟢 모든 S/L TEG가 정답지와 일치하며 누락된 필수 대상 TEG가 없습니다.
                                  </div>
                                )}
                              </div>

                              {/* Main 검증 상세 */}
                              <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 10 }}>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                                    <b style={{ fontSize: 12 }}>2. Main (Die / Block) 검증 결과</b>
                                    <span style={{ fontSize: 11, color: "var(--muted)" }}>
                                      {main.light === "none"
                                        ? "(Main TEG 미포함 Map)"
                                        : `(Main 그룹 ${main.group_count}개 / 내부 TEG ${main.total}개 / 불일치 ${main.red}건 / 주의 ${main.yellow}건)`}
                                    </span>
                                  </div>
                                  <TrafficPill
                                    lightKey={main.light}
                                    text={
                                      main.light === "green"
                                        ? "🟢 Main 정상"
                                        : main.light === "red"
                                        ? "🔴 Main 불일치"
                                        : main.light === "yellow"
                                        ? "🟡 Main 주의"
                                        : "⚪ Main 없음"
                                    }
                                  />
                                </div>

                                {main.issues?.length > 0 ? (
                                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                                    <thead>
                                      <tr style={{ borderBottom: "1px solid var(--line)", color: "var(--muted)", textAlign: "left" }}>
                                        <th style={{ padding: "4px 8px", width: 45 }}>상태</th>
                                        <th style={{ padding: "4px 8px", width: 110 }}>구분</th>
                                        <th style={{ padding: "4px 8px", width: 130 }}>모듈명 / 그룹</th>
                                        <th style={{ padding: "4px 8px", width: 130 }}>소속 Main Die</th>
                                        <th style={{ padding: "4px 8px" }}>판정 사유</th>
                                        <th style={{ padding: "4px 8px", width: 110 }}>ΔX / ΔY</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {main.issues.map((iss, idx) => (
                                        <tr key={idx} style={{ borderBottom: "1px solid var(--line-light, #eee)" }}>
                                          <td style={{ padding: "4px 8px" }}>
                                            {iss.light === "red" ? "🔴" : "🟡"}
                                          </td>
                                          <td style={{ padding: "4px 8px", color: "var(--muted)" }}>{iss.category}</td>
                                          <td style={{ padding: "4px 8px", fontWeight: 600 }}>{iss.teg_name || "-"}</td>
                                          <td style={{ padding: "4px 8px", color: "var(--muted)" }}>{iss.ref_teg || "-"}</td>
                                          <td style={{ padding: "4px 8px", color: iss.light === "red" ? "#dc2626" : "#d99a1a" }}>
                                            {iss.reason || iss.status}
                                          </td>
                                          <td style={{ padding: "4px 8px", fontFamily: "monospace" }}>
                                            {iss.delta ? `ΔX: ${iss.delta[0] ?? "-"}, ΔY: ${iss.delta[1] ?? "-"}` : "-"}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                ) : main.light === "none" ? (
                                  <div style={{ fontSize: 12, color: "var(--muted)", padding: "6px 4px" }}>
                                    ⚪ 이 Mapfile에는 Main Die 내부 TEG가 설정되어 있지 않습니다 (S/L 전용 Map).
                                  </div>
                                ) : (
                                  <div style={{ fontSize: 12, color: "#2f9e63", padding: "6px 4px" }}>
                                    🟢 Main die 내부 TEG 배치 및 purpose 규칙을 모두 만족합니다.
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
