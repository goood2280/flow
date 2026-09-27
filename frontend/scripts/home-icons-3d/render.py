"""Bake the home app icons as light isometric 3D renders.

    python frontend/scripts/home-icons-3d/render.py [key ...]

Reads the shape definitions (ICONS / FALLBACK) straight from
src/features/home/HomeAppIcons.jsx and each app's tile family from
src/app/pageManifest.jsx, renders every icon with three.js (scene.js) in a
headless Chrome, and writes transparent WebP files to
src/features/home/icons3d/<key>.webp. HomeAppIcon shows the baked image when it
exists and falls back to the SVG pictogram otherwise, so re-run this after
changing an icon shape or adding a page.

Dev-only requirements: `pip install playwright pillow` and a local Chrome.
"""
import io
import mimetypes
import pathlib
import re
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parents[1]
ICON_SOURCE = FRONTEND / "src" / "features" / "home" / "HomeAppIcons.jsx"
MANIFEST = FRONTEND / "src" / "app" / "pageManifest.jsx"
THREE_DIR = FRONTEND / "node_modules" / "three"
OUT_DIR = FRONTEND / "src" / "features" / "home" / "icons3d"
SIZE = 240

HELPERS = ("box, cylinder, sphere, prismZ, prismY, drum, top, frontLeft, topLine, frontLeftLine, disc, "
           "project, ellipse, polyline, polygon, poly3, pt, rect, grid, circle, gear")


def shapes_module() -> str:
    source = ICON_SOURCE.read_text(encoding="utf-8")
    start = source.index("const ICONS = {")
    fallback = source.index("const FALLBACK = () => [")
    end = source.index("];", fallback) + 2
    return (f"export default function buildShapes({{ {HELPERS} }}) {{\n"
            f"{source[start:end]}\nreturn {{ ICONS, FALLBACK }};\n}}\n")


def icon_keys() -> list[str]:
    block = shapes_module()
    return re.findall(r"^  (\w+): \(\) =>", block, flags=re.M)


def tile_groups() -> dict[str, str]:
    groups = {}
    for key, group in re.findall(r'key:\s*"(\w+)"[^\n]*?group:\s*"(\w+)"', MANIFEST.read_text(encoding="utf-8")):
        # My_Home.jsx: only data and system keep their family; every other group is a work tile.
        groups[key] = group if group in ("data", "system") else "work"
    return groups


def main(selected: list[str]) -> None:
    module = shapes_module().encode("utf-8")
    keys = [k for k in icon_keys() if not selected or k in selected]
    groups = tile_groups()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def serve(route):
        path = route.request.url.split("http://icons.local/", 1)[1].split("?")[0]
        if path == "icon-shapes.js":
            return route.fulfill(body=module, content_type="application/javascript")
        file = THREE_DIR / path[len("three/"):] if path.startswith("three/") else HERE / path
        route.fulfill(path=str(file), content_type=mimetypes.guess_type(str(file))[0] or "application/javascript")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        context = browser.new_context(viewport={"width": SIZE, "height": SIZE}, device_scale_factor=1)
        context.route("http://icons.local/**", serve)
        for key in keys:
            page = context.new_page()
            page.on("pageerror", lambda error, k=key: print(f"  {k}: {error}"))
            page.goto(f"http://icons.local/scene.html?key={key}&group={groups.get(key, 'work')}&size={SIZE}")
            page.wait_for_function("window.__done === true", timeout=120_000)
            png = page.locator("canvas").screenshot(omit_background=True)
            Image.open(io.BytesIO(png)).convert("RGBA").save(OUT_DIR / f"{key}.webp", "WEBP", quality=90, method=6)
            print(f"{key:16s} {groups.get(key, 'work'):6s} -> icons3d/{key}.webp")
            page.close()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
