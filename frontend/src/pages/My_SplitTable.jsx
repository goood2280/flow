import { useState, useEffect, useRef } from "react";
import Loading from "../components/Loading";
import { sf, dl } from "../lib/api";
const API="/api/splittable";
// Excel-like pastel colors (bg + dark text)
const CELL_COLORS=[
  {bg:"#C6EFCE",fg:"#006100"},  // green
  {bg:"#FFEB9C",fg:"#9C5700"},  // yellow
  {bg:"#FBE5D6",fg:"#BF4E00"},  // orange
  {bg:"#BDD7EE",fg:"#1F4E79"},  // blue
  {bg:"#E2BFEE",fg:"#7030A0"},  // purple
  {bg:"#B4DED4",fg:"#0B5345"},  // teal
  {bg:"#F4CCCC",fg:"#75194C"},  // pink
];
const COLOR_PREFIXES=["KNOB","MASK"];

export default function My_SplitTable({user}){
  const[products,setProducts]=useState([]);const[selProd,setSelProd]=useState("");
  const[lotId,setLotId]=useState("");const[waferIds,setWaferIds]=useState("");
  const[lotSuggestions,setLotSuggestions]=useState([]);const[showLotDrop,setShowLotDrop]=useState(false);const[lotFilter,setLotFilter]=useState("");
  // v8.4.3: fab_lot_id 검색도 지원 — root_lot_id 대체 키로 사용 가능.
  const[fabLotId,setFabLotId]=useState("");const[fabSuggestions,setFabSuggestions]=useState([]);const[showFabDrop,setShowFabDrop]=useState(false);
  const[prefixes,setPrefixes]=useState([]);const[selPrefixes,setSelPrefixes]=useState(["KNOB"]);
  const[customs,setCustoms]=useState([]);const[selCustom,setSelCustom]=useState("");const[isCustomMode,setIsCustomMode]=useState(false);
  const[viewMode,setViewMode]=useState("all");
  const[data,setData]=useState(null);const[loading,setLoading]=useState(false);
  const[editing,setEditing]=useState(false);const[pendingPlans,setPendingPlans]=useState({});
  const[showConfirm,setShowConfirm]=useState(false);
  // dbl-click inline edit: {cellKey, value, suggestions, param}
  const[activeCell,setActiveCell]=useState(null);
  const[colValCache,setColValCache]=useState({});
  // v8.4.7: KNOB feature_name → {label, groups}. 제품 바뀌면 재fetch.
  const[knobMeta,setKnobMeta]=useState({});
  // v8.4.9-b: Notes (wafer 태그 + param 메모). lot 단위로 fetch.
  const[notes,setNotes]=useState([]);
  const[notesOpen,setNotesOpen]=useState(false);
  const[noteFilter,setNoteFilter]=useState(null); // {scope, key} or null = all
  const[noteDraft,setNoteDraft]=useState("");
  const[noteDraftScope,setNoteDraftScope]=useState(null);  // {scope, product, root_lot_id, wafer_id, param}
  const[tab,setTab]=useState("view");const[history,setHistory]=useState([]);const[histAll,setHistAll]=useState(false);
  const[colSearch,setColSearch]=useState("");const[customCols,setCustomCols]=useState([]);const[customName,setCustomName]=useState("");
  const[showSettings,setShowSettings]=useState(false);const[newPrefix,setNewPrefix]=useState("");const[mlOnly,setMlOnly]=useState(true);
  const[precision,setPrecision]=useState({});const[precisionDraft,setPrecisionDraft]=useState({});
  const[enabledSources,setEnabledSources]=useState(null); // null = loading, Set of product names
  // v8.4.4: product 별 lot_id 컬럼 override (soft-landing)
  const[lotOverrides,setLotOverrides]=useState({});
  // v8.4.4: fab_source 후보 (FileBrowser/Dashboard 와 동일 source 리스트)
  const[fabSourceOptions,setFabSourceOptions]=useState([]);
  // v8.7.8: fab_source 후보 = DB 상위폴더 (FAB/INLINE/ET/EDS) + Base 단일파일 + DB 제품 디렉토리 + TableMap.
  // 상위폴더 옵션은 ML_TABLE_<PROD> 제품명에서 <PROD> 를 추출해 동일 폴더 아래 제품으로 자동 매칭됨.
  useEffect(()=>{
    const out=[];
    const fabRoots=sf(API+"/fab-roots").then(d=>{
      for(const r of (d.roots||[])){
        out.push({value:`root:${r.name}`,label:`[상위폴더] ${r.name} · ${r.products.length}개 제품`,source_type:"db_root",is_root:true,products:r.products});
      }
    }).catch(()=>{});
    const dash=sf("/api/dashboard/products").then(d=>{
      for(const p of (d.products||[])){
        const v=p.root&&p.product?`${p.root}/${p.product}`:(p.file||p.label||"");
        if(!v) continue;
        const tag=p.source_type==="base_file"?"Base":p.root?`DB/${p.root}`:"DB";
        out.push({value:v,label:`[${tag}] ${p.label||v}`,source_type:p.source_type||""});
      }
    }).catch(()=>{});
    const tmap=sf("/api/dbmap/tables").then(d=>{
      for(const t of (d.tables||[])){
        const name=t.display_name||t.name||t.id;
        if(!name) continue;
        out.push({value:`tablemap:${t.id}`,label:`[TableMap] ${name}`,source_type:"tablemap"});
      }
    }).catch(()=>{});
    Promise.all([fabRoots,dash,tmap]).then(()=>{
      const seen=new Set();
      setFabSourceOptions(out.filter(o=>{if(seen.has(o.value)) return false;seen.add(o.value);return true;}));
    });
  },[]);
  // v8.7.8: ML_TABLE auto-match — selProd 에서 파생 제품명 → 상위폴더 매칭 후보.
  // v8.8.3: auto_path / effective_fab_source / manual_override 도 받아서 상태 표시에 사용.
  const[mlMatch,setMlMatch]=useState({pro:"",matches:[],auto_path:"",effective_fab_source:"",manual_override:false});
  useEffect(()=>{if(!selProd){setMlMatch({pro:"",matches:[],auto_path:"",effective_fab_source:"",manual_override:false});return;}
    sf(API+"/ml-table-match?product="+encodeURIComponent(selProd))
      .then(d=>setMlMatch({pro:d.derived_product||"",matches:d.matches||[],auto_path:d.auto_path||"",effective_fab_source:d.effective_fab_source||"",manual_override:!!d.manual_override}))
      .catch(()=>setMlMatch({pro:"",matches:[],auto_path:"",effective_fab_source:"",manual_override:false}));
  },[selProd,lotOverrides]);
  const isAdmin=user?.role==="admin";
  const lotRef=useRef(null);
  // v4.1: Features tab state — drives /splittable/features (wide ET⋈INLINE) and
  // /splittable/uniques (catalog for KNOB/MASK/product/ppid filters + feature names).
  const[features,setFeatures]=useState(null);const[featuresLoading,setFeaturesLoading]=useState(false);
  const[uniques,setUniques]=useState(null);
  const[featProd,setFeatProd]=useState("");const[featPpid,setFeatPpid]=useState("");
  const[featKnob,setFeatKnob]=useState("");const[featKnobVal,setFeatKnobVal]=useState("");
  const[featMask,setFeatMask]=useState("");
  const[selFeatCols,setSelFeatCols]=useState([]);const[mlPlan,setMlPlan]=useState(null);

  const reloadCustoms=()=>sf(API+"/customs").then(d=>setCustoms(d.customs||[]));
  // v4.1: Features loader — wide ET⋈INLINE sample (default 200 rows, 40 cols).
  const loadFeatures=()=>{setFeaturesLoading(true);
    sf(API+"/features?rows=200&cols=40").then(d=>{setFeatures(d);setFeaturesLoading(false);})
      .catch(e=>{alert(e.message);setFeaturesLoading(false);});};
  // v4.1: Uniques catalog — _uniques.json as-is. Runs once alongside products.
  const loadUniques=()=>sf(API+"/uniques").then(d=>setUniques(d.uniques||{})).catch(()=>setUniques({}));
  const loadSourceConfig=()=>sf(API+"/source-config").then(d=>{if(d.enabled?.length)setEnabledSources(new Set(d.enabled));if(d.lot_overrides)setLotOverrides(d.lot_overrides);}).catch(()=>{});
  const saveSourceConfig=(enabled)=>{sf(API+"/source-config/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:[...enabled]})}).catch(()=>{});};
  useEffect(()=>{
    Promise.all([sf(API+"/products").catch(()=>({products:[]})),sf(API+"/source-config").catch(()=>({enabled:[]})),sf(API+"/prefixes").catch(()=>({prefixes:[]}))])
      .then(([prodRes,srcRes,prefRes])=>{
        const prods=prodRes.products||[];setProducts(prods);
        const enabled=srcRes.enabled?.length?new Set(srcRes.enabled):null;
        setEnabledSources(enabled);
        // Set initial product to first visible source
        const visible=enabled?prods.filter(p=>enabled.has(p.name)):prods;
        if(visible.length)setSelProd(visible[0].name);else if(prods.length)setSelProd(prods[0].name);
        setPrefixes(prefRes.prefixes||[]);
      });
    reloadCustoms();
    loadUniques();
    sf(API+"/precision").then(d=>{setPrecision(d.precision||{});setPrecisionDraft(d.precision||{});}).catch(()=>{});
  },[]);
  const visibleProducts=enabledSources&&enabledSources.size>0?products.filter(p=>enabledSources.has(p.name)):enabledSources?[]:products;
  // When enabledSources or products change, ensure selProd is in visible list
  useEffect(()=>{
    if(enabledSources&&selProd&&!enabledSources.has(selProd)){
      if(visibleProducts.length)setSelProd(visibleProducts[0].name);
    }
  },[enabledSources,products]);
  useEffect(()=>{if(selProd)sf(API+"/lot-ids?product="+selProd).then(d=>setLotSuggestions(d.lot_ids||[])).catch(()=>{});},[selProd]);
  // v8.4.7: 제품 바뀔 때 KNOB meta 재fetch.
  useEffect(()=>{if(!selProd){setKnobMeta({});return;}
    sf(API+"/knob-meta?product="+encodeURIComponent(selProd))
      .then(d=>setKnobMeta(d.features||{})).catch(()=>setKnobMeta({}));
  },[selProd]);
  // fab_lot_id 후보도 fetch (lot-candidates 엔드포인트 사용)
  useEffect(()=>{if(selProd)sf(API+"/lot-candidates?product="+encodeURIComponent(selProd)+"&col=fab_lot_id&limit=500").then(d=>setFabSuggestions(d.candidates||[])).catch(()=>{});},[selProd]);
  useEffect(()=>{const h=e=>{if(lotRef.current&&!lotRef.current.contains(e.target))setShowLotDrop(false);};document.addEventListener("mousedown",h);return()=>document.removeEventListener("mousedown",h);},[]);

  const prefixParam=isCustomMode?"":selPrefixes.join(",");
  // diff 모드는 클라이언트에서 즉시 필터 → 항상 "all" 로 fetch
  const loadView=()=>{if(!selProd||(!lotId.trim()&&!fabLotId.trim()))return;setLoading(true);
    let url=API+"/view?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+"&view_mode=all";
    if(fabLotId.trim())url+="&fab_lot_id="+encodeURIComponent(fabLotId.trim());
    if(isCustomMode&&selCustom)url+="&custom_name="+encodeURIComponent(selCustom);
    sf(url).then(d=>{setData(d);if(d.precision)setPrecision(d.precision);setLoading(false);setPendingPlans({});reloadNotes();}).catch(e=>{alert(e.message);setLoading(false);});};
  // v8.4.9-b: Notes reload — 로트가 정해지면 해당 로트 범위로 가져옴.
  const reloadNotes=()=>{const prod=selProd, lot=lotId;if(!prod||!lot){setNotes([]);return;}
    sf(API+"/notes?product="+encodeURIComponent(prod)+"&root_lot_id="+encodeURIComponent(lot))
      .then(d=>setNotes(d.notes||[])).catch(()=>setNotes([]));};
  const addNote=()=>{const txt=(noteDraft||"").trim();const sc=noteDraftScope;if(!txt||!sc)return;
    sf(API+"/notes/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...sc,text:txt,username:user?.username||""})})
      .then(()=>{setNoteDraft("");reloadNotes();}).catch(e=>alert("노트 저장 실패: "+e.message));};
  const deleteNote=(id)=>{if(!confirm("삭제?"))return;
    sf(API+"/notes/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,username:user?.username||""})})
      .then(()=>reloadNotes()).catch(e=>alert("삭제 실패: "+e.message));};
  const notesForWafer=(wid)=>notes.filter(n=>n.scope==="wafer"&&n.key===`${selProd}__${lotId}__W${wid}`);
  const notesForParam=(param)=>notes.filter(n=>n.scope==="param"&&n.key.endsWith(`__${param}`)&&n.key.startsWith(`${selProd}__${lotId}__W`));
  // v8.4.9-c: 특정 (wafer × param) 셀용 메모 — 행/열 교차 단위.
  const notesForCell=(wid,param)=>notes.filter(n=>n.scope==="param"&&n.key===`${selProd}__${lotId}__W${wid}__${param}`);
  // v8.7.8: parameter 전역 태그 (product 내 모든 LOT 공통) + LOT 노트
  const notesParamGlobal=(param)=>notes.filter(n=>n.scope==="param_global"&&n.key===`${selProd}__PARAM__${param}`);
  const notesForLot=()=>notes.filter(n=>n.scope==="lot"&&n.key===`${selProd}__LOT__${lotId}`);
  const doSearch=()=>loadView();
  const loadHistory=(all)=>{let url=API+"/history?product="+encodeURIComponent(selProd)+"&limit=500";if(!all&&lotId.trim())url+="&root_lot_id="+encodeURIComponent(lotId);sf(url).then(d=>setHistory(d.history||[]));};
  const savePlans=()=>{if(!Object.keys(pendingPlans).length)return;
    sf(API+"/plan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,plans:pendingPlans,username:user?.username||"",root_lot_id:lotId})})
      .then(()=>{setShowConfirm(false);setEditing(false);loadView();}).catch(e=>alert(e.message));};
  const deletePlan=(ck)=>{if(!confirm("Delete?"))return;sf(API+"/plan/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:selProd,cell_keys:[ck],username:user?.username||""})}).then(loadView);};

  // v8.6.1: 낙관적 잠금 — 동일 name 의 기존 custom version 을 expected_version 으로 첨부.
  // 충돌(다른 사용자 저장) 시 conflict 응답 → confirm 으로 덮어쓸지 reload 할지 선택.
  const saveCustom=(force)=>{if(!customName.trim()||!customCols.length)return;
    const existing=customs.find(c=>c.name===customName);
    const ev=force?null:(existing?(existing.version||1):0);
    sf(API+"/customs/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:customName,username:user?.username||"",columns:customCols,expected_version:ev})})
      .then(d=>{
        if(d&&d.conflict){
          if(confirm("⚠ '"+customName+"' 가 다른 사용자에 의해 변경되었습니다.\n\nOK = 그래도 덮어쓰기\nCancel = 최신 데이터 불러오기")){
            saveCustom(true);
          } else {
            reloadCustoms();
            const cur=d.current||{};
            if(cur.columns)setCustomCols(cur.columns);
          }
          return;
        }
        reloadCustoms();setSelCustom(customName);setIsCustomMode(true);
      }).catch(e=>alert("저장 실패: "+(e.message||e)));};
  const deleteCustom=(name)=>{if(!confirm("Delete '"+name+"'?"))return;
    sf(API+"/customs/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,username:user?.username||""})})
      .then(()=>{reloadCustoms();if(selCustom===name)setSelCustom("");}).catch(e=>alert(e.message));};
  const selectCustomSet=(c)=>{setSelCustom(c.name);setCustomCols(c.columns||[]);setCustomName(c.name);};

  const togglePrefix=(p)=>{if(isCustomMode){setIsCustomMode(false);setSelCustom("");setSelPrefixes([p]);return;}
    setSelPrefixes(prev=>prev.includes(p)?prev.filter(x=>x!==p).length?prev.filter(x=>x!==p):[p]:[...prev,p]);};
  const addPrefix=()=>{if(!newPrefix.trim())return;const np=newPrefix.trim().toUpperCase();
    sf(API+"/prefixes/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prefixes:[...prefixes,np]})}).then(()=>{setPrefixes(prev=>[...prev,np]);setNewPrefix("");});};
  const savePrecision=()=>{
    sf(API+"/precision/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({precision:precisionDraft})})
      .then(d=>{setPrecision(d.precision||{});setPrecisionDraft(d.precision||{});})
      .catch(e=>alert(e.message));
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
  // v8.4.5: plan 이 actual 과 같은 값이면 팔레트 bg 그대로 + 이탤릭 + 핀 + 주황 테두리.
  // 다른 값(mismatch) 이면 빨간 좌측 테두리 + 연한 빨강 bg.
  // plan-only (actual 없음) 이면 plan 값의 팔레트 bg (색상 맞춰짐) + 이탤릭 + 주황 테두리.
  const getCellPlanStyle=(cell)=>{if(!cell)return{};
    if(cell.plan&&cell.actual){
      if(String(cell.plan)===String(cell.actual))return{}; // match = normal (값이 같아서 별도 강조 불필요)
      return{borderLeft:"3px solid #ef4444",background:"#fef2f2"}; // MISMATCH = 빨강
    }
    if(cell.plan)return{borderLeft:"3px solid #f97316",fontStyle:"italic",fontWeight:700}; // plan-only: bg 는 getCellBg 가 plan 값 기준으로 처리
    return{};};

  const allCols=data?.all_columns||[];
  const filteredCols=colSearch?allCols.filter(c=>c.toLowerCase().includes(colSearch.toLowerCase())):allCols.slice(0,100);
  const filteredLots=lotFilter?lotSuggestions.filter(l=>l.toLowerCase().includes(lotFilter.toLowerCase())):lotSuggestions;
  const S={padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none"};
  const chipS=(active)=>({padding:"3px 8px",borderRadius:4,fontSize:10,cursor:"pointer",fontWeight:active?700:400,background:active?"var(--accent-glow)":"var(--bg-hover)",color:active?"var(--accent)":"var(--text-secondary)",border:active?"1px solid var(--accent)":"1px solid transparent"});

  return(<div style={{display:"flex",height:"calc(100vh - 48px)",background:"var(--bg-primary)",color:"var(--text-primary)"}}>
    {/* v8.4.9-c: 셀 hover 시 빈 💬+ 배지 페이드인 */}
    <style>{`.stm-cell:hover .stm-note-btn{opacity:1 !important;}`}</style>
    {/* Sidebar */}
    <div style={{width:250,minWidth:250,borderRight:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",flexDirection:"column",overflow:"auto",position:"relative"}}>
      <div style={{padding:"12px 14px",borderBottom:"1px solid var(--border)",fontSize:12,fontWeight:700,color:"var(--text-secondary)",textTransform:"uppercase"}}>Split Table</div>
      <div style={{padding:"8px 12px"}}><div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Product</div>
        <select value={selProd} onChange={e=>setSelProd(e.target.value)} style={{...S,width:"100%"}}>{visibleProducts.map(p=><option key={p.name} value={p.name}>{p.name}</option>)}</select></div>
      {/* Lot ID dropdown */}
      <div style={{padding:"4px 12px"}} ref={lotRef}>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Root Lot ID</div>
        <input value={lotId} onChange={e=>{setLotId(e.target.value);setLotFilter(e.target.value);setShowLotDrop(true);}}
          onFocus={()=>setShowLotDrop(true)} placeholder="Enter or select..."
          style={{...S,width:"100%"}} onKeyDown={e=>e.key==="Enter"&&(setShowLotDrop(false),doSearch())}/>
        {showLotDrop&&filteredLots.length>0&&<div style={{maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)",marginTop:2}}>
          {filteredLots.slice(0,50).map(l=><div key={l} onClick={()=>{setLotId(l);setShowLotDrop(false);}}
            style={{padding:"6px 10px",fontSize:11,cursor:"pointer",borderBottom:"1px solid var(--border)",color:"var(--text-primary)"}}
            onMouseEnter={e=>e.currentTarget.style.background="var(--bg-hover)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{l}</div>)}
        </div>}
      </div>
      {/* v8.4.3: fab_lot_id 검색 — root_lot_id 대신 FAB 쪽 ID 로 조회 */}
      <div style={{padding:"4px 12px"}}>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Fab Lot ID (alt)</div>
        <input value={fabLotId} onChange={e=>{setFabLotId(e.target.value);setShowFabDrop(true);}}
          onFocus={()=>setShowFabDrop(true)} onBlur={()=>setTimeout(()=>setShowFabDrop(false),150)}
          placeholder="root_lot_id 대신 사용" style={{...S,width:"100%"}} onKeyDown={e=>e.key==="Enter"&&(setShowFabDrop(false),doSearch())}/>
        {showFabDrop&&fabSuggestions.length>0&&(fabLotId?fabSuggestions.filter(f=>f.toLowerCase().includes(fabLotId.toLowerCase())):fabSuggestions).length>0&&
          <div style={{maxHeight:160,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)",marginTop:2}}>
            {(fabLotId?fabSuggestions.filter(f=>f.toLowerCase().includes(fabLotId.toLowerCase())):fabSuggestions).slice(0,50).map(f=><div key={f} onMouseDown={()=>{setFabLotId(f);setShowFabDrop(false);}}
              style={{padding:"6px 10px",fontSize:11,cursor:"pointer",borderBottom:"1px solid var(--border)",color:"var(--text-primary)"}}
              onMouseEnter={e=>e.currentTarget.style.background="var(--bg-hover)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{f}</div>)}
          </div>}
      </div>
      <div style={{padding:"4px 12px"}}><div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Wafer IDs (optional)</div>
        <input value={waferIds} onChange={e=>setWaferIds(e.target.value)} placeholder="e.g. 1,2,3" style={{...S,width:"100%"}} onKeyDown={e=>e.key==="Enter"&&doSearch()}/></div>
      <div style={{padding:"6px 12px"}}><button onClick={doSearch} style={{width:"100%",padding:"7px 0",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:12,fontWeight:600,cursor:"pointer"}}>Search</button></div>
      {/* Prefix multi-select */}
      <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)"}}><div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Prefix</div>
        <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
          {prefixes.map(p=><span key={p} onClick={()=>togglePrefix(p)} style={chipS(selPrefixes.includes(p)&&!isCustomMode)}>{p}</span>)}
          <span onClick={()=>{setIsCustomMode(true);setSelPrefixes([]);}} style={chipS(isCustomMode)}>CUSTOM</span>
        </div></div>
      {/* Custom mode */}
      {isCustomMode&&<div style={{padding:"8px 12px",borderTop:"1px solid var(--border)",flex:1,overflow:"auto"}}>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Custom Sets</div>
        {customs.map(c=><div key={c.name} style={{display:"flex",alignItems:"center",gap:4,padding:"3px 6px",borderRadius:4,marginBottom:2,background:selCustom===c.name?"var(--accent-glow)":"transparent",cursor:"pointer"}}
          onClick={()=>selectCustomSet(c)}>
          <span style={{flex:1,fontSize:11,color:selCustom===c.name?"var(--accent)":"var(--text-primary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{c.name}</span>
          <span style={{fontSize:8,color:"var(--text-secondary)",flexShrink:0}}>{c.updated?.slice(5,10)||c.created?.slice(5,10)||""}</span>
          {(c.username===user?.username||isAdmin)&&<span onClick={e=>{e.stopPropagation();deleteCustom(c.name);}} style={{fontSize:9,color:"#ef4444",cursor:"pointer",flexShrink:0}} title="Delete">✕</span>}
        </div>)}
        <div style={{marginTop:6,fontSize:10,color:"var(--text-secondary)"}}>Create / Edit:</div>
        <input value={colSearch} onChange={e=>setColSearch(e.target.value)} placeholder="Search columns..." style={{...S,width:"100%",fontSize:10,marginBottom:4,marginTop:4}}/>
        <div style={{maxHeight:120,overflow:"auto"}}>
          {filteredCols.map(c=><div key={c} onClick={()=>{if(!customCols.includes(c))setCustomCols([...customCols,c]);else setCustomCols(customCols.filter(x=>x!==c));}} style={{fontSize:10,padding:"2px 6px",cursor:"pointer",color:customCols.includes(c)?"var(--accent)":"var(--text-secondary)"}}>{customCols.includes(c)?"✓ ":""}{c}</div>)}
        </div>
        {customCols.length>0&&<div style={{marginTop:4}}>
          <div style={{fontSize:9,color:"var(--text-secondary)"}}>{customCols.length} selected</div>
          <div style={{display:"flex",gap:4,marginTop:4}}>
            <input value={customName} onChange={e=>setCustomName(e.target.value)} placeholder="Set name" style={{...S,flex:1,fontSize:10}}/>
            <button onClick={saveCustom} style={{padding:"3px 8px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:10,cursor:"pointer"}}>Save</button>
          </div>
          <div style={{fontSize:8,color:"var(--text-secondary)",marginTop:2}}>Same name = overwrite</div>
        </div>}
      </div>}
      {/* Settings gear */}
      {isAdmin&&<div>
        <div onClick={()=>setShowSettings(!showSettings)} style={{position:"fixed",bottom:16,left:16,width:40,height:40,borderRadius:"50%",background:"var(--bg-secondary)",border:"1px solid var(--border)",display:"flex",alignItems:"center",justifyContent:"center",cursor:"pointer",zIndex:97,boxShadow:"0 2px 8px rgba(0,0,0,0.3)",fontSize:18}} title="Admin settings">⚙️</div>
        {showSettings&&<><div style={{position:"fixed",inset:0,zIndex:98}} onClick={()=>setShowSettings(false)}/><div style={{position:"fixed",bottom:48,left:16,background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:10,padding:16,width:280,maxHeight:"70vh",overflow:"auto",zIndex:99,boxShadow:"0 8px 30px rgba(0,0,0,0.5)"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
            <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>Split Table Settings</span>
            <span onClick={()=>setShowSettings(false)} style={{cursor:"pointer",color:"var(--text-secondary)",fontSize:16}}>✕</span>
          </div>
          {/* Source visibility checkboxes — Base 파일(ML_TABLE_ 등)만 표시 */}
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:6,fontWeight:600}}>Visible Sources (check to show to users)</div>
          {(()=>{const baseProds=products.filter(p=>p.source_type==="base_file");const allBaseNames=baseProds.map(x=>x.name);return(<>
            {baseProds.map(p=>{const checked=!enabledSources||enabledSources.has(p.name);return(
              <label key={p.name} style={{display:"flex",alignItems:"center",gap:6,padding:"4px 0",fontSize:11,cursor:"pointer",borderBottom:"1px solid var(--border)"}}>
                <input type="checkbox" checked={checked} onChange={()=>{
                  const next=new Set(enabledSources||allBaseNames);
                  if(next.has(p.name))next.delete(p.name);else next.add(p.name);
                  setEnabledSources(next);saveSourceConfig(next);
                }} style={{width:14,height:14,accentColor:"var(--accent)"}}/>
                <span style={{fontFamily:"monospace",flex:1}}>{p.name}</span>
                <span style={{fontSize:9,color:"var(--text-secondary)"}}>{p.type||"parquet"}</span>
              </label>);})}
            <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:4,marginBottom:10}}>
              {enabledSources?[...enabledSources].filter(n=>allBaseNames.includes(n)).length:baseProds.length} of {baseProds.length} visible to users
            </div>
          </>)})()}
          {/* Prefix management */}
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4,fontWeight:600}}>Prefix Management</div>
          {prefixes.map(p=><div key={p} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"3px 0",fontSize:11}}>
            <span style={{fontFamily:"monospace"}}>{p}</span><span onClick={()=>removePrefix(p)} style={{color:"#ef4444",cursor:"pointer",fontSize:10}}>✕</span>
          </div>)}
          <div style={{display:"flex",gap:4,marginTop:6}}>
            <input value={newPrefix} onChange={e=>setNewPrefix(e.target.value)} placeholder="New prefix" style={{...S,flex:1,fontSize:10}} onKeyDown={e=>e.key==="Enter"&&addPrefix()}/>
            <button onClick={addPrefix} style={{padding:"3px 8px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:10,cursor:"pointer"}}>+</button>
          </div>
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4,fontWeight:600,marginTop:10}}>Decimal Precision (per prefix)</div>
          <div style={{fontSize:9,color:"var(--text-secondary)",marginBottom:6}}>숫자 셀을 몇째 자리까지 표시할지 (0-10, 기본 INLINE/VM = 2)</div>
          {[...new Set([...Object.keys(precisionDraft||{}),...prefixes,"INLINE","VM"])].map(pfx=>{
            const v=precisionDraft[pfx];
            return(<div key={pfx} style={{display:"flex",alignItems:"center",gap:6,padding:"3px 0",fontSize:11}}>
              <span style={{fontFamily:"monospace",flex:1}}>{pfx}</span>
              <input type="number" min={0} max={10} value={v==null?"":v} placeholder="none"
                onChange={e=>{
                  const val=e.target.value;
                  const next={...precisionDraft};
                  if(val===""||val==null)delete next[pfx];
                  else next[pfx]=Math.max(0,Math.min(10,Number(val)||0));
                  setPrecisionDraft(next);
                }}
                style={{width:60,padding:"3px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,fontFamily:"monospace"}}/>
            </div>);
          })}
          <button onClick={savePrecision} style={{marginTop:6,padding:"4px 10px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:10,cursor:"pointer",fontWeight:600}}>Save Precision</button>

          {/* v8.4.4: root/fab_lot_id 컬럼 오버라이드 (선택된 product 기준, soft-landing) */}
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4,fontWeight:600,marginTop:10}}>Lot ID 컬럼 오버라이드 ({selProd||"product 선택 필요"})</div>
          <div style={{fontSize:9,color:"var(--text-secondary)",marginBottom:6}}>비우면 자동 감지. 입력 시 지정 컬럼 사용.</div>
          {/* v8.8.3: 자동 매칭 상태 표시 — ML_TABLE_<PROD> → DB/<root>/<PROD> 자동 매칭 결과.
                      매뉴얼 fab_source 비워두면 이 경로가 자동 사용됨. */}
          {selProd&&mlMatch&&(mlMatch.effective_fab_source||mlMatch.auto_path||(mlMatch.matches&&mlMatch.matches.length>0))&&(
            <div style={{fontSize:9,color:"var(--text-secondary)",marginBottom:8,padding:"6px 8px",background:"var(--bg-card)",borderRadius:4,border:"1px dashed var(--border)",lineHeight:1.5}}>
              <div><b>자동 매칭</b>: ML_TABLE_{mlMatch.pro||"?"} → <span style={{color:"var(--accent)",fontFamily:"monospace"}}>{mlMatch.auto_path||"(매칭 없음)"}</span></div>
              <div>실제 사용: <span style={{color:mlMatch.manual_override?"#f59e0b":"#22c55e",fontFamily:"monospace"}}>{mlMatch.effective_fab_source||"(오버라이드 off)"}</span> {mlMatch.manual_override?"(매뉴얼)":"(자동)"}</div>
              {mlMatch.matches&&mlMatch.matches.length>1&&(
                <div>후보: {mlMatch.matches.map(m=>m.path).join(", ")}</div>
              )}
              <div style={{marginTop:3,color:"var(--text-secondary)"}}>ts_col 기준 최신 레코드만 join — 매뉴얼 오버라이드가 비어있으면 자동 매칭 경로 사용.</div>
            </div>
          )}
          {selProd&&(()=>{const ov=(lotOverrides&&lotOverrides[selProd])||{};const setOv=(k,v)=>{const n={...lotOverrides,[selProd]:{...ov,[k]:v}};setLotOverrides(n);};
            return(<div style={{display:"flex",flexDirection:"column",gap:4}}>
              <label style={{display:"flex",alignItems:"center",gap:6,fontSize:10}}><span style={{width:80,fontFamily:"monospace",color:"var(--text-secondary)"}}>root_col</span><input value={ov.root_col||""} onChange={e=>setOv("root_col",e.target.value)} placeholder="root_lot_id" style={{...S,flex:1,fontSize:10,fontFamily:"monospace"}}/></label>
              <label style={{display:"flex",alignItems:"center",gap:6,fontSize:10}}><span style={{width:80,fontFamily:"monospace",color:"var(--text-secondary)"}}>wf_col</span><input value={ov.wf_col||""} onChange={e=>setOv("wf_col",e.target.value)} placeholder="wafer_id" style={{...S,flex:1,fontSize:10,fontFamily:"monospace"}}/></label>
              <label style={{display:"flex",alignItems:"center",gap:6,fontSize:10}}><span style={{width:80,fontFamily:"monospace",color:"var(--text-secondary)"}}>fab_col</span><input value={ov.fab_col||""} onChange={e=>setOv("fab_col",e.target.value)} placeholder="fab_lot_id" style={{...S,flex:1,fontSize:10,fontFamily:"monospace"}}/></label>
              <div style={{display:"flex",flexDirection:"column",gap:2}}>
                <div style={{display:"flex",alignItems:"center",gap:6,fontSize:10}}>
                  <span style={{width:80,fontFamily:"monospace",color:"var(--text-secondary)"}}>fab_source</span>
                  <label style={{display:"flex",alignItems:"center",gap:4,marginLeft:"auto",fontSize:9,color:"var(--text-secondary)",cursor:"pointer",userSelect:"none"}}>
                    <input type="checkbox" checked={mlOnly} onChange={e=>setMlOnly(e.target.checked)} style={{width:11,height:11,accentColor:"var(--accent)"}}/>
                    ML_TABLE만 보기
                  </label>
                </div>
                <select value={ov.fab_source||""} onChange={e=>setOv("fab_source",e.target.value)} style={{...S,width:"100%",fontSize:10,fontFamily:"monospace"}}>
                  <option value="">— 없음 (ML_TABLE 내장 사용) —</option>
                  {fabSourceOptions.filter(o=>!mlOnly||o.value.toUpperCase().includes("ML_TABLE")).map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <label style={{display:"flex",alignItems:"center",gap:6,fontSize:10}}><span style={{width:80,fontFamily:"monospace",color:"var(--text-secondary)"}}>ts_col</span><input value={ov.ts_col||""} onChange={e=>setOv("ts_col",e.target.value)} placeholder="out_ts (최신기준)" style={{...S,flex:1,fontSize:10,fontFamily:"monospace"}}/></label>
              <button onClick={()=>{sf(API+"/source-config/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:[...(enabledSources||new Set())],lot_overrides:lotOverrides||{}})}).then(()=>{loadView&&loadView();});}} style={{marginTop:4,padding:"4px 10px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:10,cursor:"pointer",fontWeight:600}}>Save Overrides</button>
            </div>);
          })()}

          <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:10,marginBottom:10,lineHeight:1.5}}>
            Color-coded: {COLOR_PREFIXES.join(", ")}
          </div>
          <button onClick={()=>setShowSettings(false)} style={{width:"100%",padding:"8px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,fontSize:11,cursor:"pointer"}}>Save & Close</button>
        </div></>}
      </div>}
    </div>
    {/* Main */}
    <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
      <div style={{padding:"8px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
        <span style={{fontSize:13,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{selProd}</span>
        {lotId&&<span style={{fontSize:11,color:"var(--text-secondary)"}}>| {lotId}</span>}
        <span style={{fontSize:10,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"2px 8px",borderRadius:4}}>
          {isCustomMode?"CUSTOM"+(selCustom?": "+selCustom:""):selPrefixes.join("+")}</span>
        <div style={{marginLeft:"auto",display:"flex",gap:4,alignItems:"center"}}>
          {/* v8.4.3: Features 탭 제거 — ML_TABLE_PROD* 가 source 이므로 별도 features 뷰 불필요. */}
          {[{k:"view",l:"View"},{k:"history",l:"History"}].map(({k,l})=><span key={k} className={"splittable-tab splittable-tab-"+k} data-active={tab===k?"1":"0"} onClick={()=>{setTab(k);if(k==="history")loadHistory(histAll);}} style={{padding:"4px 10px",borderRadius:4,fontSize:11,cursor:"pointer",background:tab===k?"var(--accent-glow)":"transparent",color:tab===k?"var(--accent)":"var(--text-secondary)",fontWeight:tab===k?600:400}}>{l}</span>)}
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          {["all","diff"].map(m=><span key={m} onClick={()=>setViewMode(m)} style={{padding:"4px 10px",borderRadius:4,fontSize:11,cursor:"pointer",background:viewMode===m?"var(--accent-glow)":"transparent",color:viewMode===m?"var(--accent)":"var(--text-secondary)",fontWeight:viewMode===m?600:400}}>{m}</span>)}
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          {editing?<>
            <button onClick={()=>{if(Object.keys(pendingPlans).length>0)setShowConfirm(true);else setEditing(false);}} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"#22c55e",color:"#fff",fontSize:11,fontWeight:600,cursor:"pointer"}}>Save ({Object.keys(pendingPlans).length})</button>
            <button onClick={()=>{setEditing(false);setPendingPlans({});}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>Cancel</button>
          </>:<>
            {/* v8.4.9: window.open → dl() — 새 탭은 토큰 헤더가 안 붙어 401. blob 다운로드로 전환. */}
            <button onClick={()=>{const url=API+"/download-csv?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+(isCustomMode&&selCustom?"&custom_name="+encodeURIComponent(selCustom):"")+"&transposed=true&username="+encodeURIComponent(user?.username||"");dl(url, `splittable_${selProd}_${lotId||"all"}.csv`).catch(e=>alert("CSV 다운로드 실패: "+e.message));}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:11,cursor:"pointer"}}>⬇ CSV</button>
            <button onClick={()=>{const url=API+"/download-xlsx?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+(isCustomMode&&selCustom?"&custom_name="+encodeURIComponent(selCustom):"")+"&username="+encodeURIComponent(user?.username||"");dl(url, `splittable_${selProd}_${lotId||"all"}.xlsx`).catch(e=>alert("XLSX 다운로드 실패: "+e.message));}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid #10b981",background:"transparent",color:"#10b981",fontSize:11,cursor:"pointer"}} title="XLSX (fab_lot_id 병합)">⬇ XLSX</button>
            <button onClick={()=>setEditing(true)} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,fontWeight:600,cursor:"pointer"}}>Edit</button>
            {/* v8.4.9-b: 노트 드로어 토글 */}
            <button onClick={()=>{setNoteFilter(null);setNotesOpen(true);}} title="wafer 태그 · 파라미터 메모" style={{padding:"4px 12px",borderRadius:4,border:"1px solid #3b82f6",background:"transparent",color:"#3b82f6",fontSize:11,fontWeight:600,cursor:"pointer",display:"inline-flex",gap:4,alignItems:"center"}}>📝 노트{notes.length>0&&<span style={{padding:"0 6px",borderRadius:10,background:"#3b82f6",color:"#fff",fontSize:9,fontWeight:700}}>{notes.length}</span>}</button>
          </>}
        </div>
      </div>
      {loading?<div style={{padding:40,textAlign:"center"}}><Loading text="Loading..."/></div>
      :data?.msg&&!data?.rows?.length?<div style={{padding:60,textAlign:"center",color:"var(--text-secondary)",fontSize:13}}>{data.msg}</div>
      :tab==="view"&&data?.rows?.length?(()=>{
        // 클라이언트 diff 필터: viewMode==='diff' 이면 non-null unique 값 >= 2 인 행만
        const displayRows = viewMode==="diff"
          ? data.rows.filter(r=>{const vs=Object.values(r._cells||{}).map(c=>c?.actual).filter(v=>v!=null&&v!==""&&v!=="None"&&v!=="null");return new Set(vs).size>=2;})
          : data.rows;
        return <div style={{flex:1,overflow:"auto",background:"var(--bg-card)"}}>
        <table style={{borderCollapse:"collapse",fontSize:11,background:"var(--bg-card)",tableLayout:"fixed",width:288+(data.headers?.length||1)*115}}>
          <colgroup>
            <col style={{width:288}}/>
            {data.headers?.map((_,i)=><col key={i} style={{width:115}}/>)}
          </colgroup>
          <thead>
            {data.root_lot_id&&<tr style={{height:28}}><th style={{boxSizing:"border-box",height:28,padding:0,background:"var(--bg-tertiary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",position:"sticky",top:0,left:0,zIndex:5}}></th>
              <th colSpan={data.headers?.length||1} style={{boxSizing:"border-box",height:28,textAlign:"center",padding:"0 8px",lineHeight:"27px",fontWeight:700,fontSize:12,color:"var(--accent)",background:"var(--bg-tertiary)",borderBottom:"1px solid #555",position:"sticky",top:0,zIndex:4,fontFamily:"monospace"}}>{data.root_lot_id}{viewMode==="diff"?<span style={{marginLeft:8,fontSize:10,color:"var(--text-secondary)",fontWeight:400}}>(diff: {displayRows.length}/{data.rows.length})</span>:null}</th></tr>}
            {data.header_groups?.length>0&&<tr style={{height:24}}>
              <th style={{boxSizing:"border-box",height:24,padding:0,background:"var(--bg-tertiary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",position:"sticky",top:data.root_lot_id?28:0,left:0,zIndex:5}}></th>
              {data.header_groups.map((g,gi)=><th key={gi} colSpan={g.span} style={{boxSizing:"border-box",height:24,textAlign:"center",padding:"0 6px",fontWeight:700,fontSize:10,color:"#fbbf24",background:"var(--bg-tertiary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",position:"sticky",top:data.root_lot_id?28:0,zIndex:4,fontFamily:"monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={g.label}>{g.label}</th>)}
            </tr>}
            <tr>
            <th style={{textAlign:"left",padding:"8px 10px",fontWeight:700,fontSize:10,color:"var(--accent)",borderBottom:"2px solid #555",borderRight:"1px solid #555",background:"var(--bg-tertiary)",position:"sticky",top:data.root_lot_id?(data.header_groups?.length>0?52:27):(data.header_groups?.length>0?24:0),left:0,zIndex:5,minWidth:260}}>Parameter</th>
            {data.headers?.map((h,i)=>{const wid=String(h).replace(/^#/,"");const wn=notesForWafer(wid).length;return(<th key={i} style={{textAlign:"center",padding:"6px 8px",fontWeight:600,fontSize:10,color:"var(--text-secondary)",borderBottom:"2px solid #555",borderRight:"1px solid #555",background:"var(--bg-tertiary)",position:"sticky",top:data.root_lot_id?(data.header_groups?.length>0?52:27):(data.header_groups?.length>0?24:0),zIndex:3,whiteSpace:"normal",wordBreak:"break-word",minWidth:100,cursor:"pointer"}} title={wn>0?`wafer ${h} — ${wn}개 태그 · 클릭해서 보기`:`wafer ${h} — 태그 추가`} onClick={()=>{setNoteFilter({scope:"wafer",key:`${selProd}__${lotId}__W${wid}`});setNoteDraftScope({scope:"wafer",product:selProd,root_lot_id:lotId,wafer_id:wid});setNotesOpen(true);}}>
              <div>{h}</div>
              {wn>0&&<span style={{display:"inline-block",marginTop:2,padding:"0 6px",borderRadius:10,background:"#3b82f6",color:"#fff",fontSize:9,fontWeight:700}}>🏷 {wn}</span>}
            </th>);})}
          </tr></thead>
          <tbody>{displayRows.map((row,ri)=>{
            const cells=row._cells||{};
            // v8.4.5: plan 값도 uniqMap 에 포함 — 같은 값이면 같은 팔레트 색상
            const allVals=Object.values(cells).map(c=>c?.actual||c?.plan).filter(v=>v&&v!=="None"&&v!=="null");
            const uniqVals=[...new Set(allVals)];const uniqMap={};uniqVals.forEach((v,i)=>{uniqMap[v]=i;});
            return(<tr key={ri}>
              <td style={{padding:"6px 10px",fontWeight:600,fontSize:11,color:"var(--text-primary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",background:"var(--bg-secondary)",position:"sticky",left:0,zIndex:2,whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35}} title={(knobMeta[row._param]?.label)?(row._param+" — "+knobMeta[row._param].label):row._param}>
                <div>{row._param?.replace(/^[A-Z]+_/,"")}</div>
                {/* v8.4.9: + 결합이면 줄바꿈. step_id 는 파란 pill 로 대비 강화. */}
                {Array.isArray(knobMeta[row._param]?.groups) && knobMeta[row._param].groups.length > 0 && (
                  <div style={{fontSize:10,fontWeight:400,lineHeight:1.5,marginTop:4,fontFamily:"monospace"}}>
                    {knobMeta[row._param].groups.map((g, gi) => (
                      <div key={gi} style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4,marginTop:gi>0?2:0}}>
                        {gi > 0 && <span style={{color:"#ef4444",fontWeight:800,fontSize:12,marginRight:2}}>+</span>}
                        <span style={{color:"#fbbf24",fontWeight:700}}>{g.func_step}</span>
                        {Array.isArray(g.step_ids) && g.step_ids.length > 0 && (
                          <span style={{display:"inline-flex",flexWrap:"wrap",gap:3}}>
                            {g.step_ids.map((sid, si) => (
                              <span key={si} style={{padding:"0 6px",borderRadius:3,background:"rgba(96,165,250,0.18)",border:"1px solid rgba(96,165,250,0.5)",color:"#93c5fd",fontWeight:700,fontSize:10,letterSpacing:0.3}}>{sid}</span>
                            ))}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </td>
              {data.headers?.map((_,ci)=>{
                const cell=cells[String(ci)];const wid=String(data.headers[ci]??"").replace(/^#/,"");
                const cellNoteCount=notesForCell(wid,row._param).length;
                if(!cell)return(<td key={ci} style={{borderBottom:"1px solid #555",borderRight:"1px solid #555",background:"var(--bg-card)",position:"relative"}}>
                  {cellNoteCount>0&&<span onClick={e=>{e.stopPropagation();setNoteFilter({scope:"cell",wafer_id:wid,param:row._param});setNoteDraftScope({scope:"param",product:selProd,root_lot_id:lotId,wafer_id:wid,param:row._param});setNotesOpen(true);}} title={`${cellNoteCount}개 메모`} style={{position:"absolute",top:1,right:2,cursor:"pointer",fontSize:9,padding:"0 5px",borderRadius:7,background:"#8b5cf6",color:"#fff",fontWeight:700,lineHeight:"14px"}}>💬 {cellNoteCount}</span>}
                </td>);
                const bgStyle=getCellBg(cell.actual||cell.plan,uniqMap,row._param);const planStyle=getCellPlanStyle(cell);
                const canPlan=cell.can_plan!==false; // default true for backward compat
                const baseStyle={background:"var(--bg-card)",color:"var(--text-primary)"};
                const canEdit=canPlan&&!cell.actual;
                const style={...baseStyle,...bgStyle,...planStyle,padding:"4px 8px",borderBottom:"1px solid #555",borderRight:"1px solid #555",textAlign:"center",fontSize:11,cursor:canEdit?"pointer":"default",whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35,position:"relative"};
                const hasPlan=cell.plan&&!cell.actual;
                const isMismatch=cell.mismatch||false;
                const display=formatCell(cell.actual,row._param)||"";
                const openEdit=()=>{if(!canEdit)return;
                  // 자동으로 editing 모드 진입 (dbl-click 시 Edit 버튼 클릭 없이도 작동)
                  if(!editing)setEditing(true);
                  setActiveCell({key:cell.key,param:row._param,value:pendingPlans[cell.key]||""});
                  // suggestion 캐시 확인 후 없으면 fetch
                  if(!colValCache[row._param]){
                    sf(API+"/column-values?product="+encodeURIComponent(selProd)+"&col="+encodeURIComponent(row._param)+"&limit=200")
                      .then(d=>setColValCache(m=>({...m,[row._param]:d.values||[]}))).catch(()=>{});
                  }
                };
                return(<td key={ci} className="stm-cell" style={style}
                  onClick={()=>{if(editing&&canEdit)openEdit();}}
                  onDoubleClick={()=>{if(canEdit)openEdit();}}
                  onContextMenu={e=>{if(cell.plan){e.preventDefault();deletePlan(cell.key);}}}>
                  {pendingPlans[cell.key]?<span style={{color:"#ea580c",fontWeight:700,fontStyle:"italic"}}>{"📌 "}{pendingPlans[cell.key]}</span>
                  :isMismatch?<span style={{color:"#dc2626",fontWeight:700}}>{"✗ "}{formatCell(cell.actual,row._param)}<span style={{fontSize:9,color:"#ef4444"}}>{" (≠"+cell.plan+")"}</span></span>
                  :hasPlan?<span style={{fontStyle:"italic",fontWeight:700}}>{"📌 "}{cell.plan}</span>
                  :display}
                  {/* v8.4.9-c: per-cell 메모 배지. 메모가 있으면 항상 표시, 없으면 hover 시에만 + 아이콘 노출. */}
                  <span className="stm-note-btn" onClick={e=>{e.stopPropagation();setNoteFilter({scope:"cell",wafer_id:wid,param:row._param});setNoteDraftScope({scope:"param",product:selProd,root_lot_id:lotId,wafer_id:wid,param:row._param});setNotesOpen(true);}} title={cellNoteCount>0?`${cellNoteCount}개 메모`:"메모 추가"} style={{position:"absolute",top:1,right:2,cursor:"pointer",fontSize:9,padding:"0 5px",borderRadius:7,background:cellNoteCount>0?"#8b5cf6":"rgba(139,92,246,0.25)",color:cellNoteCount>0?"#fff":"#8b5cf6",fontWeight:700,lineHeight:"14px",opacity:cellNoteCount>0?1:0,transition:"opacity 0.15s"}}>💬{cellNoteCount>0?" "+cellNoteCount:"+"}</span>
                </td>);})}
            </tr>);})}</tbody>
        </table></div>;
      })()
      :tab==="history"?<div style={{flex:1,overflow:"auto",padding:16}}>
        <div style={{display:"flex",gap:8,marginBottom:12,alignItems:"center"}}>
          <span onClick={()=>{setHistAll(false);loadHistory(false);}} style={{fontSize:11,cursor:"pointer",padding:"4px 10px",borderRadius:4,...(!histAll?{background:"var(--accent-glow)",color:"var(--accent)",fontWeight:600}:{color:"var(--text-secondary)"})}}>This Lot</span>
          <span onClick={()=>{setHistAll(true);loadHistory(true);}} style={{fontSize:11,cursor:"pointer",padding:"4px 10px",borderRadius:4,...(histAll?{background:"var(--accent-glow)",color:"var(--accent)",fontWeight:600}:{color:"var(--text-secondary)"})}}>All History</span>
          {isAdmin&&<button onClick={()=>dl(API+"/history-csv?product="+encodeURIComponent(selProd), `splittable_history_${selProd}.csv`).catch(e=>alert("이력 CSV 다운로드 실패: "+e.message))} style={{marginLeft:"auto",padding:"4px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:11,cursor:"pointer"}}>⬇ History CSV</button>}
        </div>
        {history.length===0?<div style={{textAlign:"center",padding:40,color:"var(--text-secondary)"}}>No history</div>
        :<table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
          <thead><tr>{["Time","User","Root Lot","Wafer","Column","Action","Old","New"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 10px",borderBottom:"2px solid var(--border)",color:"var(--text-secondary)",fontSize:11}}>{h}</th>)}</tr></thead>
          <tbody>{[...history].reverse().map((h,i)=>{const parts=h.cell?.split("|")||[];const lotPart=parts[0]||"";const wfPart=parts[1]||"";const colPart=parts[2]||h.cell||"";
            return(<tr key={i}>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{h.time?.slice(0,16)}</td>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>{h.user}</td>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,color:"var(--accent)"}}>{lotPart}</td>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10}}>{wfPart}</td>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,maxWidth:140,overflow:"hidden",textOverflow:"ellipsis"}} title={colPart}>{colPart}</td>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}><span style={{fontSize:10,padding:"1px 5px",borderRadius:3,background:h.action==="set"?"#f9731622":"#ef444422",color:h.action==="set"?"#f97316":"#ef4444"}}>{h.action}</span></td>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{h.old||"-"}</td>
            <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:"#22c55e"}}>{h.new||"-"}</td>
          </tr>);})}</tbody></table>}
      </div>:tab==="features"?(()=>{
        // v4.1: Features tab — wide ET⋈INLINE table with KNOB/MASK/product/ppid filters.
        // Feature names are sourced from _uniques.json (dvc_features + inline_features)
        // so "TC_NS_X" (canonical) and any alias form cannot diverge.
        const u=uniques||{};
        const prodOpts=Array.isArray(u.products)?u.products:[];
        const ppidOpts=featProd&&u.ppids&&u.ppids[featProd]?u.ppids[featProd]:[];
        const knobOpts=u.knobs&&typeof u.knobs==="object"?Object.keys(u.knobs):[];
        const knobValOpts=featKnob&&u.knobs&&Array.isArray(u.knobs[featKnob])?u.knobs[featKnob]:[];
        const maskOpts=u.masks&&Array.isArray(u.masks.reticles)?u.masks.reticles:(u.masks&&Array.isArray(u.masks.photo_steps)?u.masks.photo_steps:[]);
        const dvcNames=Array.isArray(u.dvc_features)?u.dvc_features.map(f=>f.name).filter(Boolean):[];
        const inlineNames=Array.isArray(u.inline_features)?u.inline_features.map(f=>f.name).filter(Boolean):[];
        const featureNames=[...dvcNames,...inlineNames];
        const sampleRows=features?.sample||[];
        // Client-side filter by selected values (best-effort; exact column name
        // depends on the feature-table schema — the filter is additive).
        const filterRow=(r)=>{
          if(featProd&&r.product!=null&&String(r.product)!==featProd)return false;
          if(featPpid&&r.ppid!=null&&String(r.ppid)!==featPpid)return false;
          if(featKnob&&featKnobVal&&r[featKnob]!=null&&String(r[featKnob])!==featKnobVal)return false;
          return true;
        };
        const filtered=sampleRows.filter(filterRow);
        const cols=features?.columns||[];
        const toggleFeat=(n)=>setSelFeatCols(prev=>prev.includes(n)?prev.filter(x=>x!==n):[...prev,n]);
        const buildPlan=()=>{
          const plan={
            created:new Date().toISOString(),
            product:featProd||null,ppid:featPpid||null,
            filters:{knob:featKnob?{name:featKnob,value:featKnobVal||null}:null,mask:featMask||null},
            features:[...selFeatCols],
            source:{endpoint:API+"/features",rows:features?.total_rows||null,cols:features?.total_cols||null},
          };
          setMlPlan(plan);
        };
        return(<div className="splittable-features" style={{flex:1,overflow:"auto",padding:16}}>
          <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginBottom:12}}>
            <button className="splittable-load-features" onClick={loadFeatures} disabled={featuresLoading}
              style={{padding:"6px 14px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:12,fontWeight:600,cursor:featuresLoading?"default":"pointer",opacity:featuresLoading?0.5:1}}>
              {featuresLoading?"Loading…":"Load features"}
            </button>
            {features&&<span style={{fontSize:11,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"4px 10px",borderRadius:6}}>
              {features.total_rows?.toLocaleString()}행 × {features.total_cols}열 | 표시 {sampleRows.length} | join: {features.join}
            </span>}
            {uniques&&<span style={{fontSize:10,color:"var(--text-secondary)"}}>uniques: {Object.keys(uniques).length} keys</span>}
          </div>
          {/* Filter row */}
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(180px,1fr))",gap:8,marginBottom:12,padding:10,background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:6}}>
            <label style={{fontSize:10,color:"var(--text-secondary)"}}>제품 (product)
              <select className="splittable-feat-product" value={featProd} onChange={e=>{setFeatProd(e.target.value);setFeatPpid("");}} style={{...S,width:"100%",marginTop:4}}>
                <option value="">— 전체 —</option>
                {prodOpts.map(p=><option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label style={{fontSize:10,color:"var(--text-secondary)"}}>PPID
              <select className="splittable-feat-ppid" value={featPpid} onChange={e=>setFeatPpid(e.target.value)} disabled={!featProd} style={{...S,width:"100%",marginTop:4,opacity:featProd?1:0.5}}>
                <option value="">— 전체 —</option>
                {ppidOpts.map(p=><option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label style={{fontSize:10,color:"var(--text-secondary)"}}>KNOB
              <select className="splittable-feat-knob" value={featKnob} onChange={e=>{setFeatKnob(e.target.value);setFeatKnobVal("");}} style={{...S,width:"100%",marginTop:4}}>
                <option value="">— 선택 —</option>
                {knobOpts.map(k=><option key={k} value={k}>{k}</option>)}
              </select>
            </label>
            <label style={{fontSize:10,color:"var(--text-secondary)"}}>KNOB 값
              <select className="splittable-feat-knob-val" value={featKnobVal} onChange={e=>setFeatKnobVal(e.target.value)} disabled={!featKnob} style={{...S,width:"100%",marginTop:4,opacity:featKnob?1:0.5}}>
                <option value="">— 전체 —</option>
                {knobValOpts.map(v=><option key={String(v)} value={String(v)}>{String(v)}</option>)}
              </select>
            </label>
            <label style={{fontSize:10,color:"var(--text-secondary)"}}>MASK
              <select className="splittable-feat-mask" value={featMask} onChange={e=>setFeatMask(e.target.value)} style={{...S,width:"100%",marginTop:4}}>
                <option value="">— 전체 —</option>
                {maskOpts.map(m=><option key={m} value={m}>{m}</option>)}
              </select>
            </label>
          </div>
          {/* Feature picker */}
          {featureNames.length>0&&<div style={{padding:10,background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:6,marginBottom:12}}>
            <div style={{fontSize:11,fontWeight:600,color:"var(--accent)",marginBottom:6}}>Feature 선택 ({selFeatCols.length}/{featureNames.length}) — _uniques.json 기반</div>
            <div style={{display:"flex",flexWrap:"wrap",gap:4,maxHeight:140,overflow:"auto"}}>
              {featureNames.map(n=>{const on=selFeatCols.includes(n);return(
                <span key={n} className="splittable-feature-chip" data-selected={on?"1":"0"} onClick={()=>toggleFeat(n)}
                  style={{padding:"3px 8px",borderRadius:4,fontSize:10,cursor:"pointer",fontFamily:"monospace",fontWeight:on?700:400,
                    background:on?"var(--accent-glow)":"var(--bg-hover)",color:on?"var(--accent)":"var(--text-secondary)",
                    border:"1px solid "+(on?"var(--accent)":"transparent")}}>{n}</span>
              );})}
            </div>
            <div style={{display:"flex",gap:6,marginTop:8,alignItems:"center"}}>
              <button className="splittable-build-plan" onClick={buildPlan} disabled={!selFeatCols.length}
                style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,fontWeight:600,cursor:selFeatCols.length?"pointer":"default",opacity:selFeatCols.length?1:0.5}}>
                ML plan 생성
              </button>
              {selFeatCols.length>0&&<button onClick={()=>{setSelFeatCols([]);setMlPlan(null);}} style={{padding:"4px 10px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>초기화</button>}
              {mlPlan&&<span style={{fontSize:10,color:"var(--accent)",fontFamily:"monospace"}}>plan: {mlPlan.features.length} features • {mlPlan.created.slice(11,19)}</span>}
            </div>
            {mlPlan&&<pre className="splittable-ml-plan" style={{margin:"8px 0 0",padding:8,background:"var(--bg-primary)",border:"1px solid var(--border)",borderRadius:4,fontSize:10,fontFamily:"monospace",color:"var(--text-secondary)",maxHeight:140,overflow:"auto",whiteSpace:"pre-wrap"}}>{JSON.stringify(mlPlan,null,2)}</pre>}
          </div>}
          {/* Features table */}
          {featuresLoading?<div style={{padding:40,textAlign:"center"}}><Loading text="Loading features..."/></div>
          :!features?<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)",fontSize:12}}>상단의 <b>Load features</b> 버튼으로 ET⋈INLINE wide form 을 불러오세요.</div>
          :<div className="splittable-features-table" style={{overflow:"auto",maxHeight:"calc(100vh - 440px)",background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:6}}>
            <table style={{borderCollapse:"collapse",fontSize:11,width:"max-content",minWidth:"100%"}}>
              <thead><tr>
                <th style={{padding:"6px 8px",textAlign:"left",fontSize:10,fontWeight:700,color:"var(--text-secondary)",borderBottom:"1px solid #555",background:"var(--bg-tertiary)",position:"sticky",top:0,zIndex:1}}>#</th>
                {cols.map(c=><th key={c} data-col={c} style={{padding:"6px 8px",textAlign:"left",fontSize:10,fontWeight:700,color:selFeatCols.includes(c)?"var(--accent)":"var(--text-secondary)",borderBottom:"1px solid #555",background:"var(--bg-tertiary)",position:"sticky",top:0,zIndex:1,whiteSpace:"nowrap",cursor:"pointer"}} onClick={()=>toggleFeat(c)} title="클릭 → feature select 토글">{c}</th>)}
              </tr></thead>
              <tbody>{filtered.slice(0,200).map((r,i)=>(<tr key={i}>
                <td style={{padding:"4px 8px",borderBottom:"1px solid #555",color:"#64748b",fontSize:10}}>{i+1}</td>
                {cols.map(c=><td key={c} style={{padding:"4px 8px",borderBottom:"1px solid #555",maxWidth:180,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontSize:11,background:selFeatCols.includes(c)?"var(--accent-glow)":"transparent"}} title={String(r[c]==null?"":r[c])}>
                  {r[c]===null||r[c]===undefined?<span style={{color:"#64748b"}}>null</span>:String(r[c])}
                </td>)}
              </tr>))}</tbody>
            </table>
            {filtered.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:11}}>필터와 일치하는 행이 없습니다.</div>}
          </div>}
        </div>);
      })():null}
    </div>
    {activeCell&&(()=>{const sugg=colValCache[activeCell.param]||[];const commit=(v)=>{const t=(v??"").trim();if(t)setPendingPlans(p=>({...p,[activeCell.key]:t}));setActiveCell(null);};
      return <div style={{position:"fixed",inset:0,zIndex:9998,background:"rgba(0,0,0,0.55)",display:"flex",alignItems:"center",justifyContent:"center"}} onClick={()=>setActiveCell(null)}>
        <div onClick={e=>e.stopPropagation()} style={{background:"var(--bg-secondary)",borderRadius:10,padding:18,width:360,border:"1px solid var(--border)"}}>
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4,fontFamily:"monospace"}}>{activeCell.key.split("|").slice(0,2).join(" · ")}</div>
          <div style={{fontSize:13,fontWeight:700,marginBottom:10,color:"var(--accent)",fontFamily:"monospace"}}>{activeCell.param}</div>
          <input autoFocus value={activeCell.value} onChange={e=>setActiveCell(c=>({...c,value:e.target.value}))}
            onKeyDown={e=>{if(e.key==="Enter")commit(activeCell.value);else if(e.key==="Escape")setActiveCell(null);}}
            list={`cv-${activeCell.key}`}
            placeholder="값 입력 또는 아래 리스트 선택"
            style={{width:"100%",padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:12,fontFamily:"monospace",boxSizing:"border-box"}}/>
          <datalist id={`cv-${activeCell.key}`}>{sugg.map(v=><option key={v} value={v}/>)}</datalist>
          <div style={{marginTop:10,maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)"}}>
            {sugg.length===0?<div style={{padding:"10px 12px",fontSize:11,color:"var(--text-secondary)"}}>{colValCache[activeCell.param]===undefined?"로딩…":"suggestion 없음"}</div>
             :sugg.slice(0,100).map((v,i)=><div key={i} onClick={()=>commit(v)} style={{padding:"6px 10px",fontSize:11,fontFamily:"monospace",cursor:"pointer",borderBottom:i<sugg.length-1?"1px solid var(--border)":"none"}} onMouseEnter={e=>e.currentTarget.style.background="var(--accent-glow)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{v}</div>)}
          </div>
          {sugg.length>0&&<div style={{fontSize:10,color:"var(--text-secondary)",marginTop:6}}>{sugg.length} 개 (전체 데이터셋 unique + plan 포함)</div>}
          <div style={{display:"flex",gap:8,marginTop:12}}>
            <button onClick={()=>commit(activeCell.value)} style={{flex:1,padding:"8px 12px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer",fontSize:12}}>Apply</button>
            <button onClick={()=>setActiveCell(null)} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer",fontSize:12}}>Cancel</button>
          </div>
        </div>
      </div>;})()}
    {showConfirm&&<div style={{position:"fixed",inset:0,zIndex:9999,background:"rgba(0,0,0,0.6)",display:"flex",alignItems:"center",justifyContent:"center"}} onClick={()=>setShowConfirm(false)}>
      <div onClick={e=>e.stopPropagation()} style={{background:"var(--bg-secondary)",borderRadius:12,padding:24,width:400,border:"1px solid var(--border)",maxHeight:"80vh",overflow:"auto"}}>
        <div style={{fontSize:16,fontWeight:700,marginBottom:12}}>Confirm Changes</div>
        <div style={{fontSize:13,color:"var(--text-secondary)",marginBottom:16}}>{Object.keys(pendingPlans).length} cells will be updated</div>
        {Object.entries(pendingPlans).map(([k,v])=>(<div key={k} style={{fontSize:11,padding:"4px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between"}}><span style={{fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:250,overflow:"hidden",textOverflow:"ellipsis"}}>{k.split("|").pop()}</span><span style={{color:"#f97316",fontWeight:600}}>{v}</span></div>))}
        <div style={{display:"flex",gap:8,marginTop:16}}>
          <button onClick={savePlans} style={{flex:1,padding:10,borderRadius:6,border:"none",background:"#22c55e",color:"#fff",fontWeight:600,cursor:"pointer"}}>Confirm</button>
          <button onClick={()=>setShowConfirm(false)} style={{padding:"10px 20px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>Cancel</button>
        </div></div></div>}

    {/* v8.4.9-b: Notes 드로어 — 오른쪽 side drawer. wafer 태그 / param 메모 통합 뷰. */}
    {notesOpen && (()=>{
      const filtered=(!noteFilter)?notes
        :noteFilter.scope==="wafer"?notes.filter(n=>n.key===noteFilter.key)
        :noteFilter.scope==="cell"?notes.filter(n=>n.scope==="param"&&n.key===`${selProd}__${lotId}__W${noteFilter.wafer_id}__${noteFilter.param}`)
        :notes.filter(n=>n.scope==="param"&&n.key.endsWith(`__${noteFilter.param}`));
      // 헤더 라벨
      const title=!noteFilter?"노트 (전체)"
        :noteFilter.scope==="wafer"?`wafer #${noteFilter.key.split("__W").pop()} 태그`
        :noteFilter.scope==="cell"?`W${noteFilter.wafer_id} × ${noteFilter.param} 메모`
        :`${noteFilter.param} 메모 (lot ${lotId})`;
      return(<div style={{position:"fixed",top:0,right:0,bottom:0,width:420,background:"var(--bg-secondary)",borderLeft:"1px solid var(--border)",zIndex:2000,display:"flex",flexDirection:"column",boxShadow:"-4px 0 16px rgba(0,0,0,0.35)"}}>
        <div style={{padding:"12px 16px",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <div style={{fontSize:13,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>📝 {title}</div>
          <span onClick={()=>{setNotesOpen(false);setNoteDraft("");setNoteFilter(null);setNoteDraftScope(null);}} style={{cursor:"pointer",fontSize:18,color:"var(--text-secondary)"}}>✕</span>
        </div>
        {!noteFilter&&<div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)",display:"flex",gap:6,fontSize:10,color:"var(--text-secondary)"}}>
          <span>전체 {notes.length}개 · wafer 태그 {notes.filter(n=>n.scope==="wafer").length} · param 메모 {notes.filter(n=>n.scope==="param").length}</span>
        </div>}
        <div style={{flex:1,overflow:"auto",padding:"10px 16px",display:"flex",flexDirection:"column",gap:8}}>
          {filtered.length===0&&<div style={{padding:24,textAlign:"center",color:"var(--text-secondary)",fontSize:11}}>기록된 노트 없음</div>}
          {filtered.map(n=>{
            // key 파싱: {product}__{root}__W{wid}  or  ...__W{wid}__{param}
            const parts=(n.key||"").split("__");
            const wid=(parts[2]||"").replace(/^W/,"");
            const param=n.scope==="param"?parts[3]||"":"";
            return(<div key={n.id} style={{padding:"8px 10px",borderRadius:6,background:"var(--bg-card)",border:"1px solid var(--border)"}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:4,gap:6}}>
                <span style={{fontSize:9,fontWeight:700,padding:"1px 6px",borderRadius:8,background:n.scope==="wafer"?"#3b82f6":"#8b5cf6",color:"#fff"}}>{n.scope==="wafer"?`🏷 W${wid}`:`💬 ${param}`}</span>
                <span style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace"}}>{n.username} · {(n.created_at||"").replace("T"," ").slice(0,16)}</span>
              </div>
              <div style={{fontSize:12,whiteSpace:"pre-wrap",lineHeight:1.5}}>{n.text}</div>
              {(n.username===(user?.username||"")||isAdmin)&&<div style={{marginTop:4,textAlign:"right"}}>
                <span onClick={()=>deleteNote(n.id)} style={{cursor:"pointer",fontSize:9,color:"#ef4444"}}>삭제</span>
              </div>}
            </div>);
          })}
        </div>
        {noteDraftScope&&<div style={{padding:"10px 16px",borderTop:"1px solid var(--border)",display:"flex",flexDirection:"column",gap:6}}>
          <div style={{fontSize:10,color:"var(--text-secondary)"}}>
            대상:{" "}<span style={{color:noteDraftScope.scope==="wafer"?"#3b82f6":"#8b5cf6",fontWeight:700}}>{noteDraftScope.scope==="wafer"?`🏷 W${noteDraftScope.wafer_id}`:`💬 ${noteDraftScope.param}`}</span>
            {noteDraftScope.scope==="param"&&<span style={{marginLeft:6}}>wafer:
              <input value={noteDraftScope.wafer_id} onChange={e=>setNoteDraftScope({...noteDraftScope,wafer_id:e.target.value})} placeholder="wafer_id" style={{marginLeft:4,width:60,padding:"2px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:10}}/>
            </span>}
          </div>
          <textarea value={noteDraft} onChange={e=>setNoteDraft(e.target.value)} placeholder="새 노트 내용…" rows={3}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,resize:"vertical",fontFamily:"inherit"}}/>
          <div style={{display:"flex",gap:6,justifyContent:"flex-end"}}>
            <button onClick={addNote} disabled={!noteDraft.trim()||(noteDraftScope.scope==="param"&&!noteDraftScope.wafer_id)} style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,fontWeight:600,cursor:(noteDraft.trim()&&(noteDraftScope.scope!=="param"||noteDraftScope.wafer_id))?"pointer":"not-allowed",opacity:(noteDraft.trim()&&(noteDraftScope.scope!=="param"||noteDraftScope.wafer_id))?1:0.5}}>저장</button>
          </div>
        </div>}
        {!noteDraftScope&&<div style={{padding:"10px 16px",borderTop:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)"}}>
          상단 wafer 헤더 또는 좌측 parameter 행의 💬 아이콘을 클릭해 노트를 추가하세요.
        </div>}
      </div>);
    })()}
  </div>);
}
