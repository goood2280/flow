"""FabCanvas.ai Backend v4.0.0 — uvicorn app:app --host 0.0.0.0 --port 8080"""
import os, importlib, logging
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from core.paths import PATHS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
# legacy HOL prefix kept for logger name to avoid disturbing existing log filters; brand is now FabCanvas.ai
logger = logging.getLogger("holweb")

app = FastAPI(title="FabCanvas.ai", version="4.0.0")

# ── Dynamic Router Loading ──
ROUTERS_DIR = Path(__file__).parent / "routers"
loaded, failed = [], []
for f in sorted(ROUTERS_DIR.glob("*.py")):
    if f.name.startswith("_"):
        continue
    module_name = f"routers.{f.stem}"
    try:
        mod = importlib.import_module(module_name)
        if hasattr(mod, "router"):
            app.include_router(mod.router)
            loaded.append(f.stem)
        # v8.2.1: some routers expose additional routers (e.g. match_router on catalog)
        for extra_name in ("match_router",):
            extra = getattr(mod, extra_name, None)
            if extra is not None:
                app.include_router(extra)
                loaded.append(f"{f.stem}:{extra_name}")
    except Exception as e:
        failed.append((f.stem, str(e)))
        logger.warning(f"Router load failed: {module_name} — {e}")

logger.info(f"Loaded routers: {loaded}")
if failed:
    logger.warning(f"Failed routers: {failed}")

# v7.3: log resolved paths (critical for prod vs dev confusion)
logger.info(f"FabCanvas.ai paths — prod={PATHS.is_prod}")
logger.info(f"  app_root  = {PATHS.app_root}")
logger.info(f"  data_root = {PATHS.data_root}")
logger.info(f"  db_root   = {PATHS.db_root}")


@app.get("/version.json")
def serve_version():
    vp = Path(__file__).parent.parent / "version.json"
    if vp.exists():
        return FileResponse(str(vp), media_type="application/json")
    return {"version": "4.0.0"}


# ── Serve React build ──
DIST = Path(__file__).parent.parent / "frontend" / "dist"
if DIST.exists():
    if (DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{path:path}")
    def serve_spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API not found")
        fp = DIST / path
        if fp.exists() and fp.is_file():
            return FileResponse(str(fp))
        return FileResponse(str(DIST / "index.html"))

# ── Seed admin user ──
from routers.auth import read_users, write_users, hash_pw
import datetime
users = read_users()
if not any(u["username"] == "hol" for u in users):
    users.append({
        "username": "hol", "password_hash": hash_pw("hol12345!"),
        "role": "admin", "status": "approved",
        "created": datetime.datetime.now().isoformat(),
        "tabs": "__all__",
    })
    write_users(users)
    logger.info("Admin user 'hol' created")
