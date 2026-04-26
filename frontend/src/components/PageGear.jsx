/* PageGear.jsx v8.8.3 — 페이지별 공용 톱니(⚙️) 설정 패널.
 * v8.8.3: FileBrowser S3 ⚙️ 스타일과 100% 통일 (40px · ⚙️ emoji · 우하단 default).
 *   - 이전 v8.8.1 은 36px + ⚙ (no emoji) 으로 파일탐색기와 어긋남. 전 탭 한 방에 수렴.
 */
import { useEffect, useRef, useState } from "react";

/*
 * 사용법:
 *   <PageGear title="대시보드 설정" canEdit={isAdmin}>
 *     <div>...panel contents...</div>
 *   </PageGear>
 *
 * props:
 *   - title:   string. drawer 제목.
 *   - children:React. 패널 내용 (설정 폼).
 *   - canEdit: boolean. false 면 disabled 배지.
 *   - position: "bottom-right" (default, v8.8.3 통일) | "top-right" | "bottom-left" | "inline".
 *
 * 특징:
 *   - 버튼: 40 x 40, ⚙️ emoji, 보더/그림자 FileBrowser S3 gear 와 동일.
 *   - 클릭 시 우측에 360px drawer 오픈.
 *   - ESC 또는 외부 클릭 시 닫힘.
 *   - z-index 50 (모달·dropdown 아래).
 */
export default function PageGear({ title = "설정", children, canEdit = true, position = "bottom-right" }) {
  const [open, setOpen] = useState(false);
  const drawerRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    const onClick = (e) => { if (drawerRef.current && !drawerRef.current.contains(e.target)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    setTimeout(() => window.addEventListener("mousedown", onClick), 0);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("mousedown", onClick); };
  }, [open]);

  // v8.8.3: 전 탭이 bottom-right 로 통일 (FileBrowser ⚙️ 와 일치). position prop 은
  // 후방 호환을 위해 남겨두되, 특이 케이스(inline) 가 아니면 bottom-right 로 정규화.
  const normalized = (position === "inline" || position === "top-right")
    ? position
    : "bottom-right";

  const pos = normalized === "inline"
    ? { position: "relative" }
    : normalized === "top-right"
      ? { position: "absolute", top: 14, right: 16 }
      : { position: "fixed", bottom: 16, right: 16 };  // bottom-right (default)

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title={canEdit ? title : title + " (읽기 전용)"}
        style={{
          ...pos,
          zIndex: 40,
          width: 40, height: 40, borderRadius: "50%",
          border: "1px solid var(--border)",
          background: "var(--bg-secondary)",
          color: "var(--text-secondary)",
          cursor: "pointer", fontSize: 18,
          display: "flex", alignItems: "center", justifyContent: "center",
          boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
        }}
      >⚙️</button>
      {open && (
        <>
          <div style={{
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
            background: "rgba(0,0,0,0.3)", zIndex: 49,
          }} />
          <div ref={drawerRef} style={{
            position: "fixed", top: 48, right: 0, bottom: 0,
            width: 360, background: "var(--bg-secondary)",
            borderLeft: "1px solid var(--border)",
            boxShadow: "-4px 0 16px rgba(0,0,0,0.3)",
            zIndex: 50, display: "flex", flexDirection: "column",
          }}>
            <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 14, fontWeight: 700, flex: 1 }}>{title}</span>
              {!canEdit && <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>읽기 전용</span>}
              <span onClick={() => setOpen(false)} style={{ cursor: "pointer", color: "var(--text-secondary)", padding: "2px 8px" }}>×</span>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
              {children}
            </div>
          </div>
        </>
      )}
    </>
  );
}
