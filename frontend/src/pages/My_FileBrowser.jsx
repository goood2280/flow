import { useState, useEffect, useCallback } from "react";
import Loading from "../components/Loading";
import S3StatusLight from "../components/S3StatusLight";
import Modal from "../components/Modal";
import { PageGearButton } from "../components/PageGear";
import { toast } from "../components/Toast";
import { dl, sf } from "../lib/api";
import { statusPalette, chartPalette } from "../components/UXKit";
const API="/api/filebrowser";
const PAGE_SIZE=100;
const FB_OK = statusPalette.ok;
const FB_WARN = statusPalette.warn;
const FB_BAD = statusPalette.bad;
const FB_INFO = statusPalette.info;
const FB_AMBER = chartPalette.series[1];
const FB_MUTED = "#64748b";
const FB_DISABLED = "#94a3b8";
const FB_GRID_LINE = "1px solid var(--border)";
const BASE_EDIT_FILE_EXTS = new Set(["csv","parquet"]);
const BASE_EDIT_FILE_SOURCES = new Set(["base_root","db_root"]);
const canEditBaseMeta=(meta)=>{
  if(!meta) return false;
  if(meta.editable===true) return true;
  if(meta.editable===false) return false;
  if(!BASE_EDIT_FILE_EXTS.has((meta.ext||"").toLowerCase())) return false;
  if(!meta.source) return true;
  return BASE_EDIT_FILE_SOURCES.has(meta.source);
};
const detectDelimiterFromGridText=(text)=>{
  const normalize=(raw)=>{
    if(raw==null)return"";
    return String(raw).replace(/\r/g,"");
  };
  const parse=(delimiter)=>{
    const rows=[]; let row=[]; let cell=""; let inQuote=false;
    const src=normalize(text);
    for(let i=0;i<src.length;i++){
      const ch=src[i], nx=src[i+1];
      if(inQuote){
        if(ch==='"'){
          if(nx==='"'){cell+='"'; i++; }
          else inQuote=false;
        }else cell+=ch;
      }else{
        if(ch===delimiter){
          row.push(cell); cell="";
        }else if(ch==='"'){
          inQuote=true;
        }else if(ch==="\n"){
          row.push(cell); rows.push(row); row=[]; cell="";
        }else{
          cell+=ch;
        }
      }
    }
    row.push(cell);
    while(rows.length&&rows[rows.length-1].every(v=>v===""))rows.pop();
    return rows;
  };
  const tab=parse("\t");
  if(text.includes("\t")||tab.some(r=>r.length>1))return[tab,"tab"];
  return[parse(","),"comma"];
};
const normalizeGridRows=(rows,width,fill="")=>{
  return rows.map(r=>{
    const src=(r||[]).map(v=>v==null?"":String(v));
    if(src.length<width)return src.concat(Array(width-src.length).fill(fill));
    if(src.length>width)return src.slice(0,width);
    return src;
  });
};
const isHeaderMatch=(pastedRows,cols)=>{
  if(!pastedRows||!pastedRows.length||!cols.length)return false;
  const first=(pastedRows[0]||[]).map(v=>String(v||"").trim().toLowerCase()).filter(Boolean);
  if(!first.length)return false;
  if(first.length>cols.length)return false;
  for(let i=0;i<first.length;i++){
    if(first[i]!==String(cols[i]||"").trim().toLowerCase())return false;
  }
  return true;
};
const csvEscape=(value,delimiter)=>{
  const text=value==null?"":String(value);
  if(text==="")return"";
  if(/[",\n\r\t]/.test(text) || text.includes(delimiter)){
    return "\""+text.replace(/"/g,'""')+"\"";
  }
  return text;
};
const buildSaveText=(cols,rows,delimiter,includeHeader)=>{
  const delim=delimiter==="tab"?"\t":",";
  const source = includeHeader ? [cols, ...rows] : rows;
  return source.map(r=>r.map(v=>csvEscape(v,delim)).join(delim)).join("\n");
};
const defaultDelimiterForFile=(file)=>{
  const name=String(file||"").toLowerCase();
  if(name.endsWith(".csv"))return"comma";
  return"tab";
};
const splitRuleList=(value)=>String(value||"").split(/[\n,]/).map(v=>v.trim()).filter(Boolean);
const joinRuleList=(value)=>Array.isArray(value)?value.join(", "):"";
const parseUniqueKeys=(text)=>String(text||"").split(/\n/).map(line=>line.split(",").map(v=>v.trim()).filter(Boolean)).filter(cols=>cols.length);
const formatUniqueKeys=(value)=>Array.isArray(value)?value.map(cols=>(cols||[]).join(", ")).join("\n"):"";
const parseEnumLines=(text)=>{
  const out={};
  String(text||"").split(/\n/).forEach(line=>{
    const [col,...rest]=line.split("=");
    const key=String(col||"").trim();
    if(!key)return;
    const vals=rest.join("=").split(/[|,]/).map(v=>v.trim()).filter(Boolean);
    if(vals.length)out[key]=vals;
  });
  return out;
};
const formatEnumLines=(value)=>Object.entries(value||{}).map(([col,vals])=>`${col}=${(vals||[]).join("|")}`).join("\n");
const parseNumericLines=(text)=>{
  const out={};
  String(text||"").split(/\n/).forEach(line=>{
    const parts=line.trim().split(/\s+/).filter(Boolean);
    const col=parts.shift();
    if(!col)return;
    const spec={};
    parts.forEach(part=>{
      const [k,v]=part.split("=");
      if(k==="min"&&v!=="")spec.min=Number(v);
      else if(k==="max"&&v!=="")spec.max=Number(v);
      else if(k==="integer")spec.integer=String(v||"true").toLowerCase()!=="false";
    });
    out[col]=spec;
  });
  return out;
};
const formatNumericLines=(value)=>Object.entries(value||{}).map(([col,spec])=>{
  const parts=[col];
  if(spec?.min!==undefined)parts.push(`min=${spec.min}`);
  if(spec?.max!==undefined)parts.push(`max=${spec.max}`);
  if(spec?.integer)parts.push("integer=true");
  return parts.join(" ");
}).join("\n");
const parseRegexLines=(text)=>{
  const out={};
  String(text||"").split(/\n/).forEach(line=>{
    const [col,...rest]=line.split("=");
    const key=String(col||"").trim();
    const pattern=rest.join("=").trim();
    if(key&&pattern)out[key]=pattern;
  });
  return out;
};
const formatRegexLines=(value)=>Object.entries(value||{}).map(([col,pattern])=>`${col}=${pattern}`).join("\n");
const parseConditionLines=(text)=>String(text||"").split(/\n/).map(line=>{
  const [expr,...rest]=line.split("=>");
  return {expr:String(expr||"").trim(),message:rest.join("=>").trim()};
}).filter(x=>x.expr);
const formatConditionLines=(value)=>Array.isArray(value)?value.map(x=>`${x.expr||""}${x.message?" => "+x.message:""}`).join("\n"):"";
const parseSortLines=(text)=>String(text||"").split(/\n/).map(line=>{
  const [column,direction="asc",type="string",nulls="last"]=line.trim().split(/\s+/).filter(Boolean);
  return column?{column,direction,type,nulls}:null;
}).filter(Boolean);
const formatSortLines=(value)=>Array.isArray(value)?value.map(x=>[x.column,x.direction||"asc",x.type||"string",x.nulls||"last"].filter(Boolean).join(" ")).join("\n"):"";
const parseOrderedByLines=(text)=>{
  const keys=parseSortLines(text);
  return keys.length?{keys}:null;
};
const formatOrderedByLines=(value)=>{
  if(Array.isArray(value))return formatSortLines(value);
  return formatSortLines(value?.keys||[]);
};
const LOT_PROGRESS_COLUMNS=["root_lot_id","lot_id","wafer_id","step_id","process_id","tkin_time","tkout_time","time","update_time","eqp_id","chamber_id","ppid"];
const defaultLotProgressColumnMapping=()=>LOT_PROGRESS_COLUMNS.reduce((acc,col)=>({...acc,[col]:col}),{});
const normalizeLotProgressColumnMapping=(value={})=>{
  const base=defaultLotProgressColumnMapping();
  LOT_PROGRESS_COLUMNS.forEach(col=>{
    const text=String(value?.[col]??"").trim();
    if(text)base[col]=text;
  });
  return base;
};
const emptyRuleForm=()=>({required_columns:"",not_empty:"",unique_keys:"",enums:"",numeric:"",date:"",regex:"",conditions:"",ordered_by:"",sort:""});
const ruleToForm=(rule={})=>({
  required_columns:joinRuleList(rule.required_columns),
  not_empty:joinRuleList(rule.not_empty),
  unique_keys:formatUniqueKeys(rule.unique_keys),
  enums:formatEnumLines(rule.enums),
  numeric:formatNumericLines(rule.numeric),
  date:joinRuleList(rule.date),
  regex:formatRegexLines(rule.regex),
  conditions:formatConditionLines(rule.conditions),
  ordered_by:formatOrderedByLines(rule.ordered_by),
  sort:formatSortLines(rule.sort),
});
const formToRule=(form={})=>{
  const rule={};
  const required=splitRuleList(form.required_columns);
  const notEmpty=splitRuleList(form.not_empty);
  const unique=parseUniqueKeys(form.unique_keys);
  const enums=parseEnumLines(form.enums);
  const numeric=parseNumericLines(form.numeric);
  const date=splitRuleList(form.date);
  const regex=parseRegexLines(form.regex);
  const conditions=parseConditionLines(form.conditions);
  const orderedBy=parseOrderedByLines(form.ordered_by);
  const sort=parseSortLines(form.sort);
  if(required.length)rule.required_columns=required;
  if(notEmpty.length)rule.not_empty=notEmpty;
  if(unique.length)rule.unique_keys=unique;
  if(Object.keys(enums).length)rule.enums=enums;
  if(Object.keys(numeric).length)rule.numeric=numeric;
  if(date.length)rule.date=date;
  if(Object.keys(regex).length)rule.regex=regex;
  if(conditions.length)rule.conditions=conditions;
  if(orderedBy)rule.ordered_by=orderedBy;
  if(sort.length)rule.sort=sort;
  return rule;
};
const mergeRuleList=(a=[],b=[])=>{
  const out=[];const seen=new Set();
  [...(a||[]),...(b||[])].forEach(v=>{const text=String(v||"").trim();const key=text.toLowerCase();if(text&&!seen.has(key)){seen.add(key);out.push(text);}});
  return out;
};
const mergeUniqueKeys=(a=[],b=[])=>{
  const out=[];const seen=new Set();
  [...(a||[]),...(b||[])].forEach(cols=>{
    const clean=(cols||[]).map(v=>String(v||"").trim()).filter(Boolean);
    const key=clean.map(v=>v.toLowerCase()).join("\u0001");
    if(clean.length&&!seen.has(key)){seen.add(key);out.push(clean);}
  });
  return out;
};
const mergeRuleMaps=(a={},b={})=>({...a,...b});
const mergeEnumMaps=(a={},b={})=>{
  const out={...a};
  Object.entries(b||{}).forEach(([col,vals])=>{out[col]=mergeRuleList(out[col]||[],vals||[]);});
  return out;
};
const mergeConditions=(a=[],b=[])=>{
  const out=[];const seen=new Set();
  [...(a||[]),...(b||[])].forEach(x=>{const item={expr:String(x?.expr||"").trim(),message:String(x?.message||"").trim()};const key=item.expr+"\u0001"+item.message;if(item.expr&&!seen.has(key)){seen.add(key);out.push(item);}});
  return out;
};
const mergeCsvRule=(base={},draft={})=>{
  const out={...base};
  const listKeys=["required_columns","not_empty","date"];
  listKeys.forEach(k=>{if(draft[k]?.length)out[k]=mergeRuleList(out[k]||[],draft[k]);});
  if(draft.unique_keys?.length)out.unique_keys=mergeUniqueKeys(out.unique_keys||[],draft.unique_keys);
  if(draft.enums&&Object.keys(draft.enums).length)out.enums=mergeEnumMaps(out.enums||{},draft.enums);
  ["numeric","regex"].forEach(k=>{if(draft[k]&&Object.keys(draft[k]).length)out[k]=mergeRuleMaps(out[k]||{},draft[k]);});
  if(draft.conditions?.length)out.conditions=mergeConditions(out.conditions||[],draft.conditions);
  if(draft.ordered_by?.keys?.length)out.ordered_by=draft.ordered_by;
  if(draft.sort?.length)out.sort=draft.sort;
  return out;
};
const ruleCount=(summary={})=>Object.values(summary||{}).reduce((sum,v)=>sum+Number(v||0),0);
const ruleSummaryGroups=(rule={})=>{
  const groups=[];
  const list=(key,label,values)=>{if(values?.length)groups.push({key,label,items:values.map(v=>String(v))});};
  list("required_columns","필수 컬럼",rule.required_columns||[]);
  list("not_empty","빈 값 금지",rule.not_empty||[]);
  if(rule.unique_keys?.length)groups.push({key:"unique_keys",label:"중복 금지",items:rule.unique_keys.map(cols=>(cols||[]).join(" + "))});
  if(rule.enums&&Object.keys(rule.enums).length)groups.push({key:"enums",label:"허용값",items:Object.entries(rule.enums).map(([col,vals])=>`${col}: ${(vals||[]).join("|")}`)});
  if(rule.numeric&&Object.keys(rule.numeric).length)groups.push({key:"numeric",label:"숫자",items:Object.entries(rule.numeric).map(([col,spec])=>`${col}${spec?.min!==undefined?" min="+spec.min:""}${spec?.max!==undefined?" max="+spec.max:""}${spec?.integer?" integer":""}`)});
  list("date","날짜/시간",rule.date||[]);
  if(rule.regex&&Object.keys(rule.regex).length)groups.push({key:"regex",label:"정규식",items:Object.entries(rule.regex).map(([col,pattern])=>`${col}: ${pattern}`)});
  if(rule.conditions?.length)groups.push({key:"conditions",label:"조건",items:rule.conditions.map(x=>`${x.expr||""}${x.message?" => "+x.message:""}`)});
  if(rule.ordered_by?.keys?.length)groups.push({key:"ordered_by",label:"현재 순서 검증",items:rule.ordered_by.keys.map(x=>[x.column,x.direction||"asc",x.type||"string",x.nulls||"last"].join(" "))});
  if(rule.sort?.length)groups.push({key:"sort",label:"저장 정렬",items:rule.sort.map(x=>[x.column,x.direction||"asc",x.type||"string",x.nulls||"last"].join(" "))});
  return groups;
};
const VALIDATION_RULE_KEYS=new Set(["required_columns","not_empty","unique_keys","enums","numeric","date","regex","conditions","ordered_by"]);
const ruleSummarySections=(rule={})=>{
  const groups=ruleSummaryGroups(rule);
  return [
    {key:"validation",label:"검증로직",groups:groups.filter(g=>VALIDATION_RULE_KEYS.has(g.key))},
    {key:"sort",label:"정렬로직",groups:groups.filter(g=>g.key==="sort")},
  ];
};
const validationRuleFields=[
  ["required_columns","필수 컬럼","id, name"],
  ["not_empty","빈 값 금지","id, name"],
  ["unique_keys","중복 금지 키","product, lot_id, wafer_id"],
  ["enums","허용값","status=OK|NG|HOLD"],
  ["numeric","숫자","rank min=1 max=999 integer=true"],
  ["date","날짜/시간 컬럼","created_at, updated_at"],
  ["regex","정규식","code=[A-Z]{2}\\d{2}"],
  ["conditions","AND 통과 조건","end_time >= start_time => 종료가 시작보다 빠를 수 없습니다"],
  ["ordered_by","현재 순서 검증","product asc string last\nfeature_name asc leading_number last\nrule_order asc rule_order last"],
];
const sortRuleFields=[
  ["sort","저장 정렬","product asc string last\nfeature_name asc leading_number last\nrule_order asc rule_order last"],
];
const normalizePageableSource = (file) => (file||"");
function formatSize(b){if(!b)return"-";if(b<1024)return b+" B";if(b<1048576)return(b/1024).toFixed(1)+" KB";if(b<1073741824)return(b/1048576).toFixed(1)+" MB";return(b/1073741824).toFixed(2)+" GB";}
function revStyle(rev){
  if(rev==="추가")return{bg:"#dcfce7",fg:"#166534",line:FB_OK.fg};
  if(rev==="삭제")return{bg:"#fee2e2",fg:"#991b1b",line:FB_BAD.fg};
  if(rev==="수정")return{bg:"#fef9c3",fg:"#854d0e",line:FB_AMBER};
  return{bg:"var(--bg-primary)",fg:"var(--text-primary)",line:"var(--border)"};
}
function versionChangeLabel(summary){
  const s=summary&&typeof summary==="object"?summary:{};
  const modified=Number(s.modified_rows||0);
  const added=Number(s.added_rows||0);
  const deleted=Number(s.deleted_rows||0);
  const parts=[];
  if(modified)parts.push(`수정 ${modified}행`);
  if(added)parts.push(`추가 ${added}행`);
  if(deleted)parts.push(`삭제 ${deleted}행`);
  const addedCols=Array.isArray(s.added_columns)?s.added_columns.length:Number(s.added_columns||s.added_columns_count||0);
  const removedCols=Array.isArray(s.removed_columns)?s.removed_columns.length:Number(s.removed_columns||s.removed_columns_count||0);
  if(addedCols||removedCols){
    parts.push(`열 ${addedCols?`+${addedCols}`:""}${addedCols&&removedCols?"/":""}${removedCols?`-${removedCols}`:""}`);
  }
  const colDelta=Number(s.columns_delta||0);
  if(!addedCols&&!removedCols&&colDelta)parts.push(`열 ${colDelta>0?"+":""}${colDelta}`);
  if(parts.length)return parts.join(" / ");
  const raw=String(s.label||"");
  if(raw==="initial snapshot")return"초기 버전";
  if(raw==="content updated")return"내용 수정";
  if(raw==="no data change")return"변경 없음";
  if(/cells changed/i.test(raw)||/\brows\b/i.test(raw)){
    const rd=Number(s.rows_delta||0);
    if(rd>0)return`추가 ${rd}행`;
    if(rd<0)return`삭제 ${Math.abs(rd)}행`;
    return"내용 수정";
  }
  return raw||"-";
}
function diffTableCountLabel(diffTable){
  const c=diffTable?.counts||{};
  const modified=Number(c.modified||0);
  const added=Number(c.added||0);
  const deleted=Number(c.deleted||0);
  const parts=[];
  if(modified)parts.push(`수정 ${modified}행`);
  if(added)parts.push(`추가 ${added}행`);
  if(deleted)parts.push(`삭제 ${deleted}행`);
  const addedCols=Number(diffTable?.added_columns?.length||diffTable?.added_columns_count||0);
  const removedCols=Number(diffTable?.removed_columns?.length||diffTable?.removed_columns_count||0);
  if(addedCols||removedCols)parts.push(`열 ${addedCols?`+${addedCols}`:""}${addedCols&&removedCols?"/":""}${removedCols?`-${removedCols}`:""}`);
  return parts.join(" / ");
}
function tableShapeLabel(profile, label=""){
  const rows=profile?.rows??"-";
  const cols=profile?.column_count??profile?.columns??"-";
  return `${label}${label?" ":""}${rows}행 ${cols}열`;
}

function LazyAwsPanel({ user, compact = false }) {
  const [Comp, setComp] = useState(null);
  useEffect(() => {
    let alive = true;
    import("../components/AwsPanel").then(m => {
      if (alive) setComp(() => m.default);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!Comp) return <div style={{fontSize:14,color:"var(--text-secondary)",padding:12}}>AWS 설정 로딩...</div>;
  return <Comp user={user} compact={compact} />;
}

export default function My_FileBrowser({user,onNavigate}){
  const[roots,setRoots]=useState([]);const[rootPqs,setRootPqs]=useState([]);const[selRoot,setSelRoot]=useState("");
  const[products,setProducts]=useState([]);const[selProd,setSelProd]=useState("");const[sideLoading,setSideLoading]=useState(true);
  const[data,setData]=useState(null);const[sql,setSql]=useState("");const[sortSpec,setSortSpec]=useState(null);const[loading,setLoading]=useState(false);
  const[tab,setTab]=useState("data");const[colSearch,setColSearch]=useState("");const[showGuide,setShowGuide]=useState(false);const[mode,setMode]=useState("hive");
  const[selRootPq,setSelRootPq]=useState("");
  // v4.1: scope switcher — "DB" (hive-flat) or "Base" (single-file rulebook/wide parquet).
  // `scopes` keyed array from /api/filebrowser/scopes; `scope` = active key.
  const[scopes,setScopes]=useState([]);const[scope,setScope]=useState("DB");
  const[baseFiles,setBaseFiles]=useState([]);const[selBaseFile,setSelBaseFile]=useState("");
  const[baseDir,setBaseDir]=useState("");
  // v4.1: raw preview for json/md so the main pane can render them natively
  // (pretty-printed JSON / markdown-as-pre) instead of stuffing text into the table.
  const[baseRaw,setBaseRaw]=useState(null);
  const[selBaseMeta,setSelBaseMeta]=useState(null);
  // Column selection state
  const[selectedCols,setSelectedCols]=useState([]);const[colSelectMode,setColSelectMode]=useState(false);
  const[page,setPage]=useState(0);
  const[error,setError]=useState("");
  const[isBaseEditing,setIsBaseEditing]=useState(false);
  const[editRows,setEditRows]=useState([]);
  const[editCols,setEditCols]=useState([]);
  const[editOriginRows,setEditOriginRows]=useState([]);
  const[pasteMode,setPasteMode]=useState("replace");
  const[saveDelimiter,setSaveDelimiter]=useState("tab");
  const[includeHeader,setIncludeHeader]=useState(true);
  const[selectedEditCell,setSelectedEditCell]=useState({r:0,c:0});
  const[baseVersions,setBaseVersions]=useState([]);
  const[baseVersionCap,setBaseVersionCap]=useState(30);
  const[baseVersioned,setBaseVersioned]=useState(false);
  const[baseVersionLoading,setBaseVersionLoading]=useState(false);
  const[baseVersionMsg,setBaseVersionMsg]=useState("");
  const[baseVersionPreview,setBaseVersionPreview]=useState(null);
  const[baseVersionPreviewLoading,setBaseVersionPreviewLoading]=useState(false);
  const[baseVersionFilter,setBaseVersionFilter]=useState("");
  const[baseCurrentProfile,setBaseCurrentProfile]=useState(null);
  const[rawEditing,setRawEditing]=useState(false);
  const[rawEditText,setRawEditText]=useState("");
  const[schemaSnapshotMsg,setSchemaSnapshotMsg]=useState("");
  const[schemaSnapshots,setSchemaSnapshots]=useState([]);
  // S3 sync status map (public endpoint) — powers sidebar traffic-light dots
  const[s3Status,setS3Status]=useState({});
  useEffect(()=>{
    const load=()=>sf("/api/s3ingest/status-by-target").then(d=>{if(d&&d.by_target)setS3Status(d.by_target);}).catch(()=>{});
    load();
    const t=setInterval(load,30000);
    return()=>clearInterval(t);
  },[]);
  // v8.8.2: path 기반 lookup — 정확 매칭 없으면 상위 경로(예: "1.RAWDATA_DB") 에서 상속.
  // "제품명" 을 키로 넘기면 sidebar 제품 리스트 렌더. "DB/PROD" 도 지원.
  const s3LookupPath=(path)=>{
    if(!s3Status||!path)return null;
    if(s3Status[path])return{info:s3Status[path],from:path,inherited:false};
    // 상위 segment 폴백 — "a/b/c" → "a/b" → "a" 순.
    const parts=String(path).split("/").filter(Boolean);
    for(let i=parts.length-1;i>0;i--){
      const anc=parts.slice(0,i).join("/");
      if(s3Status[anc])return{info:s3Status[anc],from:anc,inherited:true};
    }
    return null;
  };
  const s3Light=(path)=>{
    const found=s3LookupPath(path);
    const info=found?.info;
    const inh=found?.inherited;
    const fromLabel=found?.from;
    const direction=(info?.direction||"download").toLowerCase();
    const last=info?(info.last_end||info.last_start):null;
    const lastStr=last?last.slice(0,16).replace("T"," "):"-";
    const latestItemAt=info?.latest_item_at||null;
    const latestItemStr=latestItemAt?latestItemAt.slice(5,16).replace("T"," "):"-";
    const latestItemAge=Number.isFinite(Number(info?.latest_item_age_hours))?Number(info.latest_item_age_hours):null;
    const latestItemStaleRaw=!!info?.latest_item_stale_6h;
    const freshnessState=String(info?.freshness_state||"");
    const latestItemPath=info?.latest_item_relpath||"";
    const ageH=last?(Date.now()-new Date(last).getTime())/3600000:Infinity;
    const nextStr=info&&info.next_due?info.next_due.slice(0,16).replace("T"," "):(info&&info.interval_min>0?"계산중":"수동 실행만");
    const directionLabel=direction==="upload"?"업로드":(direction==="mixed"?"혼합":"다운로드");
    const directionArrow=direction==="upload"?"↑":(direction==="mixed"?"↕":"↓");
    const st=info?.last_status||"never";
    const syncFresh=st==="ok"&&isFinite(ageH)&&ageH<=6;
    const latestItemStale=freshnessState==="stale_item"||(latestItemStaleRaw&&!syncFresh&&freshnessState!=="ok");
    if(!info)return{color:FB_BAD.fg,tip:"S3 동기화 미설정 — File Browser 우하단 ⚙️(admin) 에서 설정하세요",directionLabel:"미설정",directionArrow:"·",freshLabel:"-",latestItemStale:false};
    if(info.is_running)return{color:FB_INFO.fg,tip:(inh?`상위 경로 '${fromLabel}' 에서 상속\n`:"")+`S3 ${directionLabel} 실행 중…\n이전 실행: ${lastStr}\n최신 항목: ${latestItemStr}`,directionLabel,directionArrow,freshLabel:latestItemStr,latestItemStale:false};
    let color,line;
    if(st==="error"){color=FB_BAD.fg;line="실패 (exit="+(info.last_exit_code??"?")+")";}
    else if(st==="ok"&&latestItemStale){color=FB_BAD.fg;line="최신 항목 지연 ("+(latestItemAge!=null?latestItemAge.toFixed(1)+"시간":"6시간+")+")";}
    else if(st==="ok"&&isFinite(ageH)&&ageH<=6){color=FB_OK.fg;line="정상 (최근 "+ageH.toFixed(1)+"시간)";}
    else if(st==="ok"){color=chartPalette.series[5];line="오래됨 ("+(isFinite(ageH)?Math.floor(ageH)+"시간 경과":"기록 없음")+")";}
    else{color=FB_BAD.fg;line="실행 기록 없음";}
    const prefix=inh?`(상위 '${fromLabel}' 상속) `:"";
    const latestItemLine=`최신 항목: ${latestItemStr}${latestItemPath?` (${latestItemPath})`:""}${latestItemAge!=null?` / ${latestItemAge.toFixed(1)}h 전`:""}`;
    return{color,inherited:!!inh,directionLabel,directionArrow,latestItemStale,freshLabel:latestItemStale?"6h+":latestItemStr,tip:prefix+`S3 ${directionLabel} — `+line+"\n마지막 실행: "+lastStr+"\n"+latestItemLine+"\n다음: "+nextStr+(info.interval_min>0?" ("+info.interval_min+"분 주기)":"")};
  };
  // 상속 상태일 때는 내부에 점(·) 을 표시해 구분.
  const lightDot=(name)=>{const l=s3Light(name);return(
    <span title={l.tip} style={{display:"inline-flex",alignItems:"center",justifyContent:"center",minWidth:16,height:16,padding:"0 4px",borderRadius:999,background:l.color,flexShrink:0,boxShadow:"0 0 4px "+l.color+"66",border:l.latestItemStale?"1px solid var(--bg-secondary)":(l.inherited?"1px dashed rgba(255,255,255,0.55)":"none"),color:"var(--bg-secondary)",fontSize:14,fontWeight:800,fontFamily:"monospace",lineHeight:1}}>
      {l.directionArrow||"·"}
    </span>
  );};
  const lightFreshText=(name)=>{
    const l=s3Light(name);
    if(!l?.freshLabel||l.freshLabel==="-")return null;
    return (
      <span title={l.tip} style={{fontSize:11,fontFamily:"monospace",fontWeight:700,color:l.latestItemStale?FB_BAD.fg:FB_MUTED,flexShrink:0,maxWidth:62,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
        {l.freshLabel}
      </span>
    );
  };

  // S3 ingest admin modal state
  const isAdmin=user?.role==="admin";
  const pageAdmins=(user?.page_admins)||[];
  const isFileBrowserAdmin=isAdmin
    || (Array.isArray(pageAdmins)&&pageAdmins.includes("filebrowser"))
    || (!!pageAdmins?.filebrowser===true)
    || (typeof pageAdmins==="string"&&pageAdmins.split(",").map(s=>s.trim()).includes("filebrowser"));
  const loadBaseVersions=useCallback((file=selBaseFile)=>{
    if(!file){
      setBaseVersions([]);setBaseVersioned(false);setBaseVersionMsg("");
      setBaseCurrentProfile(null);
      return Promise.resolve(null);
    }
    setBaseVersionLoading(true);
    return sf(API+"/base-file/versions?file="+encodeURIComponent(file))
      .then(d=>{
        setBaseVersions(d.versions||[]);
        setBaseVersionCap(d.cap||30);
        setBaseVersioned(!!d.versioned);
        setBaseCurrentProfile(d.current_profile||null);
        setBaseVersionMsg("");
        return d;
      })
      .catch(e=>{
        setBaseVersions([]);
        setBaseVersioned(false);
        setBaseCurrentProfile(null);
        setBaseVersionMsg(e.message||"버전 이력 로드 실패");
        return null;
      })
      .finally(()=>setBaseVersionLoading(false));
  },[selBaseFile]);
  useEffect(()=>{
    if(mode==="base"&&selBaseFile)loadBaseVersions(selBaseFile);
    else{setBaseVersions([]);setBaseVersioned(false);setBaseCurrentProfile(null);setBaseVersionMsg("");setBaseVersionPreview(null);setRawEditing(false);setRawEditText("");}
  },[mode,selBaseFile,loadBaseVersions]);
  const previewBaseVersion=async(version)=>{
    if(!selBaseFile||!version)return;
    setBaseVersionPreviewLoading(true);
    setBaseVersionMsg("");
    try{
      const d=await sf(API+"/base-file/version-content?file="+encodeURIComponent(selBaseFile)+"&version="+encodeURIComponent(version));
      setBaseVersionPreview(d);
    }catch(e){setBaseVersionMsg(e.message||"버전 미리보기 실패");}
    finally{setBaseVersionPreviewLoading(false);}
  };
  const migrateLegacyHistory=async()=>{
    if(!selBaseFile)return;
    if(!isFileBrowserAdmin){toast.warn("Admin 또는 FileBrowser page_admin 만 migration 할 수 있습니다.");return;}
    const note=window.prompt("legacy .history migration 사유를 입력하세요.", "Migrate legacy .history to EDM versions");
    if(note===null)return;
    try{
      const d=await sf(API+"/base-file/migrate-history",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({file:selBaseFile,username:user?.username||"",note})});
      setBaseVersionMsg(`migration 완료 · migrated ${d.migrated||0} / skipped ${d.skipped||0}`);
      loadBaseVersions(selBaseFile);
    }catch(e){setBaseVersionMsg(e.message||"migration 실패");}
  };
  const rollbackBaseVersion=async(version)=>{
    if(!selBaseFile||!version)return;
    if(!isFileBrowserAdmin){toast.warn("Admin 또는 FileBrowser page_admin 만 롤백할 수 있습니다.");return;}
    if(!window.confirm(`${selBaseFile}\n${version} 버전으로 롤백하시겠습니까?\n현재 파일은 pre-rollback 버전으로 먼저 보존됩니다.`))return;
    const note=window.prompt("롤백 사유를 입력하세요.", `Rollback to ${version}`);
    if(note===null)return;
    try{
      const d=await sf(API+"/base-file/rollback",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({file:selBaseFile,version,username:user?.username||"",note})});
      setBaseVersionMsg(`롤백 완료: ${d.rolled_back_to||version}`);
      setIsBaseEditing(false);
      setData(null);setBaseRaw(null);
      setBaseVersionPreview(null);
      loadBaseVersions(selBaseFile);
      loadBaseFileView(selBaseFile,{});
    }catch(e){setBaseVersionMsg(e.message||"롤백 실패");}
  };
  const canEditRawBase=mode==="base"&&!!baseRaw&&baseVersioned&&isFileBrowserAdmin&&["yaml","json","md","txt"].includes(String(baseRaw.kind||"").toLowerCase());
  const saveRawBaseFile=async()=>{
    if(!canEditRawBase||!selBaseFile)return;
    const note=window.prompt("변경 사유를 입력하세요.", "Raw EDM edit");
    if(note===null)return;
    try{
      const d=await sf(API+"/base-file/text-save",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({file:selBaseFile,text:rawEditText,username:user?.username||"",note})});
      setBaseVersionMsg(`저장 완료${d.version?.version?`: ${d.version.version}`:""}`);
      setRawEditing(false);
      setBaseRaw(null);
      loadBaseVersions(selBaseFile);
      loadBaseFileView(selBaseFile,{});
    }catch(e){setBaseVersionMsg(e.message||"저장 실패");}
  };
  const saveSchemaSnapshot=async()=>{
    const cols=(data?.all_columns||data?.columns||[]).map(c=>String(c||"")).filter(Boolean);
    if(!cols.length){setSchemaSnapshotMsg("저장할 schema column 이 없습니다.");return;}
    const isDbMode=mode!=="base"&&mode!=="root";
    const colLower=new Map(cols.map(c=>[c.toLowerCase(),c]));
    const joinKeyCandidates=["product","process_id","root_lot_id","lot_id","wafer_id","lot_wf","LOT_WF","shot_x","shot_y","item_id","step_id","step_seq","subitem_id"];
    const joinKeys=joinKeyCandidates.map(k=>colLower.get(k.toLowerCase())).filter(Boolean);
    const grain=colLower.has("shot_x")&&colLower.has("shot_y")?"shot":(colLower.has("wafer_id")?"wafer":(colLower.has("root_lot_id")||colLower.has("lot_id")?"lot":"row"));
    const sourceType=mode==="base"?"base_file":(mode==="root"?"root_parquet":"db_product");
    const body={
      source_type:sourceType,
      root:isDbMode?selRoot:"",
      product:isDbMode?selProd:"",
      file:mode==="base"?selBaseFile:(mode==="root"?selRootPq:""),
      columns:cols,
      dtypes:data?.dtypes||{},
      grain,
      join_keys:joinKeys,
      total_rows:data?.total_rows,
      username:user?.username||"",
      note:"FileBrowser schema snapshot",
    };
    try{
      const d=await sf(API+"/schema/snapshot",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const added=d.diff?.added_columns?.length||0,removed=d.diff?.removed_columns?.length||0,dtype=d.diff?.dtype_changes?.length||0;
      setSchemaSnapshotMsg(`schema 저장 완료 · added ${added} / removed ${removed} / dtype ${dtype}`);
      loadSchemaSnapshots(body);
    }catch(e){setSchemaSnapshotMsg(e.message||"schema 저장 실패");}
  };
  const loadSchemaSnapshots=async(sourceBody=null)=>{
    const isDbMode=mode!=="base"&&mode!=="root";
    const body=sourceBody||{
      source_type:mode==="base"?"base_file":(mode==="root"?"root_parquet":"db_product"),
      root:isDbMode?selRoot:"",
      product:isDbMode?selProd:"",
      file:mode==="base"?selBaseFile:(mode==="root"?selRootPq:""),
    };
    const q=new URLSearchParams({source_type:body.source_type||"",root:body.root||"",product:body.product||"",file:body.file||""});
    try{
      const d=await sf(API+"/schema/snapshots?"+q.toString());
      setSchemaSnapshots(d.snapshots||[]);
      const added=d.diff?.added_columns?.length||0,removed=d.diff?.removed_columns?.length||0,dtype=d.diff?.dtype_changes?.length||0;
      setSchemaSnapshotMsg((d.snapshots||[]).length?`schema 이력 ${(d.snapshots||[]).length}개 · latest diff +${added}/-${removed} dtype ${dtype}`:"schema 이력이 없습니다.");
    }catch(e){setSchemaSnapshotMsg(e.message||"schema 이력 로드 실패");}
  };
  const[s3Open,setS3Open]=useState(false);
  const[s3Items,setS3Items]=useState([]);
  const[s3Avail,setS3Avail]=useState({dbs:[],root_parquets:[]});
  const[s3Tab,setS3Tab]=useState("items"); // items | add | history
  const[s3Hist,setS3Hist]=useState([]);
  const[s3Form,setS3Form]=useState(null);
  const[s3AwsOk,setS3AwsOk]=useState(true);
  const[s3Tick,setS3Tick]=useState(0);
  const[s3Detail,setS3Detail]=useState(null); // show last_output_tail
  const[s3Now,setS3Now]=useState(Date.now());
  const[s3Profiles,setS3Profiles]=useState([]); // v8.7.9 AWS profile (key) list
  const closeS3Settings=()=>{
    setS3Open(false);
    setS3Form(null);
    setS3Detail(null);
  };
  const toggleS3Settings=()=>{
    const nextOpen=!s3Open;
    if(nextOpen&&!isAdmin)setS3Tab("folder");
    setS3Open(nextOpen);
  };
  const[fbSettings,setFbSettings]=useState({csv_full_read_max_bytes:10485760,csv_download_max_rows:500000,csv_download_max_bytes:100000000,sql_query_max_source_bytes:5368709120,preview_max_columns:100,preview_max_rows:100,schema_column_page_size:200,csv_rules:{},hidden_db_dirs:["cache","reformatter"],versioned_single_file_dirs:["reformatter"],auto_s3_upload_on_save:false,can_manage:false});
  const[fbAutoS3Upload,setFbAutoS3Upload]=useState(false);
  const[fbThresholdMb,setFbThresholdMb]=useState("10");
  const[fbDownloadMb,setFbDownloadMb]=useState("100");
  const[fbDownloadRows,setFbDownloadRows]=useState("500000");
  const[fbHiddenDbDirsText,setFbHiddenDbDirsText]=useState("cache\nreformatter");
  const[fbVersionedDirsText,setFbVersionedDirsText]=useState("reformatter");
  const[fbSettingsMsg,setFbSettingsMsg]=useState("");
  const[fbSettingsLoading,setFbSettingsLoading]=useState(false);
  const[fbSelectedFile,setFbSelectedFile]=useState("");
  const[fbRuleForm,setFbRuleForm]=useState(emptyRuleForm());
  const[fbValidation,setFbValidation]=useState(null);
  const[fbSettingsLlmPrompt,setFbSettingsLlmPrompt]=useState("현재 CSV 컬럼 기준으로 필수 컬럼, 빈 값 금지, unique key 검증로직과 저장 시 정렬로직 초안 만들어줘");
  const[fbSettingsLlmBusy,setFbSettingsLlmBusy]=useState(false);
  const[fbSettingsLlmDraft,setFbSettingsLlmDraft]=useState(null);
  const[fbCacheStatus,setFbCacheStatus]=useState({lot_progress:null});
  const[fbCacheBusy,setFbCacheBusy]=useState("");
  const[fbCacheMsg,setFbCacheMsg]=useState("");
  const[fbCacheInterval,setFbCacheInterval]=useState("30");
  const[fbCacheSourceRoot,setFbCacheSourceRoot]=useState("");
  const[fbCacheColumnMapping,setFbCacheColumnMapping]=useState(defaultLotProgressColumnMapping());
  const[fbCacheSettingsBusy,setFbCacheSettingsBusy]=useState(false);
  const[aiSqlOpen,setAiSqlOpen]=useState(false);
  const[aiSqlPrompt,setAiSqlPrompt]=useState("");
  const[aiSqlBusy,setAiSqlBusy]=useState(false);
  const[aiSqlResult,setAiSqlResult]=useState(null);
  const[aiSqlFeedbackBusy,setAiSqlFeedbackBusy]=useState("");
  const[aiSqlFeedbackReasonOpen,setAiSqlFeedbackReasonOpen]=useState(false);
  const[aiSqlFeedbackReason,setAiSqlFeedbackReason]=useState("");
  const[remoteCols,setRemoteCols]=useState([]);
  const[remoteColsLoading,setRemoteColsLoading]=useState(false);
  const fbCacheTargets=[
    ["lot_progress","LOT 진행 최신 캐시","lot_progress_latest_lot_by_root_wafer",fbCacheStatus.lot_progress],
  ];

  const csvBaseFiles=(baseFiles||[]).filter(f=>(f?.kind||"file").toLowerCase()!=="dir"&&(f?.ext||"").toLowerCase()==="csv");
  const selectFileRule=(file,settings=fbSettings)=>{
    const key=String(file||"");
    setFbSelectedFile(key);
    setFbValidation(null);
    setFbSettingsLlmDraft(null);
    setFbRuleForm(ruleToForm((settings?.csv_rules||{})[key]||{}));
  };
  const loadFilebrowserSettings=async()=>{
    if(!isFileBrowserAdmin)return;
    setFbSettingsLoading(true);
    try{
      let localCsvFiles=csvBaseFiles;
      if(!localCsvFiles.length){
        const bf=await sf(API+"/base-files?_ts="+Date.now()).catch(()=>({files:[]}));
        const files=bf.files||[];
        setBaseFiles(files);
        localCsvFiles=files.filter(f=>(f?.kind||"file").toLowerCase()!=="dir"&&(f?.ext||"").toLowerCase()==="csv");
      }
      const d=await sf(API+"/settings");
      const settings={csv_full_read_max_bytes:d.csv_full_read_max_bytes??10485760,csv_download_max_rows:d.csv_download_max_rows??500000,csv_download_max_bytes:d.csv_download_max_bytes??100000000,sql_query_max_source_bytes:d.sql_query_max_source_bytes??5368709120,preview_max_columns:d.preview_max_columns??100,preview_max_rows:d.preview_max_rows??100,schema_column_page_size:d.schema_column_page_size??200,csv_rules:d.csv_rules||{},hidden_db_dirs:d.hidden_db_dirs||["cache","reformatter"],versioned_single_file_dirs:d.versioned_single_file_dirs||["reformatter"],auto_s3_upload_on_save:!!d.auto_s3_upload_on_save,can_manage:!!d.can_manage,max_csv_full_read_max_bytes:d.max_csv_full_read_max_bytes,max_csv_download_max_rows:d.max_csv_download_max_rows,max_csv_download_max_bytes:d.max_csv_download_max_bytes,max_sql_query_max_source_bytes:d.max_sql_query_max_source_bytes,max_preview_max_columns:d.max_preview_max_columns,max_schema_column_page_size:d.max_schema_column_page_size};
      setFbSettings(settings);
      setFbAutoS3Upload(!!settings.auto_s3_upload_on_save);
      setFbThresholdMb(String(((Number(settings.csv_full_read_max_bytes)||0)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadMb(String(((Number(settings.csv_download_max_bytes)||100000000)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadRows(String(Number(settings.csv_download_max_rows)||500000));
      setFbHiddenDbDirsText((settings.hidden_db_dirs||[]).join("\n"));
      setFbVersionedDirsText((settings.versioned_single_file_dirs||[]).join("\n"));
      const currentCsv=(selBaseMeta&&(selBaseMeta.ext||"").toLowerCase()==="csv")?String(selBaseMeta.path||selBaseMeta.name||""):"";
      const target=fbSelectedFile||currentCsv||localCsvFiles[0]?.path||localCsvFiles[0]?.name||"";
      if(target)selectFileRule(target,settings);
      setFbSettingsMsg("");
    }catch(e){
      setFbSettingsMsg(e.message||"설정 로드 실패");
    }finally{
      setFbSettingsLoading(false);
    }
  };
  const saveFilebrowserSettings=async(section="file")=>{
    const isFileSection=section==="file";
    const nextRules={...(fbSettings.csv_rules||{})};
    if(isFileSection&&fbSelectedFile){
      const rule=formToRule(fbRuleForm);
      if(Object.keys(rule).length)nextRules[fbSelectedFile]=rule;
      else delete nextRules[fbSelectedFile];
    }
    const thresholdBytes=isFileSection
      ? Math.max(0,Math.round(Number(fbThresholdMb||0)*1048576))
      : Number(fbSettings.csv_full_read_max_bytes||10485760);
    setFbSettingsLoading(true);
    try{
      const hiddenDbDirs=String(fbHiddenDbDirsText||"").split(/[\n,]/).map(v=>v.trim()).filter(Boolean);
      const versionedSingleFileDirs=String(fbVersionedDirsText||"").split(/[\n,]/).map(v=>v.trim()).filter(Boolean);
      const parsedRows=Number(fbDownloadRows);
      const downloadRows=Math.max(1,Math.min(Number(fbSettings.max_csv_download_max_rows||500000),Number.isFinite(parsedRows)?Math.round(parsedRows):500000));
      const parsedBytes=Math.round(Number(fbDownloadMb||100)*1048576);
      const downloadBytes=Math.max(1,Math.min(Number(fbSettings.max_csv_download_max_bytes||100000000),Number.isFinite(parsedBytes)?parsedBytes:100000000));
      const d=await sf(API+"/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({csv_full_read_max_bytes:thresholdBytes,csv_download_max_rows:downloadRows,csv_download_max_bytes:downloadBytes,sql_query_max_source_bytes:fbSettings.sql_query_max_source_bytes,preview_max_columns:fbSettings.preview_max_columns,preview_max_rows:fbSettings.preview_max_rows,schema_column_page_size:fbSettings.schema_column_page_size,csv_rules:nextRules,hidden_db_dirs:hiddenDbDirs,versioned_single_file_dirs:versionedSingleFileDirs,auto_s3_upload_on_save:!!fbAutoS3Upload})});
      const settings={csv_full_read_max_bytes:d.csv_full_read_max_bytes??thresholdBytes,csv_download_max_rows:d.csv_download_max_rows??downloadRows,csv_download_max_bytes:d.csv_download_max_bytes??downloadBytes,sql_query_max_source_bytes:d.sql_query_max_source_bytes??fbSettings.sql_query_max_source_bytes,preview_max_columns:d.preview_max_columns??fbSettings.preview_max_columns,preview_max_rows:d.preview_max_rows??fbSettings.preview_max_rows,schema_column_page_size:d.schema_column_page_size??fbSettings.schema_column_page_size,csv_rules:d.csv_rules||{},hidden_db_dirs:d.hidden_db_dirs||hiddenDbDirs,versioned_single_file_dirs:d.versioned_single_file_dirs||versionedSingleFileDirs,auto_s3_upload_on_save:!!d.auto_s3_upload_on_save,can_manage:!!d.can_manage,max_csv_full_read_max_bytes:d.max_csv_full_read_max_bytes,max_csv_download_max_rows:d.max_csv_download_max_rows,max_csv_download_max_bytes:d.max_csv_download_max_bytes,max_sql_query_max_source_bytes:d.max_sql_query_max_source_bytes,max_preview_max_columns:d.max_preview_max_columns,max_schema_column_page_size:d.max_schema_column_page_size};
      setFbSettings(settings);
      setFbAutoS3Upload(!!settings.auto_s3_upload_on_save);
      setFbThresholdMb(String(((Number(settings.csv_full_read_max_bytes)||0)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadMb(String(((Number(settings.csv_download_max_bytes)||downloadBytes)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadRows(String(Number(settings.csv_download_max_rows)||downloadRows));
      setFbHiddenDbDirsText((settings.hidden_db_dirs||[]).join("\n"));
      setFbVersionedDirsText((settings.versioned_single_file_dirs||[]).join("\n"));
      selectFileRule(fbSelectedFile,settings);
      setFbSettingsMsg(isFileSection?"파일 설정 저장 완료":"폴더 설정 저장 완료");
      sf(API+"/roots?_ts="+Date.now()).then(r=>setRoots(r.roots||[])).catch(()=>{});
      sf(API+"/base-files?_ts="+Date.now()).then(r=>setBaseFiles(r.files||[])).catch(()=>{});
    }catch(e){
      setFbSettingsMsg(e.message||(isFileSection?"파일 설정 저장 실패":"폴더 설정 저장 실패"));
    }finally{
      setFbSettingsLoading(false);
    }
  };
  const testFileRule=async()=>{
    if(!fbSelectedFile){setFbSettingsMsg("CSV 파일을 먼저 선택하세요.");return;}
    const sameOpen=mode==="base"&&selBaseFile===fbSelectedFile&&editCols.length;
    const csvText=sameOpen?buildSaveText(editCols,editRows,saveDelimiter,includeHeader):"";
    setFbSettingsLoading(true);
    try{
      const d=await sf(API+"/base-file/validate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({file:fbSelectedFile,csv_text:csvText,delimiter:sameOpen?saveDelimiter:"auto",include_header:sameOpen?includeHeader:true})});
      setFbValidation(d);
      setFbSettingsMsg(d.ok?`검증 통과 · rows ${d.rows||0}${d.sorted?" · 검증 통과 시 저장 정렬 적용":""}`:`검증 실패 · ${d.error_count||0}건`);
    }catch(e){
      setFbValidation({ok:false,errors:[{message:e.message||"검증 실패"}]});
      setFbSettingsMsg(e.message||"검증 실패");
    }finally{
      setFbSettingsLoading(false);
    }
  };
  const loadFileRuleDraftContext=async()=>{
    if(mode==="base"&&selBaseFile===fbSelectedFile){
      const columns=(data?.all_columns||data?.columns||editCols||[]).map(c=>String(c||"")).filter(Boolean);
      const sampleRows=(data?.data||[]).slice(0,5);
      if(columns.length||sampleRows.length)return{columns,sample_rows:sampleRows};
    }
    try{
      const q=new URLSearchParams({file:fbSelectedFile,rows:"20",cols:"80",meta_only:"false",_ts:String(Date.now())});
      const d=await sf(API+"/base-file-view?"+q.toString());
      return{columns:(d.all_columns||d.columns||[]).map(c=>String(c||"")).filter(Boolean),sample_rows:(d.data||[]).slice(0,5)};
    }catch(_){
      return{columns:[],sample_rows:[]};
    }
  };
  const draftFileRuleByLlm=async()=>{
    if(!fbSelectedFile){setFbSettingsMsg("CSV 파일을 먼저 선택하세요.");return;}
    const prompt=String(fbSettingsLlmPrompt||"").trim();
    if(!prompt){setFbSettingsMsg("규칙 초안 prompt를 입력하세요.");return;}
    setFbSettingsLlmBusy(true);setFbSettingsMsg("");setFbSettingsLlmDraft(null);
    try{
      const ctx=await loadFileRuleDraftContext();
      const d=await sf(API+"/settings/llm/draft",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        file:fbSelectedFile,
        prompt,
        columns:ctx.columns||[],
        sample_rows:ctx.sample_rows||[],
        current_rule:formToRule(fbRuleForm),
      })});
      setFbSettingsLlmDraft(d);
      const warnCount=(d.warnings||[]).length;
      setFbSettingsMsg(`규칙 초안 생성 완료${warnCount?` · warnings ${warnCount}`:""}`);
    }catch(e){
      setFbSettingsLlmDraft({ok:false,error:e.message||"규칙 초안 생성 실패"});
      setFbSettingsMsg(e.message||"규칙 초안 생성 실패");
    }finally{
      setFbSettingsLlmBusy(false);
    }
  };
  const applyFileRuleDraft=()=>{
    const draft=fbSettingsLlmDraft?.draft||fbSettingsLlmDraft?.csv_rules?.[fbSelectedFile]||null;
    if(!draft)return;
    setFbRuleForm(ruleToForm(mergeCsvRule(formToRule(fbRuleForm),draft)));
    setFbValidation(null);
    setFbSettingsMsg("초안을 기존 form에 병합했습니다. 저장을 눌러야 반영됩니다.");
  };
  const loadFilebrowserCacheStatus=async()=>{
    try{
      const lotProgress=await sf(API+"/cache/match/status?target=lot_progress").catch(e=>({ok:false,target:"lot_progress",error:e.message}));
      setFbCacheStatus({lot_progress:lotProgress});
      if(lotProgress?.interval_minutes)setFbCacheInterval(String(lotProgress.interval_minutes));
      if(Object.prototype.hasOwnProperty.call(lotProgress||{},"configured_source_root"))setFbCacheSourceRoot(String(lotProgress.configured_source_root||""));
      setFbCacheColumnMapping(normalizeLotProgressColumnMapping(lotProgress?.column_mapping||lotProgress?.column_mapping_defaults));
      if(Object.prototype.hasOwnProperty.call(lotProgress||{},"auto_s3_upload_on_save"))setFbAutoS3Upload(!!lotProgress.auto_s3_upload_on_save);
    }catch(_){}
  };
  const saveFilebrowserCacheSchedule=async()=>{
    setFbCacheSettingsBusy(true);setFbCacheMsg("");
    try{
      const d=await sf(API+"/cache/match/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target:"lot_progress",interval_minutes:Number(fbCacheInterval||30),source_root:fbCacheSourceRoot,auto_s3_upload_on_save:!!fbAutoS3Upload,column_mapping:fbCacheColumnMapping})});
      if(d?.interval_minutes)setFbCacheInterval(String(d.interval_minutes));
      if(Object.prototype.hasOwnProperty.call(d||{},"configured_source_root"))setFbCacheSourceRoot(String(d.configured_source_root||""));
      setFbCacheColumnMapping(normalizeLotProgressColumnMapping(d?.column_mapping||fbCacheColumnMapping));
      if(Object.prototype.hasOwnProperty.call(d||{},"auto_s3_upload_on_save"))setFbAutoS3Upload(!!d.auto_s3_upload_on_save);
      setFbCacheStatus(s=>({...s,lot_progress:d}));
      setFbCacheMsg(`LOT 진행 최신 캐시 설정 저장됨 · ${d?.interval_minutes||fbCacheInterval}분 · DB ${d?.configured_source_root||"auto"} · 컬럼 매핑 저장`);
    }catch(e){
      setFbCacheMsg(e.message||"캐시 설정 저장 실패");
    }finally{
      setFbCacheSettingsBusy(false);
    }
  };
  const refreshFilebrowserCache=async(target)=>{
    setFbCacheBusy(target);setFbCacheMsg("");
    try{
      const d=await sf(API+"/cache/match/refresh",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target,source_root:fbCacheSourceRoot,force:true})});
      if(d.disabled)setFbCacheMsg(`${target.toUpperCase()} 캐시가 비활성화되어 있습니다.`);
      else if(d.queued)setFbCacheMsg(`${target.toUpperCase()} 캐시 갱신 예약됨`);
      else if(d.running)setFbCacheMsg(`${target.toUpperCase()} 캐시 갱신 실행 중`);
      else setFbCacheMsg(`${target.toUpperCase()} 캐시 갱신 완료`);
      loadFilebrowserCacheStatus();
    }catch(e){
      setFbCacheMsg(e.message||"캐시 갱신 실패");
    }finally{
      setFbCacheBusy("");
    }
  };

  // Poll s3 items/history while modal open
  useEffect(()=>{
    if(!s3Open||!isAdmin)return;
    const un=encodeURIComponent(user?.username||"");
    const loadItems=()=>sf("/api/s3ingest/items?username="+un).then(d=>{setS3Items(d.items||[]);setS3AwsOk(d.aws_available!==false);}).catch(()=>{});
    const loadAvail=()=>sf("/api/s3ingest/available?username="+un).then(d=>setS3Avail(d||{dbs:[],root_parquets:[]})).catch(()=>{});
    const loadHist=()=>sf("/api/s3ingest/history?username="+un+"&limit=100").then(d=>setS3Hist(d.entries||[])).catch(()=>{});
    const loadProfiles=()=>sf("/api/s3ingest/aws-config?username="+un).then(d=>setS3Profiles((d&&d.profiles)||[])).catch(()=>setS3Profiles([]));
    loadItems();
    if(s3Tab==="add"){loadAvail();loadProfiles();}
    if(s3Tab==="history")loadHist();
    const t=setInterval(()=>{loadItems();if(s3Tab==="history")loadHist();},5000);
    return()=>clearInterval(t);
  },[s3Open,s3Tab,s3Tick,isAdmin,user?.username]);

  useEffect(()=>{
    if(s3Open&&!isAdmin&&!["folder","file","cache"].includes(s3Tab))setS3Tab("folder");
  },[s3Open,isAdmin,s3Tab]);

  useEffect(()=>{
    if(!s3Open||!isFileBrowserAdmin)return;
    loadFilebrowserSettings();
  },[s3Open,isFileBrowserAdmin]);

  useEffect(()=>{
    if(!(s3Open&&s3Tab==="cache"))return;
    loadFilebrowserCacheStatus();
    const t=setInterval(loadFilebrowserCacheStatus,15000);
    return()=>clearInterval(t);
  },[s3Open,s3Tab]);

  // 1s ticker for ETA countdown (only while modal open)
  useEffect(()=>{if(!s3Open)return;const t=setInterval(()=>setS3Now(Date.now()),1000);return()=>clearInterval(t);},[s3Open]);

  const s3Save=async(form)=>{
    if(!form.target||!form.s3_url){toast.warn("target 과 s3_url 은 필수입니다");return;}
    const body={...form,username:user?.username||""};
    try{
      await sf("/api/s3ingest/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      setS3Form(null);setS3Tab("items");setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"저장 실패");}
  };
  const s3Delete=async(id)=>{
    if(!window.confirm("이 S3 동기화 항목을 삭제하시겠습니까?\n("+id+")"))return;
    try{
      await sf("/api/s3ingest/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id})});
      setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"삭제 실패");}
  };
  const s3Run=async(id)=>{
    try{
      await sf("/api/s3ingest/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id})});
      setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"실행 실패");}
  };
  const s3FmtETA=(item)=>{
    const iv=Number(item.interval_min||0);if(iv<=0)return"수동";
    if(item.due)return"지금 실행 예정";
    if(item.next_due){
      const dueMs=new Date(item.next_due).getTime();
      if(!isNaN(dueMs)){
        const diff=dueMs-s3Now;
        if(diff<=0)return"지금 실행 예정";
        const m=Math.floor(diff/60000),s=Math.floor((diff%60000)/1000);
        return m>=60?Math.floor(m/60)+"시간 "+(m%60)+"분":m+"분 "+s+"초";
      }
    }
    const st=item.status||{};const last=st.last_end||st.last_start;
    if(!last)return"지금 실행 예정";
    const lastMs=new Date(last).getTime();if(isNaN(lastMs))return"-";
    const dueMs=lastMs+iv*60000;const diff=dueMs-s3Now;
    if(diff<=0)return"지금 실행 예정";
    const m=Math.floor(diff/60000),s=Math.floor((diff%60000)/1000);
    return m>=60?Math.floor(m/60)+"시간 "+(m%60)+"분":m+"분 "+s+"초";
  };

  // v8.8.3: Admin Base 단일파일 원본 삭제 (archive to .trash). host_root 자동 감지.
  const deleteBaseFile=async(name)=>{
    if(!isAdmin){toast.warn("Admin 만 삭제할 수 있습니다.");return;}
    if(!window.confirm("정말 이 Base 단일 파일을 삭제하시겠습니까?\n"+name+"\n\n.trash 폴더로 이동됩니다 (복구 가능)."))return;
    try{
      await sf(API+"/base-file/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({file:name,username:user?.username||""})});
      // 리스트 즉시 반영 + 선택 상태 정리
      setBaseFiles(prev=>prev.filter(f=>f.name!==name));
    if(selBaseFile===name){setSelBaseFile("");setData(null);setBaseRaw(null);setBaseVersions([]);setBaseVersioned(false);setBaseVersionPreview(null);setRawEditing(false);setRawEditText("");}
    if(selBaseMeta?.name===name)setSelBaseMeta(null);
    setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);
    }catch(e){toast.error("삭제 실패: "+(e?.message||e));}
  };

  useEffect(()=>{
  // v4.1: boot-load scopes + DB listings in parallel. Base listing is lazy
  // (loaded only when user switches scope) to keep the default cold-start fast.
  const loadInitial = async () => {
    try {
      const sc = await sf(API+"/scopes").catch(()=>({scopes:[{key:"DB",label:"DB",exists:true,icon:"🗄️"}]}));
      const scopesPayload = sc.scopes || [];
      setScopes(scopesPayload);
      let rp = await sf(API+"/roots");
      const rp2 = await sf(API+"/root-parquets");
      if (!rp.roots?.length) {
        const baseScope = scopesPayload.find((s) => s?.key === "Base") || {};
        if (baseScope?.exists) {
          rp = await sf(API+"/roots?all=1").catch(() => rp);
        }
      }
      setRoots(rp.roots||[]);
      setRootPqs(rp2.files||[]);
      if(rp.roots?.length)setSelRoot(rp.roots[0].name);
      const rootScope = scopesPayload.find((s) => s?.key === "DB") || {};
      const baseScope = scopesPayload.find((s) => s?.key === "Base") || {};
      const nextScope = (rootScope?.exists && (rp?.roots||[]).length > 0)
        ? "DB"
        : (baseScope?.exists ? "Base" : (scopesPayload[0]?.key || "DB"));
      setScope(nextScope);
      setSideLoading(false);
    } catch (_) {
      setSideLoading(false);
    }
  };
  loadInitial();
  },[]);

  // v4.1: when user switches to Base scope, fetch /base-files (idempotent).
  useEffect(()=>{
    if(scope!=="Base")return;
    setSideLoading(true);
    sf(API+"/base-files?_ts="+Date.now()).then(d=>{setBaseFiles(d.files||[]);setSideLoading(false);}).catch(()=>setSideLoading(false));
  },[scope]);

  // v4.1: Base-file preview loader (parquet/csv/json/md).
  // 단일 관리 파일은 전체, parquet/cache 파일은 서버에서 기본 100행 샘플로 보여준다.
  const syncBaseEditState=(d)=>{
    const cols=(d.showing_cols||d.columns||[]);
    const rows=(d.data||[]).map(r=>cols.map(c=>{
      const v=r?.[c];
      return v===null||v===undefined?"":String(v);
    }));
    setEditCols(cols);
    setEditRows(rows);
    setEditOriginRows(rows.map(r=>r.slice()));
  };
  const selectedColsFromResponse=(d,fallback=[])=>{
    if(Array.isArray(d?.showing_cols)&&d.showing_cols.length)return d.showing_cols.map(c=>String(c));
    if(Array.isArray(d?.selected_cols)&&d.selected_cols.length)return d.selected_cols.map(c=>String(c));
    if(typeof d?.selected_cols==="string"&&d.selected_cols.trim())return d.selected_cols.split(",").map(c=>c.trim()).filter(Boolean);
    return fallback;
  };
  const cleanSortSpec=(sort)=>{
    if(!sort||typeof sort!=="object")return null;
    const column=String(sort.column||"").trim();
    if(!column)return null;
    const direction=String(sort.direction||"asc").toLowerCase()==="desc"?"desc":"asc";
    const nulls=String(sort.nulls||"last").toLowerCase()==="first"?"first":"last";
    return{column,direction,nulls};
  };
  const sortParams=(spec)=>{
    const s=cleanSortSpec(spec);
    return s?{sort_column:s.column,sort_direction:s.direction,sort_nulls:s.nulls}:{};
  };
  const sortLabel=(spec)=>{
    const s=cleanSortSpec(spec);
    if(!s)return"";
    return `${s.column} ${s.direction}${s.nulls==="first"?" nulls first":""}`;
  };

  const loadBaseFileView=(file,{full=true,page:pageArg=0}={})=>{
    setLoading(true);setTab("data");setMode("base");setSelBaseFile(file);
    setSortSpec(null);
    setPage(pageArg);
    setSelProd("");setSelRootPq("");setError("");setBaseRaw(null);
    setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);
    const params={file,rows:PAGE_SIZE,page:pageArg,page_size:PAGE_SIZE,cols:10,meta_only:!full,_ts:Date.now()};
    const url=buildUrl(API+"/base-file-view",params);
    sf(url).then(d=>{
      if(d.kind==="json"||d.kind==="md"||d.kind==="yaml"){
        // Render natively: JSON is pretty-printed, md shown as raw <pre>.
        let pretty=d.preview||d.text||"(empty)";
        if(d.kind==="json"){
          try{pretty=JSON.stringify(JSON.parse(pretty),null,2);}catch(_){/* leave raw */}
        }
        setBaseRaw({kind:d.kind,file,size:d.size,truncated:d.truncated,top_keys:d.parsed_top_keys,text:pretty});
        setData(null);
      }else{
        setData(d);
        syncBaseEditState(d);
      }
      setLoading(false);
    }).catch(e=>{setError(e.message);setLoading(false);});
  };

  useEffect(()=>{
    if(!selRoot){setProducts([]);return;}
    setSideLoading(true);
    sf(API+"/products?root="+encodeURIComponent(selRoot)).then(d=>{
      setProducts(d.products||[]);
      setSideLoading(false);
      // v8.8.32: 교차 선택 — 이미 제품이 선택된 상태에서 다른 DB 루트를 클릭하면
      //   그 DB 에 같은 제품이 있을 경우 자동으로 view 를 갱신. UX: DB 를 바꿔도
      //   제품 클릭을 다시 안 해도 됨.
      if(selProd){
        const match=(d.products||[]).find(p=>p.name===selProd);
        if(match){
          setSelectedCols([]);setSortSpec(null);
          setSql("");
          loadHiveView(selRoot,selProd,"",[],{full:true,page:0,sortOverride:null});
        }
      }
    }).catch(()=>setSideLoading(false));
  },[selRoot]);

  const buildUrl=(base,params)=>{
    const q=Object.entries(params).filter(([_,v])=>v!==undefined&&v!=="").map(([k,v])=>k+"="+encodeURIComponent(v)).join("&");
    return base+"?"+q;
  };

  useEffect(()=>{
    const q=String(colSearch||"").trim();
    if(!data?.all_columns_truncated||!q){
      setRemoteCols([]);
      setRemoteColsLoading(false);
      return;
    }
    const params={q,limit:fbSettings.schema_column_page_size||200,_ts:Date.now()};
    if(mode==="hive"&&selRoot&&selProd){params.root=selRoot;params.product=selProd;}
    else if(mode==="base"&&selBaseFile){params.file=selBaseFile;}
    else if(mode==="rootpq"&&selRootPq){params.file=selRootPq;}
    else return;
    let alive=true;
    setRemoteColsLoading(true);
    const t=setTimeout(()=>{
      sf(buildUrl(API+"/columns/search",params)).then(d=>{
        if(!alive)return;
        setRemoteCols((d.columns||[]).map(c=>String(c||"")).filter(Boolean));
        if(d.dtypes)setData(prev=>prev?{...prev,dtypes:{...(prev.dtypes||{}),...(d.dtypes||{})}}:prev);
      }).catch(()=>{if(alive)setRemoteCols([]);}).finally(()=>{if(alive)setRemoteColsLoading(false);});
    },220);
    return()=>{alive=false;clearTimeout(t);};
  },[colSearch,data?.all_columns_truncated,mode,selRoot,selProd,selBaseFile,selRootPq,fbSettings.schema_column_page_size]);

  // 첫 클릭도 100행 샘플을 보여주고, SQL/SELECT는 같은 cap 안에서 조건 결과를 조회한다.
  const loadHiveView=(root,prod,sqlQ,selColsOverride,{full=true,page:pageArg=0,sortOverride=undefined}={})=>{
    setLoading(true);setTab("data");setMode("hive");setSelProd(prod);setSelRootPq("");setError("");setBaseRaw(null);
    setSelBaseMeta(null);setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);
    setPage(pageArg);
    const sc=selColsOverride||selectedCols;
    const activeSort=sortOverride===undefined?sortSpec:sortOverride;
    const params={root,product:prod,sql:sqlQ||"",rows:PAGE_SIZE,page:pageArg,page_size:PAGE_SIZE,cols:20,select_cols:sc.length?sc.join(","):"",meta_only:!full,...sortParams(activeSort)};
    const url=buildUrl(API+"/view",params);
    sf(url).then(d=>{if(sc.length)setSelectedCols(selectedColsFromResponse(d,sc));setData(d);setLoading(false);}).catch(e=>{setError(e.message);setLoading(false);});
  };

  const loadRootPqView=(file,sqlQ,selColsOverride,{full=true,page:pageArg=0,sortOverride=undefined}={})=>{
    setLoading(true);setTab("data");setMode("rootpq");setSelRootPq(file);setSelProd("");setError("");setBaseRaw(null);
    setSelBaseMeta(null);setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);
    setPage(pageArg);
    const sc=selColsOverride||selectedCols;
    const activeSort=sortOverride===undefined?sortSpec:sortOverride;
    const params={file,sql:sqlQ||"",rows:PAGE_SIZE,page:pageArg,page_size:PAGE_SIZE,cols:10,select_cols:sc.length?sc.join(","):"",meta_only:!full,...sortParams(activeSort)};
    const url=buildUrl(API+"/root-parquet-view",params);
    sf(url).then(d=>{if(sc.length)setSelectedCols(selectedColsFromResponse(d,sc));setData(d);setLoading(false);}).catch(e=>{setError(e.message);setLoading(false);});
  };

  // v8.8.16: "실행" 클릭 = 실제 행 조회 트리거. meta_only 없이 호출 → 서버에서 collect.
  const applySql=(sqlOverride,selectedColsOverride,sortOverride=undefined)=>{
    const activeSql=typeof sqlOverride==="string"?sqlOverride:sql;
    const activeSelectedCols=Array.isArray(selectedColsOverride)?selectedColsOverride:selectedCols;
    const activeSort=sortOverride===undefined?sortSpec:sortOverride;
    if(mode==="base"&&isBaseEditing){
      setError("편집 모드에서는 SQL 실행이 비활성됩니다.");
      return;
    }
    if(mode==="rootpq"&&selRootPq)loadRootPqView(selRootPq,activeSql,activeSelectedCols,{full:true,page:0,sortOverride:activeSort});
    else if(mode==="base"&&selBaseFile){
      // Base JSON/md files have no SQL surface — silently ignore. Tabular
      // parquet/csv re-load with the SQL param applied server-side.
      if(baseRaw)return; // json/md 는 SQL 적용 불가 — baseRaw 상태로 판단
      setLoading(true);setError("");
      // full=true 와 동일 — SQL 이 비어도 sample 행을 보여줘야 하므로 meta_only 꺼둠.
      setPage(0);
      const url=buildUrl(API+"/base-file-view",{file:selBaseFile,sql:activeSql||"",rows:PAGE_SIZE,page:0,page_size:PAGE_SIZE,cols:10,_ts:Date.now(),
        select_cols:activeSelectedCols.length?activeSelectedCols.join(","):"",...sortParams(activeSort)});
      sf(url).then(d=>{if(activeSelectedCols.length)setSelectedCols(selectedColsFromResponse(d,activeSelectedCols));setData(d);if(!d.kind)syncBaseEditState(d);setLoading(false);}).catch(e=>{setError(e.message||String(e));setLoading(false);});
    }
    else if(selRoot&&selProd)loadHiveView(selRoot,selProd,activeSql,activeSelectedCols,{full:true,page:0,sortOverride:activeSort});
  };

  const draftAiSql=async()=>{
    const prompt=String(aiSqlPrompt||"").trim();
    if(!prompt){toast.warn("자연어 조건을 입력하세요.");return;}
    const columns=(data?.all_columns||data?.columns||[]).map(c=>String(c||"")).filter(Boolean);
    if(!columns.length){toast.warn("먼저 DB나 파일을 열어 컬럼을 로드하세요.");return;}
    setAiSqlBusy(true);setAiSqlResult(null);
    setAiSqlFeedbackReasonOpen(false);setAiSqlFeedbackReason("");setAiSqlFeedbackBusy("");
    try{
      const body={
        natural_language:prompt,
        columns,
        dtypes:data?.dtypes||data?.schema||{},
        sample_rows:(data?.data||[]).slice(0,5),
        preferred_selected_columns:selectedCols,
        current_sql:sql||"",
        scope:mode,
        root:selRoot||"",
        product:selProd||"",
        file:selBaseFile||selRootPq||"",
        choice:payloadOverride.choice||"",
      };
      const d=await sf(API+"/sql/llm/draft",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      setAiSqlResult(d);
      if(d.ok&&(typeof d.sql==="string")){
        const nextSql=d.sql||"";
        const nextSelectedCols=Array.isArray(d.selected_columns)?d.selected_columns.map(c=>String(c||"")).filter(Boolean):[];
        const nextSort=cleanSortSpec(d.sort);
        setSql(nextSql);
        setSelectedCols(nextSelectedCols);
        setSortSpec(nextSort);
        applySql(nextSql,nextSelectedCols,nextSort);
        toast.ok("AI SQL 초안으로 조회했습니다.");
      }else{
        toast.warn((d.warnings||[])[0]||"AI SQL 초안 생성 실패");
      }
    }catch(e){
      const msg=e.message||"AI SQL 초안 생성 실패";
      setAiSqlResult({ok:false,warnings:[msg]});
      toast.error(msg);
    }finally{
      setAiSqlBusy(false);
    }
  };

  const submitAiSqlFeedback=async(rating,reasonOverride,payloadOverride={})=>{
    if(!aiSqlResult?.draft_id){toast.warn("저장할 AI SQL 초안이 없습니다.");return;}
    const ratingKey=String(rating||"").toLowerCase()==="down"?"down":"up";
    setAiSqlFeedbackBusy(ratingKey);
    try{
      const columns=(data?.all_columns||data?.columns||[]).map(c=>String(c||"")).filter(Boolean);
      const body={
        draft_id:aiSqlResult.draft_id,
        rating:ratingKey,
        reason:reasonOverride!==undefined?reasonOverride:aiSqlFeedbackReason,
        natural_language:aiSqlPrompt,
        sql:payloadOverride.sql!==undefined?payloadOverride.sql:(aiSqlResult.sql||sql||""),
        sort:payloadOverride.sort!==undefined?(cleanSortSpec(payloadOverride.sort)||{}):(cleanSortSpec(aiSqlResult.sort)||sortSpec||{}),
        selected_columns:Array.isArray(payloadOverride.selected_columns)?payloadOverride.selected_columns:(Array.isArray(aiSqlResult.selected_columns)?aiSqlResult.selected_columns:selectedCols),
        columns,
        scope:mode,
        root:selRoot||"",
        product:selProd||"",
        file:selBaseFile||selRootPq||"",
      };
      const d=await sf(API+"/sql/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      setAiSqlResult(prev=>prev?{...prev,feedback_saved:ratingKey,feedback_id:d.feedback_id}:prev);
      toast.ok(ratingKey==="up"?"좋아요를 저장했습니다.":"싫어요를 저장했습니다.");
      if(ratingKey==="down")setAiSqlFeedbackReasonOpen(false);
    }catch(e){
      toast.error(e.message||"피드백 저장 실패");
    }finally{
      setAiSqlFeedbackBusy("");
    }
  };

  const baseFileExt=(selBaseMeta?.ext||"").toLowerCase();
  const baseFileEditable = canEditBaseMeta(selBaseMeta);
  const canEditCurrentBase=mode==="base"&&!!selBaseFile&&!baseRaw&&baseFileEditable&&isFileBrowserAdmin;
  const baseFileComplete = !!data&&(data.single_file_full_read||(data.total_rows||0)<= (data.showing||0));
  const basePathOf=(f)=>String(f?.path||f?.name||"").replace(/\\/g,"/");
  const baseAllItems = baseFiles || [];
  const baseItems = baseDir
    ? [
        {name:"상위 폴더",path:"__base_dir_up__",kind:"dir_up",ext:"dir",description:"상위 폴더로 이동"},
        ...baseAllItems.filter(f => (f?.kind || "file").toLowerCase() !== "dir" && basePathOf(f).startsWith(baseDir+"/"))
      ]
    : baseAllItems.filter(f => {
        const kind=(f?.kind || "file").toLowerCase();
        if(kind==="dir")return true;
        return !basePathOf(f).includes("/");
      });
  const baseFileCount = baseItems.filter(f => {
    const kind=(f?.kind || "file").toLowerCase();
    return kind !== "dir" && kind !== "dir_up";
  }).length;

  const startBaseEdit=()=>{
    if(!canEditCurrentBase||!data){
      setError("현재 파일은 편집 대상이 아닙니다.");
      return;
    }
    if(!baseFileComplete){
      setError("미리보기 행 수가 전체를 포함하지 않습니다. 전체 조회 후 편집을 진행하세요.");
      return;
    }
    if(!editCols.length){
      setError("열 정보가 없어 편집을 시작할 수 없습니다.");
      return;
    }
    setError("");
    setPasteMode("replace");
    setSaveDelimiter(defaultDelimiterForFile(selBaseFile));
    setIncludeHeader(true);
    setSelectedEditCell({r:0,c:0});
    setIsBaseEditing(true);
    setTab("data");
  };

  const cancelBaseEdit=()=>{
    setEditRows(editOriginRows.map(r=>r.slice()));
    setIsBaseEditing(false);
    setSelectedEditCell({r:0,c:0});
  };

  const restoreBaseEdit=()=>{
    setEditRows(editOriginRows.map(r=>r.slice()));
    setSelectedEditCell({r:0,c:0});
  };

  const addBaseEditRow=()=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    if(!editCols.length){
      setError("열 정보가 없어 새 행을 추가할 수 없습니다.");
      return;
    }
    setEditRows(prev=>{
      const next=[...prev,Array(editCols.length).fill("")];
      setSelectedEditCell({r:next.length-1,c:0});
      return next;
    });
  };

  const deleteBaseEditRow=(targetR=selectedEditCell.r)=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    setEditRows(prev=>{
      if(!prev.length)return prev;
      const idx=Math.max(0,Math.min(targetR,prev.length-1));
      const next=prev.filter((_,i)=>i!==idx);
      const nextR=next.length?Math.min(idx,next.length-1):0;
      setSelectedEditCell(cur=>({r:nextR,c:Math.max(0,Math.min(cur.c||0,editCols.length-1))}));
      return next;
    });
  };

  const patchBaseCell=(r,c,val)=>{
    setEditRows(prev=>{
      const next=prev.map(x=>x.slice());
      while(next.length<=r)next.push(Array(editCols.length).fill(""));
      while(next[r].length<editCols.length)next[r].push("");
      next[r][c]=val;
      return next;
    });
  };

  const pasteBaseRows=(rowsRaw)=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    let rows=rowsRaw||[];
    if(isHeaderMatch(rows,editCols)){
      rows=rows.slice(1);
    }
    if(!rows.length)return;
    const normalized=normalizeGridRows(rows,editCols.length,"");
    setEditRows(prev=>{
      let next=prev.map(x=>x.slice());
      const startR=pasteMode==="append"?(next.length):(Math.max(0,Math.min(selectedEditCell.r,next.length-1)));
      const startC=pasteMode==="append"?0:Math.max(0,Math.min(selectedEditCell.c,editCols.length-1));
      normalized.forEach((row,ri)=>{
        const targetR=startR+ri;
        while(next.length<=targetR)next.push(Array(editCols.length).fill(""));
        for(let ci=0;ci<Math.min(editCols.length,row.length);ci++){
          if(startC+ci>=editCols.length)break;
          next[targetR][startC+ci]=row[ci];
        }
      });
      return next;
    });
  };

  const onBasePaste=(e)=>{
    if(!isBaseEditing)return;
    e.preventDefault();
    const text=e.clipboardData?.getData("text/plain")||"";
    if(!text.trim())return;
    const [rows]=detectDelimiterFromGridText(text);
    pasteBaseRows(rows);
  };

  const saveBaseEdit=async()=>{
    if(!canEditCurrentBase||!isBaseEditing){setError("현재 편집 상태가 아닙니다.");return;}
    if(!editCols.length){setError("열이 없습니다.");return;}
    const csvText=buildSaveText(editCols,editRows,saveDelimiter,includeHeader);
    const note=window.prompt("변경 사유를 입력하세요.", "Grid EDM edit");
    if(note===null)return;
    const payload=JSON.stringify({
      file:selBaseFile,
      mode:"replace",
      csv_text:csvText,
      delimiter:saveDelimiter,
      include_header:includeHeader,
      note,
    });
    const candidates=[API+"/base-file/save",API+"/base-file-save"];
    let saved=false;
    let savedResult=null;
    let lastErr=null;
    let lastUrl="";
    for(const p of candidates){
      // API not-found 대비: /base-file-save alias도 순차 시도
      lastUrl=p;
      try{
        savedResult=await sf(p,{method:"POST",headers:{"Content-Type":"application/json"},body:payload});
        saved=true;
        break;
      }catch(e){
        lastErr=e;
        if(e.status!==404)break;
      }
    }
    if(!saved){
      if(lastErr?.status===404){
        const detail = String(lastErr?.message || "").trim();
        if(detail && detail !== "API not found"){
          setError(`${detail} (${lastUrl})`);
        }else{
          setError(`API not found: ${lastUrl}`);
        }
      }else{
        setError(lastErr?.message||"저장 실패");
      }
      return;
    }
    try{
      const reloadState={full:true,page:0};
      setIsBaseEditing(false);
      if(savedResult?.s3_sync?.status)setBaseVersionMsg(`저장 완료 · s3 ${savedResult.s3_sync.status}`);
      loadBaseVersions(selBaseFile);
      loadBaseFileView(selBaseFile,reloadState);
    }catch(e){setError(e?.message||"저장 처리 중 오류");}
  };

  const toggleCol=(col)=>{
    setSelectedCols(prev=>{
      const next=prev.includes(col)?prev.filter(c=>c!==col):[...prev,col];
      return next;
    });
  };

  const reloadWithCols=(cols)=>{
    // v8.4.4: Base 모드도 select_cols 적용되도록 분기 추가
    if(mode==="base"&&isBaseEditing){
      setError("편집 모드에서는 컬럼 선택이 비활성됩니다.");
      return;
    }
    if(mode==="rootpq"&&selRootPq){loadRootPqView(selRootPq,sql,cols,{full:true,page:0});}
    else if(mode==="base"&&selBaseFile){
      if(baseRaw)return; // json/md 는 컬럼 선택 불가 — baseRaw 상태로 판단
      setLoading(true);setError("");setTab("data");
      setPage(0);
      const url=buildUrl(API+"/base-file-view",{file:selBaseFile,sql:sql||"",rows:PAGE_SIZE,page:0,page_size:PAGE_SIZE,cols:10,_ts:Date.now(),
        select_cols:cols.length?cols.join(","):"",...sortParams(sortSpec)});
      sf(url).then(d=>{setSelectedCols(cols.length?selectedColsFromResponse(d,cols):[]);setData(d);if(!d.kind)syncBaseEditState(d);setLoading(false);}).catch(e=>{setError(e.message||String(e));setLoading(false);});
    }
    else if(selRoot&&selProd){loadHiveView(selRoot,selProd,sql,cols,{full:true,page:0});}
  };
  const applySelectedCols=()=>reloadWithCols(selectedCols);
  const clearSelectedCols=()=>{setSelectedCols([]);reloadWithCols([]);};

  const insertColToSql=(col)=>{
    setSql(prev=>{
      if(!prev.trim())return col+" == ''";
      return prev+" & ("+col+" == '')";
    });
    setTab("data");
  };

  const downloadCsv=()=>{
    const maxRows=Math.max(1,Math.min(Number(fbSettings.max_csv_download_max_rows||500000),Number(fbSettings.csv_download_max_rows||500000)||500000));
    const maxBytes=Math.max(1,Math.min(Number(fbSettings.max_csv_download_max_bytes||100000000),Number(fbSettings.csv_download_max_bytes||100000000)||100000000));
    let url=API+"/download-csv?username="+(user?.username||"anon")+"&max_rows="+encodeURIComponent(String(maxRows))+"&max_bytes="+encodeURIComponent(String(maxBytes))+"&sql="+encodeURIComponent(sql);
    if(selectedCols.length)url+="&select_cols="+encodeURIComponent(selectedCols.join(","));
    if(mode==="base")url+="&file="+encodeURIComponent(selBaseFile);
    else if(mode==="rootpq")url+="&file="+encodeURIComponent(selRootPq);
    else url+="&root="+encodeURIComponent(selRoot)+"&product="+encodeURIComponent(selProd);
    dl(url,"data.csv").catch(e=>toast.error(e.message||"다운로드 실패"));
  };

  const gotoPage=(nextPage)=>{
    if(mode==="base"&&isBaseEditing){
      setError("편집 모드에서는 페이지 이동이 비활성됩니다.");
      return;
    }
    const p=Math.max(0,nextPage);
    if(mode==="rootpq"&&selRootPq)loadRootPqView(selRootPq,sql,selectedCols,{full:true,page:p});
    else if(mode==="base"&&selBaseFile&&!baseRaw){
      setLoading(true);setError("");setTab("data");setPage(p);
      const url=buildUrl(API+"/base-file-view",{file:selBaseFile,sql:sql||"",rows:PAGE_SIZE,page:p,page_size:PAGE_SIZE,cols:10,_ts:Date.now(),
        select_cols:selectedCols.length?selectedCols.join(","):"",...sortParams(sortSpec)});
      sf(url).then(d=>{setData(d);if(!d.kind)syncBaseEditState(d);setLoading(false);}).catch(e=>{setError(e.message||String(e));setLoading(false);});
    } else if(selRoot&&selProd)loadHiveView(selRoot,selProd,sql,selectedCols,{full:true,page:p});
  };

  const allCols=data?.all_columns||data?.columns||[];
  const filteredCols=colSearch?allCols.filter(c=>c.toLowerCase().includes(colSearch.toLowerCase())):allCols;
  const displayCols=(data?.all_columns_truncated&&colSearch.trim())?remoteCols:filteredCols;
  const fbActiveRule=formToRule(fbRuleForm);
  const fbActiveRuleSections=ruleSummarySections(fbActiveRule);
  const fbDraftRule=fbSettingsLlmDraft?.draft||fbSettingsLlmDraft?.csv_rules?.[fbSelectedFile]||null;
  const fbDraftRuleSections=fbDraftRule?ruleSummarySections(fbDraftRule):[];
  const canEnterBaseEdit = canEditCurrentBase && baseFileComplete;
  const isBaseEditingMode = mode==="base"&&isBaseEditing;
  const baseEditingTabs = isBaseEditingMode ? ["data"] : ["data","columns"];
  const showBasePager = false;
  const settingsTabs=[
    ...(isAdmin?[{k:"items",l:"항목 ("+s3Items.length+")"},{k:"add",l:"+ 추가"},{k:"history",l:"이력"},{k:"aws",l:"AWS 설정"}]:[]),
    {k:"cache",l:"캐시"},
    {k:"folder",l:"폴더 설정"},
    {k:"file",l:"파일 설정"},
  ];
  const settingsTitle=s3Tab==="folder"?"FileBrowser 폴더 설정":(s3Tab==="file"?"FileBrowser 파일 설정":(s3Tab==="cache"?"FileBrowser 캐시 운영":"S3 동기화 설정 — aws s3 cp/sync"));
  const activeQueryMode=!!(String(sql||"").trim() || selectedCols.length || data?.selected_cols);
  const activePreviewLimit=Number(data?.preview_row_limit||fbSettings.preview_max_rows||PAGE_SIZE)||PAGE_SIZE;
  const previewStatusLabel=data?.single_file_full_read
    ?"전체 표시"
    :(activeQueryMode?"검색 결과":`예시 ${activePreviewLimit}행`);

  const chipS={display:"inline-flex",alignItems:"center",gap:4,padding:"2px 8px",borderRadius:4,fontSize:14,cursor:"pointer",marginRight:4,marginBottom:4,border:"1px solid var(--border)",transition:"all 0.15s"};
  const chipActive={...chipS,background:"var(--accent-glow)",borderColor:"var(--accent)",color:"var(--accent)",fontWeight:600};
  const chipInactive={...chipS,background:"var(--bg-hover)",color:"var(--text-secondary)"};
  const sidebarText={flex:1,minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"};
  const sidebarMeta={fontSize:11,color:FB_MUTED,flexShrink:0,maxWidth:82,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontFamily:"monospace"};
  const sidebarRowBase={display:"flex",alignItems:"center",gap:6,minWidth:0,overflow:"hidden"};
  const sidebarStack={display:"flex",flexDirection:"column",gap:2,flex:1,minWidth:0,overflow:"hidden"};
  const sidebarMetaLine={display:"flex",alignItems:"center",gap:6,minWidth:0,overflow:"hidden",lineHeight:1.15};
  const baseEditWrap={overflow:"auto",maxHeight:"calc(100vh - 320px)",border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-primary)"};
  const baseReadWrap={...baseEditWrap,maxHeight:"calc(100vh - 280px)"};
  const baseEditTable={width:"100%",borderCollapse:"separate",borderSpacing:0,fontSize:13,fontFamily:"ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",background:"var(--bg-primary)"};
  const baseEditHeaderCell={padding:"6px 10px",height:34,fontWeight:700,fontSize:13,color:"var(--text-secondary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)",
    background:"var(--bg-tertiary)",position:"sticky",top:0,zIndex:6,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",minWidth:84};
  const baseEditCornerCell={...baseEditHeaderCell,textAlign:"center",width:76,left:0,zIndex:7};
  const baseEditHeaderInput={...baseEditHeaderCell, fontWeight:700,textAlign:"left",minWidth:160,paddingRight:24};
  const baseEditRowCell={padding:0,borderBottom:"1px solid var(--border)",borderRight:"1px solid var(--border)",background:"var(--bg-primary)",height:34};
  const baseEditIndexBody={...baseEditRowCell,position:"sticky",left:0,zIndex:5,textAlign:"center",color:"var(--text-secondary)",fontSize:12,letterSpacing:0.2};
  const baseEditCellInput={width:"100%",height:"100%",padding:"0 10px",border:"none",outline:"none",background:"transparent",color:"var(--text-primary)",fontSize:13,fontFamily:"inherit",boxSizing:"border-box"};
  const baseEditCellActive={boxShadow:`inset 0 0 0 2px var(--accent)`,background:"#dbeafe",zIndex:2};
  const baseReadCell={...baseEditRowCell,padding:"0 10px",fontSize:13,color:"var(--text-primary)",height:34,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"};
  const baseReadIndexCell={...baseEditIndexBody,padding:"0 10px",height:34,width:54,textAlign:"center",fontSize:13};
  const baseEditRowHighlight={background:"rgba(59,130,246,0.12)"};
  const baseEditColHighlight={background:"rgba(59,130,246,0.09)"};

  return(
    <div className="flow-connected-page" style={{display:"flex",height:"calc(100vh - 52px)",background:"var(--bg-primary)",color:"var(--text-primary)"}}>
      {/* Sidebar */}
      <div style={{width:260,minWidth:260,borderRight:"1px solid var(--border)",display:"flex",flexDirection:"column",background:"var(--bg-secondary)",overflow:"hidden"}}>
        <div className="flow-sidebar-header" style={{padding:"12px 16px",borderBottom:"1px solid var(--border)",fontSize:14,fontWeight:700,color:"var(--text-secondary)"}}>
          <span className="flow-sidebar-header-title">파일탐색기</span>
          <div className="flow-sidebar-header-meta">{scope==="Base"?baseFileCount:products.length} items</div>
        </div>
        {/* Scope switcher (DB / root-level files). Shown only when backend reports 2+ scopes. */}
        {scopes.length>=2&&<div className="filebrowser-scope-switcher" style={{display:"flex",gap:4,padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>
          {scopes.map(s=>{
            const active=scope===s.key;const disabled=s.exists===false;
            return(<span key={s.key} className={"filebrowser-scope-option filebrowser-scope-"+s.key} data-scope={s.key} data-active={active?"1":"0"}
              onClick={()=>{if(disabled)return;setScope(s.key);setBaseDir("");setData(null);setBaseRaw(null);setSelBaseMeta(null);setError("");setSelProd("");setSelRootPq("");setSelBaseFile("");setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);setSelectedCols([]);setSortSpec(null);}}
              title={s.description+(disabled?"\n(경로 없음 — admin_settings 확인)":"")}
              style={{flex:1,minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",textAlign:"center",padding:"6px 8px",borderRadius:5,fontSize:14,cursor:disabled?"not-allowed":"pointer",fontWeight:active?700:500,
                background:active?"var(--accent-glow)":"var(--bg-hover)",color:disabled?"var(--text-secondary)":(active?"var(--accent)":"var(--text-primary)"),
                opacity:disabled?0.4:1,border:"1px solid "+(active?"var(--accent)":"var(--border)")}}>
              {s.icon} {s.label}
            </span>);
          })}
        </div>}
        {sideLoading?<div style={{padding:20}}><Loading text="로딩 중..." size="sm"/></div>:scope==="Base"?<>
          {/* Root-level DB files — legacy scope key remains "Base" for compatibility. */}
          <div style={{flex:1,overflow:"auto",padding:"6px 8px"}}>
            <div style={{fontSize:14,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>{baseDir||"운영 파일"} ({baseFileCount})</div>
            {baseItems.length===0&&<div style={{padding:"10px 12px",fontSize:14,color:"var(--text-secondary)"}}>표시할 ML_TABLE / 매칭 CSV / 제품 YAML / reformatter CSV 가 없습니다.</div>}
            {baseItems.map(f=>{
              const fileKey=f.path||f.name;
              const isSel=selBaseFile===fileKey;
              const kind=(f.kind||"file").toLowerCase();
              const isDir=kind==="dir";
              const isDirUp=kind==="dir_up";
              const extColor={parquet:"#10b981",csv:FB_INFO.fg,json:FB_AMBER,md:FB_DISABLED,yaml:"#eab308",yml:"#eab308",dir:FB_DISABLED}[f.ext]||FB_MUTED;
              const icon=isDirUp?"↩":({parquet:"📊",csv:"📋",json:"🔧",md:"📄",yaml:"⚙️",yml:"⚙️",dir:"📂"}[f.ext]||"📁");
              const displayName=baseDir&&!isDirUp?String(fileKey).replace(new RegExp("^"+baseDir.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")+"/"),""):f.name;
              const titlePath=[f.name];
              return(<div key={fileKey} className="filebrowser-base-file" data-file={fileKey} data-ext={f.ext}
                onClick={()=>{
                  if(isDirUp){
                    setBaseDir("");
                    setSelBaseMeta(null);
                    setSelBaseFile("");
                    setData(null);
                    setBaseRaw(null);
                    return;
                  }
                  if(isDir){
                    const nextDir=String(f.name||"").replace(/^.*:/,"").replace(/^\/+|\/+$/g,"");
                    setBaseDir(nextDir||"cache");
                    setSelBaseMeta(null);
                    setSelBaseFile("");
                    setData(null);
                    setBaseRaw(null);
                    return;
                  }
                  setSelectedCols([]);setSortSpec(null);setSelBaseMeta(f);loadBaseFileView(fileKey);setIsBaseEditing(false);setError("");setData(null);setBaseRaw(null);setEditCols([]);setEditRows([]);setEditOriginRows([]);
                }}
                title={(f.description||titlePath.join(" "))+ (f.role?`\n${f.role}`:"")}
                style={{...sidebarRowBase,alignItems:"flex-start",padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:14,marginBottom:1,
                  background:isSel?"var(--bg-hover)":"transparent",color:isSel?"var(--accent)":"var(--text-primary)"}}>
                {/* v8.7.5: Base 단일 파일도 S3 신호등 표시 (다운로드/업로드 양방향). */}
                {!isDir&&!isDirUp&&lightDot(fileKey)}
                <span style={{flexShrink:0,lineHeight:1.5}}>{icon}</span>
                <span style={sidebarStack}>
                  <span style={sidebarText} title={displayName}>{displayName}</span>
                  <span style={sidebarMetaLine}>
                    {!isDir&&!isDirUp&&lightFreshText(fileKey)}
                    {/* v8.7.7: `db` 소스 태그 제거 — Base 단일 파일은 소스 구분 없이 한 번만 표시. */}
                    {!isDir&&!isDirUp&&<>
                      <span style={{fontSize:11,padding:"1px 4px",borderRadius:3,background:extColor+"22",color:extColor,fontWeight:700,fontFamily:"monospace",flexShrink:0}}>{f.ext}</span>
                      <span style={sidebarMeta}>{formatSize(f.size)}</span>
                    </>}
                    {isDir&&<span style={{fontSize:11,padding:"1px 4px",borderRadius:3,background:extColor+"22",color:extColor,fontWeight:700,fontFamily:"monospace",flexShrink:0}}>DIR</span>}
                  </span>
                </span>
                {/* DB/root 원본은 read-only. Flow-i가 Files 영역에 등록한 uploads 파일만 삭제 가능. */}
                {isAdmin&&!isDir&&f.source==="uploads"&&<span
                  onClick={(e)=>{e.stopPropagation();deleteBaseFile(f.name);}}
                  title={"Files 등록 파일 삭제 (admin) — "+f.name+" 을 .trash 로 이동"}
                  style={{fontSize:14,lineHeight:1,padding:"1px 5px",borderRadius:3,cursor:"pointer",color:FB_BAD.fg,border:`1px solid ${FB_BAD.fg}55`,background:"transparent",flexShrink:0}}>
                  🗑
                </span>}
              </div>);
            })}
          </div>
        </>:<>
          <div style={{padding:"8px 12px"}}>
            {roots.map(r=>{
              // v8.4.3: icon + level badge 제거 — 깔끔한 이름만.
              return (
              <div key={r.name} onClick={()=>{setSelRoot(r.name);setSelectedCols([]);}} title={r.description||""} style={{...sidebarRowBase,alignItems:"flex-start",padding:"7px 12px",borderRadius:6,cursor:"pointer",fontSize:14,
                background:selRoot===r.name?"var(--bg-hover)":"transparent",fontWeight:selRoot===r.name?600:400,color:selRoot===r.name?"var(--accent)":"var(--text-primary)"}}>
                {lightDot(r.name)}
                <span style={sidebarStack}>
                  <span style={sidebarText}>{r.canonical||r.name}</span>
                  <span style={sidebarMetaLine}>
                    {lightFreshText(r.name)}
                    <span style={{...sidebarMeta,maxWidth:60}}>파일 {r.parquet_count}</span>
                  </span>
                </span>
              </div>);
            })}
          </div>
          {products.length>0&&<div style={{flex:1,overflow:"auto",borderTop:"1px solid var(--border)",padding:"4px 8px"}}>
            <div style={{fontSize:14,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>제품</div>
            {products.map(p=>(
              <div key={p.name} onClick={()=>{setSelectedCols([]);setSortSpec(null);setSql("");loadHiveView(selRoot,p.name,"",[],{full:true,page:0,sortOverride:null});}} style={{...sidebarRowBase,alignItems:"flex-start",padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:14,marginBottom:1,
                background:selProd===p.name?"var(--bg-hover)":"transparent",color:selProd===p.name?"var(--accent)":"var(--text-primary)"}}>
                {/* v8.8.2: 제품별 S3 신호등 — 본인 설정 없으면 상위 DB 에서 상속. */}
                {lightDot(selRoot+"/"+p.name)}
                <span style={sidebarStack}>
                  <span style={sidebarText} title={p.name}>{p.name}</span>
                  <span style={sidebarMetaLine}>
                    {lightFreshText(selRoot+"/"+p.name)}
                    <span style={sidebarMeta}>{p.latest_date}</span>
                  </span>
                </span>
              </div>))}
          </div>}
          {rootPqs.length>0&&<div style={{borderTop:"1px solid var(--border)",padding:"4px 8px",maxHeight:200,overflow:"auto"}}>
            <div style={{fontSize:14,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>루트 Parquet</div>
            {rootPqs.map(f=>(
              <div key={f.name} onClick={()=>{setSelectedCols([]);setSortSpec(null);loadRootPqView(f.name,"",[],{sortOverride:null});}} style={{...sidebarRowBase,alignItems:"flex-start",padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:14,marginBottom:1,
                background:selRootPq===f.name?"var(--bg-hover)":"transparent",color:selRootPq===f.name?"var(--accent)":"var(--text-primary)"}}>
                {lightDot(f.name)}
                <span style={sidebarStack}>
                  <span style={sidebarText} title={f.name}>📊 {f.name}</span>
                  <span style={sidebarMetaLine}>
                    {lightFreshText(f.name)}
                    <span style={sidebarMeta}>{formatSize(f.size)}</span>
                  </span>
                </span>
              </div>))}
          </div>}
        </>}
      </div>
      {/* Main */}
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
        {/* SQL Bar */}
        <div style={{padding:"10px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",gap:8,alignItems:"center"}}>
          <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",flexShrink:0}}>SQL:</span>
          <input value={sql} onChange={e=>setSql(e.target.value)} placeholder="예: PRODUCT LIKE '%ABC%' 또는 col == 'value'"
            disabled={mode==="base"&&isBaseEditing}
            style={{flex:1,padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace",outline:"none"}}
            onKeyDown={e=>e.key==="Enter"&&applySql()}/>
          <button onClick={applySql} disabled={mode==="base"&&isBaseEditing}
            style={{padding:"6px 14px",borderRadius:5,border:"none",background:mode==="base"&&isBaseEditing?"var(--border)": "var(--accent)",color:mode==="base"&&isBaseEditing?"var(--text-secondary)":"#fff",fontSize:14,fontWeight:600,cursor:mode==="base"&&isBaseEditing?"default":"pointer"}}>실행</button>
          <button onClick={()=>{setAiSqlOpen(true);setAiSqlResult(null);}} disabled={!data||mode==="base"&&isBaseEditing}
            style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:14,fontWeight:700,cursor:!data||mode==="base"&&isBaseEditing?"default":"pointer",opacity:!data||mode==="base"&&isBaseEditing?0.5:1}}>AI SQL</button>
          {data&&!(mode==="base"&&isBaseEditing)&&<button onClick={downloadCsv} title={`표시는 ${PAGE_SIZE}행, CSV는 서버 허용 한도까지 다운로드합니다.`} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,fontWeight:600,cursor:"pointer"}}>CSV</button>}
          {data&&!(mode==="base"&&isBaseEditing)&&<span style={{fontSize:12,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>CSV 최대 50만행/100MB</span>}
        </div>
        {sortSpec&&<div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
          <span style={{fontSize:13,color:"var(--text-secondary)",fontWeight:700,flexShrink:0}}>SORT:</span>
          <span style={{fontSize:13,color:"var(--text-primary)",fontFamily:"monospace",padding:"2px 7px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>{sortLabel(sortSpec)}</span>
          <button onClick={()=>{setSortSpec(null);applySql(sql,selectedCols,null);}} style={{padding:"3px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>해제</button>
        </div>}

        {/* Selected columns chips */}
        {!isBaseEditing&&selectedCols.length>0&&<div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",alignItems:"center",gap:6,flexWrap:"wrap"}}>
          <span style={{fontSize:14,color:"var(--text-secondary)",fontWeight:600,flexShrink:0}}>SELECT:</span>
          {selectedCols.map(c=><span key={c} style={chipActive} onClick={()=>toggleCol(c)}>{c} ×</span>)}
          <button onClick={applySelectedCols} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:600,cursor:"pointer"}}>적용</button>
          <button onClick={clearSelectedCols} style={{padding:"3px 10px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>초기화</button>
        </div>}

        {/* SQL Guide */}
        <div style={{padding:"0 16px"}}>
          <div onClick={()=>setShowGuide(!showGuide)} style={{fontSize:14,color:"var(--accent)",cursor:"pointer",padding:"4px 0"}}>
            {showGuide?"▼":"▶"} SQL 가이드</div>
          {showGuide&&<div style={{background:"var(--bg-card)",borderRadius:6,padding:"8px 12px",marginBottom:8,border:"1px solid var(--border)",fontSize:14,fontFamily:"monospace",lineHeight:1.8,color:"var(--text-secondary)"}}>
            <div>col_name == 'value' <span style={{color:"var(--accent)"}}>— 같음</span></div>
            <div>col_name LIKE '%pattern%' <span style={{color:"var(--accent)"}}>— 포함 (SQL LIKE)</span></div>
            <div>col_name NOT LIKE '%XX%' <span style={{color:"var(--accent)"}}>— 포함하지 않음</span></div>
            <div>(col_a &gt; 1) & (col_b == 'X') <span style={{color:"var(--accent)"}}>— AND</span></div>
            <div>col_name.is_in(['A','B','C']) <span style={{color:"var(--accent)"}}>— IN 리스트</span></div>
            <div>col_name.is_not_null() <span style={{color:"var(--accent)"}}>— NOT NULL</span></div>
            <div style={{color:"var(--accent)",marginTop:4}}>팁: 컬럼 탭에서 열 클릭 → SQL 삽입 / 체크 → 열 선택 보기</div>
          </div>}
        </div>

        {/* Error display */}
        {error&&<div style={{margin:"0 16px 8px",padding:"8px 12px",background:FB_BAD.bg,border:`1px solid ${FB_BAD.fg}`,borderRadius:6,fontSize:14,color:FB_BAD.fg}}>
          {error} <span onClick={()=>setError("")} style={{cursor:"pointer",marginLeft:8}}>✕</span>
        </div>}

        {/* Content */}
        <div style={{flex:1,overflow:"auto",padding:16}}>
          {loading&&<div style={{padding:40,textAlign:"center"}}><Loading text="로딩 중..."/></div>}
          {mode==="base"&&selBaseFile&&<div style={{margin:"10px 12px 0",padding:12,border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-secondary)"}}>
            <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap",marginBottom:8}}>
              <b style={{fontSize:14}}>Version History</b>
              <span style={{fontSize:12,color:baseVersioned?"var(--accent)":"var(--text-secondary)",fontFamily:"monospace"}}>
                {baseVersioned?`versioned · ${baseVersions.length}/${baseVersionCap}`:"preview only"}
              </span>
              {(baseVersionLoading||baseVersionPreviewLoading)&&<span style={{fontSize:12,color:"var(--text-secondary)"}}>loading...</span>}
              {baseVersionMsg&&<span style={{fontSize:12,color:baseVersionMsg.includes("완료")?FB_OK.fg:FB_BAD.fg}}>{baseVersionMsg}</span>}
              <input value={baseVersionFilter} onChange={e=>setBaseVersionFilter(e.target.value)} placeholder="filter actor/action/note" style={{marginLeft:"auto",padding:"3px 7px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,width:170}}/>
              <button onClick={()=>loadBaseVersions(selBaseFile)} style={{padding:"3px 8px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>새로고침</button>
            </div>
            {baseCurrentProfile&&<div style={{display:"flex",gap:10,flexWrap:"wrap",marginBottom:8,fontSize:12,color:"var(--text-secondary)",fontFamily:"monospace"}}>
              <span>현재 버전</span>
              <span>{baseCurrentProfile.rows??"-"}행 / {baseCurrentProfile.columns??"-"}열</span>
              <span>size={formatSize(baseCurrentProfile.size)}</span>
              <span>modified={(baseCurrentProfile.modified_at||"").replace("T"," ").slice(0,16)||"-"}</span>
            </div>}
            {baseVersioned&&baseVersions.length===0&&<div style={{fontSize:12,color:"var(--text-secondary)"}}>아직 저장된 이전 버전이 없습니다. 다음 저장부터 수정 전 snapshot 이 남습니다.</div>}
            {baseVersions.length>0&&<div style={{display:"flex",flexDirection:"column",gap:4,maxHeight:150,overflow:"auto"}}>
              {baseVersions.filter(v=>{
                const q=baseVersionFilter.trim().toLowerCase();
                if(!q)return true;
                return [v.version,v.actor,v.action,v.note,v.created_at].some(x=>String(x||"").toLowerCase().includes(q));
              }).map(v=><div key={v.version} style={{display:"grid",gridTemplateColumns:"58px minmax(120px,0.9fr) minmax(130px,1fr) 96px 72px 136px 82px 58px 70px",gap:8,alignItems:"center",fontSize:12,padding:"5px 6px",border:"1px solid var(--border)",borderRadius:5,background:"var(--bg-primary)"}}>
                <span style={{fontFamily:"monospace",fontWeight:900,color:String(v.version||"").startsWith("legacy_")?"#a855f7":"var(--accent)"}} title={v.storage_version||v.version}>{String(v.version||"-").startsWith("legacy_")?"legacy":v.version}</span>
                <span style={{color:"var(--text-primary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={`${v.version} · ${v.action||"edit"}`}>{v.note||v.action||"edit"}</span>
                <span style={{color:"#eab308",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontFamily:"monospace"}} title={JSON.stringify(v.change_summary||{})}>{versionChangeLabel(v.change_summary)}</span>
                <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{v.rows??"-"}행 / {v.columns??"-"}열</span>
                <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{formatSize(v.size)}</span>
                <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{(v.created_at||"").replace("T"," ").slice(0,16)||"-"}</span>
                <span style={{color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{v.actor||"-"}</span>
                <button onClick={()=>previewBaseVersion(v.version)} style={{padding:"3px 7px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>보기</button>
                <button onClick={()=>rollbackBaseVersion(v.version)} disabled={!isFileBrowserAdmin} style={{padding:"3px 7px",borderRadius:4,border:`1px solid ${FB_BAD.fg}`,background:"transparent",color:isFileBrowserAdmin?FB_BAD.fg:"var(--text-secondary)",fontSize:12,cursor:isFileBrowserAdmin?"pointer":"not-allowed"}}>롤백</button>
              </div>)}
            </div>}
            {baseVersionPreview&&<div style={{marginTop:10,border:"1px solid var(--border)",borderRadius:6,overflow:"hidden",background:"var(--bg-primary)"}}>
              <div style={{display:"flex",gap:8,alignItems:"center",padding:"7px 9px",borderBottom:"1px solid var(--border)",fontSize:12,flexWrap:"wrap"}}>
                <b style={{fontFamily:"monospace",color:"var(--accent)"}}>{baseVersionPreview.version}</b>
                <span style={{color:"var(--text-secondary)"}}>{baseVersionPreview.kind||"file"} preview</span>
                {baseVersionPreview.diff&&<span style={{color:baseVersionPreview.diff.checksum_equal?FB_OK.fg:"#eab308"}}>
                  {baseVersionPreview.diff.checksum_equal?"현재와 동일":"현재와 다름"}
                  {diffTableCountLabel(baseVersionPreview.diff_table)?` · ${diffTableCountLabel(baseVersionPreview.diff_table)}`:""}
                </span>}
                <span style={{color:"var(--text-secondary)",fontFamily:"monospace"}}>
                  {tableShapeLabel(baseVersionPreview.current_profile,"현재")} · {tableShapeLabel(baseVersionPreview.version_profile,"버전")}
                </span>
                <button onClick={()=>setBaseVersionPreview(null)} style={{marginLeft:"auto",padding:"2px 7px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>닫기</button>
              </div>
              {baseVersionPreview.diff&&(baseVersionPreview.diff.added_columns_in_current?.length||baseVersionPreview.diff.removed_columns_from_current?.length)?<div style={{padding:"6px 9px",fontSize:12,color:"var(--text-secondary)",borderBottom:"1px solid var(--border)"}}>
                {baseVersionPreview.diff.added_columns_in_current?.length?`현재에만 있는 컬럼: ${baseVersionPreview.diff.added_columns_in_current.slice(0,12).join(", ")}${baseVersionPreview.diff.added_columns_in_current.length>12?"...":""}`:""}
                {baseVersionPreview.diff.added_columns_in_current?.length&&baseVersionPreview.diff.removed_columns_from_current?.length?" / ":""}
                {baseVersionPreview.diff.removed_columns_from_current?.length?`버전에만 있는 컬럼: ${baseVersionPreview.diff.removed_columns_from_current.slice(0,12).join(", ")}${baseVersionPreview.diff.removed_columns_from_current.length>12?"...":""}`:""}
              </div>:null}
              {baseVersionPreview.diff_table?.rows?.length?<div style={{maxHeight:320,overflow:"auto"}}>
                <div style={{padding:"6px 9px",fontSize:12,color:"var(--text-secondary)",borderBottom:"1px solid var(--border)",display:"flex",gap:10,flexWrap:"wrap"}}>
                  <b style={{color:"var(--text-primary)"}}>직전 버전 대비 변경점</b>
                  <span>수정 {baseVersionPreview.diff_table.counts?.modified||0}</span>
                  <span>추가 {baseVersionPreview.diff_table.counts?.added||0}</span>
                  <span>삭제 {baseVersionPreview.diff_table.counts?.deleted||0}</span>
                  {baseVersionPreview.diff_table.truncated&&<span style={{color:FB_BAD.fg}}>일부만 표시됨</span>}
                </div>
                <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                  <thead><tr>{(baseVersionPreview.diff_table.columns||[]).map(c=><th key={c} style={{position:"sticky",top:0,background:"var(--bg-secondary)",borderBottom:"1px solid var(--border)",padding:5,textAlign:"left",zIndex:1}}>{c==="changed_cols"?"변경컬럼":c}</th>)}</tr></thead>
                  <tbody>{baseVersionPreview.diff_table.rows.map((r,i)=>{
                    const st=revStyle(r.rev);
                    const changed=new Set(r._changed_cols||[]);
                    return <tr key={i} style={{background:st.bg,color:st.fg,borderLeft:"4px solid "+st.line}}>{(baseVersionPreview.diff_table.columns||[]).map((c,ci)=>{
                      const isChanged=changed.has(c)&&r.rev==="수정";
                      return <td key={c} style={{borderBottom:"1px solid var(--border)",padding:5,fontFamily:ci<=1?"monospace":"inherit",fontWeight:c==="rev"||isChanged?800:500,background:isChanged?"#fde68a":undefined,color:st.fg,maxWidth:220,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={String(r[c]??"")}>{String(r[c]??"")}</td>;
                    })}</tr>;
                  })}</tbody>
                </table>
              </div>:null}
              {!baseVersionPreview.diff_table&&baseVersionPreview.text!=null?<pre style={{margin:0,padding:10,maxHeight:260,overflow:"auto",fontSize:12,lineHeight:1.45,whiteSpace:"pre-wrap",color:"var(--text-primary)"}}>{baseVersionPreview.text}</pre>:null}
              {!baseVersionPreview.diff_table&&baseVersionPreview.rows?.length?<div style={{maxHeight:260,overflow:"auto"}}>
                <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}><thead><tr>{(baseVersionPreview.columns||[]).map(c=><th key={c} style={{position:"sticky",top:0,background:"var(--bg-secondary)",borderBottom:"1px solid var(--border)",padding:5,textAlign:"left"}}>{c}</th>)}</tr></thead>
                <tbody>{baseVersionPreview.rows.map((r,i)=><tr key={i}>{(baseVersionPreview.columns||[]).map(c=><td key={c} style={{borderBottom:"1px solid var(--border)",padding:5}}>{String(r[c]??"")}</td>)}</tr>)}</tbody></table>
              </div>:null}
            </div>}
          </div>}
          {!loading&&!data&&!baseRaw&&!error&&<div style={{padding:60,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>사이드바에서 제품 또는 루트 parquet 을 선택하세요</div>}
          {!loading&&baseRaw&&<div className="filebrowser-base-raw" data-kind={baseRaw.kind}>
            <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12}}>
              <span style={{fontSize:14,fontWeight:600,flex:"1 1 220px",minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={baseRaw.file}>{baseRaw.file}</span>
              <span style={{fontSize:14,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"4px 10px",borderRadius:6,flexShrink:0}}>
                {baseRaw.kind==="json"?"JSON":baseRaw.kind==="yaml"?"YAML":"Markdown"} | {formatSize(baseRaw.size)}{baseRaw.truncated?" | 일부만 표시됨":""}
                {baseRaw.top_keys?.length&&<span style={{color:"var(--accent)",marginLeft:8}}>top: {baseRaw.top_keys.slice(0,6).join(", ")}{baseRaw.top_keys.length>6?"…":""}</span>}
              </span>
              {canEditRawBase&&!rawEditing&&<button onClick={()=>{setRawEditText(baseRaw.text||"");setRawEditing(true);}} style={{padding:"4px 10px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:12,fontWeight:700,cursor:"pointer"}}>편집</button>}
              {rawEditing&&<>
                <button onClick={saveRawBaseFile} style={{padding:"4px 10px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:"pointer"}}>저장</button>
                <button onClick={()=>{setRawEditing(false);setRawEditText("");}} style={{padding:"4px 10px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>취소</button>
              </>}
            </div>
            {rawEditing?<textarea value={rawEditText} onChange={e=>setRawEditText(e.target.value)} spellCheck={false}
              style={{width:"100%",minHeight:"55vh",boxSizing:"border-box",margin:0,padding:12,background:"var(--bg-card)",border:"1px solid var(--accent)",borderRadius:6,fontSize:14,lineHeight:1.5,fontFamily:"monospace",color:"var(--text-primary)",resize:"vertical"}}/>:
              <pre style={{margin:0,padding:12,background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:6,fontSize:14,lineHeight:1.5,fontFamily:"monospace",color:"var(--text-primary)",whiteSpace:"pre-wrap",wordBreak:"break-word",maxHeight:"calc(100vh - 240px)",overflow:"auto"}}>
                <code>{baseRaw.text}</code>
              </pre>}
          </div>}
            {!loading&&data&&<>
              <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12,flexWrap:"wrap"}}>
                {/* v8.8.31: 어떤 datalake 소스(FAB/INLINE/ET) 인지 한눈에 보이게 배지.
                     selRoot 이 "1.RAWDATA_DB_FAB" / "_INLINE" / "_ET" 로 끝나는지 판정. */}
              {mode==="hive" && selRoot && (()=>{
                const name=(selRoot||"").toUpperCase();
                let label="",bg="",fg="";
                if(name.endsWith("_FAB")||name.endsWith(".RAWDATA_DB_FAB")){label="FAB";bg="#3b82f622";fg="#1d4ed8";}
                else if(name.endsWith("_INLINE")){label="INLINE";bg="#10b98122";fg="#047857";}
                else if(name.endsWith("_ET")){label="ET";bg="#ec489922";fg="#be185d";}
                if(!label) return null;
                return <span title={`datalake 소스: ${label} (${selRoot})`}
                  style={{fontSize:14,fontWeight:700,fontFamily:"monospace",padding:"3px 10px",borderRadius:4,background:bg,color:fg,letterSpacing:0.5}}>{label}</span>;
              })()}
              <span style={{fontSize:14,fontWeight:600,flex:"1 1 220px",minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={selProd||selRootPq||selBaseFile}>{selProd||selRootPq||selBaseFile}</span>
                <span style={{fontSize:14,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"4px 10px",borderRadius:6,flexShrink:0}}>
                  {data.meta_only
                    ?<>스키마만 · {data.total_cols}열{data.row_count_unknown?<> · 행수 미계산</>:data.total_rows?<> · {data.total_rows.toLocaleString()}행</>:null}{data.all_columns_truncated?<> · 컬럼 일부 표시</>:null}</>
                    :<><span style={{color:"var(--accent)",fontWeight:700}}>{previewStatusLabel}</span> · 표시 {data.showing}행{!data.single_file_full_read&&data.preview_row_limit?<> / 최대 {data.preview_row_limit}행</>:null}{data.latest_order_col?<> · 기준 {data.latest_order_col}</>:null} | {data.total_rows?.toLocaleString()}행 × {data.total_cols}열
                       {data.selected_cols&&<span style={{color:"var(--accent)"}}> | {selectedCols.length||String(data.selected_cols).split(",").filter(Boolean).length}열 선택됨</span>}
                       {data.truncated_cols&&<span style={{color:"var(--accent)"}}> | 기본 미리보기 {data.preview_cols}열</span>}</>}
                  {data.source_modified&&<span title={data.source_path||""}> | 수정 {new Date(data.source_modified*1000).toLocaleString()}</span>}
                </span>
                {mode==="base"&&data.csv_rule_summary&&ruleCount(data.csv_rule_summary)>0&&<span title={JSON.stringify(data.csv_rule_summary)} style={{fontSize:12,fontWeight:700,padding:"4px 9px",borderRadius:5,background:FB_OK.bg,color:"#16a34a",fontFamily:"monospace"}}>
                  CSV rule {ruleCount(data.csv_rule_summary)}{data.csv_rule_summary.sort?` · sort ${data.csv_rule_summary.sort}`:""}
                </span>}
                <div style={{display:"inline-flex",alignItems:"center",gap:6,marginLeft:"auto",flexWrap:"wrap"}}>
                  {mode==="base"&&isFileBrowserAdmin&&BASE_EDIT_FILE_EXTS.has(baseFileExt)&&baseFileEditable&&
                    <button onClick={startBaseEdit} disabled={!canEnterBaseEdit}
                      title={!canEnterBaseEdit?(baseFileComplete? "편집 대상이 아닙니다. (Base/db_root 단일 CSV, Parquet만 가능)": "미리보기 전체 행이 필요합니다. 전체 조회 후 시작하세요.")
                      : "클립보드로 붙여넣고 저장할 수 있습니다."}
                      style={{padding:"5px 12px",borderRadius:5,border:"1px solid var(--accent)",background:canEnterBaseEdit?"var(--accent)":"transparent",color:canEnterBaseEdit?"#fff":"var(--text-secondary)",fontSize:14,fontWeight:600,cursor:canEnterBaseEdit?"pointer":"default"}}>편집</button>}
                  {isBaseEditingMode&&<>
                    <button onClick={saveBaseEdit} style={{padding:"5px 12px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:600,cursor:"pointer"}}>저장</button>
                    <button onClick={restoreBaseEdit} style={{padding:"5px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>원본복원</button>
                    <button onClick={cancelBaseEdit} style={{padding:"5px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>취소</button>
                    <span style={{fontSize:13,color:"var(--text-secondary)",display:"inline-flex",gap:6,alignItems:"center"}}>
                      <span>붙여넣기:</span>
                      <select value={pasteMode} onChange={e=>setPasteMode(e.target.value)} style={{padding:"2px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13}}>
                        <option value="replace">replace</option>
                        <option value="append">append</option>
                      </select>
                    </span>
                    <span style={{fontSize:13,color:"var(--text-secondary)",display:"inline-flex",gap:6,alignItems:"center"}}>
                      <span>저장 구분자:</span>
                      <select value={saveDelimiter} onChange={e=>setSaveDelimiter(e.target.value)} style={{padding:"2px 6px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13}}>
                        <option value="tab">tab</option>
                        <option value="comma">comma</option>
                        <option value="auto">auto</option>
                      </select>
                    </span>
                    <label style={{fontSize:13,color:"var(--text-secondary)",display:"inline-flex",gap:6,alignItems:"center",cursor:"pointer",lineHeight:1.2}}>
                      <input type="checkbox" checked={includeHeader} onChange={e=>setIncludeHeader(e.target.checked)}
                        style={{width:14,height:14}}/> 헤더 포함</label>
                    <span style={{padding:"2px 8px",borderRadius:4,border:"1px dashed var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:12}}>Ctrl+V 붙여넣기 안내</span>
                  </>}
                  {showBasePager&&<>
                    <button onClick={()=>gotoPage((data.page??page)-1)} disabled={(data.page??page)<=0}
                      style={{padding:"4px 9px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:(data.page??page)<=0?"default":"pointer",opacity:(data.page??page)<=0?0.45:1}}>이전</button>
                    <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>page {(data.page??page)+1}</span>
                    <button onClick={()=>gotoPage((data.page??page)+1)} disabled={!data.has_more}
                      style={{padding:"4px 9px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:data.has_more?"pointer":"default",opacity:data.has_more?1:0.45}}>다음</button>
                  </>}
                </div>
              </div>
              {/* Tabs: Data + Columns */}
              <div style={{display:"flex",gap:0,borderBottom:"1px solid var(--border)",marginBottom:12}}>
                {baseEditingTabs.map(t=>(<div key={t} onClick={()=>setTab(t)} style={{padding:"8px 16px",fontSize:14,cursor:"pointer",fontWeight:tab===t?600:400,
                  borderBottom:tab===t?"2px solid var(--accent)":"2px solid transparent",color:tab===t?"var(--text-primary)":"var(--text-secondary)"}}>
                  {t==="data"?"데이터 ("+data.showing+")":"컬럼 ("+allCols.length+")"}</div>))}
              </div>
              {tab==="data"&&isBaseEditingMode&&<>
                <div style={{margin:"0 0 8px",fontSize:12,color:"var(--text-secondary)"}}>
                  셀 기준 붙여넣기: 입력 중인 셀을 시작점으로 반영되며, 첫 행 헤더가 기존 헤더와 일치하면 자동으로 제외됩니다.
                </div>
                <div style={baseEditWrap} onPaste={onBasePaste}>
                  <table style={baseEditTable}>
                    <thead><tr>
                      <th style={baseEditCornerCell}>#</th>
                      {editCols.map((c,i)=>{
                        const isColActive = isBaseEditingMode&&selectedEditCell.c===i;
                        return <th key={i}
                          style={{...baseEditHeaderInput,background:isColActive? "#dbeafe":"var(--bg-tertiary)"}}>
                          {c}
                        </th>;
                      })}
                    </tr></thead>
                    <tbody>{editRows.length?editRows.map((row,ri)=>(
                      <tr key={ri}>
                        <td style={{...baseEditIndexBody, background:ri===selectedEditCell.r?baseEditRowHighlight.background||"rgba(59,130,246,0.12)":"var(--bg-tertiary)",padding:"0 6px"}}>
                          <span style={{display:"inline-flex",alignItems:"center",justifyContent:"space-between",gap:6,width:"100%"}}>
                            <span>{ri+1}</span>
                            <button type="button" onClick={(e)=>{e.stopPropagation();deleteBaseEditRow(ri);}}
                              title={`${ri+1}행 삭제`}
                              style={{width:22,height:22,borderRadius:4,border:`1px solid ${FB_BAD.fg}55`,background:FB_BAD.bg,color:FB_BAD.fg,fontSize:12,fontWeight:800,cursor:"pointer",lineHeight:1}}>
                              ×
                            </button>
                          </span>
                        </td>
                        {editCols.map((_,ci)=>{
                          const isRowActive = ri===selectedEditCell.r;
                          const isColActive = ci===selectedEditCell.c;
                          const isActiveCell = isRowActive&&isColActive;
                          const baseCellStyle={...baseEditRowCell,background:isActiveCell?baseEditCellActive.background: isRowActive?baseEditRowHighlight.background:isColActive?baseEditColHighlight.background:undefined};
                          return <td key={ci}
                            style={baseCellStyle}
                            onClick={()=>setSelectedEditCell({r:ri,c:ci})}>
                            <input value={String(row?.[ci]||"")} onChange={(e)=>patchBaseCell(ri,ci,e.target.value)}
                              onFocus={()=>setSelectedEditCell({r:ri,c:ci})}
                              onPaste={onBasePaste}
                              style={{...baseEditCellInput,...(isActiveCell?baseEditCellActive:{})}}
                              title={row?.[ci]||""}/>
                          </td>;
                        })}
                      </tr>)):<tr><td colSpan={Math.max(editCols.length+1,1)} style={{padding:"20px",textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>
                        데이터가 비어 있습니다. Ctrl+V로 붙여넣기 하거나 직접 입력해 저장하세요.
                      </td></tr>}</tbody>
                  </table>
                </div>
                <div style={{display:"flex",justifyContent:"flex-start",marginTop:10,gap:8}}>
                  <button onClick={addBaseEditRow} disabled={!editCols.length}
                    style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--accent)",background:"var(--bg-card)",color:"var(--accent)",fontSize:14,fontWeight:600,cursor:!editCols.length?"default":"pointer",opacity:!editCols.length?0.45:1}}>
                    행 추가
                  </button>
                  <button onClick={()=>deleteBaseEditRow()} disabled={!editRows.length}
                    style={{padding:"6px 12px",borderRadius:5,border:`1px solid ${FB_BAD.fg}`,background:FB_BAD.bg,color:FB_BAD.fg,fontSize:14,fontWeight:700,cursor:!editRows.length?"default":"pointer",opacity:!editRows.length?0.45:1}}>
                    선택 행 삭제
                  </button>
                </div>
              </>}
              {tab==="data"&&!isBaseEditingMode&&<div style={baseReadWrap}>
                <table style={baseEditTable}>
                  <thead><tr>
                    <th style={baseReadIndexCell}>#</th>
                    {(data.showing_cols||data.columns||[]).map((c,i)=><th key={i} style={{...baseEditHeaderInput,minWidth:160}}>{c}</th>)}</tr></thead>
                    <tbody>{data.data?.map((row,ri)=>(
                      <tr key={ri}><td style={{...baseReadIndexCell,color:FB_MUTED}}>{ri+1}</td>
                        {(data.showing_cols||data.columns||[]).map((c,ci)=><td key={ci}
                          style={baseReadCell}
                          title={String(row[c]||"")}>
                          {row[c]===null?<span style={{color:FB_MUTED}}>null</span>:String(row[c])}</td>)}</tr>))}</tbody>
                </table></div>}
              {tab==="columns"&&!isBaseEditingMode&&<div>
                <div style={{display:"flex",gap:8,marginBottom:8,alignItems:"center"}}>
                  <input value={colSearch} onChange={e=>setColSearch(e.target.value)} placeholder="컬럼 검색..."
                    style={{flex:1,padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"}}/>
                  {selectedCols.length>0&&<span style={{fontSize:14,color:"var(--accent)",fontWeight:600}}>{selectedCols.length}개 선택됨</span>}
                </div>
              <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:8,padding:"4px 0",lineHeight:1.6}}>
                클릭 → SQL 필터에 추가 | ☑ 체크 → 해당 열만 선택해서 보기
                {data.all_columns_truncated&&<span style={{color:"var(--accent)"}}> | schema {data.schema_columns_returned}/{data.total_cols}열 표시{remoteColsLoading?" · 검색 중":""}</span>}
              </div>
              <div style={{maxHeight:"calc(100vh - 340px)",overflow:"auto"}}>
                {displayCols.map((c,i)=>{
                  const isSelected=selectedCols.includes(c);
                  return(
                  <div key={i} style={{display:"flex",alignItems:"center",padding:"5px 12px",borderBottom:"1px solid var(--border)",fontSize:14,gap:8}}>
                    {/* Checkbox for column selection */}
                    <input type="checkbox" checked={isSelected} onChange={()=>toggleCol(c)}
                      style={{width:14,height:14,accentColor:"var(--accent)",cursor:"pointer",flexShrink:0}}/>
                    {/* Column name - click to insert into SQL */}
                    <span onClick={()=>insertColToSql(c)} style={{flex:1,cursor:"pointer",fontWeight:isSelected?600:500,color:isSelected?"var(--accent)":"var(--text-primary)"}} title={"클릭하면 SQL 필터에 추가됩니다"}>
                      {c}
                    </span>
                    {data.dtypes&&<span style={{fontSize:14,padding:"1px 6px",borderRadius:3,background:"var(--bg-tertiary)",color:"var(--accent)",flexShrink:0}}>{data.dtypes[c]}</span>}
                    <span onClick={()=>insertColToSql(c)} style={{fontSize:14,color:"var(--accent)",cursor:"pointer",padding:"2px 6px",borderRadius:3,background:"var(--accent-glow)",flexShrink:0}} title="SQL 필터에 추가">+ SQL</span>
                  </div>);})}
              </div>
              {selectedCols.length>0&&<div style={{marginTop:12,padding:"10px 12px",background:"var(--bg-card)",borderRadius:8,border:"1px solid var(--border)"}}>
                <div style={{fontSize:14,fontWeight:600,color:"var(--accent)",marginBottom:6}}>선택된 컬럼 ({selectedCols.length})</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:4,marginBottom:8}}>
                  {selectedCols.map(c=><span key={c} style={chipActive} onClick={()=>toggleCol(c)}>{c} ×</span>)}
                </div>
                <div style={{display:"flex",gap:6}}>
                  <button onClick={applySelectedCols} style={{padding:"6px 16px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:600,cursor:"pointer"}}>선택 적용</button>
                  <button onClick={clearSelectedCols} style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>모두 해제</button>
                </div>
              </div>}
            </div>}
          </>}
        </div>
      </div>
      {aiSqlOpen&&(
        <Modal open onClose={()=>setAiSqlOpen(false)} title="AI SQL 작성" width={560} zIndex={101}>
          <div style={{display:"grid",gap:10,fontSize:14,color:"var(--text-primary)"}}>
            <textarea value={aiSqlPrompt} onChange={e=>setAiSqlPrompt(e.target.value)} rows={4} spellCheck={false}
              placeholder="예: PRODA 제품에서 wafer 21이고 step이 ETCH인 행"
              style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"8px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"inherit",lineHeight:1.45,outline:"none"}}/>
            <div style={{display:"flex",alignItems:"center",gap:8,justifyContent:"space-between",flexWrap:"wrap"}}>
              <span style={{fontSize:12,color:"var(--text-secondary)"}}>작성하면 SQL과 선택 컬럼을 반영하고 바로 조회합니다.</span>
              <div style={{display:"flex",gap:8}}>
                <button onClick={()=>setAiSqlOpen(false)} style={{padding:"7px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>닫기</button>
                <button onClick={draftAiSql} disabled={aiSqlBusy} style={{padding:"7px 14px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:aiSqlBusy?"wait":"pointer",opacity:aiSqlBusy?0.6:1}}>{aiSqlBusy?"작성 중":"작성"}</button>
              </div>
            </div>
            {aiSqlResult&&<div style={{display:"grid",gap:5,padding:9,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",fontSize:12,fontFamily:"monospace",color:aiSqlResult.ok===false?FB_BAD.fg:"var(--text-secondary)",lineHeight:1.45}}>
              <span>llm={aiSqlResult.llm?.used?"used":(aiSqlResult.llm?.available?"available":"fallback")} · saved=false · draft={aiSqlResult.draft_id||"-"}</span>
              {aiSqlResult.feedback_context_used?<span>feedback: like {aiSqlResult.feedback_context?.positive||0} · dislike {aiSqlResult.feedback_context?.negative||0}</span>:null}
              {cleanSortSpec(aiSqlResult.sort)?<span>sort: {sortLabel(aiSqlResult.sort)}</span>:null}
              {aiSqlResult.selected_columns?.length?<span>selected: {aiSqlResult.selected_columns.join(", ")}</span>:null}
              {aiSqlResult.sample_profile?<span>profile: rows {aiSqlResult.sample_profile.rows_sampled||0} · cols {aiSqlResult.sample_profile.columns_scanned||0} · {aiSqlResult.sample_profile.source||"request"}</span>:null}
              {aiSqlResult.resolved_columns?.length?<span>resolved: {aiSqlResult.resolved_columns.join(", ")}</span>:null}
              {aiSqlResult.unknown_column_terms?.length?<span style={{color:FB_BAD.fg}}>unknown: {aiSqlResult.unknown_column_terms.join(", ")}</span>:null}
              {aiSqlResult.resolved_values?.length?<span>values: {aiSqlResult.resolved_values.join(", ")}</span>:null}
              {aiSqlResult.value_terms?.length?<span>value terms: {aiSqlResult.value_terms.join(", ")}</span>:null}
              {aiSqlResult.sql&&<span style={{color:"var(--accent)"}}>{aiSqlResult.sql}</span>}
              {(aiSqlResult.warnings||[]).slice(0,4).map((w,i)=><span key={i}>warn: {w}</span>)}
              {Array.isArray(aiSqlResult.alternatives)&&aiSqlResult.alternatives.length>0&&<div style={{display:"grid",gap:4,marginTop:4}}>
                {aiSqlResult.alternatives.map(alt=><button key={alt.key} onClick={()=>{const nextSort=cleanSortSpec(alt.sort);const altCols=Array.isArray(alt.selected_columns)?alt.selected_columns:[];setSql(alt.sql||"");setSelectedCols(altCols);setSortSpec(nextSort);applySql(alt.sql||"",altCols,nextSort);submitAiSqlFeedback("up","alternative "+alt.key,{sql:alt.sql||"",sort:nextSort||{},selected_columns:altCols,choice:alt.key});}} style={{textAlign:"left",padding:"5px 7px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontSize:12,cursor:"pointer",fontFamily:"inherit"}}>
                  {alt.key}안 {alt.label}: {(alt.sql||"(no filter)")}{cleanSortSpec(alt.sort)?` · ${sortLabel(alt.sort)}`:""}
                </button>)}
              </div>}
              <div style={{display:"flex",alignItems:"center",gap:6,marginTop:5,fontFamily:"inherit",flexWrap:"wrap"}}>
                <button onClick={()=>submitAiSqlFeedback("up","")} disabled={!!aiSqlFeedbackBusy} style={{padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:aiSqlResult.feedback_saved==="up"?"var(--accent-glow)":"transparent",color:"var(--accent)",fontSize:12,fontWeight:700,cursor:aiSqlFeedbackBusy?"wait":"pointer"}}>좋아요</button>
                <button onClick={()=>setAiSqlFeedbackReasonOpen(v=>!v)} disabled={!!aiSqlFeedbackBusy} style={{padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:aiSqlResult.feedback_saved==="down"?"rgba(239,68,68,.08)":"transparent",color:FB_BAD.fg,fontSize:12,fontWeight:700,cursor:aiSqlFeedbackBusy?"wait":"pointer"}}>싫어요</button>
                {aiSqlResult.feedback_saved?<span style={{color:"var(--text-secondary)"}}>feedback saved</span>:null}
              </div>
              {aiSqlFeedbackReasonOpen&&<div style={{display:"grid",gap:5,marginTop:4,fontFamily:"inherit"}}>
                <textarea value={aiSqlFeedbackReason} onChange={e=>setAiSqlFeedbackReason(e.target.value)} rows={2} placeholder="사유는 선택입니다"
                  style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:"var(--text-primary)",fontSize:12,fontFamily:"inherit",outline:"none"}}/>
                <div style={{display:"flex",gap:6,justifyContent:"flex-end"}}>
                  <button onClick={()=>setAiSqlFeedbackReasonOpen(false)} style={{padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>취소</button>
                  <button onClick={()=>submitAiSqlFeedback("down",aiSqlFeedbackReason)} disabled={!!aiSqlFeedbackBusy} style={{padding:"4px 8px",borderRadius:4,border:"none",background:FB_BAD.fg,color:"#fff",fontSize:12,fontWeight:700,cursor:aiSqlFeedbackBusy?"wait":"pointer"}}>싫어요 저장</button>
                </div>
              </div>}
            </div>}
          </div>
        </Modal>
      )}
      {/* v8.7.5: Admin S3 ingest gear — PageGear 스타일 통일 · 좌하단 */}
      {isFileBrowserAdmin&&<>
        <PageGearButton onClick={toggleS3Settings} title={isAdmin?"폴더 설정 / 파일 설정 / S3 동기화 / AWS 설정":"폴더 설정 / 파일 설정"} zIndex={97} />
        {s3Open&&<>
          <Modal open onClose={closeS3Settings} width={1040} zIndex={98}>
          <div style={{display:"flex",flexDirection:"column",maxHeight:"86vh"}}>
            <div style={{display:"flex",alignItems:"center",padding:"12px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",borderRadius:"10px 10px 0 0"}}>
              <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace",flex:1}}>{settingsTitle}</span>
              {!s3AwsOk&&<span style={{fontSize:14,padding:"2px 8px",borderRadius:4,background:FB_BAD.bg,color:FB_BAD.fg,marginRight:8}}>aws CLI 미설치</span>}
              <span onClick={closeS3Settings} style={{cursor:"pointer",color:"var(--text-secondary)",fontSize:18,padding:"0 4px"}}>✕</span>
            </div>
            {/* Tabs */}
            <div style={{display:"flex",gap:4,padding:"8px 12px",borderBottom:"1px solid var(--border)",background:"var(--bg-primary)"}}>
              {settingsTabs.map(t=>(
                <span key={t.k} onClick={()=>{setS3Tab(t.k);if(t.k==="add")setS3Form({id:"",kind:"db",target:"",s3_url:"",command:"sync",direction:"download",extra_args:"",endpoint_url:"",profile:"",interval_min:0,enabled:true});}} style={{padding:"5px 12px",borderRadius:5,fontSize:14,cursor:"pointer",fontWeight:s3Tab===t.k?700:500,background:s3Tab===t.k?"var(--accent-glow)":"transparent",color:s3Tab===t.k?"var(--accent)":"var(--text-secondary)"}}>{t.l}</span>
              ))}
            </div>
            <div style={{flex:1,overflow:"auto",padding:"12px 16px"}}>
              {s3Tab==="cache"&&<div style={{display:"grid",gap:12,fontSize:14}}>
                {fbCacheTargets.map(([target,title,desc,status])=>{
                  const isScheduled=target==="lot_progress";
                  const intervalMin=status?.interval_min_minutes||30;
                  const intervalMax=status?.interval_max_minutes||1440;
                  const nextAt=status?.next_refresh_at||status?.latest_cache?.next_refresh_at||"";
                  const nextLabel=nextAt?String(nextAt).slice(0,16).replace("T"," "):"-";
                  const scheduleOn=isScheduled&&status?.schedule_enabled!==false;
                  const sourceRootOptions=Array.from(new Set((status?.source_root_candidates||[]).map(c=>String(c?.source_root||"").trim()).filter(Boolean)));
                  const sourceCandidates=status?.source_root_candidates||[];
                  const effectiveRootText=(status?.effective_source_roots||status?.source_roots||[]).join(",")||status?.source_root||"-";
                  const latestKeyText=Array.isArray(status?.latest_key_columns)?status.latest_key_columns.join(" + "):(status?.latest_key_columns||"-");
                  const latestOrderText=Array.isArray(status?.latest_order_columns)?status.latest_order_columns.join(" > "):(status?.latest_order_columns||"-");
                  const stepMappingText=Array.isArray(status?.step_mapping_sources)?status.step_mapping_sources.join(", "):(status?.step_mapping_sources||"-");
                  const productBinding=status?.product_binding||{};
                  const manualPoints=status?.manual_change_points||{};
                  return(
                    <div key={target} style={{display:"grid",gap:8,padding:"10px 12px",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)"}}>
                      <div style={{display:"flex",gap:8,alignItems:"center",justifyContent:"space-between",flexWrap:"wrap"}}>
                        <div>
                          <div style={{fontSize:14,fontWeight:800,color:"var(--text-primary)"}}>{title}</div>
                          <div style={{fontSize:13,color:"var(--text-secondary)",marginTop:2}}>{desc}</div>
                        </div>
                        <button onClick={()=>refreshFilebrowserCache(target)} disabled={!isAdmin||fbCacheBusy===target}
                          title={!isAdmin?"admin only":"캐시 수동 갱신"}
                          style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:14,fontWeight:800,cursor:!isAdmin?"not-allowed":fbCacheBusy===target?"wait":"pointer",opacity:!isAdmin?0.5:1}}>
                          {fbCacheBusy===target?"갱신 중":"수동 갱신"}
                        </button>
                      </div>
                      {isScheduled?(
                        <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                          <span style={{fontSize:13,color:"var(--text-secondary)",fontWeight:700}}>자동 주기</span>
                          <input type="number" min={intervalMin} max={intervalMax} step="1" value={fbCacheInterval}
                            onChange={e=>setFbCacheInterval(e.target.value)}
                            disabled={!isAdmin||fbCacheSettingsBusy}
                            style={{width:82,padding:"5px 8px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13}}/>
                          <span style={{fontSize:13,color:"var(--text-secondary)"}}>분</span>
                          <label style={{display:"inline-flex",alignItems:"center",gap:6,fontSize:13,color:"var(--text-secondary)",fontWeight:700}}>
                            DB root
                            <input value={fbCacheSourceRoot} onChange={e=>setFbCacheSourceRoot(e.target.value)}
                              list={`${target}-source-root-options`}
                              placeholder="auto"
                              disabled={!isAdmin||fbCacheSettingsBusy}
                              style={{width:190,minWidth:140,padding:"5px 8px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace"}}/>
                            <datalist id={`${target}-source-root-options`}>
                              {sourceRootOptions.map(root=><option key={root} value={root}/>)}
                            </datalist>
                          </label>
                          <label style={{display:"inline-flex",alignItems:"center",gap:6,fontSize:13,color:"var(--text-secondary)",fontWeight:700,cursor:isAdmin?"pointer":"not-allowed"}}>
                            <input type="checkbox" checked={!!fbAutoS3Upload} disabled={!isAdmin||fbCacheSettingsBusy} onChange={e=>setFbAutoS3Upload(e.target.checked)} style={{width:14,height:14,accentColor:"var(--accent)"}}/>
                            저장/캐시 갱신 후 S3 업로드
                          </label>
                          <button onClick={saveFilebrowserCacheSchedule} disabled={!isAdmin||fbCacheSettingsBusy}
                            style={{padding:"5px 10px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontSize:13,fontWeight:700,cursor:!isAdmin?"not-allowed":fbCacheSettingsBusy?"wait":"pointer",opacity:!isAdmin?0.5:1}}>
                            {fbCacheSettingsBusy?"저장 중":"저장"}
                          </button>
                          <span style={{fontSize:13,color:scheduleOn?"var(--text-secondary)":FB_BAD.fg}}>다음 {nextLabel}</span>
                        </div>
                      ):(
                        <div style={{fontSize:13,color:"var(--text-secondary)",fontWeight:700}}>수동 전용</div>
                      )}
                      {isScheduled&&<div style={{display:"grid",gap:7,padding:"8px 10px",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-primary)"}}>
                        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,flexWrap:"wrap"}}>
                          <span style={{fontSize:13,fontWeight:900,color:"var(--text-primary)"}}>LOT 컬럼 매칭</span>
                          <button onClick={()=>setFbCacheColumnMapping(defaultLotProgressColumnMapping())} disabled={!isAdmin||fbCacheSettingsBusy}
                            style={{padding:"4px 8px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,fontWeight:700,cursor:!isAdmin?"not-allowed":"pointer",opacity:!isAdmin||fbCacheSettingsBusy?0.55:1}}>
                            기본값
                          </button>
                        </div>
                        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:7}}>
                          {LOT_PROGRESS_COLUMNS.map(col=><label key={col} style={{display:"grid",gap:3,minWidth:0,fontSize:12,fontWeight:800,color:"var(--text-secondary)"}}>
                            <span style={{fontFamily:"monospace"}}>{col}</span>
                            <input value={fbCacheColumnMapping[col]||col}
                              onChange={e=>setFbCacheColumnMapping(prev=>({...normalizeLotProgressColumnMapping(prev),[col]:e.target.value}))}
                              disabled={!isAdmin||fbCacheSettingsBusy}
                              style={{minWidth:0,padding:"5px 7px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:"var(--text-primary)",fontSize:12,fontFamily:"monospace"}}/>
                          </label>)}
                        </div>
                      </div>}
                      <div style={{display:"flex",gap:8,flexWrap:"wrap",fontFamily:"monospace",fontSize:13,color:"var(--text-secondary)"}}>
                        <span>target={target}</span>
                        <span>mode={isScheduled?"scheduled":"manual"}</span>
                        <span>products={status?.product_count??(status?.products||[]).length}</span>
                        <span>configured_db_root={status?.configured_source_root||"auto"}</span>
                        <span>effective_db_root={effectiveRootText}</span>
                        <span>rows={status?.row_count??status?.total_row_count??0}</span>
                        <span>scanned={status?.files_scanned??0}/{status?.rows_seen??0}</span>
                        <span>updated={status?.updated_at||status?.latest_updated_at||"-"}</span>
                        <span>fresh={status?.freshness_state||"-"}</span>
                        <span>success={String(status?.last_success_at||"-").slice(0,16).replace("T"," ")}</span>
                        {status?.running&&<span style={{color:FB_AMBER}}>running</span>}
                        {status?.skipped_by_lock&&<span style={{color:FB_AMBER}}>lock skip</span>}
                        {isScheduled&&status?.schedule_enabled===false&&<span style={{color:FB_BAD.fg}}>scheduler off</span>}
                      </div>
                      <div style={{display:"grid",gap:4,fontFamily:"monospace",fontSize:12,color:"var(--text-secondary)"}}>
                        <span>settings.json.lot_progress_source_root = {status?.configured_source_root||"auto"}</span>
                        <span>settings.json.lot_progress_column_mapping = {Object.keys(status?.column_mapping||fbCacheColumnMapping||{}).length} columns</span>
                        <span>effective DB root = {effectiveRootText}</span>
                        {status?.cache_path&&<span style={{overflowWrap:"anywhere"}}>cache path = {status.cache_path}</span>}
                        {status?.json_cache_path&&<span style={{overflowWrap:"anywhere"}}>json cache path = {status.json_cache_path}</span>}
                      </div>
                      {sourceCandidates.length>0&&<div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                        {sourceCandidates.map(c=><span key={`${c.source_root||c.path}-${c.origin||""}`} title={c.path} style={{display:"inline-flex",alignItems:"center",gap:5,padding:"3px 7px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:c.exists?"var(--text-secondary)":FB_BAD.fg,fontSize:12,fontFamily:"monospace"}}>
                          {c.source_root||"-"} · {c.exists?"exists":"missing"}
                        </span>)}
                      </div>}
                      <div style={{display:"grid",gap:4,fontSize:12,color:"var(--text-secondary)",lineHeight:1.45}}>
                        <div><b style={{color:"var(--text-primary)"}}>product binding</b> {productBinding.rule||"-"} <span style={{fontFamily:"monospace"}}>{productBinding.example_path_shape||""}</span></div>
                        <div><b style={{color:"var(--text-primary)"}}>latest key</b> <span style={{fontFamily:"monospace"}}>{latestKeyText}</span> · <b style={{color:"var(--text-primary)"}}>order</b> <span style={{fontFamily:"monospace"}}>{latestOrderText}</span></div>
                        <div><b style={{color:"var(--text-primary)"}}>source columns</b> lot_id=<span style={{fontFamily:"monospace"}}>{status?.lot_id_source_column||"lot_id"}</span> · root_lot_id=<span style={{fontFamily:"monospace"}}>{status?.root_lot_id_source_column||"root_lot_id"}</span> · wafer_id=<span style={{fontFamily:"monospace"}}>{status?.wafer_id_source_column||"wafer_id"}</span></div>
                        <div><b style={{color:"var(--text-primary)"}}>step mapping</b> <span style={{fontFamily:"monospace"}}>{stepMappingText}</span></div>
                        <div><b style={{color:"var(--text-primary)"}}>change points</b> DB root=<span style={{fontFamily:"monospace"}}>{manualPoints.db_root||"settings.json.lot_progress_source_root"}</span> · product=<span style={{fontFamily:"monospace"}}>{manualPoints.product_binding||"-"}</span> · latest=<span style={{fontFamily:"monospace"}}>{manualPoints.latest_rule||"-"}</span> · step=<span style={{fontFamily:"monospace"}}>{manualPoints.step_mapping||"-"}</span></div>
                      </div>
                      {status?.refresh_log_path&&<div style={{fontSize:12,color:"var(--text-secondary)",fontFamily:"monospace",overflowWrap:"anywhere"}}>log {status.refresh_log_path}</div>}
                      {status?.s3_sync&&<div style={{fontSize:12,color:"var(--text-secondary)",fontFamily:"monospace",overflowWrap:"anywhere"}}>s3 {status.s3_sync.status||status.s3_sync.reason||"-"}</div>}
                      {status?.error&&<div style={{fontSize:13,color:FB_BAD.fg}}>{status.error}</div>}
                    </div>
                  );
                })}
                <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                  <button onClick={loadFilebrowserCacheStatus} style={{padding:"7px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontSize:14,fontWeight:700,cursor:"pointer"}}>상태 새로고침</button>
                  {fbCacheMsg&&<span style={{fontSize:14,color:fbCacheMsg.includes("실패")||fbCacheMsg.includes("비활성")?FB_BAD.fg:"var(--text-secondary)"}}>{fbCacheMsg}</span>}
                </div>
              </div>}
              {s3Tab==="folder"&&<div style={{maxWidth:520,display:"flex",flexDirection:"column",gap:10,fontSize:14}}>
                <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                  Files로 노출할 DB 폴더
                  <textarea value={fbHiddenDbDirsText} onChange={e=>setFbHiddenDbDirsText(e.target.value)} rows={4} spellCheck={false} placeholder={"cache\nreformatter"} style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace",lineHeight:1.45}}/>
                </label>
                <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                  폴더 안 파일 버전 관리
                  <textarea value={fbVersionedDirsText} onChange={e=>setFbVersionedDirsText(e.target.value)} rows={3} spellCheck={false} placeholder={"reformatter"} style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace",lineHeight:1.45}}/>
                </label>
                <div style={{fontSize:12,color:"var(--text-secondary)",lineHeight:1.45}}>버전 이력은 flow-data/file_versions에 저장되고, 파일 목록에는 별도 파일로 노출되지 않습니다.</div>
                <button onClick={()=>saveFilebrowserSettings("folder")} disabled={fbSettingsLoading} style={{alignSelf:"flex-start",padding:"8px 12px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:800,cursor:fbSettingsLoading?"default":"pointer",opacity:fbSettingsLoading?0.5:1}}>저장</button>
                {fbSettingsMsg&&<div style={{padding:9,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:fbSettingsMsg.includes("실패")||fbSettingsMsg.includes("오류")?FB_BAD.fg:"var(--text-secondary)",lineHeight:1.45}}>{fbSettingsMsg}</div>}
              </div>}
              {s3Tab==="file"&&<div style={{display:"grid",gridTemplateColumns:"minmax(180px,240px) 1fr",gap:14,fontSize:14}}>
                <div style={{display:"flex",flexDirection:"column",gap:10}}>
                  <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                    CSV 전체 표시 기준 (MB)
                    <input type="number" min={0} step={0.5} value={fbThresholdMb} onChange={e=>setFbThresholdMb(e.target.value)} style={{padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
                  </label>
                  <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                    CSV 다운로드 최대 크기 (MB)
                    <input type="number" min={1} max={Math.round((fbSettings.max_csv_download_max_bytes||100000000)/1048576)} step={1} value={fbDownloadMb} onChange={e=>setFbDownloadMb(e.target.value)} style={{padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
                  </label>
                  <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                    CSV 다운로드 최대 행 (보조)
                    <input type="number" min={1} max={fbSettings.max_csv_download_max_rows||500000} step={1000} value={fbDownloadRows} onChange={e=>setFbDownloadRows(e.target.value)} style={{padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
                  </label>
                  <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                    CSV 파일
                    <select value={fbSelectedFile} onChange={e=>selectFileRule(e.target.value)} style={{padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}>
                      <option value="">CSV 선택</option>
                      {csvBaseFiles.map(f=><option key={f.path||f.name} value={f.path||f.name}>{f.path||f.name}</option>)}
                    </select>
                  </label>
                  <button onClick={testFileRule} disabled={!fbSelectedFile||fbSettingsLoading} style={{padding:"8px 12px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,fontWeight:700,cursor:!fbSelectedFile||fbSettingsLoading?"default":"pointer",opacity:!fbSelectedFile||fbSettingsLoading?0.5:1}}>검증 테스트</button>
                  <button onClick={()=>saveFilebrowserSettings("file")} disabled={fbSettingsLoading} style={{padding:"8px 12px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:800,cursor:fbSettingsLoading?"default":"pointer",opacity:fbSettingsLoading?0.5:1}}>저장</button>
                  <button onClick={()=>selectFileRule(fbSelectedFile,{...fbSettings,csv_rules:{...(fbSettings.csv_rules||{}),[fbSelectedFile]:{}}})} disabled={!fbSelectedFile} style={{padding:"7px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:!fbSelectedFile?"default":"pointer"}}>규칙 비우기</button>
                  <div style={{display:"grid",gap:7,padding:"9px 10px",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)"}}>
                    <div style={{fontSize:13,fontWeight:800,color:"var(--text-primary)"}}>LLM으로 규칙 초안</div>
                    <textarea value={fbSettingsLlmPrompt} onChange={e=>setFbSettingsLlmPrompt(e.target.value)} rows={3} spellCheck={false} disabled={!fbSelectedFile||fbSettingsLlmBusy}
                      style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace",lineHeight:1.45}}/>
                    <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                      <button onClick={draftFileRuleByLlm} disabled={!fbSelectedFile||fbSettingsLlmBusy} style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:13,fontWeight:800,cursor:!fbSelectedFile?"default":fbSettingsLlmBusy?"wait":"pointer",opacity:!fbSelectedFile||fbSettingsLlmBusy?0.55:1}}>{fbSettingsLlmBusy?"작성 중":"초안 생성"}</button>
                      <button onClick={applyFileRuleDraft} disabled={!fbDraftRule} style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontSize:13,fontWeight:700,cursor:!fbDraftRule?"default":"pointer",opacity:!fbDraftRule?0.55:1}}>초안 적용</button>
                    </div>
                    {fbSettingsLlmDraft&&<div style={{display:"grid",gap:4,fontSize:12,color:fbSettingsLlmDraft.ok===false?"var(--danger,#ef4444)":"var(--text-secondary)",fontFamily:"monospace",lineHeight:1.4}}>
                      <span>llm={fbSettingsLlmDraft.llm?.used?"used":(fbSettingsLlmDraft.llm?.available?"available":"fallback")} · saved={String(!!fbSettingsLlmDraft.saved)}</span>
                      {(fbSettingsLlmDraft.warnings||[]).slice(0,4).map((w,i)=><span key={i}>warn: {w}</span>)}
                      {fbSettingsLlmDraft.error&&<span>{fbSettingsLlmDraft.error}</span>}
                      {fbDraftRule&&<div style={{display:"grid",gap:6,marginTop:4,padding:8,border:"1px solid var(--border)",borderRadius:5,background:"var(--bg-primary)",fontFamily:"inherit"}}>
                        <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center"}}>
                          <span style={{fontWeight:800,color:"var(--text-primary)"}}>초안 미리보기</span>
                          <span style={{color:"var(--text-secondary)"}}>{fbDraftRuleSections.reduce((sum,s)=>sum+s.groups.length,0)} groups</span>
                        </div>
                        {fbDraftRuleSections.some(section=>section.groups.length)?<div style={{display:"grid",gap:7}}>
                          {fbDraftRuleSections.map(section=>section.groups.length?<div key={section.key} style={{display:"grid",gap:5}}>
                            <span style={{fontWeight:900,color:"var(--text-primary)"}}>{section.label}</span>
                            {section.groups.map(group=><div key={group.key} style={{display:"grid",gap:3,paddingLeft:8,borderLeft:"2px solid var(--border)"}}>
                              <span style={{fontWeight:800,color:"var(--accent)",textTransform:"uppercase"}}>{group.label}</span>
                              {group.items.slice(0,8).map((item,i)=><span key={i} style={{color:"var(--text-primary)",overflowWrap:"anywhere"}}>{item}</span>)}
                              {group.items.length>8&&<span style={{color:"var(--text-secondary)"}}>+{group.items.length-8}</span>}
                            </div>)}
                          </div>:null)}
                        </div>:<span>적용할 규칙이 없습니다.</span>}
                        <details>
                          <summary style={{cursor:"pointer",fontWeight:800,color:"var(--text-secondary)"}}>JSON</summary>
                          <pre style={{margin:"6px 0 0",maxHeight:220,overflow:"auto",whiteSpace:"pre-wrap",fontSize:12,lineHeight:1.45,color:"var(--text-primary)"}}>{JSON.stringify(fbDraftRule,null,2)}</pre>
                        </details>
                      </div>}
                    </div>}
                  </div>
                  {fbSettingsMsg&&<div style={{padding:9,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:fbSettingsMsg.includes("실패")||fbSettingsMsg.includes("오류")?FB_BAD.fg:"var(--text-secondary)",lineHeight:1.45}}>{fbSettingsMsg}</div>}
                </div>
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,alignItems:"start"}}>
                  <div style={{gridColumn:"1 / -1",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)",overflow:"hidden"}}>
                    <div style={{padding:"8px 10px",borderBottom:"1px solid var(--border)",fontWeight:800,color:"var(--text-primary)"}}>적용 규칙</div>
                    {fbActiveRuleSections.some(section=>section.groups.length)?<div style={{display:"grid",gap:10,padding:10}}>
                      {fbActiveRuleSections.map(section=>section.groups.length?<div key={section.key} style={{display:"grid",gap:7}}>
                        <div style={{fontSize:13,fontWeight:900,color:"var(--text-primary)"}}>{section.label}</div>
                        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:8}}>
                          {section.groups.map(group=><div key={group.key} style={{display:"grid",gap:5,minWidth:0}}>
                            <div style={{fontSize:12,fontWeight:800,color:"var(--text-secondary)",textTransform:"uppercase"}}>{group.label}</div>
                            <div style={{display:"flex",gap:5,flexWrap:"wrap",minWidth:0}}>
                              {group.items.slice(0,12).map((item,i)=><span key={i} title={item} style={{maxWidth:"100%",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",padding:"2px 7px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,fontFamily:"monospace"}}>{item}</span>)}
                              {group.items.length>12&&<span style={{padding:"2px 7px",borderRadius:4,border:"1px solid var(--border)",color:"var(--text-secondary)",fontSize:12}}>+{group.items.length-12}</span>}
                            </div>
                          </div>)}
                        </div>
                      </div>:null)}
                    </div>:<div style={{padding:"9px 10px",fontSize:12,color:"var(--text-secondary)"}}>현재 form에 적용된 CSV 규칙이 없습니다.</div>}
                  </div>
                  {[
                    {key:"validation",label:"검증로직",caption:"실패하면 저장이 차단됩니다.",fields:validationRuleFields},
                    {key:"sort",label:"정렬로직",caption:"검증 통과 시 저장 정렬 적용",fields:sortRuleFields},
                  ].map(section=><div key={section.key} style={{gridColumn:"1 / -1",display:"grid",gap:8,padding:10,border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)"}}>
                    <div style={{display:"flex",alignItems:"baseline",gap:8,flexWrap:"wrap"}}>
                      <span style={{fontSize:14,fontWeight:900,color:"var(--text-primary)"}}>{section.label}</span>
                      <span style={{fontSize:12,color:"var(--text-secondary)"}}>{section.caption}</span>
                    </div>
                    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:10}}>
                      {section.fields.map(([key,label,ph])=><label key={key} style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                        {label}
                        <textarea value={fbRuleForm[key]||""} onChange={e=>setFbRuleForm(f=>({...f,[key]:e.target.value}))} placeholder={ph} rows={key==="conditions"||key==="ordered_by"||key==="sort"?3:2} spellCheck={false}
                          style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace",lineHeight:1.45}}/>
                      </label>)}
                    </div>
                  </div>)}
                  {fbValidation&&<div style={{gridColumn:"1 / -1",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)",overflow:"hidden"}}>
                    <div style={{padding:"8px 10px",borderBottom:"1px solid var(--border)",fontWeight:800,color:fbValidation.ok?"#16a34a":FB_BAD.fg}}>{fbValidation.ok?"검증 통과":"검증 오류"} · {fbValidation.error_count||0}건</div>
                    {fbValidation.errors?.length?<div style={{maxHeight:170,overflow:"auto"}}>
                      {fbValidation.errors.slice(0,50).map((err,i)=><div key={i} style={{display:"grid",gridTemplateColumns:"54px 100px 1fr",gap:8,padding:"6px 10px",borderBottom:"1px solid var(--border)",fontSize:12}}>
                        <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{err.row?`row ${err.row}`:"-"}</span>
                        <span style={{fontFamily:"monospace",color:"var(--accent)"}}>{err.column||err.rule}</span>
                        <span>{err.message}</span>
                      </div>)}
                    </div>:<div style={{padding:"8px 10px",fontSize:12,color:"var(--text-secondary)"}}>검증 통과 시 저장 정렬 적용</div>}
                  </div>}
                </div>
              </div>}
              {/* ITEMS tab */}
              {s3Tab==="items"&&<>
                {s3Items.length===0?<div style={{padding:30,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>설정된 S3 동기화 항목이 없습니다. <b>+ 추가</b> 를 클릭해 생성하세요.</div>
                :<table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
                  <thead><tr style={{background:"var(--bg-secondary)"}}>
                    {["","타겟","종류","방향","S3 URL","명령","주기","다음","마지막","동작"].map(h=>(
                      <th key={h} style={{padding:"6px 8px",textAlign:"left",fontSize:14,fontWeight:700,color:"var(--text-secondary)",borderBottom:FB_GRID_LINE,whiteSpace:"nowrap"}}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {s3Items.map(it=>{
                      const st=it.status||{};const s=st.last_status||"never";
                      const badge={ok:{c:FB_OK.fg,bg:"#22c55e22",t:"OK"},error:{c:FB_BAD.fg,bg:"#ef444422",t:"ERR"},running:{c:FB_AMBER,bg:"#f59e0b22",t:"RUN"},never:{c:FB_DISABLED,bg:"#94a3b822",t:"—"}}[s]||{c:FB_DISABLED,bg:"#94a3b822",t:s};
                      const isRunning=it.is_running||s==="running";
                      return(<tr key={it.id} style={{borderBottom:FB_GRID_LINE,opacity:it.enabled===false?0.5:1}}>
                        <td style={{padding:"6px 8px"}}><span style={{fontSize:14,padding:"2px 6px",borderRadius:3,background:badge.bg,color:badge.c,fontWeight:700,fontFamily:"monospace"}}>{badge.t}</span></td>
                        <td style={{padding:"6px 8px",fontFamily:"monospace",fontWeight:600}}>{it.target}</td>
                        <td style={{padding:"6px 8px",fontSize:14,color:"var(--text-secondary)"}}>{it.kind}</td>
                        <td style={{padding:"6px 8px",fontSize:14,fontWeight:700,color:(it.direction||"download")==="upload"?FB_AMBER:FB_INFO.fg}}>{(it.direction||"download")==="upload"?"⬆ 업":"⬇ 다"}</td>
                        <td style={{padding:"6px 8px",fontFamily:"monospace",fontSize:14,maxWidth:220,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={it.s3_url}>{it.s3_url}</td>
                        <td style={{padding:"6px 8px",fontSize:14}}>{it.command}</td>
                        <td style={{padding:"6px 8px",fontSize:14}}>{Number(it.interval_min)>0?it.interval_min+"분":"수동"}</td>
                        <td style={{padding:"6px 8px",fontSize:14,color:isRunning?FB_AMBER:"var(--text-secondary)"}}>{isRunning?"실행 중…":s3FmtETA(it)}</td>
                        <td style={{padding:"6px 8px",fontSize:14,color:"var(--text-secondary)"}}>
                          {st.last_end?<span title={"exit="+st.last_exit_code+" dur="+st.last_duration_sec+"s"}>{st.last_end.slice(5,16).replace("T"," ")}</span>:"-"}
                          {st.last_output_tail&&<span onClick={()=>setS3Detail({id:it.id,tail:st.last_output_tail,cmd:it.s3_url,exit:st.last_exit_code})} style={{marginLeft:4,cursor:"pointer",color:"var(--accent)"}}>로그</span>}
                        </td>
                        <td style={{padding:"6px 8px",whiteSpace:"nowrap"}}>
                          <button disabled={isRunning} onClick={()=>s3Run(it.id)} style={{padding:"3px 8px",borderRadius:3,border:"none",background:isRunning?FB_DISABLED:"var(--accent)",color:"#fff",fontSize:14,cursor:isRunning?"default":"pointer",marginRight:3}}>▶ 실행</button>
                          <button onClick={()=>{setS3Form({...it});setS3Tab("add");}} style={{padding:"3px 8px",borderRadius:3,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer",marginRight:3}}>수정</button>
                          <button onClick={()=>s3Delete(it.id)} style={{padding:"3px 8px",borderRadius:3,border:`1px solid ${FB_BAD.fg}`,background:"transparent",color:FB_BAD.fg,fontSize:14,cursor:"pointer"}}>✕</button>
                        </td>
                      </tr>);
                    })}
                  </tbody>
                </table>}
              </>}
              {/* ADD/EDIT tab */}
              {s3Tab==="add"&&s3Form&&<div style={{maxWidth:620}}>
                <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginBottom:10}}>{s3Form.id?"수정: "+s3Form.id:"새 S3 동기화 항목"}</div>
                <div style={{display:"grid",gridTemplateColumns:"120px 1fr",rowGap:10,columnGap:10,fontSize:14,alignItems:"center"}}>
                  <label>종류</label>
                  <div style={{display:"flex",gap:6}}>
                    {["db","root_parquet"].map(k=>(
                      <span key={k} onClick={()=>setS3Form(f=>({...f,kind:k,target:"",command:k==="root_parquet"?"cp":"sync"}))} style={{padding:"4px 10px",borderRadius:4,fontSize:14,cursor:"pointer",fontWeight:s3Form.kind===k?700:500,background:s3Form.kind===k?"var(--accent-glow)":"var(--bg-hover)",color:s3Form.kind===k?"var(--accent)":"var(--text-secondary)",border:"1px solid "+(s3Form.kind===k?"var(--accent)":"var(--border)")}}>{k}</span>
                    ))}
                  </div>
                  <label>타겟</label>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <input list="s3-target-list" value={s3Form.target} onChange={e=>setS3Form(f=>({...f,target:e.target.value}))} placeholder={s3Form.kind==="db"?"예: DB/1.RAWDATA/제품명 (슬래시로 하위 경로 지정 가능)":"예: root_file.parquet"} style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}/>
                    <datalist id="s3-target-list">{(s3Form.kind==="db"?s3Avail.dbs:s3Avail.root_parquets).map(x=><option key={x.name} value={x.name}/>)}</datalist>
                    <span style={{fontSize:14,color:"var(--text-secondary)"}}>DB_BASE 하위 경로. 슬래시(/)로 하위 디렉터리까지 지정 가능 — 예: <code>DB/1.RAWDATA/제품명</code></span>
                  </div>
                  <label>S3 URL</label>
                  <input value={s3Form.s3_url} onChange={e=>setS3Form(f=>({...f,s3_url:e.target.value}))} placeholder={s3Form.kind==="db"?"s3://bucket/prefix/INLINE/":"s3://bucket/prefix/file.parquet"} style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}/>
                  <label>명령</label>
                  <div style={{display:"flex",gap:6}}>
                    {["sync","cp"].map(c=>{
                      const disabled=c==="sync"&&s3Form.kind==="root_parquet";
                      return(<span key={c} onClick={()=>!disabled&&setS3Form(f=>({...f,command:c}))} style={{padding:"4px 10px",borderRadius:4,fontSize:14,cursor:disabled?"not-allowed":"pointer",opacity:disabled?0.4:1,fontWeight:s3Form.command===c?700:500,background:s3Form.command===c?"var(--accent-glow)":"var(--bg-hover)",color:s3Form.command===c?"var(--accent)":"var(--text-secondary)",border:"1px solid "+(s3Form.command===c?"var(--accent)":"var(--border)")}}>{c}</span>);
                    })}
                  </div>
                  {/* v8.8.0: 동기화 방향 선택 */}
                  <label>방향</label>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <div style={{display:"flex",gap:6}}>
                      {[{k:"download",l:"⬇ 다운로드 (S3 → 로컬)"},{k:"upload",l:"⬆ 업로드 (로컬 → S3)"}].map(d=>(
                        <span key={d.k} onClick={()=>setS3Form(f=>({...f,direction:d.k}))} style={{padding:"4px 10px",borderRadius:4,fontSize:14,cursor:"pointer",fontWeight:(s3Form.direction||"download")===d.k?700:500,background:(s3Form.direction||"download")===d.k?"var(--accent-glow)":"var(--bg-hover)",color:(s3Form.direction||"download")===d.k?"var(--accent)":"var(--text-secondary)",border:"1px solid "+((s3Form.direction||"download")===d.k?"var(--accent)":"var(--border)")}}>{d.l}</span>
                      ))}
                    </div>
                    <span style={{fontSize:14,color:"var(--text-secondary)"}}>업로드 선택 시 로컬 타겟이 src, S3 URL 이 dst 가 됩니다. cp + 디렉토리는 자동 --recursive.</span>
                  </div>
                  <label>엔드포인트 URL</label>
                  <input value={s3Form.endpoint_url||""} onChange={e=>setS3Form(f=>({...f,endpoint_url:e.target.value}))} placeholder="(선택) https://s3.internal.company:9000" style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}/>
                  <label>AWS 키(프로필)</label>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <select value={s3Form.profile||""} onChange={e=>setS3Form(f=>({...f,profile:e.target.value}))} style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}>
                      <option value="">(기본) 자격증명 자동 선택</option>
                      {s3Profiles.map(p=><option key={p.profile} value={p.profile}>{p.profile}{p.aws_access_key_id?" · "+p.aws_access_key_id.slice(0,8)+"…":""}</option>)}
                    </select>
                    <span style={{fontSize:14,color:"var(--text-secondary)"}}>선택 시 <code>--profile</code>로 해당 키를 사용해 전송/다운로드. AWS 설정 탭에서 프로필 관리.</span>
                  </div>
                  <label>추가 인자</label>
                  <input value={s3Form.extra_args} onChange={e=>setS3Form(f=>({...f,extra_args:e.target.value}))} placeholder="--exclude '*.tmp' --delete --size-only" style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}/>
                  <label>주기 (분)</label>
                  <div style={{display:"flex",gap:6,alignItems:"center"}}>
                    <input type="number" min={0} max={10080} value={s3Form.interval_min} onChange={e=>setS3Form(f=>({...f,interval_min:Number(e.target.value||0)}))} style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,width:100}}/>
                    <span style={{fontSize:14,color:"var(--text-secondary)"}}>0 = 수동 전용. 예: 60 = 매시간, 1440 = 매일</span>
                  </div>
                  <label>활성화</label>
                  <label style={{display:"flex",alignItems:"center",gap:6,cursor:"pointer"}}><input type="checkbox" checked={s3Form.enabled!==false} onChange={e=>setS3Form(f=>({...f,enabled:e.target.checked}))} style={{width:14,height:14,accentColor:"var(--accent)"}}/><span style={{fontSize:14}}>예약 + 수동 실행</span></label>
                </div>
                <div style={{marginTop:14,padding:10,background:"var(--bg-secondary)",borderRadius:6,fontSize:14,fontFamily:"monospace",color:"var(--text-secondary)",lineHeight:1.5}}>
                  <div style={{color:"var(--accent)",fontWeight:700,marginBottom:4}}># 미리보기 (dry · 방향: {(s3Form.direction||"download")==="upload"?"⬆ 업로드":"⬇ 다운로드"}):</div>
                  {(s3Form.direction||"download")==="upload"
                    ? <>aws s3 {s3Form.command} {"{DB_BASE}/"+(s3Form.target||"TARGET")} {s3Form.s3_url||"s3://..."} {s3Form.endpoint_url?"--endpoint-url "+s3Form.endpoint_url+" ":""}{s3Form.profile?"--profile "+s3Form.profile+" ":""}{s3Form.extra_args}</>
                    : <>aws s3 {s3Form.command} {s3Form.s3_url||"s3://..."} {"{DB_BASE}/"+(s3Form.target||"TARGET")} {s3Form.endpoint_url?"--endpoint-url "+s3Form.endpoint_url+" ":""}{s3Form.profile?"--profile "+s3Form.profile+" ":""}{s3Form.extra_args}</>}
                </div>
                <div style={{display:"flex",gap:8,marginTop:16}}>
                  <button onClick={()=>s3Save(s3Form)} style={{padding:"8px 18px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontWeight:700,fontSize:14,cursor:"pointer"}}>저장</button>
                  <button onClick={()=>{setS3Form(null);setS3Tab("items");}} style={{padding:"8px 16px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>취소</button>
                </div>
                <div style={{marginTop:14,fontSize:14,color:"var(--text-secondary)",lineHeight:1.6}}>
                  <b>허용 플래그:</b> --delete --exact-timestamps --dryrun --size-only --quiet --no-progress --recursive --only-show-errors --no-verify-ssl<br/>
                  <b>값이 있는 플래그:</b> --exclude VAL --include VAL --storage-class VAL --sse VAL --endpoint-url URL --profile NAME --region REGION --ca-bundle PATH<br/>
                  <b>참고:</b> 타겟 경로는 항상 DB_BASE 하위. sync 는 디렉토리 전용입니다.<br/><b>엔드포인트 URL:</b> 위 전용 필드 사용, 또는 <b>Admin → AWS Config</b> 에서 전역 자격/엔드포인트 설정.
                </div>
              </div>}
              {/* HISTORY tab */}
              {s3Tab==="history"&&<>
                {s3Hist.length===0?<div style={{padding:30,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>이력이 아직 없습니다.</div>
                :<table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
                  <thead><tr style={{background:"var(--bg-secondary)"}}>
                    {["시간","항목","상태","종료코드","소요시간","명령"].map(h=>(<th key={h} style={{padding:"6px 8px",textAlign:"left",fontSize:14,fontWeight:700,color:"var(--text-secondary)",borderBottom:FB_GRID_LINE}}>{h}</th>))}
                  </tr></thead>
                  <tbody>
                    {s3Hist.map((h,i)=>(<tr key={i} style={{borderBottom:FB_GRID_LINE}}>
                      <td style={{padding:"5px 8px",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",whiteSpace:"nowrap"}}>{(h.timestamp||"").slice(5,19).replace("T"," ")}</td>
                      <td style={{padding:"5px 8px",fontSize:14,fontFamily:"monospace"}}>{h.id}</td>
                      <td style={{padding:"5px 8px"}}><span style={{fontSize:14,padding:"2px 6px",borderRadius:3,background:h.status==="ok"?"#22c55e22":"#ef444422",color:h.status==="ok"?FB_OK.fg:FB_BAD.fg,fontWeight:700}}>{h.status}</span></td>
                      <td style={{padding:"5px 8px",fontSize:14,fontFamily:"monospace"}}>{h.exit_code??"-"}</td>
                      <td style={{padding:"5px 8px",fontSize:14}}>{h.duration_sec!=null?h.duration_sec+"s":"-"}</td>
                      <td style={{padding:"5px 8px",fontSize:14,fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={h.cmd||h.error||""}>{h.cmd||h.error||"-"}</td>
                    </tr>))}
                  </tbody>
                </table>}
              </>}
              {/* AWS tab — v8.4.3 단위기능 페이지 철학: Admin 에서 이관됨 */}
              {s3Tab==="aws"&&<LazyAwsPanel user={user} compact={true} />}
            </div>
          </div>
          </Modal>
          {/* Detail log overlay */}
          {s3Detail&&(
            <Modal open onClose={()=>setS3Detail(null)} width={700} zIndex={100}>
            <div style={{display:"flex",flexDirection:"column",maxHeight:"70vh"}}>
              <div style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center"}}>
                <span style={{flex:1,fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{s3Detail.id} — exit={s3Detail.exit}</span>
                <span onClick={()=>setS3Detail(null)} style={{cursor:"pointer",fontSize:16,color:"var(--text-secondary)"}}>✕</span>
              </div>
              <pre style={{flex:1,overflow:"auto",margin:0,padding:12,fontSize:14,fontFamily:"monospace",color:"var(--text-primary)",background:"var(--bg-primary)",whiteSpace:"pre-wrap",wordBreak:"break-all"}}>{s3Detail.tail||"(출력 없음)"}</pre>
            </div>
            </Modal>
          )}
        </>}
      </>}
    </div>);
}
