# Permission Matrix

Generated from `backend/routers/*.py` and `frontend/src` API call sites.
`admin_settings.json` is intentionally not embedded in this report.

| endpoint | method | backend gate | FE caller(file:line) | FE gate | risk |
|---|---:|---|---|---|---|
| `/api/admin/activity/features` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:1019 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/activity/summary` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:1018 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/all-notifications` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:211, frontend/src/pages/My_DevGuide.jsx:160 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/approve` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:398, frontend/src/pages/My_DevGuide.jsx:154 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/restore` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:954, frontend/src/pages/My_Admin.jsx:1602 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/run` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:949, frontend/src/pages/My_Admin.jsx:1593 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/schedule` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:961, frontend/src/pages/My_Admin.jsx:964 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/status` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:931, frontend/src/pages/My_Admin.jsx:1577 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/base-csv` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:2681, frontend/src/pages/My_Admin.jsx:2681, frontend/src/pages/My_Admin.jsx:2698, frontend/src/pages/My_Admin.jsx:2698 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/base-csv` | `PUT` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:2681, frontend/src/pages/My_Admin.jsx:2681, frontend/src/pages/My_Admin.jsx:2698, frontend/src/pages/My_Admin.jsx:2698 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/broadcast` | `POST` | `require_admin` | frontend/src/pages/My_DevGuide.jsx:158 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/bulk-users` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:290 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/delete-user` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:403 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/dismiss` | `POST` | `owner_or_admin, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/dismiss-batch` | `POST` | `owner_or_admin, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/download-history` | `GET` | `require_admin` | - | - | `ok` |
| `/api/admin/ettime/download-log` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:243 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/log` | `POST` | `current_user, legacy_self_service_exception` | frontend/src/lib/api.js:155, frontend/src/pages/My_Admin.jsx:215, frontend/src/pages/My_Admin.jsx:218, frontend/src/pages/My_Admin.jsx:228, frontend/src/pages/My_DevGuide.jsx:162, frontend/src/pages/My_DevGuide.jsx:163 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/logs` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:215, frontend/src/pages/My_Admin.jsx:218, frontend/src/pages/My_Admin.jsx:228, frontend/src/pages/My_DevGuide.jsx:163 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/logs/users` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:215 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/mark-read` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/App.jsx:449, frontend/src/pages/My_Admin.jsx:299, frontend/src/pages/My_DevGuide.jsx:161 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/mark-read-batch` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/App.jsx:449, frontend/src/pages/My_Admin.jsx:299 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/my-notifications` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:311, frontend/src/pages/My_DevGuide.jsx:159 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/my-page-admin` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:251 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/notify-rules` | `GET` | `current_user, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/notify-rules` | `POST` | `current_user, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/page-admins` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:800, frontend/src/pages/My_Admin.jsx:800, frontend/src/pages/My_Admin.jsx:820, frontend/src/pages/My_Admin.jsx:820, frontend/src/pages/My_Admin.jsx:839, frontend/src/pages/My_Admin.jsx:839, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/page-admins` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:800, frontend/src/pages/My_Admin.jsx:800, frontend/src/pages/My_Admin.jsx:820, frontend/src/pages/My_Admin.jsx:820, frontend/src/pages/My_Admin.jsx:839, frontend/src/pages/My_Admin.jsx:839, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/qa/report` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:249 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/qa/trigger` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:593 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/reject` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:399, frontend/src/pages/My_DevGuide.jsx:155 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/reset-password` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:276, frontend/src/pages/My_DevGuide.jsx:156 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/send-inquiry` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:207 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/send-message` | `POST` | `require_admin` | frontend/src/pages/My_DevGuide.jsx:157 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/set-email` | `POST` | `require_admin` | - | - | `ok` |
| `/api/admin/set-name` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:389 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/set-tabs` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:284 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/settings` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:299, frontend/src/pages/My_Admin.jsx:820, frontend/src/pages/My_Admin.jsx:865, frontend/src/pages/My_Admin.jsx:940, frontend/src/pages/My_Admin.jsx:1136, frontend/src/pages/My_Admin.jsx:1144, +12 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/settings/save` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:865, frontend/src/pages/My_Admin.jsx:940, frontend/src/pages/My_Admin.jsx:1144, frontend/src/pages/My_Admin.jsx:1463, frontend/src/pages/My_Admin.jsx:1585, frontend/src/pages/My_Admin.jsx:1614, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/tracker-schema-migrate` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:1762 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/user-tabs` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:247 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/users` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:213, frontend/src/pages/My_DevGuide.jsx:153 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/knowledge/ingest` | `POST` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:1107 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/knowledge/list` | `GET` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:1098, frontend/src/components/agent/AgentLegacy.jsx:1111 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/matching/apply` | `POST` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:1005 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/matching/suggest` | `POST` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:999 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/rulebook/apply` | `POST` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:1051 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/rulebook/suggest` | `POST` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:1045 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/status` | `GET` | `require_admin` | - | - | `ok` |
| `/api/agent/item-rules` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:920 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/knowledge-inventory` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:424, frontend/src/components/agent/AgentLegacy.jsx:435 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/knowledge-inventory/promote` | `POST` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:435 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/persona` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:271 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/prompt-history` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:2266 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/prompt-preview` | `POST` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:347 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/prompt-review` | `POST` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:2334 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/recent-rag` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:879 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/resolve_term` | `GET` | `current_user` | - | - | `ok` |
| `/api/agent/schema-relations/delete` | `POST` | `require_page_manager:diagnosis` | frontend/src/components/agent/AgentLegacy.jsx:1621 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/schema-relations/graph` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:1492 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/schema-relations/preview` | `POST` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:1575 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/schema-relations/save` | `POST` | `require_page_manager:diagnosis` | frontend/src/components/agent/AgentLegacy.jsx:1606 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/schema-relations/scan` | `POST` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:1590 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/schema_doc/ai-draft` | `POST` | `require_page_manager:diagnosis` | - | - | `ok` |
| `/api/agent/schema_doc/ai-upsert` | `POST` | `require_page_manager:diagnosis` | - | - | `ok` |
| `/api/agent/schema_doc/scan_sources` | `POST` | `require_page_manager:diagnosis` | - | - | `ok` |
| `/api/agent/wiki/ingest/commit` | `POST` | `require_page_manager:diagnosis` | frontend/src/components/agent/AgentLegacy.jsx:602, frontend/src/components/agent/WikiTab.jsx:276 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/ingest/preview` | `POST` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:584, frontend/src/components/agent/WikiTab.jsx:260 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/lint` | `POST` | `require_page_manager:diagnosis` | frontend/src/components/agent/AgentLegacy.jsx:694, frontend/src/components/agent/WikiTab.jsx:315 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/log` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:538, frontend/src/components/agent/AgentLegacy.jsx:698 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/page` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:537, frontend/src/components/agent/AgentLegacy.jsx:624, frontend/src/components/agent/AgentLegacy.jsx:659, frontend/src/components/agent/AgentLegacy.jsx:681, frontend/src/components/agent/WikiTab.jsx:188, frontend/src/components/agent/WikiTab.jsx:210, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/page/delete` | `POST` | `require_page_manager:diagnosis` | frontend/src/components/agent/AgentLegacy.jsx:681, frontend/src/components/agent/WikiTab.jsx:302 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/page/save` | `POST` | `require_page_manager:diagnosis` | frontend/src/components/agent/AgentLegacy.jsx:659 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/pages` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:537, frontend/src/components/agent/WikiTab.jsx:188 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/search` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:634, frontend/src/components/agent/AgentLegacy.jsx:646 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/source` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:536, frontend/src/components/agent/AgentLegacy.jsx:560, frontend/src/components/agent/WikiTab.jsx:187, frontend/src/components/agent/WikiTab.jsx:240 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/sources` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:536, frontend/src/components/agent/AgentLegacy.jsx:536, frontend/src/components/agent/AgentLegacy.jsx:560, frontend/src/components/agent/AgentLegacy.jsx:560, frontend/src/components/agent/WikiTab.jsx:187, frontend/src/components/agent/WikiTab.jsx:187, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/wiki/sources` | `POST` | `require_page_manager:diagnosis` | frontend/src/components/agent/AgentLegacy.jsx:536, frontend/src/components/agent/AgentLegacy.jsx:536, frontend/src/components/agent/AgentLegacy.jsx:560, frontend/src/components/agent/AgentLegacy.jsx:560, frontend/src/components/agent/WikiTab.jsx:187, frontend/src/components/agent/WikiTab.jsx:187, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/workflow` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:235 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/analytics/correlation` | `POST` | `current_user` | - | - | `ok` |
| `/api/analytics/trend` | `POST` | `current_user` | - | - | `ok` |
| `/api/auth/change-password` | `POST` | `current_user` | frontend/src/App.jsx:520 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/forgot-password` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/pages/My_Login.jsx:162 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/login` | `POST` | `session_middleware` | frontend/src/main.jsx:15, frontend/src/pages/My_DevGuide.jsx:148, frontend/src/pages/My_Login.jsx:150 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/logout` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/app/useFlowShell.js:159 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/me` | `GET` | `session_middleware` | frontend/src/app/useFlowShell.js:202 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/register` | `POST` | `session_middleware` | frontend/src/main.jsx:15, frontend/src/pages/My_DevGuide.jsx:149, frontend/src/pages/My_Login.jsx:156 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/reset-request` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/pages/My_DevGuide.jsx:150 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/set-name` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/categories` | `GET` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:351, frontend/src/pages/My_Meeting.jsx:1694 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/calendar/categories/save` | `POST` | `require_page_manager:calendar` | frontend/src/pages/My_Meeting.jsx:1694 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/calendar/event` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/status` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/{eid}` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/events` | `GET` | `current_user` | - | - | `ok` |
| `/api/calendar/events/search` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/meetings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/settings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/settings/save` | `POST` | `require_page_manager:calendar` | - | - | `ok` |
| `/api/catalog/matching/download` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1861 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/list` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1845 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/preview` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1856 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/save` | `POST` | `require_page_manager:splittable` | frontend/src/pages/My_Admin.jsx:1873 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/schema` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/catalog/product/list` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1953, frontend/src/pages/My_WaferLayout.jsx:433 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/product/load` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1962 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/product/save` | `POST` | `require_page_manager:tablemap` | frontend/src/pages/My_Admin.jsx:1963 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/artifacts` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1998 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/config` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1997, frontend/src/pages/My_Admin.jsx:2008 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/config/save` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_Admin.jsx:2008 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/status` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1999 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/sync` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_Admin.jsx:2009 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/charts/spec` | `POST` | `current_user` | - | - | `ok` |
| `/api/dashboard/apply-default` | `POST` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/chart-defaults` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/chart-defaults` | `POST` | `require_page_manager:dashboard, is_page_manager, current_user` | - | - | `ok` |
| `/api/dashboard/chart-refine` | `POST` | `current_user` | - | - | `ok` |
| `/api/dashboard/charts` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/charts/copy` | `POST` | `require_page_manager:dashboard` | - | - | `ok` |
| `/api/dashboard/charts/delete` | `POST` | `require_page_manager:dashboard` | - | - | `ok` |
| `/api/dashboard/charts/save` | `POST` | `require_page_manager:dashboard` | - | - | `ok` |
| `/api/dashboard/columns` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dashboard/data` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/fab-progress` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/items` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/layout` | `POST` | `current_user` | - | - | `ok` |
| `/api/dashboard/multi-db-chart` | `POST` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dashboard/products` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/refresh` | `POST` | `require_page_manager:dashboard` | - | - | `ok` |
| `/api/dashboard/snapshots` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/stuck-lots` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/summary` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/trend-alerts` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/data/query-et` | `POST` | `current_user` | - | - | `ok` |
| `/api/data/query-inline` | `POST` | `current_user` | - | - | `ok` |
| `/api/dbmap/config` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/db-ref/add` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/db-ref/delete` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/db-ref/description` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/db-ref/info` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/db-sources` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/groups` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/groups/delete` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/groups/save` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/groups/{group_id}` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/lineage` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/node/color` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/node/position` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/nodes/unlink` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/ontology` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/product-config` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/product-config/delete` | `DELETE` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/product-config/delete` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/product-config/save` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/product-configs` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/product-pages` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/product-pages/delete` | `DELETE` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/product-pages/delete` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/product-pages/hide` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/product-pages/unhide` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/relations/delete` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/relations/label-position` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/relations/save` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/tables` | `GET` | `session_middleware` | frontend/src/pages/My_SplitTable.jsx:142, frontend/src/pages/My_TableMap.jsx:1128 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/dbmap/tables/delete` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/tables/import` | `POST` | `require_page_admin:tablemap` | frontend/src/pages/My_TableMap.jsx:1128 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/dbmap/tables/save` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/tables/{table_id}` | `GET` | `session_middleware` | frontend/src/pages/My_TableMap.jsx:1128 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/dbmap/tables/{table_id}/auto-load` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/version-content` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dbmap/versions/rollback` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/versions/{table_id}` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/diagnosis/knowledge` | `GET` | `current_user` | - | - | `ok` |
| `/api/diagnosis/knowledge/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/diagnosis/run` | `POST` | `current_user` | - | - | `ok` |
| `/api/diagnosis/{run_id}` | `GET` | `current_user` | - | - | `ok` |
| `/api/ettime/lot/{root_lot_id}` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/ettime/lots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/ettime/products` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/ettime/report` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/ettime/report/csv` | `GET` | `current_user` | - | - | `ok` |
| `/api/ettime/report/pdf` | `GET` | `current_user` | - | - | `ok` |
| `/api/ettime/report/pptx` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/base-file-save` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/base-file-view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/base-file/delete` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/base-file/migrate-history` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/base-file/rollback` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/base-file/save` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/base-file/save/` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/base-file/text-save` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/base-file/validate` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/base-file/version-content` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/base-file/versions` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/base-files` | `GET` | `session_middleware` | frontend/src/components/agent/AgentLegacy.jsx:1099, frontend/src/components/agent/AgentLegacy.jsx:1498 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/cache/cleanup` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/cleanup-candidates` | `GET` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/llm/refresh` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/match/refresh` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/match/settings` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/match/status` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/domain` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/download-csv` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/download-history` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:240 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/parquet-meta` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/parquet-meta/invalidate` | `POST` | `current_user` | - | - | `ok` |
| `/api/filebrowser/products` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/root-parquet-view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/root-parquets` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/roots` | `GET` | `session_middleware` | frontend/src/pages/My_DevGuide.jsx:166 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/schema/snapshot` | `POST` | `current_user` | - | - | `ok` |
| `/api/filebrowser/schema/snapshots` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/scopes` | `GET` | `session_middleware` | frontend/src/pages/My_FileBrowser.jsx:336 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/scopes/roots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/settings` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/settings` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/settings/llm/draft` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/sql-guide` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/sql/llm/draft` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/groups/audit` | `GET` | `require_admin` | - | - | `ok` |
| `/api/groups/create` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2359 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/delete` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2364 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/eligible-users` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2353 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/list` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2352, frontend/src/pages/My_Calendar.jsx:92, frontend/src/pages/My_Dashboard.jsx:1656, frontend/src/pages/My_Inform.jsx:2283, frontend/src/pages/My_Meeting.jsx:320, frontend/src/pages/My_Meeting.jsx:353, +1 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/lots/add` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2373 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/lots/remove` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2376 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/members/add` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2366 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/members/remove` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2369 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/mine` | `GET` | `current_user` | - | - | `ok` |
| `/api/groups/modules/set` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2379 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/my-modules` | `GET` | `current_user` | - | - | `ok` |
| `/api/groups/update` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2382, frontend/src/pages/My_Admin.jsx:2504, frontend/src/pages/My_Admin.jsx:2512 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/home/release-notes` | `GET` | `current_user` | - | - | `ok` |
| `/api/home/summary` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2561, frontend/src/pages/My_Admin.jsx:2561, +58 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2561, frontend/src/pages/My_Admin.jsx:2561, +58 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/audit-log` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/bulk-create` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:21 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/by-lot` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/by-product` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/check` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/config` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2561, frontend/src/pages/My_Admin.jsx:2561, frontend/src/pages/My_Inform.jsx:16, frontend/src/pages/My_Inform.jsx:16, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/config` | `POST` | `require_page_manager:inform` | frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2561, frontend/src/pages/My_Admin.jsx:2561, frontend/src/pages/My_Inform.jsx:16, frontend/src/pages/My_Inform.jsx:16, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/dashboard-data` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/deadline` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/delete` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/informs/edit` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/informs/eligible-contacts` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:2281, frontend/src/pages/My_Inform.jsx:2282 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/files/{uid}/{name}` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/lot-matrix` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/lots` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/mail-groups` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:20 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2584 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/knob-map` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2584, frontend/src/pages/My_Admin.jsx:2584 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/knob-map` | `POST` | `require_page_manager:inform, is_page_manager, current_user` | frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2584, frontend/src/pages/My_Admin.jsx:2584 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/recipients` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/modules/summary` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/my` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:2168 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/my-modules` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:2168 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2242, frontend/src/pages/My_Inform.jsx:2242, frontend/src/pages/My_Inform.jsx:2253, frontend/src/pages/My_Inform.jsx:2253, frontend/src/pages/My_Inform.jsx:2254, frontend/src/pages/My_Inform.jsx:2254, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2242, frontend/src/pages/My_Inform.jsx:2242, frontend/src/pages/My_Inform.jsx:2253, frontend/src/pages/My_Inform.jsx:2253, frontend/src/pages/My_Inform.jsx:2254, frontend/src/pages/My_Inform.jsx:2254, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/bulk-add` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2289 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/delete` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2272 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/update` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2253 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-lots` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/product/add` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/product/add` | `PATCH` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/informs/product/add` | `POST` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/informs/product/add` | `PUT` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/informs/products` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/products` | `PATCH` | `session_middleware` | - | - | `ok` |
| `/api/informs/products` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/informs/products` | `PUT` | `session_middleware` | - | - | `ok` |
| `/api/informs/products/add` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/products/add` | `PATCH` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/informs/products/add` | `POST` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/informs/products/add` | `PUT` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/informs/products/dedup` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/products/delete` | `POST` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/informs/recent` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/recipients` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:19, frontend/src/pages/My_Meeting.jsx:354 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/settings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/settings` | `POST` | `require_page_manager:inform` | - | - | `ok` |
| `/api/informs/sidebar` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/splittable` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:18, frontend/src/pages/My_Inform.jsx:2384, frontend/src/pages/My_Inform.jsx:2586, frontend/src/pages/My_Inform.jsx:4682 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/splittable-sets` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/splittable-snapshot` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:18, frontend/src/pages/My_Inform.jsx:2384, frontend/src/pages/My_Inform.jsx:2586, frontend/src/pages/My_Inform.jsx:4682 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/status` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/upload` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1044, frontend/src/pages/My_Inform.jsx:2492, frontend/src/pages/My_Inform.jsx:2515, frontend/src/pages/My_SplitTable.jsx:563 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/upload-attachment` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/user-modules` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:3570, frontend/src/pages/My_Inform.jsx:3585, frontend/src/pages/My_Inform.jsx:3593 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/user-modules/clear` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:3585 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/user-modules/save` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:3593 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/wafers` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}` | `DELETE` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2545, frontend/src/pages/My_Admin.jsx:2546, frontend/src/pages/My_Admin.jsx:2561, frontend/src/pages/My_Admin.jsx:2584, frontend/src/pages/My_Inform.jsx:16, frontend/src/pages/My_Inform.jsx:18, +24 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/{inform_id}/comments` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/comments` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/comments/{cid}/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/comments/{cid}/edit` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/edit` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/informs/{inform_id}/edit` | `PUT` | `session_middleware` | - | - | `ok` |
| `/api/informs/{inform_id}/history` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/mail-preview` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/send-mail` | `POST` | `current_user` | - | - | `ok` |
| `/api/items/resolve` | `POST` | `current_user` | - | - | `ok` |
| `/api/items/search` | `GET` | `current_user` | - | - | `ok` |
| `/api/knowledge/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/knowledge/rca` | `GET` | `current_user` | - | - | `ok` |
| `/api/knowledge/rca/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/llm/chat` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/admin/update` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:1155 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/agent/chat` | `POST` | `current_user` | frontend/src/pages/My_Dashboard.jsx:2159, frontend/src/pages/My_Diagnosis.jsx:35, frontend/src/components/agent/AgentLegacy.jsx:1850, frontend/src/components/agent/AgentLegacy.jsx:2358 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/chat` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:162 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/edm/execute` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:704 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/edm/propose` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:165 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:1132, frontend/src/pages/My_Admin.jsx:1176, frontend/src/pages/My_Home.jsx:1006 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback/promote` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:1176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback/summary` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:1132 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/function-call/preview` | `POST` | `require_admin` | - | - | `ok` |
| `/api/llm/flowi/inform/confirm` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/confirm` | `POST` | `require_admin, current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/resolve` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/start` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/orchestrator/preview` | `POST` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:26, frontend/src/components/agent/AgentLegacy.jsx:18, frontend/src/components/agent/AgentLegacy.jsx:1776 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/persona` | `GET` | `require_admin` | frontend/src/components/agent/AgentLegacy.jsx:272, frontend/src/components/agent/AgentLegacy.jsx:272 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/persona` | `POST` | `require_admin, current_user` | frontend/src/components/agent/AgentLegacy.jsx:272, frontend/src/components/agent/AgentLegacy.jsx:272 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/persona-card` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:272 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/profile` | `GET` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/profile` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/verify` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:104 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/status` | `GET` | `current_user` | frontend/src/pages/My_Calendar.jsx:93, frontend/src/pages/My_Dashboard.jsx:2149, frontend/src/pages/My_Home.jsx:89, frontend/src/components/agent/LlmCfgPanel.jsx:56, frontend/src/components/agent/LlmTab.jsx:14 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/test` | `POST` | `require_admin` | frontend/src/components/agent/LlmCfgPanel.jsx:124 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/create` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1555, frontend/src/pages/My_Meeting.jsx:1890 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/delete` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1575, frontend/src/pages/My_Meeting.jsx:1899 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/list` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:1356, frontend/src/pages/My_Inform.jsx:1409, frontend/src/pages/My_Inform.jsx:4640, frontend/src/pages/My_Meeting.jsx:319, frontend/src/pages/My_Tracker.jsx:1296 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/members/add` | `POST` | `current_user` | - | - | `ok` |
| `/api/mail-groups/members/remove` | `POST` | `current_user` | - | - | `ok` |
| `/api/mail-groups/update` | `POST` | `current_user` | frontend/src/pages/My_Meeting.jsx:1890 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/match/area-rollup` | `GET` | `session_middleware` | frontend/src/constants/processAreas.js:9, frontend/src/pages/My_Admin.jsx:1858, frontend/src/pages/My_Admin.jsx:1906 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/match/areas` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/meetings/action/push` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/action/unpush` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/add` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/ask` | `POST` | `current_user` | frontend/src/pages/My_Calendar.jsx:113 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/categories` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/meetings/categories/save` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/meetings/create` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/decision/push` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/decision/unpush` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/list` | `GET` | `current_user` | - | - | `ok` |
| `/api/meetings/minutes/append` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/minutes/append/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/minutes/save` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/session/add` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/session/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/session/mail-preview` | `POST` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:1993 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/session/send-mail` | `POST` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:2000 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/session/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/stream` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:369 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/{mid}` | `GET` | `current_user` | frontend/src/pages/My_Calendar.jsx:113, frontend/src/pages/My_Meeting.jsx:369, frontend/src/pages/My_Meeting.jsx:1993, frontend/src/pages/My_Meeting.jsx:2000 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/mark_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Admin.jsx:2083, frontend/src/pages/My_Home.jsx:1278 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notice_create` | `POST` | `owner_or_admin` | frontend/src/App.jsx:272, frontend/src/pages/My_Admin.jsx:2149, frontend/src/pages/My_Home.jsx:1344 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notice_delete` | `POST` | `owner_or_admin` | frontend/src/App.jsx:278, frontend/src/pages/My_Admin.jsx:2153, frontend/src/pages/My_Home.jsx:1349 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notices` | `GET` | `owner_or_admin` | frontend/src/pages/My_Admin.jsx:2146, frontend/src/pages/My_Home.jsx:1339 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/reply` | `POST` | `owner_or_admin` | frontend/src/App.jsx:266, frontend/src/pages/My_Admin.jsx:2085, frontend/src/pages/My_Home.jsx:1280 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/thread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:235, frontend/src/App.jsx:240, frontend/src/pages/My_Admin.jsx:2079, frontend/src/pages/My_Admin.jsx:2080, frontend/src/pages/My_Home.jsx:1274, frontend/src/pages/My_Home.jsx:1275 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/threads` | `GET` | `owner_or_admin` | frontend/src/App.jsx:235, frontend/src/pages/My_Admin.jsx:2079, frontend/src/pages/My_Home.jsx:1274 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/unread` | `GET` | `owner_or_admin` | - | - | `ok` |
| `/api/messages/mark_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Home.jsx:1192 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/notice_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Home.jsx:1206 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/notices` | `GET` | `session_middleware` | frontend/src/App.jsx:232, frontend/src/App.jsx:396, frontend/src/pages/My_Home.jsx:1194 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/send` | `POST` | `owner_or_admin` | frontend/src/App.jsx:260, frontend/src/pages/My_Home.jsx:1202 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/thread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:228, frontend/src/pages/My_Home.jsx:1191 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/unread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:223 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/ml/columns` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/ml/config` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/ml/inline_corr_search` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/inline_et_overview` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/knob_lineage_summary` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/model_flow` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/pareto` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/ppid_stratify` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/predict` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/process_window` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/shot_interp` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/sources` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/ml/train` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/transfer` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/ml/wf_map` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/monitor/farm-status` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:252 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/heartbeat` | `POST` | `session_middleware` | frontend/src/pages/My_DevGuide.jsx:181, frontend/src/pages/My_DevGuide.jsx:447 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/history` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/monitor/load/start` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:256 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/load/stop` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:267 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/resource-log` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:251 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/state` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/monitor/system` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:250, frontend/src/pages/My_DevGuide.jsx:180 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/rca/knowledge` | `GET` | `current_user` | - | - | `ok` |
| `/api/rca/knowledge/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/reformatter/preview` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/products` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/report-profiles` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/rules` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/rules/save` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/schema` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/table` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/table/save` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/reformatter/validate` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/s3ingest/available` | `GET` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:801 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config` | `GET` | `require_page_manager:filebrowser` | frontend/src/components/AwsPanel.jsx:21, frontend/src/components/AwsPanel.jsx:44, frontend/src/components/AwsPanel.jsx:61, frontend/src/pages/My_Admin.jsx:2197, frontend/src/pages/My_Admin.jsx:2219, frontend/src/pages/My_Admin.jsx:2236, +1 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config/delete` | `POST` | `require_page_manager:filebrowser` | frontend/src/components/AwsPanel.jsx:61, frontend/src/pages/My_Admin.jsx:2236 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config/save` | `POST` | `require_page_manager:filebrowser` | frontend/src/components/AwsPanel.jsx:44, frontend/src/pages/My_Admin.jsx:2219 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/delete` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:841 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/health` | `GET` | `session_middleware` | frontend/src/components/S3StatusLight.jsx:22 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/history` | `GET` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:802 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/items` | `GET` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:800 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/push` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/s3ingest/run` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:847 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/save` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:834 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/schedule` | `GET` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/s3ingest/schedule/save` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/s3ingest/status-by-target` | `GET` | `session_middleware` | frontend/src/pages/My_FileBrowser.jsx:372 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/dataset/profile` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/dataset/sample` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/engineer-knowledge` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/engineer-knowledge` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge` | `GET` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:1145, frontend/src/components/agent/AgentLegacy.jsx:1151 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/knowledge/document` | `POST` | `require_admin` | - | - | `ok` |
| `/api/semiconductor/knowledge/import` | `POST` | `require_admin` | - | - | `ok` |
| `/api/semiconductor/knowledge/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge/table/commit` | `POST` | `is_page_admin, current_user` | frontend/src/components/agent/AgentLegacy.jsx:1151 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/knowledge/table/preview` | `POST` | `current_user` | frontend/src/components/agent/AgentLegacy.jsx:1145 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/knowledge/update-prompt` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/reformatter/apply` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/semiconductor/reformatter/propose` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/source-profiles` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/teg/apply` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/semiconductor/teg/propose` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/use-cases` | `GET` | `current_user` | - | - | `ok` |
| `/api/session/load` | `GET` | `owner_or_admin` | frontend/src/app/useFlowShell.js:239, frontend/src/pages/My_DevGuide.jsx:185 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/session/save` | `POST` | `owner_or_admin` | frontend/src/app/useFlowShell.js:306, frontend/src/pages/My_DevGuide.jsx:184 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/column-values` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/customs` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2545, frontend/src/pages/My_Inform.jsx:4770 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/customs/delete` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/customs/save` | `POST` | `require_page_manager:splittable, current_user` | frontend/src/pages/My_Inform.jsx:4770 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/download-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/download-xlsx` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/fab-roots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/features` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/history` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2700 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/history-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/history/final` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/infer-step-mapping` | `POST` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/splittable/inline-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:616 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/knob-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:614 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/long-items` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/long-wide-preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/lot-candidates` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:17, frontend/src/pages/My_Inform.jsx:2613, frontend/src/pages/My_Inform.jsx:2633 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/lot-ids` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/match-cache/refresh` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/match-cache/status` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/ml-table-match` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/notes` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2232 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/notes/comment` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/notes/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/notes/save` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/operational-history` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/override-debug` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/override-link-preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/paste-sets` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2732, frontend/src/pages/My_Inform.jsx:2740, frontend/src/pages/My_Inform.jsx:2767 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/paste-sets/delete` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/paste-sets/save` | `POST` | `require_page_manager:splittable, current_user` | frontend/src/pages/My_Inform.jsx:2767 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/paste-sets/to-custom` | `POST` | `require_page_manager:splittable, current_user` | - | - | `ok` |
| `/api/splittable/plan` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/plan/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/plans-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/precision` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/precision/save` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/prefixes` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/prefixes/save` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/products` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/rulebook` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/rulebook/save` | `POST` | `require_page_manager:splittable, current_user` | - | - | `ok` |
| `/api/splittable/rulebook/schema` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/rulebook/schema/save` | `POST` | `require_page_manager:splittable, current_user` | - | - | `ok` |
| `/api/splittable/schema` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2553 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/source-config` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/source-config/save` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/uniques` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/view` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:2523 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/vm-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:615 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/system/stats` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/tracker` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1755, frontend/src/pages/My_Admin.jsx:1756, frontend/src/pages/My_Admin.jsx:1759, frontend/src/pages/My_DevGuide.jsx:174, frontend/src/pages/My_DevGuide.jsx:175, frontend/src/pages/My_DevGuide.jsx:176, +8 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1755, frontend/src/pages/My_Admin.jsx:1756, frontend/src/pages/My_Admin.jsx:1759, frontend/src/pages/My_DevGuide.jsx:174, frontend/src/pages/My_DevGuide.jsx:175, frontend/src/pages/My_DevGuide.jsx:176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories/save` | `POST` | `require_page_manager:tracker` | frontend/src/pages/My_Admin.jsx:1759, frontend/src/pages/My_DevGuide.jsx:176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories/usage` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1756, frontend/src/pages/My_DevGuide.jsx:175 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/comment` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/comment/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/comment/reply` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/create` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/db-sources` | `GET` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/tracker/db-sources/save` | `POST` | `require_page_manager:tracker` | - | - | `ok` |
| `/api/tracker/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/et-lot-cache/refresh` | `POST` | `require_page_manager:tracker` | - | - | `ok` |
| `/api/tracker/et-lot-cache/status` | `GET` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/tracker/image` | `GET` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:59, frontend/src/pages/My_Meeting.jsx:60, frontend/src/pages/My_Tracker.jsx:299 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:425, frontend/src/pages/My_Meeting.jsx:538, frontend/src/pages/My_Meeting.jsx:552, frontend/src/pages/My_Tracker.jsx:1390 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue-mail` | `GET` | `current_user` | frontend/src/pages/My_Tracker.jsx:1390, frontend/src/pages/My_Tracker.jsx:1390 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue-mail` | `POST` | `current_user` | frontend/src/pages/My_Tracker.jsx:1390, frontend/src/pages/My_Tracker.jsx:1390 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issues` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:538 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/lot-candidates` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/lot-check` | `POST` | `require_page_manager:tracker` | - | - | `ok` |
| `/api/tracker/lot-check-all` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/lot-step` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/lot-summary` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/lot-watch` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/lots/bulk` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/mail-template-preview` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/products` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/scheduler` | `GET` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/tracker/scheduler/run-now` | `POST` | `require_page_manager:tracker` | - | - | `ok` |
| `/api/tracker/scheduler/save` | `POST` | `require_page_manager:tracker` | - | - | `ok` |
| `/api/tracker/settings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/tracker/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/waferlayout/edge-shots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/waferlayout/grid` | `GET` | `session_middleware` | frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:792, frontend/src/pages/My_WaferLayout.jsx:792 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/waferlayout/grid` | `PUT` | `require_admin` | frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:792, frontend/src/pages/My_WaferLayout.jsx:792 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/waferlayout/tech-list` | `GET` | `session_middleware` | frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:892, frontend/src/pages/My_WaferLayout.jsx:892 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/waferlayout/tech-list` | `PUT` | `require_admin` | frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:892, frontend/src/pages/My_WaferLayout.jsx:892 | admin/page helper or inline role guard where rendered | `ok` |

## Risk Counts

- ok: 537
- leak_be_open: 0
- leak_fe_open: 0
- inconsistent: 0

## Change Notes

- `/api/dashboard/chart-defaults`, dashboard refresh and saved-chart mutations accept global admin or `dashboard` page-manager delegation.
- Inform module/config/catalog/contact writes and SplitTable shared config/rule/set writes accept global admin or page-manager delegation.
- S3 ingest/AWS credential endpoints no longer trust body/query `username`; they require `filebrowser` page-manager delegation.
- `/api/informs/{id}/send-mail` now requires the inform author or global admin.
- Home Flowi blocks regular users from admin-function prompts with `blocked=true` and `reject_reason`.
- Legacy `/api/admin/*` self-service notification/settings routes remain owner/self guarded to avoid breaking normal user flows; admin management routes remain `require_admin`.

## 갱신 절차

1. 새 backend endpoint를 추가하면 이 표에 `endpoint`, `method`, `backend gate`, FE caller를 추가한다.
2. admin 전용 write는 `require_admin`, 페이지 위임 write는 `require_page_manager("page_key")` 또는 동일한 `is_page_manager` 검사를 붙인다.
3. FE에서 admin/page-admin UI를 추가하면 `frontend/src/lib/permissions.js` 헬퍼를 우선 사용한다.
4. CI 또는 로컬에서 `python3 scripts/check_permission_matrix.py`를 실행해 라우터와 표 누락을 확인한다.
