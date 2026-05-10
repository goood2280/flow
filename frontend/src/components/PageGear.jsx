/* PageGear.jsx — 페이지별 공용 톱니(⚙️) 설정 패널.
 * 스타일은 40 × 40 / ⚙️ emoji / 그림자로 FileBrowser 자체 톱니(좌하단)와 통일.
 * 위치는 페이지가 명시한 position 을 우선한다. 우측 상세 패널을 두는 페이지는
 * bottom-left 를 지정해 우측 콘텐츠(삭제/저장 버튼 등)를 가리지 않는다.
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
 *   - position: "bottom-left" (default) | "bottom-right" | "top-right" | "inline".
 *
 * 특징:
 *   - 버튼: 40 x 40, ⚙️ emoji, 보더/그림자 FileBrowser S3 gear 와 동일.
 *   - 클릭 시 우측에 360px drawer 오픈.
 *   - ESC 또는 외부 클릭 시 닫힘.
 *   - z-index 50 (모달·dropdown 아래).
 */
export default function PageGear({ title = "설정", children, canEdit = true, position = "bottom-left" }) {
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

  const validPositions = new Set(["bottom-left", "bottom-right", "top-right", "inline"]);
  const normalized = validPositions.has(position) ? position : "bottom-left";

  const pos = normalized === "inline"
    ? { position: "relative" }
    : normalized === "top-right"
      ? { position: "absolute", top: 14, right: 16 }
      : normalized === "bottom-right"
        ? { position: "fixed", bottom: 16, right: 16 }
        : { position: "fixed", bottom: 16, left: 16 };

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
              {!canEdit && <span style={{ fontSize: 14, padding: "2px 8px", borderRadius: 999, background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>읽기 전용</span>}
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
