import { useEffect, useMemo, useRef, useState } from "react";
import { Banner, Button, Input, Select, TabStrip } from "../../components/ui";
import { sf } from "../../lib/api";
import ProductStructure from "../productwiki/ProductStructure";

const ADMIN_SECTIONS = [
  { k: "product_aliases", l: "1. 제품별 별칭 연결 테이블" },
  { k: "inline_items", l: "2. Inline별 Step / Item & 별칭 매핑 테이블" },
  { k: "structure", l: "3. 공정 모듈 & 세부 구조물 단계" },
];

const post = (path, body) =>
  sf(`/api/product-semantics${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export default function ProductAdminPanel({ user }) {
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [section, setSection] = useState("inline_items");

  // Semantic Data
  const [semanticData, setSemanticData] = useState(null);
  const [dataLoading, setDataLoading] = useState(false);
  const [catalog, setCatalog] = useState(null);

  // Filter states for inline items table
  const [searchQuery, setSearchQuery] = useState("");
  const [moduleFilter, setModuleFilter] = useState("all");
  const [aliasOnlyFilter, setAliasOnlyFilter] = useState("all");

  // Inline Item Alias Editing Modal/Row state
  const [editingItem, setEditingItem] = useState(null); // { step_id, item_id, step_desc, item_desc, module, aliases: [] }
  const [newAliasInput, setNewAliasInput] = useState("");

  // Product Alias Editing state
  const [newProductAliasInput, setNewProductAliasInput] = useState("");
  const [productAliasList, setProductAliasList] = useState([]);

  // Direct Add New Item Modal state
  const [showAddItemModal, setShowAddItemModal] = useState(false);
  const [newItemForm, setNewItemForm] = useState({
    module: "",
    step_id: "",
    step_desc: "",
    item_id: "",
    item_desc: "",
    aliases_text: "",
  });

  const generation = useRef(0);

  // 1. Initial Load: Product List & Catalog
  useEffect(() => {
    setLoading(true);
    sf("/api/product-wiki/products")
      .then((data) => {
        const list = data.products || [];
        setProducts(list);
        if (list.length > 0) {
          setProduct((cur) => cur || list[0]);
        }
      })
      .catch((err) => setError(err.message || "제품 목록을 불러오지 못했습니다."))
      .finally(() => setLoading(false));

    sf("/api/product-semantics/catalog")
      .then(setCatalog)
      .catch(() => {});
  }, []);

  // 2. Load Semantic Data for selected Product
  async function reloadSemantic(targetProduct = product) {
    if (!targetProduct) return;
    const ticket = ++generation.current;
    setDataLoading(true);
    try {
      const data = await sf(`/api/product-semantics/product?product=${encodeURIComponent(targetProduct)}`);
      if (ticket !== generation.current) return;
      setSemanticData(data);
      const currentAliases = data.product_aliases?.[0]?.aliases || [];
      setProductAliasList(currentAliases);
    } catch (err) {
      if (ticket === generation.current) setError(err.message || "시맨틱 정보를 불러오지 못했습니다.");
    } finally {
      if (ticket === generation.current) setDataLoading(false);
    }
  }

  useEffect(() => {
    setEditingItem(null);
    setNotice("");
    setError("");
    reloadSemantic(product);
  }, [product]);

  // Handle Product Alias Save
  async function handleSaveProductAliases(updatedAliases) {
    if (!product) return;
    try {
      await post("/product-aliases", { product, aliases: updatedAliases });
      setProductAliasList(updatedAliases);
      setNotice(`[${product}] 제품 별칭을 성공적으로 저장했습니다.`);
      reloadSemantic(product);
      sf("/api/product-semantics/catalog").then(setCatalog).catch(() => {});
    } catch (err) {
      setError(err.message || "제품 별칭 저장 실패");
    }
  }

  function handleAddProductAlias() {
    const val = newProductAliasInput.trim();
    if (!val) return;
    if (productAliasList.includes(val)) {
      setError("이미 등록된 별칭입니다.");
      return;
    }
    const next = [...productAliasList, val];
    setNewProductAliasInput("");
    handleSaveProductAliases(next);
  }

  function handleRemoveProductAlias(aliasToRemove) {
    const next = productAliasList.filter((a) => a !== aliasToRemove);
    handleSaveProductAliases(next);
  }

  // Handle Inline Item Alias Save
  async function handleSaveItemAlias(itemData) {
    if (!product) return;
    try {
      await post("/item-alias", {
        product,
        step_id: itemData.step_id,
        item_id: itemData.item_id,
        module: itemData.module || "",
        step_desc: itemData.step_desc || "",
        item_desc: itemData.item_desc || "",
        aliases: itemData.aliases || [],
      });
      setNotice(`[${itemData.step_id} / ${itemData.item_id}] 별칭 매핑을 저장했습니다.`);
      setEditingItem(null);
      reloadSemantic(product);
    } catch (err) {
      setError(err.message || "아이템 별칭 저장 실패");
    }
  }

  // Handle Direct Add Item
  async function handleDirectAddItem(e) {
    e.preventDefault();
    if (!newItemForm.step_id.trim() || !newItemForm.item_id.trim()) {
      setError("Step ID와 Item ID는 필수입니다.");
      return;
    }
    const aliases = newItemForm.aliases_text
      .split(/[,;\n]/)
      .map((s) => s.trim())
      .filter(Boolean);

    await handleSaveItemAlias({
      step_id: newItemForm.step_id.trim(),
      item_id: newItemForm.item_id.trim(),
      module: newItemForm.module.trim(),
      step_desc: newItemForm.step_desc.trim(),
      item_desc: newItemForm.item_desc.trim(),
      aliases,
    });
    setShowAddItemModal(false);
    setNewItemForm({ module: "", step_id: "", step_desc: "", item_id: "", item_desc: "", aliases_text: "" });
  }

  // Filtered Measurements Rows
  const measurements = semanticData?.measurements || [];
  const modulesList = useMemo(() => {
    return [...new Set(measurements.map((r) => r.module).filter(Boolean))].sort();
  }, [measurements]);

  const filteredItems = useMemo(() => {
    return measurements.filter((row) => {
      // Module filter
      if (moduleFilter !== "all" && row.module !== moduleFilter) return false;
      // Alias filter
      const hasAliases = (row.aliases || []).length > 0;
      if (aliasOnlyFilter === "has_alias" && !hasAliases) return false;
      if (aliasOnlyFilter === "no_alias" && hasAliases) return false;
      // Search query (supports flexible whitespace & delimiters e.g., 'gatecd' matches 'Gate CD')
      if (!searchQuery.trim()) return true;
      const q = searchQuery.trim().toLowerCase();
      const combined = [
        row.module,
        row.step_id,
        row.step_desc,
        row.item_id,
        row.item_desc,
        ...(row.aliases || []),
      ]
        .join(" ")
        .toLowerCase();
      if (combined.includes(q)) return true;
      const normQ = q.replace(/[\s_\-]/g, "");
      const normCombined = combined.replace(/[\s_\-]/g, "");
      return normQ.length >= 2 && normCombined.includes(normQ);
    });
  }, [measurements, moduleFilter, aliasOnlyFilter, searchQuery]);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* 1. 상단: 제품별 관리 컨트롤 바 */}
      <section
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "16px 20px",
          display: "grid",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h3 style={{ margin: "0 0 4px 0", fontSize: 17, fontWeight: 700, display: "flex", alignItems: "center", gap: 8 }}>
              <span>제품별 공정·구조 & 시맨틱 관리</span>
              {product && (
                <span
                  style={{
                    fontSize: 12,
                    background: "var(--accent)",
                    color: "#fff",
                    padding: "2px 8px",
                    borderRadius: 12,
                    fontWeight: 600,
                  }}
                >
                  현재 제품: {product}
                </span>
              )}
            </h3>
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
              선택한 제품의 정규 별칭, Inline Step/Item 항목별 별칭 매핑, 그리고 모듈 및 세부 구조물 단계를 제품별 테이블로 통합 관리합니다.
            </p>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <label htmlFor="product-admin-select" style={{ fontSize: 13, fontWeight: 600 }}>
              관리 대상 제품:
            </label>
            <Select
              id="product-admin-select"
              value={product}
              disabled={loading || !products.length}
              onChange={(e) => setProduct(e.target.value)}
              style={{ minWidth: 160, fontWeight: 600 }}
            >
              <option value="">제품을 선택하세요</option>
              {products.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {error && <Banner tone="danger"><span role="alert">{error}</span></Banner>}
        {notice && <Banner tone="info"><span role="status">{notice}</span></Banner>}

        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <TabStrip active={section} onChange={setSection} items={ADMIN_SECTIONS} />
        </div>
      </section>

      {!product ? (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            color: "var(--text-secondary)",
          }}
        >
          관리할 대상 제품을 상단에서 선택해 주세요.
        </div>
      ) : dataLoading ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>
          제품 [{product}] 시맨틱 데이터 로딩 중…
        </div>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          {/* ══════════════════════════════════════════════════════════════════
              SECTION 1: 제품별 별칭 연결 테이블
             ══════════════════════════════════════════════════════════════════ */}
          {section === "product_aliases" && (
            <div style={{ display: "grid", gap: 16 }}>
              {/* 현재 제품 별칭 연결 카드 */}
              <div
                style={{
                  background: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  padding: 20,
                  display: "grid",
                  gap: 16,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <h4 style={{ margin: "0 0 4px 0", fontSize: 16 }}>
                      제품 [{product}] 별칭 연결 관리
                    </h4>
                    <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
                      홈 Flow-i 질의 또는 검색 시 이 제품을 가리키는 모든 동의어 및 코드명을 연결합니다.
                    </p>
                  </div>
                </div>

                {/* 제품별 별칭 연결 테이블 */}
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: "var(--bg-secondary)", borderBottom: "2px solid var(--border)", textAlign: "left" }}>
                      <th style={{ padding: "10px 14px", width: "180px" }}>기준 제품명</th>
                      <th style={{ padding: "10px 14px" }}>연결된 별칭 목록 (Aliases)</th>
                      <th style={{ padding: "10px 14px", width: "260px" }}>새 별칭 추가</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr style={{ borderBottom: "1px solid var(--border)", verticalAlign: "middle" }}>
                      <td style={{ padding: "14px", fontWeight: 700, fontSize: 14 }}>
                        <code>{product}</code>
                      </td>
                      <td style={{ padding: "14px" }}>
                        {!productAliasList.length ? (
                          <span style={{ color: "var(--text-secondary)", fontStyle: "italic" }}>
                            등록된 별칭이 없습니다. 우측에서 추가하세요.
                          </span>
                        ) : (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                            {productAliasList.map((alias) => (
                              <span
                                key={alias}
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 6,
                                  background: "var(--surface-subtle)",
                                  border: "1px solid var(--border)",
                                  borderRadius: 14,
                                  padding: "3px 10px",
                                  fontSize: 12,
                                  fontWeight: 600,
                                }}
                              >
                                {alias}
                                <button
                                  type="button"
                                  onClick={() => handleRemoveProductAlias(alias)}
                                  title="별칭 삭제"
                                  style={{
                                    border: "none",
                                    background: "transparent",
                                    cursor: "pointer",
                                    color: "var(--text-secondary)",
                                    fontSize: 12,
                                    padding: 0,
                                  }}
                                >
                                  ✕
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: "14px" }}>
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            handleAddProductAlias();
                          }}
                          style={{ display: "flex", gap: 6 }}
                        >
                          <Input
                            placeholder="예: PRODA_DEV"
                            value={newProductAliasInput}
                            onChange={(e) => setNewProductAliasInput(e.target.value)}
                            style={{ flex: 1, minWidth: 120 }}
                          />
                          <Button type="submit" variant="primary" disabled={!newProductAliasInput.trim()}>
                            + 추가
                          </Button>
                        </form>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {/* 전체 제품별 별칭 등록 현황 테이블 */}
              <div
                style={{
                  background: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  padding: 20,
                  display: "grid",
                  gap: 12,
                }}
              >
                <h4 style={{ margin: 0, fontSize: 15 }}>전체 제품별 별칭 연결 현황</h4>
                <div style={{ maxHeight: 300, overflow: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ background: "var(--bg-secondary)", borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                        <th style={{ padding: "8px 12px" }}>제품명</th>
                        <th style={{ padding: "8px 12px" }}>등록된 별칭</th>
                        <th style={{ padding: "8px 12px" }}>최종 갱신</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(catalog?.product_aliases || []).map((row) => (
                        <tr
                          key={row.product}
                          style={{
                            borderBottom: "1px solid var(--border)",
                            background: row.product === product ? "rgba(59, 130, 246, 0.05)" : "transparent",
                          }}
                        >
                          <td style={{ padding: "8px 12px", fontWeight: 600 }}>
                            {row.product} {row.product === product && " (선택됨)"}
                          </td>
                          <td style={{ padding: "8px 12px" }}>
                            {(row.aliases || []).map((a) => (
                              <span
                                key={a}
                                style={{
                                  display: "inline-block",
                                  background: "var(--surface-subtle)",
                                  borderRadius: 4,
                                  padding: "2px 6px",
                                  marginRight: 4,
                                  fontSize: 12,
                                }}
                              >
                                {a}
                              </span>
                            ))}
                          </td>
                          <td style={{ padding: "8px 12px", color: "var(--text-secondary)", fontSize: 12 }}>
                            {row.updated_at ? new Date(row.updated_at).toLocaleDateString() : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              SECTION 2: Inline별 Step / Item & 별칭 매핑 테이블
             ══════════════════════════════════════════════════════════════════ */}
          {section === "inline_items" && (
            <div
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: 20,
                display: "grid",
                gap: 16,
              }}
            >
              {/* 상단 툴바 & 필터 바 */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
                <div>
                  <h4 style={{ margin: "0 0 4px 0", fontSize: 16, fontWeight: 700 }}>
                    Inline 계측 항목별 Step ID · Item ID & 별칭 매핑 테이블
                  </h4>
                  <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
                    기본 Step ID · Item ID는 <code>Inline_matching.csv</code>에서 자동 연동되며, 등록된 별칭(Aliases)은 공백·대소문자·기호 무관하게 질의 및 검색에 유연하게 연결됩니다.
                  </p>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <Button variant="primary" onClick={() => setShowAddItemModal(true)}>
                    + 새 항목·별칭 직접 추가
                  </Button>
                </div>
              </div>

              {/* 검색 및 필터 컨트롤 */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 10,
                  padding: "10px 14px",
                  background: "var(--bg-secondary)",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                }}
              >
                <div style={{ flex: 1, minWidth: 200 }}>
                  <Input
                    placeholder="Step ID, Item ID, 설명, 별칭으로 검색… (공백·구분자 무관)"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>모듈:</span>
                  <Select value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)} style={{ minWidth: 120 }}>
                    <option value="all">전체 모듈</option>
                    {modulesList.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </Select>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>별칭 상태:</span>
                  <Select value={aliasOnlyFilter} onChange={(e) => setAliasOnlyFilter(e.target.value)} style={{ minWidth: 120 }}>
                    <option value="all">전체 항목 ({measurements.length})</option>
                    <option value="has_alias">별칭 등록됨 ({measurements.filter((r) => (r.aliases || []).length > 0).length})</option>
                    <option value="no_alias">별칭 미등록</option>
                  </Select>
                </div>
              </div>

              {/* 체계적인 매핑 테이블 */}
              <div style={{ maxHeight: 520, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, textAlign: "left" }}>
                  <thead style={{ position: "sticky", top: 0, background: "var(--bg-secondary)", zIndex: 1 }}>
                    <tr style={{ borderBottom: "2px solid var(--border)" }}>
                      <th style={{ padding: "10px 12px", width: "110px" }}>모듈</th>
                      <th style={{ padding: "10px 12px", width: "110px" }}>Step ID</th>
                      <th style={{ padding: "10px 12px", width: "150px" }}>공정 설명 (Step Desc)</th>
                      <th style={{ padding: "10px 12px", width: "130px" }}>Item ID</th>
                      <th style={{ padding: "10px 12px", width: "180px" }}>아이템 설명 (Item Desc)</th>
                      <th style={{ padding: "10px 12px" }}>연결된 별칭 (Aliases)</th>
                      <th style={{ padding: "10px 12px", width: "90px", textAlign: "center" }}>관리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {!filteredItems.length ? (
                      <tr>
                        <td colSpan={7} style={{ padding: 30, textAlign: "center", color: "var(--text-secondary)" }}>
                          조건에 일치하는 인라인 측정 항목이 없습니다.
                        </td>
                      </tr>
                    ) : (
                      filteredItems.map((row, idx) => {
                        const isEditing =
                          editingItem?.step_id === row.step_id && editingItem?.item_id === row.item_id;
                        return (
                          <tr
                            key={`${row.step_id}-${row.item_id}-${idx}`}
                            style={{
                              borderBottom: "1px solid var(--border)",
                              background: isEditing
                                ? "rgba(59, 130, 246, 0.08)"
                                : (row.aliases || []).length > 0
                                ? "rgba(16, 185, 129, 0.02)"
                                : "transparent",
                            }}
                          >
                            <td style={{ padding: "10px 12px", fontWeight: 600, color: "var(--text-strong)" }}>
                              {row.module || <span style={{ color: "var(--text-secondary)" }}>—</span>}
                            </td>
                            <td style={{ padding: "10px 12px" }}>
                              <code style={{ fontSize: 12, background: "var(--surface-subtle)", padding: "2px 4px", borderRadius: 3 }}>
                                {row.step_id}
                              </code>
                            </td>
                            <td style={{ padding: "10px 12px", color: "var(--text-strong)" }}>
                              {row.step_desc || <span style={{ color: "var(--text-secondary)" }}>—</span>}
                            </td>
                            <td style={{ padding: "10px 12px" }}>
                              <code style={{ fontSize: 12, background: "var(--surface-subtle)", padding: "2px 4px", borderRadius: 3 }}>
                                {row.item_id || <span style={{ color: "var(--text-secondary)" }}>—</span>}
                              </code>
                            </td>
                            <td style={{ padding: "10px 12px", color: "var(--text-strong)" }}>
                              {row.item_desc || <span style={{ color: "var(--text-secondary)" }}>—</span>}
                            </td>
                            <td style={{ padding: "10px 12px" }}>
                              {isEditing ? (
                                <div style={{ display: "grid", gap: 8 }}>
                                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                    {(editingItem.aliases || []).map((a, i) => (
                                      <span
                                        key={i}
                                        style={{
                                          display: "inline-flex",
                                          alignItems: "center",
                                          gap: 4,
                                          background: "var(--surface-subtle)",
                                          border: "1px solid var(--border)",
                                          padding: "2px 6px",
                                          borderRadius: 4,
                                          fontSize: 11,
                                        }}
                                      >
                                        {a}
                                        <button
                                          type="button"
                                          onClick={() =>
                                            setEditingItem((cur) => ({
                                              ...cur,
                                              aliases: cur.aliases.filter((_, idx2) => idx2 !== i),
                                            }))
                                          }
                                          style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0 }}
                                        >
                                          ✕
                                        </button>
                                      </span>
                                    ))}
                                  </div>
                                  <div style={{ display: "flex", gap: 6 }}>
                                    <Input
                                      placeholder="새 별칭 입력"
                                      value={newAliasInput}
                                      onChange={(e) => setNewAliasInput(e.target.value)}
                                      onKeyDown={(e) => {
                                        if (e.key === "Enter") {
                                          e.preventDefault();
                                          if (newAliasInput.trim()) {
                                            setEditingItem((cur) => ({
                                              ...cur,
                                              aliases: [...new Set([...(cur.aliases || []), newAliasInput.trim()])],
                                            }));
                                            setNewAliasInput("");
                                          }
                                        }
                                      }}
                                      style={{ flex: 1 }}
                                    />
                                    <Button
                                      type="button"
                                      onClick={() => {
                                        if (newAliasInput.trim()) {
                                          setEditingItem((cur) => ({
                                            ...cur,
                                            aliases: [...new Set([...(cur.aliases || []), newAliasInput.trim()])],
                                          }));
                                          setNewAliasInput("");
                                        }
                                      }}
                                    >
                                      추가
                                    </Button>
                                  </div>
                                </div>
                              ) : (
                                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                                  {(row.aliases || []).length === 0 ? (
                                    <span style={{ color: "var(--text-secondary)", fontSize: 12, fontStyle: "italic" }}>
                                      별칭 없음
                                    </span>
                                  ) : (
                                    row.aliases.map((alias, i) => (
                                      <span
                                        key={i}
                                        style={{
                                          display: "inline-block",
                                          background: "rgba(59, 130, 246, 0.12)",
                                          color: "var(--accent)",
                                          border: "1px solid rgba(59, 130, 246, 0.3)",
                                          padding: "2px 8px",
                                          borderRadius: 12,
                                          fontSize: 12,
                                          fontWeight: 600,
                                        }}
                                      >
                                        {alias}
                                      </span>
                                    ))
                                  )}
                                </div>
                              )}
                            </td>
                            <td style={{ padding: "10px 12px", textAlign: "center" }}>
                              {isEditing ? (
                                <div style={{ display: "flex", gap: 4, justifyContent: "center" }}>
                                  <Button
                                    variant="primary"
                                    onClick={() => handleSaveItemAlias(editingItem)}
                                    title="저장"
                                  >
                                    저장
                                  </Button>
                                  <Button onClick={() => setEditingItem(null)} title="취소">
                                    취소
                                  </Button>
                                </div>
                              ) : (
                                <Button
                                  onClick={() => {
                                    setEditingItem({
                                      step_id: row.step_id,
                                      item_id: row.item_id,
                                      module: row.module || "",
                                      step_desc: row.step_desc || "",
                                      item_desc: row.item_desc || "",
                                      aliases: [...(row.aliases || [])],
                                    });
                                    setNewAliasInput("");
                                  }}
                                >
                                  {(row.aliases || []).length ? "수정" : "+ 연결"}
                                </Button>
                              )}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--text-secondary)" }}>
                <span>
                  표시 중: {filteredItems.length}건 / 전체 {measurements.length}건
                </span>
                <span>실제 DB INLINE 및 FAB 측정 파라미터 매핑</span>
              </div>
            </div>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              SECTION 3: 공정 모듈 & 세부 구조물 단계
             ══════════════════════════════════════════════════════════════════ */}
          {section === "structure" && (
            <section
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: 20,
              }}
            >
              <div style={{ marginBottom: 16 }}>
                <h4 style={{ margin: "0 0 4px 0", fontSize: 16, fontWeight: 700 }}>
                  제품 [{product}] 공정 모듈 & 세부 구조물 단계 (Structure Tree & Grid)
                </h4>
                <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
                  모듈(Module)별 하위 구조물 단계(Path)와 Step ID 연결, 공정 전이(Transitions)를 조회하고 편집합니다.
                </p>
              </div>
              <ProductStructure product={product} user={user} entries={[]} />
            </section>
          )}
        </div>
      )}

      {/* 새 항목 직접 추가 모달 */}
      {showAddItemModal && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: 24,
              width: "100%",
              maxWidth: 500,
              display: "grid",
              gap: 14,
            }}
          >
            <h3 style={{ margin: 0, fontSize: 17 }}>새 Inline 항목 및 별칭 등록</h3>
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)" }}>
              제품 [{product}]에 새로운 Step/Item 및 현업 별칭을 직접 등록합니다.
            </p>

            <form onSubmit={handleDirectAddItem} style={{ display: "grid", gap: 10 }}>
              <label style={{ display: "grid", gap: 4, fontSize: 12, fontWeight: 600 }}>
                모듈 (Module)
                <Input
                  placeholder="예: FEOL_Gate"
                  value={newItemForm.module}
                  onChange={(e) => setNewItemForm((f) => ({ ...f, module: e.target.value }))}
                />
              </label>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <label style={{ display: "grid", gap: 4, fontSize: 12, fontWeight: 600 }}>
                  Step ID *
                  <Input
                    required
                    placeholder="예: CC942300"
                    value={newItemForm.step_id}
                    onChange={(e) => setNewItemForm((f) => ({ ...f, step_id: e.target.value }))}
                  />
                </label>
                <label style={{ display: "grid", gap: 4, fontSize: 12, fontWeight: 600 }}>
                  공정 설명 (Step Desc)
                  <Input
                    placeholder="예: GATE_ETCH"
                    value={newItemForm.step_desc}
                    onChange={(e) => setNewItemForm((f) => ({ ...f, step_desc: e.target.value }))}
                  />
                </label>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <label style={{ display: "grid", gap: 4, fontSize: 12, fontWeight: 600 }}>
                  Item ID *
                  <Input
                    required
                    placeholder="예: CD_GATE"
                    value={newItemForm.item_id}
                    onChange={(e) => setNewItemForm((f) => ({ ...f, item_id: e.target.value }))}
                  />
                </label>
                <label style={{ display: "grid", gap: 4, fontSize: 12, fontWeight: 600 }}>
                  아이템 설명 (Item Desc)
                  <Input
                    placeholder="예: Gate Poly CD"
                    value={newItemForm.item_desc}
                    onChange={(e) => setNewItemForm((f) => ({ ...f, item_desc: e.target.value }))}
                  />
                </label>
              </div>

              <label style={{ display: "grid", gap: 4, fontSize: 12, fontWeight: 600 }}>
                연결할 별칭 (쉼표로 구분)
                <Input
                  placeholder="예: PC CD1, 게이트 CD, Poly Width"
                  value={newItemForm.aliases_text}
                  onChange={(e) => setNewItemForm((f) => ({ ...f, aliases_text: e.target.value }))}
                />
              </label>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
                <Button type="button" onClick={() => setShowAddItemModal(false)}>
                  취소
                </Button>
                <Button type="submit" variant="primary">
                  등록 및 저장
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
