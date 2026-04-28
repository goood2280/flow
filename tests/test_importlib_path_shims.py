from __future__ import annotations

import base64
import gzip
import importlib.util
import os
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


def _is_checkout_path(raw: str) -> bool:
    if not raw:
        return False
    try:
        resolved = Path(raw).resolve()
    except OSError:
        return False
    return resolved in {ROOT, BACKEND}


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


def test_direct_router_path_loads_find_app_v2_without_checkout_sys_path(tmp_path, monkeypatch):
    original_cwd = Path.cwd()
    original_sys_path = list(sys.path)
    monkeypatch.setenv("FLOW_APP_ROOT", str(ROOT))
    monkeypatch.setenv("FLOW_DATA_ROOT", str(tmp_path / "flow-data"))

    for rel_path, prefix in (
        ("backend/routers/informs.py", "/api/informs"),
        ("backend/routers/meetings.py", "/api/meetings"),
        ("backend/routers/tracker.py", "/api/tracker"),
    ):
        saved = _clear_imports(("core", "app_v2", "routers"))
        try:
            os.chdir(tmp_path)
            sys.path[:] = [p for p in original_sys_path if not _is_checkout_path(p)]

            module = _load_by_path(rel_path)

            assert module.router.prefix == prefix
        finally:
            os.chdir(original_cwd)
            sys.path[:] = original_sys_path
            _restore_imports(saved, ("core", "app_v2", "routers"))


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


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("_flow_setup_probe", ROOT / "setup.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_flow_setup_probe"] = module
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        sys.modules.pop("_flow_setup_probe", None)
        raise


def _gz_payload(text: str) -> str:
    return base64.b64encode(gzip.compress(text.encode("utf-8"), mtime=0)).decode("ascii")


def test_setup_write_guard_allows_app_v2_data_named_source_dirs(tmp_path):
    setup_module = _load_setup_module()
    try:
        setup_module.ROOT = tmp_path
        payload = _gz_payload("# probe\n")

        for module_name in ("informs", "tracker", "meetings"):
            rel = f"backend/app_v2/modules/{module_name}/probe.py"
            setup_module._write(rel, payload)

            assert (tmp_path / rel).read_text(encoding="utf-8") == "# probe\n"
    finally:
        sys.modules.pop("_flow_setup_probe", None)


def test_setup_write_guard_still_blocks_runtime_data_paths(tmp_path):
    setup_module = _load_setup_module()
    try:
        setup_module.ROOT = tmp_path
        payload = _gz_payload("# probe\n")

        setup_module._write("data/flow-data/informs/probe.py", payload)
        setup_module._write("backend/app_v2/modules/informs/config.json", payload)

        assert not (tmp_path / "data/flow-data/informs/probe.py").exists()
        assert not (tmp_path / "backend/app_v2/modules/informs/config.json").exists()
    finally:
        sys.modules.pop("_flow_setup_probe", None)
