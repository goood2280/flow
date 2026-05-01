import { useState, useEffect, useRef } from "react";
import BrandLogo from "../components/BrandLogo";
import { postJson } from "../lib/api";
const B="#ea580c",M="#f97316",L="#fb923c",D="#9a3412",BK="#171717",W="#fff7ed",PK="#fda4af",G="#fbbf24";

// v8.3.3: PF_HOME / PixelGlyph / HomeBrandLogo extracted to shared ../components/BrandLogo.jsx.
// Home uses <BrandLogo size="home"/>; nav uses <BrandLogo size="nav"/> (see App.jsx).


const BASE_PX=[[2,5,B],[2,6,B],[2,7,B],[2,8,B],[2,9,B],[2,10,B],[3,4,B],[3,5,M],[3,6,M],[3,7,M],[3,8,M],[3,9,M],[3,10,M],[3,11,B],[4,3,B],[4,4,M],[4,5,L],[4,6,L],[4,7,L],[4,8,L],[4,9,L],[4,10,L],[4,11,M],[4,12,B],[5,3,B],[5,4,M],[5,5,L],[5,6,L],[5,7,L],[5,8,L],[5,9,L],[5,10,L],[5,11,M],[5,12,B],[8,3,B],[8,4,PK],[8,5,L],[8,6,L],[8,7,L],[8,8,L],[8,9,L],[8,10,L],[8,11,PK],[8,12,B],[9,3,B],[9,4,M],[9,5,L],[9,6,L],[9,7,BK],[9,8,BK],[9,9,L],[9,10,L],[9,11,M],[9,12,B],[10,3,B],[10,4,M],[10,5,M],[10,6,M],[10,7,M],[10,8,M],[10,9,M],[10,10,M],[10,11,M],[10,12,B],[11,4,B],[11,5,B],[11,6,B],[11,7,B],[11,8,B],[11,9,B],[11,10,B],[11,11,B],[12,5,B],[12,6,B],[12,9,B],[12,10,B],[13,5,D],[13,6,D],[13,9,D],[13,10,D],[0,7,G],[1,7,G],[0,8,G],[1,8,G]];
const EO=[[6,3,B],[6,4,M],[6,5,W],[6,6,BK],[6,7,L],[6,8,L],[6,9,W],[6,10,BK],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,W],[7,6,BK],[7,7,L],[7,8,L],[7,9,W],[7,10,BK],[7,11,M],[7,12,B]];
const EC=[[6,3,B],[6,4,M],[6,5,L],[6,6,L],[6,7,L],[6,8,L],[6,9,L],[6,10,L],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,BK],[7,6,BK],[7,7,L],[7,8,L],[7,9,BK],[7,10,BK],[7,11,M],[7,12,B]];
const AD=[[7,1,M],[7,2,M],[8,1,B],[7,13,M],[7,14,M],[8,14,B]];
const AW=[[7,1,M],[7,2,M],[8,1,B],[5,13,M],[5,14,G],[6,13,M],[6,14,B]];
function Holli({size=72}){const[fr,setFr]=useState("idle");const t=useRef(null);useEffect(()=>{const loop=()=>{t.current=setTimeout(()=>{if(Math.random()<0.6){setFr("blink");setTimeout(()=>{setFr("idle");loop();},150);}else{setFr("wave");setTimeout(()=>{setFr("idle");loop();},600);}},1500+Math.random()*2500);};loop();return()=>clearTimeout(t.current);},[]);const px=[...BASE_PX,...(fr==="blink"?EC:EO),...(fr==="wave"?AW:AD)];return(<div style={{animation:fr==="idle"?"holBob 2s ease-in-out infinite":"none"}}><style>{`@keyframes holBob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}@keyframes holBlink{0%,100%{opacity:1}50%{opacity:0}}`}</style><svg width={size} height={size} viewBox="0 0 16 16" style={{imageRendering:"pixelated"}}>{px.map(([r,c,color],i)=><rect key={i} x={c} y={r} width={1} height={1} fill={color}/>)}</svg></div>);}
function Cli({cmd,output,delay=0}){const line=`> flow ${cmd}`;const parts=[{text:">",color:"#f97316"},{text:" flow ",color:"#737373"},{text:cmd,color:"#e5e5e5"}];const[show,setShow]=useState(delay===0);const[typedLen,setTypedLen]=useState(0);const[done,setDone]=useState(false);useEffect(()=>{if(delay){const t=setTimeout(()=>setShow(true),delay);return()=>clearTimeout(t);}},[delay]);useEffect(()=>{if(!show)return;setTypedLen(0);setDone(false);let i=0;const iv=setInterval(()=>{i++;setTypedLen(i);if(i>=line.length){clearInterval(iv);setTimeout(()=>setDone(true),100);}},30);return()=>clearInterval(iv);},[show,line]);if(!show)return null;let remain=typedLen;return(<div style={{marginBottom:4,fontFamily:"'JetBrains Mono',monospace",fontSize:14,lineHeight:1.7}}>{parts.map((p,idx)=>{const s=p.text.slice(0,Math.max(0,Math.min(p.text.length,remain)));remain-=s.length;return s?<span key={idx} style={{color:p.color}}>{s}</span>:null;})}{!done&&<span style={{display:"inline-block",width:8,height:14,background:"#f97316",marginLeft:2,animation:"holBlink 0.6s step-end infinite"}}/>}{done&&output&&<div style={{color:"#a3a3a3",paddingLeft:20,fontSize:14}}>{output}</div>}</div>);}
function WelcomeType({name}){const full=`${name}님, 안녕하세요`;const[len,setLen]=useState(0);useEffect(()=>{const t=setTimeout(()=>{let i=0;const iv=setInterval(()=>{i++;setLen(i);if(i>=full.length)clearInterval(iv);},70);return()=>clearInterval(iv);},800);return()=>clearTimeout(t);},[full]);return(<span><span style={{color:"#e5e5e5",fontWeight:700}}>{full.slice(0,len)}</span></span>);}
function Card({icon,title,desc,tag,onClick,width=220}){return(<div onClick={onClick} onMouseEnter={e=>{e.currentTarget.style.borderColor="#f97316";e.currentTarget.style.background="#f9731610";}} onMouseLeave={e=>{e.currentTarget.style.borderColor="var(--border,#333)";e.currentTarget.style.background="var(--bg-card,#2a2a2a)";}} style={{background:"var(--bg-card,#2a2a2a)",borderRadius:12,padding:"20px 24px",cursor:onClick?"pointer":"default",border:"1px solid var(--border,#333)",transition:"all 0.2s",position:"relative",width,boxSizing:"border-box"}}>{tag&&<span style={{position:"absolute",top:12,right:12,fontSize:14,fontWeight:700,padding:"2px 6px",borderRadius:3,background:"#f9731622",color:"#f97316",fontFamily:"monospace",textTransform:"uppercase"}}>{tag}</span>}<div style={{fontSize:28,marginBottom:10}}>{icon}</div><div style={{fontSize:14,fontWeight:700,color:"var(--text-primary,#e5e5e5)",marginBottom:6,fontFamily:"'JetBrains Mono',monospace"}}>{title}</div><div style={{fontSize:14,color:"var(--text-secondary,#a3a3a3)",lineHeight:1.6}}>{desc}</div></div>);}

// Feature guide content shown to users (non-admin) instead of changelog
const FEATURE_GUIDES={
  filebrowser:{icon:"📂",title:"파일 브라우저",steps:["좌측 사이드바에서 DB 선택","하위 Product/파일 선택 시 데이터 자동 로드","SQL 입력창에 필터 입력 (예: PRODUCT_TYPE == 'A', LOT_ID LIKE '%ABC%')","컬럼 선택 → CSV 다운로드 버튼"]},
  dashboard:{icon:"📊",title:"대시보드",steps:["데이터 소스 선택 (DB / Root Parquet / Product)","차트 타입: scatter / line / bar / pie / binning","X/Y 컬럼 선택 + 필터 SQL 입력","Days 옵션으로 기간 제한, binning 은 bin_count/bin_width 조정"]},
  splittable:{icon:"🗂️",title:"스플릿 테이블",steps:["Product 선택 → Root Lot + Wafer IDs 입력 → 검색","Plan 입력 모드: 편집 클릭 후 셀 클릭하여 계획값 입력","셀 색: 회색(없음) / 주황(plan만) / 파스텔(actual) / 초록(match) / 빨강(mismatch)","이력 탭에서 변경 이력 확인"]},
  diagnosis:{icon:"🤖",title:"에이전트",steps:["Flow-i가 RCA·차트·데이터 확인을 수행할 때 쓰는 참조 지식 확인","RAG 반영 문서 / 표 지식 / TableMap / Source Profile 연결 구조 검토","Admin은 품질 피드백, golden workflow, LLM 설정을 같은 화면에서 관리"]},
  tracker:{icon:"📋",title:"트래커",steps:["이슈 게시판 — 제목 + 본문 + 이미지 업로드","Lot/Wafer 범위 지정 (Excel 붙여넣기 지원)","댓글 + 중첩 답글 + 이미지","Gantt 뷰로 전체 진행 현황 확인"]},
  inform:{icon:"📢",title:"인폼 로그",steps:["제품/lot 선택 후 인폼 등록","SplitTable 스냅샷 자동 첨부 확인","댓글 스레드와 담당자 흐름 추적","필요 시 메일 미리보기 후 발송"]},
  meeting:{icon:"🗓",title:"회의관리",steps:["회의 선택 또는 신규 회의 생성","아젠다/회의록/결정사항 입력","액션아이템과 달력 연동 확인","필요 시 메일로 회의록 공유"]},
  calendar:{icon:"📅",title:"변경점 관리",steps:["월별 변경 일정 확인","카테고리별 이벤트 필터","회의 액션/결정사항 연동 확인","상태(pending/in_progress/done) 관리"]},
  ettime:{icon:"⏱️",title:"ET 레포트",steps:["lot/root_lot_id 기준 조회","fab_lot_id + step별 ET 패키지 확인","상세 breakdown 표 확인","CSV/PDF 리포트 다운로드"]},
  waferlayout:{icon:"🧭",title:"WF Layout",steps:["제품별 wafer layout 불러오기","shot/chip/TEG 배치 확인","edge shot 후보 검토","layout 저장 및 재검증"]},
  tablemap:{icon:"🗺️",title:"테이블 맵",steps:["DB 간 관계 그래프 조회","노드 더블클릭 → 상세 정보","관계선 drag-drop 으로 편집"]},
  devguide:{icon:"📖",title:"개발 가이드",steps:["아키텍처 다이어그램","API 엔드포인트 문서","Gotchas / 코드 규칙"]},
};
function FlowiConsole({onNavigate,user}){
  const isAdmin=user?.role==="admin";
  const[active,setActive]=useState(false);
  const[connState,setConnState]=useState("idle");
  const[prompt,setPrompt]=useState("");
  const[busy,setBusy]=useState(false);
  const[result,setResult]=useState(null);
  const[lastPrompt,setLastPrompt]=useState("");
  const[err,setErr]=useState("");
  const[modelLabel,setModelLabel]=useState("");
  const[messages,setMessages]=useState([]);
  const[liveStep,setLiveStep]=useState(0);
  const[personaCard,setPersonaCard]=useState(null);
  const[personaOpen,setPersonaOpen]=useState(false);
  const[activeChartSessionId,setActiveChartSessionId]=useState("");
  const promptRef=useRef(null);
  const scrollRef=useRef(null);
  const verifySeq=useRef(0);
  const CTX_LIMIT=12000;

  useEffect(()=>{if(active&&promptRef.current)setTimeout(()=>promptRef.current?.focus(),30);},[active]);
  useEffect(()=>{if(active&&scrollRef.current)scrollRef.current.scrollTop=scrollRef.current.scrollHeight;},[active,messages,busy]);
  useEffect(()=>{
    if(!busy){setLiveStep(0);return undefined;}
    setLiveStep(0);
    const iv=setInterval(()=>setLiveStep(v=>Math.min(v+1,FLOWI_LIVE_STEPS.length-1)),850);
    return()=>clearInterval(iv);
  },[busy]);
  useEffect(()=>{
    let alive=true;
    fetch("/api/llm/status").then(r=>r.ok?r.json():null).then(d=>{
      if(!alive)return;
      const cfg=d?.config||{};
      const model=String(cfg.model||"").trim();
      setModelLabel(d?.available&&model?model:"");
      if(d&&!d.available)setConnState("disconnected");
    }).catch(()=>{if(alive)setModelLabel("");});
    return()=>{alive=false;};
  },[]);
  useEffect(()=>{
    if(!active||personaCard)return;
    let alive=true;
    fetch("/api/llm/flowi/persona-card").then(r=>r.ok?r.json():null).then(d=>{
      if(!alive||!d?.ok)return;
      setPersonaCard(d);
      const seen=localStorage.getItem("flowiPersonaCardSeen")==="1";
      setPersonaOpen(!seen);
      if(!seen)localStorage.setItem("flowiPersonaCardSeen","1");
    }).catch(()=>{});
    return()=>{alive=false;};
  },[active,personaCard]);

  const activate=()=>{
    setActive(true);setErr("");
    const seq=++verifySeq.current;
    setConnState("checking");
    postJson("/api/llm/flowi/verify",{token:""})
      .then(d=>{
        if(seq!==verifySeq.current)return;
        const msg=String(d?.message||d?.text||"");
        setConnState(d?.ok&&msg.includes("확인완료")?"connected":"disconnected");
      })
      .catch(()=>{if(seq===verifySeq.current)setConnState("disconnected");});
    return true;
  };
  const close=()=>{setActive(false);setErr("");};
  const contextMessages=messages.slice(-8).map(m=>({
    role:m.role,
    prompt:m.prompt||"",
    text:String(m.answer||m.text||"").slice(0,900),
    intent:m.intent||"",
    feature:m.result?.tool?.feature||"",
    action:m.result?.tool?.action||"",
    blocked:!!m.result?.tool?.blocked,
    created_record:m.result?.tool?.created_record||null,
    missing:m.result?.tool?.missing||[],
    arguments_choices:m.result?.tool?.arguments_choices||{},
    walkthrough:m.result?.tool?.walkthrough||{},
    slots:m.result?.tool?.slots||{},
    filters:m.result?.tool?.filters||{},
    chart_session_id:m.result?.tool?.chart_session_id||m.result?.tool?.chart_result?.chart_session_id||"",
    workflow_state:m.result?.workflow_state||m.result?.tool?.workflow_state||{},
    output_summary:m.result?.workflow_state?.outputs||m.result?.tool?.workflow_state?.outputs||{},
    pending_prompt:m.result?.tool?.pending_prompt||"",
  }));
  const contextText=contextMessages.map(m=>`${m.role}: ${m.prompt||m.text||""} ${m.intent?`(${m.intent})`:""}`).join("\n");
  const contextRemaining=Math.max(0,CTX_LIMIT-String(contextText||"").length-String(prompt||"").length);
  const contextUsed=CTX_LIMIT-contextRemaining;
  const contextPct=Math.max(0,Math.min(100,Math.round(contextRemaining/CTX_LIMIT*100)));
  const ask=(overridePrompt="")=>{
    if(busy)return;
    const q=String(overridePrompt||prompt||"").trim();
    if(!q){setErr("질문을 입력해주세요.");return;}
    if(overridePrompt)setPrompt(q);
    const userMsg={id:`u-${Date.now()}`,role:"user",text:q,ts:new Date().toISOString()};
    const context={type:"home_flowi_chat",limit_chars:CTX_LIMIT,remaining_chars:contextRemaining,messages:contextMessages,chart_session_id:activeChartSessionId||""};
    setMessages(prev=>[...prev,userMsg]);
    setActive(true);setBusy(true);setErr("");setLastPrompt(q);
    const started=Date.now();
    postJson("/api/llm/flowi/chat",{prompt:q,product:"",max_rows:12,context})
      .then(d=>{
        const enriched={...(d||{}),elapsed_ms:Date.now()-started};
        const sid=enriched?.tool?.chart_session_id||enriched?.tool?.chart_result?.chart_session_id||"";
        if(sid)setActiveChartSessionId(sid);
        setResult(enriched);
        setMessages(prev=>[...prev,{id:`a-${Date.now()}`,role:"assistant",answer:enriched?.answer||"",prompt:q,result:enriched,intent:enriched?.tool?.intent||"",ts:new Date().toISOString()}]);
        setPrompt("");
      }).catch(e=>setErr(e.message||String(e))).finally(()=>setBusy(false));
  };
  const connLabel=connState==="checking"?"연결확인중":connState==="connected"?"연결":connState==="disconnected"?"연결끊김":"";
  const connColor=connState==="connected"?"#22c55e":connState==="checking"?"#f97316":"#ef4444";
  return(<section style={{marginTop:12,fontFamily:"'JetBrains Mono',monospace"}}>
    <style>{`@keyframes flowiPanelWake{0%{opacity:0;transform:translateY(-8px) scaleY(.96)}100%{opacity:1;transform:translateY(0) scaleY(1)}}@keyframes flowiConnBlink{0%,100%{opacity:.45}50%{opacity:1}}`}</style>
    <form onSubmit={e=>{e.preventDefault();activate();}} style={{margin:0}}>
      <div style={{display:"flex",alignItems:"center",gap:7,minWidth:0,fontSize:14,lineHeight:1.7,flexWrap:"wrap"}}>
        <span style={{color:"#f97316"}}>{">"}</span>
        <span style={{color:"#737373",whiteSpace:"nowrap"}}>flow-i</span>
        {active&&connLabel&&<span title={modelLabel?`LLM ${modelLabel}`:"LLM 연결 확인"} style={{display:"inline-flex",alignItems:"center",gap:5,color:connColor,border:`1px solid ${connColor}66`,background:`${connColor}14`,borderRadius:999,padding:"1px 8px",fontSize:14,fontFamily:"monospace",fontWeight:800,whiteSpace:"nowrap"}}>
          <span style={{width:6,height:6,borderRadius:"50%",background:connColor,animation:connState==="checking"?"flowiConnBlink .75s ease-in-out infinite":"none"}}/>{connLabel}
        </span>}
        {!active&&<button type="submit" aria-label="start flowi"
          style={{padding:"2px 8px",borderRadius:5,border:"1px solid #333",background:"#171717",color:"#f97316",fontSize:14,fontFamily:"monospace",fontWeight:800,cursor:"pointer"}}>START</button>}
        {active&&<button type="button" onClick={close} aria-label="close flowi"
          style={{padding:"1px 6px",borderRadius:5,border:"1px solid #333",background:"transparent",color:"#737373",fontSize:14,fontFamily:"monospace",cursor:"pointer"}}>CLOSE</button>}
      </div>
    </form>
    {active&&<div style={{marginTop:10,border:"1px solid #2a2a2a",borderRadius:10,background:"#101010",overflow:"hidden",animation:"flowiPanelWake .32s ease-out",transformOrigin:"top"}}>
      {personaCard&&<FlowiPersonaCard card={personaCard} open={personaOpen} onToggle={()=>setPersonaOpen(v=>!v)}/>}
      <div ref={scrollRef} style={{height:messages.length?420:260,maxHeight:"48vh",overflowY:"auto",padding:"12px 14px",borderBottom:"1px solid #262626",scrollBehavior:"smooth"}}>
        {messages.length===0&&!busy&&<div style={{height:"100%",display:"flex",alignItems:"center",justifyContent:"center",color:"#d4d4d4",fontSize:14,fontWeight:800,textAlign:"center"}}>
          오늘 어떤 도움을 드릴까요?
        </div>}
        {messages.map(m=>m.role==="user"
          ?<div key={m.id} style={{display:"flex",justifyContent:"flex-end",margin:"0 0 10px"}}>
            <div style={{maxWidth:"82%",background:"#1f130b",border:"1px solid #7c2d12",borderRadius:"10px 10px 2px 10px",padding:"8px 10px",color:"#f5f5f5",fontSize:14,lineHeight:1.55,whiteSpace:"pre-wrap"}}>{m.text}</div>
          </div>
          :<div key={m.id} style={{margin:"0 0 14px",maxWidth:"92%"}}>
            <div style={{fontSize:14,color:"#737373",fontFamily:"monospace",marginBottom:4}}>flow-i{isAdmin&&m.intent?` · ${m.intent}`:""}</div>
            <FlowiResult busy={false} error="" result={m.result} prompt={m.prompt} onNavigate={onNavigate} onChoice={ask} embedded isAdmin={isAdmin} activeChartSessionId={activeChartSessionId} onUseChartSession={setActiveChartSessionId}/>
          </div>)}
        {busy&&isAdmin&&<FlowiLiveTrace step={liveStep}/>}
      </div>
      <form onSubmit={e=>{e.preventDefault();ask();}} style={{margin:0,padding:"10px 10px 10px 0"}}>
      <div style={{display:"flex",alignItems:"stretch",gap:8,minWidth:0}}>
        <span style={{color:"#f97316"}}>{">"}</span>
        <div style={{position:"relative",flex:1,minWidth:0}}>
          <textarea ref={promptRef} value={prompt} onChange={e=>setPrompt(e.target.value)}
            placeholder=""
            aria-label="Flowi prompt"
            rows={5}
            onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();ask();}}}
            style={{width:"100%",minWidth:0,padding:isAdmin?"10px 12px 48px":"10px 12px",borderRadius:8,border:"1px solid #525252",background:"#3a3a3a",color:"#f5f5f5",fontSize:14,lineHeight:1.55,fontFamily:"'JetBrains Mono',monospace",outline:"none",resize:"vertical",boxSizing:"border-box",display:"block"}}/>
          {isAdmin&&<div title="현재 연결 모델과 남은 대화 context 추정치" style={{position:"absolute",right:10,bottom:8,display:"flex",gap:6,alignItems:"center",justifyContent:"flex-end",maxWidth:"calc(100% - 20px)",pointerEvents:"none",fontFamily:"'JetBrains Mono',monospace"}}>
            <span style={{minWidth:0,maxWidth:260,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontSize:14,lineHeight:1.1,color:modelLabel?"#d4d4d4":"#737373",border:"1px solid #333",background:"#0f0f0f",borderRadius:999,padding:"6px 9px",fontWeight:900}}>
              MODEL {modelLabel||"미연결"}
            </span>
            <span style={{whiteSpace:"nowrap",fontSize:14,lineHeight:1.1,color:contextPct<20?"#fb923c":"#d4d4d4",border:`1px solid ${contextPct<20?"#f9731666":"#333"}`,background:contextPct<20?"#2a1207":"#0f0f0f",borderRadius:999,padding:"6px 9px",fontWeight:900}}>
              CTX {contextUsed.toLocaleString()} / {CTX_LIMIT.toLocaleString()}
            </span>
          </div>}
        </div>
        {busy&&<div aria-live="polite" style={{alignSelf:"center",color:"#f97316",fontSize:14,fontFamily:"monospace",fontWeight:800,whiteSpace:"nowrap"}}>RUNNING</div>}
      </div>
      </form>
    </div>}
    {err&&<FlowiResult busy={false} error={err} result={null} prompt={lastPrompt} onNavigate={onNavigate} onChoice={ask} isAdmin={isAdmin} activeChartSessionId={activeChartSessionId} onUseChartSession={setActiveChartSessionId}/>}
  </section>);
}

function FlowiPersonaCard({card,open,onToggle}){
  const doList=Array.isArray(card?.do_list)?card.do_list:[];
  const dontList=Array.isArray(card?.dont_list)?card.dont_list:[];
  return(<div style={{borderBottom:"1px solid #262626",background:"#121212",padding:"9px 12px"}}>
    <button type="button" onClick={onToggle} style={{width:"100%",display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,border:"0",background:"transparent",color:"#e5e5e5",fontSize:14,fontWeight:900,fontFamily:"'JetBrains Mono',monospace",cursor:"pointer",padding:0,textAlign:"left"}}>
      <span>이 도우미가 도와주는 일 / 하지 않는 일</span>
      <span style={{color:"#f97316"}}>{open?"접기":"펼치기"}</span>
    </button>
    {open&&<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:10,marginTop:9}}>
      <div>
        <div style={{fontSize:14,color:"#f97316",fontWeight:900,marginBottom:5}}>도와주는 일</div>
        <div style={{display:"flex",gap:5,flexWrap:"wrap"}}>
          {doList.map(x=><span key={x} style={{fontSize:14,color:"#d4d4d4",border:"1px solid #333",borderRadius:999,padding:"2px 7px",background:"#171717"}}>{x}</span>)}
        </div>
      </div>
      <div>
        <div style={{fontSize:14,color:"#f97316",fontWeight:900,marginBottom:5}}>하지 않는 일</div>
        <div style={{display:"grid",gap:3}}>
          {dontList.slice(0,5).map((x,i)=><div key={i} style={{fontSize:14,lineHeight:1.45,color:"#a3a3a3"}}>{x}</div>)}
        </div>
      </div>
    </div>}
  </div>);
}

function FlowiResult({busy,error,result,prompt,onNavigate,onChoice,embedded=false,isAdmin=false,activeChartSessionId="",onUseChartSession=null}){
  if(busy)return <div style={{marginTop:embedded?0:10,fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>local tools + llm 처리 중...</div>;
  if(error)return <div style={{marginTop:10,padding:"9px 10px",borderRadius:6,background:"#7f1d1d33",color:"#fca5a5",fontSize:14,border:"1px solid #7f1d1d"}}>{error}</div>;
  if(!result)return null;
  const tool=result.tool||{};
  const table=tool.table&&Array.isArray(tool.table.rows)&&Array.isArray(tool.table.columns)?tool.table:null;
  const rows=Array.isArray(tool.rows)?tool.rows:[];
  const knobs=Array.isArray(tool.knobs)?tool.knobs:[];
  const canNavigate=typeof onNavigate==="function";
  const featureEntries=Array.isArray(tool.feature_entrypoints)?tool.feature_entrypoints.slice(0,3):[];
  const choices=Array.isArray(tool?.clarification?.choices)?tool.clarification.choices.slice(0,3):[];
  const argumentChoices=tool.arguments_choices||result.arguments_choices||{};
  const walkthrough=tool.walkthrough||{};
  const workflow=tool.workflow_state||result.workflow_state||{};
  const nextActions=(Array.isArray(tool.next_actions)?tool.next_actions:(Array.isArray(result.next_actions)?result.next_actions:[])).filter(a=>a&&a.type!=="respond_with_prompt").slice(0,6);
  const chart=tool?.chart&&typeof tool.chart==="object"?tool.chart:null;
  const chartResult=tool?.chart_result&&typeof tool.chart_result==="object"?tool.chart_result:null;
  const chartSessionId=tool?.chart_session_id||chartResult?.chart_session_id||"";
  return(<div style={{marginTop:embedded?0:12,borderTop:embedded?"none":"1px solid #262626",paddingTop:embedded?0:10}}>
    <div style={{whiteSpace:"pre-wrap",fontSize:14,lineHeight:1.65,color:"#d4d4d4"}}>{result.answer||"응답이 없습니다."}</div>
    {isAdmin&&<div style={{display:"flex",gap:6,marginTop:8,flexWrap:"wrap"}}>
      {tool.intent&&<span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{tool.intent}</span>}
      {workflow.status&&<span style={{fontSize:14,color:workflow.status.startsWith("awaiting")?"#f97316":workflow.status==="blocked"?"#ef4444":"#22c55e",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{workflow.status}</span>}
      {result.llm&&<span style={{fontSize:14,color:result.llm.used?"#22c55e":"#737373",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{result.llm.used?"llm used":"local result"}</span>}
      {featureEntries.map(ep=>canNavigate?<button key={ep.key} type="button" onClick={()=>onNavigate(ep.key)} title={ep.description||""} style={{fontSize:14,color:"#f97316",fontFamily:"monospace",border:"1px solid #7c2d12",borderRadius:999,padding:"2px 8px",background:"#1f130b",cursor:"pointer"}}>{ep.title} 열기</button>:<span key={ep.key} title={ep.description||""} style={{fontSize:14,color:"#f97316",fontFamily:"monospace",border:"1px solid #7c2d12",borderRadius:999,padding:"2px 7px"}}>{ep.title}</span>)}
    </div>}
    {isAdmin&&<FlowiTrace trace={result.trace}/>}
    {chartResult&&<FlowiScatterResult data={chartResult}/>}
    {chartSessionId&&<div style={{marginTop:8,display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}>
      <button type="button" onClick={()=>onUseChartSession&&onUseChartSession(chartSessionId)}
        style={{fontSize:14,color:"#f97316",fontFamily:"monospace",border:"1px solid #7c2d12",borderRadius:999,padding:"3px 9px",background:activeChartSessionId===chartSessionId?"#2a1608":"#1f130b",cursor:"pointer",fontWeight:900}}>
        수정 요청
      </button>
      <span style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{String(chartSessionId).slice(0,12)}</span>
    </div>}
    {chart&&<FlowiChartPlan chart={chart}/>}
    {walkthrough&&walkthrough.session_id&&<FlowiWalkthrough data={walkthrough}/>}
    {table&&<FlowiDataTable table={table}/>}
    {!table&&rows.length>0&&<div style={{marginTop:10,overflowX:"auto"}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace"}}>
        <thead><tr>{["product","step","item","wf","median","mean","n"].map(h=><th key={h} style={{textAlign:"left",padding:"5px 6px",borderBottom:"1px solid #333",color:"#a3a3a3"}}>{h}</th>)}</tr></thead>
        <tbody>{rows.slice(0,12).map((r,i)=><tr key={i}>
          <td style={FR_TD}>{r.product||""}</td><td style={FR_TD}>{r.step_id||""}</td><td style={FR_TD}>{r.item_id||""}</td><td style={FR_TD}>{r.wafer_id||""}</td>
          <td style={FR_TD}>{r.median??""}</td><td style={FR_TD}>{r.mean??""}</td><td style={FR_TD}>{r.count??""}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {knobs.length>0&&<div style={{marginTop:10,display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:8}}>
      {knobs.slice(0,8).map(k=><div key={k.knob} style={{border:"1px solid #333",borderRadius:6,padding:"8px 10px",background:"#151515"}}>
        <div style={{fontSize:14,fontWeight:800,color:"#e5e5e5",marginBottom:4}}>{k.display_name||k.knob}</div>
        {(k.values||[]).slice(0,3).map(v=><div key={String(v.value)} style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",lineHeight:1.55}}>{String(v.value)} · {v.count}wf{Array.isArray(v.wafers)&&v.wafers.length?" · "+v.wafers.slice(0,8).join(","):""}</div>)}
      </div>)}
    </div>}
    {choices.length>0&&<FlowiChoices question={tool.clarification?.question} choices={choices} onChoice={onChoice} onNavigate={onNavigate}/>}
    {argumentChoices&&Array.isArray(argumentChoices.fields)&&argumentChoices.fields.length>0&&<FlowiArgumentChoices data={argumentChoices} basePrompt={prompt} onChoice={onChoice}/>}
    <FlowiFeedback result={result} tool={tool} prompt={prompt} isAdmin={isAdmin}/>
    {isAdmin&&nextActions.length>0&&<FlowiNextActions actions={nextActions} onNavigate={onNavigate} onChoice={onChoice}/>}
  </div>);
}
const FR_TD={padding:"5px 6px",borderBottom:"1px solid #262626",color:"#d4d4d4",whiteSpace:"nowrap"};

const FLOWI_LIVE_STEPS=[
  ["요청 접수","prompt와 대화 context를 서버로 보냅니다."],
  ["권한 확인","현재 계정이 사용할 수 있는 단위기능을 확인합니다."],
  ["의도 선택","가장 가까운 workflow와 tool을 고릅니다."],
  ["DB/cache 조회","필요한 FAB/ET/INLINE/cache 데이터를 찾습니다."],
  ["LLM 정리","로컬 결과를 근거로 답변 문장을 다듬습니다."],
  ["화면 구성","표, 차트, 선택지, 답변을 같은 카드에 묶습니다."],
];

function FlowiLiveTrace({step=0}){
  return(<div style={{marginTop:8,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"9px 10px",fontFamily:"monospace"}}>
    <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:7}}>
      <span style={{width:7,height:7,borderRadius:999,background:"#f97316",display:"inline-block",animation:"flowiConnBlink .75s ease-in-out infinite"}}/>
      <span style={{fontSize:14,fontWeight:900,color:"#e5e5e5"}}>작업 흐름</span>
      <span style={{fontSize:14,color:"#737373"}}>공개 가능한 실행 단계 요약</span>
    </div>
    <div style={{display:"grid",gap:5}}>
      {FLOWI_LIVE_STEPS.map(([label,detail],i)=>{
        const done=i<step,active=i===step;
        return <div key={label} style={{display:"grid",gridTemplateColumns:"18px 96px minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
          <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:14,border:"1px solid "+(done?"#22c55e":active?"#f97316":"#333"),color:done?"#22c55e":active?"#f97316":"#737373"}}>{done?"✓":i+1}</span>
          <span style={{color:active?"#f97316":done?"#d4d4d4":"#737373",fontWeight:active?900:700}}>{label}</span>
          <span style={{color:active?"#d4d4d4":"#737373",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{detail}</span>
        </div>;
      })}
    </div>
  </div>);
}

function FlowiTrace({trace}){
  const steps=Array.isArray(trace?.steps)?trace.steps:[];
  if(!steps.length)return null;
  const colorFor=(status)=>status==="done"?"#22c55e":status==="blocked"?"#ef4444":status==="error"?"#ef4444":status==="skipped"?"#737373":"#f97316";
  return(<details style={{marginTop:8,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"7px 9px"}}>
    <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontFamily:"monospace",fontWeight:800}}>
      작업 흐름 보기 <span style={{fontWeight:400,color:"#737373"}}>사고과정 원문이 아닌 실행 로그</span>
    </summary>
    <div style={{marginTop:7,display:"grid",gap:5,fontFamily:"monospace"}}>
      {steps.map((s,i)=><div key={s.key||i} style={{display:"grid",gridTemplateColumns:"18px 118px minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.4}}>
        <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:14,border:`1px solid ${colorFor(s.status)}99`,color:colorFor(s.status)}}>{s.status==="done"?"✓":s.status==="blocked"?"!":i+1}</span>
        <span style={{color:"#d4d4d4",fontWeight:800}}>{s.label||s.key}</span>
        <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={s.detail||""}>{s.detail||""}</span>
      </div>)}
      {trace.note&&<div style={{marginTop:4,fontSize:14,color:"#737373",lineHeight:1.45}}>{trace.note}</div>}
    </div>
  </details>);
}

function FlowiChoices({question,choices,onChoice,onNavigate}){
  return(<div style={{marginTop:12,border:"1px solid #7c2d12",borderRadius:8,background:"#1f130b",padding:"10px 11px",boxShadow:"0 0 0 1px rgba(249,115,22,0.12)"}}>
    <div style={{display:"flex",alignItems:"center",gap:7,marginBottom:8,flexWrap:"wrap"}}>
      <span style={{fontSize:14,color:"#f97316",fontWeight:900,fontFamily:"'JetBrains Mono',monospace",border:"1px solid #7c2d12",borderRadius:999,padding:"2px 7px"}}>확인 선택</span>
      <span style={{fontSize:14,fontWeight:800,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace"}}>{question||"확인이 필요합니다."}</span>
    </div>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:7}}>
      {choices.map((c,i)=><button key={c.id||i} type="button" onClick={()=>{
        const tab=c.tab||c.feature||"";
        if(tab&&typeof onNavigate==="function")onNavigate(tab);
        else if(onChoice)onChoice(c.prompt||c.title||"");
      }}
        style={{textAlign:"left",border:"1px solid "+(c.recommended?"#f97316":"#333"),borderRadius:7,background:c.recommended?"#2a1608":"#171717",padding:"8px 9px",cursor:"pointer",color:"#d4d4d4"}}>
        <div style={{display:"flex",gap:6,alignItems:"center",marginBottom:3}}>
          <span style={{fontSize:14,fontWeight:900,color:c.recommended?"#f97316":"#a3a3a3",fontFamily:"monospace"}}>{c.label||i+1}</span>
          <span style={{fontSize:14,fontWeight:800,color:"#e5e5e5"}}>{c.title}</span>
          {c.recommended&&<span style={{fontSize:14,color:"#f97316",border:"1px solid #7c2d12",borderRadius:999,padding:"1px 5px",marginLeft:"auto"}}>recommended</span>}
        </div>
        <div style={{fontSize:14,lineHeight:1.45,color:"#a3a3a3"}}>{c.description}</div>
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
  const submit=(field,value,prompt)=>{
    const val=String(value||"").trim();
    const q=String(prompt||basePrompt||"").trim();
    if(onChoice)onChoice(val?(q?`${q} ${val}`:val):q);
  };
  return(<div style={{marginTop:12,border:"1px solid #333",borderRadius:8,background:"#111",padding:"10px 11px"}}>
    <div style={{fontSize:14,color:"#f97316",fontWeight:900,fontFamily:"monospace",marginBottom:8}}>{data.message||"또는 직접 입력해 주세요"}</div>
    <div style={{display:"grid",gap:9}}>
      {fields.map(f=>{
        const choices=Array.isArray(f.choices)?f.choices:[];
        return <div key={f.field} style={{display:"grid",gap:6}}>
          <div style={{fontSize:14,color:"#e5e5e5",fontWeight:800,fontFamily:"monospace"}}>{f.field}</div>
          <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
            {choices.filter(c=>!c.free_input).slice(0,3).map(c=><button key={c.id||c.value} type="button" onClick={()=>submit(f.field,c.value,c.prompt)} style={{border:"1px solid "+(c.recommended?"#f97316":"#333"),borderRadius:999,background:c.recommended?"#2a1608":"#171717",color:"#d4d4d4",fontSize:14,padding:"5px 9px",cursor:"pointer"}}>
              <span style={{color:"#f97316",fontWeight:900,marginRight:5}}>{c.label}</span>{c.title||c.value}
            </button>)}
          </div>
          <div style={{display:"flex",gap:6,minWidth:0}}>
            <input value={free[f.field]||""} onChange={e=>setFree(v=>({...v,[f.field]:e.target.value}))} placeholder={f.free_input_label||"직접 입력"} style={{flex:1,minWidth:0,border:"1px solid #333",borderRadius:7,background:"#171717",color:"#e5e5e5",fontSize:14,padding:"7px 8px",fontFamily:"'JetBrains Mono',monospace"}}/>
            <button type="button" onClick={()=>submit(f.field,free[f.field]||"",basePrompt)} style={{border:"1px solid #7c2d12",borderRadius:7,background:"#1f130b",color:"#f97316",fontSize:14,fontWeight:900,padding:"7px 10px",cursor:"pointer"}}>입력</button>
          </div>
        </div>;
      })}
    </div>
  </div>);
}

function FlowiNextActions({actions,onNavigate,onChoice}){
  return(<div style={{marginTop:10,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"8px 9px"}}>
    <div style={{fontSize:14,fontWeight:900,color:"#a3a3a3",fontFamily:"'JetBrains Mono',monospace",marginBottom:6}}>next actions</div>
    <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
      {actions.map((a,i)=>{
        const clickable=(a.type==="open_tab"&&a.tab&&typeof onNavigate==="function")||(a.prompt&&typeof onChoice==="function");
        const click=()=>{if(a.type==="open_tab"&&a.tab&&onNavigate)onNavigate(a.tab);else if(a.prompt&&onChoice)onChoice(a.prompt);};
        return <button key={a.id||i} type="button" onClick={click} disabled={!clickable} title={a.description||""}
          style={{fontSize:14,color:clickable?"#f97316":"#a3a3a3",fontFamily:"monospace",border:"1px solid "+(clickable?"#7c2d12":"#333"),borderRadius:999,padding:"3px 8px",background:clickable?"#1f130b":"#171717",cursor:clickable?"pointer":"default",opacity:clickable?1:.82}}>
          {a.title||a.type}
        </button>;
      })}
    </div>
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
  if(Array.isArray(data.series)&&data.series.length)return <FlowiLineResult data={data}/>;
  if(Array.isArray(data.groups)&&data.groups.length)return <FlowiGroupBarResult data={data}/>;
  if(Array.isArray(data.boxes)&&data.boxes.length)return <FlowiBoxResult data={data}/>;
  if(data.kind==="dashboard_wafer_map"&&Array.isArray(data.points))return <FlowiWaferMapResult data={data}/>;
  const pts=Array.isArray(data.points)?data.points.filter(p=>Number.isFinite(Number(p.x))&&Number.isFinite(Number(p.y))):[];
  if(!pts.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 numeric point가 없습니다.</div>;
  const W=520,H=300,pad={l:54,r:18,t:22,b:44};
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
  const colorFor=(p)=>colorMap.get(String(p.color_value??""))||"#3b82f6";
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5"}}>{data.title||"Flowi scatter"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>n={data.total||pts.length} · corr={data.corr??"-"}{fit?` · R²=${fit.r2}`:""}{data.color_by?` · color=${data.color_by}`:""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block"}}>
      {[0,0.5,1].map((f)=><g key={`y${f}`}>
        <line x1={pad.l} x2={W-pad.r} y1={pad.t+(H-pad.t-pad.b)*(1-f)} y2={pad.t+(H-pad.t-pad.b)*(1-f)} stroke="#333" strokeDasharray="3,4"/>
        <text x={pad.l-8} y={pad.t+(H-pad.t-pad.b)*(1-f)+3} textAnchor="end" fontSize="9" fill="#a3a3a3">{(minY+ry*f).toFixed(2)}</text>
      </g>)}
      {[0,0.5,1].map((f)=><g key={`x${f}`}>
        <line y1={pad.t} y2={H-pad.b} x1={pad.l+(W-pad.l-pad.r)*f} x2={pad.l+(W-pad.l-pad.r)*f} stroke="#262626" strokeDasharray="2,5"/>
        <text x={pad.l+(W-pad.l-pad.r)*f} y={H-20} textAnchor="middle" fontSize="9" fill="#a3a3a3">{(minX+rx*f).toFixed(2)}</text>
      </g>)}
      <line x1={pad.l} x2={W-pad.r} y1={H-pad.b} y2={H-pad.b} stroke="#525252"/>
      <line x1={pad.l} x2={pad.l} y1={pad.t} y2={H-pad.b} stroke="#525252"/>
      {fit&&<line x1={sx(x0)} y1={sy(y0)} x2={sx(x1)} y2={sy(y1)} stroke="#ef4444" strokeWidth="2" strokeDasharray="7,4"/>}
      {pts.slice(0,500).map((p,i)=><circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="3.2" fill={colorFor(p)} opacity="0.78">
        <title>{`${p.label||p.join_key||""}\nX=${p.x}\nY=${p.y}${p.color_value?`\n${data.color_by||"color"}=${p.color_value}`:""}\nINLINE n=${p.inline_n||0}, ET n=${p.et_n||0}`}</title>
      </circle>)}
      <text x={(pad.l+W-pad.r)/2} y={H-5} textAnchor="middle" fontSize="10" fill="#f97316">{data.x_label||"x"}</text>
      <text x="12" y={(pad.t+H-pad.b)/2} transform={`rotate(-90,12,${(pad.t+H-pad.b)/2})`} textAnchor="middle" fontSize="10" fill="#f97316">{data.y_label||"y"}</text>
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
  const W=620,H=300,pad={l:54,r:20,t:22,b:66};
  const vals=boxes.flatMap(b=>[Number(b.min),Number(b.max),Number(b.q1),Number(b.q3),Number(b.median)]);
  const minY=Math.min(...vals),maxY=Math.max(...vals),ry=maxY-minY||1;
  const sy=(v)=>pad.t+(H-pad.t-pad.b)-(Number(v)-minY)/ry*(H-pad.t-pad.b);
  const step=(W-pad.l-pad.r)/boxes.length;
  const boxW=Math.max(12,Math.min(34,step*.48));
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5"}}>{data.title||"Flowi box plot"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>groups={data.total||boxes.length} · {data.metric||""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block"}}>
      {[0,0.5,1].map(f=><g key={f}>
        <line x1={pad.l} x2={W-pad.r} y1={pad.t+(H-pad.t-pad.b)*(1-f)} y2={pad.t+(H-pad.t-pad.b)*(1-f)} stroke="#333" strokeDasharray="3,4"/>
        <text x={pad.l-8} y={pad.t+(H-pad.t-pad.b)*(1-f)+3} textAnchor="end" fontSize="9" fill="#a3a3a3">{(minY+ry*f).toFixed(2)}</text>
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
          <text x={cx} y={H-36} textAnchor="end" transform={`rotate(-38 ${cx} ${H-36})`} fontSize="9" fill="#a3a3a3">{String(b.label||"-").slice(0,14)}</text>
          <title>{`${b.label||""}\nmin=${b.min}\nq1=${b.q1}\nmedian=${b.median}\nq3=${b.q3}\nmax=${b.max}\nmean=${b.mean??"-"}\nn=${b.n??"-"}`}</title>
        </g>;
      })}
      <text x="12" y={(pad.t+H-pad.b)/2} transform={`rotate(-90,12,${(pad.t+H-pad.b)/2})`} textAnchor="middle" fontSize="10" fill="#f97316">{data.y_label||"value"}</text>
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
  const W=620,H=300,pad={l:54,r:18,t:22,b:48};
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
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5"}}>{data.title||"Flowi trend"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>points={data.total||series[0].points.length} · {data.metric||""}</div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block"}}>
      {[0,0.5,1].map((f)=><g key={`y${f}`}>
        <line x1={pad.l} x2={W-pad.r} y1={pad.t+(H-pad.t-pad.b)*(1-f)} y2={pad.t+(H-pad.t-pad.b)*(1-f)} stroke="#333" strokeDasharray="3,4"/>
        <text x={pad.l-8} y={pad.t+(H-pad.t-pad.b)*(1-f)+3} textAnchor="end" fontSize="9" fill="#a3a3a3">{(minY+ry*f).toFixed(2)}</text>
      </g>)}
      {[0,0.5,1].map((f)=>{
        const idx=Math.round((maxN-1)*f);
        return <g key={`x${f}`}>
          <line y1={pad.t} y2={H-pad.b} x1={sx(idx)} x2={sx(idx)} stroke="#262626" strokeDasharray="2,5"/>
          <text x={sx(idx)} y={H-20} textAnchor="middle" fontSize="9" fill="#a3a3a3">{labelAt(idx)}</text>
        </g>;
      })}
      <line x1={pad.l} x2={W-pad.r} y1={H-pad.b} y2={H-pad.b} stroke="#525252"/>
      <line x1={pad.l} x2={pad.l} y1={pad.t} y2={H-pad.b} stroke="#525252"/>
      {series.map((s,si)=><g key={s.name||si}>
        <path d={pathFor(s.points)} fill="none" stroke={palette[si%palette.length]} strokeWidth="2.2"/>
        {s.points.map((p,i)=><circle key={i} cx={sx(i)} cy={sy(p.y)} r="3" fill={palette[si%palette.length]} opacity=".9">
          <title>{`${p.x_label||p.bucket||p.x}\n${s.name||data.metric||"value"}=${p.y}\nmean=${p.mean??"-"}\nn=${p.n??"-"}\nwafer_groups=${p.wafer_groups??"-"}`}</title>
        </circle>)}
      </g>)}
      <text x={(pad.l+W-pad.r)/2} y={H-5} textAnchor="middle" fontSize="10" fill="#f97316">{data.x_label||"x"}</text>
      <text x="12" y={(pad.t+H-pad.b)/2} transform={`rotate(-90,12,${(pad.t+H-pad.b)/2})`} textAnchor="middle" fontSize="10" fill="#f97316">{data.y_label||"y"}</text>
    </svg>
    <div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>
      {series.map((s,si)=><span key={s.name||si} style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px",display:"inline-flex",alignItems:"center",gap:5}}>
        <span style={{width:8,height:8,borderRadius:999,background:palette[si%palette.length],display:"inline-block"}}></span>{s.name||data.metric||"series"}
      </span>)}
      <span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>INLINE median by date</span>
    </div>
  </div>);
}

function FlowiGroupBarResult({data}){
  const groups=(Array.isArray(data.groups)?data.groups:[]).map(g=>({...g,value:Number(g.value??g.median??g.mean)})).filter(g=>Number.isFinite(g.value)).slice(0,24);
  if(!groups.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 group 값이 없습니다.</div>;
  const W=620,H=Math.max(260,groups.length*24+58),pad={l:148,r:64,t:18,b:30};
  const minV=Math.min(0,...groups.map(g=>g.value)),maxV=Math.max(...groups.map(g=>g.value));
  const rv=maxV-minV||1;
  const sx=(v)=>pad.l+(Number(v)-minV)/rv*(W-pad.l-pad.r);
  const rowH=(H-pad.t-pad.b)/groups.length;
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101418",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:8}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5"}}>{data.title||"Flowi group chart"}</div>
      <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>groups={data.total||groups.length} · {data.metric||""}</div>
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
  const cols=table.columns||[];
  const rows=table.rows||[];
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,overflow:"hidden",background:"#121212"}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:8,padding:"8px 10px",borderBottom:"1px solid #2a2a2a",background:"#171717"}}>
      <div style={{fontSize:14,fontWeight:800,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace"}}>{table.title||"Flowi table"}</div>
      <div style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{rows.length}{table.total&&table.total!==rows.length?` / ${table.total}`:""} rows</div>
    </div>
    <div style={{overflow:"auto",maxHeight:360}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace"}}>
        <thead><tr>{cols.map(c=><th key={c.key} style={{position:"sticky",top:0,zIndex:1,textAlign:"left",padding:"7px 8px",borderBottom:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",whiteSpace:"nowrap"}}>{c.label||c.key}</th>)}</tr></thead>
        <tbody>{rows.map((r,i)=><tr key={i}>
          {cols.map(c=><td key={c.key} style={{padding:"6px 8px",borderBottom:"1px solid #262626",color:c.key==="wafer_id"||String(c.key).includes("STI")?"#e5e5e5":"#c7c7c7",whiteSpace:"nowrap",fontWeight:String(c.key).startsWith("KNOB")||String(c.label).includes("KNOB")?800:500}}>{r[c.key]??""}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
  </div>);
}

export default function My_Home({onNavigate,user}){
  const nav=(k)=>onNavigate&&onNavigate(k);
  const isAdmin=user?.role==="admin";
  const userTabs=isAdmin?"__all__":(user?.tabs||"");
  const hasTab=(k)=>userTabs==="__all__"||userTabs.split(",").map(s=>s.trim()).filter(Boolean).includes(k);

  // v8.7.4: TABS 순서와 동일하게 카드 정렬. 홈 카드에 inform/meeting/calendar 포함.
  // v8.8.5: 카드별 tag(개별 버전) 제거 — 통합 버전(v8.8.5) 만 의미 있음.
  const ALL_CARDS=[
    {key:"filebrowser",icon:"📂",title:"파일 탐색기",desc:"Parquet 탐색, SQL 필터, CSV 다운로드"},
    {key:"dashboard",  icon:"📊",title:"대시보드",desc:"동적 차트, 산점도, 추세"},
    {key:"splittable", icon:"🗂️",title:"스플릿 테이블",desc:"Plan vs actual, 공유 추적"},
    {key:"diagnosis",  icon:"🤖",title:"에이전트",desc:"Flow-i 동작, RCA 지식, 품질/LLM 관리"},
    {key:"tracker",    icon:"📋",title:"이슈 추적",desc:"이슈 게시판, Lot/Wafer 추적"},
    {key:"inform",     icon:"📢",title:"인폼 로그",desc:"모듈 인폼 + 스레드 + 이미지"},
    {key:"meeting",    icon:"🗓",title:"회의관리",desc:"차수·반복·아젠다·회의록"},
    {key:"calendar",   icon:"📅",title:"변경점 관리",desc:"달력·카테고리·회의 연동"},
    {key:"ettime",     icon:"⏱️",title:"ET 레포트",desc:"fab_lot_id + step 기준 elapsed 분석"},
    {key:"waferlayout",icon:"🧭",title:"WF Layout",desc:"제품별 wafer/shot/chip layout 검토"},
    {key:"tablemap",   icon:"🗺️",title:"테이블 맵",desc:"DB 관계 그래프",adminOnly:true},
    {key:"admin",      icon:"⚙️",title:"관리자",desc:"사용자, 권한, 모니터",adminOnly:true},
    {key:"devguide",   icon:"📖",title:"개발자 가이드",desc:"아키텍처, API 레퍼런스"},
  ];
  const visibleCards=ALL_CARDS.filter(c=>(!c.adminOnly||isAdmin)&&hasTab(c.key));

  return(<div style={{minHeight:"calc(100vh - 52px)",padding:"32px 32px 96px",background:"var(--bg-primary,#1a1a1a)",color:"var(--text-primary,#e5e5e5)",fontFamily:"'Pretendard',sans-serif",maxWidth:1040,margin:"0 auto"}}>
    {/* v8.3.3: Home brand logo — shared BrandLogo.jsx, size="home" retains .home-brand-logo marker. */}
    <BrandLogo size="home"/>
    {/* Terminal header */}
    <div style={{background:"#111",borderRadius:12,border:"1px solid #333",overflow:"hidden",marginBottom:28,boxShadow:"0 2px 20px rgba(0,0,0,0.4)"}}>
      <div style={{display:"flex",alignItems:"center",gap:8,padding:"8px 14px",background:"#1a1a1a",borderBottom:"1px solid #333"}}>
        <div style={{display:"flex",gap:6}}><div style={{width:10,height:10,borderRadius:"50%",background:"#ef4444"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#fbbf24"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#22c55e"}}/></div>
        <span style={{fontSize:14,color:"#525252",fontFamily:"monospace",marginLeft:6}}>flow-i console</span>
      </div>
      <div style={{display:"flex",gap:20,padding:"20px 24px",alignItems:"flex-start"}}>
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:4,flexShrink:0}}><Holli size={72}/><span style={{fontSize:14,color:"#f97316",fontFamily:"monospace",letterSpacing:"0.12em",fontWeight:700}}>flow-i</span></div>
        <div style={{flex:1,paddingTop:4}}>
          <div style={{marginTop:6,fontFamily:"'JetBrains Mono',monospace",fontSize:14}}><span style={{color:"#f97316"}}>{">"}</span><span style={{color:"#737373"}}> </span><WelcomeType name={user?.username||"user"}/></div>
          <FlowiConsole onNavigate={nav} user={user}/>
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
      <div style={{padding:"14px 20px",borderBottom:"1px solid var(--border,#333)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
        <span style={{fontSize:14,fontWeight:700,fontFamily:"'JetBrains Mono',monospace",color:"var(--accent,#f97316)"}}>{">"} 사용 방법</span>
        <span style={{fontSize:14,color:"var(--text-secondary)"}}>권한있는 기능 가이드</span>
      </div>
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
