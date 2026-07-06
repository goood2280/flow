import { SUB_TABS } from "../config";

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

export function useUserRole(user) {
  return {
    role: user?.role || "user",
    pageAdmins: pageAdmins(user),
    isAdmin: isAdmin(user),
    isPageAdmin: (pageKey) => isPageAdmin(user, pageKey),
    canManagePage: (pageKey) => canManagePage(user, pageKey),
  };
}
