import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sf } from "../../lib/api";
import { toast } from "../../components/Toast";

const IMPORTANCE_TABS = [
  { key: "all", label: "전체" },
  { key: "critical", label: "Critical" },
  { key: "warning", label: "Warning" },
  { key: "info", label: "Info" },
  { key: "notice", label: "Notice" },
];

function getAlertPriorityGroup(item) {
  if (item?.priority_group) return item.priority_group;
  if (item?.tone === "danger") return "critical";
  if (item?.tone === "warn") return "warning";
  if (item?.tone === "notice") return "notice";
  return "info";
}

export default function HomeAlertsSection({ onNavigate, user }) {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState("all");
  const [markingBusy, setMarkingBusy] = useState(false);
  const [markingIds, setMarkingIds] = useState(() => new Set());
  const mountedRef = useRef(false);
  const alertsRef = useRef([]);
  const fetchInFlightRef = useRef(false);
  const queuedRefreshRef = useRef(false);
  const fetchSequenceRef = useRef(0);
  const fetchAbortRef = useRef(null);
  const markingIdsRef = useRef(new Set());
  const pendingIdsRef = useRef(new Set());

  const updateAlerts = useCallback((next) => {
    setAlerts((current) => {
      const resolved = typeof next === "function" ? next(current) : next;
      alertsRef.current = resolved;
      return resolved;
    });
  }, []);

  const isVisible = () => typeof document === "undefined" || document.visibilityState === "visible";

  const fetchAlerts = useCallback((force = false) => {
    if (!isVisible()) return;
    if (fetchInFlightRef.current && !force) {
      queuedRefreshRef.current = true;
      return;
    }
    if (fetchInFlightRef.current) fetchAbortRef.current?.abort();
    queuedRefreshRef.current = false;
    const sequence = ++fetchSequenceRef.current;
    const controller = new AbortController();
    fetchAbortRef.current = controller;
    fetchInFlightRef.current = true;
    setLoading(true);
    setError(null);
    sf("/api/home/alerts?limit=500", { signal: controller.signal })
      .then((data) => {
        if (!mountedRef.current || sequence !== fetchSequenceRef.current) return;
        if (data && Array.isArray(data.alerts)) {
          updateAlerts(data.alerts.filter((alert) => !pendingIdsRef.current.has(alert.id)));
        } else {
          updateAlerts([]);
        }
      })
      .catch((err) => {
        if (!mountedRef.current || sequence !== fetchSequenceRef.current || err?.name === "AbortError") return;
        setError(err?.message || "알람 정보를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (sequence !== fetchSequenceRef.current) return;
        fetchInFlightRef.current = false;
        fetchAbortRef.current = null;
        if (mountedRef.current) setLoading(false);
        if (queuedRefreshRef.current && isVisible() && mountedRef.current) {
          queuedRefreshRef.current = false;
          fetchAlerts();
        }
      });
  }, [updateAlerts]);

  useEffect(() => {
    mountedRef.current = true;
    fetchAlerts();
    const onRefresh = () => fetchAlerts(true);
    const onFocus = () => fetchAlerts(true);
    window.addEventListener("hol:notif-refresh", onRefresh);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    const timer = window.setInterval(() => fetchAlerts(), 30000);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("hol:notif-refresh", onRefresh);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
      window.clearInterval(timer);
      fetchAbortRef.current?.abort();
      fetchSequenceRef.current += 1;
      fetchInFlightRef.current = false;
      queuedRefreshRef.current = false;
    };
  }, [fetchAlerts]);

  // 개별 알람 읽음(확인) 처리
  const handleMarkRead = useCallback((alertId, silent = false) => {
    if (!alertId || markingBusy || markingIdsRef.current.has(alertId)) return;
    const previous = alertsRef.current;
    const removedIndex = previous.findIndex((a) => a.id === alertId);
    if (removedIndex < 0) return;
    const nextMarkingIds = new Set(markingIdsRef.current).add(alertId);
    markingIdsRef.current = nextMarkingIds;
    pendingIdsRef.current.add(alertId);
    setMarkingIds(nextMarkingIds);
    updateAlerts(previous.filter((a) => a.id !== alertId));

    sf("/api/home/alerts/mark-read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: [alertId] }),
    })
      .then(() => {
        window.dispatchEvent(new CustomEvent("hol:notif-refresh"));
        if (mountedRef.current && !silent) {
          toast.ok("알람을 확인 처리했습니다.");
        }
      })
      .catch((err) => {
        if (mountedRef.current) {
          updateAlerts((current) => {
            if (current.some((a) => a.id === alertId)) return current;
            const restored = current.slice();
            restored.splice(Math.min(removedIndex, restored.length), 0, previous[removedIndex]);
            return restored;
          });
          toast.error(`알람 확인 실패: ${err?.message || err}`);
        }
      })
      .finally(() => {
        pendingIdsRef.current.delete(alertId);
        const remaining = new Set(markingIdsRef.current);
        remaining.delete(alertId);
        markingIdsRef.current = remaining;
        if (mountedRef.current) setMarkingIds(remaining);
      });
  }, [markingBusy, updateAlerts]);

  // 모든 알람 일괄 읽음(확인) 처리
  const handleMarkAllRead = useCallback(() => {
    if (alertsRef.current.length === 0 || markingBusy || markingIdsRef.current.size > 0) return;
    const previous = alertsRef.current;
    const ids = previous.map((alert) => alert.id).filter(Boolean);
    if (ids.length === 0) return;
    setMarkingBusy(true);
    ids.forEach((id) => pendingIdsRef.current.add(id));
    updateAlerts([]);

    sf("/api/home/alerts/mark-read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    })
      .then(() => {
        window.dispatchEvent(new CustomEvent("hol:notif-refresh"));
        toast.ok("모든 알람을 확인 처리했습니다.");
      })
      .catch((err) => {
        if (mountedRef.current) {
          updateAlerts((current) => {
            const currentIds = new Set(current.map((alert) => alert.id));
            return [...previous.filter((alert) => !currentIds.has(alert.id)), ...current];
          });
          toast.error(`일괄 확인 실패: ${err?.message || err}`);
        }
      })
      .finally(() => {
        ids.forEach((id) => pendingIdsRef.current.delete(id));
        if (mountedRef.current) setMarkingBusy(false);
      });
  }, [markingBusy, updateAlerts]);

  const handleAlertClick = (alert) => {
    if (!alert?.target_tab) return;
    const tab = alert.target_tab;
    const search = alert.target_search || "";

    // 클릭하여 이동 시 자동 확인 처리
    handleMarkRead(alert.id, true);

    window.dispatchEvent(
      new CustomEvent("flow:navigate", {
        detail: { tab, search },
      })
    );

    if (typeof onNavigate === "function") {
      onNavigate(tab, search);
    }
  };

  // 중요도별 카운트 계산 (Critical, Warning, Info, Notice)
  const tabCounts = useMemo(() => {
    const counts = { all: alerts.length, critical: 0, warning: 0, info: 0, notice: 0 };
    for (const a of alerts) {
      const group = getAlertPriorityGroup(a);
      if (counts[group] !== undefined) {
        counts[group] += 1;
      } else {
        counts.info += 1;
      }
    }
    return counts;
  }, [alerts]);

  // 중요도 탭별 필터링
  const filteredAlerts = useMemo(() => {
    if (activeTab === "all") return alerts;
    return alerts.filter((a) => getAlertPriorityGroup(a) === activeTab);
  }, [alerts, activeTab]);

  return (
    <section className="home-alerts-section" aria-label="진행 이상 및 알람">
      <div className="home-alerts-header">
        <div className="home-alerts-header__left">
          <span
            className={`home-alerts-status-dot ${
              alerts.length > 0 ? "is-anomaly" : "is-ok"
            }`}
            aria-hidden="true"
          />
          <h2 className="home-alerts-title">진행 이상 및 중요 알람</h2>
          {alerts.length > 0 && (
            <span className="home-alerts-count-badge">
              {alerts.length}건
            </span>
          )}

          {/* 중요도 탭 (전체, Warning / 진행이상, 일반 알람 / 변동) */}
          <div className="home-alerts-tabs" role="tablist" aria-label="알람 중요도 탭">
            {IMPORTANCE_TABS.map((tab) => {
              const count = tabCounts[tab.key] || 0;
              const isActive = activeTab === tab.key;
              return (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  className={`home-alerts-tab-btn ${
                    isActive ? "is-active" : ""
                  }`}
                  onClick={() => setActiveTab(tab.key)}
                >
                  <span>{tab.label}</span>
                  <span className="home-alerts-tab-count">{count}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="home-alerts-header__right">
          {alerts.length > 0 && (
            <button
              type="button"
              className="home-alerts-mark-all-btn"
              onClick={handleMarkAllRead}
              disabled={markingBusy}
              title="모든 알람 및 종 알림 일괄 확인"
              aria-label="모든 알람 일괄 확인"
            >
              ✓ 모두 확인
            </button>
          )}
          <button
            type="button"
            className="home-alerts-refresh-btn"
            onClick={fetchAlerts}
            disabled={loading}
            title="알람 새로고침"
            aria-label="알람 새로고침"
          >
            {loading ? "조회 중..." : "↻ 새로고침"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="home-alerts-state home-alerts-state--error">
          <span>⚠️ {error}</span>
          <button
            type="button"
            className="home-alerts-retry-btn"
            onClick={fetchAlerts}
          >
            재시도
          </button>
        </div>
      ) : loading && alerts.length === 0 ? (
        <div className="home-alerts-state home-alerts-state--loading">
          <span className="home-alerts-spinner" aria-hidden="true" />
          <span>진행 이상 및 알람 상태를 확인하고 있습니다...</span>
        </div>
      ) : filteredAlerts.length === 0 ? (
        /* 심플한 흰색/서피스 바탕의 담백한 클린 상태 */
        <div className="home-alerts-state home-alerts-state--simple">
          <span>현재 알람이 없습니다</span>
        </div>
      ) : (
        /* 알람 하나가 전체 너비를 쓰고 긴 제목·상세는 줄바꿈한다. */
        <div className="home-alerts-compact-grid">
          {filteredAlerts.map((item) => {
            const group = getAlertPriorityGroup(item);
            const isWatched = item.category === "관심랏";

            return (
              <div
                key={item.id}
                className={`home-alert-row home-alert-row--${group} ${
                  isWatched ? "home-alert-row--watched" : ""
                }`}
                onClick={() => handleAlertClick(item)}
                role="button"
                tabIndex={0}
                title={`${item.title}\n${item.detail || ""}\n(클릭 시 확인 및 이동)`}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleAlertClick(item);
                  }
                }}
              >
                <div className="home-alert-row__left">
                  <span
                    className={`home-alert-badge home-alert-badge--${group}`}
                  >
                    {item.badge || (
                      group === "critical"
                        ? "Critical"
                        : group === "warning"
                        ? "Warning"
                        : group === "notice"
                        ? "Notice"
                        : "Info"
                    )}
                  </span>
                  {item.root_lot_id && (
                    <span className="home-alert-row__lot">
                      {isWatched && <span className="home-alert-row__star" aria-hidden="true">★</span>}
                      {item.root_lot_id}
                    </span>
                  )}
                  {item.product_key && !item.root_lot_id && (
                    <span className="home-alert-row__product">
                      {item.product_key}
                    </span>
                  )}
                  <span className="home-alert-row__title">
                    {item.title}
                  </span>
                  {item.detail && (
                    <span className="home-alert-row__detail">{item.detail}</span>
                  )}
                </div>

                <div className="home-alert-row__right">
                  {/* 개별 읽음(확인) 버튼 */}
                  <button
                    type="button"
                    className="home-alert-row__check-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleMarkRead(item.id);
                    }}
                    disabled={markingBusy || markingIds.has(item.id)}
                    title="확인(읽음) 처리 - 목록 및 우상단 종 알림에서 제거"
                    aria-label="알람 확인 처리"
                  >
                    ✓
                  </button>
                  {/* 딥링크 바로가기 버튼 */}
                  <button
                    type="button"
                    className="home-alert-row__arrow-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleAlertClick(item);
                    }}
                    title={item.action_label || "이동"}
                    aria-label={item.action_label || "이동"}
                  >
                    →
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
