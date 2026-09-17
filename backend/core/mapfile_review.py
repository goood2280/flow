"""Small traffic snapshots and durable comments attached to opened content versions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
from pathlib import Path
import threading
import time
import uuid

from core import mapfile_traffic as traffic
from core.file_transaction import file_transaction
from core.utils import load_json, save_json

REFRESH_SECONDS = 300
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mapfile-summary")
_guard = threading.Lock()
_pending: set[str] = set()


def _path(kind: str, *identity: str) -> Path:
    key = hashlib.sha256("\0".join(identity).encode("utf-8")).hexdigest()
    return traffic.get_traffic_cache_path().parent / kind / f"{key}.json"


def publish_snapshot(result: dict) -> dict:
    """Publish atomically; readers never acquire the expensive verification lock."""
    def compact(entry):
        row = {key: entry.get(key) for key in (
            "filename", "rel_path", "signature", "dc_type", "dc_label",
            "traffic_light", "verified_at", "status", "error", "mtime")}
        row["checked_at"] = entry.get("verified_at", "")
        sl = entry.get("sl") or {}
        main = entry.get("main") or {}
        summary = entry.get("summary") or {}
        row["sl"] = {"light": sl.get("light", "gray"), "red": int(sl.get("red") or 0)}
        row["main"] = {"light": main.get("light", "gray"), "red": int(main.get("red") or 0)}
        row["mismatch_count"] = int(summary.get("red") or row["sl"]["red"] + row["main"]["red"])
        reasons = list(dict.fromkeys(str(issue.get("reason") or issue.get("status") or "")
                                   for issue in entry.get("issues", [])))
        row["comment"] = str(entry.get("error") or " · ".join(filter(None, reasons[:3]))
                             or ("검사 완료" if entry.get("status") == "ok" else "검사 대기"))[:500]
        return row

    snapshot = {key: result.get(key) for key in (
        "vehicle", "product_code", "overall_light", "summary", "github_sync")}
    snapshot.update(ok=True, checked_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    checked_epoch=time.time(), error="")
    snapshot["files"] = [compact(row) for row in result.get("files", [])]
    snapshot["groups"] = [dict(group, files=[compact(row) for row in group["files"]])
                          for group in result.get("groups", [])]
    path = _path("traffic_snapshots", result["vehicle"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_transaction(path):
        save_json(path, snapshot)
    return snapshot


def _refresh(vehicle: str, force: bool = False) -> None:
    path = _path("traffic_snapshots", vehicle)
    try:
        # Other workers/processes recheck freshness after acquiring this lock.
        with file_transaction(path):
            previous = load_json(path, {}) or {}
            if not force and time.time() - previous.get("checked_epoch", 0) < REFRESH_SECONDS:
                return
            result = traffic.inspect_mapfiles_for_product(vehicle, force=force)
            publish_snapshot(result)
    except Exception:
        traffic.logger.exception("Background Mapfile refresh failed for %s", vehicle)
        with file_transaction(path):
            previous = load_json(path, {}) or {}
            if time.time() - previous.get("checked_epoch", 0) >= REFRESH_SECONDS:
                previous.update(error="주기검사 실패 — 이전 결과입니다. 다음 검사에서 재시도합니다.",
                                checked_epoch=time.time())
                save_json(path, previous)
    finally:
        with _guard:
            _pending.discard(str(path))


def get_summary(vehicle: str, force: bool = False) -> dict:
    path = _path("traffic_snapshots", vehicle)
    snapshot = load_json(path, {}) or {}
    key = str(path)
    with _guard:
        needs_refresh = force or time.time() - snapshot.get("checked_epoch", 0) >= REFRESH_SECONDS
        if needs_refresh and key not in _pending:
            _pending.add(key)
            _pool.submit(_refresh, vehicle, force)
        refreshing = key in _pending
    # Read only tiny version-specific comment summaries, never the Mapfile or
    # unbounded comment history. Keep comments fresh without rerunning inspection.
    summaries = {}
    for row in snapshot.get("files", []):
        identity = (vehicle, row.get("rel_path") or row.get("filename", ""), row.get("signature", ""))
        summaries[identity[1:]] = load_json(_path("mapfile_comment_summaries", *identity), {}) or {}
        row["comment_summary"] = summaries[identity[1:]]
    for group in snapshot.get("groups", []):
        for row in group.get("files", []):
            row["comment_summary"] = summaries.get((row.get("rel_path") or row.get("filename", ""),
                                                   row.get("signature", "")), {})
    return {"ok": True, "vehicle": vehicle, "files": [], "groups": [],
            "overall_light": "gray", "checked_at": "", **snapshot, "refreshing": refreshing}


def open_version(vehicle: str, filename: str, version: str = "") -> dict:
    """Only open a file belonging to the authorized product; hash the bytes returned."""
    normalized = filename.replace("\\", "/")
    if normalized.startswith("/") or any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ValueError("잘못된 Mapfile 경로입니다")
    root = traffic.get_mapfile_dir().resolve()
    _, paths = traffic.list_mapfiles_for_product(vehicle)
    matches = [p for p in paths if p.resolve().is_relative_to(root)
               and p.resolve().relative_to(root).as_posix() == normalized]
    if not matches:
        raise FileNotFoundError("이 제품의 Mapfile을 찾을 수 없습니다")
    path = matches[0]
    raw = path.read_bytes()
    signature = f"{path.name}:sha256:{hashlib.sha256(raw).hexdigest()}"
    if version and signature != version:
        raise ValueError("파일 버전이 변경되었습니다. 신호등을 새로 조회한 뒤 열어 주세요.")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp949", errors="replace")
    record_path = _path("mapfile_versions", vehicle, normalized, signature)
    with file_transaction(record_path):
        if not record_path.exists():
            save_json(record_path, {"vehicle": vehicle, "filename": normalized,
                                   "signature": signature, "comments": []})
    return {"ok": True, "filename": normalized, "signature": signature, "content": content}


def version_comments(vehicle: str, filename: str, version: str, *, text: str | None = None,
                     user: dict | None = None) -> dict:
    path = _path("mapfile_versions", vehicle, filename, version)
    with file_transaction(path):
        record = load_json(path, None)
        if not record:
            raise FileNotFoundError("Mapfile 검증 탭에서 해당 버전을 먼저 열어 주세요")
        if text is not None:
            text = text.strip()
            if not text or len(text) > 4000:
                raise ValueError("코멘트는 1~4000자로 입력해 주세요")
            username = str((user or {}).get("username") or "").strip()
            if not username:
                raise ValueError("작성자 계정을 확인할 수 없습니다")
            name = str((user or {}).get("name") or "").strip()
            record["comments"].append({"id": uuid.uuid4().hex, "text": text,
                "author": f"{name} ({username})" if name else username,
                "username": username,
                "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
            save_json(path, record)
            save_json(_path("mapfile_comment_summaries", vehicle, filename, version),
                      {"count": len(record["comments"]), "latest": record["comments"][-1]})
        return {"ok": True, "comments": record["comments"]}
