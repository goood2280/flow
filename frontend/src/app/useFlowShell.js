import { startTransition, useCallback, useEffect, useRef, useState } from "react";

import { TABS } from "../config";
import { logActivity, postJson, sf } from "../lib/api";

const darkV = {
  "--bg-primary": "#1a1a1a",
  "--bg-secondary": "#262626",
  "--bg-card": "#2a2a2a",
  "--bg-hover": "#333",
  "--bg-tertiary": "#1a1a1a",
  "--text-primary": "#e5e5e5",
  "--text-secondary": "#a3a3a3",
  "--border": "#333",
  "--accent": "#f97316",
  "--accent-dim": "#ea580c",
  "--accent-glow": "rgba(249,115,22,0.15)",
  "--ok": "#22c55e",
  "--warn": "#f97316",
  "--bad": "#ef4444",
  "--info": "#3b82f6",
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
  "--accent": "#ea580c",
  "--accent-dim": "#c2410c",
  "--accent-glow": "rgba(234,88,12,0.1)",
  "--ok": "#16a34a",
  "--warn": "#ea580c",
  "--bad": "#dc2626",
  "--info": "#2563eb",
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

function toTabList(userTabs) {
  if (Array.isArray(userTabs)) return userTabs;
  if (typeof userTabs === "string") return userTabs.split(",");
  return [];
}

export function useFlowShell() {
  const [user, setUser] = useState(null);
  const [tab, setTab] = useState("home");
  const [dark, setDark] = useState(true);
  const [notifs, setNotifs] = useState([]);
  const [userTabs, setUserTabs] = useState("__all__");
  const [showPw, setShowPw] = useState(false);
  const [sidebarPolicy, setSidebarPolicy] = useState({ devguide_allowed: false });

  const handleLogout = useCallback(() => {
    try {
      postJson("/api/auth/logout", {}).catch(() => {});
    } catch (_) {
      // Best-effort revoke only.
    }
    setUser(null);
    localStorage.removeItem("hol_user");
  }, []);

  useIdleLogout(handleLogout);

  useEffect(() => {
    setUser(readStoredUser());
    setDark(localStorage.getItem("hol_dark") !== "false");
    const onExpire = () => setUser(null);
    window.addEventListener("flow:session-expired", onExpire);
    return () => window.removeEventListener("flow:session-expired", onExpire);
  }, []);

  useEffect(() => {
    Object.entries(dark ? darkV : lightV).forEach(([key, value]) => {
      document.documentElement.style.setProperty(key, value);
    });
  }, [dark]);

  useEffect(() => {
    if (!user) return;
    sf("/api/session/load?username=" + user.username)
      .then((data) => {
        if (data.last_tab) setTab(data.last_tab);
      })
      .catch(() => {});
    if (user.tabs) {
      setUserTabs(user.tabs);
    } else {
      sf("/api/admin/user-tabs?username=" + user.username)
        .then((data) => setUserTabs(data.tabs || "filebrowser,dashboard,splittable"))
        .catch(() => {});
    }
  }, [user]);

  useEffect(() => {
    if (!user) {
      setSidebarPolicy({ devguide_allowed: false });
      return;
    }
    sf("/api/admin/settings")
      .then((data) => setSidebarPolicy({ devguide_allowed: !!data?.devguide_allowed }))
      .catch(() => setSidebarPolicy({ devguide_allowed: false }));
  }, [user]);

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

  const canAccess = useCallback(
    (tabKey) => {
      if (tabKey === "home") return true;
      if (userTabs === "__all__") return true;
      const tabConfig = TABS.find((item) => item.key === tabKey);
      if (tabConfig?.adminOnly && user?.role !== "admin") return false;
      if (tabConfig?.restrictedSetting && user?.role !== "admin" && !sidebarPolicy[tabConfig.restrictedSetting]) {
        return false;
      }
      return toTabList(userTabs).includes(tabKey);
    },
    [sidebarPolicy, user?.role, userTabs],
  );

  const nav = useCallback(
    (tabKey) => {
      if (!canAccess(tabKey) && tabKey !== "admin") return;
      startTransition(() => setTab(tabKey));
      if (user) logActivity(user.username, "nav:" + tabKey);
    },
    [canAccess, user],
  );

  const handleLogin = useCallback((nextUser) => {
    setUser(nextUser);
    localStorage.setItem("hol_user", JSON.stringify(nextUser));
    if (nextUser.tabs) setUserTabs(nextUser.tabs);
  }, []);

  return {
    user,
    tab,
    dark,
    setDark,
    notifs,
    showPw,
    setShowPw,
    visibleTabs: TABS.filter((item) => item.key !== "home" && canAccess(item.key)),
    tabInfo: TABS.find((item) => item.key === tab),
    handleLogin,
    handleLogout,
    nav,
    refreshNotifications,
  };
}
