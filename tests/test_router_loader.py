from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app_v2.runtime.router_loader import include_router_modules  # noqa: E402


def _clear_imports(prefixes: tuple[str, ...]) -> dict[str, object]:
    saved = {}
    for name in list(sys.modules):
        if name in prefixes or any(name.startswith(prefix + ".") for prefix in prefixes):
            saved[name] = sys.modules.pop(name)
    return saved


def _restore_imports(saved: dict[str, object], prefixes: tuple[str, ...]) -> None:
    for name in list(sys.modules):
        if name in prefixes or any(name.startswith(prefix + ".") for prefix in prefixes):
            sys.modules.pop(name, None)
    sys.modules.update(saved)


def test_router_loader_adds_backend_root_for_absolute_core_import(tmp_path):
    backend = tmp_path / "backend"
    routers_dir = backend / "routers"
    core_dir = backend / "core"
    routers_dir.mkdir(parents=True)
    core_dir.mkdir()
    (routers_dir / "__init__.py").write_text("", encoding="utf-8")
    (core_dir / "__init__.py").write_text("", encoding="utf-8")
    (core_dir / "fake.py").write_text("VALUE = 7\n", encoding="utf-8")
    (routers_dir / "probe.py").write_text(
        "from fastapi import APIRouter\n"
        "from core.fake import VALUE\n\n"
        "router = APIRouter(prefix='/probe')\n\n"
        "@router.get('/value')\n"
        "def value():\n"
        "    return {'value': VALUE}\n",
        encoding="utf-8",
    )

    saved = _clear_imports(("core", "routers"))
    original_sys_path = list(sys.path)
    try:
        app = FastAPI()
        loaded, failed = include_router_modules(app, routers_dir, logging.getLogger("test.router_loader"))

        assert loaded == ["probe"]
        assert failed == []
    finally:
        sys.path[:] = original_sys_path
        _restore_imports(saved, ("core", "routers"))


def test_router_loader_failure_includes_traceback(tmp_path):
    backend = tmp_path / "backend"
    routers_dir = backend / "routers"
    routers_dir.mkdir(parents=True)
    (routers_dir / "__init__.py").write_text("", encoding="utf-8")
    (routers_dir / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    saved = _clear_imports(("routers",))
    original_sys_path = list(sys.path)
    try:
        app = FastAPI()
        loaded, failed = include_router_modules(app, routers_dir, logging.getLogger("test.router_loader"))

        assert loaded == []
        assert len(failed) == 1
        name, detail = failed[0]
        assert name == "broken"
        assert "module=routers.broken" in detail
        assert "RuntimeError: boom" in detail
        assert "Traceback" in detail
        assert "backend_root=" in detail
    finally:
        sys.path[:] = original_sys_path
        _restore_imports(saved, ("routers",))


def test_router_loader_uses_requested_router_directory_when_package_is_stale(tmp_path):
    stale_backend = tmp_path / "stale" / "backend"
    stale_routers = stale_backend / "routers"
    stale_routers.mkdir(parents=True)
    (stale_routers / "__init__.py").write_text("", encoding="utf-8")
    (stale_routers / "probe.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter(prefix='/wrong')\n\n"
        "@router.get('/value')\n"
        "def value():\n"
        "    return {'value': 'wrong'}\n",
        encoding="utf-8",
    )

    backend = tmp_path / "backend"
    routers_dir = backend / "routers"
    routers_dir.mkdir(parents=True)
    (routers_dir / "__init__.py").write_text("", encoding="utf-8")
    (routers_dir / "probe.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter(prefix='/target')\n\n"
        "@router.get('/value')\n"
        "def value():\n"
        "    return {'value': 'target'}\n",
        encoding="utf-8",
    )

    saved = _clear_imports(("routers",))
    original_sys_path = list(sys.path)
    try:
        sys.path.insert(0, str(stale_backend))
        importlib.import_module("routers")

        app = FastAPI()
        loaded, failed = include_router_modules(app, routers_dir, logging.getLogger("test.router_loader"))

        route_paths = {route.path for route in app.routes}
        assert loaded == ["probe"]
        assert failed == []
        assert "/target/value" in route_paths
        assert "/wrong/value" not in route_paths
    finally:
        sys.path[:] = original_sys_path
        _restore_imports(saved, ("routers",))
