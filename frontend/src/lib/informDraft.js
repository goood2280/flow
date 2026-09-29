// SplitTable → Inform 위저드 초안 전달과 자동 저장.
//
// 초안에는 SplitTable 스냅샷(embed)이 통째로 들어간다. 운영 규모 표(수천 항목 × 25 wafer)는
// JSON 이 10MB 를 넘어 브라우저 localStorage 한도(오리진당 약 5MB)를 넘는다. 예전에는
// setItem 예외를 조용히 삼키고 Inform 으로 이동해, 위저드가 빈 초안이나 이전 초안으로 열렸다
// (로컬 소량 데이터에서는 재현되지 않고 운영에서만 나던 "스냅샷 이동 오류").
//
// 그래서 같은 탭 안의 전달은 메모리로 하고, localStorage 에는 들어가는 만큼만 저장한다.
// 스냅샷이 너무 크면 스냅샷만 빼고 저장해 새로고침 때도 나머지 입력은 살린다.

export const INFORM_WIZARD_DRAFT_KEY = "flow_inform_wizard_draft_v1";
export const INFORM_WIZARD_OPEN_KEY = "flow_inform_open_wizard_v1";

// 자동 저장 한도. 입력마다 JSON 직렬화가 돌기 때문에 한도 밖 큰 스냅샷은 아예 저장하지 않는다.
const STORE_MAX_CHARS = 1_500_000;

let pendingDraft = null;

function stripEmbed(draft) {
  const form = draft?.form && typeof draft.form === "object" ? draft.form : {};
  if (!form.embed) return draft;
  return { ...draft, form: { ...form, embed: null }, embedOmitted: true };
}

// 스냅샷 크기는 객체마다 한 번만 잰다 — 위저드 자동 저장이 입력마다 불리므로
// 10MB 스냅샷을 매번 직렬화하면 타이핑이 끊긴다.
const embedChars = new WeakMap();

function embedSize(embed) {
  if (!embed || typeof embed !== "object") return 0;
  if (!embedChars.has(embed)) {
    let n = Infinity;
    try { n = JSON.stringify(embed).length; } catch (_) { /* 직렬화 불가 = 저장 제외 */ }
    embedChars.set(embed, n);
  }
  return embedChars.get(embed);
}

// 초안을 저장한다. 저장 결과: "full" | "without_embed" | "failed".
export function saveInformDraft(draft) {
  let text = "";
  try {
    text = embedSize(draft?.form?.embed) > STORE_MAX_CHARS ? "" : JSON.stringify(draft);
  } catch (_) {
    return "failed";
  }
  if (text && text.length <= STORE_MAX_CHARS) {
    try {
      localStorage.setItem(INFORM_WIZARD_DRAFT_KEY, text);
      return "full";
    } catch (_) { /* 한도 초과 — 스냅샷을 빼고 다시 시도 */ }
  }
  try {
    localStorage.setItem(INFORM_WIZARD_DRAFT_KEY, JSON.stringify(stripEmbed(draft)));
    return "without_embed";
  } catch (_) {
    try { localStorage.removeItem(INFORM_WIZARD_DRAFT_KEY); } catch (__) { /* noop */ }
    return "failed";
  }
}

// SplitTable 이 Inform 위저드를 열 때 쓴다. 메모리 전달이 정본이다.
export function handOffInformDraft(draft) {
  pendingDraft = draft;
  const stored = saveInformDraft(draft);
  try { localStorage.setItem(INFORM_WIZARD_OPEN_KEY, "1"); } catch (_) { /* noop */ }
  return stored;
}

// 위저드가 열릴 때 한 번 읽는다. 메모리 전달본이 있으면 그것, 없으면 저장본.
export function takeInformDraft() {
  if (pendingDraft) {
    const draft = pendingDraft;
    pendingDraft = null;
    return draft;
  }
  try {
    const raw = localStorage.getItem(INFORM_WIZARD_DRAFT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

export function clearInformDraft() {
  pendingDraft = null;
  try { localStorage.removeItem(INFORM_WIZARD_DRAFT_KEY); } catch (_) { /* noop */ }
}
