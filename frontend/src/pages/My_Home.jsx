import { useState, useEffect, useRef } from "react";
import BrandLogo from "../components/BrandLogo";
import { dl, postJson, sf } from "../lib/api";
import { isAdmin as isAdminUser, isPageAdmin } from "../lib/permissions";
import { toast } from "../components/Toast";
import { PageHeader, statusPalette } from "../components/UXKit";
import { FlowPlotlyChart } from "../components/PlotlyChart";
import SplitTableSnapshotView from "../components/SplitTableSnapshotView";
const B="#ea580c",M="#f97316",L="#fb923c",D="#9a3412",BK="#171717",W="#fff7ed",PK="#fda4af",G="#fbbf24";
const HOME_UI={
  accent:statusPalette.warn.fg,
  accentBg:statusPalette.warn.bg,
  ok:statusPalette.ok.fg,
  okBg:statusPalette.ok.bg,
  bad:statusPalette.bad.fg,
  badBg:statusPalette.bad.bg,
  text:"var(--text-primary,#e5e5e5)",
  textSub:"var(--text-secondary,#a3a3a3)",
  textDim:"#737373",
  textSoft:"#d4d4d4",
  border:"var(--border,#333)",
  borderStrong:"#333",
  borderSoft:"#2a2a2a",
  card:"var(--bg-card,#2a2a2a)",
  panel:"#111",
  panelSoft:"#151515",
  terminal:"#171717",
};
const FLOWI_CLIENT_TIMEOUT_MS=105000;
const FLOWI_CLIENT_TIMEOUT_S=Math.round(FLOWI_CLIENT_TIMEOUT_MS/1000);

// v8.3.3: PF_HOME / PixelGlyph / HomeBrandLogo extracted to shared ../components/BrandLogo.jsx.
// Home uses <BrandLogo size="home"/>; nav uses <BrandLogo size="nav"/> (see App.jsx).


const BASE_PX=[[2,5,B],[2,6,B],[2,7,B],[2,8,B],[2,9,B],[2,10,B],[3,4,B],[3,5,M],[3,6,M],[3,7,M],[3,8,M],[3,9,M],[3,10,M],[3,11,B],[4,3,B],[4,4,M],[4,5,L],[4,6,L],[4,7,L],[4,8,L],[4,9,L],[4,10,L],[4,11,M],[4,12,B],[5,3,B],[5,4,M],[5,5,L],[5,6,L],[5,7,L],[5,8,L],[5,9,L],[5,10,L],[5,11,M],[5,12,B],[8,3,B],[8,4,PK],[8,5,L],[8,6,L],[8,7,L],[8,8,L],[8,9,L],[8,10,L],[8,11,PK],[8,12,B],[9,3,B],[9,4,M],[9,5,L],[9,6,L],[9,7,BK],[9,8,BK],[9,9,L],[9,10,L],[9,11,M],[9,12,B],[10,3,B],[10,4,M],[10,5,M],[10,6,M],[10,7,M],[10,8,M],[10,9,M],[10,10,M],[10,11,M],[10,12,B],[11,4,B],[11,5,B],[11,6,B],[11,7,B],[11,8,B],[11,9,B],[11,10,B],[11,11,B],[12,5,B],[12,6,B],[12,9,B],[12,10,B],[13,5,D],[13,6,D],[13,9,D],[13,10,D],[0,7,G],[1,7,G],[0,8,G],[1,8,G]];
const EO=[[6,3,B],[6,4,M],[6,5,W],[6,6,BK],[6,7,L],[6,8,L],[6,9,W],[6,10,BK],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,W],[7,6,BK],[7,7,L],[7,8,L],[7,9,W],[7,10,BK],[7,11,M],[7,12,B]];
const EC=[[6,3,B],[6,4,M],[6,5,L],[6,6,L],[6,7,L],[6,8,L],[6,9,L],[6,10,L],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,BK],[7,6,BK],[7,7,L],[7,8,L],[7,9,BK],[7,10,BK],[7,11,M],[7,12,B]];
const AD=[[7,1,M],[7,2,M],[8,1,B],[7,13,M],[7,14,M],[8,14,B]];
const AW=[[7,1,M],[7,2,M],[8,1,B],[5,13,M],[5,14,G],[6,13,M],[6,14,B]];
function Holli({size=72}){const[fr,setFr]=useState("idle");const t=useRef(null);useEffect(()=>{const loop=()=>{t.current=setTimeout(()=>{if(Math.random()<0.6){setFr("blink");setTimeout(()=>{setFr("idle");loop();},150);}else{setFr("wave");setTimeout(()=>{setFr("idle");loop();},600);}},1500+Math.random()*2500);};loop();return()=>clearTimeout(t.current);},[]);const px=[...BASE_PX,...(fr==="blink"?EC:EO),...(fr==="wave"?AW:AD)];return(<div style={{animation:fr==="idle"?"holBob 2s ease-in-out infinite":"none"}}><style>{`@keyframes holBob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}@keyframes holBlink{0%,100%{opacity:1}50%{opacity:0}}`}</style><svg width={size} height={size} viewBox="0 0 16 16" style={{imageRendering:"pixelated"}}>{px.map(([r,c,color],i)=><rect key={i} x={c} y={r} width={1} height={1} fill={color}/>)}</svg></div>);}
function Cli({cmd,output,delay=0}){const line=`> flow ${cmd}`;const parts=[{text:">",color:HOME_UI.accent},{text:" flow ",color:HOME_UI.textDim},{text:cmd,color:HOME_UI.text}];const[show,setShow]=useState(delay===0);const[typedLen,setTypedLen]=useState(0);const[done,setDone]=useState(false);useEffect(()=>{if(delay){const t=setTimeout(()=>setShow(true),delay);return()=>clearTimeout(t);}},[delay]);useEffect(()=>{if(!show)return;setTypedLen(0);setDone(false);let i=0;const iv=setInterval(()=>{i++;setTypedLen(i);if(i>=line.length){clearInterval(iv);setTimeout(()=>setDone(true),100);}},30);return()=>clearInterval(iv);},[show,line]);if(!show)return null;let remain=typedLen;return(<div style={{marginBottom:4,fontFamily:"'JetBrains Mono',monospace",fontSize:14,lineHeight:1.7}}>{parts.map((p,idx)=>{const s=p.text.slice(0,Math.max(0,Math.min(p.text.length,remain)));remain-=s.length;return s?<span key={idx} style={{color:p.color}}>{s}</span>:null;})}{!done&&<span style={{display:"inline-block",width:8,height:14,background:HOME_UI.accent,marginLeft:2,animation:"holBlink 0.6s step-end infinite"}}/>}{done&&output&&<div style={{color:HOME_UI.textSub,paddingLeft:20,fontSize:14}}>{output}</div>}</div>);}
function WelcomeType({name}){const full=`${name}님, 안녕하세요`;const[len,setLen]=useState(0);useEffect(()=>{const t=setTimeout(()=>{let i=0;const iv=setInterval(()=>{i++;setLen(i);if(i>=full.length)clearInterval(iv);},70);return()=>clearInterval(iv);},800);return()=>clearTimeout(t);},[full]);return(<span><span style={{color:"#fff",fontWeight:700}}>{full.slice(0,len)}</span></span>);}
function Card({icon,title,desc,tag,onClick,width=220}){return(<div onClick={onClick} onMouseEnter={e=>{e.currentTarget.style.borderColor=HOME_UI.accent;e.currentTarget.style.background=HOME_UI.accent+"10";}} onMouseLeave={e=>{e.currentTarget.style.borderColor=HOME_UI.border;e.currentTarget.style.background=HOME_UI.card;}} style={{background:HOME_UI.card,borderRadius:12,padding:"20px 24px",cursor:onClick?"pointer":"default",border:`1px solid ${HOME_UI.border}`,transition:"all 0.2s",position:"relative",width,boxSizing:"border-box"}}>{tag&&<span style={{position:"absolute",top:12,right:12,fontSize:14,fontWeight:700,padding:"2px 6px",borderRadius:3,background:HOME_UI.accentBg,color:HOME_UI.accent,fontFamily:"monospace",textTransform:"uppercase"}}>{tag}</span>}<div style={{fontSize:28,marginBottom:10}}>{icon}</div><div style={{fontSize:14,fontWeight:700,color:HOME_UI.text,marginBottom:6,fontFamily:"'JetBrains Mono',monospace"}}>{title}</div><div style={{fontSize:14,color:HOME_UI.textSub,lineHeight:1.6}}>{desc}</div></div>);}

// Feature guide content shown to users (non-admin) instead of release history.
const FEATURE_GUIDES={
  filebrowser:{icon:"📂",title:"파일 브라우저",steps:["좌측 사이드바에서 DB 선택","하위 Product/파일 선택 시 데이터 자동 로드","SQL 입력창에 필터 입력 (예: PRODUCT_TYPE == 'A', LOT_ID LIKE '%ABC%')","컬럼 선택 → CSV 다운로드 버튼"]},
  dashboard:{icon:"📊",title:"대시보드",steps:["데이터 소스 선택 (DB / Root Parquet / Product)","차트 타입: scatter / line / bar / pie / binning","X/Y 컬럼 선택 + 필터 SQL 입력","Days 옵션으로 기간 제한, binning 은 bin_count/bin_width 조정"]},
  splittable:{icon:"🗂️",title:"스플릿 테이블",steps:["Product 선택 → Root Lot + Wafer IDs 입력 → 검색","Plan 입력 모드: 편집 클릭 후 셀 클릭하여 계획값 입력","셀 색: 회색(없음) / 주황(plan만) / 파스텔(actual) / 초록(match) / 빨강(mismatch)","이력 탭에서 변경 이력 확인"]},
  diagnosis:{icon:"🤖",title:"에이전트 설정",steps:["LLM endpoint 상태 확인","Admin LLM profile과 token 설정","연결 테스트 실행"]},
  tracker:{icon:"📋",title:"트래커",steps:["이슈 게시판 — 제목 + 본문 + 이미지 업로드","Lot/Wafer 범위 지정 (Excel 붙여넣기 지원)","댓글 + 중첩 답글 + 이미지","Gantt 뷰로 전체 진행 현황 확인"]},
  inform:{icon:"📢",title:"인폼 로그",steps:["제품/lot 선택 후 인폼 등록","SplitTable 스냅샷 자동 첨부 확인","댓글 스레드와 담당자 흐름 추적","필요 시 메일 미리보기 후 발송"]},
  meeting:{icon:"🗓",title:"회의관리",steps:["회의 선택 또는 신규 회의 생성","아젠다/회의록/결정사항 입력","액션아이템과 달력 연동 확인","필요 시 메일로 회의록 공유"]},
  calendar:{icon:"📅",title:"변경점 관리",steps:["월별 변경 일정 확인","카테고리별 이벤트 필터","회의 액션/결정사항 연동 확인","상태(pending/in_progress/done) 관리"]},
  devguide:{icon:"📖",title:"개발 가이드",steps:["아키텍처 다이어그램","API 엔드포인트 문서","Gotchas / 코드 규칙"]},
};
function shortFlowiVerifyError(value){
  const text=String(value||"").replace(/Bearer\s+[A-Za-z0-9._~+/=-]{12,}/gi,"Bearer <redacted>").replace(/ya29\.[A-Za-z0-9._~+/=-]+/g,"ya29.<redacted>").replace(/sk-[A-Za-z0-9._~+/=-]{12,}/g,"sk-<redacted>").replace(/\s+/g," ").trim();
  return text.length>120?`${text.slice(0,117)}...`:text;
}
function flowiOutputSummaryForContext(result){
  const tool=result?.tool||{};
  const table=tool?.table&&typeof tool.table==="object"?tool.table:{};
  const split=tool?.split_view&&typeof tool.split_view==="object"?tool.split_view:{};
  const chart=tool?.chart_result&&typeof tool.chart_result==="object"?tool.chart_result:(tool?.chart&&typeof tool.chart==="object"?tool.chart:{});
  const blocks=Array.isArray(tool?.blocks)?tool.blocks:[];
  return {
    table:table?.kind?{kind:table.kind||"",title:table.title||"",total:table.total??(Array.isArray(table.rows)?table.rows.length:0)}:{},
    split_view:split?.kind?{kind:split.kind||"",title:split.title||"",total:split.total??(Array.isArray(split.rows)?split.rows.length:0),row_label:split.row_label||""}:{},
    chart:chart?.kind||chart?.status?{kind:chart.kind||chart.status||"",title:chart.title||"",status:chart.status||""}:{},
    blocks:blocks.slice(0,6).map(b=>({kind:b?.kind||"",title:b?.title||""})).filter(b=>b.kind||b.title),
  };
}
function FlowiConsole({onNavigate,user,onActiveChange}){
  const isAdmin=user?.role==="admin";
  const[active,setActive]=useState(false);
  const[connState,setConnState]=useState("idle");
  const[prompt,setPrompt]=useState("");
  const[busy,setBusy]=useState(false);
  const[result,setResult]=useState(null);
  const[lastPrompt,setLastPrompt]=useState("");
  const[err,setErr]=useState("");
  const[modelLabel,setModelLabel]=useState("");
  const[verifyError,setVerifyError]=useState("");
  const[messages,setMessages]=useState([]);
  const[liveStep,setLiveStep]=useState(0);
  const[liveElapsed,setLiveElapsed]=useState(0);
  const[activeChartSessionId,setActiveChartSessionId]=useState("");
  const promptRef=useRef(null);
  const scrollRef=useRef(null);
  const verifySeq=useRef(0);
  const CTX_LIMIT=12000;

  useEffect(()=>{if(active&&promptRef.current)setTimeout(()=>promptRef.current?.focus(),30);},[active]);
  useEffect(()=>{if(active&&scrollRef.current)scrollRef.current.scrollTop=scrollRef.current.scrollHeight;},[active,messages,busy]);
  useEffect(()=>{
    if(!busy){setLiveStep(0);setLiveElapsed(0);return undefined;}
    const started=Date.now();
    const tick=()=>{
      const elapsed=Math.max(0,Math.floor((Date.now()-started)/1000));
      setLiveElapsed(elapsed);
      setLiveStep(elapsed<2?0:elapsed<6?1:elapsed<18?2:3);
    };
    tick();
    const iv=setInterval(tick,1000);
    return()=>clearInterval(iv);
  },[busy]);
  useEffect(()=>{
    let alive=true;
    sf("/api/llm/status").then(d=>{
      if(!alive)return;
      const cfg=d?.config||{};
      const model=String(cfg.model||"").trim();
      setModelLabel(d?.available&&model?model:"");
      if(d&&!d.available)setConnState("unavailable");
    }).catch(()=>{if(alive)setModelLabel("");});
    return()=>{alive=false;};
  },[]);
  const activate=()=>{
    setActive(true);setErr("");setVerifyError("");
    onActiveChange&&onActiveChange(true);
    const seq=++verifySeq.current;
    setConnState("checking");
    postJson("/api/llm/flowi/verify",{token:""})
      .then(d=>{
        if(seq!==verifySeq.current)return;
        const msg=String(d?.message||d?.text||"");
        if(d?.status==="connected"||(d?.ok&&msg.includes("확인완료"))){
          setConnState("connected");
          setVerifyError("");
        }else if(d?.status==="delayed"){
          setConnState("delayed");
          setVerifyError(shortFlowiVerifyError(d?.error||d?.message||"LLM 연결 확인 지연"));
        }else if(d?.status==="unavailable"||d?.unavailable||d?.error==="llm unavailable"){
          setConnState("unavailable");
          setVerifyError("");
        }else{
          setConnState("verify_failed");
          setVerifyError(shortFlowiVerifyError(d?.error||d?.message||"unknown"));
        }
      })
      .catch(e=>{
        if(seq===verifySeq.current){
          setConnState("verify_failed");
          setVerifyError(shortFlowiVerifyError(e?.message||"verify request failed"));
        }
      });
    return true;
  };
  const close=()=>{setActive(false);setErr("");onActiveChange&&onActiveChange(false);};
  const contextMessages=messages.slice(-8).map(m=>{
    const outputSummary=flowiOutputSummaryForContext(m.result);
    return {
      role:m.role,
      prompt:m.prompt||"",
      text:String(m.answer||m.text||"").slice(0,900),
      answer_excerpt:String(m.answer||m.result?.answer||m.text||"").slice(0,600),
      intent:m.intent||m.result?.tool?.intent||"",
      feature:m.result?.tool?.feature||"",
      action:m.result?.tool?.action||"",
      blocked:!!m.result?.tool?.blocked,
      created_record:m.result?.tool?.created_record||null,
      missing:m.result?.tool?.missing||[],
      arguments_choices:m.result?.tool?.arguments_choices||{},
      missing_freetext:m.result?.tool?.missing_freetext||m.result?.missing_freetext||[],
      arguments_partial:m.result?.tool?.arguments_partial||m.result?.tool?.arguments||{},
      last_partial_prompt:m.result?.tool?.last_partial_prompt||m.result?.last_partial_prompt||"",
      walkthrough:m.result?.tool?.walkthrough||{},
      slots:m.result?.tool?.slots||{},
      filters:m.result?.tool?.filters||{},
      table_kind:outputSummary.table?.kind||"",
      split_view_kind:outputSummary.split_view?.kind||"",
      split_view_summary:outputSummary.split_view||{},
      chart_session_id:m.result?.tool?.chart_session_id||m.result?.tool?.chart_result?.chart_session_id||"",
      workflow_state:m.result?.workflow_state||m.result?.tool?.workflow_state||{},
      output_summary:outputSummary,
      pending_prompt:m.result?.tool?.pending_prompt||"",
    };
  });
  const contextText=contextMessages.map(m=>`${m.role}: ${m.prompt||m.text||""} ${m.intent?`(${m.intent})`:""}`).join("\n");
  const contextRemaining=Math.max(0,CTX_LIMIT-String(contextText||"").length-String(prompt||"").length);
  const contextUsed=CTX_LIMIT-contextRemaining;
  const contextPct=Math.max(0,Math.min(100,Math.round(contextRemaining/CTX_LIMIT*100)));
  const ask=(overridePrompt="",options={})=>{
    if(busy)return;
    const q=String(overridePrompt||prompt||"").trim();
    if(!q){setErr("질문을 입력해주세요.");return;}
    const displayText=String(options?.displayText||"").trim()||q;
    if(overridePrompt)setPrompt("");
    const userMsg={id:`u-${Date.now()}`,role:"user",text:displayText,prompt:q,ts:new Date().toISOString()};
    const context={type:"home_flowi_chat",limit_chars:CTX_LIMIT,remaining_chars:contextRemaining,messages:contextMessages,chart_session_id:activeChartSessionId||""};
    setMessages(prev=>[...prev,userMsg]);
    setActive(true);setBusy(true);setErr("");setLastPrompt(q);
    const started=Date.now();
    let endpoint="/api/llm/flowi/chat";
    let body={prompt:q,product:"",max_rows:12,context};
    if(q.toUpperCase().startsWith("FLOWI_EDM_PROPOSE ")){
      endpoint="/api/llm/flowi/edm/propose";
      try{body=JSON.parse(q.slice("FLOWI_EDM_PROPOSE ".length).trim());}
      catch(e){setErr("FLOWI_EDM_PROPOSE JSON parse 실패: "+e.message);setBusy(false);return;}
    }
    const controller=typeof AbortController!=="undefined"?new AbortController():null;
    const timeoutId=controller?setTimeout(()=>controller.abort(),FLOWI_CLIENT_TIMEOUT_MS):null;
    sf(endpoint,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body||{}),
      signal:controller?.signal,
    })
      .then(d=>{
        const enriched={...(d||{}),elapsed_ms:Date.now()-started};
        const sid=enriched?.tool?.chart_session_id||enriched?.tool?.chart_result?.chart_session_id||"";
        if(sid)setActiveChartSessionId(sid);
        setResult(enriched);
        setMessages(prev=>[...prev,{id:`a-${Date.now()}`,role:"assistant",answer:enriched?.answer||"",prompt:q,result:enriched,intent:enriched?.tool?.intent||"",ts:new Date().toISOString()}]);
        setPrompt("");
      }).catch(e=>{
        const timedOut=e?.name==="AbortError";
        const msg=timedOut
          ?`${FLOWI_CLIENT_TIMEOUT_S}초 안에 결과가 없어 요청을 중단했습니다. 조건을 더 좁히거나 같은 질문을 다시 실행해 주세요.`
          :(e.message||String(e));
        const failure={
          ok:false,
          answer:msg,
          elapsed_ms:Date.now()-started,
          tool:{handled:false,blocked:timedOut,intent:timedOut?"client_timeout":"request_error",action:timedOut?"client.timeout":"request.error",feature:"home",answer:msg},
          llm:{available:!!modelLabel,used:false},
        };
        setResult(failure);
        setMessages(prev=>[...prev,{id:`a-${Date.now()}`,role:"assistant",answer:msg,prompt:q,result:failure,intent:failure.tool.intent,ts:new Date().toISOString()}]);
        setErr("");
      }).finally(()=>{
        if(timeoutId)clearTimeout(timeoutId);
        setBusy(false);
      });
  };
  const connLabel=connState==="checking"?"연결확인중":connState==="connected"?"연결":connState==="delayed"?"연결 확인 지연":connState==="verify_failed"?"LLM 확인 실패":connState==="unavailable"?"LLM 미설정":"";
  const connColor=connState==="connected"?HOME_UI.ok:(connState==="checking"||connState==="delayed"||connState==="verify_failed")?HOME_UI.accent:HOME_UI.bad;
  return(<section style={{marginTop:12,fontFamily:"'JetBrains Mono',monospace"}}>
    <style>{`@keyframes flowiPanelWake{0%{opacity:0;transform:translateY(-8px) scaleY(.96)}100%{opacity:1;transform:translateY(0) scaleY(1)}}@keyframes flowiConnBlink{0%,100%{opacity:.45}50%{opacity:1}}`}</style>
    <form onSubmit={e=>{e.preventDefault();activate();}} style={{margin:0}}>
      <div style={{display:"flex",alignItems:"center",gap:7,minWidth:0,fontSize:14,lineHeight:1.7,flexWrap:"wrap"}}>
        <span style={{color:HOME_UI.accent}}>{">"}</span>
        <span style={{color:HOME_UI.textDim,whiteSpace:"nowrap"}}>flow-i</span>
        {active&&connLabel&&<span title={verifyError?`LLM 확인 실패: ${verifyError}`:(modelLabel?`LLM ${modelLabel}`:"LLM 연결 확인")} style={{display:"inline-flex",alignItems:"center",gap:5,color:connColor,border:`1px solid ${connColor}66`,background:`${connColor}14`,borderRadius:999,padding:"1px 8px",fontSize:14,fontFamily:"monospace",fontWeight:800,whiteSpace:"nowrap"}}>
          <span style={{width:6,height:6,borderRadius:"50%",background:connColor,animation:connState==="checking"?"flowiConnBlink .75s ease-in-out infinite":"none"}}/>{connLabel}
        </span>}
        {active&&verifyError&&<span style={{color:HOME_UI.textDim,fontSize:14,fontFamily:"monospace",minWidth:0,maxWidth:420,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
          {verifyError}
        </span>}
        {!active&&<button type="submit" aria-label="start flowi"
          style={{padding:"2px 8px",borderRadius:5,border:`1px solid ${HOME_UI.borderStrong}`,background:HOME_UI.terminal,color:HOME_UI.accent,fontSize:14,fontFamily:"monospace",fontWeight:800,cursor:"pointer"}}>START</button>}
        {active&&<button type="button" onClick={close} aria-label="close flowi"
          style={{padding:"1px 6px",borderRadius:5,border:`1px solid ${HOME_UI.borderStrong}`,background:"transparent",color:HOME_UI.textDim,fontSize:14,fontFamily:"monospace",cursor:"pointer"}}>CLOSE</button>}
      </div>
    </form>
    {active&&<div style={{marginTop:10,border:`1px solid ${HOME_UI.borderSoft}`,borderRadius:10,background:"#101010",overflow:"hidden",animation:"flowiPanelWake .32s ease-out",transformOrigin:"top"}}>
      <div ref={scrollRef} style={{height:messages.length?"clamp(520px, 72vh, 860px)":340,maxHeight:"calc(100vh - 230px)",overflow:"auto",padding:"14px 16px",borderBottom:"1px solid #262626",scrollBehavior:"smooth"}}>
        {messages.length===0&&!busy&&<div style={{height:"100%",display:"flex",alignItems:"center",justifyContent:"center",color:"#d4d4d4",fontSize:14,fontWeight:800,textAlign:"center"}}>
          오늘 어떤 도움을 드릴까요?
        </div>}
        {messages.map(m=>m.role==="user"
          ?<div key={m.id} style={{display:"flex",justifyContent:"flex-end",margin:"0 0 10px"}}>
            <div style={{maxWidth:"92%",background:"#1f130b",border:"1px solid #7c2d12",borderRadius:"10px 10px 2px 10px",padding:"8px 10px",color:"#f5f5f5",fontSize:14,lineHeight:1.55,whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{m.text}</div>
          </div>
          :<div key={m.id} style={{margin:"0 0 16px",maxWidth:"100%"}}>
            <div style={{fontSize:14,color:HOME_UI.textDim,fontFamily:"monospace",marginBottom:4}}>flow-i{isAdmin&&m.intent?` · ${m.intent}`:""}</div>
            <FlowiResult busy={false} error="" result={m.result} prompt={m.prompt} onNavigate={onNavigate} onChoice={ask} embedded isAdmin={isAdmin} activeChartSessionId={activeChartSessionId} onUseChartSession={setActiveChartSessionId}/>
          </div>)}
        {busy&&<FlowiLiveTrace step={liveStep} elapsed={liveElapsed} prompt={lastPrompt}/>}
      </div>
      <form onSubmit={e=>{e.preventDefault();ask();}} style={{margin:0,padding:"10px 10px 10px 0"}}>
      <div style={{display:"flex",alignItems:"stretch",gap:8,minWidth:0}}>
        <span style={{color:HOME_UI.accent}}>{">"}</span>
        <div style={{position:"relative",flex:1,minWidth:0}}>
          <textarea ref={promptRef} value={prompt} onChange={e=>setPrompt(e.target.value)}
            placeholder=""
            aria-label="Flowi prompt"
            rows={5}
            onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();ask();}}}
            style={{width:"100%",minWidth:0,padding:isAdmin?"10px 12px 48px":"10px 12px",borderRadius:8,border:"1px solid #525252",background:"#3a3a3a",color:"#f5f5f5",fontSize:14,lineHeight:1.55,fontFamily:"'JetBrains Mono',monospace",outline:"none",resize:"vertical",boxSizing:"border-box",display:"block"}}/>
          {isAdmin&&<div title="현재 연결 모델과 남은 대화 context 추정치" style={{position:"absolute",right:10,bottom:8,display:"flex",gap:6,alignItems:"center",justifyContent:"flex-end",maxWidth:"calc(100% - 20px)",pointerEvents:"none",fontFamily:"'JetBrains Mono',monospace"}}>
            <span style={{minWidth:0,maxWidth:260,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontSize:14,lineHeight:1.1,color:modelLabel?HOME_UI.textSoft:HOME_UI.textDim,border:`1px solid ${HOME_UI.borderStrong}`,background:"#0f0f0f",borderRadius:999,padding:"6px 9px",fontWeight:900}}>
              MODEL {modelLabel||"미연결"}
            </span>
            <span style={{whiteSpace:"nowrap",fontSize:14,lineHeight:1.1,color:contextPct<20?"#fb923c":HOME_UI.textSoft,border:`1px solid ${contextPct<20?HOME_UI.accent+"66":HOME_UI.borderStrong}`,background:contextPct<20?"#2a1207":"#0f0f0f",borderRadius:999,padding:"6px 9px",fontWeight:900}}>
              CTX {contextUsed.toLocaleString()} / {CTX_LIMIT.toLocaleString()}
            </span>
          </div>}
        </div>
        {busy&&<div aria-live="polite" style={{alignSelf:"center",color:HOME_UI.accent,fontSize:14,fontFamily:"monospace",fontWeight:800,whiteSpace:"nowrap"}}>RUNNING</div>}
      </div>
      </form>
    </div>}
    {err&&<FlowiResult busy={false} error={err} result={null} prompt={lastPrompt} onNavigate={onNavigate} onChoice={ask} isAdmin={isAdmin} activeChartSessionId={activeChartSessionId} onUseChartSession={setActiveChartSessionId}/>}
  </section>);
}

const FLOWI_ACTION_BTN={fontSize:14,color:HOME_UI.accent,fontFamily:"monospace",border:"1px solid #7c2d12",borderRadius:6,padding:"4px 8px",background:"#1f130b",cursor:"pointer",fontWeight:800,whiteSpace:"nowrap"};

function flowiShortText(value,max=140){
  const text=String(value??"").replace(/\s+/g," ").trim();
  return text.length>max?`${text.slice(0,max-1)}...`:text;
}

function flowiUniqueLines(lines,max=6){
  const seen=new Set();
  const out=[];
  (Array.isArray(lines)?lines:[]).forEach(line=>{
    const text=String(line||"").replace(/\s+/g," ").trim();
    if(!text||seen.has(text))return;
    seen.add(text);
    out.push(text);
  });
  return out.slice(0,max);
}

function flowiIsStepIdToken(value){
  return /^[A-Z]{2}\d{6}(?:[A-Z]{1,4})?$/i.test(String(value||"").trim());
}

function flowiIsRootLotToken(value){
  const token=String(value||"").trim();
  return /^[A-Z0-9]{5}$/i.test(token)&&/[A-Z]/i.test(token)&&/\d/.test(token)&&!flowiIsStepIdToken(token);
}

function flowiPromptEntities(prompt){
  const text=String(prompt||"").replace(/\s+/g," ").trim();
  if(!text)return {text:"",rootLot:"",stepId:"",knob:"",hasSplit:false,hasChart:false,hasFab:false,hasFile:false};
  const tokens=[...(text.matchAll(/\b[A-Z0-9][A-Z0-9_.-]*\b/gi))].map(m=>m[0]);
  const stepId=tokens.find(flowiIsStepIdToken)||"";
  const rootLot=tokens
    .find(v=>!String(v).includes(".")&&flowiIsRootLotToken(v))||"";
  const hasSplit=/(split\s*table|split|knob|스플릿|노브)/i.test(text);
  const hasChart=/(chart|plot|scatter|trend|그래프|차트|산점도|추이)/i.test(text);
  const hasFab=/(fab|current\s*location|progress|현재\s*위치|진행\s*상태|공정\s*진행)/i.test(text);
  const hasFile=/(filebrowser|sql|raw\s*data|csv|parquet|파일|원천\s*데이터|로우\s*데이터)/i.test(text);
  const hasMeasurement=/(측정값|값\s*(?:몇|보여|알려)|몇이야|measurement)/i.test(text);
  let knob="";
  if(hasSplit){
    let scope=text;
    if(rootLot){
      const idx=text.toLowerCase().indexOf(rootLot.toLowerCase());
      if(idx>=0)scope=text.slice(idx+rootLot.length).trim();
    }
    const beforeKeyword=scope.match(/^(.{1,90}?)(?=\s*(?:split\s*table|split|knob|스플릿\s*테이블|스플릿테이블|스플릿|노브|\(|보여|찾아|조회|검색|$))/i);
    const raw=beforeKeyword?.[1]||"";
    knob=raw.replace(/\([^)]*\)/g," ")
      .replace(/\b(?:split|table|knob|or|show|find|search)\b/gi," ")
      .replace(/(?:스플릿\s*테이블|스플릿테이블|스플릿|노브)/g," ")
      .replace(/(?:보여줘|보여|찾아줘|찾아|조회해줘|조회|검색해줘|검색)/g," ")
      .replace(/^(?:은|는|이|가|을|를|의|에서|으로|로)\s+/,"")
      .replace(/\s+(?:은|는|이|가|을|를|의|에서|으로|로)$/,"")
      .replace(/\s+/g," ")
      .trim();
    if(rootLot&&knob.toLowerCase().startsWith(rootLot.toLowerCase()))knob=knob.slice(rootLot.length).trim();
    if(/^(?:은|는|이|가|을|를|의|에서|으로|로)?$/i.test(knob))knob="";
  }
  return {text,rootLot,stepId,knob,hasSplit,hasChart,hasFab,hasFile,hasMeasurement};
}

function flowiPromptProgressLines(prompt,tool={},phase="result"){
  const entity=flowiPromptEntities(prompt);
  const feature=String(tool?.feature||"").toLowerCase();
  const kind=String(tool?.table?.kind||tool?.split_view?.kind||tool?.type||"").toLowerCase();
  const splitRequested=entity.hasSplit||feature==="splittable"||kind.includes("split")||kind.includes("knob");
  const chartRequested=entity.hasChart||feature==="dashboard"||kind.includes("chart");
  const fabRequested=entity.hasFab||feature==="fab"||kind.includes("fab");
  const fileRequested=entity.hasFile||feature==="filebrowser"||kind.includes("sql");
  const measurementRequested=entity.hasMeasurement||kind.includes("semantic_measurement")||String(tool?.action||"").includes("semantic_measurement");
  const running=phase==="live";
  const lines=[];
  const subject=entity.stepId?`${entity.stepId} step_id`:entity.rootLot?`${entity.rootLot} root lot`:"질문";
  if(measurementRequested){
    lines.push(`${subject} 기준으로 측정값과 관련 source/item 매핑을 확인하는 요청으로 이해했습니다.`);
    lines.push(running?"측정 용어와 데이터 위치를 확인한 뒤 결과를 준비하고 있습니다.":"측정 용어와 source/item 매핑을 확인해 결과를 정리했습니다.");
  }else if(splitRequested){
    lines.push(entity.knob
      ?`${subject} 기준으로 SplitTable 조회와 ${entity.knob} knob 조건 확인 요청을 이해했습니다.`
      :`${subject} 기준으로 SplitTable 조회 요청을 이해했습니다.`);
    lines.push(running?"SplitTable 데이터를 조회해 화면에 바로 보여줄 결과를 준비하고 있습니다.":"SplitTable 조회 결과를 요약과 인라인 표로 정리했습니다.");
  }else if(chartRequested){
    lines.push(`${subject} 기준으로 Dashboard 차트 요청을 확인했습니다.`);
    lines.push(running?"필요한 데이터와 차트 구성을 확인하고 있습니다.":"차트 결과를 화면에서 확인할 수 있게 정리했습니다.");
  }else if(fabRequested){
    lines.push(`${subject} 기준으로 FAB 진행 상태나 현재 위치를 확인하는 요청으로 이해했습니다.`);
    lines.push(running?"진행 상태 데이터를 조회하고 있습니다.":"FAB 조회 결과를 정리했습니다.");
  }else if(fileRequested){
    lines.push(`${subject} 기준으로 FileBrowser/SQL 데이터 확인 요청을 이해했습니다.`);
    lines.push(running?"읽기 전용 조회 경로로 데이터를 확인하고 있습니다.":"조회 결과를 화면에 정리했습니다.");
  }else if(entity.stepId){
    lines.push(`${subject}의 공정/기능 step 정보를 확인하는 요청으로 이해했습니다.`);
    if(running)lines.push("step 기준 데이터를 조회하고 답변을 준비하고 있습니다.");
  }else if(entity.rootLot){
    lines.push(`${subject} 관련 데이터를 확인하는 요청으로 이해했습니다.`);
    if(running)lines.push("필요한 단위기능을 확인하고 답변을 준비하고 있습니다.");
  }
  return flowiUniqueLines(lines,3);
}

function flowiInterpretationLines(trace,tool){
  const interpretation=trace?.interpretation||{};
  const slots=interpretation.input_slots||tool?.slots||{};
  const missing=Array.isArray(interpretation.missing_slots)?interpretation.missing_slots:(Array.isArray(tool?.missing)?tool.missing:[]);
  const terms=Array.isArray(interpretation.term_resolution)?interpretation.term_resolution:[];
  const knowledge=Array.isArray(trace?.retrieved_knowledge)?trace.retrieved_knowledge:[];
  const slotLabels=[
    ["product","제품"],["root_lot_id","Root Lot"],["root_lot","Root Lot"],["lot","Lot"],["wafer","Wafer"],["step","Step"],["knob","Knob"],["knobs","Knob"],["semantic_term","측정용어"],["agg","집계"],["item","항목"],["source_candidates","소스"],
  ];
  const slotParts=slotLabels.map(([key,label])=>{
    const raw=slots[key];
    const text=Array.isArray(raw)?raw.filter(Boolean).join(", "):String(raw??"").trim();
    return text?`${label} ${flowiShortText(text,70)}`:"";
  }).filter(Boolean);
  const termParts=terms.map(row=>{
    const token=flowiShortText(row?.token||row?.term||"",48);
    const meaning=flowiShortText(row?.meaning||row?.query_filter||row?.status||"",90);
    return token&&meaning?`${token} -> ${meaning}`:"";
  }).filter(Boolean);
  const lines=[];
  if(slotParts.length)lines.push(`질문에서 ${slotParts.slice(0,6).join(", ")}를 확인했습니다.`);
  if(termParts.length)lines.push(`${termParts.slice(0,5).join(" · ")}로 해석했습니다.`);
  if(missing.length)lines.push(`추가 확인이 필요합니다: ${missing.slice(0,5).join(", ")}.`);
  if(knowledge.length)lines.push(`Wiki/schema 근거 ${knowledge.length}건을 참고했습니다.`);
  return lines.slice(0,4);
}

function flowiMethodLine(trace,tool){
  const activation=trace?.activation||{};
  const evidence=trace?.evidence||{};
  const feature=tool?.feature||evidence.used_feature_ai||activation.feature||"Flow-i";
  const action=tool?.action||activation.action||"";
  const table=tool?.table&&typeof tool.table==="object"?tool.table:null;
  const split=tool?.split_view&&typeof tool.split_view==="object"?tool.split_view:null;
  if(tool?.blocked)return "권한과 정책을 먼저 확인해 허용되지 않은 작업은 실행하지 않았습니다.";
  if(split)return "SplitTable 화면 API를 read-only로 호출해 같은 셀 기준의 인라인 결과로 표시합니다.";
  if(table)return `${feature} 기능을 read-only로 호출하고 ${table.total??(Array.isArray(table.rows)?table.rows.length:0)}건의 결과 표를 구성합니다.`;
  if(action)return `${feature} 기능의 ${action} 결과를 홈 화면에서 바로 확인합니다.`;
  return "";
}

function FlowiInterpretationSummary({trace,tool,prompt}){
  const lines=flowiUniqueLines([...flowiPromptProgressLines(prompt,tool,"result"),...flowiInterpretationLines(trace,tool)],5);
  const method=flowiMethodLine(trace,tool);
  if(!lines.length&&!method)return null;
  return <details style={{margin:"10px 0 0",border:"1px solid #262626",borderRadius:8,background:"#101010",padding:"8px 9px",fontFamily:"'JetBrains Mono',monospace"}}>
    <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontWeight:900}}>요청 해석 / 진행 방식</summary>
    {lines.length>0&&<div style={{display:"grid",gap:4,marginBottom:method?8:0}}>
      <div style={{fontSize:14,color:"#f5f5f5",fontWeight:900}}>요청 해석</div>
      {lines.map((line,i)=><div key={i} style={{fontSize:14,lineHeight:1.55,color:i===0?"#e5e5e5":"#a3a3a3",whiteSpace:"normal",overflowWrap:"anywhere"}}>{line}</div>)}
    </div>}
    {method&&<div style={{display:"grid",gap:4}}>
      <div style={{fontSize:14,color:"#f5f5f5",fontWeight:900}}>진행 방식</div>
      <div style={{fontSize:14,lineHeight:1.55,color:"#a3a3a3",whiteSpace:"normal",overflowWrap:"anywhere"}}>{method}</div>
    </div>
    }
  </details>;
}

function flowiResultShellStyle(embedded=false,isClarificationOnly=false){
  if(isClarificationOnly){
    return {width:"100%",boxSizing:"border-box",marginTop:embedded?0:8,padding:"2px 0 0",background:"transparent",border:"0",borderRadius:0,overflow:"visible"};
  }
  return {width:"100%",boxSizing:"border-box",marginTop:embedded?0:12,border:embedded?"1px solid #2a2a2a":"1px solid #333",borderRadius:10,padding:12,background:"#111",overflow:"visible"};
}

function FlowiResult({busy,error,result,prompt,onNavigate,onChoice,embedded=false,isAdmin=false,activeChartSessionId="",onUseChartSession=null}){
  if(busy)return <div style={{marginTop:embedded?0:10,fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>local tools + llm 처리 중...</div>;
  if(error)return <div style={{marginTop:10,padding:"9px 10px",borderRadius:6,background:"#7f1d1d33",color:"#fca5a5",fontSize:14,border:"1px solid #7f1d1d"}}>{error}</div>;
  if(!result)return null;
  const tool=result.tool||{};
  const table=tool.table&&Array.isArray(tool.table.rows)?tool.table:null;
  const choices=Array.isArray(tool?.clarification?.choices)?tool.clarification.choices.slice(0,3):[];
  const argumentChoices=tool.arguments_choices||result.arguments_choices||{};
  const hasArgumentChoices=argumentChoices&&Array.isArray(argumentChoices.fields)&&argumentChoices.fields.length>0;
  const missingFreetext=Array.isArray(tool.missing_freetext)?tool.missing_freetext:(Array.isArray(result.missing_freetext)?result.missing_freetext:[]);
  const hasMissingFreetext=missingFreetext.length>0;
  const partialPrompt=tool.last_partial_prompt||result.last_partial_prompt||prompt;
  const walkthrough=tool.walkthrough||{};
  const workflow=tool.workflow_state||result.workflow_state||{};
  const chart=tool?.chart&&typeof tool.chart==="object"?tool.chart:null;
  const chartResult=tool?.chart_result&&typeof tool.chart_result==="object"?tool.chart_result:null;
  const chartSessionId=tool?.chart_session_id||chartResult?.chart_session_id||"";
  const summary=flowiResultSummary(tool,result);
  const actions=flowiResultActions(tool,table,chartResult,onNavigate);
  // 구조화된 콘텐츠(표/차트/선택지 등)가 없고 답변 텍스트만 있으면 plain text 로 단순 표시.
  const hasStructured=!!(table||chart||chartResult||tool.split_view
    ||(Array.isArray(tool.lot_list)&&tool.lot_list.length)
    ||(Array.isArray(tool.rows)&&tool.rows.length)
    ||(Array.isArray(tool.knobs)&&tool.knobs.length)
    ||(Array.isArray(tool.blocks)&&tool.blocks.length)
    ||tool.sql_draft
    ||choices.length||hasArgumentChoices||hasMissingFreetext
    ||(walkthrough&&walkthrough.session_id)
    ||(result.proposal&&result.confirm));
  const isClarificationOnly=!!(choices.length&&!table&&!chart&&!chartResult&&!tool.split_view
    &&!(Array.isArray(tool.lot_list)&&tool.lot_list.length)
    &&!(Array.isArray(tool.rows)&&tool.rows.length)
    &&!(Array.isArray(tool.knobs)&&tool.knobs.length)
    &&!(Array.isArray(tool.blocks)&&tool.blocks.length)
    &&!tool.sql_draft
    &&!hasArgumentChoices&&!hasMissingFreetext
    &&(tool.needs_input||result.needs_input||String(tool.action||"").startsWith("clarify_")||String(workflow.status||"").startsWith("awaiting")));
  const plain=!hasStructured&&!!result.answer;
  const emptyHint=!result.answer&&(tool.missing||hasArgumentChoices||hasMissingFreetext)
    ?"필요한 조건이 조금 더 있어요. 아래 선택지나 직접 입력으로 이어서 알려주세요."
    :"표시할 결과가 비어 있습니다. 조건을 조금 더 좁혀서 다시 물어봐 주세요.";
  return(<div style={flowiResultShellStyle(embedded,isClarificationOnly)}>
    {!isClarificationOnly&&(!plain||actions.length>0)&&<div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:10,marginBottom:8}}>
      <div style={{minWidth:0,fontSize:14,color:"#e5e5e5",fontWeight:900,fontFamily:"'JetBrains Mono',monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{plain?"":summary}</div>
      {actions.length>0&&<div style={{display:"flex",gap:6,alignItems:"center",justifyContent:"flex-end",flexWrap:"wrap"}}>{actions.map(a=><button key={a.key} type="button" onClick={a.onClick} title={a.title} style={FLOWI_ACTION_BTN}>{a.label}</button>)}</div>}
    </div>}
    {!isClarificationOnly&&!plain&&result.run_id&&<div style={{display:"flex",gap:6,alignItems:"center",margin:"-2px 0 8px",fontFamily:"monospace",fontSize:14,color:"#737373",flexWrap:"wrap"}}>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px",background:"#151515"}}>run {String(result.run_id).slice(0,22)}</span>
      {result.runtime_status&&<span style={{color:flowiTraceStatusColor(result.runtime_status)}}>{result.runtime_status}</span>}
    </div>}
    <FlowiMarkdown text={result.answer||emptyHint}/>
    {!isClarificationOnly&&<FlowiInterpretationSummary trace={result.trace} tool={tool} prompt={prompt}/>}
    {!isClarificationOnly&&<FlowiExecutionProof tool={tool} trace={result.trace}/>}
    {!isClarificationOnly&&<FlowiActionLogPanel actionLog={result.action_log} trace={result.trace}/>}
    {!isClarificationOnly&&isAdmin&&!plain&&<div style={{display:"flex",gap:6,marginTop:8,flexWrap:"wrap"}}>
      {tool.intent&&<span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{tool.intent}</span>}
      {workflow.status&&<span style={{fontSize:14,color:workflow.status.startsWith("awaiting")?"#f97316":workflow.status==="blocked"?"#ef4444":"#22c55e",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{workflow.status}</span>}
      {result.llm&&<span style={{fontSize:14,color:result.llm.used?"#22c55e":"#737373",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{result.llm.used?"llm used":"local result"}</span>}
    </div>}
    {choices.length>0&&!hasArgumentChoices&&!hasMissingFreetext&&<FlowiChoices question={tool.clarification?.question} choices={choices} onChoice={onChoice} onNavigate={onNavigate}/>}
    {hasArgumentChoices&&<FlowiArgumentChoices data={argumentChoices} basePrompt={partialPrompt} onChoice={onChoice}/>}
    {hasMissingFreetext&&<FlowiMissingFreetext fields={missingFreetext} basePrompt={partialPrompt} onChoice={onChoice}/>}
    <FlowiInlineContent tool={tool} table={table} chart={chart} chartResult={chartResult}/>
    {chartSessionId&&<div style={{marginTop:8,display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}>
      <button type="button" onClick={()=>onUseChartSession&&onUseChartSession(chartSessionId)}
        style={{fontSize:14,color:"#f97316",fontFamily:"monospace",border:"1px solid #7c2d12",borderRadius:999,padding:"3px 9px",background:activeChartSessionId===chartSessionId?"#2a1608":"#1f130b",cursor:"pointer",fontWeight:900}}>
        수정 요청
      </button>
      <span style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{String(chartSessionId).slice(0,12)}</span>
    </div>}
    {walkthrough&&walkthrough.session_id&&<FlowiWalkthrough data={walkthrough}/>}
    {isAdmin&&result.proposal&&result.confirm&&<FlowiEdmProposal result={result}/>}
    {!isClarificationOnly&&isAdmin&&<FlowiTrace trace={result.trace}/>}
    {!isClarificationOnly&&<FlowiFeedback result={result} tool={tool} prompt={prompt} isAdmin={isAdmin}/>}
  </div>);
}

function FlowiMarkdown({text}){
  const lines=String(text||"").split("\n");
  return <div style={{whiteSpace:"pre-wrap",fontSize:14,lineHeight:1.75,color:"#d4d4d4",overflowWrap:"anywhere"}}>
    {lines.map((line,i)=>{
      const m=line.match(/^([^:：]{1,24})[:：]\s*(.*)$/);
      if(m&&m[2])return <div key={i} style={{marginTop:i?4:0}}><span style={{color:"#f5f5f5",fontWeight:900}}>{m[1]}: </span><span>{m[2]}</span></div>;
      return <div key={i} style={{marginTop:i&&line.trim()?4:0}}>{line}</div>;
    })}
  </div>;
}

function flowiTableColumns(table){
  const rows=Array.isArray(table?.rows)?table.rows:[];
  const cols=Array.isArray(table?.columns)?table.columns:[];
  if(cols.length)return cols.map(c=>typeof c==="string"?{key:c,label:c}:{key:c.key||c.label,label:c.label||c.key}).filter(c=>c.key);
  if(rows.length&&rows[0]&&typeof rows[0]==="object")return Object.keys(rows[0]).filter(k=>!String(k).startsWith("__")).map(k=>({key:k,label:k}));
  return [];
}

function flowiResultType(tool,table,chartResult){
  if(tool?.type)return String(tool.type);
  if(chartResult||tool?.chart)return "chart";
  if(tool?.split_view)return "split_view";
  if(Array.isArray(tool?.lot_list))return "lot_list";
  if(table||Array.isArray(tool?.rows)||Array.isArray(tool?.knobs))return "table";
  return "message";
}

function flowiResultSummary(tool,result){
  if(tool?.inline_summary)return tool.inline_summary;
  const table=tool?.table;
  const chart=tool?.chart_result||tool?.chart;
  if(tool?.raw_data_download)return `Chart raw data ${tool.raw_data_download.row_count??""} rows`;
  if(tool?.split_view)return `${tool.split_view.title||"SplitTable"} ${tool.split_view.total??(tool.split_view.rows||[]).length}개 셀`;
  if(Array.isArray(tool?.lot_list)&&tool.lot_list.length)return `Lot list ${tool.lot_list.length}건`;
  if(table&&Array.isArray(table.rows)){
    const cols=flowiTableColumns(table);
    return `${table.title||table.kind||"Flowi table"} ${table.total??table.rows.length} rows · ${cols.length} columns`;
  }
  if(chart)return chart.title||chart.kind||"Flowi chart";
  return result?.answer?"Flowi 응답":"Flowi 결과";
}

function flowiResultActions(tool,table,chartResult,onNavigate){
  const items=[];
  const canNav=typeof onNavigate==="function";
  const feature=tool?.feature||"";
  const kind=String(table?.kind||tool?.split_view?.kind||"").toLowerCase();
  const rawDownload=tool?.raw_data_download&&typeof tool.raw_data_download==="object"?tool.raw_data_download:null;
  const chartSessionId=tool?.chart_session_id||chartResult?.chart_session_id||rawDownload?.chart_session_id||"";
  const addNav=(key,label,title)=>{if(canNav&&!items.some(x=>x.key===`nav-${key}`))items.push({key:`nav-${key}`,label,title,onClick:()=>onNavigate(key)});};
  if(feature==="splittable"||kind.includes("split")||kind.includes("knob"))addNav("splittable","전체화면 SplitTable","SplitTable 화면에서 전체 결과 보기");
  if(rawDownload?.url)items.push({key:"chart-raw-csv",label:"Raw CSV",title:`Chart raw data CSV 다운로드 · ${rawDownload.row_count??"-"}행`,onClick:()=>flowiDownloadChartRaw(rawDownload)});
  else if(chartSessionId)items.push({key:"chart-raw-csv",label:"Raw CSV",title:"직전 chart session raw data를 CSV로 내려받기",onClick:()=>flowiDownloadChartRaw({chart_session_id:chartSessionId})});
  if(feature==="dashboard"||chartResult||tool?.chart)addNav("dashboard","차트 페이지","Dashboard 화면에서 차트 보기");
  if(table&&Array.isArray(table.rows)&&table.rows.length)items.push({key:"export-table",label:"엑셀 내보내기",title:"현재 인라인 표를 CSV로 내려받기",onClick:()=>flowiDownloadTable(table)});
  const entries=Array.isArray(tool?.feature_entrypoints)?tool.feature_entrypoints:[];
  entries.slice(0,2).forEach(ep=>{if(ep?.key&&ep.key!==feature)addNav(ep.key,`${ep.title||ep.key} 열기`,ep.description||"관련 화면 열기");});
  return items.slice(0,4);
}

function flowiDownloadChartRaw(rawDownload){
  const sid=String(rawDownload?.chart_session_id||"").trim();
  const url=rawDownload?.url||`/api/llm/flowi/chart-session/raw-data.csv?chart_session_id=${encodeURIComponent(sid)}`;
  const filename=rawDownload?.filename||`flowi_chart_raw_${sid.slice(0,8)||"data"}.csv`;
  if(!sid&&!rawDownload?.url)return;
  dl(url,filename).catch(e=>toast.error(e?.message||"chart raw CSV 다운로드 실패"));
}

function flowiDownloadTable(table){
  const rows=Array.isArray(table?.rows)?table.rows:[];
  const cols=flowiTableColumns(table);
  if(!rows.length||!cols.length||typeof document==="undefined")return;
  const esc=(v)=>`"${String(v??"").replace(/"/g,'""')}"`;
  const csv=[cols.map(c=>esc(c.label||c.key)).join(","),...rows.map(r=>cols.map(c=>esc(r[c.key])).join(","))].join("\n");
  const blob=new Blob(["\uFEFF"+csv],{type:"text/csv;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");
  a.href=url;
  a.download=`flowi_${String(table.kind||"result").replace(/[^A-Za-z0-9_-]+/g,"_")}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function flowiSplitStView(tool){
  const st=tool?.splittable_view&&typeof tool.splittable_view==="object"?tool.splittable_view:null;
  if(st&&Array.isArray(st.headers)&&Array.isArray(st.rows)&&st.rows.some(r=>r&&typeof r==="object"&&r._cells))return st;
  return null;
}

function flowiSplitProduct(tool,stView){
  return stView?.product||tool?.filters?.product||tool?.arguments?.product||"";
}

function FlowiInlineContent({tool,table,chart,chartResult}){
  const type=flowiResultType(tool,table,chartResult);
  const explicitBlocks=Array.isArray(tool?.blocks)?tool.blocks:[];
  if(explicitBlocks.length)return <>{explicitBlocks.map((block,i)=><FlowiResultBlock key={block?.id||`${block?.kind||"block"}-${i}`} block={block}/>)}</>;
  const lotList=Array.isArray(tool?.lot_list)?tool.lot_list:[];
  const rows=Array.isArray(tool?.rows)?tool.rows:[];
  const knobs=Array.isArray(tool?.knobs)?tool.knobs:[];
  const sqlDraft=tool?.sql_draft&&typeof tool.sql_draft==="object"?tool.sql_draft:null;
  const blocks=[];
  if(sqlDraft)blocks.push(<FlowiSqlDraft key="sql" draft={sqlDraft}/>);
  if((type==="chart"||chartResult)&&chartResult)blocks.push(<FlowiScatterResult key="chart-result" data={chartResult}/>);
  else if(type==="chart"&&chart)blocks.push(<FlowiChartPlan key="chart-plan" chart={chart}/>);
  else if((type==="split_view"||tool?.split_view)&&tool?.split_view){
    const stView=flowiSplitStView(tool);
    blocks.push(stView
      ? <SplitTableSnapshotView key="split" stView={stView} product={flowiSplitProduct(tool,stView)} source="Home Flow-i" maxHeight={360}/>
      : <FlowiSplitView key="split" view={tool.split_view}/>);
  }
  else if((type==="lot_list"||lotList.length>0)&&lotList.length>0)blocks.push(<FlowiLotList key="lots" items={lotList}/>);
  else if(table)blocks.push(<FlowiDataTable key="table" table={table}/>);
  else if(rows.length>0)blocks.push(<FlowiDataTable key="rows" table={{kind:"flowi_rows",title:"Flowi rows",columns:_legacyRowColumns(rows),rows,total:rows.length}}/>);
  else if(knobs.length>0)blocks.push(<FlowiKnobCards key="knobs" knobs={knobs}/>);
  if(blocks.length)return <>{blocks}</>;
  return null;
}

function FlowiResultBlock({block}){
  if(!block||typeof block!=="object")return null;
  const kind=String(block.kind||"");
  const payload=block.payload&&typeof block.payload==="object"?block.payload:{};
  const title=block.title||payload.title||"Flowi block";
  if(kind==="lot_table"){
    return <FlowiDataTable table={{...payload,title,highlight:block.highlight||payload.highlight}}/>;
  }
  if(kind==="chart_scatter"||kind==="chart_trend"){
    return <FlowiScatterResult data={{...payload,title}}/>;
  }
  if(kind==="sql_draft")return <FlowiSqlDraft draft={payload}/>;
  if(kind==="evidence_note"){
    return <div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"9px 10px",fontSize:14,color:"#d4d4d4",lineHeight:1.5,whiteSpace:"pre-wrap"}}>{payload.text||block.text||""}</div>;
  }
  if(Array.isArray(payload.series)||Array.isArray(payload.points)||Array.isArray(payload.groups)||Array.isArray(payload.boxes)){
    return <FlowiScatterResult data={{...payload,title}}/>;
  }
  if(Array.isArray(payload.rows))return <FlowiDataTable table={{...payload,title}}/>;
  return null;
}

function FlowiSqlDraft({draft}){
  const cols=Array.isArray(draft?.selected_columns)?draft.selected_columns:[];
  const warnings=Array.isArray(draft?.warnings)?draft.warnings:[];
  const sql=String(draft?.sql||"");
  return <div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"9px 10px",fontFamily:"monospace"}}>
    <div style={{display:"flex",gap:8,alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",marginBottom:7}}>
      <span style={{fontSize:14,color:"#f97316",fontWeight:900}}>FileBrowser SQL draft</span>
      <span style={{fontSize:14,color:draft?.fallback?"#f97316":"#22c55e"}}>{draft?.fallback?"fallback":"validated"}</span>
    </div>
    <div style={{fontSize:14,color:"#d4d4d4",lineHeight:1.55,whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{sql||"(필터 없음)"}</div>
    {cols.length>0&&<div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap"}}>
      {cols.slice(0,18).map(c=><span key={c} style={{fontSize:14,color:"#a3a3a3",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{c}</span>)}
    </div>}
    {warnings.length>0&&<div style={{marginTop:7,fontSize:14,color:"#fbbf24",lineHeight:1.45}}>
      {warnings.slice(0,4).map((w,i)=><div key={i}>{w}</div>)}
    </div>}
  </div>;
}

function _legacyRowColumns(rows){
  const keys=rows.length&&rows[0]?Object.keys(rows[0]).filter(k=>!String(k).startsWith("__")):["product","step_id","item_id","wafer_id","median","mean","count"];
  return keys.map(k=>({key:k,label:k}));
}

function FlowiKnobCards({knobs}){
  return <div style={{marginTop:10,display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:8}}>
    {knobs.slice(0,8).map(k=><div key={k.knob} style={{border:"1px solid #333",borderRadius:8,padding:"8px 10px",background:"#151515"}}>
      <div style={{fontSize:14,fontWeight:800,color:"#e5e5e5",marginBottom:4}}>{k.display_name||k.knob}</div>
      {(k.values||[]).slice(0,3).map(v=><div key={String(v.value)} style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",lineHeight:1.55}}>{String(v.value)} · {v.count}wf{Array.isArray(v.wafers)&&v.wafers.length?" · "+v.wafers.slice(0,8).join(","):""}</div>)}
    </div>)}
  </div>;
}

function FlowiLotList({items}){
  return <div style={{marginTop:10,display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(190px,1fr))",gap:8}}>
    {items.slice(0,24).map((item,i)=><div key={`${item.root_lot||item.root_lot_id||i}-${item.wafer||item.wafer_id||""}`} style={{border:"1px solid #333",borderRadius:8,padding:"9px 10px",background:"#151515",minWidth:0}}>
      <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"baseline",marginBottom:5}}>
        <span style={{fontSize:14,color:"#f97316",fontWeight:900,fontFamily:"monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{item.root_lot||item.root_lot_id||"-"}</span>
        <span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>{item.product||""}</span>
      </div>
      <FlowiLotLine label="fab" value={item.fab_lot||item.fab_lot_id||item.lot_id}/>
      <FlowiLotLine label="wf" value={item.wafer||item.wafer_id}/>
      <FlowiLotLine label="step" value={item.current_step||item.current_func_step}/>
      <FlowiLotLine label="time" value={item.tkout_time}/>
      {(item.knob||item.knob_value)&&<FlowiLotLine label="knob" value={[item.knob,item.knob_value].filter(Boolean).join(" = ")}/>}
    </div>)}
  </div>;
}

function FlowiLotLine({label,value}){
  if(value===undefined||value===null||value==="")return null;
  return <div style={{display:"grid",gridTemplateColumns:"42px minmax(0,1fr)",gap:6,fontSize:14,lineHeight:1.45,fontFamily:"monospace"}}>
    <span style={{color:"#737373"}}>{label}</span><span style={{color:"#d4d4d4",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={String(value)}>{String(value)}</span>
  </div>;
}

function FlowiSplitView({view}){
  const headers=Array.isArray(view?.headers)?view.headers:[];
  const rows=Array.isArray(view?.rows)?view.rows:[];
  if(!rows.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>인라인으로 표시할 SplitTable 셀이 없습니다.</div>;
  const values=[];
  rows.forEach(r=>(r.cells||[]).forEach(c=>{[c.actual,c.plan].forEach(v=>{if(v!==undefined&&v!==null&&v!=="")values.push(String(v));});}));
  const uniq=[...new Set(values)].slice(0,18);
  const palette=["#1f2937","#3b2f16","#1f3a2d","#26324a","#3a2535","#243b3f","#3a2a20","#2f3340"];
  const colorFor=(v)=>{const idx=uniq.indexOf(String(v));return idx>=0?palette[idx%palette.length]:"#171717";};
  return <div style={{marginTop:10,border:"1px solid #333",borderRadius:8,overflow:"hidden",background:"#121212"}}>
    <div style={{display:"flex",justifyContent:"space-between",gap:8,padding:"8px 10px",borderBottom:"1px solid #2a2a2a",background:"#171717"}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace"}}>{view.title||"SplitTable inline"}</div>
      <div style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{view.total??rows.length} cells</div>
    </div>
    <div style={{overflow:"auto",maxHeight:320}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace",tableLayout:"fixed",minWidth:Math.max(360,120+(headers.length||1)*92)}}>
        <thead><tr>
          <th style={{position:"sticky",left:0,top:0,zIndex:2,textAlign:"left",padding:"7px 8px",borderBottom:"1px solid #333",borderRight:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",width:120}}>{view.row_label||"항목"}</th>
          {headers.map((h,i)=><th key={`${h}-${i}`} style={{position:"sticky",top:0,zIndex:1,textAlign:"center",padding:"7px 8px",borderBottom:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",width:92}}>{h}</th>)}
        </tr></thead>
        <tbody>{rows.map((r,ri)=><tr key={r.parameter||ri}>
          <td style={{position:"sticky",left:0,zIndex:1,padding:"6px 8px",borderBottom:"1px solid #262626",borderRight:"1px solid #333",background:"#151515",color:"#e5e5e5",fontWeight:900,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={r.parameter||r.display}>{r.display||r.parameter}</td>
          {(r.cells||[]).map((c,ci)=>{
            const actual=c.actual??"";
            const plan=c.plan??"";
            const show=actual?String(actual):(plan?`plan ${plan}`:"");
            const mismatch=!!(c.mismatch||c.highlight);
            return <td key={ci} style={{padding:"6px 8px",borderBottom:"1px solid #262626",borderRight:"1px solid #262626",textAlign:"center",background:colorFor(actual||plan),color:"#e5e5e5",boxShadow:mismatch?"inset 0 0 0 2px rgba(239,68,68,0.9)":"none",whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35}}>
              {show}{mismatch&&plan&&actual&&<span style={{display:"block",fontSize:14,color:"#fca5a5"}}>plan {plan}</span>}
            </td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}
const FR_TD={padding:"5px 6px",borderBottom:"1px solid #262626",color:"#d4d4d4",whiteSpace:"nowrap"};

function flowiSplitApiCall(trace){
  const calls=Array.isArray(trace?.api_calls)?trace.api_calls:[];
  return calls.find(c=>String(c?.path||"")==="/api/splittable/view"||String(c?.name||"").toLowerCase().includes("splittable view"))||null;
}

function flowiCacheLabel(cache){
  if(!cache||typeof cache!=="object")return "";
  const bits=[];
  ["status","state","source"].forEach(k=>{if(cache[k]!==undefined&&cache[k]!==null&&cache[k]!=="")bits.push(`${k} ${cache[k]}`);});
  if(cache.hit!==undefined)bits.push(`hit ${cache.hit?"yes":"no"}`);
  if(cache.fresh!==undefined)bits.push(`fresh ${cache.fresh?"yes":"no"}`);
  return bits.slice(0,3).join(" · ");
}

function FlowiExecutionProof({tool,trace}){
  const splitCall=flowiSplitApiCall(trace);
  const splitApi=tool?.split_api&&typeof tool.split_api==="object"?tool.split_api:null;
  const splitIntent=String([tool?.feature,tool?.intent,tool?.action,tool?.table?.kind,tool?.split_view?.kind].filter(Boolean).join(" ")).toLowerCase();
  const hasSplit=!!(tool?.split_view||splitCall||splitApi||splitIntent.includes("split"));
  if(!hasSplit)return null;
  const meta=splitCall?.metadata&&typeof splitCall.metadata==="object"?splitCall.metadata:{};
  const runtime=tool?.runtime_profile&&typeof tool.runtime_profile==="object"?tool.runtime_profile:(meta.runtime_profile&&typeof meta.runtime_profile==="object"?meta.runtime_profile:{});
  const cache=tool?.view_cache&&typeof tool.view_cache==="object"?tool.view_cache:(meta.view_cache&&typeof meta.view_cache==="object"?meta.view_cache:{});
  const elapsed=tool?.elapsed_ms??splitApi?.elapsed_ms??meta.elapsed_ms;
  const rows=Array.isArray(tool?.split_view?.rows)?tool.split_view.rows.length:null;
  const chips=[
    "/api/splittable/view",
    splitCall?.callee||splitApi?.callee||"routers.splittable.view_split",
    elapsed!==undefined&&elapsed!==null&&elapsed!==""?`${elapsed}ms`:"",
    rows!==null?`${rows} rows`:"",
    flowiCacheLabel(cache),
    runtime.total_ms!==undefined?`runtime ${runtime.total_ms}ms`:"",
  ].filter(Boolean);
  return <div style={{marginTop:10,border:"1px solid #2f3b2f",borderRadius:8,background:"#101611",padding:"8px 9px",fontFamily:"'JetBrains Mono',monospace"}}>
    <div style={{fontSize:14,color:"#d9f99d",fontWeight:900}}>실제 실행</div>
    <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:7}}>
      {chips.slice(0,6).map(chip=><span key={chip} style={{fontSize:14,color:"#d4d4d4",border:"1px solid #334155",borderRadius:999,padding:"2px 7px",background:"#151515",whiteSpace:"nowrap"}}>{chip}</span>)}
    </div>
  </div>;
}

const FLOWI_LIVE_STEPS=[
  {key:"interpret",label:"요청 해석",detail:"lot, step, product, 차트/표 의도를 확인합니다."},
  {key:"route",label:"실행 경로 선택",detail:"권한과 입력값 기준으로 사용할 단위기능을 고릅니다."},
  {key:"run",label:"단위기능 실행",detail:"FileBrowser, SplitTable, Dashboard 등 read-only 경로를 호출합니다."},
  {key:"render",label:"출력 정리",detail:"답변, 표/차트, 실행 근거를 화면 응답으로 묶습니다."},
];

function flowiTraceStatusColor(status){
  return status==="done"||status==="success"?"#22c55e":status==="blocked"||status==="error"||status==="failed"?"#ef4444":status==="skipped"||status==="available"?"#737373":"#f97316";
}

function FlowiLiveTrace({step=0,elapsed=0,prompt=""}){
  const lines=flowiPromptProgressLines(prompt,{},"live");
  const activeIndex=Math.max(0,Math.min(step,FLOWI_LIVE_STEPS.length-1));
  const delayed=elapsed>=60;
  return(<div style={{marginTop:8,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"9px 10px",fontFamily:"monospace"}}>
    <div style={{display:"flex",alignItems:"center",gap:8,justifyContent:"space-between",minWidth:0}}>
      <div style={{display:"flex",alignItems:"center",gap:8,minWidth:0}}>
        <span style={{width:7,height:7,borderRadius:999,background:"#f97316",display:"inline-block",animation:"flowiConnBlink .75s ease-in-out infinite",flexShrink:0}}/>
        <span style={{fontSize:14,fontWeight:900,color:"#e5e5e5",whiteSpace:"nowrap"}}>답변 준비 중</span>
        <span style={{fontSize:14,color:"#a3a3a3",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{FLOWI_LIVE_STEPS[activeIndex]?.label}</span>
      </div>
      <span style={{fontSize:14,color:delayed?"#fb923c":"#737373",whiteSpace:"nowrap"}}>{elapsed}s / {FLOWI_CLIENT_TIMEOUT_S}s</span>
    </div>
    {lines.length>0&&<div style={{marginTop:7,display:"grid",gap:3}}>
      {lines.map((line,i)=><div key={i} style={{fontSize:14,lineHeight:1.45,color:i===0?"#d4d4d4":"#8f8f8f",whiteSpace:"normal",overflowWrap:"anywhere"}}>{line}</div>)}
    </div>}
    <div style={{marginTop:8,display:"grid",gap:5}}>
      {FLOWI_LIVE_STEPS.map((item,i)=>{
        const done=i<activeIndex;
        const current=i===activeIndex;
        const color=done?"#22c55e":current?"#f97316":"#737373";
        return <div key={item.key} style={{display:"grid",gridTemplateColumns:"18px minmax(82px,126px) minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
          <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:10,border:`1px solid ${color}99`,color}}>
            {done?"✓":current?"•":i+1}
          </span>
          <span style={{color:current?"#e5e5e5":"#a3a3a3",fontWeight:900,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{item.label}</span>
          <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={item.detail}>{item.detail}</span>
        </div>;
      })}
    </div>
    {delayed&&<div style={{marginTop:7,fontSize:14,color:"#fb923c",lineHeight:1.45}}>
      응답이 길어지고 있습니다. 클라이언트는 {FLOWI_CLIENT_TIMEOUT_S}초에서 요청을 중단하고 다시 시도할 수 있게 합니다.
    </div>}
  </div>);
}

function FlowiActionLogPanel({actionLog,trace}){
  const summary=Array.isArray(actionLog?.summary)?actionLog.summary.filter(Boolean):[];
  const timeline=Array.isArray(actionLog?.timeline)?actionLog.timeline.filter(Boolean):[];
  const fallbackSummary=!summary.length?flowiInterpretationLines(trace,{}):[];
  const lines=(summary.length?summary:fallbackSummary).slice(0,6);
  if(!lines.length&&!timeline.length)return null;
  const disclaimer=actionLog?.disclaimer||trace?.note||"내부 추론 원문이 아니라 검증 가능한 실행 요약입니다.";
  return <details style={{marginTop:10,border:"1px solid #262626",borderRadius:8,background:"#101010",padding:"8px 9px",fontFamily:"'JetBrains Mono',monospace"}}>
    <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontWeight:900}}>
      실행 근거 <span style={{fontWeight:400,color:"#737373"}}>필요할 때 펼쳐보기</span>
    </summary>
    {lines.length>0&&<div style={{display:"grid",gap:4,marginTop:8}}>
      {lines.map((line,i)=><div key={i} style={{fontSize:14,lineHeight:1.5,color:i===0?"#d4d4d4":"#a3a3a3",whiteSpace:"normal",overflowWrap:"anywhere"}}>{line}</div>)}
    </div>}
    {timeline.length>0&&<details style={{marginTop:8}}>
      <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontWeight:800}}>상세 흐름</summary>
      <div style={{marginTop:8,display:"grid",gap:6}}>
        {timeline.slice(0,8).map((item,i)=><FlowiActionLogStep key={item.stage||i} item={item}/>)}
      </div>
    </details>}
    {disclaimer&&<div style={{marginTop:7,fontSize:14,color:"#737373",lineHeight:1.4}}>{disclaimer}</div>}
  </details>;
}

function FlowiActionLogStep({item}){
  const color=flowiTraceStatusColor(item?.status);
  const apiRefs=Array.isArray(item?.api_refs)?item.api_refs:[];
  const evidenceRefs=Array.isArray(item?.evidence_refs)?item.evidence_refs:[];
  const detail=[item?.detail,evidenceRefs.length?`근거 ${evidenceRefs.slice(0,4).join(", ")}`:"",apiRefs.length?`API ${apiRefs.length}`:""].filter(Boolean).join(" · ");
  return <div style={{display:"grid",gridTemplateColumns:"18px minmax(104px,150px) minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.38}}>
    <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:10,border:`1px solid ${color}99`,color}}>{item?.status==="done"?"✓":item?.status==="blocked"?"!":"•"}</span>
    <span style={{color:"#d4d4d4",fontWeight:900,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={item?.stage||""}>{item?.stage||item?.title||"-"}</span>
    <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={detail}>{item?.title||""}{detail?` · ${detail}`:""}</span>
  </div>;
}

function FlowiTraceStrip({trace}){
  const steps=Array.isArray(trace?.steps)?trace.steps.filter(Boolean):[];
  const visibleSteps=steps.filter(s=>s.visible!==false);
  const activation=trace?.activation||{};
  const evidence=trace?.evidence||{};
  const validation=trace?.validation||{};
  const interpretation=trace?.interpretation||{};
  const missing=Array.isArray(interpretation?.missing_slots)?interpretation.missing_slots:[];
  const knowledge=Array.isArray(trace?.retrieved_knowledge)?trace.retrieved_knowledge:[];
  const apiCalls=Array.isArray(trace?.api_calls)?trace.api_calls:(Array.isArray(evidence?.api_calls)?evidence.api_calls:[]);
  const llmStep=steps.find(s=>s.key==="llm");
  if(!visibleSteps.length&&!activation.feature&&!evidence.used_feature_ai&&!knowledge.length&&!missing.length)return null;
  const chips=[
    evidence.used_feature_ai||activation.feature?[`기능 ${evidence.used_feature_ai||activation.feature}`,"#d4d4d4"]:null,
    activation.action?[`action ${activation.action}`,"#a3a3a3"]:null,
    validation.rows!==undefined?[`rows ${validation.rows}`,"#22c55e"]:null,
    knowledge.length?[`Wiki ${knowledge.length}건`,"#f97316"]:null,
    llmStep?[`LLM ${llmStep.status||"pending"}`,flowiTraceStatusColor(llmStep.status)]:null,
  ].filter(Boolean);
  const primary=visibleSteps.find(s=>s.key==="knowledge")||visibleSteps.find(s=>s.key==="tool")||visibleSteps[visibleSteps.length-1]||{};
  const primaryText=[primary.label||primary.title||primary.key,primary.detail].filter(Boolean).join(" · ");
  return <div style={{margin:"10px 0 0",border:"1px solid #262626",borderRadius:8,background:"#101010",padding:"7px 9px",fontFamily:"monospace"}}>
    <div style={{display:"flex",alignItems:"center",gap:7,flexWrap:"wrap"}}>
      <span style={{fontSize:14,color:"#737373",fontWeight:900}}>실행 로그</span>
      {primaryText&&<span style={{fontSize:14,color:"#a3a3a3",minWidth:0,flex:"1 1 260px",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={primaryText}>{primaryText}</span>}
    </div>
    {chips.length>0&&<div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:7}}>
      {chips.slice(0,5).map(([label,color])=><span key={label} style={{fontSize:14,color,border:"1px solid #2a2a2a",borderRadius:999,padding:"2px 7px",background:"#151515",whiteSpace:"nowrap"}}>{label}</span>)}
      {apiCalls.length>0&&<span style={{fontSize:14,color:"#737373",border:"1px solid #2a2a2a",borderRadius:999,padding:"2px 7px",background:"#151515",whiteSpace:"nowrap"}}>API {apiCalls.length}회</span>}
    </div>}
    {missing.length>0&&<div style={{marginTop:7,fontSize:14,color:"#f97316",lineHeight:1.4}}>
      필요한 값: {missing.join(", ")}. 아래 선택지나 직접 입력으로 이어서 진행합니다.
    </div>}
  </div>;
}

function FlowiTrace({trace}){
  const steps=Array.isArray(trace?.steps)?trace.steps:[];
  if(!steps.length&&!trace?.interpretation&&!trace?.evidence&&!trace?.validation)return null;
  const activation=trace?.activation||{};
  const interpretation=trace?.interpretation||{};
  const inputSlots=interpretation?.input_slots||{};
  const evidence=trace?.evidence||{};
  const validation=trace?.validation||{};
  const subagentChildren=Array.isArray(trace?.subagent_context?.children)?trace.subagent_context.children:[];
  const missing=Array.isArray(interpretation?.missing_slots)?interpretation.missing_slots:[];
  const warnings=Array.isArray(validation?.warnings)?validation.warnings:[];
  const knowledge=Array.isArray(trace?.retrieved_knowledge)?trace.retrieved_knowledge:[];
  const termResolution=Array.isArray(interpretation?.term_resolution)?interpretation.term_resolution:[];
  const apiCalls=Array.isArray(trace?.api_calls)?trace.api_calls:(Array.isArray(evidence?.api_calls)?evidence.api_calls:[]);
  return(<details style={{marginTop:8,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"7px 9px"}}>
    <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontFamily:"monospace",fontWeight:800}}>
      실행 로그 <span style={{fontWeight:400,color:"#737373"}}>사용한 근거와 호출한 기능</span>
    </summary>
    <div style={{marginTop:8,display:"grid",gap:8,fontFamily:"monospace"}}>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(170px,1fr))",gap:6}}>
        <FlowiTraceKV label="기능 AI" value={evidence.used_feature_ai||activation.feature}/>
        <FlowiTraceKV label="Unit action" value={activation.action}/>
        <FlowiTraceKV label="Endpoint" value={evidence.endpoint||activation.api||activation.endpoint}/>
        <FlowiTraceKV label="검증" value={[validation.rows!==undefined?`rows ${validation.rows}`:"",validation.chart_readiness?`chart ${validation.chart_readiness}`:"",validation.fallback?"fallback":""].filter(Boolean).join(" · ")}/>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(170px,1fr))",gap:6}}>
        <FlowiTraceKV label="product" value={inputSlots.product}/>
        <FlowiTraceKV label="lot" value={Array.isArray(inputSlots.lot)?inputSlots.lot.join(", "):inputSlots.lot}/>
        <FlowiTraceKV label="wafer" value={Array.isArray(inputSlots.wafer)?inputSlots.wafer.join(", "):inputSlots.wafer}/>
        <FlowiTraceKV label="step/item" value={[inputSlots.step,inputSlots.item].filter(Boolean).map(v=>Array.isArray(v)?v.join(", "):v).join(" / ")}/>
        <FlowiTraceKV label="meeting" value={[inputSlots.meeting,inputSlots.session].filter(Boolean).join(" · ")}/>
        <FlowiTraceKV label="source" value={Array.isArray(inputSlots.source_candidates)?inputSlots.source_candidates.join(", "):inputSlots.source_candidates}/>
      </div>
      {evidence.sql&&<FlowiTraceKV label="SQL/filter" value={evidence.sql} wide/>}
      {Array.isArray(evidence.selected_columns)&&evidence.selected_columns.length>0&&<FlowiTraceKV label="선택 컬럼" value={evidence.selected_columns.slice(0,12).join(", ")} wide/>}
      {Array.isArray(evidence.source_ids)&&evidence.source_ids.length>0&&<FlowiTraceKV label="source ids" value={evidence.source_ids.slice(0,6).join(", ")} wide/>}
      {Array.isArray(evidence.relation_ids)&&evidence.relation_ids.length>0&&<FlowiTraceKV label="confirmed relations" value={evidence.relation_ids.slice(0,6).join(", ")} wide/>}
      {Array.isArray(evidence.join_keys)&&evidence.join_keys.length>0&&<FlowiTraceKV label="join keys" value={evidence.join_keys.slice(0,8).join(", ")} wide/>}
      {missing.length>0&&<FlowiTraceKV label="빈칸 보완" value={missing.join(", ")} wide tone="#f97316"/>}
      {warnings.length>0&&<FlowiTraceKV label="warnings" value={warnings.slice(0,4).join(" · ")} wide tone="#fbbf24"/>}
      <FlowiTermResolution rows={termResolution}/>
      {knowledge.length>0&&<FlowiKnowledgeTrace rows={knowledge}/>}
      <FlowiFilterTrace rows={termResolution} filters={evidence.filters}/>
      <FlowiValidationTrace validation={validation}/>
      {subagentChildren.length>0&&<div style={{display:"grid",gap:4,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"6px 7px"}}>
        <div style={{fontSize:14,color:"#737373",marginBottom:2}}>subagent chain</div>
        {subagentChildren.slice(0,8).map((c,i)=><div key={`${c.name||"child"}-${i}`} style={{display:"grid",gridTemplateColumns:"18px minmax(90px,150px) 72px minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
          <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:14,border:`1px solid ${flowiTraceStatusColor(c.status)}99`,color:flowiTraceStatusColor(c.status)}}>{c.status==="done"?"✓":c.status==="error"?"!":i+1}</span>
          <span style={{color:"#d4d4d4",fontWeight:800,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={c.name||""}>{c.name||"-"}</span>
          <span style={{color:"#a3a3a3"}}>{Number(c.took_ms||0)}ms</span>
          <span style={{color:c.error?"#fca5a5":"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={c.error||c.action||c.intent||""}>{c.error||c.action||c.intent||""}</span>
        </div>)}
      </div>}
      {steps.map((s,i)=><div key={s.key||i} style={{display:"grid",gridTemplateColumns:"18px 118px minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.4}}>
        <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:14,border:`1px solid ${flowiTraceStatusColor(s.status)}99`,color:flowiTraceStatusColor(s.status)}}>{s.status==="done"?"✓":s.status==="blocked"?"!":i+1}</span>
        <span style={{color:"#d4d4d4",fontWeight:800}}>{s.label||s.title||s.key}</span>
        <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={s.detail||""}>{s.detail||""}</span>
      </div>)}
      {apiCalls.length>0&&<div style={{display:"grid",gap:4}}>
        {apiCalls.slice(0,4).map((c,i)=><div key={i} style={{display:"grid",gridTemplateColumns:"92px minmax(0,1fr) 72px",gap:7,fontSize:14,lineHeight:1.35}}>
          <span style={{color:"#737373"}}>{c.method||c.stage}</span>
          <span style={{color:"#a3a3a3",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={c.path||c.callee||""}>{c.path||c.callee||"-"}</span>
          <span style={{color:flowiTraceStatusColor(c.status)}}>{c.status||""}</span>
        </div>)}
      </div>}
      {trace.note&&<div style={{marginTop:4,fontSize:14,color:"#737373",lineHeight:1.45}}>{trace.note}</div>}
    </div>
  </details>);
}

function FlowiTermResolution({rows}){
  const items=Array.isArray(rows)?rows.filter(Boolean).slice(0,8):[];
  if(!items.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{fontSize:14,color:"#737373"}}>단어 해석</div>
    {items.map((row,i)=>{
      const refs=Array.isArray(row.wiki_refs)?row.wiki_refs.filter(Boolean).slice(0,3).join(", "):"";
      const meta=[row.meaning,row.status].filter(Boolean).join(" · ");
      return <div key={`${row.token||"term"}-${i}`} style={{display:"grid",gridTemplateColumns:"minmax(76px,140px) minmax(0,1fr)",gap:8,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
        <span style={{color:"#e5e5e5",fontWeight:900,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={row.token||""}>{row.token||"-"}</span>
        <span style={{color:"#a3a3a3",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={[meta,refs].filter(Boolean).join(" / ")}>{meta}{refs?` / ${refs}`:""}</span>
      </div>;
    })}
  </div>;
}

function FlowiKnowledgeTrace({rows}){
  const items=Array.isArray(rows)?rows.filter(Boolean).slice(0,8):[];
  if(!items.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{display:"flex",gap:7,alignItems:"baseline",justifyContent:"space-between"}}>
      <div style={{fontSize:14,color:"#737373"}}>참고한 Wiki / Schema</div>
      <div style={{fontSize:14,color:"#737373"}}>{items.length} hits</div>
    </div>
    {items.map((row,i)=>{
      const id=String(row.id||row.doc_id||"");
      const title=String(row.title||id||"knowledge");
      const meta=[row.kind,row.term?`term ${row.term}`:"",row.relation_id&&row.column?`${row.relation_id}.${row.column}`:"",row.source].filter(Boolean).join(" · ");
      return <div key={`${id}-${i}`} style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) minmax(90px,160px)",gap:8,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
        <span style={{color:"#d4d4d4",fontWeight:800,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={title}>{title}</span>
        <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",textAlign:"right"}} title={meta||id}>{meta||id}</span>
      </div>;
    })}
  </div>;
}

function FlowiFilterTrace({rows,filters}){
  const lines=[];
  for(const row of Array.isArray(rows)?rows:[]){
    if(row?.query_filter)lines.push(`${row.token||"term"}: ${row.query_filter}`);
  }
  const filterKeys=filters&&typeof filters==="object"?Object.entries(filters).filter(([,v])=>v!==undefined&&v!==null&&v!==""&&!(Array.isArray(v)&&!v.length)).slice(0,8):[];
  if(!lines.length&&!filterKeys.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{fontSize:14,color:"#737373"}}>실행 필터</div>
    {lines.slice(0,8).map((line,i)=><div key={`filter-${i}`} style={{fontSize:14,color:"#d4d4d4",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={line}>{line}</div>)}
    {filterKeys.length>0&&<div style={{fontSize:14,color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={JSON.stringify(Object.fromEntries(filterKeys))}>
      filters {JSON.stringify(Object.fromEntries(filterKeys))}
    </div>}
  </div>;
}

function FlowiValidationTrace({validation}){
  if(!validation||typeof validation!=="object")return null;
  const warnings=Array.isArray(validation.warnings)?validation.warnings:[];
  const lines=[
    validation.rows!==undefined?`결과 ${validation.rows}건`:"",
    validation.chart_readiness?`chart ${validation.chart_readiness}`:"",
    validation.source_count!==undefined?`근거 ${validation.source_count}건`:"",
    validation.fallback?"fallback 사용":"",
  ].filter(Boolean);
  if(!lines.length&&!warnings.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{fontSize:14,color:"#737373"}}>결과 검증</div>
    <div style={{fontSize:14,color:"#d4d4d4"}}>{lines.join(" · ")}</div>
    {warnings.slice(0,4).map((w,i)=><div key={`warn-${i}`} style={{fontSize:14,color:"#fbbf24",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={w}>{w}</div>)}
  </div>;
}

function FlowiTraceKV({label,value,wide=false,tone="#d4d4d4"}){
  const text=Array.isArray(value)?value.join(", "):String(value??"");
  if(!text)return null;
  return <div style={{gridColumn:wide?"1 / -1":undefined,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"6px 7px",minWidth:0}}>
    <div style={{fontSize:14,color:"#737373",marginBottom:3}}>{label}</div>
    <div style={{fontSize:14,color:tone,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={text}>{text}</div>
  </div>;
}

const FLOWI_CHOICE_BTN={textAlign:"left",border:`1px solid ${HOME_UI.accent}`,borderRadius:6,background:"#1f130b",padding:"7px 10px",cursor:"pointer",color:HOME_UI.textSoft,fontSize:14,fontFamily:"'JetBrains Mono',monospace",lineHeight:1.35};

function FlowiChoices({question,choices,onChoice,onNavigate}){
  return(<div style={{marginTop:8}}>
    <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace",marginBottom:7}}>{question||"어떻게 진행할까요?"}</div>
    <div style={{display:"flex",gap:7,flexWrap:"wrap"}}>
      {choices.map((c,i)=><button key={c.id||i} type="button" onClick={()=>{
        const tab=c.tab||c.feature||"";
        if(tab&&typeof onNavigate==="function")onNavigate(tab);
        else if(onChoice)onChoice(c.submit_prompt||c.prompt||c.value||c.title||"",{displayText:c.title||c.value||c.label||"선택"});
      }}
        onMouseEnter={e=>{e.currentTarget.style.background="#3a3a3a";}}
        onMouseLeave={e=>{e.currentTarget.style.background="#2a2a2a";}}
        style={{...FLOWI_CHOICE_BTN,minWidth:150,maxWidth:"100%"}}>
        <span style={{fontWeight:900,color:"#f97316",marginRight:7}}>{c.label||i+1}</span>
        <span style={{fontWeight:900,color:"#e5e5e5"}}>{c.title||c.value}</span>
      </button>)}
    </div>
  </div>);
}

function FlowiWalkthrough({data}){
  const entries=Array.isArray(data.entries)?data.entries:[];
  const remaining=Array.isArray(data.modules_remaining)?data.modules_remaining:[];
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#111",padding:"9px 10px"}}>
    <div style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap",marginBottom:7}}>
      <span style={{fontSize:14,color:"#f97316",fontWeight:900,fontFamily:"monospace"}}>inform walkthrough</span>
      {data.current_module&&<span style={{fontSize:14,color:"#e5e5e5",fontFamily:"monospace"}}>현재 {data.current_module}</span>}
      <span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>완료 {entries.length} · 남음 {remaining.length}</span>
    </div>
    {entries.length>0&&<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:6}}>
      {entries.slice(0,8).map((e,i)=><div key={i} style={{border:"1px solid #2a2a2a",borderRadius:6,padding:"6px 7px",background:"#151515",fontSize:14,lineHeight:1.45}}>
        <div style={{color:"#e5e5e5",fontWeight:800}}>{e.module||"-"}</div>
        <div style={{color:"#a3a3a3",fontFamily:"monospace"}}>{e.split_set||e.reason||"-"}</div>
      </div>)}
    </div>}
  </div>);
}

function FlowiArgumentChoices({data,basePrompt,onChoice}){
  const fields=Array.isArray(data?.fields)?data.fields:[];
  const[free,setFree]=useState({});
  if(!fields.length)return null;
  const submit=(field,value)=>{
    const val=String(value||"").trim();
    if(!val)return;
    const payload=field?`${field}: ${val}`:val;
    if(onChoice)onChoice(payload,{displayText:val});
  };
  return(<div style={{marginTop:12,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"10px 11px"}}>
    <div style={{display:"grid",gap:9}}>
      {fields.map(f=>{
        const choices=Array.isArray(f.choices)?f.choices:[];
        return <div key={f.field} style={{display:"grid",gap:6}}>
          <div style={{fontSize:14,color:"#e5e5e5",fontWeight:900,fontFamily:"'JetBrains Mono',monospace"}}>{f.question||flowiFieldQuestion(f.field)}</div>
          <div style={{display:"flex",gap:7,flexWrap:"wrap"}}>
            {choices.filter(c=>!c.free_input).slice(0,3).map(c=><button key={c.id||c.value} type="button" onClick={()=>submit(f.field,c.value)}
              onMouseEnter={e=>{e.currentTarget.style.background="#3a3a3a";}}
              onMouseLeave={e=>{e.currentTarget.style.background="#2a2a2a";}}
              style={{...FLOWI_CHOICE_BTN,minWidth:112}}>
              <span style={{color:"#f97316",fontWeight:900,marginRight:7}}>{c.label}</span>{c.title||c.value}
            </button>)}
          </div>
          <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"'JetBrains Mono',monospace"}}>{data.message||"또는 직접 입력해 주세요"}</div>
          <div style={{display:"flex",gap:6,minWidth:0,alignItems:"stretch"}}>
            <input value={free[f.field]||""} onChange={e=>setFree(v=>({...v,[f.field]:e.target.value}))} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();submit(f.field,free[f.field]||"");}}} placeholder={f.free_input_label||"직접 입력"} style={{flex:1,minWidth:0,border:"1px solid #333",borderRadius:7,background:"#171717",color:"#e5e5e5",fontSize:14,padding:"8px 10px",fontFamily:"'JetBrains Mono',monospace",boxSizing:"border-box"}}/>
            <button type="button" onClick={()=>submit(f.field,free[f.field]||"")} style={{border:"1px solid #f97316",borderRadius:7,background:"#2a2a2a",color:"#f97316",fontSize:14,fontWeight:900,padding:"8px 12px",cursor:"pointer",fontFamily:"'JetBrains Mono',monospace"}}>보내기</button>
          </div>
        </div>;
      })}
    </div>
  </div>);
}

function FlowiMissingFreetext({fields,basePrompt,onChoice}){
  const items=Array.isArray(fields)?fields.filter(Boolean):[];
  const[values,setValues]=useState({});
  if(!items.length)return null;
  const submit=(item)=>{
    const key=item.key||item.label||"value";
    const val=String(values[key]||"").trim();
    if(!val)return;
    const label=String(item.label||key||"내용").trim();
    const payload=`${label}: ${val}`;
    if(onChoice)onChoice(payload,{displayText:payload});
  };
  return(<div style={{marginTop:12,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"10px 11px"}}>
    <div style={{display:"grid",gap:9}}>
      {items.map(item=>{
        const key=item.key||item.label||"value";
        return <div key={key} style={{display:"grid",gap:6}}>
          <label style={{fontSize:14,color:"#e5e5e5",fontWeight:900,fontFamily:"'JetBrains Mono',monospace"}}>{item.label||flowiFieldQuestion(key)}</label>
          <div style={{display:"flex",gap:6,minWidth:0,alignItems:"stretch"}}>
            <input value={values[key]||""}
              onChange={e=>setValues(v=>({...v,[key]:e.target.value}))}
              onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();submit(item);}else if(e.key==="Escape"){e.preventDefault();setValues(v=>({...v,[key]:""}));}}}
              placeholder={item.placeholder||"내용을 입력해 주세요"}
              autoFocus={items.length===1}
              style={{flex:1,minWidth:0,border:"1px solid #333",borderRadius:7,background:"#171717",color:"#e5e5e5",fontSize:14,padding:"8px 10px",fontFamily:"'JetBrains Mono',monospace",boxSizing:"border-box"}}/>
            <button type="button" onClick={()=>submit(item)} style={{border:"1px solid #f97316",borderRadius:7,background:"#2a2a2a",color:"#f97316",fontSize:14,fontWeight:900,padding:"8px 12px",cursor:"pointer",fontFamily:"'JetBrains Mono',monospace"}}>보내기</button>
          </div>
        </div>;
      })}
    </div>
  </div>);
}

function flowiFieldQuestion(field){
  const map={product:"어느 제품인가요?",module:"어느 모듈인가요?",root_lot_ids:"어느 Root Lot인가요?",root_lot_id:"어느 Root Lot인가요?",lot_ids:"어느 Lot인가요?",fab_lot_ids:"어느 Fab Lot인가요?",root_lot_id_or_fab_lot_id:"어느 Lot인가요?",step:"어느 Step인가요?",metric:"어느 항목인가요?",metrics_or_items:"어느 항목인가요?",knob_value:"어떤 KNOB 값인가요?",source_type:"어느 Source인가요?",split_set:"SplitTable은 어떤 Split으로 진행할까요?",wafer_ids:"어느 Wafer인가요?"};
  return map[field]||`${field} 값을 알려주세요.`;
}

function FlowiNextActions({actions,onNavigate,onChoice}){
  return(<div style={{marginTop:10,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"8px 9px"}}>
    <div style={{fontSize:14,fontWeight:900,color:"#a3a3a3",fontFamily:"'JetBrains Mono',monospace",marginBottom:6}}>후속 작업</div>
    <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
      {actions.map((a,i)=>{
        const clickable=(a.type==="open_tab"&&a.tab&&typeof onNavigate==="function")||(a.prompt&&typeof onChoice==="function");
        const click=()=>{if(a.type==="open_tab"&&a.tab&&onNavigate)onNavigate(a.tab);else if(a.prompt&&onChoice)onChoice(a.prompt,{displayText:a.title||a.label||"선택"});};
        return <button key={a.id||i} type="button" onClick={click} disabled={!clickable} title={a.description||""}
          style={{fontSize:14,color:clickable?"#f97316":"#a3a3a3",fontFamily:"monospace",border:"1px solid "+(clickable?"#7c2d12":"#333"),borderRadius:999,padding:"3px 8px",background:clickable?"#1f130b":"#171717",cursor:clickable?"pointer":"default",opacity:clickable?1:.82}}>
          {a.title||a.type}
        </button>;
      })}
    </div>
  </div>);
}

function FlowiEdmProposal({result}){
  const proposal=result.proposal||{};
  const confirm=result.confirm||"";
  const[busy,setBusy]=useState(false);
  const[execResult,setExecResult]=useState(null);
  const[err,setErr]=useState("");
  const run=()=>{
    if(!proposal.action_id||!confirm||busy)return;
    if(!window.confirm(`${proposal.summary||proposal.action_type}\n\nEDM 작업을 실행할까요?`))return;
    setBusy(true);setErr("");
    postJson("/api/llm/flowi/edm/execute",{proposal_id:proposal.action_id,confirm})
      .then(setExecResult)
      .catch(e=>setErr(e.message||String(e)))
      .finally(()=>setBusy(false));
  };
  return(<div style={{marginTop:10,border:"1px solid #7c2d12",borderRadius:8,background:"#1f130b",padding:"9px 10px"}}>
    <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
      <b style={{fontSize:14,color:"#fb923c",fontFamily:"monospace"}}>EDM proposal</b>
      <span style={{fontSize:14,color:"#e5e5e5",fontFamily:"monospace"}}>{proposal.action_type}</span>
      {proposal.file&&<span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>{proposal.file}</span>}
      <button onClick={run} disabled={busy||!!execResult?.ok} style={{marginLeft:"auto",padding:"4px 10px",borderRadius:5,border:"none",background:execResult?.ok?"#64748b":"#f97316",color:"#fff",fontSize:14,fontWeight:800,cursor:busy||execResult?.ok?"default":"pointer"}}>{busy?"실행중":execResult?.ok?"실행됨":"확인 실행"}</button>
    </div>
    <div style={{fontSize:14,color:"#d4d4d4",marginTop:6,lineHeight:1.5}}>{proposal.summary}</div>
    <div style={{fontSize:12,color:"#a3a3a3",marginTop:5,fontFamily:"monospace"}}>confirm {confirm}</div>
    {err&&<div style={{fontSize:14,color:"#fca5a5",marginTop:6}}>{err}</div>}
    {execResult&&<pre style={{margin:"8px 0 0",maxHeight:160,overflow:"auto",fontSize:12,color:"#d4d4d4",whiteSpace:"pre-wrap"}}>{JSON.stringify(execResult.result||execResult,null,2)}</pre>}
  </div>);
}

function FlowiChartPlan({chart}){
  const metrics=Array.isArray(chart.metrics)?chart.metrics:[];
  const ops=Array.isArray(chart.operations)?chart.operations:[];
  const requires=Array.isArray(chart.requires)?chart.requires:[];
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101827",padding:"9px 10px"}}>
    <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",marginBottom:7}}>
      <div style={{fontSize:14,fontWeight:900,color:"#dbeafe",fontFamily:"'JetBrains Mono',monospace"}}>Dashboard chart plan</div>
      <span style={{fontSize:14,color:requires.length?"#f97316":"#22c55e",fontFamily:"monospace"}}>{requires.length?"needs confirmation":"ready to route"}</span>
    </div>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:6,fontSize:14,color:"#bfdbfe",fontFamily:"monospace"}}>
      <div>kind: {chart.kind||"scatter"}</div>
      <div>source: {(chart.sources||[]).join(", ")||"-"}</div>
      <div>ops: {ops.join(", ")||"-"}</div>
      <div>join: {chart.join_key||"lot_wf"}</div>
      <div>INLINE: {chart.aggregations?.INLINE||"avg"}</div>
      <div>ET: {chart.aggregations?.ET||"median"}</div>
    </div>
    {metrics.length>0&&<div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap"}}>
      {metrics.slice(0,10).map(m=><span key={m.metric} style={{fontSize:14,color:"#dbeafe",background:"#1e3a8a66",border:"1px solid #3b82f666",borderRadius:999,padding:"2px 7px"}}>{m.metric}</span>)}
    </div>}
  </div>);
}

function FlowiScatterResult({data}){
  const plotlyType=String(data?.chart_type||data?.chart_config?.chart_type||data?.config?.chart_type||data?.kind||"").replace("dashboard_","");
  if(["pie","donut","bar"].includes(plotlyType)&&Array.isArray(data.groups)&&data.groups.length)return <div style={{marginTop:10,border:"1px solid #d1d5db",borderRadius:8,background:"#ffffff",padding:"8px 10px",minWidth:0}}>
    <FlowPlotlyChart chart={data} cfg={data.chart_config||data.config||data.config_overrides||data} height={430} dark={false} />
  </div>;
  if(Array.isArray(data.series)&&data.series.length)return <FlowiLineResult data={data}/>;
  if(Array.isArray(data.groups)&&data.groups.length)return <FlowiGroupBarResult data={data}/>;
  if(Array.isArray(data.boxes)&&data.boxes.length)return <FlowiBoxResult data={data}/>;
  if(data.kind==="dashboard_wafer_map"&&Array.isArray(data.points))return <FlowiWaferMapResult data={data}/>;
  const rawPts=Array.isArray(data.points)?data.points:[];
  if(rawPts.length)return <div style={{marginTop:10,border:"1px solid #d1d5db",borderRadius:8,background:"#ffffff",padding:"8px 10px",minWidth:0}}>
    <FlowPlotlyChart chart={data} cfg={data.chart_config||data.config||data.config_overrides||data} height={430} dark={false} />
    <div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#475569",fontFamily:"monospace"}}>
      <span style={{border:"1px solid #cbd5e1",borderRadius:999,padding:"2px 7px"}}>join {Array.isArray(data.join_cols)?data.join_cols.join("+"):"lot_wf"} · {data.join_how||"left"}</span>
      {data.aggregations?.INLINE&&<span style={{border:"1px solid #cbd5e1",borderRadius:999,padding:"2px 7px"}}>INLINE {data.aggregations.INLINE}</span>}
      {data.aggregations?.ET&&<span style={{border:"1px solid #cbd5e1",borderRadius:999,padding:"2px 7px"}}>ET {data.aggregations.ET}</span>}
      {data.color_by&&<span style={{border:"1px solid #cbd5e1",borderRadius:999,padding:"2px 7px"}}>color {data.color_by}</span>}
    </div>
  </div>;
  const pts=Array.isArray(data.points)?data.points.filter(p=>Number.isFinite(Number(p.x))&&Number.isFinite(Number(p.y))):[];
  if(!pts.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 numeric point가 없습니다.</div>;
  const W=980,H=480,pad={l:70,r:28,t:28,b:58};
  const xs=pts.map(p=>Number(p.x)),ys=pts.map(p=>Number(p.y));
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const rx=maxX-minX||1,ry=maxY-minY||1;
  const sx=(v)=>pad.l+(Number(v)-minX)/rx*(W-pad.l-pad.r);
  const sy=(v)=>pad.t+(H-pad.t-pad.b)-(Number(v)-minY)/ry*(H-pad.t-pad.b);
  const fit=data.fit&&Number.isFinite(Number(data.fit.slope))&&Number.isFinite(Number(data.fit.intercept))?data.fit:null;
  const x0=minX,x1=maxX,y0=fit?fit.slope*x0+fit.intercept:null,y1=fit?fit.slope*x1+fit.intercept:null;
  const palette=["#3b82f6","#f97316","#22c55e","#eab308","#a855f7","#06b6d4","#ef4444","#84cc16","#ec4899","#14b8a6"];
  const colorValues=(Array.isArray(data.color_values)&&data.color_values.length?data.color_values.map(v=>String(v.value??"")).filter(Boolean):[...new Set(pts.map(p=>String(p.color_value??"")).filter(Boolean))]).slice(0,10);
  const colorMap=new Map(colorValues.map((v,i)=>[v,palette[i%palette.length]]));
  const missingColor=data.color_missing==="gray"?"#9ca3af":"#3b82f6";
  const colorFor=(p)=>String(p.color_value??"")?colorMap.get(String(p.color_value??""))||"#3b82f6":missingColor;
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"12px 14px",minWidth:0}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8,flexWrap:"wrap"}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",minWidth:0,flex:"1 1 320px",overflowWrap:"anywhere"}}>{data.title||"Flowi scatter"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",minWidth:0,flex:"1 1 260px",textAlign:"right",overflowWrap:"anywhere"}}>n={data.total||pts.length} · corr={data.corr??"-"}{fit?` · R²=${fit.r2}`:""}{data.color_by?` · color=${data.color_by}`:""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block",minHeight:360}}>
      {[0,0.5,1].map((f)=><g key={`y${f}`}>
        <line x1={pad.l} x2={W-pad.r} y1={pad.t+(H-pad.t-pad.b)*(1-f)} y2={pad.t+(H-pad.t-pad.b)*(1-f)} stroke="#333" strokeDasharray="3,4"/>
        <text x={pad.l-10} y={pad.t+(H-pad.t-pad.b)*(1-f)+4} textAnchor="end" fontSize="11" fill="#a3a3a3">{(minY+ry*f).toFixed(2)}</text>
      </g>)}
      {[0,0.5,1].map((f)=><g key={`x${f}`}>
        <line y1={pad.t} y2={H-pad.b} x1={pad.l+(W-pad.l-pad.r)*f} x2={pad.l+(W-pad.l-pad.r)*f} stroke="#262626" strokeDasharray="2,5"/>
        <text x={pad.l+(W-pad.l-pad.r)*f} y={H-28} textAnchor="middle" fontSize="11" fill="#a3a3a3">{(minX+rx*f).toFixed(2)}</text>
      </g>)}
      <line x1={pad.l} x2={W-pad.r} y1={H-pad.b} y2={H-pad.b} stroke="#525252"/>
      <line x1={pad.l} x2={pad.l} y1={pad.t} y2={H-pad.b} stroke="#525252"/>
      {fit&&<line x1={sx(x0)} y1={sy(y0)} x2={sx(x1)} y2={sy(y1)} stroke="#ef4444" strokeWidth="2" strokeDasharray="7,4"/>}
      {pts.slice(0,900).map((p,i)=><circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="3.8" fill={colorFor(p)} opacity="0.78">
        <title>{`${p.label||p.join_key||""}\nX=${p.x}\nY=${p.y}${p.color_value?`\n${data.color_by||"color"}=${p.color_value}`:""}\nINLINE n=${p.inline_n||0}, ET n=${p.et_n||0}`}</title>
      </circle>)}
      <text x={(pad.l+W-pad.r)/2} y={H-8} textAnchor="middle" fontSize="12" fill="#f97316">{data.x_label||"x"}</text>
      <text x="16" y={(pad.t+H-pad.b)/2} transform={`rotate(-90,16,${(pad.t+H-pad.b)/2})`} textAnchor="middle" fontSize="12" fill="#f97316">{data.y_label||"y"}</text>
    </svg>
    <div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>join {Array.isArray(data.join_cols)?data.join_cols.join("+"):"lot_wf"} · {data.join_how||"left"}</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>INLINE avg</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>ET median</span>
      {data.color_by&&<span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>color {data.color_by}</span>}
      {colorValues.map(v=><span key={v} style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px",display:"inline-flex",alignItems:"center",gap:5}}>
        <span style={{width:8,height:8,borderRadius:999,background:colorMap.get(v),display:"inline-block"}}></span>{v}
      </span>)}
    </div>
  </div>);
}

function FlowiBoxResult({data}){
  const boxes=(Array.isArray(data.boxes)?data.boxes:[]).filter(b=>["min","q1","median","q3","max"].every(k=>Number.isFinite(Number(b[k])))).slice(0,18);
  if(!boxes.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 box 값이 없습니다.</div>;
  const W=900,H=420,pad={l:66,r:28,t:28,b:78};
  const vals=boxes.flatMap(b=>[Number(b.min),Number(b.max),Number(b.q1),Number(b.q3),Number(b.median)]);
  const minY=Math.min(...vals),maxY=Math.max(...vals),ry=maxY-minY||1;
  const sy=(v)=>pad.t+(H-pad.t-pad.b)-(Number(v)-minY)/ry*(H-pad.t-pad.b);
  const step=(W-pad.l-pad.r)/boxes.length;
  const boxW=Math.max(12,Math.min(34,step*.48));
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"12px 14px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8,flexWrap:"wrap"}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",minWidth:0,flex:"1 1 320px",overflowWrap:"anywhere"}}>{data.title||"Flowi box plot"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",minWidth:0,flex:"1 1 220px",textAlign:"right",overflowWrap:"anywhere"}}>groups={data.total||boxes.length} · {data.metric||""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block",minHeight:340}}>
      {[0,0.5,1].map(f=><g key={f}>
        <line x1={pad.l} x2={W-pad.r} y1={pad.t+(H-pad.t-pad.b)*(1-f)} y2={pad.t+(H-pad.t-pad.b)*(1-f)} stroke="#333" strokeDasharray="3,4"/>
        <text x={pad.l-10} y={pad.t+(H-pad.t-pad.b)*(1-f)+4} textAnchor="end" fontSize="11" fill="#a3a3a3">{(minY+ry*f).toFixed(2)}</text>
      </g>)}
      <line x1={pad.l} x2={W-pad.r} y1={H-pad.b} y2={H-pad.b} stroke="#525252"/>
      <line x1={pad.l} x2={pad.l} y1={pad.t} y2={H-pad.b} stroke="#525252"/>
      {boxes.map((b,i)=>{
        const cx=pad.l+step*i+step/2;
        const yMin=sy(b.min),yQ1=sy(b.q1),yMed=sy(b.median),yQ3=sy(b.q3),yMax=sy(b.max);
        return <g key={b.label||i}>
          <line x1={cx} x2={cx} y1={yMax} y2={yMin} stroke="#f97316" strokeWidth="1.4"/>
          <line x1={cx-boxW*.35} x2={cx+boxW*.35} y1={yMax} y2={yMax} stroke="#f97316" strokeWidth="1.4"/>
          <line x1={cx-boxW*.35} x2={cx+boxW*.35} y1={yMin} y2={yMin} stroke="#f97316" strokeWidth="1.4"/>
          <rect x={cx-boxW/2} y={Math.min(yQ1,yQ3)} width={boxW} height={Math.max(2,Math.abs(yQ3-yQ1))} rx="3" fill="#f9731633" stroke="#f97316" strokeWidth="1.4"/>
          <line x1={cx-boxW/2} x2={cx+boxW/2} y1={yMed} y2={yMed} stroke="#e5e5e5" strokeWidth="1.6"/>
          <text x={cx} y={H-42} textAnchor="end" transform={`rotate(-38 ${cx} ${H-42})`} fontSize="10" fill="#a3a3a3">{String(b.label||"-").slice(0,14)}</text>
          <title>{`${b.label||""}\nmin=${b.min}\nq1=${b.q1}\nmedian=${b.median}\nq3=${b.q3}\nmax=${b.max}\nmean=${b.mean??"-"}\nn=${b.n??"-"}`}</title>
        </g>;
      })}
      <text x="16" y={(pad.t+H-pad.b)/2} transform={`rotate(-90,16,${(pad.t+H-pad.b)/2})`} textAnchor="middle" fontSize="12" fill="#f97316">{data.y_label||"value"}</text>
    </svg>
    <div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>median / IQR</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{data.x_label||"group"}</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{data.y_label||data.metric||"value"}</span>
    </div>
  </div>);
}

function FlowiWaferMapResult({data}){
  const pts=(Array.isArray(data.points)?data.points:[]).filter(p=>Number.isFinite(Number(p.x))&&Number.isFinite(Number(p.y))&&Number.isFinite(Number(p.value))).slice(0,900);
  if(!pts.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 WF map point가 없습니다.</div>;
  const W=360,H=360,pad=26;
  const xs=pts.map(p=>Number(p.x)),ys=pts.map(p=>Number(p.y)),vs=pts.map(p=>Number(p.value));
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),minV=Math.min(...vs),maxV=Math.max(...vs);
  const rx=maxX-minX||1,ry=maxY-minY||1,rv=maxV-minV||1;
  const sx=(v)=>pad+(Number(v)-minX)/rx*(W-pad*2);
  const sy=(v)=>H-pad-(Number(v)-minY)/ry*(H-pad*2);
  const color=(v)=>{const f=(Number(v)-minV)/rv;const r=Math.round(59+190*f),g=Math.round(130-70*f),b=Math.round(246-200*f);return `rgb(${r},${g},${b})`;};
  const cx=W/2,cy=H/2,rad=Math.min(W,H)/2-pad*.7;
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5"}}>{data.title||"Flowi WF map"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>points={data.total||pts.length} · {data.metric||""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block",maxWidth:520,margin:"0 auto"}}>
      <circle cx={cx} cy={cy} r={rad} fill="#0f172a" stroke="#334155" strokeWidth="1.5"/>
      <line x1={cx-rad} x2={cx+rad} y1={cy} y2={cy} stroke="#334155" strokeDasharray="4,4"/>
      <line x1={cx} x2={cx} y1={cy-rad} y2={cy+rad} stroke="#334155" strokeDasharray="4,4"/>
      {pts.map((p,i)=><circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="5" fill={color(p.value)} opacity=".88" stroke="#111827" strokeWidth=".7">
        <title>{`${p.label||`shot(${p.x},${p.y})`}\n${data.value_label||"value"}=${p.value}\nmean=${p.mean??"-"}\nn=${p.n??"-"}\nlot_count=${p.lot_count??"-"}\nwafer_count=${p.wafer_count??"-"}`}</title>
      </circle>)}
    </svg>
    <div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{data.source||"source"}</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{data.value_label||"median"}</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>range {minV.toFixed(3)}~{maxV.toFixed(3)}</span>
    </div>
  </div>);
}

function FlowiLineResult({data}){
  const series=(Array.isArray(data.series)?data.series:[]).map(s=>({...s,points:(Array.isArray(s.points)?s.points:[]).filter(p=>Number.isFinite(Number(p.y)))})).filter(s=>s.points.length);
  if(!series.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 trend point가 없습니다.</div>;
  const W=980,H=460,pad={l:70,r:28,t:28,b:62};
  const all=series.flatMap(s=>s.points.map((p,i)=>({...p,_i:i})));
  const ys=all.map(p=>Number(p.y));
  const minY=Math.min(...ys),maxY=Math.max(...ys),ry=maxY-minY||1;
  const maxN=Math.max(...series.map(s=>s.points.length),1);
  const sx=(i)=>pad.l+(maxN<=1?0:i/(maxN-1))*(W-pad.l-pad.r);
  const sy=(v)=>pad.t+(H-pad.t-pad.b)-(Number(v)-minY)/ry*(H-pad.t-pad.b);
  const palette=["#f97316","#3b82f6","#22c55e","#a855f7"];
  const pathFor=(pts)=>pts.map((p,i)=>`${i?"L":"M"}${sx(i).toFixed(2)},${sy(p.y).toFixed(2)}`).join(" ");
  const labelAt=(idx)=>{
    const pts=series[0].points;
    const p=pts[Math.max(0,Math.min(pts.length-1,idx))]||{};
    return p.x_label||p.bucket||String(p.x??idx);
  };
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"12px 14px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8,flexWrap:"wrap"}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",minWidth:0,flex:"1 1 320px",overflowWrap:"anywhere"}}>{data.title||"Flowi trend"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",minWidth:0,flex:"1 1 220px",textAlign:"right",overflowWrap:"anywhere"}}>points={data.total||series[0].points.length} · {data.metric||""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block",minHeight:350}}>
      {[0,0.5,1].map((f)=><g key={`y${f}`}>
        <line x1={pad.l} x2={W-pad.r} y1={pad.t+(H-pad.t-pad.b)*(1-f)} y2={pad.t+(H-pad.t-pad.b)*(1-f)} stroke="#333" strokeDasharray="3,4"/>
        <text x={pad.l-10} y={pad.t+(H-pad.t-pad.b)*(1-f)+4} textAnchor="end" fontSize="11" fill="#a3a3a3">{(minY+ry*f).toFixed(2)}</text>
      </g>)}
      {[0,0.5,1].map((f)=>{
        const idx=Math.round((maxN-1)*f);
        return <g key={`x${f}`}>
          <line y1={pad.t} y2={H-pad.b} x1={sx(idx)} x2={sx(idx)} stroke="#262626" strokeDasharray="2,5"/>
          <text x={sx(idx)} y={H-28} textAnchor="middle" fontSize="11" fill="#a3a3a3">{labelAt(idx)}</text>
        </g>;
      })}
      <line x1={pad.l} x2={W-pad.r} y1={H-pad.b} y2={H-pad.b} stroke="#525252"/>
      <line x1={pad.l} x2={pad.l} y1={pad.t} y2={H-pad.b} stroke="#525252"/>
      {series.map((s,si)=><g key={s.name||si}>
        <path d={pathFor(s.points)} fill="none" stroke={palette[si%palette.length]} strokeWidth="2.2"/>
        {s.points.map((p,i)=><circle key={i} cx={sx(i)} cy={sy(p.y)} r="3.7" fill={palette[si%palette.length]} opacity=".9">
          <title>{`${p.x_label||p.bucket||p.x}\n${s.name||data.metric||"value"}=${p.y}\nmean=${p.mean??"-"}\nn=${p.n??"-"}\nwafer_groups=${p.wafer_groups??"-"}`}</title>
        </circle>)}
      </g>)}
      <text x={(pad.l+W-pad.r)/2} y={H-8} textAnchor="middle" fontSize="12" fill="#f97316">{data.x_label||"x"}</text>
      <text x="16" y={(pad.t+H-pad.b)/2} transform={`rotate(-90,16,${(pad.t+H-pad.b)/2})`} textAnchor="middle" fontSize="12" fill="#f97316">{data.y_label||"y"}</text>
    </svg>
    <div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>
      {series.map((s,si)=><span key={s.name||si} style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px",display:"inline-flex",alignItems:"center",gap:5}}>
        <span style={{width:8,height:8,borderRadius:999,background:palette[si%palette.length],display:"inline-block"}}></span>{s.name||data.metric||"series"}
      </span>)}
      {data.color_by&&<span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>color {data.color_by}</span>}
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>INLINE median by date</span>
    </div>
  </div>);
}

function FlowiGroupBarResult({data}){
  const groups=(Array.isArray(data.groups)?data.groups:[]).map(g=>({...g,value:Number(g.value??g.median??g.mean)})).filter(g=>Number.isFinite(g.value)).slice(0,24);
  if(!groups.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 group 값이 없습니다.</div>;
  const W=920,H=Math.max(340,groups.length*28+76),pad={l:174,r:78,t:24,b:38};
  const minV=Math.min(0,...groups.map(g=>g.value)),maxV=Math.max(...groups.map(g=>g.value));
  const rv=maxV-minV||1;
  const sx=(v)=>pad.l+(Number(v)-minV)/rv*(W-pad.l-pad.r);
  const rowH=(H-pad.t-pad.b)/groups.length;
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"12px 14px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8,flexWrap:"wrap"}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",minWidth:0,flex:"1 1 320px",overflowWrap:"anywhere"}}>{data.title||"Flowi group chart"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",minWidth:0,flex:"1 1 220px",textAlign:"right",overflowWrap:"anywhere"}}>groups={data.total||groups.length} · {data.metric||""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block"}}>
      {[0,0.5,1].map(f=><g key={f}>
        <line x1={pad.l+(W-pad.l-pad.r)*f} x2={pad.l+(W-pad.l-pad.r)*f} y1={pad.t} y2={H-pad.b} stroke="#262626" strokeDasharray="2,5"/>
        <text x={pad.l+(W-pad.l-pad.r)*f} y={H-10} textAnchor="middle" fontSize="9" fill="#a3a3a3">{(minV+rv*f).toFixed(2)}</text>
      </g>)}
      {groups.map((g,i)=>{
        const y=pad.t+i*rowH+rowH*.18;
        const x0=sx(0),x1=sx(g.value);
        const x=Math.min(x0,x1),w=Math.max(2,Math.abs(x1-x0));
        return <g key={g.label||i}>
          <text x={pad.l-8} y={y+rowH*.42} textAnchor="end" fontSize="10" fill="#d4d4d4">{String(g.label||"-").slice(0,24)}</text>
          <rect x={x} y={y} width={w} height={Math.max(9,rowH*.62)} rx="3" fill="#f97316" opacity=".86"/>
          <text x={x1+6} y={y+rowH*.42} fontSize="10" fill="#a3a3a3">{g.value.toFixed(3)}</text>
          <title>{`${g.label||""}\nmedian=${g.median??g.value}\nmean=${g.mean??"-"}\nwafer_groups=${g.wafer_groups??"-"}\nmetric_n=${g.metric_n??"-"}`}</title>
        </g>;
      })}
      <line x1={sx(0)} x2={sx(0)} y1={pad.t} y2={H-pad.b} stroke="#525252"/>
      <text x={(pad.l+W-pad.r)/2} y={H-1} textAnchor="middle" fontSize="10" fill="#f97316">{data.y_label||"value"}</text>
    </svg>
    <div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>group {Array.isArray(data.group_by)?data.group_by.join("+"):"-"}</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>median</span>
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>join {Array.isArray(data.join_cols)?data.join_cols.join("+"):"root_lot_id+wafer_id"}</span>
    </div>
  </div>);
}

const FLOWI_FEEDBACK_TAGS=[
  ["correct","정확함"],
  ["explanation_gap","설명 부족"],
  ["wrong_data_source","잘못된 DB/컬럼"],
  ["wrong_workflow","workflow 다름"],
  ["missed_clarification","질문 필요"],
  ["too_slow","느림"],
  ["permission_risk","권한 우려"],
  ["output_issue","출력 문제"],
  ["hallucination","없는 값"],
  ["key_matching_error","key 매칭"],
  ["aggregation_error","집계 오류"],
];
const FLOWI_USER_FEEDBACK_KEYS=new Set(["correct","explanation_gap","missed_clarification","too_slow","output_issue","hallucination"]);
function FlowiFeedback({result,tool,prompt,isAdmin=false}){
  const[rating,setRating]=useState("");
  const[tags,setTags]=useState([]);
  const[note,setNote]=useState("");
  const[expectedWorkflow,setExpectedWorkflow]=useState("");
  const[correctRoute,setCorrectRoute]=useState("");
  const[dataRefs,setDataRefs]=useState("");
  const[golden,setGolden]=useState(false);
  const[msg,setMsg]=useState("");
  const[open,setOpen]=useState(false);
  const toggleTag=(key)=>{
    setTags(prev=>{
      const next=prev.includes(key)?prev.filter(x=>x!==key):[...prev,key];
      if(key==="correct"&&!prev.includes(key))setRating("up");
      if(key!=="correct"&&!prev.includes(key))setRating("down");
      return next;
    });
  };
  const send=(nextRating=rating)=>{
    const r=nextRating||((tags.length&&tags.some(t=>t!=="correct"))?"down":"neutral");
    const payloadTags=tags.length?tags:(r==="up"?["correct"]:[]);
    setRating(r);setMsg("");
    postJson("/api/llm/flowi/feedback",{
      rating:r,
      prompt:prompt||"",
      answer:result?.answer||"",
      run_id:result?.run_id||"",
      intent:tool?.intent||"",
      note:note||"",
      tags:payloadTags,
      expected_workflow:isAdmin?expectedWorkflow||"":"",
      correct_route:isAdmin?correctRoute||"":"",
      data_refs:isAdmin?dataRefs||"":"",
      golden_candidate:isAdmin&&golden,
      tool:tool||{},
      llm:result?.llm||{},
      elapsed_ms:result?.elapsed_ms||null,
    }).then(d=>setMsg(d?.needs_review?"관리자 검토함에 저장됨":"피드백 저장됨")).catch(e=>setMsg(e.message||"저장 실패"));
  };
  const chip=(key,label)=>{
    const on=tags.includes(key);
    const bad=key!=="correct";
    return <button key={key} type="button" onClick={()=>toggleTag(key)}
      style={{padding:"3px 7px",borderRadius:5,border:"1px solid "+(on?(bad?"#ef4444":"#22c55e"):"#333"),background:on?(bad?"#7f1d1d33":"#14532d33"):"transparent",color:on?(bad?"#fca5a5":"#86efac"):"#a3a3a3",fontSize:14,fontFamily:"monospace",cursor:"pointer",whiteSpace:"nowrap"}}>{label}</button>;
  };
  const feedbackTags=isAdmin?FLOWI_FEEDBACK_TAGS:FLOWI_FEEDBACK_TAGS.filter(([key])=>FLOWI_USER_FEEDBACK_KEYS.has(key));
  return(<div style={{marginTop:8,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"7px 8px"}}>
    <div style={{display:"flex",alignItems:"center",gap:6,flexWrap:"wrap"}}>
      <span style={{fontSize:14,color:"#737373",fontFamily:"monospace",fontWeight:800,whiteSpace:"nowrap"}}>응답 피드백</span>
      <button type="button" onClick={()=>{setTags(["correct"]);send("up");}} style={{padding:"3px 8px",borderRadius:5,border:"1px solid #333",background:rating==="up"?"#22c55e22":"transparent",color:rating==="up"?"#22c55e":"#a3a3a3",fontSize:14,fontFamily:"monospace",cursor:"pointer"}}>정확함</button>
      <button type="button" onClick={()=>{setOpen(true);setRating("down");if(!tags.length)setTags(["output_issue"]);}} style={{padding:"3px 8px",borderRadius:5,border:"1px solid #333",background:rating==="down"?"#ef444422":"transparent",color:rating==="down"?"#fca5a5":"#a3a3a3",fontSize:14,fontFamily:"monospace",cursor:"pointer"}}>개선 필요</button>
      <button type="button" onClick={()=>setOpen(!open)} style={{padding:"3px 8px",borderRadius:5,border:"1px solid #333",background:"transparent",color:"#737373",fontSize:14,fontFamily:"monospace",cursor:"pointer"}}>{open?"접기":"상세"}</button>
      <input value={note} onChange={e=>setNote(e.target.value)} onFocus={()=>setOpen(true)} onKeyDown={e=>{if(e.key==="Enter")send(rating||"neutral");}} placeholder="짧은 개선 의견"
        style={{flex:"1 1 190px",minWidth:170,padding:"4px 7px",borderRadius:5,border:"1px solid #333",background:"#141414",color:"#d4d4d4",fontSize:14,outline:"none"}}/>
      <button type="button" onClick={()=>send(rating||"neutral")} style={{padding:"3px 8px",borderRadius:5,border:"1px solid #333",background:"#171717",color:"#a3a3a3",fontSize:14,fontFamily:"monospace",cursor:"pointer"}}>저장</button>
      {msg&&<span style={{fontSize:14,color:msg.includes("실패")?"#fca5a5":"#22c55e",fontFamily:"monospace"}}>{msg}</span>}
    </div>
    {open&&<div style={{marginTop:8,display:"grid",gap:7}}>
      <div style={{display:"flex",gap:5,flexWrap:"wrap"}}>{feedbackTags.map(([k,l])=>chip(k,l))}</div>
      {isAdmin&&<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:7}}>
        <input value={expectedWorkflow} onChange={e=>setExpectedWorkflow(e.target.value)} placeholder="기대 동작/사용해야 할 tool"
          style={{padding:"6px 8px",borderRadius:5,border:"1px solid #333",background:"#141414",color:"#d4d4d4",fontSize:14,outline:"none"}}/>
        <input value={dataRefs} onChange={e=>setDataRefs(e.target.value)} placeholder="정답 DB/컬럼/join key"
          style={{padding:"6px 8px",borderRadius:5,border:"1px solid #333",background:"#141414",color:"#d4d4d4",fontSize:14,outline:"none"}}/>
      </div>}
      {isAdmin&&<textarea value={correctRoute} onChange={e=>setCorrectRoute(e.target.value)} placeholder="정답 경로 또는 기대 결과를 적어주세요"
        rows={2} style={{width:"100%",boxSizing:"border-box",padding:"7px 8px",borderRadius:5,border:"1px solid #333",background:"#141414",color:"#d4d4d4",fontSize:14,lineHeight:1.45,outline:"none",resize:"vertical"}}/>
      }
      {isAdmin&&<label style={{display:"flex",alignItems:"center",gap:6,fontSize:14,color:"#a3a3a3"}}>
        <input type="checkbox" checked={golden} onChange={e=>setGolden(e.target.checked)} style={{accentColor:"#f97316"}}/>
        좋은 답변 기준 후보로 관리자 검토함에 올리기
      </label>}
    </div>}
  </div>);
}

function FlowiDataTable({table}){
  const cols=flowiTableColumns(table);
  const rows=Array.isArray(table.rows)?table.rows:[];
  const maxHeight=Number(table.max_height||table.maxHeight||320);
  const cellStyle=(row,c)=>{
    const key=String(c.key||"");
    const isSplit=/^(KNOB|MASK|FAB)_/i.test(key)||["parameter","actual","plan","status"].includes(key);
    const highlighted=!!(row.__highlight||row._highlight||row.highlight||table.highlight);
    return {
      padding:"6px 8px",
      borderBottom:"1px solid #262626",
      color:isSplit?"#e5e5e5":"#c7c7c7",
      whiteSpace:"nowrap",
      fontWeight:isSplit?800:500,
      background:highlighted?"rgba(127,29,29,0.18)":"transparent",
      boxShadow:highlighted&&["actual","plan","status"].includes(key)?"inset 0 0 0 2px rgba(239,68,68,0.85)":"none",
    };
  };
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,overflow:"hidden",background:"#121212"}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:8,padding:"8px 10px",borderBottom:"1px solid #2a2a2a",background:"#171717"}}>
      <div style={{fontSize:14,fontWeight:800,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace"}}>{table.title||"Flowi table"}</div>
      <div style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{rows.length}{table.total&&table.total!==rows.length?` / ${table.total}`:""} rows</div>
    </div>
    <div style={{overflow:"auto",maxHeight}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace"}}>
        <thead><tr>{cols.map(c=><th key={c.key} style={{position:"sticky",top:0,zIndex:1,textAlign:"left",padding:"7px 8px",borderBottom:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",whiteSpace:"nowrap"}}>{c.label||c.key}</th>)}</tr></thead>
        <tbody>{rows.map((r,i)=><tr key={i}>
          {cols.map(c=><td key={c.key} style={cellStyle(r,c)}>{r[c.key]??""}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
  </div>);
}

export default function My_Home({onNavigate,user}){
  const nav=(k)=>onNavigate&&onNavigate(k);
  const isAdmin=isAdminUser(user);
  const userTabs=isAdmin?"__all__":(user?.tabs||"");
  const[flowiActive,setFlowiActive]=useState(false);
  const hasTab=(k)=>userTabs==="__all__"||userTabs.split(",").map(s=>s.trim()).filter(Boolean).includes(k);

  // v8.7.4: TABS 순서와 동일하게 카드 정렬. 홈 카드에 inform/meeting/calendar 포함.
  // v8.8.5: 카드별 tag(개별 버전) 제거 — 통합 버전(v8.8.5) 만 의미 있음.
  const ALL_CARDS=[
    {key:"filebrowser",icon:"📂",title:"파일 탐색기",desc:"Parquet 탐색, SQL 필터, CSV 다운로드"},
    {key:"dashboard",  icon:"📊",title:"대시보드",desc:"동적 차트, 산점도, 추세"},
    {key:"splittable", icon:"🗂️",title:"스플릿 테이블",desc:"Plan vs actual, 공유 추적"},
    {key:"diagnosis",  icon:"🤖",title:"에이전트 설정",desc:"LLM 연결 상태와 관리자 설정"},
    {key:"tracker",    icon:"📋",title:"이슈 추적",desc:"이슈 게시판, Lot/Wafer 추적"},
    {key:"inform",     icon:"📢",title:"인폼 로그",desc:"모듈 인폼 + 스레드 + 이미지"},
    {key:"meeting",    icon:"🗓",title:"회의관리",desc:"차수·반복·아젠다·회의록"},
    {key:"calendar",   icon:"📅",title:"변경점 관리",desc:"달력·카테고리·회의 연동"},
    {key:"admin",      icon:"⚙️",title:"관리자",desc:"사용자, 권한, 모니터",adminOnly:true},
    {key:"devguide",   icon:"📖",title:"개발자 가이드",desc:"아키텍처, API 레퍼런스"},
  ];
  const visibleCards=ALL_CARDS.filter(c=>{
    const delegated=c.key!=="admin"&&isPageAdmin(user,c.key);
    return (!c.adminOnly||isAdmin||delegated)&&(hasTab(c.key)||delegated);
  });

  return(<div style={{minHeight:"calc(100vh - 52px)",width:"100%",boxSizing:"border-box",padding:flowiActive?"20px 12px 96px":"32px 32px 96px",background:"var(--bg-primary,#1a1a1a)",color:"var(--text-primary,#e5e5e5)",fontFamily:"'Pretendard',sans-serif",maxWidth:flowiActive?"min(1760px, calc(100vw - 24px))":1040,margin:"0 auto",transition:"max-width .24s ease,padding .24s ease"}}>
    {/* v8.3.3: Home brand logo — shared BrandLogo.jsx, size="home" retains .home-brand-logo marker. */}
    <BrandLogo size="home"/>
    {/* Terminal header */}
    <div style={{background:"#111",borderRadius:12,border:"1px solid #333",overflow:"hidden",marginBottom:28,boxShadow:"0 2px 20px rgba(0,0,0,0.4)"}}>
      <div style={{display:"flex",alignItems:"center",gap:8,padding:"8px 14px",background:"#1a1a1a",borderBottom:"1px solid #333"}}>
        <div style={{display:"flex",gap:6}}><div style={{width:10,height:10,borderRadius:"50%",background:"#ef4444"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#fbbf24"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#22c55e"}}/></div>
        <span style={{fontSize:14,color:"#525252",fontFamily:"monospace",marginLeft:6}}>flow-i console</span>
      </div>
      <div style={{display:"flex",gap:flowiActive?16:20,padding:flowiActive?"16px 18px":"20px 24px",alignItems:"flex-start"}}>
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:4,flexShrink:0}}><Holli size={flowiActive?60:72}/><span style={{fontSize:14,color:"#f97316",fontFamily:"monospace",letterSpacing:"0.12em",fontWeight:700}}>flow-i</span></div>
        <div style={{flex:"1 1 auto",minWidth:0,paddingTop:4}}>
          <div style={{marginTop:6,fontFamily:"'JetBrains Mono',monospace",fontSize:14}}><span style={{color:"#f97316"}}>{">"}</span><span style={{color:"#737373"}}> </span><WelcomeType name={user?.username||"user"}/></div>
          <FlowiConsole onNavigate={nav} user={user} onActiveChange={setFlowiActive}/>
        </div>
      </div>
    </div>

    {/* Permission-filtered cards, centered */}
    {visibleCards.length>0?<div style={{display:"grid",gridTemplateColumns:"repeat(4, minmax(0, 1fr))",gap:14,justifyContent:"start",marginBottom:32}}>
      {visibleCards.map(c=><Card key={c.key} icon={c.icon} title={c.title} desc={c.desc} tag={c.tag} onClick={()=>nav(c.key)} width="100%"/>)}
    </div>:<div style={{padding:"40px 20px",textAlign:"center",color:"var(--text-secondary)",fontSize:14,marginBottom:32}}>
      사용 가능한 탭이 없습니다. 관리자에게 권한을 요청해주세요.
    </div>}

    <div style={{background:"var(--bg-secondary,#262626)",borderRadius:12,border:"1px solid var(--border,#333)",overflow:"hidden"}}>
      <PageHeader title="사용 방법" subtitle="권한있는 기능 가이드" style={{fontFamily:"'JetBrains Mono',monospace"}} />
      <div style={{padding:"6px 20px 16px"}}>
        {visibleCards.filter(c=>FEATURE_GUIDES[c.key]).map((c,i,arr)=>{const g=FEATURE_GUIDES[c.key];return(<div key={c.key} style={{paddingTop:16,paddingBottom:12,borderBottom:i<arr.length-1?"1px solid var(--border,#333)":"none"}}>
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:10,cursor:"pointer"}} onClick={()=>nav(c.key)}>
            <span style={{fontSize:24}}>{g.icon}</span>
            <span style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",fontFamily:"'JetBrains Mono',monospace"}}>{g.title}</span>
            <span style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace",marginLeft:"auto"}}>→ 열기</span>
          </div>
          <ol style={{margin:0,paddingLeft:28,fontSize:14,lineHeight:1.8,color:"var(--text-secondary)"}}>
            {g.steps.map((s,si)=><li key={si} style={{marginBottom:2}}>{s}</li>)}
          </ol>
        </div>);})}
        {visibleCards.filter(c=>FEATURE_GUIDES[c.key]).length===0&&<div style={{padding:"20px 0",textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>권한있는 기능이 없습니다. 아래 관리자 문의 버튼으로 문의해주세요.</div>}
      </div>
    </div>

    {/* v8.3.1: Contact 섹션 — 메시지 탭/팝업 대체.
         v8.4.5: Contact 는 우상단 ✉ 버튼(ContactButton)으로 이관 — 홈 하단 섹션 제거. */}
  </div>);
}

// ─── Contact section (replaces nav Messages tab + unread popup) ────────────────
function fmtT(iso){if(!iso)return"";try{const d=new Date(iso);const mm=String(d.getMonth()+1).padStart(2,"0");const dd=String(d.getDate()).padStart(2,"0");const H=String(d.getHours()).padStart(2,"0");const M=String(d.getMinutes()).padStart(2,"0");return `${mm}-${dd} ${H}:${M}`;}catch{return(iso||"").slice(0,16).replace("T"," ");}}
const SEC_WRAP={marginTop:40,background:"var(--bg-secondary,#262626)",borderRadius:12,border:"1px solid var(--border,#333)",overflow:"hidden"};
const SEC_HEADER={padding:"14px 20px",borderBottom:"1px solid var(--border,#333)",display:"flex",justifyContent:"space-between",alignItems:"center"};
const SEC_TITLE={fontSize:14,fontWeight:700,fontFamily:"'JetBrains Mono',monospace",color:"var(--accent,#f97316)"};

function ContactSection({user}){
  const isAdmin=user?.role==="admin";
  return(<section data-testid="home-contact-section" id="home-contact-section" style={SEC_WRAP}>
    <div style={SEC_HEADER}>
      <span style={SEC_TITLE}>{"> contact"}</span>
      <span style={{fontSize:14,color:"var(--text-secondary)"}}>{isAdmin?"관리자 — 1:1 문의함 + 전체 공지":"관리자에게 문의 보내기"}</span>
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
    sf("/api/messages/thread?username="+encodeURIComponent(uname))
      .then(d=>{setThread(d||{messages:[]});postJson("/api/messages/mark_read",{username:uname}).catch(()=>{});})
      .catch(()=>{});
    sf("/api/messages/notices?username="+encodeURIComponent(uname))
      .then(d=>setNotices(d.notices||[])).catch(()=>{});
  };
  useEffect(()=>{if(uname)load();},[uname]);
  const send=()=>{
    const v=(text||"").trim();if(!v||sending)return;
    if(v.length>5000){toast.warn("최대 5000자까지 입력 가능합니다.");return;}
    setSending(true);
    postJson("/api/messages/send",{username:uname,text:v})
      .then(()=>{setText("");load();}).catch(e=>toast.error("전송 실패: "+(e.message||e))).finally(()=>setSending(false));
  };
  const markNoticeRead=(id)=>{
    postJson("/api/messages/notice_read",{username:uname,ids:[id]})
      .then(()=>setNotices(p=>p.map(x=>x.id===id?{...x,read:true}:x))).catch(()=>{});
  };
  const msgs=thread.messages||[];
  const unreadNotices=notices.filter(n=>!n.read);
  return(<div data-testid="contact-user" style={{padding:"16px 20px"}}>
    {/* 최신 공지 pinned to top */}
    {unreadNotices.length>0&&<div style={{marginBottom:16}}>
      <div style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace",marginBottom:6,fontWeight:700}}>📢 새 공지사항 ({unreadNotices.length})</div>
      {unreadNotices.slice(0,3).map(n=>(
        <div key={n.id} onClick={()=>markNoticeRead(n.id)} style={{padding:"10px 12px",borderRadius:6,background:"var(--accent-glow,rgba(249,115,22,0.1))",border:"1px solid var(--border)",marginBottom:6,cursor:"pointer"}}>
          <div style={{fontSize:14,fontWeight:700,color:"var(--text-primary)"}}>{n.title||"(제목 없음)"}</div>
          {n.body&&<div style={{fontSize:14,color:"var(--text-secondary)",marginTop:3,whiteSpace:"pre-wrap",lineHeight:1.5}}>{n.body}</div>}
          <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:4}}>{n.author} · {fmtT(n.created_at)}</div>
        </div>))}
    </div>}

    {/* Send-to-admin input */}
    <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:6,fontFamily:"monospace"}}>💬 관리자에게 문의</div>
    <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
      <textarea data-testid="contact-user-input" value={text} onChange={e=>setText(e.target.value)} disabled={sending}
        onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")send();}}
        placeholder="버그 리포트 / 기능 요청 / 권한 요청 등 (Cmd/Ctrl + Enter 전송)" rows={3}
        style={{flex:1,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
      <button data-testid="contact-user-send" onClick={send} disabled={sending||!text.trim()}
        style={{padding:"8px 18px",borderRadius:6,border:"none",background:sending||!text.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:sending||!text.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>
        {sending?"…":"보내기"}
      </button>
    </div>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4,textAlign:"right"}}>{text.length} / 5000</div>

    {/* Collapsible history */}
    <div style={{marginTop:18,borderTop:"1px solid var(--border)",paddingTop:10}}>
      <div onClick={()=>setShowHistory(!showHistory)} style={{cursor:"pointer",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",display:"flex",alignItems:"center",gap:6}}>
        <span>{showHistory?"▼":"▶"}</span><span>과거 대화 ({msgs.length})</span>
      </div>
      {showHistory&&<div data-testid="contact-user-history" style={{marginTop:10,maxHeight:300,overflowY:"auto",padding:"4px 2px"}}>
        {msgs.length===0&&<div style={{textAlign:"center",color:"var(--text-secondary)",fontSize:14,padding:20}}>아직 대화가 없습니다.</div>}
        {msgs.map(m=>{const mine=m.from===uname;return(
          <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:8}}>
            <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
              <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?"나":m.from} · {fmtT(m.created_at)}</div>
              <div style={{padding:"6px 10px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:14,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
            </div>
          </div>);})}
      </div>}
    </div>
  </div>);
}

// ── Admin side: two tabs only — [📨 1:1 문의함] [📢 전체 공지].
function AdminContact({user}){
  const[sub,setSub]=useState("inbox");
  const tS=(a)=>({padding:"7px 14px",fontSize:14,cursor:"pointer",fontWeight:a?700:500,borderRadius:5,background:a?"var(--accent-glow)":"transparent",color:a?"var(--accent)":"var(--text-secondary)",fontFamily:"'JetBrains Mono',monospace"});
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
  const loadThreads=()=>sf("/api/messages/admin/threads?admin="+encodeURIComponent(admin)).then(d=>setThreads(d.threads||[])).catch(()=>{});
  const loadThread=(u)=>sf("/api/messages/admin/thread?admin="+encodeURIComponent(admin)+"&user="+encodeURIComponent(u)).then(setThr).catch(()=>{});
  useEffect(()=>{if(admin)loadThreads();},[admin]);
  useEffect(()=>{if(sel)loadThread(sel);else setThr(null);},[sel]);
  const open=(u)=>{setSel(u);postJson("/api/messages/admin/mark_read",{admin,to_user:u}).then(loadThreads).catch(()=>{});};
  const send=()=>{const v=(reply||"").trim();if(!v||!sel||sending)return;if(v.length>5000){toast.warn("최대 5000자");return;}setSending(true);
    postJson("/api/messages/admin/reply",{admin,to_user:sel,text:v})
      .then(()=>{setReply("");loadThread(sel);loadThreads();}).catch(e=>toast.error("실패: "+(e.message||e))).finally(()=>setSending(false));};
  const totalUnread=threads.reduce((s,t)=>s+(t.unread_for_admin||0),0);
  return(<div style={{display:"flex",gap:12,minHeight:340}}>
    <div style={{width:240,background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",display:"flex",flexDirection:"column",flexShrink:0}}>
      <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:6}}>
        <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>스레드</span>
        <span style={{fontSize:14,color:"var(--text-secondary)"}}>{threads.length}·미확인 {totalUnread}</span>
        <div style={{flex:1}}/>
        <span onClick={loadThreads} style={{fontSize:14,cursor:"pointer",color:"var(--text-secondary)"}} title="새로고침">↻</span>
      </div>
      <div style={{flex:1,overflowY:"auto",maxHeight:340}}>
        {threads.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>수신 없음</div>}
        {threads.map(t=>(
          <div key={t.user} onClick={()=>open(t.user)} style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",cursor:"pointer",background:sel===t.user?"var(--accent-glow)":(t.unread_for_admin>0?"rgba(249,115,22,0.05)":"transparent")}}>
            <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:2}}>
              {t.unread_for_admin>0&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--accent)",flexShrink:0}}/>}
              <span style={{fontSize:14,fontWeight:t.unread_for_admin>0?700:500,color:"var(--text-primary)",fontFamily:"monospace",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.user}</span>
              {t.unread_for_admin>0&&<span style={{fontSize:14,fontWeight:700,padding:"1px 5px",borderRadius:3,background:"var(--accent)",color:"#fff"}}>{t.unread_for_admin}</span>}
            </div>
            <div style={{fontSize:14,color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",lineHeight:1.4}}>{t.last_from?`[${t.last_from}] `:""}{t.last_preview||"(비어 있음)"}</div>
          </div>))}
      </div>
    </div>
    <div style={{flex:1,background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",display:"flex",flexDirection:"column",minWidth:0,minHeight:340}}>
      {!sel&&<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-secondary)",fontSize:14,padding:20}}>← 스레드를 선택하세요</div>}
      {sel&&thr&&<>
        <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>💬 {sel}</span>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>{(thr.messages||[]).length} 메시지</span>
        </div>
        <div style={{flex:1,overflowY:"auto",padding:12,maxHeight:280}}>
          {(thr.messages||[]).map(m=>{const mine=m.from===admin;return(
            <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:8}}>
              <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
                <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?`나 (${m.from})`:m.from} · {fmtT(m.created_at)}</div>
                <div style={{padding:"6px 10px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:14,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
              </div>
            </div>);})}
        </div>
        <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)"}}>
          <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
            <textarea value={reply} onChange={e=>setReply(e.target.value)} disabled={sending}
              onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")send();}}
              placeholder={`${sel} 에게 답장 (Cmd/Ctrl+Enter 전송)`} rows={2}
              style={{flex:1,padding:"7px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:"var(--text-primary)",fontSize:14,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
            <button onClick={send} disabled={sending||!reply.trim()}
              style={{padding:"7px 16px",borderRadius:6,border:"none",background:sending||!reply.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:sending||!reply.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>{sending?"…":"답장"}</button>
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
  const loadNotices=()=>sf("/api/messages/admin/notices?admin="+encodeURIComponent(admin)).then(d=>setNotices(d.notices||[])).catch(()=>{});
  useEffect(()=>{if(admin){loadNotices();}},[admin]);
  const publish=()=>{
    const t=title.trim(),b=body.trim();if(!t&&!b){toast.warn("제목 또는 본문을 입력하세요.");return;}
    if(sending)return;setSending(true);
    postJson("/api/messages/admin/notice_create",{author:admin,title:t,body:b})
      .then(()=>{setTitle("");setBody("");loadNotices();toast.ok("전체 공지가 발행되었습니다.");})
      .catch(e=>toast.error("실패: "+(e.message||e))).finally(()=>setSending(false));
  };
  const del=(id)=>{if(!confirm("공지사항을 삭제하시겠습니까?"))return;
    postJson("/api/messages/admin/notice_delete",{admin,id}).then(loadNotices).catch(e=>toast.error(e.message));};
  const S={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",fontFamily:"'Pretendard',sans-serif",boxSizing:"border-box"};
  return(<div>
    <div style={{background:"var(--bg-primary)",border:"1px solid var(--accent)",borderRadius:8,padding:14,marginBottom:14}}>
      <div data-testid="contact-admin-mode-all" style={{display:"flex",alignItems:"center",gap:6,fontSize:14,marginBottom:10,color:"var(--accent)",fontFamily:"'JetBrains Mono',monospace",fontWeight:700}}>
        📢 전체 공지 작성 — 모든 사용자에게 발행
      </div>
      <input data-testid="contact-admin-notice-title" value={title} onChange={e=>setTitle(e.target.value)} placeholder="제목 (최대 200자)" maxLength={200} style={{...S,marginBottom:8,fontWeight:600}}/>
      <textarea data-testid="contact-admin-notice-body" value={body} onChange={e=>setBody(e.target.value)} placeholder="공지 본문 (최대 5000자)" rows={4} style={{...S,marginBottom:8,resize:"vertical"}}/>
      <div style={{display:"flex",alignItems:"center"}}>
        <span style={{fontSize:14,color:"var(--text-secondary)"}}>{title.length}/200 · {body.length}/5000</span>
        <div style={{flex:1}}/>
        <button data-testid="contact-admin-notice-publish" onClick={publish} disabled={sending||(!title.trim()&&!body.trim())}
          style={{padding:"7px 18px",borderRadius:5,border:"none",background:sending||(!title.trim()&&!body.trim())?"#94a3b8":"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:sending?"default":"pointer"}}>
          {sending?"…":"전체 발행"}
        </button>
      </div>
    </div>

    {/* 기존 공지 리스트 */}
    <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:6}}>기존 공지사항 ({notices.length})</div>
    <div style={{background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",maxHeight:320,overflowY:"auto"}}>
      {notices.length===0&&<div style={{padding:24,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>등록된 공지사항이 없습니다.</div>}
      {notices.map(n=>(
        <div key={n.id} style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:3}}>
            <span style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",flex:1}}>{n.title||"(제목 없음)"}</span>
            <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{fmtT(n.created_at)}</span>
            <span style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace"}}>👁 {n.read_count||0}/{n.total_recipients||"?"}</span>
            <span onClick={()=>del(n.id)} style={{cursor:"pointer",color:"#ef4444",fontSize:14}}>🗑</span>
          </div>
          {n.body&&<div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5,whiteSpace:"pre-wrap"}}>{n.body}</div>}
          <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:3}}>by {n.author}</div>
        </div>))}
    </div>
  </div>);
}
