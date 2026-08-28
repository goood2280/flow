import { Fragment, Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import Modal from "../../components/Modal";
import Loading from "../../components/Loading";
import { PageGearButton } from "../../components/PageGear";
import ProductOrderEditor from "../../components/ProductOrderEditor";
import { toast } from "../../components/Toast";
import { Button, Input, PageHeader, PageShell, Pill, Select, Toolbar } from "../../components/ui";
import { sf } from "../../lib/api";
import { useUserRole } from "../../lib/permissions";
import { orderProductItems } from "../../lib/productOrder";

const LazySplitTable = lazy(() => import("../splittable/My_SplitTable"));

const API = "/api/lot-management";
const SPLIT_API = "/api/splittable";
const PALETTE = [
  "#ffffff", "#f3f4f6", "#d1d5db", "#fecaca", "#fed7aa",
  "#fef3c7", "#d9f99d", "#bbf7d0", "#99f6e4", "#a5f3fc",
  "#bfdbfe", "#c7d2fe", "#ddd6fe", "#e9d5ff", "#f5d0fe",
  "#fbcfe8", "#fee2e2", "#ffedd5", "#ecfccb", "#e0f2fe",
];
const DEFAULT_COLUMNS = [
  { id: "purpose", label: "purpose" },
  { id: "lot_id", label: "lot_id" },
  { id: "current_step_id", label: "현step_id" },
  { id: "step_desc", label: "step_desc" },
  { id: "qty", label: "Qty" },
  { id: "comment", label: "comment" },
];
const COMPUTED_COLUMNS = new Set(["current_step_id", "step_desc", "qty"]);
const COLORABLE_COLUMNS = new Set(["purpose", "lot_id"]);
const TABLE_LOAD_TIMEOUT_MS = 20_000;
const LOT_CANDIDATE_PREVIEW_LIMIT = 300;
const LOT_CANDIDATE_SEARCH_LIMIT = 500;
const buttonStyle = {padding:"6px 11px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:13,fontWeight:700,cursor:"pointer"};
const uid = prefix => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,8)}`;
const clone = value => JSON.parse(JSON.stringify(value));
const changeTypeLabel = type => ({column_added:"열 추가",column_removed:"열 삭제",column_renamed:"열 이름 변경",row_added:"행 추가",row_removed:"행 삭제",cell_changed:"셀 변경",color_changed:"색상 변경"}[type] || type);
const formatQty = value => Number(value) > 0 ? String(Number(value)) : "-";

function LotIdEditor({ value, candidates, onChange, onCommit, onPaste, onSearch }) {
  const [open, setOpen] = useState(false);
  const query = String(value || "").trim().toLowerCase();
  const filtered = useMemo(() => {
    const rows = query ? candidates.filter(lot => lot.toLowerCase().includes(query)) : candidates;
    return rows.slice(0, 500);
  }, [candidates, query]);
  return <div className="lot-management__lot-editor">
    <Input className="lot-management__cell-input" value={value || ""} onChange={e => {onChange(e.target.value);onSearch?.(e.target.value);setOpen(true);}} onFocus={() => {onSearch?.(value);setOpen(true);}} onBlur={() => {onCommit(value);setTimeout(() => setOpen(false), 120);}} onPaste={onPaste} autoComplete="off" aria-label="LOT ID"/>
    {open&&<div className="lot-management__lot-options">
      {filtered.length?filtered.map(lot => <button type="button" className="lot-management__lot-option" key={lot} onMouseDown={e => {e.preventDefault();onChange(lot);onCommit(lot);setOpen(false);}} title={lot}>{lot}</button>):<div className="lot-management__lot-empty">일치하는 LOT_ID가 없습니다.</div>}
    </div>}
  </div>;
}

function normalizeTable(raw, product) {
  const sourceColumns = Array.isArray(raw?.columns) ? raw.columns : [];
  const requiredIds = new Set(DEFAULT_COLUMNS.map(column => column.id));
  const columns = [...clone(DEFAULT_COLUMNS), ...sourceColumns.filter(column => column?.id && !requiredIds.has(column.id))];
  return {
    product,
    version: Number(raw?.version || 0),
    columns,
    rows: Array.isArray(raw?.rows) ? raw.rows.map(row => ({
      ...row,
      values:{...(row?.values || {}), qty:formatQty(row?.values?.qty)},
    })) : [],
    colors: raw?.colors && typeof raw.colors === "object" ? raw.colors : {},
    updated_at: raw?.updated_at || "",
    updated_by: raw?.updated_by || "",
    note: raw?.note || "",
  };
}

function withStatuses(rawTable, statuses) {
  if (!rawTable || !statuses || typeof statuses !== "object") return rawTable;
  return {...rawTable, rows:(rawTable.rows || []).map(row => {
    const values = row?.values && typeof row.values === "object" ? row.values : {};
    const lotId = String(values.lot_id || "").trim().toUpperCase();
    if (!lotId) return row;
    const status = statuses[lotId];
    if (!status) return row;
    return {...row, values:{...values, current_step_id:status.current_step_id || "", step_desc:status.step_desc || "", qty:formatQty(status.qty)}};
  })};
}

export default function My_LotManagement({ user }) {
  const role = useUserRole(user);
  const canManage = role.canManagePage("lotmanage");
  const [products, setProducts] = useState([]);
  const [productOrder, setProductOrder] = useState([]);
  const [productOrderBusy, setProductOrderBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [product, setProduct] = useState("");
  const [table, setTable] = useState(null);
  const [draft, setDraft] = useState(null);
  const [editing, setEditing] = useState(false);
  const [productsLoading, setProductsLoading] = useState(true);
  const [productsError, setProductsError] = useState("");
  const [productsReloadToken, setProductsReloadToken] = useState(0);
  const [loading, setLoading] = useState(false);
  const [statusLoading, setStatusLoading] = useState(false);
  const [tableError, setTableError] = useState("");
  const [tableReloadToken, setTableReloadToken] = useState(0);
  const [saving, setSaving] = useState(false);
  const [versions, setVersions] = useState([]);
  const [showVersions, setShowVersions] = useState(false);
  const [versionDiffs, setVersionDiffs] = useState({});
  const [openVersion, setOpenVersion] = useState(null);
  const [diffLoading, setDiffLoading] = useState(null);
  const [purposeSearch, setPurposeSearch] = useState("");
  const [viewLot, setViewLot] = useState("");
  const [customs, setCustoms] = useState([]);
  const [selectedCustom, setSelectedCustom] = useState("");
  const [showCustom, setShowCustom] = useState(false);
  const [schema, setSchema] = useState([]);
  const [customName, setCustomName] = useState("");
  const [customColumns, setCustomColumns] = useState([]);
  const [customSearch, setCustomSearch] = useState("");
  const [lotCandidates, setLotCandidates] = useState([]);
  const [lotCandidatePreview, setLotCandidatePreview] = useState([]);
  const [colorPicker, setColorPicker] = useState(null);
  const splitViewRef = useRef(null);
  const candidateSearchTimerRef = useRef(null);
  const candidateSearchControllerRef = useRef(null);
  const candidateQueryRef = useRef("");

  useEffect(() => {
    if (!viewLot) return;
    splitViewRef.current?.scrollIntoView({behavior:"smooth", block:"start"});
  }, [viewLot]);

  useEffect(() => {
    if (!colorPicker) return;
    const close = () => setColorPicker(null);
    const closeOnKey = event => { if (event.key === "Escape") close(); };
    window.addEventListener("mousedown", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", closeOnKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", closeOnKey);
    };
  }, [colorPicker]);

  useEffect(() => () => {
    window.clearTimeout(candidateSearchTimerRef.current);
    candidateSearchControllerRef.current?.abort();
  }, []);

  const loadCustoms = () => sf(`${SPLIT_API}/customs`).then(d => setCustoms(Array.isArray(d.customs) ? d.customs : [])).catch(() => setCustoms([]));
  useEffect(() => {
    let active = true;
    setProductsLoading(true);
    setProductsError("");
    sf(`${SPLIT_API}/products`).then(p => {
      if (!active) return;
      const order = Array.isArray(p.product_order) ? p.product_order : [];
      const list = orderProductItems((Array.isArray(p.products) ? p.products : [])
        .map(item => typeof item === "string" ? {name:item} : item)
        .filter(item => item?.name), order, item => item.name);
      setProductOrder(order);
      setProducts(list);
      setProduct(current => list.some(item => item.name === current) ? current : (list[0]?.name || ""));
    }).catch(e => {
      if (!active) return;
      const message = `제품 목록을 불러오지 못했습니다: ${e.message}`;
      setProducts([]);
      setProduct("");
      setProductsError(message);
      toast.error(message);
    }).finally(() => { if (active) setProductsLoading(false); });
    sf(`${SPLIT_API}/customs`)
      .then(c => { if (active) setCustoms(Array.isArray(c.customs) ? c.customs : []); })
      .catch(() => { if (active) setCustoms([]); });
    return () => { active = false; };
  }, [productsReloadToken]);

  useEffect(() => {
    setVersionDiffs({});
    setOpenVersion(null);
    setEditing(false);
    setColorPicker(null);
    setPurposeSearch("");
    setViewLot("");
    setTableError("");
    setLotCandidates([]);
    setLotCandidatePreview([]);
    setStatusLoading(false);
    candidateQueryRef.current = "";
    window.clearTimeout(candidateSearchTimerRef.current);
    candidateSearchControllerRef.current?.abort();
    if (!product) {
      setLoading(false);
      setTable(null);
      setDraft(null);
      return undefined;
    }

    let active = true;
    let timedOut = false;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, TABLE_LOAD_TIMEOUT_MS);
    setLoading(true);
    setTable(null);
    setDraft(null);

    sf(`${API}/table?product=${encodeURIComponent(product)}&include_status=false`, {signal:controller.signal})
      .then(data => {
        if (!active) return;
        const next = normalizeTable(data, product);
        setTable(next);
        setDraft(clone(next));
        const lotIds = [...new Set(next.rows.map(row => String(row.values?.lot_id || "").trim()).filter(Boolean))];
        if (lotIds.length) {
          setStatusLoading(true);
          sf(`${API}/statuses`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product, lot_ids:lotIds}), signal:controller.signal})
            .then(result => {
              if (!active) return;
              const statuses = result?.statuses || {};
              setTable(current => current?.product === product ? withStatuses(current, statuses) : current);
              setDraft(current => current?.product === product ? withStatuses(current, statuses) : current);
            })
            .catch(() => {})
            .finally(() => { if (active) setStatusLoading(false); });
        }
      })
      .catch(e => {
        if (!active) return;
        const message = timedOut
          ? "랏 관리 표 조회가 20초를 초과했습니다. 잠시 후 다시 시도해 주세요."
          : `랏 관리 표를 불러오지 못했습니다: ${e.message}`;
        setTableError(message);
        toast.error(message);
      })
      .finally(() => {
        window.clearTimeout(timeoutId);
        if (active) setLoading(false);
      });

    sf(`${SPLIT_API}/lot-candidates?product=${encodeURIComponent(product)}&col=fab_lot_id&limit=${LOT_CANDIDATE_PREVIEW_LIMIT}`, {signal:controller.signal})
      .then(d => {
        if (!active) return;
        const candidates = [...new Set((d.candidates || []).map(v => String(v || "").trim()).filter(Boolean))];
        setLotCandidatePreview(candidates);
        if (!candidateQueryRef.current) setLotCandidates(candidates);
      })
      .catch(() => { if (active && !controller.signal.aborted) setLotCandidates([]); });

    return () => {
      active = false;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [product, tableReloadToken]);

  const searchLotCandidates = rawQuery => {
    const query = String(rawQuery || "").trim();
    candidateQueryRef.current = query;
    window.clearTimeout(candidateSearchTimerRef.current);
    candidateSearchControllerRef.current?.abort();
    if (!query) {
      setLotCandidates(lotCandidatePreview);
      return;
    }
    const selectedProduct = product;
    candidateSearchTimerRef.current = window.setTimeout(() => {
      const controller = new AbortController();
      candidateSearchControllerRef.current = controller;
      sf(`${SPLIT_API}/lot-candidates?product=${encodeURIComponent(selectedProduct)}&col=fab_lot_id&prefix=${encodeURIComponent(query)}&limit=${LOT_CANDIDATE_SEARCH_LIMIT}`, {signal:controller.signal})
        .then(d => {
          if (candidateQueryRef.current !== query || selectedProduct !== product) return;
          setLotCandidates([...new Set((d.candidates || []).map(value => String(value || "").trim()).filter(Boolean))]);
        })
        .catch(() => {});
    }, 180);
  };

  const loadVersions = () => {
    if (!product) return;
    sf(`${API}/versions?product=${encodeURIComponent(product)}`)
      .then(d => { setVersions(d.versions || []); setShowVersions(true); })
      .catch(e => toast.error(`버전 이력을 불러오지 못했습니다: ${e.message}`));
  };
  const toggleVersionChanges = version => {
    if (openVersion === version) { setOpenVersion(null); return; }
    setOpenVersion(version);
    if (versionDiffs[version]) return;
    setDiffLoading(version);
    sf(`${API}/versions/${version}/diff?product=${encodeURIComponent(product)}`)
      .then(d => setVersionDiffs(cur => ({...cur, [version]:d})))
      .catch(e => toast.error(`변경항목을 불러오지 못했습니다: ${e.message}`))
      .finally(() => setDiffLoading(null));
  };

  const updateCell = (rowId, columnId, value) => setDraft(cur => ({...cur, rows:cur.rows.map(row => row.id === rowId ? {...row, values:{...row.values, [columnId]:value}} : row)}));
  const updateLotId = (rowId, value) => setDraft(cur => ({...cur, rows:cur.rows.map(row => row.id === rowId ? {...row, values:{...row.values, lot_id:value, current_step_id:"", step_desc:"", qty:"-"}} : row)}));
  const refreshRowStatus = (rowId, rawLotId) => {
    const lotId = String(rawLotId || "").trim();
    if (!lotId) return updateLotId(rowId, "");
    sf(`${API}/lot-status?product=${encodeURIComponent(product)}&lot_id=${encodeURIComponent(lotId)}`)
      .then(status => setDraft(cur => ({...cur, rows:cur.rows.map(row => {
        if (row.id !== rowId || String(row.values?.lot_id || "").trim().toUpperCase() !== lotId.toUpperCase()) return row;
        return {...row, values:{...row.values, current_step_id:status.current_step_id || "", step_desc:status.step_desc || "", qty:formatQty(status.qty)}};
      })})))
      .catch(e => toast.error(`LOT 현재 정보를 불러오지 못했습니다: ${e.message}`));
  };
  const pasteBlock = (event, originRowId, originColumnId) => {
    const text = event.clipboardData?.getData("text/plain") ?? "";
    if (!text) return;
    const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    const grid = lines.map(line => line.split("\t"));
    if (grid.length <= 1 && grid[0].length <= 1) return;
    event.preventDefault();
    setDraft(cur => {
      const matchesPurpose = row => {
        const purpose = String(row.values?.purpose || "").trim();
        const query = purposeSearch.trim().toLowerCase();
        return !query || purpose.toLowerCase().includes(query);
      };
      const targetRows = cur.rows.filter(matchesPurpose);
      const rowIndex = targetRows.findIndex(r => r.id === originRowId);
      const pasteColumns = cur.columns.filter(column => !COMPUTED_COLUMNS.has(column.id));
      const colIndex = pasteColumns.findIndex(c => c.id === originColumnId);
      if (rowIndex === -1 || colIndex === -1) return cur;
      const rows = cur.rows.slice();
      const needed = rowIndex + grid.length - targetRows.length;
      for (let i = 0; i < needed; i++) {
        const values = Object.fromEntries(cur.columns.map(c => [c.id, ""]));
        const row = {id:uid("row"), values};
        rows.push(row);
        targetRows.push(row);
      }
      grid.forEach((lineCells, ri) => {
        const targetRow = targetRows[rowIndex + ri];
        const targetIndex = rows.findIndex(r => r.id === targetRow.id);
        const values = {...targetRow.values};
        lineCells.forEach((cellValue, ci) => {
          const column = pasteColumns[colIndex + ci];
          if (column) values[column.id] = cellValue;
        });
        rows[targetIndex] = {...targetRow, values};
      });
      return {...cur, rows};
    });
  };
  const addRow = () => setDraft(cur => {
    const values = Object.fromEntries(cur.columns.map(c => [c.id, ""]));
    return {...cur, rows:[...cur.rows, {id:uid("row"), values}]};
  });
  const deleteRow = rowId => setDraft(cur => ({...cur, rows:cur.rows.filter(r => r.id !== rowId), colors:Object.fromEntries(Object.entries(cur.colors).filter(([key]) => !key.startsWith(`${rowId}:`)))}));
  const addColumn = () => {
    const label = window.prompt("추가할 열 이름을 입력하세요.", "new_column")?.trim();
    if (!label) return;
    const id = uid("col");
    setDraft(cur => ({...cur, columns:[...cur.columns, {id, label}], rows:cur.rows.map(r => ({...r, values:{...r.values, [id]:""}}))}));
  };
  const renameColumn = column => {
    if (DEFAULT_COLUMNS.some(item => item.id === column.id)) return;
    const label = window.prompt("열 이름을 수정하세요.", column.label)?.trim();
    if (label) setDraft(cur => ({...cur, columns:cur.columns.map(c => c.id === column.id ? {...c, label} : c)}));
  };
  const deleteColumn = column => {
    if (DEFAULT_COLUMNS.some(item => item.id === column.id)) return toast.warn("기본 열은 삭제할 수 없습니다.");
    if (!window.confirm(`'${column.label}' 열을 삭제할까요?`)) return;
    setDraft(cur => ({...cur, columns:cur.columns.filter(c => c.id !== column.id), rows:cur.rows.map(r => { const values={...r.values}; delete values[column.id]; return {...r, values}; }), colors:Object.fromEntries(Object.entries(cur.colors).filter(([key]) => !key.endsWith(`:${column.id}`)))}));
  };
  const openCellColorPicker = (event, rowId, columnId) => {
    if (!editing || !COLORABLE_COLUMNS.has(columnId)) return;
    event.preventDefault();
    event.stopPropagation();
    const paletteWidth = 190;
    const paletteHeight = 142;
    setColorPicker({
      rowId,
      columnId,
      left:Math.max(8, Math.min(event.clientX, window.innerWidth - paletteWidth - 8)),
      top:Math.max(8, Math.min(event.clientY, window.innerHeight - paletteHeight - 8)),
    });
  };
  const setCellColor = color => {
    if (!colorPicker) return;
    const {rowId, columnId} = colorPicker;
    const key = `${rowId}:${columnId}`;
    setDraft(cur => ({...cur, colors:{...cur.colors, [key]:color}}));
    setColorPicker(null);
  };

  const save = () => {
    const note = window.prompt("변경 사유를 입력하세요.", "LOT table edit");
    if (note === null) return;
    setSaving(true);
    sf(`${API}/table/save`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product, columns:draft.columns, rows:draft.rows, colors:draft.colors, expected_version:table.version, note})})
      .then(d => { const next=normalizeTable(d.table, product); setTable(next); setDraft(clone(next)); setEditing(false); toast.ok(`v${next.version}으로 저장했습니다.`); })
      .catch(e => toast.error(`저장하지 못했습니다: ${e.message}`))
      .finally(() => setSaving(false));
  };

  const rollback = version => {
    if (!window.confirm(`v${version} 상태로 롤백할까요? 현재 상태도 버전 이력에 남습니다.`)) return;
    sf(`${API}/rollback`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product, version, expected_version:table.version})})
      .then(d => { const next=normalizeTable(d.table, product); setTable(next); setDraft(clone(next)); setEditing(false); setShowVersions(false); toast.ok(`v${version} 상태를 새 v${next.version}으로 복원했습니다.`); })
      .catch(e => toast.error(`롤백하지 못했습니다: ${e.message}`));
  };

  const openCustomEditor = () => {
    setShowCustom(true);
    sf(`${SPLIT_API}/schema?product=${encodeURIComponent(product)}`).then(d => setSchema((d.columns || []).map(c => c.name || c).filter(Boolean))).catch(() => setSchema([]));
    const existing = customs.find(c => c.name === selectedCustom);
    setCustomName(existing?.name || "");
    setCustomColumns(existing?.columns || []);
  };
  const saveCustom = () => {
    const name = customName.trim();
    if (!name || !customColumns.length) return toast.warn("세트명과 컬럼을 선택해 주세요.");
    const existing = customs.find(c => c.name === name);
    sf(`${SPLIT_API}/customs/save`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({name, columns:customColumns, username:user?.username || "", expected_version:existing ? (existing.version || 1) : 0})})
      .then(d => { if (d?.conflict) throw new Error("다른 사용자가 먼저 수정했습니다. 다시 열어 주세요."); setSelectedCustom(name); setShowCustom(false); return loadCustoms(); })
      .then(() => toast.ok(`CUSTOM SET '${name}'을 저장했습니다.`))
      .catch(e => toast.error(`CUSTOM SET 저장 실패: ${e.message}`));
  };
  const saveProductOrder = (next) => {
    setProductOrderBusy(true);
    return sf(`${API}/product-order`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product_order:next})})
      .then(result => {
        const saved = result.product_order || next;
        setProductOrder(saved);
        setProducts(current => orderProductItems(current, saved, item => item.name));
        toast.ok("제품 선택 순서를 저장했습니다.");
      })
      .catch(error => toast.error(`제품 순서 저장 실패: ${error.message || error}`))
      .finally(() => setProductOrderBusy(false));
  };
  const customPool = useMemo(() => schema.filter(c => c.toLowerCase().includes(customSearch.trim().toLowerCase())), [schema, customSearch]);
  const work = editing ? draft : table;
  const visibleRows = useMemo(() => {
    const rows = work?.rows || [];
    const query = purposeSearch.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter(row => String(row.values?.purpose || "").trim().toLowerCase().includes(query));
  }, [work, purposeSearch]);
  const tableColumnCount = 1 + (work?.columns?.length || 0) + (work?.columns?.some(column => column.id === "lot_id") ? 1 : 0) + (editing ? 1 : 0);

  return <PageShell layout="explorer" className="lot-management">
    <aside className="flow-layout-sidebar">
      <div className="flow-layout-sidebar__header">
        <span className="flow-layout-sidebar__title">제품</span>
        <span className="lot-management__product-count">{products.length}개</span>
      </div>
      <div className="flow-layout-sidebar__body">
        {products.map(item => <button type="button" className="ds-sidebar-item" key={item.name} onClick={() => setProduct(item.name)} aria-current={product===item.name?"page":undefined}>{String(item.name || "").replace(/^ML_TABLE_/, "")}</button>)}
      </div>
    </aside>
    <main className="flow-layout-main">
      <PageHeader
        title="랏 관리"
        subtitle={product ? `${String(product).replace(/^ML_TABLE_/, "")} 제품의 LOT 배정과 진행 상태를 관리합니다.` : "제품을 선택해 주세요."}
        status={editing ? <Pill tone="warn">편집 중</Pill> : <Pill tone="neutral">조회</Pill>}
      />
      <Toolbar>
        <Input className="lot-management__toolbar-search" type="search" value={purposeSearch} onChange={e => setPurposeSearch(e.target.value)} placeholder="purpose 검색 (예: CS)" aria-label="purpose 검색" title="purpose에 입력한 문자가 포함된 LOT만 표시" autoComplete="off" disabled={!work}/>
        <span className="u-muted">{visibleRows.length}건</span>
        {statusLoading&&<span className="u-muted">LOT 현황 갱신 중…</span>}
        <Select className="lot-management__toolbar-select" value={selectedCustom} onChange={e => setSelectedCustom(e.target.value)} title="LOT View에 적용할 SplitTable CUSTOM SET" disabled={!product}>
          <option value="">CUSTOM SET 선택</option>{customs.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </Select>
        <Button variant="secondary" onClick={openCustomEditor} disabled={!product}>CUSTOM SET 편집</Button>
        <span className="lot-management__version">{work?.version ? `v${work.version} · ${work.updated_by || "-"}` : "아직 저장되지 않음"}</span>
        <Button variant="ghost" onClick={loadVersions} disabled={!product}>버전 기록</Button>
        {editing ? <>
          <Button variant="primary" disabled={saving} onClick={save}>{saving ? "저장 중…" : "저장"}</Button>
          <Button variant="ghost" onClick={() => {setDraft(clone(table));setEditing(false);}}>취소</Button>
        </> : <Button variant="primary" disabled={!table || loading} onClick={() => {setDraft(clone(table));setEditing(true);}}>편집</Button>}
      </Toolbar>
      {productsLoading ? <Loading text="제품 목록 로딩..."/>
        : productsError ? <div className="ds-feedback"><div className="ds-feedback__inner"><div className="ds-feedback__title">제품 목록을 불러오지 못했습니다</div><div className="ds-feedback__message">{productsError}</div><Button variant="secondary" onClick={() => setProductsReloadToken(value => value + 1)}>다시 시도</Button></div></div>
        : !product ? <div className="ds-feedback"><div className="ds-feedback__inner"><div className="ds-feedback__title">표시할 제품이 없습니다</div><div className="ds-feedback__message">SplitTable 제품이 등록되면 랏 관리 표를 사용할 수 있습니다.</div><Button variant="secondary" onClick={() => setProductsReloadToken(value => value + 1)}>새로고침</Button></div></div>
        : loading ? <Loading text="랏 관리 표 로딩..."/>
        : tableError ? <div className="ds-feedback"><div className="ds-feedback__inner"><div className="ds-feedback__title">랏 관리 표를 불러오지 못했습니다</div><div className="ds-feedback__message">{tableError}</div><Button variant="secondary" onClick={() => setTableReloadToken(value => value + 1)}>다시 시도</Button></div></div>
        : !work ? <div className="ds-feedback"><div className="ds-feedback__inner"><div className="ds-feedback__title">랏 관리 표가 준비되지 않았습니다</div><Button variant="secondary" onClick={() => setTableReloadToken(value => value + 1)}>다시 시도</Button></div></div>
        : <div className="flow-page__content">
        {viewLot&&<section ref={splitViewRef} className="lot-management__split-preview"><div className="lot-management__split-preview-header"><strong>LOT {viewLot} SplitTable</strong><span className="u-muted">{selectedCustom ? `CUSTOM: ${selectedCustom}` : "기본 KNOB"}</span><Button className="u-push-right" variant="ghost" size="compact" onClick={() => setViewLot("")}>닫기</Button></div><Suspense fallback={<Loading text="SplitTable 불러오는 중..."/>}><LazySplitTable key={`${product}:${viewLot}:${selectedCustom}`} user={user} initialProduct={product} initialFabLotId={viewLot} initialCustomName={selectedCustom} embedded/></Suspense></section>}
        <div className="lot-management__grid-frame">
          <table className="lot-management__grid">
            <thead><tr><th style={{width:42}}>#</th>{work.columns.map(column => <Fragment key={column.id}><th onDoubleClick={() => editing && renameColumn(column)} style={{minWidth:column.id==="comment"?260:170,cursor:editing&&!DEFAULT_COLUMNS.some(c=>c.id===column.id)?"pointer":"default"}}>{column.label}{editing&&!DEFAULT_COLUMNS.some(c=>c.id===column.id)&&<Button variant="danger" size="compact" className="u-push-right" onClick={() => deleteColumn(column)} aria-label={`${column.label} 열 삭제`}>×</Button>}</th>{column.id==="lot_id"&&<th title="SplitTable 보기" style={{width:72}}>View</th>}</Fragment>)}{editing&&<th style={{width:42}}/>}</tr></thead>
            <tbody>{visibleRows.length ? visibleRows.map((row, ri) => <tr key={row.id}>
              <td className="lot-management__row-number">{ri+1}</td>
              {work.columns.map(column => {
                const key = `${row.id}:${column.id}`;
                const cellColor = work.colors[key] || PALETTE[0];
                const value = row.values?.[column.id] || "";
                const computed = COMPUTED_COLUMNS.has(column.id);
                const colorable = COLORABLE_COLUMNS.has(column.id);
                return <Fragment key={column.id}>
                  <td onContextMenu={editing&&colorable ? event => openCellColorPicker(event,row.id,column.id) : undefined} title={editing&&colorable ? "우클릭: 배경색 팔레트 열기" : undefined} style={{padding:0,background:cellColor}}>
                    {editing ? (computed
                      ? <div className="lot-management__computed-cell">{value}</div>
                      : <div className="u-inline">{column.id==="lot_id"
                        ? <LotIdEditor value={value} candidates={lotCandidates} onChange={nextValue => updateLotId(row.id,nextValue)} onCommit={nextValue => refreshRowStatus(row.id,nextValue)} onPaste={e => pasteBlock(e,row.id,column.id)} onSearch={searchLotCandidates}/>
                        : <textarea className="lot-management__cell-input" value={value} onChange={e => updateCell(row.id,column.id,e.target.value)} onPaste={e => pasteBlock(e,row.id,column.id)} rows={1}/>}</div>)
                      : <div className="lot-management__readonly-cell">{value}</div>}
                  </td>
                  {column.id==="lot_id"&&<td className="lot-management__cell-actions"><Button variant="secondary" size="compact" disabled={!String(row.values?.lot_id || "").trim()} onClick={() => setViewLot(String(row.values?.lot_id || "").trim())} title="SplitTable 보기" aria-label="SplitTable 보기">보기</Button></td>}
                </Fragment>;
              })}
              {editing&&<td className="lot-management__cell-actions"><Button variant="danger" size="compact" onClick={() => deleteRow(row.id)} aria-label={`${ri+1}행 삭제`}>×</Button></td>}
            </tr>) : <tr><td colSpan={tableColumnCount}><div className="ds-feedback"><div className="ds-feedback__inner"><div className="ds-feedback__title">검색 결과가 없습니다</div><div className="ds-feedback__message">purpose 검색어를 바꾸거나 지워 주세요.</div></div></div></td></tr>}</tbody>
          </table>
        </div>
        {editing&&<div className="lot-management__footer-actions"><Button variant="secondary" onClick={addRow}>＋ 행 추가</Button><Button variant="secondary" onClick={addColumn}>＋ 열 추가</Button><span className="u-muted">purpose 또는 lot_id 셀을 우클릭해 배경색을 선택하세요.</span></div>}
      </div>}
    </main>
    {canManage&&<>
      <PageGearButton onClick={() => setShowSettings(true)} title="랏 관리 설정" position="bottom-right" zIndex={97}/>
      {showSettings&&<Modal open onClose={() => setShowSettings(false)} width={560} zIndex={2500}>
        <div style={{fontWeight:900,fontSize:16,marginBottom:12}}>랏 관리 설정</div>
        <ProductOrderEditor products={products.map(item => item.name)} productOrder={productOrder}
          onSave={saveProductOrder} busy={productOrderBusy}/>
      </Modal>}
    </>}
    {colorPicker&&<div role="dialog" aria-label="셀 배경색 팔레트" onMouseDown={event => event.stopPropagation()} onContextMenu={event => event.preventDefault()} style={{position:"fixed",left:colorPicker.left,top:colorPicker.top,zIndex:3000,width:190,padding:10,boxSizing:"border-box",border:"1px solid #9ca3af",borderRadius:8,background:"#ffffff",boxShadow:"0 8px 24px rgba(0,0,0,.24)"}}>
      <div style={{fontSize:12,fontWeight:800,color:"#374151",marginBottom:8}}>배경색 선택</div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(5, 1fr)",gap:6}}>{PALETTE.map(color => {
        const selected = (draft?.colors?.[`${colorPicker.rowId}:${colorPicker.columnId}`] || PALETTE[0]) === color;
        return <button key={color} type="button" onClick={() => setCellColor(color)} aria-label={`배경색 ${color}`} title={color} style={{height:26,border:selected?"2px solid #2563eb":"1px solid #9ca3af",borderRadius:4,background:color,cursor:"pointer",boxShadow:selected?"0 0 0 1px #ffffff inset":"none"}}/>;
      })}</div>
    </div>}
    {showVersions&&<Modal open onClose={() => setShowVersions(false)} width={760} zIndex={2500}><div style={{fontWeight:900,fontSize:16,marginBottom:12}}>버전 기록 · {String(product).replace(/^ML_TABLE_/, "")}</div><div style={{maxHeight:560,overflow:"auto"}}>{versions.length ? versions.map(v => {const diff=versionDiffs[v.version];const open=openVersion===v.version;return <div key={v.version} style={{borderBottom:"1px solid var(--border)"}}><div style={{display:"grid",gridTemplateColumns:"70px 1fr auto auto",gap:8,alignItems:"center",padding:"9px 4px"}}><strong style={{fontFamily:"monospace",color:"var(--accent)"}}>v{v.version}</strong><div><div style={{fontSize:13}}>{v.note || "LOT table edit"}</div><div style={{fontSize:11,color:"var(--text-secondary)"}}>{v.updated_at?.slice(0,19).replace("T"," ")} · {v.updated_by || "-"} · {v.row_count}행 / {v.column_count}열</div></div><button style={buttonStyle} onClick={() => toggleVersionChanges(v.version)}>{open?"▾ 변경항목 닫기":`▸ 변경항목${diff?` (${diff.change_count})`:""}`}</button><button style={buttonStyle} onClick={() => rollback(v.version)}>롤백</button></div>{open&&<div style={{margin:"0 4px 10px 74px",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)",overflow:"hidden"}}>{diffLoading===v.version?<div style={{padding:12,fontSize:12,color:"var(--text-secondary)"}}>변경항목을 불러오는 중...</div>:diff?<>{!diff.comparison_available&&v.version>1&&<div style={{padding:"8px 10px",fontSize:12,color:"var(--warn)",borderBottom:"1px solid var(--border)"}}>비교할 직전 버전이 보존기간을 지나 현재 버전 전체를 기준으로 표시합니다.</div>}{diff.changes.length?diff.changes.map((change,index)=><div key={`${change.type}-${index}`} style={{display:"grid",gridTemplateColumns:"80px minmax(90px,140px) 1fr",gap:8,padding:"7px 9px",borderBottom:index===diff.changes.length-1?0:"1px solid var(--border)",fontSize:12,alignItems:"start"}}><span style={{fontWeight:800,color:change.type==="row_removed"||change.type==="column_removed"?"var(--danger)":change.type==="color_changed"?"var(--violet)":"var(--accent)"}}>{changeTypeLabel(change.type)}</span><span style={{fontFamily:"monospace",color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis"}} title={change.lot_id||change.row_id||""}>{change.lot_id||change.column||"-"}</span><div><span style={{fontWeight:700}}>{change.column||"행"}</span>{change.type==="color_changed"?<span style={{marginLeft:8,display:"inline-flex",alignItems:"center",gap:5}}><i style={{width:14,height:14,border:"1px solid #9ca3af",background:change.old,display:"inline-block"}}/> {change.old} → <i style={{width:14,height:14,border:"1px solid #9ca3af",background:change.new,display:"inline-block"}}/> {change.new}</span>:<div style={{marginTop:2,whiteSpace:"pre-wrap",wordBreak:"break-word"}}><span style={{color:"var(--danger)"}}>{change.old||"(없음)"}</span><span style={{margin:"0 6px",color:"var(--text-secondary)"}}>→</span><span style={{color:"var(--ok)"}}>{change.new||"(없음)"}</span></div>}</div></div>):<div style={{padding:12,fontSize:12,color:"var(--text-secondary)"}}>직전 버전과 달라진 항목이 없습니다.</div>}{diff.truncated&&<div style={{padding:8,fontSize:11,color:"var(--warn)"}}>변경항목이 많아 처음 1,000건만 표시합니다.</div>}</>:null}</div>}</div>}) : <div style={{padding:30,textAlign:"center",color:"var(--text-secondary)"}}>저장된 버전이 없습니다.</div>}</div></Modal>}
    {showCustom&&<Modal open onClose={() => setShowCustom(false)} width={720} zIndex={2600}><div style={{fontWeight:900,fontSize:16,marginBottom:10}}>SplitTable CUSTOM SET</div><div style={{display:"flex",gap:8,marginBottom:10}}><input value={customName} onChange={e=>setCustomName(e.target.value)} placeholder="세트명" style={{...buttonStyle,flex:1}}/><input value={customSearch} onChange={e=>setCustomSearch(e.target.value)} placeholder="컬럼 검색" style={{...buttonStyle,flex:1}}/></div><div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,maxHeight:420}}><div style={{border:"1px solid var(--border)",borderRadius:7,overflow:"auto",padding:6}}>{customs.map(c => <button key={c.name} onClick={() => {setCustomName(c.name);setCustomColumns(c.columns || []);}} style={{...buttonStyle,width:"100%",textAlign:"left",marginBottom:4,background:customName===c.name?"var(--accent-glow)":"var(--bg-card)"}}>{c.name} <span style={{float:"right",color:"var(--text-secondary)"}}>{(c.columns||[]).length}</span></button>)}</div><div style={{border:"1px solid var(--border)",borderRadius:7,overflow:"auto",padding:6}}>{customPool.map(column => {const checked=customColumns.includes(column);return <label key={column} style={{display:"block",padding:"5px 6px",fontSize:12,cursor:"pointer",color:checked?"var(--accent)":"var(--text-secondary)"}}><input type="checkbox" checked={checked} onChange={() => setCustomColumns(cur => checked?cur.filter(c=>c!==column):[...cur,column])}/> {column}</label>})}</div></div><div style={{display:"flex",justifyContent:"flex-end",gap:8,marginTop:12}}><button style={buttonStyle} onClick={()=>setShowCustom(false)}>취소</button><button style={{...buttonStyle,background:"var(--accent)",color:"white"}} onClick={saveCustom}>저장</button></div></Modal>}
  </PageShell>;
}
