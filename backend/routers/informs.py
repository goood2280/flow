"""routers/informs.py v8.7.0 — 모듈 인폼 시스템 (역할 뷰 + 체크 + flow 상태 + SplitTable 연동 + 이미지 첨부 + 설정형 모듈/사유 + SplitTable 자동기록).

스키마 ({data_root}/informs/informs.json):
  [{
    id, parent_id, wafer_id, lot_id, product,
    module, reason, text, author, created_at,
    checked, checked_by, checked_at,
    flow_status, status_history:[{status, actor, at, note}],
    splittable_change: {column, old_value, new_value, applied} | null
  }]

규약:
  - parent_id 가 null 이면 루트 인폼. 답글/재인폼은 parent_id 로 트리 구성.
  - 삭제는 **작성자 본인만** 가능 (다른 유저·admin 불가) — v8.7.0 정책 변경.
  - 체크·상태변경은 해당 인폼 module 을 담당하는 유저 또는 admin.
  - flow_status: received | reviewing | in_progress | completed (순서 강제는 안 함).
  - splittable_change 는 자유형 메타. FE 에서 plan 변경 요약 카드로 렌더.

엔드포인트:
  GET  /api/informs?wafer_id=...        — 특정 wafer 스레드
  GET  /api/informs/recent              — 최근 루트 (role 필터 적용)
  GET  /api/informs/wafers              — 인폼 있는 wafer 목록
  GET  /api/informs/by-lot?lot_id=...   — 해당 lot 의 모든 스레드 (root+전체뷰)
  GET  /api/informs/by-product?product= — 해당 product 인폼 목록
  GET  /api/informs/my                  — 내 모듈 범위 인폼 (담당자용)
  GET  /api/informs/products            — 인폼 기록된 product 목록
  GET  /api/informs/lots                — 인폼 기록된 lot 목록
  GET  /api/informs/modules             — 모듈 드롭다운 옵션 (constants)
  POST /api/informs                     — 생성
  POST /api/informs/delete?id=          — 삭제 (작성자 본인만)
  POST /api/informs/check?id=           — 체크 토글
  POST /api/informs/status?id=          — flow_status 변경
  POST /api/informs/splittable?id=      — SplitTable 변경요청 attach
"""
import datetime
import html as _html
import json as _json
import mimetypes
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user, require_admin
from core.audit import record as _audit
from routers.groups import user_modules

router = APIRouter(prefix="/api/informs", tags=["informs"])

INFORMS_DIR = PATHS.data_root / "informs"
INFORMS_DIR.mkdir(parents=True, exist_ok=True)
INFORMS_FILE = INFORMS_DIR / "informs.json"
CONFIG_FILE = INFORMS_DIR / "config.json"
UPLOADS_DIR = INFORMS_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Default 모듈·사유. config.json 에 저장된 값이 있으면 그것을 우선.
DEFAULT_MODULES = ["GATE", "STI", "PC", "MOL", "BEOL", "ET", "EDS", "S-D Epi", "Spacer", "Well", "기타"]
DEFAULT_REASONS = ["재측정", "장비 이상", "공정 OOS", "혐의 확인", "레시피 변경", "외관 결함", "기타"]
# v8.7.9: 플로우 단순화 — 접수(received) → 완료(completed) 2단계.
# 과거 reviewing/in_progress 가 들어온 경우는 호환을 위해 수용.
FLOW_STATUSES = ["received", "completed"]
FLOW_STATUSES_LEGACY = ["received", "reviewing", "in_progress", "completed"]
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB/이미지


def _load_config() -> dict:
    data = load_json(CONFIG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    mods = data.get("modules")
    reas = data.get("reasons")
    prods = data.get("products")
    raw_root = data.get("raw_db_root")
    # v8.8.17: 사유별 메일 템플릿. schema: { "<reason>": {"subject": "...", "body": "..."} }
    rt = data.get("reason_templates")
    if not isinstance(mods, list) or not mods:
        mods = list(DEFAULT_MODULES)
    if not isinstance(reas, list) or not reas:
        reas = list(DEFAULT_REASONS)
    if not isinstance(prods, list):
        prods = []
    if not isinstance(raw_root, str):
        raw_root = ""
    if not isinstance(rt, dict):
        rt = {}
    # sanitize — drop entries where subject/body not str.
    rt_clean = {}
    for k, v in rt.items():
        if isinstance(v, dict):
            s = str(v.get("subject", "") or "")
            b = str(v.get("body", "") or "")
            rt_clean[str(k)] = {"subject": s, "body": b}
    return {"modules": mods, "reasons": reas, "products": prods,
            "raw_db_root": raw_root, "reason_templates": rt_clean}


def _save_config(cfg: dict) -> None:
    save_json(CONFIG_FILE, cfg, indent=2)


# v8.8.13: 유저별 인폼 모듈 조회 권한. admin_settings.json 의 `inform_user_modules` 에 저장.
#   스키마: { username: [module, ...] }.
#   - admin 은 항상 전체(all_rounder) — 설정값과 무관.
#   - username 이 키에 없으면 기존 `/api/groups/my-modules` 동작 fallback.
#   - 빈 배열은 "아무 모듈도 조회 못함" 으로 해석.
_INFORM_USER_MODS_KEY = "inform_user_modules"


def _inform_user_mods_path():
    return PATHS.data_root / "admin_settings.json"


def _read_admin_settings() -> dict:
    p = _inform_user_mods_path()
    try:
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                d = _json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        return {}
    return {}


def _write_admin_settings(cfg: dict) -> None:
    p = _inform_user_mods_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    import os as _os
    _os.replace(tmp, p)


def _get_inform_user_mods() -> dict:
    d = _read_admin_settings()
    um = d.get(_INFORM_USER_MODS_KEY) or {}
    return um if isinstance(um, dict) else {}


def _user_module_scope(username: str, role: str):
    """인폼 목록 필터링용 모듈 scope 반환.
      - None          : 필터 off (admin 또는 권한 설정 없음 → 기존 group 기반).
      - set({...})    : 이 모듈들만 통과. module 비어있는 인폼은 항상 통과(legacy 보호).
    """
    if role == "admin":
        return None
    um = _get_inform_user_mods()
    if username and username in um:
        return set([str(m) for m in (um[username] or [])])
    return None


def _effective_modules(username: str, role: str) -> set:
    """admin → {"__all__"} sentinel.
    inform_user_modules 에 지정이 있으면 그 set 을 사용(빈 set 포함 = 아무것도 못 봄).
    없으면 groups 기반 user_modules fallback."""
    from routers.groups import user_modules as _um
    if role == "admin":
        return {"__all__"}
    um = _get_inform_user_mods()
    if username and username in um:
        return set(um[username] or [])
    return _um(username, role)


# legacy 변수 — 다른 모듈에서 import 해도 기본값 세트로 동작.
MODULES = list(DEFAULT_MODULES)
REASONS = list(DEFAULT_REASONS)


# ── helpers ────────────────────────────────────────────────────────────
def _load() -> list:
    data = load_json(INFORMS_FILE, [])
    return data if isinstance(data, list) else []


def _save(items: list) -> None:
    save_json(INFORMS_FILE, items, indent=2)


def _new_id() -> str:
    return f"inf_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _find(items: list, iid: str) -> Optional[dict]:
    return next((x for x in items if x.get("id") == iid), None)


def _upgrade(entry: dict) -> dict:
    """Legacy v8.5.1 레코드에 v8.7.0 필드를 채워 넣는다 (in-place safe copy)."""
    entry.setdefault("lot_id", "")
    # v8.7.9: root_lot_id = lot_id[:5] (backfill).
    if not entry.get("root_lot_id"):
        entry["root_lot_id"] = (entry.get("lot_id") or "")[:5]
    entry.setdefault("product", "")
    entry.setdefault("checked", False)
    entry.setdefault("checked_by", "")
    entry.setdefault("checked_at", "")
    entry.setdefault("flow_status", "received" if not entry.get("parent_id") else "")
    entry.setdefault("status_history", [])
    entry.setdefault("splittable_change", None)
    entry.setdefault("images", [])
    entry.setdefault("embed_table", None)
    entry.setdefault("auto_generated", False)
    entry.setdefault("deadline", "")  # v8.7.1: 이슈 마감일 (YYYY-MM-DD 또는 "")
    entry.setdefault("group_ids", [])  # v8.7.6: 그룹 가시성
    # v8.8.2: status_history 의 `prev` 필드 backfill — legacy 엔트리는
    # prev 가 없어 "확인 취소" 이벤트가 TimelineLog 에서 사라졌다.
    hist = entry.get("status_history") or []
    last_status = ""
    dirty = False
    for h in hist:
        if not isinstance(h, dict):
            continue
        if "prev" not in h:
            h["prev"] = last_status
            dirty = True
        # received 이면서 이전이 completed 였다면 자동으로 "확인 취소" note 부여.
        if (h.get("status") == "received" and last_status == "completed"
                and not h.get("note")):
            h["note"] = "확인 취소"
            dirty = True
        last_status = h.get("status") or last_status
    if dirty:
        entry["status_history"] = hist
    return entry


def _group_visible(entry: dict, username: str, role: str) -> bool:
    """v8.7.6: group_ids 기반 가시성. 비어 있으면 public."""
    gids = entry.get("group_ids") or []
    if not gids:
        return True
    if role == "admin":
        return True
    try:
        from routers.groups import user_group_ids as _ugids
        my = _ugids(username, role)
    except Exception:
        my = set()
    return any(g in my for g in gids)


def _load_upgraded() -> list:
    items = _load()
    changed = False
    for x in items:
        before_keys = set(x.keys())
        _upgrade(x)
        if set(x.keys()) != before_keys:
            changed = True
    if changed:
        _save(items)
    return items


def _visible_to(entry: dict, username: str, role: str, my_mods: set) -> bool:
    """admin/all-rounder 전부 통과. 그 외에는 본인이 작성했거나 모듈 담당인 경우.
    v8.7.6: group_ids 가 설정된 인폼은 해당 그룹에 속해야만 추가로 통과."""
    if role == "admin" or "__all__" in my_mods:
        return True
    if not _group_visible(entry, username, role):
        return False
    if entry.get("author") == username:
        return True
    mod = entry.get("module") or ""
    if mod and mod in my_mods:
        return True
    return False


def _can_moderate(entry: dict, username: str, role: str, my_mods: set) -> bool:
    """체크·상태변경 권한: admin 또는 해당 module 담당자 또는 작성자."""
    if role == "admin":
        return True
    if entry.get("author") == username:
        return True
    mod = entry.get("module") or ""
    return bool(mod and mod in my_mods)


def _root_id(items: list, entry: dict) -> str:
    """entry 가 속한 루트 인폼의 id 반환."""
    cur = entry
    seen: set = set()
    while cur and cur.get("parent_id"):
        if cur["id"] in seen:
            break
        seen.add(cur["id"])
        parent = _find(items, cur.get("parent_id"))
        if not parent:
            break
        cur = parent
    return cur.get("id", "") if cur else ""


# ── Pydantic ───────────────────────────────────────────────────────────
class SplitChange(BaseModel):
    column: str = ""
    old_value: str = ""
    new_value: str = ""
    applied: bool = False


# v8.8.23: CI (case-insensitive) 컬럼 해석 헬퍼 — splittable.py 와 동일 정신.
#   ML_TABLE_<PROD> 가 대문자 (ROOT_LOT_ID / LOT_ID / FAB_LOT_ID / WAFER_ID) 로 찍히거나,
#   hive 원천이 소문자로 찍혀도 동일 컬럼으로 인식.
def _ci_resolve(name: str, pool) -> str:
    """pool 에서 name 과 casefold equal 인 실제 컬럼명 반환. 없으면 '' ."""
    if not name:
        return ""
    want = str(name).casefold()
    for n in pool:
        if str(n).casefold() == want:
            return n
    return ""


def _ci_pick_first(candidates, pool) -> str:
    """candidates 중 pool 에 CI 존재하는 첫 이름(실제 casing) 반환."""
    for c in candidates:
        got = _ci_resolve(c, pool)
        if got:
            return got
    return ""


# v8.8.15: 인폼 저장 시점의 fab_lot_id 를 ML_TABLE 에서 resolve.
#   FE 가 명시적으로 보내지 않았을 때만 호출. splittable 의 /view 가 fab_lot_id 를 join 해 주는 로직을
#   재구현하면 비용/결합이 크므로, 여기서는 ML_TABLE_<PRODUCT>.parquet 에서 lot_id==root_lot_id 인
#   가장 최근 행의 fab_lot_id 하나만 싸게 조회 (실패해도 "" 반환).
# v8.8.23: 컬럼명을 case-insensitive 로 매칭 — ML_TABLE 이 대문자(ROOT_LOT_ID/FAB_LOT_ID) 로
#   찍혀도 정상 추출. 기존엔 literal 비교라 대/소문자 혼재 시 "" 반환 → fab_lot_id 누락.
def _resolve_fab_lot_snapshot(product: str, lot_id: str, wafer_id: str) -> str:
    try:
        if not product or not (lot_id or wafer_id):
            return ""
        import polars as pl  # runtime optional
        from core.roots import get_base_root as _get_base_root
        base_dir = _get_base_root()
        if not base_dir or not Path(base_dir).exists():
            return ""
        base_dir = Path(base_dir)
        # product 는 "ML_TABLE_<X>" 형태이거나 순수 제품명일 수 있음.
        stem = product if product.startswith("ML_TABLE_") else f"ML_TABLE_{product}"
        candidates = [base_dir / f"{stem}.parquet", base_dir / f"{stem.upper()}.parquet"]
        fp = next((c for c in candidates if c.exists()), None)
        if not fp:
            return ""
        lf = pl.scan_parquet(fp)
        names = list(lf.collect_schema().names()) if hasattr(lf, "collect_schema") else list(lf.schema.keys())
        fab_col = _ci_resolve("fab_lot_id", names)
        if not fab_col:
            return ""
        # lot_id / wafer_id 컬럼 CI 감지 — root_lot_id 우선.
        lot_col = _ci_pick_first(("root_lot_id", "lot_id"), names)
        wf_col = _ci_resolve("wafer_id", names)
        if not lot_col and not wf_col:
            return ""
        key = (lot_id or "")[:5]
        expr = None
        if lot_col and key:
            expr = pl.col(lot_col).cast(pl.Utf8).str.starts_with(key)
        elif wf_col and wafer_id:
            expr = pl.col(wf_col).cast(pl.Utf8) == str(wafer_id)
        if expr is None:
            return ""
        # 최신 fab_lot_id 하나만 뽑기 (order-by 없이 임의의 첫 값 — 스냅샷 용도로 충분).
        df = lf.filter(expr).select(pl.col(fab_col).cast(pl.Utf8)).head(1).collect()
        if df.height == 0:
            return ""
        v = df.item(0, 0)
        return str(v or "").strip()
    except Exception:
        return ""


class ImageRef(BaseModel):
    filename: str
    url: str
    size: int = 0


class EmbedTable(BaseModel):
    source: str = ""          # 예: "SplitTable/PROD_A"
    columns: List[str] = []
    rows: List[List] = []
    note: str = ""


class InformCreate(BaseModel):
    # v8.7.9: wafer_id 선택 필드. 없으면 lot_id 로 자동 채움 (스레드 묶기 용).
    wafer_id: str = ""
    lot_id: str = ""
    product: str = ""
    module: str = ""
    reason: str = ""
    text: str = ""
    parent_id: Optional[str] = None
    splittable_change: Optional[SplitChange] = None
    images: List[ImageRef] = []
    embed_table: Optional[EmbedTable] = None
    # v8.7.9: deadline 필드 폐기. 호환을 위해 스키마에 남겨 두되 저장하지 않음.
    deadline: str = ""
    group_ids: List[str] = []  # v8.7.6: 그룹 가시성 필터. 비어 있으면 public (모듈 규칙만 적용)
    # v8.8.15: 저장 시점의 fab_lot_id 스냅샷 — FE SplitTable 맥락에서 resolve 된 값.
    #   이후 ML_TABLE 이 재빌드되어 fab_lot_id 매핑이 바뀌어도, 인폼이 가리키던 fab_lot_id 는 보존된다.
    fab_lot_id_at_save: str = ""


class ConfigReq(BaseModel):
    modules: Optional[List[str]] = None
    reasons: Optional[List[str]] = None
    products: Optional[List[str]] = None
    raw_db_root: Optional[str] = None
    # v8.8.17: { "<reason>": {"subject": "...", "body": "..."} } — admin 만 편집.
    reason_templates: Optional[dict] = None


class ProductReq(BaseModel):
    product: str


class StatusReq(BaseModel):
    status: str
    note: str = ""


class CheckReq(BaseModel):
    checked: bool


class DeadlineReq(BaseModel):
    deadline: str = ""  # YYYY-MM-DD 또는 "" (해제)


def _validate_deadline(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        datetime.date.fromisoformat(s[:10])
        return s[:10]
    except Exception:
        raise HTTPException(400, "deadline 포맷: YYYY-MM-DD")


# ── Endpoints ──────────────────────────────────────────────────────────
@router.get("/modules")
def list_modules():
    cfg = _load_config()
    return {
        "modules": cfg["modules"],
        "reasons": cfg["reasons"],
        "flow_statuses": FLOW_STATUSES,
    }


@router.get("/config")
def get_config():
    return _load_config()


@router.post("/config")
def save_config_endpoint(req: ConfigReq, _admin=Depends(require_admin)):
    """Admin 전용 — 모듈/사유 옵션 목록 편집."""
    cfg = _load_config()
    if req.modules is not None:
        cfg["modules"] = [m.strip() for m in req.modules if m and m.strip()]
    if req.reasons is not None:
        cfg["reasons"] = [r.strip() for r in req.reasons if r and r.strip()]
    if req.products is not None:
        cfg["products"] = [p.strip() for p in req.products if p and p.strip()]
    if req.raw_db_root is not None:
        cfg["raw_db_root"] = req.raw_db_root.strip()
    # v8.8.17: 사유별 메일 템플릿 upsert. None = 변경 없음, {} = 전체 비움.
    if req.reason_templates is not None:
        rt_in = req.reason_templates or {}
        rt_out = {}
        if isinstance(rt_in, dict):
            for k, v in rt_in.items():
                if isinstance(v, dict):
                    s = str(v.get("subject", "") or "")[:500]
                    b = str(v.get("body", "") or "")[:10000]
                    if s or b:
                        rt_out[str(k)[:100]] = {"subject": s, "body": b}
        cfg["reason_templates"] = rt_out
    # de-dup 유지 순서
    cfg["modules"] = list(dict.fromkeys(cfg["modules"]))
    cfg["reasons"] = list(dict.fromkeys(cfg["reasons"]))
    cfg["products"] = list(dict.fromkeys(cfg.get("products") or []))
    if not cfg["modules"]:
        cfg["modules"] = list(DEFAULT_MODULES)
    if not cfg["reasons"]:
        cfg["reasons"] = list(DEFAULT_REASONS)
    _save_config(cfg)
    return {"ok": True, "config": cfg}


# v8.8.13: 유저별 인폼 모듈 조회 권한 엔드포인트 ────────────────────────
class UserModulesSaveReq(BaseModel):
    username: str
    modules: List[str] = []


@router.get("/user-modules")
def list_user_modules(request: Request):
    """Admin: 인폼 탭 접근 가능한 유저 + 각자의 현재 모듈 권한.
    인폼 탭 권한이 있는 유저(tabs 에 'inform' 또는 '__all__') 만 노출."""
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    from routers.auth import read_users
    um = _get_inform_user_mods()
    out = []
    for u in read_users():
        if u.get("status") != "approved":
            continue
        tabs = (u.get("tabs") or "").strip()
        has_inform = (tabs == "__all__") or ("inform" in [t.strip() for t in tabs.split(",")])
        if u.get("role") != "admin" and not has_inform:
            continue
        un = u.get("username") or ""
        out.append({
            "username": un,
            "role": u.get("role", "user"),
            "email": u.get("email") or "",
            "modules": list(um.get(un, [])),
            "has_setting": un in um,
        })
    return {"users": out}


@router.post("/user-modules/save")
def save_user_modules(req: UserModulesSaveReq, request: Request):
    """Admin: 특정 유저의 인폼 모듈 조회 권한 저장. 빈 배열 = '아무 모듈도 조회 못함'."""
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    uname = (req.username or "").strip()
    if not uname:
        raise HTTPException(400, "username required")
    cfg = _read_admin_settings()
    um = dict(cfg.get(_INFORM_USER_MODS_KEY) or {})
    mods = [str(m).strip() for m in (req.modules or []) if str(m).strip()]
    um[uname] = list(dict.fromkeys(mods))
    cfg[_INFORM_USER_MODS_KEY] = um
    _write_admin_settings(cfg)
    _audit(request, "inform:user-modules",
           detail=f"user={uname} modules={','.join(um[uname])}", tab="inform")
    return {"ok": True, "username": uname, "modules": um[uname]}


@router.post("/user-modules/clear")
def clear_user_modules(req: UserModulesSaveReq, request: Request):
    """Admin: 특정 유저의 권한 설정 완전 제거 → group 기반 fallback 으로 복귀."""
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "admin only")
    uname = (req.username or "").strip()
    if not uname:
        raise HTTPException(400, "username required")
    cfg = _read_admin_settings()
    um = dict(cfg.get(_INFORM_USER_MODS_KEY) or {})
    um.pop(uname, None)
    cfg[_INFORM_USER_MODS_KEY] = um
    _write_admin_settings(cfg)
    return {"ok": True, "username": uname, "cleared": True}


@router.get("/my-modules")
def my_inform_modules(request: Request):
    """현재 유저의 인폼 모듈 조회 권한.
      - admin → all_rounder=True
      - inform_user_modules 에 저장된 값 있으면 그걸 사용
      - 그 외엔 /api/groups/my-modules 값으로 fallback
    """
    me = current_user(request)
    uname = me.get("username") or ""
    role = me.get("role") or "user"
    if role == "admin":
        return {"modules": [], "all_rounder": True, "source": "admin"}
    um = _get_inform_user_mods()
    if uname in um:
        return {"modules": list(um[uname]), "all_rounder": False, "source": "inform_user_modules"}
    # fallback: groups.user_modules 에서 compute
    try:
        from routers.groups import user_modules
        mods = user_modules(uname, role) or set()
        # "__all__" sentinel 은 admin 경로에서만 나오므로 여기선 없음.
        return {"modules": list(mods), "all_rounder": False, "source": "groups"}
    except Exception:
        return {"modules": [], "all_rounder": False, "source": "fallback"}


# v8.8.1: 제품 카탈로그 CRUD (모든 로그인 유저 — 등록된 제품 선택용).
@router.post("/products/add")
def add_product(req: ProductReq, request: Request):
    me = current_user(request)
    p = (req.product or "").strip()
    if not p:
        raise HTTPException(400, "product required")
    cfg = _load_config()
    products = list(cfg.get("products") or [])
    if p not in products:
        products.append(p)
    cfg["products"] = products
    _save_config(cfg)
    _audit(request, "inform:product_add", detail=f"product={p} by={me['username']}", tab="inform")
    return {"ok": True, "products": products}


@router.post("/products/delete")
def delete_product(req: ProductReq, request: Request):
    """v8.8.1: 카탈로그에서 제품 삭제. admin 또는 등록자(추적불가시 admin) 권한.
    실제 인폼 레코드(product 필드)는 건드리지 않음 — 드롭다운에서만 제외."""
    me = current_user(request)
    p = (req.product or "").strip()
    if not p:
        raise HTTPException(400, "product required")
    cfg = _load_config()
    before = list(cfg.get("products") or [])
    after = [x for x in before if x != p]
    if len(after) == len(before):
        raise HTTPException(404, "product not in catalog")
    cfg["products"] = after
    _save_config(cfg)
    _audit(request, "inform:product_delete", detail=f"product={p} by={me['username']}", tab="inform")
    return {"ok": True, "products": after}


@router.get("/product-lots")
def list_product_lots(request: Request, product: str = Query(...)):
    """v8.8.1: Admin 이 설정한 raw_db_root 에서 제품별 Lot 후보 스캔.
    스캔 위치: {raw_db_root}/1.RAWDATA_DB/{product}/  (서브폴더 이름을 lot 으로 간주).
    폴더가 없거나 설정 안 된 경우 빈 리스트."""
    _ = current_user(request)
    cfg = _load_config()
    root = (cfg.get("raw_db_root") or "").strip()
    product = (product or "").strip()
    if not root or not product:
        return {"product": product, "lots": [], "source": ""}
    try:
        # 표준 경로. 필요시 여러 후보 검색.
        candidates = [
            Path(root) / "1.RAWDATA_DB" / product,
            Path(root) / product,
        ]
        target = next((c for c in candidates if c.exists() and c.is_dir()), None)
        if not target:
            return {"product": product, "lots": [], "source": str(candidates[0])}
        lots = sorted({d.name for d in target.iterdir() if d.is_dir() and not d.name.startswith(".")})
        return {"product": product, "lots": lots, "source": str(target)}
    except Exception as e:
        return {"product": product, "lots": [], "source": root, "error": str(e)}


# ── Image upload / serving ────────────────────────────────────────────
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(name: str) -> str:
    name = Path(name).name  # strip dirs
    name = _SAFE_NAME_RE.sub("_", name)
    return name[-120:] or "file"


@router.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """인폼용 이미지 업로드. 유저당 세션으로만 가능 (current_user 검증)."""
    me = current_user(request)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, f"이미지 형식만 업로드 가능합니다 ({', '.join(sorted(ALLOWED_IMAGE_EXTS))}).")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "파일이 너무 큽니다 (최대 8MB).")
    if not data:
        raise HTTPException(400, "빈 파일입니다.")

    uid = uuid.uuid4().hex[:12]
    safe = _safe_filename(file.filename or ("image" + ext))
    if not safe.lower().endswith(ext):
        safe += ext
    subdir = UPLOADS_DIR / uid
    subdir.mkdir(parents=True, exist_ok=True)
    dst = subdir / safe
    dst.write_bytes(data)

    url = f"/api/informs/files/{uid}/{safe}"
    return {"ok": True, "filename": safe, "url": url, "size": len(data),
            "uploaded_by": me["username"]}


# v8.8.18: 메일 첨부용 범용 업로드 — 이미지뿐 아니라 xlsx/pptx/pdf 등 모든 파일.
#   인폼 메일 다이얼로그에서 사용자가 선택한 파일을 먼저 이 엔드포인트로 올려
#   반환 URL 을 send-mail 의 attachments 에 포함. `_resolve_inform_attachment`
#   가 동일 경로 규약(/api/informs/files/<uid>/<name>)으로 이미 해석 가능.
_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024   # 10MB 개별 / send-mail 에서 총합 10MB 한도 재검사
_ATTACHMENT_BLOCKED_EXTS = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi",
                             ".dll", ".vbs", ".ps1", ".sh", ".js"}


@router.post("/upload-attachment")
async def upload_attachment(request: Request, file: UploadFile = File(...)):
    """메일 첨부용 범용 업로드. 실행 가능한 확장자(.exe/.bat/...)는 차단.
    반환 URL 은 이미지 업로드와 동일 경로 규약 → 기존 send-mail attachment resolver 재사용."""
    me = current_user(request)
    ext = Path(file.filename or "").suffix.lower()
    if ext in _ATTACHMENT_BLOCKED_EXTS:
        raise HTTPException(400, f"보안상 업로드 차단된 확장자: {ext}")
    data = await file.read()
    if len(data) > _ATTACHMENT_MAX_BYTES:
        raise HTTPException(413, f"파일이 너무 큽니다 (최대 {_ATTACHMENT_MAX_BYTES // (1024*1024)}MB).")
    if not data:
        raise HTTPException(400, "빈 파일입니다.")

    uid = uuid.uuid4().hex[:12]
    safe = _safe_filename(file.filename or ("attachment" + ext))
    if ext and not safe.lower().endswith(ext):
        safe += ext
    subdir = UPLOADS_DIR / uid
    subdir.mkdir(parents=True, exist_ok=True)
    dst = subdir / safe
    dst.write_bytes(data)

    url = f"/api/informs/files/{uid}/{safe}"
    mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    return {"ok": True, "filename": safe, "url": url, "size": len(data),
            "mime": mime, "uploaded_by": me["username"]}


@router.get("/files/{uid}/{name}")
def serve_image(request: Request, uid: str, name: str):
    """업로드 이미지 서빙. path traversal 차단."""
    # 인증은 전역 미들웨어가 처리하지만 방어적 검증.
    _ = current_user(request)
    if not re.fullmatch(r"[A-Za-z0-9]+", uid):
        raise HTTPException(400, "bad uid")
    safe = _safe_filename(name)
    dst = (UPLOADS_DIR / uid / safe).resolve()
    try:
        dst.relative_to(UPLOADS_DIR.resolve())
    except Exception:
        raise HTTPException(403, "path traversal")
    if not dst.is_file():
        raise HTTPException(404)
    mime, _ = mimetypes.guess_type(str(dst))
    return FileResponse(str(dst), media_type=mime or "application/octet-stream")


@router.get("")
def list_by_wafer(wafer_id: str = Query(..., min_length=1)):
    items = [x for x in _load_upgraded() if x.get("wafer_id") == wafer_id]
    items.sort(key=lambda x: x.get("created_at", ""))
    return {"informs": items}


@router.get("/recent")
def recent_roots(request: Request, limit: int = Query(50, ge=1, le=500)):
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    roots = [x for x in items if not x.get("parent_id")]
    roots = [x for x in roots if _visible_to(x, me["username"], me.get("role", "user"), my_mods)]
    roots.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"informs": roots[:limit]}


@router.get("/wafers")
def list_wafers(request: Request, limit: int = Query(500, ge=1, le=5000)):
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    seen: dict = {}
    for x in items:
        w = x.get("wafer_id")
        if not w:
            continue
        if not _visible_to(x, me["username"], me.get("role", "user"), my_mods):
            continue
        cur = seen.get(w)
        ts = x.get("created_at", "")
        if cur is None or ts > cur.get("last", ""):
            if cur is None:
                seen[w] = {"wafer_id": w, "last": ts, "count": 0, "lot_id": x.get("lot_id", ""),
                           "product": x.get("product", "")}
            else:
                cur["last"] = ts
                if x.get("lot_id"):
                    cur["lot_id"] = x.get("lot_id")
                if x.get("product"):
                    cur["product"] = x.get("product")
        seen[w]["count"] = seen[w].get("count", 0) + 1
    arr = sorted(seen.values(), key=lambda v: v.get("last", ""), reverse=True)
    return {"wafers": arr[:limit]}


@router.get("/by-lot")
def by_lot(request: Request, lot_id: str = Query(..., min_length=1)):
    """v8.7.9: 앞 5자(root_lot_id) 매칭. ABCDE 로 검색 시 ABCDE01, ABCDE02 … 전부 포함.
    길이 > 5 인 쿼리는 root_lot = query[:5] 로 축약해 같은 groupings 를 반환.
    """
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    root = (lot_id or "")[:5]
    hits = [x for x in items if (x.get("root_lot_id") or (x.get("lot_id") or "")[:5]) == root]
    hits = [x for x in hits if _visible_to(x, me["username"], me.get("role", "user"), my_mods)]
    hits.sort(key=lambda x: x.get("created_at", ""))
    wafers = sorted({x.get("wafer_id") for x in hits if x.get("wafer_id")})
    lots = sorted({x.get("lot_id") for x in hits if x.get("lot_id")})
    return {"informs": hits, "wafers": wafers, "lots": lots, "root_lot_id": root, "count": len(hits)}


@router.get("/by-product")
def by_product(request: Request, product: str = Query(..., min_length=1),
               limit: int = Query(500, ge=1, le=5000)):
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    hits = [x for x in items if x.get("product") == product]
    hits = [x for x in hits if _visible_to(x, me["username"], me.get("role", "user"), my_mods)]
    # 루트 우선 최근순
    roots = [x for x in hits if not x.get("parent_id")]
    roots.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"informs": roots[:limit], "count": len(roots)}


@router.get("/my")
def my_informs(request: Request, limit: int = Query(200, ge=1, le=2000)):
    """현재 유저 모듈 범위의 인폼 루트 (담당자 대시보드)."""
    me = current_user(request)
    role = me.get("role", "user")
    my_mods = user_modules(me["username"], role)
    items = _load_upgraded()
    roots = [x for x in items if not x.get("parent_id")]
    if role == "admin" or "__all__" in my_mods:
        vis = roots
    else:
        vis = [
            x for x in roots
            if (x.get("module") in my_mods) or x.get("author") == me["username"]
        ]
    vis.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"informs": vis[:limit], "all_rounder": role == "admin" or "__all__" in my_mods,
            "my_modules": [] if "__all__" in my_mods else sorted(my_mods)}


@router.get("/products")
def list_products(request: Request):
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    seen: dict = {}
    for x in items:
        p = x.get("product")
        if not p:
            continue
        if not _visible_to(x, me["username"], me.get("role", "user"), my_mods):
            continue
        s = seen.setdefault(p, {"product": p, "count": 0, "last": ""})
        s["count"] += 1
        ts = x.get("created_at", "")
        if ts > s["last"]:
            s["last"] = ts
    arr = sorted(seen.values(), key=lambda v: v["last"], reverse=True)
    return {"products": arr}


@router.get("/lots")
def list_lots(request: Request):
    """v8.7.9: root_lot_id 기준으로 그룹핑. 각 root 아래에 포함된 fab_lots 와 합계.
    하위 호환: lot_id 에 root_lot_id 를 넣어 기존 FE 의 selectedLot 흐름이 그대로 동작.
    """
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    seen: dict = {}
    for x in items:
        l = x.get("lot_id")
        if not l:
            continue
        if not _visible_to(x, me["username"], me.get("role", "user"), my_mods):
            continue
        root = x.get("root_lot_id") or (l[:5] if l else "")
        if not root:
            continue
        s = seen.setdefault(root, {
            "lot_id": root,              # FE 호환: selectedLot 키
            "root_lot_id": root,
            "count": 0, "last": "",
            "product": x.get("product", ""),
            "fab_lots": set(),
        })
        s["count"] += 1
        ts = x.get("created_at", "")
        if ts > s["last"]:
            s["last"] = ts
        if x.get("product"):
            s["product"] = x.get("product")
        s["fab_lots"].add(l)
    arr = []
    for s in seen.values():
        s["fab_lots"] = sorted(s["fab_lots"])
        arr.append(s)
    arr.sort(key=lambda v: v["last"], reverse=True)
    return {"lots": arr}


@router.post("")
def create_inform(req: InformCreate, request: Request):
    me = current_user(request)
    # v8.7.9: wafer_id 는 선택. 없으면 lot_id 로 자동 채움. 둘 다 없고 parent 도 없으면 400.
    wid = (req.wafer_id or "").strip()
    lot_for_fallback = (req.lot_id or "").strip()
    if not wid:
        wid = lot_for_fallback
    if not wid and not req.parent_id:
        raise HTTPException(400, "lot_id (또는 wafer_id) 가 필요합니다.")
    items = _load_upgraded()

    # parent 검증 + 상속 (lot_id / product).
    inherit_lot = (req.lot_id or "").strip()
    inherit_product = (req.product or "").strip()
    if req.parent_id:
        parent = _find(items, req.parent_id)
        if not parent:
            raise HTTPException(404, "parent not found")
        if parent.get("wafer_id") != wid:
            raise HTTPException(400, "parent wafer mismatch")
        # 자식은 부모 lot/product 상속 (입력 없을 때)
        inherit_lot = inherit_lot or parent.get("lot_id", "")
        inherit_product = inherit_product or parent.get("product", "")

    sc = None
    if req.splittable_change and (req.splittable_change.column or req.splittable_change.new_value):
        sc = {
            "column": (req.splittable_change.column or "").strip(),
            "old_value": (req.splittable_change.old_value or "").strip(),
            "new_value": (req.splittable_change.new_value or "").strip(),
            "applied": bool(req.splittable_change.applied),
        }

    # 이미지 화이트리스트: 서버에 저장된 업로드 경로만 허용 (URL 필터링).
    imgs = []
    for im in (req.images or []):
        if not im.url or not im.url.startswith("/api/informs/files/"):
            continue
        imgs.append({
            "filename": _safe_filename(im.filename or "image"),
            "url": im.url,
            "size": max(0, int(im.size or 0)),
        })

    embed = None
    if req.embed_table and (req.embed_table.columns or req.embed_table.rows):
        cols = [str(c) for c in (req.embed_table.columns or [])][:40]
        rows = []
        for r in (req.embed_table.rows or [])[:200]:
            if isinstance(r, list):
                rows.append([("" if v is None else str(v)) for v in r[:len(cols) if cols else 40]])
        embed = {
            "source": (req.embed_table.source or "").strip()[:160],
            "columns": cols,
            "rows": rows,
            "note": (req.embed_table.note or "").strip()[:500],
        }

    now = _now()
    is_root = not req.parent_id
    # v8.7.9: root_lot_id = lot_id[:5] — lot 검색의 앞5자 그룹핑 키.
    root_lot = (inherit_lot or "")[:5]
    entry = {
        "id": _new_id(),
        "parent_id": req.parent_id or None,
        "wafer_id": wid,
        "lot_id": inherit_lot,
        "root_lot_id": root_lot,
        "product": inherit_product,
        "module": (req.module or "").strip(),
        "reason": (req.reason or "").strip(),
        "text": (req.text or "").strip(),
        "author": me["username"],
        "created_at": now,
        "checked": False,
        "checked_by": "",
        "checked_at": "",
        "flow_status": "received" if is_root else "",
        "status_history": (
            [{"status": "received", "actor": me["username"], "at": now, "note": "created"}]
            if is_root else []
        ),
        "splittable_change": sc,
        "images": imgs,
        "embed_table": embed,
        "auto_generated": False,
        # v8.7.9: deadline 필드 폐기 — 저장하지 않음.
        "group_ids": [str(g).strip() for g in (req.group_ids or []) if g and str(g).strip()],
        # v8.8.15: fab_lot_id 스냅샷 — FE 가 SplitTable 맥락에서 resolve 해서 보내준 값.
        #   없으면 BE 에서 resolve 시도 (product + lot_id 기준으로 ML_TABLE 의 최신 fab_lot_id 조회).
        "fab_lot_id_at_save": (req.fab_lot_id_at_save or "").strip() or _resolve_fab_lot_snapshot(inherit_product, inherit_lot, wid),
    }
    items.append(entry)
    _save(items)
    _audit(request, "inform:reply" if req.parent_id else "inform:create",
           detail=f"wafer={wid} module={entry['module']} lot={inherit_lot}", tab="inform")
    return {"ok": True, "inform": entry}


# ── Auto-log helper (다른 라우터가 import) ─────────────────────────────
def auto_log_splittable_change(author: str, product: str, lot_id: str,
                               cell_key: str, old_value, new_value, action: str = "set") -> None:
    """SplitTable plan 변경이 일어나면 해당 lot 에 자동 인폼 루트를 남긴다.

    - wafer_id 가 없으면 lot_id 를 placeholder 로 사용 (스레드는 lot 뷰에서 묶여 보임).
    - module 은 cell_key prefix 로 추정 (KNOB/MASK/FAB → 기타). 추후 룰 확장.
    - auto_generated=True 로 표시 → FE 에서 시스템 발행 카드로 렌더.
    """
    try:
        items = _load_upgraded()
        col = cell_key.split("|")[-1] if "|" in cell_key else cell_key
        upper = col.upper()
        if upper.startswith("MASK_"):
            mod = "MASK"
        elif upper.startswith("FAB_"):
            mod = "FAB"
        elif upper.startswith("KNOB_") or "_" not in upper:
            mod = "KNOB"
        else:
            mod = ""
        now = _now()
        text = f"[SplitTable 자동기록] {action} · {col} · {old_value!r} → {new_value!r}"
        entry = {
            "id": _new_id(),
            "parent_id": None,
            "wafer_id": lot_id or product or "auto",
            "lot_id": lot_id or "",
            "product": product or "",
            "module": mod,
            "reason": "레시피 변경",
            "text": text,
            "author": author or "system",
            "created_at": now,
            "checked": False, "checked_by": "", "checked_at": "",
            "flow_status": "received",
            "status_history": [{"status": "received", "actor": author or "system",
                                "at": now, "note": "auto from SplitTable"}],
            "splittable_change": {
                "column": col,
                "old_value": ("" if old_value is None else str(old_value)),
                "new_value": ("" if new_value is None else str(new_value)),
                "applied": (action == "set"),
            },
            "images": [],
            "embed_table": None,
            "auto_generated": True,
        }
        items.append(entry)
        _save(items)
    except Exception:
        # 자동기록 실패로 인해 plan 저장까지 실패시키면 안 됨.
        pass


@router.post("/delete")
def delete_inform(request: Request, id: str = Query(...)):
    """v8.8.12: 공동편집 정책 확장 — 원작성자 / admin / 동일 모듈 담당자 모두 삭제 가능.
    자식 있으면 무결성 차단."""
    me = current_user(request)
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    role = me.get("role", "user")
    my_mods = user_modules(me["username"], role)
    allowed = (
        target.get("author") == me["username"]
        or role == "admin"
        or _can_moderate(target, me["username"], role, my_mods)
    )
    if not allowed:
        raise HTTPException(403, "삭제 권한이 없습니다 (작성자/admin/모듈담당자).")
    has_child = any(x.get("parent_id") == id for x in items)
    if has_child:
        raise HTTPException(400, "답글이 달린 글은 삭제할 수 없습니다.")
    items = [x for x in items if x.get("id") != id]
    _save(items)
    _audit(request, "inform:delete", detail=f"id={id} by={me['username']}", tab="inform")
    return {"ok": True}


class InformEditReq(BaseModel):
    text: Optional[str] = None
    module: Optional[str] = None
    reason: Optional[str] = None
    # wafer_id / lot_id / product 는 변경 불가 (스레드/매칭 깨짐 방지).


@router.post("/edit")
def edit_inform(req: InformEditReq, request: Request, id: str = Query(...)):
    """v8.8.12: 등록된 인폼의 본문/모듈/사유 수정.
    v8.8.13: 수정 권한을 **admin 전용**으로 제한. 작성자 본인도 수정 불가 (답글로 추가만 가능)."""
    me = current_user(request)
    if me.get("role") != "admin":
        raise HTTPException(403, "수정은 관리자(admin)만 가능합니다. 내용 변경은 답글로 추가하세요.")
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    now = datetime.datetime.now().isoformat()
    hist = target.get("edit_history") or []
    changed = []
    if req.text is not None and (req.text or "").strip() != (target.get("text") or ""):
        before = target.get("text") or ""
        target["text"] = (req.text or "").strip()
        hist.append({"at": now, "actor": me["username"], "field": "text",
                     "before": before[:400], "after": target["text"][:400],
                     "kind": "edit"})
        changed.append("text")
    if req.module is not None and (req.module or "").strip() != (target.get("module") or ""):
        before = target.get("module") or ""
        target["module"] = (req.module or "").strip()
        hist.append({"at": now, "actor": me["username"], "field": "module",
                     "before": before, "after": target["module"], "kind": "edit"})
        changed.append("module")
    if req.reason is not None and (req.reason or "").strip() != (target.get("reason") or ""):
        before = target.get("reason") or ""
        target["reason"] = (req.reason or "").strip()
        hist.append({"at": now, "actor": me["username"], "field": "reason",
                     "before": before, "after": target["reason"], "kind": "edit"})
        changed.append("reason")
    if not changed:
        return {"ok": True, "noop": True}
    target["edit_history"] = hist[-200:]
    target["updated_at"] = now
    _save(items)
    _audit(request, "inform:edit",
           detail=f"id={id} by={me['username']} fields={','.join(changed)}", tab="inform")
    return {"ok": True, "changed": changed}


@router.post("/check")
def check_inform(req: CheckReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403, "모듈 담당자만 체크할 수 있습니다.")
    target["checked"] = bool(req.checked)
    target["checked_by"] = me["username"] if req.checked else ""
    target["checked_at"] = _now() if req.checked else ""
    _save(items)
    _audit(request, "inform:check", detail=f"id={id} checked={target['checked']}", tab="inform")
    return {"ok": True, "inform": target}


@router.post("/status")
def set_status(req: StatusReq, request: Request, id: str = Query(...)):
    st = (req.status or "").strip()
    # v8.7.9: 2단계 플로우. legacy reviewing/in_progress 는 completed 전 단계로 허용하되 권장하지 않음.
    if st not in FLOW_STATUSES_LEGACY:
        raise HTTPException(400, f"invalid status; must be one of {FLOW_STATUSES_LEGACY}")
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if target.get("parent_id"):
        raise HTTPException(400, "status 는 루트 인폼에만 적용됩니다.")
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403, "모듈 담당자만 상태를 변경할 수 있습니다.")
    prev_status = target.get("flow_status") or ""
    if prev_status == st:
        return {"ok": True, "inform": target}
    target["flow_status"] = st
    hist = target.get("status_history") or []
    note = (req.note or "").strip()
    # v8.8.1: 확인 취소(completed→received) 이력 라벨링.
    if prev_status == "completed" and st == "received" and not note:
        note = "확인 취소"
    hist.append({"status": st, "prev": prev_status, "actor": me["username"],
                 "at": _now(), "note": note})
    target["status_history"] = hist
    _save(items)
    _audit(request, "inform:status", detail=f"id={id} prev={prev_status} status={st}", tab="inform")
    return {"ok": True, "inform": target}


@router.post("/deadline")
def set_deadline(req: DeadlineReq, request: Request, id: str = Query(...)):
    """루트 인폼의 마감일을 설정/해제. 작성자/모듈 담당자/admin 만 가능."""
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if target.get("parent_id"):
        raise HTTPException(400, "deadline 은 루트 인폼에만 설정 가능합니다.")
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403, "작성자/모듈 담당자/관리자만 변경 가능합니다.")
    dl = _validate_deadline(req.deadline)
    target["deadline"] = dl
    _save(items)
    _audit(request, "inform:deadline", detail=f"id={id} deadline={dl or '(clear)'}", tab="inform")
    return {"ok": True, "inform": target}


# ── v8.8.0: 제품별 담당자 (product contacts) ───────────────────────
# 좌측 사이드바 + 메일 본문 자동 삽입용. 모든 로그인 유저가 CRUD 가능.
PRODUCT_CONTACTS_FILE = INFORMS_DIR / "product_contacts.json"


def _load_product_contacts() -> dict:
    data = load_json(PRODUCT_CONTACTS_FILE, {"products": {}})
    if isinstance(data, dict) and isinstance(data.get("products"), dict):
        return data
    return {"products": {}}


def _save_product_contacts(data: dict) -> None:
    save_json(PRODUCT_CONTACTS_FILE, data)


def _new_contact_id() -> str:
    import secrets as _secrets
    return "pc_" + _secrets.token_hex(5)


class ProductContactReq(BaseModel):
    product: str
    name: str
    role: str = ""           # 직책/역할 (예: "PIE", "측정")
    email: str = ""
    phone: str = ""
    note: str = ""


@router.get("/product-contacts")
def list_product_contacts(product: str = Query("")):
    data = _load_product_contacts()
    products = data.get("products") or {}
    if product:
        return {"product": product, "contacts": products.get(product, [])}
    return {"products": products}


@router.post("/product-contacts")
def add_product_contact(req: ProductContactReq, request: Request):
    me = current_user(request)
    prod = (req.product or "").strip()
    name = (req.name or "").strip()
    if not prod or not name:
        raise HTTPException(400, "product/name required")
    data = _load_product_contacts()
    products = data.setdefault("products", {})
    contact = {
        "id": _new_contact_id(),
        "name": name,
        "role": (req.role or "").strip(),
        "email": (req.email or "").strip(),
        "phone": (req.phone or "").strip(),
        "note": (req.note or "").strip(),
        "added_by": me.get("username", ""),
        "added_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    products.setdefault(prod, []).append(contact)
    _save_product_contacts(data)
    _audit(request, "inform:product-contact-add", detail=f"product={prod} name={name}", tab="inform")
    return {"ok": True, "contact": contact}


@router.post("/product-contacts/update")
def update_product_contact(req: ProductContactReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    prod = (req.product or "").strip()
    if not prod:
        raise HTTPException(400, "product required")
    data = _load_product_contacts()
    arr = data.get("products", {}).get(prod) or []
    target = next((c for c in arr if c.get("id") == id), None)
    if not target:
        raise HTTPException(404, "contact not found")
    target["name"] = (req.name or target.get("name", "")).strip()
    target["role"] = (req.role or "").strip()
    target["email"] = (req.email or "").strip()
    target["phone"] = (req.phone or "").strip()
    target["note"] = (req.note or "").strip()
    target["updated_by"] = me.get("username", "")
    target["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save_product_contacts(data)
    return {"ok": True, "contact": target}


@router.post("/product-contacts/delete")
def delete_product_contact(request: Request, id: str = Query(...), product: str = Query(...)):
    _ = current_user(request)
    data = _load_product_contacts()
    arr = data.get("products", {}).get(product) or []
    new_arr = [c for c in arr if c.get("id") != id]
    if len(new_arr) == len(arr):
        raise HTTPException(404, "contact not found")
    data["products"][product] = new_arr
    _save_product_contacts(data)
    _audit(request, "inform:product-contact-del", detail=f"product={product} id={id}", tab="inform")
    return {"ok": True}


# v8.8.19: 인폼 제품 담당자 전용 필터 — admin 역할 / `admin`·`hol`·`test` 포함
#   username 을 제외한다. 그룹(`_is_blocked_member`) 는 v8.8.5 에서 admin 을 허용하도록
#   완화됐지만, 인폼 제품 담당자는 "메일 수신 대상 + 시스템 계정 배제" 요건이 강해
#   별도 규칙을 쓴다. eligible-users 엔드포인트와 bulk-add 양쪽에서 동일 기준 적용.
_BLOCKED_UNAME_TOKENS = ("admin", "hol", "test")


def _is_blocked_contact(username: str, user_obj: dict | None = None) -> bool:
    un = (username or "").strip()
    if not un:
        return True
    low = un.lower()
    for tok in _BLOCKED_UNAME_TOKENS:
        if tok in low:
            return True
    if isinstance(user_obj, dict):
        role = (user_obj.get("role") or "").strip().lower()
        if role == "admin":
            return True
    return False


@router.get("/eligible-contacts")
def eligible_contacts(request: Request):
    """v8.8.19: 인폼 제품 담당자로 등록 가능한 유저 목록.
    admin 역할 / admin·hol·test 포함 username 은 제외."""
    _me = current_user(request)
    try:
        from routers.auth import read_users
        users = read_users()
    except Exception:
        users = []
    out = []
    for u in users:
        un = (u.get("username") or "").strip() if isinstance(u, dict) else ""
        if not un:
            continue
        if _is_blocked_contact(un, u):
            continue
        out.append({
            "username": un,
            "email": (u.get("email") or "").strip() if isinstance(u, dict) else "",
            "role": (u.get("role") or "user") if isinstance(u, dict) else "user",
        })
    out.sort(key=lambda x: x["username"].lower())
    return {"users": out}


# v8.8.2: bulk add — 개별 유저 / 그룹 멤버 혼합 추가.
class ProductContactBulkReq(BaseModel):
    product: str
    usernames: List[str] = []         # 개별 유저 선택 결과
    group_ids: List[str] = []         # 선택한 그룹(들) — 멤버 전체 풀
    role: str = ""                    # 일괄 적용할 역할 (선택)


@router.post("/product-contacts/bulk-add")
def bulk_add_product_contacts(req: ProductContactBulkReq, request: Request):
    """유저 / 그룹 혼합 일괄 추가.

    - usernames 에 적힌 각 유저를 contacts 로 등록.
    - group_ids 의 모든 그룹 members 도 pool 에 합류.
    - admin/test 계정은 서버측에서 한 번 더 필터.
    - 이미 같은 product 에 동일 username(혹은 email) 이 등록돼 있으면 dedup.
    """
    me = current_user(request)
    prod = (req.product or "").strip()
    if not prod:
        raise HTTPException(400, "product required")
    from routers.groups import _load_users_by_name, _load as _load_groups
    users_by_name = _load_users_by_name()
    # pool: 유니크 username 모음
    pool: List[str] = []
    for un in (req.usernames or []):
        un = (un or "").strip()
        if un and un not in pool:
            pool.append(un)
    if req.group_ids:
        gids = set(req.group_ids)
        for g in _load_groups():
            if g.get("id") in gids:
                for m in (g.get("members") or []):
                    if m and m not in pool:
                        pool.append(m)
    # 필터 + 유저 프로필 resolve
    data = _load_product_contacts()
    products = data.setdefault("products", {})
    existing = products.setdefault(prod, [])
    existing_keys = set()
    for c in existing:
        uname = (c.get("source_username") or "").strip().lower()
        email = (c.get("email") or "").strip().lower()
        if uname:
            existing_keys.add(("u", uname))
        if email:
            existing_keys.add(("e", email))
    added: List[dict] = []
    skipped: List[str] = []
    for un in pool:
        u = users_by_name.get(un) or {}
        # v8.8.19: 인폼 담당자는 admin/hol/test 완전 차단 (_is_blocked_contact).
        if _is_blocked_contact(un, u):
            skipped.append(un)
            continue
        email = (u.get("email") or "").strip() if isinstance(u, dict) else ""
        name = (u.get("display_name") or u.get("name") or un) if isinstance(u, dict) else un
        key_u = ("u", un.lower())
        key_e = ("e", email.lower()) if email else None
        if key_u in existing_keys or (key_e and key_e in existing_keys):
            skipped.append(un)
            continue
        contact = {
            "id": _new_contact_id(),
            "name": name,
            "role": (req.role or (u.get("role", "") if isinstance(u, dict) else "") or "").strip(),
            "email": email,
            "phone": (u.get("phone", "") if isinstance(u, dict) else "").strip(),
            "note": "",
            "source_username": un,
            "added_by": me.get("username", ""),
            "added_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        existing.append(contact)
        added.append(contact)
        existing_keys.add(key_u)
        if key_e:
            existing_keys.add(key_e)
    _save_product_contacts(data)
    _audit(request, "inform:product-contact-bulk",
           detail=f"product={prod} added={len(added)} skipped={len(skipped)}", tab="inform")
    return {"ok": True, "added": added, "skipped": skipped, "total": len(existing)}


# ── v8.7.2: Mail relay ─────────────────────────────────────────────
ADMIN_SETTINGS_FILE = PATHS.data_root / "admin_settings.json"


def _load_mail_cfg() -> dict:
    data = load_json(ADMIN_SETTINGS_FILE, {})
    if not isinstance(data, dict):
        return {}
    m = data.get("mail") or {}
    return m if isinstance(m, dict) else {}


class SendMailReq(BaseModel):
    to: List[str] = []              # resolved email addresses (fallback)
    to_users: List[str] = []        # usernames — also resolved to emails via users.csv
    groups: List[str] = []          # recipient group names (resolved via admin settings)
    subject: str = ""               # → title
    body: str = ""                  # optional extra prose prepended to HTML body
    include_thread: bool = True     # include full thread HTML in content
    status_code: str = ""           # per-send override; else admin default
    attachments: List[str] = []     # inform image URLs to attach


@router.get("/mail-groups")
def list_mail_groups(request: Request):
    """v8.8.23: 단일 진실원 = groups.json (Admin 그룹).
    이름 → [이메일] 리스트 포맷으로 resolve (members 의 사내 이메일 + extra_emails).
    legacy 엔드포인트 — recipient_groups 와 동일 응답 모양을 유지해 FE 호환성 확보.
    """
    _ = current_user(request)
    try:
        from routers.groups import _load as _groups_load
        groups = _groups_load()
    except Exception:
        groups = []
    # username → email 매핑 (users.csv + admin 도메인 자동합성).
    try:
        from routers.auth import read_users
        users = read_users()
    except Exception:
        users = []
    try:
        from core.mail import load_mail_cfg as _load_mcfg
        _domain = (_load_mcfg().get("domain") or "").strip().lstrip("@")
    except Exception:
        _domain = ""
    un2em = {}
    for u in users or []:
        un = (u.get("username") or "").strip()
        if not un:
            continue
        em = (u.get("email") or "").strip()
        if em and "@" in em:
            un2em[un] = em
        elif "@" in un and "." in un.split("@", 1)[1]:
            un2em[un] = un
        elif _domain:
            un2em[un] = f"{un}@{_domain}"
    out: dict = {}
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        name = (g.get("name") or "").strip()
        if not name:
            continue
        emails = []
        seen = set()
        for un in (g.get("members") or []):
            em = un2em.get(un, "")
            if em and em not in seen:
                seen.add(em); emails.append(em)
        for em in (g.get("extra_emails") or []):
            em = str(em).strip()
            if em and "@" in em and em not in seen:
                seen.add(em); emails.append(em)
        out[name] = emails
    return {"groups": out}


@router.get("/recipients")
def list_recipients(request: Request):
    """모든 승인 유저 + email. 인폼 메일 수신자 선택용 (로그인 유저 누구나 조회).

    v8.8.17: username 이 곧 사내 email 인 케이스를 effective_email 로 보조 노출.
    v8.8.22: admin 메일 도메인(admin_settings.mail.domain) 이 설정돼 있으면
      `<username>@<domain>` 으로 자동 합성. users.csv 에 email 이 안 적혀 있어도
      개별 유저가 picker 에 정상 노출되어 "리스트가 비어 있음" 문제 해결.
    """
    _ = current_user(request)  # enforce login
    from routers.auth import read_users
    # v8.8.22: admin 메일 도메인 가져와 CI 합성.
    try:
        from core.mail import load_mail_cfg as _load_mcfg
        _domain = (_load_mcfg().get("domain") or "").strip().lstrip("@")
    except Exception:
        _domain = ""
    out = []
    for u in read_users():
        if u.get("status") != "approved":
            continue
        un = u.get("username", "") or ""
        em = (u.get("email", "") or "").strip()
        if em and "@" in em:
            eff = em
        elif "@" in un and "." in un.split("@", 1)[1]:
            eff = un
        elif _domain and un:
            eff = f"{un}@{_domain}"
        else:
            eff = ""
        # v8.8.21: 인폼 메일 수신자 picker 에서는 admin/hol/test 계정 + 이메일 해결 불가 계정
        #   (_is_blocked_contact 기준) 은 아예 제외. FE 가 "(no email)" 을 표시할 상황이 없어진다.
        if _is_blocked_contact(un, u):
            continue
        if not eff:
            continue
        out.append({
            "username": un,
            "email": em,
            "effective_email": eff,
            "role": u.get("role", ""),
        })
    out.sort(key=lambda x: (x.get("username") or "").lower())
    return {"recipients": out}


def _thread_text(items: list, root_id: str) -> str:
    """작성 시각 순으로 root+children 본문을 평탄화 (plain text fallback)."""
    root = next((x for x in items if x.get("id") == root_id), None)
    if not root:
        return ""
    lines: List[str] = []

    def dump(node: dict, depth: int):
        prefix = "  " * depth
        ts = (node.get("created_at") or "")[:16].replace("T", " ")
        lines.append(f"{prefix}[{ts}] {node.get('author','?')} · {node.get('module','')} / {node.get('reason','')}")
        body = (node.get("text") or "").strip()
        for ln in body.splitlines() or [""]:
            lines.append(f"{prefix}  {ln}")
        kids = sorted(
            [x for x in items if x.get("parent_id") == node.get("id")],
            key=lambda x: x.get("created_at", ""),
        )
        for k in kids:
            dump(k, depth + 1)

    dump(root, 0)
    return "\n".join(lines)


def _thread_html(items: list, root_id: str) -> str:
    """Render the root + its children as a nested HTML block."""
    root = next((x for x in items if x.get("id") == root_id), None)
    if not root:
        return ""

    def esc(s):
        return _html.escape(str(s or ""))

    parts: List[str] = []

    def render(node: dict, depth: int):
        bg = "#fff" if depth == 0 else "#fafafa"
        border = "#f97316" if depth == 0 else "#d1d5db"
        left_pad = 14 + depth * 14
        ts = (node.get("created_at") or "")[:16].replace("T", " ")
        author = esc(node.get("author", "?"))
        module = esc(node.get("module", ""))
        reason = esc(node.get("reason", ""))
        status = esc(node.get("flow_status", ""))
        body_lines = (node.get("text") or "").splitlines()
        body_html = "<br/>".join(esc(ln) for ln in body_lines) or "<i style='color:#999'>(본문 없음)</i>"
        sc = node.get("splittable_change") or None
        sc_block = ""
        if sc and (sc.get("column") or sc.get("new_value")):
            sc_block = (
                "<div style='margin-top:6px;padding:6px 8px;background:#fff7ed;border-left:3px solid #f97316;font-family:monospace;font-size:12px;'>"
                f"▸ <b>{esc(sc.get('column',''))}</b>: "
                f"<span style='color:#6b7280;text-decoration:line-through'>{esc(sc.get('old_value','-'))}</span>"
                f" → <span style='color:#16a34a;font-weight:700'>{esc(sc.get('new_value','-'))}</span>"
                "</div>"
            )
        parts.append(
            f"<div style='margin-left:{left_pad}px;margin-bottom:8px;padding:10px 12px;"
            f"background:{bg};border:1px solid {border};border-left:4px solid {border};"
            f"border-radius:6px;font-family:-apple-system,Segoe UI,Arial,sans-serif;font-size:13px;color:#1f2937;'>"
            f"<div style='font-size:11px;color:#6b7280;margin-bottom:4px;'>"
            f"<b style='color:#1f2937'>{author}</b> · {esc(ts)} · "
            f"<span style='color:#f97316'>{module}</span>"
            + (f" / {reason}" if reason else "")
            + (f" · <span style='padding:1px 6px;border-radius:10px;background:#e0f2fe;color:#0369a1;font-size:10px;'>{status}</span>" if status else "")
            + f"</div>"
            f"<div style='line-height:1.55'>{body_html}</div>"
            f"{sc_block}"
            f"</div>"
        )
        kids = sorted(
            [x for x in items if x.get("parent_id") == node.get("id")],
            key=lambda x: x.get("created_at", ""),
        )
        for k in kids:
            render(k, depth + 1)

    render(root, 0)
    return "\n".join(parts)


def _render_embed_table_html(embed: Optional[dict], max_rows: int = 60) -> str:
    """v8.8.22: 인폼에 붙어있는 SplitTable 스냅샷을 메일 본문 HTML 테이블로 인라인 렌더.
    - st_view (SplitTable /view 응답) 가 있으면 parameter x wafer 매트릭스로 렌더.
    - 없으면 legacy 2D (columns/rows) 를 그대로 렌더.
    max_rows 초과 시 하단에 잘림 표시.
    """
    if not embed or not isinstance(embed, dict):
        return ""
    esc = _html.escape
    source = (embed.get("source") or "").strip()
    note = (embed.get("note") or "").strip()

    # Try st_view first.
    st = embed.get("st_view") or {}
    rows_st = st.get("rows") or []
    headers = st.get("headers") or []
    wafer_fab = st.get("wafer_fab_list") or []

    def _wrap(body_rows_html: str, head_cells: list[str], truncated: bool) -> str:
        th_style = ("border:1px solid #d1d5db;padding:4px 8px;background:#f3f4f6;"
                    "font-size:11px;color:#1f2937;text-align:center;font-family:monospace;")
        td_style_first = ("border:1px solid #d1d5db;padding:4px 8px;background:#f9fafb;"
                          "font-size:11px;font-weight:700;color:#1f2937;font-family:monospace;")
        thead = "<tr>" + "".join(f"<th style='{th_style}'>{c}</th>" for c in head_cells) + "</tr>"
        hdr = (
            f"<div style='margin:12px 0 4px 0;font-size:12px;font-weight:700;color:#ea580c;'>"
            f"🔗 SplitTable 스냅샷"
            + (f" <span style='color:#6b7280;font-weight:500;font-size:11px;'>· {esc(source)}</span>" if source else "")
            + "</div>"
        )
        note_html = f"<div style='font-size:10px;color:#6b7280;margin-bottom:4px;'>{esc(note)}</div>" if note else ""
        trunc_html = (f"<div style='font-size:10px;color:#b91c1c;margin-top:4px;'>"
                      f"⚠ {max_rows}행으로 잘림 — 전체 데이터는 첨부 xlsx 참고</div>") if truncated else ""
        return (
            f"{hdr}{note_html}"
            f"<div style='overflow:auto;'>"
            f"<table style='border-collapse:collapse;font-size:11px;max-width:100%;'>"
            f"<thead>{thead}</thead>"
            f"<tbody>{body_rows_html}</tbody>"
            f"</table></div>{trunc_html}"
        )

    if rows_st and headers:
        head_cells = ["parameter"] + [esc(h or "") for h in headers]
        if wafer_fab:
            head_cells_row2 = ["fab_lot_id"] + [esc(f or "—") for f in wafer_fab]
        else:
            head_cells_row2 = []
        truncated = len(rows_st) > max_rows
        shown = rows_st[:max_rows]
        body_parts = []
        td_first = ("border:1px solid #d1d5db;padding:4px 8px;background:#f9fafb;"
                    "font-size:11px;font-weight:700;font-family:monospace;")
        td_cell = ("border:1px solid #d1d5db;padding:4px 8px;text-align:center;"
                   "font-size:11px;font-family:monospace;")
        for r in shown:
            param = esc(str(r.get("_param", "")))
            cells = r.get("_cells") or {}
            tds = [f"<td style='{td_first}'>{param}</td>"]
            for i in range(len(headers)):
                cell = cells.get(i) or cells.get(str(i)) or {}
                actual = cell.get("actual")
                plan = cell.get("plan")
                disp = "" if actual is None or actual == "" else str(actual)
                if plan is not None and plan != "" and plan != actual:
                    disp = f"{disp} <span style='color:#ea580c;font-weight:700'>→ {esc(str(plan))}</span>"
                else:
                    disp = esc(disp)
                tds.append(f"<td style='{td_cell}'>{disp}</td>")
            body_parts.append("<tr>" + "".join(tds) + "</tr>")
        # Render with two header rows if wafer_fab present.
        if head_cells_row2:
            th_style = ("border:1px solid #d1d5db;padding:4px 8px;background:#f3f4f6;"
                        "font-size:11px;color:#1f2937;text-align:center;font-family:monospace;")
            th_style2 = ("border:1px solid #d1d5db;padding:3px 8px;background:#fafafa;"
                         "font-size:10px;color:#6b7280;text-align:center;font-family:monospace;")
            thead = (
                "<tr>" + "".join(f"<th style='{th_style}'>{c}</th>" for c in head_cells) + "</tr>"
                "<tr>" + "".join(f"<th style='{th_style2}'>{c}</th>" for c in head_cells_row2) + "</tr>"
            )
            hdr = (
                f"<div style='margin:12px 0 4px 0;font-size:12px;font-weight:700;color:#ea580c;'>"
                f"🔗 SplitTable 스냅샷"
                + (f" <span style='color:#6b7280;font-weight:500;font-size:11px;'>· {esc(source)}</span>" if source else "")
                + "</div>"
            )
            note_html = f"<div style='font-size:10px;color:#6b7280;margin-bottom:4px;'>{esc(note)}</div>" if note else ""
            trunc_html = (f"<div style='font-size:10px;color:#b91c1c;margin-top:4px;'>"
                          f"⚠ {max_rows}행으로 잘림 — 전체 데이터는 첨부 xlsx 참고</div>") if truncated else ""
            return (
                f"{hdr}{note_html}"
                f"<div style='overflow:auto;'>"
                f"<table style='border-collapse:collapse;font-size:11px;max-width:100%;'>"
                f"<thead>{thead}</thead>"
                f"<tbody>{''.join(body_parts)}</tbody>"
                f"</table></div>{trunc_html}"
            )
        return _wrap("".join(body_parts), head_cells, truncated)

    # Legacy 2D path.
    cols = embed.get("columns") or []
    rows2d = embed.get("rows") or []
    if not cols and not rows2d:
        return ""
    truncated = len(rows2d) > max_rows
    shown = rows2d[:max_rows]
    td_cell = ("border:1px solid #d1d5db;padding:4px 8px;font-size:11px;"
               "font-family:monospace;")
    body_parts = []
    for r in shown:
        if not isinstance(r, (list, tuple)):
            continue
        tds = "".join(f"<td style='{td_cell}'>{esc('' if v is None else str(v))}</td>" for v in r)
        body_parts.append(f"<tr>{tds}</tr>")
    return _wrap("".join(body_parts), [esc(c or "") for c in cols], truncated)


def _build_html_body(root: dict, thread_html: str, extra_prose: str,
                     sender_username: str = "", product_contacts: Optional[list] = None,
                     embed_table: Optional[dict] = None) -> str:
    """최상위 루트 메타 + 사용자 prose + 스레드 HTML 을 한 문서로.

    v8.8.0: 메일은 Admin 계정으로 발송되더라도 본문에 실제 요청자(sender_username) 를
    명시. 또한 해당 product 의 담당자 그룹(product_contacts) 이 있으면 표로 첨부.
    v8.8.22: embed_table 이 있으면 SplitTable 스냅샷을 HTML 테이블로 본문에 인라인 주입.
    """
    esc = _html.escape
    meta_rows = []
    for k, label in [("module", "모듈"), ("reason", "사유"), ("product", "제품"),
                     ("lot_id", "Lot"), ("wafer_id", "Wafer"), ("deadline", "마감일"),
                     ("flow_status", "진행상태"), ("author", "작성자")]:
        val = root.get(k, "")
        if not val:
            continue
        meta_rows.append(
            f"<tr><td style='padding:4px 10px;font-size:11px;color:#6b7280;background:#f3f4f6;width:90px;'>{esc(label)}</td>"
            f"<td style='padding:4px 10px;font-size:12px;color:#1f2937;font-family:monospace;'>{esc(val)}</td></tr>"
        )
    meta_tbl = "<table style='border-collapse:collapse;border:1px solid #d1d5db;margin:10px 0;width:100%;max-width:560px;'>" + "".join(meta_rows) + "</table>"
    prose_block = ""
    if extra_prose.strip():
        safe = _html.escape(extra_prose).replace("\n", "<br/>")
        prose_block = (
            f"<div style='margin:12px 0;padding:10px 12px;background:#fffbeb;border-left:4px solid #f59e0b;"
            f"border-radius:4px;font-size:13px;color:#78350f;'>{safe}</div>"
        )
    # v8.8.1: 발송 요청자(hol) 자동 명시 제거.
    contacts_block = ""
    if product_contacts:
        names = []
        for c in product_contacts:
            nm = (c.get("name") or "").strip()
            em = (c.get("email") or "").strip()
            if nm and em:
                names.append(f"{esc(nm)} &lt;{esc(em)}&gt;")
            elif nm:
                names.append(esc(nm))
            elif em:
                names.append(esc(em))
        if names:
            contacts_block = (
                f"<div style='margin:10px 0;padding:8px 12px;background:#f0fdf4;border-left:4px solid #16a34a;"
                f"border-radius:4px;font-size:12px;color:#14532d;'>"
                f"<b>제품 담당자</b> : " + ", ".join(names)
                + "</div>"
            )
    embed_html = _render_embed_table_html(embed_table) if embed_table else ""
    return (
        "<div style='font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1f2937;max-width:720px;'>"
        f"<h2 style='font-size:16px;margin:0 0 6px 0;color:#ea580c;'>flow · 인폼 공유</h2>"
        f"<div style='font-size:11px;color:#6b7280;margin-bottom:10px;'>Inform ID <code>{esc(root.get('id',''))}</code></div>"
        f"{meta_tbl}"
        f"{contacts_block}"
        f"{prose_block}"
        f"{embed_html}"
        f"<h3 style='font-size:13px;margin:14px 0 6px 0;color:#374151;'>스레드</h3>"
        f"{thread_html}"
        "<hr style='border:none;border-top:1px solid #e5e7eb;margin:18px 0 8px 0;'/>"
        "<div style='font-size:10px;color:#9ca3af;'>Sent by flow · 자동 전송된 메일입니다.</div>"
        "</div>"
    )


def _resolve_users_to_emails(usernames: List[str]) -> List[str]:
    """v8.8.17: 사내 username 이 곧 사내 email 인 환경 대응.
      우선순위:
        1) users.csv 에 email 필드가 있고 '@' 가 포함되면 그 값.
        2) username 자체가 '@' 를 포함하면 email 로 취급 (가입 시 사내 메일 입력 규약).
        3) 둘 다 아니면 제외 (admin / test 등 시스템 계정 자동 스킵).
    """
    if not usernames:
        return []
    from routers.auth import read_users
    all_users = {u.get("username", ""): u for u in read_users()}
    out = []
    for un in usernames:
        if not un:
            continue
        u = all_users.get(un) or {}
        em = (u.get("email") or "").strip()
        if em and "@" in em:
            out.append(em)
            continue
        # username 자체가 이메일 포맷이면 그대로 사용.
        if "@" in un and "." in un.split("@", 1)[1]:
            out.append(un)
    return out


MAIL_CONTENT_MAX = 2 * 1024 * 1024          # 2 MB HTML body
MAIL_ATTACH_MAX  = 10 * 1024 * 1024         # 10 MB total attachments


# v8.8.21: 인폼 → SplitTable 스냅샷 xlsx 자동 첨부.
#   사용자가 직접 파일 업로드하지 않아도, 해당 인폼에 담긴 제품/lot/wafer 스냅샷을
#   SplitTable 엑셀 내보내기와 동일 형식으로 렌더해 첨부한다.
def _build_inform_snapshot_xlsx(target: dict) -> Optional[tuple]:
    """Return (filename, bytes, mime) or None if no snapshot available."""
    try:
        import io as _io
        try:
            from openpyxl import Workbook
        except Exception:
            return None
        wb = Workbook()
        ws = wb.active
        ws.title = "Inform Snapshot"
        ws.append(["Field", "Value"])
        meta_keys = ("id", "product", "module", "reason", "root_lot_id", "lot_id",
                     "wafer_id", "fab_lot_id_at_save", "flow_status", "author",
                     "created_at", "updated_at")
        for k in meta_keys:
            v = target.get(k, "")
            if v not in ("", None):
                ws.append([k, str(v)])
        sc = target.get("splittable_change") or {}
        if sc:
            ws.append([])
            ws.append(["SplitTable Change", ""])
            for k in ("column", "old_value", "new_value", "applied"):
                if k in sc:
                    ws.append([k, str(sc.get(k, ""))])
        # body text 도 같이 싣기 (읽기 편하게).
        body = (target.get("text") or "").strip()
        if body:
            ws.append([])
            ws.append(["Body", ""])
            for ln in body.splitlines():
                ws.append(["", ln])
        buf = _io.BytesIO()
        wb.save(buf)
        data = buf.getvalue()
        if not data:
            return None
        fn = f"inform_{target.get('id','snapshot')}.xlsx"
        return (fn, data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception:
        return None


@router.get("/{inform_id}/mail-preview")
def mail_preview(inform_id: str, request: Request, body: str = Query("")):
    """v8.8.21: FE MailDialog 용 실시간 프리뷰.
    실제로 send-mail 이 호출할 HTML body / 수신자 그룹 해석 / 담당자 라인 / 자동 첨부 목록을 반환.
    """
    _ = current_user(request)
    items = _load_upgraded()
    target = _find(items, inform_id)
    if not target:
        raise HTTPException(404)
    pc_data = _load_product_contacts()
    pc_list = (pc_data.get("products") or {}).get(target.get("product", ""), []) or []
    thread_html = _thread_html(items, inform_id)
    html = _build_html_body(target, thread_html, body or "", product_contacts=pc_list,
                             embed_table=target.get("embed_table"))
    snap = _build_inform_snapshot_xlsx(target)
    owners_line = ", ".join([(c.get("name") or c.get("email") or "").strip()
                             for c in pc_list if c.get("name") or c.get("email")])
    return {
        "inform_id": inform_id,
        "product": target.get("product", ""),
        "owners_line": owners_line,
        "product_contacts": pc_list,
        "html_body": html,
        "auto_attachments": [{"name": snap[0], "bytes": len(snap[1])}] if snap else [],
    }


def _resolve_inform_attachment(url: str) -> Optional[Path]:
    """Map /api/informs/files/{uid}/{name} → local UPLOADS_DIR/{uid}/{name}."""
    if not url:
        return None
    m = re.match(r"^/?api/informs/files/([A-Za-z0-9_\-]+)/([^/\\?#]+)", url)
    if not m:
        return None
    uid, name = m.group(1), m.group(2)
    if ".." in name or "/" in name or "\\" in name:
        return None
    fp = UPLOADS_DIR / uid / name
    try:
        fp_res = fp.resolve()
        root_res = UPLOADS_DIR.resolve()
        fp_res.relative_to(root_res)  # traversal guard
    except Exception:
        return None
    return fp if fp.is_file() else None


def _encode_multipart(fields: Dict[str, str], files: List[tuple]) -> tuple:
    """Encode form fields + files as multipart/form-data.
    fields: {name: string_value}
    files:  [(field_name, filename, bytes, mime)]
    Returns (body_bytes, content_type_header).
    """
    boundary = "----flowInform" + uuid.uuid4().hex
    chunks: List[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n'.encode())
        chunks.append(b"Content-Type: text/plain; charset=utf-8\r\n\r\n")
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for (fname_field, filename, content, mime) in files:
        chunks.append(f"--{boundary}\r\n".encode())
        safe_fn = filename.replace('"', '').replace("\r", "").replace("\n", "")
        chunks.append(
            f'Content-Disposition: form-data; name="{fname_field}"; filename="{safe_fn}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


@router.post("/{inform_id}/send-mail")
def send_mail(inform_id: str, req: SendMailReq, request: Request):
    """인폼 HTML 본문 + 선택 수신자로 사내 메일 API 호출.

    수신자 확정 순서:
      1) req.to (이메일 직접 지정)
      2) req.to_users (username → email 매핑)
      3) req.groups → admin 설정 recipient_groups[group] (username 리스트) → email

    Admin 설정의 mail.api_url/headers/from_addr/extra_data 를 사용. enabled=False
    이거나 api_url 이 비어있으면 400. api_url=='dry-run' 이면 실제 전송 없이 payload
    를 그대로 반환 (구성 검증용).
    """
    me = current_user(request)
    cfg = _load_mail_cfg()
    if not cfg.get("enabled") or not (cfg.get("api_url") or "").strip():
        raise HTTPException(400, "메일 API 가 설정되지 않았습니다. Admin > 메일 API 에서 활성화하세요.")

    items = _load_upgraded()
    target = _find(items, inform_id)
    if not target:
        raise HTTPException(404, "인폼을 찾을 수 없습니다.")

    # Resolve recipients. Admin-side groups store emails directly; to_users is
    # a convenience path that looks up emails from users.csv.
    to_addrs: List[str] = []
    seen_addrs: set = set()

    def _push(em: str):
        em = (em or "").strip()
        if not em or "@" not in em:
            return
        if em in seen_addrs:
            return
        seen_addrs.add(em)
        to_addrs.append(em)

    for a in (req.to or []):
        _push(a)
    for em in _resolve_users_to_emails(list(req.to_users or [])):
        _push(em)
    rg_cfg = cfg.get("recipient_groups") or {}
    for gname in (req.groups or []):
        members = rg_cfg.get(gname) if isinstance(rg_cfg, dict) else None
        if isinstance(members, list):
            for em in members:
                _push(str(em))

    if not to_addrs:
        raise HTTPException(400, "수신자 이메일이 없습니다 (유저 email 또는 group 을 먼저 설정하세요).")
    if len(to_addrs) > 199:
        raise HTTPException(400, f"수신자는 최대 199명까지 지정할 수 있습니다 (현재 {len(to_addrs)}명).")
    # receiverList object form per mail API spec.
    receiver_list = [{"email": em, "recipientType": "To", "seq": i + 1}
                     for i, em in enumerate(to_addrs)]
    to_list = to_addrs  # kept for audit (plain list of emails)

    subject = (req.subject or "").strip() or f"[flow 인폼] {target.get('module','')} · {target.get('lot_id') or target.get('wafer_id') or ''}".strip()
    # HTML body (content)
    thread_html = _thread_html(items, inform_id) if req.include_thread else ""
    # v8.8.0: sender = 실제 요청 유저(me), 제품 담당자 자동 첨부.
    pc_data = _load_product_contacts()
    pc_list = (pc_data.get("products") or {}).get(target.get("product", ""), []) or []
    html_body = _build_html_body(target, thread_html, (req.body or ""),
                                 sender_username=me.get("username", ""),
                                 product_contacts=pc_list,
                                 embed_table=target.get("embed_table"))
    content_bytes_len = len(html_body.encode("utf-8"))
    if content_bytes_len > MAIL_CONTENT_MAX:
        raise HTTPException(400, f"메일 본문이 2MB 한도를 초과했습니다 ({content_bytes_len // 1024}KB). 스레드 첨부를 끄거나 본문을 줄여주세요.")

    # Collect attachments (optional)
    attach_files: List[tuple] = []
    attach_total = 0
    # v8.8.21: 인폼 스냅샷 xlsx 자동 첨부 — FE 에서 별도 업로드 불필요.
    snap = _build_inform_snapshot_xlsx(target)
    if snap:
        _fn, _bytes, _mime = snap
        attach_total += len(_bytes)
        if attach_total <= MAIL_ATTACH_MAX:
            attach_files.append(("files", _fn, _bytes, _mime))
    for url_ in (req.attachments or []):
        fp = _resolve_inform_attachment(url_)
        if not fp:
            continue
        content = fp.read_bytes()
        attach_total += len(content)
        if attach_total > MAIL_ATTACH_MAX:
            raise HTTPException(400, f"첨부파일 총 용량이 10MB 한도를 초과했습니다 ({attach_total // 1024}KB).")
        mime = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        attach_files.append(("files", fp.name, content, mime))

    # Build `data` object per spec.
    # v8.8.21: senderMailAddress(camelCase) 를 표준으로 쓰고 legacy 소문자 변형도 함께 주입.
    _sender_addr = (cfg.get("from_addr") or "").strip()
    data_obj: Dict[str, Any] = {
        "content":           html_body,
        "receiverList":      receiver_list,
        "senderMailAddress": _sender_addr,
        "senderMailaddress": _sender_addr,
        "statusCode":        (req.status_code or cfg.get("status_code") or "").strip(),
        "title":             subject,
    }
    # Merge admin extra_data without clobbering reserved keys.
    extra = cfg.get("extra_data") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k and k not in data_obj:
                data_obj[k] = v

    headers = {}
    cfg_headers = cfg.get("headers") or {}
    if isinstance(cfg_headers, dict):
        for k, v in cfg_headers.items():
            if k:
                headers[str(k)] = str(v)

    url = cfg.get("api_url").strip()
    dry_run = url.lower() == "dry-run"
    # v8.8.24: 사내 메일 API 규약 정합 — `mailSendString` 은 multipart 의 top-level
    #   form field 로 직접 전송 (값은 data_obj 를 JSON 직렬화한 문자열). 이전
    #   v8.8.21~v8.8.23 의 "data" → JSON({mailSendString:...}) 이중 래핑 구조 교정.
    mail_send_string = _json.dumps(data_obj, ensure_ascii=False)
    wrapped = {"mailSendString": mail_send_string}
    if dry_run:
        result_info = {
            "status": 200, "dry_run": True,
            "preview_data": data_obj,
            "preview_data_wrapped": wrapped,
            "preview_attachments": [{"name": f[1], "bytes": len(f[2])} for f in attach_files],
            "preview_headers": headers,
        }
    else:
        # multipart/form-data: "mailSendString" (JSON string) + "files" parts.
        fields = {"mailSendString": mail_send_string}
        body_bytes, content_type = _encode_multipart(fields, attach_files)
        hdrs_out = dict(headers)
        hdrs_out["Content-Type"] = content_type
        try:
            r = urllib.request.Request(url, data=body_bytes, headers=hdrs_out, method="POST")
            with urllib.request.urlopen(r, timeout=15) as resp:
                status = resp.status
                text = resp.read(2048).decode("utf-8", errors="replace")
            result_info = {"status": status, "response": text[:512]}
        except urllib.error.HTTPError as e:
            detail_text = ""
            try:
                detail_text = e.read(512).decode("utf-8", errors="replace")
            except Exception:
                pass
            _audit(request, "inform:mail-fail", detail=f"id={inform_id} http={e.code}", tab="inform")
            raise HTTPException(502, f"메일 API 오류: HTTP {e.code} {detail_text[:200]}")
        except Exception as e:
            _audit(request, "inform:mail-fail", detail=f"id={inform_id} err={e}", tab="inform")
            raise HTTPException(502, f"메일 전송 실패: {e}")

    # Best-effort audit log on the inform itself.
    hist = target.get("mail_history") or []
    hist.append({
        "at": _now(),
        "by": me.get("username", ""),
        "to": to_list,
        "to_users": list(req.to_users or []),
        "groups": list(req.groups or []),
        "subject": subject,
    })
    target["mail_history"] = hist[-20:]  # keep last 20
    _save(items)
    _audit(request, "inform:mail-send", detail=f"id={inform_id} n_to={len(to_list)} dry={dry_run}", tab="inform")
    return {"ok": True, "to": to_list, "subject": subject, **result_info}


@router.post("/splittable")
def attach_splittable(req: SplitChange, request: Request, id: str = Query(...)):
    """해당 인폼에 SplitTable 변경요청 메타 attach (작성자/담당자/admin)."""
    me = current_user(request)
    my_mods = _effective_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403)
    target["splittable_change"] = {
        "column": (req.column or "").strip(),
        "old_value": (req.old_value or "").strip(),
        "new_value": (req.new_value or "").strip(),
        "applied": bool(req.applied),
    }
    _save(items)
    return {"ok": True, "inform": target}
