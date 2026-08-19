"""Execute mechanically split legacy router sources in one module namespace.

Flow's oldest routers accumulated thousands of lines while callers, schedulers,
and tests imported their module-level helpers directly.  Moving those helpers to
independent modules in one change would alter import order, singleton caches, and
monkeypatch targets.  The first migration stage therefore stores cohesive source
parts separately but executes them in the public router module's namespace.

This is intentionally a compatibility bridge, not the final service boundary.
New code belongs in ``app_v2.modules.<feature>`` and should be imported by the
parts.  Existing symbols remain available as ``routers.<feature>.<symbol>`` until
their callers have migrated.
"""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from pathlib import Path


PART_SUFFIX = ".part.py"


def execute_module_parts(
    module_globals: MutableMapping[str, object],
    parts: Iterable[Path],
) -> tuple[str, ...]:
    """Execute *parts* in order using one shared legacy module namespace.

    ``__file__`` and ``__name__`` deliberately remain those of the public router
    module.  Existing path resolution and direct imports therefore keep their
    previous behaviour, while tracebacks still identify the physical part file
    through the filename passed to :func:`compile`.
    """

    loaded: list[str] = []
    for raw_path in parts:
        path = Path(raw_path)
        if not path.is_file():
            raise RuntimeError(f"router source part is missing: {path}")
        source = path.read_text(encoding="utf-8")
        code = compile(source, str(path), "exec")
        exec(code, module_globals, module_globals)
        loaded.append(path.name)
    module_globals["__flow_source_parts__"] = tuple(loaded)
    return tuple(loaded)


def ordered_part_paths(parts_dir: Path) -> tuple[Path, ...]:
    """Return deterministic source parts and fail loudly for an empty bundle."""

    root = Path(parts_dir)
    parts = tuple(sorted(root.glob(f"*{PART_SUFFIX}")))
    if not parts:
        raise RuntimeError(f"no router source parts found under {root}")
    return parts
