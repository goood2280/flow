#!/usr/bin/env python3
"""Builder: walks the FabCanvas.ai source tree and emits setup_v852.py.

Run from the FabCanvas.ai directory:
    python _build_setup_v852.py

Output: setup_v852.py next to this script.
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
    'FabCanvas_domain.txt',
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
        # non-recursive for src subdirs — they're listed explicitly; use rglob instead
        for p in base.rglob('*'):
            if not p.is_file():
                continue
            if any(part in EXCLUDE_PARTS for part in p.parts):
                continue
            # skip __init__.pyc / compiled
            if p.suffix in {'.pyc'}:
                continue
            add(p)

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

    # Group files into parts — matching the original setup_v8 spirit but collapsed.
    # For simplicity with v8.5.2, one big FILES dict is fine.
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
"""FabCanvas v{version['version']} ({version['codename']}) — single-file installer.

Usage (on a fresh machine):

    python setup_v852.py            # extracts sources into ./FabCanvas.ai (or . if run inside)
    cd backend && pip install -r ../requirements.txt   # see generated file list
    cd ../frontend && npm install && npm run build
    cd ../backend && uvicorn app:app --host 0.0.0.0 --port 8080

Login: hol / hol12345!  (override with FABCANVAS_ADMIN_PW / HOL_ADMIN_PW)

This file embeds {len(files)} source files as gzip+base64 blobs. Data files
(data/Base, data/DB, wafer_maps, parquet) are NOT included — populate those
separately on the target machine.
"""
import base64
import gzip
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

VERSION = {json.dumps(version, ensure_ascii=False)}


def _write(rel: str, gz_b64: str) -> None:
    data = gzip.decompress(base64.b64decode(gz_b64))
    dst = ROOT / rel
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

    # 2) VERSION.json
    (ROOT / 'VERSION.json').write_text(
        json.dumps(VERSION, indent=2, ensure_ascii=False), encoding='utf-8'
    )

    # 3) ensure empty data dirs exist (app will create on demand otherwise)
    for sub in ('data', 'data/Base', 'data/DB', 'reports'):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)

    print(f"\\n[extract] FabCanvas v{VERSION['version']} ({VERSION['codename']}) - {len(FILES)} files -> {ROOT}")

    if not install:
        return

    # 4) backend deps (pip)
    if _has('pip') or _has('pip3'):
        pip = 'pip' if _has('pip') else 'pip3'
        reqs = [
            'fastapi', 'uvicorn[standard]', 'pandas', 'pyarrow', 'numpy',
            'python-multipart', 'boto3', 'scikit-learn', 'scipy',
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

    print(f"\\n[done] cd backend && uvicorn app:app --host 0.0.0.0 --port 8080")
    print(f"[done] open http://localhost:8080 — login: hol / hol12345!")


if __name__ == '__main__':
    install = '--no-install' not in sys.argv
    build = '--no-build' not in sys.argv
    setup(install=install, build=build)
'''

    return header + files_block + footer


def main():
    out = build()
    dst = ROOT / 'setup_v852.py'
    dst.write_text(out, encoding='utf-8')
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
