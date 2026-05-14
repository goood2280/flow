const PAGE_ID_ALIASES = {
  informs: "inform",
  meetings: "meeting",
  wafer_map: "waferlayout",
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

export function useUserRole(user) {
  return {
    role: user?.role || "user",
    pageAdmins: pageAdmins(user),
    isAdmin: isAdmin(user),
    isPageAdmin: (pageKey) => isPageAdmin(user, pageKey),
    canManagePage: (pageKey) => canManagePage(user, pageKey),
  };
}
