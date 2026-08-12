"""Compatibility package for root-based imports.

The real router modules live under ``backend/routers``.  This shim lets
``from routers...`` imports resolve when importlib loads backend files from the
repository root without adding ``backend/`` to ``sys.path`` first.
"""
from pathlib import Path

_TARGET = Path(__file__).resolve().parents[1] / "backend" / "routers"
if not _TARGET.is_dir():
    raise ImportError(f"Cannot locate backend routers package: {_TARGET}")

__path__ = [str(_TARGET)]
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__
