import { useState, useEffect, useRef } from "react";
import Loading from "../components/Loading";
import { PROCESS_AREAS, areaColor } from "../constants/processAreas";
import { sf, dl } from "../lib/api";
const ALL_TABS=["filebrowser","dashboard","splittable","tracker","tablemap","ml","devguide","dashboard_chart"];

function Gauge({label,pct,used,total,unit="GB"}){
  const color=pct>85?"#ef4444":pct>60?"#fbbf24":"#22c55e";
  return(<div style={{background:"var(--bg-card)",borderRadius:8,padding:"12px 16px",border:"1px solid var(--border)"}}>
    <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}><span style={{fontSize:12,fontWeight:600}}>{label}</span><span style={{fontSize:12,fontWeight:700,color}}>{pct}%</span></div>
    <div style={{height:6,borderRadius:3,background:"var(--border)"}}><div style={{height:6,borderRadius:3,background:color,width:Math.min(pct,100)+"%",transition:"width 0.3s"}}/></div>
    <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:4}}>{used} / {total} {unit}</div>
  </div>);
}

const FARM_ANIM=`@keyframes fabFarm{0%{transform:translateX(0)}50%{transform:translateX(10px)}100%{transform:translateX(0)}}`;

export default function My_Admin({user}){
  const isAdmin=user?.role==="admin";
  const[users,setUsers]=useState([]);const[logs,setLogs]=useState([]);const[notifs,setNotifs]=useState([]);
  const[tab,setTab]=useState("notifs");const[dlHistory,setDlHistory]=useState([]);
  const[sys,setSys]=useState({});const[resLog,setResLog]=useState([]);const[farmStatus,setFarmStatus]=useState({});
  const[editPerm,setEditPerm]=useState(null);const[permTabs,setPermTabs]=useState([]);

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
      sf("/api/admin/logs?limit=100").then(d=>setLogs(d.logs||[])).catch(()=>{});
    } else {
      // User: load own logs and downloads
      sf("/api/admin/logs?limit=100&username="+(user?.username||"")).then(d=>setLogs(d.logs||[])).catch(()=>{});
      loadDl();
    }
  };
  useEffect(load,[]);
  // v8.2.0: Bell dismiss / external read → re-load this tab's notif list immediately
  useEffect(()=>{
    const onRefresh=()=>load();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>window.removeEventListener("hol:notif-refresh",onRefresh);
  },[user]);

  const loadDl=()=>{
    const url=isAdmin?"/api/filebrowser/download-history":"/api/filebrowser/download-history?username="+(user?.username||"");
    sf(url).then(d=>setDlHistory(d.logs||[])).catch(()=>{});
  };
  const loadSys=()=>{sf("/api/monitor/system").then(setSys).catch(()=>{});
    sf("/api/monitor/resource-log?limit=200").then(d=>setResLog(d.logs||[])).catch(()=>{});
    sf("/api/monitor/farm-status").then(setFarmStatus).catch(()=>{});};
  const action=(url,body)=>sf(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(()=>setTimeout(load,500));
  const savePerm=()=>{if(!editPerm)return;sf("/api/admin/set-tabs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:editPerm,tabs:permTabs})}).then(()=>{setEditPerm(null);load();setTab("perms");});};
  const markRead=(ids)=>{if(!ids.length)return;sf("/api/admin/mark-read-batch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:user?.username||"",ids})}).then(()=>{load();window.dispatchEvent(new CustomEvent("hol:notif-refresh"));}).catch(()=>{});};
  const toggleRead=(n)=>{if(!n.id)return;markRead([n.id]);};

  const tS=(a)=>({padding:"10px 16px",fontSize:12,cursor:"pointer",fontWeight:a?600:400,borderBottom:a?"2px solid var(--accent)":"2px solid transparent",color:a?"var(--text-primary)":"var(--text-secondary)"});

  // Tabs differ by role
  // v8.4.3 단위기능 페이지 철학: AWS 설정은 FileBrowser 톱니로 이관 예정 (제거).
  const adminTabs=[["users","사용자"],["notifs","알림"],["perms","권한"],["logs","로그"],["downloads","다운로드"],["monitor","모니터"],["data_roots","데이터 루트"]];
  const userTabs=[["notifs","알림"],["logs","내 로그"],["downloads","내 다운로드"]];
  const tabs=isAdmin?adminTabs:userTabs;

  return(
    <div style={{padding:"24px 32px",background:"var(--bg-primary)",minHeight:"calc(100vh - 48px)",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif"}}>
      <div style={{display:"flex",borderBottom:"1px solid var(--border)",marginBottom:20,flexWrap:"wrap"}}>
        {tabs.map(([k,l])=>(
          <div key={k} style={tS(tab===k)} onClick={()=>{setTab(k);if(k==="downloads")loadDl();if(k==="monitor")loadSys();}}>{l}</div>))}
      </div>

      {/* Users (admin only) */}
      {tab==="users"&&isAdmin&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",overflow:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
          <thead><tr>{["사용자","역할","상태","탭","작업"].map(h=><th key={h} style={{textAlign:"left",padding:"10px 14px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:11,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
          <tbody>{users.map((u,i)=><tr key={i}>
            <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>{u.username}</td>
            <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>{u.role}</td>
            <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}><span style={{fontSize:11,padding:"2px 8px",borderRadius:4,background:u.status==="approved"?"#05966922":"#f5920b22",color:u.status==="approved"?"#22c55e":"#f59e0b"}}>{u.status}</span></td>
            <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)",maxWidth:200,overflow:"hidden",textOverflow:"ellipsis"}}>{u.tabs||"default"}</td>
            <td style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>
              {u.status==="pending"&&<><span onClick={()=>action("/api/admin/approve",{username:u.username})} style={{color:"#22c55e",cursor:"pointer",marginRight:12,fontSize:12}}>승인</span><span onClick={()=>action("/api/admin/reject",{username:u.username})} style={{color:"#ef4444",cursor:"pointer",fontSize:12}}>거절</span></>}
              {u.status==="approved"&&u.role!=="admin"&&<><span onClick={()=>action("/api/admin/reset-password",{username:u.username})} style={{color:"var(--accent)",cursor:"pointer",fontSize:12,marginRight:8}}>비밀번호 초기화(1111)</span>
              <span onClick={()=>{if(confirm("삭제하시겠습니까?"))action("/api/admin/delete-user",{username:u.username});}} style={{color:"#ef4444",cursor:"pointer",fontSize:12,marginRight:8}}>삭제</span>
              <span onClick={()=>{setEditPerm(u.username);setPermTabs((u.tabs||"filebrowser,dashboard,splittable").split(",").filter(Boolean));setTab("perms");}} style={{color:"#3b82f6",cursor:"pointer",fontSize:12}}>권한</span></>}
            </td></tr>)}</tbody>
        </table></div>}

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
            <tbody>{users.filter(u=>u.role!=="admin"&&u.status==="approved").map((u,i)=>{
              const ut=(u.tabs||"filebrowser,dashboard,splittable").split(",").filter(Boolean);
              return(<tr key={i}>
                <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontWeight:600,position:"sticky",left:0,background:"var(--bg-secondary)",zIndex:1}}>{u.username}</td>
                {ALL_TABS.map(t=><td key={t} style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
                  <span style={{fontSize:12,color:ut.includes(t)?"#22c55e":"#ef4444",fontWeight:700}}>{ut.includes(t)?"O":"X"}</span>
                </td>)}
                <td style={{textAlign:"center",padding:"6px",borderBottom:"1px solid var(--border)"}}>
                  <span onClick={()=>{setEditPerm(u.username);setPermTabs(ut);}} style={{color:"#3b82f6",cursor:"pointer",fontSize:11}}>편집</span>
                </td>
              </tr>);})}</tbody>
          </table>
        </div>}
        {/* Edit single user permissions */}
        {editPerm&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:20,maxWidth:400}}>
          <div style={{fontSize:14,fontWeight:700,marginBottom:12}}>권한: {editPerm}</div>
          {ALL_TABS.map(t=>(<label key={t} style={{display:"flex",alignItems:"center",gap:8,padding:"6px 0",fontSize:13,cursor:"pointer"}}><input type="checkbox" checked={permTabs.includes(t)} onChange={e=>{if(e.target.checked)setPermTabs([...permTabs,t]);else setPermTabs(permTabs.filter(x=>x!==t));}}/>{t}</label>))}
          <div style={{display:"flex",gap:8,marginTop:12}}>
            <button onClick={savePerm} style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>저장</button>
            <button onClick={()=>{setEditPerm(null);}} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>취소</button>
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
            <button onClick={sendInquiry} disabled={!inquiry.trim()} style={{padding:"8px 16px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontSize:12,fontWeight:600,cursor:"pointer",opacity:inquiry.trim()?1:0.5}}>전송</button>
          </div>
        </div>}
        {/* Actions bar */}
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:11,color:"var(--text-secondary)"}}>읽지 않음 {notifs.filter(n=>!n.read).length} / 전체 {notifs.length}</span>
          {notifs.some(n=>!n.read)&&<button onClick={()=>markRead(notifs.filter(n=>!n.read).map(n=>n.id))} style={{padding:"4px 14px",borderRadius:4,border:"1px solid var(--accent)",background:"var(--accent-glow)",color:"var(--accent)",fontSize:11,fontWeight:600,cursor:"pointer"}}>모두 읽음으로 표시</button>}
        </div>
        <div style={{maxHeight:460,overflowY:"auto"}}>
        {notifs.length===0&&<div style={{color:"var(--text-secondary)",fontSize:13,padding:20,textAlign:"center"}}>알림 없음</div>}
        {[...notifs].reverse().map((n,i)=>(
          <div key={n.id||i} style={{borderBottom:"1px solid var(--border)",fontSize:13,display:"flex",gap:8,alignItems:"flex-start",borderRadius:4,padding:"8px 6px",opacity:n.read?0.5:1}}>
            <input type="checkbox" checked={!!n.read} onChange={()=>{if(!n.read)toggleRead(n);}} disabled={!!n.read} title={n.read?"읽음":"읽음으로 표시"} style={{marginTop:2,accentColor:"#22c55e",flexShrink:0,cursor:n.read?"default":"pointer"}}/>
            <div style={{flex:1}}>
              <div style={{display:"flex",gap:8,alignItems:"center",marginBottom:4}}>
                <span style={{fontSize:10,padding:"2px 6px",borderRadius:3,fontWeight:700,color:"#fff",background:n.type==="approval"?"#f97316":n.type==="message"?"#3b82f6":"#6b7280"}}>{n.type}</span>
                <span style={{fontWeight:n.read?400:600}}>{n.title}</span>
                <span style={{fontSize:11,color:"var(--text-secondary)",marginLeft:"auto"}}>{n.timestamp?.slice(0,16)}</span>
              </div>
              <div style={{color:"var(--text-secondary)",fontSize:12,paddingLeft:4}}>{n.body}</div>
            </div>
          </div>))}
        </div>
      </div>}

      {/* Logs */}
      {tab==="logs"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",padding:16,maxHeight:500,overflowY:"auto",fontFamily:"monospace",fontSize:12}}>
        {logs.length===0&&<div style={{color:"var(--text-secondary)"}}>로그 없음</div>}
        {[...logs].reverse().map((l,i)=><div key={i} style={{padding:"4px 0",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)",display:"flex",gap:12}}>
          <span style={{color:"var(--accent)",minWidth:140}}>{l.timestamp?.slice(0,19)}</span>
          <span style={{color:"var(--text-primary)",minWidth:80}}>{l.username}</span><span>{l.action}</span></div>)}</div>}

      {/* Downloads */}
      {tab==="downloads"&&<div style={{background:"var(--bg-secondary)",borderRadius:10,border:"1px solid var(--border)",overflow:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
          <thead><tr>{["시간","사용자","Product","SQL","컬럼","행","크기"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:11,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
          <tbody>{[...dlHistory].reverse().map((d,i)=><tr key={i}>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)"}}>{d.timestamp?.slice(0,16)}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{d.username}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{d.product}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10,maxWidth:160,overflow:"hidden",textOverflow:"ellipsis"}} title={d.sql||""}>{d.sql||"-"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:10,maxWidth:140,overflow:"hidden",textOverflow:"ellipsis",color:"var(--text-secondary)"}} title={d.select_cols||""}>{d.select_cols||"all"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{d.rows?.toLocaleString()}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)"}}>{d.size_mb?d.size_mb+"MB":"-"}</td>
          </tr>)}</tbody></table></div>}

      {/* Monitor (admin only) */}
      {tab==="monitor"&&isAdmin&&<div>
        <style>{FARM_ANIM}</style>
        {farmStatus.farming&&<div style={{background:"#f9731622",border:"1px solid #f97316",borderRadius:10,padding:16,marginBottom:16,display:"flex",alignItems:"center",gap:16}}>
          <div style={{animation:"fabFarm 1s ease-in-out infinite",fontSize:32}}>🧑‍🌾</div>
          <div><div style={{fontSize:14,fontWeight:700,color:"#f97316"}}>FAB-i 가 farming 중...</div>
            <div style={{fontSize:12,color:"var(--text-secondary)"}}>리소스를 활성 상태로 유지합니다</div></div>
        </div>}
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:12,marginBottom:20}}>
          <Gauge label="CPU" pct={sys.cpu_pct||0} used={`${sys.cpu_pct||0}%`} total="100%" unit=""/>
          <Gauge label="메모리" pct={sys.mem_pct||0} used={sys.mem_used||"?"} total={sys.mem_total||"?"} unit=""/>
          <Gauge label="디스크" pct={sys.disk_pct||0} used={sys.disk_used||"?"} total={sys.disk_total||"?"} unit=""/>
        </div>
        {resLog.length>0&&<div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:12,maxHeight:300,overflow:"auto"}}>
          <div style={{fontSize:12,fontWeight:600,marginBottom:8}}>리소스 로그</div>
          <div style={{fontSize:10,fontFamily:"monospace"}}>{[...resLog].reverse().slice(0,50).map((r,i)=><div key={i} style={{padding:"2px 0",color:"var(--text-secondary)"}}>{r.timestamp?.slice(11,19)} CPU:{r.cpu}% Mem:{r.mem}%</div>)}</div>
        </div>}
      </div>}

      {/* Categories (admin only) */}
      {tab==="categories"&&isAdmin&&<CategoryPanel/>}

      {/* Catalog (admin only) — matching tables + product config + S3 sync */}
      {tab==="catalog"&&isAdmin&&<CatalogPanel/>}

      {/* AWS Config (admin only) */}
      {tab==="aws"&&isAdmin&&<AWSPanel user={user}/>}

      {/* Messages sub-tab removed in v8.3.1 — functionality moved to Home Contact 섹션 */}

      {/* Data Roots (admin only) — v8.3.0: soft-landing env abstraction */}
      {tab==="data_roots"&&isAdmin&&<DataRootsPanel/>}
    </div>);
}

// ── Data Roots Panel (v8.3.0) ──
function DataRootsPanel(){
  const[eff,setEff]=useState({db_root:"",base_root:"",wafer_map_root:"",sources:{}});
  const[form,setForm]=useState({db_root:"",base_root:"",wafer_map_root:""});
  const[msg,setMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const reload=()=>{
    sf("/api/admin/settings").then(d=>{
      const dr=d.data_roots||{db_root:"",base_root:"",wafer_map_root:"",sources:{}};
      setEff(dr);
      // Form stays empty — user only types to override.
    }).catch(e=>setMsg("로드 오류: "+e.message));
  };
  useEffect(reload,[]);
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
          data_roots: form,
        })});
    }).then(()=>{setMsg("저장되었습니다. 새 요청부터 적용됩니다.");setForm({db_root:"",base_root:"",wafer_map_root:""});reload();})
      .catch(e=>setMsg("저장 오류: "+e.message))
      .finally(()=>setBusy(false));
  };
  const L={fontSize:12,fontWeight:600,marginBottom:4,color:"var(--text-primary)"};
  const I={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",
           background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",
           fontFamily:"monospace",boxSizing:"border-box"};
  const H={fontSize:10,color:"var(--text-secondary)",marginTop:4,fontFamily:"monospace"};
  const srcBadge=(s)=>{const map={env:"#3b82f6",settings:"#22c55e",default:"#6b7280"};
    return<span style={{fontSize:9,padding:"1px 6px",borderRadius:3,background:(map[s]||"#6b7280")+"22",color:map[s]||"#6b7280",fontWeight:700,marginLeft:6}}>{s}</span>;};
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
    <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:16,lineHeight:1.5}}>
      FabCanvas 가 사용하는 데이터 루트를 런타임에 오버라이드합니다. 우선순위:
      <b> FABCANVAS_* env → admin_settings.data_roots → legacy HOL_* → default</b>.
      빈 값으로 저장하면 오버라이드가 제거되고 env/default 로 돌아갑니다.
    </div>
    {field("db_root","DB 루트","FABCANVAS_DB_ROOT")}
    {field("base_root","Base 루트","FABCANVAS_BASE_ROOT")}
    {field("wafer_map_root","Wafer-map 루트 (optional)","FABCANVAS_WAFER_MAP_ROOT")}
    <div style={{display:"flex",gap:8,marginTop:16,alignItems:"center"}}>
      <button data-dr-btn="save" onClick={save} disabled={busy}
        style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:busy?"default":"pointer",opacity:busy?0.5:1}}>
        {busy?"저장 중...":"저장"}
      </button>
      <button data-dr-btn="reload" onClick={reload} disabled={busy}
        style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>
        새로고침
      </button>
      {msg&&<span data-dr-msg style={{fontSize:11,color:msg.startsWith("저장되었습니다")?"#22c55e":"#ef4444"}}>{msg}</span>}
    </div>
  </div>);
}

function CategoryPanel(){
  // v8.1.5: cats = [{name, color}, ...]  (backend auto-upgrades legacy str list)
  const[cats,setCats]=useState([]);const[newCat,setNewCat]=useState("");const[newColor,setNewColor]=useState("#3b82f6");
  const[editIdx,setEditIdx]=useState(-1);const[editVal,setEditVal]=useState("");const[msg,setMsg]=useState("");
  const[usage,setUsage]=useState({counts:{},orphans:{},total:0});
  const load=()=>{
    sf("/api/tracker/categories").then(d=>setCats((d.categories||[]).map(c=>typeof c==="string"?{name:c,color:"#64748b"}:c))).catch(()=>{});
    sf("/api/tracker/categories/usage").then(d=>setUsage(d||{counts:{},orphans:{},total:0})).catch(()=>{});
  };
  useEffect(()=>{load();},[]);
  const save=(next)=>sf("/api/tracker/categories/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(next)}).then(()=>{setCats(next);setMsg("저장됨 ✓");setTimeout(()=>setMsg(""),1500);load();}).catch(e=>setMsg("오류: "+e.message));
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
      {msg&&<span style={{fontSize:11,color:msg.startsWith("오류")?"#ef4444":"#22c55e",fontFamily:"monospace"}}>{msg}</span>}
    </div>
    <div style={{display:"flex",gap:8,marginBottom:14,alignItems:"center"}}>
      <input type="color" value={newColor} onChange={e=>setNewColor(e.target.value)} style={{width:40,height:36,padding:0,border:"1px solid var(--border)",borderRadius:6,cursor:"pointer",background:"transparent"}} title="카테고리 색상"/>
      <input value={newCat} onChange={e=>setNewCat(e.target.value)} placeholder="새 카테고리 이름" onKeyDown={e=>e.key==="Enter"&&add()} style={{...S,flex:1}}/>
      <button onClick={add} disabled={!newCat.trim()} style={{padding:"8px 16px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer",opacity:newCat.trim()?1:0.5}}>+ 추가</button>
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
        <span onClick={()=>startEdit(i)} style={{cursor:"pointer",fontSize:11,color:"#3b82f6",padding:"2px 6px"}}>편집</span>
        <span onClick={()=>{if(n>0&&!confirm(`"${c.name}" 은(는) ${n}개 이슈에서 사용 중입니다. 그래도 삭제하시겠습니까? 기존 이슈는 고아(orphan) 상태가 됩니다.`))return;del(i);}} style={{cursor:"pointer",fontSize:11,color:"#ef4444",padding:"2px 6px"}}>삭제</span>
      </div>);})}
      {Object.keys(usage.orphans||{}).length>0&&<div style={{padding:"10px 12px",background:"rgba(239,68,68,0.08)",borderTop:"1px solid var(--border)"}}>
        <div style={{fontSize:10,fontWeight:700,color:"#ef4444",marginBottom:4}}>⚠ 고아 카테고리 (이슈에서 사용 중이나 목록에 없음)</div>
        {Object.entries(usage.orphans).map(([oc,n])=>(<div key={oc} style={{display:"flex",justifyContent:"space-between",fontSize:11,fontFamily:"monospace",marginBottom:2}}>
          <span>{oc}</span>
          <span style={{color:"var(--text-secondary)"}}>{n}개 이슈 — <span onClick={()=>{if(confirm(`"${oc}" 을(를) 카테고리 목록에 복원하시겠습니까?`))save([...cats,{name:oc,color:"#64748b"}]);}} style={{cursor:"pointer",color:"#3b82f6"}}>복원</span></span>
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
  useEffect(load,[]);
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
          <span style={{fontSize:12,fontWeight:700,fontFamily:"monospace",color:t.exists?"var(--text-primary)":"#94a3b8"}}>{t.name}</span>
          <span style={{fontSize:9,padding:"1px 6px",borderRadius:3,background:t.exists?"rgba(16,185,129,0.15)":"rgba(239,68,68,0.15)",color:t.exists?"#10b981":"#ef4444",fontWeight:700}}>{t.exists?t.rows+"행":"없음"}</span>
        </div>
        <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:2}}>{t.description}</div>
        <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:2,fontFamily:"monospace"}}>적용: {(t.applies_to||[]).join(", ")}</div>
        {t.missing_cols?.length>0&&<div style={{fontSize:9,color:"#ef4444",marginTop:2}}>⚠ 누락 컬럼: {t.missing_cols.join(", ")}</div>}
      </div>))}
    </div>
    <div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",padding:16,minHeight:300}}>
      {!sel&&<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}>미리보기를 위해 좌측에서 매칭 테이블을 선택하세요</div>}
      {sel&&preview&&(<>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
          <span style={{fontSize:13,fontWeight:700,fontFamily:"monospace"}}>{sel}</span>
          <div style={{display:"flex",gap:6,alignItems:"center"}}>
            {saveMsg&&<span style={{fontSize:10,fontFamily:"monospace",color:saveMsg.startsWith("⚠")?"#ef4444":"#10b981"}}>{saveMsg}</span>}
            {hasAreaCol&&Object.keys(edits).length>0&&<button onClick={saveAreas} style={{padding:"4px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:10,fontWeight:700,cursor:"pointer"}} title="영역 편집 저장">💾 저장 ({Object.keys(edits).length})</button>}
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
  useEffect(load,[]);
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
  const[cfg,setCfg]=useState({bucket:"",prefix:"holweb/artifacts/",region:"ap-northeast-2",enabled:false,profile:""});
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
        <span style={{fontSize:10,padding:"2px 8px",borderRadius:10,background:boto?"rgba(16,185,129,0.15)":"rgba(239,68,68,0.15)",color:boto?"#10b981":"#ef4444",fontWeight:700}}>{boto?"boto3 설치됨":"boto3 없음 (로그만 기록)"}</span>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr",gap:8,marginBottom:8}}>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>Bucket</div><input value={cfg.bucket} onChange={e=>setCfg({...cfg,bucket:e.target.value})} style={{...S,width:"100%"}} placeholder="my-bucket"/></div>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>Prefix</div><input value={cfg.prefix} onChange={e=>setCfg({...cfg,prefix:e.target.value})} style={{...S,width:"100%"}}/></div>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>리전</div><input value={cfg.region} onChange={e=>setCfg({...cfg,region:e.target.value})} style={{...S,width:"100%"}}/></div>
        <div><div style={{fontSize:10,color:"var(--text-secondary)"}}>프로파일 (선택)</div><input value={cfg.profile} onChange={e=>setCfg({...cfg,profile:e.target.value})} style={{...S,width:"100%"}}/></div>
      </div>
      <div style={{display:"flex",gap:12,alignItems:"center"}}>
        <label style={{fontSize:11,display:"flex",alignItems:"center",gap:4,fontFamily:"monospace"}}><input type="checkbox" checked={cfg.enabled} onChange={e=>setCfg({...cfg,enabled:e.target.checked})} style={{accentColor:"var(--accent)"}}/>활성화</label>
        <button onClick={saveCfg} style={{padding:"5px 14px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,fontWeight:600,cursor:"pointer"}}>설정 저장</button>
        <button onClick={()=>syncAll("")} style={{padding:"5px 14px",borderRadius:4,border:"1px solid #10b981",background:"rgba(16,185,129,0.1)",color:"#10b981",fontSize:11,fontWeight:600,cursor:"pointer"}}>▶ 전체 동기화</button>
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
        <tbody>{items.map((a,i)=>{const last=a.last_sync;const st=last?.status;const color=a.in_sync?"#10b981":st==="error"?"#ef4444":st==="queued"?"#f59e0b":"#94a3b8";return(<tr key={i} style={{borderBottom:"1px solid rgba(255,255,255,0.04)"}}>
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
          <span style={{color:"var(--accent)"}}>{e.ts?.slice(11,19)}</span> <span style={{color:e.status==="uploaded"?"#10b981":e.status==="error"?"#ef4444":"#f59e0b"}}>{e.status}</span> {e.s3_key||e.key} {e.error?"— "+e.error:""}
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
              {t.unread_for_admin>0&&<span style={{fontSize:9,fontWeight:700,padding:"1px 5px",borderRadius:3,background:"var(--accent)",color:"#fff"}}>{t.unread_for_admin}</span>}
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
                <div style={{padding:"8px 12px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:13,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
              </div>
            </div>);})}
        </div>
        <div style={{padding:"10px 14px",borderTop:"1px solid var(--border)"}}>
          <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
            <textarea value={reply} onChange={e=>setReply(e.target.value)} disabled={sending} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")sendReply();}} placeholder={`${sel} 에게 답장 (Cmd/Ctrl+Enter 전송)`} rows={2} style={{flex:1,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
            <button onClick={sendReply} disabled={sending||!reply.trim()} style={{padding:"8px 18px",borderRadius:6,border:"none",background:sending||!reply.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:sending||!reply.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>{sending?"…":"답장"}</button>
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
      <button onClick={()=>setShowNew(!showNew)} style={{padding:"6px 14px",borderRadius:5,border:"1px solid var(--accent)",background:showNew?"var(--accent)":"transparent",color:showNew?"#fff":"var(--accent)",fontSize:11,fontWeight:700,cursor:"pointer"}}>{showNew?"취소":"+ 새 공지사항"}</button>
    </div>
    {showNew&&<div style={{background:"var(--bg-secondary)",border:"1px solid var(--accent)",borderRadius:8,padding:16,marginBottom:14}}>
      <input value={title} onChange={e=>setTitle(e.target.value)} placeholder="제목 (최대 200자)" maxLength={200} style={{...S,marginBottom:8,fontWeight:600}}/>
      <textarea value={body} onChange={e=>setBody(e.target.value)} placeholder="공지 본문 (최대 5000자)" rows={5} style={{...S,marginBottom:8,resize:"vertical"}}/>
      <div style={{display:"flex",alignItems:"center"}}>
        <span style={{fontSize:10,color:"var(--text-secondary)"}}>{title.length}/200 · {body.length}/5000</span>
        <div style={{flex:1}}/>
        <button onClick={create} disabled={sending||(!title.trim()&&!body.trim())} style={{padding:"7px 18px",borderRadius:5,border:"none",background:sending||(!title.trim()&&!body.trim())?"#94a3b8":"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:sending?"default":"pointer"}}>{sending?"…":"발행"}</button>
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
