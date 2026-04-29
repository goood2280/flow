// v8.8.5: 단위기능별 버전 관리 중단 — VERSION.json 의 통합 버전만 유지.
//         FEATURE_VERSIONS export 를 빈 객체로 유지해서 외부 참조가 있어도 깨지지 않도록 함.
export const FEATURE_VERSIONS = {};
// v8.5.1: Inform log 추가.
export const TABS = [
  {key:"home",label:"홈",icon:"🏠",group:"main"},
  {key:"filebrowser",label:"파일탐색기",icon:"📂",group:"data",defaultTab:true},
  {key:"dashboard",label:"대시보드",icon:"📊",group:"data",defaultTab:true},
  {key:"splittable",label:"스플릿 테이블",icon:"🗂️",group:"data",defaultTab:true},
  {key:"diagnosis",label:"에이전트",icon:"🤖",group:"tool",defaultTab:true},
  {key:"tracker",label:"이슈 추적",icon:"📋",group:"tool"},
  {key:"inform",label:"인폼 로그",icon:"📢",group:"tool"},
  {key:"meeting",label:"회의관리",icon:"🗓",group:"tool"},
  {key:"calendar",label:"변경점 관리",icon:"📅",group:"tool"},
  {key:"ettime",label:"ET 레포트",icon:"⏱️",group:"data",defaultTab:true},
  {key:"waferlayout",label:"웨이퍼 레이아웃",icon:"🧭",group:"data",defaultTab:true},
  {key:"tablemap",label:"테이블맵",icon:"🔗",group:"tool",adminOnly:true},
  {key:"admin",label:"관리자",icon:"⚙️",group:"system",adminOnly:true},
  {key:"devguide",label:"개발자 가이드",icon:"📖",group:"system",restrictedSetting:"devguide_allowed"},
];
export const FEATURE_MAP = {};
// v9.0.3: planned 섹션에는 아직 비활성 기능만 남긴다.
export const PLANNED_FEATURES = [];
// TAB_CONFIG_END
