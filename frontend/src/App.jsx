import { Suspense, useState, useEffect, useRef, Component } from "react";
import My_Login from "./pages/My_Login";
import ComingSoon from "./components/ComingSoon";
import Loading from "./components/Loading";
import Modal from "./components/Modal";
import BrandLogo from "./components/BrandLogo";
import { PAGE_MAP } from "./app/pageRegistry";
import { useFlowShell } from "./app/useFlowShell";
import { sf, postJson } from "./lib/api";

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error("[flow page crash]", error, info); }
  render() {
    if (this.state.error) {
      return (<div style={{padding:"40px 32px",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif",maxWidth:720}}>
        <div style={{fontSize:18,fontWeight:800,color:"#ef4444",marginBottom:8,fontFamily:"'JetBrains Mono',monospace"}}>⚠ 오류가 발생했습니다</div>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:6}}>이 페이지에서 JavaScript 에러가 발생했습니다. 아래 재시도 버튼을 눌러 다시 렌더링하거나 다른 탭으로 이동하세요.</div>
        <div style={{fontSize:14,color:"#fbbf24",marginBottom:16,padding:"8px 12px",borderRadius:6,background:"rgba(251,191,36,0.08)",border:"1px solid rgba(251,191,36,0.25)",fontFamily:"monospace",wordBreak:"break-word"}}>{String(this.state.error?.message || this.state.error)}</div>
        <button onClick={()=>this.setState({error:null})} style={{padding:"8px 18px",borderRadius:5,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,fontWeight:600,cursor:"pointer",marginRight:8}}>↻ 재시도</button>
        <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>콘솔 (F12) 에서 전체 스택 확인</span>
      </div>);
    }
    return this.props.children;
  }
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
        padding:"4px 10px",borderRadius:6,background:open?"var(--bg-hover)":"transparent",fontSize:14,
        color:"var(--text-secondary)"}}>
        <span style={{fontSize:14}}>👤</span>{user.username}
      </div>
      {open && <div style={{position:"fixed",top:52,right:16,background:"var(--bg-secondary)",border:"1px solid var(--border)",
        borderRadius:8,padding:6,minWidth:150,zIndex:9999,boxShadow:"0 4px 12px rgba(0,0,0,0.3)"}}>
        <div style={{padding:"8px 12px",fontSize:14,color:"var(--text-secondary)",borderBottom:"1px solid var(--border)"}}>
          {user.role} | {user.username}
        </div>
        <div onClick={() => { setDark(!dark); localStorage.setItem("hol_dark",String(!dark)); }}
          style={{padding:"8px 12px",fontSize:14,cursor:"pointer",color:"var(--text-primary)"}}>
          {dark ? "☀ 라이트 모드" : "☾ 다크 모드"}
        </div>
        <div onClick={() => { setOpen(false); onChangePw(); }}
          style={{padding:"8px 12px",fontSize:14,cursor:"pointer",color:"var(--text-primary)"}}>
          🔑 비밀번호 변경
        </div>
        <div onClick={onLogout} style={{padding:"8px 12px",fontSize:14,cursor:"pointer",color:"#ef4444"}}>
          ⏻ 로그아웃
        </div>
      </div>}
    </div>
  );
}

const NAV_GROUPS = [
  { id: "home", label: "홈", keys: ["home"], direct: true },
  { id: "data", label: "데이터", keys: ["filebrowser", "dashboard", "splittable", "ettime", "waferlayout"] },
  { id: "work", label: "업무", keys: ["inform", "tracker", "meeting", "calendar"] },
  { id: "agent", label: "에이전트", keys: ["diagnosis"], direct: true },
  { id: "admin", label: "관리", keys: ["tablemap", "admin", "devguide"] },
];

function buildNavGroups(visibleTabs) {
  const byKey = new Map((visibleTabs || []).map(t => [t.key, t]));
  const used = new Set();
  const groups = NAV_GROUPS.map(group => {
    const items = group.keys.map(k => byKey.get(k)).filter(Boolean);
    items.forEach(item => used.add(item.key));
    return { ...group, items };
  }).filter(group => group.items.length > 0);
  const extra = (visibleTabs || []).filter(t => !used.has(t.key));
  if (extra.length) groups.push({ id: "extra", label: "기타", keys: extra.map(t => t.key), items: extra });
  return groups;
}

function NavGroup({ group, activeKey, onNavigate }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const activeItem = group.items.find(t => t.key === activeKey);
  const active = !!activeItem;
  const direct = group.direct && group.items.length === 1;

  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  if (direct) {
    const item = group.items[0];
    return (
      <button
        type="button"
        className={"flow-nav-trigger" + (active ? " is-active" : "")}
        onClick={() => onNavigate(item.key)}
      >
        {item.label}
      </button>
    );
  }

  return (
    <div ref={ref} className="flow-nav-group">
      <button
        type="button"
        className={"flow-nav-trigger" + (active ? " is-active" : "")}
        onClick={() => setOpen(v => !v)}
      >
        <span>{group.label}</span>
        {activeItem && <span className="flow-nav-current">{activeItem.label}</span>}
        <span className="flow-nav-caret">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="flow-nav-menu">
          {group.items.map(item => (
            <button
              key={item.key}
              type="button"
              className={"flow-nav-menu-item" + (item.key === activeKey ? " is-active" : "")}
              onClick={() => { setOpen(false); onNavigate(item.key); }}
            >
              <span>{item.label}</span>
              {(item.badge || item.status === "beta") && <span className="flow-nav-badge">{item.badge || "BETA"}</span>}
            </button>
          ))}
        </div>
      )}
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
    padding: "6px 14px", fontSize: 14, cursor: "pointer",
    fontWeight: tab === k ? 700 : 400,
    borderBottom: tab === k ? "2px solid var(--accent)" : "2px solid transparent",
    color: tab === k ? "var(--accent)" : "var(--text-secondary)"
  }}>{l}</div>;

  return (<>
    <div onClick={() => setOpen(true)} style={{ cursor: "pointer", position: "relative" }} title="문의">
      <span style={{ fontSize: 14 }}>✉️</span>
      {unread > 0 && <span style={{
        position: "absolute", top: -4, right: -6, fontSize: 14, fontWeight: 700,
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
          <div style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)" }}>문의</div>
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
              {thread.length === 0 && <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 14, padding: 40 }}>관리자와의 이전 문의가 없습니다. 아래에 메시지를 입력해 시작하세요.</div>}
              {thread.map((m, i) => (<div key={i} style={{ alignSelf: m.from === user.username ? "flex-end" : "flex-start", maxWidth: "80%", padding: "6px 12px", borderRadius: 8, background: m.from === user.username ? "var(--accent-glow)" : "var(--bg-hover)", border: "1px solid " + (m.from === user.username ? "var(--accent)" : "var(--border)") }}>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 2, fontFamily: "monospace" }}>{m.from === user.username ? "나" : "관리자"} · {(m.created_at || m.ts || "").slice(5, 16).replace("T", " ")}</div>
                <div style={{ fontSize: 14, whiteSpace: "pre-wrap" }}>{m.text || m.body}</div>
              </div>))}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <input value={msg} onChange={e => setMsg(e.target.value)} onKeyDown={e => { if (e.key === "Enter") send(); }} placeholder="관리자에게 보낼 메시지…" style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
              <button onClick={send} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>전송</button>
            </div>
          </div>}
          {tab === "notices" && <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {notices.length === 0 && <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 14, padding: 40 }}>등록된 공지 없음</div>}
            {notices.map(n => (<div key={n.id} style={{ padding: 12, borderRadius: 8, border: "1px solid var(--border)", background: "var(--bg-card)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>{n.title}</div>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace" }}>{(n.created || n.ts || "").slice(0, 16).replace("T", " ")}</div>
              </div>
              <div style={{ fontSize: 14, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{n.body}</div>
              {isAdmin && <div style={{ marginTop: 8 }}><span onClick={() => deleteNotice(n.id)} style={{ fontSize: 14, color: "#ef4444", cursor: "pointer" }}>삭제</span></div>}
            </div>))}
          </div>}
          {tab === "inbox" && isAdmin && <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 12, minHeight: 360 }}>
            <div style={{ borderRight: "1px solid var(--border)", paddingRight: 10, maxHeight: 400, overflow: "auto" }}>
              {adminThreads.length === 0 && <div style={{ fontSize: 14, color: "var(--text-secondary)", padding: 12 }}>받은 문의 없음</div>}
              {adminThreads.map(t => (<div key={t.user} onClick={() => loadAdminThread(t.user)} style={{ padding: "8px 10px", borderRadius: 6, cursor: "pointer", background: selThreadUser === t.user ? "var(--accent-glow)" : "transparent", marginBottom: 2 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 14, fontFamily: "monospace", fontWeight: t.unread_for_admin > 0 ? 700 : 400 }}>{t.user}</span>
                  {t.unread_for_admin > 0 && <span style={{ fontSize: 14, background: "#ef4444", color: "#fff", borderRadius: 8, padding: "1px 6px" }}>{t.unread_for_admin}</span>}
                </div>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.last_body || ""}</div>
              </div>))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ maxHeight: 300, overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-card)", display: "flex", flexDirection: "column", gap: 8 }}>
                {!selThreadUser && <div style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 14, padding: 40 }}>좌측에서 유저 선택</div>}
                {adminThread.map((m, i) => (<div key={i} style={{ alignSelf: m.from === "admin" ? "flex-end" : "flex-start", maxWidth: "85%", padding: "6px 12px", borderRadius: 8, background: m.from === "admin" ? "var(--accent-glow)" : "var(--bg-hover)", border: "1px solid " + (m.from === "admin" ? "var(--accent)" : "var(--border)") }}>
                  <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 2, fontFamily: "monospace" }}>{m.from} · {(m.created_at || m.ts || "").slice(5, 16).replace("T", " ")}</div>
                  <div style={{ fontSize: 14, whiteSpace: "pre-wrap" }}>{m.text || m.body}</div>
                </div>))}
              </div>
              {selThreadUser && <div style={{ display: "flex", gap: 6 }}>
                <input value={replyMsg} onChange={e => setReplyMsg(e.target.value)} onKeyDown={e => { if (e.key === "Enter") reply(); }} placeholder={`${selThreadUser} 에게 답장…`} style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14 }} />
                <button onClick={reply} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>답장</button>
              </div>}
            </div>
          </div>}
          {tab === "compose" && isAdmin && <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <input value={noticeTitle} onChange={e => setNoticeTitle(e.target.value)} placeholder="공지 제목" style={{ padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, fontWeight: 600 }} />
            <textarea value={noticeBody} onChange={e => setNoticeBody(e.target.value)} rows={6} placeholder="공지 내용" style={{ padding: "8px 12px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, resize: "vertical", fontFamily: "inherit" }} />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>공지는 홈 상단 배너에 3일간 표시됩니다.</span>
              <button onClick={postNotice} style={{ padding: "8px 20px", borderRadius: 6, border: "none", background: "var(--accent)", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}>공지 등록</button>
            </div>
            <div style={{ borderTop: "1px dashed var(--border)", paddingTop: 10 }}>
              <div style={{ fontSize: 14, color: "var(--text-secondary)", marginBottom: 6 }}>기존 공지 ({notices.length})</div>
              {notices.map(n => (<div key={n.id} style={{ display: "flex", justifyContent: "space-between", padding: "6px 10px", borderRadius: 5, background: "var(--bg-card)", border: "1px solid var(--border)", marginBottom: 4 }}>
                <span style={{ fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{n.title}</span>
                <span onClick={() => deleteNotice(n.id)} style={{ fontSize: 14, color: "#ef4444", cursor: "pointer", marginLeft: 10 }}>삭제</span>
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
    gap: 10, fontSize: 14
  }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)", flexShrink: 0 }}>📢 {label}</span>
    {title && <span style={{ fontWeight: 700, flexShrink: 0 }}>{title}</span>}
    <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>{body}</span>
    <span style={{ fontSize: 14, color: "var(--text-secondary)", fontFamily: "monospace", flexShrink: 0 }}>by {notice.author || "admin"}</span>
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
        {notifs.length > 0 && <span style={{ position: "absolute", top: -4, right: -6, fontSize: 14, fontWeight: 700,
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
          <span style={{ fontSize: 14, fontWeight: 800, color: "var(--accent)" }}>
            알림 ({notifs.length})
          </span>
          {sel.size > 0 && <button onClick={dismissSel} style={{ fontSize: 14, padding: "3px 8px", borderRadius: 4,
            border: "1px solid var(--accent)", background: "var(--accent-glow)", color: "var(--accent)", cursor: "pointer",
            fontWeight: 600 }}>읽음 처리 ({sel.size})</button>}
        </div>
        <div style={{ maxHeight: 320, overflow: "auto" }}>
          {recent.length === 0 && <div style={{ padding: 24, textAlign: "center", fontSize: 14,
            color: "var(--text-secondary)" }}>알림 없음</div>}
          {recent.map(n => (
            <div key={n.id} style={{ display: "flex", gap: 8, padding: "8px 14px", alignItems: "flex-start",
              borderBottom: "1px solid var(--border)", background: sel.has(n.id) ? "var(--accent-glow)" : "transparent" }}>
              <input type="checkbox" checked={sel.has(n.id)} onChange={() => toggle(n.id)}
                style={{ marginTop: 2, accentColor: "var(--accent)", flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 2 }}>
                  <span style={{ fontSize: 14, fontWeight: 700, color: "#fff", padding: "1px 5px", borderRadius: 3,
                    background: typeColor[n.type] || "#6b7280", textTransform: "uppercase" }}>{n.type}</span>
                  <span style={{ fontSize: 14, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis",
                    whiteSpace: "nowrap" }}>{n.title}</span>
                </div>
                <div style={{ fontSize: 14, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis",
                  whiteSpace: "nowrap" }}>{n.body}</div>
              </div>
              <span style={{ fontSize: 14, color: "var(--text-secondary)", flexShrink: 0, whiteSpace: "nowrap" }}>
                {(n.timestamp || "").slice(11, 16)}
              </span>
            </div>
          ))}
        </div>
        <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)", display: "flex",
          justifyContent: "space-between", alignItems: "center" }}>
          <span onClick={() => { const all = new Set(recent.map(n => n.id)); setSel(prev => prev.size === all.size ? new Set() : all); }}
            style={{ fontSize: 14, color: "var(--accent)", cursor: "pointer" }}>
            {sel.size === recent.length && recent.length > 0 ? "전체 해제" : "전체 선택"}
          </span>
          <span onClick={() => { setOpen(false); onNavigate("admin"); }}
            style={{ fontSize: 14, color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>전체 보기 →</span>
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
    background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"};
  return (
    <Modal open onClose={onClose} title="비밀번호 변경" width={320}>
      <input value={oldPw} onChange={e=>setOldPw(e.target.value)} placeholder="현재 비밀번호" type="password"
        style={{...S,marginBottom:10}} />
      <input value={newPw} onChange={e=>setNewPw(e.target.value)} placeholder="새 비밀번호" type="password"
        style={{...S,marginBottom:12}} onKeyDown={e=>e.key==="Enter"&&submit()} />
      <button onClick={submit} style={{width:"100%",padding:10,borderRadius:6,border:"none",
        background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>변경</button>
      {msg && <div style={{marginTop:8,fontSize:14,textAlign:"center",
        color:msg.includes("변경 완료")?"#22c55e":"#ef4444"}}>{msg}</div>}
    </Modal>
  );
}

export default function App() {
  const {
    user,
    tab,
    dark,
    setDark,
    notifs,
    showPw,
    setShowPw,
    visibleTabs,
    tabInfo,
    handleLogin,
    handleLogout,
    nav,
    refreshNotifications,
  } = useFlowShell();

  if (!user) return <My_Login onLogin={handleLogin} />;

  const Page = PAGE_MAP[tab];
  const navGroups = buildNavGroups(visibleTabs);

  return (
    <div className="flow-app">
      <nav className="flow-nav">
        {/* v8.3.3: nav brand logo — pixel glyph unified with home, compact (2px cell), subtle glow. */}
        <BrandLogo size="nav" onClick={()=>nav("home")} />
        <div className="flow-nav-separator" />
        <div className="flow-nav-groups">
          {navGroups.map(group => (
            <NavGroup key={group.id} group={group} activeKey={tab} onNavigate={nav} />
          ))}
        </div>
        <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:10,flexShrink:0}}>
          <ContactButton user={user} />
          <BellDropdown notifs={notifs} user={user} onDismiss={refreshNotifications} onNavigate={nav} />
          <ProfileMenu user={user} dark={dark} setDark={setDark} onLogout={handleLogout}
            onChangePw={()=>setShowPw(true)} />
        </div>
      </nav>
      <NoticeBanner user={user} />
      <div style={{flex:1,minHeight:0,overflow:tab==="dashboard"?"hidden":"auto"}}>
        {Page ? (
          <ErrorBoundary key={tab}>
            <Suspense fallback={<Loading text="페이지 로딩..." />}>
              <Page onNavigate={nav} user={user} />
            </Suspense>
          </ErrorBoundary>
        ) : <ComingSoon name={tabInfo?.label || tab} />}
      </div>
      {showPw && <PwModal user={user} onClose={()=>setShowPw(false)} />}
      {/* v8.4.9: floating 오렌지 톱니 전면 제거.
          각 페이지는 자체 in-page ⚙️ 아이콘(예: SplitTable 상단 prefix 관리)을 가짐.
          관리자 전역 설정(dashboard refresh, data_roots 등)은 관리자 탭의 해당 서브탭으로 이미 이관됨. */}
    </div>
  );
}
