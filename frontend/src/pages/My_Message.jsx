/* My_Message.jsx v8.1.6 — User Messages page:
 *   - Notices tab: admin broadcast list (unread highlighted, click to read)
 *   - Inquiry tab: 1:1 chat with admin
 */
import { useState, useEffect, useRef } from "react";
import Loading from "../components/Loading";
import { sf, postJson } from "../lib/api";

function fmtTime(iso){
  if(!iso)return "";
  try{const d=new Date(iso);const mm=String(d.getMonth()+1).padStart(2,"0");const dd=String(d.getDate()).padStart(2,"0");const H=String(d.getHours()).padStart(2,"0");const M=String(d.getMinutes()).padStart(2,"0");return `${mm}-${dd} ${H}:${M}`;}catch{return (iso||"").slice(0,16).replace("T"," ");}
}

function NoticePanel({user,onAfterRead}){
  const[notices,setNotices]=useState([]);const[loading,setLoading]=useState(true);
  const[expanded,setExpanded]=useState(new Set());
  const load=()=>{
    setLoading(true);
    sf("/api/messages/notices?username="+encodeURIComponent(user?.username||""))
      .then(d=>setNotices(d.notices||[])).catch(()=>{}).finally(()=>setLoading(false));
  };
  useEffect(()=>{load();},[user?.username]);
  const toggle=(n)=>{
    const nxt=new Set(expanded);if(nxt.has(n.id))nxt.delete(n.id);else nxt.add(n.id);setExpanded(nxt);
    if(!n.read){
      postJson("/api/messages/notice_read",{username:user?.username||"",ids:[n.id]})
        .then(()=>{setNotices(p=>p.map(x=>x.id===n.id?{...x,read:true}:x));if(onAfterRead)onAfterRead();})
        .catch(()=>{});
    }
  };
  const markAll=()=>{
    const unreadIds=notices.filter(n=>!n.read).map(n=>n.id);
    if(!unreadIds.length)return;
    postJson("/api/messages/notice_read",{username:user?.username||"",ids:unreadIds})
      .then(()=>{setNotices(p=>p.map(x=>({...x,read:true})));if(onAfterRead)onAfterRead();})
      .catch(()=>{});
  };
  if(loading)return<div style={{padding:40,textAlign:"center"}}><Loading text="불러오는 중..."/></div>;
  const unreadCount=notices.filter(n=>!n.read).length;
  return(<div>
    <div style={{display:"flex",alignItems:"center",padding:"10px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)"}}>
      <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{"> 공지"}</span>
      <span style={{fontSize:11,color:"var(--text-secondary)",marginLeft:10}}>전체 {notices.length}건 · 안 읽음 {unreadCount}건</span>
      <div style={{flex:1}}/>
      {unreadCount>0&&<button onClick={markAll} style={{padding:"4px 10px",fontSize:10,borderRadius:4,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",cursor:"pointer",fontWeight:600}}>모두 읽음</button>}
    </div>
    {notices.length===0?<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)",fontSize:13}}>공지사항이 없습니다.</div>:
    <div>{notices.map(n=>{const isExp=expanded.has(n.id);return(
      <div key={n.id} onClick={()=>toggle(n)} style={{borderBottom:"1px solid var(--border)",padding:"12px 16px",cursor:"pointer",background:n.read?"transparent":"var(--accent-glow)"}}>
        <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:isExp?8:0}}>
          {!n.read&&<span style={{width:7,height:7,borderRadius:"50%",background:"var(--accent)",flexShrink:0}}/>}
          <span style={{fontSize:13,fontWeight:n.read?500:700,color:"var(--text-primary)",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:isExp?"normal":"nowrap"}}>{n.title||"(제목 없음)"}</span>
          <span style={{fontSize:10,color:"var(--text-secondary)",flexShrink:0,fontFamily:"monospace"}}>{n.author}</span>
          <span style={{fontSize:10,color:"var(--text-secondary)",flexShrink:0,fontFamily:"monospace"}}>{fmtTime(n.created_at)}</span>
        </div>
        {isExp&&n.body&&<div style={{fontSize:12,color:"var(--text-primary)",lineHeight:1.6,paddingLeft:15,whiteSpace:"pre-wrap",marginTop:6}}>{n.body}</div>}
      </div>);})}</div>}
  </div>);
}

function InquiryPanel({user,onAfterRead}){
  const[thread,setThread]=useState({messages:[]});const[loading,setLoading]=useState(true);
  const[text,setText]=useState("");const[sending,setSending]=useState(false);
  const listRef=useRef(null);
  const uname=user?.username||"";
  const load=(markRead=true)=>{
    sf("/api/messages/thread?username="+encodeURIComponent(uname))
      .then(d=>{setThread(d||{messages:[]});if(markRead){postJson("/api/messages/mark_read",{username:uname}).then(()=>{if(onAfterRead)onAfterRead();}).catch(()=>{});}})
      .catch(()=>{}).finally(()=>setLoading(false));
  };
  useEffect(()=>{load();},[uname]);
  useEffect(()=>{if(listRef.current)listRef.current.scrollTop=listRef.current.scrollHeight;},[thread.messages?.length]);
  const send=()=>{
    const v=(text||"").trim();if(!v||sending)return;
    if(v.length>5000){alert("최대 5000자까지 입력 가능합니다.");return;}
    setSending(true);
    postJson("/api/messages/send",{username:uname,text:v})
      .then(()=>{setText("");load(false);})
      .catch(e=>alert("전송 실패: "+e.message))
      .finally(()=>setSending(false));
  };
  if(loading)return<div style={{padding:40,textAlign:"center"}}><Loading text="불러오는 중..."/></div>;
  const msgs=thread.messages||[];
  return(<div style={{display:"flex",flexDirection:"column",height:"calc(100vh - 48px - 48px - 48px)"}}>
    <div style={{display:"flex",alignItems:"center",padding:"10px 16px",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)"}}>
      <span style={{fontSize:12,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>{"> 1:1 문의 (관리자)"}</span>
      <span style={{fontSize:11,color:"var(--text-secondary)",marginLeft:10}}>메시지 {msgs.length}건</span>
      <div style={{flex:1}}/>
      <button onClick={()=>load(true)} style={{padding:"4px 10px",fontSize:10,borderRadius:4,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>↻</button>
    </div>
    <div ref={listRef} style={{flex:1,overflowY:"auto",padding:"16px",background:"var(--bg-primary)"}}>
      {msgs.length===0&&<div style={{textAlign:"center",color:"var(--text-secondary)",fontSize:12,padding:40}}>
        아직 메시지가 없습니다. 아래에 버그 리포트 / 기능 요청 / 권한 요청 등을 입력해보세요.
      </div>}
      {msgs.map(m=>{const mine=m.from===uname;return(
        <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:10}}>
          <div style={{maxWidth:"75%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
            <div style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>
              {mine?"나":m.from} · {fmtTime(m.created_at)}
            </div>
            <div style={{padding:"8px 12px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:13,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>
              {m.text}
            </div>
          </div>
        </div>);})}
    </div>
    <div style={{padding:"10px 16px",borderTop:"1px solid var(--border)",background:"var(--bg-secondary)"}}>
      <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
        <textarea value={text} onChange={e=>setText(e.target.value)} disabled={sending} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")send();}} placeholder="메시지 입력 (Cmd/Ctrl + Enter 로 전송)" rows={2} style={{flex:1,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
        <button onClick={send} disabled={sending||!text.trim()} style={{padding:"8px 18px",borderRadius:6,border:"none",background:sending||!text.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:12,fontWeight:700,cursor:sending||!text.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>
          {sending?"…":"보내기"}
        </button>
      </div>
      <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:4,textAlign:"right"}}>{text.length} / 5000</div>
    </div>
  </div>);
}

export default function My_Message({user,onMsgUnreadChange}){
  const[subtab,setSubtab]=useState("notices");
  const refreshUnread=()=>{
    if(onMsgUnreadChange)onMsgUnreadChange();
    else if(user?.username){sf("/api/messages/unread?username="+encodeURIComponent(user.username)).then(()=>{}).catch(()=>{});}
  };
  const tS=(a)=>({padding:"9px 16px",fontSize:12,cursor:"pointer",fontWeight:a?700:500,borderBottom:a?"2px solid var(--accent)":"2px solid transparent",color:a?"var(--text-primary)":"var(--text-secondary)",fontFamily:"'JetBrains Mono',monospace"});
  return(<div style={{background:"var(--bg-primary)",minHeight:"calc(100vh - 48px)",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif"}}>
    <div style={{display:"flex",borderBottom:"1px solid var(--border)",padding:"0 16px",background:"var(--bg-secondary)"}}>
      <div style={tS(subtab==="notices")} onClick={()=>setSubtab("notices")}>📢 공지사항</div>
      <div style={tS(subtab==="inquiry")} onClick={()=>setSubtab("inquiry")}>💬 1:1 문의</div>
    </div>
    <div>
      {subtab==="notices"&&<NoticePanel user={user} onAfterRead={refreshUnread}/>}
      {subtab==="inquiry"&&<InquiryPanel user={user} onAfterRead={refreshUnread}/>}
    </div>
  </div>);
}
