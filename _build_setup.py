#!/usr/bin/env python3
"""Build the Flow self-contained installer.

Run from the flow/ directory:

    python _build_setup.py

Output: overwrites setup.py at the repo root by default. To include the latest
private admin knowledge, use --include-domain-knowledge [SQLITE] and --output
pointing outside this public repository (for example a local deliverables folder).
Release history is read from
VERSION.json, while user-facing version output is mtime-based.

The default installer embeds source files only. The private build also carries
a seed of the latest domain knowledge; existing revisions are never overwritten.
Other runtime data under data/, FLOW_DATA_ROOT, FLOW_DB_ROOT, or
FLOW_WAFER_MAP_ROOT is not bundled. Before extraction it snapshots small config/state
files to ~/.flow_backups and can restore them with
`python setup.py restore [latest|<timestamp>]`.
"""
import base64
import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent

# ROOT 자체가 tool worktree 안에 있으면 EXCLUDE_PARTS 가 모든 소스 파일을
# 걸러버려 빈 번들이 만들어진다. 반드시 main 체크아웃에서 실행.
if 'worktrees' in ROOT.parts:
    sys.stderr.write(
        f"ERROR: _build_setup.py must run from the main checkout, not a worktree.\n"
        f"  ROOT = {ROOT}\n"
        f"  worktrees segment in ROOT causes EXCLUDE_PARTS to drop every source file.\n"
        f"  Run this script from the primary repo root instead.\n"
    )
    sys.exit(2)

INCLUDE_DIRS = [
    'backend/app_v2',
    'backend/core',
    'backend/routers',
    'frontend/src',
    'frontend/public',
    'frontend/scripts',
    # 빌드된 프런트 산출물. 예전엔 git 이 dist 를 추적해서 체크아웃만 하면 UI 가
    # 있었지만, 2026-07-27 저장소 축소 이후 origin/main 은 README+setup.py 뿐이라
    # 번들이 dist 의 유일한 출처다. 이게 빠지면 npm registry 가 막힌 사내망에서
    # build_frontend 가 조용히 실패하고, backend/app.py 의 `if DIST.exists()` 가
    # False 라 SPA 라우트 자체가 안 붙어 모든 화면이 404 가 된다.
    'frontend/dist',
    'docs',
    # 검증 수단과 운영 스크립트도 번들에 담는다. 이게 빠져 있으면 GitHub 저장소를
    # "README.md + setup.py" 로 줄였을 때 영구 소실된다 — 2026-07-20 커밋
    # bb0737b5 에서 실제로 tests/ 92 개와 scripts/ 가 그렇게 사라졌다.
    'tests',
    'scripts',
]

INCLUDE_FILES = [
    'README.md',
    'SECOND_BRAIN.md',
    'package.json',
    'package-lock.json',
    'VERSION.json',
    'app.py',
    # Root import shims keep direct path/importlib loads stable after setup.py
    # is copied to a fresh working directory.
    'app_v2/__init__.py',
    'core/__init__.py',
    'routers/__init__.py',
    'backend/app.py',
    # requirements.txt 와 Dockerfile/.dockerignore 는 설치 폴더에서 운영자가
    # 관리한다. setup.py 에 넣으면 extract 때 pip freeze 결과와 현장 Docker
    # 설정을 덮어쓰므로 번들에 포함하지 않는다.
    # backend/ 루트 모듈 — INCLUDE_DIRS 의 app_v2/core/routers 에 안 걸린다.
    # startup.py 가 import 하므로 빠지면 제품 dedup 스케줄러가 조용히 죽는다.
    'backend/scheduler.py',
    'frontend/index.html',
    'frontend/package.json',
    'frontend/package-lock.json',
    'frontend/vite.config.js',
    # 에이전트 진입점 + 저장소 위생 규칙 + 번들 빌더 자신.
    # _build_setup.py 가 빠지면 setup.py 를 다시 만들 수단이 사라진다.
    'CLAUDE.md',
    'AGENTS.md',
    '.gitignore',
    '.gitattributes',
    '_build_setup.py',
    # NOTE: archive/domain_sources_* 내부 원문 도메인 노트는 번들에서 제외.
    # 내부 도메인 지식 파일은 public repo/installer payload 에 유출되어서는 안 됨.
]

# 빌드 시에도 "사용자 데이터로 분류되는 디렉토리/파일은 절대 포함하지 않는다"를
# 이중 방어. INCLUDE_DIRS 밑을 rglob 하면서 아래 세그먼트 중 하나라도 있으면 skip.
EXCLUDE_PARTS = {
    '__pycache__', 'node_modules', 'dist', '.git', 'reports',
    # 사용자 데이터 디렉토리 — 빌드 시 번들에서 제외 (런타임엔 _write 가드도 있음)
    'data', 'flow-data', 'Fab', 'Base', 'DB', 'wafer_maps', 'confidential',
    # 퇴역 코드 보관소 — INCLUDE_DIRS 밖(루트 archive/)이 기본이지만, 포함 디렉토리
    # 안에 reference/backup 폴더를 만들어 옮겨도 번들에 새지 않게 이중 방어.
    'archive', 'reference', 'backup',
}

# The retired agent runtime is parked locally under backup/flowi-*. Keep that
# graph out of the deployable installer, but preserve dependencies used by the
# active home data chat (`backend/routers/data_chat.py`).
FLOWI_EXCLUDE_PREFIXES = (
    'backend/app_v2/modules/llm/',
    'backend/app_v2/modules/agent_runtime/',
    'backend/app_v2/modules/semantic_learning/',
    'backend/core/flowi_units/',
    'frontend/src/features/diagnosis/',
)
FLOWI_EXCLUDE_FILES = {
    'backend/core/flowi_fewshots.py',
    'backend/core/flowi_file_docs.py',
    'backend/core/flowi_multisource.py',
    'backend/core/flowi_workflow_catalog.py',
    'backend/core/flowi_workflow_defaults.json',
    'backend/core/home_memory.py',
    'backend/core/home_orchestrator.py',
    'backend/routers/agent.py',
    'backend/routers/flowi_learning.py',
    'backend/routers/home_agent.py',
    'frontend/src/pages/My_Diagnosis.jsx',
    'tests/test_flowi_chart_sql_contract.py',
}

LOCAL_EXCLUDE_FILES = {
    # Local import-graph probe is intentionally ignored by Git and not deployed.
    'frontend/scripts/test-page-loading.mjs',
}


def gather_files():
    seen = set()
    out = []

    def add(p):
        # 서버 역할/정체성은 hostname별 data_root에만 존재해야 한다. 실수로
        # include 목록이 넓어져도 legacy 역할 파일은 설치 번들에 넣지 않는다.
        if p.name.lower() in {
            '.dev_worker', '.dev_worker.txt', '.standalone', '.standalone.txt',
            'server_role.json', 'worker.marker', 'standalone.marker',
        }:
            return
        if p in seen or not p.is_file():
            return
        try:
            rel = p.relative_to(ROOT).as_posix()
        except ValueError:
            return
        if rel in FLOWI_EXCLUDE_FILES or rel in LOCAL_EXCLUDE_FILES or rel.startswith(FLOWI_EXCLUDE_PREFIXES):
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
            # base 아래 경로만 검사한다. 절대경로 전체를 보면 (a) 저장소가
            # "…/data/…" 같은 폴더 밑에 있을 때 전부 탈락하고, (b) INCLUDE_DIRS
            # 로 명시한 'frontend/dist' 가 자기 이름('dist') 때문에 스스로 걸린다.
            if any(part in EXCLUDE_PARTS for part in p.relative_to(base).parts):
                continue
            if p.suffix in {'.pyc'}:
                continue
            # 정적 자산으로 위장한 사용자 데이터 차단
            # (예: frontend/src 안에 실수로 users.csv 를 두는 경우)
            if p.name.lower() in {'users.csv', 'users.json', 'users_cache.json',
                                   'groups.json', 'admin_settings.json',
                                   'settings.json', 'filebrowser_settings.json',
                                   'filebrowser_agent_prompts.json',
                                   'flowi_workflows.json', 'measurement_terms.json',
                                   'shares.json', 'informs.json',
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
        for p in list(scripts.glob('*.py')) + list(scripts.glob('*.js')):
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


# 프런트엔드 소스 지문 — frontend/dist 가 어떤 소스에서 나왔는지 식별한다.
# 번들 빌드 시 계산해 setup.py 에 박아두고, 배포 서버에서 추출된 frontend/src 로
# 다시 계산해 비교한다. 같으면 npm 을 아예 돌릴 필요가 없다.
# 주의: 이 함수는 생성되는 setup.py 안에도 같은 내용으로 들어간다. 한쪽만 고치면
# 지문이 영원히 불일치해서 서버가 매번 npm 을 시도한다. 반드시 양쪽을 같이 고칠 것.
_FE_STAMP_EXTRA = ('package.json', 'package-lock.json', 'vite.config.js', 'vite.config.mjs',
                   'vite.config.ts', 'index.html')


def frontend_src_fingerprint(root: Path) -> str:
    fe = root / 'frontend'
    items = []
    src = fe / 'src'
    if src.is_dir():
        for p in src.rglob('*'):
            if not p.is_file():
                continue
            # 번들에 싣지 않는 파일(은퇴한 화면 등)은 설치 폴더에 없다. 여기서 세면 설치기가
            # 다시 계산한 지문과 영원히 달라, 소스가 같은데도 설치 때마다 npm install·빌드가 돌아
            # 검증된 dist 가 설치 서버의 npm 이 고른 패키지 버전으로 바뀐다.
            rel_root = p.relative_to(root).as_posix()
            if (rel_root in FLOWI_EXCLUDE_FILES or rel_root in LOCAL_EXCLUDE_FILES
                    or rel_root.startswith(FLOWI_EXCLUDE_PREFIXES)):
                continue
            items.append((p.relative_to(fe).as_posix(), p))
    for name in _FE_STAMP_EXTRA:
        p = fe / name
        if p.is_file():
            items.append((p.relative_to(fe).as_posix(), p))
    h = hashlib.sha256()
    for rel, p in sorted(items, key=lambda x: x[0]):
        h.update(rel.encode('utf-8'))
        h.update(b'\0')
        h.update(p.read_bytes())
        h.update(b'\0')
    return h.hexdigest()


def ensure_frontend_build() -> dict:
    """번들에 실릴 frontend/dist 를 현재 소스로 다시 빌드한다.

    dist 는 사내 서버에서 npm 이 안 돌 때 그대로 서빙되는 산출물이다. 낡은 dist 가
    실리면 소스만 최신이고 화면은 과거인 채로 배포되며, 서버 쪽 soft landing 이
    그 사실을 조용히 덮는다. 그래서 여기서 항상 다시 빌드하고, 실패하면 번들을
    만들지 않는다. FLOW_BUILD_SKIP_NPM=1 이면 기존 dist 를 그대로 쓴다(비상용).
    """
    fe = ROOT / 'frontend'
    dist_index = fe / 'dist' / 'index.html'
    skip = os.environ.get('FLOW_BUILD_SKIP_NPM', '').strip().lower() not in ('', '0', 'false', 'no', 'off')

    if not (fe / 'package.json').is_file():
        print('[build] frontend/package.json 없음 - 프런트엔드 빌드 생략')
    elif skip:
        if not dist_index.is_file():
            sys.exit('[build] FLOW_BUILD_SKIP_NPM=1 인데 frontend/dist 도 없습니다. 번들에 실을 화면이 없어 중단합니다.')
        print('[build] WARN FLOW_BUILD_SKIP_NPM=1 - 기존 frontend/dist 를 그대로 싣습니다 (소스와 다를 수 있음)')
    elif shutil.which('npm') is None:
        if not dist_index.is_file():
            sys.exit('[build] npm 도 없고 frontend/dist 도 없습니다. 번들에 실을 화면이 없어 중단합니다.')
        print('[build] WARN npm 없음 - 기존 frontend/dist 를 그대로 싣습니다 (소스와 다를 수 있음)')
    else:
        print('[build] npm run build - 번들에 실을 frontend/dist 를 현재 소스로 재생성')
        rc = subprocess.run('npm run build', cwd=str(fe), shell=True).returncode
        if rc != 0:
            sys.exit(f'[build] npm run build 실패(rc={rc}) - 낡은 dist 를 배포하지 않기 위해 번들 생성을 중단합니다.')

    sha = frontend_src_fingerprint(ROOT)
    dist_files = sum(1 for p in (fe / 'dist').rglob('*') if p.is_file()) if (fe / 'dist').is_dir() else 0
    stamp = {
        'src_sha': sha,
        'built_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'dist_files': dist_files,
        'verified': not skip and shutil.which('npm') is not None,
        'dist_sha256': {p.relative_to(fe / 'dist').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((fe / 'dist').rglob('*')) if p.is_file()},
    }
    print(f"[build] frontend stamp src_sha={sha[:16]} dist_files={dist_files} verified={stamp['verified']}")
    return stamp


def installer_version_meta(version: dict) -> dict:
    """설치본이 들고 다니는 버전 메타 = VERSION.json **전문**.

    예전에는 여기서 release_notes 를 rollup 1건으로 압축했다. 그런데 extract 가
    이 값을 작업트리 VERSION.json 위에 덮어써서, 번들을 풀 때마다 릴리스 노트
    히스토리가 통째로 사라졌다 — CLAUDE.md 가 "가장 신뢰할 수 있는 변경 이력" 이라
    부르는 그 파일이다. 지금은 두 값을 **같게** 만들어 어느 경로로 써도 손실이
    없게 한다 (수십 KB 로, 6MB 번들에서 무시할 수 있는 비용).
    """
    meta = dict(version or {})
    meta.setdefault("version", "")
    meta.setdefault("codename", "flow")
    notes = meta.get("release_notes")
    meta["release_notes"] = notes if isinstance(notes, list) else []
    return meta


# 설치 끝 점검 목록 — setup.py 에 그대로 실린다(단일 원천).
# (import 이름 후보, pip 이름, 최소 버전, 등급, 용도)
#   required = 없거나 최소 미만이면 서버가 안 뜨거나 로그인·조회가 실패한다.
#   perf     = 없으면 느린 경로로 동작한다.
#   feature  = 해당 기능만 실패한다.
# 최소 버전은 코드가 실제로 쓰는 API 로 확인한 값만 적는다(모르면 None).
# 개발 PC 에서 검증한 버전은 빌드할 때 자동으로 기록된다(DEP_TESTED_VERSIONS).
DEPENDENCY_CHECKS = [
    (("fastapi",), "fastapi", None, "required", "웹 서버"),
    (("uvicorn",), "uvicorn", None, "required", "웹 서버 실행"),
    (("python_multipart", "multipart"), "python-multipart", None, "required", "파일 업로드·폼(없으면 서버 기동 실패)"),
    (("pydantic",), "pydantic", "2.0", "required", "요청 검증·설정 저장(model_dump 필요)"),
    (("polars",), "polars", "1.0", "required", "SplitTable·파일탐색기 계산(1.0 미만은 collect_schema 없음)"),
    (("pyarrow",), "pyarrow", None, "required", "parquet 읽기·쓰기"),
    (("pandas",), "pandas", None, "required", "표 처리·TEG·엑셀"),
    (("numpy",), "numpy", None, "required", "수치 계산"),
    (("psutil",), "psutil", None, "required", "메모리 감시·자원 보호(없으면 Windows 메모리 보호가 꺼짐)"),
    (("cryptography",), "cryptography", None, "required", "사내 로그인 연락처 암호화 저장"),
    (("duckdb",), "duckdb", None, "perf", "파일탐색기 대용량 SQL(없으면 느린 Polars 경로)"),
    (("orjson",), "orjson", None, "perf", "큰 응답 JSON 직렬화(없으면 표준 json)"),
    (("websockets",), "websockets", "11.0", "feature", "WebSocket 로그인 서버 재검증(ws:// 주소일 때)"),
    (("openpyxl",), "openpyxl", None, "feature", "엑셀 읽기"),
    (("xlsxwriter",), "xlsxwriter", None, "feature", "엑셀 내보내기"),
    (("xlrd",), "xlrd", None, "feature", "옛 xls 읽기"),
    (("matplotlib",), "matplotlib", None, "feature", "리포트 그림"),
    (("pptx",), "python-pptx", None, "feature", "PPT 리포트"),
    (("PIL",), "pillow", None, "feature", "TEG shot 그림 인식"),
    (("dotenv",), "python-dotenv", None, "feature", ".env 설정"),
    (("requests",), "requests", None, "feature", "외부 HTTP 연동"),
    (("yaml",), "pyyaml", None, "feature", "YAML 설정"),
    (("boto3",), "boto3", None, "feature", "S3 연동"),
    (("sklearn",), "scikit-learn", None, "feature", "ML 분석"),
    (("scipy",), "scipy", None, "feature", "통계 분석"),
]


def tested_dependency_versions() -> dict:
    """빌드한 개발 환경의 설치 버전 — 현장 버전과 주 버전이 다르면 설치 점검이 알린다."""
    from importlib import metadata

    out = {}
    for _imports, dist, _minimum, _level, _purpose in DEPENDENCY_CHECKS:
        try:
            out[dist] = metadata.version(dist)
        except Exception:
            out[dist] = ""
    out["python"] = ".".join(str(part) for part in sys.version_info[:3])
    return out


def _load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _domain_knowledge_payload(source: str = "auto") -> str:
    helper = _load_local_module("flow_knowledge_seed", ROOT / "backend/core/domain_knowledge_seed.py")
    if source == "auto":
        profile = _load_local_module("flow_root_profile", ROOT / "backend/core/root_profile.py")
        data_root = Path(os.environ.get("FLOW_DATA_ROOT") or profile.default_data_root())
        source = data_root / "knowledge/domain_knowledge.sqlite3"
    document = helper.export_document(Path(source))
    return base64.b64encode(gzip.compress(json.dumps(document, ensure_ascii=False).encode("utf-8"))).decode("ascii")


def build(domain_knowledge_payload: str = ""):
    # dist 를 먼저 재생성한 뒤에 파일을 모은다 — 순서가 바뀌면 방금 빌드한 dist 가
    # 번들에 안 실린다.
    frontend_stamp = ensure_frontend_build()
    files = gather_files()
    version = json.loads((ROOT / 'VERSION.json').read_text(encoding='utf-8'))
    installer_meta = installer_version_meta(version)

    entries = []
    for p in files:
        rel = to_rel_posix(p)
        b64 = encode(p)
        payload = format_payload(b64, indent=8)
        entries.append(f"    {rel!r}: (\n{payload}\n    ),")

    files_block = "FILES = {\n" + "\n".join(entries) + "\n}\n"

    header = f'''#!/usr/bin/env python3
"""Flow self-contained installer.

Usage (fresh machine):

    python setup.py                # extract + install deps + build frontend
    python setup.py extract        # 운영에 필요한 소스만 추출 (tests/docs/CLAUDE.md 제외)
    python setup.py extract --all  # 번들에 든 모든 파일 추출 (문서·테스트 포함)
    python setup.py install-deps   # use existing requirements.txt; fallback to minimum deps
    python setup.py check-deps     # 필요한 라이브러리가 조건대로 설치됐는지 표로 확인 (install_check.json)
    python setup.py build-frontend # npm install + npm run build only
    python setup.py version        # print mtime-based version label
    python setup.py sync-version   # rewrite VERSION.json metadata

Run the server afterwards:

    scripts\\\\windows\\\\flow_run.bat  # Windows: site environment + supervisor
    python scripts/flow_server.py  # Other platforms: supervised API

Initial admin: set FLOW_ADMIN_PW to an explicit non-default password (10+ characters)

This file embeds {len(files)} source files as gzip+base64 blobs. Data
(data/, flow-data/, Fab/, DB/, Base/, wafer_maps/ and external
FLOW_DATA_ROOT/FLOW_DB_ROOT — users.csv, groups, informs, admin_settings,
tracker, splittable, meetings, calendar, messages, dbmap, S3 sync config, …)
is not bundled or overwritten. An explicitly built PRIVATE installer can carry
the latest admin domain knowledge as an initial seed; existing revisions are
always preserved. The seed is stored in data/install-seeds beside this installer.

데이터 보존 정책 (요약):
  - data/ 트리 전체 (data/Fab, data/Base, data/DB, data/flow-data)
  - flow-data/ 세그먼트가 포함된 모든 경로
  - FLOW_DATA_ROOT 환경변수 아래의 모든 경로
  - FLOW_DB_ROOT / FLOW_WAFER_MAP_ROOT
  - 사용자 데이터 기본 파일명 (users.csv, groups.json, admin_settings.json,
    settings.json, shares.json, informs.json, product_contacts.json,
    notes.json, source_config.json, dashboard_*.json, meetings.json,
    events.json, notices.json, tokens.json, issues.json)
"""
from __future__ import annotations

import base64
import datetime
import gzip
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Windows cp949 기본 stdout 에서 em-dash/non-ASCII print 가 터지는 것을
# 방지 — UTF-8 reconfigure. 실패해도 조용히 무시.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VERSION = "{version['version']}"
CODENAME = "{version.get('codename', 'flow')}"
VERSION_META = {json.dumps(installer_meta, ensure_ascii=False)}
# 이 번들의 frontend/dist 가 어느 소스에서 빌드됐는지. build_frontend() 가 서버에서
# frontend/src 로 지문을 다시 계산해 비교한다 (_build_setup.ensure_frontend_build).
FRONTEND_STAMP = {frontend_stamp!r}
DOMAIN_KNOWLEDGE_PAYLOAD = {domain_knowledge_payload!r}
DEPENDENCY_CHECKS = {DEPENDENCY_CHECKS!r}
DEP_TESTED_VERSIONS = {tested_dependency_versions()!r}
_FE_STAMP_EXTRA =('package.json', 'package-lock.json', 'vite.config.js', 'vite.config.mjs',
                   'vite.config.ts', 'index.html')


def _frontend_src_fingerprint() -> str:
    # _build_setup.frontend_src_fingerprint 와 반드시 동일한 계산이어야 한다.
    fe = ROOT / 'frontend'
    items = []
    src = fe / 'src'
    if src.is_dir():
        for p in src.rglob('*'):
            if p.is_file():
                items.append((p.relative_to(fe).as_posix(), p))
    for name in _FE_STAMP_EXTRA:
        p = fe / name
        if p.is_file():
            items.append((p.relative_to(fe).as_posix(), p))
    h = hashlib.sha256()
    for rel, p in sorted(items, key=lambda x: x[0]):
        h.update(rel.encode('utf-8'))
        h.update(b'\\0')
        h.update(p.read_bytes())
        h.update(b'\\0')
    return h.hexdigest()


def _version_time_label() -> str:
    times = []
    for fp in (ROOT / 'VERSION.json', ROOT / 'setup.py'):
        try:
            times.append(fp.stat().st_mtime)
        except OSError:
            pass
    if not times:
        return "unknown"
    return datetime.datetime.fromtimestamp(max(times)).isoformat(timespec="seconds")


# 사용자 데이터 보존 whitelist (덮어쓰기 금지 파일명)
_PROTECTED_BASENAMES = {{
    # 회원/인증
    'users.csv', 'users.json', 'users_cache.json',
    'tokens.json', 'sessions.json', 'session_tokens.json',
    # 그룹/설정
    'groups.json', 'admin_settings.json', 'settings.json',
    'filebrowser_settings.json', 'filebrowser_agent_prompts.json',
    'flowi_workflows.json', 'measurement_terms.json',
    'shares.json', 'informs.json', 'config.json', 'product_contacts.json',
    'mail_groups.json', 'mail_config.json',
    # SplitTable / Dashboard / 인폼 state
    'notes.json', 'source_config.json', 'dashboard_snapshots.json',
    'dashboard_charts.json', 'rulebook_schema.json',
    'paste_sets.json', 'prefix_config.json',
    # 회의/트래커/공지/이슈
    'meetings.json', 'events.json', 'notices.json', 'issues.json',
    'messages.json', 'inform_user_modules.json', 'page_admins.json',
    # S3 / 로그
    's3_ingest_config.json', 's3_sync.json', 'history.jsonl', 'status.json',
    'activity.jsonl', 'downloads.jsonl', 'resource.jsonl',
    # 캘린더/대시보드 state
    'calendar.json', 'reformatter.json',
    # 시스템 모니터 state (resource.jsonl 은 이미 위 등록).
    'farm_status.json', 'sysmon_state.json',
}}

# 데이터 루트로 간주되는 세그먼트.
_PROTECTED_SEGMENTS = {{
    'flow-data',    # 사내 운영 데이터 디렉토리
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
    # 재배포 시 초기화되면 안 되는 런타임 항목들.
    's3_ingest',      # 파일탐색기 S3 동기화 config/status/history
    'reformatter',    # 제품별 reformatter 룰
    'notifications',  # 사용자 알림 큐
    'cache',          # 런타임 캐시 (초기화해도 재생성되지만 덮어쓰지 말 것)
    'data',           # 전체 data 트리 — 어떤 경로 아래에 있든 덮어쓰기 금지 (defense-in-depth)
}}


_ALLOWED_TOP_LEVEL = {{
    'backend', 'frontend', 'docs', 'scripts', 'app_v2', 'core', 'routers',
    'tests',
    'app.py', 'README.md', 'SECOND_BRAIN.md', 'VERSION.json',
    # 에이전트 진입점 / 저장소 위생 규칙 / 번들 빌더 자신 / npm 잠금.
    # 이 화이트리스트에 없으면 FILES 에 담겨 있어도 extract 가 조용히 버린다 —
    # GitHub 저장소를 "README.md + setup.py" 로 줄였을 때 영구 소실되는 경로다.
    'CLAUDE.md', 'AGENTS.md', '.gitignore', '.gitattributes', '_build_setup.py',
    'package.json', 'package-lock.json',
}}


def _is_backend_app_v2_source(parts: list[str]) -> bool:
    return len(parts) >= 2 and parts[0] == "backend" and parts[1] == "app_v2"


def _is_frontend_source(parts: list[str]) -> bool:
    # frontend/src 는 화면 소스다. features/splittable·tracker·calendar 폴더 이름이 데이터
    # 루트의 같은 이름 폴더로 오인돼 조용히 버려지면, 설치 폴더에서 npm run build 가
    # "Could not resolve ../features/calendar/My_Calendar" 로 실패한다.
    return len(parts) >= 2 and parts[0] == "frontend" and parts[1] == "src"


# 쓰기 실패(잠긴 파일/권한)를 모아 두었다가 extract 끝에서 요약한다. 운영서버에서
# 앱(uvicorn)이 실행 중이면 Windows 가 로드된 .py/.pyd 를 잠가 write 가 실패하는데,
# 예전엔 여기서 예외가 그대로 터져 setup.py 가 exit 1(+traceback) → Hudson durable
# task step 비정상 종료로 이어졌다. 이제 파일별로 잡고 계속 진행한다.
_WRITE_FAILURES: list = []


def _write(rel: str, gz_b64: str) -> None:
    # 사용자 데이터 보존 가드 — defense in depth.
    #
    # 원칙: setup.py 는 **코드만 교체하고 flow-data/ 안의 어떤 파일도
    # 건드리지 않는다**. FILES dict 는 backend/ frontend/ docs/ app.py 등 소스만 담아야 함.
    # 6개 레이어로 검증 (하나라도 match 하면 쓰기 skip):
    #   L0) top-level 세그먼트가 _ALLOWED_TOP_LEVEL 에 없으면 화이트리스트 위반 → skip
    #   L1) 경로 prefix 가 data/ 또는 flow-data/ 이면 skip
    #   L2) backend/app_v2·frontend/src 소스가 아닌 경로의 보호 세그먼트는 skip
    #   L3) 파일명이 _PROTECTED_BASENAMES 에 있으면 skip
    #   L4) resolve() 한 절대 경로가 ./data 또는 ./data/flow-data 아래면 skip
    #   L5) FLOW_DATA_ROOT / FLOW_{{DB,WAFER_MAP}}_ROOT 아래면 skip
    # NOTE: lstrip("./") 은 문자 집합 제거라 ".gitignore" -> "gitignore" 로 망가뜨려
    # 닷파일이 L0 화이트리스트에서 탈락했다. "./" prefix 와 선행 "/" 만 제거한다
    # (선행 "/" 를 남기면 L1 의 startswith("data/") 가드가 "/data/x" 를 놓친다).
    rel_posix = rel.replace("\\\\", "/")
    while rel_posix.startswith("./"):
        rel_posix = rel_posix[2:]
    rel_posix = rel_posix.lstrip("/")
    parts = [p for p in rel_posix.split("/") if p]

    # L0: 화이트리스트 — 허용 루트가 아니면 설치 대상 아님 (보수적 기본값).
    if parts and parts[0] not in _ALLOWED_TOP_LEVEL:
        return

    # L1
    for guard in ("data/", "flow-data/"):
        if rel_posix.startswith(guard) or rel_posix.rstrip("/") == guard.rstrip("/"):
            return

    # L2: app_v2 migration layer has legitimate source module names such as
    # informs/tracker/meetings, and frontend/src/features has splittable/tracker/calendar.
    # Do not classify those code paths as data roots.
    if not (_is_backend_app_v2_source(parts) or _is_frontend_source(parts)):
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
        for data_sub in ("data", "data/flow-data", "data/Base", "data/DB"):
            try:
                dst_abs.relative_to((ROOT / data_sub).resolve())
                return
            except Exception:
                pass
    except Exception:
        pass

    # L5
    for env_key in ("FLOW_DATA_ROOT", "FLOW_DB_ROOT", "FLOW_WAFER_MAP_ROOT"):
        env_val = os.environ.get(env_key)
        if env_val:
            try:
                root_resolved = Path(env_val).resolve()
                if str(dst.resolve()).startswith(str(root_resolved)):
                    return
            except Exception:
                pass

    # L6: 사내 공유 경로 `/config/work/sharedworkspace/{{flow-data,DB}}`
    #   환경변수 없이도 절대 덮어쓰지 않는다 — setup.py 가 공유 데이터 휘발시키는
    #   사고 방지. 해당 경로가 실제 존재하지 않으면 아무 효과 없음 (개발 PC 무해).
    try:
        dst_abs = dst.resolve()
        for _shared_sub in ("/config/work/sharedworkspace/flow-data",
                            "/config/work/sharedworkspace/DB"):
            if str(dst_abs).startswith(_shared_sub):
                return
    except Exception:
        pass

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    except Exception as _e:
        # 잠긴 파일/권한 등으로 개별 파일 쓰기가 실패해도 배포 전체를 중단하지 않는다.
        # (예외를 그대로 던지면 setup.py 가 exit 1 → 파이프라인 abort.)
        # NOTE: 이 블록은 빌더의 header f-string 안이라 중괄호를 피해 문자열 연결 사용.
        _WRITE_FAILURES.append((rel, type(_e).__name__ + ": " + str(_e)))


'''

    footer = '''

# ── 데이터 보존 — 스냅샷 + 검증 + 복구 ───────────────────────────────────
import hashlib as _hashlib
import shutil as _shutil
import time as _time
from datetime import datetime as _dt


def _resolve_data_roots() -> list:
    """보호 대상 루트 디렉토리 목록 (존재하는 것만). FLOW_DATA_ROOT
    환경변수가 있으면 그쪽을, 없으면 ROOT/data 전체.

    `/config/work/sharedworkspace` 존재 시 사내 공유 경로를 자동 보호
    (flow-data + DB). 환경변수 없어도 setup.py 가 사용자 데이터를
    절대 덮어쓰지 않도록 보장.
    """
    roots = []
    for env_key in ("FLOW_DATA_ROOT",):
        v = os.environ.get(env_key)
        if v:
            p = Path(v).resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)
    # 사내 공유 경로 자동 보호.
    _shared = Path("/config/work/sharedworkspace")
    if _shared.is_dir():
        for sub in ("flow-data", "DB"):
            p = (_shared / sub).resolve()
            if p.is_dir() and p not in roots:
                roots.append(p)
    for sub in ("data", "data/flow-data", "data/DB", "data/Fab"):
        p = (ROOT / sub).resolve()
        if p.is_dir() and p not in roots:
            roots.append(p)
    # dedupe — drop any path that is a descendant of another root.
    uniq = []
    for p in sorted(roots, key=lambda x: len(str(x))):
        if not any(str(p).startswith(str(u) + os.sep) for u in uniq):
            uniq.append(p)
    return uniq


def _backups_dir() -> Path:
    """외부 백업 디렉토리 — ~/.flow_backups/ (repo 외부).
    기존 사용자 환경 호환성을 위해 이름 유지 — 폴더 리네임(2026-04-24) 후에도
    이전 버전 스냅샷을 그대로 복구 가능하게 하려는 의도."""
    home = Path(os.path.expanduser("~"))
    d = home / ".flow_backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 스냅샷 대상을 **소형 config/state 파일로 한정**.
#   이전에는 data_root 전체(parquet/CSV 원천 포함 수 GB)를 shutil.copytree 로
#   통째 복사 → 사내 공유 환경에서 setup.py 가 수 분~수 시간 멈춘 것처럼 보임.
#
# 핵심 원칙
# 1) DB(`/config/work/sharedworkspace/DB`)와 Base 는 **참조만** 한다.
# 2) DB/Base 는 스냅샷 백업 대상에서 **완전히 제외** — 복사 시도 자체 금지.
# 3) parquet/arrow 등 bulk 원천 확장자는 어떤 경로에서도 절대 복사/업로드 금지.
# 4) 백업 대상은 오직 **경량 설정/상태 파일**: users.csv, groups.json,
#    config.json, informs/**, meetings/**, calendar/** 등.
# 5) _write 가드(L0~L6)가 이미 DB/Base 쓰기를 차단 → 스냅샷은 소형 설정파일만
#    대상으로 해도 안전.
_SNAPSHOT_INCLUDE_EXT = {
    '.json', '.jsonl', '.csv', '.md', '.txt', '.yaml', '.yml', '.toml', '.ini',
}
# parquet/bulk 확장자는 **어떤 경우에도** 복사하지 않는다 (이중 방어).
_SNAPSHOT_FORBIDDEN_EXT = {
    '.parquet', '.pq', '.arrow', '.feather', '.orc', '.avro',
    '.db', '.sqlite', '.sqlite3',
    '.zip', '.gz', '.bz2', '.xz', '.7z', '.tar',
    '.bin', '.pkl', '.pickle', '.npy', '.npz',
    '.mp4', '.mov', '.avi', '.mp3', '.wav',
    '.exe', '.dll', '.so', '.dylib',
}
_SNAPSHOT_MAX_FILE_BYTES = 5 * 1024 * 1024   # 개별 파일 5MB 상한
_SNAPSHOT_MAX_TOTAL_BYTES = 200 * 1024 * 1024  # 루트당 총 200MB 상한 (초과 시 중단)
_SNAPSHOT_MAX_FILES = 20000                   # 루트당 파일 수 상한
_SNAPSHOT_SKIP_DIRNAMES = {
    '__pycache__', '.trash', 'uploads', 'cache', '_backups',
    # **DB 트리는 통째 배제** — parquet hive 원천은 어떤 파일도 복사 금지.
    'DB', 'wafer_maps', 'parquet', 'Fab',
    # NOTE: 'Base' 는 **제외하지 않음** — Base 안에는 rulebook CSV/JSON/TXT 같은
    #   경량 설정 파일이 있고 이건 백업 대상. 대형 parquet 는 아래 확장자/크기
    #   필터로 차단.
}
# 절대 경로로도 하드-코딩 배제: DB 원천 트리.
# Base 는 path substring 배제 대상에서 제외 — 소형 파일은 백업 필요.
_SNAPSHOT_FORBIDDEN_PATH_SUBSTR = (
    '/config/work/sharedworkspace/DB',
    '/config/work/sharedworkspace/wafer_maps',
)


def _is_forbidden_bulk_path(p: Path) -> bool:
    """DB/wafer_maps/parquet 가 경로 어디에든 세그먼트로 있으면 True.
    DB 원천 데이터는 어떤 방식으로도 외부 반출 금지.
    Base 는 여기서 차단하지 않음 — 대형 parquet 는 확장자/크기 필터가 거르고,
    Base 하위 소형 설정 파일(csv/json/txt)은 정상적으로 백업 대상.
    """
    try:
        s = str(p).replace('\\\\', '/')
    except Exception:
        return False
    for seg in ('DB', 'wafer_maps', 'parquet', 'Fab'):
        if f"/{seg}/" in s or s.endswith(f"/{seg}"):
            return True
    for sub in _SNAPSHOT_FORBIDDEN_PATH_SUBSTR:
        if s.startswith(sub) or f"{sub}/" in s:
            return True
    return False


def _should_snapshot_file(p: Path) -> bool:
    ext = p.suffix.lower()
    # 이중 방어: forbidden 확장자 (parquet/arrow/pickle/zip 등) 절대 거부.
    if ext in _SNAPSHOT_FORBIDDEN_EXT:
        return False
    if ext not in _SNAPSHOT_INCLUDE_EXT:
        return False
    if _is_forbidden_bulk_path(p):
        return False
    try:
        if p.stat().st_size > _SNAPSHOT_MAX_FILE_BYTES:
            return False
    except Exception:
        return False
    return True


def _file_hashes(root: Path) -> dict:
    """root 아래 스냅샷 대상 파일의 SHA-256 해시 맵. 상대경로 key.
    bulk data(parquet 등) 는 해싱 대상이 아니므로 skip.
    """
    out = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # skip segments we also skipped at snapshot time
        if any(part in _SNAPSHOT_SKIP_DIRNAMES for part in p.parts):
            continue
        if not _should_snapshot_file(p):
            continue
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        h = _hashlib.sha256()
        try:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            out[rel] = h.hexdigest()
        except Exception:
            out[rel] = "__unreadable__"
    return out


def _snapshot_roots() -> list:
    """스냅샷 대상 루트 — _resolve_data_roots() 중 bulk data root 는 완전히 제외.

    `/config/work/sharedworkspace/DB` 같은 수 GB 원천 parquet 루트는 스냅샷에서
    처음부터 배제 (_write L0~L6 가드가 이미 쓰기를 차단).
    basename 뿐 아니라 절대 경로 substring 도 체크 (defense in depth).
    """
    out = []
    for r in _resolve_data_roots():
        # DB/wafer_maps/Fab/parquet 가 root 이름이면 통째 배제.
        # Base 는 root 가 되어도 허용 — 대형 parquet 는 내부에서 확장자로 거름.
        if r.name in {"DB", "wafer_maps", "Fab", "parquet"}:
            print(f"[snapshot]   skip bulk data root {r}")
            continue
        if _is_forbidden_bulk_path(r):
            print(f"[snapshot]   skip forbidden bulk path {r}")
            continue
        out.append(r)
    return out


def _walk_snapshot(root: Path):
    """os.walk with dir pruning — bulk/skip 디렉토리로는 **들어가지도** 않는다.
    yield (abs_file_path, size) tuples for files matching include filter.
    """
    for dirpath, dirnames, filenames in os.walk(str(root)):
        # prune in-place so os.walk doesn't recurse into skipped dirs
        dirnames[:] = [d for d in dirnames if d not in _SNAPSHOT_SKIP_DIRNAMES]
        # forbidden-path prune (defense in depth against symlink/renamed dirs)
        dp = str(dirpath).replace('\\\\', '/')
        if any(sub in dp for sub in _SNAPSHOT_FORBIDDEN_PATH_SUBSTR):
            dirnames[:] = []
            continue
        for fn in filenames:
            p = Path(dirpath) / fn
            if not _should_snapshot_file(p):
                continue
            try:
                sz = p.stat().st_size
            except Exception:
                continue
            yield p, sz


def _snapshot_data() -> Path | None:
    """추출 직전 data_root 스냅샷. 반환: 스냅샷 디렉토리 경로 (없으면 None).

    **소형 config/state 파일만 복사** — parquet/CSV-bulk/대형 binary 는 skip.
    루트별 진행 상황 즉시 출력 (setup.py 가 멈춰 보이지 않도록).
    """
    roots = _snapshot_roots()
    if not roots:
        print("[snapshot] no eligible data roots - skipping")
        return None
    stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    snap = _backups_dir() / f"v{VERSION}-{stamp}"
    snap.mkdir(parents=True, exist_ok=True)
    manifest = {"version": VERSION, "created_at": stamp, "roots": {}}
    grand_files = 0
    grand_bytes = 0
    t_start = _time.time()
    print(f"[snapshot] scanning {len(roots)} root(s) for config/state files "
          f"(ext={sorted(_SNAPSHOT_INCLUDE_EXT)}, <={_SNAPSHOT_MAX_FILE_BYTES//1024//1024}MB/file, "
          f"<={_SNAPSHOT_MAX_TOTAL_BYTES//1024//1024}MB/root)")
    for root in roots:
        print(f"[snapshot]   scan {root}", flush=True)
        t0 = _time.time()
        tag = root.name or "root"
        dest = snap / tag
        i = 1
        while dest.exists():
            dest = snap / f"{tag}__{i}"
            i += 1
        n_files = 0
        n_bytes = 0
        capped = False
        try:
            for src, sz in _walk_snapshot(root):
                if n_bytes + sz > _SNAPSHOT_MAX_TOTAL_BYTES or n_files >= _SNAPSHOT_MAX_FILES:
                    capped = True
                    print(f"[snapshot]     ! cap reached at {n_files} files / "
                          f"{n_bytes/1024/1024:.1f} MB - skipping remainder of {root}")
                    break
                try:
                    rel = src.relative_to(root)
                except Exception:
                    continue
                dst_f = dest / rel
                try:
                    dst_f.parent.mkdir(parents=True, exist_ok=True)
                    _shutil.copy2(str(src), str(dst_f))
                    n_files += 1
                    n_bytes += sz
                except Exception as e:
                    print(f"[snapshot]     WARN copy {rel}: {e}")
            if n_files > 0:
                manifest["roots"][str(root)] = str(dest.relative_to(snap))
            else:
                try:
                    if dest.is_dir() and not any(dest.rglob("*")):
                        _shutil.rmtree(str(dest), ignore_errors=True)
                except Exception:
                    pass
        except Exception as e:
            print(f"[snapshot] WARN scan failed {root}: {e}")
        dt = _time.time() - t0
        suffix = " (capped)" if capped else ""
        print(f"[snapshot]     {n_files} files, {n_bytes/1024/1024:.1f} MB, "
              f"{dt:.1f}s{suffix}", flush=True)
        grand_files += n_files
        grand_bytes += n_bytes
    (snap / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[snapshot] total {grand_files} files, {grand_bytes/1024/1024:.1f} MB, "
          f"{_time.time()-t_start:.1f}s -> {snap}")
    return snap


def _verify_and_restore(snap: Path | None) -> None:
    """추출 후 data_root 가 스냅샷과 동일한지 확인. 변경된 파일이 있으면
    스냅샷에서 즉시 복구 + loud 경고."""
    if snap is None or not snap.is_dir():
        return
    manifest_path = snap / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except Exception:
        return
    bad = []
    for original_root_str, snap_sub in (manifest.get("roots") or {}).items():
        orig = Path(original_root_str)
        snap_root = snap / snap_sub
        if not snap_root.is_dir():
            continue
        # Spot-check: any file that existed in snapshot but is MISSING or DIFFERENT now.
        snap_hashes = _file_hashes(snap_root)
        now_hashes = _file_hashes(orig)
        for rel, h_snap in snap_hashes.items():
            h_now = now_hashes.get(rel)
            if h_now is None:
                bad.append((orig, rel, "MISSING"))
            elif h_now != h_snap:
                bad.append((orig, rel, "MODIFIED"))
    if not bad:
        print(f"[verify] data integrity OK ({len(manifest.get('roots') or {})} roots)")
        return
    # Restore
    print(f"[verify] !!! {len(bad)} protected files changed - restoring from {snap}")
    for orig, rel, reason in bad:
        # locate in snap
        for sub in (manifest.get("roots") or {}).values():
            src = snap / sub / rel
            if src.is_file():
                dst = orig / rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _shutil.copy2(str(src), str(dst))
                    print(f"  [restore] {reason}: {rel}")
                except Exception as e:
                    print(f"  [restore] FAIL {rel}: {e}")
                break
    print(f"[verify] restored {len(bad)} files from snapshot")


def restore(argv: list = None) -> int:
    """수동 복구: `python setup.py restore [latest|<timestamp>]`."""
    argv = argv or []
    want = (argv[0] if argv else "latest").strip()
    bdir = _backups_dir()
    snaps = sorted([p for p in bdir.iterdir() if p.is_dir()], key=lambda p: p.name)
    if not snaps:
        print(f"[restore] no snapshots in {bdir}")
        return 1
    chosen = None
    if want == "latest":
        chosen = snaps[-1]
    else:
        for p in snaps:
            if want in p.name:
                chosen = p
                break
    if chosen is None:
        print(f"[restore] no match for '{want}'. Available:")
        for p in snaps[-10:]:
            print(f"  - {p.name}")
        return 1
    mf_path = chosen / "manifest.json"
    if not mf_path.is_file():
        print(f"[restore] manifest missing in {chosen}")
        return 1
    manifest = json.loads(mf_path.read_text("utf-8"))
    restored = 0
    for original_root_str, snap_sub in (manifest.get("roots") or {}).items():
        orig = Path(original_root_str)
        snap_root = chosen / snap_sub
        if not snap_root.is_dir():
            continue
        orig.mkdir(parents=True, exist_ok=True)
        for src in snap_root.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(snap_root)
            dst = orig / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(str(src), str(dst))
            restored += 1
    print(f"[restore] {restored} files restored from {chosen}")
    return 0


def list_snapshots(argv: list = None) -> int:
    bdir = _backups_dir()
    snaps = sorted([p for p in bdir.iterdir() if p.is_dir()], key=lambda p: p.name)
    if not snaps:
        print(f"[snapshots] (none) at {bdir}")
        return 0
    print(f"[snapshots] {bdir}:")
    for p in snaps[-20:]:
        sz = sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) / (1024*1024)
        n = sum(1 for f in p.rglob('*') if f.is_file())
        print(f"  {p.name}  ({n} files, {sz:.1f} MB)")
    return 0


def _run(cmd: str, cwd: Path, check: bool = False, timeout: int | None = None) -> int:
    print(f"\\n$ ({cwd.name}) {cmd}")
    try:
        r = subprocess.run(cmd, cwd=str(cwd), shell=True, timeout=timeout)
        if check and r.returncode != 0:
            print(f"  -> exit {r.returncode}")
        return r.returncode
    except subprocess.TimeoutExpired:
        print(f"  -> TIMEOUT after {timeout}s - skipping")
        return 124
    except FileNotFoundError as e:
        print(f"  -> not found: {e}")
        return 127


def _has(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def _ensure_pip_ready() -> None:
    def run_python(args, timeout):
        argv = [sys.executable, *args]
        print("\\n$ (" + ROOT.name + ") " + ' '.join(shlex.quote(p) for p in argv))
        try:
            return subprocess.run(argv, cwd=str(ROOT), timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            print(f"  -> TIMEOUT after {timeout}s - skipping")
            return 124
        except FileNotFoundError as e:
            print(f"  -> not found: {e}")
            return 127

    rc = run_python(['-m', 'pip', '--version'], 30)
    if rc == 0:
        return
    print("[deps] python -m pip is not ready; trying ensurepip bootstrap")
    run_python(['-m', 'ensurepip', '--upgrade'], 120)


def _pip_install(pkgs: list[str], timeout: int | None = None) -> int:
    _ensure_pip_ready()
    argv = [sys.executable, '-m', 'pip', 'install',
            '--disable-pip-version-check', *pkgs]
    print("\\n$ (" + ROOT.name + ") " + ' '.join(shlex.quote(p) for p in argv))
    try:
        return subprocess.run(argv, cwd=str(ROOT), timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"  -> TIMEOUT after {timeout}s - skipping")
        return 124
    except FileNotFoundError as e:
        print(f"  -> not found: {e}")
        return 127


_INSTALL_ATTEMPTS = []
_SETUP_STAGES = {}


def _dependency_spec(dist, minimum):
    return dist + ((">=" + minimum) if minimum else "")


def _dependency_issues(rows):
    """Return every unanswered, missing, too-old, or import-broken dependency."""
    by_dist = {str(row.get('dist')): row for row in (rows or [])}
    issues = []
    for imports, dist, minimum, level, purpose in DEPENDENCY_CHECKS:
        row = by_dist.get(dist)
        installed = str((row or {}).get('installed') or '')
        error = str((row or {}).get('error') or '')
        reason = ''
        if row is None:
            reason = '점검 응답 없음'
        elif error:
            reason = 'import 실패: ' + error
        elif not installed:
            reason = '설치 버전 확인 불가'
        elif minimum and _version_tuple(installed) < _version_tuple(minimum):
            reason = '최소 ' + minimum + ' 미만 (' + installed + ')'
        if reason:
            issues.append({'dist': dist, 'spec': _dependency_spec(dist, minimum),
                           'level': level, 'purpose': purpose, 'reason': reason})
    return issues


def _record_install(kind, packages, rc):
    _INSTALL_ATTEMPTS.append({'kind': kind, 'packages': list(packages), 'returncode': int(rc)})


def _ensure_checked_deps() -> list[str]:
    """Retry unresolved packages one by one so one unavailable extra cannot block the rest."""
    rows, probe_error = _probe_dependency_rows()
    if probe_error:
        print('[deps] 설치 후 점검 경고: ' + probe_error)
    issues = _dependency_issues(rows)
    if not issues:
        return []
    print('[deps] 개별 재시도 필요: ' + ', '.join(item['spec'] for item in issues))
    failed = []
    for item in issues:
        rc = _pip_install([item['spec']], timeout=180)
        _record_install('individual', [item['spec']], rc)
        if rc != 0:
            failed.append(item['dist'])
            print('[deps] FAIL 개별 설치: ' + item['dist'] + ' (rc=' + str(rc)
                  + ', 등급=' + item['level'] + ')', file=sys.stderr)
    if failed:
        print('[deps] 설치 명령 실패 패키지: ' + ', '.join(failed)
              + ' — 아래 최종 점검은 현재 실제 사용 가능 상태를 별도로 표시합니다.', file=sys.stderr)
    return failed


def _seed_domain_knowledge() -> None:
    if not DOMAIN_KNOWLEDGE_PAYLOAD:
        return
    # These modules use only the standard library, before pip dependencies exist.
    import importlib.util

    def load(name, relative):
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    helper = load('flow_install_knowledge', 'backend/core/domain_knowledge_seed.py')
    profile = load('flow_install_roots', 'backend/core/root_profile.py')
    data_root = Path(os.environ.get('FLOW_DATA_ROOT') or profile.default_data_root())
    document = json.loads(gzip.decompress(base64.b64decode(DOMAIN_KNOWLEDGE_PAYLOAD)))
    installed = helper.install_seed(ROOT, document, data_root)
    print('[seed] domain knowledge ' + ('installed' if installed else 'preserved'))
    print('[seed] local seed: ' + str(ROOT / helper.SEED_RELATIVE_PATH))


def _seed_semiconductor_flow_data() -> None:
    """Install default RCA seed knowledge only when the runtime copy is absent."""
    src = ROOT / 'backend' / 'core' / 'semiconductor_rca_seed_knowledge.json'
    if not src.is_file():
        return
    flow_root = Path(os.environ.get('FLOW_DATA_ROOT') or (ROOT / 'data' / 'flow-data')).resolve()
    dst = flow_root / 'semiconductor' / 'seed_knowledge' / 'semiconductor_rca_seed_knowledge.json'
    if dst.exists():
        print(f"[seed] semiconductor RCA seed preserved: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    print(f"[seed] semiconductor RCA seed installed: {dst}")


def _seed_filebrowser_agent_prompts() -> None:
    """Install FileBrowser LLM prompt defaults only when the runtime copy is absent."""
    src = ROOT / 'backend' / 'core' / 'filebrowser_agent_prompts.default.json'
    if not src.is_file():
        return
    flow_root = Path(os.environ.get('FLOW_DATA_ROOT') or (ROOT / 'data' / 'flow-data')).resolve()
    dst = flow_root / 'filebrowser_agent_prompts.json'
    if dst.exists():
        print(f"[seed] FileBrowser agent prompts preserved: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    print(f"[seed] FileBrowser agent prompts installed: {dst}")


def _seed_semantic_measure_terms() -> None:
    """Install or merge default semantic measurement term templates."""
    src = ROOT / 'backend' / 'core' / 'semantic_measure_defaults.json'
    if not src.is_file():
        return
    try:
        defaults_payload = json.loads(src.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"[seed] WARN semantic measurement defaults unreadable: {e}")
        return
    defaults = [row for row in defaults_payload.get('terms', []) if isinstance(row, dict) and row.get('id')]
    if not defaults:
        return
    flow_root = Path(os.environ.get('FLOW_DATA_ROOT') or (ROOT / 'data' / 'flow-data')).resolve()
    dst = flow_root / 'semantic' / 'measurement_terms.json'
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    if dst.exists():
        try:
            existing = json.loads(dst.read_text(encoding='utf-8'))
        except Exception:
            existing = {}
        terms = existing.get('terms') if isinstance(existing, dict) else []
        by_id = {str(row.get('id') or ''): row for row in terms if isinstance(row, dict) and row.get('id')}
        added = 0
        for row in defaults:
            term_id = str(row.get('id') or '')
            if term_id and term_id not in by_id:
                by_id[term_id] = row
                added += 1
        if not added:
            print(f"[seed] semantic measurement terms preserved: {dst}")
            return
        payload = dict(existing) if isinstance(existing, dict) else {}
        payload['version'] = int(payload.get('version') or defaults_payload.get('version') or 1)
        payload['description'] = payload.get('description') or defaults_payload.get('description') or ''
        payload['updated_at'] = now
        payload['updated_by'] = 'setup.py'
        payload['terms'] = sorted(by_id.values(), key=lambda r: (str(r.get('source_type') or ''), str(r.get('product') or ''), str(r.get('term') or '')))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
        print(f"[seed] semantic measurement terms merged: +{added} -> {dst}")
        return
    payload = {
        'version': int(defaults_payload.get('version') or 1),
        'description': defaults_payload.get('description') or '',
        'created_at': now,
        'updated_at': now,
        'updated_by': 'setup.py',
        'terms': sorted(defaults, key=lambda r: (str(r.get('source_type') or ''), str(r.get('product') or ''), str(r.get('term') or ''))),
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
    print(f"[seed] semantic measurement terms installed: {dst}")


def _seed_default_agent_wiki_docs() -> None:
    """Install bundled Agent Wiki defaults only when each runtime doc is absent."""
    src_dir = ROOT / 'backend' / 'core' / 'default_agent_wiki_seed'
    if not src_dir.is_dir():
        return
    flow_root = Path(os.environ.get('FLOW_DATA_ROOT') or (ROOT / 'data' / 'flow-data')).resolve()
    wiki_dir = flow_root / 'knowledge' / 'wiki'
    installed = 0
    preserved = 0
    skipped = 0
    installed_docs = []

    def safe_name(value: object, fallback: str = 'item') -> str:
        text = ''.join(ch if (ch.isalnum() or ch in '._-') else '_' for ch in str(value or '').strip()).strip('._-')
        return text[:160] or fallback

    def frontmatter(text: str) -> tuple[dict, str]:
        if not text.startswith('---\\n'):
            return {}, text
        end = text.find('\\n---', 4)
        if end < 0:
            return {}, text
        meta = {}
        for line in text[4:end].strip().splitlines():
            if ':' not in line:
                continue
            key, raw = line.split(':', 1)
            value = raw.strip()
            if value.startswith('[') or value.startswith('{'):
                try:
                    meta[key.strip()] = json.loads(value)
                    continue
                except Exception:
                    pass
            meta[key.strip()] = value
        return meta, text[end + 5:].lstrip('\\n')

    for src in sorted(src_dir.rglob('*.md')):
        if src.name.startswith('_'):
            skipped += 1
            continue
        try:
            text = src.read_text(encoding='utf-8')
        except Exception:
            skipped += 1
            continue
        meta, _body = frontmatter(text)
        doc_id = safe_name(meta.get('doc_id') or '', fallback='')
        if not doc_id:
            skipped += 1
            continue
        kind = safe_name(meta.get('kind') or 'agent_wiki', fallback='agent_wiki')
        exists = any(fp.stem == doc_id for fp in wiki_dir.rglob('*.md')) if wiki_dir.is_dir() else False
        if exists:
            preserved += 1
            continue
        dst = wiki_dir / kind / f'{doc_id}.md'
        if dst.exists():
            preserved += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text.rstrip() + '\\n', encoding='utf-8')
        installed += 1
        installed_docs.append({'doc_id': doc_id, 'path': str(dst.relative_to(flow_root / 'knowledge'))})

    if installed:
        log_file = flow_root / 'knowledge' / 'index' / 'wiki_log.jsonl'
        log_file.parent.mkdir(parents=True, exist_ok=True)
        row = {
            'created_at': _dt.now().astimezone().isoformat(timespec='seconds'),
            'action': 'default_seed_install',
            'actor': 'setup.py',
            'doc_id': '',
            'source_ids': [],
            'title': 'Default Agent Wiki seed',
            'message': f'Installed {installed} default Agent Wiki seed docs',
            'meta': {'installed': installed_docs, 'preserved_count': preserved, 'skipped': skipped},
            'log_id': f"log_{_dt.now().strftime('%Y%m%d%H%M%S')}_default_seed",
        }
        with log_file.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + '\\n')
        print(f"[seed] default Agent Wiki docs installed: {installed}")
    else:
        print(f"[seed] default Agent Wiki docs preserved: {preserved}, skipped: {skipped}")


# 사내 web 운영에 필요 없는 번들 항목 — extract 기본값에서 제외한다.
# 번들에는 그대로 담긴다(origin/main 이 두 파일뿐이라 번들이 유일한 원격 사본).
# 풀어야 할 때는 `python setup.py extract --all`.
#
# scripts/ 는 제외하지 않는다 — preflight_internal.py(사내 반입 점검)와
# flow_server.py(감시기)·windows/*.bat 이 운영 절차에 들어 있다.
_RUNTIME_SKIP_TOP = {'tests', 'docs'}
_RUNTIME_SKIP_FILES = {'CLAUDE.md', 'AGENTS.md', '_build_setup.py', '.gitignore', '.gitattributes'}


def _runtime_only_skip(rel: str) -> bool:
    rel_posix = rel.replace('\\\\', '/').lstrip('./')
    if rel_posix in _RUNTIME_SKIP_FILES:
        return True
    head = rel_posix.split('/', 1)[0]
    return head in _RUNTIME_SKIP_TOP


def _dist_self_check() -> list:
    """추출된 dist 의 index.html 참조 asset 실존 검사 + extract_report.json 기록.

    운영 Docker 서버는 셸 접근이 없어 extract 로그를 볼 수 없다. 결과를
    extract_report.json 으로 남기면 앱의 /deploy-info.json 이 브라우저 부팅
    진단 패널로 전달한다 — 화면 캡처만으로 추출 실패/프록시 문제를 가릴 수 있다.
    """
    import re as _re
    dist = ROOT / 'frontend' / 'dist'
    index = dist / 'index.html'
    refs, missing = [], []
    if index.is_file():
        try:
            html = index.read_text(encoding='utf-8', errors='replace')
            refs = sorted(set(_re.findall('assets/[A-Za-z0-9_.-]+[.](?:js|css)', html)))
            missing = [r for r in refs if not (dist / r).is_file()]
        except OSError as e:
            missing = ['index.html 읽기 실패: ' + str(e)]
    else:
        missing = ['frontend/dist/index.html 없음']
    try:
        import shutil as _sh
        disk_free_mb = _sh.disk_usage(str(ROOT)).free // (1024 * 1024)
    except OSError:
        disk_free_mb = None
    report = {
        'at': datetime.datetime.now().isoformat(timespec='seconds'),
        'version': VERSION,
        'index_refs': refs,
        'missing': missing,
        'write_failures': [rel + ' — ' + err for rel, err in _WRITE_FAILURES],
        'disk_free_mb': disk_free_mb,
    }
    try:
        (ROOT / 'extract_report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    except OSError as e:
        print('[extract] WARN extract_report.json 쓰기 실패: ' + str(e), file=sys.stderr)
    return missing


def extract(argv: list = None) -> int:
    # 기본은 운영 파일만. --all 이면 문서·테스트까지 전부 푼다.
    argv = argv or []
    # 모르는 인자는 조용히 무시하지 않는다. extract 는 **항상 현재 트리**에 풀며
    # 대상 폴더를 못 바꾼다 — `--dest /tmp/...` 처럼 없는 옵션을 붙이면 안전한
    # 임시 추출로 착각한 채 소스 트리를 번들 시점으로 되돌리게 된다.
    unknown = [a for a in argv if a not in ('--all', '-a')]
    if unknown:
        print(f"[extract] 알 수 없는 인자: {' '.join(unknown)}", file=sys.stderr)
        print("[extract] 지원 인자는 --all 뿐이며, 추출 위치는 언제나 현재 폴더다 "
              "(다른 곳에 풀려면 setup.py 를 그 폴더로 복사해서 실행).", file=sys.stderr)
        return 2
    want_all = any(a in ('--all', '-a') for a in argv) or os.environ.get('FLOW_EXTRACT_ALL') == '1'
    # 추출 직전 data_root 스냅샷 (~/.flow_backups/v<ver>-<stamp>/).
    # 스냅샷 실패/없음이면 snap=None 으로 계속 진행 — 신규 설치는 보호할 게 없음.
    snap = None
    if os.environ.get("FLOW_SKIP_SNAPSHOT") == "1":
        print("[snapshot] skipped (FLOW_SKIP_SNAPSHOT=1)")
    else:
        print(f"[extract] flow {_version_time_label()} starting - snapshot + extract + deps")
        try:
            snap = _snapshot_data()
        except Exception as e:
            print(f"[snapshot] WARN failed: {e}")

    skipped = 0
    written = 0
    for rel, payload in FILES.items():
        if not want_all and _runtime_only_skip(rel):
            skipped += 1
            continue
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
    # VERSION.json 은 FILES 에 들어 있어 위 루프가 이미 번들 원본으로 썼다.
    # 여기서 VERSION_META 로 다시 덮어쓰면 release_notes 히스토리가 날아간다 —
    # 2026-07-31 에 실제로 9.5.x 전량이 그렇게 사라졌다. 파일이 없을 때만 채운다.
    _vj = ROOT / 'VERSION.json'
    if not _vj.is_file():
        _vj.write_text(
            json.dumps(VERSION_META, indent=2, ensure_ascii=False), encoding='utf-8'
        )
    for sub in ('data', 'data/flow-data'):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    # dist 정합 검사·기록은 파일 쓰기 직후에 한다 — 이후 단계(pip/seed)가 죽어도
    # extract_report.json 은 남아서 /deploy-info.json 이 실패 원인을 보여줄 수 있다.
    # (운영서버에서 '기록 없음' + asset 누락으로 원인 특정이 불가능했던 사례의 재발 방지)
    dist_missing = _dist_self_check()
    # extract 는 파일만 푼다. 의존성은 기존 requirements.txt 를 사용하는
    # `install-deps`(또는 인자 없는 기본 실행)의 책임이다.
    # 추출 후 data 변조 검증 — 변조된 파일은 즉시 스냅샷에서 복구.
    try:
        _verify_and_restore(snap)
    except Exception as e:
        print(f"[verify] WARN failed: {e}")
    # Unlike optional legacy seeds, a requested knowledge installation must not
    # report success when the database or local seed could not be written.
    try:
        _seed_domain_knowledge()
    except Exception as e:
        print(f"[seed] ERROR domain knowledge install failed: {e}", file=sys.stderr)
        return 1
    try:
        _seed_semiconductor_flow_data()
    except Exception as e:
        print(f"[seed] WARN semiconductor RCA seed install failed: {e}")
    try:
        _seed_filebrowser_agent_prompts()
    except Exception as e:
        print(f"[seed] WARN FileBrowser agent prompts install failed: {e}")
    try:
        _seed_semantic_measure_terms()
    except Exception as e:
        print(f"[seed] WARN semantic measurement terms install failed: {e}")
    try:
        _seed_default_agent_wiki_docs()
    except Exception as e:
        print(f"[seed] WARN default Agent Wiki seed install failed: {e}")
    if skipped:
        print(f"[extract] runtime-only: {skipped} files skipped "
              f"({', '.join(sorted(_RUNTIME_SKIP_TOP))} + {', '.join(sorted(_RUNTIME_SKIP_FILES))}) "
              f"- 전부 풀려면 `python setup.py extract --all`")
    print(f"\\n[extract] flow {_version_time_label()} - {len(FILES) - skipped} files processed -> {ROOT}")
    print(f"[extract] user data preservation: snapshot @ ~/.flow_backups/ + "
          f"5-layer _write guard + post-extract SHA-256 verify/restore.")
    print(f"[extract] manual restore: python setup.py restore [latest|<timestamp>]")
    if _WRITE_FAILURES:
        # 앱(uvicorn)이 실행 중이면 로드된 파일이 잠겨 쓰기가 실패할 수 있다 —
        # 배포 전 앱 중지 후 재실행 권장. 파이프라인은 중단하지 않는다(경고).
        print(f"[extract] WARN {len(_WRITE_FAILURES)}개 파일 쓰기 실패(잠김/권한 추정) — "
              f"운영 앱 실행 중이면 중지 후 재배포 권장:", file=sys.stderr)
        for rel, err in _WRITE_FAILURES[:20]:
            print(f"  - {rel}: {err}", file=sys.stderr)
        if len(_WRITE_FAILURES) > 20:
            print(f"  ... 외 {len(_WRITE_FAILURES) - 20}개", file=sys.stderr)
        print("[extract] 쓰기 실패로 추출 단계를 실패 처리(exit 1)", file=sys.stderr)
        return 1
    if dist_missing:
        print(f"[extract] FAIL frontend/dist 불완전 — index.html 이 참조하는 파일 누락: "
              f"{', '.join(dist_missing)}", file=sys.stderr)
        return 1
    return 0


def install_deps() -> int:
    _INSTALL_ATTEMPTS.clear()
    req = ROOT / 'requirements.txt'
    if req.is_file():
        print(f'[deps] using existing operator-managed requirements: {req}')
        rc = _pip_install(['-r', str(req)], timeout=600)
        _record_install('requirements', ['-r', str(req)], rc)
        failed = _ensure_checked_deps()
        return 1 if rc or failed else 0

    print('[deps] requirements.txt not found - installing Flow minimum dependencies')
    # DEPENDENCY_CHECKS 가 설치와 최종 점검의 단일 원천이다. uvicorn[standard] 같은
    # optional extra 를 필수 batch 에 넣지 않아 사내 mirror 의 extra 하나가 전체 설치를 막지 않는다.
    pkgs = [_dependency_spec(dist, minimum)
            for _imports, dist, minimum, _level, _purpose in DEPENDENCY_CHECKS]
    rc = _pip_install(pkgs, timeout=600)
    _record_install('flow-minimum-batch', pkgs, rc)
    failed = _ensure_checked_deps()
    return 1 if rc or failed else 0


def _setup_strict() -> bool:
    return os.environ.get('FLOW_SETUP_STRICT', '').strip().lower() not in ('', '0', 'false', 'no', 'off')


def _dist_asset_refs(html):
    """index.html 이 실제로 참조하는 /assets/... 경로 목록. re 없이 스캔한다."""
    refs = []
    marker = '"/assets/'
    i = html.find(marker)
    while i != -1:
        start = i + 1                      # 여는 따옴표 다음
        end = html.find('"', start)
        if end == -1:
            break
        refs.append(html[start:end])
        i = html.find(marker, end)
    return refs


def _dist_intact(fe) -> bool:
    """index.html 과 그 index.html 이 참조하는 자산이 모두 실제로 있는지 확인한다.

    index.html 존재 여부만 보는 것으로는 부족하다. vite 는 emptyOutDir 로 dist 를
    지우고 다시 쓰기 때문에, 빌드가 중간에 깨지면 index.html 은 있는데 그것이 가리키는
    /assets/*.js 는 없는 상태가 남는다. 그 상태를 '기존 dist 사용' 으로 통과시키면
    배포는 성공했다고 말하는데 브라우저는 '앱 파일을 서버에서 받지 못했습니다' 만 본다.
    """
    dist = fe / 'dist'
    index = dist / 'index.html'
    if not index.is_file():
        return False
    try:
        html = index.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return False
    refs = _dist_asset_refs(html)
    if not refs:
        return False                       # 참조가 하나도 없으면 정상 vite 산출물이 아니다
    for ref in refs:
        if not (dist / ref.lstrip('/')).is_file():
            return False
    return True


def _bundled_dist_matches(fe) -> bool:
    """Verify every shipped asset, including lazy page chunks not referenced by index.html."""
    hashes = FRONTEND_STAMP.get('dist_sha256') or {}
    if not hashes:
        return False
    try:
        return all(hashlib.sha256((fe / 'dist' / name).read_bytes()).hexdigest() == sha
                   for name, sha in hashes.items())
    except OSError:
        return False


def build_frontend() -> int:
    fe = ROOT / 'frontend'
    # 소스/lock 및 모든 자산이 검증된 번들과 같으면 npm 없이 설치할 수 있다.
    dist_ok = _dist_intact(fe)
    # 번들에 실린 dist 가 지금 추출된 frontend/src 와 같은 소스에서 나왔으면 npm 을
    # 아예 시도하지 않는다. 사내망에서는 registry 접근이 막혀 install/build 가 수 분
    # 걸려 실패한 뒤 어차피 이 dist 로 되돌아온다 — 그 경로를 통째로 없앤다.
    if dist_ok and FRONTEND_STAMP.get('verified') and FRONTEND_STAMP.get('src_sha'):
        try:
            current = _frontend_src_fingerprint()
        except Exception as e:
            current = ''
            print(f'[npm] frontend 지문 계산 실패 - 통상 경로로 진행: {e}')
        if current and current == FRONTEND_STAMP['src_sha'] and _bundled_dist_matches(fe):
            print(f"[npm] prebuilt frontend/dist 가 현재 소스와 일치 - npm 생략 "
                  f"(built {FRONTEND_STAMP.get('built_at', '?')}, "
                  f"{FRONTEND_STAMP.get('dist_files', 0)} files)")
            return 0
        if current:
            print('[npm] WARN 소스·lock 또는 dist 자산이 검증된 번들과 다릅니다 - 재빌드를 시도합니다')

    def _ok_or(rc, where):
        if rc == 0:
            return 0
        # 실패한 뒤에는 dist 를 '지금' 다시 본다. 위에서 잡아둔 dist_ok 는 빌드 시작 전
        # 스냅샷이라, vite 가 emptyOutDir 로 방금 지워버린 dist 를 근거로 '기존 dist 사용'
        # 이라고 잘못 보고할 수 있다 — 그게 첫 화면이 죽는 경로다.
        state = ('온전하지 않습니다 (index.html 이 참조하는 /assets 파일이 없음)'
                 if not _dist_intact(fe) else '현재 소스와 일치한다고 검증되지 않았습니다')
        print(f'[npm] {where} 실패(rc={rc}) - frontend/dist 가 ' + state + '.', file=sys.stderr)
        print('[npm]   -> 사내 npm 저장소·Node 버전과 위 오류를 확인하거나, '
              '개발 PC에서 python _build_setup.py 로 번들을 다시 만들어 배포하세요.', file=sys.stderr)
        return rc

    if not (fe / 'package.json').exists():
        print('frontend/package.json not found - skipping', file=sys.stderr)
        return 1
    if not _has('npm'):
        print('[npm] npm 실행 파일을 찾지 못했습니다 - 검증된 동일 소스 dist 를 재사용할 수 없고 '
              'frontend 를 빌드할 수 없습니다', file=sys.stderr)
        return 1
    if (ROOT / 'package.json').exists():
        root_cmd = 'npm ci' if (ROOT / 'package-lock.json').is_file() else 'npm install'
        rc = _ok_or(_run(root_cmd, cwd=ROOT, timeout=600), 'root ' + root_cmd)
        if rc != 0:
            return rc
    install_cmd = 'npm ci' if (fe / 'package-lock.json').is_file() else 'npm install'
    rc = _ok_or(_run(install_cmd, cwd=fe, timeout=600), 'frontend ' + install_cmd)
    if rc != 0:
        return rc
    rc = _ok_or(_run('npm run build', cwd=fe, timeout=600), 'npm run build')
    if not rc and not _dist_intact(fe):
        print('[npm] FAIL 빌드 명령은 성공했지만 frontend/dist 자산이 없습니다', file=sys.stderr)
        return 1
    return rc


def print_version() -> int:
    print(f"flow (flow) {_version_time_label()} - codename {CODENAME}")
    return 0


def sync_version_json() -> int:
    """번들이 들고 온 버전 메타를 VERSION.json 에 되쓴다 (명시 호출 전용).

    VERSION_META 는 빌드 시점 VERSION.json 전문이므로 릴리스 노트가 보존된다.
    그래도 현재 파일 쪽이 더 최신일 수 있어(빌드 후 노트를 더 적은 경우)
    노트 수가 줄어드는 쓰기는 거부한다 — 이 명령으로 이력을 잃지 않게."""
    vj = ROOT / 'VERSION.json'
    incoming = VERSION_META if isinstance(VERSION_META, dict) else {}
    incoming_notes = incoming.get('release_notes') or []
    if vj.is_file():
        try:
            current = json.loads(vj.read_text(encoding='utf-8'))
        except Exception:
            current = {}
        current_notes = (current or {}).get('release_notes') or []
        if len(current_notes) > len(incoming_notes):
            print(f"[version] skip - 작업트리 VERSION.json 이 더 많은 릴리스 노트를 "
                  f"갖고 있음 ({len(current_notes)} > {len(incoming_notes)})")
            return 0
    vj.write_text(json.dumps(incoming, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"VERSION.json mtime -> {_version_time_label()}")
    return 0


def _version_tuple(text):
    parts = []
    for piece in str(text or "").replace("-", ".").replace("+", ".").split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _major(text):
    parts = _version_tuple(text)
    if not parts:
        return ()
    # 0.x 라이브러리는 두 번째 자리까지가 호환 단위다.
    return parts[:2] if parts[0] == 0 else parts[:1]


def _dep_probe() -> int:
    """새 인터프리터에서 실제 import 결과를 JSON 한 줄로 출력한다(check_deps 가 부른다)."""
    import importlib
    import warnings
    from importlib import metadata

    rows = []
    for imports, dist, _minimum, _level, _purpose in DEPENDENCY_CHECKS:
        row = {"dist": dist, "module": imports[0], "installed": "", "error": "", "warnings": []}
        try:
            row["installed"] = metadata.version(dist)
        except Exception:
            row["installed"] = ""
        last_error = ""
        for name in imports:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    module = importlib.import_module(name)
                    row["module"] = name
                    last_error = ""
                    if not row["installed"]:
                        row["installed"] = str(getattr(module, "__version__", "") or "")
                    row["warnings"] = [str(w.message)[:200] for w in caught][:3]
                    break
                except Exception as exc:
                    last_error = (type(exc).__name__ + ": " + str(exc))[:300]
        row["error"] = last_error
        rows.append(row)
    print("FLOW_DEP_PROBE " + json.dumps(rows, ensure_ascii=False))
    return 0


def _probe_dependency_rows(timeout=300):
    """Run imports in a fresh interpreter and return (rows, diagnostic)."""
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_dep-probe"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except Exception as exc:
        return [], "점검 프로세스 실패: " + type(exc).__name__ + ": " + str(exc)
    rows = []
    parse_error = ''
    for line in (proc.stdout or "").splitlines():
        if line.startswith("FLOW_DEP_PROBE "):
            try:
                rows = json.loads(line[len("FLOW_DEP_PROBE "):])
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    rows = []
                    parse_error = "점검 결과 형식 오류"
            except Exception as exc:
                rows = []
                parse_error = "점검 결과 JSON 해석 실패: " + str(exc)
    diagnostics = []
    if proc.returncode != 0:
        diagnostics.append("점검 프로세스 rc=" + str(proc.returncode))
    if parse_error:
        diagnostics.append(parse_error)
    if not rows:
        diagnostics.append("점검 결과 없음")
    return rows, "; ".join(diagnostics)


def check_deps(*, strict_exit: bool = False) -> int:
    """설치가 끝난 뒤 필요한 라이브러리가 조건대로 있는지 표로 출력하고 install_check.json 에 남긴다.

    사내 저장소는 버전이 다르거나 없는 패키지가 있을 수 있다. 실제 서버와 같은 조건을
    보려고 새 인터프리터에서 import 해 본다(DLL 로드 실패·CPU 경고까지 잡힌다)."""
    print("")
    print("[check] 설치 점검 — Python " + ".".join(str(p) for p in sys.version_info[:3]) + " · " + sys.executable)
    print("[check] TESTED = 이 번들을 만든 개발 PC 의 버전(Python " + str(DEP_TESTED_VERSIONS.get("python") or "?")
          + "). 주 버전이 다르면 WARN 으로 알린다.", flush=True)
    rows, probe_error = _probe_dependency_rows()
    if probe_error:
        print("[check] " + probe_error, file=sys.stderr)
    by_dist = {row.get("dist"): row for row in rows}
    results = []
    counts = {"FAIL": 0, "WARN": 0, "OK": 0}
    if sys.version_info[:2] < (3, 10):
        results.append({"status": "FAIL", "package": "python", "installed": ".".join(str(p) for p in sys.version_info[:3]),
                        "required": ">=3.10", "tested": DEP_TESTED_VERSIONS.get("python", ""),
                        "level": "required", "purpose": "Flow 실행", "note": "Python 3.10 이상이 필요합니다"})
    for imports, dist, minimum, level, purpose in DEPENDENCY_CHECKS:
        row = by_dist.get(dist) or {}
        installed = str(row.get("installed") or "")
        tested = str(DEP_TESTED_VERSIONS.get(dist) or "")
        error = str(row.get("error") or "")
        notes = []
        bad = False
        if not rows:
            notes.append("점검 불가")
            bad = True
        elif dist not in by_dist:
            notes.append("점검 응답 없음")
            bad = True
        elif error:
            notes.append("import 실패 — " + error if installed else "설치되지 않음")
            bad = True
        elif not installed:
            notes.append("설치 버전 확인 불가")
            bad = True
        elif minimum and _version_tuple(installed) < _version_tuple(minimum):
            notes.append("최소 " + minimum + " 미만")
            bad = True
        if not bad and tested and installed and _major(installed) != _major(tested):
            notes.append("개발 검증 버전(" + tested + ")과 주 버전이 다름 — 동작 확인 필요")
        for warning in row.get("warnings") or []:
            notes.append("경고: " + warning)
        if bad:
            status = "FAIL" if level == "required" else "WARN"
        elif notes:
            status = "WARN"
        else:
            status = "OK"
        counts[status] = counts.get(status, 0) + 1
        results.append({"status": status, "package": dist, "installed": installed or "-",
                        "required": (">=" + minimum) if minimum else "-", "tested": tested or "-",
                        "level": level, "purpose": purpose, "note": "; ".join(notes)})
    width = max(len(r["package"]) for r in results) if results else 10
    # 한 스트림(stdout)으로만 출력한다 — stderr 를 섞으면 콘솔에서 줄 순서가 뒤섞인다.
    print("[check] " + "STATE".ljust(6) + "PACKAGE".ljust(width + 2) + "INSTALLED".ljust(14)
          + "MIN".ljust(9) + "TESTED".ljust(14) + "용도 / 비고")
    for r in results:
        line = ("[check] " + r["status"].ljust(6) + r["package"].ljust(width + 2) + str(r["installed"]).ljust(14)
                + str(r["required"]).ljust(9) + str(r["tested"]).ljust(14) + r["purpose"])
        if r["note"]:
            line += "  <- " + r["note"]
        print(line, flush=True)
    for tool in ("node", "npm"):
        print("[check] INFO " + tool + ": " + ("있음" if _has(tool) else "없음 — frontend/src 를 고쳐도 다시 빌드할 수 없습니다(번들 dist 사용)"))
    required_fail = [r["package"] for r in results if r["status"] == "FAIL"]
    warn = [r["package"] for r in results if r["status"] == "WARN"]
    summary = {}
    labels = {"required": "필수", "perf": "성능", "feature": "기능"}
    for level in ("required", "perf", "feature"):
        selected = [r for r in results if r["level"] == level]
        failed = [r["package"] for r in selected if r["status"] != "OK"]
        summary[level] = {"total": len(selected), "ok": len(selected) - len(failed),
                          "problems": failed}
        print("[check] 요약 " + labels[level] + ": " + str(len(selected) - len(failed))
              + "/" + str(len(selected)) + " OK"
              + ((" — " + ", ".join(failed)) if failed else ""))
    command_failures = [item for item in _INSTALL_ATTEMPTS if item.get('returncode')]
    report = {"checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
              "python": sys.executable, "version": VERSION, "results": results,
              "probe_error": probe_error, "summary": summary,
              "required_failures": required_fail, "warnings": warn,
              "installation": {"stages": dict(_SETUP_STAGES), "attempts": list(_INSTALL_ATTEMPTS),
                               "command_failures": command_failures}}
    report_written = True
    try:
        (ROOT / "install_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        report_written = False
        print("[check] FAIL install_check.json 쓰기 실패: " + str(exc), file=sys.stderr)
    if required_fail:
        print("[check] FAIL 필수 " + str(len(required_fail)) + "개: " + ", ".join(required_fail)
              + " — 서버가 뜨지 않거나 로그인·조회가 실패합니다. 사내 저장소에서 해당 패키지를 설치한 뒤 "
              + "python setup.py check-deps 로 다시 확인하세요.", flush=True)
    if warn:
        print("[check] WARN " + str(len(warn)) + "개: " + ", ".join(warn) + " — 해당 기능이 느리거나 실패할 수 있습니다.")
    if command_failures:
        print("[check] WARN 설치 명령 실패 " + str(len(command_failures))
              + "건 — install_check.json의 installation에서 재시도 결과와 함께 확인하세요.")
    if not required_fail and not warn:
        print("[check] OK 필요한 라이브러리가 모두 조건대로 설치되어 있습니다.")
    print("[check] 결과 파일: " + str(ROOT / "install_check.json"))
    if required_fail or not report_written:
        return 1
    return 0


def check_deps_command() -> int:
    return check_deps(strict_exit=True)


def install_deps_command() -> int:
    rc = install_deps()
    rc_check = check_deps()
    return rc or rc_check


def _setup_stage(name, fn):
    try:
        rc = int(fn() or 0)
    except Exception as exc:
        print('[setup] FAIL ' + name + ': ' + type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        rc = 1
    _SETUP_STAGES[name] = rc
    return rc


def all_steps() -> int:
    # 어느 단계가 실패해도 최종 dependency report 를 남긴다. 설치 명령의 실패와
    # 실제 import 가능 상태는 구별하며, required FAIL 은 strict 와 무관하게 실패다.
    strict = _setup_strict()
    _SETUP_STAGES.clear()
    rc_extract = _setup_stage('extract', extract)
    if rc_extract != 0:
        print(f"[setup] extract 실패(rc={rc_extract}) - 소스 추출 단계는 필수", file=sys.stderr)
    rc_deps = _setup_stage('python_dependencies', install_deps)
    if rc_deps != 0:
        print(f"[setup] WARN install_deps rc={rc_deps} - 최종 점검에서 현재 사용 가능 상태를 확인합니다")
    rc_fe = _setup_stage('frontend', build_frontend)
    if rc_fe != 0:
        print(f"[setup] FAIL build_frontend rc={rc_fe} - 최종 dependency report 작성 후 실패 처리", file=sys.stderr)
    # best-effort 는 '기존 dist 로 앱이 뜬다' 를 전제로 한 완화다. dist 자체가 깨져 있으면
    # 그 전제가 사라진다 — 배포는 성공이라고 말하는데 사용자는 첫 화면부터 죽은 화면을 본다.
    # 이 경우만은 조용히 넘어가지 않는다.
    if not _dist_intact(ROOT / 'frontend'):
        print("[setup] FAIL frontend/dist 가 온전하지 않습니다 - index.html 이 참조하는 "
              "/assets 파일이 없어 첫 화면이 '앱 파일을 서버에서 받지 못했습니다' 로 뜹니다.",
              file=sys.stderr)
        print("[setup]   -> 개발 PC에서 python _build_setup.py 로 번들을 다시 만들어 배포하세요.",
              file=sys.stderr)
        rc_fe = rc_fe or 1
        _SETUP_STAGES['frontend'] = rc_fe
    # 사내 저장소는 버전이 다르거나 빠진 패키지가 있을 수 있다 — 마지막에 조건대로 설치됐는지 표로 보여 준다.
    rc_check = check_deps()
    if rc_check:
        print("[setup] 필수 dependency 점검 실패 - 설치를 실패 처리", file=sys.stderr)
        return rc_check
    if strict and (rc_extract or rc_deps or rc_fe or rc_check):
        print("[setup] FLOW_SETUP_STRICT=1 - 하위 단계 실패로 실패 처리", file=sys.stderr)
        return rc_extract or rc_deps or rc_fe or rc_check
    if rc_extract or rc_fe:
        return rc_extract or rc_fe
    print(f"\\n[done] Windows: scripts/windows/flow_run.bat   (run from {ROOT})")
    print("[done] Other platforms: python scripts/flow_server.py")
    print("[done] initial admin 'hol' is created only when FLOW_ADMIN_PW is explicitly set (10+ characters)")
    print("[done] 필수 라이브러리·프런트 확인 완료. WARN 항목은 위 표와 install_check.json을 확인하세요.")
    return 0


COMMANDS = {
    'extract':        extract,
    'install-deps':   install_deps_command,
    'check-deps':     check_deps_command,
    '_dep-probe':     _dep_probe,
    'build-frontend': build_frontend,
    'version':        print_version,
    'sync-version':   sync_version_json,
    'all':            all_steps,
    'restore':        restore,
    'snapshots':      list_snapshots,
    'snapshot':       lambda: (_snapshot_data() and 0) or 0,
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
    # restore/extract take extra args
    if cmd in ('restore', 'extract'):
        return fn(argv[1:])
    return fn()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
'''

    return header + files_block + footer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-domain-knowledge', nargs='?', const='auto', metavar='SQLITE',
                        help='Include the latest saved domain knowledge in a PRIVATE local installer; optionally specify its SQLite path.')
    parser.add_argument('--output', type=Path, help='Installer output path (private installers must be outside this public repository).')
    args = parser.parse_args(argv)
    dst = (args.output or ROOT / 'setup.py').resolve()
    payload = ''
    if args.include_domain_knowledge:
        if not args.output or dst.is_relative_to(ROOT.resolve()):
            parser.error('Knowledge contains private content: use --output outside the public repository, e.g. ../deliverables/flow-private/setup.py')
        payload = _domain_knowledge_payload(args.include_domain_knowledge)
        print('[build] PRIVATE installer: includes latest domain knowledge; do not publish it.')
    out = build(payload)
    compile(out, str(dst), 'exec')
    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=dst.parent,
                                         prefix=dst.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(out)
        temporary.replace(dst)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
