import { useState, useEffect, useRef } from "react";
import { FEATURE_VERSIONS } from "../config";
import BrandLogo from "../components/BrandLogo";
const B="#ea580c",M="#f97316",L="#fb923c",D="#9a3412",BK="#171717",W="#fff7ed",PK="#fda4af",G="#fbbf24";

// v8.3.3: PF_HOME / PixelGlyph / HomeBrandLogo extracted to shared ../components/BrandLogo.jsx.
// Home uses <BrandLogo size="home" version={ver}/>; nav uses <BrandLogo size="nav"/> (see App.jsx).


const BASE_PX=[[2,5,B],[2,6,B],[2,7,B],[2,8,B],[2,9,B],[2,10,B],[3,4,B],[3,5,M],[3,6,M],[3,7,M],[3,8,M],[3,9,M],[3,10,M],[3,11,B],[4,3,B],[4,4,M],[4,5,L],[4,6,L],[4,7,L],[4,8,L],[4,9,L],[4,10,L],[4,11,M],[4,12,B],[5,3,B],[5,4,M],[5,5,L],[5,6,L],[5,7,L],[5,8,L],[5,9,L],[5,10,L],[5,11,M],[5,12,B],[8,3,B],[8,4,PK],[8,5,L],[8,6,L],[8,7,L],[8,8,L],[8,9,L],[8,10,L],[8,11,PK],[8,12,B],[9,3,B],[9,4,M],[9,5,L],[9,6,L],[9,7,BK],[9,8,BK],[9,9,L],[9,10,L],[9,11,M],[9,12,B],[10,3,B],[10,4,M],[10,5,M],[10,6,M],[10,7,M],[10,8,M],[10,9,M],[10,10,M],[10,11,M],[10,12,B],[11,4,B],[11,5,B],[11,6,B],[11,7,B],[11,8,B],[11,9,B],[11,10,B],[11,11,B],[12,5,B],[12,6,B],[12,9,B],[12,10,B],[13,5,D],[13,6,D],[13,9,D],[13,10,D],[0,7,G],[1,7,G],[0,8,G],[1,8,G]];
const EO=[[6,3,B],[6,4,M],[6,5,W],[6,6,BK],[6,7,L],[6,8,L],[6,9,W],[6,10,BK],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,W],[7,6,BK],[7,7,L],[7,8,L],[7,9,W],[7,10,BK],[7,11,M],[7,12,B]];
const EC=[[6,3,B],[6,4,M],[6,5,L],[6,6,L],[6,7,L],[6,8,L],[6,9,L],[6,10,L],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,BK],[7,6,BK],[7,7,L],[7,8,L],[7,9,BK],[7,10,BK],[7,11,M],[7,12,B]];
const AD=[[7,1,M],[7,2,M],[8,1,B],[7,13,M],[7,14,M],[8,14,B]];
const AW=[[7,1,M],[7,2,M],[8,1,B],[5,13,M],[5,14,G],[6,13,M],[6,14,B]];
function Holli({size=72}){const[fr,setFr]=useState("idle");const t=useRef(null);useEffect(()=>{const loop=()=>{t.current=setTimeout(()=>{if(Math.random()<0.6){setFr("blink");setTimeout(()=>{setFr("idle");loop();},150);}else{setFr("wave");setTimeout(()=>{setFr("idle");loop();},600);}},1500+Math.random()*2500);};loop();return()=>clearTimeout(t.current);},[]);const px=[...BASE_PX,...(fr==="blink"?EC:EO),...(fr==="wave"?AW:AD)];return(<div style={{animation:fr==="idle"?"holBob 2s ease-in-out infinite":"none"}}><style>{`@keyframes holBob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}@keyframes holBlink{0%,100%{opacity:1}50%{opacity:0}}`}</style><svg width={size} height={size} viewBox="0 0 16 16" style={{imageRendering:"pixelated"}}>{px.map(([r,c,color],i)=><rect key={i} x={c} y={r} width={1} height={1} fill={color}/>)}</svg></div>);}
function Cli({cmd,output,delay=0}){const[show,setShow]=useState(delay===0);const[typed,setTyped]=useState("");const[done,setDone]=useState(false);useEffect(()=>{if(delay){const t=setTimeout(()=>setShow(true),delay);return()=>clearTimeout(t);}},[delay]);useEffect(()=>{if(!show)return;let i=0;const iv=setInterval(()=>{i++;setTyped(cmd.slice(0,i));if(i>=cmd.length){clearInterval(iv);setTimeout(()=>setDone(true),100);}},30);return()=>clearInterval(iv);},[show,cmd]);if(!show)return null;return(<div style={{marginBottom:4,fontFamily:"'JetBrains Mono',monospace",fontSize:13,lineHeight:1.7}}><span style={{color:"#f97316"}}>{">"}</span><span style={{color:"#737373"}}> flow </span><span style={{color:"#e5e5e5"}}>{typed}</span>{!done&&<span style={{display:"inline-block",width:8,height:14,background:"#f97316",marginLeft:2,animation:"holBlink 0.6s step-end infinite"}}/>}{done&&output&&<div style={{color:"#a3a3a3",paddingLeft:20,fontSize:12}}>{output}</div>}</div>);}
function WelcomeType({name}){const full=name.toUpperCase()+"_";const[len,setLen]=useState(0);const[done,setDone]=useState(false);useEffect(()=>{const t=setTimeout(()=>{let i=0;const iv=setInterval(()=>{i++;setLen(i);if(i>=full.length){clearInterval(iv);setDone(true);}},70);return()=>clearInterval(iv);},1200);return()=>clearTimeout(t);},[full]);return(<span><span style={{color:"#e5e5e5",fontWeight:700}}>{full.slice(0,len)}</span>{!done&&<span style={{display:"inline-block",width:8,height:14,background:"#f97316",marginLeft:2,animation:"holBlink 0.5s step-end infinite"}}/>}{done&&<span style={{display:"inline-block",width:8,height:14,background:"#f97316",marginLeft:2,animation:"holBlink 1s step-end infinite"}}/>}</span>);}
function Card({icon,title,desc,tag,onClick,width=220}){return(<div onClick={onClick} onMouseEnter={e=>{e.currentTarget.style.borderColor="#f97316";e.currentTarget.style.background="#f9731610";}} onMouseLeave={e=>{e.currentTarget.style.borderColor="var(--border,#333)";e.currentTarget.style.background="var(--bg-card,#2a2a2a)";}} style={{background:"var(--bg-card,#2a2a2a)",borderRadius:12,padding:"20px 24px",cursor:onClick?"pointer":"default",border:"1px solid var(--border,#333)",transition:"all 0.2s",position:"relative",width,boxSizing:"border-box"}}>{tag&&<span style={{position:"absolute",top:12,right:12,fontSize:9,fontWeight:700,padding:"2px 6px",borderRadius:3,background:"#f9731622",color:"#f97316",fontFamily:"monospace",textTransform:"uppercase"}}>{tag}</span>}<div style={{fontSize:28,marginBottom:10}}>{icon}</div><div style={{fontSize:14,fontWeight:700,color:"var(--text-primary,#e5e5e5)",marginBottom:6,fontFamily:"'JetBrains Mono',monospace"}}>{title}</div><div style={{fontSize:12,color:"var(--text-secondary,#a3a3a3)",lineHeight:1.6}}>{desc}</div></div>);}

// Feature guide content shown to users (non-admin) instead of changelog
const FEATURE_GUIDES={
  filebrowser:{icon:"📂",title:"파일 브라우저",steps:["좌측 사이드바에서 DB 선택","하위 Product/파일 선택 시 데이터 자동 로드","SQL 입력창에 필터 입력 (예: PRODUCT_TYPE == 'A', LOT_ID LIKE '%ABC%')","컬럼 선택 → CSV 다운로드 버튼"]},
  splittable:{icon:"🗂️",title:"스플릿 테이블",steps:["Product 선택 → Root Lot + Wafer IDs 입력 → 검색","Plan 입력 모드: 편집 클릭 후 셀 클릭하여 계획값 입력","셀 색: 회색(없음) / 주황(plan만) / 파스텔(actual) / 초록(match) / 빨강(mismatch)","이력 탭에서 변경 이력 확인"]},
  dashboard:{icon:"📊",title:"대시보드",steps:["데이터 소스 선택 (DB / Root Parquet / Product)","차트 타입: scatter / line / bar / pie / binning","X/Y 컬럼 선택 + 필터 SQL 입력","Days 옵션으로 기간 제한, binning 은 bin_count/bin_width 조정"]},
  tracker:{icon:"📋",title:"트래커",steps:["이슈 게시판 — 제목 + 본문 + 이미지 업로드","Lot/Wafer 범위 지정 (Excel 붙여넣기 지원)","댓글 + 중첩 답글 + 이미지","Gantt 뷰로 전체 진행 현황 확인"]},
  ettime:{icon:"⏱️",title:"ET Time",steps:["장비별 경과시간 분석","Equipment → Step 선택","시간 구간 설정 후 차트 확인"]},
  tablemap:{icon:"🗺️",title:"테이블 맵",steps:["DB 간 관계 그래프 조회","노드 더블클릭 → 상세 정보","관계선 drag-drop 으로 편집"]},
  devguide:{icon:"📖",title:"개발 가이드",steps:["아키텍처 다이어그램","API 엔드포인트 문서","Gotchas / 코드 규칙"]},
};

// v8.1.7: localStorage cache for instant first-paint of version block
const VCACHE_KEY="hol_home_version_v1";
function readVerCache(){try{const s=localStorage.getItem(VCACHE_KEY);return s?JSON.parse(s):null;}catch{return null;}}
function writeVerCache(v){try{localStorage.setItem(VCACHE_KEY,JSON.stringify(v));}catch{}}

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
  const nav=(k)=>onNavigate&&onNavigate(k);const fv=FEATURE_VERSIONS||{};
  const isAdmin=user?.role==="admin";
  const userTabs=isAdmin?"__all__":(user?.tabs||"");
  const hasTab=(k)=>userTabs==="__all__"||userTabs.split(",").map(s=>s.trim()).filter(Boolean).includes(k);

  const ALL_CARDS=[
    {key:"filebrowser",icon:"📂",title:"파일 브라우저",desc:"Parquet 탐색, SQL 필터, CSV 다운로드",tag:fv.filebrowser?"v"+fv.filebrowser:""},
    {key:"splittable", icon:"🗂️",title:"스플릿 테이블",desc:"Plan vs actual, 공유 추적",tag:fv.splittable?"v"+fv.splittable:""},
    {key:"dashboard",  icon:"📊",title:"대시보드",desc:"동적 차트, 산점도, 추세",tag:fv.dashboard?"v"+fv.dashboard:""},
    {key:"tracker",    icon:"📋",title:"트래커",desc:"이슈 게시판, Lot/Wafer 추적",tag:fv.tracker?"v"+fv.tracker:""},
    {key:"ettime",     icon:"⏱️",title:"ET Time",desc:"장비 경과시간 분석",tag:fv.ettime?"v"+fv.ettime:""},
    {key:"tablemap",   icon:"🗺️",title:"테이블 맵",desc:"DB 관계 그래프",tag:fv.tablemap?"v"+fv.tablemap:"",adminOnly:true},
    {key:"admin",      icon:"⚙️",title:"관리자",desc:"사용자, 권한, 모니터",tag:fv.admin?"v"+fv.admin:"",adminOnly:true},
    {key:"devguide",   icon:"📖",title:"개발 가이드",desc:"아키텍처, API 레퍼런스",tag:fv.devguide?"v"+fv.devguide:""},
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
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:4,flexShrink:0}}><Holli size={72}/><span style={{fontSize:9,color:"#f97316",fontFamily:"monospace",letterSpacing:"0.12em",fontWeight:700}}>flow-i</span></div>
        <div style={{flex:1,paddingTop:4}}>
          <Cli cmd="--version" output={`v${ver} "${codename}"`}/>
          <div style={{marginTop:6,fontFamily:"'JetBrains Mono',monospace",fontSize:13}}><span style={{color:"#f97316"}}>{">"}</span><span style={{color:"#737373"}}> WELCOME </span><WelcomeType name={user?.username||"user"}/></div>
        </div>
      </div>
    </div>

    {/* Permission-filtered cards, centered */}
    {visibleCards.length>0?<div style={{display:"flex",flexWrap:"wrap",gap:14,justifyContent:"center",marginBottom:32}}>
      {visibleCards.map(c=><Card key={c.key} icon={c.icon} title={c.title} desc={c.desc} tag={c.tag} onClick={()=>nav(c.key)}/>)}
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
