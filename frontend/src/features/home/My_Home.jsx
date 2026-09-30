import { useEffect, useMemo, useState } from "react";
import BrandLogo from "../../components/BrandLogo";
import { canAccessTab, isAdmin as isAdminUser, visibleTabsFor } from "../../lib/permissions";
import HomeAlertsSection from "./HomeAlertsSection";
import HomeAppIcon from "./HomeAppIcons";
import HomeDataChat from "./HomeDataChat";
import { preloadPage } from "../../app/pageManifest";

const CARD_DESC = {
  filebrowser: "DB와 Files 데이터 조회",
  dashboard: "저장된 지표와 차트 확인",
  splittable: "Lot·Wafer split plan 관리",
  lotmanage: "주요 Lot 현황 관리",
  productwiki: "제품별 공정과 분석 지식 확인",
  ramcache: "제품별 데이터 캐시 상태 관리",
  matchfill: "매칭되지 않은 데이터 연결",
  chartbuilder: "데이터 쿼리와 차트 생성",
  templatereport: "Template Report 작성",
  autoreport: "자동 리포트 생성과 이력",
  lotrequest: "Lot 배정과 요청 관리",
  analysisrequest: "분석의뢰 · split·ET·DCOP 대조",
  lotlocation: "Lot·Wafer 현위치 및 공정 확인",
  inform: "공정 인폼 기록과 조회",
  meeting: "회의와 안건 관리",
  calendar: "일정과 변경점 관리",
  tracker: "ET 이슈 추적",
  lottracker: "LOT 공정 이력과 진행 예측",
  valve: "매칭 알람 확인",
  teg: "TEG 좌표와 Mapfile 검증",
  yieldmap: "Wafer Map 조회",
  ettime: "ET 측정시간 분석",
  reformatize: "ET 데이터 다운로드",
  dcop: "양산 DCOP 검사",
  admin: "사용자와 시스템 설정",
};

function favoriteStorageKey(username) {
  return `flow:home-favorites:${username || "guest"}`;
}

function readFavorites(username) {
  try {
    const value = JSON.parse(localStorage.getItem(favoriteStorageKey(username)) || "[]");
    return Array.isArray(value) ? value.filter((key) => typeof key === "string") : [];
  } catch {
    return [];
  }
}

function RoundedStar({ filled }) {
  return (
    <svg
      className="home-feature-favorite__icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        d="M12 3.7l2.38 4.82 5.32.77-3.85 3.75.91 5.3L12 15.84l-4.76 2.5.91-5.3L4.3 9.29l5.32-.77L12 3.7Z"
        fill={filled ? "currentColor" : "none"}
      />
    </svg>
  );
}

function FeatureCard({ tab, favorite, onFavorite, onOpen }) {
  const description = CARD_DESC[tab.key] || "기능 열기";
  return (
    <div className="home-feature-item" data-app-key={tab.key} data-app-group={["data", "system"].includes(tab.group) ? tab.group : "work"}>
      <button
        type="button"
        className={`home-feature-favorite${favorite ? " is-favorite" : ""}`}
        aria-label={`${tab.label} ${favorite ? "즐겨찾기 해제" : "즐겨찾기 추가"}`}
        aria-pressed={favorite}
        title={favorite ? "즐겨찾기 해제" : "즐겨찾기 추가"}
        onClick={() => onFavorite(tab.key)}
      >
        <RoundedStar filled={favorite} />
      </button>
      <button
        type="button"
        className="home-feature-card"
        title={description}
        aria-label={`${tab.label}: ${description}`}
        onClick={() => onOpen(tab.key)}
        onMouseEnter={() => preloadPage(tab.key)}
        onFocus={() => preloadPage(tab.key)}
      >
        <span className="home-feature-card__topline">
          <span className="home-feature-card__icon" aria-hidden="true">
            <HomeAppIcon appKey={tab.key} />
          </span>
        </span>
        <span className="home-feature-card__content">
          <span className="home-feature-card__title">{tab.label}</span>
          <span className="home-feature-card__description u-sr-only">{description}</span>
        </span>
      </button>
    </div>
  );
}

export default function My_Home({ onNavigate, user, visibleTabs }) {
  const admin = isAdminUser(user);
  const canUseFlowi = canAccessTab(user, admin ? "__all__" : (user?.tabs || ""), "flowi");
  const tabs = Array.isArray(visibleTabs)
    ? visibleTabs
    : visibleTabsFor(user, admin ? "__all__" : (user?.tabs || ""));
  const cards = tabs.filter((tab) => !["home", "diagnosis"].includes(tab.key));
  const open = onNavigate || (() => {});
  const username = user?.username || "guest";
  const [favorites, setFavorites] = useState(() => readFavorites(username));
  const [flowiOpen, setFlowiOpen] = useState(false);
  const [flowiProbeKey, setFlowiProbeKey] = useState(0);
  const [flowiTarget, setFlowiTarget] = useState(null);

  useEffect(() => {
    setFavorites(readFavorites(username));
    setFlowiOpen(false);
  }, [username]);

  const toggleFlowi = () => {
    if (!flowiOpen) setFlowiProbeKey((key) => key + 1);
    setFlowiTarget(null);
    setFlowiOpen(!flowiOpen);
  };

  const favoriteSet = useMemo(() => new Set(favorites), [favorites]);
  const favoriteRank = useMemo(() => (
    new Map(favorites.map((key, index) => [key, index]))
  ), [favorites]);
  const orderedCards = useMemo(() => (
    cards
      .map((tab, originalIndex) => ({ tab, originalIndex }))
      .sort((a, b) => {
        const aRank = favoriteRank.get(a.tab.key);
        const bRank = favoriteRank.get(b.tab.key);
        if (aRank !== undefined && bRank !== undefined) return bRank - aRank;
        if (aRank !== undefined) return -1;
        if (bRank !== undefined) return 1;
        return a.originalIndex - b.originalIndex;
      })
      .map(({ tab }) => tab)
  ), [cards, favoriteRank]);

  const toggleFavorite = (key) => {
    setFavorites((current) => {
      const next = current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key];
      try {
        localStorage.setItem(favoriteStorageKey(username), JSON.stringify(next));
      } catch {
        // The launcher still works when storage is unavailable; only persistence is skipped.
      }
      return next;
    });
  };

  return (
    <main className={`home-page${canUseFlowi ? " has-admin-chat" : ""}${flowiOpen ? " is-flowi-open" : ""}`}>
      {!flowiOpen && <div className="home-top-section">
        <BrandLogo size="home" />
        <section className="home-welcome">
          <div className="home-welcome__title">
            {user?.name || user?.username || "user"}님, 안녕하세요
          </div>
          <HomeAlertsSection onNavigate={open} user={user} />
          {canUseFlowi && (
            <button
              type="button"
              className={`home-flowi-connect${flowiOpen ? " is-open" : ""}`}
              aria-expanded={flowiOpen}
              aria-controls="home-flowi-chat"
              onClick={toggleFlowi}
            >
              <span className="home-flowi-connect__mark" aria-hidden="true">Flow-i</span>
              <span className="home-flowi-connect__copy">
                <strong>{flowiOpen ? "Flow-i 닫기" : "Flow-i 연결"}</strong>
                <small>{flowiOpen ? "대화창이 연결되어 있습니다" : "질문을 시작하면 연결 상태를 한 번 확인합니다"}</small>
              </span>
              <span className="home-flowi-connect__arrow" aria-hidden="true">{flowiOpen ? "−" : "→"}</span>
            </button>
          )}
        </section>
      </div>}

      {canUseFlowi && flowiOpen && (
        <div id="home-flowi-chat" className="home-flowi-panel">
          <HomeDataChat user={user} onNavigate={open} enabled probeKey={flowiProbeKey} initialConversation={flowiTarget} onClose={() => setFlowiOpen(false)} />
        </div>
      )}

      {!flowiOpen && <div className="home-bottom-section">
        {orderedCards.length ? (
          <div className="home-feature-grid">
            {orderedCards.map((tab) => (
              <FeatureCard
                key={tab.key}
                tab={tab}
                favorite={favoriteSet.has(tab.key)}
                onFavorite={toggleFavorite}
                onOpen={open}
              />
            ))}
          </div>
        ) : (
          <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>
            사용 가능한 기능이 없습니다. 관리자에게 권한을 요청해주세요.
          </div>
        )}
      </div>}
    </main>
  );
}
