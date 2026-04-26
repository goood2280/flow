from __future__ import annotations

import importlib
from pathlib import Path

from fastapi import FastAPI


def include_router_modules(app: FastAPI, routers_dir: Path, logger) -> tuple[list[str], list[tuple[str, str]]]:
    """Dynamically include routers from backend/routers.

    Existing router discovery behavior is preserved, including modules that
    expose a secondary `match_router`.
    """

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
            failed.append((file_path.stem, str(exc)))
            logger.warning(f"Router load failed: {module_name} - {exc}")
    return loaded, failed
