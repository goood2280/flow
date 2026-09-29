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
  ["mol_level_count", "epi(S/D) MOL 단 수", 1, 4, 1, 2],
  ["gate_mol_level_count", "게이트 MOL 단 수 (미지정 시 epi와 같음)", 1, 4, 1, null],
  ["contact_epi_recess_nm", "epi MOL이 epi 상면을 깎은 깊이 (nm)", 0, 40, 0.5, 0],
  ["gate_height_above_ns_nm", "NS 최상층 위 게이트 높이 (nm)", 4, 80, 0.5, null],
  ["mol_height_nm", "S/D 위 MOL M0 높이 (nm)", 8, 100, 1, 24],
  ["mol_level_pitch_nm", "MOL 층간 간격 (nm)", 8, 80, 1, 12.8],
  ["gate_to_sd_gap_nm", "게이트–S/D 간격 (nm)", 0, 30, 0.5, 14.2],
  ["sd_width_nm", "S/D 가로 폭 (nm)", 8, 80, 1, 26],
  ["sd_protrusion_nm", "NS 최상층 위 S/D 돌출 (nm)", 0, 80, 1, 22.4],
  ["epi_facet_angle_deg", "Epi 성장면 각도 (°) · 기본 {111}/{001}", 25, 80, 0.1, 54.7356],
  ["sdb_width_nm", "SDB 폭 (nm)", 2, 40, 0.5, 5.2],
  ["sd_doping_log10_cm3", "S/D 대표 도핑 log10(cm⁻³) · 색상만 변화", 17, 21, 0.1, 19],
];
const MATERIAL_MODE_OPTIONS = [["bottom", "아래부터 쌓기"], ["liner", "측벽"], ["u_liner", "U자"], ["fill", "나머지 채움"]];
const MATERIAL_SIZE_OPTIONS = [["", "보통"], ["thin", "얇게"], ["thick", "두껍게"]];
const MATERIAL_REGION_ROLES = { gate: ["gate"], sd_contact: ["contact"], gate_contact: ["contact"] };
const DISPLAY_LABELS = { inline_parameters: "Inline parameter 위치", part_labels: "소구조물 이름", structure_labels: "구조 이름" };
// A single remainder fill must stay last, whatever order the admin typed.
const orderLayers = (layers) => [...layers.filter((layer) => layer.mode !== "fill"), ...layers.filter((layer) => layer.mode === "fill").slice(-1)];
const SHAPABLE = new Set(["source", "drain", "gate", "spacer", "contact", "mol", "beol", "sdb"]);
const CD_FIELDS = [["tcd_nm", "TCD"], ["mcd_nm", "MCD"], ["bcd_nm", "BCD"]];
const PARAM_LABELS = Object.fromEntries([...PARAMS, ...DETAIL_PARAMS].map(([key, label]) => [key, label]));
const PARAM_FALLBACKS = Object.fromEntries(DETAIL_PARAMS.map(([key, , , , , fallback]) => [key, fallback]));
function paramLabel(name) {
  const inner = /^inner_gate(\d)_(width|height)_nm$/.exec(name);
  if (inner) return `Inner gate ${inner[1]} ${inner[2] === "width" ? "폭" : "높이"} (nm)`;
  const sheet = /^ns(\d)_(width|thickness)_nm$/.exec(name);
  if (sheet) return `NS ${sheet[1]}층 ${sheet[2] === "width" ? "폭" : "두께"} (nm)`;
  if (name === "mol_sd_landing_pad") return "MOL 단 사이 네모 패드 (0=바로 연결·기본 · 1=있음)";
  return PARAM_LABELS[name] || name;
}
function layerText(layer) {
  const mode = MATERIAL_MODE_OPTIONS.find(([value]) => value === layer.mode)?.[1] || layer.mode;
  const size = layer.thickness_nm ? `${layer.thickness_nm} nm` : layer.size === "thin" ? "얇게" : layer.size === "thick" ? "두껍게" : "";
  return `${layer.material}(${mode}${size ? `, ${size}` : ""})`;
}
// Structures an LLM edit touches, highlighted in the 3D preview before it is applied.
function rolesForEdit(edit) {
  if (edit.kind === "shape") return [edit.role];
  if (edit.kind === "material") return MATERIAL_REGION_ROLES[edit.region] || [];
  const name = String(edit.name || "");
  if (/^(sheet_|ns\d)/.test(name)) return ["channel", "inner_gate", "highk"];
  if (/^gate_/.test(name)) return ["gate", "inner_gate", "spacer"];
  if (/^inner_gate/.test(name)) return ["inner_gate", "channel"];
  if (/^(mol_|gate_mol_)/.test(name)) return ["mol", "contact"];
  if (name === "contact_epi_recess_nm") return ["contact", "source", "drain"];
  if (/^(sd_|epi_)/.test(name)) return ["source", "drain"];
  if (/^sdb_/.test(name)) return ["sdb"];
  return [];
}
const copy = (value) => JSON.parse(JSON.stringify(value));
const modelPayload = (doc) => ({ variants: doc.variants, products: doc.products, dimensions: doc.dimensions,
  shape_profiles: doc.shape_profiles, material_stacks: doc.material_stacks || {} });
const keyOf = (row) => JSON.stringify([row.module || "", row.step_id, row.item_id]);

// stacked: 조절 패널을 오른쪽 열 대신 전체 폭 3D 아래에 둔다(제품 위키).
export default function StructureModelWorkspace({ admin = false, onNavigate, fixedProduct = "", stacked = false }) {
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(null);
  const [products, setProducts] = useState([]);
  const [product, setProduct] = useState(fixedProduct);
  const [type, setType] = useState("logic");
  const [variant, setVariant] = useState("6T");
  const [view, setView] = useState("gaa");
  const [cameraPreset, setCameraPreset] = useState("isometric");
  const [cutAxis, setCutAxis] = useState("z");
  const [cutPosition, setCutPosition] = useState(50);
  // 3D text overlays stay off until the viewer asks for them.
  const [showLabels, setShowLabels] = useState(false);
  const [showSubLabels, setShowSubLabels] = useState(false);
  const [showInline, setShowInline] = useState(false);
  const [showEdges, setShowEdges] = useState(true);
  const [resetToken, setResetToken] = useState(0);
  const [powerOn, setPowerOn] = useState(false);
  const [injection, setInjection] = useState(false);
  const [tapNear, setTapNear] = useState(true);
  const [guardRing, setGuardRing] = useState(false);
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
  // 앵커 후보가 수천 개인 관리자 장면을 매 렌더 문자열로 만들지 않는다.
  const [sceneJsonOpen, setSceneJsonOpen] = useState(false);

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
  const activeStacks = { ...(draft?.material_stacks?.[profileKey] || {}), ...(override?.material_stacks || {}) };
  // Current model value for a parameter the admin has not set explicitly.
  const currentFallback = (key) => {
    const inner = /^inner_gate(\d)_(width|height)_nm$/.exec(key);
    if (inner) return scene?.nanosheet_dimensions_nm?.inner_gates?.[Number(inner[1]) - 1]?.[`${inner[2]}_nm`] ?? "";
    if (key === "gate_mol_level_count") return activeParams?.mol_level_count ?? 2;
    if (key === "gate_height_above_ns_nm" && scene?.landmarks?.gate_top != null)
      return Math.round((scene.landmarks.gate_top - scene.landmarks.ns_stack_top) * 400) / 10;
    if (key === "mol_sd_landing_pad") return 0;
    return PARAM_FALLBACKS[key] ?? "";
  };
  const proposalOpen = editProposal?.target === `${product}/${type}/${variant}`;
  const previewRoles = useMemo(() => (proposalOpen ? [...new Set([
    ...editProposal.edits.flatMap(rolesForEdit),
    ...Object.keys(editProposal.material_stacks || {}).flatMap((region) => rolesForEdit({ kind: "material", region })),
  ])] : []), [proposalOpen, editProposal]);
  const dirty = Boolean(saved && draft) && JSON.stringify(modelPayload(draft)) !== JSON.stringify(modelPayload(saved));

  useEffect(() => {
    if (!draft) return;
    let live = true;
    const timer = setTimeout(async () => {
      try {
        const data = admin
          ? await sf(`${API}/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...modelPayload(draft), product, type, variant, view }) })
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
    const base = shapes[role] || activeShapes[role] || selectedShape;
    shapes[role] = field === "primitive" && value === "cylinder"
      ? { ...base, primitive: value, tcd_nm: base.mcd_nm, bcd_nm: base.mcd_nm }
      : base.primitive === "cylinder" && CD_FIELDS.some(([name]) => name === field)
        ? { ...base, tcd_nm: value, mcd_nm: value, bcd_nm: value }
        : { ...base, [field]: value };
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
          ...old, shape_profiles: { ...(old.shape_profiles || {}), [role]: { ...activeShapes[role], ...values } },
        } } };
      } else next.shape_profiles = { ...next.shape_profiles,
        [profileKey]: { ...(next.shape_profiles?.[profileKey] || {}), [role]: { ...activeShapes[role], ...values } } };
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
        body: JSON.stringify({ ...modelPayload(draft), product, type, variant, instruction: editPrompt.trim() }) });
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
      const stacks = product ? { ...(old.material_stacks || {}) } : { ...(next.material_stacks?.[key] || {}) };
      for (const [region, layers] of Object.entries(editProposal.material_stacks || {})) {
        if (layers.length) stacks[region] = orderLayers(layers);
        else delete stacks[region];
      }
      for (const edit of editProposal.edits) {
        if (edit.kind === "parameter") params[edit.name] = edit.value;
        else shapes[edit.role] = { ...(shapes[edit.role] || activeShapes[edit.role] || { tcd_nm: 24, mcd_nm: 24, bcd_nm: 24 }),
          [edit.name]: edit.value };
      }
      if (product) next.products[product] = { profiles: { ...profiles, [key]: { ...old, parameters: params, shape_profiles: shapes, material_stacks: stacks } } };
      else {
        next.variants[type][variant] = params; next.shape_profiles[key] = shapes;
        next.material_stacks = { ...(next.material_stacks || {}), [key]: stacks };
      }
      return next;
    });
    const display = editProposal.display || {};
    if ("inline_parameters" in display) setShowInline(Boolean(display.inline_parameters));
    if ("part_labels" in display) setShowSubLabels(Boolean(display.part_labels));
    if ("structure_labels" in display) setShowLabels(Boolean(display.structure_labels));
    setEditProposal(null);
  };
  const setStack = (region, layers) => setDraft((current) => {
    const next = copy(current);
    const ordered = orderLayers(layers);
    if (product) {
      const profiles = next.products[product]?.profiles || {};
      const old = profiles[profileKey] || { parameters: {}, anchors: {}, dimension_anchors: {}, shape_profiles: {} };
      const stacks = { ...(old.material_stacks || {}) };
      if (ordered.length) stacks[region] = ordered; else delete stacks[region];
      next.products[product] = { profiles: { ...profiles, [profileKey]: { ...old, material_stacks: stacks } } };
    } else {
      const stacks = { ...(next.material_stacks?.[profileKey] || {}) };
      if (ordered.length) stacks[region] = ordered; else delete stacks[region];
      next.material_stacks = { ...(next.material_stacks || {}), [profileKey]: stacks };
    }
    return next;
  });
  const updateLayer = (region, index, field, value) => {
    const layers = (activeStacks[region] || []).map((layer, position) => {
      if (position !== index) return layer;
      const updated = { ...layer, [field]: value };
      if (value === "" || value === null || Number.isNaN(value)) delete updated[field];
      return updated;
    });
    setStack(region, layers);
  };
  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await sf(API, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_version: saved.version, ...modelPayload(draft) }) });
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
  const defaultCds = (selectedParts[0]?.profile_widths || [1, 1, 1]).map((ratio) => Math.round(shapeFallback * ratio * 10) / 10);
  const selectedShape = activeShapes[selectedRole] || { tcd_nm: defaultCds[2], mcd_nm: defaultCds[1],
    bcd_nm: defaultCds[0], primitive: selectedParts[0]?.shape || "profile_box" };
  const landmarkEntries = Object.entries(scene?.landmark_labels || {});
  const dimensionRows = Object.entries(scene?.measurements || {});

  return <section className={`gaa-workspace${admin ? " is-admin" : ""}${stacked ? " is-stacked" : ""}`} aria-label="GAA 3D 구조 모델">
    <header className="gaa-heading">
      <div><div className="gaa-eyebrow">DEVICE STRUCTURE</div><h2>GAA 구조 3D</h2>
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
          <option value="gaa">{type === "sram" ? "SRAM 6Tr · 교차 결합 셀" : "GAA 1Tr · FEOL/MOL/BEOL"}</option><option value="latchup">Latch-up · N/P Well</option></select></label>
        {product && onNavigate && <button type="button" onClick={() => onNavigate("productwiki", `?product=${encodeURIComponent(product)}`)}>제품 위키 열기</button>}
      </div>
      <div className="gaa-camera-bar" role="group" aria-label="3D 카메라 보기">
        {[ ["isometric", "3D 회전"], ["top", "탑뷰"], ["front", "정면"], ["side", "측면"], ["cut", "단면"] ].map(([value, label]) =>
          <button type="button" key={value} className={cameraPreset === value ? "is-active" : ""}
            aria-pressed={cameraPreset === value} onClick={() => setCameraPreset(value)}>{label}</button>)}
        <button type="button" onClick={() => setResetToken((value) => value + 1)}>화면 맞춤</button>
        <button type="button" onClick={() => { const el = viewerRef.current; if (document.fullscreenElement) document.exitFullscreen?.(); else el?.requestFullscreen?.(); }}>전체 화면</button>
        <label><input type="checkbox" checked={showLabels} onChange={(event) => setShowLabels(event.target.checked)}/>구조 이름</label>
        <label><input type="checkbox" checked={showSubLabels} onChange={(event) => setShowSubLabels(event.target.checked)}/>소구조물 이름</label>
        <label><input type="checkbox" checked={showInline} onChange={(event) => setShowInline(event.target.checked)}/>Inline parameter</label>
        <label><input type="checkbox" checked={showEdges} onChange={(event) => setShowEdges(event.target.checked)}/>윤곽선</label>
        <button type="button" onClick={() => { setHiddenRoles(["beol", "mol", "bitline", "contact"]); setResetToken((value) => value + 1); }}>소자만 보기</button>
        <button type="button" onClick={() => { setHiddenRoles([]); setResetToken((value) => value + 1); }}>전체 레이어</button>
      </div>
      {cameraPreset === "cut" && <div className="gaa-cut-controls">
        <label>절개 축 <select value={cutAxis} onChange={(event) => setCutAxis(event.target.value)}><option value="x">X</option><option value="y">Y</option><option value="z">Z</option></select></label>
        <label>단면 위치 <input aria-label="단면 위치" type="range" min="0" max="100" value={cutPosition} onChange={(event) => setCutPosition(Number(event.target.value))}/>{cutPosition}%</label>
        <span>선택 축의 양의 방향을 절개합니다. 절개면 채움 없이 내부 표면을 표시합니다.</span>
      </div>}
      {type === "sram" && view === "gaa" && <p className="gaa-dimensions">6Tr = PU pMOS 2개 + PD nMOS 2개 + PG 접근 nMOS 2개 · 연결 확인용 펼친 배치 · HD/HC는 개념 치수 프리셋</p>}
      {view === "gaa" && scene?.nanosheet_dimensions_nm && <p className="gaa-dimensions">
        NS {scene.nanosheet_dimensions_nm.count_per_stack}층 · 폭/두께 {(scene.nanosheet_dimensions_nm.sheets || []).map((sheet) => `${sheet.index}층 ${sheet.width}/${sheet.thickness}`).join(" · ") || "—"} nm · 층간 빈 공간 {(scene.nanosheet_dimensions_nm.pair_gaps || []).join("/") || "—"} nm · 중심 간격 {scene.nanosheet_dimensions_nm.center_pitch} nm · 채널 적층 높이 {scene.nanosheet_dimensions_nm.active_stack_height} nm
        <a href={scene.dimension_reference?.url} target="_blank" rel="noopener noreferrer">치수 근거</a>
      </p>}
      <div className="gaa-main">
        <div className="gaa-viewer" ref={viewerRef}>
          {scene && viewerVisible ? <Suspense fallback={<div className="gaa-loading">3D 엔진 준비 중…</div>}><GaaScene sceneData={scene} hiddenRoles={hiddenRoles} selectedRole={selectedRole} previewRoles={previewRoles} onPick={setSelectedRole} cameraPreset={cameraPreset} latchHighlight={powerOn && injection} cutAxis={cutAxis} cutPosition={cutPosition} showLabels={showLabels} showSubLabels={showSubLabels} showInline={showInline} showEdges={showEdges} resetToken={resetToken} guardRing={guardRing}/></Suspense> : <div className="gaa-loading">{scene ? "3D 모델 보기" : "장면을 만드는 중…"}</div>}
          <span className="gaa-viewer-hint">드래그 회전 · 우클릭 이동 · 휠 확대 · 구조 클릭</span>
        </div>
        <div className="gaa-controls">
          <h3>구조 레이어 · FEOL / MOL / BEOL</h3>
          {view === "gaa" && <div className="gaa-mol-legend" aria-label="MOL 층별 색상">
            <span><i style={{ background: "#eda951" }}/>MOL M0</span>
            {(scene?.mol_level_count || 0) >= 2 && <span><i style={{ background: "#50b7d9" }}/>MOL M1 / V1</span>}
            {(scene?.mol_level_count || 0) >= 3 && <span><i style={{ background: "#ab8ce0" }}/>MOL M2 / V2</span>}
            {Math.max(scene?.mol_level_count || 0, scene?.gate_mol_level_count || 0) >= 4 && <span><i style={{ background: "#e07a9a" }}/>MOL M3 / V3</span>}
            {scene?.mol_connection === "direct" && <span>epi MOL 직결 (패드 없음)</span>}
          </div>}
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
              <p>관리자 기본지식과 선택 제품 위키를 참고해 치수 변경안을 만듭니다. 제안 적용 후 3D를 확인하고 모델 저장을 눌러야 확정됩니다.</p>
              <textarea rows={3} maxLength={1200} value={editPrompt} onChange={(event) => setEditPrompt(event.target.value)}
                placeholder={"예: epi MOL 3단으로 해줘 · epi MOL 네모 없애고 바로 연결해줘\nGATE는 밑에서부터 D,C,E로 채워줘 G,H는 U자로 채워넣고 I는 나머지에 채워줘\ninner gate 1,2,3 각각 width 30,28,26 nm height 12,10,8 nm · Inline parameter 위치 표시해줘"}/>
              <button type="button" disabled={editing || !editPrompt.trim()} onClick={suggestEdits}>{editing ? "제안 중…" : "구조 변경안 만들기"}</button>
              {proposalOpen && <div className="gaa-llm-proposal">
                <b>{editProposal.summary || "치수 변경 제안"}</b>
                <span className="gaa-llm-hint">{editProposal.source === "rule" ? "규칙으로 해석 · LLM 호출 없음" : editProposal.source === "llm" ? "연결된 LLM 제안" : "규칙 해석 + 연결된 LLM 제안"}</span>
                {(editProposal.notes || []).map((note) => <span className="gaa-llm-hint" key={note}>{note}</span>)}
                <table className="gaa-llm-diff">
                  <thead><tr><th>항목</th><th>현재</th><th>제안</th></tr></thead>
                  <tbody>
                    {editProposal.edits.map((edit, index) => {
                      const current = edit.kind === "shape"
                        ? activeShapes[edit.role]?.[edit.name]
                        : activeParams?.[edit.name] ?? currentFallback(edit.name);
                      return <tr key={index}>
                        <td>{edit.kind === "shape" ? `${scene?.roles?.[edit.role] || edit.role} ${edit.name.replace("_nm", "").toUpperCase()}` : paramLabel(edit.name)}</td>
                        <td>{current ?? "-"}</td>
                        <td className="is-proposed">{edit.value}</td>
                      </tr>;
                    })}
                    {Object.entries(editProposal.material_stacks || {}).map(([region, layers]) => <tr key={region}>
                      <td>{scene?.material_regions?.[region] || region} 재료</td>
                      <td>{(activeStacks[region] || []).map(layerText).join(" → ") || "-"}</td>
                      <td className="is-proposed">{layers.map(layerText).join(" → ") || "제거"}</td></tr>)}
                    {Object.entries(editProposal.display || {}).map(([key, value]) => <tr key={key}>
                      <td>{DISPLAY_LABELS[key] || key}</td><td>-</td><td className="is-proposed">{value ? "3D에 표시" : "숨김"}</td></tr>)}
                    <tr><td>NS 적층 높이 (nm)</td><td>{editProposal.before.sheet_dimensions_nm.active_stack_height}</td>
                      <td className="is-proposed">{editProposal.after.sheet_dimensions_nm.active_stack_height}</td></tr>
                  </tbody>
                </table>
                <span className="gaa-llm-hint">3D에서 주황색으로 강조된 구조물이 바뀝니다.</span>
                <div className="gaa-llm-actions">
                  <button type="button" className="gaa-primary" onClick={applyEdits}>3D 미리보기에 적용</button>
                  <button type="button" onClick={() => setEditProposal(null)}>제안 닫기</button>
                </div>
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
              {["width", "height"].map((field) => {
                const key = `inner_gate${index}_${field}_nm`;
                return <label key={key}>Inner gate {index} {field === "width" ? "폭" : "높이"} (nm)
                  <input type="number" min="2" max={field === "width" ? "90" : "40"} step="0.5"
                    value={activeParams[key] ?? currentFallback(key)} onChange={(event) => updateParam(key, Number(event.target.value))}/></label>;
              })}
            </div>)}
            {DETAIL_PARAMS.map(([key, label, min, max, step]) => <label key={key}><span>{label} <b>{activeParams[key] ?? currentFallback(key)}</b></span>
              <input type="range" min={min} max={max} step={step} value={activeParams[key] ?? currentFallback(key)}
                onChange={(event) => updateParam(key, Number(event.target.value))}/></label>)}
            <label className="gaa-inline-check"><input type="checkbox" checked={(activeParams.mol_sd_landing_pad ?? 0) === 0}
              onChange={(event) => updateParam("mol_sd_landing_pad", event.target.checked ? 0 : 1)}/>MOL 단 사이 네모 패드 없이 바로 연결 (epi·게이트)</label>
            <div className="gaa-material-editor"><h3>재료 채움 (바깥층부터 순서대로)</h3>
              <p>아래부터 쌓기 → 측벽/U자 → 나머지 채움 순으로 영역 안쪽을 채웁니다. 두께를 비우면 영역 크기에 맞춰 자동으로 정합니다.</p>
              {Object.entries(scene?.material_regions || {}).map(([region, regionLabel]) => {
                const layers = activeStacks[region] || [];
                const resolved = scene?.material_layers?.[region] || [];
                return <div className="gaa-material-region" key={region}>
                  <div className="gaa-material-head"><b>{regionLabel}</b>
                    <button type="button" onClick={() => setStack(region, [...layers, { material: `M${layers.length + 1}`, mode: layers.length ? "fill" : "bottom" }])}>층 추가</button>
                    {!!layers.length && <button type="button" onClick={() => setStack(region, [])}>비우기</button>}
                  </div>
                  {layers.map((layer, index) => <div className="gaa-material-row" key={index}>
                    <input aria-label={`${regionLabel} ${index + 1}층 재료`} value={layer.material} maxLength={40}
                      onChange={(event) => updateLayer(region, index, "material", event.target.value)}/>
                    <select aria-label="채움 방식" value={layer.mode} onChange={(event) => updateLayer(region, index, "mode", event.target.value)}>
                      {MATERIAL_MODE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                    <select aria-label="두께 정도" value={layer.size || ""} disabled={layer.mode === "fill"} onChange={(event) => updateLayer(region, index, "size", event.target.value)}>
                      {MATERIAL_SIZE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                    <input aria-label="두께 nm" type="number" min="0.2" max="40" step="0.1" placeholder={resolved[index]?.thickness_nm ? `자동 ${resolved[index].thickness_nm}` : "nm"}
                      disabled={layer.mode === "fill"} value={layer.thickness_nm ?? ""}
                      onChange={(event) => updateLayer(region, index, "thickness_nm", event.target.value === "" ? "" : Number(event.target.value))}/>
                    <button type="button" aria-label="층 삭제" onClick={() => setStack(region, layers.filter((_, position) => position !== index))}>삭제</button>
                  </div>)}
                </div>;
              })}
            </div>
            {SHAPABLE.has(selectedRole) && <div className="gaa-shape-editor"><h3>{scene?.roles?.[selectedRole]} 단면 CD</h3>
              <p>TCD·MCD·BCD는 선택한 소구조물의 상·중·하단 가로 폭입니다. 단위는 nm입니다.</p>
              <label>3D 기본 형상<select value={selectedShape.primitive || selectedParts[0]?.shape || "profile_box"}
                onChange={(event) => updateShape(selectedRole, "primitive", event.target.value)}>
                <option value="tapered_cylinder">테이퍼 원형 기둥</option>
                <option value="cylinder">원기둥</option>
                <option value="profile_box">사각 단면</option>
                <option value="faceted_epi">결정면 Epi · {'{111}'} 성장면</option>
                <option value="gate_shell">시트를 감싸는 게이트 / 스페이서</option>
              </select></label>
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
      {!!scene?.references?.length && <details className="gaa-references"><summary>TEM · 구조 참고 자료와 모델 범위</summary>
        <p>공개 단면과 구조 문헌을 참고한 조절 가능한 개념 모델입니다. Epi 성장면과 식각 각도는 공정·결정 방향에 따라 달라지며, 특정 제품의 TEM을 재구성한 모델은 아닙니다.</p>
        {scene.references.map((reference) => <p key={reference.url}><a href={reference.url} target="_blank" rel="noopener noreferrer">{reference.title}</a>{reference.note && <> · {reference.note}</>}</p>)}
      </details>}
      {type === "sram" && view === "gaa" && !!scene?.netlist?.length && <details className="gaa-references"><summary>6Tr 회로 연결 · Q/QB · WL · BL/BLB</summary>
        <p>Q는 반대 인버터의 게이트를, QB는 첫 번째 인버터의 게이트를 구동합니다. WL은 두 접근 소자를 동시에 제어합니다. 배선 높이는 연결을 구분하기 위한 개념 층입니다.</p>
        <table className="gaa-netlist"><thead><tr><th>소자</th><th>종류</th><th>Gate</th><th>Source</th><th>Drain</th><th>Body</th></tr></thead>
          <tbody>{scene.netlist.map((device) => <tr key={device.id}><th>{device.id}</th><td>{device.type}</td>{["G", "S", "D", "B"].map((terminal) => <td key={terminal}>{device.terminals[terminal]}</td>)}</tr>)}</tbody></table>
      </details>}
      {view === "latchup" && <section className="gaa-latchup" aria-label="Latch-up turn-on 개념 시나리오">
        <div><h3>Latch-up turn-on 경로</h3><p>P+ 주입 접합과 N-Well·P-Well의 기생 PNP/NPN 결합을 표시합니다. 실제 발화 여부는 공정별 저항, 전류 이득, 온도와 바이어스 검증이 필요합니다.</p></div>
        <div className="gaa-latch-controls">
          <label><input type="checkbox" checked={powerOn} onChange={(event) => setPowerOn(event.target.checked)}/>전원 ON</label>
          <label><input type="checkbox" checked={injection} onChange={(event) => setInjection(event.target.checked)}/>전류 주입 사건 표시</label>
          <label><input type="checkbox" checked={tapNear} onChange={(event) => setTapNear(event.target.checked)}/>Well / substrate tap 연결 양호</label>
          <label><input type="checkbox" checked={guardRing} onChange={(event) => setGuardRing(event.target.checked)}/>Guard ring 적용 가정</label>
        </div>
        <strong className={powerOn && injection ? "is-active" : ""}>{powerOn && injection
          ? "주입 상태: 가능한 기생 PNPN 경로를 붉게 강조" : powerOn ? "전원 ON: 주입 사건이 없어 경로는 비활성 표시" : "전원 OFF: 기생 경로 비활성 표시"}</strong>
        <div className="gaa-hotspots"><b>검토할 위치</b>
          {(scene?.latchup_path?.hotspots || []).map((hotspot) => <p key={hotspot.id}><span>{hotspot.label}</span> · {hotspot.reason}</p>)}
          <p>{!tapNear ? "Tap 연결이 약한 조건: well·기판의 전압 강하 경로를 우선 검토하세요." : "Tap 연결 양호 조건: 실제 접촉 저항과 배치를 확인하세요."}</p>
          <p>{guardRing ? "Guard ring 적용 가정: 주입 캐리어 수집 경로를 확인하세요." : "Guard ring이 없는 조건: 주입원 주변 보호 구조를 검토하세요."}</p>
        </div>
        <small>이 화면은 정성적 경로 시각화입니다. 발화 전류·holding 전류·불량 확률을 계산하지 않습니다. <a href={scene?.latchup_path?.reference} target="_blank" rel="noopener noreferrer">기생 SCR 구조 근거</a></small>
      </section>}
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
      <details className="gaa-data" onToggle={(event) => setSceneJsonOpen(event.currentTarget.open)}><summary>장면 데이터 보기 · LLM/코드용 JSON</summary><p>각 도형의 역할, 좌표, 크기와 제품별 inline 앵커를 문자 데이터로 확인할 수 있습니다.</p>{sceneJsonOpen && <pre>{JSON.stringify(scene, null, 2)}</pre>}</details>
    </>}
  </section>;
}
