import { useState, useEffect, useRef, useCallback, Component } from "react";
import My_Login from "./pages/My_Login";
import My_Home from "./pages/My_Home";
import My_FileBrowser from "./pages/My_FileBrowser";
import My_DevGuide from "./pages/My_DevGuide";
import My_Admin from "./pages/My_Admin";
import My_SplitTable from "./pages/My_SplitTable";
import My_Dashboard from "./pages/My_Dashboard";
import My_Tracker from "./pages/My_Tracker";
import My_Inform from "./pages/My_Inform";
import My_Calendar from "./pages/My_Calendar";
import My_Meeting from "./pages/My_Meeting";
import My_TableMap from "./pages/My_TableMap";
import My_ML from "./pages/My_ML";
import ComingSoon from "./components/ComingSoon";
import Modal from "./components/Modal";
import BrandLogo from "./components/BrandLogo";
import { TABS } from "./config";
import { sf, postJson, logActivity } from "./lib/api";

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error("[flow page crash]", error, info); }
  render() {
    if (this.state.error) {
      return (<div style={{padding:"40px 32px",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif",maxWidth:720}}>
        <div style={{fontSize:18,fontWeight:800,color:"#ef4444",marginBottom:8,fontFamily:"'JetBrains Mono',monospace"}}>⚠ 오류가 발생했습니다</div>
        <div style={{fontSize:12,color:"var(--text-secondary)",marginBottom:6}}>이 페이지에서 JavaScript 에러가 발생했습니다. 아래 재시도 버튼을 눌러 다시 렌더링하거나 다른 탭으로 이동하세요.</div>
        <div style={{fontSize:11,color:"#fbbf24",marginBottom:16,padding:"8px 12px",borderRadius:6,background:"rgba(251,191,36,0.08)",border:"1px solid rgba(251,191,36,0.25)",fontFamily:"monospace",wordBreak:"break-word"}}>{String(this.state.error?.message || this.state.error)}</div>
        <button onClick={()=>this.setState({error:null})} style={{padding:"8px 18px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:12,fontWeight:600,cursor:"pointer",marginRight:8}}>↻ 재시도</button>
        <span style={{fontSize:10,color:"var(--text-secondary)",fontFamily:"monospace"}}>콘솔 (F12) 에서 전체 스택 확인</span>
      </div>);
    }
    return this.props.children;
  }
}

const PAGE_MAP = {
  home: My_Home, filebrowser: My_FileBrowser, splittable: My_SplitTable,
  dashboard: My_Dashboard, tracker: My_Tracker, inform: My_Inform, calendar: My_Calendar, meeting: My_Meeting, tablemap: My_TableMap,
  ml: My_ML, devguide: My_DevGuide, admin: My_Admin,
};

const darkV = {"--bg-primary":"#1a1a1a","--bg-secondary":"#262626","--bg-card":"#2a2a2a","--bg-hover":"#333",
  "--bg-tertiary":"#1a1a1a","--text-primary":"#e5e5e5","--text-secondary":"#a3a3a3","--border":"#333",
  "--accent":"#f97316","--accent-dim":"#ea580c","--accent-glow":"rgba(249,115,22,0.15)"};
const lightV = {"--bg-primary":"#fafafa","--bg-secondary":"#fff","--bg-card":"#fff","--bg-hover":"#f5f5f5",
  "--bg-tertiary":"#f5f5f5","--text-primary":"#171717","--text-secondary":"#737373","--border":"#e5e5e5",
  "--accent":"#ea580c","--accent-dim":"#c2410c","--accent-glow":"rgba(234,88,12,0.1)"};

function useIdleLogout(onLogout, timeoutMs = 4 * 3600 * 1000) {
  // v8.4.6: 마지막 활동 = 클릭 / 키입력 / 스크롤 / 터치 + API 호출 (api.js 의 flow:activity 이벤트).
  // 4시간 무활동 시 onLogout 호출 (프론트 localStorage 정리 + /api/auth/logout 으로 토큰 revoke).
  const timer = useRef(null);
  useEffect(() => {
    const reset = () => { clearTimeout(timer.current); timer.current = setTimeout(onLogout, timeoutMs); };
    const winEvents = ["mousedown","keydown","scroll","touchstart"];
    winEvents.forEach(e => window.addEventListener(e, reset));
    window.addEventListener("flow:activity", reset);
    reset();
    return () => {
      clearTimeout(timer.current);
      winEvents.forEach(e => window.removeEventListener(e, reset));
      window.removeEventListener("flow:activity", reset);
    };
  }, [onLogout, timeoutMs]);
}

function ProfileMenu({ user, dark, setDark, onLogout, onChangePw }) {
  const [open, setOpen] = useState(false);
  const ref = useRef();
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, []);
  return (
    <div ref={ref} style={{position:"relative"}}>
      <div onClick={() => setOpen(!open)} style={{cursor:"pointer",display:"flex",alignItems:"center",gap:6,
        padding:"4px 10px",borderRadius:6,background:open?"var(--bg-hover)":"transparent",fontSize:12,
        fontFamily:"monospace",color:"var(--text-secondary)"}}>
        <span style={{fontSize:14}}>👤</span>{user.username}
      </div>
      {open && <div style={{position:"fixed",top:52,right:16,background:"var(--bg-secondary)",border:"1px solid var(--border)",
        borderRadius:8,padding:6,minWidth:150,zIndex:9999,boxShadow:"0 4px 12px rgba(0,0,0,0.3)"}}>
        <div style={{padding:"8px 12px",fontSize:12,color:"var(--text-secondary)",borderBottom:"1px solid var(--border)"}}>
          {user.role} | {user.username}
        </div>
        <div onClick={() => { setDark(!dark); localStorage.setItem("hol_dark",String(!dark)); }}
          style={{padding:"8px 12px",fontSize:12,cursor:"pointer",color:"var(--text-primary)"}}>
          {dark ? "☀ 라이트 모드" : "☾ 다크 모드"}
        </div>
        <div onClick={() => { setOpen(false); onChangePw(); }}
          style={{padding:"8px 12px",fontSize:12,cursor:"pointer",color:"var(--text-primary)"}}>
          🔑 비밀번호 변경
        </div>
        <div onClick={onLogout} style={{padding:"8px 12px",fontSize:12,cursor:"pointer",color:"#ef4444"}}>
          ⏻ 로그아웃
        </div>
      </div>}
    </div>
  );
}

/* v6: Bell dropdown with checkbox dismiss */
// v8.4.4: Contact button + modal (nav bell 옆). 백엔드 /api/messages/* 재사용.
function ContactButton({ user }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("inquiry"); // inquiry | notices | inbox | compose
  const [thread, setThread] = useState([]);
  const [msg, setMsg] = useState("");
  const [notices, setNotices] = useState([]);
  const [unread, setUnread] = useState(0);
  const [adminThreads, setAdminThreads] = useState([]);
  const [selThreadUser, setSelThreadUser] = useState("");
  const [adminThread, setAdminThread] = useState([]);
  const [replyMsg, setReplyMsg] = useState("");
  const [noticeTitle, setNoticeTitle] = useState("");
  const [noticeBody, setNoticeBody] = useState("");
  const isAdmin = user?.role === "admin";
  const listRef = useRef();

  const loadUnread = () => {
    if (!user?.username) return;
    sf(`/api/messages/unread?username=${encodeURIComponent(user.username)}`)
      .then(d => setUnread(d.total || d.unread || 0)).catch(() => {});
  };
  const loadThread = () => {
    if (!user?.username) return;
    sf(`/api/messages/thread?username=${encodeURIComponent(user.username)}`)
      .then(d => { setThread(d.messages || []); setTimeout(() => { if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight; }, 50); })
      .catch(() => {});
  };
  const loadNotices = () => sf("/api/messages/notices").then(d => setNotices(d.notices || [])).catch(() => {});
  const loadAdminThreads = () => {
    if (!isAdmin) return;
    sf(`/api/messages/admin/threads?admin=${encodeURIComponent(user.username)}`)
      .then(d => setAdminThreads(d.threads || [])).catch(() => {});
  };
  const loadAdminThread = (u) => {
    setSelThreadUser(u);
    sf(`/api/messages/admin/thread?admin=${encodeURIComponent(user.username)}&user=${encodeURIComponent(u)}`)
      .then(d => setAdminThread(d.messages || [])).catch(() => {});
  };

  useEffect(() => {
    loadUnread();
    const t = setInterval(loadUnread, 45000);
    return () => clearInterval(t);
  }, [user?.username]);

  useEffect(() => {
    if (!open) return;
    if (tab === "inquiry") loadThread();
    if (tab === "notices") loadNotices();
    if (tab === "inbox") loadAdminThreads();
    if (tab === "compose") loadNotices();
  }, [open, tab]);

  const send = () => {
    const t = msg.trim(); if (!t) return;
    postJson("/api/messages/send", { username: user.username, text: t })
      .then(() => { setMsg(""); loadThread(); loadUnread(); })
      .catch(e => alert(e?.message || "전송 실패"));
  };
  const reply = () => {
    const t = replyMsg.trim(); if (!t || !selThreadUser) return;
    postJson("/api/messages/admin/reply", { admin: user.username, to_user: selThreadUser, text: t })
      .then(() => { setReplyMsg(""); loadAdminThread(selThreadUser); loadAdminThreads(); })
      .catch(e => alert(e?.message || "답장 실패"));
  };
  const postNotice = () => {
    if (!noticeTitle.trim() && !noticeBody.trim()) return;
    postJson("/api/messages/admin/notice_create", { author: user.username, title: noticeTitle.trim(), body: noticeBody.trim() })
      .then(() => { setNoticeTitle(""); setNoticeBody(""); loadNotices(); alert("공지 등록됨"); })
      .catch(e => alert(e?.message || "공지 등록 실패"));
  };
  const deleteNotice = (id) => {
    if (!confirm("삭제?")) return;
    postJson("/api/messages/admin/notice_delete", { admin: user.username, id }).then(loadNotices);
  };

  const tabBtn = (k, l) => <div key={k} onClick={() => setTab(k)} style={{
    padding: "6px 14px", fontSize: 11, fontFamily: "monospace", cursor: "pointer",
    fontWeight: tab === k ? 700 : 400,
    borderBottom: tab === k ? "2px solid var(--accent)" : "2px solid transparent",
    color: tab === k ? "var(--accent)" : "var(--text-secondary)"
  }}>{l}</div>;

  return (<>
    <div onClick={() => setOpen(true)} style={{ cursor: "pointer", position: "relative" }} title="Contact">
      <span style={{ fontSize: 14 }}>✉️</span>
      {unread > 0 && <span style={{
        position: "absolute", top: -4, right: -6, fontSize: 9, fontWeight: 700,
        background: "#3b82f6", color: "#fff", borderRadius: "50%", minWidth: 14, height: 14,
        display: "flex", alignItems: "center", justifyContent: "center", padding: "0 2px"
      }}>{unread > 99 ? "99+" : unread}</span>}
    </div>
    {open && <div style={{
      position: "fixed", inset: 0, zIndex: 9998, background: "rgba(0,0,0,0.55)",
      display: "flex", alignItems: "center", justifyContent: "center"
    }} onClick={() => setOpen(false)}>
      <div onClick={e => e.stopPropagation()} style={{
        width: "92%", maxWidth: 680, maxHeight: "80vh", background: "var(--bg-secondary)",
        borderRadius: 10, border: "1px solid var(--border)", overflow: "hidden",
        display: "flex", flexDirection: "column"
      }}>
        <div style={{ padding: "12px 18px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ fontSize: 14, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>✉ Contact</div>
          <span onClick={() => setOpen(false)} style={{ cursor: "pointer", fontSize: 18 }}>✕</span>
        </div>
        <div style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--border)", padding: "0 18px" }}>
          {tabBtn("inquiry", "📨 내 문의")}
          {tabBtn("notices", "📢 공지")}
          {isAdmin && tabBtn("inbox", "📥 받은 문의")}
          {isAdmin && tabBtn("compose", "✍ 공지 작성")}
        </div>
        <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
          {tab === "inquiry" && <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div ref={listRef} style={{ minHeight: 260, maxHeight: 360, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-card)", display: "flex", flexDirection: "column", gap: 8 }}>
              {thread.length === 0 && <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 11, padding: 40 }}>관리자와의 이전 문의가 없습니다. 아래에 메시지를 입력해 시작하세요.</div>}
              {thread.map((m, i) => (<div key={i} style={{ alignSelf: m.from === user.username ? "flex-end" : "flex-start", maxWidth: "80%", padding: "6px 12px", borderRadius: 8, background: m.from === user.username ? "var(--accent-glow)" : "var(--bg-hover)", border: "1px solid " + (m.from === user.username ? "var(--accent)" : "var(--border)") }}>
                <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 2, fontFamily: "monospace" }}>{m.from === user.username ? "나" : "관리자"} · {(m.created_at || m.ts || "").slice(5, 16).replace("T", " ")}</div>
                <div style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{m.text || m.body}</div>
              </div>))}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <input value={msg} onChange={e => setMsg(e.target.value)} onKeyDown={e => { if (e.key === "Enter") send(); }} placeholder="관리자에게 보낼 메시지…" style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }} />
              <button onClick={send} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>전송</button>
            </div>
          </div>}
          {tab === "notices" && <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {notices.length === 0 && <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 11, padding: 40 }}>등록된 공지 없음</div>}
            {notices.map(n => (<div key={n.id} style={{ padding: 12, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)" }}>{n.title}</div>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace" }}>{(n.created || n.ts || "").slice(0, 16).replace("T", " ")}</div>
              </div>
              <div style={{ fontSize: 12, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{n.body}</div>
              {isAdmin && <div style={{ marginTop: 8 }}><span onClick={() => deleteNotice(n.id)} style={{ fontSize: 10, color: "#ef4444", cursor: "pointer" }}>삭제</span></div>}
            </div>))}
          </div>}
          {tab === "inbox" && isAdmin && <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 12, minHeight: 360 }}>
            <div style={{ borderRight: "1px solid var(--border)", paddingRight: 10, maxHeight: 400, overflow: "auto" }}>
              {adminThreads.length === 0 && <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: 12 }}>받은 문의 없음</div>}
              {adminThreads.map(t => (<div key={t.user} onClick={() => loadAdminThread(t.user)} style={{ padding: "8px 10px", borderRadius: 6, cursor: "pointer", background: selThreadUser === t.user ? "var(--accent-glow)" : "transparent", marginBottom: 2 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 11, fontFamily: "monospace", fontWeight: t.unread_for_admin > 0 ? 700 : 400 }}>{t.user}</span>
                  {t.unread_for_admin > 0 && <span style={{ fontSize: 9, background: "#ef4444", color: "#fff", borderRadius: 8, padding: "1px 6px" }}>{t.unread_for_admin}</span>}
                </div>
                <div style={{ fontSize: 9, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.last_body || ""}</div>
              </div>))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ maxHeight: 300, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-card)", display: "flex", flexDirection: "column", gap: 8 }}>
                {!selThreadUser && <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 11, padding: 40 }}>좌측에서 유저 선택</div>}
                {adminThread.map((m, i) => (<div key={i} style={{ alignSelf: m.from === "admin" ? "flex-end" : "flex-start", maxWidth: "85%", padding: "6px 12px", borderRadius: 8, background: m.from === "admin" ? "var(--accent-glow)" : "var(--bg-hover)", border: "1px solid " + (m.from === "admin" ? "var(--accent)" : "var(--border)") }}>
                  <div style={{ fontSize: 9, color: "var(--text-secondary)", marginBottom: 2, fontFamily: "monospace" }}>{m.from} · {(m.created_at || m.ts || "").slice(5, 16).replace("T", " ")}</div>
                  <div style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{m.text || m.body}</div>
                </div>))}
              </div>
              {selThreadUser && <div style={{ display: "flex", gap: 6 }}>
                <input value={replyMsg} onChange={e => setReplyMsg(e.target.value)} onKeyDown={e => { if (e.key === "Enter") reply(); }} placeholder={`${selThreadUser} 에게 답장…`} style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12 }} />
                <button onClick={reply} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>답장</button>
              </div>}
            </div>
          </div>}
          {tab === "compose" && isAdmin && <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <input value={noticeTitle} onChange={e => setNoticeTitle(e.target.value)} placeholder="공지 제목" style={{ padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 13, fontWeight: 600 }} />
            <textarea value={noticeBody} onChange={e => setNoticeBody(e.target.value)} rows={6} placeholder="공지 내용" style={{ padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, resize: "vertical", fontFamily: "inherit" }} />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>공지는 홈 상단 배너에 3일간 표시됩니다.</span>
              <button onClick={postNotice} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>공지 등록</button>
            </div>
            <div style={{ borderTop: "1px dashed var(--border)", paddingTop: 10 }}>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6 }}>기존 공지 ({notices.length})</div>
              {notices.map(n => (<div key={n.id} style={{ display: "flex", justifyContent: "space-between", padding: "6px 10px", borderRadius: 5, background: "var(--bg-card)", border: "1px solid var(--border)", marginBottom: 4 }}>
                <span style={{ fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{n.title}</span>
                <span onClick={() => deleteNotice(n.id)} style={{ fontSize: 10, color: "#ef4444", cursor: "pointer", marginLeft: 10 }}>삭제</span>
              </div>))}
            </div>
          </div>}
        </div>
      </div>
    </div>}
  </>);
}

// v8.4.5: Nav 아래 공지 배너 — 최신 1개, 3일 TTL, dismissible. 포맷: "📢 M월 D일 — <title> <body>"
function NoticeBanner({ user }) {
  const [notice, setNotice] = useState(null);
  const [dismissed, setDismissed] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem("flow_notice_dismiss") || "[]")); }
    catch { return new Set(); }
  });
  useEffect(() => {
    if (!user?.username) return;
    const load = () => sf("/api/messages/notices").then(d => {
      const list = d.notices || [];
      const now = Date.now();
      // 3-day TTL filter. backend 는 created_at 필드.
      const fresh = list.filter(n => {
        const ts = new Date(n.created_at || n.created || n.ts || 0).getTime();
        return ts > 0 && (now - ts) < 3 * 86400000 && !dismissed.has(n.id);
      });
      // 가장 최신 (created_at 내림차순)
      fresh.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
      setNotice(fresh[0] || null);
    }).catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [user?.username, dismissed]);
  const dismiss = () => {
    if (!notice) return;
    const next = new Set(dismissed); next.add(notice.id);
    localStorage.setItem("flow_notice_dismiss", JSON.stringify([...next]));
    setDismissed(next);
  };
  if (!notice) return null;
  const dt = new Date(notice.created_at || notice.created || notice.ts || Date.now());
  const label = `${dt.getMonth() + 1}월 ${dt.getDate()}일`;
  const title = (notice.title || "").trim();
  const body = (notice.body || "").trim();
  return (<div style={{
    padding: "6px 18px", background: "linear-gradient(90deg, rgba(249,115,22,0.15), rgba(249,115,22,0.05))",
    borderBottom: "1px solid rgba(249,115,22,0.35)", display: "flex", alignItems: "center",
    gap: 10, fontSize: 12
  }}>
    <span style={{ fontSize: 11, fontWeight: 700, color: "var(--accent)", fontFamily: "monospace", flexShrink: 0 }}>📢 {label}</span>
    {title && <span style={{ fontWeight: 700, flexShrink: 0 }}>{title}</span>}
    <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>{body}</span>
    <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "monospace", flexShrink: 0 }}>by {notice.author || "admin"}</span>
    <span onClick={dismiss} title="닫기 (3일간 숨김)" style={{ cursor: "pointer", fontSize: 14, color: "var(--text-secondary)", flexShrink: 0 }}>✕</span>
  </div>);
}

function BellDropdown({ notifs, user, onDismiss, onNavigate }) {
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(new Set());
  const ref = useRef();
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, []);
  const toggle = (id) => { if(!id) return; setSel(prev => { const s = new Set(prev); s.has(id) ? s.delete(id) : s.add(id); return s; }); };
  const dismissSel = () => {
    const ids = [...sel].filter(Boolean);
    if (!ids.length) { alert("선택된 항목이 없습니다"); return; }
    // Mark as read — removes from bell count but keeps history in Admin
    postJson("/api/admin/mark-read-batch", { username: user.username, ids })
      .then(() => { setSel(new Set()); onDismiss(); window.dispatchEvent(new CustomEvent("hol:notif-refresh")); })
      .catch(e => alert("실패: " + (e.message || "알 수 없는 오류")));
  };
  const recent = notifs.slice(-8).reverse();
  const typeColor = { approval: "#f59e0b", message: "#3b82f6", info: "#6b7280" };
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div onClick={() => setOpen(!open)} style={{ cursor: "pointer", position: "relative" }}>
        <span style={{ fontSize: 14 }}>🔔</span>
        {notifs.length > 0 && <span style={{ position: "absolute", top: -4, right: -6, fontSize: 9, fontWeight: 700,
          background: "#ef4444", color: "#fff", borderRadius: "50%", minWidth: 14, height: 14, display: "flex",
          alignItems: "center", justifyContent: "center", padding: "0 2px" }}>
          {notifs.length > 99 ? "99+" : notifs.length}
        </span>}
      </div>
      {open && <div style={{ position: "fixed", top: 52, right: 80, width: 340, background: "var(--bg-secondary)",
        border: "1px solid var(--border)", borderRadius: 10, zIndex: 9999, boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
        overflow: "hidden" }}>
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex",
          justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: "var(--accent)" }}>
            알림 ({notifs.length})
          </span>
          {sel.size > 0 && <button onClick={dismissSel} style={{ fontSize: 10, padding: "3px 8px", borderRadius: 4,
            border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", cursor: "pointer",
            fontWeight: 600 }}>읽음 처리 ({sel.size})</button>}
        </div>
        <div style={{ maxHeight: 320, overflow: "auto" }}>
          {recent.length === 0 && <div style={{ padding: 24, textAlign: "center", fontSize: 11,
            color: "var(--text-secondary)" }}>알림 없음</div>}
          {recent.map(n => (
            <div key={n.id} style={{ display: "flex", gap: 8, padding: "8px 14px", alignItems: "flex-start",
              borderBottom: "1px solid var(--border)", background: sel.has(n.id) ? "var(--accent-glow)" : "transparent" }}>
              <input type="checkbox" checked={sel.has(n.id)} onChange={() => toggle(n.id)}
                style={{ marginTop: 2, accentColor: "var(--accent)", flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 2 }}>
                  <span style={{ fontSize: 8, fontWeight: 700, color: "#fff", padding: "1px 5px", borderRadius: 3,
                    background: typeColor[n.type] || "#6b7280", textTransform: "uppercase" }}>{n.type}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis",
                    whiteSpace: "nowrap" }}>{n.title}</span>
                </div>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis",
                  whiteSpace: "nowrap" }}>{n.body}</div>
              </div>
              <span style={{ fontSize: 9, color: "var(--text-secondary)", flexShrink: 0, whiteSpace: "nowrap" }}>
                {(n.timestamp || "").slice(11, 16)}
              </span>
            </div>
          ))}
        </div>
        <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)", display: "flex",
          justifyContent: "space-between", alignItems: "center" }}>
          <span onClick={() => { const all = new Set(recent.map(n => n.id)); setSel(prev => prev.size === all.size ? new Set() : all); }}
            style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer" }}>
            {sel.size === recent.length && recent.length > 0 ? "전체 해제" : "전체 선택"}
          </span>
          <span onClick={() => { setOpen(false); onNavigate("admin"); }}
            style={{ fontSize: 10, color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>전체 보기 →</span>
        </div>
      </div>}
    </div>
  );
}

function PwModal({ user, onClose }) {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [msg, setMsg] = useState("");
  const submit = () => {
    postJson("/api/auth/change-password", {
      username: user.username, old_password: oldPw, new_password: newPw,
    }).then(() => { setMsg("변경 완료!"); setTimeout(onClose, 1000); })
      .catch(e => setMsg(e.message));
  };
  const S = {width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",
    background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:13,outline:"none"};
  return (
    <Modal open onClose={onClose} title="비밀번호 변경" width={320}>
      <input value={oldPw} onChange={e=>setOldPw(e.target.value)} placeholder="현재 비밀번호" type="password"
        style={{...S,marginBottom:10}} />
      <input value={newPw} onChange={e=>setNewPw(e.target.value)} placeholder="새 비밀번호" type="password"
        style={{...S,marginBottom:12}} onKeyDown={e=>e.key==="Enter"&&submit()} />
      <button onClick={submit} style={{width:"100%",padding:10,borderRadius:6,border:"none",
        background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>변경</button>
      {msg && <div style={{marginTop:8,fontSize:12,textAlign:"center",
        color:msg.includes("변경 완료")?"#22c55e":"#ef4444"}}>{msg}</div>}
    </Modal>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [tab, setTab] = useState("home");
  const [dark, setDark] = useState(true);
  const [notifs, setNotifs] = useState([]);
  const [userTabs, setUserTabs] = useState("__all__");
  const [showPw, setShowPw] = useState(false);

  const handleLogout = () => {
    // v8.4.6: 서버 토큰도 revoke (best-effort).
    try { postJson("/api/auth/logout", {}).catch(() => {}); } catch (_) {}
    setUser(null);
    localStorage.removeItem("hol_user");
  };
  useIdleLogout(handleLogout);

  useEffect(() => {
    const s = localStorage.getItem("hol_user");
    if (s) {
      try {
        const parsed = JSON.parse(s);
        // v8.4.6: 저장된 세션에 토큰이 없으면(구버전) 강제 로그아웃 → 로그인 유도
        if (parsed && parsed.token) setUser(parsed);
        else localStorage.removeItem("hol_user");
      } catch (_) { localStorage.removeItem("hol_user"); }
    }
    setDark(localStorage.getItem("hol_dark") !== "false");
    // v8.4.6: api.js 의 401 핸들러에서 발행되는 session-expired 수신 → 로그인 페이지로
    const onExpire = () => { setUser(null); };
    window.addEventListener("flow:session-expired", onExpire);
    return () => window.removeEventListener("flow:session-expired", onExpire);
  }, []);
  useEffect(() => {
    Object.entries(dark?darkV:lightV).forEach(([k,v])=>document.documentElement.style.setProperty(k,v));
  }, [dark]);

  useEffect(() => {
    if (!user) return;
    sf("/api/session/load?username="+user.username).then(d=>{if(d.last_tab)setTab(d.last_tab);}).catch(()=>{});
    if (user.tabs) setUserTabs(user.tabs);
    else sf("/api/admin/user-tabs?username="+user.username)
      .then(d=>setUserTabs(d.tabs||"filebrowser,dashboard,splittable")).catch(()=>{});
  }, [user]);
  useEffect(() => {
    if (!user) return;
    postJson("/api/session/save", { username:user.username, last_tab:tab }).catch(()=>{});
  }, [tab, user]);
  useEffect(() => {
    if (!user) return;
    const poll=()=>sf("/api/admin/my-notifications?username="+user.username)
      .then(d=>setNotifs(d.notifications||[])).catch(()=>{});
    poll();
    const iv=setInterval(poll,30000);
    // v8.2.0: listen for read/dismiss events from Admin tab → refresh bell immediately
    const onRefresh=()=>poll();
    window.addEventListener("hol:notif-refresh",onRefresh);
    return()=>{clearInterval(iv);window.removeEventListener("hol:notif-refresh",onRefresh);};
  }, [user]);
  const canAccess = (tabKey) => {
    if (tabKey === "home") return true;
    // v8.5.1: inform (wafer 인폼 스레드) 는 모든 유저에게 기본 노출.
    if (tabKey === "inform") return true;
    // v8.6.0: calendar (변경점 달력) 도 모든 유저에게 기본 노출.
    if (tabKey === "calendar") return true;
    // v8.7.2: meeting (회의관리) 도 모든 유저에게 기본 노출.
    if (tabKey === "meeting") return true;
    if (userTabs === "__all__") return true;
    const t = TABS.find(t=>t.key===tabKey);
    if (t?.adminOnly && user?.role !== "admin") return false;
    return userTabs.split(",").includes(tabKey);
  };

  const nav = (k) => {
    if (!canAccess(k) && k !== "admin") return;
    setTab(k);
    if (user) logActivity(user.username, "nav:"+k);
  };
  const handleLogin = (u) => {
    setUser(u); localStorage.setItem("hol_user",JSON.stringify(u));
    if(u.tabs) setUserTabs(u.tabs);
  };

  if (!user) return <My_Login onLogin={handleLogin} />;

  const Page = PAGE_MAP[tab];
  const tabInfo = TABS.find(t=>t.key===tab);
  const visibleTabs = TABS.filter(t => t.key !== "home" && canAccess(t.key));

  return (
    <div style={{minHeight:"100vh",background:"var(--bg-primary)"}}>
      <nav style={{display:"flex",alignItems:"center",height:48,padding:"0 16px",background:"var(--bg-secondary)",
        borderBottom:"1px solid var(--border)",gap:2,overflowX:"auto",whiteSpace:"nowrap"}}>
        {/* v8.3.3: nav brand logo — pixel glyph unified with home, compact (2px cell), subtle glow. */}
        <BrandLogo size="nav" onClick={()=>nav("home")} />
        <div style={{width:1,height:20,background:"var(--border)",marginRight:6,flexShrink:0}} />
        {visibleTabs.map(t=>(
          <div key={t.key} onClick={()=>nav(t.key)} style={{padding:"5px 10px",borderRadius:5,cursor:"pointer",
            fontSize:11,flexShrink:0,fontFamily:"'JetBrains Mono',monospace",position:"relative",
            background:tab===t.key?"var(--accent-glow)":"transparent",
            color:tab===t.key?"var(--accent)":"var(--text-secondary)",
            fontWeight:tab===t.key?600:400}}>{t.icon&&<span style={{marginRight:3}}>{t.icon}</span>}{t.label}
          </div>
        ))}
        <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:10,flexShrink:0}}>
          <ContactButton user={user} />
          <BellDropdown notifs={notifs} user={user} onDismiss={() => {
            sf("/api/admin/my-notifications?username="+user.username)
              .then(d=>setNotifs(d.notifications||[])).catch(()=>{});
          }} onNavigate={nav} />
          <ProfileMenu user={user} dark={dark} setDark={setDark} onLogout={handleLogout}
            onChangePw={()=>setShowPw(true)} />
        </div>
      </nav>
      <NoticeBanner user={user} />
      {Page ? <ErrorBoundary key={tab}><Page onNavigate={nav} user={user} /></ErrorBoundary> : <ComingSoon name={tabInfo?.label || tab} />}
      {showPw && <PwModal user={user} onClose={()=>setShowPw(false)} />}
      {/* v8.4.9: floating 오렌지 톱니 전면 제거.
          각 페이지는 자체 in-page ⚙️ 아이콘(예: SplitTable 상단 prefix 관리)을 가짐.
          관리자 전역 설정(dashboard refresh, data_roots 등)은 관리자 탭의 해당 서브탭으로 이미 이관됨. */}
    </div>
  );
}

// v8.4.4: Page-contextual floating gear. FB 탭에서는 FB 전용 (AWS/Sync/Logs),
// 그 외에는 전역 admin_settings (대시보드 refresh 등).
function AdminSettingsGear({ user, currentTab }) {
  const [open, setOpen] = useState(false);
  const isFB = currentTab === "filebrowser";
  const btnS = { position: "fixed", right: 18, bottom: 18, width: 44, height: 44, borderRadius: "50%", background: "var(--accent)", color: "#fff", border: "none", boxShadow: "0 4px 12px rgba(0,0,0,0.3)", cursor: "pointer", fontSize: 20, zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" };
  const panelS = { position: "fixed", right: 18, bottom: 72, width: isFB ? 460 : 320, maxHeight: "75vh", overflow: "auto", padding: 16, borderRadius: 10, background: "var(--bg-secondary)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.35)", zIndex: 1001, fontFamily: "'JetBrains Mono',monospace" };
  return (<>
    <button onClick={() => setOpen(!open)} style={btnS} title={isFB ? "파일탐색기 설정" : "관리자 설정"}>⚙</button>
    {open && (<div style={panelS} onClick={e => e.stopPropagation()}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)" }}>{isFB ? "> filebrowser_settings" : "> admin_settings"}</span>
        <span onClick={() => setOpen(false)} style={{ cursor: "pointer", fontSize: 14, color: "var(--text-secondary)" }}>✕</span>
      </div>
      {isFB ? <FBSettingsContent user={user} /> : <AdminGlobalSettingsContent />}
    </div>)}
  </>);
}

function AdminGlobalSettingsContent() {
  const [s, setS] = useState({ dashboard_refresh_minutes: 10, dashboard_bg_refresh_minutes: 10 });
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    sf("/api/admin/settings").then(d => { if (d) setS({ dashboard_refresh_minutes: d.dashboard_refresh_minutes || 10, dashboard_bg_refresh_minutes: d.dashboard_bg_refresh_minutes || 10 }); }).catch(() => {});
  }, []);
  const save = () => {
    setSaving(true); setMsg("");
    sf("/api/admin/settings/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s) })
      .then(() => { setMsg("저장됨 ✓"); setTimeout(() => setMsg(""), 1500); })
      .catch(e => setMsg("오류: " + e.message)).finally(() => setSaving(false));
  };
  const inputS = { width: 70, padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 12, outline: "none", fontFamily: "inherit" };
  return (<>
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>대시보드 자동 새로고침</div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <input type="number" min="1" max="240" value={s.dashboard_refresh_minutes} onChange={e => setS({ ...s, dashboard_refresh_minutes: parseInt(e.target.value) || 10 })} style={inputS} />
        <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>분 (1~240)</span>
      </div>
    </div>
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 4 }}>대시보드 백그라운드 재계산</div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <input type="number" min="1" max="240" value={s.dashboard_bg_refresh_minutes} onChange={e => setS({ ...s, dashboard_bg_refresh_minutes: parseInt(e.target.value) || 10 })} style={inputS} />
        <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>분</span>
      </div>
    </div>
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <button onClick={save} disabled={saving} style={{ padding: "6px 14px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontSize: 11, fontWeight: 600, cursor: saving ? "wait" : "pointer", fontFamily: "inherit" }}>{saving ? "..." : "저장"}</button>
      {msg && <span style={{ fontSize: 10, color: msg.startsWith("오류") ? "#ef4444" : "#22c55e" }}>{msg}</span>}
    </div>
  </>);
}

function FBSettingsContent({ user }) {
  const [sub, setSub] = useState("aws");  // aws | sync | logs
  const [syncItems, setSyncItems] = useState([]);
  const [logs, setLogs] = useState([]);
  const [loadingItems, setLoadingItems] = useState(false);
  const [schedule, setSchedule] = useState({ enabled: false, interval_minutes: 60 });
  useEffect(() => {
    if (sub === "sync") { setLoadingItems(true);
      sf(`/api/s3ingest/items?username=${encodeURIComponent(user?.username||"")}`)
        .then(d => setSyncItems(d.items || [])).catch(() => {}).finally(() => setLoadingItems(false));
      sf(`/api/s3ingest/schedule?username=${encodeURIComponent(user?.username||"")}`)
        .then(d => setSchedule(d || schedule)).catch(() => {}); }
    if (sub === "logs")
      sf(`/api/s3ingest/history?username=${encodeURIComponent(user?.username||"")}&limit=40`)
        .then(d => setLogs(d.entries || d.history || d.logs || [])).catch(() => {});
  }, [sub]);
  const saveSchedule = () => postJson("/api/s3ingest/schedule/save", { ...schedule, username: user?.username || "" }).then(() => alert("저장됨")).catch(e => alert(e.message));
  const pushOne = (item) => {
    if (!confirm(`${item.name || item.id} 을(를) S3 로 업로드 하시겠습니까?`)) return;
    postJson("/api/s3ingest/push", { id: item.id, username: user?.username || "" }).then(() => alert("업로드 실행됨")).catch(e => alert(e.message));
  };
  const pullOne = (item) => postJson("/api/s3ingest/run", { id: item.id, username: user?.username || "" }).then(() => alert("pull 실행됨")).catch(e => alert(e.message));
  const tabBtn = (k, l) => <div key={k} onClick={() => setSub(k)} style={{ padding: "5px 12px", fontSize: 10, cursor: "pointer", fontWeight: sub === k ? 700 : 400, borderBottom: sub === k ? "2px solid var(--accent)" : "2px solid transparent", color: sub === k ? "var(--accent)" : "var(--text-secondary)" }}>{l}</div>;
  return (<>
    <div style={{ display: "flex", gap: 2, borderBottom: "1px solid var(--border)", marginBottom: 10 }}>
      {tabBtn("aws", "☁ AWS Configure")}
      {tabBtn("sync", "↕ S3 Sync")}
      {tabBtn("logs", "📜 로그")}
    </div>
    {sub === "aws" && <div style={{ fontSize: 11 }}>
      <AwsPanelInline user={user} />
    </div>}
    {sub === "sync" && <div style={{ fontSize: 11 }}>
      <div style={{ padding: "8px 10px", marginBottom: 10, background: "var(--bg-card)", borderRadius: 6, border: "1px solid var(--border)" }}>
        <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent)", marginBottom: 6 }}>주기 설정</div>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
          <input type="checkbox" checked={schedule.enabled} onChange={e => setSchedule({ ...schedule, enabled: e.target.checked })} style={{ accentColor: "var(--accent)" }} />
          자동 동기화
          <input type="number" min="5" max="1440" value={schedule.interval_minutes} onChange={e => setSchedule({ ...schedule, interval_minutes: parseInt(e.target.value) || 60 })} style={{ width: 60, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 11, marginLeft: 8 }} />
          <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>분마다</span>
          <button onClick={saveSchedule} style={{ marginLeft: "auto", padding: "3px 10px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", fontSize: 10, cursor: "pointer" }}>저장</button>
        </label>
      </div>
      <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent)", marginBottom: 6 }}>테이블별 Sync (양방향)</div>
      {loadingItems ? <div style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)" }}>로딩...</div>
        : syncItems.length === 0 ? <div style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)", fontSize: 10 }}>동기화 대상 테이블 없음. AWS Configure 후 sync 설정.</div>
          : <div style={{ maxHeight: 240, overflow: "auto" }}>{syncItems.map(it => (
            <div key={it.name} style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 8px", borderBottom: "1px solid var(--border)", fontSize: 10 }}>
              <span style={{ flex: 1, fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.name}</span>
              <span style={{ fontSize: 9, color: "var(--text-secondary)" }}>{it.last_synced ? it.last_synced.slice(5, 16).replace("T", " ") : "—"}</span>
              <button onClick={() => pullOne(it)} style={{ padding: "2px 8px", borderRadius: 3, border: "1px solid #3b82f6", background: "transparent", color: "#3b82f6", fontSize: 9, cursor: "pointer" }} title="S3 → local">↓</button>
              <button onClick={() => pushOne(it)} style={{ padding: "2px 8px", borderRadius: 3, border: "1px solid #10b981", background: "transparent", color: "#10b981", fontSize: 9, cursor: "pointer" }} title="local → S3">↑</button>
            </div>))}</div>}
    </div>}
    {sub === "logs" && <div style={{ fontSize: 10, maxHeight: 380, overflow: "auto" }}>
      {logs.length === 0 ? <div style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)" }}>로그 없음</div>
        : logs.map((l, i) => (<div key={i} style={{ padding: "4px 8px", borderBottom: "1px solid var(--border)", fontFamily: "monospace", display: "flex", gap: 6 }}>
          <span style={{ color: "var(--text-secondary)", flexShrink: 0 }}>{(l.ts || "").slice(5, 16).replace("T", " ")}</span>
          <span style={{ color: l.level === "error" ? "#ef4444" : l.level === "warn" ? "#f59e0b" : "#22c55e", fontWeight: 700, flexShrink: 0, width: 40 }}>{l.level || "info"}</span>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={l.message}>{l.message}</span>
        </div>))}
    </div>}
  </>);
}

// Wrapper to lazy-import AwsPanel without circular deps
function AwsPanelInline({ user }) {
  const [Comp, setComp] = useState(null);
  useEffect(() => {
    import("./components/AwsPanel").then(m => setComp(() => m.default)).catch(() => {});
  }, []);
  if (!Comp) return <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: 12 }}>로딩…</div>;
  return <Comp user={user} compact />;
}
