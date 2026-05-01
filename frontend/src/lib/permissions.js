export function pageAdmins(user) {
  return Array.isArray(user?.page_admins)
    ? user.page_admins.map((key) => String(key || "").trim()).filter(Boolean)
    : [];
}

export function isAdmin(user) {
  return user?.role === "admin";
}

export function isPageAdmin(user, pageKey) {
  const key = String(pageKey || "").trim();
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
