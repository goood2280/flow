import { useState, useEffect, useCallback } from "react";
import Loading from "../components/Loading";
import AwsPanel from "../components/AwsPanel";
import S3StatusLight from "../components/S3StatusLight";
import { sf } from "../lib/api";
const API="/api/filebrowser";
function formatSize(b){if(!b)return"-";if(b<1024)return b+" B";if(b<1048576)return(b/1024).toFixed(1)+" KB";if(b<1073741824)return(b/1048576).toFixed(1)+" MB";return(b/1073741824).toFixed(2)+" GB";}

export default function My_FileBrowser({user}){
  const[roots,setRoots]=useState([]);const[rootPqs,setRootPqs]=useState([]);const[selRoot,setSelRoot]=useState("");
  const[products,setProducts]=useState([]);const[selProd,setSelProd]=useState("");const[sideLoading,setSideLoading]=useState(true);
  const[data,setData]=useState(null);const[sql,setSql]=useState("");const[loading,setLoading]=useState(false);
  const[tab,setTab]=useState("data");const[colSearch,setColSearch]=useState("");const[showGuide,setShowGuide]=useState(false);const[mode,setMode]=useState("hive");
  const[selRootPq,setSelRootPq]=useState("");
  // v4.1: scope switcher — "DB" (hive-flat) or "Base" (single-file rulebook/wide parquet).
  // `scopes` keyed array from /api/filebrowser/scopes; `scope` = active key.
  const[scopes,setScopes]=useState([]);const[scope,setScope]=useState("DB");
  const[baseFiles,setBaseFiles]=useState([]);const[selBaseFile,setSelBaseFile]=useState("");
  // v4.1: raw preview for json/md so the main pane can render them natively
  // (pretty-printed JSON / markdown-as-pre) instead of stuffing text into the table.
  const[baseRaw,setBaseRaw]=useState(null);
  // Column selection state
  const[selectedCols,setSelectedCols]=useState([]);const[colSelectMode,setColSelectMode]=useState(false);
  const[error,setError]=useState("");
  // S3 sync status map (public endpoint) — powers sidebar traffic-light dots
  const[s3Status,setS3Status]=useState({});
  useEffect(()=>{
    const load=()=>fetch("/api/s3ingest/status-by-target").then(r=>r.ok?r.json():null).then(d=>{if(d&&d.by_target)setS3Status(d.by_target);}).catch(()=>{});
    load();
    const t=setInterval(load,30000);
    return()=>clearInterval(t);
  },[]);
  const s3Light=(name)=>{
    const info=s3Status&&s3Status[name];
    const last=info?(info.last_end||info.last_start):null;
    const lastStr=last?last.slice(0,16).replace("T"," "):"-";
    const ageH=last?(Date.now()-new Date(last).getTime())/3600000:Infinity;
    const nextStr=info&&info.next_due?info.next_due.slice(0,16).replace("T"," "):(info&&info.interval_min>0?"계산중":"수동 실행만");
    if(!info)return{color:"#ef4444",tip:"S3 동기화 미설정 — File Browser 우하단 ⚙️(admin) 에서 설정하세요"};
    if(info.is_running)return{color:"#3b82f6",tip:"S3 동기화 실행 중…\n이전 실행: "+lastStr};
    const st=info.last_status||"never";
    let color,line;
    if(st==="error"){color="#ef4444";line="실패 (exit="+(info.last_exit_code??"?")+")";}
    else if(st==="ok"&&isFinite(ageH)&&ageH<=6){color="#22c55e";line="정상 (최근 "+ageH.toFixed(1)+"시간)";}
    else if(st==="ok"){color="#eab308";line="오래됨 ("+(isFinite(ageH)?Math.floor(ageH)+"시간 경과":"기록 없음")+")";}
    else{color="#ef4444";line="실행 기록 없음";}
    return{color,tip:"S3 "+(info.kind||"")+" 동기화 — "+line+"\n마지막: "+lastStr+"\n다음: "+nextStr+(info.interval_min>0?" ("+info.interval_min+"분 주기)":"")};
  };
  const lightDot=(name)=>{const l=s3Light(name);return(<span title={l.tip} style={{display:"inline-block",width:8,height:8,borderRadius:"50%",background:l.color,flexShrink:0,boxShadow:"0 0 4px "+l.color+"66"}}/>);};

  // S3 ingest admin modal state
  const isAdmin=user?.role==="admin";
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

  // Poll s3 items/history while modal open
  useEffect(()=>{
    if(!s3Open||!isAdmin)return;
    const un=encodeURIComponent(user?.username||"");
    const loadItems=()=>sf("/api/s3ingest/items?username="+un).then(d=>{setS3Items(d.items||[]);setS3AwsOk(d.aws_available!==false);}).catch(()=>{});
    const loadAvail=()=>sf("/api/s3ingest/available?username="+un).then(d=>setS3Avail(d||{dbs:[],root_parquets:[]})).catch(()=>{});
    const loadHist=()=>sf("/api/s3ingest/history?username="+un+"&limit=100").then(d=>setS3Hist(d.entries||[])).catch(()=>{});
    loadItems();
    if(s3Tab==="add")loadAvail();
    if(s3Tab==="history")loadHist();
    const t=setInterval(()=>{loadItems();if(s3Tab==="history")loadHist();},5000);
    return()=>clearInterval(t);
  },[s3Open,s3Tab,s3Tick,isAdmin,user?.username]);

  // 1s ticker for ETA countdown (only while modal open)
  useEffect(()=>{if(!s3Open)return;const t=setInterval(()=>setS3Now(Date.now()),1000);return()=>clearInterval(t);},[s3Open]);

  const s3Save=async(form)=>{
    if(!form.target||!form.s3_url){alert("target 과 s3_url 은 필수입니다");return;}
    const body={...form,username:user?.username||""};
    const r=await fetch("/api/s3ingest/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(!r.ok){const d=await r.json().catch(()=>({detail:"저장 실패"}));alert(d.detail||"저장 실패");return;}
    setS3Form(null);setS3Tab("items");setS3Tick(x=>x+1);
  };
  const s3Delete=async(id)=>{
    if(!window.confirm("이 S3 동기화 항목을 삭제하시겠습니까?\n("+id+")"))return;
    const r=await fetch("/api/s3ingest/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id})});
    if(r.ok)setS3Tick(x=>x+1);else{const d=await r.json().catch(()=>({}));alert(d.detail||"삭제 실패");}
  };
  const s3Run=async(id)=>{
    const r=await fetch("/api/s3ingest/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",id})});
    if(r.ok)setS3Tick(x=>x+1);else{const d=await r.json().catch(()=>({}));alert(d.detail||"실행 실패");}
  };
  const s3FmtETA=(item)=>{
    const iv=Number(item.interval_min||0);if(iv<=0)return"수동";
    const st=item.status||{};const last=st.last_end||st.last_start;
    if(!last)return"지금 실행 예정";
    const lastMs=new Date(last).getTime();if(isNaN(lastMs))return"-";
    const dueMs=lastMs+iv*60000;const diff=dueMs-s3Now;
    if(diff<=0)return"지금 실행 예정";
    const m=Math.floor(diff/60000),s=Math.floor((diff%60000)/1000);
    return m>=60?Math.floor(m/60)+"시간 "+(m%60)+"분":m+"분 "+s+"초";
  };

  useEffect(()=>{
    // v4.1: boot-load scopes + DB listings in parallel. Base listing is lazy
    // (loaded only when user switches scope) to keep the default cold-start fast.
    Promise.all([
      sf(API+"/scopes").catch(()=>({scopes:[{key:"DB",label:"DB",exists:true,icon:"🗄️"}]})),
      sf(API+"/roots"),
      sf(API+"/root-parquets"),
    ]).then(([sc,r,rp])=>{
      setScopes(sc.scopes||[]);
      setRoots(r.roots||[]);setRootPqs(rp.files||[]);
      if(r.roots?.length)setSelRoot(r.roots[0].name);
      setSideLoading(false);
    }).catch(()=>setSideLoading(false));
  },[]);

  // v4.1: when user switches to Base scope, fetch /base-files (idempotent).
  useEffect(()=>{
    if(scope!=="Base")return;
    if(baseFiles.length>0)return;
    setSideLoading(true);
    sf(API+"/base-files").then(d=>{setBaseFiles(d.files||[]);setSideLoading(false);}).catch(()=>setSideLoading(false));
  },[scope]); // eslint-disable-line

  // v4.1: Base-file preview loader (parquet/csv/json/md).
  const loadBaseFileView=(file)=>{
    setLoading(true);setTab("data");setMode("base");setSelBaseFile(file);
    setSelProd("");setSelRootPq("");setError("");setBaseRaw(null);
    const url=buildUrl(API+"/base-file-view",{file,rows:200,cols:10});
    sf(url).then(d=>{
      if(d.kind==="json"||d.kind==="md"){
        // Render natively: JSON is pretty-printed, md shown as raw <pre>.
        let pretty=d.preview||d.text||"(empty)";
        if(d.kind==="json"){
          try{pretty=JSON.stringify(JSON.parse(pretty),null,2);}catch(_){/* leave raw */}
        }
        setBaseRaw({kind:d.kind,file,size:d.size,truncated:d.truncated,top_keys:d.parsed_top_keys,text:pretty});
        setData(null);
      }else{
        setData(d);
      }
      setLoading(false);
    }).catch(e=>{setError(e.message);setLoading(false);});
  };

  useEffect(()=>{
    if(!selRoot){setProducts([]);return;}
    setSideLoading(true);
    sf(API+"/products?root="+encodeURIComponent(selRoot)).then(d=>{setProducts(d.products||[]);setSideLoading(false);}).catch(()=>setSideLoading(false));
  },[selRoot]);

  const buildUrl=(base,params)=>{
    const q=Object.entries(params).filter(([_,v])=>v!==undefined&&v!=="").map(([k,v])=>k+"="+encodeURIComponent(v)).join("&");
    return base+"?"+q;
  };

  const loadHiveView=(root,prod,sqlQ,selColsOverride)=>{
    setLoading(true);setTab("data");setMode("hive");setSelProd(prod);setSelRootPq("");setError("");setBaseRaw(null);
    const sc=selColsOverride||selectedCols;
    const url=buildUrl(API+"/view",{root,product:prod,sql:sqlQ||"",rows:200,select_cols:sc.length?sc.join(","):""});
    sf(url).then(d=>{setData(d);setLoading(false);}).catch(e=>{setError(e.message);setLoading(false);});
  };

  const loadRootPqView=(file,sqlQ,selColsOverride)=>{
    setLoading(true);setTab("data");setMode("rootpq");setSelRootPq(file);setSelProd("");setError("");setBaseRaw(null);
    const sc=selColsOverride||selectedCols;
    const url=buildUrl(API+"/root-parquet-view",{file,sql:sqlQ||"",rows:200,cols:10,select_cols:sc.length?sc.join(","):""});
    sf(url).then(d=>{setData(d);setLoading(false);}).catch(e=>{setError(e.message);setLoading(false);});
  };

  const applySql=()=>{
    if(mode==="rootpq"&&selRootPq)loadRootPqView(selRootPq,sql);
    else if(mode==="base"&&selBaseFile){
      // Base JSON/md files have no SQL surface — silently ignore. Tabular
      // parquet/csv re-load with the SQL param applied server-side.
      if(data?._kind==="json"||data?._kind==="md")return;
      setLoading(true);setError("");
      const url=buildUrl(API+"/base-file-view",{file:selBaseFile,sql:sql||"",rows:200,cols:10,
        select_cols:selectedCols.length?selectedCols.join(","):""});
      sf(url).then(d=>{setData(d);setLoading(false);}).catch(e=>{setError(e.message);setLoading(false);});
    }
    else if(selRoot&&selProd)loadHiveView(selRoot,selProd,sql);
  };

  const toggleCol=(col)=>{
    setSelectedCols(prev=>{
      const next=prev.includes(col)?prev.filter(c=>c!==col):[...prev,col];
      return next;
    });
  };

  const reloadWithCols=(cols)=>{
    // v8.4.4: Base 모드도 select_cols 적용되도록 분기 추가
    if(mode==="rootpq"&&selRootPq){loadRootPqView(selRootPq,sql,cols);}
    else if(mode==="base"&&selBaseFile){
      if(data?._kind==="json"||data?._kind==="md")return;
      setLoading(true);setError("");setTab("data");
      const url=buildUrl(API+"/base-file-view",{file:selBaseFile,sql:sql||"",rows:200,cols:10,
        select_cols:cols.length?cols.join(","):""});
      sf(url).then(d=>{setData(d);setLoading(false);}).catch(e=>{setError(e.message);setLoading(false);});
    }
    else if(selRoot&&selProd){loadHiveView(selRoot,selProd,sql,cols);}
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
    let url=API+"/download-csv?username="+(user?.username||"anon")+"&sql="+encodeURIComponent(sql);
    if(selectedCols.length)url+="&select_cols="+encodeURIComponent(selectedCols.join(","));
    if(mode==="rootpq")url+="&file="+encodeURIComponent(selRootPq);
    else url+="&root="+encodeURIComponent(selRoot)+"&product="+encodeURIComponent(selProd);
    fetch(url).then(r=>{if(!r.ok)return r.json().then(d=>{alert(d.detail||"다운로드 실패");throw new Error();});
      return r.blob();}).then(blob=>{const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='data.csv';a.click();}).catch(()=>{});
  };

  const allCols=data?.all_columns||data?.columns||[];
  const filteredCols=colSearch?allCols.filter(c=>c.toLowerCase().includes(colSearch.toLowerCase())):allCols;

  const chipS={display:"inline-flex",alignItems:"center",gap:4,padding:"2px 8px",borderRadius:4,fontSize:10,cursor:"pointer",marginRight:4,marginBottom:4,border:"1px solid var(--border)",transition:"all 0.15s"};
  const chipActive={...chipS,background:"var(--accent-glow)",borderColor:"var(--accent)",color:"var(--accent)",fontWeight:600};
  const chipInactive={...chipS,background:"var(--bg-hover)",color:"var(--text-secondary)"};

  return(
    <div style={{display:"flex",height:"calc(100vh - 48px)",fontFamily:"'Pretendard',sans-serif",background:"var(--bg-primary)",color:"var(--text-primary)"}}>
      {/* Sidebar */}
      <div style={{width:260,minWidth:260,borderRight:"1px solid var(--border)",display:"flex",flexDirection:"column",background:"var(--bg-secondary)"}}>
        <div style={{padding:"14px 16px 10px",borderBottom:"1px solid var(--border)",fontSize:12,fontWeight:700,color:"var(--text-secondary)",textTransform:"uppercase",letterSpacing:"0.04em",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <span>데이터 브라우저</span>
          <S3StatusLight compact />
        </div>
        {/* v4.1: Scope switcher (DB / Base). Shown only when backend reports 2+ scopes. */}
        {scopes.length>=2&&<div className="filebrowser-scope-switcher" style={{display:"flex",gap:4,padding:"6px 10px",borderBottom:"1px solid var(--border)"}}>
          {scopes.map(s=>{
            const active=scope===s.key;const disabled=s.exists===false;
            return(<span key={s.key} className={"filebrowser-scope-option filebrowser-scope-"+s.key} data-scope={s.key} data-active={active?"1":"0"}
              onClick={()=>{if(disabled)return;setScope(s.key);setData(null);setBaseRaw(null);setError("");setSelProd("");setSelRootPq("");setSelBaseFile("");setSelectedCols([]);}}
              title={s.description+(disabled?"\n(경로 없음 — admin_settings 확인)":"")}
              style={{flex:1,textAlign:"center",padding:"6px 8px",borderRadius:5,fontSize:11,cursor:disabled?"not-allowed":"pointer",fontWeight:active?700:500,
                background:active?"var(--accent-glow)":"var(--bg-hover)",color:disabled?"var(--text-secondary)":(active?"var(--accent)":"var(--text-primary)"),
                opacity:disabled?0.4:1,border:"1px solid "+(active?"var(--accent)":"var(--border)")}}>
              {s.icon} {s.label}
            </span>);
          })}
        </div>}
        {sideLoading?<div style={{padding:20}}><Loading text="로딩 중..." size="sm"/></div>:scope==="Base"?<>
          {/* v4.1: Base scope — single-file rulebook + wide parquet listing. */}
          <div style={{flex:1,overflow:"auto",padding:"6px 8px"}}>
            <div style={{fontSize:10,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>Base 파일 ({baseFiles.length})</div>
            {baseFiles.length===0&&<div style={{padding:"10px 12px",fontSize:11,color:"var(--text-secondary)"}}>Base 루트에서 파일을 찾을 수 없습니다.</div>}
            {baseFiles.map(f=>{
              const isSel=selBaseFile===f.name;
              const extColor={parquet:"#10b981",csv:"#3b82f6",json:"#f59e0b",md:"#94a3b8"}[f.ext]||"#64748b";
              const icon={parquet:"📊",csv:"📋",json:"🔧",md:"📄"}[f.ext]||"📁";
              return(<div key={f.name} className="filebrowser-base-file" data-file={f.name} data-ext={f.ext} onClick={()=>{setSelectedCols([]);loadBaseFileView(f.name);}}
                style={{display:"flex",alignItems:"center",gap:6,padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:11,marginBottom:1,
                  background:isSel?"var(--bg-hover)":"transparent",color:isSel?"var(--accent)":"var(--text-primary)"}}>
                {/* v8.7.5: Base 단일 파일도 S3 신호등 표시 (다운로드/업로드 양방향). */}
                {lightDot(f.name)}
                <span>{icon}</span>
                <span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={f.name}>{f.name}</span>
                {/* v8.7.7: `db` 소스 태그 제거 — Base 단일 파일은 소스 구분 없이 한 번만 표시. */}
                <span style={{fontSize:9,padding:"1px 4px",borderRadius:3,background:extColor+"22",color:extColor,fontWeight:700,fontFamily:"monospace"}}>{f.ext}</span>
                <span style={{fontSize:9,color:"#64748b"}}>{formatSize(f.size)}</span>
              </div>);
            })}
          </div>
        </>:<>
          <div style={{padding:"8px 12px"}}>
            {roots.map(r=>{
              // v8.4.3: icon + level badge 제거 — 깔끔한 이름만.
              return (
              <div key={r.name} onClick={()=>{setSelRoot(r.name);setSelectedCols([]);}} title={r.description||""} style={{display:"flex",alignItems:"center",gap:6,padding:"8px 12px",borderRadius:6,cursor:"pointer",fontSize:12,
                background:selRoot===r.name?"var(--bg-hover)":"transparent",fontWeight:selRoot===r.name?600:400,color:selRoot===r.name?"var(--accent)":"var(--text-primary)"}}>
                {lightDot(r.name)}
                <span style={{flex:1}}>{r.canonical||r.name}</span>
                <span style={{fontSize:9,color:"#64748b"}}>{r.parquet_count}</span>
              </div>);
            })}
          </div>
          {products.length>0&&<div style={{flex:1,overflow:"auto",borderTop:"1px solid var(--border)",padding:"4px 8px"}}>
            <div style={{fontSize:10,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>제품</div>
            {products.map(p=>(
              <div key={p.name} onClick={()=>{setSelectedCols([]);loadHiveView(selRoot,p.name,"");}} style={{padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:11,marginBottom:1,
                background:selProd===p.name?"var(--bg-hover)":"transparent",color:selProd===p.name?"var(--accent)":"var(--text-primary)"}}>
                {p.name} <span style={{fontSize:9,color:"#64748b"}}>{p.latest_date}</span>
              </div>))}
          </div>}
          {rootPqs.length>0&&<div style={{borderTop:"1px solid var(--border)",padding:"4px 8px",maxHeight:200,overflow:"auto"}}>
            <div style={{fontSize:10,fontWeight:700,color:"var(--text-secondary)",padding:"6px 8px",textTransform:"uppercase"}}>루트 Parquet</div>
            {rootPqs.map(f=>(
              <div key={f.name} onClick={()=>{setSelectedCols([]);loadRootPqView(f.name,"");}} style={{display:"flex",alignItems:"center",gap:6,padding:"6px 10px",borderRadius:5,cursor:"pointer",fontSize:11,marginBottom:1,
                background:selRootPq===f.name?"var(--bg-hover)":"transparent",color:selRootPq===f.name?"var(--accent)":"var(--text-primary)"}}>
                {lightDot(f.name)}
                <span>📊 {f.name}</span> <span style={{fontSize:9,color:"#64748b",marginLeft:"auto"}}>{formatSize(f.size)}</span>
              </div>))}
          </div>}
        </>}
      </div>
      {/* Main */}
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
        {/* SQL Bar */}
        <div style={{padding:"10px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",gap:8,alignItems:"center"}}>
          <span style={{fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace",flexShrink:0}}>SQL:</span>
          <input value={sql} onChange={e=>setSql(e.target.value)} placeholder="예: PRODUCT LIKE '%ABC%' 또는 col == 'value'"
            style={{flex:1,padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,fontFamily:"monospace",outline:"none"}}
            onKeyDown={e=>e.key==="Enter"&&applySql()}/>
          <button onClick={applySql} style={{padding:"6px 14px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,fontWeight:600,cursor:"pointer"}}>실행</button>
          {data&&<button onClick={downloadCsv} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:11,fontWeight:600,cursor:"pointer"}}>⬇ CSV</button>}
        </div>

        {/* Selected columns chips */}
        {selectedCols.length>0&&<div style={{padding:"6px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",display:"flex",alignItems:"center",gap:6,flexWrap:"wrap"}}>
          <span style={{fontSize:10,color:"var(--text-secondary)",fontWeight:600,flexShrink:0}}>SELECT:</span>
          {selectedCols.map(c=><span key={c} style={chipActive} onClick={()=>toggleCol(c)}>{c} ×</span>)}
          <button onClick={applySelectedCols} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:10,fontWeight:600,cursor:"pointer"}}>적용</button>
          <button onClick={clearSelectedCols} style={{padding:"3px 10px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:10,cursor:"pointer"}}>초기화</button>
        </div>}

        {/* SQL Guide */}
        <div style={{padding:"0 16px"}}>
          <div onClick={()=>setShowGuide(!showGuide)} style={{fontSize:11,color:"var(--accent)",cursor:"pointer",padding:"4px 0"}}>
            {showGuide?"▼":"▶"} SQL 가이드</div>
          {showGuide&&<div style={{background:"var(--bg-card)",borderRadius:6,padding:"8px 12px",marginBottom:8,border:"1px solid var(--border)",fontSize:11,fontFamily:"monospace",lineHeight:1.8,color:"var(--text-secondary)"}}>
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
        {error&&<div style={{margin:"0 16px 8px",padding:"8px 12px",background:"#ef444422",border:"1px solid #ef4444",borderRadius:6,fontSize:12,color:"#ef4444"}}>
          {error} <span onClick={()=>setError("")} style={{cursor:"pointer",marginLeft:8}}>✕</span>
        </div>}

        {/* Content */}
        <div style={{flex:1,overflow:"auto",padding:16}}>
          {loading&&<div style={{padding:40,textAlign:"center"}}><Loading text="로딩 중..."/></div>}
          {!loading&&!data&&!baseRaw&&!error&&<div style={{padding:60,textAlign:"center",color:"var(--text-secondary)",fontSize:13}}>사이드바에서 제품 또는 루트 parquet 을 선택하세요</div>}
          {!loading&&baseRaw&&<div className="filebrowser-base-raw" data-kind={baseRaw.kind}>
            <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12}}>
              <span style={{fontSize:14,fontWeight:600}}>{baseRaw.file}</span>
              <span style={{fontSize:12,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"4px 10px",borderRadius:6}}>
                {baseRaw.kind==="json"?"JSON":"Markdown"} | {formatSize(baseRaw.size)}{baseRaw.truncated?" | 일부만 표시됨":""}
                {baseRaw.top_keys?.length&&<span style={{color:"var(--accent)",marginLeft:8}}>top: {baseRaw.top_keys.slice(0,6).join(", ")}{baseRaw.top_keys.length>6?"…":""}</span>}
              </span>
            </div>
            <pre style={{margin:0,padding:12,background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:6,fontSize:11,lineHeight:1.5,fontFamily:"monospace",color:"var(--text-primary)",whiteSpace:"pre-wrap",wordBreak:"break-word",maxHeight:"calc(100vh - 240px)",overflow:"auto"}}>
              <code>{baseRaw.text}</code>
            </pre>
          </div>}
          {!loading&&data&&<>
            <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12}}>
              <span style={{fontSize:14,fontWeight:600}}>{selProd||selRootPq||selBaseFile}</span>
              <span style={{fontSize:12,color:"var(--text-secondary)",background:"var(--bg-card)",padding:"4px 10px",borderRadius:6}}>
                {data.total_rows?.toLocaleString()}행 × {data.total_cols}열 | 표시 {data.showing}
                {data.selected_cols&&<span style={{color:"var(--accent)"}}> | {data.selected_cols.length}열 선택됨</span>}
              </span>
            </div>
            {/* Tabs: Data + Columns */}
            <div style={{display:"flex",gap:0,borderBottom:"1px solid var(--border)",marginBottom:12}}>
              {["data","columns"].map(t=>(<div key={t} onClick={()=>setTab(t)} style={{padding:"8px 16px",fontSize:12,cursor:"pointer",fontWeight:tab===t?600:400,
                borderBottom:tab===t?"2px solid var(--accent)":"2px solid transparent",color:tab===t?"var(--text-primary)":"var(--text-secondary)"}}>
                {t==="data"?"데이터 ("+data.showing+")":"컬럼 ("+allCols.length+")"}</div>))}
            </div>
            {tab==="data"&&<div style={{overflow:"auto",maxHeight:"calc(100vh - 280px)"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                <thead><tr><th style={{textAlign:"left",padding:"6px 10px",fontWeight:600,fontSize:10,color:"var(--text-secondary)",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)",position:"sticky",top:0,zIndex:1}}>#</th>
                  {(data.showing_cols||data.columns||[]).map((c,i)=><th key={i} style={{textAlign:"left",padding:"6px 10px",fontWeight:600,fontSize:10,color:"var(--text-secondary)",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)",position:"sticky",top:0,zIndex:1,whiteSpace:"nowrap"}}>{c}</th>)}</tr></thead>
                <tbody>{data.data?.map((row,ri)=>(
                  <tr key={ri}><td style={{padding:"4px 10px",borderBottom:"1px solid var(--border)",color:"#64748b",fontSize:10}}>{ri+1}</td>
                    {(data.showing_cols||data.columns||[]).map((c,ci)=><td key={ci} style={{padding:"4px 10px",borderBottom:"1px solid var(--border)",maxWidth:180,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={String(row[c]||"")}>
                      {row[c]===null?<span style={{color:"#64748b"}}>null</span>:String(row[c])}</td>)}</tr>))}</tbody>
              </table></div>}
            {tab==="columns"&&<div>
              <div style={{display:"flex",gap:8,marginBottom:8,alignItems:"center"}}>
                <input value={colSearch} onChange={e=>setColSearch(e.target.value)} placeholder="컬럼 검색..."
                  style={{flex:1,padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none"}}/>
                {selectedCols.length>0&&<span style={{fontSize:11,color:"var(--accent)",fontWeight:600}}>{selectedCols.length}개 선택됨</span>}
              </div>
              <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:8,padding:"4px 0",lineHeight:1.6}}>
                클릭 → SQL 필터에 추가 | ☑ 체크 → 해당 열만 선택해서 보기
              </div>
              <div style={{maxHeight:"calc(100vh - 340px)",overflow:"auto"}}>
                {filteredCols.map((c,i)=>{
                  const isSelected=selectedCols.includes(c);
                  return(
                  <div key={i} style={{display:"flex",alignItems:"center",padding:"5px 12px",borderBottom:"1px solid var(--border)",fontSize:12,gap:8}}>
                    {/* Checkbox for column selection */}
                    <input type="checkbox" checked={isSelected} onChange={()=>toggleCol(c)}
                      style={{width:14,height:14,accentColor:"var(--accent)",cursor:"pointer",flexShrink:0}}/>
                    {/* Column name - click to insert into SQL */}
                    <span onClick={()=>insertColToSql(c)} style={{flex:1,cursor:"pointer",fontWeight:isSelected?600:500,color:isSelected?"var(--accent)":"var(--text-primary)"}} title={"클릭하면 SQL 필터에 추가됩니다"}>
                      {c}
                    </span>
                    {data.dtypes&&<span style={{fontSize:10,padding:"1px 6px",borderRadius:3,background:"var(--bg-tertiary)",color:"var(--accent)",flexShrink:0}}>{data.dtypes[c]}</span>}
                    <span onClick={()=>insertColToSql(c)} style={{fontSize:10,color:"var(--accent)",cursor:"pointer",padding:"2px 6px",borderRadius:3,background:"var(--accent-glow)",flexShrink:0}} title="SQL 필터에 추가">+ SQL</span>
                  </div>);})}
              </div>
              {selectedCols.length>0&&<div style={{marginTop:12,padding:"10px 12px",background:"var(--bg-card)",borderRadius:8,border:"1px solid var(--border)"}}>
                <div style={{fontSize:11,fontWeight:600,color:"var(--accent)",marginBottom:6}}>선택된 컬럼 ({selectedCols.length})</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:4,marginBottom:8}}>
                  {selectedCols.map(c=><span key={c} style={chipActive} onClick={()=>toggleCol(c)}>{c} ×</span>)}
                </div>
                <div style={{display:"flex",gap:6}}>
                  <button onClick={applySelectedCols} style={{padding:"6px 16px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,fontWeight:600,cursor:"pointer"}}>선택 적용</button>
                  <button onClick={clearSelectedCols} style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>모두 해제</button>
                </div>
              </div>}
            </div>}
          </>}
        </div>
      </div>
      {/* v8.7.5: Admin S3 ingest gear — PageGear 스타일 통일 · 좌하단 */}
      {isAdmin&&<>
        <div onClick={()=>setS3Open(!s3Open)} title="S3 동기화 / AWS 설정 (admin)" style={{position:"fixed",bottom:16,left:16,width:40,height:40,borderRadius:"50%",background:"var(--bg-secondary)",border:"1px solid var(--border)",display:"flex",alignItems:"center",justifyContent:"center",cursor:"pointer",zIndex:97,boxShadow:"0 2px 8px rgba(0,0,0,0.3)",fontSize:18}}>⚙️</div>
        {s3Open&&<>
          <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.5)",zIndex:98}} onClick={()=>{setS3Open(false);setS3Form(null);setS3Detail(null);}}/>
          <div style={{position:"fixed",top:"50%",left:"50%",transform:"translate(-50%,-50%)",width:"min(780px,94vw)",maxHeight:"86vh",background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:10,zIndex:99,display:"flex",flexDirection:"column",boxShadow:"0 16px 48px rgba(0,0,0,0.6)"}}>
            <div style={{display:"flex",alignItems:"center",padding:"12px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",borderRadius:"10px 10px 0 0"}}>
              <span style={{fontSize:13,fontWeight:700,color:"var(--accent)",fontFamily:"monospace",flex:1}}>S3 동기화 설정 — aws s3 cp/sync</span>
              {!s3AwsOk&&<span style={{fontSize:10,padding:"2px 8px",borderRadius:4,background:"#ef444422",color:"#ef4444",marginRight:8}}>aws CLI 미설치</span>}
              <span onClick={()=>{setS3Open(false);setS3Form(null);setS3Detail(null);}} style={{cursor:"pointer",color:"var(--text-secondary)",fontSize:18,padding:"0 4px"}}>✕</span>
            </div>
            {/* Tabs */}
            <div style={{display:"flex",gap:4,padding:"8px 12px",borderBottom:"1px solid var(--border)",background:"var(--bg-primary)"}}>
              {[{k:"items",l:"항목 ("+s3Items.length+")"},{k:"add",l:"+ 추가"},{k:"history",l:"이력"},{k:"aws",l:"AWS 설정"}].map(t=>(
                <span key={t.k} onClick={()=>{setS3Tab(t.k);if(t.k==="add")setS3Form({id:"",kind:"db",target:"",s3_url:"",command:"sync",extra_args:"",endpoint_url:"",interval_min:0,enabled:true});}} style={{padding:"5px 12px",borderRadius:5,fontSize:11,cursor:"pointer",fontWeight:s3Tab===t.k?700:500,background:s3Tab===t.k?"var(--accent-glow)":"transparent",color:s3Tab===t.k?"var(--accent)":"var(--text-secondary)"}}>{t.l}</span>
              ))}
            </div>
            <div style={{flex:1,overflow:"auto",padding:"12px 16px"}}>
              {/* ITEMS tab */}
              {s3Tab==="items"&&<>
                {s3Items.length===0?<div style={{padding:30,textAlign:"center",color:"var(--text-secondary)",fontSize:12}}>설정된 S3 동기화 항목이 없습니다. <b>+ 추가</b> 를 클릭해 생성하세요.</div>
                :<table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                  <thead><tr style={{background:"var(--bg-secondary)"}}>
                    {["","타겟","종류","S3 URL","명령","주기","다음","마지막","동작"].map(h=>(
                      <th key={h} style={{padding:"6px 8px",textAlign:"left",fontSize:10,fontWeight:700,color:"var(--text-secondary)",borderBottom:"1px solid #555",whiteSpace:"nowrap"}}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {s3Items.map(it=>{
                      const st=it.status||{};const s=st.last_status||"never";
                      const badge={ok:{c:"#22c55e",bg:"#22c55e22",t:"OK"},error:{c:"#ef4444",bg:"#ef444422",t:"ERR"},running:{c:"#f59e0b",bg:"#f59e0b22",t:"RUN"},never:{c:"#94a3b8",bg:"#94a3b822",t:"—"}}[s]||{c:"#94a3b8",bg:"#94a3b822",t:s};
                      const isRunning=it.is_running||s==="running";
                      return(<tr key={it.id} style={{borderBottom:"1px solid #555",opacity:it.enabled===false?0.5:1}}>
                        <td style={{padding:"6px 8px"}}><span style={{fontSize:9,padding:"2px 6px",borderRadius:3,background:badge.bg,color:badge.c,fontWeight:700,fontFamily:"monospace"}}>{badge.t}</span></td>
                        <td style={{padding:"6px 8px",fontFamily:"monospace",fontWeight:600}}>{it.target}</td>
                        <td style={{padding:"6px 8px",fontSize:10,color:"var(--text-secondary)"}}>{it.kind}</td>
                        <td style={{padding:"6px 8px",fontFamily:"monospace",fontSize:10,maxWidth:220,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={it.s3_url}>{it.s3_url}</td>
                        <td style={{padding:"6px 8px",fontSize:10}}>{it.command}</td>
                        <td style={{padding:"6px 8px",fontSize:10}}>{Number(it.interval_min)>0?it.interval_min+"분":"수동"}</td>
                        <td style={{padding:"6px 8px",fontSize:10,color:isRunning?"#f59e0b":"var(--text-secondary)"}}>{isRunning?"실행 중…":s3FmtETA(it)}</td>
                        <td style={{padding:"6px 8px",fontSize:10,color:"var(--text-secondary)"}}>
                          {st.last_end?<span title={"exit="+st.last_exit_code+" dur="+st.last_duration_sec+"s"}>{st.last_end.slice(5,16).replace("T"," ")}</span>:"-"}
                          {st.last_output_tail&&<span onClick={()=>setS3Detail({id:it.id,tail:st.last_output_tail,cmd:it.s3_url,exit:st.last_exit_code})} style={{marginLeft:4,cursor:"pointer",color:"var(--accent)"}}>로그</span>}
                        </td>
                        <td style={{padding:"6px 8px",whiteSpace:"nowrap"}}>
                          <button disabled={isRunning} onClick={()=>s3Run(it.id)} style={{padding:"3px 8px",borderRadius:3,border:"none",background:isRunning?"#94a3b8":"var(--accent)",color:"#fff",fontSize:10,cursor:isRunning?"default":"pointer",marginRight:3}}>▶ 실행</button>
                          <button onClick={()=>{setS3Form({...it});setS3Tab("add");}} style={{padding:"3px 8px",borderRadius:3,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:10,cursor:"pointer",marginRight:3}}>수정</button>
                          <button onClick={()=>s3Delete(it.id)} style={{padding:"3px 8px",borderRadius:3,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",fontSize:10,cursor:"pointer"}}>✕</button>
                        </td>
                      </tr>);
                    })}
                  </tbody>
                </table>}
              </>}
              {/* ADD/EDIT tab */}
              {s3Tab==="add"&&s3Form&&<div style={{maxWidth:620}}>
                <div style={{fontSize:12,fontWeight:700,color:"var(--accent)",marginBottom:10}}>{s3Form.id?"수정: "+s3Form.id:"새 S3 동기화 항목"}</div>
                <div style={{display:"grid",gridTemplateColumns:"120px 1fr",rowGap:10,columnGap:10,fontSize:11,alignItems:"center"}}>
                  <label>종류</label>
                  <div style={{display:"flex",gap:6}}>
                    {["db","root_parquet"].map(k=>(
                      <span key={k} onClick={()=>setS3Form(f=>({...f,kind:k,target:"",command:k==="root_parquet"?"cp":"sync"}))} style={{padding:"4px 10px",borderRadius:4,fontSize:11,cursor:"pointer",fontWeight:s3Form.kind===k?700:500,background:s3Form.kind===k?"var(--accent-glow)":"var(--bg-hover)",color:s3Form.kind===k?"var(--accent)":"var(--text-secondary)",border:"1px solid "+(s3Form.kind===k?"var(--accent)":"var(--border)")}}>{k}</span>
                    ))}
                  </div>
                  <label>타겟</label>
                  <select value={s3Form.target} onChange={e=>setS3Form(f=>({...f,target:e.target.value}))} style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11}}>
                    <option value="">— {s3Form.kind} 선택 —</option>
                    {(s3Form.kind==="db"?s3Avail.dbs:s3Avail.root_parquets).map(x=><option key={x.name} value={x.name}>{x.name}</option>)}
                  </select>
                  <label>S3 URL</label>
                  <input value={s3Form.s3_url} onChange={e=>setS3Form(f=>({...f,s3_url:e.target.value}))} placeholder={s3Form.kind==="db"?"s3://bucket/prefix/INLINE/":"s3://bucket/prefix/file.parquet"} style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,fontFamily:"monospace"}}/>
                  <label>명령</label>
                  <div style={{display:"flex",gap:6}}>
                    {["sync","cp"].map(c=>{
                      const disabled=c==="sync"&&s3Form.kind==="root_parquet";
                      return(<span key={c} onClick={()=>!disabled&&setS3Form(f=>({...f,command:c}))} style={{padding:"4px 10px",borderRadius:4,fontSize:11,cursor:disabled?"not-allowed":"pointer",opacity:disabled?0.4:1,fontWeight:s3Form.command===c?700:500,background:s3Form.command===c?"var(--accent-glow)":"var(--bg-hover)",color:s3Form.command===c?"var(--accent)":"var(--text-secondary)",border:"1px solid "+(s3Form.command===c?"var(--accent)":"var(--border)")}}>{c}</span>);
                    })}
                  </div>
                  <label>엔드포인트 URL</label>
                  <input value={s3Form.endpoint_url||""} onChange={e=>setS3Form(f=>({...f,endpoint_url:e.target.value}))} placeholder="(선택) https://s3.internal.company:9000" style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,fontFamily:"monospace"}}/>
                  <label>추가 인자</label>
                  <input value={s3Form.extra_args} onChange={e=>setS3Form(f=>({...f,extra_args:e.target.value}))} placeholder="--exclude '*.tmp' --delete --size-only" style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,fontFamily:"monospace"}}/>
                  <label>주기 (분)</label>
                  <div style={{display:"flex",gap:6,alignItems:"center"}}>
                    <input type="number" min={0} max={10080} value={s3Form.interval_min} onChange={e=>setS3Form(f=>({...f,interval_min:Number(e.target.value||0)}))} style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,width:100}}/>
                    <span style={{fontSize:10,color:"var(--text-secondary)"}}>0 = 수동 전용. 예: 60 = 매시간, 1440 = 매일</span>
                  </div>
                  <label>활성화</label>
                  <label style={{display:"flex",alignItems:"center",gap:6,cursor:"pointer"}}><input type="checkbox" checked={s3Form.enabled!==false} onChange={e=>setS3Form(f=>({...f,enabled:e.target.checked}))} style={{width:14,height:14,accentColor:"var(--accent)"}}/><span style={{fontSize:11}}>예약 + 수동 실행</span></label>
                </div>
                <div style={{marginTop:14,padding:10,background:"var(--bg-secondary)",borderRadius:6,fontSize:10,fontFamily:"monospace",color:"var(--text-secondary)",lineHeight:1.5}}>
                  <div style={{color:"var(--accent)",fontWeight:700,marginBottom:4}}># 미리보기 (dry):</div>
                  aws s3 {s3Form.command} {s3Form.s3_url||"s3://..."} {"{DB_BASE}/"+(s3Form.target||"TARGET")} {s3Form.endpoint_url?"--endpoint-url "+s3Form.endpoint_url+" ":""}{s3Form.extra_args}
                </div>
                <div style={{display:"flex",gap:8,marginTop:16}}>
                  <button onClick={()=>s3Save(s3Form)} style={{padding:"8px 18px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontWeight:700,fontSize:12,cursor:"pointer"}}>저장</button>
                  <button onClick={()=>{setS3Form(null);setS3Tab("items");}} style={{padding:"8px 16px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>취소</button>
                </div>
                <div style={{marginTop:14,fontSize:10,color:"var(--text-secondary)",lineHeight:1.6}}>
                  <b>허용 플래그:</b> --delete --exact-timestamps --dryrun --size-only --quiet --no-progress --recursive --only-show-errors --no-verify-ssl<br/>
                  <b>값이 있는 플래그:</b> --exclude VAL --include VAL --storage-class VAL --sse VAL --endpoint-url URL --profile NAME --region REGION --ca-bundle PATH<br/>
                  <b>참고:</b> 타겟 경로는 항상 DB_BASE 하위. sync 는 디렉토리 전용입니다.<br/><b>엔드포인트 URL:</b> 위 전용 필드 사용, 또는 <b>Admin → AWS Config</b> 에서 전역 자격/엔드포인트 설정.
                </div>
              </div>}
              {/* HISTORY tab */}
              {s3Tab==="history"&&<>
                {s3Hist.length===0?<div style={{padding:30,textAlign:"center",color:"var(--text-secondary)",fontSize:12}}>이력이 아직 없습니다.</div>
                :<table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                  <thead><tr style={{background:"var(--bg-secondary)"}}>
                    {["시간","항목","상태","종료코드","소요시간","명령"].map(h=>(<th key={h} style={{padding:"6px 8px",textAlign:"left",fontSize:10,fontWeight:700,color:"var(--text-secondary)",borderBottom:"1px solid #555"}}>{h}</th>))}
                  </tr></thead>
                  <tbody>
                    {s3Hist.map((h,i)=>(<tr key={i} style={{borderBottom:"1px solid #555"}}>
                      <td style={{padding:"5px 8px",fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace",whiteSpace:"nowrap"}}>{(h.timestamp||"").slice(5,19).replace("T"," ")}</td>
                      <td style={{padding:"5px 8px",fontSize:10,fontFamily:"monospace"}}>{h.id}</td>
                      <td style={{padding:"5px 8px"}}><span style={{fontSize:9,padding:"2px 6px",borderRadius:3,background:h.status==="ok"?"#22c55e22":"#ef444422",color:h.status==="ok"?"#22c55e":"#ef4444",fontWeight:700}}>{h.status}</span></td>
                      <td style={{padding:"5px 8px",fontSize:10,fontFamily:"monospace"}}>{h.exit_code??"-"}</td>
                      <td style={{padding:"5px 8px",fontSize:10}}>{h.duration_sec!=null?h.duration_sec+"s":"-"}</td>
                      <td style={{padding:"5px 8px",fontSize:10,fontFamily:"monospace",color:"var(--text-secondary)",maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={h.cmd||h.error||""}>{h.cmd||h.error||"-"}</td>
                    </tr>))}
                  </tbody>
                </table>}
              </>}
              {/* AWS tab — v8.4.3 단위기능 페이지 철학: Admin 에서 이관됨 */}
              {s3Tab==="aws"&&<AwsPanel user={user} compact={true} />}
            </div>
          </div>
          {/* Detail log overlay */}
          {s3Detail&&<>
            <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.5)",zIndex:100}} onClick={()=>setS3Detail(null)}/>
            <div style={{position:"fixed",top:"50%",left:"50%",transform:"translate(-50%,-50%)",width:"min(700px,90vw)",maxHeight:"70vh",background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:10,zIndex:101,display:"flex",flexDirection:"column"}}>
              <div style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center"}}>
                <span style={{flex:1,fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{s3Detail.id} — exit={s3Detail.exit}</span>
                <span onClick={()=>setS3Detail(null)} style={{cursor:"pointer",fontSize:16,color:"var(--text-secondary)"}}>✕</span>
              </div>
              <pre style={{flex:1,overflow:"auto",margin:0,padding:12,fontSize:10,fontFamily:"monospace",color:"var(--text-primary)",background:"var(--bg-primary)",whiteSpace:"pre-wrap",wordBreak:"break-all"}}>{s3Detail.tail||"(출력 없음)"}</pre>
            </div>
          </>}
        </>}
      </>}
    </div>);
}
