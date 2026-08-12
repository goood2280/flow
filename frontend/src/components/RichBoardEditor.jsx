import { useCallback, useEffect, useRef, useState } from "react";

import { authSrc, sf } from "../lib/api";
import { sanitizeHtml } from "../lib/sanitizeHtml";


function rawImageUrl(value) {
  const src = String(value || "");
  return src.replace(/([?&])t=[^&#]*/g, "").replace(/[?&]$/, "");
}

function decorateEditorImages(root) {
  if (!root) return;
  root.querySelectorAll("img").forEach((img) => {
    const raw = rawImageUrl(img.dataset.flowSrc || img.getAttribute("src") || "");
    if (!raw) return;
    img.dataset.flowSrc = raw;
    img.setAttribute("src", authSrc(raw));
  });
}

function serializedHtml(root) {
  if (!root) return "";
  const clone = root.cloneNode(true);
  clone.querySelectorAll("img").forEach((img) => {
    const raw = rawImageUrl(img.dataset.flowSrc || img.getAttribute("src") || "");
    if (raw) img.setAttribute("src", raw);
    img.removeAttribute("data-flow-src");
  });
  clone.querySelectorAll("[data-upload-placeholder]").forEach((node) => node.remove());
  return sanitizeHtml(clone.innerHTML).trim();
}

function insertFragment(range, fragment) {
  range.deleteContents();
  const last = fragment.lastChild;
  range.insertNode(fragment);
  if (last) {
    range.setStartAfter(last);
    range.collapse(true);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }
}

function rangeInsideEditor(root) {
  if (!root) return null;
  const selection = window.getSelection();
  if (selection?.rangeCount) {
    const selected = selection.getRangeAt(0);
    const container = selected.commonAncestorContainer;
    if (container === root || root.contains(container.nodeType === Node.TEXT_NODE ? container.parentNode : container)) {
      return selected.cloneRange();
    }
  }
  const fallback = document.createRange();
  fallback.selectNodeContents(root);
  fallback.collapse(false);
  return fallback;
}

function imageExtension(type) {
  const mime = String(type || "image/png").toLowerCase();
  if (mime === "image/jpeg" || mime === "image/jpg" || mime === "image/pjpeg") return "jpg";
  if (mime === "image/x-png") return "png";
  if (mime === "image/x-ms-bmp") return "bmp";
  return mime.split("/")[1]?.replace(/[^a-z0-9]/g, "") || "png";
}

function dataImageFile(src, index) {
  const match = String(src || "").match(/^data:(image\/[a-z0-9.+-]+);base64,([a-z0-9+/=\s]+)$/i);
  if (!match) return null;
  try {
    const binary = atob(match[2].replace(/\s/g, ""));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new File([bytes], `paste_${Date.now()}_${index}.${imageExtension(match[1])}`, { type: match[1] });
  } catch (_) { return null; }
}

function clipboardImageFiles(clipboard) {
  const files = [];
  const seen = new Set();
  const add = (file) => {
    if (!file || !String(file.type || "").toLowerCase().startsWith("image/")) return;
    // lastModified 를 키에 넣으면 안 된다. 클립보드에서 만들어지는 File 은 생성
    // 시각이 lastModified 로 찍혀서, 같은 이미지라도 꺼낼 때마다 값이 달라진다
    // (실제로 한 번 붙여넣은 표가 14ms 차이로 두 번 업로드돼 그림이 2개 들어갔다).
    const key = `${file.name || ""}:${file.type || ""}:${file.size || 0}`;
    if (seen.has(key)) return;
    seen.add(key);
    files.push(file);
  };
  // items 와 files 는 같은 클립보드 이미지를 서로 다른 File 객체로 준다. 한쪽만 읽는다.
  const direct = Array.from(clipboard?.files || []);
  if (direct.length) direct.forEach(add);
  else {
    Array.from(clipboard?.items || []).forEach((item) => {
      if (item.kind === "file") add(item.getAsFile());
    });
  }
  if (!files.length) {
    const rich = clipboard?.getData("text/html") || "";
    try {
      const doc = new DOMParser().parseFromString(rich, "text/html");
      doc.querySelectorAll('img[src^="data:image/"]').forEach((img, index) => add(dataImageFile(img.getAttribute("src"), index)));
    } catch (_) { /* fall through to ordinary rich-text paste */ }
  }
  return files;
}

function tsvTableFragment(text) {
  const rows = String(text || "").replace(/\r/g, "").split("\n").filter((line, i, all) => line || i < all.length - 1);
  const table = document.createElement("table");
  const tbody = document.createElement("tbody");
  rows.forEach((line, ri) => {
    const tr = document.createElement("tr");
    line.split("\t").forEach((value) => {
      const cell = document.createElement(ri === 0 ? "th" : "td");
      cell.textContent = value;
      tr.appendChild(cell);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  const fragment = document.createDocumentFragment();
  fragment.appendChild(table);
  fragment.appendChild(document.createElement("p"));
  return fragment;
}

function richFragment(html) {
  const template = document.createElement("template");
  template.innerHTML = sanitizeHtml(html);
  // Excel/Word 클립보드 HTML 은 <style> 블록을 끌고 온다. contentEditable 안에
  // 들어가면 그 CSS 가 문서 전체에 적용돼 앱 화면이 뒤틀린다(저장 시엔 서버가
  // 지우므로 새로고침 전까지만 깨져 원인 찾기도 어렵다). 붙일 때 걷어낸다.
  template.content.querySelectorAll("style").forEach((node) => node.remove());
  return template.content.cloneNode(true);
}

export function richTextHasContent(value) {
  const raw = String(value || "");
  if (/<(?:img|table)\b/i.test(raw)) return true;
  if (typeof DOMParser === "undefined") return raw.replace(/<[^>]*>/g, "").trim().length > 0;
  try { return !!new DOMParser().parseFromString(raw, "text/html").body.textContent.trim(); }
  catch (_) { return raw.replace(/<[^>]*>/g, "").trim().length > 0; }
}

export function RichBoardContent({ html, className = "", style = {} }) {
  let safe = sanitizeHtml(html || "");
  if (typeof DOMParser !== "undefined") {
    try {
      const doc = new DOMParser().parseFromString(safe, "text/html");
      doc.body.querySelectorAll("img").forEach((img) => {
        const src = rawImageUrl(img.getAttribute("src") || "");
        if (src) img.setAttribute("src", authSrc(src));
      });
      safe = doc.body.innerHTML;
    } catch (_) { /* sanitized source is still safe */ }
  }
  return <div className={`rich-board-content${className ? ` ${className}` : ""}`} style={style} dangerouslySetInnerHTML={{ __html: safe }} />;
}

export default function RichBoardEditor({
  value = "", onChange, uploadUrl, placeholder = "본문을 입력하세요", minHeight = 150,
  disabled = false, onUploadError, showCommands = true,
}) {
  const ref = useRef(null);
  const lastEmitted = useRef("");
  const [uploading, setUploading] = useState(false);

  const emit = useCallback(() => {
    const html = serializedHtml(ref.current);
    lastEmitted.current = html;
    onChange?.(html);
  }, [onChange]);

  useEffect(() => {
    if (!ref.current || value === lastEmitted.current) return;
    const safe = sanitizeHtml(value || "");
    if (serializedHtml(ref.current) !== safe) {
      ref.current.innerHTML = safe;
      decorateEditorImages(ref.current);
    }
  }, [value]);

  const paste = useCallback(async (event) => {
    if (disabled) return;
    const clipboard = event.clipboardData;
    const range = rangeInsideEditor(ref.current);
    const plain = clipboard?.getData("text/plain") || "";
    const rich = clipboard?.getData("text/html") || "";

    // 표가 이미지보다 먼저다. Excel·시트에서 표를 복사하면 클립보드에 표(text/plain
    // TSV·text/html)와 "표를 찍은 비트맵"이 함께 담긴다. 이미지를 먼저 보면 표가
    // 그림 한 장으로 들어가 버려서 본문에서 검색도 편집도 되지 않는다.
    if (range && plain.includes("\t")) {
      event.preventDefault();
      insertFragment(range, tsvTableFragment(plain));
      emit();
      return;
    }
    if (range && /<table[\s>]/i.test(rich)) {
      event.preventDefault();
      insertFragment(range, richFragment(rich));
      decorateEditorImages(ref.current);
      emit();
      return;
    }

    const imageFiles = clipboardImageFiles(clipboard);
    if (imageFiles.length && range && uploadUrl) {
      event.preventDefault();
      const marker = document.createElement("span");
      marker.dataset.uploadPlaceholder = "1";
      marker.textContent = "이미지 업로드 중…";
      marker.style.cssText = "color:var(--text-secondary);font-size:12px;";
      range.deleteContents(); range.insertNode(marker);
      setUploading(true);
      try {
        const uploaded = [];
        for (const file of imageFiles) {
          const fd = new FormData();
          const ext = imageExtension(file.type);
          fd.append("file", new File([file], `paste_${Date.now()}_${uploaded.length}.${ext}`, { type: file.type }));
          const result = await sf(uploadUrl, { method: "POST", body: fd });
          if (result?.url) uploaded.push(result);
        }
        if (!uploaded.length) throw new Error("업로드된 이미지 주소를 받지 못했습니다");
        const fragment = document.createDocumentFragment();
        uploaded.forEach((item) => {
          const img = document.createElement("img");
          img.dataset.flowSrc = item.url;
          img.src = authSrc(item.url);
          img.alt = item.filename || "pasted image";
          fragment.appendChild(img);
        });
        fragment.appendChild(document.createElement("p"));
        marker.replaceWith(fragment);
        emit();
      } catch (error) {
        marker.textContent = "이미지 붙여넣기 실패";
        marker.style.color = "var(--danger, #dc2626)";
        onUploadError?.(error);
      } finally { setUploading(false); }
      return;
    }

    if (range && rich) {
      event.preventDefault();
      insertFragment(range, richFragment(rich));
      decorateEditorImages(ref.current);
      emit();
    }
  }, [disabled, emit, onUploadError, uploadUrl]);

  const command = (name) => {
    ref.current?.focus();
    document.execCommand(name, false);
    emit();
  };

  return <div className="rich-board-shell">
    <style>{`
      .rich-board-shell{border:1px solid var(--border);border-radius:6px;background:var(--bg-primary);overflow:hidden}
      .rich-board-toolbar{display:flex;gap:4px;align-items:center;padding:5px 7px;border-bottom:1px solid var(--border);background:var(--bg-tertiary)}
      .rich-board-toolbar button{border:1px solid var(--border);background:var(--bg-secondary);color:var(--text-secondary);border-radius:4px;padding:3px 8px;font-size:12px;cursor:pointer}
      .rich-board-toolbar span{margin-left:auto;color:var(--text-secondary);font-size:11px}
      .rich-board-editor{padding:10px 12px;outline:none;overflow:auto;line-height:1.65;white-space:pre-wrap;word-break:break-word}
      .rich-board-editor:empty:before{content:attr(data-placeholder);color:var(--text-secondary);pointer-events:none}
      .rich-board-content{line-height:1.65;white-space:pre-wrap;word-break:break-word;overflow:auto}
      .rich-board-editor img,.rich-board-content img{display:block;max-width:min(100%,720px);max-height:560px;object-fit:contain;margin:9px 0;border:1px solid var(--border);border-radius:6px;background:var(--bg-primary)}
      .rich-board-editor table,.rich-board-content table{border-collapse:collapse;min-width:360px;max-width:100%;margin:9px 0;background:var(--bg-card);white-space:normal}
      .rich-board-editor th,.rich-board-editor td,.rich-board-content th,.rich-board-content td{border:1px solid var(--border);padding:6px 9px;text-align:left;vertical-align:top;min-width:72px}
      .rich-board-editor th,.rich-board-content th{background:var(--bg-tertiary);font-weight:750}
      .rich-board-editor p{margin:4px 0}.rich-board-content p{margin:4px 0}
    `}</style>
    <div className="rich-board-toolbar">
      {showCommands && <>
        <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => command("bold")} disabled={disabled}><b>B</b> 굵게</button>
        <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => command("insertUnorderedList")} disabled={disabled}>• 목록</button>
      </>}
      {/* 버튼을 감춰도 서식은 살아 있다 — contentEditable 이 Ctrl+B/I/U 를 직접 처리하고,
          그때 input 이벤트가 떠서 emit 이 그대로 걸린다. */}
      <span>{uploading ? "이미지 업로드 중…" : showCommands ? "이미지·Excel 표 Ctrl+V 지원" : "Ctrl+B 굵게 · 이미지·Excel 표 Ctrl+V 지원"}</span>
    </div>
    <div ref={ref} className="rich-board-editor" contentEditable={!disabled} suppressContentEditableWarning
      data-placeholder={placeholder} onInput={emit} onBlur={emit} onPaste={paste}
      style={{ minHeight, maxHeight: Math.max(360, minHeight * 2.5) }} />
  </div>;
}
