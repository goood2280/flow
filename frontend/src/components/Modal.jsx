// components/Modal.jsx v4.0.0 — reusable modal dialog with backdrop + ESC close.
import { useEffect } from "react";

export default function Modal({
  open = true, onClose, title, children,
  width = 420, zIndex = 9999, closeOnBackdrop = true,
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape" && onClose) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex, background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 16,
      }}
      onClick={() => { if (closeOnBackdrop && onClose) onClose(); }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-secondary)", borderRadius: 12, padding: 20,
          width: "100%", maxWidth: width, maxHeight: "90vh", overflow: "auto",
          border: "1px solid var(--border)",
          boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
        }}
      >
        {title && (
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            marginBottom: 14, paddingBottom: 10, borderBottom: "1px solid var(--border)",
          }}>
            <div style={{ fontWeight: 700, fontSize: 14, color: "var(--text-primary)" }}>
              {title}
            </div>
            {onClose && (
              <span onClick={onClose} style={{
                cursor: "pointer", fontSize: 18, color: "var(--text-secondary)",
                padding: "0 6px", lineHeight: 1,
              }}>×</span>
            )}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

// Confirmation dialog helper: <ConfirmModal open onConfirm onCancel />.
export function ConfirmModal({
  open, title = "Confirm", message, onConfirm, onCancel,
  confirmText = "Confirm", cancelText = "Cancel", danger = false,
}) {
  return (
    <Modal open={open} onClose={onCancel} title={title} width={360}>
      <div style={{ fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5, marginBottom: 16 }}>
        {message}
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button onClick={onCancel} style={{
          padding: "6px 14px", borderRadius: 6,
          border: "1px solid var(--border)",
          background: "transparent", color: "var(--text-secondary)",
          fontSize: 12, cursor: "pointer",
        }}>{cancelText}</button>
        <button onClick={onConfirm} style={{
          padding: "6px 14px", borderRadius: 6, border: "none",
          background: danger ? "#ef4444" : "var(--accent)",
          color: "#fff", fontSize: 12, fontWeight: 600, cursor: "pointer",
        }}>{confirmText}</button>
      </div>
    </Modal>
  );
}
