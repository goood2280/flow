export function Feedback({
  kind = "empty",
  icon,
  title,
  message,
  actions,
  className = "",
}) {
  const defaultIcon = {
    loading: "…",
    empty: "○",
    error: "!",
    permission: "⊘",
    offline: "↯",
  }[kind] || "○";

  return (
    <div className={`ds-feedback${className ? ` ${className}` : ""}`} data-kind={kind} role={kind === "error" ? "alert" : "status"}>
      <div className="ds-feedback__inner">
        <div className="ds-feedback__icon" aria-hidden="true">{icon || defaultIcon}</div>
        {title && <div className="ds-feedback__title">{title}</div>}
        {message && <div className="ds-feedback__message">{message}</div>}
        {actions && <div className="ds-feedback__actions">{actions}</div>}
      </div>
    </div>
  );
}

export function LoadingState({ title = "데이터를 불러오는 중입니다", message, actions }) {
  return <Feedback kind="loading" title={title} message={message} actions={actions} />;
}

export function EmptyState({ title = "표시할 데이터가 없습니다", message, actions }) {
  return <Feedback kind="empty" title={title} message={message} actions={actions} />;
}

export function ErrorState({ title = "요청을 완료하지 못했습니다", message, actions }) {
  return <Feedback kind="error" title={title} message={message} actions={actions} />;
}

export function PermissionState({ title = "이 기능을 사용할 권한이 없습니다", message, actions }) {
  return <Feedback kind="permission" title={title} message={message} actions={actions} />;
}

export function OfflineState({ title = "네트워크 연결을 확인해 주세요", message, actions }) {
  return <Feedback kind="offline" title={title} message={message} actions={actions} />;
}
