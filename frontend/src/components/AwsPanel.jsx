// v8.4.3: AwsPanel — FileBrowser 톱니 와 Admin 에서 공유. 멀티 프로파일 관리.
// 원래 My_Admin.jsx 내부에 정의돼 있던 걸 단위기능 페이지 철학에 맞춰 FB 톱니로
// 이관 (Admin tab 에서는 제거됨). 여기를 단일 source of truth 로 유지.

import { useState, useEffect } from "react";
import Loading from "./Loading";

const sf = (url, o) => fetch(url, o).then(r => {
  if (!r.ok) return r.json().then(d => { throw new Error(d.detail || "HTTP " + r.status); });
  return r.json();
});

export default function AwsPanel({ user, compact = false }) {
  const [data, setData] = useState(null);
  const [selIdx, setSelIdx] = useState(0);
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState("");
  const [newProfile, setNewProfile] = useState("");
  const [secretEdit, setSecretEdit] = useState(false);

  const load = () => sf("/api/s3ingest/aws-config?username=" + encodeURIComponent(user?.username || ""))
    .then(d => { setData(d); setSelIdx(0); })
    .catch(e => setMsg("오류: " + e.message));
  useEffect(() => { load(); }, []); // eslint-disable-line

  useEffect(() => {
    if (!data || !Array.isArray(data.profiles) || !data.profiles[selIdx]) { setForm(null); return; }
    const p = data.profiles[selIdx];
    setForm({
      profile: p.profile || "default",
      aws_access_key_id: p.aws_access_key_id || "",
      aws_secret_access_key: p.has_secret ? (p.aws_secret_access_key_masked || "") : "",
      region: p.region || "",
      output: p.output || "",
      endpoint_url: p.endpoint_url || "",
    });
    setSecretEdit(false);
  }, [data, selIdx]);

  const save = () => {
    if (!form) return;
    const payload = { ...form, username: user?.username || "" };
    if (!secretEdit) payload.aws_secret_access_key = "";
    sf("/api/s3ingest/aws-config/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
      .then(() => { setMsg("저장됨 ✓"); setTimeout(() => setMsg(""), 2000); load(); })
      .catch(e => setMsg("오류: " + e.message));
  };
  const addProfile = () => {
    const v = (newProfile || "").trim();
    if (!v || !/^[a-zA-Z0-9_-]{1,64}$/.test(v)) { setMsg("잘못된 프로파일 이름"); return; }
    if (data && Array.isArray(data.profiles) && data.profiles.some(p => p.profile === v)) { setMsg("프로파일이 이미 존재합니다"); return; }
    const nextProfiles = [...(Array.isArray(data?.profiles) ? data.profiles : []), { profile: v, aws_access_key_id: "", aws_secret_access_key_masked: "", has_secret: false, region: "", output: "", endpoint_url: "" }];
    setData({ ...data, profiles: nextProfiles });
    setSelIdx(nextProfiles.length - 1);
    setNewProfile("");
  };
  const delProfile = () => {
    if (!form) return;
    if (form.profile === "default") { setMsg("'default' 프로파일은 삭제할 수 없습니다"); return; }
    if (!window.confirm(`AWS 프로파일 '${form.profile}' 을(를) 삭제하시겠습니까?`)) return;
    sf("/api/s3ingest/aws-config/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: user?.username || "", profile: form.profile }) })
      .then(() => { setMsg("삭제됨"); load(); })
      .catch(e => setMsg("오류: " + e.message));
  };

  const S = { padding: "7px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "var(--bg-primary)", color: "var(--text-primary)", fontSize: 14, outline: "none", fontFamily: "monospace" };
  const labelS = { fontSize: 14, color: "var(--text-secondary)", marginBottom: 4 };

  if (!data) return <div style={{ padding: 30, textAlign: "center", color: "var(--text-secondary)" }}><Loading text="로딩 중..." /></div>;

  const wrap = compact
    ? { padding: 16 }
    : { background: "var(--bg-secondary)", borderRadius: 10, border: "1px solid var(--border)", padding: 20, maxWidth: 700 };

  return (
    <div style={wrap}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>AWS 설정 (멀티 프로파일)</span>
          <span style={{ fontSize: 14, color: "var(--text-secondary)", marginLeft: 10, fontFamily: "monospace" }}>{data.credentials_path}</span>
        </div>
        {msg && <span style={{ fontSize: 14, color: msg.startsWith("오류") ? "#ef4444" : "#22c55e", fontFamily: "monospace" }}>{msg}</span>}
      </div>

      {!data.aws_available && <div style={{ padding: "8px 12px", borderRadius: 6, background: "rgba(251,191,36,0.1)", border: "1px solid rgba(251,191,36,0.3)", marginBottom: 12, fontSize: 14, color: "#fbbf24" }}>⚠ aws CLI 미설치 — sync 실행은 불가. 자격증명은 저장 가능.</div>}

      {/* Profile selector */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>프로파일:</span>
        {(Array.isArray(data.profiles) ? data.profiles : []).map((p, i) => (
          <span key={p.profile + "_" + i} onClick={() => setSelIdx(i)} style={{ padding: "5px 12px", borderRadius: 5, fontSize: 14, cursor: "pointer", fontWeight: selIdx === i ? 700 : 500, background: selIdx === i ? "var(--accent-glow)" : "var(--bg-primary)", color: selIdx === i ? "var(--accent)" : "var(--text-secondary)", border: "1px solid " + (selIdx === i ? "var(--accent)" : "var(--border)"), fontFamily: "monospace" }}>{p.profile}</span>
        ))}
        <span style={{ color: "var(--border)" }}>|</span>
        <input value={newProfile} onChange={e => setNewProfile(e.target.value)} onKeyDown={e => e.key === "Enter" && addProfile()} placeholder="새 프로파일 이름" style={{ ...S, width: 160, fontSize: 14, padding: "5px 8px" }} />
        <button onClick={addProfile} style={{ padding: "5px 12px", borderRadius: 5, border: "1px solid var(--accent)", background: "transparent", color: "var(--accent)", fontSize: 14, cursor: "pointer" }}>+ 추가</button>
      </div>

      {form && <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px 14px" }}>
        <div style={{ gridColumn: "1 / 3" }}>
          <div style={labelS}>Access Key ID</div>
          <input value={form.aws_access_key_id} onChange={e => setForm(f => ({ ...f, aws_access_key_id: e.target.value }))} placeholder="AKIA... (16-32 uppercase/digits)" style={{ ...S, width: "100%" }} />
        </div>
        <div style={{ gridColumn: "1 / 3" }}>
          <div style={labelS}>Secret Access Key {form.profile !== "default" || secretEdit ? "" : <span style={{ color: "var(--text-secondary)", fontSize: 14 }}> (마스킹됨 — 변경하려면 편집 클릭)</span>}</div>
          <div style={{ display: "flex", gap: 6 }}>
            <input value={form.aws_secret_access_key} disabled={!secretEdit} onChange={e => setForm(f => ({ ...f, aws_secret_access_key: e.target.value }))} placeholder={secretEdit ? "40자 secret" : ""} style={{ ...S, flex: 1, opacity: secretEdit ? 1 : 0.7 }} type={secretEdit ? "text" : "password"} />
            {!secretEdit
              ? <button onClick={() => { setSecretEdit(true); setForm(f => ({ ...f, aws_secret_access_key: "" })); }} style={{ padding: "6px 14px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>편집</button>
              : <button onClick={() => { setSecretEdit(false); load(); }} style={{ padding: "6px 14px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>취소</button>}
          </div>
        </div>
        <div>
          <div style={labelS}>리전</div>
          <input value={form.region} onChange={e => setForm(f => ({ ...f, region: e.target.value }))} placeholder="예: ap-northeast-2" style={{ ...S, width: "100%" }} />
        </div>
        <div>
          <div style={labelS}>Output</div>
          <select value={form.output} onChange={e => setForm(f => ({ ...f, output: e.target.value }))} style={{ ...S, width: "100%" }}>
            <option value="">(기본값)</option>
            <option value="json">json</option>
            <option value="text">text</option>
            <option value="table">table</option>
            <option value="yaml">yaml</option>
          </select>
        </div>
        <div style={{ gridColumn: "1 / 3" }}>
          <div style={labelS}>Endpoint URL (선택, ~/.aws/config 에 저장됨)</div>
          <input value={form.endpoint_url} onChange={e => setForm(f => ({ ...f, endpoint_url: e.target.value }))} placeholder="https://s3.internal.company:9000" style={{ ...S, width: "100%" }} />
        </div>
      </div>}

      <div style={{ display: "flex", gap: 8, marginTop: 18 }}>
        <button onClick={save} style={{ padding: "9px 22px", borderRadius: 5, border: "none", background: "var(--accent)", color: "#fff", fontWeight: 700, fontSize: 14, cursor: "pointer" }}>저장</button>
        {form && form.profile !== "default" && <button onClick={delProfile} style={{ padding: "9px 16px", borderRadius: 5, border: "1px solid #ef4444", background: "transparent", color: "#ef4444", fontSize: 14, cursor: "pointer" }}>프로파일 삭제</button>}
        <div style={{ flex: 1 }} />
        <button onClick={load} style={{ padding: "9px 14px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-secondary)", fontSize: 14, cursor: "pointer" }}>↻ 새로고침</button>
      </div>

      <div style={{ marginTop: 14, padding: 12, background: "var(--bg-primary)", borderRadius: 6, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.6, fontFamily: "monospace" }}>
        <b style={{ color: "var(--accent)" }}># 동작</b><br />
        • 멀티 프로파일 지원 — 각 S3 sync 항목이 원하는 프로파일 지정 가능<br />
        • Access Key + Secret → <code>{data.credentials_path}</code> (mode 600)<br />
        • Region / Output / Endpoint URL → <code>{data.config_path}</code><br />
        • per-item endpoint 는 각 S3 sync 항목의 Endpoint URL 필드 사용
      </div>
    </div>
  );
}
