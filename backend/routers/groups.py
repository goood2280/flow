"""routers/groups.py v8.8.3 — User groups for Dashboard/Tracker visibility + LOT watch + module 담당.

스키마 ({data_root}/groups/groups.json):
  [{id, name, description, owner, members:[username], watched_lots:[lot_id],
    modules:[module_name], created, updated}]

v8.8.3 변경:
  - description(optional str) 필드 추가: 그룹 목적 자유 텍스트.
  - 기존 레코드는 description 없어도 옵셔널 처리.

v8.7.0 추가:
  - modules: 이 그룹이 담당하는 공정 모듈 (GATE/STI/PC/MOL/BEOL/ET/EDS/...).
  - user_modules(username, role): 해당 유저가 담당하는 모듈 set. 인폼 모듈별 필터용.

v8.8.1 정책 변경:
  - 일반 유저도 그룹 생성·편집 가능 (admin 전용 기능 아님).
  - 생성자가 반드시 members 에 포함될 필요 없음 (owner 만 기록).
  - admin 계정 및 "test" 가 포함된 username 은 members 대상에서 자동 제외
    (admin 은 사내 이메일이 없어 메일 발송 대상이 될 수 없고, test 계정은 가상).

규약:
  - admin 은 모든 그룹 조회/수정 가능.
  - 일반 유저는 자기가 owner 이거나 member 인 그룹만 조회·LOT watch 편집 가능.
  - 생성·삭제·멤버 편집은 owner 또는 admin.
  - 감사 로그: groups_audit.jsonl (actor, action, group_id, timestamp, detail).

가시성 필터 헬퍼 (다른 라우터가 import):
  filter_by_visibility(items, username, role, key="group_ids")
    - admin 은 모두 통과.
    - item 에 group_ids 가 비어있으면 public → 통과.
    - group_ids 가 있으면 유저가 최소 1개 그룹의 member 여야 통과.
"""
import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json, jsonl_append
from core.auth import current_user, require_admin

router = APIRouter(prefix="/api/groups", tags=["groups"])


# v8.8.1/v8.8.5: admin 필터 해제 — admin 도 그룹 멤버로 추가 가능 (사내 이메일 있는 경우 多).
#   v8.8.1 에선 "admin 은 사내 이메일이 없어 메일 발송 대상 아님" 가정으로 제외했으나,
#   실사내 admin 계정은 정상 이메일 보유 → 배제하면 안 됨. test substring 만 block.
def _is_blocked_member(username: str, users_by_name: dict | None = None) -> bool:
    un = (username or "").strip()
    if not un:
        return True
    if "test" in un.lower():
        return True
    return False


def _load_users_by_name() -> dict:
    try:
        from routers.auth import read_users
        return {u.get("username", ""): u for u in read_users() if u.get("username")}
    except Exception:
        return {}


def _sanitize_members(raw, users_by_name: dict | None = None) -> list:
    if users_by_name is None:
        users_by_name = _load_users_by_name()
    out = []
    seen = set()
    for m in (raw or []):
        s = str(m).strip()
        if not s or s in seen:
            continue
        if _is_blocked_member(s, users_by_name):
            continue
        seen.add(s)
        out.append(s)
    return out

GROUPS_DIR = PATHS.data_root / "groups"
GROUPS_DIR.mkdir(parents=True, exist_ok=True)
GROUPS_FILE = GROUPS_DIR / "groups.json"
AUDIT_FILE = GROUPS_DIR / "groups_audit.jsonl"


def _clean_emails_for_group(raw) -> list:
    """v8.8.23: 메일 그룹 통합 — email 정규화 헬퍼 (mail_groups.py 에서 가져옴)."""
    if not isinstance(raw, (list, tuple, set)):
        return []
    out = []
    seen = set()
    for e in raw:
        s = str(e).strip()
        if s and "@" in s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


_MIGRATION_DONE = False


def _migrate_legacy_mail_groups(groups: list) -> list:
    """v8.8.23: 레거시 `mail_groups.json` + admin_settings.json:recipient_groups 를
    groups.json 으로 일회성 병합. 이름 기준 match — 기존 그룹이 있으면 extra_emails 만
    보강, 없으면 새 그룹으로 추가 (owner="system").
    마이그레이션 완료 파일은 `.migrated` suffix 로 이름 변경해 두번 돌지 않게.
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return groups
    _MIGRATION_DONE = True
    changed = False
    # 1) mail_groups.json
    legacy_fp = PATHS.data_root / "mail_groups.json"
    if legacy_fp.exists():
        try:
            legacy = load_json(legacy_fp, [])
            if isinstance(legacy, list):
                by_name = {(g.get("name") or "").strip().lower(): g for g in groups}
                for mg in legacy:
                    if not isinstance(mg, dict):
                        continue
                    nm = (mg.get("name") or "").strip()
                    if not nm:
                        continue
                    key = nm.lower()
                    mg_members = _sanitize_members(mg.get("members") or [])
                    mg_extras = _clean_emails_for_group(mg.get("extra_emails") or [])
                    mg_note = (mg.get("note") or "").strip() or None
                    if key in by_name:
                        tgt = by_name[key]
                        # extra_emails 병합 (dedupe)
                        cur_ext = _clean_emails_for_group(tgt.get("extra_emails") or [])
                        for e in mg_extras:
                            if e not in cur_ext:
                                cur_ext.append(e)
                        if cur_ext != (tgt.get("extra_emails") or []):
                            tgt["extra_emails"] = cur_ext
                            changed = True
                        # members 병합
                        cur_m = set(tgt.get("members") or [])
                        for m in mg_members:
                            if m not in cur_m:
                                cur_m.add(m)
                                changed = True
                        tgt["members"] = sorted(cur_m)
                    else:
                        gid = mg.get("id") or f"grp_mig_{uuid.uuid4().hex[:8]}"
                        groups.append({
                            "id": gid,
                            "name": nm,
                            "description": mg_note,
                            "owner": mg.get("created_by") or "system",
                            "members": sorted(set(mg_members)),
                            "watched_lots": [],
                            "modules": [],
                            "extra_emails": mg_extras,
                            "created": mg.get("created") or datetime.datetime.now().isoformat(timespec="seconds"),
                            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                        })
                        by_name[key] = groups[-1]
                        changed = True
            # 이름 변경 — 두번 안 돌게.
            try:
                legacy_fp.rename(legacy_fp.with_suffix(".json.migrated"))
            except Exception:
                pass
        except Exception:
            pass
    # 2) admin_settings.json:recipient_groups (dict: name → [usernames])
    try:
        adm_fp = PATHS.data_root / "admin_settings.json"
        if adm_fp.exists():
            adm = load_json(adm_fp, {})
            rg = (adm.get("mail") or {}).get("recipient_groups") or adm.get("recipient_groups") or {}
            if isinstance(rg, dict) and rg:
                by_name = {(g.get("name") or "").strip().lower(): g for g in groups}
                for nm, ulist in rg.items():
                    nm = (nm or "").strip()
                    if not nm:
                        continue
                    key = nm.lower()
                    members = _sanitize_members([u for u in (ulist or []) if isinstance(u, str)])
                    if key in by_name:
                        tgt = by_name[key]
                        cur_m = set(tgt.get("members") or [])
                        for m in members:
                            if m not in cur_m:
                                cur_m.add(m); changed = True
                        tgt["members"] = sorted(cur_m)
                    else:
                        gid = f"grp_mig_{uuid.uuid4().hex[:8]}"
                        groups.append({
                            "id": gid,
                            "name": nm,
                            "description": None,
                            "owner": "system",
                            "members": sorted(set(members)),
                            "watched_lots": [],
                            "modules": [],
                            "extra_emails": [],
                            "created": datetime.datetime.now().isoformat(timespec="seconds"),
                            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                        })
                        by_name[key] = groups[-1]
                        changed = True
    except Exception:
        pass
    return groups if changed else groups  # caller will save if needed


def _load() -> list:
    data = load_json(GROUPS_FILE, [])
    if not isinstance(data, list):
        return []
    # v8.8.23: 정규화 — extra_emails 필드 보장.
    for g in data:
        if isinstance(g, dict):
            g.setdefault("extra_emails", [])
    # 최초 로드 시 레거시 병합 시도.
    global _MIGRATION_DONE
    if not _MIGRATION_DONE:
        before = [dict(x) for x in data]
        migrated = _migrate_legacy_mail_groups(data)
        # 변경 여부 단순 검사 — 리스트 길이 or 대표 필드 비교.
        if len(migrated) != len(before) or any(
            (a.get("extra_emails") or []) != (b.get("extra_emails") or []) or
            (a.get("members") or []) != (b.get("members") or [])
            for a, b in zip(migrated, before)
        ):
            try:
                save_json(GROUPS_FILE, migrated, indent=2)
            except Exception:
                pass
        data = migrated
    return data


def _save(groups: list) -> None:
    save_json(GROUPS_FILE, groups, indent=2)


def _audit(actor: str, action: str, group_id: str, detail: str = "") -> None:
    jsonl_append(
        AUDIT_FILE,
        {
            "actor": actor,
            "action": action,
            "group_id": group_id,
            "detail": detail,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def _find(groups: list, gid: str) -> Optional[dict]:
    return next((g for g in groups if g.get("id") == gid), None)


def _can_view(g: dict, username: str, role: str) -> bool:
    if role == "admin":
        return True
    if g.get("owner") == username:
        return True
    return username in (g.get("members") or [])


def _can_edit(g: dict, username: str, role: str) -> bool:
    if role == "admin":
        return True
    return g.get("owner") == username


# ── Visibility filter (다른 라우터가 import) ─────────────────────────
def user_group_ids(username: str, role: str) -> set:
    """해당 유저가 속한 group id set. admin 은 *모두 포함* 간주 → 전역 통과."""
    if role == "admin":
        return {"__admin__"}
    groups = _load()
    return {
        g.get("id", "")
        for g in groups
        if g.get("owner") == username or username in (g.get("members") or [])
    }


def user_modules(username: str, role: str) -> set:
    """해당 유저가 담당하는 공정 모듈 set. admin 은 sentinel '__all__' 반환 (전체 담당)."""
    if role == "admin":
        return {"__all__"}
    groups = _load()
    mods: set = set()
    for g in groups:
        if g.get("owner") == username or username in (g.get("members") or []):
            for m in (g.get("modules") or []):
                if m:
                    mods.add(m)
    return mods


def filter_by_visibility(items: list, username: str, role: str, key: str = "group_ids") -> list:
    """item.group_ids 가 비어있으면 public (통과). 값이 있으면 유저 그룹과 교집합 필요.
    admin 은 항상 전부 통과."""
    if role == "admin":
        return items
    my = user_group_ids(username, role)
    out = []
    for it in items:
        gids = it.get(key) or []
        if not gids:
            out.append(it)
            continue
        if any(g in my for g in gids):
            out.append(it)
    return out


# ── Pydantic ────────────────────────────────────────────────────────
class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    members: List[str] = []
    watched_lots: List[str] = []
    modules: List[str] = []
    # v8.8.23: 메일 그룹 통합 — 외부 고정 수신자(email) 리스트를 그룹 레코드에 보관.
    extra_emails: List[str] = []


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    members: Optional[List[str]] = None
    watched_lots: Optional[List[str]] = None
    modules: Optional[List[str]] = None
    extra_emails: Optional[List[str]] = None  # v8.8.23


class ModulesReq(BaseModel):
    modules: List[str]


class MemberReq(BaseModel):
    username: str


class LotReq(BaseModel):
    lot_id: str


# ── Endpoints ───────────────────────────────────────────────────────
@router.get("/list")
def list_groups(request: Request):
    """내가 속한(또는 owner) 그룹 목록. admin 은 전체."""
    me = current_user(request)
    groups = _load()
    if me.get("role") == "admin":
        return {"groups": groups}
    vis = [g for g in groups if _can_view(g, me["username"], me.get("role", "user"))]
    return {"groups": vis}


@router.get("/mine")
def my_group_ids(request: Request):
    """내가 속한 그룹 id 배열. Dashboard/Tracker visibility UI 용."""
    me = current_user(request)
    if me.get("role") == "admin":
        # admin 은 모든 그룹을 선택 가능
        return {"group_ids": [g.get("id") for g in _load()], "admin": True}
    groups = _load()
    mine = [g for g in groups if _can_view(g, me["username"], me.get("role", "user"))]
    return {"group_ids": [g.get("id") for g in mine], "admin": False}


@router.post("/create")
def create_group(req: GroupCreate, request: Request):
    me = current_user(request)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    groups = _load()
    if any(g.get("name") == name for g in groups):
        raise HTTPException(409, "group name already exists")
    gid = f"grp_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    # v8.8.1: 생성자 자동 포함 X — 요청된 멤버만 수용. admin/test 필터.
    members = sorted(set(_sanitize_members(req.members or [])))
    g = {
        "id": gid,
        "name": name,
        "description": (req.description or "").strip() or None,
        "owner": me["username"],
        "members": members,
        "watched_lots": sorted(set(req.watched_lots or [])),
        "modules": sorted(set(req.modules or [])),
        # v8.8.23: 메일 통합 — extra_emails (외부 고정 수신자).
        "extra_emails": _clean_emails_for_group(req.extra_emails or []),
        "created": now,
        "updated": now,
    }
    groups.append(g)
    _save(groups)
    _audit(me["username"], "create", gid, name)
    return {"ok": True, "group": g}


@router.post("/update")
def update_group(req: GroupUpdate, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403, "Only owner or admin can edit")
    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "name empty")
        if any(x.get("name") == name and x.get("id") != id for x in groups):
            raise HTTPException(409, "group name already exists")
        g["name"] = name
    if req.description is not None:
        g["description"] = req.description.strip() or None
    if req.members is not None:
        # v8.8.1: owner 자동 포함 X. admin/test 필터.
        g["members"] = sorted(set(_sanitize_members(req.members)))
    if req.watched_lots is not None:
        g["watched_lots"] = sorted(set(req.watched_lots))
    if req.modules is not None:
        g["modules"] = sorted({m.strip() for m in req.modules if m and m.strip()})
    # v8.8.23: extra_emails 편집 지원.
    if req.extra_emails is not None:
        g["extra_emails"] = _clean_emails_for_group(req.extra_emails)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "update", id, g.get("name", ""))
    return {"ok": True, "group": g}


@router.post("/modules/set")
def set_modules(req: ModulesReq, request: Request, id: str = Query(...)):
    """그룹 담당 모듈 일괄 설정 (owner/admin)."""
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    g["modules"] = sorted({m.strip() for m in (req.modules or []) if m and m.strip()})
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "modules_set", id, ",".join(g["modules"]))
    return {"ok": True, "modules": g["modules"]}


@router.get("/my-modules")
def get_my_modules(request: Request):
    """현재 유저가 담당하는 모듈 list. admin 은 '__all__' sentinel."""
    me = current_user(request)
    mods = user_modules(me["username"], me.get("role", "user"))
    all_rounder = "__all__" in mods or me.get("role") == "admin"
    return {
        "modules": [] if all_rounder else sorted(mods),
        "all_rounder": all_rounder,
    }


@router.post("/delete")
def delete_group(request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403, "Only owner or admin can delete")
    groups = [x for x in groups if x.get("id") != id]
    _save(groups)
    _audit(me["username"], "delete", id, g.get("name", ""))
    return {"ok": True}


@router.post("/members/add")
def add_member(req: MemberReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    users_by_name = _load_users_by_name()
    if _is_blocked_member(req.username, users_by_name):
        raise HTTPException(400, "admin/test 계정은 멤버로 추가할 수 없습니다.")
    members = set(g.get("members") or [])
    members.add(req.username)
    g["members"] = sorted(_sanitize_members(members, users_by_name))
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "member_add", id, req.username)
    return {"ok": True, "members": g["members"]}


@router.post("/members/remove")
def remove_member(req: MemberReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    # v8.8.1: owner 자동 포함 정책 제거. 멤버는 자유롭게 제거 가능.
    g["members"] = sorted([m for m in (g.get("members") or []) if m != req.username])
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "member_remove", id, req.username)
    return {"ok": True, "members": g["members"]}


@router.post("/lots/add")
def add_lot(req: LotReq, request: Request, id: str = Query(...)):
    """그룹의 관심 LOT_WF 목록에 추가 (member 도 가능 — 공유 와치리스트)."""
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_view(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    lots = set(g.get("watched_lots") or [])
    lots.add(req.lot_id)
    g["watched_lots"] = sorted(lots)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "lot_add", id, req.lot_id)
    return {"ok": True, "watched_lots": g["watched_lots"]}


@router.post("/lots/remove")
def remove_lot(req: LotReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_view(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    g["watched_lots"] = sorted([x for x in (g.get("watched_lots") or []) if x != req.lot_id])
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "lot_remove", id, req.lot_id)
    return {"ok": True, "watched_lots": g["watched_lots"]}


@router.get("/eligible-users")
def eligible_users(request: Request):
    """v8.8.1: 그룹 멤버로 추가 가능한 username 목록.
    admin 계정과 "test" 가 포함된 계정은 제외 (메일 발송 대상 아님).
    로그인 유저 누구나 조회 가능 (GroupsPanel FE 용)."""
    _me = current_user(request)
    users_by_name = _load_users_by_name()
    out = []
    for un, u in users_by_name.items():
        if _is_blocked_member(un, users_by_name):
            continue
        out.append({
            "username": un,
            "email": u.get("email", "") if isinstance(u, dict) else "",
            "role": u.get("role", "user") if isinstance(u, dict) else "user",
            # v8.8.27: 이름(실명) 라벨. FE 가 `{name} ({username})` 로 표시.
            "name": (u.get("name", "") if isinstance(u, dict) else "").strip(),
        })
    out.sort(key=lambda x: ((x.get("name") or "").lower(), x["username"].lower()))
    return {"users": out}


@router.get("/audit")
def audit_log(limit: int = 200, _admin=Depends(require_admin)):
    from core.utils import jsonl_read
    return {"entries": jsonl_read(AUDIT_FILE, limit)}
