import { useState } from "react";

function text(value) {
  if (value == null) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function asTextList(value, limit = 20) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => text(item).trim()).filter(Boolean).slice(0, limit);
}

/**
 * 응답에서 Human-in-the-loop 선택지를 정규화한다.
 * ask_user(ReAct) · clarification · teg/split 후보 · 승인대기 · missing 순으로
 * 섹션 배열 [{ key, title, options: [{label, value}], allowOther, hint }] 반환.
 * options 의 value 는 그대로 submit 하면 되는 프롬프트 텍스트다.
 */
export function hitlSections(response) {
  if (!response || typeof response !== "object") return [];
  const sections = [];
  const tool = response.tool && typeof response.tool === "object" ? response.tool : {};

  const askUser = response.ask_user && typeof response.ask_user === "object" ? response.ask_user : null;
  if (askUser && text(askUser.question).trim()) {
    const choices = Array.isArray(askUser.choices) ? askUser.choices : [];
    sections.push({
      key: "ask_user",
      title: text(askUser.question).trim(),
      options: choices.map((choice) => ({ label: text(choice).trim(), value: text(choice).trim() }))
        .filter((option) => option.label),
      allowOther: true,
      otherPlaceholder: "직접 답변 입력",
    });
  }

  const clarification = tool.clarification && typeof tool.clarification === "object" ? tool.clarification : null;
  if (clarification && clarification.kind && Array.isArray(clarification.options)) {
    const options = clarification.options
      .map((option) => ({
        label: text(option?.label ?? option?.value).trim(),
        value: text(option?.value ?? option?.label).trim(),
      }))
      .filter((option) => option.label && option.value)
      .slice(0, 100);
    if (options.length || clarification.allow_other !== false) {
      sections.push({
        key: `clarification:${clarification.kind}`,
        title: text(clarification.title).trim() || "값을 선택하세요",
        options,
        allowOther: clarification.allow_other !== false,
        otherPlaceholder: text(clarification.placeholder).trim() || "직접 입력",
      });
    }
  } else if (Array.isArray(tool.missing) && tool.missing.some((item) => text(item).toLowerCase() === "product")) {
    const rows = Array.isArray(tool.table?.rows) ? tool.table.rows : [];
    const values = [...new Set(rows.map((row) => {
      if (typeof row === "string" || typeof row === "number") return text(row).trim();
      if (row && typeof row === "object" && !Array.isArray(row)) {
        const key = Object.keys(row).find((name) => /^(product|product_name|product_id|name|value)$/i.test(name));
        return key ? text(row[key]).trim() : "";
      }
      return "";
    }).filter(Boolean))].slice(0, 5).map((value) => ({ label: value, value }));
    if (values.length) {
      sections.push({
        key: "missing:product",
        title: "제품을 선택하세요",
        options: values,
        allowOther: true,
        otherPlaceholder: "제품명 직접 입력",
      });
    }
  }

  const pushCandidateGroups = (groups, prefix, fallbackTitle) => {
    (Array.isArray(groups) ? groups : []).forEach((group, groupIndex) => {
      const candidates = Array.isArray(group?.candidates) ? group.candidates : [];
      if (!candidates.length) return;
      sections.push({
        key: `${prefix}:${groupIndex}`,
        title: text(group.title).trim()
          || (group.requested ? `“${text(group.requested).trim()}” 후보를 선택하세요` : fallbackTitle),
        options: candidates.map((candidate) => {
          const label = text(candidate?.label ?? candidate?.value ?? candidate).trim();
          const value = text(candidate?.prompt ?? candidate?.value ?? candidate).trim();
          return { label, value };
        }).filter((option) => option.label && option.value),
        allowOther: false,
      });
    });
  };
  pushCandidateGroups(tool.split_candidates, "split", "Split 조건을 선택하세요");
  pushCandidateGroups(tool.teg_candidates, "teg", "TEG 후보를 선택하세요");

  if (Array.isArray(tool.missing) && tool.missing.includes("semantic_target")) {
    const rows = Array.isArray(tool.table?.rows) ? tool.table.rows : [];
    const options = rows.map((row, index) => ({
      label: [row.module, row.term || row.path, row.step_id, row.item_id].filter(Boolean).join(" · ") || `후보 ${index + 1}`,
      value: String(row["번호"] || index + 1),
    }));
    if (options.length) sections.push({
      key: "semantic_target", title: "용어에 연결할 대상을 선택하세요", options, allowOther: false,
    });
  }

  const approvalId = text(tool.approval?.id).trim();
  if (tool.approval?.status === "pending" && approvalId) {
    sections.push({
      key: "approval",
      title: "변경 승인",
      options: [
        { label: "승인하고 반영", value: `승인 ${approvalId}` },
        { label: "취소", value: `취소 ${approvalId}` },
      ],
      allowOther: false,
    });
  }

  const missing = Array.isArray(tool.missing) ? tool.missing.map((item) => text(item).trim()).filter(Boolean) : [];
  const coveredMissing = sections.some((section) => section.key === "missing:product");
  if ((tool.needs_input || missing.length) && !coveredMissing && sections.length === 0 && missing.length > 0) {
    sections.push({
      key: "missing",
      title: `추가 입력 필요: ${missing.join(" · ")}`,
      options: [],
      allowOther: false,
      hint: "아래 입력창에 보충해서 다시 질문하세요.",
    });
  }
  return sections;
}

function ChoiceSection({ section, disabled, onSubmit }) {
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherValue, setOtherValue] = useState("");
  const submitOther = (event) => {
    event.preventDefault();
    const next = otherValue.trim();
    if (!next || disabled) return;
    onSubmit(next);
    setOtherValue("");
    setOtherOpen(false);
  };
  return (
    <div className="home-interp__choice" aria-label={section.title}>
      <div className="home-interp__choice-title">{section.title}</div>
      {section.options.length > 0 && (
        <div className="home-data-chat__actions is-choice-list">
          {section.options.map((option) => (
            <button type="button" key={option.value} disabled={disabled} onClick={() => onSubmit(option.value)}>
              {option.label}
            </button>
          ))}
        </div>
      )}
      {section.allowOther && (
        !otherOpen ? (
          <button
            type="button"
            className="home-interp__other-toggle"
            disabled={disabled}
            onClick={() => setOtherOpen(true)}
          >
            기타 직접 입력
          </button>
        ) : (
          <form className="home-interp__other-form" onSubmit={submitOther}>
            <input
              value={otherValue}
              onChange={(event) => setOtherValue(event.target.value)}
              placeholder={section.otherPlaceholder || "직접 입력"}
              disabled={disabled}
              aria-label={section.otherPlaceholder || "직접 입력"}
            />
            <button type="submit" disabled={disabled || !otherValue.trim()}>확인</button>
          </form>
        )
      )}
      {section.hint && <div className="home-interp__hint">{section.hint}</div>}
    </div>
  );
}

export function HitlCard({ response, disabled = false, onSubmit }) {
  const sections = hitlSections(response);
  if (!sections.length) return null;
  return (
    <div className="home-interp__hitl" aria-label="선택 필요">
      <div className="home-interp__hitl-header">확인이 필요합니다</div>
      {sections.map((section) => (
        <ChoiceSection key={section.key} section={section} disabled={disabled} onSubmit={onSubmit} />
      ))}
    </div>
  );
}

function SemanticBlock({ semantic }) {
  if (!semantic || typeof semantic !== "object") return null;
  const resolved = asTextList(semantic.resolved_columns);
  const aliases = asTextList(semantic.alias_hits);
  const unknown = asTextList(semantic.unknown_terms);
  const values = asTextList(semantic.value_terms);
  const intents = asTextList(semantic.intent_matches);
  const slots = semantic.slot_hints && typeof semantic.slot_hints === "object" ? semantic.slot_hints : {};
  const slotEntries = Object.entries(slots)
    .map(([key, value]) => ({ key: text(key), value: Array.isArray(value) ? value.map(text).join(", ") : text(value) }))
    .filter((entry) => entry.key && entry.value)
    .slice(0, 12);
  const catalog = Array.isArray(semantic.value_catalog_matches) ? semantic.value_catalog_matches : [];
  const hasAny = resolved.length || aliases.length || unknown.length || values.length
    || intents.length || slotEntries.length || catalog.length;
  if (!hasAny) return null;
  return (
    <div className="home-interp__section" aria-label="질문 해석">
      <div className="home-interp__section-title">질문 해석 세부사항</div>
      {resolved.length > 0 && <div className="home-interp__row"><b>해석된 컬럼</b><span>{resolved.join(" · ")}</span></div>}
      {aliases.length > 0 && <div className="home-interp__row"><b>용어 매칭</b><span>{aliases.join(" · ")}</span></div>}
      {slotEntries.length > 0 && (
        <div className="home-interp__row"><b>슬롯</b><span>{slotEntries.map((entry) => `${entry.key}=${entry.value}`).join(" · ")}</span></div>
      )}
      {values.length > 0 && <div className="home-interp__row"><b>값 용어</b><span>{values.join(" · ")}</span></div>}
      {catalog.length > 0 && (
        <div className="home-interp__row"><b>값 후보</b><span>{catalog.slice(0, 6).map((item) => text(item.column) && text(item.value) ? `${text(item.column)}=${text(item.value)}` : text(item.value)).filter(Boolean).join(" · ")}</span></div>
      )}
      {intents.length > 0 && <div className="home-interp__row"><b>의도</b><span>{intents.join(" · ")}</span></div>}
      {unknown.length > 0 && <div className="home-interp__row is-warn"><b>미해석 용어</b><span>{unknown.join(" · ")}</span></div>}
    </div>
  );
}

function SourcesBlock({ steps }) {
  const targets = {};
  const sources = [];
  (Array.isArray(steps) ? steps : []).forEach((step) => {
    const stepTargets = step?.targets && typeof step.targets === "object" ? step.targets : {};
    ["product", "root_lot_id", "lot_id", "file"].forEach((key) => {
      const value = text(stepTargets[key]).trim();
      if (value && !targets[key]) targets[key] = value;
    });
    asTextList(step?.sources, 12).forEach((source) => {
      if (!sources.includes(source)) sources.push(source);
    });
  });
  const targetBits = [
    targets.product ? `제품 ${targets.product}` : "",
    targets.root_lot_id ? `Root ${targets.root_lot_id}` : "",
    !targets.root_lot_id && targets.lot_id ? `Lot ${targets.lot_id}` : "",
    targets.file ? `파일 ${targets.file}` : "",
  ].filter(Boolean);
  if (!targetBits.length && !sources.length) return null;
  return (
    <div className="home-interp__section" aria-label="참고 DB">
      <div className="home-interp__section-title">참고 데이터</div>
      {targetBits.length > 0 && <div className="home-interp__row"><b>대상</b><span>{targetBits.join(" · ")}</span></div>}
      {sources.length > 0 && (
        <div className="home-interp__sources">
          {sources.map((source) => <span key={source} className="home-data-chat__source-pill">{source}</span>)}
        </div>
      )}
    </div>
  );
}

function QueryBlock({ steps }) {
  const withQuery = (Array.isArray(steps) ? steps : []).filter((step) => text(step?.query?.sql).trim());
  if (!withQuery.length) return null;
  return (
    <div className="home-interp__section" aria-label="실행 쿼리">
      <div className="home-interp__section-title">실행 쿼리</div>
      {withQuery.map((step, index) => (
        <details key={`${step.tool || "step"}-${index}`} className="home-interp__query">
          <summary>{text(step.title).trim() || text(step.tool).trim() || `쿼리 ${index + 1}`}</summary>
          <pre><code>{text(step.query.sql).trim()}</code></pre>
          {Array.isArray(step.query.selected_columns) && step.query.selected_columns.length > 0 && (
            <div className="home-interp__row"><b>컬럼</b><span>{step.query.selected_columns.join(", ")}</span></div>
          )}
        </details>
      ))}
    </div>
  );
}

function StepsBlock({ steps }) {
  const list = (Array.isArray(steps) ? steps : []).filter((step) => step && typeof step === "object");
  if (!list.length) return null;
  return (
    <div className="home-interp__section" aria-label="실행 경로">
      <div className="home-interp__section-title">실행 경로</div>
      <ol className="home-interp__steps">
        {list.map((step, index) => (
          <li key={`${step.tool || "step"}-${index}`}>
            <b>{text(step.title).trim() || text(step.tool).trim() || `단계 ${index + 1}`}</b>
            {text(step.reason).trim() && <span className="home-interp__step-reason">{text(step.reason).trim()}</span>}
            <span className={`home-interp__step-status is-${text(step.status).trim() || (step.ok ? "success" : "failed")}`}>
              {text(step.status).trim() || (step.ok ? "성공" : "실패")}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function UsageLine({ usage, budgetExhausted }) {
  if (!usage || typeof usage !== "object") return null;
  return (
    <div className="home-interp__usage" aria-label="호출 차감">
      이번 요청 LLM {usage.llm_calls_used ?? "—"}회 차감 / 최대 {usage.llm_call_limit ?? "—"}회
      {typeof usage.minute_calls_remaining !== "undefined" && (
        <> · 분당 잔여 {usage.minute_calls_remaining}회</>
      )}
      {budgetExhausted && <span className="home-interp__budget-warn"> · 이번 요청 상한 도달 — 나머지는 다음 질문으로 나눠주세요</span>}
    </div>
  );
}

function InterpretationSummary({ interpretation }) {
  if (!interpretation || typeof interpretation !== "object") return null;
  const summary = text(interpretation.summary).trim();
  const origin = text(interpretation.origin).trim() || "실행 계획 요약";
  const status = ({ completed: "처리 완료", needs_input: "조건 확인 필요", failed: "처리 미완료" })[interpretation.status] || text(interpretation.status).trim();
  const details = Array.isArray(interpretation.details)
    ? interpretation.details.filter((item) => item && typeof item === "object" && (text(item.label).trim() || text(item.value).trim())).slice(0, 30)
    : [];
  const unresolved = asTextList(interpretation.unresolved, 30);
  if (!summary && !details.length && !unresolved.length && !status) return null;
  return (
    <section className="home-interp__summary" aria-labelledby="home-interp-summary-title">
      <h2 id="home-interp-summary-title">이렇게 이해했어요</h2>
      {summary && <p className="home-interp__summary-text">{summary}</p>}
      <details className="home-interp__explainability">
        <summary>해석 근거와 상태</summary>
        <div className="home-interp__explainability-body">
          <div className="home-interp__row"><b>출처</b><span>{origin}</span></div>
          <p>확정된 대상과 실행 조건을 설명합니다.</p>
          {status && <div className="home-interp__row"><b>상태</b><span>{status}</span></div>}
        </div>
      {details.length > 0 && (
        <dl className="home-interp__details">
          {details.map((item, index) => (
            <div key={`${text(item.label)}-${index}`}>
              <dt>{text(item.label).trim() || "항목"}</dt>
              <dd>{text(item.value).trim()}</dd>
            </div>
          ))}
        </dl>
      )}
      {unresolved.length > 0 && (
        <div className="home-interp__unresolved" aria-label="확인되지 않은 조건">
          <div className="home-interp__subheading">확인되지 않은 조건</div>
          <ul>{unresolved.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      )}
      </details>
    </section>
  );
}

function RawEvidence({ response, semantic, steps, disabled, onSubmit }) {
  const raw = response.raw && typeof response.raw === "object" ? response.raw
    : response.evidence?.raw && typeof response.evidence.raw === "object" ? response.evidence.raw : {};
  const route = text(raw.route || response.route).trim();
  const api = raw.api || response.api || response.evidence?.api;
  const hasRaw = route || api || steps.length || Object.keys(semantic).length;
  if (!hasRaw) return null;
  return (
    <details className="home-interp__raw">
      <summary>실행 근거 보기 (API · SQL · 경로)</summary>
      <div className="home-interp__raw-body">
        {route && <div className="home-interp__row"><b>경로</b><code>{route}</code></div>}
        {api && <div className="home-interp__row"><b>API</b><code>{typeof api === "string" ? api : JSON.stringify(api)}</code></div>}
        <SemanticBlock semantic={semantic} />
        <SourcesBlock steps={steps} />
        <QueryBlock steps={steps} />
        <StepsBlock steps={steps} />
      </div>
    </details>
  );
}

export default function InterpretationPanel({ response, disabled = false, onSubmit }) {
  if (!response || typeof response !== "object") {
    return (
      <div className="home-interp" aria-label="질문 해석">
        <div className="home-interp__empty">질문을 입력하면 여기에 해석·참고 DB·쿼리·선택지가 표시됩니다.</div>
      </div>
    );
  }
  const evidence = response.evidence && typeof response.evidence === "object" ? response.evidence : {};
  const semantic = (evidence.semantic && typeof evidence.semantic === "object" && Object.keys(evidence.semantic).length)
    ? evidence.semantic
    : (response.meta?.semantic_summary && typeof response.meta.semantic_summary === "object" ? response.meta.semantic_summary : {});
  const steps = Array.isArray(evidence.steps) ? evidence.steps : [];
  const planner = text(response.meta?.planner).trim();
  return (
    <div className="home-interp" aria-label="질문 해석">
      <InterpretationSummary interpretation={response.interpretation} />
      {!response.interpretation && (
        <div className="home-interp__head">
          <span className="home-interp__badge">질문 해석</span>
          {planner && <span className="home-interp__planner">{planner}</span>}
        </div>
      )}
      <HitlCard response={response} disabled={disabled} onSubmit={onSubmit} />
      <RawEvidence response={response} semantic={semantic} steps={steps} disabled={disabled} onSubmit={onSubmit} />
      <UsageLine usage={response.usage} budgetExhausted={Boolean(response.meta?.budget_exhausted)} />
    </div>
  );
}
