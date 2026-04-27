import { useState, useEffect, useRef } from "react";
import BrandLogo from "../components/BrandLogo";
import { postJson } from "../lib/api";
const B="#ea580c",M="#f97316",L="#fb923c",D="#9a3412",BK="#171717",W="#fff7ed",PK="#fda4af",G="#fbbf24";

// v8.3.3: PF_HOME / PixelGlyph / HomeBrandLogo extracted to shared ../components/BrandLogo.jsx.
// Home uses <BrandLogo size="home" version={ver}/>; nav uses <BrandLogo size="nav"/> (see App.jsx).


const BASE_PX=[[2,5,B],[2,6,B],[2,7,B],[2,8,B],[2,9,B],[2,10,B],[3,4,B],[3,5,M],[3,6,M],[3,7,M],[3,8,M],[3,9,M],[3,10,M],[3,11,B],[4,3,B],[4,4,M],[4,5,L],[4,6,L],[4,7,L],[4,8,L],[4,9,L],[4,10,L],[4,11,M],[4,12,B],[5,3,B],[5,4,M],[5,5,L],[5,6,L],[5,7,L],[5,8,L],[5,9,L],[5,10,L],[5,11,M],[5,12,B],[8,3,B],[8,4,PK],[8,5,L],[8,6,L],[8,7,L],[8,8,L],[8,9,L],[8,10,L],[8,11,PK],[8,12,B],[9,3,B],[9,4,M],[9,5,L],[9,6,L],[9,7,BK],[9,8,BK],[9,9,L],[9,10,L],[9,11,M],[9,12,B],[10,3,B],[10,4,M],[10,5,M],[10,6,M],[10,7,M],[10,8,M],[10,9,M],[10,10,M],[10,11,M],[10,12,B],[11,4,B],[11,5,B],[11,6,B],[11,7,B],[11,8,B],[11,9,B],[11,10,B],[11,11,B],[12,5,B],[12,6,B],[12,9,B],[12,10,B],[13,5,D],[13,6,D],[13,9,D],[13,10,D],[0,7,G],[1,7,G],[0,8,G],[1,8,G]];
const EO=[[6,3,B],[6,4,M],[6,5,W],[6,6,BK],[6,7,L],[6,8,L],[6,9,W],[6,10,BK],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,W],[7,6,BK],[7,7,L],[7,8,L],[7,9,W],[7,10,BK],[7,11,M],[7,12,B]];
const EC=[[6,3,B],[6,4,M],[6,5,L],[6,6,L],[6,7,L],[6,8,L],[6,9,L],[6,10,L],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,BK],[7,6,BK],[7,7,L],[7,8,L],[7,9,BK],[7,10,BK],[7,11,M],[7,12,B]];
const AD=[[7,1,M],[7,2,M],[8,1,B],[7,13,M],[7,14,M],[8,14,B]];
const AW=[[7,1,M],[7,2,M],[8,1,B],[5,13,M],[5,14,G],[6,13,M],[6,14,B]];
function Holli({size=72}){const[fr,setFr]=useState("idle");const t=useRef(null);useEffect(()=>{const loop=()=>{t.current=setTimeout(()=>{if(Math.random()<0.6){setFr("blink");setTimeout(()=>{setFr("idle");loop();},150);}else{setFr("wave");setTimeout(()=>{setFr("idle");loop();},600);}},1500+Math.random()*2500);};loop();return()=>clearTimeout(t.current);},[]);const px=[...BASE_PX,...(fr==="blink"?EC:EO),...(fr==="wave"?AW:AD)];return(<div style={{animation:fr==="idle"?"holBob 2s ease-in-out infinite":"none"}}><style>{`@keyframes holBob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}@keyframes holBlink{0%,100%{opacity:1}50%{opacity:0}}`}</style><svg width={size} height={size} viewBox="0 0 16 16" style={{imageRendering:"pixelated"}}>{px.map(([r,c,color],i)=><rect key={i} x={c} y={r} width={1} height={1} fill={color}/>)}</svg></div>);}
function Cli({cmd,output,delay=0}){const line=`> flow ${cmd}`;const parts=[{text:">",color:"#f97316"},{text:" flow ",color:"#737373"},{text:cmd,color:"#e5e5e5"}];const[show,setShow]=useState(delay===0);const[typedLen,setTypedLen]=useState(0);const[done,setDone]=useState(false);useEffect(()=>{if(delay){const t=setTimeout(()=>setShow(true),delay);return()=>clearTimeout(t);}},[delay]);useEffect(()=>{if(!show)return;setTypedLen(0);setDone(false);let i=0;const iv=setInterval(()=>{i++;setTypedLen(i);if(i>=line.length){clearInterval(iv);setTimeout(()=>setDone(true),100);}},30);return()=>clearInterval(iv);},[show,line]);if(!show)return null;let remain=typedLen;return(<div style={{marginBottom:4,fontFamily:"'JetBrains Mono',monospace",fontSize:13,lineHeight:1.7}}>{parts.map((p,idx)=>{const s=p.text.slice(0,Math.max(0,Math.min(p.text.length,remain)));remain-=s.length;return s?<span key={idx} style={{color:p.color}}>{s}</span>:null;})}{!done&&<span style={{display:"inline-block",width:8,height:14,background:"#f97316",marginLeft:2,animation:"holBlink 0.6s step-end infinite"}}/>}{done&&output&&<div style={{color:"#a3a3a3",paddingLeft:20,fontSize:12}}>{output}</div>}</div>);}
function WelcomeType({name}){const full=name.toUpperCase()+"_";const[len,setLen]=useState(0);useEffect(()=>{const t=setTimeout(()=>{let i=0;const iv=setInterval(()=>{i++;setLen(i);if(i>=full.length)clearInterval(iv);},70);return()=>clearInterval(iv);},1200);return()=>clearTimeout(t);},[full]);return(<span><span style={{color:"#e5e5e5",fontWeight:700}}>{full.slice(0,len)}</span></span>);}
function Card({icon,title,desc,tag,onClick,width=220}){return(<div onClick={onClick} onMouseEnter={e=>{e.currentTarget.style.borderColor="#f97316";e.currentTarget.style.background="#f9731610";}} onMouseLeave={e=>{e.currentTarget.style.borderColor="var(--border,#333)";e.currentTarget.style.background="var(--bg-card,#2a2a2a)";}} style={{background:"var(--bg-card,#2a2a2a)",borderRadius:12,padding:"20px 24px",cursor:onClick?"pointer":"default",border:"1px solid var(--border,#333)",transition:"all 0.2s",position:"relative",width,boxSizing:"border-box"}}>{tag&&<span style={{position:"absolute",top:12,right:12,fontSize:9,fontWeight:700,padding:"2px 6px",borderRadius:3,background:"#f9731622",color:"#f97316",fontFamily:"monospace",textTransform:"uppercase"}}>{tag}</span>}<div style={{fontSize:28,marginBottom:10}}>{icon}</div><div style={{fontSize:14,fontWeight:700,color:"var(--text-primary,#e5e5e5)",marginBottom:6,fontFamily:"'JetBrains Mono',monospace"}}>{title}</div><div style={{fontSize:12,color:"var(--text-secondary,#a3a3a3)",lineHeight:1.6}}>{desc}</div></div>);}

// Feature guide content shown to users (non-admin) instead of changelog
const FEATURE_GUIDES={
  filebrowser:{icon:"📂",title:"파일 브라우저",steps:["좌측 사이드바에서 DB 선택","하위 Product/파일 선택 시 데이터 자동 로드","SQL 입력창에 필터 입력 (예: PRODUCT_TYPE == 'A', LOT_ID LIKE '%ABC%')","컬럼 선택 → CSV 다운로드 버튼"]},
  dashboard:{icon:"📊",title:"대시보드",steps:["데이터 소스 선택 (DB / Root Parquet / Product)","차트 타입: scatter / line / bar / pie / binning","X/Y 컬럼 선택 + 필터 SQL 입력","Days 옵션으로 기간 제한, binning 은 bin_count/bin_width 조정"]},
  splittable:{icon:"🗂️",title:"스플릿 테이블",steps:["Product 선택 → Root Lot + Wafer IDs 입력 → 검색","Plan 입력 모드: 편집 클릭 후 셀 클릭하여 계획값 입력","셀 색: 회색(없음) / 주황(plan만) / 파스텔(actual) / 초록(match) / 빨강(mismatch)","이력 탭에서 변경 이력 확인"]},
  tracker:{icon:"📋",title:"트래커",steps:["이슈 게시판 — 제목 + 본문 + 이미지 업로드","Lot/Wafer 범위 지정 (Excel 붙여넣기 지원)","댓글 + 중첩 답글 + 이미지","Gantt 뷰로 전체 진행 현황 확인"]},
  inform:{icon:"📢",title:"인폼 로그",steps:["제품/lot 선택 후 인폼 등록","SplitTable 스냅샷 자동 첨부 확인","댓글 스레드와 담당자 흐름 추적","필요 시 메일 미리보기 후 발송"]},
  meeting:{icon:"🗓",title:"회의관리",steps:["회의 선택 또는 신규 회의 생성","아젠다/회의록/결정사항 입력","액션아이템과 달력 연동 확인","필요 시 메일로 회의록 공유"]},
  calendar:{icon:"📅",title:"변경점 관리",steps:["월별 변경 일정 확인","카테고리별 이벤트 필터","회의 액션/결정사항 연동 확인","상태(pending/in_progress/done) 관리"]},
  ettime:{icon:"⏱️",title:"ET 레포트",steps:["lot/root_lot_id 기준 조회","fab_lot_id + step별 ET 패키지 확인","상세 breakdown 표 확인","CSV/PDF 리포트 다운로드"]},
  waferlayout:{icon:"🧭",title:"WF Layout",steps:["제품별 wafer layout 불러오기","shot/chip/TEG 배치 확인","edge shot 후보 검토","layout 저장 및 재검증"]},
  tablemap:{icon:"🗺️",title:"테이블 맵",steps:["DB 간 관계 그래프 조회","노드 더블클릭 → 상세 정보","관계선 drag-drop 으로 편집"]},
  ml:{icon:"🧠",title:"ML 분석",steps:["소스/타깃 컬럼 선택","상관/학습 기반 중요도 확인","공정 window 및 원인 분석 확인","결과를 차트와 표로 비교"]},
  devguide:{icon:"📖",title:"개발 가이드",steps:["아키텍처 다이어그램","API 엔드포인트 문서","Gotchas / 코드 규칙"]},
};

// v8.1.7: localStorage cache for instant first-paint of version block
const VCACHE_KEY="hol_home_version_v1";
function readVerCache(){try{const s=localStorage.getItem(VCACHE_KEY);return s?JSON.parse(s):null;}catch{return null;}}
function writeVerCache(v){try{localStorage.setItem(VCACHE_KEY,JSON.stringify(v));}catch{}}

const FLOWI_TOKEN_KEY="flowi_llm_token_v1";
function readFlowiToken(){try{return sessionStorage.getItem(FLOWI_TOKEN_KEY)||"";}catch{return"";}}
function writeFlowiToken(v){try{v?sessionStorage.setItem(FLOWI_TOKEN_KEY,v):sessionStorage.removeItem(FLOWI_TOKEN_KEY);}catch{}}

function FlowiConsole({onActiveChange}){
  const[token,setToken]=useState(()=>readFlowiToken());
  const[active,setActive]=useState(false);
  const[prompt,setPrompt]=useState("");
  const[verifying,setVerifying]=useState(false);
  const[verifyMsg,setVerifyMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const[result,setResult]=useState(null);
  const[lastPrompt,setLastPrompt]=useState("");
  const[err,setErr]=useState("");
  const tokenInputRef=useRef(null);
  const promptRef=useRef(null);

  useEffect(()=>{if(active&&promptRef.current)setTimeout(()=>promptRef.current?.focus(),30);},[active]);
  useEffect(()=>{if(typeof onActiveChange==="function")onActiveChange(active);},[active,onActiveChange]);

  const activate=()=>{
    if(verifying)return false;
    const tk=(token||"").trim();
    if(!tk){setActive(false);setErr("Flowi 토큰을 입력해주세요.");return false;}
    setVerifying(true);setErr("");setVerifyMsg("");
    postJson("/api/llm/flowi/verify",{token:tk})
      .then(d=>{
        if(!d?.ok)throw new Error(d?.message||d?.error||"LLM 연결 확인 실패");
        writeFlowiToken(tk);setActive(true);setVerifyMsg(d.message||"확인완료");
      })
      .catch(e=>{setActive(false);setVerifyMsg("");setErr(e.message||String(e));})
      .finally(()=>setVerifying(false));
    return true;
  };
  const clear=()=>{setToken("");setActive(false);writeFlowiToken("");setResult(null);setErr("");setVerifyMsg("");};
  const ask=()=>{
    const q=(prompt||"").trim();
    const tk=(token||"").trim();
    if(!q){setErr("질문을 입력해주세요.");return;}
    if(!tk){setActive(false);setErr("Flowi 토큰을 입력하면 실행할 수 있습니다.");return;}
    writeFlowiToken(tk);setActive(true);setBusy(true);setErr("");setLastPrompt(q);
    postJson("/api/llm/flowi/chat",{prompt:q,token:tk,product:"",max_rows:12})
      .then(d=>setResult(d)).catch(e=>setErr(e.message||String(e))).finally(()=>setBusy(false));
  };
  const examples=[
    "PRODA0 root_lot_id A10001 wafer_id 07 ET ETA100010 VTH median wf별 몇이야?",
    "PRODA0 root_lot_id A10001 knob 어떻게돼",
    "PRODB root_lot_id B1000 wafer_id 12 knob M1 어떻게돼",
  ];
  const tokenMask=token?"*".repeat((token||"").length):"";
  return(<section style={{marginTop:12,fontFamily:"'JetBrains Mono',monospace"}}>
    <form onSubmit={e=>{e.preventDefault();activate();}} style={{margin:0}}>
      <div style={{display:"flex",alignItems:"center",gap:7,minWidth:0,fontSize:13,lineHeight:1.7,flexWrap:"wrap"}}>
        <span style={{color:"#f97316"}}>{">"}</span>
        <span style={{color:"#737373",whiteSpace:"nowrap"}}>LLM TOKEN :</span>
        <span onClick={()=>tokenInputRef.current?.focus()} style={{position:"relative",display:"inline-flex",alignItems:"center",minWidth:12,minHeight:18,maxWidth:"100%",cursor:"text",color:"#e5e5e5"}}>
          <input ref={tokenInputRef} type="text" value={token} onChange={e=>{setToken(e.target.value);if(!e.target.value){setActive(false);setVerifyMsg("");writeFlowiToken("");}}}
            autoComplete="off" aria-label="LLM token"
            style={{position:"absolute",inset:0,width:"100%",height:"100%",opacity:0,border:"none",background:"transparent",color:"transparent",caretColor:"transparent",outline:"none",fontSize:13,fontFamily:"'JetBrains Mono',monospace"}}/>
          <span aria-hidden="true" style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere",fontSize:13,fontFamily:"'JetBrains Mono',monospace"}}>{tokenMask}</span>
          <span aria-hidden="true" style={{display:"inline-block",width:8,height:14,background:"#f97316",animation:"holBlink 1s step-end infinite",marginLeft:tokenMask?2:0}}/>
        </span>
        {verifying&&<span style={{fontSize:10,color:"#fbbf24",fontFamily:"monospace"}}>checking...</span>}
        {verifyMsg&&<span style={{fontSize:10,color:"#22c55e",fontFamily:"monospace"}}>{verifyMsg}</span>}
        {active&&<button type="button" onClick={clear} aria-label="clear llm token"
          style={{padding:"1px 6px",borderRadius:5,border:"1px solid #333",background:"transparent",color:"#737373",fontSize:10,fontFamily:"monospace",cursor:"pointer"}}>CLEAR</button>}
      </div>
    </form>
    {active&&<form onSubmit={e=>{e.preventDefault();ask();}} style={{margin:"10px 0 0"}}>
      <div style={{display:"flex",alignItems:"stretch",gap:8,minWidth:0}}>
        <span style={{color:"#f97316"}}>{">"}</span>
        <textarea ref={promptRef} value={prompt} onChange={e=>setPrompt(e.target.value)}
          placeholder="PRODA0 root_lot_id A10001 wafer_id 07 ET ETA100010 VTH median wf별 몇이야?"
          aria-label="Flowi prompt"
          rows={5}
          onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key==="Enter")ask();}}
          style={{flex:1,minWidth:0,padding:"10px 12px",borderRadius:8,border:"1px solid #333",background:"#141414",color:"#e5e5e5",fontSize:13,lineHeight:1.55,fontFamily:"'JetBrains Mono',monospace",outline:"none",resize:"vertical",boxSizing:"border-box"}}/>
        <button type="submit" disabled={busy||!prompt.trim()} aria-label="run flowi prompt"
          style={{alignSelf:"stretch",padding:"0 12px",borderRadius:8,border:"none",background:busy||!prompt.trim()?"#404040":"#f97316",color:"#111",fontSize:11,fontFamily:"monospace",fontWeight:800,cursor:busy||!prompt.trim()?"default":"pointer"}}>{busy?"RUNNING":"RUN"}</button>
      </div>
    </form>}
    {active&&<div style={{display:"flex",gap:6,marginTop:8,flexWrap:"wrap"}}>
      {examples.map(ex=><button key={ex} onClick={()=>setPrompt(ex)}
        style={{padding:"4px 8px",borderRadius:5,border:"1px solid #333",background:"#151515",color:"#a3a3a3",fontSize:10,fontFamily:"monospace",cursor:"pointer",maxWidth:"100%",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{ex}</button>)}
    </div>}
    <FlowiResult busy={busy} error={err} result={result} prompt={lastPrompt}/>
  </section>);
}

function FlowiResult({busy,error,result,prompt}){
  if(busy)return <div style={{marginTop:10,fontSize:12,color:"#a3a3a3",fontFamily:"monospace"}}>local tools + llm 처리 중...</div>;
  if(error)return <div style={{marginTop:10,padding:"9px 10px",borderRadius:6,background:"#7f1d1d33",color:"#fca5a5",fontSize:12,border:"1px solid #7f1d1d"}}>{error}</div>;
  if(!result)return null;
  const tool=result.tool||{};
  const table=tool.table&&Array.isArray(tool.table.rows)&&Array.isArray(tool.table.columns)?tool.table:null;
  const rows=Array.isArray(tool.rows)?tool.rows:[];
  const knobs=Array.isArray(tool.knobs)?tool.knobs:[];
  return(<div style={{marginTop:12,borderTop:"1px solid #262626",paddingTop:10}}>
    <div style={{whiteSpace:"pre-wrap",fontSize:12,lineHeight:1.65,color:"#d4d4d4"}}>{result.answer||"응답이 없습니다."}</div>
    <div style={{display:"flex",gap:6,marginTop:8,flexWrap:"wrap"}}>
      {tool.intent&&<span style={{fontSize:10,color:"#a3a3a3",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{tool.intent}</span>}
      {result.llm&&<span style={{fontSize:10,color:result.llm.used?"#22c55e":"#737373",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{result.llm.used?"llm used":"local result"}</span>}
    </div>
    <FlowiFeedback result={result} tool={tool} prompt={prompt}/>
    {table&&<FlowiDataTable table={table}/>}
    {rows.length>0&&<div style={{marginTop:10,overflowX:"auto"}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:11,fontFamily:"monospace"}}>
        <thead><tr>{["product","step","item","wf","median","mean","n"].map(h=><th key={h} style={{textAlign:"left",padding:"5px 6px",borderBottom:"1px solid #333",color:"#a3a3a3"}}>{h}</th>)}</tr></thead>
        <tbody>{rows.slice(0,12).map((r,i)=><tr key={i}>
          <td style={FR_TD}>{r.product||""}</td><td style={FR_TD}>{r.step_id||""}</td><td style={FR_TD}>{r.item_id||""}</td><td style={FR_TD}>{r.wafer_id||""}</td>
          <td style={FR_TD}>{r.median??""}</td><td style={FR_TD}>{r.mean??""}</td><td style={FR_TD}>{r.count??""}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {knobs.length>0&&<div style={{marginTop:10,display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:8}}>
      {knobs.slice(0,8).map(k=><div key={k.knob} style={{border:"1px solid #333",borderRadius:6,padding:"8px 10px",background:"#151515"}}>
        <div style={{fontSize:11,fontWeight:800,color:"#e5e5e5",marginBottom:4}}>{k.display_name||k.knob}</div>
        {(k.values||[]).slice(0,3).map(v=><div key={String(v.value)} style={{fontSize:10,color:"#a3a3a3",fontFamily:"monospace",lineHeight:1.55}}>{String(v.value)} · {v.count}wf{Array.isArray(v.wafers)&&v.wafers.length?" · "+v.wafers.slice(0,8).join(","):""}</div>)}
      </div>)}
    </div>}
  </div>);
}
const FR_TD={padding:"5px 6px",borderBottom:"1px solid #262626",color:"#d4d4d4",whiteSpace:"nowrap"};

function FlowiFeedback({result,tool,prompt}){
  const[rating,setRating]=useState("");
  const[note,setNote]=useState("");
  const[msg,setMsg]=useState("");
  const send=(nextRating=rating)=>{
    const r=nextRating||"neutral";
    setRating(r);setMsg("");
    postJson("/api/llm/flowi/feedback",{
      rating:r,
      prompt:prompt||"",
      answer:result?.answer||"",
      intent:tool?.intent||"",
      note:note||"",
    }).then(()=>setMsg("피드백 저장됨")).catch(e=>setMsg(e.message||"저장 실패"));
  };
  return(<div style={{marginTop:8,display:"flex",alignItems:"center",gap:6,flexWrap:"wrap"}}>
    <button type="button" onClick={()=>send("up")} style={{padding:"3px 8px",borderRadius:5,border:"1px solid #333",background:rating==="up"?"#22c55e22":"transparent",color:rating==="up"?"#22c55e":"#a3a3a3",fontSize:10,fontFamily:"monospace",cursor:"pointer"}}>좋아요</button>
    <button type="button" onClick={()=>send("down")} style={{padding:"3px 8px",borderRadius:5,border:"1px solid #333",background:rating==="down"?"#ef444422":"transparent",color:rating==="down"?"#fca5a5":"#a3a3a3",fontSize:10,fontFamily:"monospace",cursor:"pointer"}}>개선 필요</button>
    <input value={note} onChange={e=>setNote(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")send(rating||"neutral");}} placeholder="워크플로우 개선 의견"
      style={{flex:"1 1 180px",minWidth:160,padding:"4px 7px",borderRadius:5,border:"1px solid #333",background:"#141414",color:"#d4d4d4",fontSize:10,outline:"none"}}/>
    <button type="button" onClick={()=>send(rating||"neutral")} style={{padding:"3px 8px",borderRadius:5,border:"1px solid #333",background:"#171717",color:"#a3a3a3",fontSize:10,fontFamily:"monospace",cursor:"pointer"}}>의견 저장</button>
    {msg&&<span style={{fontSize:10,color:msg.includes("실패")?"#fca5a5":"#22c55e",fontFamily:"monospace"}}>{msg}</span>}
  </div>);
}

function FlowiDataTable({table}){
  const cols=table.columns||[];
  const rows=table.rows||[];
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,overflow:"hidden",background:"#121212"}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:8,padding:"8px 10px",borderBottom:"1px solid #2a2a2a",background:"#171717"}}>
      <div style={{fontSize:11,fontWeight:800,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace"}}>{table.title||"Flowi table"}</div>
      <div style={{fontSize:10,color:"#737373",fontFamily:"monospace"}}>{rows.length}{table.total&&table.total!==rows.length?` / ${table.total}`:""} rows</div>
    </div>
    <div style={{overflow:"auto",maxHeight:360}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:11,fontFamily:"monospace"}}>
        <thead><tr>{cols.map(c=><th key={c.key} style={{position:"sticky",top:0,zIndex:1,textAlign:"left",padding:"7px 8px",borderBottom:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",whiteSpace:"nowrap"}}>{c.label||c.key}</th>)}</tr></thead>
        <tbody>{rows.map((r,i)=><tr key={i}>
          {cols.map(c=><td key={c.key} style={{padding:"6px 8px",borderBottom:"1px solid #262626",color:c.key==="wafer_id"||String(c.key).includes("STI")?"#e5e5e5":"#c7c7c7",whiteSpace:"nowrap",fontWeight:String(c.key).startsWith("KNOB")||String(c.label).includes("KNOB")?800:500}}>{r[c.key]??""}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
  </div>);
}

export default function My_Home({onNavigate,user}){
  // v8.1.7: initial value from cache → no spinner on 2nd+ visit
  const[version,setVersion]=useState(()=>readVerCache());

  // v8.1.7: fetch version.json (small, fast) independently — no Promise.all blocking
  useEffect(()=>{
    // cache:'no-store' — version.json 은 설치 직후 자주 바뀌는데 브라우저가
    // last-modified/etag 이전의 응답을 그대로 재사용해서 새 브랜드가 반영 안
    // 되는 현상 방지. 파일 작음 (~10KB) 이라 부하 무시 가능.
    fetch("/version.json",{cache:"no-store"}).then(r=>r.json()).then(v=>{
      if(v){setVersion(v);writeVerCache(v);}
    }).catch(()=>{});
  },[]);

  // v8.1.7: no loading gate — render immediately with cached or fallback values
  const ver=version?.version||"...",codename=version?.codename||"",changelog=version?.changelog||[];
  const nav=(k)=>onNavigate&&onNavigate(k);
  const isAdmin=user?.role==="admin";
  const userTabs=isAdmin?"__all__":(user?.tabs||"");
  const hasTab=(k)=>userTabs==="__all__"||userTabs.split(",").map(s=>s.trim()).filter(Boolean).includes(k);
  const[flowiConnected,setFlowiConnected]=useState(false);

  // v8.7.4: TABS 순서와 동일하게 카드 정렬. 홈 카드에 inform/meeting/calendar 포함.
  // v8.8.5: 카드별 tag(개별 버전) 제거 — 통합 버전(v8.8.5) 만 의미 있음.
  const ALL_CARDS=[
    {key:"filebrowser",icon:"📂",title:"파일 탐색기",desc:"Parquet 탐색, SQL 필터, CSV 다운로드"},
    {key:"dashboard",  icon:"📊",title:"대시보드",desc:"동적 차트, 산점도, 추세"},
    {key:"splittable", icon:"🗂️",title:"스플릿 테이블",desc:"Plan vs actual, 공유 추적"},
    {key:"tracker",    icon:"📋",title:"이슈 추적",desc:"이슈 게시판, Lot/Wafer 추적"},
    {key:"inform",     icon:"📢",title:"인폼 로그",desc:"모듈 인폼 + 스레드 + 이미지"},
    {key:"meeting",    icon:"🗓",title:"회의관리",desc:"차수·반복·아젠다·회의록"},
    {key:"calendar",   icon:"📅",title:"변경점 관리",desc:"달력·카테고리·회의 연동"},
    {key:"ettime",     icon:"⏱️",title:"ET 레포트",desc:"fab_lot_id + step 기준 elapsed 분석"},
    {key:"waferlayout",icon:"🧭",title:"WF Layout",desc:"제품별 wafer/shot/chip layout 검토"},
    {key:"tablemap",   icon:"🗺️",title:"테이블 맵",desc:"DB 관계 그래프",adminOnly:true},
    {key:"ml",         icon:"🧠",title:"ML 분석",desc:"Inline_ET 요약, 공정 윈도우, 원인 분석",tag:"BETA"},
    {key:"admin",      icon:"⚙️",title:"관리자",desc:"사용자, 권한, 모니터",adminOnly:true},
    {key:"devguide",   icon:"📖",title:"개발자 가이드",desc:"아키텍처, API 레퍼런스"},
  ];
  const visibleCards=ALL_CARDS.filter(c=>(!c.adminOnly||isAdmin)&&hasTab(c.key));

  return(<div style={{minHeight:"calc(100vh - 48px)",padding:"32px 32px 96px",background:"var(--bg-primary,#1a1a1a)",color:"var(--text-primary,#e5e5e5)",fontFamily:"'Pretendard',sans-serif",maxWidth:1040,margin:"0 auto"}}>
    {/* v8.3.3: Home brand logo — shared BrandLogo.jsx, size="home" retains .home-brand-logo marker. */}
    <BrandLogo size="home" version={ver}/>
    {/* Terminal header */}
    <div style={{background:"#111",borderRadius:12,border:"1px solid #333",overflow:"hidden",marginBottom:28,boxShadow:"0 2px 20px rgba(0,0,0,0.4)"}}>
      <div style={{display:"flex",alignItems:"center",gap:8,padding:"8px 14px",background:"#1a1a1a",borderBottom:"1px solid #333"}}>
        <div style={{display:"flex",gap:6}}><div style={{width:10,height:10,borderRadius:"50%",background:"#ef4444"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#fbbf24"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#22c55e"}}/></div>
        <span style={{fontSize:11,color:"#525252",fontFamily:"monospace",marginLeft:6}}>flow ~ v{ver}</span>
      </div>
      <div style={{display:"flex",gap:20,padding:"20px 24px",alignItems:"flex-start"}}>
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:4,flexShrink:0}}><Holli size={72}/><span style={{fontSize:9,color:"#f97316",fontFamily:"monospace",letterSpacing:"0.12em",fontWeight:700}}>flow-i</span>{flowiConnected&&<span style={{fontSize:8,color:"#22c55e",fontFamily:"monospace",border:"1px solid #22c55e66",borderRadius:999,padding:"1px 6px",letterSpacing:0}}>connected</span>}</div>
        <div style={{flex:1,paddingTop:4}}>
          <Cli cmd="--version" output={`v${ver} "${codename}"`}/>
          <div style={{marginTop:6,fontFamily:"'JetBrains Mono',monospace",fontSize:13}}><span style={{color:"#f97316"}}>{">"}</span><span style={{color:"#737373"}}> WELCOME </span><WelcomeType name={user?.username||"user"}/></div>
          <FlowiConsole onActiveChange={setFlowiConnected}/>
        </div>
      </div>
    </div>

    {/* Permission-filtered cards, centered */}
    {visibleCards.length>0?<div style={{display:"grid",gridTemplateColumns:"repeat(4, minmax(0, 1fr))",gap:14,justifyContent:"start",marginBottom:32}}>
      {visibleCards.map(c=><Card key={c.key} icon={c.icon} title={c.title} desc={c.desc} tag={c.tag} onClick={()=>nav(c.key)} width="100%"/>)}
    </div>:<div style={{padding:"40px 20px",textAlign:"center",color:"var(--text-secondary)",fontSize:13,marginBottom:32}}>
      사용 가능한 탭이 없습니다. 관리자에게 권한을 요청해주세요.
    </div>}

    {/* Admin: changelog | User: feature guide */}
    {isAdmin?<div style={{background:"var(--bg-secondary,#262626)",borderRadius:12,border:"1px solid var(--border,#333)",overflow:"hidden"}}>
      <div style={{padding:"14px 20px",borderBottom:"1px solid var(--border,#333)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
        <span style={{fontSize:13,fontWeight:700,fontFamily:"'JetBrains Mono',monospace",color:"var(--accent,#f97316)"}}>{">"} changelog</span>
        <span style={{fontSize:11,color:"var(--text-secondary)"}}>{changelog.length} 개 릴리스 · 관리자 뷰</span>
      </div>
      <div style={{padding:"0 20px 16px"}}>{changelog.map((rel,ri)=>{const tC={feature:["#f97316","#f9731622"],fix:["#ef4444","#ef444422"],improve:["#3b82f6","#3b82f622"],init:["#a855f7","#a855f722"]};const tc=tC[rel.tag]||tC.feature;return(<div key={ri} style={{paddingTop:16,paddingBottom:12,borderBottom:ri<changelog.length-1?"1px solid var(--border,#333)":"none"}}>
        <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:8,flexWrap:"wrap"}}>
          <span style={{fontFamily:"monospace",fontSize:14,fontWeight:800,color:tc[0]}}>v{rel.version}</span>
          <span style={{fontSize:10,fontWeight:700,padding:"2px 6px",borderRadius:3,background:tc[1],color:tc[0],textTransform:"uppercase"}}>{rel.tag}</span>
          <span style={{fontSize:13,fontWeight:600}}>{rel.title}</span>
          <span style={{fontSize:11,color:"var(--text-secondary)",marginLeft:"auto"}}>{rel.date}</span>
        </div>
        {(rel.changes||[]).map((c,ci)=>(<div key={ci} style={{display:"flex",gap:6,fontSize:12,lineHeight:1.7,color:"var(--text-secondary,#a3a3a3)",paddingLeft:6}}><span style={{color:"#f97316"}}>-</span><span>{c}</span></div>))}
      </div>);})}</div>
    </div>:<div style={{background:"var(--bg-secondary,#262626)",borderRadius:12,border:"1px solid var(--border,#333)",overflow:"hidden"}}>
      <div style={{padding:"14px 20px",borderBottom:"1px solid var(--border,#333)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
        <span style={{fontSize:13,fontWeight:700,fontFamily:"'JetBrains Mono',monospace",color:"var(--accent,#f97316)"}}>{">"} 사용 방법</span>
        <span style={{fontSize:11,color:"var(--text-secondary)"}}>권한있는 기능 가이드</span>
      </div>
      <div style={{padding:"6px 20px 16px"}}>
        {visibleCards.filter(c=>FEATURE_GUIDES[c.key]).map((c,i,arr)=>{const g=FEATURE_GUIDES[c.key];return(<div key={c.key} style={{paddingTop:16,paddingBottom:12,borderBottom:i<arr.length-1?"1px solid var(--border,#333)":"none"}}>
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:10,cursor:"pointer"}} onClick={()=>nav(c.key)}>
            <span style={{fontSize:24}}>{g.icon}</span>
            <span style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",fontFamily:"'JetBrains Mono',monospace"}}>{g.title}</span>
            <span style={{fontSize:10,color:"var(--accent)",fontFamily:"monospace",marginLeft:"auto"}}>→ 열기</span>
          </div>
          <ol style={{margin:0,paddingLeft:28,fontSize:12,lineHeight:1.8,color:"var(--text-secondary)"}}>
            {g.steps.map((s,si)=><li key={si} style={{marginBottom:2}}>{s}</li>)}
          </ol>
        </div>);})}
        {visibleCards.filter(c=>FEATURE_GUIDES[c.key]).length===0&&<div style={{padding:"20px 0",textAlign:"center",color:"var(--text-secondary)",fontSize:12}}>권한있는 기능이 없습니다. 아래 관리자 문의 버튼으로 문의해주세요.</div>}
      </div>
    </div>}

    {/* v8.3.1: Contact 섹션 — 메시지 탭/팝업 대체.
         v8.4.5: Contact 는 우상단 ✉ 버튼(ContactButton)으로 이관 — 홈 하단 섹션 제거. */}
  </div>);
}

// ─── Contact section (replaces nav Messages tab + unread popup) ────────────────
function fmtT(iso){if(!iso)return"";try{const d=new Date(iso);const mm=String(d.getMonth()+1).padStart(2,"0");const dd=String(d.getDate()).padStart(2,"0");const H=String(d.getHours()).padStart(2,"0");const M=String(d.getMinutes()).padStart(2,"0");return `${mm}-${dd} ${H}:${M}`;}catch{return(iso||"").slice(0,16).replace("T"," ");}}
const SEC_WRAP={marginTop:40,background:"var(--bg-secondary,#262626)",borderRadius:12,border:"1px solid var(--border,#333)",overflow:"hidden"};
const SEC_HEADER={padding:"14px 20px",borderBottom:"1px solid var(--border,#333)",display:"flex",justifyContent:"space-between",alignItems:"center"};
const SEC_TITLE={fontSize:13,fontWeight:700,fontFamily:"'JetBrains Mono',monospace",color:"var(--accent,#f97316)"};

function ContactSection({user}){
  const isAdmin=user?.role==="admin";
  return(<section data-testid="home-contact-section" id="home-contact-section" style={SEC_WRAP}>
    <div style={SEC_HEADER}>
      <span style={SEC_TITLE}>{"> contact"}</span>
      <span style={{fontSize:11,color:"var(--text-secondary)"}}>{isAdmin?"관리자 — 1:1 문의함 + 전체 공지":"관리자에게 문의 보내기"}</span>
    </div>
    {isAdmin?<AdminContact user={user}/>:<UserContact user={user}/>}
  </section>);
}

// ── User side: inline 1:1 inquiry + collapsible history ──
function UserContact({user}){
  const uname=user?.username||"";
  const[thread,setThread]=useState({messages:[]});const[text,setText]=useState("");
  const[sending,setSending]=useState(false);const[showHistory,setShowHistory]=useState(false);
  const[notices,setNotices]=useState([]);
  const load=()=>{
    fetch("/api/messages/thread?username="+encodeURIComponent(uname)).then(r=>r.json())
      .then(d=>{setThread(d||{messages:[]});fetch("/api/messages/mark_read",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:uname})}).catch(()=>{});})
      .catch(()=>{});
    fetch("/api/messages/notices?username="+encodeURIComponent(uname)).then(r=>r.json())
      .then(d=>setNotices(d.notices||[])).catch(()=>{});
  };
  useEffect(()=>{if(uname)load();},[uname]);
  const send=()=>{
    const v=(text||"").trim();if(!v||sending)return;
    if(v.length>5000){alert("최대 5000자까지 입력 가능합니다.");return;}
    setSending(true);
    fetch("/api/messages/send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:uname,text:v})})
      .then(r=>r.json()).then(()=>{setText("");load();}).catch(e=>alert("전송 실패: "+(e.message||e))).finally(()=>setSending(false));
  };
  const markNoticeRead=(id)=>{
    fetch("/api/messages/notice_read",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:uname,ids:[id]})})
      .then(()=>setNotices(p=>p.map(x=>x.id===id?{...x,read:true}:x))).catch(()=>{});
  };
  const msgs=thread.messages||[];
  const unreadNotices=notices.filter(n=>!n.read);
  return(<div data-testid="contact-user" style={{padding:"16px 20px"}}>
    {/* 최신 공지 pinned to top */}
    {unreadNotices.length>0&&<div style={{marginBottom:16}}>
      <div style={{fontSize:10,color:"var(--accent)",fontFamily:"monospace",marginBottom:6,fontWeight:700}}>📢 새 공지사항 ({unreadNotices.length})</div>
      {unreadNotices.slice(0,3).map(n=>(
        <div key={n.id} onClick={()=>markNoticeRead(n.id)} style={{padding:"10px 12px",borderRadius:6,background:"var(--accent-glow,rgba(249,115,22,0.1))",border:"1px solid var(--border)",marginBottom:6,cursor:"pointer"}}>
          <div style={{fontSize:12,fontWeight:700,color:"var(--text-primary)"}}>{n.title||"(제목 없음)"}</div>
          {n.body&&<div style={{fontSize:11,color:"var(--text-secondary)",marginTop:3,whiteSpace:"pre-wrap",lineHeight:1.5}}>{n.body}</div>}
          <div style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:4}}>{n.author} · {fmtT(n.created_at)}</div>
        </div>))}
    </div>}

    {/* Send-to-admin input */}
    <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:6,fontFamily:"monospace"}}>💬 관리자에게 문의</div>
    <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
      <textarea data-testid="contact-user-input" value={text} onChange={e=>setText(e.target.value)} disabled={sending}
        onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")send();}}
        placeholder="버그 리포트 / 기능 요청 / 권한 요청 등 (Cmd/Ctrl + Enter 전송)" rows={3}
        style={{flex:1,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
      <button data-testid="contact-user-send" onClick={send} disabled={sending||!text.trim()}
        style={{padding:"8px 18px",borderRadius:6,border:"none",background:sending||!text.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:sending||!text.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>
        {sending?"…":"보내기"}
      </button>
    </div>
    <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:4,textAlign:"right"}}>{text.length} / 5000</div>

    {/* Collapsible history */}
    <div style={{marginTop:18,borderTop:"1px solid var(--border)",paddingTop:10}}>
      <div onClick={()=>setShowHistory(!showHistory)} style={{cursor:"pointer",fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace",display:"flex",alignItems:"center",gap:6}}>
        <span>{showHistory?"▼":"▶"}</span><span>과거 대화 ({msgs.length})</span>
      </div>
      {showHistory&&<div data-testid="contact-user-history" style={{marginTop:10,maxHeight:300,overflowY:"auto",padding:"4px 2px"}}>
        {msgs.length===0&&<div style={{textAlign:"center",color:"var(--text-secondary)",fontSize:11,padding:20}}>아직 대화가 없습니다.</div>}
        {msgs.map(m=>{const mine=m.from===uname;return(
          <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:8}}>
            <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
              <div style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?"나":m.from} · {fmtT(m.created_at)}</div>
              <div style={{padding:"6px 10px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:12,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
            </div>
          </div>);})}
      </div>}
    </div>
  </div>);
}

// ── Admin side: two tabs only — [📨 1:1 문의함] [📢 전체 공지].
function AdminContact({user}){
  const[sub,setSub]=useState("inbox");
  const tS=(a)=>({padding:"7px 14px",fontSize:11,cursor:"pointer",fontWeight:a?700:500,borderRadius:5,background:a?"var(--accent-glow)":"transparent",color:a?"var(--accent)":"var(--text-secondary)",fontFamily:"'JetBrains Mono',monospace"});
  return(<div data-testid="contact-admin" style={{padding:"14px 20px"}}>
    <div style={{display:"flex",gap:6,marginBottom:14}}>
      <div data-testid="contact-admin-tab-inbox" style={tS(sub==="inbox")} onClick={()=>setSub("inbox")}>📨 1:1 문의함</div>
      <div data-testid="contact-admin-tab-notices" style={tS(sub==="notices")} onClick={()=>setSub("notices")}>📢 전체 공지</div>
    </div>
    {sub==="inbox"&&<AdminContactInbox user={user}/>}
    {sub==="notices"&&<AdminContactNotices user={user}/>}
  </div>);
}

function AdminContactInbox({user}){
  const admin=user?.username||"";
  const[threads,setThreads]=useState([]);const[sel,setSel]=useState("");const[thr,setThr]=useState(null);
  const[reply,setReply]=useState("");const[sending,setSending]=useState(false);
  const loadThreads=()=>fetch("/api/messages/admin/threads?admin="+encodeURIComponent(admin)).then(r=>r.json()).then(d=>setThreads(d.threads||[])).catch(()=>{});
  const loadThread=(u)=>fetch("/api/messages/admin/thread?admin="+encodeURIComponent(admin)+"&user="+encodeURIComponent(u)).then(r=>r.json()).then(setThr).catch(()=>{});
  useEffect(()=>{if(admin)loadThreads();},[admin]);
  useEffect(()=>{if(sel)loadThread(sel);else setThr(null);},[sel]);
  const open=(u)=>{setSel(u);fetch("/api/messages/admin/mark_read",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,to_user:u})}).then(loadThreads).catch(()=>{});};
  const send=()=>{const v=(reply||"").trim();if(!v||!sel||sending)return;if(v.length>5000){alert("최대 5000자");return;}setSending(true);
    fetch("/api/messages/admin/reply",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,to_user:sel,text:v})})
      .then(r=>r.json()).then(()=>{setReply("");loadThread(sel);loadThreads();}).catch(e=>alert("실패: "+(e.message||e))).finally(()=>setSending(false));};
  const totalUnread=threads.reduce((s,t)=>s+(t.unread_for_admin||0),0);
  return(<div style={{display:"flex",gap:12,minHeight:340}}>
    <div style={{width:240,background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",display:"flex",flexDirection:"column",flexShrink:0}}>
      <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:6}}>
        <span style={{fontSize:11,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>스레드</span>
        <span style={{fontSize:9,color:"var(--text-secondary)"}}>{threads.length}·미확인 {totalUnread}</span>
        <div style={{flex:1}}/>
        <span onClick={loadThreads} style={{fontSize:11,cursor:"pointer",color:"var(--text-secondary)"}} title="새로고침">↻</span>
      </div>
      <div style={{flex:1,overflowY:"auto",maxHeight:340}}>
        {threads.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:11}}>수신 없음</div>}
        {threads.map(t=>(
          <div key={t.user} onClick={()=>open(t.user)} style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",cursor:"pointer",background:sel===t.user?"var(--accent-glow)":(t.unread_for_admin>0?"rgba(249,115,22,0.05)":"transparent")}}>
            <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:2}}>
              {t.unread_for_admin>0&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--accent)",flexShrink:0}}/>}
              <span style={{fontSize:12,fontWeight:t.unread_for_admin>0?700:500,color:"var(--text-primary)",fontFamily:"monospace",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.user}</span>
              {t.unread_for_admin>0&&<span style={{fontSize:9,fontWeight:700,padding:"1px 5px",borderRadius:3,background:"var(--accent)",color:"#fff"}}>{t.unread_for_admin}</span>}
            </div>
            <div style={{fontSize:10,color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",lineHeight:1.4}}>{t.last_from?`[${t.last_from}] `:""}{t.last_preview||"(비어 있음)"}</div>
          </div>))}
      </div>
    </div>
    <div style={{flex:1,background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",display:"flex",flexDirection:"column",minWidth:0,minHeight:340}}>
      {!sel&&<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-secondary)",fontSize:12,padding:20}}>← 스레드를 선택하세요</div>}
      {sel&&thr&&<>
        <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>💬 {sel}</span>
          <span style={{fontSize:10,color:"var(--text-secondary)"}}>{(thr.messages||[]).length} 메시지</span>
        </div>
        <div style={{flex:1,overflowY:"auto",padding:12,maxHeight:280}}>
          {(thr.messages||[]).map(m=>{const mine=m.from===admin;return(
            <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:8}}>
              <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
                <div style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?`나 (${m.from})`:m.from} · {fmtT(m.created_at)}</div>
                <div style={{padding:"6px 10px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:12,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
              </div>
            </div>);})}
        </div>
        <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)"}}>
          <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
            <textarea value={reply} onChange={e=>setReply(e.target.value)} disabled={sending}
              onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")send();}}
              placeholder={`${sel} 에게 답장 (Cmd/Ctrl+Enter 전송)`} rows={2}
              style={{flex:1,padding:"7px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:"var(--text-primary)",fontSize:12,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
            <button onClick={send} disabled={sending||!reply.trim()}
              style={{padding:"7px 16px",borderRadius:6,border:"none",background:sending||!reply.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:sending||!reply.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>{sending?"…":"답장"}</button>
          </div>
        </div>
      </>}
    </div>
  </div>);
}

function AdminContactNotices({user}){
  const admin=user?.username||"";
  const[notices,setNotices]=useState([]);
  const[title,setTitle]=useState("");const[body,setBody]=useState("");const[sending,setSending]=useState(false);
  const loadNotices=()=>fetch("/api/messages/admin/notices?admin="+encodeURIComponent(admin)).then(r=>r.json()).then(d=>setNotices(d.notices||[])).catch(()=>{});
  useEffect(()=>{if(admin){loadNotices();}},[admin]);
  const publish=()=>{
    const t=title.trim(),b=body.trim();if(!t&&!b){alert("제목 또는 본문을 입력하세요.");return;}
    if(sending)return;setSending(true);
    fetch("/api/messages/admin/notice_create",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({author:admin,title:t,body:b})})
      .then(r=>r.json()).then(()=>{setTitle("");setBody("");loadNotices();alert("전체 공지가 발행되었습니다.");})
      .catch(e=>alert("실패: "+(e.message||e))).finally(()=>setSending(false));
  };
  const del=(id)=>{if(!confirm("공지사항을 삭제하시겠습니까?"))return;
    fetch("/api/messages/admin/notice_delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,id})}).then(loadNotices).catch(e=>alert(e.message));};
  const S={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,outline:"none",fontFamily:"'Pretendard',sans-serif",boxSizing:"border-box"};
  return(<div>
    <div style={{background:"var(--bg-primary)",border:"1px solid var(--accent)",borderRadius:8,padding:14,marginBottom:14}}>
      <div data-testid="contact-admin-mode-all" style={{display:"flex",alignItems:"center",gap:6,fontSize:12,marginBottom:10,color:"var(--accent)",fontFamily:"'JetBrains Mono',monospace",fontWeight:700}}>
        📢 전체 공지 작성 — 모든 사용자에게 발행
      </div>
      <input data-testid="contact-admin-notice-title" value={title} onChange={e=>setTitle(e.target.value)} placeholder="제목 (최대 200자)" maxLength={200} style={{...S,marginBottom:8,fontWeight:600}}/>
      <textarea data-testid="contact-admin-notice-body" value={body} onChange={e=>setBody(e.target.value)} placeholder="공지 본문 (최대 5000자)" rows={4} style={{...S,marginBottom:8,resize:"vertical"}}/>
      <div style={{display:"flex",alignItems:"center"}}>
        <span style={{fontSize:10,color:"var(--text-secondary)"}}>{title.length}/200 · {body.length}/5000</span>
        <div style={{flex:1}}/>
        <button data-testid="contact-admin-notice-publish" onClick={publish} disabled={sending||(!title.trim()&&!body.trim())}
          style={{padding:"7px 18px",borderRadius:5,border:"none",background:sending||(!title.trim()&&!body.trim())?"#94a3b8":"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:sending?"default":"pointer"}}>
          {sending?"…":"전체 발행"}
        </button>
      </div>
    </div>

    {/* 기존 공지 리스트 */}
    <div style={{fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:6}}>기존 공지사항 ({notices.length})</div>
    <div style={{background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",maxHeight:320,overflowY:"auto"}}>
      {notices.length===0&&<div style={{padding:24,textAlign:"center",color:"var(--text-secondary)",fontSize:12}}>등록된 공지사항이 없습니다.</div>}
      {notices.map(n=>(
        <div key={n.id} style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:3}}>
            <span style={{fontSize:12,fontWeight:700,color:"var(--text-primary)",flex:1}}>{n.title||"(제목 없음)"}</span>
            <span style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace"}}>{fmtT(n.created_at)}</span>
            <span style={{fontSize:10,color:"var(--accent)",fontFamily:"monospace"}}>👁 {n.read_count||0}/{n.total_recipients||"?"}</span>
            <span onClick={()=>del(n.id)} style={{cursor:"pointer",color:"#ef4444",fontSize:11}}>🗑</span>
          </div>
          {n.body&&<div style={{fontSize:11,color:"var(--text-secondary)",lineHeight:1.5,whiteSpace:"pre-wrap"}}>{n.body}</div>}
          <div style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:3}}>by {n.author}</div>
        </div>))}
    </div>
  </div>);
}
