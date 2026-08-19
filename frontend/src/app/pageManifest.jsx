import { lazy } from "react";

export const PAGE_GROUPS = [
  { id: "main", label: "홈", direct: true },
  { id: "data", label: "데이터" },
  { id: "work", label: "업무" },
  { id: "agent", label: "에이전트", direct: true },
  { id: "system", label: "관리" },
];

const definitions = [
  { key: "home", label: "홈", icon: "🏠", group: "main", layout: "landing", helpId: "home", defaultEnabled: true, load: () => import("../pages/My_Home") },
  { key: "filebrowser", label: "파일탐색기", icon: "📂", group: "data", layout: "explorer", helpId: "filebrowser", defaultEnabled: true, subtabs: [{ key: "db", label: "DB" }, { key: "files", label: "Files" }], load: () => import("../pages/My_FileBrowser") },
  { key: "dashboard", label: "대시보드", icon: "📊", group: "data", layout: "analysis", scrollMode: "locked", helpId: "dashboard", defaultEnabled: true, load: () => import("../pages/My_Dashboard") },
  { key: "splittable", label: "스플릿 테이블", icon: "🗂️", group: "data", layout: "explorer", helpId: "splittable", defaultEnabled: true, subtabs: [{ key: "view", label: "View" }, { key: "history", label: "History" }], load: () => import("../pages/My_SplitTable") },
  { key: "lotmanage", label: "랏 관리", icon: "🏷️", group: "data", layout: "explorer", helpId: "lotmanage", defaultEnabled: true, designSystem: true, load: () => import("../pages/My_LotManagement") },
  { key: "ramcache", label: "캐시 관리", icon: "🧠", group: "data", layout: "admin", helpId: "ramcache", defaultEnabled: false, load: () => import("../pages/My_RamCache") },
  { key: "matchfill", label: "매칭 채우기", icon: "🧩", group: "data", layout: "workflow", helpId: "matchfill", defaultEnabled: false, load: () => import("../pages/My_MatchFill") },

  { key: "chartbuilder", label: "차트생성", icon: "📈", group: "work", layout: "analysis", helpId: "chartbuilder", defaultEnabled: true, load: () => import("../pages/My_ChartBuilder") },
  { key: "templatereport", label: "Template Report", icon: "🖼️", group: "work", layout: "workflow", helpId: "templatereport", defaultEnabled: true, load: () => import("../pages/My_TemplateReport") },
  { key: "autoreport", label: "Auto report", icon: "📑", group: "work", layout: "workflow", helpId: "autoreport", defaultEnabled: true, load: () => import("../pages/My_AutoReport") },
  { key: "lotrequest", label: "랏 배정/요청", icon: "📨", group: "work", layout: "workboard", helpId: "lotrequest", defaultEnabled: true, load: () => import("../pages/My_LotRequest") },
  { key: "inform", label: "인폼 로그", icon: "📢", group: "work", layout: "workboard", helpId: "inform", defaultEnabled: false, subtabs: [{ key: "inform", label: "인폼" }, { key: "matrix", label: "매트릭스" }, { key: "audit", label: "로그" }], load: () => import("../pages/My_Inform") },
  { key: "meeting", label: "회의관리", icon: "🗓", group: "work", layout: "workboard", helpId: "meeting", defaultEnabled: false, load: () => import("../pages/My_Meeting") },
  { key: "calendar", label: "변경점 관리", icon: "📅", group: "work", layout: "workboard", helpId: "calendar", defaultEnabled: false, load: () => import("../pages/My_Calendar") },
  { key: "tracker", label: "ET 추적", icon: "📋", group: "work", layout: "workboard", helpId: "tracker", defaultEnabled: false, load: () => import("../pages/My_Tracker") },
  { key: "valve", label: "매칭알람", icon: "🚨", group: "work", layout: "workboard", helpId: "valve", defaultEnabled: false, load: () => import("../pages/My_ValveAlerts") },
  { key: "teg", label: "TEG 위치 조회", icon: "📐", group: "work", layout: "analysis", helpId: "teg", defaultEnabled: false, load: () => import("../pages/My_TegMap") },
  { key: "yieldmap", label: "Yield Map", icon: "◫", group: "work", layout: "analysis", helpId: "yieldmap", defaultEnabled: false, load: () => import("../pages/My_YieldMap") },
  { key: "ettime", label: "ET 측정시간", icon: "⏱️", group: "work", layout: "analysis", helpId: "ettime", defaultEnabled: false, load: () => import("../pages/My_EtTime") },
  { key: "reformatize", label: "ET 다운로드", icon: "🧮", group: "work", layout: "workflow", helpId: "reformatize", defaultEnabled: false, load: () => import("../pages/My_Reformatize") },
  { key: "dcop", label: "양산DCOP 검사", icon: "✅", group: "work", layout: "workflow", helpId: "dcop", defaultEnabled: false, load: () => import("../pages/My_DcopCheck") },

  { key: "diagnosis", label: "에이전트", icon: "🤖", group: "agent", layout: "analysis", helpId: "diagnosis", defaultEnabled: true, subtabs: [{ key: "catalog", label: "기능 카탈로그" }, { key: "runtime", label: "실행 추적" }, { key: "workflows", label: "Workflow 템플릿" }], load: () => import("../pages/My_Diagnosis") },

  { key: "admin", label: "관리자", icon: "⚙️", group: "system", layout: "admin", helpId: "admin", adminOnly: true, defaultEnabled: false, load: () => import("../pages/My_Admin") },
  { key: "devguide", label: "개발자 가이드", icon: "📖", group: "system", layout: "admin", helpId: "devguide", adminOnly: true, strictAdmin: true, defaultEnabled: false, load: () => import("../pages/My_DevGuide") },

  /* Internal route: available to embedded/admin flows but intentionally absent from navigation. */
  { key: "tablemap", label: "테이블 맵", icon: "🗺️", group: "system", layout: "analysis", helpId: "tablemap", navigation: false, defaultEnabled: false, load: () => import("../pages/My_TableMap") },
  { key: "knowledge", label: "지식", icon: "📚", group: "agent", layout: "workboard", helpId: "knowledge", navigation: false, defaultEnabled: false, load: () => import("../pages/My_Knowledge") },
];

export const PAGE_MANIFEST = definitions.map((definition) => ({
  ...definition,
  component: lazy(definition.load),
}));

export const PAGE_BY_KEY = Object.fromEntries(PAGE_MANIFEST.map((page) => [page.key, page]));
export const PAGE_MAP = Object.fromEntries(PAGE_MANIFEST.map((page) => [page.key, page.component]));

export const TABS = PAGE_MANIFEST
  .filter((page) => page.navigation !== false)
  .map(({ component, load, subtabs, ...page }) => ({
    ...page,
    defaultTab: page.defaultEnabled,
  }));

export const SUB_TABS = Object.fromEntries(
  PAGE_MANIFEST.filter((page) => page.subtabs?.length).map((page) => [page.key, page.subtabs]),
);

export function buildNavGroups(visibleTabs = []) {
  return PAGE_GROUPS.map((group) => ({
    ...group,
    items: visibleTabs.filter((tab) => tab.group === group.id),
  })).filter((group) => group.items.length > 0);
}
