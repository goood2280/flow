// v8.5.1: Inform log 추가.
export const TABS = [
  {key:"home",label:"홈",icon:"🏠",group:"main"},
  {key:"filebrowser",label:"파일탐색기",icon:"📂",group:"data",defaultTab:true},
  {key:"dashboard",label:"대시보드",icon:"📊",group:"data",defaultTab:true},
  {key:"splittable",label:"스플릿 테이블",icon:"🗂️",group:"data",defaultTab:true},
  {key:"ramcache",label:"캐시 관리",icon:"🧠",group:"data"},
  {key:"diagnosis",label:"에이전트",icon:"🤖",group:"tool",defaultTab:true},
  {key:"valve",label:"매칭알람",icon:"🚨",group:"tool"},
  {key:"teg",label:"TEG 위치 조회",icon:"📐",group:"tool"},
  {key:"ettime",label:"ET 측정시간",icon:"⏱️",group:"tool"},
  {key:"inform",label:"인폼 로그",icon:"📢",group:"tool"},
  {key:"reformatize",label:"ET 다운로드",icon:"🧮",group:"tool"},
  {key:"tracker",label:"ET 추적",icon:"📋",group:"tool"},
  {key:"meeting",label:"회의관리",icon:"🗓",group:"tool"},
  {key:"calendar",label:"변경점 관리",icon:"📅",group:"tool"},
  {key:"admin",label:"관리자",icon:"⚙️",group:"system",adminOnly:true},
  // v9.3.x: DevGuide 는 global admin 전용 (devguide_user 위임 폐기).
  {key:"devguide",label:"개발자 가이드",icon:"📖",group:"system",adminOnly:true,strictAdmin:true},
];
// v9.1.x: 소탭 단위 권한 — backend core/auth.py TAB_SUBTABS 와 동기 유지.
// tabs 토큰: "tab"(전체 소탭) 또는 "tab:subtab".
export const SUB_TABS = {
  filebrowser: [{key:"db",label:"DB"},{key:"files",label:"Files"}],
  splittable: [{key:"view",label:"View"},{key:"history",label:"History"}],
  inform: [{key:"inform",label:"인폼"},{key:"matrix",label:"매트릭스"},{key:"audit",label:"로그"}],
  // v9.2.x: 에이전트 탭 재편 — semantic/llm 은 관리 탭으로 이관, home-flowi/unit-ai 는 catalog/runtime 으로 흡수.
  diagnosis: [{key:"catalog",label:"기능 카탈로그"},{key:"runtime",label:"실행 추적"},{key:"workflows",label:"Workflow 템플릿"}],
};
// TAB_CONFIG_END
