import { SUB_TABS, TABS } from "../config";

const PAGE_ID_ALIASES = {
  informs: "inform",
  meetings: "meeting",
  dbmap: "tablemap",
};

function canonicalPageId(pageKey) {
  const key = String(pageKey || "").trim();
  return PAGE_ID_ALIASES[key] || key;
}

export function pageAdmins(user) {
  return Array.isArray(user?.page_admins)
    ? user.page_admins.map(canonicalPageId).filter(Boolean)
    : [];
}

export function isAdmin(user) {
  return user?.role === "admin";
}

export function isPageAdmin(user, pageKey) {
  const key = canonicalPageId(pageKey);
  if (!key) return false;
  return pageAdmins(user).includes(key);
}

export function canManagePage(user, pageKey) {
  return isAdmin(user) || isPageAdmin(user, pageKey);
}

// ── v9.1.x: 소탭 단위 권한 ───────────────────────────────────────────
// tabs 토큰 규약: "tab"(해당 탭 전체 소탭) | "tab:subtab"(해당 소탭만).
// 로그인 시 localStorage.hol_user.tabs 에 저장된 토큰을 읽는다 (기존 탭 게이팅과 동일한 갱신 주기).

function storedTabTokens() {
  try {
    const u = JSON.parse(localStorage.getItem("hol_user") || "null");
    if (!u) return null;
    if (u.role === "admin" || u.tabs === "__all__") return "__all__";
    const raw = Array.isArray(u.tabs) ? u.tabs.join(",") : String(u.tabs || "");
    return raw.split(",").map((s) => s.trim()).filter(Boolean);
  } catch {
    return null;
  }
}

export function allowedSubTabs(tabKey) {
  const key = canonicalPageId(tabKey);
  const catalog = (SUB_TABS[key] || []).map((s) => s.key);
  const tokens = storedTabTokens();
  if (tokens === null || tokens === "__all__") return catalog;
  const subs = [];
  let bare = false;
  for (const t of tokens) {
    const [main, sub] = t.split(":");
    if (canonicalPageId(main) !== key) continue;
    if (!sub) bare = true;
    else if (catalog.includes(sub) && !subs.includes(sub)) subs.push(sub);
  }
  if (bare) return catalog;
  return subs;
}

export function canAccessSubTab(tabKey, subKey) {
  return allowedSubTabs(tabKey).includes(subKey);
}

// ── 탭 접근 판정 ────────────────────────────────────────────────────
// nav(App.jsx) · 홈 카드(My_Home) 가 같은 규칙을 쓰도록 여기 한 곳에 둔다.
// 예전에는 홈이 user.tabs 를 직접 쪼개 보느라 소탭 토큰("splittable:view")과
// 승계 규칙(랏 관리 ← splittable 등)을 놓쳐서 nav 에는 있는 탭이 홈 카드에는
// 안 보였다.
const REMOVED_TAB_KEYS = new Set(["aihub", "sqlworkspace"]);

export function grantedTabKeys(userTabs) {
  // v9.1.x: "tab:subtab" 소탭 토큰은 main tab 접근을 부여한다.
  const parts = Array.isArray(userTabs)
    ? userTabs
    : (typeof userTabs === "string" ? userTabs.split(",") : []);
  return parts.map((t) => String(t || "").trim().split(":")[0]).filter(Boolean);
}

export function canAccessTab(user, userTabs, tabKey) {
  if (REMOVED_TAB_KEYS.has(tabKey)) return false;
  if (tabKey === "home") return true;
  if (userTabs === "__all__") return true;
  const tabConfig = TABS.find((item) => item.key === tabKey);
  // PI 처리 담당자는 페이지 위임만 받아도 랏 요청 보드에 바로 진입할 수 있다.
  // (일반 요청자는 기존 탭 권한으로 진입.)
  if (tabKey === "lotrequest" && isPageAdmin(user, tabKey)) return true;
  if (tabConfig?.adminOnly && !isAdmin(user)) {
    // strictAdmin: page-admin 위임으로도 노출 불가 (devguide 등).
    if (tabConfig?.strictAdmin || tabKey === "admin" || !isPageAdmin(user, tabKey)) return false;
    return true;
  }
  const granted = grantedTabKeys(userTabs);
  // 랏 관리는 SplitTable의 LOT/CUSTOM/plan 흐름을 확장한 데이터 화면이다.
  // 기존 배포 사용자가 권한 재저장 전에도 기존 splittable 권한으로 접근한다.
  if (tabKey === "lotmanage" && granted.includes("splittable")) return true;
  // 차트생성은 기존 Dashboard/FileBrowser 권한 사용자가 권한 재저장 전에도 이용한다.
  if (tabKey === "chartbuilder" && (granted.includes("dashboard") || granted.includes("filebrowser"))) return true;
  // Template Report는 저장된 ChartBuilder 코드를 재사용한다. 기존 차트생성
  // 권한 사용자가 관리자 권한 재저장 없이 바로 이용할 수 있게 승계한다.
  if (tabKey === "templatereport" && (granted.includes("chartbuilder") || granted.includes("dashboard") || granted.includes("filebrowser"))) return true;
  return granted.includes(tabKey);
}

// 홈 카드/네비게이션에 노출할 탭 목록. TABS 순서를 그대로 유지한다.
export function visibleTabsFor(user, userTabs) {
  return TABS.filter((item) => item.key !== "home" && canAccessTab(user, userTabs, item.key));
}

export function useUserRole(user) {
  return {
    role: user?.role || "user",
    pageAdmins: pageAdmins(user),
    isAdmin: isAdmin(user),
    isPageAdmin: (pageKey) => isPageAdmin(user, pageKey),
    canManagePage: (pageKey) => canManagePage(user, pageKey),
  };
}
