import { useState, useEffect, useRef, useMemo, Component } from "react";
import Loading from "../components/Loading";
import { PageHeader, TabStrip, Button, Banner, Pill, statusPalette, chartPalette } from "../components/UXKit";
import { toast } from "../components/Toast";
import { PROCESS_AREAS, areaColor } from "../constants/processAreas";
import { sf, dl, postJson, userLabel, userMatches } from "../lib/api";
import { SUB_TABS, TABS } from "../config";
// v9.2.x: 에이전트 탭 재편 — Semantic layer 편집기와 LLM 설정을 관리 탭으로 이관.
import SemanticLayerPanel from "../components/agent/SemanticLayerPanel";
import LlmTab from "../components/agent/LlmTab";
// v8.8.3: inform/meeting/calendar 권한 항목 추가.
// v8.8.22: dashboard_chart 제거 (페이지 위임 탭이 같은 역할 수행). 실제 nav 메뉴 순서로 재배치.
// v9.3.x: devguide 는 admin 전용 — 유저 탭 권한 목록에서 제외.
// v9.4.x: flowi — Flow-i 채팅 사용 권한 (홈 채팅 + home agent orchestrate 게이트).
const ALL_TABS=["flowi","filebrowser","dashboard","splittable","ramcache","diagnosis","tracker","valve","inform","meeting","calendar","teg","ettime"];
const BULK_DEFAULT_TABS=["filebrowser","dashboard","splittable","diagnosis","inform","meeting","calendar"];
const BULK_HEADER_KEYS=new Set(["name","username","email","role","tabs"]);
const CANONICAL_PAGE_IDS=["filebrowser","dashboard","splittable","tracker","valve","inform","meeting","calendar","tablemap","groups","messages","diagnosis"];
const PAGE_ID_ALIASES={informs:"inform",meetings:"meeting",dbmap:"tablemap"};
function _canonicalPageId(v){
  const key=String(v||"").trim().toLowerCase();
  return PAGE_ID_ALIASES[key]||key;
}
// v8.7.5: u.tabs 는 string 이지만 legacy json 에서 array 로 저장된 기록이 있을 수 있어
// "r.split is not a function" 방지를 위해 정규화 헬퍼를 둔다.
function _tabsToArray(v){
  if(Array.isArray(v))return v.filter(Boolean).map(String);
  if(typeof v==="string"&&v)return v.split(",").map(s=>s.trim()).filter(Boolean);
  return ["filebrowser","dashboard","splittable"];
}
function _cleanTabs(v){
  const arr=Array.isArray(v)?v:(typeof v==="string"?v.split(","):[]);
  const seen=new Set();
  return arr.map(s=>String(s||"").trim()).filter((s)=>s&&ALL_TABS.includes(s)&&!seen.has(s)&&seen.add(s));
}
// ── 탭/소탭 표시 이름 — 사이드바·각 페이지의 실제 탭 이름과 동일하게 노출 ──
// 권한 화면에서 raw key(filebrowser 등) 대신 실제 화면 이름(파일탐색기 등)을 보여준다.
// 사이드바 TABS 에 없는 위임 전용 페이지(tablemap/groups/messages)도 같은 이름 규칙으로.
const TAB_LABELS={tablemap:"테이블 맵",groups:"그룹",messages:"문의함",flowi:"Flow-i",...Object.fromEntries(TABS.map(t=>[t.key,t.label]))};
const SUB_TAB_LABELS=Object.fromEntries(Object.entries(SUB_TABS).map(([t,subs])=>[t,Object.fromEntries(subs.map(s=>[s.key,s.label]))]));
function _tabLabel(key){return TAB_LABELS[key]||key;}
function _tabTokenLabel(token){
  const [t,s]=String(token||"").split(":");
  const tl=_tabLabel(t);
  return s?`${tl}·${SUB_TAB_LABELS[t]?.[s]||s}`:tl;
}
function _tabTokensLabel(v){
  const arr=Array.isArray(v)?v:(typeof v==="string"?v.split(","):[]);
  return arr.map(x=>String(x||"").trim()).filter(Boolean).map(_tabTokenLabel).join(", ");
}
// ── v9.1.x: 소탭 단위 권한 helpers ─────────────────────────────
// tabs 토큰: "tab"(전체 소탭) | "tab:subtab". bare 토큰이 있으면 그 탭 전체 허용.
function _mainTabChecked(tokens,t){return tokens.some(x=>x===t||String(x).startsWith(t+":"));}
function _subTabChecked(tokens,t,s){return tokens.includes(t)||tokens.includes(t+":"+s);}
function _tabTokensFor(tokens,t){return tokens.filter(x=>x===t||String(x).startsWith(t+":"));}
function _toggleMainTab(tokens,t,on){
  const rest=tokens.filter(x=>x!==t&&!String(x).startsWith(t+":"));
  return on?[...rest,t]:rest;
}
function _toggleSubTab(tokens,t,s,on){
  const subs=(SUB_TABS[t]||[]).map(x=>x.key);
  // bare 토큰이면 먼저 전체 소탭 토큰으로 펼친다.
  let cur=tokens.includes(t)?subs.slice():_tabTokensFor(tokens,t).map(x=>x.split(":")[1]).filter(Boolean);
  cur=on?[...new Set([...cur,s])]:cur.filter(x=>x!==s);
  const rest=tokens.filter(x=>x!==t&&!String(x).startsWith(t+":"));
  if(!cur.length)return rest;                       // 소탭 0개 = 탭 권한 제거
  if(subs.length&&cur.length===subs.length)return[...rest,t]; // 전체 = bare 토큰으로 압축
  return[...rest,...cur.map(x=>t+":"+x)];
}
function _tabCellMark(tokens,t){
  if(tokens.includes(t))return"O";
  const subs=_tabTokensFor(tokens,t);
  if(!subs.length)return"X";
  const all=(SUB_TABS[t]||[]).length;
  return all&&subs.length>=all?"O":"△";
}
function _splitBulkRow(line){
  return String(line||"").includes("\t")?String(line||"").split("\t"):String(line||"").split(",");
}
function _parseBulkUsers(text,defaultTabs=[]){
  const lines=String(text||"").replace(/\r\n/g,"\n").replace(/\r/g,"\n").split("\n").filter(ln=>ln.trim());
  if(!lines.length)return{hasHeader:false,rows:[]};
  const rawRows=lines.map(_splitBulkRow);
  const header=rawRows[0].map(x=>String(x||"").trim().toLowerCase());
  const hasHeader=header.some(x=>BULK_HEADER_KEYS.has(x));
  const body=hasHeader?rawRows.slice(1):rawRows;
  const fallbackTabs=_cleanTabs(defaultTabs);
  return{hasHeader,rows:body.map((parts,i)=>{
    const vals=parts.map(x=>String(x||"").trim());
    let name="",username="",email="",role="user",tabs="";
    if(hasHeader){
      const data={};
      header.forEach((h,idx)=>{data[h]=vals[idx]||"";});
      name=data.name||"";
      username=data.username||"";
      email=data.email||"";
      role=data.role||"user";
      tabs=data.tabs||"";
    }else{
      name=vals[0]||"";
      username=vals[1]||(vals[0]||"");
      const third=vals[2]||"";
      if(third.includes("@")){
        email=third;
        role=vals[3]||"user";
        tabs=vals[4]||"";
      }else{
        role=third||"user";
        tabs=(vals[3]||"").includes(",")?vals[3]:"";
      }
    }
    role=role==="admin"?"admin":"user";
    const rowTabs=_cleanTabs(tabs);
    const effectiveTabs=rowTabs.length?rowTabs:fallbackTabs;
    return{
      row:hasHeader?i+2:i+1,
      name,
      username,
      email,
      role,
      tabs:effectiveTabs,
      tabsSource:rowTabs.length?"row":"default",
    };
  })};
}
function _arr(v){return Array.isArray(v)?v:[];}
function _obj(v){return v&&typeof v==="object"&&!Array.isArray(v)?v:{};}
function _entries(v){return Object.entries(_obj(v));}
function _effectivePermissionText(u){
  const eff=_obj(u?.effective_permissions);
  const role=eff.role||u?.role||"user";
  const rawTabs=_arr(eff.tabs).length?_arr(eff.tabs):(u?.tabs||"");
  const tabs=eff.tabs==="__all__"?"all":_tabTokensLabel(rawTabs)||"default";
  const pages=_arr(eff.page_manager).map(_tabLabel).join(", ")||"-";
  const groups=_obj(eff.groups);
  const owner=_arr(groups.owner).length;
  const member=_arr(groups.member).length;
  const groupText=groups.all?"all":`owner ${owner} / member ${member}`;
  return `role ${role} · tabs ${tabs} · page ${pages} · devguide ${eff.devguide?"Y":"N"} · groups ${groupText}`;
}
const OK = statusPalette.ok;
const WARN = statusPalette.warn;
const BAD = statusPalette.bad;
const INFO = statusPalette.info;
const NEUTRAL = statusPalette.neutral;
const WHITE = "var(--bg-secondary)";
const SKY = chartPalette.series[13];
const SLATE = "rgba(107,114,128,0.95)";
const SILVER = "rgba(148,163,184,0.95)";
function notificationType(type){
  const raw=String(type||"info").trim().toLowerCase();
  return raw==="warn"?"warning":raw;
}
function notificationLabel(type){
  const normalized=notificationType(type);
  return normalized==="warning"?"! warning":normalized;
}
function notificationColor(type){
  const normalized=notificationType(type);
  return normalized==="approval"?WARN.fg:normalized==="message"?INFO.fg:normalized==="warning"?BAD.fg:SLATE;
}
function notificationBody(n){
  const payload=_obj(n?.payload);
  if(n?.event==="my_plan_actual_mismatch"&&payload.product&&payload.root_lot_id&&payload.column){
    const wafer=payload.wafer_id?` WF${payload.wafer_id}`:"";
    return `! ${payload.product}/${payload.root_lot_id}${wafer} ${payload.column}: [plan] ${payload.plan||""} → [actual] ${payload.actual||""}`;
  }
  return n?.body||"";
}

// v8.7.5: Admin 탭 전환 시 서브 패널에서 던진 에러가 페이지 전체를 마비시키지 않도록.
class TabBoundary extends Component{
  constructor(p){super(p);this.state={err:null};}
  static getDerivedStateFromError(e){return{err:e};}
  componentDidCatch(err,info){try{console.error("[admin tab boundary]",this.props.tabKey,err,info);}catch(_){}}
  componentDidUpdate(prev){if(prev.tabKey!==this.props.tabKey&&this.state.err)this.setState({err:null});}
  render(){
    if(this.state.err){
      return(<div style={{padding:"20px 24px",background:BAD.bg,border:`1px solid ${BAD.fg}66`,borderRadius:8,color:BAD.fg,fontSize:14}}>
        <div style={{fontWeight:700,marginBottom:6}}>⚠ 이 탭을 렌더하는 도중 오류가 발생했습니다.</div>
        <div style={{fontFamily:"monospace",fontSize:14,marginBottom:8,opacity:0.9}}>{String(this.state.err?.message||this.state.err)}</div>
        <Button variant="ghost" onClick={()=>this.setState({err:null})}>재시도</Button>
      </div>);
    }
    return this.props.children;
  }
}

function Gauge({label,pct,used,total,unit="GB"}){
  const color=pct>85?BAD.fg:pct>60?"rgba(251,191,36,0.95)":OK.fg;
  return(<div style={{background:"var(--bg-card)",borderRadius:8,padding:"12px 16px",border:"1px solid var(--border)"}}>
    <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}><span style={{fontSize:14,fontWeight:600}}>{label}</span><span style={{fontSize:14,fontWeight:700,color}}>{pct}%</span></div>
    <div style={{height:6,borderRadius:3,background:"var(--border)"}}><div style={{height:6,borderRadius:3,background:color,width:Math.min(pct,100)+"%",transition:"width 0.3s"}}/></div>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4}}>{used} / {total} {unit}</div>
  </div>);
}

const RESOURCE_LOG_LIMIT = 2100; // 7일 @ 5분 샘플(2016) + 여유.

function _resourceTimeMs(row){
  const epoch=Number(row?.ts_epoch||0);
  if(Number.isFinite(epoch)&&epoch>0)return epoch*1000;
  const parsed=Date.parse(row?.timestamp||"");
  return Number.isFinite(parsed)?parsed:0;
}

function _timeLabel(ms){
  if(!ms)return "-";
  try{
    return new Date(ms).toLocaleString("ko-KR",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});
  }catch(_){return "-";}
}

function ResourceSparkline({label,rows,metric,color,hours}){
  const expected=hours>=168?2016:288;
  const cutoff=Date.now()-hours*3600*1000;
  const cleaned=_arr(rows).map((r)=>({t:_resourceTimeMs(r),v:Number(r?.[metric]||0)})).filter((r)=>Number.isFinite(r.v)&&r.v>=0);
  let data=cleaned.filter((r)=>r.t&&r.t>=cutoff);
  if(data.length===0)data=cleaned.slice(-expected);
  const latest=data.length?data[data.length-1].v:0;
  const avg=data.length?data.reduce((a,b)=>a+b.v,0)/data.length:0;
  const max=data.length?Math.max(...data.map((d)=>d.v)):0;
  const W=360,H=126,pl=28,pr=10,pt=12,pb=22;
  const x=(i)=>data.length<=1?pl+(W-pl-pr)/2:pl+(i*(W-pl-pr))/(data.length-1);
  const y=(v)=>pt+(100-Math.max(0,Math.min(100,v)))*(H-pt-pb)/100;
  const points=data.map((d,i)=>`${x(i).toFixed(1)},${y(d.v).toFixed(1)}`).join(" ");
  const area=points?`M ${pl},${H-pb} L ${points} L ${W-pr},${H-pb} Z`:"";
  return(<div style={{background:"var(--bg-card)",borderRadius:8,padding:"10px 12px",border:"1px solid var(--border)",minWidth:220}}>
    <div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:6}}>
      <div style={{fontSize:14,fontWeight:700}}>{label}</div>
      <div style={{fontSize:14,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>
        <b style={{color}}>{latest.toFixed(1)}%</b> avg {avg.toFixed(1)}%
      </div>
    </div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{display:"block",overflow:"visible"}}>
      {[0,50,100].map((v)=><g key={v}>
        <line x1={pl} x2={W-pr} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeWidth="1" opacity={v===0?0.9:0.55}/>
        <text x={2} y={y(v)+3} fontSize="9" fill="var(--text-secondary)">{v}</text>
      </g>)}
      {area&&<path d={area} fill={color} opacity="0.14"/>}
      {points&&<polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"/>}
      {data.length===1&&<circle cx={x(0)} cy={y(latest)} r="3" fill={color}/>}
      {data.length>1&&<circle cx={x(data.length-1)} cy={y(latest)} r="3" fill={color}/>}
    </svg>
    <div style={{display:"flex",justifyContent:"space-between",gap:8,fontSize:14,color:"var(--text-secondary)"}}>
      <span>{data.length?`${_timeLabel(data[0].t)} - ${_timeLabel(data[data.length-1].t)}`:"수집 대기"}</span>
      <span>max {max.toFixed(1)}% · {data.length}건</span>
    </div>
  </div>);
}

const FARM_ANIM=`@keyframes fabFarm{0%{transform:translateX(0)}50%{transform:translateX(10px)}100%{transform:translateX(0)}}`;

export default function My_Admin({user}){
  const isAdmin=user?.role==="admin";
  const[users,setUsers]=useState([]);const[logs,setLogs]=useState([]);const[notifs,setNotifs]=useState([]);
  const[tab,setTab]=useState("notifs");const[dlHistory,setDlHistory]=useState([]);
  const[dlFilter,setDlFilter]=useState({q:"",source:""});
  const[sys,setSys]=useState({});const[resLog,setResLog]=useState([]);const[farmStatus,setFarmStatus]=useState({});
  const[resWindow,setResWindow]=useState("24h");
  const[loadBusy,setLoadBusy]=useState(false);
  const[qaReport,setQaReport]=useState({runs:[]});const[qaBusy,setQaBusy]=useState(false);const[qaMsg,setQaMsg]=useState("");
  const[editPerm,setEditPerm]=useState(null);const[permTabs,setPermTabs]=useState([]);
  const[bulkUsersText,setBulkUsersText]=useState("name\tusername\trole\n홍길동\thong\tuser");
  const[bulkUsersDefaultTabs,setBulkUsersDefaultTabs]=useState(BULK_DEFAULT_TABS);
  const[bulkUsersResult,setBulkUsersResult]=useState(null);
  const[bulkUsersBusy,setBulkUsersBusy]=useState(false);
  // v8.7.1: Admin Activity Log 필터
  const[logUsers,setLogUsers]=useState([]);
  const[logFilter,setLogFilter]=useState({username:"",action:"",tab:""});

  const[inquiry,setInquiry]=useState("");
  const sendInquiry=()=>{
    if(!inquiry.trim())return;
    sf("/api/admin/send-inquiry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",message:inquiry.trim()})}).then(()=>{setInquiry("");toast.ok("관리자에게 전송되었습니다.");load();}).catch(e=>toast.error(e.message));
  };
  const load=()=>{
    // Load ALL notifications (not just unread) so user can see history
    sf("/api/admin/all-notifications?username="+(user?.username||"")).then(d=>setNotifs(d.notifications||[])).catch(()=>{});
    if(isAdmin){
      sf("/api/admin/users").then(d=>setUsers(d.users||[])).catch(()=>{});
      reloadLogs();
      sf("/api/admin/logs/users").then(d=>setLogUsers(d.users||[])).catch(()=>{});
    } else {
      // User: load own logs and downloads
      sf("/api/admin/logs?limit=200&username="+(user?.username||"")).then(d=>setLogs(d.logs||[])).catch(()=>{});
      loadDl();
    }
  };
  // v8.7.1: Admin log 필터 적용 재로딩
  const reloadLogs=()=>{
    const q=new URLSearchParams({limit:"500"});
    if(logFilter.username)q.set("username",logFilter.username);
    if(logFilter.action)q.set("action",logFilter.action);
    if(logFilter.tab)q.set("tab",logFilter.tab);
    sf("/api/admin/logs?"+q.toString()).then(d=>setLogs(d.logs||[])).catch(()=>{});
  };
  useEffect(()=>{load();},[]);
  useEffect(()=>{if(isAdmin&&tab==="logs")reloadLogs();},[logFilter.username,logFilter.action,logFilter.tab]);
  // v8.2.0: Bell dismiss / external read → re-load this tab's notif list immediately
  useEffect(()=>{
    const onRefresh=()=>load();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>window.removeEventListener("hol:notif-refresh",onRefresh);
  },[user]);

  const loadDl=()=>{
    const url=isAdmin?"/api/filebrowser/download-history":"/api/filebrowser/download-history?username="+(user?.username||"");
    return sf(url).then(d=>setDlHistory(d.logs||[])).catch(()=>setDlHistory([]));
  };
  const loadQa=()=>{if(!isAdmin)return;sf("/api/admin/qa/report").then(d=>{setQaReport(d.report||{runs:[]});}).catch(e=>setQaMsg(e.message));};
  const loadSys=()=>{sf("/api/monitor/system").then(setSys).catch(()=>{});
    sf(`/api/monitor/resource-log?limit=${RESOURCE_LOG_LIMIT}`).then(d=>setResLog(d.logs||[])).catch(()=>{});
    sf("/api/monitor/farm-status").then(setFarmStatus).catch(()=>{});};
  const startPaverLoad=()=>{
    if(!isAdmin||loadBusy)return;
    setLoadBusy(true);
    sf("/api/monitor/load/start",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({duration_sec:180,target_pct:85,memory:true})
    }).then(d=>{setFarmStatus(d.state||{});loadSys();})
      .catch(e=>toast.error(e.message||"부하 시작 실패"))
      .finally(()=>setLoadBusy(false));
  };
  const stopPaverLoad=()=>{
    if(!isAdmin||loadBusy)return;
    setLoadBusy(true);
    sf("/api/monitor/load/stop",{method:"POST"})
      .then(d=>{setFarmStatus(d.state||{});loadSys();})
      .catch(e=>toast.error(e.message||"부하 중지 실패"))
      .finally(()=>setLoadBusy(false));
  };
  const action=(url,body)=>sf(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(()=>setTimeout(load,500));
  const resetPassword=(username)=>{
    if(!username)return;
    if(!confirm(`${username} 비밀번호를 초기화하고 설정된 메일 도메인으로 임시 비밀번호를 보낼까요?`))return;
    sf("/api/admin/reset-password",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username})})
      .then((r)=>{
        const to=_arr(r?.mail_to).join(", ");
        toast.ok(to?`임시 비밀번호를 발송했습니다: ${to}`:"임시 비밀번호를 발송했습니다.");
        setTimeout(load,500);
      })
      .catch(e=>toast.error("비번 초기화 실패: "+e.message));
  };
  const savePerm=()=>{if(!editPerm)return;sf("/api/admin/set-tabs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:editPerm,tabs:permTabs})}).then(()=>{setEditPerm(null);load();setTab("perms");});};
  const submitBulkUsers=()=>{
    const text=String(bulkUsersText||"").trim();
    if(!text){toast.warn("붙여넣을 사용자 행이 없습니다.");return;}
    if(!bulkDefaultTabs.length){toast.warn("기본 권한을 하나 이상 선택하세요.");return;}
    setBulkUsersBusy(true);
    sf("/api/admin/bulk-users",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({text,default_password:"1111",default_tabs:bulkDefaultTabs})
    }).then((d)=>{
      setBulkUsersResult(d||{});
      load();
    }).catch((e)=>toast.error(e.message)).finally(()=>setBulkUsersBusy(false));
  };
  const markRead=(ids)=>{if(!ids.length)return;sf("/api/admin/mark-read-batch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",ids})}).then(()=>{load();window.dispatchEvent(new CustomEvent("hol:notif-refresh"));}).catch(()=>{});};
  const toggleRead=(n)=>{if(!n.id)return;markRead([n.id]);};

  // Tabs differ by role
  // v8.4.3 단위기능 페이지 철학: AWS 설정은 FileBrowser 톱니로 이관 예정 (제거).
  // v8.8.14: page_admins / backup_sched / activity_dash 3개 탭 추가.
  //   - page_admins: 각 페이지의 "위임 admin" 을 유저에게 부여 (각 페이지에서 관리는 각 페이지가 수행한다는 철학).
  //   - backup_sched: 자동 백업 주기 + 예약 1회 백업 (서버 점검 전 대비).
  //   - activity_dash: 최근 활동 요약 + 기능별 사용 현황 (어떤 기능이 활성화되어 있는지 파악).
  const adminTabs=[["users","사용자"],["notifs","알림"],["perms","권한"],["page_admins","페이지 위임"],["groups","그룹"],["mail_cfg","메일 API"],["qa","QA 점검"],["logs","관리 로그"],["activity_dash","활동 대시보드"],["backup_sched","백업"],["downloads","다운로드"],["monitor","모니터"],["data_roots","데이터 루트"],["flowi_learning","Flow-i 학습"],["llm_cfg","LLM 설정"]];
  // v8.8.1: 일반 유저도 그룹 탭 사용 가능.
  const userTabs=[["notifs","알림"],["groups","그룹"],["logs","내 로그"],["downloads","내 다운로드"]];
  const tabs=isAdmin?adminTabs:userTabs;
  const tabItems=(tabs||[]).map(([k,l])=>({k,l,badge:k==="users"&&isAdmin?String(_arr(users).length):undefined}));
  const approvedUsers=_arr(users).filter(u=>u?.status==="approved").length;
  const pendingUsers=_arr(users).filter(u=>u?.status==="pending").length;
  // v9.1.x: downloads.jsonl 의 source 필드로 구분 표시 (없으면 파일 다운로드).
  const DL_SOURCES={filebrowser:{label:"파일 다운로드",tone:"accent"},reformatize:{label:"ET Index 다운로드",tone:"info"},reformatize_test:{label:"ET Index 테스트",tone:"warn"}};
  const combinedDownloads=[
    ..._arr(dlHistory).map((d)=>{
      const src=DL_SOURCES[d.source]||DL_SOURCES.filebrowser;
      return{
        timestamp:d.timestamp||"",
        source:src.label,
        sourceTone:src.tone,
        username:d.username||"-",
        target:d.product||"-",
        detail:d.sql||"-",
        aux:d.select_cols||"all",
        rows:d.rows?.toLocaleString?.()||d.rows||"-",
        size:d.size_mb?`${d.size_mb}MB`:"-",
      };
    }),
  ].sort((a,b)=>String(b.timestamp||"").localeCompare(String(a.timestamp||"")))
   .filter(d=>(!dlFilter.source||d.source===dlFilter.source)
     &&(!dlFilter.q||`${d.username} ${d.target} ${d.detail}`.toLowerCase().includes(dlFilter.q.toLowerCase())));
  const resourceChartHours=resWindow==="7d"?168:24;
  const bulkDefaultTabs=_cleanTabs(bulkUsersDefaultTabs);
  const bulkParsed=useMemo(()=>_parseBulkUsers(bulkUsersText,bulkDefaultTabs),[bulkUsersText,bulkDefaultTabs.join(",")]);
  const bulkPreviewRows=useMemo(()=>{
    const existing=new Set(_arr(users).map(u=>String(u?.username||"").trim().toLowerCase()).filter(Boolean));
    const seen=new Set();
    return _arr(bulkParsed.rows).map((row)=>{
      const key=String(row.username||"").trim().toLowerCase();
      let issue="";
      if(!key)issue="아이디 누락";
      else if(existing.has(key))issue="기존 사용자";
      else if(seen.has(key))issue="중복 행";
      else seen.add(key);
      return{...row,issue};
    });
  },[bulkParsed.rows,users]);
  const bulkCreatableCount=bulkPreviewRows.filter(row=>!row.issue).length;

  return(
    <div style={{padding:"24px 32px",background:"var(--bg-primary)",minHeight:"calc(100vh - 52px)",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif"}}>
      <PageHeader
        title={isAdmin?"관리자 콘솔":"내 관리"}
        subtitle={isAdmin?"사용자·권한·운영 설정을 한 곳에서 관리합니다.":"내 알림과 로그를 확인합니다."}
        right={<div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
          {isAdmin&&<Pill tone="accent" size="md">승인 {approvedUsers}</Pill>}
          {isAdmin&&pendingUsers>0&&<Pill tone="warn" size="md">대기 {pendingUsers}</Pill>}
          <Pill tone="neutral" size="md">{user?.username||"guest"}</Pill>
        </div>}
        style={{borderRadius:10,border:"1px solid var(--border)",marginBottom:14}}
      />
      <TabStrip
        items={tabItems}
        active={tab}
        onChange={(k)=>{
          setTab(k);
          try{ if(k==="downloads")loadDl(); }catch(e){console.warn("[admin tab] downloads loader threw",e);}
          try{ if(k==="monitor")loadSys(); }catch(e){console.warn("[admin tab] monitor loader threw",e);}
          try{ if(k==="qa")loadQa(); }catch(e){console.warn("[admin tab] qa loader threw",e);}
        }}
      />
      <div style={{height:16}} />
      <TabBoundary tabKey={tab}>

      {/* Users (admin only) — v8.8.27: 이름 컬럼 추가 + inline 편집. */}
      {tab==="users"&&isAdmin&&<div style={{display:"grid",gridTemplateColumns:"minmax(0,1.5fr) minmax(360px,0.9fr)",gap:16,alignItems:"start"}}>
        <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",overflow:"auto"}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
            <thead><tr>{["이름","아이디","역할","상태","탭","작업"].map(h=><th key={h} style={{textAlign:"left",padding:"10px 14px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:14,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
            <tbody>{(Array.isArray(users)?users:[]).map((u,i)=><tr key={i}>
              <td style={{padding:"6px 14px",borderBottom:"1px solid var(--border)",fontSize:14}}>
                <NameInlineEdit u={u} onSave={(nm)=>sf("/api/admin/set-name",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u.username,name:nm})}).then(load).catch(e=>toast.error(e.message))}/>
              </td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:14}}>{u.username}</td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>{u.role}</td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}><Pill tone={u.status==="approved"?"ok":"warn"}>{u.status}</Pill></td>
              <td title={u.tabs||""} style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",maxWidth:200,overflow:"hidden",textOverflow:"ellipsis"}}>{u.tabs==="__all__"?"__all__":_tabTokensLabel(u.tabs)||"default"}</td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>
                <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                  {u.status==="pending"&&<>
                    <Button variant="ghost" onClick={()=>action("/api/admin/approve",{username:u.username})} style={{color:"var(--ok,#22c55e)",border:"1px solid var(--ok,#22c55e)"}}>승인</Button>
                    <Button variant="danger" onClick={()=>action("/api/admin/reject",{username:u.username})}>거절</Button>
                  </>}
                  {u.status==="approved"&&u.role!=="admin"&&<>
                    <Button variant="ghost" onClick={()=>resetPassword(u.username)}>비번 초기화</Button>
                    <Button variant="danger" onClick={()=>{if(confirm("삭제하시겠습니까?"))action("/api/admin/delete-user",{username:u.username});}}>삭제</Button>
                    <Button variant="ghost" onClick={()=>{setEditPerm(u.username);setPermTabs(_tabsToArray(u.tabs));setTab("perms");}} style={{color:"var(--info,#3b82f6)",border:"1px solid var(--info,#3b82f6)"}}>권한</Button>
                  </>}
                </div>
              </td></tr>)}</tbody>
          </table>
        </div>
        <div style={{display:"flex",flexDirection:"column",gap:12}}>
          <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:12,marginBottom:10}}>
              <div>
                <div style={{fontSize:14,fontWeight:700}}>사용자 일괄 생성</div>
                <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4}}>엑셀에서 `name / username / role`을 붙여넣으면 표로 미리 확인하고, 선택한 기본 권한으로 생성합니다.</div>
              </div>
              <Button variant="primary" onClick={submitBulkUsers} disabled={bulkUsersBusy||!bulkCreatableCount}>{bulkUsersBusy?"생성 중...":`일괄 생성 ${bulkCreatableCount}건`}</Button>
            </div>
            {bulkUsersResult&&<Banner tone={_arr(bulkUsersResult.skipped).length?"warn":"ok"} style={{marginBottom:10}}>
              생성 {_arr(bulkUsersResult.created).length}건 / 건너뜀 {_arr(bulkUsersResult.skipped).length}건
            </Banner>}
            <div style={{display:"grid",gap:10}}>
              <label style={{display:"grid",gap:6}}>
                <span style={{display:"flex",alignItems:"center",gap:8,fontSize:14,color:"var(--text-secondary)"}}>
                  붙여넣기 원본
                  <Pill tone={bulkParsed.hasHeader?"info":"neutral"}>{bulkParsed.hasHeader?"헤더 인식":"헤더 없음"}</Pill>
                </span>
                <textarea
                  value={bulkUsersText}
                  onChange={(e)=>setBulkUsersText(e.target.value)}
                  spellCheck={false}
                  style={{width:"100%",minHeight:78,resize:"vertical",padding:"10px 12px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,lineHeight:1.45,fontFamily:"ui-monospace, SFMono-Regular, Menlo, monospace",outline:"none"}}
                />
              </label>
              <div style={{border:"1px solid var(--border)",borderRadius:8,overflow:"hidden",background:"var(--bg-primary)"}}>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,padding:"8px 10px",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)"}}>
                  <div style={{fontSize:14,fontWeight:700,color:"var(--text-secondary)"}}>기본 권한</div>
                  <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                    <Button variant="subtle" onClick={()=>setBulkUsersDefaultTabs(BULK_DEFAULT_TABS)}>기본값</Button>
                    <Button variant="subtle" onClick={()=>setBulkUsersDefaultTabs(ALL_TABS)}>전체</Button>
                  </div>
                </div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(128px,1fr))",gap:6,padding:10}}>
                  {ALL_TABS.map(t=><label key={t} style={{display:"flex",alignItems:"center",gap:7,fontSize:14,color:"var(--text-secondary)",cursor:"pointer",minWidth:0}}>
                    <input
                      type="checkbox"
                      checked={bulkDefaultTabs.includes(t)}
                      onChange={(e)=>{
                        const next=e.target.checked?[...bulkDefaultTabs,t]:bulkDefaultTabs.filter(x=>x!==t);
                        setBulkUsersDefaultTabs(ALL_TABS.filter(x=>next.includes(x)));
                      }}
                      style={{accentColor:"var(--accent)",flexShrink:0}}
                    />
                    <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t}</span>
                  </label>)}
                </div>
              </div>
              <div style={{border:"1px solid var(--border)",borderRadius:8,overflow:"auto",background:"var(--bg-primary)",maxHeight:260}}>
                <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
                  <thead><tr>{["행","이름","아이디","역할","적용 권한","상태"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",borderBottom:"1px solid var(--border)",whiteSpace:"nowrap",position:"sticky",top:0,zIndex:1}}>{h}</th>)}</tr></thead>
                  <tbody>
                    {!bulkPreviewRows.length&&<tr><td colSpan={6} style={{padding:24,textAlign:"center",color:"var(--text-secondary)"}}>붙여넣은 사용자 행이 없습니다.</td></tr>}
                    {bulkPreviewRows.map((row,idx)=><tr key={idx}>
                      <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{row.row}</td>
                      <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)"}}>{row.name||"-"}</td>
                      <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{row.username||"-"}</td>
                      <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)"}}><Pill tone={row.role==="admin"?"warn":"neutral"}>{row.role}</Pill></td>
                      <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",minWidth:180}}>
                        {row.role==="admin"?<Pill tone="warn">관리자 전체</Pill>:<div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                          <Pill tone={row.tabsSource==="row"?"info":"accent"}>{row.tabsSource==="row"?"행 지정":"기본"}</Pill>
                          {row.tabs.slice(0,3).map(t=><Pill key={t} tone="neutral">{t}</Pill>)}
                          {row.tabs.length>3&&<Pill tone="neutral">+{row.tabs.length-3}</Pill>}
                        </div>}
                      </td>
                      <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)"}}><Pill tone={row.issue?"warn":"ok"}>{row.issue||"생성 예정"}</Pill></td>
                    </tr>)}
                  </tbody>
                </table>
              </div>
            </div>
            {bulkUsersResult&&<div style={{border:"1px solid var(--border)",borderRadius:8,overflow:"auto",marginTop:10,background:"var(--bg-primary)",maxHeight:180}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
                <thead><tr>{["결과","행","아이디","상세"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 10px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",borderBottom:"1px solid var(--border)",whiteSpace:"nowrap",position:"sticky",top:0,zIndex:1}}>{h}</th>)}</tr></thead>
                <tbody>
                  {!_arr(bulkUsersResult.created).length&&!_arr(bulkUsersResult.skipped).length&&<tr><td colSpan={4} style={{padding:18,textAlign:"center",color:"var(--text-secondary)"}}>결과 없음</td></tr>}
                  {_arr(bulkUsersResult.created).map((row,idx)=><tr key={`c-${idx}`}>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)"}}><Pill tone="ok">생성</Pill></td>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>-</td>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{row.username}</td>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{row.role} · {row.name||"-"}</td>
                  </tr>)}
                  {_arr(bulkUsersResult.skipped).map((row,idx)=><tr key={`s-${idx}`}>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)"}}><Pill tone="warn">건너뜀</Pill></td>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{row.row}</td>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{row.username||"-"}</td>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{row.reason}</td>
                  </tr>)}
                </tbody>
              </table>
            </div>}
          </div>
          <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
            <div style={{fontSize:14,fontWeight:700,marginBottom:8}}>복붙 예시</div>
            <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:8}}>헤더가 있으면 자동 인식합니다. 헤더 없이 붙일 때도 `name, username, role` 순서로 봅니다.</div>
            <pre style={{margin:0,whiteSpace:"pre-wrap",fontSize:14,lineHeight:1.55,color:"var(--text-secondary)",fontFamily:"ui-monospace, SFMono-Regular, Menlo, monospace"}}>{`name\tusername\trole
홍길동\thong\tuser
김관리\tkimadmin\tadmin`}</pre>
          </div>
        </div>
      </div>}

      {/* Permissions (admin only) */}
      {tab==="perms"&&isAdmin&&<div>
        {/* O/X Permission Table */}
        {!editPerm&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",overflow:"auto",marginBottom:16}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
            <thead><tr>
              <th style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",position:"sticky",left:0,zIndex:1}}>사용자</th>
              <th style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",minWidth:340}}>실제 적용 권한</th>
              {ALL_TABS.map(t=><th key={t} title={t} style={{textAlign:"center",padding:"8px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{_tabLabel(t)}</th>)}
              <th style={{textAlign:"center",padding:"8px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}}></th>
            </tr></thead>
            <tbody>{_arr(users).filter(u=>u?.role!=="admin"&&u?.status==="approved").map((u,i)=>{
              const ut=_tabsToArray(u.tabs);
              return(<tr key={i}>
                <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontWeight:600,position:"sticky",left:0,background:"var(--bg-secondary)",zIndex:1}}>{u.username}</td>
                <td title={_effectivePermissionText(u)} style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)",fontSize:13,maxWidth:520,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{_effectivePermissionText(u)}</td>
                {ALL_TABS.map(t=>{const mark=_tabCellMark(ut,t);return(<td key={t} style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
                  <span title={mark==="△"?_tabTokensLabel(_tabTokensFor(ut,t)):""} style={{fontSize:14,color:mark==="O"?"var(--ok,#22c55e)":(mark==="△"?"var(--warn,#f59e0b)":"var(--bad,#ef4444)"),fontWeight:700}}>{mark}</span>
                </td>);})}
                <td style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
                  <span onClick={()=>{setEditPerm(u.username);setPermTabs(ut);}} style={{color:"var(--info,#3b82f6)",cursor:"pointer",fontSize:14}}>편집</span>
                </td>
              </tr>);})}</tbody>
          </table>
        </div>}
        {/* Edit single user permissions */}
        {editPerm&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:400}}>
          <div style={{fontSize:14,fontWeight:700,marginBottom:12}}>권한: {editPerm}</div>
          {ALL_TABS.map(t=>(<div key={t}>
            <label title={t} style={{display:"flex",alignItems:"center",gap:8,padding:"6px 0",fontSize:14,cursor:"pointer"}}>
              <input type="checkbox" checked={_mainTabChecked(permTabs,t)} onChange={e=>setPermTabs(_toggleMainTab(permTabs,t,e.target.checked))}/>{_tabLabel(t)}
            </label>
            {/* v9.1.x: 소탭 단위 권한 */}
            {SUB_TABS[t]&&_mainTabChecked(permTabs,t)&&<div style={{display:"flex",flexWrap:"wrap",gap:10,padding:"0 0 6px 24px"}}>
              {SUB_TABS[t].map(s=>(<label key={s.key} style={{display:"flex",alignItems:"center",gap:5,fontSize:13,color:"var(--text-secondary)",cursor:"pointer"}}>
                <input type="checkbox" checked={_subTabChecked(permTabs,t,s.key)} onChange={e=>setPermTabs(_toggleSubTab(permTabs,t,s.key,e.target.checked))}/>{s.label}
              </label>))}
            </div>}
          </div>))}
          <div style={{display:"flex",gap:8,marginTop:12}}>
            <Button variant="primary" onClick={savePerm} style={{padding:"8px 20px"}}>저장</Button>
            <Button variant="subtle" onClick={()=>{setEditPerm(null);}} style={{padding:"8px 16px"}}>취소</Button>
          </div></div>}
      </div>}

      {/* Notifications (everyone) */}
      {tab==="notifs"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
        {/* User inquiry box */}
        {!isAdmin&&<div style={{marginBottom:14,padding:"12px 14px",background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)"}}>
          <div style={{fontSize:14,fontWeight:600,color:"var(--accent)",marginBottom:6}}>관리자 문의</div>
          <div style={{display:"flex",gap:8}}>
            <input value={inquiry} onChange={e=>setInquiry(e.target.value)} placeholder="관리자에게 보낼 메시지를 입력하세요..."
              onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;sendInquiry();}}}
              style={{flex:1,padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:14,outline:"none"}}/>
            <Button variant="primary" onClick={sendInquiry} disabled={!inquiry.trim()} style={{padding:"8px 16px",fontSize:14}}>전송</Button>
          </div>
        </div>}
        {/* Actions bar */}
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>읽지 않음 {_arr(notifs).filter(n=>!n?.read).length} / 전체 {_arr(notifs).length}</span>
          {_arr(notifs).some(n=>!n?.read)&&<button onClick={()=>markRead(_arr(notifs).filter(n=>!n?.read).map(n=>n.id).filter(Boolean))} style={{padding:"4px 14px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:14,fontWeight:600,cursor:"pointer"}}>모두 읽음으로 표시</button>}
        </div>
        <div style={{maxHeight:460,overflowY:"auto"}}>
        {_arr(notifs).length===0&&<div style={{color:"var(--text-secondary)",fontSize:14,padding:20,textAlign:"center"}}>알림 없음</div>}
        {[..._arr(notifs)].reverse().map((n,i)=>(
          <div key={n.id||i} style={{borderBottom:"1px solid var(--border)",fontSize:14,display:"flex",gap:8,alignItems:"flex-start",borderRadius:4,padding:"8px 6px",opacity:n.read?0.5:1}}>
            <input type="checkbox" checked={!!n.read} onChange={()=>{if(!n.read)toggleRead(n);}} disabled={!!n.read} title={n.read?"읽음":"읽음으로 표시"} style={{marginTop:2,accentColor:OK.fg,flexShrink:0,cursor:n.read?"default":"pointer"}}/>
            <div style={{flex:1}}>
              <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:4}}>
                <span style={{fontSize:14,padding:"2px 6px",borderRadius:3,fontWeight:700,color:WHITE,background:notificationColor(n.type),textTransform:"uppercase"}}>{notificationLabel(n.type)}</span>
                <span style={{fontWeight:n.read?400:600}}>{n.title}</span>
                <span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:"auto"}}>{n.timestamp?.slice(0,16)}</span>
              </div>
              <div style={{color:"var(--text-secondary)",fontSize:14,paddingLeft:4}}>{notificationBody(n)}</div>
            </div>
          </div>))}
        </div>
      </div>}

      {/* Admin Log (v8.7.1) — 유저별/액션별 감사 로그 */}
      {tab==="qa"&&isAdmin&&<div style={{display:"grid",gap:16}}>
        <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:12,flexWrap:"wrap"}}>
            <div>
              <div style={{fontSize:14,fontWeight:700}}>자동 QA 리포트</div>
              <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4}}>user/admin 페르소나, edge case, 차트 schema, rule-based UX score 결과를 최근 10회까지 보관합니다.</div>
            </div>
            <div style={{display:"flex",gap:8,alignItems:"center"}}>
              <Button variant="ghost" onClick={loadQa}>새로고침</Button>
              <Button variant="primary" disabled={qaBusy} onClick={()=>{
                setQaBusy(true);setQaMsg("");
                sf("/api/admin/qa/trigger",{method:"POST"}).then((d)=>{setQaMsg(`QA 실행 완료 (code=${d.code})`);loadQa();}).catch((e)=>setQaMsg(e.message)).finally(()=>setQaBusy(false));
              }}>{qaBusy?"실행 중...":"QA 재실행"}</Button>
            </div>
          </div>
          {qaMsg&&<Banner tone={qaMsg.includes("완료")?"ok":"warn"} style={{marginTop:12}}>{qaMsg}</Banner>}
        </div>
        <div style={{display:"grid",gridTemplateColumns:"minmax(0,1.1fr) minmax(320px,0.9fr)",gap:16}}>
          <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
            <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>최근 실행</div>
            <div style={{maxHeight:520,overflow:"auto",display:"grid",gap:10}}>
              {_arr(qaReport.runs).length===0&&<div style={{fontSize:14,color:"var(--text-secondary)"}}>리포트가 없습니다. QA 재실행으로 첫 결과를 생성하세요.</div>}
              {_arr(qaReport.runs).map((run,idx)=>(
                <div key={idx} style={{padding:12,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
                  <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:8,flexWrap:"wrap"}}>
                    <Pill tone={(run.issues||[]).length?"warn":"ok"}>{(run.issues||[]).length?`issues ${(run.issues||[]).length}`:"clean"}</Pill>
                    <span style={{fontSize:14,fontFamily:"monospace",color:"var(--text-secondary)"}}>{(run.run_at||"").replace("T"," ").slice(0,19)}</span>
                    <span style={{fontSize:14,fontFamily:"monospace",color:"var(--text-secondary)"}}>duration {run.duration_sec||0}s</span>
                  </div>
                  <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,fontSize:14}}>
                    <div style={{padding:10,borderRadius:8,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
                      <div style={{fontWeight:700,marginBottom:6}}>Persona</div>
                      <div>admin: {(run.personas?.admin?.pass)||0} pass / {(run.personas?.admin?.fail)||0} fail</div>
                      <div>user: {(run.personas?.user?.pass)||0} pass / {(run.personas?.user?.fail)||0} fail</div>
                    </div>
                    <div style={{padding:10,borderRadius:8,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
                      <div style={{fontWeight:700,marginBottom:6}}>Extra</div>
                      <div>edge cases: {_arr(run.edge_cases).filter(x=>x?.ok).length}/{_arr(run.edge_cases).length}</div>
                      <div>charts: {_arr(run.charts).filter(x=>x?.ok).length}/{_arr(run.charts).length}</div>
                    </div>
                  </div>
                  {!!_arr(run.issues).length&&<div style={{marginTop:10,display:"grid",gap:6}}>
                    {_arr(run.issues).slice(0,5).map((issue,i)=><div key={i} style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{issue.area}: {issue.desc}</div>)}
                  </div>}
                </div>
              ))}
            </div>
          </div>
          <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
            <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>최신 UX Score</div>
            {_arr(qaReport.runs?.[0]?.ux_scores?.pages).length===0&&<div style={{fontSize:14,color:"var(--text-secondary)"}}>UX score 없음</div>}
            <div style={{display:"grid",gap:8}}>
              {_arr(qaReport.runs?.[0]?.ux_scores?.pages).map((page,idx)=>(
                <div key={idx} style={{padding:10,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                    <span style={{fontFamily:"monospace",fontWeight:700}}>{page.page}</span>
                    <Pill tone={page.score>=4?"ok":page.score>=3?"warn":"bad"}>{page.score}/5</Pill>
                  </div>
                  {!!_arr(page.notes).length&&<div style={{marginTop:6,fontSize:14,color:"var(--text-secondary)"}}>{_arr(page.notes).join(" · ")}</div>}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>}

      {/* Admin Log (v8.7.1) — 유저별/액션별 감사 로그 */}
      {tab==="logs"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
        {isAdmin&&<div style={{display:"flex",gap:10,marginBottom:12,flexWrap:"wrap",alignItems:"center"}}>
          <span style={{fontSize:14,fontWeight:700,color:"var(--accent)"}}>📋 Admin Activity Log</span>
          <select value={logFilter.username} onChange={e=>setLogFilter({...logFilter,username:e.target.value})}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,minWidth:160}}>
            <option value="">-- 유저 전체 --</option>
            {_arr(logUsers).map(u=><option key={u.username} value={u.username}>{u.username} ({u.count})</option>)}
          </select>
          <input placeholder="action 필터 (예: inform, login)" value={logFilter.action}
            onChange={e=>setLogFilter({...logFilter,action:e.target.value})}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,width:200}}/>
          <input placeholder="tab 필터 (inform/calendar/...)" value={logFilter.tab}
            onChange={e=>setLogFilter({...logFilter,tab:e.target.value})}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,width:170}}/>
          {(logFilter.username||logFilter.action||logFilter.tab)&&
            <button onClick={()=>setLogFilter({username:"",action:"",tab:""})}
              style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:BAD.fg,fontSize:14,cursor:"pointer"}}>× 초기화</button>}
          <button onClick={reloadLogs}
            style={{padding:"6px 12px",borderRadius:5,border:"none",background:"var(--accent)",color:WHITE,fontSize:14,fontWeight:600,cursor:"pointer"}}>↻ 새로고침</button>
          <span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:"auto"}}>{_arr(logs).length}건</span>
        </div>}
        <div style={{maxHeight:540,overflowY:"auto",border:"1px solid var(--border)",borderRadius:6}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
            <thead style={{position:"sticky",top:0,background:"var(--bg-tertiary)",zIndex:1}}>
              <tr>{["시간","유저","탭","동작","상세"].map(h=>
                <th key={h} style={{textAlign:"left",padding:"8px 12px",color:"var(--text-secondary)",fontSize:14,borderBottom:"1px solid var(--border)",whiteSpace:"nowrap"}}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {_arr(logs).length===0&&<tr><td colSpan={5} style={{padding:20,textAlign:"center",color:"var(--text-secondary)"}}>로그 없음</td></tr>}
              {[..._arr(logs)].reverse().map((l,i)=>(
                <tr key={i} style={{borderBottom:"1px solid var(--border)"}}>
                  <td style={{padding:"6px 12px",fontFamily:"monospace",fontSize:14,color:"var(--accent)",whiteSpace:"nowrap"}}>{l.timestamp?.slice(0,19)?.replace("T"," ")}</td>
                  <td style={{padding:"6px 12px",fontWeight:600}}>{l.username||"-"}</td>
                  <td style={{padding:"6px 12px",fontSize:14,color:"var(--text-secondary)"}}>{l.tab?<span style={{padding:"2px 8px",borderRadius:999,background:"var(--bg-hover)",fontSize:14}}>{l.tab}</span>:"-"}</td>
                  <td style={{padding:"6px 12px",fontFamily:"monospace",fontSize:14}}>{l.action||"-"}</td>
                  <td style={{padding:"6px 12px",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",maxWidth:420,overflow:"hidden",textOverflow:"ellipsis"}} title={l.detail||""}>{l.detail||""}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </div>}

      {/* Downloads — v9.1.x: 구분(파일 다운로드/리포마타이즈) + 사용자·대상 검색 필터 */}
      {tab==="downloads"&&<div>
        <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:10,flexWrap:"wrap"}}>
          <select value={dlFilter.source} onChange={e=>setDlFilter(f=>({...f,source:e.target.value}))}
            style={{padding:"6px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13}}>
            <option value="">전체 구분</option>
            <option value="파일 다운로드">파일 다운로드</option>
            <option value="ET Index 다운로드">ET Index 다운로드</option>
            <option value="ET Index 테스트">ET Index 테스트</option>
          </select>
          <input value={dlFilter.q} onChange={e=>setDlFilter(f=>({...f,q:e.target.value}))}
            placeholder="사용자·대상·상세 검색"
            style={{padding:"6px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,minWidth:220}}/>
          <span style={{fontSize:13,color:"var(--text-secondary)"}}>{combinedDownloads.length.toLocaleString()}건</span>
        </div>
        <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",overflow:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
          <thead><tr>{["시간","구분","사용자","대상","상세","컬럼","행","크기"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:14,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
          <tbody>
            {combinedDownloads.length===0&&<tr><td colSpan={8} style={{padding:20,textAlign:"center",color:"var(--text-secondary)"}}>다운로드 이력 없음</td></tr>}
            {combinedDownloads.map((d,i)=><tr key={i}>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}}>{String(d.timestamp||"").slice(0,19).replace("T"," ")}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>
                <Pill tone={d.sourceTone}>{d.source}</Pill>
              </td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{d.username}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{d.target}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:14,maxWidth:180,overflow:"hidden",textOverflow:"ellipsis"}} title={d.detail||""}>{d.detail||"-"}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:14,maxWidth:140,overflow:"hidden",textOverflow:"ellipsis",color:"var(--text-secondary)"}} title={d.aux||""}>{d.aux||"-"}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{d.rows}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{d.size}</td>
            </tr>)}
          </tbody></table></div></div>}

      {/* Monitor (admin only) — v8.8.27: BE psutil 필드명에 맞춰 재매핑.
           구 FE: sys.cpu_pct/mem_pct/disk_pct/mem_used/mem_total/disk_used/disk_total
           신 BE: cpu_percent/memory_percent/disk_percent/memory_used_gb/memory_total_gb/disk_used_gb/disk_total_gb
           필드명 불일치로 사용량이 전부 0 으로 표시되던 문제. */}
      {tab==="monitor"&&isAdmin&&<div>
        <style>{FARM_ANIM}</style>
        {farmStatus.farming&&<div style={{background:WARN.bg,border:`1px solid ${WARN.fg}`,borderRadius:10,padding:16,marginBottom:16,display:"flex",alignItems:"center",gap:16}}>
          <div style={{animation:"fabFarm 1s ease-in-out infinite",fontSize:32}}>🧑‍🌾</div>
          <div><div style={{fontSize:14,fontWeight:700,color:WARN.fg}}>FAB-i 가 farming 중...</div>
            <div style={{fontSize:14,color:"var(--text-secondary)"}}>리소스를 활성 상태로 유지합니다 · {farmStatus.load_mode||"auto"} · MEM hold {farmStatus.load_memory_allocated_mb||0}MB</div></div>
        </div>}
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,marginBottom:12,padding:"10px 12px",border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-secondary)"}}>
          <div>
            <div style={{fontSize:14,fontWeight:800,color:"var(--text-primary)"}}>보도블럭 갈기</div>
            <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:2}}>Admin 수동 부하 테스트. 목표 85%, 최대 3분, 사용자 활동 감지 시 자동 중단됩니다.</div>
          </div>
          <div style={{display:"flex",gap:8,alignItems:"center",flexShrink:0}}>
            <Button variant="subtle" onClick={loadSys}>새로고침</Button>
            {farmStatus.load_active||farmStatus.farming
              ? <Button variant="danger" disabled={loadBusy} onClick={stopPaverLoad}>중지</Button>
              : <Button variant="primary" disabled={loadBusy} onClick={startPaverLoad}>보도블럭 갈기</Button>}
          </div>
        </div>
        {sys && sys.psutil === false && <div style={{marginBottom:12,padding:"8px 12px",border:`1px solid ${WARN.fg}`,background:WARN.bg,borderRadius:6,color:WARN.fg,fontSize:14}}>
          ⚠ psutil 미설치 (폴백 모드: Linux /proc/statvfs). 정확한 측정을 원하면 서버에 <code>pip install psutil</code>.
        </div>}
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:12,marginBottom:20}}>
          <Gauge label="CPU" pct={Math.round(sys.cpu_percent||0)} used={`${(sys.cpu_percent||0).toFixed(1)}%`} total="100%" unit=""/>
          <Gauge label="메모리" pct={Math.round(sys.memory_percent||0)} used={(sys.memory_used_gb||0).toFixed(1)} total={(sys.memory_total_gb||0).toFixed(1)} unit="GB"/>
          <Gauge label="디스크" pct={Math.round(sys.disk_percent||0)} used={(sys.disk_used_gb||0).toFixed(0)} total={(sys.disk_total_gb||0).toFixed(0)} unit="GB"/>
        </div>
        {(sys.process_cpu_budget_cores||sys.memory_source)&&<div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center",marginTop:-8,marginBottom:16,fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>
          <span>Flow CPU {Number(sys.process_cpu_cores||0).toFixed(2)} / {Number(sys.process_cpu_guard_cores||sys.process_cpu_budget_cores||0).toFixed(2)} cores{sys.process_cpu_over_limit?" · over":""}</span>
          <span>MEM source {sys.memory_source||sys.system_memory_source||"-"}{sys.system_memory_raw_total_gb&&sys.system_memory_raw_total_gb!==sys.system_memory_total_gb?` · raw ${Number(sys.system_memory_raw_total_gb||0).toFixed(1)}GB`:""}{Number(sys.system_memory_cache_reclaimable_gb||0)>0.05?` · cache ${Number(sys.system_memory_cache_reclaimable_gb||0).toFixed(1)}GB (회수가능, 사용량 제외)`:""}</span>
        </div>}
        {/* v9.2.1: 프로세스 메모리 상세 — RSS/PSS/USS 구분 표시 */}
        {(sys.process_rss_gb>0)&&<div style={{background:"var(--bg-card)",borderRadius:8,border:"1px solid var(--border)",padding:"10px 14px",marginTop:-8,marginBottom:16}}>
          <div style={{fontSize:14,fontWeight:700,marginBottom:6}}>프로세스 메모리 (Flow 서버)</div>
          <div style={{display:"flex",gap:16,flexWrap:"wrap",fontSize:14,fontFamily:"monospace",color:"var(--text-secondary)"}}>
            {sys.process_uss_gb>0&&<span title="USS (Unique Set Size): 이 프로세스만의 전용 메모리. 프로세스 종료 시 실제 해제되는 양.">USS <b style={{color:"var(--text-primary)"}}>{Number(sys.process_uss_gb).toFixed(2)}</b>GB</span>}
            {sys.process_pss_gb>0&&<span title="PSS (Proportional Set Size): 공유 라이브러리를 프로세스 수로 나눠 계산. 실제 사용량에 가장 가까운 지표.">PSS <b style={{color:"var(--text-primary)"}}>{Number(sys.process_pss_gb).toFixed(2)}</b>GB</span>}
            <span title="RSS (Resident Set Size): 공유 라이브러리 + mmap 파일 캐시 포함. 실제보다 크게 표시됨.">RSS <b style={{color:sys.process_pss_gb>0?"var(--text-secondary)":"var(--text-primary)"}}>{Number(sys.process_rss_gb).toFixed(2)}</b>GB{sys.process_pss_gb>0?" (참고)":""}</span>
            {sys.process_memory_limit_gb>0&&<span>limit {Number(sys.process_memory_limit_gb).toFixed(1)}GB ({Number(sys.process_memory_limit_percent||0).toFixed(0)}%)</span>}
          </div>
          {sys.process_pss_gb>0&&sys.process_rss_gb>0&&(sys.process_rss_gb-sys.process_pss_gb)>0.05&&<div style={{fontSize:12,color:"var(--text-secondary)",marginTop:4}}>
            RSS와 PSS 차이 {(sys.process_rss_gb-sys.process_pss_gb).toFixed(2)}GB = 공유 라이브러리·mmap 파일 중복 계상분
          </div>}
        </div>}
        <div style={{marginBottom:16}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,marginBottom:8}}>
            <div style={{fontSize:14,fontWeight:700}}>리소스 차트</div>
            <TabStrip
              items={[{k:"24h",l:"24시간"},{k:"7d",l:"7일"}]}
              active={resWindow}
              onChange={setResWindow}
            />
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:12}}>
            <ResourceSparkline label="CPU" rows={resLog} metric="cpu_percent" color={chartPalette.series[4]} hours={resourceChartHours}/>
            <ResourceSparkline label="MEM" rows={resLog} metric="memory_percent" color={chartPalette.series[3]} hours={resourceChartHours}/>
            <ResourceSparkline label="DISK" rows={resLog} metric="disk_percent" color={chartPalette.series[1]} hours={resourceChartHours}/>
          </div>
        </div>
        {resLog.length>0&&<div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:12,maxHeight:300,overflow:"auto"}}>
          <div style={{fontSize:14,fontWeight:600,marginBottom:8}}>리소스 로그 ({resLog.length}건, 최근 50 표시)</div>
          <div style={{fontSize:14,fontFamily:"monospace"}}>{[..._arr(resLog)].reverse().slice(0,50).map((r,i)=><div key={i} style={{padding:"2px 0",color:"var(--text-secondary)"}}>{(r.timestamp||"").slice(11,19)} CPU:{(r.cpu_percent||0).toFixed(1)}% Mem:{(r.memory_percent||0).toFixed(1)}% Disk:{(r.disk_percent||0).toFixed(1)}%</div>)}</div>
        </div>}
        {_arr(resLog).length===0&&<div style={{fontSize:14,color:"var(--text-secondary)",padding:"10px 0"}}>리소스 로그 수집 중 (5분 간격). 잠시 후 새로고침해주세요.</div>}
      </div>}

      {/* Groups (admin only) — v8.5.0 */}
      {tab==="groups"&&<GroupsPanel allUsers={users} isAdmin={isAdmin} currentUser={user}/>}

      {/* Categories (admin only) */}
      {tab==="categories"&&isAdmin&&<CategoryPanel/>}

      {/* Catalog (admin only) — matching tables + product config + S3 sync */}
      {tab==="catalog"&&isAdmin&&<CatalogPanel/>}

      {/* AWS Config (admin only) */}
      {tab==="aws"&&isAdmin&&<AWSPanel user={user}/>}

      {/* Messages sub-tab removed in v8.3.1 — functionality moved to Home Contact 섹션 */}

      {/* Data Roots (admin only) — v8.3.0: soft-landing env abstraction */}
      {tab==="data_roots"&&isAdmin&&<DataRootsPanel/>}

      {tab==="flowi_learning"&&isAdmin&&<FlowiLearningPanel/>}

      {/* v9.2.x: LLM 연결/설정 — 에이전트 탭에서 이관 (admin only) */}
      {tab==="llm_cfg"&&isAdmin&&<LlmTab isAdmin={isAdmin}/>}

      {/* v8.7.2: Mail API (admin only) */}
      {tab==="mail_cfg"&&isAdmin&&<MailCfgPanel/>}

      {/* v8.8.14: Per-page admin delegation (admin only) */}
      {tab==="page_admins"&&isAdmin&&<PageAdminsPanel users={users}/>}

      {/* v8.8.14: Backup schedule + one-off (admin only) */}
      {tab==="backup_sched"&&isAdmin&&<BackupSchedulePanel/>}

      {/* v8.8.14: Activity dashboard (admin only) */}
      {tab==="activity_dash"&&isAdmin&&<ActivityDashboardPanel/>}
      </TabBoundary>
    </div>);
}

// ── v8.8.14: Per-page admin delegation ──
// 유저별로 "이 페이지의 관리 권한을 위임한다" 를 체크박스로 토글. admin 유저는 global 이라 배제.
// 저장 즉시 /api/admin/page-admins 로 POST.
// v9.0.3: 메시지 기능은 "문의함" 용어로 정리.
const PAGE_IDS=[
  ["filebrowser","파일탐색기"],["dashboard","대시보드"],["splittable","스플릿 테이블"],
  ["tracker","이슈 추적"],["inform","인폼 로그"],["meeting","회의관리"],["calendar","변경점 관리"],
  ["tablemap","테이블 맵"],
  // v9.3.x: devguide 는 admin 전용 — 페이지 위임 대상에서 제외.
  ["groups","그룹"],["messages","문의함"],["diagnosis","에이전트"],
];
const PAGE_PRESETS=[
  {key:"read",label:"조회만",pages:[]},
  {key:"ops",label:"운영관리",pages:["filebrowser","splittable","inform","tracker","calendar","meeting"]},
  {key:"all",label:"전체관리",pages:CANONICAL_PAGE_IDS},
];

function PageAdminsPanel({users}){
  const [pa,setPa]=useState({});
  const [msg,setMsg]=useState("");
  const [busy,setBusy]=useState(false);
  const reload=()=>{
    sf("/api/admin/page-admins")
      .then((paResp)=>{setPa(paResp.page_admins||{});})
      .catch(e=>setMsg("로드 오류: "+e.message));
  };
  useEffect(()=>{reload();},[]);
  // v8.8.21: 행=유저 / 열=페이지 매트릭스. admin 유저는 자동 전체 허용 (체크 disabled).
  // v8.8.28: Array.isArray 가드 — users 가 object 로 떨어져도 PageAdminsPanel 크래시 방지.
  const approved=(Array.isArray(users)?users:[]).filter(u=>u&&u.status==="approved");
  const isFullAdmin=(u)=>u.role==="admin" || ["admin","hol"].includes((u.username||"").toLowerCase());
  const toggle=(pageId,username)=>{
    pageId=_canonicalPageId(pageId);
    const cur=new Set(pa[pageId]||[]);
    if(cur.has(username))cur.delete(username);else cur.add(username);
    const next={...pa,[pageId]:Array.from(cur).sort()};
    if(next[pageId].length===0)delete next[pageId];
    setBusy(true);setMsg("");
    sf("/api/admin/page-admins",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({page_id:pageId,usernames:next[pageId]||[]})})
      .then(()=>{setPa(next);setMsg("✔ "+pageId+" 저장");setBusy(false);setTimeout(()=>setMsg(""),2000);})
      .catch(e=>{setMsg("오류: "+e.message);setBusy(false);});
  };
  const applyPreset=(username,preset)=>{
    const pages=new Set((preset.pages||[]).map(_canonicalPageId));
    const next={...pa};
    for(const [pid] of PAGE_IDS){
      const key=_canonicalPageId(pid);
      const cur=new Set(next[key]||[]);
      if(pages.has(key))cur.add(username);else cur.delete(username);
      if(cur.size)next[key]=Array.from(cur).sort();else delete next[key];
    }
    setBusy(true);setMsg("");
    Promise.all(PAGE_IDS.map(([pid])=>{
      const key=_canonicalPageId(pid);
      return sf("/api/admin/page-admins",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({page_id:key,usernames:next[key]||[]})});
    }))
      .then(()=>{setPa(next);setMsg("✔ "+username+" "+preset.label+" 적용");setBusy(false);setTimeout(()=>setMsg(""),2000);})
      .catch(e=>{setMsg("오류: "+e.message);setBusy(false);});
  };
  return(<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16,overflow:"auto"}}>
    <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12,flexWrap:"wrap"}}>
      <div style={{fontSize:14,fontWeight:700}}>페이지별 권한 매트릭스</div>
      <div style={{fontSize:14,color:"var(--text-secondary)"}}>
        행=유저 · 열=페이지. 체크한 유저는 해당 페이지 관리 기능(설정/카탈로그/권한 편집) 수행 가능.
        admin 역할 / <code>admin</code>·<code>hol</code> 계정은 자동 전체 허용 (수정 불가).
      </div>
      {msg&&<span style={{fontSize:14,color:msg.startsWith("✔")?OK.fg:BAD.fg,marginLeft:"auto"}}>{msg}</span>}
      {busy&&<span style={{fontSize:14,color:"var(--text-secondary)"}}>저장 중…</span>}
    </div>
    <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
      <thead><tr>
        <th style={{position:"sticky",left:0,background:"var(--bg-tertiary)",textAlign:"left",padding:"8px 12px",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",zIndex:1,minWidth:140}}>유저</th>
        <th style={{textAlign:"center",padding:"8px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>프리셋</th>
        {PAGE_IDS.map(([pid,label])=><th key={pid} title={pid} style={{textAlign:"center",padding:"8px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{label}</th>)}
      </tr></thead>
      <tbody>{approved.map(u=>{
        const full=isFullAdmin(u);
        return(<tr key={u.username}>
          <td style={{position:"sticky",left:0,background:"var(--bg-secondary)",padding:"6px 12px",borderBottom:"1px solid var(--border)",fontWeight:600,zIndex:1}}>
            {u.username}{full&&<span style={{marginLeft:6,fontSize:14,padding:"1px 6px",borderRadius:8,background:BAD.bg,color:BAD.fg,fontWeight:700}}>ADMIN</span>}
          </td>
          <td style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)",whiteSpace:"nowrap"}}>
            {!full&&PAGE_PRESETS.map(p=>(
              <Button key={p.key} variant="ghost" disabled={busy} onClick={()=>applyPreset(u.username,p)} style={{padding:"4px 8px",fontSize:12,marginRight:4}}>{p.label}</Button>
            ))}
          </td>
          {PAGE_IDS.map(([pid])=>{
            const key=_canonicalPageId(pid);
            const assigned=(pa[key]||[]).includes(u.username);
            const checked=full||assigned;
            return(<td key={pid} style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
              <input type="checkbox" checked={checked} disabled={busy||full} onChange={()=>toggle(key,u.username)} title={full?"admin 자동 허용":""}/>
            </td>);
          })}
        </tr>);
      })}</tbody>
    </table>
  </div>);
}

// ── v8.8.14: Backup 주기 설정 + 1회 예약 ──
// interval_hours 조절 + enabled 토글 + "이 시각에 1회 백업" 예약 (서버 점검 대비).
// v9.x: Flow-i human-in-the-loop 학습 데이터(few-shot 용어 매핑, 파일 설명 카탈로그) 관리.
// v9.2.x: Semantic layer 편집기(용어사전)를 에이전트 탭에서 이관해 하위 섹션으로 통합.
//   저장소는 서로 다름 — semantic: FLOW_DATA_ROOT/semantic/* (/api/agent/semantic/*),
//   few-shot/파일설명: flowi_fewshots.json / flowi_file_docs.json (/api/flowi-learning/*).
const FLOWI_LEARNING_SECTIONS=[
  {k:"semantic",l:"용어사전 (Semantic layer)"},
  {k:"fewshot",l:"few-shot 용어"},
  {k:"filedocs",l:"파일 설명"},
];
function FlowiLearningPanel(){
  const[section,setSection]=useState("semantic");
  const[fewshots,setFewshots]=useState([]);
  const[fileDocs,setFileDocs]=useState([]);
  const[fsDraft,setFsDraft]=useState({term:"",answer:""});
  const[fdDraft,setFdDraft]=useState({file:"",description:""});
  const[msg,setMsg]=useState("");
  const load=()=>{
    sf("/api/flowi-learning/fewshots").then(d=>setFewshots(d.items||[])).catch(e=>setMsg("로드 오류: "+e.message));
    sf("/api/flowi-learning/file-docs").then(d=>setFileDocs(d.items||[])).catch(()=>{});
  };
  useEffect(()=>{load();},[]);
  const saveFewshot=(term,answer)=>{
    if(!term.trim()||!answer.trim()){toast.warn("용어와 답을 입력하세요.");return;}
    sf("/api/flowi-learning/fewshots/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({term:term.trim(),answer:answer.trim()})})
      .then(()=>{toast.ok("저장됨");setFsDraft({term:"",answer:""});load();}).catch(e=>toast.error(e.message||"저장 실패"));
  };
  const deleteFewshot=(term)=>{
    if(!window.confirm(`'${term}' 학습 데이터를 삭제할까요?`))return;
    sf("/api/flowi-learning/fewshots/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({term})})
      .then(()=>{toast.ok("삭제됨");load();}).catch(e=>toast.error(e.message||"삭제 실패"));
  };
  const saveFileDoc=(file,description)=>{
    if(!file.trim()||!description.trim()){toast.warn("파일명과 설명을 입력하세요.");return;}
    sf("/api/flowi-learning/file-docs/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({file:file.trim(),description:description.trim()})})
      .then(()=>{toast.ok("저장됨");setFdDraft({file:"",description:""});load();}).catch(e=>toast.error(e.message||"저장 실패"));
  };
  const deleteFileDoc=(file)=>{
    if(!window.confirm(`'${file}' 파일 설명을 삭제할까요?`))return;
    sf("/api/flowi-learning/file-docs/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({file})})
      .then(()=>{toast.ok("삭제됨");load();}).catch(e=>toast.error(e.message||"삭제 실패"));
  };
  const th={textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:14,borderBottom:"1px solid var(--border)",whiteSpace:"nowrap"};
  const td={padding:"8px 12px",borderBottom:"1px solid var(--border)",fontSize:14,verticalAlign:"top"};
  const inp={padding:"6px 8px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,boxSizing:"border-box"};
  return(<div style={{display:"grid",gap:16}}>
    <TabStrip active={section} onChange={setSection} items={FLOWI_LEARNING_SECTIONS}/>
    {section==="semantic"&&<SemanticLayerPanel/>}
    {section==="fewshot"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:15,fontWeight:800,marginBottom:4}}>few-shot 용어 매핑</div>
      <div style={{fontSize:13,color:"var(--text-secondary)",marginBottom:10}}>
        홈 채팅 "기억해: &lt;용어&gt;는 &lt;답&gt;" 티칭과 싫어요+교정 코멘트로 쌓인 학습 데이터입니다. 같은 용어 질문에 조회보다 먼저 이 답을 씁니다.
      </div>
      <div style={{display:"flex",gap:8,marginBottom:10,flexWrap:"wrap"}}>
        <input value={fsDraft.term} onChange={e=>setFsDraft(d=>({...d,term:e.target.value}))} placeholder="용어 (예: AB100000EC)" style={{...inp,width:220,fontFamily:"monospace"}}/>
        <input value={fsDraft.answer} onChange={e=>setFsDraft(d=>({...d,answer:e.target.value}))} placeholder="답" style={{...inp,flex:1,minWidth:240}}/>
        <Button variant="primary" onClick={()=>saveFewshot(fsDraft.term,fsDraft.answer)}>추가/수정</Button>
      </div>
      <div style={{overflow:"auto",maxHeight:360}}>
        <table style={{width:"100%",borderCollapse:"collapse"}}>
          <thead><tr>{["용어","답","가르친 사람","출처","사용","수정시각",""].map(h=><th key={h} style={th}>{h}</th>)}</tr></thead>
          <tbody>
            {fewshots.length===0&&<tr><td colSpan={7} style={{...td,textAlign:"center",color:"var(--text-secondary)"}}>학습 데이터 없음</td></tr>}
            {fewshots.map(e=>(<tr key={e.term}>
              <td style={{...td,fontFamily:"monospace",fontWeight:700}}>{e.term}</td>
              <td style={td}>{e.answer}</td>
              <td style={td}>{e.taught_by||"-"}</td>
              <td style={td}>{e.source||"-"}</td>
              <td style={td}>{e.uses||0}</td>
              <td style={{...td,color:"var(--text-secondary)"}}>{String(e.updated_at||"").slice(0,16).replace("T"," ")}</td>
              <td style={{...td,whiteSpace:"nowrap"}}>
                <Button variant="ghost" onClick={()=>setFsDraft({term:e.term,answer:e.answer})}>수정</Button>
                <Button variant="danger" onClick={()=>deleteFewshot(e.term)}>삭제</Button>
              </td>
            </tr>))}
          </tbody>
        </table>
      </div>
    </div>}
    {section==="filedocs"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:15,fontWeight:800,marginBottom:4}}>파일 설명 카탈로그</div>
      <div style={{fontSize:13,color:"var(--text-secondary)",marginBottom:10}}>
        홈 채팅 "파일 설명: &lt;파일명&gt;은 &lt;설명&gt;" 으로 등록된 카탈로그입니다. Flow-i가 질문과 설명을 대조해 검색할 파일을 고릅니다.
      </div>
      <div style={{display:"flex",gap:8,marginBottom:10,flexWrap:"wrap"}}>
        <input value={fdDraft.file} onChange={e=>setFdDraft(d=>({...d,file:e.target.value}))} placeholder="파일명 (예: step_matching.csv)" style={{...inp,width:260,fontFamily:"monospace"}}/>
        <input value={fdDraft.description} onChange={e=>setFdDraft(d=>({...d,description:e.target.value}))} placeholder="설명 (무슨 데이터가 있는 파일인지)" style={{...inp,flex:1,minWidth:240}}/>
        <Button variant="primary" onClick={()=>saveFileDoc(fdDraft.file,fdDraft.description)}>추가/수정</Button>
      </div>
      <div style={{overflow:"auto",maxHeight:360}}>
        <table style={{width:"100%",borderCollapse:"collapse"}}>
          <thead><tr>{["파일","설명","등록자","수정시각",""].map(h=><th key={h} style={th}>{h}</th>)}</tr></thead>
          <tbody>
            {fileDocs.length===0&&<tr><td colSpan={5} style={{...td,textAlign:"center",color:"var(--text-secondary)"}}>등록된 파일 설명 없음</td></tr>}
            {fileDocs.map(e=>(<tr key={e.file}>
              <td style={{...td,fontFamily:"monospace",fontWeight:700}}>{e.file}</td>
              <td style={td}>{e.description}</td>
              <td style={td}>{e.updated_by||"-"}</td>
              <td style={{...td,color:"var(--text-secondary)"}}>{String(e.updated_at||"").slice(0,16).replace("T"," ")}</td>
              <td style={{...td,whiteSpace:"nowrap"}}>
                <Button variant="ghost" onClick={()=>setFdDraft({file:e.file,description:e.description})}>수정</Button>
                <Button variant="danger" onClick={()=>deleteFileDoc(e.file)}>삭제</Button>
              </td>
            </tr>))}
          </tbody>
        </table>
      </div>
    </div>}
    {msg&&<Banner tone="warn">{msg}</Banner>}
  </div>);
}

function BackupSchedulePanel(){
  const [st,setSt]=useState(null);
  const [msg,setMsg]=useState("");
  const [form,setForm]=useState({interval_hours:24,enabled:true,keep:5});
  const [sched,setSched]=useState({at:"",reason:"pre-maintenance"});
  const reload=()=>sf("/api/admin/backup/status").then(d=>{
    setSt(d);
    const s=d.settings||{};
    setForm({interval_hours:s.interval_hours||24,enabled:s.enabled!==false,keep:s.keep||5});
    setSched({at:(s.scheduled_at||"").slice(0,16),reason:s.scheduled_reason||"pre-maintenance"});
  }).catch(e=>setMsg("로드 오류: "+e.message));
  useEffect(()=>{reload();},[]);
  const saveSettings=()=>{
    setMsg("");
    sf("/api/admin/settings").then(cur=>sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      dashboard_refresh_minutes:cur.dashboard_refresh_minutes||10,
      dashboard_bg_refresh_minutes:cur.dashboard_bg_refresh_minutes||10,
      backup:{interval_hours:form.interval_hours,enabled:form.enabled,keep:form.keep},
    })})).then(()=>{setMsg("✔ 저장됨");reload();}).catch(e=>setMsg("오류: "+e.message));
  };
  const runNow=()=>{
    if(!confirm("지금 백업을 실행할까요? (최대 수십 MB)"))return;
    setMsg("백업 진행 중…");
    sf("/api/admin/backup/run",{method:"POST"}).then(r=>{setMsg(r.ok?"✔ 백업 완료: "+(r.path||""):"✗ 실패: "+(r.error||""));reload();}).catch(e=>setMsg("오류: "+e.message));
  };
  const restoreBackup=(b)=>{
    if(!confirm(`${b.filename} 백업으로 data_root 를 롤백할까요?\n현재 상태는 pre-restore 백업으로 먼저 저장됩니다.`))return;
    setMsg("롤백 진행 중…");
    sf("/api/admin/backup/restore",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename:b.filename})})
      .then(r=>{setMsg(`✔ 롤백 완료: ${r.restored||0} files (pre: ${r.pre_restore_backup||"-"})`);reload();})
      .catch(e=>setMsg("롤백 오류: "+e.message));
  };
  const schedule=()=>{
    const at=(sched.at||"").trim();
    if(!at){setMsg("예약 시각(YYYY-MM-DDTHH:MM)을 입력하세요.");return;}
    sf("/api/admin/backup/schedule",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({at:at+":00",reason:sched.reason||"pre-maintenance"})})
      .then(()=>{setMsg("✔ 예약됨: "+at);reload();}).catch(e=>setMsg("예약 오류: "+e.message));
  };
  const cancelSched=()=>sf("/api/admin/backup/schedule",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({at:"",reason:""})})
    .then(()=>{setMsg("✔ 예약 취소");reload();}).catch(e=>setMsg("취소 오류: "+e.message));
  const L={fontSize:14,color:"var(--text-secondary)",marginBottom:4,marginTop:10,fontWeight:600};
  const I={width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"};
  return(<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,maxWidth:1100}}>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:8}}>자동 백업 설정</div>
      <div style={L}>활성화</div>
      <label style={{display:"flex",alignItems:"center",gap:6,fontSize:14}}><input type="checkbox" checked={!!form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})}/>자동 백업 사용</label>
      <div style={L}>주기 (시간)</div>
      <input type="number" min={1} max={168} value={form.interval_hours} onChange={e=>setForm({...form,interval_hours:parseInt(e.target.value)||24})} style={I}/>
      <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4}}>12 → 12시간마다, 24 → 하루 1회 (기본). 1~168 시간 범위.</div>
      <div style={L}>보관 개수 (최대 5)</div>
      <input type="number" min={1} max={5} value={form.keep} onChange={e=>setForm({...form,keep:parseInt(e.target.value)||5})} style={I}/>
      <button onClick={saveSettings} style={{marginTop:14,padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:"pointer"}}>설정 저장</button>
      <button onClick={runNow} style={{marginTop:14,marginLeft:8,padding:"8px 20px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",cursor:"pointer"}}>🗄 즉시 백업</button>
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:8}}>예약 백업 (서버 점검 대비)</div>
      <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:10}}>지정한 시각이 지나면 백그라운드 스케줄러가 1회 백업을 실행하고 자동으로 취소됩니다. (주기 백업과 중복 실행되지 않음)</div>
      <div style={L}>예약 시각</div>
      <input type="datetime-local" value={sched.at} onChange={e=>setSched({...sched,at:e.target.value})} style={I}/>
      <div style={L}>사유 (메모)</div>
      <input value={sched.reason} onChange={e=>setSched({...sched,reason:e.target.value})} placeholder="pre-maintenance" style={I}/>
      <div style={{display:"flex",gap:8,marginTop:14}}>
        <button onClick={schedule} style={{padding:"8px 16px",borderRadius:6,border:"none",background:WARN.fg,color:WHITE,fontWeight:600,cursor:"pointer"}}>⏰ 예약</button>
        {st?.settings?.scheduled_at&&<button onClick={cancelSched} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>예약 취소</button>}
      </div>
      {st?.settings?.scheduled_at&&<div style={{marginTop:10,padding:"6px 10px",borderRadius:6,background:WARN.bg,color:WARN.fg,fontSize:14}}>🔔 예약됨: {st.settings.scheduled_at} ({st.settings.scheduled_reason||"-"})</div>}
    </div>
    <div style={{gridColumn:"1 / -1",background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:8}}>최근 백업</div>
      {st?.settings?.last&&<div style={{fontSize:14,color:st.settings.last.ok?OK.fg:BAD.fg,marginBottom:8}}>
        {st.settings.last.ok?"✔":"✗"} {st.settings.last.at} · {st.settings.last.reason||"-"} · {st.settings.last.bytes?Math.round(st.settings.last.bytes/1024)+" KB":""} {st.settings.last.error?"· "+st.settings.last.error:""}
      </div>}
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
        <thead><tr><th style={{textAlign:"left",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>파일</th><th style={{textAlign:"right",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>크기</th><th style={{textAlign:"left",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>시각</th><th style={{textAlign:"right",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>작업</th></tr></thead>
        <tbody>{(st?.backups||[]).map(b=>(<tr key={b.filename}><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{b.filename}</td><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",textAlign:"right"}}>{Math.round((b.size||0)/1024).toLocaleString()} KB</td><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{b.modified}</td><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",textAlign:"right"}}><button onClick={()=>restoreBackup(b)} style={{padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:WARN.fg,cursor:"pointer",fontSize:14}}>롤백</button></td></tr>))}</tbody>
      </table>
    </div>
    {msg&&<div style={{gridColumn:"1 / -1",fontSize:14,color:msg.startsWith("✔")?OK.fg:BAD.fg}}>{msg}</div>}
  </div>);
}

// ── v8.8.14: Activity Dashboard ──
// 최근 N일 활동 요약 + 기능(action prefix) 별 사용 현황. admin 이 "누가 뭘 쓰는지",
// "어떤 기능이 활성화되어 있는지" 한눈에 파악할 수 있게.
function ActivityDashboardPanel(){
  const [days,setDays]=useState(7);
  const [summary,setSummary]=useState(null);
  const [features,setFeatures]=useState(null);
  const [splitCache,setSplitCache]=useState(null);
  const [err,setErr]=useState("");
  const reload=()=>{
    setErr("");
    sf("/api/admin/activity/summary?days="+days).then(setSummary).catch(e=>setErr("요약 로드 오류: "+e.message));
    sf("/api/admin/activity/features?days="+days).then(setFeatures).catch(()=>{});
    // SplitTable RAM 캐시 상태 + 최근 검색 단계별 타이밍(관리자에게만 recent_searches 포함).
    sf("/api/splittable/root-lot-cache/status").then(setSplitCache).catch(()=>setSplitCache(null));
  };
  useEffect(()=>{reload();},[days]);
  const barItem=(label,val,max,color)=>(<div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
    <span style={{fontSize:14,minWidth:120,fontFamily:"monospace"}}>{label}</span>
    <div style={{flex:1,height:14,background:"var(--bg-tertiary)",borderRadius:3,overflow:"hidden"}}>
      <div style={{width:(max>0?(100*val/max):0)+"%",height:"100%",background:color}}/>
    </div>
    <span style={{fontSize:14,minWidth:50,textAlign:"right",color:"var(--text-secondary)"}}>{val}</span>
  </div>);
  const maxUser=summary?Math.max(0,...Object.values(_obj(summary.by_user))):0;
  const maxAct=summary?Math.max(0,...Object.values(_obj(summary.by_action))):0;
  const maxDay=summary?Math.max(0,...Object.values(_obj(summary.by_day))):0;
  return(<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
    <div style={{gridColumn:"1 / -1",display:"flex",alignItems:"center",gap:12}}>
      <span style={{fontSize:14,fontWeight:700}}>활동 대시보드</span>
      <span style={{fontSize:14,color:"var(--text-secondary)"}}>최근</span>
      {[1,7,30,90].map(d=>(<span key={d} onClick={()=>setDays(d)} style={{cursor:"pointer",fontSize:14,padding:"3px 10px",borderRadius:6,background:days===d?"var(--accent-glow)":"transparent",color:days===d?"var(--accent)":"var(--text-secondary)",fontWeight:days===d?700:500,border:"1px solid "+(days===d?"var(--accent)":"var(--border)")}}>{d}일</span>))}
      {summary&&<span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:"auto"}}>총 {summary.total}건 · 기능 {features?.feature_count||0}개</span>}
      {err&&<span style={{fontSize:14,color:BAD.fg}}>{err}</span>}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>유저별</div>
      {summary?_entries(summary.by_user).map(([u,v])=>barItem(u,v,maxUser,INFO.fg)):<span style={{color:"var(--text-secondary)",fontSize:14}}>로딩…</span>}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>액션별</div>
      <div style={{maxHeight:340,overflowY:"auto"}}>
        {summary?_entries(summary.by_action).map(([a,v])=>barItem(a,v,maxAct,chartPalette.series[6])):<span style={{color:"var(--text-secondary)",fontSize:14}}>로딩…</span>}
      </div>
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>일자별</div>
      {summary?_entries(summary.by_day).map(([d,v])=>barItem(d,v,maxDay,OK.fg)):<span style={{color:"var(--text-secondary)",fontSize:14}}>로딩…</span>}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>기능별 활성 현황</div>
      <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:8}}>action prefix (예: inform, splittable, admin) 기준</div>
      <div style={{maxHeight:340,overflowY:"auto"}}>
        {_arr(features?.features).map(f=>(<div key={f.feature} style={{padding:"8px 10px",marginBottom:6,borderRadius:6,background:"var(--bg-tertiary)",border:"1px solid var(--border)"}}>
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <span style={{fontSize:14,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>{f.feature}</span>
            <span style={{fontSize:14,color:"var(--text-secondary)"}}>{f.count}건 · 유저 {f.user_count}명</span>
            <span style={{flex:1}}/>
            <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}} title={`first: ${f.first_seen}\nlast: ${f.last_seen}`}>~{(f.last_seen||"").slice(0,16)}</span>
          </div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:3,fontFamily:"monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>
            top: {_entries(f.top_actions).map(([k,v])=>k+"("+v+")").join(" · ")}
          </div>
        </div>))}
        {_arr(features?.features).length===0&&<span style={{color:"var(--text-secondary)",fontSize:14}}>로딩…</span>}
      </div>
    </div>
    <div style={{gridColumn:"1 / -1",background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>SplitTable LOT 검색</div>
      <div style={{maxHeight:220,overflowY:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
          <thead><tr>{["시각","유저","product","root_lot_id","fab_lot_id","prefix"].map(h=><th key={h} style={{textAlign:"left",padding:"4px 8px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}}>{h}</th>)}</tr></thead>
          <tbody>{_arr(summary?.split_table_lot_searches).map((r,i)=>(<tr key={i}>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{(r.timestamp||"").replace("T"," ").slice(0,16)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontWeight:600}}>{r.username||""}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{r.product||""}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--accent)"}}>{r.root_lot_id||""}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{r.fab_lot_id||""}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{r.prefix||""}</td>
          </tr>))}</tbody>
        </table>
        {_arr(summary?.split_table_lot_searches).length===0&&<div style={{padding:20,textAlign:"center",fontSize:14,color:"var(--text-secondary)"}}>최근 SplitTable LOT 검색이 없습니다</div>}
      </div>
    </div>
    {(()=>{const rc=splitCache?.cache||{};const st=splitCache?.settings||{};const rows=_arr(splitCache?.recent_searches);
      const dsLabel={payload_cache:"응답캐시",pivot_cache:"pivot캐시",product_ram:"제품RAM",ram:"메모리HIT",ram_load:"메모리적재",disk:"디스크(첫검색)",root_cache:"캐시",raw:"원본스캔"};
      const dsColor=(ds)=>ds==="disk"||ds==="ram_load"?"var(--accent)":(ds==="ram"||ds==="payload_cache"||ds==="pivot_cache"||ds==="product_ram")?"#22c55e":"var(--text-secondary)";
      const ms=(v)=>Number(v||0).toFixed(1);
      return(
    <div style={{gridColumn:"1 / -1",background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>SplitTable 검색 타이밍 · Root RAM 캐시</div>
      <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:10,display:"flex",gap:14,flexWrap:"wrap"}}>
        <span>캐시 {rc.hit_roots||0} roots (step {rc.step_hit_roots||0} / other {rc.other_hit_roots||0})</span>
        <span>{Number(rc.estimated_mb||0).toFixed(1)} MB / {rc.max_gb||0} GB</span>
        <span>CPU {Number(rc.cpu_budget_cores||0).toFixed(1)} cores · polars {rc.polars_threads||"?"} threads</span>
        <span>target {st.target_roots||0} · step [{(st.step_ids||[]).join(",")||"-"}]</span>
        <span>최근갱신 {(rc.last_refresh_at||"").replace("T"," ")||"-"}</span>
      </div>
      <div style={{maxHeight:260,overflowY:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
          <thead><tr>{["시각","product","root_lot_id","데이터소스","total","scan","root_scan","collect","matrix","overlay","rows"].map(h=><th key={h} style={{textAlign:"left",padding:"4px 8px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r,i)=>(<tr key={i}>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{(r.at||"").replace("T"," ").slice(0,19)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{r.product||""}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--accent)"}}>{r.root_lot_id||""}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontWeight:700,color:dsColor(r.data_source)}}>{dsLabel[r.data_source]||r.data_source||"-"}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontWeight:700}}>{ms(r.total_ms)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{ms(r.scan_ms)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{ms(r.root_scan_ms)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{ms(r.collect_ms)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{ms(r.matrix_ms)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{ms(r.overlay_ms)}</td>
            <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)"}}>{r.row_count||0}</td>
          </tr>))}</tbody>
        </table>
        {rows.length===0&&<div style={{padding:20,textAlign:"center",fontSize:14,color:"var(--text-secondary)"}}>최근 검색 타이밍이 없습니다 (SplitTable에서 root_lot_id로 검색하면 기록됩니다)</div>}
      </div>
      <div style={{fontSize:13,color:"var(--text-secondary)",marginTop:8}}>ms 단위 · <b>메모리HIT</b>=RAM 캐시 즉시응답, <b>디스크(첫검색)</b>/<b>메모리적재</b>=첫 조회로 파티션 parquet 읽음. scan=캐시/조인 준비, collect=피벗 수집, matrix=셀 매트릭스 구성.</div>
      {/* RAM 캐시 개별 항목 목록 + 삭제 관리 */}
      {_arr(splitCache?.cache?.roots).length > 0 && (<>
        <div style={{fontSize:14,fontWeight:700,marginTop:16,marginBottom:8}}>RAM 캐시 항목 ({_arr(splitCache?.cache?.roots).length}개)</div>
        <div style={{maxHeight:280,overflowY:"auto"}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
            <thead><tr>{["vehicle","root_lot_id","사유","MB","rows","조회수","적재시각",""].map(h=><th key={h} style={{textAlign:"left",padding:"4px 8px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:13,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{h}</th>)}</tr></thead>
            <tbody>{_arr(splitCache?.cache?.roots).map((r,i)=>{
              const groupLabel={"step":"step기반","searched":"검색됨","recent":"최근tkout","frequent":"자주조회","warmup":"워밍업"};
              const groupColor={"step":"#3e7bd6","searched":"#22c55e","recent":"#c78a1e","frequent":"#8a5fd0","warmup":"var(--text-secondary)"};
              const grp=r.cache_group||"other";
              // source_path 에서 vehicle(product) 이름 추출 — 경로의 마지막 폴더 or 파일명
              const sp=r.source_path||"";
              const veh=sp.split(/[/\\]/).filter(Boolean).pop()||sp;
              return(<tr key={i}>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:13}} title={sp}>{veh}</td>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--accent)",fontWeight:600}}>{r.root_lot_id||""}</td>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:groupColor[grp]||"var(--text-secondary)",fontWeight:600}}>{groupLabel[grp]||grp}</td>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{Number(r.estimated_mb||0).toFixed(1)}</td>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)"}}>{r.row_count||0}</td>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)"}}>{r.access_count||0}</td>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{(r.loaded_at||"").replace("T"," ").slice(0,19)}</td>
                <td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)"}}>
                  <button onClick={async()=>{try{await postJson("/api/splittable/root-lot-cache/evict",{source_path:r.source_path,root_lot_id:r.root_lot_id});toast.ok(r.root_lot_id+" 캐시 제거됨");reload();}catch(e){toast.error(String(e.message||e));}}}
                    style={{fontSize:12,padding:"2px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:BAD.fg,cursor:"pointer"}} title="이 항목을 RAM 캐시에서 제거">삭제</button>
                </td>
              </tr>);
            })}</tbody>
          </table>
        </div>
      </>)}
    </div>);})()}
    <div style={{gridColumn:"1 / -1",background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:10}}>최근 이벤트 (50건)</div>
      <div style={{maxHeight:400,overflowY:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
          <thead><tr>{["시각","유저","action","tab","detail"].map(h=><th key={h} style={{textAlign:"left",padding:"4px 8px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}}>{h}</th>)}</tr></thead>
          <tbody>{_arr(summary?.recent).map((r,i)=>(<tr key={i}><td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{(r.timestamp||r.time||"").replace("T"," ").slice(0,16)}</td><td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontWeight:600}}>{r.username||r.actor}</td><td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{r.action}</td><td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{r.tab||""}</td><td style={{padding:"4px 8px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)",maxWidth:400,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={r.detail}>{r.detail}</td></tr>))}</tbody>
        </table>
      </div>
    </div>
  </div>);
}

// ── v9.0.5: Flow-i structured feedback review loop ──
const FLOWI_DEFAULTS_FALLBACK={
  chart_defaults:{
    surface:"home_flowi",
    scatter:{grain:"wafer_agg",max_points:500,inline_agg:"avg",et_agg:"median"},
    line:{grain:"wafer_agg",max_points_per_series:120},
    bar:{top_n:12,other_bucket:true},
    pie:{max_slices:6,other_bucket:true},
    box:{max_groups:12,min_n:3},
  },
  feedback_policy:{auto_apply_to_rag:false,review_required:true,promotion_target:"golden_cases"},
  engineer_knowledge:{rag_update_requires_marker:true,admin_review_required:true,custom_knowledge_append_only:true},
};
function normalizeFlowiDefaults(raw={}){
  const c=_obj(raw.chart_defaults);
  const scatter={...FLOWI_DEFAULTS_FALLBACK.chart_defaults.scatter,..._obj(c.scatter)};
  const line={...FLOWI_DEFAULTS_FALLBACK.chart_defaults.line,..._obj(c.line)};
  const bar={...FLOWI_DEFAULTS_FALLBACK.chart_defaults.bar,..._obj(c.bar)};
  const pie={...FLOWI_DEFAULTS_FALLBACK.chart_defaults.pie,..._obj(c.pie)};
  const box={...FLOWI_DEFAULTS_FALLBACK.chart_defaults.box,..._obj(c.box)};
  return{
    chart_defaults:{
      surface:c.surface||"home_flowi",
      scatter:{...scatter,max_points:Number(scatter.max_points)||500},
      line:{...line,max_points_per_series:Number(line.max_points_per_series)||120},
      bar:{...bar,top_n:Number(bar.top_n)||12,other_bucket:bar.other_bucket!==false},
      pie:{...pie,max_slices:Number(pie.max_slices)||6,other_bucket:pie.other_bucket!==false},
      box:{...box,max_groups:Number(box.max_groups)||12,min_n:Number(box.min_n)||3},
    },
    feedback_policy:{...FLOWI_DEFAULTS_FALLBACK.feedback_policy,..._obj(raw.feedback_policy),auto_apply_to_rag:false},
    engineer_knowledge:{...FLOWI_DEFAULTS_FALLBACK.engineer_knowledge,..._obj(raw.engineer_knowledge)},
  };
}

export function FlowiQualityPanel(){
  const[days,setDays]=useState(30);
  const[data,setData]=useState(null);
  const[err,setErr]=useState("");
  const[msg,setMsg]=useState("");
  const[promoting,setPromoting]=useState("");
  const[defaults,setDefaults]=useState(FLOWI_DEFAULTS_FALLBACK);
  const[defaultsMsg,setDefaultsMsg]=useState("");
  const[defaultsBusy,setDefaultsBusy]=useState(false);
  const[adminUpdate,setAdminUpdate]=useState({mode:"workflow",prompt:"",expected_intent:"",expected_tool:"",expected_answer:"",data_refs:"",notes:""});
  const[adminUpdateMsg,setAdminUpdateMsg]=useState("");
  const[adminUpdateBusy,setAdminUpdateBusy]=useState(false);
  const reload=()=>{
    setErr("");
    sf(`/api/llm/flowi/feedback/summary?days=${days}&limit=300`).then(setData).catch(e=>setErr("로드 오류: "+e.message));
  };
  useEffect(()=>{reload();},[days]);
  const reloadDefaults=()=>{
    sf("/api/admin/settings").then(d=>setDefaults(normalizeFlowiDefaults(d.flowi_defaults||{}))).catch(e=>setDefaultsMsg("기본값 로드 오류: "+e.message));
  };
  useEffect(()=>{reloadDefaults();},[]);
  const patchChart=(kind,next)=>setDefaults(d=>({...d,chart_defaults:{...d.chart_defaults,[kind]:{..._obj(d.chart_defaults?.[kind]),...next}}}));
  const patchPolicy=(next)=>setDefaults(d=>({...d,feedback_policy:{..._obj(d.feedback_policy),...next,auto_apply_to_rag:false}}));
  const saveDefaults=()=>{
    setDefaultsBusy(true);setDefaultsMsg("");
    const payload=normalizeFlowiDefaults(defaults);
    sf("/api/admin/settings").then(cur=>sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      dashboard_refresh_minutes:cur.dashboard_refresh_minutes??10,
      dashboard_bg_refresh_minutes:cur.dashboard_bg_refresh_minutes??10,
      flowi_defaults:payload,
    })})).then(()=>{setDefaultsMsg("운영 기본값 저장됨");reloadDefaults();})
      .catch(e=>setDefaultsMsg("저장 오류: "+e.message))
      .finally(()=>setDefaultsBusy(false));
  };
  const patchAdminUpdate=(next)=>setAdminUpdate(d=>({...d,...next}));
  const submitAdminUpdate=()=>{
    setAdminUpdateBusy(true);setAdminUpdateMsg("");
    postJson("/api/llm/flowi/admin/update",adminUpdate)
      .then(d=>{
        const workflowId=d?.workflow?.id||"";
        const bits=[];
        if(workflowId)bits.push(`workflow ${workflowId}`);
        setAdminUpdateMsg(bits.length?`workflow 저장됨: ${bits.join(" / ")}`:"workflow 저장됨");
        reload();
      })
      .catch(e=>setAdminUpdateMsg("업데이트 오류: "+(e.message||e)))
      .finally(()=>setAdminUpdateBusy(false));
  };
  const taxonomy=Object.fromEntries(_arr(data?.taxonomy).map(t=>[t.key,t]));
  const labelTag=(key)=>taxonomy[key]?.label||key;
  const toneFor=(rating,tags)=>{
    if(rating==="up"&&(!tags||tags.every(t=>t==="correct")))return "ok";
    if(_arr(tags).some(t=>["wrong_data_source","permission_risk","hallucination","key_matching_error","aggregation_error"].includes(t)))return "bad";
    return rating==="down"?"warn":"neutral";
  };
  const promote=(rec)=>{
    if(!rec?.id)return;
    setPromoting(rec.id);setMsg("");
    postJson("/api/llm/flowi/feedback/promote",{
      feedback_id:rec.id,
      expected_intent:rec.intent||rec.tool_summary?.intent||"",
      expected_tool:rec.expected_workflow||rec.tool_summary?.action||"",
      expected_answer:rec.correct_route||rec.expected_answer||"",
      notes:rec.note||"",
    }).then(d=>{setMsg(`Golden case 저장됨: ${d?.case?.id||""}`);reload();})
      .catch(e=>setMsg(e.message||"승격 실패"))
      .finally(()=>setPromoting(""));
  };
  const smallCount=(label,val,tone="neutral")=><div style={{padding:"10px 12px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-secondary)"}}>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>{label}</div>
    <div style={{fontSize:22,fontWeight:900,color:tone==="ok"?OK.fg:tone==="bad"?BAD.fg:tone==="warn"?WARN.fg:"var(--text-primary)",fontFamily:"monospace"}}>{Number(val||0).toLocaleString()}</div>
  </div>;
  const counterList=(title,obj,color)=><div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14,minHeight:160}}>
    <div style={{fontSize:14,fontWeight:800,marginBottom:10}}>{title}</div>
    <div style={{display:"grid",gap:6}}>
      {_entries(obj).slice(0,10).map(([k,v])=><div key={k} style={{display:"grid",gridTemplateColumns:"minmax(90px,0.8fr) minmax(80px,1fr) 42px",alignItems:"center",gap:8}}>
        <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={k}>{title==="실패 유형"?labelTag(k):k}</span>
        <div style={{height:9,borderRadius:999,background:"var(--bg-tertiary)",overflow:"hidden"}}><div style={{height:"100%",width:`${Math.min(100,(Number(v)||0)/Math.max(1,...Object.values(_obj(obj)).map(Number))*100)}%`,background:color}}/></div>
        <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",textAlign:"right"}}>{v}</span>
      </div>)}
      {_entries(obj).length===0&&<div style={{fontSize:14,color:"var(--text-secondary)"}}>데이터 없음</div>}
    </div>
  </div>;
  const feedbackCard=(rec,idx)=><div key={rec.id||idx} style={{padding:12,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
    <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginBottom:7}}>
      <Pill tone={toneFor(rec.rating,rec.tags)}>{rec.rating||"neutral"}</Pill>
      <span style={{fontSize:14,fontFamily:"monospace",color:"var(--text-secondary)"}}>{String(rec.timestamp||"").replace("T"," ").slice(0,19)}</span>
      <span style={{fontSize:14,fontWeight:700}}>{rec.username||"-"}</span>
      <span style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace"}}>{rec.intent||rec.tool_summary?.intent||"-"}</span>
      {rec.golden_candidate&&<Pill tone="warn">golden 후보</Pill>}
      <span style={{flex:1}}/>
      <Button variant="ghost" disabled={promoting===rec.id} onClick={()=>promote(rec)}>{promoting===rec.id?"저장 중":"Golden 저장"}</Button>
    </div>
    <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.55,display:"grid",gap:5}}>
      <div><b style={{color:"var(--text-primary)"}}>Prompt</b> {rec.prompt_excerpt||"-"}</div>
      <div><b style={{color:"var(--text-primary)"}}>Answer</b> {rec.answer_excerpt||"-"}</div>
      {(rec.note||rec.correct_route||rec.expected_workflow||rec.data_refs)&&<div style={{padding:8,borderRadius:6,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
        {rec.note&&<div>의견: {rec.note}</div>}
        {rec.expected_workflow&&<div>기대 workflow: {rec.expected_workflow}</div>}
        {rec.data_refs&&<div>정답 DB/컬럼: {rec.data_refs}</div>}
        {rec.correct_route&&<div>정답 경로: {rec.correct_route}</div>}
      </div>}
      <div style={{display:"flex",gap:5,flexWrap:"wrap"}}>
        {_arr(rec.tags).map(t=><span key={t} style={{fontSize:14,padding:"2px 7px",borderRadius:999,border:"1px solid var(--border)",color:taxonomy[t]?.tone==="bad"?BAD.fg:taxonomy[t]?.tone==="warn"?WARN.fg:OK.fg}}>{labelTag(t)}</span>)}
        {rec.tool_summary?.action&&<span style={{fontSize:14,padding:"2px 7px",borderRadius:999,border:"1px solid var(--border)",color:"var(--text-secondary)",fontFamily:"monospace"}}>{rec.tool_summary.action}</span>}
        {rec.elapsed_ms!=null&&<span style={{fontSize:14,padding:"2px 7px",borderRadius:999,border:"1px solid var(--border)",color:"var(--text-secondary)",fontFamily:"monospace"}}>{rec.elapsed_ms}ms</span>}
      </div>
    </div>
  </div>;
  const review=_arr(data?.review_queue);
  const recent=_arr(data?.recent);
  const golden=_arr(data?.golden_cases);
  const cd=defaults.chart_defaults||{};
  const scatter=cd.scatter||{};
  const line=cd.line||{};
  const bar=cd.bar||{};
  const pie=cd.pie||{};
  const box=cd.box||{};
  const policy=defaults.feedback_policy||{};
  const L={fontSize:14,color:"var(--text-secondary)",marginBottom:4,fontWeight:700};
  const I={width:"100%",padding:"7px 9px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",boxSizing:"border-box"};
  return(<div style={{display:"grid",gap:16}}>
    <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
      <div>
        <div style={{fontSize:14,fontWeight:800}}>Flow-i 품질 피드백</div>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:3}}>실패 유형을 모아 tool schema, 확인 질문, cache/query 경로, golden workflow를 개선합니다.</div>
      </div>
      <span style={{marginLeft:"auto",fontSize:14,color:"var(--text-secondary)"}}>최근</span>
      <TabStrip
        items={[7,30,90,180].map(d=>({k:String(d),l:`${d}일`}))}
        active={String(days)}
        onChange={(k)=>setDays(Number(k))}
      />
      <Button variant="ghost" onClick={reload}>새로고침</Button>
    </div>
    {err&&<Banner tone="bad">{err}</Banner>}
    {msg&&<Banner tone={msg.includes("저장됨")?"ok":"warn"}>{msg}</Banner>}
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
      <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap",marginBottom:12}}>
        <div>
          <div style={{fontSize:14,fontWeight:800}}>Admin workflow 업데이트</div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:3}}>홈 Flow-i 피드백을 바탕으로 golden workflow만 관리합니다. RAG 지식 등록은 에이전트 페이지의 RAG 반영 화면에서 처리합니다.</div>
        </div>
        <span style={{flex:1}}/>
        <Button onClick={submitAdminUpdate} disabled={adminUpdateBusy}>{adminUpdateBusy?"저장 중":"workflow 저장"}</Button>
      </div>
      {adminUpdateMsg&&<div style={{fontSize:14,color:adminUpdateMsg.includes("오류")?BAD.fg:OK.fg,marginBottom:10}}>{adminUpdateMsg}</div>}
      <div style={{display:"grid",gridTemplateColumns:"minmax(280px,1.2fr) minmax(240px,0.8fr)",gap:12}}>
        <div>
          <div style={L}>대표 prompt</div>
          <textarea value={adminUpdate.prompt} onChange={e=>patchAdminUpdate({prompt:e.target.value})} rows={7}
            placeholder="예: 사용자가 INLINE CD와 ET metric 상관을 물으면 source type과 join grain을 먼저 확인"
            style={{...I,resize:"vertical",lineHeight:1.55}}/>
        </div>
        <div style={{display:"grid",gap:8}}>
          <div>
            <div style={L}>기대 intent</div>
            <input value={adminUpdate.expected_intent} onChange={e=>patchAdminUpdate({expected_intent:e.target.value})} placeholder="dashboard_scatter_plan" style={{...I,fontFamily:"monospace"}}/>
          </div>
          <div>
            <div style={L}>기대 동작/tool</div>
            <input value={adminUpdate.expected_tool} onChange={e=>patchAdminUpdate({expected_tool:e.target.value})} placeholder="build_metric_scatter" style={{...I,fontFamily:"monospace"}}/>
          </div>
          <div>
            <div style={L}>정답 DB/컬럼</div>
            <input value={adminUpdate.data_refs} onChange={e=>patchAdminUpdate({data_refs:e.target.value})} placeholder="INLINE item_id=..., ET item_id=..." style={{...I,fontFamily:"monospace"}}/>
          </div>
        </div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,marginTop:10}}>
        <div>
          <div style={L}>기대 답변 / 정답 경로</div>
          <textarea value={adminUpdate.expected_answer} onChange={e=>patchAdminUpdate({expected_answer:e.target.value})} rows={4}
            placeholder="Flow-i가 따라야 할 조회 순서, 집계 기준, 확인 질문 기준"
            style={{...I,resize:"vertical",lineHeight:1.5}}/>
        </div>
        <div>
          <div style={L}>운영 메모</div>
          <textarea value={adminUpdate.notes} onChange={e=>patchAdminUpdate({notes:e.target.value})} rows={4}
            placeholder="관리자가 리뷰한 이유, 적용 범위, 금지 조건"
            style={{...I,resize:"vertical",lineHeight:1.5}}/>
        </div>
      </div>
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
      <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap",marginBottom:12}}>
        <div>
          <div style={{fontSize:14,fontWeight:800}}>Flow-i 운영 기본값</div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:3}}>홈 Flow-i 차트와 피드백 운영 정책입니다. RAG 지식 등록은 에이전트 페이지에서 처리합니다.</div>
        </div>
        <span style={{flex:1}}/>
        <Button variant="ghost" onClick={reloadDefaults} disabled={defaultsBusy}>불러오기</Button>
        <Button onClick={saveDefaults} disabled={defaultsBusy}>{defaultsBusy?"저장 중":"저장"}</Button>
      </div>
      {defaultsMsg&&<div style={{fontSize:14,color:defaultsMsg.includes("오류")?BAD.fg:OK.fg,marginBottom:10}}>{defaultsMsg}</div>}
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(190px,1fr))",gap:10}}>
        <div>
          <div style={L}>Scatter grain</div>
          <select value={scatter.grain||"wafer_agg"} onChange={e=>patchChart("scatter",{grain:e.target.value})} style={I}>
            <option value="wafer_agg">WF Agg</option>
            <option value="shot">Shot</option>
            <option value="die">Die</option>
            <option value="map">Map</option>
          </select>
        </div>
        <div>
          <div style={L}>Scatter max points</div>
          <input type="number" min={50} max={5000} value={scatter.max_points||500} onChange={e=>patchChart("scatter",{max_points:Number(e.target.value)||500})} style={{...I,fontFamily:"monospace"}}/>
        </div>
        <div>
          <div style={L}>INLINE agg</div>
          <select value={scatter.inline_agg||"avg"} onChange={e=>patchChart("scatter",{inline_agg:e.target.value})} style={I}>
            <option value="avg">avg</option>
            <option value="median">median</option>
          </select>
        </div>
        <div>
          <div style={L}>ET agg</div>
          <select value={scatter.et_agg||"median"} onChange={e=>patchChart("scatter",{et_agg:e.target.value})} style={I}>
            <option value="median">median</option>
            <option value="avg">avg</option>
          </select>
        </div>
        <div>
          <div style={L}>Line max / series</div>
          <input type="number" min={20} max={1000} value={line.max_points_per_series||120} onChange={e=>patchChart("line",{max_points_per_series:Number(e.target.value)||120})} style={{...I,fontFamily:"monospace"}}/>
        </div>
        <div>
          <div style={L}>Bar top N</div>
          <input type="number" min={3} max={50} value={bar.top_n||12} onChange={e=>patchChart("bar",{top_n:Number(e.target.value)||12})} style={{...I,fontFamily:"monospace"}}/>
        </div>
        <div>
          <div style={L}>Pie max slices</div>
          <input type="number" min={3} max={20} value={pie.max_slices||6} onChange={e=>patchChart("pie",{max_slices:Number(e.target.value)||6})} style={{...I,fontFamily:"monospace"}}/>
        </div>
        <div>
          <div style={L}>Box groups / min N</div>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6}}>
            <input type="number" min={3} max={50} value={box.max_groups||12} onChange={e=>patchChart("box",{max_groups:Number(e.target.value)||12})} style={{...I,fontFamily:"monospace"}}/>
            <input type="number" min={1} max={30} value={box.min_n||3} onChange={e=>patchChart("box",{min_n:Number(e.target.value)||3})} style={{...I,fontFamily:"monospace"}}/>
          </div>
        </div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))",gap:10,marginTop:12}}>
        <label style={{display:"flex",alignItems:"center",gap:8,fontSize:14,color:"var(--text-primary)",padding:"8px 10px",borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
          <input type="checkbox" checked={false} disabled onChange={()=>{}}/>
          피드백 RAG 자동반영 비활성
        </label>
        <label style={{display:"flex",alignItems:"center",gap:8,fontSize:14,color:"var(--text-primary)",padding:"8px 10px",borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
          <input type="checkbox" checked={policy.review_required!==false} onChange={e=>patchPolicy({review_required:e.target.checked})}/>
          피드백 리뷰 후 Golden 승격
        </label>
      </div>
    </div>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:10}}>
      {smallCount("총 피드백",data?.total||0)}
      {smallCount("정확함",data?.by_rating?.up||0,"ok")}
      {smallCount("개선 필요",data?.by_rating?.down||0,"warn")}
      {smallCount("리뷰 큐",review.length,review.length?"bad":"ok")}
      {smallCount("Golden case",golden.length,"ok")}
    </div>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(260px,1fr))",gap:12}}>
      {counterList("실패 유형",data?.by_tag||{},BAD.fg)}
      {counterList("의도별",data?.by_intent||{},INFO.fg)}
      {counterList("유저별",data?.by_user||{},OK.fg)}
    </div>
    <div style={{display:"grid",gridTemplateColumns:"minmax(0,1.15fr) minmax(320px,0.85fr)",gap:14,alignItems:"start"}}>
      <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:10}}>
          <div style={{fontSize:14,fontWeight:800}}>리뷰 큐</div>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>개선 필요, 실패 유형, golden 후보</span>
        </div>
        <div style={{display:"grid",gap:10,maxHeight:720,overflow:"auto"}}>
          {review.length?review.map(feedbackCard):<div style={{fontSize:14,color:"var(--text-secondary)",padding:20,textAlign:"center"}}>리뷰할 피드백이 없습니다.</div>}
        </div>
      </div>
      <div style={{display:"grid",gap:12}}>
        <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
          <div style={{fontSize:14,fontWeight:800,marginBottom:10}}>최근 Golden cases</div>
          <div style={{display:"grid",gap:8,maxHeight:280,overflow:"auto"}}>
            {golden.slice(0,12).map(g=><div key={g.id} style={{padding:9,borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
              <div style={{fontSize:14,fontFamily:"monospace",color:"var(--accent)",marginBottom:4}}>{g.expected_intent||"-"} · {g.expected_tool||"-"}</div>
              <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.45}}>{g.prompt||"-"}</div>
            </div>)}
            {!golden.length&&<div style={{fontSize:14,color:"var(--text-secondary)"}}>아직 저장된 golden case가 없습니다.</div>}
          </div>
        </div>
        <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
          <div style={{fontSize:14,fontWeight:800,marginBottom:10}}>최근 전체 피드백</div>
          <div style={{display:"grid",gap:8,maxHeight:360,overflow:"auto"}}>
            {recent.slice(0,20).map((r,i)=><div key={r.id||i} style={{padding:8,borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
              <div style={{display:"flex",gap:6,alignItems:"center",marginBottom:4}}>
                <Pill tone={toneFor(r.rating,r.tags)}>{r.rating}</Pill>
                <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{String(r.timestamp||"").slice(5,16).replace("T"," ")}</span>
                <span style={{fontSize:14,color:"var(--text-secondary)"}}>{r.username}</span>
              </div>
              <div style={{fontSize:14,color:"var(--text-secondary)",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={r.prompt_excerpt}>{r.prompt_excerpt||"-"}</div>
            </div>)}
            {!recent.length&&<div style={{fontSize:14,color:"var(--text-secondary)"}}>피드백 없음</div>}
          </div>
        </div>
      </div>
    </div>
  </div>);
}

// ── v8.8.27: Inline 실명(name) 편집기 — Users 테이블에서 admin 이 즉시 수정. ──
// v8.8.28: onSave 미지정 방어 + safeCall 로 "n is not a function" 류 런타임 에러 차단.
function NameInlineEdit({u,onSave}){
  const[val,setVal]=useState(u?.name||"");
  const[edit,setEdit]=useState(false);
  useEffect(()=>{setVal(u?.name||"");},[u?.name]);
  const safeSave=(v)=>{try{(typeof onSave==="function")&&onSave(v);}catch(e){console.warn("[NameInlineEdit] onSave threw",e);}};
  if(!edit){
    return(<span onClick={()=>setEdit(true)} style={{cursor:"pointer",color:val?"var(--text-primary)":"var(--text-secondary)",textDecoration:"underline dotted",textDecorationColor:"var(--border)",fontWeight:val?600:400}}>{val||"— 이름 —"}</span>);
  }
  return(<span>
    <input autoFocus value={val} onChange={e=>setVal(e.target.value)}
      onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;safeSave(val.trim());setEdit(false);}else if(e.key==="Escape"){setVal(u?.name||"");setEdit(false);}}}
      placeholder="이름"
      style={{padding:"3px 6px",borderRadius:3,border:"1px solid var(--accent)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,minWidth:140}}/>
    <span onClick={()=>{safeSave(val.trim());setEdit(false);}} style={{marginLeft:6,cursor:"pointer",color:OK.fg,fontSize:14}}>✔</span>
    <span onClick={()=>{setVal(u?.name||"");setEdit(false);}} style={{marginLeft:4,cursor:"pointer",color:BAD.fg,fontSize:14}}>✕</span>
  </span>);
}

// ── v8.7.2/v8.8.18: 사내 메일 API 연동 설정 패널 ──
// v8.8.18: recipient_groups 제거 (수신자는 각 페이지에서 선택). dep_ticket 단일 필드
//          + API 전체 틀 JSON 미리보기. senderMailAddress/statusCode/url 만 남김.
function MailCfgPanel(){
  const[cfg,setCfg]=useState({api_url:"",dep_ticket:"",from_addr:"",status_code:"",domain:"",enabled:false});
  const[msg,setMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const reload=()=>{
    sf("/api/admin/settings").then(d=>{
      const m=d.mail||{};
      // dep_ticket 필드가 없으면 headers["x-dep-ticket"] 에서 추출 (backward compat).
      const dt=(m.dep_ticket||"").toString().trim()||((m.headers||{})["x-dep-ticket"]||"");
      setCfg({api_url:m.api_url||"",dep_ticket:dt,from_addr:m.from_addr||"",status_code:m.status_code||"",domain:(m.domain||"").replace(/^@/,""),enabled:!!m.enabled});
    }).catch(()=>{});
  };
  useEffect(()=>{reload();},[]);
  const save=()=>{
    setBusy(true);setMsg("");
    sf("/api/admin/settings").then(cur=>{
      return sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        dashboard_refresh_minutes:cur.dashboard_refresh_minutes||10,
        dashboard_bg_refresh_minutes:cur.dashboard_bg_refresh_minutes||10,
        mail:{
          api_url:cfg.api_url,
          dep_ticket:cfg.dep_ticket,
          from_addr:cfg.from_addr,
          status_code:cfg.status_code,
          domain:(cfg.domain||"").replace(/^@/,""),
          enabled:cfg.enabled,
        },
      })});
    }).then(()=>{setMsg("✔ 저장됨");setBusy(false);reload();}).catch(e=>{setMsg("오류: "+e.message);setBusy(false);});
  };

  // v8.8.18: API 전체 틀 preview — admin 이 저장 전에 실제 request 모양을 확인.
  // v8.8.19: domain 설정이 있으면 username-only 샘플도 합성.
  const _dom=(cfg.domain||"").replace(/^@/,"");
  const _combine=(un)=>_dom && !un.includes("@") ? `${un}@${_dom}` : un;
  // v9.0.0: 실제 전송 구조 정합 — multipart/form-data 의 top-level form field 는
  //   `mailsendString` (소문자 s) 키이고, 값은 JSON 직렬화된 data_obj 문자열.
  //   과거 미리보기는 data 안에 필드를 평면 나열해 실제 구조와 달랐음.
  const _dataObj = {
    content: "(본문 HTML)",
    receiverList: [
      {email: _combine("user1"), recipientType: "TO", seq: 1},
      {email: _combine("user2"), recipientType: "TO", seq: 2},
    ],
    senderMailAddress: cfg.from_addr || _combine("sender") || "(설정 필요)",
    statusCode: cfg.status_code || "",
    title: "(제목)",
  };
  // v9.0.0 (Q4+가독성): 미리보기에서 mailSendString 값을 escape 된 JSON string 대신
  //   실제 객체 구조 그대로 표시. 실제 전송 시에는 이 객체를 JSON.stringify() 하여
  //   top-level form field "mailSendString" 의 값으로 넣는다 (아래 주석 참조).
  const preview={
    url: cfg.api_url || "(설정 필요)",
    headers: cfg.dep_ticket ? {"x-dep-ticket": cfg.dep_ticket} : {},
    data: {
      mailSendString: _dataObj,  // 실제 전송 시 JSON.stringify(_dataObj) 로 직렬화됨
    },
    files: [
      ["file", ["attachment.xlsx", "(binary)", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]],
    ],
  };

  const L={fontSize:14,color:"var(--text-secondary)",marginBottom:4,marginTop:10,fontWeight:600};
  const I={width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",fontFamily:"'Segoe UI',Arial,sans-serif"};
  return(<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:900}}>
    <div style={{fontSize:14,fontWeight:700,marginBottom:4}}>✉ 메일 API 설정</div>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.6}}>
      사내 메일 API 규약: <code>multipart/form-data</code> POST.  top-level form field 는 <b><code>mailsendString</code></b> 한 개
      (값 = <code>{"{content, receiverList, senderMailAddress, statusCode, title}"}</code> 를 JSON 직렬화한 문자열),
      그리고 첨부가 있으면 <code>files</code> parts.  2MB 본문 / 10MB 첨부 한도.  URL 에 <code>dry-run</code> 입력 시 실제 전송 없이 preview 만 반환.<br/>
      <b>수신자는 각 페이지의 메일 발송 다이얼로그에서 선택</b> — Admin 에서 그룹 관리하지 않음.
    </div>
    <label style={{display:"flex",alignItems:"center",gap:6,fontSize:14,marginBottom:6}}>
      <input type="checkbox" checked={!!cfg.enabled} onChange={e=>setCfg({...cfg,enabled:e.target.checked})}/>
      메일 기능 활성화
    </label>
    <div style={L}>API URL</div>
    <input value={cfg.api_url} onChange={e=>setCfg({...cfg,api_url:e.target.value})} placeholder="https://mail.internal/api/send  (또는 'dry-run')" style={I}/>
    <div style={L}>x-dep-ticket <span style={{fontWeight:400,color:"var(--text-secondary)"}}>(요청 헤더에 자동 첨부)</span></div>
    <input value={cfg.dep_ticket} onChange={e=>setCfg({...cfg,dep_ticket:e.target.value})} placeholder="사내 발급 티켓값" style={{...I,fontFamily:"monospace"}}/>
    <div style={{display:"flex",gap:10}}>
      <div style={{flex:2}}>
        <div style={L}>senderMailAddress (기본 발신자)</div>
        <input value={cfg.from_addr} onChange={e=>setCfg({...cfg,from_addr:e.target.value})} placeholder="flow-noreply@company.com" style={I}/>
      </div>
      <div style={{flex:1}}>
        <div style={L}>statusCode 기본값</div>
        <input value={cfg.status_code} onChange={e=>setCfg({...cfg,status_code:e.target.value})} placeholder="예: NORMAL" style={I}/>
      </div>
    </div>
    {/* v8.8.19: 이메일 도메인 — username-only 값 뒤에 자동 합성. */}
    <div style={L}>메일 도메인 <span style={{fontWeight:400,color:"var(--text-secondary)"}}>
      (선택 — '@' 없이 도메인만. 예: <code>company.co.kr</code>. username 이 이메일 포맷이 아닐 때 <code>&lt;username&gt;@&lt;domain&gt;</code> 로 자동 조합)
    </span></div>
    <input value={cfg.domain} onChange={e=>setCfg({...cfg,domain:e.target.value.replace(/^@/,"")})} placeholder="company.co.kr" style={{...I,fontFamily:"monospace"}}/>

    {/* v8.8.18: API 전체 틀 미리보기 */}
    <div style={{marginTop:18,padding:12,background:"var(--bg-card)",borderRadius:6,border:"1px solid var(--border)"}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:6}}>🔍 전체 API 틀 미리보기</div>
      <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:6,lineHeight:1.5}}>
        현재 저장된 설정 기반으로 실제 호출 시 전송될 request 구조. 본문/제목/수신자는 인폼·회의 등 발송 화면에서 채워집니다.
      </div>
      <pre style={{fontSize:14,lineHeight:1.45,padding:10,background:"var(--bg-primary)",border:"1px solid var(--border)",borderRadius:4,overflow:"auto",maxHeight:360,fontFamily:"monospace",margin:0,color:"var(--text-primary)"}}>
{JSON.stringify(preview, null, 2)}
      </pre>
    </div>

    <div style={{marginTop:14,display:"flex",gap:8,alignItems:"center"}}>
      <button onClick={save} disabled={busy} style={{padding:"8px 18px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:busy?"wait":"pointer"}}>{busy?"저장 중…":"저장"}</button>
      {msg&&<span style={{fontSize:14,color:msg.startsWith("오류")?BAD.fg:OK.fg}}>{msg}</span>}
    </div>
  </div>);
}

// ── v9.0.4: Flowi LLM 설정 — admin token 을 서버 설정에 저장하고 사용자는 실행만 한다. ──
// ── Data Roots Panel (v8.3.0 + backup v8.7.0) ──
function DataRootsPanel(){
  const[eff,setEff]=useState({db_root:"",sources:{}});
  const[form,setForm]=useState({db_root:""});
  const[backup,setBackup]=useState({path:"",interval_hours:24,keep:5,enabled:true,last:{}});
  const[backupList,setBackupList]=useState([]);
  const[bkBusy,setBkBusy]=useState(false);
  const[msg,setMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const reload=()=>{
    sf("/api/admin/settings").then(d=>{
      const dr=d.data_roots||{db_root:"",sources:{}};
      setEff(dr);
      if(d.backup)setBackup(prev=>({...prev,...d.backup}));
    }).catch(e=>setMsg("로드 오류: "+e.message));
    sf("/api/admin/backup/status").then(d=>{
      if(d.settings)setBackup(b=>({...b,...d.settings}));
      setBackupList(d.backups||[]);
    }).catch(()=>{});
  };
  useEffect(()=>{reload();},[]);
  const saveBackup=()=>{
    setBkBusy(true);
    sf("/api/admin/settings").then(cur=>sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      dashboard_refresh_minutes:cur.dashboard_refresh_minutes??10,
      dashboard_bg_refresh_minutes:cur.dashboard_bg_refresh_minutes??10,
      backup:{path:backup.path||"",interval_hours:Number(backup.interval_hours)||24,keep:Number(backup.keep)||5,enabled:!!backup.enabled},
    })})).then(()=>{setMsg("백업 설정 저장됨");reload();}).catch(e=>setMsg("저장 오류: "+e.message)).finally(()=>setBkBusy(false));
  };
  const runBackupNow=()=>{
    setBkBusy(true);
    sf("/api/admin/backup/run",{method:"POST"}).then(r=>{
      if(r.ok)setMsg("백업 완료: "+r.path+" ("+(r.bytes||0).toLocaleString()+" bytes)");
      else setMsg("백업 실패: "+(r.error||"unknown"));
      reload();
    }).catch(e=>setMsg("백업 오류: "+e.message)).finally(()=>setBkBusy(false));
  };
  const restoreBackupNow=(b)=>{
    if(!confirm(`${b.filename} 백업으로 data_root 를 롤백할까요?\n현재 상태는 pre-restore 백업으로 먼저 저장됩니다.`))return;
    setBkBusy(true);
    sf("/api/admin/backup/restore",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename:b.filename})})
      .then(r=>{setMsg(`롤백 완료: ${r.restored||0} files (pre: ${r.pre_restore_backup||"-"})`);reload();})
      .catch(e=>setMsg("롤백 오류: "+e.message))
      .finally(()=>setBkBusy(false));
  };
  const save=()=>{
    setBusy(true);setMsg("");
    const payload={
      // Preserve existing refresh settings when admin clicks Save on this panel:
      // backend re-clamps whatever we send. Fetch current refresh values first.
    };
    sf("/api/admin/settings").then(cur=>{
      return sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          dashboard_refresh_minutes: cur.dashboard_refresh_minutes??10,
          dashboard_bg_refresh_minutes: cur.dashboard_bg_refresh_minutes??10,
          data_roots: {db_root:form.db_root||""},
        })});
    }).then(()=>{setMsg("저장되었습니다. 새 요청부터 적용됩니다.");setForm({db_root:""});reload();})
      .catch(e=>setMsg("저장 오류: "+e.message))
      .finally(()=>setBusy(false));
  };
  const L={fontSize:14,fontWeight:600,marginBottom:4,color:"var(--text-primary)"};
  const I={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",
           background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",
           fontFamily:"monospace",boxSizing:"border-box"};
  const H={fontSize:14,color:"var(--text-secondary)",marginTop:4,fontFamily:"monospace"};
  const srcBadge=(s)=>{const map={env:INFO.fg,settings:OK.fg,default:SLATE};
    return<span style={{fontSize:14,padding:"1px 6px",borderRadius:3,background:(map[s]||SLATE)+"22",color:map[s]||SLATE,fontWeight:700,marginLeft:6}}>{s}</span>;};
  const field=(key,label,envHint)=>{
    const currentEff=eff[key]||"(unresolved)";
    const src=(eff.sources||{})[key]||"default";
    const hint=src==="env"?`(env: ${envHint})`:src==="settings"?"(settings)":"(default)";
    return(<div data-dr-key={key} style={{marginBottom:14}}>
      <div style={L}>{label} {srcBadge(src)}</div>
      <input data-dr-input={key}
        value={form[key]||""}
        onChange={e=>setForm({...form,[key]:e.target.value})}
        placeholder={`${currentEff}  ${hint}`}
        style={I}/>
      <div style={H} data-dr-effective={key}>현재 effective: {currentEff} <span style={{opacity:0.7}}>{hint}</span></div>
    </div>);
  };
  return(<div data-admin-panel="data_roots" style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:760}}>
    <div style={{fontSize:15,fontWeight:700,marginBottom:6}}>📂 데이터 루트 (소프트랜딩)</div>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:16,lineHeight:1.5}}>
      flow 는 기본적으로 <b>DB 루트 하나</b>만 받습니다. 로컬 checkout 기본값은
      <span style={{fontFamily:"monospace"}}> data/Fab </span>,
      prod 앱 루트 또는 FLOW_PROD=1 에서는
      <span style={{fontFamily:"monospace"}}> /config/work/sharedworkspace/DB </span>
      입니다. 단일 파일(rulebook, ML_TABLE, features parquet)도 DB 루트 최상단에서 읽습니다.
      우선순위: <b>FLOW env → admin_settings.data_roots → default</b>.
      DB 루트는 이미 존재하는 디렉터리만 저장됩니다. 빈 값으로 저장하면 오버라이드가 제거되고 env/default 로 돌아갑니다.
    </div>
    {field("db_root","DB 루트","FLOW_DB_ROOT")}
    <div style={{display:"flex",gap:8,marginTop:16,alignItems:"center"}}>
      <button data-dr-btn="save" onClick={save} disabled={busy}
        style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:busy?"default":"pointer",opacity:busy?0.5:1}}>
        {busy?"저장 중...":"저장"}
      </button>
      <button data-dr-btn="reload" onClick={reload} disabled={busy}
        style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>
        새로고침
      </button>
      {msg&&<span data-dr-msg style={{fontSize:14,color:(msg.includes("완료")||msg.includes("저장"))?OK.fg:BAD.fg}}>{msg}</span>}
    </div>

    {/* v8.7.0: 백업 설정 */}
    <div style={{marginTop:28,paddingTop:20,borderTop:"1px solid var(--border)"}}>
      <div style={{fontSize:15,fontWeight:700,marginBottom:6}}>💾 자동 백업</div>
      <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:12,lineHeight:1.5}}>
        data_root 전체와 DB 루트 최상단 설정 파일을 zip 스냅샷으로 백업합니다.
        서버 기동 시 1회 + 설정된 주기로 자동 실행. 보관개수 초과 시 오래된 백업부터 자동 삭제.
        경로를 비워두면 현재 <span style={{fontFamily:"monospace"}}>/config/work/sharedworkspace</span> 를 자동 사용합니다.
      </div>
      <div style={{display:"grid",gridTemplateColumns:"2fr 1fr 1fr 1fr",gap:10,alignItems:"end"}}>
        <div>
          <div style={L}>백업 경로 (비워두면 /config/work/sharedworkspace 자동)</div>
          <input value={backup.path||""} onChange={e=>setBackup({...backup,path:e.target.value})}
            placeholder="예: /config/work/sharedworkspace"
            style={I}/>
        </div>
        <div>
          <div style={L}>주기 (시간)</div>
          <input type="number" min={1} max={168} value={backup.interval_hours||24}
            onChange={e=>setBackup({...backup,interval_hours:Number(e.target.value)})} style={I}/>
        </div>
        <div>
          <div style={L}>보관 개수 (최대 5)</div>
          <input type="number" min={1} max={5} value={backup.keep||5}
            onChange={e=>setBackup({...backup,keep:Number(e.target.value)})} style={I}/>
        </div>
        <div>
          <div style={L}>활성</div>
          <label style={{display:"flex",alignItems:"center",gap:6,padding:"8px 0"}}>
            <input type="checkbox" checked={!!backup.enabled} onChange={e=>setBackup({...backup,enabled:e.target.checked})}/>
            <span style={{fontSize:14}}>스케줄러 on/off</span>
          </label>
        </div>
      </div>
      <div style={{display:"flex",gap:8,marginTop:12,alignItems:"center",flexWrap:"wrap"}}>
        <button onClick={saveBackup} disabled={bkBusy}
          style={{padding:"8px 16px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:bkBusy?"default":"pointer",opacity:bkBusy?0.5:1}}>
          {bkBusy?"처리 중...":"설정 저장"}
        </button>
        <button onClick={runBackupNow} disabled={bkBusy}
          style={{padding:"8px 16px",borderRadius:6,border:`1px solid ${OK.fg}`,background:"transparent",color:OK.fg,fontWeight:600,cursor:bkBusy?"default":"pointer"}}>
          💾 지금 백업
        </button>
        {backup.last&&backup.last.at&&(
          <span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:6}}>
            마지막: {(backup.last.at||"").replace("T"," ")} ·
            {backup.last.ok?<span style={{color:OK.fg}}> ok ({(backup.last.bytes||0).toLocaleString()}B)</span>
                           :<span style={{color:BAD.fg}}> 실패 {backup.last.error}</span>}
          </span>
        )}
      </div>
      {backupList.length>0&&(
        <div style={{marginTop:14,maxHeight:220,overflow:"auto",border:"1px solid var(--border)",borderRadius:6}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace"}}>
            <thead><tr>
              <th style={{textAlign:"left",padding:"6px 10px",background:"var(--bg-primary)",position:"sticky",top:0}}>파일</th>
              <th style={{textAlign:"right",padding:"6px 10px",background:"var(--bg-primary)",position:"sticky",top:0}}>크기</th>
              <th style={{textAlign:"left",padding:"6px 10px",background:"var(--bg-primary)",position:"sticky",top:0}}>시각</th>
              <th style={{textAlign:"right",padding:"6px 10px",background:"var(--bg-primary)",position:"sticky",top:0}}>작업</th>
            </tr></thead>
            <tbody>
              {backupList.map(b=>(
                <tr key={b.filename}>
                  <td style={{padding:"4px 10px",borderTop:"1px solid var(--border)"}} title={b.path}>{b.filename}</td>
                  <td style={{padding:"4px 10px",borderTop:"1px solid var(--border)",textAlign:"right"}}>{(b.size||0).toLocaleString()}</td>
                  <td style={{padding:"4px 10px",borderTop:"1px solid var(--border)"}}>{(b.modified||"").replace("T"," ")}</td>
                  <td style={{padding:"4px 10px",borderTop:"1px solid var(--border)",textAlign:"right"}}>
                    <button onClick={()=>restoreBackupNow(b)} disabled={bkBusy}
                      style={{padding:"3px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:WARN.fg,cursor:bkBusy?"default":"pointer",fontSize:14}}>롤백</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  </div>);
}

function CategoryPanel(){
  // v8.1.5: cats = [{name, color}, ...]  (backend auto-upgrades legacy str list)
  const[cats,setCats]=useState([]);const[newCat,setNewCat]=useState("");const[newColor,setNewColor]=useState("#3b82f6");
  const[editIdx,setEditIdx]=useState(-1);const[editVal,setEditVal]=useState("");const[msg,setMsg]=useState("");
  const[usage,setUsage]=useState({counts:{},orphans:{},total:0});
  const[migrateBusy,setMigrateBusy]=useState(false);
  const load=()=>{
    sf("/api/tracker/categories").then(d=>setCats((d.categories||[]).map(c=>typeof c==="string"?{name:c,color:"#64748b"}:c))).catch(()=>{});
    sf("/api/tracker/categories/usage").then(d=>setUsage(d||{counts:{},orphans:{},total:0})).catch(()=>{});
  };
  useEffect(()=>{load();},[]);
  const save=(next)=>sf("/api/tracker/categories/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(next)}).then(()=>{setCats(next);setMsg("저장됨 ✓");setTimeout(()=>setMsg(""),1500);load();}).catch(e=>setMsg("오류: "+e.message));
  const rerunTrackerSchema=()=>{
    setMigrateBusy(true);setMsg("");
    sf("/api/admin/tracker-schema-migrate",{method:"POST"})
      .then(d=>setMsg(`트래커 스키마 재마이그레이션 완료 · changed=${d.changed?"yes":"no"} · lots=${d.lots_updated||0}`))
      .catch(e=>setMsg("오류: "+e.message))
      .finally(()=>setMigrateBusy(false));
  };
  const add=()=>{const v=newCat.trim();if(!v||cats.some(c=>c.name===v))return;save([...cats,{name:v,color:newColor}]);setNewCat("");setNewColor("#3b82f6");};
  const del=(i)=>{if(!confirm(`"${cats[i].name}" 을(를) 삭제하시겠습니까?`))return;save(cats.filter((_,j)=>j!==i));};
  const startEdit=(i)=>{setEditIdx(i);setEditVal(cats[i].name);};
  const saveEdit=()=>{const v=editVal.trim();if(!v){setEditIdx(-1);return;}const next=cats.map((c,i)=>i===editIdx?{...c,name:v}:c);save(next);setEditIdx(-1);};
  const setColor=(i,color)=>{const next=cats.map((c,j)=>j===i?{...c,color}:c);save(next);};
  const move=(i,dir)=>{const j=i+dir;if(j<0||j>=cats.length)return;const next=[...cats];[next[i],next[j]]=[next[j],next[i]];save(next);};
  const S={padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"};
  return(<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:620}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
      <span style={{fontSize:14,fontWeight:700}}>트래커 카테고리</span>
      {msg&&<span style={{fontSize:14,color:msg.startsWith("오류")?BAD.fg:OK.fg,fontFamily:"monospace"}}>{msg}</span>}
    </div>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:10,marginBottom:12,flexWrap:"wrap"}}>
      <div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5}}>
        LOT_WF 확장 필드가 누락된 기존 tracker/issues.json 은 여기서 다시 마이그레이션할 수 있습니다.
      </div>
      <button onClick={rerunTrackerSchema} disabled={migrateBusy} style={{padding:"8px 14px",borderRadius:6,border:"1px solid var(--border)",background:migrateBusy?"var(--bg-tertiary)":"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontWeight:700,cursor:migrateBusy?"default":"pointer",opacity:migrateBusy?0.7:1}}>
        {migrateBusy?"재마이그레이션 중...":"트래커 스키마 재마이그레이션"}
      </button>
    </div>
    <div style={{display:"flex",gap:8,marginBottom:14,alignItems:"center"}}>
      <input type="color" value={newColor} onChange={e=>setNewColor(e.target.value)} style={{width:40,height:36,padding:0,border:"1px solid var(--border)",borderRadius:6,cursor:"pointer",background:"transparent"}} title="카테고리 색상"/>
      <input value={newCat} onChange={e=>setNewCat(e.target.value)} placeholder="새 카테고리 이름" onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;add();}}} style={{...S,flex:1}}/>
      <button onClick={add} disabled={!newCat.trim()} style={{padding:"8px 16px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:"pointer",opacity:newCat.trim()?1:0.5}}>+ 추가</button>
    </div>
    <div style={{border:"1px solid var(--border)",borderRadius:8,overflow:"hidden"}}>
      {cats.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>카테고리 없음</div>}
      {cats.map((c,i)=>{const n=usage.counts?.[c.name]||0;return(<div key={i} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 12px",borderBottom:i<cats.length-1?"1px solid var(--border)":"none",background:editIdx===i?"var(--accent-glow)":"transparent"}}>
        <span style={{fontSize:14,color:"var(--text-secondary)",minWidth:22,fontFamily:"monospace"}}>{(i+1).toString().padStart(2,"0")}</span>
        <input type="color" value={c.color||"#64748b"} onChange={e=>setColor(i,e.target.value)} style={{width:26,height:26,padding:0,border:"1px solid var(--border)",borderRadius:4,cursor:"pointer",background:"transparent",flexShrink:0}} title="클릭하여 색상 선택"/>
        {editIdx===i
          ?<input autoFocus value={editVal} onChange={e=>setEditVal(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;saveEdit();}}} onBlur={saveEdit} style={{...S,flex:1,padding:"4px 8px",fontSize:14}}/>
          :<span style={{flex:1,fontSize:14,cursor:"pointer",display:"flex",alignItems:"center",gap:6}} onClick={()=>startEdit(i)}><span style={{width:8,height:8,borderRadius:"50%",background:c.color||"#64748b",flexShrink:0}}/>{c.name}</span>}
        <span style={{fontSize:14,color:n>0?"var(--accent)":"var(--text-secondary)",fontFamily:"monospace",padding:"1px 6px",borderRadius:10,background:n>0?"var(--accent-glow)":"transparent",minWidth:28,textAlign:"center"}}>{n}</span>
        <span onClick={()=>move(i,-1)} style={{cursor:i===0?"not-allowed":"pointer",opacity:i===0?0.3:0.8,fontSize:14,color:"var(--text-secondary)",padding:"2px 4px"}}>▲</span>
        <span onClick={()=>move(i,1)} style={{cursor:i===cats.length-1?"not-allowed":"pointer",opacity:i===cats.length-1?0.3:0.8,fontSize:14,color:"var(--text-secondary)",padding:"2px 4px"}}>▼</span>
        <span onClick={()=>startEdit(i)} style={{cursor:"pointer",fontSize:14,color:INFO.fg,padding:"2px 6px"}}>편집</span>
        <span onClick={()=>{if(n>0&&!confirm(`"${c.name}" 은(는) ${n}개 이슈에서 사용 중입니다. 그래도 삭제하시겠습니까? 기존 이슈는 고아(orphan) 상태가 됩니다.`))return;del(i);}} style={{cursor:"pointer",fontSize:14,color:BAD.fg,padding:"2px 6px"}}>삭제</span>
      </div>);})}
      {Object.keys(usage.orphans||{}).length>0&&<div style={{padding:"10px 12px",background:"rgba(239,68,68,0.08)",borderTop:"1px solid var(--border)"}}>
        <div style={{fontSize:14,fontWeight:700,color:BAD.fg,marginBottom:4}}>⚠ 고아 카테고리 (이슈에서 사용 중이나 목록에 없음)</div>
        {Object.entries(usage.orphans).map(([oc,n])=>(<div key={oc} style={{display:"flex",justifyContent:"space-between",fontSize:14,fontFamily:"monospace",marginBottom:2}}>
          <span>{oc}</span>
          <span style={{color:"var(--text-secondary)"}}>{n}개 이슈 — <span onClick={()=>{if(confirm(`"${oc}" 을(를) 카테고리 목록에 복원하시겠습니까?`))save([...cats,{name:oc,color:"#64748b"}]);}} style={{cursor:"pointer",color:INFO.fg}}>복원</span></span>
        </div>))}
      </div>}
    </div>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:10,lineHeight:1.5}}>색상 원 클릭으로 카테고리 색 변경. 이 색상은 트래커 이슈 리스트 prefix, Gantt bar, 상세 뷰에 반영됩니다.</div>
  </div>);
}

function CatalogPanel(){
  const[sub,setSub]=useState("matching");
  const tS=(a)=>({padding:"6px 14px",fontSize:14,fontFamily:"monospace",cursor:"pointer",fontWeight:a?700:400,borderBottom:a?"2px solid var(--accent)":"2px solid transparent",color:a?"var(--accent)":"var(--text-secondary)"});
  return(<div>
    <div style={{display:"flex",gap:4,borderBottom:"1px solid var(--border)",marginBottom:16}}>
      {[["matching","🔗 매칭 테이블"],["product","📋 Product 설정"],["s3","☁ S3 동기화"]].map(([k,l])=>(<div key={k} style={tS(sub===k)} onClick={()=>setSub(k)}>{l}</div>))}
    </div>
    {sub==="matching"&&<MatchingPanel/>}
    {sub==="product"&&<ProductPanel/>}
    {sub==="s3"&&<S3Panel/>}
  </div>);
}

// v8.2.1: color chip for a process area cell
function AreaChip({value}){
  if(!value)return(<span style={{color:"#64748b",fontStyle:"italic"}}>—</span>);
  const bg=areaColor(value);
  return(<span style={{display:"inline-flex",alignItems:"center",gap:5,padding:"1px 7px",borderRadius:10,background:bg+"22",border:"1px solid "+bg,fontSize:14,fontFamily:"monospace",color:bg,fontWeight:700}}>
    <span style={{width:7,height:7,borderRadius:"50%",background:bg}}/>{value}
  </span>);
}

function MatchingPanel(){
  const[tables,setTables]=useState([]);const[sel,setSel]=useState(null);const[preview,setPreview]=useState(null);
  // v8.2.1: local edits to area cells per row-index (undefined = unchanged)
  const[edits,setEdits]=useState({});const[saveMsg,setSaveMsg]=useState("");
  const[rollup,setRollup]=useState(null);
  const load=()=>sf("/api/catalog/matching/list").then(d=>setTables(d.tables||[]));
  // fix: arrow+Promise 를 useEffect 에 바로 넘기면 cleanup 자리에 Promise 가 들어가 unmount 시 crash ("n is not a function").
  useEffect(()=>{load();},[]);
  // v8.2.0: Bell dismiss / external read → re-load this tab's notif list immediately
  useEffect(()=>{
    const onRefresh=()=>load();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>window.removeEventListener("hol:notif-refresh",onRefresh);
  },[]);
  const loadPreview=(name)=>{
    setSel(name);setEdits({});setSaveMsg("");setRollup(null);
    sf("/api/catalog/matching/preview?name="+name+"&rows=30").then(setPreview).catch(()=>setPreview(null));
    if(name==="matching_step"){
      sf("/api/match/area-rollup").then(setRollup).catch(()=>setRollup(null));
    }
  };
  const download=(name)=>{dl("/api/catalog/matching/download?name="+encodeURIComponent(name), `${name}.csv`).catch(e=>toast.error("다운로드 실패: "+e.message));};
  const setAreaEdit=(i,v)=>setEdits(e=>({...e,[i]:v||null}));
  const hasAreaCol=sel==="matching_step"&&preview&&(preview.columns.includes("area")||preview.rows.some(r=>"area" in r));
  const saveAreas=()=>{
    if(!preview||!sel)return;
    // Merge edits back into rows, ensure area column exists
    const cols=Array.from(new Set([...(preview.columns||[]),"area"]));
    const rows=preview.rows.map((r,i)=>{
      const area=edits[i]!==undefined?edits[i]:(r.area||null);
      return {...r,area};
    });
    setSaveMsg("저장 중…");
    sf("/api/catalog/matching/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:sel,rows})})
      .then(()=>{
        setSaveMsg("저장됨 ✓");setEdits({});
        loadPreview(sel);
        setTimeout(()=>setSaveMsg(""),2500);
      })
      .catch(e=>setSaveMsg("⚠ "+e.message));
  };
  return(<div style={{display:"grid",gridTemplateColumns:"320px 1fr",gap:16}}>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:10,maxHeight:560,overflow:"auto"}}>
      <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginBottom:8,fontFamily:"monospace"}}>등록된 테이블 ({tables.length})</div>
      {tables.map(t=>(<div key={t.name} onClick={()=>loadPreview(t.name)} style={{padding:"8px 10px",borderRadius:6,cursor:"pointer",marginBottom:4,background:sel===t.name?"var(--accent-glow)":"var(--bg-primary)",border:"1px solid "+(sel===t.name?"var(--accent)":"var(--border)")}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <span style={{fontSize:14,fontWeight:700,fontFamily:"monospace",color:t.exists?"var(--text-primary)":SILVER}}>{t.name}</span>
          <span style={{fontSize:14,padding:"1px 6px",borderRadius:3,background:t.exists?OK.bg:BAD.bg,color:t.exists?OK.fg:BAD.fg,fontWeight:700}}>{t.exists?t.rows+"행":"없음"}</span>
        </div>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:2}}>{t.description}</div>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:2,fontFamily:"monospace"}}>적용: {(t.applies_to||[]).join(", ")}</div>
        {t.missing_cols?.length>0&&<div style={{fontSize:14,color:BAD.fg,marginTop:2}}>⚠ 누락 컬럼: {t.missing_cols.join(", ")}</div>}
      </div>))}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:16,minHeight:300}}>
      {!sel&&<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}>미리보기를 위해 좌측에서 매칭 테이블을 선택하세요</div>}
      {sel&&preview&&(<>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:14,fontWeight:700,fontFamily:"monospace"}}>{sel}</span>
          <div style={{display:"flex",gap:6,alignItems:"center"}}>
            {saveMsg&&<span style={{fontSize:14,fontFamily:"monospace",color:saveMsg.startsWith("⚠")?BAD.fg:OK.fg}}>{saveMsg}</span>}
            {hasAreaCol&&Object.keys(edits).length>0&&<button onClick={saveAreas} style={{padding:"4px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:WHITE,fontSize:14,fontWeight:700,cursor:"pointer"}} title="영역 편집 저장">💾 저장 ({Object.keys(edits).length})</button>}
            <button onClick={()=>download(sel)} style={{padding:"4px 10px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:"pointer"}}>⬇ CSV</button>
          </div>
        </div>
        {sel==="matching_step"&&rollup&&rollup.total>0&&(
          <div style={{display:"flex",flexWrap:"wrap",gap:4,marginBottom:10,padding:"6px 8px",background:"var(--bg-primary)",borderRadius:6,border:"1px solid var(--border)"}} title="Process-area rollup (/api/match/area-rollup)">
            <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginRight:4}}>🧩 area-rollup:</span>
            {rollup.rollup.map(b=>(
              <span key={b.area} style={{display:"inline-flex",alignItems:"center",gap:4,padding:"1px 7px",borderRadius:10,fontSize:14,fontFamily:"monospace",background:(b.area==="(unmatched)"?"#4b5563":areaColor(b.area))+"22",color:b.area==="(unmatched)"?"#94a3b8":areaColor(b.area),border:"1px solid "+(b.area==="(unmatched)"?"#4b5563":areaColor(b.area))}}>
                {b.area} · {b.count}
              </span>
            ))}
            <span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:"auto"}}>{rollup.matched}/{rollup.total} 태그됨</span>
          </div>
        )}
        {preview.rows.length===0?<div style={{color:"var(--text-secondary)",fontSize:14}}>데이터 없음. CSV를 먼저 업로드/시드하세요.</div>:(
          <div style={{overflow:"auto",maxHeight:480}}>
            <table style={{width:"100%",fontSize:14,borderCollapse:"collapse",fontFamily:"monospace"}}>
              <thead><tr style={{position:"sticky",top:0,background:"var(--bg-tertiary)"}}>
                {/* v8.2.1: ensure `area` column is shown even if csv predates the schema */}
                {(hasAreaCol&&!preview.columns.includes("area")?[...preview.columns,"area"]:preview.columns).map(c=>(
                  <th key={c} style={{textAlign:"left",padding:"4px 8px",color:c==="area"?"var(--accent)":"var(--text-secondary)",fontSize:14,borderBottom:"1px solid var(--border)"}}>{c}</th>
                ))}
              </tr></thead>
              <tbody>{preview.rows.map((r,i)=>(<tr key={i} style={{borderBottom:"1px solid rgba(255,255,255,0.04)"}}>
                {(hasAreaCol&&!preview.columns.includes("area")?[...preview.columns,"area"]:preview.columns).map(c=>{
                  if(c==="area"&&sel==="matching_step"){
                    const v=edits[i]!==undefined?edits[i]:r.area;
                    return(<td key={c} style={{padding:"3px 8px"}}>
                      <div style={{display:"flex",gap:6,alignItems:"center"}}>
                        <AreaChip value={v}/>
                        <select value={v||""} onChange={e=>setAreaEdit(i,e.target.value)} style={{fontSize:14,fontFamily:"monospace",background:"var(--bg-primary)",color:"var(--text-primary)",border:"1px solid var(--border)",borderRadius:3,padding:"1px 4px"}}>
                          <option value="">—</option>
                          {PROCESS_AREAS.map(a=>(<option key={a} value={a}>{a}</option>))}
                        </select>
                      </div>
                    </td>);
                  }
                  return(<td key={c} style={{padding:"3px 8px",color:"var(--text-primary)"}}>{r[c]==null?"-":String(r[c])}</td>);
                })}
              </tr>))}</tbody>
            </table>
            {preview.total>preview.rows.length&&<div style={{fontSize:14,color:"var(--text-secondary)",marginTop:6}}>{preview.rows.length} / {preview.total} 행 표시</div>}
          </div>
        )}
      </>)}
    </div>
  </div>);
}

function ProductPanel(){
  const[list,setList]=useState([]);const[sel,setSel]=useState(null);const[cfg,setCfg]=useState(null);const[raw,setRaw]=useState("");const[msg,setMsg]=useState("");
  const load=()=>sf("/api/catalog/product/list").then(d=>setList(d.products||[]));
  // fix: arrow+Promise → Promise 가 cleanup 에 저장되어 unmount 시 crash 방지.
  useEffect(()=>{load();},[]);
  // v8.2.0: Bell dismiss / external read → re-load this tab's notif list immediately
  useEffect(()=>{
    const onRefresh=()=>load();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>window.removeEventListener("hol:notif-refresh",onRefresh);
  },[]);
  const pick=(p)=>{setSel(p);sf("/api/catalog/product/load?product="+p).then(d=>{setCfg(d.config||{});setRaw(JSON.stringify(d.config||{},null,2));}).catch(()=>{setCfg({});setRaw("{}");});};
  const save=()=>{try{const parsed=JSON.parse(raw);sf("/api/catalog/product/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:sel,config:parsed})}).then(d=>{setMsg(d.errors?.length?"⚠ "+d.errors.join(", "):"저장됨 ✓");setTimeout(()=>setMsg(""),2500);load();});}catch(e){setMsg("JSON 파싱 오류: "+e.message);}};
  return(<div style={{display:"grid",gridTemplateColumns:"280px 1fr",gap:16}}>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:10,maxHeight:560,overflow:"auto"}}>
      <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginBottom:8,fontFamily:"monospace"}}>Product ({list.length})</div>
      {list.map(p=>(<div key={p.product} onClick={()=>pick(p.product)} style={{padding:"8px 10px",borderRadius:6,cursor:"pointer",marginBottom:4,background:sel===p.product?"var(--accent-glow)":"var(--bg-primary)",border:"1px solid "+(sel===p.product?"var(--accent)":"var(--border)")}}>
        <div style={{fontSize:14,fontWeight:700,fontFamily:"monospace"}}>{p.product}</div>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:2}}>proc_id: {p.process_id||"-"} · owner: {p.owner||"-"}</div>
        <div style={{fontSize:14,color:"var(--text-secondary)"}}>KNOB: {p.knob_count} · ET 항목: {p.et_key_count} · spec: {p.has_spec?"✓":"-"}</div>
      </div>))}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:16,minHeight:300}}>
      {!sel&&<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}>설정을 보거나 편집할 Product를 선택하세요 (YAML로 저장되며, 편집 시 JSON으로 표시)</div>}
      {sel&&cfg&&(<>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:14,fontWeight:700,fontFamily:"monospace"}}>{sel}.yaml</span>
          <div style={{display:"flex",gap:8,alignItems:"center"}}>
            {msg&&<span style={{fontSize:14,fontFamily:"monospace",color:msg.startsWith("⚠")?"var(--danger)":"var(--ok)"}}>{msg}</span>}
            <button onClick={save} style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,fontWeight:600,cursor:"pointer"}}>저장</button>
          </div>
        </div>
        <textarea value={raw} onChange={e=>setRaw(e.target.value)} spellCheck={false}
          style={{width:"100%",minHeight:440,fontFamily:"monospace",fontSize:14,padding:12,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",resize:"vertical",outline:"none"}}/>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:6,lineHeight:1.5}}>
          JSON으로 편집; YAML로 저장됨. 키: product, process_id, owner, canonical_knobs[], canonical_inline_items[], et_key_items[], yld_metric, perf_metric, target_spec{`{item: [lsl, usl, target]}`}, measured_shots[[x,y],...]
        </div>
      </>)}
    </div>
  </div>);
}

function S3Panel(){
  const[cfg,setCfg]=useState({bucket:"",prefix:"flow/artifacts/",region:"ap-northeast-2",enabled:false,profile:""});
  const[boto,setBoto]=useState(false);const[arts,setArts]=useState([]);const[events,setEvents]=useState([]);const[msg,setMsg]=useState("");
  // v9.1.x: S3 전역 마스터 스위치 — 주기 스케줄·업로드·수동 run/push 전체 통제 (dev/운영 공통).
  const[master,setMaster]=useState(true);
  const toggleMaster=()=>{
    const next=!master;setMaster(next);
    sf("/api/admin/s3-master",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:next})})
      .then(()=>{setMsg(next?"S3 전체 켜짐":"S3 전체 꺼짐");setTimeout(()=>setMsg(""),2500);})
      .catch(()=>{setMaster(!next);toast.error("S3 전역 스위치 저장 실패");});
  };
  const load=()=>{
    sf("/api/admin/s3-master").then(d=>setMaster(!!d.enabled)).catch(()=>{});
    sf("/api/catalog/s3/config").then(d=>{setCfg(d.config||cfg);setBoto(d.boto3_installed);});
    sf("/api/catalog/s3/artifacts").then(d=>setArts(d.artifacts||[]));
    sf("/api/catalog/s3/status?limit=30").then(d=>setEvents(d.events||[]));
  };
  useEffect(()=>{load();},[]);
  // v8.2.0: Bell dismiss / external read → re-load this tab's notif list immediately
  useEffect(()=>{
    const onRefresh=()=>load();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>window.removeEventListener("hol:notif-refresh",onRefresh);
  },[]);
  const saveCfg=()=>sf("/api/catalog/s3/config/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:cfg})}).then(()=>{setMsg("설정 저장됨");setTimeout(()=>setMsg(""),2000);});
  const syncAll=(t)=>{setMsg("동기화 중...");sf("/api/catalog/s3/sync"+(t?"?filter_type="+t:""),{method:"POST"}).then(d=>{setMsg(d.count+"개 아티팩트 동기화 완료");setTimeout(()=>setMsg(""),3000);load();});};
  const S={padding:"6px 10px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",fontFamily:"monospace"};
  const byType={};arts.forEach(a=>{(byType[a.type]=byType[a.type]||[]).push(a);});
  return(<div>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:14,marginBottom:12}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
        <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>☁ S3 동기화 설정</div>
        <div style={{display:"flex",gap:8,alignItems:"center"}}>
          <label style={{display:"flex",alignItems:"center",gap:6,fontSize:14,fontWeight:700,cursor:"pointer",padding:"2px 10px",borderRadius:10,background:master?OK.bg:BAD.bg,color:master?OK.fg:BAD.fg}}>
            <input type="checkbox" checked={master} onChange={toggleMaster}/>{master?"S3 전체 ON":"S3 전체 OFF"}
          </label>
          <span style={{fontSize:14,padding:"2px 8px",borderRadius:10,background:boto?OK.bg:BAD.bg,color:boto?OK.fg:BAD.fg,fontWeight:700}}>{boto?"boto3 설치됨":"boto3 없음 (로그만 기록)"}</span>
        </div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr",gap:8,marginBottom:8}}>
        <div><div style={{fontSize:14,color:"var(--text-secondary)"}}>Bucket</div><input value={cfg.bucket} onChange={e=>setCfg({...cfg,bucket:e.target.value})} style={{...S,width:"100%"}} placeholder="my-bucket"/></div>
        <div><div style={{fontSize:14,color:"var(--text-secondary)"}}>Prefix</div><input value={cfg.prefix} onChange={e=>setCfg({...cfg,prefix:e.target.value})} style={{...S,width:"100%"}}/></div>
        <div><div style={{fontSize:14,color:"var(--text-secondary)"}}>리전</div><input value={cfg.region} onChange={e=>setCfg({...cfg,region:e.target.value})} style={{...S,width:"100%"}}/></div>
        <div><div style={{fontSize:14,color:"var(--text-secondary)"}}>프로파일 (선택)</div><input value={cfg.profile} onChange={e=>setCfg({...cfg,profile:e.target.value})} style={{...S,width:"100%"}}/></div>
      </div>
      <div style={{display:"flex",gap:12,alignItems:"center"}}>
        <label style={{fontSize:14,display:"flex",alignItems:"center",gap:4,fontFamily:"monospace"}}><input type="checkbox" checked={cfg.enabled} onChange={e=>setCfg({...cfg,enabled:e.target.checked})} style={{accentColor:"var(--accent)"}}/>활성화</label>
        <button onClick={saveCfg} style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:WHITE,fontSize:14,fontWeight:600,cursor:"pointer"}}>설정 저장</button>
        <button onClick={()=>syncAll("")} style={{padding:"5px 14px",borderRadius:4,border:`1px solid ${OK.fg}`,background:OK.bg,color:OK.fg,fontSize:14,fontWeight:600,cursor:"pointer"}}>▶ 전체 동기화</button>
        {msg&&<span style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace"}}>{msg}</span>}
      </div>
    </div>
    {Object.entries(byType).map(([t,items])=>(<div key={t} style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:12,marginBottom:10}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
        <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{t} ({items.length})</span>
        <button onClick={()=>syncAll(t)} style={{padding:"3px 10px",borderRadius:3,border:"1px solid var(--border)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:"pointer"}}>{t} 동기화</button>
      </div>
      <table style={{width:"100%",fontSize:14,borderCollapse:"collapse",fontFamily:"monospace"}}>
        <thead><tr style={{color:"var(--text-secondary)"}}>
          <th style={{textAlign:"left",padding:"3px 6px"}}>키</th>
          <th style={{textAlign:"right",padding:"3px 6px"}}>크기</th>
          <th style={{textAlign:"center",padding:"3px 6px"}}>sha</th>
          <th style={{textAlign:"center",padding:"3px 6px"}}>상태</th>
        </tr></thead>
        <tbody>{items.map((a,i)=>{const last=a.last_sync;const st=last?.status;const color=a.in_sync?OK.fg:st==="error"?BAD.fg:st==="queued"?WARN.fg:SILVER;return(<tr key={i} style={{borderBottom:"1px solid rgba(255,255,255,0.04)"}}>
          <td style={{padding:"3px 6px",color:"var(--text-primary)"}}>{a.key}</td>
          <td style={{padding:"3px 6px",textAlign:"right",color:"var(--text-secondary)"}}>{(a.size/1024).toFixed(1)}KB</td>
          <td style={{padding:"3px 6px",textAlign:"center",color:"var(--text-secondary)"}}>{a.sha1||"-"}</td>
          <td style={{padding:"3px 6px",textAlign:"center",color,fontWeight:700}}>{a.in_sync?"✓ 동기화됨":(st||"없음")}</td>
        </tr>);})}</tbody>
      </table>
    </div>))}
    {events.length>0&&(<div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:12}}>
      <div style={{fontSize:14,fontWeight:700,color:"var(--accent)",marginBottom:8,fontFamily:"monospace"}}>최근 이벤트 ({events.length})</div>
      <div style={{maxHeight:200,overflow:"auto",fontSize:14,fontFamily:"monospace"}}>
        {[...events].reverse().map((e,i)=>(<div key={i} style={{padding:"2px 0",borderBottom:"1px solid rgba(255,255,255,0.04)",color:"var(--text-secondary)"}}>
          <span style={{color:"var(--accent)"}}>{e.ts?.slice(11,19)}</span> <span style={{color:e.status==="uploaded"?OK.fg:e.status==="error"?BAD.fg:WARN.fg}}>{e.status}</span> {e.s3_key||e.key} {e.error?"— "+e.error:""}
        </div>))}
      </div>
    </div>)}
  </div>);
}

function AdminMessagesPanel({user}){
  const[sub,setSub]=useState("inbox");
  const tS=(a)=>({padding:"7px 14px",fontSize:14,cursor:"pointer",fontWeight:a?700:500,borderRadius:5,background:a?"var(--accent-glow)":"transparent",color:a?"var(--accent)":"var(--text-secondary)",fontFamily:"'JetBrains Mono',monospace"});
  return(<div>
    <div style={{display:"flex",gap:4,marginBottom:12}}>
      <div style={tS(sub==="inbox")} onClick={()=>setSub("inbox")}>💬 받은함 (1:1)</div>
      <div style={tS(sub==="notices")} onClick={()=>setSub("notices")}>📢 공지사항 관리</div>
    </div>
    {sub==="inbox"&&<AdminInbox user={user}/>}
    {sub==="notices"&&<AdminNotices user={user}/>}
  </div>);
}

function AdminInbox({user}){
  const[threads,setThreads]=useState([]);const[sel,setSel]=useState("");const[thr,setThr]=useState(null);
  const[reply,setReply]=useState("");const[sending,setSending]=useState(false);const listRef=useRef(null);
  const admin=user?.username||"";
  const loadThreads=()=>sf("/api/messages/admin/threads?admin="+encodeURIComponent(admin)).then(d=>setThreads(d.threads||[])).catch(()=>{});
  const loadThread=(u)=>sf("/api/messages/admin/thread?admin="+encodeURIComponent(admin)+"&user="+encodeURIComponent(u)).then(d=>{setThr(d);setTimeout(()=>{if(listRef.current)listRef.current.scrollTop=listRef.current.scrollHeight;},50);}).catch(()=>{});
  useEffect(()=>{loadThreads();const iv=setInterval(loadThreads,30000);return()=>clearInterval(iv);},[admin]);
  useEffect(()=>{if(sel){loadThread(sel);}else setThr(null);},[sel]);
  const openThread=(u)=>{setSel(u);sf("/api/messages/admin/mark_read",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,to_user:u})}).then(loadThreads).catch(()=>{});};
  const sendReply=()=>{const v=(reply||"").trim();if(!v||!sel||sending)return;if(v.length>5000){toast.warn("최대 5000자");return;}setSending(true);
    sf("/api/messages/admin/reply",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,to_user:sel,text:v})})
      .then(()=>{setReply("");loadThread(sel);loadThreads();})
      .catch(e=>toast.error("실패: "+e.message))
      .finally(()=>setSending(false));};
  const totalUnread=threads.reduce((s,t)=>s+(t.unread_for_admin||0),0);
  return(<div style={{display:"flex",gap:12,height:"calc(100vh - 52px - 80px - 20px)"}}>
    <div style={{width:280,background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",display:"flex",flexDirection:"column",flexShrink:0}}>
      <div style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center"}}>
        <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{"> 스레드"}</span>
        <span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:8}}>{threads.length} · 읽지 않음 {totalUnread}</span>
        <div style={{flex:1}}/>
        <span onClick={loadThreads} style={{fontSize:14,color:"var(--text-secondary)",cursor:"pointer"}} title="새로고침">↻</span>
      </div>
      <div style={{flex:1,overflowY:"auto"}}>
        {threads.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>수신된 메시지가 없습니다.</div>}
        {threads.map(t=>(
          <div key={t.user} onClick={()=>openThread(t.user)} style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",cursor:"pointer",background:sel===t.user?"var(--accent-glow)":(t.unread_for_admin>0?"rgba(249,115,22,0.05)":"transparent")}}>
            <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:3}}>
              {t.unread_for_admin>0&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--accent)",flexShrink:0}}/>}
              <span style={{fontSize:14,fontWeight:t.unread_for_admin>0?700:500,color:"var(--text-primary)",fontFamily:"monospace",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.user}</span>
              {t.unread_for_admin>0&&<span style={{fontSize:14,fontWeight:700,padding:"1px 5px",borderRadius:3,background:"var(--accent)",color:WHITE}}>{t.unread_for_admin}</span>}
            </div>
            <div style={{fontSize:14,color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",lineHeight:1.4}}>{t.last_from?`[${t.last_from}] `:""}{t.last_preview||"(비어 있음)"}</div>
            <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:2}}>{(t.last_at||"").replace("T"," ").slice(0,16)}</div>
          </div>))}
      </div>
    </div>
    <div style={{flex:1,background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",display:"flex",flexDirection:"column",minWidth:0}}>
      {!sel&&<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-secondary)",fontSize:14}}>← 좌측에서 사용자를 선택하세요</div>}
      {sel&&thr&&<>
        <div style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>💬 {sel}</span>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>{(thr.messages||[]).length} 메시지</span>
          <div style={{flex:1}}/>
          <span onClick={()=>loadThread(sel)} style={{fontSize:14,color:"var(--text-secondary)",cursor:"pointer"}} title="새로고침">↻</span>
        </div>
        <div ref={listRef} style={{flex:1,overflowY:"auto",padding:14,background:"var(--bg-primary)"}}>
          {(thr.messages||[]).length===0&&<div style={{textAlign:"center",color:"var(--text-secondary)",fontSize:14,padding:30}}>메시지 없음</div>}
          {(thr.messages||[]).map(m=>{const mine=m.from===admin;return(
            <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:10}}>
              <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
                <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?`나 (${m.from})`:m.from} · {(m.created_at||"").replace("T"," ").slice(0,16)}</div>
                <div style={{padding:"8px 12px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?WHITE:"var(--text-primary)",fontSize:14,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
              </div>
            </div>);})}
        </div>
        <div style={{padding:"10px 14px",borderTop:"1px solid var(--border)"}}>
          <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
            <textarea value={reply} onChange={e=>setReply(e.target.value)} disabled={sending} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;sendReply();}}} placeholder={`${sel} 에게 답장 (Cmd/Ctrl+Enter 전송)`} rows={2} style={{flex:1,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
            <button onClick={sendReply} disabled={sending||!reply.trim()} style={{padding:"8px 18px",borderRadius:6,border:"none",background:sending||!reply.trim()?SILVER:"var(--accent)",color:WHITE,fontSize:14,fontWeight:700,cursor:sending||!reply.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>{sending?"…":"답장"}</button>
          </div>
        </div>
      </>}
    </div>
  </div>);
}

function AdminNotices({user}){
  const[notices,setNotices]=useState([]);const[showNew,setShowNew]=useState(false);
  const[title,setTitle]=useState("");const[body,setBody]=useState("");const[sending,setSending]=useState(false);
  const admin=user?.username||"";
  const load=()=>sf("/api/messages/admin/notices?admin="+encodeURIComponent(admin)).then(d=>setNotices(d.notices||[])).catch(()=>{});
  useEffect(()=>{load();},[admin]);
  const create=()=>{const t=title.trim(),b=body.trim();if(!t&&!b)return;if(sending)return;setSending(true);
    sf("/api/messages/admin/notice_create",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({author:admin,title:t,body:b})})
      .then(()=>{setTitle("");setBody("");setShowNew(false);load();})
      .catch(e=>toast.error("실패: "+e.message)).finally(()=>setSending(false));};
  const del=(id)=>{if(!confirm("공지사항을 삭제하시겠습니까?"))return;
    sf("/api/messages/admin/notice_delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,id})}).then(load).catch(e=>toast.error(e.message));};
  const S={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",fontFamily:"'Pretendard',sans-serif",boxSizing:"border-box"};
  return(<div>
    <div style={{display:"flex",alignItems:"center",marginBottom:12}}>
      <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{"> 공지사항"}</span>
      <span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:8}}>{notices.length} 개</span>
      <div style={{flex:1}}/>
      <button onClick={()=>setShowNew(!showNew)} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--accent)",background:showNew?"var(--accent)":"transparent",color:showNew?WHITE:"var(--accent)",fontSize:14,fontWeight:700,cursor:"pointer"}}>{showNew?"취소":"+ 새 공지사항"}</button>
    </div>
    {showNew&&<div style={{background:"var(--bg-secondary)",border:"1px solid var(--accent)",borderRadius:8,padding:16,marginBottom:14}}>
      <input value={title} onChange={e=>setTitle(e.target.value)} placeholder="제목 (최대 200자)" maxLength={200} style={{...S,marginBottom:8,fontWeight:600}}/>
      <textarea value={body} onChange={e=>setBody(e.target.value)} placeholder="공지 본문 (최대 5000자)" rows={5} style={{...S,marginBottom:8,resize:"vertical"}}/>
      <div style={{display:"flex",alignItems:"center"}}>
        <span style={{fontSize:14,color:"var(--text-secondary)"}}>{title.length}/200 · {body.length}/5000</span>
        <div style={{flex:1}}/>
        <button onClick={create} disabled={sending||(!title.trim()&&!body.trim())} style={{padding:"7px 18px",borderRadius:5,border:"none",background:sending||(!title.trim()&&!body.trim())?SILVER:"var(--accent)",color:WHITE,fontSize:14,fontWeight:700,cursor:sending?"default":"pointer"}}>{sending?"…":"발행"}</button>
      </div>
    </div>}
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden"}}>
      {notices.length===0&&<div style={{padding:30,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>등록된 공지사항이 없습니다.</div>}
      {notices.map(n=>(
        <div key={n.id} style={{padding:"12px 16px",borderBottom:"1px solid var(--border)"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
            <span style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",flex:1}}>{n.title||"(제목 없음)"}</span>
            <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{(n.created_at||"").replace("T"," ").slice(0,16)}</span>
            <span style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace"}}>👁 {n.read_count||0}/{n.total_recipients||"?"}</span>
            <span onClick={()=>del(n.id)} style={{cursor:"pointer",color:"var(--danger)",fontSize:14}}>🗑</span>
          </div>
          {n.body&&<div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5,whiteSpace:"pre-wrap",paddingLeft:2}}>{n.body}</div>}
          <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:4}}>by {n.author}</div>
        </div>))}
    </div>
  </div>);
}


function AWSPanel({user}){
  const[data,setData]=useState(null);
  const[selIdx,setSelIdx]=useState(0);
  const[form,setForm]=useState(null);
  const[msg,setMsg]=useState("");
  const[newProfile,setNewProfile]=useState("");
  const[secretEdit,setSecretEdit]=useState(false);

  const load=()=>sf("/api/s3ingest/aws-config?username="+encodeURIComponent(user?.username||"")).then(d=>{setData(d);setSelIdx(0);}).catch(e=>setMsg("오류: "+e.message));
  useEffect(()=>{load();},[]);

  useEffect(()=>{
    if(!data||!Array.isArray(data.profiles)||!data.profiles[selIdx]){setForm(null);return;}
    const p=data.profiles[selIdx];
    setForm({
      profile:p.profile||"default",
      aws_access_key_id:p.aws_access_key_id||"",
      aws_secret_access_key:p.has_secret?p.aws_secret_access_key_masked:"",
      region:p.region||"",
      output:p.output||"",
      endpoint_url:p.endpoint_url||"",
    });
    setSecretEdit(false);
  },[data,selIdx]);

  const save=()=>{
    if(!form)return;
    const payload={...form,username:user?.username||""};
    // If user didn't edit secret, send empty string so backend keeps existing
    if(!secretEdit)payload.aws_secret_access_key="";
    sf("/api/s3ingest/aws-config/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})
      .then(()=>{setMsg("저장됨 ✓");setTimeout(()=>setMsg(""),2000);load();})
      .catch(e=>setMsg("오류: "+e.message));
  };
  const addProfile=()=>{
    const v=(newProfile||"").trim();
    if(!v||!/^[a-zA-Z0-9_-]{1,64}$/.test(v)){setMsg("잘못된 프로파일 이름");return;}
    if(data&&Array.isArray(data.profiles)&&data.profiles.some(p=>p.profile===v)){setMsg("프로파일이 이미 존재합니다");return;}
    const nextProfiles=[...(Array.isArray(data?.profiles)?data.profiles:[]),{profile:v,aws_access_key_id:"",aws_secret_access_key_masked:"",has_secret:false,region:"",output:"",endpoint_url:""}];
    setData({...data,profiles:nextProfiles});
    setSelIdx(nextProfiles.length-1);
    setNewProfile("");
  };
  const delProfile=()=>{
    if(!form)return;
    if(form.profile==="default"){setMsg("'default' 프로파일은 삭제할 수 없습니다");return;}
    if(!confirm(`AWS 프로파일 '${form.profile}' 을(를) 삭제하시겠습니까?`))return;
    sf("/api/s3ingest/aws-config/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",profile:form.profile})})
      .then(()=>{setMsg("삭제됨");load();})
      .catch(e=>setMsg("오류: "+e.message));
  };

  const S={padding:"7px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",fontFamily:"monospace"};
  const labelS={fontSize:14,color:"var(--text-secondary)",marginBottom:4};

  if(!data)return<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}><Loading text="로딩 중..."/></div>;

  return(
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:700}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
        <div>
          <span style={{fontSize:14,fontWeight:700,color:"var(--accent)"}}>AWS 설정</span>
          <span style={{fontSize:14,color:"var(--text-secondary)",marginLeft:10,fontFamily:"monospace"}}>{data.credentials_path}</span>
        </div>
        {msg&&<span style={{fontSize:14,color:msg.startsWith("오류")?"var(--danger)":"var(--ok)",fontFamily:"monospace"}}>{msg}</span>}
      </div>

      {!data.aws_available&&<div style={{padding:"8px 12px",borderRadius:6,background:"rgba(251,191,36,0.1)",border:"1px solid rgba(251,191,36,0.3)",marginBottom:12,fontSize:14,color:"#fbbf24"}}>⚠ aws CLI 미설치 — sync 실행은 불가. 자격증명은 저장 가능.</div>}

      {/* Profile selector */}
      <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:16,flexWrap:"wrap"}}>
        <span style={{fontSize:14,color:"var(--text-secondary)"}}>프로파일:</span>
        {(Array.isArray(data.profiles)?data.profiles:[]).map((p,i)=>(
          <span key={p.profile+"_"+i} onClick={()=>setSelIdx(i)} style={{padding:"5px 12px",borderRadius:5,fontSize:14,cursor:"pointer",fontWeight:selIdx===i?700:500,background:selIdx===i?"var(--accent-glow)":"var(--bg-primary)",color:selIdx===i?"var(--accent)":"var(--text-secondary)",border:"1px solid "+(selIdx===i?"var(--accent)":"var(--border)"),fontFamily:"monospace"}}>{p.profile}</span>
        ))}
        <span style={{color:"var(--border)"}}>|</span>
        <input value={newProfile} onChange={e=>setNewProfile(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;addProfile();}}} placeholder="새 프로파일 이름" style={{...S,width:160,fontSize:14,padding:"5px 8px"}}/>
        <button onClick={addProfile} style={{padding:"5px 12px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:"pointer"}}>+ 추가</button>
      </div>

      {/* Form */}
      {form&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"12px 14px"}}>
        <div style={{gridColumn:"1 / 3"}}>
          <div style={labelS}>Access Key ID</div>
          <input value={form.aws_access_key_id} onChange={e=>setForm(f=>({...f,aws_access_key_id:e.target.value}))} placeholder="AKIA... (16-32 uppercase/digits)" style={{...S,width:"100%"}}/>
        </div>
        <div style={{gridColumn:"1 / 3"}}>
          <div style={labelS}>Secret Access Key {form.profile!=="default"||secretEdit?"":<span style={{color:"var(--text-secondary)",fontSize:14}}> (마스킹됨 — 변경하려면 편집 클릭)</span>}</div>
          <div style={{display:"flex",gap:6}}>
            <input value={form.aws_secret_access_key} disabled={!secretEdit} onChange={e=>setForm(f=>({...f,aws_secret_access_key:e.target.value}))} placeholder={secretEdit?"40자 secret":""} style={{...S,flex:1,opacity:secretEdit?1:0.7}} type={secretEdit?"text":"password"}/>
            {!secretEdit?<button onClick={()=>{setSecretEdit(true);setForm(f=>({...f,aws_secret_access_key:""}));}} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>편집</button>
            :<button onClick={()=>{setSecretEdit(false);load();}} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>취소</button>}
          </div>
        </div>
        <div>
          <div style={labelS}>리전</div>
          <input value={form.region} onChange={e=>setForm(f=>({...f,region:e.target.value}))} placeholder="예: ap-northeast-2" style={{...S,width:"100%"}}/>
        </div>
        <div>
          <div style={labelS}>Output</div>
          <select value={form.output} onChange={e=>setForm(f=>({...f,output:e.target.value}))} style={{...S,width:"100%"}}>
            <option value="">(기본값)</option>
            <option value="json">json</option>
            <option value="text">text</option>
            <option value="table">table</option>
            <option value="yaml">yaml</option>
          </select>
        </div>
        <div style={{gridColumn:"1 / 3"}}>
          <div style={labelS}>Endpoint URL (선택, flow-data AWS config에 저장됨)</div>
          <input value={form.endpoint_url} onChange={e=>setForm(f=>({...f,endpoint_url:e.target.value}))} placeholder="https://s3.internal.company:9000" style={{...S,width:"100%"}}/>
        </div>
      </div>}

      <div style={{display:"flex",gap:8,marginTop:18}}>
        <button onClick={save} style={{padding:"9px 22px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontWeight:700,fontSize:14,cursor:"pointer"}}>저장</button>
        {form&&form.profile!=="default"&&<button onClick={delProfile} style={{padding:"9px 16px",borderRadius:5,border:"1px solid var(--danger)",background:"transparent",color:"var(--danger)",fontSize:14,cursor:"pointer"}}>프로파일 삭제</button>}
        <div style={{flex:1}}/>
        <button onClick={load} style={{padding:"9px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>↻ 새로고침</button>
      </div>

      <div style={{marginTop:18,padding:12,background:"var(--bg-primary)",borderRadius:6,fontSize:14,color:"var(--text-secondary)",lineHeight:1.6,fontFamily:"monospace"}}>
        <b style={{color:"var(--accent)"}}># 동작 방식</b><br/>
        • Access Key + Secret 은 <code>{data.credentials_path}</code> 에 저장 (mode 600)<br/>
        • Region / Output / Endpoint URL 은 <code>{data.config_path}</code> 에 저장<br/>
        • Secret 은 기본적으로 마스킹 표시. '편집' 눌러야 변경 가능<br/>
        • 저장 후 파일 브라우저의 S3 Sync 항목이 이 자격증명으로 실행됨<br/>
        • Per-item endpoint 가 필요하면 파일 브라우저 → S3 Sync 모달의 Endpoint URL 필드 사용
      </div>
    </div>
  );
}

// v8.8.23: Admin 그룹 패널 내부에서 extra_emails 추가용 미니 인풋.
function ExtraEmailAdd({current,onSave}){
  const [v,setV]=useState("");
  const submit=()=>{
    const s=(v||"").trim();
    if(!s||!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s)){toast.warn("이메일 형식이 올바르지 않습니다.");return;}
    const next=Array.from(new Set([...(current?.extra_emails||[]),s]));
    onSave(next);
    setV("");
  };
  return (<div style={{display:"flex",gap:6}}>
    <input value={v} onChange={e=>setV(e.target.value)} placeholder="외부 이메일 추가 (e.g. vendor@company.co.kr)"
      onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;submit();}}}
      style={{flex:1,padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}/>
    <button onClick={submit} style={{padding:"6px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer"}}>추가</button>
  </div>);
}


// ── Groups Panel (v8.8.3 — description 추가, 관심 WF 제거 · v8.8.23 extra_emails 통합) ──
function GroupsPanel({allUsers, isAdmin, currentUser}){
  const [groups,setGroups]=useState([]);
  const [sel,setSel]=useState(null);
  const [newName,setNewName]=useState("");
  const [newDesc,setNewDesc]=useState("");
  const [editDesc,setEditDesc]=useState("");
  const [editDescSaved,setEditDescSaved]=useState(false);
  const [msg,setMsg]=useState("");
  // v8.8.1: 그룹 멤버 후보. admin/test 제외 — 모든 로그인 유저가 조회 가능.
  const [eligible,setEligible]=useState([]);
  const load=()=>sf("/api/groups/list").then(d=>setGroups(d.groups||[])).catch(e=>setMsg(e.message));
  const loadEligible=()=>sf("/api/groups/eligible-users")
    .then(d=>setEligible(d.users||[]))
    .catch(()=>setEligible((allUsers||[]).filter(u=>u.role!=="admin"&&!/test/i.test(u.username||""))));
  useEffect(()=>{load();loadEligible();},[]);
  const create=()=>{
    const n=newName.trim();if(!n)return;
    sf("/api/groups/create",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:n,description:newDesc.trim()||null,members:[]})})
      .then(()=>{setNewName("");setNewDesc("");setMsg("생성 완료");load();}).catch(e=>setMsg(e.message));
  };
  const del=(id)=>{if(!confirm("삭제하시겠습니까?"))return;
    sf("/api/groups/delete?id="+encodeURIComponent(id),{method:"POST"})
      .then(()=>{setSel(null);load();}).catch(e=>setMsg(e.message));};
  const addMember=(id,u)=>sf("/api/groups/members/add?id="+encodeURIComponent(id),
    {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u})})
    .then(load);
  const rmMember=(id,u)=>sf("/api/groups/members/remove?id="+encodeURIComponent(id),
    {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u})})
    .then(load);
  const addLot=(id)=>{const v=newLot.trim();if(!v)return;
    sf("/api/groups/lots/add?id="+encodeURIComponent(id),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({lot_id:v})})
      .then(()=>{setNewLot("");load();});};
  const rmLot=(id,l)=>sf("/api/groups/lots/remove?id="+encodeURIComponent(id),
    {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({lot_id:l})})
    .then(load);
  const setModules=(id,mods)=>sf("/api/groups/modules/set?id="+encodeURIComponent(id),
    {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({modules:mods})})
    .then(load);
  const saveDesc=(id,desc)=>sf("/api/groups/update?id="+encodeURIComponent(id),
    {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({description:desc.trim()||null})})
    .then(()=>{setEditDescSaved(true);setTimeout(()=>setEditDescSaved(false),2000);load();})
    .catch(e=>setMsg(e.message));
  const MODULES=["GATE","STI","PC","MOL","BEOL","ET","EDS","S-D Epi","Spacer","Well","기타"];
  const toggleModule=(id,mod,arr)=>{
    const set=new Set(arr||[]);
    if(set.has(mod)) set.delete(mod); else set.add(mod);
    setModules(id,Array.from(set).sort());
  };

  const cur=groups.find(g=>g.id===sel);
  // 선택 그룹 변경 시 editDesc 동기화.
  useEffect(()=>{setEditDesc(cur?.description||"");setEditDescSaved(false);},[sel,cur?.description]);
  // v8.8.1: admin/test 제외된 후보 풀에서 이미 멤버인 사람 제외.
  // v8.8.27: 후보를 username 문자열이 아닌 유저 오브젝트({username,name})로 보존 → 드롭다운에서 이름+id 표시.
  const availableUserObjs=(eligible||[]).filter(u=>u&&u.username&&!(cur?.members||[]).includes(u.username));
  // username→user 매핑(멤버 chip 에 이름을 붙이기 위해).
  const userIndex=Object.fromEntries((eligible||[]).filter(u=>u&&u.username).map(u=>[u.username,u]));
  // 편집 권한 — admin 또는 owner.
  const canEdit=cur?(isAdmin||cur.owner===(currentUser?.username||"")):false;

  return(
    <div style={{display:"grid",gridTemplateColumns:"300px 1fr",gap:16}}>
      {/* List */}
      <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:12}}>
        <div style={{fontSize:14,fontWeight:600,marginBottom:10}}>그룹 목록 ({groups.length})</div>
        <div style={{display:"flex",flexDirection:"column",gap:4,marginBottom:10}}>
          <input value={newName} onChange={e=>setNewName(e.target.value)} placeholder="새 그룹 이름"
            onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;create();}}}
            style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
          <input value={newDesc} onChange={e=>setNewDesc(e.target.value)} placeholder="설명 (선택)"
            onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;create();}}}
            style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
          <button onClick={create} style={{padding:"6px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer"}}>생성</button>
        </div>
        <div style={{display:"flex",flexDirection:"column",gap:4,maxHeight:400,overflow:"auto"}}>
          {groups.map(g=>(
            <div key={g.id} onClick={()=>setSel(g.id)}
              style={{padding:"8px 10px",borderRadius:6,cursor:"pointer",
                background:sel===g.id?"var(--bg-tertiary)":"transparent",
                border:"1px solid "+(sel===g.id?"var(--accent)":"transparent")}}>
              <div style={{fontSize:14,fontWeight:600}}>{g.name}</div>
              {g.description&&<div style={{fontSize:14,color:"var(--text-secondary)",marginTop:1,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{g.description}</div>}
              <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:1}}>
                owner: {g.owner} · members: {(g.members||[]).length} · modules: {(g.modules||[]).length}
              </div>
            </div>
          ))}
          {groups.length===0&&<div style={{fontSize:14,color:"var(--text-secondary)",padding:"20px 0",textAlign:"center"}}>그룹 없음</div>}
        </div>
        {msg&&<div style={{marginTop:10,fontSize:14,color:"var(--accent)"}}>{msg}</div>}
      </div>

      {/* Detail */}
      <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
        {!cur&&<div style={{fontSize:14,color:"var(--text-secondary)"}}>좌측에서 그룹을 선택하세요.</div>}
        {cur&&<>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}>
            <div style={{fontSize:16,fontWeight:700}}>{cur.name}</div>
            <div style={{flex:1,fontSize:14,color:"var(--text-secondary)"}}>owner: {cur.owner} · id: {cur.id}</div>
            {canEdit&&<button onClick={()=>del(cur.id)} style={{padding:"5px 10px",borderRadius:4,border:"1px solid var(--danger)",background:"transparent",color:"var(--danger)",fontSize:14,cursor:"pointer"}}>그룹 삭제</button>}
          </div>

          {/* 설명 */}
          <div style={{marginBottom:14}}>
            <div style={{fontSize:14,fontWeight:600,marginBottom:4}}>설명</div>
            {canEdit
              ?<div style={{display:"flex",gap:6,alignItems:"flex-start"}}>
                <textarea value={editDesc} onChange={e=>setEditDesc(e.target.value)} rows={2}
                  placeholder="이 그룹의 목적을 간단히 설명하세요."
                  style={{flex:1,padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,resize:"vertical"}}/>
                <button onClick={()=>saveDesc(cur.id,editDesc)}
                  style={{padding:"6px 12px",borderRadius:4,border:"none",background:editDescSaved?"var(--ok)":"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer",whiteSpace:"nowrap"}}>
                  {editDescSaved?"저장됨":"저장"}
                </button>
              </div>
              :<div style={{fontSize:14,color:cur.description?"var(--text-primary)":"var(--text-secondary)",fontStyle:cur.description?"normal":"italic",padding:"4px 0"}}>
                {cur.description||"설명 없음"}
              </div>
            }
          </div>

          <div style={{fontSize:14,fontWeight:600,marginBottom:6}}>멤버 ({(cur.members||[]).length})</div>
          {/* v8.8.27: 멤버 chip 에 이름(있으면) + id 표시. 동명이인이어도 id 가 항상 붙음. */}
          <div style={{display:"flex",flexWrap:"wrap",gap:6,marginBottom:10}}>
            {(cur.members||[]).map(m=>{
              const u=userIndex[m]||{username:m};
              return(
                <span key={m} title={m} style={{padding:"3px 10px",borderRadius:999,background:"var(--bg-tertiary)",fontSize:14,display:"inline-flex",alignItems:"center",gap:6}}>
                  {userLabel(u)}
                  {canEdit&&<button onClick={()=>rmMember(cur.id,m)} style={{border:"none",background:"transparent",color:"var(--danger)",cursor:"pointer",fontSize:14,padding:0}}>×</button>}
                </span>
              );
            })}
            {(cur.members||[]).length===0&&<span style={{fontSize:14,color:"var(--text-secondary)",fontStyle:"italic"}}>멤버 없음 — 아래 + 멤버 추가 에서 선택</span>}
          </div>
          {canEdit&&<div style={{display:"flex",gap:6,marginBottom:16}}>
            <select onChange={e=>{if(e.target.value){addMember(cur.id,e.target.value);e.target.value="";}}}
              style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,minWidth:260}}>
              <option value="">+ 멤버 추가…</option>
              {/* v8.8.27: 옵션 텍스트도 name+id. 이름이 없으면 id 만. */}
              {availableUserObjs.map(u=><option key={u.username} value={u.username}>{userLabel(u)}</option>)}
            </select>
            <span style={{fontSize:14,color:"var(--text-secondary)",alignSelf:"center"}}>test 계정만 자동 제외</span>
          </div>}

          {/* v8.8.5: 담당 모듈 UI 제거 — 불필요. 그룹은 단순 멤버 풀로 사용. */}

          {/* v8.8.23: 외부 고정 수신자(extra_emails) — 인폼/회의 메일 발송 시 자동 포함되는 주소. */}
          <div style={{marginTop:16}}>
            <div style={{fontSize:14,fontWeight:600,marginBottom:6}}>외부 수신자 이메일 ({(cur.extra_emails||[]).length})
              <span style={{marginLeft:8,fontSize:14,color:"var(--text-secondary)",fontWeight:400}}>
                메일 발송 시 members 의 사내 이메일과 함께 항상 포함됩니다.
              </span>
            </div>
            <div style={{display:"flex",flexWrap:"wrap",gap:6,marginBottom:8}}>
              {(cur.extra_emails||[]).map(e=>(
                <span key={e} style={{padding:"3px 10px",borderRadius:999,background:"var(--bg-tertiary)",fontSize:14,display:"inline-flex",alignItems:"center",gap:6,fontFamily:"monospace"}}>
                  {e}
                  {canEdit&&<button onClick={()=>{
                    const next=(cur.extra_emails||[]).filter(x=>x!==e);
                    sf("/api/groups/update?id="+encodeURIComponent(cur.id),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({extra_emails:next})})
                      .then(load).catch(err=>setMsg(err.message));
                  }} style={{border:"none",background:"transparent",color:"var(--danger)",cursor:"pointer",fontSize:14,padding:0}}>×</button>}
                </span>
              ))}
              {(cur.extra_emails||[]).length===0&&<span style={{fontSize:14,color:"var(--text-secondary)",fontStyle:"italic"}}>외부 수신자 없음</span>}
            </div>
            {canEdit&&<ExtraEmailAdd current={cur} onSave={(next)=>
              sf("/api/groups/update?id="+encodeURIComponent(cur.id),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({extra_emails:next})})
                .then(load).catch(err=>setMsg(err.message))
            }/>}
          </div>

          <div style={{marginTop:16,padding:10,background:"var(--bg-primary)",borderRadius:6,fontSize:14,color:"var(--text-secondary)",lineHeight:1.6}}>
            • 이 그룹에 속한 유저는 Dashboard/Tracker 에서 이 그룹에 연결된 차트·이슈만 공유함.<br/>
            • admin 은 모든 그룹과 콘텐츠를 볼 수 있음 (전체 담당).<br/>
            • <b>설명</b>은 그룹의 목적·소속 부서 등 자유 텍스트. 리스트 보조 텍스트로 노출됨.<br/>
            • <b>v8.8.23</b> 메일 그룹과 이슈추적 그룹이 이 Admin 그룹으로 통합됨. 여기서 만든 그룹이
              인폼 메일 수신 드롭다운 / 이슈추적 그룹 선택 / 회의 mail_group_ids 에 모두 노출됩니다.
              기존 <code>mail_groups.json</code> 과 <code>admin_settings:recipient_groups</code> 는 자동 병합.<br/>
            • <b>v8.8.5</b> admin 도 멤버 풀에 포함 (사내 계정은 이메일 보유) · test substring 계정만 제외 · 생성자는 자동 가입되지 않음 (명시적으로 추가).
          </div>
        </>}
      </div>
    </div>
  );
}

// ── Base CSV Editor Panel (v8.5.2) ──
const BASE_CSVS = [
  {key:"step_matching",label:"step_matching.csv"},
  {key:"knob_ppid",label:"knob_ppid.csv"},
  // v8.7.5: INLINE prefix 항목 매칭용.
  {key:"inline_matching",label:"inline_matching.csv"},
  // v8.7.5: VM_ prefix 항목 매칭용.
  {key:"vm_matching",label:"vm_matching.csv"},
];
function BaseCsvPanel(){
  const [cur,setCur]=useState("step_matching");
  const [columns,setColumns]=useState([]);
  const [uniqueKey,setUniqueKey]=useState([]);
  const [rows,setRows]=useState([]);
  const [msg,setMsg]=useState("");
  const [saving,setSaving]=useState(false);
  const [filter,setFilter]=useState("");
  const load=(name)=>{
    setMsg("");
    sf("/api/admin/base-csv?name="+encodeURIComponent(name)).then(d=>{
      setColumns(d.columns||[]);setUniqueKey(d.unique_key||[]);setRows(d.rows||[]);
    }).catch(e=>setMsg(e.message));
  };
  useEffect(()=>{load(cur);},[cur]);
  const updCell=(ri,ci,v)=>{
    const next=rows.map((r,i)=>i===ri?r.map((x,j)=>j===ci?v:x):r);
    setRows(next);
  };
  const addRow=()=>setRows([...rows,columns.map(()=>"")]);
  const delRow=(ri)=>setRows(rows.filter((_,i)=>i!==ri));
  const moveRow=(ri,dir)=>{
    const ni=ri+dir;if(ni<0||ni>=rows.length)return;
    const next=[...rows];[next[ri],next[ni]]=[next[ni],next[ri]];setRows(next);
  };
  const save=()=>{
    setSaving(true);setMsg("");
    sf("/api/admin/base-csv",{method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:cur,rows})})
      .then(d=>{setMsg(`저장 완료 (${d.rows_saved}행)`);load(cur);})
      .catch(e=>setMsg(e.message))
      .finally(()=>setSaving(false));
  };
  const filtered=filter
    ?rows.map((r,i)=>[r,i]).filter(([r])=>r.some(v=>String(v||"").toLowerCase().includes(filter.toLowerCase())))
    :rows.map((r,i)=>[r,i]);
  return(
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:12,flexWrap:"wrap"}}>
        {BASE_CSVS.map(c=>(
          <span key={c.key} onClick={()=>setCur(c.key)} style={{
            padding:"5px 12px",borderRadius:6,cursor:"pointer",fontSize:14,fontWeight:cur===c.key?700:400,
            background:cur===c.key?"var(--accent-glow)":"transparent",
            color:cur===c.key?"var(--accent)":"var(--text-secondary)",
            border:"1px solid "+(cur===c.key?"var(--accent)":"var(--border)"),
            fontFamily:"monospace",
          }}>{c.label}</span>
        ))}
        <div style={{flex:1}}/>
        <input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="필터..."
          style={{padding:"5px 10px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,width:160}}/>
        <button onClick={()=>load(cur)} style={{padding:"5px 10px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>↻ 재로드</button>
      </div>
      <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:8}}>
        컬럼: <code>{columns.join(", ")}</code> · unique: <code>{uniqueKey.join(", ")}</code> · 총 {rows.length}행
        {filter&&` · 필터 매칭 ${filtered.length}행`}
      </div>

      <div style={{maxHeight:500,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-primary)"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace"}}>
          <thead><tr>
            <th style={{position:"sticky",top:0,background:"var(--bg-tertiary)",padding:"6px 8px",borderBottom:"2px solid var(--border)",width:38}}>#</th>
            {columns.map(c=>(
              <th key={c} style={{position:"sticky",top:0,background:"var(--bg-tertiary)",padding:"6px 8px",borderBottom:"2px solid var(--border)",textAlign:"left",color:uniqueKey.includes(c)?"var(--accent)":"var(--text-primary)"}}>{c}{uniqueKey.includes(c)?" *":""}</th>
            ))}
            <th style={{position:"sticky",top:0,background:"var(--bg-tertiary)",padding:"6px 8px",borderBottom:"2px solid var(--border)",width:80}}>작업</th>
          </tr></thead>
          <tbody>
            {filtered.map(([r,ri])=>(
              <tr key={ri} style={{borderBottom:"1px solid var(--border)"}}>
                <td style={{padding:"3px 8px",color:"var(--text-secondary)"}}>{ri+1}</td>
                {r.map((v,ci)=>(
                  <td key={ci} style={{padding:0,borderLeft:"1px solid var(--border)"}}>
                    <input value={v||""} onChange={e=>updCell(ri,ci,e.target.value)}
                      style={{width:"100%",padding:"4px 8px",border:"none",background:"transparent",color:"var(--text-primary)",fontFamily:"monospace",fontSize:14,outline:"none"}}/>
                  </td>
                ))}
                <td style={{padding:"2px 4px",borderLeft:"1px solid var(--border)",whiteSpace:"nowrap"}}>
                  <span onClick={()=>moveRow(ri,-1)} style={{cursor:"pointer",color:"var(--text-secondary)",padding:"0 4px"}}>↑</span>
                  <span onClick={()=>delRow(ri)} style={{cursor:"pointer",color:"var(--danger)",padding:"0 4px"}}>✕</span>
                </td>
              </tr>
            ))}
            {rows.length===0&&<tr><td colSpan={columns.length+2} style={{padding:20,textAlign:"center",color:"var(--text-secondary)"}}>데이터 없음. 아래 '+행 추가' 로 시작하세요.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={{display:"flex",gap:8,marginTop:12,alignItems:"center"}}>
        <button onClick={addRow} style={{padding:"7px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontSize:14,cursor:"pointer"}}>+ 행 추가</button>
        <button onClick={save} disabled={saving} style={{padding:"7px 18px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontWeight:700,fontSize:14,cursor:saving?"wait":"pointer"}}>{saving?"저장 중...":"저장"}</button>
        {msg&&<span style={{fontSize:14,color:msg.startsWith("저장")?"var(--ok)":"var(--danger)"}}>{msg}</span>}
      </div>

      <div style={{marginTop:14,padding:10,background:"var(--bg-primary)",borderRadius:6,fontSize:14,color:"var(--text-secondary)",lineHeight:1.6}}>
        • 컬럼 뒤 <b style={{color:"var(--accent)"}}>*</b> 는 unique key. 중복 시 저장 거부.<br/>
        • step_matching/Vehicle_matching: product + step_desc 로 해당 제품의 step_id 를 찾습니다.<br/>
        • knob_ppid: (feature_name, function_step, rule_order, ppid, operator, category, use) — 앞 3개 복합 unique. use ∈ Y/N/0/1.<br/>
        • inline_matching: (product, step_id, item_id), vm_matching: (step_desc, item_id) — VM step_id 는 Vehicle_matching 에서 가져옵니다.<br/>
        • 저장 시 UTF-8 BOM 포함 CSV 로 덮어씁니다 (Excel 호환). SplitTable KNOB 메타는 자동 재조회.
      </div>
    </div>
  );
}
