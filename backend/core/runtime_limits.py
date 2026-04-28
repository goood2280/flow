"""Runtime resource defaults for small Flow deployments.

These defaults are intentionally conservative and can be overridden by the
operator through environment variables. They must run before importing Polars,
NumPy, or other native compute libraries.
"""
from __future__ import annotations

import os


def _default_polars_threads() -> str:
    raw = os.environ.get("FLOW_POLARS_MAX_THREADS", "").strip()
    if raw:
        return raw
    cores = os.cpu_count() or 2
    # Keep one core free for uvicorn/event loop/OS on 2-core hosts, while still
    # allowing modest parallelism on larger boxes.
    return str(max(1, min(4, cores - 1)))


def apply_runtime_limits() -> None:
    """Apply CPU thread defaults unless the deploy already set explicit values."""
    os.environ.setdefault("POLARS_MAX_THREADS", _default_polars_threads())
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        flow_name = f"FLOW_{name}"
        os.environ.setdefault(name, os.environ.get(flow_name, "1"))
