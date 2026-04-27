import { lazy } from "react";

export const PAGE_MAP = {
  home: lazy(() => import("../pages/My_Home")),
  filebrowser: lazy(() => import("../pages/My_FileBrowser")),
  splittable: lazy(() => import("../pages/My_SplitTable")),
  dashboard: lazy(() => import("../pages/My_Dashboard")),
  ettime: lazy(() => import("../pages/My_ETTime")),
  waferlayout: lazy(() => import("../pages/My_WaferLayout")),
  tracker: lazy(() => import("../pages/My_Tracker")),
  inform: lazy(() => import("../pages/My_Inform")),
  calendar: lazy(() => import("../pages/My_Calendar")),
  meeting: lazy(() => import("../pages/My_Meeting")),
  tablemap: lazy(() => import("../pages/My_TableMap")),
  ml: lazy(() => import("../pages/My_ML")),
  devguide: lazy(() => import("../pages/My_DevGuide")),
  admin: lazy(() => import("../pages/My_Admin")),
};
