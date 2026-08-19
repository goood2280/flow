// components/Modal.jsx v4.0.0 — reusable modal dialog with backdrop + ESC close.
import { useEffect, useId } from "react";

export default function Modal({
  open = true, onClose, title, children,
  width = 420, zIndex = 9999, closeOnBackdrop = true,
  maxHeight = "90vh",
}) {
  const generatedTitleId = useId();
  const titleId = title ? `flow-modal-${generatedTitleId.replace(/:/g, "")}` : undefined;
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape" && onClose) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="ds-modal-backdrop"
      role="presentation"
      style={{ zIndex }}
      onClick={() => { if (closeOnBackdrop && onClose) onClose(); }}
    >
      <div
        className="flow-modal ds-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: width, maxHeight }}
      >
        {title && (
          <div className="ds-modal__header">
            <div className="ds-modal__title" id={titleId}>{title}</div>
            {onClose && (
              <button type="button" className="ds-modal__close" onClick={onClose} aria-label="대화상자 닫기">×</button>
            )}
          </div>
        )}
        <div className="ds-modal__body">{children}</div>
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
      <div className="ds-modal__message">{message}</div>
      <div className="ds-modal__actions">
        <button type="button" className="ds-button ds-button--ghost" onClick={onCancel}>{cancelText}</button>
        <button type="button" className={`ds-button ${danger ? "ds-button--danger" : "ds-button--primary"}`} onClick={onConfirm}>{confirmText}</button>
      </div>
    </Modal>
  );
}
