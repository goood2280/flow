import { useState, useEffect, useRef, Component } from "react";
import Loading from "../components/Loading";
import { PageHeader, TabStrip, Button, Banner, Pill, statusPalette, chartPalette } from "../components/UXKit";
import { PROCESS_AREAS, areaColor } from "../constants/processAreas";
import { sf, dl, postJson, userLabel, userMatches } from "../lib/api";
// v8.8.3: inform/meeting/calendar 권한 항목 추가.
// v8.8.22: dashboard_chart 제거 (페이지 위임 탭이 같은 역할 수행). 실제 nav 메뉴 순서로 재배치.
//   순서 = TABS(config.js) — home/admin 제외: filebrowser → dashboard → splittable → tracker →
//   inform → meeting → calendar → tablemap → ml → devguide(맨 뒤).
const ALL_TABS=["filebrowser","dashboard","splittable","diagnosis","ettime","waferlayout","tracker","inform","meeting","calendar","tablemap","ml","devguide"];
// v8.7.5: u.tabs 는 string 이지만 legacy json 에서 array 로 저장된 기록이 있을 수 있어
// "r.split is not a function" 방지를 위해 정규화 헬퍼를 둔다.
function _tabsToArray(v){
  if(Array.isArray(v))return v.filter(Boolean).map(String);
  if(typeof v==="string"&&v)return v.split(",").map(s=>s.trim()).filter(Boolean);
  return ["filebrowser","dashboard","splittable"];
}
function _arr(v){return Array.isArray(v)?v:[];}
function _obj(v){return v&&typeof v==="object"&&!Array.isArray(v)?v:{};}
function _entries(v){return Object.entries(_obj(v));}
const OK = statusPalette.ok;
const WARN = statusPalette.warn;
const BAD = statusPalette.bad;
const INFO = statusPalette.info;
const NEUTRAL = statusPalette.neutral;
const WHITE = "var(--bg-secondary)";
const SKY = chartPalette.series[13];
const SLATE = "rgba(107,114,128,0.95)";
const SILVER = "rgba(148,163,184,0.95)";

// v8.7.5: Admin 탭 전환 시 서브 패널에서 던진 에러가 페이지 전체를 마비시키지 않도록.
class TabBoundary extends Component{
  constructor(p){super(p);this.state={err:null};}
  static getDerivedStateFromError(e){return{err:e};}
  componentDidCatch(err,info){try{console.error("[admin tab boundary]",this.props.tabKey,err,info);}catch(_){}}
  componentDidUpdate(prev){if(prev.tabKey!==this.props.tabKey&&this.state.err)this.setState({err:null});}
  render(){
    if(this.state.err){
      return(<div style={{padding:"20px 24px",background:BAD.bg,border:`1px solid ${BAD.fg}66`,borderRadius:8,color:BAD.fg,fontSize:12}}>
        <div style={{fontWeight:700,marginBottom:6}}>⚠ 이 탭을 렌더하는 도중 오류가 발생했습니다.</div>
        <div style={{fontFamily:"monospace",fontSize:11,marginBottom:8,opacity:0.9}}>{String(this.state.err?.message||this.state.err)}</div>
        <Button variant="ghost" onClick={()=>this.setState({err:null})}>재시도</Button>
      </div>);
    }
    return this.props.children;
  }
}

function Gauge({label,pct,used,total,unit="GB"}){
  const color=pct>85?BAD.fg:pct>60?"rgba(251,191,36,0.95)":OK.fg;
  return(<div style={{background:"var(--bg-card)",borderRadius:8,padding:"12px 16px",border:"1px solid var(--border)"}}>
    <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}><span style={{fontSize:12,fontWeight:600}}>{label}</span><span style={{fontSize:12,fontWeight:700,color}}>{pct}%</span></div>
    <div style={{height:6,borderRadius:3,background:"var(--border)"}}><div style={{height:6,borderRadius:3,background:color,width:Math.min(pct,100)+"%",transition:"width 0.3s"}}/></div>
    <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:4}}>{used} / {total} {unit}</div>
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
      <div style={{fontSize:12,fontWeight:700}}>{label}</div>
      <div style={{fontSize:11,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>
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
    <div style={{display:"flex",justifyContent:"space-between",gap:8,fontSize:10,color:"var(--text-secondary)"}}>
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
  const[sys,setSys]=useState({});const[resLog,setResLog]=useState([]);const[farmStatus,setFarmStatus]=useState({});
  const[resWindow,setResWindow]=useState("24h");
  const[loadBusy,setLoadBusy]=useState(false);
  const[qaReport,setQaReport]=useState({runs:[]});const[qaBusy,setQaBusy]=useState(false);const[qaMsg,setQaMsg]=useState("");
  const[etDlHistory,setEtDlHistory]=useState([]);
  const[editPerm,setEditPerm]=useState(null);const[permTabs,setPermTabs]=useState([]);
  const[bulkUsersText,setBulkUsersText]=useState("name\tusername\trole\n홍길동\thong\tuser");
  const[bulkUsersResult,setBulkUsersResult]=useState(null);
  const[bulkUsersBusy,setBulkUsersBusy]=useState(false);
  // v8.7.1: Admin Activity Log 필터
  const[logUsers,setLogUsers]=useState([]);
  const[logFilter,setLogFilter]=useState({username:"",action:"",tab:""});

  const[inquiry,setInquiry]=useState("");
  const sendInquiry=()=>{
    if(!inquiry.trim())return;
    sf("/api/admin/send-inquiry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",message:inquiry.trim()})}).then(()=>{setInquiry("");alert("관리자에게 전송되었습니다!");load();}).catch(e=>alert(e.message));
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
  useEffect(load,[]);
  useEffect(()=>{if(isAdmin&&tab==="logs")reloadLogs();},[logFilter.username,logFilter.action,logFilter.tab]);
  // v8.2.0: Bell dismiss / external read → re-load this tab's notif list immediately
  useEffect(()=>{
    const onRefresh=()=>load();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>window.removeEventListener("hol:notif-refresh",onRefresh);
  },[user]);

  const loadDl=()=>{
    const url=isAdmin?"/api/filebrowser/download-history":"/api/filebrowser/download-history?username="+(user?.username||"");
    const jobs=[sf(url).then(d=>setDlHistory(d.logs||[])).catch(()=>setDlHistory([]))];
    if(isAdmin){
      jobs.push(sf("/api/admin/ettime/download-log?limit=500").then(d=>setEtDlHistory(d.logs||[])).catch(()=>setEtDlHistory([])));
    }else{
      setEtDlHistory([]);
    }
    return Promise.all(jobs);
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
      .catch(e=>alert(e.message||"부하 시작 실패"))
      .finally(()=>setLoadBusy(false));
  };
  const stopPaverLoad=()=>{
    if(!isAdmin||loadBusy)return;
    setLoadBusy(true);
    sf("/api/monitor/load/stop",{method:"POST"})
      .then(d=>{setFarmStatus(d.state||{});loadSys();})
      .catch(e=>alert(e.message||"부하 중지 실패"))
      .finally(()=>setLoadBusy(false));
  };
  const action=(url,body)=>sf(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(()=>setTimeout(load,500));
  const savePerm=()=>{if(!editPerm)return;sf("/api/admin/set-tabs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:editPerm,tabs:permTabs})}).then(()=>{setEditPerm(null);load();setTab("perms");});};
  const submitBulkUsers=()=>{
    const text=String(bulkUsersText||"").trim();
    if(!text)return alert("붙여넣을 사용자 행이 없습니다.");
    setBulkUsersBusy(true);
    sf("/api/admin/bulk-users",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({text,default_password:"1111"})
    }).then((d)=>{
      setBulkUsersResult(d||{});
      load();
    }).catch((e)=>alert(e.message)).finally(()=>setBulkUsersBusy(false));
  };
  const markRead=(ids)=>{if(!ids.length)return;sf("/api/admin/mark-read-batch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",ids})}).then(()=>{load();window.dispatchEvent(new CustomEvent("hol:notif-refresh"));}).catch(()=>{});};
  const toggleRead=(n)=>{if(!n.id)return;markRead([n.id]);};

  // Tabs differ by role
  // v8.4.3 단위기능 페이지 철학: AWS 설정은 FileBrowser 톱니로 이관 예정 (제거).
  // v8.8.14: page_admins / backup_sched / activity_dash 3개 탭 추가.
  //   - page_admins: 각 페이지의 "위임 admin" 을 유저에게 부여 (각 페이지에서 관리는 각 페이지가 수행한다는 철학).
  //   - backup_sched: 자동 백업 주기 + 예약 1회 백업 (서버 점검 전 대비).
  //   - activity_dash: 최근 활동 요약 + 기능별 사용 현황 (어떤 기능이 활성화되어 있는지 파악).
  const adminTabs=[["users","사용자"],["notifs","알림"],["perms","권한"],["page_admins","페이지 위임"],["groups","그룹"],["inform_cfg","인폼 설정"],["mail_cfg","메일 API"],["llm_cfg","LLM"],["flowi_quality","Flow-i 품질"],["qa","QA 점검"],["logs","관리 로그"],["activity_dash","활동 대시보드"],["backup_sched","백업"],["downloads","다운로드"],["monitor","모니터"],["data_roots","데이터 루트"]];
  // v8.8.1: 일반 유저도 그룹 탭 사용 가능.
  const userTabs=[["notifs","알림"],["groups","그룹"],["logs","내 로그"],["downloads","내 다운로드"]];
  const tabs=isAdmin?adminTabs:userTabs;
  const tabItems=(tabs||[]).map(([k,l])=>({k,l,badge:k==="users"&&isAdmin?String(_arr(users).length):undefined}));
  const approvedUsers=_arr(users).filter(u=>u?.status==="approved").length;
  const pendingUsers=_arr(users).filter(u=>u?.status==="pending").length;
  const combinedDownloads=[
    ..._arr(dlHistory).map((d)=>({
      timestamp:d.timestamp||"",
      source:"파일 다운로드",
      sourceTone:"accent",
      username:d.username||"-",
      target:d.product||"-",
      detail:d.sql||"-",
      aux:d.select_cols||"all",
      rows:d.rows?.toLocaleString?.()||d.rows||"-",
      size:d.size_mb?`${d.size_mb}MB`:"-",
    })),
    ..._arr(etDlHistory).map((d)=>({
      timestamp:d.timestamp||"",
      source:"ET 레포트",
      sourceTone:"ok",
      username:d.username||"-",
      target:d.root_lot_id||"-",
      detail:d.type||"-",
      aux:"-",
      rows:"-",
      size:`${d.size_bytes||0} bytes`,
    })),
  ].sort((a,b)=>String(b.timestamp||"").localeCompare(String(a.timestamp||"")));
  const resourceChartHours=resWindow==="7d"?168:24;

  return(
    <div style={{padding:"24px 32px",background:"var(--bg-primary)",minHeight:"calc(100vh - 48px)",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif"}}>
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
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
            <thead><tr>{["이름","아이디","역할","상태","탭","작업"].map(h=><th key={h} style={{textAlign:"left",padding:"10px 14px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:11,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
            <tbody>{(Array.isArray(users)?users:[]).map((u,i)=><tr key={i}>
              <td style={{padding:"6px 14px",borderBottom:"1px solid var(--border)",fontSize:12}}>
                <NameInlineEdit u={u} onSave={(nm)=>sf("/api/admin/set-name",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u.username,name:nm})}).then(load).catch(e=>alert(e.message))}/>
              </td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:12}}>{u.username}</td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>{u.role}</td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}><Pill tone={u.status==="approved"?"ok":"warn"}>{u.status}</Pill></td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)",maxWidth:200,overflow:"hidden",textOverflow:"ellipsis"}}>{u.tabs||"default"}</td>
              <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>
                <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                  {u.status==="pending"&&<>
                    <Button variant="ghost" onClick={()=>action("/api/admin/approve",{username:u.username})} style={{color:"var(--ok,#22c55e)",border:"1px solid var(--ok,#22c55e)"}}>승인</Button>
                    <Button variant="danger" onClick={()=>action("/api/admin/reject",{username:u.username})}>거절</Button>
                  </>}
                  {u.status==="approved"&&u.role!=="admin"&&<>
                    <Button variant="ghost" onClick={()=>action("/api/admin/reset-password",{username:u.username})}>비번 초기화</Button>
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
                <div style={{fontSize:13,fontWeight:700}}>사용자 일괄 생성</div>
                <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:4}}>엑셀에서 `name / username / role`만 복붙하면 됩니다. 메일 주소는 메일 API domain 설정으로 username에서 자동 조합됩니다.</div>
              </div>
              <Button variant="primary" onClick={submitBulkUsers} disabled={bulkUsersBusy}>{bulkUsersBusy?"생성 중...":"일괄 생성"}</Button>
            </div>
            {bulkUsersResult&&<Banner tone={_arr(bulkUsersResult.skipped).length?"warn":"ok"} style={{marginBottom:10}}>
              생성 {_arr(bulkUsersResult.created).length}건 / 건너뜀 {_arr(bulkUsersResult.skipped).length}건 / 기본 비밀번호 {bulkUsersResult.default_password||"1111"}
            </Banner>}
            <textarea
              value={bulkUsersText}
              onChange={(e)=>setBulkUsersText(e.target.value)}
              spellCheck={false}
              style={{width:"100%",minHeight:220,resize:"vertical",padding:"12px 14px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,lineHeight:1.5,fontFamily:"ui-monospace, SFMono-Regular, Menlo, monospace",outline:"none"}}
            />
            {bulkUsersResult&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,marginTop:10}}>
              <div style={{padding:12,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
                <div style={{fontSize:11,fontWeight:700,color:"var(--text-secondary)",marginBottom:6}}>생성된 사용자</div>
                <div style={{display:"flex",flexWrap:"wrap",gap:6}}>
                  {_arr(bulkUsersResult.created).length?_arr(bulkUsersResult.created).map((row,idx)=><Pill key={idx} tone="ok">{row.username}</Pill>):<span style={{fontSize:11,color:"var(--text-secondary)"}}>없음</span>}
                </div>
              </div>
              <div style={{padding:12,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
                <div style={{fontSize:11,fontWeight:700,color:"var(--text-secondary)",marginBottom:6}}>건너뜀</div>
                <div style={{display:"flex",flexDirection:"column",gap:6,maxHeight:120,overflow:"auto"}}>
                  {_arr(bulkUsersResult.skipped).length?_arr(bulkUsersResult.skipped).map((row,idx)=><div key={idx} style={{fontSize:11,color:"var(--text-secondary)"}}>row {row.row}: {row.username||"-"} / {row.reason}</div>):<span style={{fontSize:11,color:"var(--text-secondary)"}}>없음</span>}
                </div>
              </div>
            </div>}
          </div>
          <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
            <div style={{fontSize:13,fontWeight:700,marginBottom:8}}>복붙 예시</div>
            <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:8}}>헤더가 있으면 자동 인식합니다. 헤더 없이 붙일 때도 `name, username, role` 순서로 봅니다.</div>
            <pre style={{margin:0,whiteSpace:"pre-wrap",fontSize:11,lineHeight:1.55,color:"var(--text-secondary)",fontFamily:"ui-monospace, SFMono-Regular, Menlo, monospace"}}>{`name\tusername\trole
홍길동\thong\tuser
김관리\tkimadmin\tadmin`}</pre>
          </div>
        </div>
      </div>}

      {/* Permissions (admin only) */}
      {tab==="perms"&&isAdmin&&<div>
        {/* O/X Permission Table */}
        {!editPerm&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",overflow:"auto",marginBottom:16}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
            <thead><tr>
              <th style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)",position:"sticky",left:0,zIndex:1}}>사용자</th>
              {ALL_TABS.map(t=><th key={t} style={{textAlign:"center",padding:"8px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:9,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{t}</th>)}
              <th style={{textAlign:"center",padding:"8px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)"}}></th>
            </tr></thead>
            <tbody>{_arr(users).filter(u=>u?.role!=="admin"&&u?.status==="approved").map((u,i)=>{
              const ut=_tabsToArray(u.tabs);
              return(<tr key={i}>
                <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontWeight:600,position:"sticky",left:0,background:"var(--bg-secondary)",zIndex:1}}>{u.username}</td>
                {ALL_TABS.map(t=><td key={t} style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
                  <span style={{fontSize:12,color:ut.includes(t)?"var(--ok,#22c55e)":"var(--bad,#ef4444)",fontWeight:700}}>{ut.includes(t)?"O":"X"}</span>
                </td>)}
                <td style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
                  <span onClick={()=>{setEditPerm(u.username);setPermTabs(ut);}} style={{color:"var(--info,#3b82f6)",cursor:"pointer",fontSize:11}}>편집</span>
                </td>
              </tr>);})}</tbody>
          </table>
        </div>}
        {/* Edit single user permissions */}
        {editPerm&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:400}}>
          <div style={{fontSize:14,fontWeight:700,marginBottom:12}}>권한: {editPerm}</div>
          {ALL_TABS.map(t=>(<label key={t} style={{display:"flex",alignItems:"center",gap:8,padding:"6px 0",fontSize:13,cursor:"pointer"}}><input type="checkbox" checked={permTabs.includes(t)} onChange={e=>{if(e.target.checked)setPermTabs([...permTabs,t]);else setPermTabs(permTabs.filter(x=>x!==t));}}/>{t}</label>))}
          <div style={{display:"flex",gap:8,marginTop:12}}>
            <Button variant="primary" onClick={savePerm} style={{padding:"8px 20px"}}>저장</Button>
            <Button variant="subtle" onClick={()=>{setEditPerm(null);}} style={{padding:"8px 16px"}}>취소</Button>
          </div></div>}
      </div>}

      {/* Notifications (everyone) */}
      {tab==="notifs"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
        {/* User inquiry box */}
        {!isAdmin&&<div style={{marginBottom:14,padding:"12px 14px",background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)"}}>
          <div style={{fontSize:11,fontWeight:600,color:"var(--accent)",marginBottom:6}}>관리자 문의</div>
          <div style={{display:"flex",gap:8}}>
            <input value={inquiry} onChange={e=>setInquiry(e.target.value)} placeholder="관리자에게 보낼 메시지를 입력하세요..."
              onKeyDown={e=>{if(e.key==="Enter")sendInquiry();}}
              style={{flex:1,padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",color:"var(--text-primary)",fontSize:12,outline:"none"}}/>
            <Button variant="primary" onClick={sendInquiry} disabled={!inquiry.trim()} style={{padding:"8px 16px",fontSize:12}}>전송</Button>
          </div>
        </div>}
        {/* Actions bar */}
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:11,color:"var(--text-secondary)"}}>읽지 않음 {_arr(notifs).filter(n=>!n?.read).length} / 전체 {_arr(notifs).length}</span>
          {_arr(notifs).some(n=>!n?.read)&&<button onClick={()=>markRead(_arr(notifs).filter(n=>!n?.read).map(n=>n.id).filter(Boolean))} style={{padding:"4px 14px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:11,fontWeight:600,cursor:"pointer"}}>모두 읽음으로 표시</button>}
        </div>
        <div style={{maxHeight:460,overflowY:"auto"}}>
        {_arr(notifs).length===0&&<div style={{color:"var(--text-secondary)",fontSize:13,padding:20,textAlign:"center"}}>알림 없음</div>}
        {[..._arr(notifs)].reverse().map((n,i)=>(
          <div key={n.id||i} style={{borderBottom:"1px solid var(--border)",fontSize:13,display:"flex",gap:8,alignItems:"flex-start",borderRadius:4,padding:"8px 6px",opacity:n.read?0.5:1}}>
            <input type="checkbox" checked={!!n.read} onChange={()=>{if(!n.read)toggleRead(n);}} disabled={!!n.read} title={n.read?"읽음":"읽음으로 표시"} style={{marginTop:2,accentColor:OK.fg,flexShrink:0,cursor:n.read?"default":"pointer"}}/>
            <div style={{flex:1}}>
              <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:4}}>
                <span style={{fontSize:10,padding:"2px 6px",borderRadius:3,fontWeight:700,color:WHITE,background:n.type==="approval"?WARN.fg:n.type==="message"?INFO.fg:SLATE}}>{n.type}</span>
                <span style={{fontWeight:n.read?400:600}}>{n.title}</span>
                <span style={{fontSize:11,color:"var(--text-secondary)",marginLeft:"auto"}}>{n.timestamp?.slice(0,16)}</span>
              </div>
              <div style={{color:"var(--text-secondary)",fontSize:12,paddingLeft:4}}>{n.body}</div>
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
              <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:4}}>user/admin 페르소나, edge case, 차트 schema, rule-based UX score 결과를 최근 10회까지 보관합니다.</div>
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
            <div style={{fontSize:12,fontWeight:700,marginBottom:10}}>최근 실행</div>
            <div style={{maxHeight:520,overflow:"auto",display:"grid",gap:10}}>
              {_arr(qaReport.runs).length===0&&<div style={{fontSize:12,color:"var(--text-secondary)"}}>리포트가 없습니다. QA 재실행으로 첫 결과를 생성하세요.</div>}
              {_arr(qaReport.runs).map((run,idx)=>(
                <div key={idx} style={{padding:12,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
                  <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:8,flexWrap:"wrap"}}>
                    <Pill tone={(run.issues||[]).length?"warn":"ok"}>{(run.issues||[]).length?`issues ${(run.issues||[]).length}`:"clean"}</Pill>
                    <span style={{fontSize:11,fontFamily:"monospace",color:"var(--text-secondary)"}}>{(run.run_at||"").replace("T"," ").slice(0,19)}</span>
                    <span style={{fontSize:11,fontFamily:"monospace",color:"var(--text-secondary)"}}>duration {run.duration_sec||0}s</span>
                  </div>
                  <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,fontSize:11}}>
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
                    {_arr(run.issues).slice(0,5).map((issue,i)=><div key={i} style={{fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace"}}>{issue.area}: {issue.desc}</div>)}
                  </div>}
                </div>
              ))}
            </div>
          </div>
          <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
            <div style={{fontSize:12,fontWeight:700,marginBottom:10}}>최신 UX Score</div>
            {_arr(qaReport.runs?.[0]?.ux_scores?.pages).length===0&&<div style={{fontSize:12,color:"var(--text-secondary)"}}>UX score 없음</div>}
            <div style={{display:"grid",gap:8}}>
              {_arr(qaReport.runs?.[0]?.ux_scores?.pages).map((page,idx)=>(
                <div key={idx} style={{padding:10,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                    <span style={{fontFamily:"monospace",fontWeight:700}}>{page.page}</span>
                    <Pill tone={page.score>=4?"ok":page.score>=3?"warn":"bad"}>{page.score}/5</Pill>
                  </div>
                  {!!_arr(page.notes).length&&<div style={{marginTop:6,fontSize:11,color:"var(--text-secondary)"}}>{_arr(page.notes).join(" · ")}</div>}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>}

      {/* Admin Log (v8.7.1) — 유저별/액션별 감사 로그 */}
      {tab==="logs"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
        {isAdmin&&<div style={{display:"flex",gap:10,marginBottom:12,flexWrap:"wrap",alignItems:"center"}}>
          <span style={{fontSize:12,fontWeight:700,color:"var(--accent)"}}>📋 Admin Activity Log</span>
          <select value={logFilter.username} onChange={e=>setLogFilter({...logFilter,username:e.target.value})}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,minWidth:160}}>
            <option value="">-- 유저 전체 --</option>
            {_arr(logUsers).map(u=><option key={u.username} value={u.username}>{u.username} ({u.count})</option>)}
          </select>
          <input placeholder="action 필터 (예: inform, login)" value={logFilter.action}
            onChange={e=>setLogFilter({...logFilter,action:e.target.value})}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,width:200}}/>
          <input placeholder="tab 필터 (inform/calendar/...)" value={logFilter.tab}
            onChange={e=>setLogFilter({...logFilter,tab:e.target.value})}
            style={{padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,width:170}}/>
          {(logFilter.username||logFilter.action||logFilter.tab)&&
            <button onClick={()=>setLogFilter({username:"",action:"",tab:""})}
              style={{padding:"6px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:BAD.fg,fontSize:11,cursor:"pointer"}}>× 초기화</button>}
          <button onClick={reloadLogs}
            style={{padding:"6px 12px",borderRadius:5,border:"none",background:"var(--accent)",color:WHITE,fontSize:11,fontWeight:600,cursor:"pointer"}}>↻ 새로고침</button>
          <span style={{fontSize:10,color:"var(--text-secondary)",marginLeft:"auto"}}>{_arr(logs).length}건</span>
        </div>}
        <div style={{maxHeight:540,overflowY:"auto",border:"1px solid var(--border)",borderRadius:6}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
            <thead style={{position:"sticky",top:0,background:"var(--bg-tertiary)",zIndex:1}}>
              <tr>{["시간","유저","탭","동작","상세"].map(h=>
                <th key={h} style={{textAlign:"left",padding:"8px 12px",color:"var(--text-secondary)",fontSize:11,borderBottom:"1px solid var(--border)",whiteSpace:"nowrap"}}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {_arr(logs).length===0&&<tr><td colSpan={5} style={{padding:20,textAlign:"center",color:"var(--text-secondary)"}}>로그 없음</td></tr>}
              {[..._arr(logs)].reverse().map((l,i)=>(
                <tr key={i} style={{borderBottom:"1px solid var(--border)"}}>
                  <td style={{padding:"6px 12px",fontFamily:"monospace",fontSize:11,color:"var(--accent)",whiteSpace:"nowrap"}}>{l.timestamp?.slice(0,19)?.replace("T"," ")}</td>
                  <td style={{padding:"6px 12px",fontWeight:600}}>{l.username||"-"}</td>
                  <td style={{padding:"6px 12px",fontSize:11,color:"var(--text-secondary)"}}>{l.tab?<span style={{padding:"2px 8px",borderRadius:999,background:"var(--bg-hover)",fontSize:10}}>{l.tab}</span>:"-"}</td>
                  <td style={{padding:"6px 12px",fontFamily:"monospace",fontSize:11}}>{l.action||"-"}</td>
                  <td style={{padding:"6px 12px",fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace",maxWidth:420,overflow:"hidden",textOverflow:"ellipsis"}} title={l.detail||""}>{l.detail||""}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </div>}

      {/* Downloads */}
      {tab==="downloads"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",overflow:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
          <thead><tr>{["시간","구분","사용자","대상","상세","컬럼","행","크기"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:11,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
          <tbody>
            {combinedDownloads.length===0&&<tr><td colSpan={8} style={{padding:20,textAlign:"center",color:"var(--text-secondary)"}}>다운로드 이력 없음</td></tr>}
            {combinedDownloads.map((d,i)=><tr key={i}>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)"}}>{String(d.timestamp||"").slice(0,19).replace("T"," ")}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>
                <Pill tone={d.sourceTone}>{d.source}</Pill>
              </td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{d.username}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{d.target}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,maxWidth:180,overflow:"hidden",textOverflow:"ellipsis"}} title={d.detail||""}>{d.detail||"-"}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:10,maxWidth:140,overflow:"hidden",textOverflow:"ellipsis",color:"var(--text-secondary)"}} title={d.aux||""}>{d.aux||"-"}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{d.rows}</td>
              <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace"}}>{d.size}</td>
            </tr>)}
          </tbody></table></div>}

      {/* Monitor (admin only) — v8.8.27: BE psutil 필드명에 맞춰 재매핑.
           구 FE: sys.cpu_pct/mem_pct/disk_pct/mem_used/mem_total/disk_used/disk_total
           신 BE: cpu_percent/memory_percent/disk_percent/memory_used_gb/memory_total_gb/disk_used_gb/disk_total_gb
           필드명 불일치로 사용량이 전부 0 으로 표시되던 문제. */}
      {tab==="monitor"&&isAdmin&&<div>
        <style>{FARM_ANIM}</style>
        {farmStatus.farming&&<div style={{background:WARN.bg,border:`1px solid ${WARN.fg}`,borderRadius:10,padding:16,marginBottom:16,display:"flex",alignItems:"center",gap:16}}>
          <div style={{animation:"fabFarm 1s ease-in-out infinite",fontSize:32}}>🧑‍🌾</div>
          <div><div style={{fontSize:14,fontWeight:700,color:WARN.fg}}>FAB-i 가 farming 중...</div>
            <div style={{fontSize:12,color:"var(--text-secondary)"}}>리소스를 활성 상태로 유지합니다 · {farmStatus.load_mode||"auto"} · MEM hold {farmStatus.load_memory_allocated_mb||0}MB</div></div>
        </div>}
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,marginBottom:12,padding:"10px 12px",border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-secondary)"}}>
          <div>
            <div style={{fontSize:12,fontWeight:800,color:"var(--text-primary)"}}>보도블럭 갈기</div>
            <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:2}}>Admin 수동 부하 테스트. 목표 85%, 최대 3분, 사용자 활동 감지 시 자동 중단됩니다.</div>
          </div>
          <div style={{display:"flex",gap:8,alignItems:"center",flexShrink:0}}>
            <Button variant="subtle" onClick={loadSys}>새로고침</Button>
            {farmStatus.load_active||farmStatus.farming
              ? <Button variant="danger" disabled={loadBusy} onClick={stopPaverLoad}>중지</Button>
              : <Button variant="primary" disabled={loadBusy} onClick={startPaverLoad}>보도블럭 갈기</Button>}
          </div>
        </div>
        {sys && sys.psutil === false && <div style={{marginBottom:12,padding:"8px 12px",border:`1px solid ${WARN.fg}`,background:WARN.bg,borderRadius:6,color:WARN.fg,fontSize:11}}>
          ⚠ psutil 미설치 (폴백 모드: Linux /proc/statvfs). 정확한 측정을 원하면 서버에 <code>pip install psutil</code>.
        </div>}
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:12,marginBottom:20}}>
          <Gauge label="CPU" pct={Math.round(sys.cpu_percent||0)} used={`${(sys.cpu_percent||0).toFixed(1)}%`} total="100%" unit=""/>
          <Gauge label="메모리" pct={Math.round(sys.memory_percent||0)} used={(sys.memory_used_gb||0).toFixed(1)} total={(sys.memory_total_gb||0).toFixed(1)} unit="GB"/>
          <Gauge label="디스크" pct={Math.round(sys.disk_percent||0)} used={(sys.disk_used_gb||0).toFixed(0)} total={(sys.disk_total_gb||0).toFixed(0)} unit="GB"/>
        </div>
        <div style={{marginBottom:16}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:12,marginBottom:8}}>
            <div style={{fontSize:12,fontWeight:700}}>리소스 차트</div>
            <div style={{display:"inline-flex",gap:4,padding:2,border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-secondary)"}}>
              {[["24h","24시간"],["7d","7일"]].map(([k,l])=><button key={k} type="button" onClick={()=>setResWindow(k)}
                style={{border:0,borderRadius:4,padding:"4px 9px",fontSize:11,fontWeight:resWindow===k?700:500,cursor:"pointer",background:resWindow===k?"var(--accent-glow)":"transparent",color:resWindow===k?"var(--accent)":"var(--text-secondary)"}}>{l}</button>)}
            </div>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",gap:12}}>
            <ResourceSparkline label="CPU" rows={resLog} metric="cpu_percent" color={chartPalette.series[4]} hours={resourceChartHours}/>
            <ResourceSparkline label="MEM" rows={resLog} metric="memory_percent" color={chartPalette.series[3]} hours={resourceChartHours}/>
            <ResourceSparkline label="DISK" rows={resLog} metric="disk_percent" color={chartPalette.series[1]} hours={resourceChartHours}/>
          </div>
        </div>
        {resLog.length>0&&<div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:12,maxHeight:300,overflow:"auto"}}>
          <div style={{fontSize:12,fontWeight:600,marginBottom:8}}>리소스 로그 ({resLog.length}건, 최근 50 표시)</div>
          <div style={{fontSize:10,fontFamily:"monospace"}}>{[..._arr(resLog)].reverse().slice(0,50).map((r,i)=><div key={i} style={{padding:"2px 0",color:"var(--text-secondary)"}}>{(r.timestamp||"").slice(11,19)} CPU:{(r.cpu_percent||0).toFixed(1)}% Mem:{(r.memory_percent||0).toFixed(1)}% Disk:{(r.disk_percent||0).toFixed(1)}%</div>)}</div>
        </div>}
        {_arr(resLog).length===0&&<div style={{fontSize:11,color:"var(--text-secondary)",padding:"10px 0"}}>리소스 로그 수집 중 (5분 간격). 잠시 후 새로고침해주세요.</div>}
      </div>}

      {/* Groups (admin only) — v8.5.0 */}
      {tab==="groups"&&<GroupsPanel allUsers={users} isAdmin={isAdmin} currentUser={user}/>}
      {tab==="inform_cfg"&&isAdmin&&<InformConfigPanel/>}

      {/* Categories (admin only) */}
      {tab==="categories"&&isAdmin&&<CategoryPanel/>}

      {/* Catalog (admin only) — matching tables + product config + S3 sync */}
      {tab==="catalog"&&isAdmin&&<CatalogPanel/>}

      {/* AWS Config (admin only) */}
      {tab==="aws"&&isAdmin&&<AWSPanel user={user}/>}

      {/* Messages sub-tab removed in v8.3.1 — functionality moved to Home Contact 섹션 */}

      {/* Data Roots (admin only) — v8.3.0: soft-landing env abstraction */}
      {tab==="data_roots"&&isAdmin&&<DataRootsPanel/>}

      {/* v8.7.2: Mail API (admin only) */}
      {tab==="mail_cfg"&&isAdmin&&<MailCfgPanel/>}

      {/* v9.0.4: Flowi LLM admin-managed token */}
      {tab==="llm_cfg"&&isAdmin&&<LlmCfgPanel/>}

      {/* v9.0.5: Flow-i structured feedback review loop */}
      {tab==="flowi_quality"&&isAdmin&&<FlowiQualityPanel/>}

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
  ["tracker","이슈 추적"],["informs","인폼 로그"],["meetings","회의관리"],["calendar","변경점 관리"],
  ["tablemap","테이블맵"],["ml","ML 분석"],
  ["spc","SPC"],["ettime","ET 레포트"],["wafer_map","웨이퍼 맵"],
  ["messages","문의함"],["groups","그룹"],
];

function PageAdminsPanel({users}){
  const [pa,setPa]=useState({});
  const [devguideUsers,setDevguideUsers]=useState([]);
  const [msg,setMsg]=useState("");
  const [busy,setBusy]=useState(false);
  const reload=()=>{
    Promise.all([sf("/api/admin/page-admins"), sf("/api/admin/settings")])
      .then(([paResp, settingsResp])=>{
        setPa(paResp.page_admins||{});
        setDevguideUsers(Array.isArray(settingsResp?.devguide_user) ? settingsResp.devguide_user : []);
      })
      .catch(e=>setMsg("로드 오류: "+e.message));
  };
  useEffect(reload,[]);
  // v8.8.21: 행=유저 / 열=페이지 매트릭스. admin 유저는 자동 전체 허용 (체크 disabled).
  // v8.8.28: Array.isArray 가드 — users 가 object 로 떨어져도 PageAdminsPanel 크래시 방지.
  const approved=(Array.isArray(users)?users:[]).filter(u=>u&&u.status==="approved");
  const isFullAdmin=(u)=>u.role==="admin" || ["admin","hol"].includes((u.username||"").toLowerCase());
  const toggle=(pageId,username)=>{
    const cur=new Set(pa[pageId]||[]);
    if(cur.has(username))cur.delete(username);else cur.add(username);
    const next={...pa,[pageId]:Array.from(cur).sort()};
    if(next[pageId].length===0)delete next[pageId];
    setBusy(true);setMsg("");
    sf("/api/admin/page-admins",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({page_id:pageId,usernames:next[pageId]||[]})})
      .then(()=>{setPa(next);setMsg("✔ "+pageId+" 저장");setBusy(false);setTimeout(()=>setMsg(""),2000);})
      .catch(e=>{setMsg("오류: "+e.message);setBusy(false);});
  };
  const toggleDevguide=(username)=>{
    const cur=new Set(devguideUsers||[]);
    if(cur.has(username))cur.delete(username);else cur.add(username);
    const next=Array.from(cur).sort();
    setBusy(true);setMsg("");
    sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({dashboard_refresh_minutes:10,dashboard_bg_refresh_minutes:10,devguide_user:next})})
      .then(()=>{setDevguideUsers(next);setMsg("✔ devguide_user 저장");setBusy(false);setTimeout(()=>setMsg(""),2000);})
      .catch(e=>{setMsg("오류: "+e.message);setBusy(false);});
  };
  return(<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16,overflow:"auto"}}>
    <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:12,flexWrap:"wrap"}}>
      <div style={{fontSize:14,fontWeight:700}}>페이지별 권한 매트릭스</div>
      <div style={{fontSize:11,color:"var(--text-secondary)"}}>
        행=유저 · 열=페이지. 체크한 유저는 해당 페이지 관리 기능(설정/카탈로그/권한 편집) 수행 가능.
        admin 역할 / <code>admin</code>·<code>hol</code> 계정은 자동 전체 허용 (수정 불가).
      </div>
      {msg&&<span style={{fontSize:11,color:msg.startsWith("✔")?OK.fg:BAD.fg,marginLeft:"auto"}}>{msg}</span>}
      {busy&&<span style={{fontSize:11,color:"var(--text-secondary)"}}>저장 중…</span>}
    </div>
    <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
      <thead><tr>
        <th style={{position:"sticky",left:0,background:"var(--bg-tertiary)",textAlign:"left",padding:"8px 12px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)",zIndex:1,minWidth:140}}>유저</th>
        {PAGE_IDS.map(([pid,label])=><th key={pid} title={pid} style={{textAlign:"center",padding:"8px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)",whiteSpace:"nowrap"}}>{label}</th>)}
      </tr></thead>
      <tbody>{approved.map(u=>{
        const full=isFullAdmin(u);
        return(<tr key={u.username}>
          <td style={{position:"sticky",left:0,background:"var(--bg-secondary)",padding:"6px 12px",borderBottom:"1px solid var(--border)",fontWeight:600,zIndex:1}}>
            {u.username}{full&&<span style={{marginLeft:6,fontSize:9,padding:"1px 6px",borderRadius:8,background:BAD.bg,color:BAD.fg,fontWeight:700}}>ADMIN</span>}
          </td>
          {PAGE_IDS.map(([pid])=>{
            const assigned=(pa[pid]||[]).includes(u.username);
            const checked=full||assigned;
            return(<td key={pid} style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
              <input type="checkbox" checked={checked} disabled={busy||full} onChange={()=>toggle(pid,u.username)} title={full?"admin 자동 허용":""}/>
            </td>);
          })}
        </tr>);
      })}</tbody>
    </table>
    <div style={{marginTop:16,paddingTop:16,borderTop:"1px solid var(--border)"}}>
      <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:10,flexWrap:"wrap"}}>
        <div style={{fontSize:13,fontWeight:700}}>DevGuide 접근 허용</div>
        <div style={{fontSize:11,color:"var(--text-secondary)"}}>admin_settings.devguide_user 목록. 체크된 일반 계정만 DevGuide 사이드바에 노출됩니다.</div>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit, minmax(220px, 1fr))",gap:8}}>
        {approved.filter(u=>!isFullAdmin(u)).map(u=>(
          <label key={u.username} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 10px",borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-card)",fontSize:12,cursor:busy?"default":"pointer"}}>
            <input type="checkbox" checked={devguideUsers.includes(u.username)} disabled={busy} onChange={()=>toggleDevguide(u.username)}/>
            <span>{userLabel(u)}</span>
          </label>
        ))}
      </div>
    </div>
  </div>);
}

// ── v8.8.14: Backup 주기 설정 + 1회 예약 ──
// interval_hours 조절 + enabled 토글 + "이 시각에 1회 백업" 예약 (서버 점검 대비).
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
  useEffect(reload,[]);
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
  const L={fontSize:11,color:"var(--text-secondary)",marginBottom:4,marginTop:10,fontWeight:600};
  const I={width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none"};
  return(<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,maxWidth:1100}}>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:8}}>자동 백업 설정</div>
      <div style={L}>활성화</div>
      <label style={{display:"flex",alignItems:"center",gap:6,fontSize:12}}><input type="checkbox" checked={!!form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})}/>자동 백업 사용</label>
      <div style={L}>주기 (시간)</div>
      <input type="number" min={1} max={168} value={form.interval_hours} onChange={e=>setForm({...form,interval_hours:parseInt(e.target.value)||24})} style={I}/>
      <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:4}}>12 → 12시간마다, 24 → 하루 1회 (기본). 1~168 시간 범위.</div>
      <div style={L}>보관 개수 (최대 5)</div>
      <input type="number" min={1} max={5} value={form.keep} onChange={e=>setForm({...form,keep:parseInt(e.target.value)||5})} style={I}/>
      <button onClick={saveSettings} style={{marginTop:14,padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:"pointer"}}>설정 저장</button>
      <button onClick={runNow} style={{marginTop:14,marginLeft:8,padding:"8px 20px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",cursor:"pointer"}}>🗄 즉시 백업</button>
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:8}}>예약 백업 (서버 점검 대비)</div>
      <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:10}}>지정한 시각이 지나면 백그라운드 스케줄러가 1회 백업을 실행하고 자동으로 취소됩니다. (주기 백업과 중복 실행되지 않음)</div>
      <div style={L}>예약 시각</div>
      <input type="datetime-local" value={sched.at} onChange={e=>setSched({...sched,at:e.target.value})} style={I}/>
      <div style={L}>사유 (메모)</div>
      <input value={sched.reason} onChange={e=>setSched({...sched,reason:e.target.value})} placeholder="pre-maintenance" style={I}/>
      <div style={{display:"flex",gap:8,marginTop:14}}>
        <button onClick={schedule} style={{padding:"8px 16px",borderRadius:6,border:"none",background:WARN.fg,color:WHITE,fontWeight:600,cursor:"pointer"}}>⏰ 예약</button>
        {st?.settings?.scheduled_at&&<button onClick={cancelSched} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>예약 취소</button>}
      </div>
      {st?.settings?.scheduled_at&&<div style={{marginTop:10,padding:"6px 10px",borderRadius:6,background:WARN.bg,color:WARN.fg,fontSize:11}}>🔔 예약됨: {st.settings.scheduled_at} ({st.settings.scheduled_reason||"-"})</div>}
    </div>
    <div style={{gridColumn:"1 / -1",background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:13,fontWeight:700,marginBottom:8}}>최근 백업</div>
      {st?.settings?.last&&<div style={{fontSize:11,color:st.settings.last.ok?OK.fg:BAD.fg,marginBottom:8}}>
        {st.settings.last.ok?"✔":"✗"} {st.settings.last.at} · {st.settings.last.reason||"-"} · {st.settings.last.bytes?Math.round(st.settings.last.bytes/1024)+" KB":""} {st.settings.last.error?"· "+st.settings.last.error:""}
      </div>}
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
        <thead><tr><th style={{textAlign:"left",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>파일</th><th style={{textAlign:"right",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>크기</th><th style={{textAlign:"left",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>시각</th><th style={{textAlign:"right",padding:"6px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>작업</th></tr></thead>
        <tbody>{(st?.backups||[]).map(b=>(<tr key={b.filename}><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{b.filename}</td><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",textAlign:"right"}}>{Math.round((b.size||0)/1024).toLocaleString()} KB</td><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{b.modified}</td><td style={{padding:"6px 10px",borderBottom:"1px solid var(--border)",textAlign:"right"}}><button onClick={()=>restoreBackup(b)} style={{padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:WARN.fg,cursor:"pointer",fontSize:10}}>롤백</button></td></tr>))}</tbody>
      </table>
    </div>
    {msg&&<div style={{gridColumn:"1 / -1",fontSize:11,color:msg.startsWith("✔")?OK.fg:BAD.fg}}>{msg}</div>}
  </div>);
}

// ── v8.8.14: Activity Dashboard ──
// 최근 N일 활동 요약 + 기능(action prefix) 별 사용 현황. admin 이 "누가 뭘 쓰는지",
// "어떤 기능이 활성화되어 있는지" 한눈에 파악할 수 있게.
function ActivityDashboardPanel(){
  const [days,setDays]=useState(7);
  const [summary,setSummary]=useState(null);
  const [features,setFeatures]=useState(null);
  const [err,setErr]=useState("");
  const reload=()=>{
    setErr("");
    sf("/api/admin/activity/summary?days="+days).then(setSummary).catch(e=>setErr("요약 로드 오류: "+e.message));
    sf("/api/admin/activity/features?days="+days).then(setFeatures).catch(()=>{});
  };
  useEffect(reload,[days]);
  const barItem=(label,val,max,color)=>(<div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
    <span style={{fontSize:11,minWidth:120,fontFamily:"monospace"}}>{label}</span>
    <div style={{flex:1,height:14,background:"var(--bg-tertiary)",borderRadius:3,overflow:"hidden"}}>
      <div style={{width:(max>0?(100*val/max):0)+"%",height:"100%",background:color}}/>
    </div>
    <span style={{fontSize:11,minWidth:50,textAlign:"right",color:"var(--text-secondary)"}}>{val}</span>
  </div>);
  const maxUser=summary?Math.max(0,...Object.values(_obj(summary.by_user))):0;
  const maxAct=summary?Math.max(0,...Object.values(_obj(summary.by_action))):0;
  const maxDay=summary?Math.max(0,...Object.values(_obj(summary.by_day))):0;
  return(<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
    <div style={{gridColumn:"1 / -1",display:"flex",alignItems:"center",gap:12}}>
      <span style={{fontSize:14,fontWeight:700}}>활동 대시보드</span>
      <span style={{fontSize:11,color:"var(--text-secondary)"}}>최근</span>
      {[1,7,30,90].map(d=>(<span key={d} onClick={()=>setDays(d)} style={{cursor:"pointer",fontSize:11,padding:"3px 10px",borderRadius:6,background:days===d?"var(--accent-glow)":"transparent",color:days===d?"var(--accent)":"var(--text-secondary)",fontWeight:days===d?700:500,border:"1px solid "+(days===d?"var(--accent)":"var(--border)")}}>{d}일</span>))}
      {summary&&<span style={{fontSize:11,color:"var(--text-secondary)",marginLeft:"auto"}}>총 {summary.total}건 · 기능 {features?.feature_count||0}개</span>}
      {err&&<span style={{fontSize:11,color:BAD.fg}}>{err}</span>}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:13,fontWeight:700,marginBottom:10}}>유저별</div>
      {summary?_entries(summary.by_user).map(([u,v])=>barItem(u,v,maxUser,INFO.fg)):<span style={{color:"var(--text-secondary)",fontSize:11}}>로딩…</span>}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:13,fontWeight:700,marginBottom:10}}>액션별</div>
      <div style={{maxHeight:340,overflowY:"auto"}}>
        {summary?_entries(summary.by_action).map(([a,v])=>barItem(a,v,maxAct,chartPalette.series[6])):<span style={{color:"var(--text-secondary)",fontSize:11}}>로딩…</span>}
      </div>
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:13,fontWeight:700,marginBottom:10}}>일자별</div>
      {summary?_entries(summary.by_day).map(([d,v])=>barItem(d,v,maxDay,OK.fg)):<span style={{color:"var(--text-secondary)",fontSize:11}}>로딩…</span>}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:13,fontWeight:700,marginBottom:10}}>기능별 활성 현황</div>
      <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:8}}>action prefix (예: inform, splittable, admin) 기준</div>
      <div style={{maxHeight:340,overflowY:"auto"}}>
        {_arr(features?.features).map(f=>(<div key={f.feature} style={{padding:"8px 10px",marginBottom:6,borderRadius:6,background:"var(--bg-tertiary)",border:"1px solid var(--border)"}}>
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <span style={{fontSize:12,fontWeight:700,fontFamily:"monospace",color:"var(--accent)"}}>{f.feature}</span>
            <span style={{fontSize:10,color:"var(--text-secondary)"}}>{f.count}건 · 유저 {f.user_count}명</span>
            <span style={{flex:1}}/>
            <span style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace"}} title={`first: ${f.first_seen}\nlast: ${f.last_seen}`}>~{(f.last_seen||"").slice(0,16)}</span>
          </div>
          <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:3,fontFamily:"monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>
            top: {_entries(f.top_actions).map(([k,v])=>k+"("+v+")").join(" · ")}
          </div>
        </div>))}
        {_arr(features?.features).length===0&&<span style={{color:"var(--text-secondary)",fontSize:11}}>로딩…</span>}
      </div>
    </div>
    <div style={{gridColumn:"1 / -1",background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:13,fontWeight:700,marginBottom:10}}>최근 이벤트 (50건)</div>
      <div style={{maxHeight:400,overflowY:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
          <thead><tr>{["시각","유저","action","tab","detail"].map(h=><th key={h} style={{textAlign:"left",padding:"4px 8px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:10,color:"var(--text-secondary)"}}>{h}</th>)}</tr></thead>
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

function FlowiQualityPanel(){
  const[days,setDays]=useState(30);
  const[data,setData]=useState(null);
  const[err,setErr]=useState("");
  const[msg,setMsg]=useState("");
  const[promoting,setPromoting]=useState("");
  const[defaults,setDefaults]=useState(FLOWI_DEFAULTS_FALLBACK);
  const[defaultsMsg,setDefaultsMsg]=useState("");
  const[defaultsBusy,setDefaultsBusy]=useState(false);
  const reload=()=>{
    setErr("");
    sf(`/api/llm/flowi/feedback/summary?days=${days}&limit=300`).then(setData).catch(e=>setErr("로드 오류: "+e.message));
  };
  useEffect(reload,[days]);
  const reloadDefaults=()=>{
    sf("/api/admin/settings").then(d=>setDefaults(normalizeFlowiDefaults(d.flowi_defaults||{}))).catch(e=>setDefaultsMsg("기본값 로드 오류: "+e.message));
  };
  useEffect(reloadDefaults,[]);
  const patchChart=(kind,next)=>setDefaults(d=>({...d,chart_defaults:{...d.chart_defaults,[kind]:{..._obj(d.chart_defaults?.[kind]),...next}}}));
  const patchPolicy=(next)=>setDefaults(d=>({...d,feedback_policy:{..._obj(d.feedback_policy),...next,auto_apply_to_rag:false}}));
  const patchKnowledge=(next)=>setDefaults(d=>({...d,engineer_knowledge:{..._obj(d.engineer_knowledge),...next}}));
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
    <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:4}}>{label}</div>
    <div style={{fontSize:22,fontWeight:900,color:tone==="ok"?OK.fg:tone==="bad"?BAD.fg:tone==="warn"?WARN.fg:"var(--text-primary)",fontFamily:"monospace"}}>{Number(val||0).toLocaleString()}</div>
  </div>;
  const counterList=(title,obj,color)=><div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14,minHeight:160}}>
    <div style={{fontSize:13,fontWeight:800,marginBottom:10}}>{title}</div>
    <div style={{display:"grid",gap:6}}>
      {_entries(obj).slice(0,10).map(([k,v])=><div key={k} style={{display:"grid",gridTemplateColumns:"minmax(90px,0.8fr) minmax(80px,1fr) 42px",alignItems:"center",gap:8}}>
        <span style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={k}>{title==="실패 유형"?labelTag(k):k}</span>
        <div style={{height:9,borderRadius:999,background:"var(--bg-tertiary)",overflow:"hidden"}}><div style={{height:"100%",width:`${Math.min(100,(Number(v)||0)/Math.max(1,...Object.values(_obj(obj)).map(Number))*100)}%`,background:color}}/></div>
        <span style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace",textAlign:"right"}}>{v}</span>
      </div>)}
      {_entries(obj).length===0&&<div style={{fontSize:11,color:"var(--text-secondary)"}}>데이터 없음</div>}
    </div>
  </div>;
  const feedbackCard=(rec,idx)=><div key={rec.id||idx} style={{padding:12,borderRadius:8,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
    <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginBottom:7}}>
      <Pill tone={toneFor(rec.rating,rec.tags)}>{rec.rating||"neutral"}</Pill>
      <span style={{fontSize:11,fontFamily:"monospace",color:"var(--text-secondary)"}}>{String(rec.timestamp||"").replace("T"," ").slice(0,19)}</span>
      <span style={{fontSize:11,fontWeight:700}}>{rec.username||"-"}</span>
      <span style={{fontSize:10,color:"var(--accent)",fontFamily:"monospace"}}>{rec.intent||rec.tool_summary?.intent||"-"}</span>
      {rec.golden_candidate&&<Pill tone="warn">golden 후보</Pill>}
      <span style={{flex:1}}/>
      <Button variant="ghost" disabled={promoting===rec.id} onClick={()=>promote(rec)}>{promoting===rec.id?"저장 중":"Golden 저장"}</Button>
    </div>
    <div style={{fontSize:11,color:"var(--text-secondary)",lineHeight:1.55,display:"grid",gap:5}}>
      <div><b style={{color:"var(--text-primary)"}}>Prompt</b> {rec.prompt_excerpt||"-"}</div>
      <div><b style={{color:"var(--text-primary)"}}>Answer</b> {rec.answer_excerpt||"-"}</div>
      {(rec.note||rec.correct_route||rec.expected_workflow||rec.data_refs)&&<div style={{padding:8,borderRadius:6,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>
        {rec.note&&<div>의견: {rec.note}</div>}
        {rec.expected_workflow&&<div>기대 workflow: {rec.expected_workflow}</div>}
        {rec.data_refs&&<div>정답 DB/컬럼: {rec.data_refs}</div>}
        {rec.correct_route&&<div>정답 경로: {rec.correct_route}</div>}
      </div>}
      <div style={{display:"flex",gap:5,flexWrap:"wrap"}}>
        {_arr(rec.tags).map(t=><span key={t} style={{fontSize:10,padding:"2px 7px",borderRadius:999,border:"1px solid var(--border)",color:taxonomy[t]?.tone==="bad"?BAD.fg:taxonomy[t]?.tone==="warn"?WARN.fg:OK.fg}}>{labelTag(t)}</span>)}
        {rec.tool_summary?.action&&<span style={{fontSize:10,padding:"2px 7px",borderRadius:999,border:"1px solid var(--border)",color:"var(--text-secondary)",fontFamily:"monospace"}}>{rec.tool_summary.action}</span>}
        {rec.elapsed_ms!=null&&<span style={{fontSize:10,padding:"2px 7px",borderRadius:999,border:"1px solid var(--border)",color:"var(--text-secondary)",fontFamily:"monospace"}}>{rec.elapsed_ms}ms</span>}
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
  const knowledge=defaults.engineer_knowledge||{};
  const L={fontSize:11,color:"var(--text-secondary)",marginBottom:4,fontWeight:700};
  const I={width:"100%",padding:"7px 9px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",boxSizing:"border-box"};
  return(<div style={{display:"grid",gap:16}}>
    <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
      <div>
        <div style={{fontSize:14,fontWeight:800}}>Flow-i 품질 피드백</div>
        <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:3}}>실패 유형을 모아 tool schema, 확인 질문, cache/query 경로, golden workflow를 개선합니다.</div>
      </div>
      <span style={{marginLeft:"auto",fontSize:11,color:"var(--text-secondary)"}}>최근</span>
      {[7,30,90,180].map(d=><button key={d} type="button" onClick={()=>setDays(d)}
        style={{padding:"4px 10px",borderRadius:6,border:"1px solid "+(days===d?"var(--accent)":"var(--border)"),background:days===d?"var(--accent-glow)":"transparent",color:days===d?"var(--accent)":"var(--text-secondary)",fontSize:11,fontWeight:days===d?800:500,cursor:"pointer"}}>{d}일</button>)}
      <Button variant="ghost" onClick={reload}>새로고침</Button>
    </div>
    {err&&<Banner tone="bad">{err}</Banner>}
    {msg&&<Banner tone={msg.includes("저장됨")?"ok":"warn"}>{msg}</Banner>}
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
      <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap",marginBottom:12}}>
        <div>
          <div style={{fontSize:13,fontWeight:800}}>Flow-i 운영 기본값</div>
          <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:3}}>홈 Flow-i 차트와 엔지니어 지식 업데이트 정책입니다. 일반 유저는 이 값을 수정할 수 없습니다.</div>
        </div>
        <span style={{flex:1}}/>
        <Button variant="ghost" onClick={reloadDefaults} disabled={defaultsBusy}>불러오기</Button>
        <Button onClick={saveDefaults} disabled={defaultsBusy}>{defaultsBusy?"저장 중":"저장"}</Button>
      </div>
      {defaultsMsg&&<div style={{fontSize:11,color:defaultsMsg.includes("오류")?BAD.fg:OK.fg,marginBottom:10}}>{defaultsMsg}</div>}
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
        <label style={{display:"flex",alignItems:"center",gap:8,fontSize:12,color:"var(--text-primary)",padding:"8px 10px",borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
          <input type="checkbox" checked={false} disabled onChange={()=>{}}/>
          피드백 RAG 자동반영 비활성
        </label>
        <label style={{display:"flex",alignItems:"center",gap:8,fontSize:12,color:"var(--text-primary)",padding:"8px 10px",borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
          <input type="checkbox" checked={policy.review_required!==false} onChange={e=>patchPolicy({review_required:e.target.checked})}/>
          피드백 리뷰 후 Golden 승격
        </label>
        <label style={{display:"flex",alignItems:"center",gap:8,fontSize:12,color:"var(--text-primary)",padding:"8px 10px",borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
          <input type="checkbox" checked={knowledge.rag_update_requires_marker!==false} onChange={e=>patchKnowledge({rag_update_requires_marker:e.target.checked})}/>
          RAG 업데이트 marker 필요
        </label>
        <label style={{display:"flex",alignItems:"center",gap:8,fontSize:12,color:"var(--text-primary)",padding:"8px 10px",borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
          <input type="checkbox" checked={knowledge.custom_knowledge_append_only!==false} onChange={e=>patchKnowledge({custom_knowledge_append_only:e.target.checked})}/>
          엔지니어 지식 append-only
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
          <div style={{fontSize:13,fontWeight:800}}>리뷰 큐</div>
          <span style={{fontSize:10,color:"var(--text-secondary)"}}>개선 필요, 실패 유형, golden 후보</span>
        </div>
        <div style={{display:"grid",gap:10,maxHeight:720,overflow:"auto"}}>
          {review.length?review.map(feedbackCard):<div style={{fontSize:12,color:"var(--text-secondary)",padding:20,textAlign:"center"}}>리뷰할 피드백이 없습니다.</div>}
        </div>
      </div>
      <div style={{display:"grid",gap:12}}>
        <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
          <div style={{fontSize:13,fontWeight:800,marginBottom:10}}>최근 Golden cases</div>
          <div style={{display:"grid",gap:8,maxHeight:280,overflow:"auto"}}>
            {golden.slice(0,12).map(g=><div key={g.id} style={{padding:9,borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
              <div style={{fontSize:10,fontFamily:"monospace",color:"var(--accent)",marginBottom:4}}>{g.expected_intent||"-"} · {g.expected_tool||"-"}</div>
              <div style={{fontSize:11,color:"var(--text-secondary)",lineHeight:1.45}}>{g.prompt||"-"}</div>
            </div>)}
            {!golden.length&&<div style={{fontSize:11,color:"var(--text-secondary)"}}>아직 저장된 golden case가 없습니다.</div>}
          </div>
        </div>
        <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:14}}>
          <div style={{fontSize:13,fontWeight:800,marginBottom:10}}>최근 전체 피드백</div>
          <div style={{display:"grid",gap:8,maxHeight:360,overflow:"auto"}}>
            {recent.slice(0,20).map((r,i)=><div key={r.id||i} style={{padding:8,borderRadius:7,border:"1px solid var(--border)",background:"var(--bg-primary)"}}>
              <div style={{display:"flex",gap:6,alignItems:"center",marginBottom:4}}>
                <Pill tone={toneFor(r.rating,r.tags)}>{r.rating}</Pill>
                <span style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace"}}>{String(r.timestamp||"").slice(5,16).replace("T"," ")}</span>
                <span style={{fontSize:10,color:"var(--text-secondary)"}}>{r.username}</span>
              </div>
              <div style={{fontSize:11,color:"var(--text-secondary)",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={r.prompt_excerpt}>{r.prompt_excerpt||"-"}</div>
            </div>)}
            {!recent.length&&<div style={{fontSize:11,color:"var(--text-secondary)"}}>피드백 없음</div>}
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
      onKeyDown={e=>{if(e.key==="Enter"){safeSave(val.trim());setEdit(false);}else if(e.key==="Escape"){setVal(u?.name||"");setEdit(false);}}}
      placeholder="이름"
      style={{padding:"3px 6px",borderRadius:3,border:"1px solid var(--accent)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,minWidth:140}}/>
    <span onClick={()=>{safeSave(val.trim());setEdit(false);}} style={{marginLeft:6,cursor:"pointer",color:OK.fg,fontSize:11}}>✔</span>
    <span onClick={()=>{setVal(u?.name||"");setEdit(false);}} style={{marginLeft:4,cursor:"pointer",color:BAD.fg,fontSize:11}}>✕</span>
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
  useEffect(reload,[]);
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

  const L={fontSize:11,color:"var(--text-secondary)",marginBottom:4,marginTop:10,fontWeight:600};
  const I={width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",fontFamily:"'Segoe UI',Arial,sans-serif"};
  return(<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:900}}>
    <div style={{fontSize:14,fontWeight:700,marginBottom:4}}>✉ 메일 API 설정</div>
    <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.6}}>
      사내 메일 API 규약: <code>multipart/form-data</code> POST.  top-level form field 는 <b><code>mailsendString</code></b> 한 개
      (값 = <code>{"{content, receiverList, senderMailAddress, statusCode, title}"}</code> 를 JSON 직렬화한 문자열),
      그리고 첨부가 있으면 <code>files</code> parts.  2MB 본문 / 10MB 첨부 한도.  URL 에 <code>dry-run</code> 입력 시 실제 전송 없이 preview 만 반환.<br/>
      <b>수신자는 각 페이지의 메일 발송 다이얼로그에서 선택</b> — Admin 에서 그룹 관리하지 않음.
    </div>
    <label style={{display:"flex",alignItems:"center",gap:6,fontSize:12,marginBottom:6}}>
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
      <div style={{fontSize:12,fontWeight:700,marginBottom:6}}>🔍 전체 API 틀 미리보기</div>
      <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:6,lineHeight:1.5}}>
        현재 저장된 설정 기반으로 실제 호출 시 전송될 request 구조. 본문/제목/수신자는 인폼·회의 등 발송 화면에서 채워집니다.
      </div>
      <pre style={{fontSize:10,lineHeight:1.45,padding:10,background:"var(--bg-primary)",border:"1px solid var(--border)",borderRadius:4,overflow:"auto",maxHeight:360,fontFamily:"monospace",margin:0,color:"var(--text-primary)"}}>
{JSON.stringify(preview, null, 2)}
      </pre>
    </div>

    <div style={{marginTop:14,display:"flex",gap:8,alignItems:"center"}}>
      <button onClick={save} disabled={busy} style={{padding:"8px 18px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:busy?"wait":"pointer"}}>{busy?"저장 중…":"저장"}</button>
      {msg&&<span style={{fontSize:11,color:msg.startsWith("오류")?BAD.fg:OK.fg}}>{msg}</span>}
    </div>
  </div>);
}

// ── v9.0.4: Flowi LLM 설정 — admin token 을 서버 설정에 저장하고 사용자는 실행만 한다. ──
function LlmCfgPanel(){
  const[cfg,setCfg]=useState({enabled:false,api_url:"",model:"",mode:"fast",admin_token:"",provider:"generic",auth_mode:"bearer",system_name:"",user_id:"",user_type:"",format:"openai",timeout_s:20});
  const[msg,setMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const[testBusy,setTestBusy]=useState(false);
  const[testPrompt,setTestPrompt]=useState("연결 확인입니다. 정상 수신했다면 확인완료 라고만 답하세요.");
  const[showToken,setShowToken]=useState(false);
  const normalize=(l={})=>{
    const provider=(l.provider||"generic").toString().trim()||"generic";
    return {
      enabled:!!l.enabled,
      api_url:l.api_url||"",
      model:l.model||"",
      mode:l.mode||"fast",
      admin_token:l.admin_token||"",
      provider,
      auth_mode:l.auth_mode||(provider==="playground"?"dep_ticket":"bearer"),
      system_name:l.system_name||(provider==="playground"?"playground":""),
      user_id:l.user_id||"",
      user_type:l.user_type||"",
      format:l.format||"openai",
      timeout_s:Number(l.timeout_s||20),
    };
  };
  const reload=()=>{
    sf("/api/admin/settings").then(d=>setCfg(normalize(d.llm||{}))).catch(e=>setMsg("로드 오류: "+e.message));
  };
  useEffect(reload,[]);
  const patch=(next)=>setCfg(c=>({...c,...next}));
  const setProvider=(provider)=>setCfg(c=>({
    ...c,
    provider,
    auth_mode:provider==="playground"?"dep_ticket":(c.auth_mode==="dep_ticket"?"bearer":(c.auth_mode||"bearer")),
    system_name:provider==="playground"?(c.system_name||"playground"):c.system_name,
    format:provider==="playground"||provider==="openai"||provider==="openai_compatible"?"openai":c.format,
    api_url:provider==="openai"&&!c.api_url?"https://api.openai.com/v1":c.api_url,
    model:provider==="openai"&&!c.model?"gpt-5-nano":c.model,
  }));
  const save=()=>{
    setBusy(true);setMsg("");
    sf("/api/admin/settings").then(cur=>sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      dashboard_refresh_minutes:cur.dashboard_refresh_minutes??10,
      dashboard_bg_refresh_minutes:cur.dashboard_bg_refresh_minutes??10,
      llm:{
        enabled:!!cfg.enabled,
        api_url:cfg.api_url,
        model:cfg.model,
        mode:cfg.mode||"fast",
        admin_token:cfg.admin_token,
        provider:cfg.provider||"generic",
        auth_mode:cfg.auth_mode||(cfg.provider==="playground"?"dep_ticket":"bearer"),
        system_name:cfg.system_name,
        user_id:cfg.user_id,
        user_type:cfg.user_type,
        format:cfg.format||"openai",
        timeout_s:Number(cfg.timeout_s)||20,
      },
    })})).then(()=>{setMsg("저장됨");reload();}).catch(e=>setMsg("오류: "+e.message)).finally(()=>setBusy(false));
  };
  const test=()=>{
    setTestBusy(true);setMsg("");
    sf("/api/llm/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:testPrompt||"연결 확인"})})
      .then(d=>setMsg(d?.ok===false?"테스트 실패: "+(d.error||"unknown"):"테스트 완료: "+String(d?.text||"응답 있음").slice(0,160)))
      .catch(e=>setMsg("테스트 오류: "+e.message))
      .finally(()=>setTestBusy(false));
  };
  const L={fontSize:11,color:"var(--text-secondary)",marginBottom:4,marginTop:10,fontWeight:600};
  const I={width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",boxSizing:"border-box"};
  const isPlayground=(cfg.provider||"generic")==="playground";
  const authMode=cfg.auth_mode||(isPlayground?"dep_ticket":"bearer");
  const previewHeaders={
    Accept:"application/json",
    "Content-Type":"application/json",
    ...(authMode==="bearer"&&cfg.admin_token?{Authorization:"Bearer <admin_token>"}:{}),
    ...(authMode==="dep_ticket"&&cfg.admin_token?{"x-dep-ticket":"<credential_key>"}:{}),
    ...(isPlayground?{
      "Send-System-Name":cfg.system_name||"playground",
      "User-Id":cfg.user_id||"(입력 필요)",
      "User-Type":cfg.user_type||"(입력 필요)",
      "Prompt-Msg-Id":"<uuid4>",
      "Completion-Msg-Id":"<uuid4>",
    }:{}),
  };
  const previewBody=isPlayground?{
    model:cfg.model||"(입력 필요)",
    messages:[{role:"system",content:"You are a helpful assistant."},{role:"user",content:"..."}],
    temperature:0.5,
    stream:false,
  }:{
    ...(cfg.mode?{mode:cfg.mode}:{}),
    ...(cfg.model?{model:cfg.model}:{}),
    [cfg.format==="raw"?"prompt":"messages"]:cfg.format==="raw"?"...":[{role:"system",content:"..."},{role:"user",content:"..."}],
  };
  const preview={
    enabled:!!cfg.enabled,
    request:"POST "+(cfg.api_url||"(설정 필요)"),
    provider:cfg.provider||"generic",
    auth_mode:authMode,
    headers:previewHeaders,
    body:previewBody,
    users:"홈 Flowi 사용자는 별도 LLM token 입력 없이 서버 설정으로 실행",
  };
  return(<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:900}}>
    <div style={{fontSize:14,fontWeight:700,marginBottom:4}}>Flowi LLM 설정</div>
    <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.6}}>
      Admin 이 저장한 credential 을 서버에서 사용합니다. 일반 사용자는 홈 Flowi 콘솔에서 질문만 입력하고, 답변과 기능별 표/추천은 홈에서 바로 확인합니다.
    </div>
    <label style={{display:"flex",alignItems:"center",gap:6,fontSize:12,marginBottom:6}}>
      <input type="checkbox" checked={!!cfg.enabled} onChange={e=>patch({enabled:e.target.checked})}/>
      LLM 기능 활성화
    </label>
    <div style={{display:"grid",gridTemplateColumns:"1fr 2fr",gap:10}}>
      <div>
        <div style={L}>API Profile</div>
        <select value={cfg.provider||"generic"} onChange={e=>setProvider(e.target.value)} style={I}>
          <option value="openai">OpenAI API</option>
          <option value="openai_compatible">OpenAI 호환 API</option>
          <option value="generic">Custom Generic</option>
          <option value="playground">사내 Playground API</option>
        </select>
      </div>
      <div>
        <div style={L}>API URL</div>
        <input value={cfg.api_url} onChange={e=>patch({api_url:e.target.value})} placeholder={cfg.provider==="openai"?"https://api.openai.com/v1":"https://llm.internal/v1/chat/completions"} style={I}/>
      </div>
    </div>
    <div style={{display:"grid",gridTemplateColumns:isPlayground?"2fr 1fr 1fr":"2fr 1fr 1fr 1fr",gap:10}}>
      <div>
        <div style={L}>Model</div>
        <input value={cfg.model} onChange={e=>patch({model:e.target.value})} placeholder={cfg.provider==="openai"?"gpt-5-nano":"internal-model"} style={I}/>
      </div>
      {!isPlayground&&<div>
        <div style={L}>Mode</div>
        <select value={cfg.mode||"fast"} onChange={e=>patch({mode:e.target.value})} style={I}>
          <option value="fast">fast</option>
          <option value="balanced">balanced</option>
          <option value="quality">quality</option>
        </select>
      </div>}
      <div>
        <div style={L}>Format</div>
        <select value={cfg.format||"openai"} onChange={e=>patch({format:e.target.value})} disabled={isPlayground} style={{...I,opacity:isPlayground?0.65:1}}>
          <option value="openai">openai</option>
          <option value="raw">raw</option>
        </select>
      </div>
      <div>
        <div style={L}>Timeout (sec)</div>
        <input type="number" min={3} max={120} value={cfg.timeout_s||20} onChange={e=>patch({timeout_s:Number(e.target.value)})} style={{...I,fontFamily:"monospace"}}/>
      </div>
    </div>
    <div style={L}>{isPlayground?"Credential Key":"Admin LLM Token"} <span style={{fontWeight:400,color:"var(--text-secondary)"}}>({isPlayground?"x-dep-ticket 로 전송":"Authorization Bearer 로 전송"})</span></div>
    <div style={{display:"flex",gap:8}}>
      <input type={showToken?"text":"password"} value={cfg.admin_token} onChange={e=>patch({admin_token:e.target.value})} placeholder={isPlayground?"사내 credential key":"admin token"} autoComplete="off" style={{...I,fontFamily:"monospace",flex:1}}/>
      <button type="button" onClick={()=>setShowToken(!showToken)} style={{padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>{showToken?"숨김":"보기"}</button>
    </div>
    <div style={{display:isPlayground?"grid":"none",gridTemplateColumns:"1fr 1fr 1fr",gap:10}}>
      <div>
        <div style={L}>Send-System-Name</div>
        <input value={cfg.system_name} onChange={e=>patch({system_name:e.target.value})} placeholder="playground" style={I}/>
      </div>
      <div>
        <div style={L}>User-Id</div>
        <input value={cfg.user_id} onChange={e=>patch({user_id:e.target.value})} placeholder="Knox ID" style={I}/>
      </div>
      <div>
        <div style={L}>User-Type</div>
        <input value={cfg.user_type} onChange={e=>patch({user_type:e.target.value})} placeholder="admin/user type" style={I}/>
      </div>
    </div>
    <div style={{marginTop:18,padding:12,background:"var(--bg-card)",borderRadius:6,border:"1px solid var(--border)"}}>
      <div style={{fontSize:12,fontWeight:700,marginBottom:6}}>요청 미리보기</div>
      <pre style={{fontSize:10,lineHeight:1.45,padding:10,background:"var(--bg-primary)",border:"1px solid var(--border)",borderRadius:4,overflow:"auto",maxHeight:260,fontFamily:"monospace",margin:0,color:"var(--text-primary)"}}>
{JSON.stringify(preview, null, 2)}
      </pre>
    </div>
    <div style={{marginTop:14}}>
      <div style={L}>테스트 프롬프트</div>
      <textarea value={testPrompt} onChange={e=>setTestPrompt(e.target.value)} rows={3} style={{...I,resize:"vertical"}}/>
    </div>
    <div style={{marginTop:14,display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
      <button onClick={save} disabled={busy} style={{padding:"8px 18px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:busy?"wait":"pointer"}}>{busy?"저장 중...":"저장"}</button>
      <button onClick={test} disabled={testBusy||!cfg.enabled||!cfg.api_url} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontWeight:600,cursor:testBusy?"wait":"pointer",opacity:(!cfg.enabled||!cfg.api_url)?0.45:1}}>{testBusy?"테스트 중...":"연결 테스트"}</button>
      <button onClick={reload} disabled={busy||testBusy} style={{padding:"8px 14px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>새로고침</button>
      {msg&&<span style={{fontSize:11,color:msg.startsWith("오류")||msg.includes("실패")?BAD.fg:OK.fg}}>{msg}</span>}
    </div>
  </div>);
}

// ── Data Roots Panel (v8.3.0 + backup v8.7.0) ──
function DataRootsPanel(){
  const[eff,setEff]=useState({db_root:"",sources:{}});
  const[form,setForm]=useState({db_root:""});
  const[splitRefresh,setSplitRefresh]=useState(30);
  const[etRefresh,setEtRefresh]=useState(30);
  const[cacheBusy,setCacheBusy]=useState("");
  const[cacheStatus,setCacheStatus]=useState({fab:[],et:[]});
  const[backup,setBackup]=useState({path:"",interval_hours:24,keep:5,enabled:true,last:{}});
  const[backupList,setBackupList]=useState([]);
  const[bkBusy,setBkBusy]=useState(false);
  const[msg,setMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const reload=()=>{
    sf("/api/admin/settings").then(d=>{
      const dr=d.data_roots||{db_root:"",sources:{}};
      setEff(dr);
      setSplitRefresh(Math.max(30,Math.min(60,Number(d.splittable_match_refresh_minutes)||30)));
      setEtRefresh(Math.max(30,Math.min(60,Number(d.tracker_et_match_refresh_minutes)||30)));
      if(d.backup)setBackup(prev=>({...prev,...d.backup}));
    }).catch(e=>setMsg("로드 오류: "+e.message));
    Promise.all([
      sf("/api/splittable/match-cache/status").catch(()=>({products:[]})),
      sf("/api/tracker/et-lot-cache/status").catch(()=>({products:[]})),
    ]).then(([fab,et])=>setCacheStatus({fab:fab.products||[],et:et.products||[]})).catch(()=>{});
    sf("/api/admin/backup/status").then(d=>{
      if(d.settings)setBackup(b=>({...b,...d.settings}));
      setBackupList(d.backups||[]);
    }).catch(()=>{});
  };
  useEffect(reload,[]);
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
          splittable_match_refresh_minutes: Number(splitRefresh)||30,
          tracker_et_match_refresh_minutes: Number(etRefresh)||30,
          data_roots: {db_root:form.db_root||""},
        })});
    }).then(()=>{setMsg("저장되었습니다. 새 요청부터 적용됩니다.");setForm({db_root:""});reload();})
      .catch(e=>setMsg("저장 오류: "+e.message))
      .finally(()=>setBusy(false));
  };
  const L={fontSize:12,fontWeight:600,marginBottom:4,color:"var(--text-primary)"};
  const I={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",
           background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",
           fontFamily:"monospace",boxSizing:"border-box"};
  const H={fontSize:10,color:"var(--text-secondary)",marginTop:4,fontFamily:"monospace"};
  const srcBadge=(s)=>{const map={env:INFO.fg,settings:OK.fg,default:SLATE};
    return<span style={{fontSize:9,padding:"1px 6px",borderRadius:3,background:(map[s]||SLATE)+"22",color:map[s]||SLATE,fontWeight:700,marginLeft:6}}>{s}</span>;};
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
  const runCacheRefresh=(kind)=>{
    const url=kind==="et"?"/api/tracker/et-lot-cache/refresh":"/api/splittable/match-cache/refresh";
    setCacheBusy(kind);setMsg("");
    sf(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({force:true})})
      .then(r=>{setMsg(`${kind==="et"?"ET":"FAB"} 캐시 스캔 완료: ${(r.products||[]).filter(x=>x.ok).length}/${(r.products||[]).length}`);reload();})
      .catch(e=>setMsg("캐시 스캔 오류: "+e.message))
      .finally(()=>setCacheBusy(""));
  };
  return(<div data-admin-panel="data_roots" style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:760}}>
    <div style={{fontSize:15,fontWeight:700,marginBottom:6}}>📂 데이터 루트 (소프트랜딩)</div>
    <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:16,lineHeight:1.5}}>
      flow 는 기본적으로 <b>DB 루트 하나</b>만 받습니다. 로컬 checkout 기본값은
      <span style={{fontFamily:"monospace"}}> data/Fab </span>,
      prod 앱 루트 또는 FLOW_PROD=1 에서는
      <span style={{fontFamily:"monospace"}}> /config/work/sharedworkspace/DB </span>
      입니다. 단일 파일(rulebook, ML_TABLE, features parquet)도 DB 루트 최상단에서 읽습니다.
      우선순위: <b>FLOW env → admin_settings.data_roots → default</b>.
      DB 루트는 이미 존재하는 디렉터리만 저장됩니다. 빈 값으로 저장하면 오버라이드가 제거되고 env/default 로 돌아갑니다.
    </div>
    {field("db_root","DB 루트","FLOW_DB_ROOT")}
    <div style={{marginTop:18,paddingTop:16,borderTop:"1px solid var(--border)"}}>
      <div style={{fontSize:15,fontWeight:700,marginBottom:6}}>🧩 SplitTable 매칭 캐시</div>
      <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.5}}>
        FAB DB 를 주기적으로 스캔해 root_lot_id ↔ fab_lot_id 연결 테이블을 미리 만듭니다.
        SplitTable 조회는 이 캐시를 먼저 사용하고, 캐시가 없을 때만 원천 DB 로 폴백합니다.
      </div>
      <div style={L}>갱신 주기 (분)</div>
      <input type="number" min={30} max={60} value={splitRefresh}
        onChange={e=>setSplitRefresh(Math.max(30,Math.min(60,Number(e.target.value)||30)))}
        style={{...I,maxWidth:140}}/>
      <div style={H}>30~60분 범위. 저장 후 백그라운드 스케줄러의 다음 tick부터 적용됩니다.</div>
      <div style={{display:"flex",gap:8,alignItems:"center",marginTop:10,flexWrap:"wrap"}}>
        <button onClick={()=>runCacheRefresh("fab")} disabled={cacheBusy==="fab"}
          style={{padding:"6px 12px",borderRadius:6,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:11,fontWeight:700,cursor:cacheBusy==="fab"?"wait":"pointer"}}>
          {cacheBusy==="fab"?"FAB 스캔 중...":"FAB 수동 스캔"}
        </button>
        <span style={{fontSize:10,color:"var(--text-secondary)"}}>캐시 {cacheStatus.fab?.length||0}개 제품</span>
      </div>
    </div>
    <div style={{marginTop:18,paddingTop:16,borderTop:"1px solid var(--border)"}}>
      <div style={{fontSize:15,fontWeight:700,marginBottom:6}}>🧪 Tracker Analysis ET 캐시</div>
      <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.5}}>
        ET DB 를 주기적으로 스캔해 root_lot_id / fab_lot_id / lot_id 후보를 미리 저장합니다.
        이슈추적 Analysis 의 lot 선택 목록은 이 캐시를 먼저 사용합니다.
      </div>
      <div style={L}>갱신 주기 (분)</div>
      <input type="number" min={30} max={60} value={etRefresh}
        onChange={e=>setEtRefresh(Math.max(30,Math.min(60,Number(e.target.value)||30)))}
        style={{...I,maxWidth:140}}/>
      <div style={H}>30~60분 범위. 저장 후 ET 캐시 스케줄러의 다음 tick부터 적용됩니다.</div>
      <div style={{display:"flex",gap:8,alignItems:"center",marginTop:10,flexWrap:"wrap"}}>
        <button onClick={()=>runCacheRefresh("et")} disabled={cacheBusy==="et"}
          style={{padding:"6px 12px",borderRadius:6,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:11,fontWeight:700,cursor:cacheBusy==="et"?"wait":"pointer"}}>
          {cacheBusy==="et"?"ET 스캔 중...":"ET 수동 스캔"}
        </button>
        <span style={{fontSize:10,color:"var(--text-secondary)"}}>캐시 {cacheStatus.et?.length||0}개 제품</span>
      </div>
    </div>
    <div style={{display:"flex",gap:8,marginTop:16,alignItems:"center"}}>
      <button data-dr-btn="save" onClick={save} disabled={busy}
        style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:busy?"default":"pointer",opacity:busy?0.5:1}}>
        {busy?"저장 중...":"저장"}
      </button>
      <button data-dr-btn="reload" onClick={reload} disabled={busy}
        style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>
        새로고침
      </button>
      {msg&&<span data-dr-msg style={{fontSize:11,color:(msg.includes("완료")||msg.includes("저장"))?OK.fg:BAD.fg}}>{msg}</span>}
    </div>

    {/* v8.7.0: 백업 설정 */}
    <div style={{marginTop:28,paddingTop:20,borderTop:"1px solid var(--border)"}}>
      <div style={{fontSize:15,fontWeight:700,marginBottom:6}}>💾 자동 백업</div>
      <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:12,lineHeight:1.5}}>
        data_root 전체와 DB 루트 최상단 설정 파일을 zip 스냅샷으로 백업합니다.
        서버 기동 시 1회 + 설정된 주기로 자동 실행. 보관개수 초과 시 오래된 백업부터 자동 삭제.
        경로를 비워두면 현재 <span style={{fontFamily:"monospace"}}>data_root/_backups</span> 를 자동 사용합니다.
      </div>
      <div style={{display:"grid",gridTemplateColumns:"2fr 1fr 1fr 1fr",gap:10,alignItems:"end"}}>
        <div>
          <div style={L}>백업 경로 (비워두면 data_root/_backups 자동)</div>
          <input value={backup.path||""} onChange={e=>setBackup({...backup,path:e.target.value})}
            placeholder="예: D:/flow_backups"
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
            <span style={{fontSize:11}}>스케줄러 on/off</span>
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
          <span style={{fontSize:10,color:"var(--text-secondary)",marginLeft:6}}>
            마지막: {(backup.last.at||"").replace("T"," ")} ·
            {backup.last.ok?<span style={{color:OK.fg}}> ok ({(backup.last.bytes||0).toLocaleString()}B)</span>
                           :<span style={{color:BAD.fg}}> 실패 {backup.last.error}</span>}
          </span>
        )}
      </div>
      {backupList.length>0&&(
        <div style={{marginTop:14,maxHeight:220,overflow:"auto",border:"1px solid var(--border)",borderRadius:6}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:11,fontFamily:"monospace"}}>
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
                      style={{padding:"3px 8px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:WARN.fg,cursor:bkBusy?"default":"pointer",fontSize:10}}>롤백</button>
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
  const S={padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,outline:"none"};
  return(<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:620}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
      <span style={{fontSize:14,fontWeight:700}}>트래커 카테고리</span>
      {msg&&<span style={{fontSize:11,color:msg.startsWith("오류")?BAD.fg:OK.fg,fontFamily:"monospace"}}>{msg}</span>}
    </div>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:10,marginBottom:12,flexWrap:"wrap"}}>
      <div style={{fontSize:10,color:"var(--text-secondary)",lineHeight:1.5}}>
        LOT_WF 확장 필드가 누락된 기존 tracker/issues.json 은 여기서 다시 마이그레이션할 수 있습니다.
      </div>
      <button onClick={rerunTrackerSchema} disabled={migrateBusy} style={{padding:"8px 14px",borderRadius:6,border:"1px solid var(--border)",background:migrateBusy?"var(--bg-tertiary)":"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,fontWeight:700,cursor:migrateBusy?"default":"pointer",opacity:migrateBusy?0.7:1}}>
        {migrateBusy?"재마이그레이션 중...":"트래커 스키마 재마이그레이션"}
      </button>
    </div>
    <div style={{display:"flex",gap:8,marginBottom:14,alignItems:"center"}}>
      <input type="color" value={newColor} onChange={e=>setNewColor(e.target.value)} style={{width:40,height:36,padding:0,border:"1px solid var(--border)",borderRadius:6,cursor:"pointer",background:"transparent"}} title="카테고리 색상"/>
      <input value={newCat} onChange={e=>setNewCat(e.target.value)} placeholder="새 카테고리 이름" onKeyDown={e=>e.key==="Enter"&&add()} style={{...S,flex:1}}/>
      <button onClick={add} disabled={!newCat.trim()} style={{padding:"8px 16px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:"pointer",opacity:newCat.trim()?1:0.5}}>+ 추가</button>
    </div>
    <div style={{border:"1px solid var(--border)",borderRadius:8,overflow:"hidden"}}>
      {cats.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:12}}>카테고리 없음</div>}
      {cats.map((c,i)=>{const n=usage.counts?.[c.name]||0;return(<div key={i} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 12px",borderBottom:i<cats.length-1?"1px solid var(--border)":"none",background:editIdx===i?"var(--accent-glow)":"transparent"}}>
        <span style={{fontSize:10,color:"var(--text-secondary)",minWidth:22,fontFamily:"monospace"}}>{(i+1).toString().padStart(2,"0")}</span>
        <input type="color" value={c.color||"#64748b"} onChange={e=>setColor(i,e.target.value)} style={{width:26,height:26,padding:0,border:"1px solid var(--border)",borderRadius:4,cursor:"pointer",background:"transparent",flexShrink:0}} title="클릭하여 색상 선택"/>
        {editIdx===i
          ?<input autoFocus value={editVal} onChange={e=>setEditVal(e.target.value)} onKeyDown={e=>e.key==="Enter"&&saveEdit()} onBlur={saveEdit} style={{...S,flex:1,padding:"4px 8px",fontSize:12}}/>
          :<span style={{flex:1,fontSize:13,cursor:"pointer",display:"flex",alignItems:"center",gap:6}} onClick={()=>startEdit(i)}><span style={{width:8,height:8,borderRadius:"50%",background:c.color||"#64748b",flexShrink:0}}/>{c.name}</span>}
        <span style={{fontSize:10,color:n>0?"var(--accent)":"var(--text-secondary)",fontFamily:"monospace",padding:"1px 6px",borderRadius:10,background:n>0?"var(--accent-glow)":"transparent",minWidth:28,textAlign:"center"}}>{n}</span>
        <span onClick={()=>move(i,-1)} style={{cursor:i===0?"not-allowed":"pointer",opacity:i===0?0.3:0.8,fontSize:11,color:"var(--text-secondary)",padding:"2px 4px"}}>▲</span>
        <span onClick={()=>move(i,1)} style={{cursor:i===cats.length-1?"not-allowed":"pointer",opacity:i===cats.length-1?0.3:0.8,fontSize:11,color:"var(--text-secondary)",padding:"2px 4px"}}>▼</span>
        <span onClick={()=>startEdit(i)} style={{cursor:"pointer",fontSize:11,color:INFO.fg,padding:"2px 6px"}}>편집</span>
        <span onClick={()=>{if(n>0&&!confirm(`"${c.name}" 은(는) ${n}개 이슈에서 사용 중입니다. 그래도 삭제하시겠습니까? 기존 이슈는 고아(orphan) 상태가 됩니다.`))return;del(i);}} style={{cursor:"pointer",fontSize:11,color:BAD.fg,padding:"2px 6px"}}>삭제</span>
      </div>);})}
      {Object.keys(usage.orphans||{}).length>0&&<div style={{padding:"10px 12px",background:"rgba(239,68,68,0.08)",borderTop:"1px solid var(--border)"}}>
        <div style={{fontSize:10,fontWeight:700,color:BAD.fg,marginBottom:4}}>⚠ 고아 카테고리 (이슈에서 사용 중이나 목록에 없음)</div>
        {Object.entries(usage.orphans).map(([oc,n])=>(<div key={oc} style={{display:"flex",justifyContent:"space-between",fontSize:11,fontFamily:"monospace",marginBottom:2}}>
          <span>{oc}</span>
          <span style={{color:"var(--text-secondary)"}}>{n}개 이슈 — <span onClick={()=>{if(confirm(`"${oc}" 을(를) 카테고리 목록에 복원하시겠습니까?`))save([...cats,{name:oc,color:"#64748b"}]);}} style={{cursor:"pointer",color:INFO.fg}}>복원</span></span>
        </div>))}
      </div>}
    </div>
    <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:10,lineHeight:1.5}}>색상 원 클릭으로 카테고리 색 변경. 이 색상은 트래커 이슈 리스트 prefix, Gantt bar, 상세 뷰에 반영됩니다.</div>
  </div>);
}

function CatalogPanel(){
  const[sub,setSub]=useState("matching");
  const tS=(a)=>({padding:"6px 14px",fontSize:11,fontFamily:"monospace",cursor:"pointer",fontWeight:a?700:400,borderBottom:a?"2px solid var(--accent)":"2px solid transparent",color:a?"var(--accent)":"var(--text-secondary)"});
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
  return(<span style={{display:"inline-flex",alignItems:"center",gap:5,padding:"1px 7px",borderRadius:10,background:bg+"22",border:"1px solid "+bg,fontSize:10,fontFamily:"monospace",color:bg,fontWeight:700}}>
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
  const download=(name)=>{dl("/api/catalog/matching/download?name="+encodeURIComponent(name), `${name}.csv`).catch(e=>alert("다운로드 실패: "+e.message));};
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
      <div style={{fontSize:11,fontWeight:700,color:"var(--accent)",marginBottom:8,fontFamily:"monospace"}}>등록된 테이블 ({tables.length})</div>
      {tables.map(t=>(<div key={t.name} onClick={()=>loadPreview(t.name)} style={{padding:"8px 10px",borderRadius:6,cursor:"pointer",marginBottom:4,background:sel===t.name?"var(--accent-glow)":"var(--bg-primary)",border:"1px solid "+(sel===t.name?"var(--accent)":"var(--border)")}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <span style={{fontSize:12,fontWeight:700,fontFamily:"monospace",color:t.exists?"var(--text-primary)":SILVER}}>{t.name}</span>
          <span style={{fontSize:9,padding:"1px 6px",borderRadius:3,background:t.exists?OK.bg:BAD.bg,color:t.exists?OK.fg:BAD.fg,fontWeight:700}}>{t.exists?t.rows+"행":"없음"}</span>
        </div>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:2}}>{t.description}</div>
        <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:2,fontFamily:"monospace"}}>적용: {(t.applies_to||[]).join(", ")}</div>
        {t.missing_cols?.length>0&&<div style={{fontSize:9,color:BAD.fg,marginTop:2}}>⚠ 누락 컬럼: {t.missing_cols.join(", ")}</div>}
      </div>))}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:16,minHeight:300}}>
      {!sel&&<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}>미리보기를 위해 좌측에서 매칭 테이블을 선택하세요</div>}
      {sel&&preview&&(<>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:13,fontWeight:700,fontFamily:"monospace"}}>{sel}</span>
          <div style={{display:"flex",gap:6,alignItems:"center"}}>
            {saveMsg&&<span style={{fontSize:10,fontFamily:"monospace",color:saveMsg.startsWith("⚠")?BAD.fg:OK.fg}}>{saveMsg}</span>}
            {hasAreaCol&&Object.keys(edits).length>0&&<button onClick={saveAreas} style={{padding:"4px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:WHITE,fontSize:10,fontWeight:700,cursor:"pointer"}} title="영역 편집 저장">💾 저장 ({Object.keys(edits).length})</button>}
            <button onClick={()=>download(sel)} style={{padding:"4px 10px",borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:10,cursor:"pointer"}}>⬇ CSV</button>
          </div>
        </div>
        {sel==="matching_step"&&rollup&&rollup.total>0&&(
          <div style={{display:"flex",flexWrap:"wrap",gap:4,marginBottom:10,padding:"6px 8px",background:"var(--bg-primary)",borderRadius:6,border:"1px solid var(--border)"}} title="Process-area rollup (/api/match/area-rollup)">
            <span style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace",marginRight:4}}>🧩 area-rollup:</span>
            {rollup.rollup.map(b=>(
              <span key={b.area} style={{display:"inline-flex",alignItems:"center",gap:4,padding:"1px 7px",borderRadius:10,fontSize:10,fontFamily:"monospace",background:(b.area==="(unmatched)"?"#4b5563":areaColor(b.area))+"22",color:b.area==="(unmatched)"?"#94a3b8":areaColor(b.area),border:"1px solid "+(b.area==="(unmatched)"?"#4b5563":areaColor(b.area))}}>
                {b.area} · {b.count}
              </span>
            ))}
            <span style={{fontSize:9,color:"var(--text-secondary)",marginLeft:"auto"}}>{rollup.matched}/{rollup.total} 태그됨</span>
          </div>
        )}
        {preview.rows.length===0?<div style={{color:"var(--text-secondary)",fontSize:12}}>데이터 없음. CSV를 먼저 업로드/시드하세요.</div>:(
          <div style={{overflow:"auto",maxHeight:480}}>
            <table style={{width:"100%",fontSize:11,borderCollapse:"collapse",fontFamily:"monospace"}}>
              <thead><tr style={{position:"sticky",top:0,background:"var(--bg-tertiary)"}}>
                {/* v8.2.1: ensure `area` column is shown even if csv predates the schema */}
                {(hasAreaCol&&!preview.columns.includes("area")?[...preview.columns,"area"]:preview.columns).map(c=>(
                  <th key={c} style={{textAlign:"left",padding:"4px 8px",color:c==="area"?"var(--accent)":"var(--text-secondary)",fontSize:10,borderBottom:"1px solid var(--border)"}}>{c}</th>
                ))}
              </tr></thead>
              <tbody>{preview.rows.map((r,i)=>(<tr key={i} style={{borderBottom:"1px solid rgba(255,255,255,0.04)"}}>
                {(hasAreaCol&&!preview.columns.includes("area")?[...preview.columns,"area"]:preview.columns).map(c=>{
                  if(c==="area"&&sel==="matching_step"){
                    const v=edits[i]!==undefined?edits[i]:r.area;
                    return(<td key={c} style={{padding:"3px 8px"}}>
                      <div style={{display:"flex",gap:6,alignItems:"center"}}>
                        <AreaChip value={v}/>
                        <select value={v||""} onChange={e=>setAreaEdit(i,e.target.value)} style={{fontSize:10,fontFamily:"monospace",background:"var(--bg-primary)",color:"var(--text-primary)",border:"1px solid var(--border)",borderRadius:3,padding:"1px 4px"}}>
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
            {preview.total>preview.rows.length&&<div style={{fontSize:10,color:"var(--text-secondary)",marginTop:6}}>{preview.rows.length} / {preview.total} 행 표시</div>}
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
      <div style={{fontSize:11,fontWeight:700,color:"var(--accent)",marginBottom:8,fontFamily:"monospace"}}>Product ({list.length})</div>
      {list.map(p=>(<div key={p.product} onClick={()=>pick(p.product)} style={{padding:"8px 10px",borderRadius:6,cursor:"pointer",marginBottom:4,background:sel===p.product?"var(--accent-glow)":"var(--bg-primary)",border:"1px solid "+(sel===p.product?"var(--accent)":"var(--border)")}}>
        <div style={{fontSize:12,fontWeight:700,fontFamily:"monospace"}}>{p.product}</div>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:2}}>proc_id: {p.process_id||"-"} · owner: {p.owner||"-"}</div>
        <div style={{fontSize:9,color:"var(--text-secondary)"}}>KNOB: {p.knob_count} · ET 항목: {p.et_key_count} · spec: {p.has_spec?"✓":"-"}</div>
      </div>))}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:16,minHeight:300}}>
      {!sel&&<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}>설정을 보거나 편집할 Product를 선택하세요 (YAML로 저장되며, 편집 시 JSON으로 표시)</div>}
      {sel&&cfg&&(<>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:13,fontWeight:700,fontFamily:"monospace"}}>{sel}.yaml</span>
          <div style={{display:"flex",gap:8,alignItems:"center"}}>
            {msg&&<span style={{fontSize:11,fontFamily:"monospace",color:msg.startsWith("⚠")?"#ef4444":"#10b981"}}>{msg}</span>}
            <button onClick={save} style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,fontWeight:600,cursor:"pointer"}}>저장</button>
          </div>
        </div>
        <textarea value={raw} onChange={e=>setRaw(e.target.value)} spellCheck={false}
          style={{width:"100%",minHeight:440,fontFamily:"monospace",fontSize:11,padding:12,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",resize:"vertical",outline:"none"}}/>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:6,lineHeight:1.5}}>
          JSON으로 편집; YAML로 저장됨. 키: product, process_id, owner, canonical_knobs[], canonical_inline_items[], et_key_items[], yld_metric, perf_metric, target_spec{`{item: [lsl, usl, target]}`}, measured_shots[[x,y],...]
        </div>
      </>)}
    </div>
  </div>);
}

function S3Panel(){
  const[cfg,setCfg]=useState({bucket:"",prefix:"flow/artifacts/",region:"ap-northeast-2",enabled:false,profile:""});
  const[boto,setBoto]=useState(false);const[arts,setArts]=useState([]);const[events,setEvents]=useState([]);const[msg,setMsg]=useState("");
  const load=()=>{
    sf("/api/catalog/s3/config").then(d=>{setCfg(d.config||cfg);setBoto(d.boto3_installed);});
    sf("/api/catalog/s3/artifacts").then(d=>setArts(d.artifacts||[]));
    sf("/api/catalog/s3/status?limit=30").then(d=>setEvents(d.events||[]));
  };
  useEffect(load,[]);
  // v8.2.0: Bell dismiss / external read → re-load this tab's notif list immediately
  useEffect(()=>{
    const onRefresh=()=>load();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>window.removeEventListener("hol:notif-refresh",onRefresh);
  },[]);
  const saveCfg=()=>sf("/api/catalog/s3/config/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:cfg})}).then(()=>{setMsg("설정 저장됨");setTimeout(()=>setMsg(""),2000);});
  const syncAll=(t)=>{setMsg("동기화 중...");sf("/api/catalog/s3/sync"+(t?"?filter_type="+t:""),{method:"POST"}).then(d=>{setMsg(d.count+"개 아티팩트 동기화 완료");setTimeout(()=>setMsg(""),3000);load();});};
  const S={padding:"6px 10px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,outline:"none",fontFamily:"monospace"};
  const byType={};arts.forEach(a=>{(byType[a.type]=byType[a.type]||[]).push(a);});
  return(<div>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:14,marginBottom:12}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
        <div style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>☁ S3 동기화 설정</div>
        <span style={{fontSize:10,padding:"2px 8px",borderRadius:10,background:boto?OK.bg:BAD.bg,color:boto?OK.fg:BAD.fg,fontWeight:700}}>{boto?"boto3 설치됨":"boto3 없음 (로그만 기록)"}</span>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr",gap:8,marginBottom:8}}>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>Bucket</div><input value={cfg.bucket} onChange={e=>setCfg({...cfg,bucket:e.target.value})} style={{...S,width:"100%"}} placeholder="my-bucket"/></div>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>Prefix</div><input value={cfg.prefix} onChange={e=>setCfg({...cfg,prefix:e.target.value})} style={{...S,width:"100%"}}/></div>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>리전</div><input value={cfg.region} onChange={e=>setCfg({...cfg,region:e.target.value})} style={{...S,width:"100%"}}/></div>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>프로파일 (선택)</div><input value={cfg.profile} onChange={e=>setCfg({...cfg,profile:e.target.value})} style={{...S,width:"100%"}}/></div>
      </div>
      <div style={{display:"flex",gap:12,alignItems:"center"}}>
        <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,fontFamily:"monospace"}}><input type="checkbox" checked={cfg.enabled} onChange={e=>setCfg({...cfg,enabled:e.target.checked})} style={{accentColor:"var(--accent)"}}/>활성화</label>
        <button onClick={saveCfg} style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:WHITE,fontSize:11,fontWeight:600,cursor:"pointer"}}>설정 저장</button>
        <button onClick={()=>syncAll("")} style={{padding:"5px 14px",borderRadius:4,border:`1px solid ${OK.fg}`,background:OK.bg,color:OK.fg,fontSize:11,fontWeight:600,cursor:"pointer"}}>▶ 전체 동기화</button>
        {msg&&<span style={{fontSize:11,color:"var(--accent)",fontFamily:"monospace"}}>{msg}</span>}
      </div>
    </div>
    {Object.entries(byType).map(([t,items])=>(<div key={t} style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:12,marginBottom:10}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
        <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{t} ({items.length})</span>
        <button onClick={()=>syncAll(t)} style={{padding:"3px 10px",borderRadius:3,border:"1px solid var(--border)",background:"transparent",color:"var(--accent)",fontSize:10,cursor:"pointer"}}>{t} 동기화</button>
      </div>
      <table style={{width:"100%",fontSize:10,borderCollapse:"collapse",fontFamily:"monospace"}}>
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
      <div style={{fontSize:11,fontWeight:700,color:"var(--accent)",marginBottom:8,fontFamily:"monospace"}}>최근 이벤트 ({events.length})</div>
      <div style={{maxHeight:200,overflow:"auto",fontSize:10,fontFamily:"monospace"}}>
        {[...events].reverse().map((e,i)=>(<div key={i} style={{padding:"2px 0",borderBottom:"1px solid rgba(255,255,255,0.04)",color:"var(--text-secondary)"}}>
          <span style={{color:"var(--accent)"}}>{e.ts?.slice(11,19)}</span> <span style={{color:e.status==="uploaded"?OK.fg:e.status==="error"?BAD.fg:WARN.fg}}>{e.status}</span> {e.s3_key||e.key} {e.error?"— "+e.error:""}
        </div>))}
      </div>
    </div>)}
  </div>);
}

function AdminMessagesPanel({user}){
  const[sub,setSub]=useState("inbox");
  const tS=(a)=>({padding:"7px 14px",fontSize:11,cursor:"pointer",fontWeight:a?700:500,borderRadius:5,background:a?"var(--accent-glow)":"transparent",color:a?"var(--accent)":"var(--text-secondary)",fontFamily:"'JetBrains Mono',monospace"});
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
  const sendReply=()=>{const v=(reply||"").trim();if(!v||!sel||sending)return;if(v.length>5000){alert("최대 5000자");return;}setSending(true);
    sf("/api/messages/admin/reply",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,to_user:sel,text:v})})
      .then(()=>{setReply("");loadThread(sel);loadThreads();})
      .catch(e=>alert("실패: "+e.message))
      .finally(()=>setSending(false));};
  const totalUnread=threads.reduce((s,t)=>s+(t.unread_for_admin||0),0);
  return(<div style={{display:"flex",gap:12,height:"calc(100vh - 48px - 80px - 20px)"}}>
    <div style={{width:280,background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",display:"flex",flexDirection:"column",flexShrink:0}}>
      <div style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center"}}>
        <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{"> 스레드"}</span>
        <span style={{fontSize:10,color:"var(--text-secondary)",marginLeft:8}}>{threads.length} · 읽지 않음 {totalUnread}</span>
        <div style={{flex:1}}/>
        <span onClick={loadThreads} style={{fontSize:11,color:"var(--text-secondary)",cursor:"pointer"}} title="새로고침">↻</span>
      </div>
      <div style={{flex:1,overflowY:"auto"}}>
        {threads.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:11}}>수신된 메시지가 없습니다.</div>}
        {threads.map(t=>(
          <div key={t.user} onClick={()=>openThread(t.user)} style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",cursor:"pointer",background:sel===t.user?"var(--accent-glow)":(t.unread_for_admin>0?"rgba(249,115,22,0.05)":"transparent")}}>
            <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:3}}>
              {t.unread_for_admin>0&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--accent)",flexShrink:0}}/>}
              <span style={{fontSize:12,fontWeight:t.unread_for_admin>0?700:500,color:"var(--text-primary)",fontFamily:"monospace",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.user}</span>
              {t.unread_for_admin>0&&<span style={{fontSize:9,fontWeight:700,padding:"1px 5px",borderRadius:3,background:"var(--accent)",color:WHITE}}>{t.unread_for_admin}</span>}
            </div>
            <div style={{fontSize:10,color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",lineHeight:1.4}}>{t.last_from?`[${t.last_from}] `:""}{t.last_preview||"(비어 있음)"}</div>
            <div style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:2}}>{(t.last_at||"").replace("T"," ").slice(0,16)}</div>
          </div>))}
      </div>
    </div>
    <div style={{flex:1,background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",display:"flex",flexDirection:"column",minWidth:0}}>
      {!sel&&<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-secondary)",fontSize:13}}>← 좌측에서 사용자를 선택하세요</div>}
      {sel&&thr&&<>
        <div style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:13,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>💬 {sel}</span>
          <span style={{fontSize:10,color:"var(--text-secondary)"}}>{(thr.messages||[]).length} 메시지</span>
          <div style={{flex:1}}/>
          <span onClick={()=>loadThread(sel)} style={{fontSize:11,color:"var(--text-secondary)",cursor:"pointer"}} title="새로고침">↻</span>
        </div>
        <div ref={listRef} style={{flex:1,overflowY:"auto",padding:14,background:"var(--bg-primary)"}}>
          {(thr.messages||[]).length===0&&<div style={{textAlign:"center",color:"var(--text-secondary)",fontSize:12,padding:30}}>메시지 없음</div>}
          {(thr.messages||[]).map(m=>{const mine=m.from===admin;return(
            <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:10}}>
              <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
                <div style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?`나 (${m.from})`:m.from} · {(m.created_at||"").replace("T"," ").slice(0,16)}</div>
                <div style={{padding:"8px 12px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?WHITE:"var(--text-primary)",fontSize:13,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
              </div>
            </div>);})}
        </div>
        <div style={{padding:"10px 14px",borderTop:"1px solid var(--border)"}}>
          <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
            <textarea value={reply} onChange={e=>setReply(e.target.value)} disabled={sending} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")sendReply();}} placeholder={`${sel} 에게 답장 (Cmd/Ctrl+Enter 전송)`} rows={2} style={{flex:1,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
            <button onClick={sendReply} disabled={sending||!reply.trim()} style={{padding:"8px 18px",borderRadius:6,border:"none",background:sending||!reply.trim()?SILVER:"var(--accent)",color:WHITE,fontSize:12,fontWeight:700,cursor:sending||!reply.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>{sending?"…":"답장"}</button>
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
      .catch(e=>alert("실패: "+e.message)).finally(()=>setSending(false));};
  const del=(id)=>{if(!confirm("공지사항을 삭제하시겠습니까?"))return;
    sf("/api/messages/admin/notice_delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin,id})}).then(load).catch(e=>alert(e.message));};
  const S={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,outline:"none",fontFamily:"'Pretendard',sans-serif",boxSizing:"border-box"};
  return(<div>
    <div style={{display:"flex",alignItems:"center",marginBottom:12}}>
      <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{"> 공지사항"}</span>
      <span style={{fontSize:11,color:"var(--text-secondary)",marginLeft:8}}>{notices.length} 개</span>
      <div style={{flex:1}}/>
      <button onClick={()=>setShowNew(!showNew)} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--accent)",background:showNew?"var(--accent)":"transparent",color:showNew?WHITE:"var(--accent)",fontSize:11,fontWeight:700,cursor:"pointer"}}>{showNew?"취소":"+ 새 공지사항"}</button>
    </div>
    {showNew&&<div style={{background:"var(--bg-secondary)",border:"1px solid var(--accent)",borderRadius:8,padding:16,marginBottom:14}}>
      <input value={title} onChange={e=>setTitle(e.target.value)} placeholder="제목 (최대 200자)" maxLength={200} style={{...S,marginBottom:8,fontWeight:600}}/>
      <textarea value={body} onChange={e=>setBody(e.target.value)} placeholder="공지 본문 (최대 5000자)" rows={5} style={{...S,marginBottom:8,resize:"vertical"}}/>
      <div style={{display:"flex",alignItems:"center"}}>
        <span style={{fontSize:10,color:"var(--text-secondary)"}}>{title.length}/200 · {body.length}/5000</span>
        <div style={{flex:1}}/>
        <button onClick={create} disabled={sending||(!title.trim()&&!body.trim())} style={{padding:"7px 18px",borderRadius:5,border:"none",background:sending||(!title.trim()&&!body.trim())?SILVER:"var(--accent)",color:WHITE,fontSize:12,fontWeight:700,cursor:sending?"default":"pointer"}}>{sending?"…":"발행"}</button>
      </div>
    </div>}
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden"}}>
      {notices.length===0&&<div style={{padding:30,textAlign:"center",color:"var(--text-secondary)",fontSize:12}}>등록된 공지사항이 없습니다.</div>}
      {notices.map(n=>(
        <div key={n.id} style={{padding:"12px 16px",borderBottom:"1px solid var(--border)"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
            <span style={{fontSize:13,fontWeight:700,color:"var(--text-primary)",flex:1}}>{n.title||"(제목 없음)"}</span>
            <span style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace"}}>{(n.created_at||"").replace("T"," ").slice(0,16)}</span>
            <span style={{fontSize:10,color:"var(--accent)",fontFamily:"monospace"}}>👁 {n.read_count||0}/{n.total_recipients||"?"}</span>
            <span onClick={()=>del(n.id)} style={{cursor:"pointer",color:"#ef4444",fontSize:11}}>🗑</span>
          </div>
          {n.body&&<div style={{fontSize:11,color:"var(--text-secondary)",lineHeight:1.5,whiteSpace:"pre-wrap",paddingLeft:2}}>{n.body}</div>}
          <div style={{fontSize:9,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:4}}>by {n.author}</div>
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

  const S={padding:"7px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",fontFamily:"monospace"};
  const labelS={fontSize:11,color:"var(--text-secondary)",marginBottom:4};

  if(!data)return<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}><Loading text="로딩 중..."/></div>;

  return(
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:700}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
        <div>
          <span style={{fontSize:14,fontWeight:700,color:"var(--accent)"}}>AWS 설정</span>
          <span style={{fontSize:10,color:"var(--text-secondary)",marginLeft:10,fontFamily:"monospace"}}>{data.credentials_path}</span>
        </div>
        {msg&&<span style={{fontSize:11,color:msg.startsWith("오류")?"#ef4444":"#22c55e",fontFamily:"monospace"}}>{msg}</span>}
      </div>

      {!data.aws_available&&<div style={{padding:"8px 12px",borderRadius:6,background:"rgba(251,191,36,0.1)",border:"1px solid rgba(251,191,36,0.3)",marginBottom:12,fontSize:11,color:"#fbbf24"}}>⚠ aws CLI 미설치 — sync 실행은 불가. 자격증명은 저장 가능.</div>}

      {/* Profile selector */}
      <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:16,flexWrap:"wrap"}}>
        <span style={{fontSize:11,color:"var(--text-secondary)"}}>프로파일:</span>
        {(Array.isArray(data.profiles)?data.profiles:[]).map((p,i)=>(
          <span key={p.profile+"_"+i} onClick={()=>setSelIdx(i)} style={{padding:"5px 12px",borderRadius:5,fontSize:11,cursor:"pointer",fontWeight:selIdx===i?700:500,background:selIdx===i?"var(--accent-glow)":"var(--bg-primary)",color:selIdx===i?"var(--accent)":"var(--text-secondary)",border:"1px solid "+(selIdx===i?"var(--accent)":"var(--border)"),fontFamily:"monospace"}}>{p.profile}</span>
        ))}
        <span style={{color:"var(--border)"}}>|</span>
        <input value={newProfile} onChange={e=>setNewProfile(e.target.value)} onKeyDown={e=>e.key==="Enter"&&addProfile()} placeholder="새 프로파일 이름" style={{...S,width:160,fontSize:11,padding:"5px 8px"}}/>
        <button onClick={addProfile} style={{padding:"5px 12px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:11,cursor:"pointer"}}>+ 추가</button>
      </div>

      {/* Form */}
      {form&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"12px 14px"}}>
        <div style={{gridColumn:"1 / 3"}}>
          <div style={labelS}>Access Key ID</div>
          <input value={form.aws_access_key_id} onChange={e=>setForm(f=>({...f,aws_access_key_id:e.target.value}))} placeholder="AKIA... (16-32 uppercase/digits)" style={{...S,width:"100%"}}/>
        </div>
        <div style={{gridColumn:"1 / 3"}}>
          <div style={labelS}>Secret Access Key {form.profile!=="default"||secretEdit?"":<span style={{color:"var(--text-secondary)",fontSize:10}}> (마스킹됨 — 변경하려면 편집 클릭)</span>}</div>
          <div style={{display:"flex",gap:6}}>
            <input value={form.aws_secret_access_key} disabled={!secretEdit} onChange={e=>setForm(f=>({...f,aws_secret_access_key:e.target.value}))} placeholder={secretEdit?"40자 secret":""} style={{...S,flex:1,opacity:secretEdit?1:0.7}} type={secretEdit?"text":"password"}/>
            {!secretEdit?<button onClick={()=>{setSecretEdit(true);setForm(f=>({...f,aws_secret_access_key:""}));}} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>편집</button>
            :<button onClick={()=>{setSecretEdit(false);load();}} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>취소</button>}
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
          <div style={labelS}>Endpoint URL (선택, ~/.aws/config 에 저장됨)</div>
          <input value={form.endpoint_url} onChange={e=>setForm(f=>({...f,endpoint_url:e.target.value}))} placeholder="https://s3.internal.company:9000" style={{...S,width:"100%"}}/>
        </div>
      </div>}

      <div style={{display:"flex",gap:8,marginTop:18}}>
        <button onClick={save} style={{padding:"9px 22px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontWeight:700,fontSize:12,cursor:"pointer"}}>저장</button>
        {form&&form.profile!=="default"&&<button onClick={delProfile} style={{padding:"9px 16px",borderRadius:5,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",fontSize:12,cursor:"pointer"}}>프로파일 삭제</button>}
        <div style={{flex:1}}/>
        <button onClick={load} style={{padding:"9px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:12,cursor:"pointer"}}>↻ 새로고침</button>
      </div>

      <div style={{marginTop:18,padding:12,background:"var(--bg-primary)",borderRadius:6,fontSize:10,color:"var(--text-secondary)",lineHeight:1.6,fontFamily:"monospace"}}>
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
    if(!s||!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s)){alert("이메일 형식이 올바르지 않습니다.");return;}
    const next=Array.from(new Set([...(current?.extra_emails||[]),s]));
    onSave(next);
    setV("");
  };
  return (<div style={{display:"flex",gap:6}}>
    <input value={v} onChange={e=>setV(e.target.value)} placeholder="외부 이메일 추가 (e.g. vendor@company.co.kr)"
      onKeyDown={e=>{if(e.key==="Enter")submit();}}
      style={{flex:1,padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,fontFamily:"monospace"}}/>
    <button onClick={submit} style={{padding:"6px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,cursor:"pointer"}}>추가</button>
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
        <div style={{fontSize:13,fontWeight:600,marginBottom:10}}>그룹 목록 ({groups.length})</div>
        <div style={{display:"flex",flexDirection:"column",gap:4,marginBottom:10}}>
          <input value={newName} onChange={e=>setNewName(e.target.value)} placeholder="새 그룹 이름"
            onKeyDown={e=>{if(e.key==="Enter")create();}}
            style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12}}/>
          <input value={newDesc} onChange={e=>setNewDesc(e.target.value)} placeholder="설명 (선택)"
            onKeyDown={e=>{if(e.key==="Enter")create();}}
            style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11}}/>
          <button onClick={create} style={{padding:"6px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:12,cursor:"pointer"}}>생성</button>
        </div>
        <div style={{display:"flex",flexDirection:"column",gap:4,maxHeight:400,overflow:"auto"}}>
          {groups.map(g=>(
            <div key={g.id} onClick={()=>setSel(g.id)}
              style={{padding:"8px 10px",borderRadius:6,cursor:"pointer",
                background:sel===g.id?"var(--bg-tertiary)":"transparent",
                border:"1px solid "+(sel===g.id?"var(--accent)":"transparent")}}>
              <div style={{fontSize:12,fontWeight:600}}>{g.name}</div>
              {g.description&&<div style={{fontSize:10,color:"var(--text-secondary)",marginTop:1,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{g.description}</div>}
              <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:1}}>
                owner: {g.owner} · members: {(g.members||[]).length} · modules: {(g.modules||[]).length}
              </div>
            </div>
          ))}
          {groups.length===0&&<div style={{fontSize:11,color:"var(--text-secondary)",padding:"20px 0",textAlign:"center"}}>그룹 없음</div>}
        </div>
        {msg&&<div style={{marginTop:10,fontSize:11,color:"var(--accent)"}}>{msg}</div>}
      </div>

      {/* Detail */}
      <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
        {!cur&&<div style={{fontSize:12,color:"var(--text-secondary)"}}>좌측에서 그룹을 선택하세요.</div>}
        {cur&&<>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}>
            <div style={{fontSize:16,fontWeight:700}}>{cur.name}</div>
            <div style={{flex:1,fontSize:10,color:"var(--text-secondary)"}}>owner: {cur.owner} · id: {cur.id}</div>
            {canEdit&&<button onClick={()=>del(cur.id)} style={{padding:"5px 10px",borderRadius:4,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",fontSize:11,cursor:"pointer"}}>그룹 삭제</button>}
          </div>

          {/* 설명 */}
          <div style={{marginBottom:14}}>
            <div style={{fontSize:12,fontWeight:600,marginBottom:4}}>설명</div>
            {canEdit
              ?<div style={{display:"flex",gap:6,alignItems:"flex-start"}}>
                <textarea value={editDesc} onChange={e=>setEditDesc(e.target.value)} rows={2}
                  placeholder="이 그룹의 목적을 간단히 설명하세요."
                  style={{flex:1,padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,resize:"vertical"}}/>
                <button onClick={()=>saveDesc(cur.id,editDesc)}
                  style={{padding:"6px 12px",borderRadius:4,border:"none",background:editDescSaved?"#10b981":"var(--accent)",color:"#fff",fontSize:11,cursor:"pointer",whiteSpace:"nowrap"}}>
                  {editDescSaved?"저장됨":"저장"}
                </button>
              </div>
              :<div style={{fontSize:11,color:cur.description?"var(--text-primary)":"var(--text-secondary)",fontStyle:cur.description?"normal":"italic",padding:"4px 0"}}>
                {cur.description||"설명 없음"}
              </div>
            }
          </div>

          <div style={{fontSize:12,fontWeight:600,marginBottom:6}}>멤버 ({(cur.members||[]).length})</div>
          {/* v8.8.27: 멤버 chip 에 이름(있으면) + id 표시. 동명이인이어도 id 가 항상 붙음. */}
          <div style={{display:"flex",flexWrap:"wrap",gap:6,marginBottom:10}}>
            {(cur.members||[]).map(m=>{
              const u=userIndex[m]||{username:m};
              return(
                <span key={m} title={m} style={{padding:"3px 10px",borderRadius:999,background:"var(--bg-tertiary)",fontSize:11,display:"inline-flex",alignItems:"center",gap:6}}>
                  {userLabel(u)}
                  {canEdit&&<button onClick={()=>rmMember(cur.id,m)} style={{border:"none",background:"transparent",color:"#ef4444",cursor:"pointer",fontSize:11,padding:0}}>×</button>}
                </span>
              );
            })}
            {(cur.members||[]).length===0&&<span style={{fontSize:10,color:"var(--text-secondary)",fontStyle:"italic"}}>멤버 없음 — 아래 + 멤버 추가 에서 선택</span>}
          </div>
          {canEdit&&<div style={{display:"flex",gap:6,marginBottom:16}}>
            <select onChange={e=>{if(e.target.value){addMember(cur.id,e.target.value);e.target.value="";}}}
              style={{padding:"6px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,minWidth:260}}>
              <option value="">+ 멤버 추가…</option>
              {/* v8.8.27: 옵션 텍스트도 name+id. 이름이 없으면 id 만. */}
              {availableUserObjs.map(u=><option key={u.username} value={u.username}>{userLabel(u)}</option>)}
            </select>
            <span style={{fontSize:10,color:"var(--text-secondary)",alignSelf:"center"}}>test 계정만 자동 제외</span>
          </div>}

          {/* v8.8.5: 담당 모듈 UI 제거 — 불필요. 그룹은 단순 멤버 풀로 사용. */}

          {/* v8.8.23: 외부 고정 수신자(extra_emails) — 인폼/회의 메일 발송 시 자동 포함되는 주소. */}
          <div style={{marginTop:16}}>
            <div style={{fontSize:12,fontWeight:600,marginBottom:6}}>외부 수신자 이메일 ({(cur.extra_emails||[]).length})
              <span style={{marginLeft:8,fontSize:10,color:"var(--text-secondary)",fontWeight:400}}>
                메일 발송 시 members 의 사내 이메일과 함께 항상 포함됩니다.
              </span>
            </div>
            <div style={{display:"flex",flexWrap:"wrap",gap:6,marginBottom:8}}>
              {(cur.extra_emails||[]).map(e=>(
                <span key={e} style={{padding:"3px 10px",borderRadius:999,background:"var(--bg-tertiary)",fontSize:11,display:"inline-flex",alignItems:"center",gap:6,fontFamily:"monospace"}}>
                  {e}
                  {canEdit&&<button onClick={()=>{
                    const next=(cur.extra_emails||[]).filter(x=>x!==e);
                    sf("/api/groups/update?id="+encodeURIComponent(cur.id),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({extra_emails:next})})
                      .then(load).catch(err=>setMsg(err.message));
                  }} style={{border:"none",background:"transparent",color:"#ef4444",cursor:"pointer",fontSize:11,padding:0}}>×</button>}
                </span>
              ))}
              {(cur.extra_emails||[]).length===0&&<span style={{fontSize:10,color:"var(--text-secondary)",fontStyle:"italic"}}>외부 수신자 없음</span>}
            </div>
            {canEdit&&<ExtraEmailAdd current={cur} onSave={(next)=>
              sf("/api/groups/update?id="+encodeURIComponent(cur.id),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({extra_emails:next})})
                .then(load).catch(err=>setMsg(err.message))
            }/>}
          </div>

          <div style={{marginTop:16,padding:10,background:"var(--bg-primary)",borderRadius:6,fontSize:10,color:"var(--text-secondary)",lineHeight:1.6}}>
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

// ── Inform Config Panel (v8.8.1) — 모듈/사유/제품/DB경로 Admin 관리 ──
function InformConfigPanel(){
  const [cfg,setCfg]=useState({modules:[],reasons:[],products:[],raw_db_root:""});
  const [newMod,setNewMod]=useState("");
  const [newReason,setNewReason]=useState("");
  const [newProduct,setNewProduct]=useState("");
  const [rawRootDraft,setRawRootDraft]=useState("");
  const [msg,setMsg]=useState("");
  const load=()=>sf("/api/informs/config").then(d=>{setCfg(d);setRawRootDraft(d.raw_db_root||"");}).catch(e=>setMsg(e.message));
  // fix: arrow+Promise crash 회피.
  useEffect(()=>{load();},[]);
  const saveAll=(next)=>sf("/api/informs/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(next)})
    .then(r=>{setCfg(r.config||next);setMsg("저장되었습니다.");}).catch(e=>setMsg(e.message));
  const addMod=()=>{const v=newMod.trim();if(!v)return;
    if((cfg.modules||[]).includes(v)){setMsg("이미 존재합니다.");return;}
    saveAll({modules:[...(cfg.modules||[]),v]});setNewMod("");};
  const rmMod=(m)=>{if(!confirm(`모듈 '${m}' 삭제?`))return;
    saveAll({modules:(cfg.modules||[]).filter(x=>x!==m)});};
  const addReason=()=>{const v=newReason.trim();if(!v)return;
    if((cfg.reasons||[]).includes(v)){setMsg("이미 존재합니다.");return;}
    saveAll({reasons:[...(cfg.reasons||[]),v]});setNewReason("");};
  const rmReason=(r)=>{if(!confirm(`사유 '${r}' 삭제?`))return;
    saveAll({reasons:(cfg.reasons||[]).filter(x=>x!==r)});};
  const addProduct=()=>{const v=newProduct.trim();if(!v)return;
    if((cfg.products||[]).includes(v)){setMsg("이미 존재합니다.");return;}
    saveAll({products:[...(cfg.products||[]),v]});setNewProduct("");};
  const rmProduct=(p)=>{if(!confirm(`제품 '${p}' 삭제? (기존 인폼 레코드는 유지)`))return;
    saveAll({products:(cfg.products||[]).filter(x=>x!==p)});};
  const saveRawRoot=()=>saveAll({raw_db_root:rawRootDraft});

  // v8.7.5: Section 을 inline 컴포넌트로 두면 매 렌더마다 새 reference 라 input focus 가 날아감.
  // 여기서는 간단하게 JSX 로 inline 하게 두 블록을 렌더한다.
  const renderSection=(title,items,onRemove,addValue,onAddChange,onAdd,placeholder)=>(
    <div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16}}>
      <div style={{fontSize:13,fontWeight:700,marginBottom:10}}>{title} ({(items||[]).length})</div>
      <div style={{display:"flex",flexWrap:"wrap",gap:6,marginBottom:10}}>
        {(items||[]).map(m=>(
          <span key={m} style={{padding:"4px 12px",borderRadius:999,background:"var(--accent)22",color:"var(--accent)",fontSize:11,fontWeight:600,display:"inline-flex",alignItems:"center",gap:6}}>
            {m}
            <button onClick={()=>onRemove(m)} style={{border:"none",background:"transparent",color:"#ef4444",cursor:"pointer",fontSize:11,padding:0}}>×</button>
          </span>
        ))}
        {(items||[]).length===0&&<span style={{fontSize:11,color:"var(--text-secondary)"}}>없음</span>}
      </div>
      <div style={{display:"flex",gap:6}}>
        <input value={addValue||""} onChange={e=>onAddChange(e.target.value)} placeholder={placeholder}
          onKeyDown={e=>{if(e.key==="Enter")onAdd();}}
          style={{flex:1,padding:"6px 10px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12}}/>
        <button onClick={onAdd} style={{padding:"6px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:12,fontWeight:600,cursor:"pointer"}}>+추가</button>
      </div>
    </div>
  );
  return(<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,maxWidth:1000}}>
    {renderSection("모듈 옵션",cfg.modules||[],rmMod,newMod,setNewMod,addMod,"예: NEW_MOD")}
    {renderSection("사유 옵션",cfg.reasons||[],rmReason,newReason,setNewReason,addReason,"예: 신뢰성 이슈")}
    {renderSection("제품 카탈로그",cfg.products||[],rmProduct,newProduct,setNewProduct,addProduct,"예: PROD_A")}
    {/* v9.0.0: RAWDATA_DB 루트 경로 섹션 제거 — SplitTable source_config 의 제품별 fab_source override 로 통합.
        인폼 Lot 드롭다운이 SplitTable override 경로를 공유 — 관리 지점 단일화. */}
    {msg&&<div style={{gridColumn:"span 2",fontSize:11,color:"var(--accent)"}}>{msg}</div>}
    <div style={{gridColumn:"span 2",padding:12,background:"var(--bg-primary)",borderRadius:6,fontSize:11,color:"var(--text-secondary)",lineHeight:1.6}}>
      • 여기서 편집한 옵션은 인폼 작성/답글 드롭다운, 그룹 담당 모듈 선택, 대시보드 모듈 필터에 반영됩니다.<br/>
      • 기존 인폼에 이미 저장된 값은 목록에서 빠져도 그대로 보존됩니다 (표시만 자유문자열).<br/>
      • 기본값(GATE/STI/PC/MOL/…, 재측정/장비 이상/…)은 비워지면 자동 복구됩니다.<br/>
      • <b>v9.0.0</b>: RAWDATA_DB 루트 경로는 <b>SplitTable</b> 의 source_config (제품별 fab_source override) 에서 관리합니다 — 인폼 Lot 드롭다운은 그 경로를 그대로 공유합니다.<br/>
      • <b>데이터 루트</b> 전반 설정은 우측 사이드바의 <b>데이터 루트</b> 탭을 사용하세요.
    </div>
  </div>);
};

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
            padding:"5px 12px",borderRadius:6,cursor:"pointer",fontSize:12,fontWeight:cur===c.key?700:400,
            background:cur===c.key?"var(--accent-glow)":"transparent",
            color:cur===c.key?"var(--accent)":"var(--text-secondary)",
            border:"1px solid "+(cur===c.key?"var(--accent)":"var(--border)"),
            fontFamily:"monospace",
          }}>{c.label}</span>
        ))}
        <div style={{flex:1}}/>
        <input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="필터..."
          style={{padding:"5px 10px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:11,width:160}}/>
        <button onClick={()=>load(cur)} style={{padding:"5px 10px",borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:11,cursor:"pointer"}}>↻ 재로드</button>
      </div>
      <div style={{fontSize:10,color:"var(--text-secondary)",marginBottom:8}}>
        컬럼: <code>{columns.join(", ")}</code> · unique: <code>{uniqueKey.join(", ")}</code> · 총 {rows.length}행
        {filter&&` · 필터 매칭 ${filtered.length}행`}
      </div>

      <div style={{maxHeight:500,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-primary)"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:11,fontFamily:"monospace"}}>
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
                      style={{width:"100%",padding:"4px 8px",border:"none",background:"transparent",color:"var(--text-primary)",fontFamily:"monospace",fontSize:11,outline:"none"}}/>
                  </td>
                ))}
                <td style={{padding:"2px 4px",borderLeft:"1px solid var(--border)",whiteSpace:"nowrap"}}>
                  <span onClick={()=>moveRow(ri,-1)} style={{cursor:"pointer",color:"var(--text-secondary)",padding:"0 4px"}}>↑</span>
                  <span onClick={()=>moveRow(ri,+1)} style={{cursor:"pointer",color:"var(--text-secondary)",padding:"0 4px"}}>↓</span>
                  <span onClick={()=>delRow(ri)} style={{cursor:"pointer",color:"#ef4444",padding:"0 4px"}}>✕</span>
                </td>
              </tr>
            ))}
            {rows.length===0&&<tr><td colSpan={columns.length+2} style={{padding:20,textAlign:"center",color:"var(--text-secondary)"}}>데이터 없음. 아래 '+행 추가' 로 시작하세요.</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={{display:"flex",gap:8,marginTop:12,alignItems:"center"}}>
        <button onClick={addRow} style={{padding:"7px 14px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontSize:12,cursor:"pointer"}}>+ 행 추가</button>
        <button onClick={save} disabled={saving} style={{padding:"7px 18px",borderRadius:5,border:"none",background:"var(--accent)",color:"#fff",fontWeight:700,fontSize:12,cursor:saving?"wait":"pointer"}}>{saving?"저장 중...":"저장"}</button>
        {msg&&<span style={{fontSize:11,color:msg.startsWith("저장")?"#22c55e":"#ef4444"}}>{msg}</span>}
      </div>

      <div style={{marginTop:14,padding:10,background:"var(--bg-primary)",borderRadius:6,fontSize:10,color:"var(--text-secondary)",lineHeight:1.6}}>
        • 컬럼 뒤 <b style={{color:"var(--accent)"}}>*</b> 는 unique key. 중복 시 저장 거부.<br/>
        • step_matching: (step_id, func_step) — step_id 유니크.<br/>
        • knob_ppid: (feature_name, function_step, rule_order, ppid, operator, category, use) — 앞 3개 복합 unique. use ∈ Y/N/0/1.<br/>
        • 저장 시 UTF-8 BOM 포함 CSV 로 덮어씁니다 (Excel 호환). SplitTable KNOB 메타는 자동 재조회.
      </div>
    </div>
  );
}
