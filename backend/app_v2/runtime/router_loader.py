from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import sys
import traceback
import types

from fastapi import FastAPI


def _prepend_sys_path(path: Path) -> None:
    raw = str(path)
    sys.path[:] = [p for p in sys.path if p != raw]
    sys.path.insert(0, raw)


def _ensure_backend_import_path(routers_dir: Path) -> tuple[Path, Path]:
    """Ensure absolute imports like `core.*` and `routers.*` resolve.

    Routers intentionally use top-level absolute imports (`from core...`,
    `from routers...`) while living under backend/.  Keeping backend/ on
    sys.path here makes router loading robust even when app.py was imported
    from a different working directory or package context.
    """
    backend_root = routers_dir.resolve().parent
    app_root = backend_root.parent
    # Keep this checkout ahead of any older install/worktree that may already
    # be on sys.path. This matters when setup.py was copied to a new directory
    # and uvicorn is restarted from a shell that previously imported Flow.
    for path in (app_root, backend_root):
        _prepend_sys_path(path)
    return backend_root, app_root


def _module_path(module: object) -> Path | None:
    raw = getattr(module, "__file__", None)
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except OSError:
        return None


def _package_paths(module: object) -> list[Path]:
    paths = getattr(module, "__path__", None)
    if not paths:
        return []
    resolved: list[Path] = []
    for raw in paths:
        try:
            resolved.append(Path(raw).resolve())
        except OSError:
            continue
    return resolved


def _clear_package(package_name: str) -> None:
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            sys.modules.pop(name, None)


def _ensure_local_package(package_name: str, package_dir: Path) -> None:
    """Bind a top-level package name to the package beside this app."""

    package_dir = package_dir.resolve()
    existing = sys.modules.get(package_name)
    if existing is not None and package_dir in _package_paths(existing):
        return

    if existing is not None:
        _clear_package(package_name)

    init_file = package_dir / "__init__.py"
    if init_file.exists():
        spec = importlib.util.spec_from_file_location(
            package_name,
            init_file,
            submodule_search_locations=[str(package_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot locate package spec for {package_name}: {init_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
        return

    module = types.ModuleType(package_name)
    module.__package__ = package_name
    module.__path__ = [str(package_dir)]  # type: ignore[attr-defined]
    spec = importlib.machinery.ModuleSpec(package_name, loader=None, is_package=True)
    spec.submodule_search_locations = [str(package_dir)]
    module.__spec__ = spec
    sys.modules[package_name] = module


def _import_module_from_path(module_name: str, file_path: Path):
    file_path = file_path.resolve()
    existing = sys.modules.get(module_name)
    if existing is not None and _module_path(existing) == file_path:
        return existing

    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot locate module spec for {module_name}: {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


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

    routers_dir = routers_dir.resolve()
    backend_root, app_root = _ensure_backend_import_path(routers_dir)
    for package_name, package_dir in (
        ("core", backend_root / "core"),
        ("app_v2", backend_root / "app_v2"),
        ("routers", routers_dir),
    ):
        if package_dir.is_dir():
            _ensure_local_package(package_name, package_dir)
    loaded: list[str] = []
    failed: list[tuple[str, str]] = []
    for file_path in sorted(routers_dir.glob("*.py")):
        if file_path.name.startswith("_"):
            continue
        module_name = f"routers.{file_path.stem}"
        try:
            module = _import_module_from_path(module_name, file_path)
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
