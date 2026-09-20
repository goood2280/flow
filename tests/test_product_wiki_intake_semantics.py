import json
import sqlite3
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_product_state(monkeypatch, tmp_path):
    from core import product_semantics as sem
    from core import product_wiki as wiki

    db_root = tmp_path / "db"
    data_root = tmp_path / "data"
    db_root.mkdir()
    data_root.mkdir()
    paths = SimpleNamespace(db_root=db_root, data_root=data_root)
    monkeypatch.setattr(wiki, "PATHS", paths)
    monkeypatch.setattr(sem, "PATHS", paths)
    return wiki, sem, paths


def _seed_product(wiki, product="PRODA", revision=0, document=None):
    with wiki.database() as db:
        db.execute(
            "INSERT OR REPLACE INTO products(key,name,revision,wiki_document,wiki_toc) VALUES(?,?,?,?,?)",
            (product.casefold(), product, revision, document, "[]"),
        )


def test_intake_route_keeps_title_and_rich_original_and_passes_semantic_reference(
    isolated_product_state, monkeypatch
):
    wiki, sem, _ = isolated_product_state
    from routers import product_wiki as router

    _seed_product(wiki)
    captured = {}
    reference = {
        "product_aliases": [{"product": "PRODA", "aliases": ["Alpha Device"]}],
        "measurements": [{
            "source_type": "INLINE", "module": "FEOL", "step_id": "S200",
            "item_id": "CD_GATE", "item_desc": "Gate CD", "aliases": ["Gate CD1"],
        }],
    }

    monkeypatch.setattr(sem, "intake_reference", lambda product, text: reference)
    monkeypatch.setattr(sem, "propose", lambda product, text, actor, entry_id, source_title="": captured.update(
        {"product": product, "text": text, "actor": actor, "entry_id": entry_id, "source_title": source_title}
    ) or {"id": "proposal-1", "status": "pending"})
    monkeypatch.setattr("core.product_wiki_structure.intake_context", lambda product, text: {"product": product, "structure": []})

    def fake_complete(prompt, **kwargs):
        captured["prompt"] = prompt
        return {
            "ok": True,
            "text": json.dumps({
                "title": "Gate CD issue",
                "kind": "issue",
                "body": "Gate CD review remains open.",
                "status": "open",
                "lot_ids": [],
                "evidence": "",
            }),
        }

    monkeypatch.setattr("core.llm_adapter.complete", fake_complete)
    result = router.intake(
        router.IntakeRequest(
            product="PRODA",
            expected_revision=0,
            title="현업 제목",
            text='<p data-kind="rich">Gate CD <strong>review</strong></p>',
        ),
        user={"username": "alice"},
    )

    entry = result["entries"][0]
    assert entry["title"] == "현업 제목"
    assert entry["source_title"] == "현업 제목"
    assert entry["source_text"] == '<p data-kind="rich">Gate CD <strong>review</strong></p>'
    assert entry["body"] == "Gate CD review remains open."
    assert captured["source_title"] == "현업 제목"
    assert captured["text"] == '<p data-kind="rich">Gate CD <strong>review</strong></p>'
    assert "Alpha Device" in captured["prompt"]
    assert "S200" in captured["prompt"]
    assert "CD_GATE" in captured["prompt"]


def test_compile_ai_failure_does_not_hold_wiki_write_lock(isolated_product_state, monkeypatch):
    wiki, _, _ = isolated_product_state
    from core import llm_adapter

    _seed_product(wiki)
    entry = {
        "id": "entry-1", "title": "Original", "body": "Original body",
        "source_text": "Original source", "kind": "issue", "status": "open",
        "author": "alice", "created_at": wiki.now(), "updated_at": wiki.now(),
        "updated_by": "alice", "deleted": False,
    }
    with wiki.database() as db:
        db.execute("INSERT INTO entries VALUES(?,?,?)", ("proda", entry["id"], json.dumps(entry)))

    def complete_writes_other_product(*args, **kwargs):
        with wiki.database() as db:
            db.execute("INSERT OR REPLACE INTO products(key,name,revision) VALUES(?,?,0)", ("other", "OTHER"))
        raise RuntimeError("simulated AI failure")

    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", complete_writes_other_product)
    saved = wiki.save_entry(
        "PRODA", 0,
        {"title": "New original", "body": "Committed source", "source_text": "Committed source", "kind": "issue", "status": "open"},
        "alice",
    )

    assert any(row["title"] == "New original" for row in saved["entries"])
    with wiki.database() as db:
        products = [dict(row) for row in db.execute("SELECT name FROM products")]
    assert any(row["name"] == "OTHER" for row in products)
    assert any(row["source_text"] == "Committed source" for row in wiki.document("PRODA")["entries"])


def test_compile_cas_rejects_renderer_overwrite_after_newer_entry(isolated_product_state, monkeypatch):
    wiki, _, _ = isolated_product_state
    from core import llm_adapter

    _seed_product(wiki)
    with wiki.database() as db:
        db.execute("INSERT INTO entries VALUES(?,?,?)", ("proda", "old", json.dumps({"id": "old", "title": "Old", "body": "Old", "source_text": "Old", "author": "a", "status": "open", "deleted": False})))

    def renderer_saves_newer_entry(*args, **kwargs):
        with wiki.database() as db:
            newer = {"id": "new", "title": "New", "body": "New", "source_text": "New", "author": "b", "status": "open", "deleted": False}
            db.execute("UPDATE products SET revision=revision+1 WHERE key='proda'")
            db.execute("INSERT INTO entries VALUES(?,?,?)", ("proda", "new", json.dumps(newer)))
        return {"ok": True, "text": "## Renderer result"}

    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", renderer_saves_newer_entry)
    with pytest.raises(wiki.Conflict):
        wiki.compile_product_wiki("PRODA", actor="compiler", use_ai=True)
    assert wiki.document("PRODA")["wiki_document"] != "## Renderer result"
    assert any(row["id"] == "new" for row in wiki.document("PRODA")["entries"])


def test_fallback_is_fact_only_without_synthetic_ids_or_normality(isolated_product_state):
    wiki, _, _ = isolated_product_state
    markdown, _ = wiki.fallback_compile_wiki(
        "PRODA",
        [{"id": "e1", "title": "Observed", "body": "Gate CD review is pending", "source_text": "Gate CD review is pending", "status": "open"}],
        structure_rows=[],
    )
    assert "ST1000" not in markdown
    assert "LOT-FA101" not in markdown
    assert "정상" not in markdown
