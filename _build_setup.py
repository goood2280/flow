#!/usr/bin/env python3
"""Builder: walks the FabCanvas.ai (flow) source tree and emits setup.py.

`setup.py` is the single self-extracting installer that bundles every source
file as gzip+base64. Run it on a fresh machine to extract + install + build:

    python setup.py                  # extract + pip install + npm build
    python setup.py --no-install     # extract only
    python setup.py --no-build       # extract + pip install (skip frontend build)
    uvicorn app:app --host 0.0.0.0 --port 8080

This builder must be re-run whenever ANY tracked source file changes. CI is
the eventual home for this; for now it is invoked manually before tagging.

NOTE: data/ is intentionally excluded — runtime data (인폼/트래커/달력/회의/
유저/세션 등) lives there and must never be overwritten by setup.
"""
import base64
import gzip
import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).parent

# ── Files to include ───────────────────────────────────────────────
INCLUDE_DIRS = [
    'backend/core',
    'backend/routers',
    'frontend/src',
    'frontend/src/components',
    'frontend/src/pages',
    'frontend/src/lib',
    'frontend/src/constants',
    'frontend/public',
    'docs',
]

# Exact file list at repo root
INCLUDE_FILES = [
    'README.md',
    'CHANGELOG.md',
    'FabCanvas_domain.txt',
    'app.py',                  # uvicorn shim (top-level)
    'backend/app.py',
    'frontend/index.html',
    'frontend/package.json',
    'frontend/vite.config.js',
]

# scripts/ — include only .py files, skip _req_*.json fixtures
SCRIPTS_GLOB = 'scripts/*.py'

# Patterns to exclude when walking dirs
EXCLUDE_PARTS = {'__pycache__', 'node_modules', 'dist', '.claude', '.git'}


def gather_files() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []

    def add(p: Path):
        if p in seen or not p.is_file():
            return
        seen.add(p)
        out.append(p)

    for rel in INCLUDE_FILES:
        add(ROOT / rel)

    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob('*'):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_PARTS for part in p.parts):
                continue
            if p.suffix in {'.pyc'}:
                continue
            add(p)

    if (ROOT / 'scripts').is_dir():
        for p in (ROOT / 'scripts').glob('*.py'):
            add(p)

    return sorted(out)


def encode(path: Path) -> str:
    data = path.read_bytes()
    gz = gzip.compress(data, compresslevel=9, mtime=0)
    return base64.b64encode(gz).decode('ascii')


def format_payload(b64: str, indent: int = 8) -> str:
    lines = textwrap.wrap(b64, width=72, break_long_words=True, break_on_hyphens=False)
    sp = ' ' * indent
    return '\n'.join(f"{sp}'{ln}'" for ln in lines)


def to_rel_posix(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def build() -> str:
    files = gather_files()

    version = json.loads((ROOT / 'VERSION.json').read_text(encoding='utf-8'))

    entries = []
    for p in files:
        rel = to_rel_posix(p)
        b64 = encode(p)
        payload = format_payload(b64, indent=8)
        entries.append(
            f"    {rel!r}: (\n{payload}\n    ),"
        )

    files_block = "FILES = {\n" + "\n".join(entries) + "\n}\n"

    header = f'''#!/usr/bin/env python3
"""flow (FabCanvas) v{version['version']} — single-file installer.

Usage on a fresh machine:

    python setup.py                  # extract + pip install backend deps + npm build
    python setup.py --no-install     # extract only (skip pip + npm)
    python setup.py --no-build       # extract + pip install (skip frontend build)
    python setup.py --version        # print embedded version and exit

After setup, start the server:

    uvicorn app:app --host 0.0.0.0 --port 8080

Login: hol / hol12345!  (override via FABCANVAS_ADMIN_PW / HOL_ADMIN_PW)

This file embeds {len(files)} source files as gzip+base64 blobs. Runtime data
(data/Base, data/DB, data/meetings, data/informs, data/calendar, data/sessions
…) is NOT included and is NEVER overwritten — populate or preserve separately.
"""
import base64
import gzip
import json
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

VERSION = {json.dumps(version, ensure_ascii=False)}


def _write(rel: str, gz_b64: str) -> None:
    # HARD GUARD — never overwrite anything under data/. 기존 인폼/트래커/달력/
    # 회의/유저/세션 기록이 setup 재실행이나 pull 후 재설치에 의해 지워지지
    # 않도록 이중 가드 (prefix check + resolve relative_to data/).
    rel_posix = rel.replace("\\\\", "/").lstrip("./")
    if rel_posix.startswith("data/") or rel_posix == "data":
        return
    data = gzip.decompress(base64.b64decode(gz_b64))
    dst = ROOT / rel
    try:
        dst.resolve().relative_to((ROOT / "data").resolve())
        return
    except Exception:
        pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


'''

    footer = '''

def _run(cmd: str, cwd: Path, check: bool = False) -> int:
    print(f"\\n$ ({cwd.name}) {cmd}")
    try:
        r = subprocess.run(cmd, cwd=str(cwd), shell=True)
        if check and r.returncode != 0:
            print(f"  -> exit {r.returncode}")
        return r.returncode
    except FileNotFoundError as e:
        print(f"  -> not found: {e}")
        return 127


def _has(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def setup(install: bool = True, build: bool = True) -> None:
    # 1) extract files
    for rel, payload in FILES.items():
        _write(rel, ''.join(payload) if isinstance(payload, (list, tuple)) else payload)

    # 2) VERSION.json (always rewritten — source of truth on disk).
    (ROOT / 'VERSION.json').write_text(
        json.dumps(VERSION, indent=2, ensure_ascii=False), encoding='utf-8'
    )

    # 3) ensure empty data dirs exist (app will create on demand otherwise).
    #    NOTE: mkdir(exist_ok=True) never overwrites contents.
    for sub in ('data', 'data/Base', 'data/DB', 'reports'):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)

    print(f"\\n[extract] flow v{VERSION['version']} ({VERSION['codename']}) - {len(FILES)} files -> {ROOT}")

    if not install:
        return

    # 4) backend deps (pip)
    if _has('pip') or _has('pip3'):
        pip = 'pip' if _has('pip') else 'pip3'
        reqs = [
            'fastapi', 'uvicorn[standard]', 'pandas', 'pyarrow', 'polars',
            'numpy', 'python-multipart', 'boto3', 'scikit-learn', 'scipy',
            'openpyxl',
        ]
        _run(f"{pip} install " + ' '.join(shlex.quote(r) for r in reqs), cwd=ROOT)
    else:
        print("[pip] not found - skip backend deps")

    # 5) frontend deps (npm)
    fe = ROOT / 'frontend'
    if _has('npm'):
        _run('npm install', cwd=fe)
        if build:
            _run('npm run build', cwd=fe)
    else:
        print("[npm] not found - skip frontend install/build")

    print(f"\\n[done] uvicorn app:app --host 0.0.0.0 --port 8080   (run from {ROOT})")
    print(f"[done] open http://localhost:8080 - login: hol / hol12345!")


def _print_version() -> None:
    print(f"flow (FabCanvas) v{VERSION['version']} - codename {VERSION['codename']}")
    print(f"  embedded files: {len(FILES)}")


if __name__ == '__main__':
    if '--version' in sys.argv or '-v' in sys.argv:
        _print_version()
        raise SystemExit(0)
    install = '--no-install' not in sys.argv
    build = '--no-build' not in sys.argv
    setup(install=install, build=build)
'''

    return header + files_block + footer


def main():
    out = build()
    dst = ROOT / 'setup.py'
    dst.write_text(out, encoding='utf-8')
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
