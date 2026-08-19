import Modal from "../Modal";
import { Button } from "../UXKit";

export function ConfirmDialog({
  open,
  title = "확인",
  message,
  detail,
  confirmText = "확인",
  cancelText = "취소",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}) {
  return (
    <Modal open={open} onClose={busy ? undefined : onCancel} title={title} width={420} closeOnBackdrop={!busy}>
      <div className="u-stack">
        <div>{message}</div>
        {detail && <div className="u-muted">{detail}</div>}
        <div className="ds-feedback__actions u-push-right">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>{cancelText}</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} disabled={busy}>
            {busy ? "처리 중…" : confirmText}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export default ConfirmDialog;
