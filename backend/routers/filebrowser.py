"""routers/filebrowser.py v4.1.0 - lazy parquet + CSV + SQL, DB + Base roots.

v4.1.0 (2026-04): FabCanvas data split — in addition to the Hive-flat DB root
(FAB/VM/MASK/KNOB/INLINE/ET/YLD/LOTS + wafer_maps/), a sibling `Base` root now
holds single-file rulebooks + wide parquet (matching_step.csv, knob_ppid.csv,
mask.csv, inline_*.csv, yld_shot_agg.csv, dvc_rulebook.csv,
features_*.parquet, _uniques.json). The Base root is resolved via
`core.paths.PATHS.base_root` (backed by `core.roots.get_base_root()` — supports
FABCANVAS_BASE_ROOT env or admin_settings.data_roots.base).

New endpoints:
  - GET /api/filebrowser/scopes        → list of active scopes (DB + Base)
  - GET /api/filebrowser/roots?scope=  → scope-parameterised roots listing
                                          (`?scope=Base` returns Base leaves
                                          rather than canonical DB registry)
  - GET /api/filebrowser/base-files    → top-level file listing under Base root
  - GET /api/filebrowser/base-file-view → preview one Base file (parquet/csv/json)

Legacy `/roots` (no `scope` param) keeps its v7.1 shape — DB-canonical only.
"""
import json
import logging
import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import polars as pl
from core.paths import PATHS
from core.utils import (
    cast_cats, read_source, read_one_file, scan_one_file, apply_sql_like, serialize_rows,
    jsonl_append, jsonl_read, csv_response, safe_filename,
    DATA_EXTENSIONS, _glob_data_files,
)

logger = logging.getLogger("holweb.fb")
router = APIRouter(prefix="/api/filebrowser", tags=["filebrowser"])
# v4.1.1 (2026-04-19): module-level DB_BASE removed. Every route handler now
# reads `PATHS.db_root` / `PATHS.base_root` at request time so env overrides
# (FABCANVAS_*) and admin_settings.json data_roots land without reload.
DL_LOG = PATHS.download_log

# Extensions accepted for Base file listings.  Base parquet/csv come from
# DATA_EXTENSIONS; we additionally surface JSON (e.g. _uniques.json) and md
# so engineers can inspect rulebook docs from the same pane.
BASE_EXTENSIONS = set(DATA_EXTENSIONS)  # csv + parquet 만 타겟 (json/md 제외 — v8.3.x)


def _log_dl(username, product, sql, rows, cols, select_cols="", size_bytes=0):
    jsonl_append(DL_LOG, {
        "username": username, "product": product, "sql": sql or "",
        "rows": rows, "cols": cols, "select_cols": select_cols,
        "size_mb": round(size_bytes / 1e6, 2),
    })


@router.get("/domain")
def domain_info():
    """v7.2: Expose canonical domain model to frontend (level hierarchy, granularity, DB registry)."""
    from core.domain import DB_REGISTRY, VISIBLE_CANONICAL, LEVEL_ORDER
    return {
        "dbs": {k: v for k, v in DB_REGISTRY.items() if k in VISIBLE_CANONICAL or k == "ML_TABLE"},
        "level_order": LEVEL_ORDER,
        "visible": sorted(list(VISIBLE_CANONICAL)),
    }


@router.get("/roots")
def list_roots(all: bool = Query(False)):
    """v7.1: only canonical whitelisted DBs (FAB/VM/MASK/KNOB/INLINE/ET/YLD/ML_TABLE).

    Pass ?all=1 to bypass the whitelist (admin diagnostics).

    v8.7.6 fix: hive/flat 파티션 구조를 가진 임의 디렉토리도 DB 섹션에 노출.
    판단 규칙 — 디렉토리 자체 또는 하위에 parquet/csv 데이터 파일이 존재하면
    whitelist 바깥이어도 DB 로 간주. 루트의 단일 파일은 (신규 정책) Base 섹션에서만 보여줌.
    """
    from core.utils import detect_structure
    from core.domain import is_visible_root, is_visible_file, canonical_name, DB_REGISTRY
    result = []
    DB_BASE = PATHS.db_root
    if not DB_BASE.exists():
        return {"roots": []}
    for d in sorted(DB_BASE.iterdir()):
        # v8.1.2: explicit file skip — root-level single files go via Base only (v8.7.6).
        if not d.is_dir():
            continue
        # v8.7.6: whitelist 바깥이어도 데이터가 있으면 표시 (hive/flat 인식).
        file_count = len(_glob_data_files(d))
        whitelisted = is_visible_root(d.name)
        if not all and not whitelisted and file_count == 0:
            continue
        canon = canonical_name(d.name) if whitelisted else d.name
        meta = DB_REGISTRY.get(canon, {}) if whitelisted else {}
        structure = "directory"
        try:
            for sub in d.iterdir():
                if sub.is_dir():
                    structure = detect_structure(sub)
                    break
        except Exception:
            pass
        # v8.7.6: parquet 이 루트 직속에만 있어도 flat/hive 로 간주 → DB 노드로 노출
        if structure == "directory" and file_count > 0:
            structure = detect_structure(d) or "flat"
        result.append({
            "name": d.name,
            "canonical": canon,
            "level": meta.get("level", ""),
            "granularity": meta.get("granularity", ""),
            "icon": meta.get("icon", ""),
            "description": meta.get("description", "") if whitelisted else "(auto-detected hive/flat)",
            "path": str(d),
            "structure": structure,
            "dir_count": sum(1 for x in d.iterdir() if x.is_dir()),
            "parquet_count": file_count,
            "whitelisted": whitelisted,
        })
    # v8.1.1: root-level single files are now served ONLY by /root-parquets (sidebar "Root Parquets" section).
    # Keeping them here caused duplication with the DB list section.
    # Sort: directories first by level (L0→L3→wide), then rulebooks
    level_order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "wide": 4, "rulebook": 5, "": 6}
    result.sort(key=lambda r: (level_order.get(r.get("level", ""), 99), r["name"]))
    return {"roots": result}


@router.get("/scopes")
def list_scopes():
    """v4.1: Enumerate top-level data scopes for the sidebar switcher.

    Returns `DB` (Hive-flat source tree) always; `Base` appears only when
    `PATHS.base_root` exists on disk so the frontend can gracefully hide the
    tab for deploys that haven't been migrated yet.
    """
    scopes = []
    db_root = PATHS.db_root
    scopes.append({
        "key": "DB",
        "label": "DB",
        "description": "Hive-flat source tree — FAB/VM/MASK/KNOB/INLINE/ET/YLD + wafer_maps",
        "path": str(db_root),
        "exists": db_root.is_dir(),
        "icon": "🗄️",
    })
    base_root = PATHS.base_root
    scopes.append({
        "key": "Base",
        "label": "Base",
        "description": "Single-file rulebooks + wide parquet (matching / _uniques / features)",
        "path": str(base_root),
        "exists": base_root.is_dir(),
        "icon": "📚",
    })
    return {"scopes": scopes}


@router.get("/base-files")
def base_files():
    """v4.1: List top-level files under the Base root (single-file layout).

    Returns name/size/ext/modified for every *.csv / *.parquet / *.json / *.md
    — including `_uniques.json` which the adapter layer reads. Directories under
    Base are listed with a `kind=dir` row so the UI can still step into them if
    needed (the Base layout is currently flat so this is usually empty).
    """
    base_root = PATHS.base_root
    files, dirs = [], []
    if base_root.is_dir():
        for f in sorted(base_root.iterdir(), key=lambda p: (not p.is_file(), p.name.lower())):
            try:
                stat = f.stat()
            except OSError:
                continue
            if f.is_file():
                ext = f.suffix.lower()
                if ext not in BASE_EXTENSIONS:
                    continue
                files.append({
                    "name": f.name,
                    "path": f.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "ext": ext.lstrip("."),
                    "kind": "file",
                    "source": "base_root",
                })
            elif f.is_dir():
                data_files = _glob_data_files(f)
                dirs.append({
                    "name": f.name,
                    "path": f.name,
                    "kind": "dir",
                    "parquet_count": len(data_files),
                })
    # v8.7.5: DB 루트에 있는 단일 CSV 는 "Base" 로 분류 (물리적 위치와 무관하게 의미적 Base).
    # v8.7.6: 단일 parquet 도 동일 — 폴더(hive/flat) 구조만 DB 섹션에 노출됨.
    # v8.7.7: 같은 파일명이 base_root 와 db_root 양쪽에 있으면 dedup. UI 에 소스 태그
    # (db) 를 노출하던 것도 제거 — 사용자 입장에서 Base 단일 파일은 "한 번만" 보여야 함.
    seen_names = {f["name"].lower() for f in files}
    db_root = PATHS.db_root
    if db_root.is_dir() and db_root.resolve() != base_root.resolve():
        for f in sorted(db_root.iterdir()):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext not in (".csv", ".parquet"):
                continue
            if f.name.lower() in seen_names:
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            files.append({
                "name": f.name,
                "path": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "ext": ext.lstrip("."),
                "kind": "file",
                # v8.7.7: source 는 내부적으로만 유지 (preview 라우팅에 필요), UI 태그는 제거.
                "source": "db_root",
            })
            seen_names.add(f.name.lower())
    files.sort(key=lambda x: x["name"].lower())
    return {"files": files, "dirs": dirs,
            "path": str(base_root) if base_root.is_dir() else "",
            "exists": base_root.is_dir() or any(x.get("source") == "db_root" for x in files)}


@router.get("/base-file-view")
def base_file_view(file: str = Query(...), sql: str = Query(""),
                   rows: int = Query(200), cols: int = Query(10),
                   select_cols: str = Query("")):
    """v4.1: Preview a file under the Base root.

    Parquet/CSV use the same lazy reader path as `/root-parquet-view`; JSON
    files are returned as-is (truncated to first 2KB preview + full size) so
    `_uniques.json` can be inspected.
    """
    # Guard against path traversal — allow base_root, and also db_root-level
    # single files (CSV/Parquet). v8.7.7: parquet 도 허용 (base-files 에 노출되므로
    # 미리보기도 가능해야 함).
    base_root = PATHS.base_root
    db_root = PATHS.db_root
    fp = None
    for candidate_root in (base_root, db_root):
        if not candidate_root.is_dir():
            continue
        cand = (candidate_root / file).resolve()
        try:
            cand.relative_to(candidate_root.resolve())
        except ValueError:
            continue
        if cand.is_file():
            # v8.7.7: db_root 도 CSV + parquet 모두 Base 단일 파일로 취급.
            if candidate_root == db_root and cand.suffix.lower() not in (".csv", ".parquet"):
                continue
            fp = cand
            break
    if fp is None:
        raise HTTPException(404, f"File not found in Base or DB root: {file}")

    ext = fp.suffix.lower()
    if ext == ".json":
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(400, f"Cannot read JSON: {e}")
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        return {
            "kind": "json",
            "file": file,
            "size": fp.stat().st_size,
            "preview": text[:4096],
            "truncated": len(text) > 4096,
            "parsed_top_keys": list(parsed.keys()) if isinstance(parsed, dict) else None,
        }
    if ext == ".md":
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(400, f"Cannot read md: {e}")
        return {"kind": "md", "file": file, "size": fp.stat().st_size, "text": text[:16000],
                "truncated": len(text) > 16000}
    if ext not in DATA_EXTENSIONS:
        raise HTTPException(400, f"Unsupported ext for preview: {ext}")
    # v8.4.3 OOM-aware — lazy scan 동일.
    try:
        lf = scan_one_file(fp)
        if lf is None:
            raise HTTPException(400, f"Cannot read: {file}")
        full_schema_obj = lf.collect_schema()
        all_cols_full = list(full_schema_obj.names())
        schema_full = {n: str(full_schema_obj[n]) for n in all_cols_full}
        if not (select_cols and select_cols.strip()) and not (sql and sql.strip()):
            lf = lf.select(all_cols_full[:cols])
        resp = _run_view_lazy(lf, sql, select_cols, rows)
        resp["all_columns"] = all_cols_full
        resp["total_cols"] = len(all_cols_full)
        resp["dtypes"] = schema_full
        resp["kind"] = "table"
        resp["file"] = file
        return resp
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error: {str(e)}")


@router.get("/products")
def list_products(root: str = Query(...)):
    """List products available under a root.

    v8.2.2 — Hive-partitioned layout support:
      If the root's immediate subdirs are NOT `product=<P>/` (e.g. the FAB
      root contains `fab_history/` which then contains `product=<P>/`),
      walk one level deeper so the sidebar shows real product names
      (PRODUCT_A0, PRODUCT_A1, ...) instead of table names (fab_history,
      et_wafer, ...).  For tables in multi-table roots we aggregate the
      parquet count across all tables hosting that product.
    """
    rp = PATHS.db_root / root
    if not rp.is_dir():
        raise HTTPException(404)

    # 1. Collect every `product=<P>` directory at depth 1 or 2.
    direct_hive = [d for d in rp.iterdir()
                   if d.is_dir() and d.name.startswith("product=")]
    nested_hive = []
    for sub in rp.iterdir():
        if not sub.is_dir() or sub.name.startswith("product="):
            continue
        for inner in sub.iterdir():
            if inner.is_dir() and inner.name.startswith("product="):
                nested_hive.append(inner)
    hive_dirs = direct_hive + nested_hive

    if hive_dirs:
        # Group partitions by the product value (strip `product=` prefix).
        by_name: dict[str, list] = {}
        for d in hive_dirs:
            name = d.name[len("product="):]
            by_name.setdefault(name, []).append(d)
        prods = []
        for name in sorted(by_name):
            parts = by_name[name]
            total_files = 0
            for p in parts:
                total_files += len(_glob_data_files(p))
            prods.append({
                "name": name,
                "date_count": 0,
                "parquet_count": total_files,
                "latest_date": "",
                "structure": "hive",
            })
        return {"products": prods}

    # 2. Legacy fallback — emit each subdir as a "product" (pre-v8.2.2 behaviour).
    prods = []
    for d in sorted(rp.iterdir()):
        if not d.is_dir():
            continue
        data_files = _glob_data_files(d)
        if not data_files:
            continue
        has_hive = any(x.is_dir() and x.name.startswith("date=") for x in d.iterdir())
        structure = "hive" if has_hive else "flat"
        dates = sorted([x.name.replace("date=", "")
                        for x in d.iterdir()
                        if x.is_dir() and x.name.startswith("date=")])
        prods.append({
            "name": d.name, "date_count": len(dates), "parquet_count": len(data_files),
            "latest_date": dates[-1] if dates else "", "structure": structure,
        })
    return {"products": prods}


def _run_view(df, sql: str, select_cols: str, rows: int):
    """Apply select + sql + head; return standard response dict. Legacy DataFrame path."""
    all_columns = list(df.columns)
    schema = {n: str(d) for n, d in df.schema.items()}
    total = df.height

    if select_cols and select_cols.strip():
        sel = [c.strip() for c in select_cols.split(",") if c.strip() in set(all_columns)]
        if sel:
            df = df.select(sel)
    if sql and sql.strip():
        df = apply_sql_like(df, sql)
        total = df.height
    show = df.head(rows) if df.height > rows else df
    return {
        "total_rows": total, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": len(show),
    }


def _run_view_lazy(lf, sql: str, select_cols: str, rows: int):
    """v8.4.3 OOM-aware: lazy 스캔 + projection pushdown + head + (필요 시) SQL.

    - 컬럼 선택 / head 은 lazy 에서 처리 → parquet reader 에서 필요한 컬럼·행만 읽음
    - SQL 필터가 있으면 .collect() 후 apply_sql_like (projection 뒤라 메모리 작음)
    - 초기 미리보기(SQL/select 없음) 는 head 만 읽어 10GB 파일도 수백 KB 만 로드
    """
    schema_obj = lf.collect_schema()
    all_columns = list(schema_obj.names())
    schema = {n: str(schema_obj[n]) for n in all_columns}
    # Column-projection pushdown
    if select_cols and select_cols.strip():
        sel = [c.strip() for c in select_cols.split(",") if c.strip() in set(all_columns)]
        if sel:
            lf = lf.select(sel)

    if sql and sql.strip():
        df = lf.collect()
        df = apply_sql_like(df, sql)
        total = df.height
        show = df.head(rows) if df.height > rows else df
    else:
        # Head-only path: parquet scan + lazy head → only fetches the rows we need.
        show = lf.head(rows).collect()
        total = show.height  # 정확한 총 rows 는 defer — 성능 우선

    return {
        "total_rows": total, "total_cols": len(all_columns),
        "columns": list(show.columns), "all_columns": all_columns,
        "dtypes": schema, "showing_cols": list(show.columns),
        "selected_cols": select_cols.strip() or None,
        "data": serialize_rows(show.to_dicts()), "showing": len(show),
    }


@router.get("/view")
def view_product(root: str = Query(...), product: str = Query(...),
                 sql: str = Query(""), rows: int = Query(200),
                 select_cols: str = Query("")):
    # v8.4.3 OOM-aware: Hive-flat 도 lazy_read_source 로 scan. Polars 가 projection +
    # head 를 parquet reader 로 pushdown → 메모리 수 GB 제품도 안전.
    try:
        from core.utils import lazy_read_source
        lf = lazy_read_source(root=root, product=product)
        if lf is not None:
            return _run_view_lazy(lf, sql, select_cols, rows)
        # Fallback — legacy DF 경로
        df = read_source(root=root, product=product)
        return _run_view(df, sql, select_cols, rows)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"view {root}/{product}: {e}", exc_info=True)
        raise HTTPException(400, f"Error: {str(e)}")


@router.get("/root-parquets")
def root_parquets():
    """List root-level data files.
    v8.7.6 정책 변경: DB 루트의 단일 parquet 도 Base 로 분류 권장. 이 엔드포인트는
    하위호환용으로만 유지하며 빈 배열을 반환해 UI 에서 별도 섹션이 사라지도록 한다.
    (/api/filebrowser/base-files 가 db_root 의 단일 parquet 을 통합 노출한다.)"""
    return {"files": []}


@router.get("/root-parquet-view")
def view_root_parquet(file: str = Query(...), sql: str = Query(""),
                      rows: int = Query(200), cols: int = Query(10),
                      select_cols: str = Query("")):
    # v8.4.6: path traversal 방어 — db_root 밖 파일 접근 차단
    db_root = PATHS.db_root
    fp = (db_root / file).resolve()
    try:
        fp.relative_to(db_root.resolve())
    except ValueError:
        raise HTTPException(400, "Path escapes DB root")
    if not fp.is_file():
        raise HTTPException(404)
    try:
        # v8.4.3 OOM-aware: lazy scan — full read 회피. 10GB+ parquet 도 안전.
        lf = scan_one_file(fp)
        if lf is None:
            raise HTTPException(400, f"Cannot read: {file}")
        full_schema_obj = lf.collect_schema()
        all_cols_full = list(full_schema_obj.names())
        schema_full = {n: str(full_schema_obj[n]) for n in all_cols_full}
        # 미리보기 기본: 앞쪽 N 컬럼만. SQL 또는 select_cols 가 오면 그쪽 우선.
        if not (select_cols and select_cols.strip()) and not (sql and sql.strip()):
            lf = lf.select(all_cols_full[:cols])
        resp = _run_view_lazy(lf, sql, select_cols, rows)
        resp["all_columns"] = all_cols_full
        resp["total_cols"] = len(all_cols_full)
        resp["dtypes"] = schema_full
        return resp
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Error: {str(e)}")


@router.get("/download-csv")
def download_csv(root: str = Query(""), product: str = Query(""),
                 file: str = Query(""), sql: str = Query(""),
                 select_cols: str = Query(""), username: str = Query("anonymous"),
                 apply_reformatter: bool = Query(True)):
    """v7.2: If apply_reformatter=True and a per-product rules file exists,
    derived indices (VTH_IDX, CD_RANGE, poly2 window width, etc.) are appended
    to the download — matching what engineers actually need, not raw VALUE."""
    try:
        if file:
            # v8.4.6: traversal 방어
            db_root = PATHS.db_root
            fp = (db_root / file).resolve()
            try:
                fp.relative_to(db_root.resolve())
            except ValueError:
                raise HTTPException(400, "Path escapes DB root")
            if not fp.is_file():
                raise HTTPException(404)
            df = read_one_file(fp)
            if df is None:
                raise HTTPException(400, f"Cannot read: {file}")
            label = file
        elif root and product:
            df = read_source(root=root, product=product)
            label = f"{root}/{product}"
        else:
            raise HTTPException(400, "Specify file or root+product")

        # v7.2: Apply reformatter rules BEFORE select/sql so derived cols can be selected/filtered
        rf_applied = []
        if apply_reformatter and product:
            try:
                from core.reformatter import load_rules, apply_rules
                BASE = PATHS.data_root / "reformatter"
                rules = load_rules(BASE, product)
                if rules:
                    orig = set(df.columns)
                    df = apply_rules(df, rules, enabled_only=True)
                    rf_applied = [c for c in df.columns if c not in orig]
                    logger.info(f"Reformatter applied {len(rules)} rules → {len(rf_applied)} derived cols")
            except Exception as e:
                logger.warning(f"Reformatter skipped: {e}")

        if select_cols.strip():
            sel = [c.strip() for c in select_cols.split(",") if c.strip() in set(df.columns)]
            if sel:
                df = df.select(sel)
        if sql.strip():
            df = apply_sql_like(df, sql)

        csv_bytes = df.write_csv().encode("utf-8")
        if len(csv_bytes) > 100_000_000:
            raise HTTPException(400, "CSV too large (>100MB)")
        _log_dl(username, label, sql, df.height, df.width,
                select_cols=select_cols, size_bytes=len(csv_bytes))
        return csv_response(csv_bytes, label)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Download failed: {str(e)}")


@router.get("/download-history")
def download_history(username: str = Query(""), limit: int = Query(100)):
    f = (lambda e: e.get("username") == username) if username else None
    return {"logs": jsonl_read(DL_LOG, limit, f)}


class BaseDeleteReq(BaseModel):
    file: str
    username: str = ""

@router.post("/base-file/delete")
def delete_base_file(req: BaseDeleteReq):
    """v8.4.5 — Admin 이 Base 루트의 파일 삭제. 화이트리스트(parquet/csv/md/json) 만 허용,
    subdir escape 금지. Archive 로 이동 후 원본 제거 (복구 가능).
    """
    from routers.admin import _is_admin
    if not _is_admin(req.username):
        raise HTTPException(403, "Admin only")
    name = (req.file or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(400, "Invalid filename")
    base = PATHS.base_root
    fp = base / name
    if not fp.is_file():
        raise HTTPException(404, f"Not found: {name}")
    if fp.suffix.lower() not in {".parquet", ".csv", ".json", ".md", ".txt"}:
        raise HTTPException(400, f"Unsupported file type: {fp.suffix}")
    # Archive to .trash/<ts>_<name>
    try:
        trash = base / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        archived = trash / f"{ts}_{name}"
        fp.rename(archived)
        return {"ok": True, "file": name, "archived": str(archived)}
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")


@router.get("/sql-guide")
def sql_guide():
    return {"examples": [
        {"desc": "Equal", "sql": "col_name == 'value'"},
        {"desc": "LIKE", "sql": "col_name LIKE '%pattern%'"},
        {"desc": "NOT LIKE", "sql": "col_name NOT LIKE '%X%'"},
        {"desc": "IN", "sql": "col_name.is_in(['A','B'])"},
        {"desc": "AND", "sql": "(col_a > 1) & (col_b == 'X')"},
        {"desc": "BETWEEN", "sql": "(col >= 0.1) & (col <= 0.9)"},
    ]}
