"""setup.py 설치 끝의 라이브러리 점검 표 — 번들 전체를 만들지 않고 생성 템플릿만 검증한다."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def generated_setup(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("flow_build_setup_under_test", ROOT / "_build_setup.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.ensure_frontend_build = lambda: {"fingerprint": "test"}
    builder.gather_files = lambda: []
    text = builder.build("")
    target = tmp_path_factory.mktemp("setup") / "setup.py"
    target.write_text(text, encoding="utf-8")
    return target, builder


def test_generated_setup_carries_single_source_checks(generated_setup):
    target, builder = generated_setup
    text = target.read_text(encoding="utf-8")
    compile(text, str(target), "exec")
    assert "DEPENDENCY_CHECKS = " in text and "DEP_TESTED_VERSIONS = " in text
    assert "'check-deps':" in text and "rc_check = check_deps()" in text
    assert {dist for _i, dist, *_ in builder.DEPENDENCY_CHECKS} >= {"polars", "duckdb", "orjson", "psutil", "cryptography"}


def test_check_deps_prints_table_and_writes_report(generated_setup):
    target, _builder = generated_setup
    proc = subprocess.run([sys.executable, str(target), "check-deps"], cwd=str(target.parent),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    out = proc.stdout + proc.stderr
    assert "[check] 설치 점검" in out
    assert "polars" in out and "duckdb" in out
    report = json.loads((target.parent / "install_check.json").read_text(encoding="utf-8"))
    by_package = {row["package"]: row for row in report["results"]}
    assert by_package["polars"]["status"] == "OK"
    assert by_package["polars"]["required"] == ">=1.0"
    # 이 환경에 없는 필수 패키지가 있으면 종료 코드 1, 없으면 0.
    assert proc.returncode == (1 if report["required_failures"] else 0)


def test_version_helpers(generated_setup):
    target, _builder = generated_setup
    namespace = {}
    source = target.read_text(encoding="utf-8")
    start = source.index("def _version_tuple(")
    end = source.index("def _dep_probe(")
    exec(source[start:end], namespace)
    assert namespace["_version_tuple"]("1.40.1") == (1, 40, 1)
    assert namespace["_version_tuple"]("2.0.0rc1") == (2, 0, 0)
    assert namespace["_major"]("0.136.1") == (0, 136)
    assert namespace["_major"]("1.26.4") == (1,)


def _load_generated(generated_setup, tmp_path, monkeypatch):
    """생성된 설치기를 tmp_path 를 ROOT 로 임포트한다(번들 파일 없이 _write 가드만 검증)."""
    target, _builder = generated_setup
    copy = tmp_path / "setup.py"
    copy.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("FLOW_DATA_ROOT", "FLOW_DB_ROOT", "FLOW_WAFER_MAP_ROOT"):
        monkeypatch.setenv(name, str(tmp_path / "runtime" / name.lower()))
    spec = importlib.util.spec_from_file_location("generated_setup_under_test", copy)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(text="x"):
    import base64
    import gzip

    return base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")


def test_extract_keeps_frontend_source_whose_folder_names_look_like_data_roots(generated_setup, tmp_path, monkeypatch):
    """features/splittable·tracker·calendar 는 데이터 폴더 이름과 같아도 화면 소스다 — 버려지면 npm run build 가 실패한다."""
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    for rel in ("frontend/src/features/splittable/My_SplitTable.jsx",
                "frontend/src/features/tracker/My_Tracker.jsx",
                "frontend/src/features/calendar/My_Calendar.jsx",
                "backend/app_v2/modules/splittable/router.py"):
        module._write(rel, _payload())
        assert (tmp_path / rel).is_file(), rel


def test_extract_still_protects_data_like_paths(generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    for rel in ("flow-data/notes.txt", "data/x.txt", "frontend/data/x.txt", "frontend/cache/x.js",
                "scripts/logs/x.py", "backend/core/tracker/x.py", "docs/calendar/x.md", "frontend/public/users.csv"):
        module._write(rel, _payload())
        assert not (tmp_path / rel).exists(), rel


def test_frontend_fingerprint_counts_only_files_that_ship(generated_setup):
    """번들에서 뺀 파일을 지문에 넣으면 설치기 지문과 영원히 달라져 설치마다 불필요한 npm 재빌드가 돈다."""
    import hashlib

    # 공용 fixture 는 빌드 속도를 위해 gather_files 를 비워 두므로, 실제 수집 함수를 가진 새 인스턴스를 쓴다.
    spec = importlib.util.spec_from_file_location("flow_build_setup_fingerprint_test", ROOT / "_build_setup.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    root = builder.ROOT
    shipped = {}
    for p in builder.gather_files():
        rel = p.relative_to(root).as_posix()
        if rel.startswith("frontend/src/"):
            shipped[rel[len("frontend/"):]] = p
    for name in builder._FE_STAMP_EXTRA:
        p = root / "frontend" / name
        if p.is_file():
            shipped[name] = p
    digest = hashlib.sha256()
    for rel in sorted(shipped):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(shipped[rel].read_bytes())
        digest.update(b"\0")
    assert builder.frontend_src_fingerprint(root) == digest.hexdigest()
    assert not any("diagnosis" in rel.lower() for rel in shipped)
