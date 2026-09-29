import { useCallback, useState } from "react";
import Modal from "./Modal";
import { Button } from "./UXKit";

// 다운로드 용량/행 수 한도 안내 팝업 — 파일탐색기 CSV, ET 다운로드(대기열·Python 테스트) 공용.
// 결과가 한도에서 잘렸거나(응답 헤더) 용량초과로 실패했을 때 "필터나 SQL식을 좁혀 다시 받으세요"를 안내한다.

const LIMIT_TEXT = /용량\s*초과|byte limit|too large|너무 큽니다|한도.*초과|초과.*한도/i;

export function isDownloadLimitError(err) {
  if (!err) return false;
  if (Number(err.status) === 413) return true;
  return LIMIT_TEXT.test(String(err.message || err.rawMessage || err || ""));
}

// dl() 이 돌려준 응답 정보(행 수 한도에서 잘렸는지)를 팝업 정보로 바꾼다.
export function truncatedDownloadInfo(result, label = "CSV") {
  if (!result?.truncated) return null;
  const limit = Number(result.rowLimit || 0);
  return {
    kind: "truncated",
    title: `${label} 결과가 잘렸습니다`,
    message: limit
      ? `다운로드 한도 ${limit.toLocaleString()}행까지만 받았습니다. 전체 결과는 이보다 많습니다.`
      : "다운로드 한도까지만 받았습니다. 전체 결과는 이보다 많습니다.",
  };
}

export function limitErrorInfo(err, label = "다운로드") {
  return {
    kind: "error",
    title: `${label} 용량 한도 초과`,
    message: String(err?.message || err || "결과가 다운로드 한도를 넘었습니다."),
  };
}

export function useDownloadLimitDialog() {
  const [info, setInfo] = useState(null);
  const close = useCallback(() => setInfo(null), []);
  const dialog = info ? (
    <Modal open onClose={close} title={info.title} width={460}>
      <div style={{ display: "grid", gap: 10, fontSize: 14, lineHeight: 1.6 }}>
        <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{info.message}</p>
        <p style={{ margin: 0, fontWeight: 700 }}>필터나 SQL식을 좁혀 다시 받으세요.</p>
        <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-secondary)", fontSize: 13 }}>
          <li>기간·제품·LOT 조건을 추가해 행 수를 줄입니다.</li>
          <li>필요한 컬럼만 선택해 용량을 줄입니다.</li>
          <li>여러 번에 나눠 받습니다(예: 월 단위).</li>
        </ul>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Button variant="primary" onClick={close} autoFocus>확인</Button>
        </div>
      </div>
    </Modal>
  ) : null;
  return [dialog, setInfo];
}
