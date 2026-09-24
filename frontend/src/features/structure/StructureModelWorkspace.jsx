import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { sf, qs } from "../../lib/api";
import "./StructureModelWorkspace.css";

const GaaScene = lazy(() => import("./GaaScene"));
const API = "/api/structure-model";
const VARIANTS = { logic: ["5T", "6T", "7.5T", "9T"], sram: ["HD", "HC"] };
const PARAMS = [
  ["cell_height", "셀 외곽 깊이 (개념 단위)", 1, 4, 0.05],
  ["gate_width", "게이트 폭", 0.45, 2.2, 0.05],
  ["sheet_width", "나노시트 폭 (40 nm/단위)", 0.5, 2, 0.05],
  ["sheet_spacing", "시트 중심 간격 (40 nm/단위)", 0.18, 0.65, 0.005],
  ["sheet_count", "시트 수", 1, 5, 1],
];
const DETAIL_PARAMS = [
  ["mol_level_count", "MOL 배선 층 수", 1, 3, 1, 2],
  ["mol_height_nm", "S/D 위 MOL M0 높이 (nm)", 8, 100, 1, 24],
  ["mol_level_pitch_nm", "MOL 층간 간격 (nm)", 8, 80, 1, 12.8],
  ["gate_to_sd_gap_nm", "게이트–S/D 간격 (nm)", 0, 30, 0.5, 14.2],
  ["sd_width_nm", "S/D 가로 폭 (nm)", 8, 80, 1, 26],
  ["sd_protrusion_nm", "NS 최상층 위 S/D 돌출 (nm)", 0, 80, 1, 22.4],
  ["sdb_width_nm", "SDB 폭 (nm)", 2, 40, 0.5, 5.2],
  ["sd_doping_log10_cm3", "S/D 대표 도핑 log10(cm⁻³) · 색상만 변화", 17, 21, 0.1, 19],
];
const SHAPABLE = new Set(["source", "drain", "gate", "spacer", "contact", "mol", "beol", "sdb"]);
const CD_FIELDS = [["tcd_nm", "TCD"], ["mcd_nm", "MCD"], ["bcd_nm", "BCD"]];
const copy = (value) => JSON.parse(JSON.stringify(value));
const keyOf = (row) => JSON.stringify([row.module || "", row.step_id, row.item_id]);

export default function StructureModelWorkspace({ admin = false, onNavigate, fixedProduct = "" }) {
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(null);
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState(fixedProduct);
  const [type, setType] = useState("logic");
  const [variant, setVariant] = useState("6T");
  const [view, setView] = useState("gaa");
  const [scene, setScene] = useState(null);
  const [selectedRole, setSelectedRole] = useState("inner_gate");
  const [hiddenRoles, setHiddenRoles] = useState([]);
  const [anchorFilter, setAnchorFilter] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [shapePrompt, setShapePrompt] = useState("");
  const [shapeSuggestion, setShapeSuggestion] = useState(null);
  const [suggesting, setSuggesting] = useState(false);
  const [editPrompt, setEditPrompt] = useState("");
  const [editProposal, setEditProposal] = useState(null);
  const [editing, setEditing] = useState(false);
  const viewerRef = useRef(null);
  const [viewerVisible, setViewerVisible] = useState(false);

  useEffect(() => {
    const node = viewerRef.current;
    if (!node || viewerVisible) return;
    if (!window.IntersectionObserver) { setViewerVisible(true); return; }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setViewerVisible(true);
    }, { rootMargin: "160px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [draft, viewerVisible]);

  const load = async (askBeforeDiscard = false) => {
    if (askBeforeDiscard && dirty && !window.confirm("저장하지 않은 3D 모델 변경을 버리고 다시 불러올까요?")) return;
    setError("");
    try {
      const [model, catalog] = await Promise.all([sf(API), sf(`${API}/products`)]);
      setSaved(model);
      setDraft(copy(model));
      setProducts(catalog.products || []);
    } catch (err) { setError(err.message || "3D 모델을 불러오지 못했습니다."); }
  };
  useEffect(() => { load(); }, []);

  const profileKey = `${type}/${variant}`;
  const override = product ? draft?.products?.[product]?.profiles?.[profileKey] : null;
  const activeParams = useMemo(() => {
    const preset = draft?.variants?.[type]?.[variant];
    if (!preset) return null;
    return { ...preset, ...(override?.parameters || {}) };
  }, [draft, product, type, variant]);
  const activeShapes = { ...(draft?.shape_profiles?.[profileKey] || {}), ...(override?.shape_profiles || {}) };
  const dirty = Boolean(saved && draft) && JSON.stringify({ variants: draft.variants, products: draft.products,
    dimensions: draft.dimensions, shape_profiles: draft.shape_profiles })
    !== JSON.stringify({ variants: saved.variants, products: saved.products,
      dimensions: saved.dimensions, shape_profiles: saved.shape_profiles });

  useEffect(() => {
    if (!draft) return;
    let live = true;
    const timer = setTimeout(async () => {
      try {
        const data = admin
          ? await sf(`${API}/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ variants: draft.variants, products: draft.products, dimensions: draft.dimensions, shape_profiles: draft.shape_profiles, product, type, variant, view }) })
          : await sf(`${API}/scene${qs({ product, type, variant, view })}`);
        if (live) { setScene(data); setError(""); }
      } catch (err) { if (live) setError(err.message || "3D 모델을 그리지 못했습니다."); }
    }, 120);
    return () => { live = false; clearTimeout(timer); };
  }, [draft, product, type, variant, view, admin]);

  const selectProduct = (name) => {
    setScene(null);
    setProduct(name);
    setShapeSuggestion(null);
    setEditProposal(null);
    setAnchorFilter("");
  };
  useEffect(() => { if (fixedProduct && draft) selectProduct(fixedProduct); }, [fixedProduct, draft?.version]);
  const selectType = (next) => {
    setScene(null);
    setShapeSuggestion(null);
    setEditProposal(null);
    setType(next);
    setVariant(next === "logic" ? "6T" : "HD");
  };
  const updateParam = (key, value) => {
    setDraft((current) => {
      const next = copy(current);
      if (product) {
        const profiles = next.products[product]?.profiles || {};
        const old = profiles[profileKey] || { parameters: {}, anchors: {}, dimension_anchors: {}, shape_profiles: {} };
        next.products[product] = { profiles: { ...profiles, [profileKey]: { ...old, parameters: { ...old.parameters, [key]: value } } } };
      } else next.variants[type][variant][key] = value;
      return next;
    });
  };
  const setAnchor = (value) => {
    if (!product) return;
    setDraft((current) => {
      const next = copy(current);
      const profiles = next.products[product]?.profiles || {};
      const old = profiles[profileKey] || { parameters: {}, anchors: {}, dimension_anchors: {}, shape_profiles: {} };
      const anchors = { ...old.anchors };
      if (value) {
        const [module, step_id, item_id] = JSON.parse(value);
        anchors[selectedRole] = { module, step_id, item_id };
      }
      else delete anchors[selectedRole];
      if (!Object.keys(anchors).length && !Object.keys(old.parameters).length
          && !Object.keys(old.dimension_anchors || {}).length && !Object.keys(old.shape_profiles || {}).length) {
        delete profiles[profileKey];
        if (!Object.keys(profiles).length) delete next.products[product];
      } else next.products[product] = { profiles: { ...profiles, [profileKey]: { ...old, anchors } } };
      return next;
    });
  };
  const updateDimension = (dimensionId, field, value) => setDraft((current) => {
    const next = copy(current);
    next.dimensions[dimensionId] = { ...next.dimensions[dimensionId], [field]: value };
    return next;
  });
  const addDimension = () => setDraft((current) => {
    const next = copy(current);
    const id = `dimension_${Date.now()}`;
    next.dimensions[id] = { label: "새 측정 구간", from: "ns_stack_top", to: "mol_m0_bottom" };
    return next;
  });
  const removeDimension = (dimensionId) => setDraft((current) => {
    const next = copy(current);
    delete next.dimensions[dimensionId];
    Object.values(next.products).forEach((entry) => Object.values(entry.profiles).forEach((profile) => {
      if (profile.dimension_anchors) delete profile.dimension_anchors[dimensionId];
    }));
    return next;
  });
  const setDimensionAnchor = (dimensionId, value) => {
    if (!product) return;
    setDraft((current) => {
      const next = copy(current);
      const profiles = next.products[product]?.profiles || {};
      const old = profiles[profileKey] || { parameters: {}, anchors: {}, dimension_anchors: {}, shape_profiles: {} };
      const dimension_anchors = { ...(old.dimension_anchors || {}) };
      if (value) {
        const [module, step_id, item_id] = JSON.parse(value);
        dimension_anchors[dimensionId] = { module, step_id, item_id };
      } else delete dimension_anchors[dimensionId];
      next.products[product] = { profiles: { ...profiles, [profileKey]: { ...old, dimension_anchors } } };
      return next;
    });
  };
  const updateShape = (role, field, value) => setDraft((current) => {
    const next = copy(current);
    const profiles = product ? (next.products[product]?.profiles || {}) : (next.shape_profiles || {});
    const old = product ? (profiles[profileKey] || { parameters: {}, anchors: {}, dimension_anchors: {}, shape_profiles: {} }) : null;
    const shapes = product ? { ...(old.shape_profiles || {}) } : { ...(profiles[profileKey] || {}) };
    const base = shapes[role] || activeShapes[role] || { tcd_nm: 24, mcd_nm: 24, bcd_nm: 24 };
    shapes[role] = { ...base, [field]: value };
    if (product) next.products[product] = { profiles: { ...profiles, [profileKey]: { ...old, shape_profiles: shapes } } };
    else next.shape_profiles = { ...next.shape_profiles, [profileKey]: shapes };
    return next;
  });
  const clearShape = (role) => setDraft((current) => {
    const next = copy(current);
    if (product) {
      const profile = next.products[product]?.profiles?.[profileKey];
      if (profile?.shape_profiles) delete profile.shape_profiles[role];
    } else if (next.shape_profiles?.[profileKey]) delete next.shape_profiles[profileKey][role];
    return next;
  });
  const applyShape = (role, values) => {
    setDraft((current) => {
      const next = copy(current);
      if (product) {
        const profiles = next.products[product]?.profiles || {};
        const old = profiles[profileKey] || { parameters: {}, anchors: {}, dimension_anchors: {}, shape_profiles: {} };
        next.products[product] = { profiles: { ...profiles, [profileKey]: {
          ...old, shape_profiles: { ...(old.shape_profiles || {}), [role]: values },
        } } };
      } else next.shape_profiles = { ...next.shape_profiles,
        [profileKey]: { ...(next.shape_profiles?.[profileKey] || {}), [role]: values } };
      return next;
    });
    setShapeSuggestion(null);
  };
  const suggestShape = async () => {
    if (!shapePrompt.trim() || suggesting) return;
    setSuggesting(true); setError(""); setShapeSuggestion(null);
    try {
      const result = await sf(`${API}/suggest-shape`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: selectedRole, current: selectedShape, instruction: shapePrompt.trim() }) });
      setShapeSuggestion({ role: selectedRole, values: result.shape_profile });
    } catch (err) { setError(err.message || "LLM 형상 제안을 받지 못했습니다."); }
    finally { setSuggesting(false); }
  };
  const suggestEdits = async () => {
    if (!editPrompt.trim() || editing) return;
    setEditing(true); setError(""); setEditProposal(null);
    try {
      const result = await sf(`${API}/suggest-edits`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variants: draft.variants, products: draft.products, dimensions: draft.dimensions,
          shape_profiles: draft.shape_profiles, product, type, variant, instruction: editPrompt.trim() }) });
      setEditProposal({ ...result, target: `${product}/${type}/${variant}` });
    } catch (err) { setError(err.message || "LLM 구조 변경안을 받지 못했습니다."); }
    finally { setEditing(false); }
  };
  const applyEdits = () => {
    if (editProposal?.target !== `${product}/${type}/${variant}`) return;
    setDraft((current) => {
      const next = copy(current);
      const key = `${type}/${variant}`;
      const profiles = product ? (next.products[product]?.profiles || {}) : null;
      const old = product ? (profiles[key] || { parameters: {}, anchors: {}, dimension_anchors: {}, shape_profiles: {} }) : null;
      const params = product ? { ...old.parameters } : { ...next.variants[type][variant] };
      const shapes = product ? { ...old.shape_profiles } : { ...(next.shape_profiles[key] || {}) };
      for (const edit of editProposal.edits) {
        if (edit.kind === "parameter") params[edit.name] = edit.value;
        else shapes[edit.role] = { ...(shapes[edit.role] || activeShapes[edit.role] || { tcd_nm: 24, mcd_nm: 24, bcd_nm: 24 }),
          [edit.name]: edit.value };
      }
      if (product) next.products[product] = { profiles: { ...profiles, [key]: { ...old, parameters: params, shape_profiles: shapes } } };
      else { next.variants[type][variant] = params; next.shape_profiles[key] = shapes; }
      return next;
    });
    setEditProposal(null);
  };
  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await sf(API, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_version: saved.version, variants: draft.variants, products: draft.products,
          dimensions: draft.dimensions, shape_profiles: draft.shape_profiles }) });
      setSaved(result); setDraft(copy(result));
      setNotice(`3D 모델 v${result.version}을 저장했습니다. 홈 화면에도 반영됩니다.`);
    } catch (err) { setError(err.message || "3D 모델을 저장하지 못했습니다."); }
    finally { setSaving(false); }
  };
  const resetProduct = () => {
    if (!product) return;
    setDraft((current) => {
      const next = copy(current);
      if (next.products[product]?.profiles) {
        delete next.products[product].profiles[profileKey];
        if (!Object.keys(next.products[product].profiles).length) delete next.products[product];
      }
      return next;
    });
  };
  const candidates = scene?.anchor_candidates || [];
  const selectedAnchor = override?.anchors?.[selectedRole];
  const filtered = candidates.filter((row) => {
    const text = `${row.module} ${row.step_id} ${row.item_id} ${row.step_desc} ${row.item_desc}`.toLowerCase();
    return !anchorFilter || text.includes(anchorFilter.toLowerCase());
  }).slice(0, 80);
  const chosenKey = selectedAnchor ? keyOf(selectedAnchor) : "";
  if (chosenKey && !filtered.some((row) => keyOf(row) === chosenKey)) {
    filtered.unshift({ ...selectedAnchor, item_desc: "현재 지정값" });
  }
  const roles = Object.entries(scene?.roles || {}).filter(([role]) => scene?.parts?.some((part) => part.role === role));
  const selectedParts = scene?.parts?.filter((part) => part.role === selectedRole) || [];
  const shapeFallback = Math.round((selectedParts[0]?.size?.[0] || 0.6) * 40 * 10) / 10;
  const selectedShape = activeShapes[selectedRole] || { tcd_nm: shapeFallback, mcd_nm: shapeFallback, bcd_nm: shapeFallback };
  const landmarkEntries = Object.entries(scene?.landmark_labels || {});
  const dimensionRows = Object.entries(scene?.measurements || {});

  return <section className={`gaa-workspace${admin ? " is-admin" : ""}`} aria-label="GAA 3D 구조 모델">
    <header className="gaa-heading">
      <div><div className="gaa-eyebrow">GAA STRUCTURE · CODE-READABLE 3D</div><h2>GAA 구조 3D</h2>
        <p>X: 소스→드레인, Y: 높이, Z: 셀 높이 방향 · 나노시트 치수는 공개 연구 사례 기준, 나머지 형상은 개념도</p></div>
      {admin && <div className="gaa-actions"><button type="button" onClick={() => load(true)} disabled={saving}>새로고침</button><button type="button" className="gaa-primary" onClick={save} disabled={!dirty || saving}>{saving ? "저장 중…" : "모델 저장"}</button></div>}
    </header>
    {error && <div className="gaa-message is-error" role="alert">{error}</div>}
    {notice && <div className="gaa-message" role="status">{notice}</div>}
    {!draft ? <div className="gaa-loading">모델을 불러오는 중…</div> : <>
      <div className="gaa-toolbar">
        {!fixedProduct && <label>제품<select value={product} onChange={(event) => selectProduct(event.target.value)}><option value="">공통 개념 모델</option>{products.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>}
        <label>타입<select value={type} onChange={(event) => selectType(event.target.value)}><option value="logic">Logic</option><option value="sram">SRAM</option></select></label>
        <label>{type === "logic" ? "Cell height" : "SRAM 타입"}<select value={variant} onChange={(event) => { setScene(null); setEditProposal(null); setVariant(event.target.value); }}>{VARIANTS[type].map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
        <label>보기<select value={view} onChange={(event) => { const next = event.target.value; setScene(null); setView(next); setSelectedRole(next === "latchup" ? "nwell" : "inner_gate"); }}>
          <option value="gaa">GAA 1Tr · FEOL/MOL/BEOL</option><option value="latchup">Latch-up · N/P Well</option></select></label>
        {product && onNavigate && <button type="button" onClick={() => onNavigate("productwiki", `?product=${encodeURIComponent(product)}`)}>제품 Wiki 열기</button>}
      </div>
      {view === "gaa" && scene?.nanosheet_dimensions_nm && <p className="gaa-dimensions">
        NS {scene.nanosheet_dimensions_nm.count_per_stack}층 · 폭/두께 {(scene.nanosheet_dimensions_nm.sheets || []).map((sheet) => `${sheet.index}층 ${sheet.width}/${sheet.thickness}`).join(" · ") || "—"} nm · 층간 빈 공간 {(scene.nanosheet_dimensions_nm.pair_gaps || []).join("/") || "—"} nm · 중심 간격 {scene.nanosheet_dimensions_nm.center_pitch} nm · 채널 적층 높이 {scene.nanosheet_dimensions_nm.active_stack_height} nm
        <a href={scene.dimension_reference?.url} target="_blank" rel="noopener noreferrer">치수 근거</a>
      </p>}
      <div className="gaa-main">
        <div className="gaa-viewer" ref={viewerRef}>
          {scene && viewerVisible ? <Suspense fallback={<div className="gaa-loading">3D 엔진 준비 중…</div>}><GaaScene sceneData={scene} hiddenRoles={hiddenRoles} selectedRole={selectedRole} onPick={setSelectedRole}/></Suspense> : <div className="gaa-loading">{scene ? "3D 모델 보기" : "장면을 만드는 중…"}</div>}
          <span className="gaa-viewer-hint">드래그 회전 · 휠 확대 · 구조 클릭</span>
        </div>
        <div className="gaa-controls">
          <h3>구조 레이어 · FEOL / MOL / BEOL</h3>
          <div className="gaa-roles">{roles.map(([role, label]) => <div className={`gaa-role${selectedRole === role ? " is-selected" : ""}`} key={role}>
            <label><input type="checkbox" checked={!hiddenRoles.includes(role)} onChange={(event) => setHiddenRoles((list) => event.target.checked ? list.filter((item) => item !== role) : [...list, role])}/><span>{label}</span></label>
            <button type="button" onClick={() => setSelectedRole(role)}>선택</button>
            {scene?.anchors?.[role] && <small className={scene.anchors[role].status === "missing" ? "is-missing" : ""}>{scene.anchors[role].status === "matched" ? "앵커 연결" : "앵커 불일치"}</small>}
          </div>)}</div>
          <div className="gaa-selected"><b>{scene?.roles?.[selectedRole] || selectedRole}</b><span>{selectedParts.length}개 도형 · {selectedParts.map((part) => part.name).join(", ") || "이 타입에는 표시되지 않음"}</span>
            {product && <span>{scene?.anchors?.[selectedRole]
              ? `${scene.anchors[selectedRole].module ? `${scene.anchors[selectedRole].module} · ` : ""}${scene.anchors[selectedRole].step_id} / ${scene.anchors[selectedRole].item_id} · ${scene.anchors[selectedRole].status === "matched" ? "현재 제품 매칭 확인" : "현재 제품 매칭 없음"}`
              : "연결된 INLINE Step/Item 없음"}</span>}
          </div>
          {admin && view === "gaa" && activeParams && <div className="gaa-parameter-editor"><h3>{product ? `${product} 개별 형상` : `${type.toUpperCase()} ${variant} 공통 형상`}</h3>
            <div className="gaa-llm-editor"><h3>연결된 LLM으로 구조 수정 제안</h3>
              <p>관리자 기본지식과 선택 제품 Wiki를 참고해 치수 변경안을 만듭니다. 제안 적용 후 3D를 확인하고 모델 저장을 눌러야 확정됩니다.</p>
              <textarea rows={3} maxLength={1200} value={editPrompt} onChange={(event) => setEditPrompt(event.target.value)}
                placeholder="예: NS 2층 폭 34 nm, 시트 4층, MOL 3단, 소스 TCD 18 nm"/>
              <button type="button" disabled={editing || !editPrompt.trim()} onClick={suggestEdits}>{editing ? "제안 중…" : "구조 변경안 만들기"}</button>
              {editProposal?.target === `${product}/${type}/${variant}` && <div className="gaa-llm-proposal">
                <b>{editProposal.summary || "치수 변경 제안"}</b>
                {editProposal.edits.map((edit, index) => <span key={index}>{edit.kind === "shape" ? `${edit.role}.${edit.name}` : edit.name}: {edit.value}</span>)}
                <span>NS 적층 높이 {editProposal.before.sheet_dimensions_nm.active_stack_height} → {editProposal.after.sheet_dimensions_nm.active_stack_height} nm</span>
                <button type="button" onClick={applyEdits}>3D 미리보기에 적용</button>
              </div>}
            </div>
            {PARAMS.map(([key, label, min, max, step]) => <label key={key}><span>{label} <b>{activeParams[key]}</b></span><input type="range" min={min} max={max} step={step} value={activeParams[key]} onChange={(event) => updateParam(key, Number(event.target.value))}/></label>)}
            <h3>제품별 MTS 형상</h3>
            {Array.from({ length: activeParams.sheet_count }, (_, index) => index + 1).map((index) => <div className="gaa-sheet-fields" key={index}>
              <b>NS {index}층</b>
              {["width", "thickness"].map((field) => {
                const key = `ns${index}_${field}_nm`;
                const fallback = field === "width" ? Math.round(activeParams.sheet_width * 40 * 10) / 10 : 5;
                return <label key={key}>{field === "width" ? "가로 폭" : "두께"} (nm)
                  <input type="number" min="2" max={field === "width" ? "80" : "15"} step="0.5"
                    value={activeParams[key] ?? fallback} onChange={(event) => updateParam(key, Number(event.target.value))}/></label>;
              })}
            </div>)}
            {DETAIL_PARAMS.map(([key, label, min, max, step, fallback]) => <label key={key}><span>{label} <b>{activeParams[key] ?? fallback}</b></span>
              <input type="range" min={min} max={max} step={step} value={activeParams[key] ?? fallback}
                onChange={(event) => updateParam(key, Number(event.target.value))}/></label>)}
            {SHAPABLE.has(selectedRole) && <div className="gaa-shape-editor"><h3>{scene?.roles?.[selectedRole]} 단면 CD</h3>
              <p>TCD·MCD·BCD는 선택한 소구조물의 상·중·하단 가로 폭입니다. 단위는 nm입니다.</p>
              <div>{CD_FIELDS.map(([field, label]) => <label key={field}>{label}
                <input type="number" min="2" max="120" step="0.5" value={selectedShape[field]}
                  onChange={(event) => updateShape(selectedRole, field, Number(event.target.value))}/></label>)}</div>
              <button type="button" onClick={() => clearShape(selectedRole)}>이 구조물의 CD 재설정</button>
              <label>LLM으로 단면 제안
                <input type="text" maxLength={1000} value={shapePrompt} onChange={(event) => setShapePrompt(event.target.value)}
                  placeholder="예: 상단은 좁고 중간은 넓은 테이퍼"/></label>
              <button type="button" disabled={suggesting || !shapePrompt.trim()} onClick={suggestShape}>
                {suggesting ? "제안 중…" : "CD 형상 제안"}</button>
              {shapeSuggestion?.role === selectedRole && <div className="gaa-shape-suggestion">
                제안 TCD {shapeSuggestion.values.tcd_nm} / MCD {shapeSuggestion.values.mcd_nm} / BCD {shapeSuggestion.values.bcd_nm} nm
                <button type="button" onClick={() => applyShape(selectedRole, shapeSuggestion.values)}>미리보기 적용</button>
              </div>}
            </div>}
            {product && <button type="button" onClick={resetProduct}>이 제품·변형의 설정 지우기</button>}
          </div>}
        </div>
      </div>
      {view === "latchup" && <div className="gaa-message">Latch-up 단면: P+ PMOS → N-Well → P-Well/P형 기판 → N+ NMOS의 기생 PNP/NPN 결합 경로를 표시합니다. Well/탭 구조는 개념도이며 trigger 전류는 계산하지 않습니다.</div>}
      {view === "gaa" && <section className="gaa-measurements" aria-label="구조 높이와 Inline 측정 구간">
        <div className="gaa-measurements-heading"><div><h3>구조 높이 · INLINE 측정 구간</h3>
          <p>공통 지식에서 기준면을 정의하고, 제품별 Step ID / Item ID를 따로 연결합니다. 표시 높이는 현재 개념 형상에서 계산합니다.</p></div>
          {admin && !product && <button type="button" onClick={addDimension}>측정 구간 추가</button>}
        </div>
        {dimensionRows.map(([id, row]) => {
          const assigned = override?.dimension_anchors?.[id];
          const assignedKey = assigned ? keyOf(assigned) : "";
          const options = [...filtered];
          if (assignedKey && !options.some((item) => keyOf(item) === assignedKey)) options.unshift({ ...assigned, item_desc: "현재 지정값" });
          return <div className="gaa-measurement-row" key={id}>
            {admin && !product ? <>
              <input aria-label={`${id} 이름`} value={draft.dimensions[id]?.label || ""} maxLength={100}
                onChange={(event) => updateDimension(id, "label", event.target.value)}/>
              <select aria-label={`${id} 시작점`} value={draft.dimensions[id]?.from} onChange={(event) => updateDimension(id, "from", event.target.value)}>
                {landmarkEntries.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <select aria-label={`${id} 끝점`} value={draft.dimensions[id]?.to} onChange={(event) => updateDimension(id, "to", event.target.value)}>
                {landmarkEntries.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <button type="button" onClick={() => removeDimension(id)}>삭제</button>
            </> : <span><b>{row.label}</b> · {row.from_label} → {row.to_label}</span>}
            <strong>{row.height_nm} nm</strong>
            {product && (admin ? <select aria-label={`${row.label} Inline 앵커`} value={assignedKey}
              onChange={(event) => setDimensionAnchor(id, event.target.value)}>
              <option value="">Step/Item 미연결</option>{options.map((candidate) => <option key={keyOf(candidate)} value={keyOf(candidate)}>
                {candidate.module ? `${candidate.module} · ` : ""}{candidate.step_id} / {candidate.item_id}</option>)}</select>
              : <span>{row.anchor ? `${row.anchor.step_id} / ${row.anchor.item_id} · ${row.anchor.status === "matched" ? "연결 확인" : "매칭 없음"}` : "Step/Item 미연결"}</span>)}
          </div>;
        })}
      </section>}
      {product && <div className="gaa-anchor-panel"><div><h3>제품별 INLINE 앵커</h3><p>현재 제품의 Inline_matching.csv에 있는 module · step_id · item_id 조합만 연결 상태로 표시합니다. 다른 제품의 동일한 이름도 자동 재사용하지 않습니다.</p></div>
        {admin && <div className="gaa-anchor-edit"><label>구조물 <select value={selectedRole} onChange={(event) => setSelectedRole(event.target.value)}>{roles.map(([role, label]) => <option key={role} value={role}>{label}</option>)}</select></label>
          <label>Step/Item 검색 <input value={anchorFilter} onChange={(event) => setAnchorFilter(event.target.value)} placeholder="step_id, item_id, 설명"/></label>
          <label>연결 <select value={chosenKey} onChange={(event) => setAnchor(event.target.value)}><option value="">연결 없음</option>{filtered.map((row) => <option key={keyOf(row)} value={keyOf(row)}>{row.module ? `${row.module} · ` : ""}{row.step_id} / {row.item_id} {row.item_desc ? `· ${row.item_desc}` : ""}</option>)}</select></label>
        </div>}
        <div className="gaa-anchor-summary">현재 제품 INLINE 후보 {scene?.anchor_candidate_count || 0}개 · 연결 {Object.keys(scene?.anchors || {}).length}개
          {scene?.warnings?.map((warning) => <span key={warning} className="is-missing">{warning}</span>)}
          {!scene?.anchor_candidate_count && <span>매칭 행이 없어 구조는 개념 형상으로 표시됩니다. 제품별 앵커를 연결하려면 INLINE 기준정보를 확인하세요.</span>}
        </div>
      </div>}
      <details className="gaa-data"><summary>장면 데이터 보기 · LLM/코드용 JSON</summary><p>각 도형의 역할, 좌표, 크기와 제품별 inline 앵커를 문자 데이터로 확인할 수 있습니다.</p><pre>{JSON.stringify(scene, null, 2)}</pre></details>
    </>}
  </section>;
}
