import { useState, useEffect, useRef } from "react";
import Loading from "../components/Loading";
import { sf, dl } from "../lib/api";
import { statusPalette, chartPalette } from "../components/UXKit";
const API="/api/splittable";
const OK = statusPalette.ok;
const WARN = statusPalette.warn;
const BAD = statusPalette.bad;
const INFO = statusPalette.info;
const WHITE = "var(--bg-secondary)";
const GRID_BORDER = "rgba(85,85,85,0.95)";
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

export default function My_SplitTable({user}){
  const normFabSource=(v)=>{
    let s=String(v||"").trim().replaceAll("\\","/");
    if(!s) return "";
    if(s.toLowerCase().startsWith("db/")) s=s.slice(3);
    else if(s.toLowerCase().startsWith("base/")) s=s.slice(5);
    while(s.startsWith("/")) s=s.slice(1);
    return s;
  };
  const[products,setProducts]=useState([]);const[selProd,setSelProd]=useState("");
  const[lotId,setLotId]=useState("");const[waferIds,setWaferIds]=useState("");
  const[lotSuggestions,setLotSuggestions]=useState([]);const[showLotDrop,setShowLotDrop]=useState(false);const[lotFilter,setLotFilter]=useState("");
  // v8.4.3: fab_lot_id 검색도 지원 — root_lot_id 대체 키로 사용 가능.
  const[fabLotId,setFabLotId]=useState("");const[fabSuggestions,setFabSuggestions]=useState([]);const[showFabDrop,setShowFabDrop]=useState(false);
  const[prefixes,setPrefixes]=useState([]);const[selPrefixes,setSelPrefixes]=useState(["KNOB"]);
  const[customs,setCustoms]=useState([]);const[selCustom,setSelCustom]=useState("");const[isCustomMode,setIsCustomMode]=useState(false);
  const[viewMode,setViewMode]=useState("all");
  const[showParamMeta,setShowParamMeta]=useState(true);
  const[showLineageSummary,setShowLineageSummary]=useState(false);
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
  // v8.8.13: 노트 drawer 내부 검색 (wafer id / param 이름 / text 부분일치)
  const[noteSearch,setNoteSearch]=useState("");
  const[tab,setTab]=useState("view");const[history,setHistory]=useState([]);
  const[opHistory,setOpHistory]=useState([]);
  const[histMode,setHistMode]=useState("lot_final");const[histFinal,setHistFinal]=useState({final:[],drift:[],drift_count:0,total_cells:0});
  const[colSearch,setColSearch]=useState("");const[customCols,setCustomCols]=useState([]);const[customName,setCustomName]=useState("");
  const[showSettings,setShowSettings]=useState(false);const[settingsTab,setSettingsTab]=useState("basic");const[newPrefix,setNewPrefix]=useState("");
  const[precision,setPrecision]=useState({});const[precisionDraft,setPrecisionDraft]=useState({});
  const[enabledSources,setEnabledSources]=useState(null); // null = loading, Set of product names
  // v8.4.4: product 별 lot_id 컬럼 override (soft-landing)
  const[lotOverrides,setLotOverrides]=useState({});
  const[fabRoots,setFabRoots]=useState([]);
  const[overridePreview,setOverridePreview]=useState(null);
  const[overridePreviewLoading,setOverridePreviewLoading]=useState(false);
  // v8.4.4: fab_source 후보 (FileBrowser/Dashboard 와 동일 source 리스트)
  const[fabSourceOptions,setFabSourceOptions]=useState([]);
  // v8.7.8: fab_source 후보 = DB 상위폴더 (FAB/INLINE/ET/EDS) + Base 단일파일 + DB 제품 디렉토리 + TableMap.
  // v8.8.5: fab_source = DB 에서 고르는 값. ML_TABLE_*.parquet(모 테이블) 은 후보에서 제외.
  //   옵션 구성:
  //     - (자동) 옵션: 빈값 — ML_TABLE_<PROD> 에서 PROD 파생 후 1.RAWDATA_DB/<PROD> 자동 매칭.
  //     - 제품폴더 옵션: `<1.RAWDATA_DB_xxx>/<PROD>` — `/fab-roots` 가 반환한 각 root 의 products 를 펼침.
  //     - TableMap 옵션: `tablemap:<id>` — 사용자 정의.
  //   v8.8.21: `root:<name>` 옵션 제거 — 제품 스코프를 넘어 섞인 데이터로 join 되는 footgun.
  useEffect(()=>{
    const out=[{value:"",label:"(자동 매칭) ML_TABLE_PRODA → 1.RAWDATA_DB/PRODA",source_type:"auto"}];
    const fabRootsReq=sf(API+"/fab-roots").then(d=>{
      const roots = d.roots || [];
      setFabRoots(roots);
      for(const r of roots){
        for(const p of r.products){
          out.push({value:`${r.name}/${p}`,label:`[DB] ${r.name}/${p}`,source_type:"db_product"});
        }
      }
    }).catch(()=>{setFabRoots([]);});
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
  const[mlMatch,setMlMatch]=useState({pro:"",matches:[],auto_path:"",effective_fab_source:"",manual_override:false,override:null});
  useEffect(()=>{if(!selProd){setMlMatch({pro:"",matches:[],auto_path:"",effective_fab_source:"",manual_override:false,override:null});return;}
    sf(API+"/ml-table-match?product="+encodeURIComponent(selProd))
      .then(d=>setMlMatch({pro:d.derived_product||"",matches:d.matches||[],auto_path:d.auto_path||"",effective_fab_source:d.effective_fab_source||"",manual_override:!!d.manual_override,override:d.override||null}))
      .catch(()=>setMlMatch({pro:"",matches:[],auto_path:"",effective_fab_source:"",manual_override:false,override:null}));
  },[selProd,lotOverrides]);
  const deriveProductFolder=(prod)=>{
    const p=String(prod||"").trim();
    if(!p) return "";
    if(p.startsWith("ML_TABLE_")) return p.slice("ML_TABLE_".length).trim();
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
  const isAdmin=user?.role==="admin";
  const lotRef=useRef(null);
  const settingsLotLinkRef=useRef(null);
  const scrollToSettingsLotLink=()=>settingsLotLinkRef.current?.scrollIntoView({behavior:"smooth",block:"start"});
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
  const normalizeOverrideConfig=(raw)=>{
    const next={...(raw||{})};
    Object.keys(next).forEach((k)=>{ if(next[k]) next[k]={...next[k], fab_source:normFabSource(next[k].fab_source)}; });
    return next;
  };
  const loadSourceConfig=()=>sf(API+"/source-config").then(d=>{
    if(d.enabled?.length)setEnabledSources(new Set(d.enabled));
    if(d.lot_overrides)setLotOverrides(normalizeOverrideConfig(d.lot_overrides));
    return d;
  }).catch(()=>({}));
  const reloadMlMatch=()=>{if(!selProd)return Promise.resolve();
    return sf(API+"/ml-table-match?product="+encodeURIComponent(selProd))
      .then(d=>setMlMatch({pro:d.derived_product||"",matches:d.matches||[],auto_path:d.auto_path||"",effective_fab_source:d.effective_fab_source||"",manual_override:!!d.manual_override,override:d.override||null}))
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
  useEffect(()=>{
    Promise.all([sf(API+"/products").catch(()=>({products:[]})),sf(API+"/source-config").catch(()=>({enabled:[]})),sf(API+"/prefixes").catch(()=>({prefixes:[]}))])
      .then(([prodRes,srcRes,prefRes])=>{
        const prods=prodRes.products||[];setProducts(prods);
        const enabled=srcRes.enabled?.length?new Set(srcRes.enabled):null;
        setEnabledSources(enabled);
        if(srcRes.lot_overrides) setLotOverrides(normalizeOverrideConfig(srcRes.lot_overrides));
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
  useEffect(()=>{
    if(!selProd){setLotSuggestions([]);return;}
    const prefix=(lotId||"").trim();
    let url=API+"/lot-candidates?product="+encodeURIComponent(selProd)+"&col=root_lot_id&limit=500";
    if(prefix) url+="&prefix="+encodeURIComponent(prefix);
    sf(url)
      .then(d=>setLotSuggestions(d.candidates||[]))
      .catch(()=>{
        if(!prefix) sf(API+"/lot-ids?product="+encodeURIComponent(selProd)).then(d=>setLotSuggestions(d.lot_ids||[])).catch(()=>{});
      });
  },[selProd,lotId]);
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
    if(!selProd){setProductSchema([]);setOverrideCols([]);return;}
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
  const buildLineageSummary=(rows)=>{
    const out=[];
    (rows||[]).forEach((row)=>{
      const param=String(row?._param||"");
      if(!param) return;
      const km=knobLookup(param);
      if(Array.isArray(km?.groups)&&km.groups.length){
        km.groups.forEach((g,gi)=>out.push({key:`${param}-k-${gi}`,parameter:param,function_step:g.func_step||"",step_ids:Array.isArray(g.step_ids)?g.step_ids:[]}));
        return;
      }
      const vm=vmLookup(param)||{};
      if(param.startsWith("VM_")&&(vm.step_id||vm.function_step||Array.isArray(vm.groups))){
        if(Array.isArray(vm.groups)&&vm.groups.length){
          vm.groups.forEach((g,gi)=>out.push({key:`${param}-v-${gi}`,parameter:param,function_step:g.function_step||vm.function_step||"",step_ids:Array.isArray(g.step_ids)&&g.step_ids.length?g.step_ids:(g.step_id?[g.step_id]:(Array.isArray(vm.step_ids)?vm.step_ids:(vm.step_id?[vm.step_id]:[])))}));
        }else{
          out.push({key:`${param}-v`,parameter:param,function_step:vm.function_step||"",step_ids:Array.isArray(vm.step_ids)?vm.step_ids:(vm.step_id?[vm.step_id]:[])});
        }
        return;
      }
      const im=inlineLookup(param)||{};
      if(param.startsWith("INLINE_")&&(im.step_id||im.function_step||Array.isArray(im.groups))){
        if(Array.isArray(im.groups)&&im.groups.length){
          im.groups.forEach((g,gi)=>out.push({key:`${param}-i-${gi}`,parameter:param,function_step:g.function_step||im.function_step||"",step_ids:Array.isArray(g.step_ids)&&g.step_ids.length?g.step_ids:(g.step_id?[g.step_id]:(Array.isArray(im.step_ids)?im.step_ids:(im.step_id?[im.step_id]:[])))}));
        }else{
          out.push({key:`${param}-i`,parameter:param,function_step:im.function_step||"",step_ids:Array.isArray(im.step_ids)?im.step_ids:(im.step_id?[im.step_id]:[])});
        }
      }
    });
    return out;
  };
  // v8.8.10: Rulebook 컬럼 매핑 schema — admin 이 역할→실제컬럼명 조정 가능.
  const[rbSchema,setRbSchema]=useState({schema:{},defaults:{}});
  const[rbEditKind,setRbEditKind]=useState(null);   // "knob_ppid"|"step_matching"|"inline_matching"|"vm_matching"|null
  const[rbDraftMap,setRbDraftMap]=useState({});
  const reloadRbSchema=()=>sf(API+"/rulebook/schema").then(d=>setRbSchema({schema:d.schema||{},defaults:d.defaults||{}})).catch(()=>{});
  // v8.8.13-fix: 이전에는 `useEffect(reloadRbSchema,[])` 였는데 reloadRbSchema 가 Promise 를 반환하는 함수라
  // React 가 그 Promise 를 cleanup 로 저장 → unmount 시 Promise() 호출 → "n is not a function" 흰 화면 튕김.
  // 화살표로 감싸 void 반환으로 변경.
  useEffect(()=>{reloadRbSchema();},[]);
  const openSchemaEditor=(kind)=>{setRbEditKind(kind);setRbDraftMap({...(rbSchema.schema?.[kind]||rbSchema.defaults?.[kind]||{})});};
  const saveSchemaEdit=()=>{if(!rbEditKind)return;
    sf(API+"/rulebook/schema/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({kind:rbEditKind,mapping:rbDraftMap,username:user?.username||""})})
      .then(()=>{setRbEditKind(null);reloadRbSchema();loadView&&loadView();})
      .catch(e=>alert("저장 실패: "+e.message));
  };
  // v8.8.15: Rulebook 행 CRUD modal — admin 이 knob_ppid/step_matching/inline_matching/vm_matching 의
  //   제품별 행을 직접 추가/수정/삭제. BE 는 /rulebook (GET) + /rulebook/save (POST product-scoped).
  const[rbRowKind,setRbRowKind]=useState(null);
  const[rbRowCols,setRbRowCols]=useState([]);
  const[rbRowReq,setRbRowReq]=useState([]);
  const[rbRowRows,setRbRowRows]=useState([]);
  const[rbRowSaving,setRbRowSaving]=useState(false);
  const openRowEditor=(kind)=>{
    if(!selProd){alert("먼저 제품을 선택하세요.");return;}
    setRbRowKind(kind);setRbRowRows([]);setRbRowCols([]);setRbRowReq([]);
    sf(API+"/rulebook?kind="+encodeURIComponent(kind)+"&product="+encodeURIComponent(selProd))
      .then(d=>{
        setRbRowCols(d.columns||[]);
        // required 는 FE 스키마 (product는 자동 스코프)
        const reqMap={knob_ppid:["feature_name","function_step"],step_matching:["step_id","func_step"],inline_matching:["step_id","item_id"],vm_matching:["feature_name","step_id"]};
        setRbRowReq(reqMap[kind]||[]);
        // 현재 제품 행만 골라 편집 대상으로. 공용(product 빈값) 행은 read-only 프리뷰 뒤에.
        const prodRows=(d.rows||[]).filter(r=>(r.product||"")===selProd).map(r=>({...r}));
        setRbRowRows(prodRows);
      })
      .catch(e=>{alert("Rulebook 로드 실패: "+e.message);setRbRowKind(null);});
  };
  const rbAddRow=()=>{const blank={};(rbRowCols||[]).forEach(c=>{blank[c]=c==="product"?(selProd||""):"";});setRbRowRows(rs=>[...rs,blank]);};
  const rbUpdateCell=(i,col,v)=>{setRbRowRows(rs=>rs.map((r,idx)=>idx===i?{...r,[col]:v}:r));};
  const rbDelRow=(i)=>{setRbRowRows(rs=>rs.filter((_,idx)=>idx!==i));};
  const rbSaveRows=()=>{if(!rbRowKind||!selProd)return;
    // validate required
    const bad=rbRowRows.findIndex(r=>(rbRowReq||[]).some(c=>!String(r[c]||"").trim()));
    if(bad>=0){alert(`행 ${bad+1}: 필수 컬럼 누락 (${rbRowReq.join(", ")})`);return;}
    setRbRowSaving(true);
    sf(API+"/rulebook/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({kind:rbRowKind,rows:rbRowRows,product:selProd,username:user?.username||""})})
      .then(()=>{setRbRowKind(null);setRbRowRows([]);
        // 관련 메타 재로드
        if(rbRowKind==="knob_ppid"||rbRowKind==="step_matching"){sf(API+"/knob-meta?product="+encodeURIComponent(selProd)).then(d=>setKnobMeta(d.features||{})).catch(()=>{});}
        if(rbRowKind==="vm_matching"){sf(API+"/vm-meta?product="+encodeURIComponent(selProd)).then(d=>setVmMeta(d.items||{})).catch(()=>{});}
        loadView&&loadView();
      })
      .catch(e=>alert("저장 실패: "+e.message))
      .finally(()=>setRbRowSaving(false));
  };
  const parseCsvTokens=(value)=>String(value||"").split(",").map(s=>s.trim()).filter(Boolean);
  // fab_lot_id 후보도 fetch (lot-candidates 엔드포인트 사용)
  // v9.0.2: fabLotId 입력값도 서버 prefix 로 전송 — 초기 500개 밖의 fab_lot_id 도 검색 가능.
  // v9.0.1: lotId 가 비어있지 않으면 root_lot_id scope 전송 — BE 가 데이터-중심 join
  //   (root_lot_id == lotId 인 row 의 fab_lot_id) 으로 매칭, 0건이면 starts_with → 전체 폴백.
  //   기존 'lotId.length===5' 분기는 시드 데이터에서 root/fab 앞 5자가 다른 케이스를 못 잡았음.
  useEffect(()=>{
    if(!selProd) return;
    let url=API+"/lot-candidates?product="+encodeURIComponent(selProd)+"&col=fab_lot_id&limit=500";
    const _r=(lotId||"").trim();
    const _f=(fabLotId||"").trim();
    if(_r) url+="&root_lot_id="+encodeURIComponent(_r);
    if(_f) url+="&prefix="+encodeURIComponent(_f);
    sf(url).then(d=>setFabSuggestions(d.candidates||[])).catch(()=>{});
  },[selProd,lotId,fabLotId]);
  useEffect(()=>{const h=e=>{if(lotRef.current&&!lotRef.current.contains(e.target))setShowLotDrop(false);};document.addEventListener("mousedown",h);return()=>document.removeEventListener("mousedown",h);},[]);

  const prefixParam=isCustomMode?"":selPrefixes.join(",");
  // diff 모드는 클라이언트에서 즉시 필터 → 항상 "all" 로 fetch
  // v9.0.3: 한 root_lot_id 아래 여러 fab_lot_id 가 정상이다.
  // FAB 공정 진행 중 fab_lot_id 가 바뀔 수 있으므로 앞 5자 일치 검증으로 검색을 막지 않는다.
  const loadView=()=>{if(!selProd||(!lotId.trim()&&!fabLotId.trim()))return;setLoading(true);
    let url=API+"/view?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+"&view_mode=all&history_mode=all";
    if(fabLotId.trim())url+="&fab_lot_id="+encodeURIComponent(fabLotId.trim());
    // v8.8.33: Save 없이 체크만 한 ad-hoc customCols 우선 — set name 은 보조.
    if(isCustomMode&&customCols.length>0)url+="&custom_cols="+encodeURIComponent(customCols.join(","));
    else if(isCustomMode&&selCustom)url+="&custom_name="+encodeURIComponent(selCustom);
    sf(url).then(d=>{
      setData(d);
      if(d.precision)setPrecision(d.precision);
      // v9.0.1: 응답에 동봉된 같은 root 의 fab_lot_id 들로 콤보박스 자동 채움 —
      //   별도 lot-candidates 호출 없이 즉시 보임. 빈 배열이면 기존 fabSuggestions 유지.
      if(Array.isArray(d.available_fab_lots)&&d.available_fab_lots.length>0){
        setFabSuggestions(d.available_fab_lots);
      }
      setLoading(false);setPendingPlans({});reloadNotes();
    }).catch(e=>{alert(e.message);setLoading(false);});};
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
  const isLotHistoryMode=(mode)=>mode==="lot_all"||mode==="lot_final";
  const isFinalHistoryMode=(mode)=>mode==="lot_final"||mode==="all_final";
  const normalizeHistoryMode=(mode)=>{
    const next=mode||"lot_final";
    if(isLotHistoryMode(next)&&!lotId.trim()) return next==="lot_all"?"all":"all_final";
    return next;
  };
  const loadHistory=(mode)=>{const next=normalizeHistoryMode(mode);let url=API+"/history?product="+encodeURIComponent(selProd)+"&limit=5000";if(next==="lot_all"&&lotId.trim())url+="&root_lot_id="+encodeURIComponent(lotId.trim());sf(url).then(d=>setHistory(d.history||[]));};
  const loadHistoryFinal=(mode)=>{const next=normalizeHistoryMode(mode);let url=API+"/history/final?product="+encodeURIComponent(selProd);if(next==="lot_final"&&lotId.trim())url+="&root_lot_id="+encodeURIComponent(lotId.trim());sf(url).then(d=>setHistFinal({final:d.final||[],drift:d.drift||[],drift_count:d.drift_count||0,total_cells:d.total_cells||0}));};
  const loadHistoryByMode=(mode)=>{const next=normalizeHistoryMode(mode);setHistMode(next);if(isFinalHistoryMode(next))loadHistoryFinal(next);else loadHistory(next);if(isLotHistoryMode(next))loadOperationalHistory();else setOpHistory([]);};
  const loadOperationalHistory=()=>{if(!selProd||!lotId.trim()){setOpHistory([]);return;}
    let url=API+"/operational-history?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId.trim());
    if((waferIds||"").trim())url+="&wafer_ids="+encodeURIComponent(waferIds.trim());
    sf(url).then(d=>setOpHistory(d.items||[])).catch(()=>setOpHistory([]));};
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
  const selectCustomSet=(c)=>{
    // v8.8.33: 저장 set 에서 기본 식별자(root_lot_id/wafer_id/lot_id/fab_lot_id/product) 자동 제거 — 자동 첨부되는 컬럼.
    const _drop=new Set(["product","root_lot_id","wafer_id","lot_id","fab_lot_id"]);
    const cleaned=(c.columns||[]).filter(col=>!_drop.has(String(col).toLowerCase()));
    setSelCustom(c.name);setCustomCols(cleaned);setCustomName(c.name);
  };

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
  // plan/actual 이 다르면 mismatch 로 표시하고, actual 이 아직 없으면 plan-only 상태로 보여준다.
  const getCellPlanStyle=(cell)=>{if(!cell)return{};
    if(cell.plan&&cell.actual){
      if(String(cell.plan)===String(cell.actual))return{}; // match = normal (값이 같아서 별도 강조 불필요)
      return{borderLeft:"3px solid #ef4444",background:"#fef2f2"}; // MISMATCH = 빨강
    }
    if(cell.plan)return{borderLeft:"3px solid #f97316",fontStyle:"italic",fontWeight:700}; // plan-only: bg 는 getCellBg 가 plan 값 기준으로 처리
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
  const filteredCols=colSearch?allCols.filter(c=>c.toLowerCase().includes(colSearch.toLowerCase())):allCols.slice(0,100);
  // v8.8.16: CUSTOM 모드 전용 컬럼 풀 — productSchema (전체) + allCols (현재 lot) + customCols 합집합.
  //   lot 검색 전이라도 선택 가능하며, plan 전용 가상 컬럼(저장된 customCols) 도 보존.
  // v8.8.33: product/root_lot_id/wafer_id/lot_id/fab_lot_id 는 **항상** 자동 첨부되는 기본 식별자 —
  //   CUSTOM pool 에서 절대 노출 X (override 에서 왔든 아니든 동일). 사용자가 의미 있는 파라미터에만
  //   집중하도록 근본적으로 차단. 기존에 customCols 에 섞여있던 것도 로드 타임에 자동 제거.
  const _CUSTOM_HIDDEN_BASE = new Set(["product","root_lot_id","wafer_id","lot_id","fab_lot_id"]);
  const customPool=(()=>{const seen=new Set();const out=[];
    for(const c of [...productSchema,...allCols,...customCols,...overrideCols]){
      const lc = String(c).toLowerCase();
      if(_CUSTOM_HIDDEN_BASE.has(lc)) continue;
      if(!seen.has(c)){seen.add(c);out.push(c);}
    }return out;})();
  const filteredCustomCols=colSearch
    ?customPool.filter(c=>c.toLowerCase().includes(colSearch.toLowerCase()))
    :customPool;
  const filteredLots=lotFilter?lotSuggestions.filter(l=>l.toLowerCase().includes(lotFilter.toLowerCase())):lotSuggestions;
  const S={padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none"};
  const chipS=(active)=>({padding:"3px 8px",borderRadius:4,fontSize:10,cursor:"pointer",fontWeight:active?700:400,background:active?"var(--accent-glow)":"var(--bg-hover)",color:active?"var(--accent)":"var(--text-secondary)",border:active?"1px solid var(--accent)":"1px solid transparent"});

  return(<div style={{display:"flex",height:"calc(100vh - 48px)",background:"var(--bg-primary)",color:"var(--text-primary)"}}>
    {/* v8.4.9-c: 셀 hover 시 빈 💬+ 배지 페이드인 */}
    <style>{`.stm-cell:hover .stm-note-btn{opacity:1 !important;}`}</style>
    {/* Sidebar */}
    <div style={{width:250,minWidth:250,borderRight:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",flexDirection:"column",overflow:"auto",position:"relative"}}>
      <div style={{padding:"12px 14px",borderBottom:"1px solid var(--border)",fontSize:12,fontWeight:700,color:"var(--text-secondary)"}}>스플릿 테이블</div>
      <div style={{padding:"8px 12px"}}><div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>제품</div>
        <select value={selProd} onChange={e=>setSelProd(e.target.value)} style={{...S,width:"100%"}}>{visibleProducts.map(p=><option key={p.name} value={p.name}>{p.name}</option>)}</select></div>
      {/* Lot ID dropdown */}
      <div style={{padding:"4px 12px"}} ref={lotRef}>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>루트 Lot ID</div>
        <input value={lotId} onChange={e=>{setLotId(e.target.value);setLotFilter(e.target.value);setShowLotDrop(true);}}
          onFocus={()=>setShowLotDrop(true)} placeholder="입력 또는 선택"
          style={{...S,width:"100%"}} onKeyDown={e=>e.key==="Enter"&&(setShowLotDrop(false),doSearch())}/>
        {showLotDrop&&filteredLots.length>0&&<div style={{maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)",marginTop:2}}>
          {filteredLots.slice(0,50).map(l=><div key={l} onClick={()=>{setLotId(l);setShowLotDrop(false);}}
            style={{padding:"6px 10px",fontSize:11,cursor:"pointer",borderBottom:"1px solid var(--border)",color:"var(--text-primary)"}}
            onMouseEnter={e=>e.currentTarget.style.background="var(--bg-hover)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{l}</div>)}
        </div>}
      </div>
      {/* v8.4.3: fab_lot_id 검색 — root_lot_id 대신 FAB 쪽 ID 로 조회 */}
      <div style={{padding:"4px 12px"}}>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Fab Lot ID</div>
        <input value={fabLotId} onChange={e=>{setFabLotId(e.target.value);setShowFabDrop(true);}}
          onFocus={()=>setShowFabDrop(true)} onBlur={()=>setTimeout(()=>setShowFabDrop(false),150)}
          placeholder="fab_lot_id 입력" style={{...S,width:"100%"}} onKeyDown={e=>e.key==="Enter"&&(setShowFabDrop(false),doSearch())}/>
        {showFabDrop&&fabSuggestions.length>0&&(fabLotId?fabSuggestions.filter(f=>f.toLowerCase().includes(fabLotId.toLowerCase())):fabSuggestions).length>0&&
          <div style={{maxHeight:160,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)",marginTop:2}}>
            {(fabLotId?fabSuggestions.filter(f=>f.toLowerCase().includes(fabLotId.toLowerCase())):fabSuggestions).slice(0,50).map(f=><div key={f} onMouseDown={()=>{setFabLotId(f);setShowFabDrop(false);}}
              style={{padding:"6px 10px",fontSize:11,cursor:"pointer",borderBottom:"1px solid var(--border)",color:"var(--text-primary)"}}
              onMouseEnter={e=>e.currentTarget.style.background="var(--bg-hover)"} onMouseLeave={e=>e.currentTarget.style.background="transparent"}>{f}</div>)}
          </div>}
      </div>
      <div style={{padding:"4px 12px"}}><div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>Wafer ID</div>
        <input value={waferIds} onChange={e=>setWaferIds(e.target.value)} placeholder="예: 1,2,3" style={{...S,width:"100%"}} onKeyDown={e=>e.key==="Enter"&&doSearch()}/></div>
      <div style={{padding:"6px 12px"}}>
        <button onClick={doSearch} title="검색"
          style={{width:"100%",padding:"7px 0",borderRadius:5,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:12,fontWeight:600,cursor:"pointer",opacity:1}}>
          검색
        </button>
      </div>
      {/* Prefix multi-select */}
      <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)"}}><div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>컬럼 그룹</div>
        <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
          {prefixes.map(p=><span key={p} onClick={()=>togglePrefix(p)} style={chipS(selPrefixes.includes(p)&&!isCustomMode)}>{p}</span>)}
          <span onClick={()=>{setIsCustomMode(true);setSelPrefixes([]);}} style={chipS(isCustomMode)}>CUSTOM</span>
        </div></div>
      {/* Custom mode */}
      {isCustomMode&&<div style={{padding:"8px 12px",borderTop:"1px solid var(--border)",flex:1,overflow:"auto"}}>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>커스텀 세트</div>
        {customs.map(c=><div key={c.name} style={{display:"flex",alignItems:"center",gap:4,padding:"3px 6px",borderRadius:4,marginBottom:2,background:selCustom===c.name?"var(--accent-glow)":"transparent",cursor:"pointer"}}
          onClick={()=>selectCustomSet(c)}>
          <span style={{flex:1,fontSize:11,color:selCustom===c.name?"var(--accent)":"var(--text-primary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{c.name}</span>
          <span style={{fontSize:8,color:"var(--text-secondary)",flexShrink:0}}>{c.updated?.slice(5,10)||c.created?.slice(5,10)||""}</span>
          {(c.username===user?.username||isAdmin)&&<span onClick={e=>{e.stopPropagation();deleteCustom(c.name);}} style={{fontSize:9,color:"rgba(239,68,68,0.95)",cursor:"pointer",flexShrink:0}} title="Delete">✕</span>}
        </div>)}
        {/* v8.8.16: 선택된 Set 의 컬럼을 pill 로 현재 선택 상태에 노출 — 어느 컬럼이 포함됐는지 한눈에. */}
        {selCustom&&customCols.length>0&&<div style={{marginTop:6,padding:"5px 6px",borderRadius:4,background:"var(--bg-card)",border:"1px dashed var(--border)"}}>
          <div style={{fontSize:9,color:"var(--text-secondary)",marginBottom:3,fontWeight:600}}>'{selCustom}' 선택 컬럼 ({customCols.length})</div>
          <div style={{display:"flex",flexWrap:"wrap",gap:3}}>
            {customCols.map(c=><span key={c} title={c}
              style={{display:"inline-flex",alignItems:"center",gap:2,padding:"1px 5px",borderRadius:3,fontSize:9,background:"var(--accent-glow)",color:"var(--accent)",fontFamily:"monospace"}}>
              {c}<span onClick={()=>setCustomCols(customCols.filter(x=>x!==c))} style={{cursor:"pointer",fontSize:10,lineHeight:1,marginLeft:2,color:"rgba(239,68,68,0.95)"}} title="제거">×</span>
            </span>)}
          </div>
        </div>}
        <div style={{marginTop:6,fontSize:10,color:"var(--text-secondary)"}}>생성 / 편집</div>
        <input value={colSearch} onChange={e=>setColSearch(e.target.value)} placeholder="컬럼 검색" style={{...S,width:"100%",fontSize:10,marginBottom:4,marginTop:4}}/>
        {/* v8.8.16: 전체 체크/제거 + 개수 표시 */}
        <div style={{display:"flex",gap:4,marginBottom:4,fontSize:9,alignItems:"center"}}>
          <button onClick={()=>{const all=Array.from(new Set([...customCols,...filteredCustomCols]));setCustomCols(all);}}
            style={{padding:"2px 8px",borderRadius:3,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:9,cursor:"pointer",fontWeight:600}}>
            ✓ 전체 체크{colSearch?` (${filteredCustomCols.length})`:""}
          </button>
          <button onClick={()=>{if(colSearch){const fs=new Set(filteredCustomCols);setCustomCols(customCols.filter(c=>!fs.has(c)));}else setCustomCols([]);}}
            style={{padding:"2px 8px",borderRadius:3,border:"1px solid #ef4444",background:"transparent",color:"rgba(239,68,68,0.95)",fontSize:9,cursor:"pointer",fontWeight:600}}>
            ✕ 전체 제거
          </button>
          <span style={{marginLeft:"auto",color:"var(--text-secondary)",fontSize:9}}>{customCols.length}/{customPool.length} 선택</span>
        </div>
        <div style={{maxHeight:120,overflow:"auto"}}>
          {filteredCustomCols.map(c=><div key={c} onClick={()=>{if(!customCols.includes(c))setCustomCols([...customCols,c]);else setCustomCols(customCols.filter(x=>x!==c));}} style={{fontSize:10,padding:"2px 6px",cursor:"pointer",color:customCols.includes(c)?"var(--accent)":"var(--text-secondary)"}}>{customCols.includes(c)?"✓ ":""}{c}</div>)}
          {filteredCustomCols.length===0&&<div style={{fontSize:10,color:"var(--text-secondary)",padding:6,fontStyle:"italic"}}>
            {productSchema.length===0?"제품 스키마 로딩 중...":"검색 결과 없음"}
          </div>}
        </div>
        {customCols.length>0&&<div style={{marginTop:4}}>
          <div style={{fontSize:9,color:"var(--text-secondary)"}}>{customCols.length}개 선택</div>
          <div style={{display:"flex",gap:4,marginTop:4}}>
            <input value={customName} onChange={e=>setCustomName(e.target.value)} placeholder="세트명" style={{...S,flex:1,fontSize:10}}/>
            <button onClick={saveCustom} style={{padding:"3px 8px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:10,cursor:"pointer"}}>저장</button>
          </div>
          <div style={{fontSize:8,color:"var(--text-secondary)",marginTop:2}}>같은 이름은 덮어쓰기</div>
        </div>}
      </div>}
      {/* Settings gear */}
      {isAdmin&&<div>
        <div onClick={()=>setShowSettings(!showSettings)} style={{position:"fixed",bottom:16,left:16,width:40,height:40,borderRadius:"50%",background:"var(--bg-secondary)",border:"1px solid var(--border)",display:"flex",alignItems:"center",justifyContent:"center",cursor:"pointer",zIndex:97,boxShadow:"0 2px 8px rgba(0,0,0,0.3)",fontSize:18}} title="Admin settings">⚙️</div>
        {showSettings&&<><div style={{position:"fixed",inset:0,zIndex:98}} onClick={()=>setShowSettings(false)}/><div style={{position:"fixed",left:"50%",top:"50%",transform:"translate(-50%, -50%)",background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:12,padding:16,width:"min(920px, calc(100vw - 32px))",maxHeight:"84vh",overflow:"auto",zIndex:99,boxShadow:"0 16px 48px rgba(0,0,0,0.55)"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
            <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>Split Table 설정</span>
            <span onClick={()=>setShowSettings(false)} style={{cursor:"pointer",color:"var(--text-secondary)",fontSize:16}}>✕</span>
          </div>
          <div style={{display:"flex",gap:4,marginBottom:12,borderBottom:"1px solid var(--border)"}}>
            <span onClick={()=>setSettingsTab("basic")} style={{padding:"5px 10px",fontSize:11,cursor:"pointer",fontWeight:settingsTab==="basic"?700:500,borderBottom:settingsTab==="basic"?"2px solid var(--accent)":"2px solid transparent",color:settingsTab==="basic"?"var(--accent)":"var(--text-secondary)"}}>기본</span>
            <span onClick={()=>setSettingsTab("advanced")} style={{padding:"5px 10px",fontSize:11,cursor:"pointer",fontWeight:settingsTab==="advanced"?700:500,borderBottom:settingsTab==="advanced"?"2px solid var(--accent)":"2px solid transparent",color:settingsTab==="advanced"?"var(--accent)":"var(--text-secondary)"}}>고급</span>
          </div>
          {settingsTab==="basic"&&<div style={{display:"grid",gap:10,marginBottom:10}}>
            <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
              <div style={{fontSize:11,fontWeight:700,color:"var(--text-primary)",marginBottom:6}}>기본 표시 설정</div>
              <div style={{display:"grid",gap:8,fontSize:10,color:"var(--text-secondary)",lineHeight:1.55}}>
                <div>사용자에게 보이는 기본 표시 형식을 조정합니다.</div>
                <div>적용 공정 정보, 하단 적용 요약, 표시 자리수는 화면 상단 토글과 기본 설정으로 조정할 수 있습니다.</div>
                <div>데이터 연결 방식, 원천 컬럼 매칭, 규칙 편집은 <b>고급</b> 탭에서 관리합니다.</div>
              </div>
            </div>
            <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
              <div style={{fontSize:11,fontWeight:700,color:"var(--text-primary)",marginBottom:6}}>용어 안내</div>
              <div style={{fontSize:10,color:"var(--text-secondary)",lineHeight:1.6}}>
                내부 용어는 [docs/splittable_terms_ko.md] 에 정리되어 있습니다. 일반 사용자는 화면에서 technical 용어 대신 더 쉬운 표현을 우선 보게 됩니다.
              </div>
            </div>
          </div>}
          {settingsTab==="advanced"&&<>
          <div style={{display:"grid",gap:8,marginBottom:12,padding:"10px 12px",borderRadius:10,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
            <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
              <div style={{fontSize:11,fontWeight:700,color:"var(--accent)"}}>설정 연결 흐름</div>
              <button onClick={scrollToSettingsLotLink}
                style={{marginLeft:"auto",padding:"4px 10px",borderRadius:999,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:10,fontWeight:700,cursor:"pointer"}}>
                Lot 컬럼 연결로 이동
              </button>
            </div>
            <div style={{fontSize:10,color:"var(--text-secondary)",lineHeight:1.6}}>
              제품 노출, Lot 컬럼 연결, 컬럼/공정 규칙만 관리합니다. 규칙 추가·수정은 각 섹션의 <b>편집</b> 버튼에서 처리합니다.
            </div>
          </div>
          {/* Source visibility checkboxes — Base 파일(ML_TABLE_ 등)만 표시 */}
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:6,fontWeight:600}}>사용자 표시 대상</div>
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
              사용자 노출 {enabledSources?[...enabledSources].filter(n=>allBaseNames.includes(n)).length:baseProds.length} / {baseProds.length} · 선택한 제품의 실제 DB 연결은 아래 Lot 컬럼 연결에서 조정합니다.
            </div>
          </>)})()}
          {/* Prefix management */}
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4,fontWeight:600}}>컬럼 그룹 관리</div>
          {prefixes.map(p=><div key={p} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"3px 0",fontSize:11}}>
            <span style={{fontFamily:"monospace"}}>{p}</span><span onClick={()=>removePrefix(p)} style={{color:"rgba(239,68,68,0.95)",cursor:"pointer",fontSize:10}}>✕</span>
          </div>)}
          <div style={{display:"flex",gap:4,marginTop:6}}>
            <input value={newPrefix} onChange={e=>setNewPrefix(e.target.value)} placeholder="새 그룹명" style={{...S,flex:1,fontSize:10}} onKeyDown={e=>e.key==="Enter"&&addPrefix()}/>
            <button onClick={addPrefix} style={{padding:"3px 8px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:10,cursor:"pointer"}}>+</button>
          </div>
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4,fontWeight:600,marginTop:10}}>표시 자리수</div>
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

          <div ref={settingsLotLinkRef} style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4,fontWeight:600,marginTop:10,scrollMarginTop:16}}>Lot 컬럼 연결 조정 ({selProd||"제품 선택 필요"})</div>
          <div style={{fontSize:9,color:"var(--text-secondary)",marginBottom:6}}>자동 매칭을 1순위로 사용하고, 안 맞을 때만 탐색기에 보이는 DB 경로로 수동 연결합니다.</div>
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
            const currentMeta=mlMatch.override||{};
            const currentMode=mlMatch.manual_override?"manual":"auto";
            const currentSource=mlMatch.effective_fab_source||"";
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
                alert("수동 연결은 DB 경로를 먼저 선택해야 합니다.");
                return;
              }
              if(draftOverrideMode==="auto"&&!autoFabSource){
                alert("자동 매칭 후보가 없습니다. 수동 연결로 DB 경로를 선택하세요.");
                return;
              }
              if(preview.error&&!previewApiMissing){alert("현재 연결 미리보기가 유효하지 않습니다. 자동 경로나 수동 DB 경로를 다시 확인하세요.");return;}
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
                alert("✔ 연결 저장됨. 다음 조회부터 바로 적용됩니다.");
              }catch(e){
                alert("저장 실패: "+(e?.message||e));
              }
            };
            const selectS={...S,width:"100%",fontSize:10,fontFamily:"monospace"};
            const currentPreviewLots=(currentMeta.sample_fab_values||[]).filter(Boolean);
            const draftPreviewLots=(preview.latest_fab_lot_ids||[]).filter(Boolean);
            const statusTone=currentMeta.error?"rgba(239,68,68,0.95)":currentMode==="manual"?"rgba(245,158,11,0.95)":"rgba(34,197,94,0.95)";
            const currentAliasPairs=Object.entries(currentMeta.column_aliases||{});
            const draftAliasPairs=Object.entries(aliasMap||{});
            return(<div style={{display:"grid",gap:10,padding:"12px 14px",borderRadius:10,background:"var(--bg-secondary)",border:"1px solid var(--border)",marginBottom:10}}>
              <div style={{display:"grid",gridTemplateColumns:"repeat(2, minmax(0, 1fr))",gap:8}}>
                <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-card)",border:"1px solid var(--border)"}}>
                  <div style={{fontSize:10,fontWeight:700,color:"var(--accent)",marginBottom:6}}>현재 적용</div>
                  <div style={{display:"grid",gap:4,fontSize:10,color:"var(--text-secondary)",lineHeight:1.55}}>
                    <div>방식: <span style={{color:statusTone,fontWeight:700}}>{currentMode==="manual"?"수동 연결":"자동 매칭"}</span></div>
                    <div>경로: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{currentSource||"(없음)"}</span></div>
                    <div>fab_col / ts_col: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{currentMeta.fab_col||"fab_lot_id"} / {currentMeta.ts_col||"last"}</span></div>
                    <div>join_keys: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{(currentMeta.join_keys||[]).length?(currentMeta.join_keys||[]).join(", "):"미확정"}</span></div>
                    {currentAliasPairs.length>0&&<div>raw → runtime: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{currentAliasPairs.map(([raw,runtime])=>`${raw}→${runtime}`).join(", ")}</span></div>}
                    <div style={{display:"flex",flexWrap:"wrap",gap:4,alignItems:"center"}}>fab_lot 예시:
                      {currentPreviewLots.length?currentPreviewLots.map(v=><span key={v} style={{padding:"1px 7px",borderRadius:999,background:"rgba(34,197,94,0.12)",color:"rgba(22,163,74,0.95)",fontSize:10,fontFamily:"monospace",fontWeight:700}}>{v}</span>)
                        :<span style={{fontSize:10,color:"var(--text-secondary)"}}>표시할 값 없음</span>}
                    </div>
                    {currentMeta.error&&<div style={{padding:"6px 8px",borderRadius:6,background:"rgba(239,68,68,0.12)",border:"1px solid rgba(239,68,68,0.35)",fontSize:10,color:"rgba(239,68,68,0.95)"}}>{currentMeta.error}</div>}
                  </div>
                </div>
                <div style={{padding:"10px 12px",borderRadius:8,background:"var(--bg-card)",border:"1px solid var(--border)"}}>
                  <div style={{fontSize:10,fontWeight:700,color:"var(--accent)",marginBottom:6}}>자동 매칭 후보</div>
                  <div style={{display:"grid",gap:4,fontSize:10,color:"var(--text-secondary)",lineHeight:1.55}}>
                    <div>ML_TABLE 파생 제품: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{mlMatch.pro||deriveProductFolder(selProd)||"(없음)"}</span></div>
                    <div>자동 경로: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{autoFabSource||"(자동 후보 없음)"}</span></div>
                    <div>탐색기 DB 후보: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{(mlMatch.matches||[]).length?(mlMatch.matches||[]).map(x=>x.path).join(", "):"(없음)"}</span></div>
                    <div style={{fontSize:9,color:"var(--text-secondary)"}}>수동 연결은 아래 목록에서 탐색기와 같은 DB 경로를 직접 고릅니다.</div>
                  </div>
                </div>
              </div>

              <div style={{fontSize:10,fontWeight:700,color:"var(--accent)"}}>1. 연결 방식 선택</div>
              <div style={{display:"flex",flexWrap:"wrap",gap:6}}>
                <button onClick={()=>setMode("auto")} style={{padding:"5px 12px",borderRadius:999,border:draftOverrideMode==="auto"?"1px solid var(--accent)":"1px solid var(--border)",background:draftOverrideMode==="auto"?"var(--accent-glow)":"var(--bg-card)",color:draftOverrideMode==="auto"?"var(--accent)":"var(--text-secondary)",fontSize:10,fontWeight:700,cursor:"pointer"}}>자동 매칭</button>
                <button onClick={()=>setMode("manual")} disabled={!manualFabOptions.length} style={{padding:"5px 12px",borderRadius:999,border:draftOverrideMode==="manual"?"1px solid rgba(245,158,11,0.95)":"1px solid var(--border)",background:draftOverrideMode==="manual"?"rgba(245,158,11,0.12)":"var(--bg-card)",color:draftOverrideMode==="manual"?"rgba(245,158,11,0.95)":"var(--text-secondary)",fontSize:10,fontWeight:700,cursor:manualFabOptions.length?"pointer":"not-allowed",opacity:manualFabOptions.length?1:0.5}}>수동 연결</button>
              </div>
              {draftOverrideMode==="auto"
                ?<div style={{padding:"10px 12px",borderRadius:8,background:"rgba(34,197,94,0.08)",border:"1px solid rgba(34,197,94,0.24)",fontSize:10,color:"var(--text-secondary)",lineHeight:1.6}}>
                  <div>저장 시 <span style={{fontFamily:"monospace",color:"rgba(22,163,74,0.95)",fontWeight:700}}>{autoFabSource||"(자동 후보 없음)"}</span> 를 사용합니다.</div>
                  <div>자동 후보가 없으면 수동 연결로 전환해서 탐색기 DB 경로를 선택하면 됩니다.</div>
                </div>
                :<div style={{display:"grid",gap:6}}>
                  <div style={{fontSize:10,fontWeight:700,color:"var(--accent)"}}>2. 수동 DB 경로 선택</div>
                  <select value={currentManualFabSource||""} onChange={e=>setManualSource(e.target.value)} style={selectS}>
                    <option value="">DB 경로 선택</option>
                    {manualFabOptions.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  <div style={{fontSize:9,color:"var(--text-secondary)"}}>탐색기에 보이는 DB 경로와 같은 형식으로 연결됩니다. 예: <span style={{fontFamily:"monospace"}}>1.RAWDATA_DB_FAB/PRODA</span></div>
                </div>}

              <div style={{fontSize:10,fontWeight:700,color:"var(--accent)",marginTop:2}}>3. 연결 열 확인</div>
              {overridePreviewLoading?<div style={{fontSize:10,color:"var(--text-secondary)"}}>연결 미리보기 로딩 중...</div>
              :!effectivePreviewSource?<div style={{padding:"8px 10px",borderRadius:6,background:"var(--bg-card)",border:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)"}}>먼저 자동 후보를 확인하거나 수동 DB 경로를 선택하세요.</div>
              :preview.error&&!previewApiMissing?<div style={{padding:"8px 10px",borderRadius:6,background:"rgba(239,68,68,0.12)",border:"1px solid rgba(239,68,68,0.35)",fontSize:10,color:"rgba(239,68,68,0.95)",lineHeight:1.5}}>{preview.error}</div>
              :<div style={{display:"grid",gap:8}}>
                <div style={{fontSize:10,color:"var(--text-secondary)",lineHeight:1.5}}>
                  미리보기 경로: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{effectivePreviewSource}</span>
                </div>
                {draftAliasPairs.length>0&&<div style={{padding:"8px 10px",borderRadius:6,background:"rgba(59,130,246,0.08)",border:"1px solid rgba(59,130,246,0.24)",fontSize:10,color:"var(--text-secondary)",lineHeight:1.5}}>
                  실제 DB 컬럼 선택 기준: <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>{draftAliasPairs.map(([raw,runtime])=>`${raw} → ${runtime}`).join(", ")}</span>
                </div>}
                {previewApiMissing&&<div style={{padding:"8px 10px",borderRadius:6,background:"rgba(245,158,11,0.12)",border:"1px solid rgba(245,158,11,0.35)",fontSize:10,color:"rgba(245,158,11,0.95)",lineHeight:1.5}}>
                  {preview.error}
                </div>}
                <div style={{display:"grid",gridTemplateColumns:"repeat(2, minmax(0, 1fr))",gap:6}}>
                  <label style={{fontSize:10,color:"var(--text-secondary)"}}>root_col
                    <select value={selectedRootCol||""} onChange={e=>setOv("root_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.root_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                  <label style={{fontSize:10,color:"var(--text-secondary)"}}>wf_col
                    <select value={selectedWfCol||""} onChange={e=>setOv("wf_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.wf_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                  <label style={{fontSize:10,color:"var(--text-secondary)"}}>fab_col
                    <select value={selectedFabCol||""} onChange={e=>setOv("fab_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.fab_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                  <label style={{fontSize:10,color:"var(--text-secondary)"}}>ts_col
                    <select value={selectedTsCol||""} onChange={e=>setOv("ts_col",e.target.value)} style={{...selectS,marginTop:4}}>
                      <option value="">자동 ({rec.ts_col||"없음"})</option>
                      {rawCols.map(c=><option key={c} value={c}>{formatColLabel(c)}</option>)}
                    </select>
                  </label>
                </div>
                <div style={{fontSize:10,color:"var(--text-secondary)"}}>4. 가져올 열 (실제 DB 컬럼)</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:4,maxHeight:120,overflowY:"auto",padding:"2px 0"}}>
                  {overrideOptions.map(col=><span key={col} onClick={()=>toggleOverrideCol(col)}
                    style={{padding:"3px 8px",borderRadius:999,cursor:"pointer",fontSize:10,fontFamily:"monospace",
                      background:chosenCols.includes(col)?"var(--accent-glow)":"var(--bg-card)",
                      color:chosenCols.includes(col)?"var(--accent)":"var(--text-secondary)",
                      border:"1px solid "+(chosenCols.includes(col)?"var(--accent)":"var(--border)")}}>
                    {chosenCols.includes(col)?"✓ ":""}{formatColLabel(col)}
                  </span>)}
                  {overrideOptions.length===0&&<span style={{fontSize:10,color:"var(--text-secondary)"}}>선택 가능한 DB 컬럼 없음</span>}
                </div>
                <div style={{fontSize:10,color:"var(--text-secondary)"}}>5. 최근 fab_lot_id 미리보기</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                  {draftPreviewLots.map(v=><span key={v} style={{padding:"2px 8px",borderRadius:999,background:"rgba(245,158,11,0.14)",color:"rgba(245,158,11,0.95)",fontSize:10,fontFamily:"monospace",fontWeight:700}}>{v}</span>)}
                  {draftPreviewLots.length===0&&<span style={{fontSize:10,color:"var(--text-secondary)"}}>{previewApiMissing?"미리보기 API가 없어 저장 후 조회에서 확인됩니다.":"표시할 fab_lot_id 가 없습니다."}</span>}
                </div>
              </div>}
              <div style={{display:"flex",alignItems:"center",gap:6,marginTop:2}}>
                <button onClick={applyLink} style={{padding:"5px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:10,cursor:"pointer",fontWeight:700}}>연결 적용</button>
                <span style={{fontSize:9,color:"var(--text-secondary)"}}>현재 선택된 제품에만 저장됩니다.</span>
              </div>
              <details style={{marginTop:2}}>
                <summary style={{cursor:"pointer",fontSize:10,color:"var(--text-secondary)"}}>고급 수동 조정</summary>
                <div style={{display:"grid",gap:6,marginTop:8}}>
                  <textarea value={ov.override_cols||""} onChange={e=>setOv("override_cols",e.target.value)} rows={2}
                    placeholder={(overrideOptions||[]).join(", ")||"root_lot_id, wafer_id, lot_id, time"} style={{...S,width:"100%",fontSize:10,fontFamily:"monospace",resize:"vertical"}}/>
                </div>
              </details>
            </div>);
          })()}

          {/* v8.8.9: Column/step rulebook — prefix 별 섹션 분리.
                KNOB: knob_ppid.csv (feature→func_step 조합/연산자/ppid) + step_matching.csv (func_step→step_id 확장)
                INLINE: inline_matching.csv (item_id/step_id/desc) — INLINE_<item_id> 가 해당 step 에서 측정
                VM: vm_matching.csv (feature_name/step_desc/step_id) — VM_<feature_name> 이 해당 step 에서 예측
             */}
          {selProd && (() => {
            const rulebookSpecs={
              knob_ppid:{file:"knob_ppid.csv",color:"rgba(251,191,36,0.95)",roles:[["feature","feature_col"],["func_step","func_step_col"],["rule_order","rule_order_col"],["ppid","ppid_col"],["operator","operator_col"],["category","category_col"],["use","use_col"],["product","product_col"]]},
              step_matching:{file:"step_matching.csv",color:"rgba(96,165,250,0.95)",roles:[["step_id","step_id_col"],["func_step","func_step_col"],["module","module_col"],["product","product_col"]]},
              inline_matching:{file:"inline_matching.csv",color:"rgba(16,185,129,0.95)",roles:[["item_id","item_id_col"],["step_id","step_id_col"],["item_desc","item_desc_col"],["product","product_col"]]},
              vm_matching:{file:"vm_matching.csv",color:"rgba(196,181,253,0.95)",roles:[["feature","feature_col"],["step_desc","step_desc_col"],["step_id","step_id_col"],["product","product_col"]]},
            };
            const SectionHeader = ({title, files, count, editKinds}) => (
              <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:4,flexWrap:"wrap"}}>
                <span style={{fontSize:10,fontWeight:700,color:"var(--text-primary)"}}>{title}</span>
                <span style={{fontSize:9,color:"var(--text-secondary)"}}>({count} 항목)</span>
                <span style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace"}}>
                  → {files.join(" + ")}
                </span>
                {isAdmin && (editKinds||[]).map(k => (
                  <span key={k} style={{display:"inline-flex",gap:2}}>
                    <button onClick={()=>openRowEditor(k)}
                      title={`${k} 의 ${selProd||"제품"} 행 추가/수정/삭제`}
                      style={{padding:"1px 6px",borderRadius:3,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:9,cursor:"pointer"}}>편집 {k}</button>
                    <button onClick={()=>openSchemaEditor(k)}
                      title={`${k} 의 역할→실제 컬럼명 매핑 조정 (soft-landing)`}
                      style={{padding:"1px 6px",borderRadius:3,border:"1px dashed var(--text-secondary)",background:"transparent",color:"var(--text-secondary)",fontSize:9,cursor:"pointer"}}>🔧 컬럼</button>
                  </span>
                ))}
              </div>
            );
            const RulebookSourceSummary=({kinds})=>(
              <div style={{display:"grid",gap:6,marginBottom:8}}>
                {(kinds||[]).map((kind)=>{
                  const spec=rulebookSpecs[kind];
                  if(!spec) return null;
                  const defaults=rbSchema.defaults?.[kind]||{};
                  const current={...defaults,...(rbSchema.schema?.[kind]||{})};
                  return(
                    <div key={kind} style={{padding:"7px 8px",borderRadius:6,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
                      <div style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap",marginBottom:5}}>
                        <span style={{fontSize:10,fontWeight:700,color:spec.color,fontFamily:"monospace"}}>{spec.file}</span>
                        <span style={{fontSize:9,color:"var(--text-secondary)"}}>기준 CSV</span>
                      </div>
                      <div style={{display:"flex",flexWrap:"wrap",gap:4}}>
                        {spec.roles.map(([label,key])=>{
                          const value=String(current[key]||"—").trim()||"—";
                          const changed=String(defaults[key]||"").trim()!==value;
                          return(
                            <span key={key} style={{
                              padding:"2px 7px",
                              borderRadius:999,
                              fontSize:9,
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
                <div style={{fontSize:11,fontWeight:700,color:"var(--accent)",marginBottom:8}}>📘 컬럼/공정 연결 규칙 — {selProd}</div>
                <div style={{marginBottom:8,padding:"8px 10px",borderRadius:6,background:"var(--bg-secondary)",border:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)",lineHeight:1.6}}>
                  <div>기본값은 <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>같은 이름의 Base 파일</span>과 <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>기본 열 이름</span>을 자동으로 사용합니다.</div>
                  <div><span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>KNOB_*</span> 는 조건 항목을 <span style={{fontFamily:"monospace"}}>knob_ppid.csv</span> 와 <span style={{fontFamily:"monospace"}}>step_matching.csv</span> 로 function_step / step_id 에 연결합니다.</div>
                  <div><span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>INLINE_*</span>, <span style={{fontFamily:"monospace",color:"var(--text-primary)"}}>VM_*</span> 은 측정/예측 항목을 각 matching 파일로 step_id 에 연결합니다.</div>
                  <div>열 이름이 다르거나 다른 Base 데이터와 연결해야 하면 각 섹션의 <b>편집</b> / <b>🔧 컬럼</b>에서 역할과 실제 CSV 헤더를 바꾸면 됩니다.</div>
                </div>

                {/* ── KNOB 섹션 ───────────────────────────── */}
                <div style={{marginBottom:10,padding:"6px 8px",borderRadius:4,background:"var(--bg-primary)",border:"1px solid rgba(251,191,36,0.3)"}}>
                  <SectionHeader title="🔧 KNOB_*" count={knobEntries.length}
                    files={["knob_ppid.csv", "step_matching.csv"]}
                    editKinds={["knob_ppid","step_matching"]} />
                  <RulebookSourceSummary kinds={["knob_ppid","step_matching"]}/>
                  {knobEntries.length===0 && (
                    <div style={{fontSize:9,fontStyle:"italic",color:"var(--text-secondary)"}}>등록된 KNOB 룰 없음.</div>
                  )}
                  <div style={{maxHeight:160,overflowY:"auto",display:"flex",flexDirection:"column",gap:4}}>
                    {knobEntries.map(([fname, meta]) => (
                      <div key={fname} style={{padding:"4px 6px",borderRadius:3,background:"var(--bg-secondary)"}}>
                        <div style={{fontFamily:"monospace",fontSize:10,color:"rgba(251,191,36,0.95)",fontWeight:700}}>{fname}</div>
                        <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:1,lineHeight:1.4}}>
                          {(meta.groups || []).map((g, gi) => (
                            <div key={gi} style={{display:"flex",gap:3,flexWrap:"wrap",marginBottom:1}}>
                              <span style={{padding:"0 3px",background:"rgba(59,130,246,0.15)",color:"rgba(59,130,246,0.95)",borderRadius:2,fontFamily:"monospace",fontWeight:700}}>#{g.rule_order}</span>
                              <span style={{fontFamily:"monospace",fontWeight:600,color:"var(--text-primary)"}}>{g.func_step}</span>
                              {Array.isArray(g.modules) && g.modules.length > 0 && g.modules.map((mod) => (
                                <span key={mod} style={{padding:"0 4px",background:"rgba(16,185,129,0.14)",color:"rgba(16,185,129,0.95)",borderRadius:999,fontFamily:"monospace",fontWeight:700}}>{mod}</span>
                              ))}
                              {g.operator && <span style={{opacity:0.55}}>{g.operator}</span>}
                              {g.ppid && <span style={{fontFamily:"monospace",opacity:0.7}}>[{g.ppid}]</span>}
                              <span style={{flex:"1 1 100%",marginLeft:12,fontFamily:"monospace",fontSize:9,color:"var(--text-secondary)"}}>
                                → [{(g.step_ids || []).join(", ") || "—"}]
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* ── INLINE 섹션 ─────────────────────────── */}
                <div style={{marginBottom:10,padding:"6px 8px",borderRadius:4,background:"var(--bg-primary)",border:"1px solid rgba(16,185,129,0.3)"}}>
                  <SectionHeader title="🔬 INLINE_*" count={inlineEntries.length}
                    files={["inline_matching.csv"]}
                    editKinds={["inline_matching"]} />
                  <RulebookSourceSummary kinds={["inline_matching"]}/>
                  {inlineEntries.length===0 && (
                    <div style={{fontSize:9,fontStyle:"italic",color:"var(--text-secondary)"}}>등록된 INLINE 룰 없음.</div>
                  )}
                  <div style={{maxHeight:120,overflowY:"auto",display:"flex",flexDirection:"column",gap:3}}>
                    {inlineEntries.map(([fname, meta]) => (
                      <div key={fname} style={{padding:"3px 6px",borderRadius:3,background:"var(--bg-secondary)",display:"flex",gap:6,fontFamily:"monospace",fontSize:9,alignItems:"center"}}>
                        <span style={{color:"rgba(16,185,129,0.95)",fontWeight:700}}>{fname}</span>
                        {meta.item_desc && <span style={{color:"var(--text-secondary)"}}>{meta.item_desc}</span>}
                        <span style={{flex:1}}/>
                        {(meta.step_ids||[]).slice(0,3).map(sid=>(
                          <span key={sid} style={{padding:"0 4px",background:"rgba(96,165,250,0.15)",color:"#60a5fa",borderRadius:2,fontWeight:700}}>{sid}</span>
                        ))}
                      </div>
                    ))}
                  </div>
                </div>

                {/* ── VM 섹션 ─────────────────────────────── */}
                <div style={{marginBottom:6,padding:"6px 8px",borderRadius:4,background:"var(--bg-primary)",border:"1px solid rgba(139,92,246,0.3)"}}>
                  <SectionHeader title="🤖 VM_*" count={vmEntries.length}
                    files={["vm_matching.csv"]}
                    editKinds={["vm_matching"]} />
                  <RulebookSourceSummary kinds={["vm_matching"]}/>
                  {vmEntries.length===0 && (
                    <div style={{fontSize:9,fontStyle:"italic",color:"var(--text-secondary)"}}>등록된 VM 룰 없음.</div>
                  )}
                  <div style={{maxHeight:140,overflowY:"auto",display:"flex",flexDirection:"column",gap:3}}>
                    {vmEntries.map(([fname, meta]) => (
                      <div key={fname} style={{padding:"3px 6px",borderRadius:3,background:"var(--bg-secondary)",display:"flex",gap:6,fontFamily:"monospace",fontSize:9}}>
                        <span style={{color:"rgba(139,92,246,0.95)",fontWeight:700}}>{fname}</span>
                        {meta.step_desc && <span style={{color:"var(--text-secondary)"}}>{meta.step_desc}</span>}
                        <span style={{flex:1}}/>
                        {meta.step_id && <span style={{padding:"0 4px",background:"rgba(96,165,250,0.15)",color:"#60a5fa",borderRadius:2,fontWeight:700}}>{meta.step_id}</span>}
                      </div>
                    ))}
                  </div>
                </div>

                <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:4,lineHeight:1.4}}>
                  {isAdmin ? "admin: 섹션별 [편집]에서 제품별 연결 규칙을 추가/수정/삭제하고, [컬럼]에서 CSV 헤더 매핑을 조정합니다." : "편집은 admin 권한이 필요합니다. 규칙 파일은 DB 루트 최상단에 있습니다."}
                </div>
              </div>
            );
          })()}

          <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:10,marginBottom:10,lineHeight:1.5}}>
            Color-coded: {COLOR_PREFIXES.join(", ")}
          </div>
          </>}
          <button onClick={()=>setShowSettings(false)} style={{width:"100%",padding:"8px",borderRadius:6,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontWeight:600,fontSize:11,cursor:"pointer"}}>{settingsTab==="advanced"?"고급 설정 닫기":"닫기"}</button>
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
        {/* v8.8.5: 상단 fab_source 배지 — Fab Lot ID 가 어디서 join 되어 왔는지 한눈에 확인. */}
        {(data?.override||mlMatch.override) && (()=>{const ov=data?.override||mlMatch.override;
          if(ov.error){
            // v8.8.19: fab_source off 툴팁에 탐색한 경로/db_root 까지 노출 + 클릭 시 상세 표시.
            const detail = [
              ov.error,
              ov.db_root ? `\n[db_root] ${ov.db_root}` : "",
              ov.base_root ? `\n[DB 단일 파일 루트] ${ov.base_root}` : "",
              (ov.searched_db_roots && ov.searched_db_roots.length) ? `\n[DB 최상위 후보] ${ov.searched_db_roots.join(", ")}` : "",
              (ov.tried_candidates && ov.tried_candidates.length) ? `\n[탐색 경로]\n  - ${ov.tried_candidates.join("\n  - ")}` : ""
            ].filter(Boolean).join("");
            return <span title={detail}
              onClick={()=>alert(detail)}
              style={{fontSize:10,padding:"2px 8px",borderRadius:4,background:"rgba(239,68,68,0.15)",color:"rgba(239,68,68,0.95)",border:"1px solid #ef4444",cursor:"help"}}>⚠ FAB 연동 꺼짐 (상세)</span>;
          }
          if(!ov.enabled){
            if(!mlMatch.effective_fab_source) return null;
            return <span style={{fontSize:10,padding:"2px 8px",borderRadius:4,background:"rgba(245,158,11,0.12)",color:"rgba(245,158,11,0.95)",border:"1px solid rgba(245,158,11,0.45)",fontFamily:"monospace"}}>
              🔗 {mlMatch.effective_fab_source} (확인 필요)
            </span>;
          }
          const sfx=ov.manual_override?"매뉴얼":"자동";
          const title=`fab_source: ${ov.fab_source}\nfab_col: ${ov.fab_col} · ts_col: ${ov.ts_col||"(없음)"}\njoin_keys: [${(ov.join_keys||[]).join(", ")}]\nscanned: ${ov.scanned_count}파일 / ${ov.row_count}행\nsample: ${(ov.sample_fab_values||[]).join(", ")||"(없음)"}`;
          return <span title={title} style={{fontSize:10,padding:"2px 8px",borderRadius:4,background:"rgba(34,197,94,0.12)",color:"rgba(22,163,74,0.95)",border:"1px solid #22c55e",fontFamily:"monospace",cursor:"help"}}>
            🔗 {ov.fab_source} · {ov.fab_col}@{ov.ts_col||"last"} ({sfx})
          </span>;
        })()}
        <div style={{marginLeft:"auto",display:"flex",gap:4,alignItems:"center"}}>
          {/* v8.4.3: Features 탭 제거 — ML_TABLE_PROD* 가 source 이므로 별도 features 뷰 불필요. */}
          {[{k:"view",l:"View"},{k:"history",l:"History"}].map(({k,l})=><span key={k} className={"splittable-tab splittable-tab-"+k} data-active={tab===k?"1":"0"} onClick={()=>{setTab(k);if(k==="history")loadHistoryByMode(histMode);}} style={{padding:"4px 10px",borderRadius:4,fontSize:11,cursor:"pointer",background:tab===k?"var(--accent-glow)":"transparent",color:tab===k?"var(--accent)":"var(--text-secondary)",fontWeight:tab===k?600:400}}>{l}</span>)}
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          {["all","diff"].map(m=><span key={m} onClick={()=>setViewMode(m)} style={{padding:"4px 10px",borderRadius:4,fontSize:11,cursor:"pointer",background:viewMode===m?"var(--accent-glow)":"transparent",color:viewMode===m?"var(--accent)":"var(--text-secondary)",fontWeight:viewMode===m?600:400}}>{m}</span>)}
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          <label title="필요할 때만 적용 대상 공정 정보를 표시합니다" style={{display:"inline-flex",alignItems:"center",gap:5,fontSize:11,color:showParamMeta?"var(--accent)":"var(--text-secondary)",cursor:"pointer",padding:"2px 6px"}}>
            <input type="checkbox" checked={showParamMeta} onChange={e=>setShowParamMeta(e.target.checked)}/>
            적용 공정 정보
          </label>
          <label title="아래 요약 표에서 항목과 적용 공정을 함께 봅니다" style={{display:"inline-flex",alignItems:"center",gap:5,fontSize:11,color:showLineageSummary?"var(--accent)":"var(--text-secondary)",cursor:"pointer",padding:"2px 6px"}}>
            <input type="checkbox" checked={showLineageSummary} onChange={e=>setShowLineageSummary(e.target.checked)}/>
            하단 적용 요약
          </label>
          <span style={{width:1,height:16,background:"var(--border)"}}/>
          {editing?<>
            <button onClick={()=>{if(Object.keys(pendingPlans).length>0)setShowConfirm(true);else setEditing(false);}} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"rgba(34,197,94,0.95)",color:"var(--bg-secondary)",fontSize:11,fontWeight:600,cursor:"pointer"}}>Save ({Object.keys(pendingPlans).length})</button>
            <button onClick={()=>{setEditing(false);setPendingPlans({});}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>Cancel</button>
          </>:<>
            {/* v8.4.9: window.open → dl() — 새 탭은 토큰 헤더가 안 붙어 401. blob 다운로드로 전환. */}
            <button onClick={()=>{const url=API+"/download-csv?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+(isCustomMode&&selCustom?"&custom_name="+encodeURIComponent(selCustom):"")+"&transposed=true&username="+encodeURIComponent(user?.username||"");dl(url, `splittable_${selProd}_${lotId||"all"}.csv`).catch(e=>alert("CSV 다운로드 실패: "+e.message));}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:11,cursor:"pointer"}}>⬇ CSV</button>
            <button onClick={()=>{const url=API+"/download-xlsx?product="+encodeURIComponent(selProd)+"&root_lot_id="+encodeURIComponent(lotId)+"&wafer_ids="+encodeURIComponent(waferIds)+"&prefix="+encodeURIComponent(prefixParam)+(isCustomMode&&selCustom?"&custom_name="+encodeURIComponent(selCustom):"")+"&username="+encodeURIComponent(user?.username||"");dl(url, `splittable_${selProd}_${lotId||"all"}.xlsx`).catch(e=>alert("XLSX 다운로드 실패: "+e.message));}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid #10b981",background:"transparent",color:"rgba(16,185,129,0.95)",fontSize:11,cursor:"pointer"}} title="XLSX (fab_lot_id 병합)">⬇ XLSX</button>
            <button onClick={()=>setEditing(true)} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:11,fontWeight:600,cursor:"pointer"}}>Edit</button>
            {/* v8.4.9-b: 노트 드로어 토글 */}
            <button onClick={()=>{setNoteFilter(null);setNotesOpen(true);}} title="wafer 태그 · 항목 메모" style={{padding:"4px 12px",borderRadius:4,border:"1px solid #3b82f6",background:"transparent",color:"rgba(59,130,246,0.95)",fontSize:11,fontWeight:600,cursor:"pointer",display:"inline-flex",gap:4,alignItems:"center"}}>📝 노트{notes.length>0&&<span style={{padding:"0 6px",borderRadius:10,background:"rgba(59,130,246,0.95)",color:"var(--bg-secondary)",fontSize:9,fontWeight:700}}>{notes.length}</span>}</button>
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
        const lineageSummary = buildLineageSummary(displayRows);
        return <div style={{flex:1,overflow:"auto",background:"var(--bg-card)"}}>
        {/* v8.8.13: 빈 셀 / knobMeta 확장 행에서 테두리 끊기는 현상 — 전체 td/th 기본 border 강제.
            inline style(borderLeft plan 등)은 specificity 가 높아 유지됨. */}
        <style>{`.splittable-grid td, .splittable-grid th { border: 1px solid #555; }`}</style>
        <table className="splittable-grid" style={{borderCollapse:"collapse",fontSize:11,background:"var(--bg-card)",tableLayout:"fixed",width:288+(data.headers?.length||1)*115}}>
          <colgroup>
            <col style={{width:288}}/>
            {data.headers?.map((_,i)=><col key={i} style={{width:115}}/>)}
          </colgroup>
          <thead>
            {data.root_lot_id&&(()=>{const lotN=notesForLot().length;return(<tr style={{height:28}}><th style={{boxSizing:"border-box",height:28,padding:0,background:"var(--bg-tertiary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",position:"sticky",top:0,left:0,zIndex:5}}></th>
              <th colSpan={data.headers?.length||1} style={{boxSizing:"border-box",height:28,textAlign:"center",padding:"0 8px",lineHeight:"27px",fontWeight:700,fontSize:12,color:"var(--accent)",background:"var(--bg-tertiary)",borderBottom:"1px solid #555",position:"sticky",top:0,zIndex:4,fontFamily:"monospace",cursor:"pointer"}} title={lotN>0?`LOT ${data.root_lot_id} — ${lotN}개 태그 · 클릭해서 보기`:`LOT ${data.root_lot_id} — 태그 추가`} onClick={()=>{setNoteFilter({scope:"lot"});setNoteDraftScope({scope:"lot",product:selProd,root_lot_id:lotId});setNotesOpen(true);}}>{data.root_lot_id}{lotN>0&&<span style={{marginLeft:8,padding:"0 6px",borderRadius:10,background:"rgba(16,185,129,0.95)",color:"var(--bg-secondary)",fontSize:10,fontWeight:700}}>📦 {lotN}</span>}{viewMode==="diff"?<span style={{marginLeft:8,fontSize:10,color:"var(--text-secondary)",fontWeight:400}}>(diff: {displayRows.length}/{data.rows.length})</span>:null}</th></tr>);})()}
            {data.header_groups?.length>0&&<tr style={{height:24}}>
              <th style={{boxSizing:"border-box",height:24,padding:0,background:"var(--bg-tertiary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",position:"sticky",top:data.root_lot_id?28:0,left:0,zIndex:5}}></th>
              {data.header_groups.map((g,gi)=><th key={gi} colSpan={g.span} style={{boxSizing:"border-box",height:24,textAlign:"center",padding:"0 6px",fontWeight:700,fontSize:10,color:"rgba(251,191,36,0.95)",background:"var(--bg-tertiary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",position:"sticky",top:data.root_lot_id?28:0,zIndex:4,fontFamily:"monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={g.label}>{g.label}</th>)}
            </tr>}
            <tr>
            <th style={{textAlign:"left",padding:"8px 10px",fontWeight:700,fontSize:10,color:"var(--accent)",borderBottom:"2px solid #555",borderRight:"1px solid #555",background:"var(--bg-tertiary)",position:"sticky",top:data.root_lot_id?(data.header_groups?.length>0?52:27):(data.header_groups?.length>0?24:0),left:0,zIndex:5,minWidth:260}}>항목</th>
            {data.headers?.map((h,i)=>{const wid=String(h).replace(/^#/,"");const wn=notesForWafer(wid).length;return(<th key={i} style={{textAlign:"center",padding:"6px 8px",fontWeight:600,fontSize:10,color:"var(--text-secondary)",borderBottom:"2px solid #555",borderRight:"1px solid #555",background:"var(--bg-tertiary)",position:"sticky",top:data.root_lot_id?(data.header_groups?.length>0?52:27):(data.header_groups?.length>0?24:0),zIndex:3,whiteSpace:"normal",wordBreak:"break-word",minWidth:100,cursor:"pointer"}} title={wn>0?`wafer ${h} — ${wn}개 태그 · 클릭해서 보기`:`wafer ${h} — 태그 추가`} onClick={()=>{setNoteFilter({scope:"wafer",key:`${selProd}__${lotId}__W${wid}`});setNoteDraftScope({scope:"wafer",product:selProd,root_lot_id:lotId,wafer_id:wid});setNotesOpen(true);}}>
              <div>{h}</div>
              {wn>0&&<span style={{display:"inline-block",marginTop:2,padding:"0 6px",borderRadius:10,background:"rgba(59,130,246,0.95)",color:"var(--bg-secondary)",fontSize:9,fontWeight:700}}>🏷 {wn}</span>}
            </th>);})}
          </tr></thead>
          <tbody>{displayRows.map((row,ri)=>{
            const cells=row._cells||{};
            // v8.4.5: plan 값도 uniqMap 에 포함 — 같은 값이면 같은 팔레트 색상
            const allVals=Object.values(cells).map(c=>c?.actual||c?.plan).filter(v=>v&&v!=="None"&&v!=="null");
            const uniqVals=[...new Set(allVals)];const uniqMap={};uniqVals.forEach((v,i)=>{uniqMap[v]=i;});
            return(<tr key={ri}>
              {(()=>{const pLotN=notesForParam(row._param).length;return(
              <td style={{padding:"6px 10px",fontWeight:600,fontSize:11,color:"var(--text-primary)",borderBottom:"1px solid #555",borderRight:"1px solid #555",background:"var(--bg-secondary)",position:"sticky",left:0,zIndex:2,whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35,cursor:"pointer"}} title={(pLotN>0?`${row._param} — lot내 ${pLotN}개 태그 · 클릭해서 보기`:`${row._param} — 태그 보기/추가`)+((knobLookup(row._param)?.label)?"\n"+knobLookup(row._param).label:"")} onClick={()=>{setNoteFilter({scope:"param",param:row._param});setNoteDraftScope(null);setNotesOpen(true);}}>
                <div style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap"}}>
                  {/* v8.8.14: _display 가 있으면(KNOB/INLINE/VM 에서 rule_order+func_step 끼워 넣은 이름) 그것을, 없으면 raw _param 을 prefix strip 해서 표시. */}
                  <span>{(row._display||row._param||"").replace(/^[A-Z]+_/,"")}</span>
                  {pLotN>0&&<span style={{padding:"0 5px",borderRadius:8,background:"rgba(139,92,246,0.95)",color:"var(--bg-secondary)",fontSize:9,fontWeight:700}}>💬 {pLotN}</span>}
                </div>
                {/* v8.4.9: + 결합이면 줄바꿈. step_id 는 파란 pill 로 대비 강화. */}
                {showParamMeta && Array.isArray(knobLookup(row._param)?.groups) && knobLookup(row._param).groups.length > 0 && (
                  <div style={{fontSize:10,fontWeight:400,lineHeight:1.5,marginTop:4,fontFamily:"monospace"}}>
                    {knobLookup(row._param).groups.map((g, gi) => (
                      <div key={gi} style={{marginTop:gi>0?4:0,padding:"4px 6px",borderRadius:4,background:"rgba(251,191,36,0.06)",border:"1px solid rgba(251,191,36,0.18)"}}>
                        <div style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4}}>
                        {gi > 0 && <span style={{color:"rgba(239,68,68,0.95)",fontWeight:800,fontSize:12,marginRight:2}}>+</span>}
                        <span style={{color:"rgba(251,191,36,0.95)",fontWeight:700}}>{g.func_step}</span>
                        {Array.isArray(g.modules) && g.modules.length > 0 && g.modules.map((mod) => (
                          <span key={mod} style={{padding:"0 6px",borderRadius:999,background:"rgba(16,185,129,0.14)",border:"1px solid rgba(16,185,129,0.35)",color:"rgba(16,185,129,0.95)",fontWeight:700,fontSize:9}}>{mod}</span>
                        ))}
                        </div>
                        {Array.isArray(g.step_ids) && g.step_ids.length > 0 && (
                          <div style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4,marginTop:4}}>
                            <span style={{color:"var(--text-secondary)",fontSize:9}}>step_id</span>
                            <span style={{display:"inline-flex",flexWrap:"wrap",gap:3}}>
                            {g.step_ids.map((sid, si) => (
                              <span key={si} style={{padding:"0 6px",borderRadius:3,background:"rgba(96,165,250,0.18)",border:"1px solid rgba(96,165,250,0.5)",color:"rgba(147,197,253,0.95)",fontWeight:700,fontSize:10,letterSpacing:0.3}}>{sid}</span>
                            ))}
                            </span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {/* v8.8.15/v8.8.33: VM_ prefix row 의 step_id/step_desc sub-label — 항상 렌더, step_id 없으면 "미등록" pill. */}
                {showParamMeta && (row._param||"").startsWith("VM_") && (()=>{const vm=vmLookup(row._param)||{};const hasMeta=vm.step_id||vm.step_desc;return(
                  <div style={{fontSize:10,fontWeight:400,lineHeight:1.5,marginTop:4,fontFamily:"monospace"}}>
                    <div style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4}}>
                      <span style={{color:"rgba(139,92,246,0.95)",fontWeight:700}}>🤖 VM</span>
                      {vm.step_desc && <span style={{color:"rgba(196,181,253,0.95)"}}>{vm.step_desc}</span>}
                      {!hasMeta && <span title={row._param} style={{fontSize:9,color:"var(--text-secondary)",fontStyle:"italic"}}>{(row._param||"").replace(/^VM_/,"")}</span>}
                    </div>
                    {Array.isArray(vm.groups) && vm.groups.length>0 ? vm.groups.map((g,gi)=>(
                      <div key={gi} style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4,marginTop:gi>0?2:4}}>
                        {g.function_step && <span style={{color:"rgba(196,181,253,0.95)",fontWeight:700}}>{g.function_step}</span>}
                        {(()=>{const sids=Array.isArray(g.step_ids)&&g.step_ids.length?g.step_ids:(g.step_id?[g.step_id]:[]);return sids.length?sids.map((sid,si)=><span key={si} style={{padding:"0 6px",borderRadius:3,background:"rgba(139,92,246,0.18)",border:"1px solid rgba(139,92,246,0.5)",color:"rgba(196,181,253,0.95)",fontWeight:700,fontSize:10,letterSpacing:0.3}}>{sid}</span>):<span title="vm_matching.csv 에 step_id 미등록 — 연결 규칙 탭에서 자동 추정 실행" style={{padding:"0 6px",borderRadius:3,background:"rgba(148,148,148,0.18)",border:"1px dashed rgba(148,148,148,0.5)",color:"var(--text-secondary)",fontWeight:600,fontSize:10,letterSpacing:0.3}}>연결 확인 필요</span>;})()}
                      </div>
                    )) : (
                      <div style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4,marginTop:4}}>
                        {vm.function_step && <span style={{color:"rgba(196,181,253,0.95)",fontWeight:700}}>{vm.function_step}</span>}
                        {(()=>{const sids=Array.isArray(vm.step_ids)&&vm.step_ids.length?vm.step_ids:(vm.step_id?[vm.step_id]:[]);return sids.length?sids.map((sid,si)=><span key={si} style={{padding:"0 6px",borderRadius:3,background:"rgba(139,92,246,0.18)",border:"1px solid rgba(139,92,246,0.5)",color:"rgba(196,181,253,0.95)",fontWeight:700,fontSize:10,letterSpacing:0.3}}>{sid}</span>):<span title="vm_matching.csv 에 step_id 미등록 — 연결 규칙 탭에서 자동 추정 실행" style={{padding:"0 6px",borderRadius:3,background:"rgba(148,148,148,0.18)",border:"1px dashed rgba(148,148,148,0.5)",color:"var(--text-secondary)",fontWeight:600,fontSize:10,letterSpacing:0.3}}>연결 확인 필요</span>;})()}
                      </div>
                    )}
                  </div>);})()}
                {/* v8.8.15/v8.8.33: INLINE_ prefix row 의 step_id/item_desc sub-label — 항상 렌더. */}
                {showParamMeta && (row._param||"").startsWith("INLINE_") && (()=>{const im=inlineLookup(row._param)||{};const hasMeta=im.step_id||im.item_desc;return(
                  <div style={{fontSize:10,fontWeight:400,lineHeight:1.5,marginTop:4,fontFamily:"monospace"}}>
                    <div style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4}}>
                      <span style={{color:"rgba(16,185,129,0.95)",fontWeight:700}}>🔬 INLINE</span>
                      {im.item_desc && <span style={{color:"rgba(110,231,183,0.95)"}}>{im.item_desc}</span>}
                      {!hasMeta && <span title={row._param} style={{fontSize:9,color:"var(--text-secondary)",fontStyle:"italic"}}>{(row._param||"").replace(/^INLINE_/,"")}</span>}
                    </div>
                    {Array.isArray(im.groups) && im.groups.length>0 ? im.groups.map((g,gi)=>(
                      <div key={gi} style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4,marginTop:gi>0?2:4}}>
                        {g.function_step && <span style={{color:"rgba(110,231,183,0.95)",fontWeight:700}}>{g.function_step}</span>}
                        {(()=>{const sids=Array.isArray(g.step_ids)&&g.step_ids.length?g.step_ids:(g.step_id?[g.step_id]:[]);return sids.length?sids.map((sid,si)=><span key={si} style={{padding:"0 6px",borderRadius:3,background:"rgba(16,185,129,0.18)",border:"1px solid rgba(16,185,129,0.5)",color:"rgba(110,231,183,0.95)",fontWeight:700,fontSize:10,letterSpacing:0.3}}>{sid}</span>):<span title="inline_matching.csv 에 step_id 미등록 — 연결 규칙 탭에서 자동 추정 실행" style={{padding:"0 6px",borderRadius:3,background:"rgba(148,148,148,0.18)",border:"1px dashed rgba(148,148,148,0.5)",color:"var(--text-secondary)",fontWeight:600,fontSize:10,letterSpacing:0.3}}>연결 확인 필요</span>;})()}
                      </div>
                    )) : (
                      <div style={{display:"flex",flexWrap:"wrap",alignItems:"center",gap:4,marginTop:4}}>
                        {im.function_step && <span style={{color:"rgba(110,231,183,0.95)",fontWeight:700}}>{im.function_step}</span>}
                        {(()=>{const sids=Array.isArray(im.step_ids)&&im.step_ids.length?im.step_ids:(im.step_id?[im.step_id]:[]);return sids.length?sids.map((sid,si)=><span key={si} style={{padding:"0 6px",borderRadius:3,background:"rgba(16,185,129,0.18)",border:"1px solid rgba(16,185,129,0.5)",color:"rgba(110,231,183,0.95)",fontWeight:700,fontSize:10,letterSpacing:0.3}}>{sid}</span>):<span title="inline_matching.csv 에 step_id 미등록 — 연결 규칙 탭에서 자동 추정 실행" style={{padding:"0 6px",borderRadius:3,background:"rgba(148,148,148,0.18)",border:"1px dashed rgba(148,148,148,0.5)",color:"var(--text-secondary)",fontWeight:600,fontSize:10,letterSpacing:0.3}}>연결 확인 필요</span>;})()}
                      </div>
                    )}
                  </div>);})()}
              </td>);})()}
              {data.headers?.map((_,ci)=>{
                const cell=cells[String(ci)];const wid=String(data.headers[ci]??"").replace(/^#/,"");
                const cellNoteCount=notesForCell(wid,row._param).length;
                if(!cell)return(<td key={ci} style={{borderBottom:"1px solid #555",borderRight:"1px solid #555",background:"var(--bg-card)",position:"relative"}}>
                  {cellNoteCount>0&&<span onClick={e=>{e.stopPropagation();setNoteFilter({scope:"cell",wafer_id:wid,param:row._param});setNoteDraftScope({scope:"param",product:selProd,root_lot_id:lotId,wafer_id:wid,param:row._param});setNotesOpen(true);}} title={`${cellNoteCount}개 메모`} style={{position:"absolute",top:1,right:2,cursor:"pointer",fontSize:9,padding:"0 5px",borderRadius:7,background:"rgba(139,92,246,0.95)",color:"var(--bg-secondary)",fontWeight:700,lineHeight:"14px"}}>💬 {cellNoteCount}</span>}
                </td>);
                const bgStyle=getCellBg(cell.actual||cell.plan,uniqMap,row._param);const planStyle=getCellPlanStyle(cell);
                const canPlan=cell.can_plan!==false; // default true for backward compat
                const baseStyle={background:"var(--bg-card)",color:"var(--text-primary)"};
                const canEdit=canPlan;
                const style={...baseStyle,...bgStyle,...planStyle,padding:"4px 8px",borderBottom:"1px solid #555",borderRight:"1px solid #555",textAlign:"center",fontSize:11,cursor:canEdit?"pointer":"default",whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35,position:"relative"};
                const hasPlan=cell.plan&&!cell.actual;
                const isMismatch=cell.mismatch||false;
                const display=formatCell(cell.actual,row._param)||"";
                const openEdit=()=>{if(!canEdit)return;
                  // 자동으로 editing 모드 진입 (dbl-click 시 Edit 버튼 클릭 없이도 작동)
                  if(!editing)setEditing(true);
                  const editValue=Object.prototype.hasOwnProperty.call(pendingPlans,cell.key)
                    ? pendingPlans[cell.key]
                    : (cell.plan ?? cell.actual ?? "");
                  setActiveCell({key:cell.key,param:row._param,value:editValue});
                  // suggestion 캐시 확인 후 없으면 fetch
                  if(!colValCache[row._param]){
                    sf(API+"/column-values?product="+encodeURIComponent(selProd)+"&col="+encodeURIComponent(row._param)+"&limit=200")
                      .then(d=>setColValCache(m=>({...m,[row._param]:d.values||[]}))).catch(()=>{});
                  }
                };
                return(<td key={ci} className="stm-cell" style={style}
                  onClick={()=>{if(editing&&canEdit)openEdit();}}
                  onDoubleClick={()=>{if(canEdit)openEdit();}}
                  onContextMenu={e=>{if(cell.plan){e.preventDefault();deletePlan(cell.key);}}}
                  title={canPlan
                    ? (cell.actual ? "actual 값이 있어도 plan 입력/수정 가능. plan 과 actual 이 다르면 ✗ 로 표시됩니다." : "plan 입력 가능")
                    : "이 항목은 plan 입력 대상이 아닙니다"}>
                  {pendingPlans[cell.key]?<span style={{color:"#ea580c",fontWeight:700,fontStyle:"italic"}}>{"📌 "}{pendingPlans[cell.key]}</span>
                  :isMismatch?<span style={{color:"#dc2626",fontWeight:700}}>{"✗ "}{formatCell(cell.actual,row._param)}<span style={{fontSize:9,color:"rgba(239,68,68,0.95)"}}>{" (≠"+cell.plan+")"}</span></span>
                  :hasPlan?<span style={{fontStyle:"italic",fontWeight:700}}>{"📌 "}{cell.plan}</span>
                  :display}
                  {/* v8.4.9-c: per-cell 메모 배지. 메모가 있으면 항상 표시, 없으면 hover 시에만 + 아이콘 노출. */}
                  <span className="stm-note-btn" onClick={e=>{e.stopPropagation();setNoteFilter({scope:"cell",wafer_id:wid,param:row._param});setNoteDraftScope({scope:"param",product:selProd,root_lot_id:lotId,wafer_id:wid,param:row._param});setNotesOpen(true);}} title={cellNoteCount>0?`${cellNoteCount}개 메모`:"메모 추가"} style={{position:"absolute",top:1,right:2,cursor:"pointer",fontSize:9,padding:"0 5px",borderRadius:7,background:cellNoteCount>0?"rgba(139,92,246,0.95)":"rgba(139,92,246,0.25)",color:cellNoteCount>0?"var(--bg-secondary)":"rgba(139,92,246,0.95)",fontWeight:700,lineHeight:"14px",opacity:cellNoteCount>0?1:0,transition:"opacity 0.15s"}}>💬{cellNoteCount>0?" "+cellNoteCount:"+"}</span>
                </td>);})}
            </tr>);})}</tbody>
        </table>
        {showLineageSummary && lineageSummary.length>0&&<div style={{margin:"12px 10px 18px",border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-card)",overflow:"hidden"}}>
          <div style={{padding:"10px 12px",fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace",borderTop:"1px solid var(--border)",borderBottom:"1px solid var(--border)"}}>항목 → function_step → step_id 요약</div>
          <div style={{maxHeight:320,overflow:"auto"}}>
            <table style={{borderCollapse:"collapse",width:"100%",fontSize:11,fontFamily:"monospace"}}>
              <thead>
                <tr>
                  <th style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid #555",minWidth:220}}>항목</th>
                  <th style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid #555",minWidth:180}}>function_step</th>
                  <th style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid #555",minWidth:260}}>step_id</th>
                </tr>
              </thead>
              <tbody>
                {lineageSummary.map(x=>(
                  <tr key={x.key}>
                    <td style={{padding:"6px 10px",borderBottom:"1px solid #555"}}>{x.parameter}</td>
                    <td style={{padding:"6px 10px",borderBottom:"1px solid #555",color:"var(--text-secondary)"}}>{x.function_step||"—"}</td>
                    <td style={{padding:"6px 10px",borderBottom:"1px solid #555",color:"rgba(147,197,253,0.95)",fontWeight:700}}>{(x.step_ids||[]).length?x.step_ids.join(", "):"—"}</td>
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
            <label key={opt.key} title={opt.title} style={{display:"inline-flex",alignItems:"center",gap:6,fontSize:11,color:opt.disabled?"var(--text-muted)":"var(--text-primary)",cursor:opt.disabled?"not-allowed":"pointer",opacity:opt.disabled?0.55:1}}>
              <input type="radio" name="history-mode" checked={histMode===opt.key} disabled={!!opt.disabled} onChange={()=>!opt.disabled&&loadHistoryByMode(opt.key)} />
              <span style={histMode===opt.key?{color:"var(--accent)",fontWeight:700}:{color:"inherit"}}>{opt.label}</span>
            </label>
          ))}
          {isFinalHistoryMode(histMode)&&histFinal.drift_count>0&&<span style={{fontSize:10,padding:"2px 8px",borderRadius:10,background:"rgba(239,68,68,0.13)",color:"rgba(239,68,68,0.95)",fontWeight:600}}>⚠ drift {histFinal.drift_count}/{histFinal.total_cells}</span>}
          {isAdmin&&<button onClick={()=>dl(API+"/history-csv?product="+encodeURIComponent(selProd), `splittable_history_${selProd}.csv`).catch(e=>alert("이력 CSV 다운로드 실패: "+e.message))} style={{marginLeft:"auto",padding:"4px 12px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:11,cursor:"pointer"}}>⬇ History CSV</button>}
        </div>
        {isLotHistoryMode(histMode)&&lotId.trim()&&(
          <div style={{marginBottom:16,padding:12,borderRadius:8,background:"var(--bg-card)",border:"1px solid var(--border)"}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
              <div style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>Lot Operational History</div>
              <div style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace"}}>{lotId}{waferIds?.trim()?` · wafers ${waferIds.trim()}`:""}</div>
            </div>
            {opHistory.length===0?<div style={{fontSize:11,color:"var(--text-secondary)",padding:"8px 2px"}}>연결된 tracker / inform 기록 없음</div>
            :<table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
              <thead><tr>{["Time","Source","Scope","Wafer","Title","Status","Author"].map(h=><th key={h} style={{textAlign:"left",padding:"6px 8px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)",fontSize:10}}>{h}</th>)}</tr></thead>
              <tbody>{opHistory.map((r,i)=><tr key={i}>
                <td style={{padding:"6px 8px",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{(r.time||"").slice(0,16)}</td>
                <td style={{padding:"6px 8px",borderBottom:"1px solid var(--border)"}}><span style={{fontSize:10,padding:"2px 6px",borderRadius:4,background:r.source?.includes("tracker")?"#3b82f622":"#10b98122",color:r.source?.includes("tracker")?"rgba(59,130,246,0.95)":"rgba(16,185,129,0.95)"}}>{r.source}</span></td>
                <td style={{padding:"6px 8px",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)"}}>{r.scope}</td>
                <td style={{padding:"6px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10}}>{r.wafer_id||"-"}</td>
                <td style={{padding:"6px 8px",borderBottom:"1px solid var(--border)"}} title={r.detail||r.title}><div style={{fontWeight:600,color:"var(--text-primary)"}}>{r.title}</div><div style={{fontSize:10,color:"var(--text-secondary)",maxWidth:420,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{r.detail||""}</div></td>
                <td style={{padding:"6px 8px",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)"}}>{r.status||"-"}</td>
                <td style={{padding:"6px 8px",borderBottom:"1px solid var(--border)",fontSize:10}}>{r.author||"-"}</td>
              </tr>)}</tbody>
            </table>}
          </div>
        )}
        {isFinalHistoryMode(histMode)?(
          histFinal.final.length===0?<div style={{textAlign:"center",padding:40,color:"var(--text-secondary)"}}>No plan cells</div>
          :<table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
            <thead><tr>{["Last","User","Root Lot","Wafer","Column","Final","Changes","Drift"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 10px",borderBottom:"2px solid var(--border)",color:"var(--text-secondary)",fontSize:11}}>{h}</th>)}</tr></thead>
            <tbody>{histFinal.final.map((r,i)=>{
              const drift=Array.isArray(r.drift)?r.drift:[];
              const driftLabel=drift.includes("multi_change")&&drift.includes("multi_user")?"다수 변경·다수 사용자":drift.includes("multi_change")?"다수 변경":drift.includes("multi_user")?"다수 사용자":drift.includes("reinstated")?"삭제 후 재설정":"";
              return(<tr key={i} style={drift.length>0?{background:"#ef444408"}:{}}>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{(r.final_time||"").slice(0,16)}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>{r.final_user||"-"}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,color:"var(--accent)"}}>{r.root_lot_id}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10}}>{r.wafer_id}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,maxWidth:180,overflow:"hidden",textOverflow:"ellipsis"}} title={r.column}>{r.column}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:r.final_action==="delete"?"rgba(239,68,68,0.95)":"rgba(34,197,94,0.95)",fontWeight:600}}>{r.final_action==="delete"?"(삭제)":(r.final_value??"-")}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)"}} title={"distinct values: "+JSON.stringify(r.distinct_values)}>set {r.set_count}{r.delete_count>0?` / del ${r.delete_count}`:""}</td>
                <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>{driftLabel?<span style={{fontSize:10,padding:"2px 6px",borderRadius:3,background:"rgba(239,68,68,0.13)",color:"rgba(239,68,68,0.95)"}} title={drift.join(", ")}>⚠ {driftLabel}</span>:<span style={{fontSize:10,color:"var(--text-secondary)"}}>-</span>}</td>
              </tr>);})}</tbody>
          </table>
        ):(
          history.length===0?<div style={{textAlign:"center",padding:40,color:"var(--text-secondary)"}}>No history</div>
          :<table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
            <thead><tr>{["Time","User","Root Lot","Wafer","Column","Action","Old","New"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 10px",borderBottom:"2px solid var(--border)",color:"var(--text-secondary)",fontSize:11}}>{h}</th>)}</tr></thead>
            <tbody>{[...history].reverse().map((h,i)=>{const parts=h.cell?.split("|")||[];const lotPart=parts[0]||"";const wfPart=parts[1]||"";const colPart=parts[2]||h.cell||"";
              return(<tr key={i}>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{h.time?.slice(0,16)}</td>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>{h.user}</td>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,color:"var(--accent)"}}>{lotPart}</td>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10}}>{wfPart}</td>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,maxWidth:140,overflow:"hidden",textOverflow:"ellipsis"}} title={colPart}>{colPart}</td>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)"}}><span style={{fontSize:10,padding:"1px 5px",borderRadius:3,background:h.action==="set"?"#f9731622":"rgba(239,68,68,0.13)",color:h.action==="set"?"rgba(249,115,22,0.95)":"rgba(239,68,68,0.95)"}}>{h.action}</span></td>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{h.old||"-"}</td>
              <td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:"rgba(34,197,94,0.95)"}}>{h.new||"-"}</td>
            </tr>);})}</tbody></table>
        )}
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
              style={{padding:"6px 14px",borderRadius:5,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:12,fontWeight:600,cursor:featuresLoading?"default":"pointer",opacity:featuresLoading?0.5:1}}>
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
                style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:11,fontWeight:600,cursor:selFeatCols.length?"pointer":"default",opacity:selFeatCols.length?1:0.5}}>
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
                <td style={{padding:"4px 8px",borderBottom:"1px solid #555",color:"rgba(100,116,139,0.95)",fontSize:10}}>{i+1}</td>
                {cols.map(c=><td key={c} style={{padding:"4px 8px",borderBottom:"1px solid #555",maxWidth:180,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontSize:11,background:selFeatCols.includes(c)?"var(--accent-glow)":"transparent"}} title={String(r[c]==null?"":r[c])}>
                  {r[c]===null||r[c]===undefined?<span style={{color:"rgba(100,116,139,0.95)"}}>null</span>:String(r[c])}
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
            <button onClick={()=>commit(activeCell.value)} style={{flex:1,padding:"8px 12px",borderRadius:6,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontWeight:600,cursor:"pointer",fontSize:12}}>Apply</button>
            <button onClick={()=>setActiveCell(null)} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer",fontSize:12}}>Cancel</button>
          </div>
        </div>
      </div>;})()}
    {showConfirm&&<div style={{position:"fixed",inset:0,zIndex:9999,background:"rgba(0,0,0,0.6)",display:"flex",alignItems:"center",justifyContent:"center"}} onClick={()=>setShowConfirm(false)}>
      <div onClick={e=>e.stopPropagation()} style={{background:"var(--bg-secondary)",borderRadius:12,padding:24,width:400,border:"1px solid var(--border)",maxHeight:"80vh",overflow:"auto"}}>
        <div style={{fontSize:16,fontWeight:700,marginBottom:12}}>Confirm Changes</div>
        <div style={{fontSize:13,color:"var(--text-secondary)",marginBottom:16}}>{Object.keys(pendingPlans).length} cells will be updated</div>
        {Object.entries(pendingPlans).map(([k,v])=>(<div key={k} style={{fontSize:11,padding:"4px 0",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between"}}><span style={{fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:250,overflow:"hidden",textOverflow:"ellipsis"}}>{k.split("|").pop()}</span><span style={{color:"rgba(249,115,22,0.95)",fontWeight:600}}>{v}</span></div>))}
        <div style={{display:"flex",gap:8,marginTop:16}}>
          <button onClick={savePlans} style={{flex:1,padding:10,borderRadius:6,border:"none",background:"rgba(34,197,94,0.95)",color:"var(--bg-secondary)",fontWeight:600,cursor:"pointer"}}>Confirm</button>
          <button onClick={()=>setShowConfirm(false)} style={{padding:"10px 20px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>Cancel</button>
        </div></div></div>}

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
      return(<div style={{position:"fixed",top:0,right:0,bottom:0,width:420,background:"var(--bg-secondary)",borderLeft:"1px solid var(--border)",zIndex:2000,display:"flex",flexDirection:"column",boxShadow:"-4px 0 16px rgba(0,0,0,0.35)"}}>
        <div style={{padding:"12px 16px",borderBottom:"1px solid var(--border)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <div style={{fontSize:13,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>📝 {title}</div>
          <span onClick={()=>{setNotesOpen(false);setNoteDraft("");setNoteFilter(null);setNoteDraftScope(null);setNoteSearch("");}} style={{cursor:"pointer",fontSize:18,color:"var(--text-secondary)"}}>✕</span>
        </div>
        {/* scope 필터 칩 — 전체 / wafer / param / lot (global 제거) */}
        <div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)",display:"flex",gap:4,flexWrap:"wrap",fontSize:10,color:"var(--text-secondary)"}}>
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
            style={{width:"100%",padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,boxSizing:"border-box"}}/>
        </div>
        {/* lot 노트 추가 버튼 — root_lot_id 있을 때만 (param/wafer 는 테이블에서 진입) */}
        {lotId && !noteDraftScope && (
          <div style={{padding:"6px 16px",borderBottom:"1px dashed var(--border)",display:"flex",gap:6,fontSize:10}}>
            <button onClick={()=>setNoteDraftScope({scope:"lot",product:selProd,root_lot_id:lotId})}
              style={{padding:"3px 10px",borderRadius:4,border:"1px solid #16a34a",background:"transparent",color:"rgba(22,163,74,0.95)",fontSize:10,cursor:"pointer"}}>+ LOT 노트 (A{lotId})</button>
          </div>
        )}
        <div style={{flex:1,overflow:"auto",padding:"8px 14px",display:"flex",flexDirection:"column",gap:4}}>
          {filtered.length===0&&<div style={{padding:24,textAlign:"center",color:"var(--text-secondary)",fontSize:11}}>기록된 노트 없음</div>}
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
            return(<div key={n.id} title={n.text} style={{display:"flex",alignItems:"center",gap:6,padding:"4px 6px",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",fontSize:11,minHeight:26}}>
              <span style={{flexShrink:0,fontSize:9,fontWeight:700,padding:"1px 6px",borderRadius:8,background:badge.bg,color:"var(--bg-secondary)",whiteSpace:"nowrap"}}>{badge.txt}</span>
              <span style={{flex:1,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",color:"var(--text-primary)"}}>{n.text}</span>
              <span style={{flexShrink:0,fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace"}}>{n.username}</span>
              <span style={{flexShrink:0,fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace"}}>{time}</span>
              {mine&&<span onClick={()=>deleteNote(n.id)} title="작성자만 삭제 가능" style={{flexShrink:0,cursor:"pointer",fontSize:11,color:"rgba(239,68,68,0.95)",padding:"0 4px"}}>×</span>}
            </div>);
          })}
        </div>
        {/* draft 패널 — scope 별 입력 */}
        {noteDraftScope&&<div style={{padding:"10px 16px",borderTop:"1px solid var(--border)",display:"flex",flexDirection:"column",gap:6}}>
          <div style={{fontSize:10,color:"var(--text-secondary)",display:"flex",alignItems:"center",flexWrap:"wrap",gap:6}}>
            {(() => {
              const sc = noteDraftScope.scope;
              const color = sc==="wafer"?"rgba(59,130,246,0.95)":sc==="param"?"rgba(139,92,246,0.95)":sc==="lot"?"rgba(22,163,74,0.95)":"rgba(107,114,128,0.95)";
              const label = sc==="wafer"?`🏷 W${noteDraftScope.wafer_id}`
                :sc==="param"?`💬 W${noteDraftScope.wafer_id||"?"}·${noteDraftScope.param}`
                :sc==="lot"?`📦 LOT ${noteDraftScope.root_lot_id}`:sc;
              return <>대상: <span style={{color,fontWeight:700}}>{label}</span></>;
            })()}
            {noteDraftScope.scope==="param"&&<span>wafer:
              <input value={noteDraftScope.wafer_id||""} onChange={e=>setNoteDraftScope({...noteDraftScope,wafer_id:e.target.value})} placeholder="wafer_id" style={{marginLeft:4,width:70,padding:"2px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:10}}/>
            </span>}
            <span style={{marginLeft:"auto"}}><span onClick={()=>setNoteDraftScope(null)} style={{cursor:"pointer",color:"var(--text-secondary)",fontSize:10}}>✕ 취소</span></span>
          </div>
          <textarea value={noteDraft} onChange={e=>setNoteDraft(e.target.value)} placeholder="새 노트 내용…" rows={2}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,resize:"vertical",fontFamily:"inherit"}}/>
          <div style={{display:"flex",gap:6,justifyContent:"flex-end"}}>
            {(() => {
              const sc = noteDraftScope.scope;
              const need = sc==="param" ? !!(noteDraftScope.wafer_id||"").trim() : true;
              const canSave = !!noteDraft.trim() && need;
              return <button onClick={addNote} disabled={!canSave}
                style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:11,fontWeight:600,cursor:canSave?"pointer":"not-allowed",opacity:canSave?1:0.5}}>저장 ({me||"anonymous"})</button>;
            })()}
          </div>
        </div>}
        {!noteDraftScope&&<div style={{padding:"8px 16px",borderTop:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)",lineHeight:1.5}}>
          위 목록 아래에 직접 답글/태그를 추가하려면 테이블에서 해당 셀(wafer·param·lot)을 클릭하세요.
        </div>}
      </div>);
    })()}

    {/* v8.8.10: Rulebook 컬럼 매핑 편집 modal — 역할 → 실제 CSV 컬럼명 조정. soft-landing. */}
    {rbEditKind && (
      <div onClick={()=>setRbEditKind(null)}
           style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.55)",zIndex:3000,display:"flex",alignItems:"center",justifyContent:"center"}}>
        <div onClick={e=>e.stopPropagation()}
             style={{background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:10,padding:18,width:500,maxWidth:"92vw",color:"var(--text-primary)"}}>
          <div style={{display:"flex",alignItems:"center",marginBottom:10}}>
            <div style={{fontSize:13,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>🔧 컬럼 매핑 — {rbEditKind}</div>
            <span style={{flex:1}}/>
            <span onClick={()=>setRbEditKind(null)} style={{cursor:"pointer",fontSize:16}}>✕</span>
          </div>
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.5}}>
            역할 → 실제 CSV 컬럼명. 사내 CSV 의 헤더가 다르면 여기만 조정해도 연결 유지됨.
            입력 안 한 값은 기본값으로 저장.
          </div>
          <div style={{display:"flex",flexDirection:"column",gap:6}}>
            {Object.entries(rbSchema.defaults?.[rbEditKind] || {}).map(([role, dfl]) => (
              <label key={role} style={{display:"flex",alignItems:"center",gap:8,fontSize:11}}>
                <span style={{width:140,color:"var(--text-secondary)",fontFamily:"monospace"}}>{role}</span>
                <input value={rbDraftMap[role] ?? dfl}
                  onChange={e=>setRbDraftMap(m=>({...m,[role]:e.target.value}))}
                  style={{flex:1,padding:"5px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,fontFamily:"monospace"}} />
                <span style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace",opacity:0.7,width:120,textAlign:"right"}}>기본: {dfl}</span>
              </label>
            ))}
          </div>
          <div style={{display:"flex",justifyContent:"flex-end",gap:6,marginTop:14}}>
            <button onClick={()=>{
              // 기본값으로 리셋
              setRbDraftMap({...(rbSchema.defaults?.[rbEditKind]||{})});
            }} style={{padding:"6px 12px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>기본값 복원</button>
            <button onClick={()=>setRbEditKind(null)}
              style={{padding:"6px 12px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>취소</button>
            <button onClick={saveSchemaEdit}
              style={{padding:"6px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"var(--bg-secondary)",fontSize:11,fontWeight:600,cursor:"pointer"}}>저장</button>
          </div>
        </div>
      </div>
    )}
    {/* v8.8.15: Rulebook 행 CRUD modal — 제품 스코프 행 편집. 공용(product 빈값) 행은 여기서 건드리지 않음. */}
    {rbRowKind && (
      <div onClick={()=>!rbRowSaving&&setRbRowKind(null)}
           style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.55)",zIndex:3000,display:"flex",alignItems:"center",justifyContent:"center"}}>
        <div onClick={e=>e.stopPropagation()}
             style={{background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:10,padding:18,width:920,maxWidth:"96vw",maxHeight:"88vh",display:"flex",flexDirection:"column",color:"var(--text-primary)"}}>
          <div style={{display:"flex",alignItems:"center",marginBottom:10,gap:8}}>
            <div style={{fontSize:13,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>📘 Rulebook 편집 — {rbRowKind}</div>
            <span style={{fontSize:10,padding:"2px 8px",borderRadius:10,background:"var(--accent-glow)",color:"var(--accent)",fontFamily:"monospace"}}>product = {selProd}</span>
            <span style={{fontSize:10,color:"var(--text-secondary)"}}>행 {rbRowRows.length}개</span>
            <span style={{flex:1}}/>
            <button onClick={rbAddRow} disabled={rbRowSaving}
              style={{padding:"4px 12px",borderRadius:4,border:"1px solid #22c55e",background:"transparent",color:"rgba(34,197,94,0.95)",fontSize:11,cursor:"pointer",fontWeight:600}}>+ 행 추가</button>
            <span onClick={()=>!rbRowSaving&&setRbRowKind(null)} style={{cursor:rbRowSaving?"wait":"pointer",fontSize:16,marginLeft:4}}>✕</span>
          </div>
          <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:8,lineHeight:1.5}}>
            이 제품({selProd})의 행만 이 modal 에서 편집합니다. 저장 시 기존 해당 제품 행은 전체 교체되고, 다른 제품/공용 행은 보존됩니다.
            필수 컬럼: <span style={{fontFamily:"monospace",color:"rgba(245,158,11,0.95)"}}>{(rbRowReq||[]).join(", ")}</span>
          </div>
          <div style={{flex:1,overflow:"auto",border:"1px solid var(--border)",borderRadius:4}}>
            {rbRowRows.length===0 ? (
              <div style={{padding:"30px 20px",textAlign:"center",fontSize:11,color:"var(--text-secondary)",fontStyle:"italic"}}>
                이 제품에 등록된 행이 없습니다. 우측 상단 <b>+ 행 추가</b>로 시작하세요.
              </div>
            ) : (
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:10,fontFamily:"monospace"}}>
                <thead style={{position:"sticky",top:0,background:"var(--bg-card)",zIndex:1}}>
                  <tr>
                    <th style={{padding:"6px 8px",textAlign:"left",borderBottom:"1px solid var(--border)",width:40,color:"var(--text-secondary)"}}>#</th>
                    {(rbRowCols||[]).map(c=>(
                      <th key={c} style={{padding:"6px 8px",textAlign:"left",borderBottom:"1px solid var(--border)",color:(rbRowReq||[]).includes(c)?"rgba(245,158,11,0.95)":"var(--text-secondary)",fontWeight:(rbRowReq||[]).includes(c)?700:500}}>
                        {c}{(rbRowReq||[]).includes(c)&&" *"}{c==="product"&&" 🔒"}
                      </th>
                    ))}
                    <th style={{padding:"6px 8px",borderBottom:"1px solid var(--border)",width:50}}></th>
                  </tr>
                </thead>
                <tbody>
                  {rbRowRows.map((r,i)=>(
                    <tr key={i} style={{borderBottom:"1px solid var(--border)"}}>
                      <td style={{padding:"4px 8px",color:"var(--text-secondary)"}}>{i+1}</td>
                      {(rbRowCols||[]).map(c=>(
                        <td key={c} style={{padding:"2px 4px"}}>
                          <input value={r[c]||""} disabled={c==="product"}
                            onChange={e=>rbUpdateCell(i,c,e.target.value)}
                            placeholder={(rbRowReq||[]).includes(c)?"필수":""}
                            style={{width:"100%",padding:"4px 6px",borderRadius:3,border:`1px solid ${(rbRowReq||[]).includes(c)&&!r[c]?"rgba(239,68,68,0.95)":"var(--border)"}`,background:c==="product"?"var(--bg-card)":"var(--bg-primary)",color:"var(--text-primary)",fontSize:10,fontFamily:"monospace"}}/>
                        </td>
                      ))}
                      <td style={{padding:"2px 4px",textAlign:"center"}}>
                        <button onClick={()=>rbDelRow(i)} title="행 삭제" disabled={rbRowSaving}
                          style={{padding:"2px 8px",borderRadius:3,border:"1px solid #ef4444",background:"transparent",color:"rgba(239,68,68,0.95)",fontSize:10,cursor:"pointer"}}>🗑</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div style={{display:"flex",alignItems:"center",gap:6,marginTop:12}}>
            <div style={{fontSize:9,color:"var(--text-secondary)"}}>* 필수 · 🔒 자동</div>
            <span style={{flex:1}}/>
            <button onClick={()=>!rbRowSaving&&setRbRowKind(null)} disabled={rbRowSaving}
              style={{padding:"6px 14px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:rbRowSaving?"wait":"pointer"}}>취소</button>
            <button onClick={rbSaveRows} disabled={rbRowSaving}
              style={{padding:"6px 16px",borderRadius:4,border:"none",background:rbRowSaving?"var(--border)":"var(--accent)",color:"var(--bg-secondary)",fontSize:11,fontWeight:600,cursor:rbRowSaving?"wait":"pointer"}}>{rbRowSaving?"저장 중…":`저장 (${rbRowRows.length}행)`}</button>
          </div>
        </div>
      </div>
    )}
  </div>);
}
