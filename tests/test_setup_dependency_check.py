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


def _ok_row(dist, version="1.0"):
    return {"dist": dist, "installed": version, "error": "", "warnings": []}


def test_fallback_batch_failure_retries_each_unresolved_package(generated_setup, tmp_path, monkeypatch, capsys):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    module.DEPENDENCY_CHECKS = [
        (("required_mod",), "required-one", "2.0", "required", "server"),
        (("optional_mod",), "optional-gone", None, "feature", "optional feature"),
    ]
    monkeypatch.setattr(module, "_probe_dependency_rows", lambda timeout=300: ([], "probe incomplete"))
    calls = []

    def fake_install(packages, timeout=None):
        calls.append((list(packages), timeout))
        if packages == ["optional-gone"]:
            return 1
        return 1 if len(packages) > 1 else 0

    monkeypatch.setattr(module, "_pip_install", fake_install)
    assert module.install_deps() == 1
    assert calls[0][0] == ["required-one>=2.0", "optional-gone"]
    assert (["required-one>=2.0"], 180) in calls
    assert (["optional-gone"], 180) in calls
    assert "FAIL 개별 설치: optional-gone" in capsys.readouterr().err


def test_required_failure_is_nonzero_without_strict_and_partial_probe_is_not_ok(
        generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    module.DEPENDENCY_CHECKS = [
        (("must",), "must-have", "2.0", "required", "server"),
        (("perf",), "fast-path", None, "perf", "speed"),
    ]
    monkeypatch.delenv("FLOW_SETUP_STRICT", raising=False)
    monkeypatch.setattr(module, "_probe_dependency_rows",
                        lambda timeout=300: ([_ok_row("fast-path")], "partial result"))
    assert module.check_deps() == 1
    report = json.loads((tmp_path / "install_check.json").read_text(encoding="utf-8"))
    by_package = {row["package"]: row for row in report["results"]}
    assert by_package["must-have"]["status"] == "FAIL"
    assert "점검 응답 없음" in by_package["must-have"]["note"]
    assert report["summary"]["required"]["problems"] == ["must-have"]


def test_all_steps_always_writes_dependency_report_after_earlier_failures(
        generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    module.DEPENDENCY_CHECKS = [(("must",), "must-have", None, "required", "server")]
    monkeypatch.setattr(module, "extract", lambda: 7)
    monkeypatch.setattr(module, "install_deps", lambda: 8)
    monkeypatch.setattr(module, "build_frontend", lambda: 9)
    monkeypatch.setattr(module, "_dist_intact", lambda _fe: True)
    monkeypatch.setattr(module, "_probe_dependency_rows",
                        lambda timeout=300: ([_ok_row("must-have")], ""))
    assert module.all_steps() == 7
    report = json.loads((tmp_path / "install_check.json").read_text(encoding="utf-8"))
    assert report["required_failures"] == []


def _make_frontend(module, tmp_path, *, intact=True):
    fe = tmp_path / "frontend"
    (fe / "src").mkdir(parents=True)
    (fe / "src" / "app.js").write_text("export default 1", encoding="utf-8")
    (fe / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (fe / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
    (fe / "dist" / "assets").mkdir(parents=True)
    (fe / "dist" / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>', encoding="utf-8")
    if intact:
        (fe / "dist" / "assets" / "app.js").write_text("built", encoding="utf-8")
    return fe


def _dist_hashes(fe):
    import hashlib

    return {p.relative_to(fe / "dist").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (fe / "dist").rglob("*") if p.is_file()}


def test_strict_reuses_verified_matching_dist_without_npm(generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    fe = _make_frontend(module, tmp_path)
    module.FRONTEND_STAMP = {"verified": True, "src_sha": module._frontend_src_fingerprint(),
                             "built_at": "test", "dist_files": 2, "dist_sha256": _dist_hashes(fe)}
    monkeypatch.setenv("FLOW_SETUP_STRICT", "1")
    monkeypatch.setattr(module, "_run", lambda *_a, **_k: pytest.fail("npm must be skipped"))
    assert module.build_frontend() == 0


@pytest.mark.parametrize("intact", [True, False])
def test_stale_or_broken_dist_is_not_reported_as_success(generated_setup, tmp_path, monkeypatch, intact):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    _make_frontend(module, tmp_path, intact=intact)
    module.FRONTEND_STAMP = {"verified": True, "src_sha": "stale", "built_at": "test", "dist_files": 2}
    monkeypatch.setattr(module, "_has", lambda _name: False)
    assert module.build_frontend() == 1


def test_frontend_uses_npm_ci_when_lockfile_exists(generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    _make_frontend(module, tmp_path)
    module.FRONTEND_STAMP = {"verified": False, "src_sha": "stale"}
    calls = []
    monkeypatch.setattr(module, "_has", lambda _name: True)
    monkeypatch.setattr(module, "_run", lambda cmd, cwd, **kwargs: calls.append((cmd, cwd, kwargs)) or 0)
    assert module.build_frontend() == 0
    assert calls[0][0] == "npm ci"
    assert calls[0][2]["timeout"] == 600


def test_lockfile_alone_changes_frontend_fingerprint(generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    fe = _make_frontend(module, tmp_path)
    before = module._frontend_src_fingerprint()
    (fe / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{"x":{}}}', encoding="utf-8")
    assert module._frontend_src_fingerprint() != before


def test_pip_bootstrap_uses_argv_for_python_path_with_spaces(generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(module.sys, "executable", r"C:\Program Files\Miniforge\python.exe")
    monkeypatch.setattr(module.subprocess, "run",
                        lambda argv, **kwargs: calls.append((argv, kwargs)) or type("P", (), {"returncode": 0})())
    module._ensure_pip_ready()
    assert calls[0][0] == [r"C:\Program Files\Miniforge\python.exe", "-m", "pip", "--version"]


@pytest.mark.parametrize("change", ["missing", "modified"])
def test_prebuilt_reuse_checks_lazy_page_chunks(generated_setup, tmp_path, monkeypatch, change):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    fe = _make_frontend(module, tmp_path)
    lazy = fe / "dist" / "assets" / "page.js"
    lazy.write_text("original", encoding="utf-8")
    module.FRONTEND_STAMP = {"verified": True, "src_sha": module._frontend_src_fingerprint(),
                             "dist_sha256": _dist_hashes(fe)}
    if change == "missing":
        lazy.unlink()
    else:
        lazy.write_text("different", encoding="utf-8")
    monkeypatch.setattr(module, "_has", lambda _name: False)
    assert module.build_frontend() == 1


def test_operator_requirements_are_preserved_and_missing_packages_are_filled(generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    req = tmp_path / "requirements.txt"
    req.write_text("operator-package==1.0\n", encoding="utf-8")
    module.DEPENDENCY_CHECKS = [
        (("pydantic",), "pydantic", "2.0", "required", "model_dump"),
        (("fastapi",), "fastapi", None, "required", "server"),
        (("yaml",), "pyyaml", None, "feature", "YAML"),
    ]
    monkeypatch.setattr(module, "_probe_dependency_rows", lambda timeout=300: (
        [_ok_row("pydantic", "1.10.0"), _ok_row("fastapi", "0.135.0")], ""))
    calls = []
    monkeypatch.setattr(module, "_pip_install", lambda pkgs, **kwargs: calls.append(pkgs) or 0)
    assert module.install_deps() == 0
    assert calls == [["-r", str(req)], ["pydantic>=2.0"], ["pyyaml"]]
    assert req.read_text(encoding="utf-8") == "operator-package==1.0\n"


def test_all_reports_required_missing_after_install_exception(generated_setup, tmp_path, monkeypatch, capsys):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    module.DEPENDENCY_CHECKS = [(("must",), "must-have", None, "required", "server")]
    monkeypatch.setattr(module, "extract", lambda: 0)
    def fail():
        raise OSError("synthetic install failure")
    monkeypatch.setattr(module, "install_deps", fail)
    monkeypatch.setattr(module, "build_frontend", lambda: 3)
    monkeypatch.setattr(module, "_dist_intact", lambda _fe: False)
    monkeypatch.setattr(module, "_probe_dependency_rows", lambda timeout=300: (
        [{"dist": "must-have", "error": "ModuleNotFoundError", "installed": ""}], ""))
    assert module.all_steps() == 1
    report = json.loads((tmp_path / "install_check.json").read_text(encoding="utf-8"))
    assert report["required_failures"] == ["must-have"]
    assert report["installation"]["stages"] == {"extract": 0, "python_dependencies": 1, "frontend": 3}
    output = capsys.readouterr()
    assert "설치되지 않음" in output.out
    assert "[done]" not in output.out


@pytest.mark.parametrize("problem,version,note", [
    ("ImportError: DLL load failed", "2.0", "import 실패"),
    ("", "1.10.0", "최소 2.0 미만"),
])
def test_version_and_import_failures_are_printed(generated_setup, tmp_path, monkeypatch, capsys, problem, version, note):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    module.DEPENDENCY_CHECKS = [(("pydantic",), "pydantic", "2.0", "required", "model_dump")]
    row = _ok_row("pydantic", version)
    row["error"] = problem
    monkeypatch.setattr(module, "_probe_dependency_rows", lambda timeout=300: ([row], ""))
    assert module.check_deps() == 1
    output = capsys.readouterr().out
    assert "pydantic" in output and note in output


def test_npm_install_failure_does_not_run_build_or_report_stale_success(generated_setup, tmp_path, monkeypatch):
    module = _load_generated(generated_setup, tmp_path, monkeypatch)
    _make_frontend(module, tmp_path)
    monkeypatch.setattr(module, "_has", lambda _name: True)
    calls = []
    monkeypatch.setattr(module, "_run", lambda cmd, **kwargs: calls.append(cmd) or 7)
    assert module.build_frontend() == 7
    assert calls == ["npm ci"]
