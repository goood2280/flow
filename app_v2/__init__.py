"""Compatibility package for root-based imports.

The real ``app_v2`` package lives under ``backend/app_v2``.  This shim keeps
top-level absolute imports working when modules are loaded directly by path
from the repository root.
"""
from pathlib import Path

_TARGET = Path(__file__).resolve().parents[1] / "backend" / "app_v2"
if not _TARGET.is_dir():
    raise ImportError(f"Cannot locate backend app_v2 package: {_TARGET}")

__path__ = [str(_TARGET)]
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__
