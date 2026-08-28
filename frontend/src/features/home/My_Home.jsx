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

function FeatureCard({ tab, onOpen }) {
  return (
    <button type="button" className="home-feature-card" onClick={() => onOpen(tab.key)} style={{ width: "100%" }}>
      <span className="home-feature-card__topline">
        <span className="home-feature-card__icon" aria-hidden="true">{tab.icon}</span>
      </span>
      <span className="home-feature-card__content">
        <span className="home-feature-card__title">{tab.label}</span>
        <span className="home-feature-card__description">{CARD_DESC[tab.key] || "기능 열기"}</span>
      </span>
      <span className="home-feature-card__arrow" aria-hidden="true">→</span>
    </button>
  );
}

export default function My_Home({ onNavigate, user, visibleTabs }) {
  const admin = isAdminUser(user);
  const tabs = Array.isArray(visibleTabs)
    ? visibleTabs
    : visibleTabsFor(user, admin ? "__all__" : (user?.tabs || ""));
  const cards = tabs.filter((tab) => !["home", "diagnosis"].includes(tab.key));
  const open = onNavigate || (() => {});

  return (
    <main style={{ minHeight: "calc(100vh - 52px)", width: "100%", boxSizing: "border-box", padding: "32px 32px 96px", maxWidth: 1040, margin: "0 auto" }}>
      <BrandLogo size="home" />
      <section style={{ margin: "8px 0 28px", padding: "20px 22px", border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg-secondary)" }}>
        <div style={{ fontSize: 22, fontWeight: 800, color: "var(--text-primary)" }}>
          {user?.username || "user"}님, 안녕하세요
        </div>
        <div style={{ marginTop: 7, fontSize: 14, color: "var(--text-secondary)" }}>
          사용할 기능을 선택하세요.
        </div>
      </section>
      {cards.length ? (
        <div className="home-feature-grid">
          {cards.map((tab) => <FeatureCard key={tab.key} tab={tab} onOpen={open} />)}
        </div>
      ) : (
        <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>
          사용 가능한 기능이 없습니다. 관리자에게 권한을 요청해주세요.
        </div>
      )}
    </main>
  );
}
