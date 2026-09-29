import { useEffect, useState } from "react";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows } from "../../components/SpreadsheetPasteGrid";
import { Banner, Button } from "../../components/UXKit";
import { toast } from "../../components/Toast";
import { postJson, sf } from "../../lib/api";

// 부서별 로그인·권한 규칙 (websocket 사내 로그인 사용자용).
//   - 규칙이 하나도 없으면 모두 기본 탭으로 로그인된다.
//   - 규칙이 있으면 허용 규칙에 맞는 부서만 로그인되고, 탭은 맞는 규칙들의 합이다.
// 관리자·대리인의 이름·메일·부서는 서버에 암호화 저장되며 로그인한 사용자에게만 보인다.

const COLUMNS = ["department", "match", "allow_login", "tabs"];
const LABELS = { department: "부서", match: "일치 방식 (exact/prefix)", allow_login: "로그인 (O/X)", tabs: "탭 권한 (쉼표, 비우면 관리자 탭 외 전부)" };

const toRows = (rules) => (rules || []).map((r) => ({
  department: r.department || "",
  match: r.match || "exact",
  allow_login: r.allow_login === false ? "X" : "O",
  tabs: r.tabs || "",
}));

const PROFILE_COLUMNS = ["username", "name", "email", "role", "pages", "department"];
const PROFILE_LABELS = {
  username: "계정", name: "이름", email: "메일", role: "역할 (관리자 admin / 페이지 대리인 user)",
  pages: "페이지 권한 (탭 ID, 쉼표 구분)", department: "부서 (선택)",
};
const toProfileRows = (profiles) => (profiles || []).map((p) => ({
  username: p.username || "", name: p.name || "", email: p.email || "",
  role: p.role === "admin" ? "admin" : "user",
  pages: Array.isArray(p.pages) ? p.pages.join(",") : (p.pages || ""),
  department: p.department || "",
}));

export default function DepartmentAccessPanel({ onChanged }) {
  const [rows, setRows] = useState([]);
  const [tabIds, setTabIds] = useState([]);
  const [profileRows, setProfileRows] = useState([]);
  const [profileError, setProfileError] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = () => {
    sf("/api/auth/department-rules")
      .then((d) => { setRows(toRows(d.rules)); setTabIds(d.tab_ids || []); setError(""); })
      .catch((e) => setError(e.message));
    sf("/api/admin/manager-profiles")
      .then((d) => { setProfileRows(toProfileRows(d.profiles)); setTabIds(d.tab_ids || []); setProfileError(""); })
      .catch((e) => setProfileError(e.message || "관리자·대리인 정보를 불러오지 못했습니다."));
  };

  const saveProfiles = async () => {
    setProfileBusy(true);
    setProfileError("");
    try {
      const profiles = profileRows
        .filter((r) => Object.values(r).some((v) => String(v || "").trim()))
        .map((r) => {
          const role = String(r.role || "user").trim().toLowerCase() === "admin" ? "admin" : "user";
          return {
          username: String(r.username || "").trim(),
          name: String(r.name || "").trim(),
          email: String(r.email || "").trim(),
          role,
          pages: role === "admin" ? [] : String(r.pages || "").split(",").map((v) => v.trim()).filter(Boolean),
          department: String(r.department || "").trim(),
        }; });
      const d = await postJson("/api/admin/manager-profiles", { profiles });
      if (Array.isArray(d.profiles)) setProfileRows(toProfileRows(d.profiles));
      toast.ok(`관리자·대리인 정보 ${profiles.length}건 저장`);
      onChanged?.();
    } catch (e) {
      setProfileError(e.message || "관리자·대리인 정보 저장 실패");
      toast.error(e.message || "관리자·대리인 정보 저장 실패");
    } finally {
      setProfileBusy(false);
    }
  };
  useEffect(load, []);

  const save = async () => {
    setBusy(true);
    try {
      const rules = rows
        .filter((r) => String(r.department || "").trim())
        .map((r) => ({
          department: String(r.department).trim(),
          match: String(r.match || "exact").trim().toLowerCase() === "prefix" ? "prefix" : "exact",
          allow_login: !["x", "n", "no", "false", "0", "거부", "불가"].includes(String(r.allow_login || "O").trim().toLowerCase()),
          tabs: String(r.tabs || "").trim(),
        }));
      const d = await postJson("/api/auth/department-rules", { rules });
      setRows(toRows(d.rules));
      toast.ok(`부서 규칙 ${d.rules.length}건 저장`);
    } catch (e) {
      toast.error(e.message || "저장 실패");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {error && <Banner tone="danger">{error}</Banner>}
      <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6 }}>
        일반 사내 로그인(websocket) 사용자는 계정 목록에 저장하지 않고, 인증서버가 확인해 준 <b>부서</b>로 로그인 허용과 탭 권한을 정합니다.
        규칙이 하나도 없으면 모두 로그인됩니다. 규칙을 넣으면 <b>허용(O) 규칙에 맞는 부서만</b> 로그인되고, 거부(X)가 우선합니다.
        prefix 는 부서명이 그 글자로 시작하면 맞습니다. 탭 ID: <code>{tabIds.join(", ")}</code>
      </div>
      <SpreadsheetPasteGrid
        columns={COLUMNS}
        rows={normalizeSpreadsheetRows(rows, COLUMNS, { minRows: 10, maxRows: 500 })}
        onChange={setRows}
        ariaLabel="부서별 로그인·권한 규칙"
        columnLabels={LABELS}
        aliases={{ 부서: "department", 일치: "match", 로그인: "allow_login", 탭: "tabs" }}
        placeholders={{ department: "예: 공정기술", match: "exact", allow_login: "O", tabs: "splittable,filebrowser" }}
        minRows={10}
        maxRows={500}
        minTableWidth={760}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <Button variant="primary" disabled={busy} onClick={save}>{busy ? "저장 중…" : "부서 규칙 저장"}</Button>
        <Button variant="secondary" disabled={busy} onClick={load}>다시 불러오기</Button>
      </div>

      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>관리자·대리인 정보 (암호화 저장)</div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8, lineHeight: 1.6 }}>
          사내 계정 ID로 관리자와 페이지 대리인을 등록·수정합니다. 이름과 메일은 필수입니다. 관리자 역할은 admin으로 입력하며 모든 관리 권한을 가져 페이지 권한이 필요 없습니다.
          페이지 대리인 역할은 user로 입력하며 지정한 페이지 권한이 하나 이상 필요합니다. 페이지 권한에는 탭 ID를 쉼표로 구분해 입력하세요.
          행을 비워도 기존 등록은 삭제되지 않습니다. 위임 해제는 기존 페이지 위임 관리에서 할 수 있습니다. 부서는 선택 항목입니다. 사내 SSO 로그인 때 직접 입력한 연락처 정보는 계속 보존됩니다. 탭 ID: <code>{tabIds.join(", ")}</code>
        </div>
        {profileError && <Banner tone="danger">{profileError}</Banner>}
        <SpreadsheetPasteGrid
          columns={PROFILE_COLUMNS}
          rows={normalizeSpreadsheetRows(profileRows, PROFILE_COLUMNS, { minRows: 10, maxRows: 500 })}
          onChange={setProfileRows}
          ariaLabel="관리자·대리인 권한"
          columnLabels={PROFILE_LABELS}
          aliases={{ 계정: "username", 이름: "name", 메일: "email", 역할: "role", 페이지: "pages", 부서: "department" }}
          placeholders={{ username: "사내 계정", name: "이름", email: "name@example.com", role: "user", pages: "splittable,filebrowser", department: "선택" }}
          minRows={10}
          maxRows={500}
          minTableWidth={1000}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <Button variant="primary" disabled={profileBusy} onClick={saveProfiles}>{profileBusy ? "저장 중…" : "관리자·대리인 저장"}</Button>
          <Button variant="secondary" disabled={profileBusy} onClick={load}>다시 불러오기</Button>
        </div>
      </div>
    </div>
  );
}
