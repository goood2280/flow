import { useEffect, useMemo, useState } from "react";
import BrandLogo from "../../components/BrandLogo";
import { isAdmin as isAdminUser, visibleTabsFor } from "../../lib/permissions";

const CARD_DESC = {
  filebrowser: "DB와 Files 데이터 조회",
  dashboard: "저장된 지표와 차트 확인",
  splittable: "Lot·Wafer split plan 관리",
  lotmanage: "주요 Lot 현황 관리",
  chartbuilder: "데이터 쿼리와 차트 생성",
  templatereport: "Template Report 작성",
  autoreport: "자동 리포트 생성과 이력",
  lotrequest: "Lot 배정과 요청 관리",
  inform: "공정 인폼 기록과 조회",
  meeting: "회의와 안건 관리",
  calendar: "일정과 변경점 관리",
  tracker: "ET 이슈 추적",
  valve: "매칭 알람 확인",
  teg: "TEG 좌표와 Mapfile 검증",
  yieldmap: "Wafer Map 조회",
  ettime: "ET 측정시간 분석",
  reformatize: "ET 데이터 다운로드",
  dcop: "양산 DCOP 검사",
  admin: "사용자와 시스템 설정",
};

const APP_TONES = ["blue", "violet", "green", "amber", "pink"];
const HOME_ICON_OVERRIDES = {
  lotrequest: "🤝",
};

function appToneFor(key) {
  const seed = [...String(key)].reduce((sum, character) => sum + character.charCodeAt(0), 0);
  return APP_TONES[seed % APP_TONES.length];
}

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

const WAFER_SHOTS = [
  [10, 5.8], [16, 5.8],
  [5.2, 10.4], [11, 10.4], [16.8, 10.4], [22.6, 10.4],
  [5.2, 15], [11, 15], [16.8, 15], [22.6, 15],
  [5.2, 19.6], [11, 19.6], [16.8, 19.6], [22.6, 19.6],
  [10, 24.2], [16, 24.2],
];

function WaferMapGlyph() {
  const waferPath = "M16 2.5C23.46 2.5 29.5 8.54 29.5 16c0 6.12-4.08 11.3-9.68 12.95h-7.64C6.58 27.3 2.5 22.12 2.5 16 2.5 8.54 8.54 2.5 16 2.5Z";
  return (
    <svg className="home-wafer-glyph" viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <clipPath id="home-wafer-shot-clip">
          <path d={waferPath} />
        </clipPath>
      </defs>
      <path className="home-wafer-glyph__surface" d={waferPath} />
      <g clipPath="url(#home-wafer-shot-clip)">
        {WAFER_SHOTS.map(([x, y]) => (
          <rect
            key={`${x}-${y}`}
            className="home-wafer-glyph__shot"
            x={x}
            y={y}
            width="4.2"
            height="3.3"
            rx="0.55"
          />
        ))}
      </g>
      <path className="home-wafer-glyph__outline" d={waferPath} />
      <path className="home-wafer-glyph__notch" d="M14.7 28.95 16 27.65l1.3 1.3" />
    </svg>
  );
}

function TemplateReportGlyph() {
  return (
    <svg className="home-template-report-glyph" viewBox="0 0 36 36" aria-hidden="true">
      <rect className="home-template-report-glyph__template" x="2.5" y="3" width="31" height="30" rx="4" />
      <rect className="home-template-report-glyph__report" x="6.5" y="6.5" width="23" height="23" rx="2.3" />
      <path className="home-template-report-glyph__heading" d="M10 10.5h16M10 13h10" />
      {[1, 2, 3].map((number, index) => {
        const x = 11 + (index * 7);
        return (
          <g key={number}>
            <circle className="home-template-report-glyph__step" cx={x} cy="19" r="2.8" />
            <text className="home-template-report-glyph__number" x={x} y="21.15" textAnchor="middle">{number}</text>
          </g>
        );
      })}
      <path className="home-template-report-glyph__body" d="M10 24h16M10 26.7h12" />
    </svg>
  );
}

function HomeAppGlyph({ tab }) {
  if (tab.key === "yieldmap") return <WaferMapGlyph />;
  if (tab.key === "templatereport") return <TemplateReportGlyph />;
  return HOME_ICON_OVERRIDES[tab.key] || tab.icon || tab.label?.slice(0, 1) || "•";
}

function FeatureCard({ tab, favorite, onFavorite, onOpen }) {
  const description = CARD_DESC[tab.key] || "기능 열기";
  return (
    <div className="home-feature-item" data-app-key={tab.key} data-tone={appToneFor(tab.key)}>
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
      >
        <span className="home-feature-card__topline">
          <span className="home-feature-card__icon" aria-hidden="true">
            <span className="home-feature-card__glyph"><HomeAppGlyph tab={tab} /></span>
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
  const tabs = Array.isArray(visibleTabs)
    ? visibleTabs
    : visibleTabsFor(user, admin ? "__all__" : (user?.tabs || ""));
  const cards = tabs.filter((tab) => !["home", "diagnosis"].includes(tab.key));
  const open = onNavigate || (() => {});
  const username = user?.username || "guest";
  const [favorites, setFavorites] = useState(() => readFavorites(username));

  useEffect(() => {
    setFavorites(readFavorites(username));
  }, [username]);

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
    <main className="home-page">
      <BrandLogo size="home" />
      <section className="home-welcome">
        <div className="home-welcome__title">
          {user?.username || "user"}님, 안녕하세요
        </div>
      </section>
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
    </main>
  );
}
