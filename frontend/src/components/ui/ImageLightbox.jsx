// components/ui/ImageLightbox.jsx — 썸네일을 누르면 같은 화면 위 팝업으로 원본 크기 표시.
// 이미지·배경을 누르거나 ESC 를 누르면 닫힌다. 화면보다 큰 이미지는 팝업 안에서 스크롤.
import { useEffect } from "react";
import { createPortal } from "react-dom";

export default function ImageLightbox({ src, alt = "", onClose, zIndex = 10000 }) {
  useEffect(() => {
    if (!src) return undefined;
    // capture 단계에서 먹어서, 아래에 깔린 Modal 의 ESC 닫기까지 번지지 않게 한다.
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose?.();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [src, onClose]);

  if (!src) return null;
  const close = (e) => { e.stopPropagation(); onClose?.(); };
  return createPortal(
    <div
      className="ds-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={alt || "이미지 원본 보기"}
      onClick={close}
      style={{ zIndex, overflow: "auto", display: "block", cursor: "zoom-out" }}
    >
      <div style={{ minWidth: "100%", minHeight: "100%", display: "flex", alignItems: "center", justifyContent: "center", width: "max-content" }}>
        <img
          src={src}
          alt={alt}
          title="클릭해서 닫기"
          onClick={close}
          style={{ display: "block", maxWidth: "none", background: "var(--bg-primary)" }}
        />
      </div>
    </div>,
    document.body,
  );
}
