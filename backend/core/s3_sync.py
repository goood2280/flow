"""core/s3_sync.py v7.3 — Push artifacts (reformatters, matching tables, product YAMLs)
to S3 so the remote compute backend can read them.

boto3 is optional: if not installed, the sync path logs the intent and marks
each file as "queued" in the local status JSON. This lets engineers configure
and preview the sync without needing AWS creds on dev machines.

Config lives at `data/flow-data/s3_sync.json`:
  {
    "bucket": "my-bucket",
    "prefix": "flow/artifacts/",
    "region": "ap-northeast-2",
    "enabled": true
  }

Status (append-only log) at `data/flow-data/s3_sync_status.jsonl`.
"""
from __future__ import annotations
import hashlib
import json
import logging
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger("flow.s3_sync")

try:
    import boto3 as _boto3
    _HAS_BOTO = True
except Exception:
    _boto3 = None
    _HAS_BOTO = False


# ──────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "bucket": "",
    "prefix": "flow/artifacts/",
    "region": "ap-northeast-2",
    "enabled": False,
    "profile": "",      # optional named AWS profile
}

SYNCABLE_DB_ROOT_FILES = {
    "matching_step.csv",
    "step_matching.csv",
    "knob_ppid.csv",
    "inline_matching.csv",
    "vm_matching.csv",
    "mask.csv",
    "inline_item_map.csv",
    "inline_step_match.csv",
    "inline_subitem_pos.csv",
    "yld_shot_agg.csv",
}


def load_config(data_root: Path) -> Dict[str, Any]:
    fp = data_root / "s3_sync.json"
    if not fp.exists():
        return DEFAULT_CONFIG.copy()
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        merged = DEFAULT_CONFIG.copy(); merged.update(d)
        return merged
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(data_root: Path, cfg: Dict[str, Any]) -> None:
    fp = data_root / "s3_sync.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# Status log
# ──────────────────────────────────────────────────────────────────
def _status_path(data_root: Path) -> Path:
    return data_root / "s3_sync_status.jsonl"


def _append_status(data_root: Path, entry: Dict[str, Any]) -> None:
    fp = _status_path(data_root)
    fp.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.datetime.now().isoformat()
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recent_status(data_root: Path, limit: int = 50) -> List[Dict[str, Any]]:
    fp = _status_path(data_root)
    if not fp.exists():
        return []
    lines = fp.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for ln in lines:
        try: out.append(json.loads(ln))
        except Exception: pass
    return out


def last_sync_index(data_root: Path) -> Dict[str, Dict[str, Any]]:
    """Return {artifact_key: last_status} — latest entry per file."""
    idx = {}
    for e in recent_status(data_root, limit=5000):
        k = e.get("key") or e.get("file", "")
        if k:
            idx[k] = e  # later entries overwrite → ends with latest
    return idx


# ──────────────────────────────────────────────────────────────────
# Artifact discovery
# ──────────────────────────────────────────────────────────────────
def list_artifacts(data_root: Path, db_root: Path) -> List[Dict[str, Any]]:
    """Discover all syncable artifacts grouped by type.

    Types:
      reformatter       data/flow-data/reformatter/*.json
      matching          <db_root>/matching/*.csv
      product_config    data/flow-data/product_config/*.yaml
    """
    out = []
    # reformatter
    rf_dir = data_root / "reformatter"
    if rf_dir.exists():
        for fp in sorted(rf_dir.glob("*.json")):
            out.append({
                "type": "reformatter", "product": fp.stem,
                "path": str(fp), "key": f"reformatter/{fp.name}",
                "size": fp.stat().st_size,
                "sha1": _sha1(fp),
                "mtime": fp.stat().st_mtime,
            })
    # matching (legacy subdir)
    mt_dir = db_root / "matching"
    if mt_dir.exists():
        for fp in sorted(mt_dir.glob("*.csv")):
            out.append({
                "type": "matching", "product": "",
                "path": str(fp), "key": f"matching/{fp.name}",
                "size": fp.stat().st_size,
                "sha1": _sha1(fp),
                "mtime": fp.stat().st_mtime,
            })
    # matching / rulebook (current db_root direct files)
    if db_root.exists():
        for name in sorted(SYNCABLE_DB_ROOT_FILES):
            fp = db_root / name
            if not fp.exists() or not fp.is_file():
                continue
            out.append({
                "type": "matching", "product": "",
                "path": str(fp), "key": f"matching/{fp.name}",
                "size": fp.stat().st_size,
                "sha1": _sha1(fp),
                "mtime": fp.stat().st_mtime,
            })
    # product_config (yaml)
    yc_dir = data_root / "product_config"
    if yc_dir.exists():
        for fp in sorted(yc_dir.glob("*.yaml")):
            out.append({
                "type": "product_config", "product": fp.stem,
                "path": str(fp), "key": f"product_config/{fp.name}",
                "size": fp.stat().st_size,
                "sha1": _sha1(fp),
                "mtime": fp.stat().st_mtime,
            })
    return out


def _sha1(fp: Path) -> str:
    h = hashlib.sha1()
    try:
        with fp.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return ""


def artifact_from_path(data_root: Path, db_root: Path, path: Path) -> Optional[Dict[str, Any]]:
    fp = Path(path)
    if not fp.exists() or not fp.is_file():
        return None
    try:
        fp = fp.resolve()
    except Exception:
        pass
    try:
        data_root_r = data_root.resolve()
    except Exception:
        data_root_r = data_root
    try:
        db_root_r = db_root.resolve()
    except Exception:
        db_root_r = db_root

    try:
        rel = fp.relative_to(data_root_r)
        if rel.parts[:1] == ("reformatter",) and fp.suffix.lower() == ".json":
            return {
                "type": "reformatter", "product": fp.stem,
                "path": str(fp), "key": f"reformatter/{fp.name}",
                "size": fp.stat().st_size, "sha1": _sha1(fp), "mtime": fp.stat().st_mtime,
            }
        if rel.parts[:1] == ("product_config",) and fp.suffix.lower() in (".yaml", ".yml"):
            return {
                "type": "product_config", "product": fp.stem,
                "path": str(fp), "key": f"product_config/{fp.name}",
                "size": fp.stat().st_size, "sha1": _sha1(fp), "mtime": fp.stat().st_mtime,
            }
    except Exception:
        pass

    try:
        rel = fp.relative_to(db_root_r)
        if len(rel.parts) == 1 and fp.name in SYNCABLE_DB_ROOT_FILES:
            return {
                "type": "matching", "product": "",
                "path": str(fp), "key": f"matching/{fp.name}",
                "size": fp.stat().st_size, "sha1": _sha1(fp), "mtime": fp.stat().st_mtime,
            }
        if rel.parts[:1] == ("matching",) and fp.suffix.lower() == ".csv":
            return {
                "type": "matching", "product": "",
                "path": str(fp), "key": f"matching/{fp.name}",
                "size": fp.stat().st_size, "sha1": _sha1(fp), "mtime": fp.stat().st_mtime,
            }
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────────────────────────
# Sync action
# ──────────────────────────────────────────────────────────────────
def sync_one(data_root: Path, artifact: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    fp = Path(artifact["path"])
    key = cfg.get("prefix", "").rstrip("/") + "/" + artifact["key"]
    entry = {
        "key": artifact["key"], "s3_key": key, "type": artifact["type"],
        "sha1": artifact.get("sha1", ""), "size": artifact.get("size", 0),
    }
    if not cfg.get("enabled"):
        entry["status"] = "disabled"; _append_status(data_root, entry); return entry
    if not cfg.get("bucket"):
        entry["status"] = "no_bucket"; _append_status(data_root, entry); return entry
    if not _HAS_BOTO:
        entry["status"] = "queued"; entry["note"] = "boto3 not installed — logged only"
        _append_status(data_root, entry); return entry
    try:
        session = _boto3.Session(profile_name=cfg["profile"]) if cfg.get("profile") else _boto3.Session()
        s3 = session.client("s3", region_name=cfg.get("region"))
        s3.upload_file(str(fp), cfg["bucket"], key)
        entry["status"] = "uploaded"
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)[:300]
        logger.warning(f"S3 upload failed {key}: {e}")
    _append_status(data_root, entry)
    return entry


def sync_all(data_root: Path, db_root: Path, filter_type: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = load_config(data_root)
    arts = list_artifacts(data_root, db_root)
    if filter_type:
        arts = [a for a in arts if a["type"] == filter_type]
    results = []
    for a in arts:
        results.append(sync_one(data_root, a, cfg))
    return results


def sync_saved_path(data_root: Path, db_root: Path, path: Path) -> Dict[str, Any]:
    artifact = artifact_from_path(data_root, db_root, path)
    if not artifact:
        return {"ok": False, "status": "skipped", "reason": "not_syncable", "path": str(path)}
    cfg = load_config(data_root)
    result = sync_one(data_root, artifact, cfg)
    result["ok"] = result.get("status") in {"uploaded", "queued", "disabled", "no_bucket"}
    return result
