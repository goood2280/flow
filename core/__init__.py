"""Compatibility package for root-based imports.

The backend source tree keeps the real ``core`` package under ``backend/core``,
while legacy modules import it as a top-level package.  Point this package at
that directory so direct ``importlib`` path loads from the repository root do
not require a separate PYTHONPATH tweak.
"""
from pathlib import Path

_TARGET = Path(__file__).resolve().parents[1] / "backend" / "core"
if not _TARGET.is_dir():
    raise ImportError(f"Cannot locate backend core package: {_TARGET}")

__path__ = [str(_TARGET)]
if __spec__ is not None:
    __spec__.submodule_search_locations = __path__
