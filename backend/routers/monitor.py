"""routers/monitor.py v4.0.0 - System monitor + heartbeat + farming"""
import os, time, datetime, threading
from pathlib import Path
from fastapi import APIRouter, Query
from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, jsonl_trim, load_json, save_json

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

RESOURCE_LOG = PATHS.resource_log
FARM_STATUS_FILE = PATHS.log_dir / "farm_status.json"
RESOURCE_LOG.parent.mkdir(parents=True, exist_ok=True)

_prev_cpu = None


def _read_proc_cpu():
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        return [int(x) for x in parts[1:8]]
    except Exception:
        return [0] * 7


def _read_memory():
    try:
        cg = Path("/sys/fs/cgroup")
        usage = int((cg / "memory.current").read_text().strip())
        try:
            limit = int((cg / "memory.max").read_text().strip())
        except Exception:
            limit = usage * 2
        return usage, limit
    except Exception:
        try:
            lines = Path("/proc/meminfo").read_text()
            total = int(lines.split("MemTotal:")[1].split()[0]) * 1024
            avail = int(lines.split("MemAvailable:")[1].split()[0]) * 1024
            return total - avail, total
        except Exception:
            return 0, 1


def _read_disk():
    try:
        st = os.statvfs("/config")
        total = st.f_blocks * st.f_frsize
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        return used, total
    except Exception:
        return 0, 1


@router.get("/system")
def system_info():
    global _prev_cpu
    cur = _read_proc_cpu()
    cpu_pct = 0.0
    if _prev_cpu:
        d = [c - p for c, p in zip(cur, _prev_cpu)]
        total = sum(d)
        idle = d[3] if len(d) > 3 else 0
        cpu_pct = round((1 - idle / max(total, 1)) * 100, 1)
    _prev_cpu = cur
    mem_used, mem_total = _read_memory()
    disk_used, disk_total = _read_disk()
    return {
        "cpu_percent": cpu_pct,
        "memory_used_gb": round(mem_used / 1e9, 2),
        "memory_total_gb": round(mem_total / 1e9, 2),
        "memory_percent": round(mem_used / max(mem_total, 1) * 100, 1),
        "disk_used_gb": round(disk_used / 1e9, 2),
        "disk_total_gb": round(disk_total / 1e9, 2),
        "disk_percent": round(disk_used / max(disk_total, 1) * 100, 1),
    }


@router.get("/resource-log")
def resource_log(limit: int = Query(200)):
    return {"logs": jsonl_read(RESOURCE_LOG, limit)}


def _log_resource():
    """Append current system stats to resource log."""
    info = system_info()
    jsonl_append(RESOURCE_LOG, info)
    jsonl_trim(RESOURCE_LOG, 2000)
    info["timestamp"] = datetime.datetime.now().isoformat()
    return info


def _check_need_farming():
    """Check if all CPU & memory in last 24h stayed below 85%."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
    entries = jsonl_read(RESOURCE_LOG, 0, lambda e: e.get("timestamp", "") >= cutoff)
    if len(entries) < 6:
        return False
    for e in entries:
        if e.get("cpu_percent", 0) > 85 or e.get("memory_percent", 0) > 85:
            return False
    return True


def _do_farming():
    """Run CPU-intensive dummy work for ~5 minutes."""
    now = datetime.datetime.now()
    save_json(FARM_STATUS_FILE, {
        "farming": True, "started": now.isoformat(),
        "estimated_end": (now + datetime.timedelta(minutes=5)).isoformat(),
    })
    end_time = time.time() + 300
    while time.time() < end_time:
        _ = sum(i * i for i in range(2_000_000))
        big_list = [0] * 5_000_000
        del big_list
        time.sleep(0.1)
    save_json(FARM_STATUS_FILE, {
        "farming": False, "ended": datetime.datetime.now().isoformat(),
    })


@router.post("/heartbeat")
def heartbeat():
    """Log resource + check if farming needed (call every 5 min via cron)."""
    info = _log_resource()
    farm_status = load_json(FARM_STATUS_FILE, {"farming": False})

    now = datetime.datetime.now()
    need_farm = False
    last_farm_check = farm_status.get("last_check", "")
    if last_farm_check:
        try:
            lc = datetime.datetime.fromisoformat(last_farm_check)
            if (now - lc).total_seconds() >= 5 * 3600:
                need_farm = _check_need_farming()
        except Exception:
            need_farm = _check_need_farming()
    else:
        need_farm = _check_need_farming()

    farm_status["last_check"] = now.isoformat()
    next_check = (now + datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    farm_status["next_check"] = next_check

    if need_farm and not farm_status.get("farming"):
        farm_status["farming"] = True
        farm_status["started"] = now.isoformat()
        farm_status["estimated_end"] = (now + datetime.timedelta(minutes=5)).isoformat()
        save_json(FARM_STATUS_FILE, farm_status)
        threading.Thread(target=_do_farming, daemon=True).start()
    else:
        save_json(FARM_STATUS_FILE, farm_status)

    return {
        "ok": True, "timestamp": info["timestamp"],
        "farming": farm_status.get("farming", False),
        "next_check": next_check,
        "checksum": sum(i * i for i in range(100_000)) % 9999,
    }


@router.get("/farm-status")
def get_farm_status():
    return load_json(FARM_STATUS_FILE, {"farming": False, "next_check": "unknown"})
