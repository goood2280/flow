import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core import domain_knowledge_seed as seed
from core import domain_knowledge as knowledge


def _doc(title="Seed title", body="## Seed\nA private seed.", guidelines="Keep facts and exceptions."):
    return {"title": title, "body": body, "editing_guidelines": guidelines}


def _rows(path: Path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT version, document FROM revisions ORDER BY version").fetchall()


def _make_db(path: Path, documents):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE revisions (version INTEGER PRIMARY KEY, document TEXT NOT NULL)")
        db.executemany("INSERT INTO revisions(version, document) VALUES (?, ?)", documents)


def test_clean_install_writes_seed_file_and_revision_one(tmp_path):
    app_root = tmp_path / "app"
    data_root = tmp_path / "runtime"

    assert seed.install_seed(app_root, _doc(), data_root) is True

    seed_file = app_root / "data" / "install-seeds" / "domain_knowledge.json"
    db_file = data_root / "knowledge" / "domain_knowledge.sqlite3"
    assert json.loads(seed_file.read_text(encoding="utf-8")) == _doc()
    rows = _rows(db_file)
    assert len(rows) == 1
    assert rows[0][0] == 1
    installed = json.loads(rows[0][1])
    assert installed["title"] == _doc()["title"]
    assert installed["body"] == _doc()["body"]
    assert installed["editing_guidelines"] == _doc()["editing_guidelines"]
    assert installed["version"] == 1
    assert installed["updated_by"] == "installer"
    assert installed["updated_at"]


def test_reinstall_preserves_existing_edits_and_all_revisions(tmp_path):
    db_file = tmp_path / "knowledge.sqlite3"
    existing = {"title": "Existing", "body": "## Existing", "editing_guidelines": "Existing rules", "version": 1,
                "updated_at": "when", "updated_by": "admin"}
    later = {**existing, "version": 2, "body": "## Later"}
    _make_db(db_file, [(1, json.dumps(existing)), (2, json.dumps(later))])

    assert seed.seed_database(db_file, _doc()) is False
    assert [(version, json.loads(document)) for version, document in _rows(db_file)] == [(1, existing), (2, later)]


def test_reinstall_preserves_initial_seed_file_and_existing_database(tmp_path):
    app_root = tmp_path / "app"
    data_root = tmp_path / "runtime"
    first = _doc("First")
    second = _doc("Second")

    assert seed.install_seed(app_root, first, data_root) is True
    before = _rows(data_root / "knowledge" / "domain_knowledge.sqlite3")
    assert seed.install_seed(app_root, second, data_root) is False

    seed_file = app_root / "data" / "install-seeds" / "domain_knowledge.json"
    assert json.loads(seed_file.read_text(encoding="utf-8")) == first
    assert _rows(data_root / "knowledge" / "domain_knowledge.sqlite3") == before


def test_concurrent_seeds_insert_only_one_revision(tmp_path):
    db_file = tmp_path / "knowledge.sqlite3"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: seed.seed_database(db_file, _doc()), range(2)))

    assert sorted(outcomes) == [False, True]
    rows = _rows(db_file)
    assert len(rows) == 1
    assert rows[0][0] == 1


def test_runtime_reads_install_seed_when_data_root_changes(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    seed.install_seed(app_root, _doc(), tmp_path / "old-runtime")
    new_db = tmp_path / "new-runtime" / "knowledge.sqlite3"
    monkeypatch.setattr(knowledge, "_path", lambda: new_db)
    monkeypatch.setattr(knowledge, "_seed_path", lambda: app_root / seed.SEED_RELATIVE_PATH)

    assert knowledge.read_document()["body"] == _doc()["body"]
    assert knowledge.history()[0]["version"] == 1
    changed = knowledge.save_document(**_doc(body="## Updated\nKept by operator"), base_version=1, actor="admin")
    assert knowledge.read_document() == changed
    assert len(knowledge.history()) == 2


def test_generated_installer_seeds_only_private_build(tmp_path, monkeypatch):
    import importlib.util
    import base64
    import gzip
    import shutil

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_flow_builder", root / "_build_setup.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "ensure_frontend_build", lambda: {})
    monkeypatch.setattr(builder, "gather_files", lambda: [])
    monkeypatch.setenv("FLOW_DATA_ROOT", str(tmp_path / "runtime"))
    payload = base64.b64encode(gzip.compress(json.dumps(_doc()).encode())).decode()

    for name, encoded in (("public", ""), ("private", payload)):
        app = tmp_path / name
        (app / "backend/core").mkdir(parents=True)
        for filename in ("domain_knowledge_seed.py", "root_profile.py"):
            shutil.copy2(root / "backend/core" / filename, app / "backend/core" / filename)
        script = builder.build(encoded)
        namespace = {"__name__": "test_installer", "__file__": str(app / "setup.py")}
        exec(compile(script, str(app / "setup.py"), "exec"), namespace)
        namespace["_seed_domain_knowledge"]()
        assert (app / seed.SEED_RELATIVE_PATH).exists() == bool(encoded)
    assert seed.export_document(tmp_path / "runtime/knowledge/domain_knowledge.sqlite3") == _doc()


def test_private_build_rejects_public_repository_output(monkeypatch):
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_flow_builder_guard", root / "_build_setup.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "build", lambda *args: pytest.fail("unsafe build started"))
    with pytest.raises(SystemExit) as exc:
        builder.main(["--include-domain-knowledge", "--output", str(root / "setup.py")])
    assert exc.value.code == 2


def test_invalid_generated_installer_preserves_previous_file(tmp_path, monkeypatch):
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("test_flow_builder_atomic", root / "_build_setup.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    destination = tmp_path / "setup.py"
    destination.write_text("# previous working installer\n", encoding="utf-8")
    monkeypatch.setattr(builder, "build", lambda *args: "    invalid indentation\n")
    with pytest.raises(IndentationError):
        builder.main(["--output", str(destination)])
    assert destination.read_text(encoding="utf-8") == "# previous working installer\n"
    assert list(tmp_path.iterdir()) == [destination]


def test_export_reads_latest_revision_and_returns_only_seed_fields(tmp_path):
    db_file = tmp_path / "knowledge.sqlite3"
    first = {**_doc("First"), "version": 1, "updated_at": "old", "updated_by": "one"}
    latest = {**_doc("Latest"), "version": 2, "updated_at": "new", "updated_by": "two", "extra": "ignored"}
    _make_db(db_file, [(1, json.dumps(first)), (2, json.dumps(latest))])

    exported = seed.export_document(db_file)
    assert exported == _doc("Latest")


def test_export_missing_or_empty_db_is_read_only(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        seed.export_document(missing)
    assert not missing.exists()

    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    with pytest.raises(sqlite3.OperationalError):
        seed.export_document(empty)
    assert empty.read_bytes() == b""

    no_revisions = tmp_path / "no-revisions.sqlite3"
    _make_db(no_revisions, [])
    with pytest.raises(ValueError):
        seed.export_document(no_revisions)


@pytest.mark.parametrize("bad", [
    {},
    {"title": "", "body": "body", "editing_guidelines": "rules"},
    {"title": "title", "body": "", "editing_guidelines": "rules"},
    {"title": "title", "body": "body", "editing_guidelines": ""},
    {"title": "x" * 161, "body": "body", "editing_guidelines": "rules"},
    {"title": "title", "body": "x" * 60001, "editing_guidelines": "rules"},
    {"title": "title", "body": "body", "editing_guidelines": "x" * 12001},
])
def test_invalid_seed_fails_without_creating_destination(tmp_path, bad):
    destination = tmp_path / "app" / "data" / "install-seeds" / "domain_knowledge.json"
    with pytest.raises((TypeError, ValueError)):
        seed.validate_seed(bad)
    with pytest.raises((TypeError, ValueError)):
        seed.install_seed(tmp_path / "app", bad, tmp_path / "runtime")
    assert not destination.exists()
    assert not (tmp_path / "runtime").exists()
