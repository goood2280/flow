import { useState, useEffect, useCallback, useMemo, useRef, memo, useDeferredValue } from "react";
import Loading from "../../components/Loading";
import Modal from "../../components/Modal";
import { PageGearButton } from "../../components/PageGear";
import { toast } from "../../components/Toast";
import { dl, qs, sf } from "../../lib/api";
import { allowedSubTabs } from "../../lib/permissions";
import { statusPalette, chartPalette } from "../../components/UXKit";
import { copyHistoryShareLink, historyIdFromLocation } from "../../lib/historyShare";
const API="/api/filebrowser";
const PAGE_SIZE=100;
// DB(hive) 제품 첫 프리뷰는 최신 date 파티션에서만 읽으므로 500행까지 요청한다.
// SQL/컬럼 선택/정렬/집계가 있는 조회는 기존 100행(PAGE_SIZE) 계약 유지.
const DB_PREVIEW_ROWS=500;
// SPLITTABLE is a virtual pivot-cache source used by ChartBuilder. It is not a
// user-facing database in FileBrowser; the original ML_TABLE remains available
// from the Files (single-file) scope.
const fileBrowserRoots=(roots=[])=>roots.filter(root=>String(root?.name||"").toUpperCase()!=="SPLITTABLE");
const FB_OK = statusPalette.ok;
const FB_WARN = statusPalette.warn;
const FB_BAD = statusPalette.bad;
const FB_INFO = statusPalette.info;
const FB_AMBER = chartPalette.series[1];
const FB_MUTED = "#64748b";
const FB_DISABLED = "#94a3b8";
const FB_GRID_LINE = "1px solid var(--border)";
// 파일 목록 렌더용 확장자 색/아이콘 — 행 map 안에서 객체를 재할당하지 않도록 모듈 상수로 유지.
const EXT_COLOR={parquet:"var(--ok)",csv:FB_INFO.fg,json:FB_AMBER,md:FB_DISABLED,yaml:"var(--warn)",yml:"var(--warn)",dir:FB_DISABLED};
const EXT_ICON={parquet:"📊",csv:"📋",json:"🔧",md:"📄",yaml:"⚙️",yml:"⚙️",dir:"📂"};
const BASE_EDIT_FILE_EXTS = new Set(["csv","parquet"]);
const BASE_EDIT_FILE_SOURCES = new Set(["base_root","db_root"]);
const S3_STATUS_FAST_URL="/api/s3ingest/status-by-target?include_local=0";
const S3_STATUS_FULL_URL="/api/s3ingest/status-by-target?include_local=1";
const S3_STATUS_SESSION_KEY="flow.filebrowser.s3Status.fast";
const S3_STATUS_SESSION_MAX_AGE_MS=10*60*1000;
const S3_LOCAL_STATUS_KEYS=["latest_item_at","latest_item_relpath","latest_item_age_hours","latest_item_stale_6h","latest_item_scan_error"];
const sqlAutocompleteIdent=(name)=>{
  const text=String(name||"").trim();
  if(!text)return"";
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(text)?text:"`"+text.replace(/`/g,"``")+"`";
};
const sqlAutocompleteContext=(value,caret)=>{
  const source=String(value||"");
  const position=Math.max(0,Math.min(Number(caret)||0,source.length));
  const before=source.slice(0,position);
  const keywords=[...before.matchAll(/\b(SELECT|WHERE|ORDER\s+BY|GROUP\s+BY|LIMIT)\b/gi)];
  const latest=keywords[keywords.length-1];
  if(!latest)return null;
  const clause=latest[1].replace(/\s+/g," ").toUpperCase();
  if(clause!=="SELECT"&&clause!=="WHERE")return null;
  const clauseText=before.slice((latest.index||0)+latest[0].length);
  let singleQuoted=false;
  for(let index=0;index<clauseText.length;index+=1){
    if(clauseText[index]!=="'")continue;
    if(singleQuoted&&clauseText[index+1]==="'"){index+=1;continue;}
    singleQuoted=!singleQuoted;
  }
  if(singleQuoted)return null;
  let start=position;
  while(start>0&&/[A-Za-z0-9_$]/.test(source[start-1]))start-=1;
  const token=source.slice(start,position);
  if(token.length<3||!/^[A-Za-z_$]/.test(token))return null;
  let end=position;
  while(end<source.length&&/[A-Za-z0-9_$]/.test(source[end]))end+=1;
  return{clause,token,start,end};
};
const sqlInputCaretPoint=(input,position)=>{
  if(!input||typeof document==="undefined")return{left:8,top:34,width:320};
  const computed=window.getComputedStyle(input);
  const mirror=document.createElement("div");
  [
    "boxSizing","borderLeftWidth","borderRightWidth","paddingLeft","paddingRight","fontStyle",
    "fontVariant","fontWeight","fontStretch","fontSize","fontFamily","lineHeight","letterSpacing",
    "textTransform","textIndent","textDecoration","wordSpacing","tabSize","MozTabSize",
  ].forEach(property=>{mirror.style[property]=computed[property];});
  mirror.style.position="absolute";
  mirror.style.visibility="hidden";
  mirror.style.whiteSpace="pre";
  mirror.style.top="0";
  mirror.style.left="-9999px";
  mirror.textContent=String(input.value||"").slice(0,position);
  const marker=document.createElement("span");
  marker.textContent="\u200b";
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const point={left:marker.offsetLeft-input.scrollLeft,top:input.offsetHeight+3,width:input.clientWidth};
  mirror.remove();
  return point;
};
function FileBrowserSqlAutocomplete({value,onChange,onExecute,mode,root,product,file,accessScope,columns,disabled}){
  const inputRef=useRef(null);
  const[caret,setCaret]=useState(String(value||"").length);
  const[focused,setFocused]=useState(false);
  const[remoteColumns,setRemoteColumns]=useState([]);
  const[loading,setLoading]=useState(false);
  const[activeIndex,setActiveIndex]=useState(0);
  const[suspendSearch,setSuspendSearch]=useState(false);
  const[caretPoint,setCaretPoint]=useState({left:8,top:34,width:320});
  const completion=sqlAutocompleteContext(value,caret);
  const sourceReady=mode==="hive"?Boolean(root&&product):Boolean(file);
  const canSearch=Boolean(focused&&!disabled&&!suspendSearch&&sourceReady&&completion);
  const localMatches=useMemo(()=>{
    if(!canSearch)return[];
    const needle=completion.token.toLocaleLowerCase();
    return [...new Set((columns||[]).map(column=>String(column||"").trim()).filter(Boolean))]
      .filter(column=>column.toLocaleLowerCase().includes(needle))
      .sort((left,right)=>{
        const l=left.toLocaleLowerCase(),r=right.toLocaleLowerCase();
        return Number(!l.startsWith(needle))-Number(!r.startsWith(needle))||l.localeCompare(r);
      }).slice(0,80);
  },[canSearch,columns,completion?.token]);
  const suggestions=useMemo(()=>{
    const seen=new Set();
    return [...localMatches,...remoteColumns].filter(column=>{
      const key=column.toLocaleLowerCase();
      if(seen.has(key))return false;
      seen.add(key);return true;
    }).slice(0,80);
  },[localMatches,remoteColumns]);
  const listId="filebrowser-sql-column-suggestions";

  useEffect(()=>{
    if(!canSearch){setRemoteColumns([]);setLoading(false);return undefined;}
    let alive=true;
    // 검색어가 바뀌는 즉시 이전 원격 후보를 비워, 새 결과가 올 때까지 잠깐 섞여 보이지 않게 한다.
    setRemoteColumns([]);
    setLoading(true);
    const timer=setTimeout(()=>{
      const params={q:completion.token,limit:80,_ts:Date.now()};
      if(mode==="hive"){params.root=root;params.product=product;}
      else{params.file=file;if(accessScope)params.access_scope=accessScope;}
      sf(API+"/columns/search"+qs(params)).then(response=>{
        if(!alive)return;
        const needle=completion.token.toLocaleLowerCase();
        setRemoteColumns((response.columns||[]).map(column=>String(column||"").trim()).filter(Boolean)
          .sort((left,right)=>Number(!left.toLocaleLowerCase().startsWith(needle))-Number(!right.toLocaleLowerCase().startsWith(needle))||left.localeCompare(right)));
        setActiveIndex(0);
      }).catch(()=>{if(alive)setRemoteColumns([]);}).finally(()=>{if(alive)setLoading(false);});
    },180);
    return()=>{alive=false;clearTimeout(timer);};
  },[canSearch,mode,root,product,file,accessScope,completion?.token,completion?.clause]);

  useEffect(()=>{setActiveIndex(index=>Math.min(index,Math.max(0,suggestions.length-1)));},[suggestions.length]);
  const syncCaret=target=>{
    const position=Number(target?.selectionStart)||0;
    setCaret(position);
    setCaretPoint(sqlInputCaretPoint(target,position));
  };
  const applySuggestion=column=>{
    if(!completion||!column)return;
    const inserted=sqlAutocompleteIdent(column);
    const next=String(value||"").slice(0,completion.start)+inserted+String(value||"").slice(completion.end);
    const nextCaret=completion.start+inserted.length;
    onChange(next);
    setRemoteColumns([]);
    setSuspendSearch(true);
    setCaret(nextCaret);
    requestAnimationFrame(()=>{
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(nextCaret,nextCaret);
      if(inputRef.current)syncCaret(inputRef.current);
    });
  };
  // 결과가 0건이어도 패널을 유지해 "잠깐 나타났다 사라지는" 느낌을 없앤다.
  const showPanel=canSearch;
  const popupLeft=Math.max(4,Math.min(caretPoint.left,Math.max(4,caretPoint.width-250)));
  const popupWidth=Math.max(230,Math.min(420,caretPoint.width-popupLeft-4));
  return <div style={{position:"relative",flex:1,minWidth:0}}>
    <input
      ref={inputRef}
      aria-label="파일탐색기 SQL"
      aria-autocomplete="list"
      aria-controls={showPanel?listId:undefined}
      aria-expanded={showPanel}
      aria-activedescendant={showPanel&&suggestions.length?`${listId}-${activeIndex}`:undefined}
      value={value}
      onChange={event=>{setSuspendSearch(false);onChange(event.target.value);syncCaret(event.target);}}
      onFocus={event=>{setFocused(true);syncCaret(event.target);}}
      onBlur={()=>setFocused(false)}
      onClick={event=>syncCaret(event.target)}
      onSelect={event=>syncCaret(event.target)}
      onScroll={event=>syncCaret(event.target)}
      onKeyUp={event=>{if(!["Tab","ArrowDown","ArrowUp","Escape"].includes(event.key))syncCaret(event.target);}}
      onKeyDown={event=>{
        if(canSearch&&suggestions.length&&event.key==="Tab"){
          event.preventDefault();applySuggestion(suggestions[activeIndex]||suggestions[0]);return;
        }
        if(canSearch&&suggestions.length&&event.key==="ArrowDown"){
          event.preventDefault();setActiveIndex(index=>(index+1)%suggestions.length);return;
        }
        if(canSearch&&suggestions.length&&event.key==="ArrowUp"){
          event.preventDefault();setActiveIndex(index=>(index-1+suggestions.length)%suggestions.length);return;
        }
        if(showPanel&&event.key==="Escape"){
          event.preventDefault();setSuspendSearch(true);setRemoteColumns([]);return;
        }
        if(event.key==="Enter"){
          if(event.nativeEvent?.isComposing||event.keyCode===229)return;
          onExecute();
        }
      }}
      placeholder="SQL 고유키 또는 SQL식을 입력하세요 · 예: root_lot_id = 'A1000'"
      disabled={disabled}
      spellCheck={false}
      style={{width:"100%",boxSizing:"border-box",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace",outline:"none"}}
    />
    {showPanel&&<div id={listId} role="listbox" aria-label="파일탐색기 SQL 열 자동완성" style={{position:"absolute",zIndex:50,left:popupLeft,top:caretPoint.top,width:popupWidth,border:"1px solid var(--accent)",borderRadius:6,background:"var(--bg-primary)",boxShadow:"0 10px 26px rgba(15,23,42,.24)",maxHeight:180,overflow:"auto"}}>
      <div style={{position:"sticky",top:0,zIndex:1,display:"flex",alignItems:"center",gap:6,padding:"6px 9px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)"}}>
        <b style={{color:"var(--accent)"}}>{completion?.clause}</b>
        <span>열 검색 · Tab 자동완성</span>
        {loading&&<span style={{marginLeft:"auto"}}>조회 중…</span>}
      </div>
      {suggestions.map((column,index)=><button key={column} id={`${listId}-${index}`} type="button" role="option" aria-selected={index===activeIndex}
        onMouseEnter={()=>setActiveIndex(index)} onMouseDown={event=>{event.preventDefault();applySuggestion(column);}}
        style={{display:"block",width:"100%",padding:"7px 9px",border:0,borderBottom:index<suggestions.length-1?"1px solid var(--border)":0,background:index===activeIndex?"var(--accent-glow)":"transparent",color:"var(--text-primary)",textAlign:"left",fontFamily:"monospace",fontSize:12,cursor:"pointer"}}>{column}</button>)}
      {!suggestions.length&&<div style={{padding:"9px",fontSize:12,color:"var(--text-secondary)"}}>{loading?"일치하는 열 조회 중…":"일치하는 열이 없습니다."}</div>}
    </div>}
  </div>;
}
const readStoredS3Status=()=>{
  if(typeof window==="undefined"||!window.sessionStorage)return{};
  try{
    const raw=window.sessionStorage.getItem(S3_STATUS_SESSION_KEY);
    if(!raw)return{};
    const parsed=JSON.parse(raw);
    if(!parsed||typeof parsed!=="object")return{};
    if(parsed.ts&&Date.now()-Number(parsed.ts)>S3_STATUS_SESSION_MAX_AGE_MS)return{};
    const byTarget=parsed.by_target||parsed.byTarget||{};
    return byTarget&&typeof byTarget==="object"?byTarget:{};
  }catch{return{};}
};
const storeFastS3Status=(byTarget)=>{
  if(typeof window==="undefined"||!window.sessionStorage)return;
  try{window.sessionStorage.setItem(S3_STATUS_SESSION_KEY,JSON.stringify({ts:Date.now(),by_target:byTarget||{}}));}
  catch{}
};
const mergeS3StatusByTarget=(prev={},incoming={},localIncluded=false)=>{
  const next={};
  Object.entries(incoming||{}).forEach(([target,info])=>{
    const merged={...(info||{})};
    if(!localIncluded&&prev?.[target]){
      S3_LOCAL_STATUS_KEYS.forEach(key=>{
        if(!(key in merged)&&prev[target][key]!==undefined)merged[key]=prev[target][key];
      });
    }
    next[target]=merged;
  });
  return next;
};
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
const normalizeColumnNames=(cols)=>{
  const used=new Set();
  return (cols||[]).map((value,index)=>{
    const raw=String(value||"").trim();
    const fallback=`new_col_${index+1}`;
    const base=raw||fallback;
    let name=base;
    let suffix=2;
    while(used.has(name.toLowerCase())){
      name=`${base}_${suffix}`;
      suffix+=1;
    }
    used.add(name.toLowerCase());
    return name;
  });
};
const nextGeneratedColumnName=(cols)=>{
  const used=new Set((cols||[]).map(c=>String(c||"").trim().toLowerCase()).filter(Boolean));
  let idx=1;
  while(used.has(`new_col_${idx}`))idx+=1;
  return `new_col_${idx}`;
};
const extendColumns=(cols,width)=>{
  const next=[...(cols||[])];
  while(next.length<width)next.push(nextGeneratedColumnName(next));
  return normalizeColumnNames(next);
};
const extendRow=(row,width,fill="")=>{
  const next=(row||[]).map(v=>v==null?"":String(v));
  while(next.length<width)next.push(fill);
  if(next.length>width)return next.slice(0,width);
  return next;
};
const looksLikePasteHeader=(firstRow,rowsAfter,cols,startC=0)=>{
  const cells=(firstRow||[]).map(v=>String(v||"").trim());
  if(!cells.length||!cells.some(Boolean))return false;
  const lowerCells=cells.map(v=>v.toLowerCase());
  const slice=(cols||[]).slice(startC,startC+cells.length).map(v=>String(v||"").trim().toLowerCase());
  if(slice.length===cells.length&&slice.length&&lowerCells.every((v,i)=>v===slice[i]))return true;
  const existing=(cols||[]).map(v=>String(v||"").trim().toLowerCase());
  if(startC===0&&cells.length>=existing.length&&existing.length&&existing.every((v,i)=>lowerCells[i]===v))return true;
  if(startC!==0||cells.length<=(cols||[]).length||!(rowsAfter||[]).length)return false;
  if(cells.some(v=>!v))return false;
  if(new Set(lowerCells).size!==lowerCells.length)return false;
  const hasHeaderText=cells.some(v=>/[A-Za-z_\u3131-\uD79D]/.test(v)&&!/^-?\d+(?:\.\d+)?$/.test(v));
  const differsFromData=(rowsAfter||[]).some(row=>cells.some((cell,i)=>String(row?.[i]??"").trim()!==cell));
  return hasHeaderText&&differsFromData;
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
const writeClipboardText=async(text)=>{
  if(typeof navigator!=="undefined"&&navigator.clipboard?.writeText){
    try{
      await navigator.clipboard.writeText(text);
      return;
    }catch(_){}
  }
  if(typeof document==="undefined")return Promise.reject(new Error("clipboard unavailable"));
  const ta=document.createElement("textarea");
  ta.value=text;
  ta.setAttribute("readonly","");
  ta.style.position="fixed";
  ta.style.left="-9999px";
  document.body.appendChild(ta);
  ta.select();
  try{
    document.execCommand("copy");
  }finally{
    ta.remove();
  }
};
const readCellCoord=(el)=>{
  const r=Number(el?.getAttribute?.("data-row"));
  const c=Number(el?.getAttribute?.("data-col"));
  return Number.isFinite(r)&&Number.isFinite(c)?{r,c}:null;
};
// 셀/헤더 입력창에 캐럿이 있고 붙여넣는 내용이 단일 셀 텍스트(탭·개행 없음)이면
// 그리드 블록 붙여넣기로 가로채지 않는다. 가로채면 preventDefault 때문에 브라우저
// 기본 삽입이 통째로 막혀서 "복사한 문자가 붙여넣기되지 않는" 것처럼 보인다.
const isTextEntryTarget=(el)=>{
  if(!el)return false;
  if(el.isContentEditable)return true;
  const tag=String(el.tagName||"").toLowerCase();
  if(tag==="textarea")return true;
  if(tag!=="input")return false;
  return !el.readOnly&&!el.disabled;
};
const basePasteTargetFromEvent=(e)=>{
  const el=e?.target?.closest?.("[data-base-edit-cell='1']");
  return el?readCellCoord(el):null;
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
  ["ordered_by","현재 순서 검증","feature_name asc leading_number last\nrule_order asc rule_order last"],
];
const sortRuleFields=[
  ["sort","저장 정렬","feature_name asc leading_number last\nrule_order asc rule_order last"],
];
function formatSize(b){if(!b)return"-";if(b<1024)return b+" B";if(b<1048576)return(b/1024).toFixed(1)+" KB";if(b<1073741824)return(b/1048576).toFixed(1)+" MB";return(b/1073741824).toFixed(2)+" GB";}
function revStyle(rev){
  if(rev==="추가")return{bg:"#dcfce7",fg:"#166534",line:FB_OK.fg};
  if(rev==="삭제")return{bg:"#fee2e2",fg:"#991b1b",line:FB_BAD.fg};
  if(rev==="수정")return{bg:"#fef9c3",fg:"#854d0e",line:FB_AMBER};
  return{bg:"var(--bg-primary)",fg:"var(--text-primary)",line:"var(--border)"};
}
function versionChangeLabel(summary){
  const s=summary&&typeof summary==="object"?summary:{};
  const raw=String(s.label||"");
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
  if(raw==="초기 버전"||raw==="initial snapshot")return"초기 버전";
  if(s.schema_reinitialized||s.schema_changed)return raw||"열 변경";
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
    import("../../components/AwsPanel").then(m => {
      if (alive) setComp(() => m.default);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!Comp) return <div style={{fontSize:14,color:"var(--text-secondary)",padding:12}}>AWS 설정 로딩...</div>;
  return <Comp user={user} compact={compact} />;
}

// ── 데이터 그리드 스타일 ────────────────────────────────────────────────────
// 모두 정적이라 모듈 스코프에 둔다. 컴포넌트 본문에 두면 렌더마다 새 객체가 되어
// 셀 수만큼(수만 개) style prop 이 매번 "바뀐 것"으로 취급된다.
// 값 찾기 하이라이트 색. 현재 매치는 더 진하게 + 테두리.
const FIND_HIT_BG="#fde68a";
const FIND_CUR_BG="#fb923c";
const FIND_MAX_HITS=5000;   // 이보다 많으면 "5000+" 로 표시하고 목록은 잘라둔다
const baseEditWrap={overflow:"auto",maxHeight:"calc(100vh - 320px)",border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-primary)"};
const baseReadWrap={...baseEditWrap,maxHeight:"calc(100vh - 280px)"};
const baseEditTable={width:"100%",borderCollapse:"separate",borderSpacing:0,fontSize:13,fontFamily:"ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",background:"var(--bg-primary)"};
const baseEditHeaderCell={padding:"6px 10px",height:34,fontWeight:700,fontSize:13,color:"var(--text-secondary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)",
  background:"var(--bg-tertiary)",position:"sticky",top:0,zIndex:6,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",minWidth:84};
const baseEditCornerCell={...baseEditHeaderCell,textAlign:"center",width:74,minWidth:74,left:0,zIndex:7};
const baseEditHeaderInput={...baseEditHeaderCell, fontWeight:700,textAlign:"left",minWidth:160,paddingRight:24};
const baseEditHeaderReadCell={...baseEditHeaderInput,minWidth:160};
const baseEditRowCell={padding:0,borderBottom:"1px solid var(--border)",borderRight:"1px solid var(--border)",background:"var(--bg-primary)",height:34};
const baseEditIndexBody={...baseEditRowCell,position:"sticky",left:0,zIndex:5,textAlign:"center",color:"var(--text-secondary)",fontSize:12,letterSpacing:0.2};
const baseEditCellInput={width:"100%",height:"100%",padding:"0 10px",border:"none",outline:"none",background:"transparent",color:"var(--text-primary)",fontSize:13,fontFamily:"inherit",boxSizing:"border-box"};
const baseEditCellActive={boxShadow:`inset 0 0 0 2px var(--accent)`,background:"#dbeafe",zIndex:2};
const baseReadCell={...baseEditRowCell,padding:"0 10px",fontSize:13,color:"var(--text-primary)",height:34,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"};
const baseReadIndexCell={...baseEditIndexBody,padding:"0 10px",height:34,width:54,textAlign:"center",fontSize:13};
const baseReadIndexBodyCell={...baseReadIndexCell,color:FB_MUTED};
const baseReadCellFind={...baseReadCell,background:FIND_HIT_BG,color:"#1f2937"};
const baseReadCellFindCur={...baseReadCell,background:FIND_CUR_BG,color:"#1f2937",boxShadow:"inset 0 0 0 2px #c2410c"};
const baseEditRowHighlight={background:"rgba(59,130,246,0.12)"};
const baseEditColHighlight={background:"rgba(59,130,246,0.09)"};
const baseEditRowActionButton={height:22,borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-secondary)",fontSize:11,fontWeight:800,cursor:"pointer",lineHeight:1};
// 셀 배경 변형 4종 · 입력창 변형 2종을 미리 만들어 둔다 (셀마다 스프레드하지 않는다).
const TD_CELL=baseEditRowCell;
const TD_CELL_ROW={...baseEditRowCell,background:baseEditRowHighlight.background};
const TD_CELL_COL={...baseEditRowCell,background:baseEditColHighlight.background};
const TD_CELL_ACTIVE={...baseEditRowCell,background:baseEditCellActive.background};
const TD_INDEX={...baseEditIndexBody,background:"var(--bg-tertiary)",padding:"0 6px",width:74,minWidth:74};
const TD_INDEX_ACTIVE={...TD_INDEX,background:baseEditRowHighlight.background};
const CELL_INPUT=baseEditCellInput;
const CELL_INPUT_ACTIVE={...baseEditCellInput,...baseEditCellActive};
const TD_CELL_FIND={...baseEditRowCell,background:FIND_HIT_BG};
const TD_CELL_FIND_CUR={...baseEditRowCell,background:FIND_CUR_BG};
const CELL_INPUT_FIND_CUR={...baseEditCellInput,boxShadow:"inset 0 0 0 2px #c2410c"};
const ROW_INDEX_SPAN={display:"inline-flex",alignItems:"center",justifyContent:"space-between",gap:6,width:"100%"};
const ROW_DELETE_BUTTON={...baseEditRowActionButton,width:22,border:"1px solid var(--danger-line)",background:FB_BAD.bg,color:FB_BAD.fg,fontSize:12};
const SPACER_CELL={padding:0,border:"none",background:"transparent"};

// (r,c) 셀이 스크롤 컨테이너 안에 보이도록 최소한만 스크롤한다.
// sticky thead 와 sticky 행번호 열 뒤로 숨지 않게 그만큼 여백을 둔다.
// 행이 아직 안 그려져 있으면(가상화) false 를 돌려준다.
const ensureGridCellVisible=(root,r,c)=>{
  if(!root)return false;
  const rowEl=root.querySelector(`tbody tr[data-vrow="${r}"]`);
  if(!rowEl)return false;
  const cells=rowEl.children;
  const cell=cells[c+1]||cells[cells.length-1];
  if(!cell)return false;
  const rootRect=root.getBoundingClientRect();
  const cellRect=cell.getBoundingClientRect();
  const headH=root.querySelector("thead")?.getBoundingClientRect().height||0;
  const idxW=cells[0]&&cells[0]!==cell?cells[0].getBoundingClientRect().width:0;
  const topLimit=rootRect.top+headH;
  const leftLimit=rootRect.left+idxW;
  if(cellRect.top<topLimit)root.scrollTop-=(topLimit-cellRect.top);
  else if(cellRect.bottom>rootRect.bottom)root.scrollTop+=(cellRect.bottom-rootRect.bottom);
  if(cellRect.left<leftLimit)root.scrollLeft-=(leftLimit-cellRect.left);
  else if(cellRect.right>rootRect.right)root.scrollLeft+=(cellRect.right-rootRect.right);
  return true;
};
const cellMatchesFind=(text,needle,exact)=>{
  if(!needle)return false;
  const lower=text.toLowerCase();
  return exact?lower===needle:lower.includes(needle);
};

// 편집 그리드 한 행. row 배열의 참조가 유지되면(= 그 행을 안 건드렸으면) 리렌더를 건너뛴다.
// 셀 핸들러는 전부 상위에서 내려온 공용 함수라 셀마다 클로저를 새로 만들지 않는다.
// findNeedle/findExact/findCurCol 은 전부 원시값이라 memo 를 깨지 않는다.
// 매치 판정은 각 행이 자기 데이터로 직접 하므로 상위에서 행별 객체를 만들 필요가 없다.
const BaseEditRow=memo(function BaseEditRow({row,ri,colCount,activeCol,isRowActive,
  findNeedle,findExact,findCurCol,
  onCellChange,onCellFocus,onCellKeyDown,onCellPaste,onCellClick,onDeleteRow,onRowMenu}){
  const cells=[];
  for(let ci=0;ci<colCount;ci++){
    const isColActive=ci===activeCol;
    const isActiveCell=isRowActive&&isColActive;
    const raw=row?.[ci];
    const text=raw==null?"":String(raw);
    const isFindCur=ci===findCurCol;
    const isFindHit=isFindCur||cellMatchesFind(text,findNeedle,findExact);
    const tdStyle=isFindCur?TD_CELL_FIND_CUR
      :isFindHit?TD_CELL_FIND
      :isActiveCell?TD_CELL_ACTIVE:isRowActive?TD_CELL_ROW:isColActive?TD_CELL_COL:TD_CELL;
    const inputStyle=isFindCur?CELL_INPUT_FIND_CUR
      :isFindHit?CELL_INPUT
      :isActiveCell?CELL_INPUT_ACTIVE:CELL_INPUT;
    cells.push(
      <td key={ci} style={tdStyle}
        data-base-edit-cell="1" data-row={ri} data-col={ci} onClick={onCellClick}>
        <input value={text} onChange={onCellChange} onFocus={onCellFocus}
          onKeyDown={onCellKeyDown} onPaste={onCellPaste}
          data-base-edit-cell="1" data-row={ri} data-col={ci}
          style={inputStyle} title={text}/>
      </td>);
  }
  return(
    <tr data-vrow={ri} onContextMenu={onRowMenu}>
      <td style={isRowActive?TD_INDEX_ACTIVE:TD_INDEX} data-row={ri}>
        <span style={ROW_INDEX_SPAN}>
          <span>{ri+1}</span>
          <button type="button" onClick={onDeleteRow} data-row={ri} title={`${ri+1}행 삭제`} style={ROW_DELETE_BUTTON}>×</button>
        </span>
      </td>
      {cells}
    </tr>);
});

// 행 가상화. 행 높이가 고정(34px 계열)이고 스크롤 컨테이너가 하나뿐이라
// 실제 높이를 한 번 재고 보이는 구간만 렌더한 뒤 위아래를 spacer <tr> 로 채운다.
const VIRT_OVERSCAN=24;    // 보이는 구간 위아래로 더 그려두는 행 수
const VIRT_MARGIN=6;       // 남은 여유가 이보다 줄어들 때만 창을 다시 잡는다
const VIRT_MIN_ROWS=200;   // 이보다 적으면 그냥 전부 렌더한다(동작 변화 최소화)
const useVirtualRows=(containerRef,rowCount,enabled)=>{
  // 창(start~end)은 state 로 들고 있지 않고 매 렌더에 현재 스크롤 위치에서 계산한다.
  // 그래야 행이 지워지거나 파일이 바뀌어도 낡은 창이 남지 않는다.
  // state 는 "다시 그려야 한다"는 신호(tick)와, 자주 안 바뀌는 실측값(metrics)뿐이다.
  const [,bumpWindow]=useState(0);
  const [metrics,setMetrics]=useState({viewH:600,rowH:35,headH:34});
  const metricsRef=useRef(metrics);
  metricsRef.current=metrics;
  const windowRef=useRef({start:0,end:0});
  const scrollTopRef=useRef(0);
  const rowCountRef=useRef(rowCount);
  rowCountRef.current=rowCount;
  const enabledRef=useRef(enabled);
  enabledRef.current=enabled;
  const measuredRef=useRef(false);
  const rafRef=useRef(0);

  // 행 높이는 CSS 로 34px 계열이지만 border/box-sizing 에 따라 실제값이 달라진다.
  // 가정값으로 spacer 높이를 잡으면 스크롤 위치와 그려진 행이 어긋나므로 실제로 잰다.
  const measure=useCallback(()=>{
    const root=containerRef.current;
    const cur=metricsRef.current;
    if(!root)return cur;
    const head=root.querySelector("thead");
    const firstRow=root.querySelector("tbody tr[data-vrow]");
    const headH=head?head.getBoundingClientRect().height:cur.headH;
    const measured=firstRow?firstRow.getBoundingClientRect().height:0;
    const rowH=measured>0?measured:cur.rowH;
    const viewH=root.clientHeight||cur.viewH;
    if(Math.abs(headH-cur.headH)<0.5&&Math.abs(rowH-cur.rowH)<0.5&&Math.abs(viewH-cur.viewH)<0.5)return cur;
    const next={viewH,rowH,headH};
    metricsRef.current=next;
    setMetrics(next);
    return next;
  },[containerRef]);

  // 스크롤 프레임마다 setState 하면 페이지 전체가 매 프레임 리렌더된다.
  // 이미 그려둔 창이 화면을 여유(MARGIN) 있게 덮고 있으면 리렌더를 요청하지 않는다.
  const sync=useCallback((force)=>{
    const root=containerRef.current;
    if(!root||!enabledRef.current)return;
    scrollTopRef.current=root.scrollTop;
    const m=measure();
    if(force){bumpWindow(t=>t+1);return;}
    const rows=rowCountRef.current;
    const rowH=m.rowH>0?m.rowH:35;
    const top=scrollTopRef.current;
    const visStart=Math.max(0,Math.floor((top-m.headH)/rowH));
    const visEnd=Math.min(rows,Math.ceil((top+m.viewH-m.headH)/rowH));
    const cur=windowRef.current;
    if((cur.start===0||cur.start+VIRT_MARGIN<=visStart)
      &&(cur.end>=rows||cur.end-VIRT_MARGIN>=visEnd))return;
    bumpWindow(t=>t+1);
  },[containerRef,measure]);

  const onScroll=useCallback(()=>{
    if(rafRef.current)return;
    rafRef.current=requestAnimationFrame(()=>{rafRef.current=0;sync(false);});
  },[sync]);

  const scrollToRow=useCallback((r)=>{
    const root=containerRef.current;
    if(!root)return;
    const m=metricsRef.current;
    const rowH=m.rowH>0?m.rowH:35;
    const center=Math.max(0,(root.clientHeight-m.headH-rowH)/2);
    root.scrollTop=Math.max(0,m.headH+r*rowH-center);
    sync(true);
  },[containerRef,sync]);

  useEffect(()=>{
    if(!enabled){measuredRef.current=false;return undefined;}
    sync(true);
    const root=containerRef.current;
    if(!root||typeof ResizeObserver==="undefined")return undefined;
    const ro=new ResizeObserver(()=>sync(true));
    ro.observe(root);
    return()=>ro.disconnect();
  },[enabled,rowCount,sync,containerRef]);

  // 첫 렌더에는 행이 없어 실제 행 높이를 잴 수 없다. 행이 그려진 뒤 한 번 다시 잰다.
  useEffect(()=>{
    if(!enabled||measuredRef.current)return;
    const root=containerRef.current;
    if(!root||!root.querySelector("tbody tr[data-vrow]"))return;
    measuredRef.current=true;
    sync(true);
  });

  useEffect(()=>()=>{if(rafRef.current)cancelAnimationFrame(rafRef.current);},[]);

  if(!enabled){
    windowRef.current={start:0,end:rowCount};
    return{start:0,end:rowCount,padTop:0,padBottom:0,onScroll,scrollToRow};
  }
  const rowH=metrics.rowH>0?metrics.rowH:35;
  const top=scrollTopRef.current;
  const visStart=Math.max(0,Math.floor((top-metrics.headH)/rowH));
  const visEnd=Math.min(rowCount,Math.ceil((top+metrics.viewH-metrics.headH)/rowH));
  const start=Math.max(0,Math.min(visStart-VIRT_OVERSCAN,Math.max(0,rowCount-1)));
  const end=Math.min(rowCount,Math.max(visEnd+VIRT_OVERSCAN,start+1));
  windowRef.current={start,end};
  return{start,end,padTop:start*rowH,padBottom:Math.max(0,(rowCount-end)*rowH),onScroll,scrollToRow};
};

export default function My_FileBrowser({
  user,
  embeddedBaseFiles=null,
  embeddedTitle="파일탐색기",
  embeddedCanEdit=false,
  onBaseFileChanged=null,
}){
  const embedded=Array.isArray(embeddedBaseFiles);
  const accessScope=embedded?"teg_reference":"";
  const withAccess=(params={})=>accessScope?{...params,access_scope:accessScope}:params;
  const[roots,setRoots]=useState([]);const[rootPqs,setRootPqs]=useState([]);const[selRoot,setSelRoot]=useState("");
  const[products,setProducts]=useState([]);const[selProd,setSelProd]=useState("");const[sideLoading,setSideLoading]=useState(true);const[productsLoading,setProductsLoading]=useState(false);
  const[data,setData]=useState(null);const[sql,setSql]=useState("");const[sortSpec,setSortSpec]=useState(null);const[aggregateSpec,setAggregateSpec]=useState(null);const[loading,setLoading]=useState(false);
  const[showAggregateBuilder,setShowAggregateBuilder]=useState(false);const[aggregateFunction,setAggregateFunction]=useState("latest");const[aggregateColumn,setAggregateColumn]=useState("tkout_time");const[aggregateGroupByText,setAggregateGroupByText]=useState("root_lot_id, wafer_id");
  const[sampleLoading,setSampleLoading]=useState(false);const viewSeqRef=useRef(0);const viewAbortRef=useRef(null);
  const viewSessionRef=useRef(globalThis.crypto?.randomUUID?.()||(`fb-${Date.now()}-${Math.random()}`));
  const activeViewQueryRef=useRef("");
  const[tab,setTab]=useState("data");const[colSearch,setColSearch]=useState("");const[showGuide,setShowGuide]=useState(false);const[showSqlHistory,setShowSqlHistory]=useState(false);const[sqlHistory,setSqlHistory]=useState([]);const[sqlHistoryLoading,setSqlHistoryLoading]=useState(false);const[sqlHistoryError,setSqlHistoryError]=useState("");const[mode,setMode]=useState(embedded?"base":"hive");
  const[selRootPq,setSelRootPq]=useState("");
  // v4.1: scope switcher — "DB" (hive-flat) or "Base" (single-file rulebook/wide parquet).
  // `scopes` keyed array from /api/filebrowser/scopes; `scope` = active key.
  const[scopes,setScopes]=useState(embedded?[{key:"Base",label:"기준파일",exists:true}]:[]);const[scope,setScope]=useState(embedded?"Base":"DB");
  const[baseFiles,setBaseFiles]=useState(embedded?(embeddedBaseFiles||[]):[]);const[selBaseFile,setSelBaseFile]=useState("");
  const[baseDir,setBaseDir]=useState("");
  const[baseDirLoading,setBaseDirLoading]=useState(false);
  const[baseDirTruncated,setBaseDirTruncated]=useState(false);
  // v4.1: raw preview for json/md so the main pane can render them natively
  // (pretty-printed JSON / markdown-as-pre) instead of stuffing text into the table.
  const[baseRaw,setBaseRaw]=useState(null);
  const[selBaseMeta,setSelBaseMeta]=useState(null);
  // Column selection state
  const[selectedCols,setSelectedCols]=useState([]);
  const[error,setError]=useState("");
  const[isBaseEditing,setIsBaseEditing]=useState(false);
  // 값 찾기 (Ctrl+F). 로드된 데이터 전체를 훑어 셀 단위로 매치를 모은다.
  const[findOpen,setFindOpen]=useState(false);
  const[findQuery,setFindQuery]=useState("");
  const[findExact,setFindExact]=useState(false);
  const[findIdx,setFindIdx]=useState(0);
  const[editRows,setEditRows]=useState([]);
  const[editCols,setEditCols]=useState([]);
  const[editOriginRows,setEditOriginRows]=useState([]);
  const[editOriginCols,setEditOriginCols]=useState([]);
  const[pasteMode,setPasteMode]=useState("replace");
  const[saveDelimiter,setSaveDelimiter]=useState("tab");
  const[includeHeader,setIncludeHeader]=useState(true);
  const[selectedEditCell,setSelectedEditCell]=useState({r:0,c:0});
  const[baseEditContextMenu,setBaseEditContextMenu]=useState(null);
  const baseEditCopiedRowsRef=useRef(null);
  const baseEditGridRef=useRef(null);
  const baseReadGridRef=useRef(null);
  const editColCountRef=useRef(0);
  const editRowCountRef=useRef(0);
  const selectedEditCellRef=useRef({r:0,c:0});
  const pendingCellFocusRef=useRef(null);
  const editVirtRef=useRef(null);
  const readVirtRef=useRef(null);
  const pendingFindScrollRef=useRef(null);
  const restoringSqlHistoryRef=useRef(false);
  const findInputRef=useRef(null);
  const findHitsRef=useRef(0);
  // 행 단위 액션은 매 렌더 새로 만들어지는 함수라, memo 된 행에 그대로 내리면 memo 가 깨진다.
  // 최신 함수는 ref 로 들고 다니고 행에는 안정된 래퍼만 내린다.
  const deleteBaseEditRowRef=useRef(()=>{});
  const openBaseEditRowMenuRef=useRef(()=>{});
  const[baseVersions,setBaseVersions]=useState([]);
  const[baseVersionCap,setBaseVersionCap]=useState(20);
  const[baseVersioned,setBaseVersioned]=useState(false);
  const[baseVersionLoading,setBaseVersionLoading]=useState(false);
  const[baseVersionMsg,setBaseVersionMsg]=useState("");
  const[baseVersionPreview,setBaseVersionPreview]=useState(null);
  const[baseVersionPreviewLoading,setBaseVersionPreviewLoading]=useState(false);
  const[baseVersionFilter,setBaseVersionFilter]=useState("");
  const[baseCurrentProfile,setBaseCurrentProfile]=useState(null);
  const[baseSaveBusy,setBaseSaveBusy]=useState("");
  const[rawEditing,setRawEditing]=useState(false);
  const[rawEditText,setRawEditText]=useState("");
  // S3 sync status map (public endpoint) — powers sidebar traffic-light dots
  const[s3Status,setS3Status]=useState(()=>readStoredS3Status());
  const[s3StatusReady,setS3StatusReady]=useState(false);
  const[s3StatusLoadError,setS3StatusLoadError]=useState(false);
  useEffect(()=>{
    if(embedded)return undefined;
    let alive=true;
    let idleId=null;
    let idleTimeout=null;
    const applyStatus=(d,storeFast=false)=>{
      if(!alive||!d||!d.by_target)return;
      const localIncluded=d.local_freshness_included!==false;
      setS3Status(prev=>mergeS3StatusByTarget(prev,d.by_target,localIncluded));
      setS3StatusReady(true);
      setS3StatusLoadError(false);
      if(storeFast)storeFastS3Status(d.by_target);
    };
    const loadFast=()=>sf(S3_STATUS_FAST_URL).then(d=>applyStatus(d,true)).catch(()=>{if(alive)setS3StatusLoadError(true);});
    const loadFull=()=>sf(S3_STATUS_FULL_URL).then(d=>applyStatus(d,false)).catch(()=>{});
    const scheduleFull=()=>{
      if(!alive)return;
      const run=()=>{idleId=null;idleTimeout=null;loadFull();};
      if(typeof window!=="undefined"&&typeof window.requestIdleCallback==="function")idleId=window.requestIdleCallback(run,{timeout:2500});
      else idleTimeout=setTimeout(run,1200);
    };
    loadFast().then(scheduleFull,scheduleFull);
    const fastTimer=setInterval(loadFast,30000);
    const fullTimer=setInterval(loadFull,5*60*1000);
    return()=>{
      alive=false;
      clearInterval(fastTimer);
      clearInterval(fullTimer);
      if(idleTimeout!=null)clearTimeout(idleTimeout);
      if(idleId!=null&&typeof window!=="undefined"&&typeof window.cancelIdleCallback==="function")window.cancelIdleCallback(idleId);
    };
  },[embedded]);
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
    const latestItemStale=freshnessState==="stale_item"||(latestItemStaleRaw&&!syncFresh);
    if(!info&&!s3StatusReady)return{color:FB_DISABLED,tip:"S3 상태 확인 중...",directionLabel:"확인중",directionArrow:"·",freshLabel:"-",latestItemStale:false};
    if(!info&&s3StatusLoadError)return{color:FB_DISABLED,tip:"S3 상태 확인 실패 — 다음 polling에서 다시 확인합니다",directionLabel:"확인실패",directionArrow:"·",freshLabel:"-",latestItemStale:false};
    if(!info)return{color:FB_BAD.fg,tip:"S3 동기화 미설정 — FileBrowser 우하단 ⚙️에서 상태와 실행 권한을 확인하세요",directionLabel:"미설정",directionArrow:"·",freshLabel:"-",latestItemStale:false};
    if(info.is_queued)return{color:FB_AMBER,tip:(inh?`상위 경로 '${fromLabel}' 에서 상속\n`:"")+`S3 ${directionLabel} 대기 중\n이전 실행: ${lastStr}\n최신 항목: ${latestItemStr}`,directionLabel,directionArrow,freshLabel:latestItemStale?"6h+":latestItemStr,latestItemStale};
    if(info.is_running)return{color:FB_INFO.fg,tip:(inh?`상위 경로 '${fromLabel}' 에서 상속\n`:"")+`S3 ${directionLabel} 실행 중…\n이전 실행: ${lastStr}\n최신 항목: ${latestItemStr}`,directionLabel,directionArrow,freshLabel:latestItemStr,latestItemStale:false};
    let color,line;
    if(st==="error"){color=FB_BAD.fg;line="실패 (exit="+(info.last_exit_code??"?")+")";}
    else if(st==="ok"&&isFinite(ageH)&&ageH<=6){color=FB_OK.fg;line="정상 (최근 "+ageH.toFixed(1)+"시간)";}
    else if(st==="ok"){color=chartPalette.series[5];line="오래됨 ("+(isFinite(ageH)?Math.floor(ageH)+"시간 경과":"기록 없음")+")";}
    else{color=FB_BAD.fg;line="실행 기록 없음";}
    const prefix=inh?`(상위 '${fromLabel}' 상속) `:"";
    const latestItemLine=`최신 항목: ${latestItemStr}${latestItemPath?` (${latestItemPath})`:""}${latestItemAge!=null?` / ${latestItemAge.toFixed(1)}h 전`:""}`;
    return{color,inherited:!!inh,directionLabel,directionArrow,latestItemStale,freshLabel:latestItemStale?"6h+":latestItemStr,tip:prefix+`S3 ${directionLabel} — `+line+"\n마지막 실행: "+lastStr+"\n"+latestItemLine+"\n다음: "+nextStr+(info.interval_min>0?" ("+info.interval_min+"분 주기)":"")};
  };
  // 상속 상태일 때는 내부에 점(·) 을 표시해 구분.
  // 색 = 동기화 상태(신호등), 화살표 = 방향(↑업로드 / ↓다운로드 / ↕혼합).
  // 두 정보를 서로 다른 채널로 나눠서 색각 이상이나 흑백 출력에서도 방향이 남는다.
  // directionArrow/directionLabel 은 s3Light 가 예전부터 계산해 두고도 화면에
  // 내보내지 않던 값이다 — 툴팁에만 있던 걸 여기서 눈에 보이게 한다.
  const lightDot=(name)=>{const l=s3Light(name);return(
    <span title={l.tip} style={{display:"inline-flex",alignItems:"center",gap:3,flexShrink:0,marginTop:3,marginRight:6}}>
      <span style={{display:"inline-block",width:14,height:14,borderRadius:"50%",background:l.color,flexShrink:0,boxShadow:"0 0 5px "+l.color,border:l.latestItemStale?"2px solid var(--danger)":(l.inherited?"1px dashed rgba(255,255,255,0.6)":"1px solid rgba(0,0,0,0.1)")}}>
      </span>
      <span aria-label={"S3 "+l.directionLabel} style={{fontFamily:"var(--font-mono)",fontSize:11,fontWeight:700,lineHeight:1,color:l.color,flexShrink:0,width:7,textAlign:"center"}}>
        {l.directionArrow}
      </span>
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
  const isFileBrowserAdmin=embeddedCanEdit||isAdmin
    || (Array.isArray(pageAdmins)&&pageAdmins.includes("filebrowser"))
    || (!!pageAdmins?.filebrowser===true)
    || (typeof pageAdmins==="string"&&pageAdmins.split(",").map(s=>s.trim()).includes("filebrowser"));
  const canRunS3Ingest=isFileBrowserAdmin;
  const canManageS3Ingest=isAdmin;
  const s3AllowedTabs=[
    ...(canRunS3Ingest?["items","history"]:[]),
    ...(canManageS3Ingest?["add","aws"]:[]),
    "folder","file",
  ];
  const s3AllowedTabKey=s3AllowedTabs.join("|");
  const loadBaseVersions=useCallback((file=selBaseFile)=>{
    if(!file){
      setBaseVersions([]);setBaseVersioned(false);setBaseVersionMsg("");
      setBaseCurrentProfile(null);
      return Promise.resolve(null);
    }
    setBaseVersionLoading(true);
    return sf(API+"/base-file/versions?file="+encodeURIComponent(file)+(accessScope?"&access_scope="+encodeURIComponent(accessScope):""))
      .then(d=>{
        setBaseVersions(d.versions||[]);
        setBaseVersionCap(d.cap||20);
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
  },[selBaseFile,accessScope]);
  useEffect(()=>{
    if(mode==="base"&&selBaseFile)loadBaseVersions(selBaseFile);
    else{setBaseVersions([]);setBaseVersioned(false);setBaseCurrentProfile(null);setBaseVersionMsg("");setBaseVersionPreview(null);setRawEditing(false);setRawEditText("");}
  },[mode,selBaseFile,loadBaseVersions]);
  const previewBaseVersion=async(version)=>{
    if(!selBaseFile||!version)return;
    setBaseVersionPreviewLoading(true);
    setBaseVersionMsg("");
    try{
      const d=await sf(API+"/base-file/version-content?file="+encodeURIComponent(selBaseFile)+"&version="+encodeURIComponent(version)+(accessScope?"&access_scope="+encodeURIComponent(accessScope):""));
      setBaseVersionPreview(d);
    }catch(e){setBaseVersionMsg(e.message||"버전 미리보기 실패");}
    finally{setBaseVersionPreviewLoading(false);}
  };
  const rollbackBaseVersion=async(version)=>{
    if(!selBaseFile||!version)return;
    if(!isFileBrowserAdmin){toast.warn("Admin 또는 FileBrowser page_admin 만 롤백할 수 있습니다.");return;}
    if(!window.confirm(`${selBaseFile}\n${version} 버전으로 롤백하시겠습니까?\n현재 파일은 pre-rollback 버전으로 먼저 보존됩니다.`))return;
    const note=window.prompt("롤백 사유를 입력하세요.", `Rollback to ${version}`);
    if(note===null)return;
    try{
      const d=await sf(API+"/base-file/rollback",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({file:selBaseFile,version,username:user?.username||"",note,access_scope:accessScope})});
      setBaseVersionMsg(`롤백 완료: ${d.rolled_back_to||version}`);
      setIsBaseEditing(false);
      setData(null);setBaseRaw(null);
      setBaseVersionPreview(null);
      loadBaseVersions(selBaseFile);
      loadBaseFileView(selBaseFile,{});
      if(onBaseFileChanged)onBaseFileChanged({file:selBaseFile,action:"rollback"});
    }catch(e){setBaseVersionMsg(e.message||"롤백 실패");}
  };
  const canEditRawBase=mode==="base"&&!!baseRaw&&baseVersioned&&isFileBrowserAdmin&&["yaml","json","md","txt"].includes(String(baseRaw.kind||"").toLowerCase());
  const saveRawBaseFile=async()=>{
    if(!canEditRawBase||!selBaseFile)return;
    if(baseSaveBusy)return;
    const note=window.prompt("변경 사유를 입력하세요.", "Raw EDM edit");
    if(note===null)return;
    setBaseSaveBusy("raw");
    setBaseVersionMsg("저장 중...");
    try{
      const d=await sf(API+"/base-file/text-save",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({file:selBaseFile,text:rawEditText,username:user?.username||"",note,access_scope:accessScope})});
      setBaseVersionMsg(`저장 완료${d.version?.version?`: ${d.version.version}`:""}`);
      setRawEditing(false);
      setBaseRaw(null);
      loadBaseVersions(selBaseFile);
      loadBaseFileView(selBaseFile,{});
      if(onBaseFileChanged)onBaseFileChanged({file:selBaseFile,action:"save"});
    }catch(e){setBaseVersionMsg(e.message||"저장 실패");}
    finally{setBaseSaveBusy("");}
  };
  const[s3Open,setS3Open]=useState(false);
  const[s3Items,setS3Items]=useState([]);
  const[s3AutoSync,setS3AutoSync]=useState(null);
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
    if(nextOpen&&!s3AllowedTabs.includes(s3Tab))setS3Tab(s3AllowedTabs[0]||"folder");
    setS3Open(nextOpen);
  };
  const[fbSettings,setFbSettings]=useState({csv_full_read_max_bytes:10485760,csv_download_max_rows:500000,csv_download_max_bytes:100000000,sql_query_max_source_bytes:5368709120,preview_max_columns:100,preview_max_rows:100,schema_column_page_size:200,csv_rules:{},file_descriptions:{},hidden_db_dirs:["reformatter"],db_name_aliases:{},versioned_single_file_dirs:["reformatter"],auto_s3_upload_on_save:false,can_manage:false});
  const[fbAutoS3Upload,setFbAutoS3Upload]=useState(false);
  const[fbThresholdMb,setFbThresholdMb]=useState("10");
  const[fbDownloadMb,setFbDownloadMb]=useState("100");
  const[fbDownloadRows,setFbDownloadRows]=useState("500000");
  const[fbHiddenDbDirsText,setFbHiddenDbDirsText]=useState("reformatter");
  const[fbDbNameAliases,setFbDbNameAliases]=useState({});
  const[fbVersionedDirsText,setFbVersionedDirsText]=useState("reformatter");
  const[fbSettingsMsg,setFbSettingsMsg]=useState("");
  const[fbSettingsLoading,setFbSettingsLoading]=useState(false);
  const[fbSelectedFile,setFbSelectedFile]=useState("");
  const[fbDescriptionFile,setFbDescriptionFile]=useState("");
  const[fbDescriptionText,setFbDescriptionText]=useState("");
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

  const settingsBaseFiles=(baseFiles||[]).filter(f=>(f?.kind||"file").toLowerCase()!=="dir");
  const csvBaseFiles=settingsBaseFiles.filter(f=>(f?.ext||"").toLowerCase()==="csv");
  const selectDescriptionFile=(file,settings=fbSettings)=>{
    const key=String(file||"");
    setFbDescriptionFile(key);
    setFbDescriptionText(String((settings?.file_descriptions||{})[key]||""));
  };
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
      let localBaseFiles=settingsBaseFiles;
      let localCsvFiles=csvBaseFiles;
      if(!localBaseFiles.length){
        const bf=await sf(API+"/base-files?fast=1&_ts="+Date.now()).catch(()=>({files:[]}));
        const files=bf.files||[];
        setBaseFiles(files);
        localBaseFiles=files.filter(f=>(f?.kind||"file").toLowerCase()!=="dir");
        localCsvFiles=files.filter(f=>(f?.kind||"file").toLowerCase()!=="dir"&&(f?.ext||"").toLowerCase()==="csv");
      }
      const d=await sf(API+"/settings");
      const settings={csv_full_read_max_bytes:d.csv_full_read_max_bytes??10485760,csv_download_max_rows:d.csv_download_max_rows??500000,csv_download_max_bytes:d.csv_download_max_bytes??100000000,sql_query_max_source_bytes:d.sql_query_max_source_bytes??5368709120,preview_max_columns:d.preview_max_columns??100,preview_max_rows:d.preview_max_rows??100,schema_column_page_size:d.schema_column_page_size??200,csv_rules:d.csv_rules||{},file_descriptions:d.file_descriptions||{},hidden_db_dirs:d.hidden_db_dirs||["reformatter"],db_name_aliases:d.db_name_aliases||{},versioned_single_file_dirs:d.versioned_single_file_dirs||["reformatter"],auto_s3_upload_on_save:!!d.auto_s3_upload_on_save,can_manage:!!d.can_manage,max_csv_full_read_max_bytes:d.max_csv_full_read_max_bytes,max_csv_download_max_rows:d.max_csv_download_max_rows,max_csv_download_max_bytes:d.max_csv_download_max_bytes,max_sql_query_max_source_bytes:d.max_sql_query_max_source_bytes,max_preview_max_columns:d.max_preview_max_columns,max_schema_column_page_size:d.max_schema_column_page_size};
      setFbSettings(settings);
      setFbAutoS3Upload(!!settings.auto_s3_upload_on_save);
      setFbThresholdMb(String(((Number(settings.csv_full_read_max_bytes)||0)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadMb(String(((Number(settings.csv_download_max_bytes)||100000000)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadRows(String(Number(settings.csv_download_max_rows)||500000));
      setFbHiddenDbDirsText((settings.hidden_db_dirs||[]).join("\n"));
      setFbDbNameAliases(settings.db_name_aliases||{});
      setFbVersionedDirsText((settings.versioned_single_file_dirs||[]).join("\n"));
      const currentCsv=(selBaseMeta&&(selBaseMeta.ext||"").toLowerCase()==="csv")?String(selBaseMeta.path||selBaseMeta.name||""):"";
      const target=fbSelectedFile||currentCsv||localCsvFiles[0]?.path||localCsvFiles[0]?.name||"";
      if(target)selectFileRule(target,settings);
      const currentFile=selBaseMeta?String(selBaseMeta.path||selBaseMeta.name||""):"";
      const descriptionTarget=fbDescriptionFile||currentFile||localBaseFiles[0]?.path||localBaseFiles[0]?.name||"";
      if(descriptionTarget)selectDescriptionFile(descriptionTarget,settings);
      setFbSettingsMsg("");
    }catch(e){
      setFbSettingsMsg(e.message||"설정 로드 실패");
    }finally{
      setFbSettingsLoading(false);
    }
  };
  const saveFilebrowserSettings=async(section="file")=>{
    const isFileSection=section==="file";
    const isDescriptionSection=section==="description";
    const nextRules={...(fbSettings.csv_rules||{})};
    if(isFileSection&&fbSelectedFile){
      const rule=formToRule(fbRuleForm);
      if(Object.keys(rule).length)nextRules[fbSelectedFile]=rule;
      else delete nextRules[fbSelectedFile];
    }
    const nextDescriptions={...(fbSettings.file_descriptions||{})};
    if(isDescriptionSection&&fbDescriptionFile){
      const description=String(fbDescriptionText||"").trim();
      if(description)nextDescriptions[fbDescriptionFile]=description;
      else delete nextDescriptions[fbDescriptionFile];
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
      const d=await sf(API+"/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({csv_full_read_max_bytes:thresholdBytes,csv_download_max_rows:downloadRows,csv_download_max_bytes:downloadBytes,sql_query_max_source_bytes:fbSettings.sql_query_max_source_bytes,preview_max_columns:fbSettings.preview_max_columns,preview_max_rows:fbSettings.preview_max_rows,schema_column_page_size:fbSettings.schema_column_page_size,csv_rules:nextRules,file_descriptions:nextDescriptions,hidden_db_dirs:hiddenDbDirs,db_name_aliases:fbDbNameAliases,versioned_single_file_dirs:versionedSingleFileDirs,auto_s3_upload_on_save:!!fbAutoS3Upload})});
      const settings={csv_full_read_max_bytes:d.csv_full_read_max_bytes??thresholdBytes,csv_download_max_rows:d.csv_download_max_rows??downloadRows,csv_download_max_bytes:d.csv_download_max_bytes??downloadBytes,sql_query_max_source_bytes:d.sql_query_max_source_bytes??fbSettings.sql_query_max_source_bytes,preview_max_columns:d.preview_max_columns??fbSettings.preview_max_columns,preview_max_rows:d.preview_max_rows??fbSettings.preview_max_rows,schema_column_page_size:d.schema_column_page_size??fbSettings.schema_column_page_size,csv_rules:d.csv_rules||{},file_descriptions:d.file_descriptions||nextDescriptions,hidden_db_dirs:d.hidden_db_dirs||hiddenDbDirs,db_name_aliases:d.db_name_aliases||fbDbNameAliases,versioned_single_file_dirs:d.versioned_single_file_dirs||versionedSingleFileDirs,auto_s3_upload_on_save:!!d.auto_s3_upload_on_save,can_manage:!!d.can_manage,max_csv_full_read_max_bytes:d.max_csv_full_read_max_bytes,max_csv_download_max_rows:d.max_csv_download_max_rows,max_csv_download_max_bytes:d.max_csv_download_max_bytes,max_sql_query_max_source_bytes:d.max_sql_query_max_source_bytes,max_preview_max_columns:d.max_preview_max_columns,max_schema_column_page_size:d.max_schema_column_page_size};
      setFbSettings(settings);
      setFbAutoS3Upload(!!settings.auto_s3_upload_on_save);
      setFbThresholdMb(String(((Number(settings.csv_full_read_max_bytes)||0)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadMb(String(((Number(settings.csv_download_max_bytes)||downloadBytes)/1048576).toFixed(2)).replace(/\.00$/,""));
      setFbDownloadRows(String(Number(settings.csv_download_max_rows)||downloadRows));
      setFbHiddenDbDirsText((settings.hidden_db_dirs||[]).join("\n"));
      setFbDbNameAliases(settings.db_name_aliases||{});
      setFbVersionedDirsText((settings.versioned_single_file_dirs||[]).join("\n"));
      selectFileRule(fbSelectedFile,settings);
      if(fbDescriptionFile)selectDescriptionFile(fbDescriptionFile,settings);
      setFbSettingsMsg(isDescriptionSection?"파일 설명 저장 완료":(isFileSection?"파일 설정 저장 완료":"폴더 설정 저장 완료"));
      sf(API+"/roots?fast=1&_ts="+Date.now()).then(r=>setRoots(fileBrowserRoots(r.roots))).catch(()=>{});
      sf(API+"/base-files?fast=1&_ts="+Date.now()).then(r=>setBaseFiles(r.files||[])).catch(()=>{});
    }catch(e){
      setFbSettingsMsg(e.message||(isDescriptionSection?"파일 설명 저장 실패":(isFileSection?"파일 설정 저장 실패":"폴더 설정 저장 실패")));
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
    if(!s3Open||!canRunS3Ingest)return;
    const un=encodeURIComponent(user?.username||"");
    const loadItems=()=>sf("/api/s3ingest/items?username="+un).then(d=>{setS3Items(d.items||[]);setS3AwsOk(d.aws_available!==false);setS3AutoSync(d.auto_sync||null);}).catch(()=>{});
    const loadAvail=()=>sf("/api/s3ingest/available?username="+un).then(d=>setS3Avail(d||{dbs:[],root_parquets:[]})).catch(()=>{});
    const loadHist=()=>sf("/api/s3ingest/history?username="+un+"&limit=100").then(d=>setS3Hist(d.entries||[])).catch(()=>{});
    const loadProfiles=()=>sf("/api/s3ingest/aws-config?username="+un).then(d=>setS3Profiles((d&&d.profiles)||[])).catch(()=>setS3Profiles([]));
    loadItems();
    if(canManageS3Ingest&&s3Tab==="add"){loadAvail();loadProfiles();}
    if(s3Tab==="history")loadHist();
    const t=setInterval(()=>{loadItems();if(s3Tab==="history")loadHist();},5000);
    return()=>clearInterval(t);
  },[s3Open,s3Tab,s3Tick,canRunS3Ingest,canManageS3Ingest,user?.username]);

  useEffect(()=>{
    if(s3Open&&!s3AllowedTabs.includes(s3Tab))setS3Tab(s3AllowedTabs[0]||"folder");
  },[s3Open,s3Tab,s3AllowedTabKey]);

  useEffect(()=>{
    if(!s3Open||!isFileBrowserAdmin)return;
    loadFilebrowserSettings();
  },[s3Open,isFileBrowserAdmin]);

  // 1s ticker for ETA countdown (only while modal open)
  useEffect(()=>{if(!s3Open)return;const t=setInterval(()=>setS3Now(Date.now()),1000);return()=>clearInterval(t);},[s3Open]);

  useEffect(()=>{
    if(!baseEditContextMenu)return;
    const close=()=>setBaseEditContextMenu(null);
    const onKey=(e)=>{if(e.key==="Escape")close();};
    window.addEventListener("click",close);
    window.addEventListener("resize",close);
    window.addEventListener("scroll",close,true);
    window.addEventListener("keydown",onKey);
    return()=>{
      window.removeEventListener("click",close);
      window.removeEventListener("resize",close);
      window.removeEventListener("scroll",close,true);
      window.removeEventListener("keydown",onKey);
    };
  },[baseEditContextMenu]);

  const s3Save=async(form)=>{
    if(!canManageS3Ingest){toast.warn("S3 동기화 설정 변경은 Admin 전용입니다.");return;}
    if(!form.target||!form.s3_url){toast.warn("target 과 s3_url 은 필수입니다");return;}
    const body={...form,username:user?.username||""};
    try{
      await sf("/api/s3ingest/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      setS3Form(null);setS3Tab("items");setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"저장 실패");}
  };
  const s3Delete=async(id)=>{
    if(!canManageS3Ingest){toast.warn("S3 동기화 항목 삭제는 Admin 전용입니다.");return;}
    if(!window.confirm("이 S3 동기화 항목을 삭제하시겠습니까?\n("+id+")"))return;
    try{
      await sf("/api/s3ingest/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id})});
      setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"삭제 실패");}
  };
  const s3SaveAutoSync=async(next)=>{
    if(!canManageS3Ingest){toast.warn("자동 동기화 설정 변경은 Admin 전용입니다.");return;}
    try{
      const d=await sf("/api/s3ingest/auto-sync/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",...next})});
      setS3AutoSync(d.auto_sync||null);
    }catch(e){toast.error(e.message||"자동 동기화 설정 저장 실패");}
  };
  const s3Run=async(id)=>{
    if(!canRunS3Ingest){toast.warn("FileBrowser manager 권한이 필요합니다.");return;}
    try{
      await sf("/api/s3ingest/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id})});
      setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"실행 실패");}
  };
  const s3Stop=async(id)=>{
    if(!canRunS3Ingest){toast.warn("FileBrowser manager 권한이 필요합니다.");return;}
    try{
      await sf("/api/s3ingest/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id})});
      toast.info("중지 요청을 보냈습니다.");
      setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"중지 실패");}
  };
  const s3SetEnabled=async(id,enabled)=>{
    if(!canManageS3Ingest){toast.warn("S3 동기화 일시정지/재개는 Admin 전용입니다.");return;}
    try{
      await sf("/api/s3ingest/set-enabled",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id,enabled})});
      setS3Tick(x=>x+1);
    }catch(e){toast.error(e.message||"설정 실패");}
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
  const s3HistoryReason=(h)=>{
    const ai=h?.ai_explanation||{};
    return ai.summary||h?.reason||h?.error||h?.output_tail||h?.stderr_tail||h?.stdout_tail||"";
  };
  const s3HistoryAction=(h)=>{
    const a=(h?.action||"").toString();
    if(a==="save")return"등록";
    if(a==="run")return"실행";
    return h?.cmd?"실행":"등록";
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
    setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);setEditOriginCols([]);
    }catch(e){toast.error("삭제 실패: "+(e?.message||e));}
  };

  useEffect(()=>{
  if(embedded){
    setScopes([{key:"Base",label:"기준파일",exists:true}]);
    setScope("Base");
    setMode("base");
    setBaseFiles(embeddedBaseFiles||[]);
    setSideLoading(false);
    return;
  }
  // v4.1: boot-load scopes + DB listings in parallel. Base listing is lazy
  // (loaded only when user switches scope) to keep the default cold-start fast.
  const loadInitial = async () => {
    try {
      const sc = await sf(API+"/scopes").catch(()=>({scopes:[{key:"DB",label:"DB",exists:true,icon:"🗄️"}]}));
      // v9.1.x: 소탭 단위 권한 — 허용된 scope(DB→db, Files/Base→files)만 노출.
      const fbAllowed = allowedSubTabs("filebrowser");
      const SCOPE_SUBTAB = { DB: "db", Base: "files" };
      const scopesPayload = (sc.scopes || []).filter((s) => fbAllowed.includes(SCOPE_SUBTAB[s?.key] || ""));
      setScopes(scopesPayload);
      let rp = await sf(API+"/roots?fast=1");
      if (!rp.roots?.length) {
        const baseScope = scopesPayload.find((s) => s?.key === "Base") || {};
        if (baseScope?.exists) {
          rp = await sf(API+"/roots?all=1&fast=1").catch(() => rp);
        }
      }
      const visibleRoots=fileBrowserRoots(rp.roots);
      setRoots(visibleRoots);
      const rootScope = scopesPayload.find((s) => s?.key === "DB") || {};
      const baseScope = scopesPayload.find((s) => s?.key === "Base") || {};
      const nextScope = (rootScope?.exists && visibleRoots.length > 0)
        ? "DB"
        : (baseScope?.exists ? "Base" : (scopesPayload[0]?.key || "DB"));
      setScope(nextScope);
      setSideLoading(false);
      sf(API+"/root-parquets").then(d=>setRootPqs(d.files||[])).catch(()=>{});
    } catch (_) {
      setSideLoading(false);
    }
  };
  loadInitial();
  },[embedded,embeddedBaseFiles]);

  // v4.1: when user switches to Base scope, fetch /base-files (idempotent).
  useEffect(()=>{
    if(embedded)return;
    if(scope!=="Base")return;
    setSideLoading(true);
    sf(API+"/base-files?fast=1&_ts="+Date.now()).then(d=>{setBaseFiles(d.files||[]);setSideLoading(false);}).catch(()=>setSideLoading(false));
  },[scope,embedded]);

  // 폴더를 열면 그 칸의 바로 아래 항목을 따로 읽어 합친다.
  // /base-files 는 single-file 폴더 전체를 재귀로 한 번에 싣고 1000개에서 자르는데,
  // 그 스캔이 DFS 라 예산을 한 갈래에서 다 쓴다 — 운영 캐시(제품 × root 파티션 수만 개)
  // 에서는 형제 폴더가 목록엔 보이는데 열면 비어 있었다. 폴더별로 읽으면 깊이 제한 없이
  // parquet 까지 내려간다.
  const baseDirLoadedRef=useRef(new Set());
  useEffect(()=>{
    if(embedded)return;
    if(scope!=="Base"||!baseDir)return;
    if(baseDirLoadedRef.current.has(baseDir))return;
    baseDirLoadedRef.current.add(baseDir);
    setBaseDirLoading(true);
    sf(API+"/base-dir"+qs({path:baseDir,_ts:Date.now()}))
      .then(d=>{
        const items=d?.entries||[];
        setBaseDirTruncated(!!d?.truncated);
        if(!items.length)return;
        setBaseFiles(prev=>{
          const seen=new Set((prev||[]).map(f=>String(f?.path||f?.name||"").toLowerCase()));
          const add=items.filter(f=>!seen.has(String(f?.path||"").toLowerCase()));
          return add.length?[...(prev||[]),...add]:prev;
        });
      })
      .catch(()=>{ baseDirLoadedRef.current.delete(baseDir); })
      .finally(()=>setBaseDirLoading(false));
  },[scope,baseDir,embedded]);

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
    setEditOriginCols(cols.slice());
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
  const cleanAggregateSpec=(agg)=>{
    if(!agg||typeof agg!=="object")return null;
    const fn=String(agg.function||agg.func||agg.type||"").trim().toLowerCase();
    if(!["latest","avg","sum","min","max","median","count"].includes(fn))return null;
    const column=String(agg.column||"").trim();
    const groupBy=Array.isArray(agg.group_by)?agg.group_by.map(c=>String(c||"").trim()).filter(Boolean):[];
    if(fn!=="count"&&!column)return null;
    const alias=String(agg.alias||`${fn}_${column||"rows"}`).trim();
    return{function:fn,column,group_by:groupBy,alias};
  };
  const aggregateParams=(spec)=>{
    const a=cleanAggregateSpec(spec);
    return a?{agg_func:a.function,agg_column:a.column,agg_group_by:a.group_by.join(",")}:{};
  };
  const aggregateLabel=(spec)=>{
    const a=cleanAggregateSpec(spec);
    if(!a)return"";
    const body=a.function==="count"&&!a.column?"rows":a.column;
    return `${a.function}(${body})${a.group_by.length?` by ${a.group_by.join(", ")}`:""}`;
  };
  const currentColumns=()=>{
    const cols=(data?.all_columns||data?.columns||[]).map(c=>String(c||"")).filter(Boolean);
    (remoteCols||[]).forEach(c=>{const text=String(c||"").trim();if(text&&!cols.includes(text))cols.push(text);});
    return cols;
  };
  const aggregateBuilderSpec=()=>{
    const columns=currentColumns();
    const lookup=new Map(columns.map(c=>[c.toLowerCase(),c]));
    const rawGroups=String(aggregateGroupByText||"").split(",").map(c=>c.trim()).filter(Boolean);
    const unknownGroups=rawGroups.filter(c=>!lookup.has(c.toLowerCase()));
    if(unknownGroups.length){toast.error(`그룹 기준 컬럼을 찾을 수 없습니다: ${unknownGroups.join(", ")}`);return null;}
    const groupBy=[];
    rawGroups.forEach(c=>{const hit=lookup.get(c.toLowerCase());if(hit&&!groupBy.includes(hit))groupBy.push(hit);});
    const rawColumn=String(aggregateColumn||"").trim();
    const column=rawColumn?(lookup.get(rawColumn.toLowerCase())||""):"";
    if(rawColumn&&!column){toast.error(`집계 대상 컬럼을 찾을 수 없습니다: ${rawColumn}`);return null;}
    const next=cleanAggregateSpec({function:aggregateFunction,column,group_by:groupBy});
    if(!next){toast.error("집계 대상과 함수를 확인하세요. count는 대상 컬럼 없이 전체 행 개수를 집계할 수 있습니다.");return null;}
    return next;
  };
  const applyAggregateBuilder=()=>{
    const next=aggregateBuilderSpec();
    if(!next)return;
    setAggregateSpec(next);
    applySql(sql,selectedCols,sortSpec,next);
  };
  const applyLatestWaferPreset=()=>{
    const columns=currentColumns();
    const lookup=new Map(columns.map(c=>[c.toLowerCase(),c]));
    const required=["root_lot_id","wafer_id","tkout_time"];
    const missing=required.filter(c=>!lookup.has(c));
    if(missing.length){toast.error(`프리셋에 필요한 컬럼이 없습니다: ${missing.join(", ")}`);return;}
    const next=cleanAggregateSpec({function:"latest",column:lookup.get("tkout_time"),group_by:[lookup.get("root_lot_id"),lookup.get("wafer_id")]});
    setAggregateFunction("latest");setAggregateColumn(lookup.get("tkout_time"));setAggregateGroupByText(`${lookup.get("root_lot_id")}, ${lookup.get("wafer_id")}`);
    setAggregateSpec(next);applySql(sql,selectedCols,sortSpec,next);
  };
  const displaySqlIdent=(name)=>{
    const text=String(name||"").trim();
    if(!text)return"";
    if(/^[A-Za-z_][A-Za-z0-9_]*$/.test(text))return text;
    return "`"+text.replace(/`/g,"``")+"`";
  };
  const unquoteDisplaySqlIdent=(value)=>{
    const text=String(value||"").trim();
    if(text.length>=2&&text[0]==="`"&&text[text.length-1]==="`")return text.slice(1,-1).replace(/``/g,"`");
    if(text.length>=2&&text[0]==='"'&&text[text.length-1]==='"')return text.slice(1,-1).replace(/""/g,'"');
    return text;
  };
  const splitDisplaySqlIdentifiers=(value)=>{
    const text=String(value||"");
    const parts=[];let buf="";let quote="";
    for(let i=0;i<text.length;i+=1){
      const ch=text[i];
      if(quote){
        buf+=ch;
        if(ch===quote){
          if(text[i+1]===quote){buf+=text[i+1];i+=1;}
          else quote="";
        }
        continue;
      }
      if(ch==="`"||ch==='"'){quote=ch;buf+=ch;continue;}
      if(ch===","){const part=buf.trim();if(part)parts.push(part);buf="";continue;}
      buf+=ch;
    }
    if(quote)return null;
    const part=buf.trim();if(part)parts.push(part);
    return parts;
  };
  const splitDisplaySql=(value,columns=currentColumns())=>{
    const text=String(value||"").trim();
    const identPattern="(?:`(?:``|[^`])+`|\"(?:\"\"|[^\"])+\"|[A-Za-z_][A-Za-z0-9_]*)";
    const orderMatch=text.match(new RegExp("^(.*?)\\s+ORDER\\s+BY\\s+("+identPattern+")\\s+(ASC|DESC)(?:\\s+NULLS\\s+(FIRST|LAST))?\\s*$","i"));
    const body=orderMatch?String(orderMatch[1]||"").trim():text;
    const lookup=new Map(columns.map(c=>[c.toLowerCase(),c]));
    let sortSpec=null;
    if(orderMatch){
      const sortCol=unquoteDisplaySqlIdent(orderMatch[2]);
      const hit=lookup.get(sortCol.toLowerCase());
      if(hit)sortSpec={column:hit,direction:String(orderMatch[3]||"asc").toLowerCase(),nulls:String(orderMatch[4]||"last").toLowerCase()};
    }
    if(!/^select\b/i.test(body))return{whereSql:body,selectedColumns:[],sortSpec};
    const match=body.match(/^\s*SELECT\s+([\s\S]+?)(?:\s+WHERE\s+([\s\S]*))?\s*$/i);
    if(!match)return{whereSql:body,selectedColumns:[],sortSpec};
    const rawCols=String(match[1]||"").trim();
    const whereSql=String(match[2]||"").trim();
    if(!rawCols||rawCols==="*")return{whereSql,selectedColumns:[],sortSpec};
    const selected=[];
    const parts=splitDisplaySqlIdentifiers(rawCols);
    if(!parts)return{whereSql:body,selectedColumns:[],sortSpec};
    for(const part of parts){
      const token=unquoteDisplaySqlIdent(part);
      const hit=lookup.get(token.toLowerCase());
      if(!hit)return{whereSql:body,selectedColumns:[],sortSpec};
      if(!selected.includes(hit))selected.push(hit);
    }
    return{whereSql,selectedColumns:selected,sortSpec};
  };
  const buildDisplaySql=(cols,whereSql,sortOverride=null)=>{
    const selected=[];
    (cols||[]).forEach(c=>{const text=String(c||"").trim();if(text&&!selected.includes(text))selected.push(text);});
    const where=String(whereSql||"").trim();
    let base="";
    const rendered=selected.map(displaySqlIdent);
    if(selected.length&&where)base=`SELECT ${rendered.join(", ")} WHERE ${where}`;
    else if(selected.length)base=`SELECT ${rendered.join(", ")}`;
    else base=where;
    const s=cleanSortSpec(sortOverride);
    if(s)base=`${base} ORDER BY ${displaySqlIdent(s.column)} ${s.direction.toUpperCase()}${s.nulls==="first"?" NULLS FIRST":""}`.trim();
    return base;
  };
  const setSqlFromInput=(value)=>{
    const parsed=splitDisplaySql(value);
    setSql(value);
    setSelectedCols(parsed.selectedColumns);
    setSortSpec(null);
  };
  useEffect(()=>{
    if(embedded)return;
    const historyId=historyIdFromLocation(/^fb_sql_exec_[0-9a-f]{12}$/i);
    if(!historyId)return;
    let alive=true;
    sf(API+"/sql/execution-history"+qs({history_id:historyId,limit:1,_ts:Date.now()})).then(d=>{
      if(!alive)return;
      const entry=d.history?.[0];
      if(!entry)throw new Error("공유된 SQL 이력을 찾지 못했습니다.");
      restoringSqlHistoryRef.current=true;
      setTab("data");setShowGuide(false);setShowSqlHistory(true);setData(null);setError("");
      setSelectedCols([]);setSortSpec(null);setAggregateSpec(null);setSqlFromInput(entry.sql||"");
      if(entry.scope==="db_product"){
        setScope("DB");setMode("hive");setSelRoot(entry.root||"");setSelProd(entry.product||"");setSelRootPq("");setSelBaseFile("");
      }else if(entry.scope==="rootpq"){
        setScope("DB");setMode("rootpq");setSelRoot("");setSelProd("");setSelRootPq(entry.file||"");setSelBaseFile("");restoringSqlHistoryRef.current=false;
      }else{
        setScope("Base");setMode("base");setSelRoot("");setSelProd("");setSelRootPq("");setSelBaseFile(entry.file||"");restoringSqlHistoryRef.current=false;
      }
      toast.ok(`[${historyId}] 공유 SQL과 대상을 불러왔습니다. 실행 버튼을 눌러 조회하세요.`);
    }).catch(e=>{if(alive){restoringSqlHistoryRef.current=false;setError(e.message||String(e));}});
    return()=>{alive=false;};
  },[embedded]);
  const copySqlHistoryLink=async entry=>{
    try{await copyHistoryShareLink("/filebrowser",entry?.history_id);toast.ok("SQL 이력 공유 링크를 복사했습니다.");}
    catch(_error){toast.error("브라우저에서 공유 링크를 복사하지 못했습니다.");}
  };

  const cancelActiveViewRequest=()=>{
    if(viewAbortRef.current)viewAbortRef.current.abort();
    const queryId=activeViewQueryRef.current;
    if(queryId){
      sf(API+"/view/cancel",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query_session:viewSessionRef.current,query_id:queryId}),keepalive:true}).catch(()=>{});
    }
    activeViewQueryRef.current="";
  };
  const nextViewRequest=()=>{
    cancelActiveViewRequest();
    const controller=new AbortController();
    const queryId=globalThis.crypto?.randomUUID?.()||(`q-${Date.now()}-${Math.random()}`);
    viewAbortRef.current=controller;
    activeViewQueryRef.current=queryId;
    return{seq:++viewSeqRef.current,signal:controller.signal,queryId};
  };
  const isViewAbort=e=>e?.name==="AbortError";
  useEffect(()=>{
    const leave=()=>cancelActiveViewRequest();
    window.addEventListener("pagehide",leave);
    return()=>{window.removeEventListener("pagehide",leave);leave();};
  },[]);

  const loadBaseFileView=(file,{full=true,page:pageArg=0}={})=>{
    const{seq,signal,queryId}=nextViewRequest();
    setLoading(true);setTab("data");setMode("base");setSelBaseFile(file);
    setSortSpec(null);setAggregateSpec(null);
    setSelProd("");setSelRootPq("");setError("");setBaseRaw(null);
    setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);setEditOriginCols([]);
    const params=withAccess({file,rows:PAGE_SIZE,page:pageArg,page_size:PAGE_SIZE,cols:10,meta_only:!full,_ts:Date.now(),query_session:viewSessionRef.current,query_id:queryId});
    const url=API+"/base-file-view"+qs(params);
    sf(url,{signal}).then(d=>{
      if(seq!==viewSeqRef.current)return;
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
    }).catch(e=>{if(seq!==viewSeqRef.current||isViewAbort(e))return;setError(e.message);setLoading(false);});
  };

  useEffect(()=>{
    if(!selRoot){setProducts([]);setProductsLoading(false);return;}
    let alive=true;
    setProducts([]);
    setProductsLoading(true);
    sf(API+"/products?fast=1&root="+encodeURIComponent(selRoot)).then(d=>{
      if(!alive)return;
      setProducts(d.products||[]);
      // v8.8.32: 교차 선택 — 이미 제품이 선택된 상태에서 다른 DB 루트를 클릭하면
      //   그 DB 에 같은 제품이 있을 경우 자동으로 view 를 갱신. UX: DB 를 바꿔도
      //   제품 클릭을 다시 안 해도 됨.
      if(selProd){
        if(restoringSqlHistoryRef.current){restoringSqlHistoryRef.current=false;return;}
        const match=(d.products||[]).find(p=>p.name===selProd);
        if(match){
          setSelectedCols([]);setSortSpec(null);setAggregateSpec(null);
          setSql("");
          loadHiveView(selRoot,selProd,"",[],{full:true,page:0,sortOverride:null,aggregateOverride:null});
        }
      }
    }).catch(()=>{if(alive)setProducts([]);}).finally(()=>{if(alive)setProductsLoading(false);});
    return()=>{alive=false;};
  },[selRoot]);


  useEffect(()=>{
    const q=String(colSearch||"").trim();
    if(!data?.all_columns_truncated||!q){
      setRemoteCols([]);
      setRemoteColsLoading(false);
      return;
    }
    const params={q,limit:fbSettings.schema_column_page_size||200,_ts:Date.now()};
    if(mode==="hive"&&selRoot&&selProd){params.root=selRoot;params.product=selProd;}
    else if(mode==="base"&&selBaseFile){params.file=selBaseFile;if(accessScope)params.access_scope=accessScope;}
    else if(mode==="rootpq"&&selRootPq){params.file=selRootPq;}
    else return;
    let alive=true;
    setRemoteColsLoading(true);
    const t=setTimeout(()=>{
      sf(API+"/columns/search"+qs(params)).then(d=>{
        if(!alive)return;
        setRemoteCols((d.columns||[]).map(c=>String(c||"")).filter(Boolean));
        if(d.dtypes)setData(prev=>prev?{...prev,dtypes:{...(prev.dtypes||{}),...(d.dtypes||{})}}:prev);
      }).catch(()=>{if(alive)setRemoteCols([]);}).finally(()=>{if(alive)setRemoteColsLoading(false);});
    },220);
    return()=>{alive=false;clearTimeout(t);};
  },[colSearch,data?.all_columns_truncated,mode,selRoot,selProd,selBaseFile,selRootPq,fbSettings.schema_column_page_size]);

  const markSqlHistoryReused=historyId=>{
    const key=String(historyId||"").trim();
    if(!key)return;
    setSqlHistory(items=>items.map(item=>item.history_id===key?{
      ...item,
      reuse_count:Number(item.reuse_count||0)+1,
      last_reused_at:new Date().toISOString(),
      last_reused_by:user?.username||"",
    }:item));
  };

  // 첫 클릭은 스키마(meta_only)를 즉시 그리고, 최신 파티션 500행 샘플은 백그라운드로 이어서 채운다.
  // SQL/SELECT/정렬/집계가 있으면 기존처럼 한 번에 조회한다.
  const loadHiveView=(root,prod,sqlQ,selColsOverride,{full=true,page:pageArg=0,sortOverride=undefined,aggregateOverride=undefined,reuseHistoryId=""}={})=>{
    const{seq,signal,queryId}=nextViewRequest();
    setLoading(true);setTab("data");setMode("hive");setSelProd(prod);setSelRootPq("");setError("");setBaseRaw(null);
    setSelBaseMeta(null);setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);setEditOriginCols([]);
    setSampleLoading(false);
    const sc=selColsOverride||selectedCols;
    const activeSort=sortOverride===undefined?sortSpec:sortOverride;
    const activeAggregate=aggregateOverride===undefined?aggregateSpec:aggregateOverride;
    const params={root,product:prod,sql:sqlQ||"",rows:PAGE_SIZE,page:pageArg,page_size:PAGE_SIZE,cols:20,select_cols:sc.length?sc.join(","):"",meta_only:!full,query_session:viewSessionRef.current,query_id:queryId,...sortParams(activeSort),...aggregateParams(activeAggregate),...(reuseHistoryId?{reuse_history_id:reuseHistoryId}:{})};
    const url=API+"/view"+qs(params);
    const previewFirst=full&&!(sqlQ||"").trim()&&!sc.length&&pageArg===0&&!activeSort&&!activeAggregate;
    if(previewFirst){
      // 최신 date 파티션 한정 샘플 — 서버가 500행까지 허용하므로 넉넉히 요청.
      const sampleUrl=API+"/view"+qs({...params,rows:DB_PREVIEW_ROWS,page_size:DB_PREVIEW_ROWS,meta_only:false});
      sf(API+"/view"+qs({...params,meta_only:true}),{signal}).then(d=>{
        if(seq!==viewSeqRef.current)return;
        setSelectedCols([]);setData(d);setLoading(false);setSampleLoading(true);
        sf(sampleUrl,{signal}).then(d2=>{
          if(seq!==viewSeqRef.current)return;
          setData(d2);setSampleLoading(false);
        }).catch(e=>{
          if(isViewAbort(e))return;
          if(seq!==viewSeqRef.current)return;
          setError(e.message);setSampleLoading(false);
        });
      }).catch(e=>{if(seq!==viewSeqRef.current||isViewAbort(e))return;setError(e.message);setLoading(false);});
      return;
    }
    sf(url,{signal}).then(d=>{if(seq!==viewSeqRef.current)return;setSelectedCols(sc.length?selectedColsFromResponse(d,sc):[]);setData(d);markSqlHistoryReused(reuseHistoryId);setLoading(false);}).catch(e=>{if(seq!==viewSeqRef.current||isViewAbort(e))return;setError(e.message);setLoading(false);});
  };

  const loadRootPqView=(file,sqlQ,selColsOverride,{full=true,page:pageArg=0,sortOverride=undefined,aggregateOverride=undefined,reuseHistoryId=""}={})=>{
    const{seq,signal,queryId}=nextViewRequest();
    setLoading(true);setTab("data");setMode("rootpq");setSelRootPq(file);setSelProd("");setError("");setBaseRaw(null);
    setSelBaseMeta(null);setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);setEditOriginCols([]);
    setSampleLoading(false);
    const sc=selColsOverride||selectedCols;
    const activeSort=sortOverride===undefined?sortSpec:sortOverride;
    const activeAggregate=aggregateOverride===undefined?aggregateSpec:aggregateOverride;
    const params={file,sql:sqlQ||"",rows:PAGE_SIZE,page:pageArg,page_size:PAGE_SIZE,cols:10,select_cols:sc.length?sc.join(","):"",meta_only:!full,query_session:viewSessionRef.current,query_id:queryId,...sortParams(activeSort),...aggregateParams(activeAggregate),...(reuseHistoryId?{reuse_history_id:reuseHistoryId}:{})};
    const url=API+"/root-parquet-view"+qs(params);
    const previewFirst=full&&!(sqlQ||"").trim()&&!sc.length&&pageArg===0&&!activeSort&&!activeAggregate;
    if(previewFirst){
      sf(API+"/root-parquet-view"+qs({...params,meta_only:true}),{signal}).then(d=>{
        if(seq!==viewSeqRef.current)return;
        setSelectedCols([]);setData(d);setLoading(false);setSampleLoading(true);
        sf(url,{signal}).then(d2=>{
          if(seq!==viewSeqRef.current)return;
          setData(d2);setSampleLoading(false);
        }).catch(e=>{
          if(isViewAbort(e))return;
          if(seq!==viewSeqRef.current)return;
          setError(e.message);setSampleLoading(false);
        });
      }).catch(e=>{if(seq!==viewSeqRef.current||isViewAbort(e))return;setError(e.message);setLoading(false);});
      return;
    }
    sf(url,{signal}).then(d=>{if(seq!==viewSeqRef.current)return;setSelectedCols(sc.length?selectedColsFromResponse(d,sc):[]);setData(d);markSqlHistoryReused(reuseHistoryId);setLoading(false);}).catch(e=>{if(seq!==viewSeqRef.current||isViewAbort(e))return;setError(e.message);setLoading(false);});
  };

  const currentSqlHistoryTargetParams=()=>{
    if(mode==="hive"&&selRoot&&selProd)return{scope:"db_product",root:selRoot};
    if(mode==="rootpq"&&selRootPq)return{scope:"rootpq",file:selRootPq};
    if(mode==="base"&&selBaseFile)return withAccess({scope:"base",file:selBaseFile});
    return null;
  };

  // v8.8.16: "실행" 클릭 = 실제 행 조회 트리거. meta_only 없이 호출 → 서버에서 collect.
  const applySql=(sqlOverride,selectedColsOverride,sortOverride=undefined,aggregateOverride=undefined,reuseHistoryId="")=>{
    const activeSql=typeof sqlOverride==="string"?sqlOverride:sql;
    if(mode==="base"&&isBaseEditing){
      setError("편집 모드에서는 SQL 실행이 비활성됩니다.");
      return;
    }
    const historyKey=String(activeSql||"").trim();
    if(/^fb_sql_exec_[0-9a-f]{12}$/i.test(historyKey)){
      const targetParams=currentSqlHistoryTargetParams();
      if(!targetParams){setError("먼저 DB나 파일을 선택하세요.");return;}
      setLoading(true);setError("");
      sf(API+"/sql/execution-history"+qs({...targetParams,history_id:historyKey,limit:1,_ts:Date.now()})).then(d=>{
        const resolved=String(d.history?.[0]?.sql||"").trim();
        if(!resolved)throw new Error("현재 DB/파일에서 해당 SQL 고유키를 찾을 수 없습니다.");
        setSqlFromInput(resolved);
        setLoading(false);
        applySql(resolved,undefined,sortOverride,aggregateOverride,historyKey);
      }).catch(e=>{setError(e.message||String(e));setLoading(false);});
      return;
    }
    const parsedSql=splitDisplaySql(activeSql);
    const activeSelectedCols=Array.isArray(selectedColsOverride)?selectedColsOverride:parsedSql.selectedColumns;
    const activeSort=sortOverride===undefined?sortSpec:sortOverride;
    const activeAggregate=aggregateOverride===undefined?aggregateSpec:aggregateOverride;
    setSelectedCols(activeSelectedCols);
    if(mode==="rootpq"&&selRootPq)loadRootPqView(selRootPq,activeSql,activeSelectedCols,{full:true,page:0,sortOverride:activeSort,aggregateOverride:activeAggregate,reuseHistoryId});
    else if(mode==="base"&&selBaseFile){
      // Base JSON/md files have no SQL surface — silently ignore. Tabular
      // parquet/csv re-load with the SQL param applied server-side.
      if(baseRaw)return; // json/md 는 SQL 적용 불가 — baseRaw 상태로 판단
      setLoading(true);setError("");
      // full=true 와 동일 — SQL 이 비어도 sample 행을 보여줘야 하므로 meta_only 꺼둠.
      const url=API+"/base-file-view"+qs(withAccess({file:selBaseFile,sql:activeSql||"",rows:PAGE_SIZE,page:0,page_size:PAGE_SIZE,cols:10,meta_only:false,_ts:Date.now(),reuse_history_id:reuseHistoryId||"",
        select_cols:activeSelectedCols.length?activeSelectedCols.join(","):"",...sortParams(activeSort),...aggregateParams(activeAggregate)}));
      sf(url).then(d=>{setSelectedCols(activeSelectedCols.length?selectedColsFromResponse(d,activeSelectedCols):[]);setData(d);if(!d.kind)syncBaseEditState(d);markSqlHistoryReused(reuseHistoryId);setLoading(false);}).catch(e=>{setError(e.message||String(e));setLoading(false);});
    }
    else if(selRoot&&selProd)loadHiveView(selRoot,selProd,activeSql,activeSelectedCols,{full:true,page:0,sortOverride:activeSort,aggregateOverride:activeAggregate,reuseHistoryId});
  };

  useEffect(()=>{
    if(!showSqlHistory)return undefined;
    const targetParams=currentSqlHistoryTargetParams();
    if(!targetParams){
      setSqlHistory([]);setSqlHistoryError("");setSqlHistoryLoading(false);
      return undefined;
    }
    const params={...targetParams,limit:500,_ts:Date.now()};
    let alive=true;
    setSqlHistoryLoading(true);setSqlHistoryError("");
    sf(API+"/sql/execution-history"+qs(params)).then(d=>{
      if(alive)setSqlHistory(Array.isArray(d.history)?d.history:[]);
    }).catch(e=>{
      if(alive){setSqlHistory([]);setSqlHistoryError(e.message||String(e));}
    }).finally(()=>{if(alive)setSqlHistoryLoading(false);});
    return()=>{alive=false;};
  },[showSqlHistory,mode,selRoot,selProd,selRootPq,selBaseFile,accessScope]);

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
      };
      const d=await sf(API+"/sql/llm/draft",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      setAiSqlResult(d);
      if(d.ok&&(typeof d.sql==="string")){
        const nextSql=d.display_sql||d.sql||"";
        const nextSelectedCols=Array.isArray(d.selected_columns)?d.selected_columns.map(c=>String(c||"")).filter(Boolean):[];
        const nextAggregate=cleanAggregateSpec(d.aggregate);
        setSql(nextSql);
        setSelectedCols(nextSelectedCols);
        setSortSpec(null);
        setAggregateSpec(nextAggregate);
        applySql(nextSql,nextSelectedCols,null,nextAggregate);
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
        sql:payloadOverride.sql!==undefined?payloadOverride.sql:(aiSqlResult.display_sql||aiSqlResult.sql||sql||""),
        sort:payloadOverride.sort!==undefined?(cleanSortSpec(payloadOverride.sort)||{}):(cleanSortSpec(aiSqlResult.sort)||sortSpec||{}),
        aggregate:payloadOverride.aggregate!==undefined?(cleanAggregateSpec(payloadOverride.aggregate)||{}):(cleanAggregateSpec(aiSqlResult.aggregate)||aggregateSpec||{}),
        selected_columns:Array.isArray(payloadOverride.selected_columns)?payloadOverride.selected_columns:(Array.isArray(aiSqlResult.selected_columns)?aiSqlResult.selected_columns:selectedCols),
        columns,
        scope:mode,
        root:selRoot||"",
        product:selProd||"",
        file:selBaseFile||selRootPq||"",
        choice:payloadOverride.choice||"",
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
  // v9.1.x: 현재 폴더(baseDir)의 "바로 아래" 항목만 표시한다. 예전에는 최상위에서 모든
  //   kind==="dir" 을 보여줘 cache/ml_table_lookup/... 같은 깊은 폴더가 평탄하게 노출됐다.
  //   이제 depth(경로 세그먼트) 기준으로 즉시 하위 폴더/파일만 보여주고, 폴더를 클릭해
  //   들어가야 그 안이 보인다.
  const baseIsImmediateChild=(path)=>{
    const p=String(path||"");
    if(baseDir){
      if(!p.startsWith(baseDir+"/"))return false;
      return p.slice(baseDir.length+1).indexOf("/")===-1;
    }
    return p.indexOf("/")===-1;
  };
  const baseItems = embedded
    ? baseAllItems
    : baseDir
    ? [
        {name:"상위 폴더",path:"__base_dir_up__",kind:"dir_up",ext:"dir",description:"상위 폴더로 이동"},
        ...baseAllItems.filter(f => baseIsImmediateChild(basePathOf(f)))
      ]
    : baseAllItems.filter(f => baseIsImmediateChild(basePathOf(f)));
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
    setBaseEditContextMenu(null);
    baseEditCopiedRowsRef.current=null;
    setIsBaseEditing(true);
    setTab("data");
  };

  const cancelBaseEdit=()=>{
    setEditCols(editOriginCols.map(c=>String(c||"")));
    setEditRows(editOriginRows.map(r=>r.slice()));
    setIsBaseEditing(false);
    setSelectedEditCell({r:0,c:0});
    setBaseEditContextMenu(null);
    baseEditCopiedRowsRef.current=null;
  };

  const restoreBaseEdit=()=>{
    setEditCols(editOriginCols.map(c=>String(c||"")));
    setEditRows(editOriginRows.map(r=>r.slice()));
    setSelectedEditCell({r:0,c:0});
    setBaseEditContextMenu(null);
    baseEditCopiedRowsRef.current=null;
  };

  const patchBaseHeader=(c,value)=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    setEditCols(prev=>prev.map((col,i)=>i===c?value:col));
    setSelectedEditCell(cur=>({r:cur.r||0,c}));
  };

  const finalizeBaseHeaders=()=>{
    setEditCols(prev=>normalizeColumnNames(prev));
  };

  const addBaseEditColumn=()=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    setEditCols(prev=>{
      const next=[...prev,nextGeneratedColumnName(prev)];
      setSelectedEditCell(cur=>({r:cur.r||0,c:next.length-1}));
      return next;
    });
    setEditRows(prev=>prev.map(row=>[...row,""]));
  };

  const deleteBaseEditColumn=(targetC=selectedEditCell.c)=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    if(editCols.length<=1){
      toast.warn("마지막 열은 삭제할 수 없습니다.");
      return;
    }
    const idx=Math.max(0,Math.min(targetC,editCols.length-1));
    setEditCols(prev=>prev.filter((_,i)=>i!==idx));
    setEditRows(prev=>prev.map(row=>row.filter((_,i)=>i!==idx)));
    setSelectedEditCell(cur=>({r:cur.r||0,c:Math.max(0,Math.min(idx,editCols.length-2))}));
  };

  const insertBaseEditRowBelow=(targetR=selectedEditCell.r)=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    if(!editCols.length){
      setError("열 정보가 없어 새 행을 추가할 수 없습니다.");
      return;
    }
    setEditRows(prev=>{
      const idx=prev.length?Math.max(0,Math.min(targetR,prev.length-1)):-1;
      const insertAt=idx+1;
      const next=[
        ...prev.slice(0,insertAt),
        Array(editCols.length).fill(""),
        ...prev.slice(insertAt),
      ];
      setSelectedEditCell({r:insertAt,c:0});
      return next;
    });
    setBaseEditContextMenu(null);
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
    setBaseEditContextMenu(null);
  };

  const copyBaseEditRow=async(targetR=selectedEditCell.r)=>{
    if(!isBaseEditing||!editRows.length)return;
    const idx=Math.max(0,Math.min(targetR,editRows.length-1));
    const row=normalizeGridRows([editRows[idx]||[]],editCols.length,"")[0]||[];
    baseEditCopiedRowsRef.current=[row];
    setSelectedEditCell(cur=>({r:idx,c:Math.max(0,Math.min(cur.c||0,Math.max(editCols.length-1,0)))}));
    let copiedToSystem=true;
    try{
      await writeClipboardText(row.join("\t"));
    }catch(_){
      copiedToSystem=false;
    }finally{
      toast.ok(copiedToSystem?`${idx+1}행 복사됨`:`${idx+1}행 앱 안에 복사됨`,1400);
      setBaseEditContextMenu(null);
    }
  };

  const openBaseEditRowMenu=(e,targetR)=>{
    if(!isBaseEditing)return;
    e.preventDefault();
    e.stopPropagation();
    const menuWidth=188;
    const menuHeight=168;
    const maxX=Math.max(8,(window.innerWidth||0)-menuWidth-8);
    const maxY=Math.max(8,(window.innerHeight||0)-menuHeight-8);
    setSelectedEditCell(cur=>({r:targetR,c:Math.max(0,Math.min(cur.c||0,Math.max(editCols.length-1,0)))}));
    setBaseEditContextMenu({r:targetR,x:Math.min(e.clientX,maxX),y:Math.min(e.clientY,maxY)});
  };

  // 건드린 행 하나만 새 배열로 만든다. 예전처럼 prev.map(x=>x.slice()) 로 전체를 복제하면
  // 키 입력 한 번에 (행×열) 개 문자열이 복사되고 모든 행의 참조가 바뀌어 표 전체가 리렌더된다.
  const patchBaseCell=useCallback((r,c,val)=>{
    if(r<0||c<0)return;
    setEditRows(prev=>{
      const width=Math.max(editColCountRef.current,c+1);
      const next=prev.slice();
      while(next.length<=r)next.push(Array(width).fill(""));
      next[r]=extendRow(next[r],Math.max(width,next[r].length),"");
      next[r][c]=val;
      return next;
    });
  },[]);

  // 편집 그리드 키보드 이동.
  // 상하/Enter/Tab 은 항상 셀 이동, 좌우는 캐럿이 셀 텍스트 끝에 닿았을 때만 옆 셀로 넘어간다
  // (셀 안에서 글자 사이를 오가는 기본 동작을 막지 않기 위해서다).
  const applyBaseEditFocus=useCallback((r,c,caret="select")=>{
    const root=baseEditGridRef.current;
    if(!root)return false;
    const el=root.querySelector(`input[data-base-edit-cell="1"][data-row="${r}"][data-col="${c}"]`);
    if(!el)return false;
    el.focus();
    try{
      if(caret==="select")el.select();
      else if(caret==="start")el.setSelectionRange(0,0);
      else el.setSelectionRange(el.value.length,el.value.length);
    }catch(_){}
    // scrollIntoView({block:"nearest"}) 는 셀을 컨테이너 가장자리에 붙여서 sticky 헤더에 가린다.
    ensureGridCellVisible(root,r,c);
    return true;
  },[]);

  // 가상화 때문에 목표 행이 아직 DOM 에 없을 수 있다. 그럴 땐 그 행이 그려지도록
  // 먼저 스크롤을 옮기고, 다음 렌더 뒤에 포커스를 적용한다(아래 useEffect).
  const focusBaseEditCell=useCallback((r,c,caret="select")=>{
    if(applyBaseEditFocus(r,c,caret))return true;
    pendingCellFocusRef.current={r,c,caret};
    editVirtRef.current?.scrollToRow(r);
    return false;
  },[applyBaseEditFocus]);

  const focusBaseEditHeader=useCallback((c)=>{
    const root=baseEditGridRef.current;
    if(!root)return false;
    const el=root.querySelector(`input[data-base-edit-header="1"][data-col="${c}"]`);
    if(!el)return false;
    el.focus();
    try{el.select();}catch(_){}
    return true;
  },[]);

  const editRowCount=editRows.length;
  const editColCount=editCols.length;
  editColCountRef.current=editColCount;
  editRowCountRef.current=editRowCount;
  selectedEditCellRef.current=selectedEditCell;
  deleteBaseEditRowRef.current=deleteBaseEditRow;
  openBaseEditRowMenuRef.current=openBaseEditRowMenu;

  const readRowCount=data?.data?.length||0;
  const editVirt=useVirtualRows(baseEditGridRef,editRowCount,editRowCount>VIRT_MIN_ROWS);
  const readVirt=useVirtualRows(baseReadGridRef,readRowCount,readRowCount>VIRT_MIN_ROWS);
  editVirtRef.current=editVirt;
  readVirtRef.current=readVirt;

  // 가상화로 아직 안 그려진 행에 포커스를 요청했으면 렌더 직후에 적용한다.
  useEffect(()=>{
    const pending=pendingCellFocusRef.current;
    if(!pending)return;
    pendingCellFocusRef.current=null;
    applyBaseEditFocus(pending.r,pending.c,pending.caret);
  });

  // ── 값 찾기 ──────────────────────────────────────────────────────────────
  // 편집 모드 여부는 **찾기(useMemo)보다 먼저** 선언해야 한다. 아래 findHits 는
  // 렌더 중 실행되는 useMemo 라, 선언이 뒤에 있으면 TDZ
  // ("Cannot access 'isBaseEditingMode' before initialization")로 파일탐색기 탭이
  // 통째로 크래시한다. 실제로 v9.5.61 그리드 개편 이후 그 상태였다.
  const isBaseEditingMode = mode==="base"&&isBaseEditing;

  // 가상화 때문에 브라우저 Ctrl+F 는 화면 밖 행을 못 찾는다. 그리고 원래도 서버가
  // 잘라 보낸 행은 못 찾았다. 그래서 로드된 데이터 전체를 직접 훑는다.
  const findNeedleRaw=useDeferredValue(findQuery);
  const findNeedle=findOpen?String(findNeedleRaw||"").trim().toLowerCase():"";
  const findHits=useMemo(()=>{
    if(!findNeedle)return[];
    const hits=[];
    if(isBaseEditingMode){
      for(let r=0;r<editRows.length&&hits.length<FIND_MAX_HITS;r++){
        const row=editRows[r]||[];
        for(let c=0;c<editColCount&&hits.length<FIND_MAX_HITS;c++){
          const v=row[c];
          if(v==null||v==="")continue;
          if(cellMatchesFind(String(v),findNeedle,findExact))hits.push({r,c});
        }
      }
    }else{
      const cols=data?.showing_cols||data?.columns||[];
      const rows=data?.data||[];
      for(let r=0;r<rows.length&&hits.length<FIND_MAX_HITS;r++){
        const row=rows[r];
        for(let c=0;c<cols.length&&hits.length<FIND_MAX_HITS;c++){
          const v=row?.[cols[c]];
          if(v==null||v==="")continue;
          if(cellMatchesFind(String(v),findNeedle,findExact))hits.push({r,c});
        }
      }
    }
    return hits;
  },[findNeedle,findExact,isBaseEditingMode,editRows,editColCount,data]);

  const findHitCount=findHits.length;
  const findPos=findHitCount?Math.min(findIdx,findHitCount-1):0;
  const findCur=findHitCount?findHits[findPos]:null;
  const findCurRow=findCur?findCur.r:-1;
  const findCurCol=findCur?findCur.c:-1;

  // 검색어/조건이 바뀌면 첫 매치부터 다시 본다.
  useEffect(()=>{setFindIdx(0);},[findNeedle,findExact,isBaseEditingMode]);

  // 현재 매치로 스크롤. 아직 안 그려진 행이면 그 근처로 스크롤한 뒤 다음 렌더에 맞춘다.
  useEffect(()=>{
    if(findCurRow<0)return;
    const editing=isBaseEditingMode;
    const root=editing?baseEditGridRef.current:baseReadGridRef.current;
    if(!root)return;
    if(ensureGridCellVisible(root,findCurRow,findCurCol))return;
    (editing?editVirtRef.current:readVirtRef.current)?.scrollToRow(findCurRow);
    pendingFindScrollRef.current={r:findCurRow,c:findCurCol};
  },[findCurRow,findCurCol,isBaseEditingMode]);

  useEffect(()=>{
    const pending=pendingFindScrollRef.current;
    if(!pending)return;
    const root=isBaseEditingMode?baseEditGridRef.current:baseReadGridRef.current;
    if(ensureGridCellVisible(root,pending.r,pending.c))pendingFindScrollRef.current=null;
  });

  const stepFind=useCallback((delta)=>{
    setFindIdx(cur=>{
      const total=findHitsRef.current;
      if(!total)return 0;
      return((cur+delta)%total+total)%total;
    });
  },[]);
  findHitsRef.current=findHitCount;

  const openFind=useCallback(()=>{
    setFindOpen(true);
    requestAnimationFrame(()=>{
      const el=findInputRef.current;
      if(el){el.focus();el.select();}
    });
  },[]);

  const closeFind=useCallback(()=>{
    setFindOpen(false);
    pendingFindScrollRef.current=null;
  },[]);

  const onFindKeyDown=useCallback((e)=>{
    if(e.key==="Escape"){e.preventDefault();closeFind();return;}
    if(e.key!=="Enter")return;
    if(e.nativeEvent?.isComposing||e.keyCode===229)return;
    e.preventDefault();
    stepFind(e.shiftKey?-1:1);
  },[closeFind,stepFind]);

  // Ctrl+F 는 데이터 탭이 떠 있을 때만 가로챈다.
  useEffect(()=>{
    if(!data||tab!=="data")return undefined;
    const onKey=(e)=>{
      if(!(e.ctrlKey||e.metaKey)||e.altKey)return;
      if(e.key!=="f"&&e.key!=="F")return;
      e.preventDefault();
      openFind();
    };
    window.addEventListener("keydown",onKey);
    return()=>window.removeEventListener("keydown",onKey);
  },[data,tab,openFind]);

  const onBaseEditCellKeyDown=useCallback((e)=>{
    if(e.nativeEvent?.isComposing||e.keyCode===229)return;
    if(e.ctrlKey||e.metaKey||e.altKey)return;
    const key=e.key;
    if(key!=="ArrowUp"&&key!=="ArrowDown"&&key!=="ArrowLeft"&&key!=="ArrowRight"&&key!=="Enter"&&key!=="Tab")return;
    const el=e.currentTarget;
    const r=Number(el.getAttribute("data-row"));
    const c=Number(el.getAttribute("data-col"));
    if(!Number.isFinite(r)||!Number.isFinite(c))return;
    const lastR=editRowCount-1;
    const lastC=editColCount-1;
    if(lastR<0||lastC<0)return;
    if(key==="ArrowUp"){
      e.preventDefault();
      if(r>0)focusBaseEditCell(r-1,c);
      else focusBaseEditHeader(c);
      return;
    }
    if(key==="ArrowDown"){
      e.preventDefault();
      if(r<lastR)focusBaseEditCell(r+1,c);
      return;
    }
    if(key==="Enter"){
      e.preventDefault();
      const nr=e.shiftKey?r-1:r+1;
      if(nr>=0&&nr<=lastR)focusBaseEditCell(nr,c);
      return;
    }
    if(key==="Tab"){
      let tr=-1,tc=-1;
      if(e.shiftKey){
        if(c>0){tr=r;tc=c-1;}
        else if(r>0){tr=r-1;tc=lastC;}
      }else{
        if(c<lastC){tr=r;tc=c+1;}
        else if(r<lastR){tr=r+1;tc=0;}
      }
      if(tr<0)return;  // 그리드 경계에서는 기본 Tab 으로 그리드 밖으로 빠져나간다
      e.preventDefault();
      focusBaseEditCell(tr,tc);
      return;
    }
    if(e.shiftKey)return;
    const start=el.selectionStart;
    const end=el.selectionEnd;
    if(start==null||start!==end)return;
    if(key==="ArrowLeft"){
      if(start!==0)return;
      e.preventDefault();
      if(c>0)focusBaseEditCell(r,c-1,"end");
      else if(r>0)focusBaseEditCell(r-1,lastC,"end");
    }else{
      if(end!==el.value.length)return;
      e.preventDefault();
      if(c<lastC)focusBaseEditCell(r,c+1,"start");
      else if(r<lastR)focusBaseEditCell(r+1,0,"start");
    }
  },[editRowCount,editColCount,focusBaseEditCell,focusBaseEditHeader]);

  const onBaseEditHeaderKeyDown=useCallback((e)=>{
    if(e.nativeEvent?.isComposing||e.keyCode===229)return;
    if(e.ctrlKey||e.metaKey||e.altKey)return;
    if(e.key!=="ArrowDown"&&e.key!=="Enter")return;
    const c=Number(e.currentTarget.getAttribute("data-col"));
    if(!Number.isFinite(c)||!editRowCount)return;
    e.preventDefault();
    focusBaseEditCell(0,c);
  },[editRowCount,focusBaseEditCell]);

  // 셀 이벤트는 전부 공용 핸들러 하나로 처리하고 좌표는 data-row/data-col 에서 읽는다.
  // 셀마다 화살표 함수를 만들면 렌더마다 (행×열) 개 클로저가 새로 생긴다.
  const onBaseCellChange=useCallback((e)=>{
    const at=readCellCoord(e.target);
    if(at)patchBaseCell(at.r,at.c,e.target.value);
  },[patchBaseCell]);

  const onBaseCellFocus=useCallback((e)=>{
    const at=readCellCoord(e.target);
    if(!at)return;
    // 같은 셀이면 state 를 건드리지 않는다(td onClick + input onFocus 로 두 번 렌더되던 것 방지).
    setSelectedEditCell(cur=>(cur.r===at.r&&cur.c===at.c?cur:at));
  },[]);

  const onBaseCellClick=useCallback((e)=>{
    const at=readCellCoord(e.currentTarget);
    if(!at)return;
    setSelectedEditCell(cur=>(cur.r===at.r&&cur.c===at.c?cur:at));
  },[]);

  const onBaseRowDeleteClick=useCallback((e)=>{
    e.stopPropagation();
    const r=Number(e.currentTarget.getAttribute("data-row"));
    if(Number.isFinite(r))deleteBaseEditRowRef.current(r);
  },[]);

  const onBaseRowContextMenu=useCallback((e)=>{
    const r=Number(e.currentTarget.getAttribute("data-vrow"));
    if(Number.isFinite(r))openBaseEditRowMenuRef.current(e,r);
  },[]);

  const pasteBaseRows=useCallback((rowsRaw,targetCell=null)=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    let rows=(rowsRaw||[]).map(r=>(r||[]).map(v=>v==null?"":String(v)));
    const sel=selectedEditCellRef.current;
    const targetR=Number.isFinite(targetCell?.r)?targetCell.r:sel.r;
    const targetC=Number.isFinite(targetCell?.c)?targetCell.c:sel.c;
    const startR=pasteMode==="append"?null:(Math.max(0,Math.min(targetR,Math.max(editRowCountRef.current-1,0))));
    const startC=pasteMode==="append"?0:Math.max(0,Math.min(targetC,Math.max(editCols.length-1,0)));
    const headerLike=looksLikePasteHeader(rows[0],rows.slice(1),editCols,startC)||isHeaderMatch(rows,editCols);
    const headerCells=headerLike?(rows[0]||[]):null;
    if(headerLike){
      rows=rows.slice(1);
    }
    if(!rows.length&&!headerCells)return;
    const pasteWidth=Math.max(headerCells?.length||0,...rows.map(r=>r.length),1);
    const nextWidth=Math.max(editCols.length,startC+pasteWidth);
    let nextCols=extendColumns(editCols,nextWidth);
    if(headerCells){
      headerCells.forEach((value,ci)=>{
        if(startC+ci<nextCols.length)nextCols[startC+ci]=String(value||"").trim()||nextCols[startC+ci];
      });
      nextCols=normalizeColumnNames(nextCols);
      setEditCols(nextCols);
    }else if(nextWidth>editCols.length){
      setEditCols(nextCols);
    }
    const normalized=normalizeGridRows(rows,pasteWidth,"");
    setEditRows(prev=>{
      let next=prev.map(x=>extendRow(x,nextWidth,""));
      const actualStartR=pasteMode==="append"?(next.length):(startR??0);
      normalized.forEach((row,ri)=>{
        const targetR=actualStartR+ri;
        while(next.length<=targetR)next.push(Array(editCols.length).fill(""));
        next[targetR]=extendRow(next[targetR],nextWidth,"");
        for(let ci=0;ci<row.length;ci++){
          if(startC+ci>=nextWidth)break;
          next[targetR][startC+ci]=row[ci];
        }
      });
      setSelectedEditCell({r:actualStartR,c:startC});
      return next;
    });
  },[canEditCurrentBase,editCols,isBaseEditing,pasteMode]);

  const readBasePasteRows=useCallback((e)=>{
    const text=e.clipboardData?.getData("text/plain")||"";
    if(text.trim()){
      const [rows]=detectDelimiterFromGridText(text);
      return rows;
    }
    return baseEditCopiedRowsRef.current||[];
  },[]);

  // 블록 붙여넣기(여러 셀/행)는 탭이나 개행이 있을 때만 동작한다.
  // 모든 셀에 내려가는 핸들러라 반드시 안정된 참조여야 한다(아니면 행 memo 가 깨진다).
  const onBasePaste=useCallback((e)=>{
    if(!isBaseEditing)return;
    const text=e.clipboardData?.getData("text/plain")||"";
    if(text&&!/[\t\r\n]/.test(text)&&isTextEntryTarget(e.target)){
      e.stopPropagation();
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    const rows=readBasePasteRows(e);
    if(!rows.length)return;
    pasteBaseRows(rows,basePasteTargetFromEvent(e));
  },[isBaseEditing,readBasePasteRows,pasteBaseRows]);

  const pasteCopiedBaseRowsBelow=async(targetR=selectedEditCell.r)=>{
    if(!canEditCurrentBase||!isBaseEditing)return;
    let rows=(baseEditCopiedRowsRef.current||[]).map(r=>(r||[]).slice());
    if(!rows.length&&typeof navigator!=="undefined"&&navigator.clipboard?.readText){
      try{
        const text=await navigator.clipboard.readText();
        if(text.trim()){
          [rows]=detectDelimiterFromGridText(text);
        }
      }catch(_){}
    }
    if(isHeaderMatch(rows,editCols)){
      rows=rows.slice(1);
    }
    const headerLike=looksLikePasteHeader(rows[0],rows.slice(1),editCols,0);
    const headerCells=headerLike?(rows[0]||[]):null;
    if(headerLike)rows=rows.slice(1);
    if(!rows.length){
      if(headerCells?.length){
        const nextWidth=Math.max(editCols.length,headerCells.length);
        let nextCols=extendColumns(editCols,nextWidth);
        headerCells.forEach((value,ci)=>{nextCols[ci]=String(value||"").trim()||nextCols[ci];});
        setEditCols(normalizeColumnNames(nextCols));
        setEditRows(prev=>prev.map(row=>extendRow(row,nextWidth,"")));
        setBaseEditContextMenu(null);
        return;
      }
      setBaseEditContextMenu(null);
      toast.error("복사한 행이 없습니다.");
      return;
    }
    const pasteWidth=Math.max(headerCells?.length||0,...rows.map(r=>r.length),1);
    const nextWidth=Math.max(editCols.length,pasteWidth);
    let nextCols=extendColumns(editCols,nextWidth);
    if(headerCells){
      headerCells.forEach((value,ci)=>{nextCols[ci]=String(value||"").trim()||nextCols[ci];});
      setEditCols(normalizeColumnNames(nextCols));
    }else if(nextWidth>editCols.length){
      setEditCols(nextCols);
    }
    const normalized=normalizeGridRows(rows,pasteWidth,"").map(row=>extendRow(row,nextWidth,""));
    setEditRows(prev=>{
      const idx=prev.length?Math.max(0,Math.min(targetR,prev.length-1)):-1;
      const insertAt=idx+1;
      const next=[
        ...prev.slice(0,insertAt).map(row=>extendRow(row,nextWidth,"")),
        ...normalized.map(r=>r.slice()),
        ...prev.slice(insertAt).map(row=>extendRow(row,nextWidth,"")),
      ];
      setSelectedEditCell({r:insertAt,c:0});
      return next;
    });
    setBaseEditContextMenu(null);
    toast.ok(`${normalized.length}행 붙여넣음`,1400);
  };

  useEffect(()=>{
    if(!isBaseEditing)return;
    const onWindowPaste=(e)=>{
      if(!canEditCurrentBase)return;
      const target=e.target;
      if(target?.closest?.("[data-base-edit-grid='1']"))return;
      const tag=String(target?.tagName||"").toLowerCase();
      if(tag==="input"||tag==="textarea"||tag==="select"||target?.isContentEditable)return;
      const rows=readBasePasteRows(e);
      if(!rows.length)return;
      e.preventDefault();
      pasteBaseRows(rows);
    };
    window.addEventListener("paste",onWindowPaste);
    return()=>window.removeEventListener("paste",onWindowPaste);
  },[canEditCurrentBase,isBaseEditing,readBasePasteRows,pasteBaseRows]);

  const saveBaseEdit=async()=>{
    if(baseSaveBusy)return;
    if(!canEditCurrentBase||!isBaseEditing){setError("현재 편집 상태가 아닙니다.");return;}
    if(!editCols.length){setError("열이 없습니다.");return;}
    const saveCols=normalizeColumnNames(editCols);
    const saveRows=editRows.map(row=>extendRow(row,saveCols.length,""));
    setEditCols(saveCols);
    setEditRows(saveRows);
    const csvText=buildSaveText(saveCols,saveRows,saveDelimiter,includeHeader);
    const note=window.prompt("변경 사유를 입력하세요.", "Grid EDM edit");
    if(note===null)return;
    setBaseSaveBusy("grid");
    setBaseVersionMsg("저장 중...");
    const candidates=[API+"/base-file/save",API+"/base-file-save"];
    let saved=false;
    let savedResult=null;
    let lastErr=null;
    let lastUrl="";
    let confirmMissingStepDesc=false;
    while(!saved){
      lastErr=null;
      const payload=JSON.stringify({
        file:selBaseFile,
        mode:"replace",
        csv_text:csvText,
        delimiter:saveDelimiter,
        include_header:includeHeader,
        note,
        access_scope:accessScope,
        confirm_missing_step_desc:confirmMissingStepDesc,
      });
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
      if(saved)break;

      const warningDetail=lastErr?.body?.detail;
      if(lastErr?.status===409&&warningDetail?.error_code==="ppid_knob_step_desc_not_found"){
        const missing=Array.isArray(warningDetail.missing_step_desc)?warningDetail.missing_step_desc:[];
        const shown=missing.slice(0,10).map(item=>{
          const rows=Array.isArray(item?.rows)?item.rows:[];
          const rowLabel=rows.length?` (CSV ${rows.join(", ")}행)`:"";
          return `- ${item?.value||"(빈 값)"}${rowLabel}`;
        });
        if(missing.length>10)shown.push(`- 외 ${missing.length-10}개`);
        const proceed=window.confirm([
          "⚠ Warning",
          "",
          "Vehicle_matching.csv의 step_desc에 없는 값이 있습니다.",
          ...shown,
          "",
          "해당하는 step이 없는데 저장할까요?",
          "",
          "확인 = 그래도 저장 / 취소 = 편집으로 돌아가기",
        ].join("\n"));
        if(proceed){
          confirmMissingStepDesc=true;
          continue;
        }
        setBaseVersionMsg("저장 취소 · Vehicle_matching.csv에 없는 step_desc를 확인하세요.");
        setBaseSaveBusy("");
        return;
      }
      break;
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
      setBaseSaveBusy("");
      return;
    }
    try{
      const reloadState={full:true,page:0};
      setIsBaseEditing(false);
      setBaseEditContextMenu(null);
      if(savedResult?.s3_sync?.status)setBaseVersionMsg(`저장 완료 · s3 ${savedResult.s3_sync.status}`);
      loadBaseVersions(selBaseFile);
      loadBaseFileView(selBaseFile,reloadState);
      if(onBaseFileChanged)onBaseFileChanged({file:selBaseFile,action:"save"});
    }catch(e){setError(e?.message||"저장 처리 중 오류");}
    finally{setBaseSaveBusy("");}
  };

  const toggleCol=(col)=>{
    const parsed=splitDisplaySql(sql);
    const next=parsed.selectedColumns.includes(col)
      ? parsed.selectedColumns.filter(c=>c!==col)
      : [...parsed.selectedColumns,col];
    const nextSql=buildDisplaySql(next,parsed.whereSql,parsed.sortSpec);
    setSql(nextSql);
    setSelectedCols(next);
  };

  const insertColToSql=(col)=>{
    const parsed=splitDisplaySql(sql);
    const clause=displaySqlIdent(col)+" == ''";
    const nextWhere=parsed.whereSql?parsed.whereSql+" & ("+clause+")":clause;
    const nextSql=buildDisplaySql(parsed.selectedColumns,nextWhere,parsed.sortSpec);
    setSql(nextSql);
    setSelectedCols(parsed.selectedColumns);
    setTab("data");
  };

  const downloadCsv=()=>{
    const maxRows=Math.max(1,Math.min(Number(fbSettings.max_csv_download_max_rows||500000),Number(fbSettings.csv_download_max_rows||500000)||500000));
    const maxBytes=Math.max(1,Math.min(Number(fbSettings.max_csv_download_max_bytes||100000000),Number(fbSettings.csv_download_max_bytes||100000000)||100000000));
    let url=API+"/download-csv?username="+(user?.username||"anon")+"&max_rows="+encodeURIComponent(String(maxRows))+"&max_bytes="+encodeURIComponent(String(maxBytes))+"&sql="+encodeURIComponent(sql);
    if(selectedCols.length)url+="&select_cols="+encodeURIComponent(selectedCols.join(","));
    const agg=cleanAggregateSpec(aggregateSpec);
    if(agg){
      url+="&agg_func="+encodeURIComponent(agg.function);
      url+="&agg_column="+encodeURIComponent(agg.column);
      url+="&agg_group_by="+encodeURIComponent(agg.group_by.join(","));
    }
    const activeSort=cleanSortSpec(sortSpec);
    if(activeSort){
      url+="&sort_column="+encodeURIComponent(activeSort.column);
      url+="&sort_direction="+encodeURIComponent(activeSort.direction);
      url+="&sort_nulls="+encodeURIComponent(activeSort.nulls);
    }
    if(mode==="base"){
      url+="&file="+encodeURIComponent(selBaseFile);
      if(accessScope)url+="&access_scope="+encodeURIComponent(accessScope);
    }
    else if(mode==="rootpq")url+="&file="+encodeURIComponent(selRootPq);
    else url+="&root="+encodeURIComponent(selRoot)+"&product="+encodeURIComponent(selProd);
    dl(url).catch(e=>toast.error(e.message||"다운로드 실패"));
  };


  // 컬럼 목록은 data/colSearch 변경 때만 재계산 (1초 ticker 등 무관 렌더에서 재filter 방지)
  const allCols=useMemo(()=>data?.all_columns||data?.columns||[],[data]);
  const filteredCols=useMemo(()=>colSearch?allCols.filter(c=>c.toLowerCase().includes(colSearch.toLowerCase())):allCols,[allCols,colSearch]);
  const displayCols=(data?.all_columns_truncated&&colSearch.trim())?remoteCols:filteredCols;
  const fbActiveRule=formToRule(fbRuleForm);
  const fbActiveRuleSections=ruleSummarySections(fbActiveRule);
  const fbDraftRule=fbSettingsLlmDraft?.draft||fbSettingsLlmDraft?.csv_rules?.[fbSelectedFile]||null;
  const fbDraftRuleSections=fbDraftRule?ruleSummarySections(fbDraftRule):[];
  const canEnterBaseEdit = canEditCurrentBase && baseFileComplete;
  const baseEditingTabs = isBaseEditingMode ? ["data"] : ["data","columns"];
  const hasCopiedBaseRows = !!baseEditCopiedRowsRef.current?.length;
  const settingsTabs=[
    ...(canRunS3Ingest?[{k:"items",l:"항목 ("+s3Items.length+")"}]:[]),
    ...(canManageS3Ingest?[{k:"add",l:"+ 추가"}]:[]),
    ...(canRunS3Ingest?[{k:"history",l:"이력"}]:[]),
    ...(canManageS3Ingest?[{k:"aws",l:"AWS 설정"}]:[]),
    {k:"folder",l:"폴더 설정"},
    {k:"file",l:"파일 설정"},
  ];
  const settingsTitle=s3Tab==="folder"?"FileBrowser 폴더 설정":(s3Tab==="file"?"FileBrowser 파일 설정":(s3Tab==="items"||s3Tab==="history"?"S3 동기화 실행/이력":"S3 동기화 설정 — aws s3 cp/sync"));
  const activeQueryMode=!!(String(sql||"").trim() || selectedCols.length || aggregateSpec || data?.selected_cols);
  const effectiveCsvMaxRows=Math.max(1,Math.min(Number(fbSettings.max_csv_download_max_rows||500000),Number(fbSettings.csv_download_max_rows||500000)||500000));
  const effectiveCsvMaxMb=Math.max(1,Math.round(Math.min(Number(fbSettings.max_csv_download_max_bytes||100000000),Number(fbSettings.csv_download_max_bytes||100000000)||100000000)/1000000));
  const activePreviewLimit=Number(data?.preview_row_limit||fbSettings.preview_max_rows||PAGE_SIZE)||PAGE_SIZE;
  const previewStatusLabel=data?.single_file_full_read
    ?"전체 표시"
    :(activeQueryMode?"검색 결과":`예시 ${activePreviewLimit}행`);
  const baseCurrentVersion=baseCurrentProfile?.current_version||"";

  const sidebarText={flex:1,minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"};
  const sidebarMeta={fontSize:11,color:FB_MUTED,flexShrink:0,maxWidth:82,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontFamily:"monospace"};
  const sidebarRowBase={display:"flex",alignItems:"center",gap:6,minWidth:0,overflow:"hidden"};
  const sidebarStack={display:"flex",flexDirection:"column",gap:2,flex:1,minWidth:0,overflow:"hidden"};
  const sidebarMetaLine={display:"flex",alignItems:"center",gap:6,minWidth:0,overflow:"hidden",lineHeight:1.15};
  return(
    <div className="flow-connected-page" style={{display:"flex",height:embedded?"calc(100vh - 190px)":"calc(100vh - 52px)",minHeight:embedded?620:undefined,background:"var(--bg-primary)",color:"var(--text-primary)",border:embedded?"1px solid var(--border)":undefined,borderRadius:embedded?8:undefined,overflow:"hidden"}}>
      {/* Sidebar */}
      <div style={{width:260,minWidth:260,borderRight:"1px solid var(--border)",display:"flex",flexDirection:"column",background:"var(--bg-secondary)",overflow:"hidden"}}>
        <div className="flow-sidebar-header" style={{padding:"12px 16px",borderBottom:"1px solid var(--border)",fontSize:14,fontWeight:700,color:"var(--text-secondary)"}}>
          <span className="flow-sidebar-header-title">{embedded?embeddedTitle:"파일탐색기"}</span>
          <div className="flow-sidebar-header-meta">{scope==="Base"?baseFileCount:(selRoot?products.length:roots.length)} items</div>
        </div>
        {/* Scope switcher (DB / root-level files). Shown only when backend reports 2+ scopes. */}
        {!embedded&&scopes.length>=2&&<div className="filebrowser-scope-switcher" style={{display:"flex",gap:4,padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>
          {scopes.map(s=>{
            const active=scope===s.key;const disabled=s.exists===false;
            return(<span key={s.key} className={"filebrowser-scope-option filebrowser-scope-"+s.key} data-scope={s.key} data-active={active?"1":"0"}
              onClick={()=>{if(disabled)return;setScope(s.key);setBaseDir("");setData(null);setBaseRaw(null);setSelBaseMeta(null);setError("");setSelProd("");setSelRootPq("");setSelBaseFile("");setIsBaseEditing(false);setEditCols([]);setEditRows([]);setEditOriginRows([]);setEditOriginCols([]);setSelectedCols([]);setSortSpec(null);setAggregateSpec(null);}}
              title={s.description+(disabled?"\n(경로 없음 — admin_settings 확인)":"")}
              style={{flex:1,minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",textAlign:"center",padding:"6px 8px",borderRadius:5,fontSize:14,cursor:disabled?"not-allowed":"pointer",fontWeight:active?700:500,
                background:active?"var(--accent-glow)":"var(--bg-hover)",color:disabled?"var(--text-secondary)":(active?"var(--accent)":"var(--text-primary)"),
                opacity:disabled?0.4:1,border:"1px solid "+(active?"var(--accent)":"var(--border)")}}>
              {s.icon} {s.label}
            </span>);
          })}
        </div>}
        {sideLoading?<div style={{padding:20}}><Loading text="DB root 확인 중" size="sm"/></div>:scope==="Base"?<>
          {/* Root-level DB files — legacy scope key remains "Base" for compatibility. */}
          <div style={{flex:1,overflow:"auto",padding:"6px 8px"}}>
            <div style={{fontSize:14,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>
              {embedded?"TEG 기준파일":(baseDir||"운영 파일")} ({baseFileCount}){baseDirLoading&&<span style={{marginLeft:6,fontWeight:400,textTransform:"none"}}>불러오는 중…</span>}
            </div>
            {baseDir&&baseDirTruncated&&<div style={{padding:"6px 12px",fontSize:12,color:"var(--warn)"}}>
              이 폴더의 항목이 너무 많아 일부만 표시합니다 — 하위 폴더로 좁혀서 보세요.
            </div>}
            {baseItems.length===0&&!baseDirLoading&&<div style={{padding:"10px 12px",fontSize:14,color:"var(--text-secondary)"}}>
              {baseDir?"이 폴더에 표시할 항목이 없습니다.":"표시할 ML_TABLE / 매칭 CSV / 제품 YAML / reformatter CSV 가 없습니다."}
            </div>}
            {baseItems.map(f=>{
              const fileKey=f.path||f.name;
              const isSel=selBaseFile===fileKey;
              const kind=(f.kind||"file").toLowerCase();
              const isDir=kind==="dir";
              const isDirUp=kind==="dir_up";
              const extColor=EXT_COLOR[f.ext]||FB_MUTED;
              const icon=isDirUp?"↩":(EXT_ICON[f.ext]||"📁");
              const displayName=baseDir&&!isDirUp?String(fileKey).replace(new RegExp("^"+baseDir.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")+"/"),""):f.name;
              const titlePath=[f.name];
              return(<div key={fileKey} className="filebrowser-base-file" data-file={fileKey} data-ext={f.ext}
                onClick={()=>{
                  if(isDirUp){
                    // 최상위로 튀지 않고 바로 위 폴더로. (예: valve-alerts/pipeline → valve-alerts)
                    setBaseDir(baseDir.includes("/")?baseDir.slice(0,baseDir.lastIndexOf("/")):"");
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
                  setSelectedCols([]);setSortSpec(null);setAggregateSpec(null);setSelBaseMeta(f);loadBaseFileView(fileKey);setIsBaseEditing(false);setError("");setData(null);setBaseRaw(null);setEditCols([]);setEditRows([]);setEditOriginRows([]);setEditOriginCols([]);
                }}
                title={(f.description||titlePath.join(" "))+ (f.role?`\n${f.role}`:"")}
                style={{...sidebarRowBase,alignItems:"flex-start",padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:14,marginBottom:1,
                  background:isSel?"var(--bg-hover)":"transparent",color:isSel?"var(--accent)":"var(--text-primary)"}}>
                {/* v8.7.5: Base 단일 파일도 S3 신호등 표시 (다운로드/업로드 양방향). */}
                {!embedded&&!isDir&&!isDirUp&&lightDot(fileKey)}
                <span style={{flexShrink:0,lineHeight:1.5}}>{icon}</span>
                <span style={sidebarStack}>
                  <span style={sidebarText} title={displayName}>{displayName}</span>
                  <span style={sidebarMetaLine}>
                    {!embedded&&!isDir&&!isDirUp&&lightFreshText(fileKey)}
                    {/* v8.7.7: `db` 소스 태그 제거 — Base 단일 파일은 소스 구분 없이 한 번만 표시. */}
                    {!isDir&&!isDirUp&&<>
                      <span style={{fontSize:11,padding:"1px 4px",borderRadius:3,background:`color-mix(in srgb, ${extColor} 13%, transparent)`,color:extColor,fontWeight:700,fontFamily:"monospace",flexShrink:0}}>{f.ext}</span>
                      <span style={sidebarMeta}>{formatSize(f.size)}</span>
                    </>}
                    {isDir&&<span style={{fontSize:11,padding:"1px 4px",borderRadius:3,background:`color-mix(in srgb, ${extColor} 13%, transparent)`,color:extColor,fontWeight:700,fontFamily:"monospace",flexShrink:0}}>DIR</span>}
                  </span>
                </span>
                {/* DB/root 원본은 read-only. Flow-i가 Files 영역에 등록한 uploads 파일만 삭제 가능. */}
                {isAdmin&&!isDir&&f.source==="uploads"&&<span
                  onClick={(e)=>{e.stopPropagation();deleteBaseFile(f.name);}}
                  title={"Files 등록 파일 삭제 (admin) — "+f.name+" 을 .trash 로 이동"}
                  style={{fontSize:14,lineHeight:1,padding:"1px 5px",borderRadius:3,cursor:"pointer",color:FB_BAD.fg,border:"1px solid var(--danger-line)",background:"transparent",flexShrink:0}}>
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
              <div key={r.name} onClick={()=>{setSelRoot(r.name);setSelectedCols([]);setAggregateSpec(null);}} title={r.description||""} style={{...sidebarRowBase,alignItems:"flex-start",padding:"7px 12px",borderRadius:6,cursor:"pointer",fontSize:14,
                background:selRoot===r.name?"var(--bg-hover)":"transparent",fontWeight:selRoot===r.name?600:400,color:selRoot===r.name?"var(--accent)":"var(--text-primary)"}}>
                {lightDot(r.name)}
                <span style={sidebarStack}>
                  <span style={sidebarText} title={r.name}>{r.display_name||r.canonical||r.name}</span>
                  <span style={sidebarMetaLine}>
                    {lightFreshText(r.name)}
                    {!r.metadata_deferred&&<span style={{...sidebarMeta,maxWidth:60}}>파일 {r.parquet_count}</span>}
                  </span>
                </span>
              </div>);
            })}
          </div>
          {productsLoading&&<div style={{borderTop:"1px solid var(--border)",padding:"10px 12px"}}><Loading text="제품 목록 확인 중" size="sm"/></div>}
          {!productsLoading&&products.length>0&&<div style={{flex:1,overflow:"auto",borderTop:"1px solid var(--border)",padding:"4px 8px"}}>
            <div style={{fontSize:14,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>제품</div>
            {products.map(p=>(
              <div key={p.name} onClick={()=>{setSelectedCols([]);setSortSpec(null);setAggregateSpec(null);setSql("");loadHiveView(selRoot,p.name,"",[],{full:true,page:0,sortOverride:null,aggregateOverride:null});}} style={{...sidebarRowBase,alignItems:"flex-start",padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:14,marginBottom:1,
                background:selProd===p.name?"var(--bg-hover)":"transparent",color:selProd===p.name?"var(--accent)":"var(--text-primary)"}}>
                {/* v8.8.2: 제품별 S3 신호등 — 본인 설정 없으면 상위 DB 에서 상속. */}
                {lightDot(selRoot+"/"+p.name)}
                <span style={sidebarStack}>
                  <span style={sidebarText} title={p.name}>{p.name}</span>
                  <span style={sidebarMetaLine}>
                    {lightFreshText(selRoot+"/"+p.name)}
                    {!!p.latest_date&&<span style={sidebarMeta}>{p.latest_date}</span>}
                  </span>
                </span>
              </div>))}
          </div>}
          {rootPqs.length>0&&<div style={{borderTop:"1px solid var(--border)",padding:"4px 8px",maxHeight:200,overflow:"auto"}}>
            <div style={{fontSize:14,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>루트 Parquet</div>
            {rootPqs.map(f=>(
              <div key={f.name} onClick={()=>{setSelectedCols([]);setSortSpec(null);setAggregateSpec(null);loadRootPqView(f.name,"",[],{sortOverride:null,aggregateOverride:null});}} style={{...sidebarRowBase,alignItems:"flex-start",padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:14,marginBottom:1,
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
          <FileBrowserSqlAutocomplete value={sql} onChange={setSqlFromInput} onExecute={applySql}
            mode={mode} root={selRoot} product={selProd} file={mode==="base"?selBaseFile:selRootPq} accessScope={accessScope}
            columns={data?.all_columns||data?.columns||[]} disabled={mode==="base"&&isBaseEditing}/>
          <button onClick={applySql} disabled={mode==="base"&&isBaseEditing}
            style={{padding:"6px 14px",borderRadius:5,border:"none",background:mode==="base"&&isBaseEditing?"var(--border)": "var(--accent)",color:mode==="base"&&isBaseEditing?"var(--text-secondary)":"#fff",fontSize:14,fontWeight:600,cursor:mode==="base"&&isBaseEditing?"default":"pointer"}}>실행</button>
          {data&&!(mode==="base"&&isBaseEditing)&&<button onClick={()=>setShowAggregateBuilder(v=>!v)} title="그룹 기준과 집계 함수를 선택합니다." style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--border)",background:showAggregateBuilder?"var(--accent-glow)":"transparent",color:showAggregateBuilder?"var(--accent)":"var(--text-secondary)",fontSize:13,fontWeight:700,cursor:"pointer",whiteSpace:"nowrap"}}>피벗/집계</button>}
          {data&&!(mode==="base"&&isBaseEditing)&&<button onClick={downloadCsv} title={`표시는 ${PAGE_SIZE}행, CSV는 서버 허용 한도까지 다운로드합니다.`} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,fontWeight:600,cursor:"pointer"}}>CSV</button>}
          {data&&!(mode==="base"&&isBaseEditing)&&<span style={{fontSize:12,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>CSV 최대 {effectiveCsvMaxRows.toLocaleString()}행/{effectiveCsvMaxMb.toLocaleString()}MB</span>}
        </div>
        {showAggregateBuilder&&data&&!(mode==="base"&&isBaseEditing)&&<div style={{padding:"9px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",alignItems:"end",gap:8,flexWrap:"wrap"}}>
          <label style={{display:"flex",flexDirection:"column",gap:4,minWidth:230,fontSize:12,color:"var(--text-secondary)",fontWeight:700}}>
            그룹 기준 (쉼표로 구분)
            <input value={aggregateGroupByText} onChange={e=>setAggregateGroupByText(e.target.value)} placeholder="root_lot_id, wafer_id" style={{padding:"6px 8px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace"}}/>
          </label>
          <label style={{display:"flex",flexDirection:"column",gap:4,minWidth:145,fontSize:12,color:"var(--text-secondary)",fontWeight:700}}>
            집계 함수
            <select value={aggregateFunction} onChange={e=>setAggregateFunction(e.target.value)} style={{padding:"6px 8px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13}}>
              <option value="latest">최신값 (latest)</option><option value="avg">평균 (avg)</option><option value="sum">합계 (sum)</option><option value="min">최솟값 (min)</option><option value="max">최댓값 (max)</option><option value="median">중앙값 (median)</option><option value="count">건수 (count)</option>
            </select>
          </label>
          <label style={{display:"flex",flexDirection:"column",gap:4,minWidth:190,fontSize:12,color:"var(--text-secondary)",fontWeight:700}}>
            대상 컬럼 {aggregateFunction==="count"?"(비우면 행 수)":""}
            <select value={aggregateColumn} onChange={e=>setAggregateColumn(e.target.value)} style={{padding:"6px 8px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace"}}>
              <option value="">{aggregateFunction==="count"?"전체 행":"컬럼 선택"}</option>
              {currentColumns().map(c=><option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <button onClick={applyAggregateBuilder} style={{padding:"7px 12px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:13,fontWeight:800,cursor:"pointer"}}>집계 적용</button>
          <button onClick={applyLatestWaferPreset} title="root_lot_id와 wafer_id별 tkout_time의 최댓값을 반환합니다." style={{padding:"7px 10px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:12,fontWeight:700,cursor:"pointer"}}>Root Lot + Wafer별 최신 tkout_time</button>
          <button onClick={()=>{setAggregateSpec(null);applySql(sql,selectedCols,sortSpec,null);}} style={{padding:"7px 10px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>집계 해제</button>
          <span style={{fontSize:11,color:"var(--text-secondary)"}}>latest는 그룹별 대상 컬럼의 가장 늦은 값만 반환합니다.</span>
        </div>}
        {aggregateSpec&&<div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
          <span style={{fontSize:13,color:"var(--text-secondary)",fontWeight:700,flexShrink:0}}>AGG:</span>
          <span style={{fontSize:13,color:"var(--text-primary)",fontFamily:"monospace",padding:"2px 7px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>{aggregateLabel(aggregateSpec)}</span>
          <button onClick={()=>{setAggregateSpec(null);applySql(sql,selectedCols,sortSpec,null);}} style={{padding:"3px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>해제</button>
        </div>}
        {/* SQL Guide / Execution History */}
        <div style={{padding:"0 16px"}}>
          <div style={{display:"flex",alignItems:"center",gap:24}}>
            <div onClick={()=>{const next=!showGuide;setShowGuide(next);if(next)setShowSqlHistory(false);}} style={{fontSize:13,color:"var(--accent)",cursor:"pointer",padding:"3px 0"}}>
              {showGuide?"▼":"▶"} SQL 가이드(예시)</div>
            <div onClick={()=>{const next=!showSqlHistory;setShowSqlHistory(next);if(next)setShowGuide(false);}} style={{fontSize:13,color:"var(--accent)",cursor:"pointer",padding:"3px 0"}}>
              {showSqlHistory?"▼":"▶"} SQL 이력 ({sqlHistory.length.toLocaleString()}/500)</div>
          </div>
          <div style={{display:(showGuide||showSqlHistory)?"grid":"none",gridTemplateColumns:showGuide&&showSqlHistory?"minmax(0,1fr) minmax(0,1fr)":"minmax(0,1fr)",gap:8,marginBottom:8}}>
          {showGuide&&<div style={{background:"var(--bg-card)",borderRadius:6,padding:"7px 10px",border:"1px solid var(--border)",fontSize:12,fontFamily:"monospace",lineHeight:1.6,color:"var(--text-secondary)",minWidth:0}}>
            <div>SELECT lot_id, wafer_id WHERE root_lot_id = 'A1000' <span style={{color:"var(--accent)"}}>— 표시 열 + 조건</span></div>
            <div>SELECT lot_id, wafer_id WHERE item_id = 'IOFF' ORDER BY value DESC <span style={{color:"var(--accent)"}}>— 표시 열 + 조건 + 정렬</span></div>
            <div>root_lot_id = 'A1000' <span style={{color:"var(--accent)"}}>— 조건만</span></div>
            <div>lot_id LIKE '%A1000%' <span style={{color:"var(--accent)"}}>— 포함</span></div>
            <div>step_id NOT LIKE '%TEST%' <span style={{color:"var(--accent)"}}>— 포함하지 않음</span></div>
            <div>wafer_id = 3 AND item_id = 'IOFF' <span style={{color:"var(--accent)"}}>— AND</span></div>
            <div>item_id IN ('IOFF', 'ION') <span style={{color:"var(--accent)"}}>— IN 리스트</span></div>
            <div>value BETWEEN 0.1 AND 0.9 <span style={{color:"var(--accent)"}}>— 범위</span></div>
            <div>CAST(value AS DOUBLE) &gt;= 10 <span style={{color:"var(--accent)"}}>— 문자열 숫자 비교</span></div>
            <div>CAST(tkout_time AS TIMESTAMP) &gt;= '2024-04-21' <span style={{color:"var(--accent)"}}>— 문자열 시간 비교</span></div>
            <div>tkout_time IS NOT NULL <span style={{color:"var(--accent)"}}>— NOT NULL</span></div>
            <div style={{color:"var(--accent)",marginTop:4}}>팁: 컬럼 탭에서 컬럼명 클릭 → SELECT 토글, 실행 → 조회 적용, + WHERE → 조건 템플릿 삽입</div>
          </div>}
          {showSqlHistory&&<div style={{background:"var(--bg-card)",borderRadius:6,padding:"7px 10px",border:"1px solid var(--border)",fontSize:12,color:"var(--text-secondary)",minWidth:0,maxHeight:280,overflow:"auto"}}>
            {sqlHistoryLoading&&<div style={{padding:"8px 0"}}>SQL 이력을 불러오는 중...</div>}
            {!sqlHistoryLoading&&sqlHistoryError&&<div style={{padding:"8px 0",color:FB_BAD.fg}}>{sqlHistoryError}</div>}
            {!sqlHistoryLoading&&!sqlHistoryError&&sqlHistory.length===0&&<div style={{padding:"8px 0"}}>{(mode==="hive"&&selRoot&&selProd)||(mode==="rootpq"&&selRootPq)||(mode==="base"&&selBaseFile)?"이 DB/파일에서 실행한 SQL 이력이 없습니다.":"먼저 DB나 파일을 선택하세요."}</div>}
            {!sqlHistoryLoading&&!sqlHistoryError&&sqlHistory.map(h=>{
              const at=h.timestamp?new Date(h.timestamp).toLocaleString():"-";
              const detail=`${h.sql||"-"}${!h.ok&&h.error?` · ${h.error}`:""}`;
              return <div key={h.history_id||`${h.timestamp}-${h.username}`} style={{padding:"4px 0",borderBottom:"1px solid var(--border)",display:"grid",gap:2,minWidth:0}}>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,minWidth:0,whiteSpace:"nowrap",fontSize:11}}>
                  <span style={{display:"inline-flex",alignItems:"center",gap:7,minWidth:0}}>
                    <span style={{fontWeight:900,color:h.ok?FB_OK.fg:FB_BAD.fg,flexShrink:0}}>{h.ok?"성공":"실패"}</span>
                    <span style={{fontFamily:"monospace",fontWeight:800,color:"var(--accent)",overflow:"hidden",textOverflow:"ellipsis"}} title="SQL 이력 고유키">{h.history_id||"-"}</span>
                    <span style={{fontWeight:900,color:"var(--text-secondary)",flexShrink:0}} title="이 고유키로 다시 실행된 횟수">🔄 {Number(h.reuse_count||0).toLocaleString()}</span>
                    {mode==="hive"&&h.product&&<span style={{padding:"1px 5px",borderRadius:4,border:"1px solid var(--border)",color:"var(--text-secondary)",fontFamily:"monospace",flexShrink:0}} title="최초 실행 제품">{h.product}</span>}
                  </span>
                  <span style={{display:"inline-flex",alignItems:"center",justifyContent:"flex-end",gap:7,minWidth:0,marginLeft:"auto",overflow:"hidden"}}>
                    <span style={{color:"var(--text-primary)",fontWeight:700,flexShrink:0}}>{h.username||"-"}</span>
                    <span style={{overflow:"hidden",textOverflow:"ellipsis"}} title={at}>{at}</span>
                    {Number.isFinite(h.duration_ms)&&<span style={{flexShrink:0}}>{h.duration_ms.toLocaleString()}ms</span>}
                    {h.ok&&Number.isFinite(h.rows_returned)&&<span style={{flexShrink:0}}>{h.rows_returned.toLocaleString()}행</span>}
                  </span>
                </div>
                <div style={{display:"flex",alignItems:"center",gap:7,minWidth:0,fontSize:11,whiteSpace:"nowrap"}}>
                  <code style={{overflow:"hidden",textOverflow:"ellipsis",color:h.ok?"var(--text-primary)":FB_BAD.fg,fontFamily:"monospace"}} title={detail}>{detail}</code>
                  <button type="button" onClick={()=>copySqlHistoryLink(h)} style={{marginLeft:"auto",flexShrink:0,border:"1px solid var(--border)",borderRadius:4,background:"var(--bg-secondary)",color:"var(--text-primary)",padding:"2px 6px",fontSize:10,cursor:"pointer"}}>공유 링크</button>
                </div>
              </div>;
            })}
          </div>}
          </div>
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
              <span style={{fontSize:12,color:"var(--text-secondary)",maxWidth:260,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={selBaseFile}>
                {selBaseFile}
              </span>
              {baseCurrentVersion&&<span style={{fontSize:12,fontWeight:800,color:"var(--accent)",background:"var(--accent-glow)",border:"1px solid var(--accent)",borderRadius:4,padding:"2px 6px",fontFamily:"monospace"}}>
                현재 {baseCurrentVersion}
              </span>}
              <span style={{fontSize:12,color:baseVersioned?"var(--accent)":"var(--text-secondary)",fontFamily:"monospace"}}>
                {baseVersioned?`versioned · ${baseVersions.length}/${baseVersionCap}`:"preview only"}
              </span>
              {(baseVersionLoading||baseVersionPreviewLoading)&&<span style={{fontSize:12,color:"var(--text-secondary)"}}>loading...</span>}
              {baseSaveBusy&&<Loading text="저장 중..." size="sm" />}
              {baseVersionMsg&&<span style={{fontSize:12,color:baseVersionMsg.includes("완료")?FB_OK.fg:(baseVersionMsg.includes("중")?"var(--text-secondary)":FB_BAD.fg)}}>{baseVersionMsg}</span>}
              <input value={baseVersionFilter} onChange={e=>setBaseVersionFilter(e.target.value)} placeholder="filter actor/action/note" style={{marginLeft:"auto",padding:"3px 7px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,width:170}}/>
              <button onClick={()=>loadBaseVersions(selBaseFile)} style={{padding:"3px 8px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>새로고침</button>
            </div>
            {baseCurrentProfile&&<div style={{display:"flex",gap:10,flexWrap:"wrap",marginBottom:6,fontSize:12,color:"var(--text-secondary)",fontFamily:"monospace"}}>
              <span>{baseCurrentProfile.rows??"-"}행 / {baseCurrentProfile.columns??"-"}열</span>
              <span>size={formatSize(baseCurrentProfile.size)}</span>
              <span>modified={(baseCurrentProfile.modified_at||"").replace("T"," ").slice(0,16)||"-"}</span>
            </div>}
            {baseVersioned&&baseVersions.length===0&&<div style={{fontSize:12,color:"var(--text-secondary)"}}>아직 저장된 이전 버전이 없습니다. 다음 저장부터 수정 전 snapshot 이 남습니다.</div>}
            {baseVersions.length>0&&<div style={{display:"flex",flexDirection:"column",gap:2,maxHeight:150,overflow:"auto"}}>
              {baseVersions.filter(v=>{
                const q=baseVersionFilter.trim().toLowerCase();
                if(!q)return true;
                return [v.version,v.actor,v.action,v.note,v.created_at].some(x=>String(x||"").toLowerCase().includes(q));
              }).map(v=><div key={v.version} style={{display:"grid",gridTemplateColumns:"58px minmax(120px,0.9fr) minmax(130px,1fr) 96px 72px 136px 82px 58px 70px",gap:8,alignItems:"center",fontSize:12,padding:"2px 6px",border:"1px solid var(--border)",borderRadius:5,background:"var(--bg-primary)"}}>
                <span style={{fontFamily:"monospace",fontWeight:900,color:String(v.version||"").startsWith("legacy_")?"#a855f7":"var(--accent)"}} title={v.storage_version||v.version}>{String(v.version||"-").startsWith("legacy_")?"legacy":v.version}</span>
                <span style={{color:"var(--text-primary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={`${v.version} · ${v.action||"edit"}`}>{v.note||v.action||"edit"}</span>
                <span style={{color:"#eab308",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontFamily:"monospace"}} title={JSON.stringify(v.change_summary||{})}>{versionChangeLabel(v.change_summary)}</span>
                <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{v.rows??"-"}행 / {v.columns??"-"}열</span>
                <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{formatSize(v.size)}</span>
                <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{(v.created_at||"").replace("T"," ").slice(0,16)||"-"}</span>
                <span style={{color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{v.actor||"-"}</span>
                <button onClick={()=>previewBaseVersion(v.version)} style={{padding:"1px 7px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>보기</button>
                <button onClick={()=>rollbackBaseVersion(v.version)} disabled={!isFileBrowserAdmin} style={{padding:"1px 7px",borderRadius:4,border:`1px solid ${FB_BAD.fg}`,background:"transparent",color:isFileBrowserAdmin?FB_BAD.fg:"var(--text-secondary)",fontSize:12,cursor:isFileBrowserAdmin?"pointer":"not-allowed"}}>롤백</button>
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
                <button onClick={saveRawBaseFile} disabled={!!baseSaveBusy} style={{padding:"4px 10px",borderRadius:5,border:"none",background:baseSaveBusy?"var(--text-secondary)":"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:baseSaveBusy?"wait":"pointer",opacity:baseSaveBusy?0.75:1}}>{baseSaveBusy==="raw"?"저장 중...":"저장"}</button>
                <button onClick={()=>{setRawEditing(false);setRawEditText("");}} disabled={!!baseSaveBusy} style={{padding:"4px 10px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:baseSaveBusy?"wait":"pointer",opacity:baseSaveBusy?0.5:1}}>취소</button>
              </>}
            </div>
            {rawEditing?<textarea value={rawEditText} onChange={e=>setRawEditText(e.target.value)} spellCheck={false} disabled={!!baseSaveBusy}
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
                if(name.endsWith("_FAB")||name.endsWith(".RAWDATA_DB_FAB")){label="FAB";bg="var(--info-50)";fg="var(--info)";}
                else if(name.endsWith("_INLINE")){label="INLINE";bg="var(--ok-50)";fg="var(--ok)";}
                else if(name.endsWith("_ET")){label="ET";bg="var(--pink-50)";fg="var(--pink)";}
                if(!label) return null;
                return <span title={`datalake 소스: ${label} (${selRoot})`}
                  style={{fontSize:14,fontWeight:700,fontFamily:"monospace",padding:"3px 10px",borderRadius:4,background:bg,color:fg,letterSpacing:0.5}}>{label}</span>;
              })()}
              <span style={{fontSize:14,fontWeight:600,flex:"1 1 220px",minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={selProd||selRootPq||selBaseFile}>{selProd||selRootPq||selBaseFile}</span>
                <span style={{fontSize:14,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"4px 10px",borderRadius:6,flexShrink:0}}>
                  {data.meta_only
                    ?<>스키마만 · {data.total_cols}열{data.row_count_unknown?<> · 행수 미계산</>:data.total_rows?<> · {data.total_rows.toLocaleString()}행</>:null}{data.all_columns_truncated?<> · 컬럼 일부 표시</>:null}{sampleLoading&&<span style={{color:"var(--accent)",fontWeight:700}}> · 샘플 행 불러오는 중…</span>}</>
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
                    <button onClick={addBaseEditColumn} style={{padding:"5px 10px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:13,fontWeight:700,cursor:"pointer"}}>열 추가</button>
                    <button onClick={()=>deleteBaseEditColumn(selectedEditCell.c)} disabled={editCols.length<=1}
                      style={{padding:"5px 10px",borderRadius:5,border:`1px solid ${FB_BAD.fg}`,background:"transparent",color:editCols.length>1?FB_BAD.fg:"var(--text-secondary)",fontSize:13,fontWeight:700,cursor:editCols.length>1?"pointer":"default",opacity:editCols.length>1?1:0.45}}>활성 열 삭제</button>
                    <button onClick={saveBaseEdit} disabled={!!baseSaveBusy} style={{padding:"5px 12px",borderRadius:5,border:"none",background:baseSaveBusy?"var(--text-secondary)":"var(--accent)",color:"#fff",fontSize:14,fontWeight:600,cursor:baseSaveBusy?"wait":"pointer",opacity:baseSaveBusy?0.75:1}}>{baseSaveBusy==="grid"?"저장 중...":"저장"}</button>
                    <button onClick={restoreBaseEdit} disabled={!!baseSaveBusy} style={{padding:"5px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:baseSaveBusy?"wait":"pointer",opacity:baseSaveBusy?0.5:1}}>원본복원</button>
                    <button onClick={cancelBaseEdit} disabled={!!baseSaveBusy} style={{padding:"5px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:baseSaveBusy?"wait":"pointer",opacity:baseSaveBusy?0.5:1}}>취소</button>
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
                </div>
              </div>
              {/* Tabs: Data + Columns */}
              <div style={{display:"flex",gap:0,borderBottom:"1px solid var(--border)",marginBottom:12,alignItems:"center"}}>
                {baseEditingTabs.map(t=>(<div key={t} onClick={()=>setTab(t)} style={{padding:"8px 16px",fontSize:14,cursor:"pointer",fontWeight:tab===t?600:400,
                  borderBottom:tab===t?"2px solid var(--accent)":"2px solid transparent",color:tab===t?"var(--text-primary)":"var(--text-secondary)"}}>
                  {t==="data"?"데이터 ("+data.showing+")":"컬럼 ("+allCols.length+")"}</div>))}
                {tab==="data"&&!findOpen&&<button onClick={openFind} title="표에서 값 찾기 (Ctrl+F)"
                  style={{marginLeft:"auto",padding:"4px 11px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:13,fontWeight:600,cursor:"pointer"}}>값 찾기</button>}
              </div>
              {tab==="data"&&findOpen&&<div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap",margin:"0 0 10px",padding:"7px 10px",borderRadius:6,border:"1px solid var(--accent)",background:"var(--accent-glow)"}}>
                <span style={{fontSize:13,fontWeight:700,color:"var(--accent)"}}>값 찾기</span>
                <input ref={findInputRef} value={findQuery} onChange={e=>setFindQuery(e.target.value)} onKeyDown={onFindKeyDown}
                  placeholder="셀 값 일부 입력 · Enter 다음 / Shift+Enter 이전" spellCheck={false}
                  style={{flex:"1 1 240px",minWidth:180,padding:"5px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace",outline:"none"}}/>
                <span style={{fontSize:13,fontFamily:"monospace",color:findHitCount?"var(--text-primary)":FB_MUTED,minWidth:76,textAlign:"center"}}>
                  {findNeedle?(findHitCount?`${findPos+1} / ${findHitCount}${findHitCount>=FIND_MAX_HITS?"+":""}`:"결과 없음"):"—"}
                </span>
                <button onClick={()=>stepFind(-1)} disabled={!findHitCount} title="이전 (Shift+Enter)"
                  style={{padding:"4px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,cursor:findHitCount?"pointer":"default",opacity:findHitCount?1:0.45}}>‹</button>
                <button onClick={()=>stepFind(1)} disabled={!findHitCount} title="다음 (Enter)"
                  style={{padding:"4px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,cursor:findHitCount?"pointer":"default",opacity:findHitCount?1:0.45}}>›</button>
                <label style={{fontSize:13,color:"var(--text-secondary)",display:"inline-flex",gap:5,alignItems:"center",cursor:"pointer"}}>
                  <input type="checkbox" checked={findExact} onChange={e=>setFindExact(e.target.checked)} style={{width:14,height:14}}/> 정확히 일치</label>
                <button onClick={closeFind} title="닫기 (Esc)"
                  style={{padding:"4px 10px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:13,cursor:"pointer"}}>닫기</button>
                {!isBaseEditingMode&&data.total_rows>(data.showing||0)&&<span style={{flexBasis:"100%",fontSize:12,color:"var(--text-secondary)"}}>
                  지금 불러온 {(data.showing||0).toLocaleString()}행 안에서만 찾습니다. 전체 {Number(data.total_rows||0).toLocaleString()}행에서 찾으려면 SQL 조건으로 조회하세요.
                </span>}
              </div>}
              {tab==="data"&&isBaseEditingMode&&<>
                <div style={{margin:"0 0 8px",fontSize:12,color:"var(--text-secondary)"}}>
                  셀 기준 붙여넣기: 입력 중인 셀을 시작점으로 반영되며, 첫 행이 헤더면 열 이름으로 반영됩니다.
                  탭·줄바꿈이 없는 단일 값은 커서 위치에 그대로 삽입됩니다.
                  <br/>셀 이동: ↑↓ · Enter(위로는 Shift+Enter) · Tab / Shift+Tab. ←→ 는 커서가 셀 텍스트 끝에 닿으면 옆 셀로 넘어갑니다.
                </div>
                <div ref={baseEditGridRef} style={baseEditWrap} onPaste={onBasePaste} onScroll={editVirt.onScroll} data-base-edit-grid="1">
                  <table style={baseEditTable}>
                    <thead><tr>
                      <th style={baseEditCornerCell}>#</th>
                      {editCols.map((c,i)=>{
                        const isColActive = isBaseEditingMode&&selectedEditCell.c===i;
                        return <th key={i}
                          onClick={()=>setSelectedEditCell(cur=>({r:cur.r||0,c:i}))}
                          style={{...baseEditHeaderInput,background:isColActive? "#dbeafe":"var(--bg-tertiary)",padding:0}}>
                          <input value={String(c||"")}
                            onChange={(e)=>patchBaseHeader(i,e.target.value)}
                            onFocus={()=>setSelectedEditCell(cur=>({r:cur.r||0,c:i}))}
                            onBlur={finalizeBaseHeaders}
                            onKeyDown={onBaseEditHeaderKeyDown}
                            data-base-edit-header="1"
                            data-col={i}
                            style={{width:"100%",height:34,boxSizing:"border-box",border:"none",outline:isColActive?"2px solid var(--accent)":"none",outlineOffset:-2,background:"transparent",color:"var(--text-primary)",fontSize:13,fontWeight:800,fontFamily:"inherit",padding:"0 10px"}}
                            title={String(c||"")}/>
                        </th>;
                      })}
                    </tr></thead>
                    <tbody>
                      {editVirt.padTop>0&&<tr aria-hidden="true" style={{height:editVirt.padTop}}><td colSpan={editColCount+1} style={SPACER_CELL}/></tr>}
                      {editRowCount?editRows.slice(editVirt.start,editVirt.end).map((row,i)=>{
                        const ri=editVirt.start+i;
                        return <BaseEditRow key={ri} row={row} ri={ri} colCount={editColCount}
                          activeCol={selectedEditCell.c} isRowActive={ri===selectedEditCell.r}
                          findNeedle={findNeedle} findExact={findExact}
                          findCurCol={ri===findCurRow?findCurCol:-1}
                          onCellChange={onBaseCellChange} onCellFocus={onBaseCellFocus}
                          onCellKeyDown={onBaseEditCellKeyDown} onCellPaste={onBasePaste}
                          onCellClick={onBaseCellClick} onDeleteRow={onBaseRowDeleteClick}
                          onRowMenu={onBaseRowContextMenu}/>;
                      }):<tr><td colSpan={Math.max(editColCount+1,1)} style={{padding:"20px",textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>
                        데이터가 비어 있습니다. Ctrl+V로 붙여넣기 하거나 직접 입력해 저장하세요.
                      </td></tr>}
                      {editVirt.padBottom>0&&<tr aria-hidden="true" style={{height:editVirt.padBottom}}><td colSpan={editColCount+1} style={SPACER_CELL}/></tr>}
                    </tbody>
                  </table>
                </div>
                {baseEditContextMenu&&<div
                  onClick={(e)=>e.stopPropagation()}
                  onContextMenu={(e)=>e.preventDefault()}
                  style={{position:"fixed",left:baseEditContextMenu.x,top:baseEditContextMenu.y,zIndex:10000,minWidth:188,padding:6,border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)",boxShadow:"0 12px 28px rgba(15,23,42,0.18)",display:"flex",flexDirection:"column",gap:4}}>
                  <button type="button" onClick={()=>insertBaseEditRowBelow(baseEditContextMenu.r)}
                    style={{padding:"7px 10px",borderRadius:5,border:"none",background:"transparent",color:"var(--text-primary)",fontSize:13,fontWeight:700,textAlign:"left",cursor:"pointer"}}>
                    아래에 행 추가
                  </button>
                  <button type="button" onClick={()=>pasteCopiedBaseRowsBelow(baseEditContextMenu.r)} disabled={!hasCopiedBaseRows}
                    style={{padding:"7px 10px",borderRadius:5,border:"none",background:"transparent",color:hasCopiedBaseRows?"var(--text-primary)":"var(--text-secondary)",opacity:hasCopiedBaseRows?1:0.45,fontSize:13,fontWeight:700,textAlign:"left",cursor:hasCopiedBaseRows?"pointer":"default"}}>
                    아래에 붙여넣기
                  </button>
                  <button type="button" onClick={()=>copyBaseEditRow(baseEditContextMenu.r)}
                    style={{padding:"7px 10px",borderRadius:5,border:"none",background:"transparent",color:"var(--text-primary)",fontSize:13,fontWeight:700,textAlign:"left",cursor:"pointer"}}>
                    행 복사
                  </button>
                  <button type="button" onClick={()=>deleteBaseEditRow(baseEditContextMenu.r)}
                    style={{padding:"7px 10px",borderRadius:5,border:"none",background:"transparent",color:FB_BAD.fg,fontSize:13,fontWeight:700,textAlign:"left",cursor:"pointer"}}>
                    행 삭제
                  </button>
                </div>}
              </>}
              {tab==="data"&&!isBaseEditingMode&&(()=>{
                const readCols=data.showing_cols||data.columns||[];
                return <div ref={baseReadGridRef} style={baseReadWrap} onScroll={readVirt.onScroll}>
                  <table style={baseEditTable}>
                    <thead><tr>
                      <th style={baseReadIndexCell}>#</th>
                      {readCols.map((c,i)=><th key={i} style={baseEditHeaderReadCell}>{c}</th>)}</tr></thead>
                    <tbody>
                      {readVirt.padTop>0&&<tr aria-hidden="true" style={{height:readVirt.padTop}}><td colSpan={readCols.length+1} style={SPACER_CELL}/></tr>}
                      {(data.data||[]).slice(readVirt.start,readVirt.end).map((row,i)=>{
                        const ri=readVirt.start+i;
                        const rowIsCur=ri===findCurRow;
                        return <tr key={ri} data-vrow={ri}><td style={baseReadIndexBodyCell}>{ri+1}</td>
                          {readCols.map((c,ci)=>{
                            const text=row[c]==null?"":String(row[c]);
                            const isCur=rowIsCur&&ci===findCurCol;
                            const isHit=isCur||cellMatchesFind(text,findNeedle,findExact);
                            return <td key={ci} style={isCur?baseReadCellFindCur:isHit?baseReadCellFind:baseReadCell} title={text}>
                              {row[c]===null?<span style={{color:FB_MUTED}}>null</span>:text}</td>;
                          })}</tr>;
                      })}
                      {readVirt.padBottom>0&&<tr aria-hidden="true" style={{height:readVirt.padBottom}}><td colSpan={readCols.length+1} style={SPACER_CELL}/></tr>}
                    </tbody>
                  </table></div>;
              })()}
              {tab==="columns"&&!isBaseEditingMode&&<div>
                <div style={{display:"flex",gap:8,marginBottom:8,alignItems:"center"}}>
                  <input value={colSearch} onChange={e=>setColSearch(e.target.value)} placeholder="컬럼 검색..."
                    style={{flex:1,padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"}}/>
                  {selectedCols.length>0&&<span style={{fontSize:14,color:"var(--accent)",fontWeight:600}}>{selectedCols.length}개 선택됨</span>}
                </div>
              <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:8,padding:"4px 0",lineHeight:1.6}}>
                컬럼명 클릭 → SELECT 토글 후 실행으로 조회 | + WHERE → 조건 템플릿 삽입
                {data.all_columns_truncated&&<span style={{color:"var(--accent)"}}> | schema {data.schema_columns_returned}/{data.total_cols}열 표시{remoteColsLoading?" · 검색 중":""}</span>}
              </div>
              <div style={{maxHeight:"calc(100vh - 340px)",overflow:"auto"}}>
                {displayCols.map((c,i)=>{
                  const isSelected=selectedCols.includes(c);
                  return(
                  <div key={i} style={{display:"flex",alignItems:"center",padding:"5px 12px",borderBottom:"1px solid var(--border)",fontSize:14,gap:8}}>
                    {/* Checkbox mirrors the SELECT clause for keyboard-friendly toggling. */}
                    <input type="checkbox" checked={isSelected} onChange={()=>toggleCol(c)} title="실행을 눌러 조회에 적용됩니다."
                      style={{width:14,height:14,accentColor:"var(--accent)",cursor:"pointer",flexShrink:0}}/>
                    {/* Column name toggles SELECT projection. */}
                    <span onClick={()=>toggleCol(c)} style={{flex:1,cursor:"pointer",fontWeight:isSelected?600:500,color:isSelected?"var(--accent)":"var(--text-primary)"}} title={"SELECT 절에 추가/제거됩니다. 실행을 눌러 조회에 적용됩니다."}>
                      {c}
                    </span>
                    {data.dtypes&&<span style={{fontSize:14,padding:"1px 6px",borderRadius:3,background:"var(--bg-tertiary)",color:"var(--accent)",flexShrink:0}}>{data.dtypes[c]}</span>}
                    <span onClick={()=>insertColToSql(c)} style={{fontSize:14,color:"var(--accent)",cursor:"pointer",padding:"2px 6px",borderRadius:3,background:"var(--accent-glow)",flexShrink:0}} title="WHERE 조건 템플릿 추가">+ WHERE</span>
                  </div>);})}
              </div>
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
              <span style={{fontSize:12,color:"var(--text-secondary)"}}>작성하면 SELECT 포함 SQL을 반영하고 바로 조회합니다.</span>
              <div style={{display:"flex",gap:8}}>
                <button onClick={()=>setAiSqlOpen(false)} style={{padding:"7px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>닫기</button>
                <button onClick={draftAiSql} disabled={aiSqlBusy} style={{padding:"7px 14px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:aiSqlBusy?"wait":"pointer",opacity:aiSqlBusy?0.6:1}}>{aiSqlBusy?"작성 중":"작성"}</button>
              </div>
            </div>
            {aiSqlResult&&<div style={{display:"grid",gap:5,padding:9,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",fontSize:12,fontFamily:"monospace",color:aiSqlResult.ok===false?FB_BAD.fg:"var(--text-secondary)",lineHeight:1.45}}>
              <span>llm={aiSqlResult.llm?.used?"used":(aiSqlResult.llm?.available?"available":"fallback")} · saved=false · draft={aiSqlResult.draft_id||"-"}</span>
              {aiSqlResult.feedback_context_used?<span>feedback: like {aiSqlResult.feedback_context?.positive||0} · dislike {aiSqlResult.feedback_context?.negative||0}</span>:null}
              {cleanAggregateSpec(aiSqlResult.aggregate)?<span>aggregate: {aggregateLabel(aiSqlResult.aggregate)}</span>:null}
              {aiSqlResult.display_sql?<span style={{color:"var(--accent)"}}>display_sql: {aiSqlResult.display_sql}</span>:null}
              {aiSqlResult.where_sql?<span>where_sql: {aiSqlResult.where_sql}</span>:null}
              {aiSqlResult.selected_columns?.length?<span>selected_columns: {aiSqlResult.selected_columns.join(", ")}</span>:null}
              {aiSqlResult.sample_profile?<span>profile: rows {aiSqlResult.sample_profile.rows_sampled||0} · cols {aiSqlResult.sample_profile.columns_scanned||0} · {aiSqlResult.sample_profile.source||"request"}</span>:null}
              {aiSqlResult.resolved_columns?.length?<span>resolved: {aiSqlResult.resolved_columns.join(", ")}</span>:null}
              {aiSqlResult.unknown_column_terms?.length?<span style={{color:FB_BAD.fg}}>unknown: {aiSqlResult.unknown_column_terms.join(", ")}</span>:null}
              {aiSqlResult.resolved_values?.length?<span>values: {aiSqlResult.resolved_values.join(", ")}</span>:null}
              {aiSqlResult.value_terms?.length?<span>value terms: {aiSqlResult.value_terms.join(", ")}</span>:null}
              {aiSqlResult.sql&&<span>sql: {aiSqlResult.sql}</span>}
              {(aiSqlResult.warnings||[]).slice(0,4).map((w,i)=><span key={i}>warn: {w}</span>)}
              {Array.isArray(aiSqlResult.alternatives)&&aiSqlResult.alternatives.length>0&&<div style={{display:"grid",gap:4,marginTop:4}}>
                {aiSqlResult.alternatives.map(alt=><button key={alt.key} onClick={()=>{const nextSort=cleanSortSpec(alt.sort);const nextAggregate=cleanAggregateSpec(alt.aggregate);const altCols=Array.isArray(alt.selected_columns)?alt.selected_columns:[];const altSql=alt.display_sql||buildDisplaySql(altCols,alt.where_sql||alt.sql||"",nextSort);setSql(altSql);setSelectedCols(altCols);setSortSpec(null);setAggregateSpec(nextAggregate);applySql(altSql,altCols,null,nextAggregate);submitAiSqlFeedback("up","alternative "+alt.key,{sql:altSql,sort:nextSort||{},aggregate:nextAggregate||{},selected_columns:altCols,choice:alt.key});}} style={{textAlign:"left",padding:"5px 7px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontSize:12,cursor:"pointer",fontFamily:"inherit"}}>
                  {alt.key}안 {alt.label}: {(alt.display_sql||alt.sql||"(no filter)")}{cleanAggregateSpec(alt.aggregate)?` · ${aggregateLabel(alt.aggregate)}`:""}
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
      {/* v8.7.5: FileBrowser gear — PageGear 스타일 통일 · 좌하단 */}
      {!embedded&&isFileBrowserAdmin&&<>
        <PageGearButton onClick={toggleS3Settings} title={isAdmin?"폴더 설정 / 파일 설정 / S3 동기화 / AWS 설정":"S3 실행 / 이력 / 폴더 설정 / 파일 설정"} zIndex={97} />
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
                <span key={t.k} onClick={()=>{setS3Tab(t.k);if(t.k==="add"&&canManageS3Ingest)setS3Form({id:"",kind:"db",target:"",s3_url:"",command:"sync",direction:"download",extra_args:"",endpoint_url:"",profile:"",interval_min:0,enabled:true});}} style={{padding:"5px 12px",borderRadius:5,fontSize:14,cursor:"pointer",fontWeight:s3Tab===t.k?700:500,background:s3Tab===t.k?"var(--accent-glow)":"transparent",color:s3Tab===t.k?"var(--accent)":"var(--text-secondary)"}}>{t.l}</span>
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
                        {isScheduled&&<span style={{color:status?.format_current?"var(--text-secondary)":FB_BAD.fg}}>canonical format = v{status?.format_version||0} / expected v{status?.expected_format_version||2} · {status?.format_current?"current":"legacy ignored"}</span>}
                        {status?.canonical_owned_by&&<span>writer = {status.canonical_owned_by}</span>}
                        {status?.legacy_parquet_path&&<span style={{overflowWrap:"anywhere"}}>legacy preserved (not read) = {status.legacy_parquet_path}</span>}
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
                <div style={{display:"grid",gap:7,padding:"10px 12px",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)"}}>
                  <div style={{fontWeight:800,color:"var(--text-primary)"}}>DB 이름 표시 매칭</div>
                  <div style={{fontSize:12,color:"var(--text-secondary)",lineHeight:1.45}}>왼쪽 실제 폴더명은 유지하고 파일탐색기에는 오른쪽 표시명으로 보여줍니다. 기본 규칙은 <code>1.RAWDATA_DB → FAB</code>, <code>1.RAWDATA_DB_이름 → 이름</code>입니다.</div>
                  {Object.entries(fbDbNameAliases||{}).sort(([a],[b])=>a.localeCompare(b)).map(([source,display])=><label key={source} style={{display:"grid",gridTemplateColumns:"minmax(190px,1fr) minmax(120px,0.7fr)",gap:8,alignItems:"center"}}>
                    <span title={source} style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontFamily:"monospace",fontSize:12,color:"var(--text-secondary)"}}>{source}</span>
                    <input value={display} onChange={e=>setFbDbNameAliases(prev=>({...prev,[source]:e.target.value}))} placeholder={source} style={{minWidth:0,padding:"6px 8px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13}}/>
                  </label>)}
                  {!Object.keys(fbDbNameAliases||{}).length&&<span style={{fontSize:12,color:"var(--text-secondary)"}}>표시할 DB 폴더가 없습니다.</span>}
                </div>
                <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                  Files에 표시할 폴더
                  <textarea value={fbHiddenDbDirsText} onChange={e=>setFbHiddenDbDirsText(e.target.value)} rows={4} spellCheck={false} placeholder={"reformatter"} style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace",lineHeight:1.45}}/>
                  <span style={{fontSize:12,fontWeight:400,color:"var(--text-secondary)",lineHeight:1.45}}>한 줄에 폴더 하나. 여기에 등록한 운영 폴더와 최상위 파일만 Files 목록에 보입니다. cache, credential, teg_location 및 이름에 backup이 포함된 폴더는 관리자에게도 DB와 Files에서 항상 숨깁니다.</span>
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
                  <div style={{display:"grid",gap:7,padding:"9px 10px",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)"}}>
                    <div style={{fontSize:13,fontWeight:900,color:"var(--text-primary)"}}>Files 설명</div>
                    <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                      파일
                      <select value={fbDescriptionFile} onChange={e=>selectDescriptionFile(e.target.value)} style={{padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"monospace"}}>
                        <option value="">파일 선택</option>
                        {settingsBaseFiles.map(f=><option key={f.path||f.name} value={f.path||f.name}>{f.path||f.name}</option>)}
                      </select>
                    </label>
                    <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                      설명
                      <textarea value={fbDescriptionText} onChange={e=>setFbDescriptionText(e.target.value.slice(0,500))} rows={3} maxLength={500} disabled={!fbDescriptionFile} placeholder="파일의 용도, 원천, 갱신 주기 등을 입력하세요." style={{width:"100%",boxSizing:"border-box",resize:"vertical",padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,lineHeight:1.4}}/>
                    </label>
                    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,fontSize:11,color:"var(--text-secondary)"}}>
                      <span>Files 목록에서 파일에 커서를 올리면 표시됩니다.</span>
                      <span>{fbDescriptionText.length}/500</span>
                    </div>
                    <button onClick={()=>saveFilebrowserSettings("description")} disabled={!fbDescriptionFile||fbSettingsLoading} style={{padding:"7px 10px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:13,fontWeight:800,cursor:!fbDescriptionFile||fbSettingsLoading?"default":"pointer",opacity:!fbDescriptionFile||fbSettingsLoading?0.5:1}}>설명 저장</button>
                  </div>
                  <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                    CSV 전체 표시 기준 (MB)
                    <input type="number" min={0} step={0.5} value={fbThresholdMb} onChange={e=>setFbThresholdMb(e.target.value)} style={{padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
                  </label>
                  <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                    CSV 다운로드 최대 크기 (MB)
                    <input type="number" min={1} max={Math.round((fbSettings.max_csv_download_max_bytes||100000000)/1048576)} step={1} value={fbDownloadMb} onChange={e=>setFbDownloadMb(e.target.value)} style={{padding:"7px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
                  </label>
                  <label style={{display:"flex",flexDirection:"column",gap:4,color:"var(--text-secondary)",fontWeight:700}}>
                    CSV 다운로드 최대 행 (1~{Number(fbSettings.max_csv_download_max_rows||500000).toLocaleString()})
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
                {s3AutoSync&&<div style={{display:"flex",alignItems:"center",gap:10,padding:"8px 10px",marginBottom:8,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-secondary)",fontSize:14}}>
                  <span style={{fontWeight:700,color:"var(--text-secondary)"}}>주기 동기화</span>
                  {s3AutoSync.disabled_by_env?<span style={{color:FB_AMBER,fontWeight:700}}>환경변수(FLOW_DISABLE_S3_INGEST)로 이 서버의 주기 실행이 꺼져 있습니다 — 수동 실행만 가능</span>
                  :<>
                    {[{k:"auto_download_enabled",l:"⬇ 다운로드"},{k:"auto_upload_enabled",l:"⬆ 업로드"}].map(t=>{
                      const on=!!s3AutoSync[t.k];
                      return(<span key={t.k} onClick={()=>canManageS3Ingest&&s3SaveAutoSync({auto_download_enabled:!!s3AutoSync.auto_download_enabled,auto_upload_enabled:!!s3AutoSync.auto_upload_enabled,[t.k]:!on})}
                        title={canManageS3Ingest?"클릭하여 전환":"Admin 전용"}
                        style={{padding:"3px 10px",borderRadius:12,cursor:canManageS3Ingest?"pointer":"default",fontWeight:700,
                          background:on?"#22c55e22":"#94a3b822",color:on?FB_OK.fg:FB_DISABLED,border:"1px solid "+(on?FB_OK.fg:"var(--border)")}}>
                        {t.l} {on?"ON":"OFF"}
                      </span>);
                    })}
                    <span style={{color:"var(--text-secondary)"}}>OFF 시 주기 실행만 멈추고 ▶ 수동 실행은 가능합니다. 서버별(개발/양산) 역할에 맞게 설정하세요.</span>
                  </>}
                </div>}
                {s3Items.length===0?<div style={{padding:30,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>{canManageS3Ingest?<>설정된 S3 동기화 항목이 없습니다. <b>+ 추가</b> 를 클릭해 생성하세요.</>:"설정된 S3 동기화 항목이 없습니다. 항목 생성과 AWS 설정은 Admin이 관리합니다."}</div>
                :<table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
                  <thead><tr style={{background:"var(--bg-secondary)"}}>
                    {["","타겟","종류","방향","S3 URL","명령","주기","다음","마지막","동작"].map(h=>(
                      <th key={h} style={{padding:"6px 8px",textAlign:"left",fontSize:14,fontWeight:700,color:"var(--text-secondary)",borderBottom:FB_GRID_LINE,whiteSpace:"nowrap"}}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {s3Items.map(it=>{
                      const st=it.status||{};const s=st.last_status||"never";
                      const badge={ok:{c:FB_OK.fg,bg:"#22c55e22",t:"OK"},error:{c:FB_BAD.fg,bg:"#ef444422",t:"ERR"},running:{c:FB_AMBER,bg:"#f59e0b22",t:"RUN"},cancelled:{c:FB_AMBER,bg:"#94a3b822",t:"중지"},never:{c:FB_DISABLED,bg:"#94a3b822",t:"—"}}[s]||{c:FB_DISABLED,bg:"#94a3b822",t:s};
                      const isRunning=it.is_running||s==="running";
                      const isBusy=isRunning||it.is_queued;
                      const isPaused=it.enabled===false;
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
                          {st.last_output_tail&&<span onClick={()=>setS3Detail({id:it.id,tail:st.last_output_tail,cmd:it.s3_url,exit:st.last_exit_code,reason:st.last_reason||st.last_output_tail,aiExplanation:st.last_ai_explanation,action:"run"})} style={{marginLeft:4,cursor:"pointer",color:"var(--accent)"}}>로그</span>}
                        </td>
                        <td style={{padding:"6px 8px",whiteSpace:"nowrap"}}>
                          {isBusy
                            ?<button onClick={()=>s3Stop(it.id)} style={{padding:"3px 8px",borderRadius:3,border:"none",background:FB_BAD.fg,color:"#fff",fontSize:14,cursor:"pointer",marginRight:3}} title="실행 중/대기 중인 전송을 즉시 중지">■ 중지</button>
                            :<button onClick={()=>s3Run(it.id)} style={{padding:"3px 8px",borderRadius:3,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer",marginRight:3}}>▶ 실행</button>}
                          {canManageS3Ingest&&<button onClick={()=>s3SetEnabled(it.id,isPaused)} style={{padding:"3px 8px",borderRadius:3,border:"1px solid "+(isPaused?FB_OK.fg:FB_AMBER),background:"transparent",color:isPaused?FB_OK.fg:FB_AMBER,fontSize:14,cursor:"pointer",marginRight:3}} title={isPaused?"주기 동기화 재개":"항목 삭제 없이 주기 동기화만 일시정지"}>{isPaused?"재개":"⏸ 정지"}</button>}
                          {canManageS3Ingest&&<button onClick={()=>{setS3Form({...it});setS3Tab("add");}} style={{padding:"3px 8px",borderRadius:3,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer",marginRight:3}}>수정</button>}
                          {canManageS3Ingest&&<button onClick={()=>s3Delete(it.id)} style={{padding:"3px 8px",borderRadius:3,border:`1px solid ${FB_BAD.fg}`,background:"transparent",color:FB_BAD.fg,fontSize:14,cursor:"pointer"}}>✕</button>}
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
                    <span style={{fontSize:14,color:"var(--text-secondary)"}}>DB_BASE 하위 경로. 슬래시(/)로 하위 디렉터리까지 지정 가능 — 예: <code>DB/1.RAWDATA/제품명</code>. 파일탐색기에서 숨긴 <code>credential</code>도 S3 동기화 타겟으로 등록할 수 있습니다.</span>
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
                    {["시간","구분","항목","상태","종료코드","소요시간","사유","명령"].map(h=>(<th key={h} style={{padding:"6px 8px",textAlign:"left",fontSize:14,fontWeight:700,color:"var(--text-secondary)",borderBottom:FB_GRID_LINE}}>{h}</th>))}
                  </tr></thead>
                  <tbody>
                    {s3Hist.map((h,i)=>{
                      const reason=s3HistoryReason(h);
                      const ai=h.ai_explanation||null;
                      const tail=h.output_tail||h.stderr_tail||h.stdout_tail||h.error||h.reason||"";
                      return(<tr key={i} style={{borderBottom:FB_GRID_LINE}}>
                        <td style={{padding:"5px 8px",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",whiteSpace:"nowrap"}}>{(h.timestamp||"").slice(5,19).replace("T"," ")}</td>
                        <td style={{padding:"5px 8px",fontSize:14,whiteSpace:"nowrap"}}>{s3HistoryAction(h)}</td>
                        <td style={{padding:"5px 8px",fontSize:14,fontFamily:"monospace"}}>{h.id}</td>
                        <td style={{padding:"5px 8px"}}><span style={{fontSize:14,padding:"2px 6px",borderRadius:3,background:h.status==="ok"?"#22c55e22":"#ef444422",color:h.status==="ok"?FB_OK.fg:FB_BAD.fg,fontWeight:700}}>{h.status}</span></td>
                        <td style={{padding:"5px 8px",fontSize:14,fontFamily:"monospace"}}>{h.exit_code??"-"}</td>
                        <td style={{padding:"5px 8px",fontSize:14}}>{h.duration_sec!=null?h.duration_sec+"s":"-"}</td>
                        <td style={{padding:"5px 8px",fontSize:14,maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={reason}>
                          {reason?<span onClick={()=>setS3Detail({id:h.id||h.target||"S3",tail,cmd:h.cmd||"",exit:h.exit_code,reason,aiExplanation:ai,action:s3HistoryAction(h)})} style={{cursor:"pointer",color:h.status==="ok"?"var(--text-secondary)":"var(--accent)"}}>{reason}</span>:"-"}
                          {ai&&<span onClick={()=>setS3Detail({id:h.id||h.target||"S3",tail,cmd:h.cmd||"",exit:h.exit_code,reason,aiExplanation:ai,action:s3HistoryAction(h)})} style={{marginLeft:6,padding:"1px 5px",borderRadius:3,background:"var(--accent-glow)",color:"var(--accent)",fontSize:12,fontWeight:700,cursor:"pointer"}}>AI</span>}
                        </td>
                        <td style={{padding:"5px 8px",fontSize:14,fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:260,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={h.cmd||""}>{h.cmd||"-"}</td>
                      </tr>);
                    })}
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
            <Modal open onClose={()=>setS3Detail(null)} width={760} zIndex={100}>
            <div style={{display:"flex",flexDirection:"column",maxHeight:"70vh"}}>
              <div style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center"}}>
                <span style={{flex:1,fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{s3Detail.id} — {s3Detail.action||"실행"} · exit={s3Detail.exit??"-"}</span>
                <span onClick={()=>setS3Detail(null)} style={{cursor:"pointer",fontSize:16,color:"var(--text-secondary)"}}>✕</span>
              </div>
              <div style={{padding:"12px 14px",display:"grid",gap:8,borderBottom:"1px solid var(--border)",fontSize:14,lineHeight:1.55}}>
                {s3Detail.reason&&<div><b style={{color:"var(--text-primary)"}}>사유</b><div style={{marginTop:3,color:"var(--text-secondary)",whiteSpace:"pre-wrap",wordBreak:"break-word"}}>{s3Detail.reason}</div></div>}
                {s3Detail.aiExplanation&&<div style={{padding:10,border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)"}}>
                  <div style={{fontWeight:700,color:"var(--accent)",marginBottom:5}}>AI 오류 해석</div>
                  {s3Detail.aiExplanation.summary&&<div><b>문제:</b> {s3Detail.aiExplanation.summary}</div>}
                  {s3Detail.aiExplanation.cause&&<div><b>가능한 원인:</b> {s3Detail.aiExplanation.cause}</div>}
                  {(s3Detail.aiExplanation.how_to_fix||[]).length>0&&<div><b>확인할 것:</b> {(s3Detail.aiExplanation.how_to_fix||[]).join(" / ")}</div>}
                </div>}
                {s3Detail.cmd&&<div><b style={{color:"var(--text-primary)"}}>명령</b><div style={{marginTop:3,color:"var(--text-secondary)",fontFamily:"monospace",whiteSpace:"pre-wrap",wordBreak:"break-all"}}>{s3Detail.cmd}</div></div>}
              </div>
              <pre style={{flex:1,overflow:"auto",margin:0,padding:12,fontSize:14,fontFamily:"monospace",color:"var(--text-primary)",background:"var(--bg-primary)",whiteSpace:"pre-wrap",wordBreak:"break-all"}}>{s3Detail.tail||"(출력 없음)"}</pre>
            </div>
            </Modal>
          )}
        </>}
      </>}
    </div>);
}
