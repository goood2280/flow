/* PageGear.jsx — 페이지별 공용 톱니(gear 아이콘) 설정 패널.
 * 스타일은 48 × 48 원형 / 공용 gear 아이콘(24px) — 모든 페이지 톱니가 이 버튼 하나를 쓴다.
 * 위치는 페이지가 명시한 position 을 우선한다. 우측 상세 패널을 두는 페이지는
 * bottom-left 를 지정해 우측 콘텐츠(삭제/저장 버튼 등)를 가리지 않는다.
 */
import { useEffect, useRef, useState } from "react";
import { Icon } from "./ui/Icon";

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
 *   - 버튼: 48 x 48(inline 은 40), 공용 gear 아이콘(components/ui/Icon) 24px.
 *   - 클릭 시 우측에 360px drawer 오픈.
 *   - ESC 또는 외부 클릭 시 닫힘.
 *   - z-index 50 (모달·dropdown 아래).
 */
function gearPosition(position) {
  const validPositions = new Set(["bottom-left", "bottom-right", "top-right", "inline"]);
  const normalized = validPositions.has(position) ? position : "bottom-left";
  return normalized === "inline"
    ? { position: "relative" }
    : normalized === "top-right"
      ? { position: "absolute", top: 14, right: 16 }
      : normalized === "bottom-right"
        ? { position: "fixed", bottom: 16, right: 16 }
        : { position: "fixed", bottom: 16, left: 16 };
}

export function PageGearButton({ title = "설정", canEdit = true, position = "bottom-left", onClick, zIndex = 40, style = {} }) {
  // 떠 있는 톱니는 48px·아이콘 24px 로 키운다(페이지 머리에 끼우는 inline 은 40px).
  const inline = position === "inline";
  const box = inline ? 40 : 48;
  return (
    <button
      type="button"
      className="flow-page-gear"
      onClick={onClick}
      title={canEdit ? title : title + " (읽기 전용)"}
      aria-label={title}
      style={{
        ...gearPosition(position),
        zIndex,
        width: box, height: box, minHeight: box, padding: 0, borderRadius: "50%",
        border: "1px solid var(--border-strong)",
        background: "var(--surface-panel)",
        color: "var(--text-strong)",
        cursor: "pointer",
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: inline ? "none" : "var(--shadow-flyout)",
        ...style,
      }}
    ><Icon name="gear" size={inline ? 20 : 24} /></button>
  );
}

export default function PageGear({ title = "설정", children, canEdit = true, position = "bottom-left", width = 360 }) {
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

  return (
    <>
      <PageGearButton title={title} canEdit={canEdit} position={position} onClick={() => setOpen(true)} />
      {open && (
        <>
          <div style={{
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
            background: "rgba(0,0,0,0.3)", zIndex: 49,
          }} />
          <div ref={drawerRef} style={{
            position: "fixed", top: 48, right: 0, bottom: 0,
            width, maxWidth: "calc(100vw - 24px)", background: "var(--bg-secondary)",
            borderLeft: "1px solid var(--border)",
            boxShadow: "var(--shadow-modal)",
            zIndex: 50, display: "flex", flexDirection: "column",
          }}>
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10 }}>
              <Icon name="gear" size={20} style={{ color: "var(--text-muted)" }} />
              <span style={{ fontSize: 18, fontWeight: 700, flex: 1, color: "var(--text-strong)" }}>{title}</span>
              {!canEdit && <span style={{ fontSize: 12, fontWeight: 600, padding: "2px 9px", borderRadius: 999, border: "1px solid var(--border)", background: "var(--surface-subtle)", color: "var(--text-muted)" }}>읽기 전용</span>}
              <button type="button" className="flow-icon-button" onClick={() => setOpen(false)} title="닫기" aria-label="닫기"><Icon name="close" /></button>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "16px 20px 20px" }}>
              {children}
            </div>
          </div>
        </>
      )}
    </>
  );
}
