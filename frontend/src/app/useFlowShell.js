import { startTransition, useCallback, useEffect, useRef, useState } from "react";

import { TABS } from "../config";
import { logActivity, postJson, sf } from "../lib/api";
import { canAccessTab, visibleTabsFor } from "../lib/permissions";

const TAB_KEYS = new Set(TABS.map((item) => item.key));
const REMOVED_TAB_KEYS = new Set(["aihub", "sqlworkspace"]);

function tabFromPath(pathname = window.location.pathname) {
  const key = String(pathname || "").split("/").filter(Boolean)[0] || "";
  return TAB_KEYS.has(key) ? key : "";
}

const darkV = {
  "--bg-primary": "#1a1a1a",
  "--bg-secondary": "#262626",
  "--bg-card": "#2a2a2a",
  "--bg-hover": "#333",
  "--bg-tertiary": "#1a1a1a",
  "--text-primary": "#e5e5e5",
  "--text-secondary": "#a3a3a3",
  "--border": "#333",
  "--ink": "#e5e5e5",
  "--muted": "#a3a3a3",
  "--line": "#333",
  "--brand": "#E25822",
  "--brand-50": "rgba(226,88,34,0.16)",
  "--brand-line": "rgba(239,107,58,0.42)",
  "--accent": "#EF6B3A",
  "--accent-dim": "#E25822",
  "--accent-glow": "rgba(226,88,34,0.16)",
  "--ok": "#22c55e",
  "--ok-50": "rgba(34,197,94,0.14)",
  "--ok-line": "rgba(34,197,94,0.38)",
  "--info": "#3b82f6",
  "--info-50": "rgba(59,130,246,0.16)",
  "--info-line": "rgba(59,130,246,0.42)",
  "--warn": "#f59e0b",
  "--warn-50": "rgba(245,158,11,0.16)",
  "--warn-line": "rgba(245,158,11,0.42)",
  "--danger": "#ef4444",
  "--danger-50": "rgba(239,68,68,0.16)",
  "--danger-line": "rgba(239,68,68,0.42)",
  "--bad": "#ef4444",
  "--violet": "#8b5cf6",
  "--violet-50": "rgba(139,92,246,0.16)",
  "--violet-line": "rgba(139,92,246,0.42)",
  "--pink": "#ec4899",
  "--pink-50": "rgba(236,72,153,0.16)",
  "--pink-line": "rgba(236,72,153,0.42)",
  "--shadow-sm": "0 8px 18px rgba(0,0,0,0.18)",
  "--shadow-md": "0 14px 30px rgba(0,0,0,0.24)",
  "--shadow-lg": "0 24px 54px rgba(0,0,0,0.32)",
};

const lightV = {
  "--bg-primary": "#fafafa",
  "--bg-secondary": "#fff",
  "--bg-card": "#fff",
  "--bg-hover": "#f5f5f5",
  "--bg-tertiary": "#f5f5f5",
  "--text-primary": "#171717",
  "--text-secondary": "#737373",
  "--border": "#e5e5e5",
  "--ink": "#171717",
  "--muted": "#737373",
  "--line": "#e5e5e5",
  "--brand": "#E25822",
  "--brand-50": "#FDF2EB",
  "--brand-line": "#F4C4A8",
  "--accent": "#E25822",
  "--accent-dim": "#B94418",
  "--accent-glow": "rgba(226,88,34,0.10)",
  "--ok": "#16a34a",
  "--ok-50": "#ECFDF3",
  "--ok-line": "#BBF7D0",
  "--info": "#2563eb",
  "--info-50": "#EFF6FF",
  "--info-line": "#BFDBFE",
  "--warn": "#d97706",
  "--warn-50": "#FFFBEB",
  "--warn-line": "#FDE68A",
  "--danger": "#dc2626",
  "--danger-50": "#FEF2F2",
  "--danger-line": "#FECACA",
  "--bad": "#dc2626",
  "--violet": "#7c3aed",
  "--violet-50": "#F5F3FF",
  "--violet-line": "#DDD6FE",
  "--pink": "#db2777",
  "--pink-50": "#FDF2F8",
  "--pink-line": "#FBCFE8",
  "--shadow-sm": "0 1px 2px rgba(15,23,42,0.06)",
  "--shadow-md": "0 8px 20px rgba(15,23,42,0.08)",
  "--shadow-lg": "0 18px 44px rgba(15,23,42,0.12)",
};

function useIdleLogout(onLogout, timeoutMs = 6 * 3600 * 1000) {
  const timer = useRef(null);
  useEffect(() => {
    const reset = () => {
      clearTimeout(timer.current);
      timer.current = setTimeout(onLogout, timeoutMs);
    };
    const winEvents = ["mousedown", "keydown", "scroll", "touchstart"];
    winEvents.forEach((eventName) => window.addEventListener(eventName, reset));
    window.addEventListener("flow:activity", reset);
    reset();
    return () => {
      clearTimeout(timer.current);
      winEvents.forEach((eventName) => window.removeEventListener(eventName, reset));
      window.removeEventListener("flow:activity", reset);
    };
  }, [onLogout, timeoutMs]);
}

function readStoredUser() {
  const stored = localStorage.getItem("hol_user");
  if (!stored) return null;
  try {
    const parsed = JSON.parse(stored);
    if (parsed?.token) return parsed;
  } catch (_) {
    // Invalid localStorage should behave the same as no session.
  }
  localStorage.removeItem("hol_user");
  return null;
}

function mergeSessionUser(storedUser, sessionUser) {
  return {
    ...(storedUser || {}),
    username: sessionUser.username,
    role: sessionUser.role || storedUser?.role || "user",
    name: sessionUser.name ?? storedUser?.name ?? "",
    email: sessionUser.email ?? storedUser?.email ?? "",
    // An empty server value is authoritative: it means the account has no
    // granted feature permissions. Do not revive stale tabs from localStorage.
    tabs: sessionUser.tabs ?? storedUser?.tabs ?? "",
  };
}

export function useFlowShell() {
  const [user, setUser] = useState(null);
  const [tab, setTab] = useState(() => tabFromPath() || "home");
  const [dark, setDark] = useState(false);
  const [notifs, setNotifs] = useState([]);
  const [userTabs, setUserTabs] = useState("__all__");
  const [showPw, setShowPw] = useState(false);
  const [authReady, setAuthReady] = useState(false);

  const handleLogout = useCallback(() => {
    try {
      postJson("/api/auth/logout", {}).catch(() => {});
    } catch (_) {
      // Best-effort revoke only.
    }
    setUser(null);
    setUserTabs("__all__");
    localStorage.removeItem("hol_user");
  }, []);

  // 접근 판정은 lib/permissions.js 한 곳에만 둔다 — 홈 카드도 같은 함수를 쓴다.
  const canAccess = useCallback(
    (tabKey) => canAccessTab(user, userTabs, tabKey),
    [user, userTabs],
  );

  useIdleLogout(handleLogout);

  useEffect(() => {
    setDark(localStorage.getItem("hol_dark") === "true");
    let cancelled = false;
    const onExpire = () => {
      setUser(null);
      setUserTabs("__all__");
      localStorage.removeItem("hol_user");
      setAuthReady(true);
    };
    window.addEventListener("flow:session-expired", onExpire);
    const stored = readStoredUser();
    if (!stored) {
      setAuthReady(true);
      return () => window.removeEventListener("flow:session-expired", onExpire);
    }
    sf("/api/auth/me")
      .then((data) => {
        if (cancelled) return;
        if (!data?.authenticated || !data?.username) {
          localStorage.removeItem("hol_user");
          setUser(null);
          setUserTabs("__all__");
          setAuthReady(true);
          return;
        }
        const nextUser = mergeSessionUser(stored, data);
        localStorage.setItem("hol_user", JSON.stringify(nextUser));
        setUser(nextUser);
        setUserTabs(nextUser.role === "admin" ? "__all__" : (nextUser.tabs || ""));
        setAuthReady(true);
      })
      .catch(() => {
        if (cancelled) return;
        setUser(null);
        setUserTabs("__all__");
        setAuthReady(true);
      });
    return () => {
      cancelled = true;
      window.removeEventListener("flow:session-expired", onExpire);
    };
  }, []);

  useEffect(() => {
    Object.entries(dark ? darkV : lightV).forEach(([key, value]) => {
      document.documentElement.style.setProperty(key, value);
    });
  }, [dark]);

  useEffect(() => {
    if (!user) return;
    const urlTab = tabFromPath();
    sf("/api/session/load?username=" + user.username)
      .then((data) => {
        if (data.last_tab && !urlTab && !REMOVED_TAB_KEYS.has(data.last_tab)) setTab(data.last_tab);
      })
      .catch(() => {});
    if (user.tabs) {
      setUserTabs(user.tabs);
    } else {
      sf("/api/admin/user-tabs?username=" + user.username)
        .then((data) => setUserTabs(data.tabs || ""))
        .catch(() => setUserTabs(""));
    }
    sf("/api/admin/my-page-admin")
      .then((data) => {
        const pages = Array.isArray(data?.pages) ? data.pages : [];
        setUser((prev) => {
          if (!prev || prev.username !== user.username) return prev;
          if ((prev.page_admins || []).join(",") === pages.join(",")) return prev;
          const merged = { ...prev, page_admins: pages };
          localStorage.setItem("hol_user", JSON.stringify(merged));
          return merged;
        });
      })
      .catch(() => {});
  }, [user]);

  useEffect(() => {
    if (!user) return;
    const openTab = (tabKey, search = "", push = false) => {
      if (!tabKey || (!canAccess(tabKey) && tabKey !== "admin")) return;
      startTransition(() => setTab(tabKey));
      if (push) {
        const nextUrl = `/${tabKey}${search || ""}`;
        if (window.location.pathname + window.location.search !== nextUrl) {
          window.history.pushState({ tab: tabKey }, "", nextUrl);
        }
      }
      logActivity(user.username, "nav:" + tabKey);
    };
    const onNavigate = (event) => {
      const detail = event.detail || {};
      openTab(detail.tab || tabFromPath(detail.path || ""), detail.search || "", true);
    };
    const onPopState = () => {
      const next = tabFromPath();
      if (next) openTab(next, window.location.search || "", false);
    };
    window.addEventListener("flow:navigate", onNavigate);
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("flow:navigate", onNavigate);
      window.removeEventListener("popstate", onPopState);
    };
  }, [canAccess, user]);

  useEffect(() => {
    if (!user) return;
    postJson("/api/session/save", { username: user.username, last_tab: tab }).catch(() => {});
  }, [tab, user]);

  const refreshNotifications = useCallback(() => {
    if (!user) return Promise.resolve();
    return sf("/api/admin/my-notifications?username=" + user.username)
      .then((data) => setNotifs(data.notifications || []))
      .catch(() => {});
  }, [user]);

  useEffect(() => {
    if (!user) return;
    refreshNotifications();
    const intervalId = setInterval(refreshNotifications, 30000);
    window.addEventListener("hol:notif-refresh", refreshNotifications);
    return () => {
      clearInterval(intervalId);
      window.removeEventListener("hol:notif-refresh", refreshNotifications);
    };
  }, [refreshNotifications, user]);

  const nav = useCallback(
    (tabKey) => {
      if (!canAccess(tabKey) && tabKey !== "admin") return;
      startTransition(() => setTab(tabKey));
      const nextUrl = `/${tabKey}`;
      if (window.location.pathname + window.location.search !== nextUrl) {
        window.history.pushState({ tab: tabKey }, "", nextUrl);
      }
      if (user) logActivity(user.username, "nav:" + tabKey);
    },
    [canAccess, user],
  );

  const handleLogin = useCallback((nextUser) => {
    setUser(nextUser);
    localStorage.setItem("hol_user", JSON.stringify(nextUser));
    setUserTabs(nextUser.role === "admin" ? "__all__" : (nextUser.tabs || ""));
  }, []);

  return {
    user,
    tab,
    dark,
    authReady,
    setDark,
    notifs,
    showPw,
    setShowPw,
    visibleTabs: visibleTabsFor(user, userTabs),
    tabInfo: TABS.find((item) => item.key === tab),
    handleLogin,
    handleLogout,
    nav,
    refreshNotifications,
  };
}
