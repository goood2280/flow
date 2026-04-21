"""v8.8.3 — VERSION.json + CHANGELOG.md bump.
Adds a v8.8.3 entry at top of VERSION.json changelog, updates top-level version.
Idempotent: if 8.8.3 already present, no-ops.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
vfile = ROOT / "VERSION.json"
cfile = ROOT / "CHANGELOG.md"

data = json.loads(vfile.read_text(encoding="utf-8"))

if data.get("version") == "8.8.3":
    print("VERSION.json already at 8.8.3")
else:
    data["version"] = "8.8.3"
    entry = {
        "version": "8.8.3",
        "date": "2026-04-21",
        "title": "자동백업 최대 5개 · PageGear 전 탭 40px 우하단 통일 · FileBrowser Base 단일파일 admin 삭제 · 인폼 댓글/이력 엔드포인트 · 회의 공개범위 FE picker(patcher) · SplitTable/회의동시편집 이월",
        "tag": "feat+fix",
        "changes": [
            "**자동백업 최대 5개 유지** — core/backup.py `_DEFAULT_KEEP=14→5`, `_MAX_KEEP=5` 상한 강제. run_backup / list_backups / start_scheduler 모두 공용 `_cleanup_backups()` 훅 사용 — 앱 기동 직후 1회 즉시 정리 + 매 백업마다 초과분 삭제. 디스크 보호.",
            "**PageGear 전 탭 40px · ⚙️ · 우하단 통일** — PageGear.jsx 를 FileBrowser S3 gear 와 100% 동일 (40px 원형, ⚙️ emoji, box-shadow). position prop 은 후방 호환을 위해 유지하되 inline/top-right 외엔 모두 bottom-right 로 정규화 — Tracker/Meeting/Calendar/Dashboard 의 `bottom-left` 호출도 한 방에 우하단으로 수렴.",
            "**FileBrowser Base 단일 파일 admin 삭제** — 사이드바 Base 파일 목록에 admin 전용 🗑 버튼. BE `/api/filebrowser/base-file/delete` 가 base_root + db_root(의미적 Base) 두 루트를 모두 탐색하도록 확장, 해당 host_root 의 `.trash/<ts>_<name>` 으로 archive (복구 가능). 화이트리스트(parquet/csv/json/md/txt), 숨김/경로탈출 방어.",
            "**인폼 댓글 + 수정 이력 전용 엔드포인트** — routers/informs_extra.py 분리 (informs.py 1480+라인 부담 경감). GET /api/informs/{id}/comments · POST 추가·수정·삭제 (작성자/admin 만 edit/delete) + GET /api/informs/{id}/history (status_history + edit_history 병합, 시간 역순). comments 스키마는 {id, author, at, text, edited_at?} · edit_history 엔트리마다 kind 구분.",
            "**회의 공개범위 FE picker (patcher 준비)** — v8.8.2 에서 BE(group_ids) 는 완료. FE 의 create + meta-edit 모달에 그룹 칩 multi-picker 를 주입하는 _patch_meeting_v883.py 패처 스크립트 포함. 적용: `python _patch_meeting_v883.py` (멱등).",
            "**이월 (v8.8.4 타겟)** — (a) SplitTable 오버라이드 hive DB 적용 버그, (b) SplitTable 정렬을 fab_lot_id 그룹 내 wafer_id numeric 순으로 고정, (c) SplitTable paste 세트 BE 공유 + CUSTOM 탭 연동, (d) SplitTable tag FE 드로어, (e) 회의록 WebSocket/SSE 동시편집. 4건은 splittable.py (1699라인) · My_SplitTable.jsx 대수술이 필요해 별도 배치로 분리."
        ]
    }
    data["changelog"].insert(0, entry)
    vfile.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("VERSION.json bumped to 8.8.3")

# CHANGELOG.md — prepend "## v8.8.3" block if not present.
md = cfile.read_text(encoding="utf-8") if cfile.exists() else "# CHANGELOG\n\n"
if "## v8.8.3" in md:
    print("CHANGELOG.md already contains v8.8.3")
else:
    block = """## v8.8.3 — 2026-04-21

자동백업 최대 5개 · PageGear 전 탭 40px 우하단 통일 · FileBrowser Base 단일파일 admin 삭제 · 인폼 댓글/이력 엔드포인트 · 회의 공개범위 FE picker(patcher) · SplitTable/회의 동시편집 이월.

- **자동백업 최대 5개 유지** — `core/backup.py` `_DEFAULT_KEEP=14→5`, `_MAX_KEEP=5` 상한. `_cleanup_backups()` 공용 훅 (기동 시 1회 + 매 백업마다 + list 조회 시).
- **PageGear 전 탭 통일** — 40px · ⚙️ · 우하단. `position` prop 정규화로 Tracker/Meeting/Calendar/Dashboard 의 `bottom-left` 호출도 자동 수렴.
- **FileBrowser Base 단일 파일 admin 삭제** — 🗑 버튼 + `/base-file/delete` 가 base_root + db_root fallback, `.trash/` archive, 화이트리스트 방어.
- **인폼 댓글/이력 엔드포인트** — `routers/informs_extra.py` 신규. `/api/informs/{id}/comments` (CRUD) + `/api/informs/{id}/history` (status_history + edit_history 병합 타임라인).
- **회의 공개범위 FE picker (patcher)** — `_patch_meeting_v883.py` 에 create/edit 모달 + state + payload 주입.
- **이월 (v8.8.4)** — SplitTable 오버라이드/정렬/paste-세트/태그 드로어 + 회의록 WebSocket 동시편집.

"""
    md2 = block + md
    cfile.write_text(md2, encoding="utf-8")
    print("CHANGELOG.md updated")
