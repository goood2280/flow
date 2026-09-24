from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core import domain_knowledge as knowledge
from core import llm_adapter
from routers import domain_knowledge as api


TITLE = "공정설계 기본지식"
BODY = "## 제품\n제품과 공정의 관계를 기록한다."
GUIDELINES = "기존 사실과 예외를 보존하고 전체 수정 본문만 반환한다."


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "domain-knowledge.sqlite3"
    monkeypatch.setattr(knowledge, "_path", lambda: path)
    monkeypatch.setattr(knowledge, "references", lambda: {"sources": [], "warnings": []})
    return path


def _save(*, body=BODY, base_version=0, actor="admin", title=TITLE, guidelines=GUIDELINES):
    return knowledge.save_document(
        title=title,
        body=body,
        editing_guidelines=guidelines,
        base_version=base_version,
        actor=actor,
    )


def test_saved_versions_and_history_are_immutable(isolated_store):
    first = _save(actor="first-admin")
    second = _save(body="## 측정\n두 번째 본문", base_version=1, actor="second-admin")

    assert first["version"] == 1
    assert second["version"] == 2
    assert knowledge.read_document() == second
    assert knowledge.read_document(1) == first
    assert knowledge.history() == [
        {key: second[key] for key in ("version", "title", "updated_at", "updated_by")},
        {key: first[key] for key in ("version", "title", "updated_at", "updated_by")},
    ]
    with pytest.raises(KeyError):
        knowledge.read_document(999)


def test_stale_save_is_rejected(isolated_store):
    _save()
    _save(body="## 최신\n새 본문", base_version=1)

    with pytest.raises(knowledge.Conflict):
        _save(body="## 오래된 수정\n덮어쓰면 안 된다.", base_version=1)

    assert knowledge.read_document()["body"] == "## 최신\n새 본문"
    assert [item["version"] for item in knowledge.history()] == [2, 1]


def test_two_simultaneous_writers_cannot_both_commit(isolated_store):
    _save()
    barrier = Barrier(2)

    def write(index):
        barrier.wait(timeout=5)
        try:
            return _save(body=f"## 작성자 {index}\n동시 수정", base_version=1, actor=f"admin-{index}")
        except knowledge.Conflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, (1, 2)))

    committed = [item for item in outcomes if isinstance(item, dict)]
    conflicts = [item for item in outcomes if isinstance(item, knowledge.Conflict)]
    assert len(committed) == len(conflicts) == 1
    assert committed[0]["version"] == 2
    assert knowledge.read_document() == committed[0]
    assert [item["version"] for item in knowledge.history()] == [2, 1]


@pytest.mark.parametrize(
    "changes",
    [
        {"title": "   "},
        {"body": ""},
        {"guidelines": "\n"},
        {"title": "제" * 161},
        {"body": "본" * (knowledge.MAX_BODY + 1)},
        {"guidelines": "지" * (knowledge.MAX_GUIDELINES + 1)},
    ],
)
def test_save_rejects_invalid_document_fields_without_writing(isolated_store, changes):
    kwargs = {"title": TITLE, "body": BODY, "guidelines": GUIDELINES, **changes}
    with pytest.raises(ValueError):
        _save(**kwargs)
    assert not isolated_store.exists()
    assert knowledge.history() == []


@pytest.mark.parametrize("instruction", ["", "   ", "요" * 20001], ids=["empty", "blank", "too-long"])
def test_preview_rejects_invalid_instruction_before_llm(isolated_store, monkeypatch, instruction):
    _save()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", lambda *_args, **_kwargs: pytest.fail("invalid input reached LLM"))

    with pytest.raises(ValueError):
        knowledge.preview(
            title=TITLE,
            body=BODY,
            editing_guidelines=GUIDELINES,
            base_version=1,
            instruction=instruction,
        )
    assert [item["version"] for item in knowledge.history()] == [1]


def test_ai_preview_includes_editing_guidelines_and_never_writes(isolated_store, monkeypatch):
    original = _save()
    captured = {}
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def complete(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return {"ok": True, "text": "```markdown\n## 제품\nAI 수정안\n```"}

    monkeypatch.setattr(llm_adapter, "complete", complete)
    draft = knowledge.preview(
        title=TITLE,
        body=BODY,
        editing_guidelines=GUIDELINES,
        base_version=1,
        instruction="측정 정의를 추가해 줘",
    )

    assert draft == {"title": TITLE, "body": "## 제품\nAI 수정안", "base_version": 1}
    assert GUIDELINES in captured["prompt"]
    assert "측정 정의를 추가해 줘" in captured["prompt"]
    assert "editing_guidelines" in captured["system"]
    assert knowledge.read_document() == original
    assert [item["version"] for item in knowledge.history()] == [1]


def test_ai_failure_and_invalid_draft_do_not_write(isolated_store, monkeypatch):
    original = _save()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": False, "error": "offline"})
    kwargs = dict(title=TITLE, body=BODY, editing_guidelines=GUIDELINES, base_version=1, instruction="수정")

    with pytest.raises(knowledge.Unavailable):
        knowledge.preview(**kwargs)
    monkeypatch.setattr(llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": True, "text": "제목 없는 답"})
    with pytest.raises(ValueError):
        knowledge.preview(**kwargs)

    assert knowledge.read_document() == original
    assert [item["version"] for item in knowledge.history()] == [1]


def test_preview_detects_document_changed_during_llm_call(isolated_store, monkeypatch):
    _save()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def complete(*_args, **_kwargs):
        _save(body="## 다른 관리자\n먼저 저장한 변경", base_version=1, actor="other-admin")
        return {"ok": True, "text": "## 초안\n늦게 도착한 수정안"}

    monkeypatch.setattr(llm_adapter, "complete", complete)
    with pytest.raises(knowledge.Conflict):
        knowledge.preview(
            title=TITLE,
            body=BODY,
            editing_guidelines=GUIDELINES,
            base_version=1,
            instruction="수정",
        )

    assert knowledge.read_document()["updated_by"] == "other-admin"
    assert [item["version"] for item in knowledge.history()] == [2, 1]


def test_default_editing_guidelines_cover_safe_wiki_editing():
    guidelines = knowledge.DEFAULT_GUIDELINES
    assert "[업무 규칙]" in guidelines
    assert "[일반 지식]" in guidelines
    assert "[확인 필요]" in guidelines
    assert "기존의 중요한 예외와 출처를 보존" in guidelines
    assert "전체 수정 본문만 Markdown으로 반환" in guidelines


def test_prompt_context_uses_only_relevant_saved_sections_within_budget(isolated_store):
    relevant = "## ET 측정\nET 두께 측정의 좌표와 예외를 기록한다."
    unrelated = "## 제품 식별자\n제품 이름과 LOT 식별자를 설명한다."
    body = unrelated + "\n" + relevant + "\n## 공정 조건\n온도 조건을 설명한다."
    secret_instruction = "절대로 모델 프롬프트에 넣지 않을 편집 지침"
    _save(body=body, guidelines=secret_instruction)

    context = knowledge.prompt_context("ET 측정 좌표", max_chars=len(relevant) + 1)

    assert context["body"] == relevant
    assert len(context["body"]) <= len(relevant) + 1
    assert context["partial"] is True
    assert "제품 식별자" not in context["body"]
    assert secret_instruction not in str(context)
    assert set(context) == {"title", "version", "source", "body", "partial"}


def test_domain_knowledge_endpoints_require_admin(isolated_store):
    app = FastAPI()

    @app.middleware("http")
    async def bind_test_user(request: Request, call_next):
        request.state.user = {"username": "tester", "role": request.headers.get("x-test-role", "user")}
        return await call_next(request)

    app.include_router(api.router)
    document = {
        "title": TITLE,
        "body": BODY,
        "editing_guidelines": GUIDELINES,
        "base_version": 0,
    }
    with TestClient(app) as client:
        requests = [
            client.get("/api/admin/domain-knowledge"),
            client.put("/api/admin/domain-knowledge", json=document),
            client.post("/api/admin/domain-knowledge/preview", json={**document, "instruction": "수정"}),
            client.get("/api/admin/domain-knowledge/history"),
            client.get("/api/admin/domain-knowledge/history/1"),
        ]
        assert [response.status_code for response in requests] == [403, 403, 403, 403, 403]
        assert client.get("/api/admin/domain-knowledge", headers={"x-test-role": "admin"}).status_code == 200

    assert not isolated_store.exists()


def test_llm_policy_allows_admin_preview_path_but_denies_non_admin():
    path = "/api/admin/domain-knowledge/preview"
    with llm_adapter.request_execution_scope({"username": "admin", "role": "admin"}, path):
        assert llm_adapter._execution_denial() == ""
    with llm_adapter.request_execution_scope({"username": "engineer", "role": "user"}, path):
        assert "admin-only" in llm_adapter._execution_denial()


def test_section_preview_preserves_other_sections(isolated_store, monkeypatch):
    body = "## First\nKeep this exactly.\n\n## Second\nOld.\n\n## Third\nKeep this too."
    _save(body=body)
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", lambda *a, **kw: {"ok": True, "text": "## Second\nUpdated."})
    result = knowledge.preview(title=TITLE, body=body, editing_guidelines=GUIDELINES,
                               base_version=1, instruction="update", section_heading="## Second")
    assert result["body"] == body.replace("Old.", "Updated.")
    assert knowledge.read_document()["body"] == body


def test_truncated_ai_result_is_not_accepted(isolated_store, monkeypatch):
    _save()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", lambda *a, **kw: {
        "ok": True, "text": "## Partial\nUnfinished", "raw": {"choices": [{"finish_reason": "length"}]}})
    with pytest.raises(ValueError, match="길이 제한"):
        knowledge.preview(title=TITLE, body=BODY, editing_guidelines=GUIDELINES, base_version=1, instruction="update")
    assert knowledge.read_document()["body"] == BODY


def test_schema_and_mapping_reference_reads_actual_files(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(knowledge, "PATHS", SimpleNamespace(db_root=tmp_path))
    from core import flowi_db_reference
    monkeypatch.setattr(flowi_db_reference, "PATHS", SimpleNamespace(db_root=tmp_path))
    (tmp_path / "step_matching.csv").write_text("product,step_id,function_step\nDEMO,AB100000,TEST\n", encoding="utf-8")
    observed = knowledge.references()
    sample = next(s for s in observed["sources"] if s["kind"] == "mapping_sample")
    assert sample["columns"] == ["product", "step_id", "function_step"]
    assert sample["examples"] == [{"product": "DEMO", "step_id": "AB100000", "function_step": "TEST"}]


def test_image_attachment_is_validated_and_admin_only(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from types import SimpleNamespace
    monkeypatch.setattr(api, "PATHS", SimpleNamespace(data_root=tmp_path))
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_admin] = lambda: {"username": "admin", "role": "admin"}
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    with TestClient(app) as client:
        bad = client.post("/api/admin/domain-knowledge/assets", files={"file": ("fake.png", b"<script>x</script>", "image/png")})
        assert bad.status_code == 400
        response = client.post("/api/admin/domain-knowledge/assets", files={"file": ("structure.png", output.getvalue(), "image/png")})
        assert response.status_code == 200
        url = response.json()["url"]
        assert client.get(url).headers["content-type"] == "image/png"
        app.dependency_overrides.clear()
        assert client.get(url).status_code == 401


def test_data_chat_llm_receives_saved_domain_context(isolated_store, monkeypatch):
    import json
    from types import SimpleNamespace
    from core import data_chat, product_semantics, ai_semantic, flowi_db_reference
    _save()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(data_chat, "available_product_catalog", lambda: [])
    monkeypatch.setattr(product_semantics, "prompt_context", lambda *a: {})
    monkeypatch.setattr(ai_semantic, "prompt_context", lambda *a: {})
    monkeypatch.setattr(flowi_db_reference, "load_reference_context", lambda: "")
    captured = {}
    def complete(prompt, **kwargs):
        captured.update(json.loads(prompt))
        return {"ok": True, "obj": {"action": "clarify", "params": {}}}
    monkeypatch.setattr(llm_adapter, "complete_json", complete)
    data_chat._feature_plan("unknown conceptual request", {}, [], SimpleNamespace(ACTIONS={}, plan=lambda *a: None))
    assert captured["domain_knowledge"]["body"] == BODY
    assert GUIDELINES not in str(captured["domain_knowledge"])
