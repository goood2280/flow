from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


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


def _is_backend_path(raw: str) -> bool:
    if not raw:
        return False
    try:
        return Path(raw).resolve() == BACKEND
    except OSError:
        return False


def _load_by_path(rel_path: str):
    path = ROOT / rel_path
    module_name = "_flow_importlib_probe_" + rel_path.replace("/", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load importlib spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def test_importlib_path_loads_backend_modules_without_backend_sys_path():
    saved = _clear_imports(("core", "app_v2", "routers"))
    original_sys_path = list(sys.path)
    try:
        sys.path[:] = [p for p in original_sys_path if not _is_backend_path(p)]
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        paths = _load_by_path("backend/core/paths.py")
        scheduler = _load_by_path("backend/scheduler.py")
        source_adapter = _load_by_path("backend/app_v2/shared/source_adapter.py")

        assert paths.PATHS.app_root == ROOT
        assert scheduler.PATHS.app_root == ROOT
        assert source_adapter.PATHS.app_root == ROOT
    finally:
        sys.path[:] = original_sys_path
        _restore_imports(saved, ("core", "app_v2", "routers"))


def test_setup_builder_includes_root_import_shims():
    spec = importlib.util.spec_from_file_location("_flow_build_setup_probe", ROOT / "_build_setup.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_flow_build_setup_probe"] = module
    try:
        spec.loader.exec_module(module)
        bundled = {module.to_rel_posix(path) for path in module.gather_files()}
    finally:
        sys.modules.pop("_flow_build_setup_probe", None)

    assert {"core/__init__.py", "routers/__init__.py", "app_v2/__init__.py"}.issubset(bundled)
