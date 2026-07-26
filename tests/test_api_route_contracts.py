from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import informs, tracker  # noqa: E402


def _route_methods(router) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for route in router.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", set()) or set())
        if path:
            out.setdefault(path, set()).update(methods)
    return out


def test_informs_route_contract_includes_operational_reads():
    routes = _route_methods(informs.router)

    for path in [
        "/api/informs/config",
        "/api/informs/my-modules",
        "/api/informs/wafers",
        "/api/informs/products",
        "/api/informs/lots",
        "/api/informs/recent",
        "/api/informs/product-contacts",
        "/api/informs/modules",
        "/api/informs/user-modules",
    ]:
        assert "GET" in routes.get(path, set())


def test_informs_product_add_accepts_plural_and_singular_compat_paths():
    routes = _route_methods(informs.router)

    for path in ["/api/informs/products/add", "/api/informs/product/add"]:
        assert {"POST", "PUT", "PATCH", "GET"}.issubset(routes.get(path, set()))


def test_tracker_route_contract_includes_page_bootstrap_reads():
    routes = _route_methods(tracker.router)

    for path in [
        "/api/tracker",
        "/api/tracker/categories",
        "/api/tracker/db-sources",
        "/api/tracker/issues",
        "/api/tracker/products",
        "/api/tracker/scheduler",
    ]:
        assert "GET" in routes.get(path, set())
