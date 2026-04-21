#!/usr/bin/env python3
"""Builder: walks the FabCanvas.ai source tree and emits setup_v864.py.

Run from the FabCanvas.ai directory:
    python _build_setup_v864.py

Output: setup_v864.py next to this script.

v8.6.4 differences vs v8.5.2 builder:
  - Root `app.py` shim is embedded (uvicorn app:app --host 0.0.0.0 --port 8080).
  - Pulls in new routers (calendar.py, groups.py, informs.py) and pages
    (My_Calendar.jsx, My_Inform.jsx) + components (S3StatusLight.jsx, PageGear.jsx)
    automatically — anything added to the source tree is picked up by the globs.
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
    'app.py',                  # ← v8.6.x uvicorn shim (top-level)
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
"""FabCanvas v{version['version']} ({version['codename']}) — single-file installer.

Usage (on a fresh machine):

    python setup_v864.py            # extracts sources into ./FabCanvas.ai (or . if run inside)
    pip install fastapi uvicorn[standard] pandas pyarrow numpy python-multipart boto3 scikit-learn scipy
    cd frontend && npm install && npm run build && cd ..
    uvicorn app:app --host 0.0.0.0 --port 8080

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
    # v8.7.0: HARD GUARD — never overwrite anything under data/. 기존 인폼/트래커/
    # 달력/유저/세션 기록이 setup 재실행이나 pull 후 재설치에 의해 지워지지 않도록.
    rel_posix = rel.replace("\\\\", "/").lstrip("./")
    if rel_posix.startswith("data/") or rel_posix == "data":
        return
    data = gzip.decompress(base64.b64decode(gz_b64))
    dst = ROOT / rel
    # 존재하는 data 이하 파일은 이중 방어: 절대 덮어쓰지 않는다.
    try:
        dst_rel = dst.resolve().relative_to((ROOT / "data").resolve())
        # data/ 밑으로 떨어졌으면 skip.
        _ = dst_rel
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

    # 2) VERSION.json
    (ROOT / 'VERSION.json').write_text(
        json.dumps(VERSION, indent=2, ensure_ascii=False), encoding='utf-8'
    )

    # 3) ensure empty data dirs exist (app will create on demand otherwise)
    #    NOTE: mkdir(exist_ok=True) never overwrites contents. 기존 인폼/트래커/달력/유저
    #    데이터는 건드리지 않음.
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

    print(f"\\n[done] uvicorn app:app --host 0.0.0.0 --port 8080   (run from {ROOT})")
    print(f"[done] open http://localhost:8080 - login: hol / hol12345!")


if __name__ == '__main__':
    install = '--no-install' not in sys.argv
    build = '--no-build' not in sys.argv
    setup(install=install, build=build)
'''

    return header + files_block + footer


def main():
    out = build()
    dst = ROOT / 'setup_v864.py'
    dst.write_text(out, encoding='utf-8')
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
