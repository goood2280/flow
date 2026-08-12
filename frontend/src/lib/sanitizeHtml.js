// lib/sanitizeHtml.js — dangerouslySetInnerHTML 앞에 반드시 통과시키는 allowlist 살균기.
//
// 왜 필요한가:
//   인폼 메일 본문, 회의/트래커 이슈 설명(description_html)은 **다른 사용자가 쓴
//   HTML** 이다. 그대로 innerHTML 로 넣으면 저장형 XSS 가 된다 — 이슈 설명에
//   <img src=x onerror="fetch('/api/admin/users',...)"> 한 줄을 넣어두면, 그 이슈를
//   여는 관리자의 세션으로 관리자 API 가 호출된다. 피해자가 링크를 클릭할 필요도
//   없고, 저장돼 있으니 보는 사람마다 반복 발동한다.
//
// 왜 DOMPurify 를 안 쓰는가:
//   사내 서버는 npm registry 가 막혀 있고 frontend/package-lock.json 도 없다.
//   새 런타임 의존성을 넣으면 반입 절차가 하나 더 늘어난다. 이 파일은 의존성 0.
//
// 정책 (allowlist — 모르는 것은 막는다):
//   - DANGEROUS 태그: 서브트리째 제거 (script/iframe/svg/form/...)
//   - ALLOWED 태그: 유지하되 속성 검사
//   - 그 외 태그: **언랩** (자식은 살리고 태그만 벗김) — 내용 손실 방지
//   - on* 이벤트 핸들러 속성 전량 제거
//   - href/src 는 http(s)/mailto/tel/상대경로/raster data URI 만 허용
//     (data:image/svg+xml 은 SVG 안에 스크립트를 넣을 수 있어 제외)
//   - id/name 은 제거 (DOM clobbering 방지)
//   - style 은 유지하되 expression()/javascript:/@import/behavior/-moz-binding 제거
//     (메일 미리보기가 인라인 style 로만 표를 그리므로 통째로 지우면 화면이 깨진다)
//   - 주석 노드 제거
//
// 주의: DOMParser 로 만든 문서는 inert 라 파싱 단계에서 스크립트 실행도, 이미지
// 로드도 일어나지 않는다. 살균 후 문자열만 꺼내 쓴다.

const DANGEROUS_TAGS = new Set([
  "script", "iframe", "frame", "frameset", "object", "embed", "applet",
  "link", "meta", "base", "noscript", "template", "portal",
  "form", "input", "button", "select", "option", "textarea", "label",
  "svg", "math", "audio", "video", "source", "track", "canvas",
]);

const ALLOWED_TAGS = new Set([
  "a", "abbr", "b", "big", "blockquote", "br", "caption", "center", "code",
  "col", "colgroup", "dd", "del", "details", "div", "dl", "dt", "em", "figcaption",
  "figure", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins",
  "li", "mark", "ol", "p", "pre", "s", "small", "span", "strike", "strong",
  "style", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th",
  "thead", "tr", "u", "ul", "wbr",
]);

// id/name 은 의도적으로 빠져 있다 (DOM clobbering). srcset/formaction 도 제외.
const ALLOWED_ATTRS = new Set([
  "align", "alt", "bgcolor", "border", "cellpadding", "cellspacing", "class",
  "colspan", "dir", "height", "href", "lang", "rel", "rowspan", "span", "src",
  "start", "style", "target", "title", "valign", "width",
]);

// 상대경로(/api/tracker/image?...), 앵커, 쿼리, http(s), mailto, tel 만 허용.
const SAFE_URL = /^(?:https?:\/\/|mailto:|tel:|#|\?|\/(?!\/)|\.{1,2}\/|[^:/?#]*(?:[/?#]|$))/i;
// SVG 는 스크립트를 품을 수 있어 raster 만 허용한다.
const SAFE_DATA_IMG = /^data:image\/(?:png|jpe?g|gif|webp|bmp|x-icon);base64,[a-z0-9+/=\s]+$/i;
const CSS_DANGER = /(expression\s*\(|javascript\s*:|vbscript\s*:|behaviou?r\s*:|-moz-binding|@import|url\s*\(\s*['"]?\s*(?:javascript|vbscript|data:text))/gi;

function isSafeUrl(value, { allowDataImage = false } = {}) {
  const raw = String(value == null ? "" : value);
  // 제어문자/공백을 끼워 넣는 "java&#9;script:" 류 우회가 있어 판정 전에 털어낸다.
  // (\x00-\x20 제어·공백, \x7f DEL, 유니코드 공백/zero-width/BOM 을 전부 제거)
  const url = raw.replace(/[\x00-\x20\x7f\u00a0\u1680\u2000-\u200f\u2028\u2029\u202f\u205f\u3000\ufeff]/g, "");
  if (!url) return false;
  if (allowDataImage && SAFE_DATA_IMG.test(url)) return true;
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) {
    // 스킴이 있으면 허용 목록에 든 것만.
    return /^(?:https?:|mailto:|tel:)/i.test(url);
  }
  return SAFE_URL.test(url);
}

// 위험 토큰만 지우면 `width:expression(alert(1))` 이 `width:alert(1))` 같은
// 쓰레기 선언으로 남는다. 무해하긴 해도, 걸린 선언은 통째로 버리는 게 맞다.
function sanitizeStyle(value) {
  const raw = String(value == null ? "" : value);
  if (!raw) return "";
  return raw
    .split(";")
    .filter((decl) => {
      CSS_DANGER.lastIndex = 0;      // /g 정규식은 lastIndex 가 남는다
      return decl.trim() && !CSS_DANGER.test(decl);
    })
    .map((decl) => decl.trim())
    .join("; ");
}

function sanitizeCssText(value) {
  return String(value == null ? "" : value).replace(CSS_DANGER, "").replace(/<\/?\s*style/gi, "");
}

function escapeText(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function scrubElement(el) {
  const tag = el.tagName.toLowerCase();

  if (DANGEROUS_TAGS.has(tag) && tag !== "style") {
    el.remove();
    return;
  }

  if (!ALLOWED_TAGS.has(tag)) {
    // 모르는 태그는 벗기되 자식은 살린다 (html/head/body/section/... 포함).
    const parent = el.parentNode;
    if (!parent) { el.remove(); return; }
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    el.remove();
    return;
  }

  if (tag === "style") {
    el.textContent = sanitizeCssText(el.textContent);
    return;
  }

  for (const attr of Array.from(el.attributes)) {
    const name = attr.name.toLowerCase();
    const value = attr.value;

    if (name.startsWith("on") || name === "srcdoc" || name.startsWith("xlink:") || name.startsWith("xml:")) {
      el.removeAttribute(attr.name);
      continue;
    }
    if (!ALLOWED_ATTRS.has(name)) {
      el.removeAttribute(attr.name);
      continue;
    }
    if (name === "style") {
      el.setAttribute("style", sanitizeStyle(value));
      continue;
    }
    if (name === "href" || name === "src") {
      if (!isSafeUrl(value, { allowDataImage: name === "src" && tag === "img" })) {
        el.removeAttribute(attr.name);
      }
      continue;
    }
    if (name === "target") {
      // target=_blank 는 opener 탈취 경로라 rel 을 강제한다.
      el.setAttribute("rel", "noopener noreferrer");
    }
  }

  if (tag === "a" && el.getAttribute("target") && !el.getAttribute("rel")) {
    el.setAttribute("rel", "noopener noreferrer");
  }
}

/**
 * 신뢰할 수 없는 HTML 을 allowlist 로 살균한다.
 * @param {string} html
 * @returns {string} innerHTML 에 넣어도 되는 HTML
 */
export function sanitizeHtml(html) {
  const input = html == null ? "" : String(html);
  if (!input) return "";
  if (typeof DOMParser === "undefined") {
    // 브라우저가 아닌 환경(테스트 등) — 살균 불가하면 전부 텍스트로 떨어뜨린다.
    return escapeText(input);
  }

  let doc;
  try {
    doc = new DOMParser().parseFromString(input, "text/html");
  } catch (_) {
    return escapeText(input);
  }
  if (!doc || !doc.body) return escapeText(input);

  // 앞머리에 온 <style>/<meta>/<link> 는 파서가 <head> 로 보낸다. body 만
  // 직렬화하면 그게 통째로 사라져서, 메일 템플릿이 <style> 블록을 쓰기 시작하는
  // 순간 표 서식이 조용히 날아간다. head 내용을 body 앞으로 옮겨 놓고 아래
  // 살균 패스에 똑같이 태운다 — 위험한 태그(script/link/meta/base)는 거기서 걸린다.
  try {
    const head = doc.head;
    if (head && head.firstChild) {
      const first = doc.body.firstChild;
      while (head.firstChild) doc.body.insertBefore(head.firstChild, first);
    }
  } catch (_) { /* head 가 없으면 그대로 진행 */ }

  // 주석 노드 제거 (IE 조건부 주석 우회 차단).
  try {
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_COMMENT);
    const comments = [];
    while (walker.nextNode()) comments.push(walker.currentNode);
    comments.forEach((c) => c.remove());
  } catch (_) { /* TreeWalker 없으면 건너뛴다 */ }

  // 언랩이 자식을 부모로 끌어올리므로 살아있는 목록을 매번 다시 훑는다.
  // 무한루프 방지용 상한을 둔다 (중첩 깊이가 비정상적으로 깊은 입력 방어).
  for (let pass = 0; pass < 20; pass += 1) {
    const elements = Array.from(doc.body.querySelectorAll("*"));
    let dirty = false;
    for (const el of elements) {
      if (!el.isConnected) continue;
      const tag = el.tagName.toLowerCase();
      if (DANGEROUS_TAGS.has(tag) || !ALLOWED_TAGS.has(tag)) dirty = true;
      scrubElement(el);
    }
    if (!dirty) break;
  }

  return doc.body.innerHTML;
}

/**
 * dangerouslySetInnerHTML 에 바로 넘길 수 있는 형태.
 *   <div {...safeHtml(issue.description_html)} />
 */
export function safeHtml(html) {
  return { __html: sanitizeHtml(html) };
}

/**
 * HTML 에서 순수 텍스트만 뽑는다.
 *
 * ⚠️ `document.createElement("div").innerHTML = html` 로 하면 안 된다.
 *    문서에 붙이지 않은(detached) 요소라도 **이미지는 로드되고 onerror 가
 *    실행된다** — 실제 브라우저에서 확인했다. 즉 "텍스트만 뽑으려던" 코드가
 *    그대로 XSS 실행 지점이 된다. DOMParser 로 만든 문서는 inert 라
 *    스크립트도 이미지 로드도 일어나지 않고, textContent 결과는 동일하다.
 */
export function htmlToText(html) {
  const input = html == null ? "" : String(html);
  if (!input) return "";
  if (typeof DOMParser === "undefined") return input.replace(/<[^>]*>/g, "");
  try {
    const doc = new DOMParser().parseFromString(input, "text/html");
    return (doc.body?.textContent || "").trim();
  } catch (_) {
    return input.replace(/<[^>]*>/g, "");
  }
}

export default sanitizeHtml;
