#!/usr/bin/env python3
"""Builder: walks the FabCanvas.ai source tree and emits setup.py as a
self-contained installer (gzip+base64 embedded payloads).

Run from the FabCanvas.ai directory:

    python _build_setup.py

Output: overwrites setup.py at the repo root. Version is read from
VERSION.json, so bump that first.
"""
import base64
import gzip
import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).parent

INCLUDE_DIRS = [
    'backend/core',
    'backend/routers',
    'frontend/src',
    'frontend/public',
    'docs',
]

INCLUDE_FILES = [
    'README.md',
    'CHANGELOG.md',
    # v8.7.6: VERSION.json / CHANGELOG.md 는 반드시 포함 — 홈 화면에 최신 버전·로그 표시용.
    'VERSION.json',
    'app.py',
    'backend/app.py',
    'backend/requirements.txt',
    'frontend/index.html',
    'frontend/package.json',
    'frontend/vite.config.js',
    # NOTE: FabCanvas_domain.txt 는 의도적으로 번들에서 제외. 내부 도메인 지식 파일로
    # public repo 에 유출되어서는 안 됨 (.gitignore 에도 등재).
]

EXCLUDE_PARTS = {'__pycache__', 'node_modules', 'dist', '.claude', '.git', 'reports'}


def gather_files():
    seen = set()
    out = []

    def add(p):
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

    scripts = ROOT / 'scripts'
    if scripts.is_dir():
        for p in scripts.glob('*.py'):
            add(p)

    return sorted(out)


def encode(path):
    data = path.read_bytes()
    gz = gzip.compress(data, compresslevel=9, mtime=0)
    return base64.b64encode(gz).decode('ascii')


def format_payload(b64, indent=8):
    lines = textwrap.wrap(b64, width=72, break_long_words=True, break_on_hyphens=False)
    sp = ' ' * indent
    return '\n'.join(f"{sp}'{ln}'" for ln in lines)


def to_rel_posix(p):
    return p.relative_to(ROOT).as_posix()


def build():
    files = gather_files()
    version = json.loads((ROOT / 'VERSION.json').read_text(encoding='utf-8'))

    entries = []
    for p in files:
        rel = to_rel_posix(p)
        b64 = encode(p)
        payload = format_payload(b64, indent=8)
        entries.append(f"    {rel!r}: (\n{payload}\n    ),")

    files_block = "FILES = {\n" + "\n".join(entries) + "\n}\n"

    header = f'''#!/usr/bin/env python3
"""FabCanvas (flow) v{version['version']} — self-contained installer.

Usage (fresh machine):

    python setup.py                # extract + install deps + build frontend
    python setup.py extract        # just extract embedded sources
    python setup.py install-deps   # pip install backend deps only
    python setup.py build-frontend # npm install + npm run build only
    python setup.py version        # print VERSION
    python setup.py sync-version   # stamp VERSION onto VERSION.json

Run the server afterwards:

    uvicorn app:app --host 0.0.0.0 --port 8080

Login: hol / hol12345!  (override with FABCANVAS_ADMIN_PW / HOL_ADMIN_PW)

This file embeds {len(files)} source files as gzip+base64 blobs. Data
(data/Base, data/DB, users.csv, informs, tracker, …) is NEVER bundled
and never overwritten — re-running setup.py on an existing install
preserves all user-generated data.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

VERSION = "{version['version']}"
CODENAME = "{version.get('codename', 'flow')}"
VERSION_META = {json.dumps(version, ensure_ascii=False)}


def _write(rel: str, gz_b64: str) -> None:
    # v8.8.0: 사용자 데이터 보존 가드 강화.
    # 보존 대상 (덮어쓰기 금지):
    #   - data/ 트리 전체 (users.csv, groups.json, mail_groups, admin_settings,
    #     informs/product_contacts.json, meetings, splittable/notes.json, dashboard,
    #     tracker, ml, …)
    #   - holweb-data/ 트리 전체 (사내 운영 데이터 디렉토리)
    #   - 경로 어디에든 holweb-data 세그먼트가 들어 있으면 보호
    #   - users.csv / *.csv 형태로 data 하위 어디에 있든 보호 (defense in depth)
    rel_posix = rel.replace("\\\\", "/").lstrip("./")
    parts = [p for p in rel_posix.split("/") if p]
    for guard in ("data/", "holweb-data/"):
        if rel_posix.startswith(guard) or rel_posix.rstrip("/") == guard.rstrip("/"):
            return
    if "holweb-data" in parts or "data" in parts[:1]:
        return
    data = gzip.decompress(base64.b64decode(gz_b64))
    dst = ROOT / rel
    try:
        # 사용자 데이터 루트 가드 (case: data/ 아래 절대경로 회피)
        dst_rel = dst.resolve().relative_to((ROOT / "data").resolve())
        _ = dst_rel
        return
    except Exception:
        pass
    # 추가 가드: 외부 prod 데이터 루트가 환경변수로 지정된 경우 그 안쪽도 보호.
    for env_key in ("HOL_DATA_ROOT", "FABCANVAS_DATA_ROOT"):
        env_val = os.environ.get(env_key)
        if env_val:
            try:
                root_resolved = Path(env_val).resolve()
                if str(dst.resolve()).startswith(str(root_resolved)):
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


def extract() -> int:
    for rel, payload in FILES.items():
        _write(rel, ''.join(payload) if isinstance(payload, (list, tuple)) else payload)
    (ROOT / 'VERSION.json').write_text(
        json.dumps(VERSION_META, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    for sub in ('data', 'data/Base', 'data/DB', 'reports'):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    print(f"\\n[extract] flow v{VERSION} - {len(FILES)} files -> {ROOT}")
    return 0


def install_deps() -> int:
    pkgs = [
        'fastapi', 'uvicorn[standard]', 'pandas', 'pyarrow', 'polars', 'numpy',
        'python-multipart', 'boto3', 'scikit-learn', 'scipy', 'openpyxl',
    ]
    return _run(f"{sys.executable} -m pip install " + ' '.join(shlex.quote(p) for p in pkgs), cwd=ROOT)


def build_frontend() -> int:
    fe = ROOT / 'frontend'
    if not (fe / 'package.json').exists():
        print('frontend/package.json not found - skipping', file=sys.stderr)
        return 1
    if not _has('npm'):
        print('[npm] not found - skip frontend install/build')
        return 0
    rc = _run('npm install', cwd=fe)
    if rc != 0:
        return rc
    return _run('npm run build', cwd=fe)


def print_version() -> int:
    print(f"flow (FabCanvas) v{VERSION} - codename {CODENAME}")
    return 0


def sync_version_json() -> int:
    vj = ROOT / 'VERSION.json'
    vj.write_text(json.dumps(VERSION_META, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"VERSION.json -> {VERSION}")
    return 0


def all_steps() -> int:
    rc = extract() or install_deps() or build_frontend()
    if rc == 0:
        print(f"\\n[done] uvicorn app:app --host 0.0.0.0 --port 8080   (run from {ROOT})")
        print(f"[done] open http://localhost:8080 - login: hol / hol12345!")
    return rc


COMMANDS = {
    'extract':        extract,
    'install-deps':   install_deps,
    'build-frontend': build_frontend,
    'version':        print_version,
    'sync-version':   sync_version_json,
    'all':            all_steps,
}


def main(argv):
    if not argv:
        return all_steps()
    cmd = argv[0]
    if cmd in ('-h', '--help', 'help'):
        print(__doc__)
        print('\\nCommands: ' + ', '.join(sorted(COMMANDS)))
        return 0
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 2
    return fn()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
'''

    return header + files_block + footer


def main():
    out = build()
    dst = ROOT / 'setup.py'
    dst.write_text(out, encoding='utf-8')
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
