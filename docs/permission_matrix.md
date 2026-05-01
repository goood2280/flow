# Permission Matrix

Generated from `backend/routers/*.py` and `frontend/src` API call sites.
`admin_settings.json` is intentionally not embedded in this report.

| endpoint | method | backend gate | FE caller(file:line) | FE gate | risk |
|---|---:|---|---|---|---|
| `/api/admin/activity/features` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:836 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/activity/summary` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:835 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/all-notifications` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:136, frontend/src/pages/My_DevGuide.jsx:160 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/approve` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:306, frontend/src/pages/My_DevGuide.jsx:154 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/restore` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:771, frontend/src/pages/My_Admin.jsx:1663 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/run` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:766, frontend/src/pages/My_Admin.jsx:1654 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/schedule` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:778, frontend/src/pages/My_Admin.jsx:781 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/backup/status` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:748, frontend/src/pages/My_Admin.jsx:1638 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/base-csv` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:2798, frontend/src/pages/My_Admin.jsx:2798, frontend/src/pages/My_Admin.jsx:2815, frontend/src/pages/My_Admin.jsx:2815 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/base-csv` | `PUT` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:2798, frontend/src/pages/My_Admin.jsx:2798, frontend/src/pages/My_Admin.jsx:2815, frontend/src/pages/My_Admin.jsx:2815 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/broadcast` | `POST` | `require_admin` | frontend/src/pages/My_DevGuide.jsx:158 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/bulk-users` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:214 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/delete-user` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:311 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/dismiss` | `POST` | `owner_or_admin, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/dismiss-batch` | `POST` | `owner_or_admin, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/download-history` | `GET` | `require_admin` | - | - | `ok` |
| `/api/admin/ettime/download-log` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:168 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/log` | `POST` | `current_user, legacy_self_service_exception` | frontend/src/lib/api.js:155, frontend/src/pages/My_Admin.jsx:140, frontend/src/pages/My_Admin.jsx:143, frontend/src/pages/My_Admin.jsx:153, frontend/src/pages/My_DevGuide.jsx:162, frontend/src/pages/My_DevGuide.jsx:163 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/logs` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:140, frontend/src/pages/My_Admin.jsx:143, frontend/src/pages/My_Admin.jsx:153, frontend/src/pages/My_DevGuide.jsx:163 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/logs/users` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:140 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/mark-read` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/App.jsx:387, frontend/src/pages/My_Admin.jsx:223, frontend/src/pages/My_DevGuide.jsx:161 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/mark-read-batch` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/App.jsx:387, frontend/src/pages/My_Admin.jsx:223 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/my-notifications` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:215, frontend/src/pages/My_DevGuide.jsx:159 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/my-page-admin` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:155 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/notify-rules` | `GET` | `current_user, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/notify-rules` | `POST` | `current_user, legacy_self_service_exception` | - | - | `ok` |
| `/api/admin/page-admins` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:646, frontend/src/pages/My_Admin.jsx:646, frontend/src/pages/My_Admin.jsx:662, frontend/src/pages/My_Admin.jsx:662, frontend/src/pages/My_Admin.jsx:680, frontend/src/pages/My_Admin.jsx:680 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/page-admins` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:646, frontend/src/pages/My_Admin.jsx:646, frontend/src/pages/My_Admin.jsx:662, frontend/src/pages/My_Admin.jsx:662, frontend/src/pages/My_Admin.jsx:680, frontend/src/pages/My_Admin.jsx:680 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/qa/report` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:174 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/qa/trigger` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:440 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/reject` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:307, frontend/src/pages/My_DevGuide.jsx:155 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/reset-password` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:201, frontend/src/pages/My_DevGuide.jsx:156 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/send-inquiry` | `POST` | `owner_or_admin, legacy_self_service_exception` | frontend/src/pages/My_Admin.jsx:132 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/send-message` | `POST` | `require_admin` | frontend/src/pages/My_DevGuide.jsx:157 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/set-email` | `POST` | `require_admin` | - | - | `ok` |
| `/api/admin/set-name` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:297 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/set-tabs` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:209 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/settings` | `GET` | `current_user, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:203, frontend/src/pages/My_Admin.jsx:662, frontend/src/pages/My_Admin.jsx:689, frontend/src/pages/My_Admin.jsx:757, frontend/src/pages/My_Admin.jsx:953, frontend/src/pages/My_Admin.jsx:961, +12 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/settings/save` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:689, frontend/src/pages/My_Admin.jsx:757, frontend/src/pages/My_Admin.jsx:961, frontend/src/pages/My_Admin.jsx:1277, frontend/src/pages/My_Admin.jsx:1455, frontend/src/pages/My_Admin.jsx:1646, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/tracker-schema-migrate` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Admin.jsx:1879 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/user-tabs` | `GET` | `owner_or_admin, legacy_self_service_exception` | frontend/src/app/useFlowShell.js:151 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/admin/users` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:138, frontend/src/pages/My_DevGuide.jsx:153 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/knowledge/ingest` | `POST` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:674 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/knowledge/list` | `GET` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:665, frontend/src/pages/My_Diagnosis.jsx:678 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/matching/apply` | `POST` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:572 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/matching/suggest` | `POST` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:566 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/rulebook/apply` | `POST` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:618 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/rulebook/suggest` | `POST` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:612 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/admin-tools/status` | `GET` | `require_admin` | - | - | `ok` |
| `/api/agent/item-rules` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:452 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/knowledge-inventory` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:329, frontend/src/pages/My_Diagnosis.jsx:340 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/knowledge-inventory/promote` | `POST` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:340 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/persona` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:171 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/prompt-preview` | `POST` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:248 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/recent-rag` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:411 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/agent/workflow` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:138 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/analytics/correlation` | `POST` | `current_user` | - | - | `ok` |
| `/api/analytics/trend` | `POST` | `current_user` | - | - | `ok` |
| `/api/auth/change-password` | `POST` | `current_user` | frontend/src/App.jsx:458 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/forgot-password` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/pages/My_Login.jsx:162 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/login` | `POST` | `session_middleware` | frontend/src/main.jsx:15, frontend/src/pages/My_DevGuide.jsx:148, frontend/src/pages/My_Login.jsx:150 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/logout` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/app/useFlowShell.js:99 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/me` | `GET` | `current_user` | - | - | `ok` |
| `/api/auth/register` | `POST` | `session_middleware` | frontend/src/main.jsx:15, frontend/src/pages/My_DevGuide.jsx:149, frontend/src/pages/My_Login.jsx:156 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/reset-request` | `POST` | `session_middleware` | frontend/src/main.jsx:16, frontend/src/pages/My_DevGuide.jsx:150 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/auth/set-name` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/categories` | `GET` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:165, frontend/src/pages/My_Meeting.jsx:1484 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/calendar/categories/save` | `POST` | `require_admin` | frontend/src/pages/My_Meeting.jsx:1484 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/calendar/event` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/status` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/calendar/event/{eid}` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/events` | `GET` | `current_user` | - | - | `ok` |
| `/api/calendar/events/search` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/meetings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/settings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/calendar/settings/save` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/catalog/matching/download` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1978 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/list` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1962 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/preview` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1973 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/save` | `POST` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1990 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/matching/schema` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/catalog/product/list` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2070, frontend/src/pages/My_WaferLayout.jsx:433 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/product/load` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2079 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/product/save` | `POST` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2080 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/artifacts` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2115 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/config` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2114, frontend/src/pages/My_Admin.jsx:2125 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/config/save` | `POST` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2125 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/status` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2116 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/catalog/s3/sync` | `POST` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2126 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/charts/spec` | `POST` | `current_user` | - | - | `ok` |
| `/api/dashboard/apply-default` | `POST` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/chart-defaults` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/chart-defaults` | `POST` | `require_admin, current_user` | - | - | `ok` |
| `/api/dashboard/chart-refine` | `POST` | `current_user` | - | - | `ok` |
| `/api/dashboard/charts` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/charts/copy` | `POST` | `require_admin` | - | - | `ok` |
| `/api/dashboard/charts/delete` | `POST` | `require_admin` | - | - | `ok` |
| `/api/dashboard/charts/save` | `POST` | `require_admin` | - | - | `ok` |
| `/api/dashboard/columns` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dashboard/data` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/fab-progress` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/items` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/layout` | `POST` | `current_user` | - | - | `ok` |
| `/api/dashboard/multi-db-chart` | `POST` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/dashboard/products` | `GET` | `dashboard_section` | - | - | `ok` |
| `/api/dashboard/refresh` | `POST` | `require_admin` | - | - | `ok` |
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
| `/api/dbmap/tables` | `GET` | `session_middleware` | frontend/src/pages/My_SplitTable.jsx:102, frontend/src/pages/My_TableMap.jsx:1062 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/dbmap/tables/delete` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/tables/import` | `POST` | `require_page_admin:tablemap` | frontend/src/pages/My_TableMap.jsx:1062 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/dbmap/tables/save` | `POST` | `require_page_admin:tablemap` | - | - | `ok` |
| `/api/dbmap/tables/{table_id}` | `GET` | `session_middleware` | frontend/src/pages/My_TableMap.jsx:1062 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/filebrowser/base-file-view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/base-file/delete` | `POST` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/filebrowser/base-files` | `GET` | `session_middleware` | frontend/src/pages/My_Diagnosis.jsx:666 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/domain` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/download-csv` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/download-history` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:165 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/parquet-meta` | `GET` | `current_user` | - | - | `ok` |
| `/api/filebrowser/parquet-meta/invalidate` | `POST` | `current_user` | - | - | `ok` |
| `/api/filebrowser/products` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/root-parquet-view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/root-parquets` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/roots` | `GET` | `session_middleware` | frontend/src/pages/My_DevGuide.jsx:166 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/scopes` | `GET` | `session_middleware` | frontend/src/pages/My_FileBrowser.jsx:29 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/filebrowser/sql-guide` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/filebrowser/view` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/groups/audit` | `GET` | `require_admin` | - | - | `ok` |
| `/api/groups/create` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2476 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/delete` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2481 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/eligible-users` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2470 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/list` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2469, frontend/src/pages/My_Calendar.jsx:86, frontend/src/pages/My_Dashboard.jsx:1480, frontend/src/pages/My_Inform.jsx:1771, frontend/src/pages/My_Meeting.jsx:134, frontend/src/pages/My_Meeting.jsx:167, +1 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/lots/add` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2490 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/lots/remove` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2493 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/members/add` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2483 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/members/remove` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2486 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/mine` | `GET` | `current_user` | - | - | `ok` |
| `/api/groups/modules/set` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2496 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/groups/my-modules` | `GET` | `current_user` | - | - | `ok` |
| `/api/groups/update` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2499, frontend/src/pages/My_Admin.jsx:2621, frontend/src/pages/My_Admin.jsx:2629 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/home/release-notes` | `GET` | `current_user` | - | - | `ok` |
| `/api/home/summary` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2678, frontend/src/pages/My_Admin.jsx:2678, +42 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2678, frontend/src/pages/My_Admin.jsx:2678, +42 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/by-lot` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/by-product` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/check` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/config` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2678, frontend/src/pages/My_Admin.jsx:2678, frontend/src/pages/My_Inform.jsx:2283, frontend/src/pages/My_Inform.jsx:2283, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/config` | `POST` | `require_page_admin:informs` | frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2662, frontend/src/pages/My_Admin.jsx:2678, frontend/src/pages/My_Admin.jsx:2678, frontend/src/pages/My_Inform.jsx:2283, frontend/src/pages/My_Inform.jsx:2283, +2 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/deadline` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/edit` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/eligible-contacts` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:1769, frontend/src/pages/My_Inform.jsx:1770 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/files/{uid}/{name}` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/lots` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/mail-groups` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/modules` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2701 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/knob-map` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2701, frontend/src/pages/My_Admin.jsx:2701 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/knob-map` | `POST` | `require_page_admin:informs, current_user` | frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2663, frontend/src/pages/My_Admin.jsx:2701, frontend/src/pages/My_Admin.jsx:2701 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/modules/recipients` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/my` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:1656 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/my-modules` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:1656 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:1730, frontend/src/pages/My_Inform.jsx:1730, frontend/src/pages/My_Inform.jsx:1741, frontend/src/pages/My_Inform.jsx:1741, frontend/src/pages/My_Inform.jsx:1742, frontend/src/pages/My_Inform.jsx:1742, +6 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1730, frontend/src/pages/My_Inform.jsx:1730, frontend/src/pages/My_Inform.jsx:1741, frontend/src/pages/My_Inform.jsx:1741, frontend/src/pages/My_Inform.jsx:1742, frontend/src/pages/My_Inform.jsx:1742, +6 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/bulk-add` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1777 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/delete` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1760, frontend/src/pages/My_Inform.jsx:2482 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-contacts/update` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1741 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/product-lots` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/product/add` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/product/add` | `PATCH` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/informs/product/add` | `POST` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/informs/product/add` | `PUT` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/informs/products` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/products` | `PATCH` | `session_middleware` | - | - | `ok` |
| `/api/informs/products` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/informs/products` | `PUT` | `session_middleware` | - | - | `ok` |
| `/api/informs/products/add` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/products/add` | `PATCH` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/informs/products/add` | `POST` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/informs/products/add` | `PUT` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/informs/products/dedup` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/products/delete` | `POST` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/informs/recent` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/recipients` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:168 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/settings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/informs/settings` | `POST` | `require_page_admin:informs` | - | - | `ok` |
| `/api/informs/sidebar` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/splittable` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1971 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/splittable-snapshot` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1971 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/status` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/upload` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:739, frontend/src/pages/My_Inform.jsx:1887, frontend/src/pages/My_Inform.jsx:1910 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/upload-attachment` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/user-modules` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:3435, frontend/src/pages/My_Inform.jsx:3450, frontend/src/pages/My_Inform.jsx:3458 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/user-modules/clear` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:3450 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/user-modules/save` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:3458 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/informs/wafers` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/comments` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/comments` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/comments/{cid}/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/comments/{cid}/edit` | `POST` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/history` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/mail-preview` | `GET` | `current_user` | - | - | `ok` |
| `/api/informs/{inform_id}/send-mail` | `POST` | `current_user` | - | - | `ok` |
| `/api/items/resolve` | `POST` | `current_user` | - | - | `ok` |
| `/api/items/search` | `GET` | `current_user` | - | - | `ok` |
| `/api/knowledge/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/knowledge/rca` | `GET` | `current_user` | - | - | `ok` |
| `/api/knowledge/rca/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/llm/chat` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/admin/update` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:972 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/agent/chat` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/chat` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:135 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:949, frontend/src/pages/My_Admin.jsx:993, frontend/src/pages/My_Home.jsx:711 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback/promote` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:993 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/feedback/summary` | `GET` | `require_admin` | frontend/src/pages/My_Admin.jsx:949 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/function-call/preview` | `POST` | `require_admin` | - | - | `ok` |
| `/api/llm/flowi/inform/confirm` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/confirm` | `POST` | `require_admin, current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/resolve` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/inform/walkthrough/start` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/persona` | `GET` | `require_admin` | frontend/src/pages/My_Diagnosis.jsx:172, frontend/src/pages/My_Diagnosis.jsx:172, frontend/src/pages/My_Home.jsx:78, frontend/src/pages/My_Home.jsx:78 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/persona` | `POST` | `require_admin, current_user` | frontend/src/pages/My_Diagnosis.jsx:172, frontend/src/pages/My_Diagnosis.jsx:172, frontend/src/pages/My_Home.jsx:78, frontend/src/pages/My_Home.jsx:78 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/persona-card` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:172, frontend/src/pages/My_Home.jsx:78 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/flowi/profile` | `GET` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/profile` | `POST` | `current_user` | - | - | `ok` |
| `/api/llm/flowi/verify` | `POST` | `current_user` | frontend/src/pages/My_Home.jsx:92 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/status` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:500, frontend/src/pages/My_Home.jsx:66 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/llm/test` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:1476 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/create` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1196, frontend/src/pages/My_Meeting.jsx:1677 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/delete` | `POST` | `current_user` | frontend/src/pages/My_Inform.jsx:1216, frontend/src/pages/My_Meeting.jsx:1686 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/list` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:989, frontend/src/pages/My_Inform.jsx:1047, frontend/src/pages/My_Meeting.jsx:133, frontend/src/pages/My_Tracker.jsx:950 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/mail-groups/members/add` | `POST` | `current_user` | - | - | `ok` |
| `/api/mail-groups/members/remove` | `POST` | `current_user` | - | - | `ok` |
| `/api/mail-groups/update` | `POST` | `current_user` | frontend/src/pages/My_Meeting.jsx:1677 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/match/area-rollup` | `GET` | `session_middleware` | frontend/src/constants/processAreas.js:9, frontend/src/pages/My_Admin.jsx:1975, frontend/src/pages/My_Admin.jsx:2023 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/match/areas` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/meetings/action/push` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/action/unpush` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/add` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/agenda/update` | `POST` | `current_user` | - | - | `ok` |
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
| `/api/meetings/session/send-mail` | `POST` | `current_user` | frontend/src/pages/My_Meeting.jsx:1772 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/session/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/stream` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:183 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/meetings/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/meetings/{mid}` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:183, frontend/src/pages/My_Meeting.jsx:1772 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/mark_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Admin.jsx:2200, frontend/src/pages/My_Home.jsx:971 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notice_create` | `POST` | `owner_or_admin` | frontend/src/App.jsx:210, frontend/src/pages/My_Admin.jsx:2266, frontend/src/pages/My_Home.jsx:1037 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notice_delete` | `POST` | `owner_or_admin` | frontend/src/App.jsx:216, frontend/src/pages/My_Admin.jsx:2270, frontend/src/pages/My_Home.jsx:1042 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/notices` | `GET` | `owner_or_admin` | frontend/src/pages/My_Admin.jsx:2263, frontend/src/pages/My_Home.jsx:1032 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/reply` | `POST` | `owner_or_admin` | frontend/src/App.jsx:204, frontend/src/pages/My_Admin.jsx:2202, frontend/src/pages/My_Home.jsx:973 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/thread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:173, frontend/src/App.jsx:178, frontend/src/pages/My_Admin.jsx:2196, frontend/src/pages/My_Admin.jsx:2197, frontend/src/pages/My_Home.jsx:967, frontend/src/pages/My_Home.jsx:968 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/threads` | `GET` | `owner_or_admin` | frontend/src/App.jsx:173, frontend/src/pages/My_Admin.jsx:2196, frontend/src/pages/My_Home.jsx:967 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/admin/unread` | `GET` | `owner_or_admin` | - | - | `ok` |
| `/api/messages/mark_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Home.jsx:885 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/notice_read` | `POST` | `owner_or_admin` | frontend/src/pages/My_Home.jsx:899 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/notices` | `GET` | `session_middleware` | frontend/src/App.jsx:170, frontend/src/App.jsx:334, frontend/src/pages/My_Home.jsx:887 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/send` | `POST` | `owner_or_admin` | frontend/src/App.jsx:198, frontend/src/pages/My_Home.jsx:895 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/thread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:166, frontend/src/pages/My_Home.jsx:884 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/messages/unread` | `GET` | `owner_or_admin` | frontend/src/App.jsx:161 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/monitor/farm-status` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:177 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/heartbeat` | `POST` | `session_middleware` | frontend/src/pages/My_DevGuide.jsx:181, frontend/src/pages/My_DevGuide.jsx:447 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/history` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/monitor/load/start` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:181 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/load/stop` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:192 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/resource-log` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/monitor/state` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/monitor/system` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:175, frontend/src/pages/My_DevGuide.jsx:180 | admin/page helper or inline role guard where rendered | `ok` |
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
| `/api/s3ingest/available` | `GET` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:127 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config` | `GET` | `require_admin` | frontend/src/components/AwsPanel.jsx:21, frontend/src/components/AwsPanel.jsx:44, frontend/src/components/AwsPanel.jsx:61, frontend/src/pages/My_Admin.jsx:2314, frontend/src/pages/My_Admin.jsx:2336, frontend/src/pages/My_Admin.jsx:2353, +1 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config/delete` | `POST` | `require_admin` | frontend/src/components/AwsPanel.jsx:61, frontend/src/pages/My_Admin.jsx:2353 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/aws-config/save` | `POST` | `require_admin` | frontend/src/components/AwsPanel.jsx:44, frontend/src/pages/My_Admin.jsx:2336 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/delete` | `POST` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:149 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/health` | `GET` | `session_middleware` | frontend/src/components/S3StatusLight.jsx:22 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/history` | `GET` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:128 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/items` | `GET` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:126 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/push` | `POST` | `require_admin` | - | - | `ok` |
| `/api/s3ingest/run` | `POST` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:153 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/save` | `POST` | `require_admin` | frontend/src/pages/My_FileBrowser.jsx:143 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/s3ingest/schedule` | `GET` | `require_admin` | - | - | `ok` |
| `/api/s3ingest/schedule/save` | `POST` | `require_admin` | - | - | `ok` |
| `/api/s3ingest/status-by-target` | `GET` | `session_middleware` | frontend/src/pages/My_FileBrowser.jsx:42 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/dataset/profile` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/dataset/sample` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/engineer-knowledge` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/engineer-knowledge` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge` | `GET` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:712, frontend/src/pages/My_Diagnosis.jsx:718 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/knowledge/document` | `POST` | `require_admin` | - | - | `ok` |
| `/api/semiconductor/knowledge/import` | `POST` | `require_admin` | - | - | `ok` |
| `/api/semiconductor/knowledge/rag-view` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/knowledge/table/commit` | `POST` | `is_page_admin, current_user` | frontend/src/pages/My_Diagnosis.jsx:718 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/knowledge/table/preview` | `POST` | `current_user` | frontend/src/pages/My_Diagnosis.jsx:712 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/semiconductor/knowledge/update-prompt` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/reformatter/apply` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/semiconductor/reformatter/propose` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/source-profiles` | `GET` | `current_user` | - | - | `ok` |
| `/api/semiconductor/teg/apply` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/semiconductor/teg/propose` | `POST` | `current_user` | - | - | `ok` |
| `/api/semiconductor/use-cases` | `GET` | `current_user` | - | - | `ok` |
| `/api/session/load` | `GET` | `owner_or_admin` | frontend/src/app/useFlowShell.js:143, frontend/src/pages/My_DevGuide.jsx:185 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/session/save` | `POST` | `owner_or_admin` | frontend/src/app/useFlowShell.js:210, frontend/src/pages/My_DevGuide.jsx:184 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/column-values` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/customs` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:1939, frontend/src/pages/My_Inform.jsx:2840, frontend/src/pages/My_Inform.jsx:2845 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/customs/delete` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/splittable/customs/save` | `POST` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2840 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/download-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/download-xlsx` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/fab-roots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/features` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/history` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2028 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/history-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/history/final` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/infer-step-mapping` | `POST` | `is_page_admin, current_user` | - | - | `ok` |
| `/api/splittable/inline-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:394 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/knob-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:392 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/long-items` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/long-wide-preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/lot-candidates` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2004, frontend/src/pages/My_Inform.jsx:2005 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/lot-ids` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/match-cache/refresh` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:1709 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/match-cache/status` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:1630 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/ml-table-match` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/notes` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:1720, frontend/src/pages/My_Inform.jsx:3533 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/notes/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/notes/save` | `POST` | `current_user` | - | - | `ok` |
| `/api/splittable/operational-history` | `GET` | `current_user` | - | - | `ok` |
| `/api/splittable/override-debug` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/override-link-preview` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/paste-sets` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2060, frontend/src/pages/My_Inform.jsx:2068, frontend/src/pages/My_Inform.jsx:2095 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/paste-sets/delete` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/splittable/paste-sets/save` | `POST` | `session_middleware` | frontend/src/pages/My_Inform.jsx:2095 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/paste-sets/to-custom` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/splittable/plan` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/splittable/plan/delete` | `POST` | `session_middleware` | - | - | `ok` |
| `/api/splittable/plans-csv` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/precision` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/precision/save` | `POST` | `require_page_admin:splittable` | - | - | `ok` |
| `/api/splittable/prefixes` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/prefixes/save` | `POST` | `require_page_admin:splittable` | - | - | `ok` |
| `/api/splittable/products` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/rulebook` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/rulebook/save` | `POST` | `require_page_admin:splittable, current_user` | - | - | `ok` |
| `/api/splittable/rulebook/schema` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/rulebook/schema/save` | `POST` | `require_page_admin:splittable, current_user` | - | - | `ok` |
| `/api/splittable/schema` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:1947 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/source-config` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/source-config/save` | `POST` | `require_page_admin:splittable` | - | - | `ok` |
| `/api/splittable/uniques` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/splittable/view` | `GET` | `current_user` | frontend/src/pages/My_Inform.jsx:1918 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/splittable/vm-meta` | `GET` | `session_middleware` | frontend/src/pages/My_Inform.jsx:393 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/system/stats` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/tracker` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1631, frontend/src/pages/My_Admin.jsx:1709, frontend/src/pages/My_Admin.jsx:1872, frontend/src/pages/My_Admin.jsx:1873, frontend/src/pages/My_Admin.jsx:1876, frontend/src/pages/My_DevGuide.jsx:174, +10 more | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1872, frontend/src/pages/My_Admin.jsx:1873, frontend/src/pages/My_Admin.jsx:1876, frontend/src/pages/My_DevGuide.jsx:174, frontend/src/pages/My_DevGuide.jsx:175, frontend/src/pages/My_DevGuide.jsx:176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories/save` | `POST` | `current_user` | frontend/src/pages/My_Admin.jsx:1876, frontend/src/pages/My_DevGuide.jsx:176 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/categories/usage` | `GET` | `session_middleware` | frontend/src/pages/My_Admin.jsx:1873, frontend/src/pages/My_DevGuide.jsx:175 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/comment` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/comment/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/comment/reply` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/create` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/db-sources` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/db-sources/save` | `POST` | `require_admin` | - | - | `ok` |
| `/api/tracker/delete` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/et-lot-cache/refresh` | `POST` | `require_admin` | frontend/src/pages/My_Admin.jsx:1709 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/et-lot-cache/status` | `GET` | `current_user` | frontend/src/pages/My_Admin.jsx:1631 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/image` | `GET` | `session_middleware` | frontend/src/pages/My_Meeting.jsx:50, frontend/src/pages/My_Meeting.jsx:51, frontend/src/pages/My_Tracker.jsx:179 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:239, frontend/src/pages/My_Meeting.jsx:352, frontend/src/pages/My_Meeting.jsx:366, frontend/src/pages/My_Tracker.jsx:1044 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue-mail` | `GET` | `current_user` | frontend/src/pages/My_Tracker.jsx:1044, frontend/src/pages/My_Tracker.jsx:1044 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issue-mail` | `POST` | `current_user` | frontend/src/pages/My_Tracker.jsx:1044, frontend/src/pages/My_Tracker.jsx:1044 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/issues` | `GET` | `current_user` | frontend/src/pages/My_Meeting.jsx:352 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/tracker/lot-candidates` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/lot-check` | `POST` | `require_admin` | - | - | `ok` |
| `/api/tracker/lot-check-all` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/lot-step` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/lot-watch` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/lots/bulk` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/mail-template-preview` | `POST` | `current_user` | - | - | `ok` |
| `/api/tracker/products` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/scheduler` | `GET` | `current_user` | - | - | `ok` |
| `/api/tracker/scheduler/run-now` | `POST` | `require_admin` | - | - | `ok` |
| `/api/tracker/scheduler/save` | `POST` | `require_admin` | - | - | `ok` |
| `/api/tracker/settings` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/tracker/update` | `POST` | `current_user` | - | - | `ok` |
| `/api/waferlayout/edge-shots` | `GET` | `session_middleware` | - | - | `ok` |
| `/api/waferlayout/grid` | `GET` | `session_middleware` | frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:792, frontend/src/pages/My_WaferLayout.jsx:792 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/waferlayout/grid` | `PUT` | `require_admin` | frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:419, frontend/src/pages/My_WaferLayout.jsx:792, frontend/src/pages/My_WaferLayout.jsx:792 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/waferlayout/tech-list` | `GET` | `session_middleware` | frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:892, frontend/src/pages/My_WaferLayout.jsx:892 | admin/page helper or inline role guard where rendered | `ok` |
| `/api/waferlayout/tech-list` | `PUT` | `require_admin` | frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:451, frontend/src/pages/My_WaferLayout.jsx:892, frontend/src/pages/My_WaferLayout.jsx:892 | admin/page helper or inline role guard where rendered | `ok` |

## Risk Counts

- ok: 475
- leak_be_open: 0
- leak_fe_open: 0
- inconsistent: 0

## Change Notes

- `/api/dashboard/chart-defaults`, dashboard refresh and saved-chart mutations are backend admin-gated.
- Informs module/config writes and SplitTable rule/config writes accept global admin or page-admin delegation.
- `/api/informs/{id}/send-mail` now requires the inform author or global admin.
- Home Flowi blocks regular users from admin-function prompts with `blocked=true` and `reject_reason`.
- Legacy `/api/admin/*` self-service notification/settings routes remain owner/self guarded to avoid breaking normal user flows; admin management routes remain `require_admin`.

## 갱신 절차

1. 새 backend endpoint를 추가하면 이 표에 `endpoint`, `method`, `backend gate`, FE caller를 추가한다.
2. admin 전용 write는 `require_admin`, 페이지 위임 write는 `require_page_admin("page_key")` 또는 동일한 `is_page_admin` 검사를 붙인다.
3. FE에서 admin/page-admin UI를 추가하면 `frontend/src/lib/permissions.js` 헬퍼를 우선 사용한다.
4. CI 또는 로컬에서 `python3 scripts/check_permission_matrix.py`를 실행해 라우터와 표 누락을 확인한다.
