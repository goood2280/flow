/* Background refresh that only runs while the Flow tab is visible.
   People keep several Flow tabs open all day; every hidden tab used to keep
   polling the production server (alerts, unread counts, monitors). Hidden
   ticks are skipped, and the callback runs once as soon as the tab becomes
   visible again so the screen is fresh when someone looks at it.
   Returns the cleanup function for useEffect. */
export function setVisibleInterval(callback, intervalMs, { refreshOnVisible = true } = {}) {
  const hasDocument = typeof document !== "undefined";
  const tick = () => {
    if (hasDocument && document.hidden) return;
    callback();
  };
  const timer = setInterval(tick, intervalMs);
  const onVisibility = () => {
    if (refreshOnVisible && hasDocument && !document.hidden) callback();
  };
  if (hasDocument) document.addEventListener("visibilitychange", onVisibility);
  return () => {
    clearInterval(timer);
    if (hasDocument) document.removeEventListener("visibilitychange", onVisibility);
  };
}
