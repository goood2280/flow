# Permission Matrix

Generated from `backend/routers/*.py` and `frontend/src` API call sites.
`admin_settings.json` is intentionally not embedded in this report.

| endpoint | method | backend gate | FE caller(file:line) | FE gate | risk |
|---|---:|---|---|---|---|
| `/api/admin/activity/features` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:1021 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/activity/summary` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:1020 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/all-notifications` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:230, frontend/src/pages/My_DevGuide.jsx:160 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/approve` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:400, frontend/src/pages/My_DevGuide.jsx:154 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/restore` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:956, frontend/src/pages/My_Admin.jsx:1604 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/run` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:951, frontend/src/pages/My_Admin.jsx:1595 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/schedule` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:963, frontend/src/pages/My_Admin.jsx:966 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/status` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:933, frontend/src/pages/My_Admin.jsx:1579 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/base-csv` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:2683, frontend/src/pages/My_Admin.jsx:2683, frontend/src/pages/My_Admin.jsx:2700, frontend/src/pages/My_Admin.jsx:2700 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/base-csv` | `PUT` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:2683, frontend/src/pages/My_Admin.jsx:2683, frontend/src/pages/My_Admin.jsx:2700, frontend/src/pages/My_Admin.jsx:2700 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/broadcast` | `POST` | `require_admin` | frontend/src/pages/My_DevGuide.jsx:158 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/bulk-users` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:303 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/delete-user` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:405 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/dismiss` | `POST` | `owner_or_admin, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/dismiss-batch` | `POST` | `owner_or_admin, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/download-history` | `GET` | `require_admin` | - | - | `ok` |
| `/api/admin/llm/presets` | `GET` | `require_admin` | frontend/src/components/agent/LlmCfgPanel.jsx:85 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/log` | `POST` | `current_user, legacy_self_service_exception` | frontend/src/lib/api.js:163, frontend/src/pages/My_Admin.jsx:234, frontend/src/pages/My_Admin.jsx:237, frontend/src/pages/My_Admin.jsx:247, frontend/src/pages/My_DevGuide.jsx:162, frontend/src/pages/My_DevGuide.jsx:163 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/logs` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:234, frontend/src/pages/My_Admin.jsx:237, frontend/src/pages/My_Admin.jsx:247, frontend/src/pages/My_DevGuide.jsx:163 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/logs/users` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:234 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/mark-read` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/App.jsx:449, frontend/src/pages/My_Admin.jsx:312, frontend/src/pages/My_DevGuide.jsx:161 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/mark-read-batch` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/App.jsx:449, frontend/src/pages/My_Admin.jsx:312 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/my-notifications` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:313, frontend/src/pages/My_DevGuide.jsx:159 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/my-page-admin` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:253 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/notify-rules` | `GET` | `current_user, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/notify-rules` | `POST` | `current_user, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/page-admins` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:802, frontend/src/pages/My_Admin.jsx:802, frontend/src/pages/My_Admin.jsx:822, frontend/src/pages/My_Admin.jsx:822, frontend/src/pages/My_Admin.jsx:841, frontend/src/pages/My_Admin.jsx:841, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/page-admins` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:802, frontend/src/pages/My_Admin.jsx:802, frontend/src/pages/My_Admin.jsx:822, frontend/src/pages/My_Admin.jsx:822, frontend/src/pages/My_Admin.jsx:841, frontend/src/pages/My_Admin.jsx:841, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/qa/report` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:262 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/qa/trigger` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:595 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/reject` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:401, frontend/src/pages/My_DevGuide.jsx:155 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/reset-password` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:289, frontend/src/pages/My_DevGuide.jsx:156 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/send-inquiry` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:226 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/send-message` | `POST` | `require_admin` | frontend/src/pages/My_DevGuide.jsx:157 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/set-email` | `POST` | `require_admin` | - | - | `ok` |
| `/api/admin/set-name` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:391 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/set-tabs` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:297 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/settings` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:301, frontend/src/pages/My_Admin.jsx:822, frontend/src/pages/My_Admin.jsx:867, frontend/src/pages/My_Admin.jsx:942, frontend/src/pages/My_Admin.jsx:1138, frontend/src/pages/My_Admin.jsx:1146, +12 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/settings/save` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:867, frontend/src/pages/My_Admin.jsx:942, frontend/src/pages/My_Admin.jsx:1146, frontend/src/pages/My_Admin.jsx:1465, frontend/src/pages/My_Admin.jsx:1587, frontend/src/pages/My_Admin.jsx:1616, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/tracker-schema-migrate` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:1764 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/user-tabs` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:249 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/users` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:232, frontend/src/pages/My_DevGuide.jsx:153 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/status` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/analytics/correlation` | `POST` | `current_user` | - | - | `ok` |
| `/api/analytics/trend` | `POST` | `current_user` | - | - | `ok` |
| `/api/auth/change-password` | `POST` | `current_user` | frontend/src/App.jsx:536 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/forgot-password` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/pages/My_Login.jsx:162 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/login` | `POST` | `session_middleware` | frontend/src/main.jsx:15, frontend/src/pages/My_DevGuide.jsx:148, frontend/src/pages/My_Login.jsx:150 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/logout` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/app/useFlowShell.js:160 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/me` | `GET` | `session_middleware` | frontend/src/app/useFlowShell.js:204 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/register` | `POST` | `session_middleware` | frontend/src/main.jsx:15, frontend/src/pages/My_DevGuide.jsx:149, frontend/src/pages/My_Login.jsx:156 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/reset-request` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/pages/My_DevGuide.jsx:150 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/set-name` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/categories` | `GET` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:450, frontend/src/pages/My_Meeting.jsx:1814 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/calendar/categories/save` | `POST` | `require_page_manager:calendar` | frontend/src/pages/My_Meeting.jsx:1814 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/catalog/matching/download` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1863 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/list` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1847 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/preview` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1858 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/save` | `POST` | `require_page_manager:splittable` | frontend/src/pages/My_Admin.jsx:1875 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/schema` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/catalog/product/list` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1955 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/product/load` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1964 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/product/save` | `POST` | `require_page_manager:tablemap` | frontend/src/pages/My_Admin.jsx:1965 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/artifacts` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2000 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/config` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1999, frontend/src/pages/My_Admin.jsx:2010 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/config/save` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_Admin.jsx:2010 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/status` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2001 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/sync` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_Admin.jsx:2011 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/filebrowser/base-files` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/cache/cleanup` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/cleanup-candidates` | `GET` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/llm/refresh` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/match/refresh` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/match/settings` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/cache/match/status` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/columns/search` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/domain` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/download-csv` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/download-history` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:259 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/ml-table/lookup` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/ml-table/lookup` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/ml-table/lookup-status` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/parquet-meta` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/parquet-meta/invalidate` | `POST` | `current_user` | - | - | `ok` |
| `/api/filebrowser/products` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/root-parquet-view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/root-parquets` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/roots` | `GET` | `session_middleware` | frontend/src/pages/My_DevGuide.jsx:166 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/schema/snapshot` | `POST` | `current_user` | - | - | `ok` |
| `/api/filebrowser/schema/snapshots` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/scopes` | `GET` | `session_middleware` | frontend/src/pages/My_FileBrowser.jsx:368 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/scopes/roots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/settings` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/settings` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/settings/llm/draft` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/filebrowser/sql-guide` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/sql/feedback` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/sql/llm/draft` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/groups/audit` | `GET` | `require_admin` | - | - | `ok` |
| `/api/groups/create` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2361 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/delete` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2366 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/eligible-users` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2355 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/list` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2354, frontend/src/pages/My_Calendar.jsx:91, frontend/src/pages/My_Dashboard.jsx:1726, frontend/src/pages/My_Inform.jsx:2320, frontend/src/pages/My_Meeting.jsx:419, frontend/src/pages/My_Meeting.jsx:452, +1 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/lots/add` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2375 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/lots/remove` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2378 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/members/add` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2368 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/members/remove` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2371 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/mine` | `GET` | `current_user` | - | - | `ok` |
| `/api/groups/modules/set` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2381 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/my-modules` | `GET` | `current_user` | - | - | `ok` |
| `/api/groups/update` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2384, frontend/src/pages/My_Admin.jsx:2506, frontend/src/pages/My_Admin.jsx:2514 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/home/release-notes` | `GET` | `current_user` | - | - | `ok` |
| `/api/home/summary` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2563, frontend/src/pages/My_Admin.jsx:2563, +52 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2563, frontend/src/pages/My_Admin.jsx:2563, +52 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/audit-log` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/bulk-create` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:23 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/by-lot` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/by-product` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/check` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/config` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2563, frontend/src/pages/My_Admin.jsx:2563, frontend/src/pages/My_Inform.jsx:17, frontend/src/pages/My_Inform.jsx:17, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/config` | `POST` | `require_page_manager:inform` | frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2563, frontend/src/pages/My_Admin.jsx:2563, frontend/src/pages/My_Inform.jsx:17, frontend/src/pages/My_Inform.jsx:17, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/dashboard-data` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/deadline` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/delete` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/informs/edit` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/informs/eligible-contacts` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:2318, frontend/src/pages/My_Inform.jsx:2319 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/files/{uid}/{name}` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/lot-matrix` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/lots` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/mail-groups` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:21 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2586 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/knob-map` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2586, frontend/src/pages/My_Admin.jsx:2586 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/knob-map` | `POST` | `require_page_manager:inform, is_page_manager, current_user` | frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2586, frontend/src/pages/My_Admin.jsx:2586 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/recipients` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/modules/summary` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/my` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/my-modules` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/product-contacts` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2287, frontend/src/pages/My_Inform.jsx:2287, frontend/src/pages/My_Inform.jsx:2298, frontend/src/pages/My_Inform.jsx:2298, frontend/src/pages/My_Inform.jsx:2299, frontend/src/pages/My_Inform.jsx:2299, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2287, frontend/src/pages/My_Inform.jsx:2287, frontend/src/pages/My_Inform.jsx:2298, frontend/src/pages/My_Inform.jsx:2298, frontend/src/pages/My_Inform.jsx:2299, frontend/src/pages/My_Inform.jsx:2299, +4 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/bulk-add` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2326 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/delete` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2309 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/update` | `POST` | `require_page_manager:inform, current_user` | frontend/src/pages/My_Inform.jsx:2298 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/informs/recipients` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:20, frontend/src/pages/My_Meeting.jsx:453 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/settings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/settings` | `POST` | `require_page_manager:inform` | - | - | `ok` |
| `/api/informs/sidebar` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/splittable` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:19, frontend/src/pages/My_Inform.jsx:2421, frontend/src/pages/My_Inform.jsx:2657, frontend/src/pages/My_Inform.jsx:4647 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/splittable-sets` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/splittable-snapshot` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:19, frontend/src/pages/My_Inform.jsx:2421, frontend/src/pages/My_Inform.jsx:2657, frontend/src/pages/My_Inform.jsx:4647 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/status` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/upload` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1113, frontend/src/pages/My_Inform.jsx:2563, frontend/src/pages/My_Inform.jsx:2586, frontend/src/pages/My_SplitTable.jsx:575 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/upload-attachment` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/user-modules` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/user-modules/clear` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/user-modules/save` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/wafers` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}` | `DELETE` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2547, frontend/src/pages/My_Admin.jsx:2548, frontend/src/pages/My_Admin.jsx:2563, frontend/src/pages/My_Admin.jsx:2586, frontend/src/pages/My_Inform.jsx:17, frontend/src/pages/My_Inform.jsx:19, +20 more | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/llm/flowi/admin/update` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:1157 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/agent/chat` | `POST` | `current_user` | frontend/src/pages/My_Dashboard.jsx:2399 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/chart-session/raw-data.csv` | `GET` | `current_user` | frontend/src/pages/My_Home.jsx:440 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/chat` | `POST` | `current_user` | frontend/src/components/FlowiPromptBox.jsx:264, frontend/src/pages/My_Home.jsx:181 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/edm/execute` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:997 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/edm/propose` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:184 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:1134, frontend/src/pages/My_Admin.jsx:1178, frontend/src/pages/My_Home.jsx:1315 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback/promote` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:1178 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback/summary` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:1134 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/function-call/preview` | `POST` | `require_admin` | - | - | `ok` |
| `/api/llm/flowi/inform/confirm` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/confirm` | `POST` | `require_admin, current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/resolve` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/start` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/orchestrator/preview` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/persona` | `GET` | `require_admin` | - | - | `ok` |
| `/api/llm/flowi/persona` | `POST` | `require_admin, current_user` | - | - | `ok` |
| `/api/llm/flowi/persona-card` | `GET` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/profile` | `GET` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/profile` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/verify` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:116 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/status` | `GET` | `current_user` | frontend/src/pages/My_Dashboard.jsx:2389, frontend/src/pages/My_Home.jsx:101, frontend/src/components/agent/LlmCfgPanel.jsx:58, frontend/src/components/agent/LlmTab.jsx:14 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/test` | `POST` | `require_admin` | frontend/src/components/agent/LlmCfgPanel.jsx:149 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/create` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1596, frontend/src/pages/My_Meeting.jsx:2010 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/delete` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1616, frontend/src/pages/My_Meeting.jsx:2019 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/list` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:1397, frontend/src/pages/My_Inform.jsx:1450, frontend/src/pages/My_Inform.jsx:4598, frontend/src/pages/My_Meeting.jsx:418, frontend/src/pages/My_Tracker.jsx:1297 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/members/add` | `POST` | `current_user` | - | - | `ok` |
| `/api/mail-groups/members/remove` | `POST` | `current_user` | - | - | `ok` |
| `/api/mail-groups/update` | `POST` | `current_user` | frontend/src/pages/My_Meeting.jsx:2010 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/match/area-rollup` | `GET` | `session_middleware` | frontend/src/constants/processAreas.js:9, frontend/src/pages/My_Admin.jsx:1860, frontend/src/pages/My_Admin.jsx:1908 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/match/areas` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/meetings/action/push` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/action/unpush` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/add` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/image` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:78, frontend/src/pages/My_Meeting.jsx:79 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/agenda/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/ask` | `POST` | `current_user` | - | - | `ok` |
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
| `/api/meetings/session/mail-preview` | `POST` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:2113 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/session/send-mail` | `POST` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:2120 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/session/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/stream` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:468 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/{mid}` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:78, frontend/src/pages/My_Meeting.jsx:79, frontend/src/pages/My_Meeting.jsx:468, frontend/src/pages/My_Meeting.jsx:2113, frontend/src/pages/My_Meeting.jsx:2120 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/mark_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Admin.jsx:2085, frontend/src/pages/My_Home.jsx:1586 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notice_create` | `POST` | `owner_or_admin` | frontend/src/App.jsx:272, frontend/src/pages/My_Admin.jsx:2151, frontend/src/pages/My_Home.jsx:1652 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notice_delete` | `POST` | `owner_or_admin` | frontend/src/App.jsx:278, frontend/src/pages/My_Admin.jsx:2155, frontend/src/pages/My_Home.jsx:1657 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notices` | `GET` | `owner_or_admin` | frontend/src/pages/My_Admin.jsx:2148, frontend/src/pages/My_Home.jsx:1647 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/reply` | `POST` | `owner_or_admin` | frontend/src/App.jsx:266, frontend/src/pages/My_Admin.jsx:2087, frontend/src/pages/My_Home.jsx:1588 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/thread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:235, frontend/src/App.jsx:240, frontend/src/pages/My_Admin.jsx:2081, frontend/src/pages/My_Admin.jsx:2082, frontend/src/pages/My_Home.jsx:1582, frontend/src/pages/My_Home.jsx:1583 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/threads` | `GET` | `owner_or_admin` | frontend/src/App.jsx:235, frontend/src/pages/My_Admin.jsx:2081, frontend/src/pages/My_Home.jsx:1582 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/unread` | `GET` | `owner_or_admin` | - | - | `ok` |
| `/api/messages/mark_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Home.jsx:1500 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/notice_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Home.jsx:1514 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/notices` | `GET` | `session_middleware` | frontend/src/App.jsx:232, frontend/src/App.jsx:396, frontend/src/pages/My_Home.jsx:1502 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/send` | `POST` | `owner_or_admin` | frontend/src/App.jsx:260, frontend/src/pages/My_Home.jsx:1510 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/thread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:228, frontend/src/pages/My_Home.jsx:1499 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/monitor/farm-status` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:265 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/heartbeat` | `POST` | `session_middleware` | frontend/src/pages/My_DevGuide.jsx:181, frontend/src/pages/My_DevGuide.jsx:447 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/history` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/monitor/load/start` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:269 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/load/stop` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:280 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/resource-log` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:264 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/state` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/monitor/system` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:263, frontend/src/pages/My_DevGuide.jsx:180 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/s3ingest/available` | `GET` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:854 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config` | `GET` | `require_admin` | frontend/src/components/AwsPanel.jsx:21, frontend/src/components/AwsPanel.jsx:44, frontend/src/components/AwsPanel.jsx:61, frontend/src/pages/My_Admin.jsx:2199, frontend/src/pages/My_Admin.jsx:2221, frontend/src/pages/My_Admin.jsx:2238, +1 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config/delete` | `POST` | `require_admin` | frontend/src/components/AwsPanel.jsx:61, frontend/src/pages/My_Admin.jsx:2238 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config/save` | `POST` | `require_admin` | frontend/src/components/AwsPanel.jsx:44, frontend/src/pages/My_Admin.jsx:2221 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/delete` | `POST` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:896 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/health` | `GET` | `session_middleware` | frontend/src/components/S3StatusLight.jsx:22 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/history` | `GET` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:855 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/items` | `GET` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:853 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/push` | `POST` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/s3ingest/run` | `POST` | `require_page_manager:filebrowser` | frontend/src/pages/My_FileBrowser.jsx:903 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/save` | `POST` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:888 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/schedule` | `GET` | `require_page_manager:filebrowser` | - | - | `ok` |
| `/api/s3ingest/schedule/save` | `POST` | `require_admin` | - | - | `ok` |
| `/api/s3ingest/status-by-target` | `GET` | `session_middleware` | frontend/src/pages/My_FileBrowser.jsx:404 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/dataset/profile` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/dataset/sample` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/engineer-knowledge` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/engineer-knowledge` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge/document` | `POST` | `require_admin` | - | - | `ok` |
| `/api/semiconductor/knowledge/import` | `POST` | `require_admin` | - | - | `ok` |
| `/api/semiconductor/knowledge/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge/table/commit` | `POST` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge/table/preview` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge/update-prompt` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/reformatter/apply` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/semiconductor/reformatter/propose` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/source-profiles` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/teg/apply` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/semiconductor/teg/propose` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/use-cases` | `GET` | `current_user` | - | - | `ok` |
| `/api/session/load` | `GET` | `owner_or_admin` | frontend/src/app/useFlowShell.js:241, frontend/src/pages/My_DevGuide.jsx:185 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/session/save` | `POST` | `owner_or_admin` | frontend/src/app/useFlowShell.js:308, frontend/src/pages/My_DevGuide.jsx:184 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/column-values` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/custom-tags` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/custom-tags/columns/delete` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/custom-tags/columns/save` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/custom-tags/delete` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/custom-tags/values` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/customs` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2616, frontend/src/pages/My_Inform.jsx:4748 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/customs/delete` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/customs/save` | `POST` | `require_page_manager:splittable, current_user` | frontend/src/pages/My_Inform.jsx:4748 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/download-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/download-xlsx` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/fab-roots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/features` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/history` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2771 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/history-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/history/final` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/infer-step-mapping` | `POST` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/splittable/inline-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:689 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/knob-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:687 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/long-items` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/long-wide-preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/lot-candidates` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:18, frontend/src/pages/My_Inform.jsx:2684, frontend/src/pages/My_Inform.jsx:2704 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/lot-ids` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/management-rows` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/management-rows/columns/save` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/management-rows/values` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/match-cache/refresh` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/match-cache/status` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/ml-table-match` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/notes` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2277 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/notes/comment` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/notes/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/notes/save` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/operational-history` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/override-debug` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/override-link-preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/paste-sets` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2803, frontend/src/pages/My_Inform.jsx:2811, frontend/src/pages/My_Inform.jsx:2838 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/paste-sets/delete` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/paste-sets/save` | `POST` | `require_page_manager:splittable, current_user` | frontend/src/pages/My_Inform.jsx:2838 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/splittable/schema` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2624 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/source-config` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/source-config/save` | `POST` | `require_page_manager:splittable` | - | - | `ok` |
| `/api/splittable/uniques` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/view` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:2594 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/vm-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:688 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/system/stats` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/tracker` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1757, frontend/src/pages/My_Admin.jsx:1758, frontend/src/pages/My_Admin.jsx:1761, frontend/src/pages/My_DevGuide.jsx:174, frontend/src/pages/My_DevGuide.jsx:175, frontend/src/pages/My_DevGuide.jsx:176, +8 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1757, frontend/src/pages/My_Admin.jsx:1758, frontend/src/pages/My_Admin.jsx:1761, frontend/src/pages/My_DevGuide.jsx:174, frontend/src/pages/My_DevGuide.jsx:175, frontend/src/pages/My_DevGuide.jsx:176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories/save` | `POST` | `require_page_manager:tracker` | frontend/src/pages/My_Admin.jsx:1761, frontend/src/pages/My_DevGuide.jsx:176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories/usage` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1758, frontend/src/pages/My_DevGuide.jsx:175 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/comment` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/comment/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/comment/reply` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/create` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/db-sources` | `GET` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/tracker/db-sources/save` | `POST` | `require_page_manager:tracker` | - | - | `ok` |
| `/api/tracker/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/et-lot-cache/refresh` | `POST` | `require_page_manager:tracker` | - | - | `ok` |
| `/api/tracker/et-lot-cache/status` | `GET` | `is_page_manager, current_user` | - | - | `ok` |
| `/api/tracker/image` | `GET` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:60, frontend/src/pages/My_Meeting.jsx:61, frontend/src/pages/My_Tracker.jsx:300 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:524, frontend/src/pages/My_Meeting.jsx:637, frontend/src/pages/My_Meeting.jsx:651, frontend/src/pages/My_Tracker.jsx:1391 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue-mail` | `GET` | `current_user` | frontend/src/pages/My_Tracker.jsx:1391, frontend/src/pages/My_Tracker.jsx:1391 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue-mail` | `POST` | `current_user` | frontend/src/pages/My_Tracker.jsx:1391, frontend/src/pages/My_Tracker.jsx:1391 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issues` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:637 | admin/page helper or inline role guard where rendered | `ok` |
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

## Risk Counts

- ok: 504
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
