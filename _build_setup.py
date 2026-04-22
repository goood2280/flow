#!/usr/bin/env python3
"""Builder: walks the FabCanvas.ai source tree and emits setup.py as a
self-contained installer (gzip+base64 embedded payloads).

Run from the FabCanvas.ai directory:

    python _build_setup.py

Output: overwrites setup.py at the repo root. Version is read from
VERSION.json, so bump that first.

v8.8.3 — 데이터 보존 whitelist 명시화.
v8.8.16 — 파일탐색기 S3 동기화 설정(data_root/s3_ingest/*) 누락 보완.
  사용자 생성 데이터는 *절대* 번들에 포함되지 않으며, 기존 설치 위에
  setup.py 를 재실행해도 아래 경로/패턴은 덮어쓰기 되지 않습니다:

    1) 파일탐색기 S3 동기화 설정
       - {data_root}/s3_ingest/config.json     (동기화 항목 목록 · target/s3_url/interval)
       - {data_root}/s3_ingest/status.json     (마지막 실행 상태)
       - {data_root}/s3_ingest/history.jsonl   (실행 이력)
       - {data_root}/admin_settings.json       (AWS creds/프로파일 등)
       - {data_root}/s3_sync_*.json, {data_root}/logs/s3_*.jsonl  (레거시)
    2) 가입한 사용자 목록
       - {data_root}/users.csv
       - {data_root}/sessions/*.json, {data_root}/sessions/tokens.json
    3) 만든 그룹들
       - {data_root}/groups/groups.json
       - {data_root}/mail_groups/*.json
    4) 인폼 설정 (제품 카탈로그, DB루트, 모듈순서 등)
       - {data_root}/informs/informs.json
       - {data_root}/informs/config.json
       - {data_root}/informs/product_contacts.json
       - {data_root}/informs/*.json
    5) 기타 config.json / admin_settings.json / 사용자 생성 데이터
       - {data_root}/admin_settings.json
       - {data_root}/settings.json
       - {data_root}/dbmap/**      (TableMap 버전/아카이브)
       - {data_root}/splittable/** (notes, source_config, ML_TABLE_*)
       - {data_root}/dashboard_*.json
       - {data_root}/tracker/**
       - {data_root}/calendar/**, {data_root}/meetings/**, {data_root}/messages/**
       - {data_root}/shares.json, {data_root}/uploads/**, {data_root}/logs/**
       - {base_root} 전체 (Base/*.csv, *.parquet — 사용자 추가 rulebook)
       - {db_root} 전체 (DB/** — 대용량 원천)
       - {wafer_map_root} 전체

  {data_root} 해석 순서:
     HOL_DATA_ROOT → FABCANVAS_DATA_ROOT → (prod auto) → ./data/holweb-data
  모두 보호. 경로 정규화로 심볼릭링크 우회도 차단.
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

# v8.8.3: 빌드 시에도 "사용자 데이터로 분류되는 디렉토리/파일은 절대 포함하지 않는다"
# 를 이중 방어. INCLUDE_DIRS 밑을 rglob 하면서 아래 세그먼트 중 하나라도 있으면 skip.
EXCLUDE_PARTS = {
    '__pycache__', 'node_modules', 'dist', '.claude', '.git', 'reports',
    # 사용자 데이터 디렉토리 — 빌드 시 번들에서 제외 (런타임엔 _write 가드도 있음)
    'holweb-data', 'Base', 'DB', 'wafer_maps',
}


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
            # v8.8.3: 정적 자산으로 위장한 사용자 데이터 차단
            # (예: frontend/src 안에 실수로 users.csv 를 두는 경우)
            # v8.8.16: users 관련 변형 / S3 sync / 회의록 state 파일까지 확장.
            if p.name.lower() in {'users.csv', 'users.json', 'users_cache.json',
                                   'groups.json', 'admin_settings.json',
                                   'settings.json', 'shares.json', 'informs.json',
                                   'product_contacts.json', 'notes.json',
                                   'source_config.json', 'dashboard_snapshots.json',
                                   'dashboard_charts.json', 'meetings.json',
                                   'events.json', 'notices.json', 'tokens.json',
                                   'sessions.json', 'session_tokens.json',
                                   'mail_groups.json', 'mail_config.json',
                                   'rulebook_schema.json',
                                   'inform_user_modules.json', 'page_admins.json',
                                   's3_ingest_config.json', 's3_sync.json',
                                   'issues.json', 'messages.json'}:
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
(data/Base, data/DB, data/holweb-data — users.csv, groups, informs,
admin_settings, tracker, splittable, meetings, calendar, messages,
dbmap, S3 sync config, …) is NEVER bundled and NEVER overwritten —
re-running setup.py on an existing install preserves ALL user data.

보존 whitelist (v8.8.3):
  - data/ 트리 전체 (data/Base, data/DB, data/holweb-data)
  - holweb-data/ 세그먼트가 포함된 모든 경로
  - HOL_DATA_ROOT / FABCANVAS_DATA_ROOT 환경변수 아래의 모든 경로
  - FABCANVAS_DB_ROOT / FABCANVAS_BASE_ROOT / FABCANVAS_WAFER_MAP_ROOT
  - 사용자 데이터 기본 파일명 (users.csv, groups.json, admin_settings.json,
    settings.json, shares.json, informs.json, product_contacts.json,
    notes.json, source_config.json, dashboard_*.json, meetings.json,
    events.json, notices.json, tokens.json, issues.json)
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


# v8.8.3 — 사용자 데이터 보존 whitelist (덮어쓰기 금지 파일명)
# v8.8.16 — users 관련 변형 / S3 sync 관련 / 기타 런타임 state 파일 추가.
_PROTECTED_BASENAMES = {{
    # 회원/인증
    'users.csv', 'users.json', 'users_cache.json',
    'tokens.json', 'sessions.json', 'session_tokens.json',
    # 그룹/설정
    'groups.json', 'admin_settings.json', 'settings.json',
    'shares.json', 'informs.json', 'config.json', 'product_contacts.json',
    'mail_groups.json', 'mail_config.json',
    # SplitTable / Dashboard / 인폼 state
    'notes.json', 'source_config.json', 'dashboard_snapshots.json',
    'dashboard_charts.json', 'rulebook_schema.json',
    # 회의/트래커/공지/이슈
    'meetings.json', 'events.json', 'notices.json', 'issues.json',
    'messages.json', 'inform_user_modules.json', 'page_admins.json',
    # S3 / 로그
    's3_ingest_config.json', 's3_sync.json',
    'activity.jsonl', 'downloads.jsonl',
}}

# v8.8.3 — 데이터 루트로 간주되는 세그먼트 (경로 어디에 있든 보호)
# v8.8.16 — s3_ingest / reformatter / notifications / cache 추가 보호.
_PROTECTED_SEGMENTS = {{
    'holweb-data',    # 사내 운영 데이터 디렉토리
    'informs',        # 인폼 설정/카탈로그/담당자
    'groups',         # 그룹 정의
    'mail_groups',    # 메일 그룹
    'dbmap',          # TableMap 버전/아카이브
    'splittable',     # SplitTable 노트/설정
    'tracker',        # 이슈 트래커
    'calendar',       # 달력 이벤트
    'meetings',       # 회의/아젠다/액션아이템
    'messages',       # 쪽지/공지 스레드
    'sessions',       # 로그인 세션/토큰
    'uploads',        # 업로드 파일
    'logs',           # activity/download/resource/S3 sync 로그
    '_backups',       # 자동 백업
    '.trash',         # Base 파일 휴지통
    'Base',           # rulebook / parquet / 사용자 추가 CSV
    'DB',             # Hive-flat 원천 데이터
    'wafer_maps',     # wafer map JSON 라이브러리
    # v8.8.16 — 재배포 시 초기화되던 항목들.
    's3_ingest',      # 파일탐색기 S3 동기화 config/status/history
    'reformatter',    # 제품별 reformatter 룰
    'notifications',  # 사용자 알림 큐
    'cache',          # 런타임 캐시 (초기화해도 재생성되지만 덮어쓰지 말 것)
    'data',           # 전체 data 트리 — 어떤 경로 아래에 있든 덮어쓰기 금지 (defense-in-depth)
}}


def _write(rel: str, gz_b64: str) -> None:
    # v8.8.3: 사용자 데이터 보존 가드 — defense in depth.
    #
    # 5개 레이어로 검증 (하나라도 match 하면 쓰기 skip):
    #   L1) 경로 prefix 가 data/ 또는 holweb-data/ 이면 skip
    #   L2) 경로 세그먼트에 _PROTECTED_SEGMENTS 가 하나라도 있으면 skip
    #   L3) 파일명이 _PROTECTED_BASENAMES 에 있으면 skip
    #   L4) resolve() 한 절대 경로가 ./data 또는 ./data/holweb-data 아래면 skip
    #   L5) HOL_DATA_ROOT / FABCANVAS_{{DATA,DB,BASE,WAFER_MAP}}_ROOT 아래면 skip
    rel_posix = rel.replace("\\\\", "/").lstrip("./")
    parts = [p for p in rel_posix.split("/") if p]

    # L1
    for guard in ("data/", "holweb-data/"):
        if rel_posix.startswith(guard) or rel_posix.rstrip("/") == guard.rstrip("/"):
            return

    # L2
    for seg in parts:
        if seg in _PROTECTED_SEGMENTS:
            return

    # L3
    if parts and parts[-1].lower() in _PROTECTED_BASENAMES:
        return

    data = gzip.decompress(base64.b64decode(gz_b64))
    dst = ROOT / rel

    # L4
    try:
        dst_abs = dst.resolve()
        for data_sub in ("data", "data/holweb-data", "data/Base", "data/DB"):
            try:
                dst_abs.relative_to((ROOT / data_sub).resolve())
                return
            except Exception:
                pass
    except Exception:
        pass

    # L5
    for env_key in ("HOL_DATA_ROOT", "FABCANVAS_DATA_ROOT",
                    "FABCANVAS_DB_ROOT", "FABCANVAS_BASE_ROOT",
                    "FABCANVAS_WAFER_MAP_ROOT"):
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


def _ensure_critical_deps() -> None:
    """v8.8.2: extract 시에도 엑셀 관련 핵심 의존성은 자동 설치.
    openpyxl 은 인폼 표 embed / SplitTable 엑셀 export 에서 즉시 사용되므로
    pip install 을 따로 실행하지 않아도 동작해야 한다는 요구에 따른 필수 패키지.
    이미 import 되면 skip."""
    critical = ('openpyxl',)
    missing = []
    for mod in critical:
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if not missing:
        return
    print(f"[deps] ensure critical: {', '.join(missing)}")
    _run(f"{sys.executable} -m pip install " + ' '.join(shlex.quote(p) for p in missing), cwd=ROOT)


def extract() -> int:
    skipped = 0
    written = 0
    for rel, payload in FILES.items():
        # _write 내부에서 보호된 경로면 조용히 return 하므로,
        # 여기서 쓰기 전 후 파일 존재 여부로 write/skip 집계.
        dst = ROOT / rel
        existed = dst.exists()
        _write(rel, ''.join(payload) if isinstance(payload, (list, tuple)) else payload)
        if dst.exists() and not existed:
            written += 1
        elif existed:
            # 기존 파일이 덮어써졌는지 여부는 파일명으로 판단 불가 — 단순 카운트만.
            written += 1
    (ROOT / 'VERSION.json').write_text(
        json.dumps(VERSION_META, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    for sub in ('data', 'data/Base', 'data/DB', 'reports'):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    # v8.8.2: extract 단독 실행에도 openpyxl 같은 필수 dep 는 자동으로 채워넣음.
    _ensure_critical_deps()
    print(f"\\n[extract] flow v{VERSION} - {len(FILES)} files processed -> {ROOT}")
    print(f"[extract] user data preservation: data/holweb-data, data/Base, data/DB, "
          f"HOL_DATA_ROOT, FABCANVAS_*_ROOT  (see _write guard)")
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
