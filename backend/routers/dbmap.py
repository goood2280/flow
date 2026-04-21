"""routers/dbmap.py v8.4.4 - Table Map: tables, groups, DB refs, relations, AWS
v8.1.8 additions:
  - _write_db_csv(): persist table rows as single CSV in DB root
  - save_table: also writes/renames/cleans up CSV; returns csv_path
  - delete_table: removes CSV from DB root too (archive keeps JSON)
v8.4.4 change:
  - CSV 저장 위치를 Base 루트로 변경 (PATHS.base_root/<name>.csv). Dashboard
    데이터 소스 드롭다운이 base_file 로 자동 인식. TableMap/파일탐색기 Base 탭에서
    바로 확인 가능.
  - display_name 필드 지원 — UI 표시 전용, 물리 파일명과 분리 저장.
"""
import datetime  # v8.4.6: subprocess/shlex 제거 — aws_cmd RCE 경로 차단
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from core.paths import PATHS
from core.utils import detect_structure, load_json, save_json, safe_id

router = APIRouter(prefix="/api/dbmap", tags=["dbmap"])
DB_BASE = PATHS.db_root
TABLE_CSV_ROOT = PATHS.base_root  # v8.4.4: TableMap 이 만드는 CSV 는 Base 루트로
DBMAP_DIR = PATHS.data_root / "dbmap"
CONFIG_FILE = DBMAP_DIR / "config.json"
TABLES_DIR = DBMAP_DIR / "tables"
GROUPS_DIR = DBMAP_DIR / "groups"
VERSIONS_DIR = DBMAP_DIR / "versions"
ARCHIVE_DIR = DBMAP_DIR / "archive"
for d in (DBMAP_DIR, TABLES_DIR, GROUPS_DIR, VERSIONS_DIR, ARCHIVE_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _load_config():
    return load_json(CONFIG_FILE, {"nodes": [], "relations": []})


def _save_config(cfg):
    save_json(CONFIG_FILE, cfg, indent=2)


def _stamp(prefix: str):
    return f"{prefix}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"


def _csv_filename(name: str, tbl_id: str) -> str:
    """Sanitize table name for DB-root CSV filename."""
    base = safe_id(name) if name else tbl_id
    return f"{base or tbl_id}.csv"


def _write_db_csv(tbl_id: str, name: str, rows, columns) -> str:
    """Persist table rows as single CSV in DB root (PATHS.db_root/<name>.csv).
    - Returns path written (or empty string if skipped/failed).
    - Overwrites existing file with same name.
    - Empty rows/columns -> header-only file.
    """
    try:
        TABLE_CSV_ROOT.mkdir(parents=True, exist_ok=True)
        cols = [c.get("name", "") for c in (columns or []) if c.get("name")]
        if not cols:
            return "CSV write skipped: no named columns"
        fname = _csv_filename(name, tbl_id)
        dest = TABLE_CSV_ROOT / fname

        def esc(v) -> str:
            s = "" if v is None else str(v)
            if any(ch in s for ch in (",", '"', "\n", "\r")):
                s = '"' + s.replace('"', '""') + '"'
            return s

        lines = [",".join(esc(c) for c in cols)]
        for r in (rows or []):
            lines.append(",".join(esc(r.get(c, "")) for c in cols))
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(dest)
    except Exception as e:
        return f"CSV write error: {e}"


def _delete_db_csv(name: str, tbl_id: str):
    """Remove TableMap CSV if exists (best effort, no error).
    v8.4.4: 저장 위치가 Base 루트로 변경됨. 하위호환으로 DB_BASE 도 제거 시도.
    """
    try:
        fname = _csv_filename(name, tbl_id)
        for root in (TABLE_CSV_ROOT, DB_BASE):
            fp = root / fname
            if fp.is_file():
                fp.unlink()
    except Exception:
        pass


@router.get("/config")
def get_config():
    return _load_config()


@router.get("/db-sources")
def list_db_sources():
    """DB sources for pin references: root parquets + hive/flat products."""
    sources = []
    if not DB_BASE.exists():
        return {"sources": sources}
    for f in sorted(DB_BASE.iterdir()):
        if f.is_file() and f.suffix == ".parquet":
            sources.append({
                "kind": "db_ref", "source_type": "root_parquet",
                "name": f.stem, "path": f.name,
                "label": f"📊 {f.name} (root)",
            })
    for d in sorted(DB_BASE.iterdir()):
        if not d.is_dir():
            continue
        for prod in sorted(d.iterdir()):
            if not prod.is_dir():
                continue
            st = detect_structure(prod)
            if st in ("hive", "flat"):
                icon = "🗄️" if st == "hive" else "📁"
                sources.append({
                    "kind": "db_ref", "source_type": st,
                    "name": f"{d.name}/{prod.name}",
                    "root": d.name, "product": prod.name,
                    "label": f"{icon} {d.name}/{prod.name} ({st})",
                })
    return {"sources": sources}


# ── Table Groups ──
class TableGroupReq(BaseModel):
    id: str = ""
    name: str
    columns: List[dict]
    description: str = ""
    tables: List[str] = []


@router.post("/groups/save")
def save_group(req: TableGroupReq):
    sid = safe_id(req.id) if req.id else _stamp("grp")
    fp = GROUPS_DIR / f"{sid}.json"
    data = req.dict()
    data["id"] = sid
    data["updated"] = datetime.datetime.now().isoformat()
    save_json(fp, data, indent=2)

    cfg = _load_config()
    existing = next((n for n in cfg["nodes"] if n.get("id") == sid), None)
    if existing:
        existing.update({"kind": "group", "name": req.name, "ref_id": sid})
    else:
        cfg["nodes"].append({
            "id": sid, "kind": "group", "name": req.name, "ref_id": sid,
            "x": 100 + len(cfg["nodes"]) * 40,
            "y": 100 + len(cfg["nodes"]) * 40,
        })
    _save_config(cfg)
    return {"ok": True, "id": sid}


# ── Tables ──
class TableReq(BaseModel):
    id: str = ""
    name: str
    display_name: str = ""  # v8.4.4: UI 표시 전용. 비면 name 사용.
    group_id: str = ""
    table_type: str = "data"  # "data" | "matching" | "rulebook"
    columns: List[dict] = []
    rows: List[dict] = []
    description: str = ""
    aws_cmd: str = ""
    username: str = ""


def _run_aws_sync(tbl_id: str, rows, columns, aws_cmd: str) -> str:
    """v8.4.6 보안 패치: 사용자 입력 임의 명령 실행(RCE) 제거.

    기존 구현은 TableReq.aws_cmd (사용자 문자열) 을 shlex.split 후 subprocess.run 에
    그대로 넘겨 임의 명령 실행이 가능했음. S3 동기화는 `routers/s3_ingest.py` 의
    whitelist 된 aws CLI 플로우로 이관됨. 이 함수는 no-op 으로 유지해 기존
    호출부는 그대로 동작하게 함.
    """
    if aws_cmd and aws_cmd.strip():
        return "aws_cmd is disabled in v8.4.6 — use FileBrowser → S3 Sync."
    return ""


VERSION_CAP = 30  # v8.4.4: per-table rolling version history cap

def _cap_versions(vdir):
    """v8.4.4 — keep only last VERSION_CAP version snapshots."""
    try:
        files = sorted(vdir.glob("v*.json"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - VERSION_CAP
        if excess > 0:
            for f in files[:excess]:
                try: f.unlink()
                except Exception: pass
    except Exception:
        pass

def _next_version_num(vdir) -> int:
    """v8.4.4 — monotonic version counter (이름 기반, capped 재사용 안 함).
    가장 높은 v숫자 파일명 + 1 반환.
    """
    try:
        nums = []
        for f in vdir.glob("v*.json"):
            try: nums.append(int(f.stem.lstrip("v")))
            except ValueError: pass
        return (max(nums) if nums else 0) + 1
    except Exception:
        return 1


@router.post("/tables/save")
def save_table(req: TableReq):
    sid = safe_id(req.id) if req.id else _stamp("tbl")
    fp = TABLES_DIR / f"{sid}.json"

    # Track previous name for CSV rename cleanup
    prev_name = ""
    if fp.exists():
        old = load_json(fp, None)
        if old:
            prev_name = old.get("name", "")
            vdir = VERSIONS_DIR / sid
            vdir.mkdir(parents=True, exist_ok=True)
            vnum = _next_version_num(vdir)
            # Audit: username + action + ts
            snapshot = dict(old)
            snapshot["_audit"] = {
                "username": req.username or "",
                "action": "edit",
                "saved_at": datetime.datetime.now().isoformat(),
                "version": vnum,
            }
            save_json(vdir / f"v{vnum}.json", snapshot, indent=2)
            _cap_versions(vdir)

    # v8.1.9: always inherit when group_id is set AND group has columns.
    # Previous `not req.columns` check failed because TableEditor initializes
    # columns to [{name:"",type:"string"}] (len=1, truthy) even when a group
    # is chosen, leaving the CSV writer with empty column names.
    if req.group_id:
        g = load_json(GROUPS_DIR / f"{req.group_id}.json", {})
        group_cols = g.get("columns", [])
        if group_cols:
            # Keep only cols explicitly named in group; blank-name entries discarded
            req.columns = [c for c in group_cols if c.get("name")]

    data = req.dict()
    data["id"] = sid
    data["updated"] = datetime.datetime.now().isoformat()
    save_json(fp, data, indent=2)

    # Upsert into config.nodes (display_name 이 있으면 그래프 라벨에도 반영)
    display_label = (req.display_name or req.name).strip() or req.name
    cfg = _load_config()
    if not req.group_id:
        existing = next((n for n in cfg["nodes"] if n.get("id") == sid), None)
        if existing:
            existing.update({"kind": "table", "name": display_label,
                              "physical_name": req.name, "ref_id": sid})
        else:
            cfg["nodes"].append({
                "id": sid, "kind": "table", "name": display_label,
                "physical_name": req.name, "ref_id": sid,
                "x": 100 + len(cfg["nodes"]) * 40,
                "y": 100 + len(cfg["nodes"]) * 40,
            })
    _save_config(cfg)

    # Update group's tables list
    if req.group_id:
        gfp = GROUPS_DIR / f"{req.group_id}.json"
        g = load_json(gfp, None)
        if g is not None and sid not in g.get("tables", []):
            g.setdefault("tables", []).append(sid)
            save_json(gfp, g, indent=2)

    # v8.1.8: clean old CSV if name changed, then write new one
    if prev_name and prev_name != req.name:
        _delete_db_csv(prev_name, sid)
    csv_path = _write_db_csv(sid, req.name, req.rows, req.columns)

    aws_result = _run_aws_sync(sid, req.rows, req.columns, req.aws_cmd)
    return {"ok": True, "id": sid, "aws_result": aws_result, "csv_path": csv_path}


@router.get("/tables/{table_id}")
def get_table(table_id: str):
    fp = TABLES_DIR / f"{safe_id(table_id)}.json"
    if not fp.exists():
        raise HTTPException(404)
    data = load_json(fp, {})
    if data.get("group_id") and not data.get("columns"):
        g = load_json(GROUPS_DIR / f"{data['group_id']}.json", {})
        if g.get("columns"):
            data["columns"] = g["columns"]
    return data


@router.get("/groups/{group_id}")
def get_group(group_id: str):
    fp = GROUPS_DIR / f"{safe_id(group_id)}.json"
    if not fp.exists():
        raise HTTPException(404)
    data = load_json(fp, {})
    group_cols = data.get("columns", [])
    tables = []
    for tid in data.get("tables", []):
        tfp = TABLES_DIR / f"{tid}.json"
        t = load_json(tfp, None)
        if t:
            if group_cols:
                t["columns"] = group_cols
            tables.append(t)
    data["_tables"] = tables
    return data


def _archive_and_remove(fp, prefix: str):
    if not fp.exists():
        return
    arch = ARCHIVE_DIR / f"{prefix}_{fp.stem}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    arch.write_text(fp.read_text("utf-8"), "utf-8")
    fp.unlink()


def _remove_node(cfg, sid: str):
    cfg["nodes"] = [n for n in cfg["nodes"] if n.get("ref_id") != sid and n.get("id") != sid]
    cfg["relations"] = [r for r in cfg["relations"]
                        if r.get("from") != sid and r.get("to") != sid]


@router.post("/tables/delete")
def delete_table(table_id: str = Query(...)):
    sid = safe_id(table_id)
    # v8.1.8: also remove DB-root CSV (read name from existing JSON first)
    fp = TABLES_DIR / f"{sid}.json"
    tbl_name = ""
    if fp.exists():
        old = load_json(fp, None)
        if old:
            tbl_name = old.get("name", "")
    _archive_and_remove(fp, "table")
    if tbl_name:
        _delete_db_csv(tbl_name, sid)
    cfg = _load_config()
    _remove_node(cfg, sid)
    _save_config(cfg)
    for gfp in GROUPS_DIR.glob("*.json"):
        g = load_json(gfp, None)
        if g and sid in g.get("tables", []):
            g["tables"] = [t for t in g["tables"] if t != sid]
            save_json(gfp, g, indent=2)
    return {"ok": True}


@router.post("/groups/delete")
def delete_group(group_id: str = Query(...)):
    sid = safe_id(group_id)
    _archive_and_remove(GROUPS_DIR / f"{sid}.json", "group")
    cfg = _load_config()
    _remove_node(cfg, sid)
    _save_config(cfg)
    return {"ok": True}


# ── DB reference pins ──
class DBRefReq(BaseModel):
    source_type: str
    name: str
    path: str = ""
    root: str = ""
    product: str = ""


@router.post("/db-ref/add")
def add_db_ref(req: DBRefReq):
    cfg = _load_config()
    db_id = _stamp("db")
    cfg["nodes"].append({
        "id": db_id, "kind": "db_ref", "name": req.name,
        "source_type": req.source_type, "path": req.path,
        "root": req.root, "product": req.product,
        "x": 400 + len(cfg["nodes"]) * 30,
        "y": 200 + len(cfg["nodes"]) * 30,
    })
    _save_config(cfg)
    return {"ok": True, "id": db_id}


@router.get("/db-ref/info")
def db_ref_info(node_id: str = Query(...)):
    cfg = _load_config()
    node = next((n for n in cfg["nodes"] if n.get("id") == node_id), None)
    if not node or node.get("kind") != "db_ref":
        raise HTTPException(404, "DB ref not found")

    info = {
        "node_id": node_id, "name": node.get("name", ""),
        "source_type": node.get("source_type", ""),
        "root": node.get("root", ""), "product": node.get("product", ""),
        "description": node.get("description", ""),
        "structure": "unknown", "file_count": 0, "columns": [], "dtypes": {},
    }

    from core.utils import read_one_file, _glob_data_files, detect_structure
    try:
        root = node.get("root", "")
        product = node.get("product", "")
        file = node.get("file", "")

        if file:
            fp = DB_BASE / file
            if fp.is_file():
                info["structure"] = "root_file"
                info["file_count"] = 1
                df = read_one_file(fp)
                if df is not None:
                    info["columns"] = list(df.columns)
                    info["dtypes"] = {n: str(d) for n, d in df.schema.items()}
        elif root and product:
            prod_path = DB_BASE / root / product
            if prod_path.is_dir():
                info["structure"] = detect_structure(prod_path)
                files = _glob_data_files(prod_path)
                info["file_count"] = len(files)
                if files:
                    df = read_one_file(files[0])
                    if df is not None:
                        info["columns"] = list(df.columns)
                        info["dtypes"] = {n: str(d) for n, d in df.schema.items()}
    except Exception:
        pass

    return info


class DBRefDescReq(BaseModel):
    node_id: str
    description: str = ""

@router.post("/db-ref/description")
def save_db_ref_description(req: DBRefDescReq):
    cfg = _load_config()
    node = next((n for n in cfg["nodes"] if n.get("id") == req.node_id), None)
    if not node:
        raise HTTPException(404)
    node["description"] = req.description
    _save_config(cfg)
    return {"ok": True}


@router.post("/db-ref/delete")
def delete_db_ref(node_id: str = Query(...)):
    cfg = _load_config()
    cfg["nodes"] = [n for n in cfg["nodes"] if n.get("id") != node_id]
    cfg["relations"] = [r for r in cfg["relations"]
                        if r.get("from") != node_id and r.get("to") != node_id]
    _save_config(cfg)
    return {"ok": True}


# ── Relations ──
class RelationReq(BaseModel):
    id: str = ""
    from_id: str
    to_id: str
    from_col: str = ""
    to_col: str = ""
    description: str = ""


@router.post("/relations/save")
def save_relation(req: RelationReq):
    cfg = _load_config()
    rid = req.id or _stamp("rel")
    rel = {"id": rid, "from": req.from_id, "to": req.to_id,
           "from_col": req.from_col, "to_col": req.to_col,
           "description": req.description}
    existing = next((r for r in cfg.get("relations", []) if r.get("id") == rid), None)
    if existing:
        existing.update(rel)
    else:
        cfg.setdefault("relations", []).append(rel)
    _save_config(cfg)
    return {"ok": True, "id": rid}


@router.post("/relations/delete")
def delete_relation(relation_id: str = Query(...)):
    cfg = _load_config()
    cfg["relations"] = [r for r in cfg.get("relations", []) if r.get("id") != relation_id]
    _save_config(cfg)
    return {"ok": True}


# ── Position ──
class PositionReq(BaseModel):
    node_id: str
    x: float
    y: float


@router.post("/node/position")
def update_position(req: PositionReq):
    cfg = _load_config()
    for n in cfg["nodes"]:
        if n.get("id") == req.node_id:
            n["x"] = req.x
            n["y"] = req.y
            _save_config(cfg)
            return {"ok": True}
    raise HTTPException(404)


# ── Listings ──
def _list_dir(dir_path, fields=("name", "updated")):
    items = []
    for fp in sorted(dir_path.glob("*.json")):
        d = load_json(fp, None)
        if not d:
            continue
        entry = {"id": d.get("id", fp.stem)}
        for f in fields:
            entry[f] = d.get(f, "")
        if "tables" in d:
            entry["tables"] = d.get("tables", [])
        if "group_id" in d:
            entry["group_id"] = d.get("group_id", "")
        if "columns" in d:
            entry["columns"] = d.get("columns", [])
        items.append(entry)
    return items


@router.get("/tables")
def list_tables():
    return {"tables": _list_dir(TABLES_DIR)}


@router.get("/groups")
def list_groups():
    return {"groups": _list_dir(GROUPS_DIR)}


# ── Versions ──
@router.get("/versions/{table_id}")
def get_versions(table_id: str):
    vdir = VERSIONS_DIR / safe_id(table_id)
    if not vdir.exists():
        return {"versions": [], "cap": VERSION_CAP}
    vs = []
    # Sort descending (latest first)
    for fp in sorted(vdir.glob("v*.json"),
                     key=lambda p: (p.stat().st_mtime, p.stem), reverse=True):
        d = load_json(fp, {})
        audit = d.get("_audit", {})
        vs.append({
            "name": fp.stem,
            "updated": audit.get("saved_at") or d.get("updated", ""),
            "user": audit.get("username") or d.get("username", ""),
            "action": audit.get("action", "edit"),
            "rows": len(d.get("rows", [])),
            "cols": len(d.get("columns", [])),
        })
    return {"versions": vs, "cap": VERSION_CAP}


@router.get("/version-content")
def get_version_content(table_id: str = Query(...), version: str = Query(...)):
    vfile = VERSIONS_DIR / safe_id(table_id) / f"{version}.json"
    if not vfile.exists():
        raise HTTPException(404)
    return load_json(vfile, {})


class RollbackReq(BaseModel):
    table_id: str
    version: str
    username: str = ""

@router.post("/versions/rollback")
def rollback_version(req: RollbackReq):
    """v8.4.4 — 선택 버전으로 롤백. 현재 상태도 rollback 직전 snapshot 으로 보존."""
    sid = safe_id(req.table_id)
    vdir = VERSIONS_DIR / sid
    vfile = vdir / f"{req.version}.json"
    if not vfile.exists():
        raise HTTPException(404, f"Version {req.version} not found")
    target = load_json(vfile, None)
    if target is None:
        raise HTTPException(500, "Cannot read version snapshot")

    # Snapshot current before rollback
    tfp = TABLES_DIR / f"{sid}.json"
    if tfp.exists():
        vdir.mkdir(parents=True, exist_ok=True)
        vnum = _next_version_num(vdir)
        cur = load_json(tfp, {})
        cur["_audit"] = {
            "username": req.username or "",
            "action": "pre-rollback",
            "saved_at": datetime.datetime.now().isoformat(),
            "version": vnum,
        }
        save_json(vdir / f"v{vnum}.json", cur, indent=2)

    # Apply target as current
    target.pop("_audit", None)  # audit 는 버전별, 현재 state 에는 미보관
    target["updated"] = datetime.datetime.now().isoformat()
    target["id"] = sid
    save_json(tfp, target, indent=2)

    # Re-write CSV
    _write_db_csv(sid, target.get("name", ""), target.get("rows", []), target.get("columns", []))
    _cap_versions(vdir)
    return {"ok": True, "id": sid, "rolled_back_to": req.version}


# ── Import from existing source (Base/DB file → TableMap) ──
class ImportReq(BaseModel):
    source_type: str = ""  # "base_file" | "root_parquet"
    file: str = ""
    root: str = ""
    product: str = ""
    name: str = ""
    display_name: str = ""
    group_id: str = ""
    username: str = ""
    rows_limit: int = 1000


@router.post("/tables/import")
def import_table(req: ImportReq):
    """v8.4.4 — FileBrowser/DB 소스에서 기존 parquet/csv 를 TableMap 테이블로 import.
    스키마 (columns) + 제한된 rows (<= rows_limit) 를 TableReq 로 변환해 save_table.
    """
    from core.utils import read_source
    try:
        df = read_source(req.source_type, req.root, req.product, req.file)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Import read failed: {e}")
    if df is None or df.height == 0:
        raise HTTPException(400, "Source is empty")

    # Truncate + convert
    if df.height > req.rows_limit:
        df = df.head(req.rows_limit)
    # Infer simple column types
    def dtype_to_str(dt):
        s = str(dt)
        if "Int" in s or "UInt" in s: return "int"
        if "Float" in s or "Decimal" in s: return "float"
        if "Bool" in s: return "bool"
        return "string"
    cols = [{"name": c, "type": dtype_to_str(df.schema[c])} for c in df.columns]
    rows = df.to_dicts()

    # Build TableReq
    name = req.name or (req.file.split(".")[0] if req.file else (req.product or "imported"))
    tr = TableReq(
        id="", name=name, display_name=req.display_name, group_id=req.group_id,
        table_type="data", columns=cols, rows=rows,
        description=f"Imported from {req.source_type}:{req.file or req.root+'/'+req.product}",
        username=req.username,
    )
    return save_table(tr)


# ── Data Lineage (v8.6.3) ─────────────────────────────────────────────
# 명시적 relations 외에, 노드 이름 휴리스틱으로 dataflow 를 추론한다.
# 핵심 룰: ML_TABLE_PROD* 는 ET / INLINE / EDS / KNOB / MASK / FAB / VM 소스로부터 파생.
#
# 노드 매칭 (대소문자 무시):
#   - upstream(=소스): 노드 이름에 ET / INLINE / EDS / KNOB / MASK / FAB / VM 가 포함.
#   - downstream(=피처): 노드 이름이 ML_TABLE 로 시작 (예: ML_TABLE_PRODA).
# 동일 product suffix (예: A, B) 가 있으면 우선 매칭한다.

UPSTREAM_TOKENS = ["ET", "INLINE", "EDS", "KNOB", "MASK", "FAB", "VM"]


def _classify_node(name: str) -> dict:
    """이름에서 token / ml_table 여부 / product suffix 를 추출."""
    n = (name or "").upper()
    is_ml = n.startswith("ML_TABLE") or "ML_TABLE_" in n
    tokens = [t for t in UPSTREAM_TOKENS if t in n]
    # Product suffix: ML_TABLE_PRODA → 'A' / ML_TABLE_PRODB → 'B'
    prod = ""
    if is_ml:
        idx = n.find("PROD")
        if idx >= 0 and idx + 4 < len(n):
            tail = n[idx + 4:]
            # 첫 영문자 1~2글자만 (PRODAA → 'AA')
            buf = ""
            for ch in tail:
                if ch.isalpha() and len(buf) < 2:
                    buf += ch
                else:
                    break
            prod = buf
    return {"is_ml": is_ml, "tokens": tokens, "prod": prod}


@router.get("/lineage")
def get_lineage():
    """추론된 dataflow + 명시적 relations 통합. UI 가 그대로 그리면 됨.

    응답:
      {
        "edges": [
          {"from_id": "...", "to_id": "...", "kind": "declared"|"inferred", "reason": "..."},
          ...
        ],
        "stats": {"declared": N, "inferred": M, "ml_targets": K, "sources": L}
      }
    """
    cfg = _load_config()
    nodes = cfg.get("nodes", []) or []
    relations = cfg.get("relations", []) or []
    edges = []

    # 1) Declared relations 그대로
    for r in relations:
        if r.get("from") and r.get("to"):
            edges.append({
                "from_id": r["from"], "to_id": r["to"], "kind": "declared",
                "reason": (r.get("description") or "수동 등록 관계"),
            })

    # 2) Inferred — name 휴리스틱
    classified = [{**_classify_node(n.get("physical_name") or n.get("name") or ""), "node": n}
                  for n in nodes]
    ml_nodes = [c for c in classified if c["is_ml"]]
    src_nodes = [c for c in classified if c["tokens"] and not c["is_ml"]]

    seen = {(e["from_id"], e["to_id"]) for e in edges}
    inferred_count = 0
    for ml in ml_nodes:
        for src in src_nodes:
            # 매칭: src 의 token 이 ml 의 token (제외 ML_TABLE) 과 겹치면 OK.
            # ml token 은 의미가 약하므로(이름에 ET 같은 키워드가 우연히 들어갈 수 있음) src 기준만 사용.
            token = src["tokens"][0]
            edge = (src["node"]["id"], ml["node"]["id"])
            if edge in seen:
                continue
            seen.add(edge)
            reason = f"{token} → ML_TABLE"
            if ml["prod"]:
                reason += f" (PROD{ml['prod']})"
            edges.append({
                "from_id": src["node"]["id"], "to_id": ml["node"]["id"],
                "kind": "inferred", "reason": reason,
            })
            inferred_count += 1

    return {
        "edges": edges,
        "stats": {
            "declared": len(relations),
            "inferred": inferred_count,
            "ml_targets": len(ml_nodes),
            "sources": len(src_nodes),
        },
    }
