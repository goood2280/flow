from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import traceback

from fastapi import FastAPI


def _ensure_backend_import_path(routers_dir: Path) -> tuple[Path, Path]:
    """Ensure absolute imports like `core.*` and `routers.*` resolve.

    Routers intentionally use top-level absolute imports (`from core...`,
    `from routers...`) while living under backend/.  Keeping backend/ on
    sys.path here makes router loading robust even when app.py was imported
    from a different working directory or package context.
    """
    backend_root = routers_dir.resolve().parent
    app_root = backend_root.parent
    for path in (backend_root, app_root):
        raw = str(path)
        if raw not in sys.path:
            sys.path.insert(0, raw)
    return backend_root, app_root


def _format_import_error(module_name: str, file_path: Path, exc: Exception, backend_root: Path, app_root: Path) -> str:
    exc_type = type(exc).__name__
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    sys_path_head = [str(p) for p in sys.path[:8]]
    return (
        f"module={module_name}\n"
        f"file={file_path}\n"
        f"error_type={exc_type}\n"
        f"error={exc}\n"
        f"cwd={os.getcwd()}\n"
        f"backend_root={backend_root}\n"
        f"app_root={app_root}\n"
        f"sys_path_head={sys_path_head}\n"
        f"{tb}"
    )


def include_router_modules(app: FastAPI, routers_dir: Path, logger) -> tuple[list[str], list[tuple[str, str]]]:
    """Dynamically include routers from backend/routers.

    Existing router discovery behavior is preserved, including modules that
    expose a secondary `match_router`.
    """

    backend_root, app_root = _ensure_backend_import_path(routers_dir)
    loaded: list[str] = []
    failed: list[tuple[str, str]] = []
    for file_path in sorted(routers_dir.glob("*.py")):
        if file_path.name.startswith("_"):
            continue
        module_name = f"routers.{file_path.stem}"
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, "router"):
                app.include_router(module.router)
                loaded.append(file_path.stem)
            for extra_name in ("match_router",):
                extra_router = getattr(module, extra_name, None)
                if extra_router is not None:
                    app.include_router(extra_router)
                    loaded.append(f"{file_path.stem}:{extra_name}")
        except Exception as exc:
            detail = _format_import_error(module_name, file_path, exc, backend_root, app_root)
            failed.append((file_path.stem, detail))
            logger.error("Router load failed: %s\n%s", module_name, detail)
    return loaded, failed
