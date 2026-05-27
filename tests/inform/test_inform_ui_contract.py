from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MY_INFORM = ROOT / "frontend" / "src" / "pages" / "My_Inform.jsx"
SPLIT_SNAPSHOT_VIEW = ROOT / "frontend" / "src" / "components" / "SplitTableSnapshotView.jsx"


def test_inform_wizard_five_step_backend_contract_order():
    src = MY_INFORM.read_text(encoding="utf-8")

    for token in [
        'export const WIZARD_STEPS = ["lot", "module", "splittable", "mail_preview", "review"]',
        '"/api/informs/config"',
        '"/api/splittable/lot-candidates"',
        "mergeLotCandidateOptions",
        "&col=lot_id",
        "prefix=${encodeURIComponent(rawLot)}",
        "lotCandidateRootScope(rawLot)",
        "root_lot_id=${encodeURIComponent(rootScope)}",
        "LOT_CANDIDATE_LIMIT = 20000",
        '"/api/informs/splittable-snapshot"',
        '"/api/informs/recipients"',
        '"/api/informs/mail-groups"',
        'postJson(API + "/bulk-create"',
        "fab_lot_id_at_save = targetLot",
        "buildEmbedForLot",
        "lot_id: targetLot",
        "custom_cols: customCols",
        'const shouldAttachKnobSnapshot = wizardAttachMode === "knob" && embedCustomCols.length > 0;',
        'const shouldAttachSetSnapshot = wizardAttachMode === "sets" && form.attach_embed && attachedSetsForSubmit().length > 0;',
        'if (wizardAttachMode === "knob")',
        'if (wizardAttachMode !== "sets") return [];',
        "if (!shouldAttachKnobSnapshot && !shouldAttachSetSnapshot) return null;",
        "LOT_ID 검색 (입력 즉시 필터)",
        "체크된 LOT_ID",
        'const lotIdFilterText = String(fabSearch || "").trim().toLowerCase();',
        'const fabLotOptions = (lotOptions || []).filter(o => o.type !== "root");',
        "const needle = rawLot.toLowerCase();",
        'filter(o => !String(o.value || "").trim().toLowerCase().includes(needle))',
        'return { ...f, lot_id: nextFabs[0] || "", fab_lot_ids: nextFabs };',
        "여러 LOT_ID 중 가장 위에 선택된 LOT_ID만 미리보기로 표시합니다.",
        'gridTemplateRows: "auto 150px"',
        "height: 150",
        '"POST /api/informs/bulk-create"',
    ]:
        assert token in src
    assert ".slice(0, 500)" not in src
    assert "fabSearch || form.lot_id" not in src
    assert "&col=root_lot_id" not in src

    ordered = [
        '"/api/informs/config"',
        '"/api/splittable/lot-candidates"',
        '"/api/informs/splittable-snapshot"',
        '"/api/informs/recipients"',
        '"/api/informs/mail-groups"',
        '"POST /api/informs/bulk-create"',
    ]
    positions = [src.index(token) for token in ordered]
    assert positions == sorted(positions)


def test_inform_wizard_mail_note_is_plain_top_block():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert 'fontSize: "12px"' in src
    assert 'fontSize: "12pt"' not in src
    assert 'background: "#fffbeb"' not in src
    assert 'borderLeft: "4px solid #f59e0b"' not in src


def test_mail_preview_byte_formatter_is_available_to_panel():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert "function formatBytes(n)" in src
    assert src.index("function formatBytes(n)") < src.index("function MailDialogPreviewPanel")
    assert "const formatBytes = (n) =>" not in src


def test_inform_splittable_embed_matches_split_table_header_and_plan_contract():
    src = MY_INFORM.read_text(encoding="utf-8")
    view = SPLIT_SNAPSHOT_VIEW.read_text(encoding="utf-8")

    for token in [
        'import SplitTableSnapshotView from "../components/SplitTableSnapshotView"',
        "return <SplitTableSnapshotView embed={embed} product={product} footer={renderAttachedSets()} />;",
        "display_mode: form.split_check_display ? \"split_check\" : \"matrix\"",
        "Split 체크 표시",
    ]:
        assert token in src

    for token in [
        "const firstColWidth = 288",
        "const dataColWidth = 115",
        'const rootRowLabel = rowLabels.root_lot_id || "root_lot_id"',
        'const lotRowLabel = rowLabels.lot_id || "lot_id"',
        'const paramRowLabel = rowLabels.parameter || "항목"',
        "const hasRootRow = hasLotContext",
        "const hasLotRow = hasLotContext || headerGroups.length > 0",
        'String(r._display || r._param || "").replace(/^[A-Z]+_/, "")',
        "const splitCheckMode = String(st.display_mode || embed?.display_mode || embed?.st_scope?.display_mode || \"\") === \"split_check\"",
        "const rawPrefixColumns = Array.isArray(st.prefix_columns)",
        "const isPlanOnly = !splitCheckMode && hasPlan && !hasActual",
        "const isMismatch = !splitCheckMode && hasPlan && hasActual && String(cell.plan) !== String(cell.actual)",
        "const isAppliedPlan = !splitCheckMode && hasPlan && hasActual && String(cell.plan) === String(cell.actual)",
        '" (plan 적용)"',
    ]:
        assert token in view

    assert 'root_lot_id</span> {rootLotId || "-"}' not in src
    assert 'lot_id</span> {lotIdLabel || "-"}' not in src
    assert "Wafer별 적용 plan 요약" not in src


def test_split_check_snapshot_renderer_merges_param_cell_without_step_refs():
    view = SPLIT_SNAPSHOT_VIEW.read_text(encoding="utf-8")

    for token in [
        'export const SPLIT_CHECK_PREFIX_COLUMNS = ["항목", "값", "Split"]',
        "export function buildSplitCheckStView",
        "rowSpan: span",
        "sf(`/api/splittable/knob-meta${metaQs}`)",
        "sf(`/api/splittable/vm-meta${metaQs}`)",
        "sf(`/api/splittable/inline-meta${metaQs}`)",
    ]:
        assert token in view

    assert "renderSplitParamCell" not in view
    assert "splitParamRefs" not in view
    assert "[ {ref.step_id}" not in view
    assert "복수 step_id 이므로 적용 전 담당 엔지니어가 실제 사용 step_id를 확인해 주세요." not in view


def test_inform_wizard_splittable_preview_states_are_visible():
    src = MY_INFORM.read_text(encoding="utf-8")

    for token in [
        "setSetSnapshotState",
        "SplitTable LOT snapshot 생성 중...",
        "SplitTable 스냅샷 생성 실패:",
        "데이터 없음: 선택 LOT/컬럼에 표시할 값이 없습니다.",
        "세트 목록 미리보기와 실제 LOT snapshot 미리보기는 아래에서 구분됩니다",
        "원본 세트 목록",
        "실제 LOT snapshot 미리보기",
        "미리보기 생성",
        "embedSnapshotSummary(form.embed)",
        "hasLotSnapshotData(form.embed)",
    ]:
        assert token in src

    assert ".catch(() => { setEmbedFetching(false); })" not in src


def test_inform_detail_tabs_are_body_and_mail_history_with_comment_button():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert '["body", "본문"]' in src
    assert '["mail", "메일 이력"]' in src
    assert '["comments", "댓글"]' not in src
    assert '["history", "이력"]' not in src
    assert '["attachments", "첨부"]' not in src
    assert "openReInformWizard" in src
    assert "재인폼 {commentCount}" in src


def test_inform_detail_edit_creates_reinform_instead_of_body_edit():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert "원문을 덮어쓰지 않고 재인폼을 작성합니다" not in src
    assert "✎ 수정" not in src
    assert ">수정<" not in src
    assert "ReInformComposer" not in src
    assert "const openReInformWizard = (root) =>" in src
    assert 'setWizardMode("reinform")' in src
    assert "setReInformParent(root)" in src
    assert "setWizardStep(2)" in src
    assert 'mode={wizardMode}' in src
    assert 'parentInform={reInformParent}' in src
    assert "재인폼 작성" in src
    assert "인폼 본문 수정" not in src
    assert 'onEdit(root.id, { text: next })' not in src
    assert "withRePrefix(form.text)" in src
    assert "parent_id: isReInform ? reInformParent.id : null" in src
    assert "return postJson(API, payload)" in src
    assert "const childReInforms" not in src
    assert "재인폼이 없습니다." not in src
    assert "↳ [RE]" in src
    assert "onReInform={openReInformWizard}" in src
    assert "onClick={() => onReInform?.(root)}" in src
    assert "onClick={() => onReInform?.(node)}" in src
    assert "재인폼 {commentCount}" in src
    assert "reInformTextForDisplay(node)" in src
    assert 'title="재인폼 작성"' in src
    assert 'canEdit={canEditDelete}' not in src
    assert "onRemoveSet={removeAttachedSet}" not in src


def test_inform_recent_list_renders_reinform_children_as_tree_rows():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert 'sf(API + "/recent?limit=500&include_children=true")' in src
    assert "const [listChildrenByParent, setListChildrenByParent] = useState({});" in src
    assert "setListChildrenByParent(d.children_by_parent || {});" in src
    assert "childrenByParent={listChildrenByParent}" in src
    assert "function InformVirtualList({ roots, childrenByParent = {}, selectedId, onOpen })" in src
    assert "const renderRows = (node, root, depth = 0) =>" in src
    assert "kids.map(child => renderRows(child, root, depth + 1))" in src
    assert "onOpen={() => onOpen(root)}" in src
    assert "StatusBadge status={isChild ? \"reinform\" : status} compact" in src
    assert "↳ [RE]" in src


def test_inform_recent_list_columns_status_and_row_times_are_contract():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert "minWidth: 1060" in src
    assert '<col style={{ width: 176 }} />' in src
    assert '<th style={headStyle}>카운트</th>' not in src
    assert "maxWidth: compact ? 114" not in src
    assert 'maxWidth: "100%"' in src
    assert "const timeValue = row.created_at || \"\";" in src
    assert 'const timeLabel = isChild ? "재인폼" : "등록";' in src
    assert "row.thread_updated_at || row.created_at" not in src
    assert "const mailCount = (row.mail_history || []).length;" not in src
    assert "const replyCount = Number(row.reply_count || row.comment_count || 0);" not in src
    assert "💬{replyCount || 0} · ✉{mailCount || 0} · 📎{attachCount || 0}" not in src
    assert "kids.map(child => renderRows(child, root, depth + 1))" in src
    assert "↳ [RE]" in src


def test_inform_pagegear_reason_subject_templates_are_saved_and_used():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert "ReasonTemplatesPanel" in src
    assert 'postJson(API + "/config", { reason_templates: draft || {} })' in src
    assert "defaultInformMailSubject(form, lotLabel, constants.reason_templates || {})" in src
    assert "defaultInformMailSubject(form, mailLotLabel, reasonTemplates || {})" in src
    assert "{product}" in src
    assert "{lot}" in src
    assert "{module}" in src
    assert "{reason}" in src
