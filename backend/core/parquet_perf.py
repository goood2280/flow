"""core/parquet_perf.py v1.0.0 (v8.8.33)
30~60GB parquet 대응 3종 헬퍼:
  1. collect_streaming(lf) — polars streaming engine 으로 aggregation 메모리 상한 고정
  2. prune_recent_partitions(files) — 경로 내 date= 파티션 중 최근 N 일 만 선택
  3. meta cache — row_count / schema 를 .meta.json 사이드카로 저장해 scan 없이 즉답

호출부는 lazy_read_source / filebrowser /view / dashboard 쪽에서 선택적으로 사용.
기존 lazy 경로는 유지 — 이 모듈은 opt-in helper.
"""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Iterable

import polars as pl


# ─────────────────────────────────────────────────────────────
# 1. streaming collect
# ─────────────────────────────────────────────────────────────
def collect_streaming(lf, fallback: bool = True):
    """Streaming engine 으로 collect. aggregation/group_by 메모리 상한 고정.
    polars >= 0.20 의 `collect(streaming=True)` 지원.
    실패 시 일반 collect 로 자동 fallback.
    """
    try:
        return lf.collect(engine="streaming")
    except TypeError:
        pass
    except Exception:
        if not fallback:
            raise
    try:
        return lf.collect(streaming=True)
    except TypeError:
        # 구버전 polars — streaming 인자 없음
        try:
            return lf.collect()
        except Exception:
            if fallback:
                return lf.collect()
            raise
    except Exception:
        if fallback:
            return lf.collect()
        raise


# ─────────────────────────────────────────────────────────────
# 2. partition pruning
# ─────────────────────────────────────────────────────────────
_HIVE_DATE_RE = re.compile(r"date=(\d{4}-?\d{2}-?\d{2})")
_DATE_TOKEN_RE = re.compile(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?!\d)")


def _parse_hive_date(path: Path):
    """path 어딘가의 `date=YYYYMMDD` 또는 파일명 날짜를 datetime.date 로."""
    for part in path.parts:
        m = _HIVE_DATE_RE.search(part)
        if m:
            raw = m.group(1).replace("-", "")
            if len(raw) == 8:
                try:
                    return datetime.strptime(raw, "%Y%m%d").date()
                except ValueError:
                    return None
    m = _DATE_TOKEN_RE.search(path.name)
    if m:
        raw = "".join(m.groups())
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def prune_recent_partitions(files: Iterable[Path], days: int = 30,
                            max_files: int | None = None) -> list[Path]:
    """hive 파티션 파일 리스트에서 최근 `days` 일 안의 파일만 반환.
    date 파티션이 없는 파일은 그대로 유지 (보수적).
    `max_files` 가 주어지면 결과 상한도 적용.
    """
    if not files:
        return []
    files = list(files)
    dated = []
    undated = []
    for fp in files:
        d = _parse_hive_date(fp)
        if d is None:
            undated.append(fp)
        else:
            dated.append((d, fp))
    if not dated:
        return files if max_files is None else files[-max_files:]

    cutoff = datetime.now().date() - timedelta(days=days)
    recent = [fp for (d, fp) in sorted(dated) if d >= cutoff]
    merged = undated + recent
    if not recent:
        # 최근 N일 안에 파일이 하나도 없음 → 가장 최신 파티션만 반환해 스펙 붕괴 방지
        merged = undated + [fp for (_, fp) in sorted(dated)[-1:]]
    if max_files is not None and len(merged) > max_files:
        merged = merged[-max_files:]
    return sorted(merged)


def prune_latest_partitions(files: Iterable[Path], max_files: int | None = None) -> list[Path]:
    """Return files from the newest date partition/file date only.

    This is the fast path for File Browser default previews. It avoids scanning
    a whole 30-day window just to show the first visible 200 rows.
    """
    files = list(files or [])
    if not files:
        return []
    dated: list[tuple[object, Path]] = []
    undated: list[Path] = []
    for fp in files:
        d = _parse_hive_date(fp)
        if d is None:
            undated.append(fp)
        else:
            dated.append((d, fp))
    if dated:
        latest = max(d for d, _fp in dated)
        selected = [fp for d, fp in dated if d == latest]
    else:
        selected = sorted(undated, key=lambda p: (p.stat().st_mtime if p.exists() else 0.0, str(p)))
    if max_files is not None and len(selected) > max_files:
        selected = selected[-max_files:]
    return sorted(selected)


def has_date_filter(sql: str | None) -> bool:
    """SQL 문자열에 date / time 필터가 있는지 대충 판정 — 있으면 prune 생략."""
    if not sql:
        return False
    s = sql.lower()
    return any(tok in s for tok in ("date", "time", "timestamp", "tkout", "tkin"))


# ─────────────────────────────────────────────────────────────
# 3. meta sidecar cache
# ─────────────────────────────────────────────────────────────
def _meta_path_for(fp: Path) -> Path:
    """`<file>.parquet` → `<file>.parquet.meta.json` (같은 폴더)."""
    return fp.with_suffix(fp.suffix + ".meta.json")


def _meta_path_for_dir(directory: Path) -> Path:
    """디렉토리 전체 요약용: `<dir>/.parquet_meta.json`."""
    return directory / ".parquet_meta.json"


def read_meta(fp: Path) -> dict | None:
    """사이드카 meta 가 최신이면 읽어 반환, 아니면 None.
    최신성 판정: meta.json 의 mtime >= parquet 의 mtime.
    """
    meta = _meta_path_for(fp)
    if not meta.exists() or not fp.exists():
        return None
    try:
        if meta.stat().st_mtime < fp.stat().st_mtime:
            return None
        return json.loads(meta.read_text("utf-8"))
    except Exception:
        return None


def write_meta(fp: Path, row_count: int, schema: dict, size_bytes: int | None = None) -> bool:
    """사이드카 meta 기록. 실패해도 조용히 False."""
    meta = _meta_path_for(fp)
    try:
        payload = {
            "row_count": int(row_count),
            "schema": {k: str(v) for k, v in (schema or {}).items()},
            "size_bytes": int(size_bytes) if size_bytes is not None else None,
            "written_at": datetime.now().isoformat(),
            "source_mtime": fp.stat().st_mtime if fp.exists() else None,
        }
        meta.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        return True
    except Exception:
        return False


def get_or_compute_meta(fp: Path) -> dict:
    """meta 가 있으면 즉답, 없으면 polars scan_parquet 으로 1회 계산 후 사이드카 기록."""
    cached = read_meta(fp)
    if cached is not None:
        return cached
    try:
        lf = pl.scan_parquet(str(fp))
        schema_obj = lf.collect_schema()
        schema = {n: str(schema_obj[n]) for n in schema_obj.names()}
        # row_count: streaming 으로 lean 하게
        try:
            row_count = int(lf.select(pl.len()).collect(streaming=True)[0, 0])
        except Exception:
            row_count = int(lf.select(pl.len()).collect()[0, 0])
        size = fp.stat().st_size if fp.exists() else None
        write_meta(fp, row_count, schema, size)
        return {
            "row_count": row_count,
            "schema": schema,
            "size_bytes": size,
            "written_at": datetime.now().isoformat(),
            "source_mtime": fp.stat().st_mtime if fp.exists() else None,
        }
    except Exception as e:
        return {"row_count": 0, "schema": {}, "size_bytes": None, "error": str(e)}


def invalidate_meta(fp: Path) -> bool:
    """meta 사이드카 제거 (수동 갱신용)."""
    meta = _meta_path_for(fp)
    try:
        if meta.exists():
            meta.unlink()
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────
# lazy_read + perf 합성
# ─────────────────────────────────────────────────────────────
def scan_parquet_perf(files: list[Path], *, hive: bool = True,
                      recent_days: int | None = 30,
                      max_files: int = 30,
                      latest_only: bool = False):
    """30~60GB 스케일 전용 scan helper.
      - hive_partitioning 활성
      - date= 파티션 pruning 기본 30일
      - 최대 파일 수 상한 (중복 방지 + 메모리 안전)
    recent_days=None 이면 pruning 생략.
    """
    if not files:
        return None
    selected = files
    if latest_only:
        selected = prune_latest_partitions(files, max_files=max_files)
    elif recent_days is not None:
        selected = prune_recent_partitions(files, days=recent_days, max_files=max_files)
    elif max_files is not None and len(selected) > max_files:
        selected = selected[-max_files:]
    if not selected:
        return None
    try:
        return pl.scan_parquet([str(f) for f in selected], hive_partitioning=hive)
    except Exception:
        # hive=True 가 동작 안 하면 off 로 재시도
        try:
            return pl.scan_parquet([str(f) for f in selected], hive_partitioning=False)
        except Exception:
            return None
