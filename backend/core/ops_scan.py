"""관리자 에이전트 > 운영 점검 스캔.

관리자가 스캔 버튼을 누를 때만 돈다. 파일(DB 루트)·서버(운영/개발)·라이브러리·운영
상태를 **읽기만** 해서 규칙으로 findings 를 만들고, 연결된 LLM(사내 Gemma4)이 있으면
그 findings 만 근거로 한 추천을 덧붙인다. 설정·파일은 아무것도 바꾸지 않는다.

- findings 는 LLM 없이도 완결된다(각 항목에 권장 조치가 있다). AI 는 선택 레이어다.
- LLM 추천은 ``finding_ids`` 로 근거를 달아야 하고, 없는 id 는 버린다. 근거가 하나도
  없는 추천은 ``grounded: False`` 로 표시해 화면이 "일반 제안"으로 구분한다.
- 파일 훑기는 항목 수·시간 상한이 있다(대형 DB 에서도 수 초 안에 끝나게). 상한에
  걸리면 그 사실을 facts 에 남기고 "일부만 확인"으로 보고한다.
- 마지막 보고서는 ``{data_root}/ops_scan/latest.json`` 에 두어 탭을 다시 열어도 보인다.
- 버튼 외에 하루 1회 자동 점검이 돈다(기본 07시, 관리자 화면에서 끄거나 시각 변경).
  자동 점검도 읽기만 하고, 결과는 관리자 전원에게 bell 알림 한 건으로 남긴다
  (기본은 높음·보통 항목이 있을 때만). 설정은 ``schedule.json``, 실행 기록은
  ``schedule_state.json`` — 설정 저장이 실행 기록을 덮지 않게 파일을 나눈다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.paths import PATHS
from core.utils import load_json, save_json

SCHEMA_VERSION = 1
HISTORY_KEEP = 30

# 파일 훑기 상한 — 운영 DB 는 수십만 파일이다. 시간·항목 둘 다 넘기면 멈추고 표시한다.
WALK_MAX_ENTRIES = 60_000
WALK_MAX_SECONDS = 8.0
STALE_DAYS = 3.0            # 원천 DB 최신 파일이 이보다 오래되면 적재 정지 의심
BIG_CSV_BYTES = 256 * 1024 * 1024
SMALL_FILE_BYTES = 256 * 1024
SMALL_FILE_MIN_COUNT = 3000
LOG_DIR_WARN_BYTES = 2 * 1024 ** 3

DATA_SUFFIXES = {".parquet", ".csv"}
_SKIP_TOP_DIRS = {"cache", "credential", "confidential", "_backups", "mapfile", "teg_location", "valve-alerts",
                  "auto report", "reformatter", "wafer_maps"}

# 코드가 DB 루트에서 찾는 단일 파일과 그 파일이 없을 때 멈추는 기능.
EXPECTED_ROOT_FILES = (
    ("step_matching.csv", "스텝 매칭 — SplitTable·랏 진행·공정 구간"),
    ("ppid_knob.csv", "PPID→KNOB 분류 — SplitTable knob 열"),
    ("inline_matching.csv", "Inline 항목 매칭 — Inline 차트·홈 챗"),
    ("vm_matching.csv", "VM 항목 매칭"),
    ("Vehicle_matching.csv", "ET Index 다운로드 vehicle 매칭"),
    ("inline_shot_matching.csv", "Inline shot 좌표 매칭"),
    ("Chip_Radius.csv", "차트 radius 레이아웃"),
    ("Teg_location.csv", "TEG 지도"),
    ("dc_layer_step_mapping.csv", "DC layer 공용 매핑(분석의뢰·홈 챗)"),
)

# importlib 이름 → 없을 때 영향. _build_setup.py 의 필수/최소 의존성과 같은 목록.
LIBRARIES = (
    ("fastapi", True, "웹 API 전체"),
    ("uvicorn", True, "웹 서버"),
    ("polars", True, "SplitTable·파일 조회"),
    ("pyarrow", True, "Parquet 읽기/쓰기"),
    ("duckdb", True, "SQL 조회·차트생성"),
    ("pandas", True, "보고서·TEG·엑셀"),
    ("numpy", True, "수치 계산 전반"),
    ("psutil", True, "시스템 모니터·메모리 워치독"),
    ("openpyxl", False, "엑셀 읽기/쓰기"),
    ("xlsxwriter", False, "엑셀 내보내기"),
    ("xlrd", False, "구형 xls 읽기"),
    ("python-pptx", False, "Template Report·분석의뢰 PPTX"),
    ("matplotlib", False, "보고서 이미지 차트"),
    ("pillow", False, "TEG shot 그림 인식"),
    ("python-dotenv", False, ".env 설정 읽기"),
    ("requests", False, "LLM·메일 API 호출"),
    ("pyyaml", False, "제품 설정 YAML"),
    ("boto3", False, "S3 동기화"),
    ("scikit-learn", False, "홈 챗 ML 분석"),
    ("scipy", False, "통계 검정"),
    ("python-multipart", False, "파일 업로드"),
)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
AREAS = {"files": "파일", "servers": "서버", "libraries": "라이브러리", "operations": "운영"}

_RUN_LOCK = threading.Lock()


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _scan_dir() -> Path:
    return PATHS.data_root / "ops_scan"


def _latest_path() -> Path:
    return _scan_dir() / "latest.json"


def _history_path() -> Path:
    return _scan_dir() / "history.json"


def _gb(n: float) -> float:
    return round(float(n or 0) / (1024 ** 3), 2)


def _mb(n: float) -> float:
    return round(float(n or 0) / (1024 ** 2), 1)


class _Findings:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self._ids: dict[str, int] = {}

    def add(self, area: str, severity: str, key: str, title: str, detail: str = "", suggestion: str = "",
            evidence: dict | None = None, link: str = "") -> None:
        base = f"{area[:3]}.{key}"
        n = self._ids.get(base, 0)
        self._ids[base] = n + 1
        self.items.append({
            "id": base if n == 0 else f"{base}.{n + 1}",
            "area": area,
            "severity": severity if severity in SEVERITY_ORDER else "info",
            "title": title,
            "detail": detail,
            "suggestion": suggestion,
            "evidence": evidence or {},
            "link": link,
        })


def _guard(findings: _Findings, area: str, label: str, fn: Callable[[], Any], default=None):
    """한 점검이 실패해도 나머지는 돈다. 실패 자체를 info finding 으로 남긴다."""
    try:
        return fn()
    except Exception as exc:  # pragma: no cover - 방어 경로
        findings.add(area, "info", "scan_error", f"{label} 점검을 끝내지 못했습니다",
                     f"{type(exc).__name__}: {str(exc)[:200]}", "서버 로그에서 같은 시각의 오류를 확인해 주세요.")
        return default


# ── 파일 ──────────────────────────────────────────────────────────────────────

def _filebrowser_settings() -> dict:
    data = load_json(PATHS.data_root / "filebrowser_settings.json", {})
    return data if isinstance(data, dict) else {}


def _walk_source(root: Path, budget: dict) -> dict:
    """원천 DB 폴더 하나를 상한 안에서 훑는다(파일은 stat 만, 열지 않는다)."""
    stats = {"files": 0, "bytes": 0, "csv": 0, "parquet": 0, "newest": 0.0, "oldest": 0.0,
             "small_files": 0, "big_csv": [], "products": {}, "truncated": False}
    try:
        children = [c for c in root.iterdir()]
    except OSError:
        return stats
    for child in children:
        if child.is_dir():
            stats["products"][child.name.split("=", 1)[-1]] = {"files": 0, "newest": 0.0}
    stack = [root]
    while stack:
        if budget["entries"] >= WALK_MAX_ENTRIES or time.monotonic() > budget["deadline"]:
            stats["truncated"] = True
            budget["truncated"] = True
            break
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    budget["entries"] += 1
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    suffix = os.path.splitext(entry.name)[1].lower()
                    if suffix not in DATA_SUFFIXES:
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    stats["files"] += 1
                    stats["bytes"] += st.st_size
                    stats[suffix[1:]] += 1
                    stats["newest"] = max(stats["newest"], st.st_mtime)
                    stats["oldest"] = st.st_mtime if not stats["oldest"] else min(stats["oldest"], st.st_mtime)
                    if st.st_size < SMALL_FILE_BYTES:
                        stats["small_files"] += 1
                    if suffix == ".csv" and st.st_size >= BIG_CSV_BYTES and len(stats["big_csv"]) < 5:
                        stats["big_csv"].append({"path": str(Path(entry.path).relative_to(root)), "mb": _mb(st.st_size)})
                    rel = Path(entry.path).relative_to(root).parts
                    if len(rel) > 1:
                        product = rel[0].split("=", 1)[-1]
                        slot = stats["products"].setdefault(product, {"files": 0, "newest": 0.0})
                        slot["files"] += 1
                        slot["newest"] = max(slot["newest"], st.st_mtime)
        except OSError:
            continue
    return stats


def scan_files(findings: _Findings) -> dict:
    from core import roots

    db_root = roots.get_db_root()
    facts: dict[str, Any] = {"db_root": str(db_root), "sources": [], "root_files": [], "products": []}
    if not db_root.is_dir():
        findings.add("files", "high", "db_root_missing", "DB 루트 폴더가 없습니다", str(db_root),
                     "관리자 > 시스템 > 데이터 루트에서 경로를 확인하거나 공유 폴더 마운트를 점검하세요.",
                     link="admin:data_roots")
        return facts
    settings = _filebrowser_settings()
    hidden = {str(x).casefold() for x in settings.get("hidden_db_dirs") or []}
    now = time.time()
    budget = {"entries": 0, "deadline": time.monotonic() + WALK_MAX_SECONDS, "truncated": False}

    try:
        top = sorted(db_root.iterdir(), key=lambda p: p.name.casefold())
    except OSError as exc:
        findings.add("files", "high", "db_root_unreadable", "DB 루트를 읽을 수 없습니다", str(exc),
                     "서버 계정의 읽기 권한과 마운트 상태를 확인하세요.", link="admin:data_roots")
        return facts

    sources = [p for p in top if p.is_dir() and not p.name.startswith((".", "_"))
               and p.name.casefold() not in _SKIP_TOP_DIRS and p.name.casefold() not in hidden]
    stale_sources = []
    for src in sources:
        st = _walk_source(src, budget)
        age_days = round((now - st["newest"]) / 86400, 1) if st["newest"] else None
        row = {
            "name": src.name, "files": st["files"], "size_gb": _gb(st["bytes"]), "csv": st["csv"],
            "parquet": st["parquet"], "products": len(st["products"]),
            "newest": _dt.datetime.fromtimestamp(st["newest"]).isoformat(timespec="minutes") if st["newest"] else "",
            "newest_age_days": age_days, "truncated": st["truncated"],
        }
        facts["sources"].append(row)
        if st["files"] == 0 and not st["truncated"]:
            findings.add("files", "medium", "empty_source", f"{src.name} 폴더에 데이터 파일이 없습니다",
                         "parquet·csv 파일이 하나도 없습니다.",
                         "적재 경로가 바뀌었는지 확인하고, 쓰지 않는 폴더면 파일탐색기 설정에서 숨기세요.",
                         {"source": src.name}, "filebrowser")
            continue
        if age_days is not None and age_days >= STALE_DAYS and not st["truncated"]:
            stale_sources.append((src.name, age_days))
        if st["big_csv"]:
            findings.add("files", "low", "big_csv", f"{src.name} 에 큰 CSV 가 있습니다",
                         ", ".join(f"{b['path']} ({b['mb']}MB)" for b in st["big_csv"]),
                         "큰 CSV 는 조회마다 전체를 읽습니다. Parquet 로 바꾸면 조회·메모리가 크게 줄어듭니다.",
                         {"source": src.name, "files": st["big_csv"]}, "filebrowser")
        if st["small_files"] >= SMALL_FILE_MIN_COUNT and st["files"] and st["small_files"] / st["files"] > 0.8:
            findings.add("files", "low", "small_files", f"{src.name} 에 작은 파일이 많습니다",
                         f"{st['files']:,}개 중 {st['small_files']:,}개가 {SMALL_FILE_BYTES // 1024}KB 미만입니다.",
                         "날짜 파티션을 주·월 단위로 합치면(compaction) 스캔 시간과 파일 핸들이 줄어듭니다.",
                         {"source": src.name, "files": st["files"], "small": st["small_files"]}, "filebrowser")
        # 한 원천 안에서 다른 제품은 갱신되는데 멈춘 제품
        fresh = [p for p, v in st["products"].items() if v["newest"] and now - v["newest"] < STALE_DAYS * 86400]
        if fresh:
            stuck = sorted((p, round((now - v["newest"]) / 86400, 1)) for p, v in st["products"].items()
                           if v["newest"] and now - v["newest"] >= STALE_DAYS * 86400)
            if stuck:
                findings.add("files", "medium", "product_stale", f"{src.name} 에서 일부 제품만 갱신이 멈췄습니다",
                             ", ".join(f"{p} {d}일" for p, d in stuck[:12]),
                             "같은 원천의 다른 제품은 최근 파일이 있습니다. 해당 제품 적재 작업(수집 스크립트)을 확인하세요.",
                             {"source": src.name, "stuck": stuck[:30]}, "filebrowser")
        empty_products = sorted(p for p, v in st["products"].items() if v["files"] == 0)
        if empty_products and not st["truncated"]:
            findings.add("files", "low", "empty_product", f"{src.name} 에 빈 제품 폴더가 있습니다",
                         ", ".join(empty_products[:15]),
                         "제품 목록·홈 챗 제품 인식에 빈 제품이 섞입니다. 필요 없으면 폴더를 정리하세요.",
                         {"source": src.name, "products": empty_products[:50]}, "filebrowser")

    if stale_sources:
        if len(stale_sources) == len([s for s in facts["sources"] if s["files"]]):
            newest = min(d for _, d in stale_sources)
            findings.add("files", "high", "all_stale", "모든 원천 DB 의 갱신이 멈춰 있습니다",
                         f"가장 최근 파일도 {newest}일 전입니다.",
                         "공통 적재 경로(공유 폴더 마운트, 수집 서버, S3 동기화)를 먼저 확인하세요.",
                         {"sources": stale_sources}, "admin:data_roots")
        else:
            for name, days in stale_sources:
                findings.add("files", "medium", "source_stale", f"{name} 의 최신 파일이 {days}일 전입니다",
                             "다른 원천은 최근까지 갱신되고 있습니다.",
                             f"{name} 적재 작업이 멈췄는지 확인하세요.", {"source": name, "age_days": days},
                             "filebrowser")
    if budget["truncated"]:
        findings.add("files", "info", "walk_truncated", "파일이 많아 일부만 훑었습니다",
                     f"{budget['entries']:,}개 항목 또는 {WALK_MAX_SECONDS:.0f}초 상한에 도달했습니다.",
                     "결과의 파일 수·크기는 하한값입니다.")
    facts["walk_entries"] = budget["entries"]
    facts["walk_truncated"] = budget["truncated"]

    # 루트 단일 파일: 기대 파일 존재, 설명·검증 규칙
    root_files = {p.name.casefold(): p for p in top if p.is_file()}
    facts["root_files"] = sorted(p.name for p in root_files.values())[:200]
    missing = [(name, why) for name, why in EXPECTED_ROOT_FILES if name.casefold() not in root_files]
    if missing:
        findings.add("files", "medium", "expected_missing", f"기능이 찾는 기준 파일 {len(missing)}개가 없습니다",
                     "\n".join(f"{n} — {w}" for n, w in missing),
                     "해당 기능을 쓰는 곳이면 파일탐색기에서 CSV 를 만들어 두세요(쓰지 않는 기능이면 무시해도 됩니다).",
                     {"missing": [n for n, _ in missing]}, "filebrowser")

    descriptions = {str(k).casefold() for k in (settings.get("file_descriptions") or {})}
    rules = {str(k).casefold() for k in (settings.get("csv_rules") or {})}
    known_by_wiki: set[str] = set()
    try:
        from core import file_knowledge

        entries = file_knowledge.catalog().get("entries") or []
        for entry in entries:
            known_by_wiki.add(str(entry.get("file") or "").rsplit("/", 1)[-1].casefold())
            if entry.get("status") != "ready" and entry.get("warnings"):
                findings.add("files", "medium", "knowledge_file_warn", f"기본지식의 파일 설명이 파일과 맞지 않습니다: {entry.get('file')}",
                             "; ".join(str(w) for w in entry.get("warnings")[:4]),
                             "기본지식에서 파일명·열 이름을 실제 헤더와 같게 고치세요. 맞기 전에는 홈 챗 차트가 이 파일을 쓰지 않습니다.",
                             {"file": entry.get("file")}, "admin:domain_knowledge")
    except Exception:
        pass
    csvs = [p for p in root_files.values() if p.suffix.lower() == ".csv" and not p.name.lower().endswith(".bak")]
    undocumented = sorted(p.name for p in csvs if p.name.casefold() not in descriptions and p.name.casefold() not in known_by_wiki)
    if undocumented:
        findings.add("files", "low", "undocumented", f"설명이 없는 루트 CSV {len(undocumented)}개",
                     ", ".join(undocumented[:20]),
                     "파일탐색기 파일 설명이나 기본지식에 '무슨 파일·시간 열·값 열'을 적으면 홈 챗이 차트·조회에 바로 씁니다.",
                     {"files": undocumented[:60]}, "admin:domain_knowledge")
    unruled = sorted(p.name for p in csvs if p.name.casefold() not in rules
                     and any(p.name.casefold() == n.casefold() for n, _ in EXPECTED_ROOT_FILES))
    if unruled:
        findings.add("files", "low", "no_csv_rules", f"검증 규칙이 없는 기준 CSV {len(unruled)}개",
                     ", ".join(unruled),
                     "기준 파일은 저장할 때 잘못된 값이 들어가면 여러 기능이 같이 틀립니다. 파일탐색기 톱니 > 파일 설정에서 검증 규칙(LLM 초안 가능)을 두세요.",
                     {"files": unruled}, "filebrowser")
    leftovers = sorted(p.name for p in root_files.values() if p.name.lower().endswith((".bak", ".tmp", ".old")))
    if leftovers:
        findings.add("files", "info", "leftover", f"정리할 임시·백업 파일 {len(leftovers)}개",
                     ", ".join(leftovers[:15]), "버전 이력이 따로 남으므로 루트의 .bak/.tmp 파일은 옮기거나 지워도 됩니다.",
                     {"files": leftovers[:40]}, "filebrowser")

    # 제품: DB 에 있는데 위키·ML_TABLE 이 없는 제품
    try:
        from core import data_product_catalog

        catalog = data_product_catalog.discover_product_catalog(db_root)
    except Exception:
        catalog = []
    wiki_products: set[str] = set()
    try:
        from core import product_wiki

        wiki_products = {str(p).casefold() for p in product_wiki.products()}
    except Exception:
        pass
    no_wiki, no_split = [], []
    for row in catalog:
        name = str(row.get("product") or "")
        srcs = row.get("source_roots") or []
        facts["products"].append({"product": name, "sources": srcs, "split_table": bool(row.get("split_table"))})
        if name.upper().startswith("ML_TABLE_"):
            continue
        if wiki_products and name.casefold() not in wiki_products and len(srcs) >= 2:
            no_wiki.append(name)
        if not row.get("split_table") and len(srcs) >= 2:
            no_split.append(name)
    facts["products"] = facts["products"][:200]
    if no_wiki:
        findings.add("files", "low", "product_no_wiki", f"제품 위키가 없는 제품 {len(no_wiki)}개",
                     ", ".join(no_wiki[:20]),
                     "제품 위키에 공정·측정 요약을 두면 홈 챗 답변 근거가 생깁니다(원천 2개 이상인 제품만 셌습니다).",
                     {"products": no_wiki[:60]}, "productwiki")
    if no_split:
        findings.add("files", "info", "product_no_split", f"SplitTable(ML_TABLE) 이 없는 제품 {len(no_split)}개",
                     ", ".join(no_split[:20]), "SplitTable 을 쓰는 제품이면 ML_TABLE 생성 대상에 넣으세요.",
                     {"products": no_split[:60]}, "splittable")
    return facts


# ── 서버 ──────────────────────────────────────────────────────────────────────

def scan_servers(findings: _Findings) -> dict:
    from core import heavy_jobs, runtime_limits, sysmon

    facts: dict[str, Any] = {}
    sample = sysmon.collect_once()
    facts["this_server"] = {
        "host": platform.node(),
        "cpu_count": sample.get("system_cpu_count"), "memory_total_gb": sample.get("system_memory_total_gb"),
        "memory_percent": sample.get("memory_percent"), "disk_percent": sample.get("disk_percent"),
        "disk_free_gb": round(float(sample.get("disk_total_gb") or 0) - float(sample.get("disk_used_gb") or 0), 1),
        "process_memory_gb": sample.get("process_memory_effective_gb"),
        "process_memory_limit_gb": sample.get("process_memory_limit_gb"),
        "cpu_budget_cores": runtime_limits.cpu_budget_cores(), "resource_profile": runtime_limits.resource_profile(),
    }
    disk = float(sample.get("disk_percent") or 0)
    if disk >= 90:
        findings.add("servers", "high", "disk_full", f"디스크 사용률 {disk:.0f}%", f"남은 용량 {facts['this_server']['disk_free_gb']}GB",
                     "캐시관리에서 오래된 캐시를 정리하고 백업 보관 개수를 줄이세요. 90%를 넘으면 캐시 쓰기가 실패합니다.",
                     {"disk_percent": disk}, "ramcache")
    elif disk >= 80:
        findings.add("servers", "medium", "disk_high", f"디스크 사용률 {disk:.0f}%", f"남은 용량 {facts['this_server']['disk_free_gb']}GB",
                     "증가 추세라면 캐시 정리 주기나 백업 보관 개수를 조정하세요.", {"disk_percent": disk}, "ramcache")

    hist = sysmon.history(limit=288)
    mems = [float(h.get("memory_percent") or 0) for h in hist if h.get("memory_percent") is not None]
    if mems:
        mems_sorted = sorted(mems)
        p95 = mems_sorted[int(len(mems_sorted) * 0.95) - 1] if len(mems_sorted) > 1 else mems_sorted[0]
        facts["memory_24h"] = {"samples": len(mems), "avg": round(sum(mems) / len(mems), 1), "p95": round(p95, 1),
                               "max": round(max(mems), 1)}
        if p95 >= 90:
            findings.add("servers", "high", "memory_pressure", f"최근 메모리 사용률 p95 {p95:.0f}%",
                         f"평균 {facts['memory_24h']['avg']}%, 최대 {facts['memory_24h']['max']}% ({len(mems)}개 표본)",
                         "캐시 예산(캐시관리 톱니 pool_fraction)을 낮추거나 캐시 빌드·자동 스캔 주기를 줄이세요. 메모리 워치독 긴급 축출이 잦아집니다.",
                         facts["memory_24h"], "ramcache")
        elif facts["memory_24h"]["avg"] < 35 and float(sample.get("system_memory_total_gb") or 0) >= 24:
            findings.add("servers", "info", "memory_headroom", "메모리 여유가 큽니다",
                         f"최근 평균 {facts['memory_24h']['avg']}%",
                         "SplitTable 제품 RAM 캐시를 켜거나 캐시 예산을 올리면 조회 속도가 좋아집니다.",
                         facts["memory_24h"], "ramcache")

    heavy = heavy_jobs.status()
    facts["heavy_jobs"] = heavy
    for item in heavy.get("running") or []:
        if float(item.get("elapsed_sec") or 0) >= 3 * 3600:
            findings.add("servers", "medium", "heavy_job_long", f"무거운 작업이 {float(item['elapsed_sec']) / 3600:.1f}시간째 실행 중입니다",
                         str(item.get("label") or ""),
                         "캐시관리 스캔 큐에서 진행 상황을 보고, 멈춘 것 같으면 중단 후 다시 실행하세요.",
                         dict(item), "ramcache")
    stats = heavy.get("stats") or {}
    refused = int(stats.get("memory_guard") or 0) + int(stats.get("queue_timeout") or 0)
    if refused >= 5:
        findings.add("servers", "medium", "heavy_job_refused", f"무거운 작업이 메모리·대기 한도로 {refused}번 미뤄졌습니다",
                     f"메모리 부족 {int(stats.get('memory_guard') or 0)}회 · 대기 시간 초과 {int(stats.get('queue_timeout') or 0)}회 (서버 기동 이후)",
                     "캐시 예산(캐시관리 톱니 pool_fraction)을 낮추거나 캐시 빌드 주기를 사용자가 적은 시간대로 옮기세요.",
                     {k: v for k, v in stats.items() if isinstance(v, (int, float))}, "ramcache")
    legacy = {k: os.environ.get(k) for k in _LEGACY_WORKER_ENV if os.environ.get(k) not in (None, "")}
    if legacy:
        findings.add("servers", "low", "legacy_worker_env", "폐지된 개발서버(worker) 설정이 남아 있습니다",
                     " · ".join(f"{k}={v}" for k, v in legacy.items()),
                     "운영 단일 서버로 바뀌어 효과가 없는 값입니다. flow_env.bat·flow_env.local.bat 에서 지워 두세요.",
                     legacy)

    facts["schedulers"] = _scheduler_states()
    for st in facts["schedulers"]:
        for svc in st.get("failed") or []:
            hours = float(svc.get("down_hours") or 0)
            findings.add("servers", "high" if hours >= 24 else "medium", "scheduler_down",
                         f"{st.get('host')} 스케줄러 '{svc.get('service')}' 기동 실패", f"{svc.get('down_for') or ''} · {svc.get('error') or ''}"[:300],
                         "해당 기능이 조용히 멈춘 상태입니다. 오류 원인을 고치고 서버를 재시작하세요.",
                         {"host": st.get("host"), "service": svc.get("service"), "down_hours": hours}, "admin:monitor")

    from core.resource_diagnostics import RESOURCE_ENV_KEYS

    env_keys = RESOURCE_ENV_KEYS + ("FLOW_LLM_CONTEXT_SCALE", "FLOW_DB_ROOT", "FLOW_DATA_ROOT")
    facts["env_overrides"] = {k: os.environ.get(k) for k in env_keys if os.environ.get(k) not in (None, "")}
    resources, resource_warnings = runtime_limits.host_diagnostics()
    facts["resources"] = resources
    if resource_warnings:
        findings.add("servers", "medium", "resource_overrides", "자원 설정과 성능 경로를 확인하세요",
                     " · ".join(resource_warnings),
                     "현재 기동 로그와 flow_ctl.bat perf를 비교하고, 이전 VM의 제한값만 해제하세요. "
                     "환경 변경은 감시기까지 stop 후 다시 기동해야 적용됩니다.",
                     {"resources": resources, "warnings": resource_warnings}, "admin:monitor")
    try:
        from core import cache_settings

        facts["cache_settings"] = cache_settings.read()
    except Exception:
        facts["cache_settings"] = {}
    return facts


_LEGACY_WORKER_ENV = (
    "FLOW_SERVER_ROLE", "FLOW_WORKER_OFFLOAD", "FLOW_HOME_AGENT_OFFLOAD", "FLOW_CHART_BUILDER_OFFLOAD",
    "FLOW_FLOWI_OFFLOAD", "FLOW_WORKER_CONCURRENCY", "FLOW_WORKER_INTERACTIVE_CONCURRENCY",
    "FLOW_WORKER_POLARS_THREADS", "FLOW_WORKER_CACHE_BUDGET_FACTOR", "FLOW_API_SERVER_URL",
    "FLOW_HOME_AGENT_PROXY_TO_API",
)


def _scheduler_states() -> list[dict]:
    """이 data_root 의 스케줄러 실패 상태(예전 개발서버가 남긴 파일 포함)."""
    from core import scheduler_health

    out = []
    seen = set()
    try:
        own = scheduler_health.snapshot()
        out.append(own)
        seen.add(str(own.get("host")))
    except Exception:
        pass
    folder = getattr(scheduler_health, "HEALTH_DIR", None)
    if folder and Path(folder).is_dir():
        for fp in sorted(Path(folder).glob("*.json"))[:10]:
            state = load_json(fp, {})
            host = str(state.get("host") or fp.stem)
            if not isinstance(state, dict) or host in seen:
                continue
            seen.add(host)
            failed = []
            for name, info in (state.get("services") or {}).items():
                first = str((info or {}).get("first_failed_at") or "")
                hours = 0.0
                try:
                    hours = round((_dt.datetime.now() - _dt.datetime.fromisoformat(first)).total_seconds() / 3600, 1)
                except Exception:
                    pass
                failed.append({"service": name, "error": str((info or {}).get("error") or ""), "down_hours": hours,
                               "down_for": f"{hours}시간" if hours else ""})
            out.append({"host": host, "role": state.get("role") or "", "updated_at": state.get("updated_at") or "",
                        "failed": failed})
    return out


# ── 라이브러리 ────────────────────────────────────────────────────────────────

def scan_libraries(findings: _Findings) -> dict:
    from importlib import metadata

    rows = []
    for dist, required, used_for in LIBRARIES:
        try:
            version = metadata.version(dist)
        except metadata.PackageNotFoundError:
            version = ""
        rows.append({"name": dist, "version": version, "required": required, "used_for": used_for})
    facts = {"python": sys.version.split()[0], "platform": platform.platform(terse=True), "packages": rows}
    missing_required = [r for r in rows if r["required"] and not r["version"]]
    missing_optional = [r for r in rows if not r["required"] and not r["version"]]
    if missing_required:
        findings.add("libraries", "high", "missing_required", f"필수 라이브러리 {len(missing_required)}개가 없습니다",
                     ", ".join(f"{r['name']}({r['used_for']})" for r in missing_required),
                     "setup.py 의 의존성 설치(pip)를 다시 돌리거나 사내 미러에서 설치하세요.",
                     {"packages": [r["name"] for r in missing_required]})
    if missing_optional:
        findings.add("libraries", "low", "missing_optional", f"선택 라이브러리 {len(missing_optional)}개가 없습니다",
                     "\n".join(f"{r['name']} — {r['used_for']}" for r in missing_optional),
                     "해당 기능을 쓰면 설치하세요. 없으면 그 기능만 실패합니다.",
                     {"packages": [r["name"] for r in missing_optional]})
    if sys.version_info < (3, 10):
        findings.add("libraries", "medium", "python_old", f"Python {facts['python']}",
                     "flow 는 3.10 이상을 기준으로 테스트합니다.", "Python 3.10 이상 환경으로 옮기세요.")
    by_name = {r["name"]: r["version"] for r in rows}
    duck = by_name.get("duckdb") or ""
    if duck.startswith("1.1."):
        findings.add("libraries", "info", "duckdb_11", f"duckdb {duck}",
                     "1.1.x 는 결과 열 타입을 'NUMBER'/'STRING' 으로만 알려 줍니다(코드는 DESCRIBE 로 우회 중).",
                     "업그레이드 여유가 있으면 1.2 이상을 검토하세요. 필수는 아닙니다.")
    pl = by_name.get("polars") or ""
    if pl and pl.split(".")[0] == "0" and int((pl.split(".") + ["0", "0"])[1] or 0) < 20:
        findings.add("libraries", "low", "polars_old", f"polars {pl}", "1.x 이전 버전은 streaming·메모리 동작이 다릅니다.",
                     "운영 반영 전 개발 PC 에서 polars 1.x 로 올려 테스트해 보세요.")
    return facts


# ── 운영 ──────────────────────────────────────────────────────────────────────

def scan_operations(findings: _Findings) -> dict:
    facts: dict[str, Any] = {}
    from core import llm_adapter

    cfg = llm_adapter.get_config(redact=True) or {}
    health = llm_adapter.health_snapshot() or {}
    facts["llm"] = {"available": llm_adapter.is_available(), "provider": cfg.get("provider") or "",
                    "model": cfg.get("model") or "", "status": health.get("status") or "",
                    "last_error": str(health.get("last_error") or "")[:200], "last_latency_ms": health.get("last_latency_ms")}
    if not facts["llm"]["available"]:
        findings.add("operations", "low", "llm_off", "연결된 LLM 이 없습니다", "홈 챗·AI 초안·이 스캔의 AI 추천이 규칙 요약으로만 동작합니다.",
                     "관리자 > 에이전트 > LLM 설정에서 사내 Gemma4 를 연결하세요.", link="admin:llm_cfg")
    elif facts["llm"]["status"] == "unhealthy":
        findings.add("operations", "medium", "llm_unhealthy", "최근 LLM 호출이 실패했습니다", facts["llm"]["last_error"],
                     "LLM 설정의 연결 테스트로 확인하세요. 실패가 이어지면 60초 차단기가 계속 열립니다.", link="admin:llm_cfg")

    from core import backup

    bk = backup.get_settings()
    items = backup.list_backups()
    facts["backup"] = {"enabled": bk.get("enabled"), "interval_hours": bk.get("interval_hours"), "keep": bk.get("keep"),
                       "path": bk.get("path") or "(기본)", "count": len(items), "latest": items[0]["modified"] if items else ""}
    if not bk.get("enabled"):
        findings.add("operations", "medium", "backup_off", "자동 백업이 꺼져 있습니다", "",
                     "관리자 > 시스템 > 백업에서 주기 백업을 켜 두세요. 사용자·권한·설정 파일은 백업이 없으면 복구가 어렵습니다.",
                     link="admin:backup_sched")
    elif not items:
        findings.add("operations", "medium", "backup_none", "백업 파일이 하나도 없습니다", str(bk.get("path") or ""),
                     "백업 경로 쓰기 권한을 확인하고 지금 한 번 수동 백업을 실행하세요.", link="admin:backup_sched")
    else:
        try:
            age_h = (_dt.datetime.now() - _dt.datetime.fromisoformat(items[0]["modified"])).total_seconds() / 3600
            if age_h > max(48.0, 2.5 * float(bk.get("interval_hours") or 24)):
                findings.add("operations", "medium", "backup_old", f"마지막 백업이 {age_h / 24:.1f}일 전입니다",
                             f"주기 {bk.get('interval_hours')}시간", "백업 스케줄러가 멈췄는지 확인하세요.",
                             {"age_hours": round(age_h, 1)}, "admin:backup_sched")
        except Exception:
            pass

    from core import mail

    mcfg = mail.load_mail_cfg()
    facts["mail"] = {"enabled": mcfg.get("enabled"), "api_url_set": bool(mcfg.get("api_url")),
                     "domain": mcfg.get("domain"), "app_base_url_set": bool(mcfg.get("app_base_url"))}
    if not mcfg.get("enabled") or not mcfg.get("api_url"):
        findings.add("operations", "low", "mail_off", "메일 발송이 설정되지 않았습니다", "인폼·분석의뢰·ET 추적 메일이 나가지 않습니다.",
                     "관리자 > 시스템 > 메일 API 를 설정하세요.", link="admin:mail_cfg")
    elif not mcfg.get("app_base_url"):
        findings.add("operations", "low", "mail_no_link", "메일 속 바로가기 주소가 비어 있습니다", "",
                     "메일 API 설정의 앱 주소(app_base_url)를 채우면 메일에서 화면으로 바로 이동합니다.", link="admin:mail_cfg")

    try:
        from routers.auth import read_users

        users = read_users() or []
        pending = [u for u in users if str(u.get("status") or "") == "pending"]
        facts["users"] = {"total": len(users), "pending": len(pending)}
        if pending:
            findings.add("operations", "medium", "users_pending", f"가입 승인 대기 {len(pending)}명", "",
                         "관리자 > 운영 > 사용자에서 승인하거나 거절하세요.", {"pending": len(pending)}, "admin:users")
    except Exception:
        pass

    try:
        from core import domain_knowledge

        doc = domain_knowledge.read_document()
        body = str(doc.get("body") or "")
        facts["domain_knowledge"] = {"chars": len(body), "version": doc.get("version")}
        if len(body) < 300:
            findings.add("operations", "low", "knowledge_thin", "기본지식이 거의 비어 있습니다", f"{len(body)}자",
                         "제품·공정 용어, 주요 파일 설명을 적어 두면 홈 챗이 질문을 훨씬 잘 알아듣습니다.",
                         link="admin:domain_knowledge")
    except Exception:
        pass

    log_dir = PATHS.data_root / "logs"
    if log_dir.is_dir():
        total = 0
        for fp in list(log_dir.rglob("*"))[:5000]:
            try:
                if fp.is_file():
                    total += fp.stat().st_size
            except OSError:
                continue
        facts["log_dir_mb"] = _mb(total)
        if total >= LOG_DIR_WARN_BYTES:
            findings.add("operations", "low", "logs_big", f"로그 폴더가 {_gb(total)}GB 입니다", str(log_dir),
                         "오래된 로그를 압축·삭제하는 주기 작업을 두세요.", {"mb": _mb(total)})

    feedback = PATHS.data_root / "agent_feedback.jsonl"
    if feedback.is_file():
        try:
            cutoff = time.time() - 7 * 86400
            neg = 0
            total = 0
            with feedback.open("r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()[-3000:]
            for line in lines:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("ts") or rec.get("timestamp") or rec.get("at")
                try:
                    t = float(ts) if isinstance(ts, (int, float)) else _dt.datetime.fromisoformat(str(ts)[:19]).timestamp()
                except Exception:
                    t = 0
                if t and t < cutoff:
                    continue
                total += 1
                rating = str(rec.get("rating") or rec.get("vote") or rec.get("feedback") or "").lower()
                if rating in {"down", "bad", "-1", "negative", "dislike", "thumbs_down"}:
                    neg += 1
            facts["chat_feedback_7d"] = {"total": total, "negative": neg}
            if total >= 10 and neg / total >= 0.3:
                findings.add("operations", "medium", "chat_negative", f"최근 7일 홈 챗 부정 평가 {neg}/{total}건", "",
                             "Flow-i 학습에서 부정 평가 질문을 모아 별칭·기본지식을 보강하세요.",
                             facts["chat_feedback_7d"], "admin:flowi_learning")
        except Exception:
            pass
    return facts


# ── LLM 추천 ─────────────────────────────────────────────────────────────────

LLM_SYSTEM = (
    "당신은 사내 반도체 데이터 웹앱 flow 의 운영 점검 도우미입니다. "
    "입력의 findings(규칙 점검 결과)와 facts(수집한 사실)만 근거로 관리자가 할 일을 추천합니다. "
    "설정을 직접 바꾸지 않으며, 관리자가 읽고 고칠 수 있는 짧은 한국어 조치를 씁니다. "
    "facts 에 없는 숫자·파일명·설정 키·경로를 만들지 않습니다. "
    "findings 를 한 건씩 되풀이하지 말고, 원인이나 고치는 화면이 같은 findings 는 하나의 추천으로 묶어 "
    "근거 finding id 를 모두 finding_ids 에 넣습니다(예: 무거운 작업 지연과 메모리 압박). "
    "severity 가 info 인 finding 은 다른 추천에 덧붙일 때만 씁니다. "
    "reason 에는 facts 의 수치를 인용하고, action 은 순서가 있으면 ①②③ 단계로 씁니다. "
    "area 는 고치는 곳 기준입니다(원천 DB 적재·파일은 files, 서버 자원·스케줄러는 servers). "
    "findings 에 없는 새 관점(서버 설정 균형, 캐시 예산, 생성하면 좋을 파일·문서)은 facts 근거가 있을 때만 제안합니다. "
    "priority 는 high|medium|low, area 는 files|servers|libraries|operations 중 하나입니다. 최대 8개, 중요한 순서."
)
LLM_SCHEMA = {
    "type": "object",
    "required": ["recommendations"],
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {"type": "array"},
    },
}


def _llm_payload(findings: list[dict], facts: dict) -> str:
    compact_findings = [
        {k: f[k] for k in ("id", "area", "severity", "title", "detail", "suggestion")}
        | {"detail": str(f.get("detail") or "")[:300]}
        for f in findings[:60]
    ]
    files = facts.get("files") or {}
    servers = facts.get("servers") or {}
    compact_facts = {
        "files": {"sources": files.get("sources", [])[:30], "root_files": files.get("root_files", [])[:80],
                  "products": len(files.get("products") or []), "walk_truncated": files.get("walk_truncated")},
        "servers": {k: servers.get(k) for k in ("this_server", "memory_24h", "heavy_jobs", "env_overrides", "cache_settings")},
        "libraries": {"python": (facts.get("libraries") or {}).get("python"),
                      "packages": {r["name"]: r["version"] or "없음" for r in (facts.get("libraries") or {}).get("packages", [])}},
        "operations": facts.get("operations") or {},
    }
    text = json.dumps({"findings": compact_findings, "facts": compact_facts,
                       "response_schema": {"summary": "전체 상태 한두 문장",
                                           "recommendations": [{"title": "짧은 제목", "area": "files|servers|libraries|operations",
                                                                "priority": "high|medium|low", "reason": "왜 필요한지(근거 수치 포함)",
                                                                "action": "관리자가 할 일(어느 화면에서 무엇을)",
                                                                "finding_ids": ["근거 finding id"]}]}},
                      ensure_ascii=False, separators=(",", ":"), default=str)
    return text[:24000]


def ai_recommendations(findings: list[dict], facts: dict) -> dict:
    from core import llm_adapter

    if not llm_adapter.is_available():
        return {"used": False, "reason": "llm_not_connected", "message": "연결된 LLM 이 없어 규칙 점검 결과만 보여 줍니다."}
    try:
        model = str((llm_adapter.get_config(redact=True) or {}).get("model") or "")
    except Exception:
        model = ""
    started = time.monotonic()
    out = llm_adapter.complete_json(_llm_payload(findings, facts), system=LLM_SYSTEM, schema=LLM_SCHEMA,
                                    timeout=90, max_retries=1)
    elapsed = round(time.monotonic() - started, 1)
    if not out.get("ok"):
        return {"used": False, "reason": "llm_call_failed", "model": model, "elapsed_s": elapsed,
                "message": f"LLM 호출 실패 · {str(out.get('error') or '응답 없음')[:240]}"}
    obj = out.get("obj") or {}
    known = {f["id"] for f in findings}
    info_ids = {f["id"] for f in findings if f["severity"] == "info"}
    recs = []
    for raw in (obj.get("recommendations") or [])[:8]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:120]
        action = str(raw.get("action") or "").strip()[:600]
        if not title or not action:
            continue
        ids = [str(i) for i in (raw.get("finding_ids") or []) if str(i) in known][:10]
        if ids and all(i in info_ids for i in ids):
            # 참고(info) 항목만 근거인 추천은 점검 결과 목록과 중복이다(작은 모델이 자주 만든다).
            continue
        area = str(raw.get("area") or "").strip()
        priority = str(raw.get("priority") or "").strip().lower()
        recs.append({
            "title": title,
            "area": area if area in AREAS else "operations",
            "priority": priority if priority in {"high", "medium", "low"} else "medium",
            "reason": str(raw.get("reason") or "").strip()[:600],
            "action": action,
            "finding_ids": ids,
            "grounded": bool(ids),
        })
    return {"used": True, "model": model, "elapsed_s": elapsed, "summary": str(obj.get("summary") or "").strip()[:400],
            "recommendations": recs}


# ── 실행·보관 ─────────────────────────────────────────────────────────────────

def busy() -> bool:
    return _RUN_LOCK.locked()


def run(actor: str = "", use_ai: bool = True) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("scan_in_progress")
    try:
        started = time.monotonic()
        findings = _Findings()
        facts: dict[str, Any] = {}
        timings: dict[str, float] = {}
        for area, label, fn in (("files", "파일", scan_files), ("servers", "서버", scan_servers),
                                ("libraries", "라이브러리", scan_libraries), ("operations", "운영", scan_operations)):
            t0 = time.monotonic()
            facts[area] = _guard(findings, area, label, lambda fn=fn: fn(findings), default={}) or {}
            timings[area] = round(time.monotonic() - t0, 2)
        items = sorted(findings.items, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["area"], f["id"]))
        counts = {sev: sum(1 for f in items if f["severity"] == sev) for sev in SEVERITY_ORDER}
        t0 = time.monotonic()
        ai = ai_recommendations(items, facts) if use_ai else {"used": False, "reason": "disabled",
                                                              "message": "AI 추천 없이 규칙 점검만 실행했습니다."}
        timings["ai"] = round(time.monotonic() - t0, 2)
        if ai.get("used"):
            # 직전 실패로 남은 health 상태는 방금 성공한 호출로 이미 낡았다.
            items = [f for f in items if f["id"] != "ope.llm_unhealthy"]
            counts = {sev: sum(1 for f in items if f["severity"] == sev) for sev in SEVERITY_ORDER}
        report = {
            "schema": SCHEMA_VERSION,
            "scanned_at": _now_iso(),
            "actor": actor,
            "elapsed_s": round(time.monotonic() - started, 2),
            "timings": timings,
            "counts": counts,
            "findings": items,
            "facts": facts,
            "ai": ai,
        }
        _save(report)
        return report
    finally:
        _RUN_LOCK.release()


def _save(report: dict) -> None:
    try:
        _scan_dir().mkdir(parents=True, exist_ok=True)
        save_json(_latest_path(), report)
        hist = load_json(_history_path(), [])
        if not isinstance(hist, list):
            hist = []
        hist.insert(0, {"scanned_at": report["scanned_at"], "actor": report["actor"], "counts": report["counts"],
                        "ai_used": bool((report.get("ai") or {}).get("used")), "elapsed_s": report["elapsed_s"]})
        save_json(_history_path(), hist[:HISTORY_KEEP])
    except Exception:
        pass


def latest() -> dict:
    report = load_json(_latest_path(), {})
    hist = load_json(_history_path(), [])
    return {"report": report if isinstance(report, dict) and report.get("scanned_at") else None,
            "history": hist if isinstance(hist, list) else [], "busy": busy(), "schedule": get_schedule()}


# ── 매일 자동 점검 + 관리자 알림 ──────────────────────────────────────────────
# 공유 백그라운드 owner 프로세스 하나에서만 돈다(app_v2/runtime/startup.py owner_starters).
# 날짜 표식(last_auto_date)으로 하루 1회를 지키고, 알림 id 도 날짜로 고정해 두 번
# 불려도 같은 날 알림이 두 건 생기지 않는다.
SCHEDULE_DEFAULTS = {"enabled": True, "hour": 7, "use_ai": True, "notify": "issues"}
NOTIFY_MODES = ("issues", "always")   # issues = 높음·보통 항목이 있을 때만
SCHEDULER_TICK_SECONDS = 600.0
SCHEDULER_STARTUP_DELAY_SECONDS = 300.0   # 기동 직후 캐시 예열과 겹치지 않게
ALERT_EVENT = "ops_scan_daily"
ALERT_TOP_ITEMS = 3

_SCHEDULER_STARTED = False
_SCHEDULER_LOCK = threading.Lock()


def _schedule_path() -> Path:
    return _scan_dir() / "schedule.json"


def _schedule_state_path() -> Path:
    return _scan_dir() / "schedule_state.json"


def _clean_schedule(raw: Any) -> dict:
    out = dict(SCHEDULE_DEFAULTS)
    if not isinstance(raw, dict):
        return out
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    if "use_ai" in raw:
        out["use_ai"] = bool(raw["use_ai"])
    try:
        hour = int(raw.get("hour", out["hour"]))
        if 0 <= hour <= 23:
            out["hour"] = hour
    except (TypeError, ValueError):
        pass
    if raw.get("notify") in NOTIFY_MODES:
        out["notify"] = raw["notify"]
    return out


def _load_state() -> dict:
    state = load_json(_schedule_state_path(), {})
    return state if isinstance(state, dict) else {}


def _save_state(state: dict) -> None:
    try:
        _scan_dir().mkdir(parents=True, exist_ok=True)
        save_json(_schedule_state_path(), state)
    except Exception:
        pass


def schedule_settings() -> dict:
    return _clean_schedule(load_json(_schedule_path(), {}))


def is_due(now: _dt.datetime, settings: dict, state: dict) -> bool:
    return bool(settings["enabled"]) and now.hour >= settings["hour"] \
        and state.get("last_auto_date") != now.date().isoformat()


def _next_run(now: _dt.datetime, settings: dict, state: dict) -> str:
    if not settings["enabled"]:
        return ""
    if is_due(now, settings, state):
        return now.isoformat(timespec="minutes")   # 다음 틱(최대 10분 안)에 돈다
    at = now.replace(hour=settings["hour"], minute=0, second=0, microsecond=0)
    if state.get("last_auto_date") == now.date().isoformat():
        at += _dt.timedelta(days=1)
    return at.isoformat(timespec="minutes")


def get_schedule(now: _dt.datetime | None = None) -> dict:
    now = now or _dt.datetime.now()
    settings = schedule_settings()
    state = _load_state()
    return {**settings, "next_run_at": _next_run(now, settings, state),
            "last_auto_at": state.get("last_auto_at", ""), "last_result": state.get("last_result", ""),
            "last_alert_title": state.get("last_alert_title", ""), "last_error": state.get("last_error", "")}


def save_schedule(patch: dict) -> dict:
    settings = _clean_schedule({**schedule_settings(), **(patch or {})})
    _scan_dir().mkdir(parents=True, exist_ok=True)
    save_json(_schedule_path(), settings)
    return get_schedule()


def build_alert(report: dict, previous: dict | None, mode: str = "issues") -> dict | None:
    """보고서 → 관리자 알림 한 건(title/body/tone). 보낼 게 없으면 None."""
    findings = [f for f in (report.get("findings") or []) if isinstance(f, dict)]
    issues = [f for f in findings if f.get("severity") in ("high", "medium")]
    if not issues and mode != "always":
        return None
    counts = report.get("counts") or {}
    prev_ids = {f.get("id") for f in ((previous or {}).get("findings") or []) if isinstance(f, dict)}
    new_ids = {f["id"] for f in issues if previous and f.get("id") not in prev_ids}
    if issues:
        title = f"[운영 점검] 높음 {counts.get('high', 0)} · 보통 {counts.get('medium', 0)}"
        if new_ids:
            title += f" · 새 항목 {len(new_ids)}"
        ranked = sorted(issues, key=lambda f: (f["id"] not in new_ids, SEVERITY_ORDER.get(f["severity"], 9)))
        parts = [("새 · " if f["id"] in new_ids else "") + str(f.get("title") or f["id"])
                 for f in ranked[:ALERT_TOP_ITEMS]]
        if len(issues) > ALERT_TOP_ITEMS:
            parts.append(f"외 {len(issues) - ALERT_TOP_ITEMS}건")
        body = " / ".join(parts)
    else:
        title = "[운영 점검] 큰 문제 없음"
        body = f"낮음 {counts.get('low', 0)} · 참고 {counts.get('info', 0)}건"
    summary = str(((report.get("ai") or {}).get("summary")) or "").strip()
    if summary:
        body += f" — AI: {summary}"
    return {"title": title, "body": body[:400], "tone": "warning" if counts.get("high") else "info",
            "new_count": len(new_ids)}


def _admin_usernames() -> list[str]:
    import csv

    from core.paths import PATHS as _paths   # 테스트가 모듈 PATHS 를 data_root 만으로 바꿔 둔다
    out: list[str] = []
    try:
        with open(_paths.users_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                status = str(row.get("status") or "approved").strip()
                if row.get("role") == "admin" and status == "approved" and row.get("username"):
                    out.append(row["username"])
    except Exception:
        pass
    return out


def notify_admins(alert: dict, day: str) -> int:
    from core import notify

    sent = 0
    for username in _admin_usernames():
        try:
            if notify.emit_event(ALERT_EVENT, target_user=username, title=alert["title"], body=alert["body"],
                                 payload={"target_tab": "admin", "target_search": "?tab=ops_scan",
                                          "action_label": "운영 점검 보기"},
                                 notification_id=f"opsscan-{day}", tone=alert["tone"]):
                sent += 1
        except Exception:
            continue
    return sent


def run_scheduled(now: _dt.datetime | None = None) -> dict | None:
    """때가 됐으면 자동 점검 1회 + 관리자 알림. 안 돌았으면 None."""
    now = now or _dt.datetime.now()
    settings = schedule_settings()
    state = _load_state()
    if not is_due(now, settings, state):
        return None
    previous = load_json(_latest_path(), {})
    previous = previous if isinstance(previous, dict) and isinstance(previous.get("findings"), list) else None
    day = now.date().isoformat()
    try:
        report = run(actor="자동(매일)", use_ai=settings["use_ai"])
    except RuntimeError as exc:
        if str(exc) == "scan_in_progress":
            return None   # 관리자가 수동 스캔 중 — 다음 틱에 다시 본다
        raise
    except Exception as exc:
        # 같은 날 10분마다 재시도하며 로그를 채우지 않게 오늘 몫은 소진한다.
        state.update({"last_auto_date": day, "last_auto_at": _now_iso(), "last_result": "error",
                      "last_error": f"{type(exc).__name__}: {exc}"[:300]})
        _save_state(state)
        raise
    alert = build_alert(report, previous, settings["notify"])
    sent = notify_admins(alert, day) if alert else 0
    state.update({"last_auto_date": day, "last_auto_at": report["scanned_at"], "last_error": "",
                  "last_result": "notified" if sent else ("no_admin" if alert else "quiet"),
                  "last_alert_title": alert["title"] if alert else ""})
    _save_state(state)
    return {"report": report, "alert": alert, "sent": sent}


def start_scheduler() -> bool:
    """하루 1회 자동 점검 스레드. 중복 호출 무해. FLOW_DISABLE_OPS_SCAN_SCHEDULE=1 이면 끈다."""
    global _SCHEDULER_STARTED
    if os.environ.get("FLOW_DISABLE_OPS_SCAN_SCHEDULE") == "1":
        return False
    with _SCHEDULER_LOCK:
        if _SCHEDULER_STARTED:
            return False
        _SCHEDULER_STARTED = True
    import logging
    log = logging.getLogger("flow.ops_scan")

    def _loop() -> None:
        stop = threading.Event()
        stop.wait(SCHEDULER_STARTUP_DELAY_SECONDS)
        while True:
            try:
                result = run_scheduled()
                if result:
                    log.info("ops scan daily run: counts=%s alert=%s sent=%s", result["report"].get("counts"),
                             (result["alert"] or {}).get("title", "-"), result["sent"])
            except Exception:
                log.warning("ops scan daily run failed", exc_info=True)
            stop.wait(SCHEDULER_TICK_SECONDS)

    threading.Thread(target=_loop, name="ops-scan-daily", daemon=True).start()
    log.info("ops scan daily scheduler started (tick %.0fs)", SCHEDULER_TICK_SECONDS)
    return True
