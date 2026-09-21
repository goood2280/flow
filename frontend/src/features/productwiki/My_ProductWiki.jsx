import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ProductSemanticPanel from "./ProductSemanticPanel";
import RichBoardEditor from "../../components/RichBoardEditor";
import { Banner, Button, Input, PageShell, Select } from "../../components/ui";
import { authSrc, sf } from "../../lib/api";
import { canManagePage } from "../../lib/permissions";
import { sanitizeHtml } from "../../lib/sanitizeHtml";
import "./My_ProductWiki.css";

const API = "/api/product-wiki";

function formatDateTime(val) {
  if (!val) return "—";
  try {
    const s = String(val).replace("T", " ");
    return s.includes(".") ? s.split(".")[0] : s.slice(0, 19);
  } catch {
    return val;
  }
}

function formatInline(str) {
  if (!str) return "";
  let s = String(str);
  // Images: ![alt](url)
  s = s.replace(
    /!\[([^\]]*)\]\(([^)]+)\)/g,
    '<div class="pw-wiki-img-wrap"><img src="$2" alt="$1" class="pw-wiki-img" /><span class="pw-wiki-img-caption">$1</span></div>'
  );
  // Plain text rule: strip bold and raw <b>/<strong> tags completely
  s = s.replace(/\*\*([^*]+)\*\*/g, "$1");
  s = s.replace(/<\/?(?:b|strong)>/gi, "");
  // Italic: *text* -> plain text as well
  s = s.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "$1");
  // Links: [text](url)
  s = s.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer" class="pw-wiki-link">$1</a>'
  );
  // Code: `code`
  s = s.replace(/`([^`]+)`/g, '<code class="pw-wiki-code">$1</code>');
  return s;
}

function markdownToHtml(md) {
  if (!md) return "";
  let html = "";
  const lines = String(md).split("\n");
  let inTable = false;
  let tableRows = [];
  let secIdx = 0;

  const flushTable = () => {
    if (!tableRows.length) return "";
    let t = '<div class="pw-table-wrapper"><table class="pw-wiki-table">';
    tableRows.forEach((row, rIdx) => {
      const cells = row
        .split("|")
        .map((c) => c.trim())
        .filter((_, idx, arr) => idx > 0 && idx < arr.length - 1);
      if (rIdx === 0) {
        t += "<thead><tr>" + cells.map((c) => `<th>${formatInline(c)}</th>`).join("") + "</tr></thead><tbody>";
      } else if (rIdx === 1 && cells.every((c) => /^:?-+:?$/.test(c))) {
        // separator line
      } else {
        t += "<tr>" + cells.map((c) => `<td>${formatInline(c)}</td>`).join("") + "</tr>";
      }
    });
    t += "</tbody></table></div>";
    tableRows = [];
    inTable = false;
    return t;
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.trim();

    if (line.startsWith("|") && line.endsWith("|")) {
      inTable = true;
      tableRows.push(line);
      continue;
    } else if (inTable) {
      html += flushTable();
    }

    if (!line) continue;

    if (line.startsWith(">")) {
      const quoteContent = line.replace(/^>\s*/, "");
      html += `<blockquote class="pw-wiki-quote">${formatInline(quoteContent)}</blockquote>`;
      continue;
    }

    if (line.startsWith("#### ")) {
      const title = line.slice(5).trim();
      html += `<h4 class="pw-wiki-h4">${formatInline(title)}</h4>`;
      continue;
    }
    if (line.startsWith("### ")) {
      secIdx++;
      const title = line.slice(4).trim();
      const tag = title.match(/\[([0-9a-fA-F]{8})\]\s*$/);
      const id = tag ? `entry-${tag[1].toLowerCase()}` : `sec-${secIdx}`;
      html += `<h3 id="${id}" class="pw-wiki-h3">${formatInline(title)}</h3>`;
      continue;
    }
    if (line.startsWith("## ")) {
      secIdx++;
      const title = line.slice(3).trim();
      html += `<h2 id="sec-${secIdx}" class="pw-wiki-h2"><span class="pw-heading-anchor">§</span> ${formatInline(title)}</h2>`;
      continue;
    }
    if (line.startsWith("# ")) {
      const title = line.slice(2).trim();
      html += `<h1 class="pw-wiki-h1">${formatInline(title)}</h1>`;
      continue;
    }

    if (line.startsWith("<") && line.endsWith(">")) {
      html += line;
      continue;
    }

    // Strip any bullet marks (- or * or 1.) so all items render as regular plain text paragraphs
    const plainLine = line.replace(/^[-*+]\s+/, "").replace(/^\d+\.\s+/, "");
    html += `<p class="pw-wiki-p">${formatInline(plainLine)}</p>`;
  }

  if (inTable) html += flushTable();

  return sanitizeHtml(html);
}

export default function My_ProductWiki({ user }) {
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  // Issue registration / edit form state
  const [formOpen, setFormOpen] = useState(true);
  const [editingEntry, setEditingEntry] = useState(null);
  const [issueTitle, setIssueTitle] = useState("");
  const [issueBody, setIssueBody] = useState("");

  // Raw issues drawer / modal state & accordion expanded IDs
  const [rawModalOpen, setRawModalOpen] = useState(false);
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [selectedHistory, setSelectedHistory] = useState(null);
  const [historyRows, setHistoryRows] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // TOC collapsible state
  const [tocOpen, setTocOpen] = useState(true);

  const mounted = useRef(true);
  const loadGeneration = useRef(0);
  const [semanticRefresh, setSemanticRefresh] = useState(0);

  // Load products list on mount
  useEffect(() => {
    mounted.current = true;
    sf(`${API}/products`)
      .then((data) => {
        if (!mounted.current) return;
        const list = data.products || [];
        setProducts(list);
        if (list.length > 0) {
          setProduct((prev) => prev || list[0]);
        }
      })
      .catch((err) => {
        if (mounted.current) setError(err.message);
      })
      .finally(() => {
        if (mounted.current) setCatalogLoading(false);
      });
    return () => {
      mounted.current = false;
    };
  }, []);

  // Load product document
  const loadProduct = useCallback(async (targetProduct) => {
    if (!targetProduct) return;
    const ticket = ++loadGeneration.current;
    setDoc(null);
    setLoading(true);
    setError("");
    try {
      const data = await sf(`${API}/product?product=${encodeURIComponent(targetProduct)}`);
      if (mounted.current && ticket === loadGeneration.current) {
        setDoc(data);
      }
    } catch (err) {
      if (mounted.current && ticket === loadGeneration.current) setError(err.message);
    } finally {
      if (mounted.current && ticket === loadGeneration.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (product) {
      loadProduct(product);
      setEditingEntry(null);
      setIssueTitle("");
      setIssueBody("");
    }
  }, [product, loadProduct]);

  // Handle saving an issue (create or update)
  const handleSaveIssue = async (e) => {
    e.preventDefault();
    if (busy || loading || !doc || doc.product?.toLowerCase() !== product.toLowerCase()) return;
    if (!issueTitle.trim()) {
      setError("이슈 제목을 입력해 주세요.");
      return;
    }
    if (!product) {
      setError("제품을 먼저 선택해 주세요.");
      return;
    }

    setBusy(true);
    setError("");
    setNotice("");

    const payload = {
      product,
      expected_revision: doc.revision,
      entry_id: editingEntry?.id || "",
      title: issueTitle.trim(),
      text: issueBody || issueTitle.trim(),
    };
    if (payload.text.length > 40000) {
      setError("이슈 내용은 40,000자 이내로 입력해 주세요.");
      setBusy(false);
      return;
    }

    try {
      const updatedDoc = await sf(`${API}/intake`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setDoc(updatedDoc);
      const warnings = [updatedDoc.intake_warning, updatedDoc.compile_warning, updatedDoc.semantic_proposal?.warning].filter(Boolean);
      setNotice(warnings.length ? `원문을 저장했습니다. ${warnings.join(" ")}` : "이슈를 구조화하고 위키에 반영했습니다. 아래에서 제안된 Step·Item 연결을 확인해 주세요.");
      setSemanticRefresh((value) => value + 1);
      setEditingEntry(null);
      setIssueTitle("");
      setIssueBody("");
    } catch (err) {
      setError(err.message || "이슈 저장에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  // Begin editing an existing entry
  const startEdit = (entry) => {
    setEditingEntry(entry);
    setIssueTitle(entry.title || "");
    setIssueBody(entry.source_text ?? entry.body ?? "");
    setFormOpen(true);
    setRawModalOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  // Cancel editing
  const cancelEdit = () => {
    setEditingEntry(null);
    setIssueTitle("");
    setIssueBody("");
  };

  // Delete an entry
  const handleDeleteEntry = async (entry) => {
    const ok = window.confirm(
      `'${entry.title}' 이슈를 삭제하시겠습니까?\n삭제한 사용자와 시각이 영구 감사 기록에 보존되며, 전체 위키 문서에서도 제외됩니다.`
    );
    if (!ok) return;

    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updatedDoc = await sf(
        `${API}/entries/${encodeURIComponent(entry.id)}?product=${encodeURIComponent(
          product
        )}&expected_revision=${doc?.revision || 0}`,
        { method: "DELETE" }
      );
      setDoc(updatedDoc);
      setNotice(`이슈 '${entry.title}'가 삭제되었으며 위키 문서가 재합성되었습니다.`);
      if (editingEntry?.id === entry.id) {
        cancelEdit();
      }
    } catch (err) {
      setError(err.message || "이슈 삭제에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  // Reprocess the original issue with the current product vocabulary.
  const handleResyncEntry = async (entry) => {
    if (busy || loading) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const res = await sf(`${API}/intake`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product, expected_revision: doc.revision,
          entry_id: entry.id, title: entry.source_title || entry.title,
          text: entry.source_text || entry.body || entry.title }),
      });
      setDoc(res);
      setSemanticRefresh((value) => value + 1);
      const warnings = [res.intake_warning, res.compile_warning, res.semantic_proposal?.warning].filter(Boolean);
      setNotice(warnings.length ? `원문을 보존했습니다. ${warnings.join(" ")}` : "최신 제품 연결표로 이슈를 다시 정리했습니다. 제안된 연결을 확인해 주세요.");
    } catch (err) { setError(err.message || "이슈 재정리에 실패했습니다."); }
    finally { setBusy(false); }
  };

  // View audit history for an entry
  const viewHistory = async (entry) => {
    setSelectedHistory(entry);
    setHistoryLoading(true);
    try {
      const data = await sf(
        `${API}/history?product=${encodeURIComponent(product)}&entry_id=${encodeURIComponent(entry.id)}`
      );
      setHistoryRows(data.history || []);
    } catch (err) {
      setError(err.message || "이력 조회 실패");
    } finally {
      setHistoryLoading(false);
    }
  };

  // Download compiled markdown
  const downloadMarkdown = () => {
    if (!doc?.wiki_document) return;
    const blob = new Blob([doc.wiki_document], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${product}_Wiki_${new Date().toISOString().slice(0, 10)}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const compiledHtml = useMemo(() => {
    return markdownToHtml(doc?.wiki_document || "");
  }, [doc?.wiki_document]);

  const tocList = doc?.wiki_toc || [];
  const activeEntries = (doc?.entries || []).filter((e) => !e.deleted);
  const wikiBodyRef = useRef(null);

  // Section chips: each entry-owned h3 (id="entry-xxxxxxxx") gets
  // 수정/이력/원문 shortcuts. Injected as real DOM nodes (not HTML strings)
  // so the HTML sanitizer policy stays untouched.
  const handleWikiChip = (short, action) => {
    const entry = (doc?.entries || []).find((e) =>
      String(e.id || "").toLowerCase().startsWith(String(short || "").toLowerCase())
    );
    if (!entry) {
      setError("연결된 이슈를 찾지 못했습니다.");
      return;
    }
    if (action === "edit") startEdit(entry);
    else if (action === "history") viewHistory(entry);
    else if (action === "source") {
      setExpandedIds((prev) => {
        const next = new Set(prev);
        next.add(entry.id);
        return next;
      });
      setRawModalOpen(true);
    }
  };

  useEffect(() => {
    const root = wikiBodyRef.current;
    if (!root) return;
    const byShort = new Map(
      (doc?.entries || []).map((e) => [String(e.id || "").slice(0, 8).toLowerCase(), e])
    );
    root.querySelectorAll(".pw-sec-chips[data-injected]").forEach((n) => n.remove());
    root.querySelectorAll(".pw-module-records[data-injected]").forEach((n) => n.remove());
    root.querySelectorAll(".pw-table-source[data-injected]").forEach((n) => n.remove());
    root.querySelectorAll("tr[data-folded]").forEach((tr) => {
      tr.removeAttribute("data-folded");
      tr.style.display = "";
    });
    root.querySelectorAll('h3[id^="entry-"]').forEach((h) => {
      const short = String(h.id || "").replace(/^entry-/, "").toLowerCase();
      const entry = byShort.get(short);
      if (!short || !entry) return;
      const wrap = document.createElement("span");
      wrap.className = "pw-sec-chips";
      wrap.setAttribute("data-injected", "1");
      [["edit", "수정"], ["history", "이력"], ["source", "원문"]].forEach(([action, label]) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "pw-sec-chip";
        b.textContent = label;
        b.setAttribute("aria-label", `${entry.title || entry.id} ${label}`);
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          handleWikiChip(short, action);
        });
        wrap.appendChild(b);
      });
      h.appendChild(wrap);
    });
    // Module record list: every h2 owning entry-owned h3 sections gets a
    // "기록 N건" toggle with a 갱신순 contributor popover. Grouping is read
    // from the rendered document (h2 scope), so AI and deterministic docs
    // behave the same without extra bucket logic.
    const closeAllPopovers = () => {
      root.querySelectorAll(".pw-module-popover[data-open]").forEach((p) => p.removeAttribute("data-open"));
    };
    const onDocClick = (ev) => {
      if (!ev.target.closest(".pw-module-records")) closeAllPopovers();
    };
    document.addEventListener("click", onDocClick);
    root.querySelectorAll("h2").forEach((h) => {
      const shorts = [];
      let node = h.nextElementSibling;
      while (node && !/^H[12]$/.test(node.tagName || "")) {
        if (node.tagName === "H3" && String(node.id || "").startsWith("entry-")) {
          const short = String(node.id).replace(/^entry-/, "").toLowerCase();
          if (byShort.has(short) && !shorts.includes(short)) shorts.push(short);
        }
        node = node.nextElementSibling;
      }
      if (!shorts.length) return;
      const rows = shorts
        .map((s) => byShort.get(s))
        .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
      const wrap = document.createElement("div");
      wrap.className = "pw-module-records";
      wrap.setAttribute("data-injected", "1");
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "pw-module-toggle";
      toggle.textContent = `기록 ${rows.length}건 ▾`;
      toggle.setAttribute("aria-label", "기여 이슈 목록 보기 (갱신순)");
      const pop = document.createElement("div");
      pop.className = "pw-module-popover";
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", "기여 이슈 목록 (갱신순)");
      const list = document.createElement("ul");
      list.className = "pw-module-list";
      rows.slice(0, 50).forEach((entry) => {
        const li = document.createElement("li");
        li.className = "pw-module-item";
        const title = document.createElement("button");
        title.type = "button";
        title.className = "pw-module-title";
        title.textContent = entry.title || entry.id;
        title.title = entry.title || entry.id;
        title.addEventListener("click", () => handleWikiChip(String(entry.id).slice(0, 8), "source"));
        const meta = document.createElement("span");
        meta.className = "pw-module-meta";
        meta.textContent = `${entry.author || "—"} · ${formatDateTime(entry.updated_at)} · ${entry.status || "open"}`;
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "pw-sec-chip";
        edit.textContent = "수정";
        edit.addEventListener("click", () => handleWikiChip(String(entry.id).slice(0, 8), "edit"));
        li.append(title, meta, edit);
        list.appendChild(li);
      });
      pop.appendChild(list);
      if (rows.length > 50) {
        const more = document.createElement("p");
        more.className = "pw-module-more";
        more.textContent = `외 ${rows.length - 50}건은 아래 섹션에서 확인하세요.`;
        pop.appendChild(more);
      }
      toggle.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const wasOpen = pop.hasAttribute("data-open");
        closeAllPopovers();
        if (!wasOpen) pop.setAttribute("data-open", "1");
      });
      wrap.append(toggle, pop);
      h.insertAdjacentElement("afterend", wrap);
    });
    // Table source + folding: every rendered table gets an attribution line
    // from its nearest preceding entry-owned h3 (1-issue-1-section rule).
    // Body tables with >20 rows fold to the first 20.
    const FOLD_LIMIT = 20;
    root.querySelectorAll(".pw-table-wrapper").forEach((tw) => {
      let node = tw.previousElementSibling;
      let ownerShort = "";
      while (node) {
        const tag = node.tagName || "";
        if (tag === "H3" && String(node.id || "").startsWith("entry-")) {
          ownerShort = String(node.id).replace(/^entry-/, "").toLowerCase();
          break;
        }
        if (/^H[12]$/.test(tag) || node.classList?.contains("pw-module-records")) break;
        node = node.previousElementSibling;
      }
      const src = document.createElement("div");
      src.className = "pw-table-source";
      src.setAttribute("data-injected", "1");
      const owner = ownerShort ? byShort.get(ownerShort) : null;
      if (owner) {
        const label = document.createElement("span");
        label.textContent = `출처: ${owner.title || owner.id} · ${owner.updated_by || "—"} ${formatDateTime(owner.updated_at)} `;
        const go = document.createElement("button");
        go.type = "button";
        go.className = "pw-table-src-btn";
        go.textContent = "원문 보기";
        go.addEventListener("click", () => handleWikiChip(ownerShort, "source"));
        src.append(label, go);
      } else {
        src.textContent = "출처 확인 필요 — 합성표일 수 있습니다.";
      }
      const bodyRows = tw.querySelectorAll("table tbody tr");
      if (bodyRows.length > FOLD_LIMIT) {
        bodyRows.forEach((tr, i) => {
          if (i >= FOLD_LIMIT) {
            tr.setAttribute("data-folded", "1");
            tr.style.display = "none";
          }
        });
        const fold = document.createElement("button");
        fold.type = "button";
        fold.className = "pw-table-src-btn";
        fold.textContent = `전체 ${bodyRows.length}행 보기 ▾`;
        fold.addEventListener("click", () => {
          const folded = tw.querySelectorAll('tr[data-folded]');
          const collapsed = Array.from(folded).some((tr) => tr.style.display === "none");
          folded.forEach((tr) => {
            tr.style.display = collapsed ? "" : "none";
          });
          fold.textContent = collapsed ? "접기 ▴" : `전체 ${bodyRows.length}행 보기 ▾`;
        });
        src.append(document.createTextNode(" · "), fold);
      }
      tw.insertAdjacentElement("afterend", src);
    });
    return () => {
      document.removeEventListener("click", onDocClick);
      root.querySelectorAll(".pw-sec-chips[data-injected]").forEach((n) => n.remove());
      root.querySelectorAll(".pw-module-records[data-injected]").forEach((n) => n.remove());
      root.querySelectorAll(".pw-table-source[data-injected]").forEach((n) => n.remove());
    };
    // handleWikiChip follows the current render's doc; re-inject per document.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compiledHtml, doc]);

  return (
    <PageShell className="product-wiki full-screen-mode">
      {/* ── Top Bar: Product Selector & Action Buttons ── */}
      <header className="pw-top-bar">
        <div className="pw-top-left">
          <label className="pw-selector-label">
            <span className="pw-label-text">제품</span>
            <Select
              className="pw-product-select"
              value={product}
              disabled={catalogLoading || busy || loading}
              onChange={(e) => setProduct(e.target.value)}
            >
              <option value="">제품을 선택하세요</option>
              {products.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </label>
        </div>

        <div className="pw-top-actions">
          <Button
            variant="secondary"
            onClick={() => setRawModalOpen(true)}
            title="등록된 원본 이슈 목록을 열어보고 수정하거나 삭제합니다."
          >
            📄 원본 이슈 목록 ({activeEntries.length}건)
          </Button>

          {doc?.wiki_document && (
            <Button variant="ghost" onClick={downloadMarkdown} title="Markdown 형식으로 다운로드">
              📥 다운로드
            </Button>
          )}
        </div>
      </header>

      {error && (
        <Banner tone="danger">
          <span>{error}</span>
        </Banner>
      )}
      {notice && (
        <Banner tone="info">
          <span>{notice}</span>
        </Banner>
      )}

      {/* ── Direct Issue Registration Box ── */}
      {product && (
        <section className="pw-issue-card">
          <div className="pw-issue-card-header">
            <div className="pw-issue-card-title">
              <span className="pw-icon">📝</span>
              <h3>{editingEntry ? "이슈 수정" : "이슈 등록"}</h3>
              {editingEntry && (
                <span className="pw-editing-indicator">
                  원작성자: <b>{editingEntry.author}</b> · 작성일: {formatDateTime(editingEntry.created_at)}
                  <button type="button" onClick={cancelEdit} className="pw-cancel-link">
                    수정 취소
                  </button>
                </span>
              )}
            </div>

            <button
              type="button"
              className="pw-collapse-btn"
              onClick={() => setFormOpen(!formOpen)}
            >
              {formOpen ? "▲ 작성창 접기" : "▼ 이슈 등록창 열기"}
            </button>
          </div>

          {formOpen && (
            <form onSubmit={handleSaveIssue} className="pw-issue-form">
              <div className="pw-form-field">
                <input
                  type="text"
                  className="pw-title-input"
                  placeholder="이슈 제목을 입력하세요"
                  value={issueTitle}
                  onChange={(e) => setIssueTitle(e.target.value)}
                  disabled={busy}
                  required
                  maxLength={200}
                />
              </div>

              <div className="pw-form-field">
                <div className="pw-editor-container">
                  <RichBoardEditor
                    value={issueBody}
                    onChange={setIssueBody}
                    uploadUrl="/api/product-wiki/upload"
                    placeholder="이슈 내용을 입력하세요"
                    disabled={busy}
                  />
                </div>
              </div>

              <div className="pw-form-footer">
                <div className="pw-hint">
                  원문을 보존하고 연결된 AI가 제품 별칭·Step·Item 참고표로 내용을 정리합니다. 불확실한 연결은 아래에서 확인하세요.
                </div>
                <div className="pw-form-buttons">
                  {editingEntry && (
                    <Button type="button" variant="secondary" onClick={cancelEdit} disabled={busy}>
                      취소
                    </Button>
                  )}
                  <Button type="submit" variant="primary" disabled={busy || loading || !doc || !issueTitle.trim()}>
                    {busy
                      ? "원문 저장 · AI 정리 중…"
                      : editingEntry
                      ? "수정 내용 위키에 반영"
                      : "저장 · AI로 정리"}
                  </Button>
                </div>
              </div>
            </form>
          )}
        </section>
      )}

      {product && <details className="pw-issue-card">
        <summary>이슈의 제품 용어 · Step / Item 연결 검토</summary>
        <ProductSemanticPanel key={product} product={product} user={user} reviewOnly refreshKey={semanticRefresh} />
      </details>}

      {/* ── Main Wiki Document View (Namuwiki / Wikipedia style) ── */}
      {loading ? (
        <div className="pw-loading-state">
          <div className="pw-spinner" />
          <p>제품 위키 문서를 불러오는 중입니다…</p>
        </div>
      ) : !product ? (
        <div className="pw-empty-view">
          <h2>제품을 선택하세요</h2>
          <p>상단에서 제품을 선택하여 위키를 시작하세요.</p>
        </div>
      ) : (
        <div className="pw-wiki-layout">
          <article className="pw-wiki-article">
            {/* Wiki metadata header */}
            <div className="pw-wiki-meta-header">
              <div className="pw-wiki-doc-title">
                <h1>{product}</h1>
                <span className="pw-wiki-badge">위키 문서</span>
              </div>
              <div className="pw-wiki-byline">
                <span>최근 갱신: {formatDateTime(doc?.wiki_updated_at)}</span>
                {doc?.wiki_updated_by && <span> · 편집자: <b>{doc.wiki_updated_by}</b></span>}
                <span> · 등록 기술 항목: <b>{activeEntries.length}건</b></span>
                <span> · Revision {doc?.revision || 0}</span>
              </div>
            </div>

            {/* Namuwiki Style Table of Contents (TOC) */}
            {tocList.length > 0 && (
              <nav className="pw-namu-toc" aria-label="문서 목차">
                <div className="pw-namu-toc-header">
                  <span className="pw-namu-toc-title">목차</span>
                  <button
                    type="button"
                    onClick={() => setTocOpen(!tocOpen)}
                    className="pw-namu-toc-toggle"
                  >
                    [{tocOpen ? "숨기기" : "보이기"}]
                  </button>
                </div>
                {tocOpen && (
                  <ol className="pw-namu-toc-list">
                    {tocList.map((item, idx) => (
                      <li key={idx} className={`pw-namu-toc-item pw-level-${item.level}`}>
                        <a href={`#${item.id}`}>{item.title}</a>
                      </li>
                    ))}
                  </ol>
                )}
              </nav>
            )}

            {/* Continuous Wiki Content */}
            <div
              ref={wikiBodyRef}
              className="pw-wiki-body"
              dangerouslySetInnerHTML={{
                __html: compiledHtml || "<p>등록된 위키 본문이 없습니다.</p>",
              }}
            />
          </article>
        </div>
      )}

      {/* ── Raw Issues Modal / Drawer (Single-Line Compact Accordion List) ── */}
      {rawModalOpen && (
        <div className="pw-modal-overlay" onClick={() => setRawModalOpen(false)}>
          <div className="pw-modal-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="pw-drawer-header">
              <div>
                <h2>{product} 원본 이슈 목록</h2>
                <p className="pw-drawer-sub">
                  총 {activeEntries.length}건 · 이슈를 클릭하면 세부 내용을 펼쳐보고 수정, 삭제 및 위키 재반영을 할 수 있습니다.
                </p>
              </div>
              <button
                type="button"
                className="pw-drawer-close"
                onClick={() => setRawModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <div className="pw-drawer-content">
              {activeEntries.length === 0 ? (
                <div className="pw-drawer-empty">
                  등록된 원본 이슈가 없습니다. 상단에서 첫 이슈를 등록해 보세요.
                </div>
              ) : (
                activeEntries.map((entry, idx) => {
                  const isExpanded = expandedIds.has(entry.id);
                  const isModified = entry.updated_by && entry.updated_by !== entry.author;
                  return (
                    <div key={entry.id} className={`pw-raw-card ${isExpanded ? "pw-expanded" : ""}`}>
                      <div
                        className="pw-raw-card-row"
                        onClick={() => {
                          setExpandedIds((prev) => {
                            const next = new Set(prev);
                            if (next.has(entry.id)) next.delete(entry.id);
                            else next.add(entry.id);
                            return next;
                          });
                        }}
                        role="button"
                        tabIndex={0}
                      >
                        <div className="pw-raw-title-row">
                          <span className="pw-raw-num">#{idx + 1}</span>
                          <span className="pw-raw-title-single" title={entry.title}>
                            {entry.title}
                          </span>
                        </div>
                        <div className="pw-raw-row-meta">
                          <span className="pw-raw-meta-author">{entry.author}</span>
                          <span className="pw-raw-meta-time">{formatDateTime(entry.created_at)}</span>
                          <span className="pw-raw-chevron">{isExpanded ? "▲" : "▼"}</span>
                        </div>
                      </div>

                      {isExpanded && (
                        <div className="pw-raw-expanded-body">
                          <div className="pw-raw-byline">
                            <span>작성자: <b>{entry.author}</b> ({formatDateTime(entry.created_at)})</span>
                            {isModified && (
                              <span className="pw-raw-updated">
                                · 최종 수정: <b>{entry.updated_by}</b> ({formatDateTime(entry.updated_at)})
                              </span>
                            )}
                          </div>

                          <div
                            className="pw-raw-content"
                            dangerouslySetInnerHTML={{
                              __html: sanitizeHtml(entry.source_text ?? entry.body ?? "—"),
                            }}
                          />
                          {entry.source_text && entry.body && entry.body !== entry.source_text && <details>
                            <summary>AI가 정리한 내용</summary>
                            <div className="pw-raw-content" dangerouslySetInnerHTML={{ __html: sanitizeHtml(entry.body) }} />
                          </details>}

                          <div className="pw-raw-footer">
                            <div className="pw-raw-actions">
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => startEdit(entry)}
                                disabled={busy}
                                title="이 이슈를 수정합니다."
                              >
                                ✏️ 수정
                              </Button>
                              <Button
                                size="sm"
                                variant="secondary"
                                disabled={busy}
                                onClick={() => handleResyncEntry(entry)}
                                title="원문을 최신 제품 연결표로 다시 해석하고 위키를 정리합니다."
                              >
                                🔄 위키에 재반영
                              </Button>
                              <Button
                                size="sm"
                                variant="danger"
                                onClick={() => handleDeleteEntry(entry)}
                                disabled={busy}
                                title="이 이슈를 삭제합니다."
                              >
                                🗑️ 삭제
                              </Button>
                            </div>
                            <button
                              type="button"
                              className="pw-history-btn"
                              onClick={() => viewHistory(entry)}
                            >
                              📜 변경 감사 이력 보기
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── History Audit Log Modal ── */}
      {selectedHistory && (
        <div className="pw-modal-overlay" onClick={() => setSelectedHistory(null)}>
          <div className="pw-history-modal" onClick={(e) => e.stopPropagation()}>
            <div className="pw-modal-header">
              <div>
                <h3>'{selectedHistory.title}' 변경 감사 이력</h3>
                <p className="pw-drawer-sub">ID: {selectedHistory.id}</p>
              </div>
              <button
                type="button"
                className="pw-drawer-close"
                onClick={() => setSelectedHistory(null)}
              >
                ✕
              </button>
            </div>

            <div className="pw-modal-body">
              {historyLoading ? (
                <div className="pw-loading-state">
                  <div className="pw-spinner" />
                  <p>이력 조회 중…</p>
                </div>
              ) : historyRows.length === 0 ? (
                <p>기록된 수정 이력이 없습니다.</p>
              ) : (
                historyRows.map((h, i) => (
                  <div key={i} className="pw-history-entry">
                    <div className="pw-history-header">
                      <span className="pw-rev-tag">Rev {h.revision}</span>
                      <span className="pw-action-tag pw-action-{h.action || 'update'}">
                        {h.action === "create" ? "최초 생성" : h.action === "delete" ? "삭제" : "수정"}
                      </span>
                      <span className="pw-actor">
                        작업자: <b>{h.actor}</b>
                      </span>
                      <span className="pw-time">{formatDateTime(h.at)}</span>
                    </div>
                    {h.changes && h.changes.length > 0 && (
                      <div className="pw-history-diff">
                        {h.changes.map((c, cIdx) => {
                          const hasTable = /<table[\s>]/i.test(String(c.before || "")) || /<table[\s>]/i.test(String(c.after || ""));
                          return (
                          <div key={cIdx} className="pw-diff-line">
                            <span className="pw-diff-field">{c.field}:</span>
                            {hasTable && <span className="pw-diff-table-tag">[표]</span>}
                            <span className="pw-diff-before">
                              {typeof c.before === "string" ? c.before.slice(0, 60) : String(c.before || "—")}
                            </span>
                            <span className="pw-diff-arrow">→</span>
                            <span className="pw-diff-after">
                              {typeof c.after === "string" ? c.after.slice(0, 60) : String(c.after || "—")}
                            </span>
                          </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
}
