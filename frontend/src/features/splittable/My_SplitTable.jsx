import { useState, useEffect, useRef, Fragment } from "react";
import Loading from "../../components/Loading";
import Modal from "../../components/Modal";
import { PageGearButton } from "../../components/PageGear";
import ProductOrderEditor from "../../components/ProductOrderEditor";
import { toast } from "../../components/Toast";
import { authSrc, sf, dl } from "../../lib/api";
import { allowedSubTabs, useUserRole } from "../../lib/permissions";
import { orderProductItems } from "../../lib/productOrder";

// v9.1.x: 소탭 단위 권한 — 허용된 소탭(view/history)만 노출.
// 주의: localStorage(hol_user)는 로그인 후에 채워지므로 모듈 로드 시점이 아니라
// 렌더 시점에 평가해야 한다 (모듈 상수로 고정하면 로그인 전 평가 시 탭이 영구히 빈다).
const SPLITTABLE_TABS_ALL = [{k:"view",l:"View"},{k:"history",l:"History"}];
const splittableTabs = () => SPLITTABLE_TABS_ALL.filter(({k})=>allowedSubTabs("splittable").includes(k));
import { statusPalette } from "../../components/UXKit";
import SplitTableSnapshotView, { buildPemsStView, buildSplitCheckStView, SPLIT_CHECK_PREFIX_COLUMNS, splitParamDisplayName } from "../../components/SplitTableSnapshotView";
const API="/api/splittable";
const INFORM_API="/api/informs";
const INFORM_WIZARD_DRAFT_KEY="flow_inform_wizard_draft_v1";
const INFORM_WIZARD_OPEN_KEY="flow_inform_open_wizard_v1";
const BAD = statusPalette.bad;
const GRID_BORDER = "rgba(85,85,85,0.95)";
const GRID_LINE = `1px solid ${GRID_BORDER}`;
const GRID_LINE_STRONG = `1px solid ${GRID_BORDER}`;
const GRID_TEXT = "#000000";
// Excel-like pastel colors (bg + dark text)
const CELL_COLORS=[
  {bg:"rgba(198,239,206,0.95)",fg:"rgba(0,97,0,0.95)"},  // green
  {bg:"rgba(255,235,156,0.95)",fg:"rgba(156,87,0,0.95)"},  // yellow
  {bg:"rgba(251,229,214,0.95)",fg:"rgba(191,78,0,0.95)"},  // orange
  {bg:"rgba(189,215,238,0.95)",fg:"rgba(31,78,121,0.95)"},  // blue
  {bg:"rgba(226,191,238,0.95)",fg:"rgba(112,48,160,0.95)"},  // purple
  {bg:"rgba(180,222,212,0.95)",fg:"rgba(11,83,69,0.95)"},  // teal
  {bg:"rgba(244,204,204,0.95)",fg:"rgba(117,25,76,0.95)"},  // pink
];
const COLOR_PREFIXES=["KNOB","MASK"];
const HIST_PAGE=300;   // History 탭 1회 조회량 ("더 보기" 로 누적)
const HIST_TH={textAlign:"left",padding:"8px 10px",borderBottom:"2px solid var(--border)",color:"var(--text-secondary)",fontSize:14,whiteSpace:"nowrap"};
const HIST_TD={padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:14,verticalAlign:"top"};
const HIST_MONO={...HIST_TD,fontFamily:"monospace"};
const HIST_INPUT={padding:"3px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:14};
const CANDIDATE_PREVIEW_LIMIT=50;
const CANDIDATE_SEARCH_LIMIT=120;
const ROOT_LOT_CACHE_LIMIT_MAX=50000;
const candidateLimit=(value)=>String(value||"").trim()?CANDIDATE_SEARCH_LIMIT:CANDIDATE_PREVIEW_LIMIT;
// v9.5.x: Root lot RAM cache 설정/수동 스캔/쿼리 코어 조정은 캐시 관리 탭(My_RamCache)으로 이동.
const isInlineVmSplitParam=(value)=>{
  const v=String(value||"").trim().toUpperCase();
  return v==="INLINE"||v==="VM"||v.startsWith("INLINE_")||v.startsWith("VM_");
};
// 통합(병합) 표시는 split 조건 열에만 의미가 있다. INLINE/VM 은 wafer 별 실측값,
// TAG/관리 행은 자유 입력이라 값이 우연히 같다고 묶으면 wafer 별 값을 못 읽는다.
// backend routers/splittable.py 의 MERGE_VIEW_PREFIXES 와 같은 규약이다.
const MERGE_PREFIXES=["KNOB","FAB","MASK"];
const isMergeableParam=(value)=>{
  const v=String(value||"").trim().toUpperCase();
  return MERGE_PREFIXES.some(p=>v===p||v.startsWith(p+"_"));
};
const stripMlPrefix=(s)=>{
  const v=String(s||"").trim();
  return v.startsWith("ML_TABLE_")?v.slice("ML_TABLE_".length):v;
};
const stepIdsForGroup=(group)=>Array.isArray(group?.step_ids)?group.step_ids.filter(Boolean):[];
const normalizeOperatorKey=(value)=>String(value??"").trim().toLowerCase().replace(/[\s-]+/g,"_");
const isNullOperator=(value)=>normalizeOperatorKey(value)==="is_null";
const isNotNullOperator=(value)=>normalizeOperatorKey(value)==="not_null";
const knobStepGroups=(groups,{excludeNotNull=false}={})=>{
  const byStep=new Map();
  (Array.isArray(groups)?groups:[]).forEach(g=>{
    if(isNullOperator(g?.operator))return;
    if(excludeNotNull&&isNotNullOperator(g?.operator))return;
    const desc=String(g?.step_desc||g?.func_step||"").trim();
    stepIdsForGroup(g).forEach(rawSid=>{
      const sid=String(rawSid||"").trim();
      if(!sid)return;
      if(!byStep.has(sid))byStep.set(sid,{step_id:sid,step_descs:[],seen:new Set()});
      const item=byStep.get(sid);
      const key=desc.toLowerCase();
      if(desc&&!item.seen.has(key)){item.seen.add(key);item.step_descs.push(desc);}
    });
  });
  return Array.from(byStep.values()).map(({seen,...item})=>item);
};
const knobLineageRow=(param,groups,{excludeNotNull=false}={})=>{
  const steps=knobStepGroups(groups,{excludeNotNull});
  if(!steps.length)return null;
  const descs=[];const seenDesc=new Set();
  const stepIds=[];const seenStep=new Set();
  steps.forEach(item=>{
    (item.step_descs||[]).forEach(desc=>{
      const key=String(desc||"").trim().toLowerCase();
      if(key&&!seenDesc.has(key)){seenDesc.add(key);descs.push(desc);}
    });
    const sid=String(item.step_id||"").trim();
    const sidKey=sid.toLowerCase();
    if(sid&&!seenStep.has(sidKey)){seenStep.add(sidKey);stepIds.push(sid);}
  });
  return {key:param,parameter:param,step_desc:descs.join(", "),step_ids:stepIds};
};
const matchedWaferSummary=(matches)=>{
  const wafers=[];
  const seen=new Set();
  (matches||[]).forEach(m=>{
    const wafer=String(m?.wafer||"").replace(/^#?W/i,"").trim();
    if(!wafer||seen.has(wafer))return;
    seen.add(wafer);wafers.push(wafer);
  });
  wafers.sort((a,b)=>{
    const na=Number(a), nb=Number(b);
    if(Number.isFinite(na)&&Number.isFinite(nb))return na-nb;
    return a.localeCompare(b);
  });
  return wafers.length?`해당 WF #${wafers.join(",")}`:"";
};
// module 묶음 열 폭 — Vehicle_matching 에 module 열이 있을 때만 붙는다.
const MODULE_COL_W=86;
// ── 적용공정정보 표기 ────────────────────────────────────────────────────
// "적용 공정 정보" 를 켜면 항목 칸은 이름 대신 **연결된 공정**을 보여준다.
//   KNOB          → step_id (한 rule_order 안의 서로 다른 step_desc 조건만 `&`)
//   INLINE / VM   → `step_id | item_id`
// 어느 쪽이든 표시할 게 여러 개면 같은 칸에서 줄바꿈으로 쌓는다.
//
// 한 step_desc 가 여러 step_id 로 매핑되면 같은 셀 안에서 줄바꿈한다. 그 step_id
// 들은 AND 조건이 아니다. is_null 조건은 매핑 step_id 가 있더라도 표시 조합에서
// 항상 빼고, not_null 은 기존 화면 옵션이 켜졌을 때만 뺀다.
const knobStepLines=(groups,{excludeNotNull=false}={})=>{
  const lines=[];const seen=new Set();
  knobRuleSets(groups).forEach(set=>{
    const byDesc=new Map();
    (set.conditions||[]).forEach(g=>{
      if(isNullOperator(g?.operator))return;
      if(excludeNotNull&&isNotNullOperator(g?.operator))return;
      const desc=String(g?.step_desc||g?.func_step||"").trim();
      if(!desc)return;
      const descKey=desc.toLowerCase();
      if(!byDesc.has(descKey))byDesc.set(descKey,{step_desc:desc,step_ids:[],seen:new Set()});
      const descGroup=byDesc.get(descKey);
      stepIdsForGroup(g).forEach(raw=>{
        const sid=String(raw||"").trim();
        const sidKey=sid.toLowerCase();
        if(sid&&!descGroup.seen.has(sidKey)){descGroup.seen.add(sidKey);descGroup.step_ids.push(sid);}
      });
    });
    const descGroups=Array.from(byDesc.values()).filter(g=>g.step_ids.length>0);
    if(!descGroups.length)return;
    // step_desc 내부 복수 step_id는 줄바꿈, 같은 rule_order의 서로 다른
    // step_desc 블록 사이에만 &를 둔다.
    const line=descGroups.map(g=>g.step_ids.join("\n")).join("\n&\n");
    if(!seen.has(line)){seen.add(line);lines.push(line);}
  });
  return lines;
};
const knobStepSummaryText=(groups,options={})=>knobStepLines(groups,options).join("\n");
// INLINE(inline_matching) / VM(vm_matching + Vehicle_matching) 공용.
// VM 은 step_desc 를 Vehicle_matching 의 제품별 step_id 로 푼 결과가 이미
// meta.groups[].step_ids 에 들어 있다 (backend _build_vm_meta).
const stepItemLines=(meta)=>{
  const out=[];const seen=new Set();
  const fallbackItem=String(meta?.item_id||"").trim();
  const push=(sid,itemId)=>{
    const s=String(sid||"").trim();
    if(!s)return;
    const i=String(itemId||fallbackItem||"").trim();
    const line=i?`${s} | ${i}`:s;
    if(!seen.has(line)){seen.add(line);out.push(line);}
  };
  (Array.isArray(meta?.groups)?meta.groups:[]).forEach(g=>{
    const ids=stepIdsForGroup(g);
    if(ids.length)ids.forEach(sid=>push(sid,g?.item_id));
    else push(g?.step_id,g?.item_id);
  });
  if(!out.length)(Array.isArray(meta?.step_ids)?meta.step_ids:[]).forEach(sid=>push(sid,fallbackItem));
  return out;
};
// 이 행이 적용공정정보 모드에서 보여줄 줄 목록. 빈 배열이면 표시 대상이 없다.
const matchStepLines=(kind,meta,{excludeNotNull=false}={})=>{
  if(kind==="knob_ppid")return knobStepLines(meta?.groups||[],{excludeNotNull});
  if(kind==="inline_matching"||kind==="vm_matching")return stepItemLines(meta||{});
  return [];
};
// Vehicle_matching.csv 의 module 열이 유일한 원천이다. 그 열이 없으면 KNOB/VM 도
// 빈 값이라 module 열 자체가 안 붙는다. INLINE 은 자기 CSV 에 module 이 없으므로
// 백엔드가 항상 빈 값을 주고, 따로 묶이지 않고 '—' 로만 나온다.
const matchModuleOf=(meta)=>{
  const mods=Array.isArray(meta?.modules)?meta.modules:[];
  const first=String(meta?.module||mods[0]||"").trim();
  return first;
};
const knobRuleSets=(groups)=>{
  const sets=[];const byOrder=new Map();
  (Array.isArray(groups)?groups:[]).forEach((g,idx)=>{
    const order=String(g?.rule_order||`R${idx+1}`).trim()||`R${idx+1}`;
    if(!byOrder.has(order)){
      const item={rule_order:order,conditions:[]};
      byOrder.set(order,item);sets.push(item);
    }
    byOrder.get(order).conditions.push(g);
  });
  return sets;
};
const knobRuleBadgeStyle=(highlight=false)=>({
  padding:"0 5px",
  borderRadius:2,
  fontFamily:"monospace",
  fontWeight:800,
  fontSize:14,
  lineHeight:"18px",
  ...(highlight
    ? {background:"rgba(239,68,68,0.08)",border:"1px solid rgba(239,68,68,0.95)",color:"rgba(220,38,38,0.95)"}
    : {background:"rgba(59,130,246,0.15)",border:"1px solid rgba(59,130,246,0.35)",color:"rgba(59,130,246,0.95)"})
});
const hasRuleMatchValue=(v)=>v!=null&&v!==""&&v!=="None"&&v!=="null";
// /view 슬림 셀 포맷(cells_format v2) 디코더 — 서버는 행당 actual 배열(a) +
// sparse plan(p)/mismatch(m) + 행-상수 플래그만 보낸다(전송량 ~10x 감소).
// 수신 직후 기존 _cells 형태로 복원해 이후 로직(선택/paste/plan 편집)은 그대로
// 동작한다. 셀 key 조립 규칙(root|wafer_key|param)은 서버 _compact_view_rows
// 와 계약이므로 함께 바꿔야 한다.
const expandViewRows=(d)=>{
  if(!d||d.cells_format!=="v2"||!Array.isArray(d.rows))return d;
  const wfKeys=Array.isArray(d.wafer_keys)?d.wafer_keys:[];
  const root=d.root_lot_id??"";
  const rows=d.rows.map(r=>{
    const vals=Array.isArray(r?.a)?r.a:[];
    const plans=r?.p||{};
    const mism=new Set(Array.isArray(r?.m)?r.m:[]);
    const canPlan=!!r?.can_plan,isTag=!!r?.tag,isMgmt=!!r?.mgmt;
    const cells={};
    for(let ci=0;ci<vals.length;ci++){
      const key=String(ci);
      cells[key]={
        actual:vals[ci]??null,
        plan:plans[key]??null,
        key:`${root}|${wfKeys[ci]??ci}|${r._param}`,
        can_plan:canPlan,
        mismatch:mism.has(ci),
        is_custom_tag:isTag,
        can_tag:isTag,
        is_management_row:isMgmt,
        can_management_edit:isMgmt,
      };
    }
    return {_param:r._param,_display:r._display,_cells:cells};
  });
  const out={...d,rows};
  delete out.cells_format;delete out.wafer_keys;
  return out;
};
const normalizeKnobRuleValue=(value)=>String(value??"").trim().toLowerCase();
const waferFromCellKey=(key)=>{
  const parts=String(key||"").split("|");
  return parts.length>=2?String(parts[1]||"").trim():"";
};
const matchKnobRuleToRowValues=(group,row,pendingValueFor)=>{
  const target=normalizeKnobRuleValue(group?.category);
  if(!target||!row||!row._cells)return[];
  const out=[];const seen=new Set();
  Object.entries(row._cells||{}).forEach(([ci,cell])=>{
    const wafer=waferFromCellKey(cell?.key)||String(Number(ci)+1);
    const candidates=[["actual",cell?.actual],["plan",cell?.plan]];
    const pending=pendingValueFor?pendingValueFor(cell):undefined;
    if(pending!==undefined)candidates.push(["pending",pending]);
    candidates.forEach(([source,value])=>{
      if(!hasRuleMatchValue(value)||normalizeKnobRuleValue(value)!==target)return;
      const key=`${wafer}|${source}|${String(value)}`;
      if(seen.has(key))return;
      seen.add(key);
      out.push({wafer,source,value:String(value)});
    });
  });
  return out;
};

function SplitTableCellEditor({activeCell,suggestions=[],suggestionsLoading=false,onValueChange,onCommit,onClose}){
  if(!activeCell)return null;
  const commit=(v)=>onCommit&&onCommit((v??"").trim());
  return <Modal open onClose={onClose} width={360} zIndex={9998}>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4,fontFamily:"monospace"}}>{activeCell.key.split("|").slice(0,2).join(" · ")}</div>
    <div style={{fontSize:14,fontWeight:700,marginBottom:10,color:"var(--accent)",fontFamily:"monospace"}}>{activeCell.param}</div>
    <input autoFocus value={activeCell.value} onChange={e=>onValueChange&&onValueChange(e.target.value)}
      onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;commit(activeCell.value);}else if(e.key==="Escape")onClose&&onClose();}}
      list={`cv-${activeCell.key}`}
      placeholder="값 입력 또는 아래 리스트 선택"
      style={{width:"100%",padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace",boxSizing:"border-box"}}/>
    <datalist id={`cv-${activeCell.key}`}>{suggestions.map(v=><option key={v} value={v}/>)}</datalist>
    <div style={{marginTop:10,maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)"}}>
      {suggestions.length===0?<div style={{padding:"10px 12px",fontSize:14,color:"var(--text-secondary)"}}>{suggestionsLoading?"로딩…":"suggestion 없음"}</div>
       :suggestions.slice(0,100).map((v,i)=><div key={i} onClick={()=>commit(v)} style={{padding:"6px 10px",fontSize:14,fontFamily:"monospace",cursor:"pointer",borderBottom:i<suggestions.length-1?"1px solid var(--border)":"none"}} onMouseEnter={e=>e.currentTarget.style.background="var(--accent-glow)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{v}</div>)}
    </div>
    {suggestions.length>0&&<div style={{fontSize:14,color:"var(--text-secondary)",marginTop:6}}>{suggestions.length} 개 (전체 데이터셋 unique + plan 포함)</div>}
    <div style={{display:"flex",gap:8,marginTop:12}}>
      <button onClick={()=>commit(activeCell.value)} style={{flex:1,padding:"8px 12px",borderRadius:6,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontWeight:600,cursor:"pointer",fontSize:14}}>Apply</button>
      <button onClick={onClose} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer",fontSize:14}}>Cancel</button>
    </div>
  </Modal>;
}

export default function My_SplitTable({user,initialProduct="",initialFabLotId="",initialCustomName="",embedded=false}){
  const normFabSource=(v)=>{
    let s=String(v||"").trim().replaceAll("\\","/");
    if(!s) return "";
    if(s.toLowerCase().startsWith("db/")) s=s.slice(3);
    else if(s.toLowerCase().startsWith("base/")) s=s.slice(5);
    while(s.startsWith("/")) s=s.slice(1);
    return s;
  };
  const[products,setProducts]=useState([]);const[selProd,setSelProd]=useState(initialProduct||"");
  const[productOrder,setProductOrder]=useState([]);const[productOrderBusy,setProductOrderBusy]=useState(false);
  const[lotId,setLotId]=useState("");const[waferIds,setWaferIds]=useState("");
  const[lotSuggestions,setLotSuggestions]=useState([]);const[showLotDrop,setShowLotDrop]=useState(false);const[lotFilter,setLotFilter]=useState("");
  const[lotSuggestMsg,setLotSuggestMsg]=useState("");
  const[lotPoolVer,setLotPoolVer]=useState(0); // v9.3.x: 풀 로드 완료 시 증가 → 필터 useEffect 재실행 트리거
  // v8.4.3: fab_lot_id 검색도 지원 — root_lot_id 대체 키로 사용 가능.
  const[fabLotId,setFabLotId]=useState(initialFabLotId||"");const[fabSuggestions,setFabSuggestions]=useState([]);const[showFabDrop,setShowFabDrop]=useState(false);
  const[fabSuggestBusy,setFabSuggestBusy]=useState(false);const[fabSuggestMsg,setFabSuggestMsg]=useState("");
  const[prefixes,setPrefixes]=useState([]);const[selPrefixes,setSelPrefixes]=useState(["KNOB"]);
  const[customs,setCustoms]=useState([]);const[selCustom,setSelCustom]=useState(initialCustomName||"");const[isCustomMode,setIsCustomMode]=useState(!!initialCustomName);
  const[viewMode,setViewMode]=useState("all");
  // v9.2.x: 대형 결과(예: KNOB 1000행) 첫 페인트 <1s 보장 — 행을 점진 렌더.
  //   초기 ROW_RENDER_INITIAL 행만 그리고, 하단 sentinel 이 보이면 CHUNK 씩 확장.
  //   전체를 한 커밋에 그리면 1000행×25웨이퍼 기준 메인스레드가 2초+ 블로킹된다.
  const ROW_RENDER_INITIAL=200, ROW_RENDER_CHUNK=300;
  const[rowRenderLimit,setRowRenderLimit]=useState(ROW_RENDER_INITIAL);
  const renderMoreRef=useRef(null);
  const[showParamMeta,setShowParamMeta]=useState(false);
  const[showLineageSummary,setShowLineageSummary]=useState(false);
  const[showSplitCheckView,setShowSplitCheckView]=useState(false);
  // v9.1.x: 제3 표시형식 — 행에서 왼쪽 값과 같은 칸을 colSpan 으로 병합해 표시 (읽기 전용).
  const[showMergedView,setShowMergedView]=useState(false);
  const[showPemsView,setShowPemsView]=useState(false);
  const[excludeNotNullStepMeta,setExcludeNotNullStepMeta]=useState(true);
  const[data,setData]=useState(null);const[loading,setLoading]=useState(false);const[informSnapshotBusy,setInformSnapshotBusy]=useState(false);
  useEffect(()=>{
    const el=renderMoreRef.current;
    if(!el)return;
    const grow=()=>setRowRenderLimit(l=>l+ROW_RENDER_CHUNK);
    const io=new IntersectionObserver(es=>{es.forEach(en=>{if(en.isIntersecting)grow();});},{rootMargin:"600px"});
    io.observe(el);
    // IO 콜백이 억제되는 임베디드/스로틀 환경 폴백 — 스크롤로 sentinel 에 근접하면 확장.
    let scroller=el.parentElement;
    while(scroller&&scroller.scrollHeight<=scroller.clientHeight+50)scroller=scroller.parentElement;
    const target=scroller||window;
    let last=0;
    const onScroll=()=>{
      const now=Date.now();
      if(now-last<150)return;
      last=now;
      const r=el.getBoundingClientRect();
      if(r.top<window.innerHeight+600)grow();
    };
    target.addEventListener("scroll",onScroll,{passive:true});
    return()=>{io.disconnect();target.removeEventListener("scroll",onScroll);};
    // deps: sentinel 은 data/viewMode/limit 변경 때만 재마운트 — 매 렌더 재등록 방지
  },[data,viewMode,rowRenderLimit]);
  const viewRetryTimerRef=useRef(null);
  const viewSearchSeqRef=useRef(0);
  // 검색 조건이 바뀌면 이전 lookup 준비 polling을 즉시 중단한다.
  useEffect(()=>{
    viewSearchSeqRef.current+=1;
    if(viewRetryTimerRef.current){clearTimeout(viewRetryTimerRef.current);viewRetryTimerRef.current=null;}
    return()=>{if(viewRetryTimerRef.current){clearTimeout(viewRetryTimerRef.current);viewRetryTimerRef.current=null;}};
  },[selProd,lotId,fabLotId]);
  // 새 검색 결과/표시 모드 변경 시 점진 렌더 한도를 초기화.
  useEffect(()=>{setRowRenderLimit(ROW_RENDER_INITIAL);},[data,viewMode]);
  const[editing,setEditing]=useState(false);const[pendingPlans,setPendingPlans]=useState({});const[pendingTags,setPendingTags]=useState({});const[pendingManagement,setPendingManagement]=useState({});
  const[showConfirm,setShowConfirm]=useState(false);
  // 저장 1회당 선택 입력하는 변경 사유. 셀 메모(💬)와 별개로 이력에만 붙는다.
  const[planReason,setPlanReason]=useState("");
  // dbl-click inline edit: {cellKey, value, suggestions, param}
  const[activeCell,setActiveCell]=useState(null);
  const[selectedCellRange,setSelectedCellRange]=useState(null); // {startRow,startCol,endRow,endCol}
  const[selectionAnchor,setSelectionAnchor]=useState(null);
  const[isDraggingSelection,setIsDraggingSelection]=useState(false);
  const[colValCache,setColValCache]=useState({});
  // v8.4.7: KNOB feature_name → {label, groups}. 제품 바뀌면 재fetch.
  const[knobMeta,setKnobMeta]=useState({});
  // v8.4.9-b: Notes (wafer 태그 + param 메모). lot 단위로 fetch.
  const[notes,setNotes]=useState([]);
  const[notesOpen,setNotesOpen]=useState(false);
  const[noteFilter,setNoteFilter]=useState(null); // {scope, key} or null = all
  const[noteDraft,setNoteDraft]=useState("");
  const[noteImages,setNoteImages]=useState([]);
  const[noteUploading,setNoteUploading]=useState(false);
  const[noteDraftScope,setNoteDraftScope]=useState(null);  // {scope, product, root_lot_id, wafer_id, param}
  const[expandedNoteId,setExpandedNoteId]=useState("");
  // v8.8.13: 노트 drawer 내부 검색 (wafer id / param 이름 / text 부분일치)
  const[noteSearch,setNoteSearch]=useState("");
  const SPLITTABLE_TABS=splittableTabs();
  const[tab,setTab]=useState(()=>splittableTabs()[0]?.k||"view");const[history,setHistory]=useState([]);
  const[histMode,setHistMode]=useState("lot_final");const[histFinal,setHistFinal]=useState({final:[],drift:[],drift_count:0,total_cells:0});
  // History 탭: "누가 어떻게 바꿨는지" 추적용 필터/페이지 상태.
  const[histFilter,setHistFilter]=useState({user:"",action:"",column:"",wafer_id:"",q:"",since:"",until:"",has_reason:false});
  const[histFacets,setHistFacets]=useState({users:[],actions:[],columns:[]});
  const[histMeta,setHistMeta]=useState({total:0,scope_total:0,has_more:false});
  const[histLimit,setHistLimit]=useState(HIST_PAGE);
  const[histGroup,setHistGroup]=useState(true);          // 한 번의 저장을 batch 로 묶어 보기
  const[histOpenBatch,setHistOpenBatch]=useState({});    // gid -> 펼침 여부
  const[histLoading,setHistLoading]=useState(false);
  const histDebounceRef=useRef(null);
  const[colSearch,setColSearch]=useState("");const[customCols,setCustomCols]=useState([]);const[customName,setCustomName]=useState("");
  const[showSettings,setShowSettings]=useState(false);const[settingsTab,setSettingsTab]=useState("basic");const[newPrefix,setNewPrefix]=useState("");
  const toggleSettings=()=>setShowSettings(open=>!open);
  const closeSettings=()=>setShowSettings(false);
  const[precision,setPrecision]=useState({});const[precisionDraft,setPrecisionDraft]=useState({});
  const[enabledSources,setEnabledSources]=useState(null); // null = loading, Set of product names
  // v8.4.4: product 별 lot_id 컬럼 override (soft-landing)
  const[lotOverrides,setLotOverrides]=useState({});
  const[overridePreview,setOverridePreview]=useState(null);
  const[overridePreviewLoading,setOverridePreviewLoading]=useState(false);
  // v8.4.4: fab_source 후보 (FileBrowser/Dashboard 와 동일 source 리스트)
  const[fabSourceOptions,setFabSourceOptions]=useState([]);
  // v8.7.8: fab_source 후보 = DB 상위폴더 (FAB/INLINE/ET/EDS) + Base 단일파일 + DB 제품 디렉토리 + TableMap.
  // v8.8.5: fab_source = DB 에서 고르는 값. ML_TABLE_*.parquet(모 테이블) 은 후보에서 제외.
  //   옵션 구성:
  //     - (자동) 옵션: 빈값 — LOT 최신 캐시를 우선 쓰고, 없을 때만 DB FAB 경로로 fallback.
  //     - 제품폴더 옵션: `<1.RAWDATA_DB_xxx>/<PROD>` — `/fab-roots` 가 반환한 각 root 의 products 를 펼침.
  //     - TableMap 옵션: `tablemap:<id>` — 사용자 정의.
  //   v8.8.21: `root:<name>` 옵션 제거 — 제품 스코프를 넘어 섞인 데이터로 join 되는 footgun.
  useEffect(()=>{
    const out=[{value:"",label:"(자동) LOT 최신 캐시 우선, 없으면 DB FAB 경로 fallback",source_type:"auto"}];
    const fabRootsReq=sf(API+"/fab-roots").then(d=>{
      const roots = d.roots || [];
      for(const r of roots){
        for(const p of r.products){
          out.push({value:`${r.name}/${p}`,label:`[DB] ${r.name}/${p}`,source_type:"db_product"});
        }
      }
    }).catch(()=>{});
    const tmap=sf("/api/dbmap/tables").then(d=>{
      for(const t of (d.tables||[])){
        const name=t.display_name||t.name||t.id;
        if(!name) continue;
        out.push({value:`tablemap:${t.id}`,label:`[TableMap] ${name}`,source_type:"tablemap"});
      }
    }).catch(()=>{});
    Promise.all([fabRootsReq,tmap]).then(()=>{
      const seen=new Set();
      setFabSourceOptions(out.filter(o=>{if(seen.has(o.value)) return false;seen.add(o.value);return true;}));
    });
  },[]);
  // v8.7.8: ML_TABLE auto-match — selProd 에서 파생 제품명 → 상위폴더 매칭 후보.
  // v8.8.3: auto_path / effective_fab_source / manual_override 도 받아서 상태 표시에 사용.
  // v8.8.5: override resolve meta(ts_col/fab_col/scanned_files/row_count/sample/error) 까지 풀세트.
  const toMlMatch=(d={})=>({pro:d.derived_product||"",matches:d.matches||[],auto_path:d.auto_path||"",effective_fab_source:d.effective_fab_source||"",manual_override:!!d.manual_override,override:d.override||null,match_cache:d.match_cache||null});
  const[mlMatch,setMlMatch]=useState(()=>toMlMatch());
  useEffect(()=>{if(!selProd){setMlMatch(toMlMatch());return;}
    sf(API+"/ml-table-match?product="+encodeURIComponent(selProd))
      .then(d=>setMlMatch(toMlMatch(d)))
      .catch(()=>setMlMatch(toMlMatch()));
  },[selProd,lotOverrides]);
  const deriveProductFolder=(prod)=>{
    const p=String(prod||"").trim();
    if(!p) return "";
    if(p.startsWith("ML_TABLE_")) return stripMlPrefix(p);
    if(p.includes("_")) return p.split("_").pop().trim();
    return p;
  };
  const getProductOverride=(product)=>((lotOverrides&&lotOverrides[product])||{});
  const mergeProductOverride=(product, patch)=>{
    setLotOverrides(cur=>({...cur,[product]:{...((cur&&cur[product])||{}),...patch}}));
  };
  const currentOverride=getProductOverride(selProd);
  const currentManualFabSource=normFabSource(currentOverride.fab_source);
  const manualFabOptions=(()=>{
    const base=(fabSourceOptions||[]).filter(o=>o.source_type==="db_product");
    if(currentManualFabSource&&!base.some(o=>normFabSource(o.value)===currentManualFabSource)){
      return [{value:currentManualFabSource,label:`[현재 설정] ${currentManualFabSource}`,source_type:"db_product"},...base];
    }
    return base;
  })();
  const draftOverrideMode=currentManualFabSource?"manual":"auto";
  const autoFabSource=mlMatch.auto_path||"";
  const effectivePreviewSource=currentManualFabSource||autoFabSource||"";
  useEffect(()=>{
    if(!selProd||!effectivePreviewSource||String(effectivePreviewSource).startsWith("tablemap:")){
      setOverridePreview(null);
      return;
    }
    setOverridePreviewLoading(true);
    sf(API+`/override-link-preview?product=${encodeURIComponent(selProd)}&fab_source=${encodeURIComponent(effectivePreviewSource)}`)
      .then(d=>setOverridePreview(d))
      .catch(e=>{
        const msg=e?.message||"연결 미리보기 실패";
        const apiMissing=/404|not found/i.test(String(msg));
        setOverridePreview({
          error: apiMissing ? "미리보기 API가 없어도 저장은 가능합니다. 경로만 저장하고 실제 컬럼은 서버가 추론합니다." : msg,
          api_missing: apiMissing,
          columns:[],
          latest_fab_lot_ids:[],
          recommended:{
            root_col:"root_lot_id",
            wf_col:"wafer_id",
            fab_col:"fab_lot_id",
            ts_col:"",
            join_keys:["root_lot_id","wafer_id"],
            override_cols:["root_lot_id","wafer_id","lot_id","tkout_time"],
          }
        });
      })
      .finally(()=>setOverridePreviewLoading(false));
  },[selProd,effectivePreviewSource]);
  const role = useUserRole(user);
  const isAdmin = role.isAdmin;
  const canManage = role.canManagePage("splittable");
  // 상단 배지는 FAB join 컬럼명이 아니라 실제 필수 캐시 4종의 파일 상태를
  // 보여준다. 미완료 동안만 짧게 폴링하고, 모두 준비되면 추가 요청을 멈춘다.
  // 관리자(role=admin) 전용 표시다 — 일반 사용자·페이지 관리자는 조회조차
  // 하지 않는다. 캐시 준비 상태는 운영 정보이지 검색 화면의 정보가 아니다.
  const[requiredCacheStatus,setRequiredCacheStatus]=useState(null);
  useEffect(()=>{
    let cancelled=false;let timer=null;
    if(!selProd||!isAdmin){setRequiredCacheStatus(null);return()=>{};}
    const load=()=>sf(API+"/cache/required-status?product="+encodeURIComponent(selProd))
      .then(d=>{
        if(cancelled)return;
        setRequiredCacheStatus(d);
        if(!d.all_ready)timer=setTimeout(load,15000);
      })
      .catch(()=>{
        if(cancelled)return;
        setRequiredCacheStatus(null);
        timer=setTimeout(load,30000);
      });
    load();
    return()=>{cancelled=true;if(timer)clearTimeout(timer);};
  },[selProd,isAdmin]);
  const lotRef=useRef(null);
  const lotSuggestSeqRef=useRef(0);
  const fabSuggestSeqRef=useRef(0);
  // v9.1.x: 후보 풀 — 서버 응답을 제품별로 누적해 두고, 키 입력 즉시 로컬 필터로
  // 부분 결과를 먼저 보여준다 (서버 결과가 도착하면 갱신).
  const lotPoolRef=useRef({key:"",values:[]});
  const fabPoolRef=useRef({key:"",values:[]});
  const poolValues=(ref,key)=>ref.current.key===key?ref.current.values:[];
  const poolMerge=(ref,key,vals)=>{
    if(ref.current.key!==key)ref.current={key,values:[]};
    if(Array.isArray(vals)&&vals.length)ref.current.values=[...new Set([...ref.current.values,...vals])];
    return ref.current.values;
  };
  const splitTableRef=useRef(null);
  const settingsLotLinkRef=useRef(null);
  const scrollToSettingsLotLink=()=>settingsLotLinkRef.current?.scrollIntoView({behavior:"smooth",block:"start"});
  const[customTags,setCustomTags]=useState([]);
  const[mismatchMailEnabled,setMismatchMailEnabled]=useState(false);
  const[mismatchMailSaveBusy,setMismatchMailSaveBusy]=useState(false);

  const reloadCustoms=()=>sf(API+"/customs").then(d=>setCustoms(cleanCustomSets(d.customs||[])));
  const reloadCustomTags=()=>{if(!selProd){setCustomTags([]);return Promise.resolve();}
    return sf(API+"/custom-tags?product="+encodeURIComponent(selProd))
      .then(d=>setCustomTags(d.columns||[]))
      .catch(()=>setCustomTags([]));
  };
  const normalizeOverrideConfig=(raw)=>{
    const next={...(raw||{})};
    Object.keys(next).forEach((k)=>{ if(next[k]) next[k]={...next[k], fab_source:normFabSource(next[k].fab_source)}; });
    return next;
  };
  const normalizeEnabledProducts=(enabledList, productList=products)=>{
    if(!Array.isArray(enabledList)||!enabledList.length)return null;
    const next=new Set(enabledList);
    (productList||[]).filter(p=>p.source_type==="base_file").forEach(p=>{if(p.name)next.add(p.name);});
    return next;
  };
  const loadSourceConfig=()=>sf(API+"/source-config").then(d=>{
    setEnabledSources(normalizeEnabledProducts(d.enabled));
    if(d.lot_overrides)setLotOverrides(normalizeOverrideConfig(d.lot_overrides));
    setMismatchMailEnabled(!!d.mismatch_mail_enabled);
    return d;
  }).catch(()=>({}));
  const toggleMismatchMail=(next)=>{
    const enabledForSave=enabledSources?[...enabledSources]:(products||[]).filter(p=>p.source_type==="base_file").map(p=>p.name).filter(Boolean);
    setMismatchMailSaveBusy(true);
    setMismatchMailEnabled(next);
    sf(API+"/source-config/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({enabled:enabledForSave,lot_overrides:lotOverrides||{},mismatch_mail_enabled:next})})
      .then(()=>{toast.ok(next?"불일치 알람 메일 발송 켜짐":"불일치 알람 메일 발송 꺼짐");return loadSourceConfig();})
      .catch(e=>{setMismatchMailEnabled(!next);toast.error("메일 발송 설정 저장 실패: "+(e?.message||e));})
      .finally(()=>setMismatchMailSaveBusy(false));
  };
  const reloadMlMatch=()=>{if(!selProd)return Promise.resolve();
    return sf(API+"/ml-table-match?product="+encodeURIComponent(selProd))
      .then(d=>setMlMatch(toMlMatch(d)))
      .catch(()=>{});
  };
  const persistLotOverrides=async(nextLotOverrides)=>{
    await sf(API+"/source-config/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({enabled:[...(enabledSources||new Set())],lot_overrides:nextLotOverrides||lotOverrides||{}})});
    await loadSourceConfig();
    await reloadMlMatch();
    if(loadView&&(lotId.trim()||fabLotId.trim())) loadView();
  };
  const saveSourceConfig=(enabled)=>{sf(API+"/source-config/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:[...enabled]})}).catch(()=>{});};
  const saveProductOrder=(next)=>{
    setProductOrderBusy(true);
    return sf(API+"/product-order",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product_order:next})})
      .then(d=>{
        const saved=d.product_order||next;
        setProductOrder(saved);
        setProducts(current=>orderProductItems(current,saved,item=>item.name));
        toast.ok("제품 선택 순서를 저장했습니다");
      })
      .catch(e=>toast.error("제품 순서 저장 실패: "+(e?.message||e)))
      .finally(()=>setProductOrderBusy(false));
  };
  useEffect(()=>{
    Promise.all([sf(API+"/products").catch(()=>({products:[]})),sf(API+"/source-config").catch(()=>({enabled:[]})),sf(API+"/prefixes").catch(()=>({prefixes:[]}))])
      .then(([prodRes,srcRes,prefRes])=>{
        const prods=prodRes.products||[];setProducts(prods);setProductOrder(prodRes.product_order||[]);
        const enabled=normalizeEnabledProducts(srcRes.enabled, prods);
        setEnabledSources(enabled);
        if(srcRes.lot_overrides) setLotOverrides(normalizeOverrideConfig(srcRes.lot_overrides));
        setMismatchMailEnabled(!!srcRes.mismatch_mail_enabled);
        // Set initial product to first visible source
        // 제품 노출 기준은 source-config 토글이 아니라 실제 ML_TABLE_* 파일 전체다.
        const visible=prods;
        const requested=String(initialProduct||"").trim().toUpperCase();
        const initialMatch=requested?prods.find(p=>String(p?.name||"").trim().toUpperCase()===requested):null;
        if(initialMatch)setSelProd(initialMatch.name);
        else if(visible.length)setSelProd(visible[0].name);
        else if(prods.length)setSelProd(prods[0].name);
        // flow-i 딥링크 (?product=PRODA&root=A1006) — 제품/root lot 프리필.
        const dl=deepLinkRef.current;
        if(dl&&dl.prod){
          const pu=dl.prod.toUpperCase();
          const target=prods.find(p=>{const n=String(p.name||"").toUpperCase();return n===pu||n===`ML_TABLE_${pu}`||n.endsWith(`_${pu}`);});
          if(target)setSelProd(target.name);
        }
        if(dl&&dl.root)setLotId(dl.root);
        setPrefixes(prefRes.prefixes||[]);
      });
    reloadCustoms();
    sf(API+"/precision").then(d=>{setPrecision(d.precision||{});setPrecisionDraft(d.precision||{});}).catch(()=>{});
  },[]);
  // v9.2.x: flow-i 딥링크 — URL query(product/root) 를 1회 소비해 자동 검색까지 수행.
  const deepLinkRef=useRef(null);
  if(deepLinkRef.current===null){
    let dl=false;
    try{
      const qs=new URLSearchParams(window.location.search||"");
      const root=(qs.get("root")||"").trim();
      const prod=(qs.get("product")||"").trim();
      if(root||prod)dl={root,prod,searched:false};
    }catch(_){/* noop */}
    deepLinkRef.current=dl;
  }
  useEffect(()=>{
    const dl=deepLinkRef.current;
    if(!dl||dl.searched||!selProd)return;
    if(dl.prod){
      const pu=dl.prod.toUpperCase();const su=String(selProd).toUpperCase();
      if(su!==pu&&su!==`ML_TABLE_${pu}`&&!su.endsWith(`_${pu}`))return; // 제품 프리필 대기
    }
    if(dl.root&&lotId!==dl.root)return; // root 프리필 대기
    dl.searched=true;
    if(dl.root)loadView();
  },[selProd,lotId]);
  const visibleProducts=products;
  const normalizeLotList=(values)=>[...new Set((values||[]).map(v=>String(v||"").trim()).filter(Boolean))];
  // 실제 ML_TABLE_* 파일 목록이 바뀌면 현재 선택이 유효한지만 확인한다.
  useEffect(()=>{
    if(selProd&&!products.some(p=>p.name===selProd)){
      if(visibleProducts.length)setSelProd(visibleProducts[0].name);
    }
  },[products]);
  // v9.3.x: 제품 선택 시 root lot id 전체 목록을 한 번만 서버에서 받아 lotPoolRef 에
  //   캐시한다(최대 ROOT_LOT_CACHE_LIMIT_MAX). 이후 사용자 키 입력은 로컬 필터만으로
  //   즉시 반영돼 429/대기 없이 후보를 보여준다. 서버 재요청은 제품 변경 시에만 발생.
  const lotPoolLoadedRef=useRef("");            // 풀이 완성된 제품명 (중복 요청 방지)
  const lotPoolControllerRef=useRef(null);      // 진행 중 AbortController
  const lotPoolStateRef=useRef("idle");         // loading|preparing|ready|empty|error
  useEffect(()=>{
    if(!selProd){lotPoolRef.current={key:"",values:[]};lotPoolLoadedRef.current="";lotPoolStateRef.current="idle";return;}
    if(lotPoolLoadedRef.current===selProd)return; // 이미 이 제품의 풀이 완성됨
    // 이전 제품 로드 중이면 취소
    if(lotPoolControllerRef.current)lotPoolControllerRef.current.abort();
    const ctrl=new AbortController();lotPoolControllerRef.current=ctrl;
    lotPoolRef.current={key:selProd,values:[]};  // 풀 초기화
    lotPoolStateRef.current="loading";
    const url=API+"/lot-candidates?product="+encodeURIComponent(selProd)+"&col=root_lot_id&limit="+ROOT_LOT_CACHE_LIMIT_MAX;
    let prepTimer=null;
    const MAX_PREP_RETRY=30;
    const fetchAll=(attempt)=>sf(url,{signal:ctrl.signal})
      .then(d=>{
        if(ctrl.signal.aborted)return;
        const candidates=normalizeLotList(d.candidates||[]);
        // v10.4.x: 서버가 lookup 인덱스 완성 전에도 파티션 스냅샷을 잠정 목록으로
        //   내려준다(`provisional`/`complete:false`). 바로 보여주되 완성본이 올
        //   때까지 재확인을 계속한다 — 여기서 loaded 로 굳히면 빌드 중 스냅샷이
        //   그대로 남아 새로 추가된 root 가 안 보인다.
        const isProvisional=d.provisional===true||d.complete===false;
        if(candidates.length){
          poolMerge(lotPoolRef,selProd,candidates);
          setLotPoolVer(v=>v+1);
          if(!isProvisional){
            lotPoolStateRef.current="ready";
            lotPoolLoadedRef.current=selProd;
            lotPoolControllerRef.current=null;
            return;
          }
          lotPoolStateRef.current="partial";
          if(attempt<MAX_PREP_RETRY){
            prepTimer=setTimeout(()=>{if(!ctrl.signal.aborted)fetchAll(attempt+1);},2000);
            return;
          }
          // 재확인 한도까지 완성본이 안 오면 잠정 목록을 그대로 쓴다.
          lotPoolStateRef.current="ready";
          lotPoolLoadedRef.current=selProd;
          lotPoolControllerRef.current=null;
          return;
        }
        const lc=d.lookup_cache||{};
        const preparing=lc.queued===true||lc.status==="queued"||lc.status==="running"||d.match_mode==="lookup_cache_preparing";
        if(preparing&&attempt<MAX_PREP_RETRY){
          // 원천/FAB 폴백은 사용자 요청에서 실행하지 않는다. lookup worker가
          // 목록을 게시할 때까지 화면을 막지 않는 상태로 조용히 재확인한다.
          lotPoolStateRef.current="preparing";
          setLotPoolVer(v=>v+1);
          prepTimer=setTimeout(()=>{if(!ctrl.signal.aborted)fetchAll(attempt+1);},2000);
          return;
        }
        lotPoolStateRef.current=preparing?"preparing":"empty";
        lotPoolControllerRef.current=null;
        setLotPoolVer(v=>v+1);
      })
      .catch(e=>{if(e?.name==="AbortError")return;lotPoolStateRef.current="error";lotPoolControllerRef.current=null;setLotPoolVer(v=>v+1);});
    fetchAll(0);
    return()=>{ctrl.abort();if(prepTimer)clearTimeout(prepTimer);};
  },[selProd]);
  // v9.3.x: 키 입력 → 로컬 풀 즉시 필터. 서버 재요청 없음.
  useEffect(()=>{
    const seq=++lotSuggestSeqRef.current;
    if(!selProd){setLotSuggestions([]);setLotSuggestMsg("");return;}
    const prefix=(lotId||"").trim();
    const limit=candidateLimit(prefix);
    const pooled=poolValues(lotPoolRef,selProd);
    // 네트워크 왕복 중에만 로딩을 보인다. lookup 빌드 중에는 직접 입력/검색을
    // 막지 않는 안내로 바꾸고, 백그라운드 재확인 후 자동 갱신한다.
    if(!pooled.length){
      const poolState=lotPoolStateRef.current;
      setLotSuggestMsg(poolState==="loading"
        ?"Lot 후보 캐시 확인 중입니다. 직접 입력해 바로 조회할 수 있습니다."
        :poolState==="preparing"
        ?"Lot 후보 캐시 준비 중입니다. 직접 입력해 바로 조회할 수 있습니다."
        :poolState==="error"?"Lot 후보 캐시를 불러오지 못했습니다. 직접 입력해 조회해 주세요."
        :poolState==="empty"?"Lot 후보가 없습니다.":"");
      setLotSuggestions([]);
      return;
    }
    const local=prefix?pooled.filter(v=>v.toUpperCase().includes(prefix.toUpperCase())):pooled;
    setLotSuggestions(local.slice(0,limit));
    setLotSuggestMsg(local.length?"":"Lot 후보가 없습니다.");
  },[selProd,lotId,lotPoolVer]);
  // v9.0.0: 제품 변경 시 lotId/fabLotId/waferIds 초기화 — 직전 제품의 lot 이 남아 잘못된 필터링 방지.
  //   (예: PRODA 의 A1000A.1_V1 이 PRODB 로 전환 후에도 fab_lot_id 칸에 남아 있으면 B0001 root 와 어긋나는 조합 생성).
  const _prevProd = useRef(selProd);
  useEffect(()=>{
    if (_prevProd.current && _prevProd.current !== selProd) {
      setLotId("");
      setFabLotId("");
      setWaferIds("");
      setLotFilter("");
      setShowLotDrop(false);
      setShowFabDrop(false);
      setData(null);  // 이전 제품 뷰 날려서 오해 방지
    }
    _prevProd.current = selProd;
  }, [selProd]);
  // v8.8.16: 제품 전체 스키마 fetch — lot 조회와 무관하게 CUSTOM 컬럼 선택 pool 제공.
  //   all_columns 는 현재 검색된 lot 의 df.columns 기반이라 lot 검색 전에는 비어있음.
  //   스키마는 lot 검색 없이도 가져올 수 있어 CUSTOM 모드에서 자유롭게 컬럼을 고를 수 있다.
  const[productSchema,setProductSchema]=useState([]);
  // v8.8.23: override_cols_present — 오버라이드에서 실제 join 된 컬럼 목록.
  //   CUSTOM pool 의 `_CUSTOM_HIDDEN` 기본 숨김 목록에서 예외 처리 → 검색/필터 드롭다운에 노출.
  const[overrideCols,setOverrideCols]=useState([]);
  useEffect(()=>{
    if(!selProd){setProductSchema([]);setOverrideCols([]);setCustomTags([]);return;}
    reloadCustomTags();
    sf(API+"/schema?product="+encodeURIComponent(selProd))
      .then(d=>{
        setProductSchema((d.columns||[]).map(c=>c.name||c));
        setOverrideCols(Array.isArray(d.override_cols_present)?d.override_cols_present:[]);
      })
      .catch(()=>{setProductSchema([]);setOverrideCols([]);});
  },[selProd]);
  // v8.4.7: 제품 바뀔 때 KNOB meta 재fetch.
  useEffect(()=>{if(!selProd){setKnobMeta({});return;}
    sf(API+"/knob-meta?product="+encodeURIComponent(selProd))
      .then(d=>setKnobMeta(d.features||{})).catch(()=>setKnobMeta({}));
  },[selProd]);
  // v8.8.7: VM meta fetch — VM_ parameter 아래 step_id/step_desc 노출용.
  const[vmMeta,setVmMeta]=useState({});
  useEffect(()=>{
    sf(API+"/vm-meta"+(selProd?("?product="+encodeURIComponent(selProd)):""))
      .then(d=>setVmMeta(d.items||{})).catch(()=>setVmMeta({}));
  },[selProd]);
  // v8.8.15: INLINE meta — INLINE_<item_id> row 의 step_id sub-label 용.
  const[inlineMetaSt,setInlineMetaSt]=useState({});
  useEffect(()=>{
    sf(API+"/inline-meta"+(selProd?("?product="+encodeURIComponent(selProd)):""))
      .then(d=>setInlineMetaSt(d.items||{})).catch(()=>setInlineMetaSt({}));
  },[selProd]);
  // 헤더의 빨간 핀은 LOT 관리 테이블을 정본으로 사용한다. 예전에는 별도
  // 캐시관리의 주요 Lot purpose를 읽어 같은 위치에 표시해 두 화면의 값이 달랐다.
  const lotManagementPurposeHit=(()=>{
    const purposes=Array.isArray(data?.lot_management_purposes)
      ? data.lot_management_purposes.filter(item=>String(item?.lot_id||"").trim()&&String(item?.purpose||"").trim())
      : [];
    const findExact=(raw)=>{
      const key=String(raw||"").trim().toLowerCase();
      return key?purposes.find(item=>String(item.lot_id||"").trim().toLowerCase()===key):null;
    };
    for(const raw of [fabLotId,lotId,data?.fab_lot_id,data?.lot_id]){
      const hit=findExact(raw);
      if(hit)return hit;
    }
    // root lot 검색 결과에 대응하는 LOT 관리 행이 하나뿐이면 그 목적을 표시한다.
    return purposes.length===1?purposes[0]:null;
  })();
  // v9.0.4: 이름이 같거나 prefix/casing 만 다른 경우도 soft-landing 으로 자동 매칭.
  const metaLookup=(metaMap, param, prefix)=>{
    if(!param||!metaMap) return null;
    const full=String(param||"").trim();
    const tail=full.replace(new RegExp(`^${prefix}_`,"i"),"").trim();
    if(metaMap[full]) return metaMap[full];
    if(metaMap[tail]) return metaMap[tail];
    const fullLower=full.toLowerCase();
    const tailLower=tail.toLowerCase();
    const hitKey=Object.keys(metaMap).find(k=>{
      const key=String(k||"").trim().toLowerCase();
      return key===fullLower||key===tailLower;
    });
    return hitKey?metaMap[hitKey]:null;
  };
  const knobLookup=(param)=>metaLookup(knobMeta,param,"KNOB");
  const vmLookup=(param)=>metaLookup(vmMeta,param,"VM");
  const inlineLookup=(param)=>metaLookup(inlineMetaSt,param,"INLINE");
  // 항목명 prefix → 매칭 종류. 적용공정 표기/모달/행 숨김이 같은 판정을 쓴다.
  const matchKindOf=(param)=>{
    const u=String(param||"").trim().toUpperCase();
    if(u.startsWith("KNOB_")||u==="KNOB")return "knob_ppid";
    if(u.startsWith("INLINE_")||u==="INLINE")return "inline_matching";
    if(u.startsWith("VM_")||u==="VM")return "vm_matching";
    return null;
  };
  const matchMetaFor=(kind,param)=>(
    kind==="knob_ppid"?knobLookup(param)
    :kind==="inline_matching"?inlineLookup(param)
    :kind==="vm_matching"?vmLookup(param)
    :null
  );
  const buildLineageSummary=(rows)=>{
    const out=[];
    const seen=new Set();
    (rows||[]).forEach((row)=>{
      const param=String(row?._param||"");
      if(!param) return;
      const paramKey=param.toLowerCase();
      if(seen.has(paramKey))return;
      const km=knobLookup(param);
      if(Array.isArray(km?.groups)&&km.groups.length){
        const summary=knobLineageRow(param,km.groups,{excludeNotNull:excludeNotNullStepMeta});
        if(summary){out.push(summary);seen.add(paramKey);}
      }
    });
    return out;
  };
  // v8.8.10: Rulebook 컬럼 매핑 schema — admin 이 역할→실제컬럼명 조정 가능.
  const[rbSchema,setRbSchema]=useState({schema:{},defaults:{}});
  const[rbEditKind,setRbEditKind]=useState(null);   // "knob_ppid"|"step_matching"|"inline_matching"|"vm_matching"|null
  const[rbDraftMap,setRbDraftMap]=useState({});
  const[rbFileDrafts,setRbFileDrafts]=useState({});
  const reloadRbSchema=()=>sf(API+"/rulebook/schema").then(d=>setRbSchema({schema:d.schema||{},defaults:d.defaults||{}})).catch(()=>{});
  // v8.8.13-fix: 이전에는 `useEffect(reloadRbSchema,[])` 였는데 reloadRbSchema 가 Promise 를 반환하는 함수라
  // React 가 그 Promise 를 cleanup 로 저장 → unmount 시 Promise() 호출 → "n is not a function" 흰 화면 튕김.
  // 화살표로 감싸 void 반환으로 변경.
  useEffect(()=>{reloadRbSchema();},[]);
  useEffect(()=>{
    const next={};
    Object.keys(rbSchema.defaults||{}).forEach(kind=>{
      const current={...(rbSchema.defaults?.[kind]||{}),...(rbSchema.schema?.[kind]||{})};
      next[kind]=current.file_name||"";
    });
    setRbFileDrafts(next);
  },[rbSchema]);
  const rulebookMap=(kind)=>({...(rbSchema.defaults?.[kind]||{}),...(rbSchema.schema?.[kind]||{})});
  const rulebookFileName=(kind,fallback="")=>String(rulebookMap(kind).file_name||fallback||"").trim();
  const saveRulebookMapping=(kind,mapping)=>{
    if(!kind)return Promise.reject(new Error("kind is required"));
    return sf(API+"/rulebook/schema/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({kind,mapping,username:user?.username||""})})
      .then(()=>{reloadRbSchema();loadView&&loadView();});
  };
  const openSchemaEditor=(kind)=>{setRbEditKind(kind);setRbDraftMap({...(rbSchema.schema?.[kind]||rbSchema.defaults?.[kind]||{})});};
  const saveSchemaEdit=()=>{if(!rbEditKind)return;
    saveRulebookMapping(rbEditKind,rbDraftMap)
      .then(()=>{setRbEditKind(null);reloadRbSchema();loadView&&loadView();})
      .catch(e=>toast.error("저장 실패: "+e.message));
  };
  const saveRulebookFileName=(kind)=>{
    const filename=String(rbFileDrafts[kind]||"").trim();
    if(!filename){toast.warn("파일명을 입력하세요.");return;}
    saveRulebookMapping(kind,{...rulebookMap(kind),file_name:filename})
      .then(()=>toast.ok("파일명 매칭 저장됨"))
      .catch(e=>toast.error("파일명 저장 실패: "+(e?.message||e)));
  };
  // v9.0.5: KNOB/INLINE/VM Index 클릭 시 매칭 규칙 미리보기 모달.
  const[rbMatchKind,setRbMatchKind]=useState(null); // "knob_ppid" | "inline_matching" | "vm_matching" | null
  const[rbMatchParam,setRbMatchParam]=useState("");
  const[rbMatchRow,setRbMatchRow]=useState(null);
  const openRuleMatchView=(kind,param,row=null)=>{
    setRbMatchKind(kind);
    setRbMatchParam(String(param || "").trim());
    setRbMatchRow(kind==="knob_ppid"&&row?row:null);
  };
  const closeRuleMatchView=()=>{setRbMatchKind(null);setRbMatchParam("");setRbMatchRow(null);};
  const parseCsvTokens=(value)=>String(value||"").split(",").map(s=>s.trim()).filter(Boolean);
  const rbMatchData = (() => {
    const p = String(rbMatchParam || "").trim();
    if (rbMatchKind === "knob_ppid") return knobLookup(p) || null;
    if (rbMatchKind === "inline_matching") return inlineLookup(p) || null;
    if (rbMatchKind === "vm_matching") return vmLookup(p) || null;
    return null;
  })();
  const rbMatchTitle = rbMatchKind === "knob_ppid" ? "KNOB"
    : rbMatchKind === "inline_matching" ? "INLINE"
    : rbMatchKind === "vm_matching" ? "VM"
    : "";
  // fab_lot_id 후보도 fetch (lot-candidates 엔드포인트 사용)
  // v9.0.2: fabLotId 입력값도 서버 prefix 로 전송 — 초기 500개 밖의 fab_lot_id 도 검색 가능.
  // v9.0.1: lotId 가 비어있지 않으면 root_lot_id scope 전송 — BE 가 데이터-중심 join
  //   (root_lot_id == lotId 인 row 의 fab_lot_id) 으로 매칭, 0건이면 starts_with → 전체 폴백.
  //   기존 'lotId.length===5' 분기는 시드 데이터에서 root/fab 앞 5자가 다른 케이스를 못 잡았음.
  useEffect(()=>{
    const seq=++fabSuggestSeqRef.current;
    if(!selProd){setFabSuggestions([]);setFabSuggestBusy(false);setFabSuggestMsg("");return;}
    const controller=new AbortController();
    const isCurrent=()=>seq===fabSuggestSeqRef.current&&!controller.signal.aborted;
    const _r=(lotId||"").trim();
    const _f=(fabLotId||"").trim();
    const limit=candidateLimit(_f||_r);
    let url=API+"/lot-candidates?product="+encodeURIComponent(selProd)+"&col=fab_lot_id&limit="+limit;
    if(_r) url+="&root_lot_id="+encodeURIComponent(_r);
    if(_f) url+="&prefix="+encodeURIComponent(_f);
    setFabSuggestBusy(true);setFabSuggestMsg("");
    // 키 입력 즉시: 같은 product+root scope 의 누적 풀을 로컬 필터해 먼저 표시.
    const poolKey=selProd+"|"+_r.toUpperCase();
    const pooled=poolValues(fabPoolRef,poolKey);
    if(pooled.length){
      const local=_f?pooled.filter(v=>v.toUpperCase().includes(_f.toUpperCase())):pooled;
      if(local.length){setFabSuggestions(local.slice(0,limit));setFabSuggestMsg("");}
    }
    const timer=setTimeout(()=>sf(url,{signal:controller.signal}).then(d=>{
      if(!isCurrent())return;
      const rows=normalizeLotList(d.candidates||[]);
      poolMerge(fabPoolRef,poolKey,rows);
      setFabSuggestions(rows);
      setFabSuggestMsg(rows.length?"":"Fab lot 후보가 없습니다. root_lot_id 범위 또는 DB 연결을 확인하세요.");
    }).catch(e=>{if(!isCurrent()||e?.name==="AbortError")return;setFabSuggestions([]);setFabSuggestMsg(e?.message||"Fab lot 후보 조회 실패");})
      .finally(()=>{if(isCurrent())setFabSuggestBusy(false);}),200);
    return()=>{clearTimeout(timer);controller.abort();};
  },[selProd,lotId,fabLotId]);
  useEffect(()=>{const h=e=>{if(lotRef.current&&!lotRef.current.contains(e.target))setShowLotDrop(false);};document.addEventListener("mousedown",h);return()=>document.removeEventListener("mousedown",h);},[]);
  useEffect(()=>{if(!editing)clearCellSelection();},[editing]);
  useEffect(()=>{clearCellSelection();},[data]);

  const prefixParam=isCustomMode?"":selPrefixes.join(",");
  // "적용 공정 정보" 를 켠 채 받은 CSV/XLSX 는 화면과 같은 표기여야 한다 —
  // 항목명 대신 연결 공정, 표시할 공정이 없는 매칭 행은 제외.
  const stepLabelQ=showParamMeta
    ?`&step_labels=1&exclude_not_null=${excludeNotNullStepMeta?1:0}`
    :"";
  const splitCheckDisabled=(!isCustomMode&&selPrefixes.some(isInlineVmSplitParam))
    ||(isCustomMode&&customCols.some(isInlineVmSplitParam))
    ||(Array.isArray(data?.rows)&&data.rows.some(row=>isInlineVmSplitParam(row?._param)||isInlineVmSplitParam(row?._display)));
  const pemsRootOnly=Boolean(lotId.trim())&&!fabLotId.trim();
  const pemsDisabled=!pemsRootOnly||splitCheckDisabled;
  const splitCheckViewActive=showSplitCheckView&&!splitCheckDisabled;
  const pemsViewActive=showPemsView&&!pemsDisabled&&!splitCheckViewActive;
  const mergedViewActive=showMergedView&&!splitCheckViewActive&&!pemsViewActive;
  // 표시 형식 4종: cell / split / merged / PEMS(root lot 전용 1..25 고정).
  const tableFormat=splitCheckViewActive?"split":(pemsViewActive?"pems":(mergedViewActive?"merged":"cell"));
  const setTableFormat=(m)=>{
    if(m==="split"){if(splitCheckDisabled)return;setShowMergedView(false);setShowPemsView(false);setShowSplitCheckView(true);return;}
    if(m==="pems"){
      if(pemsDisabled)return;
      setShowMergedView(false);setShowSplitCheckView(false);setShowPemsView(true);
      // PEMS는 wafer 필터와 무관하게 물리 wafer 1..25 전체를 사용한다.
      if(waferIds.trim()){
        setWaferIds("");
        if(data) setTimeout(()=>loadView({waferIds:""}),0);
      }
      return;
    }
    setShowSplitCheckView(false);
    setShowPemsView(false);
    setShowMergedView(m==="merged");
  };
  const splitCheckToggleTitle=splitCheckDisabled
    ?"INLINE/VM 항목은 wafer별 Split 체크 표시 대상이 아닙니다"
    :"각 항목 값을 S0/S1 체크 행으로 펼쳐 wafer별 적용 위치를 봅니다";
  const TABLE_FORMAT_OPTIONS=[
    {k:"cell",l:"기본",t:"모든 행/열을 개별 칸으로 표시"},
    {k:"split",l:"Split 체크",t:splitCheckToggleTitle,d:splitCheckDisabled},
    {k:"merged",l:"병합",t:"KNOB/FAB/MASK 행에서 왼쪽 값과 같은 칸을 하나로 병합해 표시 (읽기 전용). INLINE/VM/TAG 는 wafer별 값이라 병합하지 않습니다."},
    {k:"pems",l:"PEMS",t:!pemsRootOnly?"PEMS는 lot_id가 아닌 root_lot_id 단독 조회에서만 사용할 수 있습니다":"wafer 1~25를 고정 표시하고 값별 S0/S1 그룹을 직접 표기합니다. 없는 wafer는 S0에 회색으로 표시합니다.",d:pemsDisabled},
  ];
  useEffect(()=>{
    if(splitCheckDisabled&&showSplitCheckView)setShowSplitCheckView(false);
  },[splitCheckDisabled,showSplitCheckView]);
  useEffect(()=>{
    if(pemsDisabled&&showPemsView)setShowPemsView(false);
  },[pemsDisabled,showPemsView]);
  const expireSessionFromPreflight=()=>{
    try{localStorage.removeItem("hol_user");}catch(_e){}
    window.dispatchEvent(new Event("flow:session-expired"));
  };
  const ensureSessionForSearch=async()=>{
    const me=await sf("/api/auth/me");
    if(!me?.authenticated||!me?.username){
      expireSessionFromPreflight();
      throw new Error("Session expired — please log in again");
    }
    return me;
  };
  const loadRelatedIssuesForView=(viewData)=>{
    const prod=String(viewData?.product||selProd||"").trim();
    const root=String(viewData?.root_lot_id||lotId||"").trim();
    if(!prod||!root)return;
    const url=API+"/related-issues?product="+encodeURIComponent(prod)+"&root_lot_id="+encodeURIComponent(root);
    sf(url).then(d=>{
      const issues=Array.isArray(d.related_issues)?d.related_issues:[];
      setData(prev=>{
        if(!prev)return prev;
        const sameProd=String(prev.product||selProd||"").trim()===prod;
        const sameRoot=String(prev.root_lot_id||lotId||"").trim()===root;
        return sameProd&&sameRoot?{...prev,related_issues:issues}:prev;
      });
    }).catch(()=>{});
  };
  const loadLotManagementPurposesForView=(viewData)=>{
    const prod=String(viewData?.product||selProd||"").trim();
    const root=String(viewData?.root_lot_id||lotId||"").trim();
    const lotIds=[...new Set([
      ...(Array.isArray(viewData?.available_fab_lots)?viewData.available_fab_lots:[]),
      viewData?.fab_lot_id,viewData?.lot_id,fabLotId,lotId,
    ].map(v=>String(v||"").trim()).filter(Boolean))].slice(0,200);
    if(!prod||!root||!lotIds.length)return;
    const url="/api/lot-management/purposes?product="+encodeURIComponent(prod)+"&lot_ids="+encodeURIComponent(lotIds.join(","));
    sf(url).then(d=>{
      const purposes=Array.isArray(d.purposes)?d.purposes:[];
      setData(prev=>{
        if(!prev)return prev;
        const sameProd=String(prev.product||selProd||"").trim()===prod;
        const sameRoot=String(prev.root_lot_id||lotId||"").trim()===root;
        return sameProd&&sameRoot?{...prev,lot_management_purposes:purposes}:prev;
      });
    }).catch(()=>{});
  };
  // diff 모드는 클라이언트에서 즉시 필터 → 항상 "all" 로 fetch
  // v9.0.3: 한 root_lot_id 아래 여러 fab_lot_id 가 정상이다.
  // FAB 공정 진행 중 fab_lot_id 가 바뀔 수 있으므로 앞 5자 일치 검증으로 검색을 막지 않는다.
  const loadView=(opts={})=>{if(!selProd||(!lotId.trim()&&!fabLotId.trim()))return;
    const prepAttempt=Number(opts._prepAttempt||0);
    const searchSeq=opts._searchSeq||(++viewSearchSeqRef.current);
    if(opts._searchSeq&&searchSeq!==viewSearchSeqRef.current)return;
    if(prepAttempt===0&&viewRetryTimerRef.current){clearTimeout(viewRetryTimerRef.current);viewRetryTimerRef.current=null;}
    const effectiveCustomMode=opts.customMode ?? isCustomMode;
    const effectiveCustomCols=cleanCustomColumns(opts.customCols ?? customCols);
    const effectiveCustomName=cleanCustomName(opts.customName ?? selCustom);
    const effectivePrefixParam=effectiveCustomMode?"":selPrefixes.join(",");
    const effectiveWaferIds=opts.waferIds ?? waferIds;
    let url=API+"/view?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(effectiveWaferIds)+"&prefix="+encodeURIComponent(effectivePrefixParam)+"&view_mode=all&history_mode=all";
    if(prepAttempt>0)url+="&cache_first=true";
    if(fabLotId.trim())url+="&fab_lot_id="+encodeURIComponent(fabLotId.trim());
    // v8.8.33: Save 없이 체크만 한 ad-hoc customCols 우선 — set name 은 보조.
    if(effectiveCustomMode&&effectiveCustomCols.length>0)url+="&custom_cols="+encodeURIComponent(effectiveCustomCols.join(","));
    else if(effectiveCustomMode&&effectiveCustomName)url+="&custom_name="+encodeURIComponent(effectiveCustomName);
    let loadingStarted=false;let retryScheduled=false;
    ensureSessionForSearch().then(()=>{
      if(searchSeq!==viewSearchSeqRef.current)return null;
      if(prepAttempt===0){loadingStarted=true;setLoading(true);}
      return sf(url);
    }).then(raw=>{
      if(!raw||searchSeq!==viewSearchSeqRef.current)return;
      const d=expandViewRows(raw);
      // LOT 관리 임베드에서는 fab_lot_id로 진입한다. plan 저장 API는 canonical
      // root_lot_id를 사용하므로 조회 응답에서 확정된 root를 편집 상태에 반영한다.
      if(embedded&&d.root_lot_id&&!lotId)setLotId(String(d.root_lot_id));
      const lc=d.lookup_cache||{};
      const preparing=!d.rows?.length&&(lc.queued===true||lc.status==="queued"||lc.status==="running");
      if(preparing&&prepAttempt<240){
        setData(d);setLoading(false);retryScheduled=true;
        viewRetryTimerRef.current=setTimeout(()=>loadView({...opts,_prepAttempt:prepAttempt+1,_searchSeq:searchSeq}),1500);
        return;
      }
      setData(d);
      if(d.precision)setPrecision(d.precision);
      // v9.0.1: 응답에 동봉된 같은 root 의 fab_lot_id 들로 콤보박스 자동 채움 —
      //   별도 lot-candidates 호출 없이 즉시 보임. 빈 배열이면 기존 fabSuggestions 유지.
      if(Array.isArray(d.available_fab_lots)&&d.available_fab_lots.length>0){
        setFabSuggestions(d.available_fab_lots);
      }
      setPendingPlans({});setPendingTags({});setPendingManagement({});clearCellSelection();reloadNotes();
      loadRelatedIssuesForView(d);
      loadLotManagementPurposesForView(d);
      const backgroundPreparing=d.background_cache?.queued===true;
      if(backgroundPreparing&&prepAttempt<60){
        setLoading(false);retryScheduled=true;
        viewRetryTimerRef.current=setTimeout(()=>loadView({...opts,_prepAttempt:prepAttempt+1,_searchSeq:searchSeq}),10000);
      }
    }).catch(e=>{
      if(searchSeq!==viewSearchSeqRef.current)return;
      if(prepAttempt>0&&prepAttempt<240){
        retryScheduled=true;
        viewRetryTimerRef.current=setTimeout(()=>loadView({...opts,_prepAttempt:prepAttempt+1,_searchSeq:searchSeq}),2000);
        return;
      }
      if(String(e?.message||"").includes("Session expired"))return;
      toast.error(e.message);
    }).finally(()=>{if(loadingStarted&&!retryScheduled)setLoading(false);});};
  // v8.4.9-b: Notes reload — 로트가 정해지면 해당 로트 범위로 가져옴.
  const reloadNotes=()=>{const prod=selProd, lot=lotId;if(!prod||!lot){setNotes([]);return;}
    sf(API+"/notes?product="+encodeURIComponent(prod)+"&root_lot_id="+encodeURIComponent(lot))
      .then(d=>setNotes(d.notes||[])).catch(()=>setNotes([]));};
  const embeddedSearchRef=useRef("");
  useEffect(()=>{
    if(!embedded||!selProd||!initialFabLotId)return;
    const requested=String(initialProduct||"").trim().toUpperCase();
    if(requested&&String(selProd).trim().toUpperCase()!==requested)return;
    const key=[selProd,initialFabLotId,initialCustomName].join("|");
    if(embeddedSearchRef.current===key)return;
    embeddedSearchRef.current=key;
    setFabLotId(initialFabLotId);
    setSelCustom(initialCustomName||"");
    setIsCustomMode(!!initialCustomName);
    const timer=setTimeout(()=>loadView({customMode:!!initialCustomName,customName:initialCustomName||"",customCols:[]}),0);
    return()=>clearTimeout(timer);
  },[embedded,selProd,initialProduct,initialFabLotId,initialCustomName]);
  const normalizeNoteFile=(f,i=0)=>{
    const extFromType=(f?.type||"").split("/")[1]||"png";
    const hasExt=/\.[A-Za-z0-9]{2,5}$/.test(f?.name||"");
    return hasExt?f:new File([f],`note_${Date.now()}_${i}.${extFromType}`,{type:f?.type||"image/png"});
  };
  const uploadNoteFiles=async(files)=>{
    const list=Array.from(files||[]).filter(f=>/^image\//.test(f?.type||""));
    if(!list.length)return;
    setNoteUploading(true);
    const uploaded=[];
    for(let i=0;i<list.length;i++){
      try{
        const fd=new FormData();
        fd.append("file",normalizeNoteFile(list[i],i));
        const res=await sf("/api/informs/upload",{method:"POST",body:fd});
        uploaded.push({filename:res.filename,url:res.url,size:res.size});
      }catch(e){toast.error("이미지 업로드 실패: "+(e.message||e));}
    }
    if(uploaded.length)setNoteImages(prev=>[...prev,...uploaded].slice(0,12));
    setNoteUploading(false);
  };
  const handleNotePaste=(e)=>{
    const items=e.clipboardData?.items||[];
    const files=[];
    for(const it of items){
      if(it.kind==="file"&&/^image\//.test(it.type||"")){
        const f=it.getAsFile();
        if(f)files.push(f);
      }
    }
    if(!files.length)return;
    e.preventDefault();
    uploadNoteFiles(files);
  };
  const clearNoteDraft=()=>{setNoteDraft("");setNoteImages([]);setNoteDraftScope(null);};
  const addNote=()=>{const txt=(noteDraft||"").trim();const sc=noteDraftScope;if((!txt&&noteImages.length===0)||!sc)return;
    sf(API+"/notes/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...sc,text:txt,images:noteImages,username:user?.username||""})})
      .then(()=>{setNoteDraft("");setNoteImages([]);reloadNotes();}).catch(e=>toast.error("노트 저장 실패: "+e.message));};
  const deleteNote=(id)=>{if(!confirm("삭제?"))return;
    sf(API+"/notes/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,username:user?.username||""})})
      .then(()=>reloadNotes()).catch(e=>toast.error("삭제 실패: "+e.message));};
  const notesForWafer=(wid)=>notes.filter(n=>n.scope==="wafer"&&n.key===`${selProd}__${lotId}__W${wid}`);
  const notesForParam=(param)=>notes.filter(n=>n.scope==="param"&&n.key.endsWith(`__${param}`)&&n.key.startsWith(`${selProd}__${lotId}__W`));
  // v8.4.9-c: 특정 (wafer × param) 셀용 메모 — 행/열 교차 단위.
  const notesForCell=(wid,param)=>notes.filter(n=>n.scope==="param"&&n.key===`${selProd}__${lotId}__W${wid}__${param}`);
  // v8.7.8: parameter 전역 태그 (product 내 모든 LOT 공통) + LOT 노트
  const notesForLot=()=>notes.filter(n=>n.scope==="lot"&&n.key===`${selProd}__LOT__${lotId}`);
  const doSearch=()=>loadView();
  const openTrackerIssue=(issueId)=>{
    const iid=String(issueId||"").trim();
    if(!iid)return;
    const search=`?issue_id=${encodeURIComponent(iid)}`;
    window.dispatchEvent(new CustomEvent("flow:navigate",{detail:{tab:"tracker",search}}));
  };
  const isLotHistoryMode=(mode)=>mode==="lot_all"||mode==="lot_final";
  const isFinalHistoryMode=(mode)=>mode==="lot_final"||mode==="all_final";
  const normalizeHistoryMode=(mode)=>{
    const next=mode||"lot_final";
    if(isLotHistoryMode(next)&&!lotId.trim()) return next==="lot_all"?"all":"all_final";
    return next;
  };
  // 필터는 서버가 전체 아카이브 위에서 건다 — 화면에 실린 페이지만 걸러 봐야
  // "1000건 넘게 지난 변경"을 못 찾으므로 클라이언트 필터로는 안 된다.
  const historyQuery=(mode,filter,limit)=>{
    const p=new URLSearchParams({product:selProd,limit:String(limit||HIST_PAGE)});
    if(mode==="lot_all"&&lotId.trim())p.set("root_lot_id",lotId.trim());
    // 불리언 필터는 켜졌을 때만 실어 보낸다 ("false" 문자열이 붙지 않게).
    Object.entries(filter||{}).forEach(([k,v])=>{
      if(typeof v==="boolean"){if(v)p.set(k,"true");return;}
      const s=String(v??"").trim();if(s)p.set(k,s);
    });
    return p.toString();
  };
  const loadHistory=(mode,filter,limit)=>{
    const next=normalizeHistoryMode(mode);
    const f=filter||histFilter, n=limit||histLimit;
    setHistLoading(true);
    sf(API+"/history?"+historyQuery(next,f,n))
      .then(d=>{
        setHistory(d.history||[]);
        setHistFacets(d.facets||{users:[],actions:[],columns:[]});
        setHistMeta({total:d.total||0,scope_total:d.scope_total||0,has_more:!!d.has_more});
      })
      .catch(e=>toast.error("이력 조회 실패: "+(e.message||"")))
      .finally(()=>setHistLoading(false));
  };
  const applyHistFilter=(patch,debounce)=>{
    const next={...histFilter,...patch};
    setHistFilter(next);setHistLimit(HIST_PAGE);setHistOpenBatch({});
    if(histDebounceRef.current)clearTimeout(histDebounceRef.current);
    if(debounce)histDebounceRef.current=setTimeout(()=>loadHistory(histMode,next,HIST_PAGE),400);
    else loadHistory(histMode,next,HIST_PAGE);
  };
  const resetHistFilter=()=>applyHistFilter({user:"",action:"",column:"",wafer_id:"",q:"",since:"",until:"",has_reason:false});
  const histFilterActive=Object.values(histFilter).some(v=>String(v||"").trim());
  const loadMoreHistory=()=>{const n=histLimit+HIST_PAGE;setHistLimit(n);loadHistory(histMode,histFilter,n);};
  const loadHistoryFinal=(mode)=>{const next=normalizeHistoryMode(mode);let url=API+"/history/final?product="+encodeURIComponent(selProd);if(next==="lot_final"&&lotId.trim())url+="&root_lot_id="+encodeURIComponent(lotId.trim());sf(url).then(d=>setHistFinal({final:d.final||[],drift:d.drift||[],drift_count:d.drift_count||0,total_cells:d.total_cells||0}));};
  const loadHistoryByMode=(mode)=>{const next=normalizeHistoryMode(mode);setHistMode(next);setHistOpenBatch({});if(isFinalHistoryMode(next))loadHistoryFinal(next);else loadHistory(next,histFilter,histLimit);};
  // 표시용 파생 — 최신 먼저, batch(한 번의 저장) 단위로 접기.
  const histRowParts=(h)=>{
    const parts=String(h?.cell||"").split("|");
    return{
      root:h?.root_lot_id||parts[0]||"",
      wafer:h?.wafer_id||parts[1]||"",
      column:h?.column||parts.slice(2).join("|")||h?.cell||"",
    };
  };
  const histTime=(t)=>String(t||"").slice(0,19).replace("T"," ");
  const historyGroups=(()=>{
    const rows=[...history].reverse();
    if(!histGroup)return rows.map((h,i)=>({gid:"r"+i,rows:[h]}));
    const out=[];
    rows.forEach(h=>{
      const gid=h?.batch||`${h?.time}|${h?.user}|${h?.action}`;
      const last=out[out.length-1];
      if(last&&last.gid===gid)last.rows.push(h);
      else out.push({gid,rows:[h]});
    });
    return out;
  })();
  const histActionStyle=(action)=>action==="delete"
    ?{background:"rgba(239,68,68,0.13)",color:"rgba(239,68,68,0.95)"}
    :{background:"rgba(249,115,22,0.13)",color:"rgba(249,115,22,0.95)"};
  const renderHistRow=(h,key,indent)=>{
    const{root,wafer,column}=histRowParts(h);
    const isDel=h?.action==="delete";
    const oldVal=hasValue(h?.old)?String(h.old):"";
    const newVal=hasValue(h?.new)?String(h.new):"";
    // "어떻게 바꿨는지"가 한 눈에 보이도록 old→new 를 한 칸에 붙여 보여준다.
    return(<tr key={key} style={indent?{background:"var(--bg-hover, rgba(127,127,127,0.04))"}:undefined}>
      <td style={{...HIST_TD,color:"var(--text-secondary)",whiteSpace:"nowrap",paddingLeft:indent?26:10}} title={h?.time||""}>{histTime(h?.time)}</td>
      <td style={{...HIST_TD,fontWeight:600}} title={h?.prev_user&&h.prev_user!==h?.user?`이전 plan 작성자: ${h.prev_user}`:""}>
        {h?.user||"-"}
        {h?.prev_user&&h.prev_user!==h?.user?<span style={{marginLeft:6,fontSize:14,fontWeight:400,color:"var(--text-secondary)"}}>← {h.prev_user}</span>:null}
      </td>
      <td style={{...HIST_MONO,color:"var(--accent)"}}>{root||"-"}</td>
      <td style={HIST_MONO}>{wafer||"-"}</td>
      <td style={{...HIST_MONO,maxWidth:220,overflow:"hidden",textOverflow:"ellipsis"}} title={column}>{column||"-"}</td>
      <td style={HIST_TD}><span style={{fontSize:14,padding:"1px 6px",borderRadius:3,...histActionStyle(h?.action)}}>{h?.action||"set"}</span></td>
      <td style={HIST_TD}>
        <span style={{color:"var(--text-secondary)",textDecoration:oldVal?"line-through":"none"}}>{oldVal||"(없음)"}</span>
        <span style={{margin:"0 6px",color:"var(--text-secondary)"}}>→</span>
        <span style={{color:isDel?"rgba(239,68,68,0.95)":"rgba(34,197,94,0.95)",fontWeight:700}}>{isDel?"(삭제)":(newVal||"(없음)")}</span>
      </td>
      <td style={{...HIST_TD,maxWidth:280,whiteSpace:"pre-wrap",wordBreak:"break-word"}} title={h?.reason||""}>
        {h?.reason?h.reason:<span style={{color:"var(--text-secondary)"}}>-</span>}
      </td>
    </tr>);
  };
  const columnFromCellKey=(key)=>String(key||"").split("|").slice(2).join("|");
  const hasValue=(v)=>v!=null&&v!==""&&v!=="None"&&v!=="null";
  const pendingFor=(map)=>(cell)=>cell?.key&&Object.prototype.hasOwnProperty.call(map,cell.key)?map[cell.key]:undefined;
  const pendingValueFor=pendingFor(pendingPlans);
  const pendingTagValueFor=pendingFor(pendingTags);
  const pendingManagementValueFor=pendingFor(pendingManagement);
  // 셀에 pending 편집(tag > mgmt > plan 우선순위)을 반영한 유효 셀 계산 — 렌더/복사/뷰 5곳 공용.
  const effectiveCellFor=(cell)=>{
    const pendingPlan=pendingValueFor(cell);
    const pendingTag=pendingTagValueFor(cell);
    const pendingMgmt=pendingManagementValueFor(cell);
    const effectiveCell=pendingTag!==undefined?{...cell,actual:pendingTag}
      :pendingMgmt!==undefined?{...cell,actual:pendingMgmt}
      :pendingPlan!==undefined?{...cell,plan:pendingPlan}:cell;
    return {effectiveCell,pendingPlan,pendingTag,pendingMgmt};
  };
  const suggestionValuesFor=(param,base=[])=>{
    const out=[];const seen=new Set();
    const add=(v)=>{if(!hasValue(v))return;const s=String(v);if(seen.has(s))return;seen.add(s);out.push(s);};
    (base||[]).forEach(add);
    Object.entries(pendingPlans).forEach(([key,val])=>{if(columnFromCellKey(key)===param)add(val);});
    Object.entries(pendingTags).forEach(([key,val])=>{if(columnFromCellKey(key)===param)add(val);});
    Object.entries(pendingManagement).forEach(([key,val])=>{if(columnFromCellKey(key)===param)add(val);});
    return out;
  };
  const primePlanValueCache=(plans)=>{
    setColValCache(m=>{
      const next={...m};
      Object.entries(plans||{}).forEach(([key,val])=>{
        const col=columnFromCellKey(key);
        if(!col||!hasValue(val)||!Array.isArray(next[col]))return;
        const s=String(val);
        if(!next[col].includes(s))next[col]=[...next[col],s];
      });
      return next;
    });
  };
  const clearCellSelection=()=>{setSelectedCellRange(null);setSelectionAnchor(null);setIsDraggingSelection(false);};
  const normalizeCellRange=(r1,c1,r2,c2)=>({
    startRow:Math.min(r1,r2),
    startCol:Math.min(c1,c2),
    endRow:Math.max(r1,r2),
    endCol:Math.max(c1,c2),
  });
  const pendingEditCount=Object.keys(pendingPlans).length+Object.keys(pendingTags).length+Object.keys(pendingManagement).length;
  const savePlans=()=>{if(!pendingEditCount)return;
    const plansToSave={...pendingPlans};
    const tagsToSave={...pendingTags};
    const managementToSave={...pendingManagement};
    const jobs=[];
    if(Object.keys(plansToSave).length){
      jobs.push(sf(API+"/plan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,plans:plansToSave,username:user?.username||"",root_lot_id:lotId,reason:planReason.trim()})}));
    }
    if(Object.keys(tagsToSave).length){
      jobs.push(sf(API+"/custom-tags/values",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,values:tagsToSave,username:user?.username||"",root_lot_id:lotId})}));
    }
    if(Object.keys(managementToSave).length){
      jobs.push(sf(API+"/management-rows/values",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,values:managementToSave,username:user?.username||"",root_lot_id:lotId})}));
    }
    Promise.all(jobs)
      .then(()=>{primePlanValueCache(plansToSave);setShowConfirm(false);setEditing(false);setPendingPlans({});setPendingTags({});setPendingManagement({});setPlanReason("");reloadCustomTags();loadView();})
      .catch(e=>toast.error(e.message));};
  const deletePlan=(ck)=>{if(!confirm("Delete?"))return;sf(API+"/plan/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,cell_keys:[ck],username:user?.username||""})}).then(loadView);};
  // 적용 공정 정보가 켜져 있으면 스냅샷도 화면과 같은 표기(항목명 대신 연결 공정)로
  // 나가야 한다. 표시 대상 공정이 없는 매칭 행은 화면에서처럼 스냅샷에서도 뺀다.
  const applyStepLabelsForSnapshot=(rows)=>{
    if(!showParamMeta)return rows;
    const out=[];
    (rows||[]).forEach(row=>{
      const kind=matchKindOf(row?._param);
      if(!kind){out.push(row);return;}
      const lines=matchStepLines(kind,matchMetaFor(kind,row?._param),{excludeNotNull:excludeNotNullStepMeta});
      if(!lines.length)return;
      const text=lines.join("\n");
      // 적용 공정은 인폼 서버가 나중에 메타 CSV를 다시 읽어 복원하지 않도록
      // 현재 화면에서 확정한 줄을 스냅샷 행에 그대로 싣는다.
      out.push({...row,_display:text,_applied_process:{kind,lines:[...lines],text}});
    });
    return out;
  };
  const currentRowsForInformSnapshot=()=>{
    const rows=Array.isArray(data?.rows)?data.rows:[];
    const rowHasPlan=(row)=>{const cells=row?._cells||{};return Object.values(cells).some(cell=>hasValue(cell?.plan));};
    const rowHasPendingPlan=(row)=>{const cells=row?._cells||{};return Object.values(cells).some(c=>hasValue(pendingValueFor(c))||hasValue(pendingTagValueFor(c))||hasValue(pendingManagementValueFor(c)));};
    if(viewMode!=="diff")return applyStepLabelsForSnapshot(rows);
    return applyStepLabelsForSnapshot(rows.filter(r=>{
      const vs=Object.values(r._cells||{}).map(c=>c?.actual).filter(v=>v!=null&&v!==""&&v!=="None"&&v!=="null");
      return new Set(vs).size>=2 || rowHasPlan(r) || rowHasPendingPlan(r);
    }));
  };
  const rowsWithPendingPlans=(rows)=>rows.map(row=>{
    const nextCells={};
    Object.entries(row._cells||{}).forEach(([ci,cell])=>{
      const {effectiveCell,pendingPlan,pendingTag,pendingMgmt}=effectiveCellFor(cell);
      nextCells[String(ci)]=(pendingTag!==undefined||pendingMgmt!==undefined||pendingPlan!==undefined)?effectiveCell:{...cell};
    });
    // _applied_process 등 현재 화면의 표시 메타도 보존한다.
    return {...row,_cells:nextCells};
  });
  const uniqueVisibleFabLotsForInform=()=>{
    const out=[];const seen=new Set();
    const add=(v)=>{
      const label=String(v||"").trim();
      if(!label||label==="-"||label==="—")return;
      const key=label.toLowerCase();
      if(seen.has(key))return;
      seen.add(key);out.push(label);
    };
    (Array.isArray(data?.header_groups)?data.header_groups:[]).forEach(g=>add(g?.label));
    (Array.isArray(data?.wafer_fab_list)?data.wafer_fab_list:[]).forEach(add);
    (Array.isArray(data?.available_fab_lots)?data.available_fab_lots:[]).forEach(add);
    return out;
  };
  const startInformFromCurrentSnapshot=()=>{
    const rows=currentRowsForInformSnapshot();
    if(!selProd||!data||!rows.length){toast.warn("먼저 SplitTable 을 조회하세요.");return;}
    const rootLot=String(data.root_lot_id||lotId||"").trim();
    const typedFabLot=String(fabLotId||"").trim();
    const typedLot=String(lotId||"").trim();
    const visibleFabLots=uniqueVisibleFabLotsForInform();
    const viewFabLot=visibleFabLots[0]||"";
    const typedLotIsFab=/[._\-/]/.test(typedLot);
    const rootOnly=!!typedLot&&!typedFabLot&&!typedLotIsFab;
    const selectedFabLots=typedFabLot
      ? [typedFabLot]
      : (typedLotIsFab ? [typedLot] : visibleFabLots);
    const targetLot=String(typedFabLot||(rootOnly?(rootLot||typedLot):viewFabLot)||(typedLotIsFab?typedLot:"")).trim();
    if(!targetLot){toast.warn("SplitTable 의 lot_id/fab_lot_id 를 먼저 선택하세요.");return;}
    const targetIsFab=!rootOnly&&Boolean(typedFabLot||viewFabLot||/[._\-/]/.test(targetLot));
    const draftFabLots=rootOnly?selectedFabLots:selectedFabLots.slice(0,1);
    const rowParams=rows.map(r=>String(r?._param||"").trim()).filter(Boolean);
    const pendingCount=Object.keys(pendingPlans).length+Object.keys(pendingTags).length+Object.keys(pendingManagement).length;
    const currentView={
      headers:Array.isArray(data.headers)?data.headers:[],
      rows:rowsWithPendingPlans(rows),
      wafer_fab_list:Array.isArray(data.wafer_fab_list)?data.wafer_fab_list:[],
      header_groups:Array.isArray(data.header_groups)?data.header_groups:[],
      row_labels:data.row_labels||{"root_lot_id":"root_lot_id","lot_id":"lot_id","parameter":"항목"},
      root_lot_id:rootLot,
      // 인폼 스냅샷과 메일이 화면과 같은 모습이 되려면 표시 규약도 같이 보내야 한다.
      precision:data.precision||{},
      step_progress:(data.step_progress&&typeof data.step_progress==="object")?data.step_progress:{},
      wafer_keys:Array.isArray(data.wafer_keys)?data.wafer_keys:[],
      step_labels:!!showParamMeta,
      applied_processes:showParamMeta?rows.filter(r=>r?._applied_process).map(r=>({
        parameter:String(r?._param||""),
        kind:String(r?._applied_process?.kind||""),
        lines:Array.isArray(r?._applied_process?.lines)?[...r._applied_process.lines]:[],
        text:String(r?._applied_process?.text||r?._display||""),
      })):[],
      msg:`${rows.length} params · current SplitTable${showParamMeta?" · 적용 공정 정보 포함":""}${pendingCount?` · pending plan ${pendingCount}건 포함`:""}`,
    };
    setInformSnapshotBusy(true);
    sf(INFORM_API+"/splittable-snapshot",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      product:selProd,
      lot_id:targetLot,
      custom_cols:rowParams,
      is_fab_lot:targetIsFab,
      current_view:currentView,
      // 화면에서 Split 체크로 보고 있으면 스냅샷도 같은 형식으로 잡는다 —
      // 예전엔 항상 matrix 여서 체크 상태가 스냅샷에 안 실렸다.
      display_mode:splitCheckViewActive?"split_check":(mergedViewActive?"merged":"matrix"),
      // "적용 공정 정보" 도 화면과 같게 — 항목명이 step_id 로 나가야 인폼에서도 같은 표를 본다.
      show_step_ids:!!showParamMeta,
    })}).then(d=>{
      const embed=d?.embed||{};
      const draft={
        wizardVersion:2,
        form:{
          wafer_id:"",
          lot_id:draftFabLots[0]||targetLot,
          fab_lot_ids:draftFabLots,
          product:stripMlPrefix(selProd),
          module:"",
          reason:"PEMS",
          text:"",
          deadline:"",
          attach_split:false,
          split:{column:"",old_value:"",new_value:""},
          attach_embed:true,
          embed,
          snapshot_mode:splitCheckViewActive?"split_check":(mergedViewActive?"merged":"matrix"),
          show_step_ids:!!showParamMeta,
        },
        createImages:[],
        wizardStep:1,
        wizardAttachMode:"knob",
        wizardSelectedSetIds:[],
        embedCustomCols:rowParams,
        wizardMailDraft:{subject:"",body:"",generatedFor:""},
      };
      try{
        localStorage.setItem(INFORM_WIZARD_DRAFT_KEY,JSON.stringify(draft));
        localStorage.setItem(INFORM_WIZARD_OPEN_KEY,"1");
      }catch(_){}
      window.dispatchEvent(new CustomEvent("flow:navigate",{detail:{tab:"inform",search:"?inform_tab=inform&create=1"}}));
    }).catch(e=>toast.error("Inform 스냅샷 생성 실패: "+(e?.message||e)))
      .finally(()=>setInformSnapshotBusy(false));
  };

  // v8.6.1: 낙관적 잠금 — 동일 name 의 기존 custom version 을 expected_version 으로 첨부.
  // 충돌(다른 사용자 저장) 시 conflict 응답 → confirm 으로 덮어쓸지 reload 할지 선택.
  const cleanCustomName=(name)=>{
    if(typeof name!=="string")return "";
    const next=name.trim();
    if(!next)return "";
    const lowered=next.toLowerCase();
    return lowered==="undefined"||lowered==="null"?"":next;
  };
  const cleanCustomColumns=(cols=[])=>{
    const out=[];const seen=new Set();
    (Array.isArray(cols)?cols:[]).forEach(col=>{
      if(typeof col!=="string")return;
      const next=col.trim();
      if(!next)return;
      const lowered=next.toLowerCase();
      if(lowered==="undefined"||lowered==="null")return;
      if(next.toUpperCase().startsWith("MGMT_"))return;
      if(seen.has(next))return;
      seen.add(next);out.push(next);
    });
    return out;
  };
  const cleanCustomSet=(set)=>{
    if(!set||typeof set!=="object")return null;
    const name=cleanCustomName(set.name);
    const columns=cleanCustomColumns(set.columns);
    if(!name||!columns.length)return null;
    return {...set,name,columns};
  };
  const cleanCustomSets=(sets=[])=>(Array.isArray(sets)?sets:[]).map(cleanCustomSet).filter(Boolean);
  const saveCustom=(force)=>{const nameToSave=cleanCustomName(customName);const colsToSave=cleanCustomColumns(customCols);if(!nameToSave||!colsToSave.length)return;
    const existing=customs.find(c=>c.name===nameToSave);
    const ev=force?null:(existing?(existing.version||1):0);
    sf(API+"/customs/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:nameToSave,username:user?.username||"",columns:colsToSave,expected_version:ev})})
      .then(d=>{
        if(d&&d.conflict){
          if(confirm("⚠ '"+nameToSave+"' 가 다른 사용자에 의해 변경되었습니다.\n\nOK = 그래도 덮어쓰기\nCancel = 최신 데이터 불러오기")){
            saveCustom(true);
          } else {
            reloadCustoms();
            const cur=d.current||{};
            if(cur.columns)setCustomCols(cleanCustomColumns(cur.columns));
          }
          return;
        }
        reloadCustoms();setSelCustom(nameToSave);setCustomName(nameToSave);setCustomCols(colsToSave);setIsCustomMode(true);
      }).catch(e=>toast.error("저장 실패: "+(e.message||e)));};
  const deleteCustom=(name)=>{if(!confirm("Delete '"+name+"'?"))return;
    sf(API+"/customs/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,username:user?.username||""})})
      .then(()=>{reloadCustoms();if(selCustom===name)setSelCustom("");}).catch(e=>toast.error(e.message));};
  const selectCustomSet=(c)=>{
    // v8.8.33: 저장 set 에서 기본 식별자(root_lot_id/wafer_id/lot_id/fab_lot_id/product) 자동 제거 — 자동 첨부되는 컬럼.
    const set=cleanCustomSet(c);
    if(!set)return;
    const _drop=new Set(["product","root_lot_id","wafer_id","lot_id","fab_lot_id"]);
    const cleaned=cleanCustomColumns((set.columns||[]).filter(col=>!_drop.has(String(col).toLowerCase())));
    setSelCustom(set.name);setCustomCols(cleaned);setCustomName(set.name);
  };
  const currentResultParams=()=>Array.from(new Set((data?.rows||[]).map(r=>String(r?._param||"").trim()).filter(Boolean)));
  const includeTagInCurrentResult=(column)=>{
    const nextCols=cleanCustomColumns([...currentResultParams(),column]);
    setIsCustomMode(true);
    setSelCustom("");
    setCustomCols(nextCols);
    loadView({customMode:true,customCols:nextCols,customName:""});
  };
  const createCustomTag=(nameOverride="",moduleOverride="")=>{
    const name=(nameOverride||"").trim();
    if(!selProd||!name)return;
    sf(API+"/custom-tags/columns/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,name,module:String(moduleOverride||"").trim(),username:user?.username||""})})
      .then(d=>{
        const col=d.column;
        setCustomTags(d.columns||[]);
        if(col){
          includeTagInCurrentResult(col);
          setProductSchema(prev=>prev.includes(col)?prev:[...prev,col]);
        }
        toast.ok("꼬리표 열 추가됨");
      })
      .catch(e=>toast.error("꼬리표 열 추가 실패: "+(e.message||e)));
  };
  const promptCreateCustomTag=()=>{
    const name=window.prompt("TAG 이름");
    if(!name||!String(name).trim())return;
    // module 은 선택 사항이다 — 취소하거나 비워두면 module 없이 만든다.
    const module=window.prompt(`'${String(name).trim()}' 의 module (선택 — 비워두면 빈 칸)`,"");
    createCustomTag(name,module===null?"":module);
  };
  // TAG 행의 module 만 따로 고친다. 값 저장(꼬리표 값)과는 별개 경로다.
  const saveCustomTagModule=(column,module)=>{
    if(!selProd||!column)return;
    sf(API+"/custom-tags/columns/module",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,column,module:String(module||"").trim(),username:user?.username||""})})
      .then(d=>{
        setCustomTags(d.columns||[]);
        toast.ok(d.module?`module '${d.module}' 저장됨`:"module 비움");
      })
      .catch(e=>toast.error("module 저장 실패: "+(e.message||e)));
  };
  const promptSetCustomTagModule=(column,current="")=>{
    if(!selProd||!column)return;
    const next=window.prompt(`'${customLabelFor(column)}' 의 module (비워두면 빈 칸)`,current||"");
    if(next===null)return;
    if(String(next).trim()===String(current||"").trim())return;
    saveCustomTagModule(column,next);
  };
  const deleteCustomTagColumn=(column)=>{
    if(!canManage||!column)return;
    if(!confirm(`TAG 열 '${column}' 을 삭제할까요? 저장된 값도 함께 삭제됩니다.`))return;
    sf(API+"/custom-tags/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,column,username:user?.username||""})})
      .then(d=>{
        setCustomTags(d.columns||[]);
        setProductSchema(prev=>prev.filter(c=>c!==column));
        setCustomCols(prev=>cleanCustomColumns(prev).filter(c=>c!==column));
        setPendingTags(prev=>Object.fromEntries(Object.entries(prev||{}).filter(([k])=>!String(k).endsWith("|"+column))));
        setData(cur=>cur?{...cur,all_columns:(cur.all_columns||[]).filter(c=>c!==column),rows:(cur.rows||[]).filter(r=>r?._param!==column)}:cur);
        toast.ok("꼬리표 열 삭제됨");
      })
      .catch(e=>toast.error("꼬리표 열 삭제 실패: "+(e.message||e)));
  };
  const togglePrefix=(p)=>{if(isCustomMode){setIsCustomMode(false);setSelCustom("");setSelPrefixes([p]);return;}
    setSelPrefixes(prev=>prev.includes(p)?prev.filter(x=>x!==p).length?prev.filter(x=>x!==p):[p]:[...prev,p]);};
  const addPrefix=()=>{if(!newPrefix.trim())return;const np=newPrefix.trim().toUpperCase();
    sf(API+"/prefixes/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prefixes:[...prefixes,np]})}).then(()=>{setPrefixes(prev=>[...prev,np]);setNewPrefix("");});};
  const savePrecision=()=>{
    sf(API+"/precision/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({precision:precisionDraft})})
      .then(d=>{setPrecision(d.precision||{});setPrecisionDraft(d.precision||{});})
      .catch(e=>toast.error(e.message));
  };
  const removePrefix=(p)=>{if(!confirm("Remove "+p+"?"))return;const next=prefixes.filter(x=>x!==p);
    sf(API+"/prefixes/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prefixes:next})}).then(()=>setPrefixes(next));};

  const formatCell=(val,paramName)=>{
    // Apply prefix-based decimal precision to numeric values.
    // Non-numeric values pass through unchanged.
    if(val===null||val===undefined||val==="")return val;
    const s=String(val);
    if(s==="None"||s==="null"||s==="NaN")return val;
    const num=Number(s);
    if(!isFinite(num)||isNaN(num))return val;
    const pn=(paramName||"").toUpperCase();
    // Find which prefix this param matches (prefix followed by underscore)
    for(const pfx of Object.keys(precision||{})){
      if(pn.startsWith(pfx.toUpperCase()+"_")){
        const n=precision[pfx];
        if(typeof n==="number"&&n>=0&&n<=10)return num.toFixed(n);
      }
    }
    return val;
  };
  const getCellBg=(val,uniqueMap,paramName)=>{
    if(!val||val==="None"||val==="null"||val===null)return{};
    const pn=(paramName||"").toUpperCase();
    const shouldColor=COLOR_PREFIXES.some(p=>pn.startsWith(p+"_"));
    if(!shouldColor)return{};
    const strVal=String(val);
    const idx=uniqueMap[strVal];
    if(idx!==undefined){const c=CELL_COLORS[idx%CELL_COLORS.length];return{background:c.bg,color:c.fg};}
    return{};};
  // plan 표시 규약 두 가지.
  //   왼쪽 파란선 = 이 셀에 plan 이 있다 (세운 상태 / 그대로 진행 / 다르게 진행 모두).
  //   칸 안쪽 빨강 = plan 과 다르게 진행됐다. unique 값 색상보다 우선한다.
  // (예전엔 mismatch 를 왼쪽 빨간선 + inset 링으로 표시했는데, 링은 아래에서
  //  선택 표시 boxShadow 가 덮어써 실제로는 보이지 않았다.)
  const PLAN_LINE="3px solid #3b82f6";
  const getCellPlanStyle=(cell)=>{if(!cell)return{};
    if(cell.plan&&cell.actual){
      if(String(cell.plan)===String(cell.actual))return{borderLeft:PLAN_LINE}; // plan 대로 진행됨
      return{borderLeft:PLAN_LINE,background:"#ef4444",color:"#fff"};          // plan 과 다르게 진행됨
    }
    if(cell.plan)return{borderLeft:PLAN_LINE,fontStyle:"italic",fontWeight:700}; // plan-only: bg 는 getCellBg 가 plan 값 기준으로 처리
    return{};};

  // v8.8.23: view 응답의 all_columns 는 이미 오버라이드 조인 후 df.columns 이지만,
  //   lot 검색 전에는 비어있어 drawer/검색 UI 에 override 컬럼이 안 보였음.
  //   productSchema 와 overrideCols 를 union 해 어느 상태에서도 override 컬럼이 드롭될 일이 없게.
  const allCols=(()=>{
    const base = data?.all_columns || [];
    const seen = new Set(base);
    const out = [...base];
    for(const c of [...overrideCols, ...productSchema]){
      if(c && !seen.has(c)){ seen.add(c); out.push(c); }
    }
    return out;
  })();
  // v8.8.16: CUSTOM 모드 전용 컬럼 풀 — productSchema (전체) + allCols (현재 lot) + customCols 합집합.
  //   lot 검색 전이라도 선택 가능하며, plan 전용 가상 컬럼(저장된 customCols) 도 보존.
  // v8.8.33: product/root_lot_id/wafer_id/lot_id/fab_lot_id 는 **항상** 자동 첨부되는 기본 식별자 —
  //   CUSTOM pool 에서 절대 노출 X (override 에서 왔든 아니든 동일). 사용자가 의미 있는 파라미터에만
  //   집중하도록 근본적으로 차단. 기존에 customCols 에 섞여있던 것도 로드 타임에 자동 제거.
  const _CUSTOM_HIDDEN_BASE = new Set(["product","root_lot_id","wafer_id","lot_id","fab_lot_id"]);
  const customPool=(()=>{const seen=new Set();const out=[];
    const candidateCols=cleanCustomColumns([...productSchema,...allCols,...customCols,...overrideCols,...customTags.map(t=>t?.column)]);
    for(const c of candidateCols){
      const lc = String(c).toLowerCase();
      if(_CUSTOM_HIDDEN_BASE.has(lc)) continue;
      if(!seen.has(c)){seen.add(c);out.push(c);}
    }return out;})();
  const customLabelFor=(column)=>{
    if(typeof column!=="string")return "";
    const hit=(customTags||[]).find(t=>t.column===column);
    if(hit)return `${hit.label||column} (${column})`;
    return column;
  };
  // TAG 열의 module — 엔지니어가 직접 적은 값이며 비어 있는 게 정상이다.
  const tagModuleFor=(column)=>{
    if(typeof column!=="string")return "";
    const hit=(customTags||[]).find(t=>t.column===column);
    return String(hit?.module||"").trim();
  };
  // 쉼표로 여러 검색어를 입력하면 하나라도 포함되는 컬럼을 함께 표시한다.
  // 예: "KNOB, INLINE, TAG". 빈 토큰은 무시해 끝에 쉼표를 입력하는 중에도 목록이 흔들리지 않는다.
  const customSearchTerms=colSearch.split(/[,，]/).map(term=>term.trim().toLowerCase()).filter(Boolean);
  const hasCustomSearch=customSearchTerms.length>0;
  const filteredCustomCols=hasCustomSearch
    ?customPool.filter(c=>customSearchTerms.some(term=>c.toLowerCase().includes(term)))
    :customPool;
  const activeCustomCols=cleanCustomColumns(customCols);
  const activeCustomColSet=new Set(activeCustomCols);
  const filteredLots=lotFilter?lotSuggestions.filter(l=>String(l||"").toLowerCase().includes(lotFilter.toLowerCase())):lotSuggestions;
  const S={padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"};
  const chipS=(active)=>({padding:"3px 8px",borderRadius:4,fontSize:14,cursor:"pointer",fontWeight:active?700:400,background:active?"var(--accent-glow)":"var(--bg-hover)",color:active?"var(--accent)":"var(--text-secondary)",border:active?"1px solid var(--accent)":"1px solid transparent"});

  return(<div className="flow-connected-page" style={{display:"flex",height:embedded?680:"calc(100vh - 52px)",background:"var(--bg-primary)",color:"var(--text-primary)"}}>
    {/* v8.4.9-c: 셀 hover 시 빈 💬+ 배지 페이드인 */}
    <style>{`.stm-cell:hover .stm-note-btn{opacity:1 !important;}`}</style>
    {/* Sidebar */}
    <div style={{width:250,minWidth:250,borderRight:"1px solid var(--border)",background:"var(--bg-secondary)",display:embedded?"none":"flex",flexDirection:"column",overflow:"auto",position:"relative"}}>
      <div className="flow-sidebar-header" style={{padding:"12px 14px",borderBottom:"1px solid var(--border)",fontSize:14,fontWeight:700,color:"var(--text-secondary)"}}>
        <span className="flow-sidebar-header-title">스플릿 테이블</span>
        <div className="flow-sidebar-header-meta">{visibleProducts.length} products</div>
      </div>
      <div style={{padding:"8px 12px"}}><div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>PRODUCT</div>
        <select value={selProd} onChange={e=>setSelProd(e.target.value)} style={{...S,width:"100%"}}>{visibleProducts.map(p=><option key={p.name} value={p.name}>{stripMlPrefix(p.name)}</option>)}</select></div>
      {/* Lot ID dropdown */}
      <div style={{padding:"4px 12px"}} ref={lotRef}>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>ROOT LOT ID</div>
        <input value={lotId} onChange={e=>{setLotId(e.target.value);setFabLotId("");setLotFilter(e.target.value);setShowLotDrop(true);}}
          onFocus={()=>setShowLotDrop(true)} placeholder="입력 또는 선택"
          style={{...S,width:"100%"}} onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;setShowLotDrop(false);doSearch();}}}/>
        {showLotDrop&&(filteredLots.length>0||lotSuggestMsg)&&<div style={{maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)",marginTop:2}}>
          {filteredLots.length===0&&lotSuggestMsg&&<div style={{padding:"7px 10px",fontSize:14,color:"var(--text-secondary)",lineHeight:1.4}}>{lotSuggestMsg}</div>}
          {filteredLots.slice(0,50).map(l=><div key={l} onClick={()=>{setLotId(l);setFabLotId("");setShowLotDrop(false);}}
            style={{padding:"6px 10px",fontSize:14,cursor:"pointer",borderBottom:"1px solid var(--border)",color:"var(--text-primary)"}}
            onMouseEnter={e=>e.currentTarget.style.background="var(--bg-hover)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{l}</div>)}
        </div>}
      </div>
      {/* v8.4.3: fab_lot_id 검색 — root_lot_id 대신 FAB 쪽 ID 로 조회 */}
      <div style={{padding:"4px 12px"}}>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>LOT ID</div>
        <input value={fabLotId} onChange={e=>{setFabLotId(e.target.value);setShowFabDrop(true);}}
          onFocus={()=>setShowFabDrop(true)} onBlur={()=>setTimeout(()=>setShowFabDrop(false),150)}
          placeholder="fab_lot_id 입력" style={{...S,width:"100%"}} onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;setShowFabDrop(false);doSearch();}}}/>
        {showFabDrop&&(fabSuggestions.length>0||fabSuggestBusy||fabSuggestMsg)&&
          <div style={{maxHeight:160,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)",marginTop:2}}>
            {fabSuggestBusy&&<div style={{padding:"7px 10px",fontSize:14,color:"var(--text-secondary)"}}>Fab lot 후보 조회 중...</div>}
            {!fabSuggestBusy&&(fabLotId?fabSuggestions.filter(f=>f.toLowerCase().includes(fabLotId.toLowerCase())):fabSuggestions).length===0&&fabSuggestMsg&&<div style={{padding:"7px 10px",fontSize:14,color:BAD.fg,lineHeight:1.4}}>{fabSuggestMsg}</div>}
            {!fabSuggestBusy&&(fabLotId?fabSuggestions.filter(f=>f.toLowerCase().includes(fabLotId.toLowerCase())):fabSuggestions).slice(0,50).map(f=><div key={f} onMouseDown={()=>{setFabLotId(f);setShowFabDrop(false);}}
              style={{padding:"6px 10px",fontSize:14,cursor:"pointer",borderBottom:"1px solid var(--border)",color:"var(--text-primary)"}}
              onMouseEnter={e=>e.currentTarget.style.background="var(--bg-hover)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{f}</div>)}
          </div>}
      </div>
      <div style={{padding:"4px 12px"}}><div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>WAFER ID</div>
        <input value={waferIds} onChange={e=>setWaferIds(e.target.value)} placeholder="예: 1,2,3" style={{...S,width:"100%"}} onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;doSearch();}}}/></div>
      <div style={{padding:"6px 12px"}}>
        <button onClick={doSearch} title="검색"
          style={{width:"100%",padding:"7px 0",borderRadius:5,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:14,fontWeight:600,cursor:"pointer",opacity:1}}>
          검색
        </button>
      </div>
      {/* Prefix multi-select */}
      <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)"}}><div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>컬럼 그룹</div>
        <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
          {prefixes.map(p=><span key={p} onClick={()=>togglePrefix(p)} style={chipS(selPrefixes.includes(p)&&!isCustomMode)}>{p}</span>)}
          <span onClick={()=>{setIsCustomMode(true);setSelPrefixes([]);}} style={chipS(isCustomMode)}>CUSTOM</span>
        </div></div>
      {/* Custom mode */}
      {isCustomMode&&<div style={{padding:"8px 12px",borderTop:"1px solid var(--border)",flex:1,minWidth:0,overflowY:"auto",overflowX:"hidden"}}>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>커스텀 세트</div>
        {customs.map(c=><div key={c.name} style={{display:"flex",alignItems:"center",gap:4,padding:"3px 6px",borderRadius:4,marginBottom:2,background:selCustom===c.name?"var(--accent-glow)":"transparent",cursor:"pointer"}}
          onClick={()=>selectCustomSet(c)}>
          <span style={{flex:1,fontSize:14,color:selCustom===c.name?"var(--accent)":"var(--text-primary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{c.name}</span>
          <span style={{fontSize:14,color:"var(--text-secondary)",flexShrink:0}}>{c.updated?.slice(5,10)||c.created?.slice(5,10)||""}</span>
          {(c.username===user?.username||isAdmin)&&<span onClick={e=>{e.stopPropagation();deleteCustom(c.name);}} style={{fontSize:14,color:"rgba(239,68,68,0.95)",cursor:"pointer",flexShrink:0}} title="Delete">✕</span>}
        </div>)}
        {/* v8.8.16: 선택된 Set 의 컬럼을 pill 로 현재 선택 상태에 노출 — 어느 컬럼이 포함됐는지 한눈에. */}
        {selCustom&&activeCustomCols.length>0&&<div style={{marginTop:6,padding:"5px 6px",minWidth:0,overflow:"hidden",borderRadius:4,background:"var(--bg-card)",border:"1px dashed var(--border)"}}>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:3,fontWeight:600}}>'{selCustom}' 선택 컬럼 ({activeCustomCols.length})</div>
          <div style={{display:"flex",flexWrap:"wrap",gap:3}}>
            {activeCustomCols.map(c=><span key={c} title={c}
              style={{display:"inline-flex",alignItems:"center",gap:2,maxWidth:"100%",minWidth:0,padding:"1px 5px",borderRadius:3,fontSize:14,background:"var(--accent-glow)",color:"var(--accent)",fontFamily:"monospace",whiteSpace:"normal",overflowWrap:"anywhere",wordBreak:"break-word"}}>
              {customLabelFor(c)}<span onClick={()=>setCustomCols(activeCustomCols.filter(x=>x!==c))} style={{cursor:"pointer",fontSize:14,lineHeight:1,marginLeft:2,color:"rgba(239,68,68,0.95)"}} title="제거">×</span>
            </span>)}
          </div>
        </div>}
        <div style={{marginTop:6,fontSize:14,color:"var(--text-secondary)"}}>생성 / 편집</div>
        <input value={colSearch} onChange={e=>setColSearch(e.target.value)} placeholder="컬럼 검색 (쉼표로 여러 개)" style={{...S,width:"100%",minWidth:0,fontSize:14,marginBottom:4,marginTop:4}}/>
        {/* 좁은 사이드바에서도 선택 수가 먼저 보이도록 카운트를 강조하고, 일괄 동작은 아이콘 버튼으로 축소. */}
        <div style={{display:"flex",gap:5,marginBottom:5,alignItems:"center"}}>
          <span
            title={`선택 ${activeCustomCols.length} / 전체 ${customPool.length}`}
            style={{display:"inline-flex",alignItems:"baseline",gap:4,minWidth:0,padding:"3px 8px",borderRadius:999,background:"var(--accent-glow)",border:"1px solid var(--accent)",color:"var(--accent)",fontSize:13,fontWeight:700,whiteSpace:"nowrap"}}
          >
            <span style={{fontSize:11,fontWeight:600}}>선택</span>
            <span style={{fontFamily:"monospace",fontVariantNumeric:"tabular-nums"}}>{activeCustomCols.length} / {customPool.length}</span>
          </span>
          <button onClick={()=>{const all=cleanCustomColumns([...activeCustomCols,...filteredCustomCols]);setCustomCols(all);}}
            type="button" aria-label={hasCustomSearch?`검색 결과 ${filteredCustomCols.length}개 전체 체크`:"전체 체크"} title={hasCustomSearch?`검색 결과 ${filteredCustomCols.length}개 전체 체크`:"전체 체크"}
            style={{marginLeft:"auto",width:26,height:26,padding:0,borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:13,cursor:"pointer",fontWeight:700,lineHeight:1}}>
            ✓
          </button>
          <button onClick={()=>{if(hasCustomSearch){const fs=new Set(filteredCustomCols);setCustomCols(activeCustomCols.filter(c=>!fs.has(c)));}else setCustomCols([]);}}
            type="button" aria-label={hasCustomSearch?`검색 결과 ${filteredCustomCols.length}개 전체 제거`:"전체 제거"} title={hasCustomSearch?`검색 결과 ${filteredCustomCols.length}개 전체 제거`:"전체 제거"}
            style={{width:26,height:26,padding:0,borderRadius:4,border:"1px solid var(--danger-line)",background:"transparent",color:"var(--danger)",fontSize:13,cursor:"pointer",fontWeight:700,lineHeight:1}}>
            ✕
          </button>
        </div>
        <div style={{maxHeight:180,overflowY:"auto",overflowX:"hidden",minWidth:0}}>
          {filteredCustomCols.map(c=><div key={c} onClick={()=>{if(!activeCustomColSet.has(c))setCustomCols(cleanCustomColumns([...activeCustomCols,c]));else setCustomCols(activeCustomCols.filter(x=>x!==c));}} style={{fontSize:14,padding:"2px 6px",cursor:"pointer",color:activeCustomColSet.has(c)?"var(--accent)":"var(--text-secondary)",fontFamily:String(c).startsWith("TAG_")?"monospace":"inherit",whiteSpace:"normal",overflowWrap:"anywhere",wordBreak:"break-word",lineHeight:1.35}}>{activeCustomColSet.has(c)?"✓ ":""}{customLabelFor(c)}</div>)}
          {filteredCustomCols.length===0&&<div style={{fontSize:14,color:"var(--text-secondary)",padding:6,fontStyle:"italic"}}>
            {productSchema.length===0?"제품 스키마 로딩 중...":"검색 결과 없음"}
          </div>}
        </div>
        {activeCustomCols.length>0&&<div style={{marginTop:4}}>
          <div style={{fontSize:14,color:"var(--text-secondary)"}}>{activeCustomCols.length}개 선택</div>
          <div style={{display:"grid",gridTemplateColumns:"minmax(0, 1fr) auto",gap:4,width:"100%",minWidth:0,marginTop:4}}>
            <input value={customName} onChange={e=>setCustomName(e.target.value)} aria-label="커스텀 세트 이름" placeholder="세트명" style={{...S,width:"100%",minWidth:0,fontSize:14}}/>
            <button type="button" onClick={()=>saveCustom(false)} style={{padding:"3px 8px",minWidth:44,borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:14,cursor:"pointer",whiteSpace:"nowrap"}}>저장</button>
          </div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:2}}>같은 이름은 덮어쓰기</div>
        </div>}
      </div>}
      {/* Settings gear */}
      {canManage&&<div>
        <PageGearButton onClick={toggleSettings} title="Admin settings" position="bottom-right" zIndex={97} />
        {showSettings&&<Modal open onClose={closeSettings} width={920} zIndex={98}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
            <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>Split Table 설정</span>
            <span onClick={closeSettings} style={{cursor:"pointer",color:"var(--text-secondary)",fontSize:16}}>✕</span>
          </div>
          <div style={{display:"flex",gap:4,marginBottom:12,borderBottom:"1px solid var(--border)"}}>
            <span onClick={()=>setSettingsTab("basic")} style={{padding:"5px 10px",fontSize:14,cursor:"pointer",fontWeight:settingsTab==="basic"?700:500,borderBottom:settingsTab==="basic"?"2px solid var(--accent)":"2px solid transparent",color:settingsTab==="basic"?"var(--accent)":"var(--text-secondary)"}}>기본</span>
            <span onClick={()=>setSettingsTab("advanced")} style={{padding:"5px 10px",fontSize:14,cursor:"pointer",fontWeight:settingsTab==="advanced"?700:500,borderBottom:settingsTab==="advanced"?"2px solid var(--accent)":"2px solid transparent",color:settingsTab==="advanced"?"var(--accent)":"var(--text-secondary)"}}>고급</span>
            {/* v9.3.x: 캐시 관리는 데이터 > 캐시 관리 탭(My_RamCache)으로 승격 — 여기서 제거 */}
          </div>
          {settingsTab==="basic"&&<div style={{display:"grid",gap:10,marginBottom:10}}>
            <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
              <ProductOrderEditor products={products.map(p=>p.name)} productOrder={productOrder}
                onSave={saveProductOrder} busy={productOrderBusy}/>
            </div>
            <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
              <div style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",marginBottom:6}}>기본 표시 설정</div>
              <div style={{display:"grid",gap:8,fontSize:14,color:"var(--text-secondary)",lineHeight:1.55}}>
                <label style={{display:"flex",alignItems:"center",gap:8,cursor:"pointer",color:"var(--text-primary)"}}>
                  <input type="checkbox" checked={showParamMeta} onChange={e=>setShowParamMeta(e.target.checked)} style={{width:14,height:14,accentColor:"var(--accent)"}}/>
                  적용 공정 정보
                </label>
                <label style={{display:"flex",alignItems:"center",gap:8,cursor:"pointer",color:"var(--text-primary)"}}>
                  <input type="checkbox" checked={excludeNotNullStepMeta} onChange={e=>setExcludeNotNullStepMeta(e.target.checked)} style={{width:14,height:14,accentColor:"var(--accent)"}}/>
                  not_null operator는 적용공정 step 표시에서 제외
                </label>

                <div style={{display:"flex",alignItems:"center",gap:6,color:"var(--text-primary)"}}>
                  <span>표시 형식</span>
                  {TABLE_FORMAT_OPTIONS.map(m=>(
                    <span key={m.k} title={m.t} onClick={()=>{if(m.d)return;setTableFormat(m.k);}}
                      style={{padding:"3px 9px",borderRadius:4,fontSize:13,cursor:m.d?"not-allowed":"pointer",opacity:m.d?0.55:1,background:tableFormat===m.k?"var(--accent-glow)":"var(--bg-hover)",color:tableFormat===m.k?"var(--accent)":"var(--text-secondary)",fontWeight:tableFormat===m.k?700:400,border:"1px solid "+(tableFormat===m.k?"var(--accent)":"var(--border)")}}>{m.l}</span>
                  ))}
                </div>
                <div>표시 자리수, 데이터 연결 방식, 원천 컬럼 매칭, 규칙 편집은 <b>고급</b> 탭에서 관리합니다.</div>
              </div>
            </div>
            <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
              <div style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",marginBottom:6}}>용어 안내</div>
              <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.6}}>
                내부 용어는 [docs/splittable_terms_ko.md] 에 정리되어 있습니다. 일반 사용자는 화면에서 technical 용어 대신 더 쉬운 표현을 우선 보게 됩니다.
              </div>
            </div>
          </div>}
          {settingsTab==="advanced"&&<>
          <div style={{display:"grid",gap:8,marginBottom:12,padding:"10px 12px",borderRadius:10,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
            <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
              <div style={{fontSize:14,fontWeight:700,color:"var(--accent)"}}>설정 연결 흐름</div>
              <button onClick={scrollToSettingsLotLink}
                style={{marginLeft:"auto",padding:"4px 10px",borderRadius:999,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:14,fontWeight:700,cursor:"pointer"}}>
                Lot 컬럼 연결로 이동
              </button>
            </div>
            <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.6}}>
              제품 노출, Lot 컬럼 연결, 컬럼/공정 규칙만 관리합니다. 규칙 추가·수정은 각 섹션의 <b>편집</b> 버튼에서 처리합니다.
            </div>
            <div style={{fontSize:13,color:"var(--text-secondary)",lineHeight:1.5}}>
              캐시 수동 스캔(FAB/제품 원본/Root lot RAM cache)과 쿼리 병렬 코어 수 조정은 <b>데이터 &gt; 캐시 관리</b> 탭으로 이동했습니다.
            </div>
            <div style={{display:"grid",gap:8,padding:"8px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-card)"}}>
              <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                <div style={{fontSize:14,fontWeight:800,color:"var(--text-primary)"}}>plan/actual 불일치 알람 메일</div>
              </div>
              <div style={{fontSize:13,color:"var(--text-secondary)",lineHeight:1.5}}>
                알람은 계획 작성자와, 이름이 제품명과 같은 그룹(대소문자 무시)의 멤버에게 발송됩니다.
              </div>
              <label style={{display:"flex",alignItems:"center",gap:8,fontSize:13,color:"var(--text-primary)",cursor:mismatchMailSaveBusy?"wait":"pointer",userSelect:"none"}}>
                <input type="checkbox" checked={mismatchMailEnabled} disabled={mismatchMailSaveBusy}
                  onChange={e=>toggleMismatchMail(e.target.checked)}/>
                알람 발생 시 수신자에게 메일도 발송 (기본 꺼짐)
              </label>
            </div>
          </div>
          {/* Source visibility checkboxes — Base 파일(ML_TABLE_ 등)만 표시 */}
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:6,fontWeight:600}}>사용자 표시 대상</div>
          {(()=>{const baseProds=products.filter(p=>p.source_type==="base_file");const allBaseNames=baseProds.map(x=>x.name);return(<>
            {baseProds.map(p=>{const checked=!enabledSources||enabledSources.has(p.name);return(
              <label key={p.name} style={{display:"flex",alignItems:"center",gap:6,padding:"4px 0",fontSize:14,cursor:"pointer",borderBottom:"1px solid var(--border)"}}>
                <input type="checkbox" checked={checked} onChange={()=>{
                  const next=new Set(enabledSources||allBaseNames);
                  if(next.has(p.name))next.delete(p.name);else next.add(p.name);
                  setEnabledSources(next);saveSourceConfig(next);
                }} style={{width:14,height:14,accentColor:"var(--accent)"}}/>
                <span style={{fontFamily:"monospace",flex:1}}>{p.name}</span>
                <span style={{fontSize:14,color:"var(--text-secondary)"}}>{p.type||"parquet"}</span>
              </label>);})}
            <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4,marginBottom:10}}>
              사용자 노출 {enabledSources?[...enabledSources].filter(n=>allBaseNames.includes(n)).length:baseProds.length} / {baseProds.length} · 선택한 제품의 실제 DB 연결은 아래 Lot 컬럼 연결에서 조정합니다.
            </div>
          </>)})()}
          {/* Prefix management */}
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4,fontWeight:600}}>컬럼 그룹 관리</div>
          {prefixes.map(p=><div key={p} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"3px 0",fontSize:14}}>
            <span style={{fontFamily:"monospace"}}>{p}</span><span onClick={()=>removePrefix(p)} style={{color:"rgba(239,68,68,0.95)",cursor:"pointer",fontSize:14}}>✕</span>
          </div>)}
          <div style={{display:"flex",gap:4,marginTop:6}}>
            <input value={newPrefix} onChange={e=>setNewPrefix(e.target.value)} placeholder="새 그룹명" style={{...S,flex:1,fontSize:14}} onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;addPrefix();}}}/>
            <button onClick={addPrefix} style={{padding:"3px 8px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:14,cursor:"pointer"}}>+</button>
          </div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4,fontWeight:600,marginTop:10}}>표시 자리수</div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:6}}>숫자 셀을 몇째 자리까지 표시할지 (0-10, 기본 INLINE/VM = 2)</div>
          {[...new Set([...Object.keys(precisionDraft||{}),...prefixes,"INLINE","VM"])].map(pfx=>{
            const v=precisionDraft[pfx];
            return(<div key={pfx} style={{display:"flex",alignItems:"center",gap:6,padding:"3px 0",fontSize:14}}>
              <span style={{fontFamily:"monospace",flex:1}}>{pfx}</span>
              <input type="number" min={0} max={10} value={v==null?"":v} placeholder="none"
                onChange={e=>{
                  const val=e.target.value;
                  const next={...precisionDraft};
                  if(val===""||val==null)delete next[pfx];
                  else next[pfx]=Math.max(0,Math.min(10,Number(val)||0));
                  setPrecisionDraft(next);
                }}
                style={{width:60,padding:"3px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}/>
            </div>);
          })}
          <button onClick={savePrecision} style={{marginTop:6,padding:"4px 10px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:14,cursor:"pointer",fontWeight:600}}>Save Precision</button>

          {/* "어느 캐시·어느 DB 경로와 연결됐는가" 는 관리자 전용이다.
              일반 사용자에게는 바꿀 수도 없고 판단에도 안 쓰이는 내부 배선 정보다. */}
          {isAdmin&&<>
          <div ref={settingsLotLinkRef} style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4,fontWeight:600,marginTop:10,scrollMarginTop:16}}>Lot 컬럼 연결 조정 ({selProd||"제품 선택 필요"})</div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:6}}>자동 매칭을 1순위로 사용하고, 안 맞을 때만 탐색기에 보이는 DB 경로로 수동 연결합니다.</div>
          {selProd&&(()=>{const ov=currentOverride||{};const preview=overridePreview||{};const rec=preview.recommended||{};const cols=preview.columns||[];
            const rawCols=(Array.isArray(preview.raw_columns)&&preview.raw_columns.length?preview.raw_columns:cols);
            const aliasMap=preview.column_aliases||{};
            const runtimeToRaw=Object.fromEntries(Object.entries(aliasMap).map(([raw,runtime])=>[String(runtime||"").toLowerCase(),raw]));
            const toPreviewCol=(value)=>{
              const v=String(value||"").trim();
              if(!v) return "";
              const exact=rawCols.find(c=>String(c).toLowerCase()===v.toLowerCase());
              if(exact) return exact;
              const alias=runtimeToRaw[v.toLowerCase()];
              if(alias){
                const aliased=rawCols.find(c=>String(c).toLowerCase()===String(alias).toLowerCase());
                return aliased||alias;
              }
              return v;
            };
            const formatColLabel=(value)=>{
              const raw=toPreviewCol(value);
              const runtime=aliasMap[raw];
              return runtime?`${raw} → ${runtime}`:raw;
            };
            const currentCache=(data?.match_cache?.hit?data.match_cache:(mlMatch.match_cache?.hit?mlMatch.match_cache:null));
            const currentMeta=currentCache||(mlMatch.override||{});
            const currentMode=currentCache?"cache":(mlMatch.manual_override?"manual":"auto");
            const currentSource=currentCache?(currentCache.fab_source||currentCache.source||"lot_progress_latest_cache"):(mlMatch.effective_fab_source||"");
            const previewApiMissing=preview.api_missing===true;
            const selectedRootCol=toPreviewCol(ov.root_col||rec.root_col||currentMeta.root_col||"");
            const selectedWfCol=toPreviewCol(ov.wf_col||rec.wf_col||currentMeta.wf_col||"");
            const selectedFabCol=toPreviewCol(ov.fab_col||rec.fab_col||currentMeta.fab_col||"");
            const selectedTsCol=toPreviewCol(ov.ts_col||rec.ts_col||currentMeta.ts_col||"");
            const chosenCols=[...new Set(parseCsvTokens(ov.override_cols||((rec.override_cols||[]).join(", "))).map(toPreviewCol).filter(Boolean))];
            const overrideOptions=rawCols.filter(c=>![selectedRootCol,selectedWfCol].some(k=>k&&String(k).toLowerCase()===String(c).toLowerCase()));
            const setOv=(k,v)=>mergeProductOverride(selProd,{[k]:v});
            const setMode=(mode)=>{
              if(mode==="auto"){
                mergeProductOverride(selProd,{fab_source:"",fab_root:""});
                return;
              }
              if(currentManualFabSource) return;
              const fallback=manualFabOptions.find(o=>normFabSource(o.value)===normFabSource(currentSource))
                || manualFabOptions.find(o=>normFabSource(o.value)===normFabSource(autoFabSource))
                || manualFabOptions[0];
              mergeProductOverride(selProd,{
                fab_source:normFabSource(fallback?.value||""),
                fab_root:String(fallback?.value||"").split("/")[0]||"",
              });
            };
            const setManualSource=(value)=>{
              const next=normFabSource(value);
              mergeProductOverride(selProd,{fab_source:next,fab_root:next?next.split("/")[0]:""});
            };
            const toggleOverrideCol=(col)=>{
              const next=chosenCols.includes(col)?chosenCols.filter(x=>x!==col):[...chosenCols,col];
              setOv("override_cols",next.join(", "));
            };
            const applyLink=async()=>{
              if(draftOverrideMode==="manual"&&!currentManualFabSource){
                toast.warn("수동 연결은 DB 경로를 먼저 선택해야 합니다.");
                return;
              }
              if(draftOverrideMode==="auto"&&!autoFabSource&&!mlMatch.match_cache?.hit){
                toast.warn("자동 매칭 후보가 없습니다. 수동 연결로 DB 경로를 선택하세요.");
                return;
              }
              if(preview.error&&!previewApiMissing){toast.error("현재 연결 미리보기가 유효하지 않습니다. 자동 경로나 수동 DB 경로를 다시 확인하세요.");return;}
              const nextRootCol=String(selectedRootCol||"").trim();
              const nextWfCol=String(selectedWfCol||"").trim();
              const nextFabCol=String(selectedFabCol||"").trim();
              const nextTsCol=String(selectedTsCol||"").trim();
              const nextJoinKeysRaw=(Array.isArray(ov.join_keys)&&ov.join_keys.length?ov.join_keys
                :parseCsvTokens(ov.join_keys))
                .concat(Array.isArray(rec.join_keys)&&rec.join_keys.length?rec.join_keys:[])
                .concat(Array.isArray(currentMeta.join_keys)&&currentMeta.join_keys.length?currentMeta.join_keys:[])
                .concat([nextRootCol,nextWfCol]);
              const nextJoinKeys=[...new Set(nextJoinKeysRaw.map(v=>toPreviewCol(v)).map(v=>String(v||"").trim()).filter(Boolean))];
              const nextOverrides={...lotOverrides,[selProd]:{
                ...ov,
                fab_root:draftOverrideMode==="manual"?(currentManualFabSource.split("/")[0]||""):"",
                fab_source:draftOverrideMode==="manual"?currentManualFabSource:"",
                root_col:nextRootCol,
                wf_col:nextWfCol,
                fab_col:nextFabCol,
                ts_col:nextTsCol,
                join_keys:nextJoinKeys,
                override_cols:chosenCols.join(", "),
              }};
              setLotOverrides(nextOverrides);
              try{
                await persistLotOverrides(nextOverrides);
                toast.ok("연결 저장됨. 다음 조회부터 바로 적용됩니다.");
              }catch(e){
                toast.error("저장 실패: "+(e?.message||e));
              }
            };
            const selectS={...S,width:"100%",fontSize:14,fontFamily:"monospace"};
            const currentPreviewLots=currentCache?[]:(currentMeta.sample_fab_values||[]).filter(Boolean);
            const draftPreviewLots=(preview.latest_fab_lot_ids||[]).filter(Boolean);
            const draftAutoSourceLabel=mlMatch.match_cache?.hit?"LOT 최신 캐시":(autoFabSource||"(자동 후보 없음)");
            const statusTone=currentMeta.error?"rgba(239,68,68,0.95)":currentMode==="cache"?"rgba(37,99,235,0.95)":currentMode==="manual"?"rgba(245,158,11,0.95)":"rgba(34,197,94,0.95)";
            const currentAliasPairs=currentCache?[]:Object.entries(currentMeta.column_aliases||{});
            const draftAliasPairs=Object.entries(aliasMap||{});
            return(<div style={{display:"grid",gap:10,padding:"12px 14px",borderRadius:10,background:"var(--bg-secondary)",border:"1px solid var(--border)",marginBottom:10}}>
              <div style={{display:"grid",gridTemplateColumns:"repeat(2, minmax(0, 1fr))",gap:8}}>
                <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-card)",border:"1px solid var(--border)"}}>
                  <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginBottom:6}}>현재 적용</div>
                  <div style={{display:"grid",gap:4,fontSize:14,color:"var(--text-secondary)",lineHeight:1.55}}>
                    <div>방식: <span style={{color:statusTone,fontWeight:700}}>{currentMode==="cache"?"LOT 최신 캐시":currentMode==="manual"?"수동 연결":"자동 매칭"}</span></div>
                    <div>경로: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{currentSource||"(없음)"}</span></div>
                    <div>fab_col / ts_col: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{currentMeta.fab_col||"fab_lot_id"} / {currentMeta.ts_col||"last"}</span></div>
                    <div>join_keys: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{(currentMeta.join_keys||[]).length?(currentMeta.join_keys||[]).join(", "):"미확정"}</span></div>
                    {currentCache&&<div>cache: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{currentCache.source||"lot_progress_latest_cache"} · rows {currentCache.row_count||0} · {currentCache.built_at||"(mtime 없음)"}</span></div>}
                    {currentAliasPairs.length>0&&<div>raw → runtime: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{currentAliasPairs.map(([raw,runtime])=>`${raw}→${runtime}`).join(", ")}</span></div>}
                    <div style={{display:"flex",flexWrap:"wrap",gap:4,alignItems:"center"}}>fab_lot 예시:
                      {currentPreviewLots.length?currentPreviewLots.map(v=><span key={v} style={{padding:"1px 7px",borderRadius:999,background:"rgba(34,197,94,0.12)",color:"rgba(22,163,74,0.95)",fontSize:14,fontFamily:"monospace",fontWeight:700}}>{v}</span>)
                        :<span style={{fontSize:14,color:"var(--text-secondary)"}}>표시할 값 없음</span>}
                    </div>
                    {currentMeta.error&&<div style={{padding:"6px 8px",borderRadius:6,background:"rgba(239,68,68,0.12)",border:"1px solid rgba(239,68,68,0.35)",fontSize:14,color:"rgba(239,68,68,0.95)"}}>{currentMeta.error}</div>}
                  </div>
                </div>
                <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-card)",border:"1px solid var(--border)"}}>
                  <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginBottom:6}}>자동 매칭 후보</div>
                  <div style={{display:"grid",gap:4,fontSize:14,color:"var(--text-secondary)",lineHeight:1.55}}>
                    <div>ML_TABLE 파생 제품: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{mlMatch.pro||deriveProductFolder(selProd)||"(없음)"}</span></div>
                    <div>자동 경로: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{mlMatch.match_cache?.hit?"LOT 최신 캐시":(autoFabSource||"(자동 후보 없음)")}</span></div>
                    <div>탐색기 DB 후보: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{(mlMatch.matches||[]).length?(mlMatch.matches||[]).map(x=>x.path).join(", "):"(없음)"}</span></div>
                    <div style={{fontSize:14,color:"var(--text-secondary)"}}>수동 연결은 아래 목록에서 탐색기와 같은 DB 경로를 직접 고릅니다.</div>
                  </div>
                </div>
              </div>

              <div style={{fontSize:14,fontWeight:700,color:"var(--accent)"}}>1. 연결 방식 선택</div>
              <div style={{display:"flex",flexWrap:"wrap",gap:6}}>
                <button onClick={()=>setMode("auto")} style={{padding:"5px 12px",borderRadius:999,border:draftOverrideMode==="auto"?"1px solid var(--accent)":"1px solid var(--border)",background:draftOverrideMode==="auto"?"var(--accent-glow)":"var(--bg-card)",color:draftOverrideMode==="auto"?"var(--accent)":"var(--text-secondary)",fontSize:14,fontWeight:700,cursor:"pointer"}}>자동 매칭</button>
                <button onClick={()=>setMode("manual")} disabled={!manualFabOptions.length} style={{padding:"5px 12px",borderRadius:999,border:draftOverrideMode==="manual"?"1px solid rgba(245,158,11,0.95)":"1px solid var(--border)",background:draftOverrideMode==="manual"?"rgba(245,158,11,0.12)":"var(--bg-card)",color:draftOverrideMode==="manual"?"rgba(245,158,11,0.95)":"var(--text-secondary)",fontSize:14,fontWeight:700,cursor:manualFabOptions.length?"pointer":"not-allowed",opacity:manualFabOptions.length?1:0.5}}>수동 연결</button>
              </div>
              {draftOverrideMode==="auto"
                ?<div style={{padding:"10px 12px",borderRadius:8,background:"rgba(34,197,94,0.08)",border:"1px solid rgba(34,197,94,0.24)",fontSize:14,color:"var(--text-secondary)",lineHeight:1.6}}>
                  <div>저장 시 <span style={{fontFamily:"monospace",color:"rgba(22,163,74,0.95)",fontWeight:700}}>{draftAutoSourceLabel}</span> 를 우선 사용합니다.</div>
                  <div>{mlMatch.match_cache?.hit?"캐시가 비어 있거나 scope가 맞지 않을 때만 DB FAB 경로로 fallback합니다.":"자동 후보가 없으면 수동 연결로 전환해서 탐색기 DB 경로를 선택하면 됩니다."}</div>
                </div>
                :<div style={{display:"grid",gap:6}}>
                  <div style={{fontSize:14,fontWeight:700,color:"var(--accent)"}}>2. 수동 DB 경로 선택</div>
                  <select value={currentManualFabSource||""} onChange={e=>setManualSource(e.target.value)} style={selectS}>
                    <option value="">DB 경로 선택</option>
                    {manualFabOptions.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  <div style={{fontSize:14,color:"var(--text-secondary)"}}>탐색기에 보이는 DB 경로와 같은 형식으로 연결됩니다. 예: <span style={{fontFamily:"monospace"}}>1.RAWDATA_DB_FAB/PRODA</span></div>
                </div>}

              <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginTop:2}}>3. 연결 열 확인</div>
              {overridePreviewLoading?<div style={{fontSize:14,color:"var(--text-secondary)"}}>연결 미리보기 로딩 중...</div>
              :!effectivePreviewSource?<div style={{padding:"8px 10px",borderRadius:6,background:"var(--bg-card)",border:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}}>{mlMatch.match_cache?.hit?"LOT 최신 캐시를 사용 중입니다. 수동 DB 경로 미리보기는 fallback이 필요할 때만 선택하세요.":"먼저 자동 후보를 확인하거나 수동 DB 경로를 선택하세요."}</div>
              :preview.error&&!previewApiMissing?<div style={{padding:"8px 10px",borderRadius:6,background:"rgba(239,68,68,0.12)",border:"1px solid rgba(239,68,68,0.35)",fontSize:14,color:"rgba(239,68,68,0.95)",lineHeight:1.5}}>{preview.error}</div>
              :<div style={{display:"grid",gap:8}}>
                <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5}}>
                  미리보기 경로: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{effectivePreviewSource}</span>
                </div>
                {draftAliasPairs.length>0&&<div style={{padding:"8px 10px",borderRadius:6,background:"rgba(59,130,246,0.08)",border:"1px solid rgba(59,130,246,0.24)",fontSize:14,color:"var(--text-secondary)",lineHeight:1.5}}>
                  실제 DB 컬럼 선택 기준: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{draftAliasPairs.map(([raw,runtime])=>`${raw} → ${runtime}`).join(", ")}</span>
                </div>}
                {previewApiMissing&&<div style={{padding:"8px 10px",borderRadius:6,background:"rgba(245,158,11,0.12)",border:"1px solid rgba(245,158,11,0.35)",fontSize:14,color:"rgba(245,158,11,0.95)",lineHeight:1.5}}>
                  {preview.error}
                </div>}
                <div style={{display:"grid",gridTemplateColumns:"repeat(2, minmax(0, 1fr))",gap:6}}>
                  <label style={{fontSize:14,color:"var(--text-secondary)"}}>root_col
                    <select value={selectedRootCol||""} onChange={e=>setOv("root_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.root_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                  <label style={{fontSize:14,color:"var(--text-secondary)"}}>wf_col
                    <select value={selectedWfCol||""} onChange={e=>setOv("wf_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.wf_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                  <label style={{fontSize:14,color:"var(--text-secondary)"}}>fab_col
                    <select value={selectedFabCol||""} onChange={e=>setOv("fab_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.fab_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                  <label style={{fontSize:14,color:"var(--text-secondary)"}}>ts_col
                    <select value={selectedTsCol||""} onChange={e=>setOv("ts_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.ts_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                </div>
                <div style={{fontSize:14,color:"var(--text-secondary)"}}>4. 가져올 열 (실제 DB 컬럼)</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:4,maxHeight:120,overflowY:"auto",padding:"2px 0"}}>
                  {overrideOptions.map(col=><span key={col} onClick={()=>toggleOverrideCol(col)}
                    style={{padding:"3px 8px",borderRadius:999,cursor:"pointer",fontSize:14,fontFamily:"monospace",
                      background:chosenCols.includes(col)?"var(--accent-glow)":"var(--bg-card)",
                      color:chosenCols.includes(col)?"var(--accent)":"var(--text-secondary)",
                      border:"1px solid "+(chosenCols.includes(col)?"var(--accent)":"var(--border)")}}>
                    {chosenCols.includes(col)?"✓ ":""}{formatColLabel(col)}
                  </span>)}
                  {overrideOptions.length===0&&<span style={{fontSize:14,color:"var(--text-secondary)"}}>선택 가능한 DB 컬럼 없음</span>}
                </div>
                <div style={{fontSize:14,color:"var(--text-secondary)"}}>5. 최근 fab_lot_id 미리보기</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                  {draftPreviewLots.map(v=><span key={v} style={{padding:"2px 8px",borderRadius:999,background:"rgba(245,158,11,0.14)",color:"rgba(245,158,11,0.95)",fontSize:14,fontFamily:"monospace",fontWeight:700}}>{v}</span>)}
                  {draftPreviewLots.length===0&&<span style={{fontSize:14,color:"var(--text-secondary)"}}>{previewApiMissing?"미리보기 API가 없어 저장 후 조회에서 확인됩니다.":"표시할 fab_lot_id 가 없습니다."}</span>}
                </div>
              </div>}
              <div style={{display:"flex",alignItems:"center",gap:6,marginTop:2}}>
                <button onClick={applyLink} style={{padding:"5px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:14,cursor:"pointer",fontWeight:700}}>연결 적용</button>
                <span style={{fontSize:14,color:"var(--text-secondary)"}}>현재 선택된 제품에만 저장됩니다.</span>
              </div>
              <details style={{marginTop:2}}>
                <summary style={{cursor:"pointer",fontSize:14,color:"var(--text-secondary)"}}>고급 수동 조정</summary>
                <div style={{display:"grid",gap:6,marginTop:8}}>
                  <textarea value={ov.override_cols||""} onChange={e=>setOv("override_cols",e.target.value)} rows={2}
                    placeholder={(overrideOptions||[]).join(", ")||"root_lot_id, wafer_id, lot_id, time"} style={{...S,width:"100%",fontSize:14,fontFamily:"monospace",resize:"vertical"}}/>
                </div>
              </details>
            </div>);
          })()}
          </>}

          {/* v8.8.9: Column/step rulebook — prefix 별 섹션 분리.
                KNOB: ppid_knob.csv 공용 룰 + Vehicle_matching.csv 제품별 step_desc→step_id 확장
                INLINE: inline_matching.csv (item_id/step_id/desc) — INLINE_<item_id> 가 해당 step 에서 측정
                VM: vm_matching.csv (step_desc/item_id) + Vehicle_matching.csv — VM_<step_desc>_<item_id> 이 해당 제품 step 에서 예측
             */}
          {selProd && (() => {
            const rulebookSpecs={
              knob_ppid:{file:"ppid_knob.csv",color:"rgba(251,191,36,0.95)",roles:[["feature","feature_col"],["step_desc","step_desc_col"],["rule_order","rule_order_col"],["operator","operator_col"],["cell_value","value_col"],["category","category_col"]]},
              step_matching:{file:"Vehicle_matching.csv",color:"rgba(96,165,250,0.95)",roles:[["product","product_col"],["step_id","step_id_col"],["step_desc","step_desc_col"]]},
              inline_matching:{file:"inline_matching.csv",color:"rgba(16,185,129,0.95)",roles:[["item_id","item_id_col"],["step_id","step_id_col"],["item_desc","item_desc_col"],["product","product_col"],["matching_table","matching_table_col"]]},
              vm_matching:{file:"vm_matching.csv",color:"rgba(196,181,253,0.95)",roles:[["step_desc","step_desc_col"],["item_id","item_id_col"]]},
            };
            const SectionHeader = ({title, files, count}) => (
              <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:4,flexWrap:"wrap"}}>
                <span style={{fontSize:14,fontWeight:700,color:"var(--text-primary)"}}>{title}</span>
                <span style={{fontSize:14,color:"var(--text-secondary)"}}>({count} 항목)</span>
                <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>
                  → {files.join(" + ")}
                </span>
              </div>
            );
            const RulebookSourceSummary=({kinds})=>(
              <div style={{display:"grid",gap:6,marginBottom:8}}>
                {(kinds||[]).map((kind)=>{
                  const spec=rulebookSpecs[kind];
                  if(!spec) return null;
                  const defaults=rbSchema.defaults?.[kind]||{};
                  const current={...defaults,...(rbSchema.schema?.[kind]||{})};
                  const fileName=String(current.file_name||spec.file||"").trim();
                  return(
                    <div key={kind} style={{padding:"7px 8px",borderRadius:6,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
                      <div style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap",marginBottom:5}}>
                        <span style={{fontSize:14,fontWeight:700,color:spec.color,fontFamily:"monospace"}}>{fileName}</span>
                        <span style={{fontSize:14,color:"var(--text-secondary)"}}>기준 CSV</span>
                        {canManage&&<span style={{display:"inline-flex",alignItems:"center",gap:4,marginLeft:"auto"}}>
                          <span style={{fontSize:14,color:"var(--text-secondary)"}}>파일명</span>
                          <input value={rbFileDrafts[kind] ?? fileName}
                            onChange={e=>setRbFileDrafts(m=>({...m,[kind]:e.target.value}))}
                            onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;saveRulebookFileName(kind);}}}
                            style={{width:190,padding:"3px 7px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}/>
                          <button onClick={()=>saveRulebookFileName(kind)}
                            title={`${kind} 파일명 매칭 저장`}
                            style={{padding:"3px 7px",borderRadius:3,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:"pointer"}}>저장</button>
                          <button onClick={()=>openSchemaEditor(kind)}
                            title={`${kind} 의 역할→실제 컬럼명 매핑 조정`}
                            style={{padding:"3px 7px",borderRadius:3,border:"1px dashed var(--text-secondary)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>컬럼</button>
                        </span>}
                      </div>
                      <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                        {spec.roles.map(([label,key])=>{
                          const value=String(current[key]||"—").trim()||"—";
                          const changed=String(defaults[key]||"").trim()!==value;
                          return(
                            <span key={key} style={{
                              padding:"2px 7px",
                              borderRadius:999,
                              fontSize:14,
                              fontFamily:"monospace",
                              background:changed?"rgba(245,158,11,0.12)":"var(--bg-card)",
                              color:changed?"rgba(245,158,11,0.95)":"var(--text-secondary)",
                              border:"1px solid "+(changed?"rgba(245,158,11,0.35)":"var(--border)")
                            }}>
                              {label}: {value}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            );

            const knobEntries = Object.entries(knobMeta || {});
            const inlineEntries = Object.entries(inlineMetaSt || {});
            const vmEntries = Object.entries(vmMeta || {});

            return (
              <div style={{marginTop:12,marginBottom:10,padding:"8px 10px",borderRadius:6,background:"var(--bg-card)",border:"1px dashed var(--border)"}}>
                <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginBottom:8}}>📘 컬럼/공정 연결 규칙 — {selProd}</div>
                <div style={{marginBottom:8,padding:"8px 10px",borderRadius:6,background:"var(--bg-secondary)",border:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",lineHeight:1.6}}>
                  <div>기본값은 <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>같은 이름의 Base 파일</span>과 <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>기본 열 이름</span>을 자동으로 사용합니다.</div>
                  <div><span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>KNOB_*</span> 는 <span style={{fontFamily:"monospace"}}>ppid_knob.csv</span> 의 step_desc를 <span style={{fontFamily:"monospace"}}>Vehicle_matching.csv</span> 제품별 step_id에 연결합니다.</div>
                  <div><span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>INLINE_&lt;item_id&gt;</span> 는 <span style={{fontFamily:"monospace"}}>inline_matching.csv</span> 의 같은 product 행만, <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>VM_&lt;step_desc&gt;_&lt;item_id&gt;</span> 는 <span style={{fontFamily:"monospace"}}>vm_matching.csv</span> 의 step_desc/item_id를 <span style={{fontFamily:"monospace"}}>Vehicle_matching.csv</span> 제품별 step_id에 연결합니다.</div>
                  <div>열 이름이 다르거나 다른 Base 데이터와 연결해야 하면 각 섹션의 <b>편집</b> / <b>🔧 컬럼</b>에서 역할과 실제 CSV 헤더를 바꾸면 됩니다.</div>
                </div>

                {/* ── KNOB 섹션 ───────────────────────────── */}
                <div style={{marginBottom:10,padding:"6px 8px",borderRadius:4,background:"var(--bg-primary)",border:"1px solid rgba(251,191,36,0.3)"}}>
                  <SectionHeader title="🔧 KNOB_*" count={knobEntries.length}
                    files={[rulebookFileName("knob_ppid","ppid_knob.csv"), rulebookFileName("step_matching","Vehicle_matching.csv")]} />
                  <RulebookSourceSummary kinds={["knob_ppid","step_matching"]}/>
                  <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5}}>룰북 row 미리보기는 이 설정 화면에 표시하지 않습니다. 실제 분류 규칙은 SplitTable 항목명을 클릭해 확인합니다.</div>
                </div>

                {/* ── INLINE 섹션 ─────────────────────────── */}
                <div style={{marginBottom:10,padding:"6px 8px",borderRadius:4,background:"var(--bg-primary)",border:"1px solid rgba(16,185,129,0.3)"}}>
                  <SectionHeader title="🔬 INLINE_*" count={inlineEntries.length}
                    files={[rulebookFileName("inline_matching","inline_matching.csv")]} />
                  <RulebookSourceSummary kinds={["inline_matching"]}/>
                  <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5}}>INLINE row 미리보기는 표시하지 않고, 파일명 매칭과 컬럼 매칭만 관리합니다.</div>
                </div>

                {/* ── VM 섹션 ─────────────────────────────── */}
                <div style={{marginBottom:6,padding:"6px 8px",borderRadius:4,background:"var(--bg-primary)",border:"1px solid rgba(139,92,246,0.3)"}}>
                  <SectionHeader title="🤖 VM_*" count={vmEntries.length}
                    files={[rulebookFileName("vm_matching","vm_matching.csv"), rulebookFileName("step_matching","Vehicle_matching.csv")]} />
                  <RulebookSourceSummary kinds={["vm_matching","step_matching"]}/>
                  <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5}}>VM row 미리보기는 표시하지 않고, 파일명 매칭과 컬럼 매칭만 관리합니다.</div>
                </div>

                <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4,lineHeight:1.4}}>
                  {canManage ? "관리 권한: 섹션별 [편집]에서 제품별 연결 규칙을 추가/수정/삭제하고, [컬럼]에서 CSV 헤더 매핑을 조정합니다." : "편집은 관리자 권한이 필요합니다. 규칙 파일은 DB 루트 최상단에 있습니다."}
                </div>
              </div>
            );
          })()}

          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:10,marginBottom:10,lineHeight:1.5}}>
            Color-coded: {COLOR_PREFIXES.join(", ")}
          </div>
          </>}
          <button onClick={closeSettings} style={{width:"100%",padding:"8px",borderRadius:6,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontWeight:600,fontSize:14,cursor:"pointer"}}>{settingsTab==="advanced"?"고급 설정 닫기":"닫기"}</button>
        </Modal>}
      </div>}
    </div>
    {/* Main */}
    <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
      <div style={{padding:"8px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
        <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{stripMlPrefix(selProd)}</span>
        {lotId&&<span style={{fontSize:14,color:"var(--text-secondary)"}}>| {lotId}</span>}
        {fabLotId&&<span style={{fontSize:14,color:"var(--text-secondary)"}}>| {fabLotId}</span>}
        {lotManagementPurposeHit&&<span title={`LOT 관리 — ${lotManagementPurposeHit.lot_id} · ${lotManagementPurposeHit.purpose}`}
          style={{fontSize:14,padding:"2px 8px",borderRadius:4,background:"var(--accent-glow)",color:"var(--accent)",fontWeight:600}}>
          📌 {lotManagementPurposeHit.purpose}</span>}
        {isCustomMode&&<span style={{fontSize:14,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"2px 8px",borderRadius:4}}>
          {"CUSTOM"+(selCustom?": "+selCustom:"")}</span>}
        {/* 관리자도 내부 source/fab_col@ts_col 대신 제품별 필수 4종 준비 상태만 본다.
            연결 컬럼 상세는 설정 > 고급에 남아 있어 화면 상단을 오염시키지 않는다. */}
        {isAdmin&&requiredCacheStatus&&(()=>{
          const ready=Number(requiredCacheStatus.ready_count||0);
          const total=Number(requiredCacheStatus.total||4);
          const building=(requiredCacheStatus.kinds||[]).some(k=>k.state==="building"||k.state==="queued"||k.state==="running");
          const allReady=requiredCacheStatus.all_ready===true;
          const label=allReady?"✓ 모든 필수 캐시 준비 완료":building?`● 필수 캐시 준비 중 ${ready}/${total}`:`필수 캐시 ${ready}/${total} 준비`;
          const detail=(requiredCacheStatus.kinds||[]).map(k=>`${k.ready?"✓":"○"} ${k.label}`).join("\n");
          return <span title={detail} style={{fontSize:14,padding:"2px 9px",borderRadius:999,
            background:allReady?"var(--ok-50)":building?"var(--info-50)":"var(--warn-50)",
            color:allReady?"var(--ok)":building?"var(--info)":"var(--warn)",
            border:`1px solid ${allReady?"var(--ok-line)":building?"var(--info-line)":"var(--warn-line)"}`,
            fontWeight:700,cursor:"help"}}>{label}</span>;
        })()}
        <div style={{marginLeft:"auto",display:"flex",gap:4,alignItems:"center",flexWrap:"wrap",minWidth:0,justifyContent:"flex-end"}}>
          {/* v8.4.3: Features 탭 제거 — ML_TABLE_PROD* 가 source 이므로 별도 features 뷰 불필요. */}
          {SPLITTABLE_TABS.map(({k,l})=><span key={k} className={"splittable-tab splittable-tab-"+k} data-active={tab===k?"1":"0"} onClick={()=>{setTab(k);if(k==="history")loadHistoryByMode(histMode);}} style={{padding:"4px 10px",borderRadius:4,fontSize:14,cursor:"pointer",background:tab===k?"var(--accent-glow)":"transparent",color:tab===k?"var(--accent)":"var(--text-secondary)",fontWeight:tab===k?600:400}}>{l}</span>)}
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          {["all","diff"].map(m=><span key={m} onClick={()=>setViewMode(m)} style={{padding:"4px 10px",borderRadius:4,fontSize:14,cursor:"pointer",background:viewMode===m?"var(--accent-glow)":"transparent",color:viewMode===m?"var(--accent)":"var(--text-secondary)",fontWeight:viewMode===m?600:400}}>{m}</span>)}
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          <label title="필요할 때만 적용 대상 공정 정보를 표시합니다" style={{display:"inline-flex",alignItems:"center",gap:5,fontSize:14,color:showParamMeta?"var(--accent)":"var(--text-secondary)",cursor:"pointer",padding:"2px 6px"}}>
            <input type="checkbox" checked={showParamMeta} onChange={e=>setShowParamMeta(e.target.checked)}/>
            적용 공정 정보
          </label>

          <span style={{width:1,height:16,background:"var(--border)"}}/>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>표시</span>
          {TABLE_FORMAT_OPTIONS.map(m=>(
            <span key={m.k} title={m.t} onClick={()=>{if(m.d)return;setTableFormat(m.k);}}
              style={{padding:"4px 10px",borderRadius:4,fontSize:14,cursor:m.d?"not-allowed":"pointer",opacity:m.d?0.55:1,background:tableFormat===m.k?"var(--accent-glow)":"transparent",color:tableFormat===m.k?"var(--accent)":"var(--text-secondary)",fontWeight:tableFormat===m.k?600:400}}>{m.l}</span>
          ))}
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          {editing?<>
            <button onClick={()=>{if(pendingEditCount>0)setShowConfirm(true);else{setEditing(false);clearCellSelection();}}} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--ok)",color:"var(--bg-secondary)",fontSize:14,fontWeight:600,cursor:"pointer"}}>Save ({pendingEditCount})</button>
            <button onClick={()=>{setEditing(false);setPendingPlans({});setPendingTags({});setPendingManagement({});setActiveCell(null);clearCellSelection();}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>Cancel</button>
          </>:<>
            {/* v8.4.9: window.open → dl() — 새 탭은 토큰 헤더가 안 붙어 401. blob 다운로드로 전환. */}
            <button onClick={()=>{const cols=cleanCustomColumns(customCols);const customQ=isCustomMode&&cols.length?"&custom_cols="+encodeURIComponent(cols.join(",")):(isCustomMode&&cleanCustomName(selCustom)?"&custom_name="+encodeURIComponent(cleanCustomName(selCustom)):"");const url=API+"/download-csv?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+customQ+stepLabelQ+"&transposed=true&username="+encodeURIComponent(user?.username||"");dl(url, `splittable_${selProd}_${lotId||"all"}.csv`).catch(e=>toast.error("CSV 다운로드 실패: "+e.message));}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:"pointer"}}>⬇ CSV</button>
            <button onClick={()=>{const cols=cleanCustomColumns(customCols);const customQ=isCustomMode&&cols.length?"&custom_cols="+encodeURIComponent(cols.join(",")):(isCustomMode&&cleanCustomName(selCustom)?"&custom_name="+encodeURIComponent(cleanCustomName(selCustom)):"");const splitQ=pemsViewActive?"&display_mode=pems":(splitCheckViewActive?"&display_mode=split_check":(mergedViewActive?"&display_mode=merged":""));const fmtSuffix=pemsViewActive?"_pems":(splitCheckViewActive?"_split_check":(mergedViewActive?"_merged":""));const url=API+"/download-xlsx?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+customQ+splitQ+stepLabelQ+"&username="+encodeURIComponent(user?.username||"");dl(url, `splittable_${selProd}_${lotId||"all"}${fmtSuffix}.xlsx`).catch(e=>toast.error("XLSX 다운로드 실패: "+e.message));}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--ok-line)",background:"transparent",color:"var(--ok)",fontSize:14,cursor:"pointer"}} title={pemsViewActive?"XLSX (PEMS 1~25 · S0/S1 표시 형식)":(splitCheckViewActive?"XLSX (Split 체크 표시 형식)":(mergedViewActive?"XLSX (좌측 동일값 병합 형식)":"XLSX (fab_lot_id 병합)"))}>⬇ XLSX</button>
            <button onClick={()=>{setEditing(true);clearCellSelection();}} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:14,fontWeight:600,cursor:"pointer"}}>Edit</button>
            {/* v8.4.9-b: 노트 드로어 토글 */}
            <button onClick={()=>{setNoteFilter(null);setNotesOpen(true);}} title="wafer 태그 · 항목 메모" style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--info)",background:"transparent",color:"var(--info)",fontSize:14,fontWeight:600,cursor:"pointer",display:"inline-flex",gap:4,alignItems:"center"}}>📝 노트{notes.length>0&&<span style={{padding:"0 6px",borderRadius:10,background:"rgba(59,130,246,0.95)",color:"var(--bg-secondary)",fontSize:14,fontWeight:700}}>{notes.length}</span>}</button>
          </>}
          <button onClick={startInformFromCurrentSnapshot} disabled={informSnapshotBusy||!data?.rows?.length}
            title="현재 SplitTable 화면을 plan 포함 snapshot 으로 Inform 작성에 첨부"
            style={{padding:"4px 12px",borderRadius:4,border:"1px solid rgba(139,92,246,0.95)",background:"transparent",color:"rgba(139,92,246,0.95)",fontSize:14,fontWeight:600,cursor:informSnapshotBusy||!data?.rows?.length?"not-allowed":"pointer",opacity:informSnapshotBusy||!data?.rows?.length?0.5:1}}>
            {informSnapshotBusy?"Inform 준비...":"Inform 스냅샷"}
          </button>
        </div>
      </div>
      {loading?<div style={{padding:40,textAlign:"center"}}><Loading text="Loading..."/></div>
      :data?.msg&&!data?.rows?.length?<div style={{padding:60,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>{data.msg}</div>
      :tab==="view"&&data?.rows?.length?(()=>{
        // 클라이언트 diff 필터: viewMode==='diff' 이면 non-null unique 값 >= 2 인 행만
        const diffRows = viewMode==="diff"
          ? data.rows.filter(r=>{const vs=Object.values(r._cells||{}).map(c=>c?.actual).filter(v=>v!=null&&v!==""&&v!=="None"&&v!=="null");return new Set(vs).size>=2;})
          : data.rows;
        const isTagRow=(row)=>String(row?._param||"").toUpperCase().startsWith("TAG_")||Object.values(row?._cells||{}).some(c=>c?.is_custom_tag===true);
        // 적용 공정 정보 모드 — 항목 칸을 연결 공정 표기로 바꾸고, 표시할 공정이
        // 하나도 없는 매칭 행(KNOB/INLINE/VM)은 아예 감춘다. TAG/관리 행처럼
        // 매칭 규칙이 없는 행은 원래대로 남는다.
        const viewRows = showParamMeta
          ? diffRows.filter(r=>{const k=matchKindOf(r?._param);return !k||matchStepLines(k,matchMetaFor(k,r?._param),{excludeNotNull:excludeNotNullStepMeta}).length>0;})
          : diffRows;
        // 점진 렌더: 한도까지만 그린다. slice(0,N) 이므로 행 인덱스(선택/paste)는 그대로 유효.
        const displayRows=rowRenderLimit<viewRows.length?viewRows.slice(0,rowRenderLimit):viewRows;
        // module 묶음 열. KNOB/VM 은 Vehicle_matching 의 module 열이 원천이고,
        // TAG 행은 원천이 없어 엔지니어가 직접 적는다(비워두면 그대로 빈 칸).
        // TAG 행이 하나라도 있으면 적을 자리가 있어야 하므로 열을 붙인다.
        // 위아래로 같은 module 이 이어지면 rowSpan 으로 한 칸에 합친다.
        const rowTagFlags=displayRows.map(r=>isTagRow(r));
        const rowModules=displayRows.map((r,i)=>{
          if(rowTagFlags[i])return tagModuleFor(r?._param);
          const k=matchKindOf(r?._param);
          return k?matchModuleOf(matchMetaFor(k,r?._param)):"";
        });
        const showModuleCol=rowModules.some(Boolean)||rowTagFlags.some(Boolean);
        // TAG 행은 열마다 module 이 따로라 위아래로 합치지 않는다 — 행 인덱스를 키에 섞어 항상 span 1.
        const moduleGroupKey=(ri)=>rowTagFlags[ri]?`tag:${ri}`:`m:${rowModules[ri]}`;
        const moduleSpanAt=(ri)=>{
          if(!showModuleCol)return 0;
          if(ri>0&&moduleGroupKey(ri-1)===moduleGroupKey(ri))return 0;   // 위 칸에 합쳐짐
          let n=1;while(ri+n<rowModules.length&&moduleGroupKey(ri+n)===moduleGroupKey(ri))n+=1;
          return n;
        };
        // 서버 step_progress: latest-lot 캐시 기준 현재 진행 step 보다 뒤(미진행) 공정의 행은 진한 회색.
        const fabMissing=data?.step_progress?.fab_missing===true;
        const notReachedParams=new Set(Array.isArray(data?.step_progress?.not_reached)?data.step_progress.not_reached:[]);
        const notReachedStep=data?.step_progress?.step_id||"";
        const waferStepProgress=Object.fromEntries(Object.entries(data?.step_progress?.by_wafer||{}).map(([wafer,meta])=>[
          String(wafer).replace(/^(?:#|WAFER|WF|W)\s*/i,"").replace(/^0+(?=\d)/,""),
          {...(meta||{}),notReached:new Set(Array.isArray(meta?.not_reached)?meta.not_reached:[])},
        ]));
        const hasWaferStepProgress=Object.keys(waferStepProgress).length>0;
        const waferKeyAt=(ci)=>String(data?.wafer_keys?.[ci]??data?.headers?.[ci]??"").replace(/^(?:#|WAFER|WF|W)\s*/i,"").replace(/^0+(?=\d)/,"");
        const waferProgressAt=(ci)=>waferStepProgress[waferKeyAt(ci)]||null;
        const NOT_REACHED_BG="rgba(107,114,128,0.45)";
        // 라벨 셀은 sticky 라 반투명이면 스크롤 시 아래 셀이 비친다 — 불투명 혼색 사용.
        const NOT_REACHED_LABEL_BG="color-mix(in srgb, var(--bg-secondary) 60%, #6b7280)";
        // 회색 판정 보조표. 회색은 "이 wafer 에서 아직 안 온 구간"이고 이유는 둘이다:
        //   ① FAB latest 기준 이 step 이 아직 진행 전 (step_progress)
        //   ② split table 에 이 step 의 split 자체가 없다 (행 전체가 비어 있음)
        // 단, 같은 wafer 열에서 **더 뒤 step 에 split 이 채워져 있으면** 그 앞의 빈
        // 칸은 칠하지 않는다 — 가운데가 빈 건 "아직 안 왔다"가 아니라 그 step 에 값이
        // 없는 것뿐이다. 회색은 마지막으로 채워진 split **뒤에서만** 시작한다.
        //
        // viewRows(잘리기 전 전체)로 계산한다. displayRows 는 스크롤에 따라 늘어나는
        // 앞부분 슬라이스라(인덱스는 같다), 그걸로 재면 스크롤할 때마다 회색이 바뀐다.
        const displayValueOf=(row,ci)=>{
          const cell=(row?._cells||{})[String(ci)];
          if(!cell)return null;
          const {effectiveCell}=effectiveCellFor(cell);
          return hasValue(effectiveCell.plan)?effectiveCell.plan:effectiveCell.actual;
        };
        const headerCount=(data.headers||[]).length;
        const lastFilledRowByCol=new Array(headerCount).fill(-1);
        const rowHasNoSplit=viewRows.map((row,ri)=>{
          let any=false;
          for(let ci=0;ci<headerCount;ci+=1){
            if(hasValue(displayValueOf(row,ci))){any=true;lastFilledRowByCol[ci]=ri;}
          }
          return !any;
        });
        const normalizedSelection=selectedCellRange
          ? normalizeCellRange(selectedCellRange.startRow,selectedCellRange.startCol,selectedCellRange.endRow,selectedCellRange.endCol)
          : null;
        const selectedCellStart=normalizedSelection?{rowIndex:normalizedSelection.startRow,colIndex:normalizedSelection.startCol}:null;
        const activeSelectionStart=selectedCellStart || (selectionAnchor?{rowIndex:selectionAnchor.rowIndex,colIndex:selectionAnchor.colIndex}:null);
        const isCellSelected=(ri,ci)=>!!(normalizedSelection&&ri>=normalizedSelection.startRow&&ri<=normalizedSelection.endRow&&ci>=normalizedSelection.startCol&&ci<=normalizedSelection.endCol);
        // v9.1.x: 셀 선택은 edit 모드에 한정하지 않는다 — 뷰 모드에서도 사각 영역을 잡아
        //   엑셀처럼 그 영역만 복사(onCopy)할 수 있어야 한다. 편집 액션(더블클릭 입력,
        //   Delete plan 삭제, paste)만 editing 으로 게이팅된다.
        const beginCellSelection=(e,ri,ci)=>{
          if(e.button!==0)return;
          if(e.shiftKey&&selectionAnchor){
            const nextRange=normalizeCellRange(selectionAnchor.rowIndex,selectionAnchor.colIndex,ri,ci);
            setSelectedCellRange(nextRange);
          }else{
            setSelectionAnchor({rowIndex:ri,colIndex:ci});
            setSelectedCellRange(normalizeCellRange(ri,ci,ri,ci));
          }
          setActiveCell(null);
          splitTableRef.current?.focus();
          setIsDraggingSelection(true);
        };
        const updateCellSelection=(ri,ci)=>{
          if(!isDraggingSelection||!selectionAnchor)return;
          const nextRange=normalizeCellRange(selectionAnchor.rowIndex,selectionAnchor.colIndex,ri,ci);
          setSelectedCellRange(nextRange);
          setActiveCell(null);
        };
        const handleCellSelection=(e,ri,ci)=>{
          if(e.shiftKey&&selectionAnchor){
            const nextRange=normalizeCellRange(selectionAnchor.rowIndex,selectionAnchor.colIndex,ri,ci);
            setSelectedCellRange(nextRange);
          }else{
            setSelectionAnchor({rowIndex:ri,colIndex:ci});
            setSelectedCellRange(normalizeCellRange(ri,ci,ri,ci));
          }
          setActiveCell(null);
          splitTableRef.current?.focus();
          setIsDraggingSelection(false);
        };
        const handleSplitPaste=(e)=>{
          if(!editing||!activeSelectionStart||activeCell)return;
          const target=e.target||{};
          const tagName=String(target.tagName||"").toLowerCase();
          if(tagName==="input"||tagName==="textarea"||tagName==="select"||target.isContentEditable)return;
          const raw=e.clipboardData?.getData("text/plain");
          if(!raw)return;
          const rows=String(raw).replace(/\r\n?/g,"\n").split("\n");
          const matrix=rows
            .filter((line,idx)=>!(idx===rows.length-1&&line===""))
            .map(line=>line.split("\t"));
          if(!matrix.length||matrix.every(r=>r.length===1&&r[0]===""))return;
          const maxRow=displayRows?.length||0;
          const maxCol=(data?.headers||[]).length||0;
          e.preventDefault();
          let changed=0;
          const nextPlans={};
          const nextTags={};
          const nextManagement={};
          matrix.forEach((rowVals,offR)=>{
            const rowIndex=activeSelectionStart.rowIndex+offR;
            if(rowIndex>=maxRow)return;
            const rowData=displayRows[rowIndex];
            if(!rowData||!rowData._cells)return;
            const cells=rowData._cells||{};
            rowVals.forEach((rawValue,offC)=>{
              const colIndex=activeSelectionStart.colIndex+offC;
              if(colIndex>=maxCol)return;
              const cell=cells[String(colIndex)];
              if(!cell||!cell.key)return;
              const canTag=cell.is_custom_tag===true;
              const canManagement=cell.is_management_row===true;
              const canPlan=cell.can_plan!==false;
              if(!canTag&&!canManagement&&!canPlan)return;
              const nextVal=String(rawValue||"").trim();
              if(!nextVal)return;
              if(canTag)nextTags[cell.key]=nextVal;
              else if(canManagement)nextManagement[cell.key]=nextVal;
              else nextPlans[cell.key]=nextVal;
              changed++;
            });
          });
          if(Object.keys(nextPlans).length)setPendingPlans(p=>({...p,...nextPlans}));
          if(Object.keys(nextTags).length)setPendingTags(p=>({...p,...nextTags}));
          if(Object.keys(nextManagement).length)setPendingManagement(p=>({...p,...nextManagement}));
        };
        // v9.1.x: Edit 모드에서 멀티셀 선택 후 Delete/Backspace → 선택 범위의 plan 일괄 삭제.
        //   미저장 pending plan 은 상태에서 제거하고, 저장된 plan 은 한 번의 /plan/delete 로 지운다.
        const handleSplitKeyDown=(e)=>{
          if(!editing||activeCell)return;
          if(e.key!=="Delete"&&e.key!=="Backspace")return;
          if(!normalizedSelection)return;
          const target=e.target||{};
          const tagName=String(target.tagName||"").toLowerCase();
          if(tagName==="input"||tagName==="textarea"||tagName==="select"||target.isContentEditable)return;
          const savedKeys=[];const pendingKeys=[];
          for(let ri=normalizedSelection.startRow;ri<=normalizedSelection.endRow;ri++){
            const rowData=displayRows[ri];if(!rowData||!rowData._cells)continue;
            const cells=rowData._cells;
            for(let ci=normalizedSelection.startCol;ci<=normalizedSelection.endCol;ci++){
              const cell=cells[String(ci)];
              if(!cell||!cell.key||cell.can_plan===false)continue;
              if(Object.prototype.hasOwnProperty.call(pendingPlans,cell.key))pendingKeys.push(cell.key);
              if(hasValue(cell.plan))savedKeys.push(cell.key);
            }
          }
          if(!savedKeys.length&&!pendingKeys.length)return;
          e.preventDefault();
          if(pendingKeys.length)setPendingPlans(p=>{const n={...p};pendingKeys.forEach(k=>delete n[k]);return n;});
          if(savedKeys.length){
            sf(API+"/plan/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,cell_keys:savedKeys,username:user?.username||""})})
              .then(()=>{toast.ok(`plan ${savedKeys.length}건 삭제`);loadView();})
              .catch(err=>toast.error(err.message||"plan 삭제 실패"));
          }else if(pendingKeys.length){
            toast.ok(`미저장 plan ${pendingKeys.length}건 제거`);
          }
        };
        // v9.1.x: Ctrl+C — 브라우저 기본 복사는 행 전체 텍스트를 잡으므로, 선택한 사각
        //   셀 영역만 엑셀처럼 TSV(탭=열, 개행=행)로 클립보드에 넣는다.
        const handleSplitCopy=(e)=>{
          if(!normalizedSelection)return;
          const target=e.target||{};
          const tagName=String(target.tagName||"").toLowerCase();
          if(tagName==="input"||tagName==="textarea"||tagName==="select"||target.isContentEditable)return;
          const lines=[];
          for(let ri=normalizedSelection.startRow;ri<=normalizedSelection.endRow;ri++){
            const rowData=displayRows[ri];const cells=rowData?._cells||{};
            const cols=[];
            for(let ci=normalizedSelection.startCol;ci<=normalizedSelection.endCol;ci++){
              const cell=cells[String(ci)];let val="";
              if(cell){
                const {effectiveCell}=effectiveCellFor(cell);
                const raw=hasValue(effectiveCell.plan)?effectiveCell.plan:effectiveCell.actual;
                val=hasValue(raw)?String(formatCell(raw,rowData._param)??raw):"";
              }
              cols.push(val);
            }
            lines.push(cols.join("\t"));
          }
          e.preventDefault();
          const tsv=lines.join("\n");
          if(e.clipboardData)e.clipboardData.setData("text/plain",tsv);
          else if(navigator.clipboard)navigator.clipboard.writeText(tsv).catch(()=>{});
        };
        // 요약 토글이 켜졌을 때만 계산 (매 렌더 전체 행 × knobLookup 비용 절약)
        const lineageSummary = showLineageSummary ? buildLineageSummary(displayRows) : [];
        const headerGroupLabels = [...new Set((data.header_groups||[]).map(g=>String(g?.label||"").trim()).filter(Boolean))];
        const lotHeaderRoot = String(data.root_lot_id||lotId||"").trim();
        const lotHeaderLot = String((fabLotId||"").trim() || headerGroupLabels.join(", ") || lotId || "").trim();
        const hasLotContext = !!(lotHeaderRoot || lotHeaderLot);
        const rowLabels = data.row_labels || {};
        const rootRowLabel = rowLabels.root_lot_id || "root_lot_id";
        const lotRowLabel = rowLabels.lot_id || "lot_id";
        const paramRowLabel = rowLabels.parameter || "항목";
        const hasRootRow = hasLotContext;
        const hasLotRow = hasLotContext || data.header_groups?.length>0;
        const rootHeaderHeight = hasRootRow ? 32 : 0;
        const lotHeaderHeight = hasLotRow ? 24 : 0;
        const paramHeaderTop = rootHeaderHeight + lotHeaderHeight;
        const lotContextTitle = `root_lot_id: ${lotHeaderRoot || "-"}\nlot_id: ${lotHeaderLot || "-"}`;
        const splitLikeSource={
          ...data,
          rows:displayRows,
          root_lot_id:lotHeaderRoot,
          lot_id_label:lotHeaderLot,
          prefix_columns:SPLIT_CHECK_PREFIX_COLUMNS,
          row_labels:{...(data.row_labels||{}),parameter:"항목"},
        };
        const splitLikeBuildOptions={
          valueForCell:(cell,row)=>{
            if(!cell)return "";
            const {effectiveCell}=effectiveCellFor(cell);
            const raw=hasValue(effectiveCell.plan)?effectiveCell.plan:effectiveCell.actual;
            return hasValue(raw)?raw:"";
          },
          displayForValue:(raw,row)=>String(formatCell(raw,row._param) ?? raw),
          // Split 체크 뷰에서도 "적용 공정 정보" 는 그리드와 똑같이 보여야 한다.
          labelForParam:(param)=>{
            if(!showParamMeta)return "";
            const kind=matchKindOf(param);
            if(!kind)return "";
            const lines=matchStepLines(kind,matchMetaFor(kind,param),{excludeNotNull:excludeNotNullStepMeta});
            return lines.length?lines.join("\n"):"";
          },
        };
        // Split 체크와 PEMS는 같은 항목/값/S그룹 골격을 공유한다.
        const splitCheckStView=!splitCheckViewActive?null:buildSplitCheckStView({
          ...splitLikeSource,
          display_mode:"split_check",
        },splitLikeBuildOptions);
        const pemsStView=!pemsViewActive?null:buildPemsStView({
          ...splitLikeSource,
          display_mode:"pems",
        },splitLikeBuildOptions);
        return <div ref={splitTableRef} tabIndex={0} onPaste={handleSplitPaste} onCopy={handleSplitCopy} onKeyDown={handleSplitKeyDown} onMouseUp={()=>setIsDraggingSelection(false)} onMouseLeave={()=>setIsDraggingSelection(false)} style={{flex:1,overflow:"auto",background:"var(--bg-card)"}}>
        {data.background_cache?.queued&&<div style={{padding:"7px 10px",fontSize:14,fontWeight:600,color:"rgba(30,64,175,0.95)",background:"rgba(59,130,246,0.10)",borderBottom:"1px solid rgba(59,130,246,0.28)"}}>{data.background_cache.message||"관련 캐시를 백그라운드에서 준비 중입니다."}</div>}
        {data.lot_warn&&<div style={{padding:"7px 10px",fontSize:14,fontWeight:600,color:"rgba(180,83,9,0.95)",background:"rgba(251,191,36,0.14)",borderBottom:"1px solid rgba(251,191,36,0.35)"}}>{data.lot_warn}</div>}
        {Array.isArray(data.lot_management_purposes)&&data.lot_management_purposes.length>0&&<div style={{padding:"8px 10px",fontSize:14,lineHeight:1.55,color:"var(--text-primary)"}}>
          <div style={{fontWeight:700,color:"var(--text-secondary)",marginBottom:2}}>LOT 관리 purpose</div>
          {data.lot_management_purposes.map((item,index)=><div key={`${item.lot_id}-${index}`} style={{whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{item.lot_id} · {item.purpose}</div>)}
        </div>}
        {Array.isArray(data.related_issues)&&data.related_issues.length>0&&<div style={{padding:"8px 10px",display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",fontSize:14,background:"rgba(59,130,246,0.10)",borderBottom:"1px solid rgba(59,130,246,0.28)"}}>
          <span style={{fontWeight:800,color:"rgba(59,130,246,0.95)",fontFamily:"monospace"}}>이슈추적 {data.related_issues.length}건</span>
          {data.related_issues.slice(0,6).map(iss=><button key={iss.id} onClick={()=>openTrackerIssue(iss.id)} title={`${iss.title}\n${iss.category||"-"} · ${iss.status||"-"} · ${iss.updated_at||""}`}
            style={{display:"inline-flex",alignItems:"center",gap:5,maxWidth:260,padding:"3px 8px",borderRadius:999,border:"1px solid rgba(59,130,246,0.45)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:14,fontWeight:700,cursor:"pointer"}}>
            <span style={{width:7,height:7,borderRadius:"50%",background:iss.status==="closed"?"rgba(34,197,94,0.95)":"rgba(249,115,22,0.95)",flexShrink:0}}/>
            <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{iss.title||iss.id}</span>
            {iss.matched_wafers?.length>0&&<span style={{color:"var(--text-secondary)",fontFamily:"monospace",flexShrink:0}}>W{iss.matched_wafers.slice(0,3).join(",")}</span>}
            {iss.comment_count>0&&<span style={{color:"var(--text-secondary)",fontFamily:"monospace",flexShrink:0}}>댓글 {iss.comment_count}</span>}
          </button>)}
          {data.related_issues.length>6&&<span style={{fontSize:14,color:"var(--text-secondary)"}}>+{data.related_issues.length-6}</span>}
        </div>}
        {/* v8.8.13: 빈 셀 / knobMeta 확장 행에서 테두리 끊기는 현상 — 전체 td/th 기본 border 강제.
            inline style(borderLeft plan 등)은 specificity 가 높아 유지됨. */}
        {/* plan 과 다르게 진행된 칸은 진한 빨강으로 채우므로 글자만 흰색으로 예외를 준다.
            위 규칙이 모든 자손에 !important 로 걸려 있어 인라인 color 로는 못 이긴다.
            메모 배지(.stm-note-btn)는 자기 배경색을 갖고 있어 제외한다. */}
        <style>{`.splittable-grid td, .splittable-grid th { border: none; }
          .splittable-grid td, .splittable-grid th, .splittable-grid td *, .splittable-grid th * { color: ${GRID_TEXT} !important; }
          .splittable-grid td.stm-mismatch, .splittable-grid td.stm-mismatch *:not(.stm-note-btn) { color: #fff !important; }
          .splittable-grid td.stm-cell { user-select: none; }
          .splittable-grid td.stm-module-edit .stm-module-hint { opacity: 0; transition: opacity 0.15s; }
          .splittable-grid td.stm-module-edit:hover .stm-module-hint { opacity: 1; }`}</style>
        {splitCheckViewActive||pemsViewActive ? (
        <SplitTableSnapshotView
          stView={pemsViewActive?pemsStView:splitCheckStView}
          product={selProd}
          showTitle={false}
          emptyMessage={pemsViewActive?"PEMS로 표시할 값이 없습니다":"Split 체크로 표시할 값이 없습니다"}
          maxHeight="none"
        />
        ) : (
        <table className="splittable-grid" style={{borderCollapse:"separate",borderSpacing:0,borderTop:GRID_LINE,borderLeft:GRID_LINE,fontSize:14,background:"var(--bg-card)",tableLayout:"fixed",width:288+(showModuleCol?MODULE_COL_W:0)+(data.headers?.length||1)*115}}>
          <colgroup>
            {showModuleCol&&<col style={{width:MODULE_COL_W}}/>}
            <col style={{width:288}}/>
            {data.headers?.map((_,i)=><col key={i} style={{width:115}}/>)}
          </colgroup>
          <thead>
            {hasRootRow&&(()=>{const lotN=notesForLot().length;const drawerRoot=lotHeaderRoot || data.root_lot_id || lotId || "-";return(<tr style={{height:rootHeaderHeight}}>
              {showModuleCol&&<th style={{boxSizing:"border-box",height:rootHeaderHeight,background:"var(--bg-tertiary)",borderBottom:GRID_LINE,borderRight:GRID_LINE,position:"sticky",top:0,left:0,zIndex:5}}/>}
              <th title={lotContextTitle} style={{boxSizing:"border-box",height:rootHeaderHeight,padding:"4px 8px",background:"var(--bg-tertiary)",borderBottom:GRID_LINE,borderRight:GRID_LINE,position:"sticky",top:0,left:showModuleCol?MODULE_COL_W:0,zIndex:5,textAlign:"left",fontSize:14,lineHeight:1.25,color:GRID_TEXT,fontWeight:800,whiteSpace:"normal",wordBreak:"break-word"}}>
                {rootRowLabel}
              </th>
              <th colSpan={data.headers?.length||1} style={{boxSizing:"border-box",height:rootHeaderHeight,textAlign:"center",padding:"0 8px",lineHeight:`${rootHeaderHeight-1}px`,fontWeight:700,fontSize:14,color:GRID_TEXT,background:"var(--bg-tertiary)",borderBottom:GRID_LINE,position:"sticky",top:0,zIndex:4,cursor:"pointer"}} title={lotN>0?`LOT ${drawerRoot} — ${lotN}개 태그 · 클릭해서 보기`:`LOT ${drawerRoot} — 태그 추가`} onClick={()=>{setNoteFilter({scope:"lot"});setNoteDraftScope({scope:"lot",product:selProd,root_lot_id:lotId});setNotesOpen(true);}}>{drawerRoot}{lotN>0&&<span style={{marginLeft:8,padding:"0 6px",borderRadius:10,background:"rgba(16,185,129,0.95)",color:"var(--bg-secondary)",fontSize:14,fontWeight:700}}>📦 {lotN}</span>}{viewMode==="diff"?<span style={{marginLeft:8,fontSize:14,color:GRID_TEXT,fontWeight:400}}>(diff: {viewRows.length}/{data.rows.length})</span>:null}</th></tr>);})()}
            {hasLotRow&&<tr style={{height:lotHeaderHeight}}>
              {showModuleCol&&<th style={{boxSizing:"border-box",height:lotHeaderHeight,background:"var(--bg-tertiary)",borderBottom:GRID_LINE,borderRight:GRID_LINE,position:"sticky",top:rootHeaderHeight,left:0,zIndex:5}}/>}
              <th style={{boxSizing:"border-box",height:lotHeaderHeight,padding:"0 8px",background:"var(--bg-tertiary)",borderBottom:GRID_LINE,borderRight:GRID_LINE,position:"sticky",top:rootHeaderHeight,left:showModuleCol?MODULE_COL_W:0,zIndex:5,textAlign:"left",fontSize:14,color:GRID_TEXT,fontWeight:800}} title={lotContextTitle}>{lotRowLabel}</th>
              {data.header_groups?.length>0
                ? data.header_groups.map((g,gi)=><th key={gi} colSpan={g.span} style={{boxSizing:"border-box",height:lotHeaderHeight,textAlign:"center",padding:"0 6px",fontWeight:800,fontSize:14,color:GRID_TEXT,background:"var(--bg-tertiary)",borderBottom:GRID_LINE,borderRight:GRID_LINE,position:"sticky",top:rootHeaderHeight,zIndex:4,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={g.label}>{g.label}</th>)
                : <th colSpan={data.headers?.length||1} style={{boxSizing:"border-box",height:lotHeaderHeight,textAlign:"center",padding:"0 6px",fontWeight:800,fontSize:14,color:GRID_TEXT,background:"var(--bg-tertiary)",borderBottom:GRID_LINE,borderRight:GRID_LINE,position:"sticky",top:rootHeaderHeight,zIndex:4,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={lotHeaderLot}>{lotHeaderLot || "-"}</th>}
            </tr>}
            <tr>
            {showModuleCol&&<th style={{textAlign:"center",padding:"8px 6px",fontWeight:700,fontSize:13,color:GRID_TEXT,borderBottom:GRID_LINE_STRONG,borderRight:GRID_LINE,background:"var(--bg-tertiary)",position:"sticky",top:paramHeaderTop,left:0,zIndex:5}}>module</th>}
            <th style={{textAlign:"left",padding:"8px 10px",fontWeight:700,fontSize:14,color:GRID_TEXT,borderBottom:GRID_LINE_STRONG,borderRight:GRID_LINE,background:"var(--bg-tertiary)",position:"sticky",top:paramHeaderTop,left:showModuleCol?MODULE_COL_W:0,zIndex:5,minWidth:260}}>{paramRowLabel}</th>
            {data.headers?.map((h,i)=>{const wid=String(h).replace(/^#/,"");const wn=notesForWafer(wid).length;return(<th key={i} style={{textAlign:"center",padding:"6px 8px",fontWeight:600,fontSize:14,color:GRID_TEXT,borderBottom:GRID_LINE_STRONG,borderRight:GRID_LINE,background:"var(--bg-tertiary)",position:"sticky",top:paramHeaderTop,zIndex:3,whiteSpace:"normal",wordBreak:"break-word",minWidth:100,cursor:"pointer"}} title={wn>0?`wafer ${h} — ${wn}개 태그 · 클릭해서 보기`:`wafer ${h} — 태그 추가`} onClick={()=>{setNoteFilter({scope:"wafer",key:`${selProd}__${lotId}__W${wid}`});setNoteDraftScope({scope:"wafer",product:selProd,root_lot_id:lotId,wafer_id:wid});setNotesOpen(true);}}>
              <div>{h}</div>
              {wn>0&&<span style={{display:"inline-block",marginTop:2,padding:"0 6px",borderRadius:10,background:"rgba(59,130,246,0.95)",color:"var(--bg-secondary)",fontSize:14,fontWeight:700}}>🏷 {wn}</span>}
            </th>);})}
          </tr></thead>
          <tbody>{displayRows.map((row,ri)=>{
            const cells=row._cells||{};
            // v8.4.5/v9.x: saved/pending plan 값도 uniqMap 에 포함 — 같은 값이면 같은 팔레트 색상
            const allVals=[];Object.values(cells).forEach(c=>{[c?.actual,c?.plan,pendingValueFor(c),pendingManagementValueFor(c)].forEach(v=>{if(hasValue(v))allVals.push(String(v));});});
            const uniqVals=[...new Set(allVals)];const uniqMap={};uniqVals.forEach((v,i)=>{uniqMap[v]=i;});
            const rowParam=String(row?._param || "").trim();
            const rowIsTag=isTagRow(row);
            const rowKnob=knobLookup(rowParam);
            const rowMatchKind=matchKindOf(rowParam);
            const matchTitle = rowMatchKind==="knob_ppid"?"KNOB 매칭 규칙"
              :rowMatchKind==="inline_matching"?"INLINE 매칭 규칙"
              :rowMatchKind==="vm_matching"?"VM 매칭 규칙":"";
            const rowKnobStepTitle = rowMatchKind==="knob_ppid"
              ? knobStepSummaryText(rowKnob?.groups||[],{excludeNotNull:excludeNotNullStepMeta})
              : "";
            // 적용공정 표기 — KNOB 은 step_desc 내부 step_id는 줄바꿈하고,
            // 같은 rule_order의 서로 다른 유효 step_desc 사이에만 `&`를 둔다.
            const rowStepLines = showParamMeta&&rowMatchKind
              ? matchStepLines(rowMatchKind,matchMetaFor(rowMatchKind,rowParam),{excludeNotNull:excludeNotNullStepMeta})
              : [];
            const rowModuleSpan = moduleSpanAt(ri);
            const cellDisplayValueAt=(ci)=>{
              const cell=cells[String(ci)];
              if(!cell)return null;
              const {effectiveCell}=effectiveCellFor(cell);
              return hasValue(effectiveCell.plan)?effectiveCell.plan:effectiveCell.actual;
            };
            // 회색은 빈 셀에만 붙는다. FAB에 아직 없어도 KNOB/INLINE/VM 실제값이나
            // 표시할 plan 값이 있으면 그 셀의 값/팔레트 색을 보존한다.
            // 위 lastFilledRowByCol / rowHasNoSplit 주석 참조.
            const cellNotReachedAt=(ci)=>{
              if(hasValue(cellDisplayValueAt(ci)))return false;
              // 이 wafer 열에서 더 뒤 step 에 split 이 채워져 있으면 여긴 아직 회색이 아니다.
              if(ri<lastFilledRowByCol[ci])return false;
              const progressNotReached=hasWaferStepProgress
                ? waferProgressAt(ci)?.notReached?.has(rowParam)===true
                : notReachedParams.has(rowParam);
              return progressNotReached||rowHasNoSplit[ri]===true;
            };
            const waferNotReachedFlags=(data.headers||[]).map((_,ci)=>cellNotReachedAt(ci));
            const waferNotReachedCount=waferNotReachedFlags.filter(Boolean).length;
            const rowNotReached=waferNotReachedFlags.length>0&&waferNotReachedCount===waferNotReachedFlags.length;
            const notReachedTitle=rowHasNoSplit[ri]&&waferNotReachedCount
              ? "\n⏳ 이 step 의 split 이 없습니다 — 뒤쪽에 채워진 split 이 없어 회색으로 표시합니다."
              : fabMissing&&waferNotReachedCount
              ? "\n⏳ FAB 매칭 없음 — 아직 FAB에 없는 공정으로 회색 표시합니다."
              : hasWaferStepProgress
              ? (waferNotReachedCount?`\n⏳ 미진행 wafer ${waferNotReachedCount}/${waferNotReachedFlags.length} — wafer별 latest step 기준입니다.`:"")
              : (rowNotReached?`\n⏳ 미진행 — 현재 lot 진행 step(${notReachedStep||"?"}) 이후 공정입니다.`:"");
            return(<tr key={ri}>
              {/* TAG 행의 module 칸만 클릭해서 직접 적는다. 비어 있으면 빈 칸으로 두고
                  hover 했을 때만 + 힌트를 보여준다. 매칭 행은 원천이 CSV 라 읽기 전용. */}
              {showModuleCol&&rowModuleSpan>0&&(()=>{const rowMod=rowModules[ri];const modEditable=rowTagFlags[ri];return(
                <td rowSpan={rowModuleSpan} className={modEditable?"stm-module-edit":undefined}
                  onClick={modEditable?(e)=>{e.stopPropagation();promptSetCustomTagModule(rowParam,rowMod);}:undefined}
                  title={modEditable?(rowMod?`module ${rowMod} — 클릭해서 수정`:"module 미입력 — 클릭해서 입력"):(rowMod?`module ${rowMod}`:"module 미지정")}
                  style={{padding:"6px 8px",fontWeight:800,fontSize:13,color:GRID_TEXT,borderBottom:GRID_LINE,borderRight:GRID_LINE,background:"var(--bg-tertiary)",position:"sticky",left:0,zIndex:2,textAlign:"center",verticalAlign:"middle",whiteSpace:"normal",wordBreak:"break-word",cursor:modEditable?"pointer":"default"}}>
                  {modEditable
                    ?(rowMod||<span className="stm-module-hint" style={{fontWeight:400,color:"var(--text-secondary)"}}>+</span>)
                    :(rowMod||"—")}
                </td>);})()}
              {(()=>{const pLotN=notesForParam(row._param).length;return(
              <td style={{padding:"6px 10px",fontWeight:600,fontSize:14,color:GRID_TEXT,borderBottom:GRID_LINE,borderRight:GRID_LINE,background:rowNotReached?NOT_REACHED_LABEL_BG:"var(--bg-secondary)",position:"sticky",left:showModuleCol?MODULE_COL_W:0,zIndex:2,whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35,cursor:"pointer"}} title={(pLotN>0?`${rowParam} — lot내 ${pLotN}개 태그 · 클릭해서 보기`:`${rowParam} — 태그 보기/추가`)+(rowKnobStepTitle?`\n${rowKnobStepTitle}`:"")+notReachedTitle} onClick={()=>{setNoteFilter({scope:"param",param:rowParam});setNoteDraftScope(null);setNotesOpen(true);}}>
                <div style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap"}}>
                {/* v8.8.14: _display 가 있으면(KNOB/INLINE/VM 에서 rule_order+step_desc 끼워 넣은 이름) 그것을, 없으면 raw _param 을 prefix strip 해서 표시.
                    v10.0.8: KNOB 항목은 꼬리 `_Split` 도 라벨에서만 제거 (raw key 는 title/편집/plan 에 그대로).
                    KNOB/INLINE/VM 항목은 클릭 시 매칭 규칙 모달이 열리고, 설정값은 그때만 표출한다. */}
                  <span onClick={(e)=>{if (!rowMatchKind) return; e.stopPropagation(); openRuleMatchView(rowMatchKind,rowParam,row);}}
                    title={rowMatchKind ? `${rowParam}\n이 항목의 ${matchTitle} 보기` : ""}
                    style={rowMatchKind ? {cursor:"pointer",color:GRID_TEXT} : undefined}>
                    {/* 적용 공정 정보 모드에서는 항목명 대신 연결 공정을 줄바꿈으로 쌓는다. */}
                    {rowStepLines.length
                      ? <span style={{display:"inline-flex",flexDirection:"column",gap:2,fontSize:13,letterSpacing:0.2}}>
                          {rowStepLines.map((line,li)=><span key={li} style={{whiteSpace:"pre-line"}}>{line}</span>)}
                        </span>
                      : splitParamDisplayName(row._display||rowParam||"",rowParam)}
                  </span>
                  {pLotN>0&&<span style={{padding:"0 5px",borderRadius:8,background:"rgba(139,92,246,0.95)",color:"var(--bg-secondary)",fontSize:14,fontWeight:700}}>💬 {pLotN}</span>}
                  {rowIsTag&&canManage&&<button onClick={(e)=>{e.stopPropagation();deleteCustomTagColumn(rowParam);}} title="TAG 열 삭제"
                    style={{marginLeft:"auto",padding:"0 6px",height:20,borderRadius:3,border:"1px solid rgba(239,68,68,0.65)",background:"transparent",color:"rgba(239,68,68,0.95)",fontSize:14,fontWeight:800,cursor:"pointer",lineHeight:"18px"}}>×</button>}
                </div>
              </td>);})()}
              {(mergedViewActive&&!editing&&isMergeableParam(row._param))?(()=>{
                // v9.1.x: 병합 표시 — 왼쪽 칸과 같은 값이면 colSpan 으로 합쳐 한눈에 보이게 (읽기 전용).
                // KNOB/FAB/MASK 행만 대상이다. INLINE/VM/TAG 는 아래 일반 셀 경로로 그린다.
                const groups=[];let cur=null;
                (data.headers||[]).forEach((_,ci)=>{
                  const paintVal=cellDisplayValueAt(ci);
                  const mergedCellNotReached=cellNotReachedAt(ci);
                  const key=hasValue(paintVal)?String(paintVal):"";
                  if(cur&&cur.key===key&&cur.notReached===mergedCellNotReached){cur.span+=1;}
                  else{cur={key,paintVal,notReached:mergedCellNotReached,span:1,start:ci};groups.push(cur);}
                });
                return groups.map(g=>{
                  const disp=g.key?String(formatCell(g.paintVal,row._param)??g.paintVal):"";
                  const bgStyle=g.key?getCellBg(g.paintVal,uniqMap,row._param):{};
                  const wStart=String(data.headers[g.start]??"");
                  const wEnd=String(data.headers[g.start+g.span-1]??"");
                  return(<td key={g.start} colSpan={g.span}
                    title={g.span>1?`${wStart}~${wEnd} · ${g.span}개 wafer 동일 값`:undefined}
                    style={{background:"var(--bg-card)",color:"var(--text-primary)",...bgStyle,...(g.notReached?{background:NOT_REACHED_BG}:{}),padding:"4px 8px",borderBottom:GRID_LINE,borderRight:GRID_LINE,textAlign:"center",fontSize:14,whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35,fontWeight:g.span>1?700:400}}>
                    {disp}
                  </td>);
                });
              })():data.headers?.map((_,ci)=>{
                const cell=cells[String(ci)];const wid=String(data.headers[ci]??"").replace(/^#/,"");
                const cellNotReached=cellNotReachedAt(ci);
                const cellNoteCount=notesForCell(wid,row._param).length;
                if(!cell)return(<td key={ci} className="stm-cell"
                  onMouseDown={(e)=>{beginCellSelection(e,ri,ci);}}
                  onMouseEnter={(e)=>{if(e.buttons===1)updateCellSelection(ri,ci);}}
                  onMouseUp={()=>setIsDraggingSelection(false)}
                  style={{borderBottom:GRID_LINE,borderRight:GRID_LINE,background:cellNotReached?NOT_REACHED_BG:"var(--bg-card)",position:"relative",outline:isCellSelected(ri,ci)?"2px solid rgba(59,130,246,0.9)":"none",outlineOffset:-1}}>
                  {cellNoteCount>0&&<span onClick={e=>{e.stopPropagation();setNoteFilter({scope:"cell",wafer_id:wid,param:row._param});setNoteDraftScope({scope:"param",product:selProd,root_lot_id:lotId,wafer_id:wid,param:row._param});setNotesOpen(true);}} title={`${cellNoteCount}개 메모`} style={{position:"absolute",top:1,right:2,cursor:"pointer",fontSize:14,padding:"0 5px",borderRadius:7,background:"rgba(139,92,246,0.95)",color:"var(--bg-secondary)",fontWeight:700,lineHeight:"14px"}}>💬 {cellNoteCount}</span>}
                </td>);
                const {effectiveCell,pendingPlan,pendingTag,pendingMgmt}=effectiveCellFor(cell);
                const isCustomTag=cell.is_custom_tag===true;
                const isManagementRow=cell.is_management_row===true;
                const paintVal=hasValue(effectiveCell.plan)?effectiveCell.plan:effectiveCell.actual;
                const bgStyle=getCellBg(paintVal,uniqMap,row._param);const planStyle=getCellPlanStyle(effectiveCell);
                const canPlan=cell.can_plan!==false; // default true for backward compat
                const baseStyle={background:isCustomTag?"rgba(59,130,246,0.06)":isManagementRow?"rgba(16,185,129,0.05)":"var(--bg-card)",color:"var(--text-primary)"};
                const canEdit=isCustomTag||isManagementRow||canPlan;
                const style={...baseStyle,...bgStyle,...planStyle,...(cellNotReached?{background:NOT_REACHED_BG}:{}),padding:"4px 8px",borderBottom:GRID_LINE,borderRight:GRID_LINE,textAlign:"center",fontSize:14,cursor:canEdit?"pointer":"default",whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35,position:"relative",outline:isCellSelected(ri,ci)?"2px solid rgba(59,130,246,0.9)":"none",outlineOffset:-1,boxShadow:isCellSelected(ri,ci)?"inset 0 0 0 1px rgba(147,197,253,0.35)":"none"};
                const hasPlan=hasValue(effectiveCell.plan)&&!hasValue(effectiveCell.actual);
                const isMismatch=(hasValue(effectiveCell.plan)&&hasValue(effectiveCell.actual)&&String(effectiveCell.plan)!==String(effectiveCell.actual))||false;
                const display=formatCell(cell.actual,row._param)||"";
                const openEdit=()=>{if(!canEdit)return;
                  // 자동으로 editing 모드 진입 (dbl-click 시 Edit 버튼 클릭 없이도 작동)
                  setSelectedCellRange(normalizeCellRange(ri,ci,ri,ci));
                  setSelectionAnchor({rowIndex:ri,colIndex:ci});
                  if(!editing)setEditing(true);
                  const editValue=isCustomTag
                    ?(pendingTag!==undefined?pendingTag:(cell.actual ?? ""))
                    :isManagementRow
                    ?(pendingMgmt!==undefined?pendingMgmt:(cell.actual ?? ""))
                    :(pendingPlan!==undefined?pendingPlan:(cell.plan ?? cell.actual ?? ""));
                  setActiveCell({key:cell.key,param:row._param,value:editValue,kind:isCustomTag?"tag":(isManagementRow?"management":"plan")});
                  // suggestion 캐시 확인 후 없으면 fetch
                  if(!colValCache[row._param]){
                    sf(API+"/column-values?product="+encodeURIComponent(selProd)+"&col="+encodeURIComponent(row._param)+"&limit=200")
                      .then(d=>setColValCache(m=>({...m,[row._param]:d.values||[]}))).catch(()=>{});
                  }
                  };
                return(<td key={ci} className={"stm-cell"+(isMismatch?" stm-mismatch":"")} style={style}
                  onMouseDown={(e)=>{beginCellSelection(e,ri,ci);}}
                  onMouseEnter={(e)=>{if(e.buttons===1)updateCellSelection(ri,ci);}}
                  onMouseUp={()=>setIsDraggingSelection(false)}
                  onClick={(e)=>{if(editing&&canEdit)handleCellSelection(e,ri,ci);}}
                  onDoubleClick={()=>{if(canEdit)openEdit();}}
                  onContextMenu={e=>{if(cell.plan){e.preventDefault();deletePlan(cell.key);}}}
                  title={isCustomTag
                    ? "꼬리표 값 입력 가능. 원본 파일은 수정하지 않고 flow-data에 저장됩니다."
                    : isManagementRow
                    ? "관리 행 값 입력 가능. 원본 파일은 수정하지 않고 flow-data에 저장됩니다."
                    : canPlan
                    ? (cell.actual ? "actual 값이 있어도 plan 입력/수정 가능. plan 이 있으면 왼쪽에 파란선, plan 과 다르게 진행되면 칸이 빨간색으로 표시됩니다." : "plan 입력 가능")
                    : "이 항목은 plan 입력 대상이 아닙니다"}>
                  {isCustomTag&&pendingTag!==undefined?<span style={{color:"rgba(37,99,235,0.95)",fontWeight:700}}>{pendingTag}</span>
                  :isCustomTag?<span style={{color:display?"var(--text-primary)":"var(--text-secondary)",fontWeight:display?700:400}}>{display}</span>
                  :isManagementRow&&pendingMgmt!==undefined?<span style={{color:"rgba(5,150,105,0.95)",fontWeight:700}}>{pendingMgmt}</span>
                  :isManagementRow?<span style={{color:display?"var(--text-primary)":"var(--text-secondary)",fontWeight:display?700:400}}>{display}</span>
                  :pendingPlan!==undefined?<span style={{color:"#ea580c",fontWeight:700,fontStyle:"italic"}}>{"📌 "}{pendingPlan}</span>
                  /* 진한 빨강 배경 위라 글자는 흰색이다 (getCellPlanStyle 과 한 쌍). */
                  :isMismatch?<span style={{color:"#fff",fontWeight:800}}>{"✗ "}{formatCell(effectiveCell.actual,row._param)}<span style={{fontSize:14,color:"rgba(255,255,255,0.85)"}}>{" (≠"+effectiveCell.plan+")"}</span></span>
                  :hasPlan?<span style={{fontStyle:"italic",fontWeight:700}}>{"📌 "}{effectiveCell.plan}</span>
                  :display}
                  {/* v8.4.9-c: per-cell 메모 배지. 메모가 있으면 항상 표시, 없으면 hover 시에만 + 아이콘 노출. */}
                  <span className="stm-note-btn" onClick={e=>{e.stopPropagation();setNoteFilter({scope:"cell",wafer_id:wid,param:row._param});setNoteDraftScope({scope:"param",product:selProd,root_lot_id:lotId,wafer_id:wid,param:row._param});setNotesOpen(true);}} title={cellNoteCount>0?`${cellNoteCount}개 메모`:"메모 추가"} style={{position:"absolute",top:1,right:2,cursor:"pointer",fontSize:14,padding:"0 5px",borderRadius:7,background:cellNoteCount>0?"rgba(139,92,246,0.95)":"rgba(139,92,246,0.25)",color:cellNoteCount>0?"var(--bg-secondary)":"rgba(139,92,246,0.95)",fontWeight:700,lineHeight:"14px",opacity:cellNoteCount>0?1:0,transition:"opacity 0.15s"}}>💬{cellNoteCount>0?" "+cellNoteCount:"+"}</span>
                </td>);})}
            </tr>);})}
            {displayRows.length<viewRows.length&&<tr ref={renderMoreRef}>
              <td colSpan={1+(showModuleCol?1:0)+(data.headers?.length||0)} style={{padding:"10px",textAlign:"center",fontSize:14,color:"var(--text-secondary)",borderBottom:GRID_LINE,background:"var(--bg-secondary)"}}>
                {displayRows.length} / {viewRows.length} 행 표시 — 스크롤하면 자동으로 더 표시됩니다
              </td>
            </tr>}
            {/* 마지막 줄의 TAG 추가 행. module 열이 붙어 있으면 그 칸까지 채워야
                아래 셀들이 한 칸씩 밀리지 않는다 (마지막 wafer 아래가 비던 원인). */}
            <tr>
              {showModuleCol&&<td style={{borderBottom:GRID_LINE,borderRight:GRID_LINE,background:"var(--bg-tertiary)",position:"sticky",left:0,zIndex:2}}/>}
              <td onClick={promptCreateCustomTag} title="TAG 열 추가"
                style={{padding:"7px 10px",fontWeight:800,fontSize:14,color:"rgba(37,99,235,0.95)",borderBottom:GRID_LINE,borderRight:GRID_LINE,background:"rgba(59,130,246,0.08)",position:"sticky",left:showModuleCol?MODULE_COL_W:0,zIndex:2,whiteSpace:"nowrap",cursor:"pointer"}}>
                + TAG
              </td>
              {data.headers?.map((h,ci)=>(
                <td key={`tag-add-${ci}`} onClick={promptCreateCustomTag} title={`${h} TAG 열 추가`}
                  style={{padding:"7px 8px",borderBottom:GRID_LINE,borderRight:GRID_LINE,textAlign:"center",fontSize:14,color:"rgba(37,99,235,0.95)",background:"rgba(59,130,246,0.04)",cursor:"pointer",fontWeight:800}}>
                  +
                </td>
              ))}
            </tr>
          </tbody>
        </table>
        )}
        {showLineageSummary && lineageSummary.length>0&&<div style={{margin:"12px 10px 18px",border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-card)",overflow:"hidden"}}>
          <div style={{padding:"10px 12px",fontSize:14,fontWeight:700,color:"var(--accent)",borderTop:"1px solid var(--border)",borderBottom:"1px solid var(--border)"}}>KNOB별 step_desc → step_id 요약</div>
          <div style={{maxHeight:320,overflow:"auto"}}>
            <table style={{borderCollapse:"separate",borderSpacing:0,width:"100%",fontSize:14}}>
              <thead>
                <tr>
                  <th style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",borderBottom:GRID_LINE,minWidth:220,color:GRID_TEXT}}>KNOB</th>
                  <th style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",borderBottom:GRID_LINE,minWidth:180}}>step_desc</th>
                  <th style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",borderBottom:GRID_LINE,minWidth:260}}>step_id</th>
                </tr>
              </thead>
              <tbody>
                {lineageSummary.map(x=>(
                  <tr key={x.key}>
                    <td style={{padding:"6px 10px",borderBottom:GRID_LINE,color:GRID_TEXT}} title={x.parameter}>{splitParamDisplayName(x.parameter)}</td>
                    <td style={{padding:"6px 10px",borderBottom:GRID_LINE,color:"var(--text-secondary)"}}>{x.step_desc||"—"}</td>
                    <td style={{padding:"6px 10px",borderBottom:GRID_LINE,color:"rgba(147,197,253,0.95)",fontWeight:700}}>{(x.step_ids||[]).length?x.step_ids.join(", "):"—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>}
        </div>;
      })()
      :tab==="history"?<div style={{flex:1,overflow:"auto",padding:16}}>
        <div style={{display:"flex",gap:12,marginBottom:12,alignItems:"center",flexWrap:"wrap"}}>
          {[
            {key:"lot_all",label:"현재 LOT 전체 Log",title:"현재 선택한 root lot의 전체 변경 이력",disabled:!lotId.trim()},
            {key:"lot_final",label:"현재 LOT 최종 Log",title:"현재 선택한 root lot에서 지금 적용 중인 최종 값만 표시",disabled:!lotId.trim()},
            {key:"all",label:"전체 History Log",title:"제품 전체 변경 이력"},
            {key:"all_final",label:"전체 최종 Log",title:"제품 전체에서 지금 적용 중인 최종 값만 표시"},
          ].map(opt=>(
            <label key={opt.key} title={opt.title} style={{display:"inline-flex",alignItems:"center",gap:6,fontSize:14,color:opt.disabled?"var(--text-muted)":"var(--text-primary)",cursor:opt.disabled?"not-allowed":"pointer",opacity:opt.disabled?0.55:1}}>
              <input type="radio" name="history-mode" checked={histMode===opt.key} disabled={!!opt.disabled} onChange={()=>!opt.disabled&&loadHistoryByMode(opt.key)} />
              <span style={histMode===opt.key?{color:"var(--accent)",fontWeight:700}:{color:"inherit"}}>{opt.label}</span>
            </label>
          ))}
          {isFinalHistoryMode(histMode)&&histFinal.drift_count>0&&<span style={{fontSize:14,padding:"2px 8px",borderRadius:10,background:"rgba(239,68,68,0.13)",color:"rgba(239,68,68,0.95)",fontWeight:600}}>⚠ drift {histFinal.drift_count}/{histFinal.total_cells}</span>}
          {isAdmin&&<button onClick={()=>dl(API+"/history-csv?"+historyQuery(histMode,isFinalHistoryMode(histMode)?{}:histFilter,HIST_PAGE).replace(/&limit=\d+/,""), `splittable_history_${selProd}.csv`).catch(e=>toast.error("이력 CSV 다운로드 실패: "+e.message))} title={histFilterActive&&!isFinalHistoryMode(histMode)?"현재 걸린 필터가 그대로 적용됩니다":"전체 이력을 CSV 로 내려받습니다"} style={{marginLeft:"auto",padding:"4px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:"pointer"}}>⬇ History CSV{histFilterActive&&!isFinalHistoryMode(histMode)?" (필터 적용)":""}</button>}
        </div>
        {isFinalHistoryMode(histMode)?(
          histFinal.final.length===0?<div style={{textAlign:"center",padding:40,color:"var(--text-secondary)"}}>No plan cells</div>
          :<table style={{width:"100%",borderCollapse:"separate",borderSpacing:0,fontSize:14}}>
            <thead><tr>{["Last","User","Root Lot","Wafer","Column","Final","사유","Changes","Drift"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 10px",borderBottom:"2px solid var(--border)",color:"var(--text-secondary)",fontSize:14}}>{h}</th>)}</tr></thead>
            <tbody>{histFinal.final.map((r,i)=>{
              const drift=Array.isArray(r.drift)?r.drift:[];
              const driftLabel=drift.includes("multi_change")&&drift.includes("multi_user")?"다수 변경·다수 사용자":drift.includes("multi_change")?"다수 변경":drift.includes("multi_user")?"다수 사용자":drift.includes("reinstated")?"삭제 후 재설정":"";
              return(<tr key={i} style={drift.length>0?{background:"#ef444408"}:{}}>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{(r.final_time||"").slice(0,16)}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>{r.final_user||"-"}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:14,color:"var(--accent)"}}>{r.root_lot_id}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:14}}>{r.wafer_id}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:14,maxWidth:180,overflow:"hidden",textOverflow:"ellipsis"}} title={r.column}>{r.column}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:r.final_action==="delete"?"rgba(239,68,68,0.95)":"rgba(34,197,94,0.95)",fontWeight:600}}>{r.final_action==="delete"?"(삭제)":(r.final_value??"-")}</td>
                {/* 지금 값의 사유. 마지막 변경에 사유가 없으면 이 셀에 마지막으로
                     남은 사유를 흐리게 보여준다(어느 시점 사유인지 title 로 구분). */}
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:14,maxWidth:260,whiteSpace:"pre-wrap",wordBreak:"break-word"}}
                    title={r.final_reason?r.final_reason:(r.last_reason?"최종 변경에는 사유가 없습니다. 이전 변경의 사유: "+r.last_reason:"")}>
                  {r.final_reason?r.final_reason
                    :r.last_reason?<span style={{color:"var(--text-secondary)",fontStyle:"italic"}}>이전 사유: {r.last_reason}</span>
                    :<span style={{color:"var(--text-secondary)"}}>-</span>}
                </td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}} title={"distinct values: "+JSON.stringify(r.distinct_values)}>set {r.set_count}{r.delete_count>0?` / del ${r.delete_count}`:""}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>{driftLabel?<span style={{fontSize:14,padding:"2px 6px",borderRadius:3,background:"rgba(239,68,68,0.13)",color:"rgba(239,68,68,0.95)"}} title={drift.join(", ")}>⚠ {driftLabel}</span>:<span style={{fontSize:14,color:"var(--text-secondary)"}}>-</span>}</td>
              </tr>);})}</tbody>
          </table>
        ):(<>
          {/* 필터 바 — 서버가 전체 아카이브 위에서 걸러 주므로 오래된 변경도 찾아진다. */}
          <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginBottom:10,padding:"8px 10px",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)"}}>
            <select value={histFilter.user} onChange={e=>applyHistFilter({user:e.target.value})} style={{...HIST_INPUT,minWidth:120}} title="변경한 사용자">
              <option value="">사용자 전체</option>
              {(histFacets.users||[]).map(u=><option key={u} value={u}>{u}</option>)}
            </select>
            <select value={histFilter.action} onChange={e=>applyHistFilter({action:e.target.value})} style={{...HIST_INPUT,minWidth:100}} title="변경 종류">
              <option value="">동작 전체</option>
              {(histFacets.actions||[]).map(a=><option key={a} value={a}>{a}</option>)}
            </select>
            <input value={histFilter.column} onChange={e=>applyHistFilter({column:e.target.value},true)} placeholder="컬럼 (부분일치)" style={{...HIST_INPUT,width:150}} list="hist-column-list" />
            <datalist id="hist-column-list">{(histFacets.columns||[]).map(c=><option key={c} value={c} />)}</datalist>
            <input value={histFilter.wafer_id} onChange={e=>applyHistFilter({wafer_id:e.target.value},true)} placeholder="wafer" style={{...HIST_INPUT,width:90}} />
            <input type="date" value={histFilter.since} onChange={e=>applyHistFilter({since:e.target.value})} style={HIST_INPUT} title="이 날짜부터" />
            <span style={{color:"var(--text-secondary)"}}>~</span>
            <input type="date" value={histFilter.until} onChange={e=>applyHistFilter({until:e.target.value})} style={HIST_INPUT} title="이 날짜까지" />
            <input value={histFilter.q} onChange={e=>applyHistFilter({q:e.target.value},true)} placeholder="값·셀 전체 검색" style={{...HIST_INPUT,width:170}} />
            <label style={{display:"inline-flex",alignItems:"center",gap:5,fontSize:14,cursor:"pointer",color:"var(--text-secondary)"}} title="변경 사유가 적힌 이력만 봅니다 (사유는 선택 입력이라 없는 건도 정상입니다)">
              <input type="checkbox" checked={!!histFilter.has_reason} onChange={e=>applyHistFilter({has_reason:e.target.checked})} />
              사유 있는 것만
            </label>
            {histFilterActive&&<button onClick={resetHistFilter} style={{...HIST_INPUT,cursor:"pointer",color:"var(--text-secondary)"}}>필터 해제</button>}
            <label style={{display:"inline-flex",alignItems:"center",gap:5,fontSize:14,cursor:"pointer",color:"var(--text-secondary)"}} title="한 번의 저장으로 바뀐 셀들을 하나로 묶어 봅니다">
              <input type="checkbox" checked={histGroup} onChange={e=>{setHistGroup(e.target.checked);setHistOpenBatch({});}} />
              저장 단위로 묶기
            </label>
            <span style={{marginLeft:"auto",fontSize:14,color:"var(--text-secondary)"}}>
              {histLoading?"불러오는 중…":`${history.length} / ${histMeta.total}건 표시${histFilterActive?` (전체 ${histMeta.scope_total}건 중 필터)`:""}`}
            </span>
          </div>
          {history.length===0?<div style={{textAlign:"center",padding:40,color:"var(--text-secondary)"}}>{histFilterActive?"조건에 맞는 변경 이력이 없습니다":"No history"}</div>
          :<table style={{width:"100%",borderCollapse:"separate",borderSpacing:0,fontSize:14}}>
            <thead><tr>{["Time","User","Root Lot","Wafer","Column","Action","Old → New","사유"].map(h=><th key={h} style={HIST_TH}>{h}</th>)}</tr></thead>
            <tbody>{historyGroups.map((g,gi)=>{
              if(g.rows.length===1)return renderHistRow(g.rows[0],g.gid+"-"+gi,false);
              const first=g.rows[0];
              const open=!!histOpenBatch[g.gid];
              const users=[...new Set(g.rows.map(r=>r.user).filter(Boolean))];
              const roots=[...new Set(g.rows.map(r=>histRowParts(r).root).filter(Boolean))];
              const cols=[...new Set(g.rows.map(r=>histRowParts(r).column).filter(Boolean))];
              const wafers=[...new Set(g.rows.map(r=>histRowParts(r).wafer).filter(Boolean))];
              const dels=g.rows.filter(r=>r.action==="delete").length;
              // 사유는 저장 단위로 붙으므로 묶음 헤더에 그대로 보여준다.
              const groupReason=(g.rows.find(r=>String(r?.reason||"").trim())||{}).reason||"";
              return(<Fragment key={g.gid+"-"+gi}>
                <tr onClick={()=>setHistOpenBatch(m=>({...m,[g.gid]:!open}))} style={{cursor:"pointer",background:"var(--accent-glow)"}} title="클릭하면 이 저장에 포함된 셀을 모두 펼칩니다">
                  <td style={{...HIST_TD,color:"var(--text-secondary)",whiteSpace:"nowrap"}} title={first?.time||""}>{open?"▾":"▸"} {histTime(first?.time)}</td>
                  <td style={{...HIST_TD,fontWeight:600}}>{users.join(", ")||"-"}</td>
                  <td style={{...HIST_MONO,color:"var(--accent)"}} title={roots.join(", ")}>{roots[0]||"-"}{roots.length>1?` 외 ${roots.length-1}`:""}</td>
                  <td style={HIST_MONO} title={wafers.join(", ")}>{wafers.length}종</td>
                  <td style={{...HIST_MONO,maxWidth:220,overflow:"hidden",textOverflow:"ellipsis"}} title={cols.join(", ")}>{cols[0]||"-"}{cols.length>1?` 외 ${cols.length-1}`:""}</td>
                  <td style={HIST_TD}><span style={{fontSize:14,padding:"1px 6px",borderRadius:3,...histActionStyle(dels===g.rows.length?"delete":"set")}}>{dels===g.rows.length?"delete":dels>0?"set+delete":"set"}</span></td>
                  <td style={{...HIST_TD,color:"var(--text-secondary)"}}>{g.rows.length}개 셀 일괄 변경{dels>0&&dels<g.rows.length?` (삭제 ${dels})`:""}</td>
                  <td style={{...HIST_TD,maxWidth:280,whiteSpace:"pre-wrap",wordBreak:"break-word"}} title={groupReason}>
                    {groupReason||<span style={{color:"var(--text-secondary)"}}>-</span>}
                  </td>
                </tr>
                {open&&g.rows.map((h,ri)=>renderHistRow(h,g.gid+"-"+gi+"-"+ri,true))}
              </Fragment>);
            })}</tbody></table>}
          {histMeta.has_more&&<div style={{textAlign:"center",padding:"12px 0"}}>
            <button onClick={loadMoreHistory} disabled={histLoading} style={{padding:"6px 16px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:histLoading?"wait":"pointer"}}>
              {histLoading?"불러오는 중…":`더 보기 (남은 ${Math.max(0,histMeta.total-history.length)}건)`}
            </button>
          </div>}
        </>)}
      </div>:null}
    </div>
    <SplitTableCellEditor
      activeCell={activeCell}
      suggestions={activeCell?suggestionValuesFor(activeCell.param,colValCache[activeCell.param]||[]):[]}
      suggestionsLoading={!!activeCell&&colValCache[activeCell.param]===undefined}
      onValueChange={(value)=>setActiveCell(c=>({...c,value}))}
      onCommit={(value)=>{
        if(activeCell?.kind==="tag"){
          setPendingTags(p=>({...p,[activeCell.key]:value}));
        }else if(activeCell?.kind==="management"){
          setPendingManagement(p=>({...p,[activeCell.key]:value}));
        }else if(value){
          setPendingPlans(p=>({...p,[activeCell.key]:value}));
        }
        setActiveCell(null);
      }}
      onClose={()=>setActiveCell(null)}
    />
    {showConfirm&&<Modal open onClose={()=>setShowConfirm(false)} width={460} zIndex={9999}>
        <div style={{fontSize:16,fontWeight:700,marginBottom:12}}>Confirm Changes</div>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:12}}>{pendingEditCount} cells will be updated</div>
        {/* 변경 사유 — 선택 입력. 비워도 저장되고, 쓰면 이 저장(batch)에 속한 모든
             셀의 이력에 붙어 History 탭에서 그대로 보인다. 셀 메모(💬)와는 별개다.
             바뀔 셀 목록 **위**에 둔다 — 아래에 두면 편집 셀이 많을 때 목록에 밀려
             스크롤해야만 보였다. */}
        <div style={{marginBottom:14}}>
          <label htmlFor="plan-reason" style={{display:"block",fontSize:14,fontWeight:600,marginBottom:5}}>
            변경 사유 <span style={{fontWeight:400,color:"var(--text-secondary)"}}>(선택 — 이 저장 {pendingEditCount}건에 함께 기록)</span>
          </label>
          <textarea id="plan-reason" value={planReason} onChange={e=>setPlanReason(e.target.value.slice(0,500))} rows={2}
            placeholder="예: PC Split 적용 — 안 써도 상관없습니다. 기록용"
            style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:14,fontFamily:"inherit"}} />
          <div style={{textAlign:"right",fontSize:14,color:"var(--text-secondary)"}}>{planReason.length}/500</div>
        </div>
        {/* 목록은 자체 스크롤 — 셀이 수백 건이어도 모달이 무한정 길어지지 않는다. */}
        <div style={{maxHeight:240,overflowY:"auto",border:"1px solid var(--border)",borderRadius:4,padding:"0 10px"}}>
        {Object.entries(pendingPlans).map(([k,v])=>(<div key={k} style={{fontSize:14,padding:"4px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between"}}><span style={{fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:250,overflow:"hidden",textOverflow:"ellipsis"}}>{k.split("|").pop()}</span><span style={{color:"rgba(249,115,22,0.95)",fontWeight:600}}>{v}</span></div>))}
        {Object.entries(pendingTags).map(([k,v])=>(<div key={k} style={{fontSize:14,padding:"4px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between"}}><span style={{fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:250,overflow:"hidden",textOverflow:"ellipsis"}}>{k.split("|").pop()}</span><span style={{color:"rgba(37,99,235,0.95)",fontWeight:600}}>{v||"(clear)"}</span></div>))}
        {Object.entries(pendingManagement).map(([k,v])=>(<div key={k} style={{fontSize:14,padding:"4px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between"}}><span style={{fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:250,overflow:"hidden",textOverflow:"ellipsis"}}>{customLabelFor(k.split("|").pop())}</span><span style={{color:"rgba(5,150,105,0.95)",fontWeight:600}}>{v||"(clear)"}</span></div>))}
        </div>
        <div style={{display:"flex",gap:8,marginTop:16}}>
          <button onClick={savePlans} style={{flex:1,padding:10,borderRadius:6,border:"none",background:"rgba(34,197,94,0.95)",color:"var(--bg-secondary)",fontWeight:600,cursor:"pointer"}}>Confirm</button>
          <button onClick={()=>setShowConfirm(false)} style={{padding:"10px 20px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>Cancel</button>
        </div></Modal>}

    {/* v8.8.13: Notes 드로어 — 3종 scope(wafer/param/lot) 통합 뷰.
         - global(param_global) UI 제거: 필요성 낮고 뷰를 단순하게 유지.
         - 한 줄 컴팩트 렌더 + wafer/param 검색 필터.
         - 삭제는 작성자 본인만(+admin). 타인은 아래에 답글로 태그 추가. */}
    {notesOpen && (()=>{
      // param_global 은 목록에서 완전 제외 (전역 태그 제거 요구).
      const base=notes.filter(n=>n.scope!=="param_global");
      let filtered=(!noteFilter)?base
        :noteFilter.scope==="wafer"?base.filter(n=>n.scope==="wafer"&&n.key===noteFilter.key)
        :noteFilter.scope==="cell"?base.filter(n=>n.scope==="param"&&n.key===`${selProd}__${lotId}__W${noteFilter.wafer_id}__${noteFilter.param}`)
        :noteFilter.scope==="lot"?base.filter(n=>n.scope==="lot"&&n.key===`${selProd}__LOT__${lotId}`)
        :noteFilter.scope==="param"&&noteFilter.param?base.filter(n=>n.scope==="param"&&n.key.endsWith(`__${noteFilter.param}`))
        :noteFilter.scope==="any_wafer"?base.filter(n=>n.scope==="wafer")
        :noteFilter.scope==="any_param"?base.filter(n=>n.scope==="param")
        :noteFilter.scope==="any_lot"?base.filter(n=>n.scope==="lot")
        :base;
      // wafer/param 검색: key 내 wafer id / param 이름 / 본문 부분일치.
      const q=(noteSearch||"").trim().toLowerCase();
      if(q){filtered=filtered.filter(n=>{
        const parts=(n.key||"").split("__");
        const wid=(parts[2]||"").replace(/^W/,"");
        const param=parts[3]||"";
        return (n.text||"").toLowerCase().includes(q)
          || wid.toLowerCase().includes(q)
          || param.toLowerCase().includes(q);
      });}
      const title=!noteFilter?"노트 (전체)"
        :noteFilter.scope==="wafer"?`wafer #${noteFilter.key.split("__W").pop()} 태그`
        :noteFilter.scope==="cell"?`W${noteFilter.wafer_id} × ${noteFilter.param} 메모`
        :noteFilter.scope==="lot"?`LOT ${lotId} 노트`
        :noteFilter.scope==="param"?`${noteFilter.param} 메모 (lot ${lotId})`
        :noteFilter.scope==="any_wafer"?"모든 wafer 태그"
        :noteFilter.scope==="any_param"?"모든 param 메모"
        :noteFilter.scope==="any_lot"?"모든 lot 노트"
        :"노트";
      const me=user?.username||"";
      const closeNotes=()=>{setNotesOpen(false);setNoteDraft("");setNoteFilter(null);setNoteDraftScope(null);setNoteSearch("");setExpandedNoteId("");};
      return(<Modal open onClose={closeNotes} width={520} zIndex={2000}>
        <div style={{display:"flex",flexDirection:"column",maxHeight:"82vh"}}>
        <div style={{padding:"12px 16px",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <div style={{fontSize:14,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>📝 {title}</div>
          <span onClick={closeNotes} style={{cursor:"pointer",fontSize:18,color:"var(--text-secondary)"}}>✕</span>
        </div>
        {/* scope 필터 칩 — 전체 / wafer / param / lot (global 제거) */}
        <div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)",display:"flex",gap:4,flexWrap:"wrap",fontSize:14,color:"var(--text-secondary)"}}>
          {[
            {k:"all",l:`전체 ${base.length}`},
            {k:"wafer",l:`🏷 wafer ${base.filter(n=>n.scope==="wafer").length}`},
            {k:"param",l:`💬 param ${base.filter(n=>n.scope==="param").length}`},
            {k:"lot",l:`📦 lot ${base.filter(n=>n.scope==="lot").length}`},
          ].map(b=>{const active=(b.k==="all"&&!noteFilter)
              ||(b.k==="wafer"&&noteFilter&&(noteFilter.scope==="wafer"||noteFilter.scope==="any_wafer"))
              ||(b.k==="param"&&noteFilter&&(noteFilter.scope==="param"||noteFilter.scope==="any_param"||noteFilter.scope==="cell"))
              ||(b.k==="lot"&&noteFilter&&(noteFilter.scope==="lot"||noteFilter.scope==="any_lot"));
            return <span key={b.k} onClick={()=>{
              if(b.k==="all"){setNoteFilter(null);setNoteDraftScope(null);return;}
              if(b.k==="wafer"){setNoteFilter({scope:"any_wafer"});setNoteDraftScope(null);return;}
              if(b.k==="param"){setNoteFilter({scope:"any_param"});setNoteDraftScope(null);return;}
              if(b.k==="lot"){setNoteFilter({scope:"any_lot"});setNoteDraftScope(lotId?{scope:"lot",product:selProd,root_lot_id:lotId}:null);return;}
            }} style={{padding:"2px 8px",borderRadius:10,cursor:"pointer",background:active?"var(--accent)":"var(--bg-card)",color:active?"var(--bg-secondary)":"var(--text-secondary)",fontWeight:active?700:500,border:"1px solid "+(active?"var(--accent)":"var(--border)")}}>{b.l}</span>;
          })}
        </div>
        {/* 검색 박스 — wafer id / param 이름 / 본문 부분일치 */}
        <div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)"}}>
          <input value={noteSearch} onChange={e=>setNoteSearch(e.target.value)}
            placeholder="🔍 wafer id · param 이름 · 본문 검색"
            style={{width:"100%",padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,boxSizing:"border-box"}}/>
        </div>
        <div style={{flex:1,overflow:"auto",padding:"8px 14px",display:"flex",flexDirection:"column",gap:4}}>
          {filtered.length===0&&<div style={{padding:24,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>기록된 노트 없음</div>}
          {/* 최신순 정렬 */}
          {[...filtered].sort((a,b)=>(b.created_at||"").localeCompare(a.created_at||"")).map(n=>{
            const parts=(n.key||"").split("__");
            const wid=(parts[2]||"").replace(/^W/,"");
            const param=n.scope==="param"?parts[3]||"":"";
            const lotOf=n.scope==="lot"?(parts[2]||""):"";
            const mine=(n.username||"")===me;
            const badge=n.scope==="wafer"?{bg:"rgba(59,130,246,0.95)",txt:`🏷 W${wid}`}
              :n.scope==="param"?{bg:"rgba(139,92,246,0.95)",txt:`💬 W${wid}·${param}`}
              :n.scope==="lot"?{bg:"rgba(22,163,74,0.95)",txt:`📦 ${lotOf}`}
              :{bg:"rgba(107,114,128,0.95)",txt:n.scope};
            const time=(n.created_at||"").replace("T"," ").slice(5,16);
            const expanded=expandedNoteId===n.id;
            const imgs=Array.isArray(n.images)?n.images:[];
            const comments=Array.isArray(n.comments)?n.comments:[];
            return(<div key={n.id} title={expanded?"클릭해서 접기":"클릭해서 전체 내용 보기"} onClick={()=>setExpandedNoteId(expanded?"":n.id)} style={{display:"grid",gridTemplateColumns:"minmax(0,1fr)",gap:expanded?6:0,padding:"4px 6px",borderRadius:4,background:expanded?"var(--bg-secondary)":"var(--bg-card)",border:"1px solid var(--border)",fontSize:14,minHeight:26,cursor:"pointer"}}>
              <div style={{display:"flex",alignItems:"center",gap:6,minWidth:0}}>
                <span style={{flexShrink:0,fontSize:14,fontWeight:700,padding:"1px 6px",borderRadius:8,background:badge.bg,color:"var(--bg-secondary)",whiteSpace:"nowrap"}}>{badge.txt}</span>
                <span style={{flex:1,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",color:"var(--text-primary)"}}>{n.text||"(이미지)"}</span>
                {imgs.length>0&&<span style={{flexShrink:0,fontSize:14,padding:"1px 6px",borderRadius:8,background:"rgba(59,130,246,0.15)",color:"rgba(59,130,246,0.95)",fontWeight:700}}>이미지 {imgs.length}</span>}
                {comments.length>0&&<span style={{flexShrink:0,fontSize:14,padding:"1px 6px",borderRadius:8,background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontWeight:700}}>답글 {comments.length}</span>}
                <span style={{flexShrink:0,fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{n.username}</span>
                <span style={{flexShrink:0,fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{time}</span>
                {mine&&<span onClick={e=>{e.stopPropagation();deleteNote(n.id);}} title="작성자만 삭제 가능" style={{flexShrink:0,cursor:"pointer",fontSize:14,color:"rgba(239,68,68,0.95)",padding:"0 4px"}}>×</span>}
              </div>
              {expanded&&<div style={{display:"grid",gap:8,padding:"4px 6px 6px 6px",borderTop:"1px dashed var(--border)"}}>
                {n.text&&<div style={{whiteSpace:"pre-wrap",wordBreak:"break-word",lineHeight:1.45,color:"var(--text-primary)"}}>{n.text}</div>}
                {imgs.length>0&&<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(96px,1fr))",gap:6}}>
                  {imgs.map((im,ii)=><a key={ii} href={authSrc(im.url)} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()} title={im.filename||"image"} style={{border:"1px solid var(--border)",borderRadius:4,overflow:"hidden",background:"var(--bg-primary)",height:86,display:"flex",alignItems:"center",justifyContent:"center"}}>
                    <img src={authSrc(im.url)} alt={im.filename||"note image"} style={{maxWidth:"100%",maxHeight:"100%",objectFit:"contain",display:"block"}}/>
                  </a>)}
                </div>}
                {comments.length>0&&<div style={{display:"grid",gap:5}}>
                  {comments.map(c=><div key={c.id||c.created_at} style={{padding:"5px 7px",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)"}}>
                    <div style={{display:"flex",gap:6,color:"var(--text-secondary)",fontSize:12,marginBottom:3}}><span>{c.username||"-"}</span><span>{(c.created_at||"").replace("T"," ").slice(5,16)}</span></div>
                    {c.text&&<div style={{whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{c.text}</div>}
                    {Array.isArray(c.images)&&c.images.length>0&&<div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:5}}>{c.images.map((im,ii)=><a key={ii} href={authSrc(im.url)} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()}><img src={authSrc(im.url)} alt={im.filename||"comment image"} style={{width:64,height:48,objectFit:"cover",border:"1px solid var(--border)",borderRadius:4}}/></a>)}</div>}
                  </div>)}
                </div>}
              </div>}
            </div>);
          })}
        </div>
        {/* draft 패널 — scope 별 입력 */}
        {noteDraftScope&&<div style={{padding:"10px 16px",borderTop:"1px solid var(--border)",display:"flex",flexDirection:"column",gap:6}}>
          <div style={{fontSize:14,color:"var(--text-secondary)",display:"flex",alignItems:"center",flexWrap:"wrap",gap:6}}>
            {(() => {
              const sc = noteDraftScope.scope;
              const color = sc==="wafer"?"rgba(59,130,246,0.95)":sc==="param"?"rgba(139,92,246,0.95)":sc==="lot"?"rgba(22,163,74,0.95)":"rgba(107,114,128,0.95)";
              const label = sc==="wafer"?`🏷 W${noteDraftScope.wafer_id}`
                :sc==="param"?`💬 W${noteDraftScope.wafer_id||"?"}·${noteDraftScope.param}`
                :sc==="lot"?`📦 LOT ${noteDraftScope.root_lot_id}`:sc;
              return <>대상: <span style={{color,fontWeight:700}}>{label}</span></>;
            })()}
            {noteDraftScope.scope==="param"&&<span>wafer:
              <input value={noteDraftScope.wafer_id||""} onChange={e=>setNoteDraftScope({...noteDraftScope,wafer_id:e.target.value})} placeholder="wafer_id" style={{marginLeft:4,width:70,padding:"2px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
            </span>}
            <span style={{marginLeft:"auto"}}><span onClick={clearNoteDraft} style={{cursor:"pointer",color:"var(--text-secondary)",fontSize:14}}>✕ 취소</span></span>
          </div>
          <textarea value={noteDraft} onChange={e=>setNoteDraft(e.target.value)} onPaste={handleNotePaste} placeholder="새 노트 내용…"
            rows={2}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,resize:"vertical",fontFamily:"inherit"}}/>
          <div style={{display:"flex",gap:6,alignItems:"center",flexWrap:"wrap"}}>
            <label style={{padding:"4px 9px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:noteUploading?"wait":"pointer"}}>
              이미지
              <input type="file" accept="image/*" multiple disabled={noteUploading} onChange={e=>{uploadNoteFiles(e.target.files);e.target.value="";}} style={{display:"none"}}/>
            </label>
            {noteUploading&&<span style={{fontSize:14,color:"var(--accent)"}}>업로드 중...</span>}
            {noteImages.map((im,i)=><span key={im.url||i} style={{display:"inline-flex",alignItems:"center",gap:4,padding:"2px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-card)",fontSize:14}}>
              <img src={authSrc(im.url)} alt="" style={{width:24,height:18,objectFit:"cover",borderRadius:3}}/>
              <span style={{maxWidth:110,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{im.filename||"image"}</span>
              <span onClick={()=>setNoteImages(prev=>prev.filter((_,idx)=>idx!==i))} style={{cursor:"pointer",color:"var(--text-secondary)"}}>×</span>
            </span>)}
            <span style={{marginLeft:"auto",display:"inline-flex",gap:6}}>
            {(() => {
              const sc = noteDraftScope.scope;
              const need = sc==="param" ? !!(noteDraftScope.wafer_id||"").trim() : true;
              const canSave = (!!noteDraft.trim() || noteImages.length>0) && need && !noteUploading;
              return <button onClick={addNote} disabled={!canSave}
                style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:14,fontWeight:600,cursor:canSave?"pointer":"not-allowed",opacity:canSave?1:0.5}}>저장 ({me||"anonymous"})</button>;
            })()}
            </span>
          </div>
        </div>}
        </div>
      </Modal>);
    })()}

    {/* v8.8.10: Rulebook 컬럼 매핑 편집 modal — 역할 → 실제 CSV 컬럼명 조정. soft-landing. */}
    {rbEditKind && (
      <Modal open onClose={()=>setRbEditKind(null)} width={500} zIndex={3000}>
          <div style={{display:"flex",alignItems:"center",marginBottom:10}}>
            <div style={{fontSize:14,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>🔧 컬럼 매핑 — {rbEditKind}</div>
            <span style={{flex:1}}/>
            <span onClick={()=>setRbEditKind(null)} style={{cursor:"pointer",fontSize:16}}>✕</span>
          </div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.5}}>
            역할 → 실제 CSV 컬럼명. 사내 CSV 의 헤더가 다르면 여기만 조정해도 연결 유지됨.
            입력 안 한 값은 기본값으로 저장.
          </div>
          <div style={{display:"flex",flexDirection:"column",gap:6}}>
            {Object.entries(rbSchema.defaults?.[rbEditKind] || {}).filter(([role])=>role!=="file_name").map(([role, dfl]) => (
              <label key={role} style={{display:"flex",alignItems:"center",gap:8,fontSize:14}}>
                <span style={{width:140,color:"var(--text-secondary)",fontFamily:"monospace"}}>{role}</span>
                <input value={rbDraftMap[role] ?? dfl}
                  onChange={e=>setRbDraftMap(m=>({...m,[role]:e.target.value}))}
                  style={{flex:1,padding:"5px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}} />
                <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",opacity:0.7,width:120,textAlign:"right"}}>기본: {dfl}</span>
              </label>
            ))}
          </div>
          <div style={{display:"flex",justifyContent:"flex-end",gap:6,marginTop:14}}>
            <button onClick={()=>{
              // 기본값으로 리셋
              const defaults=Object.fromEntries(Object.entries(rbSchema.defaults?.[rbEditKind]||{}).filter(([role])=>role!=="file_name"));
              setRbDraftMap(m=>({...m,...defaults}));
            }} style={{padding:"6px 12px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>기본값 복원</button>
            <button onClick={()=>setRbEditKind(null)}
              style={{padding:"6px 12px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>취소</button>
            <button onClick={saveSchemaEdit}
              style={{padding:"6px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:14,fontWeight:600,cursor:"pointer"}}>저장</button>
          </div>
      </Modal>
    )}
    {/* v9.0.5: Rulebook 규칙 미리보기 modal — 인덱스(KNOB/INLINE/VM) 클릭 시 연결 규칙 표시. */}
    {rbMatchKind && (
      <Modal open onClose={closeRuleMatchView} width={860} zIndex={3001}>
        <div style={{display:"flex",flexDirection:"column",maxHeight:"82vh"}}>
          <div style={{display:"flex",alignItems:"center",marginBottom:10,gap:8}}>
            <div style={{fontSize:14,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>🔎 {rbMatchKind==="knob_ppid"?"KNOB 분류 규칙":`${rbMatchTitle} 매칭 규칙`}</div>
            <span style={{fontSize:14,padding:"2px 8px",borderRadius:10,background:"var(--bg-card)",color:"var(--text-secondary)",fontFamily:"monospace"}}>{rbMatchParam}</span>
            <span style={{flex:1}}/>
            <span onClick={closeRuleMatchView} style={{cursor:"pointer",fontSize:16}}>✕</span>
          </div>
          {rbMatchData ? (
            <div style={{overflow:"auto"}}>
              {rbMatchKind === "knob_ppid" && (()=>{const groups=Array.isArray(rbMatchData.groups)?rbMatchData.groups:[];const sets=knobRuleSets(groups);const composite=sets.length>1;const processText=knobStepSummaryText(groups,{excludeNotNull:excludeNotNullStepMeta});return(
                <div style={{display:"grid",gap:8}}>
                  <div style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-secondary)",fontSize:12,color:"var(--text-secondary)",fontFamily:"monospace",lineHeight:1.4}}>
                    적용공정: {processText?<span style={{whiteSpace:"pre-line"}}>{processText}</span>:(groups.length?"표시 대상 없음":"매칭정보 없음")}
                  </div>
                  {groups.length===0 && <div style={{padding:10,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-secondary)",fontSize:14}}>분류 규칙이 없습니다.</div>}
                  {sets.map((set, si) => {const conditionMatches=set.conditions.map(g=>matchKnobRuleToRowValues(g,rbMatchRow,pendingValueFor));const checked=conditionMatches.length>0&&conditionMatches.every(ms=>ms.length>0);const allMatches=conditionMatches.flat();return(
                    <div key={`${rbMatchParam}-${set.rule_order}-${si}`} style={{padding:"10px 12px",borderRadius:6,border:checked?"1px solid rgba(34,197,94,0.75)":"1px solid rgba(251,191,36,0.35)",background:checked?"rgba(34,197,94,0.06)":"var(--bg-card)"}}>
                      <div style={{display:"flex",alignItems:"flex-start",gap:8}}>
                        <span title={checked?"현재 row 값이 이 rule_order 조건 묶음과 일치":"현재 row 값과 일치 없음"} style={{width:20,height:20,lineHeight:"18px",textAlign:"center",borderRadius:4,border:checked?"1px solid rgba(34,197,94,0.8)":"1px solid var(--border)",background:checked?"rgba(34,197,94,0.16)":"transparent",color:checked?"rgba(22,163,74,0.95)":"var(--text-secondary)",fontWeight:900,fontSize:14,flexShrink:0}}>{checked?"✓":""}</span>
                        <span style={knobRuleBadgeStyle(composite)}>{set.rule_order}</span>
                        <div style={{display:"flex",flexWrap:"wrap",gap:6,alignItems:"center",fontFamily:"monospace",fontSize:14}}>
                          {set.conditions.map((g,gi)=><span key={`${set.rule_order}-${gi}`} style={{display:"inline-flex",alignItems:"center",gap:4,flexWrap:"wrap"}}>
                            {gi>0&&<span style={{color:"var(--text-secondary)",fontWeight:900}}>&amp;</span>}
                            <span style={{padding:"1px 6px",borderRadius:3,background:"rgba(148,163,184,0.10)",border:"1px solid rgba(148,163,184,0.25)",color:"var(--text-secondary)"}}>{g.step_desc||g.func_step||"-"}</span>
                            <span style={{padding:"1px 6px",borderRadius:3,background:"rgba(96,165,250,0.12)",border:"1px solid rgba(96,165,250,0.35)",fontWeight:700}}>{g.operator || "-"}</span>
                            <span style={{padding:"1px 6px",borderRadius:3,background:"rgba(34,197,94,0.12)",border:"1px solid rgba(34,197,94,0.35)",fontWeight:700}}>{g.value || "-"}</span>
                            <span style={{padding:"1px 6px",borderRadius:3,background:"rgba(251,191,36,0.12)",border:"1px solid rgba(251,191,36,0.35)",fontWeight:800,color:"rgba(180,83,9,0.95)"}}>{g.category || "-"}</span>
                          </span>)}
                        </div>
                      </div>
                      {allMatches.length>0&&<div style={{marginTop:8,marginLeft:54,fontSize:13,fontFamily:"monospace",color:"rgba(22,163,74,0.95)",fontWeight:700}}>
                        {matchedWaferSummary(allMatches)}
                      </div>}
                    </div>
                  );})}
                </div>
              );})()}
              {rbMatchKind === "inline_matching" && (()=>{const im=rbMatchData;const groups=Array.isArray(im.groups)?im.groups:[];return(
                <div style={{display:"grid",gap:8}}>
                  <div style={{padding:"7px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",fontSize:14}}>
                    item_id: {String(im.item_id || rbMatchParam || "").replace(/^INLINE_/i,"") || "-"}{String(im.item_desc || "").trim() ? ` · item_desc: ${im.item_desc}` : ""}
                  </div>
                  {groups.length===0 ? (
                    <div style={{padding:10,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-secondary)",fontSize:14}}>매칭 규칙이 없습니다.</div>
                  ) : groups.map((g, gi) => (
                    <div key={`${rbMatchParam}-${gi}`} style={{padding:"8px 10px",borderRadius:6,border:"1px solid rgba(16,185,129,0.35)",background:"var(--bg-card)"}}>
                      <div style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap",marginBottom:4}}>
                        {g.function_step && <span style={{color:"rgba(16,185,129,0.95)",fontWeight:700,fontFamily:"monospace"}}>{g.function_step}</span>}
                        <span style={{color:"var(--text-secondary)",fontSize:14}}>{String(im.feature || rbMatchParam)}</span>
                      </div>
                      {(() => {const sids=Array.isArray(g.step_ids)&&g.step_ids.length?g.step_ids:(g.step_id?[g.step_id]:[]);return sids.length?(
                        <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                          {sids.map((sid)=> <span key={sid} style={{padding:"0 6px",borderRadius:3,background:"rgba(96,165,250,0.15)",color:"#60a5fa",border:"1px solid rgba(96,165,250,0.5)",fontWeight:700,fontSize:14}}>{sid}</span>)}
                        </div>
                      ) : <span style={{color:"var(--text-secondary)",fontSize:14}}>step_id 없음</span>;})()}
                    </div>
                  ))}
                </div>
              );})()}
              {rbMatchKind === "vm_matching" && (()=>{const vm=rbMatchData;const groups=Array.isArray(vm.groups)?vm.groups:[];return(
                <div style={{display:"grid",gap:8}}>
                  <div style={{padding:"7px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",fontSize:14}}>
                    {String(vm.step_desc || "").trim() ? `step_desc: ${vm.step_desc}` : "step_desc 없음"}{String(vm.item_id || "").trim() ? ` · item_id: ${vm.item_id}` : ""}
                  </div>
                  {groups.length===0 ? (
                    <div style={{padding:10,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-secondary)",fontSize:14}}>매칭 규칙이 없습니다.</div>
                  ) : groups.map((g, gi) => (
                    <div key={`${rbMatchParam}-${gi}`} style={{padding:"8px 10px",borderRadius:6,border:"1px solid rgba(139,92,246,0.35)",background:"var(--bg-card)"}}>
                      <div style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap",marginBottom:4}}>
                        {g.function_step && <span style={{color:"rgba(196,181,253,0.95)",fontWeight:700,fontFamily:"monospace"}}>{g.function_step}</span>}
                        <span style={{color:"var(--text-secondary)",fontSize:14}}>{String(vm.feature || rbMatchParam)}</span>
                      </div>
                      {(() => {const sids=Array.isArray(g.step_ids)&&g.step_ids.length?g.step_ids:(g.step_id?[g.step_id]:[]);return sids.length?(
                        <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                          {sids.map((sid)=> <span key={sid} style={{padding:"0 6px",borderRadius:3,background:"rgba(96,165,250,0.15)",color:"#60a5fa",border:"1px solid rgba(96,165,250,0.5)",fontWeight:700,fontSize:14}}>{sid}</span>)}
                        </div>
                      ) : <span style={{color:"var(--text-secondary)",fontSize:14}}>step_id 없음</span>;})()}
                    </div>
                  ))}
                </div>
              );})()}
            </div>
          ) : (
            <div style={{padding:10,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-secondary)",fontSize:14}}>매칭 데이터가 없습니다.</div>
          )}
        </div>
      </Modal>
    )}
  </div>);
}
